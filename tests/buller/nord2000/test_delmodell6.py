"""Tester för delmodell 6 (AV 1106/07 avsnitt 5.15 med TC-101327 bilaga G.4).

Svaren följer av dokumentens egna definitioner (handräknad geometri, ekvationernas gränsfall),
av oberoende teori (spegelkällemetoden för hårda ytor, reciprocitet, kontinuitet, invarians vid
uppdelning av plan mark) eller av att delar ska sammanfalla med delmodell 4 och med
terrängtolkningen.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import numpy as np

from buller.nord2000 import delmodell4 as d4
from buller.nord2000 import delmodell6 as d6
from buller.nord2000 import delmodell6_kilben as kb6
from buller.nord2000 import delmodell7 as d7
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.kildiffraktion_dubbel import (
    tva_kanter_rata_stralar,
    tva_kilar_tryck_reflektionskoefficienter,
)
from buller.nord2000.refraktion import atmosfar_ur_vader
from buller.nord2000.terrang import terranggeometri
from buller.nord2000.terrang_skarmar import (
    Skarmform,
    gangvagsskillnad_mellan_m,
    tolka_terrang_homogen,
)
from buller.typer import BullerFel, Flyttal, Terrangprofil, Vader

INF = math.inf
FREKVENSER = mittfrekvenser_hz()
ATM = atmosfar_ur_vader(Vader())
C = ATM.c_m_s
FORSTA = Skarmform(1, 2, 3)
ANDRA = Skarmform(4, 5, 6)
TESTSVIT = Path(__file__).resolve().parents[3] / "testsvit" / "N_nord2000"


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
        flodesresistivitet_kNsm4=np.broadcast_to(
            np.asarray(sigma, dtype=np.float64), (n - 1,)
        ).copy(),
        rahet_m=np.broadcast_to(np.asarray(rahet, dtype=np.float64), (n - 1,)).copy(),
        kalla_hojd_m=h_s,
        mottagare_hojd_m=h_r,
    )


def _tva_tunna(
    h1: float,
    h2: float,
    sigma_mark: float = 200.0,
    sigma_skarm: float = 20000.0,
    h_s: float = 0.5,
    h_r: float = 1.5,
    x1: float = 15.0,
    x2: float = 80.0,
    d: float = 100.0,
) -> Terrangprofil:
    """Två tunna skärmar på plan mark; formerna är `FORSTA` och `ANDRA`."""
    sm, ss = sigma_mark, sigma_skarm
    return _profil(
        [0.0, x1 - 0.01, x1, x1 + 0.01, x2 - 0.01, x2, x2 + 0.01, d],
        [0.0, 0.0, h1, 0.0, 0.0, h2, 0.0, 0.0],
        [sm, ss, ss, sm, ss, ss, sm],
        h_s,
        h_r,
    )


def _en_tunn(h1: float, sigma_mark: float = 200.0, sigma_skarm: float = 20000.0) -> Terrangprofil:
    return _profil(
        [0.0, 14.99, 15.0, 15.01, 100.0],
        [0.0, 0.0, h1, 0.0, 0.0],
        [sigma_mark, sigma_skarm, sigma_skarm, sigma_mark],
        0.5,
        1.5,
    )


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


def _spegla_form(form: Skarmform, antal_punkter: int) -> Skarmform:
    n = antal_punkter - 1
    return Skarmform(n - form.index_w2, n - form.index_kant, n - form.index_w1)


def _kor(profil: Terrangprofil, viktigast: str = "forsta", **kw: object) -> d6.Delmodell6Resultat:
    return d6.tva_skarmar(FREKVENSER, profil, FORSTA, ANDRA, viktigast, ATM, **kw)  # type: ignore[arg-type]


Punkt = tuple[float, float]


def _vinkel_mellan(a: Punkt, b: Punkt) -> float:
    return math.acos((a[0] * b[0] + a[1] * b[1]) / (math.hypot(*a) * math.hypot(*b)))


def _kil_oberoende(t: Punkt, w1: Punkt, w2: Punkt, p_s: Punkt, p_r: Punkt) -> tuple[float, ...]:
    """β, θ_S och θ_R för en kil ur vektorgeometri (figur 9), utan arctan-formerna i ekv. 227.

    β är vinkeln utanför kilen, θ_R vinkeln från kilytan mot mottagaren till riktningen mot
    p_r, och θ_S = β − vinkeln från kilytan mot källan till riktningen mot p_s.
    """
    yta_s = (w1[0] - t[0], w1[1] - t[1])
    yta_r = (w2[0] - t[0], w2[1] - t[1])
    beta = 2.0 * math.pi - _vinkel_mellan(yta_s, yta_r)
    theta_r = _vinkel_mellan(yta_r, (p_r[0] - t[0], p_r[1] - t[1]))
    theta_s = beta - _vinkel_mellan(yta_s, (p_s[0] - t[0], p_s[1] - t[1]))
    return beta, theta_s, theta_r


def _avst(a: Punkt, b: Punkt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class TestKilytorTvaSkarmar(unittest.TestCase):
    """TC-101327 bilaga G.4."""

    def test_konstanten_delta_l0(self) -> None:
        # TC-101327 anger värdet 0,003820 för θ₀ = 5° (texten före ekv. 436).
        self.assertAlmostEqual(kb6.DELTA_L0_NORMERAD, 0.003820, places=6)

    def test_overgang_skarmbredd_ekv_425(self) -> None:
        self.assertEqual(kb6.overgang_skarmbredd(10.0, 100.0), 1.0)
        self.assertEqual(kb6.overgang_skarmbredd(20.0, 100.0), 1.0)
        self.assertAlmostEqual(kb6.overgang_skarmbredd(50.0, 100.0), 0.5, places=12)
        self.assertAlmostEqual(kb6.overgang_skarmbredd(65.0, 100.0), 0.25, places=12)
        self.assertEqual(kb6.overgang_skarmbredd(80.0, 100.0), 0.0)
        self.assertEqual(kb6.overgang_skarmbredd(95.0, 100.0), 0.0)
        with self.assertRaises(BullerFel):
            kb6.overgang_skarmbredd(0.0, 100.0)

    def test_normerad_omvag_vid_fem_grader_ar_delta_l0(self) -> None:
        # W₁₂ = W₂₁ mitt emellan topparna, 5° under linjen T₁T₂ (definitionen av Δl′₀).
        djup = 10.0 * math.tan(math.radians(5.0))
        dl = kb6.normerad_omvag(0.0, 3.0, 20.0, 3.0, 10.0, 3.0 - djup, 10.0, 3.0 - djup)
        self.assertAlmostEqual(dl, kb6.DELTA_L0_NORMERAD, places=12)
        self.assertAlmostEqual(kb6.overgang_form(dl), 0.0, places=9)

    def test_normerad_omvag_lutande_linje_och_punkter_over_linjen(self) -> None:
        # Punkter på linjen T₁T₂ ger ingen omväg; punkter över linjen räknas som på den.
        self.assertAlmostEqual(
            kb6.normerad_omvag(0.0, 2.0, 20.0, 4.0, 5.0, 2.5, 15.0, 3.5), 0.0, places=12
        )
        self.assertAlmostEqual(
            kb6.normerad_omvag(0.0, 2.0, 20.0, 4.0, 5.0, 9.0, 15.0, 8.0), 0.0, places=12
        )
        self.assertEqual(kb6.overgang_form(0.0), 1.0)
        # 3-4-5: W₁₂ 3 m under linjen, 4 m från T₁; W₂₁ likadant från T₂; l = 5 + 12 + 5 − 20.
        dl = kb6.normerad_omvag(0.0, 3.0, 20.0, 3.0, 4.0, 0.0, 16.0, 0.0)
        self.assertAlmostEqual(dl, 2.0 / 20.0, places=12)
        self.assertEqual(kb6.overgang_form(dl), 0.0)

    def test_modifierad_mittreflektion_ekv_438(self) -> None:
        self.assertEqual(kb6.modifierad_mittreflektion(0.7, 0.0), 0.7)
        self.assertEqual(kb6.modifierad_mittreflektion(0.7, 1.0), 0.5)
        self.assertAlmostEqual(kb6.modifierad_mittreflektion(0.8, 0.25), 0.725, places=12)
        with self.assertRaises(BullerFel):
            kb6.modifierad_mittreflektion(0.7, 1.5)

    def test_ytterbenen_ar_delmodell_4_och_mittbenen_slutar_i_andra_toppen(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        geo = terranggeometri(profil)
        ben = kb6.kilben_tva_skarmar(geo, FORSTA, ANDRA)
        self.assertEqual(ben[0], d4.kilben_ur_terrang(geo, FORSTA)[0])
        self.assertEqual(ben[3], d4.kilben_ur_terrang(geo, ANDRA)[1])
        # Mittbenen: kilytan (2 resp. 3 m lodrät vägg) och marken fram till den andra toppen.
        self.assertAlmostEqual(ben[1].d_w_m, math.hypot(0.01, 2.0), places=12)
        self.assertAlmostEqual(ben[2].d_w_m, math.hypot(0.01, 3.0), places=12)
        # Tunna skärmar: bara skärmytan ryms inom d_FZ, som i delmodell 4 (loggen P-4).
        for b in ben:
            self.assertEqual([s for s, _ in b.impedansvikter], [20000.0])

    def test_harda_ytor_ger_q_lika_med_ett_och_r_qm_noll(self) -> None:
        profil = _tva_tunna(2.0, 3.0, INF, INF)
        ytor = kb6.kilytornas_reflektion_tva_skarmar(
            FREKVENSER, terranggeometri(profil), FORSTA, ANDRA, C
        )
        self.assertEqual(ytor.r_qm, 0.0)  # r_QMtr = 0: djup dal mellan topparna
        for q in (ytor.q_1_kalla, ytor.q_1_mottagare, ytor.q_2_kalla, ytor.q_2_mottagare):
            np.testing.assert_array_equal(q, np.ones(27))

    def test_tatt_stallda_toppar_med_rak_mitt_gar_mot_tvakantsfallet(self) -> None:
        # Två toppar 2 m från varandra med 1 cm sänka: r₁₂ = 0,02, Δl′ ≈ 5·10⁻⁵ → r_QM ≈ 1,
        # så att Q′ → 0,5·Q_hard (ekv. 438), faktorn 0,5 i tvåkantsmodellen (ekv. 102).
        profil = _profil(
            [0.0, 19.0, 20.0, 21.0, 22.0, 23.0, 100.0],
            [0.0, 0.0, 3.0, 2.99, 3.0, 0.0, 0.0],
            200.0,
            0.5,
            1.5,
        )
        geo = terranggeometri(profil)
        forsta, andra = Skarmform(1, 2, 3), Skarmform(3, 4, 5)
        r_qm = kb6.overgangsparameter_rqm(geo, forsta, andra)
        dl = math.hypot(1.0, 0.01) * 2.0 / 2.0 - 1.0
        self.assertAlmostEqual(r_qm, 1.0 - dl / kb6.DELTA_L0_NORMERAD, places=12)
        ytor = kb6.kilytornas_reflektion_tva_skarmar(FREKVENSER, geo, forsta, andra, C)
        np.testing.assert_allclose(
            ytor.q_1_mottagare,
            (1.0 - r_qm) * ytor.q_1_mottagare_omodifierad + 0.5 * r_qm,
            rtol=0,
            atol=1e-14,
        )
        self.assertTrue(bool(np.all(np.abs(ytor.q_1_mottagare - 0.5) < 0.02)))
        self.assertTrue(bool(np.all(np.abs(ytor.q_2_kalla - 0.5) < 0.02)))

    def test_givna_varden_gar_fore_och_mittytorna_modifieras(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        ytor = kb6.kilytornas_reflektion_tva_skarmar(
            FREKVENSER,
            terranggeometri(profil),
            FORSTA,
            ANDRA,
            C,
            q_1_kalla=0.9,
            q_1_mottagare=0.8,
            q_2_kalla=np.full(27, 0.7),
            q_2_mottagare=0.6,
        )
        np.testing.assert_array_equal(ytor.q_1_kalla, np.full(27, 0.9))
        np.testing.assert_array_equal(ytor.q_1_mottagare, np.full(27, 0.8))  # r_QM = 0
        np.testing.assert_array_equal(ytor.q_2_kalla, np.full(27, 0.7))
        np.testing.assert_array_equal(ytor.q_2_mottagare, np.full(27, 0.6))
        with self.assertRaises(BullerFel):
            kb6.kilytornas_reflektion_tva_skarmar(
                FREKVENSER, terranggeometri(profil), FORSTA, ANDRA, C, q_1_kalla=np.ones(3)
            )

    def test_felaktiga_former_ger_bullerfel(self) -> None:
        geo = terranggeometri(_tva_tunna(2.0, 3.0))
        with self.assertRaises(BullerFel):
            kb6.kilben_tva_skarmar(geo, ANDRA, FORSTA)  # fel ordning
        with self.assertRaises(BullerFel):
            kb6.kilben_tva_skarmar(geo, Skarmform(1, 2, 5), Skarmform(4, 5, 6))  # går om varandra
        with self.assertRaises(BullerFel):
            kb6.kilben_tva_skarmar(geo, FORSTA, Skarmform(4, 5, 9))  # utanför profilen


class TestSkarmstralar(unittest.TestCase):
    """Ekv. (226)–(228) och (307)."""

    def test_vinklar_handraknade(self) -> None:
        # Två 45°-kilar med toppar i (10; 2) och (30; 2); S = (0; 1), R = (40; 1).
        profil = _profil(
            [0.0, 8.0, 10.0, 12.0, 28.0, 30.0, 32.0, 40.0],
            [0.0, 0.0, 2.0, 0.0, 0.0, 2.0, 0.0, 0.0],
            200.0,
            1.0,
            1.0,
        )
        st = d6.skarmstralar_tva(profil, FORSTA, ANDRA, ATM)
        for kil in (st.kil_1, st.kil_2):
            self.assertAlmostEqual(kil.beta_kallsida_rad, math.pi / 4.0, places=12)
            self.assertAlmostEqual(kil.beta_mottagarsida_rad, math.pi / 4.0, places=12)
            self.assertAlmostEqual(kil.beta_rad, 1.5 * math.pi, places=12)
        k = st.kanter
        lutning = math.atan(1.0 / 10.0)  # S och R ligger 1 m under topparna, 10 m bort
        vantat_1s = 2.0 * math.pi - (math.pi / 2.0 - lutning) - math.pi / 4.0
        self.assertAlmostEqual(k.theta_1s_rad, vantat_1s, places=12)
        self.assertAlmostEqual(k.theta_1r_rad, math.pi / 4.0, places=12)  # mot T₂, vågrätt
        self.assertAlmostEqual(k.theta_2s_rad, 1.25 * math.pi, places=12)  # mot T₁, vågrätt
        self.assertAlmostEqual(k.theta_2r_rad, math.pi / 4.0 - lutning, places=12)
        self.assertAlmostEqual(k.vag_kalla_m, math.sqrt(101.0), places=12)
        self.assertAlmostEqual(k.vag_mitten_m, 20.0, places=12)
        self.assertAlmostEqual(k.vag_mottagare_m, math.sqrt(101.0), places=12)
        self.assertAlmostEqual(k.gangtid_s, (20.0 + 2.0 * math.sqrt(101.0)) / C, places=14)
        self.assertTrue(st.andra_over_linjen_t1_r)
        self.assertTrue(st.forsta_over_linjen_t2_s)

    def test_vinklar_mot_oberoende_vektorgeometri(self) -> None:
        profil = _profil(
            [0.0, 9.0, 12.0, 13.5, 40.0, 61.0, 64.0, 66.0, 90.0],
            [0.2, 0.0, 3.0, 0.4, -0.2, 0.3, 4.0, 0.1, 0.5],
            200.0,
            0.7,
            2.0,
        )
        forsta, andra = Skarmform(1, 2, 3), Skarmform(5, 6, 7)
        st = d6.skarmstralar_tva(profil, forsta, andra, ATM)
        s, r = (0.0, 0.9), (90.0, 2.5)
        t1, t2 = (12.0, 3.0), (64.0, 4.0)
        b1, th1s, th1r = _kil_oberoende(t1, (9.0, 0.0), (13.5, 0.4), s, t2)
        b2, th2s, th2r = _kil_oberoende(t2, (61.0, 0.3), (66.0, 0.1), t1, r)
        k = st.kanter
        for varde, vantat in (
            (k.beta_1_rad, b1),
            (k.theta_1s_rad, th1s),
            (k.theta_1r_rad, th1r),
            (k.beta_2_rad, b2),
            (k.theta_2s_rad, th2s),
            (k.theta_2r_rad, th2r),
            (k.vag_kalla_m, _avst(s, t1)),
            (k.vag_mitten_m, _avst(t1, t2)),
            (k.vag_mottagare_m, _avst(t2, r)),
        ):
            self.assertAlmostEqual(varde, vantat, places=11)

    def test_gangvagsskillnad_ekv_307_ar_delta_l0_mot_grannkanten(self) -> None:
        # Räta strålar: Δℓ = |AT| + |TB| − |AB| med grannkanten i stället för S eller R
        # (avsnitt 5.21.2), även när kanten ligger under linjen (negativt tecken).
        for h1, h2 in ((2.0, 3.0), (3.0, 2.0), (3.0, 1.0), (0.4, 3.0), (3.0, 1.84)):
            profil = _tva_tunna(h1, h2)
            geo = terranggeometri(profil)
            st = d6.skarmstralar_tva(profil, FORSTA, ANDRA, ATM)
            s = (geo.x_s_m, geo.z_s_m)
            r = (geo.x_r_m, geo.z_r_m)
            vantat_1 = gangvagsskillnad_mellan_m(*s, 80.0, h2, 15.0, h1)
            vantat_2 = gangvagsskillnad_mellan_m(15.0, h1, *r, 80.0, h2)
            self.assertAlmostEqual(st.gangvagsskillnad_forsta_m, vantat_1, places=11)
            self.assertAlmostEqual(st.gangvagsskillnad_andra_m, vantat_2, places=11)

    def test_gangvagsskillnad_lika_med_terrangtolkningens_for_sekundar_skarm(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        t = tolka_terrang_homogen(terranggeometri(profil), FREKVENSER, C)
        assert t.sekundar is not None and t.primar is not None
        self.assertEqual((t.primar.index, t.sekundar.index), (5, 2))
        forsta, andra, viktigast = d6.skarmar_ur_tolkning(t)
        self.assertEqual((forsta, andra, viktigast), (FORSTA, ANDRA, "andra"))
        st = d6.skarmstralar_tva(profil, forsta, andra, ATM)
        self.assertAlmostEqual(
            st.gangvagsskillnad_forsta_m, t.sekundar.gangvagsskillnad_m, places=11
        )

    def test_andra_toppen_under_linjen_ger_delmodell_4_vinklar_for_forsta_kilen(self) -> None:
        # Avsnitt 5.7.3: T₂ under linjen T₁–R → θ_1R mäts mot R. Den första kilens vinklar är då
        # de som delmodell 4 räknar för samma skärm ensam, och den andra kilen är belyst.
        profil = _tva_tunna(3.0, 1.0)
        st = d6.skarmstralar_tva(profil, FORSTA, ANDRA, ATM)
        self.assertFalse(st.andra_over_linjen_t1_r)
        ensam = d4.skarmstralar(_en_tunn(3.0), Skarmform(1, 2, 3), ATM).kil
        self.assertAlmostEqual(st.kanter.beta_1_rad, ensam.beta_rad, places=12)
        self.assertAlmostEqual(st.kanter.theta_1s_rad, ensam.theta_s_rad, places=12)
        self.assertAlmostEqual(st.kanter.theta_1r_rad, ensam.theta_r_rad, places=12)
        self.assertLess(st.kanter.theta_2s_rad - st.kanter.theta_2r_rad, math.pi)
        self.assertLess(st.gangvagsskillnad_andra_m, 0.0)

    def test_vinkelregel_ekv_227_228_mater_alltid_mot_mottagare_och_kalla(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        geo = terranggeometri(profil)
        st = d6.skarmstralar_tva(profil, FORSTA, ANDRA, ATM, vinkelregel="ekv_227_228")
        s, r = (geo.x_s_m, geo.z_s_m), (geo.x_r_m, geo.z_r_m)
        _, _, th1r = _kil_oberoende((15.0, 2.0), (14.99, 0.0), (15.01, 0.0), s, r)
        _, th2s, _ = _kil_oberoende((80.0, 3.0), (79.99, 0.0), (80.01, 0.0), s, r)
        self.assertAlmostEqual(st.kanter.theta_1r_rad, th1r, places=11)
        self.assertAlmostEqual(st.kanter.theta_2s_rad, th2s, places=11)
        with self.assertRaises(BullerFel):
            d6.skarmstralar_tva(profil, FORSTA, ANDRA, ATM, vinkelregel="annan")  # type: ignore[arg-type]

    def test_refraktion_gransvarde_och_riktning(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        rak = d6.skarmstralar_tva(profil, FORSTA, ANDRA, ATM)
        for u in (0.01, -0.01):
            st = d6.skarmstralar_tva(profil, FORSTA, ANDRA, atmosfar_ur_vader(Vader(vind_m_s=u)))
            self.assertAlmostEqual(
                st.gangvagsskillnad_andra_m, rak.gangvagsskillnad_andra_m, delta=1e-3
            )
            self.assertAlmostEqual(st.kanter.vag_m, rak.kanter.vag_m, delta=1e-6)
        medvind = atmosfar_ur_vader(Vader(vind_m_s=3.0))
        motvind = atmosfar_ur_vader(Vader(vind_m_s=-3.0))
        niva_rak, _ = d6.skarmeffekt_fritt_falt_db(FREKVENSER, profil, FORSTA, ANDRA, "andra", ATM)
        niva_med, st_med = d6.skarmeffekt_fritt_falt_db(
            FREKVENSER, profil, FORSTA, ANDRA, "andra", medvind
        )
        niva_mot, st_mot = d6.skarmeffekt_fritt_falt_db(
            FREKVENSER, profil, FORSTA, ANDRA, "andra", motvind
        )
        # Nedåtbrytning minskar omvägen över kanterna och skärmverkan; uppåtbrytning tvärtom.
        self.assertGreater(st_med.vinkelandring_mitten_rad, 0.0)
        self.assertLess(st_mot.vinkelandring_mitten_rad, 0.0)
        self.assertLess(st_med.gangvagsskillnad_andra_m, rak.gangvagsskillnad_andra_m)
        self.assertGreater(st_mot.gangvagsskillnad_andra_m, rak.gangvagsskillnad_andra_m)
        self.assertLess(st_med.gangvagsskillnad_forsta_m, rak.gangvagsskillnad_forsta_m)
        self.assertTrue(bool(np.all(niva_med[8:] > niva_rak[8:])))
        self.assertTrue(bool(np.all(niva_mot[8:] < niva_rak[8:])))

    def test_refraktion_med_topp_under_linjen_matchar_delmodell4(self) -> None:
        profil = _tva_tunna(3.0, 1.0)
        for vind in (-3.0, 3.0):
            atm = atmosfar_ur_vader(Vader(vind_m_s=vind))
            st6 = d6.skarmstralar_tva(profil, FORSTA, ANDRA, atm)
            st4 = d4.skarmstralar(profil, FORSTA, atm)
            self.assertFalse(st6.andra_over_linjen_t1_r)
            self.assertAlmostEqual(st6.kanter.theta_1r_rad, st4.kil.theta_r_rad, places=12)
        # Speglad profil: T₁ under linjen T₂–S ger θ_2S lika med delmodell 4 för T₂.
        profil_speg = _tva_tunna(0.5, 3.0)
        for vind in (-3.0, 3.0):
            atm = atmosfar_ur_vader(Vader(vind_m_s=vind))
            st6 = d6.skarmstralar_tva(profil_speg, FORSTA, ANDRA, atm)
            st4 = d4.skarmstralar(profil_speg, ANDRA, atm)
            self.assertFalse(st6.forsta_over_linjen_t2_s)
            self.assertAlmostEqual(st6.kanter.theta_2s_rad, st4.kil.theta_s_rad, places=12)

    def test_skarmeffekt_fritt_falt_ar_resultatets_skarmdel(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        niva, _ = d6.skarmeffekt_fritt_falt_db(FREKVENSER, profil, FORSTA, ANDRA, "andra", ATM)
        np.testing.assert_allclose(niva, _kor(profil, "andra").skarmeffekt_db, rtol=0, atol=1e-12)

    def test_skarmar_ur_tolkning(self) -> None:
        # Den högre skärmen närmast källan blir primär: "forsta".
        t = tolka_terrang_homogen(terranggeometri(_tva_tunna(3.0, 2.5)), FREKVENSER, C)
        self.assertEqual(d6.skarmar_ur_tolkning(t), (FORSTA, ANDRA, "forsta"))
        en = tolka_terrang_homogen(terranggeometri(_en_tunn(3.0)), FREKVENSER, C)
        with self.assertRaises(BullerFel):
            d6.skarmar_ur_tolkning(en)


class TestMarkfaktorOchVikter(unittest.TestCase):
    """Ekv. (259) och (268)–(269)."""

    TRYCK = (
        1.0 + 0.5j, 0.3 - 0.2j, 0.25 + 0.1j, -0.2 + 0.05j,
        0.1 + 0.3j, -0.05 + 0.02j, 0.07 - 0.11j, 0.03 + 0.04j,
    )  # fmt: skip
    Q = (0.9 * np.exp(0.3j), 0.8 * np.exp(-0.7j), 0.7 * np.exp(1.1j))
    W = (0.9, 0.8, 0.6)
    INK = (0.95, 0.85, 0.75)

    def _q(self) -> tuple[complex, complex, complex]:
        return (complex(self.Q[0]), complex(self.Q[1]), complex(self.Q[2]))

    def test_full_koherens_ar_parentesen_i_ekv_224(self) -> None:
        p = self.TRYCK
        q1, q2, q3 = (w * q for w, q in zip(self.W, self._q(), strict=True))
        # Ekv. (224) utskriven term för term (Q̂₂ hör till marken mellan skärmarna).
        vantat = abs(
            1.0
            + q1 * p[1] / p[0]
            + q3 * p[2] / p[0]
            + q1 * q3 * p[3] / p[0]
            + q2 * p[4] / p[0]
            + q1 * q2 * p[5] / p[0]
            + q2 * q3 * p[6] / p[0]
            + q1 * q2 * q3 * p[7] / p[0]
        )
        pg = d6.markfaktor_tre_ytor(p, self._q(), self.W, (1.0,) * 7, self.INK)
        self.assertAlmostEqual(pg, vantat, places=13)

    def test_ingen_koherens_ar_energisumman(self) -> None:
        p = self.TRYCK
        i1, i2, i3 = (w * r for w, r in zip(self.W, self.INK, strict=True))
        faktorer = (i1, i3, i1 * i3, i2, i1 * i2, i2 * i3, i1 * i2 * i3)
        vantat = math.sqrt(1.0 + sum(abs(f * p[n + 1] / p[0]) ** 2 for n, f in enumerate(faktorer)))
        pg = d6.markfaktor_tre_ytor(p, self._q(), self.W, (0.0,) * 7, self.INK)
        self.assertAlmostEqual(pg, vantat, places=13)

    def test_delvis_koherens_term_for_term(self) -> None:
        p = self.TRYCK
        f = (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)
        k = [w * q for w, q in zip(self.W, self._q(), strict=True)]
        i = [w * r for w, r in zip(self.W, self.INK, strict=True)]
        koh = (k[0], k[2], k[0] * k[2], k[1], k[0] * k[1], k[1] * k[2], k[0] * k[1] * k[2])
        ink = (i[0], i[2], i[0] * i[2], i[1], i[0] * i[1], i[1] * i[2], i[0] * i[1] * i[2])
        summa = 1.0 + sum(f[n] * koh[n] * p[n + 1] / p[0] for n in range(7))
        energi = sum((1.0 - f[n] ** 2) * abs(ink[n] * p[n + 1] / p[0]) ** 2 for n in range(7))
        pg = d6.markfaktor_tre_ytor(p, self._q(), self.W, f, self.INK)
        self.assertAlmostEqual(pg, math.sqrt(abs(summa) ** 2 + energi), places=13)

    def test_utan_verksam_mark_ar_faktorn_ett(self) -> None:
        pg = d6.markfaktor_tre_ytor(self.TRYCK, self._q(), (0.0, 0.0, 0.0), (0.5,) * 7, self.INK)
        self.assertEqual(pg, 1.0)
        with self.assertRaises(BullerFel):
            d6.markfaktor_tre_ytor((0.0j,) * 8, self._q(), self.W, (1.0,) * 7, self.INK)

    def test_vikter_under_ett_normeras_och_w_q_ar_summan_i_kubik(self) -> None:
        (w1, w2, w3), w_q = d6.normerade_vikter_tre(([0.2, 0.3], [0.8], [0.25, 0.25, 0.5]))
        self.assertEqual(w1, [0.4, 0.6])
        self.assertEqual(w2, [1.0])
        self.assertEqual(w3, [0.25, 0.25, 0.5])
        self.assertAlmostEqual(w_q[0], 0.125, places=15)  # 0,5³ (ekv. 269; AV 1849/00 ekv. 88)
        self.assertAlmostEqual(w_q[1], 0.512, places=15)
        self.assertEqual(w_q[2], 1.0)

    def test_vikter_over_ett_enligt_av1849_och_som_tryckt(self) -> None:
        bis = ([0.9, 0.6], [0.7, 0.5], [0.5])
        (w1, w2, w3), w_q = d6.normerade_vikter_tre(bis)
        self.assertEqual((w1, w2, w3), ([0.9, 0.6], [0.7, 0.5], [1.0]))
        self.assertEqual(w_q, (1.0, 1.0, 0.125))
        # Tryckt: Δw_1t = 0,5, Δw_2t = 0,2, Δw_t = 0,7 → faktorerna 12/7 och 9/7.
        (t1, t2, t3), w_q_t = d6.normerade_vikter_tre(bis, "tryckt")
        self.assertAlmostEqual(sum(t1), 12.0 / 7.0, places=12)
        self.assertAlmostEqual(sum(t2), 9.0 / 7.0, places=12)
        self.assertAlmostEqual(t1[0] / t1[1], 1.5, places=12)
        self.assertEqual(t3, [1.0])
        self.assertEqual(w_q_t, w_q)

    def test_tryckt_form_har_sprang_i_viktsumman_ett(self) -> None:
        # Samma diskontinuitet som ekv. (187): Σw′₁ hoppar från 1 till 2 när w_1t passerar 1.
        under = ([0.5, 0.5], [1.0], [1.0])
        over = ([0.5, 0.5 + 1e-12], [1.0], [1.0])
        (u1, _, _), _ = d6.normerade_vikter_tre(under, "tryckt")
        (o1, _, _), _ = d6.normerade_vikter_tre(over, "tryckt")
        self.assertAlmostEqual(sum(u1), 1.0, places=12)
        self.assertAlmostEqual(sum(o1), 2.0, places=9)
        (a1, _, _), _ = d6.normerade_vikter_tre(over, "av1849")
        self.assertAlmostEqual(sum(a1), 1.0, places=9)

    def test_viktsumma_noll_och_okand_regel(self) -> None:
        for regel in ("av1849", "tryckt"):
            (w1, w2, _), w_q = d6.normerade_vikter_tre(([0.0, 0.0], [], [1.0]), regel)
            self.assertEqual((w1, w2), ([0.0, 0.0], []))
            self.assertEqual(w_q, (0.0, 0.0, 1.0))
        with self.assertRaises(BullerFel):
            d6.normerade_vikter_tre(([1.0], [1.0], [1.0]), "annan")  # type: ignore[arg-type]


def _spegelsumma(
    frekvens_hz: float,
    viktigast: str,
    vikt_q: tuple[float, float, float],
    h_s: float,
    h_r: float,
    h1: float,
    h2: float,
    med_mitten: bool = True,
) -> complex:
    """Åtta strålar över två tunna hårda skärmar på hård plan mark z = 0 (ekv. 222).

    Egen geometri: spegelpunkterna fås med z → −z och vinklarna ur `_kil_oberoende`.
    """
    s, r = (0.0, h_s), (100.0, h_r)
    t1, t2 = (15.0, h1), (80.0, h2)
    w = ((14.99, 0.0), (15.01, 0.0), (79.99, 0.0), (80.01, 0.0))

    def bild(p: Punkt) -> Punkt:
        return (p[0], -p[1])

    summa = 0.0j
    for a in (False, True):
        for m in (False, True):
            if m and not med_mitten:
                continue
            for b in (False, True):
                p_s = bild(s) if a else s
                p_r = bild(r) if b else r
                b1, th1s, th1r = _kil_oberoende(t1, w[0], w[1], p_s, bild(t2) if m else t2)
                b2, th2s, th2r = _kil_oberoende(t2, w[2], w[3], bild(t1) if m else t1, p_r)
                kanter = tva_kanter_rata_stralar(
                    b1, th1s, th1r, b2, th2s, th2r,
                    _avst(p_s, t1), _avst(t1, bild(t2) if m else t2), _avst(t2, p_r), C,
                )  # fmt: skip
                p = tva_kilar_tryck_reflektionskoefficienter(
                    frekvens_hz,
                    kanter,
                    1.0,
                    1.0,
                    1.0,
                    1.0,
                    viktigast,  # type: ignore[arg-type]
                )
                vikt = (vikt_q[0] if a else 1.0) * (vikt_q[1] if m else 1.0)
                summa += vikt * (vikt_q[2] if b else 1.0) * p
    return summa


class TestTvaSkarmar(unittest.TestCase):
    """Den allmänna modellen, ekv. (222)–(270)."""

    def test_harda_skarmar_pa_hard_mark_mot_dubbel_diffraktion_med_spegelkallor(self) -> None:
        profil = _tva_tunna(2.0, 3.0, INF, INF)
        for viktigast in ("forsta", "andra"):
            res = _kor(profil, viktigast, fullt_koherent=True)
            for nr, f in enumerate(FREKVENSER):
                vikt_q = tuple(
                    1.0 if w >= 1.0 else float(w) ** 3
                    for w in (
                        res.viktsumma_kalla[nr],
                        res.viktsumma_mitten[nr],
                        res.viktsumma_mottagare[nr],
                    )
                )
                p = _spegelsumma(float(f), viktigast, vikt_q, 0.5, 1.5, 2.0, 3.0)  # type: ignore[arg-type]
                vantat = 20.0 * math.log10(abs(p) * math.hypot(100.0, 1.0))
                self.assertAlmostEqual(float(res.delta_l_db[nr]), vantat, places=8)
            # Marken täcker Fresnelzonerna utom vid de lägsta frekvenserna på källsidan.
            self.assertTrue(bool(np.all(res.viktsumma_mitten[1:] == 1.0)))
            self.assertTrue(bool(np.all(res.viktsumma_kalla > 0.9)))

    def test_ingen_mark_mellan_skarmarna_ger_fyra_stralar(self) -> None:
        # W₁₂ = W₂₁: två hårda triangelvallar som möts i en punkt. Området mellan skärmarna
        # saknar segment, så strålarna 5–8 faller bort (AV 1849/00 ekv. 86: neutral sida).
        # Sänkan ligger 4,4° under linjen T₁T₂ (< 5°), så r_QM > 0 och mittytorna får
        # Q′ = 1 − r_QM/2 (TC-101327 ekv. 438): r_QMtr = 1 − Δl′/Δl′₀, r_QM,2edge ≈ 0,25.
        profil = _profil(
            [0.0, 14.99, 15.0, 47.5, 80.0, 80.01, 100.0],
            [0.0, 0.0, 2.0, 0.0, 3.0, 0.0, 0.0],
            INF,
            0.5,
            1.5,
        )
        forsta, andra = Skarmform(1, 2, 3), Skarmform(3, 4, 5)
        res = d6.tva_skarmar(FREKVENSER, profil, forsta, andra, "andra", ATM, fullt_koherent=True)
        np.testing.assert_array_equal(res.viktsumma_mitten, np.zeros(27))
        dl = (2.0 * math.hypot(32.5, 2.5) - 65.0) / 65.0
        bredd = 1.0 - (math.hypot(65.0, 1.0) / 100.0 - 0.2) / 0.6  # ekv. (422)–(425)
        r_qm = (1.0 - dl / kb6.DELTA_L0_NORMERAD) * bredd
        self.assertAlmostEqual(res.kilytor.r_qm, r_qm, places=12)
        q_mitt = 1.0 - 0.5 * r_qm
        s, r, t1, t2 = (0.0, 0.5), (100.0, 1.5), (15.0, 2.0), (80.0, 3.0)
        for nr, f in enumerate(FREKVENSER):
            w1 = float(res.viktsumma_kalla[nr])
            w3 = float(res.viktsumma_mottagare[nr])
            q1 = 1.0 if w1 >= 1.0 else w1**3
            q3 = 1.0 if w3 >= 1.0 else w3**3
            summa = 0.0j
            for a in (False, True):
                for b in (False, True):
                    p_s = (0.0, -0.5) if a else s
                    p_r = (100.0, -1.5) if b else r
                    b1, th1s, th1r = _kil_oberoende(t1, (14.99, 0.0), (47.5, 0.0), p_s, t2)
                    b2, th2s, th2r = _kil_oberoende(t2, (47.5, 0.0), (80.01, 0.0), t1, p_r)
                    kanter = tva_kanter_rata_stralar(
                        b1, th1s, th1r, b2, th2s, th2r,
                        _avst(p_s, t1), _avst(t1, t2), _avst(t2, p_r), C,
                    )  # fmt: skip
                    summa += (
                        (q1 if a else 1.0)
                        * (q3 if b else 1.0)
                        * tva_kilar_tryck_reflektionskoefficienter(
                            float(f), kanter, 1.0, q_mitt, q_mitt, 1.0, "andra"
                        )
                    )
            vantat = 20.0 * math.log10(abs(summa) * math.hypot(100.0, 1.0))
            self.assertAlmostEqual(float(res.delta_l_db[nr]), vantat, places=8)

    def test_reciprocitet_vid_speglad_profil(self) -> None:
        rng = np.random.default_rng(6)
        atm = atmosfar_ur_vader(Vader(cv2=0.12, ct2=0.008))
        storst = 0.0
        for _ in range(8):
            x1, x2 = float(rng.uniform(8.0, 30.0)), float(rng.uniform(50.0, 85.0))
            xs = sorted(float(v) for v in rng.uniform(1.0, x1 - 2.0, 2))
            xm = sorted(float(v) for v in rng.uniform(x1 + 2.0, x2 - 2.0, 2))
            xr = sorted(float(v) for v in rng.uniform(x2 + 2.0, 99.0, 2))
            b = [float(v) for v in rng.uniform(0.05, 1.5, 4)]
            x = [0.0, *xs, x1 - b[0], x1, x1 + b[1], *xm, x2 - b[2], x2, x2 + b[3], *xr, 100.0]
            z = [float(v) for v in rng.uniform(-0.3, 0.3, len(x))]
            z[4], z[9] = float(rng.uniform(2.0, 5.0)), float(rng.uniform(2.0, 5.0))
            sigma = [float(v) for v in rng.choice([12.5, 200.0, 2000.0, 20000.0], len(x) - 1)]
            rahet = [float(v) for v in rng.choice([0.0, 0.05, 0.25], len(x) - 1)]
            profil = _profil(
                x, z, sigma, float(rng.uniform(0.3, 2.0)), float(rng.uniform(1.0, 4.0)), rahet
            )
            forsta, andra = Skarmform(3, 4, 5), Skarmform(8, 9, 10)
            for viktigast, omvant in (("forsta", "andra"), ("andra", "forsta")):
                fram = d6.tva_skarmar(FREKVENSER, profil, forsta, andra, viktigast, atm)  # type: ignore[arg-type]
                bak = d6.tva_skarmar(
                    FREKVENSER,
                    _spegla(profil),
                    _spegla_form(andra, len(x)),
                    _spegla_form(forsta, len(x)),
                    omvant,  # type: ignore[arg-type]
                    atm,
                )
                storst = max(storst, float(np.max(np.abs(fram.delta_l_db - bak.delta_l_db))))
                np.testing.assert_allclose(fram.markfaktor_pg, bak.markfaktor_pg, rtol=1e-8)
                np.testing.assert_allclose(
                    fram.kilytor.q_1_kalla, bak.kilytor.q_2_mottagare, rtol=0, atol=1e-12
                )
                np.testing.assert_allclose(
                    fram.kilytor.q_1_mottagare, bak.kilytor.q_2_kalla, rtol=0, atol=1e-12
                )
                np.testing.assert_allclose(
                    fram.viktsumma_kalla, bak.viktsumma_mottagare, rtol=0, atol=1e-10
                )
        self.assertLess(storst, 1e-8)

    def test_symmetrisk_uppstallning_ar_oberoende_av_viktigaste_skarm(self) -> None:
        profil = _profil(
            [0.0, 14.99, 15.0, 15.01, 84.99, 85.0, 85.01, 100.0],
            [0.0, 0.0, 3.0, 0.0, 0.0, 3.0, 0.0, 0.0],
            [200.0, 20000.0, 20000.0, 200.0, 20000.0, 20000.0, 200.0],
            1.0,
            1.0,
        )
        a, b = _kor(profil, "forsta"), _kor(profil, "andra")
        np.testing.assert_allclose(a.delta_l_db, b.delta_l_db, rtol=0, atol=1e-9)

    def test_kontinuitet_nar_andra_toppen_passerar_linjen_t1_r(self) -> None:
        # Bytet av mätriktning för θ_1R sker på linjen T₁–R (3 − 1,5·65/85 = 1,853 m i x = 80).
        pa_linjen = 3.0 - 1.5 * 65.0 / 85.0
        for eps, grans_db in ((1e-9, 1e-6), (1e-6, 0.005)):
            under = _kor(_tva_tunna(3.0, pa_linjen - eps))
            over = _kor(_tva_tunna(3.0, pa_linjen + eps))
            self.assertFalse(under.stralar.andra_over_linjen_t1_r)
            self.assertTrue(over.stralar.andra_over_linjen_t1_r)
            skillnad = float(np.max(np.abs(under.delta_l_db - over.delta_l_db)))
            self.assertLess(skillnad, grans_db)

    def test_andra_toppen_pa_linjen_halverar_faltet_fran_forsta_skarmen(self) -> None:
        # På skugggränsen ger den andra kilen halva fältet (−6,02 dB) vid höga frekvenser,
        # relativt den första skärmen ensam (delmodell 4:s skärmdel).
        pa_linjen = 3.0 - 1.5 * 65.0 / 85.0
        res = _kor(_tva_tunna(3.0, pa_linjen))
        ensam = d4.skarm_en_kant(FREKVENSER, _en_tunn(3.0), Skarmform(1, 2, 3), ATM)
        skillnad = res.skarmeffekt_db - ensam.skarmeffekt_db
        self.assertTrue(bool(np.all(np.abs(skillnad[16:] + 6.02) < 0.2)))
        self.assertTrue(bool(np.all(np.diff(skillnad) < 0.0)))  # närmar sig −6 dB jämnt

    def test_plan_mark_uppdelad_i_flera_segment_ger_samma_resultat(self) -> None:
        hel = _tva_tunna(2.0, 3.0)
        delad = _profil(
            [0.0, 4.0, 9.0, 14.99, 15.0, 15.01, 30.0, 55.0, 79.99, 80.0, 80.01, 90.0, 100.0],
            [0.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0],
            [200.0] * 3 + [20000.0] * 2 + [200.0] * 3 + [20000.0] * 2 + [200.0] * 2,
            0.5,
            1.5,
        )
        a = _kor(hel, "andra")
        b = d6.tva_skarmar(FREKVENSER, delad, Skarmform(3, 4, 5), Skarmform(8, 9, 10), "andra", ATM)
        np.testing.assert_allclose(a.delta_l_db, b.delta_l_db, rtol=0, atol=1e-7)
        np.testing.assert_allclose(a.viktsumma_mitten, b.viktsumma_mitten, rtol=0, atol=1e-9)

    def test_resultatets_delar_hanger_ihop(self) -> None:
        res = _kor(_tva_tunna(2.0, 3.0), "andra")
        np.testing.assert_allclose(
            res.delta_l_db,
            res.skarmeffekt_db + 20.0 * np.log10(res.markfaktor_pg),
            rtol=0,
            atol=1e-10,
        )
        self.assertEqual(res.viktigast, "andra")
        self.assertEqual(res.delta_l_db.shape, (27,))
        self.assertTrue(bool(np.all(np.isfinite(res.delta_l_db))))

    def test_utan_koherens_ar_markfaktorn_minst_ett(self) -> None:
        # F_s = 0 ger F₂ … F₈ = 0: |p_G|² = 1 + Σ|…|² ≥ 1 (ekv. 259).
        res = _kor(_tva_tunna(2.0, 3.0), koherens_spridningszon=0.0)
        self.assertTrue(bool(np.all(res.markfaktor_pg >= 1.0)))
        koherent = _kor(_tva_tunna(2.0, 3.0))
        self.assertTrue(bool(np.any(koherent.markfaktor_pg < 1.0)))  # markdippen finns annars

    def test_turbulens_och_rahet_andrar_bara_markdelen(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        grov = _profil(
            [float(v) for v in profil.x_m],
            [float(v) for v in profil.z_m],
            [float(v) for v in profil.flodesresistivitet_kNsm4],
            0.5,
            1.5,
            0.25,
        )
        lugn = _kor(profil)
        atm = atmosfar_ur_vader(Vader(cv2=0.12, ct2=0.008))
        turbulent = d6.tva_skarmar(FREKVENSER, profil, FORSTA, ANDRA, "forsta", atm)
        for annan in (turbulent, _kor(grov)):
            np.testing.assert_allclose(
                annan.skarmeffekt_db, lugn.skarmeffekt_db, rtol=0, atol=1e-12
            )
            self.assertGreater(
                float(np.max(np.abs(annan.markfaktor_pg - lugn.markfaktor_pg))), 0.01
            )

    def test_givna_kilytor_bilaga_g5(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        absorberande = _kor(
            profil, q_1_kalla=0.3, q_1_mottagare=0.3, q_2_kalla=0.3, q_2_mottagare=0.3
        )
        np.testing.assert_array_equal(absorberande.kilytor.q_2_mottagare, np.full(27, 0.3))
        self.assertTrue(bool(np.all(absorberande.skarmeffekt_db < _kor(profil).skarmeffekt_db)))

    def test_markfaktorn_gar_till_delmodell_7(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        geo = terranggeometri(profil)
        res = _kor(profil, "andra")
        dl7 = d7.turbulensspridning_tva_kanter_db(
            FREKVENSER, geo.x_s_m, geo.z_s_m, geo.x_r_m, geo.z_r_m,
            15.0, 2.0, 80.0, 3.0, 0.12, 0.008, 15.0, markfaktor_pg=res.markfaktor_pg,
        )  # fmt: skip
        self.assertEqual(dl7.shape, (27,))
        self.assertTrue(bool(np.all(np.isfinite(dl7))))

    def test_viktregel_tryckt_fordubblar_exponenterna_over_ett(self) -> None:
        # Plan mark i två segment på källsidan där båda speglar: w_1t > 1 i en dal. Här räcker
        # det att visa regeln på en profil där summan överstiger 1 på riktigt.
        profil = _profil(
            [0.0, 7.0, 14.99, 15.0, 15.01, 79.99, 80.0, 80.01, 100.0],
            [0.0, -1.0, 0.0, 2.0, 0.0, 0.0, 3.0, 0.0, 0.0],
            [200.0, 200.0, 20000.0, 20000.0, 200.0, 20000.0, 20000.0, 200.0],
            1.0,
            1.5,
        )
        forsta, andra = Skarmform(2, 3, 4), Skarmform(5, 6, 7)
        valt = d6.tva_skarmar(FREKVENSER, profil, forsta, andra, "andra", ATM)
        tryckt = d6.tva_skarmar(FREKVENSER, profil, forsta, andra, "andra", ATM, viktregel="tryckt")
        over = valt.viktsumma_kalla > 1.0 + 1e-6
        self.assertTrue(bool(np.any(over)))
        andra_under = (valt.viktsumma_mitten <= 1.0) & (valt.viktsumma_mottagare <= 1.0)
        for nr in np.flatnonzero(over & andra_under):
            # Bara källsidan över 1: Δw_1t/Δw_t = 1, så exponenterna blir 2/w_1t gånger valets.
            kvot = math.log(float(tryckt.markfaktor_pg[nr])) / math.log(
                float(valt.markfaktor_pg[nr])
            )
            self.assertAlmostEqual(kvot, 2.0 / float(valt.viktsumma_kalla[nr]), places=6)
        np.testing.assert_array_equal(tryckt.skarmeffekt_db, valt.skarmeffekt_db)

    def test_svag_refraktion_gar_mot_rata_stralar(self) -> None:
        """Markdelen med krökta strålar (P-S2, ekv. 231–258) återger räta strålar när ξ = 0."""
        profil = _tva_tunna(2.0, 3.0)
        rak = _kor(profil)
        for vader in (Vader(vind_m_s=1e-6), Vader(vind_m_s=-1e-6), Vader(std_vind_m_s=1e-9)):
            krokt = d6.tva_skarmar(
                FREKVENSER, profil, FORSTA, ANDRA, "forsta", atmosfar_ur_vader(vader)
            )
            np.testing.assert_allclose(krokt.delta_l_db, rak.delta_l_db, rtol=0, atol=1e-9)

    def test_refraktion_och_fluktuerande_refraktion_raknas(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        rak = _kor(profil)
        fluktuerande = d6.tva_skarmar(
            FREKVENSER, profil, FORSTA, ANDRA, "forsta", atmosfar_ur_vader(Vader(std_vind_m_s=0.5))
        )
        np.testing.assert_array_equal(fluktuerande.skarmeffekt_db, rak.skarmeffekt_db)
        self.assertGreater(float(np.max(np.abs(fluktuerande.delta_l_db - rak.delta_l_db))), 0.01)
        for vind in (3.0, -3.0):
            r = d6.tva_skarmar(
                FREKVENSER, profil, FORSTA, ANDRA, "forsta", atmosfar_ur_vader(Vader(vind_m_s=vind))
            )
            self.assertTrue(bool(np.all(np.isfinite(r.delta_l_db))))
            self.assertTrue(bool(np.all(r.markfaktor_pg > 0.0)))

    def test_bullerfel(self) -> None:
        profil = _tva_tunna(2.0, 3.0)
        with self.assertRaises(BullerFel):
            d6.tva_skarmar(FREKVENSER, profil, ANDRA, FORSTA, "forsta", ATM)
        with self.assertRaises(BullerFel):
            _kor(profil, "mitten")
        with self.assertRaises(BullerFel):
            _kor(profil, viktregel="annan")
        with self.assertRaises(BullerFel):
            _kor(profil, vinkelregel="annan")
        with self.assertRaises(BullerFel):
            _kor(profil, koherens_spridningszon=np.ones(5))
        with self.assertRaises(BullerFel):
            d6.tva_skarmar(np.array([]), profil, FORSTA, ANDRA, "forsta", ATM)
        with self.assertRaises(BullerFel):
            d6.tva_skarmar(np.array([-1.0]), profil, FORSTA, ANDRA, "forsta", ATM)
        # Andra "skärmen" i en grop under baslinjen kan inte vara kant.
        grop = _profil(
            [0.0, 14.99, 15.0, 15.01, 70.0, 80.0, 90.0, 100.0],
            [0.0, 0.0, 3.0, 0.0, -4.0, -2.0, -4.0, 0.0],
            200.0,
            0.5,
            1.5,
        )
        with self.assertRaises(BullerFel):
            d6.tva_skarmar(FREKVENSER, grop, FORSTA, ANDRA, "forsta", ATM)

    @unittest.skipUnless((TESTSVIT / "N18R091_rak_vag").is_dir(), "testsviten saknas")
    def test_testfallens_profiler_gar_att_rakna_i_homogen_atmosfar(self) -> None:
        # Blad 91 och 94 (två skärmar). Bara att kedjan terrängtolkning → delmodell 6 går
        # igenom och att primärskärmen byter med källhöjden (loggen P-T); inget facit.
        from buller.testfall.format import las_fall

        vantat = {(91, 0.01): "forsta", (91, 0.75): "andra", (94, 0.01): "forsta"}
        for (blad, h_s), viktigast_vantat in vantat.items():
            profil = las_fall(TESTSVIT / f"N18R0{blad}_rak_vag").profil(h_s)
            tolkning = tolka_terrang_homogen(terranggeometri(profil), FREKVENSER, C)
            self.assertIn(6, tolkning.delmodeller)
            forsta, andra, viktigast = d6.skarmar_ur_tolkning(tolkning)
            self.assertEqual(viktigast, viktigast_vantat)
            res: Flyttal = d6.tva_skarmar(
                FREKVENSER, profil, forsta, andra, viktigast, ATM
            ).delta_l_db
            self.assertTrue(bool(np.all(np.isfinite(res))))


if __name__ == "__main__":
    unittest.main()
