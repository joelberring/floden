"""Tester för paket P-R1: väder → A, B; ekvivalent linjär profil; cirkelstrålar; skuggzon.

Svaren kommer ur dokumenten själva (ekv. 34–41 bokstavligt, räkneexempel i Kunskapscentrum om
buller:s handledning) eller ur oberoende geometri och fysik: cirkeln genom två punkter med
medelpunkten där ljudhastigheten är noll, numerisk integration av gångtiden, Fermats princip,
reflektionslagen och reciprocitet.
"""

from __future__ import annotations

import math
import unittest
from dataclasses import replace

import numpy as np

from buller.nord2000.frekvenser import ljudhastighet_m_s
from buller.nord2000.geometri import stralar_plan_mark
from buller.nord2000.refraktion import (
    Atmosfar,
    atmosfar_ur_vader,
    calc_eq_ssp,
    ekvivalent_profil_ur_gradient,
    ljudhastighetsgradient_per_s,
    ljudhastighetsprofil_m_s,
    medelljudhastighet_m_s,
    ovre_atmosfar,
    profilkoefficient_a_m_s,
    profilkoefficient_b_per_s,
)
from buller.nord2000.stralar_krokta import (
    cirkelbage,
    direkt_strale,
    gangtidsskillnad_s,
    reflekterad_strale,
    reflexpunkt_m,
    reflexpunkter_m,
    skuggzonsavstand_m,
    storsta_gangtidsskillnad_s,
    stralar_krokt_plan_mark,
)
from buller.typer import BullerFel, Vader

C0 = 340.0
_NODER, _VIKTER = np.polynomial.legendre.leggauss(80)


def _atmosfar(a: float = 0.0, b: float = 0.0, z0: float = 0.05, t_c: float = 15.0) -> Atmosfar:
    return Atmosfar(
        a_m_s=a,
        b_per_s=b,
        s_a_m_s=0.0,
        s_b_per_s=0.0,
        z0_m=z0,
        temperatur_mark_c=t_c,
        temperatur_luft_c=t_c,
        relativ_fuktighet_procent=70.0,
        lufttryck_kpa=101.325,
        cv2=0.0,
        ct2=0.0,
    )


def _cirkel_oberoende(
    d: float, z_l: float, z_u: float, xi: float, c0: float
) -> tuple[float, float, float, float]:
    """Oberoende konstruktion av strålen i profilen c0·(1 + ξz) från (0, z_l) till (d, z_u).

    Strålen är den cirkel genom punkterna vars medelpunkt ligger på höjden −1/ξ (där c = 0).
    Returnerar (båglängd, gångtid, tangentens vinkel i startpunkten, lägsta höjd på bågen).
    Gångtiden integreras numeriskt (Gauss–Legendre) som ∫ ds/c(z).
    """
    h_c = -1.0 / xi
    x_c = (d * d + (z_u - h_c) ** 2 - (z_l - h_c) ** 2) / (2.0 * d)
    radie = math.hypot(x_c, z_l - h_c)
    v_l = math.atan2(z_l - h_c, -x_c)
    v_u = math.atan2(z_u - h_c, d - x_c)
    halv = 0.5 * (v_u - v_l)
    vinklar = 0.5 * (v_u + v_l) + halv * _NODER
    hojder = h_c + radie * np.sin(vinklar)
    gangtid = float(np.sum(_VIKTER * radie * abs(halv) / (c0 * (1.0 + xi * hojder))))
    # Tangenten är vinkelrät mot radien; riktad mot växande x.
    tangent = math.atan2(-(0.0 - x_c), z_l - h_c) if xi > 0.0 else math.atan2(-x_c, h_c - z_l)
    lagsta = h_c - radie if xi < 0.0 and 0.0 < x_c < d else min(z_l, z_u)
    return radie * abs(v_u - v_l), gangtid, tangent, lagsta


def _ekv_34_till_41(d: float, dz: float, xi: float, c0: float) -> tuple[float, float, float]:
    """AV 1106/07 ekv. (32)–(41) bokstavligt, för ξ′ > 0. Returnerar (R, τ, Δθ)."""
    tan_psi = xi * d / 2.0 + dz * (2.0 + xi * dz) / (2.0 * d)
    psi = math.atan(tan_psi)
    d_m = tan_psi / xi

    f0 = (1.0 + math.sin(psi)) / (1.0 - math.sin(psi))

    def r_av(hojd: float) -> float:
        arg = (1.0 + xi * hojd) * math.cos(psi)
        return (math.asin(arg) - math.pi / 2.0 + psi) / (xi * math.cos(psi))

    def tau_av(hojd: float) -> float:
        rot = math.sqrt(1.0 - (1.0 + xi * hojd) ** 2 * math.cos(psi) ** 2)
        return math.log(f0 / ((1.0 + rot) / (1.0 - rot))) / (2.0 * xi * c0)

    if d <= d_m:
        r, tau = r_av(dz), tau_av(dz)
    else:
        # I bågens högsta punkt (ekv. 38) är arcsin-argumentet exakt 1 och roten exakt 0. De sätts
        # in så: räknade i flyttal ger arcsin intill 1 ett fel på ca 10⁻⁸.
        r_topp = psi / (xi * math.cos(psi))
        tau_topp = math.log(f0) / (2.0 * xi * c0)
        r, tau = 2.0 * r_topp - r_av(dz), 2.0 * tau_topp - tau_av(dz)
    return r, tau, psi - math.atan(dz / d)


class TestVaderTillProfil(unittest.TestCase):
    def test_a_enligt_rakneexempel_i_kcb_handledning(self) -> None:
        """KCB:s användarhandledning avsnitt 2: A = 0,25 motsvarar 1,5 m/s på 10 m, z0 = 0,025."""
        self.assertAlmostEqual(profilkoefficient_a_m_s(1.5, 10.0, 0.025), 0.25, delta=0.0005)

    def test_a_ger_vindhastigheten_pa_mathojden(self) -> None:
        """Ekv. (26) insatt i ekv. (2): den logaritmiska delen är u på höjden z_u."""
        atm = _atmosfar(a=profilkoefficient_a_m_s(3.0, 10.0, 0.05))
        self.assertAlmostEqual(
            ljudhastighetsprofil_m_s(10.0, atm) - ljudhastighetsprofil_m_s(0.0, atm), 3.0, places=12
        )

    def test_b_ar_derivatan_av_coft(self) -> None:
        """Ekv. (27): B = dc/dt · dt/dz med c = Coft(t) (ekv. 335)."""
        for t_c in (-10.0, 15.0, 30.0):
            derivata = (ljudhastighet_m_s(t_c + 1e-4) - ljudhastighet_m_s(t_c - 1e-4)) / 2e-4
            self.assertAlmostEqual(profilkoefficient_b_per_s(0.1, t_c), 0.1 * derivata, places=8)

    def test_s_a_och_s_b_ger_ovre_varden_enligt_av_1849(self) -> None:
        """AV 1849/00 avsnitt 4.5.3.2: övre värden räknas för u + 1,7σ_u och dt/dz + 1,7σ."""
        vader = Vader(vind_m_s=3.0, dt_dz_k_m=-0.02, std_vind_m_s=0.5, std_dt_dz_k_m=0.03)
        ovre = ovre_atmosfar(atmosfar_ur_vader(vader))
        direkt = atmosfar_ur_vader(
            replace(vader, vind_m_s=3.0 + 1.7 * 0.5, dt_dz_k_m=-0.02 + 1.7 * 0.03)
        )
        self.assertAlmostEqual(ovre.a_m_s, direkt.a_m_s, places=13)
        self.assertAlmostEqual(ovre.b_per_s, direkt.b_per_s, places=13)

    def test_utan_vind_och_gradient_ar_atmosfaren_homogen(self) -> None:
        atm = atmosfar_ur_vader(Vader(cv2=0.12, ct2=0.008))
        self.assertFalse(atm.har_refraktion)
        self.assertFalse(atm.har_fluktuerande_refraktion)
        self.assertAlmostEqual(atm.c_m_s, ljudhastighet_m_s(15.0), places=12)
        self.assertTrue(atmosfar_ur_vader(Vader(std_vind_m_s=0.5)).har_fluktuerande_refraktion)

    def test_ombytt_riktning_byter_tecken_pa_a(self) -> None:
        """Vinden anges längs utbredningsriktningen: motsatt riktning ger −A, samma B."""
        med = atmosfar_ur_vader(Vader(vind_m_s=3.0, dt_dz_k_m=0.05))
        mot = atmosfar_ur_vader(Vader(vind_m_s=-3.0, dt_dz_k_m=0.05))
        self.assertEqual(mot.a_m_s, -med.a_m_s)
        self.assertEqual(mot.b_per_s, med.b_per_s)

    def test_ogiltigt_vader_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            atmosfar_ur_vader(Vader(std_vind_m_s=-0.1))
        with self.assertRaises(BullerFel):
            atmosfar_ur_vader(Vader(z0_m=0.0))


class TestEkvivalentProfil(unittest.TestCase):
    def test_medelljudhastighet_mot_numerisk_integration(self) -> None:
        """Bilaga F: ekv. (403) ska vara lika med integralen i ekv. (401).

        Prövar också valet i tolkningarna: den linjära termen är B·(h_S + h_R)/2.
        """
        for a, b, h1, h2 in ((0.5, 0.03, 0.5, 4.0), (-0.4, -0.1, 0.3, 1.5), (0.0, 0.2, 2.0, 30.0)):
            atm = _atmosfar(a=a, b=b)
            z = 0.5 * (h1 + h2) + 0.5 * (h2 - h1) * _NODER
            numerisk = 0.5 * sum(
                float(w) * ljudhastighetsprofil_m_s(float(zi), atm)
                for zi, w in zip(z, _VIKTER, strict=True)
            )
            self.assertAlmostEqual(medelljudhastighet_m_s(h1, h2, atm), numerisk, places=8)

    def test_rent_linjar_profil_aterges_exakt(self) -> None:
        """A = 0: profilen är redan linjär, så ξ = B/C och c₀ = C oavsett höjder."""
        atm = _atmosfar(b=0.05)
        for h_s, h_r in ((0.5, 1.5), (4.0, 0.3), (2.0, 2.0)):
            profil = calc_eq_ssp(h_s, h_r, atm)
            self.assertAlmostEqual(profil.c0_m_s, atm.c_m_s, places=9)
            self.assertAlmostEqual(profil.xi_per_m, 0.05 / atm.c_m_s, places=14)

    def test_profilen_gar_genom_medelvardet_pa_medelhojden(self) -> None:
        """Ekv. (18)–(20): c_e(h_S) − c_e(h_R) = c(h_S) − c(h_R) och c_e(medelhöjd) = c̄."""
        atm = _atmosfar(a=0.6, b=-0.01)
        profil = calc_eq_ssp(0.5, 4.0, atm)
        self.assertAlmostEqual(
            profil.ljudhastighet_m_s(4.0) - profil.ljudhastighet_m_s(0.5),
            ljudhastighetsprofil_m_s(4.0, atm) - ljudhastighetsprofil_m_s(0.5, atm),
            places=10,
        )
        self.assertAlmostEqual(profil.ljudhastighet_m_s(2.25), profil.c_medel_m_s, places=10)
        self.assertAlmostEqual(profil.c_medel_m_s, medelljudhastighet_m_s(0.5, 4.0, atm), places=12)

    def test_symmetrisk_i_kalla_och_mottagare(self) -> None:
        atm = _atmosfar(a=0.5, b=0.02)
        ena, andra = calc_eq_ssp(0.5, 4.0, atm), calc_eq_ssp(4.0, 0.5, atm)
        self.assertAlmostEqual(ena.xi_per_m, andra.xi_per_m, places=16)
        self.assertAlmostEqual(ena.c0_m_s, andra.c0_m_s, places=10)

    def test_lika_hojder_ger_gradienten_pa_hojden(self) -> None:
        """Avsnitt 5.5.2: vid h_S = h_R är Δc/Δz gradienten på höjden (±0,005 m)."""
        atm = _atmosfar(a=0.5, b=0.02)
        analytisk = 0.5 / (2.0 + 0.05) + 0.02
        self.assertAlmostEqual(ljudhastighetsgradient_per_s(2.0, 2.0, atm), analytisk, places=6)
        # Kontinuitet: nästan lika höjder ger nästan samma profil.
        lika, nara = calc_eq_ssp(2.0, 2.0, atm), calc_eq_ssp(2.0, 2.0001, atm)
        self.assertAlmostEqual(lika.xi_per_m / nara.xi_per_m, 1.0, places=4)

    def test_hojder_under_fem_z0_ersatts(self) -> None:
        atm = _atmosfar(a=0.5, z0=0.05)
        lag, grans = calc_eq_ssp(0.01, 1.5, atm), calc_eq_ssp(0.25, 1.5, atm)
        self.assertEqual(lag, grans)
        self.assertNotEqual(calc_eq_ssp(0.30, 1.5, atm), grans)

    def test_z0_under_en_millimeter_ersatts(self) -> None:
        self.assertEqual(
            calc_eq_ssp(0.5, 1.5, _atmosfar(a=0.5, z0=1e-5)).xi_per_m,
            calc_eq_ssp(0.5, 1.5, _atmosfar(a=0.5, z0=1e-3)).xi_per_m,
        )

    def test_svag_gradient_ger_homogen_atmosfar(self) -> None:
        """|ξ| < 10⁻⁶ ⇒ ξ = 0 och c̄ = c₀ = C."""
        atm = _atmosfar(b=0.9e-6 * 340.0)
        profil = calc_eq_ssp(0.5, 1.5, atm)
        self.assertEqual(
            (profil.xi_per_m, profil.c0_m_s, profil.c_medel_m_s), (0.0,) + (atm.c_m_s,) * 2
        )
        self.assertGreater(calc_eq_ssp(0.5, 1.5, _atmosfar(b=1.1e-6 * 341.0)).xi_per_m, 1e-6)
        homogen = calc_eq_ssp(0.5, 1.5, _atmosfar())
        self.assertEqual((homogen.xi_per_m, homogen.c0_m_s), (0.0, atm.c_m_s))

    def test_tc_2_1_begransar_xi_nedat(self) -> None:
        """TC-101327 2.1: ξ ≥ −0,2 och c₀ ur ekv. (20) med Δc/Δz = −0,2·c₀."""
        atm = _atmosfar(b=-100.0)
        profil = calc_eq_ssp(0.5, 1.5, atm)
        self.assertEqual(profil.xi_per_m, -0.2)
        # Ekv. (20) ska vara uppfylld med den begränsade gradienten.
        self.assertAlmostEqual(
            profil.c0_m_s, profil.c_medel_m_s - (-0.2 * profil.c0_m_s) * (0.5 + 1.5) / 2.0, places=9
        )
        # Strax över gränsen ändras ingenting.
        fri = ekvivalent_profil_ur_gradient(-0.19 * 300.0, 300.0 * (1 - 0.19), 0.5, 1.5, atm)
        self.assertAlmostEqual(fri.xi_per_m, -0.19, places=12)
        with self.assertRaises(BullerFel):
            calc_eq_ssp(4.0, 7.0, atm)

    def test_medvind_ger_positiv_och_motvind_negativ_gradient(self) -> None:
        med = calc_eq_ssp(0.5, 1.5, atmosfar_ur_vader(Vader(vind_m_s=3.0)))
        mot = calc_eq_ssp(0.5, 1.5, atmosfar_ur_vader(Vader(vind_m_s=-3.0)))
        self.assertGreater(med.xi_per_m, 0.0)
        self.assertLess(mot.xi_per_m, 0.0)
        self.assertAlmostEqual(med.xi_per_m * med.c0_m_s, -mot.xi_per_m * mot.c0_m_s, places=12)


class TestCirkelbage(unittest.TestCase):
    FALL = (
        # d, Δz, ξ′
        (100.0, 1.0, 1e-3),  # d > d_m: strålen passerar sin högsta punkt
        (20.0, 5.0, 1e-3),  # d ≤ d_m: stigande hela vägen
        (300.0, 0.5, 5e-3),
        (50.0, 10.0, 2e-2),
        (1000.0, 3.0, 2e-4),
    )

    def test_mot_ekv_34_till_41_bokstavligt(self) -> None:
        """Räkneformen ska ge samma R, τ och Δθ som rapportens ekv. (32)–(41)."""
        grenar = set()
        for d, dz, xi in self.FALL:
            grenar.add(d <= (xi * d / 2 + dz * (2 + xi * dz) / (2 * d)) / xi)
            r, tau, dtheta = _ekv_34_till_41(d, dz, xi, C0)
            bage = cirkelbage(d, dz, xi, C0)
            self.assertAlmostEqual(bage.vagstracka_m / r, 1.0, delta=1e-10)
            self.assertAlmostEqual(bage.gangtid_s / tau, 1.0, delta=1e-10)
            self.assertAlmostEqual(bage.vinkelandring_rad, dtheta, delta=1e-12)
        self.assertEqual(grenar, {True, False})

    def test_mot_oberoende_cirkel_och_numerisk_gangtid(self) -> None:
        """Båglängd, gångtid och startvinkel mot cirkeln med medelpunkt där c = 0, båda tecken."""
        for d, dz, xi_abs in self.FALL:
            for xi in (xi_abs, -xi_abs):
                langd, gangtid, tangent, _ = _cirkel_oberoende(d, 0.0, dz, xi, C0)
                bage = cirkelbage(d, dz, xi, C0)
                self.assertAlmostEqual(bage.vagstracka_m / langd, 1.0, delta=1e-12)
                self.assertAlmostEqual(bage.gangtid_s / gangtid, 1.0, delta=1e-12)
                self.assertAlmostEqual(bage.vinkel_nedre_rad, tangent, delta=1e-12)
                self.assertAlmostEqual(
                    bage.vinkelandring_rad, tangent - math.atan2(dz, d), delta=1e-12
                )

    def test_rat_strale_for_xi_noll_och_kontinuitet(self) -> None:
        for d, dz, _ in self.FALL:
            rat = cirkelbage(d, dz, 0.0, C0)
            self.assertAlmostEqual(rat.vagstracka_m, math.hypot(d, dz), places=12)
            self.assertAlmostEqual(rat.gangtid_s, math.hypot(d, dz) / C0, places=15)
            self.assertEqual(rat.vinkelandring_rad, 0.0)
            # Serieutvecklingens gräns (|x|, |y| = 10⁻³) ska inte synas.
            nara = cirkelbage(d, dz, 1e-12, C0)
            self.assertAlmostEqual(nara.vagstracka_m / rat.vagstracka_m, 1.0, delta=1e-15)
            self.assertAlmostEqual(nara.gangtid_s / rat.gangtid_s, 1.0, delta=1e-11)
            # Serieutvecklingens gräns (|x|, |y| = 10⁻³) ska inte synas.
            for xi in (1.9e-3 / d, 2.1e-3 / d, -1.9e-3 / d, -2.1e-3 / d):
                nara = cirkelbage(d, dz, xi, C0)
                langd, gangtid, _, _ = _cirkel_oberoende(d, 0.0, dz, xi, C0)
                self.assertAlmostEqual(nara.vagstracka_m / langd, 1.0, delta=1e-12)
                self.assertAlmostEqual(nara.gangtid_s / gangtid, 1.0, delta=1e-12)

    def test_fel_utanfor_tackningen(self) -> None:
        for argument in ((0.0, 1.0, 1e-3, C0), (10.0, -1.0, 1e-3, C0), (10.0, 1.0, 1e-3, 0.0)):
            with self.assertRaises(BullerFel):
                cirkelbage(*argument)
        with self.assertRaises(BullerFel):
            cirkelbage(10.0, 6.0, -0.2, C0)  # c ≤ 0 i övre punkten


class TestDirektStrale(unittest.TestCase):
    def test_profilen_hanfors_till_den_nedre_punkten(self) -> None:
        """Ekv. (29)–(31) mot cirkeln i markens koordinater, båda tecken (exakt läge)."""
        for xi in (2e-3, -2e-3):
            langd, gangtid, tangent, _ = _cirkel_oberoende(150.0, 2.0, 6.0, xi, C0)
            strale = direkt_strale(150.0, 2.0, 6.0, xi, C0, uppat="exakt")
            self.assertAlmostEqual(strale.vagstracka_m / langd, 1.0, delta=1e-12)
            self.assertAlmostEqual(strale.gangtid_s / gangtid, 1.0, delta=1e-12)
            self.assertAlmostEqual(
                strale.vinkelandring_rad, tangent - math.atan2(4.0, 150.0), delta=1e-12
            )
            self.assertEqual(math.copysign(1.0, strale.vinkelandring_rad), math.copysign(1.0, xi))

    def test_symmetri_vid_ombytt_kalla_och_mottagare(self) -> None:
        for xi in (1e-3, -1e-3, 0.0):
            for uppat in ("bokstavlig", "exakt"):
                self.assertEqual(
                    direkt_strale(80.0, 0.5, 4.0, xi, C0, uppat),
                    direkt_strale(80.0, 4.0, 0.5, xi, C0, uppat),
                )

    def test_bokstavlig_uppatbrytning_ar_ekv_42(self) -> None:
        """ξ < 0 bokstavligt: samma R och τ som för |ξ′|, Δθ med omvänt tecken."""
        xi = -1e-3
        upp = direkt_strale(100.0, 0.5, 1.5, xi, C0)
        xi_nedre = abs(xi / (1.0 + xi * 0.5))
        bage = cirkelbage(100.0, 1.0, xi_nedre, C0 * (1.0 + xi * 0.5))
        self.assertEqual(upp.vagstracka_m, bage.vagstracka_m)
        self.assertEqual(upp.gangtid_s, bage.gangtid_s)
        self.assertEqual(upp.vinkelandring_rad, -bage.vinkelandring_rad)
        self.assertLess(upp.vinkelandring_rad, 0.0)

    def test_bokstavlig_och_exakt_ar_lika_vid_nedatbrytning(self) -> None:
        self.assertEqual(
            direkt_strale(100.0, 0.5, 1.5, 1e-3, C0, "bokstavlig"),
            direkt_strale(100.0, 0.5, 1.5, 1e-3, C0, "exakt"),
        )

    def test_lika_hojder(self) -> None:
        """Δz = 0 kräver ingen särbehandling: bågen är symmetrisk kring mitten."""
        langd, gangtid, _, _ = _cirkel_oberoende(200.0, 2.0, 2.0, 1e-3, C0)
        strale = direkt_strale(200.0, 2.0, 2.0, 1e-3, C0)
        self.assertAlmostEqual(strale.vagstracka_m / langd, 1.0, delta=1e-13)
        self.assertAlmostEqual(strale.gangtid_s / gangtid, 1.0, delta=1e-13)

    def test_skuggzonsavstand_oandligt_utan_uppatbrytning(self) -> None:
        self.assertEqual(direkt_strale(100.0, 0.5, 1.5, 0.0, C0).skuggzonsavstand_m, math.inf)
        self.assertEqual(direkt_strale(100.0, 0.5, 1.5, 1e-3, C0).skuggzonsavstand_m, math.inf)
        self.assertFalse(direkt_strale(1e4, 0.5, 1.5, 1e-5, C0).i_skuggzon)

    def test_fel_utanfor_tackningen(self) -> None:
        with self.assertRaises(BullerFel):
            direkt_strale(100.0, -0.1, 1.5, 1e-3, C0)
        with self.assertRaises(BullerFel):
            direkt_strale(100.0, 0.5, 6.0, -0.2, C0)
        with self.assertRaises(BullerFel):
            direkt_strale(100.0, 0.5, 1.5, 1e-3, C0, uppat="spegling")  # type: ignore[arg-type]


class TestSkuggzon(unittest.TestCase):
    def test_exakt_avstand_ar_cirkeln_som_tangerar_marken(self) -> None:
        """Cirkeln med radien 1/|ξ| som tangerar marken går genom källa och mottagare."""
        for h_s, h_r, xi in ((0.5, 1.5, -1e-4), (0.3, 4.0, -2e-3), (30.0, 2.0, -1e-3)):
            radie = 1.0 / abs(xi)
            d_sz = skuggzonsavstand_m(h_s, h_r, xi, "exakt")
            # Tangeringspunkten på avståndet x_t från källan; båda punkterna på cirkeln.
            x_t = math.sqrt(h_s * (2.0 * radie - h_s))
            self.assertAlmostEqual(math.hypot(x_t, radie - h_s) / radie, 1.0, delta=1e-13)
            self.assertAlmostEqual(math.hypot(d_sz - x_t, radie - h_r) / radie, 1.0, delta=1e-13)

    def test_direkt_strale_stryker_marken_pa_avstandet(self) -> None:
        """På d = d_SZ (exakt) når den direkta strålens båge precis ned till marken."""
        for h_s, h_r, xi in ((0.5, 1.5, -1e-4), (0.3, 4.0, -2e-3), (30.0, 2.0, -1e-3)):
            d_sz = skuggzonsavstand_m(h_s, h_r, xi, "exakt")
            for andel, tecken in ((0.9, 1.0), (1.0, 0.0), (1.1, -1.0)):
                _, _, _, lagsta = _cirkel_oberoende(andel * d_sz, h_s, h_r, xi, C0)
                if tecken == 0.0:
                    self.assertAlmostEqual(lagsta, 0.0, delta=1e-9 / abs(xi) * 1e-3)
                elif andel < 1.0:
                    self.assertGreater(lagsta, 0.0)
                else:
                    self.assertLess(lagsta, 0.0)

    def test_bokstavlig_avviker_med_hogst_xi_ganger_nedre_hojden(self) -> None:
        """ξ′ i stället för ξ (ekv. 30) ändrar d_SZ relativt med högst |ξ|·z_L."""
        for h_s, h_r, xi in ((0.5, 1.5, -1e-4), (0.3, 4.0, -2e-3), (30.0, 2.0, -1e-3)):
            exakt = skuggzonsavstand_m(h_s, h_r, xi, "exakt")
            bokstavlig = skuggzonsavstand_m(h_s, h_r, xi, "bokstavlig")
            self.assertLess(bokstavlig, exakt)
            self.assertLess(1.0 - bokstavlig / exakt, abs(xi) * min(h_s, h_r))
        self.assertEqual(
            skuggzonsavstand_m(0.0, 2.0, -1e-3, "exakt"),
            skuggzonsavstand_m(0.0, 2.0, -1e-3, "bokstavlig"),
        )

    def test_tc_2_2_negativ_parentes_ger_noll(self) -> None:
        """TC-101327 2.2: z > 2/|ξ′| ger roten 0 i stället för ett imaginärt tal."""
        xi = -0.1  # 2/|ξ| = 20 m
        bara_kalla = skuggzonsavstand_m(1.0, 25.0, xi, "exakt")
        self.assertAlmostEqual(bara_kalla, math.sqrt(1.0 * 19.0), places=12)
        self.assertEqual(skuggzonsavstand_m(25.0, 30.0, xi, "exakt"), 0.0)

    def test_symmetri(self) -> None:
        for uppat in ("bokstavlig", "exakt"):
            self.assertEqual(
                skuggzonsavstand_m(0.5, 4.0, -1e-3, uppat),
                skuggzonsavstand_m(4.0, 0.5, -1e-3, uppat),
            )

    def test_skuggzon_ger_ingen_reflekterad_strale(self) -> None:
        xi = -1e-3
        d_sz = skuggzonsavstand_m(0.5, 1.5, xi)
        self.assertTrue(direkt_strale(0.96 * d_sz, 0.5, 1.5, xi, C0).i_skuggzon)
        self.assertFalse(direkt_strale(0.94 * d_sz, 0.5, 1.5, xi, C0).i_skuggzon)
        with self.assertRaises(BullerFel):
            reflekterad_strale(0.96 * d_sz, 0.5, 1.5, xi, C0)
        i_skugga = stralar_krokt_plan_mark(0.96 * d_sz, 0.5, 1.5, xi, C0, C0)
        self.assertTrue(i_skugga.i_skuggzon)
        self.assertIsNone(i_skugga.reflekterad)
        self.assertEqual(i_skugga.gangtidsskillnad_s, 0.0)
        self.assertGreater(i_skugga.direkt.vagstracka_m, 0.96 * d_sz)

    def test_vid_skuggzonsgransen_gar_stralarna_ihop(self) -> None:
        """Exakt läge: ψ_G → 0, reflexpunkten → tangeringspunkten och Δτ → 0 när d → d_SZ."""
        h_s, h_r, xi = 0.5, 1.5, -1e-3
        d_sz = skuggzonsavstand_m(h_s, h_r, xi, "exakt")
        d = d_sz * (1.0 - 1e-9)
        direkt = direkt_strale(d, h_s, h_r, xi, C0, "exakt")
        d_refl = reflexpunkt_m(d, h_s, h_r, xi)
        del_s = cirkelbage(d_refl, h_s, xi, C0)
        del_r = cirkelbage(d - d_refl, h_r, xi, C0)
        self.assertAlmostEqual(d_refl, math.sqrt(h_s * (2.0 / abs(xi) - h_s)), delta=1e-2)
        self.assertAlmostEqual(del_s.vinkel_nedre_rad, 0.0, delta=1e-5)
        self.assertAlmostEqual(
            (del_s.gangtid_s + del_r.gangtid_s - direkt.gangtid_s) / direkt.gangtid_s,
            0.0,
            delta=1e-13,
        )


class TestReflekteradStrale(unittest.TestCase):
    FALL = (
        # d, h_S, h_R, ξ
        (100.0, 0.5, 1.5, 1e-4),
        (100.0, 0.5, 1.5, -1e-4),
        (200.0, 0.01, 4.0, 2e-4),
        (50.0, 4.0, 0.3, -1e-3),
        (500.0, 2.0, 2.0, 1e-5),
        (30.0, 0.75, 1.5, 5e-3),
    )

    def test_reflexpunkten_uppfyller_reflektionslagen(self) -> None:
        """De två bågarna har samma vinkel mot marken i P, räknat ur oberoende cirklar."""
        for d, h_s, h_r, xi in self.FALL:
            d_refl = reflexpunkt_m(d, h_s, h_r, xi)
            _, _, vinkel_s, _ = _cirkel_oberoende(d_refl, 0.0, h_s, xi, C0)
            _, _, vinkel_r, _ = _cirkel_oberoende(d - d_refl, 0.0, h_r, xi, C0)
            self.assertAlmostEqual(vinkel_s, vinkel_r, delta=1e-10, msg=f"{d} {h_s} {h_r} {xi}")
            strale = reflekterad_strale(d, h_s, h_r, xi, C0, "exakt")
            self.assertAlmostEqual(strale.strykvinkel_rad, vinkel_s, delta=1e-10)
            self.assertGreater(strale.strykvinkel_rad, 0.0)

    def test_reflexpunkten_ar_fermats_minimum(self) -> None:
        """Gångtiden via marken är stationär (ett minimum) i reflexpunkten, oberoende av ekv. 49."""
        for d, h_s, h_r, xi in self.FALL:
            d_refl = reflexpunkt_m(d, h_s, h_r, xi)

            def via(
                x: float, d: float = d, h_s: float = h_s, h_r: float = h_r, xi: float = xi
            ) -> float:
                return (
                    cirkelbage(x, h_s, xi, C0).gangtid_s + cirkelbage(d - x, h_r, xi, C0).gangtid_s
                )

            steg = 1e-3 * min(d_refl, d - d_refl)
            mitt, fore, efter = via(d_refl), via(d_refl - steg), via(d_refl + steg)
            self.assertGreater(fore, mitt)
            self.assertGreater(efter, mitt)
            # Symmetriskt minimum: lutningen är noll inom avrundningen av andraderivatan.
            self.assertAlmostEqual((efter - fore) / (efter + fore - 2.0 * mitt), 0.0, delta=5e-3)

    def test_vinkelandringar_ekv_47_48_mot_oberoende_cirklar(self) -> None:
        """Δθ = (rät reflekterad stråles lutning i ändpunkten) − (bågens lutning där)."""
        for d, h_s, h_r, xi in self.FALL:
            strale = reflekterad_strale(d, h_s, h_r, xi, C0, "exakt")
            rat = math.atan2(h_s + h_r, d)
            for hojd, stracka, andring in (
                (h_s, strale.d_refl_m, strale.vinkelandring_kalla_rad),
                (h_r, d - strale.d_refl_m, strale.vinkelandring_mottagare_rad),
            ):
                # Bågen räknad från ändpunkten ned mot P: lutningen nedåt i ändpunkten.
                _, _, tangent, _ = _cirkel_oberoende(stracka, hojd, 0.0, xi, C0)
                self.assertAlmostEqual(andring, rat + tangent, delta=1e-10)

    def test_delar_summeras(self) -> None:
        strale = reflekterad_strale(100.0, 0.5, 1.5, 1e-3, C0)
        self.assertAlmostEqual(
            strale.vagstracka_m,
            strale.vagstracka_kalla_m + strale.vagstracka_mottagare_m,
            places=12,
        )
        self.assertAlmostEqual(
            strale.gangtid_s, strale.gangtid_kalla_s + strale.gangtid_mottagare_s, places=15
        )

    def test_symmetri_vid_ombytt_kalla_och_mottagare(self) -> None:
        """Reciprocitet: samma strålar när källa och mottagare byter plats i samma profil."""
        for d, h_s, h_r, xi in self.FALL:
            for uppat in ("bokstavlig", "exakt"):
                fram = stralar_krokt_plan_mark(d, h_s, h_r, xi, C0, C0, uppat)
                bak = stralar_krokt_plan_mark(d, h_r, h_s, xi, C0, C0, uppat)
                assert fram.reflekterad is not None and bak.reflekterad is not None
                self.assertEqual(fram.direkt, bak.direkt)
                self.assertAlmostEqual(
                    fram.gangtidsskillnad_s / bak.gangtidsskillnad_s, 1.0, delta=1e-9
                )
                self.assertAlmostEqual(
                    fram.reflekterad.d_refl_m + bak.reflekterad.d_refl_m, d, delta=1e-9 * d
                )
                self.assertAlmostEqual(
                    fram.reflekterad.vagstracka_kalla_m / bak.reflekterad.vagstracka_mottagare_m,
                    1.0,
                    delta=1e-10,
                )
                self.assertAlmostEqual(
                    fram.reflekterad.vinkelandring_kalla_rad,
                    bak.reflekterad.vinkelandring_mottagare_rad,
                    delta=1e-10,
                )
                self.assertAlmostEqual(
                    fram.reflekterad.strykvinkel_rad, bak.reflekterad.strykvinkel_rad, delta=1e-10
                )

    def test_symmetri_med_vader_ombytt_kalla_mottagare_och_omvand_vind(self) -> None:
        """Hela kedjan: byts källa och mottagare och vänds vinden ändras ingenting.

        Vinden anges längs utbredningsriktningen, så omvänd riktning och omvänd vind ger samma
        `vind_m_s`. Utan omvänd vind byter refraktionen tecken.
        """
        vader = Vader(vind_m_s=3.0, dt_dz_k_m=-0.02)
        atm = atmosfar_ur_vader(vader)
        fram_profil, bak_profil = calc_eq_ssp(0.5, 4.0, atm), calc_eq_ssp(4.0, 0.5, atm)
        fram = stralar_krokt_plan_mark(
            150.0, 0.5, 4.0, fram_profil.xi_per_m, fram_profil.c0_m_s, atm.c_m_s
        )
        bak = stralar_krokt_plan_mark(
            150.0, 4.0, 0.5, bak_profil.xi_per_m, bak_profil.c0_m_s, atm.c_m_s
        )
        self.assertAlmostEqual(fram.gangtidsskillnad_s / bak.gangtidsskillnad_s, 1.0, delta=1e-9)
        self.assertAlmostEqual(fram.direkt.gangtid_s / bak.direkt.gangtid_s, 1.0, delta=1e-13)
        mot = calc_eq_ssp(4.0, 0.5, atmosfar_ur_vader(replace(vader, vind_m_s=-3.0)))
        self.assertLess(mot.xi_per_m, 0.0)
        self.assertGreater(fram_profil.xi_per_m, 0.0)

    def test_tre_reflexpunkter_vid_stark_nedatbrytning(self) -> None:
        """Ekv. (49) kan ha tre lösningar; alla uppfyller reflektionslagen, valet följer 5.5.5."""
        d, xi = 400.0, 0.01
        rotter = reflexpunkter_m(d, 0.5, 1.5, xi)
        self.assertEqual(len(rotter), 3)
        for rot in rotter:
            _, _, vinkel_s, _ = _cirkel_oberoende(rot, 0.0, 0.5, xi, C0)
            _, _, vinkel_r, _ = _cirkel_oberoende(d - rot, 0.0, 1.5, xi, C0)
            self.assertAlmostEqual(vinkel_s, vinkel_r, delta=1e-11)
        self.assertEqual(reflexpunkt_m(d, 0.5, 1.5, xi), rotter[0])  # h_S < h_R: närmast källan
        self.assertAlmostEqual(reflexpunkt_m(d, 1.5, 0.5, xi), d - rotter[0], delta=1e-9)
        # Lika höjder: mittpunkten är en lösning och den som väljs.
        lika = reflexpunkter_m(d, 1.0, 1.0, xi)
        self.assertEqual(len(lika), 3)
        self.assertAlmostEqual(lika[1], 200.0, delta=1e-9)
        self.assertEqual(reflexpunkt_m(d, 1.0, 1.0, xi), 200.0)
        # Svag refraktion och uppåtbrytning: en lösning.
        self.assertEqual(len(reflexpunkter_m(100.0, 0.5, 1.5, 1e-4)), 1)
        self.assertEqual(len(reflexpunkter_m(100.0, 0.5, 1.5, -1e-3)), 1)

    def test_reflexpunkten_uppfyller_ekv_49_bokstavligt(self) -> None:
        for d, h_s, h_r, xi in self.FALL:
            x = reflexpunkt_m(d, h_s, h_r, xi)
            b_s2 = h_s / xi * (2.0 + xi * h_s)
            b_r2 = h_r / xi * (2.0 + xi * h_r)
            termer = (2.0 * x**3, -3.0 * d * x**2, (b_r2 + b_s2 + d * d) * x, -b_s2 * d)
            self.assertAlmostEqual(sum(termer) / max(abs(t) for t in termer), 0.0, delta=1e-12)

    def test_bokstavlig_uppatbrytning_avviker_fran_exakt(self) -> None:
        """Dokumenterar skillnaden: med |ξ| blir refraktionens förstaordningsverkan på Δτ fel.

        Störningsräkning (gångtiden längs den räta strålen i den störda profilen) ger
        Δτ ≈ Δτ_rät·(1 + ξ·d²/(2·(h_S + h_R))). Exakt läge följer den; bokstavligt läge ger
        faktorn 1 + 2·h_L²/(h_S·h_R) på avvikelsen vid uppåtbrytning (h_L = den lägre höjden).
        """
        d, h_s, h_r, xi = 100.0, 0.5, 1.5, -1e-7
        rat = stralar_plan_mark(d, h_s, h_r, C0).gangtidsskillnad_s
        teori = xi * d * d / (2.0 * (h_s + h_r))
        exakt = stralar_krokt_plan_mark(d, h_s, h_r, xi, C0, C0, "exakt").gangtidsskillnad_s
        bokstavlig = stralar_krokt_plan_mark(d, h_s, h_r, xi, C0, C0).gangtidsskillnad_s
        self.assertAlmostEqual((exakt / rat - 1.0) / teori, 1.0, delta=1e-3)
        self.assertAlmostEqual(
            (bokstavlig / rat - 1.0) / teori, 1.0 + 2.0 * h_s**2 / (h_s * h_r), delta=1e-3
        )

    def test_fel_utanfor_tackningen(self) -> None:
        with self.assertRaises(BullerFel):
            reflekterad_strale(100.0, 0.0, 1.5, 1e-3, C0)
        with self.assertRaises(BullerFel):
            reflexpunkter_m(0.0, 0.5, 1.5, 1e-3)
        with self.assertRaises(BullerFel):
            reflexpunkter_m(1.0, 0.5, 25.0, -0.1)  # mottagaren över 2/|ξ|


class TestSvagRefraktion(unittest.TestCase):
    GEOMETRIER = ((100.0, 0.5, 1.5), (1000.0, 0.01, 4.0), (10.0, 0.75, 1.5), (300.0, 2.0, 2.0))

    def test_xi_noll_ger_de_rata_stralarna(self) -> None:
        """ξ = 0 ska återge `geometri.stralar_plan_mark` (den befintliga homogena kärnan)."""
        for d, h_s, h_r in self.GEOMETRIER:
            rat = stralar_plan_mark(d, h_s, h_r, C0)
            for uppat in ("bokstavlig", "exakt"):
                krokt = stralar_krokt_plan_mark(d, h_s, h_r, 0.0, C0, C0, uppat)
                assert krokt.reflekterad is not None
                self.assertAlmostEqual(krokt.direkt.vagstracka_m / rat.r1_m, 1.0, delta=1e-15)
                self.assertAlmostEqual(krokt.direkt.gangtid_s / rat.tau1_s, 1.0, delta=1e-15)
                self.assertAlmostEqual(krokt.reflekterad.vagstracka_m / rat.r2_m, 1.0, delta=1e-15)
                self.assertAlmostEqual(krokt.reflekterad.gangtid_s / rat.tau2_s, 1.0, delta=1e-15)
                self.assertAlmostEqual(
                    krokt.reflekterad.strykvinkel_rad, rat.strykvinkel_rad, delta=1e-15
                )
                self.assertAlmostEqual(krokt.reflekterad.d_refl_m / rat.d_refl_m, 1.0, delta=1e-14)
                self.assertEqual(krokt.direkt.vinkelandring_rad, 0.0)
                self.assertAlmostEqual(krokt.reflekterad.vinkelandring_kalla_rad, 0.0, delta=1e-15)
                self.assertAlmostEqual(
                    krokt.reflekterad.vinkelandring_mottagare_rad, 0.0, delta=1e-15
                )
                # Δτ är en skillnad mellan nästan lika tal: τ·2⁻⁵² i absolut fel.
                self.assertAlmostEqual(
                    krokt.gangtidsskillnad_s,
                    rat.gangtidsskillnad_s,
                    delta=4.0 * rat.tau2_s * 2.3e-16,
                )

    def test_avvikelsen_fran_rata_stralar_gar_linjart_mot_noll(self) -> None:
        """Δτ − Δτ_rät mot första ordningens störningsräkning, för ξ → 0 med båda tecken.

        Enligt Fermats princip ändras gångtiden i första ordningen bara genom ljudhastigheten
        längs den räta strålen: δτ = −(ξ/c₀)·∫z ds. Medelhöjden är (h_S + h_R)/2 på den direkta
        strålen och (h_S² + h_R²)/(2·(h_S + h_R)) på den reflekterade. För små vinklar blir det
        Δτ/Δτ_rät − 1 ≈ ξ·d²/(2·(h_S + h_R)).
        """
        for d, h_s, h_r in self.GEOMETRIER:
            rat = stralar_plan_mark(d, h_s, h_r, C0)
            kanslighet = -(
                rat.r2_m * (h_s**2 + h_r**2) / (2.0 * (h_s + h_r)) - rat.r1_m * (h_s + h_r) / 2.0
            ) / (C0 * rat.gangtidsskillnad_s)
            self.assertAlmostEqual(kanslighet / (d * d / (2.0 * (h_s + h_r))), 1.0, delta=0.03)
            for storlek in (1e-3, 1e-5, 1e-7):
                for tecken in (1.0, -1.0):
                    xi = tecken * storlek / kanslighet
                    krokt = stralar_krokt_plan_mark(d, h_s, h_r, xi, C0, C0, "exakt")
                    assert krokt.reflekterad is not None
                    avvikelse = krokt.gangtidsskillnad_s / rat.gangtidsskillnad_s - 1.0
                    # Tolerans: andra ordningens term plus avrundningen i skillnaden τ₂ − τ₁.
                    avrundning = 4.0 * rat.tau2_s * 2.3e-16 / rat.gangtidsskillnad_s
                    self.assertAlmostEqual(
                        avvikelse / (tecken * storlek),
                        1.0,
                        delta=2.0 * storlek + avrundning / storlek,
                    )
                    # Väglängder och vinklar avviker i andra respektive första ordningen.
                    self.assertAlmostEqual(
                        krokt.direkt.vagstracka_m / rat.r1_m, 1.0, delta=(xi * d) ** 2
                    )
                    self.assertAlmostEqual(
                        krokt.reflekterad.strykvinkel_rad, rat.strykvinkel_rad, delta=abs(xi) * d
                    )

    def test_minsta_gradient_i_metoden(self) -> None:
        """Vid metodens minsta gradient |ξ| = 10⁻⁶ (avsnitt 5.5.2), 100 m, 0,5 och 1,5 m."""
        rat = stralar_plan_mark(100.0, 0.5, 1.5, C0)
        med = stralar_krokt_plan_mark(100.0, 0.5, 1.5, 1e-6, C0, C0)
        mot = stralar_krokt_plan_mark(100.0, 0.5, 1.5, -1e-6, C0, C0, "exakt")
        assert med.reflekterad is not None
        self.assertAlmostEqual(
            med.gangtidsskillnad_s / rat.gangtidsskillnad_s - 1.0, 2.5e-3, delta=2e-5
        )
        self.assertAlmostEqual(
            mot.gangtidsskillnad_s / rat.gangtidsskillnad_s - 1.0, -2.5e-3, delta=2e-5
        )
        self.assertAlmostEqual(med.direkt.vagstracka_m - rat.r1_m, 4.2e-8, delta=1e-9)
        self.assertAlmostEqual(
            med.reflekterad.strykvinkel_rad - rat.strykvinkel_rad, 3.3e-5, delta=2e-6
        )


class TestGangtidsskillnad(unittest.TestCase):
    def test_ekv_51_utan_uppatbrytning(self) -> None:
        self.assertEqual(
            gangtidsskillnad_s(0.30002, 0.3, 100.0, 0.5, 1.5, 1e-4, math.inf, C0), 0.30002 - 0.3
        )
        self.assertEqual(
            gangtidsskillnad_s(0.30002, 0.3, 100.0, 0.5, 1.5, 0.0, math.inf, C0), 0.30002 - 0.3
        )

    def test_ekv_52_ovre_grans(self) -> None:
        d, h_s, h_r, d_sz = 100.0, 0.5, 1.5, 150.0
        grans = storsta_gangtidsskillnad_s(d, h_s, h_r, d_sz, C0)
        bokstavligt = (
            (1.0 - (d / d_sz) ** 2)
            * (math.sqrt(d * d + (h_s + h_r) ** 2) - math.sqrt(d * d + (h_s - h_r) ** 2))
            / C0
        )
        self.assertAlmostEqual(grans / bokstavligt, 1.0, delta=1e-10)
        self.assertEqual(
            gangtidsskillnad_s(0.3 + 2 * grans, 0.3, d, h_s, h_r, -1e-3, d_sz, C0), grans
        )
        under = gangtidsskillnad_s(0.3 + 0.5 * grans, 0.3, d, h_s, h_r, -1e-3, d_sz, C0)
        self.assertAlmostEqual(under, 0.5 * grans, delta=1e-16)
        self.assertEqual(storsta_gangtidsskillnad_s(d_sz, h_s, h_r, d_sz, C0), 0.0)

    def test_skuggzon_ger_noll(self) -> None:
        self.assertEqual(gangtidsskillnad_s(0.31, 0.3, 96.0, 0.5, 1.5, -1e-3, 100.0, C0), 0.0)
        self.assertGreater(gangtidsskillnad_s(0.30001, 0.3, 94.0, 0.5, 1.5, -1e-3, 100.0, C0), 0.0)

    def test_exakt_uppatbrytning_haller_sig_under_gransen(self) -> None:
        """Med den exakta bågen är 0 ≤ τ₂ − τ₁ ≤ Δτ₀ ända fram till skuggzonen, även höga källor."""
        for h_s, h_r, xi in ((0.5, 1.5, -1e-4), (50.0, 1.5, -1e-3), (30.0, 30.0, -1e-3)):
            d_sz = skuggzonsavstand_m(h_s, h_r, xi, "exakt")
            for andel in (0.2, 0.5, 0.8, 0.9499):
                d = andel * d_sz
                direkt = direkt_strale(d, h_s, h_r, xi, C0, "exakt")
                reflekterad = reflekterad_strale(d, h_s, h_r, xi, C0, "exakt")
                skillnad = reflekterad.gangtid_s - direkt.gangtid_s
                self.assertGreater(skillnad, 0.0)
                self.assertLess(skillnad, storsta_gangtidsskillnad_s(d, h_s, h_r, d_sz, C0))

    def test_bokstavlig_uppatbrytning_hog_kalla_begransas_av_ekv_52(self) -> None:
        """Rapportens iakttagelse i 5.5.6: för höga källor blir τ₂ − τ₁ för stor nära skuggzonen."""
        h_s, h_r, xi = 50.0, 1.5, -1e-3
        d = 0.9 * skuggzonsavstand_m(h_s, h_r, xi)
        stralar = stralar_krokt_plan_mark(d, h_s, h_r, xi, C0, C0)
        assert stralar.reflekterad is not None
        ra = stralar.reflekterad.gangtid_s - stralar.direkt.gangtid_s
        grans = storsta_gangtidsskillnad_s(d, h_s, h_r, stralar.direkt.skuggzonsavstand_m, C0)
        self.assertGreater(ra, grans)
        self.assertEqual(stralar.gangtidsskillnad_s, grans)


if __name__ == "__main__":
    unittest.main()
