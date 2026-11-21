# SPDX-License-Identifier: MIT
"""Tester för `luft.geometri`: rotation, transformer, nivåskivor och källor."""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest
from shapely.geometry import LineString, box

from luft.geometri import (
    LuftVarning,
    fran_sweref,
    linjekalla,
    linjekalla_med_rapport,
    nivaskiva_till_raster,
    punktkalla_med_rapport,
    rotationsmatris,
    till_sweref,
    voxelisera,
)
from luft.typer import LuftFel

OX = 150_000.0  # övre vänstra hörnet, EPSG:3011
OY = 6_580_000.0
DX = 2.0


def _asymmetriskt_raster() -> np.ndarray:
    """4 × 6 raster med L-formad byggnad och ett torn – ingen symmetri."""
    r = np.zeros((4, 6))
    r[0, 0:3] = 10.0  # norra raden, västra delen
    r[1, 0] = 10.0
    r[2, 4] = 20.0  # torn i sydost
    return r


def _grid(raster: np.ndarray, theta: float, buffert: float = 0.0, hojd: float = 30.0):
    return voxelisera(
        raster,
        DX,
        hojd,
        raster_dx_m=DX,
        raster_origo_x_m=OX,
        raster_origo_y_m=OY,
        vindriktning_grad=theta,
        buffert_uppstroms_m=buffert,
        buffert_nedstroms_m=buffert,
        buffert_sidor_m=buffert,
    )


# Handhärledda förväntningar: G[i, j] uttryckt i rastret R[r, c] (r söderut, c österut).
#  270°: x' = öst,  y' = norr  → G[i, j] = R[rader-1-j, i]
#    0°: x' = söder, y' = öst  → G[i, j] = R[i, j]
#   90°: x' = väst, y' = söder → G[i, j] = R[j, kol-1-i]
#  180°: x' = norr, y' = väst  → G[i, j] = R[rader-1-i, kol-1-j]
FORVANTAT = {
    270.0: lambda r: r[::-1, :].T,
    0.0: lambda r: r,
    90.0: lambda r: r[:, ::-1].T,
    180.0: lambda r: r[::-1, ::-1],
}


@pytest.mark.parametrize("theta", [0.0, 90.0, 180.0, 270.0])
def test_rotationer_ger_exakt_permuterat_raster(theta: float) -> None:
    raster = _asymmetriskt_raster()
    grid = _grid(raster, theta)
    hojd_g = FORVANTAT[theta](raster)
    assert grid.form[:2] == hojd_g.shape
    z = grid.cellcentrum_z_m()
    forvantat_fast = z[None, None, :] < hojd_g[:, :, None]
    assert np.array_equal(grid.fast, forvantat_fast)
    # medvindsriktningen i SWEREF är (−sin θ, −cos θ)
    x0, y0 = till_sweref(grid, 0.0, 0.0)
    x1, y1 = till_sweref(grid, 1.0, 0.0)
    rad = math.radians(theta)
    assert (x1 - x0) / DX == pytest.approx(-math.sin(rad), abs=1e-15)
    assert (y1 - y0) / DX == pytest.approx(-math.cos(rad), abs=1e-15)


def test_rotationsmatris_ar_ortogonal_och_270_ar_identitet() -> None:
    assert np.array_equal(rotationsmatris(270.0), np.eye(2))
    for theta in (0.0, 33.0, 90.0, 180.0, 247.5):
        r = rotationsmatris(theta)
        assert np.allclose(r @ r.T, np.eye(2), atol=1e-15)
        assert np.linalg.det(r) == pytest.approx(1.0)


def test_buffertar_och_hojd() -> None:
    raster = _asymmetriskt_raster()
    grid = voxelisera(
        raster,
        DX,
        30.0,
        raster_dx_m=DX,
        raster_origo_x_m=OX,
        raster_origo_y_m=OY,
        vindriktning_grad=270.0,
        buffert_uppstroms_m=10.0,
        buffert_nedstroms_m=20.0,
        buffert_sidor_m=4.0,
    )
    assert grid.form == (6 + 5 + 10, 4 + 2 + 2, 15)
    assert grid.origo_x_m == OX - 10.0
    assert grid.origo_y_m == OY - 4 * DX - 4.0
    assert not grid.fast[:5].any() and not grid.fast[-10:].any()
    with pytest.raises(LuftFel):
        _grid(raster, 270.0, hojd=20.0)  # tornet når taket
    bad = raster.copy()
    bad[0, 0] = np.nan
    with pytest.raises(LuftFel):
        _grid(bad, 270.0)


def test_vegetation() -> None:
    raster = _asymmetriskt_raster()
    veg = np.zeros_like(raster)
    veg[3, :] = 5.0  # trädrad i söder
    veg[0, 0] = 15.0  # träd "över" byggnaden: bara över taket blir det lad
    grid = voxelisera(
        raster,
        DX,
        30.0,
        raster_dx_m=DX,
        raster_origo_x_m=OX,
        raster_origo_y_m=OY,
        vegetation_hojd_m=veg,
        lad_m2_m3=1.5,
        buffert_uppstroms_m=0.0,
        buffert_nedstroms_m=0.0,
        buffert_sidor_m=0.0,
    )
    # 270°: rad 3 (söder) är j = 0
    assert np.all(grid.lad_m2_m3[:, 0, :2] == 1.5)  # z = 1, 3 m < 5 m
    assert np.all(grid.lad_m2_m3[:, 0, 2:] == 0.0)
    # (r=0, c=0) → (i=0, j=3): byggnad till 10 m, lad 10–15 m (lager 5–7)
    assert np.all(grid.lad_m2_m3[0, 3, :5] == 0.0)
    assert np.all(grid.lad_m2_m3[0, 3, 5:7] == 1.5)
    assert not np.any(grid.fast & (grid.lad_m2_m3 > 0))


@pytest.mark.parametrize("theta", [0.0, 37.0, 90.0, 180.0, 270.0, 301.5])
def test_rundresa_sweref_index_sweref(theta: float) -> None:
    grid = _grid(_asymmetriskt_raster(), theta, buffert=6.0)
    rng = np.random.default_rng(1)
    i = rng.uniform(-3, 20, 500)
    j = rng.uniform(-3, 20, 500)
    x, y = till_sweref(grid, i, j)
    i2, j2 = fran_sweref(grid, x, y)
    assert np.max(np.abs(i2 - i)) < 1e-9
    assert np.max(np.abs(j2 - j)) < 1e-9
    x2, y2 = till_sweref(grid, i2, j2)
    assert np.max(np.abs(x2 - x)) < 1e-6 and np.max(np.abs(y2 - y)) < 1e-6


@pytest.mark.parametrize("theta", [0.0, 90.0, 180.0, 270.0])
def test_nivaskiva_ger_fotavtryck_som_nan(theta: float) -> None:
    raster = _asymmetriskt_raster()
    grid = _grid(raster, theta, buffert=4.0)
    falt = np.ones(grid.form)
    utb = (OX, OY - 4 * DX, OX + 6 * DX, OY)
    for metod in ("narmast", "bilinjar"):
        ut, ruta = nivaskiva_till_raster(grid, falt, 2.0, DX, utb, metod=metod)  # type: ignore[arg-type]
        assert ruta.form_rc == raster.shape
        assert np.array_equal(np.isnan(ut), raster > 2.0), metod
        assert np.all(ut[~np.isnan(ut)] == 1.0)
    # på 15 m syns bara tornet
    ut, _ = nivaskiva_till_raster(grid, falt, 15.0, DX, utb)
    assert np.array_equal(np.isnan(ut), raster > 15.0)


def test_nivaskiva_falt_tillbaka_och_utanfor() -> None:
    raster = np.zeros((4, 6))
    grid = _grid(raster, 90.0)
    # fält = x-koordinat i SWEREF; tillbaka på rastret ska det vara cellcentrums x
    ii, jj = np.meshgrid(np.arange(grid.form[0]), np.arange(grid.form[1]), indexing="ij")
    x, _ = till_sweref(grid, ii, jj)
    falt = np.repeat(x[:, :, None], grid.form[2], axis=2)
    ut, ruta = nivaskiva_till_raster(
        grid, falt, 1.0, DX, (OX - 4, OY - 8, OX + 12, OY), metod="bilinjar"
    )
    xs, _ = ruta.cellcentrum_m()
    inne = ~np.isnan(ut)
    assert np.allclose(ut[inne], xs[inne], atol=1e-9)
    assert np.all(np.isnan(ut[:, :2]))  # väster om domänen
    assert inne.sum() == 24


def _bruteforce_inne_langd(grid, linje: np.ndarray) -> float:
    """Längd inne i domänen via shapely i rutnätets lokala (roterade) system."""
    nx, ny, _ = grid.form
    a, b = fran_sweref(grid, linje[:, 0], linje[:, 1])
    inne = LineString(np.c_[a, b]).intersection(box(-0.5, -0.5, nx - 0.5, ny - 0.5))
    return float(inne.length) * grid.dx_m


def test_linjekalla_mot_shapely_i_sweref() -> None:
    """Helt oberoende kontroll vid 270° (domänen = rastret, kända hörn)."""
    grid = _grid(np.zeros((10, 10)), 270.0)
    linje = np.array([[OX - 7.0, OY - 5.3], [OX + 9.0, OY - 6.1], [OX + 30.0, OY - 11.0]])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LuftVarning)
        kallor = linjekalla(grid, [linje], [0.0125])
    lokal = LineString(linje - [OX, OY]).intersection(box(0.0, -20.0, 20.0, 0.0))
    forv = lokal.length * 0.0125
    assert abs(kallor.sum() - forv) / forv < 1e-12


@pytest.mark.parametrize("theta", [270.0, 0.0, 33.0, 210.0])
def test_linjekalla_massbevarande(theta: float) -> None:
    grid = _grid(np.zeros((10, 10)), theta, buffert=0.0)
    e = np.array([0.003, 0.0125, 0.007])
    linjer = [
        np.array([[OX + 1.3, OY - 2.7], [OX + 17.1, OY - 15.9]]),  # diagonal, inne
        np.array([[OX - 7.0, OY - 5.3], [OX + 9.0, OY - 6.1], [OX + 30.0, OY - 11.0]]),  # ut/in
        np.array([[OX + 4.0, OY - 4.0], [OX + 4.0, OY - 12.0]]),  # längs cellgräns
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LuftVarning)
        kallor, rapport = linjekalla_med_rapport(grid, linjer, e, hojd_m=0.5)
    forv = sum(_bruteforce_inne_langd(grid, li) * ei for li, ei in zip(linjer, e, strict=True))
    total = sum(LineString(li).length * ei for li, ei in zip(linjer, e, strict=True))
    rel = abs(kallor.sum() - forv) / forv
    assert rel < 1e-12, rel
    assert abs(rapport.inne_g_s + rapport.utanfor_g_s - total) / total < 1e-12
    assert rapport.utanfor_g_s > 0
    assert np.all(kallor[:, :, 1:] == 0)  # 0,5 m → lager 0
    assert np.all(kallor >= 0)


def test_linjekalla_diagonal_per_cell() -> None:
    """Diagonal genom cellhörn: varje cell på diagonalen får dx·√2·e."""
    grid = _grid(np.zeros((5, 5)), 270.0)
    e = 0.01
    linje = np.array([[OX, OY - 10.0], [OX + 10.0, OY]])  # SV → NO genom hörnen
    kallor = linjekalla(grid, [linje], [e])
    k0 = kallor[:, :, 0]
    assert np.allclose(np.diag(k0), DX * math.sqrt(2) * e, rtol=1e-14)
    assert np.count_nonzero(k0) == 5
    assert abs(k0.sum() - 10 * math.sqrt(2) * e) / (10 * math.sqrt(2) * e) < 1e-12


def test_linjekalla_varnar_utanfor() -> None:
    grid = _grid(np.zeros((4, 4)), 270.0)
    with pytest.warns(LuftVarning, match="utanför"):
        linjekalla(grid, [np.array([[OX - 10, OY - 3], [OX + 4, OY - 3]])], [1.0])


def test_kalla_i_fast_cell_flyttas_och_flaggas() -> None:
    raster = np.zeros((4, 4))
    raster[1, 1] = 3.0  # 3 m: lager 0 (z = 1 m) fast, lager 1 (z = 3 m) luft
    grid = _grid(raster, 270.0)
    linje = np.array([[OX + 0.5, OY - 3.0], [OX + 7.5, OY - 3.0]])  # genom rad 1
    with pytest.warns(LuftVarning, match="flyttades"):
        kallor = linjekalla(grid, [linje], [0.02])
    _, rapport = linjekalla_med_rapport(grid, [linje], [0.02])
    assert rapport.antal_flyttade_celler == 1
    assert rapport.flyttad_g_s == pytest.approx(DX * 0.02, rel=1e-14)
    # (r=1, c=1) → (i=1, j=2); massan ligger i lager 1, inget i fast cell
    assert kallor[1, 2, 0] == 0.0
    assert kallor[1, 2, 1] == pytest.approx(DX * 0.02, rel=1e-14)
    assert not np.any(kallor[grid.fast] != 0)
    assert abs(kallor.sum() - 7.0 * 0.02) / (7.0 * 0.02) < 1e-12


def test_punktkalla() -> None:
    raster = np.zeros((4, 4))
    raster[0, 3] = 5.0
    grid = _grid(raster, 0.0)
    punkter = np.array([[OX + 1.0, OY - 1.0], [OX + 7.0, OY - 1.0], [OX + 100.0, OY]])
    kallor, rapport = punktkalla_med_rapport(grid, punkter, [1.0, 2.0, 4.0], hojd_m=0.5)
    # 0°: G[i, j] = R[i, j]
    assert kallor[0, 0, 0] == 1.0
    assert kallor[0, 3, 0] == 0.0 and kallor[0, 3, 2] == 2.0  # flyttad över 5 m
    assert rapport.utanfor_g_s == 4.0 and rapport.flyttad_g_s == 2.0
    assert kallor.sum() == 3.0


def test_kalla_ovanfor_domanen_ar_fel() -> None:
    grid = _grid(np.zeros((2, 2)), 270.0)
    with pytest.raises(LuftFel):
        linjekalla(grid, [np.array([[OX, OY - 1], [OX + 2, OY - 1]])], [1.0], hojd_m=100.0)
    with pytest.raises(LuftFel):
        linjekalla(grid, [np.array([[OX, OY - 1], [OX + 2, OY - 1]])], [1.0, 2.0])
