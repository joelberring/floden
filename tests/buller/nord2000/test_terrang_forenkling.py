"""Tester för terrängförenklingen i `terrang_forenkling` (TC-101327 avsnitt 2.15, bilaga B).

Svaren följer av bilagans egna krav: gränsen för r_max, högsta antal segment, bevarade
ändpunkter och att plan mark förblir plan. Den kvarvarande avvikelsen räknas om här, oberoende
av modulens egen redovisning.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import numpy as np

from buller.nord2000.terrang_forenkling import (
    REGLER_AV_1106_07,
    REGLER_TC_101327,
    REGLER_TC_101327_SKARMAD,
    Forenklingsregler,
    forenkla_terrang,
    forenkla_terrang_detalj,
    representativ_flodesresistivitet_kNsm4,
    segmentlangdsgrans_m,
    storsta_avvikelse,
    storsta_tillatna_avvikelse_m,
)
from buller.testfall.format import las_fall
from buller.typer import BullerFel, Flyttal, Terrangprofil

TESTSVIT = Path(__file__).resolve().parents[3] / "testsvit" / "N_nord2000"


def profil(
    x: Flyttal, z: Flyttal, sigma: Flyttal | None = None, rahet: Flyttal | None = None
) -> Terrangprofil:
    n = x.shape[0]
    return Terrangprofil(
        x_m=x,
        z_m=z,
        flodesresistivitet_kNsm4=np.full(n - 1, 200.0) if sigma is None else sigma,
        rahet_m=np.zeros(n - 1) if rahet is None else rahet,
        kalla_hojd_m=0.3,
        mottagare_hojd_m=1.5,
    )


def avstand_till_polylinje(px: float, pz: float, x: Flyttal, z: Flyttal) -> float:
    """Kortaste avståndet från en punkt till en polylinje (oberoende av modulen)."""
    basta = math.inf
    for i in range(x.shape[0] - 1):
        ax, az, bx, bz = float(x[i]), float(z[i]), float(x[i + 1]), float(z[i + 1])
        t = ((px - ax) * (bx - ax) + (pz - az) * (bz - az)) / ((bx - ax) ** 2 + (bz - az) ** 2)
        t = min(1.0, max(0.0, t))
        basta = min(basta, math.hypot(px - ax - t * (bx - ax), pz - az - t * (bz - az)))
    return basta


def storsta_kvarvarande_avvikelse(tat: Terrangprofil, enkel: Terrangprofil) -> float:
    return max(
        avstand_till_polylinje(float(px), float(pz), enkel.x_m, enkel.z_m)
        for px, pz in zip(tat.x_m, tat.z_m, strict=True)
    )


def slumpterrang(rng: np.random.Generator, langd_m: float, n: int = 401) -> Terrangprofil:
    """Tät profil: summa av några sinusvågor med slumpad fas och amplitud."""
    x = np.linspace(0.0, langd_m, n)
    z = np.zeros(n)
    for _ in range(4):
        vaglangd = rng.uniform(0.1, 1.0) * langd_m
        z += rng.uniform(0.0, 1.5) * np.sin(2.0 * math.pi * x / vaglangd + rng.uniform(0.0, 6.28))
    return profil(x, z)


class TestGranser(unittest.TestCase):
    def test_r_max_grans_tc_ekv_1(self) -> None:
        self.assertEqual(storsta_tillatna_avvikelse_m(10.0), 0.1)
        self.assertEqual(storsta_tillatna_avvikelse_m(50.0), 0.1)
        self.assertAlmostEqual(storsta_tillatna_avvikelse_m(100.0), 0.2, places=12)
        self.assertAlmostEqual(storsta_tillatna_avvikelse_m(499.999), 1.0, places=5)
        self.assertEqual(storsta_tillatna_avvikelse_m(500.0), 1.0)
        self.assertEqual(storsta_tillatna_avvikelse_m(5000.0), 1.0)
        # Kontinuerlig i brytpunkterna: 0,002·50 = 0,1 och 0,002·500 = 1.
        self.assertAlmostEqual(storsta_tillatna_avvikelse_m(50.0001), 0.1, places=6)
        with self.assertRaises(BullerFel):
            storsta_tillatna_avvikelse_m(0.0)

    def test_segmentlangd_av_1106_07_ekv_393(self) -> None:
        self.assertEqual(segmentlangdsgrans_m(20.0), 1.0)
        self.assertAlmostEqual(segmentlangdsgrans_m(100.0), 5.0, places=12)
        self.assertEqual(segmentlangdsgrans_m(200.0), 10.0)
        self.assertEqual(segmentlangdsgrans_m(1000.0), 10.0)
        self.assertAlmostEqual(segmentlangdsgrans_m(20.0001), 1.0, places=4)
        with self.assertRaises(BullerFel):
            segmentlangdsgrans_m(-1.0)

    def test_tabell_14(self) -> None:
        f = representativ_flodesresistivitet_kNsm4
        for sigma, tre, tva in (
            (12.5, 31.5, 200.0),
            (124.9, 31.5, 200.0),
            (125.0, 200.0, 200.0),
            (500.0, 200.0, 200.0),
            (800.0, 200.0, 200.0),
            (800.1, 20000.0, 20000.0),
            (20000.0, 20000.0, 20000.0),
        ):
            self.assertEqual(f(sigma, 3), tre)
            self.assertEqual(f(sigma, 2), tva)
            self.assertEqual(f(sigma, 1), 200.0)
        with self.assertRaises(BullerFel):
            f(200.0, 4)
        with self.assertRaises(BullerFel):
            f(-1.0, 2)

    def test_regler(self) -> None:
        self.assertEqual(REGLER_TC_101327.max_antal_segment, 10)
        self.assertEqual(REGLER_TC_101327_SKARMAD.max_antal_segment, 20)
        self.assertFalse(REGLER_TC_101327.segmentlangdskrav)
        self.assertTrue(REGLER_AV_1106_07.segmentlangdskrav)
        with self.assertRaises(BullerFel):
            Forenklingsregler(max_antal_segment=0)
        with self.assertRaises(BullerFel):
            Forenklingsregler(antal_marktyper=4)


class TestPlanMark(unittest.TestCase):
    def test_plan_mark_blir_ett_segment(self) -> None:
        for lutning in (0.0, 0.03, -0.1):
            x = np.linspace(0.0, 250.0, 1001)
            tat = profil(x, 12.0 + lutning * x)
            f = forenkla_terrang_detalj(tat)
            self.assertEqual(f.profil.x_m.tolist(), [0.0, 250.0])
            self.assertEqual(f.profil.z_m.tolist(), [float(tat.z_m[0]), float(tat.z_m[-1])])
            self.assertEqual(f.terrangindex, (0, 1000))
            self.assertTrue(f.kraven_uppfyllda)
            self.assertLess(f.r_max_kvar_m, 1e-9)
            self.assertEqual(f.profil.kalla_hojd_m, 0.3)
            self.assertEqual(f.profil.mottagare_hojd_m, 1.5)
            self.assertEqual(f.profil.flodesresistivitet_kNsm4.tolist(), [200.0])

    def test_ojamnheter_under_gransen_raknas_som_plan_mark(self) -> None:
        rng = np.random.default_rng(3)
        x = np.linspace(0.0, 100.0, 501)  # gräns 0,2 m
        z = rng.uniform(-0.15, 0.15, 501)
        z[0] = z[-1] = 0.0
        enkel = forenkla_terrang(profil(x, z))
        self.assertEqual(enkel.x_m.shape[0], 2)

    def test_tva_punkter_lamnas_ororda(self) -> None:
        tat = profil(np.array([0.0, 40.0]), np.array([1.0, 2.0]))
        enkel = forenkla_terrang(tat)
        self.assertEqual(enkel.x_m.tolist(), [0.0, 40.0])
        self.assertEqual(enkel.z_m.tolist(), [1.0, 2.0])


class TestMaximalAvvikelse(unittest.TestCase):
    def test_storsta_avvikelse_ar_vinkelrat(self) -> None:
        # Linjen z = x från (0, 0) till (10, 10); punkten (5, 7) ligger 2/√2 från linjen.
        x = np.array([0.0, 5.0, 8.0, 10.0])
        z = np.array([0.0, 7.0, 8.5, 10.0])
        r_max, index = storsta_avvikelse(x, z, 0, 3)
        self.assertAlmostEqual(r_max, math.sqrt(2.0), places=12)
        self.assertEqual(index, 1)
        self.assertEqual(storsta_avvikelse(x, z, 2, 3), (0.0, -1))

    def test_forsta_punkten_ar_den_med_storst_avvikelse(self) -> None:
        rng = np.random.default_rng(5)
        for _ in range(20):
            tat = slumpterrang(rng, 300.0)
            f = forenkla_terrang_detalj(tat, Forenklingsregler(max_antal_segment=2))
            if len(f.terrangindex) == 2:
                continue
            x1, z1 = float(tat.x_m[0]), float(tat.z_m[0])
            x2, z2 = float(tat.x_m[-1]), float(tat.z_m[-1])
            langd = math.hypot(x2 - x1, z2 - z1)
            avstand = np.abs((x2 - x1) * (tat.z_m - z1) - (z2 - z1) * (tat.x_m - x1)) / langd
            self.assertEqual(f.terrangindex[1], int(np.argmax(avstand)))

    def test_triangelkulle(self) -> None:
        x = np.linspace(0.0, 100.0, 201)
        z = 5.0 - np.abs(x - 40.0) * 5.0 / 60.0
        z = np.where(x < 40.0, x * 5.0 / 40.0, z)
        enkel = forenkla_terrang(profil(x, z))
        self.assertEqual(enkel.x_m.tolist(), [0.0, 40.0, 100.0])
        self.assertEqual(enkel.z_m.tolist(), [0.0, 5.0, 0.0])

    def test_fler_segment_lagger_bara_till_punkter(self) -> None:
        rng = np.random.default_rng(8)
        tat = slumpterrang(rng, 400.0)
        foregaende: set[int] = set()
        for n_max in range(1, 15):
            index = set(forenkla_terrang_detalj(tat, Forenklingsregler(n_max)).terrangindex)
            self.assertTrue(foregaende <= index)
            self.assertLessEqual(len(index) - 1, n_max)
            foregaende = index


class TestKraven(unittest.TestCase):
    def test_r_max_inom_gransen_eller_segmenttaket_natt(self) -> None:
        rng = np.random.default_rng(20261121)
        uppfyllda = tak = 0
        for langd_m in (30.0, 100.0, 300.0, 1000.0):
            for _ in range(15):
                tat = slumpterrang(rng, langd_m)
                for regler in (REGLER_TC_101327, REGLER_TC_101327_SKARMAD):
                    f = forenkla_terrang_detalj(tat, regler)
                    enkel = f.profil
                    antal = enkel.x_m.shape[0] - 1
                    grans = storsta_tillatna_avvikelse_m(langd_m)
                    self.assertEqual(f.r_max_grans_m, grans)
                    self.assertLessEqual(antal, regler.max_antal_segment)
                    # Ändpunkter och höjder över mark bevaras.
                    self.assertEqual(float(enkel.x_m[0]), 0.0)
                    self.assertEqual(float(enkel.x_m[-1]), langd_m)
                    self.assertEqual(float(enkel.z_m[0]), float(tat.z_m[0]))
                    self.assertEqual(float(enkel.z_m[-1]), float(tat.z_m[-1]))
                    self.assertEqual(enkel.kalla_hojd_m, tat.kalla_hojd_m)
                    self.assertEqual(enkel.mottagare_hojd_m, tat.mottagare_hojd_m)
                    # Varje punkt i den förenklade profilen är en punkt i den täta.
                    tata = set(zip(tat.x_m.tolist(), tat.z_m.tolist(), strict=True))
                    for punkt in zip(enkel.x_m.tolist(), enkel.z_m.tolist(), strict=True):
                        self.assertIn(punkt, tata)
                    kvar = storsta_kvarvarande_avvikelse(tat, enkel)
                    self.assertLessEqual(kvar, f.r_max_kvar_m + 1e-9)
                    if f.kraven_uppfyllda:
                        uppfyllda += 1
                        self.assertLessEqual(f.r_max_kvar_m, grans)
                        self.assertLessEqual(kvar, grans + 1e-9)
                    else:
                        tak += 1
                        self.assertEqual(antal, regler.max_antal_segment)
                        self.assertGreater(f.r_max_kvar_m, grans)
        self.assertGreater(uppfyllda, 10)
        self.assertGreater(tak, 10)

    def test_segmentlangdskravet_i_av_1106_07(self) -> None:
        # Krusning med 0,5 m amplitud och 3 m våglängd över 100 m: r_max-gräns 0,2 m,
        # d_segm-gräns 5 m. Utan tak på antalet segment ska varje kvarvarande segment
        # antingen klara r_max eller vara högst 5 m långt.
        x = np.linspace(0.0, 100.0, 2001)
        tat = profil(x, 0.5 * np.sin(2.0 * math.pi * x / 3.0))
        utan = forenkla_terrang_detalj(tat, Forenklingsregler(max_antal_segment=10_000))
        med = forenkla_terrang_detalj(
            tat, Forenklingsregler(max_antal_segment=10_000, segmentlangdskrav=True)
        )
        self.assertTrue(utan.kraven_uppfyllda and med.kraven_uppfyllda)
        self.assertLessEqual(utan.r_max_kvar_m, 0.2)
        self.assertLessEqual(len(med.terrangindex), len(utan.terrangindex))
        over_gransen = 0
        for a, b in zip(med.terrangindex, med.terrangindex[1:], strict=False):
            r_max, _ = storsta_avvikelse(tat.x_m, tat.z_m, a, b)
            langd = math.hypot(float(x[b] - x[a]), float(tat.z_m[b] - tat.z_m[a]))
            self.assertTrue(r_max <= 0.2 or langd <= 5.0)
            over_gransen += r_max > 0.2
        self.assertGreater(over_gransen, 0)  # kravet på segmentlängd har haft verkan

    def test_bevara_index(self) -> None:
        x = np.linspace(0.0, 100.0, 101)
        tat = profil(x, np.zeros(101))
        f = forenkla_terrang_detalj(tat, bevara_index=(40, 41, 42))
        self.assertEqual(f.terrangindex, (0, 40, 41, 42, 100))
        self.assertEqual(f.profil.x_m.tolist(), [0.0, 40.0, 41.0, 42.0, 100.0])
        with self.assertRaises(BullerFel):
            forenkla_terrang(tat, bevara_index=(101,))


class TestMarkyta(unittest.TestCase):
    def test_brytpunkt_i_markytan_laggs_in_med_interpolerad_hojd(self) -> None:
        x = np.linspace(0.0, 100.0, 101)
        sigma = np.where(x[:-1] < 5.0, 20000.0, 200.0)
        tat = profil(x, 0.02 * x, sigma)
        f = forenkla_terrang_detalj(tat)
        self.assertEqual(f.terrangindex, (0, 100))
        self.assertEqual(f.inlagda_markpunkter, 1)
        self.assertEqual(f.profil.x_m.tolist(), [0.0, 5.0, 100.0])
        self.assertAlmostEqual(float(f.profil.z_m[1]), 0.1, places=12)
        self.assertEqual(f.profil.flodesresistivitet_kNsm4.tolist(), [20000.0, 200.0])

    def test_inlagd_punkt_ligger_pa_den_forenklade_formen(self) -> None:
        # Svag buckla (under gränsen) vid marktypsbytet: punkten läggs på räta linjen.
        x = np.linspace(0.0, 100.0, 101)
        z = np.zeros(101)
        z[50] = 0.1
        sigma = np.where(x[:-1] < 50.0, 500.0, 80.0)
        f = forenkla_terrang_detalj(profil(x, z, sigma))
        self.assertEqual(f.profil.x_m.tolist(), [0.0, 50.0, 100.0])
        self.assertEqual(f.profil.z_m.tolist(), [0.0, 0.0, 0.0])

    def test_reduktion_av_marktyper(self) -> None:
        x = np.linspace(0.0, 90.0, 91)
        sigma = np.empty(90)
        sigma[:30], sigma[30:60], sigma[60:] = 80.0, 500.0, 2000.0
        tat = profil(x, np.zeros(91), sigma)
        orord = forenkla_terrang(tat)
        self.assertEqual(orord.flodesresistivitet_kNsm4.tolist(), [80.0, 500.0, 2000.0])
        tre = forenkla_terrang(tat, Forenklingsregler(antal_marktyper=3))
        self.assertEqual(tre.x_m.tolist(), [0.0, 30.0, 60.0, 90.0])
        self.assertEqual(tre.flodesresistivitet_kNsm4.tolist(), [31.5, 200.0, 20000.0])
        tva = forenkla_terrang(tat, Forenklingsregler(antal_marktyper=2))
        self.assertEqual(tva.x_m.tolist(), [0.0, 60.0, 90.0])
        self.assertEqual(tva.flodesresistivitet_kNsm4.tolist(), [200.0, 20000.0])
        en = forenkla_terrang(tat, Forenklingsregler(antal_marktyper=1))
        self.assertEqual(en.x_m.tolist(), [0.0, 90.0])
        self.assertEqual(en.flodesresistivitet_kNsm4.tolist(), [200.0])

    def test_rahet_bevaras_som_marktyp(self) -> None:
        x = np.linspace(0.0, 100.0, 101)
        rahet = np.where(x[:-1] < 20.0, 0.0, 0.5)
        enkel = forenkla_terrang(profil(x, np.zeros(101), rahet=rahet))
        self.assertEqual(enkel.x_m.tolist(), [0.0, 20.0, 100.0])
        self.assertEqual(enkel.rahet_m.tolist(), [0.0, 0.5])


class TestTestfallensProfiler(unittest.TestCase):
    """Testfallens profiler (RL 18/18), förtätade och förenklade igen."""

    @staticmethod
    def _fortatad(p: Terrangprofil, steg_m: float = 0.25) -> Terrangprofil:
        extra = np.arange(steg_m, float(p.x_m[-1]), steg_m)
        x = np.unique(np.concatenate((p.x_m, extra)))
        z = np.interp(x, p.x_m, p.z_m)
        for xi, zi in zip(p.x_m, p.z_m, strict=True):
            z[int(np.searchsorted(x, xi))] = zi
        mitt = 0.5 * (x[:-1] + x[1:])
        seg = np.clip(np.searchsorted(p.x_m, mitt, side="right") - 1, 0, p.x_m.shape[0] - 2)
        return Terrangprofil(
            x_m=x,
            z_m=z,
            flodesresistivitet_kNsm4=p.flodesresistivitet_kNsm4[seg],
            rahet_m=p.rahet_m[seg],
            kalla_hojd_m=p.kalla_hojd_m,
            mottagare_hojd_m=p.mottagare_hojd_m,
        )

    def test_fortatade_testprofiler_haller_kraven(self) -> None:
        if not TESTSVIT.is_dir():
            self.skipTest("Testsviten saknas.")
        antal = 0
        for mapp in sorted(TESTSVIT.glob("N18R*_rak_vag")):
            fall = las_fall(mapp)
            if fall.terrang is None or fall.vag is None:
                continue
            original = fall.profil(0.3)
            tat = self._fortatad(original)
            f = forenkla_terrang_detalj(tat, REGLER_TC_101327_SKARMAD)
            enkel = f.profil
            antal += 1
            self.assertTrue(f.kraven_uppfyllda, fall.id)
            grans = storsta_tillatna_avvikelse_m(float(original.x_m[-1]))
            self.assertLessEqual(storsta_kvarvarande_avvikelse(tat, enkel), grans + 1e-9, fall.id)
            self.assertLessEqual(enkel.x_m.shape[0], original.x_m.shape[0], fall.id)
            self.assertEqual(float(enkel.x_m[-1]), float(original.x_m[-1]))
            # Högsta punkten (skärmtopp, vägbank) finns kvar med exakt höjd.
            self.assertEqual(float(np.max(enkel.z_m)), float(np.max(original.z_m)), fall.id)

            # Samma följd av marktyper som i originalet.
            def typer(p: Terrangprofil) -> list[tuple[float, float]]:
                ut: list[tuple[float, float]] = []
                for par in zip(
                    p.flodesresistivitet_kNsm4.tolist(), p.rahet_m.tolist(), strict=True
                ):
                    if not ut or ut[-1] != par:
                        ut.append(par)
                return ut

            self.assertEqual(typer(enkel), typer(original), fall.id)
        self.assertGreater(antal, 50)


if __name__ == "__main__":
    unittest.main()
