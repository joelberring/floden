"""`GET /api/prov/webgpu/fall[/<id>]`: indata och facit för webbläsarens WebGPU-prov.

Lokalt (inloggning av) är API:t öppet som resten; med inloggning på krävs superadmin.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np

from benchmark.provfall import (
    FORMAT_PROVFALL,
    fran_b64,
    provfall_svar,
    till_b64,
    vatten_indata_lutande_plan,
    vatten_schema,
)
from konto.adapter import KontoAdapter
from tests.konto.conftest import ANNA, ORIGIN, Miljo
from tests.webb.test_asgi import _anropa_asgi
from webb.asgi import FlodenAsgiApp, skapa_asgi_app


def _app(tmp_path: Path, konto: KontoAdapter | None = None) -> FlodenAsgiApp:
    (tmp_path / "config.toml").write_text('[webb]\ndemomapp = "varld"\n', encoding="utf-8")
    return skapa_asgi_app(
        testsvit_rot=tmp_path / "t", konfig_fil=tmp_path / "config.toml", konto=konto
    )


def _hamta(
    app: FlodenAsgiApp,
    sokvag: str,
    metod: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, Any]:
    status, _, kropp = asyncio.run(_anropa_asgi(app, metod, sokvag, headers=headers or []))
    return status, (json.loads(kropp) if kropp else None)


def test_roundtrip_base64_behaller_form_och_varden() -> None:
    a = np.arange(12, dtype=np.float64).reshape(3, 4) / 7.0
    d = till_b64(a, "float32")
    assert d["dtyp"] == "float32" and d["form"] == [3, 4]
    assert np.array_equal(fran_b64(d), a.astype(np.float32))
    assert fran_b64(till_b64(np.array([[1, 0]], dtype=np.uint8), "uint8")).dtype == np.uint8


def test_katalogen_listar_bada_fallen(tmp_path: Path) -> None:
    status, d = _hamta(_app(tmp_path), "/api/prov/webgpu/fall")
    assert status == 200 and d["format"] == FORMAT_PROVFALL
    assert [f["id"] for f in d["fall"]] == ["dagsljus-kvarter300", "vatten2d-lutande-plan"]
    assert "Skissläge" in d["markning"]


def test_vattenfallet_ger_indata_och_facit_som_stammer_med_schemat(tmp_path: Path) -> None:
    status, d = _hamta(_app(tmp_path), "/api/prov/webgpu/fall/vatten2d-lutande-plan")
    assert status == 200 and d["typ"] == "vatten_2d" and d["format"] == FORMAT_PROVFALL
    i = d["indata"]
    assert (i["nx"], i["ny"], i["dt"]) == (64, 64, 0.25)
    assert sum(s["antal"] for s in i["steg"]) == 1000
    z = fran_b64(i["z_bed"])
    assert z.shape == (64, 64) and z.dtype == np.float32
    assert set(np.unique(fran_b64(i["aktiv"]))) == {0, 1}
    ind = vatten_indata_lutande_plan()
    maxd, slut, _ = vatten_schema(ind)
    facit = d["facit"]["falt"]
    assert np.array_equal(fran_b64(facit["max_djup_m"]), maxd.astype(np.float32))
    assert np.array_equal(fran_b64(facit["slutdjup_m"]), slut.astype(np.float32))
    assert abs(d["facit"]["massbalans_rel"]) < 1e-12
    assert d["facit"]["tolerans"]["max_djup_m"] > 0 and "Skissläge" in d["markning"]
    assert d["kodversion"]


def test_dagsljusfallet_ger_kvarteret_och_facit_inom_rimliga_granser(tmp_path: Path) -> None:
    status, d = _hamta(_app(tmp_path), "/api/prov/webgpu/fall/dagsljus-kvarter300")
    assert status == 200 and d["typ"] == "dagsljus"
    i = d["indata"]
    assert (i["nx"], i["ny"], i["n_riktningar"]) == (300, 300, 64)
    z = fran_b64(i["z_m"])
    assert z.shape == (300, 300) and z.max() == 45.0  # tornet i gårdsrummet
    assert fran_b64(i["sol_hojd_rad"]).size == fran_b64(i["sol_azimut_rad"]).size == 144
    svf = fran_b64(d["facit"]["falt"]["svf"])
    sol = fran_b64(d["facit"]["falt"]["soltimmar_h"])
    assert svf.shape == sol.shape == (300, 300)
    assert 0.0 < svf.min() < 1.0 and svf.max() <= 1.0 + 1e-6
    assert sol.min() >= 0.0 and sol.max() <= 12.0
    # Samma fall igen ger samma objekt ur cachen (facit räknas en gång per process).
    assert provfall_svar("dagsljus-kvarter300") is provfall_svar("dagsljus-kvarter300")


def test_okant_fall_och_fel_metod(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert _hamta(app, "/api/prov/webgpu/fall/finns-inte")[0] == 404
    assert _hamta(app, "/api/prov/annat")[0] == 404
    assert _hamta(app, "/api/prov/webgpu/fall", metod="POST")[0] == 405


def _huvuden(webb: Any) -> list[tuple[bytes, bytes]]:
    kakor = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
    return [(b"origin", ORIGIN.encode()), (b"cookie", kakor.encode())]


def test_med_inloggning_kravs_superadmin(tmp_path: Path, miljo: Miljo) -> None:
    app = _app(tmp_path, miljo.adapter)
    sokvag = "/api/prov/webgpu/fall"
    assert _hamta(app, sokvag)[0] == 401  # utloggad

    konto_id = miljo.register.forbered_konto(ANNA["email"])  # inloggning kräver inbjudan/konto
    anna = miljo.webblasare()
    assert anna.logga_in(miljo.idp, ANNA)[0] == 302
    # Medlemskap och inloggning räcker inte, varken här eller för ett fall.
    assert _hamta(app, sokvag, headers=_huvuden(anna))[0] == 403
    assert _hamta(app, sokvag + "/vatten2d-lutande-plan", headers=_huvuden(anna))[0] == 403

    miljo.register.ge_superadmin(konto_id)
    status, d = _hamta(app, sokvag + "/vatten2d-lutande-plan", headers=_huvuden(anna))
    assert status == 200 and d["id"] == "vatten2d-lutande-plan"
    # Resten av API:t påverkas inte av provkontrollen.
    assert _hamta(app, "/api/moduler", headers=_huvuden(anna))[0] == 200
