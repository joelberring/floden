"""Enhetstester för kopplingen mellan T06 markförluster och T03 dynamik/CDS-regn."""

from __future__ import annotations

import unittest

import numpy as np

from dynamik.nodmodell import skapa_regn_intervall
from gemensamt.regn import hamta_regnforval
from mark_forluster.koppling import berakna_effektivt_regn_k5, k4_till_strikt_intervallserie
from mark_forluster.parametrar import GRAS, HARDGJORT


class TestMarkForlusterKoppling(unittest.TestCase):
    def test_cds_stockholmsregn_med_green_ampt_bevarar_exakt_volym(self) -> None:
        regn = hamta_regnforval("stockholm_cds_6h_100ar_kf124").generera()
        markslag = np.full((10, 10), GRAS, dtype=np.uint8)
        markslag[:, :5] = HARDGJORT

        res = berakna_effektivt_regn_k5(
            markslag,
            regn.tid_s,
            regn.intensitet_mm_h,
            jordart="sand",
            grundvatten="normal",
            initial_mm=2.0,
        )
        self.assertAlmostEqual(res.brutto_regn_mm, regn.total_djup_mm, places=8)
        self.assertGreater(res.infiltration_medel_mm, 10.0)
        self.assertLess(res.netto_avrinning_medel_mm, res.brutto_regn_mm)
        self.assertGreater(res.effektiv_avrinningskoefficient, 0.3)
        self.assertLess(res.effektiv_avrinningskoefficient, 0.95)

        _, integrerad_m = skapa_regn_intervall(
            res.effektiv_regn_tid_s,
            res.effektiv_regn_intensitet_mm_h,
        )
        self.assertAlmostEqual(integrerad_m * 1000.0, res.netto_avrinning_medel_mm, places=8)

    def test_tjale_och_hogt_grundvatten_okar_avrinning(self) -> None:
        regn = hamta_regnforval("stockholm_cds_6h_100ar_kf140").generera()
        markslag = np.full((8, 8), GRAS, dtype=np.uint8)
        mark_z = np.linspace(10.0, 12.0, 64, dtype=np.float32).reshape(8, 8)

        normal = berakna_effektivt_regn_k5(
            markslag,
            regn.tid_s,
            regn.intensitet_mm_h,
            jordart="moran",
            grundvatten="normal",
            tjale=False,
            mark_z=mark_z,
        )
        tjale_hog = berakna_effektivt_regn_k5(
            markslag,
            regn.tid_s,
            regn.intensitet_mm_h,
            jordart="moran",
            grundvatten="hog",
            tjale=True,
            mark_z=mark_z,
        )
        self.assertGreater(
            tjale_hog.netto_avrinning_medel_mm,
            normal.netto_avrinning_medel_mm,
        )

    def test_k4_till_strikt_intervallserie_rensar_nolllangdssteg(self) -> None:
        t, i = k4_till_strikt_intervallserie(
            [0.0, 300.0, 300.0, 600.0],
            [20.0, 20.0, 80.0, 80.0],
        )
        self.assertEqual(t, [0.0, 300.0, 600.0])
        self.assertEqual(i, [20.0, 80.0, 80.0])

    def test_cds_intensitetstopp_ligger_nara_r_037_inte_sista_steget(self) -> None:
        regn = hamta_regnforval("stockholm_cds_6h_100ar_kf124").generera()
        markslag = np.full((8, 8), GRAS, dtype=np.uint8)
        markslag[:, :4] = HARDGJORT

        res = berakna_effektivt_regn_k5(
            markslag,
            regn.tid_s,
            regn.intensitet_mm_h,
            jordart="moran",
            grundvatten="normal",
            initial_mm=2.0,
        )
        max_idx = int(np.argmax(res.effektiv_regn_intensitet_mm_h))
        t_topp = res.effektiv_regn_tid_s[max_idx]
        t_slut = res.effektiv_regn_tid_s[-1]
        r_topp = t_topp / t_slut
        self.assertGreater(r_topp, 0.25)
        self.assertLess(r_topp, 0.50)
        self.assertLess(max_idx, len(res.effektiv_regn_intensitet_mm_h) - 2)


if __name__ == "__main__":
    unittest.main()
