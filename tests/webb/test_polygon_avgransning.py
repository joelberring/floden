# ruff: noqa: E501, F811
"""Avgränsning som riktig polygon i `POST /api/projekt` (K48): sparas i projekt.json, valideras och ger tydliga fel.

SYNTETISKT (samma påhittade geografi som `test_skapa_projekt.py`). Hur polygonen används i resultaten (celler och
byggnader inne i den) provas i `tests/gemensamt/test_avgransning.py`.
"""

from __future__ import annotations

import json
from typing import Any

from tests.kommuner.test_skapa import geo  # noqa: F401
from tests.konto.conftest import miljo  # noqa: F401
from tests.webb.test_skapa_projekt import Varld, skapa, v  # noqa: F401


def _l_form(i: list[float]) -> dict[str, Any]:
    """En L-formad polygon i övre vänstra delen av rektangeln i (tre fjärdedelar av ytan)."""
    x0, y0, x1, y1 = i
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, my], [mx, my], [mx, y1], [x0, y1], [x0, y0]]]}


def test_polygon_sparas_och_rektangeln_runt_den_ar_rutnatet(v: Varld) -> None:
    i = v.inre(storlek=80)
    st, d = skapa(v, "anna", "L-hus", _l_form(i))
    assert st == 201, d
    assert d["avgransning_slag"] == "polygon" and d["avgransning_3011"] == i
    ring = d["avgransning_polygon_3011"]["coordinates"][0]
    assert d["avgransning_polygon_3011"]["type"] == "Polygon" and len(ring) == 7  # L-form, ej rektangel
    assert "räknas till projektet" in d["info"][0] and "rektangeln runt" in d["info"][0]
    pj = json.loads((v.rot / "projekt" / "l-hus" / "projekt.json").read_text(encoding="utf-8"))
    assert pj["avgransning_3011"] == i and pj["avgransning_polygon_3011"] == d["avgransning_polygon_3011"]
    # Skyfallsrutan (rutnätet med buffert) är fortfarande rektangeln runt polygonen plus kontext.
    vr = pj["vatten_ruta_3011"]
    assert vr[0] <= i[0] and vr[1] <= i[1] and vr[2] >= i[2] and vr[3] >= i[3]


def test_rektangel_ger_ingen_polygon_och_beter_sig_som_forut(v: Varld) -> None:
    st, d = skapa(v, "anna", "Rek", v.inre())
    assert st == 201 and d["avgransning_slag"] == "rektangel" and d["info"] == []
    i = v.inre()
    st, d = skapa(v, "anna", "Rek polygon", {"type": "Polygon", "coordinates": [[[i[0], i[1]], [i[2], i[1]], [i[2], i[3]], [i[0], i[3]], [i[0], i[1]]]]})
    assert st == 201 and d["avgransning_slag"] == "rektangel"
    for namn in ("rek", "rek-polygon"):
        pj = json.loads((v.rot / "projekt" / namn / "projekt.json").read_text(encoding="utf-8"))
        assert "avgransning_polygon_3011" not in pj  # bakåtkompatibelt: samma fält som före polygonstödet


def test_sjalvkorsande_polygon_nekas_pa_svenska_och_inget_byggs(v: Varld) -> None:
    i = v.inre()
    fjaril = {"type": "Polygon", "coordinates": [[[i[0], i[1]], [i[2], i[3]], [i[2], i[1]], [i[0], i[3]], [i[0], i[1]]]]}
    antal = len(v.reg.projekt_med_effektiv_roll(v.konton["anna"]))
    st, d = skapa(v, "anna", "Fjäril", fjaril)
    assert st == 400 and "Polygonen korsar sig själv" in d["fel"] and "Rita om" in d["fel"]
    assert not (v.rot / "projekt" / "fjaril").exists()
    assert len(v.reg.projekt_med_effektiv_roll(v.konton["anna"])) == antal


def test_polygon_med_hal_eller_for_fa_horn_nekas(v: Varld) -> None:
    i = v.inre()
    ytter = [[i[0], i[1]], [i[2], i[1]], [i[2], i[3]], [i[0], i[3]], [i[0], i[1]]]
    hal = [[i[0] + 10, i[1] + 10], [i[0] + 20, i[1] + 10], [i[0] + 20, i[1] + 20], [i[0] + 10, i[1] + 10]]
    st, d = skapa(v, "anna", "Hål", {"type": "Polygon", "coordinates": [ytter, hal]})
    assert st == 400 and "hål" in d["fel"]
    st, d = skapa(v, "anna", "Två", {"type": "Polygon", "coordinates": [[[i[0], i[1]], [i[2], i[3]], [i[0], i[1]]]]})
    assert st == 400 and "minst 3" in d["fel"]


def test_storlek_raknas_pa_polygonen_inte_pa_rektangeln(v: Varld) -> None:
    k = v.karna
    # Tunn triangel: rektangeln runt är 1000 x 1000 m men polygonens yta är långt under 100 m².
    x, y = k[0] + 10, k[1] + 10
    tunn = {"type": "Polygon", "coordinates": [[[x, y], [x + 1000, y + 1000], [x + 1000, y + 1000.1], [x, y]]]}
    st, d = skapa(v, "anna", "Tunn", tunn)
    assert st == 400 and "för liten" in d["fel"]


def test_polygon_utanfor_yttre_gransen_nekas_och_delvis_utanfor_kärnan_varnas(v: Varld) -> None:
    g, k = v.gransen, v.karna
    ute = {"type": "Polygon", "coordinates": [[[g[2] + 10, g[3] + 10], [g[2] + 60, g[3] + 10], [g[2] + 10, g[3] + 60], [g[2] + 10, g[3] + 10]]]}
    st, d = skapa(v, "anna", "Ute", ute)
    assert st == 400 and "utanför geografin" in d["fel"]
    # Triangel vars omslutande rektangel sticker ut ur kärnan men vars yta för det mesta ligger inne: andelen räknas på polygonen.
    tri = {"type": "Polygon", "coordinates": [[[k[0] + 10, k[1] + 10], [k[0] + 70, k[1] + 10], [k[0] + 10, k[1] + 70], [k[0] + 10, k[1] + 10]]]}
    st, d = skapa(v, "anna", "Inne", tri)
    assert st == 201, d
    assert not [w for w in d["varningar"] if "giltiga kärna" in w]
    kant = {"type": "Polygon", "coordinates": [[[k[0] - 20, k[1] + 10], [k[0] + 60, k[1] + 10], [k[0] + 60, k[1] + 60], [k[0] + 20, k[1] + 60], [k[0] - 20, k[1] + 10]]]}
    st, d = skapa(v, "anna", "Kant", kant)
    assert st == 201 and any("giltiga kärna" in w for w in d["varningar"]), d
