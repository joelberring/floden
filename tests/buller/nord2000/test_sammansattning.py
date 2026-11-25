"""Tester för sammansättningen av delmodellerna (AV 1106/07 avsnitt 5.21–5.22, ekv. 332).

Svaren följer av ekv. (332) själv (sammanvägningen räknad om i testet ur de redovisade leden),
av gränsfall (plan mark ska ge precis plan mark-modellens värde) och av kontinuitet (en skärm
vars höjd går mot noll ska gå mot plan mark genom övergångsparametrarna).
"""

from __future__ import annotations

import unittest

import numpy as np

from buller.nord2000 import Nord2000Karna, delmodell8
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.refraktion import atmosfar_ur_vader
from buller.nord2000.sammansattning import (
    Sammansattningsval,
    markeffekt_plan_modell_db,
    marksegment_over_plan_mark,
    terrangeffekt,
)
from buller.nord2000.terrang import ekvivalent_plan_mark, geometri_over_plan_mark, terranggeometri
from buller.typer import BullerFel, Flyttal, Terrangprofil, Vader

F = mittfrekvenser_hz()
TURBULENS = {"cv2": 0.12, "ct2": 0.008}


def _profil(
    x: list[float], z: list[float], sigma: list[float], h_s: float = 0.5, h_r: float = 1.5
) -> Terrangprofil:
    return Terrangprofil(
        x_m=np.array(x, dtype=np.float64),
        z_m=np.array(z, dtype=np.float64),
        flodesresistivitet_kNsm4=np.array(sigma, dtype=np.float64),
        rahet_m=np.zeros(len(sigma)),
        kalla_hojd_m=h_s,
        mottagare_hojd_m=h_r,
    )


def _tunn_skarm(hojd: float, sigma: float = 200.0) -> Terrangprofil:
    return _profil([0.0, 29.99, 30.0, 30.01, 100.0], [0.0, 0.0, hojd, 0.0, 0.0], [sigma] * 4)


PLANA = (
    _profil([0.0, 100.0], [0.0, 0.0], [200.0]),
    _profil([0.0, 3.0, 100.0], [0.0, 0.0, 0.0], [20000.0, 200.0], 0.01, 4.0),
    _profil([0.0, 25.0, 50.0, 75.0, 100.0], [1.0, 1.5, 2.0, 2.5, 3.0], [200.0, 500.0, 200.0, 50.0]),
)
VADER = (
    Vader(**TURBULENS),
    Vader(std_vind_m_s=0.5, **TURBULENS),
    Vader(vind_m_s=3.0, std_vind_m_s=0.5, **TURBULENS),
    Vader(vind_m_s=-3.0, std_vind_m_s=0.5, **TURBULENS),
    Vader(dt_dz_k_m=-0.1),
)


def _plan_modell(profil: Terrangprofil, vader: Vader) -> Flyttal:
    """ΔL_flat räknad direkt, så som kärnan gjorde före sammansättningen."""
    geo = terranggeometri(profil)
    over_plan = geometri_over_plan_mark(geo, ekvivalent_plan_mark(geo.x_m, geo.z_m))
    segment = marksegment_over_plan_mark(geo, over_plan)
    return markeffekt_plan_modell_db(F, over_plan, segment, atmosfar_ur_vader(vader))[0]


class TestPlanMark(unittest.TestCase):
    def test_plan_mark_ger_plan_mark_modellen_bit_for_bit(self) -> None:
        for profil in PLANA:
            for vader in VADER:
                effekt = terrangeffekt(F, profil, atmosfar_ur_vader(vader))
                np.testing.assert_array_equal(effekt.delta_l_db, _plan_modell(profil, vader))
                self.assertIn(effekt.beteckning, ("1", "2"))
                self.assertFalse(bool(np.any(effekt.tolkning.r_scr1 > 0.0)))

    def test_karnan_ger_plan_mark_modellen_och_delmodell_8_bit_for_bit(self) -> None:
        """ΔL_t = ΔL_flat ⊕ ΔL₈, som före sammansättningen (ekv. 332 med r_scr1 = 0, r_flat = 1)."""
        karna = Nord2000Karna()
        med_flera_reflexer = 0
        for profil in PLANA:
            for vader in VADER:
                resultat = karna.berakna(profil, vader)
                vantat = _plan_modell(profil, vader)
                atm = atmosfar_ur_vader(vader)
                if atm.har_refraktion:
                    dl8 = delmodell8.flera_markreflexer(F, profil, atm, "hog")
                    if dl8.bidrar:
                        med_flera_reflexer += 1
                        vantat = delmodell8.energisumma_db(vantat, dl8.delta_l_db)
                np.testing.assert_array_equal(resultat.terrang_db, vantat)
        self.assertGreater(med_flera_reflexer, 0)

    def test_metodens_egen_overgang_ger_r_flat_1_for_plan_mark(self) -> None:
        """Utan genvägen för plan mark: delmodell 3 räknas, r_flat = 1 exakt och ΔL₃ används inte.

        Homogen atmosfär. Visar att genvägen (`PLAN_TOLERANS_M`) inte ändrar något resultat.
        """
        val = Sammansattningsval(alltid_delmodell3=True)
        for profil in PLANA[1:]:
            atm = atmosfar_ur_vader(Vader(**TURBULENS))
            effekt = terrangeffekt(F, profil, atm, val)
            assert effekt.r_flat is not None and effekt.resultat3 is not None
            np.testing.assert_array_equal(effekt.r_flat, np.ones(F.shape[0]))
            self.assertLessEqual(float(np.max(effekt.resultat3.summa_konkav_vikt)), 1.0 + 1e-9)
            np.testing.assert_array_equal(
                effekt.delta_l_db, terrangeffekt(F, profil, atm).delta_l_db
            )


class TestEkvation332(unittest.TestCase):
    def _kontrollera(self, profil: Terrangprofil, vader: Vader, delmodeller: set[str]) -> None:
        effekt = terrangeffekt(F, profil, atmosfar_ur_vader(vader))
        # Delmodell 3 kan komma till i band där r_scr1 < 1 och r_flat < 1.
        self.assertLessEqual(delmodeller, set(effekt.delmodeller))
        self.assertLessEqual(set(effekt.delmodeller) - delmodeller, {"1", "2", "3"})
        t = effekt.tolkning
        for b in range(F.shape[0]):
            r1, r2, r12 = float(t.r_scr1[b]), float(t.r_scr2[b]), float(t.r_scr12[b])
            vantat = 0.0
            if r1 < 1.0:
                assert effekt.utan_skarm_db is not None and effekt.r_flat is not None
                assert effekt.plan_db is not None
                r_flat = float(effekt.r_flat[b])
                noscr = r_flat * float(effekt.plan_db[b])
                if r_flat < 1.0:
                    assert effekt.delmodell3_db is not None
                    noscr += (1.0 - r_flat) * float(effekt.delmodell3_db[b])
                self.assertAlmostEqual(float(effekt.utan_skarm_db[b]), noscr, places=10)
                vantat += (1.0 - r1) * noscr
            if r1 > 0.0:
                scr1 = 0.0
                if r2 < 1.0:
                    if r12 > 0.0:
                        assert effekt.delmodell5_db is not None
                        scr1 += r12 * float(effekt.delmodell5_db[b])
                    if r12 < 1.0:
                        assert effekt.delmodell4_db is not None
                        scr1 += (1.0 - r12) * float(effekt.delmodell4_db[b])
                scr = (1.0 - r2) * scr1
                if r2 > 0.0:
                    assert effekt.delmodell6_db is not None
                    scr += r2 * float(effekt.delmodell6_db[b])
                vantat += r1 * scr
            self.assertAlmostEqual(float(effekt.delta_l_db[b]), vantat, places=9)

    def test_tunn_skarm(self) -> None:
        self._kontrollera(_tunn_skarm(3.0), Vader(**TURBULENS), {"4", "7"})

    def test_lag_skarm_blandar_skarm_och_plan_mark(self) -> None:
        profil = _tunn_skarm(0.3)
        effekt = terrangeffekt(F, profil, atmosfar_ur_vader(Vader()))
        self.assertTrue(
            bool(np.any((effekt.tolkning.r_scr1 > 0.0) & (effekt.tolkning.r_scr1 < 1.0)))
        )
        self._kontrollera(profil, Vader(), {"4", "7"})

    def test_tjock_skarm_och_tva_skarmar_med_vader(self) -> None:
        tjock = _profil(
            [0.0, 14.99, 15.0, 30.0, 30.01, 100.0],
            [0.0, 0.0, 2.0, 2.0, 0.0, 0.0],
            [200.0, 20000.0, 20000.0, 20000.0, 200.0],
        )
        tva = _profil(
            [0.0, 14.99, 15.0, 15.01, 79.99, 80.0, 80.01, 100.0],
            [0.0, 0.0, 2.0, 0.0, 0.0, 3.0, 0.0, 0.0],
            [200.0, 20000.0, 20000.0, 200.0, 20000.0, 20000.0, 200.0],
        )
        for vader in (Vader(std_vind_m_s=0.5, **TURBULENS), Vader(vind_m_s=3.0, **TURBULENS)):
            self._kontrollera(tjock, vader, {"4", "5", "7"})
            self._kontrollera(tva, vader, {"4", "6", "7"})

    def test_dal_anvander_delmodell_3(self) -> None:
        dal = _profil([0.0, 30.0, 50.0, 70.0, 100.0], [0.0, -2.0, -3.0, -2.0, 0.0], [200.0] * 4)
        self._kontrollera(dal, Vader(std_vind_m_s=0.5, **TURBULENS), {"3"})

    def test_turbulensspridningen_hojer_nivan_bakom_skarmen(self) -> None:
        profil = _tunn_skarm(4.0)
        utan = terrangeffekt(F, profil, atmosfar_ur_vader(Vader()))
        med = terrangeffekt(F, profil, atmosfar_ur_vader(Vader(**TURBULENS)))
        assert utan.delmodell4_db is not None and utan.resultat4 is not None
        assert med.delmodell4_db is not None and med.resultat4 is not None
        # Utan turbulens är ΔL₇ = −∞ dB och ⊕ ändrar ingenting (utöver avrundning i ⊕).
        np.testing.assert_allclose(
            utan.delmodell4_db, utan.resultat4.delta_l_db, rtol=0, atol=1e-12
        )
        self.assertTrue(bool(np.all(med.delmodell4_db >= med.resultat4.delta_l_db)))
        self.assertGreater(float(med.delmodell4_db[-1] - med.resultat4.delta_l_db[-1]), 0.1)


class TestSkarmhojdMotNoll(unittest.TestCase):
    """En skärm vars höjd går mot noll går mot plan mark genom r_scr1 (ekv. 302–306)."""

    def test_kontinuerligt_mot_plan_mark(self) -> None:
        for sigma in (200.0, 20000.0):
            plan = terrangeffekt(F, _tunn_skarm(0.0, sigma), atmosfar_ur_vader(Vader())).delta_l_db
            forra_r = 1.0
            # Uppmätt (loggen P-S2): 0,3 m ger 9,4 resp. 2,3 dB, 0,1 m 0,28 resp. 0,35 dB,
            # 0,05 m 0,007 resp. 0,021 dB; från 0,03 m är r_scr1 = 0 i alla band och resten
            # (0,0001 dB) kommer av att den ekvivalenta plana marken lyfts av bulan.
            for hojd, grans_db in (
                (0.3, 10.0),
                (0.1, 0.4),
                (0.05, 0.03),
                (0.03, 1e-3),
                (0.001, 1e-3),
            ):
                effekt = terrangeffekt(F, _tunn_skarm(hojd, sigma), atmosfar_ur_vader(Vader()))
                storsta_r = float(np.max(effekt.tolkning.r_scr1))
                self.assertLessEqual(storsta_r, forra_r)
                forra_r = storsta_r
                avvikelse = float(np.max(np.abs(effekt.delta_l_db - plan)))
                self.assertLess(avvikelse, grans_db, f"h = {hojd} m, σ = {sigma}")
            self.assertEqual(forra_r, 0.0)

    def test_inget_sprang_nar_skarmen_blir_verksam(self) -> None:
        """Där r_scr1 lämnar 0 (mellan 0,03 och 0,05 m) ändras ΔL_t med hundradels dB per mm."""
        nivaer = [
            terrangeffekt(F, _tunn_skarm(h, 200.0), atmosfar_ur_vader(Vader())).delta_l_db
            for h in np.arange(0.030, 0.0601, 0.002)
        ]
        steg = [float(np.max(np.abs(b - a))) for a, b in zip(nivaer, nivaer[1:], strict=False)]
        self.assertLess(max(steg), 0.02)


class TestBesked(unittest.TestCase):
    def test_led_utan_vikt_anvands_inte_och_led_med_vikt_kravs(self) -> None:
        from buller.nord2000.sammansattning import _blanda

        vikt = np.array([0.0, 1.0, 0.25])
        a = np.array([np.nan, 2.0, 4.0])
        b = np.array([1.0, np.nan, 8.0])
        np.testing.assert_array_equal(_blanda(vikt, a, b), np.array([1.0, 2.0, 7.0]))
        with self.assertRaises(BullerFel):
            _blanda(vikt, None, b)
        np.testing.assert_array_equal(_blanda(np.zeros(3), None, b)[[0, 2]], b[[0, 2]])


if __name__ == "__main__":
    unittest.main()


class TestSekundarKantMedRefraktion(unittest.TestCase):
    """Δℓ för T₁₂ med refraktion (avsnitt 5.21.3, "in the same way" som ekv. 307), P-S2."""

    TJOCK = _profil(
        [0.0, 14.99, 15.0, 30.0, 30.01, 100.0],
        [0.0, 0.0, 2.0, 2.0, 0.0, 0.0],
        [200.0, 20000.0, 20000.0, 20000.0, 200.0],
    )

    def _dl(self, vader: Vader) -> tuple[float | None, float | None, float | None]:
        from buller.nord2000.sammansattning import gangvagsskillnader_refraktion
        from buller.nord2000.terrang_skarmar import valj_skarmkanter

        geo = terranggeometri(self.TJOCK)
        kanter = valj_skarmkanter(geo)
        return gangvagsskillnader_refraktion(
            F, self.TJOCK, kanter, atmosfar_ur_vader(vader), Sammansattningsval()
        )

    def test_svag_refraktion_ger_rata_strålarnas_dl0(self) -> None:
        from buller.nord2000.terrang_skarmar import valj_skarmkanter

        geo = terranggeometri(self.TJOCK)
        kanter = valj_skarmkanter(geo)
        assert kanter.sekundar_kant is not None
        _, _, dl12 = self._dl(Vader(dt_dz_k_m=1e-7))
        assert dl12 is not None
        # T₁₂ ligger i skärmens ände, så Δℓ₀ mot T₁ och S (eller R) är rätt jämförelse.
        from buller.nord2000.terrang_skarmar import gangvagsskillnad_sekundar_m

        assert kanter.primar is not None
        dl0 = gangvagsskillnad_sekundar_m(geo, kanter.primar, kanter.sekundar_kant.index)
        self.assertAlmostEqual(dl12, dl0, delta=1e-4 * max(abs(dl0), 1e-3))

    def test_homogen_atmosfar_ger_none(self) -> None:
        self.assertEqual(self._dl(Vader()), (None, None, None))

    def test_medvind_minskar_dl(self) -> None:
        _, _, utan = self._dl(Vader(dt_dz_k_m=1e-7))
        _, _, med = self._dl(Vader(vind_m_s=3.0))
        assert utan is not None and med is not None
        self.assertLess(med, utan)  # strålar som böjs nedåt går närmare kanten


class TestSkuggzonISkarmmodellerna(unittest.TestCase):
    """Delmodell 4–6 avgör skuggzonen band för band (ekv. 167, 171, 184), P-S2."""

    def test_avgorande_i_skuggzonslasningen_anvands_inte(self) -> None:
        from dataclasses import replace

        from buller.nord2000.delmodell4 import Segmentvariabler, marksidor_refraktion
        from buller.nord2000.refraktion_mark import Skuggzonslasning

        variabler = Segmentvariabler(
            avstand_m=200.0, hojd_kalla_m=0.5, hojd_mottagare_m=2.0, d1_m=0.0, d2_m=200.0
        )
        atm = atmosfar_ur_vader(Vader(vind_m_s=-5.0, std_vind_m_s=0.5, **TURBULENS))
        resultat = []
        for avgorande in ("per_band", "delat", "oberoende"):
            lasning = replace(Skuggzonslasning(), avgorande=avgorande)  # type: ignore[arg-type]
            sidor = marksidor_refraktion(
                F, variabler, 1.0, 200.0, 0.0, 1.0, atm, skuggzon=lasning
            )
            resultat.append([None if s is None else s.skuggzon_db for s in sidor])
        self.assertEqual(resultat[0], resultat[1])
        self.assertEqual(resultat[0], resultat[2])
        self.assertTrue(any(v is not None and v < 0.0 for v in resultat[0]))

    def test_koherens_spridningszon_vidarebefordras_till_delmodell3_och_4(self) -> None:
        """AV 1106/07 5.22: F_s ska föras in även i delmodell 3 (dal) och delmodell 4 (skärm)."""
        f_s = np.full(F.shape[0], 0.2, dtype=np.float64)
        atm = atmosfar_ur_vader(Vader(**TURBULENS))
        dal = _profil([0.0, 20.0, 50.0, 80.0, 100.0], [0.0, -3.0, -4.0, -3.0, 0.0], [200.0] * 4)
        dal_utan = terrangeffekt(F, dal, atm)
        dal_med = terrangeffekt(F, dal, atm, koherens_spridningszon=f_s)
        assert dal_utan.delmodell3_db is not None and dal_med.delmodell3_db is not None
        self.assertGreater(
            float(np.max(np.abs(dal_med.delmodell3_db - dal_utan.delmodell3_db))), 0.5
        )

        skarm = _tunn_skarm(3.0, sigma=200.0)
        skarm_utan = terrangeffekt(F, skarm, atm)
        skarm_med = terrangeffekt(F, skarm, atm, koherens_spridningszon=f_s)
        assert skarm_utan.delmodell4_db is not None and skarm_med.delmodell4_db is not None
        self.assertGreater(
            float(np.max(np.abs(skarm_med.delmodell4_db - skarm_utan.delmodell4_db))), 0.1
        )
