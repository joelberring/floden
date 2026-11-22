"""Enhetstester för syntetiska fall A1–A11, K1/K4-format och rasterio/geopandas-kompatibilitet."""

from __future__ import annotations

import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from gemensamt.hash import sha256_fil
from testsvit.k1_io import (
    EPSG_SWEREF99_1800,
    PIXELSTORLEK_M,
    las_geotiff,
    las_gpkg_tabell,
    las_regn_csv,
)
from testsvit.korare import MAX_FALLSTORLEK_BYTES, kor_testsvit
from testsvit.syntetiska import (
    alla_syntetiska_specifikationer,
    generera_alla_syntetiska_fall,
)

HAR_RASTERIO = importlib.util.find_spec("rasterio") is not None
HAR_GEOPANDAS = importlib.util.find_spec("geopandas") is not None

REPO_ROT = Path(__file__).resolve().parent.parent.parent
TESTSVIT_ROT = REPO_ROT / "testsvit"

FORVANTADE_A_FALL = (
    ("A1", "A1_enkel_sanka"),
    ("A2", "A2_tva_sankor_serie"),
    ("A3", "A3_tva_sankor_sammanslagning"),
    ("A4", "A4_lutande_plan"),
    ("A5", "A5_gata_kantsten"),
    ("A6", "A6_sankt_kantsten_infart"),
    ("A7", "A7_vagbank_trumma"),
    ("A8", "A8_port_byggnad"),
    ("A9", "A9_brus_persistens"),
    ("A10", "A10_sluten_gard"),
    ("A11", "A11_sluten_gard_avtappning"),
)


class TestSyntetiskaFall(unittest.TestCase):
    def test_alla_nio_fall_finns_och_ar_under_1_mb(self) -> None:
        for fall_id, mappnamn in FORVANTADE_A_FALL:
            fall_mapp = TESTSVIT_ROT / mappnamn
            self.assertTrue(fall_mapp.is_dir(), f"Saknar mapp {fall_mapp}")
            self.assertTrue((fall_mapp / "fall.toml").is_file())
            total_bytes = sum(p.stat().st_size for p in fall_mapp.rglob("*") if p.is_file())
            self.assertLess(total_bytes, MAX_FALLSTORLEK_BYTES, f"{fall_id} >= 1 MB")

            indata = fall_mapp / "indata"
            for fil in (
                "mark.tif",
                "osakerhet.tif",
                "markslag.tif",
                "brytlinjer.gpkg",
                "oppningar.gpkg",
                "recept.json",
                "regn.csv",
            ):
                self.assertTrue((indata / fil).is_file(), f"{fall_id} saknar {fil}")

            facit = fall_mapp / "facit"
            self.assertTrue((facit / "facit.json").is_file())
            png_bytes = (facit / "oversikt.png").read_bytes()
            self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

            recept = json.loads((indata / "recept.json").read_text(encoding="utf-8"))
            self.assertEqual(recept["kontrakt"], "K1/v1")
            self.assertEqual(recept["epsg"], EPSG_SWEREF99_1800)
            for post in recept["kallor"].values():
                self.assertEqual(sha256_fil(indata / post["fil"]), post["sha256"])

    def test_k1_geotiff_och_gpkg_lasbara_i_ren_python(self) -> None:
        for _, mappnamn in FORVANTADE_A_FALL:
            indata = TESTSVIT_ROT / mappnamn / "indata"
            mark, m_info = las_geotiff(indata / "mark.tif")
            osak, o_info = las_geotiff(indata / "osakerhet.tif")
            slag, s_info = las_geotiff(indata / "markslag.tif")

            self.assertEqual(m_info.epsg, EPSG_SWEREF99_1800)
            self.assertEqual(o_info.epsg, EPSG_SWEREF99_1800)
            self.assertEqual(s_info.epsg, EPSG_SWEREF99_1800)
            self.assertEqual(m_info.pixelstorlek_x, PIXELSTORLEK_M)
            self.assertEqual(m_info.pixelstorlek_y, PIXELSTORLEK_M)
            self.assertEqual(mark.dtype, np.dtype(np.float32))
            self.assertEqual(osak.dtype, np.dtype(np.float32))
            self.assertEqual(slag.dtype, np.dtype(np.uint8))
            self.assertEqual(mark.shape, osak.shape)
            self.assertEqual(mark.shape, slag.shape)
            self.assertTrue(np.all(np.isfinite(mark)))

            b_srs, b_rader = las_gpkg_tabell(indata / "brytlinjer.gpkg", "brytlinjer")
            o_srs, o_rader = las_gpkg_tabell(indata / "oppningar.gpkg", "oppningar")
            self.assertEqual(b_srs, EPSG_SWEREF99_1800)
            self.assertEqual(o_srs, EPSG_SWEREF99_1800)
            for rad in b_rader:
                self.assertIn("typ", rad)
                self.assertIn("hojd_m", rad)
            for rad in o_rader:
                for kol in ("typ", "bredd_m", "botten_rh2000", "kalla"):
                    self.assertIn(kol, rad)

            regn = las_regn_csv(indata / "regn.csv")
            self.assertGreaterEqual(len(regn), 2)

    def test_deterministisk_regenerering_ger_identiska_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            mappar1 = generera_alla_syntetiska_fall(tmp1)
            mappar2 = generera_alla_syntetiska_fall(tmp2)
            self.assertEqual(len(mappar1), 11)
            for m1, m2 in zip(mappar1, mappar2, strict=True):
                for rel in (
                    "indata/mark.tif",
                    "indata/osakerhet.tif",
                    "indata/markslag.tif",
                    "indata/brytlinjer.gpkg",
                    "indata/oppningar.gpkg",
                    "indata/regn.csv",
                    "indata/recept.json",
                    "facit/facit.json",
                    "facit/oversikt.png",
                ):
                    self.assertEqual(
                        sha256_fil(m1 / rel),
                        sha256_fil(m2 / rel),
                        f"Icke-deterministisk fil: {m1.name}/{rel}",
                    )

    def test_analytisk_facit_konsistens(self) -> None:
        specs = {s.id: s for s in alla_syntetiska_specifikationer()}

        # A1: volym = area * djup, t_overfall = slutvolym / (avrinningsyta * intensitet)
        f1 = specs["A1"].facit
        self.assertAlmostEqual(
            f1["slutvolym_m3"],
            f1["sanka_area_m2"] * f1["max_djup_m"],
            places=6,
        )
        q1_m_s = f1["intensitet_mm_h"] / 1000.0 / 3600.0
        self.assertAlmostEqual(
            f1["t_overfall_s"],
            f1["slutvolym_m3"] / (f1["avrinningsyta_m2"] * q1_m_s),
            places=2,
        )

        # A2: övre svämmar över före nedre
        f2 = specs["A2"].facit
        self.assertEqual(f2["overfallsordning"], ["ovre", "nedre"])
        self.assertLess(f2["ovre"]["t_overfall_s"], f2["nedre"]["t_overfall_s"])

        # A3: sammanslagning före yttre överfall
        f3 = specs["A3"].facit
        self.assertLess(f3["t_hoger_nar_sadel_s"], f3["t_sammanslagning_s"])
        self.assertLess(f3["t_sammanslagning_s"], f3["t_yttre_overfall_s"])

        # A4: Manning-hastighet och gångtid
        f4 = specs["A4"].facit
        v_ber = (
            (1.0 / f4["manning_n"])
            * (f4["referensdjup_m"] ** (2.0 / 3.0))
            * math.sqrt(f4["lutning"])
        )
        self.assertAlmostEqual(f4["hastighet_m_s"], v_ber, places=5)
        self.assertAlmostEqual(f4["gangtid_s"], f4["flodeslangd_m"] / v_ber, places=2)

        # A5 & A6: brytlinjer finns med rätt kantstenshöjd
        b5_srs, b5_rader = las_gpkg_tabell(
            TESTSVIT_ROT / "A5_gata_kantsten" / "indata" / "brytlinjer.gpkg",
            "brytlinjer",
        )
        self.assertEqual(b5_srs, EPSG_SWEREF99_1800)
        self.assertEqual(len(b5_rader), 2)
        self.assertTrue(all(abs(r["hojd_m"] - 0.12) < 1e-6 for r in b5_rader))

        _, b6_rader = las_gpkg_tabell(
            TESTSVIT_ROT / "A6_sankt_kantsten_infart" / "indata" / "brytlinjer.gpkg",
            "brytlinjer",
        )
        self.assertIn(0.02, [round(float(r["hojd_m"]), 3) for r in b6_rader])

        # A7 & A8: öppningar finns i oppningar.gpkg
        _, o7_rader = las_gpkg_tabell(
            TESTSVIT_ROT / "A7_vagbank_trumma" / "indata" / "oppningar.gpkg",
            "oppningar",
        )
        self.assertEqual(o7_rader[0]["typ"], "trumma")
        _, o8_rader = las_gpkg_tabell(
            TESTSVIT_ROT / "A8_port_byggnad" / "indata" / "oppningar.gpkg",
            "oppningar",
        )
        self.assertEqual(o8_rader[0]["typ"], "port")

        # A9: verklig sänka betydligt djupare än brusamplitud & osäkerhet
        f9 = specs["A9"].facit
        self.assertEqual(f9["antal_verkliga_sankor"], 1)
        self.assertGreater(f9["verklig_sanka_djup_m"], 10.0 * f9["brus_amplitud_m"])

        # A10: djup = regnvolym / area (taken avvattnas inte till gården i modellen)
        f10 = specs["A10"].facit
        self.assertAlmostEqual(f10["volym_m3"], f10["gard_area_m2"] * f10["regn_mm"] / 1000.0)
        self.assertAlmostEqual(f10["djup_m"], f10["volym_m3"] / f10["gard_area_m2"])
        self.assertFalse(f10["regn_pa_tak_raknas"])
        # A11: (120 - 60) mm/h i 15 min = 15 mm, sedan -60 mm/h: tomt efter 30 min
        f11 = specs["A11"].facit
        self.assertAlmostEqual(f11["max_djup_m"], (120.0 - f11["avtappning_mm_h"]) * 0.25 / 1000)
        self.assertAlmostEqual(f11["tom_efter_s"], 900.0 + f11["max_djup_m"] * 1000 / 60 * 3600)
        self.assertAlmostEqual(f11["avtappat_m3"], f11["gard_area_m2"] * 0.030)

    def test_kor_testsvit_grupp_a_passerar_alla_tolv(self) -> None:
        resultat, tabell = kor_testsvit(rot=TESTSVIT_ROT, grupp="A")
        self.assertEqual(len(resultat), 12)
        self.assertTrue(all(r.godkand for r in resultat), tabell)
        ids = [r.fall.id for r in resultat]
        self.assertEqual(
            ids, ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11"]
        )
        for r in resultat:
            if r.fall.id != "A0":
                self.assertEqual(r.utfall, "GODKAND")
                self.assertIsNotNone(r.volymfel_rel)
                self.assertIsNotNone(r.maxdjupsfel_m)
                assert r.volymfel_rel is not None
                assert r.maxdjupsfel_m is not None
                self.assertLess(r.volymfel_rel, 0.001)
                self.assertLess(r.maxdjupsfel_m, 0.01)

    @unittest.skipUnless(HAR_RASTERIO, "rasterio saknas i denna miljö (körs i CI)")
    def test_rasterio_validerar_alla_geotiff(self) -> None:
        import rasterio

        for fall_id, mappnamn in FORVANTADE_A_FALL:
            indata = TESTSVIT_ROT / mappnamn / "indata"
            for filnamn, forvantad_dtype in (
                ("mark.tif", "float32"),
                ("osakerhet.tif", "float32"),
                ("markslag.tif", "uint8"),
            ):
                with rasterio.open(indata / filnamn) as src:
                    self.assertEqual(
                        src.crs.to_epsg(),
                        EPSG_SWEREF99_1800,
                        f"{fall_id}/{filnamn} fel CRS",
                    )
                    self.assertEqual(src.res, (PIXELSTORLEK_M, PIXELSTORLEK_M))
                    self.assertEqual(src.dtypes[0], forvantad_dtype)
                    self.assertGreater(src.height, 0)
                    self.assertGreater(src.width, 0)
                    data = src.read(1)
                    self.assertEqual(data.shape, (src.height, src.width))

    @unittest.skipUnless(HAR_GEOPANDAS, "geopandas saknas i denna miljö (körs i CI)")
    def test_geopandas_validerar_alla_gpkg(self) -> None:
        import geopandas as gpd

        for fall_id, mappnamn in FORVANTADE_A_FALL:
            indata = TESTSVIT_ROT / mappnamn / "indata"
            gdf_b = gpd.read_file(indata / "brytlinjer.gpkg", layer="brytlinjer")
            self.assertIsNotNone(gdf_b.crs)
            self.assertEqual(gdf_b.crs.to_epsg(), EPSG_SWEREF99_1800, f"{fall_id} brytlinjer CRS")
            for kol in ("typ", "hojd_m", "geometry"):
                self.assertIn(kol, gdf_b.columns)

            gdf_o = gpd.read_file(indata / "oppningar.gpkg", layer="oppningar")
            self.assertIsNotNone(gdf_o.crs)
            self.assertEqual(gdf_o.crs.to_epsg(), EPSG_SWEREF99_1800, f"{fall_id} oppningar CRS")
            for kol in ("typ", "bredd_m", "botten_rh2000", "kalla", "geometry"):
                self.assertIn(kol, gdf_o.columns)


if __name__ == "__main__":
    unittest.main()
