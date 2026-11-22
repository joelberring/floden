"""Avtappning till ledningsnätet som uttrycklig parameter (samordnarens beslut 2026-11-22).

`avtappning_mm_h` gäller hela frågans område, standard **0 mm/h**: ledningsnätet antas fullt vid
ett dimensionerande skyfall (försiktigt och vanligt i skyfallskarteringar). Inget annat
standardvärde gissas. Per område anges polygoner med eget värde, i frågans TOML
(`avtappning_omraden = [{ geometri = {type = "Polygon", coordinates = [...]}, mm_h = 20 }]`) eller
i en fil (GeoJSON eller GeoPackage, EPSG:3011, egenskapen `avtappning_mm_h`). Ett område ersätter
grundvärdet i de celler vars mittpunkt ligger i polygonen; överlappande områden med olika värden
ger fel. Värdet och källan skrivs alltid i manifestet (`beskrivning`).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from testsvit.k1_io import GeoTransformInfo

AVTAPPNING_STANDARD_MM_H = 0.0
AVTAPPNING_STANDARD_KALLA = "standard: 0 mm/h (ledningsnätet antas fullt)"


class AvtappningFel(ValueError):
    """Ogiltig avtappning (negativ, okänd form, överlapp med olika värden)."""


def _tal(v: Any, namn: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise AvtappningFel(f"{namn} måste vara ett ändligt tal, fick {v!r}.")
    if float(v) < 0.0:
        raise AvtappningFel(f"{namn} får inte vara negativ, fick {v}.")
    return float(v)


def validera_omraden(raw: Any) -> list[dict[str, Any]]:
    """Kanonisk lista `[{geometri, mm_h}]` ur frågans `avtappning_omraden`."""
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise AvtappningFel("avtappning_omraden måste vara en lista med {geometri, mm_h}.")
    from shapely.geometry import shape

    ut: list[dict[str, Any]] = []
    for i, post in enumerate(raw):
        if not isinstance(post, Mapping) or set(post) != {"geometri", "mm_h"}:
            raise AvtappningFel(
                f"avtappning_omraden[{i}] måste ha exakt fälten 'geometri' och 'mm_h'."
            )
        geom = dict(post["geometri"])
        try:
            g = shape(geom)
        except Exception as exc:  # shapely ger olika feltyper
            raise AvtappningFel(f"avtappning_omraden[{i}]: ogiltig geometri: {exc}") from exc
        if g.geom_type not in ("Polygon", "MultiPolygon") or g.is_empty or not g.is_valid:
            raise AvtappningFel(f"avtappning_omraden[{i}]: geometrin måste vara en giltig polygon.")
        ut.append({"geometri": json.loads(json.dumps(geom)), "mm_h": _tal(post["mm_h"], "mm_h")})
    return ut


def las_omraden_fil(fil: str | Path) -> list[dict[str, Any]]:
    """Områden ur GeoJSON (FeatureCollection) eller GeoPackage med `avtappning_mm_h`."""
    p = Path(fil)
    if p.suffix.lower() in (".geojson", ".json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        features = data.get("features") if isinstance(data, Mapping) else None
        if not isinstance(features, list):
            raise AvtappningFel(f"{p.name}: förväntade en GeoJSON FeatureCollection.")
        raw = []
        for f in features:
            props = f.get("properties") or {}
            if "avtappning_mm_h" not in props:
                raise AvtappningFel(f"{p.name}: ett område saknar egenskapen avtappning_mm_h.")
            raw.append({"geometri": f["geometry"], "mm_h": props["avtappning_mm_h"]})
        return validera_omraden(raw)
    if p.suffix.lower() == ".gpkg":
        import geopandas as gpd

        df = gpd.read_file(p)
        if "avtappning_mm_h" not in df.columns:
            raise AvtappningFel(f"{p.name}: kolumnen avtappning_mm_h saknas.")
        if df.crs is not None and df.crs.to_epsg() != 3011:
            raise AvtappningFel(f"{p.name}: områdena måste vara i EPSG:3011.")
        return validera_omraden(
            [
                {"geometri": json.loads(json.dumps(g.__geo_interface__)), "mm_h": float(v)}
                for g, v in zip(df.geometry, df["avtappning_mm_h"], strict=True)
            ]
        )
    raise AvtappningFel(f"{p.name}: avtappningsområden läses ur .geojson, .json eller .gpkg.")


def avtappning_raster(
    geo: GeoTransformInfo,
    mm_h: float = AVTAPPNING_STANDARD_MM_H,
    omraden: Sequence[Mapping[str, Any]] = (),
) -> NDArray[np.float64]:
    """Kapacitet (mm/h) per cell: grundvärdet, ersatt i områdenas celler (cellmitt)."""
    from shapely import contains_xy
    from shapely.geometry import shape

    bas = _tal(mm_h, "avtappning_mm_h")
    ut = np.full((geo.hojd, geo.bredd), bas, dtype=np.float64)
    if not omraden:
        return ut
    kol = geo.x_min + (np.arange(geo.bredd) + 0.5) * geo.pixelstorlek_x
    rad = geo.y_max - (np.arange(geo.hojd) + 0.5) * geo.pixelstorlek_y
    xx, yy = np.meshgrid(kol, rad)
    satt = np.full(ut.shape, np.nan)
    for i, o in enumerate(omraden):
        inne = contains_xy(shape(dict(o["geometri"])), xx, yy)
        v = float(o["mm_h"])
        krock = inne & np.isfinite(satt) & (satt != v)
        if np.any(krock):
            raise AvtappningFel(
                f"avtappning_omraden[{i}] överlappar ett annat område med annat värde "
                f"({int(np.count_nonzero(krock))} celler)."
            )
        satt[inne] = v
    return np.where(np.isfinite(satt), satt, ut)


def beskrivning(
    mm_h: float, omraden: Sequence[Mapping[str, Any]], *, angiven: bool, fil: str | None = None
) -> dict[str, Any]:
    """Värde och källa för manifest och webbens Antaganden."""
    if not angiven and not omraden:
        kalla = AVTAPPNING_STANDARD_KALLA
    elif omraden:
        kalla = f"angiven: {float(mm_h):g} mm/h, {len(omraden)} område(n) med eget värde"
        if fil:
            kalla += f" ur {fil}"
    else:
        kalla = f"angiven: {float(mm_h):g} mm/h i hela området"
    if not omraden and float(mm_h) == 0.0:
        text = "Avtappning: 0 mm/h (ledningsnätet antas fullt)"
    elif omraden:
        varden = sorted({float(o["mm_h"]) for o in omraden})
        text = (
            f"Avtappning: {float(mm_h):g} mm/h, i {len(omraden)} område(n) "
            + ", ".join(f"{v:g}" for v in varden) + " mm/h (angivet)"
        )
    else:
        text = f"Avtappning: {float(mm_h):g} mm/h (angivet)"
    return {
        "avtappning_mm_h": float(mm_h),
        "avtappning_kalla": kalla,
        "avtappning_antagande": text,
        "avtappning_omraden": [
            {"mm_h": float(o["mm_h"])} for o in omraden
        ],
    }
