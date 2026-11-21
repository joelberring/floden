# SPDX-License-Identifier: MIT
"""Tester för `luft.export` (kontrakt KL2/v1 + K0-manifest)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from gemensamt.hash import sha256_fil
from gemensamt.manifest import las_manifest
from luft.export import KONTRAKT_KL2, Luftresultat, exportera, halt_filnamn
from luft.geometri import RasterRuta, nivaskiva_till_raster, voxelisera
from luft.typer import LuftFel

OX = 150_000.0
OY = 6_580_000.0


def _resultat() -> tuple[Luftresultat, np.ndarray]:
    raster = np.zeros((20, 24))
    raster[5:9, 6:12] = 12.0
    grid = voxelisera(
        raster,
        2.0,
        30.0,
        raster_dx_m=2.0,
        raster_origo_x_m=OX,
        raster_origo_y_m=OY,
        vindriktning_grad=200.0,
        buffert_uppstroms_m=10.0,
        buffert_nedstroms_m=20.0,
        buffert_sidor_m=6.0,
    )
    rng = np.random.default_rng(3)
    halt_3d = rng.gamma(2.0, 2e-6, size=grid.form)
    halt_3d[grid.fast] = 0.0
    medel, ruta = nivaskiva_till_raster(grid, halt_3d, 2.0, 2.0, (OX, OY - 40.0, OX + 48.0, OY))
    kansl = rng.uniform(0, 1e-3, size=ruta.form_rc)
    res = Luftresultat(
        amne="nox",
        ruta=ruta,
        raster_g_m3={"arsmedel": medel, "p98": medel * 3.0},
        halt_3d_g_m3=halt_3d,
        grid=grid,
        kanslighet_g_m3_per_g_s={"forskola_1": kansl},
    )
    return res, raster


def test_exportera_kl2_och_run_json(tmp_path: Path) -> None:
    res, raster = _resultat()
    indatafil = tmp_path / "in.tif"
    indatafil.write_bytes(b"syntetisk indata")
    run, manifest = exportera(
        tmp_path / "ut",
        res,
        backend="numpy",
        parametrar={"u_min_m_s": 0.5, "hojd_over_mark_m": 2.0},
        indata=[("byggnadshojd", indatafil)],
        matvarden={"massbalans_rel": 1e-12},
        varningar=["testvarning"],
        kodversion="test",
    )
    last = las_manifest(run)
    assert last == manifest
    assert last.modul == "luft"
    assert last.parametrar["backend"] == "numpy"
    assert last.parametrar["kontrakt"] == {"KL1": "KL1/v1", "KL2": KONTRAKT_KL2}
    assert last.indata[0].sha256 == sha256_fil(indatafil)
    roller = {p.roll: p for p in last.utdata}
    assert set(roller) == {"halt_arsmedel", "halt_p98", "halt_3d", "kanslighet_forskola_1"}
    for post in last.utdata:
        assert sha256_fil(tmp_path / "ut" / post.fil) == post.sha256

    tif = tmp_path / "ut" / halt_filnamn("nox", "arsmedel")
    with rasterio.open(tif) as ds:
        assert ds.crs.to_epsg() == 3011
        assert ds.transform == Affine(2.0, 0.0, OX, 0.0, -2.0, OY)
        assert (ds.height, ds.width) == (20, 24)
        assert ds.dtypes[0] == "float32"
        assert ds.tags()["kontrakt"] == KONTRAKT_KL2
        assert ds.units[0] == "ug/m3"
        data = ds.read(1)
    forv = (res.raster_g_m3["arsmedel"] * 1e6).astype(np.float32)
    assert np.array_equal(data, forv, equal_nan=True)
    # fotavtrycket (rader 5–8, kolumner 6–11) är nodata; vid 200° kan dubbel
    # närmaste-granne-sampling flytta kanten en cell, så jämför mot krympt/vidgat avtryck
    nan = np.isnan(data)
    assert raster.sum() > 0
    assert np.all(nan[6:8, 7:11])
    vidgat = np.zeros_like(nan)
    vidgat[4:10, 5:13] = True
    assert not np.any(nan & ~vidgat)

    with rasterio.open(tmp_path / "ut" / "kanslighet_forskola_1.tif") as ds:
        assert ds.crs.to_epsg() == 3011
        assert ds.units[0] == "(ug/m3)/(g/s)"
        k = ds.read(1)
    assert np.allclose(k, res.kanslighet_g_m3_per_g_s["forskola_1"] * 1e6, rtol=1e-7)

    with np.load(tmp_path / "ut" / "halt_nox_3d.npz", allow_pickle=False) as d:
        assert np.array_equal(d["halt_g_m3"], res.halt_3d_g_m3)
        meta = json.loads(str(d["meta"]))
    assert meta["kontrakt"] == KONTRAKT_KL2 and meta["vindriktning_grad"] == 200.0


def test_export_avvisar_fel(tmp_path: Path) -> None:
    res, _ = _resultat()
    with pytest.raises(LuftFel, match="backend"):
        exportera(tmp_path, res, backend="opencl")
    with pytest.raises(LuftFel):
        halt_filnamn("../nox", "arsmedel")
    fel = Luftresultat(
        amne="nox", ruta=RasterRuta(OX, OY, 2.0, 3, 3), raster_g_m3={"a": np.ones((2, 2))}
    )
    with pytest.raises(LuftFel, match="Rasterform"):
        exportera(tmp_path, fel, backend="numpy")
    neg = Luftresultat(
        amne="nox", ruta=RasterRuta(OX, OY, 2.0, 2, 2), raster_g_m3={"a": -np.ones((2, 2))}
    )
    with pytest.raises(LuftFel, match="Negativa"):
        exportera(tmp_path, neg, backend="numpy")
