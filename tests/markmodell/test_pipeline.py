"""Helhetstester för markmodellens beräkningskedja, K1-filer, osäkerhet och bild."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from gemensamt.manifest import las_manifest
from markmodell.brytlinjer import Brytlinje
from markmodell.hinder_oppningar import BYGGNAD, HARDGJORT, Byggnad, Oppning
from markmodell.osakerhet import berakna_osakerhet
from markmodell.pipeline import bygg_markmodell_ruta, skapa_jamforelsebild
from markmodell.recept import las_recept
from markmodell.rutnat import Ruta

HAR_RASTERIO = importlib.util.find_spec("rasterio") is not None
HAR_GEOPANDAS = importlib.util.find_spec("geopandas") is not None


class TestMarkmodellPipeline(unittest.TestCase):
    def test_osakerhet_flaggning_utan_markpunkter_inom_3m(self) -> None:
        tathet = np.full((10, 10), 2.0, dtype=np.float32)
        avstand = np.full((10, 10), 0.4, dtype=np.float32)
        # 12 celler saknar markpunkter inom 3 m (som exemplet i K0/v1)
        avstand[0, :10] = 4.2
        avstand[1, :2] = 3.5

        res = berakna_osakerhet(
            punkt_tathet_per_m2=tathet,
            avstand_till_punkt_m=avstand,
            under_byggnad=np.zeros((10, 10), dtype=bool),
            under_vegetation=np.zeros((10, 10), dtype=bool),
            skanning_alder_ar=2.0,
        )
        self.assertEqual(res.antal_utan_markpunkt_inom_grans, 12)
        self.assertEqual(res.varningar, ["12 celler utan markpunkter inom 3 m"])
        self.assertGreater(float(res.sigma_z_m[0, 0]), float(res.sigma_z_m[5, 5]))

    def test_helhetskorning_reproducerbarhet_och_k1_filer(self) -> None:
        ruta = Ruta(e_min_m=150000.0, n_min_m=6576000.0, storlek_m=24.0, overlapp_m=2.0)

        # Skapa deterministiskt punktmoln på 1 m rutnät med ett hål i mitten (>3 m bort)
        # samt en bro-punkt (klass 17) som ska filtreras bort.
        e_min, n_min, e_max, n_max = ruta.yttre_bbox
        es = np.arange(e_min + 0.5, e_max, 1.0)
        ns = np.arange(n_min + 0.5, n_max, 1.0)
        ge, gn = np.meshgrid(es, ns)
        pe = ge.ravel()
        pn = gn.ravel()

        # Lämna ett glapp kring (150012, 6576012) med radie 4 m för att trigga osäkerhetsvarning
        dist_glapp = np.hypot(pe - 150012.0, pn - 6576012.0)
        behall = dist_glapp >= 4.2
        pe = pe[behall]
        pn = pn[behall]
        pz = 10.0 + 0.02 * (pe - e_min)
        klasser = np.full(pe.shape, 2, dtype=np.int32)

        # Lägg till en falsk bro-punkt (klass 17) med +15 m höjd som inte får påverka marken
        pe = np.append(pe, 150004.0)
        pn = np.append(pn, 6576004.0)
        pz = np.append(pz, 25.0)
        klasser = np.append(klasser, 17)

        brytlinjer = [
            Brytlinje("kantsten", 0.12, ((150004.0, 6576002.0), (150004.0, 6576018.0))),
            Brytlinje("kantsten", 0.12, ((150004.18, 6576018.0), (150020.0, 6576018.0))),
        ]
        byggnader = [
            Byggnad(
                polygon=(
                    (150016.0, 6576002.0),
                    (150022.0, 6576002.0),
                    (150022.0, 6576008.0),
                    (150016.0, 6576008.0),
                ),
                hinderhojd_m=10.0,
            )
        ]
        oppningar = [
            Oppning(
                typ="port",
                bredd_m=1.5,
                botten_rh2000=10.1,
                kalla="baskarta",
                linje=((150015.5, 6576005.0), (150022.5, 6576005.0)),
            )
        ]

        with tempfile.TemporaryDirectory() as tmp:
            ut_a = Path(tmp) / "korning_a"
            ut_b = Path(tmp) / "korning_b"

            res_a = bygg_markmodell_ruta(
                ruta,
                ut_a,
                mark_punkter_e=pe,
                mark_punkter_n=pn,
                mark_punkter_z=pz,
                laser_klasser=klasser,
                brytlinjer=brytlinjer,
                byggnader=byggnader,
                oppningar=oppningar,
                kodversion="testsha1234",
                start_tid="2026-11-20T18:00:00+01:00",
            )
            res_b = bygg_markmodell_ruta(
                ruta,
                ut_b,
                mark_punkter_e=pe,
                mark_punkter_n=pn,
                mark_punkter_z=pz,
                laser_klasser=klasser,
                brytlinjer=brytlinjer,
                byggnader=byggnader,
                oppningar=oppningar,
                kodversion="testsha1234",
                start_tid="2026-11-20T18:00:00+01:00",
            )

            # Reproducerbarhet: exakt samma recept-sha256 och rasterinnehåll
            self.assertEqual(res_a.recept_sha256, res_b.recept_sha256)
            np.testing.assert_array_equal(res_a.mark_z, res_b.mark_z)
            np.testing.assert_array_equal(res_a.osakerhet_z, res_b.osakerhet_z)
            np.testing.assert_array_equal(res_a.markslag, res_b.markslag)

            # Snapping ska ha slutit de två lösa ändarna vid (150004, 6576018)
            self.assertEqual(res_a.snap_statistik.antal_slutna_andar, 2)

            # Bro-punkten (25 m) ska vara borta: ingen icke-byggnadscell > 12 m
            ej_bygg = res_a.markslag != BYGGNAD
            self.assertLess(float(np.max(res_a.mark_z[ej_bygg])), 12.0)

            # Varning om celler utan markpunkter inom 3 m ska finnas i både resultat och run.json
            self.assertTrue(any("utan markpunkter inom 3 m" in v for v in res_a.varningar))

            # Kontrollera att alla K1-filer + run.json skapades och är giltiga
            for namn in (
                "mark.tif",
                "osakerhet.tif",
                "markslag.tif",
                "brytlinjer.gpkg",
                "oppningar.gpkg",
                "recept.json",
                "run.json",
            ):
                self.assertTrue((res_a.katalog / namn).is_file(), f"Saknar {namn}")

            recept_last = las_recept(res_a.katalog / "recept.json")
            self.assertEqual(recept_last.recept_sha256(), res_a.recept_sha256)
            recept_json = json.loads((res_a.katalog / "recept.json").read_text(encoding="utf-8"))
            self.assertEqual(recept_json["recept_sha256"], res_a.recept_sha256)

            manifest = las_manifest(res_a.katalog / "run.json")
            self.assertEqual(manifest.modul, "markmodell")
            self.assertEqual(manifest.parametrar["recept_sha256"], res_a.recept_sha256)
            self.assertEqual(manifest.varningar, res_a.varningar)

            # Kontrollera GeoPackage-innehåll med sqlite3
            conn_b = sqlite3.connect(str(res_a.katalog / "brytlinjer.gpkg"))
            try:
                rader_b = conn_b.execute("SELECT typ, hojd_m FROM brytlinjer").fetchall()
                self.assertEqual(len(rader_b), 2)
                self.assertEqual(rader_b[0][0], "kantsten")
                srs = conn_b.execute(
                    "SELECT srs_id FROM gpkg_geometry_columns WHERE table_name='brytlinjer'"
                ).fetchone()
                self.assertEqual(srs, (3011,))
            finally:
                conn_b.close()

            conn_o = sqlite3.connect(str(res_a.katalog / "oppningar.gpkg"))
            try:
                rader_o = conn_o.execute(
                    "SELECT typ, bredd_m, botten_rh2000, kalla FROM oppningar"
                ).fetchall()
                self.assertEqual(rader_o, [("port", 1.5, 10.1, "baskarta")])
            finally:
                conn_o.close()

            # Kontrollera jämförelsebild PNG
            referens_z = np.full(ruta.form_rc, 10.2, dtype=np.float32)
            bild_fil = skapa_jamforelsebild(
                res_a.mark_z,
                referens_z,
                ruta,
                res_a.katalog / "jamforelse.png",
            )
            self.assertTrue(bild_fil.is_file())
            with Image.open(bild_fil) as img:
                self.assertEqual(img.format, "PNG")
                self.assertGreater(img.width, 600)
                self.assertGreater(img.height, 250)

            # Port-cellen inne i byggnadsfoten ska ha markslag HARDGJORT
            port_cell = ruta.koordinat_till_cell(150019.0, 6576005.0)
            self.assertEqual(int(res_a.markslag[port_cell]), HARDGJORT)

            # Extra CI-verifiering med rasterio/geopandas när paketen finns installerade
            if HAR_RASTERIO:
                import rasterio

                with rasterio.open(res_a.katalog / "mark.tif") as src:
                    self.assertEqual(src.crs.to_epsg(), 3011)
                    self.assertEqual(src.shape, ruta.form_rc)
                    np.testing.assert_allclose(src.read(1), res_a.mark_z, rtol=0, atol=1e-5)

            if HAR_GEOPANDAS:
                import geopandas as gpd

                gdf_b = gpd.read_file(res_a.katalog / "brytlinjer.gpkg", layer="brytlinjer")
                self.assertEqual(gdf_b.crs.to_epsg(), 3011)
                self.assertEqual(len(gdf_b), 2)

                gdf_o = gpd.read_file(res_a.katalog / "oppningar.gpkg", layer="oppningar")
                self.assertEqual(gdf_o.crs.to_epsg(), 3011)
                self.assertEqual(len(gdf_o), 1)


if __name__ == "__main__":
    unittest.main()
