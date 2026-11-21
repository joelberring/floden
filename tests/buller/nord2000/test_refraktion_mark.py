"""Tester för refraktion i plan mark-modellen (paket P-R2).

Svaren följer av metodtexten (gränsfall, brytpunkter), av geometri som räknas oberoende av
funktionerna (cirklar, ellipser) eller av egenskaper som måste gälla (symmetri, riktning,
gränsvärdet utan refraktion). Ingen jämförelse görs här mot testfallens facit; den står i
`tests/buller/test_testsvit_n.py` och i `docs/buller/logg/P-R2.md`.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.nord2000 import delmodell8
from buller.nord2000.atmosfar import HomogenAtmosfar, som_atmosfar
from buller.nord2000.delmodell1 import (
    koherens_plan_mark,
    markeffekt_plan_mark_db,
)
from buller.nord2000.delmodell2 import Marksegment, Skuggzonsvikter, markeffekt_blandad_mark_db
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.fresnel import (
    fresnelzon_langs_mark,
    fresnelzon_langs_mark_refraktion,
    fresnelzonsvikt,
    fresnelzonsvikt_modifierad,
    fresnelzonsvikt_modifierad_refraktion,
    fresnelzonsvikt_refraktion,
    klippt_fresnelzon,
    reflexgeometri,
    reflexgeometri_skuggzon,
)
from buller.nord2000.karna import (
    STORSTA_A0_BANDFORMEL_DB,
    Nord2000Karna,
    luftabsorption_monoton_db,
)
from buller.nord2000.kildiffraktion import Kil, kiltryck_icke_reflekterande
from buller.nord2000.luftabsorption import luftabsorption_db, rentonsdampning_db_per_m
from buller.nord2000.mark import markimpedans
from buller.nord2000.refraktion import Atmosfar, atmosfar_ur_vader, calc_eq_ssp, ovre_atmosfar
from buller.nord2000.refraktion_mark import (
    Skuggzonslasning,
    calc_eq_ssp_mark,
    gradient_skuggzon_per_m,
    gradientfaktor,
    kilavstand_skuggzon_m,
    overgangsfrekvenser_refraktion_hz,
    skuggzonsavskarmning_db,
    skuggzonshojd_m,
)
from buller.nord2000.stralar_krokta import (
    Uppatmetod,
    skuggzonsavstand_m,
    stralar_krokt_plan_mark,
)
from buller.testfall.korare import vindkomponent_m_s
from buller.typer import BullerFel, Terrangprofil, Vader

F = mittfrekvenser_hz()
UPPAT: tuple[Uppatmetod, ...] = ("bokstavlig", "exakt")
VIKTER: tuple[Skuggzonsvikter, ...] = ("hog", "lag", "ovre_band")
MEDVIND = atmosfar_ur_vader(Vader(vind_m_s=3.0))
MOTVIND = atmosfar_ur_vader(Vader(vind_m_s=-3.0))
STILLA = atmosfar_ur_vader(Vader())
# Vädret i testfallens grupper 2–12 (RL 18/18), här med motvind.
MOTVIND_TURBULENT = atmosfar_ur_vader(Vader(vind_m_s=-3.0, std_vind_m_s=0.5, cv2=0.12, ct2=0.008))


# HeightOfCircularRay, RayCurvature och N fanns till och med P-R2 också i `refraktion_mark.py`.
# Sedan P-S1 finns de bara i `delmodell8.py`; testerna nedan prövar den enda källan genom
# samma anrop som förut.
def hojd_cirkelstrale(
    d: float, h1: float, h2: float, xi: float, d0: float
) -> tuple[float, float, float]:
    p = delmodell8.hojd_cirkelstrale(d, h1, h2, xi, d0)
    return p.hojd_m, p.vag_fran_start_m, p.vag_till_slut_m


def krokningsradie_m(d: float, atm: Atmosfar) -> float:
    return delmodell8.krokningsradie_m(d, atm.a_m_s, atm.b_per_s, atm.c_m_s)


def antal_stralar_flera_markreflexer(d: float, h_max: float, atm: Atmosfar) -> float:
    return delmodell8.antal_stralar(d, h_max, delmodell8.ekvivalent_a_m_s(d, atm), atm.c_m_s)


def hojd_cirkelstrale_bokstavlig(
    d: float, h1_in: float, h2_in: float, xi_in: float, d0_in: float
) -> tuple[float, float, float]:
    """AV 1106/07 ekv. (356)–(368) rakt av (P-R2:s tidigare `refraktion_mark.hojd_cirkelstrale`).

    Oberoende räkneväg till `delmodell8.hojd_cirkelstrale`, som använder en omskriven form utan
    utsläckning. Gäller ξ ≠ 0.
    """
    xi = abs(xi_in)  # ekv. (356)
    dh = abs(h1_in - h2_in)  # ekv. (357)
    if xi_in > 0.0:
        h1, d0 = (h2_in, d - d0_in) if h1_in > h2_in else (h1_in, d0_in)  # ekv. (358)–(359)
    elif h1_in >= h2_in:
        h1, d0 = -h1_in, d0_in  # ekv. (360)
    else:
        h1, d0 = -h2_in, d - d0_in  # ekv. (361)
    tan_psi = 0.5 * xi * d + dh * (2.0 + xi * dh) / (2.0 * d)  # ekv. (362)
    x0 = tan_psi / xi  # ekv. (363)
    z0 = h1 - 1.0 / xi  # ekv. (364)
    radie = math.sqrt(1.0 + tan_psi * tan_psi) / xi  # ekv. (365)
    theta = math.acos((d0 - x0) / radie)  # ekv. (366)
    h0 = math.copysign(1.0, xi_in) * (radie * math.sin(theta) + z0)  # ekv. (367)
    korda1 = math.hypot(d0_in, h0 - h1_in)
    korda2 = math.hypot(d - d0_in, h2_in - h0)
    return (
        h0,
        2.0 * radie * math.asin(korda1 / (2.0 * radie)),
        2.0 * radie * math.asin(korda2 / (2.0 * radie)),
    )  # ekv. (368)


def antal_stralar_bokstavlig(d: float, h_max: float, atm: Atmosfar) -> float:
    """N ur ekv. (54), (284) och (279) rakt av (P-R2:s tidigare räkneväg), 0 utan nedåtbrytning."""
    c = atm.c_m_s
    krokning = 0.0
    if atm.a_m_s != 0.0:
        krokning += math.copysign(
            1.0 / (d / 8.0 * math.sqrt(2.0 * math.pi * c / abs(atm.a_m_s))), atm.a_m_s
        )
    if atm.b_per_s != 0.0:
        krokning += math.copysign(1.0 / math.hypot(c / abs(atm.b_per_s), 0.5 * d), atm.b_per_s)
    if krokning <= 0.0:
        return 0.0
    a_ekvivalent = math.pi * c * d**2 * krokning**2 / 32.0
    return 4.0 * d / h_max * math.sqrt(a_ekvivalent / (2.0 * math.pi * c))


def impedanser(sigma: float) -> list[complex]:
    return [markimpedans(float(f), sigma) for f in F]


def plan_profil(
    d: float, sigma: list[float], x: list[float], h_s: float, h_r: float
) -> Terrangprofil:
    return Terrangprofil(
        x_m=np.array(x, dtype=np.float64),
        z_m=np.zeros(len(x), dtype=np.float64),
        flodesresistivitet_kNsm4=np.array(sigma, dtype=np.float64),
        rahet_m=np.zeros(len(sigma), dtype=np.float64),
        kalla_hojd_m=h_s,
        mottagare_hojd_m=h_r,
    )


class TestCalcEqSSPGround(unittest.TestCase):
    """AV 1106/07 avsnitt 5.5.3, ekv. (23)–(28)."""

    def test_hard_mark_ar_frekvensoberoende(self) -> None:
        p = calc_eq_ssp_mark(F, 100.0, 0.3, 1.5, 20000.0, impedanser(20000.0), MEDVIND)
        referens = calc_eq_ssp(0.3, 1.5, MEDVIND)
        self.assertEqual(p.oberoende, referens)
        self.assertTrue(all(q == referens for q in p.per_frekvens))
        self.assertIsNone(p.f_l_hz)

    def test_gransen_for_mjuk_mark(self) -> None:
        """10 000 000 Pa·s/m² = 10 000 kNs/m⁴: precis under är mjuk mark."""
        mjuk = calc_eq_ssp_mark(F, 100.0, 0.3, 1.5, 9999.0, impedanser(9999.0), MEDVIND)
        hard = calc_eq_ssp_mark(F, 100.0, 0.3, 1.5, 10000.0, impedanser(10000.0), MEDVIND)
        self.assertIsNotNone(mjuk.f_l_hz)
        self.assertIsNone(hard.f_l_hz)

    def test_oberoende_profil_ar_calc_eq_ssp(self) -> None:
        for atm in (MEDVIND, MOTVIND, atmosfar_ur_vader(Vader(dt_dz_k_m=0.1))):
            for h_s, h_r in ((0.01, 1.5), (0.3, 4.0), (2.0, 2.0)):
                p = calc_eq_ssp_mark(F, 100.0, h_s, h_r, 200.0, impedanser(200.0), atm)
                self.assertEqual(p.oberoende, calc_eq_ssp(h_s, h_r, atm))

    def test_ingen_refraktion_under_fl_full_over_fh(self) -> None:
        p = calc_eq_ssp_mark(F, 100.0, 0.3, 1.5, 200.0, impedanser(200.0), MEDVIND)
        assert p.f_l_hz is not None and p.f_h_hz is not None
        self.assertLess(p.f_l_hz, p.f_h_hz)
        for f, profil in zip(F, p.per_frekvens, strict=True):
            if f <= p.f_l_hz:
                self.assertEqual(profil.xi_per_m, 0.0)
                self.assertEqual(profil.c0_m_s, MEDVIND.c_m_s)
            elif f >= p.f_h_hz:
                self.assertEqual(profil, p.oberoende)
            else:
                self.assertGreater(profil.xi_per_m, 0.0)
                self.assertLess(profil.xi_per_m, p.oberoende.xi_per_m)

    def test_overgangen_foljer_ekv_23(self) -> None:
        """Mellan f_L och f_H: ξ(f)·c₀(f) är faktorn gånger Δc/Δz (ekv. 17, 20 och 23)."""
        p = calc_eq_ssp_mark(F, 100.0, 0.3, 1.5, 200.0, impedanser(200.0), MEDVIND)
        assert p.f_l_hz is not None and p.f_h_hz is not None
        gradient = p.oberoende.xi_per_m * p.oberoende.c0_m_s
        antal = 0
        for f, profil in zip(F, p.per_frekvens, strict=True):
            if p.f_l_hz < f < p.f_h_hz:
                faktor = (math.log10(f) - math.log10(p.f_l_hz)) / (
                    math.log10(p.f_h_hz) - math.log10(p.f_l_hz)
                )
                self.assertAlmostEqual(profil.xi_per_m * profil.c0_m_s, faktor * gradient, 12)
                antal += 1
        self.assertGreater(antal, 0)

    def test_gradientfaktor(self) -> None:
        self.assertEqual(gradientfaktor(100.0, 100.0, 400.0), 0.0)
        self.assertEqual(gradientfaktor(400.0, 100.0, 400.0), 1.0)
        self.assertAlmostEqual(gradientfaktor(200.0, 100.0, 400.0), 0.5, 12)

    def test_fl_enligt_ekv_26_och_fh_enligt_ekv_27(self) -> None:
        z = impedanser(200.0)
        c = 340.0
        f_pi, f_h_1 = overgangsfrekvenser_refraktion_hz(F, 100.0, 0.5, 1.5, z, c, 1.0)
        f_l_3, _ = overgangsfrekvenser_refraktion_hz(F, 100.0, 0.5, 1.5, z, c, 3.0)
        f_l_5, _ = overgangsfrekvenser_refraktion_hz(F, 100.0, 0.5, 1.5, z, c, 5.0)
        f_l_neg, _ = overgangsfrekvenser_refraktion_hz(F, 100.0, 0.5, 1.5, z, c, -4.0)
        self.assertAlmostEqual(f_l_3 / f_pi, (43.0 - 9.0) / 40.0, 12)
        self.assertAlmostEqual(f_l_5 / f_pi, 0.7, 12)
        self.assertEqual(f_l_neg, f_pi)
        # Ekv. (26) är kontinuerlig i Δc₁₀ = 1 och Δc₁₀ = 5.
        f_l_1e, _ = overgangsfrekvenser_refraktion_hz(F, 100.0, 0.5, 1.5, z, c, 1.0 + 1e-12)
        f_l_5e, _ = overgangsfrekvenser_refraktion_hz(F, 100.0, 0.5, 1.5, z, c, 5.0 - 1e-12)
        self.assertAlmostEqual(f_l_1e / f_pi, 1.0, 9)
        self.assertAlmostEqual(f_l_5e / f_pi, 0.7, 9)
        self.assertGreaterEqual(f_h_1, 1.25 * f_pi)

    def test_hojder_och_avstand_begransas_i_ekv_24(self) -> None:
        """h < 0,5 m räknas som 0,5 m och d > 400 m som 400 m."""
        z = impedanser(200.0)
        a = overgangsfrekvenser_refraktion_hz(F, 400.0, 0.5, 0.5, z, 340.0, 0.0)
        b = overgangsfrekvenser_refraktion_hz(F, 2000.0, 0.01, 0.3, z, 340.0, 0.0)
        self.assertEqual(a, b)

    def test_fel_antal_impedanser(self) -> None:
        with self.assertRaises(BullerFel):
            calc_eq_ssp_mark(F, 100.0, 0.3, 1.5, 200.0, impedanser(200.0)[:5], MEDVIND)


class TestHojdCirkelstrale(unittest.TestCase):
    """HeightOfCircularRay, AV 1106/07 avsnitt 5.23.9."""

    FALL = (
        (100.0, 0.5, 1.5, 2.0e-3),
        (100.0, 1.5, 0.5, 2.0e-3),
        (250.0, 2.0, 2.0, 5.0e-4),
        (100.0, 0.5, 1.5, -2.0e-3),
        (100.0, 1.5, 0.5, -2.0e-3),
        (60.0, 4.0, 0.3, -1.0e-3),
    )

    def test_gar_genom_andpunkterna(self) -> None:
        for d, h1, h2, xi in self.FALL:
            with self.subTest(d=d, h1=h1, h2=h2, xi=xi):
                self.assertAlmostEqual(hojd_cirkelstrale(d, h1, h2, xi, 0.0)[0], h1, 9)
                self.assertAlmostEqual(hojd_cirkelstrale(d, h1, h2, xi, d)[0], h2, 9)

    def test_punkterna_ligger_pa_en_cirkel_med_ratt_krokning(self) -> None:
        """Tre punkter på strålen bestämmer en cirkel; den ska gälla för en fjärde punkt.

        Cirkeln räknas här ur tre punkter (omskriven cirkel), oberoende av ekv. (362)–(367).
        Nedåtbrytning (ξ > 0) ger en båge över kordan, uppåtbrytning en båge under den.
        """
        for d, h1, h2, xi in self.FALL:
            with self.subTest(d=d, h1=h1, h2=h2, xi=xi):
                x = [0.0, 0.3 * d, 0.7 * d, d]
                z = [hojd_cirkelstrale(d, h1, h2, xi, xx)[0] for xx in x]
                (ax, az), (bx, bz), (cx, cz) = (x[0], z[0]), (x[1], z[1]), (x[3], z[3])
                det = 2.0 * (ax * (bz - cz) + bx * (cz - az) + cx * (az - bz))
                ux = (
                    (ax**2 + az**2) * (bz - cz)
                    + (bx**2 + bz**2) * (cz - az)
                    + (cx**2 + cz**2) * (az - bz)
                ) / det
                uz = (
                    (ax**2 + az**2) * (cx - bx)
                    + (bx**2 + bz**2) * (ax - cx)
                    + (cx**2 + cz**2) * (bx - ax)
                ) / det
                radie = math.hypot(ax - ux, az - uz)
                self.assertAlmostEqual(math.hypot(x[2] - ux, z[2] - uz) / radie, 1.0, 9)
                # Radien är 1/(|ξ|·cos ψ_L): något större än 1/|ξ|, och nära det för flacka strålar.
                self.assertGreaterEqual(radie * abs(xi), 1.0 - 1e-9)
                self.assertLess(radie * abs(xi), 1.05)
                korda_mitt = h1 + (h2 - h1) * 0.3
                if xi > 0.0:
                    self.assertGreater(z[1], korda_mitt)
                else:
                    self.assertLess(z[1], korda_mitt)

    def test_ombytta_andar_ger_samma_strale(self) -> None:
        for d, h1, h2, xi in self.FALL:
            for d0 in (0.2 * d, 0.5 * d, 0.9 * d):
                a = hojd_cirkelstrale(d, h1, h2, xi, d0)
                b = hojd_cirkelstrale(d, h2, h1, xi, d - d0)
                self.assertAlmostEqual(a[0], b[0], 9)
                self.assertAlmostEqual(a[1], b[2], 9)
                self.assertAlmostEqual(a[2], b[1], 9)

    def test_baglangder(self) -> None:
        """R₁ och R₂ är längre än kordorna men nära dem; summan växer med krökningen."""
        d, h1, h2 = 100.0, 0.5, 1.5
        _, r1, r2 = hojd_cirkelstrale(d, h1, h2, 2.0e-3, 40.0)
        h0 = hojd_cirkelstrale(d, h1, h2, 2.0e-3, 40.0)[0]
        self.assertGreater(r1, math.hypot(40.0, h0 - h1))
        self.assertGreater(r2, math.hypot(60.0, h2 - h0))
        self.assertLess(r1 + r2, 1.01 * d)
        # Jämför med DirectRay (P-R1) för samma ξ: samma båge inom |ξ|·h (ξ′ ≈ ξ).
        direkt = stralar_krokt_plan_mark(d, h1, h2, 2.0e-3, 340.0, 340.0).direkt
        self.assertAlmostEqual((r1 + r2) / direkt.vagstracka_m, 1.0, 5)

    def test_rat_linje_utan_refraktion(self) -> None:
        h0, r1, r2 = hojd_cirkelstrale(100.0, 0.5, 1.5, 0.0, 25.0)
        self.assertAlmostEqual(h0, 0.75, 12)
        self.assertAlmostEqual(r1 + r2, math.hypot(100.0, 1.0), 12)
        svag = hojd_cirkelstrale(100.0, 0.5, 1.5, 1.0e-9, 25.0)
        self.assertAlmostEqual(svag[0], h0, 5)

    def test_strale_som_tangerar_marken(self) -> None:
        """Lika höjder h och d² = 4·h·(2/|ξ| + h): den speglade bågens topp ligger i z = 0.

        Följer av ekv. (362)–(367) med Δh = 0: h₀(d/2) = h + 1/ξ′ − √(1 + (ξ′d/2)²)/ξ′.
        Avståndet skiljer sig från d_SZ (ekv. 43, exakt cirkel) med relativt mindre än |ξ|·h.
        """
        h, xi = 1.2, -1.5e-3
        d = 2.0 * math.sqrt(h * (2.0 / abs(xi) + h))
        self.assertAlmostEqual(hojd_cirkelstrale(d, h, h, xi, d / 2.0)[0], 0.0, 9)
        self.assertGreater(hojd_cirkelstrale(0.9 * d, h, h, xi, 0.45 * d)[0], 0.0)
        self.assertLess(hojd_cirkelstrale(1.1 * d, h, h, xi, 0.55 * d)[0], 0.0)
        self.assertAlmostEqual(d / skuggzonsavstand_m(h, h, xi, "exakt"), 1.0, delta=abs(xi) * h)

    def test_fel(self) -> None:
        with self.assertRaises(BullerFel):
            hojd_cirkelstrale(0.0, 1.0, 1.0, 1e-3, 0.0)
        with self.assertRaises(BullerFel):  # punkten ligger utanför cirkeln
            hojd_cirkelstrale(100.0, 1.0, 1.0, 1e-2, 500.0)


class TestSkuggzonsavskarmning(unittest.TestCase):
    """ShadowZoneShielding, AV 1106/07 avsnitt 5.23.16, och AV 1851/00 ekv. (41)–(42)."""

    def test_kilens_lage(self) -> None:
        self.assertAlmostEqual(kilavstand_skuggzon_m(100.0, 1.0, 1.0), 50.0, 12)
        self.assertAlmostEqual(kilavstand_skuggzon_m(100.0, 1.0, 4.0), 100.0 / 3.0, 12)
        a = kilavstand_skuggzon_m(137.0, 0.3, 1.5)
        b = kilavstand_skuggzon_m(137.0, 1.5, 0.3)
        self.assertAlmostEqual(a + b, 137.0, 12)

    def test_kilens_lage_ar_tangeringspunkten_vid_skuggzonens_grans(self) -> None:
        """På d = d_SZ ligger strålens lägsta punkt på avståndet √(h_S·(2/|ξ| − h_S)) från källan.

        Ekv. (41) i AV 1851/00 är närmevärdet √h_S/(√h_S + √h_R)·d av det (ekv. 43 i
        AV 1106/07 är summan av de två delsträckorna).
        """
        h_s, h_r, xi = 0.3, 1.5, -2.0e-3
        d_sz = skuggzonsavstand_m(h_s, h_r, xi, "exakt")
        exakt = math.sqrt(h_s * (2.0 / abs(xi) - h_s))
        self.assertAlmostEqual(kilavstand_skuggzon_m(d_sz, h_s, h_r) / exakt, 1.0, 3)

    def test_gradientens_frekvensberoende(self) -> None:
        """Ekv. (385): 0 vid 20 Hz, ξ från 2000 Hz, linjär i lg f däremellan."""
        xi = -2.0e-3
        self.assertAlmostEqual(gradient_skuggzon_per_m(20.0, xi), 0.0, 15)
        self.assertAlmostEqual(gradient_skuggzon_per_m(200.0, xi), 0.5 * xi, 15)
        self.assertEqual(gradient_skuggzon_per_m(2000.0, xi), xi)
        self.assertEqual(gradient_skuggzon_per_m(8000.0, xi), xi)

    def test_ingen_avskarmning_nar_stralen_gar_over_marken(self) -> None:
        d, h_s, h_r, xi = 40.0, 0.3, 1.5, -2.0e-3  # d < d_SZ = 55 m
        kil = kilavstand_skuggzon_m(d, h_s, h_r)
        for f in F:
            self.assertLess(skuggzonshojd_m(float(f), d, h_s, h_r, xi, kil), 0.0)
            self.assertEqual(skuggzonsavskarmning_db(float(f), d, h_s, h_r, xi, 340.0, kil), 0.0)

    def test_noll_for_platt_kil(self) -> None:
        """Faktorn 2 i ekv. (387) gör att en kil med β → π ger 0 dB (AV 1851/00 avsnitt 4)."""
        d, d_kil, c = 100.0, 30.0, 340.0
        for f in (25.0, 1000.0, 10000.0):
            for h in (1.0e-9, 1.0e-6):
                theta = math.pi + math.atan(h / d_kil) + math.atan(h / (d - d_kil))
                r_s, r_r = math.hypot(h, d_kil), math.hypot(h, d - d_kil)
                kil = Kil(theta, theta, 0.0, (r_s + r_r) / c, r_s / c, r_r / c, r_s + r_r, r_s, r_r)
                niva = 20.0 * math.log10(2.0 * d * abs(kiltryck_icke_reflekterande(f, kil)))
                self.assertAlmostEqual(niva, 0.0, 3)

    def test_kontinuerlig_vid_skuggzonens_grans(self) -> None:
        """ΔL_SZ går mot 0 när strålen just når marken vid kilens topp."""
        h_s, h_r, xi, f = 0.3, 1.5, -2.0e-3, 4000.0

        def hojd(d: float) -> float:
            return skuggzonshojd_m(f, d, h_s, h_r, xi, kilavstand_skuggzon_m(d, h_s, h_r))

        nedre, ovre = 40.0, 80.0
        self.assertLess(hojd(nedre), 0.0)
        self.assertGreater(hojd(ovre), 0.0)
        for _ in range(60):
            mitt = 0.5 * (nedre + ovre)
            if hojd(mitt) > 0.0:
                ovre = mitt
            else:
                nedre = mitt
        for steg, grans in ((1.0e-6, 1.0e-3), (1.0e-3, 0.05)):
            d = ovre + steg
            niva = skuggzonsavskarmning_db(
                f, d, h_s, h_r, xi, 340.0, kilavstand_skuggzon_m(d, h_s, h_r)
            )
            # Närmeformlerna i kildiffraktionen ger några miljondels dB över 0 för en platt kil.
            self.assertLess(niva, 1.0e-4)
            self.assertGreater(niva, -grans)

    def test_vaxer_med_avstand_frekvens_och_gradient(self) -> None:
        h_s, h_r, c = 0.3, 1.5, 340.0

        def niva(f: float, d: float, xi: float) -> float:
            return skuggzonsavskarmning_db(
                f, d, h_s, h_r, xi, c, kilavstand_skuggzon_m(d, h_s, h_r)
            )

        self.assertLess(niva(1000.0, 200.0, -2e-3), niva(1000.0, 100.0, -2e-3))
        self.assertLess(niva(4000.0, 100.0, -2e-3), niva(1000.0, 100.0, -2e-3))
        self.assertLess(niva(1000.0, 100.0, -4e-3), niva(1000.0, 100.0, -2e-3))
        self.assertLess(niva(1000.0, 100.0, -2e-3), 0.0)

    def test_reciprocitet(self) -> None:
        d, xi, c = 150.0, -2.0e-3, 340.0
        for f in (63.0, 500.0, 4000.0):
            a = skuggzonsavskarmning_db(f, d, 0.3, 1.5, xi, c, kilavstand_skuggzon_m(d, 0.3, 1.5))
            b = skuggzonsavskarmning_db(f, d, 1.5, 0.3, xi, c, kilavstand_skuggzon_m(d, 1.5, 0.3))
            self.assertAlmostEqual(a, b, 8)

    def test_fel(self) -> None:
        with self.assertRaises(BullerFel):
            skuggzonsavskarmning_db(1000.0, 100.0, 0.3, 1.5, -2e-3, 340.0, 100.0)
        with self.assertRaises(BullerFel):
            skuggzonsavskarmning_db(1000.0, 100.0, 0.3, 1.5, -2e-3, 0.0, 30.0)
        with self.assertRaises(BullerFel):
            kilavstand_skuggzon_m(100.0, 0.0, 1.5)


class TestFleraMarkreflexer(unittest.TestCase):
    """N i AV 1106/07 ekv. (279) med RayCurvature (ekv. 54) och A′ (ekv. 284)."""

    def test_logaritmisk_profil_ger_ekv_279_direkt(self) -> None:
        d, h = 96.75, 1.5
        vantad = 4.0 * d / h * math.sqrt(MEDVIND.a_m_s / (2.0 * math.pi * MEDVIND.c_m_s))
        self.assertAlmostEqual(antal_stralar_flera_markreflexer(d, h, MEDVIND), vantad, 9)
        self.assertGreater(vantad, 4.0)  # medvind 3 m/s på 100 m med h_R = 1,5 m: N ≈ 4,2

    def test_krokningsradie(self) -> None:
        d = 200.0
        c = MEDVIND.c_m_s
        self.assertAlmostEqual(
            krokningsradie_m(d, MEDVIND),
            d / 8.0 * math.sqrt(2.0 * math.pi * c / MEDVIND.a_m_s),
            9,
        )
        self.assertLess(krokningsradie_m(d, MOTVIND), 0.0)
        self.assertEqual(krokningsradie_m(d, STILLA), math.inf)
        varm = atmosfar_ur_vader(Vader(dt_dz_k_m=0.1))
        self.assertAlmostEqual(krokningsradie_m(d, varm), math.hypot(c / varm.b_per_s, d / 2.0), 9)

    def test_uppat_och_ingen_refraktion_ger_noll(self) -> None:
        self.assertEqual(antal_stralar_flera_markreflexer(1000.0, 1.5, MOTVIND), 0.0)
        self.assertEqual(antal_stralar_flera_markreflexer(1000.0, 1.5, STILLA), 0.0)
        kall = atmosfar_ur_vader(Vader(dt_dz_k_m=-0.1))
        self.assertEqual(antal_stralar_flera_markreflexer(1000.0, 1.5, kall), 0.0)

    def test_vaxer_med_avstandet(self) -> None:
        varm = atmosfar_ur_vader(Vader(dt_dz_k_m=0.1))
        n = [antal_stralar_flera_markreflexer(d, 1.5, varm) for d in (50.0, 100.0, 300.0, 1000.0)]
        self.assertEqual(n, sorted(n))
        self.assertLess(n[1], 4.0)
        self.assertGreater(n[3], 4.0)


class TestFresnelzonMedRefraktion(unittest.TestCase):
    """FresnelZoneW och FresnelZoneWm med krökta strålar, ekv. (345)–(353), TC 2.13."""

    def test_utan_refraktion_anropas_rata_stralar(self) -> None:
        arg = (100.0, 0.5, 1.5, 10.0, 40.0, 0.25 * 3.4)
        self.assertEqual(fresnelzonsvikt_refraktion(*arg, 0.0, 340.0), fresnelzonsvikt(*arg))
        self.assertEqual(
            fresnelzonsvikt_modifierad_refraktion(*arg, 0.0, 340.0),
            fresnelzonsvikt_modifierad(*arg),
        )

    def test_svag_refraktion_gar_mot_rata_stralar(self) -> None:
        arg = (100.0, 0.5, 1.5, 10.0, 40.0, 0.25 * 3.4)
        for xi in (1.0e-8, -1.0e-8):
            self.assertAlmostEqual(
                fresnelzonsvikt_refraktion(*arg, xi, 340.0), fresnelzonsvikt(*arg), 5
            )
            self.assertAlmostEqual(
                fresnelzonsvikt_modifierad_refraktion(*arg, xi, 340.0),
                fresnelzonsvikt_modifierad(*arg),
                5,
            )

    def test_modifierade_storheter(self) -> None:
        """Ekv. (348): S′ och R′ ligger på tangenterna i reflexpunkten, på avstånden R_S och R_R."""
        g = reflexgeometri(100.0, 0.5, 1.5, 2.0e-3, 340.0)
        self.assertFalse(g.i_skuggzon)
        self.assertAlmostEqual(
            math.hypot(
                g.avstand_modifierat_m, g.kallhojd_modifierad_m + g.mottagarhojd_modifierad_m
            ),
            g.vag_kalla_m + g.vag_mottagare_m,
            9,
        )
        self.assertAlmostEqual(
            g.kallhojd_modifierad_m / g.mottagarhojd_modifierad_m,
            g.vag_kalla_m / g.vag_mottagare_m,
            12,
        )
        # Nedåtbrytning ger brantare reflexion än den räta strålen, uppåtbrytning flackare.
        rat = math.atan(2.0 / 100.0)
        self.assertGreater(g.strykvinkel_rad, rat)
        self.assertLess(reflexgeometri(100.0, 0.5, 1.5, -1.0e-4, 340.0).strykvinkel_rad, rat)

    def test_zonens_grans_uppfyller_ellipsvillkoret(self) -> None:
        """|S′P| + |PR′| = R_S + R_R + F·λ i zonens ändpunkter (ekv. 108), räknat direkt."""
        g = reflexgeometri(100.0, 0.5, 1.5, 2.0e-3, 340.0)
        f_lambda = 0.25 * 340.0 / 500.0
        zon = fresnelzon_langs_mark_refraktion(g, f_lambda)
        psi = g.strykvinkel_rad
        for x in (zon.d1_fz_m - zon.d_refl_m, zon.d2_fz_m - zon.d_refl_m):
            till_s = math.hypot(x + g.vag_kalla_m * math.cos(psi), g.vag_kalla_m * math.sin(psi))
            till_r = math.hypot(
                x - g.vag_mottagare_m * math.cos(psi), g.vag_mottagare_m * math.sin(psi)
            )
            self.assertAlmostEqual(till_s + till_r, g.vag_kalla_m + g.vag_mottagare_m + f_lambda, 9)

    def test_vikterna_summerar_till_ett_over_zonen(self) -> None:
        for xi in (2.0e-3, -1.0e-4, -2.0e-3):  # medvind, svag uppåtbrytning, skuggzon
            g = reflexgeometri(100.0, 0.5, 1.5, xi, 340.0)
            f_lambda = 0.25 * 340.0 / 250.0
            zon = fresnelzon_langs_mark_refraktion(g, f_lambda)
            granser = np.linspace(zon.d1_fz_m - 5.0, zon.d2_fz_m + 5.0, 8)
            arg = (100.0, 0.5, 1.5)
            w = sum(
                fresnelzonsvikt_refraktion(*arg, float(a), float(b), f_lambda, xi, 340.0)
                for a, b in zip(granser[:-1], granser[1:], strict=True)
            )
            r = sum(
                fresnelzonsvikt_modifierad_refraktion(*arg, float(a), float(b), f_lambda, xi, 340.0)
                for a, b in zip(granser[:-1], granser[1:], strict=True)
            )
            self.assertAlmostEqual(w, 1.0, 12)
            self.assertAlmostEqual(r, 1.0, 12)

    def test_skuggzon_ekv_347(self) -> None:
        d, h_s, h_r = 100.0, 0.3, 1.5
        g = reflexgeometri(d, h_s, h_r, -2.0e-3, 340.0)
        self.assertTrue(g.i_skuggzon)
        self.assertEqual(g.strykvinkel_rad, 0.0)
        self.assertAlmostEqual(
            g.d_refl_m, d * math.sqrt(h_s) / (math.sqrt(h_s) + math.sqrt(h_r)), 12
        )
        self.assertEqual(g.kallhojd_modifierad_m, 0.0)
        # Bågarna på den tangerande cirkeln är obetydligt längre än sina horisontella sträckor.
        self.assertAlmostEqual(g.vag_kalla_m / g.d_refl_m, 1.0, 3)
        self.assertAlmostEqual(g.vag_mottagare_m / (d - g.d_refl_m), 1.0, 3)
        self.assertGreater(g.vag_kalla_m + g.vag_mottagare_m, d)

    def test_tangerande_cirkel_i_ekv_347(self) -> None:
        """R₀ = d²/(2·(√h_S + √h_R)²): cirkeln når höjden h på avståndet √(2·R₀·h − h²)."""
        d, h_s, h_r = 100.0, 0.3, 1.5
        r0 = d**2 / (2.0 * (math.sqrt(h_s) + math.sqrt(h_r)) ** 2)
        g = reflexgeometri_skuggzon(d, h_s, h_r)
        for h, bage in ((h_s, g.vag_kalla_m), (h_r, g.vag_mottagare_m)):
            self.assertAlmostEqual(r0 * math.sin(bage / r0), math.sqrt(2.0 * r0 * h - h * h), 9)

    def test_tc_2_13_arccos_begransas(self) -> None:
        """Höga höjder i förhållande till d: argumentet under −1 ersätts med −1 (R = π·R₀)."""
        d, h = 4.0, 30.0
        g = reflexgeometri_skuggzon(d, h, h)
        r0 = d**2 / (2.0 * (2.0 * math.sqrt(h)) ** 2)
        self.assertLess(1.0 - h / r0, -1.0)
        self.assertAlmostEqual(g.vag_kalla_m, math.pi * r0, 12)

    def test_klippt_zon(self) -> None:
        """Avsnitt 5.8: med zonen begränsad till källa–mottagare summerar vikterna till 1."""
        d, h_s, h_r = 100.0, 0.01, 1.5
        f_lambda = 0.25 * 340.0 / 25.0
        zon = fresnelzon_langs_mark(d, h_s, h_r, f_lambda)
        self.assertLess(zon.d1_fz_m, 0.0)  # zonen sticker ut bakom källan vid 25 Hz
        self.assertLess(fresnelzonsvikt(d, h_s, h_r, 0.0, d, f_lambda), 1.0)
        klippt = klippt_fresnelzon(zon, d)
        self.assertEqual(klippt.d1_fz_m, 0.0)
        self.assertEqual(klippt.d2_fz_m, min(zon.d2_fz_m, d))
        self.assertEqual(klippt.d_refl_m, zon.d_refl_m)


class TestDelmodell1MedRefraktion(unittest.TestCase):
    """AV 1106/07 avsnitt 5.10 med krökta strålar och skuggzon."""

    GEOMETRIER = ((100.0, 0.3, 1.5), (50.0, 0.01, 4.0), (300.0, 0.75, 1.5))

    def test_utan_refraktion_exakt_som_tidigare(self) -> None:
        """`Atmosfar` med A = B = s_A = s_B = 0 ger bit för bit samma svar som `HomogenAtmosfar`."""
        storsta = 0.0
        for cv2, ct2 in ((0.0, 0.0), (0.12, 0.008)):
            homogen = HomogenAtmosfar(15.0, 70.0, 101.325, cv2, ct2)
            allman = atmosfar_ur_vader(Vader(cv2=cv2, ct2=ct2))
            for d, h_s, h_r in self.GEOMETRIER:
                for sigma in (12.5, 200.0, 20000.0):
                    a = markeffekt_plan_mark_db(F, d, h_s, h_r, sigma, 0.0, homogen)
                    b = markeffekt_plan_mark_db(F, d, h_s, h_r, sigma, 0.0, allman)
                    storsta = max(storsta, float(np.max(np.abs(a - b))))
        self.assertEqual(storsta, 0.0)

    def test_som_atmosfar(self) -> None:
        a = som_atmosfar(HomogenAtmosfar(10.0, 80.0, 100.0, 0.12, 0.008))
        self.assertFalse(a.har_refraktion or a.har_fluktuerande_refraktion)
        self.assertEqual((a.temperatur_mark_c, a.cv2, a.ct2), (10.0, 0.12, 0.008))
        self.assertIs(som_atmosfar(MEDVIND), MEDVIND)

    def test_forsumbar_refraktion_gar_mot_homogen(self) -> None:
        """A så liten att |ξ| < 10⁻⁶ (ξ sätts till 0): refraktionsvägen ger det homogena svaret.

        Prövar att de två räknevägarna (räta strålar och krökta strålar med ξ = 0) möts.
        """
        liten = atmosfar_ur_vader(Vader(vind_m_s=1.0e-4))
        self.assertTrue(liten.har_refraktion)
        for d, h_s, h_r in self.GEOMETRIER:
            for sigma in (200.0, 20000.0):
                a = markeffekt_plan_mark_db(F, d, h_s, h_r, sigma, 0.0, STILLA)
                b = markeffekt_plan_mark_db(F, d, h_s, h_r, sigma, 0.0, liten)
                self.assertLess(float(np.max(np.abs(a - b))), 1.0e-6)

    def test_refraktionen_ar_kontinuerlig_i_vindstyrkan(self) -> None:
        """Svag vind ändrar markeffekten lite; inget språng när ξ passerar gränsen 10⁻⁶."""
        d, h_s, h_r = 100.0, 0.3, 1.5
        a = markeffekt_plan_mark_db(
            F, d, h_s, h_r, 20000.0, 0.0, atmosfar_ur_vader(Vader(vind_m_s=0.001))
        )
        b = markeffekt_plan_mark_db(
            F, d, h_s, h_r, 20000.0, 0.0, atmosfar_ur_vader(Vader(vind_m_s=0.003))
        )
        self.assertLess(float(np.max(np.abs(a - b))), 0.05)

    def test_under_fl_marks_ingen_refraktion_pa_mjuk_mark(self) -> None:
        """Mjuk mark: banden under f_L räknas som i homogen atmosfär (avsnitt 5.5.3)."""
        d, h_s, h_r = 100.0, 0.3, 1.5
        profiler = calc_eq_ssp_mark(F, d, h_s, h_r, 200.0, impedanser(200.0), MEDVIND)
        assert profiler.f_l_hz is not None
        stilla = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, STILLA)
        per_band = Skuggzonslasning(avgorande="per_band")
        for atm in (MEDVIND, MOTVIND):
            vind = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, atm, skuggzon=per_band)
            under = profiler.f_l_hz >= F
            self.assertGreater(int(np.sum(under)), 5)
            self.assertLess(float(np.max(np.abs(vind[under] - stilla[under]))), 1.0e-6)
        # Förvalet (`avgorande="delat"`): markeffekten under f_L är densamma, men i motvind,
        # där den frekvensoberoende profilen ger skuggzon, läggs ΔL_SZ(f) till i alla band.
        medvind = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, MEDVIND)
        self.assertLess(float(np.max(np.abs(medvind[under] - stilla[under]))), 1.0e-6)
        motvind = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, MOTVIND)
        oberoende = calc_eq_ssp(h_s, h_r, MOTVIND)
        kil = kilavstand_skuggzon_m(d, h_s, h_r)
        avskarmning = np.array(
            [
                skuggzonsavskarmning_db(
                    float(f), d, h_s, h_r, oberoende.xi_per_m, oberoende.c0_m_s, kil
                )
                for f in F
            ]
        )
        np.testing.assert_allclose(
            motvind[under], stilla[under] + avskarmning[under], rtol=0.0, atol=1.0e-6
        )
        self.assertEqual(float(avskarmning[0]), 0.0)  # 25 Hz: strålen når inte marken
        self.assertLess(float(avskarmning[under][-1]), -1.0)
        self.assertTrue(bool(np.all(np.diff(avskarmning) <= 1.0e-9)))  # växer jämnt med f

    def test_medvind_och_motvind_skiljer_at_ratt_hall(self) -> None:
        """Hård mark (klass G, 20 000 kNs/m⁴) och låg källa: ingen interferensdipp under 10 kHz.

        Motvind lägger mottagaren i skuggzon (d_SZ ≈ 21 m för 0,01 och 1,5 m): från 50 Hz ska
        nivån ligga under både den homogena och medvindens, och allt mer med frekvensen. (I de
        tre lägsta banden går strålen i ekv. 386 inte ned till marken och ΔL_SZ = 0.)
        """
        d, h_s, h_r = 100.0, 0.01, 1.5
        stilla = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, STILLA)
        med = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, MEDVIND)
        mot = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, MOTVIND)
        self.assertTrue(bool(np.all(mot[3:] < stilla[3:])))
        self.assertTrue(bool(np.all(mot[3:] < med[3:])))
        self.assertLess(float(np.max(np.abs(mot[:3] - stilla[:3]))), 0.02)
        skillnad = mot - stilla
        self.assertTrue(bool(np.all(np.diff(skillnad[3:]) < 0.0)))
        self.assertLess(float(skillnad[26]), -20.0)
        # Medvind ger ingen skuggzon: nära +6 dB i basen.
        self.assertTrue(bool(np.all(med[:10] > 5.9)))

    def test_riktning_med_testfallens_vader(self) -> None:
        """Mjuk mark, 0,3 och 1,5 m, turbulens och fluktuation som i RL 18/18 grupp 2–12.

        Över 1 kHz ska motvind ge lägre och medvind högre nivå än vindstilla (energimedel).
        """
        d, h_s, h_r = 100.0, 0.3, 1.5

        def medel(vind: float) -> float:
            atm = atmosfar_ur_vader(Vader(vind_m_s=vind, std_vind_m_s=0.5, cv2=0.12, ct2=0.008))
            dl = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, atm)
            return float(10.0 * np.log10(np.mean(10.0 ** (dl[16:] / 10.0))))

        self.assertLess(medel(-3.0), medel(0.0) - 3.0)
        self.assertGreater(medel(3.0), medel(0.0))

    def test_uppatbrytning_utan_skuggzon(self) -> None:
        """dt/dz = −0,1 K/m på 100 m: ingen skuggzon (d_SZ ≈ 190 m), Δτ minskar, dippen flyttas upp.

        De två läsningarna av uppåtbrytningen ger olika Δτ men samma riktning.
        """
        kall = atmosfar_ur_vader(Vader(dt_dz_k_m=-0.1))
        d, h_s, h_r = 100.0, 0.3, 1.5
        stilla = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, STILLA)
        dipp_stilla = float(F[int(np.argmin(stilla))])
        for uppat in UPPAT:
            dl = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, kall, uppat=uppat)
            self.assertTrue(bool(np.all(np.isfinite(dl))))
            self.assertGreaterEqual(float(F[int(np.argmin(dl))]), dipp_stilla)

    def test_skuggzonsgrenen_ar_ekv_121(self) -> None:
        """Hård mark i skuggzon utan turbulens: ΔL = 20·lg|1 + Q| + ΔL_SZ med Q ≈ 1, alltså
        6,02 dB plus skuggzonsavskärmningen (räknad för sig med den frekvensoberoende profilen)."""
        d, h_s, h_r = 100.0, 0.3, 1.5
        hard = 1.0e9
        dl = markeffekt_plan_mark_db(F, d, h_s, h_r, hard, 0.0, MOTVIND)
        profil = calc_eq_ssp(h_s, h_r, MOTVIND)
        self.assertGreater(d, 0.95 * skuggzonsavstand_m(h_s, h_r, profil.xi_per_m))
        kil = kilavstand_skuggzon_m(d, h_s, h_r)
        for i, f in enumerate(F):
            sz = skuggzonsavskarmning_db(float(f), d, h_s, h_r, profil.xi_per_m, profil.c0_m_s, kil)
            self.assertAlmostEqual(float(dl[i]), 20.0 * math.log10(2.0) + sz, 2)

    def test_sprang_vid_skuggzonens_grans_med_testfallens_vader(self) -> None:
        """Övergången sker vid d = 0,95·d_SZ (avsnitt 5.5.4), inte där strålen tangerar marken,
        så metoden har ett språng där. Med turbulens och fluktuation som i testfallen är det
        litet; uppmätt 0,78 dB (mjuk mark, läget `bokstavlig`) och 0,19 dB (`exakt`). Utan
        turbulens är språnget stort vid höga frekvenser (se loggen); det prövas inte här.
        """
        h_s, h_r = 0.3, 1.5
        profil = calc_eq_ssp(h_s, h_r, MOTVIND_TURBULENT)
        for uppat, grans in zip(UPPAT, (1.0, 0.3), strict=True):
            d_grans = 0.95 * skuggzonsavstand_m(h_s, h_r, profil.xi_per_m, uppat)
            fore = markeffekt_plan_mark_db(
                F,
                d_grans * (1.0 - 1e-9),
                h_s,
                h_r,
                200.0,
                0.0,
                MOTVIND_TURBULENT,
                uppat=uppat,
            )
            efter = markeffekt_plan_mark_db(
                F,
                d_grans * (1.0 + 1e-9),
                h_s,
                h_r,
                200.0,
                0.0,
                MOTVIND_TURBULENT,
                uppat=uppat,
            )
            self.assertLess(float(np.max(np.abs(fore - efter))), grans)

    def test_kontinuerlig_inne_i_skuggzonen(self) -> None:
        """Band över f_H (frekvensoberoende profil): nivån ändras jämnt med avståndet i skuggzonen.

        Banden mellan f_L och f_H går in i skuggzonen på var sitt avstånd (d_SZ beror av ξ(f))
        och har där språng som är metodens egna; de redovisas i loggen och prövas inte här.
        """
        h_s, h_r = 0.3, 1.5
        profiler = calc_eq_ssp_mark(F, 100.0, h_s, h_r, 200.0, impedanser(200.0), MOTVIND_TURBULENT)
        assert profiler.f_h_hz is not None
        over = profiler.f_h_hz <= F
        self.assertGreater(int(np.sum(over)), 3)
        foregaende = markeffekt_plan_mark_db(F, 70.0, h_s, h_r, 200.0, 0.0, MOTVIND_TURBULENT)
        for d in np.arange(70.5, 200.0, 0.5):
            nu = markeffekt_plan_mark_db(F, float(d), h_s, h_r, 200.0, 0.0, MOTVIND_TURBULENT)
            self.assertLess(float(np.max(np.abs(nu[over] - foregaende[over]))), 0.3)
            foregaende = nu

    def test_fluktuation_minskar_koherensen(self) -> None:
        """s_u > 0 utan medelvind: Δτ₊ ≠ Δτ ger F_Δτ < 1, så interferensdippen fylls igen."""
        d, h_s, h_r = 100.0, 0.3, 1.5
        hard = 1.0e9
        fluktuerande = atmosfar_ur_vader(Vader(std_vind_m_s=1.0))
        self.assertGreater(ovre_atmosfar(fluktuerande).a_m_s, 0.0)
        stilla = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, STILLA)
        dl = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, fluktuerande)
        dipp = int(np.argmin(stilla))
        self.assertGreater(float(dl[dipp]), float(stilla[dipp]) + 3.0)
        # Vid låga frekvenser är F_Δτ ≈ 1 och inget ändras.
        self.assertLess(float(np.max(np.abs(dl[:8] - stilla[:8]))), 0.01)
        dl = markeffekt_plan_mark_db(F, d, h_s, h_r, hard, 0.0, fluktuerande)
        # Helt inkoherent gräns över hård mark (ℜ_i = 1): 10·lg(1 + (R₁/R₂)²) (ekv. 120).
        kvot = math.hypot(d, h_s - h_r) / math.hypot(d, h_s + h_r)
        self.assertAlmostEqual(float(dl[26]), 10.0 * math.log10(1.0 + kvot**2), 2)

    def test_ct2_form(self) -> None:
        """Formen "tryckt" (C_T²/T) ger mindre koherens än "kvadrat" (C_T²/T²)."""
        atm = HomogenAtmosfar(15.0, 70.0, 101.325, 0.12, 0.008)
        f = 2000.0
        kvadrat = koherens_plan_mark(f, 100.0, 0.5, 1.5, 0.0, atm)
        tryckt = koherens_plan_mark(f, 100.0, 0.5, 1.5, 0.0, atm, ct2_form="tryckt")
        self.assertLess(tryckt, kvadrat)
        with self.assertRaises(BullerFel):
            koherens_plan_mark(f, 100.0, 0.5, 1.5, 0.0, atm, ct2_form="annat")  # type: ignore[arg-type]
        with self.assertRaises(BullerFel):
            koherens_plan_mark(f, 100.0, 0.5, 1.5, 0.0, MEDVIND)


class TestDelmodell2MedRefraktion(unittest.TestCase):
    """AV 1106/07 avsnitt 5.11 med Fresnelzoner för krökta strålar."""

    VAG_OCH_GRAS = [Marksegment(0.0, 1.75, 20000.0, 0.0), Marksegment(1.75, 100.0, 200.0, 0.0)]

    def test_utan_refraktion_exakt_som_tidigare(self) -> None:
        homogen = HomogenAtmosfar(15.0, 70.0, 101.325, 0.12, 0.008)
        allman = atmosfar_ur_vader(Vader(cv2=0.12, ct2=0.008))
        for h_s in (0.01, 0.3):
            a = markeffekt_blandad_mark_db(F, 100.0, h_s, 1.5, self.VAG_OCH_GRAS, homogen)
            b = markeffekt_blandad_mark_db(F, 100.0, h_s, 1.5, self.VAG_OCH_GRAS, allman)
            self.assertTrue(bool(np.array_equal(a, b)))

    def test_oberoende_av_uppdelning_i_segment(self) -> None:
        delat = [
            Marksegment(0.0, 1.0, 20000.0, 0.0),
            Marksegment(1.0, 1.75, 20000.0, 0.0),
            Marksegment(1.75, 30.0, 200.0, 0.0),
            Marksegment(30.0, 100.0, 200.0, 0.0),
        ]
        for atm in (MEDVIND, MOTVIND, MOTVIND_TURBULENT):
            for vikter in VIKTER:
                a = markeffekt_blandad_mark_db(
                    F,
                    100.0,
                    0.3,
                    1.5,
                    self.VAG_OCH_GRAS,
                    atm,
                    skuggzonsvikter=vikter,
                )
                b = markeffekt_blandad_mark_db(
                    F,
                    100.0,
                    0.3,
                    1.5,
                    delat,
                    atm,
                    skuggzonsvikter=vikter,
                )
                self.assertLess(float(np.max(np.abs(a - b))), 1.0e-9)

    def test_mellan_de_rena_marktyperna_i_skuggzon(self) -> None:
        """I skuggzonen är vikterna andelar som summerar till högst 1 (Σ = 1 för `klippt`)."""
        d, h_s, h_r = 100.0, 0.3, 1.5
        hard = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, MOTVIND_TURBULENT)
        mjuk = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, MOTVIND_TURBULENT)
        for vikter in VIKTER:
            blandad = markeffekt_blandad_mark_db(
                F,
                d,
                h_s,
                h_r,
                self.VAG_OCH_GRAS,
                MOTVIND_TURBULENT,
                fresnelzon="klippt",
                skuggzonsvikter=vikter,
            )
            self.assertTrue(bool(np.all(blandad <= np.maximum(hard, mjuk) + 1e-9)))
            self.assertTrue(bool(np.all(blandad >= np.minimum(hard, mjuk) - 1e-9)))

    def test_en_marktyp_i_tva_segment_ger_delmodell_1_med_klippt_zon(self) -> None:
        """Samma marktyp överallt: med zonen klippt till källa–mottagare är Σw = 1 och ΔL₂ = ΔL₁.

        Gäller också med refraktion och i skuggzon. En marktyp ger ett enda ΔL, så viktsumman
        syns som kvoten ΔL₂/ΔL₁.
        """
        d, h_s, h_r = 100.0, 0.3, 1.5
        segment = [Marksegment(0.0, 30.0, 200.0, 0.0), Marksegment(30.0, d, 200.0, 0.0)]
        for atm in (STILLA, MEDVIND, MOTVIND_TURBULENT):
            for vikter in VIKTER:
                ett = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, atm)
                klippt = markeffekt_blandad_mark_db(
                    F,
                    d,
                    h_s,
                    h_r,
                    segment,
                    atm,
                    fresnelzon="klippt",
                    skuggzonsvikter=vikter,
                )
                self.assertLess(float(np.max(np.abs(klippt - ett))), 1.0e-9)

    def test_oklippt_zon_ger_viktsumma_under_ett(self) -> None:
        """Ekv. (349)–(351) som tryckta: zonen sticker ut bakom källan vid låga frekvenser.

        På 10 m med höjderna 0,01 och 1,5 m och F_λ = 1/4 är viktsumman vid 25 Hz omkring 0,76.
        Förvalet sedan P-S1 är den klippta zonen (avsnitt 5.8), där en marktyp delad i två
        segment ger samma svar som delmodell 1.
        """
        d, h_s, h_r = 10.0, 0.01, 1.5
        segment = [Marksegment(0.0, 3.0, 200.0, 0.0), Marksegment(3.0, d, 200.0, 0.0)]
        ett = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, STILLA)
        oklippt = markeffekt_blandad_mark_db(F, d, h_s, h_r, segment, STILLA, fresnelzon="oklippt")
        kvot = float(oklippt[0] / ett[0])
        self.assertLess(kvot, 0.9)
        self.assertGreater(kvot, 0.6)
        forval = markeffekt_blandad_mark_db(F, d, h_s, h_r, segment, STILLA)
        self.assertLess(float(np.max(np.abs(forval - ett))), 1.0e-9)

    def test_okanda_val(self) -> None:
        with self.assertRaises(BullerFel):
            markeffekt_blandad_mark_db(
                F,
                100.0,
                0.3,
                1.5,
                self.VAG_OCH_GRAS,
                STILLA,
                fresnelzon="annat",  # type: ignore[arg-type]
            )
        with self.assertRaises(BullerFel):
            markeffekt_blandad_mark_db(
                F,
                100.0,
                0.3,
                1.5,
                self.VAG_OCH_GRAS,
                STILLA,
                skuggzonsvikter="annat",  # type: ignore[arg-type]
            )


class TestKarnaMedRefraktion(unittest.TestCase):
    PROFIL = plan_profil(100.0, [20000.0, 200.0], [0.0, 1.75, 96.75], 0.3, 1.5)

    def test_homogen_atmosfar_oforandrad(self) -> None:
        """Kärnans ΔL_t utan refraktion är delmodell 2 med `HomogenAtmosfar`.

        (Kärnan räknar d_e och höjderna ur profilen, därav toleransen; att delmodellerna själva
        är bit för bit oförändrade prövas i klasserna ovan.)
        """
        resultat = Nord2000Karna().berakna(self.PROFIL, Vader(cv2=0.12, ct2=0.008))
        segment = [Marksegment(0.0, 1.75, 20000.0, 0.0), Marksegment(1.75, 96.75, 200.0, 0.0)]
        direkt = markeffekt_blandad_mark_db(
            F, 96.75, 0.3, 1.5, segment, HomogenAtmosfar(15.0, 70.0, 101.325, 0.12, 0.008)
        )
        np.testing.assert_allclose(resultat.terrang_db, direkt, rtol=0.0, atol=1.0e-9)

    def test_refraktion_raknas(self) -> None:
        karna = Nord2000Karna()
        stilla = karna.berakna(self.PROFIL, Vader(cv2=0.12, ct2=0.008))
        for vader in (
            Vader(vind_m_s=-3.0, std_vind_m_s=0.5, cv2=0.12, ct2=0.008),
            Vader(dt_dz_k_m=-0.1),
            Vader(std_vind_m_s=0.5, cv2=0.12, ct2=0.008),
        ):
            r = karna.berakna(self.PROFIL, vader)
            self.assertEqual(r.delmodell, "2")
            self.assertTrue(bool(np.all(np.isfinite(r.overforing_db))))
            # ΔL_d och ΔL_a räknas med R_SR och ändras inte av refraktionen.
            self.assertEqual(r.spridning_db, stilla.spridning_db)
            self.assertTrue(bool(np.array_equal(r.luftabsorption_db, stilla.luftabsorption_db)))
        motvind = karna.berakna(
            self.PROFIL, Vader(vind_m_s=-3.0, std_vind_m_s=0.5, cv2=0.12, ct2=0.008)
        )
        self.assertLess(float(np.mean(motvind.terrang_db[16:] - stilla.terrang_db[16:])), -3.0)

    def test_flera_markreflexer_laggs_till_med_energisumma(self) -> None:
        """Medvind 3 m/s på 100 m ger N ≈ 4,2 > 4: ΔL_t = ΔL_flat ⊕ ΔL₈ (ekv. 332)."""
        medvind = Vader(vind_m_s=3.0)
        med = Nord2000Karna().berakna(self.PROFIL, medvind)
        utan = Nord2000Karna(delmodell8="utelamna").berakna(self.PROFIL, medvind)
        d8 = delmodell8.flera_markreflexer(F, self.PROFIL, atmosfar_ur_vader(medvind))
        self.assertGreater(med.antal_stralar, 4.0)
        self.assertEqual(med.antal_stralar, d8.antal_stralar)
        assert med.flera_markreflexer_db is not None
        self.assertTrue(bool(np.array_equal(med.flera_markreflexer_db, d8.delta_l_db)))
        np.testing.assert_allclose(
            med.terrang_db,
            10.0 * np.log10(10.0 ** (utan.terrang_db / 10.0) + 10.0 ** (d8.delta_l_db / 10.0)),
            rtol=0.0,
            atol=1.0e-12,
        )
        self.assertTrue(bool(np.all(med.terrang_db > utan.terrang_db)))
        self.assertIsNone(utan.flera_markreflexer_db)
        # ΔL_d och ΔL_a berörs inte.
        self.assertEqual(med.spridning_db, utan.spridning_db)
        self.assertTrue(bool(np.array_equal(med.luftabsorption_db, utan.luftabsorption_db)))
        # Lågfrekvenslösningen (koherent summa) ger minst lika mycket.
        lag = Nord2000Karna(delmodell8="lag").berakna(self.PROFIL, medvind)
        self.assertTrue(bool(np.all(lag.terrang_db >= med.terrang_db)))

    def test_flera_markreflexer_bidrar_inte_under_troskeln(self) -> None:
        """N ≤ 4 (kort avstånd), motvind och homogen atmosfär: bit för bit som utan ΔL₈."""
        nara = plan_profil(30.0, [200.0], [0.0, 30.0], 0.3, 1.5)
        for profil, vader in (
            (nara, Vader(vind_m_s=3.0)),
            (self.PROFIL, Vader(vind_m_s=-3.0)),
            (self.PROFIL, Vader(cv2=0.12, ct2=0.008)),
            (self.PROFIL, Vader(std_vind_m_s=0.5)),
        ):
            med = Nord2000Karna().berakna(profil, vader)
            utan = Nord2000Karna(delmodell8="utelamna").berakna(profil, vader)
            self.assertLessEqual(med.antal_stralar, 4.0)
            self.assertIsNone(med.flera_markreflexer_db)
            self.assertTrue(bool(np.array_equal(med.terrang_db, utan.terrang_db)))

    def test_valen_paverkar_bara_det_de_ska(self) -> None:
        homogen = Vader(cv2=0.12, ct2=0.008)
        bas = Nord2000Karna().overforing_db(self.PROFIL, homogen)
        for karna in (
            Nord2000Karna(uppat="bokstavlig"),
            Nord2000Karna(skuggzonsvikter="hog"),
            Nord2000Karna(skuggzon=Skuggzonslasning(avgorande="per_band", kiltopp="ekv43")),
            Nord2000Karna(delmodell8="utelamna"),
        ):
            self.assertTrue(bool(np.array_equal(karna.overforing_db(self.PROFIL, homogen), bas)))
        motvind = Vader(vind_m_s=-3.0, std_vind_m_s=0.5, cv2=0.12, ct2=0.008)
        a = Nord2000Karna().overforing_db(self.PROFIL, motvind)
        for karna in (
            Nord2000Karna(skuggzonsvikter="hog"),
            Nord2000Karna(skuggzon=Skuggzonslasning(avgorande="per_band")),
            Nord2000Karna(skuggzon=Skuggzonslasning(avgorande="oberoende")),
            Nord2000Karna(skuggzon=Skuggzonslasning(q_gangtid="noll")),
            Nord2000Karna(skuggzon=Skuggzonslasning(niva="10lg")),
            Nord2000Karna(skuggzon=Skuggzonslasning(kiltopp="ekv43")),
            Nord2000Karna(skuggzon=Skuggzonslasning(radie="tryckt_kvadrat")),
            Nord2000Karna(skuggzon=Skuggzonslasning(radie="tryckt")),
        ):
            b = karna.overforing_db(self.PROFIL, motvind)
            self.assertTrue(bool(np.all(np.isfinite(b))))
            self.assertFalse(bool(np.array_equal(a, b)))
        # Fresnelzonens klippning verkar också i homogen atmosfär (vikterna vid låga frekvenser).
        oklippt = Nord2000Karna(fresnelzon="oklippt").overforing_db(self.PROFIL, homogen)
        self.assertFalse(bool(np.array_equal(oklippt, bas)))
        self.assertLess(float(np.max(np.abs(oklippt - bas))), 0.25)

    def test_okanda_val(self) -> None:
        for kw in (
            {"uppat": "annat"},
            {"ct2_form": "annat"},
            {"delmodell8": "annat"},
            {"delmodell8": "vagra"},
            {"luftabsorption": "annat"},
            {"fresnelzon": "annat"},
            {"skuggzonsvikter": "annat"},
        ):
            with self.assertRaises(BullerFel):
                Nord2000Karna(**kw)  # type: ignore[arg-type]

    def test_inte_konform(self) -> None:
        self.assertFalse(Nord2000Karna().nord2000_konform)
        self.assertIsInstance(STILLA, Atmosfar)


class TestVindkomponent(unittest.TestCase):
    """Vindriktningen φ i bladen för rak väg (`buller.testfall.korare.vindkomponent_m_s`)."""

    def test_medvind_motvind_och_sidvind(self) -> None:
        self.assertAlmostEqual(vindkomponent_m_s(3.0, 0.0, 0.0), 3.0, 12)
        self.assertAlmostEqual(vindkomponent_m_s(3.0, 180.0, 0.0), -3.0, 12)
        self.assertAlmostEqual(vindkomponent_m_s(3.0, 90.0, 0.0), 0.0, 12)
        self.assertAlmostEqual(
            vindkomponent_m_s(3.0, 90.0, math.radians(60.0)), 3.0 * math.cos(math.radians(30.0)), 12
        )
        self.assertAlmostEqual(
            vindkomponent_m_s(3.0, 90.0, math.radians(-60.0)),
            -3.0 * math.cos(math.radians(30.0)),
            12,
        )

    def test_symmetri_over_kallpunkterna(self) -> None:
        """Mängden vindkomponenter över symmetriska källpunkter beror inte på tecknet på φ."""
        vinklar = np.radians(np.arange(-89.0, 90.0, 1.0))
        plus = sorted(vindkomponent_m_s(3.0, 45.0, float(v)) for v in vinklar)
        minus = sorted(vindkomponent_m_s(3.0, -45.0, float(v)) for v in vinklar)
        np.testing.assert_allclose(plus, minus, atol=1e-12)
        # φ = 180: motvind från alla källpunkter; φ = 0: medvind från alla.
        self.assertTrue(all(vindkomponent_m_s(3.0, 180.0, float(v)) < 0.0 for v in vinklar))
        self.assertTrue(all(vindkomponent_m_s(3.0, 0.0, float(v)) > 0.0 for v in vinklar))


if __name__ == "__main__":
    unittest.main()


class TestEnKallaMedDelmodell8(unittest.TestCase):
    """`delmodell8.py` är enda källan för strålens höjd, R_A,B och N (P-S1).

    Innan dubbletterna i `refraktion_mark.py` togs bort jämfördes de med `delmodell8.py` på
    slumpade fall (siffror i `docs/buller/logg/P-S1.md`). De borttagna funktionerna följde
    ekvationerna rakt av och finns kvar här som oberoende räkneväg.
    """

    def test_delmodell8_mot_ekvationerna_rakt_av(self) -> None:
        import random

        slump = random.Random(20261121)
        for _ in range(3000):
            vader = Vader(
                vind_m_s=slump.uniform(-8.0, 8.0),
                dt_dz_k_m=slump.choice([0.0, slump.uniform(-0.3, 0.3)]),
                z0_m=10.0 ** slump.uniform(-3.5, 0.0),
            )
            atm = atmosfar_ur_vader(vader)
            d = 10.0 ** slump.uniform(0.5, 3.8)
            h = 10.0 ** slump.uniform(-2.0, 1.5)
            n1 = antal_stralar_bokstavlig(d, h, atm)
            n2 = antal_stralar_flera_markreflexer(d, h, atm)
            self.assertAlmostEqual(n1, n2, delta=1e-12 * max(n1, 1.0))
            h1 = 10.0 ** slump.uniform(-2.0, 1.3)
            h2 = 10.0 ** slump.uniform(-2.0, 1.3)
            xi = slump.choice([-1.0, 1.0]) * 10.0 ** slump.uniform(-7.0, -1.5)
            d0 = slump.uniform(0.0, d)
            a = hojd_cirkelstrale_bokstavlig(d, h1, h2, xi, d0)
            b = hojd_cirkelstrale(d, h1, h2, xi, d0)
            for u, v in zip(a, b, strict=True):
                self.assertAlmostEqual(u, v, delta=1e-6)

    def test_skuggzonshojden_anvander_delmodell8(self) -> None:
        d, h_s, h_r, xi = 100.0, 0.3, 1.5, -2.0e-3
        kil = kilavstand_skuggzon_m(d, h_s, h_r)
        for f in (100.0, 1000.0, 4000.0):
            vantad = -delmodell8.hojd_cirkelstrale(
                d, h_s, h_r, gradient_skuggzon_per_m(f, xi), kil
            ).hojd_m
            self.assertEqual(skuggzonshojd_m(f, d, h_s, h_r, xi, kil), vantad)


class TestSkuggzonslasningar(unittest.TestCase):
    """Läsningarna av skuggzonsavsnitten (P-S1): egenskaper som följer av texten själv."""

    def test_forval_och_okanda_val(self) -> None:
        forval = Skuggzonslasning()
        self.assertEqual(
            (forval.avgorande, forval.q_gangtid, forval.niva, forval.kiltopp, forval.radie),
            ("delat", "direkt", "20lg", "av1851", "geometrisk"),
        )
        for kw in (
            {"avgorande": "annat"},
            {"q_gangtid": "annat"},
            {"niva": "annat"},
            {"kiltopp": "annat"},
            {"radie": "annat"},
        ):
            with self.assertRaises(BullerFel):
                Skuggzonslasning(**kw)  # type: ignore[arg-type]
        with self.assertRaises(BullerFel):
            skuggzonsavskarmning_db(1000.0, 100.0, 0.3, 1.5, -2e-3, 340.0, 30.0, "annat")  # type: ignore[arg-type]
        with self.assertRaises(BullerFel):
            reflexgeometri_skuggzon(100.0, 0.3, 1.5, "annat")  # type: ignore[arg-type]

    def test_inget_sprang_in_i_skuggzonen_med_delat_avgorande(self) -> None:
        """AV 1851/00 avsnitt 6.1: skuggzonsmodellen ska inte ge ett språng vid skuggzonens kant.

        Mjuk mark, motvind 3 m/s, testfallens turbulens: med `avgorande="per_band"` går varje
        band mellan f_L och f_H in i skuggzonen på sitt eget avstånd och får då hela ΔL_SZ på
        en gång (över 10 dB vid 800 Hz på 183,5 m). Med `"delat"` finns ΔL_SZ redan där, och
        största ändring över 0,5 m är under 1 dB i alla band.
        """
        avstand = np.arange(30.0, 300.01, 0.5)

        def storsta_sprang(avgorande: str) -> float:
            nivaer = np.array(
                [
                    markeffekt_plan_mark_db(
                        F,
                        float(d),
                        0.3,
                        1.5,
                        200.0,
                        0.0,
                        MOTVIND_TURBULENT,
                        skuggzon=Skuggzonslasning(avgorande=avgorande),  # type: ignore[arg-type]
                    )
                    for d in avstand
                ]
            )
            return float(np.max(np.abs(np.diff(nivaer, axis=0))))

        self.assertLess(storsta_sprang("delat"), 1.0)
        self.assertGreater(storsta_sprang("per_band"), 10.0)

    def test_avgorandet_spelar_ingen_roll_utan_skuggzon_eller_pa_hard_mark(self) -> None:
        """Medvind, och hård mark (frekvensoberoende profil): de tre läsningarna är lika."""
        for sigma, atm, d in (
            (200.0, MEDVIND, 100.0),
            (200.0, MOTVIND, 30.0),  # utanför skuggzon (d_SZ ≈ 55 m)
            (20000.0, MOTVIND_TURBULENT, 100.0),
        ):
            svar = [
                markeffekt_plan_mark_db(
                    F, d, 0.3, 1.5, sigma, 0.0, atm, skuggzon=Skuggzonslasning(avgorande=a)
                )
                for a in ("delat", "per_band", "oberoende")
            ]
            self.assertTrue(bool(np.array_equal(svar[0], svar[1])))
            np.testing.assert_allclose(svar[0], svar[2], rtol=0.0, atol=1.0e-9)

    def test_tryckta_lasningar_av_ekv_121_och_387(self) -> None:
        """Hård mark utan turbulens i skuggzon: Q(f, 0, 0, Z) = 1, och 10·lg halverar ΔL_SZ."""
        d, h_s, h_r = 100.0, 0.3, 1.5
        profil = calc_eq_ssp(h_s, h_r, MOTVIND)
        kil = kilavstand_skuggzon_m(d, h_s, h_r)
        tjugo = np.array(
            [
                skuggzonsavskarmning_db(float(f), d, h_s, h_r, profil.xi_per_m, profil.c0_m_s, kil)
                for f in F
            ]
        )
        tio = markeffekt_plan_mark_db(
            F, d, h_s, h_r, 200.0, 0.0, MOTVIND, skuggzon=Skuggzonslasning(niva="10lg")
        )
        tjugo_tot = markeffekt_plan_mark_db(F, d, h_s, h_r, 200.0, 0.0, MOTVIND)
        np.testing.assert_allclose(tjugo_tot - tio, 0.5 * tjugo, rtol=0.0, atol=1.0e-9)
        # Gångtid 0 i Q ger Q = 1 för all mark: i ett band som självt ligger i skuggzon är
        # markeffekten då 20·lg 2 + ΔL_SZ, oberoende av marken.
        noll = markeffekt_plan_mark_db(
            F, d, h_s, h_r, 200.0, 0.0, MOTVIND, skuggzon=Skuggzonslasning(q_gangtid="noll")
        )
        self.assertAlmostEqual(float(noll[-1]), 20.0 * math.log10(2.0) + float(tjugo[-1]), 9)

    def test_radien_i_ekv_347(self) -> None:
        """De tre läsningarna av R₀: bara den geometriska ger R_S + R_R ≈ d."""
        d, h_s, h_r = 100.0, 0.3, 1.5
        geo = reflexgeometri_skuggzon(d, h_s, h_r)
        kvadrat = reflexgeometri_skuggzon(d, h_s, h_r, "tryckt_kvadrat")
        tryckt = reflexgeometri_skuggzon(d, h_s, h_r, "tryckt")
        self.assertAlmostEqual((geo.vag_kalla_m + geo.vag_mottagare_m) / d, 1.0, 3)
        self.assertAlmostEqual(
            (kvadrat.vag_kalla_m + kvadrat.vag_mottagare_m) / d, 1.0 / math.sqrt(2.0), 3
        )
        self.assertLess(tryckt.vag_kalla_m + tryckt.vag_mottagare_m, 0.2 * d)
        for g in (geo, kvadrat, tryckt):
            self.assertEqual(g.d_refl_m, geo.d_refl_m)
            self.assertEqual(g.strykvinkel_rad, 0.0)

    def test_kiltopp_enligt_ekv_43_ger_noll_fram_till_d_sz(self) -> None:
        """Med kilens topp på d_SZ (ekv. 43) finns ingen kil förrän d > d_SZ."""
        h_s, h_r = 0.3, 1.5
        profil = calc_eq_ssp(h_s, h_r, MOTVIND)
        d_sz = skuggzonsavstand_m(h_s, h_r, profil.xi_per_m, "exakt")
        d = 0.98 * d_sz  # i skuggzon (d > 0,95·d_SZ) men före d_SZ
        ekv43 = markeffekt_plan_mark_db(
            F, d, h_s, h_r, 20000.0, 0.0, MOTVIND, skuggzon=Skuggzonslasning(kiltopp="ekv43")
        )
        av1851 = markeffekt_plan_mark_db(F, d, h_s, h_r, 20000.0, 0.0, MOTVIND)
        kil = kilavstand_skuggzon_m(d, h_s, h_r)
        avskarmning = np.array(
            [
                skuggzonsavskarmning_db(float(f), d, h_s, h_r, profil.xi_per_m, profil.c0_m_s, kil)
                for f in F
            ]
        )
        np.testing.assert_allclose(ekv43, av1851 - avskarmning, rtol=0.0, atol=1.0e-9)


class TestSkuggzonsvikter(unittest.TestCase):
    """De tre läsningarna av vikterna i delmodell 2 när mottagaren ligger i skuggzon."""

    def test_ovre_band_ar_lag_till_6k3_och_hog_darover(self) -> None:
        d, h_s, h_r = 96.75, 0.3, 1.5
        segment = [Marksegment(0.0, 1.75, 20000.0, 0.0), Marksegment(1.75, d, 200.0, 0.0)]
        svar = {
            vikter: markeffekt_blandad_mark_db(
                F, d, h_s, h_r, segment, MOTVIND_TURBULENT, skuggzonsvikter=vikter
            )
            for vikter in VIKTER
        }
        self.assertTrue(bool(np.array_equal(svar["ovre_band"][:25], svar["lag"][:25])))
        self.assertTrue(bool(np.array_equal(svar["ovre_band"][25:], svar["hog"][25:])))
        self.assertFalse(bool(np.array_equal(svar["lag"][25:], svar["hog"][25:])))
        forval = markeffekt_blandad_mark_db(F, d, h_s, h_r, segment, MOTVIND_TURBULENT)
        self.assertTrue(bool(np.array_equal(forval, svar["ovre_band"])))

    def test_utanfor_skuggzon_spelar_valet_ingen_roll(self) -> None:
        d, h_s, h_r = 96.75, 0.3, 1.5
        segment = [Marksegment(0.0, 1.75, 20000.0, 0.0), Marksegment(1.75, d, 200.0, 0.0)]
        for atm in (STILLA, MEDVIND):
            svar = [
                markeffekt_blandad_mark_db(F, d, h_s, h_r, segment, atm, skuggzonsvikter=v)
                for v in VIKTER
            ]
            self.assertTrue(bool(np.array_equal(svar[0], svar[1])))
            self.assertTrue(bool(np.array_equal(svar[0], svar[2])))


class TestLuftabsorptionMonoton(unittest.TestCase):
    """`karna.luftabsorption_monoton_db`: ekv. (287) hållen på sitt största värde."""

    def test_lika_med_ekv_287_under_maximum(self) -> None:
        for avstand in (10.0, 100.0, 1000.0):
            self.assertTrue(
                bool(
                    np.array_equal(
                        luftabsorption_monoton_db(F, avstand, 15.0, 70.0, 101.325),
                        luftabsorption_db(F, avstand, 15.0, 70.0, 101.325),
                    )
                )
            )

    def test_maximum_foljer_av_formeln(self) -> None:
        """d/dA₀ av A₀·(a − b·A₀)^1,6 är noll i A₀ = a/(2,6·b); dämpningen där är 146,2 dB."""
        a, b = 1.0053255, 0.00122622
        self.assertAlmostEqual(STORSTA_A0_BANDFORMEL_DB, a / (2.6 * b), 12)
        vid = STORSTA_A0_BANDFORMEL_DB * (a - b * STORSTA_A0_BANDFORMEL_DB) ** 1.6
        for steg in (-1.0, 1.0):
            intill = STORSTA_A0_BANDFORMEL_DB + steg
            self.assertLess(intill * (a - b * intill) ** 1.6, vid)
        self.assertAlmostEqual(vid, 146.2, 1)

    def test_avtar_aldrig_med_avstandet(self) -> None:
        avstand = [50.0 * 1.3**k for k in range(22)]  # 50 m … 12 km
        nivaer = np.array([luftabsorption_monoton_db(F, d, 15.0, 70.0, 101.325) for d in avstand])
        self.assertTrue(bool(np.all(np.diff(nivaer, axis=0) <= 0.0)))
        self.assertTrue(bool(np.all(nivaer <= 0.0)))
        self.assertAlmostEqual(float(nivaer[-1, -1]), -146.2, 1)

    def test_tryckt_formel_avtar_for_den_yttersta_kallpunkten(self) -> None:
        """100 m från en rak väg, källpunkten i 89°: A₀ ≈ 796 dB vid 10 kHz ger 2,9 dB dämpning."""
        avstand = 96.75 / math.cos(math.radians(89.0))
        tryckt = luftabsorption_db(F, avstand, 15.0, 70.0, 101.325)
        mitt = luftabsorption_db(F, 96.75, 15.0, 70.0, 101.325)
        self.assertGreater(float(tryckt[-1]), float(mitt[-1]))  # mindre dämpning 57 ggr längre bort
        self.assertAlmostEqual(float(tryckt[-1]), -2.9, 1)
        monoton = luftabsorption_monoton_db(F, avstand, 15.0, 70.0, 101.325)
        self.assertAlmostEqual(float(monoton[-1]), -146.2, 1)
        a0 = np.array([rentonsdampning_db_per_m(float(f), 15.0, 70.0) * avstand for f in F])
        under = a0 <= STORSTA_A0_BANDFORMEL_DB
        self.assertEqual(int(np.sum(~under)), 3)  # 6,3, 8 och 10 kHz
        self.assertTrue(bool(np.array_equal(monoton[under], tryckt[under])))
        self.assertTrue(bool(np.all(monoton[~under] < -146.0)))

    def test_karnans_val(self) -> None:
        profil = plan_profil(5544.0, [200.0], [0.0, 5544.0], 0.3, 1.5)
        vader = Vader(cv2=0.12, ct2=0.008)
        monoton = Nord2000Karna().berakna(profil, vader)
        tryckt = Nord2000Karna(luftabsorption="tryckt").berakna(profil, vader)
        self.assertLess(float(monoton.luftabsorption_db[-1]), -146.0)
        self.assertGreater(float(tryckt.luftabsorption_db[-1]), -3.0)
        self.assertTrue(bool(np.array_equal(monoton.terrang_db, tryckt.terrang_db)))
        # Där ekv. (287) inte är definierad (A₀ över ca 820 dB) ger den tryckta formen fel.
        langt = plan_profil(20000.0, [200.0], [0.0, 20000.0], 0.3, 1.5)
        with self.assertRaises(BullerFel):
            Nord2000Karna(luftabsorption="tryckt").berakna(langt, vader)
        overforing = Nord2000Karna().berakna(langt, vader).overforing_db
        self.assertTrue(bool(np.all(np.isfinite(overforing))))
