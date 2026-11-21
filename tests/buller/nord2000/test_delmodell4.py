"""Tester för delmodell 4 (AV 1106/07 avsnitt 5.13 med TC-101327 bilaga G.1, G.2, G.5).

Svaren följer av dokumentens egna definitioner (handräknad geometri), av oberoende teori
(spegelkällemetoden för hårda ytor, reciprocitet, kontinuitet) eller av gränsfall där metoden
ska sammanfalla med plan mark.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.nord2000 import delmodell4 as d4
from buller.nord2000 import delmodell4_kilben as kb
from buller.nord2000.atmosfar import homogen_atmosfar
from buller.nord2000.delmodell1 import markeffekt_plan_mark_db
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.kildiffraktion import kil_rata_stralar, kiltryck_reflektionskoefficienter
from buller.nord2000.mark import markimpedans, sfarisk_reflektion
from buller.nord2000.refraktion import atmosfar_ur_vader
from buller.nord2000.terrang import gangvagsskillnad_m, terranggeometri
from buller.nord2000.terrang_skarmar import Skarmform, valj_skarmkanter
from buller.typer import BullerFel, Flyttal, Terrangprofil, Vader

INF = math.inf
FREKVENSER = mittfrekvenser_hz()
ATM = atmosfar_ur_vader(Vader())
C = ATM.c_m_s


def _profil(
    x: list[float],
    z: list[float],
    sigma: list[float] | float,
    h_s: float,
    h_r: float,
    rahet: list[float] | float = 0.0,
) -> Terrangprofil:
    n = len(x)
    return Terrangprofil(
        x_m=np.array(x, dtype=np.float64),
        z_m=np.array(z, dtype=np.float64),
        flodesresistivitet_kNsm4=np.broadcast_to(np.asarray(sigma, dtype=np.float64), (n - 1,)),
        rahet_m=np.broadcast_to(np.asarray(rahet, dtype=np.float64), (n - 1,)),
        kalla_hojd_m=h_s,
        mottagare_hojd_m=h_r,
    )


def _tunn_skarm(
    hojd: float,
    sigma_mark: float = 200.0,
    sigma_skarm: float = 20000.0,
    h_s: float = 0.5,
    h_r: float = 1.5,
    x_t: float = 15.0,
    d: float = 100.0,
) -> Terrangprofil:
    return _profil(
        [0.0, x_t - 0.01, x_t, x_t + 0.01, d],
        [0.0, 0.0, hojd, 0.0, 0.0],
        [sigma_mark, sigma_skarm, sigma_skarm, sigma_mark],
        h_s,
        h_r,
    )


def _form(profil: Terrangprofil) -> Skarmform:
    form = valj_skarmkanter(terranggeometri(profil)).primar_form
    assert form is not None
    return form


def _kor(profil: Terrangprofil, **kw: object) -> d4.Delmodell4Resultat:
    return d4.skarm_en_kant(FREKVENSER, profil, _form(profil), ATM, **kw)  # type: ignore[arg-type]


def _spegla(profil: Terrangprofil) -> Terrangprofil:
    """Samma terräng sedd från mottagaren: källa och mottagare byter plats."""
    return Terrangprofil(
        x_m=profil.x_m[-1] - profil.x_m[::-1],
        z_m=profil.z_m[::-1].copy(),
        flodesresistivitet_kNsm4=profil.flodesresistivitet_kNsm4[::-1].copy(),
        rahet_m=profil.rahet_m[::-1].copy(),
        kalla_hojd_m=profil.mottagare_hojd_m,
        mottagare_hojd_m=profil.kalla_hojd_m,
    )


class TestHjalpfunktioner(unittest.TestCase):
    def test_bildpunkt_i_vagrat_linje(self) -> None:
        self.assertEqual(d4.bildpunkt(0.0, 1.0, 5.0, 1.0, 2.0, 4.0), (2.0, -2.0))

    def test_bildpunkt_i_lutande_linje(self) -> None:
        # Spegling i linjen z = x byter koordinaterna.
        x, z = d4.bildpunkt(0.0, 0.0, 3.0, 3.0, 4.0, 1.0)
        self.assertAlmostEqual(x, 1.0, places=12)
        self.assertAlmostEqual(z, 4.0, places=12)

    def test_bildpunkt_ar_sin_egen_invers_och_lika_langt_fran_linjen(self) -> None:
        linje = (1.0, 0.5, 7.0, 2.0)
        xi, zi = d4.bildpunkt(*linje, 3.0, 5.0)
        x, z = d4.bildpunkt(*linje, xi, zi)
        self.assertAlmostEqual(x, 3.0, places=12)
        self.assertAlmostEqual(z, 5.0, places=12)
        for px, pz in ((1.0, 0.5), (7.0, 2.0), (4.0, 1.25)):
            self.assertAlmostEqual(
                math.hypot(px - 3.0, pz - 5.0), math.hypot(px - xi, pz - zi), places=12
            )

    def test_segmentvariabler_vagrat_segment(self) -> None:
        v = d4.segmentvariabler(0.0, 2.0, 10.0, 3.0, 2.0, 0.0, 6.0, 0.0)
        self.assertEqual(
            (v.avstand_m, v.hojd_kalla_m, v.hojd_mottagare_m, v.d1_m, v.d2_m),
            (10.0, 2.0, 3.0, 2.0, 6.0),
        )

    def test_segmentvariabler_segment_bakom_kallan_far_negativa_avstand(self) -> None:
        v = d4.segmentvariabler(0.0, 2.0, 10.0, 3.0, -3.0, 0.0, -1.0, 0.0)
        self.assertEqual((v.d1_m, v.d2_m), (-3.0, -1.0))

    def test_segmentvariabler_segment_bortom_mottagaren_ar_positivt(self) -> None:
        v = d4.segmentvariabler(0.0, 2.0, 10.0, 3.0, 12.0, 0.0, 15.0, 0.0)
        self.assertEqual((v.d1_m, v.d2_m), (12.0, 15.0))

    def test_segmentvariabler_lutande_segment_och_punkt_under_segmentet(self) -> None:
        # Segment längs z = 0,75·x (3-4-5): S = (0; 5) ligger 4 över, R = (10; 5) ligger 2 under.
        v = d4.segmentvariabler(0.0, 5.0, 10.0, 5.0, 0.0, 0.0, 4.0, 3.0)
        self.assertAlmostEqual(v.hojd_kalla_m, 4.0, places=12)
        self.assertAlmostEqual(v.hojd_mottagare_m, -2.0, places=12)
        self.assertAlmostEqual(v.avstand_m, 8.0, places=12)  # fotpunkter (2,4; 1,8) och (8,8; 6,6)
        self.assertAlmostEqual(v.d1_m, -3.0, places=12)
        self.assertAlmostEqual(v.d2_m, 2.0, places=12)

    def test_segmentvariabler_kraver_vaxande_x(self) -> None:
        with self.assertRaises(BullerFel):
            d4.segmentvariabler(0.0, 1.0, 5.0, 1.0, 3.0, 0.0, 3.0, 1.0)

    def test_ekvivalent_skarmhojd_ekv_22(self) -> None:
        self.assertEqual(d4.ekvivalent_skarmhojd_m(60.0, 15.0, 50.0), 0.0)  # R″ = 75
        self.assertAlmostEqual(d4.ekvivalent_skarmhojd_m(200.0, 10.0, 195.0), 4.0, places=12)
        self.assertAlmostEqual(d4.ekvivalent_skarmhojd_m(100.0, 42.5, 60.0), 2.0, places=12)

    def test_hojdmodifierare_ekv_175(self) -> None:
        self.assertEqual(d4.hojdmodifierare(-0.1, 0.2), 0.0)
        self.assertEqual(d4.hojdmodifierare(0.0, 0.2), 0.0)
        self.assertAlmostEqual(d4.hojdmodifierare(0.05, 0.2), 0.25, places=12)
        self.assertEqual(d4.hojdmodifierare(0.2, 0.2), 1.0)
        self.assertEqual(d4.hojdmodifierare(3.0, 0.2), 1.0)

    def test_storsta_hojd_ekv_175(self) -> None:
        self.assertAlmostEqual(d4.storsta_hojd_m(100.0), 0.05, places=12)
        self.assertEqual(d4.storsta_hojd_m(1000.0), 0.2)


class TestGangvagsskillnad(unittest.TestCase):
    """Ekv. (301): Δℓ ur R_S, R_R, θ_S, θ_R ska för räta strålar vara Δℓ₀ (ekv. 300)."""

    def test_rata_stralar_ger_gangvagsskillnaden_i_ekv_300(self) -> None:
        # Vågrät och lutande baslinje; kanten över och under siktlinjen (men över baslinjen).
        fall = {0.0: (3.0, 1.0, 0.7, 0.3), 5.0: (3.0, 1.5, 1.0), -2.0: (3.0, 0.5, 0.2)}
        tecken = set()
        for z_r, hojder in fall.items():
            for hojd in hojder:
                p = _profil(
                    [0.0, 14.0, 15.0, 16.5, 100.0], [0.0, 0.0, hojd, 0.1, z_r], 200.0, 0.5, 1.5
                )
                geo = terranggeometri(p)
                s = d4.skarmstralar(p, Skarmform(1, 2, 3), ATM)
                dl0 = gangvagsskillnad_m(geo, 2)
                tecken.add(dl0 > 0.0)
                self.assertAlmostEqual(s.gangvagsskillnad_m, dl0, delta=1e-12)
                self.assertAlmostEqual(s.kil.vag_kalla_m, math.hypot(15.0, hojd - 0.5), delta=1e-12)
        self.assertEqual(tecken, {True, False})

    def test_tecken_negativ_under_siktlinjen_positiv_i_skuggan(self) -> None:
        self.assertLess(d4.gangvagsskillnad_refraktion_m(10.0, 20.0, 1.9 * math.pi, math.pi), 0.0)
        self.assertGreater(d4.gangvagsskillnad_refraktion_m(10.0, 20.0, 1.9 * math.pi, 0.5), 0.0)
        self.assertEqual(
            d4.gangvagsskillnad_refraktion_m(10.0, 20.0, 1.5 * math.pi, 0.5 * math.pi), 0.0
        )

    def test_tryckt_form_av_ekv_301_ar_negativ_aven_i_skuggan(self) -> None:
        # Skälet till tolkningen: Sign(…)·√(…) − R_S − R_R < 0 för en kant som skymmer sikten.
        r_s, r_r, theta_s, theta_r = 10.0, 20.0, 1.9 * math.pi, 0.5
        tryckt = (
            math.sqrt(r_s**2 + r_r**2 - 2 * r_s * r_r * math.cos(theta_s - theta_r)) - r_s - r_r
        )
        self.assertLess(tryckt, 0.0)
        self.assertAlmostEqual(
            d4.gangvagsskillnad_refraktion_m(r_s, r_r, theta_s, theta_r), -tryckt, places=12
        )

    def test_av_1851_ekv_53_under_siktlinjen(self) -> None:
        r_s, r_r, theta_s, theta_r = 30.0, 70.0, 3.5, 0.5  # θ_S − θ_R < π
        ekv_53 = (
            math.sqrt(r_s**2 + r_r**2 - 2 * r_s * r_r * math.cos(theta_s - theta_r)) - r_s - r_r
        )
        self.assertAlmostEqual(
            d4.gangvagsskillnad_refraktion_m(r_s, r_r, theta_s, theta_r), ekv_53, places=10
        )


class TestSkarmstralar(unittest.TestCase):
    def test_kilvinklar_for_symmetrisk_kil(self) -> None:
        # Kil med 45° sidor: β₁ = β₂ = π/4, β = 3π/2. S och R i höjd med toppen: θ₁ = θ₂ = π/2.
        p = _profil([0.0, 7.0, 10.0, 13.0, 20.0], [0.0, 0.0, 3.0, 0.0, 0.0], 200.0, 3.0, 3.0)
        s = d4.skarmstralar(p, Skarmform(1, 2, 3), ATM)
        self.assertAlmostEqual(s.vinklar.beta1_rad, math.pi / 4, places=12)
        self.assertAlmostEqual(s.vinklar.beta2_rad, math.pi / 4, places=12)
        self.assertAlmostEqual(s.kil.beta_rad, 1.5 * math.pi, places=12)
        self.assertAlmostEqual(
            s.kil.theta_s_rad, 2 * math.pi - math.pi / 2 - math.pi / 4, places=12
        )
        self.assertAlmostEqual(s.kil.theta_r_rad, math.pi / 4, places=12)
        self.assertAlmostEqual(s.kil.vag_m, 20.0, places=12)
        self.assertAlmostEqual(s.kil.gangtid_s, 20.0 / C, places=15)

    def test_punkter_inne_i_formen_andrar_inte_kilen(self) -> None:
        a = _profil([0.0, 7.0, 10.0, 13.0, 20.0], [0.0, 0.0, 3.0, 0.0, 0.0], 200.0, 1.0, 1.0)
        b = _profil(
            [0.0, 7.0, 8.0, 10.0, 12.0, 13.0, 20.0],
            [0.0, 0.0, 2.0, 3.0, 2.5, 0.0, 0.0],
            200.0,
            1.0,
            1.0,
        )
        self.assertEqual(
            d4.skarmstralar(a, Skarmform(1, 2, 3), ATM).kil,
            d4.skarmstralar(b, Skarmform(1, 3, 5), ATM).kil,
        )

    def test_svag_refraktion_gar_mot_rata_stralar(self) -> None:
        p = _tunn_skarm(3.0)
        form = _form(p)
        rat = d4.skarmstralar(p, form, ATM)
        for u in (0.01, -0.01):
            krokt = d4.skarmstralar(p, form, atmosfar_ur_vader(Vader(vind_m_s=u)))
            self.assertAlmostEqual(krokt.gangvagsskillnad_m, rat.gangvagsskillnad_m, delta=1e-3)
            self.assertAlmostEqual(krokt.kil.vag_m, rat.kil.vag_m, delta=1e-6)
            self.assertAlmostEqual(krokt.kil.theta_s_rad, rat.kil.theta_s_rad, delta=1e-3)

    def test_medvind_minskar_och_motvind_okar_skuggan(self) -> None:
        p = _tunn_skarm(3.0)
        form = _form(p)
        rat, _ = d4.skarmeffekt_fritt_falt_db(FREKVENSER, p, form, ATM)
        med, s_med = d4.skarmeffekt_fritt_falt_db(
            FREKVENSER, p, form, atmosfar_ur_vader(Vader(vind_m_s=3.0))
        )
        mot, s_mot = d4.skarmeffekt_fritt_falt_db(
            FREKVENSER, p, form, atmosfar_ur_vader(Vader(vind_m_s=-3.0))
        )
        dl0 = d4.skarmstralar(p, form, ATM).gangvagsskillnad_m
        self.assertLess(s_med.gangvagsskillnad_m, dl0)
        self.assertGreater(s_mot.gangvagsskillnad_m, dl0)
        self.assertGreater(s_med.vinkelandring_kalla_rad, 0.0)
        self.assertLess(s_mot.vinkelandring_mottagare_rad, 0.0)
        self.assertTrue(bool(np.all(med > rat)) and bool(np.all(mot < rat)))

    def test_refraktion_kan_gora_en_kant_under_siktlinjen_skarmande(self) -> None:
        p = _tunn_skarm(0.6)  # 0,05 m under den räta siktlinjen
        form = _form(p)
        self.assertLess(d4.skarmstralar(p, form, ATM).gangvagsskillnad_m, 0.0)
        mot = d4.skarmstralar(p, form, atmosfar_ur_vader(Vader(vind_m_s=-3.0)))
        self.assertGreater(mot.gangvagsskillnad_m, 0.0)

    def test_kant_under_baslinjen_ger_fel(self) -> None:
        p = _profil([0.0, 10.0, 20.0], [0.0, -1.0, 0.0], 200.0, 1.0, 1.0)
        with self.assertRaises(BullerFel):
            d4.skarmstralar(p, Skarmform(0, 1, 2), ATM)

    def test_ogiltiga_index_ger_fel(self) -> None:
        p = _tunn_skarm(3.0)
        with self.assertRaises(BullerFel):
            d4.kilgeometri(terranggeometri(p), 2, 2, 3)


class TestEkvivalentReflektion(unittest.TestCase):
    """TC-101327 bilaga G.1 och G.5."""

    def test_definitionen_ekv_404(self) -> None:
        f, psi, r, sigma = 250.0, 0.4, 12.0, 2000.0
        q_c = sfarisk_reflektion(f, r / C, psi, markimpedans(f, sigma))
        q_e = kb.ekvivalent_reell_reflektion(f, psi, r, sigma, C)
        self.assertGreater(q_e, kb.EKVIVALENT_REFLEKTION_MIN)
        self.assertAlmostEqual(20 * math.log10(1 + q_e), 20 * math.log10(abs(1 + q_c)), places=12)

    def test_hard_yta_ger_ett(self) -> None:
        self.assertEqual(kb.ekvivalent_reell_reflektion(1000.0, 0.3, 10.0, INF, C), 1.0)
        self.assertAlmostEqual(
            kb.ekvivalent_reell_reflektion(100.0, 1.2, 10.0, 1e9, C), 1.0, delta=1e-3
        )

    def test_nedre_grans_och_tio_decibel_under_hard_yta(self) -> None:
        # Mjuk mark, strykande infall, hög frekvens: Q_c → −1, så Q_e begränsas till −0,369.
        q_e = kb.ekvivalent_reell_reflektion(10000.0, 0.0, 50.0, 12.5, C)
        self.assertEqual(q_e, -0.369)
        self.assertAlmostEqual(20 * math.log10(1 + q_e), 20 * math.log10(2.0) - 10.0, delta=0.05)

    def test_aldrig_under_gransen_och_bara_obetydligt_over_ett(self) -> None:
        # TC-101327 anger "between −1 and 1", men |1 + Q_c| kan överstiga 2 något (ytvågstermen
        # i den sfäriska koefficienten). Ingen övre gräns föreskrivs och ingen läggs till.
        storsta = -1.0
        for f in FREKVENSER:
            for sigma in (12.5, 200.0, 20000.0):
                for psi in (0.0, 0.05, 0.5, 1.5):
                    q = kb.ekvivalent_reell_reflektion(float(f), psi, 30.0, sigma, C)
                    self.assertGreaterEqual(q, -0.369)
                    storsta = max(storsta, q)
        self.assertGreater(storsta, 1.0)
        self.assertLess(storsta, 1.1)

    def test_energireflektion_ekv_440(self) -> None:
        self.assertEqual(kb.ekvivalent_reflektion_ur_energireflektion(1.0), 1.0)
        self.assertAlmostEqual(kb.ekvivalent_reflektion_ur_energireflektion(0.64), 0.8, places=12)
        with self.assertRaises(BullerFel):
            kb.ekvivalent_reflektion_ur_energireflektion(1.2)


class TestKilben(unittest.TestCase):
    """TC-101327 bilaga G.2: handräknade impedansprofiler."""

    def test_lodrat_vagg_endast_toppen_av_vaggen_raknas(self) -> None:
        ben = kb.kilben([0.0, 0.0, 40.0], [3.0, 0.0, 0.0], [20000.0, 200.0], 1)
        self.assertEqual(ben.d_w_m, 3.0)
        self.assertAlmostEqual(ben.alfa_rad, math.pi / 2, places=12)
        self.assertEqual(ben.d_t_m, 0.0)  # ekv. (410): α ≥ π/2
        self.assertAlmostEqual(ben.avstand_m, math.hypot(40.0, 3.0), places=12)  # |T R_G|
        self.assertAlmostEqual(ben.strykvinkel_rad, math.atan(40.0 / 3.0), places=12)
        self.assertLess(ben.d_fz_m, 3.0)
        self.assertEqual(ben.impedansvikter, ((20000.0, 1.0),))
        # d_FZ ligger på Fresnelellipsen med brännpunkterna T och R_G och F·λ = 0,17 (ekv. 108).
        punkt = (0.0, 3.0 - ben.d_fz_m)
        omvag = ben.d_fz_m + math.hypot(40.0 - punkt[0], punkt[1]) - ben.avstand_m
        self.assertAlmostEqual(omvag, 0.17, places=10)

    def test_lutande_kilyta_avbildar_marken_pa_forlangningen(self) -> None:
        # Kilyta 45° nedåt, sedan vågrät mark: α = 45°.
        ben = kb.kilben([0.0, 3.0, 43.0], [3.0, 0.0, 0.0], [500.0, 200.0], 1)
        self.assertAlmostEqual(ben.alfa_rad, math.pi / 4, places=12)
        self.assertAlmostEqual(ben.d_w_m, 3.0 * math.sqrt(2.0), places=12)
        self.assertAlmostEqual(ben.d_t_m, 40.0 / math.sqrt(2.0), places=12)
        self.assertAlmostEqual(ben.h_g_m, 40.0 / math.sqrt(2.0), places=12)
        self.assertAlmostEqual(ben.avstand_m, math.hypot(43.0, 3.0), places=12)
        # Fresnelzonen (F·λ = 0,17 m) når bara 0,68 m ned längs den 4,24 m långa kilytan.
        self.assertLess(ben.d_fz_m, ben.d_w_m)
        self.assertEqual(ben.impedansvikter, ((500.0, 1.0),))

    def test_kort_kilyta_raden_for_marken_kortas_vid_d_fz(self) -> None:
        ben = kb.kilben([0.0, 0.2, 40.2], [0.2, 0.0, 0.0], [500.0, 200.0], 1)
        self.assertAlmostEqual(ben.d_w_m, 0.2 * math.sqrt(2.0), places=12)
        self.assertGreater(ben.d_fz_m, ben.d_w_m)
        self.assertLess(ben.d_fz_m, ben.d_w_m + ben.d_t_m)
        vikter = dict(ben.impedansvikter)
        self.assertAlmostEqual(vikter[500.0], ben.d_w_m / ben.d_fz_m, places=12)
        self.assertAlmostEqual(vikter[200.0], 1.0 - ben.d_w_m / ben.d_fz_m, places=12)
        self.assertAlmostEqual(ben.rader[-1].slut_fran_topp_m, ben.d_fz_m, places=12)
        self.assertAlmostEqual(ben.rader[-1].skalad_langd_m, ben.d_fz_m - ben.d_w_m, places=12)

    def test_enbart_kilform_tar_med_hela_profilen(self) -> None:
        # W = G: vikterna är segmentens andelar av polylinjens längd (ekv. 406–409).
        ben = kb.kilben([0.0, 3.0, 9.0], [5.0, 4.0, 0.0], [100.0, 300.0], 2)
        l1, l2 = math.hypot(3.0, 1.0), math.hypot(6.0, 4.0)
        self.assertEqual((ben.d_t_m, ben.h_g_m, ben.strykvinkel_rad), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(ben.avstand_m, math.hypot(9.0, 5.0), places=12)
        self.assertAlmostEqual(ben.d_fz_m, ben.d_w_m + 0.17 / 2, places=10)
        vikter = dict(ben.impedansvikter)
        self.assertAlmostEqual(vikter[100.0], l1 / (l1 + l2), places=12)
        self.assertAlmostEqual(vikter[300.0], l2 / (l1 + l2), places=12)
        self.assertAlmostEqual(sum(r.skalad_langd_m for r in ben.rader), ben.d_w_m, places=12)

    def test_mark_under_kilytans_forlangning(self) -> None:
        ben = kb.kilben([0.0, 3.0, 5.0], [3.0, 0.0, -20.0], [500.0, 200.0], 1)
        self.assertLess(ben.h_g_m, 0.0)
        self.assertLess(ben.alfa_rad, 0.0)
        self.assertAlmostEqual(ben.d_t_m, math.hypot(2.0, 20.0), places=12)  # ekv. (410): α ≤ 0
        self.assertEqual(ben.strykvinkel_rad, 0.0)  # ekv. (417)
        self.assertAlmostEqual(ben.avstand_m, ben.d_w_m + ben.d_t_m, places=12)  # ekv. (416)

    def test_samma_flodesresistivitet_slas_ihop_och_vikterna_summerar_till_ett(self) -> None:
        ben = kb.kilben([0.0, 1.0, 2.0, 4.0], [3.0, 2.0, 1.0, 0.0], [200.0, 500.0, 200.0], 3)
        self.assertEqual(len(ben.rader), 3)
        vikter = dict(ben.impedansvikter)
        self.assertEqual(set(vikter), {200.0, 500.0})
        langder = [math.sqrt(2.0), math.sqrt(2.0), math.sqrt(5.0)]
        self.assertAlmostEqual(vikter[500.0], langder[1] / sum(langder), places=12)
        self.assertAlmostEqual(sum(vikter.values()), 1.0, places=12)

    def test_medelreflektion_ar_vagt_medel_av_q_e(self) -> None:
        ben = kb.kilben([0.0, 3.0, 43.0], [3.0, 0.0, 0.0], [500.0, 200.0], 1)
        f = 500.0
        q = [
            kb.ekvivalent_reell_reflektion(f, ben.strykvinkel_rad, ben.avstand_m, s, C)
            for s, _ in ben.impedansvikter
        ]
        vantat = sum(w * qi for (_, w), qi in zip(ben.impedansvikter, q, strict=True))
        self.assertAlmostEqual(kb.medelreflektion_kilben(f, ben, C), vantat, places=14)
        self.assertTrue(min(q) <= vantat <= max(q))

    def test_kallsidan_ar_spegelbilden_av_mottagarsidan(self) -> None:
        p = _profil(
            [0.0, 20.0, 28.0, 30.0, 33.0, 60.0, 100.0],
            [0.0, 0.5, 1.0, 4.0, 0.5, 0.2, 0.0],
            [12.5, 200.0, 500.0, 2000.0, 80.0, 20000.0],
            1.0,
            2.0,
        )
        geo, form = terranggeometri(p), _form(p)
        ps = _spegla(p)
        geo_s, form_s = terranggeometri(ps), _form(ps)
        kalla, mottagare = d4.kilben_ur_terrang(geo, form)
        kalla_s, mottagare_s = d4.kilben_ur_terrang(geo_s, form_s)
        for a, b in ((kalla, mottagare_s), (mottagare, kalla_s)):
            self.assertAlmostEqual(a.d_fz_m, b.d_fz_m, places=9)
            self.assertAlmostEqual(a.strykvinkel_rad, b.strykvinkel_rad, places=12)
            for (s1, w1), (s2, w2) in zip(a.impedansvikter, b.impedansvikter, strict=True):
                self.assertEqual(s1, s2)
                self.assertAlmostEqual(w1, w2, places=9)

    def test_ogiltig_indata_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            kb.kilben([0.0, 1.0], [1.0, 0.0], [200.0], 2)
        with self.assertRaises(BullerFel):
            kb.kilben([0.0, 1.0, 1.0], [1.0, 0.0, 0.0], [200.0, 200.0], 1)

    def test_givet_varde_gar_fore_bilaga_g2(self) -> None:
        p = _tunn_skarm(3.0)
        geo, form = terranggeometri(p), _form(p)
        q_s, q_r = d4.kilytornas_reflektion(FREKVENSER, geo, form, C, q_kilyta_kalla=0.8)
        self.assertTrue(bool(np.all(q_s == 0.8)))
        self.assertTrue(bool(np.all(q_r > 0.9)))  # hård skärm (σ = 20000)


class TestViktnormering(unittest.TestCase):
    """Ekv. (187) och valet mellan AV 1849/00 och den tryckta formen."""

    def test_summor_under_ett_normeras_och_ger_w_q_i_kvadrat(self) -> None:
        w1, w2, q1, q2 = d4.normerade_vikter([0.2, 0.3], [0.9])
        self.assertEqual(w1, [0.4, 0.6])
        self.assertEqual(w2, [1.0])
        self.assertAlmostEqual(q1, 0.25, places=15)
        self.assertAlmostEqual(q2, 0.81, places=15)

    def test_summa_ett_ger_w_q_ett(self) -> None:
        regler: tuple[d4.Viktregel, ...] = ("av1849", "tryckt")
        for regel in regler:
            w1, _, q1, _ = d4.normerade_vikter([0.25, 0.75], [1.0], regel)
            self.assertEqual((w1, q1), ([0.25, 0.75], 1.0))

    def test_summa_noll_ger_noll(self) -> None:
        w1, w2, q1, q2 = d4.normerade_vikter([0.0, 0.0], [0.5])
        self.assertEqual((w1, w2, q1, q2), ([0.0, 0.0], [1.0], 0.0, 0.25))

    def test_av_1849_lamnar_vikter_over_ett_oforandrade(self) -> None:
        w1, w2, q1, q2 = d4.normerade_vikter([0.8, 0.7], [0.6, 0.6])
        self.assertEqual((w1, w2, q1, q2), ([0.8, 0.7], [0.6, 0.6], 1.0, 1.0))

    def test_tryckt_ekv_187_over_ett(self) -> None:
        w1, w2, _, _ = d4.normerade_vikter([0.8, 0.7], [0.6, 0.6], "tryckt")
        # Δw_1t = 0,5, Δw_2t = 0,2, Δw_t = 0,7.
        self.assertAlmostEqual(sum(w1), 0.5 / 0.7 + 1.0, places=12)
        self.assertAlmostEqual(sum(w2), 0.2 / 0.7 + 1.0, places=12)
        self.assertAlmostEqual(w1[0] / w1[1], 0.8 / 0.7, places=12)

    def test_tryckt_form_hoppar_vid_summan_ett_men_inte_av_1849(self) -> None:
        # Skälet till valet: vanlig plan mark ger w_t = 1 så när som på avrundning.
        strax_over = [0.5, 0.5 + 1e-12]
        tryckt, _, _, _ = d4.normerade_vikter(strax_over, [1.0], "tryckt")
        vald, _, _, _ = d4.normerade_vikter(strax_over, [1.0], "av1849")
        self.assertAlmostEqual(sum(tryckt), 2.0, places=9)
        self.assertAlmostEqual(sum(vald), 1.0, places=9)

    def test_okand_regel_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            d4.normerade_vikter([1.0], [1.0], "annan")  # type: ignore[arg-type]


class TestBasmodell(unittest.TestCase):
    """Ekv. (182)."""

    def test_fullt_koherent_ar_beloppet_av_stralsumman(self) -> None:
        p1, p2, p3, p4 = 1.0 + 0.2j, 0.5 - 0.3j, -0.4 + 0.1j, 0.2 + 0.2j
        q1, q2 = 0.7 + 0.2j, -0.3 + 0.5j
        vantat = abs(p1 + q1 * p2 + q2 * p3 + q1 * q2 * p4) / abs(p1)
        self.assertAlmostEqual(
            d4.markfaktor_basmodell(p1, p2, p3, p4, q1, q2, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9, 0.8),
            vantat,
            places=13,
        )

    def test_helt_inkoherent_ar_energisumma_med_inkoherenta_koefficienter(self) -> None:
        p1, p2, p3, p4 = 1.0, 0.5j, -0.4, 0.2
        vantat = math.sqrt(1.0 + (0.9 * 0.5) ** 2 + (0.8 * 0.4) ** 2 + (0.9 * 0.8 * 0.2) ** 2)
        self.assertAlmostEqual(
            d4.markfaktor_basmodell(p1, p2, p3, p4, 0.3, 0.3, 1.0, 1.0, 0.0, 0.0, 0.0, 0.9, 0.8),
            vantat,
            places=13,
        )

    def test_vikterna_w_q_skalar_reflexerna(self) -> None:
        a = d4.markfaktor_basmodell(
            1.0, 0.5, 0.4, 0.2, 1.0, 1.0, 0.5, 0.25, 1.0, 1.0, 1.0, 1.0, 1.0
        )
        self.assertAlmostEqual(a, 1.0 + 0.5 * 0.5 + 0.25 * 0.4 + 0.5 * 0.25 * 0.2, places=13)
        utan = d4.markfaktor_basmodell(
            1.0, 0.5, 0.4, 0.2, 1.0, 1.0, 0.0, 0.0, 0.3, 0.3, 0.3, 1.0, 1.0
        )
        self.assertEqual(utan, 1.0)

    def test_p1_noll_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            d4.markfaktor_basmodell(0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)


class TestHardSkarmHardMark(unittest.TestCase):
    """Hård skärm på hård plan mark: spegelkällemetoden med kildiffraktionen (ekv. 157)."""

    X_T, HOJD, H_S, H_R, D = 15.0, 3.0, 0.3, 1.5, 100.0

    def _kiltryck(self, z_s: float, z_r: float, f: float) -> complex:
        """pwedge för källa i (0, z_s) och mottagare i (D, z_r), geometrin räknad för sig."""
        b = math.atan2(0.01, self.HOJD)  # kilytans vinkel mot lodlinjen (fötter ±0,01 m)
        theta_1 = math.atan2(self.X_T, self.HOJD - z_s)
        theta_2 = math.atan2(self.D - self.X_T, self.HOJD - z_r)
        kil = kil_rata_stralar(
            2 * math.pi - 2 * b,
            2 * math.pi - theta_1 - b,
            theta_2 - b,
            math.hypot(self.X_T, self.HOJD - z_s),
            math.hypot(self.D - self.X_T, self.HOJD - z_r),
            C,
        )
        return kiltryck_reflektionskoefficienter(f, kil, 1.0, 1.0)

    def _resultat(self) -> d4.Delmodell4Resultat:
        p = _tunn_skarm(self.HOJD, INF, INF, self.H_S, self.H_R, self.X_T, self.D)
        return _kor(p, fullt_koherent=True)

    def test_fyra_stralar_med_spegelkalla_och_spegelmottagare(self) -> None:
        r = self._resultat()
        r_sr = math.hypot(self.D, self.H_R - self.H_S)
        storsta = 0.0
        for i, f_np in enumerate(FREKVENSER):
            f = float(f_np)
            w1 = min(float(r.viktsumma_kalla[i]), 1.0) ** 2  # w_Q1, ekv. (187)
            w2 = min(float(r.viktsumma_mottagare[i]), 1.0) ** 2
            summa = (
                self._kiltryck(self.H_S, self.H_R, f)
                + w1 * self._kiltryck(-self.H_S, self.H_R, f)
                + w2 * self._kiltryck(self.H_S, -self.H_R, f)
                + w1 * w2 * self._kiltryck(-self.H_S, -self.H_R, f)
            )
            storsta = max(storsta, abs(20 * math.log10(abs(summa) * r_sr) - float(r.delta_l_db[i])))
        self.assertLess(storsta, 1e-8)

    def test_marken_tacker_fresnelzonen_fran_160_hz(self) -> None:
        # Profilen börjar i källan, så zonen når bakom profilens början vid låga frekvenser.
        r = self._resultat()
        self.assertTrue(bool(np.all(r.viktsumma_mottagare > 1.0 - 1e-9)))
        self.assertTrue(bool(np.all(r.viktsumma_kalla[8:] > 1.0 - 1e-9)))
        self.assertTrue(bool(np.all(r.viktsumma_kalla[:6] < 1.0)))
        self.assertTrue(bool(np.all(r.viktsumma_kalla > 0.96)))

    def test_kilytorna_ar_harda(self) -> None:
        r = self._resultat()
        self.assertTrue(bool(np.all(r.q_av_kalla == 1.0)) and bool(np.all(r.q_av_mottagare == 1.0)))

    def test_delarna_summerar(self) -> None:
        r = self._resultat()
        np.testing.assert_allclose(
            r.delta_l_db, r.skarmeffekt_db + 20 * np.log10(r.markfaktor_pg), atol=1e-10
        )

    def test_lag_frekvens_fyra_stralar_i_fas(self) -> None:
        # 25 Hz: gångvägsskillnaderna är små mot våglängden, markfaktorn närmar sig 4·(…).
        r = _kor(_tunn_skarm(3.0, INF, INF, 0.01, 0.01), fullt_koherent=True)
        w = float(r.viktsumma_kalla[0]) ** 2
        self.assertAlmostEqual(float(r.markfaktor_pg[0]), (1 + w) * 2.0, delta=0.05)

    def test_med_koherens_ar_markfaktorn_begransad_av_fyra(self) -> None:
        r = _kor(_tunn_skarm(self.HOJD, INF, INF, self.H_S, self.H_R))
        self.assertTrue(bool(np.all(r.markfaktor_pg <= 4.0 + 1e-9)))
        self.assertTrue(bool(np.all(r.markfaktor_pg > 0.0)))


class TestReciprocitet(unittest.TestCase):
    def test_ombytt_kalla_och_mottagare(self) -> None:
        rng = np.random.default_rng(20261121)
        storsta = 0.0
        for _ in range(12):
            x = [0.0, 5.0, 12.0, 20.0, 21.5, 23.0, 40.0, 70.0, 100.0]
            z = np.array([0.0, 0.2, -0.1, 0.3, 4.0, 0.5, 0.1, 0.4, 0.0]) + rng.uniform(-0.2, 0.2, 9)
            z[4] = rng.uniform(2.0, 6.0)
            p = _profil(
                x,
                [float(v) for v in z],
                [float(v) for v in rng.choice([12.5, 200.0, 500.0, 20000.0], 8)],
                float(rng.uniform(0.01, 2.0)),
                float(rng.uniform(1.0, 5.0)),
                [float(v) for v in rng.choice([0.0, 0.05, 0.25], 8)],
            )
            a, b = _kor(p), _kor(_spegla(p))
            storsta = max(storsta, float(np.max(np.abs(a.delta_l_db - b.delta_l_db))))
            np.testing.assert_allclose(a.markfaktor_pg, b.markfaktor_pg, rtol=1e-8)
            np.testing.assert_allclose(a.q_av_kalla, b.q_av_mottagare, atol=1e-9)
        self.assertLess(storsta, 1e-7)


class TestKontinuitet(unittest.TestCase):
    """Skärmkanten passerar siktlinjen (Δℓ byter tecken)."""

    @staticmethod
    def _profil(hojd: float) -> Terrangprofil:
        return _profil(
            [0.0, 29.5, 30.0, 30.5, 100.0],
            [0.0, 0.0, hojd, 0.0, 0.0],
            [200.0, 20000.0, 20000.0, 200.0],
            1.0,
            2.0,
        )

    def test_inget_sprang_over_siktlinjen(self) -> None:
        h0 = 1.0 + 30.0 / 100.0  # siktlinjens höjd i x = 30
        under, over = _kor(self._profil(h0 - 1e-6)), _kor(self._profil(h0 + 1e-6))
        self.assertLess(under.stralar.gangvagsskillnad_m, 0.0)
        self.assertGreater(over.stralar.gangvagsskillnad_m, 0.0)
        # Kildiffraktionens kända rest (tabell 4–5 ger f(0) + g(0) ≠ 1) är ca 0,002 dB.
        self.assertLess(float(np.max(np.abs(under.delta_l_db - over.delta_l_db))), 0.005)

    def test_pa_siktlinjen_ar_skarmeffekten_nara_minus_sex_decibel(self) -> None:
        r = _kor(self._profil(1.3))
        # Halvplan på skugggränsen ger −6,02 dB; kilytorna (hårda, 9,5° från lodlinjen) lägger
        # till en svag reflex vid låga frekvenser.
        self.assertTrue(bool(np.all(np.abs(r.skarmeffekt_db[12:] + 6.02) < 0.3)))

    def test_monotont_storre_skarmverkan_med_hogre_skarm(self) -> None:
        nivaer = [
            float(_kor(self._profil(h)).skarmeffekt_db[16]) for h in (1.0, 1.3, 2.0, 4.0, 8.0)
        ]
        self.assertEqual(nivaer, sorted(nivaer, reverse=True))

    def test_jamnt_forlopp_kring_siktlinjen(self) -> None:
        hojder = np.linspace(1.25, 1.35, 21)
        nivaer = np.array([_kor(self._profil(float(h))).delta_l_db for h in hojder])
        steg = np.abs(np.diff(nivaer, axis=0))
        self.assertLess(float(steg.max()), 0.6)  # 5 mm per steg; störst vid 10 kHz


class TestGransfallPlanMark(unittest.TestCase):
    """Skärmhöjden går mot noll. Metoden lovar bara plan mark genom r_scr1 → 0 (ekv. 332)."""

    D, H_S, H_R = 100.0, 0.5, 1.5

    def _bred_kil(self, hojd: float, sigma: float) -> Flyttal:
        return _kor(
            _profil([0.0, 50.0, self.D], [0.0, hojd, 0.0], sigma, self.H_S, self.H_R)
        ).delta_l_db

    def test_hard_mark_gar_mot_plan_hard_mark(self) -> None:
        # Hela terrängen är kilens två ytor (inga segment utanför formen): p_G = 1 och kilen med
        # β → π ger direkt och speglad stråle. Delmodell 1 har dessutom F_f, så jämförelsen görs
        # där F_f > 0,999 (till och med 2 kHz för dessa höjder).
        plan = markeffekt_plan_mark_db(
            FREKVENSER, self.D, self.H_S, self.H_R, 1e12, 0.0, homogen_atmosfar(Vader())
        )
        avvikelse = [
            float(np.max(np.abs(self._bred_kil(h, INF) - plan)[:20])) for h in (0.1, 0.01, 1e-4)
        ]
        self.assertLess(avvikelse[0], 0.5)
        self.assertLess(avvikelse[1], 0.05)
        self.assertLess(avvikelse[2], 0.005)

    def test_hard_mark_plus_sex_decibel_vid_lag_frekvens(self) -> None:
        self.assertAlmostEqual(float(self._bred_kil(1e-4, INF)[0]), 6.02, delta=0.01)

    def test_mjuk_mark_gar_inte_mot_delmodell_1(self) -> None:
        # Dokumenterad avvikelse: kilytornas Q_av är reell (bilaga G) och bär ingen fas, så
        # markdippen saknas. Metoden använder inte delmodell 4 här (r_scr1 = 0).
        plan = markeffekt_plan_mark_db(
            FREKVENSER, self.D, self.H_S, self.H_R, 200.0, 0.0, homogen_atmosfar(Vader())
        )
        skillnad = self._bred_kil(1e-4, 200.0) - plan
        self.assertLess(float(np.max(np.abs(skillnad[:3]))), 0.5)  # 25–40 Hz
        self.assertGreater(float(np.max(skillnad)), 10.0)  # i markdippen

    def test_lag_tunn_skarm_markreflexerna_tonas_ut(self) -> None:
        # T närmar sig markens förlängning: r_R1 och r_S2 (ekv. 175–176) går mot noll.
        r_hog = _kor(_tunn_skarm(1.0))
        r_lag = _kor(_tunn_skarm(0.001))
        self.assertTrue(bool(np.all(r_hog.viktsumma_mottagare > 0.99)))
        self.assertTrue(bool(np.all(r_lag.viktsumma_mottagare < 0.03)))
        self.assertTrue(bool(np.all(r_lag.viktsumma_kalla < 0.14)))


class TestAllmanModell(unittest.TestCase):
    def test_uppdelning_av_ett_marksegment_andrar_inget(self) -> None:
        hel = _profil(
            [0.0, 14.5, 15.0, 15.5, 100.0],
            [0.0, 0.0, 3.0, 0.0, 0.0],
            [200.0, 2e4, 2e4, 200.0],
            0.5,
            1.5,
        )
        delad = _profil(
            [0.0, 4.0, 9.0, 14.5, 15.0, 15.5, 40.0, 70.0, 100.0],
            [0.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 0.0],
            [200.0, 200.0, 200.0, 2e4, 2e4, 200.0, 200.0, 200.0],
            0.5,
            1.5,
        )
        np.testing.assert_allclose(_kor(hel).delta_l_db, _kor(delad).delta_l_db, atol=1e-8)

    def test_viktregler_vid_viktsumma_over_ett(self) -> None:
        # Dalformad mark på källsidan: två segment speglar var för sig, så w_1t > 1. Med
        # AV 1849/00 är exponenterna w″ (summa w_1t); med ekv. (187) bokstavligt
        # w″/w_1t·(Δw_1t/Δw_t + 1), alltså summan 2 när bara källsidan överstiger 1.
        dal = _profil(
            [0.0, 8.0, 16.0, 19.5, 20.0, 20.5, 100.0],
            [0.0, -1.5, 0.0, 0.0, 4.0, 0.0, 0.0],
            [200.0, 200.0, 200.0, 2e4, 2e4, 200.0],
            1.0,
            1.5,
        )
        vald = _kor(dal)
        tryckt = _kor(dal, viktregel="tryckt")
        over = (vald.viktsumma_kalla > 1.01) & (vald.viktsumma_mottagare <= 1.0)
        self.assertGreater(int(over.sum()), 5)
        kvot = np.log(tryckt.markfaktor_pg[over]) / np.log(vald.markfaktor_pg[over])
        np.testing.assert_allclose(kvot, 2.0 / vald.viktsumma_kalla[over], rtol=1e-9)
        np.testing.assert_array_equal(tryckt.skarmeffekt_db, vald.skarmeffekt_db)

    def test_sida_utan_mark_lamnar_den_andra_sidans_reflex(self) -> None:
        # Kilen börjar i profilens första punkt: ingen mark på källsidan (AV 1849/00 ekv. 70).
        p = _profil([0.0, 15.0, 15.5, 100.0], [0.0, 3.0, 0.0, 0.0], [2e4, 2e4, 200.0], 0.5, 1.5)
        r = _kor(p)
        self.assertTrue(bool(np.all(r.viktsumma_kalla == 0.0)))
        self.assertTrue(bool(np.any(np.abs(20 * np.log10(r.markfaktor_pg)) > 3.0)))
        bokstavlig = _kor(p, viktregel="tryckt")
        np.testing.assert_array_equal(bokstavlig.markfaktor_pg, np.ones(27))

    def test_inga_marksegment_alls_ger_ren_skarmeffekt(self) -> None:
        p = _profil([0.0, 15.0, 100.0], [0.0, 3.0, 0.0], 200.0, 0.5, 1.5)
        r = _kor(p)
        np.testing.assert_array_equal(r.markfaktor_pg, np.ones(27))
        np.testing.assert_array_equal(r.delta_l_db, r.skarmeffekt_db)
        fri, _ = d4.skarmeffekt_fritt_falt_db(FREKVENSER, p, _form(p), ATM)
        np.testing.assert_allclose(fri, r.skarmeffekt_db, atol=1e-12)

    def test_mark_vars_forlangning_gar_over_skarmtoppen_bidrar_inte(self) -> None:
        # Mottagarsidans enda segment faller brant; dess förlängning passerar över T, så
        # h′_S2 < 0 och r_S2 = 0 (ekv. 176): w″ = 0 och sidan lämnar ingen reflex.
        p = _profil([0.0, 14.0, 15.0, 16.0, 26.0], [0.0, 0.0, 3.0, 0.0, -40.0], 200.0, 0.5, 0.5)
        r = d4.skarm_en_kant(FREKVENSER, p, Skarmform(1, 2, 3), ATM)
        self.assertTrue(bool(np.all(r.viktsumma_mottagare == 0.0)))
        self.assertTrue(bool(np.all(r.viktsumma_kalla > 0.9)))
        self.assertTrue(bool(np.all(np.isfinite(r.delta_l_db))))

    def test_turbulens_och_rahet_sanker_koherensen(self) -> None:
        p = _tunn_skarm(3.0, 20000.0, 20000.0)
        lugn = _kor(p)
        turb = d4.skarm_en_kant(
            FREKVENSER, p, _form(p), atmosfar_ur_vader(Vader(cv2=0.12, ct2=0.008))
        )
        ra = _profil([float(v) for v in p.x_m], [float(v) for v in p.z_m], 20000.0, 0.5, 1.5, 0.25)
        raa = _kor(ra)
        np.testing.assert_allclose(turb.skarmeffekt_db, lugn.skarmeffekt_db, atol=1e-12)
        # Dippar fylls ut: den lägsta nivån stiger.
        self.assertGreater(float(turb.delta_l_db[14:].min()), float(lugn.delta_l_db[14:].min()))
        self.assertGreater(float(raa.delta_l_db[14:].min()), float(lugn.delta_l_db[14:].min()))

    def test_spridningszonens_koherens(self) -> None:
        p = _tunn_skarm(3.0)
        np.testing.assert_allclose(
            _kor(p, koherens_spridningszon=1.0).delta_l_db, _kor(p).delta_l_db, atol=1e-12
        )
        inkoherent = _kor(p, koherens_spridningszon=0.0)
        self.assertTrue(bool(np.all(inkoherent.markfaktor_pg >= 1.0)))

    def test_energireflektion_for_kilytorna_bilaga_g5(self) -> None:
        p = _tunn_skarm(3.0, 200.0, 20000.0)
        q = kb.ekvivalent_reflektion_ur_energireflektion(0.25)
        r = _kor(p, q_kilyta_kalla=q, q_kilyta_mottagare=q)
        self.assertTrue(bool(np.all(r.q_av_kalla == 0.5)))
        self.assertFalse(bool(np.allclose(r.delta_l_db, _kor(p).delta_l_db, atol=1e-3)))

    def test_refraktion_och_fluktuerande_refraktion_ger_fel(self) -> None:
        p = _tunn_skarm(3.0)
        for vader in (Vader(vind_m_s=3.0), Vader(dt_dz_k_m=-0.1), Vader(std_vind_m_s=0.5)):
            with self.assertRaises(BullerFel):
                d4.skarm_en_kant(FREKVENSER, p, _form(p), atmosfar_ur_vader(vader))

    def test_ogiltig_indata_ger_fel(self) -> None:
        p = _tunn_skarm(3.0)
        with self.assertRaises(BullerFel):
            d4.skarm_en_kant(np.array([]), p, _form(p), ATM)
        with self.assertRaises(BullerFel):
            d4.skarm_en_kant(np.array([-1.0]), p, _form(p), ATM)
        with self.assertRaises(BullerFel):
            _kor(p, koherens_spridningszon=np.ones(3))
        with self.assertRaises(BullerFel):
            _kor(p, viktregel="annan")

    def test_resultatets_form_och_andliga_varden(self) -> None:
        r = _kor(_tunn_skarm(3.0))
        for falt in (r.delta_l_db, r.skarmeffekt_db, r.markfaktor_pg, r.q_av_kalla):
            self.assertEqual(falt.shape, (27,))
            self.assertTrue(bool(np.all(np.isfinite(falt))))
        self.assertTrue(bool(np.all(r.skarmeffekt_db < 0.0)))
        self.assertTrue(bool(np.all(np.diff(r.skarmeffekt_db) < 0.0)))


if __name__ == "__main__":
    unittest.main()
