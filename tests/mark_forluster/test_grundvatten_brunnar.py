"""Enhetstester för `mark_forluster.grundvatten` och `mark_forluster.brunnar`."""

from __future__ import annotations

import unittest

import numpy as np

from mark_forluster.brunnar import (
    Brunn,
    DelomradeKapacitet,
    berakna_brunnsavbordning,
)
from mark_forluster.grundvatten import (
    INSTROMNING,
    UTSTROMNING,
    ObservationsRor,
    berakna_grundvattentak,
    kalibrera_dtw_mot_ror,
    klassificera_in_utstromning,
    uppskatta_dtw_fran_terrang,
)
from mark_forluster.parametrar import ForlustFel


class TestGrundvatten(unittest.TestCase):
    def test_grundvattentak_normal_och_hog(self) -> None:
        dtw = np.array([[0.2, 1.0], [2.0, 0.0]], dtype=np.float32)
        por = np.array([[0.4, 0.4], [0.3, 0.4]], dtype=np.float32)

        tak_norm = berakna_grundvattentak(dtw, por, lage="normal")
        self.assertAlmostEqual(float(tak_norm[0, 0]), 0.08, places=6)
        self.assertAlmostEqual(float(tak_norm[0, 1]), 0.40, places=6)
        self.assertAlmostEqual(float(tak_norm[1, 0]), 0.60, places=6)
        self.assertAlmostEqual(float(tak_norm[1, 1]), 0.0, places=6)

        tak_hog = berakna_grundvattentak(dtw, por, lage="hog", hog_faktor=0.5)
        self.assertAlmostEqual(float(tak_hog[0, 1]), 0.20, places=6)
        self.assertTrue(np.all(tak_hog <= tak_norm))

        with self.assertRaises(ForlustFel):
            berakna_grundvattentak(dtw, por, lage="okant")

    def test_klassificera_in_utstromning(self) -> None:
        dtw = np.array([[0.0, 0.49], [0.51, 2.5]], dtype=np.float32)
        karta = klassificera_in_utstromning(dtw, utstromning_grans_m=0.5)
        self.assertEqual(int(karta[0, 0]), UTSTROMNING)
        self.assertEqual(int(karta[0, 1]), UTSTROMNING)
        self.assertEqual(int(karta[1, 0]), INSTROMNING)
        self.assertEqual(int(karta[1, 1]), INSTROMNING)

    def test_uppskatta_dtw_och_kalibrera_mot_ror(self) -> None:
        # Lutande plan från dike längst västerut (kol 0, z = 10.0) upp till z = 12.0
        kol_x = np.arange(5, dtype=np.float64) * 0.5
        mark_z = np.broadcast_to(10.0 + kol_x, (4, 5)).astype(np.float32)
        vatten_mask = np.zeros((4, 5), dtype=bool)
        vatten_mask[:, 0] = True

        dtw = uppskatta_dtw_fran_terrang(mark_z, vatten_mask, pixelstorlek_m=1.0)
        self.assertAlmostEqual(float(dtw[0, 0]), 0.0, places=6)
        self.assertAlmostEqual(float(dtw[0, 4]), 2.0, places=5)

        # Observationsrör vid cell (rad=1, kol=4): z=12.0, uppmätt gv=10.5 => uppmätt DTW=1.5
        # Okalibrerat DTW=2.0 => offset = -0.5 m
        ror = [
            ObservationsRor(
                ror_id="R1",
                e_m=104.5,
                n_m=98.5,
                grundvatten_rh2000=10.5,
            )
        ]
        kalibrerad, offset = kalibrera_dtw_mot_ror(
            dtw, mark_z, ror, e_min_m=100.0, n_max_m=100.0, pixelstorlek_m=1.0
        )
        self.assertAlmostEqual(offset, -0.5, places=5)
        self.assertAlmostEqual(float(kalibrerad[1, 4]), 1.5, places=5)
        self.assertAlmostEqual(float(kalibrerad[0, 0]), 0.0, places=5)


class TestBrunnar(unittest.TestCase):
    def test_syntetiska_brunnar_igensattning_och_fulla_ledningar(self) -> None:
        brunnar = [
            Brunn("B1", e_m=150005.0, n_m=6576005.0, typ="ranni_standard", delomrade_id="D1"),
            Brunn("B2", e_m=150015.0, n_m=6576005.0, typ="kupolbrunn", delomrade_id="D1"),
        ]
        # 0.015 + 0.025 = 0.040 m³/s * 1000 s = 40 m³
        res_0 = berakna_brunnsavbordning(brunnar, varaktighet_s=1000.0, igensatta_andel=0.0)
        self.assertAlmostEqual(res_0.total_kapacitet_m3_s, 0.040, places=9)
        self.assertAlmostEqual(res_0.total_bortledd_volym_m3, 40.0, places=6)

        # 50 % igensättning => hälften
        res_50 = berakna_brunnsavbordning(brunnar, varaktighet_s=1000.0, igensatta_andel=0.5)
        self.assertAlmostEqual(res_50.total_kapacitet_m3_s, 0.020, places=9)
        self.assertAlmostEqual(res_50.total_bortledd_volym_m3, 20.0, places=6)

        # Fulla ledningar från start => 0 m³/s
        res_full = berakna_brunnsavbordning(
            brunnar, varaktighet_s=1000.0, igensatta_andel=0.0, ledningar_fulla=True
        )
        self.assertAlmostEqual(res_full.total_kapacitet_m3_s, 0.0, places=9)
        self.assertAlmostEqual(res_full.total_bortledd_volym_m3, 0.0, places=6)

    def test_delomradestak_begransar_summan(self) -> None:
        brunnar = [
            Brunn("B1", e_m=150001.0, n_m=6576001.0, kapacitet_m3_s=0.030, delomrade_id="D1"),
            Brunn("B2", e_m=150002.0, n_m=6576002.0, kapacitet_m3_s=0.030, delomrade_id="D1"),
        ]
        delomraden = [DelomradeKapacitet(delomrade_id="D1", max_flode_m3_s=0.030)]
        res = berakna_brunnsavbordning(brunnar, varaktighet_s=600.0, delomraden=delomraden)
        self.assertAlmostEqual(res.total_kapacitet_m3_s, 0.030, places=9)
        self.assertAlmostEqual(res.delomrade_utnyttjat_m3_s["D1"], 0.030, places=9)
        self.assertAlmostEqual(res.effektiv_kapacitet_m3_s["B1"], 0.015, places=9)
        self.assertAlmostEqual(res.effektiv_kapacitet_m3_s["B2"], 0.015, places=9)

    def test_ogiltiga_brunnsvarden_ger_fel(self) -> None:
        with self.assertRaises(ForlustFel):
            Brunn("B1", e_m=150000.0, n_m=6576000.0, typ="okand_brunn")
        with self.assertRaises(ForlustFel):
            berakna_brunnsavbordning([], varaktighet_s=100.0, igensatta_andel=1.2)
