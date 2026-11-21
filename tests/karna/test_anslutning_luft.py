"""Luft på gemensam geometri (K1 v2): samma voxelrutnät som gamla vägen, K9 ändrar bara berörda
voxlar.

Kända svar: ett syntetiskt kvarter byggt med markmodellens egna byggare (`dagsljus.exempelkvarter`,
platt mark på 0 m, rektanglar på jämna 2 m) ger exakt samma höjdraster som ett handbyggt raster, och
`luft.geometri.voxelisera` ger då bit för bit samma `Voxelgrid` via båda vägarna.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from dagsljus.exempelkvarter import (
    bygg_rutnat,
    byggnads_id,
    polygon_andring,
    rektangel,
    skriv_k9,
)
from karna.geometri import GeometriFel, las_markklasser
from karna.geometri.luftgeometri import lad_fran_markslag, luft_geometri, markens_z0_m
from karna.geometri.mosaik import bygg_ytmosaik
from karna.resultatcache import ResultatCache
from karna.rutscenario import materialisera_scenario
from karna.scenario import Andring
from karna.utredning import ScenarioSpec
from luft.geometri import voxelisera
from luft.kedja import Domanspec

E0, N0, RUTA_M, ANTAL, CELL = 150000.0, 6576000.0, 40.0, 2, 2.0
SIDA = RUTA_M * ANTAL
HUS = {  # id: (x0, x1, y0, y1, höjd) relativt E0/N0, alla på jämna 2 m
    "a": (10.0, 30.0, 10.0, 20.0, 12.0),
    "b": (44.0, 60.0, 50.0, 70.0, 20.0),
    "c": (46.0, 54.0, 6.0, 14.0, 8.0),
}
VEG = (20.0, 28.0, 54.0, 62.0, 6.0)  # (x0, x1, y0, y1, höjd) vegetationsfläck


def _gammalt_raster(
    hus: dict[str, tuple[float, float, float, float, float]],
) -> NDArray[np.float64]:
    """Gamla vägen: handbyggt höjdraster (rad 0 = norr) på 2 m."""
    n = int(SIDA / CELL)
    h = np.zeros((n, n))
    for x0, x1, y0, y1, hojd in hus.values():
        h[int((SIDA - y1) / CELL) : int((SIDA - y0) / CELL), int(x0 / CELL) : int(x1 / CELL)] = hojd
    return h


def _veg_raster() -> NDArray[np.float64]:
    n = int(SIDA / CELL)
    v = np.zeros((n, n))
    x0, x1, y0, y1, h = VEG
    v[int((SIDA - y1) / CELL) : int((SIDA - y0) / CELL), int(x0 / CELL) : int(x1 / CELL)] = h
    return v


def _veg_per_ruta(ruta: object) -> NDArray[np.float32]:
    """Vegetationsfläcken på 1 m-rutnätet för en ruta (K1 v2 `vegetation.tif`)."""
    r = ruta
    e0, n0 = r.e_min_m, r.n_min_m  # type: ignore[attr-defined]
    n = int(r.storlek_m)  # type: ignore[attr-defined]
    v = np.zeros((n, n), dtype=np.float32)
    x0, x1, y0, y1, h = VEG
    for j in range(n):
        for i in range(n):
            x, y = e0 + i + 0.5 - E0, n0 + (n - j - 1) + 0.5 - N0
            if x0 <= x < x1 and y0 <= y < y1:
                v[j, i] = h
    return v


class _Bas(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        kand = [
            rektangel(E0 + a, E0 + b, N0 + c, N0 + d, h, k) for k, (a, b, c, d, h) in HUS.items()
        ]
        cls.bas = bygg_rutnat(
            cls.tmp / "bas",
            kand,
            e_min_m=E0,
            n_min_m=N0,
            antal=ANTAL,
            rutstorlek_m=RUTA_M,
            vegetation_hojd_m=_veg_per_ruta,
        )
        cls.rutor = sorted(cls.bas.glob("mm_*"))
        cls.cache = ResultatCache(cls.tmp / "cache")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def scenario_rutor(self, namn: str, andringar: list[Andring]) -> list[Path]:
        k9 = skriv_k9(self.tmp / f"{namn}.json", self.bas, namn, andringar)
        sc = materialisera_scenario(ScenarioSpec(namn, self.bas, k9=(k9,)), self.cache)
        return [r.mapp for r in sc.rutor]

    @staticmethod
    def vox(spec: Domanspec, riktning: float = 270.0):  # noqa: ANN205
        return voxelisera(
            spec.hojd_over_mark_m,
            spec.dx_m,
            spec.hojd_doman_m,
            raster_dx_m=spec.raster_dx_m,
            raster_origo_x_m=spec.raster_origo_x_m,
            raster_origo_y_m=spec.raster_origo_y_m,
            buffert_uppstroms_m=spec.buffert_uppstroms_m,
            buffert_nedstroms_m=spec.buffert_nedstroms_m,
            buffert_sidor_m=spec.buffert_sidor_m,
            vegetation_hojd_m=spec.vegetation_hojd_m,
            lad_m2_m3=spec.lad_m2_m3,
            vindriktning_grad=riktning,
        )


class TestIdentiskGeometri(_Bas):
    def test_hojdraster_och_voxelrutnat_lika_via_k1_v2_och_gamla_vagen(self) -> None:
        g = luft_geometri(self.rutor, dx_m=CELL, hojd_doman_m=40.0, vegetation=True)
        gammal = _gammalt_raster(HUS)
        np.testing.assert_array_equal(g.spec.hojd_over_mark_m, gammal)
        np.testing.assert_array_equal(g.spec.vegetation_hojd_m, _veg_raster())
        self.assertEqual((g.spec.raster_origo_x_m, g.spec.raster_origo_y_m), (E0, N0 + SIDA))
        for riktning in (270.0, 225.0, 0.0):
            ny = self.vox(g.spec, riktning)
            # gamla vägen: samma Domanspec men handbyggda raster och luftens skalära standard-LAD
            # 1.0
            gl = voxelisera(
                gammal,
                CELL,
                40.0,
                raster_dx_m=CELL,
                raster_origo_x_m=E0,
                raster_origo_y_m=N0 + SIDA,
                buffert_uppstroms_m=80.0,
                buffert_nedstroms_m=160.0,
                buffert_sidor_m=60.0,
                vegetation_hojd_m=_veg_raster(),
                lad_m2_m3=1.0,
                vindriktning_grad=riktning,
            )
            self.assertEqual(ny.form, gl.form)
            np.testing.assert_array_equal(ny.fast, gl.fast)
            np.testing.assert_array_equal(ny.lad_m2_m3, gl.lad_m2_m3)
            self.assertEqual((ny.origo_x_m, ny.origo_y_m), (gl.origo_x_m, gl.origo_y_m))
            self.assertGreater(int(ny.fast.sum()), 0)
            self.assertGreater(float(ny.lad_m2_m3.sum()), 0.0)

    def test_omsampling_max_och_medel_pa_grovre_rutnat(self) -> None:
        """Ett 4 m-rutnät över ett hus som täcker halva blocket: medel = halva höjden, max =
        höjden."""
        kvarter = {"d": (10.0, 12.0, 8.0, 12.0, 10.0)}  # 2 x 4 m: halva 4 m-blocket i x
        kand = [
            rektangel(E0 + a, E0 + b, N0 + c, N0 + d, h, k)
            for k, (a, b, c, d, h) in kvarter.items()
        ]
        bas = bygg_rutnat(
            self.tmp / "grov", kand, e_min_m=E0, n_min_m=N0, antal=1, rutstorlek_m=40.0
        )
        ruta = sorted(bas.glob("mm_*"))
        medel = bygg_ytmosaik(ruta, 4.0, omsampling="medel")
        maxm = bygg_ytmosaik(ruta, 4.0, omsampling="max")
        self.assertAlmostEqual(
            float(medel.byggnadshojd_m.sum()), 10.0 * 8.0 / 16.0
        )  # volym/cellarea
        self.assertEqual(float(maxm.byggnadshojd_m.max()), 10.0)
        self.assertEqual(int(np.count_nonzero(maxm.byggnadshojd_m)), 1)
        with self.assertRaises(GeometriFel):
            bygg_ytmosaik(ruta, 0.6)  # ingen heltalsmultipel av 1 m


class TestK9(_Bas):
    def test_byggnad_hojd_andrar_bara_berord_byggnad(self) -> None:
        rutor = self.scenario_rutor(
            "hojd",
            [
                Andring(
                    "byggnad_hojd", "h_b", None, {"byggnads_id": byggnads_id("b"), "hojd_m": 26.0}
                )
            ],
        )
        fore = luft_geometri(self.rutor, dx_m=CELL, hojd_doman_m=40.0, vegetation=True)
        efter = luft_geometri(rutor, dx_m=CELL, hojd_doman_m=40.0, vegetation=True)
        d = efter.spec.hojd_over_mark_m - fore.spec.hojd_over_mark_m
        andrade = d != 0
        x0, x1, y0, y1, _ = HUS["b"]
        ny = _gammalt_raster({**HUS, "b": (x0, x1, y0, y1, 26.0)})
        np.testing.assert_array_equal(efter.spec.hojd_over_mark_m, ny)  # lika med handbyggd efter
        np.testing.assert_array_equal(andrade, _gammalt_raster({"b": HUS["b"]}) > 0)
        self.assertEqual(float(d[andrade].min()), 6.0)
        np.testing.assert_array_equal(efter.spec.vegetation_hojd_m, fore.spec.vegetation_hojd_m)
        # voxlar: bara kolumner i b:s fotavtryck, och bara lagren mellan 20 och 26 m
        vf, ve = self.vox(fore.spec), self.vox(efter.spec)
        diff = vf.fast != ve.fast
        self.assertTrue(bool(diff.any()))
        self.assertFalse(bool((vf.fast & ~ve.fast).any()))  # inget blir luft
        kolumner = diff.any(axis=2)
        rutnat_andrat = int(kolumner.sum())
        self.assertGreater(rutnat_andrat, 0)
        z = (np.arange(vf.fast.shape[2]) + 0.5) * CELL
        lager = diff.any(axis=(0, 1))
        self.assertTrue(bool(np.all((z[lager] >= 20.0) & (z[lager] < 26.0))))
        # övriga voxlar bit för bit lika
        np.testing.assert_array_equal(vf.fast[~kolumner], ve.fast[~kolumner])

    def test_byggnad_ny_lagger_till_exakt_dess_celler(self) -> None:
        rutor = self.scenario_rutor(
            "ny",
            [
                polygon_andring(
                    "byggnad_ny", "ny1", E0 + 60, E0 + 70, N0 + 20, N0 + 30, {"hojd_m": 16.0}
                )
            ],
        )
        fore = luft_geometri(self.rutor, dx_m=CELL, hojd_doman_m=40.0)
        efter = luft_geometri(rutor, dx_m=CELL, hojd_doman_m=40.0)
        facit = _gammalt_raster({**HUS, "ny": (60.0, 70.0, 20.0, 30.0, 16.0)})
        np.testing.assert_array_equal(efter.spec.hojd_over_mark_m, facit)
        diff = efter.spec.hojd_over_mark_m != fore.spec.hojd_over_mark_m
        self.assertEqual(int(diff.sum()), 25)  # 10 x 10 m på 2 m-celler
        self.assertEqual(efter.matvarden["byggnadsceller"] - fore.matvarden["byggnadsceller"], 25.0)

    def test_byggnad_bort_lika_med_bas_utan_huset(self) -> None:
        rutor = self.scenario_rutor(
            "bort", [Andring("byggnad_bort", "b_b", None, {"byggnads_id": byggnads_id("c")})]
        )
        efter = luft_geometri(rutor, dx_m=CELL, hojd_doman_m=40.0)
        np.testing.assert_array_equal(
            efter.spec.hojd_over_mark_m, _gammalt_raster({k: v for k, v in HUS.items() if k != "c"})
        )


class TestMarkklassFranGeometri(_Bas):
    def test_lad_och_z0_ur_markklasstabellen(self) -> None:
        t = las_markklasser()
        slag = np.array([[1, 4], [5, 9]], dtype=np.uint8)
        lad, okant = lad_fran_markslag(slag, t)
        self.assertEqual(okant, 0)
        np.testing.assert_array_equal(
            lad,
            [
                [
                    t.egenskaper_for(1, "luft")["lad_m2_m3"],
                    t.egenskaper_for(4, "luft")["lad_m2_m3"],
                ],
                [t.egenskaper_for(5, "luft")["lad_m2_m3"], 0.0],
            ],
        )
        # känt svar: enhetlig gräsmark -> z0 = tabellens värde för gräs; hälften gräs/hälften
        # skog = geometriskt medel
        ingen = np.zeros((2, 2))
        z_gras = markens_z0_m(np.full((2, 2), 4, np.uint8), ingen, t)
        self.assertAlmostEqual(z_gras or 0.0, t.egenskaper_for(4, "luft")["z0_m"])
        blandat = markens_z0_m(np.array([[4, 5], [4, 5]], np.uint8), ingen, t)
        self.assertAlmostEqual(
            blandat or 0.0,
            float(
                np.sqrt(t.egenskaper_for(4, "luft")["z0_m"] * t.egenskaper_for(5, "luft")["z0_m"])
            ),
        )
        # byggnadsceller och okänt markslag räknas inte
        self.assertIsNone(markens_z0_m(np.zeros((2, 2), np.uint8), ingen, t))
        self.assertIsNone(markens_z0_m(np.full((2, 2), 4, np.uint8), np.ones((2, 2)), t))

    def test_lad_per_markslag_styr_vegetationsdraget(self) -> None:
        """Samma vegetation, men över vatten (lad 0) blir det inget drag."""
        g = luft_geometri(self.rutor, dx_m=CELL, hojd_doman_m=40.0, vegetation=True)
        spec = replace(g.spec, lad_m2_m3=np.zeros_like(g.spec.hojd_over_mark_m))
        self.assertEqual(float(self.vox(spec).lad_m2_m3.sum()), 0.0)
        self.assertGreater(float(self.vox(g.spec).lad_m2_m3.sum()), 0.0)

    def test_v1_ruta_vagras(self) -> None:
        from karna.geometri.lasa import k1_version

        v1 = self.tmp / "v1" / "mm_0_0"
        shutil.copytree(self.rutor[0], v1)
        (v1 / "byggnader.gpkg").unlink()
        self.assertEqual(k1_version(v1), 1)
        with self.assertRaises(GeometriFel):
            luft_geometri([v1], dx_m=CELL, hojd_doman_m=40.0)


if __name__ == "__main__":
    unittest.main()
