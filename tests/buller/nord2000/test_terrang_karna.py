"""Tester för terrängtolkningen och den sammansatta kärnan `Nord2000Karna`."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.nord2000 import Nord2000Karna
from buller.nord2000.atmosfar import HomogenAtmosfar
from buller.nord2000.delmodell1 import markeffekt_plan_mark_db
from buller.nord2000.delmodell2 import Marksegment, markeffekt_blandad_mark_db
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.geometri import langd, normlinje
from buller.nord2000.karna import sfarisk_spridning_db
from buller.nord2000.luftabsorption import luftabsorption_db
from buller.nord2000.terrang import (
    ekvivalent_plan_mark,
    gangvagsskillnad_m,
    geometri_over_plan_mark,
    planhetsparameter_rf,
    primar_skarmkant,
    skarmparameter_rscr1,
    terranggeometri,
)
from buller.typer import ANTAL_BAND, BullerFel, Flyttal, P2PKarna, Terrangprofil, Vader


def profil(
    x: list[float],
    z: list[float],
    sigma: list[float] | None = None,
    h_s: float = 0.3,
    h_r: float = 1.5,
) -> Terrangprofil:
    n = len(x)
    return Terrangprofil(
        x_m=np.array(x, dtype=np.float64),
        z_m=np.array(z, dtype=np.float64),
        flodesresistivitet_kNsm4=np.array(sigma if sigma else [200.0] * (n - 1)),
        rahet_m=np.zeros(n - 1),
        kalla_hojd_m=h_s,
        mottagare_hojd_m=h_r,
    )


# Profilformer ur testfallen för rak väg (RL 18/18), flyttade så att källan står i x = 0.
PLAN_VAG_GRAS = profil([0.0, 1.75, 96.75], [0.0, 0.0, 0.0], [20000.0, 200.0])
TUNN_SKARM = profil(
    [0.0, 1.75, 11.74, 11.75, 11.76, 96.75],
    [0.0, 0.0, 0.0, 3.0, 0.0, 0.0],
    [20000.0, 200.0, 20000.0, 20000.0, 200.0],
)
DAL = profil([0.0, 1.75, 2.75, 21.75, 46.75, 71.75, 96.75], [0.0, 0.0, 0.0, -2.0, -3.0, -2.0, 0.0])


class TestGeometri(unittest.TestCase):
    def test_normlinje_ekv_377(self) -> None:
        x0, y0, d = normlinje(0.0, 0.0, 10.0, 0.0, 3.0, 2.0)
        self.assertEqual((x0, y0, d), (3.0, 0.0, 2.0))
        _, _, under = normlinje(0.0, 0.0, 10.0, 0.0, 3.0, -2.0)
        self.assertEqual(under, -2.0)
        x0, y0, d = normlinje(0.0, 0.0, 1.0, 1.0, 0.0, 2.0)
        self.assertAlmostEqual(x0, 1.0)
        self.assertAlmostEqual(y0, 1.0)
        self.assertAlmostEqual(d, math.sqrt(2.0))
        self.assertAlmostEqual(langd(0.0, 0.0, 3.0, 4.0), 5.0)
        with self.assertRaises(BullerFel):
            normlinje(1.0, 1.0, 1.0, 1.0, 0.0, 0.0)

    def test_plan_profil(self) -> None:
        geo = terranggeometri(PLAN_VAG_GRAS)
        self.assertAlmostEqual(geo.h_s_m, 0.3)
        self.assertAlmostEqual(geo.h_r_m, 1.5)
        self.assertAlmostEqual(geo.r_sr_m, math.hypot(96.75, 1.2))
        self.assertAlmostEqual(geo.d_bas_tot_m, 96.75)
        np.testing.assert_allclose(geo.h_bas_m, 0.0, atol=1e-12)
        np.testing.assert_allclose(geo.d_bas_m, [0.0, 1.75, 96.75], atol=1e-12)

    def test_lutande_profil_ger_vinkelrata_hojder(self) -> None:
        lutning = 0.1
        geo = terranggeometri(profil([0.0, 50.0, 100.0], [0.0, 5.0, 10.0], h_s=1.0, h_r=2.0))
        cos_v = 1.0 / math.sqrt(1.0 + lutning**2)
        self.assertAlmostEqual(geo.h_s_m, 1.0 * cos_v, places=12)
        self.assertAlmostEqual(geo.h_r_m, 2.0 * cos_v, places=12)
        # Mottagarens fotpunkt ligger bortom profilens sista punkt, som då ersätts (5.4.1).
        self.assertGreater(float(geo.x_m[-1]), 100.0)
        self.assertEqual(float(geo.x_m[0]), 0.0)

    def test_hojd_under_en_centimeter_ersatts(self) -> None:
        geo = terranggeometri(profil([0.0, 10.0], [0.0, 0.0], h_s=0.0, h_r=0.001))
        self.assertAlmostEqual(geo.h_s_m, 0.01)
        self.assertAlmostEqual(geo.h_r_m, 0.01)


class TestEkvivalentPlanMark(unittest.TestCase):
    def test_rat_linje_aterges_exakt(self) -> None:
        x = np.array([0.0, 3.0, 40.0, 100.0])
        plan = ekvivalent_plan_mark(x, 2.0 + 0.05 * x)
        self.assertAlmostEqual(plan.alfa_m, 2.0, places=10)
        self.assertAlmostEqual(plan.beta, 0.05, places=12)
        self.assertAlmostEqual(plan.delta_h_m, 0.0, places=10)

    def test_ar_minsta_kvadratlosningen(self) -> None:
        """Avsnitt 5.21.5 anger minsta kvadratmetoden; jämför med tät sampling och polyfit."""
        x = np.array([0.0, 7.0, 30.0, 50.0, 60.0, 80.0, 100.0])
        z = np.array([0.0, 0.0, -1.0, 0.0, 0.0, -2.0, 0.0])
        plan = ekvivalent_plan_mark(x, z)
        tat_x = np.linspace(0.0, 100.0, 200001)
        beta, alfa = np.polyfit(tat_x, np.interp(tat_x, x, z), 1)
        self.assertAlmostEqual(plan.beta, float(beta), places=6)
        self.assertAlmostEqual(plan.alfa_m, float(alfa), places=5)
        # Ytan över planet är lika stor som ytan under: Δh är halva medelavvikelsen.
        avvikelse = np.abs(np.interp(tat_x, x, z) - alfa - beta * tat_x)
        self.assertAlmostEqual(plan.delta_h_m, 0.5 * float(np.mean(avvikelse)), places=4)

    def test_symmetrisk_dal(self) -> None:
        plan = ekvivalent_plan_mark(np.array([0.0, 50.0, 100.0]), np.array([0.0, -2.0, 0.0]))
        self.assertAlmostEqual(plan.beta, 0.0, places=12)
        self.assertAlmostEqual(plan.alfa_m, -1.0, places=12)
        self.assertAlmostEqual(plan.delta_h_m, 0.25, places=12)  # två trianglar à 12,5 m² / 100 m

    def test_planhetsparameter_ekv_317(self) -> None:
        geo = terranggeometri(PLAN_VAG_GRAS)
        plan = ekvivalent_plan_mark(geo.x_m, geo.z_m)
        over = geometri_over_plan_mark(geo, plan)
        self.assertAlmostEqual(over.d_e_m, 96.75)
        self.assertAlmostEqual(over.h_se_m, 0.3)
        self.assertAlmostEqual(over.h_re_m, 1.5)
        for f in mittfrekvenser_hz():
            self.assertEqual(planhetsparameter_rf(float(f), over, plan.delta_h_m, 340.0), 1.0)
        # Med Δh = 0,5 m: plan vid låga frekvenser, icke-plan vid höga, konstant över 2 kHz.
        varden = [planhetsparameter_rf(float(f), over, 0.5, 340.0) for f in mittfrekvenser_hz()]
        self.assertEqual(varden[0], 1.0)
        self.assertEqual(varden[19], 0.0)
        self.assertTrue(all(a >= b for a, b in zip(varden[:-1], varden[1:], strict=True)))
        self.assertTrue(any(0.0 < v < 1.0 for v in varden))
        self.assertEqual(
            planhetsparameter_rf(8000.0, over, 0.02, 340.0),
            planhetsparameter_rf(2000.0, over, 0.02, 340.0),
        )


class TestSkarmkant(unittest.TestCase):
    def test_plan_mark_har_ingen_skarmkant(self) -> None:
        self.assertIsNone(primar_skarmkant(terranggeometri(profil([0.0, 100.0], [0.0, 0.0]))))
        self.assertIsNone(primar_skarmkant(terranggeometri(DAL)))

    def test_skarmtoppen_valjs_och_gangvagsskillnaden_har_ratt_tecken(self) -> None:
        geo = terranggeometri(TUNN_SKARM)
        kant = primar_skarmkant(geo)
        assert kant is not None
        self.assertEqual(kant.index, 3)
        self.assertAlmostEqual(kant.hojd_over_baslinje_m, 3.0)
        self.assertAlmostEqual(kant.d_bas_m, 11.75)
        facit = math.hypot(11.75, 2.7) + math.hypot(85.0, 1.5) - math.hypot(96.75, 1.2)
        self.assertAlmostEqual(kant.gangvagsskillnad_m, facit, places=12)
        # En punkt på marken under siktlinjen har negativ gångvägsskillnad (ekv. 300).
        self.assertLess(gangvagsskillnad_m(geo, 1), 0.0)

    def test_skarmparameter_ekv_302_till_305(self) -> None:
        geo = terranggeometri(TUNN_SKARM)
        kant = primar_skarmkant(geo)
        assert kant is not None
        varden = [
            skarmparameter_rscr1(float(f), kant, geo.d_bas_tot_m, 340.0)
            for f in mittfrekvenser_hz()
        ]
        self.assertEqual(varden[-1], 1.0)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in varden))
        self.assertTrue(all(b >= a for a, b in zip(varden[:-1], varden[1:], strict=True)))
        # h_SCR/λ = 0,2 ger r_λ = 0,5; siktlinjen är bruten (r_Δℓ = 1) och r_Fz = 1 här.
        self.assertAlmostEqual(
            skarmparameter_rscr1(340.0 * 0.2 / 3.0, kant, geo.d_bas_tot_m, 340.0), 0.5, places=12
        )

    def test_lag_kant_langt_under_siktlinjen_ar_overksam(self) -> None:
        geo = terranggeometri(profil([0.0, 50.0, 100.0], [0.0, 0.02, 0.0], h_s=2.0, h_r=2.0))
        kant = primar_skarmkant(geo)
        assert kant is not None
        self.assertLess(kant.gangvagsskillnad_m, 0.0)
        for f in mittfrekvenser_hz():
            self.assertEqual(skarmparameter_rscr1(float(f), kant, geo.d_bas_tot_m, 340.0), 0.0)


class TestNord2000Karna(unittest.TestCase):
    def setUp(self) -> None:
        self.karna = Nord2000Karna()
        self.f = mittfrekvenser_hz()

    def test_uppfyller_kontraktet(self) -> None:
        self.assertIsInstance(self.karna, P2PKarna)
        self.assertFalse(self.karna.nord2000_konform)
        h = self.karna.overforing_db(PLAN_VAG_GRAS, Vader())
        self.assertEqual(h.shape, (ANTAL_BAND,))
        self.assertEqual(h.dtype, np.float64)
        self.assertTrue(bool(np.all(np.isfinite(h))))

    def test_en_marktyp_ar_summan_av_delmodell_1_och_9_och_spridning(self) -> None:
        p = profil([0.0, 40.0, 120.0], [0.0, 0.0, 0.0], h_s=0.5, h_r=4.0)
        vader = Vader(temperatur_c=10.0, relativ_fuktighet_procent=80.0, cv2=0.12, ct2=0.008)
        resultat = self.karna.berakna(p, vader)
        self.assertEqual(resultat.delmodell, "1")
        atmosfar = HomogenAtmosfar(10.0, 80.0, 101.325, 0.12, 0.008)
        r = math.hypot(120.0, 3.5)
        mark = markeffekt_plan_mark_db(self.f, 120.0, 0.5, 4.0, 200.0, 0.0, atmosfar)
        luft = luftabsorption_db(self.f, r, 10.0, 80.0)
        np.testing.assert_allclose(resultat.terrang_db, mark, atol=1e-10)
        np.testing.assert_allclose(resultat.luftabsorption_db, luft, atol=1e-12)
        self.assertAlmostEqual(resultat.spridning_db, -10.0 * math.log10(4.0 * math.pi * r * r))
        np.testing.assert_allclose(
            self.karna.overforing_db(p, vader), sfarisk_spridning_db(r) + luft + mark, atol=1e-10
        )

    def test_flera_marktyper_gar_genom_delmodell_2(self) -> None:
        resultat = self.karna.berakna(PLAN_VAG_GRAS, Vader())
        self.assertEqual(resultat.delmodell, "2")
        atmosfar = HomogenAtmosfar(15.0, 70.0, 101.325, 0.0, 0.0)
        facit = markeffekt_blandad_mark_db(
            self.f,
            96.75,
            0.3,
            1.5,
            [Marksegment(0.0, 1.75, 20000.0, 0.0), Marksegment(1.75, 96.75, 200.0, 0.0)],
            atmosfar,
        )
        np.testing.assert_allclose(resultat.terrang_db, facit, atol=1e-9)

    def test_lutande_plan_mark_motsvarar_vagrat_med_vinkelrata_hojder(self) -> None:
        lutning, h_sv, h_rv, bredd = 0.08, 0.5, 3.0, 150.0
        lutande = profil([0.0, bredd], [0.0, lutning * bredd], h_s=h_sv, h_r=h_rv)
        cos_v = 1.0 / math.sqrt(1.0 + lutning**2)
        sin_v = lutning * cos_v
        d = bredd / cos_v + (h_rv - h_sv) * sin_v
        atmosfar = HomogenAtmosfar(15.0, 70.0, 101.325, 0.0, 0.0)
        facit = markeffekt_plan_mark_db(self.f, d, h_sv * cos_v, h_rv * cos_v, 200.0, 0.0, atmosfar)
        resultat = self.karna.berakna(lutande, Vader())
        np.testing.assert_allclose(resultat.terrang_db, facit, atol=1e-8)
        self.assertAlmostEqual(
            resultat.spridning_db,
            sfarisk_spridning_db(math.hypot(bredd, lutning * bredd + h_rv - h_sv)),
        )

    def test_ovre_grans(self) -> None:
        """ΔL_t ligger mellan marktypernas egna markeffekter, och nära +6 dB som mest.

        Med Fresnelzonen begränsad till sträckan källa–mottagare (avsnitt 5.8, förval sedan
        P-S1) summerar vikterna i delmodell 2 till 1, så ΔL_2 är ett vägt medelvärde av
        delmodell 1 för varje marktyp (ekv. 124) och kan inte ligga utanför deras spann. Över
        hård mark är gränsen 20·lg 2 = 6,02 dB. Över mjuk mark ger delmodell 1 själv något mer
        vid de lägsta frekvenserna (|Q| > 1; 6,03 dB vid 25 Hz för σ = 200 kNs/m⁴ på 97 m), så
        den tidigare gränsen 6,03 dB för hela kärnan höll bara därför att den oklippta zonens
        vikter summerade till mindre än 1.
        """
        atmosfar = HomogenAtmosfar(15.0, 70.0, 101.325, 0.0, 0.0)
        for p in (PLAN_VAG_GRAS, profil([0.0, 500.0], [0.0, 0.0], [20000.0], 0.01, 4.0)):
            resultat = self.karna.berakna(p, Vader())
            d = float(p.x_m[-1] - p.x_m[0])
            per_typ = np.array(
                [
                    markeffekt_plan_mark_db(
                        self.f, d, p.kalla_hojd_m, p.mottagare_hojd_m, float(sigma), 0.0, atmosfar
                    )
                    for sigma in p.flodesresistivitet_kNsm4
                ]
            )
            self.assertTrue(bool(np.all(resultat.terrang_db <= per_typ.max(axis=0) + 1e-9)))
            self.assertTrue(bool(np.all(resultat.terrang_db >= per_typ.min(axis=0) - 1e-9)))
            h: Flyttal = resultat.overforing_db
            self.assertLess(float(np.max(h - resultat.spridning_db)), 6.05)
        hard = self.karna.berakna(profil([0.0, 500.0], [0.0, 0.0], [20000.0], 0.01, 4.0), Vader())
        # σ = 20 000 kNs/m⁴ är inte oändligt hård: 2·10⁻⁵ dB över 20·lg 2 vid 25 Hz.
        self.assertLess(float(np.max(hard.terrang_db)), 20.0 * math.log10(2.0) + 1e-3)

    def test_refraktion_raknas_pa_plan_mark_och_vagras_annars(self) -> None:
        """Sedan P-R2 räknar kärnan refraktion över plan mark (tidigare gav den `BullerFel`).

        Sedan P-S1 räknas också nedåtbrytning med N > 4 (delmodell 8, flera markreflexer; förut
        `BullerFel`). Sedan P-S2 räknas också skärmar och icke-plan terräng, se testerna nedan
        och `test_sammansattning.py`.
        """
        for vader in (
            Vader(vind_m_s=-3.0),
            Vader(dt_dz_k_m=-0.1),
            Vader(std_vind_m_s=0.5),
            Vader(vind_m_s=3.0),
        ):
            overforing = self.karna.overforing_db(PLAN_VAG_GRAS, vader)
            self.assertEqual(overforing.shape, (27,))
            self.assertTrue(bool(np.all(np.isfinite(overforing))))
        medvind = self.karna.berakna(PLAN_VAG_GRAS, Vader(vind_m_s=3.0))
        self.assertGreater(medvind.antal_stralar, 4.0)
        self.assertIsNotNone(medvind.flera_markreflexer_db)
        for vader in (Vader(vind_m_s=3.0), Vader(vind_m_s=-3.0), Vader(std_vind_m_s=0.5)):
            for terrang in (TUNN_SKARM, DAL):
                overforing = self.karna.overforing_db(terrang, vader)
                self.assertTrue(bool(np.all(np.isfinite(overforing))))

    def test_skarm_och_icke_plan_terrang_raknas(self) -> None:
        """Sedan P-S2 sätts delmodell 3–7 samman (ekv. 332); förut gav profilerna `BullerFel`."""
        skarm = self.karna.berakna(TUNN_SKARM, Vader())
        self.assertIn("4", skarm.delmodell.split("+"))
        self.assertIn("7", skarm.delmodell.split("+"))
        dal = self.karna.berakna(DAL, Vader())
        self.assertIn("3", dal.delmodell.split("+"))
        for resultat in (skarm, dal):
            self.assertEqual(resultat.overforing_db.shape, (27,))
            self.assertTrue(bool(np.all(np.isfinite(resultat.overforing_db))))
        # Skärmen (3 m, 11,75 m från källan) dämpar minst 5 dB i alla band från 250 Hz.
        self.assertTrue(bool(np.all(skarm.terrang_db[10:] < -5.0)))

    def test_dal_i_motvind_sned_strale_ar_korbar(self) -> None:
        """Motvind över en dal, sedd snett (profilen utdragen med 1/cos 80°), ska vara körbar."""
        stracka = 1.0 / math.cos(math.radians(80.0))
        dal = profil(
            [x * stracka for x in (0.0, 1.75, 2.75, 21.75, 46.75, 71.75, 96.75)],
            [0.0, 0.0, 0.0, -2.0, -3.0, -2.0, 0.0],
            [20000.0] + [200.0] * 5,
        )
        vader = Vader(vind_m_s=-3.0 * math.cos(math.radians(80.0)), std_vind_m_s=0.5)
        overforing = self.karna.overforing_db(dal, vader)
        self.assertEqual(overforing.shape, (27,))
        self.assertTrue(bool(np.all(np.isfinite(overforing))))

    def test_ogiltig_mark_vagras(self) -> None:
        with self.assertRaises(BullerFel):
            self.karna.overforing_db(profil([0.0, 50.0], [0.0, 0.0], [0.0]), Vader())


if __name__ == "__main__":
    unittest.main()
