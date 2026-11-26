# ruff: noqa: E501, F811
"""Deterministiska regressionstester för P0-buggarna B6, B8, B9, B10 och B13."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dagsljus.intervall import kor_monoton_svf_intervall
from data_inlasning.nmd import NmdLager, skriv_nmd
from demovarld.varld import (
    KODVERSION,
    START_TID,
    DemoVarld,
    bygg_ruta_vatten,
    byggnader,
    kantsten,
    markslag_grund,
    portar,
    terrang_z,
    vegetation_hojd,
)
from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from dynamik.nodmodell import skapa_regn_intervall
from gemensamt.hash import sha256_fil
from geografi.modell import KONTRAKT_GEOGRAFI, lager_mapp, lagra, skriv_manifest
from karna.geometri import las_byggnader, las_terrang, ytmodell_fran
from karna.scenario import Andring, Planscenario, skriv_scenario
from markmodell.byggnadsregister import skriv_byggnader_gpkg
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta
from sankgraf.typer import MARKSLAG_BYGGNAD
from tests.konto.conftest import ANNA, ORIGIN, Miljo, Webblasare
from tests.konto.conftest import miljo as konto_miljo  # noqa: F401
from tests.webb.test_asgi import _anropa_asgi
from testsvit.k1_io import las_geotiff, skriv_geotiff
from webb.api import FlodenApiApp
from webb.asgi import FlodenAsgiApp
from webb.fel import FlodenWebbFel
from webb.projektkalla import KONTRAKT_PROJEKT

REPO = Path(__file__).resolve().parents[2]
BO = {"sub": "sub-bo-p0", "email": "bo@example.test", "email_verified": True, "name": "Bo Exempel"}
CY = {"sub": "sub-cy-p0", "email": "cy@example.test", "email_verified": True, "name": "Cy Exempel"}
DI = {"sub": "sub-di-p0", "email": "di@example.test", "email_verified": True, "name": "Di Exempel"}


# ------------------------------------------------------------------ B6: Drift (Dockerfile & systemd)


def test_b6_dockerfile_installerar_s3_extra() -> None:
    """Dockerfile måste installera extra `s3` (boto3) så S3-lagring inte kraschar i drift."""
    innehall = (REPO / "Dockerfile").read_text(encoding="utf-8")
    traff = re.search(r'pip\s+install\s+-e\s+"\.?\[([^\]]+)\]"', innehall)
    assert traff is not None, "Hittade ingen pip install -e '.[...]' rad i Dockerfile"
    extras = {del_.strip() for del_ in traff.group(1).split(",") if del_.strip()}
    assert {"konto", "mcp", "rapport", "s3", "server"} <= extras, extras


def test_b6_systemd_cache_stada_pekar_pa_srv_floden() -> None:
    """deploy/floden-cache-stada.service måste ha WorkingDirectory=/srv/floden enligt docs/DRIFT.md."""
    innehall = (REPO / "deploy" / "floden-cache-stada.service").read_text(encoding="utf-8")
    rader = [r.strip() for r in innehall.splitlines() if r.strip() and not r.strip().startswith("#")]
    assert "WorkingDirectory=/srv/floden" in rader
    assert "/opt/floden" not in innehall


# ------------------------------------------------------------------ Hjälpare


def _bygg_demo_vatten_app(rot: Path) -> tuple[FlodenApiApp, Path]:
    varld = DemoVarld()
    bas = bygg_ruta_vatten(varld, rot / "varld" / "vatten" / "bas")
    (ruta,) = sorted(bas.glob("mm_*"))
    konfig = rot / "config.toml"
    konfig.write_text(
        '[webb]\ndemomapp = "varld"\nfragemappar = ["fragor"]\ncachemapp = "cache"\n'
        'korningsmapp = "korningar"\nklientmapp = "klient"\n',
        encoding="utf-8",
    )
    tom_testsvit = rot / "tom-testsvit"
    tom_testsvit.mkdir(exist_ok=True)
    app = FlodenApiApp(testsvit_rot=tom_testsvit, konfig_fil=konfig)
    return app, ruta


def _post_json(
    app: FlodenApiApp, sokvag: str, payload: Mapping[str, Any]
) -> tuple[int, dict[str, Any]]:
    status, _, kropp = app.hantera_forfragan(
        "POST", sokvag, json.dumps(payload).encode("utf-8")
    )
    return status, json.loads(kropp.decode("utf-8"))


class _SyntetiskGeoMiljo:
    """Helt lokal (nätverksfri) syntetisk geografi + registrerat geografiprojekt med konton."""

    def __init__(self, rot: Path, miljo: Miljo) -> None:
        self.rot = rot
        self.miljo = miljo
        self.reg = miljo.register
        self.geo_rot = rot / "geo"
        self.proj_rot = rot / "projekt"

        # Bygg en K1 v2-ruta (Demovärlden: 300 x 300 m, 1 m-celler, 16 byggnader, riktig sänka)
        varld = DemoVarld()
        k1_tmp = rot / "bygge_k1"
        bygg_ruta_vatten(varld, k1_tmp)
        (k1_ruta,) = sorted(k1_tmp.glob("mm_*"))
        _, geo_info = las_geotiff(k1_ruta / "mark.tif")
        x0, y0 = float(geo_info.x_min), float(geo_info.y_min)
        x1, y1 = float(geo_info.x_max), float(geo_info.y_max)

        # Lagra K1-rutnät och syntetiskt NMD-lager i geografin (K46)
        k1_lager = lagra(self.geo_rot, k1_tmp)
        nmd_tmp = rot / "bygge_nmd"
        nmd_tmp.mkdir(parents=True)
        cell_m = 10.0
        bredd = int(np.ceil((x1 - x0) / cell_m)) + 4
        hojd = int(np.ceil((y1 - y0) / cell_m)) + 4
        # Två tydliga barrskogsfläckar (NMD 111) separerade av hårdgjord yta (NMD 53)
        klasser = np.full((hojd, bredd), 53, dtype=np.uint8)
        klasser[2 : hojd // 2 - 1, 2 : bredd - 2] = 111
        klasser[hojd // 2 + 1 : hojd - 2, 2 : bredd - 2] = 111
        skriv_nmd(NmdLager(klasser, x0 - 20.0, y1 + 20.0, cell_m), nmd_tmp / "nmd.tif")
        nmd_lager = lagra(self.geo_rot, nmd_tmp)

        _, manifest_sha = skriv_manifest(
            self.geo_rot,
            {
                "kontrakt": KONTRAKT_GEOGRAFI,
                "id": "syntgeo",
                "version": "v1",
                "kommun": "goteborg",
                "karna_3011": [x0 + 10.0, y0 + 10.0, x1 - 10.0, y1 - 10.0],
                "gransen_3011": [x0, y0, x1, y1],
                "lager": {
                    "k1_rutnat": {"sha256": k1_lager.sha256},
                    "nmd": {"sha256": nmd_lager.sha256, "buffert_m": 200.0, "cell_m": cell_m},
                },
                "kallor": {
                    "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                    "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                    "nmd": {"licens": "CC0-1.0", "kalla": "Syntetisk NMD"},
                },
                "oppen_for_mcp": True,
            },
        )

        # Konton och roller: Anna = utredare, Bo = lasare, Cy = superadmin, Di = obehörig annan org
        org1 = self.reg.skapa_organisation("Exempelkommun")
        org2 = self.reg.skapa_organisation("Grannkommun")
        anna = self.reg.forbered_konto("anna@example.test")
        self.reg.gor_organisationsadmin(anna, org1)
        bo = self.reg.forbered_konto("bo@example.test")
        self.reg.lagg_till_medlem(bo, org1, None, "test")
        cy = self.reg.forbered_konto("cy@example.test")
        self.reg.ge_superadmin(cy)
        di = self.reg.forbered_konto("di@example.test")
        self.reg.gor_organisationsadmin(di, org2)
        self.konton = {"anna": anna, "bo": bo, "cy": cy, "di": di}

        self.projekt_id, _ = self.reg.registrera_projekt_for_konto(anna, org1, "Syntetiskt kvarter")
        self.reg.bjud_in(anna, self.projekt_id, "bo@example.test", "lasare")

        # Skapa projektmappen (floden-projekt/v1) med k1_vatten, fragor/skyfall.toml och dagsljus.toml
        pmapp = self.proj_rot / "syntetiskt-kvarter"
        k1_vatten = pmapp / "k1_vatten"
        bygg_ruta_vatten(varld, k1_vatten)
        (v_ruta,) = sorted(k1_vatten.glob("mm_*"))
        (pmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
            encoding="utf-8",
        )
        (pmapp / "projekt.json").write_text(
            json.dumps(
                {
                    "kontrakt": KONTRAKT_PROJEKT,
                    "projekt_id": "syntetiskt-kvarter",
                    "register_id": self.projekt_id,
                    "namn": "Syntetiskt kvarter",
                    "kommun": "goteborg",
                    "geografi": "syntgeo@v1",
                    "geografi_manifest_sha256": manifest_sha,
                    "avgransning_3011": [x0 + 20.0, y0 + 20.0, x1 - 20.0, y1 - 20.0],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # Ett K9-scenario (nytt hus i den norra skogsfläcken) för ekologi före/efter
        hx0, hy0 = x0 + 60.0, y1 - 80.0
        k9_fil = pmapp / "k9" / "nytt-hus_vatten.json"
        sc = Planscenario(
            "nytt-hus",
            (sha256_fil(v_ruta / "recept.json"),),
            (
                Andring.fran_dict(
                    {
                        "typ": "byggnad_ny",
                        "andring_id": "hus-nytt",
                        "geometri": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [hx0, hy0],
                                    [hx0 + 30.0, hy0],
                                    [hx0 + 30.0, hy0 + 30.0],
                                    [hx0, hy0 + 30.0],
                                    [hx0, hy0],
                                ]
                            ],
                        },
                        "egenskaper": {"hojd_m": 16.0},
                    }
                ),
            ),
            beskrivning="Nytt hus i syntetiskt geografiprojekt",
        )
        skriv_scenario(sc, k9_fil)
        (pmapp / "scenarier.json").write_text(
            json.dumps(
                {
                    "kontrakt": "floden-scenarier/v1",
                    "scenarier": [
                        {
                            "slug": "nytt-hus",
                            "namn": "Nytt hus",
                            "status": "forslag",
                            "k9_vatten": "k9/nytt-hus_vatten.json",
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        fdir = pmapp / "fragor"
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "skyfall.toml").write_text(
            'kontrakt = "K20/v1"\n'
            'fraga_id = "syntetiskt-kvarter-skyfall"\n'
            'beskrivning = "Skyfall i syntetiskt geografiprojekt"\n\n'
            '[scenarier.bas]\nbas = "../k1_vatten"\n\n'
            '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
            "parametrar = { regn_mm = 50.0 }\n",
            encoding="utf-8",
        )

        self.webb: dict[str, Webblasare] = {}
        for namn, claims in (("anna", ANNA), ("bo", BO), ("cy", CY), ("di", DI)):
            w = miljo.webblasare()
            assert w.logga_in(miljo.idp, claims)[0] == 302
            self.webb[namn] = w

        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=rot / "saknas.toml",
            fragemappar=[rot / "fragor"],
            cachemapp=rot / "cache",
            korningsmapp=rot / "korningar",
            konto=miljo.adapter,
            geomapp=self.geo_rot,
            projektmapp=self.proj_rot,
            kartverktygsmapp=rot / "kartverktyg",
        )

    def http(
        self, vem: str | None, metod: str, sokvag: str, kropp: Any = None
    ) -> tuple[int, dict[str, Any]]:
        rub: dict[str, str] = {}
        if vem is not None:
            w = self.webb[vem]
            rub["Cookie"] = "; ".join(f"{k}={v}" for k, v in w.kakor.items())
            if metod != "GET":
                rub["Origin"] = ORIGIN
                rub.update(w.csrf_headers())
        data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
        status, _, svar = self.app.hantera_http(metod, sokvag, rub, lambda: data)
        return status, json.loads(svar.decode("utf-8")) if svar else {}


# ------------------------------------------------------------------ B8: SVF-intervall (MARKSLAG_BYGGNAD + K1 v2)


def test_b8_svf_intervall_pa_k1_v2_kvarter_med_byggnader(tmp_path: Path) -> None:
    """På K1 v2-kvarter med byggnader (markslag 9) ska SVF < 1.0 och osäkerhet > 0 matcha terrang.tif + byggnader.gpkg."""
    app, ruta = _bygg_demo_vatten_app(tmp_path)

    status, data = _post_json(
        app,
        "/api/analys/svf-intervall",
        {
            "omrade_id": "demokvarteret",
            "osakerhet_byggnad_m": 1.5,
            "antal_riktningar": 8,
            "svf_troskel": 0.85,
        },
    )
    assert status == 200, data
    assert data["format"] == "floden-dagsljus-svf-intervall/v1"
    # Fysiskt krav: byggnader skymmer himlen -> svf_max_medel < 1.0 och höjdosäkerhet ger bredd_max > 0.
    assert 0.0 < data["svf_min_medel"] < data["svf_max_medel"] < 1.0
    assert data["bredd_max"] > 0.0
    assert data["mittlinje_max_horisont_rad"] > 0.0

    # Exakt jämförelse mot deterministisk referens ur terrang.tif + byggnader.gpkg
    terrang, geo = las_terrang(ruta)
    byggnader = las_byggnader(ruta)
    slag, _ = las_geotiff(ruta / "markslag.tif")
    dsm = ytmodell_fran(terrang, geo, byggnader)
    ref_bygg_hojd_m = np.maximum(dsm.astype(np.float64) - terrang.astype(np.float64), 0.0)
    ref = kor_monoton_svf_intervall(
        terrang.astype(np.float64),
        ref_bygg_hojd_m,
        1.5,
        cell_m=float(geo.pixelstorlek_x),
        x_min_m=float(geo.x_min),
        y_max_m=float(geo.y_max),
        antal_riktningar=8,
        svf_troskel=0.85,
    )
    mark_mask = ~((np.asarray(slag, dtype=np.uint8) == MARKSLAG_BYGGNAD) | (ref_bygg_hojd_m > 0.0))
    assert data["svf_min_medel"] == pytest.approx(float(np.mean(ref.svf_min[mark_mask])), rel=1e-9)
    assert data["svf_max_medel"] == pytest.approx(float(np.mean(ref.svf_max[mark_mask])), rel=1e-9)
    assert data["bredd_max"] == pytest.approx(float(ref.bredd_max), rel=1e-9)
    assert data["antal_osakra_celler"] == int(ref.antal_osakra_celler)


def test_b8_svf_intervall_ger_tydligt_fel_nar_byggnadshojder_saknas(tmp_path: Path) -> None:
    """Saknade eller obestämbara byggnadshöjder får aldrig ge tyst 0 eller gissade 10 m."""
    # 1) K1 v2 med byggnadsceller (markslag 9) men tomt byggnadsregister (byggnader.gpkg)
    app1, ruta1 = _bygg_demo_vatten_app(tmp_path / "tom_bygg")
    _, geo1 = las_terrang(ruta1)
    skriv_byggnader_gpkg(
        ruta1 / "byggnader.gpkg",
        [],
        bbox_fallback=(geo1.x_min, geo1.y_min, geo1.x_max, geo1.y_max),
    )
    s1, d1 = _post_json(
        app1,
        "/api/analys/svf-intervall",
        {"omrade_id": "demokvarteret", "antal_riktningar": 8},
    )
    assert s1 == 400, d1
    assert "byggnadshöjder" in d1["fel"].lower() or "byggnader.gpkg" in d1["fel"].lower()

    # 2) K1 v2 med byggnader.gpkg men saknad terrang.tif
    app2, ruta2 = _bygg_demo_vatten_app(tmp_path / "utan_terrang")
    (ruta2 / "terrang.tif").unlink()
    s2, d2 = _post_json(
        app2,
        "/api/analys/svf-intervall",
        {"omrade_id": "demokvarteret", "antal_riktningar": 8},
    )
    assert s2 == 400, d2
    assert "terrang.tif" in d2["fel"]

    # 3) K1 v1 (utan terrang.tif och byggnader.gpkg) där mark.tif är plan utan inbränd hushöjd
    app3, ruta3 = _bygg_demo_vatten_app(tmp_path / "plan_v1")
    terrang3, geo3 = las_terrang(ruta3)
    (ruta3 / "terrang.tif").unlink()
    (ruta3 / "byggnader.gpkg").unlink()
    skriv_geotiff(
        ruta3 / "mark.tif",
        np.full(terrang3.shape, 12.0, dtype=np.float32),
        x_min=geo3.x_min,
        y_max=geo3.y_max,
        pixelstorlek=geo3.pixelstorlek_x,
    )
    s3, d3 = _post_json(
        app3,
        "/api/analys/svf-intervall",
        {"omrade_id": "demokvarteret", "antal_riktningar": 8},
    )
    assert s3 == 400, d3
    assert "byggnadshöjder" in d3["fel"].lower()


# ------------------------------------------------------------------ B9: 100 % regnvolym i intervallanalys


def test_b9_intervall_analys_ger_100_procent_regnvolym_inte_50(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/analys/intervall med referensregn_mm ska integrera 100 % regnvolym, inte 50 %."""
    import webb.api as webb_api_mod

    app, _ = _bygg_demo_vatten_app(tmp_path)
    regn_mm = 50.0

    # Fånga exakt den regnserie som API-vägen skickar till kor_monoton_intervallanalys
    fangat: list[tuple[tuple[float, ...], tuple[float, ...]]] = []
    orig = webb_api_mod.kor_monoton_intervallanalys

    def _spion(graf: Any, regn_tid_s: Any, regn_intens: Any, **kw: Any) -> Any:
        fangat.append((tuple(float(t) for t in regn_tid_s), tuple(float(i) for i in regn_intens)))
        return orig(graf, regn_tid_s, regn_intens, **kw)

    monkeypatch.setattr(webb_api_mod, "kor_monoton_intervallanalys", _spion)

    # 1) Begär 50 mm referensregn och verifiera att integrerad regnmängd är exakt 50 mm (inte 25 mm)
    st50, _ = _post_json(
        app,
        "/api/analys/intervall",
        {
            "omrade_id": "demokvarteret",
            "referensregn_mm": regn_mm,
            "klimatfaktor_intervall": [1.0, 1.0],
            "initial_forlust_mm_intervall": [0.0, 0.0],
            "avtappning_mm_h_intervall": [0.0, 0.0],
            "k_sigma": 0.0,
        },
    )
    assert st50 == 200 and len(fangat) == 1
    tid_50, int_50 = fangat[0]
    _, regn_50_m = skapa_regn_intervall(tid_50, int_50)
    assert regn_50_m == pytest.approx(regn_mm / 1000.0, rel=1e-12)

    # Verifiera även verklig dynamisk in_m3 för 100 % vs 50 % regn på projektets sänkgraf
    modell = app.ladda_projektmodell("demokvarteret")
    dyn_100 = kor_dynamik_handelsestyrd(modell.bas_graf, tid_50, int_50)
    dyn_50 = kor_dynamik_handelsestyrd(modell.bas_graf, (0.0, 3600.0), (regn_mm, 0.0))
    assert dyn_100.in_m3 == pytest.approx(2.0 * dyn_50.in_m3, rel=1e-9)

    # 2) Omättad regnmängd (5 mm) där sänkan inte svämmar över: maxdjup måste matcha 100 % och överstiga 50 %
    regn_omattat_mm = 5.0
    st5, d5 = _post_json(
        app,
        "/api/analys/intervall",
        {
            "omrade_id": "demokvarteret",
            "referensregn_mm": regn_omattat_mm,
            "klimatfaktor_intervall": [1.0, 1.0],
            "initial_forlust_mm_intervall": [0.0, 0.0],
            "avtappning_mm_h_intervall": [0.0, 0.0],
            "k_sigma": 0.0,
        },
    )
    assert st5 == 200, d5
    iv_100 = orig(
        modell.bas_graf,
        (0.0, 3600.0),
        (regn_omattat_mm, regn_omattat_mm),
        klimatfaktor_intervall=(1.0, 1.0),
        initial_forlust_mm_intervall=(0.0, 0.0),
        avtappning_mm_h_intervall=(0.0, 0.0),
        bifurkationer=(),
    )
    iv_50 = orig(
        modell.bas_graf,
        (0.0, 3600.0),
        (regn_omattat_mm, 0.0),
        klimatfaktor_intervall=(1.0, 1.0),
        initial_forlust_mm_intervall=(0.0, 0.0),
        avtappning_mm_h_intervall=(0.0, 0.0),
        bifurkationer=(),
    )
    max_100 = max(n.max_maxdjup_m for n in iv_100.noder.values())
    max_50 = max(n.max_maxdjup_m for n in iv_50.noder.values())
    assert max_100 > max_50 + 1e-3
    assert d5["sammanfattning"]["storsta_max_djup_m"] == pytest.approx(max_100, abs=1e-9)
    assert d5["sammanfattning"]["storsta_min_djup_m"] == pytest.approx(max_100, abs=1e-9)
    for post in d5["noder"]:
        nid = int(post["nod_id"])
        assert post["max_maxdjup_m"] == pytest.approx(iv_100.noder[nid].max_maxdjup_m, abs=1e-9)


# ------------------------------------------------------------------ B10 & B13: Geografiprojekt + behörighet


def test_b10_alla_fyra_analys_endpoints_pa_geografiprojekt_och_behorighet(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    """Alla 4 /api/analys/* fungerar på syntetiskt geografiprojekt med bevarad 403/404-kontroll."""
    v = _SyntetiskGeoMiljo(tmp_path, konto_miljo)
    pid = v.projekt_id

    # 1. Bifurkationer
    st_b, d_b = v.http("anna", "POST", "/api/analys/bifurkationer", {"omrade_id": pid})
    assert st_b == 200, d_b
    assert d_b["format"] == "floden-bifurkation-felindikator/v1"
    assert d_b["omrade_id"] == pid
    assert d_b["antal_instabila"] == 5
    assert d_b["felindikator"]["antal_aktiva_celler"] > 0
    assert v.http("bo", "POST", "/api/analys/bifurkationer", {"omrade_id": pid})[0] == 200
    assert v.http("di", "POST", "/api/analys/bifurkationer", {"omrade_id": pid})[0] == 403
    assert v.http(None, "POST", "/api/analys/bifurkationer", {"omrade_id": pid})[0] == 401
    assert v.http("anna", "POST", "/api/analys/bifurkationer", {"omrade_id": "finns-inte"})[0] == 404

    # 2. Vattenintervall
    st_i, d_i = v.http(
        "anna",
        "POST",
        "/api/analys/intervall",
        {"omrade_id": pid, "referensregn_mm": 50.0, "klimatfaktor_intervall": [1.0, 1.2]},
    )
    assert st_i == 200, d_i
    assert d_i["format"] == "floden-vatten-intervall/v1"
    assert d_i["omrade_id"] == pid
    assert d_i["sammanfattning"]["antal_noder"] >= 1
    assert d_i["sammanfattning"]["storsta_max_djup_m"] > 0.0
    assert d_i["max_volymfel_rel"] < 1e-2
    assert v.http("bo", "POST", "/api/analys/intervall", {"omrade_id": pid})[0] == 200
    assert v.http("di", "POST", "/api/analys/intervall", {"omrade_id": pid})[0] == 403
    assert v.http("anna", "POST", "/api/analys/intervall", {"omrade_id": "finns-inte"})[0] == 404

    # 3. SVF-intervall (verifierar B8 + B10 tillsammans på geografiprojektets K1 v2-ruta)
    st_s, d_s = v.http(
        "anna",
        "POST",
        "/api/analys/svf-intervall",
        {"omrade_id": pid, "osakerhet_byggnad_m": 1.5, "antal_riktningar": 8},
    )
    assert st_s == 200, d_s
    assert d_s["format"] == "floden-dagsljus-svf-intervall/v1"
    assert d_s["omrade_id"] == pid
    assert 0.0 < d_s["svf_min_medel"] < d_s["svf_max_medel"] < 1.0
    assert d_s["bredd_max"] > 0.0
    assert (
        v.http("bo", "POST", "/api/analys/svf-intervall", {"omrade_id": pid, "antal_riktningar": 8})[0]
        == 200
    )
    assert (
        v.http("di", "POST", "/api/analys/svf-intervall", {"omrade_id": pid, "antal_riktningar": 8})[0]
        == 403
    )
    assert v.http("anna", "POST", "/api/analys/svf-intervall", {"omrade_id": "finns-inte"})[0] == 404

    # 4. Ekologi (utredare -> 200 med verkliga tal; lasare -> 403 [B13]; obehörig -> 403; okänt -> 404)
    st_e, d_e = v.http(
        "anna",
        "POST",
        "/api/analys/ekologi",
        {"omrade_id": pid, "natverk": "barrskog-nacka"},
    )
    assert st_e == 200, d_e
    assert d_e["format"] == "floden-ekologi/v1"
    assert d_e["omrade_id"] == pid
    assert d_e["fore"]["ECA_m2"] > 0.0
    assert d_e["fore"]["antal_flackar"] >= 2
    assert d_e["delta_eca_m2"] <= 0.0
    assert d_e["lager"]["stromtathet_fore"] is not None
    assert d_e["lager"]["stromtathet_skillnad"] is not None
    # B13: lasare (Bo) nekas 403 på ekologi; utomstående (Di) nekas 403; mapp-slug nekas 404 när inloggning är på.
    assert v.http("bo", "POST", "/api/analys/ekologi", {"omrade_id": pid})[0] == 403
    assert v.http("di", "POST", "/api/analys/ekologi", {"omrade_id": pid})[0] == 403
    assert v.http("anna", "POST", "/api/analys/ekologi", {"omrade_id": "finns-inte"})[0] == 404
    assert (
        v.http("anna", "POST", "/api/analys/ekologi", {"omrade_id": "syntetiskt-kvarter"})[0] == 404
    )


def _bygg_demoruta_med_overlapp(
    varld: DemoVarld,
    ut: Path,
    e_min_m: float,
    n_min_m: float,
    storlek_m: float,
    overlapp_m: float,
) -> Path:
    ruta = Ruta(e_min_m, n_min_m, storlek_m, overlapp_m, varld.upplosning_m)
    e, n = ruta.cell_centra_en()
    ee, nn = np.meshgrid(e, n)
    ut.mkdir(parents=True, exist_ok=True)
    res = bygg_markmodell_ruta(
        ruta,
        ut,
        grund_z=terrang_z(varld, ee, nn).astype(np.float32),
        markslag_grund=markslag_grund(varld, ruta),
        brytlinjer=kantsten(varld),
        byggnadskandidater=byggnader(varld),
        oppningar=portar(varld),
        byggnadshojd_fran_laser=False,
        vegetation_hojd_m=vegetation_hojd(varld, ruta),
        vegetation_upplosning_m=varld.vegetation_upplosning_m,
        kodversion=KODVERSION,
        start_tid=START_TID,
    )
    return Path(res.katalog)


def test_b10_flerruts_geografiprojekt_utan_k1_vatten_alla_fyra_analys_endpoints(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    """2-rutors geografiprojekt utan `k1_vatten` (B7-layout) ska ge 200 och fysiska tal på alla 4 `/api/analys/*`."""
    varld = DemoVarld()
    e0, n0, _, _ = varld.fokus_3011
    storlek_m = 150.0
    overlapp_m = 10.0

    # Bygg två angränsande K1 v2-rutor med 10 m överlapp längs södra halvan av fokusområdet (300 x 150 m)
    k1_tmp = tmp_path / "bygge_k1_flerruta"
    _bygg_demoruta_med_overlapp(varld, k1_tmp, e0, n0, storlek_m, overlapp_m)
    _bygg_demoruta_med_overlapp(varld, k1_tmp, e0 + storlek_m, n0, storlek_m, overlapp_m)

    x0, y0 = e0, n0
    x1, y1 = e0 + 2.0 * storlek_m, n0 + storlek_m
    geo_rot = tmp_path / "geo_verklig"
    proj_rot = tmp_path / "projekt_verklig"
    k1_lager = lagra(geo_rot, k1_tmp)
    k1_lagrad_mapp = lager_mapp(geo_rot, k1_lager.sha256)

    # Syntetiskt NMD-lager med två skilda barrskogsfläckar över de två rutorna
    nmd_tmp = tmp_path / "bygge_nmd"
    nmd_tmp.mkdir(parents=True)
    cell_m = 10.0
    bredd = int(np.ceil((x1 - x0) / cell_m)) + 4
    hojd = int(np.ceil((y1 - y0) / cell_m)) + 4
    klasser = np.full((hojd, bredd), 53, dtype=np.uint8)
    klasser[2 : hojd - 2, 2 : bredd // 2 - 1] = 111
    klasser[2 : hojd - 2, bredd // 2 + 1 : bredd - 2] = 111
    skriv_nmd(NmdLager(klasser, x0 - 20.0, y1 + 20.0, cell_m), nmd_tmp / "nmd.tif")
    nmd_lager = lagra(geo_rot, nmd_tmp)

    _, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "flerruta",
            "version": "v1",
            "kommun": "goteborg",
            "karna_3011": [x0 + 10.0, y0 + 10.0, x1 - 10.0, y1 - 10.0],
            "gransen_3011": [x0, y0, x1, y1],
            "lager": {
                "k1_rutnat": {"sha256": k1_lager.sha256},
                "nmd": {"sha256": nmd_lager.sha256, "buffert_m": 200.0, "cell_m": cell_m},
            },
            "kallor": {
                "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                "nmd": {"licens": "CC0-1.0", "kalla": "Syntetisk NMD"},
            },
            "oppen_for_mcp": True,
        },
    )

    reg = konto_miljo.register
    org1 = reg.skapa_organisation("Flerrutekommun")
    org2 = reg.skapa_organisation("AnnanKommun")
    anna = reg.forbered_konto("anna@example.test")
    reg.gor_organisationsadmin(anna, org1)
    bo = reg.forbered_konto("bo@example.test")
    reg.lagg_till_medlem(bo, org1, None, "test")
    di = reg.forbered_konto("di@example.test")
    reg.gor_organisationsadmin(di, org2)
    pid, _ = reg.registrera_projekt_for_konto(anna, org1, "Flerrutsprojekt")
    reg.bjud_in(anna, pid, "bo@example.test", "lasare")

    # Skapa projektmapp UTAN k1_vatten: skyfall.toml pekar direkt på geografins 2-rutors k1_rutnat
    pmapp = proj_rot / "flerrutsprojekt"
    fdir = pmapp / "fragor"
    fdir.mkdir(parents=True, exist_ok=True)
    assert not (pmapp / "k1_vatten").exists()
    (pmapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
        encoding="utf-8",
    )
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": "flerrutsprojekt",
                "register_id": pid,
                "namn": "Flerrutsprojekt",
                "kommun": "goteborg",
                "geografi": "flerruta@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": [x0 + 20.0, y0 + 20.0, x1 - 20.0, y1 - 20.0],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    rel_k1 = Path(os.path.relpath(k1_lagrad_mapp.resolve(), fdir.resolve())).as_posix()
    (fdir / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\n'
        'fraga_id = "flerrutsprojekt-skyfall"\n'
        'beskrivning = "Skyfall direkt på geografins 2-rutors k1_rutnat (B7)"\n\n'
        f'[scenarier.bas]\nbas = "{rel_k1}"\n\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 50.0 }\n",
        encoding="utf-8",
    )

    # Använd symlänkade rötter för att samtidigt verifiera symlänksäker relativsökväg i ekologi
    geo_link = tmp_path / "geo_lank"
    proj_link = tmp_path / "projekt_lank"
    geo_link.symlink_to(geo_rot)
    proj_link.symlink_to(proj_rot)

    webb: dict[str, Webblasare] = {}
    for namn, claims in (("anna", ANNA), ("bo", BO), ("di", DI)):
        w = konto_miljo.webblasare()
        assert w.logga_in(konto_miljo.idp, claims)[0] == 302
        webb[namn] = w

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=konto_miljo.adapter,
        geomapp=geo_link,
        projektmapp=proj_link,
        kartverktygsmapp=tmp_path / "kartverktyg",
    )

    def _http(vem: str, sokvag: str, kropp: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        w = webb[vem]
        rub = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in w.kakor.items()),
            "Origin": ORIGIN,
            **w.csrf_headers(),
        }
        data = json.dumps(kropp).encode("utf-8")
        st, _, svar = app.hantera_http("POST", sokvag, rub, lambda: data)
        return st, json.loads(svar.decode("utf-8")) if svar else {}

    # Bakåtkompatibilitet: markmodell_rutor returnerar båda rutorna, markmodell_mapp ger fortsatt 422
    rutor = app.projekt.markmodell_rutor(pid)
    assert len(rutor) == 2
    with pytest.raises(FlodenWebbFel) as exc_422:
        app.projekt.markmodell_mapp(pid)
    assert exc_422.value.status_kod == 422

    # Verifiera att sammanslagen modell klipper bort 10 m överlapp -> 300 x 150 m (150 x 300 celler)
    modell = app.ladda_projektmodell(pid)
    assert modell.mark_z.shape == (150, 300)
    assert len(modell.k1_mappar) == 2
    assert modell.bas_graf.takvatten is not None
    assert len(modell.bas_graf.takvatten.byggnader) >= 4

    # 1. Bifurkationer
    st_b, d_b = _http("anna", "/api/analys/bifurkationer", {"omrade_id": pid})
    assert st_b == 200, d_b
    assert d_b["format"] == "floden-bifurkation-felindikator/v1"
    assert d_b["omrade_id"] == pid
    assert d_b["felindikator"]["antal_aktiva_celler"] > 0
    assert d_b["felindikator"]["antal_noder_totalt"] >= 1
    assert _http("di", "/api/analys/bifurkationer", {"omrade_id": pid})[0] == 403

    # 2. Vattenintervall
    st_i, d_i = _http(
        "anna",
        "/api/analys/intervall",
        {"omrade_id": pid, "referensregn_mm": 50.0, "klimatfaktor_intervall": [1.0, 1.2]},
    )
    assert st_i == 200, d_i
    assert d_i["format"] == "floden-vatten-intervall/v1"
    assert d_i["sammanfattning"]["antal_noder"] >= 1
    assert d_i["sammanfattning"]["storsta_max_djup_m"] > 0.0
    assert d_i["max_volymfel_rel"] < 1e-2

    # 3. SVF-intervall (sammanfogad terrang.tif + deduplicerade byggnader över 2 rutor)
    st_s, d_s = _http(
        "anna",
        "/api/analys/svf-intervall",
        {"omrade_id": pid, "osakerhet_byggnad_m": 1.5, "antal_riktningar": 8},
    )
    assert st_s == 200, d_s
    assert d_s["format"] == "floden-dagsljus-svf-intervall/v1"
    assert 0.0 < d_s["svf_min_medel"] < d_s["svf_max_medel"] < 1.0
    assert d_s["bredd_max"] > 0.0
    assert d_s["mittlinje_max_horisont_rad"] > 0.0

    # 4. Ekologi (fungerar även via symlänkad geografi-/projektrot och nekar läsare med 403)
    st_e, d_e = _http("anna", "/api/analys/ekologi", {"omrade_id": pid, "natverk": "barrskog-nacka"})
    assert st_e == 200, d_e
    assert d_e["format"] == "floden-ekologi/v1"
    assert d_e["fore"]["ECA_m2"] > 0.0
    assert d_e["fore"]["antal_flackar"] >= 2
    assert _http("bo", "/api/analys/ekologi", {"omrade_id": pid})[0] == 403
    assert _http("di", "/api/analys/ekologi", {"omrade_id": pid})[0] == 403

    # 5. Bevarat 400-fel i SVF-intervall om en av rutorna saknar terrang.tif
    (rutor[1] / "terrang.tif").unlink()
    st_fel, d_fel = _http(
        "anna", "/api/analys/svf-intervall", {"omrade_id": pid, "antal_riktningar": 8}
    )
    assert st_fel == 400, d_fel
    assert "terrang.tif" in d_fel["fel"]


# ------------------------------------------------------------------ B13: ASGI-semafor + legacy /api/rapport


class _SemaforSpyApp:
    """Stub som sover bara på valda tunga sökvägar för deterministiskt ASGI-semafortest."""

    def __init__(self, tung_sokvag: str, *, fordrojning_s: float = 0.25) -> None:
        self.tung_sokvag = tung_sokvag
        self.fordrojning_s = fordrojning_s
        self.startad = threading.Event()
        self.frigiven = threading.Event()
        self.anrop: list[tuple[str, str]] = []

    def hantera_http(
        self,
        metod: str,
        sokvag: str,
        rubriker: Mapping[str, str],
        las_kropp: Callable[[], bytes],
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        _ = las_kropp()
        self.anrop.append((metod, sokvag))
        if metod == "POST" and sokvag == self.tung_sokvag:
            self.startad.set()
            self.frigiven.wait(timeout=self.fordrojning_s)
        svar = json.dumps({"ok": True, "sokvag": sokvag}).encode("utf-8")
        return 200, [("Content-Type", "application/json; charset=utf-8")], svar


def test_b13_asgi_snabba_ui_post_blockeras_inte_av_mattad_tung_semafor() -> None:
    """När tunga semaforen är full ska snabba UI-POST:er (validera, sektion, spara) gå igenom med 200."""
    tidsfaktor = float(os.environ.get("FLODEN_TIDSFAKTOR", "1"))
    spy = _SemaforSpyApp("/api/kor", fordrojning_s=10.0 * tidsfaktor)
    asgi_app = FlodenAsgiApp(
        spy,  # type: ignore[arg-type]
        max_samtidiga_berakningar=1,
        ko_timeout_s=0.02,
    )

    async def _kor() -> None:
        tung = asyncio.create_task(
            _anropa_asgi(asgi_app, "POST", "/api/kor", chunks=(b"{}",))
        )
        deadline = time.monotonic() + 5.0 * tidsfaktor
        try:
            while not spy.startad.is_set():
                assert time.monotonic() < deadline, "Tung POST startade inte i tid"
                await asyncio.sleep(0.005)

            # Snabba UI-POST:er får INTE blockeras eller få 503
            grans_snabb_s = 0.5 * tidsfaktor
            for snabb_vag in (
                "/api/kartverktyg/demo/validera",
                "/api/kartverktyg/demo/sektion",
                "/api/kartverktyg/demo/scenarier",
                "/api/kartverktyg/demo/scenarier/nytt-hus",
                "/api/kartverktyg/demo/scenarier/nytt-hus/byt-namn",
                "/api/kartverktyg/demo/scenarier/nytt-hus/ta-bort",
                "/api/kartverktyg/demo/scenarier/nytt-hus/plan",
                "/api/kartverktyg/demo/lage-idag/plan",
                "/api/kartverktyg/wms/lager",
            ):
                t0 = time.monotonic()
                st, _, _ = await _anropa_asgi(asgi_app, "POST", snabb_vag, chunks=(b"{}",))
                assert st == 200, (snabb_vag, st)
                assert time.monotonic() - t0 < grans_snabb_s, snabb_vag

            # Tunga beräknings-/rapport-POST:er ska däremot avvisas med 503 + Retry-After
            for tung_vag in (
                "/api/analys/bifurkationer",
                "/api/analys/intervall",
                "/api/analys/svf-intervall",
                "/api/analys/ekologi",
                "/api/risk",
                "/api/rapport",
                "/api/rutt",
                "/api/projekt/demo/rapporter",
                "/api/fragor/demo_vatten/kor",
                "/api/kartverktyg/demo/lage-idag/kor",
                "/api/kartverktyg/demo/scenarier/nytt-hus/kor",
            ):
                st, headers, kropp = await _anropa_asgi(asgi_app, "POST", tung_vag, chunks=(b"{}",))
                assert st == 503, (tung_vag, st)
                assert headers["retry-after"] == "5"
                assert json.loads(kropp)["status_kod"] == 503
        finally:
            spy.frigiven.set()

        st_tung, _, _ = await tung
        assert st_tung == 200

    asyncio.run(_kor())


def test_b13_legacy_rapport_kraver_superadmin_och_ekologi_nekar_lasare(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    """Legacy POST /api/rapport nekas för utloggade (401), läsare och utredare (403); tillåts för superadmin."""
    v = _SyntetiskGeoMiljo(tmp_path, konto_miljo)
    kropp = {
        "omrade_id": "A1_enkel_sanka",
        "rapporttyp": "skyfall_oversikt",
        "format": "md",
    }
    # Utloggad -> 401
    assert v.http(None, "POST", "/api/rapport", kropp)[0] == 401
    # Läsare (Bo) -> 403
    st_bo, d_bo = v.http("bo", "POST", "/api/rapport", kropp)
    assert st_bo == 403, d_bo
    assert "superadmin" in d_bo["fel"].lower()
    # Organisationsadmin / utredare (Anna, inte superadmin) -> 403
    st_anna, d_anna = v.http("anna", "POST", "/api/rapport", kropp)
    assert st_anna == 403, d_anna
    # Superadmin (Cy) -> 200 med godkänd talkontroll
    st_cy, d_cy = v.http("cy", "POST", "/api/rapport", kropp)
    assert st_cy == 200, d_cy
    assert d_cy["kontroll_godkand"] is True
    assert d_cy["omrade_id"] == "A1_enkel_sanka"
