"""Enhetstester för Delmodell 11 (fasadreflektioner, AV 1106/07 avsnitt 5.20)."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.nord2000.reflektion import (
    VertikalReflektor,
    linje_korsar_segment,
    reflektionskorrektion_db,
    reflektionspunkt_plan,
    spegelpunkt_plan,
)
from buller.typer import TERSBAND_HZ, BullerFel


class TestReflektion(unittest.TestCase):
    def test_spegelpunkt_och_reflektionspunkt_normalt_infall(self) -> None:
        ref = VertikalReflektor(0.0, -50.0, 0.0, 50.0, 0.0, 12.0, reflektionsfaktor_e=1.0)
        sx, sy = spegelpunkt_plan(10.0, 0.0, 0.0, -50.0, 0.0, 50.0)
        self.assertAlmostEqual(sx, -10.0, places=9)
        self.assertAlmostEqual(sy, 0.0, places=9)

        g = reflektionspunkt_plan(10.0, 0.0, 20.0, 0.0, ref)
        self.assertIsNotNone(g)
        assert g is not None
        self.assertAlmostEqual(g.o_x_m, 0.0, places=9)
        self.assertAlmostEqual(g.o_y_m, 0.0, places=9)
        self.assertAlmostEqual(g.d1_m, 10.0, places=9)
        self.assertAlmostEqual(g.d2_m, 20.0, places=9)
        self.assertAlmostEqual(g.vinkel_mot_plan_rad, math.pi * 0.5, places=9)

    def test_olika_sidor_och_utanfor_2m_ger_none(self) -> None:
        ref = VertikalReflektor(0.0, 0.0, 0.0, 10.0, 0.0, 5.0)
        # Källa och mottagare på varsin sida.
        self.assertIsNone(reflektionspunkt_plan(-5.0, 5.0, 5.0, 5.0, ref))
        # Mer än 2 m utanför kanten y = 10.
        self.assertIsNone(reflektionspunkt_plan(5.0, 13.0, 5.0, 13.0, ref))
        # Inom 2 m utanför kanten y = 10 -> godkänns.
        self.assertIsNotNone(reflektionspunkt_plan(5.0, 11.5, 5.0, 11.5, ref))

    def test_stor_reflektor_ger_exakt_10_lg_rho_vid_hoga_frekvenser(self) -> None:
        # Mycket stor reflektor -> S_refl / S_Fz = 1 vid höga frekvenser -> ΔL_r = 10·lg(ρ_E).
        ref = VertikalReflektor(0.0, -500.0, 0.0, 500.0, 0.0, 200.0, reflektionsfaktor_e=0.7)
        g = reflektionspunkt_plan(10.0, 0.0, 20.0, 0.0, ref)
        assert g is not None
        frekv = np.asarray(TERSBAND_HZ, dtype=np.float64)
        dlr = reflektionskorrektion_db(frekv, 340.0, g, ref, 0.5, 1.5)
        forvantat = 10.0 * math.log10(0.7)
        self.assertAlmostEqual(float(dlr[-1]), forvantat, places=6)
        self.assertTrue(np.all(dlr <= forvantat + 1.0e-9))

    def test_liten_reflektor_ger_frekvensberoende_dampning(self) -> None:
        # 3 m hög skärm: lägre frekvenser har större Fresnelzon -> mer negativ ΔL_r.
        ref = VertikalReflektor(0.0, -20.0, 0.0, 20.0, 0.0, 3.0, reflektionsfaktor_e=1.0)
        g = reflektionspunkt_plan(15.0, 0.0, 50.0, 0.0, ref)
        assert g is not None
        frekv = np.asarray(TERSBAND_HZ, dtype=np.float64)
        dlr = reflektionskorrektion_db(frekv, 340.0, g, ref, 0.3, 1.5)
        self.assertLess(float(dlr[0]), float(dlr[-1]))
        self.assertLess(float(dlr[0]), -3.0)

    def test_ogiltig_reflektor_och_blockering(self) -> None:
        with self.assertRaises(BullerFel):
            VertikalReflektor(0.0, 0.0, 0.0, 0.0, 0.0, 3.0)
        with self.assertRaises(BullerFel):
            VertikalReflektor(0.0, 0.0, 10.0, 0.0, 0.0, 3.0, reflektionsfaktor_e=0.0)
        self.assertTrue(linje_korsar_segment(0.0, 0.0, 10.0, 0.0, 5.0, -5.0, 5.0, 5.0))
        self.assertFalse(linje_korsar_segment(0.0, 0.0, 10.0, 0.0, 5.0, 1.0, 5.0, 5.0))


if __name__ == "__main__":
    unittest.main()
