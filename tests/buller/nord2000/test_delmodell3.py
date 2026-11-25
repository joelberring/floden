"""Tester för delmodell 3 (icke-plan terräng utan skärm, räta strålar) och r_flat.

Svaren följer av AV 1106/07 själv (definitioner, gränsfall, räkneexempel för hand) eller av
oberoende teori (ellipsvillkoret, reciprocitet, kontinuitet). Inget är jämfört med facit.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import numpy as np

from buller.nord2000.atmosfar import HomogenAtmosfar
from buller.nord2000.delmodell1 import markeffekt_plan_mark_db
from buller.nord2000.delmodell3 import (
    F_LAMBDA_DELMODELL3,
    KONKAVT,
    KONVEXT,
    OVERGANG,
    Delmodell3Resultat,
    delmodell3,
    ekvivalent_kil,
    fresnelgransavvikelse_m,
    fresnelzonsvikt_delmodell3,
    kilkryss,
    klassa_segment,
    konvex_fresnelgeometri,
    markeffekt_icke_plan_terrang_db,
    markeffekt_konvext_segment_db,
    markeffekt_utan_skarm_db,
    minsta_konkava_hojd_m,
    overgang_rf,
    overgang_rflat,
    planhetsparameter_rflat,
    relativ_hojd,
    segmentvariabler,
    segmentvariabler_for,
    vag_samman_segment_db,
)
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.fresnel import fresnelzonsvikt
from buller.nord2000.karna import markeffekt_plan_modell_db, marksegment_over_plan_mark
from buller.nord2000.kildiffraktion import kil_rata_stralar, kiltryck_impedans
from buller.nord2000.mark import markimpedans
from buller.nord2000.refraktion import Atmosfar
from buller.nord2000.terrang import (
    Terranggeometri,
    ekvivalent_plan_mark,
    geometri_over_plan_mark,
    terranggeometri,
)
from buller.typer import BullerFel, Flyttal, Terrangprofil

STILLA = HomogenAtmosfar(15.0, 70.0, 101.325, 0.0, 0.0)
TURBULENT = HomogenAtmosfar(15.0, 70.0, 101.325, 0.12, 0.008)
C = STILLA.ljudhastighet_m_s
TESTSVIT = Path(__file__).resolve().parents[3] / "testsvit" / "N_nord2000"


def _profil(
    x: list[float],
    z: list[float],
    sigma: float | list[float],
    h_s: float = 0.5,
    h_r: float = 1.5,
    rahet: list[float] | None = None,
) -> Terrangprofil:
    n = len(x) - 1
    sigmor = [sigma] * n if isinstance(sigma, float) else sigma
    return Terrangprofil(
        x_m=np.array(x, dtype=np.float64),
        z_m=np.array(z, dtype=np.float64),
        flodesresistivitet_kNsm4=np.array(sigmor, dtype=np.float64),
        rahet_m=np.array(rahet if rahet is not None else [0.0] * n, dtype=np.float64),
        kalla_hojd_m=h_s,
        mottagare_hojd_m=h_r,
    )


def _spegla(p: Terrangprofil) -> Terrangprofil:
    """Samma terräng sedd från mottagaren: källa och mottagare byter plats."""
    langd = float(p.x_m[-1])
    return Terrangprofil(
        x_m=(langd - p.x_m)[::-1].copy(),
        z_m=p.z_m[::-1].copy(),
        flodesresistivitet_kNsm4=p.flodesresistivitet_kNsm4[::-1].copy(),
        rahet_m=p.rahet_m[::-1].copy(),
        kalla_hojd_m=p.mottagare_hojd_m,
        mottagare_hojd_m=p.kalla_hojd_m,
    )


def _plan_modell(p: Terrangprofil, luft: HomogenAtmosfar, f: Flyttal) -> tuple[Flyttal, str]:
    """ΔL_flat som kärnan räknar den (delmodell 1 eller 2 över ekvivalent plan mark)."""
    geo = terranggeometri(p)
    over_plan = geometri_over_plan_mark(geo, ekvivalent_plan_mark(geo.x_m, geo.z_m))
    return markeffekt_plan_modell_db(f, over_plan, marksegment_over_plan_mark(geo, over_plan), luft)


class TestSegmentvariabler(unittest.TestCase):
    """SegmentVariables, avsnitt 5.23.15."""

    def test_vagratt_segment(self) -> None:
        v = segmentvariabler(0.0, 3.0, 100.0, 5.0, 10.0, 1.0, 30.0, 1.0)
        for varde, vantat in zip(
            (v.d_m, v.h_s_m, v.h_r_m, v.d1_m, v.d2_m), (100.0, 2.0, 4.0, 10.0, 30.0), strict=True
        ):
            self.assertAlmostEqual(varde, vantat, places=12)

    def test_lutande_segment_3_4_5(self) -> None:
        """Linjen genom (0, 0) och (4, 3); S och R är lagda 5 m och 2 m vinkelrätt över den."""
        v = segmentvariabler(-3.0, 4.0, 6.8, 7.6, 4.0, 3.0, 8.0, 6.0)
        self.assertAlmostEqual(v.h_s_m, 5.0, places=12)
        self.assertAlmostEqual(v.h_r_m, 2.0, places=12)
        self.assertAlmostEqual(v.d_m, 10.0, places=12)
        self.assertAlmostEqual(v.d1_m, 5.0, places=12)
        self.assertAlmostEqual(v.d2_m, 10.0, places=12)

    def test_tecken_bakom_kallan_och_bortom_mottagaren(self) -> None:
        v = segmentvariabler(0.0, 3.0, 100.0, 5.0, -5.0, 1.0, 120.0, 1.0)
        self.assertAlmostEqual(v.d1_m, -5.0, places=12)  # d'_1 < 0: bakom källans fotpunkt
        self.assertAlmostEqual(v.d2_m, 120.0, places=12)  # d'_2 > d': bortom mottagaren

    def test_kalla_under_segmentets_forlangning_ger_negativ_hojd(self) -> None:
        v = segmentvariabler(0.0, 0.5, 100.0, 5.0, 10.0, 1.0, 30.0, 1.0)
        self.assertAlmostEqual(v.h_s_m, -0.5, places=12)

    def test_omvand_ordning_vagras(self) -> None:
        with self.assertRaises(BullerFel):
            segmentvariabler(0.0, 1.0, 10.0, 1.0, 5.0, 0.0, 5.0, 0.0)


class TestMinstaKonkavaHojd(unittest.TestCase):
    """MinConcaveHeight, avsnitt 5.23.12."""

    def test_andpunkten_ligger_pa_fresnelzonens_grans(self) -> None:
        """Definitionen i figur 40: |SP_S| + |P_S R'| − |SR'| = F_λ·λ vid den höjd som ges."""
        rng = np.random.default_rng(12)
        storst = 0.0
        antal = 0
        for _ in range(4000):
            d = float(rng.uniform(1.0, 2000.0))
            d1 = float(rng.uniform(0.0, d))
            h = float(10.0 ** rng.uniform(-2.0, 1.5))
            f_lambda = float(10.0 ** rng.uniform(-3.3, 0.0))
            hojd = minsta_konkava_hojd_m(h, d1, d, f_lambda)
            if math.isinf(hojd):
                self.assertLessEqual(math.hypot(d - d1, h) - h, f_lambda)  # ekv. (374)
                continue
            antal += 1
            self.assertGreater(hojd, 0.0)
            storst = max(storst, abs(fresnelgransavvikelse_m(hojd, h, d1, d, f_lambda)) / f_lambda)
        self.assertGreater(antal, 3000)
        self.assertLess(storst, 1.0e-6)

    def test_over_hojden_ligger_andpunkten_utanfor_zonen(self) -> None:
        h, d1, d, f_lambda = 1.5, 30.0, 100.0, 0.05
        hojd = minsta_konkava_hojd_m(h, d1, d, f_lambda)
        self.assertGreater(fresnelgransavvikelse_m(1.01 * hojd, h, d1, d, f_lambda), 0.0)
        self.assertLess(fresnelgransavvikelse_m(0.99 * hojd, h, d1, d, f_lambda), 0.0)

    def test_oandlig_nar_ekv_374_ar_uppfylld(self) -> None:
        # √(d2² + h²) − h = √(3² + 4²) − 4 = 1 ≤ F·λ = 1
        self.assertTrue(math.isinf(minsta_konkava_hojd_m(4.0, 97.0, 100.0, 1.0)))
        self.assertFalse(math.isinf(minsta_konkava_hojd_m(4.0, 97.0, 100.0, 0.999)))

    def test_hojden_vaxer_med_vaglangden(self) -> None:
        hojder = [minsta_konkava_hojd_m(2.0, 20.0, 150.0, fl) for fl in (0.002, 0.02, 0.2, 0.8)]
        self.assertEqual(hojder, sorted(hojder))

    def test_andpunkt_bakom_fotpunkten_utan_rot_ger_noll(self) -> None:
        self.assertEqual(minsta_konkava_hojd_m(0.01, -0.3, 99.8, 0.53), 0.0)

    def test_ogiltig_indata(self) -> None:
        for argument in ((-1.0, 10.0, 100.0, 0.1), (1.0, 10.0, 0.0, 0.1), (1.0, 10.0, 100.0, 0.0)):
            with self.assertRaises(BullerFel):
                minsta_konkava_hojd_m(*argument)


class TestKilkryss(unittest.TestCase):
    """WedgeCross, avsnitt 5.23.19."""

    def test_skarning_mellan_segmenten(self) -> None:
        """z = x och z = 10 − x möts i (5, 5)."""
        x0, z0 = kilkryss(0.0, 0.0, 2.0, 2.0, 8.0, 2.0, 10.0, 0.0)
        self.assertAlmostEqual(x0, 5.0, places=12)
        self.assertAlmostEqual(z0, 5.0, places=12)

    def test_skarning_pa_kallsidans_forlangning(self) -> None:
        """z = 0 och z = 7 − 0,15·x möts i x = 46,67 (används i kiltestet nedan)."""
        x0, z0 = kilkryss(0.0, 0.0, 40.0, 0.0, 50.0, -0.5, 100.0, -8.0)
        self.assertAlmostEqual(x0, 7.0 / 0.15, places=10)
        self.assertAlmostEqual(z0, 0.0, places=12)

    def test_t_under_1_ger_mottagarsegmentets_start(self) -> None:
        # Förlängningarna möts i x = 8,5, inne i mottagarsidans segment.
        self.assertEqual(kilkryss(0.0, 0.0, 2.0, 2.0, 8.0, 9.0, 10.0, 7.0), (8.0, 9.0))

    def test_s_under_1_ger_kallsegmentets_slut(self) -> None:
        # Förlängningarna möts i x = 1,5, inne i källsidans segment.
        self.assertEqual(kilkryss(0.0, 7.0, 2.0, 9.0, 8.0, 2.0, 10.0, 0.0), (2.0, 9.0))

    def test_parallella_segment_och_fel_ordning(self) -> None:
        with self.assertRaises(BullerFel):
            kilkryss(0.0, 0.0, 2.0, 1.0, 8.0, 3.0, 10.0, 4.0)
        with self.assertRaises(BullerFel):
            kilkryss(0.0, 0.0, 9.0, 1.0, 8.0, 3.0, 10.0, 4.0)


class TestRelativHojd(unittest.TestCase):
    """Ekv. (136)–(137)."""

    def test_tre_fall(self) -> None:
        self.assertEqual(relativ_hojd(2.0, 1.5), 1.0)
        self.assertEqual(relativ_hojd(1.5, 1.5), 1.0)
        self.assertAlmostEqual(relativ_hojd(0.6, 1.5), 0.4, places=12)
        self.assertEqual(relativ_hojd(0.0, 1.5), 0.0)
        self.assertEqual(relativ_hojd(-0.2, 1.5), 0.0)
        self.assertEqual(relativ_hojd(0.3, math.inf), 0.0)

    def test_avrundning_vid_gransen_raknas_som_gransen(self) -> None:
        self.assertEqual(relativ_hojd(1.5 * (1.0 - 1.0e-13), 1.5), 1.0)
        self.assertLess(relativ_hojd(1.5 * (1.0 - 1.0e-6), 1.5), 1.0)


class TestFresnelzonsvikt(unittest.TestCase):
    """Ekv. (349)–(351) med regeln i avsnitt 5.8."""

    def test_oklippt_ar_fresnelzonew(self) -> None:
        for f in (25.0, 200.0, 4000.0):
            argument = (100.0, 0.5, 1.5, 10.0, 60.0, F_LAMBDA_DELMODELL3 * C / f)
            self.assertEqual(
                fresnelzonsvikt_delmodell3(*argument, "oklippt"), fresnelzonsvikt(*argument)
            )

    def test_klippt_zon_summerar_till_1_over_plan_mark(self) -> None:
        """Marken mellan fotpunkterna täcker hela den klippta zonen i alla band."""
        granser = [0.0, 3.0, 20.0, 55.0, 100.0]
        for f in mittfrekvenser_hz():
            f_lambda = F_LAMBDA_DELMODELL3 * C / float(f)
            klippt = sum(
                fresnelzonsvikt_delmodell3(100.0, 2.0, 1.5, a, b, f_lambda)
                for a, b in zip(granser[:-1], granser[1:], strict=True)
            )
            self.assertAlmostEqual(klippt, 1.0, places=12)

    def test_oklippt_zon_nar_bakom_kallan_vid_lag_frekvens(self) -> None:
        """Skillnaden mellan reglerna: 10 m, låg källa, 25 Hz – 4 % av zonen ligger bakom källan."""
        f_lambda = F_LAMBDA_DELMODELL3 * C / 25.0
        oklippt = fresnelzonsvikt_delmodell3(10.0, 0.01, 1.5, 0.0, 10.0, f_lambda, "oklippt")
        self.assertLess(oklippt, 0.97)
        self.assertGreater(oklippt, 0.94)
        self.assertEqual(fresnelzonsvikt_delmodell3(10.0, 0.01, 1.5, 0.0, 10.0, f_lambda), 1.0)
        # På 100 m är skillnaden under en halv procent.
        langt = fresnelzonsvikt_delmodell3(100.0, 0.01, 1.5, 0.0, 100.0, f_lambda, "oklippt")
        self.assertGreater(langt, 0.995)

    def test_okand_regel(self) -> None:
        with self.assertRaises(BullerFel):
            fresnelzonsvikt_delmodell3(100.0, 2.0, 1.5, 0.0, 50.0, 0.1, "annan")  # type: ignore[arg-type]


class TestKlassning(unittest.TestCase):
    """Segmenttyper, ekv. (136)–(139) och figur 17."""

    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()

    def _typer(self, p: Terrangprofil, band: int) -> list[str]:
        geo = terranggeometri(p)
        return [
            klassa_segment(geo, i, C / float(self.f[band])).typ for i in range(p.x_m.shape[0] - 1)
        ]

    def test_plan_mark_ar_konkav(self) -> None:
        p = _profil([0.0, 10.0, 35.0, 60.0, 100.0], [0.0] * 5, 200.0)
        for band in (0, 13, 26):
            self.assertEqual(self._typer(p, band), [KONKAVT] * 4)

    def test_dal_ar_konkav(self) -> None:
        """Figur 17 a: h'_S > h_S och h'_R > h_R för alla segment i en dal."""
        p = _profil([0.0, 20.0, 50.0, 80.0, 100.0], [0.0, -3.0, -4.0, -3.0, 0.0], 200.0)
        geo = terranggeometri(p)
        for i in range(1, 3):
            v = segmentvariabler_for(geo, i)
            self.assertGreater(v.h_s_m, geo.h_s_m)
            self.assertGreater(v.h_r_m, geo.h_r_m)
        for band in (0, 13, 26):
            self.assertEqual(self._typer(p, band), [KONKAVT] * 4)

    def test_sluttning_bort_fran_lag_kalla_ar_konvex(self) -> None:
        """Figur 17 b speglad: sluttningens förlängning går över källan (h'_S < 0)."""
        p = _profil([0.0, 50.0, 100.0], [0.0, 0.0, -5.0], 200.0, h_s=0.5, h_r=1.5)
        geo = terranggeometri(p)
        self.assertLess(segmentvariabler_for(geo, 1).h_s_m, 0.0)
        klass = klassa_segment(geo, 1, C / 1000.0)
        self.assertEqual((klass.h_s_rel, klass.h_r_rel, klass.typ), (0.0, 1.0, KONVEXT))
        # Första segmentet: mottagaren ligger under dess förlängning.
        self.assertEqual(klassa_segment(geo, 0, C / 1000.0).typ, KONVEXT)

    def test_overgangssegment_figur_17c(self) -> None:
        """0 < h'_S < h_S: källan 6 m över marken, sluttningens förlängning 1 m under källan."""
        p = _profil([0.0, 50.0, 100.0], [0.0, 0.0, -5.0], 200.0, h_s=6.0, h_r=1.5)
        geo = terranggeometri(p)
        v = segmentvariabler_for(geo, 1)
        self.assertAlmostEqual(v.h_s_m, 1.0 / math.hypot(1.0, 0.1), places=12)
        klass = klassa_segment(geo, 1, C / 100.0)
        self.assertEqual(klass.typ, OVERGANG)
        self.assertAlmostEqual(klass.h_s_rel, v.h_s_m / 6.0, places=12)  # h''_S = h_S här
        self.assertEqual(klass.h_r_rel, 1.0)
        self.assertAlmostEqual(klass.r, v.h_s_m / 6.0, places=12)

    def test_typen_beror_pa_frekvensen(self) -> None:
        """Hyllan börjar 20 m från källan och ligger 0,5 m högre: h'_S = 1,5 m < h_S = 2 m.

        Vid låg frekvens ligger hyllans början inne i Fresnelzonen (h_S,Fz > h_S, övergång med
        h_S,rel = 0,75). Vid hög frekvens ligger den utanför (h_S,Fz < h'_S) och segmentet är
        konkavt. Däremellan växer h_S,rel monotont.
        """
        p = _profil([0.0, 19.0, 20.0, 150.0], [0.0, 0.0, 0.5, 0.5], 200.0, h_s=2.0, h_r=2.0)
        geo = terranggeometri(p)
        klasser = [klassa_segment(geo, 2, C / float(f)) for f in self.f]
        relativa = [k.h_s_rel for k in klasser]
        self.assertAlmostEqual(relativa[0], 0.75, places=12)
        self.assertEqual(klasser[0].typ, OVERGANG)
        self.assertEqual(relativa[-1], 1.0)
        self.assertEqual(klasser[-1].typ, KONKAVT)
        self.assertEqual(relativa, sorted(relativa))
        self.assertTrue(any(0.75 < r < 1.0 for r in relativa))
        hojder = [k.h_s_fz_m for k in klasser]
        self.assertEqual(hojder, sorted(hojder, reverse=True))

    def test_fel_index_och_vaglangd(self) -> None:
        geo = terranggeometri(_profil([0.0, 50.0, 100.0], [0.0, 0.0, 0.0], 200.0))
        with self.assertRaises(BullerFel):
            klassa_segment(geo, 2, 1.0)
        with self.assertRaises(BullerFel):
            klassa_segment(geo, 0, 0.0)


class TestEkvivalentKil(unittest.TestCase):
    """Ekv. (141)–(146) och (151)."""

    def test_intilliggande_segment_handraknat(self) -> None:
        """Plan mark fram till x = 50, därefter sluttning 1:10 nedåt; kilens topp i (50, 0)."""
        h_s, h_r = 0.5, 1.5
        geo = terranggeometri(_profil([0.0, 50.0, 100.0], [0.0, 0.0, -5.0], 200.0, h_s, h_r))
        kil = ekvivalent_kil(geo, 1, sekundar_mot_kallan=True)
        lutning = math.atan(0.1)
        self.assertEqual((kil.index_sekundar, kil.x_t_m, kil.z_t_m), (0, 50.0, 0.0))
        self.assertAlmostEqual(kil.beta_rad, math.pi + lutning, places=12)
        self.assertAlmostEqual(
            kil.theta_s_rad, math.pi - math.atan(h_s / 50.0) + lutning, places=12
        )
        z_r = float(geo.z_r_m)
        self.assertAlmostEqual(kil.theta_r_rad, math.atan(z_r / 50.0) + lutning, places=12)
        self.assertAlmostEqual(kil.r_s_m, math.hypot(50.0, h_s), places=12)
        self.assertAlmostEqual(kil.r_r_m, math.hypot(50.0, z_r), places=12)
        # Samma kil sedd från första segmentet (sekundär yta mot mottagaren).
        fran_andra_hallet = ekvivalent_kil(geo, 0, sekundar_mot_kallan=False)
        self.assertEqual(fran_andra_hallet.index_sekundar, 1)
        self.assertAlmostEqual(fran_andra_hallet.beta_rad, kil.beta_rad, places=12)

    def test_plan_mark_ger_kilvinkeln_pi(self) -> None:
        geo = terranggeometri(_profil([0.0, 40.0, 100.0], [0.0, 0.0, 0.0], 200.0))
        kil = ekvivalent_kil(geo, 1, sekundar_mot_kallan=True)
        self.assertAlmostEqual(kil.beta_rad, math.pi, places=12)
        self.assertAlmostEqual(kil.theta_s_rad, math.pi - math.atan(0.5 / 40.0), places=12)
        self.assertAlmostEqual(kil.theta_r_rad, math.atan(1.5 / 60.0), places=12)

    def test_ekvivalent_kil_hoppar_over_segment(self) -> None:
        """Segment 1 uppfyller inte h'_Sv ≥ h_Sv; segment 0 väljs och toppen ges av WedgeCross."""
        geo = terranggeometri(_profil([0.0, 40.0, 50.0, 100.0], [0.0, 0.0, -0.5, -8.0], 200.0))
        kil = ekvivalent_kil(geo, 2, sekundar_mot_kallan=True)
        self.assertEqual(kil.index_sekundar, 0)
        self.assertAlmostEqual(kil.x_t_m, 7.0 / 0.15, places=10)
        self.assertAlmostEqual(kil.z_t_m, 0.0, places=12)
        self.assertAlmostEqual(kil.beta_rad, math.pi + math.atan(0.15), places=12)

    def test_tryckt_beta2_skulle_alltid_ge_plan_kil(self) -> None:
        """Ekv. (146) som tryckt: β₂ ur (x2, z2) på källsidans yta ger β₁ + β₂ = π för varje kil.

        Det visar att (x2, z2) är ett tryckfel för mottagarsidans yttre punkt (x4, z4).
        """
        x1, z1, x2, z2 = 0.0, 0.0, 40.0, 0.0
        x_t, z_t = 7.0 / 0.15, 0.0
        beta_1 = math.atan((z1 - z_t) / (x_t - x1)) + math.pi / 2.0
        beta_2_tryckt = math.atan((z2 - z_t) / (x2 - x_t)) + math.pi / 2.0
        self.assertAlmostEqual(2.0 * math.pi - beta_1 - beta_2_tryckt, math.pi, places=12)

    def test_markeffekt_ar_pwedge_med_segmentets_impedans(self) -> None:
        geo = terranggeometri(_profil([0.0, 50.0, 100.0], [0.0, 0.0, -5.0], 80.0))
        kil = ekvivalent_kil(geo, 1, sekundar_mot_kallan=True)
        f = 500.0
        z = markimpedans(f, 80.0)
        indata = kil_rata_stralar(
            kil.beta_rad, kil.theta_s_rad, kil.theta_r_rad, kil.r_s_m, kil.r_r_m, C
        )
        vantat = 20.0 * math.log10(abs(kiltryck_impedans(f, indata, z, z)) * geo.r_sr_m)
        self.assertAlmostEqual(
            markeffekt_konvext_segment_db(f, kil, geo.r_sr_m, 80.0, C), vantat, places=12
        )

    def test_hard_plan_kil_ger_tva_stralar(self) -> None:
        """β = π med hårda ytor: pwedge är direkt plus speglad stråle (P-K1), alltså +6 dB lågt."""
        geo = terranggeometri(_profil([0.0, 40.0, 100.0], [0.0, 0.0, 0.0], 1.0e9, 0.3, 1.5))
        kil = ekvivalent_kil(geo, 1, sekundar_mot_kallan=True)
        r1, r2 = math.hypot(100.0, 1.2), math.hypot(100.0, 1.8)
        vantat = 20.0 * math.log10(1.0 + r1 / r2)
        self.assertAlmostEqual(
            markeffekt_konvext_segment_db(25.0, kil, geo.r_sr_m, 1.0e9, C), vantat, delta=0.01
        )

    def test_konvex_viktgeometri_ekv_152(self) -> None:
        """Segment under baslinjen projiceras på en parallell linje genom sin högsta punkt."""
        geo = terranggeometri(
            _profil([0.0, 30.0, 50.0, 80.0, 100.0], [0.0, -4.0, -2.0, -3.0, 0.0], 200.0, 1.0, 2.0)
        )
        g = konvex_fresnelgeometri(geo, 1)  # ändpunkter på z = −4 och −2, baslinjen z = 0
        self.assertAlmostEqual(g.h_s_m, 1.0 + 2.0, places=12)
        self.assertAlmostEqual(g.h_r_m, 2.0 + 2.0, places=12)
        self.assertAlmostEqual(g.d_m, 100.0, places=12)
        self.assertAlmostEqual(g.d1_m, 30.0, places=12)
        self.assertAlmostEqual(g.d2_m, 50.0, places=12)
        # Segment med en ändpunkt på baslinjen: ingen förskjutning.
        g0 = konvex_fresnelgeometri(geo, 0)
        self.assertAlmostEqual(g0.h_s_m, 1.0, places=12)


class TestSammanvagning(unittest.TestCase):
    """Ekv. (156)."""

    def test_positiv_summa_andras_inte(self) -> None:
        dl0, dl3 = vag_samman_segment_db(np.array([0.5, 0.25]), np.array([4.0, 2.0]))
        self.assertAlmostEqual(dl0, 2.5, places=12)
        self.assertEqual(dl3, dl0)

    def test_vikter_over_2_normeras(self) -> None:
        dl0, dl3 = vag_samman_segment_db(np.array([1.0, 1.0, 1.0, 1.0]), np.array([3.0] * 4))
        self.assertAlmostEqual(dl0, 6.0, places=12)  # 4 segment à 3 dB med vikten 0,5
        self.assertAlmostEqual(dl3, 6.0, places=12)

    def test_negativ_summa_gar_mot_vagt_medelvarde(self) -> None:
        # ΔL₀ = −10 dB, Σw' = 2: r' = 0,5 och ΔL₃ = −10·(1 − 0,5 + 0,5/2) = −7,5 dB.
        dl0, dl3 = vag_samman_segment_db(np.array([1.0, 1.0]), np.array([-5.0, -5.0]))
        self.assertAlmostEqual(dl0, -10.0, places=12)
        self.assertAlmostEqual(dl3, -7.5, places=12)
        # ΔL₀ ≤ −20 dB: rent medelvärde, −12 dB.
        dl0, dl3 = vag_samman_segment_db(np.array([1.0, 1.0]), np.array([-10.0, -14.0]))
        self.assertAlmostEqual(dl0, -24.0, places=12)
        self.assertAlmostEqual(dl3, -12.0, places=12)

    def test_viktsumma_1_andrar_ingenting(self) -> None:
        dl0, dl3 = vag_samman_segment_db(np.array([0.3, 0.7]), np.array([-20.0, -8.0]))
        self.assertAlmostEqual(dl3, dl0, places=12)

    def test_inga_vikter_ger_noll(self) -> None:
        self.assertEqual(vag_samman_segment_db(np.zeros(3), np.zeros(3)), (0.0, 0.0))


class TestPlanProfil(unittest.TestCase):
    """En plan profil delad i segment: delmodell 3 mot delmodell 1 och 2."""

    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()

    def test_en_marktyp_ger_delmodell_1(self) -> None:
        """Vikterna summerar till 1 och varje segment ger delmodell 1: ΔL₃ = ΔL₁ i alla band."""
        for luft in (STILLA, TURBULENT):
            p = _profil([0.0, 10.0, 35.0, 60.0, 100.0], [0.0] * 5, 200.0)
            resultat = delmodell3(self.f, terranggeometri(p), luft)
            referens = markeffekt_plan_mark_db(self.f, 100.0, 0.5, 1.5, 200.0, 0.0, luft)
            np.testing.assert_allclose(resultat.markeffekt_db, referens, rtol=0.0, atol=1.0e-9)
            np.testing.assert_allclose(np.sum(resultat.vikt, axis=0), 1.0, rtol=0.0, atol=1e-12)

    def test_lutande_plan_mark_ger_delmodell_1(self) -> None:
        p = _profil([0.0, 20.0, 50.0, 100.0], [0.0, 1.0, 2.5, 5.0], 500.0, 0.3, 4.0)
        referens, modell = _plan_modell(p, STILLA, self.f)
        self.assertEqual(modell, "1")
        dl3 = markeffekt_icke_plan_terrang_db(self.f, terranggeometri(p), STILLA)
        np.testing.assert_allclose(dl3, referens, rtol=0.0, atol=1.0e-9)

    def test_oklippt_zon_avviker_vid_laga_frekvenser(self) -> None:
        """Utan regeln i avsnitt 5.8 täcker marken inte hela zonen och ΔL₃ ≠ ΔL₁ i låga band."""
        p = _profil([0.0, 5.0, 10.0], [0.0, 0.0, 0.0], 200.0, 0.01, 1.5)
        geo = terranggeometri(p)
        referens = markeffekt_plan_mark_db(self.f, 10.0, 0.01, 1.5, 200.0, 0.0, STILLA)
        oklippt = delmodell3(self.f, geo, STILLA, "oklippt").markeffekt_db
        klippt = delmodell3(self.f, geo, STILLA).markeffekt_db
        skillnad = np.abs(oklippt - referens)
        self.assertGreater(float(skillnad[0]), 0.2)  # 25 Hz
        self.assertLess(float(skillnad[0]), 0.3)
        self.assertLess(float(np.max(skillnad[10:])), 0.05)  # från 250 Hz
        np.testing.assert_allclose(klippt, referens, rtol=0.0, atol=1.0e-9)

    def test_flera_marktyper_ger_inte_delmodell_2(self) -> None:
        """Delmodell 3 har F_λ = 1/16 och enkla vikter, delmodell 2 F_λ = 1/4 och ekv. (125)–(129).

        De ska alltså inte bli lika; övergången sköts av r_flat = 1 (delmodell 2 väljs). Testet
        håller fast det och den uppmätta storleken (se loggen för avvikelsen per band).
        """
        p = _profil([0.0, 5.0, 40.0, 100.0], [0.0] * 4, [20000.0, 200.0, 200.0], 0.3, 1.5)
        geo = terranggeometri(p)
        referens, modell = _plan_modell(p, STILLA, self.f)
        self.assertEqual(modell, "2")
        resultat = delmodell3(self.f, geo, STILLA)
        avvikelse = resultat.markeffekt_db - referens
        self.assertLess(float(np.max(np.abs(avvikelse[:11]))), 0.2)  # 25–250 Hz
        self.assertGreater(float(np.max(np.abs(avvikelse))), 3.0)
        self.assertLess(float(np.max(np.abs(avvikelse))), 4.0)
        np.testing.assert_array_equal(overgang_rflat(resultat, geo, C), 1.0)
        utan_skarm = markeffekt_utan_skarm_db(
            overgang_rflat(resultat, geo, C), referens, resultat.markeffekt_db
        )
        np.testing.assert_array_equal(utan_skarm, referens)

    def test_indelningen_av_en_marktyp_spelar_ingen_roll(self) -> None:
        grov = _profil([0.0, 100.0], [0.0, 0.0], 80.0, 1.0, 4.0)
        fin = _profil([0.0, 1.0, 2.0, 30.0, 70.0, 99.0, 100.0], [0.0] * 7, 80.0, 1.0, 4.0)
        a = markeffekt_icke_plan_terrang_db(self.f, terranggeometri(grov), TURBULENT)
        b = markeffekt_icke_plan_terrang_db(self.f, terranggeometri(fin), TURBULENT)
        np.testing.assert_allclose(a, b, rtol=0.0, atol=1.0e-9)


class TestRflat(unittest.TestCase):
    """Ekv. (317)–(319)."""

    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()

    def test_ekv_318_styckvis(self) -> None:
        self.assertEqual(planhetsparameter_rflat(0.8, 0.0), 0.8)
        self.assertEqual(planhetsparameter_rflat(0.8, 1.05), 0.8)
        self.assertAlmostEqual(planhetsparameter_rflat(0.8, 1.225), 0.4, places=12)
        self.assertEqual(planhetsparameter_rflat(0.8, 1.4), 0.0)
        self.assertEqual(planhetsparameter_rflat(1.0, 3.0), 0.0)
        # Kontinuerlig i båda brytpunkterna.
        self.assertAlmostEqual(planhetsparameter_rflat(1.0, 1.05 + 1e-12), 1.0, places=9)
        self.assertAlmostEqual(planhetsparameter_rflat(1.0, 1.4 - 1e-12), 0.0, places=9)

    def test_plan_mark_ger_1(self) -> None:
        for p in (
            _profil([0.0, 10.0, 35.0, 60.0, 100.0], [0.0] * 5, 200.0),
            _profil([0.0, 20.0, 50.0, 100.0], [0.0, 1.0, 2.5, 5.0], 500.0, 0.3, 4.0),
            _profil([0.0, 5.0, 40.0, 100.0], [0.0] * 4, [20000.0, 200.0, 200.0], 0.3, 1.5),
        ):
            geo = terranggeometri(p)
            resultat = delmodell3(self.f, geo, STILLA)
            np.testing.assert_array_equal(overgang_rflat(resultat, geo, C), 1.0)
            self.assertLessEqual(float(np.max(resultat.summa_konkav_vikt)), 1.0 + 1e-9)

    def test_djup_dal_ger_0(self) -> None:
        """V-dal 10 m djup: båda sidorna speglar (w_con = 2) och r_flat = 0 i alla band."""
        geo = terranggeometri(_profil([0.0, 50.0, 100.0], [0.0, -10.0, 0.0], 200.0))
        resultat = delmodell3(self.f, geo, STILLA)
        np.testing.assert_allclose(resultat.summa_konkav_vikt, 2.0, rtol=0.0, atol=1e-9)
        np.testing.assert_array_equal(overgang_rflat(resultat, geo, C), 0.0)

    def test_grundare_dal_ger_mindre_r_flat_an_plan_mark(self) -> None:
        """r_flat avtar monotont med daldjupet och med frekvensen upp till 2 kHz."""
        foregaende = np.ones(27)
        for djup in (0.01, 0.1, 0.5, 2.0, 10.0):
            geo = terranggeometri(_profil([0.0, 50.0, 100.0], [0.0, -djup, 0.0], 200.0))
            r_flat = overgang_rflat(delmodell3(self.f, geo, STILLA), geo, C)
            self.assertTrue(bool(np.all(r_flat <= foregaende + 1e-12)))
            self.assertTrue(bool(np.all((r_flat >= 0.0) & (r_flat <= 1.0))))
            foregaende = r_flat
        geo = terranggeometri(_profil([0.0, 50.0, 100.0], [0.0, -0.5, 0.0], 200.0))
        r_flat = overgang_rflat(delmodell3(self.f, geo, STILLA), geo, C)
        self.assertEqual(float(r_flat[0]), 1.0)
        self.assertEqual(float(r_flat[-1]), 0.0)
        self.assertTrue(bool(np.all(np.diff(r_flat) <= 1e-12)))

    def test_w_con_drar_ned_r_flat_dar_r_f_ar_1(self) -> None:
        """Ekv. (318): i en djup dal är r_f = 1 vid 25 Hz men r_flat = 0 genom w_con."""
        geo = terranggeometri(_profil([0.0, 100.0, 200.0], [0.0, -3.0, 0.0], 200.0, 2.0, 2.0))
        resultat = delmodell3(self.f, geo, STILLA)
        r_f = overgang_rf(self.f, geo, C)
        r_flat = overgang_rflat(resultat, geo, C)
        self.assertEqual(float(r_f[0]), 1.0)
        self.assertGreater(float(resultat.summa_konkav_vikt[0]), 1.05)
        self.assertLess(float(r_flat[0]), 1.0)

    def test_ekv_319(self) -> None:
        r = np.array([1.0, 0.25, 0.0])
        ut = markeffekt_utan_skarm_db(r, np.array([4.0, 4.0, 4.0]), np.array([-2.0, -4.0, -6.0]))
        np.testing.assert_allclose(ut, [4.0, -2.0, -6.0], rtol=0.0, atol=1e-12)
        with self.assertRaises(BullerFel):
            markeffekt_utan_skarm_db(r, np.zeros(2), np.zeros(3))


class TestReciprocitet(unittest.TestCase):
    """Speglad profil med ombytt källa och mottagare ska ge samma ΔL₃ och samma r_flat."""

    def _profiler(self) -> list[Terrangprofil]:
        profiler = [
            _profil(
                [0.0, 20.0, 50.0, 80.0, 100.0],
                [0.0, -3.0, -4.0, -3.0, 0.0],
                [200.0, 500.0, 80.0, 200.0],
            ),
            _profil(
                [0.0, 10.0, 30.0, 60.0, 100.0],
                [0.0, 0.0, -2.0, -2.0, -5.0],
                [20000.0, 200.0, 200.0, 80.0],
                0.3,
                4.0,
                [0.0, 0.0, 0.25, 0.0],
            ),
            _profil([0.0, 50.0, 100.0], [0.0, 0.0, -5.0], 200.0, 6.0, 1.5),  # övergångssegment
            _profil(
                [0.0, 2.75, 21.75, 46.75, 71.75, 96.75],
                [0.0, 0.0, -2.0, -3.0, -2.0, 0.0],
                200.0,
                0.01,
            ),
            _profil([0.0, 19.0, 20.0, 150.0], [0.0, 0.0, 0.5, 0.5], 200.0, 2.0, 2.0),
        ]
        rng = np.random.default_rng(3)
        while len(profiler) < 30:
            n = int(rng.integers(3, 8))
            x = np.concatenate(([0.0], np.sort(rng.uniform(5.0, 195.0, n - 2)), [200.0]))
            if float(np.min(np.diff(x))) < 1.0:
                continue
            z = np.concatenate(([0.0], -rng.uniform(0.0, 6.0, n - 2), [rng.uniform(-2.0, 2.0)]))
            sigma = [float(s) for s in rng.choice([12.5, 200.0, 2000.0, 20000.0], n - 1)]
            profiler.append(
                _profil(
                    [float(v) for v in x],
                    [float(v) for v in z],
                    sigma,
                    float(rng.uniform(0.01, 3.0)),
                    float(rng.uniform(0.5, 6.0)),
                )
            )
        return profiler

    def test_speglad_profil(self) -> None:
        f = mittfrekvenser_hz()
        storst = 0.0
        typer: set[str] = set()
        for p in self._profiler():
            geo_a, geo_b = terranggeometri(p), terranggeometri(_spegla(p))
            a = delmodell3(f, geo_a, TURBULENT)
            b = delmodell3(f, geo_b, TURBULENT)
            storst = max(storst, float(np.max(np.abs(a.markeffekt_db - b.markeffekt_db))))
            np.testing.assert_allclose(
                overgang_rflat(a, geo_a, C), overgang_rflat(b, geo_b, C), rtol=0.0, atol=1e-9
            )
            np.testing.assert_allclose(a.vikt, b.vikt[::-1], rtol=0.0, atol=1e-9)
            np.testing.assert_allclose(a.h_s_rel, b.h_r_rel[::-1], rtol=0.0, atol=1e-9)
            for band in (0, 13, 26):
                typer.update(a.segmenttyper(band))
        self.assertLess(storst, 1.0e-8)
        self.assertEqual(typer, {KONKAVT, KONVEXT, OVERGANG})  # alla tre typerna är prövade


class TestKontinuitet(unittest.TestCase):
    """Ett segment som går från svagt konkavt (eller svagt konvext) till plant."""

    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()
        self.plan = markeffekt_plan_mark_db(self.f, 100.0, 0.5, 1.5, 200.0, 0.0, TURBULENT)

    def _avvikelse(self, djup: float) -> tuple[float, Delmodell3Resultat, Terranggeometri]:
        geo = terranggeometri(_profil([0.0, 40.0, 100.0], [0.0, -djup, 0.0], 200.0))
        resultat = delmodell3(self.f, geo, TURBULENT)
        return float(np.max(np.abs(resultat.markeffekt_db - self.plan))), resultat, geo

    def test_svagt_konkav_gar_mot_plan(self) -> None:
        """Knäckpunkten sänks 1 dm … 1 µm: ΔL₃ → ΔL₁ proportionellt mot djupet."""
        avvikelser = []
        for djup in (1.0e-1, 1.0e-2, 1.0e-3, 1.0e-4, 1.0e-6):
            avvikelse, resultat, _ = self._avvikelse(djup)
            self.assertEqual(resultat.segmenttyper(13), [KONKAVT, KONKAVT])
            avvikelser.append(avvikelse)
        self.assertEqual(avvikelser, sorted(avvikelser, reverse=True))
        self.assertLess(avvikelser[2], 0.02)
        self.assertLess(avvikelser[3], 0.002)
        self.assertLess(avvikelser[4], 0.0001)
        self.assertLess(self._avvikelse(0.0)[0], 1.0e-9)

    def test_svagt_konvex_gar_mot_plan(self) -> None:
        """Knäckpunkten höjs: övergångssegment med r → 1, och ΔL₃ → ΔL₁ utan språng."""
        avvikelser = []
        for hojd in (1.0e-2, 1.0e-3, 1.0e-4, 1.0e-6):
            avvikelse, resultat, _ = self._avvikelse(-hojd)
            self.assertEqual(resultat.segmenttyper(13), [OVERGANG, OVERGANG])
            avvikelser.append(avvikelse)
        self.assertEqual(avvikelser, sorted(avvikelser, reverse=True))
        self.assertLess(avvikelser[1], 0.1)
        self.assertLess(avvikelser[2], 0.01)
        self.assertLess(avvikelser[3], 0.001)

    def test_r_flat_ar_1_nara_plan_mark(self) -> None:
        for djup in (1.0e-2, 1.0e-4, -1.0e-4, -1.0e-2):
            _, resultat, geo = self._avvikelse(djup)
            np.testing.assert_array_equal(overgang_rflat(resultat, geo, C), 1.0)

    def test_overgang_konvex_till_overgangssegment(self) -> None:
        """Källan höjs förbi sluttningens förlängning (h'_S går genom 0): ΔL₃ utan språng."""
        varden = []
        for h_s in (5.0 - 1.0e-6, 5.0 + 1.0e-6):
            geo = terranggeometri(_profil([0.0, 50.0, 100.0], [0.0, 0.0, -5.0], 200.0, h_s, 1.5))
            varden.append(delmodell3(self.f, geo, STILLA))
        self.assertEqual(varden[0].segmenttyper(10)[1], KONVEXT)
        self.assertEqual(varden[1].segmenttyper(10)[1], OVERGANG)
        np.testing.assert_allclose(
            varden[0].markeffekt_db, varden[1].markeffekt_db, rtol=0.0, atol=1.0e-4
        )


class TestTackning(unittest.TestCase):
    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()
        self.geo = terranggeometri(
            _profil([0.0, 20.0, 50.0, 80.0, 100.0], [0.0, -3.0, -4.0, -3.0, 0.0], 200.0)
        )

    def _atmosfar(self, a: float = 0.0, b: float = 0.0, s_a: float = 0.0) -> Atmosfar:
        return Atmosfar(
            a_m_s=a,
            b_per_s=b,
            s_a_m_s=s_a,
            s_b_per_s=0.0,
            z0_m=0.05,
            temperatur_mark_c=15.0,
            temperatur_luft_c=15.0,
            relativ_fuktighet_procent=70.0,
            lufttryck_kpa=101.325,
            cv2=0.12,
            ct2=0.008,
        )

    def test_svag_refraktion_gar_mot_rata_stralar(self) -> None:
        """Krökta strålar (P-S2): med försumbar vind eller fluktuation återfås räta strålar.

        Under gränsen i TC-101327 avsnitt 2.1 är ξ = 0, och den nya räknevägen (delmodell 1
        med refraktion i ekv. 140, vikter ur FresnelZoneW) ska då ge samma ΔL₃ som den gamla.
        """
        rak = delmodell3(self.f, self.geo, self._atmosfar()).markeffekt_db
        for atmosfar in (
            self._atmosfar(a=1.0e-7),
            self._atmosfar(a=-1.0e-7),
            self._atmosfar(s_a=1.0e-12),
        ):
            krokt = delmodell3(self.f, self.geo, atmosfar).markeffekt_db
            np.testing.assert_allclose(krokt, rak, rtol=0, atol=1e-9)

    def test_refraktion_andrar_resultatet_och_ar_andligt(self) -> None:
        rak = delmodell3(self.f, self.geo, self._atmosfar()).markeffekt_db
        for atmosfar in (self._atmosfar(a=0.5), self._atmosfar(b=0.05), self._atmosfar(s_a=0.1)):
            krokt = delmodell3(self.f, self.geo, atmosfar, uppskjut_fel=True)
            self.assertTrue(bool(np.all(np.isfinite(krokt.markeffekt_db))), krokt.fel)
            self.assertGreater(float(np.max(np.abs(krokt.markeffekt_db - rak))), 0.01)

    def test_plan_mark_i_segment_med_refraktion_ar_delmodell_1(self) -> None:
        """Plan mark delad i segment: vikterna summerar till 1 och ΔL₃ = ΔL₁, även med motvind."""
        p = _profil([0.0, 20.0, 60.0, 100.0], [0.0, 0.0, 0.0, 0.0], 200.0, 0.3, 1.5)
        geo = terranggeometri(p)
        for a, s_a in ((0.4, 0.1), (0.1, 0.0), (-0.3, 0.0)):
            atmosfar = self._atmosfar(a=a, s_a=s_a)
            resultat = delmodell3(self.f, geo, atmosfar)
            vantat = markeffekt_plan_mark_db(self.f, 100.0, 0.3, 1.5, 200.0, 0.0, atmosfar)
            np.testing.assert_allclose(np.sum(resultat.vikt, axis=0), 1.0, rtol=0, atol=1e-9)
            np.testing.assert_allclose(resultat.markeffekt_db, vantat, rtol=0, atol=1e-9)

    def test_dal_i_motvind_ar_korbar(self) -> None:
        """Motvind över en dal: geometriskt konkava segment behåller h_rel = 1 (P-S5)."""
        dal = terranggeometri(
            _profil([0.0, 40.0, 100.0, 160.0, 200.0], [0.0, -2.0, -3.0, -2.0, 0.0], 200.0, 0.5, 1.5)
        )
        motvind = self._atmosfar(a=-0.566)  # motvind ca 3 m/s på 10 m höjd
        resultat = delmodell3(self.f, dal, motvind)
        self.assertTrue(bool(np.all(np.isfinite(resultat.markeffekt_db))))
        self.assertTrue(bool(np.all(np.isfinite(resultat.summa_konkav_vikt))))
        self.assertTrue(all(f is None for f in resultat.fel))

    def test_overgangshojd_bis_ror_bara_overgangssegment(self) -> None:
        prim = delmodell3(self.f, self.geo, STILLA, overgangshojd="prim")
        bis = delmodell3(self.f, self.geo, STILLA, overgangshojd="bis")
        np.testing.assert_array_equal(prim.vikt, bis.vikt)
        for i in range(prim.vikt.shape[0]):
            for b in range(self.f.shape[0]):
                r = min(prim.h_s_rel[i, b], prim.h_r_rel[i, b])
                if r in (0.0, 1.0):
                    self.assertEqual(
                        prim.markeffekt_segment_db[i, b], bis.markeffekt_segment_db[i, b]
                    )
        with self.assertRaises(BullerFel):
            delmodell3(self.f, self.geo, STILLA, overgangshojd="annan")  # type: ignore[arg-type]

    def test_overgangshojd_bis_med_refraktion_och_fluktuation(self) -> None:
        """AV 1849/00 4.4.2 (bis) ska fungera med refraktion och fluktuationer utan bandfel."""
        # Profil med ett övergångssegment (h'_S ≈ 0,14 m < h_S = 0,30 m vid [3,8; 26,8]).
        dubbel = terranggeometri(
            _profil(
                [0.0, 3.8, 26.8, 46.8, 56.8, 76.8, 96.8],
                [0.0, 0.0, -1.0, 0.0, 0.0, -2.0, 0.0],
                200.0,
                0.30,
                1.5,
            )
        )
        for atmosfar in (
            self._atmosfar(s_a=0.1),
            self._atmosfar(a=0.4, s_a=0.1),
            self._atmosfar(a=-0.4, s_a=0.1),
        ):
            prim = delmodell3(self.f, dubbel, atmosfar, overgangshojd="prim", uppskjut_fel=True)
            bis = delmodell3(self.f, dubbel, atmosfar, overgangshojd="bis", uppskjut_fel=True)
            self.assertTrue(bool(np.all(np.isfinite(bis.markeffekt_db))), bis.fel)
            np.testing.assert_array_equal(prim.vikt, bis.vikt)
            har_overgang = False
            for i in range(bis.vikt.shape[0]):
                for b in range(self.f.shape[0]):
                    r = min(bis.h_s_rel[i, b], bis.h_r_rel[i, b])
                    if r in (0.0, 1.0):
                        self.assertEqual(
                            prim.markeffekt_segment_db[i, b], bis.markeffekt_segment_db[i, b]
                        )
                    elif bis.vikt[i, b] > 0.0:
                        har_overgang = True
            self.assertTrue(har_overgang)

    def test_koherens_spridningszon_propagera_genom_delmodell3(self) -> None:
        """F_s (AV 1106/07 ekv. 288) går in i delmodell 1 för konkava segment i delmodell 3."""
        f_s = np.full(self.f.shape[0], 0.25, dtype=np.float64)
        # Plan mark delad i segment: ΔL₃(F_s) ska vara lika med ΔL₁(F_s).
        plan = terranggeometri(_profil([0.0, 30.0, 100.0], [0.0, 0.0, 0.0], 200.0, 0.3, 1.5))
        dm3 = delmodell3(self.f, plan, TURBULENT, koherens_spridningszon=f_s)
        dm1 = markeffekt_plan_mark_db(
            self.f, 100.0, 0.3, 1.5, 200.0, 0.0, TURBULENT, koherens_spridningszon=f_s
        )
        np.testing.assert_allclose(dm3.markeffekt_db, dm1, rtol=0, atol=1e-9)
        # Dalprofil: F_s < 1 ska ändra ΔL₃ jämfört med F_s = 1.
        dal_utan = delmodell3(self.f, self.geo, TURBULENT).markeffekt_db
        dal_med = delmodell3(
            self.f, self.geo, TURBULENT, koherens_spridningszon=f_s
        ).markeffekt_db
        self.assertGreater(float(np.max(np.abs(dal_med - dal_utan))), 0.5)

    def test_atmosfar_utan_refraktion_ar_homogen(self) -> None:
        a = delmodell3(self.f, self.geo, self._atmosfar()).markeffekt_db
        b = delmodell3(self.f, self.geo, TURBULENT).markeffekt_db
        np.testing.assert_array_equal(a, b)

    def test_resultatets_form_och_granser(self) -> None:
        resultat = delmodell3(self.f, self.geo, STILLA)
        self.assertEqual(resultat.vikt.shape, (4, 27))
        self.assertEqual(resultat.markeffekt_db.shape, (27,))
        self.assertTrue(bool(np.all((resultat.vikt >= 0.0) & (resultat.vikt <= 1.0 + 1e-12))))
        self.assertTrue(bool(np.all(resultat.konkav_vikt <= resultat.vikt + 1e-12)))
        self.assertTrue(bool(np.all(np.isfinite(resultat.markeffekt_db))))
        # Hård gräns för summan av två koherenta speglingar per ekv. (156): Σw' ≤ 2 à 6,02 dB.
        self.assertLess(float(np.max(resultat.markeffekt_db)), 2.0 * 6.0206)

    def test_valley_ger_mer_an_plan_mark_vid_lag_frekvens(self) -> None:
        """Två speglande dalsidor: ΔL₃ > 6 dB vid 25 Hz, vilket plan mark aldrig ger."""
        resultat = delmodell3(self.f, self.geo, STILLA)
        self.assertGreater(float(resultat.markeffekt_db[0]), 6.03)


@unittest.skipUnless(TESTSVIT.is_dir(), "testsvit/N_nord2000 saknas")
class TestTestfallensDalprofiler(unittest.TestCase):
    """Dalprofilerna i teststraightroad (blad 51–64 och 101–104): körbara, inget facit.

    Segmentklassning och r_flat redovisas i loggen. Här prövas bara att profilerna går att
    räkna med räta strålar och att storheterna håller sig inom sina definitionsområden.
    """

    def test_profilerna_gar_att_rakna(self) -> None:
        from buller.testfall.format import las_fall

        f = mittfrekvenser_hz()
        for blad in (51, 54, 61, 64, 101, 104):
            fall = las_fall(TESTSVIT / f"N18R{blad:03d}_rak_vag")
            for h_s in (0.01, 0.30, 0.75):
                geo = terranggeometri(fall.profil(h_s))
                resultat = delmodell3(f, geo, STILLA)
                r_flat = overgang_rflat(resultat, geo, C)
                self.assertTrue(bool(np.all(np.isfinite(resultat.markeffekt_db))))
                self.assertTrue(bool(np.all((r_flat >= 0.0) & (r_flat <= 1.0))))
                self.assertEqual(float(r_flat[-1]), 0.0)  # 10 kHz: delmodell 3
                # Vägbanan och sista dalsidan är konkava i alla band.
                for band in range(27):
                    typer = resultat.segmenttyper(band)
                    self.assertEqual(typer[0], KONKAVT)
                    self.assertEqual(typer[-1], KONKAVT)

    def test_slanten_vid_vagen_ar_konvex_for_lag_kalla(self) -> None:
        """Blad 51: slänten från vägkanten ned i dalen har källan (0,01 m) under sin förlängning."""
        from buller.testfall.format import las_fall

        fall = las_fall(TESTSVIT / "N18R051_rak_vag")
        f = mittfrekvenser_hz()
        lag = delmodell3(f, terranggeometri(fall.profil(0.01)), STILLA)
        hog = delmodell3(f, terranggeometri(fall.profil(0.30)), STILLA)
        for band in (0, 13, 26):
            self.assertEqual(lag.segmenttyper(band)[2], KONVEXT)
            self.assertEqual(hog.segmenttyper(band)[2], OVERGANG)


if __name__ == "__main__":
    unittest.main()
