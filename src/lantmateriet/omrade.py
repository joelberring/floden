"""Provområdet: en polygon (GeoPackage/GeoJSON) som K1-rutor.

Området ritas i QGIS och sparas i EPSG:3011 (SWEREF 99 18 00); andra system räknas om vid
inläsningen (AGENTS.md: aldrig mitt i en beräkning). Rutorna ligger på ett fast nät
(`mm_<E>_<N>` med hörn i hela multiplar av rutstorleken), så samma yta ger samma rutnamn varje gång.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import shapely
from shapely.geometry.base import BaseGeometry

from data_inlasning.cache import DataInlasningFel
from gemensamt.hash import sha256_fil

# En ruta som bara berörs av ett avrundningsflisa (< 1 m2) hör inte till området.
_MIN_YTA_M2 = 1.0


@dataclass(frozen=True)
class Omrade:
    """Provområdets yta i EPSG:3011."""

    polygon: BaseGeometry
    sha256: str
    fil: Path

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = self.polygon.bounds
        return (float(x0), float(y0), float(x1), float(y1))

    @property
    def yta_km2(self) -> float:
        return float(self.polygon.area) / 1.0e6


def las_omrade(fil: str | Path, *, lager: str | None = None, max_km2: float = 25.0) -> Omrade:
    """Läser områdets polygon(er) och slår ihop dem. Fel om ytan saknas, är tom eller för stor."""
    import geopandas as gpd  # tung import, bara när den behövs

    p = Path(fil)
    if not p.is_file():
        raise DataInlasningFel(
            f"Områdesfilen finns inte: {p}. Rita området i QGIS och spara det som GeoPackage "
            "(EPSG:3011), till exempel data/omraden/prov1.gpkg."
        )
    gdf = gpd.read_file(p, layer=lager) if lager else gpd.read_file(p)
    if gdf.crs is None:
        raise DataInlasningFel(
            f"{p.name} saknar koordinatsystem. Spara om det i QGIS med EPSG:3011 (SWEREF 99 18 00)."
        )
    if gdf.crs.to_epsg() != 3011:
        gdf = gdf.to_crs(epsg=3011)
    ytor = [g for g in gdf.geometry if g is not None and not g.is_empty]
    ytor = [g for g in ytor if g.geom_type in ("Polygon", "MultiPolygon")]
    if not ytor:
        raise DataInlasningFel(f"{p.name} innehåller ingen polygon.")
    union = shapely.union_all([g.buffer(0) if not g.is_valid else g for g in ytor])
    om = Omrade(polygon=union, sha256=sha256_fil(p), fil=p)
    if om.yta_km2 <= 0.0:
        raise DataInlasningFel(f"Området i {p.name} har ingen yta.")
    if om.yta_km2 > max_km2:
        raise DataInlasningFel(
            f"Området är {om.yta_km2:.1f} km², mer än gränsen {max_km2:g} km² (--max-km2). "
            "Provområdet är tänkt att vara ungefär 1 km²."
        )
    return om


def rutor_for_omrade(polygon: BaseGeometry, storlek_m: float) -> list[tuple[float, float]]:
    """Sydvästra hörn `(e_min, n_min)` för alla rutor på nätet som rör polygonen, sorterade."""
    if not math.isfinite(storlek_m) or storlek_m <= 0.0:
        raise DataInlasningFel(f"Ogiltig rutstorlek: {storlek_m}.")
    x0, y0, x1, y1 = polygon.bounds
    i0, i1 = math.floor(x0 / storlek_m), math.floor(x1 / storlek_m)
    j0, j1 = math.floor(y0 / storlek_m), math.floor(y1 / storlek_m)
    ut: list[tuple[float, float]] = []
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            ruta = shapely.box(
                i * storlek_m, j * storlek_m, (i + 1) * storlek_m, (j + 1) * storlek_m
            )
            if ruta.intersection(polygon).area > _MIN_YTA_M2:
                ut.append((i * storlek_m, j * storlek_m))
    return ut
