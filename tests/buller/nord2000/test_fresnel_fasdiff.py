"""Tester för Fresnelzoner, Fresnelzonsvikter och PhaseDiffFreq i Nord2000-kärnan."""

from __future__ import annotations

import math
import unittest

from buller.nord2000.fasdiff import fasdifferensfrekvens_hz, fasskillnad_rad
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.fresnel import (
    calc_fzd,
    fresnelzon_langs_mark,
    fresnelzonsstorlek,
    fresnelzonsvikt,
    fresnelzonsvikt_modifierad,
)
from buller.nord2000.mark import markimpedans
from buller.typer import BullerFel


class TestCalcFZd(unittest.TestCase):
    def test_symmetriskt_fall_ger_lillaxeln(self) -> None:
        r0, fl = 50.0, 0.3
        a = (2.0 * r0 + fl) / 2.0
        self.assertAlmostEqual(
            calc_fzd(r0, r0, math.pi / 2.0, fl), math.sqrt(a * a - r0 * r0), places=10
        )

    def test_punkten_ligger_pa_ellipsen(self) -> None:
        """|SP| + |RP| = |SR| + F·λ (ekv. 108) för alla riktningar."""
        r_s, r_r, fl = 12.0, 70.0, 0.21
        for theta in (0.05, 0.4, 1.2, math.pi / 2.0, 2.5, 3.1):
            d = calc_fzd(r_s, r_r, theta, fl)
            px, py = d * math.cos(theta), d * math.sin(theta)  # O i origo, R längs +x
            summa = math.hypot(px + r_s, py) + math.hypot(px - r_r, py)
            self.assertAlmostEqual(summa, r_s + r_r + fl, places=9)
            self.assertGreater(d, 0.0)


class TestFresnelzon(unittest.TestCase):
    def test_lika_hojder_ger_symmetrisk_zon(self) -> None:
        a1, a2, b = fresnelzonsstorlek(100.0, 2.0, 2.0, 0.1)
        self.assertAlmostEqual(a1, a2, places=9)
        self.assertGreater(a1, b)
        zon = fresnelzon_langs_mark(100.0, 2.0, 2.0, 0.1)
        self.assertAlmostEqual(zon.d_refl_m, 50.0)
        self.assertAlmostEqual(zon.d_refl_m - zon.d1_fz_m, zon.d2_fz_m - zon.d_refl_m, places=9)

    def test_zonen_vaxer_med_vaglangden_och_speglas_med_hojderna(self) -> None:
        liten = fresnelzonsstorlek(80.0, 0.5, 3.0, 0.02)
        stor = fresnelzonsstorlek(80.0, 0.5, 3.0, 0.2)
        self.assertTrue(all(s > li for s, li in zip(stor, liten, strict=True)))
        a1, a2, b = fresnelzonsstorlek(80.0, 0.5, 3.0, 0.1)
        a1s, a2s, bs = fresnelzonsstorlek(80.0, 3.0, 0.5, 0.1)
        self.assertAlmostEqual(a1, a2s, places=9)
        self.assertAlmostEqual(a2, a1s, places=9)
        self.assertAlmostEqual(b, bs, places=9)

    def test_laga_hojder_ersatts_med_en_centimeter(self) -> None:
        self.assertEqual(
            fresnelzon_langs_mark(50.0, 0.0, 1.5, 0.1), fresnelzon_langs_mark(50.0, 0.01, 1.5, 0.1)
        )
        with self.assertRaises(BullerFel):
            fresnelzon_langs_mark(0.0, 1.0, 1.5, 0.1)


class TestFresnelzonsvikter(unittest.TestCase):
    def setUp(self) -> None:
        self.geometri = (100.0, 0.5, 1.5, 0.05)  # d, h_S, h_R, F·λ
        d, h_s, h_r, fl = self.geometri
        self.zon = fresnelzon_langs_mark(d, h_s, h_r, fl)

    def _w(self, d1: float, d2: float) -> float:
        d, h_s, h_r, fl = self.geometri
        return fresnelzonsvikt(d, h_s, h_r, d1, d2, fl)

    def _wm(self, d1: float, d2: float) -> float:
        d, h_s, h_r, fl = self.geometri
        return fresnelzonsvikt_modifierad(d, h_s, h_r, d1, d2, fl)

    def test_fem_fall_i_ekv_351(self) -> None:
        z1, z2 = self.zon.d1_fz_m, self.zon.d2_fz_m
        langd = z2 - z1
        self.assertEqual(self._w(z2, z2 + 5.0), 0.0)
        self.assertEqual(self._w(z1 - 5.0, z1), 0.0)
        self.assertEqual(self._w(z1 - 1.0, z2 + 1.0), 1.0)
        self.assertEqual(self._w(z1, z2), 1.0)
        self.assertAlmostEqual(self._w(z1 + 0.2 * langd, z1 + 0.5 * langd), 0.3, places=12)
        self.assertAlmostEqual(self._w(z1 - 3.0, z1 + 0.25 * langd), 0.25, places=12)
        self.assertAlmostEqual(self._w(z1 + 0.6 * langd, z2 + 3.0), 0.4, places=12)

    def test_vikterna_summerar_till_ett_over_en_uppdelning(self) -> None:
        z1, z2 = self.zon.d1_fz_m, self.zon.d2_fz_m
        granser = [z1 - 2.0, z1 + 1.0, self.zon.d_refl_m + 0.3, z2 - 0.1, z2 + 4.0]
        par = list(zip(granser[:-1], granser[1:], strict=True))
        self.assertAlmostEqual(sum(self._w(a, b) for a, b in par), 1.0, places=12)
        self.assertAlmostEqual(sum(self._wm(a, b) for a, b in par), 1.0, places=12)

    def test_modifierad_vikt_ger_halva_vikten_at_varje_sida(self) -> None:
        z1, z2, mitt = self.zon.d1_fz_m, self.zon.d2_fz_m, self.zon.d_refl_m
        self.assertAlmostEqual(self._wm(z1 - 1.0, mitt), 0.5, places=12)
        self.assertAlmostEqual(self._wm(mitt, z2 + 1.0), 0.5, places=12)
        self.assertEqual(self._wm(z1, z2), 1.0)
        # Segment som börjar inne i källhalvan och når förbi reflexpunkten (femte fallet i
        # ekv. 353 för w_S): andelen av källhalvan plus hela mottagarhalvan.
        start = z1 + 0.25 * (mitt - z1)
        self.assertAlmostEqual(self._wm(start, z2 + 1.0), 0.5 * (0.75 + 1.0), places=12)
        # Källhalvan är kortare än mottagarhalvan när källan är lägre, så samma sträcka väger
        # tyngre på källsidan.
        self.assertLess(mitt - z1, z2 - mitt)
        self.assertGreater(self._wm(mitt - 0.5, mitt), self._wm(mitt, mitt + 0.5))


class TestFasdifferensfrekvens(unittest.TestCase):
    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()
        self.c = 340.0

    def _impedanser(self, sigma: float) -> list[complex]:
        return [markimpedans(float(f), sigma) for f in self.f]

    def test_hard_mark_ger_frekvensen_dar_gangvagen_ar_en_halv_vaglangd(self) -> None:
        d, h_s, h_r = 20.0, 2.0, 3.0
        delta_r = math.hypot(d, h_s + h_r) - math.hypot(d, h_s - h_r)
        f_pi = fasdifferensfrekvens_hz(
            self.f, d, h_s, h_r, self._impedanser(1.0e9), self.c, math.pi
        )
        # Interpolationen i ekv. (380) är linjär i lg f mellan två tersband: någon procent.
        self.assertAlmostEqual(f_pi / (self.c / (2.0 * delta_r)), 1.0, delta=0.01)

    def test_resultatet_aterger_den_sokta_fasskillnaden(self) -> None:
        d, h_s, h_r, sigma = 100.0, 0.3, 1.5, 200.0
        z = self._impedanser(sigma)
        # Över mjuk mark vid strykande infall är arg(R_p) > 0 redan vid 25 Hz.
        alfa_25 = fasskillnad_rad(float(self.f[0]), d, h_s, h_r, z[0], self.c)
        self.assertTrue(1.0 < alfa_25 < 2.0)
        for sokt in (2.0, 2.5, math.pi):
            f = fasdifferensfrekvens_hz(self.f, d, h_s, h_r, z, self.c, sokt)
            self.assertTrue(25.0 < f < 10000.0)
            alfa = fasskillnad_rad(f, d, h_s, h_r, markimpedans(f, sigma), self.c)
            self.assertAlmostEqual(alfa, sokt, delta=0.02)

    def test_fasskillnaden_vaxer_med_frekvensen(self) -> None:
        z = self._impedanser(200.0)
        alfa = [
            fasskillnad_rad(float(f), 100.0, 0.3, 1.5, z[i], self.c) for i, f in enumerate(self.f)
        ]
        self.assertTrue(all(b > a for a, b in zip(alfa[:-1], alfa[1:], strict=True)))

    def test_extrapolation_nedat_ekv_381(self) -> None:
        d, h_s, h_r, sigma = 100.0, 0.3, 1.5, 200.0
        z = self._impedanser(sigma)
        alfa_25 = fasskillnad_rad(float(self.f[0]), d, h_s, h_r, z[0], self.c)
        f = fasdifferensfrekvens_hz(self.f, d, h_s, h_r, z, self.c, 0.5 * alfa_25)
        self.assertAlmostEqual(f, math.sqrt(float(self.f[0])), places=9)
        self.assertAlmostEqual(
            fasdifferensfrekvens_hz(self.f, d, h_s, h_r, z, self.c, -alfa_25),
            1.0 / float(self.f[0]),
            places=12,
        )

    def test_extrapolation_uppat_ar_linjar_och_begransad(self) -> None:
        d, h_s, h_r = 200.0, 0.05, 0.5
        z = self._impedanser(1.0e9)
        alfa = [fasskillnad_rad(float(f), d, h_s, h_r, z[i], self.c) for i, f in enumerate(self.f)]
        self.assertLess(alfa[-1], math.pi)
        sokt = alfa[-1] + 0.5 * (alfa[-1] - alfa[-2])
        f = fasdifferensfrekvens_hz(self.f, d, h_s, h_r, z, self.c, sokt)
        self.assertAlmostEqual(
            f, float(self.f[-1]) + 0.5 * float(self.f[-1] - self.f[-2]), places=6
        )
        self.assertEqual(fasdifferensfrekvens_hz(self.f, d, h_s, h_r, z, self.c, 1000.0), 100_000.0)


if __name__ == "__main__":
    unittest.main()
