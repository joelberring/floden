"""Green–Ampt i 0D ska inte bero på tidsstegets längd när initialförlusten fylls inne i ett steg."""

from __future__ import annotations

import unittest

import numpy as np

from mark_forluster.infiltration import green_ampt_tidsserie


class TestTidsstegsoberoende(unittest.TestCase):
    def test_ett_timsteg_lika_med_sekundsteg(self) -> None:
        tider = np.arange(0.0, 3601.0, 1.0)
        for intensitet in (50.0, 15.0, 8.0):
            with self.subTest(intensitet=intensitet):
                grov = green_ampt_tidsserie([0.0, 3600.0], [intensitet, intensitet],
                                            10.0, 0.11, 0.3, initial_mm=2.0)
                fin = green_ampt_tidsserie(tider, np.full(tider.size, intensitet),
                                           10.0, 0.11, 0.3, initial_mm=2.0)
                self.assertAlmostEqual(grov.infiltration_m, fin.infiltration_m, places=9)
                self.assertAlmostEqual(grov.initial_forlust_m, 0.002, places=12)
                balans = grov.initial_forlust_m + grov.infiltration_m + grov.netto_regn_m
                self.assertAlmostEqual(balans, grov.regn_totalt_m, places=12)


if __name__ == "__main__":
    unittest.main()
