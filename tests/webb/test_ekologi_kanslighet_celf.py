"""Tester för ekologins känslighet (`kansighet.tif`) och CELF-endpoints för ekologi & luft."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.konto.conftest import ANNA, ORIGIN, Miljo
from tests.webb.conftest import Demo
from webb.api import FlodenApiApp
from webb.ekologi_analys import ekologi_svar

REPO = Path(__file__).resolve().parents[2]


def _skapa_app(tmp_path: Path) -> FlodenApiApp:
    return FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )


def _post_json(
    app: FlodenApiApp, sokvag: str, payload: Any
) -> tuple[int, dict[str, Any]]:
    kropp = json.dumps(payload).encode("utf-8")
    status, _, svar_bytes = app.hantera_forfragan("POST", sokvag, kropp)
    return status, json.loads(svar_bytes.decode("utf-8"))


# ------------------------------------------------------------------ 1. Ekologins känslighetslager


def test_ekologi_api_exponerar_kansighet_lager(demo: Demo, tmp_path: Path) -> None:
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[demo.fragor],
        cachemapp=demo.rot / "cache",
        korningsmapp=demo.rot / "korningar",
        kartverktygsmapp=tmp_path / "kartverktyg",
    )
    app.fragor.demomapp = demo.varld

    status, data = _post_json(
        app,
        "/api/analys/ekologi",
        {"omrade_id": "demokvarteret", "artprofil": "barrskogsfagel", "cellstorlek_m": 4.0},
    )
    assert status == 200, data
    lager = data["lager"]
    for nyckel in (
        "stromtathet_fore",
        "stromtathet_efter",
        "stromtathet_skillnad",
        "kansighet_fore",
        "kansighet_efter",
        "kansighet_skillnad",
    ):
        assert nyckel in lager, nyckel
        post = lager[nyckel]
        assert post is not None, nyckel
        assert isinstance(post["lager_id"], str) and post["lager_id"]
        assert isinstance(post["png"], str) and post["png"].endswith(".png")
        assert isinstance(post["utbredning"], dict)
        # PNG-bilden ska gå att hämta med HTTP 200
        png_status, headers, png_bytes = app.hantera_forfragan("GET", post["png"])
        assert png_status == 200, (nyckel, post["png"])
        assert headers["Content-Type"] == "image/png"
        assert png_bytes.startswith(b"\x89PNG")

    assert "kansighet" in lager["kansighet_fore"]["lager_id"]
    assert "kansighet" in lager["kansighet_efter"]["lager_id"]
    assert "kansighet" in lager["kansighet_skillnad"]["lager_id"]


def test_ekologi_svar_hanterar_saknad_kansighet_utan_krasch(tmp_path: Path) -> None:
    """Om en äldre körning saknar `kansighet`-lager blir fälten None utan krasch."""
    from testsvit.k1_io import skriv_geotiff

    for scen in ("fore", "ritat"):
        mapp = tmp_path / "konnektivitet" / scen
        mapp.mkdir(parents=True)
        skriv_geotiff(
            mapp / "stromtathet.tif",
            np.array([[0.1, 0.5], [0.2, 0.0]], dtype=np.float32),
            x_min=100.0,
            y_max=200.0,
            pixelstorlek=4.0,
        )
        (mapp / "ekologi.json").write_text(
            json.dumps(
                {
                    "artprofil_id": "barrskog_mes",
                    "parameterkallor": {},
                    "cellstorlek_m": 4.0,
                    "ECA_m2": 100.0 if scen == "fore" else 90.0,
                    "ECA_intervall_m2": [80.0, 120.0],
                    "PC": 0.1 if scen == "fore" else 0.09,
                    "PC_intervall": [0.08, 0.12],
                    "antal_flackar": 2,
                    "total_habitat_area_m2": 150.0,
                    "dPC": [],
                    "varningar": [],
                }
            ),
            encoding="utf-8",
        )

    class _FakeFragor:
        def resultat(self, _korning_id: str) -> dict[str, Any]:
            utb = {"x_min": 100.0, "y_min": 192.0, "x_max": 108.0, "y_max": 200.0}
            return {
                "lager": [
                    {
                        "id": "konnektivitet.fore.stromtathet",
                        "analys": "konnektivitet",
                        "scenario": "fore",
                        "roll": "stromtathet",
                        "typ": "bas",
                        "png": "/api/korningar/k1/lager/konnektivitet.fore.stromtathet.png",
                        "utbredning": utb,
                    },
                    {
                        "id": "konnektivitet.ritat.stromtathet",
                        "analys": "konnektivitet",
                        "scenario": "ritat",
                        "roll": "stromtathet",
                        "typ": "scenario",
                        "png": "/api/korningar/k1/lager/konnektivitet.ritat.stromtathet.png",
                        "utbredning": utb,
                    },
                    {
                        "id": "skillnad.konnektivitet",
                        "analys": "konnektivitet",
                        "scenario": "ritat",
                        "roll": "stromtathet",
                        "typ": "skillnad",
                        "fore": "fore",
                        "efter": "ritat",
                        "png": "/api/korningar/k1/lager/skillnad.konnektivitet.png",
                        "utbredning": utb,
                    },
                ]
            }

        def fil_sokvag(self, _korning_id: str, rel: str) -> Path:
            return tmp_path / rel

    svar = ekologi_svar(
        _FakeFragor(),  # type: ignore[arg-type]
        korning_id="k1",
        analys="konnektivitet",
        omrade_id="test",
        scenario=None,
        antal_flaskhalsar=3,
        flaskhals_avstand_m=4.0,
        max_flackar=10,
    )
    assert svar["lager"]["stromtathet_fore"] is not None
    assert svar["lager"]["kansighet_fore"] is None
    assert svar["lager"]["kansighet_efter"] is None
    assert svar["lager"]["kansighet_skillnad"] is None


# ------------------------------------------------------------------ 2. CELF för ekologi & luft


def test_ekologi_atgarder_celf_submodular_utan_dubbelrakning(tmp_path: Path) -> None:
    app = _skapa_app(tmp_path)
    status, data = _post_json(
        app,
        "/api/analys/ekologi-atgarder",
        {
            "k": 2,
            "kandidater": {
                "korridor_norr": [10.0, 0.0, 0.0],
                "korridor_norr_alt": [9.0, 0.0, 0.0],  # överlappar helt med korridor_norr
                "tradplantering_ost": [0.0, 6.0, 2.0],
            },
        },
    )
    assert status == 200, data
    assert data["kontrakt"] == "floden-ekologi-celf/v1"
    assert data["k"] == 2
    assert data["garanti_faktor"] == pytest.approx(round(1.0 - math.exp(-1.0), 4))
    assert data["garanti_faktor"] == 0.6321
    valda = data["valda_atgarder"]
    assert len(valda) == 2
    assert valda[0] == {
        "atgard_id": "korridor_norr",
        "marginal_vinst": 10.0,
        "kumulativ_vinst": 10.0,
        "vinst_ensam": 10.0,
    }
    assert valda[1] == {
        "atgard_id": "tradplantering_ost",
        "marginal_vinst": 8.0,
        "kumulativ_vinst": 18.0,
        "vinst_ensam": 8.0,
    }
    assert data["total_vinst"] == pytest.approx(18.0)


def test_ekologi_atgarder_celf_delvis_overlapp_och_nollkandidat(tmp_path: Path) -> None:
    app = _skapa_app(tmp_path)
    status, data = _post_json(
        app,
        "/api/analys/ekologi-atgarder",
        {
            "k": 5,
            "kandidater": {
                "A": [5.0, 5.0, 0.0],
                "B": [6.0, 4.0, 2.0],  # marginal efter A: max(0,6-5)+max(0,4-5)+max(0,2-0) = 3.0
                "C": [0.0, 0.0, 0.0],  # 0-vinst, ska inte väljas
            },
        },
    )
    assert status == 200, data
    valda = data["valda_atgarder"]
    assert [v["atgard_id"] for v in valda] == ["B", "A"]
    assert valda[0]["vinst_ensam"] == pytest.approx(12.0)
    assert valda[0]["marginal_vinst"] == pytest.approx(12.0)
    # Efter B ([6,4,2]) ger A ([5,5,0]) marginalvinst 0 + 1 + 0 = 1.0
    assert valda[1]["vinst_ensam"] == pytest.approx(10.0)
    assert valda[1]["marginal_vinst"] == pytest.approx(1.0)
    assert valda[1]["kumulativ_vinst"] == pytest.approx(13.0)
    assert data["total_vinst"] == pytest.approx(13.0)


def test_luft_atgarder_celf_kontrakt_och_overlapp(tmp_path: Path) -> None:
    app = _skapa_app(tmp_path)
    status, data = _post_json(
        app,
        "/api/analys/luft-atgarder",
        {
            "k": 2,
            "kandidater": {
                "gron_skarm_1": [4.0, 2.0, 0.0, 0.0],
                "gron_skarm_2": [3.5, 2.0, 0.0, 0.0],
                "hastighet_30": [0.0, 1.0, 3.0, 2.0],
            },
        },
    )
    assert status == 200, data
    assert data["kontrakt"] == "floden-luft-celf/v1"
    assert "garanti_faktor" not in data
    assert data["k"] == 2
    valda = data["valda_atgarder"]
    assert len(valda) == 2
    # gron_skarm_1 (sum 6.0) vs hastighet_30 (sum 6.0) -> alfabetisk tie-break: gron_skarm_1 först
    assert valda[0]["atgard_id"] == "gron_skarm_1"
    assert valda[0]["marginal_vinst"] == pytest.approx(6.0)
    assert valda[1]["atgard_id"] == "hastighet_30"
    # punkt 1 redan täckt till 2.0 >= 1.0, så hastighet_30 ger 0 + 0 + 3 + 2 = 5.0
    assert valda[1]["marginal_vinst"] == pytest.approx(5.0)
    assert valda[1]["kumulativ_vinst"] == pytest.approx(11.0)
    assert valda[1]["vinst_ensam"] == pytest.approx(6.0)
    assert data["total_vinst"] == pytest.approx(11.0)


@pytest.mark.parametrize(
    "endpoint",
    ["/api/analys/ekologi-atgarder", "/api/analys/luft-atgarder"],
)
@pytest.mark.parametrize(
    "ogiltig_kropp",
    [
        b"",
        b"{trasig json",
        b"[]",
        json.dumps({"kandidater": {"A": [1.0]}}).encode(),
        json.dumps({"k": 0, "kandidater": {"A": [1.0]}}).encode(),
        json.dumps({"k": -2, "kandidater": {"A": [1.0]}}).encode(),
        json.dumps({"k": 51, "kandidater": {"A": [1.0]}}).encode(),
        json.dumps({"k": 1.5, "kandidater": {"A": [1.0]}}).encode(),
        json.dumps({"k": True, "kandidater": {"A": [1.0]}}).encode(),
        json.dumps({"k": "1", "kandidater": {"A": [1.0]}}).encode(),
        json.dumps({"k": 1}).encode(),
        json.dumps({"k": 1, "kandidater": {}}).encode(),
        json.dumps({"k": 1, "kandidater": [1.0]}).encode(),
        json.dumps({"k": 1, "kandidater": {"": [1.0]}}).encode(),
        json.dumps({"k": 1, "kandidater": {"A": []}}).encode(),
        json.dumps({"k": 1, "kandidater": {"A": [1.0], "B": [1.0, 2.0]}}).encode(),
        json.dumps({"k": 1, "kandidater": {"A": [-0.1, 1.0]}}).encode(),
        json.dumps({"k": 1, "kandidater": {"A": [True, 1.0]}}).encode(),
        json.dumps({"k": 1, "kandidater": {"A": ["1.0"]}}).encode(),
        b'{"k": 1, "kandidater": {"A": [NaN]}}',
        b'{"k": 1, "kandidater": {"A": [Infinity]}}',
        b'{"k": 1, "kandidater": {"A": [-Infinity]}}',
        json.dumps({"k": 1, "kandidater": {f"A{i}": [1.0] for i in range(101)}}).encode(),
    ],
)
def test_celf_endpoints_strikt_http_400(
    tmp_path: Path, endpoint: str, ogiltig_kropp: bytes
) -> None:
    app = _skapa_app(tmp_path)
    status, _, _ = app.hantera_forfragan("POST", endpoint, ogiltig_kropp)
    assert status == 400, (endpoint, ogiltig_kropp)


# ------------------------------------------------------------------ 3. Auth-krav på CELF-endpoints


@pytest.mark.parametrize(
    "endpoint",
    ["/api/analys/ekologi-atgarder", "/api/analys/luft-atgarder"],
)
def test_celf_endpoints_kraver_inloggning(
    tmp_path: Path, miljo: Miljo, endpoint: str
) -> None:
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=miljo.adapter,
    )
    kropp = json.dumps({"k": 1, "kandidater": {"A": [2.0, 3.0]}}).encode("utf-8")

    # Oinloggad -> 401
    st_ut, _, _ = app.hantera_http("POST", endpoint, {"Origin": ORIGIN}, lambda: kropp)
    assert st_ut == 401

    # Logga in som Anna och försök igen -> 200 (med giltig CSRF-header)
    r = miljo.register
    org = r.skapa_organisation("Exempelkommun")
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, org)
    projekt = r.skapa_projekt(org, "Provområdet")
    r.bjud_in(admin, projekt, str(ANNA["email"]))
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 302
    rubriker = {
        "Origin": ORIGIN,
        **webb.csrf_headers(),
        "Cookie": "; ".join(f"{k}={v}" for k, v in webb.kakor.items()),
    }
    st_in, _, ut = app.hantera_http("POST", endpoint, rubriker, lambda: kropp)
    assert st_in == 200, ut
    svar = json.loads(ut.decode("utf-8"))
    assert svar["total_vinst"] == pytest.approx(5.0)
