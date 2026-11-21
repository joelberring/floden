"""Enhetstester för CLI (`floden.cli`) och testsvitsköraren (`testsvit.korare`)."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import numpy as np

from floden.cli import main
from gemensamt.manifest import las_manifest
from testsvit.k1_io import las_geotiff
from testsvit.korare import TestsvitFel, formatera_tabell, hitta_testfall, kor_testsvit


class TestCliOchTestsvit(unittest.TestCase):
    def test_cli_help_avslutar_med_noll(self) -> None:
        ut = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(ut):
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        hjalptext = ut.getvalue()
        for kommando in ("markmodell", "sankgraf", "kor", "testsvit"):
            self.assertIn(kommando, hjalptext)

    def test_cli_markmodell_laser_dxf_barriar_idw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            laser_csv = tmp_path / "laser.csv"
            with laser_csv.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["e_m", "n_m", "z_m", "klass"])
                for e_idx in range(17):
                    for n_idx in range(17):
                        e_val = 150000.0 + e_idx * 0.5
                        n_val = 6576000.0 + n_idx * 0.5
                        z_val = 10.12 if n_val >= 6576004.0 else 10.00
                        writer.writerow([f"{e_val:.2f}", f"{n_val:.2f}", f"{z_val:.3f}", 2])

            dxf_fil = tmp_path / "baskarta.dxf"
            dxf_fil.write_text(
                "\n".join(
                    [
                        "0",
                        "SECTION",
                        "2",
                        "ENTITIES",
                        "0",
                        "LWPOLYLINE",
                        "8",
                        "KANTSTEN",
                        "70",
                        "0",
                        "10",
                        "150000.0",
                        "20",
                        "6576004.0",
                        "10",
                        "150008.0",
                        "20",
                        "6576004.0",
                        "0",
                        "ENDSEC",
                        "0",
                        "EOF",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            ut_mapp = tmp_path / "k1_ut"
            ut = io.StringIO()
            with redirect_stdout(ut):
                kod = main(
                    [
                        "markmodell",
                        "--ut",
                        str(ut_mapp),
                        "--laser-csv",
                        str(laser_csv),
                        "--dxf",
                        str(dxf_fil),
                        "--barriar-idw",
                        "--ruta-id",
                        "test_ruta",
                        "--upplosning-m",
                        "0.5",
                        "--json",
                    ]
                )

            self.assertEqual(kod, 0)
            data = json.loads(ut.getvalue())
            self.assertEqual(data["ruta_id"], "test_ruta")
            self.assertTrue(data["barriar_idw"])
            self.assertGreaterEqual(data["antal_brytlinjer"], 1)

            for filnamn in (
                "mark.tif",
                "osakerhet.tif",
                "markslag.tif",
                "brytlinjer.gpkg",
                "oppningar.gpkg",
                "recept.json",
                "run.json",
            ):
                self.assertTrue((ut_mapp / filnamn).is_file(), f"Saknar {filnamn}")

            from PIL import Image

            with Image.open(ut_mapp / "mark.tif") as tiff_img:
                self.assertEqual(tiff_img.format, "TIFF")
                mark_arr = np.asarray(tiff_img, dtype=np.float32)
            self.assertEqual(mark_arr.shape[0], data["form_rc"][0])
            self.assertEqual(mark_arr.shape[1], data["form_rc"][1])
            self.assertTrue(np.all(np.isfinite(mark_arr)))
            manifest = las_manifest(ut_mapp / "run.json")
            self.assertEqual(manifest.modul, "markmodell")
            self.assertEqual(manifest.parametrar["ruta_id"], "test_ruta")

            # Utan höjdkälla ska exit-kod 2 returneras
            err = io.StringIO()
            with redirect_stderr(err):
                fel_kod = main(["markmodell", "--ut", str(tmp_path / "tom_ut")])
            self.assertEqual(fel_kod, 2)

    def test_cli_sankgraf_a3_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ut_k2 = Path(tmp) / "k2_ut"
            ut = io.StringIO()
            with redirect_stdout(ut):
                kod = main(["sankgraf", "--k1", "A3", "--ut", str(ut_k2), "--json"])
            self.assertEqual(kod, 0)
            data = json.loads(ut.getvalue())
            self.assertEqual(data["antal_noder"], 3)
            self.assertEqual(data["antal_lovnoder"], 2)
            self.assertGreater(data["antal_flodesvagar"], 0)
            self.assertGreater(data["max_sankvolym_m3"], 0.0)
            self.assertTrue((ut_k2 / "sankgraf.json").is_file())
            self.assertTrue((ut_k2 / "cell_till_nod.tif").is_file())
            self.assertTrue((ut_k2 / "flodesvagar.gpkg").is_file())
            self.assertTrue((ut_k2 / "run.json").is_file())

    def test_cli_kor_a3_graf_t06_och_k6_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ut_k6 = Path(tmp) / "k6_graf"
            ut = io.StringIO()
            with redirect_stdout(ut):
                kod = main(
                    [
                        "kor",
                        "--k1",
                        "A3",
                        "--regn-id",
                        "stockholm_cds_6h_100ar_kf124",
                        "--jordart",
                        "sand",
                        "--modell",
                        "graf",
                        "--ut",
                        str(ut_k6),
                    ]
                )
            self.assertEqual(kod, 0)
            text = ut.getvalue()
            self.assertIn("T06 markförluster (sand)", text)
            self.assertIn("Maxdjup:", text)
            self.assertTrue((ut_k6 / "max_djup.tif").is_file())
            self.assertTrue((ut_k6 / "run.json").is_file())

            djup, _ = las_geotiff(ut_k6 / "max_djup.tif")
            self.assertGreater(float(np.max(djup)), 0.0)
            manifest = las_manifest(ut_k6 / "run.json")
            self.assertEqual(manifest.modul, "dynamik")
            self.assertLess(float(manifest.matvarden["volymfel_rel"]), 1e-3)

    def test_cli_kor_a1_hybrid_json(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(["kor", "--k1", "A1", "--modell", "hybrid", "--json"])
        self.assertEqual(kod, 0)
        data = json.loads(ut.getvalue())
        self.assertEqual(data["modell"], "hybrid")
        self.assertEqual(data["metod"], "hybrid")
        self.assertGreater(data["max_djup_m"], 0.0)
        self.assertLess(data["volymfel_rel"], 1e-3)


    def test_cli_testsvit_grupp_a(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(["testsvit", "--grupp", "A", "--rot", "testsvit"])
        self.assertEqual(kod, 0)
        text = ut.getvalue()
        self.assertIn("A0", text)
        self.assertIn("SKELETT", text)

    def test_kor_testsvit_och_tabell(self) -> None:
        fall = hitta_testfall(rot="testsvit", grupp="A")
        self.assertGreaterEqual(len(fall), 1)
        self.assertEqual(fall[0].id, "A0")

        resultat, tabell = kor_testsvit(rot="testsvit", grupp="A")
        self.assertTrue(all(r.godkand for r in resultat))
        self.assertIn("| ID | Grupp |", tabell)
        self.assertEqual(tabell, formatera_tabell(resultat))

    def test_kor_testsvit_saknad_rot_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(TestsvitFel):
            kor_testsvit(rot=Path(tmp) / "finns_ej")

    def test_cli_regn_lista_och_export(self) -> None:
        ut_lista = io.StringIO()
        with redirect_stdout(ut_lista):
            kod_lista = main(["regn", "--lista", "--region", "Stockholm"])
        self.assertEqual(kod_lista, 0)
        text = ut_lista.getvalue()
        self.assertIn("stockholm_cds_6h_100ar_kf124", text)
        self.assertIn("stockholm_cds_6h_100ar_kf140", text)
        self.assertIn("stockholm_cds_6h_500ar_kf140", text)

        with tempfile.TemporaryDirectory() as tmp:
            ut_fil = Path(tmp) / "sthlm100.csv"
            ut_exp = io.StringIO()
            with redirect_stdout(ut_exp):
                kod_exp = main(
                    ["regn", "--id", "stockholm_cds_6h_100ar_kf140", "--ut", str(ut_fil)]
                )
            self.assertEqual(kod_exp, 0)
            self.assertTrue(ut_fil.is_file())
            self.assertIn("tid_s,intensitet_mm_h", ut_fil.read_text(encoding="utf-8"))

    def test_cli_risk_a3(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(
                [
                    "risk",
                    "--omrade",
                    "A3",
                    "--gransdjup-m",
                    "0.20",
                    "--max-aterkomsttid-ar",
                    "100",
                    "--klimatfaktor",
                    "1.24",
                    "--tidshorisont-ar",
                    "50",
                ]
            )
        self.assertEqual(kod, 0)
        text = ut.getvalue()
        self.assertIn("Kumulativ översvämningsrisk", text)
        self.assertIn("Yta över gränsdjup", text)
        self.assertIn("| 100 |", text)

    def test_cli_risk_med_t06_jordart(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(
                [
                    "risk",
                    "--omrade",
                    "A3",
                    "--gransdjup-m",
                    "0.20",
                    "--max-aterkomsttid-ar",
                    "100",
                    "--klimatfaktor",
                    "1.24",
                    "--jordart",
                    "sand",
                ]
            )
        self.assertEqual(kod, 0)
        self.assertIn("Kumulativ översvämningsrisk", ut.getvalue())

    def test_cli_testsvit_grupp_c_ea(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(["testsvit", "--grupp", "C"])
        self.assertEqual(kod, 0)
        text = ut.getvalue()
        self.assertIn("EA1", text)
        self.assertIn("EA2", text)
        self.assertIn("EA8A", text)
        self.assertIn("3/3", text)

    def test_cli_webb_hjalp(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut), self.assertRaises(SystemExit) as ctx:
            main(["webb", "--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("--port", ut.getvalue())


if __name__ == "__main__":
    unittest.main()
