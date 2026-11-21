"""Tester för källsegment (delning, vinkelförfining) och fasadpunkter."""

from __future__ import annotations

import math
import unittest
from typing import Any

import numpy as np

from buller.scen.bullerscen import Bullerscen, Vag
from buller.scen.fasad import fasadnormal_deg, fasadpunkter
from buller.scen.kallsegment import (
    Vagdel,
    dela_vagar,
    forfina_for_mottagare,
    kallsegment_fran_vagar,
    till_kallsegment,
)
from buller.scen.syntetisk import syntetisk_scen
from buller.typer import BullerFel, Byggnad


class TestDelaVagar(unittest.TestCase):
    def setUp(self) -> None:
        # 100 m österut, sedan 30 m norrut.
        self.vag = Vag("v1", np.array([0.0, 100.0, 100.0]), np.array([0.0, 0.0, 30.0]), korfalt=2)

    def test_fast_maxlangd(self) -> None:
        delar = dela_vagar([self.vag], 20.0)
        self.assertEqual(len(delar), 5 + 2)
        np.testing.assert_allclose([d.langd_m for d in delar], [20.0] * 5 + [15.0] * 2)
        self.assertAlmostEqual(sum(d.langd_m for d in delar), 130.0)
        self.assertTrue(all(d.langd_m <= 20.0 + 1e-12 for d in delar))
        # Delarna hänger ihop.
        for a, b in zip(delar[:-1], delar[1:], strict=True):
            self.assertAlmostEqual(a.b_e_m, b.a_e_m)
            self.assertAlmostEqual(a.b_n_m, b.a_n_m)

    def test_kallsegment_med_flera_delkallhojder(self) -> None:
        segment = kallsegment_fran_vagar(
            [self.vag], max_langd_m=50.0, delkallhojder_m=[0.01, 0.3, 0.75], forsta_id=100
        )
        self.assertEqual(len(segment), 3 * 3)
        self.assertEqual([s.id for s in segment], list(range(100, 109)))
        self.assertEqual([s.hojd_over_mark_m for s in segment[:3]], [0.01, 0.3, 0.75])
        self.assertEqual((segment[0].e_m, segment[0].n_m, segment[0].langd_m), (25.0, 0.0, 50.0))
        self.assertEqual((segment[8].e_m, segment[8].n_m, segment[8].langd_m), (100.0, 15.0, 30.0))
        self.assertTrue(all(s.vag_id == "v1" and s.korfalt == 2 for s in segment))

    def test_fel(self) -> None:
        with self.assertRaises(BullerFel):
            dela_vagar([self.vag], 0.0)
        with self.assertRaises(BullerFel):
            dela_vagar([Vag("x", np.array([0.0]), np.array([0.0]))], 10.0)
        delar = dela_vagar([self.vag], 50.0)
        for hojder in ([], [-0.1], [0.3, 0.3], [float("nan")]):
            with self.subTest(hojder=hojder), self.assertRaises(BullerFel):
                till_kallsegment(delar, hojder)


class TestVinkelforfining(unittest.TestCase):
    def test_vinkel_kant_svar(self) -> None:
        # Från (0, 10) upptar sträckan (-10, 0)–(10, 0) precis 90 grader.
        self.assertAlmostEqual(
            Vagdel("v", 0, -10.0, 0.0, 10.0, 0.0).vinkel_fran_deg(0.0, 10.0), 90.0
        )
        self.assertAlmostEqual(Vagdel("v", 0, 0.0, 0.0, 10.0, 0.0).vinkel_fran_deg(0.0, 10.0), 45.0)

    def test_ingen_del_over_gransen(self) -> None:
        delar = dela_vagar([Vag("v", np.array([-200.0, 200.0]), np.array([0.0, 0.0]))], 50.0)
        for grans_deg in (20.0, 5.0, 1.0):
            resultat = forfina_for_mottagare(
                delar, 13.0, 25.0, max_vinkel_deg=grans_deg, min_langd_m=0.01
            )
            self.assertTrue(resultat.alla_uppfyller)
            vinklar = [d.vinkel_fran_deg(13.0, 25.0) for d in resultat.delar]
            self.assertLessEqual(max(vinklar), grans_deg + 1e-9)
            # Längden bevaras och delarna ligger i ordning längs vägen utan glapp.
            self.assertAlmostEqual(sum(d.langd_m for d in resultat.delar), 400.0)
            for a, b in zip(resultat.delar[:-1], resultat.delar[1:], strict=True):
                self.assertAlmostEqual(a.b_e_m, b.a_e_m)
            # Delarna är finare nära mottagaren än långt bort.
            self.assertLess(
                min(d.langd_m for d in resultat.delar), max(d.langd_m for d in resultat.delar)
            )

    def test_redan_fin_nog_andras_inte(self) -> None:
        delar = dela_vagar([Vag("v", np.array([0.0, 20.0]), np.array([0.0, 0.0]))], 10.0)
        resultat = forfina_for_mottagare(delar, 10.0, 500.0, max_vinkel_deg=5.0, min_langd_m=1.0)
        self.assertEqual(resultat.delar, tuple(delar))

    def test_minsta_langd_stoppar_och_redovisas(self) -> None:
        delar = dela_vagar([Vag("v", np.array([-50.0, 50.0]), np.array([0.0, 0.0]))], 100.0)
        resultat = forfina_for_mottagare(delar, 0.0, 1.0, max_vinkel_deg=5.0, min_langd_m=2.0)
        self.assertFalse(resultat.alla_uppfyller)
        self.assertGreaterEqual(min(d.langd_m for d in resultat.delar), 2.0)
        for del_, ok in zip(resultat.delar, resultat.uppfyller_vinkel, strict=True):
            self.assertEqual(bool(ok), del_.vinkel_fran_deg(0.0, 1.0) <= 5.0)

    def test_fel(self) -> None:
        delar = [Vagdel("v", 0, 0.0, 0.0, 10.0, 0.0)]
        for vinkel, minst in ((0.0, 1.0), (180.0, 1.0), (float("nan"), 1.0), (5.0, 0.0)):
            with self.subTest(vinkel=vinkel, minst=minst), self.assertRaises(BullerFel):
                forfina_for_mottagare(delar, 0.0, 5.0, max_vinkel_deg=vinkel, min_langd_m=minst)

    def test_vinkelgransen_har_inget_standardvarde(self) -> None:
        with self.assertRaises(TypeError):
            forfina_for_mottagare([], 0.0, 0.0)  # type: ignore[call-arg]


class TestFasadpunkter(unittest.TestCase):
    def _scen(self, byggnader: tuple[Byggnad, ...]) -> Bullerscen:
        return Bullerscen(
            terrang_rh2000_m=np.full((60, 60), 10.0),
            markslag=np.full((60, 60), 4, dtype=np.uint8),
            e_min_m=0.0,
            n_max_m=60.0,
            cell_m=1.0,
            byggnader=byggnader,
        )

    def test_rektangel_kant_svar(self) -> None:
        hus = Byggnad(
            "h", np.array([20.0, 40.0, 40.0, 20.0]), np.array([20.0, 20.0, 30.0, 30.0]), 19.0
        )
        resultat = fasadpunkter(
            self._scen((hus,)),
            avstand_fran_fasad_m=2.0,
            punktavstand_m=10.0,
            forsta_vaning_hojd_m=2.0,
            vaningshojd_m=3.0,
        )
        # Fria höjden är 9 m: våningar på 2, 5 och 8 m. Punkter: 2 + 1 + 2 + 1 per våning.
        self.assertEqual(len(resultat.mottagare), 6 * 3)
        self.assertEqual([m.id for m in resultat.mottagare], list(range(18)))
        forsta = resultat.mottagare[0]
        self.assertEqual((forsta.e_m, forsta.n_m), (25.0, 18.0))
        self.assertEqual((forsta.vaning, forsta.hojd_over_mark_m, forsta.byggnad_id), (1, 2.0, "h"))
        self.assertAlmostEqual(forsta.fasadnormal_deg or 0.0, 180.0)
        self.assertEqual([m.hojd_over_mark_m for m in resultat.mottagare[:3]], [2.0, 5.0, 8.0])
        normaler = sorted({round(m.fasadnormal_deg or 0.0, 6) for m in resultat.mottagare})
        self.assertEqual(normaler, [0.0, 90.0, 180.0, 270.0])
        oster = [m for m in resultat.mottagare if abs((m.fasadnormal_deg or 0.0) - 90.0) < 1e-9]
        self.assertEqual({(m.e_m, m.n_m) for m in oster}, {(42.0, 25.0)})
        self.assertEqual((resultat.antal_i_annan_byggnad, resultat.antal_utanfor_raster), (0, 0))

    def test_max_vaningar_och_lag_byggnad(self) -> None:
        hus = Byggnad(
            "h", np.array([20.0, 40.0, 40.0, 20.0]), np.array([20.0, 20.0, 30.0, 30.0]), 19.0
        )
        skjul = Byggnad(
            "s", np.array([5.0, 10.0, 10.0, 5.0]), np.array([5.0, 5.0, 10.0, 10.0]), 11.5
        )
        resultat = fasadpunkter(
            self._scen((hus, skjul)),
            avstand_fran_fasad_m=2.0,
            punktavstand_m=100.0,
            forsta_vaning_hojd_m=2.0,
            vaningshojd_m=3.0,
            max_vaningar=1,
        )
        self.assertEqual(len(resultat.mottagare), 4)
        self.assertEqual({m.byggnad_id for m in resultat.mottagare}, {"h"})
        self.assertEqual(resultat.antal_under_forsta_vaning, 4)

    def test_punkter_i_grannhus_och_utanfor_raster_redovisas(self) -> None:
        a = Byggnad(
            "a", np.array([20.0, 30.0, 30.0, 20.0]), np.array([20.0, 20.0, 30.0, 30.0]), 19.0
        )
        b = Byggnad(
            "b", np.array([30.0, 40.0, 40.0, 30.0]), np.array([20.0, 20.0, 30.0, 30.0]), 19.0
        )
        kant = Byggnad(
            "k", np.array([1.0, 6.0, 6.0, 1.0]), np.array([40.0, 40.0, 45.0, 45.0]), 19.0
        )
        resultat = fasadpunkter(
            self._scen((a, b, kant)),
            avstand_fran_fasad_m=2.0,
            punktavstand_m=100.0,
            forsta_vaning_hojd_m=2.0,
            vaningshojd_m=30.0,
        )
        self.assertEqual(resultat.antal_i_annan_byggnad, 2)  # den gemensamma väggen, från båda håll
        self.assertEqual(resultat.antal_utanfor_raster, 1)  # västfasaden på huset vid kanten
        self.assertEqual(len(resultat.mottagare), 12 - 3)

    def test_alla_punkter_ligger_fritt_pa_ratt_avstand(self) -> None:
        scen = syntetisk_scen(sida_m=150.0, cell_m=1.0, tomt_m=50.0, fro=6)
        resultat = fasadpunkter(
            scen,
            avstand_fran_fasad_m=1.5,
            punktavstand_m=4.0,
            forsta_vaning_hojd_m=2.0,
            vaningshojd_m=3.0,
        )
        self.assertGreater(len(resultat.mottagare), 100)
        e = np.array([m.e_m for m in resultat.mottagare])
        n = np.array([m.n_m for m in resultat.mottagare])
        self.assertFalse(bool(np.any(scen.i_byggnad(e, n))))
        import shapely

        for m in resultat.mottagare[::25]:
            assert m.byggnad_id is not None
            polygon = scen.polygoner[scen.byggnadsindex[m.byggnad_id]]
            self.assertAlmostEqual(
                float(shapely.distance(polygon, shapely.Point(m.e_m, m.n_m))), 1.5, places=6
            )
            tak = scen.byggnader[scen.byggnadsindex[m.byggnad_id]].tak_rh2000_m
            mark = float(scen.markhojd_m(np.array(m.e_m), np.array(m.n_m)))
            self.assertLess(mark + m.hojd_over_mark_m, tak)

    def test_normalriktning(self) -> None:
        self.assertAlmostEqual(fasadnormal_deg(0.0, 1.0), 0.0)
        self.assertAlmostEqual(fasadnormal_deg(1.0, 0.0), 90.0)
        self.assertAlmostEqual(fasadnormal_deg(-1.0, 0.0), 270.0)
        self.assertAlmostEqual(fasadnormal_deg(-math.sqrt(0.5), -math.sqrt(0.5)), 225.0)

    def test_fel(self) -> None:
        scen = self._scen(())
        bra: dict[str, Any] = {
            "avstand_fran_fasad_m": 2.0,
            "punktavstand_m": 5.0,
            "forsta_vaning_hojd_m": 2.0,
            "vaningshojd_m": 3.0,
        }
        for namn in bra:
            with self.subTest(namn=namn), self.assertRaises(BullerFel):
                fasadpunkter(scen, **{**bra, namn: 0.0})
        with self.assertRaises(BullerFel):
            fasadpunkter(scen, **bra, max_vaningar=0)
        self.assertEqual(fasadpunkter(scen, **bra).mottagare, ())


if __name__ == "__main__":
    unittest.main()
