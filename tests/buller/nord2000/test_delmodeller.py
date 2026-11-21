"""Tester för delmodell 1 och 2 (plan mark, homogen atmosfär) i Nord2000-kärnan."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.akustik.reflektion import tvastralefaktor
from buller.nord2000.atmosfar import HomogenAtmosfar, homogen_atmosfar
from buller.nord2000.delmodell1 import koherens_plan_mark, markeffekt_plan_mark_db
from buller.nord2000.delmodell2 import (
    Marksegment,
    markeffekt_blandad_mark_db,
    marktyper,
    modifierade_vikter,
    overgangsfrekvenser_hz,
)
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.mark import inkoherent_reflektion, markimpedans
from buller.typer import BullerFel, Vader

STILLA = HomogenAtmosfar(15.0, 70.0, 101.325, 0.0, 0.0)
TURBULENT = HomogenAtmosfar(15.0, 70.0, 101.325, 0.12, 0.008)


class TestAtmosfar(unittest.TestCase):
    def test_homogen_atmosfar_ur_vader(self) -> None:
        a = homogen_atmosfar(Vader(temperatur_c=10.0, cv2=0.12, ct2=0.008))
        self.assertAlmostEqual(a.ljudhastighet_m_s, 20.05 * math.sqrt(283.15), places=12)
        self.assertEqual((a.cv2, a.ct2), (0.12, 0.008))

    def test_refraktion_och_fluktuation_vagras(self) -> None:
        for vader in (
            Vader(vind_m_s=3.0),
            Vader(vind_m_s=-0.1),
            Vader(dt_dz_k_m=0.1),
            Vader(std_vind_m_s=0.5),
            Vader(std_dt_dz_k_m=0.05),
            Vader(cv2=-1.0),
        ):
            with self.assertRaises(BullerFel):
                homogen_atmosfar(vader)


class TestDelmodell1(unittest.TestCase):
    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()

    def test_hard_mark_lag_frekvens_ger_tryckdubbling(self) -> None:
        d, h_s, h_r = 100.0, 0.3, 1.5
        dl = markeffekt_plan_mark_db(self.f, d, h_s, h_r, 1.0e9, 0.0, STILLA)
        r1, r2 = math.hypot(d, h_s - h_r), math.hypot(d, h_s + h_r)
        self.assertAlmostEqual(float(dl[0]), 20.0 * math.log10(1.0 + r1 / r2), delta=0.01)
        self.assertLess(float(dl[0]), 6.0206)

    def test_inkoherent_grans_ekv_120(self) -> None:
        """Stor gångtidsskillnad: F_f = 0 och ΔL = 10·lg(1 + (R₁/R₂·ℜ_i)²)."""
        d, h_s, h_r, sigma = 20.0, 5.0, 5.0, 200.0
        self.assertEqual(koherens_plan_mark(float(self.f[26]), d, h_s, h_r, 0.0, STILLA), 0.0)
        dl = markeffekt_plan_mark_db(self.f, d, h_s, h_r, sigma, 0.0, STILLA)
        kvot = math.hypot(d, h_s - h_r) / math.hypot(d, h_s + h_r)
        ri = inkoherent_reflektion(markimpedans(float(self.f[26]), sigma))
        self.assertAlmostEqual(float(dl[26]), 10.0 * math.log10(1.0 + (kvot * ri) ** 2), places=12)
        self.assertTrue(0.0 < float(dl[26]) < 3.02)

    def test_reciprocitet(self) -> None:
        a = markeffekt_plan_mark_db(self.f, 150.0, 0.3, 4.0, 80.0, 0.0, TURBULENT)
        b = markeffekt_plan_mark_db(self.f, 150.0, 4.0, 0.3, 80.0, 0.0, TURBULENT)
        np.testing.assert_allclose(a, b, rtol=0.0, atol=1e-10)

    def test_laga_frekvenser_mot_baslinjens_tvastralesumma(self) -> None:
        """Där F ≈ 1 är ekv. (120) den koherenta tvåstrålesumman vid mittfrekvensen."""
        d, h_s, h_r, sigma = 100.0, 0.3, 1.5, 200.0
        c = STILLA.ljudhastighet_m_s
        dl = markeffekt_plan_mark_db(self.f, d, h_s, h_r, sigma, 0.0, STILLA)
        for i in range(0, 12):  # 25–315 Hz
            f = float(self.f[i])
            koherens = koherens_plan_mark(f, d, h_s, h_r, 0.0, STILLA)
            self.assertGreater(koherens, 0.9999)
            facit = 10.0 * math.log10(float(tvastralefaktor(f, h_s, h_r, d, sigma, c)))
            self.assertAlmostEqual(float(dl[i]), facit, delta=0.005, msg=str(f))

    def test_turbulens_och_rahet_minskar_koherensen(self) -> None:
        f = float(self.f[20])  # 2,5 kHz
        stilla = koherens_plan_mark(f, 100.0, 0.5, 1.5, 0.0, STILLA)
        turbulent = koherens_plan_mark(f, 100.0, 0.5, 1.5, 0.0, TURBULENT)
        skrovlig = koherens_plan_mark(f, 100.0, 0.5, 1.5, 0.5, STILLA)
        self.assertGreater(stilla, turbulent)
        self.assertGreater(stilla, skrovlig)
        self.assertGreater(turbulent, 0.0)

    def test_minsta_hojd_och_indatakontroll(self) -> None:
        a = markeffekt_plan_mark_db(self.f, 50.0, 0.0, 1.5, 200.0, 0.0, STILLA)
        b = markeffekt_plan_mark_db(self.f, 50.0, 0.01, 1.5, 200.0, 0.0, STILLA)
        np.testing.assert_array_equal(a, b)
        with self.assertRaises(BullerFel):
            markeffekt_plan_mark_db(self.f, 0.0, 1.0, 1.5, 200.0, 0.0, STILLA)
        with self.assertRaises(BullerFel):
            markeffekt_plan_mark_db(self.f, 50.0, 1.0, 1.5, 200.0, -0.1, STILLA)

    def test_ovre_grans_sex_decibel(self) -> None:
        rng = np.random.default_rng(4)
        for _ in range(40):
            dl = markeffekt_plan_mark_db(
                self.f,
                float(rng.uniform(5.0, 800.0)),
                float(rng.uniform(0.01, 5.0)),
                float(rng.uniform(0.5, 10.0)),
                float(10.0 ** rng.uniform(1.0, 5.0)),
                0.0,
                TURBULENT,
            )
            self.assertTrue(bool(np.all(np.isfinite(dl))))
            self.assertLess(float(np.max(dl)), 6.5)


class TestDelmodell2(unittest.TestCase):
    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()
        self.geometri = (96.75, 0.3, 1.5)
        self.vag_och_gras = [
            Marksegment(0.0, 1.75, 20000.0, 0.0),
            Marksegment(1.75, 96.75, 200.0, 0.0),
        ]

    def test_marktyper_i_forekomstordning(self) -> None:
        segment = [
            Marksegment(0.0, 5.0, 200.0, 0.0),
            Marksegment(5.0, 9.0, 20000.0, 0.0),
            Marksegment(9.0, 20.0, 200.0, 0.0),
            Marksegment(20.0, 30.0, 200.0, 0.5),
        ]
        self.assertEqual(marktyper(segment), [(200.0, 0.0), (20000.0, 0.0), (200.0, 0.5)])

    def test_overgangsfrekvenser(self) -> None:
        d, h_s, h_r = self.geometri
        f_l, f_h = overgangsfrekvenser_hz(self.f, d, h_s, h_r, 200.0, 340.0)
        self.assertTrue(0.0 < f_l <= 0.8 * f_h)
        self.assertTrue(25.0 < f_h < 10000.0)

    def test_vikter_summerar_enligt_ekv_127_till_129(self) -> None:
        d, h_s, h_r = self.geometri
        typer = marktyper(self.vag_och_gras)
        f_l, f_h = overgangsfrekvenser_hz(self.f, d, h_s, h_r, 200.0, 340.0)
        for f in self.f:
            vikter = modifierade_vikter(
                float(f), d, h_s, h_r, self.vag_och_gras, typer, 340.0, f_l, f_h
            )
            self.assertTrue(all(-1e-12 <= w <= 1.0 + 1e-12 for w in vikter))
            self.assertLessEqual(sum(vikter), 1.0 + 1e-9)
        # Högt över f_H täcker segmenten hela zonen: Σ r'_ii,ir = 1 och Σ r_ii,ir = 1.
        hoga = modifierade_vikter(
            float(self.f[26]), d, h_s, h_r, self.vag_och_gras, typer, 340.0, f_l, f_h
        )
        self.assertAlmostEqual(sum(hoga), 1.0, places=9)

    def test_uppdelning_av_segment_med_samma_marktyp_andrar_inget(self) -> None:
        d, h_s, h_r = self.geometri
        delat = [
            Marksegment(0.0, 1.75, 20000.0, 0.0),
            Marksegment(1.75, 30.0, 200.0, 0.0),
            Marksegment(30.0, 31.0, 200.0, 0.0),
            Marksegment(31.0, 96.75, 200.0, 0.0),
        ]
        a = markeffekt_blandad_mark_db(self.f, d, h_s, h_r, self.vag_och_gras, STILLA)
        b = markeffekt_blandad_mark_db(self.f, d, h_s, h_r, delat, STILLA)
        np.testing.assert_allclose(a, b, rtol=0.0, atol=1e-9)

    def test_ligger_mellan_de_rena_marktyperna_dar_zonen_ryms(self) -> None:
        """Vid höga frekvenser summerar vikterna till 1: ΔL₂ är ett vägt medelvärde."""
        d, h_s, h_r = self.geometri
        blandad = markeffekt_blandad_mark_db(self.f, d, h_s, h_r, self.vag_och_gras, STILLA)
        hard = markeffekt_plan_mark_db(self.f, d, h_s, h_r, 20000.0, 0.0, STILLA)
        mjuk = markeffekt_plan_mark_db(self.f, d, h_s, h_r, 200.0, 0.0, STILLA)
        for i in range(20, 27):
            lagst, hogst = sorted((float(hard[i]), float(mjuk[i])))
            self.assertTrue(lagst - 1e-9 <= float(blandad[i]) <= hogst + 1e-9)

    def test_forsumbar_remsa_ger_delmodell_1(self) -> None:
        d, h_s, h_r = self.geometri
        segment = [
            Marksegment(0.0, d - 1.0e-7, 200.0, 0.0),
            Marksegment(d - 1.0e-7, d, 20000.0, 0.0),
        ]
        blandad = markeffekt_blandad_mark_db(self.f, d, h_s, h_r, segment, STILLA)
        ren = markeffekt_plan_mark_db(self.f, d, h_s, h_r, 200.0, 0.0, STILLA)
        # Jämför där Fresnelzonen ryms mellan källa och mottagare (vikterna summerar till 1).
        np.testing.assert_allclose(blandad[18:], ren[18:], rtol=0.0, atol=1e-4)

    def test_reciprocitet(self) -> None:
        d, h_s, h_r = self.geometri
        speglad = [
            Marksegment(0.0, d - 1.75, 200.0, 0.0),
            Marksegment(d - 1.75, d, 20000.0, 0.0),
        ]
        a = markeffekt_blandad_mark_db(self.f, d, h_s, h_r, self.vag_och_gras, TURBULENT)
        b = markeffekt_blandad_mark_db(self.f, d, h_r, h_s, speglad, TURBULENT)
        np.testing.assert_allclose(a, b, rtol=0.0, atol=1e-9)

    def test_segment_utan_utstrackning_hoppas_over(self) -> None:
        d, h_s, h_r = self.geometri
        extra = [*self.vag_och_gras, Marksegment(50.0, 50.0, 12.5, 0.0)]
        typer = marktyper(extra)
        vikter = modifierade_vikter(1000.0, d, h_s, h_r, extra, typer, 340.0, 100.0, 400.0)
        self.assertEqual(vikter[2], 0.0)
        with self.assertRaises(BullerFel):
            markeffekt_blandad_mark_db(self.f, d, h_s, h_r, [], STILLA)


if __name__ == "__main__":
    unittest.main()
