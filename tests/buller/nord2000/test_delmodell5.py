"""Tester för delmodell 5 (AV 1106/07 avsnitt 5.14 med TC-101327 bilaga G.3).

Svaren följer av dokumentens egna definitioner (handräknad geometri), av oberoende teori
(spegelkällemetoden för hårda ytor, reciprocitet, spegelsymmetri) eller av gränsfall. Några
tester håller fast uppmätta egenskaper hos metoden som den som kopplar in delmodellen måste
känna till (ΔL₅ går inte själv mot ΔL₄; nivån hoppar när den viktigaste kanten byts); siffrorna
står i `docs/buller/logg/P-5.md`.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import numpy as np

from buller.nord2000 import delmodell4 as d4
from buller.nord2000 import delmodell4_kilben as kb
from buller.nord2000 import delmodell5 as d5
from buller.nord2000 import delmodell7 as d7
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.kildiffraktion_dubbel import (
    tjock_skarm_tryck_reflektionskoefficienter,
    tva_kanter_rata_stralar,
)
from buller.nord2000.refraktion import atmosfar_ur_vader
from buller.nord2000.terrang import gangvagsskillnad_m, terranggeometri
from buller.nord2000.terrang_skarmar import Skarmform, tolka_terrang_homogen, valj_skarmkanter
from buller.typer import BullerFel, Terrangprofil, Vader

INF = math.inf
TVA_PI = 2.0 * math.pi
FREKVENSER = mittfrekvenser_hz()
ATM = atmosfar_ur_vader(Vader())
C = ATM.c_m_s
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
        flodesresistivitet_kNsm4=np.array(
            np.broadcast_to(np.asarray(sigma, dtype=np.float64), (n - 1,))
        ),
        rahet_m=np.array(np.broadcast_to(np.asarray(rahet, dtype=np.float64), (n - 1,))),
        kalla_hojd_m=h_s,
        mottagare_hojd_m=h_r,
    )


def _tjock_skarm(
    x1: float = 15.0,
    bredd: float = 2.0,
    hojd: float = 3.0,
    sigma_mark: float = 200.0,
    sigma_skarm: float = 20000.0,
    h_s: float = 0.5,
    h_r: float = 1.5,
    d: float = 100.0,
    hojd_2: float | None = None,
) -> Terrangprofil:
    """Tjock skärm med nästan lodräta sidor (fötterna 1 cm ut), punkterna 1–4 = W₁, T₁, T₂, W₂."""
    return _profil(
        [0.0, x1 - 0.01, x1, x1 + bredd, x1 + bredd + 0.01, d],
        [0.0, 0.0, hojd, hojd if hojd_2 is None else hojd_2, 0.0, 0.0],
        [sigma_mark, sigma_skarm, sigma_skarm, sigma_skarm, sigma_mark],
        h_s,
        h_r,
    )


FORM_T1 = Skarmform(index_w1=1, index_kant=2, index_w2=4)  # primär kant närmast källan
FORM_T2 = Skarmform(index_w1=1, index_kant=3, index_w2=4)  # primär kant närmast mottagaren


def _kor(
    profil: Terrangprofil, form: Skarmform = FORM_T1, sekundar: int = 3, **kw: object
) -> d5.Delmodell5Resultat:
    return d5.skarm_tva_kanter(FREKVENSER, profil, form, sekundar, ATM, **kw)  # type: ignore[arg-type]


def _spegla(profil: Terrangprofil) -> Terrangprofil:
    d = float(profil.x_m[-1])
    return Terrangprofil(
        x_m=(d - profil.x_m)[::-1].copy(),
        z_m=profil.z_m[::-1].copy(),
        flodesresistivitet_kNsm4=profil.flodesresistivitet_kNsm4[::-1].copy(),
        rahet_m=profil.rahet_m[::-1].copy(),
        kalla_hojd_m=profil.mottagare_hojd_m,
        mottagare_hojd_m=profil.kalla_hojd_m,
    )


class TestTvakantsform(unittest.TestCase):
    """Kanternas ordning och valet av viktigaste kant (5.14.2, 5.7.4, 5.21.3)."""

    def test_primar_kant_narmast_kallan_ger_forsta(self) -> None:
        form = d5.tvakantsform(FORM_T1, 3)
        self.assertEqual((form.index_w1, form.index_t1, form.index_t2, form.index_w2), (1, 2, 3, 4))
        self.assertEqual(form.viktigast, "forsta")

    def test_primar_kant_narmast_mottagaren_ger_andra(self) -> None:
        form = d5.tvakantsform(FORM_T2, 2)
        self.assertEqual((form.index_t1, form.index_t2), (2, 3))
        self.assertEqual(form.viktigast, "andra")

    def test_sekundar_kant_utanfor_formen_eller_lika_med_primar_ger_fel(self) -> None:
        for index in (1, 2, 4, 5):
            with self.assertRaises(BullerFel):
                d5.tvakantsform(FORM_T1, index)

    def test_terrangtolkningens_val_for_tjock_skarm(self) -> None:
        # Källan nära skärmen: kanten närmast källan har störst Δℓ₀ och är primär.
        kanter = valj_skarmkanter(terranggeometri(_tjock_skarm()))
        assert kanter.primar_form is not None and kanter.sekundar_kant is not None
        form = d5.tvakantsform(kanter.primar_form, kanter.sekundar_kant.index)
        self.assertEqual((form.index_t1, form.index_t2, form.viktigast), (2, 3, "forsta"))


class TestVinklar(unittest.TestCase):
    """Ekv. (194)–(195) mot handräknad geometri."""

    def _trapets(self, h_s: float, h_r: float) -> d5.Tvakantsvinklar:
        # W₁ (10; 0), T₁ (13; 3), T₂ (17; 3), W₂ (20; 0): 45° sidor, vågrät ovansida.
        profil = _profil([0.0, 10.0, 13.0, 17.0, 20.0, 40.0], [0, 0, 3, 3, 0, 0], INF, h_s, h_r)
        return d5.tvakantsvinklar(terranggeometri(profil), d5.Tvakantsform(1, 2, 3, 4, "forsta"))

    def test_kilvinklar_for_trapets(self) -> None:
        v = self._trapets(1.0, 1.0)
        self.assertAlmostEqual(v.beta11_rad, math.pi / 4.0, places=12)
        self.assertAlmostEqual(v.beta12_rad, math.pi / 2.0, places=12)
        self.assertAlmostEqual(v.beta21_rad, math.pi / 2.0, places=12)
        self.assertAlmostEqual(v.beta22_rad, math.pi / 4.0, places=12)
        self.assertAlmostEqual(v.beta1_rad, 1.25 * math.pi, places=12)
        self.assertAlmostEqual(v.beta2_rad, 1.25 * math.pi, places=12)

    def test_lag_kalla_och_mottagare_mats_mot_den_andra_kanten(self) -> None:
        # T₂ över linjen T₁–R ger θ_1R = 0; T₁ över linjen T₂–S ger θ_2S = β₂ (5.7.4).
        v = self._trapets(1.0, 1.0)
        self.assertEqual(v.theta_1r_rad, 0.0)
        self.assertEqual(v.theta_2s_rad, v.beta2_rad)
        # θ_1S: från ovansidan (riktning +x) moturs till riktningen T₁ → S = (−13; −2).
        self.assertAlmostEqual(v.theta_1s_rad, math.atan2(-2.0, -13.0) % TVA_PI, places=12)
        # θ_2R: från mottagarsidans yta (riktning (3; −3)) moturs till T₂ → R = (23; −2).
        self.assertAlmostEqual(
            v.theta_2r_rad, math.atan2(-2.0, 23.0) - math.atan2(-3.0, 3.0), places=12
        )

    def test_hog_mottagare_ser_forsta_kanten_over_den_andra(self) -> None:
        v = self._trapets(1.0, 9.0)  # R = (40; 9): linjen T₁–R går över T₂
        self.assertAlmostEqual(v.theta_1r_rad, math.atan2(6.0, 27.0), places=12)
        self.assertEqual(v.theta_2s_rad, v.beta2_rad)

    def test_hog_kalla_ser_andra_kanten_over_den_forsta(self) -> None:
        v = self._trapets(9.0, 1.0)  # S = (0; 9): linjen T₂–S går över T₁
        self.assertEqual(v.theta_1r_rad, 0.0)
        self.assertAlmostEqual(v.theta_2s_rad, v.beta2_rad - math.atan2(6.0, 17.0), places=12)

    def test_punkter_inne_i_formen_andrar_inte_vinklarna(self) -> None:
        enkel = _profil([0.0, 10.0, 13.0, 17.0, 20.0, 40.0], [0, 0, 3, 3, 0, 0], INF, 1.0, 1.0)
        fler = _profil(
            [0.0, 10.0, 11.0, 13.0, 15.0, 17.0, 19.0, 20.0, 40.0],
            [0, 0, 1.2, 3, 3.1, 3, 0.8, 0, 0],
            INF,
            1.0,
            1.0,
        )
        v1 = d5.tvakantsvinklar(terranggeometri(enkel), d5.Tvakantsform(1, 2, 3, 4, "forsta"))
        v2 = d5.tvakantsvinklar(terranggeometri(fler), d5.Tvakantsform(1, 3, 5, 7, "forsta"))
        self.assertEqual(v1, v2)

    def test_ogiltig_ordning_ger_fel(self) -> None:
        geo = terranggeometri(_tjock_skarm())
        for index in ((1, 3, 2, 4), (2, 2, 3, 4), (1, 2, 3, 9), (1, 2, 2, 4)):
            with self.assertRaises(BullerFel):
                d5.tvakantsvinklar(geo, d5.Tvakantsform(*index, "forsta"))

    def test_stralar_ar_de_rata_strackorna(self) -> None:
        profil = _tjock_skarm(x1=15.0, bredd=2.0, hojd=3.0, h_s=0.5, h_r=1.5)
        s = d5.skarmstralar_tva_kanter(profil, d5.Tvakantsform(1, 2, 3, 4, "forsta"), ATM)
        self.assertAlmostEqual(s.kanter.vag_kalla_m, math.hypot(15.0, 2.5), places=12)
        self.assertAlmostEqual(s.kanter.vag_mitten_m, 2.0, places=12)
        self.assertAlmostEqual(s.kanter.vag_mottagare_m, math.hypot(83.0, 1.5), places=12)
        self.assertAlmostEqual(s.kanter.gangtid_s, s.kanter.vag_m / C, places=15)
        self.assertAlmostEqual(s.hojd_over_baslinje_1_m, 3.0, places=12)

    def test_kant_under_baslinjen_ger_fel(self) -> None:
        # Lutande baslinje (mottagarens mark 10 m upp): T₂ hamnar under baslinjen.
        profil = _profil(
            [0.0, 4.99, 5.0, 60.0, 60.01, 100.0], [0, 0, 3, 3, 2.9, 10.0], 200.0, 0.5, 1.5
        )
        with self.assertRaises(BullerFel):
            d5.skarmstralar_tva_kanter(profil, d5.Tvakantsform(1, 2, 3, 4, "forsta"), ATM)


class TestBilagaG3(unittest.TestCase):
    """TC-101327 bilaga G.3: Q_avS, Q′_avM, Q_avR (ekv. 422–426)."""

    def test_relativ_skarmbredd_ekv_424(self) -> None:
        geo = terranggeometri(_tjock_skarm(x1=15.0, bredd=25.0, d=100.0))
        self.assertAlmostEqual(
            d5.relativ_skarmbredd(geo, d5.Tvakantsform(1, 2, 3, 4, "forsta")), 0.25, places=12
        )

    def test_overgang_ekv_425(self) -> None:
        self.assertEqual(d5.overgang_hard_ovansida(0.0), 1.0)
        self.assertEqual(d5.overgang_hard_ovansida(0.2), 1.0)
        self.assertAlmostEqual(d5.overgang_hard_ovansida(0.5), 0.5, places=12)
        self.assertAlmostEqual(d5.overgang_hard_ovansida(0.35), 0.75, places=12)
        self.assertEqual(d5.overgang_hard_ovansida(0.8), 0.0)
        self.assertEqual(d5.overgang_hard_ovansida(3.0), 0.0)
        with self.assertRaises(BullerFel):
            d5.overgang_hard_ovansida(-0.1)

    def test_modifierad_mittreflektion_ekv_426(self) -> None:
        # Smal skärm → hård ovansida; bred skärm → Q_avM; mitt emellan medelvärdet.
        self.assertEqual(d5.modifierad_mittreflektion(-0.3, 0.1), 1.0)
        self.assertEqual(d5.modifierad_mittreflektion(-0.3, 0.9), -0.3)
        self.assertAlmostEqual(d5.modifierad_mittreflektion(-0.3, 0.5), 0.35, places=12)

    def test_smal_skarm_med_mjuk_ovansida_raknas_som_hard(self) -> None:
        r = _kor(_tjock_skarm(bredd=15.0, sigma_skarm=200.0))
        np.testing.assert_array_equal(r.q_av_mitten, np.ones(27))
        self.assertLess(float(r.q_av_mitten_omodifierad.min()), 0.5)

    def test_bred_skarm_far_medelvardet_over_hela_ovansidan(self) -> None:
        # T₁ i x = 5 och T₂ i x = 90 av 100 m: r₁₂ = 0,85 ≥ 0,8. Ovansidan har två marktyper
        # (30 m med σ = 200, 55 m med σ = 2000); Q_avM är det längdvägda medelvärdet av Q_e
        # med strykvinkeln 0 och avståndet |T₁T₂| (G.2 för enbart kilform).
        profil = _profil(
            [0.0, 4.99, 5.0, 35.0, 90.0, 90.01, 100.0],
            [0, 0, 3, 3, 3, 0, 0],
            [200.0, 200.0, 200.0, 2000.0, 200.0, 200.0],
            0.5,
            1.5,
        )
        r = _kor(profil, Skarmform(1, 2, 5), 4)
        np.testing.assert_array_equal(r.q_av_mitten, r.q_av_mitten_omodifierad)
        for nr in (0, 9, 16, 26):
            f = float(FREKVENSER[nr])
            vantat = (30.0 / 85.0) * kb.ekvivalent_reell_reflektion(f, 0.0, 85.0, 200.0, C) + (
                55.0 / 85.0
            ) * kb.ekvivalent_reell_reflektion(f, 0.0, 85.0, 2000.0, C)
            self.assertAlmostEqual(float(r.q_av_mitten[nr]), vantat, places=12)

    def test_yttersidorna_ar_delmodell_4_for_var_sin_kant(self) -> None:
        # Q_avS som källsidan av en skärm med toppen T₁, Q_avR som mottagarsidan av en skärm
        # med toppen T₂ (G.3, andra och tredje stycket).
        profil = _profil(
            [0.0, 8.0, 14.0, 15.0, 17.0, 19.0, 30.0, 100.0],
            [0, 0, 0.5, 3, 3, 0.5, 0, 0],
            [200.0, 500.0, 20000.0, 20000.0, 2000.0, 500.0, 200.0],
            0.5,
            1.5,
        )
        geo = terranggeometri(profil)
        r = _kor(profil, Skarmform(1, 3, 6), 4)
        q_s4, _ = d4.kilytornas_reflektion(FREKVENSER, geo, Skarmform(1, 3, 6), C)
        _, q_r4 = d4.kilytornas_reflektion(FREKVENSER, geo, Skarmform(1, 4, 6), C)
        np.testing.assert_allclose(r.q_av_kalla, q_s4, rtol=0, atol=1e-14)
        np.testing.assert_allclose(r.q_av_mottagare, q_r4, rtol=0, atol=1e-14)

    def test_givna_varden_gar_fore_och_ovansidan_modifieras_anda(self) -> None:
        profil = _tjock_skarm(bredd=50.0, x1=20.0)  # r₁₂ = 0,5 → r_QM = 0,5
        r = _kor(profil, q_kilyta_kalla=0.6, q_kilyta_mitten=0.2, q_kilyta_mottagare=0.4)
        np.testing.assert_array_equal(r.q_av_kalla, np.full(27, 0.6))
        np.testing.assert_array_equal(r.q_av_mottagare, np.full(27, 0.4))
        np.testing.assert_allclose(r.q_av_mitten, np.full(27, 0.6), rtol=0, atol=1e-14)
        np.testing.assert_array_equal(r.q_av_mitten_omodifierad, np.full(27, 0.2))


class TestHardSkarmHardMark(unittest.TestCase):
    """Hård tjock skärm på hård plan mark mot dubbel diffraktion med spegelkällor.

    Med Q = 1 överallt och full koherens är fältet summan av fyra dubbeldiffrakterade strålar:
    källa eller spegelkälla (z → −z) till mottagare eller spegelmottagare. Trycken räknas här
    med p2edge ur en egen geometri (vinklar direkt ur riktningsvektorer), oberoende av
    delmodellens vinkelräkning, SegmentVariables och ImagePoint.
    """

    X1, BREDD, HOJD, D, H_S, H_R = 15.0, 2.0, 3.0, 100.0, 0.3, 1.5

    def setUp(self) -> None:
        self.profil = _tjock_skarm(
            self.X1, self.BREDD, self.HOJD, INF, INF, self.H_S, self.H_R, self.D
        )

    def _tryck(self, f: float, z_s: float, z_r: float, viktigast: str) -> complex:
        x_t1, x_t2, h = self.X1, self.X1 + self.BREDD, self.HOJD
        # Kil 1: vinklar från ovansidan (riktning +x) moturs; källsidans yta går mot W₁.
        beta_1 = math.atan2(-h, -0.01) % TVA_PI
        theta_1s = math.atan2(z_s - h, 0.0 - x_t1) % TVA_PI
        # Kil 2: vinklar från mottagarsidans yta (mot W₂) moturs; ovansidan har riktning −x.
        yta_2 = math.atan2(-h, 0.01)
        beta_2 = (math.pi - yta_2) % TVA_PI
        theta_2r = math.atan2(z_r - h, self.D - x_t2) - yta_2
        geometri = tva_kanter_rata_stralar(
            beta_1,
            theta_1s,
            0.0,  # T₂ ligger över linjen T₁–R
            beta_2,
            beta_2,  # T₁ ligger över linjen T₂–S
            theta_2r,
            math.hypot(x_t1, z_s - h),
            self.BREDD,
            math.hypot(self.D - x_t2, z_r - h),
            C,
        )
        return tjock_skarm_tryck_reflektionskoefficienter(
            f,
            geometri,
            1.0,
            1.0,
            1.0,
            viktigast,  # type: ignore[arg-type]
        )

    def _jamfor(self, form: Skarmform, sekundar: int, viktigast: str) -> float:
        r = _kor(self.profil, form, sekundar, fullt_koherent=True)
        r_sr = math.hypot(self.D, self.H_R - self.H_S)
        storst = 0.0
        for nr, f_np in enumerate(FREKVENSER):
            f = float(f_np)
            w_q1 = min(1.0, float(r.viktsumma_kalla[nr])) ** 2
            w_q2 = min(1.0, float(r.viktsumma_mottagare[nr])) ** 2
            summa = (
                self._tryck(f, self.H_S, self.H_R, viktigast)
                + w_q1 * self._tryck(f, -self.H_S, self.H_R, viktigast)
                + w_q2 * self._tryck(f, self.H_S, -self.H_R, viktigast)
                + w_q1 * w_q2 * self._tryck(f, -self.H_S, -self.H_R, viktigast)
            )
            vantat = 20.0 * math.log10(abs(summa) * r_sr)
            storst = max(storst, abs(float(r.delta_l_db[nr]) - vantat))
        return storst

    def test_fyra_stralar_forsta_kanten_viktigast(self) -> None:
        self.assertLess(self._jamfor(FORM_T1, 3, "forsta"), 1e-9)

    def test_fyra_stralar_andra_kanten_viktigast(self) -> None:
        self.assertLess(self._jamfor(FORM_T2, 2, "andra"), 1e-9)

    def test_kilytorna_ar_harda_och_marken_tacker_fresnelzonen(self) -> None:
        r = _kor(self.profil)
        for q in (r.q_av_kalla, r.q_av_mitten, r.q_av_mottagare):
            np.testing.assert_array_equal(q, np.ones(27))
        np.testing.assert_allclose(r.viktsumma_mottagare, 1.0, rtol=0, atol=1e-9)
        np.testing.assert_allclose(r.viktsumma_kalla[8:], 1.0, rtol=0, atol=1e-9)

    def test_delarna_summerar(self) -> None:
        r = _kor(self.profil)
        np.testing.assert_allclose(
            r.delta_l_db,
            r.skarmeffekt_db + 20.0 * np.log10(r.markfaktor_pg),
            rtol=0,
            atol=1e-12,
        )
        niva, stralar = d5.skarmeffekt_tva_kanter_fritt_falt_db(
            FREKVENSER, self.profil, d5.tvakantsform(FORM_T1, 3), ATM
        )
        np.testing.assert_allclose(niva, r.skarmeffekt_db, rtol=0, atol=1e-12)
        self.assertEqual(stralar, r.stralar)

    def test_markfaktorn_ar_begransad_av_fyra_stralar(self) -> None:
        # Varje spegelstråle är svagare än den direkta, så |p_G| < 4 (12,04 dB).
        r = _kor(self.profil)
        self.assertLess(float(r.markfaktor_pg.max()), 4.0)
        self.assertGreater(float(r.markfaktor_pg[0]), 3.0)  # 25 Hz: nästan i fas


class TestReciprocitet(unittest.TestCase):
    def test_ombytt_kalla_och_mottagare(self) -> None:
        # Speglad profil: T₁ och T₂ byter plats och därmed valet av viktigaste kant.
        rng = np.random.default_rng(5)
        storst = 0.0
        for nr in range(10):
            x_t1 = float(rng.uniform(15.0, 60.0))
            bredd = float(rng.uniform(0.5, 20.0))
            x = [0.0, x_t1 / 2.0, x_t1 - 2.0, x_t1, x_t1 + bredd, x_t1 + bredd + 3.0, 92.0, 100.0]
            z = [
                0.0,
                float(rng.uniform(-0.3, 0.3)),
                float(rng.uniform(-0.2, 0.2)),
                float(rng.uniform(2.0, 5.0)),
                float(rng.uniform(2.0, 5.0)),
                float(rng.uniform(-0.2, 0.2)),
                float(rng.uniform(-0.3, 0.3)),
                0.0,
            ]
            sigma = [float(v) for v in rng.choice([12.5, 200.0, 2000.0, 20000.0], size=7)]
            rahet = [float(v) for v in rng.choice([0.0, 0.05, 0.25], size=7)]
            profil = _profil(
                x, z, sigma, float(rng.uniform(0.3, 2.0)), float(rng.uniform(1.0, 4.0)), rahet
            )
            primar, sekundar = (3, 4) if nr % 2 == 0 else (4, 3)
            fram = _kor(profil, Skarmform(2, primar, 5), sekundar)
            bak = _kor(_spegla(profil), Skarmform(2, 7 - primar, 5), 7 - sekundar)
            self.assertNotEqual(fram.stralar.form.viktigast, bak.stralar.form.viktigast)
            storst = max(storst, float(np.abs(fram.delta_l_db - bak.delta_l_db).max()))
            np.testing.assert_allclose(fram.q_av_kalla, bak.q_av_mottagare, rtol=0, atol=1e-12)
            np.testing.assert_allclose(fram.q_av_mitten, bak.q_av_mitten, rtol=0, atol=1e-12)
            np.testing.assert_allclose(fram.markfaktor_pg, bak.markfaktor_pg, rtol=1e-8)
        self.assertLess(storst, 1e-8)

    def test_utan_ombytt_val_haller_inte_reciprociteten(self) -> None:
        # Samma val ("forsta") i båda riktningarna är inte samma fysiska kant.
        profil = _tjock_skarm(h_s=0.5, h_r=1.5)
        fram = _kor(profil, viktigast="forsta")
        bak = _kor(_spegla(profil), viktigast="forsta")
        self.assertGreater(float(np.abs(fram.delta_l_db - bak.delta_l_db).max()), 0.5)


class TestGransfallEnKant(unittest.TestCase):
    """När den andra kanten försvinner: mot delmodell 4, i den mån metoden säger det.

    ΔL₅ går inte själv mot ΔL₄ (P-K2 visade det för p2edge); övergången görs med r_scr12 i
    ekv. (315). Siffrorna per tersband står i loggen.
    """

    def _delar(
        self, profil: Terrangprofil
    ) -> tuple[np.ndarray, d5.Delmodell5Resultat, d4.Delmodell4Resultat]:
        geo = terranggeometri(profil)
        kanter = valj_skarmkanter(geo)
        assert kanter.primar_form is not None and kanter.sekundar_kant is not None
        r12 = tolka_terrang_homogen(geo, FREKVENSER, C).r_scr12
        r5 = d5.skarm_tva_kanter(
            FREKVENSER, profil, kanter.primar_form, kanter.sekundar_kant.index, ATM
        )
        r4 = d4.skarm_en_kant(FREKVENSER, profil, kanter.primar_form, ATM)
        return r12, r5, r4

    def test_bredd_en_centimeter_ekv_315_ger_delmodell_4(self) -> None:
        for sigma_mark, sigma_skarm in ((INF, INF), (200.0, 20000.0)):
            r12, r5, r4 = self._delar(
                _tjock_skarm(bredd=0.01, sigma_mark=sigma_mark, sigma_skarm=sigma_skarm)
            )
            np.testing.assert_array_equal(r12, np.zeros(27))
            # Delmodell 5 ensam ligger kvar över delmodell 4 (uppmätt +0,6 … +3,3 dB).
            skillnad = r5.delta_l_db - r4.delta_l_db
            self.assertGreater(float(skillnad.min()), 0.5)
            self.assertLess(float(skillnad.max()), 3.5)

    def test_bidraget_fran_delmodell_5_ar_begransat_nar_bredden_krymper(self) -> None:
        # |ΔL_scr1 − ΔL₄| = r_scr12·|ΔL₅ − ΔL₄| (ekv. 315): uppmätt högst 1,0 dB för
        # bredder ≤ 0,3 m, i de band där bredden är 0,3–1 våglängd.
        for sigma_mark, sigma_skarm in ((INF, INF), (200.0, 20000.0)):
            for bredd in (0.3, 0.1, 0.03):
                r12, r5, r4 = self._delar(
                    _tjock_skarm(bredd=bredd, sigma_mark=sigma_mark, sigma_skarm=sigma_skarm)
                )
                bidrag = np.abs(r12 * (r5.delta_l_db - r4.delta_l_db))
                self.assertLess(float(bidrag.max()), 1.2, msg=f"bredd {bredd}")
                # Under 0,3 våglängders bredd är delmodell 5 helt urkopplad (ekv. 308).
                urkopplad = 0.3 * C / bredd >= FREKVENSER
                np.testing.assert_array_equal(r12[urkopplad], 0.0)

    def test_andra_kanten_langt_under_siktlinjen(self) -> None:
        # T₂ 2 m lägre än T₁: r_scr12 < 0,002 i alla band. Skärmdelen i delmodell 5 ligger då
        # inom 1,3 dB från delmodell 4 men går inte mot den (uppmätt −1,05 … +1,19 dB).
        r12, r5, r4 = self._delar(_tjock_skarm(sigma_mark=INF, sigma_skarm=INF, hojd_2=1.0))
        self.assertLess(float(r12.max()), 0.002)
        self.assertLess(float(np.abs(r5.skarmeffekt_db - r4.skarmeffekt_db).max()), 1.3)
        self.assertEqual(r5.stralar.vinklar.theta_1r_rad > 0.0, True)


class TestByteAvViktigasteKant(unittest.TestCase):
    """Vad händer när terrängtolkningen byter primär kant (störst Δℓ₀)?"""

    def test_spegelsymmetrisk_uppstallning_ger_samma_niva_for_bada_valen(self) -> None:
        profil = _tjock_skarm(x1=49.0, bredd=2.0, h_s=1.0, h_r=1.0)
        forsta = _kor(profil, viktigast="forsta")
        andra = _kor(profil, viktigast="andra")
        np.testing.assert_allclose(forsta.delta_l_db, andra.delta_l_db, rtol=0, atol=1e-9)

    def _bytespunkt(self) -> tuple[Terrangprofil, Terrangprofil]:
        def skillnad(x1: float) -> float:
            geo = terranggeometri(_tjock_skarm(x1=x1))
            return gangvagsskillnad_m(geo, 2) - gangvagsskillnad_m(geo, 3)

        a, b = 30.0, 70.0
        self.assertGreater(skillnad(a), 0.0)
        self.assertLess(skillnad(b), 0.0)
        for _ in range(60):
            mitt = 0.5 * (a + b)
            if skillnad(mitt) > 0.0:
                a = mitt
            else:
                b = mitt
        return _tjock_skarm(x1=a - 1.0e-6), _tjock_skarm(x1=b + 1.0e-6)

    def test_nivan_hoppar_i_markdelen_men_inte_i_skarmdelen(self) -> None:
        # Skärmen flyttas 2 µm förbi läget där de två kanterna har lika Δℓ₀ (x₁ ≈ 61,49 m).
        resultat = []
        for profil in self._bytespunkt():
            kanter = valj_skarmkanter(terranggeometri(profil))
            assert kanter.primar_form is not None and kanter.sekundar_kant is not None
            resultat.append(
                d5.skarm_tva_kanter(
                    FREKVENSER, profil, kanter.primar_form, kanter.sekundar_kant.index, ATM
                )
            )
        fore, efter = resultat
        self.assertEqual(
            (fore.stralar.form.viktigast, efter.stralar.form.viktigast), ("forsta", "andra")
        )
        # Skärmens verkan i fritt fält är så gott som kontinuerlig (uppmätt 0,01 dB) …
        self.assertLess(float(np.abs(efter.skarmeffekt_db - fore.skarmeffekt_db).max()), 0.05)
        # … men markfaktorn hoppar (uppmätt 2,2 dB vid 800 Hz), och inget i 5.21 jämnar ut det.
        hopp = np.abs(efter.delta_l_db - fore.delta_l_db)
        self.assertGreater(float(hopp.max()), 1.5)
        self.assertLess(float(hopp.max()), 3.0)


class TestAllmanModell(unittest.TestCase):
    def test_uppdelning_av_marksegment_andrar_inget(self) -> None:
        hel = _tjock_skarm()
        delad = _profil(
            [0.0, 6.0, 14.99, 15.0, 17.0, 17.01, 40.0, 70.0, 100.0],
            [0, 0, 0, 3, 3, 0, 0, 0, 0],
            [200.0, 200.0, 20000.0, 20000.0, 20000.0, 200.0, 200.0, 200.0],
            0.5,
            1.5,
        )
        r1 = _kor(hel)
        r2 = _kor(delad, Skarmform(2, 3, 5), 4)
        np.testing.assert_allclose(r1.delta_l_db, r2.delta_l_db, rtol=0, atol=1e-8)

    def test_viktregler_vid_viktsumma_over_ett(self) -> None:
        # Dal på källsidan: två marksegment speglar var för sig och w_1t > 1. Ekv. (220)
        # tryckt ger då Σw′_i1 = 2 (när bara den sidan överstiger 1), förvalet Σw′ = w_1t:
        # ln p_G(tryckt)/ln p_G(av1849) = 2/w_1t.
        profil = _profil(
            [0.0, 20.0, 39.99, 40.0, 42.0, 42.01, 100.0],
            [0, -1.0, 0, 3, 3, 0, 0],
            [200.0, 200.0, 20000.0, 20000.0, 20000.0, 200.0],
            1.0,
            1.5,
        )
        form = Skarmform(2, 3, 5)
        vald = _kor(profil, form, 4)
        tryckt = _kor(profil, form, 4, viktregel="tryckt")
        over = (vald.viktsumma_kalla > 1.0 + 1e-6) & (vald.viktsumma_mottagare <= 1.0)
        self.assertGreaterEqual(int(over.sum()), 5)
        kvot = np.log(tryckt.markfaktor_pg[over]) / np.log(vald.markfaktor_pg[over])
        np.testing.assert_allclose(kvot, 2.0 / vald.viktsumma_kalla[over], rtol=1e-9)
        np.testing.assert_array_equal(tryckt.skarmeffekt_db, vald.skarmeffekt_db)

    def test_tryckt_form_av_ekv_220_ar_diskontinuerlig_vid_plan_mark(self) -> None:
        # Plan mark på källsidan med en svacka på 1 mm (w_1t strax över 1 i några band)
        # jämförd med exakt plan mark. Förvalet ändras obetydligt; den tryckta formen
        # fördubblar markeffekten i dB i de banden – samma språng som i ekv. (187).
        plan = _tjock_skarm(x1=40.0, h_s=1.0)
        svacka = _profil(
            [0.0, 20.0, 39.99, 40.0, 42.0, 42.01, 100.0],
            [0, -0.001, 0, 3, 3, 0, 0],
            [200.0, 200.0, 20000.0, 20000.0, 20000.0, 200.0],
            1.0,
            1.5,
        )
        form = Skarmform(2, 3, 5)
        ref = 20.0 * np.log10(_kor(plan).markfaktor_pg)
        vald = _kor(svacka, form, 4)
        tryckt = _kor(svacka, form, 4, viktregel="tryckt")
        over = (vald.viktsumma_kalla > 1.0) & (vald.viktsumma_mottagare <= 1.0)
        self.assertGreaterEqual(int(over.sum()), 3)
        self.assertLess(float(vald.viktsumma_kalla.max()), 1.01)
        pg_vald = 20.0 * np.log10(vald.markfaktor_pg)
        pg_tryckt = 20.0 * np.log10(tryckt.markfaktor_pg)
        self.assertLess(float(np.abs(pg_vald - ref)[over].max()), 0.1)
        np.testing.assert_allclose(pg_tryckt[over], 2.0 * pg_vald[over], rtol=0.01, atol=0.01)
        self.assertGreater(float(np.abs(pg_tryckt - ref)[over].max()), 1.0)

    def test_sida_utan_mark_lamnar_den_andra_sidans_reflex(self) -> None:
        # Skärmformen börjar i profilens första punkt: ingen mark på källsidan.
        profil = _profil(
            [0.0, 5.0, 8.0, 8.01, 100.0],
            [0, 3, 3, 0, 0],
            [20000.0, 20000.0, 20000.0, 200.0],
            0.5,
            1.5,
        )
        form = Skarmform(0, 1, 3)
        vald = _kor(profil, form, 2)
        tryckt = _kor(profil, form, 2, viktregel="tryckt")
        np.testing.assert_array_equal(vald.viktsumma_kalla, np.zeros(27))
        self.assertGreater(float(np.abs(20.0 * np.log10(vald.markfaktor_pg)).max()), 3.0)
        # Ekv. (220) bokstavligt: w′_i1 = 0 för alla i1, så alla exponenter blir 0 och p_G = 1.
        np.testing.assert_array_equal(tryckt.markfaktor_pg, np.ones(27))

    def test_inga_marksegment_alls_ger_ren_skarmeffekt(self) -> None:
        profil = _profil([0.0, 40.0, 60.0, 100.0], [0, 4, 4, 0], 200.0, 0.5, 1.5)
        r = _kor(profil, Skarmform(0, 1, 3), 2)
        np.testing.assert_array_equal(r.markfaktor_pg, np.ones(27))
        np.testing.assert_array_equal(r.delta_l_db, r.skarmeffekt_db)

    def test_turbulens_rahet_och_spridningszon_sanker_koherensen(self) -> None:
        profil = _tjock_skarm(sigma_mark=INF, sigma_skarm=INF)
        lugn = _kor(profil)
        turbulent = d5.skarm_tva_kanter(
            FREKVENSER, profil, FORM_T1, 3, atmosfar_ur_vader(Vader(cv2=0.5, ct2=0.1))
        )
        skrovlig = _kor(
            _profil(
                [float(v) for v in profil.x_m], [float(v) for v in profil.z_m], INF, 0.5, 1.5, 0.25
            )
        )
        zon = _kor(profil, koherens_spridningszon=0.0)
        # Råhet (F_r) släcker den koherenta delen vid höga frekvenser: dipparna fylls ut.
        self.assertGreater(
            float(skrovlig.markfaktor_pg[18:].min()), float(lugn.markfaktor_pg[18:].min())
        )
        np.testing.assert_allclose(skrovlig.markfaktor_pg[:3], lugn.markfaktor_pg[:3], rtol=0.02)
        # Utan koherens (F_s = 0) är p_G energisumman: mellan 1 och 2 för hård mark.
        self.assertGreater(float(zon.markfaktor_pg.min()), 1.0)
        self.assertLess(float(zon.markfaktor_pg.max()), 2.0 + 1e-9)
        # Turbulens fyller ut interferensdipparna vid höga frekvenser.
        self.assertGreater(
            float(turbulent.markfaktor_pg[18:].min()), float(lugn.markfaktor_pg[18:].min())
        )
        np.testing.assert_array_equal(turbulent.skarmeffekt_db, lugn.skarmeffekt_db)

    def test_markfaktorn_gar_att_lamna_till_delmodell_7(self) -> None:
        profil = _tjock_skarm()
        r = _kor(profil)
        geo = terranggeometri(profil)
        form = r.stralar.form
        utan = d7.turbulensspridning_tva_kanter_db(
            FREKVENSER,
            geo.x_s_m,
            geo.z_s_m,
            geo.x_r_m,
            geo.z_r_m,
            float(geo.x_m[form.index_t1]),
            float(geo.z_m[form.index_t1]),
            float(geo.x_m[form.index_t2]),
            float(geo.z_m[form.index_t2]),
            0.12,
            0.008,
            15.0,
        )
        med = d7.turbulensspridning_tva_kanter_db(
            FREKVENSER,
            geo.x_s_m,
            geo.z_s_m,
            geo.x_r_m,
            geo.z_r_m,
            float(geo.x_m[form.index_t1]),
            float(geo.z_m[form.index_t1]),
            float(geo.x_m[form.index_t2]),
            float(geo.z_m[form.index_t2]),
            0.12,
            0.008,
            15.0,
            markfaktor_pg=r.markfaktor_pg,
        )
        self.assertTrue(bool(np.all(np.isfinite(med))))
        # C_SR = p_G² där p_G > 1, annars 1 (ekv. 272).
        vantat = np.where(r.markfaktor_pg > 1.0, 20.0 * np.log10(r.markfaktor_pg), 0.0)
        np.testing.assert_allclose(med - utan, vantat, rtol=0, atol=1e-9)

    def test_refraktion_och_fluktuerande_refraktion_ger_fel(self) -> None:
        profil = _tjock_skarm()
        for vader in (Vader(vind_m_s=3.0), Vader(dt_dz_k_m=0.1), Vader(std_vind_m_s=0.5)):
            with self.assertRaises(BullerFel):
                d5.skarm_tva_kanter(FREKVENSER, profil, FORM_T1, 3, atmosfar_ur_vader(vader))
        with self.assertRaises(BullerFel):
            d5.skarmeffekt_tva_kanter_fritt_falt_db(
                FREKVENSER,
                profil,
                d5.tvakantsform(FORM_T1, 3),
                atmosfar_ur_vader(Vader(vind_m_s=3.0)),
            )

    def test_ogiltig_indata_ger_fel(self) -> None:
        profil = _tjock_skarm()
        with self.assertRaises(BullerFel):
            _kor(profil, viktregel="annan")
        with self.assertRaises(BullerFel):
            _kor(profil, viktigast="mitten")
        with self.assertRaises(BullerFel):
            _kor(profil, FORM_T1, 2)
        with self.assertRaises(BullerFel):
            _kor(profil, koherens_spridningszon=np.ones(5))
        with self.assertRaises(BullerFel):
            _kor(profil, q_kilyta_mitten=np.ones(3))
        with self.assertRaises(BullerFel):
            d5.skarm_tva_kanter(np.array([]), profil, FORM_T1, 3, ATM)
        with self.assertRaises(BullerFel):
            d5.skarm_tva_kanter(np.array([100.0, -1.0]), profil, FORM_T1, 3, ATM)

    def test_resultatets_form_och_andliga_varden(self) -> None:
        r = _kor(_tjock_skarm())
        for falt in (
            r.delta_l_db,
            r.skarmeffekt_db,
            r.markfaktor_pg,
            r.q_av_kalla,
            r.q_av_mitten,
            r.q_av_mitten_omodifierad,
            r.q_av_mottagare,
            r.viktsumma_kalla,
            r.viktsumma_mottagare,
        ):
            self.assertEqual(falt.shape, (27,))
            self.assertTrue(bool(np.all(np.isfinite(falt))))
        self.assertTrue(bool(np.all(r.markfaktor_pg > 0.0)))
        self.assertTrue(bool(np.all(r.skarmeffekt_db < 0.0)))


@unittest.skipUnless(TESTSVIT.is_dir(), "testsviten grupp N saknas")
class TestTestfallensProfiler(unittest.TestCase):
    """Profilerna i blad 81 och 111 (RL 18/18) går att räkna; inget facit används."""

    def _kor_fall(self, nr: str, h_s: float) -> tuple[d5.Delmodell5Resultat, np.ndarray]:
        from buller.testfall.format import las_fall

        profil = las_fall(TESTSVIT / f"N18R{nr}_rak_vag").profil(h_s)
        geo = terranggeometri(profil)
        tolkning = tolka_terrang_homogen(geo, FREKVENSER, C)
        assert tolkning.primar_form is not None and tolkning.sekundar_kant is not None
        self.assertIn(5, tolkning.delmodeller)
        r = d5.skarm_tva_kanter(
            FREKVENSER, profil, tolkning.primar_form, tolkning.sekundar_kant.index, ATM
        )
        return r, tolkning.r_scr12

    def test_tjock_skarm_blad_81(self) -> None:
        for h_s in (0.01, 0.30, 0.75):
            r, r12 = self._kor_fall("081", h_s)
            self.assertEqual(r.stralar.form.viktigast, "forsta")
            self.assertAlmostEqual(r.stralar.kanter.vag_mitten_m, 15.0, places=9)
            np.testing.assert_array_equal(r.q_av_mitten, np.ones(27))  # r₁₂ = 0,155 ≤ 0,2
            self.assertTrue(bool(np.all(np.isfinite(r.delta_l_db))))
            np.testing.assert_array_equal(r12[4:], 1.0)

    def test_kulle_blad_111(self) -> None:
        r, r12 = self._kor_fall("111", 0.30)
        self.assertEqual(r.stralar.form.viktigast, "forsta")
        self.assertAlmostEqual(math.degrees(r.stralar.vinklar.beta1_rad), 184.8, places=1)
        self.assertTrue(bool(np.all(np.isfinite(r.delta_l_db))))
        # Dalen bakom kullen speglar på två ställen: w_2t > 1 på riktigt (viktregeln avgör).
        self.assertGreater(float(r.viktsumma_mottagare.max()), 1.5)


if __name__ == "__main__":
    unittest.main()
