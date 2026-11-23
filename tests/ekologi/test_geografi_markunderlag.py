# ruff: noqa: E501
"""NMD och VMI som lager i geografins manifest (K46): mappas en gång med kontextbuffert och verifieras av sha256."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from geografi.markunderlag import bygg_markunderlag
from geografi.modell import KONTRAKT_GEOGRAFI, GeografiFel, las_manifest, skriv_manifest

KARNA = (151_300.0, 6_580_300.0, 151_400.0, 6_580_400.0)


def _nmd_3006(fil: Path) -> None:
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_origin

    e, n = Transformer.from_crs(3011, 3006, always_xy=True).transform(151_350.0, 6_580_350.0)
    e0, n1 = math.floor(e / 10) * 10 - 3000, math.ceil(n / 10) * 10 + 3000
    koder = np.full((600, 600), 3, dtype=np.uint8)
    koder[:, :300] = 111
    with rasterio.open(fil, "w", driver="GTiff", height=600, width=600, count=1, dtype="uint8", crs="EPSG:3006", transform=from_origin(e0, n1, 10, 10)) as ds:
        ds.write(koder, 1)


def _vmi(fil: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import box

    gpd.GeoDataFrame({"n": [1]}, geometry=[box(151_100, 6_580_100, 151_200, 6_580_160)], crs=3011).to_file(fil, driver="GPKG")


def test_nmd_och_vmi_blir_lager_i_manifestet_med_buffert_och_sha(tmp_path: Path) -> None:
    nmd, vmi = tmp_path / "nmd.tif", tmp_path / "vmi.gpkg"
    _nmd_3006(nmd)
    _vmi(vmi)
    geo = tmp_path / "geo"
    lager, kallor, varn = bygg_markunderlag(geo, geo / "x" / ".bygge", KARNA, nmd_fil=nmd, vmi_fil=vmi, troskel_m=3000.0)
    assert set(lager) == {"nmd", "vmi"} and not varn
    assert lager["nmd"]["buffert_m"] == 3000.0 and lager["vmi"]["alder"] == "äldre"
    assert kallor["nmd"]["licens"] == "CC0" and "1981-2005" in kallor["vmi"]["alder"]
    manifest = {"kontrakt": KONTRAKT_GEOGRAFI, "id": "testgeo", "version": "v1", "lager": lager}
    skriv_manifest(geo, manifest)
    las_manifest(geo, "testgeo@v1")  # verifierar sha256 för varje fil
    (geo / "_lager" / lager["nmd"]["sha256"] / "nmd.tif").write_bytes(b"andrad")
    with pytest.raises(GeografiFel, match="nmd"):
        las_manifest(geo, "testgeo@v1")


def test_buffert_foljer_troskeln_2_till_5_km_och_utan_filer_ingen_lager(tmp_path: Path) -> None:
    nmd = tmp_path / "nmd.tif"
    _nmd_3006(nmd)
    geo = tmp_path / "geo"
    assert bygg_markunderlag(geo, geo / ".b", KARNA, nmd_fil=None, vmi_fil=None, troskel_m=3000.0) == ({}, {}, [])
    lager, _, _ = bygg_markunderlag(geo, geo / ".b", KARNA, nmd_fil=nmd, vmi_fil=None, troskel_m=500.0)
    assert lager["nmd"]["buffert_m"] == 2000.0
    with pytest.raises(GeografiFel, match="finns inte"):
        bygg_markunderlag(geo, geo / ".c", KARNA, nmd_fil=tmp_path / "saknas.tif", vmi_fil=None, troskel_m=3000.0)


def test_vmi_utan_nmd_ger_varning(tmp_path: Path) -> None:
    vmi = tmp_path / "vmi.gpkg"
    _vmi(vmi)
    geo = tmp_path / "geo"
    _, _, varn = bygg_markunderlag(geo, geo / ".b", KARNA, nmd_fil=None, vmi_fil=vmi, troskel_m=3000.0)
    assert varn and "NMD" in varn[0]
