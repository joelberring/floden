"""K1-raster som markmodellens skrivare skriver (deflate) ska gå att läsa med `las_geotiff`.

Tidigare läste `testsvit.k1_io.las_geotiff` bara okomprimerade TIFF, medan
`markmodell.k1_skrivare.skriv_geotiff` alltid skriver deflate. Sänkgraf, CLI och webb
kunde därför inte läsa rutor byggda av markmodellen.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from markmodell import k1_skrivare
from markmodell.rutnat import Ruta
from testsvit.k1_io import las_geotiff


def _ruta() -> Ruta:
    # Liten ruta: 20 m + 2·2 m överlapp vid 0,5 m ger 48 × 48 celler.
    return Ruta(
        e_min_m=153000.0, n_min_m=6574000.0, storlek_m=20.0, overlapp_m=2.0, upplosning_m=0.5
    )


class TestDeflateRundtur(unittest.TestCase):
    def _rundtur(self, med_rasterio: bool) -> None:
        ruta = _ruta()
        rng = np.random.default_rng(7)
        hojd = rng.normal(10.0, 2.0, size=ruta.form_rc).astype(np.float32)
        slag = rng.integers(0, 10, size=ruta.form_rc).astype(np.uint8)
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                k1_skrivare, "_HAR_RASTERIO", med_rasterio and k1_skrivare._HAR_RASTERIO
            ),
        ):
            p_h = k1_skrivare.skriv_geotiff(Path(tmp) / "mark.tif", hojd, ruta, nodata=-9999.0)
            p_s = k1_skrivare.skriv_geotiff(Path(tmp) / "markslag.tif", slag, ruta, nodata=0)
            ut_h, info = las_geotiff(p_h)
            ut_s, _ = las_geotiff(p_s)
        np.testing.assert_array_equal(ut_h, hojd)
        np.testing.assert_array_equal(ut_s, slag)
        e_min, _, _, n_max = ruta.yttre_bbox
        self.assertEqual((info.bredd, info.hojd), (ruta.form_rc[1], ruta.form_rc[0]))
        self.assertAlmostEqual(info.x_min, e_min)
        self.assertAlmostEqual(info.y_max, n_max)
        self.assertAlmostEqual(info.pixelstorlek_x, 0.5)
        self.assertEqual(info.epsg, 3011)

    def test_ren_python_skrivare(self) -> None:
        self._rundtur(med_rasterio=False)

    @unittest.skipUnless(k1_skrivare._HAR_RASTERIO, "rasterio saknas")
    def test_rasterio_skrivare(self) -> None:
        self._rundtur(med_rasterio=True)


if __name__ == "__main__":
    unittest.main()
