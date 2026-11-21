"""Tester för baslinjekärnan (B4a). Analytiska facit, inga Nord2000-testfall."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.akustik import (
    MAX_SKARMDAMPNING_DB,
    BaslinjeKarna,
    a_vagning_db,
    exakta_mittfrekvenser_hz,
    kurze_anderson_db,
    ljudhastighet_m_s,
    luftabsorption_db_per_m,
)
from buller.akustik.baslinje import _minsta_kvadrat_linje
from buller.typer import ANTAL_BAND, BullerFel, P2PKarna, Terrangprofil, Vader


def plan_profil(
    avstand_m: float,
    kalla_hojd_m: float,
    mottagare_hojd_m: float,
    flodesresistivitet_kNsm4: float,
    antal_punkter: int = 2,
    lutning: float = 0.0,
) -> Terrangprofil:
    """Plan (ev. lutande) mark med samma flödesresistivitet överallt."""
    x_m = np.linspace(0.0, avstand_m, antal_punkter)
    return Terrangprofil(
        x_m=x_m,
        z_m=10.0 + lutning * x_m,
        flodesresistivitet_kNsm4=np.full(antal_punkter - 1, flodesresistivitet_kNsm4),
        rahet_m=np.zeros(antal_punkter - 1),
        kalla_hojd_m=kalla_hojd_m,
        mottagare_hojd_m=mottagare_hojd_m,
    )


def skarmprofil(skarmhojd_m: float, skarm_x_m: float = 20.0) -> Terrangprofil:
    """Plan mark 0–100 m med en tunn skärm; källa 0,5 m och mottagare 2 m över mark."""
    x_m = np.array([0.0, skarm_x_m - 0.05, skarm_x_m, skarm_x_m + 0.05, 100.0])
    z_m = np.array([0.0, 0.0, skarmhojd_m, 0.0, 0.0])
    return Terrangprofil(
        x_m=x_m,
        z_m=z_m,
        flodesresistivitet_kNsm4=np.full(4, 200.0),
        rahet_m=np.zeros(4),
        kalla_hojd_m=0.5,
        mottagare_hojd_m=2.0,
    )


def slumpad_profil(rng: np.random.Generator) -> Terrangprofil:
    """Slumpad profil: 2–40 punkter, kuperad mark, ibland skärmar, blandade marktyper."""
    n = int(rng.integers(2, 41))
    langd_m = float(10.0 ** rng.uniform(0.0, 3.3))
    inre = np.sort(rng.uniform(0.0, langd_m, size=n - 2)) if n > 2 else np.zeros(0)
    x_m = np.concatenate(([0.0], inre, [langd_m]))
    while not bool(np.all(np.diff(x_m) > 0.0)):  # ytterst osannolikt, men håll kontraktet
        inre = np.sort(rng.uniform(0.0, langd_m, size=n - 2))
        x_m = np.concatenate(([0.0], inre, [langd_m]))
    z_m = 20.0 + np.cumsum(rng.normal(0.0, 0.03 * langd_m / n, size=n))
    if n > 4 and rng.random() < 0.4:
        z_m[int(rng.integers(1, n - 1))] += float(rng.uniform(0.5, 15.0))
    sigma = 10.0 ** rng.uniform(1.0, 5.3, size=n - 1)
    sigma[rng.random(n - 1) < 0.1] = np.inf
    return Terrangprofil(
        x_m=x_m,
        z_m=z_m,
        flodesresistivitet_kNsm4=sigma,
        rahet_m=np.zeros(n - 1),
        kalla_hojd_m=float(rng.choice([0.0, 0.01, 0.3, 0.75, 2.0, 10.0])),
        mottagare_hojd_m=float(rng.choice([0.0, 1.5, 2.0, 4.0, 15.0, 50.0])),
    )


class TestBaslinjeGranssnitt(unittest.TestCase):
    """Kontraktet KB1 och kärnans metadata."""

    def test_uppfyller_protokollet(self) -> None:
        karna = BaslinjeKarna()
        self.assertIsInstance(karna, P2PKarna)
        self.assertEqual(karna.namn, "baslinje")
        self.assertFalse(karna.nord2000_konform)
        self.assertRegex(karna.version, r"^\d+\.\d+\.\d+$")
        self.assertGreater(len(karna.ignorerat), 0)

    def test_form_och_andliga_varden(self) -> None:
        h_db = BaslinjeKarna().overforing_db(plan_profil(100.0, 0.5, 2.0, 200.0), Vader())
        self.assertEqual(h_db.shape, (ANTAL_BAND,))
        self.assertEqual(h_db.dtype, np.float64)
        self.assertTrue(bool(np.all(np.isfinite(h_db))))

    def test_determinism(self) -> None:
        profil = skarmprofil(1.0)
        fri = plan_profil(250.0, 0.5, 4.0, 80.0, antal_punkter=12)
        vader = Vader(temperatur_c=8.0, relativ_fuktighet_procent=85.0)
        for p in (profil, fri):
            a = BaslinjeKarna().overforing_db(p, vader)
            b = BaslinjeKarna().overforing_db(p, vader)
            self.assertEqual(a.tobytes(), b.tobytes())

    def test_vader_ignoreras_utan_fel_men_med_varning(self) -> None:
        karna = BaslinjeKarna()
        profil = plan_profil(100.0, 0.5, 2.0, 200.0)
        lugnt = Vader()
        blasigt = Vader(vind_m_s=5.0, dt_dz_k_m=-0.05, std_vind_m_s=1.0, cv2=0.1, ct2=0.01)
        np.testing.assert_array_equal(
            karna.overforing_db(profil, lugnt), karna.overforing_db(profil, blasigt)
        )
        self.assertEqual(karna.varningar(profil, lugnt), ())
        varningar = karna.varningar(profil, blasigt)
        self.assertEqual(len(varningar), 4)
        self.assertTrue(any("vind_m_s" in v for v in varningar))
        self.assertTrue(any("dt_dz_k_m" in v for v in varningar))

    def test_ogiltig_indata_ger_bullerfel(self) -> None:
        karna = BaslinjeKarna()
        profil = plan_profil(100.0, 0.5, 2.0, 200.0)
        with self.assertRaises(BullerFel):
            karna.overforing_db(profil, Vader(relativ_fuktighet_procent=150.0))
        with self.assertRaises(BullerFel):
            karna.overforing_db(plan_profil(100.0, 0.5, 2.0, 0.0), Vader())
        with self.assertRaises(BullerFel):
            karna.ovre_grans_db(0.0)
        with self.assertRaises(BullerFel):
            BaslinjeKarna(antal_delfrekvenser=0)


class TestBaslinjeFysik(unittest.TestCase):
    """Analytiska gränsfall."""

    def setUp(self) -> None:
        self.karna = BaslinjeKarna()
        self.vader = Vader()

    def test_fritt_falt_hogt_over_mjuk_mark(self) -> None:
        # Källa och mottagare 500 m över mjuk mark, 10 m isär: spegelvägen är ~100 gånger
        # längre än den direkta, så markens bidrag är högst 20·lg(1 + |Q|·R₁/R₂) ≈ 0,1 dB.
        res = self.karna.berakna(plan_profil(10.0, 500.0, 500.0, 12.5), self.vader)
        self.assertFalse(res.skarmad)
        spridning_db = -10.0 * math.log10(4.0 * math.pi * 10.0**2)
        np.testing.assert_allclose(res.spridning_db, spridning_db, atol=1e-12)
        self.assertLess(float(np.max(np.abs(res.mark_db))), 0.1)
        alfa = luftabsorption_db_per_m(exakta_mittfrekvenser_hz(), 15.0, 70.0)
        np.testing.assert_allclose(res.luft_db, alfa * 10.0, rtol=1e-12)
        np.testing.assert_allclose(res.overforing_db, spridning_db - alfa * 10.0, atol=0.1)

    def test_spridning_minus_6_db_per_avstandsdubbling(self) -> None:
        a = self.karna.berakna(plan_profil(50.0, 500.0, 500.0, 12.5), self.vader)
        b = self.karna.berakna(plan_profil(100.0, 500.0, 500.0, 12.5), self.vader)
        np.testing.assert_allclose(
            a.spridning_db - b.spridning_db, 20.0 * math.log10(2.0), atol=1e-12
        )

    def test_hard_mark_lag_frekvens_plus_6_db(self) -> None:
        # 0,3 m höjder och 100 m avstånd: gångvägsskillnaden är 1,8 mm, försumbar vid 25–100 Hz.
        res = self.karna.berakna(plan_profil(100.0, 0.3, 0.3, np.inf), self.vader)
        np.testing.assert_allclose(res.mark_db[:7], 20.0 * math.log10(2.0), atol=0.01)
        fritt_db = res.spridning_db - res.luft_db
        np.testing.assert_allclose((res.overforing_db - fritt_db)[:7], 6.02, atol=0.01)

    def test_interferensminimum_i_ratt_band(self) -> None:
        # Hård mark, h_s = 1,5 m, h_r = 2 m, d = 30 m: första minimum vid c/(2·ΔR).
        h_s, h_r, d = 1.5, 2.0, 30.0
        delta_r = math.hypot(d, h_s + h_r) - math.hypot(d, h_s - h_r)
        f_min_hz = ljudhastighet_m_s(15.0) / (2.0 * delta_r)
        mitt_hz = exakta_mittfrekvenser_hz()
        band = int(np.argmin(np.abs(np.log(mitt_hz / f_min_hz))))
        res = self.karna.berakna(plan_profil(d, h_s, h_r, np.inf), self.vader)
        # Upp till och med minimibandet finns bara ett minimum.
        self.assertEqual(int(np.argmin(res.mark_db[: band + 2])), band)
        self.assertLess(float(res.mark_db[band]), -5.0)

    def test_reciprocitet_plan_mark(self) -> None:
        for sigma in (12.5, 200.0, 20000.0, np.inf):
            fram = self.karna.overforing_db(plan_profil(150.0, 0.5, 4.0, sigma, 7), self.vader)
            bak = self.karna.overforing_db(plan_profil(150.0, 4.0, 0.5, sigma, 7), self.vader)
            np.testing.assert_allclose(fram, bak, atol=1e-9)

    def test_reciprocitet_speglad_profil_med_tva_marktyper(self) -> None:
        x_m = np.array([0.0, 30.0, 100.0])
        z_m = np.zeros(3)
        fram = Terrangprofil(x_m, z_m, np.array([20000.0, 80.0]), np.zeros(2), 0.5, 4.0)
        bak = Terrangprofil(
            np.array([0.0, 70.0, 100.0]), z_m, np.array([80.0, 20000.0]), np.zeros(2), 4.0, 0.5
        )
        np.testing.assert_allclose(
            self.karna.overforing_db(fram, self.vader),
            self.karna.overforing_db(bak, self.vader),
            atol=1e-9,
        )

    def test_lutande_plan_ger_samma_som_vagratt(self) -> None:
        # Höjderna mäts vinkelrätt mot planet: jämför med motsvarande vågräta geometri.
        lutning = 0.1
        cos_fi = 1.0 / math.sqrt(1.0 + lutning**2)
        lutande = plan_profil(100.0, 1.0, 3.0, 200.0, antal_punkter=5, lutning=lutning)
        res_l = self.karna.berakna(lutande, self.vader)
        h_s, h_r = 1.0 * cos_fi, 3.0 * cos_fi
        d = 100.0 / cos_fi + lutning * (3.0 - 1.0) * cos_fi
        res_v = self.karna.berakna(plan_profil(d, h_s, h_r, 200.0), self.vader)
        np.testing.assert_allclose(res_l.mark_db, res_v.mark_db, atol=1e-8)
        np.testing.assert_allclose(res_l.spridning_db, res_v.spridning_db, atol=1e-9)

    def test_fler_delfrekvenser_konvergerar(self) -> None:
        profil = plan_profil(200.0, 0.5, 2.0, 200.0)
        grov = BaslinjeKarna(antal_delfrekvenser=7).berakna(profil, self.vader).mark_db
        fin = BaslinjeKarna(antal_delfrekvenser=201).berakna(profil, self.vader).mark_db
        # Band upp till 1 kHz (få interferensfransar per band) ska vara väl upplösta.
        self.assertLess(float(np.max(np.abs(grov[:17] - fin[:17]))), 0.5)

    def test_a_vagd_niva_gar_att_bilda(self) -> None:
        # KB2: A-vägning per band och energisumma. Fritt fält, platt spektrum L_W = 100 dB/band.
        h_db = self.karna.overforing_db(plan_profil(10.0, 500.0, 500.0, 12.5), self.vader)
        l_a = 10.0 * math.log10(float(np.sum(10.0 ** ((100.0 + h_db + a_vagning_db()) / 10.0))))
        self.assertTrue(math.isfinite(l_a))
        self.assertLess(l_a, 100.0 + 10.0 * math.log10(ANTAL_BAND))


class TestSkarm(unittest.TestCase):
    """Skärmtermen (Kurze–Anderson)."""

    def setUp(self) -> None:
        self.karna = BaslinjeKarna()
        self.vader = Vader()

    def test_formelns_kanda_varden(self) -> None:
        # N = 0 ger 5 dB; N = 1: 5 + 20·lg(√(2π)/tanh √(2π)).
        self.assertAlmostEqual(float(kurze_anderson_db(np.asarray(0.0))), 5.0, places=12)
        x = math.sqrt(2.0 * math.pi)
        facit = 5.0 + 20.0 * math.log10(x / math.tanh(x))
        self.assertAlmostEqual(float(kurze_anderson_db(np.asarray(1.0))), facit, places=12)
        self.assertAlmostEqual(facit, 13.1, delta=0.1)
        self.assertEqual(float(kurze_anderson_db(np.asarray(1.0e4))), MAX_SKARMDAMPNING_DB)
        n = np.logspace(-6.0, 3.0, 200)
        self.assertTrue(bool(np.all(np.diff(kurze_anderson_db(n)) >= 0.0)))
        with self.assertRaises(ValueError):
            kurze_anderson_db(np.asarray(-0.1))

    def test_gangvagsskillnad(self) -> None:
        res = self.karna.berakna(skarmprofil(4.0), self.vader)
        self.assertTrue(res.skarmad)
        facit = math.hypot(20.0, 3.5) + math.hypot(80.0, 2.0) - math.hypot(100.0, 1.5)
        self.assertAlmostEqual(res.gangvagsskillnad_m, facit, places=10)
        np.testing.assert_array_equal(res.mark_db, np.zeros(ANTAL_BAND))
        fresneltal = 2.0 * facit * exakta_mittfrekvenser_hz() / ljudhastighet_m_s(15.0)
        np.testing.assert_allclose(res.skarm_db, kurze_anderson_db(fresneltal), rtol=1e-12)

    def test_lag_skarm_bryter_inte_siktlinjen(self) -> None:
        res = self.karna.berakna(skarmprofil(0.5), self.vader)
        self.assertFalse(res.skarmad)
        np.testing.assert_array_equal(res.skarm_db, np.zeros(ANTAL_BAND))

    def test_dampningen_vaxer_monotont_med_skarmhojd(self) -> None:
        hojder_m = np.linspace(1.0, 12.0, 45)
        skarm_db = np.array(
            [self.karna.berakna(skarmprofil(h), self.vader).skarm_db for h in hojder_m]
        )
        nivaer_db = np.array(
            [self.karna.overforing_db(skarmprofil(h), self.vader) for h in hojder_m]
        )
        steg = np.diff(skarm_db, axis=0)
        self.assertTrue(bool(np.all(steg >= 0.0)))
        under_taket = skarm_db[1:] < MAX_SKARMDAMPNING_DB
        self.assertTrue(bool(np.all(steg[under_taket] > 0.0)))
        # Överföringen sjunker strikt (även luftvägen blir längre).
        self.assertTrue(bool(np.all(np.diff(nivaer_db, axis=0) < 0.0)))
        # Minst 5 dB så snart siktlinjen är bruten, och aldrig över taket.
        self.assertTrue(bool(np.all(skarm_db >= 5.0)))
        self.assertTrue(bool(np.all(skarm_db <= MAX_SKARMDAMPNING_DB)))


class TestOvreGrans(unittest.TestCase):
    """`ovre_grans_db` ska hålla för alla profiler."""

    def test_form_och_monotoni(self) -> None:
        karna = BaslinjeKarna()
        g10 = karna.ovre_grans_db(10.0)
        self.assertEqual(g10.shape, (ANTAL_BAND,))
        facit = -10.0 * math.log10(4.0 * math.pi * 100.0) + 20.0 * math.log10(2.0)
        np.testing.assert_allclose(g10, facit, atol=1e-12)
        self.assertTrue(bool(np.all(karna.ovre_grans_db(20.0) < g10)))

    def test_ingen_slumpad_profil_overskrider_gransen(self) -> None:
        karna = BaslinjeKarna()
        rng = np.random.default_rng(20261121)
        vader = Vader()
        storsta_marginal_db = -math.inf
        antal_skarmade = 0
        for _ in range(3000):
            profil = slumpad_profil(rng)
            res = karna.berakna(profil, vader)
            antal_skarmade += int(res.skarmad)
            marginal = float(np.max(res.overforing_db - karna.ovre_grans_db(float(profil.x_m[-1]))))
            storsta_marginal_db = max(storsta_marginal_db, marginal)
            self.assertTrue(bool(np.all(np.isfinite(res.overforing_db))))
        self.assertLessEqual(storsta_marginal_db, 1e-9)
        # Testet ska ha prövat både skärmade och fria profiler.
        self.assertGreater(antal_skarmade, 100)
        self.assertGreater(3000 - antal_skarmade, 100)

    def test_gransen_nas_nastan_over_hard_mark(self) -> None:
        # Gränsen är skarp: hård mark, låg frekvens, kort avstånd (försumbar luftabsorption).
        karna = BaslinjeKarna()
        h_db = karna.overforing_db(plan_profil(20.0, 0.0, 0.0, np.inf), Vader())
        self.assertAlmostEqual(float(h_db[0] - karna.ovre_grans_db(20.0)[0]), 0.0, delta=0.01)


class TestEkvivalentPlan(unittest.TestCase):
    """Minsta kvadrat-linjen genom profilen."""

    def test_rak_linje_aterges_exakt(self) -> None:
        x_m = np.array([0.0, 3.0, 4.0, 50.0])
        a, b = _minsta_kvadrat_linje(x_m, 2.0 - 0.03 * x_m)
        self.assertAlmostEqual(a, 2.0, places=12)
        self.assertAlmostEqual(b, -0.03, places=12)

    def test_oberoende_av_punkttathet(self) -> None:
        # Samma markyta beskriven med fler punkter ska ge samma plan.
        x_gles = np.array([0.0, 40.0, 100.0])
        z_gles = np.array([0.0, 4.0, 1.0])
        x_tat = np.unique(np.concatenate((x_gles, np.linspace(0.0, 40.0, 81))))
        z_tat = np.interp(x_tat, x_gles, z_gles)
        a1, b1 = _minsta_kvadrat_linje(x_gles, z_gles)
        a2, b2 = _minsta_kvadrat_linje(x_tat, z_tat)
        self.assertAlmostEqual(a1, a2, places=10)
        self.assertAlmostEqual(b1, b2, places=12)


if __name__ == "__main__":
    unittest.main()
