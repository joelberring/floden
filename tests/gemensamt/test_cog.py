"""Tester för komprimerad GeoTIFF/COG-skrivare (`gemensamt.cog`)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio

from gemensamt.cog import skriv_komprimerad_cog, valj_cog_komprimering
from testsvit.k1_io import las_geotiff


def test_cog_float32_och_uint8_komprimering_prediktor_och_overviews(tmp_path: Path) -> None:
    komp = valj_cog_komprimering()
    assert komp in ("ZSTD", "DEFLATE")

    # 512×512-raster -> ska bli 256×256-tilat med interna overviews (faktor 2, 4, 8)
    yy, xx = np.mgrid[0:512, 0:512]
    z = (15.0 + 0.01 * xx + 0.005 * yy).astype(np.float32)
    slag = np.where((xx > 120) & (xx < 280), 9, 4).astype(np.uint8)

    z_fil = skriv_komprimerad_cog(
        tmp_path / "mark.tif",
        z,
        x_min=150000.0,
        y_max=6580256.0,
        pixelstorlek=0.5,
        nodata=-9999.0,
    )
    slag_fil = skriv_komprimerad_cog(
        tmp_path / "markslag.tif",
        slag,
        x_min=150000.0,
        y_max=6580256.0,
        pixelstorlek=0.5,
        nodata=0,
    )

    with rasterio.open(z_fil) as ds:
        assert ds.profile["tiled"] is True
        assert str(ds.profile["compress"]).upper() == komp
        assert ds.overviews(1) == [2, 4, 8]
        assert ds.tags(ns="IMAGE_STRUCTURE").get("PREDICTOR") == "3"

    with rasterio.open(slag_fil) as ds:
        assert ds.profile["tiled"] is True
        assert str(ds.profile["compress"]).upper() == komp
        assert ds.overviews(1) == [2, 4, 8]
        assert ds.tags(ns="IMAGE_STRUCTURE").get("PREDICTOR") == "2"

    # Rundresa via Flodens las_geotiff
    z_in, z_geo = las_geotiff(z_fil)
    assert np.allclose(z_in, z, atol=1e-6)
    assert z_geo.epsg == 3011

    slag_in, _ = las_geotiff(slag_fil)
    assert np.array_equal(slag_in, slag)

    # Komprimerad storlek ska vara avsevärt mindre än okomprimerad rådata
    assert z_fil.stat().st_size < z.nbytes * 0.5
    assert slag_fil.stat().st_size < slag.nbytes * 0.2
