"""Tester för Spår 2A: regntyper, berakningstyp, tidsserie-API, tidsstegs-PNG och behörighet."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from demovarld.demo import LITEN
from demovarld.varld import nytt_hus_polygon
from gemensamt.regn import generera_blockregn, generera_cds_regn
from tests.konto.conftest import ANNA, Miljo
from tests.webb.conftest import Demo
from tests.webb.test_inloggning import _http
from tests.webb.test_projekt_per_anvandare import BO, _setup
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel
from webb.kartverktyg import _STANDARDJAMFORELSE, _regn, _tillampa_regn
from webb.moduler import visningsnamn
from webb.resultatlager import ETIKETTER, SKALA_FOR_ROLL, etikett_och_enhet, farg_for_roll

REPO = Path(__file__).resolve().parents[2]
P = "/api/kartverktyg/demokvarteret"


class _Kv:
    def __init__(self, demo: Demo, rot: Path) -> None:
        self.demo = demo
        self.rot = rot
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=demo.rot / "korningar",
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot],
        )
        self.app.fragor.demomapp = demo.varld

    def json(self, verb: str, sokvag: str, kropp: Any = None) -> tuple[int, dict[str, Any]]:
        data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
        status, _, ut = self.app.hantera_forfragan(verb, sokvag, data)
        return status, json.loads(ut)

    def binart(self, sokvag: str) -> tuple[int, dict[str, str], bytes]:
        return self.app.hantera_forfragan("GET", sokvag, b"")

    def vanta_klar(self, korning_id: str, tidsgrans: float = 120.0) -> dict[str, Any]:
        slut = time.monotonic() + tidsgrans
        while time.monotonic() < slut:
            st = self.app.fragor.korning_status(korning_id)
            assert st["status"] != "fel", st
            if st["status"] == "klar":
                return self.app.fragor.resultat(korning_id)
            time.sleep(0.2)
        raise AssertionError(f"Körning {korning_id} blev inte klar i tid.")


@pytest.fixture(scope="module")
def kv(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Kv]:
    inst = _Kv(demo, tmp_path_factory.mktemp("dynamik-tid"))
    ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
    ring.append(ring[0])
    status, _ = inst.json(
        "POST",
        f"{P}/scenarier",
        {
            "namn": "Dynamikprov",
            "andringar": [
                {
                    "typ": "byggnad_ny",
                    "andring_id": "nytt_hus",
                    "geometri": {"type": "Polygon", "coordinates": [ring]},
                    "egenskaper": {"hojd_m": 18},
                }
            ],
        },
    )
    assert status == 200
    yield inst


# ------------------------------------------------------------------ etiketter & moduler


def test_etiketter_och_modulnamn_for_dynamik() -> None:
    assert ETIKETTER["tid_till_0_1m"] == ("Tid till 10 cm djup", "s")
    assert ETIKETTER["tid_till_0_2m"] == ("Tid till 20 cm djup", "s")
    assert etikett_och_enhet("tid_till_0_1m") == ("Tid till 10 cm djup", "s")
    assert etikett_och_enhet("tid_till_0_2m") == ("Tid till 20 cm djup", "s")
    assert SKALA_FOR_ROLL["tid_till_0_1m"] == "tid"
    assert SKALA_FOR_ROLL["tid_till_0_2m"] == "tid"
    assert farg_for_roll("tid_till_0_1m") == "tid"
    assert farg_for_roll("tid_till_0_2m") == "tid"
    assert visningsnamn("vatten.dynamik") == "Skyfall i tid (dynamisk)"
    assert _STANDARDJAMFORELSE["vatten.dynamik"] == ("max_djup", 0.01)


# ------------------------------------------------------------------ validering av regntyp/typ


@pytest.mark.parametrize(
    "ogiltig",
    [
        {"regntyp": "triangel"},
        {"regntyp": 123},
        {"regntyp": True},
        {"berakningstyp": "2d"},
        {"berakningstyp": False},
        {"regntyp": "cds", "regn_mm": 80},
        {"regntyp": "cds", "aterkomsttid_ar": 100, "varaktighet_min": 7},
        {"regntyp": "cds", "aterkomsttid_ar": 100, "varaktighet_min": 62},
        {"regntyp": "cds", "aterkomsttid_ar": 100, "varaktighet_min": 3},
    ],
)
def test_ogiltig_regntyp_eller_berakningstyp_ger_http_400(kv: _Kv, ogiltig: dict[str, Any]) -> None:
    with pytest.raises(FlodenWebbFel) as exc:
        _regn(ogiltig)
    assert exc.value.status_kod == 400
    status, svar = kv.json(
        "POST",
        f"{P}/lage-idag/plan",
        {"analyser": ["vatten.skyfall"], "regn": ogiltig},
    )
    assert status == 400, (ogiltig, svar)


def test_giltiga_regntyp_och_berakningstyp_samt_tillampa_regn() -> None:
    val = _regn(
        {
            "berakningstyp": "dynamisk",
            "regntyp": "cds",
            "aterkomsttid_ar": 100,
            "varaktighet_min": 60,
            "klimatfaktor": 1.25,
        }
    )
    assert val == {
        "berakningstyp": "dynamisk",
        "regntyp": "cds",
        "aterkomsttid_ar": 100.0,
        "varaktighet_min": 60.0,
        "klimatfaktor": 1.25,
    }

    # Mall med IDF skickar vidare regntyp="cds"
    par_idf: dict[str, Any] = {
        "idf": "stockholmsregn-2024",
        "aterkomsttid_ar": 100.0,
        "varaktighet_min": 60.0,
        "klimatfaktor": 1.4,
        "regntyp": "block",
    }
    _tillampa_regn("vatten.dynamik", par_idf, {"regntyp": "cds", "berakningstyp": "dynamisk"})
    assert par_idf["regntyp"] == "cds"

    # Mall utan IDF + vatten.dynamik + regntyp="cds" använder generera_cds_regn
    par_cds: dict[str, Any] = {"regn_mm": 70.0}
    _tillampa_regn(
        "vatten.dynamik",
        par_cds,
        {"aterkomsttid_ar": 100.0, "varaktighet_min": 60.0, "klimatfaktor": 1.25, "regntyp": "cds"},
    )
    forv_cds = generera_cds_regn(100.0, klimatfaktor=1.25, varaktighet_min=60.0)
    assert par_cds["regn_tid_s"] == list(forv_cds.tid_s)
    assert par_cds["regn_mm_h"] == list(forv_cds.intensitet_mm_h)
    assert par_cds["regntyp"] == "cds"
    assert "regn_mm" not in par_cds

    # Mall utan IDF + vatten.dynamik + regntyp="block" använder generera_blockregn
    par_blk: dict[str, Any] = {"regn_mm": 70.0}
    _tillampa_regn(
        "vatten.dynamik",
        par_blk,
        {
            "aterkomsttid_ar": 100.0,
            "varaktighet_min": 60.0,
            "klimatfaktor": 1.25,
            "regntyp": "block",
        },
    )
    forv_blk = generera_blockregn(100.0, klimatfaktor=1.25, varaktighet_min=60.0)
    assert par_blk["regn_tid_s"] == list(forv_blk.tid_s)
    assert par_blk["regn_mm_h"] == list(forv_blk.intensitet_mm_h)
    assert par_blk["regntyp"] == "block"


# ------------------------------------------------------------------ K20-byte & end-to-end API


def test_dynamisk_berakning_i_lage_idag_och_scenario_tidsserie_och_png(kv: _Kv) -> None:
    # 1. Plan i scenario med berakningstyp="dynamisk" byter lösare till vatten.dynamik
    st_plan, plan = kv.json(
        "POST",
        f"{P}/scenarier/dynamikprov/plan",
        {
            "analyser": ["vatten.skyfall"],
            "regn": {
                "berakningstyp": "dynamisk",
                "regntyp": "cds",
                "aterkomsttid_ar": 100,
                "varaktighet_min": 60,
                "klimatfaktor": 1.25,
            },
        },
    )
    assert st_plan == 200, plan
    k20_fil = kv.rot / "kartverktyg" / "fragor" / f"{plan['fragor'][0]['fraga_id']}.json"
    k20 = json.loads(k20_fil.read_text(encoding="utf-8"))
    assert k20["analyser"][0]["losare"] == "vatten.dynamik"
    assert k20["analyser"][0]["parametrar"]["regntyp"] == "cds"

    # 2. Kör dynamisk CDS i läget idag
    st_kor, kor = kv.json(
        "POST",
        f"{P}/lage-idag/kor",
        {
            "analyser": ["vatten.skyfall"],
            "regn": {
                "berakningstyp": "dynamisk",
                "regntyp": "cds",
                "aterkomsttid_ar": 100,
                "varaktighet_min": 60,
                "klimatfaktor": 1.25,
            },
        },
    )
    assert st_kor == 202, kor
    kid = kor["fragor"][0]["korning"]["korning_id"]
    res = kv.vanta_klar(kid)

    # Resultat har tidsserie_scenarier och ankomsttidslager med "tid"-skala
    assert res["tidsserie_scenarier"] == ["fore"]
    roller = {la["roll"]: la for la in res["lager"]}
    assert "tid_till_0_1m" in roller and roller["tid_till_0_1m"]["farg"] == "tid"
    assert roller["tid_till_0_1m"]["etikett"] == "Tid till 10 cm djup"
    assert "tid_till_0_2m" in roller and roller["tid_till_0_2m"]["farg"] == "tid"

    # 3. GET /api/korningar/<id>/tidsserie?scenario=fore
    st_ts, ts = kv.json("GET", f"/api/korningar/{kid}/tidsserie?scenario=fore")
    assert st_ts == 200, ts
    assert ts["kontrakt"] == "floden-tidsserie/v1"
    assert ts["regntyp"] == "cds"
    assert set(ts["utbredning"]) == {"x_min", "y_min", "x_max", "y_max"}
    assert ts["utbredning"]["x_max"] > ts["utbredning"]["x_min"]
    assert 12 <= len(ts["tidssteg"]) <= 16
    assert ts["tidssteg"][0]["index"] == 0
    assert ts["tidssteg"][0]["png_url"] == f"/api/korningar/{kid}/tidssteg/fore/0.png"

    # 4. GET /api/korningar/<id>/tidssteg/fore/<index>.png + diskcache
    sista = ts["tidssteg"][-1]
    st_png, hdrs, png_bytes = kv.binart(sista["png_url"])
    assert st_png == 200
    assert hdrs["Content-Type"] == "image/png"
    img = Image.open(BytesIO(png_bytes))
    assert img.mode == "RGBA"
    rgba = np.asarray(img)
    assert rgba.ndim == 3 and rgba.shape[2] == 4
    cache_fil = (
        kv.app.fragor.korningsmapp / kid / "webb" / "tidssteg" / f"fore_{sista['index']}.png"
    )
    assert cache_fil.is_file()

    # Andra anropet serverar cachad fil identiskt
    st_png2, _, png_bytes2 = kv.binart(sista["png_url"])
    assert st_png2 == 200
    assert png_bytes2 == png_bytes

    # Ogiltiga index avvisas tydligt
    assert kv.binart(f"/api/korningar/{kid}/tidssteg/fore/999.png")[0] == 404
    assert kv.binart(f"/api/korningar/{kid}/tidssteg/fore/abc.png")[0] == 400

    # 5. Statisk körning har tom tidsserie_scenarier och 404 på /tidsserie
    st_stat, kor_stat = kv.json(
        "POST",
        f"{P}/lage-idag/kor",
        {"analyser": ["vatten.skyfall"], "regn": {"berakningstyp": "statisk", "regn_mm": 40}},
    )
    assert st_stat == 202
    kid_stat = kor_stat["fragor"][0]["korning"]["korning_id"]
    res_stat = kv.vanta_klar(kid_stat)
    assert res_stat["tidsserie_scenarier"] == []
    assert kv.json("GET", f"/api/korningar/{kid_stat}/tidsserie?scenario=fore")[0] == 404


# ------------------------------------------------------------------ behörighet på nya endpoints


def test_tidsserie_och_tidssteg_kraver_konto_far_se_korning(
    demo: Demo, tmp_path: Path, miljo: Miljo
) -> None:
    """Med inloggning på kräver både `/tidsserie` och `/tidssteg/...png` läsbehörighet."""
    st = _setup(miljo)
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[demo.fragor],
        cachemapp=demo.rot / "cache",
        korningsmapp=tmp_path / "korningar",
        kartverktygsmapp=tmp_path / "kartverktyg",
        datamappar=[tmp_path],
        konto=miljo.adapter,
    )
    app.fragor.demomapp = demo.varld

    # Skapa en dynamisk körning knuten till Annas projekt (Bo saknar åtkomst)
    poster = app.kartverktyg._skriv_lage_fragor(  # noqa: SLF001
        "demokvarteret",
        ["vatten.skyfall"],
        regn=_regn(
            {
                "berakningstyp": "dynamisk",
                "regntyp": "block",
                "aterkomsttid_ar": 10,
                "varaktighet_min": 30,
                "klimatfaktor": 1.0,
            }
        ),
    )
    kid = "auth-dyn-test"
    app.fragor.kor_synkront(poster[0]["ref"], kid, projekt_id=st.anna_projekt)

    anna = miljo.webblasare()
    bo = miljo.webblasare()
    assert anna.logga_in(miljo.idp, ANNA)[0] == 302
    assert bo.logga_in(miljo.idp, BO)[0] == 302

    for sokvag in (
        f"/api/korningar/{kid}/tidsserie?scenario=fore",
        f"/api/korningar/{kid}/tidssteg/fore/0.png",
    ):
        # Utan inloggning -> 401
        assert _http(app, "GET", sokvag, None)[0] == 401
        # Bo (fel projekt) -> 404 (aldrig 403/200)
        assert _http(app, "GET", sokvag, bo)[0] == 404
        # Anna (lasare i projektet) -> 200
        assert _http(app, "GET", sokvag, anna)[0] == 200
