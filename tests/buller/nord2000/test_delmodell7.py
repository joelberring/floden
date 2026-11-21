"""Tester för delmodell 7 (turbulensspridning bakom skärm) i Nord2000-kärnan.

Svaren följer av AV 1106/07 avsnitt 5.16 (tabellvärden, ekvationernas form), av geometri
(figur 26 och 27) eller av dimensionsanalys. Sist prövas C_T²-termen i ekv. (113).
"""

from __future__ import annotations

import math
import unittest
from fractions import Fraction

import numpy as np

from buller.nord2000 import delmodell7 as d7
from buller.nord2000.delmodell7 import Spridningsgeometri
from buller.nord2000.frekvenser import exp_modifierad, ljudhastighet_m_s, mittfrekvenser_hz
from buller.nord2000.koherens import koherens_turbulens
from buller.typer import BullerFel

T_C = 15.0
CV2 = 0.12  # m^(4/3)/s², testfallens värde (RL 18/18)
CT2 = 0.008  # K²/m^(2/3)
SYMMETRISK = Spridningsgeometri(40.0, 40.0, 20.0)
SNED = Spridningsgeometri(40.0, 100.0, 10.0)


class TestTabeller(unittest.TestCase):
    def test_form_och_radsummor(self) -> None:
        """Radsummorna är räknade ur textutdraget av AV 1849/00 bilaga G (oberoende avläsning)."""
        summor_ws = (-275.9, -360.0, -414.0, -450.9, -475.1, -491.9, -503.2, -513.3)
        summor_ts = (-334.1, -394.9, -425.7, -442.8, -454.1, -462.8, -470.6, -477.5)
        for tabell, summor in ((d7.LWS0_DB, summor_ws), (d7.LTS0_DB, summor_ts)):
            self.assertEqual(len(tabell), len(d7.TABELL_HE_KVOT))
            for rad, summa in zip(tabell, summor, strict=True):
                self.assertEqual(len(rad), len(d7.TABELL_R2_KVOT))
                self.assertAlmostEqual(sum(rad), summa, places=9)

    def test_tabellerna_avtar_med_hojd_och_vaxer_med_r2(self) -> None:
        for tabell in (d7.LWS0_DB, d7.LTS0_DB):
            a = np.array(tabell)
            self.assertTrue(bool(np.all(np.diff(a, axis=0) < 0.0)))
            self.assertTrue(bool(np.all(np.diff(a, axis=1) > 0.0)))

    def test_tabellpunkter(self) -> None:
        """R₁ = 40 m gör ingångarna lika med R₂ och h_e i meter."""
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LWS0_DB, 40.0, 40.0, 20.0), -46.8)
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LTS0_DB, 40.0, 40.0, 20.0), -45.2)
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LWS0_DB, 40.0, 10.0, 5.0), -41.6)
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LWS0_DB, 40.0, 100.0, 40.0), -46.6)
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LTS0_DB, 40.0, 100.0, 5.0), -28.3)
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LTS0_DB, 40.0, 10.0, 40.0), -54.6)
        # Samma kvoter med annan skala: R₁ = 8 m, R₂ = 14 m, h_e = 5 m ger (70, 25).
        self.assertAlmostEqual(
            d7.normerad_spridningsniva_db(d7.LWS0_DB, 8.0, 14.0, 5.0), -45.1, places=9
        )

    def test_linjar_interpolation(self) -> None:
        mitt = d7.normerad_spridningsniva_db(d7.LWS0_DB, 40.0, 15.0, 7.5)
        self.assertAlmostEqual(mitt, (-41.6 - 33.9 - 49.9 - 44.0) / 4.0, places=12)
        langs_rad = d7.normerad_spridningsniva_db(d7.LTS0_DB, 40.0, 16.0, 5.0)
        self.assertAlmostEqual(langs_rad, -44.0 + 0.6 * (-39.1 + 44.0), places=12)

    def test_narmaste_varde_utanfor_tabellen(self) -> None:
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LWS0_DB, 40.0, 1.0, 0.1), -41.6)
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LWS0_DB, 40.0, 5000.0, 900.0), -46.6)
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LTS0_DB, 40.0, 500.0, 1.0), -28.3)
        self.assertEqual(d7.normerad_spridningsniva_db(d7.LTS0_DB, 40.0, 2.0, 77.0), -54.6)

    def test_bada_hall_ekv_272(self) -> None:
        """Handräknat: framåt −29,3; bakåt −41,6 + 0,6·7,7 + 10·lg(2,5) = −33,00."""
        bakat = -41.6 + 0.6 * (41.6 - 33.9) + 10.0 * math.log10(2.5)
        self.assertAlmostEqual(bakat, -33.0006, places=3)
        self.assertAlmostEqual(
            d7.normerad_spridningsniva_bada_hall_db(d7.LWS0_DB, SNED), -29.3, places=12
        )
        self.assertAlmostEqual(
            d7.normerad_spridningsniva_bada_hall_db(d7.LWS0_DB, SNED.omvand()), -29.3, places=12
        )


class TestGeometri(unittest.TestCase):
    def test_figur_26(self) -> None:
        geo = d7.spridningsgeometri(0.0, 0.0, 80.0, 0.0, 30.0, 12.0)
        self.assertAlmostEqual(geo.r1_m, 30.0, places=12)
        self.assertAlmostEqual(geo.r2_m, 50.0, places=12)
        self.assertAlmostEqual(geo.he_m, 12.0, places=12)

    def test_lutande_siktlinje(self) -> None:
        """h_e mäts vinkelrätt mot linjen S–R och R₁ + R₂ = |SR|."""
        geo = d7.spridningsgeometri(0.0, 0.0, 40.0, 30.0, 25.0, 0.0)
        self.assertAlmostEqual(geo.he_m, -15.0, places=12)
        self.assertAlmostEqual(geo.r1_m, 20.0, places=12)
        self.assertAlmostEqual(geo.r1_m + geo.r2_m, 50.0, places=12)
        self.assertFalse(geo.skarmad)

    def test_fotpunkt_utanfor_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            d7.spridningsgeometri(0.0, 0.0, 80.0, 0.0, -1.0, 5.0)
        with self.assertRaises(BullerFel):
            d7.spridningsgeometri(0.0, 0.0, 80.0, 0.0, 80.0, 5.0)
        with self.assertRaises(BullerFel):
            d7.spridningsgeometri(80.0, 0.0, 0.0, 0.0, 40.0, 5.0)
        with self.assertRaises(BullerFel):
            Spridningsgeometri(0.0, 10.0, 1.0)

    def test_spridningsvinkel_som_tryckt(self) -> None:
        for geo in (SYMMETRISK, SNED, Spridningsgeometri(3.0, 250.0, 0.4)):
            tryckt = math.pi - math.atan(geo.r1_m / geo.he_m) - math.atan(geo.r2_m / geo.he_m)
            self.assertAlmostEqual(d7.spridningsvinkel_rad(geo), tryckt, places=13)

    def test_gransfrekvens(self) -> None:
        """Symmetriskt fall: sin(θ/2) = h_e/√(R₁² + h_e²) = 1/√5."""
        c = 20.05 * math.sqrt(T_C + 273.15)
        self.assertAlmostEqual(
            d7.gransfrekvens_hz(SYMMETRISK, T_C), c * math.sqrt(5.0) / 2.0, places=9
        )

    def test_homogen_atmosfar_ger_s_och_r_tillbaka(self) -> None:
        """Ekv. (275) med θ₁, θ₂ ur ekv. (162), Δθ = 0 och räta strålar."""
        x_s, z_s, x_r, z_r, x_t, z_t = 0.0, 0.5, 120.0, 4.0, 35.0, 6.0
        theta1 = math.atan((z_s - z_t) / (x_t - x_s)) + math.pi / 2.0
        theta2 = math.atan((z_r - z_t) / (x_r - x_t)) + math.pi / 2.0
        r_s = math.hypot(x_t - x_s, z_t - z_s)
        r_r = math.hypot(x_r - x_t, z_r - z_t)
        punkter = d7.korrigerad_kalla_och_mottagare(
            x_t, z_t, x_t, z_t, r_s, r_r, theta1, theta2, 0.0, 0.0
        )
        for fatt, vantat in zip(punkter, (x_s, z_s, x_r, z_r), strict=True):
            self.assertAlmostEqual(fatt, vantat, places=11)

    def test_refraktionskorrektion_bevarar_avstand_och_vrider(self) -> None:
        x_t, z_t, r_s, r_r = 35.0, 6.0, 30.0, 70.0
        x_s, z_s, x_r, z_r = d7.korrigerad_kalla_och_mottagare(
            x_t, z_t, x_t, z_t, r_s, r_r, 1.4, 1.5, 0.03, -0.02
        )
        self.assertAlmostEqual(math.hypot(x_t - x_s, z_t - z_s), r_s, places=12)
        self.assertAlmostEqual(math.hypot(x_r - x_t, z_r - z_t), r_r, places=12)
        self.assertAlmostEqual(math.atan2(x_t - x_s, z_t - z_s), 1.43, places=12)
        self.assertAlmostEqual(math.atan2(x_r - x_t, z_t - z_r), 1.48, places=12)
        with self.assertRaises(BullerFel):
            d7.korrigerad_kalla_och_mottagare(x_t, z_t, x_t, z_t, 0.0, r_r, 1.4, 1.5, 0.0, 0.0)

    def test_tva_kanter_ger_linjernas_skarning(self) -> None:
        """Figur 27: T ligger där linjerna S–T₁ och R–T₂ skär varandra."""
        x_s, z_s, x_r, z_r = 0.0, 1.0, 100.0, 3.0
        x_t1, z_t1, x_t2, z_t2 = 30.0, 6.0, 55.0, 8.0
        geo = d7.spridningsgeometri_tva_kanter(x_s, z_s, x_r, z_r, x_t1, z_t1, x_t2, z_t2)
        a = np.array([[x_t1 - x_s, x_r - x_t2], [z_t1 - z_s, z_r - z_t2]])
        s, _ = np.linalg.solve(a, np.array([x_r - x_s, z_r - z_s]))
        x_t, z_t = x_s + s * (x_t1 - x_s), z_s + s * (z_t1 - z_s)
        direkt = d7.spridningsgeometri(x_s, z_s, x_r, z_r, float(x_t), float(z_t))
        self.assertAlmostEqual(geo.r1_m, direkt.r1_m, places=10)
        self.assertAlmostEqual(geo.r2_m, direkt.r2_m, places=10)
        self.assertAlmostEqual(geo.he_m, direkt.he_m, places=10)
        self.assertGreater(geo.he_m, max(6.0 - 1.6, 8.0 - 2.1))

    def test_tva_sammanfallande_kanter_ar_en_kant(self) -> None:
        en = d7.spridningsgeometri(0.0, 1.0, 100.0, 3.0, 40.0, 7.0)
        tva = d7.spridningsgeometri_tva_kanter(0.0, 1.0, 100.0, 3.0, 40.0, 7.0, 40.0, 7.0)
        for fatt, vantat in zip(
            (tva.r1_m, tva.r2_m, tva.he_m), (en.r1_m, en.r2_m, en.he_m), strict=True
        ):
            self.assertAlmostEqual(fatt, vantat, places=11)

    def test_tva_kanter_grans_och_fel(self) -> None:
        under = d7.spridningsgeometri_tva_kanter(0.0, 2.0, 100.0, 2.0, 30.0, 1.0, 60.0, 5.0)
        self.assertFalse(under.skarmad)
        # h_e → 0 kontinuerligt när den ena kanten närmar sig siktlinjen uppifrån.
        nara = d7.spridningsgeometri_tva_kanter(0.0, 2.0, 100.0, 2.0, 30.0, 2.0 + 1e-9, 60.0, 5.0)
        self.assertLess(nara.he_m, 1e-8)
        with self.assertRaises(BullerFel):
            d7.spridningsgeometri_tva_kanter(0.0, 2.0, 100.0, 2.0, 60.0, 5.0, 30.0, 5.0)


class TestTurbulensspridning(unittest.TestCase):
    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()

    def test_ingen_turbulens_ger_inget_bidrag(self) -> None:
        dl = d7.turbulensspridning_band_db(self.f, SYMMETRISK, 0.0, 0.0, T_C)
        self.assertTrue(bool(np.all(dl == -math.inf)))
        # Neutralt i ekv. (332): ΔL₄ ⊕ ΔL₇ = ΔL₄.
        self.assertEqual(d7.energisumma_db(-17.3, float(dl[10])), -17.3)
        self.assertEqual(d7.energisumma_db(-math.inf, -math.inf), -math.inf)

    def test_kant_pa_eller_under_siktlinjen_ger_inget_bidrag(self) -> None:
        for z_t in (2.0, 1.0, -5.0):
            dl = d7.turbulensspridning_en_kant_db(
                self.f, 0.0, 2.0, 80.0, 2.0, 30.0, z_t, CV2, CT2, T_C
            )
            self.assertTrue(bool(np.all(dl == -math.inf)))
        # Strax ovanför linjen: bidraget finns men är försumbart (f₀ → ∞).
        dl = d7.turbulensspridning_en_kant_db(
            self.f, 0.0, 2.0, 80.0, 2.0, 30.0, 2.0 + 1e-6, CV2, CT2, T_C
        )
        self.assertTrue(bool(np.all(np.isfinite(dl))))
        self.assertLess(float(dl.max()), -150.0)

    def test_handraknat_vid_2000_hz(self) -> None:
        """R₁ = R₂ = 40 m, h_e = 20 m, f = 2000 Hz > f₀ = 380 Hz, 10·C² = 1.

        Alla termer utom tabellvärdet är noll: ΔL_ws = −46,8 dB och ΔL_ts = −45,2 dB.
        """
        self.assertLess(d7.gransfrekvens_hz(SYMMETRISK, T_C), 2000.0)
        self.assertAlmostEqual(d7.vindspridning_db(2000.0, SYMMETRISK, 0.1, T_C), -46.8, places=12)
        self.assertAlmostEqual(
            d7.temperaturspridning_db(2000.0, SYMMETRISK, 0.1, T_C), -45.2, places=12
        )
        summa = 10.0 * math.log10(10.0**-4.68 + 10.0**-4.52)
        self.assertAlmostEqual(
            d7.turbulensspridning_db(2000.0, SYMMETRISK, 0.1, 0.1, T_C), summa, places=12
        )
        # Med testfallens styrkor: 10·lg(1,2) resp. 10·lg(0,08) läggs till.
        self.assertAlmostEqual(
            d7.vindspridning_db(2000.0, SYMMETRISK, CV2, T_C),
            -46.8 + 10.0 * math.log10(1.2),
            places=12,
        )

    def test_handraknat_under_halva_gransfrekvensen(self) -> None:
        """f = f₀/4: 15·lg(1/4) + 15·lg(1/2) och (10/3)·lg(f/2000) läggs till."""
        f0 = d7.gransfrekvens_hz(SYMMETRISK, T_C)
        f = f0 / 4.0
        vantat = -46.8 + 10.0 / 3.0 * math.log10(f / 2000.0) + 15.0 * math.log10(1.0 / 8.0)
        self.assertAlmostEqual(d7.vindspridning_db(f, SYMMETRISK, 0.1, T_C), vantat, places=10)

    def test_frekvensberoende(self) -> None:
        """Över f₀: 10/3 dB per dekad (k^(1/3)). Under 0,5·f₀: 10/3 + 30 dB per dekad."""
        f0 = d7.gransfrekvens_hz(SNED, T_C)

        def niva(f: float) -> float:
            return d7.turbulensspridning_db(f, SNED, CV2, CT2, T_C)

        self.assertAlmostEqual(niva(80.0 * f0) - niva(8.0 * f0), 10.0 / 3.0, places=10)
        self.assertAlmostEqual(niva(0.4 * f0) - niva(0.04 * f0), 10.0 / 3.0 + 30.0, places=10)
        self.assertAlmostEqual(
            niva(0.9 * f0) - niva(0.6 * f0), (10.0 / 3.0 + 15.0) * math.log10(1.5), places=10
        )
        for brytpunkt in (f0, 0.5 * f0):
            self.assertAlmostEqual(
                niva(brytpunkt * (1.0 - 1e-9)), niva(brytpunkt * (1.0 + 1e-9)), places=6
            )
        band = d7.turbulensspridning_band_db(self.f, SNED, CV2, CT2, T_C)
        self.assertTrue(bool(np.all(np.diff(band) > 0.0)))

    def test_vaxer_monotont_och_linjart_med_turbulensstyrkan(self) -> None:
        styrkor = [1e-4, 1e-3, 0.01, 0.05, 0.12, 0.5, 2.0]
        for f in (63.0, 500.0, 4000.0):
            vind = [d7.turbulensspridning_db(f, SNED, s, 0.0, T_C) for s in styrkor]
            temp = [d7.turbulensspridning_db(f, SNED, 0.0, s, T_C) for s in styrkor]
            bada = [d7.turbulensspridning_db(f, SNED, s, CT2, T_C) for s in styrkor]
            for serie in (vind, temp, bada):
                self.assertTrue(all(b > a for a, b in zip(serie, serie[1:], strict=False)))
            # Energin är proportionell mot styrkan: dubbel styrka ger +3,01 dB.
            dubbel = d7.turbulensspridning_db(f, SNED, 2.0 * CV2, 2.0 * CT2, T_C)
            enkel = d7.turbulensspridning_db(f, SNED, CV2, CT2, T_C)
            self.assertAlmostEqual(dubbel - enkel, 10.0 * math.log10(2.0), places=10)
            # Vind- och temperaturbidragen adderas som energier (ekv. 274).
            e_v = 10.0 ** (d7.vindspridning_db(f, SNED, CV2, T_C) / 10.0)
            e_t = 10.0 ** (d7.temperaturspridning_db(f, SNED, CT2, T_C) / 10.0)
            self.assertAlmostEqual(10.0 ** (enkel / 10.0), e_v + e_t, delta=1e-12 * (e_v + e_t))

    def test_bidraget_ar_alltid_minst_det_storsta_delbidraget(self) -> None:
        for f in self.f:
            v = d7.vindspridning_db(float(f), SNED, CV2, T_C)
            t = d7.temperaturspridning_db(float(f), SNED, CT2, T_C)
            self.assertGreaterEqual(
                d7.turbulensspridning_db(float(f), SNED, CV2, CT2, T_C), max(v, t)
            )

    def test_reciprocitet(self) -> None:
        """Källa och mottagare ombytta (spegling x → −x) ger samma ΔL₇ i alla band."""
        fall = [
            (0.0, 0.5, 200.0, 1.5, 12.0, 4.0),
            (0.0, 0.01, 60.0, 4.0, 50.0, 6.0),
            (0.0, 2.0, 1000.0, 2.0, 30.0, 3.0),
            (0.0, 0.3, 35.0, 1.5, 20.0, 12.0),
        ]
        for x_s, z_s, x_r, z_r, x_t, z_t in fall:
            fram = d7.turbulensspridning_en_kant_db(
                self.f, x_s, z_s, x_r, z_r, x_t, z_t, CV2, CT2, T_C
            )
            bak = d7.turbulensspridning_en_kant_db(
                self.f, -x_r, z_r, -x_s, z_s, -x_t, z_t, CV2, CT2, T_C
            )
            self.assertTrue(bool(np.all(np.isfinite(fram))))
            np.testing.assert_allclose(bak, fram, rtol=0.0, atol=1e-9)

    def test_reciprocitet_tva_kanter(self) -> None:
        fram = d7.turbulensspridning_tva_kanter_db(
            self.f, 0.0, 1.0, 100.0, 3.0, 30.0, 6.0, 55.0, 8.0, CV2, CT2, T_C
        )
        bak = d7.turbulensspridning_tva_kanter_db(
            self.f, -100.0, 3.0, 0.0, 1.0, -55.0, 8.0, -30.0, 6.0, CV2, CT2, T_C
        )
        np.testing.assert_allclose(bak, fram, rtol=0.0, atol=1e-9)

    def test_tabellen_ensam_ar_inte_reciprok(self) -> None:
        """Skälet till max-uttrycket i ekv. (272): utan det skiljer hållen sig åt."""
        fram = d7.normerad_spridningsniva_db(d7.LWS0_DB, 40.0, 100.0, 10.0)
        bak = d7.normerad_spridningsniva_db(d7.LWS0_DB, 100.0, 40.0, 10.0) + 10.0 * math.log10(2.5)
        self.assertGreater(abs(fram - bak), 3.0)

    def test_skalning_dimensionskontroll(self) -> None:
        """Dubbla alla längder: tabellingångar, θ och f₀ är oförändrade, nivån ökar 10·lg 2.

        Det är enkelspridningens skalning: C²/c²·k^(1/3)·R är dimensionslöst.
        """
        stor = Spridningsgeometri(2.0 * SNED.r1_m, 2.0 * SNED.r2_m, 2.0 * SNED.he_m)
        self.assertAlmostEqual(
            d7.gransfrekvens_hz(stor, T_C), d7.gransfrekvens_hz(SNED, T_C), places=9
        )
        skillnad = d7.turbulensspridning_band_db(
            self.f, stor, CV2, CT2, T_C
        ) - d7.turbulensspridning_band_db(self.f, SNED, CV2, CT2, T_C)
        np.testing.assert_allclose(skillnad, 10.0 * math.log10(2.0), rtol=0.0, atol=1e-10)

    def test_markkorrektion(self) -> None:
        self.assertEqual(d7.markkorrektion_csr(0.0), 1.0)
        self.assertEqual(d7.markkorrektion_csr(0.3), 1.0)
        self.assertEqual(d7.markkorrektion_csr(1.0), 1.0)
        self.assertEqual(d7.markkorrektion_csr(2.0), 4.0)
        utan = d7.turbulensspridning_band_db(self.f, SNED, CV2, CT2, T_C)
        dampad = d7.turbulensspridning_band_db(self.f, SNED, CV2, CT2, T_C, np.full(27, 0.1))
        forstarkt = d7.turbulensspridning_band_db(self.f, SNED, CV2, CT2, T_C, np.full(27, 2.0))
        np.testing.assert_array_equal(dampad, utan)
        np.testing.assert_allclose(forstarkt - utan, 20.0 * math.log10(2.0), rtol=0.0, atol=1e-10)

    def test_storleksordning_med_testfallens_turbulens(self) -> None:
        """Bidraget är litet men ändligt: mellan −80 och −20 dB re frifält i alla band."""
        dl = d7.turbulensspridning_band_db(self.f, SNED, CV2, CT2, T_C)
        self.assertGreater(float(dl.min()), -80.0)
        self.assertLess(float(dl.max()), -20.0)

    def test_felaktiga_indata(self) -> None:
        with self.assertRaises(BullerFel):
            d7.turbulensspridning_db(1000.0, SNED, -0.1, 0.0, T_C)
        with self.assertRaises(BullerFel):
            d7.turbulensspridning_db(1000.0, SNED, 0.1, -1e-9, T_C)
        with self.assertRaises(BullerFel):
            d7.turbulensspridning_db(1000.0, SNED, math.nan, 0.0, T_C)
        with self.assertRaises(BullerFel):
            d7.turbulensspridning_db(0.0, SNED, CV2, CT2, T_C)
        with self.assertRaises(BullerFel):
            d7.turbulensspridning_db(1000.0, SNED, CV2, CT2, T_C, -1.0)
        with self.assertRaises(BullerFel):
            d7.turbulensspridning_band_db(self.f, SNED, CV2, CT2, T_C, np.ones(3))
        with self.assertRaises(BullerFel):
            d7.normerad_spridningsniva_db(d7.LWS0_DB, 40.0, 40.0, 0.0)
        with self.assertRaises(BullerFel):
            d7.spridningsvinkel_rad(Spridningsgeometri(40.0, 40.0, -1.0))


# Dimensioner som exponenter för (meter, sekund, kelvin).
Dimension = tuple[Fraction, Fraction, Fraction]


def _dim(m: Fraction | int = 0, s: Fraction | int = 0, k: Fraction | int = 0) -> Dimension:
    return (Fraction(m), Fraction(s), Fraction(k))


def _gang(*faktorer: tuple[Dimension, Fraction | int]) -> Dimension:
    """Dimensionen av en produkt av storheter upphöjda till givna exponenter."""
    return (
        sum((d[0] * Fraction(p) for d, p in faktorer), Fraction(0)),
        sum((d[1] * Fraction(p) for d, p in faktorer), Fraction(0)),
        sum((d[2] * Fraction(p) for d, p in faktorer), Fraction(0)),
    )


class TestDimensionerEkv113(unittest.TestCase):
    """Dimensionskontroll av F_c, AV 1106/07 ekv. (113), och av delmodell 7.

    C_v² [m^(4/3)/s²] enligt AV 1106/07 avsnitt 3; C_T² [K²/m^(2/3)] enligt strukturparameterns
    definition (D_T(r) = C_T²·r^(2/3)) och KCB:s användarhandledning 2026.
    """

    CV2 = _dim(Fraction(4, 3), -2)
    CT2 = _dim(Fraction(-2, 3), 0, 2)
    C = _dim(1, -1)
    T = _dim(0, 0, 1)
    F = _dim(0, -1)
    LANGD = _dim(1)
    DIMENSIONSLOS = _dim()

    def _exponent(self, turbulensterm: Dimension) -> Dimension:
        """Termen gånger (f/c)²·ρ^(5/3)·d."""
        return _gang(
            (turbulensterm, 1),
            (self.F, 2),
            (self.C, -2),
            (self.LANGD, Fraction(5, 3)),
            (self.LANGD, 1),
        )

    def test_vindtermen_ar_dimensionslos(self) -> None:
        self.assertEqual(self._exponent(_gang((self.CV2, 1), (self.C, -2))), self.DIMENSIONSLOS)

    def test_temperaturtermen_kraver_kvadrat(self) -> None:
        med_kvadrat = _gang((self.CT2, 1), (self.T, -2))
        som_tryckt = _gang((self.CT2, 1), (self.T, -1))
        self.assertEqual(self._exponent(med_kvadrat), self.DIMENSIONSLOS)
        self.assertEqual(med_kvadrat, _gang((self.CV2, 1), (self.C, -2)))
        self.assertEqual(self._exponent(som_tryckt), _dim(0, 0, 1))  # kelvin blir över

    def test_tryckt_enhet_for_ct2_raddar_inte_den_tryckta_formeln(self) -> None:
        """Med enheten K·s⁻² (AV 1106/07 avsnitt 3) går ingen av formerna ihop med vindtermen."""
        ct2_tryckt = _dim(0, -2, 1)
        vindterm = _gang((self.CV2, 1), (self.C, -2))
        self.assertNotEqual(_gang((ct2_tryckt, 1), (self.T, -1)), vindterm)
        self.assertNotEqual(_gang((ct2_tryckt, 1), (self.T, -2)), vindterm)

    def test_konstanten_hor_till_formen_i_av_1849(self) -> None:
        """5,3888 = (3/8)·0,364·(2π)²: ekv. (113) är AV 1849/00 ekv. (141) med k = 2πf/c."""
        self.assertAlmostEqual(3.0 / 8.0 * 0.364 * (2.0 * math.pi) ** 2, 5.3888, places=4)

    def test_befintlig_kod_foljer_av_1849_ekv_141(self) -> None:
        f, t_c, rho, d = 1000.0, 15.0, 0.5, 100.0
        c = ljudhastighet_m_s(t_c)
        k = 2.0 * math.pi * f / c
        t0 = t_c + 273.15
        x = -3.0 / 8.0 * 0.364 * (CT2 / t0**2 + 22.0 / 3.0 * CV2 / c**2) * k**2 * rho ** (5 / 3) * d
        self.assertAlmostEqual(
            koherens_turbulens(f, CV2, CT2, t_c, c, rho, d), exp_modifierad(x), places=5
        )

    def test_delmodell_7_skalning_ar_dimensionslos(self) -> None:
        """10·lg(C²) + (10/3)·lg f + 10·lg R₁ motsvarar (C²/c²)·k^(1/3)·R, som är dimensionslöst.

        Gäller C_v²/c² och C_T²/T² lika; tabellerna 6 och 7 bär alltså c² respektive T².
        """
        vagtal = _gang((self.F, 1), (self.C, -1))
        for term in (_gang((self.CV2, 1), (self.C, -2)), _gang((self.CT2, 1), (self.T, -2))):
            self.assertEqual(
                _gang((term, 1), (vagtal, Fraction(1, 3)), (self.LANGD, 1)), self.DIMENSIONSLOS
            )


if __name__ == "__main__":
    unittest.main()
