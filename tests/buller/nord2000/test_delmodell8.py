"""Tester för delmodell 8 (flera markreflexer), RayCurvature och HeightOfCircularRay."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.nord2000 import delmodell8 as d8
from buller.nord2000 import delmodell10 as d10
from buller.nord2000.delmodell11 import reflexgeometri_rak_strale
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.mark import markimpedans, planvagsreflektion
from buller.nord2000.refraktion import Atmosfar
from buller.nord2000.stralar_krokta import cirkelbage
from buller.typer import BullerFel, Flyttal, Terrangprofil

C_M_S = 340.0


def _atmosfar(a_m_s: float = 0.0, b_per_s: float = 0.0, z0_m: float = 0.05) -> Atmosfar:
    return Atmosfar(
        a_m_s=a_m_s,
        b_per_s=b_per_s,
        s_a_m_s=0.0,
        s_b_per_s=0.0,
        z0_m=z0_m,
        temperatur_mark_c=15.0,
        temperatur_luft_c=15.0,
        relativ_fuktighet_procent=70.0,
        lufttryck_kpa=101.325,
        cv2=0.0,
        ct2=0.0,
    )


def _profil(
    x_m: list[float],
    z_m: list[float],
    sigma: list[float],
    h_s_m: float = 0.5,
    h_r_m: float = 1.5,
) -> Terrangprofil:
    return Terrangprofil(
        x_m=np.array(x_m, dtype=np.float64),
        z_m=np.array(z_m, dtype=np.float64),
        flodesresistivitet_kNsm4=np.array(sigma, dtype=np.float64),
        rahet_m=np.zeros(len(sigma), dtype=np.float64),
        kalla_hojd_m=h_s_m,
        mottagare_hojd_m=h_r_m,
    )


def _plan_profil(d_m: float, sigma: float, h_s_m: float = 0.5, h_r_m: float = 1.5) -> Terrangprofil:
    return _profil([0.0, d_m], [0.0, 0.0], [sigma], h_s_m, h_r_m)


def _hojd_bokstavlig(
    d: float, h1: float, h2: float, xi: float, d0: float
) -> tuple[float, float, float]:
    """AV 1106/07 ekv. (356)–(368) rakt av (kräver ξ ≠ 0)."""
    xi_p = abs(xi)
    dh = abs(h1 - h2)
    if xi > 0.0 and h1 > h2:
        h1_p, d0_p = h2, d - d0
    elif xi > 0.0:
        h1_p, d0_p = h1, d0
    elif h1 >= h2:
        h1_p, d0_p = -h1, d0
    else:
        h1_p, d0_p = -h2, d - d0
    tan_psi = xi_p * d / 2.0 + dh * (2.0 + xi_p * dh) / (2.0 * d)
    x0 = tan_psi / xi_p
    z0 = h1_p - 1.0 / xi_p
    r_c = 1.0 / (xi_p * math.cos(math.atan(tan_psi)))
    theta = math.acos((d0_p - x0) / r_c)
    h0 = math.copysign(1.0, xi) * (r_c * math.sin(theta) + z0)
    r1 = math.hypot(d0, h0 - h1)
    r2 = math.hypot(d - d0, h2 - h0)
    return (
        h0,
        2.0 * r_c * math.asin(r1 / (2.0 * r_c)),
        2.0 * r_c * math.asin(r2 / (2.0 * r_c)),
    )


FALL_HOJD = [
    # (d, h1, h2, ξ): de fyra grenarna i ekv. (358)–(361) och lika höjder
    (100.0, 0.5, 1.5, 2.0e-3),
    (100.0, 4.0, 0.3, 2.0e-3),
    (100.0, 4.0, 0.3, -1.0e-3),
    (100.0, 0.5, 1.5, -1.0e-3),
    (1000.0, 2.0, 2.0, 5.0e-4),
    (1000.0, 2.0, 2.0, -5.0e-4),
    (300.0, 1.0, 25.0, 1.0e-3),
    (40.0, 10.0, 1.0, 1.0e-2),
]


class TestRayCurvature(unittest.TestCase):
    def test_ekv_54_delarna_var_for_sig(self) -> None:
        d, a, b = 200.0, 0.6, 0.05
        r_a = d / 8.0 * math.sqrt(2.0 * math.pi * C_M_S / a)
        r_b = math.sqrt((C_M_S / b) ** 2 + (d / 2.0) ** 2)
        self.assertAlmostEqual(d8.krokningsradie_m(d, a, 0.0, C_M_S), r_a, places=9)
        self.assertAlmostEqual(d8.krokningsradie_m(d, 0.0, b, C_M_S), r_b, places=9)
        self.assertAlmostEqual(
            d8.krokningsradie_m(d, a, b, C_M_S), 1.0 / (1.0 / r_a + 1.0 / r_b), places=9
        )
        # Talvärde: d = 200 m, A = 0,6 m/s, C = 340 m/s ger R_A = 25·√(2π·340/0,6).
        self.assertAlmostEqual(r_a, 1491.74, places=2)

    def test_tecken_och_rat_strale(self) -> None:
        d = 150.0
        self.assertLess(d8.krokningsradie_m(d, -0.5, 0.0, C_M_S), 0.0)
        self.assertLess(d8.krokningsradie_m(d, 0.0, -0.05, C_M_S), 0.0)
        self.assertAlmostEqual(
            d8.krokningsradie_m(d, -0.5, 0.0, C_M_S), -d8.krokningsradie_m(d, 0.5, 0.0, C_M_S)
        )
        self.assertEqual(d8.krokningsradie_m(d, 0.0, 0.0, C_M_S), math.inf)
        k = d8.stralkrokning(d, 1.0, 4.0, 0.0, 0.0, C_M_S)
        self.assertEqual(k.xi_ray_per_m, 0.0)
        self.assertAlmostEqual(k.startvinkel_rad, math.atan(3.0 / d), places=15)

    def test_ekv_55_bokstavligt(self) -> None:
        d, h_s, h_r, a, b = 400.0, 0.5, 6.0, 0.8, -0.01
        radie = d8.krokningsradie_m(d, a, b, C_M_S)
        psi = math.asin(math.sqrt(d * d + (h_r - h_s) ** 2) / (2.0 * radie)) + math.atan(
            (h_r - h_s) / d
        )
        k = d8.stralkrokning(d, h_s, h_r, a, b, C_M_S)
        self.assertAlmostEqual(k.radie_m, radie, places=9)
        self.assertAlmostEqual(k.startvinkel_rad, psi, places=14)
        self.assertAlmostEqual(k.xi_ray_per_m, 1.0 / (radie * math.cos(psi)), places=16)

    def test_cirkeln_med_radien_r_gar_genom_kalla_och_mottagare(self) -> None:
        # Oberoende geometri: medelpunkten ligger på avståndet R från S vinkelrätt mot
        # starttangenten; mottagaren ska då också ligga på avståndet R.
        for d, h_s, h_r, a, b in [
            (200.0, 0.5, 1.5, 0.6, 0.0),
            (500.0, 5.0, 1.0, 0.3, 0.02),
            (300.0, 2.0, 2.0, 0.0, 0.1),
            (300.0, 1.0, 3.0, -0.4, 0.0),
        ]:
            k = d8.stralkrokning(d, h_s, h_r, a, b, C_M_S)
            x_c = k.radie_m * math.sin(k.startvinkel_rad)
            z_c = h_s - k.radie_m * math.cos(k.startvinkel_rad)
            self.assertAlmostEqual(math.hypot(d - x_c, h_r - z_c), abs(k.radie_m), places=7)

    def test_mot_cirkelbagen_i_stralar_krokta(self) -> None:
        # Källan är den nedre punkten: ψ_S är ψ_L i ekv. (32) för ξ′ = ξ_ray, och
        # cirkelns radie 1/(ξ′·cos ψ_L) är R_A,B.
        d, h_s, h_r = 250.0, 0.5, 4.0
        k = d8.stralkrokning(d, h_s, h_r, 0.7, 0.03, C_M_S)
        bage = cirkelbage(d, h_r - h_s, k.xi_ray_per_m, C_M_S)
        self.assertAlmostEqual(bage.vinkel_nedre_rad, k.startvinkel_rad, places=13)
        self.assertAlmostEqual(
            1.0 / (k.xi_ray_per_m * math.cos(bage.vinkel_nedre_rad)), k.radie_m, places=8
        )

    def test_buller_fel(self) -> None:
        with self.assertRaises(BullerFel):
            d8.krokningsradie_m(0.0, 1.0, 0.0, C_M_S)
        with self.assertRaises(BullerFel):
            d8.krokningsradie_m(100.0, 1.0, 0.0, 0.0)
        # Kordan längre än diametern: R_A < d/2 kräver A > 2πC/16 ≈ 134 m/s (R_B är alltid
        # minst d/2), alltså ingen verklig atmosfär; felvägen prövas ändå.
        with self.assertRaises(BullerFel):
            d8.stralkrokning(1000.0, 1.0, 1.0, 200.0, 0.0, C_M_S)
        self.assertGreaterEqual(d8.krokningsradie_m(1000.0, 0.0, 50.0, C_M_S), 500.0)


class TestHojdCirkelstrale(unittest.TestCase):
    def test_gar_genom_start_och_slutpunkt(self) -> None:
        for d, h1, h2, xi in FALL_HOJD:
            start = d8.hojd_cirkelstrale(d, h1, h2, xi, 0.0)
            slut = d8.hojd_cirkelstrale(d, h1, h2, xi, d)
            self.assertAlmostEqual(start.hojd_m, h1, places=12)
            self.assertAlmostEqual(slut.hojd_m, h2, places=10)
            self.assertAlmostEqual(start.vag_fran_start_m, 0.0, places=12)
            self.assertAlmostEqual(slut.vag_till_slut_m, 0.0, places=9)
            self.assertAlmostEqual(start.vag_till_slut_m, slut.vag_fran_start_m, places=9)

    def test_mot_ekv_362_till_368_bokstavligt(self) -> None:
        storsta = 0.0
        for d, h1, h2, xi in FALL_HOJD:
            for andel in (0.0, 0.1, 0.37, 0.5, 0.8, 1.0):
                p = d8.hojd_cirkelstrale(d, h1, h2, xi, andel * d)
                h0, r1, r2 = _hojd_bokstavlig(d, h1, h2, xi, andel * d)
                storsta = max(storsta, abs(p.hojd_m - h0))
                self.assertAlmostEqual(p.hojd_m, h0, places=9)
                self.assertAlmostEqual(p.vag_fran_start_m, r1, places=8)
                self.assertAlmostEqual(p.vag_till_slut_m, r2, places=8)
        self.assertLess(storsta, 1.0e-9)

    def test_stammer_med_cirkelgeometrin_i_stralar_krokta(self) -> None:
        # cirkelbage(d, Δh, ξ′, c) är samma cirkel (ekv. 32 = ekv. 362): båglängden är
        # R₁ + R₂, lutningen i den nedre punkten är tan ψ_L, och alla punkter ligger på
        # cirkeln med radien 1/(ξ′·cos ψ_L) kring den medelpunkt som följer av ψ_L.
        for d, h1, h2, xi in FALL_HOJD:
            bage = cirkelbage(d, abs(h1 - h2), abs(xi), C_M_S)
            radie = 1.0 / (abs(xi) * math.cos(bage.vinkel_nedre_rad))
            mitt = d8.hojd_cirkelstrale(d, h1, h2, xi, 0.4 * d)
            self.assertAlmostEqual(
                mitt.vag_fran_start_m + mitt.vag_till_slut_m, bage.vagstracka_m, places=8
            )
            # Den punkt som ekv. (358)–(361) lägger i origo, och strålens riktning ut därifrån.
            tecken = 1.0 if xi > 0.0 else -1.0
            fran_start = (h1 <= h2) if xi > 0.0 else (h1 >= h2)
            x_l, z_l, riktning = (0.0, h1, 1.0) if fran_start else (d, h2, -1.0)
            # Medelpunkten: från den punkten, vinkelrätt mot tangenten, på den sida dit
            # strålen kröker (nedåt för ξ > 0, uppåt för ξ < 0).
            x_c = x_l + riktning * radie * math.sin(bage.vinkel_nedre_rad)
            z_c = z_l - tecken * radie * math.cos(bage.vinkel_nedre_rad)
            for andel in (0.0, 0.2, 0.5, 0.9, 1.0):
                p = d8.hojd_cirkelstrale(d, h1, h2, xi, andel * d)
                avstand = math.hypot(andel * d - x_c, p.hojd_m - z_c)
                self.assertAlmostEqual(avstand / radie, 1.0, places=11)
            # Lutningen i den punkten (numerisk derivata) mot tan ψ_L.
            steg = 1.0e-4 * d
            x_nara = x_l + riktning * steg
            lutning = (
                d8.hojd_cirkelstrale(d, h1, h2, xi, x_nara).hojd_m - z_l
            ) / steg  # höjdändring per meter bort från punkten
            self.assertAlmostEqual(
                tecken * lutning, math.tan(bage.vinkel_nedre_rad), delta=2.0e-4 * d * abs(xi) + 1e-9
            )

    def test_rat_strale_utan_refraktion(self) -> None:
        d, h1, h2 = 120.0, 0.5, 7.0
        langd = math.hypot(d, h2 - h1)
        for andel in (0.0, 0.25, 0.5, 0.9, 1.0):
            p = d8.hojd_cirkelstrale(d, h1, h2, 0.0, andel * d)
            self.assertAlmostEqual(p.hojd_m, h1 + andel * (h2 - h1), places=13)
            self.assertAlmostEqual(p.vag_fran_start_m, andel * langd, places=12)
            self.assertAlmostEqual(p.vag_till_slut_m, (1.0 - andel) * langd, places=12)
            # Gränsövergången: mycket svag refraktion ger samma höjd, från båda håll.
            for xi in (1.0e-12, -1.0e-12):
                q = d8.hojd_cirkelstrale(d, h1, h2, xi, andel * d)
                self.assertAlmostEqual(q.hojd_m, p.hojd_m, places=8)
                self.assertAlmostEqual(q.vag_fran_start_m, p.vag_fran_start_m, places=8)

    def test_pilhojd_vid_lika_hojder(self) -> None:
        # Lika höjder: strålens högsta punkt ligger mitt emellan, pilhöjden R − √(R² − d²/4)
        # över ändpunkterna, med R = 1/(ξ·cos ψ_L) och sin ψ_L = d/(2R), alltså
        # R = √(1/ξ² + d²/4).
        d, h, xi = 800.0, 2.0, 4.0e-4
        radie = math.sqrt(1.0 / xi**2 + d * d / 4.0)
        pil = radie - math.sqrt(radie * radie - d * d / 4.0)
        self.assertAlmostEqual(d8.hojd_cirkelstrale(d, h, h, xi, d / 2.0).hojd_m, h + pil, places=9)
        self.assertAlmostEqual(
            d8.hojd_cirkelstrale(d, h, h, -xi, d / 2.0).hojd_m, h - pil, places=9
        )

    def test_nedat_over_kordan_uppat_under(self) -> None:
        for d, h1, h2, xi in FALL_HOJD:
            for andel in (0.2, 0.5, 0.8):
                linje = h1 + andel * (h2 - h1)
                h0 = d8.hojd_cirkelstrale(d, h1, h2, xi, andel * d).hojd_m
                if xi > 0.0:
                    self.assertGreater(h0, linje)
                else:
                    self.assertLess(h0, linje)

    def test_symmetrier(self) -> None:
        for d, h1, h2, xi in FALL_HOJD:
            for andel in (0.15, 0.5, 0.7):
                p = d8.hojd_cirkelstrale(d, h1, h2, xi, andel * d)
                # Ombytta ändpunkter ger samma stråle.
                q = d8.hojd_cirkelstrale(d, h2, h1, xi, (1.0 - andel) * d)
                self.assertAlmostEqual(q.hojd_m, p.hojd_m, places=10)
                self.assertAlmostEqual(q.vag_fran_start_m, p.vag_till_slut_m, places=9)
                # Spegling i horisontalplanet byter tecken på ξ (ekv. 360–361).
                s = d8.hojd_cirkelstrale(d, -h1, -h2, -xi, andel * d)
                self.assertAlmostEqual(s.hojd_m, -p.hojd_m, places=10)
                # Formen beror bara på höjdskillnaden: ett annat höjdsystem flyttar bara strålen.
                t = d8.hojd_cirkelstrale(d, h1 + 37.5, h2 + 37.5, xi, andel * d)
                self.assertAlmostEqual(t.hojd_m, p.hojd_m + 37.5, places=9)
                self.assertAlmostEqual(t.vag_fran_start_m, p.vag_fran_start_m, places=9)

    def test_buller_fel(self) -> None:
        with self.assertRaises(BullerFel):
            d8.hojd_cirkelstrale(100.0, 1.0, 2.0, 1e-3, -1.0)
        with self.assertRaises(BullerFel):
            d8.hojd_cirkelstrale(100.0, 1.0, 2.0, 1e-3, 100.1)
        with self.assertRaises(BullerFel):
            d8.hojd_cirkelstrale(0.0, 1.0, 2.0, 1e-3, 0.0)
        with self.assertRaises(BullerFel):
            d8.hojd_cirkelstrale(100.0, 1.0, 2.0, math.nan, 10.0)


class TestGranssnittDelmodell10Och11(unittest.TestCase):
    def test_cirkelstrale_ar_en_stralbana(self) -> None:
        strale: d10.Stralbana = d8.Cirkelstrale(3.0, 10.5, 103.0, 11.5, 2.0e-3)
        self.assertEqual(strale.x_start_m, 3.0)
        self.assertEqual(strale.x_slut_m, 103.0)
        self.assertAlmostEqual(strale.punkt(3.0).z_m, 10.5, places=12)
        self.assertAlmostEqual(strale.punkt(103.0).z_m, 11.5, places=10)
        # R₁ växer med x och R₁ + R₂ är strålens hela längd.
        langder = [strale.punkt(3.0 + 10.0 * i) for i in range(11)]
        for fore, efter in zip(langder[:-1], langder[1:], strict=True):
            self.assertGreater(efter.r1_m, fore.r1_m)
            self.assertAlmostEqual(efter.r1_m + efter.r2_m, fore.r1_m + fore.r2_m, places=9)

    def test_utan_refraktion_lika_med_rat_strale(self) -> None:
        krokt = d8.cirkelstrale_ur_atmosfar(0.0, 20.5, 200.0, 24.0, _atmosfar())
        rat = d10.RatStrale(0.0, 20.5, 200.0, 24.0)
        self.assertEqual(krokt.xi_ray_per_m, 0.0)
        for x in (0.0, 13.0, 100.0, 187.5, 200.0):
            p, q = krokt.punkt(x), rat.punkt(x)
            self.assertAlmostEqual(p.z_m, q.z_m, places=12)
            self.assertAlmostEqual(p.r1_m, q.r1_m, places=11)
            self.assertAlmostEqual(p.r2_m, q.r2_m, places=11)

    def test_ekv_292_med_medvind(self) -> None:
        atm = _atmosfar(a_m_s=0.566)
        krokt = d8.cirkelstrale_ur_atmosfar(0.0, 0.5, 400.0, 1.5, atm)
        k = d8.stralkrokning(400.0, 0.5, 1.5, 0.566, 0.0, atm.c_m_s)
        self.assertEqual(krokt.xi_ray_per_m, k.xi_ray_per_m)
        # Källan är den nedre punkten, så cirkelns radie är R_A,B: pilhöjden över kordan i
        # mitten är nära d²/(8R).
        over_kordan = krokt.hojd_m(200.0) - 1.0
        self.assertAlmostEqual(over_kordan, 400.0**2 / (8.0 * k.radie_m), delta=0.01)
        self.assertGreater(over_kordan, 5.0)

    def test_strackan_i_en_zon_genom_delmodell_10(self) -> None:
        # Ett 5 m högt bälte mitt på en 400 m lång bana. Den räta strålen (1 m över marken)
        # går genom hela bältet; medvindsstrålen går över det, och R_sc blir noll.
        balte = (d10.Zonbalte(x_start_m=150.0, x_slut_m=250.0, topp_z_m=5.0),)
        rat = d8.cirkelstrale_ur_atmosfar(0.0, 0.5, 400.0, 1.5, _atmosfar())
        krokt = d8.cirkelstrale_ur_atmosfar(0.0, 0.5, 400.0, 1.5, _atmosfar(a_m_s=0.566))
        self.assertAlmostEqual(
            d10.stracka_i_zon_m(rat, d10.zonsnitt(rat, balte)), 100.0, delta=0.01
        )
        self.assertGreater(krokt.hojd_m(150.0), 5.0)
        self.assertEqual(d10.stracka_i_zon_m(krokt, d10.zonsnitt(krokt, balte)), 0.0)
        # Ett högre bälte (15 m) skär den krökta strålen; sträckan mäts då längs bågen.
        hogt = (d10.Zonbalte(x_start_m=150.0, x_slut_m=250.0, topp_z_m=15.0),)
        stracka = d10.stracka_i_zon_m(krokt, d10.zonsnitt(krokt, hogt))
        self.assertAlmostEqual(stracka, krokt.punkt(250.0).r1_m - krokt.punkt(150.0).r1_m)
        self.assertGreater(stracka, 100.0)
        self.assertLess(stracka, 100.1)

    def test_reflexgeometri_utan_refraktion_lika_med_rat(self) -> None:
        krokt = d8.reflexgeometri_krokt_strale(30.0, 50.0, 0.5, 4.0, 1.1, _atmosfar())
        rat = reflexgeometri_rak_strale(30.0, 50.0, 0.5, 4.0, 1.1)
        self.assertAlmostEqual(krokt.z_o_m, rat.z_o_m, places=13)
        self.assertAlmostEqual(krokt.r1_m, rat.r1_m, places=12)
        self.assertAlmostEqual(krokt.r2_m, rat.r2_m, places=12)
        self.assertEqual(krokt.theta_r_rad, rat.theta_r_rad)

    def test_reflexgeometri_ekv_298(self) -> None:
        atm = _atmosfar(a_m_s=0.5, b_per_s=0.02)
        d1, d2, z_s, z_r = 120.0, 80.0, 0.5, 4.0
        geo = d8.reflexgeometri_krokt_strale(d1, d2, z_s, z_r, 0.9, atm)
        k = d8.stralkrokning(d1 + d2, z_s, z_r, 0.5, 0.02, atm.c_m_s)
        p = d8.hojd_cirkelstrale(d1 + d2, z_s, z_r, k.xi_ray_per_m, d1)
        self.assertEqual(
            (geo.z_o_m, geo.r1_m, geo.r2_m), (p.hojd_m, p.vag_fran_start_m, p.vag_till_slut_m)
        )
        # Nedåtbrytning lyfter reflexpunkten över den räta strålens; uppåtbrytning sänker den.
        rat = reflexgeometri_rak_strale(d1, d2, z_s, z_r, 0.9)
        self.assertGreater(geo.z_o_m, rat.z_o_m)
        uppat = d8.reflexgeometri_krokt_strale(d1, d2, z_s, z_r, 0.9, _atmosfar(a_m_s=-0.5))
        self.assertLess(uppat.z_o_m, rat.z_o_m)
        with self.assertRaises(BullerFel):
            d8.reflexgeometri_krokt_strale(10.0, 10.0, 0.5, 20.0, 0.9, atm)
        with self.assertRaises(BullerFel):
            d8.reflexgeometri_krokt_strale(0.0, 10.0, 0.5, 1.0, 0.9, atm)


class TestAntalStralar(unittest.TestCase):
    def test_ekv_279_talvarde(self) -> None:
        # d = 1000 m, h_max = 1,5 m, A = 1 m/s, C = 340 m/s: N = (4000/1,5)·√(1/(2π·340)).
        n = d8.antal_stralar(1000.0, 1.5, 1.0, C_M_S)
        self.assertAlmostEqual(n, 4000.0 / 1.5 * math.sqrt(1.0 / (2.0 * math.pi * 340.0)))
        self.assertAlmostEqual(n, 57.695, places=3)
        self.assertEqual(d8.antal_stralar(1000.0, 1.5, 0.0, C_M_S), 0.0)
        with self.assertRaises(BullerFel):
            d8.antal_stralar(1000.0, 0.0, 1.0, C_M_S)
        with self.assertRaises(BullerFel):
            d8.antal_stralar(1000.0, 1.5, -1.0, C_M_S)

    def test_ekv_284_ger_a_nar_b_ar_noll(self) -> None:
        d, a = 700.0, 0.45
        atm = _atmosfar(a_m_s=a)
        radie = d8.krokningsradie_m(d, a, 0.0, atm.c_m_s)
        self.assertAlmostEqual(math.pi * atm.c_m_s * d * d / (32.0 * radie**2), a, places=13)
        self.assertEqual(d8.ekvivalent_a_m_s(d, atm), a)

    def test_ekv_284_med_linjar_del(self) -> None:
        d = 700.0
        atm = _atmosfar(a_m_s=0.45, b_per_s=0.03)
        radie = d8.krokningsradie_m(d, 0.45, 0.03, atm.c_m_s)
        a_ekv = d8.ekvivalent_a_m_s(d, atm)
        self.assertAlmostEqual(a_ekv, math.pi * atm.c_m_s * d * d / (32.0 * radie**2), places=13)
        # Den rent logaritmiska profilen med A′ har samma krökningsradie.
        self.assertAlmostEqual(d8.krokningsradie_m(d, a_ekv, 0.0, atm.c_m_s), radie, places=7)
        # En linjär del som bryter nedåt ökar A′, en som bryter uppåt minskar det.
        self.assertGreater(a_ekv, 0.45)
        self.assertLess(d8.ekvivalent_a_m_s(d, _atmosfar(a_m_s=0.45, b_per_s=-0.01)), 0.45)

    def test_ingen_nedatbrytning_ger_noll(self) -> None:
        for atm in (_atmosfar(), _atmosfar(a_m_s=-0.6), _atmosfar(b_per_s=-0.06)):
            self.assertEqual(d8.ekvivalent_a_m_s(500.0, atm), 0.0)
        # Uppåtbrytning som helhet trots A > 0.
        self.assertEqual(d8.ekvivalent_a_m_s(500.0, _atmosfar(a_m_s=0.01, b_per_s=-0.5)), 0.0)

    def test_ekv_280_281_ordning(self) -> None:
        self.assertEqual([d8.stralordning(m) for m in range(1, 10)], [1, 1, 1, 1, 2, 2, 2, 2, 3])
        self.assertEqual(d8.storsta_ordning(4.0), 1)
        self.assertEqual(d8.storsta_ordning(4.99), 1)
        self.assertEqual(d8.storsta_ordning(5.0), 2)
        self.assertEqual(d8.storsta_ordning(8.99), 2)
        self.assertEqual(d8.storsta_ordning(9.0), 3)
        with self.assertRaises(BullerFel):
            d8.stralordning(0)

    def test_stralhojden_for_ordningen_n_genom_4_ar_h_max(self) -> None:
        # Ekv. (279) och (283) hänger ihop: h_max(n)·n = h_max·N/4.
        d, a, h_max = 900.0, 0.7, 2.5
        n_tal = d8.antal_stralar(d, h_max, a, C_M_S)
        for ordning in (1, 2, 5):
            self.assertAlmostEqual(
                d8.stralhojd_m(ordning, d, a, C_M_S) * ordning, h_max * n_tal / 4.0, places=10
            )


class TestStorstaHojd(unittest.TestCase):
    def test_plan_mark_ger_max_av_hojderna(self) -> None:
        self.assertAlmostEqual(d8.storsta_hojd_m(_plan_profil(300.0, 200.0, 0.5, 4.0)), 4.0)
        self.assertAlmostEqual(d8.storsta_hojd_m(_plan_profil(300.0, 200.0, 2.0, 1.5)), 2.0)
        delad = _profil([0.0, 100.0, 300.0], [5.0, 5.0, 5.0], [200.0, 20000.0], 0.5, 1.5)
        self.assertAlmostEqual(d8.storsta_hojd_m(delad), 1.5)

    def test_lutande_plan_mark_raknas_fran_baslinjen(self) -> None:
        lutande = _profil([0.0, 300.0], [0.0, 30.0], [200.0], 0.5, 4.0)
        cosinus = 300.0 / math.hypot(300.0, 30.0)
        self.assertAlmostEqual(d8.storsta_hojd_m(lutande), 4.0 * cosinus, places=12)

    def test_skarm_hogre_an_kalla_och_mottagare(self) -> None:
        skarm = _profil(
            [0.0, 99.9, 100.0, 100.1, 300.0], [0.0, 0.0, 3.0, 0.0, 0.0], [200.0] * 4, 0.5, 1.5
        )
        # Medelavvikelsen är positiv (skärmen ligger över baslinjen) och ändrar inget.
        self.assertAlmostEqual(d8.storsta_hojd_m(skarm), 3.0, places=12)

    def test_dal_lagger_till_medelavvikelsen(self) -> None:
        # Symmetrisk V-dal, 6 m djup: medelavvikelsen från baslinjen är −3 m.
        dal = _profil([0.0, 150.0, 300.0], [0.0, -6.0, 0.0], [200.0, 200.0], 0.5, 1.5)
        self.assertAlmostEqual(d8.storsta_hojd_m(dal), 1.5 + 3.0, places=12)
        # En kulle (medelavvikelse > 0) ger kullens höjd, utan tillägg.
        kulle = _profil([0.0, 150.0, 300.0], [0.0, 6.0, 0.0], [200.0, 200.0], 0.5, 1.5)
        self.assertAlmostEqual(d8.storsta_hojd_m(kulle), 6.0, places=12)


class TestStralbidrag(unittest.TestCase):
    def test_strykvinkel_och_stralrorsarea_mot_derivatan(self) -> None:
        # |ψ_G − ψ′_G| är en differenskvot: ρ·h·dψ/dh med dψ/dh = (C/c²)·(dc/dh)/sin ψ och
        # dc/dh = A/(h + z₀) för profilen i ekv. (278).
        d, a, z0 = 1000.0, 1.0, 0.05
        for ordning in (1, 2, 7):
            h = d8.stralhojd_m(ordning, d, a, C_M_S)
            c_h = a * math.log(h / z0 + 1.0) + C_M_S
            psi = d8.strykvinkel_rad(h, a, C_M_S, z0)
            self.assertAlmostEqual(math.cos(psi), C_M_S / c_h, places=14)
            dpsi_dh = C_M_S / c_h**2 * a / (h + z0) / math.sin(psi)
            vantad = 10.0**-0.1 * math.sqrt(h * dpsi_dh / math.sin(psi))
            area = d8.stralrorsarea(ordning, d, a, C_M_S, z0)
            self.assertAlmostEqual(area / vantad, 1.0, places=4)
        # Talvärde för n = 2 (handräknat ur ekv. 283): h_max(2) = 10,82 m.
        self.assertAlmostEqual(d8.stralhojd_m(2, d, a, C_M_S), 10.818, places=3)
        with self.assertRaises(BullerFel):
            d8.stralrorsarea(1, d, 0.0, C_M_S, z0)

    def test_medelreflektion(self) -> None:
        f, psi = 500.0, 0.15
        mjuk = abs(planvagsreflektion(markimpedans(f, 200.0), psi))
        hard = abs(planvagsreflektion(markimpedans(f, 20000.0), psi))
        en = d8.markstrackor(_plan_profil(400.0, 200.0))
        self.assertAlmostEqual(d8.medelreflektion(f, psi, en), mjuk, places=14)
        # Uppdelning av samma mark i flera segment ändrar inget; blandad mark är längdvägd.
        delad = d8.markstrackor(_profil([0.0, 50.0, 130.0, 400.0], [0.0] * 4, [200.0] * 3))
        self.assertAlmostEqual(sum(s.andel for s in delad), 1.0, places=14)
        self.assertAlmostEqual(d8.medelreflektion(f, psi, delad), mjuk, places=14)
        blandad = d8.markstrackor(_profil([0.0, 100.0, 400.0], [0.0] * 3, [20000.0, 200.0]))
        self.assertAlmostEqual(
            d8.medelreflektion(f, psi, blandad), 0.25 * hard + 0.75 * mjuk, places=14
        )
        self.assertLess(mjuk, hard)
        self.assertLessEqual(hard, 1.0)


class TestBidrag(unittest.TestCase):
    def setUp(self) -> None:
        self.f: Flyttal = mittfrekvenser_hz()
        self.d = 1000.0
        self.a = 1.0
        self.z0 = 0.05
        # Mycket hård mark: |R_p| ≈ 1 i alla band, så termerna är A_ray(n).
        self.hard = (d8.Markstracka(andel=1.0, flodesresistivitet_kNsm4=1.0e12),)
        self.mjuk = (d8.Markstracka(andel=1.0, flodesresistivitet_kNsm4=200.0),)

    def _bidrag(
        self, antal: float, strackor: tuple[d8.Markstracka, ...], summering: d8.Summering = "hog"
    ) -> Flyttal:
        return d8.bidrag_flera_markreflexer_db(
            self.f, antal, self.d, self.a, C_M_S, self.z0, strackor, summering
        )

    def test_under_troskeln_inget_bidrag(self) -> None:
        for antal in (0.0, 1.0, 3.999, 4.0):
            for summering in ("hog", "lag"):
                dl = self._bidrag(antal, self.mjuk, summering)
                self.assertEqual(dl.shape, self.f.shape)
                self.assertTrue(bool(np.all(np.isneginf(dl))))

    def test_kontinuerligt_vid_troskeln(self) -> None:
        # Strax över N = 4 är bidraget ΔN·R(1)·A_ray(1): amplituden är proportionell mot
        # ΔN och går mot noll (−∞ dB) när N → 4.
        area = d8.stralrorsarea(1, self.d, self.a, C_M_S, self.z0)
        for rest in (1.0e-6, 1.0e-3, 0.5):
            for summering in ("hog", "lag"):
                dl = self._bidrag(4.0 + rest, self.hard, summering)
                np.testing.assert_allclose(dl, 20.0 * math.log10(rest * area), atol=1.0e-4)
        halverad = self._bidrag(4.001, self.mjuk) - self._bidrag(4.002, self.mjuk)
        np.testing.assert_allclose(halverad, -20.0 * math.log10(2.0), atol=1.0e-9)
        self.assertLess(float(np.max(self._bidrag(4.0 + 1.0e-9, self.hard))), -150.0)

    def test_kontinuitet_over_heltal_och_sprang_vid_ny_ordning(self) -> None:
        # Vid heltal där nästa stråle har samma ordning som n_max (N = 6, 7, 8) övergår
        # resten ΔN → 1 i en hel stråle utan språng.
        for heltal in (6.0, 7.0, 8.0, 11.0):
            for strackor in (self.hard, self.mjuk):
                under = self._bidrag(heltal - 1.0e-9, strackor)
                pa = self._bidrag(heltal, strackor)
                np.testing.assert_allclose(under, pa, atol=1.0e-6)
        # Vid N = 5 (och 9, 13, …) byter ekv. (281) ordning: strax under är bidraget en hel
        # stråle av ordning 1, i N = 5 en stråle av ordning 2. Metoden har alltså ett språng
        # där; testet håller fast att ekvationerna följs bokstavligt.
        area1 = d8.stralrorsarea(1, self.d, self.a, C_M_S, self.z0)
        area2 = d8.stralrorsarea(2, self.d, self.a, C_M_S, self.z0)
        np.testing.assert_allclose(
            self._bidrag(5.0 - 1.0e-9, self.hard), 20.0 * math.log10(area1), atol=1.0e-4
        )
        np.testing.assert_allclose(
            self._bidrag(5.0, self.hard), 20.0 * math.log10(area2), atol=1.0e-4
        )

    def test_hard_mark_heltal(self) -> None:
        # N = 8: strålarna 5–8 har alla ordningen 2 och ΔN = 0.
        area2 = d8.stralrorsarea(2, self.d, self.a, C_M_S, self.z0)
        np.testing.assert_allclose(
            self._bidrag(8.0, self.hard, "hog"), 10.0 * math.log10(4.0 * area2**2), atol=1e-4
        )
        np.testing.assert_allclose(
            self._bidrag(8.0, self.hard, "lag"), 20.0 * math.log10(4.0 * area2), atol=1e-4
        )
        # N = 10,5: strålarna 5–8 (n = 2), 9–10 (n = 3) och halva stråle 11 (n_max = 3).
        area3 = d8.stralrorsarea(3, self.d, self.a, C_M_S, self.z0)
        energi = 4.0 * area2**2 + 2.0 * area3**2 + (0.5 * area3) ** 2
        np.testing.assert_allclose(
            self._bidrag(10.5, self.hard, "hog"), 10.0 * math.log10(energi), atol=1e-4
        )
        amplitud = 4.0 * area2 + 2.0 * area3 + 0.5 * area3
        np.testing.assert_allclose(
            self._bidrag(10.5, self.hard, "lag"), 20.0 * math.log10(amplitud), atol=1e-4
        )

    def test_ekv_282_bokstavligt_mjuk_mark(self) -> None:
        antal = 14.3
        n_i = 14
        dn = antal - n_i
        n_max = int((antal - 1.0) / 4.0) + 1
        dl_hf = self._bidrag(antal, self.mjuk, "hog")
        dl_lf = self._bidrag(antal, self.mjuk, "lag")
        for i in (0, 9, 16, 26):
            f = float(self.f[i])

            def term(n: int, f: float = f) -> float:
                h = self.d / n * math.sqrt(self.a / (2.0 * math.pi * C_M_S))
                psi = math.acos(C_M_S / (self.a * math.log(h / self.z0 + 1.0) + C_M_S))
                psi_p = math.acos(
                    C_M_S / (self.a * math.log(h * (1.0 + 1e-5) / self.z0 + 1.0) + C_M_S)
                )
                area = 10.0**-0.1 * math.sqrt(abs(psi - psi_p) / (1e-5 * math.sin(psi)))
                r = abs(planvagsreflektion(markimpedans(f, 200.0), psi))
                return float(r**n * area)

            ordningar = [int((m - 1) / 4) + 1 for m in range(5, n_i + 1)]
            hf = (dn * term(n_max)) ** 2 + sum(term(n) ** 2 for n in ordningar)
            lf = dn * term(n_max) + sum(term(n) for n in ordningar)
            self.assertAlmostEqual(float(dl_hf[i]), 10.0 * math.log10(hf), places=9)
            self.assertAlmostEqual(float(dl_lf[i]), 20.0 * math.log10(lf), places=9)

    def test_egenskaper(self) -> None:
        for antal in (4.5, 9.0, 23.7, 57.7):
            hf_hard = self._bidrag(antal, self.hard, "hog")
            hf_mjuk = self._bidrag(antal, self.mjuk, "hog")
            lf_mjuk = self._bidrag(antal, self.mjuk, "lag")
            self.assertTrue(bool(np.all(np.isfinite(hf_mjuk))))
            # Koherent summa av positiva termer är minst lika stor som den inkoherenta.
            self.assertTrue(bool(np.all(lf_mjuk >= hf_mjuk - 1e-12)))
            # Absorberande mark ger lägre bidrag än hård mark.
            self.assertTrue(bool(np.all(hf_mjuk < hf_hard)))
        # Fler hela strålar ger mer energi (hård mark, samma d och A).
        self.assertTrue(bool(np.all(self._bidrag(16.0, self.hard) > self._bidrag(8.0, self.hard))))

    def test_buller_fel(self) -> None:
        with self.assertRaises(BullerFel):
            self._bidrag(8.0, self.hard, "mitt")  # type: ignore[arg-type]
        with self.assertRaises(BullerFel):
            d8.bidrag_flera_markreflexer_db(self.f, 8.0, self.d, self.a, C_M_S, 0.0, self.hard)


class TestFleraMarkreflexer(unittest.TestCase):
    def setUp(self) -> None:
        self.f: Flyttal = mittfrekvenser_hz()

    def test_utan_nedatbrytning_inget_bidrag(self) -> None:
        profil = _plan_profil(1000.0, 200.0)
        for atm in (_atmosfar(), _atmosfar(a_m_s=-0.566), _atmosfar(b_per_s=-0.06)):
            r = d8.flera_markreflexer(self.f, profil, atm)
            self.assertEqual(r.antal_stralar, 0.0)
            self.assertFalse(r.bidrar)
            self.assertTrue(bool(np.all(np.isneginf(r.delta_l_db))))

    def test_svag_nedatbrytning_under_troskeln(self) -> None:
        # Medvind 3 m/s på 10 m (A = 0,566) över 100 m med h_R = 4 m: N = 100·√(A/(2πC)) < 4.
        atm = _atmosfar(a_m_s=0.566)
        r = d8.flera_markreflexer(self.f, _plan_profil(100.0, 200.0, 0.5, 4.0), atm)
        self.assertAlmostEqual(
            r.antal_stralar, 100.0 * math.sqrt(0.566 / (2.0 * math.pi * atm.c_m_s)), places=12
        )
        self.assertLess(r.antal_stralar, 4.0)
        self.assertFalse(r.bidrar)
        self.assertTrue(bool(np.all(np.isneginf(r.delta_l_db))))

    def test_stark_nedatbrytning_ger_bidrag(self) -> None:
        atm = _atmosfar(a_m_s=0.566)
        profil = _plan_profil(1000.0, 200.0, 0.5, 1.5)
        r = d8.flera_markreflexer(self.f, profil, atm)
        self.assertEqual(r.storsta_hojd_m, 1.5)
        self.assertEqual(r.a_ekvivalent_m_s, 0.566)
        self.assertAlmostEqual(
            r.antal_stralar, 4000.0 / 1.5 * math.sqrt(0.566 / (2.0 * math.pi * atm.c_m_s))
        )
        self.assertTrue(r.bidrar)
        self.assertTrue(bool(np.all(np.isfinite(r.delta_l_db))))
        direkt = d8.bidrag_flera_markreflexer_db(
            self.f,
            r.antal_stralar,
            1000.0,
            0.566,
            atm.c_m_s,
            atm.z0_effektiv_m,
            d8.markstrackor(profil),
        )
        np.testing.assert_array_equal(r.delta_l_db, direkt)
        lag = d8.flera_markreflexer(self.f, profil, atm, "lag")
        self.assertTrue(bool(np.all(lag.delta_l_db >= r.delta_l_db)))

    def test_ombytt_kalla_och_mottagare(self) -> None:
        # h_max, d och markens medelvärde är symmetriska i källa och mottagare.
        atm = _atmosfar(a_m_s=0.4, b_per_s=0.01)
        fram = _profil([0.0, 300.0, 800.0], [0.0, 0.0, 0.0], [20000.0, 200.0], 0.5, 3.0)
        bak = _profil([0.0, 500.0, 800.0], [0.0, 0.0, 0.0], [200.0, 20000.0], 3.0, 0.5)
        r1 = d8.flera_markreflexer(self.f, fram, atm)
        r2 = d8.flera_markreflexer(self.f, bak, atm)
        self.assertTrue(r1.bidrar)
        self.assertAlmostEqual(r1.antal_stralar, r2.antal_stralar, places=10)
        np.testing.assert_allclose(r1.delta_l_db, r2.delta_l_db, atol=1e-9)

    def test_energisumma(self) -> None:
        dl_t = np.array([-3.0, 2.0, -10.0])
        ingen = np.full(3, -math.inf)
        np.testing.assert_array_equal(d8.energisumma_db(dl_t, ingen), dl_t)
        np.testing.assert_allclose(
            d8.energisumma_db(dl_t, dl_t), dl_t + 10.0 * math.log10(2.0), atol=1e-12
        )
        np.testing.assert_allclose(
            d8.energisumma_db(np.array([0.0]), np.array([-10.0])), 10.0 * math.log10(1.1)
        )
        self.assertTrue(bool(np.isneginf(d8.energisumma_db(ingen, ingen)).all()))


if __name__ == "__main__":
    unittest.main()
