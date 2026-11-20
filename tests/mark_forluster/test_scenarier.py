"""Enhetstester för `mark_forluster.scenarier` (K5/v1, nodaggregering och känslighet)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from mark_forluster.brunnar import Brunn
from mark_forluster.parametrar import ForlustFel, GRAS, HARDGJORT
from mark_forluster.scenarier import (
    KONTRAKT_K5,
    ForlustInstallningar,
    ScenarioK5,
    berakna_forluster_for_scenario,
    jamfor_scenarier,
    las_scenario_k5,
    skriv_scenario_k5,
)


class TestScenarioK5(unittest.TestCase):
    def test_skriv_och_las_k5_validering(self) -> None:
        sc = ScenarioK5(
            regn="regn_100ar.csv",
            forluster=ForlustInstallningar(modell="green-ampt", initial_mm=2.0),
            grundvatten="normal",
            tjale=False,
            brunnar_igensatta_andel=0.25,
            ledningar_fulla_fran_start=False,
            slumpfro=42,
        )
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "scenario.json"
            skriv_scenario_k5(sc, fil)
            last = las_scenario_k5(fil)
            self.assertEqual(last, sc)
            self.assertEqual(last.kontrakt, KONTRAKT_K5)

        with self.assertRaises(ForlustFel):
            ScenarioK5(regn="regn.csv", kontrakt="K5/v99")
        with self.assertRaises(ForlustFel):
            ScenarioK5(regn="regn.csv", brunnar_igensatta_andel=1.5)
        with self.assertRaises(ForlustFel):
            ScenarioK5(regn="regn.csv", grundvatten="lag")

    def test_cell_och_nodaggregering_med_massbalans(self) -> None:
        # 4x4 celler, 1 m pixelstorlek => 16 m² totalt.
        # Vänster halva (nod 1): hårdgjort; höger halva (nod 2): gräs på morän.
        markslag = np.array(
            [
                [HARDGJORT, HARDGJORT, GRAS, GRAS],
                [HARDGJORT, HARDGJORT, GRAS, GRAS],
                [HARDGJORT, HARDGJORT, GRAS, GRAS],
                [HARDGJORT, HARDGJORT, GRAS, GRAS],
            ],
            dtype=np.uint8,
        )
        cell_till_nod = np.array(
            [
                [1, 1, 2, 2],
                [1, 1, 2, 2],
                [1, 1, 2, 2],
                [1, 1, 2, 2],
            ],
            dtype=np.int64,
        )
        dtw = np.full((4, 4), 1.0, dtype=np.float32)
        brunnar = [
            Brunn("B_SYN_1", e_m=100.5, n_m=199.5, kapacitet_m3_s=0.0001),
        ]
        tid = [0.0, 1800.0, 3600.0]
        intens = [60.0, 60.0, 60.0]

        sc = ScenarioK5(regn="syntet_60mm.csv")
        res = berakna_forluster_for_scenario(
            sc,
            markslag,
            "moran",
            tid,
            intens,
            pixelstorlek_m=1.0,
            dtw_m=dtw,
            brunnar=brunnar,
            cell_till_nod=cell_till_nod,
            e_min_m=100.0,
            n_max_m=200.0,
        )

        self.assertLess(res.max_massbalans_fel_m, 1e-6)
        self.assertEqual(set(res.nod_forluster.keys()), {1, 2})

        nod1 = res.nod_forluster[1]
        nod2 = res.nod_forluster[2]
        self.assertEqual(nod1.antal_celler, 8)
        self.assertAlmostEqual(nod1.regn_volym_m3, 8.0 * 0.060, places=6)
        self.assertAlmostEqual(
            nod1.total_forlust_m3 + nod1.netto_avrinning_m3,
            nod1.regn_volym_m3,
            places=6,
        )
        # Brunnen (0.0001 m³/s * 3600 s = 0.36 m³) ligger i nod 1 (0.464 m³ tillgängligt)
        self.assertAlmostEqual(nod1.brunns_avbordning_m3, 0.36, places=5)
        self.assertAlmostEqual(nod2.brunns_avbordning_m3, 0.0, places=5)
        # Nod 2 (gräs på morän) har mer markförlust än nod 1 (hårdgjort)
        self.assertGreater(nod2.mark_forlust_m3, nod1.mark_forlust_m3)

    def test_kanslighetsjamforelse_okar_avrinning_fysikaliskt(self) -> None:
        markslag = np.full((6, 6), GRAS, dtype=np.uint8)
        markslag[:, :2] = HARDGJORT
        dtw = np.full((6, 6), 0.15, dtype=np.float32)  # relativt grundvattennära
        brunnar = [Brunn("B_SYN", e_m=100.5, n_m=199.5, kapacitet_m3_s=0.0002)]
        cell_till_nod = np.ones((6, 6), dtype=np.int64)
        tid = [0.0, 1800.0, 3600.0]
        intens = [60.0, 60.0, 60.0]

        scenarier = [
            ("normal", ScenarioK5(regn="skyfall.csv", grundvatten="normal")),
            ("hog_gv", ScenarioK5(regn="skyfall.csv", grundvatten="hog")),
            (
                "tjale_fulla_ledningar",
                ScenarioK5(
                    regn="skyfall.csv",
                    grundvatten="hog",
                    tjale=True,
                    brunnar_igensatta_andel=0.5,
                    ledningar_fulla_fran_start=True,
                ),
            ),
        ]

        tabell = jamfor_scenarier(
            scenarier,
            markslag,
            "sand",
            tid,
            intens,
            pixelstorlek_m=1.0,
            dtw_m=dtw,
            brunnar=brunnar,
            cell_till_nod=cell_till_nod,
            e_min_m=100.0,
            n_max_m=200.0,
        )

        self.assertEqual([s.namn for s in tabell], ["normal", "hog_gv", "tjale_fulla_ledningar"])
        s_norm, s_hog, s_varst = tabell
        # Högre grundvatten ger mindre marklagring och större utströmningsandel
        self.assertLess(s_hog.mark_forlust_m3, s_norm.mark_forlust_m3)
        self.assertGreater(s_hog.netto_avrinning_m3, s_norm.netto_avrinning_m3)
        assert s_norm.utstromning_andel is not None
        assert s_hog.utstromning_andel is not None
        self.assertGreaterEqual(s_hog.utstromning_andel, s_norm.utstromning_andel)

        # Tjäle + fulla ledningar ger störst nettoavrinning och 0 brunnsavbördning
        self.assertAlmostEqual(s_varst.brunns_avbordning_m3, 0.0, places=6)
        self.assertGreater(s_varst.netto_avrinning_m3, s_hog.netto_avrinning_m3)
