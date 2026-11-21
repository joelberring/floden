"""Läsning och skrivning av K1-raster för geometri-API:t.

Läsningen använder rasterio (klarar både markmodellens deflate-rutor och testsvitens
okomprimerade) och faller tillbaka på testsvitens rena Python-läsare om rasterio saknas.
Skrivningen är testsvitens deterministiska, okomprimerade GeoTIFF (samma som kärnans
materialisering redan använder).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from markmodell.rutnat import Ruta as MarkRuta
from testsvit.k1_io import GeoTransformInfo, las_geotiff, skriv_geotiff


class GeometriFel(Exception):
    """Fel i den gemensamma geometrin (K1 v2)."""


def las_raster(
    sokvag: str | Path,
) -> tuple[NDArray[np.float32] | NDArray[np.uint8], GeoTransformInfo]:
    """Läser ett enkelbands K1-raster (float32 eller uint8) och dess georeferens."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise GeometriFel(f"Saknar rasterfil: {fil}")
    try:
        import rasterio
    except ImportError:  # pragma: no cover - rasterio är ett beroende
        return las_geotiff(fil)
    with rasterio.open(fil) as src:
        data = src.read(1)
        t = src.transform
        if data.dtype not in (np.dtype(np.float32), np.dtype(np.uint8)):
            raise GeometriFel(f"{fil.name}: dtype {data.dtype} stöds inte (float32 eller uint8).")
        epsg = src.crs.to_epsg() if src.crs is not None else 0
        geo = GeoTransformInfo(
            bredd=int(src.width),
            hojd=int(src.height),
            x_min=float(t.c),
            y_max=float(t.f),
            pixelstorlek_x=float(t.a),
            pixelstorlek_y=float(-t.e),
            epsg=int(epsg or 0),
            dtype=str(data.dtype),
        )
    return np.ascontiguousarray(data), geo


def las_float_raster(sokvag: str | Path) -> tuple[NDArray[np.float32], GeoTransformInfo]:
    data, geo = las_raster(sokvag)
    return np.asarray(data, dtype=np.float32), geo


def skriv_raster(
    sokvag: str | Path, raster: NDArray[np.float32] | NDArray[np.uint8], geo: GeoTransformInfo
) -> Path:
    return skriv_geotiff(
        sokvag, raster, x_min=geo.x_min, y_max=geo.y_max, pixelstorlek=geo.pixelstorlek_x
    )


def ruta_fran_geo(geo: GeoTransformInfo) -> MarkRuta:
    """Markmodellens `Ruta` för ett raster (utan överlapp; täcker exakt rasterns utsträckning)."""
    if geo.bredd != geo.hojd:
        raise GeometriFel(f"K1-rutor är kvadratiska, fick {geo.hojd}x{geo.bredd}.")
    if abs(geo.pixelstorlek_x - geo.pixelstorlek_y) > 1e-9:
        raise GeometriFel("K1-rutor har kvadratiska pixlar.")
    return MarkRuta(
        e_min_m=geo.x_min,
        n_min_m=geo.y_min,
        storlek_m=geo.bredd * geo.pixelstorlek_x,
        overlapp_m=0.0,
        upplosning_m=geo.pixelstorlek_x,
    )
