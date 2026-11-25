"""Tester för reglerbara regnparametrar (kartverktyg + API) och 6-stegs vattendjupklasser."""

from __future__ import annotations

import json
import re
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
from gemensamt.regn import generera_blockregn, skapa_regn_fran_idf_kalla
from karna.utredning import Analys, KorbarFraga
from tests.webb.conftest import Demo
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel
from webb.kartverktyg import KartverktygTjanst, _regn, _tillampa_regn
from webb.projektkalla import Mall
from webb.resultatlager import _VATTEN_KLASSER, rita_png, teckenforklaring

REPO = Path(__file__).resolve().parents[2]
P = "/api/kartverktyg/demokvarteret"

FORVANTADE_KLASSER = [
    (0.02, "#cfe8ff", "0,02–0,10 m · ytavrinning"),
    (0.1, "#7ec4f8", "0,10–0,20 m · märkbart vatten"),
    (0.2, "#2f8be6", "0,20–0,30 m · svårframkomligt"),
    (0.3, "#1a52b8", "0,30–0,50 m · ofarbart / risk"),
    (0.5, "#182b7a", "0,50–1,00 m · allvarligt djup"),
    (1.0, "#3b1363", "> 1,00 m · mycket djupt"),
]


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

    def vanta_lage(self, korning_id: str, tidsgrans: float = 120.0) -> dict[str, Any]:
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
    inst = _Kv(demo, tmp_path_factory.mktemp("regn-djup"))
    ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
    ring.append(ring[0])
    status, _ = inst.json(
        "POST",
        f"{P}/scenarier",
        {
            "namn": "Regnprov",
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


# ------------------------------------------------------------------ 6-stegs vattendjupfärger


def test_sex_vattendjupklasser_farger_och_etiketter() -> None:
    assert list(_VATTEN_KLASSER) == [(g, f) for g, f, _ in FORVANTADE_KLASSER]
    tf = teckenforklaring("vatten", 0.0, 2.0)
    assert tf["typ"] == "klasser"
    assert tf["klasser"] == [
        {"fran": g, "farg": f, "etikett": e} for g, f, e in FORVANTADE_KLASSER
    ]


def test_rita_png_alla_sex_vattendjupklasser_och_transparent_under_2cm() -> None:
    v = np.array([[0.0, 0.019, 0.02, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5]], dtype=np.float32)
    png, bredd, hojd, _ = rita_png(v, "vatten", lo=0.0, hi=2.5)
    assert (bredd, hojd) == (9, 1)
    rgba = np.asarray(Image.open(BytesIO(png)).convert("RGBA"))
    assert list(rgba[0, :, 3]) == [0, 0, 255, 255, 255, 255, 255, 255, 255]
    forvand = [
        (0xCF, 0xE8, 0xFF),
        (0x7E, 0xC4, 0xF8),
        (0x2F, 0x8B, 0xE6),
        (0x1A, 0x52, 0xB8),
        (0x18, 0x2B, 0x7A),
        (0x3B, 0x13, 0x63),
        (0x3B, 0x13, 0x63),
    ]
    for idx, rgb in enumerate(forvand, start=2):
        assert tuple(rgba[0, idx, :3]) == rgb


def test_vatten_klasser_paritet_med_analyslager_ts() -> None:
    ts = (REPO / "webb/src/flode/analyslager.ts").read_text(encoding="utf-8")
    sektion = ts.split("export const VATTEN_KLASSER")[1].split("];")[0]
    klasser = re.findall(
        r'fran: ([0-9.]+), farg: "(#[0-9a-f]{6})", etikett: "([^"]+)"', sektion
    )
    assert [(float(g), f, e) for g, f, e in klasser] == FORVANTADE_KLASSER


# ------------------------------------------------------------------ validering av regnparametrar


@pytest.mark.parametrize(
    "ogiltig",
    [
        "100mm",
        100,
        ["regn_mm"],
        {"okand": 10},
        {"regn_mm": 0},
        {"regn_mm": -5},
        {"regn_mm": 500.1},
        {"regn_mm": float("nan")},
        {"regn_mm": float("inf")},
        {"regn_mm": True},
        {"regn_mm": "50"},
        {"aterkomsttid_ar": 0.9},
        {"aterkomsttid_ar": 10001},
        {"aterkomsttid_ar": False},
        {"varaktighet_min": 4.9},
        {"varaktighet_min": 2881},
        {"klimatfaktor": 0.49},
        {"klimatfaktor": 3.01},
        {"regn_mm": 80, "aterkomsttid_ar": 100},
        {"regn_mm": 80, "klimatfaktor": 1.25},
    ],
)
def test_ogiltiga_regnparametrar_ger_http_400(kv: _Kv, ogiltig: Any) -> None:
    for url in (
        f"{P}/scenarier/regnprov/plan",
        f"{P}/scenarier/regnprov/kor",
        f"{P}/lage-idag/plan",
        f"{P}/lage-idag/kor",
    ):
        status, svar = kv.json("POST", url, {"analyser": ["vatten.skyfall"], "regn": ogiltig})
        assert status == 400, (url, ogiltig, svar)


def test_gransvarden_for_regn_accepteras() -> None:
    assert _regn(None) is None
    assert _regn({}) is None
    assert _regn({"regn_mm": None, "aterkomsttid_ar": None}) is None
    assert _regn({"regn_mm": 0.01}) == {"regn_mm": 0.01}
    assert _regn({"regn_mm": 500}) == {"regn_mm": 500.0}
    assert _regn({"aterkomsttid_ar": 1, "varaktighet_min": 5, "klimatfaktor": 0.5}) == {
        "aterkomsttid_ar": 1.0,
        "varaktighet_min": 5.0,
        "klimatfaktor": 0.5,
    }
    assert _regn({"aterkomsttid_ar": 10000, "varaktighet_min": 2880, "klimatfaktor": 3.0}) == {
        "aterkomsttid_ar": 10000.0,
        "varaktighet_min": 2880.0,
        "klimatfaktor": 3.0,
    }


# ------------------------------------------------------------------ projektinfo regn_forval


def test_projektinfo_returnerar_regn_forval_ur_skyfallsmall(kv: _Kv) -> None:
    status, info = kv.json("GET", P)
    assert status == 200
    rf = info["regn_forval"]
    assert rf is not None
    assert rf["kalla"] == "stockholmsregn-2024"
    assert rf["aterkomsttid_ar"] == 100.0
    assert rf["varaktighet_min"] == 360.0
    assert rf["klimatfaktor"] == 1.4
    forvantad = skapa_regn_fran_idf_kalla(
        "stockholmsregn-2024", 100.0, klimatfaktor=1.4, typ="block", varaktighet_min=360.0
    )
    assert rf["regn_mm"] == pytest.approx(forvantad.total_djup_mm, rel=1e-9)


def test_regn_forval_null_utan_skyfallsmall_och_direkt_mm_utan_idf() -> None:
    assert KartverktygTjanst._regn_forval({}) is None  # noqa: SLF001
    fraga = KorbarFraga(
        fraga_id="g",
        beskrivning="",
        scenarier={},
        analyser=(
            Analys(
                id="skyfall",
                losare="vatten.statisk_fyllning",
                scenarier=("bas",),
                parametrar={"regn_mm": 72.5},
            ),
        ),
        jamforelser=(),
    )
    mall = Mall("g", "vatten", fraga, Path("."), (), "bas")
    rf = KartverktygTjanst._regn_forval({"vatten": mall})  # noqa: SLF001
    assert rf == {
        "aterkomsttid_ar": None,
        "varaktighet_min": None,
        "klimatfaktor": None,
        "regn_mm": 72.5,
        "kalla": None,
    }


# ------------------------------------------------------------------ tillämpning på lösare & K20


def test_regn_null_ger_oforandrat_beteende_och_cachetraff(kv: _Kv) -> None:
    st1, p1 = kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["vatten.skyfall"]})
    st2, p2 = kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["vatten.skyfall"], "regn": None})
    assert st1 == 200 and st2 == 200
    assert p1["fragor"][0]["plan"]["plan_nyckel"] == p2["fragor"][0]["plan"]["plan_nyckel"]
    assert all(j["i_cache"] for j in p2["fragor"][0]["plan"]["jobb"])


def test_regn_mm_rensar_idf_nycklar_och_ger_annan_berakning(kv: _Kv) -> None:
    st_plan, plan = kv.json(
        "POST",
        f"{P}/scenarier/regnprov/plan",
        {"analyser": ["vatten.skyfall"], "regn": {"regn_mm": 120}},
    )
    assert st_plan == 200, plan
    fil = kv.rot / "kartverktyg" / "fragor" / f"{plan['fragor'][0]['fraga_id']}.json"
    par = json.loads(fil.read_text(encoding="utf-8"))["analyser"][0]["parametrar"]
    assert par["regn_mm"] == 120.0
    for k in ("idf", "aterkomsttid_ar", "klimatfaktor", "regntyp", "varaktighet_min"):
        assert k not in par

    # Kör två olika regn i läget idag och verifiera att större regn ger större maxdjup/volym.
    st_lag, k_lag = kv.json(
        "POST",
        f"{P}/lage-idag/kor",
        {"analyser": ["vatten.skyfall"], "regn": {"regn_mm": 15}},
    )
    assert st_lag == 202, k_lag
    res_lag = kv.vanta_lage(k_lag["fragor"][0]["korning"]["korning_id"])
    assert res_lag["antaganden"]["regn"][0]["regn_mm"] == 15.0

    st_hog, k_hog = kv.json(
        "POST",
        f"{P}/lage-idag/kor",
        {"analyser": ["vatten.skyfall"], "regn": {"regn_mm": 120}},
    )
    assert st_hog == 202, k_hog
    res_hog = kv.vanta_lage(k_hog["fragor"][0]["korning"]["korning_id"])
    assert res_hog["antaganden"]["regn"][0]["regn_mm"] == 120.0

    lag_djup = float(res_lag["tabeller"][0]["rader"][0]["varden"]["max_djup_m"])
    hog_djup = float(res_hog["tabeller"][0]["rader"][0]["varden"]["max_djup_m"])
    assert hog_djup > lag_djup

    # Resultatlagret använder de nya 6-stegs vattenklasserna med tydliga etiketter.
    vatten_lager = next(la for la in res_hog["lager"] if la["roll"] == "max_djup")
    assert vatten_lager["teckenforklaring"]["klasser"] == [
        {"fran": g, "farg": f, "etikett": e} for g, f, e in FORVANTADE_KLASSER
    ]


def test_idf_parametrar_styr_skyfallslosaren_och_uppdaterar_antaganden(kv: _Kv) -> None:
    st_kor, k = kv.json(
        "POST",
        f"{P}/lage-idag/kor",
        {
            "analyser": ["vatten.skyfall"],
            "regn": {"aterkomsttid_ar": 10, "varaktighet_min": 60, "klimatfaktor": 1.25},
        },
    )
    assert st_kor == 202, k
    res = kv.vanta_lage(k["fragor"][0]["korning"]["korning_id"])
    regn_post = res["antaganden"]["regn"][0]
    assert regn_post["kalla"] == "stockholmsregn-2024"
    assert regn_post["aterkomsttid_ar"] == 10.0
    assert regn_post["varaktighet_min"] == 60.0
    assert regn_post["klimatfaktor"] == 1.25
    forvantad = skapa_regn_fran_idf_kalla(
        "stockholmsregn-2024", 10.0, klimatfaktor=1.25, typ="block", varaktighet_min=60.0
    )
    assert regn_post["regn_mm"] == pytest.approx(forvantad.total_djup_mm, rel=1e-6)


def test_tillampa_regn_pa_geografimall_och_dynamik() -> None:
    # Geografimall (enbart regn_mm): IDF-val beräknas deterministiskt via Dahlström 2010 / P110.
    par_geo: dict[str, Any] = {"regn_mm": 70.0}
    _tillampa_regn(
        "vatten.statisk_fyllning",
        par_geo,
        {"aterkomsttid_ar": 10.0, "varaktighet_min": 60.0, "klimatfaktor": 1.25},
    )
    forvantad = generera_blockregn(10.0, klimatfaktor=1.25, varaktighet_min=60.0)
    assert par_geo["regn_mm"] == pytest.approx(round(forvantad.total_djup_mm, 3))
    assert par_geo["varaktighet_min"] == 60.0

    # Ensam klimatfaktor utan IDF-källa eller återkomsttid ger 400 (inga gissningar).
    with pytest.raises(FlodenWebbFel) as exc:
        _tillampa_regn("vatten.statisk_fyllning", {"regn_mm": 70.0}, {"klimatfaktor": 1.25})
    assert exc.value.status_kod == 400

    # vatten.dynamik med direkt regn_mm ger konstant blockregn med exakt volym.
    par_dyn: dict[str, Any] = {
        "idf": "stockholmsregn-2024",
        "aterkomsttid_ar": 100.0,
        "varaktighet_min": 60.0,
    }
    _tillampa_regn("vatten.dynamik", par_dyn, {"regn_mm": 90.0})
    assert "idf" not in par_dyn and "regn_mm" not in par_dyn
    assert par_dyn["regn_tid_s"] == [0.0, 3600.0]
    assert par_dyn["regn_mm_h"] == [90.0, 90.0]

    # Direkt regn_mm + avtappning behåller mallens varaktighet om ingen egen skickas.
    par_avt: dict[str, Any] = {
        "idf": "stockholmsregn-2024",
        "aterkomsttid_ar": 100.0,
        "varaktighet_min": 360.0,
        "klimatfaktor": 1.4,
    }
    _tillampa_regn("vatten.statisk_fyllning", par_avt, {"regn_mm": 80.0}, avtappning_mm_h=10.0)
    assert par_avt["regn_mm"] == 80.0
    assert par_avt["varaktighet_min"] == 360.0
    assert "idf" not in par_avt
