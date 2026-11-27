# ruff: noqa: E501
"""Tester för Esri WKT1 CRS-hantering i `data_inlasning.nmd` (Naturvårdsverkets läns-NMD)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from data_inlasning.nmd import EPSG_NMD, NmdFel, klipp_nmd, las_crs_epsg

# Exakt WKT1 från Naturvårdsverkets `O_lan_nmd2018bas_ogeneraliserad_v1_1.tif` (Easting/Northing-axelordning),
# där `crs.to_epsg()` ger `None` men `crs.to_epsg(confidence_threshold=20)` ger `3006`.
ESRI_WKT1_SWEREF99_TM = (
    'PROJCS["SWEREF99 TM",'
    'GEOGCS["SWEREF99",'
    'DATUM["SWEREF99",'
    'SPHEROID["GRS 1980",6378137,298.257222101004,AUTHORITY["EPSG","7019"]],'
    'AUTHORITY["EPSG","6619"]],'
    'PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4619"]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],'
    'PARAMETER["central_meridian",15],'
    'PARAMETER["scale_factor",0.9996],'
    'PARAMETER["false_easting",500000],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],'
    'AXIS["Northing",NORTH],'
    'AUTHORITY["EPSG","3006"]]'
)


def test_las_crs_epsg_hittar_3006_ur_esri_wkt1() -> None:
    crs = CRS.from_wkt(ESRI_WKT1_SWEREF99_TM)
    assert crs.to_epsg() is None
    assert las_crs_epsg(crs) == EPSG_NMD
    assert las_crs_epsg(None) is None


def test_klipp_nmd_accepterar_esri_wkt1_och_avvisar_fel_crs(tmp_path: Path) -> None:
    # Kärnområde i Göteborg (EPSG:3011)
    kaerna_3011 = (-210000.0, 6413000.0, -209800.0, 6413200.0)
    sb = transform_bounds(3011, 3006, *kaerna_3011, densify_pts=21)
    x0 = float(int(sb[0]) - 200)
    y1 = float(int(sb[3]) + 200)
    data = np.full((60, 60), 111, dtype=np.uint8)
    data[20:35, 20:35] = 51

    ok_tif = tmp_path / "nmd_esri_wkt1.tif"
    with rasterio.open(
        ok_tif,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="uint8",
        crs=CRS.from_wkt(ESRI_WKT1_SWEREF99_TM),
        transform=from_origin(x0, y1, 10.0, 10.0),
    ) as ds:
        ds.write(data, 1)

    lager = klipp_nmd(ok_tif, kaerna_3011, buffert_m=20.0)
    assert lager.klasser.dtype == np.uint8
    assert 111 in np.unique(lager.klasser)

    # Fel CRS (EPSG:3007) ska fortfarande ge NmdFel
    fel_tif = tmp_path / "nmd_fel_crs.tif"
    with rasterio.open(
        fel_tif,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="uint8",
        crs="EPSG:3007",
        transform=from_origin(147000.0, 6398000.0, 10.0, 10.0),
    ) as ds:
        ds.write(np.full((10, 10), 51, dtype=np.uint8), 1)

    with pytest.raises(NmdFel, match="3006"):
        klipp_nmd(fel_tif, kaerna_3011, buffert_m=20.0)
