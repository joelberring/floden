# ruff: noqa: E501
"""Åtgärdsförslaget för skarven (R5): deterministiskt, verifierat mot kontrollen, med känt svar.

Samma syntetiska dalgång som `test_skarvkontroll.py` (2 % fall söderut, 80 x 80 m, 0,5 m celler).
`kor` räknar om skarvkontrollen på K9-ändringarna via K3-tillämpningen, som servern gör på K1-rutor.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pytest

from lapp.kontrakt import HojdYta, LappOppning, ProjektLapp
from lapp.tillampa import _polygon_mask, tillampa_lapp
from sankgraf import skarvatgard as sa
from sankgraf.skarvkontroll import kontrollera_skarvar
from tests.sankgraf.test_skarvkontroll import B, H, _bas, _geo


def _ring(poly: tuple[tuple[float, float], ...]) -> list[list[float]]:
    r = [[x, y] for x, y in poly]
    return [*r, r[0]]


def mark(aid: str, poly: tuple[tuple[float, float], ...], **egenskaper: Any) -> dict[str, Any]:
    return {
        "typ": "markhojd",
        "andring_id": aid,
        "geometri": {"type": "Polygon", "coordinates": [_ring(poly)]},
        "egenskaper": egenskaper,
    }


def kor(andringar: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Skarvkontrollen för K9-ändringar på den syntetiska dalgången (markytor och öppningar)."""
    z0, _, _ = _bas()
    z0 = z0.astype(np.float32)
    osak = np.full((H, B), 0.05, np.float32)
    slag = np.full((H, B), 4, np.uint8)
    ytor, oppningar, polys = [], [], []
    for a in andringar:
        e = a["egenskaper"]
        if a["typ"] == "markhojd":
            poly = tuple((x, y) for x, y in a["geometri"]["coordinates"][0][:-1])
            polys.append(poly)
            ytor.append(
                HojdYta(
                    yta_id=a["andring_id"],
                    polygon=poly,
                    hojd_rh2000=e.get("niva_rh2000"),
                    delta_m=e.get("delta_m"),
                    lutning=e.get("lutning"),
                    slant_forhallande=(e.get("slant") or {}).get("forhallande"),
                )
            )
        elif a["typ"] == "oppning_ny":
            oppningar.append(
                LappOppning(
                    oppning_id=a["andring_id"],
                    koordinater=tuple((x, y) for x, y in a["geometri"]["coordinates"]),
                    typ=e["typ"],
                    bredd_m=e["bredd_m"],
                    botten_rh2000=e["botten_rh2000"],
                )
            )
    lapp = ProjektLapp(
        lapp_id="t", bas_recept=("x",), hojdytor=tuple(ytor), oppningar_nya=tuple(oppningar)
    )
    res = tillampa_lapp(z0, osak, slag, _geo(), lapp, bas_recept_sha256="x", sektioner={})
    mask = (res.mark_z != z0) | res.andrad_mask
    for p in polys:
        mask |= np.asarray(_polygon_mask(p, _geo()), dtype=np.bool_)
    r = kontrollera_skarvar(
        z0, res.mark_z, slag, osak, _geo(), mask, oppningar_efter=res.oppningar
    )
    return r.som_dict() if r.fynd else None


DAMM = ((4.0, 30.0), (76.0, 30.0), (76.0, 50.0), (4.0, 50.0))  # tvärs dalgången, 20 m i fallet


def test_damm_tvars_dalgangen_loses_med_utlopp_inte_med_slant() -> None:
    """Känt svar: en yta 0,6 m över dalgångens botten dämmer. Slänten räcker inte (prövas och
    förkastas); ett utlopp längs dalgången (passage sänkt till sänkans botten) tar bort alla fynd."""
    a = [mark("damm", DAMM, delta_m=0.6)]
    fore = kor(a)
    assert fore is not None and fore["antal"] == 1
    r = sa.foresla(a, fore, kor)
    ag = r["atgard"]
    assert r["kontrakt"] == "floden-skarvatgard/v1"
    assert ag is not None and ag["slag"] == "utlopp" and ag["loser_allt"] is True
    assert ag["efter"] == {"antal": 0, "tillkommande_volym_m3": 0.0}
    assert r["fore"]["antal"] == 1 and r["fore"]["tillkommande_volym_m3"] > 5.0
    (nytt,) = ag["nya"]
    assert nytt["typ"] == "oppning_ny" and nytt["egenskaper"]["typ"] == "passage"
    assert nytt["egenskaper"]["botten_rh2000"] == pytest.approx(fore["fynd"][0]["botten_rh2000"], abs=0.01)
    (a0, a1) = nytt["geometri"]["coordinates"]
    assert (a0[0], a0[1]) == (fore["fynd"][0]["x_m"], fore["fynd"][0]["y_m"])
    assert a1[1] < 30.0  # lämnar ytan vid dess södra kant (y = 30) och fortsätter 2 m ut
    # Förslaget är alltså verifierat: samma ändringar + utloppet ger inga fynd.
    assert kor(sa.tillampa(a, {"andra": ag["andra"], "nya": ag["nya"]})) is None


def test_slant_provas_forst_och_valjs_nar_den_raeckar() -> None:
    """Sänkt, snedställd yta med hård cellkant ger fynd; med slänt 1:3 inga: första förslaget."""
    import math

    ang = math.radians(45)
    c, s = math.cos(ang), math.sin(ang)
    poly = tuple(
        (40 + c * x - s * y, 40 + s * x + c * y)
        for x, y in ((-20, -7), (20, -7), (20, 7), (-20, 7))
    )
    z0, _, _ = _bas()
    niva = float(z0[80, 80]) - 0.3
    a = [mark("snett", poly, niva_rh2000=niva)]
    fore = kor(a)
    assert fore is not None and fore["antal"] >= 1  # hård cellkant: ny sänka vid kanten
    r = sa.foresla(a, fore, kor)
    ag = r["atgard"]
    assert ag is not None and ag["slag"] == "slant" and ag["loser_allt"] is True
    assert ag["andra"] == [{"andring_id": "snett", "egenskaper": {"slant": {"forhallande": 3.0}}}]
    assert ag["nya"] == [] and ag["efter"]["antal"] == 0
    assert "1:3" in ag["text"]


def test_nasta_slant_over_nuvarande_och_flackaste_ger_besked() -> None:
    mark_a = mark("m", DAMM, delta_m=0.6, slant={"forhallande": 3.0, "markslag": 4})
    fynd = {"x_m": 40.0, "y_m": 52.0}
    assert [k["andra"][0]["egenskaper"]["slant"]["forhallande"] for k in sa._slant_kandidater(mark_a)] == [5.0, 10.0]
    assert sa._slant_kandidater(mark_a)[0]["andra"][0]["egenskaper"]["slant"]["markslag"] == 4
    assert sa.valj_markyta([mark_a], fynd) == mark_a
    flack = mark("m", DAMM, delta_m=0.6, slant={"forhallande": 10.0})
    assert sa._slant_kandidater(flack) == []


def test_valj_markyta_narmast_och_vid_lika_lagst_id() -> None:
    a = mark("a", ((0, 0), (10, 0), (10, 10), (0, 10)), niva_rh2000=1.0)
    b = mark("b", ((30, 0), (40, 0), (40, 10), (30, 10)), niva_rh2000=1.0)
    assert sa.valj_markyta([b, a], {"x_m": 12.0, "y_m": 5.0})["andring_id"] == "a"  # type: ignore[index]
    assert sa.valj_markyta([b, a], {"x_m": 28.0, "y_m": 5.0})["andring_id"] == "b"  # type: ignore[index]
    assert sa.valj_markyta([b, a], {"x_m": 20.0, "y_m": 5.0})["andring_id"] == "a"  # type: ignore[index]
    assert sa.valj_markyta([{"typ": "byggnad_ny", "andring_id": "h", "egenskaper": {}}], {"x_m": 0.0, "y_m": 0.0}) is None


def test_deterministiskt_och_andrar_inte_indata() -> None:
    a = [mark("damm", DAMM, delta_m=0.6)]
    orig = copy.deepcopy(a)
    fore = kor(a)
    r1 = sa.foresla(a, fore, kor)
    r2 = sa.foresla(a, fore, kor)
    assert r1 == r2
    assert a == orig


def test_inga_fynd_och_ingen_markyta_ger_skal_inte_atgard() -> None:
    assert sa.foresla([], None, kor)["atgard"] is None
    assert "inga fynd" in sa.foresla([], None, kor)["skal"]
    fynd = {"antal": 1, "tillkommande_volym_m3": 3.0, "fynd": [{"x_m": 1.0, "y_m": 1.0, "botten_rh2000": 1.0, "overfall_x_m": 2.0, "overfall_y_m": 2.0}]}
    r = sa.foresla([{"typ": "byggnad_ny", "andring_id": "h", "egenskaper": {}}], fynd, kor)
    assert r["atgard"] is None and "ingen markyta" in r["skal"]


def test_ofullstandig_atgard_redovisas_som_det_den_ar() -> None:
    """Utan förbättring inget förslag; med bara förbättring `loser_allt` False."""
    fynd = {"antal": 2, "tillkommande_volym_m3": 10.0, "fynd": [{"x_m": 40.0, "y_m": 52.0, "botten_rh2000": 19.0, "overfall_x_m": 41.0, "overfall_y_m": 52.0}]}
    a = [mark("damm", DAMM, delta_m=0.6)]
    sa_samma = lambda _l: {"antal": 2, "tillkommande_volym_m3": 10.0, "fynd": fynd["fynd"]}  # noqa: E731
    r = sa.foresla(a, fynd, sa_samma)
    assert r["atgard"] is None and "minskar" in r["skal"]
    battre = lambda _l: {"antal": 1, "tillkommande_volym_m3": 4.0, "fynd": fynd["fynd"][:1]}  # noqa: E731
    r = sa.foresla(a, fynd, battre)
    assert r["atgard"]["loser_allt"] is False and r["atgard"]["efter"]["antal"] == 1
    assert len(r["atgard"]["steg"]) == 1  # andra rundan förbättrar inget mer och avbryts
    assert r["skal"]


def test_fynd_far_omrade_som_polygon() -> None:
    fore = kor([mark("damm", DAMM, delta_m=0.6)])
    assert fore is not None
    om = fore["fynd"][0]["omrade"]
    assert om["type"] == "Polygon" and len(om["coordinates"][0]) >= 4
    xs = [p[0] for p in om["coordinates"][0]]
    ys = [p[1] for p in om["coordinates"][0]]
    f = fore["fynd"][0]
    assert min(xs) <= f["x_m"] <= max(xs) and min(ys) <= f["y_m"] <= max(ys)
