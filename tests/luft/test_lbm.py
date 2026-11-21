# SPDX-License-Identifier: MIT
"""Tester för D3Q19 LBM-LES (testgrupp L1–L3 i `docs/LUFT.md` 6, plus felfall och vegetation).

Alla tester körs på CPU med NumPy. Mätvärden skrivs ut (syns med `pytest -s`).
"""

from __future__ import annotations

import math
import time
import unittest

import numpy as np

from luft.lbm import Lbm3d, LbmParametrar, kor_les
from luft.typer import Inflode, LuftFel, Vindfalt, Voxelgrid

# --------------------------------------------------------------------------------------
# Testgeometrier
# --------------------------------------------------------------------------------------


def _kanal(nz: int) -> np.ndarray:
    """Kanal mellan två plattor: marken under k = 0 och en fast platta i k = nz-1."""
    fast = np.zeros((4, 4, nz), dtype=np.bool_)
    fast[:, :, -1] = True
    return fast


def _kub_i_gransskikt(
    nx: int = 64, ny: int = 32, nz: int = 24, h: int = 8
) -> tuple[Voxelgrid, tuple[int, int, int]]:
    """Kub med sidan h celler på marken, en fjärdedel in i domänen, centrerad i y."""
    fast = np.zeros((nx, ny, nz), dtype=np.bool_)
    i0 = nx // 4
    j0 = ny // 2 - h // 2
    fast[i0 : i0 + h, j0 : j0 + h, :h] = True
    grid = Voxelgrid(dx_m=1.0, fast=fast, lad_m2_m3=np.zeros(fast.shape))
    return grid, (i0, j0, h)


def _vegetationsfall(lad: float) -> tuple[Voxelgrid, tuple[slice, slice, slice]]:
    nx, ny, nz = 48, 16, 16
    fast = np.zeros((nx, ny, nz), dtype=np.bool_)
    lad_f = np.zeros((nx, ny, nz))
    zon = (slice(16, 32), slice(None), slice(0, 6))
    lad_f[zon] = lad
    return Voxelgrid(dx_m=1.0, fast=fast, lad_m2_m3=lad_f), zon


# --------------------------------------------------------------------------------------
# L1 Poiseuille
# --------------------------------------------------------------------------------------


class TestL1Poiseuille(unittest.TestCase):
    def test_kanalflode_mot_analytisk_parabel(self) -> None:
        nz = 21
        h = nz - 1  # väggar halvvägs vid z = 0 och z = h
        tau = 1.0
        nu = (tau - 0.5) / 3.0
        u_max = 0.05
        g = 8.0 * nu * u_max / h**2
        losare = Lbm3d(
            _kanal(nz),
            tau0=tau,
            smagorinsky_cs=0.0,
            xp=np,
            acceleration_lat=(g, 0.0, 0.0),
            x_periodisk=True,
        )
        t0 = time.perf_counter()
        n_steg = 4000  # ≈ 16 e-faldningstider h²/(π² ν)
        for _ in range(n_steg):
            losare.steg()
        tid = time.perf_counter() - t0
        _, u = losare.makro()
        z = np.arange(h) + 0.5
        u_analytisk = g / (2.0 * nu) * z * (h - z)
        u_num = np.asarray(u[0, :, :, :h], dtype=np.float64)
        fel = float(
            np.linalg.norm(u_num - u_analytisk[None, None, :])
            / np.linalg.norm(np.broadcast_to(u_analytisk, u_num.shape))
        )
        print(f"\nL1 Poiseuille: rel L2-fel {fel:.2e}, {n_steg} steg på {tid:.2f} s")
        self.assertLess(fel, 0.02)
        # Ingen tvärströmning
        self.assertLess(float(np.max(np.abs(u[1:]))), 1e-6)


# --------------------------------------------------------------------------------------
# L2 Massbevarande
# --------------------------------------------------------------------------------------


class TestL2Massa(unittest.TestCase):
    def test_massa_konstant_i_periodisk_lada(self) -> None:
        nx, ny, nz = 24, 16, 12
        fast = np.zeros((nx, ny, nz), dtype=np.bool_)
        fast[8:12, 6:10, :5] = True  # hinder (bounce-back) + mark + speglande tak
        losare = Lbm3d(fast, tau0=0.51, smagorinsky_cs=0.16, xp=np, x_periodisk=True)
        rng = np.random.default_rng(1)
        u0 = 0.02 * rng.standard_normal((3, nx, ny, nz))
        u0[0] += 0.05
        losare.initiera(u0)
        m0 = losare.total_massa()
        n_steg = 500
        for _ in range(n_steg):
            losare.steg()
        drift = abs(losare.total_massa() - m0) / m0
        print(f"\nL2 massa: rel drift {drift:.2e} efter {n_steg} steg")
        self.assertTrue(losare.ar_andlig())
        self.assertLess(drift, 1e-6)


# --------------------------------------------------------------------------------------
# L3 Kub i gränsskikt
# --------------------------------------------------------------------------------------


class TestL3Kub(unittest.TestCase):
    def test_atercirkulation_bakom_kub(self) -> None:
        grid, (i0, j0, h) = _kub_i_gransskikt()
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=float(h), z0_m=0.1)
        param = LbmParametrar(
            u_lattice=0.1, upprampning_genomstromningar=1.0, medel_genomstromningar=1.0
        )
        meddelanden: list[str] = []
        t0 = time.perf_counter()
        vind = kor_les(grid, inflode, param, logg=meddelanden.append)
        tid = time.perf_counter() - t0
        self.assertIsInstance(vind, Vindfalt)
        self.assertTrue(np.all(np.isfinite(vind.u_m_s)))
        self.assertTrue(np.all(vind.u_m_s[:, grid.fast] == 0.0))
        self.assertTrue(np.all(vind.nu_t_m2_s[grid.fast] == 0.0))
        self.assertTrue(len(meddelanden) > 0)
        k = h // 2
        ux = vind.u_m_s[0]
        bakom = ux[i0 + h : i0 + 2 * h, j0 : j0 + h, k]  # en kubhöjd medvinds, halva höjden
        direkt = ux[i0 + h, j0 : j0 + h, k]
        meta = vind.metadata
        print(
            f"\nL3 kub: {meta['steg_totalt']} steg på {tid:.1f} s "
            f"({meta['steg_totalt'] / tid:.0f} steg/s, {meta['mlups']:.2f} MLUPS), "
            f"min u_x bakom kub {bakom.min():.2f} m/s, direkt bakom {direkt.min():.2f} m/s, "
            f"max Mach {meta['max_mach']:.3f}, tau_min {meta['tau_min']:.6f}"
        )
        self.assertLess(float(bakom.min()), 0.0)
        # Ovanför gränsskiktet ska vinden vara positiv och av rätt storlek.
        self.assertGreater(float(ux[i0 - 4, grid.form[1] // 2, -1]), 0.8 * inflode.u_ref_m_s)
        for nyckel in ("steg_totalt", "dt_s", "tau_min", "max_mach", "divergerat"):
            self.assertIn(nyckel, meta)
        self.assertFalse(meta["divergerat"])
        self.assertAlmostEqual(meta["dt_s"], 1.0 * 0.1 / 5.0)
        self.assertAlmostEqual(vind.medelperiod_s, meta["steg_medel"] * meta["dt_s"])


# --------------------------------------------------------------------------------------
# Felfall
# --------------------------------------------------------------------------------------


class TestFel(unittest.TestCase):
    def test_for_hog_u_lattice(self) -> None:
        with self.assertRaises(LuftFel):
            LbmParametrar(u_lattice=0.2)

    def test_inflodesprofil_over_machgrans(self) -> None:
        # u_lattice = 0,15 är under gränsen vid z_ref men profilen når över Mach 0,3 högre upp.
        grid, _ = _kub_i_gransskikt(nx=16, ny=8, nz=60, h=2)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=2.0, z0_m=0.1)
        with self.assertRaises(LuftFel):
            kor_les(grid, inflode, LbmParametrar(u_lattice=0.15))

    def test_for_manga_steg(self) -> None:
        grid, _ = _kub_i_gransskikt(nx=16, ny=8, nz=8, h=2)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=2.0, z0_m=0.1)
        with self.assertRaises(LuftFel):
            kor_les(grid, inflode, LbmParametrar(max_steg=10))

    def test_divergens_ger_luftfel(self) -> None:
        grid, _ = _kub_i_gransskikt(nx=16, ny=8, nz=8, h=2)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=2.0, z0_m=0.1)
        param = LbmParametrar(
            upprampning_genomstromningar=0.1,
            medel_genomstromningar=0.1,
            kontroll_intervall_steg=1,
        )
        original = Lbm3d.steg

        def forgifta(self: Lbm3d) -> None:
            original(self)
            self.f[:, 3, 3, 3] = np.nan

        Lbm3d.steg = forgifta  # type: ignore[method-assign]
        try:
            with self.assertRaises(LuftFel):
                kor_les(grid, inflode, param)
        finally:
            Lbm3d.steg = original  # type: ignore[method-assign]


# --------------------------------------------------------------------------------------
# Vegetation och inflödesstörning
# --------------------------------------------------------------------------------------


class TestVegetation(unittest.TestCase):
    def test_vegetation_bromsar_vinden(self) -> None:
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=6.0, z0_m=0.1)
        param = LbmParametrar(
            u_lattice=0.1, upprampning_genomstromningar=1.0, medel_genomstromningar=0.5
        )
        utan_grid, zon = _vegetationsfall(0.0)
        med_grid, _ = _vegetationsfall(1.0)
        utan = kor_les(utan_grid, inflode, param)
        med = kor_les(med_grid, inflode, param)
        u_utan = float(utan.u_m_s[0][zon].mean())
        u_med = float(med.u_m_s[0][zon].mean())
        print(f"\nVegetation: medel u_x i zonen {u_med:.2f} m/s med, {u_utan:.2f} m/s utan")
        self.assertLess(u_med, 0.8 * u_utan)
        self.assertGreater(u_med, 0.0)


class TestStorning(unittest.TestCase):
    def test_storning_ar_deterministisk_och_stabil(self) -> None:
        grid, _ = _kub_i_gransskikt(nx=32, ny=16, nz=12, h=4)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=4.0, z0_m=0.1, turbulensintensitet=0.1)
        param = LbmParametrar(
            u_lattice=0.06, upprampning_genomstromningar=0.5, medel_genomstromningar=0.5
        )
        a = kor_les(grid, inflode, param)
        b = kor_les(grid, inflode, param)
        self.assertTrue(np.array_equal(a.u_m_s, b.u_m_s))
        self.assertTrue(math.isfinite(float(a.nu_t_m2_s.max())))
        self.assertGreater(float(a.nu_t_m2_s.max()), 0.0)


if __name__ == "__main__":
    unittest.main()
