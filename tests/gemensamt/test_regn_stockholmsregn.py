"""Tester för IDF-källan Stockholmsregn 2024 (Tabell 7) i `gemensamt.regn` och CLI:t."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import shutil
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from floden.cli import main
from gemensamt.manifest import las_manifest
from gemensamt.regn import (
    DATA_REGN_MAPP,
    IDF_KALLOR,
    L_PER_S_HA_TILL_MM_H,
    RegnFel,
    generera_blockregn,
    generera_cds_regn,
    hamta_idf_kalla,
    las_idf_tabell_csv,
    las_stockholmsregn_2024,
    lista_idf_kallor,
    lista_regnforval,
    skapa_regn_fran_idf_kalla,
)

INTENSITET_CSV = DATA_REGN_MAPP / "stockholmsregn_2024_tabell7_intensitet_l_s_ha.csv"
VOLYM_CSV = DATA_REGN_MAPP / "stockholmsregn_2024_tabell7_volym_mm.csv"
SIDOKORT = DATA_REGN_MAPP / "stockholmsregn_2024.toml"


def _las_matris(fil: Path) -> tuple[list[float], list[float], list[list[float]]]:
    with fil.open(encoding="utf-8", newline="") as fh:
        rader = list(csv.reader(fh))
    t = [float(x) for x in rader[0][1:]]
    d = [float(r[0]) for r in rader[1:]]
    m = [[float(x) for x in r[1:]] for r in rader[1:]]
    return d, t, m


class TestStockholmsregnTabell(unittest.TestCase):
    def test_inlasning_axlar_och_storlek(self) -> None:
        tabell = las_stockholmsregn_2024()
        self.assertEqual(
            tabell.varaktigheter_min, (5, 10, 15, 20, 30, 60, 90, 120, 240, 360, 720, 1440)
        )
        self.assertEqual(tabell.aterkomsttider_ar, (1, 2, 5, 10, 20, 30, 50, 100, 200, 500, 1000))
        self.assertEqual(sum(len(r) for r in tabell.intensiteter_mm_h), 132)
        self.assertIn("Stockholmsregn 2024", tabell.kalla)
        self.assertIn("Tabell 7", tabell.kalla)
        self.assertIn("utan klimatfaktor", tabell.kalla)

    def test_volym_ar_intensitet_gange_varaktighet_gange_0_006_for_alla_celler(self) -> None:
        d_i, t_i, ints = _las_matris(INTENSITET_CSV)
        d_v, t_v, vols = _las_matris(VOLYM_CSV)
        self.assertEqual(d_i, d_v)
        self.assertEqual(t_i, t_v)
        antal = 0
        for d, rad_i, rad_v in zip(d_i, ints, vols, strict=True):
            for i_ls_ha, v_mm in zip(rad_i, rad_v, strict=True):
                # Intensitet avrundad till 0,1 l/(s*ha) och volym till 0,1 mm.
                tol = 0.05 * d * 0.006 + 0.05 + 1e-9
                self.assertLessEqual(abs(i_ls_ha * d * 0.006 - v_mm), tol, (d, i_ls_ha, v_mm))
                antal += 1
        self.assertEqual(antal, 132)

    def test_tabellerna_ar_monotona(self) -> None:
        for fil in (INTENSITET_CSV, VOLYM_CSV):
            _, _, m = _las_matris(fil)
            for rad in m:
                self.assertEqual(rad, sorted(rad), fil.name)
        _, _, vol = _las_matris(VOLYM_CSV)
        for k in range(len(vol[0])):
            kolumn = [r[k] for r in vol]
            self.assertEqual(kolumn, sorted(kolumn))
        _, _, ints = _las_matris(INTENSITET_CSV)
        for k in range(len(ints[0])):
            kolumn = [r[k] for r in ints]
            self.assertEqual(kolumn, sorted(kolumn, reverse=True))

    def test_stodpunkter_aterges_exakt_i_mm_h(self) -> None:
        tabell = las_stockholmsregn_2024()
        d_lista, t_lista, ints = _las_matris(INTENSITET_CSV)
        for d, rad in zip(d_lista, ints, strict=True):
            for t, i_ls_ha in zip(t_lista, rad, strict=True):
                forvantat = i_ls_ha * L_PER_S_HA_TILL_MM_H
                self.assertTrue(
                    math.isclose(tabell.intensitet_mm_h(d, t), forvantat, rel_tol=1e-12),
                    (d, t),
                )

    def test_enhetsomvandling_en_cell(self) -> None:
        # 60 min, 100 år: 127,8 l/(s*ha) = 46,008 mm/h (Tabell 7 volym: 46,0 mm på 1 h).
        tabell = las_stockholmsregn_2024()
        self.assertAlmostEqual(tabell.intensitet_mm_h(60, 100), 127.8 * 0.36, places=9)
        self.assertAlmostEqual(tabell.intensitet_mm_h(60, 100), 46.008, places=9)

    def test_interpolation_mellan_stodpunkter_ar_log_log(self) -> None:
        tabell = las_stockholmsregn_2024()
        i_lo = tabell.intensitet_mm_h(60, 100)
        i_hi = tabell.intensitet_mm_h(90, 100)
        d = 75.0
        vikt = (math.log(d) - math.log(60)) / (math.log(90) - math.log(60))
        forvantat = math.exp((1 - vikt) * math.log(i_lo) + vikt * math.log(i_hi))
        self.assertAlmostEqual(tabell.intensitet_mm_h(d, 100), forvantat, places=9)
        self.assertGreater(tabell.intensitet_mm_h(75, 100), tabell.intensitet_mm_h(90, 100))
        self.assertLess(tabell.intensitet_mm_h(75, 100), tabell.intensitet_mm_h(60, 100))
        # 1000 år finns i tabellen men 2000 år extrapoleras inte tyst.
        self.assertGreater(tabell.intensitet_mm_h(60, 1000), tabell.intensitet_mm_h(60, 500))
        with self.assertRaises(RegnFel):
            tabell.intensitet_mm_h(60, 2000)
        with self.assertRaises(RegnFel):
            tabell.intensitet_mm_h(2000, 100)

    def test_tabell_utan_klimatfaktor_och_faktor_multipliceras(self) -> None:
        tabell = las_stockholmsregn_2024()
        bas = tabell.intensitet_mm_h(60, 100)
        self.assertAlmostEqual(tabell.intensitet_mm_h(60, 100, klimatfaktor=1.4), 1.4 * bas)
        self.assertAlmostEqual(tabell.intensitet_mm_h(60, 100, klimatfaktor=1.25), 1.25 * bas)


class TestStockholmsregnSidokort(unittest.TestCase):
    def test_sidokort_innehaller_kalla_och_kontrollsummor(self) -> None:
        kort = tomllib.loads(SIDOKORT.read_text(encoding="utf-8"))
        self.assertEqual(kort["id"], "stockholmsregn-2024")
        self.assertEqual(kort["tabell"], "Tabell 7")
        self.assertEqual(kort["sida"], 30)
        self.assertFalse(kort["klimatfaktor_ingar"])
        self.assertEqual(kort["rekommenderad_klimatfaktor"], 1.4)
        self.assertIn("Geonor", kort["fotnot"])
        self.assertTrue(kort["url"].startswith("https://miljobarometern.stockholm.se/"))
        for post in kort["filer"].values():
            fil = DATA_REGN_MAPP / post["fil"]
            self.assertEqual(hashlib.sha256(fil.read_bytes()).hexdigest(), post["sha256"])

    def test_andrad_tabell_nekas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mapp = Path(tmp)
            for fil in (INTENSITET_CSV, VOLYM_CSV, SIDOKORT):
                shutil.copy(fil, mapp / fil.name)
            csv_fil = mapp / INTENSITET_CSV.name
            csv_fil.write_text(
                csv_fil.read_text(encoding="utf-8").replace("127.8", "128.8"), encoding="utf-8"
            )
            kalla = replace(IDF_KALLOR["stockholmsregn-2024"], sidokort=mapp / SIDOKORT.name)
            with self.assertRaisesRegex(RegnFel, "sha256"):
                kalla.las()

    def test_registrerad_vid_sidan_av_dahlstrom(self) -> None:
        self.assertEqual(lista_idf_kallor(), ["stockholmsregn-2024"])
        with self.assertRaises(RegnFel):
            hamta_idf_kalla("finns-inte")
        # Befintliga förval och Dahlström är oförändrade.
        self.assertEqual(len(lista_regnforval()), 40)
        self.assertEqual(len(lista_regnforval(region="Stockholm")), 10)

    def test_manifestparametrar_skriver_kalla_och_faktor(self) -> None:
        par = hamta_idf_kalla("stockholmsregn-2024").manifest_parametrar(1.4)
        self.assertEqual(par["idf_kalla"], "stockholmsregn-2024")
        self.assertEqual(par["idf_tabell"], "Tabell 7")
        self.assertEqual(par["idf_klimatfaktor"], 1.4)
        self.assertFalse(par["idf_klimatfaktor_ingar_i_tabell"])
        self.assertEqual(set(par["idf_sha256"]), {"intensitet", "volym"})


class TestStockholmsregnRegn(unittest.TestCase):
    def test_standardfaktor_ar_1_4_och_kan_vaxlas_till_1_25(self) -> None:
        std = skapa_regn_fran_idf_kalla("stockholmsregn-2024", 100, typ="block", varaktighet_min=60)
        self.assertEqual(std.klimatfaktor, 1.4)
        self.assertAlmostEqual(std.max_intensitet_mm_h, 127.8 * 0.36 * 1.4, places=9)
        self.assertAlmostEqual(std.total_djup_mm, 127.8 * 0.36 * 1.4, places=9)
        self.assertIn("kf1.4", std.regn_id)
        self.assertIn("Stockholmsregn 2024", std.statistikkalla)
        gammal = skapa_regn_fran_idf_kalla(
            "stockholmsregn-2024", 100, klimatfaktor=1.25, typ="block", varaktighet_min=60
        )
        self.assertEqual(gammal.klimatfaktor, 1.25)
        self.assertAlmostEqual(gammal.max_intensitet_mm_h / std.max_intensitet_mm_h, 1.25 / 1.4)
        ren = skapa_regn_fran_idf_kalla(
            "stockholmsregn-2024", 100, klimatfaktor=1.0, typ="block", varaktighet_min=60
        )
        self.assertAlmostEqual(ren.total_djup_mm, 46.008, places=9)

    def test_blockregn_volym_motsvarar_tabell_7_volym(self) -> None:
        # Tabell 7 volym (mm) = i * D * 0,006 inom avrundning; blockregnets totaldjup utan faktor.
        d_lista, t_lista, vols = _las_matris(VOLYM_CSV)
        for d, rad in zip(d_lista, vols, strict=True):
            for t, v in zip(t_lista, rad, strict=True):
                serie = generera_blockregn(
                    t, klimatfaktor=1.0, varaktighet_min=d, idf_tabell=las_stockholmsregn_2024()
                )
                self.assertLessEqual(abs(serie.total_djup_mm - v), 0.05 * d * 0.006 + 0.05 + 1e-9)

    def test_cds_6h_volymbevarande_och_volym_lika_med_tabellen(self) -> None:
        serie = skapa_regn_fran_idf_kalla("stockholmsregn-2024", 100, klimatfaktor=1.0)
        # 100 år, 360 min: 27,9 l/(s*ha) -> 10,044 mm/h -> 60,26 mm (Tabell 7 volym 60,3 mm).
        self.assertAlmostEqual(serie.total_djup_mm, 27.9 * 0.36 * 6.0, places=6)
        self.assertEqual(serie.typ, "cds")
        self.assertEqual(serie.varaktighet_min, 360.0)
        direkt = generera_cds_regn(
            100, klimatfaktor=1.0, idf_tabell=las_stockholmsregn_2024()
        )
        self.assertEqual(serie.intensitet_mm_h, direkt.intensitet_mm_h)
        # Klimatfaktor 1,4 multiplicerar hela kurvan.
        kf = skapa_regn_fran_idf_kalla("stockholmsregn-2024", 100)
        for a, b in zip(kf.intensitet_mm_h, serie.intensitet_mm_h, strict=True):
            self.assertAlmostEqual(a, 1.4 * b, places=9)

    def test_ogiltig_typ_och_aterkomsttid_utanfor_tabellen(self) -> None:
        with self.assertRaises(RegnFel):
            skapa_regn_fran_idf_kalla("stockholmsregn-2024", 100, typ="trekant")
        with self.assertRaises(RegnFel):
            skapa_regn_fran_idf_kalla("stockholmsregn-2024", 2000)

    def test_befintlig_idf_csv_lasare_ar_oforandrad(self) -> None:
        tabell = las_idf_tabell_csv(INTENSITET_CSV)
        self.assertEqual(tabell.intensiteter_mm_h[5][7], 127.8)  # oomvandlat: l/(s*ha)


class TestStockholmsregnCli(unittest.TestCase):
    def test_cli_regn_idf_standardfaktor_1_4(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(
                [
                    "regn", "--idf", "stockholmsregn-2024", "--aterkomsttid-ar", "100",
                    "--typ", "block", "--varaktighet-min", "60", "--json",
                ]
            )  # fmt: skip
        self.assertEqual(kod, 0)
        self.assertIn('"klimatfaktor": 1.4', ut.getvalue())
        self.assertIn("Stockholmsregn 2024", ut.getvalue())

    def test_cli_regn_idf_klimatfaktor_1_25(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(
                [
                    "regn", "--idf", "stockholmsregn-2024", "--aterkomsttid-ar", "100",
                    "--klimatfaktor", "1.25", "--json",
                ]
            )  # fmt: skip
        self.assertEqual(kod, 0)
        self.assertIn('"klimatfaktor": 1.25', ut.getvalue())

    def test_cli_regn_utan_idf_ar_oforandrad(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(["regn", "--id", "stockholm_cds_6h_100ar_kf140", "--json"])
        self.assertEqual(kod, 0)
        self.assertIn('"klimatfaktor": 1.4', ut.getvalue())
        self.assertIn("Dahlström", ut.getvalue())

    def test_cli_idf_kan_inte_kombineras_med_forval(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(
                ["regn", "--idf", "stockholmsregn-2024", "--id", "stockholm_cds_6h_100ar_kf140"]
            )
        self.assertEqual(kod, 2)

    def test_cli_kor_idf_skriver_kalla_och_faktor_i_manifestet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ut_k6 = Path(tmp) / "k6"
            ut = io.StringIO()
            with redirect_stdout(ut):
                kod = main(
                    [
                        "kor", "--k1", "A3", "--idf", "stockholmsregn-2024",
                        "--aterkomsttid-ar", "100", "--regntyp", "block",
                        "--varaktighet-min", "60", "--modell", "graf", "--ut", str(ut_k6),
                    ]
                )  # fmt: skip
            self.assertEqual(kod, 0)
            manifest = las_manifest(ut_k6 / "run.json")
            par = manifest.parametrar
            self.assertEqual(par["idf_kalla"], "stockholmsregn-2024")
            self.assertEqual(par["idf_klimatfaktor"], 1.4)
            self.assertEqual(par["regn_klimatfaktor"], 1.4)
            self.assertIn("Stockholmsregn 2024", par["regn_statistikkalla"])
            roller = {post.roll for post in manifest.indata}
            self.assertTrue({"idf_sidokort", "idf_intensitet", "idf_volym"} <= roller)

    def test_cli_risk_idf(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(
                [
                    "risk", "--omrade", "A3", "--gransdjup-m", "0.20",
                    "--max-aterkomsttid-ar", "100", "--idf", "stockholmsregn-2024",
                    "--tidshorisont-ar", "50", "--json",
                ]
            )  # fmt: skip
        self.assertEqual(kod, 0)
        self.assertIn("Stockholmsregn 2024", ut.getvalue())


if __name__ == "__main__":
    unittest.main()
