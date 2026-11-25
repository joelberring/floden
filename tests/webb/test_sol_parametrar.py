"""Tester för dagsljusparametrar (`sol`) i kartverktyget och webb-API:t."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dagsljus.floden_losare import SammanhangandeSoltimmarMark, SoltimmarMark, VscFasad
from demovarld.demo import LITEN
from demovarld.varld import nytt_hus_polygon
from karna.utredning import Analys, KorbarFraga
from tests.webb.conftest import Demo
from webb.api import FlodenApiApp
from webb.kartverktyg import _STANDARDJAMFORELSE, KartverktygTjanst, _sol
from webb.projektkalla import Mall

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

    def las_k20(self, ref: str) -> dict[str, Any]:
        fil = self.app.fragor._fil_for(ref)  # noqa: SLF001
        data: dict[str, Any] = json.loads(fil.read_text(encoding="utf-8"))
        return data


@pytest.fixture(scope="module")
def kv(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Kv]:
    inst = _Kv(demo, tmp_path_factory.mktemp("sol-param"))
    ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
    ring.append(ring[0])
    status, _ = inst.json(
        "POST",
        f"{P}/scenarier",
        {
            "namn": "Solprov",
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


# ------------------------------------------------------------------ validering (HTTP 400)


@pytest.mark.parametrize(
    "ogiltig",
    [
        "2026-06-21",
        12,
        ["2026-06-21"],
        {"okand": 1},
        {"datum": ["2026-06-21"]},
        {"datum_iso": ""},
        {"datum_iso": "2026-02-30"},
        {"datum_iso": "2026-13-01"},
        {"datum_iso": "20260621"},
        {"datum_iso": "2026-6-21"},
        {"datum_iso": 20260621},
        {"start_timme": -0.1},
        {"start_timme": 23.1},
        {"start_timme": 24},
        {"start_timme": True},
        {"start_timme": "9"},
        {"start_timme": float("nan")},
        {"start_timme": float("inf")},
        {"slut_timme": 0},
        {"slut_timme": 0.9},
        {"slut_timme": 24.1},
        {"slut_timme": False},
        {"slut_timme": "17"},
        {"slut_timme": float("nan")},
        {"start_timme": 12, "slut_timme": 12},
        {"start_timme": 17, "slut_timme": 9},
        {"sammanhangande": "ja"},
        {"sammanhangande": 1},
        {"inkludera_vsc": "true"},
        {"inkludera_vsc": 0},
    ],
)
def test_ogiltiga_solparametrar_ger_http_400(kv: _Kv, ogiltig: Any) -> None:
    for url in (
        f"{P}/scenarier/solprov/plan",
        f"{P}/scenarier/solprov/kor",
        f"{P}/lage-idag/plan",
        f"{P}/lage-idag/kor",
    ):
        status, svar = kv.json("POST", url, {"analyser": ["dagsljus.sol"], "sol": ogiltig})
        assert status == 400, (url, ogiltig, svar)


def test_gransvarden_for_sol_accepteras() -> None:
    assert _sol(None) is None
    assert _sol({}) is None
    assert _sol(
        {
            "datum_iso": None,
            "start_timme": None,
            "slut_timme": None,
            "sammanhangande": None,
            "inkludera_vsc": None,
        }
    ) is None
    assert _sol(
        {
            "datum_iso": "2026-06-21",
            "start_timme": 0,
            "slut_timme": 24,
            "sammanhangande": False,
            "inkludera_vsc": True,
        }
    ) == {
        "datum_iso": "2026-06-21",
        "start_timme": 0.0,
        "slut_timme": 24.0,
        "sammanhangande": False,
        "inkludera_vsc": True,
    }
    assert _sol({"start_timme": 23, "slut_timme": 24}) == {"start_timme": 23.0, "slut_timme": 24.0}
    assert _sol({"start_timme": 0, "slut_timme": 1}) == {"start_timme": 0.0, "slut_timme": 1.0}


# ------------------------------------------------------------------ oförändrat när sol är None


def test_sol_null_ger_oforandrat_beteende(kv: _Kv) -> None:
    st1, p1 = kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["dagsljus.sol"]})
    st2, p2 = kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["dagsljus.sol"], "sol": None})
    st3, p3 = kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["dagsljus.sol"], "sol": {}})
    assert st1 == 200 and st2 == 200 and st3 == 200
    assert p1["fragor"][0]["plan"]["plan_nyckel"] == p2["fragor"][0]["plan"]["plan_nyckel"]
    assert p1["fragor"][0]["plan"]["plan_nyckel"] == p3["fragor"][0]["plan"]["plan_nyckel"]


# ------------------------------------------------------------------ K20 scenario + lage-idag


@pytest.mark.parametrize(
    ("url", "forvantade_scenarier"),
    [
        (f"{P}/scenarier/solprov/plan", ["fore", "ritat"]),
        (f"{P}/lage-idag/plan", ["fore"]),
    ],
)
def test_sol_parametrar_hamnar_exakt_i_k20(
    kv: _Kv, url: str, forvantade_scenarier: list[str]
) -> None:
    st, svar = kv.json(
        "POST",
        url,
        {
            "analyser": ["dagsljus.sol"],
            "sol": {
                "datum_iso": "2026-06-21",
                "start_timme": 9,
                "slut_timme": 17,
            },
        },
    )
    assert st == 200, svar
    ref = svar["fragor"][0]["ref"]
    k20 = kv.las_k20(ref)
    assert len(k20["analyser"]) == 1
    a = k20["analyser"][0]
    assert a["id"] == "sol"
    assert a["losare"] == "dagsljus.soltimmar_mark"
    assert a["scenarier"] == forvantade_scenarier
    assert a["parametrar"]["datum"] == ["2026-06-21"]
    assert a["parametrar"]["start_timme"] == 9.0
    assert a["parametrar"]["slut_timme"] == 17.0
    # Lösarens egen validering ska godkänna parametrarna rakt av
    SoltimmarMark().validera_parametrar(a["parametrar"])


@pytest.mark.parametrize(
    ("url", "har_jamforelse"),
    [
        (f"{P}/scenarier/solprov/plan", True),
        (f"{P}/lage-idag/plan", False),
    ],
)
def test_sammanhangande_vaxlar_losare_och_jamforelse(
    kv: _Kv, url: str, har_jamforelse: bool
) -> None:
    st, svar = kv.json(
        "POST",
        url,
        {
            "analyser": ["dagsljus.sol"],
            "sol": {
                "datum_iso": "2026-03-20",
                "start_timme": 9,
                "slut_timme": 17,
                "sammanhangande": True,
            },
        },
    )
    assert st == 200, svar
    k20 = kv.las_k20(svar["fragor"][0]["ref"])
    a = k20["analyser"][0]
    assert a["losare"] == "dagsljus.soltimmar_sammanhangande"
    assert a["parametrar"]["datum"] == ["2026-03-20"]
    assert a["parametrar"]["start_timme"] == 9.0
    assert a["parametrar"]["slut_timme"] == 17.0
    SammanhangandeSoltimmarMark().validera_parametrar(a["parametrar"])

    if har_jamforelse:
        assert len(k20["jamforelser"]) == 1
        jf = k20["jamforelser"][0]
        assert jf["analys"] == "sol"
        assert jf["utdata"] == "sammanhangande_soltimmar"
        assert jf["troskel"] == 0.5
    else:
        assert k20["jamforelser"] == []


@pytest.mark.parametrize(
    "url",
    [
        f"{P}/scenarier/solprov/plan",
        f"{P}/lage-idag/plan",
    ],
)
def test_inkludera_vsc_tar_med_mallens_vsc_fasad(kv: _Kv, url: str) -> None:
    st, svar = kv.json(
        "POST",
        url,
        {
            "analyser": ["dagsljus.sol"],
            "sol": {
                "datum_iso": "2026-06-21",
                "inkludera_vsc": True,
            },
        },
    )
    assert st == 200, svar
    k20 = kv.las_k20(svar["fragor"][0]["ref"])
    per_id = {a["id"]: a for a in k20["analyser"]}
    assert set(per_id) == {"sol", "vsc"}
    assert per_id["vsc"]["losare"] == "dagsljus.vsc_fasad"
    # VSC ska INTE få datum/start_timme/slut_timme (det är okända parametrar för vsc_fasad)
    assert "datum" not in per_id["vsc"]["parametrar"]
    assert "start_timme" not in per_id["vsc"]["parametrar"]
    assert "slut_timme" not in per_id["vsc"]["parametrar"]
    VscFasad().validera_parametrar(per_id["vsc"]["parametrar"])
    assert "vsc" in svar["fragor"][0]["analyser"]


def test_standardjamforelse_och_syntetisk_vsc_utan_mall_vsc(kv: _Kv, tmp_path: Path) -> None:
    assert _STANDARDJAMFORELSE["dagsljus.soltimmar_sammanhangande"] == (
        "sammanhangande_soltimmar",
        0.5,
    )
    fraga = KorbarFraga(
        fraga_id="g",
        beskrivning="",
        scenarier={},
        analyser=(
            Analys(
                id="sol",
                losare="dagsljus.soltimmar_mark",
                scenarier=("bas",),
                fokus=(100.0, 200.0, 300.0, 400.0),
                parametrar={
                    "datum": ["2026-03-20"],
                    "cellstorlek_m": 2.0,
                    "hojd_max_m": 30.0,
                    "h_min_grader": 5.0,
                },
            ),
        ),
        jamforelser=(),
    )
    mall = Mall("g", "dagsljus", fraga, tmp_path / "bas", (), "bas")
    k20 = KartverktygTjanst._k20(  # noqa: SLF001
        kv.app.kartverktyg,
        mall,
        tmp_path / "fraga.json",
        tmp_path / "k9.json",
        "test-dagsljus",
        "Test",
        ["sol"],
        sol={"sammanhangande": True, "inkludera_vsc": True},
    )
    per_id = {a["id"]: a for a in k20["analyser"]}
    assert set(per_id) == {"sol", "vsc"}
    assert per_id["sol"]["losare"] == "dagsljus.soltimmar_sammanhangande"
    assert per_id["vsc"]["losare"] == "dagsljus.vsc_fasad"
    assert per_id["vsc"]["fokus"] == [100.0, 200.0, 300.0, 400.0]
    VscFasad().validera_parametrar(per_id["vsc"]["parametrar"])
    assert k20["jamforelser"] == [
        {
            "analys": "sol",
            "utdata": "sammanhangande_soltimmar",
            "fore": "fore",
            "efter": "ritat",
            "troskel": 0.5,
        }
    ]
