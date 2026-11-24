# ruff: noqa: E501
"""Skarven i klienten (R5), serversidan: fynden med yta (`omrade`), åtgärdsförslaget (`/skarvatgard`)
och att förslaget stämmer när det tillämpas och kontrolleras på nytt (demokvarteret, litet läge)."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

import pytest

from sankgraf.skarvatgard import tillampa
from tests.webb.conftest import Demo
from tests.webb.test_kartverktyg import Kv, demohus, snedyta

P = "/api/kartverktyg/demokvarteret"
CX, CY = 151350.0, 6580350.0


@pytest.fixture(scope="module")
def kv(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Kv]:
    yield Kv(demo, tmp_path_factory.mktemp("kartverktyg_skarv"))


def kolla(kv: Kv, andringar: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    _, v = kv.json("POST", f"{P}/validera", {"andringar": andringar, **extra})
    assert v["giltig"], v["fel"]
    return v


def test_fynden_har_omrade_som_polygon_runt_botten(kv: Kv) -> None:
    v = kolla(kv, [snedyta(CX, CY, 20, 55, {"niva_rh2000": 13.65})])
    assert v["skarvar"]["antal"] >= 1
    for f in v["skarvar"]["fynd"]:
        om = f["omrade"]
        assert om["type"] in ("Polygon", "MultiPolygon")
        punkter = om["coordinates"][0] if om["type"] == "Polygon" else [p for r in om["coordinates"] for p in r[0]]
        xs, ys = [p[0] for p in punkter], [p[1] for p in punkter]
        assert min(xs) <= f["x_m"] <= max(xs) and min(ys) <= f["y_m"] <= max(ys)


def test_atgard_slant_stammer_nar_den_tillampas_och_kontrolleras_pa_nytt(kv: Kv) -> None:
    """Förslagets `efter` är kontrollens egen siffra: tillämpa `andra` och kör validera, samma antal och volym."""
    a = [snedyta(CX, CY, 50, 70, {"niva_rh2000": 13.65})]
    fore = kolla(kv, a)["skarvar"]
    _, r = kv.json("POST", f"{P}/skarvatgard", {"andringar": a})
    assert r["kontrakt"] == "floden-skarvatgard/v1"
    assert r["fore"] == {"antal": fore["antal"], "tillkommande_volym_m3": fore["tillkommande_volym_m3"]}
    ag = r["atgard"]
    assert ag["slag"] == "slant" and ag["mal_id"] == "mark1"
    assert ag["efter"]["antal"] < fore["antal"] and ag["efter"]["tillkommande_volym_m3"] < fore["tillkommande_volym_m3"]
    efter = kolla(kv, tillampa(a, {"andra": ag["andra"], "nya": ag["nya"]}))["skarvar"]
    assert (efter["antal"], efter["tillkommande_volym_m3"]) == (ag["efter"]["antal"], ag["efter"]["tillkommande_volym_m3"])


def test_atgard_utlopp_ses_av_kontrollen_och_stammer(kv: Kv) -> None:
    """Upphöjd yta (0,55 m) i sänkan: förslaget innehåller ett utlopp (K9 `oppning_ny`) som validera räknar med."""
    a = [snedyta(CX, CY, 30, 0, {"niva_rh2000": 14.2})]
    fore = kolla(kv, a)["skarvar"]
    _, r = kv.json("POST", f"{P}/skarvatgard", {"andringar": a})
    ag = r["atgard"]
    assert ag is not None and any(n["typ"] == "oppning_ny" for n in ag["nya"])
    assert ag["efter"]["tillkommande_volym_m3"] < fore["tillkommande_volym_m3"]
    n = next(x for x in ag["nya"] if x["typ"] == "oppning_ny")
    assert n["egenskaper"]["typ"] == "passage" and n["egenskaper"]["bredd_m"] > 0
    efter = kolla(kv, tillampa(a, {"andra": ag["andra"], "nya": ag["nya"]}))["skarvar"]
    assert (efter["antal"] if efter else 0, efter["tillkommande_volym_m3"] if efter else 0.0) == (ag["efter"]["antal"], ag["efter"]["tillkommande_volym_m3"])
    assert ag["loser_allt"] is (ag["efter"]["antal"] == 0)


def test_atgard_for_ritat_ytobjekt_andrar_ritlagrets_objekt_id(kv: Kv) -> None:
    mark = snedyta(CX, CY, 50, 70, {"niva_rh2000": 13.65}, aid="platan")
    ob = {"ritlager": {"objekt": [{"objekt_id": "platan", "lager_id": "mark", "geometri": mark["geometri"], "egenskaper": mark["egenskaper"]}]}}
    _, r = kv.json("POST", f"{P}/skarvatgard", {"andringar": [], **ob})
    assert r["atgard"]["mal_id"] == "platan"
    assert r["atgard"]["andra"][0]["andring_id"] == "platan"
    assert r["atgard"]["andra"][0]["egenskaper"]["slant"]["forhallande"] in (3.0, 5.0, 10.0)


def test_utan_fynd_eller_utan_markyta_ger_inget_forslag_med_skal(kv: Kv) -> None:
    _, r = kv.json("POST", f"{P}/skarvatgard", {"andringar": [demohus()]})
    assert r["atgard"] is None and r["skal"] and r["fore"]["antal"] == 0
    _, r = kv.json("POST", f"{P}/skarvatgard", {"andringar": [snedyta(CX, CY, 20, 0, {"delta_m": -0.01, "slant": {"forhallande": 3.0}})]})
    assert r["atgard"] is None  # inget fynd: ingenting att jämna ut


def test_fel_i_forslaget_ger_tydligt_fel_och_kroppen_andras_inte(kv: Kv) -> None:
    a = snedyta(CX, CY, 20, 0, {})
    orig = copy.deepcopy(a)
    status, r = kv.json("POST", f"{P}/skarvatgard", {"andringar": [a]})
    assert status == 422 and "måste rättas" in r["fel"]
    assert a == orig
    status, _ = kv.json("POST", f"{P}/skarvatgard", {})
    assert status == 400


def test_validera_raknar_med_oppningar_i_skarvkontrollen(kv: Kv) -> None:
    """Ett utlopp som användaren ritat minskar fynden i den vanliga kontrollen (inte bara i förslaget)."""
    a = [snedyta(CX, CY, 30, 0, {"niva_rh2000": 14.2})]
    fore = kolla(kv, a)["skarvar"]
    _, r = kv.json("POST", f"{P}/skarvatgard", {"andringar": a})
    oppning = [x for x in r["atgard"]["nya"] if x["typ"] == "oppning_ny"]
    assert oppning
    efter = kolla(kv, [*a, *oppning])["skarvar"]
    assert efter is None or efter["tillkommande_volym_m3"] < fore["tillkommande_volym_m3"]
