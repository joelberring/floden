"""Enhetstester för `markmodell.rutnat`."""

from __future__ import annotations

import unittest

import numpy as np

from markmodell.rutnat import Ruta, RutnatFel, kontrollera_skarv, skapa_rutnat


class TestRutnat(unittest.TestCase):
    def test_ruta_namn_och_bbox_enligt_k1(self) -> None:
        ruta = Ruta(e_min_m=150000.0, n_min_m=6576000.0)
        self.assertEqual(ruta.namn, "mm_150000_6576000")
        self.assertEqual(ruta.inre_bbox, (150000.0, 6576000.0, 150500.0, 6576500.0))
        self.assertEqual(ruta.yttre_bbox, (149975.0, 6575975.0, 150525.0, 6576525.0))
        # (500 + 2*25) / 0.25 = 2200
        self.assertEqual(ruta.form_rc, (2200, 2200))

    def test_koordinat_och_cell_tur_och_retur(self) -> None:
        ruta = Ruta(e_min_m=150000.0, n_min_m=6576000.0, storlek_m=10.0, overlapp_m=2.0)
        e_in, n_in = 150003.1, 6576007.4
        rad, kol = ruta.koordinat_till_cell(e_in, n_in)
        self.assertTrue(ruta.ar_inom_raster(rad, kol))
        e_ut, n_ut = ruta.cell_till_koordinat(rad, kol)
        self.assertAlmostEqual(e_ut, 150003.125, places=6)
        self.assertAlmostEqual(n_ut, 6576007.375, places=6)

    def test_skapa_rutnat_tacker_hela_omradet(self) -> None:
        bbox = (150120.0, 6576100.0, 150800.0, 6576700.0)
        rutor = skapa_rutnat(bbox)
        self.assertEqual(
            [r.namn for r in rutor],
            [
                "mm_150000_6576000",
                "mm_150500_6576000",
                "mm_150000_6576500",
                "mm_150500_6576500",
            ],
        )
        for r in rutor:
            self.assertEqual(r.overlapp_m, 25.0)
            self.assertEqual(r.upplosning_m, 0.25)

    def test_skarvkontroll_godkand_och_underkand(self) -> None:
        ruta_a = Ruta(e_min_m=150000.0, n_min_m=6576000.0, storlek_m=20.0, overlapp_m=2.0)
        ruta_b = Ruta(e_min_m=150020.0, n_min_m=6576000.0, storlek_m=20.0, overlapp_m=2.0)

        # Samma deterministiska lutande plan z = 10 + 0.01 * (E - 150000) på båda rutorna
        ea, _ = ruta_a.cell_centra_en()
        eb, _ = ruta_b.cell_centra_en()
        raster_a = np.broadcast_to(
            (10.0 + 0.01 * (ea - 150000.0)).astype(np.float32), ruta_a.form_rc
        ).copy()
        raster_b = np.broadcast_to(
            (10.0 + 0.01 * (eb - 150000.0)).astype(np.float32), ruta_b.form_rc
        ).copy()

        rapport_ok = kontrollera_skarv(ruta_a, raster_a, ruta_b, raster_b, max_avvikelse_m=0.005)
        self.assertTrue(rapport_ok.godkand)
        self.assertEqual(rapport_ok.antal_nan, 0)
        self.assertLess(rapport_ok.max_avvikelse_m, 1e-5)
        # Gemensam bredd i E = 4 m (16 kol), höjd i N = 24 m (96 rader) => 1536 celler
        self.assertEqual(rapport_ok.antal_gemensamma_celler, 1536)

        # Inför ett höjdsprång på 4 cm i överlappet på ruta B
        raster_b_skarv = raster_b.copy()
        raster_b_skarv[10, 2] += np.float32(0.04)
        rapport_fel = kontrollera_skarv(
            ruta_a, raster_a, ruta_b, raster_b_skarv, max_avvikelse_m=0.005
        )
        self.assertFalse(rapport_fel.godkand)
        self.assertGreater(rapport_fel.max_avvikelse_m, 0.039)

        # Inför ett hål (NaN) i överlappet på ruta A
        raster_a_nan = raster_a.copy()
        raster_a_nan[20, -3] = np.nan
        rapport_nan = kontrollera_skarv(
            ruta_a, raster_a_nan, ruta_b, raster_b, max_avvikelse_m=0.005
        )
        self.assertFalse(rapport_nan.godkand)
        self.assertEqual(rapport_nan.antal_nan, 1)

    def test_ogiltiga_rutparametrar_ger_fel(self) -> None:
        with self.assertRaises(RutnatFel):
            Ruta(e_min_m=0.0, n_min_m=0.0, storlek_m=-10.0)
        with self.assertRaises(RutnatFel):
            skapa_rutnat((100.0, 100.0, 50.0, 200.0))


if __name__ == "__main__":
    unittest.main()
