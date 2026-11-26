"""Tester för momentan skuggmask (`klockslag_utc` -> `dagsljus.skuggmask_vid_klockslag`)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dagsljus.floden_losare import SkuggmaskKlockslag, VscFasad
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
    inst = _Kv(demo, tmp_path_factory.mktemp("skuggmask-api"))
    ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
    ring.append(ring[0])
    status, _ = inst.json(
        "POST",
        f"{P}/scenarier",
        {
            "namn": "Skuggprov",
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
        {"klockslag_utc": -0.01},
        {"klockslag_utc": 24.01},
        {"klockslag_utc": 25},
        {"klockslag_utc": True},
        {"klockslag_utc": False},
        {"klockslag_utc": "12"},
        {"klockslag_utc": float("nan")},
        {"klockslag_utc": float("inf")},
        {"klockslag_utc": float("-inf")},
        {"klockslag_utc": 12.0, "sammanhangande": True},
        {"datum_iso": "2026-06-21", "klockslag_utc": 15, "sammanhangande": True},
    ],
)
def test_ogiltig_klockslag_utc_ger_http_400(kv: _Kv, ogiltig: dict[str, Any]) -> None:
    for url in (
        f"{P}/scenarier/skuggprov/plan",
        f"{P}/scenarier/skuggprov/kor",
        f"{P}/lage-idag/plan",
        f"{P}/lage-idag/kor",
    ):
        status, svar = kv.json("POST", url, {"analyser": ["dagsljus.sol"], "sol": ogiltig})
        assert status == 400, (url, ogiltig, svar)


def test_gransvarden_for_klockslag_utc() -> None:
    assert _sol({"klockslag_utc": None}) is None
    assert _sol({"klockslag_utc": 0}) == {"klockslag_utc": 0.0}
    assert _sol({"klockslag_utc": 12.5}) == {"klockslag_utc": 12.5}
    assert _sol({"klockslag_utc": 24}) == {"klockslag_utc": 24.0}
    assert _sol({"klockslag_utc": 11.0, "sammanhangande": False}) == {
        "klockslag_utc": 11.0,
        "sammanhangande": False,
    }


# ------------------------------------------------------------------ K20-generering


@pytest.mark.parametrize(
    ("url", "forvantade_scenarier", "har_jamforelse"),
    [
        (f"{P}/scenarier/skuggprov/plan", ["fore", "ritat"], True),
        (f"{P}/lage-idag/plan", ["fore"], False),
    ],
)
def test_klockslag_utc_vaxlar_till_skuggmask_vid_klockslag(
    kv: _Kv, url: str, forvantade_scenarier: list[str], har_jamforelse: bool
) -> None:
    st, svar = kv.json(
        "POST",
        url,
        {
            "analyser": ["dagsljus.sol"],
            "sol": {
                "datum_iso": "2026-06-21",
                "klockslag_utc": 12.5,
            },
        },
    )
    assert st == 200, svar
    k20 = kv.las_k20(svar["fragor"][0]["ref"])
    assert len(k20["analyser"]) == 1
    a = k20["analyser"][0]
    assert a["id"] == "sol"
    assert a["losare"] == "dagsljus.skuggmask_vid_klockslag"
    assert a["scenarier"] == forvantade_scenarier
    assert a["parametrar"]["datum"] == "2026-06-21"
    assert a["parametrar"]["klockslag"] == [12.5]
    assert a["parametrar"]["utc_offset_h"] == 0.0
    assert "steg_min" not in a["parametrar"]
    assert "start_timme" not in a["parametrar"]
    assert "slut_timme" not in a["parametrar"]
    # Lösarens egen validering ska godkänna parametrarna rakt av
    SkuggmaskKlockslag().validera_parametrar(a["parametrar"])

    if har_jamforelse:
        assert len(k20["jamforelser"]) == 1
        jf = k20["jamforelser"][0]
        assert jf["analys"] == "sol"
        assert jf["utdata"] == "skuggmask"
        assert jf["troskel"] == 0.5
    else:
        assert k20["jamforelser"] == []


def test_klockslag_utc_utan_datum_iso_och_grans_24(kv: _Kv) -> None:
    # Utan datum_iso ska mallens datum-lista konverteras till en sträng
    st1, svar1 = kv.json(
        "POST",
        f"{P}/lage-idag/plan",
        {"analyser": ["dagsljus.sol"], "sol": {"klockslag_utc": 24.0}},
    )
    assert st1 == 200, svar1
    k20 = kv.las_k20(svar1["fragor"][0]["ref"])
    a = k20["analyser"][0]
    assert a["losare"] == "dagsljus.skuggmask_vid_klockslag"
    assert isinstance(a["parametrar"]["datum"], str)
    assert a["parametrar"]["klockslag"] == [0.0]
    assert a["parametrar"]["utc_offset_h"] == 0.0
    SkuggmaskKlockslag().validera_parametrar(a["parametrar"])


def test_standardjamforelse_och_mall_utan_jamforelser(kv: _Kv, tmp_path: Path) -> None:
    assert _STANDARDJAMFORELSE["dagsljus.skuggmask_vid_klockslag"] == ("skuggmask", 0.5)
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
                    "steg_min": 10.0,
                    "start_timme": 9.0,
                    "slut_timme": 17.0,
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
        "test-skuggmask",
        "Test",
        ["sol"],
        sol={"klockslag_utc": 14.0, "inkludera_vsc": True},
    )
    per_id = {a["id"]: a for a in k20["analyser"]}
    assert set(per_id) == {"sol", "vsc"}
    assert per_id["sol"]["losare"] == "dagsljus.skuggmask_vid_klockslag"
    assert per_id["sol"]["parametrar"]["datum"] == "2026-03-20"
    assert per_id["sol"]["parametrar"]["klockslag"] == [14.0]
    assert per_id["sol"]["parametrar"]["utc_offset_h"] == 0.0
    SkuggmaskKlockslag().validera_parametrar(per_id["sol"]["parametrar"])
    VscFasad().validera_parametrar(per_id["vsc"]["parametrar"])
    assert k20["jamforelser"] == [
        {
            "analys": "sol",
            "utdata": "skuggmask",
            "fore": "fore",
            "efter": "ritat",
            "troskel": 0.5,
        }
    ]
