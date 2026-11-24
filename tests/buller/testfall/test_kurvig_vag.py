"""Enhetstester för kurvig väg (plangeometri, källpunkter, profilsnitt och vindprojektion)."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.testfall.kurvig_vag import (
    KurvigVagGeometri,
    MottagarPunkt,
    Punkt3D,
    berakna_kurvig_vag,
    kallpunkter_langs_korfalt,
    las_kurvig_vag_geometri,
    profil_for_strale,
    steglangd_for_position_m,
    vindkomponent_plan_m_s,
)
from buller.typer import ANTAL_BAND, Flyttal, Terrangprofil, Vader


class _KonstantKarna:
    namn = "test-konstant"
    nord2000_konform = False

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        return np.full(ANTAL_BAND, -40.0, dtype=np.float64)


class _KonstantEmission:
    @property
    def delkallhojder_m(self) -> tuple[float, ...]:
        return (0.01,)

    def ljudeffekt_db(
        self,
        trafik: object,
        segment_langd_m: float,
        period_s: float,
        delkallhojder_m: object = None,
        forhallanden: object = None,
    ) -> Flyttal:
        return np.full((1, ANTAL_BAND), 90.0 + 10.0 * math.log10(segment_langd_m), dtype=np.float64)


def _syntetisk_geo() -> KurvigVagGeometri:
    return KurvigVagGeometri(
        vagmitt=(Punkt3D(0.0, -500.0, 0.0), Punkt3D(0.0, 500.0, 0.0)),
        tunn_skarm=(Punkt3D(15.0, -50.0, 3.0), Punkt3D(15.0, 50.0, 3.0)),
        tjock_skarm_topp=(
            Punkt3D(30.0, 100.0, 3.0),
            Punkt3D(33.0, 100.0, 3.0),
            Punkt3D(33.0, 150.0, 3.0),
            Punkt3D(30.0, 150.0, 3.0),
            Punkt3D(30.0, 100.0, 3.0),
        ),
        tjock_skarm_bas=(
            Punkt3D(24.0, 100.0, 0.0),
            Punkt3D(39.0, 100.0, 0.0),
            Punkt3D(39.0, 150.0, 0.0),
            Punkt3D(24.0, 150.0, 0.0),
            Punkt3D(24.0, 100.0, 0.0),
        ),
        hojdkurvor=((Punkt3D(40.0, -200.0, 2.0), Punkt3D(40.0, 200.0, 2.0)),),
        mottagare={"1": MottagarPunkt("1", 50.0, 0.0, 3.5, 2.0)},
    )


class TestKurvigVag(unittest.TestCase):
    def test_vindkomponent_plan_matchar_rl18_s12(self) -> None:
        # φ_u = 315° (från NV, blåser mot 135° SO): medvind mot (+1, -1), motvind mot (-1, +1).
        self.assertAlmostEqual(vindkomponent_plan_m_s(3.0, 315.0, 100.0, -100.0), 3.0, places=12)
        self.assertAlmostEqual(vindkomponent_plan_m_s(3.0, 315.0, -100.0, 100.0), -3.0, places=12)
        self.assertAlmostEqual(vindkomponent_plan_m_s(-3.0, 315.0, 100.0, -100.0), -3.0, places=12)
        self.assertAlmostEqual(vindkomponent_plan_m_s(3.0, 315.0, 100.0, 100.0), 0.0, places=12)

    def test_steglangd_enligt_rl18(self) -> None:
        self.assertEqual(steglangd_for_position_m("5-1"), 10.0)
        self.assertEqual(steglangd_for_position_m("3"), 25.0)
        self.assertEqual(steglangd_for_position_m("1"), 50.0)

    def test_kallpunkter_och_profil_med_tunn_skarm_och_hojdkurva(self) -> None:
        geo = _syntetisk_geo()
        mott = geo.mottagare["1"]
        punkter = kallpunkter_langs_korfalt(geo.vagmitt, mott, 2.5, 50.0)
        self.assertGreater(len(punkter), 5)
        mitt = min(punkter, key=lambda p: abs(p.y_m))
        prof = profil_for_strale(geo, mitt, mott, 0.01, forenkla=False)
        # Profilen ska innehålla både skärmtoppen (z = 3 m) och höjdkurvan (z = 2 m).
        self.assertAlmostEqual(float(np.max(prof.z_m)), 3.0, places=6)
        self.assertAlmostEqual(float(prof.z_m[-1]), 2.0, places=6)
        self.assertEqual(float(prof.flodesresistivitet_kNsm4[0]), 20000.0)

    def test_las_kurvig_vag_geometri_syntetiskt_blad(self) -> None:
        celler = [
            ["Road centreline coordinates"],
            ["X", "Y", "Z"],
            ["0", "0", "0"],
            ["0", "100", "0"],
            [],
            ["Thin screen coordinates (top)"],
            ["X", "Y", "Z"],
            ["10", "20", "3"],
            ["10", "80", "3"],
            [],
            ["Thick screen coordinates (top)"],
            ["X", "Y", "Z"],
            ["20", "10", "3"],
            ["23", "10", "3"],
            [],
            ["Thick screen base coordinates (embankment case)"],
            ["X", "Y", "Z"],
            ["14", "10", "0"],
            ["29", "10", "0"],
            [],
            ["Terrain coordinates"],
            ["X", "Y", "Z"],
            ["30", "0", "1"],
            ["30", "100", "1"],
            [],
            ["Receiver coordinates"],
            ["X", "Y", "Z", "Z of terrain below receiver"],
            ["50", "50", "2.5", "1.0", "Pos 1"],
        ]
        geo = las_kurvig_vag_geometri(celler)
        self.assertEqual(len(geo.vagmitt), 2)
        self.assertEqual(len(geo.hojdkurvor), 1)
        self.assertIn("1", geo.mottagare)
        self.assertAlmostEqual(geo.mottagare["1"].hojd_over_mark_m, 1.5)

    def test_berakna_kurvig_vag_ger_andligt_resultat(self) -> None:
        geo = _syntetisk_geo()
        laeq, leq = berakna_kurvig_vag(
            _KonstantKarna(),
            _KonstantEmission(),
            geo,
            "1",
            Vader(),
            np.zeros(ANTAL_BAND),
            direktivitet=False,
        )
        self.assertTrue(math.isfinite(laeq))
        self.assertEqual(leq.shape, (ANTAL_BAND,))
        self.assertTrue(bool(np.all(np.isfinite(leq))))


if __name__ == "__main__":
    unittest.main()
