"""Horisontprofiler: analytiska fall, referens mot snabba varianter och felhantering."""

from __future__ import annotations

import math
import unittest

import numpy as np

from dagsljus.horisont import (
    Hojdfalt,
    HorisontFel,
    horisont_mipmap,
    horisont_referens,
    horisont_riktning_ruta,
    riktningar_rad,
    stralsekvens,
)

from .geometri import plan


def _slumpfalt(seed: int = 1, ny: int = 90, nx: int = 110, cell_m: float = 1.0) -> Hojdfalt:
    rng = np.random.default_rng(seed)
    z = rng.uniform(0.0, 3.0, (ny, nx))
    for _ in range(25):
        i, j = int(rng.integers(0, ny - 10)), int(rng.integers(0, nx - 10))
        z[i : i + int(rng.integers(3, 9)), j : j + int(rng.integers(3, 9))] += rng.uniform(5, 30)
    return Hojdfalt(z, cell_m, 1000.0, 2000.0)


class TestFriPlan(unittest.TestCase):
    def test_fri_plan_har_horisont_noll_i_alla_varianter(self) -> None:
        f = plan(60, 1.0)
        x = np.array([30.5, 10.2, 45.9])
        y = np.array([29.5, 40.1, 12.3])
        phi = np.array([0.3, 2.0, 5.0])
        for fn in (horisont_referens, horisont_mipmap):
            r = fn(f, x, y, np.zeros(3), phi, 25.0)
            self.assertEqual(float(r.h_rad.max()), 0.0)
        h, _ = horisont_riktning_ruta(f, 1.0, 25.0)
        self.assertEqual(float(h.max()), 0.0)


class TestVagg(unittest.TestCase):
    def test_oandlig_vagg_ger_atan_h_cos_psi_over_d(self) -> None:
        """Vägg (höjd H) vars yta ligger vid avstånd d i riktning normalen (öster). En stråle
        med vinkel psi från normalen träffar väggen på s = d / cos psi: h = atan(H cos psi / d).
        Väggytan ligger på en cellgräns, så modellen ska vara exakt (maxfel < 1e-12 rad)."""
        for cell_m in (2.0, 1.0, 0.5):
            n = int(round(400 / cell_m))
            z = np.zeros((n, n))
            pj = n // 2
            gransj = pj + 20  # vägg från kolumn pj+20, höjd 20 m
            z[:, gransj:] = 20.0
            f = Hojdfalt(z, cell_m, 0.0, n * cell_m)
            d = (gransj - (pj + 0.5)) * cell_m
            x = np.array([(pj + 0.5) * cell_m])
            y = np.array([f.y_max_m - (n // 2 + 0.5) * cell_m])
            psi = np.radians(np.arange(-75, 76, 5))
            fel = []
            for p in psi:
                r = horisont_mipmap(f, x, y, np.zeros(1), np.array([math.pi / 2 + p]), 250.0)
                fel.append(abs(float(r.h_rad[0]) - math.atan(20.0 * math.cos(p) / d)))
            self.assertLess(max(fel), 1e-12, msg=f"cell {cell_m}")

    def test_vagg_bakom_punkten_syns_inte(self) -> None:
        n = 200
        z = np.zeros((n, n))
        z[:, 120:] = 20.0
        f = Hojdfalt(z, 1.0, 0.0, float(n))
        r = horisont_referens(
            f, np.array([100.5]), np.array([100.5]), np.zeros(1), np.array([3 * math.pi / 2]), 90.0
        )
        self.assertEqual(float(r.h_rad[0]), 0.0)

    def test_hojd_over_z0_raknas_fran_z0(self) -> None:
        n = 100
        z = np.zeros((n, n))
        z[:, 60:] = 20.0
        f = Hojdfalt(z, 1.0, 0.0, float(n))
        r = horisont_referens(
            f, np.array([40.5]), np.array([50.5]), np.array([10.0]), np.array([math.pi / 2]), 50.0
        )
        self.assertAlmostEqual(float(r.h_rad[0]), math.atan(10.0 / 19.5), places=12)


class TestReferensMotSnabba(unittest.TestCase):
    def test_ruta_mot_referens_i_cellmittpunkter(self) -> None:
        f = _slumpfalt()
        rng = np.random.default_rng(5)
        ii = rng.integers(0, f.hojd, 300)
        jj = rng.integers(0, f.bredd, 300)
        x = f.x_min_m + (jj + 0.5)
        y = f.y_max_m - (ii + 0.5)
        for phi in riktningar_rad(32)[[0, 1, 4, 8, 11, 16, 20, 29]]:
            h, trunk = horisont_riktning_ruta(f, float(phi), 60.0, dtyp=np.float64)
            ref = horisont_referens(f, x, y, f.z_m[ii, jj], np.full(300, phi), 60.0)
            self.assertLess(float(np.abs(h[ii, jj] - ref.h_rad).max()), 1e-6)  # float32-utdata
            np.testing.assert_array_equal(trunk[ii, jj], ref.trunkerad)

    def test_fonster_ger_samma_som_helfalt(self) -> None:
        f = _slumpfalt(seed=3)
        full, ft = horisont_riktning_ruta(f, 0.7, 50.0)
        fon, tt = horisont_riktning_ruta(f, 0.7, 50.0, fonster=(20, 70, 30, 95))
        np.testing.assert_array_equal(full[20:70, 30:95], fon)
        np.testing.assert_array_equal(ft[20:70, 30:95], tt)

    def test_mipmap_mot_referens_slumpmassiga_strålar(self) -> None:
        f = _slumpfalt(seed=2)
        rng = np.random.default_rng(9)
        n = 4000
        x = rng.uniform(f.x_min_m + 0.1, f.x_min_m + f.bredd - 0.1, n)
        y = rng.uniform(f.y_max_m - f.hojd + 0.1, f.y_max_m - 0.1, n)
        phi = rng.uniform(0, 2 * math.pi, n)
        oi = np.floor(f.y_max_m - y).astype(int)
        oj = np.floor(x - f.x_min_m).astype(int)
        z0 = f.z_m[oi, oj] + rng.uniform(0.0, 2.0, n)
        ref = horisont_referens(f, x, y, z0, phi, 70.0)
        mm = horisont_mipmap(f, x, y, z0, phi, 70.0)
        self.assertLess(float(np.abs(ref.h_rad - mm.h_rad).max()), 1e-9)
        np.testing.assert_array_equal(ref.trunkerad, mm.trunkerad)
        np.testing.assert_array_equal(ref.inuti_hinder, mm.inuti_hinder)

    def test_mipmap_tar_axelparallella_och_diagonala_strålar(self) -> None:
        f = _slumpfalt(seed=4)
        jj, ii = np.meshgrid(np.arange(10, 100, 7), np.arange(10, 80, 7))
        x = f.x_min_m + (jj.ravel() + 0.5)
        y = f.y_max_m - (ii.ravel() + 0.5)
        z0 = f.z_m[ii.ravel(), jj.ravel()]
        for phi in riktningar_rad(16):  # inkluderar 0, pi/2, pi, 3pi/2 och diagonalerna
            p = np.full(x.size, phi)
            ref = horisont_referens(f, x, y, z0, p, 60.0)
            mm = horisont_mipmap(f, x, y, z0, p, 60.0)
            self.assertLess(float(np.abs(ref.h_rad - mm.h_rad).max()), 1e-9, msg=f"phi={phi}")

    def test_stralsekvens_diagonal_hoppar_over_hornceller(self) -> None:
        dj, di, t = stralsekvens(math.pi / 4, 4.0)
        # Diagonalen NO från en cellmittpunkt: (1,-1), (2,-2), ...; ingång 0,5*sqrt(2) + n*sqrt(2).
        np.testing.assert_array_equal(dj[:3], [1, 2, 3])
        np.testing.assert_array_equal(di[:3], [-1, -2, -3])
        self.assertAlmostEqual(float(t[0]), 0.5 * math.sqrt(2), places=9)


class TestFelhantering(unittest.TestCase):
    def test_nan_i_hojdfaltet_ger_fel(self) -> None:
        z = np.zeros((10, 10))
        z[3, 3] = np.nan
        with self.assertRaises(HorisontFel):
            Hojdfalt(z, 1.0, 0.0, 10.0)

    def test_punkt_utanfor_faltet_ger_fel(self) -> None:
        f = plan(10, 1.0)
        with self.assertRaises(HorisontFel):
            horisont_referens(f, np.array([11.0]), np.array([5.0]), np.zeros(1), np.zeros(1), 5.0)
        with self.assertRaises(HorisontFel):
            horisont_mipmap(f, np.array([5.0]), np.array([-1.0]), np.zeros(1), np.zeros(1), 5.0)

    def test_ogiltig_avstand_och_antal(self) -> None:
        f = plan(10, 1.0)
        with self.assertRaises(HorisontFel):
            horisont_riktning_ruta(f, 0.0, 0.0)
        with self.assertRaises(HorisontFel):
            riktningar_rad(2)
        with self.assertRaises(HorisontFel):
            horisont_riktning_ruta(f, 0.0, 5.0, fonster=(0, 11, 0, 5))

    def test_trunkering_flaggas(self) -> None:
        f = plan(50, 1.0)
        r = horisont_referens(
            f,
            np.array([25.5, 25.5]),
            np.array([25.5, 25.5]),
            np.zeros(2),
            np.array([math.pi / 2, math.pi / 2]),
            30.0,
        )
        self.assertTrue(r.trunkerad.all())  # 24,5 m till kanten < 30 m
        r2 = horisont_referens(
            f, np.array([25.5]), np.array([25.5]), np.zeros(1), np.array([math.pi / 2]), 20.0
        )
        self.assertFalse(r2.trunkerad.any())

    def test_punkt_inuti_hinder_flaggas(self) -> None:
        z = np.zeros((20, 20))
        z[5:15, 5:15] = 10.0
        f = Hojdfalt(z, 1.0, 0.0, 20.0)
        r = horisont_referens(
            f, np.array([10.5, 2.5]), np.array([10.5, 2.5]), np.zeros(2), np.zeros(2), 10.0
        )
        self.assertEqual(list(r.inuti_hinder), [True, False])


if __name__ == "__main__":
    unittest.main()
