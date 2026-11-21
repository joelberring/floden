"""Enhetstester för T09c – Environment Agency 2D-testfall (EA1, EA2, EA8A)."""

from __future__ import annotations

import json
import math
import time
import unittest

import numpy as np

from sankgraf.hierarki import bygg_sankgraf
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.ea_fall import (
    VOLYMFEL_GRANS_REL,
    EaBenchmarkFel,
    EaBenchmarkResultat,
    ea_test1_sanka_over_troskel,
    ea_test2_sanknat_4x4,
    ea_test8a_urbant_regn,
    formatera_ea_tabell,
    kor_alla_ea_testfall,
    kor_ea_test1,
    kor_ea_test8a,
)
from testsvit.k1_io import EPSG_SWEREF99_1800


class TestEaGeometri(unittest.TestCase):
    """Verifierar geometri, parametrar och felhantering för EA1, EA2 och EA8A."""

    def test_ea1_geometri_troskel_och_upplosning(self) -> None:
        g10 = ea_test1_sanka_over_troskel(pixelstorlek_m=10.0)
        self.assertEqual(g10.geo.epsg, EPSG_SWEREF99_1800)
        self.assertAlmostEqual(g10.manning_n, 0.03)
        self.assertAlmostEqual(
            float(np.max(g10.mark_z[:, g10.troskel_kolumn])),
            10.25,
            places=5,
        )
        self.assertAlmostEqual(
            float(np.min(g10.mark_z[g10.sanka_mask])),
            9.70,
            places=5,
        )

        # Finare upplösning ger dubbel bredd/längd med bibehållen tröskel och sänka
        g5 = ea_test1_sanka_over_troskel(pixelstorlek_m=5.0)
        self.assertEqual(g5.geo.bredd, 140)
        self.assertEqual(g5.geo.hojd, 20)
        self.assertAlmostEqual(
            float(np.max(g5.mark_z[:, g5.troskel_kolumn])),
            10.25,
            places=5,
        )

        with self.assertRaises(EaBenchmarkFel):
            ea_test1_sanka_over_troskel(pixelstorlek_m=0.0)
        with self.assertRaises(EaBenchmarkFel):
            ea_test1_sanka_over_troskel(facit_sanka_niva_rh2000=10.30)

    def test_ea2_geometri_16_sankor_och_lutning(self) -> None:
        g = ea_test2_sanknat_4x4(celler_per_sanka=12, pixelstorlek_m=5.0)
        self.assertEqual(g.geo.epsg, EPSG_SWEREF99_1800)
        self.assertAlmostEqual(g.manning_n, 0.03)
        graf = bygg_sankgraf(g.mark_z, np.zeros_like(g.mark_z), g.markslag, g.geo)
        lov = graf.lovnoder()
        self.assertEqual(len(lov), 16)
        for nod in lov:
            self.assertGreater(nod.persistens_m, 0.35)
            self.assertLessEqual(nod.persistens_m, 0.55)

        # Svag generell lutning från NV (0, 0) mot SO (3, 3)
        r_nv, c_nv = g.sankcentrum[0][0]
        r_so, c_so = g.sankcentrum[3][3]
        self.assertGreater(float(g.mark_z[r_nv, c_nv]), float(g.mark_z[r_so, c_so]))

        with self.assertRaises(EaBenchmarkFel):
            ea_test2_sanknat_4x4(celler_per_sanka=7)

    def test_ea8a_geometri_byggnader_och_regn(self) -> None:
        g = ea_test8a_urbant_regn(pixelstorlek_m=2.0)
        self.assertEqual(g.geo.epsg, EPSG_SWEREF99_1800)
        self.assertAlmostEqual(g.manning_n, 0.02)
        self.assertTrue(bool(np.any(g.byggnad_mask)))
        self.assertTrue(bool(np.all(g.markslag[g.byggnad_mask] == MARKSLAG_BYGGNAD)))
        # 400 mm/h i 180 s = exakt 20 mm regn
        regn_mm = g.regn_intensitet_mm_h[0] * (g.regn_tid_s[1] / 3600.0)
        self.assertAlmostEqual(regn_mm, 20.0, places=6)

        with self.assertRaises(EaBenchmarkFel):
            ea_test8a_urbant_regn(pixelstorlek_m=-1.0)


class TestEaBenchmarkKorning(unittest.TestCase):
    """Kör samtliga tre EA-testfall och verifierar fysik, mått, tabell och prestanda."""

    resultat: list[EaBenchmarkResultat]
    tabell: str
    total_kortid_s: float

    @classmethod
    def setUpClass(cls) -> None:
        t0 = time.perf_counter()
        cls.resultat, cls.tabell = kor_alla_ea_testfall()
        cls.total_kortid_s = time.perf_counter() - t0

    def _hamta(self, fall_id: str) -> EaBenchmarkResultat:
        for post in self.resultat:
            if post.fall_id == fall_id:
                return post
        raise AssertionError(f"Saknar resultat för {fall_id}")

    def test_ea1_sanka_over_troskel(self) -> None:
        r = self._hamta("EA1")
        self.assertTrue(r.godkand, r.kommentar)
        self.assertLess(r.volymfel_rel, VOLYMFEL_GRANS_REL)
        self.assertLess(r.jamforelse_mae_m, 0.01)
        self.assertGreaterEqual(r.iou, 0.90)
        self.assertGreater(r.max_djup_m, 0.30)
        self.assertIsNone(r.kortid_hybrid_s)
        self.assertIsNone(r.uppsnabbning)
        self.assertIn("SC120002", r.ea_referens)
        self.assertIn("Neelz & Pender", r.ea_referens)
        self.assertIn("TUFLOW", r.ea_referens)
        self.assertIn("std=", r.kommentar)

    def test_ea2_sanknat_4x4_hybrid_mot_2d(self) -> None:
        r = self._hamta("EA2")
        self.assertTrue(r.godkand, r.kommentar)
        self.assertLess(r.volymfel_rel, VOLYMFEL_GRANS_REL)
        self.assertLess(r.jamforelse_mae_m, 0.05)
        self.assertGreaterEqual(r.iou, 0.80)
        self.assertIsNotNone(r.kortid_hybrid_s)
        self.assertIsNotNone(r.uppsnabbning)
        assert r.uppsnabbning is not None
        self.assertGreater(r.uppsnabbning, 1.0)
        self.assertIn("NV→SO kaskad=OK", r.kommentar)
        self.assertIn("16 lövsänkor (hittade=16)", r.kommentar)
        self.assertIn("SC120002", r.ea_referens)

    def test_ea8a_urbant_regn(self) -> None:
        r = self._hamta("EA8A")
        self.assertTrue(r.godkand, r.kommentar)
        self.assertLess(r.volymfel_rel, VOLYMFEL_GRANS_REL)
        self.assertGreater(r.max_djup_m, 0.20)
        self.assertLess(r.jamforelse_mae_m, 0.05)
        self.assertGreaterEqual(r.iou, 0.85)
        self.assertIsNotNone(r.kortid_hybrid_s)
        self.assertIsNotNone(r.uppsnabbning)
        assert r.uppsnabbning is not None
        self.assertGreater(r.uppsnabbning, 1.0)
        self.assertIn("byggnader täta=JA", r.kommentar)
        self.assertIn("v_max=", r.kommentar)
        self.assertIn("fara_max=", r.kommentar)
        self.assertIn("SC120002", r.ea_referens)

    def test_ea8a_ren_subgrid_2d(self) -> None:
        r_sub = kor_ea_test8a(
            pixelstorlek_m=4.0,
            subgrid_faktor=2,
            anvand_hybrid=False,
        )
        self.assertEqual(r_sub.fall_id, "EA8A")
        self.assertTrue(r_sub.godkand, r_sub.kommentar)
        self.assertLess(r_sub.volymfel_rel, VOLYMFEL_GRANS_REL)
        self.assertIn("subgrid-2D(f=2)", r_sub.kommentar)

    def test_alla_fall_tabell_och_kortid(self) -> None:
        self.assertEqual([r.fall_id for r in self.resultat], ["EA1", "EA2", "EA8A"])
        self.assertLess(
            self.total_kortid_s,
            5.0,
            f"Alla tre EA-fall tog {self.total_kortid_s:.2f} s (gräns 5,0 s)",
        )
        rader = self.tabell.splitlines()
        self.assertEqual(len(rader), 5)  # rubrik + avgränsare + 3 fall
        self.assertTrue(rader[0].startswith("| Fall | Namn | Utfall |"))
        for fall_id in ("EA1", "EA2", "EA8A"):
            self.assertIn(f"| {fall_id} |", self.tabell)
        self.assertEqual(formatera_ea_tabell(self.resultat), self.tabell)

    def test_serialisering_och_determinism(self) -> None:
        for r in self.resultat:
            d = r.till_dict()
            self.assertIsInstance(d, dict)
            self.assertEqual(d["fall_id"], r.fall_id)
            json_str = json.dumps(d, ensure_ascii=False)
            self.assertIn(r.fall_id, json_str)
            self.assertTrue(math.isfinite(r.volymfel_rel))
            self.assertTrue(math.isfinite(r.jamforelse_mae_m))
            self.assertTrue(math.isfinite(r.iou))

        r1_a = kor_ea_test1(pixelstorlek_m=20.0, slut_tid_s=1800.0)
        r1_b = kor_ea_test1(pixelstorlek_m=20.0, slut_tid_s=1800.0)
        self.assertEqual(r1_a.max_djup_m, r1_b.max_djup_m)
        self.assertEqual(r1_a.jamforelse_mae_m, r1_b.jamforelse_mae_m)
        self.assertEqual(r1_a.volymfel_rel, r1_b.volymfel_rel)
        self.assertEqual(r1_a.iou, r1_b.iou)


if __name__ == "__main__":
    unittest.main()
