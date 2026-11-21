"""Tester för `Bullerscen`: validering, raster, väggar och K1-inläsning."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from buller.scen.bullerscen import Bullerscen, Vag, bullerscen_fran_k1_ruta
from buller.scen.syntetisk import syntetisk_scen
from buller.typer import BullerFel, Byggnad
from tests.buller.scen.referens import bilinjar_markhojd_m, punkt_i_nagon_byggnad


def _scen(**andringar: object) -> Bullerscen:
    indata: dict[str, object] = {
        "terrang_rh2000_m": np.arange(12, dtype=np.float64).reshape(3, 4),
        "markslag": np.full((3, 4), 4, dtype=np.uint8),
        "e_min_m": 100.0,
        "n_max_m": 230.0,
        "cell_m": 10.0,
    }
    indata.update(andringar)
    return Bullerscen(**indata)  # type: ignore[arg-type]


def _hus(namn: str, e0: float, n0: float, e1: float, n1: float, tak: float = 30.0) -> Byggnad:
    return Byggnad(namn, np.array([e0, e1, e1, e0]), np.array([n0, n0, n1, n1]), tak)


class TestRaster(unittest.TestCase):
    def test_bilinjar_kanda_svar(self) -> None:
        scen = _scen()
        # Cellmitt (rad 0, kolumn 0) ligger i E 105, N 225 och har värdet 0.
        self.assertAlmostEqual(float(scen.markhojd_m(np.array(105.0), np.array(225.0))), 0.0)
        # Cellmitt (rad 2, kolumn 3): E 135, N 205, värde 11.
        self.assertAlmostEqual(float(scen.markhojd_m(np.array(135.0), np.array(205.0))), 11.0)
        # Mitt emellan fyra cellmitt: medelvärdet av 0, 1, 4 och 5.
        self.assertAlmostEqual(float(scen.markhojd_m(np.array(110.0), np.array(220.0))), 2.5)
        # En kvarts cell österut från (rad 1, kolumn 1) = 5.
        self.assertAlmostEqual(float(scen.markhojd_m(np.array(117.5), np.array(215.0))), 5.25)
        # Yttersta halva cellen hålls konstant.
        self.assertAlmostEqual(float(scen.markhojd_m(np.array(100.0), np.array(230.0))), 0.0)
        self.assertAlmostEqual(float(scen.markhojd_m(np.array(140.0), np.array(200.0))), 11.0)

    def test_bilinjar_mot_skalar_referens(self) -> None:
        scen = syntetisk_scen(sida_m=100.0, cell_m=0.5, tomt_m=50.0, fro=3)
        slump = np.random.default_rng(5)
        e = scen.e_min_m + slump.uniform(0.0, 100.0, 500)
        n = scen.n_min_m + slump.uniform(0.0, 100.0, 500)
        vantat = [bilinjar_markhojd_m(scen, float(a), float(b)) for a, b in zip(e, n, strict=True)]
        np.testing.assert_allclose(scen.markhojd_m(e, n), vantat, atol=1e-10)

    def test_markslag_i_cell(self) -> None:
        markslag = np.arange(12, dtype=np.uint8).reshape(3, 4)
        scen = _scen(markslag=markslag)
        np.testing.assert_array_equal(
            scen.markslag_i(
                np.array([101.0, 139.0, 119.9, 120.1]), np.array([229.0, 201.0, 215.0, 215.0])
            ),
            [0, 11, 5, 6],
        )

    def test_utanfor_raster_ger_fel(self) -> None:
        scen = _scen()
        with self.assertRaisesRegex(BullerFel, "utanför"):
            scen.markhojd_m(np.array([99.9]), np.array([210.0]))
        with self.assertRaisesRegex(BullerFel, "utanför"):
            scen.markslag_i(np.array([120.0]), np.array([230.1]))

    def test_andel_okant(self) -> None:
        markslag = np.full((3, 4), 4, dtype=np.uint8)
        markslag[0, :3] = 0
        self.assertAlmostEqual(_scen(markslag=markslag).andel_okant_markslag(), 0.25)


class TestValidering(unittest.TestCase):
    """Acceptans (d): fel i indata ger `BullerFel`."""

    def test_raster(self) -> None:
        felaktiga: list[dict[str, object]] = [
            {"terrang_rh2000_m": np.zeros(5)},
            {"terrang_rh2000_m": np.zeros((1, 4)), "markslag": np.zeros((1, 4), dtype=np.uint8)},
            {"terrang_rh2000_m": np.full((3, 4), np.nan)},
            {"markslag": np.full((3, 4), 4, dtype=np.int32)},
            {"markslag": np.full((4, 3), 4, dtype=np.uint8)},
            {"cell_m": 0.0},
            {"cell_m": float("nan")},
            {"e_min_m": float("inf")},
            {"terrang_rh2000_m": "inte ett raster"},
        ]
        for andring in felaktiga:
            with self.subTest(andring=list(andring)), self.assertRaises(BullerFel):
                _scen(**andring)

    def test_byggnader(self) -> None:
        bra = _hus("a", 105.0, 205.0, 115.0, 215.0)
        felaktiga: list[tuple[Byggnad, ...]] = [
            (bra, _hus("a", 120.0, 205.0, 130.0, 215.0)),  # samma id
            (Byggnad("b", np.array([105.0, 115.0]), np.array([205.0, 215.0]), 30.0),),
            (Byggnad("b", np.array([105.0, 115.0, 115.0]), np.array([205.0, 205.0]), 30.0),),
            (
                Byggnad(
                    "b", np.array([105.0, np.nan, 115.0]), np.array([205.0, 205.0, 215.0]), 30.0
                ),
            ),
            (_hus("b", 105.0, 205.0, 115.0, 215.0, tak=float("nan")),),
            (_hus("b", 105.0, 205.0, 115.0, 215.0, tak=-5.0),),  # tak under marken
            (
                Byggnad(  # självskärande (fluga)
                    "b",
                    np.array([105.0, 115.0, 105.0, 115.0]),
                    np.array([205.0, 215.0, 215.0, 205.0]),
                    30.0,
                ),
            ),
            (
                Byggnad(  # upprepad startpunkt
                    "b",
                    np.array([105.0, 115.0, 115.0, 105.0]),
                    np.array([205.0, 205.0, 215.0, 205.0]),
                    30.0,
                ),
            ),
            (bra, _hus("b", 110.0, 205.0, 125.0, 215.0)),  # överlapp
            (Byggnad("b", bra.e_m, bra.n_m, 30.0, reflektionsforlust_db=-1.0),),
        ]
        for i, byggnader in enumerate(felaktiga):
            with self.subTest(fall=i), self.assertRaises(BullerFel):
                _scen(byggnader=byggnader)

    def test_hus_med_gemensam_vagg_godtas(self) -> None:
        scen = _scen(
            byggnader=(_hus("a", 105.0, 205.0, 115.0, 215.0), _hus("b", 115.0, 205.0, 125.0, 215.0))
        )
        self.assertEqual(len(scen.byggnader), 2)

    def test_vagar(self) -> None:
        felaktiga: list[tuple[Vag, ...]] = [
            (Vag("v", np.array([105.0]), np.array([205.0])),),
            (Vag("v", np.array([105.0, 110.0]), np.array([205.0])),),
            (Vag("v", np.array([105.0, np.inf]), np.array([205.0, 205.0])),),
            (Vag("v", np.array([105.0, 105.0, 110.0]), np.array([205.0, 205.0, 205.0])),),
            (
                Vag("v", np.array([105.0, 110.0]), np.array([205.0, 205.0])),
                Vag("v", np.array([105.0, 110.0]), np.array([215.0, 215.0])),
            ),
        ]
        for i, vagar in enumerate(felaktiga):
            with self.subTest(fall=i), self.assertRaises(BullerFel):
                _scen(vagar=vagar)
        # Samma väg-id med olika körfält är tillåtet.
        scen = _scen(
            vagar=(
                Vag("v", np.array([105.0, 110.0]), np.array([205.0, 205.0]), korfalt=0),
                Vag("v", np.array([105.0, 110.0]), np.array([215.0, 215.0]), korfalt=1),
            )
        )
        self.assertEqual(len(scen.vagar), 2)


class TestVaggarOchByggnader(unittest.TestCase):
    def test_normaler_pekar_utat_oavsett_varv(self) -> None:
        moturs = _hus("a", 105.0, 205.0, 115.0, 215.0)
        medurs = Byggnad("b", moturs.e_m[::-1] + 15.0, moturs.n_m[::-1], 30.0)
        scen = _scen(byggnader=(moturs, medurs))
        vaggar = scen.vaggar
        self.assertEqual(vaggar.antal, 8)
        mitt_e = 0.5 * (vaggar.a_e_m + vaggar.b_e_m) + 0.1 * vaggar.normal_e
        mitt_n = 0.5 * (vaggar.a_n_m + vaggar.b_n_m) + 0.1 * vaggar.normal_n
        self.assertFalse(bool(np.any(scen.i_byggnad(mitt_e, mitt_n))))
        inat_e = 0.5 * (vaggar.a_e_m + vaggar.b_e_m) - 0.1 * vaggar.normal_e
        inat_n = 0.5 * (vaggar.a_n_m + vaggar.b_n_m) - 0.1 * vaggar.normal_n
        np.testing.assert_array_equal(scen.byggnad_vid(inat_e, inat_n), vaggar.byggnad_index)
        np.testing.assert_allclose(np.hypot(vaggar.normal_e, vaggar.normal_n), 1.0)
        np.testing.assert_allclose(vaggar.langd_m, 10.0)

    def test_i_byggnad_mot_stralkastning(self) -> None:
        scen = syntetisk_scen(sida_m=150.0, cell_m=1.0, tomt_m=50.0, fro=4)
        slump = np.random.default_rng(9)
        e = scen.e_min_m + slump.uniform(0.0, 150.0, 2000)
        n = scen.n_min_m + slump.uniform(0.0, 150.0, 2000)
        vantat = [
            punkt_i_nagon_byggnad(scen, float(a), float(b)) for a, b in zip(e, n, strict=True)
        ]
        np.testing.assert_array_equal(scen.i_byggnad(e, n), vantat)
        self.assertGreater(sum(vantat), 50)

    def test_syntetisk_scen_ar_deterministisk(self) -> None:
        a = syntetisk_scen(sida_m=100.0, cell_m=1.0, tomt_m=50.0, fro=8)
        b = syntetisk_scen(sida_m=100.0, cell_m=1.0, tomt_m=50.0, fro=8)
        np.testing.assert_array_equal(a.terrang_rh2000_m, b.terrang_rh2000_m)
        np.testing.assert_array_equal(a.markslag, b.markslag)
        self.assertEqual([x.id for x in a.byggnader], [x.id for x in b.byggnader])
        for x, y in zip(a.byggnader, b.byggnader, strict=True):
            np.testing.assert_array_equal(x.e_m, y.e_m)
            self.assertEqual(x.tak_rh2000_m, y.tak_rh2000_m)


class TestK1Inlasning(unittest.TestCase):
    def test_las_ruta_och_fyll_under_byggnad(self) -> None:
        from testsvit.k1_io import skriv_geotiff

        # Lutande plan 10 + 0,1 · kolumn, med ett 4 × 4 cellers hus som står 8 m över planet.
        plan = np.tile(10.0 + 0.1 * np.arange(20), (16, 1)).astype(np.float32)
        mark = plan.copy()
        mark[6:10, 8:12] += 8.0
        markslag = np.full((16, 20), 4, dtype=np.uint8)
        markslag[6:10, 8:12] = 9
        with tempfile.TemporaryDirectory() as mapp:
            skriv_geotiff(Path(mapp) / "mark.tif", mark, x_min=150000.0, y_max=6580004.0)
            skriv_geotiff(Path(mapp) / "markslag.tif", markslag, x_min=150000.0, y_max=6580004.0)
            scen = bullerscen_fran_k1_ruta(mapp)
        self.assertEqual(scen.cell_m, 0.25)
        self.assertEqual(scen.e_min_m, 150000.0)
        self.assertEqual(scen.n_max_m, 6580004.0)
        self.assertEqual(scen.terrang_rh2000_m.dtype, np.float64)
        # Utanför huset är marken orörd; under huset är den utfylld nära planet.
        utanfor = markslag != 9
        np.testing.assert_array_equal(
            scen.terrang_rh2000_m[utanfor], plan[utanfor].astype(np.float64)
        )
        self.assertLess(float(np.max(np.abs(scen.terrang_rh2000_m - plan))), 0.2)
        np.testing.assert_array_equal(scen.markslag, markslag)

    def test_saknad_eller_trasig_ruta(self) -> None:
        from testsvit.k1_io import skriv_geotiff

        with tempfile.TemporaryDirectory() as mapp:
            with self.assertRaisesRegex(BullerFel, "gick inte att läsa"):
                bullerscen_fran_k1_ruta(mapp)
            skriv_geotiff(
                Path(mapp) / "mark.tif", np.zeros((4, 4), dtype=np.float32), x_min=0.0, y_max=1.0
            )
            skriv_geotiff(
                Path(mapp) / "markslag.tif", np.zeros((4, 5), dtype=np.uint8), x_min=0.0, y_max=1.0
            )
            with self.assertRaisesRegex(BullerFel, "olika rutnät"):
                bullerscen_fran_k1_ruta(mapp)
            skriv_geotiff(
                Path(mapp) / "markslag.tif",
                np.full((4, 4), 9, dtype=np.uint8),
                x_min=0.0,
                y_max=1.0,
            )
            with self.assertRaisesRegex(BullerFel, "ingen mark"):
                bullerscen_fran_k1_ruta(mapp)


if __name__ == "__main__":
    unittest.main()
