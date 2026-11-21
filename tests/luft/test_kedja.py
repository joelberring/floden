# SPDX-License-Identifier: MIT
"""Hela luftkedjan på ett handbyggt vindbibliotek (ingen LES, snabbt)."""

from __future__ import annotations

import numpy as np
import pytest

from luft.geometri import fran_sweref, linjekalla, voxelisera
from luft.kedja import berakna_halter, kanslighet_for_receptor
from luft.transport import Amne, TransportOperator
from luft.typer import Inflode, Vindfalt
from luft.vindbibliotek import Vaderserie, Vindbibliotek

DX = 2.0
OX, OY = 152_000.0, 6_577_100.0


def _bibliotek() -> tuple[Vindbibliotek, tuple[float, float, float, float]]:
    hojd = np.zeros((30, 40))
    hojd[10:16, 18:24] = 10.0  # ett hus
    grid = voxelisera(hojd, DX, 30.0, raster_dx_m=DX, raster_origo_x_m=OX,
                      raster_origo_y_m=OY, buffert_uppstroms_m=10.0,
                      buffert_nedstroms_m=20.0, buffert_sidor_m=10.0,
                      vindriktning_grad=270.0)
    u = np.zeros((3, *grid.form))
    u[0][grid.luft] = 3.0
    nu_t = np.where(grid.luft, 0.5, 0.0)
    vind = Vindfalt(grid, Inflode(3.0), u, nu_t, medelperiod_s=600.0)
    utbredning = (OX, OY - 30 * DX, OX + 40 * DX, OY)
    return Vindbibliotek((vind,)), utbredning


def test_kedja_massbalans_och_skalning() -> None:
    bib, utb = _bibliotek()
    linje = [np.array([[OX + 4.0, OY - 41.0], [OX + 76.0, OY - 41.0]])]
    vader = Vaderserie.skapa([270.0, 270.0, 270.0, 90.0], [3.0, 6.0, 0.2, 3.0])
    res = berakna_halter(bib, Amne("NOx"), linje, [1e-4], vader, utb, DX)
    assert res.matvarden["max_rel_massbalansfel"] < 1e-8
    assert res.fordelning.antal_svag_vind == 1
    # närmaste sektor till 90° är 270° (enda riktningen): medel av f = 1, 0.5, 1
    c = res.halter_per_riktning_g_m3[0]
    np.testing.assert_allclose(res.statistik.arsmedel(), c * (1.0 + 0.5 + 1.0) / 3.0,
                               rtol=1e-12)
    assert np.all(np.isnan(c[10:16, 18:24]))  # huset
    assert np.nanmin(c) >= 0.0


def test_adjungerad_kanslighet_stammer_med_framatlosning() -> None:
    """Σ känslighet·utsläpp = årsmedel i receptorn (dualitet genom hela kedjan)."""
    bib, utb = _bibliotek()
    vind = bib.for_riktning(270.0)
    amne = Amne("NOx")
    vader = Vaderserie.skapa([270.0] * 3, [3.0, 4.0, 6.0])
    receptor = (OX + 51.0, OY - 35.0)
    linje = [np.array([[OX + 4.0, OY - 41.0], [OX + 76.0, OY - 41.0]])]
    res = berakna_halter(bib, amne, linje, [1e-4], vader, utb, DX, kallhojd_m=0.5)
    kansl, _ = kanslighet_for_receptor(bib, amne, receptor, 2.0, res.fordelning, utb, DX)

    s = linjekalla(vind.grid, linje, [1e-4], 0.5)
    i, j = fran_sweref(vind.grid, *receptor)
    ii, jj = int(np.floor(float(i) + 0.5)), int(np.floor(float(j) + 0.5))
    c_fram = TransportOperator(vind, amne).los(s)[ii, jj, 1]
    medel_f = np.mean([3.0 / 3.0, 3.0 / 4.0, 3.0 / 6.0])
    # källorna ligger i lager 0; kanslighetsrastret är lager 0 på samma rutnät
    s_raster = np.zeros_like(kansl)
    for (xi, yi) in zip(*np.nonzero(s[:, :, 0]), strict=True):
        x, y = OX + 0.0, OY + 0.0
        from luft.geometri import till_sweref

        x, y = till_sweref(vind.grid, xi, yi)
        r = int((OY - float(y)) // DX)
        k = int((float(x) - OX) // DX)
        if 0 <= r < s_raster.shape[0] and 0 <= k < s_raster.shape[1]:
            s_raster[r, k] += s[xi, yi, 0]
    assert pytest.approx(s_raster.sum(), rel=1e-12) == s[:, :, 0].sum()
    assert float(np.sum(kansl * s_raster)) == pytest.approx(c_fram * medel_f, rel=1e-8)
