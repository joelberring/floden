"""LAZ via lasclip (lyft från O-Tid) och intensitet i LAS-läsaren."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from data_inlasning import DataInlasningFel
from data_inlasning import lasclip as lasclip_modul
from data_inlasning.lantmateriet import las_las_punktmoln, skriv_syntetisk_las
from data_inlasning.lasclip import hitta_lasclip, klipp_laser, las_laser_punktmoln


def _lasclip_finns() -> bool:
    try:
        hitta_lasclip()
    except DataInlasningFel:
        return False
    return True


E0, N0 = 660000.0, 6570000.0


def _skriv_testfil(mal: Path) -> None:
    e = E0 + np.array([1.0, 5.0, 9.0, 15.0])
    n = N0 + np.array([1.0, 5.0, 9.0, 15.0])
    z = np.array([20.0, 21.5, 22.0, 30.0])
    k = np.array([2, 2, 5, 9], dtype=np.uint8)
    i = np.array([120, 2048, 65000, 7], dtype=np.uint16)
    skriv_syntetisk_las(mal, e, n, z, k, i)


class TestIntensitet(unittest.TestCase):
    def test_intensitet_lases_ur_las(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "a.las"
            _skriv_testfil(fil)
            moln = las_las_punktmoln(fil, kall_epsg=3011)
            assert moln.intensitet is not None
            np.testing.assert_array_equal(moln.intensitet, [120, 2048, 65000, 7])
            np.testing.assert_array_equal(moln.klass, [2, 2, 5, 9])

    def test_fel_langd_pa_intensitet_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(DataInlasningFel):
            skriv_syntetisk_las(
                Path(tmp) / "b.las",
                np.zeros(2),
                np.zeros(2),
                np.zeros(2),
                np.zeros(2, dtype=np.uint8),
                np.zeros(3, dtype=np.uint16),
            )


class TestLasclipSaknas(unittest.TestCase):
    def test_tydligt_fel_utan_binar(self) -> None:
        with (
            mock.patch.dict(os.environ, {"FLODEN_LASCLIP": ""}),
            mock.patch.object(lasclip_modul, "_REPO_BINAR", Path("/finns/inte/lasclip")),
            mock.patch.object(lasclip_modul.shutil, "which", return_value=None),
            self.assertRaises(DataInlasningFel) as ctx,
        ):
            hitta_lasclip("/finns/inte/heller")
        self.assertIn("cargo build", str(ctx.exception))

    def test_tom_ruta_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(DataInlasningFel):
            klipp_laser(Path(tmp) / "x.las", Path(tmp) / "y.las", (1.0, 1.0, 1.0, 2.0))


@unittest.skipUnless(_lasclip_finns(), "lasclip är inte byggt (cargo build i tools/lasclip)")
class TestLasclipLaz(unittest.TestCase):
    def test_las_till_laz_och_tillbaka_med_klippning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a, laz = Path(tmp) / "a.las", Path(tmp) / "b.laz"
            _skriv_testfil(a)
            self.assertEqual(klipp_laser(a, laz, (E0, N0, E0 + 20, N0 + 20)), 4)
            # Punktformatets bit 7 är satt i en LAZ-fil.
            self.assertGreaterEqual(laz.read_bytes()[104], 128)
            with self.assertRaises(DataInlasningFel):
                las_las_punktmoln(laz, kall_epsg=3011)

            moln = las_laser_punktmoln(laz, (E0, N0, E0 + 10, N0 + 10), kall_epsg=3011)
            np.testing.assert_allclose(moln.e_m, E0 + np.array([1.0, 5.0, 9.0]), atol=1e-3)
            np.testing.assert_allclose(moln.z_rh2000, [20.0, 21.5, 22.0], atol=1e-3)
            np.testing.assert_array_equal(moln.klass, [2, 2, 5])
            assert moln.intensitet is not None
            np.testing.assert_array_equal(moln.intensitet, [120, 2048, 65000])
            self.assertEqual(moln.kalla, str(laz))

    def test_inga_punkter_i_rutan_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.las"
            _skriv_testfil(a)
            with self.assertRaises(DataInlasningFel):
                las_laser_punktmoln(a, (E0 + 100, N0 + 100, E0 + 110, N0 + 110), kall_epsg=3011)

    def test_transformation_vid_inlasning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.las"
            _skriv_testfil(a)

            def flytta(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
                return x - 1000.0, y + 10.0

            moln = las_laser_punktmoln(
                a, (E0, N0, E0 + 2, N0 + 2), kall_epsg=3006, transformator=flytta
            )
            np.testing.assert_allclose(moln.e_m, [E0 + 1 - 1000.0], atol=1e-3)
            np.testing.assert_allclose(moln.n_m, [N0 + 1 + 10.0], atol=1e-3)


if __name__ == "__main__":
    unittest.main()
