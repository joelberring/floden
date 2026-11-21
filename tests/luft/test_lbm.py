# SPDX-License-Identifier: MIT
"""Tester för D3Q19 LBM-LES (testgrupp L1–L3 i `docs/LUFT.md` 6, plus felfall, vegetation,
randvillkor, väggmodell, syntetisk inflödesturbulens och Mach-val (uppdrag L-d).

Alla tester körs på CPU med NumPy. Mätvärden skrivs ut (syns med `pytest -s`).
"""

from __future__ import annotations

import math
import time
import unittest

import numpy as np

from luft.lbm import (
    C_D3Q19,
    Lbm3d,
    LbmParametrar,
    SyntetiskaVirvlar,
    _rullplan,
    kor_les,
    valj_u_lattice,
)
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

    def test_kanalflode_tau_skilt_fran_ett(self) -> None:
        """Kraften ska ge rätt rörelsemängd även för τ ≠ 1 (L-a gav u_max 0,66 vid τ = 0,6)."""
        nz = 11
        h = nz - 1
        tau = 0.6
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
        n_steg = int(16 * h * h / (math.pi**2 * nu))
        for _ in range(n_steg):
            losare.steg()
        _, u = losare.makro()
        z = np.arange(h) + 0.5
        u_analytisk = g / (2.0 * nu) * z * (h - z)
        u_num = np.asarray(u[0, 0, 0, :h], dtype=np.float64)
        fel = float(np.linalg.norm(u_num - u_analytisk) / np.linalg.norm(u_analytisk))
        print(f"\nL1 Poiseuille tau=0,6: rel L2-fel {fel:.2e} efter {n_steg} steg")
        self.assertLess(fel, 0.02)


class TestKollision(unittest.TestCase):
    """Kollisionen i momentrummet mot den direkta formuleringen (känt svar)."""

    def _slumpfalt(self, losare: Lbm3d, seed: int) -> None:
        rng = np.random.default_rng(seed)
        nx, ny, nz = losare.form
        u0 = 0.03 * rng.standard_normal((3, nx, ny, nz))
        losare.initiera(u0)
        losare.f += (1e-3 * rng.standard_normal(losare.f.shape)).astype(np.float32)

    def test_utan_kraft_som_reguljariserad_bgk(self) -> None:
        fast = np.zeros((4, 3, 3), dtype=np.bool_)
        losare = Lbm3d(fast, tau0=0.55, smagorinsky_cs=0.0, xp=np, x_periodisk=True)
        self._slumpfalt(losare, 2)
        f = losare.f.reshape(19, -1).astype(np.float64)
        drho = f.sum(axis=0)
        rho = 1.0 + drho
        u = (C_D3Q19.T.astype(np.float64) @ f) / rho
        feq = np.asarray(losare.jamvikt(drho, u), dtype=np.float64)
        pi = np.asarray(losare.h6, dtype=np.float64) @ (f - feq)
        fneq = np.asarray(losare.r6, dtype=np.float64) @ pi
        facit = feq + (1.0 - 1.0 / 0.55) * fneq
        ut = np.asarray(losare._kollidera(), dtype=np.float64).reshape(19, -1)
        self.assertLess(float(np.max(np.abs(ut - facit))), 1e-6)

    def test_kraft_ger_exakt_rorelsemangdsokning(self) -> None:
        fast = np.zeros((4, 3, 3), dtype=np.bool_)
        g = (2e-4, -1e-4, 5e-5)
        losare = Lbm3d(
            fast, tau0=0.52, smagorinsky_cs=0.0, xp=np, acceleration_lat=g, x_periodisk=True
        )
        self._slumpfalt(losare, 3)
        f = losare.f.reshape(19, -1).astype(np.float64)
        rho = 1.0 + f.sum(axis=0)
        j_fore = C_D3Q19.T.astype(np.float64) @ f
        ut = np.asarray(losare._kollidera(), dtype=np.float64).reshape(19, -1)
        j_efter = C_D3Q19.T.astype(np.float64) @ ut
        okning = j_efter - j_fore
        facit = rho[None, :] * np.asarray(g)[:, None]
        self.assertLess(float(np.max(np.abs(okning - facit))), 1e-7)
        self.assertLess(float(np.max(np.abs(ut.sum(axis=0) - f.sum(axis=0)))), 1e-6)

    def test_stromningsplan_som_roll(self) -> None:
        rng = np.random.default_rng(0)
        a = rng.standard_normal((5, 4, 3))
        for c in C_D3Q19:
            skift = tuple(int(v) for v in c)
            ut = np.empty_like(a)
            for mal, kalla in _rullplan(skift):
                ut[mal] = a[kalla]
            self.assertTrue(np.array_equal(ut, np.roll(a, skift, axis=(0, 1, 2))))


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

    def test_massa_konstant_med_vaggmodell(self) -> None:
        """Spegelreflektionen (och bounce-back i inåtgående hörn) ska bevara massan."""
        nx, ny, nz = 24, 16, 12
        fast = np.zeros((nx, ny, nz), dtype=np.bool_)
        fast[8:12, 6:10, :5] = True
        fast[16:18, 2:4, :3] = True
        losare = Lbm3d(
            fast,
            tau0=0.51,
            smagorinsky_cs=0.16,
            xp=np,
            x_periodisk=True,
            vaggmodell="loglag",
            z0_vagg_celler=0.1,
        )
        rng = np.random.default_rng(1)
        u0 = 0.02 * rng.standard_normal((3, nx, ny, nz))
        u0[0] += 0.05
        losare.initiera(u0)
        m0 = losare.total_massa()
        for _ in range(500):
            losare.steg()
        drift = abs(losare.total_massa() - m0) / m0
        print(
            f"\nL2 massa med väggmodell: rel drift {drift:.2e}, {losare.n_vagg} väggceller, "
            f"{losare.n_vagg_bounceback} hörnceller med bounce-back"
        )
        self.assertTrue(losare.ar_andlig())
        self.assertLess(drift, 1e-6)
        self.assertGreater(losare.n_vagg, 0)
        self.assertGreater(losare.n_vagg_bounceback, 0)


# --------------------------------------------------------------------------------------
# L3 Kub i gränsskikt
# --------------------------------------------------------------------------------------


class TestL3Kub(unittest.TestCase):
    def test_atercirkulation_bakom_kub(self) -> None:
        grid, (i0, j0, h) = _kub_i_gransskikt()
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=float(h), z0_m=0.1)
        param = LbmParametrar(
            u_lattice="auto", upprampning_genomstromningar=1.0, medel_genomstromningar=1.0
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
        self.assertAlmostEqual(meta["dt_s"], 1.0 * meta["u_lattice"] / 5.0)
        self.assertAlmostEqual(vind.medelperiod_s, meta["steg_medel"] * meta["dt_s"])
        # Byighet: standardavvikelse för |u_h|
        u_std = vind.u_std_m_s
        assert u_std is not None
        self.assertEqual(u_std.shape, grid.form)
        self.assertTrue(np.all(u_std >= 0.0))
        self.assertTrue(np.all(u_std[grid.fast] == 0.0))
        std_bakom = float(u_std[i0 + h : i0 + 2 * h, j0 : j0 + h, : h + 2].mean())
        print(f"L3 kub: medel std(|u_h|) bakom kuben {std_bakom:.3f} m/s")
        self.assertGreater(std_bakom, 0.0)


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
            u_lattice=0.06, upprampning_genomstromningar=1.0, medel_genomstromningar=0.5
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


# --------------------------------------------------------------------------------------
# Randvillkor (L-d)
# --------------------------------------------------------------------------------------


# Rekommenderade randvillkor (L-d); standard är L-a:s för bakåtkompatibilitet.
REKOMMENDERAD_RAND: dict[str, str] = {
    "inflodesrand": "extrapolation",
    "utflodesrand": "tryck",
    "start": "profil",
}


def _tom_doman(nx: int, ny: int, nz: int) -> Voxelgrid:
    fast = np.zeros((nx, ny, nz), dtype=np.bool_)
    return Voxelgrid(dx_m=1.0, fast=fast, lad_m2_m3=np.zeros(fast.shape))


class TestRandvillkor(unittest.TestCase):
    def test_inflode_ger_ratt_flode(self) -> None:
        """Volymflödet genom domänen ska vara inflödesprofilens (massbevarande med ρ ≈ 1).

        L-a:s jämviktsinflöde från vila gav 0,67–0,73 av profilen en cell in.
        """
        grid = _tom_doman(48, 8, 12)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=6.0, z0_m=0.1)
        param = LbmParametrar(
            upprampning_genomstromningar=1.0, medel_genomstromningar=1.0, **REKOMMENDERAD_RAND
        )
        vind = kor_les(grid, inflode, param)
        flode_in = float(np.sum(inflode.profil_m_s(grid.cellcentrum_z_m())))
        kvoter = [float(vind.u_m_s[0, i].mean(axis=0).sum()) / flode_in for i in (1, 24, 46)]
        print(f"\nRand: flöde/inflödesprofilens flöde vid i = 1, 24, 46: {np.round(kvoter, 4)}")
        for k in kvoter:
            self.assertAlmostEqual(k, 1.0, delta=0.02)

    def test_standard_ger_ratt_massflode(self) -> None:
        """Standard sedan L-d: extrapolerat inflöde, tryckutflöde, profilstart → rätt flöde."""
        self.assertEqual(LbmParametrar().inflodesrand, "extrapolation")
        self.assertEqual(LbmParametrar().utflodesrand, "tryck")
        self.assertEqual(LbmParametrar().start, "profil")
        grid = _tom_doman(48, 8, 12)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=6.0, z0_m=0.1)
        param = LbmParametrar(upprampning_genomstromningar=1.0, medel_genomstromningar=1.0)
        vind = kor_les(grid, inflode, param)
        self.assertTrue(np.all(np.isfinite(vind.u_m_s)))
        flode_in = float(np.sum(inflode.profil_m_s(grid.cellcentrum_z_m())))
        kvot = float(vind.u_m_s[0, 24].mean(axis=0).sum()) / flode_in
        print(f"\nStandardrand: flöde/profilens flöde vid i = 24: {kvot:.3f}")
        self.assertGreater(kvot, 0.95)
        with self.assertRaises(LuftFel):
            LbmParametrar(inflodesrand="okand")
        with self.assertRaises(LuftFel):
            LbmParametrar(start="okand")


# --------------------------------------------------------------------------------------
# Väggmodell (L-d)
# --------------------------------------------------------------------------------------


class TestVaggmodell(unittest.TestCase):
    def test_gransskikt_haller_logprofilen_battre(self) -> None:
        """Log-profil in över plan mark (grovt rutnät, 12 celler högt, z0 = 0,1 cell).

        Facit är inflödets egen log-profil: med mark vars råhet är konsistent med profilen
        ska den bevaras medvinds (jfr Blocken, Stathopoulos & Carmeliet 2007,
        Atmos. Environ. 41:238–252, om horisontellt homogent gränsskikt). Mätt fel
        (rel. L2 för y-medelprofilen vid x = 3/4·nx): utan väggmodell 0,173, med 0,085.
        """
        grid = _tom_doman(48, 12, 12)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=6.0, z0_m=0.1, turbulensintensitet=0.1)
        logp = inflode.profil_m_s(grid.cellcentrum_z_m())
        fel: dict[str, float] = {}
        for vm in ("ingen", "loglag"):
            param = LbmParametrar(
                upprampning_genomstromningar=2.0,
                medel_genomstromningar=2.0,
                vaggmodell=vm,
                inflodesturbulens="virvlar",
                inflode_langdskala_m=3.0,
                **REKOMMENDERAD_RAND,
            )
            vind = kor_les(grid, inflode, param)
            prof = vind.u_m_s[0, 36:38].mean(axis=(0, 1))
            fel[vm] = float(np.linalg.norm(prof - logp) / np.linalg.norm(logp))
            self.assertEqual(vind.metadata["vaggmodell"], vm)
        print(
            f"\nVäggmodell: rel L2-fel mot log-profil utan {fel['ingen']:.3f}, "
            f"med {fel['loglag']:.3f}"
        )
        self.assertLess(fel["loglag"], 0.7 * fel["ingen"])
        self.assertLess(fel["loglag"], 0.12)

    def test_kraver_z0(self) -> None:
        with self.assertRaises(LuftFel):
            Lbm3d(np.zeros((4, 4, 4), dtype=np.bool_), 0.6, 0.0, np, vaggmodell="loglag")
        with self.assertRaises(LuftFel):
            LbmParametrar(vaggmodell="vagg")
        with self.assertRaises(LuftFel):
            LbmParametrar(z0_vagg_m=0.0)


# --------------------------------------------------------------------------------------
# Syntetisk inflödesturbulens (L-d)
# --------------------------------------------------------------------------------------


class TestVirvlar(unittest.TestCase):
    def test_signalens_statistik(self) -> None:
        """SEM-signalen ska ha medel 0 och varians 1 på alla höjder (Jarrin m.fl. 2006)."""
        ny, nz = 32, 24
        z = np.arange(nz) + 0.5
        hast = 0.08 * np.log((z + 0.1) / 0.1) / np.log(8.1 / 0.1)
        gen = SyntetiskaVirvlar(ny, nz, hast, langdskala_celler=3.0, seed=3)
        n = 4000
        summa = np.zeros((3, ny, nz))
        kvadrat = np.zeros((3, ny, nz))
        for _ in range(n):
            s = gen.nasta()
            summa += s
            kvadrat += s * s
        medel = summa / n
        rms = np.sqrt(kvadrat / n - medel**2)
        rms_z = np.sqrt((rms**2).mean(axis=1))  # (3, nz)
        print(
            f"\nSEM: {gen.n_virvlar} virvlar, |medel| {np.abs(medel).mean():.3f}, "
            f"RMS {np.sqrt((rms**2).mean()):.3f}, RMS per höjd {rms_z.min():.3f}–{rms_z.max():.3f}"
        )
        self.assertLess(float(np.abs(medel.mean(axis=(1, 2))).max()), 0.05)
        self.assertAlmostEqual(float(np.sqrt((rms**2).mean())), 1.0, delta=0.15)
        self.assertTrue(np.all(np.abs(rms_z - 1.0) < 0.15))
        # deterministisk
        a = SyntetiskaVirvlar(ny, nz, hast, 3.0, seed=5)
        b = SyntetiskaVirvlar(ny, nz, hast, 3.0, seed=5)
        for _ in range(10):
            self.assertTrue(np.array_equal(a.nasta(), b.nasta()))

    def test_inflodesplanet_i_kor_les(self) -> None:
        """Medel ≈ profil och std(|u_h|) ≈ I·U i inflödesplanet, inom 15 %."""
        grid = _tom_doman(32, 16, 12)
        intensitet = 0.15
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=6.0, z0_m=0.1, turbulensintensitet=intensitet)
        param = LbmParametrar(
            u_lattice="auto",
            upprampning_genomstromningar=0.2,
            medel_genomstromningar=2.0,
            medel_intervall_steg=2,
            inflodesturbulens="virvlar",
            inflode_langdskala_m=3.0,
            **REKOMMENDERAD_RAND,
        )
        vind = kor_les(grid, inflode, param)
        u_prof = inflode.profil_m_s(grid.cellcentrum_z_m())
        medel = vind.u_m_s[0, 0].mean(axis=0) / u_prof
        u_std = vind.u_std_m_s
        assert u_std is not None
        kvot_std = float(u_std[0].mean() / (intensitet * u_prof).mean())
        print(
            f"\nSEM i kor_les: medel/profil {medel.min():.3f}–{medel.max():.3f}, "
            f"std(|u_h|)/(I·U) {kvot_std:.3f}, max Mach {vind.metadata['max_mach']:.3f}, "
            f"u_lattice {vind.metadata['u_lattice']:.4f}"
        )
        self.assertTrue(np.all(np.abs(medel - 1.0) < 0.05))
        self.assertAlmostEqual(kvot_std, 1.0, delta=0.15)
        self.assertEqual(vind.metadata["inflodesturbulens"], "virvlar")

    def test_virvlar_kraver_langdskala(self) -> None:
        grid = _tom_doman(16, 8, 8)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=4.0, z0_m=0.1, turbulensintensitet=0.1)
        with self.assertRaises(LuftFel):
            kor_les(grid, inflode, LbmParametrar(inflodesturbulens="virvlar"))
        with self.assertRaises(LuftFel):
            SyntetiskaVirvlar(8, 8, np.ones(8), langdskala_celler=0.5, seed=0)
        with self.assertRaises(LuftFel):
            SyntetiskaVirvlar(8, 8, np.ones(8), langdskala_celler=6.0, seed=0)


# --------------------------------------------------------------------------------------
# Mach-säkerhet (L-d)
# --------------------------------------------------------------------------------------


class TestMach(unittest.TestCase):
    def test_valj_u_lattice(self) -> None:
        grid, _ = _kub_i_gransskikt(nx=32, ny=16, nz=16, h=4)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=4.0, z0_m=0.1)
        ul = valj_u_lattice(grid, inflode, mach_mal=0.15, faktor=2.5)
        kvot = float(np.max(inflode.profil_m_s(grid.cellcentrum_z_m()))) / 5.0
        self.assertAlmostEqual(ul * kvot * 2.5 * math.sqrt(3.0), 0.15, places=12)
        self.assertLessEqual(valj_u_lattice(grid, inflode, 0.3, 1.0), 0.1)

    def test_auto_haller_machmalet(self) -> None:
        grid, _ = _kub_i_gransskikt(nx=32, ny=16, nz=16, h=4)
        inflode = Inflode(u_ref_m_s=5.0, z_ref_m=4.0, z0_m=0.1)
        param = LbmParametrar(
            u_lattice="auto",
            upprampning_genomstromningar=1.0,
            medel_genomstromningar=0.5,
            **REKOMMENDERAD_RAND,
        )
        vind = kor_les(grid, inflode, param)
        meta = vind.metadata
        print(f"\nMach auto: u_lattice {meta['u_lattice']:.4f}, max Mach {meta['max_mach']:.3f}")
        self.assertLess(meta["max_mach"], param.mach_mal)
        self.assertAlmostEqual(meta["dt_s"], meta["u_lattice"] / 5.0)
        with self.assertRaises(LuftFel):
            LbmParametrar(u_lattice="snabb")


if __name__ == "__main__":
    unittest.main()
