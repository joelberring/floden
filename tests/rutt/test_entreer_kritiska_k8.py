"""Tester för entrénåbarhet, kritiska avsnitt, ensemble och K8-export (T07)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from gemensamt.hash import sha256_fil
from gemensamt.manifest import las_manifest
from rutt import (
    HAR_PYARROW,
    STATUS_EJ_NABAR,
    STATUS_LANGRE_GANGVAG,
    STATUS_NABAR,
    VagKant,
    VagNat,
    VagNod,
    analysera_entre_nabarhet,
    berakna_insatstider,
    berakna_ofarbar_sannolikhet,
    hitta_kritiska_avsnitt,
    las_testparametrar_fran_konfig,
    skriv_ruttresultat_k8,
)
from testsvit.k1_io import las_gpkg_tabell

REPO_ROT = Path(__file__).resolve().parents[2]
CONFIG_TOML = REPO_ROT / "config.toml"
HAR_GEOPANDAS: bool = importlib.util.find_spec("geopandas") is not None


class TestEntreerKritiskaK8(unittest.TestCase):
    """Verifierar entréer, kritiska gatuavsnitt, ensemble och K8/K0-export."""

    def test_entre_nabarhet_alla_tre_status(self) -> None:
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        pixel = 5.0
        entre = (2, 1)
        uppstallning = [(2, 7)]  # rakt avstånd = 6 * 5 = 30 m (<= 100 m)

        # 1. Torr rak gångväg -> "nabar", gangvag_m == 30.0 m
        djup_torr = np.zeros((7, 9), dtype=np.float32)
        fara_torr = np.zeros((7, 9), dtype=np.float32)
        post_nabar = analysera_entre_nabarhet(
            "e1",
            entre,
            uppstallning,
            djup_torr,
            fara_torr,
            pixel,
            param,
            t_s=0.0,
            skarp=False,
        )
        self.assertEqual(post_nabar.status, STATUS_NABAR)
        self.assertAlmostEqual(post_nabar.gangvag_m, 30.0)

        # 2. Översvämmad rak väg (djup > 0.20 m) men torr omväg via rad 0 -> "langre_gangvag"
        djup_omvag = np.zeros((7, 9), dtype=np.float32)
        djup_omvag[1:6, 3:6] = np.float32(0.40)  # blockerar mittkorridoren
        post_omvag = analysera_entre_nabarhet(
            "e1",
            entre,
            uppstallning,
            djup_omvag,
            fara_torr,
            pixel,
            param,
            t_s=300.0,
            skarp=False,
        )
        self.assertEqual(post_omvag.status, STATUS_LANGRE_GANGVAG)
        self.assertGreater(post_omvag.gangvag_m, post_nabar.gangvag_m)
        self.assertLessEqual(post_omvag.gangvag_m, 100.0)

        # 3a. Helt avskuren (vattenmur längs hela kolumn 4) -> "ej_nabar"
        djup_avskuren = np.zeros((7, 9), dtype=np.float32)
        djup_avskuren[:, 4] = np.float32(0.50)
        post_avskuren = analysera_entre_nabarhet(
            "e1",
            entre,
            uppstallning,
            djup_avskuren,
            fara_torr,
            pixel,
            param,
            t_s=600.0,
            skarp=False,
        )
        self.assertEqual(post_avskuren.status, STATUS_EJ_NABAR)
        self.assertTrue(math.isinf(post_avskuren.gangvag_m))

        # 3b. Entrén själv översvämmad över entre_gransdjup_m (0.10 m) -> "ej_nabar"
        djup_entre = np.zeros((7, 9), dtype=np.float32)
        djup_entre[entre] = np.float32(0.15)
        post_entre_oversv = analysera_entre_nabarhet(
            "e1",
            entre,
            uppstallning,
            djup_entre,
            fara_torr,
            pixel,
            param,
            t_s=600.0,
            skarp=False,
        )
        self.assertEqual(post_entre_oversv.status, STATUS_EJ_NABAR)

    def test_kritiska_avsnitt_och_ensemblesannolikhet(self) -> None:
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        noder = [
            VagNod("S", 150000.0, 6577000.0, 10.0),
            VagNod("H", 150200.0, 6577000.0, 9.5),
            VagNod("A1", 150400.0, 6577100.0, 11.0),
            VagNod("A2", 150400.0, 6577000.0, 11.0),
            VagNod("A3", 150400.0, 6576900.0, 11.0),
            VagNod("B1", 150100.0, 6577200.0, 11.0),
        ]
        # Enda tillfartsleden k_sh ger åtkomst till A1, A2, A3 (vinst = 3) om den hålls öppen.
        # k_sb1 ger åtkomst till B1 (vinst = 1).
        kanter = [
            VagKant("k_sh", "S", "H", 200.0, 50.0),
            VagKant("k_ha1", "H", "A1", 220.0, 30.0),
            VagKant("k_ha2", "H", "A2", 200.0, 30.0),
            VagKant("k_ha3", "H", "A3", 220.0, 30.0),
            VagKant("k_sb1", "S", "B1", 220.0, 30.0),
        ]
        graf = VagNat.fran_komponenter(noder, kanter)
        kant_djup = {
            "k_sh": 0.45,  # ofarbar för ambulans (gräns 0.30 m)
            "k_ha1": 0.0,
            "k_ha2": 0.0,
            "k_ha3": 0.0,
            "k_sb1": 0.40,
        }
        adresser = {"a1": "A1", "a2": "A2", "a3": "A3", "b1": "B1"}

        kritiska = hitta_kritiska_avsnitt(
            graf,
            stationer={"st1": "S"},
            adresser=adresser,
            t_s=600.0,
            kant_djup=kant_djup,
            parametrar=param,
            fordon_typ="ambulans",
            max_avsnitt=5,
            skarp=False,
        )
        self.assertEqual(len(kritiska), 2)
        self.assertEqual(kritiska[0].kant_id, "k_sh")
        self.assertEqual(kritiska[0].vinst, 3)
        self.assertEqual(kritiska[1].kant_id, "k_sb1")
        self.assertEqual(kritiska[1].vinst, 1)

        # Ensemblesannolikhet över 4 scenarier
        ensemble = [
            {"k_sh": 0.35, "k_sb1": 0.10},
            {"k_sh": 0.40, "k_sb1": 0.35},
            {"k_sh": 0.10, "k_sb1": 0.15},
            {"k_sh": 0.30, "k_sb1": 0.20},
        ]
        sannolikhet = berakna_ofarbar_sannolikhet(ensemble, gransdjup_m=0.30, vagnat=graf)
        self.assertAlmostEqual(sannolikhet["k_sh"], 0.75)
        self.assertAlmostEqual(sannolikhet["k_sb1"], 0.25)
        self.assertAlmostEqual(sannolikhet["k_ha1"], 0.0)

    def test_k8_export_och_reproducerbarhet(self) -> None:
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        noder = [
            VagNod("S", 150000.0, 6577000.0, 10.0),
            VagNod("H", 150200.0, 6577000.0, 9.5),
            VagNod("A1", 150400.0, 6577000.0, 11.0),
        ]
        kanter = [
            VagKant("k_sh", "S", "H", 200.0, 50.0),
            VagKant("k_ha1", "H", "A1", 200.0, 30.0),
        ]
        graf = VagNat.fran_komponenter(noder, kanter)

        insatstider = berakna_insatstider(
            graf,
            stationer={"st1": "S"},
            adresser={"a1": "A1"},
            tider_s=[0.0, 600.0],
            kant_djup_per_tid={
                0.0: {"k_sh": 0.0, "k_ha1": 0.0},
                600.0: {"k_sh": 0.45, "k_ha1": 0.0},
            },
            parametrar=param,
            fordon_typ="ambulans",
            skarp=False,
        )
        entre_post = analysera_entre_nabarhet(
            "e1",
            (1, 1),
            [(1, 3)],
            np.zeros((4, 5), dtype=np.float32),
            np.zeros((4, 5), dtype=np.float32),
            5.0,
            param,
            t_s=0.0,
            skarp=False,
        )
        kritiska = hitta_kritiska_avsnitt(
            graf,
            stationer={"st1": "S"},
            adresser={"a1": "A1"},
            t_s=600.0,
            kant_djup={"k_sh": 0.45, "k_ha1": 0.0},
            parametrar=param,
            fordon_typ="ambulans",
            skarp=False,
        )

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            mapp1 = Path(tmp1) / "k8"
            mapp2 = Path(tmp2) / "k8"
            filer1 = skriv_ruttresultat_k8(
                insatstider,
                [entre_post],
                kritiska,
                mapp1,
                kodversion="test-sha",
                start="2026-11-20T19:00:00+01:00",
            )
            filer2 = skriv_ruttresultat_k8(
                insatstider,
                [entre_post],
                kritiska,
                mapp2,
                kodversion="test-sha",
                start="2026-11-20T19:00:00+01:00",
            )

            ins_json = json.loads(filer1["insatstider_json"].read_text(encoding="utf-8"))
            self.assertEqual(ins_json["kontrakt"], "K8/v1")
            self.assertEqual(
                ins_json["kolumner"],
                ["adress_id", "station_id", "t_s", "insatstid_s", "torr_insatstid_s"],
            )
            self.assertEqual(len(ins_json["rader"]), 2)

            ent_json = json.loads(
                filer1["entreer_nabarhet_json"].read_text(encoding="utf-8")
            )
            self.assertEqual(ent_json["kontrakt"], "K8/v1")
            self.assertEqual(
                ent_json["kolumner"],
                ["entre_id", "t_s", "status", "gangvag_m"],
            )

            srs_id, gpkg_rader = las_gpkg_tabell(
                filer1["kritiska_avsnitt"], "kritiska_avsnitt"
            )
            self.assertEqual(srs_id, 3011)
            self.assertEqual(len(gpkg_rader), 1)
            self.assertEqual(gpkg_rader[0]["kant_id"], "k_sh")
            self.assertEqual(gpkg_rader[0]["vinst"], 1)

            manifest = las_manifest(filer1["run"])
            self.assertEqual(manifest.kontrakt, "K0/v1")
            self.assertEqual(manifest.modul, "rutt")
            for post in manifest.utdata:
                self.assertEqual(sha256_fil(mapp1 / post.fil), post.sha256)

            self.assertEqual(
                sha256_fil(filer1["insatstider_json"]),
                sha256_fil(filer2["insatstider_json"]),
            )
            self.assertEqual(
                sha256_fil(filer1["kritiska_avsnitt"]),
                sha256_fil(filer2["kritiska_avsnitt"]),
            )
            self.assertEqual(sha256_fil(filer1["run"]), sha256_fil(filer2["run"]))

    @unittest.skipUnless(HAR_PYARROW, "pyarrow ej installerat lokalt")
    def test_k8_parquet_scheman_i_ci(self) -> None:
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        pq: Any = importlib.import_module("pyarrow.parquet")
        graf = VagNat.fran_komponenter(
            [VagNod("S", 150000.0, 6577000.0, 10.0), VagNod("M", 150100.0, 6577000.0, 10.0)],
            [VagKant("k1", "S", "M", 100.0, 36.0)],
        )
        ins = berakna_insatstider(
            graf,
            {"st1": "S"},
            {"a1": "M"},
            [0.0],
            {0.0: {"k1": 0.0}},
            param,
            "ambulans",
            skarp=False,
        )
        ent = [
            analysera_entre_nabarhet(
                "e1",
                (0, 0),
                [(0, 2)],
                np.zeros((3, 3), dtype=np.float32),
                np.zeros((3, 3), dtype=np.float32),
                5.0,
                param,
                skarp=False,
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            filer = skriv_ruttresultat_k8(ins, ent, [], Path(tmp))
            t_ins = pq.read_table(str(filer["insatstider_parquet"]))
            self.assertEqual(
                tuple(t_ins.column_names),
                ("adress_id", "station_id", "t_s", "insatstid_s", "torr_insatstid_s"),
            )
            t_ent = pq.read_table(str(filer["entreer_nabarhet_parquet"]))
            self.assertEqual(
                tuple(t_ent.column_names),
                ("entre_id", "t_s", "status", "gangvag_m"),
            )

    @unittest.skipUnless(HAR_GEOPANDAS, "geopandas ej installerat lokalt")
    def test_k8_kritiska_gpkg_med_geopandas_i_ci(self) -> None:
        param = las_testparametrar_fran_konfig(CONFIG_TOML)
        gpd: Any = importlib.import_module("geopandas")
        graf = VagNat.fran_komponenter(
            [VagNod("S", 150000.0, 6577000.0, 10.0), VagNod("M", 150100.0, 6577000.0, 10.0)],
            [VagKant("k1", "S", "M", 100.0, 36.0)],
        )
        krit = hitta_kritiska_avsnitt(
            graf,
            {"st1": "S"},
            {"a1": "M"},
            0.0,
            {"k1": 0.50},
            param,
            "ambulans",
            skarp=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            filer = skriv_ruttresultat_k8([], [], krit, Path(tmp))
            gdf = gpd.read_file(filer["kritiska_avsnitt"], layer="kritiska_avsnitt")
            self.assertEqual(len(gdf), 1)
            self.assertEqual(int(gdf.iloc[0]["vinst"]), 1)
            self.assertEqual(str(gdf.iloc[0]["kant_id"]), "k1")
            self.assertEqual(gdf.crs.to_epsg(), 3011)


if __name__ == "__main__":
    unittest.main()
