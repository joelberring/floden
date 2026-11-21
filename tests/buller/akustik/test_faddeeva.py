"""Tester för Faddeevafunktionen (B4a)."""

from __future__ import annotations

import importlib.util
import math
import unittest

import numpy as np

from buller.akustik.faddeeva import faddeeva

HAR_SCIPY: bool = importlib.util.find_spec("scipy") is not None


def max_relativt_fel_ovre_halvplan() -> float:
    """Största relativa felet |w − w_ref|/|w_ref| mot scipy på ett rutnät med Im z ≥ 0.

    Rutnät: Re z = 0 och ±10^(−3…3) (121 punkter per dekadspann), Im z = 0 och 10^(−6…3).
    """
    from scipy.special import wofz

    pos = np.logspace(-3.0, 3.0, 121)
    x = np.concatenate((-pos[::-1], [0.0], pos))
    y = np.concatenate(([0.0], np.logspace(-6.0, 3.0, 181)))
    z = x[np.newaxis, :] + 1j * y[:, np.newaxis]
    referens = wofz(z)
    return float(np.max(np.abs(faddeeva(z) - referens) / np.abs(referens)))


class TestFaddeeva(unittest.TestCase):
    """w(z) mot kända värden och mot scipy."""

    def test_kanda_varden(self) -> None:
        # w(0) = 1; w(i·y) = exp(y²)·erfc(y) (Abramowitz & Stegun 7.1.3).
        self.assertAlmostEqual(abs(complex(faddeeva(0.0)) - 1.0), 0.0, places=13)
        for y in (0.1, 0.5, 1.0, 2.0, 5.0):
            facit = math.exp(y * y) * math.erfc(y)
            self.assertAlmostEqual(complex(faddeeva(1j * y)).real / facit, 1.0, places=12)
            self.assertAlmostEqual(complex(faddeeva(1j * y)).imag, 0.0, places=13)

    def test_reell_axel_realdel(self) -> None:
        # Re w(x) = exp(−x²) för reella x (A&S 7.1.3).
        x = np.linspace(-3.0, 3.0, 61)
        np.testing.assert_allclose(faddeeva(x).real, np.exp(-x * x), atol=1e-13)

    def test_symmetri(self) -> None:
        # w(−conj z) = conj w(z) (A&S 7.1.12).
        rng = np.random.default_rng(1)
        z = rng.normal(size=200) * 3.0 + 1j * np.abs(rng.normal(size=200)) * 3.0
        np.testing.assert_allclose(faddeeva(-np.conj(z)), np.conj(faddeeva(z)), rtol=1e-12)

    def test_asymptot(self) -> None:
        # w(z) ≈ i/(√π·z)·(1 + 1/(2z²)) för stora |z| i övre halvplanet (A&S 7.1.23).
        z = np.array([200.0 + 0.0j, 150.0 + 150.0j, 300.0j, -250.0 + 10.0j])
        facit = 1j / (math.sqrt(math.pi) * z) * (1.0 + 0.5 / z**2)
        np.testing.assert_allclose(faddeeva(z), facit, rtol=1e-8)

    def test_form_bevaras(self) -> None:
        z = np.zeros((3, 4), dtype=np.complex128) + 1j
        self.assertEqual(faddeeva(z).shape, (3, 4))
        self.assertEqual(faddeeva(1.0 + 1.0j).shape, ())

    @unittest.skipUnless(HAR_SCIPY, "scipy saknas")
    def test_mot_scipy_ovre_halvplan(self) -> None:
        self.assertLess(max_relativt_fel_ovre_halvplan(), 1e-12)

    @unittest.skipUnless(HAR_SCIPY, "scipy saknas")
    def test_mot_scipy_nedre_halvplan(self) -> None:
        from scipy.special import wofz

        # Den sektor som reflektionskoefficienten använder (−π/4 < arg w < 0) och lite till.
        x = np.linspace(-6.0, 6.0, 49)
        y = -np.linspace(0.01, 4.0, 40)
        z = x[np.newaxis, :] + 1j * y[:, np.newaxis]
        referens = wofz(z)
        fel = np.max(np.abs(faddeeva(z) - referens) / np.abs(referens))
        self.assertLess(float(fel), 1e-11)


if __name__ == "__main__":
    unittest.main()
