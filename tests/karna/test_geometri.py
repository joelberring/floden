"""K1 v2: byggnadsregister, terräng, ytmodell, vegetation, K9-ändringar och fasadpunkter."""

from __future__ import annotations

import math
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from gemensamt.hash import sha256_fil
from karna.geometri import (
    GeometriFel,
    fasadpunkter,
    k1_version,
    las_byggnader,
    las_markklasser,
    las_terrang,
    las_vegetation,
    las_ytmodell,
)
from karna.geometri.rasterfil import las_float_raster, las_raster
from karna.resultatcache import ResultatCache
from karna.rutscenario import materialisera_scenario
from karna.scenario import Andring, Planscenario, ScenarioFel, skriv_scenario
from karna.utredning import ScenarioSpec
from markmodell.byggnadsregister import (
    ByggnadsKandidat,
    ByggnadsPost,
    byggnad_id_fran_geometri,
    byggnad_id_fran_kalla,
    las_byggnader_gpkg,
    skriv_byggnader_gpkg,
)
from markmodell.hinder_oppningar import BYGGNAD, Byggnad, byggnad_maske
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta

E0, N0 = 150000.0, 6576000.0
# Byggnad B1 korsar gränsen mellan ruta A (inre 150000-150024) och ruta B (150024-150048).
B1 = ((150020.0, 6576006.0), (150028.0, 6576006.0), (150028.0, 6576014.0), (150020.0, 6576014.0))
B2 = ((150004.0, 6576004.0), (150010.0, 6576004.0), (150010.0, 6576010.0), (150004.0, 6576010.0))
TAK_B1 = 17.0
MARK_Z = 10.0


def _punkter() -> tuple[Any, Any, Any, Any]:
    es, ns = np.meshgrid(np.arange(E0 - 4.5, E0 + 54.0, 1.0), np.arange(N0 - 4.5, N0 + 30.0, 1.0))
    e, n = es.ravel(), ns.ravel()
    z = np.full(e.shape, MARK_Z)
    k = np.full(e.shape, 2, dtype=np.int32)
    # takpunkter (klass 6) över B1
    te, tn = np.meshgrid(np.arange(150020.25, 150028.0, 0.5), np.arange(6576006.25, 6576014.0, 0.5))
    # vegetation (klass 5): träd 8 m över marken vid (150014, 6576020)
    ve, vn = np.meshgrid(np.arange(150012.5, 150015.5, 0.5), np.arange(6576018.5, 6576021.5, 0.5))
    e = np.concatenate([e, te.ravel(), ve.ravel()])
    n = np.concatenate([n, tn.ravel(), vn.ravel()])
    z = np.concatenate([z, np.full(te.size, TAK_B1), np.full(ve.size, MARK_Z + 8.0)])
    k = np.concatenate([k, np.full(te.size, 6), np.full(ve.size, 5)]).astype(np.int32)
    return e, n, z, k


def _bygg(tmp: Path, e_min: float, kandidater: tuple[ByggnadsKandidat, ...]) -> Path:
    e, n, z, k = _punkter()
    ruta = Ruta(e_min_m=e_min, n_min_m=N0, storlek_m=24.0, overlapp_m=2.0)
    res = bygg_markmodell_ruta(
        ruta,
        tmp,
        mark_punkter_e=e,
        mark_punkter_n=n,
        mark_punkter_z=z,
        laser_klasser=k,
        byggnadskandidater=kandidater,
        kodversion="test",
        start_tid="2026-11-21T00:00:00+01:00",
    )
    return res.katalog


def _kandidater() -> tuple[ByggnadsKandidat, ...]:
    return (
        ByggnadsKandidat(polygon=B1, kalla="test", kalla_id="b1"),
        ByggnadsKandidat(polygon=B2, kalla="test", kalla_id="b2", hojd_m=6.0),
    )


class TestK1V2(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.a = _bygg(cls.tmp / "ut", E0, _kandidater())
        cls.b = _bygg(cls.tmp / "ut", E0 + 24.0, _kandidater())

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_filer_och_version(self) -> None:
        for f in ("terrang.tif", "byggnader.gpkg", "vegetation.tif", "mark.tif"):
            self.assertTrue((self.a / f).is_file(), f)
        self.assertEqual(k1_version(self.a), 2)

    def test_id_stabilt_over_rutgrans_och_hojd_mot_laser(self) -> None:
        ba = {b.byggnad_id: b for b in las_byggnader(self.a)}
        bb = {b.byggnad_id: b for b in las_byggnader(self.b)}
        id1 = byggnad_id_fran_kalla("test", "b1")
        id2 = byggnad_id_fran_kalla("test", "b2")
        self.assertIn(id1, ba)
        self.assertIn(id1, bb)  # samma id i båda rutorna
        self.assertIn(id2, ba)
        self.assertNotIn(id2, bb)  # ligger bara i A
        self.assertAlmostEqual(ba[id1].takhojd_rh2000, TAK_B1, places=6)
        self.assertEqual(ba[id1].hojdkalla, "laser_klass6")
        self.assertAlmostEqual(ba[id1].markhojd_rh2000, MARK_Z, places=3)
        self.assertEqual(ba[id2].hojdkalla, "kalla_hojd")
        self.assertAlmostEqual(ba[id2].hojd_m, 6.0, places=3)

    def test_geometri_id_oberoende_av_start_och_orientering(self) -> None:
        a = byggnad_id_fran_geometri(B1)
        b = byggnad_id_fran_geometri(tuple(reversed(B1[2:] + B1[:2])))
        self.assertEqual(a, b)

    def test_mark_ar_terrang_plus_byggnader_och_dsm_ar_max(self) -> None:
        mark, geo = las_float_raster(self.a / "mark.tif")
        terrang, _ = las_terrang(self.a)
        dsm, _ = las_ytmodell(self.a)
        np.testing.assert_array_equal(dsm, mark)  # inga öppningar i rutan
        slag, _ = las_raster(self.a / "markslag.tif")
        bygg = slag == BYGGNAD
        self.assertTrue(bygg.any())
        np.testing.assert_array_equal(terrang[~bygg], mark[~bygg])
        self.assertTrue(np.all(terrang[bygg] <= mark[bygg]))
        self.assertAlmostEqual(float(mark[bygg].max()), TAK_B1, places=5)

    def test_dsm_med_vegetation_ar_max_sammansattning(self) -> None:
        dsm, geo = las_ytmodell(self.a, vegetation=True)
        terrang, _ = las_terrang(self.a)
        veg, vgeo = las_vegetation(self.a) or (None, None)
        assert veg is not None and vgeo is not None
        self.assertEqual(vgeo.pixelstorlek_x, 1.0)
        self.assertGreater(float(veg.max()), 7.0)
        upp = np.repeat(np.repeat(veg, 4, axis=0), 4, axis=1)
        mark, _ = las_float_raster(self.a / "mark.tif")
        np.testing.assert_array_equal(dsm, np.maximum(mark, terrang + upp))
        self.assertAlmostEqual(float(dsm.max()), MARK_Z + 8.0, places=5)  # trädet > taket
        # trädet (8 m) syns över marken utanför byggnaderna
        rad, kol = int((geo.y_max - 6576020.0) / 0.25), int((150014.0 - geo.x_min) / 0.25)
        self.assertGreater(float(dsm[rad, kol]), MARK_Z + 5.0)
        self.assertAlmostEqual(float(mark[rad, kol]), MARK_Z, places=5)

    def test_gpkg_reproducerbar(self) -> None:
        poster = las_byggnader_gpkg(self.a / "byggnader.gpkg")
        ut = self.tmp / "igen.gpkg"
        skriv_byggnader_gpkg(ut, poster, (0, 0, 1, 1))
        self.assertEqual(sha256_fil(ut), sha256_fil(self.a / "byggnader.gpkg"))

    def test_v1_ruta_lases_fortfarande(self) -> None:
        v1 = self.tmp / "v1"
        shutil.copytree(self.a, v1)
        for f in ("terrang.tif", "byggnader.gpkg", "vegetation.tif"):
            (v1 / f).unlink()
        self.assertEqual(k1_version(v1), 1)
        mark, _ = las_float_raster(v1 / "mark.tif")
        self.assertEqual(mark.shape, (112, 112))
        with self.assertRaises(GeometriFel):  # har byggnadsceller: ingen gissning
            las_terrang(v1)
        # v1 utan byggnadsceller: terräng = mark, tomt register
        slag, geo = las_raster(v1 / "markslag.tif")
        from testsvit.k1_io import skriv_geotiff

        skriv_geotiff(v1 / "markslag.tif", np.ones_like(slag), x_min=geo.x_min, y_max=geo.y_max)
        self.assertEqual(las_byggnader(v1), ())
        np.testing.assert_array_equal(las_terrang(v1)[0], mark)
        self.assertIsNone(las_vegetation(v1))


class TestK9PaV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.a = _bygg(cls.tmp / "ut", E0, _kandidater())
        cls.b = _bygg(cls.tmp / "ut", E0 + 24.0, _kandidater())
        cls.id1 = byggnad_id_fran_kalla("test", "b1")
        cls.id2 = byggnad_id_fran_kalla("test", "b2")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _kor(self, bas: Path, andringar: list[Andring]) -> Any:
        tmp = Path(tempfile.mkdtemp(dir=self.tmp))
        recept = (
            [bas / "recept.json"]
            if (bas / "recept.json").is_file()
            else sorted(bas.glob("mm_*/recept.json"))
        )
        sc = Planscenario("s", tuple(sha256_fil(r) for r in recept), tuple(andringar))
        f = skriv_scenario(sc, tmp / "s.json")
        spec = ScenarioSpec(id="s", bas=bas, k9=(f,))
        return materialisera_scenario(spec, ResultatCache(tmp / "cache"))

    def test_byggnad_bort_ger_exakt_terrang(self) -> None:
        res = self._kor(self.a, [Andring("byggnad_bort", "rv", None, {"byggnads_id": self.id2})])
        ny = res.rutor[0].mapp
        mark, geo = las_float_raster(ny / "mark.tif")
        terrang, _ = las_terrang(ny)
        mark0, _ = las_float_raster(self.a / "mark.tif")
        slag0, _ = las_raster(self.a / "markslag.tif")
        slag, _ = las_raster(ny / "markslag.tif")
        from karna.geometri.rasterfil import ruta_fran_geo

        traff = byggnad_maske(Byggnad(polygon=B2), ruta_fran_geo(geo))
        assert traff is not None
        m, (r0, r1, k0, k1) = traff
        full = np.zeros(mark.shape, dtype=bool)
        full[r0:r1, k0:k1] = m
        np.testing.assert_array_equal(mark[full], terrang[full])  # exakt terräng
        self.assertTrue(np.all(mark0[full] > terrang[full]))
        np.testing.assert_array_equal(mark[~full], mark0[~full])  # oförändrat utanför
        self.assertFalse(np.any(slag[full] == BYGGNAD))
        np.testing.assert_array_equal(slag[~full], slag0[~full])
        ids = {b.byggnad_id for b in las_byggnader(ny)}
        self.assertEqual(ids, {self.id1})

    def test_byggnad_hojd_ger_takhojd_plus_x(self) -> None:
        x = 3.5
        res = self._kor(
            self.a, [Andring("byggnad_hojd", "hj", None, {"byggnads_id": self.id1, "delta_m": x})]
        )
        ny = res.rutor[0].mapp
        gamla = {b.byggnad_id: b for b in las_byggnader(self.a)}
        nya = {b.byggnad_id: b for b in las_byggnader(ny)}
        self.assertAlmostEqual(nya[self.id1].takhojd_rh2000, gamla[self.id1].takhojd_rh2000 + x, 6)
        self.assertEqual(nya[self.id2], gamla[self.id2])
        mark, geo = las_float_raster(ny / "mark.tif")
        mark0, _ = las_float_raster(self.a / "mark.tif")
        diff = mark != mark0
        self.assertTrue(diff.any())
        np.testing.assert_allclose(mark[diff] - mark0[diff], x, atol=1e-5)
        slag, _ = las_raster(self.a / "markslag.tif")
        # alla ändrade celler är byggnad 1:s celler
        self.assertEqual(int(diff.sum()), int((mark0 == np.float32(TAK_B1)).sum()))

    def test_byggnad_ny_utan_entre_och_id_i_alla_rutor(self) -> None:
        ring = [[150040, 6576002], [150044, 6576002], [150044, 6576006], [150040, 6576006]]
        a = Andring("byggnad_ny", "hus9", {"type": "Polygon", "coordinates": [ring]}, {"hojd_m": 9})
        res = self._kor(self.b, [a])
        ny = res.rutor[0].mapp
        post = {b.byggnad_id: b for b in las_byggnader(ny)}["hus9"]
        self.assertAlmostEqual(post.hojd_m, 9.0, places=6)
        mark, _ = las_float_raster(ny / "mark.tif")
        self.assertGreater(float(mark.max()), MARK_Z + 8.9)

    def test_vegetation_andring(self) -> None:
        ring = [[150004, 6576014], [150008, 6576014], [150008, 6576018], [150004, 6576018]]
        a = Andring("vegetation", "tra", {"type": "Polygon", "coordinates": [ring]}, {"hojd_m": 12})
        res = self._kor(self.a, [a])
        veg = las_vegetation(res.rutor[0].mapp)
        assert veg is not None
        v0 = las_vegetation(self.a)
        assert v0 is not None
        diff = veg[0] != v0[0]
        self.assertEqual(int(diff.sum()), 16)  # 4 x 4 m vid 1 m-celler
        self.assertTrue(np.all(veg[0][diff] == 12.0))

    def test_okant_byggnads_id_ger_fel(self) -> None:
        with self.assertRaises(ScenarioFel):
            self._kor(self.a, [Andring("byggnad_bort", "x", None, {"byggnads_id": "b_finnsinte"})])

    def test_id_finns_i_annan_ruta_hoppas_over_i_ruta_b(self) -> None:
        bas = self.a.parent  # katalog med båda rutorna
        res = self._kor(bas, [Andring("byggnad_bort", "rv", None, {"byggnads_id": self.id2})])
        mappar = {r.ruta_id: r.mapp for r in res.rutor}
        # id2 finns bara i ruta A: ruta B behåller basens katalog (och därmed hash)
        self.assertEqual(mappar[self.b.name], self.b)
        self.assertNotEqual(mappar[self.a.name], self.a)

    def test_v1_ger_tydligt_fel(self) -> None:
        v1 = self.tmp / "v1"
        shutil.copytree(self.a, v1)
        for f in ("terrang.tif", "byggnader.gpkg", "vegetation.tif"):
            (v1 / f).unlink(missing_ok=True)
        with self.assertRaisesRegex(ScenarioFel, "K1 v2"):
            self._kor(v1, [Andring("byggnad_bort", "x", None, {"byggnads_id": "b_1"})])

    def test_tomt_scenario_andrar_inget_och_markhojd_ar_konsekvent(self) -> None:
        res = self._kor(self.a, [])
        self.assertEqual(res.rutor[0].mapp, self.a)  # derive är identitet för obearbetade rutor
        ring = [[150012, 6576016], [150018, 6576016], [150018, 6576022], [150012, 6576022]]
        a = Andring(
            "markhojd", "upp", {"type": "Polygon", "coordinates": [ring]}, {"niva_rh2000": 10.6}
        )
        ny = self._kor(self.a, [a]).rutor[0].mapp
        terrang, _ = las_terrang(ny)
        terrang0, _ = las_terrang(self.a)
        mark, _ = las_float_raster(ny / "mark.tif")
        mark0, _ = las_float_raster(self.a / "mark.tif")
        andrad = terrang != terrang0
        self.assertTrue(andrad.any())
        np.testing.assert_array_equal(mark[andrad], terrang[andrad])  # utanför byggnader
        np.testing.assert_array_equal(mark[~andrad], mark0[~andrad])
        self.assertEqual(las_byggnader(ny), las_byggnader(self.a))


class TestGenerellKalla(unittest.TestCase):
    def test_gpkg_i_sweref99tm_konverteras_till_3011(self) -> None:
        import geopandas as gpd
        from pyproj import Transformer

        from markmodell.byggnadskallor import las_byggnadskalla

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        e, n = 674000.0, 6578000.0  # SWEREF 99 TM, Stockholm
        poly = shapely.box(e, n, e + 10, n + 8)
        gpd.GeoDataFrame(
            {"objektidentitet": ["abc"], "height": ["12 m"], "building:levels": [4]},
            geometry=[poly],
            crs="EPSG:3006",
        ).to_file(tmp / "b.gpkg", driver="GPKG")
        res = las_byggnadskalla(tmp / "b.gpkg")
        self.assertEqual(len(res.kandidater), 1)
        k = res.kandidater[0]
        tr = Transformer.from_crs(3006, 3011, always_xy=True)
        for hx, hy in ((e, n), (e + 10, n), (e + 10, n + 8), (e, n + 8)):
            x, y = tr.transform(hx, hy)
            self.assertTrue(
                any(math.hypot(px - x, py - y) < 1e-3 for px, py in k.polygon), (x, y)
            )
        self.assertEqual(k.hojd_m, 12.0)
        self.assertEqual(k.vaningar, 4)
        self.assertEqual(k.id(), byggnad_id_fran_kalla("b", "abc"))
        self.assertEqual(res.sha256, sha256_fil(tmp / "b.gpkg"))


class TestReceptV2(unittest.TestCase):
    def test_recept_har_nya_kallor(self) -> None:
        import json

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        d = _bygg(tmp, E0, _kandidater())
        r = json.loads((d / "recept.json").read_text(encoding="utf-8"))
        roller = {k["roll"] for k in r["kallor"]}
        self.assertTrue({"byggnadskandidater", "laser_klasser"} <= roller)
        self.assertEqual(r["kontrakt"], "K1/v2")
        self.assertEqual(r["installningar"]["k1_version"], 2)
        run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        self.assertIn("byggnader", {u["roll"] for u in run["utdata"]})


class TestFasadpunkter(unittest.TestCase):
    def _post(self, bid: str, ring: tuple[tuple[float, float], ...], hojd: float) -> ByggnadsPost:
        return ByggnadsPost(
            byggnad_id=bid, polygon=ring, takhojd_rh2000=10.0 + hojd, markhojd_rh2000=10.0,
            hojd_m=hojd, kalla="t",
        )

    def _kor(self, poster: list[ByggnadsPost], steg: float, vh: float) -> Any:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        skriv_byggnader_gpkg(tmp / "byggnader.gpkg", poster, (0, 0, 1, 1))
        (tmp / "terrang.tif").write_bytes(b"")
        return fasadpunkter([tmp], steg, vh)

    def _kontrollera_utat(self, f: Any, poster: list[ByggnadsPost]) -> None:
        geoms = {p.byggnad_id: p.geometri() for p in poster}
        for i in range(len(f)):
            g = geoms[str(f.byggnad_id[i])]
            ute = shapely.Point(f.e_m[i] + 0.01 * f.normal_e[i], f.n_m[i] + 0.01 * f.normal_n[i])
            inne = shapely.Point(f.e_m[i] - 0.01 * f.normal_e[i], f.n_m[i] - 0.01 * f.normal_n[i])
            self.assertFalse(g.contains(ute))
            self.assertTrue(g.contains(inne))
            az = math.degrees(math.atan2(f.normal_e[i], f.normal_n[i])) % 360.0
            self.assertAlmostEqual(az, f.normal_azimut_grader[i], places=6)

    def test_kvadrat_normaler_antal_och_azimut(self) -> None:
        # CW-ordnad kvadrat ska normaliseras; 10 x 10 m, steg 2 m, 3 våningar (hojd 9, vh 3)
        ring = ((0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0))
        p = self._post("b_k", ring, 9.0)
        f = self._kor([p], 2.0, 3.0)
        self.assertEqual(len(f), int(40 / 2) * 3)
        self._kontrollera_utat(f, [p])
        # söderfasaden (n = 0) har azimut 180, östra 90, norra 0, västra 270
        sodra = np.isclose(f.n_m, 0.0)
        self.assertTrue(np.all(np.isclose(f.normal_azimut_grader[sodra], 180.0)))
        self.assertEqual(set(np.round(f.normal_azimut_grader).tolist()), {0.0, 90.0, 180.0, 270.0})
        self.assertEqual(set(f.vaning.tolist()), {1, 2, 3})
        self.assertAlmostEqual(float(f.z_rh2000.min()), 11.5)

    def test_l_form_normaler_pekar_utat_och_antal(self) -> None:
        ring = ((0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6))
        ring = tuple((float(x), float(y)) for x, y in ring)
        p = self._post("b_l", ring, 3.0)  # en våning
        f = self._kor([p], 1.5, 3.0)
        forvantat = sum(
            math.ceil(math.dist(ring[i], ring[(i + 1) % 6]) / 1.5 - 1e-9) for i in range(6)
        )
        self.assertEqual(len(f), forvantat)
        self._kontrollera_utat(f, [p])

    def test_gemensam_brandvagg_ger_inga_punkter(self) -> None:
        a = self._post("b_a", ((0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)), 3.0)
        b = self._post("b_b", ((5.0, 0.0), (10.0, 0.0), (10.0, 5.0), (5.0, 5.0)), 3.0)
        f = self._kor([a, b], 1.0, 3.0)
        self.assertEqual(len(f), 2 * 3 * 5)  # 2 hus, 3 fria sidor, 5 punkter, 1 våning
        self.assertFalse(np.any(np.isclose(f.e_m, 5.0)))

    def test_geoparquet(self) -> None:
        p = self._post("b_k", ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)), 3.0)
        f = self._kor([p], 2.0, 3.0)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ut = f.skriv_geoparquet(tmp / "f.parquet")
        import geopandas as gpd

        g = gpd.read_parquet(ut)
        self.assertEqual(len(g), len(f))
        self.assertEqual(g.crs.to_epsg(), 3011)


class TestMarkklass(unittest.TestCase):
    def test_tabell_och_vattenkolumner_stammer_med_mark_forluster(self) -> None:
        from mark_forluster.parametrar import effektiv_markparametrar

        t = las_markklasser()
        for kod in range(10):
            v = t.egenskaper_for(kod, "vatten")
            e = effektiv_markparametrar(kod, "sand")
            self.assertAlmostEqual(v["initial_faktor"], e.initial_faktor)
            if "ksat_faktor" in v and kod != 7:  # grus har golv på 15 mm/h i mark_forluster
                gras = effektiv_markparametrar(4, "sand").ksat_mm_h
                self.assertAlmostEqual(e.ksat_mm_h, v["ksat_faktor"] * gras)
        self.assertEqual(t.egenskaper_for(1, "dagsljus"), {})
        slag = np.array([[1, 4], [5, 8]], dtype=np.uint8)
        np.testing.assert_array_equal(
            t.raster(slag, "vatten", "initial_faktor"), [[1.0, 1.0], [1.5, 0.0]]
        )
        with self.assertRaises(GeometriFel):
            t.raster(slag, "luft", "ruhet")

    def test_vattenkolumnerna_ar_oforandrade(self) -> None:
        """Värdena som fanns före bullrets och luftens kolumner (G1), kod för kod."""
        forvantat = {
            0: {"initial_faktor": 0.5, "ksat_faktor": 0.0},
            1: {"initial_faktor": 1.0, "ksat_faktor": 0.0},
            2: {"initial_faktor": 1.0, "ksat_faktor": 0.0},
            3: {"initial_faktor": 1.0},
            4: {"initial_faktor": 1.0, "ksat_faktor": 1.0},
            5: {"initial_faktor": 1.5, "ksat_faktor": 1.5},
            6: {"initial_faktor": 1.0},
            7: {"initial_faktor": 1.0, "ksat_faktor": 1.5},
            8: {"initial_faktor": 0.0, "ksat_faktor": 0.0},
            9: {"initial_faktor": 1.0, "ksat_faktor": 0.0},
        }
        t = las_markklasser()
        for kod, v in forvantat.items():
            self.assertEqual(dict(t.egenskaper_for(kod, "vatten")), v, kod)

    def test_alla_forekommande_markslag_har_varden_for_alla_doman(self) -> None:
        """Varje kod 1-9 har alla kolumner för buller och luft; kod 0 (okänt) saknar dem med
        flit."""
        t = las_markklasser()
        kolumner = {
            "vatten": ("initial_faktor",),
            "buller": ("impedansklass", "rahetsklass", "flodesresistivitet_kNsm4", "rahet_m"),
            "luft": ("z0_m", "lad_m2_m3"),
        }
        for kod in range(1, 10):
            for doman, kol in kolumner.items():
                e = t.egenskaper_for(kod, doman)
                for k in kol:
                    self.assertIn(k, e, f"kod {kod} {doman}.{k}")
        for doman in ("buller", "luft"):
            self.assertEqual(dict(t.egenskaper_for(0, doman)), {})
        slag = np.arange(1, 10, dtype=np.uint8).reshape(3, 3)
        for doman, kol in (("buller", "flodesresistivitet_kNsm4"), ("buller", "rahet_m"),
                           ("luft", "z0_m"), ("luft", "lad_m2_m3")):
            r = t.raster(slag, doman, kol)
            self.assertTrue(bool(np.all(np.isfinite(r))), (doman, kol))
        with self.assertRaises(GeometriFel):  # kod 0 flaggas i stället för att gissas
            t.raster(np.zeros((1, 1), dtype=np.uint8), "buller", "rahet_m")

    def test_markslag_i_syntetisk_ruta_tacks_av_alla_doman(self) -> None:
        import tempfile
        from pathlib import Path as P

        from dagsljus.exempelkvarter import bygg_demokvarter
        from karna.geometri import las_markslag

        t = las_markklasser()
        with tempfile.TemporaryDirectory() as mapp:
            kv = bygg_demokvarter(P(mapp))
            for ruta in sorted(kv["bas"].glob("mm_*")):
                slag, _ = las_markslag(ruta)
                for kod in np.unique(slag):
                    for doman, kol in (("vatten", "initial_faktor"), ("buller", "rahet_m"),
                                       ("luft", "z0_m")):
                        if kod == 0:
                            continue
                        self.assertIn(kol, t.egenskaper_for(int(kod), doman))

    def test_klasstabellerna_och_markering(self) -> None:
        t = las_markklasser()
        self.assertTrue(t.preliminar("buller"))
        self.assertTrue(t.preliminar("luft"))
        self.assertEqual(t.domanversion("buller"), "markklass/v1")
        # en enda siffra per klass: härledda kolumner är lika med klasstabellens värde
        for kod in range(1, 10):
            e = t.egenskaper_for(kod, "buller")
            self.assertEqual(e["flodesresistivitet_kNsm4"],
                             t.meta["buller"]["impedansklass"][e["impedansklass"]])


if __name__ == "__main__":
    unittest.main()
