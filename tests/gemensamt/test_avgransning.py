# ruff: noqa: E501
"""Projektets avgränsning som riktig polygon: tolkning, validering, celler och byggnader inne i den."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely.geometry import Polygon

from gemensamt import avgransning as av
from kommuner.projekt import (
    projekt_polygon,
    tolka_avgransning,
    tolka_avgransning_objekt,
    tolka_avgransning_objekt_form,
)
from kommuner.skapa import SkapaFel
from webb.fragor import FragaTjanst, Korning

L_FORM = [[0, 0], [100, 0], [100, 40], [40, 40], [40, 100], [0, 100], [0, 0]]


def gj(ring: list[list[float]]) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [ring]}


def test_giltig_polygon_och_rektangel_skiljs_at() -> None:
    p = av.tolka_polygon(gj(L_FORM))
    assert p is not None and p.area == 100 * 40 + 40 * 60 and not av.ar_rektangel(p)
    assert av.omslutande(p) == (0.0, 0.0, 100.0, 100.0)
    rek = av.tolka_polygon(gj([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]))
    assert rek is not None and av.ar_rektangel(rek)
    # Rena rektanglar (lista eller text) har ingen polygon utöver rektangeln.
    assert av.tolka_polygon([0, 0, 10, 10]) is None and av.tolka_polygon("0,0,10,10") is None
    # Feature och WKT går också.
    assert av.tolka_polygon({"type": "Feature", "properties": {}, "geometry": gj(L_FORM)}) is not None
    assert av.tolka_polygon("POLYGON((0 0, 50 0, 50 50, 0 0))") is not None


def test_sjalvkorsning_hal_och_for_fa_horn_ger_tydligt_svenskt_fel() -> None:
    fjaril = gj([[0, 0], [100, 100], [100, 0], [0, 100], [0, 0]])
    with pytest.raises(av.AvgransningFel, match=r"Polygonen korsar sig själv nära E 50, N 50"):
        av.tolka_polygon(fjaril)
    hal = {"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]], [[10, 10], [20, 10], [20, 20], [10, 10]]]}
    with pytest.raises(av.AvgransningFel, match="hål"):
        av.tolka_polygon(hal)
    with pytest.raises(av.AvgransningFel, match="minst 3 olika hörn"):
        av.tolka_polygon(gj([[0, 0], [1, 1], [1, 1], [0, 0]]))
    with pytest.raises(av.AvgransningFel, match="ändliga tal"):
        av.polygon_fran_punkter([(0, 0), (float("nan"), 1), (3, 3)])
    with pytest.raises(av.AvgransningFel, match="ingen yta|ogiltig"):
        av.tolka_polygon(gj([[0, 0], [5, 5], [10, 10], [0, 0]]))  # alla hörn på en linje
    with pytest.raises(av.AvgransningFel, match="saknar hörn"):
        av.tolka_polygon({"type": "Polygon", "coordinates": "x"})


def test_dubbla_horn_i_foljd_tas_bort() -> None:
    p = av.polygon_fran_punkter([(0, 0), (10, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    assert len(p.exterior.coords) == 5


def test_celler_inne_raknar_mittpunkter() -> None:
    p = Polygon([(0, 0), (4, 0), (4, 2), (0, 2)])
    m = av.celler_inne(p, x_min=-2.0, y_max=4.0, pixel_m=1.0, rader=6, kolumner=8)
    # Rad 0 överst (y 3,5), rad 2 har mittpunkt y=1,5 (inne), kolumn 2 har x=0,5.
    assert m.shape == (6, 8) and m.sum() == 8 and m[2, 2] and m[3, 5] and not m[0, 2] and not m[2, 1]


def test_byggnad_inne_gar_pa_inre_punkt() -> None:
    p = Polygon([(0, 0), (100, 0), (100, 40), (40, 40), (40, 100), (0, 100)])
    inne = [[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]
    ute = [[60, 60], [80, 60], [80, 80], [60, 80], [60, 60]]  # i L:ets "hack"
    assert av.byggnad_inne(p, inne) and not av.byggnad_inne(p, ute)
    # Ett hus som bara sticker in över kanten räknas efter var dess inre punkt ligger.
    kant = [[90, 30], [110, 30], [110, 36], [90, 36], [90, 30]]
    assert av.byggnad_inne(p, kant)


def test_projekt_utan_eller_med_trasig_polygon_ar_rektangel() -> None:
    assert projekt_polygon({"avgransning_3011": [0, 0, 1, 1]}) is None
    assert projekt_polygon({"avgransning_polygon_3011": "skräp"}) is None
    assert projekt_polygon({"avgransning_polygon_3011": gj([[0, 0], [100, 100], [100, 0], [0, 100], [0, 0]])}) is None
    assert projekt_polygon({"avgransning_polygon_3011": gj(L_FORM)}) is not None


def test_tolkning_ger_rektangel_och_polygon() -> None:
    r, poly = tolka_avgransning_objekt_form(gj(L_FORM))
    assert r == (0.0, 0.0, 100.0, 100.0) and poly is not None and poly.area == 6400
    # En polygon som är sin egen rektangel sparas inte som polygon: projektet beter sig som förut.
    r2, poly2 = tolka_avgransning_objekt_form(gj([[0, 0], [50, 0], [50, 30], [0, 30], [0, 0]]))
    assert r2 == (0.0, 0.0, 50.0, 30.0) and poly2 is None
    assert tolka_avgransning_objekt_form([1, 2, 30, 40]) == ((1.0, 2.0, 30.0, 40.0), None)
    # Äldre funktionerna ger fortfarande bara rektangeln.
    assert tolka_avgransning_objekt(gj(L_FORM)) == (0.0, 0.0, 100.0, 100.0)
    assert tolka_avgransning(json.dumps(gj(L_FORM))) == (0.0, 0.0, 100.0, 100.0)
    with pytest.raises(SkapaFel, match="korsar sig själv"):
        tolka_avgransning_objekt_form(gj([[0, 0], [100, 100], [100, 0], [0, 100], [0, 0]]))
    with pytest.raises(SkapaFel, match="korsar sig själv"):
        tolka_avgransning("POLYGON((0 0, 100 100, 100 0, 0 100, 0 0))")


# ---------------------------------------------------------------------- resultatlagret


def _tjanst(tmp_path: Path, med_polygon: bool, register_id: str | None = "reg-1") -> tuple[FragaTjanst, Korning]:
    projektrot = tmp_path / "projekt"
    (projektrot / "p1").mkdir(parents=True)
    pj: dict[str, Any] = {"kontrakt": "floden-projekt/v1", "register_id": "reg-1", "avgransning_3011": [0, 0, 100, 100]}
    if med_polygon:
        pj["avgransning_polygon_3011"] = gj(L_FORM)
    (projektrot / "p1" / "projekt.json").write_text(json.dumps(pj), encoding="utf-8")
    t = FragaTjanst(
        rot=tmp_path, fragemappar=[], cachemapp=tmp_path / "cache", korningsmapp=tmp_path / "k", projektrot=projektrot
    )
    t._inlast = True  # noqa: SLF001
    mapp = tmp_path / "k" / "kor-1"
    (mapp / "webb").mkdir(parents=True)
    k = Korning(korning_id="kor-1", ref="r", fraga_id="f", fraga_fil="f.toml", mapp=mapp, status="klar", projekt_id=register_id)
    t._korningar["kor-1"] = k  # noqa: SLF001
    # Skillnadslager 10 x 10 celler à 10 m över 0..100: +2 överallt (rad 0 = norr).
    np.save(mapp / "webb" / "skillnad.sol.npy", np.full((10, 10), 2.0, dtype=np.float32))
    return t, k


LAGER = {"id": "skillnad.sol", "pixelstorlek_m": 10.0, "utbredning": {"x_min": 0.0, "y_min": 0.0, "x_max": 100.0, "y_max": 100.0}}


def test_markytorna_raknas_bara_inne_i_polygonen(tmp_path: Path) -> None:
    t, _ = _tjanst(tmp_path, med_polygon=True)
    # L-formen täcker 6400 m² av 10000: 64 celler à 100 m².
    s = t.marksiffror("kor-1", LAGER, 1.0)
    assert s is not None and s["yta_okning_m2"] == 6400.0 and s["max_okning"] == 2.0 and s["yta_minskning_m2"] == 0.0
    assert t.markyta_over("kor-1", LAGER, 1.0, "okning") == 6400.0
    pa = t.projekt_avgransning("kor-1")
    assert pa is not None and pa["yta_m2"] == 6400.0 and pa["polygon_3011"]["type"] == "Polygon"


def test_projekt_utan_polygon_eller_utan_projekt_raknar_hela_rutan(tmp_path: Path) -> None:
    t, _ = _tjanst(tmp_path, med_polygon=False)
    s = t.marksiffror("kor-1", LAGER, 1.0)
    assert s is not None and s["yta_okning_m2"] == 10000.0 and t.projekt_avgransning("kor-1") is None
    # Körning utan projekt (demokvarteret, testsviten): oförändrat.
    t2, _ = _tjanst(tmp_path / "b", med_polygon=True, register_id=None)
    s2 = t2.marksiffror("kor-1", LAGER, 1.0)
    assert s2 is not None and s2["yta_okning_m2"] == 10000.0
    # Ett annat projekts polygon gäller inte.
    t3, k3 = _tjanst(tmp_path / "c", med_polygon=True, register_id="reg-annat")
    s3 = t3.marksiffror("kor-1", LAGER, 1.0)
    assert s3 is not None and s3["yta_okning_m2"] == 10000.0 and k3.projekt_id == "reg-annat"


def test_byggnader_markeras_inne_och_ute(tmp_path: Path) -> None:
    t, k = _tjanst(tmp_path, med_polygon=True)
    hus = lambda i, x, y: {"id": i, "ring": [[x, y], [x + 10, y], [x + 10, y + 10], [x, y + 10], [x, y]], "hal": [], "status": "oforandrad"}  # noqa: E731
    b = {"bas": "bas", "scenarier": {"bas": [hus("inne", 10, 10), hus("ute", 60, 60)], "efter": [hus("inne", 10, 10), hus("ute", 60, 60), hus("nytt", 10, 70)]}}
    poly = t._projektpolygon(k)  # noqa: SLF001
    ut = t._markera_projektbyggnader(b, poly)  # noqa: SLF001
    assert ut is not None and ut["avgransad_till_projektet"] is True
    assert {x["id"]: x["i_projektet"] for x in ut["scenarier"]["efter"]} == {"inne": True, "ute": False, "nytt": True}
    assert ut["antal_i_projektet"] == {"bas": 1, "efter": 2}
    # Utan polygon: orört (ingen nyckel läggs till).
    assert t._markera_projektbyggnader(b, None) is b  # noqa: SLF001
