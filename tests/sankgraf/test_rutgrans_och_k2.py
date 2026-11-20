"""Tester för global rutgränssammansättning (steg 8) samt K2- och K6-export."""

from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from gemensamt.hash import sha256_fil
from gemensamt.manifest import las_manifest
from sankgraf import (
    HAR_PYARROW,
    avkoda_gp_wkb,
    bygg_sankgraf,
    filtrera_persistens,
    las_geotiff_int64,
    sammanfoga_rutor,
    skriv_sankgraf_k2,
    skriv_statiskt_resultat_k6,
    statisk_fyllning,
)
from testsvit.k1_io import GeoTransformInfo, las_geotiff, las_gpkg_tabell

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_ROT = REPO_ROT / "testsvit"

K2_KOLUMNER = (
    "nod_id",
    "forald_id",
    "barn_id",
    "overfall_rh2000",
    "overfall_x",
    "overfall_y",
    "ut_till_id",
    "volymkurva",
    "ytkurva",
    "avrinningsyta_m2",
    "persistens_m",
    "osakerhet_m",
)


class TestRutgransOchK2Export(unittest.TestCase):
    """Verifierar global rutgränssammansättning samt K2/K6/K0-filformat."""

    def test_sanka_korsar_rutgrans_mellan_tva_rutor(self) -> None:
        # Bygg ett 200 x 100 cellers område (50 m x 25 m vid 0,25 m) med en sänka
        # centrerad rakt över rutgränsen vid kolumn 100 (x = x_min + 25.0 m).
        # Dela därefter området i två angränsande rutor (vardera 25 x 25 m inre yta)
        # med 5,0 m överlapp (20 celler) på varje sida -> klipp + sammanfoga!
        dx = 0.25
        overlapp_m = 5.0
        ov_c = round(overlapp_m / dx)  # 20 celler
        inre_b = 100  # 25 m per ruta
        inre_h = 100  # 25 m
        tot_b = 2 * inre_b
        tot_h = inre_h

        # Skapa en bredare basmatris så även yttre överlappen har giltig terräng
        bas_b = tot_b + 2 * ov_c
        bas_h = tot_h + 2 * ov_c
        ii, jj = np.meshgrid(np.arange(bas_h), np.arange(bas_b), indexing="ij")
        c_rad = bas_h / 2.0
        c_kol = bas_b / 2.0  # exakt på rutgränsen mellan västra och östra rutan!

        dist = np.maximum(np.abs(ii - c_rad), np.abs(jj - c_kol))
        bas_mark = (10.60 + 0.015 * np.maximum(dist - 20.0, 0.0)).astype(np.float32)
        # Rektangulär sänka 40x40 celler centrerad över rutgränsen (20 celler i varje ruta)
        r_mid = bas_h // 2
        c_mid = bas_b // 2
        bas_mark[r_mid - 20 : r_mid + 20, c_mid - 20 : c_mid + 20] = np.float32(10.00)
        # Utloppssadel mot öster vid z = 10.50 m
        bas_mark[r_mid - 2 : r_mid + 2, c_mid + 20 : c_mid + 22] = np.float32(10.50)
        for steg, col in enumerate(range(c_mid + 22, bas_b)):
            bas_mark[r_mid - 2 : r_mid + 2, col] = np.float32(10.48 - 0.002 * steg)

        bas_osak = np.full((bas_h, bas_b), 0.02, dtype=np.float32)
        bas_slag = np.full((bas_h, bas_b), 1, dtype=np.uint8)

        x_inre_min = 151000.0
        y_inre_max = 6577025.0

        # Odelad referens (exakt den inre globala matrisen 100 x 200)
        ref_mark = bas_mark[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
        ref_osak = bas_osak[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
        ref_slag = bas_slag[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
        ref_geo = GeoTransformInfo(
            bredd=tot_b,
            hojd=tot_h,
            x_min=x_inre_min,
            y_max=y_inre_max,
            pixelstorlek_x=dx,
            pixelstorlek_y=dx,
            epsg=3011,
            dtype="float32",
        )

        # Västra och östra rutan inklusive överlapp på alla fyra sidor
        r_b = inre_b + 2 * ov_c
        r_h = inre_h + 2 * ov_c
        geo_v = GeoTransformInfo(
            bredd=r_b,
            hojd=r_h,
            x_min=x_inre_min - overlapp_m,
            y_max=y_inre_max + overlapp_m,
            pixelstorlek_x=dx,
            pixelstorlek_y=dx,
            epsg=3011,
            dtype="float32",
        )
        geo_o = GeoTransformInfo(
            bredd=r_b,
            hojd=r_h,
            x_min=x_inre_min + inre_b * dx - overlapp_m,
            y_max=y_inre_max + overlapp_m,
            pixelstorlek_x=dx,
            pixelstorlek_y=dx,
            epsg=3011,
            dtype="float32",
        )
        ruta_v = (
            geo_v,
            bas_mark[:, 0:r_b],
            bas_osak[:, 0:r_b],
            bas_slag[:, 0:r_b],
        )
        ruta_o = (
            geo_o,
            bas_mark[:, inre_b : inre_b + r_b],
            bas_osak[:, inre_b : inre_b + r_b],
            bas_slag[:, inre_b : inre_b + r_b],
        )

        glob_geo, glob_mark, glob_osak, glob_slag = sammanfoga_rutor(
            [ruta_v, ruta_o], overlapp_m=overlapp_m
        )
        self.assertEqual(glob_geo, ref_geo)
        np.testing.assert_array_equal(glob_mark, ref_mark)

        graf_ref = bygg_sankgraf(ref_mark, ref_osak, ref_slag, ref_geo)
        graf_glob = bygg_sankgraf(glob_mark, glob_osak, glob_slag, glob_geo)

        self.assertEqual(len(graf_glob.noder), 1)
        self.assertEqual(len(graf_glob.noder), len(graf_ref.noder))
        n_ref = graf_ref.lovnoder()[0]
        n_glob = graf_glob.lovnoder()[0]
        self.assertAlmostEqual(n_glob.max_volym_m3, n_ref.max_volym_m3, places=9)
        self.assertAlmostEqual(n_glob.overfall_rh2000, n_ref.overfall_rh2000, places=9)

        res_ref = statisk_fyllning(graf_ref, 0.25)
        res_glob = statisk_fyllning(graf_glob, 0.25)
        self.assertLess(res_glob.volymfel_rel, 0.001)
        self.assertAlmostEqual(res_glob.lagrat_m3, res_ref.lagrat_m3, places=9)
        np.testing.assert_allclose(res_glob.djup_m, res_ref.djup_m, atol=1e-6)

    def test_k2_och_k6_export_och_reproducerbarhet(self) -> None:
        indata = TESTSVIT_ROT / "A3_tva_sankor_sammanslagning" / "indata"
        mark_raw, geo = las_geotiff(indata / "mark.tif")
        osak_raw, _ = las_geotiff(indata / "osakerhet.tif")
        slag_raw, _ = las_geotiff(indata / "markslag.tif")

        graf = bygg_sankgraf(
            np.asarray(mark_raw, dtype=np.float32),
            np.asarray(osak_raw, dtype=np.float32),
            np.asarray(slag_raw, dtype=np.uint8),
            geo,
        )
        graf_filt = filtrera_persistens(graf, faktor=2.0)
        res = statisk_fyllning(graf_filt, 0.30)

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            k2_dir1 = Path(tmp1) / "k2"
            k6_dir1 = Path(tmp1) / "k6"
            k2_dir2 = Path(tmp2) / "k2"
            k6_dir2 = Path(tmp2) / "k6"

            k2_filer1 = skriv_sankgraf_k2(
                graf_filt,
                k2_dir1,
                kodversion="test-sha",
                start="2026-11-20T18:00:00+01:00",
            )
            k2_filer2 = skriv_sankgraf_k2(
                graf_filt,
                k2_dir2,
                kodversion="test-sha",
                start="2026-11-20T18:00:00+01:00",
            )

            # Kontrollera sankgraf.json
            json_data = json.loads(k2_filer1["sankgraf_json"].read_text(encoding="utf-8"))
            self.assertEqual(json_data["kontrakt"], "K2/v1")
            self.assertEqual(json_data["antal_noder"], 3)
            for nod_rad in json_data["noder"]:
                for kol in K2_KOLUMNER:
                    self.assertIn(kol, nod_rad)

            # Kontrollera cell_till_nod.tif (int64, EPSG:3011)
            ct_in, ct_geo = las_geotiff_int64(k2_filer1["cell_till_nod"])
            self.assertEqual(ct_geo.epsg, 3011)
            self.assertEqual(ct_geo.dtype, "int64")
            np.testing.assert_array_equal(ct_in, graf_filt.cell_till_nod)

            # Kontrollera flodesvagar.gpkg
            srs_id, rader = las_gpkg_tabell(k2_filer1["flodesvagar"], "flodesvagar")
            self.assertEqual(srs_id, 3011)
            self.assertEqual(len(rader), len(graf_filt.flodesvagar))
            self.assertGreater(len(rader), 0)
            for rad in rader[:5]:
                self.assertIn("gangtid_s", rad)
                self.assertIn("nod_id", rad)
                koord = avkoda_gp_wkb(rad["geom"])
                self.assertGreaterEqual(len(koord), 2)

            # Kontrollera K0 run.json för K2
            manifest_k2 = las_manifest(k2_filer1["run"])
            self.assertEqual(manifest_k2.kontrakt, "K0/v1")
            self.assertEqual(manifest_k2.modul, "sankgraf")
            for post in manifest_k2.utdata:
                self.assertEqual(sha256_fil(k2_dir1 / post.fil), post.sha256)

            # Reproducerbarhet: identisk sha256 mellan två körningar
            self.assertEqual(
                sha256_fil(k2_filer1["sankgraf_json"]),
                sha256_fil(k2_filer2["sankgraf_json"]),
            )
            self.assertEqual(
                sha256_fil(k2_filer1["cell_till_nod"]),
                sha256_fil(k2_filer2["cell_till_nod"]),
            )
            self.assertEqual(
                sha256_fil(k2_filer1["run"]),
                sha256_fil(k2_filer2["run"]),
            )

            # Kontrollera K6-export (max_djup.tif + run.json med nivå="graf")
            k6_filer1 = skriv_statiskt_resultat_k6(
                res,
                geo,
                k6_dir1,
                kodversion="test-sha",
                start="2026-11-20T18:00:00+01:00",
            )
            k6_filer2 = skriv_statiskt_resultat_k6(
                res,
                geo,
                k6_dir2,
                kodversion="test-sha",
                start="2026-11-20T18:00:00+01:00",
            )
            djup_in, djup_geo = las_geotiff(k6_filer1["max_djup"])
            self.assertEqual(djup_geo.epsg, 3011)
            self.assertEqual(djup_geo.dtype, "float32")
            np.testing.assert_allclose(djup_in, res.djup_m, atol=1e-6)

            manifest_k6 = las_manifest(k6_filer1["run"])
            self.assertEqual(manifest_k6.parametrar["nivå"], "graf")
            self.assertLess(float(manifest_k6.matvarden["volymfel_rel"]), 0.001)
            self.assertEqual(
                sha256_fil(k6_filer1["max_djup"]),
                sha256_fil(k6_filer2["max_djup"]),
            )

    @unittest.skipUnless(HAR_PYARROW, "pyarrow ej installerat lokalt")
    def test_sankgraf_parquet_k2_schema(self) -> None:
        indata = TESTSVIT_ROT / "A3_tva_sankor_sammanslagning" / "indata"
        mark_raw, geo = las_geotiff(indata / "mark.tif")
        osak_raw, _ = las_geotiff(indata / "osakerhet.tif")
        slag_raw, _ = las_geotiff(indata / "markslag.tif")

        graf = bygg_sankgraf(
            np.asarray(mark_raw, dtype=np.float32),
            np.asarray(osak_raw, dtype=np.float32),
            np.asarray(slag_raw, dtype=np.uint8),
            geo,
        )
        pq: Any = importlib.import_module("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as tmp:
            filer = skriv_sankgraf_k2(graf, Path(tmp))
            self.assertIn("sankgraf_parquet", filer)
            tabell = pq.read_table(str(filer["sankgraf_parquet"]))
            self.assertEqual(tuple(tabell.column_names), K2_KOLUMNER)
            self.assertEqual(tabell.num_rows, len(graf.noder))
            rader = tabell.to_pylist()
            self.assertEqual([r["nod_id"] for r in rader], sorted(graf.noder))
            for rad in rader:
                self.assertGreaterEqual(len(rad["volymkurva"]), 2)
                self.assertGreaterEqual(len(rad["ytkurva"]), 2)


if __name__ == "__main__":
    unittest.main()
