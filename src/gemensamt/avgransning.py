# ruff: noqa: E501
"""Projektets avgränsning som riktig polygon (EPSG:3011, meter).

En avgränsning ritas som rektangel eller polygon. Beräkningsrutnätet och bufferten är alltid rektangeln runt den
(`omslutande`), men vad som räknas till projektet i resultat, nyckeltal och rapporter är det som ligger **inne i
polygonen**: celler vars mittpunkt är inne, och byggnader vars fotavtrycks inre punkt (`representative_point`) är inne.

Modulen är ren geometri utan beroende av webben eller kommunkoden, så att kommandot, webben, MCP och resultatlagret
tolkar polygonen på samma sätt. Fel har svensk text och är `AvgransningFel`.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray
from shapely import contains_xy
from shapely.geometry import MultiPoint, Point, Polygon, box, mapping
from shapely.validation import explain_validity

Rektangel = tuple[float, float, float, float]

MIN_YTA_M2 = 100.0
"""Minsta avgränsning (10 x 10 m): mindre område ger inga meningsfulla frågor."""
MIN_HORN = 3
"""Så få hörn en polygon minst har (utan den stängande upprepningen)."""
_REL_TOLERANS = 1e-6


class AvgransningFel(ValueError):
    """Avgränsningen går inte att använda; texten är på svenska och går att visa för en användare."""


def _ring_ur_text(t: str) -> list[tuple[float, float]]:
    if t.startswith("{"):
        try:
            g = json.loads(t)
            geo = g.get("geometry") or g
            if geo.get("type") not in (None, "Polygon"):
                raise AvgransningFel("Avgränsningen ska vara en polygon (GeoJSON Polygon).")
            ringar = geo["coordinates"]
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
            raise AvgransningFel("Avgränsningen: GeoJSON-polygonen går inte att läsa.") from exc
        return _ringar(ringar)
    if t.upper().startswith("POLYGON"):
        try:
            from shapely import wkt

            g = wkt.loads(t)
        except Exception as exc:  # noqa: BLE001 - shapely kastar flera typer för ogiltig WKT
            raise AvgransningFel("Avgränsningen: WKT-polygonen går inte att läsa.") from exc
        if not isinstance(g, Polygon):
            raise AvgransningFel("Avgränsningen ska vara en polygon.")
        return _ringar([[list(p) for p in g.exterior.coords]] + [[list(p) for p in r.coords] for r in g.interiors])
    raise AvgransningFel("Avgränsningen går inte att läsa.")


def _ringar(ringar: Any) -> list[tuple[float, float]]:
    if not isinstance(ringar, Sequence) or isinstance(ringar, str) or len(ringar) == 0:
        raise AvgransningFel("Avgränsningen: polygonen saknar hörn.")
    if len(ringar) > 1:
        raise AvgransningFel("Avgränsningen får inte ha hål: rita en polygon utan inre ringar.")
    try:
        pts = [(float(p[0]), float(p[1])) for p in ringar[0]]
    except (TypeError, ValueError, IndexError) as exc:
        raise AvgransningFel("Avgränsningen: koordinaterna ska vara tal.") from exc
    return pts


def polygon_fran_punkter(punkter: Sequence[tuple[float, float]]) -> Polygon:
    """Validerad polygon ur hörnen (stängd eller öppen ring). Svenska fel för för få hörn, ogiltiga tal, ingen
    yta och självkorsning."""
    pts = list(punkter)
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in pts):
        raise AvgransningFel("Avgränsningen: koordinaterna ska vara ändliga tal.")
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    ren: list[tuple[float, float]] = []
    for p in pts:  # upprepade hörn i följd (dubbelklick) tas bort
        if not ren or p != ren[-1]:
            ren.append(p)
    if len(ren) < MIN_HORN:
        raise AvgransningFel(f"Avgränsningen behöver minst {MIN_HORN} olika hörn.")
    if MultiPoint(ren).convex_hull.geom_type != "Polygon":
        raise AvgransningFel("Avgränsningen har ingen yta: hörnen ligger på en rak linje.")
    poly = Polygon(ren)
    if not poly.is_valid:
        why = explain_validity(poly)
        if why.startswith("Self-intersection") or why.startswith("Ring Self-intersection"):
            m = re.search(r"\[(-?[\d.eE+-]+) (-?[\d.eE+-]+)\]", why)
            var = f" nära E {float(m.group(1)):.0f}, N {float(m.group(2)):.0f} (EPSG:3011)" if m else ""
            raise AvgransningFel(
                f"Polygonen korsar sig själv{var}. Rita om den så att kanterna inte skär varandra."
            )
        raise AvgransningFel("Polygonen är ogiltig (kanter som överlappar eller hörn som upprepas). Rita om den.")
    if not poly.area > 0.0:
        raise AvgransningFel("Avgränsningen har ingen yta.")
    return poly


def tolka_polygon(avgransning: Any, epsg: int = 3011) -> Polygon | None:
    """Polygonen ur en GeoJSON-polygon (objekt eller text) eller en WKT-polygon, i EPSG:3011. En ren rektangel
    (`xmin,ymin,xmax,ymax` som text eller lista) ger `None`: det finns ingen polygon utöver rektangeln."""
    if isinstance(avgransning, Mapping):
        pts = _ring_ur_text(json.dumps(avgransning))
    elif isinstance(avgransning, str):
        t = avgransning.strip()
        if not (t.startswith("{") or t.upper().startswith("POLYGON")):
            return None
        pts = _ring_ur_text(t)
    else:
        return None
    if epsg != 3011:
        from gemensamt.koordinater import transformera

        pts = [transformera(x, y, epsg, 3011) for x, y in pts]
    return polygon_fran_punkter(pts)


def ar_rektangel(poly: Polygon) -> bool:
    """Sant om polygonen är sin egen omslutande rektangel (axelparallell)."""
    x0, y0, x1, y1 = poly.bounds
    box_yta = (x1 - x0) * (y1 - y0)
    return bool(abs(poly.area - box_yta) <= _REL_TOLERANS * max(box_yta, 1.0))


def omslutande(poly: Polygon) -> Rektangel:
    x0, y0, x1, y1 = poly.bounds
    return (float(x0), float(y0), float(x1), float(y1))


def till_geojson(poly: Polygon) -> dict[str, Any]:
    """GeoJSON-`Polygon` med yttre ring (stängd), koordinater i meter avrundade till mm."""
    g = mapping(poly)
    ring = [[round(float(x), 3), round(float(y), 3)] for x, y in g["coordinates"][0]]
    return {"type": "Polygon", "coordinates": [ring]}


def fran_projekt(projekt: Mapping[str, Any]) -> Polygon | None:
    """Projektets polygon ur `projekt.json` (fältet `avgransning_polygon_3011`), eller `None`: projekt utan polygon
    (skapade före polygonstödet, eller ritade som rektangel) beter sig som förut, med `avgransning_3011` som
    rektangel. En polygon som inte går att läsa ignoreras (rektangeln gäller)."""
    d = projekt.get("avgransning_polygon_3011")
    if not isinstance(d, Mapping):
        return None
    try:
        return polygon_fran_punkter(_ringar(d["coordinates"]))
    except (AvgransningFel, KeyError, TypeError):
        return None


def kontrollera_yta(poly: Polygon, min_yta_m2: float = MIN_YTA_M2) -> None:
    if poly.area < min_yta_m2:
        raise AvgransningFel(
            f"Avgränsningen är för liten: minst {min_yta_m2:.0f} m² (till exempel 10 x 10 m)."
        )


def celler_inne(
    poly: Polygon, *, x_min: float, y_max: float, pixel_m: float, rader: int, kolumner: int
) -> NDArray[np.bool_]:
    """Rastrets celler (rad 0 överst, som GeoTIFF) vars mittpunkt ligger inne i polygonen."""
    xs = x_min + (np.arange(kolumner) + 0.5) * pixel_m
    ys = y_max - (np.arange(rader) + 0.5) * pixel_m
    gx, gy = np.meshgrid(xs, ys)
    return np.asarray(contains_xy(poly, gx, gy), dtype=bool)


def byggnad_inne(poly: Polygon, ring: Sequence[Sequence[float]], hal: Sequence[Sequence[Sequence[float]]] = ()) -> bool:
    """Räknas byggnaden till projektet? Ja om fotavtryckets inre punkt (`representative_point`, alltid i
    fotavtrycket) ligger inne i polygonen: ett hus räknas en gång, och hus som bara nuddar kanten räknas inte."""
    try:
        b = Polygon(ring, [list(h) for h in hal])
        if not b.is_valid:
            b = b.buffer(0)
        p = b.representative_point()
    except (ValueError, TypeError):
        return False
    return bool(poly.covers(Point(p.x, p.y)))


def tacker_rutan(yttre: Rektangel, poly: Polygon) -> bool:
    return bool(box(*yttre).covers(poly))
