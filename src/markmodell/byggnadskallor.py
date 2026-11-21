"""Generisk läsare av byggnadsdata (GeoPackage, GeoJSON m.fl.) till `ByggnadsKandidat`.

Passar t.ex. Lantmäteriets byggnadsdata eller OSM-byggnader hämtade via den befintliga
cachen (`data_inlasning.LokalDataCache`). Inget laddas ner här. Koordinatsystemet konverteras
till EPSG:3011 vid inläsning (aldrig mitt i en beräkning), och filens sha256 returneras så att
den kan skrivas som källpost i `recept.json`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_fil
from markmodell.byggnadsregister import ByggnadsKandidat, ByggnadsregisterFel

EPSG_MAL = 3011


@dataclass(frozen=True)
class FaltMappning:
    """Vilka attributkolumner som motsvarar vilka fält; första träffen i varje lista används."""

    id: Sequence[str] = ("byggnad_id", "objektidentitet", "id", "osm_id", "fid")
    tak_rh2000: Sequence[str] = ("takhojd_rh2000", "tak_rh2000")
    hojd_m: Sequence[str] = ("hojd_m", "height", "hojd")
    vaningar: Sequence[str] = ("vaningar", "building:levels", "levels")
    andamal: Sequence[str] = ("andamal", "building", "anvandning")
    takform: Sequence[str] = ("takform", "roof:shape")
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ByggnadsKallaResultat:
    kandidater: tuple[ByggnadsKandidat, ...]
    sha256: str
    kalla: str
    varningar: tuple[str, ...]


def _forsta(rad: Mapping[str, Any], namn: Sequence[str]) -> Any:
    for n in namn:
        v = rad.get(n)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            if isinstance(v, str) and not v.strip():
                continue
            return v
    return None


def _tal(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, int | float):
        return float(v) if math.isfinite(float(v)) else None
    try:
        return float(str(v).lower().replace("m", "").replace(",", ".").strip())
    except ValueError:
        return None


def las_byggnadskalla(
    sokvag: str | Path,
    *,
    lager: str | None = None,
    kalla: str | None = None,
    fran_epsg: int | None = None,
    falt: FaltMappning | None = None,
) -> ByggnadsKallaResultat:
    """Läser polygoner ur en vektorfil. `fran_epsg` krävs bara om filen saknar koordinatsystem."""
    import geopandas as gpd  # tung import, bara när den behövs

    fil = Path(sokvag)
    if not fil.is_file():
        raise ByggnadsregisterFel(f"Byggnadskällan finns inte: {fil}")
    fm = falt or FaltMappning()
    gdf = gpd.read_file(fil, layer=lager) if lager else gpd.read_file(fil)
    if gdf.crs is None:
        if fran_epsg is None:
            raise ByggnadsregisterFel(f"{fil.name} saknar koordinatsystem; ange fran_epsg.")
        gdf = gdf.set_crs(epsg=fran_epsg)
    if gdf.crs.to_epsg() != EPSG_MAL:
        gdf = gdf.to_crs(epsg=EPSG_MAL)
    kalla_namn = kalla or fil.stem
    varningar: list[str] = []
    kandidater: list[ByggnadsKandidat] = []
    hoppade = 0
    for _, rad in gdf.iterrows():
        geom = rad.geometry
        if geom is None or geom.is_empty:
            hoppade += 1
            continue
        delar = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        if any(d.geom_type != "Polygon" for d in delar):
            hoppade += 1
            continue
        attr = {k: rad[k] for k in gdf.columns if k != "geometry"}
        kid = _forsta(attr, fm.id)
        vanv = _tal(_forsta(attr, fm.vaningar))
        for i, d in enumerate(delar):
            d = d.buffer(0) if not d.is_valid else d
            if d.geom_type != "Polygon" or d.is_empty:
                hoppade += 1
                continue
            sid = None if kid is None else (str(kid) if len(delar) == 1 else f"{kid}#{i}")
            an = _forsta(attr, fm.andamal)
            tf = _forsta(attr, fm.takform)
            kandidater.append(
                ByggnadsKandidat(
                    polygon=tuple((float(x), float(y)) for x, y, *_ in d.exterior.coords),
                    hal=tuple(
                        tuple((float(x), float(y)) for x, y, *_ in r.coords) for r in d.interiors
                    ),
                    kalla=kalla_namn,
                    kalla_id=sid if sid is not None else None,
                    tak_rh2000=_tal(_forsta(attr, fm.tak_rh2000)),
                    hojd_m=(lambda h: h if h is not None and h > 0 else None)(
                        _tal(_forsta(attr, fm.hojd_m))
                    ),
                    vaningar=None if vanv is None or vanv <= 0 else int(vanv),
                    andamal=None if an is None else str(an),
                    takform=None if tf is None else str(tf),
                )
            )
    if hoppade:
        varningar.append(f"{hoppade} objekt utan polygon-geometri hoppades över i {fil.name}")
    return ByggnadsKallaResultat(
        kandidater=tuple(kandidater),
        sha256=sha256_fil(fil),
        kalla=kalla_namn,
        varningar=tuple(varningar),
    )
