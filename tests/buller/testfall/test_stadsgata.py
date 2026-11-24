"""Enhetstester för stadsgata (RL 18-18 kap. 4, N18S1–N18S4) med syntetisk geometri."""

from __future__ import annotations

import unittest

import numpy as np

from buller.akustik.band import a_vagning_db
from buller.emission.modell import Vagemission
from buller.nord2000.karna import Nord2000Karna
from buller.testfall.stadsgata import (
    ByggnadPlan,
    StadsgataGeometri,
    berakna_stadsgata,
    las_stadsgata_geometri,
)
from buller.typer import ANTAL_BAND, Vader


def _syntetisk_gata() -> StadsgataGeometri:
    return StadsgataGeometri(
        byggnader=(
            ByggnadPlan("Building #1", -90.0, -10.0, -500.0, 25.0),
            ByggnadPlan("Building #2", 10.0, 90.0, 5.0, 500.0),
            ByggnadPlan("Building #3", 10.0, 90.0, -500.0, -5.0),
            ByggnadPlan("Building #4", 110.0, 190.0, -500.0, 500.0),
        ),
        mottagare={
            "1": (10.0, 60.0),
            "3": (10.0, -150.0),
            "4": (110.0, -150.0),
        },
    )


class TestStadsgata(unittest.TestCase):
    def test_las_stadsgata_geometri(self) -> None:
        celler = [
            ["Coordinates of buildings and receivers"],
            ["X", "Y"],
            ["Building #1", "-90.0", "-100.0"],
            ["-90.0", "25.0"],
            ["-10.0", "25.0"],
            ["-10.0", "-100.0"],
            [],
            ["Receivers", "X", "Y"],
            ["10.0", "60.0"],
            ["10.0", "-150.0"],
        ]
        geo = las_stadsgata_geometri(celler)
        self.assertEqual(len(geo.byggnader), 1)
        self.assertEqual(geo.byggnader[0].x_min_m, -90.0)
        self.assertEqual(geo.byggnader[0].x_max_m, -10.0)
        self.assertEqual(geo.mottagare["1"], (10.0, 60.0))
        self.assertEqual(geo.mottagare["2"], (10.0, -150.0))

    def test_gaturum_med_reflektioner_hojer_nivan(self) -> None:
        geo = _syntetisk_gata()
        karna = Nord2000Karna()
        em = Vagemission()
        avag = np.asarray(a_vagning_db(), dtype=np.float64)
        vader = Vader(
            temperatur_c=15.0,
            relativ_fuktighet_procent=70.0,
            vind_m_s=0.0,
            zu_m=10.0,
            z0_m=0.05,
            dt_dz_k_m=0.0,
            std_vind_m_s=0.5,
            std_dt_dz_k_m=0.0,
            cv2=0.12,
            ct2=0.008,
        )
        la0, spec0 = berakna_stadsgata(karna, em, geo, "3", 1.5, 1.0, vader, avag, max_ordning=0)
        la2, spec2 = berakna_stadsgata(karna, em, geo, "3", 1.5, 1.0, vader, avag, max_ordning=2)
        self.assertEqual(spec0.shape, (ANTAL_BAND,))
        self.assertEqual(spec2.shape, (ANTAL_BAND,))
        # Reflektioner i slutet gaturum ger högre ljudnivå än enbart direktljud.
        self.assertGreater(la2, la0 + 1.0)

    def test_skarmad_parallellgata_far_tydligt_bidrag_av_andra_ordningens_reflektioner(
        self,
    ) -> None:
        # Kortare parallellgata för snabb enhetstest (samma tvärsnitt som Pos 4).
        geo = StadsgataGeometri(
            byggnader=(
                ByggnadPlan("Building #1", -90.0, -10.0, -400.0, 400.0),
                ByggnadPlan("Building #3", 10.0, 90.0, -400.0, 400.0),
                ByggnadPlan("Building #4", 110.0, 190.0, -400.0, 400.0),
            ),
            mottagare={"4": (110.0, 0.0)},
        )
        karna = Nord2000Karna()
        em = Vagemission()
        avag = np.asarray(a_vagning_db(), dtype=np.float64)
        vader = Vader(
            temperatur_c=15.0,
            relativ_fuktighet_procent=70.0,
            vind_m_s=0.0,
            zu_m=10.0,
            z0_m=0.05,
            dt_dz_k_m=0.0,
            std_vind_m_s=0.5,
            std_dt_dz_k_m=0.0,
            cv2=0.12,
            ct2=0.008,
        )
        la1, _ = berakna_stadsgata(karna, em, geo, "4", 1.5, 1.0, vader, avag, max_ordning=1)
        la2, _ = berakna_stadsgata(karna, em, geo, "4", 1.5, 1.0, vader, avag, max_ordning=2)
        # 2:a ordningens reflektioner i både käll- och mottagargaturummet höjer nivån > 1,5 dB.
        self.assertGreater(la2, la1 + 1.5)


if __name__ == "__main__":
    unittest.main()
