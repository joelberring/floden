"""Enhetstester för svenska geodatainläsare och lokal SHA-256-cache."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from data_inlasning import (
    DataInlasningFel,
    LokalDataCache,
    hamta_nvdb_vagnat,
    hamta_sgu_jordarter,
    las_las_punktmoln,
    las_nvdb_vagnat_geojson,
    mappa_sgu_jordart,
    skriv_syntetisk_las,
    sok_lantmateriet_stac,
    tolka_sgu_geojson_features,
)
from gemensamt.koordinater import EPSG_INTERN
from rutt.dijkstra import tidsberoende_dijkstra
from rutt.parametrar import las_testparametrar_fran_konfig

REPO_ROT = Path(__file__).resolve().parents[2]
CONFIG_TOML = REPO_ROT / "config.toml"


class TestLokalDataCache(unittest.TestCase):
    """Verifierar deterministisk SHA-256-cache och upptäckt av korrupt data."""

    def test_cache_miss_spara_och_cache_traff(self) -> None:
        anrop: list[str] = []

        def mock_hamtare(url: str, headers: Mapping[str, str] | None) -> bytes:
            anrop.append(url)
            return json.dumps({"ok": True, "url": url}).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            cache = LokalDataCache(tmp, hamtare=mock_hamtare)
            url = "https://example.se/data.json?b=2"

            data1, post1 = cache.hamta_json(url, filnamn="test.json", kalla="Testkälla")
            self.assertEqual(data1["ok"], True)
            self.assertFalse(post1.fran_cache)
            self.assertTrue(post1.fil.exists())
            self.assertEqual(len(anrop), 1)

            data2, post2 = cache.hamta_json(url, filnamn="test.json", kalla="Testkälla")
            self.assertEqual(data2, data1)
            self.assertTrue(post2.fran_cache)
            self.assertEqual(post2.sha256, post1.sha256)
            self.assertEqual(len(anrop), 1)

    def test_korrupt_cachefil_upptacks_via_sha256(self) -> None:
        def mock_hamtare(url: str, headers: Mapping[str, str] | None) -> bytes:
            return b"ursprungligt-innehall"

        with tempfile.TemporaryDirectory() as tmp:
            cache = LokalDataCache(tmp, hamtare=mock_hamtare)
            post = cache.hamta_eller_lasa(
                "https://example.se/fil.bin",
                filnamn="fil.bin",
                kalla="Testkälla",
            )
            post.fil.write_bytes(b"manipulerat-innehall")

            with self.assertRaises(DataInlasningFel) as ctx:
                cache.hamta_eller_lasa(
                    "https://example.se/fil.bin",
                    filnamn="fil.bin",
                    kalla="Testkälla",
                )
            self.assertIn("SHA-256", str(ctx.exception))


class TestLantmaterietStacOchLas(unittest.TestCase):
    """Verifierar mockad STAC-sökning samt binär LAS 1.2 round-trip."""

    def test_sok_lantmateriet_stac_parsar_features(self) -> None:
        stac_svar: dict[str, Any] = {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "657_15_25_2022",
                    "collection": "laserdata",
                    "bbox": [17.95, 59.28, 18.02, 59.32],
                    "properties": {"datetime": "2022-05-14T10:00:00Z"},
                    "assets": {
                        "data": {"href": "https://api.lantmateriet.se/fil/657_15_25.laz"},
                        "thumbnail": {"href": "https://api.lantmateriet.se/thumb/657.png"},
                    },
                }
            ],
        }
        anropade_urler: list[str] = []

        def mock_hamtare(url: str, headers: Mapping[str, str] | None) -> bytes:
            anropade_urler.append(url)
            return json.dumps(stac_svar).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            cache = LokalDataCache(tmp, hamtare=mock_hamtare)
            poster = sok_lantmateriet_stac(
                (17.95, 59.28, 18.02, 59.32),
                samling="laserdata",
                cache=cache,
            )
            self.assertEqual(len(poster), 1)
            post = poster[0]
            self.assertEqual(post.item_id, "657_15_25_2022")
            self.assertEqual(post.samling, "laserdata")
            self.assertEqual(post.bbox_wgs84, (17.95, 59.28, 18.02, 59.32))
            self.assertEqual(
                post.tillgangar["data"],
                "https://api.lantmateriet.se/fil/657_15_25.laz",
            )
            self.assertEqual(post.tidpunkt, "2022-05-14T10:00:00Z")
            self.assertIn("collections=laserdata", anropade_urler[0])

    def test_las_roundtrip_med_3006_till_3011_transformator(self) -> None:
        e_3006 = np.array([671234.567, 671240.125, 671250.000], dtype=np.float64)
        n_3006 = np.array([6576123.456, 6576130.789, 6576140.250], dtype=np.float64)
        z_rh2000 = np.array([12.345, 14.500, 18.750], dtype=np.float64)
        klass = np.array([2, 2, 17], dtype=np.uint8)  # 2 = mark, 17 = bro

        def mock_3006_till_3011(
            x: NDArray[np.float64],
            y: NDArray[np.float64],
        ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
            return x - 521000.0, y + 120.0

        with tempfile.TemporaryDirectory() as tmp:
            las_fil = Path(tmp) / "syntetisk.las"
            skriv_syntetisk_las(las_fil, e_3006, n_3006, z_rh2000, klass)

            moln = las_las_punktmoln(
                las_fil,
                kall_epsg=3006,
                transformator=mock_3006_till_3011,
            )
            self.assertEqual(moln.epsg, EPSG_INTERN)
            np.testing.assert_allclose(moln.e_m, e_3006 - 521000.0, atol=1e-3)
            np.testing.assert_allclose(moln.n_m, n_3006 + 120.0, atol=1e-3)
            np.testing.assert_allclose(moln.z_rh2000, z_rh2000, atol=1e-3)
            np.testing.assert_array_equal(moln.klass, klass)


class TestSguOchNvdb(unittest.TestCase):
    """Verifierar SGU-jordartsmappning samt NVDB-vägnät med bro och Dijkstra."""

    def test_sgu_jordarter_mappning_och_osakerhetsflagga(self) -> None:
        self.assertEqual(mappa_sgu_jordart("Sandig morän"), "moran")
        self.assertEqual(mappa_sgu_jordart("Postglacial lera"), "lera")
        self.assertEqual(mappa_sgu_jordart("Isälvssediment"), "sand")
        self.assertEqual(mappa_sgu_jordart("Urberg"), "berg")
        self.assertEqual(mappa_sgu_jordart("Kärrtorv"), "torv")
        self.assertEqual(mappa_sgu_jordart("Fyllning"), "fyllning")
        self.assertEqual(mappa_sgu_jordart("Silt"), "silt")

        geojson: dict[str, Any] = {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "sgu_1",
                    "properties": {"jordart": "Postglacial lera"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[150000.0, 6577000.0], [150050.0, 6577000.0], [150050.0, 6577050.0]]
                        ],
                    },
                },
                {
                    "id": "sgu_2",
                    "properties": {"jordart": "Mycket ovanlig okänd blandjord"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[150100.0, 6577000.0], [150150.0, 6577000.0], [150150.0, 6577050.0]]
                        ],
                    },
                },
            ],
        }
        ytor = tolka_sgu_geojson_features(geojson, kall_epsg=3011)
        self.assertEqual(len(ytor), 2)
        self.assertEqual(ytor[0].floden_jordart, "lera")
        self.assertFalse(ytor[0].osaker)
        self.assertEqual(ytor[1].floden_jordart, "moran")
        self.assertTrue(ytor[1].osaker)

        def mock_hamtare(url: str, headers: Mapping[str, str] | None) -> bytes:
            return json.dumps(geojson).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            cache = LokalDataCache(tmp, hamtare=mock_hamtare)
            hamtade = hamta_sgu_jordarter(
                (17.95, 59.28, 18.02, 59.32),
                cache=cache,
                kall_epsg=3011,
            )
            self.assertEqual(len(hamtade), 2)

    def test_nvdb_vagnat_med_bro_och_dijkstra(self) -> None:
        nvdb_geojson: dict[str, Any] = {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "vag_mark",
                    "properties": {"HTHAST": 36.0, "typ": "mark"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[150000.0, 6577000.0, 12.0], [150360.0, 6577000.0, 12.0]],
                    },
                },
                {
                    "id": "vag_bro",
                    "properties": {
                        "hastighet_km_h": 36.0,
                        "typ": "bro",
                        "vagbana_rh2000": 15.0,
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[150360.0, 6577000.0, 15.0], [150720.0, 6577000.0, 15.0]],
                    },
                },
            ],
        }

        graf = las_nvdb_vagnat_geojson(nvdb_geojson, kall_epsg=3011)
        self.assertEqual(len(graf.noder), 3)
        self.assertEqual(len(graf.kanter), 2)
        self.assertEqual(graf.kanter["vag_bro"].typ, "bro")
        self.assertAlmostEqual(graf.kanter["vag_bro"].langd_m, 360.0)
        self.assertEqual(graf.kanter["vag_bro"].vagbana_rh2000, 15.0)

        # Verifiera med tidsberoende_dijkstra: under bron är markdjupet 2.0 m
        # men vattenytan är 12.5 m < brobanan 15.0 m -> bron är helt farbar!
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        start_nod = graf.kanter["vag_mark"].fran_nod
        mal_nod = graf.kanter["vag_bro"].till_nod
        ankomst = tidsberoende_dijkstra(
            graf,
            start_nod,
            0.0,
            {0.0: {"vag_mark": 0.0, "vag_bro": 2.0}},
            param.fordon["ambulans"],
            vattenniva_per_tid={0.0: {"vag_mark": 12.0, "vag_bro": 12.5}},
        )
        # 360 m + 360 m vid 36 km/h (10 m/s) => 72.0 s
        self.assertAlmostEqual(ankomst[mal_nod], 72.0)

        def mock_hamtare(url: str, headers: Mapping[str, str] | None) -> bytes:
            return json.dumps(nvdb_geojson).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            cache = LokalDataCache(tmp, hamtare=mock_hamtare)
            graf_cachad = hamta_nvdb_vagnat("https://example.se/nvdb.geojson", cache=cache)
            self.assertEqual(set(graf_cachad.kanter), {"vag_mark", "vag_bro"})


if __name__ == "__main__":
    unittest.main()
