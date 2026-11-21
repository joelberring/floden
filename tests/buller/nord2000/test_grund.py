"""Tester för grundfunktionerna i Nord2000-kärnan: frekvenser, luftabsorption, mark, koherens."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.akustik.faddeeva import faddeeva
from buller.akustik.luftabsorption import luftabsorption_db_per_m
from buller.akustik.reflektion import sfarisk_reflektion as sfarisk_reflektion_baslinje
from buller.nord2000.frekvenser import (
    exp_modifierad,
    heaviside,
    ljudhastighet_m_s,
    mittfrekvenser_hz,
)
from buller.nord2000.koherens import (
    koherens_bandmedelvarde,
    koherens_fluktuerande_refraktion,
    koherens_markrahet,
    koherens_turbulens,
    transversell_separation_m,
)
from buller.nord2000.luftabsorption import luftabsorption_db, rentonsdampning_db_per_m
from buller.nord2000.mark import (
    inkoherent_reflektion,
    markimpedans,
    planvagsreflektion,
    sfarisk_reflektion,
    slumpinfallsabsorption,
    w_funktion,
)
from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel


class TestFrekvenser(unittest.TestCase):
    def test_27_band_enligt_ekv_11(self) -> None:
        f = mittfrekvenser_hz()
        self.assertEqual(f.shape, (ANTAL_BAND,))
        self.assertAlmostEqual(float(f[0]), 10.0**1.4, places=12)
        self.assertAlmostEqual(float(f[16]), 1000.0, places=9)
        self.assertAlmostEqual(float(f[26]), 10000.0, places=8)
        # Konstant kvot 10^(1/10) mellan grannband.
        np.testing.assert_allclose(f[1:] / f[:-1], 10.0**0.1, rtol=1e-13)

    def test_nara_nominella_frekvenser(self) -> None:
        np.testing.assert_allclose(mittfrekvenser_hz(), np.array(TERSBAND_HZ), rtol=0.013)

    def test_ljudhastighet_ekv_335(self) -> None:
        self.assertAlmostEqual(ljudhastighet_m_s(-273.15), 0.0)
        self.assertAlmostEqual(ljudhastighet_m_s(15.0), 20.05 * math.sqrt(288.15), places=12)
        self.assertAlmostEqual(ljudhastighet_m_s(15.0), 340.35, delta=0.01)

    def test_exp_modifierad_ar_kontinuerlig_och_nar_noll(self) -> None:
        self.assertEqual(exp_modifierad(0.0), 1.0)
        self.assertAlmostEqual(exp_modifierad(-1.0), math.exp(-1.0))
        self.assertAlmostEqual(exp_modifierad(-1.0 - 1e-12), math.exp(-1.0), places=10)
        self.assertAlmostEqual(exp_modifierad(-1.5), 0.5 * math.exp(-1.0))
        self.assertEqual(exp_modifierad(-2.0), 0.0)
        self.assertEqual(exp_modifierad(-50.0), 0.0)

    def test_heaviside(self) -> None:
        self.assertEqual(heaviside(0.0), 1.0)
        self.assertEqual(heaviside(-1e-300), 0.0)


class TestLuftabsorption(unittest.TestCase):
    def test_rentonsdampning_mot_oberoende_iso_9613_1(self) -> None:
        """Ekv. (286) anges bygga på ISO 9613-1; B4a:s funktion är avläst ur andra källor."""
        for t_c, rf in ((15.0, 70.0), (0.0, 40.0), (30.0, 90.0), (-10.0, 80.0)):
            for f in mittfrekvenser_hz():
                egen = rentonsdampning_db_per_m(float(f), t_c, rf)
                iso = float(luftabsorption_db_per_m(float(f), t_c, rf, 101.325))
                self.assertAlmostEqual(egen / iso, 1.0, delta=2e-3, msg=f"{t_c} {rf} {f}")

    def test_bandkorrektion_ekv_287(self) -> None:
        f = mittfrekvenser_hz()
        self.assertTrue(bool(np.all(luftabsorption_db(f, 0.0, 15.0, 70.0) == 0.0)))
        varden = luftabsorption_db(f, 100.0, 15.0, 70.0)
        self.assertTrue(bool(np.all(varden < 0.0)))
        self.assertTrue(bool(np.all(np.diff(varden) < 0.0)))  # mer dämpning vid högre frekvens
        a0 = rentonsdampning_db_per_m(float(f[26]), 15.0, 70.0) * 100.0
        self.assertAlmostEqual(
            float(varden[26]), -a0 * (1.0053255 - 0.00122622 * a0) ** 1.6, places=12
        )
        # Bandvärdet är till beloppet mindre än rentonsvärdet när dämpningen är stor.
        self.assertGreater(float(varden[26]), -a0)

    def test_utanfor_formelns_omrade_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            luftabsorption_db(mittfrekvenser_hz(), 20000.0, 15.0, 70.0)
        with self.assertRaises(BullerFel):
            luftabsorption_db(mittfrekvenser_hz(), -1.0, 15.0, 70.0)
        with self.assertRaises(BullerFel):
            rentonsdampning_db_per_m(1000.0, 15.0, 120.0)


class TestMark(unittest.TestCase):
    def test_impedans_ekv_57(self) -> None:
        z = markimpedans(200.0, 200.0)  # f/σ = 1
        self.assertAlmostEqual(z.real, 10.08, places=12)
        self.assertAlmostEqual(z.imag, 11.9, places=12)
        z2 = markimpedans(1000.0, 200.0)
        self.assertAlmostEqual(z2.real, 1.0 + 9.08 * 5.0**-0.75, places=12)
        self.assertAlmostEqual(z2.imag, 11.9 * 5.0**-0.73, places=12)

    def test_impedans_kraver_giltig_resistivitet(self) -> None:
        for sigma in (0.0, -1.0, math.inf, math.nan):
            with self.assertRaises(BullerFel):
                markimpedans(1000.0, sigma)

    def test_planvagsreflektion_gransfall(self) -> None:
        z = markimpedans(500.0, 200.0)
        self.assertAlmostEqual(abs(planvagsreflektion(z, 0.0) + 1.0), 0.0, places=12)
        vinkelratt = planvagsreflektion(z, math.pi / 2.0)
        self.assertAlmostEqual(abs(vinkelratt - (z - 1.0) / (z + 1.0)), 0.0, places=12)
        self.assertLess(abs(planvagsreflektion(z, 0.3)), 1.0)

    def test_w_funktion_mot_noggrann_faddeeva(self) -> None:
        rng = np.random.default_rng(20261121)
        storsta = 0.0
        for _ in range(20000):
            z = complex(rng.uniform(-10.0, 10.0), rng.uniform(-3.0, 10.0))
            facit = complex(faddeeva(np.array([z]))[0])
            storsta = max(storsta, abs(w_funktion(z) - facit) / abs(facit))
        self.assertLess(storsta, 2e-5)

    def test_w_funktion_nara_poler_pa_reella_axeln(self) -> None:
        for z in (0.0j, 1e-9 + 1e-9j, 0.8 + 0.0j, 1.6 + 1e-10j, -2.4 - 1e-8j, 0.8 + 1.1e-3j):
            facit = complex(faddeeva(np.array([z]))[0])
            self.assertLess(abs(w_funktion(z) - facit), 1e-6, msg=str(z))
        self.assertAlmostEqual(abs(w_funktion(0.0j) - 1.0), 0.0, places=12)

    def test_w_funktion_symmetri(self) -> None:
        for z in (1.0 + 2.0j, 5.0 + 0.5j, 0.3 + 4.0j, 7.0 + 7.0j):
            spegel = complex(-z.real, z.imag)
            self.assertAlmostEqual(abs(w_funktion(spegel) - w_funktion(z).conjugate()), 0.0, 6)

    def test_sfarisk_reflektion_mot_baslinjens_oberoende_formel(self) -> None:
        """Ekv. (58)–(60) är Weyl–van der Pol-formen; B4a har den ur andra källor."""
        c = 340.0
        for f in (50.0, 400.0, 2000.0, 8000.0):
            for sigma in (12.5, 200.0, 20000.0):
                for hojdsumma, d in ((0.31, 100.0), (2.0, 20.0), (5.5, 1000.0)):
                    r2 = math.hypot(d, hojdsumma)
                    psi = math.atan2(hojdsumma, d)
                    z = markimpedans(f, sigma)
                    q = sfarisk_reflektion(f, r2 / c, psi, z)
                    facit = complex(
                        sfarisk_reflektion_baslinje(
                            2.0 * math.pi * f / c, r2, math.sin(psi), 1.0 / z
                        )
                    )
                    self.assertLess(abs(q - facit), 3e-5, msg=f"{f} {sigma} {d}")

    def test_sfarisk_reflektion_hard_mark_gar_mot_ett(self) -> None:
        z = markimpedans(100.0, 1.0e9)
        q = sfarisk_reflektion(100.0, 0.3, 0.02, z)
        self.assertAlmostEqual(abs(q - 1.0), 0.0, delta=2e-3)

    def test_slumpinfallsabsorption(self) -> None:
        # Z = 1 (anpassad yta): Paris formel ger 8·(1,5 − ln 4) ≈ 0,9096.
        self.assertAlmostEqual(
            slumpinfallsabsorption(complex(1.0, 1e-9)), 8.0 * (1.5 - math.log(4.0)), places=6
        )
        self.assertLess(slumpinfallsabsorption(markimpedans(100.0, 1.0e8)), 1e-3)
        for sigma in (12.5, 200.0, 20000.0):
            for f in mittfrekvenser_hz():
                alfa = slumpinfallsabsorption(markimpedans(float(f), sigma))
                self.assertTrue(0.0 < alfa < 1.0)
        z = markimpedans(1000.0, 200.0)
        self.assertAlmostEqual(
            inkoherent_reflektion(z), math.sqrt(1.0 - slumpinfallsabsorption(z)), places=14
        )


class TestKoherens(unittest.TestCase):
    def test_bandmedelvarde_ekv_111(self) -> None:
        self.assertEqual(koherens_bandmedelvarde(1000.0, 0.0), 1.0)
        dtau = 1.0e-3
        x = 0.23 * math.pi * 1000.0 * dtau
        self.assertAlmostEqual(koherens_bandmedelvarde(1000.0, dtau), math.sin(x) / x, places=14)
        # x = π precis vid f·Δτ = 1/0,23.
        self.assertEqual(koherens_bandmedelvarde(1000.0, 1.0 / 230.0 * 1.0000001), 0.0)
        self.assertGreater(koherens_bandmedelvarde(1000.0, 1.0 / 230.0 * 0.999), 0.0)
        self.assertEqual(koherens_bandmedelvarde(10000.0, 1.0), 0.0)

    def test_fluktuerande_refraktion_ekv_112(self) -> None:
        self.assertEqual(koherens_fluktuerande_refraktion(1000.0, 1e-3, 1e-3), 1.0)
        x = 2.0 * math.pi * 500.0 * 2e-4
        self.assertAlmostEqual(
            koherens_fluktuerande_refraktion(500.0, 1e-3, 1.2e-3), math.sin(x) / x, places=12
        )
        self.assertAlmostEqual(
            koherens_fluktuerande_refraktion(500.0, 1.2e-3, 1e-3), math.sin(x) / x, places=12
        )
        self.assertEqual(koherens_fluktuerande_refraktion(1000.0, 0.0, 1e-3), 0.0)

    def test_turbulens_ekv_113(self) -> None:
        self.assertEqual(koherens_turbulens(4000.0, 0.0, 0.0, 15.0, 340.0, 0.5, 100.0), 1.0)
        c, t = 340.0, 15.0
        x = -5.3888 * (0.008 / 288.15**2 + 22.0 / 3.0 * 0.12 / c**2) * (1000.0 / c) ** 2
        x *= 0.5 ** (5.0 / 3.0) * 100.0
        self.assertGreater(x, -1.0)
        self.assertAlmostEqual(
            koherens_turbulens(1000.0, 0.12, 0.008, t, c, 0.5, 100.0), math.exp(x), places=14
        )
        varden = [
            koherens_turbulens(float(f), 0.12, 0.008, t, c, 0.5, 100.0) for f in mittfrekvenser_hz()
        ]
        self.assertTrue(all(a >= b for a, b in zip(varden[:-1], varden[1:], strict=True)))
        self.assertEqual(koherens_turbulens(10000.0, 1.0, 1.0, t, c, 2.0, 1000.0), 0.0)

    def test_separation_ekv_119(self) -> None:
        self.assertAlmostEqual(transversell_separation_m(2.0, 2.0), 2.0)
        self.assertAlmostEqual(transversell_separation_m(0.5, 1.5), 0.75)

    def test_markrahet_ekv_114(self) -> None:
        self.assertEqual(koherens_markrahet(18.0, 0.1, 0.0), 1.0)
        # Under den första brytpunkten är g = 0 och F_r = 1.
        self.assertEqual(koherens_markrahet(1.0, math.pi / 2.0, 0.02), 1.0)
        # g är kontinuerlig i båda brytpunkterna.
        for brytpunkt in (0.026686, 0.115448):
            under = koherens_markrahet(1.0, math.pi / 2.0, brytpunkt * (1.0 - 1e-9))
            over = koherens_markrahet(1.0, math.pi / 2.0, brytpunkt * (1.0 + 1e-9))
            self.assertAlmostEqual(under, over, places=5)
        self.assertAlmostEqual(
            koherens_markrahet(1.0, math.pi / 2.0, 0.115448), math.exp(-0.5 * 0.049696), places=4
        )
        self.assertEqual(koherens_markrahet(20.0, math.pi / 2.0, 0.5), 0.0)


if __name__ == "__main__":
    unittest.main()
