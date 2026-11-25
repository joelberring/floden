"""Komprimerad GeoTIFF/COG-skrivare med ZSTD (reserv DEFLATE), prediktor och overviews.

Säkerställer att 500×500 m-rutor (t.ex. 0,5 m upplösning) komprimeras effektivt med:
- `COMPRESS=ZSTD` (eller `DEFLATE` om GDAL-bygget saknar ZSTD),
- `PREDICTOR=3` för flyttal (`float32`/`float64`) och `PREDICTOR=2` för heltal (`uint8` m.fl.),
- 256×256-tiles (eller största multipel av 16) och interna overviews för snabba Range Requests.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

EPSG_SWEREF99_1800 = 3011
_HAR_RASTERIO = importlib.util.find_spec("rasterio") is not None


@lru_cache(maxsize=1)
def valj_cog_komprimering() -> str:
    """Returnerar `'ZSTD'` om GDAL stöder ZSTD i denna miljö, annars `'DEFLATE'`."""
    if not _HAR_RASTERIO:
        return "DEFLATE"
    import tempfile

    import rasterio
    from rasterio.transform import from_origin

    with tempfile.TemporaryDirectory(prefix="floden_zstd_probe_") as tmp:
        testfil = Path(tmp) / "probe.tif"
        try:
            with rasterio.open(
                testfil,
                "w",
                driver="GTiff",
                height=16,
                width=16,
                count=1,
                dtype="uint8",
                crs=f"EPSG:{EPSG_SWEREF99_1800}",
                transform=from_origin(0.0, 16.0, 1.0, 1.0),
                compress="ZSTD",
                predictor=2,
            ) as dst:
                dst.write(np.zeros((16, 16), dtype=np.uint8), 1)
            return "ZSTD"
        except Exception:  # noqa: BLE001 - GDAL utan libzstd kastar RasterioIOError
            return "DEFLATE"


def _overviews_faktorer(rader: int, kolumner: int) -> list[int]:
    """Beräknar overview-faktorer (2, 4, 8, ...) tills minsta sidan når <= 64 pixlar."""
    minsta = min(rader, kolumner)
    faktorer: list[int] = []
    faktor = 2
    while minsta // faktor >= 64:
        faktorer.append(faktor)
        faktor *= 2
    return faktorer


def skriv_komprimerad_cog(
    sokvag: str | Path,
    matris: NDArray[np.float32] | NDArray[np.uint8],
    *,
    x_min: float,
    y_max: float,
    pixelstorlek: float,
    epsg: int = EPSG_SWEREF99_1800,
    nodata: float | int | None = None,
    bygg_overviews: bool = True,
) -> Path:
    """Skriver ett enkelbands-GeoTIFF/COG med ZSTD/DEFLATE, prediktor och interna overviews."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)

    if not _HAR_RASTERIO:
        from testsvit.k1_io import skriv_geotiff

        return skriv_geotiff(
            mal,
            matris,
            x_min=x_min,
            y_max=y_max,
            pixelstorlek=pixelstorlek,
            epsg=epsg,
        )

    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin

    arr = np.ascontiguousarray(matris)
    rader, kolumner = arr.shape
    ar_flyttal = bool(np.issubdtype(arr.dtype, np.floating))
    komprimering = valj_cog_komprimering()
    predictor = 3 if ar_flyttal else 2

    transform = from_origin(float(x_min), float(y_max), float(pixelstorlek), float(pixelstorlek))
    minsta_block = min(256, rader, kolumner)
    tiled = minsta_block >= 16

    profil: dict[str, object] = {
        "driver": "GTiff",
        "height": rader,
        "width": kolumner,
        "count": 1,
        "dtype": str(arr.dtype),
        "crs": f"EPSG:{int(epsg)}",
        "transform": transform,
        "compress": komprimering,
        "predictor": predictor,
    }
    if nodata is not None:
        profil["nodata"] = nodata
    if tiled:
        profil["tiled"] = True
        profil["blockxsize"] = 256 if kolumner >= 256 else 16 * (kolumner // 16)
        profil["blockysize"] = 256 if rader >= 256 else 16 * (rader // 16)

    with rasterio.open(mal, "w", **profil) as dst:
        dst.write(arr, 1)
        if bygg_overviews:
            faktorer = _overviews_faktorer(rader, kolumner)
            if faktorer:
                resampling = Resampling.average if ar_flyttal else Resampling.nearest
                dst.build_overviews(faktorer, resampling)
                dst.update_tags(ns="rio_overview", resampling=resampling.name)

    return mal
