# ruff: noqa: E501
"""Kartverktyget och ritlagren (K50, R2): lager i projektinfo, översättning vid kontroll och sparande,
äldre förslagsfiler, samma K9-nyckel som en handskriven ändring, och stängda lager."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from floden_mcp.oppenhet import Oppenhet
from karna import ritlager as rl
from tests.webb.conftest import Demo
from tests.webb.test_kartverktyg import Kv, demohus, kvadrat

P = "/api/kartverktyg/demokvarteret"


@pytest.fixture(scope="module")
def kv(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Kv]:
    yield Kv(demo, tmp_path_factory.mktemp("kartverktyg_ritlager"))


def objekt_av(andring: dict[str, Any], lager_id: str = "hus", oid: str | None = None) -> dict[str, Any]:
    return {"objekt_id": oid or andring["andring_id"], "lager_id": lager_id, "geometri": andring["geometri"], "egenskaper": andring["egenskaper"]}


def test_projektinfo_har_forvalda_lager_och_roller(kv: Kv) -> None:
    _, info = kv.json("GET", P)
    r = info["ritlager"]
    assert r["kontrakt"] == "floden-ritlager/v1"
    assert [x["roll"] for x in r["lager"]] == list(rl.ROLLER)
    assert {x["roll"] for x in r["roller"]} == set(rl.ROLLER)
    assert all(x["blir"] for x in r["roller"])


def test_hus_i_lager_ger_samma_k9_som_en_fri_andring(kv: Kv) -> None:
    fri = demohus(18)
    _, a = kv.json("POST", f"{P}/validera", {"andringar": [fri]})
    _, b = kv.json("POST", f"{P}/validera", {"andringar": [], "ritlager": {"objekt": [objekt_av(fri)]}})
    assert a["giltig"] and b["giltig"], b
    assert a["andringar"] == b["andringar"]
    assert b["hus"] == a["hus"]
    assert a["ritlager"] is None
    assert [x["lager_id"] for x in b["ritlager"]["lager"]][0] == "hus"  # förvalda lager när inga skickas


def test_fel_i_ritat_objekt_ger_tydligt_fel_inte_undantag(kv: Kv) -> None:
    fri = demohus(18)
    o = objekt_av(fri, lager_id="mark")  # mark kräver nivå, och hojd_m hör inte till rollen
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [], "ritlager": {"objekt": [o]}})
    assert not v["giltig"] and "egenskaper som rollen inte tar" in " ".join(v["fel"])
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [], "ritlager": {"objekt": [{**objekt_av(fri), "lager_id": "finns-inte"}]}})
    assert not v["giltig"] and "som inte finns" in v["fel"][0]


def test_mark_i_lager_blir_markhojd_och_brytlinje_blir_brytlinje_ny(kv: Kv) -> None:
    u = kv.json("GET", P)[1]["utbredning"]
    cx, cy = (u["x_min"] + u["x_max"]) / 2, (u["y_min"] + u["y_max"]) / 2
    mark = kvadrat(cx - 15, cy - 15, 30)
    ob = {
        "ritlager": {
            "objekt": [
                {"objekt_id": "platan", "lager_id": "mark", "geometri": mark["geometri"], "egenskaper": {"niva_rh2000": 400.0}},
                {"objekt_id": "muren", "lager_id": "mur-och-kant", "geometri": {"type": "LineString", "coordinates": [[cx, cy], [cx + 20, cy]]}, "egenskaper": {"typ": "mur", "hojd_m": 1.0}},
            ]
        }
    }
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [], **ob})
    assert v["giltig"], v
    assert [(a["typ"], a["andring_id"]) for a in v["andringar"]] == [("brytlinje_ny", "muren"), ("markhojd", "platan")]
    assert v["skarvar"] is not None and v["skarvar"]["antal"] >= 1  # skarvkontrollen (K49) ser rollen mark
    assert not any("räknas inte" in t for t in v["varningar"])  # muren räknas sedan R3


def test_spara_hamta_och_gammal_fil(kv: Kv) -> None:
    fri = demohus(18)
    # 1) gammal väg: bara andringar, inget ritlager
    s, gammal = kv.json("POST", f"{P}/scenarier", {"namn": "Gammal", "andringar": [fri]})
    assert s == 200 and "ritlager" not in gammal
    _, h = kv.json("GET", f"{P}/scenarier/gammal")
    assert h["ritlager"]["lager"] == rl.standardlager() and h["ritlager"]["objekt"] == []
    assert h["fria_andringar"] == h["andringar"] and len(h["andringar"]) == 1
    # 2) ny väg: objekt i lager; andringar är hela K9; samma K9-nyckel som den gamla filen
    s, ny = kv.json("POST", f"{P}/scenarier", {"namn": "Nytt", "andringar": [], "ritlager": {"objekt": [objekt_av(fri)]}})
    assert s == 200, ny
    assert ny["andringar"] == gammal["andringar"] and ny["ritlager"]["objekt"][0]["lager_id"] == "hus"
    _, h = kv.json("GET", f"{P}/scenarier/nytt")
    assert h["fria_andringar"] == [] and len(h["andringar"]) == 1
    p_gammal = kv.json("POST", f"{P}/scenarier/gammal/plan", {"analyser": ["dagsljus.sol"]})[1]["fragor"]
    p_ny = kv.json("POST", f"{P}/scenarier/nytt/plan", {"analyser": ["dagsljus.sol"]})[1]["fragor"]
    assert p_gammal[0]["k9_nyckel"] == p_ny[0]["k9_nyckel"]


def test_stangt_lager_skriver_k9_till_stangd_mapp_som_spararen_stanger(kv: Kv) -> None:
    fri = demohus(18)
    lager = [*rl.standardlager(), {**rl.standardlager()[0], "lager_id": "baskarta-hus", "namn": "Baskartans hus", "kalla": "baskarta:stockholm", "dataklass": "intern"}]
    kropp = {"namn": "Stangt", "andringar": [], "ritlager": {"lager": lager, "objekt": [objekt_av(fri, "baskarta-hus")]}}
    assert kv.json("POST", f"{P}/scenarier", kropp)[0] == 200
    plan = kv.json("POST", f"{P}/scenarier/stangt/plan", {"analyser": ["dagsljus.sol"]})[1]
    mapp = kv.rot / "kartverktyg" / "demokvarteret"
    k9 = [f for f in (mapp / "k9_stangda").glob("stangt__*.json")]
    assert k9 and (mapp / "k9_stangda" / "oppenhet.toml").is_file()
    assert not list((mapp / "k9").glob("stangt__*.json"))
    o = Oppenhet(kv.rot, arbetsytor=[kv.rot / "kartverktyg"])
    assert not o.bedom(k9[0]).oppen and "inte öppen" in o.bedom(k9[0]).orsak
    assert plan["fragor"][0]["k9"] == k9[0].name
    # samma förslag utan objekt i det stängda lagret flyttas tillbaka till den öppna mappen
    kropp["ritlager"]["objekt"] = [objekt_av(fri, "hus")]
    kv.json("POST", f"{P}/scenarier/stangt", kropp)
    kv.json("POST", f"{P}/scenarier/stangt/plan", {"analyser": ["dagsljus.sol"]})
    assert list((mapp / "k9").glob("stangt__*.json")) and not list((mapp / "k9_stangda").glob("stangt__*.json"))
    assert o.bedom(next((mapp / "k9").glob("stangt__*.json"))).oppen


def test_ritlager_json_ar_tolkningsbar_i_filen(kv: Kv) -> None:
    fil = kv.rot / "kartverktyg" / "demokvarteret" / "scenarier" / "nytt.json"
    d = json.loads(fil.read_text(encoding="utf-8"))
    assert d["format"] == "floden-kartverktyg/v1" and d["ritlager"]["kontrakt"] == "floden-ritlager/v1"
    rl.kontrollera_dokument(d["ritlager"])
