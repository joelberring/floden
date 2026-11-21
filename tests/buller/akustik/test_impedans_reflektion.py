"""Tester för Delany–Bazley-impedans och reflektionskoefficienter (B4a)."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.akustik.impedans import delany_bazley_admittans, delany_bazley_impedans
from buller.akustik.reflektion import (
    numeriskt_avstand,
    planvagsreflektion,
    randforlustfaktor,
    sfarisk_reflektion,
    tvastralefaktor,
)

C_M_S = 340.0


class TestImpedans(unittest.TestCase):
    """Delany–Bazleys modell med konventionen exp(−iωt)."""

    def test_handraknat_varde(self) -> None:
        # f/σ = 1 ger Z = 1 + 9,08 + 11,9i.
        z = complex(delany_bazley_impedans(200.0, 200.0))
        self.assertAlmostEqual(z.real, 10.08, places=12)
        self.assertAlmostEqual(z.imag, 11.9, places=12)
        # f/σ = 10: 1 + 9,08·10^(−0,75) + i·11,9·10^(−0,73)
        z = complex(delany_bazley_impedans(1000.0, 100.0))
        self.assertAlmostEqual(z.real, 1.0 + 9.08 * 10.0**-0.75, places=12)
        self.assertAlmostEqual(z.imag, 11.9 * 10.0**-0.73, places=12)

    def test_mot_originalkoefficienterna(self) -> None:
        # Delany & Bazley (1970): Z = 1 + 0,0571·X^(−0,754) + i·0,087·X^(−0,732) med
        # X = ρ₀·f/σ (σ i Pa·s/m²). Markformen med 9,08 och 11,9 är samma sak med avrundade
        # exponenter och ρ₀ inbakad; de ska stämma inom några procent i giltighetsområdet.
        rho0 = 1.2
        for f_hz, sigma_k in ((500.0, 20.0), (1000.0, 200.0), (4000.0, 200.0)):
            x = rho0 * f_hz / (1000.0 * sigma_k)
            original = 1.0 + 0.0571 * x**-0.754 + 1j * 0.087 * x**-0.732
            z = complex(delany_bazley_impedans(f_hz, sigma_k))
            self.assertLess(abs(z - original) / abs(original), 0.05)

    def test_tecken_och_passivitet(self) -> None:
        f = np.logspace(1.3, 4.0, 30)[:, None]
        sigma = np.logspace(0.5, 5.0, 20)[None, :]
        z = delany_bazley_impedans(f, sigma)
        self.assertEqual(z.shape, (30, 20))
        self.assertTrue(bool(np.all(z.real > 1.0)))
        self.assertTrue(bool(np.all(z.imag > 0.0)))  # exp(−iωt): fjäderlik yta
        beta = delany_bazley_admittans(f, sigma)
        np.testing.assert_allclose(beta, 1.0 / z, rtol=1e-14)
        self.assertTrue(bool(np.all(beta.real > 0.0)))  # passiv yta

    def test_hard_yta_och_fel(self) -> None:
        self.assertEqual(complex(delany_bazley_admittans(1000.0, np.inf)), 0.0)
        with self.assertRaises(ValueError):
            delany_bazley_admittans(1000.0, 0.0)
        with self.assertRaises(ValueError):
            delany_bazley_admittans(0.0, 100.0)
        with self.assertRaises(ValueError):
            delany_bazley_admittans(1000.0, float("nan"))


class TestReflektion(unittest.TestCase):
    """Planvågs- och sfärisk reflektionskoefficient."""

    def test_randforlustfaktor_gransfall(self) -> None:
        # F(0) = 1 och F(w) ≈ 1 + i·√π·w för små |w|.
        self.assertAlmostEqual(abs(complex(randforlustfaktor(0.0)) - 1.0), 0.0, places=13)
        w = 1e-4 * (1.0 + 1.0j)
        self.assertAlmostEqual(
            abs(complex(randforlustfaktor(w)) - (1.0 + 1j * math.sqrt(math.pi) * w)), 0.0, places=7
        )
        # F(w) → −1/(2w²) → 0 för stora |w| (i den sektor som förekommer: |arg w| ≤ π/4).
        for vinkel in (-0.7, 0.0, 0.5, math.pi / 4.0):
            for belopp in (30.0, 300.0):
                ww = belopp * complex(math.cos(vinkel), math.sin(vinkel))
                f_w = complex(randforlustfaktor(ww))
                asymptot = -0.5 / ww**2
                self.assertLess(abs(f_w - asymptot), 3.0 * abs(asymptot) / belopp**2 + 1e-13)
                self.assertLess(abs(f_w), 1e-3)

    def test_hard_mark_ger_q_ett(self) -> None:
        k = 2.0 * math.pi * np.array([50.0, 500.0, 5000.0]) / C_M_S
        for cos_theta in (0.0, 1e-3, 0.3, 1.0):
            q = sfarisk_reflektion(k, 100.0, cos_theta, 0.0)
            np.testing.assert_allclose(q, 1.0, atol=1e-13)
        # Nästan hård mark (mycket hög flödesresistivitet), inte strykande infall.
        f = np.array([50.0, 500.0, 5000.0])
        beta = delany_bazley_admittans(f, 1.0e9)
        q = sfarisk_reflektion(2.0 * math.pi * f / C_M_S, 20.0, 0.5, beta)
        self.assertLess(float(np.max(np.abs(q - 1.0))), 2e-3)

    def test_mjuk_mark_strykande_infall_ger_q_minus_ett(self) -> None:
        f_hz = 2000.0
        k = 2.0 * math.pi * f_hz / C_M_S
        beta = delany_bazley_admittans(f_hz, 10.0)
        tidigare_avvikelse = math.inf
        for r2_m in (100.0, 1000.0, 10000.0):
            cos_theta = 0.02 / r2_m  # källa och mottagare 1 cm över marken
            q = complex(sfarisk_reflektion(k, r2_m, cos_theta, beta))
            avvikelse = abs(q + 1.0)
            self.assertLess(avvikelse, tidigare_avvikelse)
            tidigare_avvikelse = avvikelse
        self.assertLess(tidigare_avvikelse, 1e-3)

    def test_planvagsgrans_for_stora_numeriska_avstand(self) -> None:
        # Brant infall, långt avstånd i våglängder: Q → R_p.
        f_hz = 4000.0
        k = 2.0 * math.pi * f_hz / C_M_S
        beta = delany_bazley_admittans(f_hz, 100.0)
        r_p = complex(planvagsreflektion(0.8, beta))
        q = complex(sfarisk_reflektion(k, 500.0, 0.8, beta))
        self.assertLess(abs(q - r_p), 1e-3)
        self.assertLess(abs(r_p), 1.0)

    def test_planvagsreflektion_och_numeriskt_avstand(self) -> None:
        beta = 0.2 - 0.1j
        r_p = complex(planvagsreflektion(0.5, beta))
        self.assertAlmostEqual(abs(r_p - (0.5 - beta) / (0.5 + beta)), 0.0, places=15)
        self.assertEqual(complex(planvagsreflektion(0.0, 0.0)), 1.0)
        w = complex(numeriskt_avstand(3.0, 50.0, 0.5, beta))
        self.assertAlmostEqual(abs(w**2 - 0.5j * 3.0 * 50.0 * (0.5 + beta) ** 2), 0.0, places=11)


class TestTvastrale(unittest.TestCase):
    """Koherent tvåstrålesumma vid rena toner."""

    def test_interferensminimum_over_hard_mark(self) -> None:
        # Första minimum där gångvägsskillnaden är en halv våglängd: f = c/(2·(R₂ − R₁)).
        h_s, h_r, d = 1.5, 2.0, 30.0
        r1 = math.hypot(d, h_s - h_r)
        r2 = math.hypot(d, h_s + h_r)
        f_facit_hz = C_M_S / (2.0 * (r2 - r1))
        f = np.linspace(0.5 * f_facit_hz, 1.5 * f_facit_hz, 20001)
        faktor = tvastralefaktor(f, h_s, h_r, d, np.inf, C_M_S)
        f_min_hz = float(f[int(np.argmin(faktor))])
        self.assertAlmostEqual(f_min_hz / f_facit_hz, 1.0, delta=1e-4)
        # Djupet: |1 − R₁/R₂|².
        self.assertAlmostEqual(float(faktor.min()), (1.0 - r1 / r2) ** 2, delta=1e-8)
        # Första maximum (hel våglängd): |1 + R₁/R₂|².
        faktor_max = float(tvastralefaktor(2.0 * f_facit_hz, h_s, h_r, d, np.inf, C_M_S))
        self.assertAlmostEqual(faktor_max, (1.0 + r1 / r2) ** 2, places=10)

    def test_hard_mark_lag_frekvens_ger_plus_6_db(self) -> None:
        faktor = float(tvastralefaktor(25.0, 0.3, 0.3, 100.0, np.inf, C_M_S))
        self.assertAlmostEqual(10.0 * math.log10(faktor), 20.0 * math.log10(2.0), delta=0.001)

    def test_reciprocitet(self) -> None:
        f = np.logspace(1.4, 4.0, 40)
        a = tvastralefaktor(f, 0.5, 4.0, 80.0, 200.0, C_M_S)
        b = tvastralefaktor(f, 4.0, 0.5, 80.0, 200.0, C_M_S)
        np.testing.assert_array_equal(a, b)

    def test_ogiltig_geometri(self) -> None:
        with self.assertRaises(ValueError):
            tvastralefaktor(100.0, 1.0, 1.0, 0.0, 200.0, C_M_S)
        with self.assertRaises(ValueError):
            tvastralefaktor(100.0, -1.0, 1.0, 10.0, 200.0, C_M_S)


if __name__ == "__main__":
    unittest.main()
