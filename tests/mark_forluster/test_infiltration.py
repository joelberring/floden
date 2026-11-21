"""Enhetstester för `mark_forluster.parametrar` och `mark_forluster.infiltration`."""

from __future__ import annotations

import math
import unittest

import numpy as np

from mark_forluster.infiltration import (
    berakna_cell_infiltration,
    green_ampt_tidsserie,
)
from mark_forluster.parametrar import (
    BERG,
    BYGGNAD,
    GRAS,
    GRONT_TAK,
    GRUS,
    HARDGJORT,
    JORDART_KOD_MORAN,
    JORDART_KOD_SAND,
    OKANT,
    SKOG,
    TAK,
    VATTEN,
    ForlustFel,
    effektiv_markparametrar,
    hamta_jordart,
)


class TestParametrar(unittest.TestCase):
    def test_standard_jordarter_finns_och_ar_fysikaliska(self) -> None:
        for namn in ("berg", "moran", "sand", "silt", "lera", "fyllning", "torv"):
            j = hamta_jordart(namn)
            self.assertGreater(j.ksat_mm_h, 0.0)
            self.assertGreater(j.porositet, 0.0)
            self.assertGreaterEqual(j.delta_theta(hog_markfukt=False), 0.0)
            self.assertLessEqual(
                j.delta_theta(hog_markfukt=True),
                j.delta_theta(hog_markfukt=False),
            )

    def test_okand_jordart_och_ogiltigt_markslag_ger_tydligt_fel(self) -> None:
        with self.assertRaises(ForlustFel):
            hamta_jordart("kvicksand")
        with self.assertRaises(ForlustFel):
            hamta_jordart(99)
        with self.assertRaises(ForlustFel):
            effektiv_markparametrar(42, "moran")
        with self.assertRaises(ForlustFel):
            effektiv_markparametrar(-1, "moran")

    def test_markslag_koppling_och_tjale(self) -> None:
        okant = effektiv_markparametrar(OKANT, "moran")
        self.assertEqual(okant.ksat_mm_h, 0.0)
        self.assertEqual(okant.initial_faktor, 0.5)
        self.assertEqual(okant.delta_theta, 0.0)

        hard = effektiv_markparametrar(HARDGJORT, "sand")
        self.assertEqual(hard.ksat_mm_h, 0.0)
        self.assertEqual(hard.initial_faktor, 1.0)

        vatten = effektiv_markparametrar(VATTEN, "sand")
        self.assertEqual(vatten.ksat_mm_h, 0.0)
        self.assertEqual(vatten.initial_faktor, 0.0)

        tak = effektiv_markparametrar(TAK, "sand")
        bygg = effektiv_markparametrar(BYGGNAD, "sand")
        self.assertEqual(tak.ksat_mm_h, 0.0)
        self.assertEqual(bygg.ksat_mm_h, 0.0)

        gras = effektiv_markparametrar(GRAS, "moran")
        skog = effektiv_markparametrar(SKOG, "moran")
        grus = effektiv_markparametrar(GRUS, "lera")
        berg = effektiv_markparametrar(BERG, "sand")
        gront = effektiv_markparametrar(GRONT_TAK, "lera")

        self.assertGreater(skog.ksat_mm_h, gras.ksat_mm_h)
        self.assertGreater(grus.ksat_mm_h, gras.ksat_mm_h)
        self.assertLess(berg.ksat_mm_h, 1.0)
        self.assertGreater(gront.ksat_mm_h, 10.0)

        gras_tjale = effektiv_markparametrar(GRAS, "moran", tjale=True)
        self.assertLess(gras_tjale.ksat_mm_h, gras.ksat_mm_h * 0.1)
        self.assertLess(gras_tjale.delta_theta, gras.delta_theta)


class TestGreenAmptInfiltration(unittest.TestCase):
    def test_konstant_intensitet_under_ksat_ger_100_procent_infiltration(self) -> None:
        # 1 timme med 20 mm/h på sand (ksat = 60 mm/h) och initial förlust 2 mm
        tid = [0.0, 1800.0, 3600.0]
        intens = [20.0, 20.0, 20.0]
        res = green_ampt_tidsserie(
            tid,
            intens,
            ksat_mm_h=60.0,
            psi_m=0.06,
            delta_theta=0.33,
            initial_mm=2.0,
        )
        self.assertAlmostEqual(res.regn_totalt_m, 0.020, places=12)
        self.assertAlmostEqual(res.initial_forlust_m, 0.002, places=12)
        self.assertAlmostEqual(res.infiltration_m, 0.018, places=12)
        self.assertAlmostEqual(res.netto_regn_m, 0.0, places=12)
        self.assertLess(abs(res.massbalans_fel_m), 1e-12)

    def test_intensivt_regn_over_ksat_och_analytisk_jamforelse(self) -> None:
        # Konstant regn 100 mm/h i 1 h på jord med ksat=10 mm/h, psi=0.10 m, dtheta=0.25,
        # initial_mm=0.0 -> exakt analytisk implicit lösning:
        # F_p = (psi*dtheta) / (i/K - 1) = 0.025 / 9 = 0.002777... m
        # t_p = F_p / i = (0.025/9) / 0.10 = 100 s
        tid = np.linspace(0.0, 3600.0, 37)
        intens = np.full_like(tid, 100.0)
        res = green_ampt_tidsserie(
            tid,
            intens,
            ksat_mm_h=10.0,
            psi_m=0.10,
            delta_theta=0.25,
            initial_mm=0.0,
        )
        self.assertLess(abs(res.massbalans_fel_m), 1e-12)
        self.assertGreater(res.netto_regn_m, 0.0)
        self.assertGreater(res.infiltration_m, 0.010)

        # Kontrollera att lösningen uppfyller den analytiska Green–Ampt-ekvationen efter t_p
        ksat_m_s = 10.0 / 3_600_000.0
        psi_dtheta = 0.10 * 0.25
        f_p = psi_dtheta / (100.0 / 10.0 - 1.0)
        t_p = f_p / (100.0 / 3_600_000.0)
        f = res.infiltration_m
        lhs = f - f_p - psi_dtheta * math.log((f + psi_dtheta) / (f_p + psi_dtheta))
        rhs = ksat_m_s * (3600.0 - t_p)
        self.assertAlmostEqual(lhs, rhs, places=8)

    def test_grundvattentak_begransar_infiltration(self) -> None:
        tid = [0.0, 3600.0]
        intens = [30.0, 30.0]
        res = green_ampt_tidsserie(
            tid,
            intens,
            ksat_mm_h=60.0,
            psi_m=0.06,
            delta_theta=0.33,
            initial_mm=2.0,
            max_lagring_m=0.010,
        )
        self.assertAlmostEqual(res.initial_forlust_m, 0.002, places=12)
        self.assertAlmostEqual(res.infiltration_m, 0.010, places=12)
        self.assertAlmostEqual(res.netto_regn_m, 0.018, places=12)
        self.assertLess(abs(res.massbalans_fel_m), 1e-12)

    def test_raster_infiltration_hardgjort_gras_och_tjale(self) -> None:
        markslag = np.array(
            [
                [HARDGJORT, GRAS],
                [VATTEN, SKOG],
            ],
            dtype=np.uint8,
        )
        jordart = np.array(
            [
                [JORDART_KOD_SAND, JORDART_KOD_SAND],
                [JORDART_KOD_MORAN, JORDART_KOD_MORAN],
            ],
            dtype=np.uint8,
        )
        tid = [0.0, 1800.0, 3600.0]
        intens = [50.0, 50.0, 50.0]

        res_norm = berakna_cell_infiltration(
            markslag, jordart, tid, intens, initial_mm=2.0, tjale=False
        )
        self.assertLess(res_norm.max_massbalans_fel_m, 1e-6)
        # Hårdgjort: 0 infiltration, 2 mm initial, 48 mm netto
        self.assertAlmostEqual(float(res_norm.infiltration_m[0, 0]), 0.0, places=6)
        self.assertAlmostEqual(float(res_norm.netto_regn_m[0, 0]), 0.048, places=6)
        # Vatten: 0 infiltration, 0 initial, 50 mm netto
        self.assertAlmostEqual(float(res_norm.total_forlust_m[1, 0]), 0.0, places=6)
        self.assertAlmostEqual(float(res_norm.netto_regn_m[1, 0]), 0.050, places=6)
        # Gräs på sand: hög infiltration (< 50 mm < 60 mm/h)
        self.assertAlmostEqual(float(res_norm.netto_regn_m[0, 1]), 0.0, places=6)

        # Med tjäle minskar infiltrationen kraftigt på både gräs och skog
        res_tjale = berakna_cell_infiltration(
            markslag, jordart, tid, intens, initial_mm=2.0, tjale=True
        )
        self.assertLess(
            float(res_tjale.infiltration_m[0, 1]),
            float(res_norm.infiltration_m[0, 1]) * 0.3,
        )
        self.assertGreater(
            float(res_tjale.netto_regn_m[1, 1]),
            float(res_norm.netto_regn_m[1, 1]),
        )
