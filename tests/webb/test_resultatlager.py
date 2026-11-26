"""Rastersammanfogning och färgläggning för webbens förhandsvisning."""

from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from testsvit.k1_io import skriv_geotiff
from webb.resultatlager import (
    LagerFel,
    etikett_och_enhet,
    rita_png,
    sammanfoga,
    teckenforklaring,
    varde_intervall,
)


def _tif(mapp: Path, namn: str, varde: float, x: float, y_max: float, n: int = 10) -> Path:
    return skriv_geotiff(
        mapp / namn,
        np.full((n, n), varde, dtype=np.float32),
        x_min=x,
        y_max=y_max,
        pixelstorlek=1.0,
    )


class TestSammanfoga(unittest.TestCase):
    def test_rutor_utan_overlapp_laggs_pa_ratt_plats(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            a = _tif(Path(t), "a.tif", 1.0, 100.0, 210.0)  # väster, norr om b
            b = _tif(Path(t), "b.tif", 2.0, 110.0, 210.0)  # öster
            m = sammanfoga([a, b])
        self.assertEqual(m.raster.shape, (10, 20))
        self.assertEqual(
            m.utbredning.som_dict(),
            {"x_min": 100.0, "y_min": 200.0, "x_max": 120.0, "y_max": 210.0},
        )
        self.assertTrue((m.raster[:, :10] == 1.0).all() and (m.raster[:, 10:] == 2.0).all())

    def test_overlapp_ager_den_ruta_dar_cellen_ligger_djupast_inne(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            a = _tif(Path(t), "a.tif", 1.0, 100.0, 110.0)  # x 100-110
            b = _tif(Path(t), "b.tif", 2.0, 106.0, 110.0)  # x 106-116, överlapp 106-110
            m = sammanfoga([a, b])
        rad = m.raster[0]
        self.assertEqual(list(rad[:8]), [1.0] * 8)  # 100-108: a djupast (mitt 107,5 är jämnt)
        self.assertEqual(rad[-1], 2.0)
        self.assertFalse(np.isnan(m.raster).any())

    def test_olika_upplosning_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            a = _tif(Path(t), "a.tif", 1.0, 0.0, 10.0)
            b = skriv_geotiff(
                Path(t) / "b.tif",
                np.zeros((5, 5), np.float32),
                x_min=10.0,
                y_max=10.0,
                pixelstorlek=2.0,
            )
            with self.assertRaises(LagerFel):
                sammanfoga([a, b])


class TestRita(unittest.TestCase):
    def _bild(self, png: bytes) -> np.ndarray[Any, Any]:
        return np.asarray(Image.open(BytesIO(png)).convert("RGBA"))

    def test_nan_ar_genomskinligt_och_skalan_ar_gemensam(self) -> None:
        v = np.array([[0.0, 1.0], [2.0, np.nan]], dtype=np.float32)
        png, b, h, faktor = rita_png(v, "sekventiell", lo=0.0, hi=2.0)
        bild = self._bild(png)
        self.assertEqual((b, h, faktor), (2, 2, 1))
        self.assertEqual(list(bild[..., 3].ravel()), [255, 255, 255, 0])
        self.assertEqual(tuple(bild[0, 0, :3]), (0x00, 0x20, 0x4D))  # lägsta färgstoppet
        self.assertEqual(tuple(bild[1, 0, :3]), (0xFF, 0xEA, 0x46))  # högsta

    def test_vatten_klassas_efter_djup(self) -> None:
        v = np.array([[0.0, 0.05, 0.15, 0.6, 1.2]], dtype=np.float32)
        bild = self._bild(rita_png(v, "vatten", lo=0.0, hi=1.2)[0])
        self.assertEqual(list(bild[0, :, 3]), [0, 255, 255, 255, 255])
        self.assertEqual(tuple(bild[0, 1, :3]), (0xCF, 0xE8, 0xFF))
        self.assertEqual(tuple(bild[0, 2, :3]), (0x7E, 0xC4, 0xF8))
        self.assertEqual(tuple(bild[0, 3, :3]), (0x18, 0x2B, 0x7A))
        self.assertEqual(tuple(bild[0, 4, :3]), (0x3B, 0x13, 0x63))

    def test_skillnad_transparent_under_troskeln_och_blatt_for_minskning(self) -> None:
        v = np.array([[0.0, 0.05, -2.0, 2.0]], dtype=np.float32)
        bild = self._bild(rita_png(v, "skillnad", lo=0.0, hi=2.0, troskel=0.1)[0])
        self.assertEqual(list(bild[0, :, 3]), [0, 0, 255, 255])
        self.assertGreater(bild[0, 2, 2], bild[0, 2, 0])  # blå: B > R
        self.assertGreater(bild[0, 3, 0], bild[0, 3, 2])  # röd: R > B

    def test_stora_lager_nedsamplas(self) -> None:
        v = np.zeros((4100, 100), dtype=np.float32)
        _, b, h, faktor = rita_png(v, "sekventiell", lo=0.0, hi=1.0)
        self.assertEqual(faktor, 3)
        self.assertLessEqual(max(b, h), 2048)

    def test_okand_farg_och_intervall(self) -> None:
        with self.assertRaises(LagerFel):
            rita_png(np.zeros((2, 2), np.float32), "regnbage", lo=0.0, hi=1.0)
        self.assertEqual(varde_intervall([np.array([np.nan], dtype=np.float32)]), (0.0, 0.0))
        self.assertEqual(teckenforklaring("sekventiell", 0.0, 1.0)["typ"], "kontinuerlig")

    def test_ankomsttid_har_enhet_sekunder(self) -> None:
        # W-B6: kärnan skriver tid_till_0_1m_s i sekunder (s), inte minuter.
        self.assertEqual(etikett_och_enhet("ankomsttid"), ("Tid till vattnet kommer", "s"))


if __name__ == "__main__":
    unittest.main()
