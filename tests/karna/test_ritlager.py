# ruff: noqa: E501
"""Ritlager (K50): lagermodell, mallar, översättning roll -> K9/K1 (testad per roll) och dataklass."""

from __future__ import annotations

from typing import Any

import pytest

from karna import ritlager as rl
from karna.ritat import RitatFel
from karna.scenario import Andring


def yta(x: float = 0.0, y: float = 0.0, s: float = 10.0) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]]}


LINJE = {"type": "LineString", "coordinates": [[0, 0], [10, 0], [20, 5]]}


def dok(lager_id: str, geometri: dict[str, Any], egenskaper: dict[str, Any], oid: str = "o1") -> dict[str, Any]:
    return {"kontrakt": rl.KONTRAKT, "objekt": [{"objekt_id": oid, "lager_id": lager_id, "geometri": geometri, "egenskaper": egenskaper}]}


def test_standardmallen_har_sju_lager_en_per_roll() -> None:
    lager = rl.standardlager()
    assert [x["roll"] for x in lager] == list(rl.ROLLER)
    assert [x["lager_id"] for x in lager] == ["hus", "mark", "gata", "gronyta", "vatten", "mur-och-kant", "passage"]
    for x in lager:
        assert x["geometri"] in rl.ROLL_GEOMETRIER[x["roll"]] and x["dataklass"] == "oppen" and x["kalla"] == "ritat"
        assert x["synlig"] and not x["last"]
    # kopior: ändring av en kopia ändrar inte mallen
    lager[0]["namn"] = "x"
    assert rl.standardlager()[0]["namn"] == "Hus"


def test_forslag_utan_lager_far_forvalda_lager() -> None:
    assert rl.kontrollera_dokument(None)["lager"] == rl.standardlager()
    d = rl.kontrollera_dokument({"objekt": []})
    assert [x["lager_id"] for x in d["lager"]][0] == "hus"


@pytest.mark.parametrize(
    ("lager_id", "geom", "egen", "typer"),
    [
        ("hus", yta(), {"hojd_m": 12}, [("byggnad_ny", {"hojd_m": 12.0})]),
        ("hus", yta(), {"tak_rh2000": 40.5, "takform": "sadel", "vaningar": 4}, [("byggnad_ny", {"tak_rh2000": 40.5, "takform": "sadel", "vaningar": 4})]),
        ("mark", yta(), {"niva_rh2000": 11.5}, [("markhojd", {"niva_rh2000": 11.5})]),
        ("mark", yta(), {"niva_rh2000": 11.5, "osakerhet_m": 0.1, "markslag": 4}, [("markhojd", {"niva_rh2000": 11.5, "osakerhet_m": 0.1, "markslag": 4})]),
        ("gata", yta(), {}, [("markslag", {"kod": 1})]),
        ("gata", yta(), {"niva_rh2000": 9.0}, [("markhojd", {"niva_rh2000": 9.0, "markslag": 1})]),
        ("gronyta", yta(), {}, [("markslag", {"kod": 4})]),  # lagrets förval: gräs
        ("gronyta", yta(), {"hojd_m": 8, "markslag": 5}, [("vegetation", {"hojd_m": 8.0}), ("markslag", {"kod": 5})]),
        ("vatten", yta(), {}, [("markslag", {"kod": 8})]),
        ("vatten", yta(), {"volym_m3": 120, "botten_rh2000": 7.5}, [("magasin", {"volym_m3": 120.0, "botten_rh2000": 7.5})]),
    ],
)
def test_roll_ger_k9_andring(lager_id: str, geom: dict[str, Any], egen: dict[str, Any], typer: list[tuple[str, dict[str, Any]]]) -> None:
    d = dok(lager_id, geom, egen)
    # gronyta har förvalet markslag 4 (gräs) i standardmallen; objektets eget värde går före
    res = rl.oversatt(d)
    assert [(a["typ"], a["egenskaper"]) for a in res.k9] == typer
    for a in res.k9:  # kärnan godtar ändringen (typ, id, geometri)
        Andring.fran_dict(a)
    assert res.k9[0]["andring_id"] == "o1"


def test_vegetation_med_hojd_och_markslag_ger_tva_andringar_med_stabila_id() -> None:
    res = rl.oversatt(dok("gronyta", yta(), {"hojd_m": 8, "markslag": 5}))
    assert [a["andring_id"] for a in res.k9] == sorted(["o1", "o1" + rl.SEKUNDAR_SUFFIX])
    assert res.objekt_till_andringar == {"o1": ["o1", "o1" + rl.SEKUNDAR_SUFFIX]}


def test_brytlinje_blir_k9_brytlinje_ny_som_tillampas() -> None:
    res = rl.oversatt(dok("mur-och-kant", LINJE, {"typ": "stödmur", "hojd_m": 1.2}))
    (b,) = res.k9
    assert b["typ"] == "brytlinje_ny" and b["egenskaper"] == {"typ": "stodmur", "hojd_m": 1.2}
    assert b["geometri"]["type"] == "LineString"
    Andring.fran_dict(b)


def test_brytlinje_med_bort_blir_brytlinje_bort() -> None:
    res = rl.oversatt(dok("mur-och-kant", LINJE, {"bort": True, "typ": "kantsten", "tolerans_m": 1.0}))
    (b,) = res.k9
    assert b["typ"] == "brytlinje_bort" and b["egenskaper"] == {"typ": "kantsten", "tolerans_m": 1.0}
    res = rl.oversatt(dok("mur-och-kant", LINJE, {"bort": True}))
    assert res.k9[0]["egenskaper"] == {}


def test_oppning_blir_k9_oppning_ny() -> None:
    res = rl.oversatt(dok("passage", LINJE, {"typ": "trumma", "bredd_m": 0.8, "botten_rh2000": 5.2}))
    (o,) = res.k9
    assert o["typ"] == "oppning_ny" and o["egenskaper"] == {"typ": "trumma", "bredd_m": 0.8, "botten_rh2000": 5.2}
    Andring.fran_dict(o)


def test_mark_med_lutning_delta_och_slant() -> None:
    lut = {"punkt": [10, 10, 5.0], "fall": 0.02, "riktning_grader": 90}
    res = rl.oversatt(dok("mark", yta(), {"lutning": lut, "slant": {"forhallande": 3}}))
    (m,) = res.k9
    assert m["typ"] == "markhojd" and m["egenskaper"]["lutning"]["fall"] == 0.02
    assert m["egenskaper"]["slant"] == {"forhallande": 3.0}
    res = rl.oversatt(dok("mark", yta(), {"delta_m": -0.4, "slant": {"bredd_m": 2}}))
    assert res.k9[0]["egenskaper"] == {"delta_m": -0.4, "slant": {"bredd_m": 2.0}}


def test_oversattningen_ar_deterministisk_och_oberoende_av_ordning_och_stil() -> None:
    a = {"kontrakt": rl.KONTRAKT, "objekt": [
        {"objekt_id": "b", "lager_id": "hus", "geometri": yta(30), "egenskaper": {"hojd_m": 9}},
        {"objekt_id": "a", "lager_id": "mark", "geometri": yta(0), "egenskaper": {"niva_rh2000": 3}},
    ]}
    b = {"kontrakt": rl.KONTRAKT, "lager": [{**x, "stil": {"fyllning": "#ff0000"}, "synlig": False, "last": True} for x in rl.standardlager()],
         "objekt": list(reversed(a["objekt"]))}
    assert rl.oversatt(a).k9 == rl.oversatt(b).k9


@pytest.mark.parametrize(
    ("lager_id", "geom", "egen", "text"),
    [
        ("hus", yta(), {}, "saknar höjd"),
        ("hus", yta(), {"hojd_m": 12, "niva_rh2000": 3}, "egenskaper som rollen inte tar"),
        ("hus", yta(), {"hojd_m": "tolv"}, "ska vara ett tal"),
        ("hus", LINJE, {"hojd_m": 12}, "ska vara polygon"),
        ("mark", yta(), {}, "saknar nivån"),
        ("mark", yta(), {"niva_rh2000": 3, "delta_m": 1}, "exakt en av"),
        ("mark", yta(), {"delta_m": 1, "slant": {"forhallande": 3, "bredd_m": 2}}, "exakt en av forhallande"),
        ("mark", yta(), {"lutning": {"a": [0, 0, 1]}}, "lutning"),
        ("gata", yta(), {"osakerhet_m": 0.2}, "ange också nivån"),
        ("gronyta", yta(), {"hojd_m": -1}, "får inte vara under"),
        ("gronyta", yta(), {"markslag": 1}, "markslag ska vara"),
        ("vatten", yta(), {"botten_rh2000": 3}, "ange också volymen"),
        ("mur-och-kant", LINJE, {}, "saknar typ"),
        ("mur-och-kant", LINJE, {"typ": "mur"}, "saknar höjden"),
        ("mur-och-kant", LINJE, {"typ": "mur", "hojd_m": 0}, "måste vara större än 0"),
        ("mur-och-kant", LINJE, {"bort": True, "tolerans_m": 9}, "högst 5 m"),
        ("mur-och-kant", LINJE, {"typ": "vägg"}, "okänd brytlinjetyp"),
        ("passage", LINJE, {"typ": "trumma", "bredd_m": 1}, "saknar bottennivån"),
        ("passage", LINJE, {"typ": "tunnel", "bredd_m": 1, "botten_rh2000": 1}, "okänd öppningstyp"),
        ("hus", {"type": "Polygon", "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]}, {"hojd_m": 5}, "ingen giltig form"),
    ],
)
def test_fel_per_roll_ar_tydliga(lager_id: str, geom: dict[str, Any], egen: dict[str, Any], text: str) -> None:
    with pytest.raises(rl.RitlagerFel, match=text):
        rl.oversatt(dok(lager_id, geom, egen))


def test_lagerfel() -> None:
    bas = rl.standardlager()[0]
    for andra, text in [
        ({"roll": "tak"}, "okänd roll"),
        ({"roll": "brytlinje"}, "ritas som linje"),
        ({"lager_id": "Hus 1"}, "ogiltigt"),
        ({"stil": {"fyllning": "röd"}}, "färg"),
        ({"stil": {"glitter": 1}}, "okänd stilegenskap"),
        ({"opacitet": 2}, "mellan 0 och 1"),
        ({"synlig": "ja"}, "sant eller falskt"),
        ({"dataklass": "personuppgifter"}, "personuppgifter"),
        ({"dataklass": "hemlig"}, "okänd dataklass"),
        ({"kalla": "internet"}, "okänd källa"),
        ({"egenskaper_mall": {"niva_rh2000": "tal"}}, "hör inte till rollen"),
    ]:
        with pytest.raises(rl.RitlagerFel, match=text):
            rl.kontrollera_lager({**bas, **andra})
    with pytest.raises(rl.RitlagerFel, match="Två lager"):
        rl.kontrollera_dokument({"lager": [bas, bas]})
    with pytest.raises(rl.RitlagerFel, match="som inte finns"):
        rl.kontrollera_dokument(dok("saknas", yta(), {}))
    with pytest.raises(rl.RitlagerFel, match="Två objekt"):
        d = dok("hus", yta(), {"hojd_m": 5})
        d["objekt"].append(dict(d["objekt"][0]))
        rl.kontrollera_dokument(d)
    assert issubclass(rl.RitlagerFel, RitatFel)  # fångas av kartverktygets kontroll


def test_sla_ihop_nekar_dubbla_id_och_fria_andringar_skiljs_ut() -> None:
    d = dok("hus", yta(), {"hojd_m": 9}, oid="h1")
    fri = {"typ": "byggnad_bort", "andring_id": "bort_b_1", "geometri": None, "egenskaper": {"byggnads_id": "b_1"}}
    lista, _ = rl.sla_ihop([fri], d)
    assert {a["andring_id"] for a in lista} == {"bort_b_1", "h1"}
    assert rl.fria_andringar(lista, d) == [fri]
    with pytest.raises(rl.RitlagerFel, match="både som fri"):
        rl.sla_ihop([{**fri, "andring_id": "h1"}], d)
    assert rl.fria_andringar(lista, None) == lista  # ett förslag utan lager: allt är fritt


def test_dataklass_stangd_kalla_ger_stangt_lager() -> None:
    lager = rl.standardlager()[0]
    assert rl.effektiv_dataklass(lager) == "oppen"
    assert rl.effektiv_dataklass({**lager, "kalla": "baskarta:stockholm", "dataklass": "oppen"}) == "intern"
    assert rl.effektiv_dataklass({**lager, "kalla": "ledningar", "dataklass": "oppen"}) == "skyddad"
    assert rl.effektiv_dataklass({**lager, "kalla": "fil:a.geojson", "dataklass": "intern"}) == "intern"
    # ett lager ritat i ett stängt projekt är minst internt
    assert rl.effektiv_dataklass(lager, projekt_oppen=False) == "intern"
    assert rl.till_mcp_klass("skyddad") == "skyddat"
    # en filkälla utan deklarerad klass är inte öppen (osäkert = stängt)
    assert rl.kontrollera_lager({**lager, "kalla": "fil:x.geojson", "dataklass": None} | {"dataklass": "intern"})["dataklass"] == "intern"
    ny = dict(lager)
    del ny["dataklass"]
    ny["kalla"] = "fil:x.geojson"
    assert rl.kontrollera_lager(ny)["dataklass"] == "intern"


def test_for_mcp_utelamnar_stangda_lagers_innehall() -> None:
    lager = [*rl.standardlager(), {**rl.standardlager()[0], "lager_id": "baskarta-hus", "namn": "Baskartans hus", "kalla": "baskarta:stockholm", "dataklass": "intern"}]
    d = rl.kontrollera_dokument({
        "lager": lager,
        "objekt": [
            {"objekt_id": "oppet", "lager_id": "hus", "geometri": yta(), "egenskaper": {"hojd_m": 5}},
            {"objekt_id": "stangt", "lager_id": "baskarta-hus", "geometri": yta(50), "egenskaper": {"hojd_m": 7}},
        ],
    })
    assert rl.har_stangt_innehall(d) and not rl.har_stangt_innehall(dok("hus", yta(), {"hojd_m": 5}) | {"lager": rl.standardlager()})
    ut = rl.for_mcp(d)
    assert ut["stangda_lager"] == ["baskarta-hus"]
    assert [o["objekt_id"] for o in ut["objekt"]] == ["oppet"]
    (s,) = [x for x in ut["lager"] if x["lager_id"] == "baskarta-hus"]
    assert s == {"lager_id": "baskarta-hus", "namn": "Baskartans hus", "roll": "byggnad", "dataklass": "intern", "stangt": True}
    assert "baskarta" not in str([x for x in ut["lager"] if x["lager_id"] != "baskarta-hus"])
    assert "stangt" not in str(ut["objekt"]) and "50" not in str(ut["objekt"]).replace("5", "")  # inget från det stängda lagret
