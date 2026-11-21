"""Dagsljuslösarna genom kärnans körmotor (K21) på syntetiska K1-rutor."""

from __future__ import annotations

import csv
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from dagsljus.floden_losare import las_fasadpunkter
from dagsljus.horisont import Hojdfalt, horisont_referens, riktningar_rad
from dagsljus.matt import VSC_OSKYMD
from karna.analyslosare import LosarFel, hamta_losare
from karna.korplan import kor_utredning, planera
from karna.paverkan import dagsljus_paverkansradie_m
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import las_geotiff, skriv_geotiff

from .geometri import vsc_kanjon_facit

REPO = Path(__file__).resolve().parents[2]
PAR = {"hojd_max_m": 12.0, "h_min_grader": 10.0, "azimutriktningar": 16}


def _skriv_ruta(mapp: Path, x_min: float, y_max: float, z: np.ndarray[Any, Any]) -> None:
    mapp.mkdir(parents=True)
    kw = {"x_min": x_min, "y_max": y_max, "pixelstorlek": 1.0}
    skriv_geotiff(mapp / "mark.tif", np.asarray(z, dtype=np.float32), **kw)
    skriv_geotiff(mapp / "osakerhet.tif", np.full(z.shape, 0.05, dtype=np.float32), **kw)
    skriv_geotiff(mapp / "markslag.tif", np.full(z.shape, 1, dtype=np.uint8), **kw)
    (mapp / "recept.json").write_text(json.dumps({"ruta_id": mapp.name}), encoding="utf-8")


def _fraga(bas: Path, analyser: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> Any:
    return fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "dagsljus-test",
            "scenarier": {"s": {"bas": str(bas)}, **(extra or {})},
            "analyser": analyser,
        },
        REPO,
    )


class TestLosare(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = ResultatCache(self.tmp / "cache")
        self.bas = self.tmp / "bas"
        self.bas.mkdir()
        # A: x 150000-150060, B: x 150060-150120 (grannar), C: 300 m bort (utanför radien).
        self.a = np.zeros((60, 60))
        self.b = np.zeros((60, 60))
        self.b[20:40, 10:20] = 12.0  # byggnad 12 m i B, vägg 10 m öster om gränsen
        self.c = np.zeros((60, 60))
        _skriv_ruta(self.bas / "mm_150000_6576000", 150000.0, 6576060.0, self.a)
        _skriv_ruta(self.bas / "mm_150060_6576000", 150060.0, 6576060.0, self.b)
        _skriv_ruta(self.bas / "mm_150400_6576000", 150400.0, 6576060.0, self.c)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_registrerade_och_radie_enligt_hmax_over_tan_hmin(self) -> None:
        for namn in ("horisont", "soltimmar_mark", "svf_mark", "vsc_fasad"):
            self.assertEqual(hamta_losare(f"dagsljus.{namn}").doman, "dagsljus")
        losare = hamta_losare("dagsljus.svf_mark")
        p = losare.validera_parametrar({})
        self.assertAlmostEqual(
            losare.paverkansradie_m(p) or 0.0, 60.0 / math.tan(math.radians(5.0)), places=9
        )
        self.assertEqual(losare.paverkansradie_m(p), dagsljus_paverkansradie_m(60.0, 5.0))

    def test_okanda_och_ogiltiga_parametrar_ger_fel(self) -> None:
        for namn, p in (
            ("dagsljus.svf_mark", {"stavfel": 1}),
            ("dagsljus.svf_mark", {"h_min_grader": 0}),
            ("dagsljus.svf_mark", {"h_min_grader": 95}),
            ("dagsljus.svf_mark", {"azimutriktningar": 3}),
            ("dagsljus.svf_mark", {"omsampling": "nej"}),
            ("dagsljus.soltimmar_mark", {"datum": []}),
            ("dagsljus.soltimmar_mark", {"steg_min": 7}),
            ("dagsljus.vsc_fasad", {"halvplansriktningar": 2}),
        ):
            with self.assertRaises(LosarFel, msg=f"{namn} {p}"):
                hamta_losare(namn).validera_parametrar(p)

    def test_svf_mot_referens_med_granne_i_mosaiken(self) -> None:
        res = kor_utredning(
            _fraga(self.bas, [{"id": "svf", "losare": "dagsljus.svf_mark", "parametrar": PAR}]),
            self.cache,
            self.tmp / "ut",
        )
        radie = dagsljus_paverkansradie_m(12.0, 10.0)
        svf, geo = las_geotiff(self.tmp / "ut" / "svf" / "s" / "mm_150000_6576000" / "svf.tif")
        self.assertEqual(svf.shape, (60, 60))
        self.assertAlmostEqual(geo.x_min, 150000.0)
        # Facit: samma höjdfält sammanfogat för hand (A och B), referensimplementationen.
        z = np.hstack([self.a, self.b])
        falt = Hojdfalt(z, 1.0, 150000.0, 6576060.0)
        ii, jj = np.array([30, 30, 10, 50]), np.array([58, 30, 5, 59])
        x = 150000.0 + jj + 0.5
        y = 6576060.0 - ii - 0.5
        summa = np.zeros(4)
        trunk = np.zeros(4, dtype=bool)
        for phi in riktningar_rad(16):
            r = horisont_referens(falt, x, y, z[ii, jj], np.full(4, phi), radie)
            summa += np.sin(r.h_rad) ** 2
            trunk |= r.trunkerad
        facit = 1.0 - summa / 16
        np.testing.assert_allclose(svf[ii, jj], facit, atol=2e-6)
        # Cellen vid väggen är skymd; en cell långt från den är det inte lika mycket.
        self.assertLess(float(svf[30, 58]), float(svf[30, 30]))
        jobb = [j for j in res.sammanstallning["jobb"] if j["analys"] == "svf"]
        self.assertEqual(len(jobb), 3)
        self.assertGreater(jobb[0]["matvarden"]["celler"], 0)

    def test_lokal_omrakning_ruta_c_ligger_utanfor_radien(self) -> None:
        """Ändring i B ska byta nyckel för A (granne inom radien 68 m) och B, men inte för C."""
        analys = [{"id": "svf", "losare": "dagsljus.svf_mark", "parametrar": PAR}]
        plan1 = planera(_fraga(self.bas, analys), self.cache)
        bas2 = self.tmp / "bas2"
        shutil.copytree(self.bas, bas2)
        b2 = self.b.copy()
        b2[20:40, 10:20] = 20.0
        shutil.rmtree(bas2 / "mm_150060_6576000")
        _skriv_ruta(bas2 / "mm_150060_6576000", 150060.0, 6576060.0, b2)
        plan2 = planera(_fraga(bas2, analys), self.cache)
        n1 = {j.ruta.ruta_id: j.nyckel for j in plan1.jobb if j.ruta}
        n2 = {j.ruta.ruta_id: j.nyckel for j in plan2.jobb if j.ruta}
        self.assertNotEqual(n1["mm_150000_6576000"], n2["mm_150000_6576000"])
        self.assertNotEqual(n1["mm_150060_6576000"], n2["mm_150060_6576000"])
        self.assertEqual(n1["mm_150400_6576000"], n2["mm_150400_6576000"])

    def test_horisont_sparas_kvantiserad_och_deterministisk(self) -> None:
        analys = [{"id": "hor", "losare": "dagsljus.horisont", "parametrar": PAR}]
        kor_utredning(_fraga(self.bas, analys), self.cache, self.tmp / "ut")
        mapp = self.tmp / "ut" / "hor" / "s" / "mm_150000_6576000"
        h = np.load(mapp / "horisont.npy")
        meta = json.loads((mapp / "horisont.json").read_text(encoding="utf-8"))
        self.assertEqual(h.shape, (16, 60, 60))
        self.assertEqual(h.dtype, np.uint16)
        self.assertEqual(meta["azimutriktningar"], 16)
        # Cell (30, 59), 11,5 m (mittpunkt) från väggen i öster (riktning 4 = 90 grader):
        # atan(12 / 10,5) = 48,81 grader (väggen börjar vid kolumn 10 i B = 10 m från gränsen).
        self.assertAlmostEqual(
            h[4, 30, 59] / 100.0, math.degrees(math.atan(12.0 / 10.5)), delta=0.01
        )
        # Samma fråga i en ny cache ger byte-identiska filer.
        cache2 = ResultatCache(self.tmp / "cache2")
        kor_utredning(_fraga(self.bas, analys), cache2, self.tmp / "ut2")
        m2 = self.tmp / "ut2" / "hor" / "s" / "mm_150000_6576000"
        self.assertEqual((mapp / "horisont.npy").read_bytes(), (m2 / "horisont.npy").read_bytes())

    def test_soltimmar_fria_ytor_ger_dagens_langd(self) -> None:
        p = {**PAR, "datum": ["2026-03-20"], "steg_min": 10}
        analys = [{"id": "sol", "losare": "dagsljus.soltimmar_mark", "parametrar": p}]
        res = kor_utredning(_fraga(self.bas, analys), self.cache, self.tmp / "ut")
        t, _ = las_geotiff(self.tmp / "ut" / "sol" / "s" / "mm_150400_6576000" / "soltimmar.tif")
        self.assertAlmostEqual(float(t.max()), 12.0, delta=0.15)  # dagjämning
        jobb = [j for j in res.sammanstallning["jobb"] if j["analys"] == "sol"]
        self.assertAlmostEqual(jobb[0]["matvarden"]["lat_grader"], 59.5, delta=0.5)  # E 150000
        self.assertTrue(any("azimutriktningar=16" in v for j in jobb for v in j["varningar"]))

    def test_nan_i_ruta_ger_fel(self) -> None:
        bas = self.tmp / "nanbas"
        bas.mkdir()
        z = np.zeros((20, 20))
        z[3, 3] = np.nan
        _skriv_ruta(bas / "mm_150000_6576000", 150000.0, 6576020.0, z)
        with self.assertRaises(Exception) as cm:
            kor_utredning(
                _fraga(bas, [{"id": "svf", "losare": "dagsljus.svf_mark", "parametrar": PAR}]),
                self.cache,
                self.tmp / "ut",
            )
        self.assertIn("NaN", str(cm.exception))


class TestVscFasad(unittest.TestCase):
    """Gaturum 21 m brett, väggar 20 m, i en enda ruta; fasadpunkt vid väggfoten."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = ResultatCache(self.tmp / "cache")
        n = 241
        z = np.zeros((n, n))
        kol = np.arange(n) - n // 2
        z[:, np.abs(kol) > 10] = 20.0  # gata |kolumn| <= 10 (21 celler), väggytor vid +-10,5 m
        self.bas = self.tmp / "bas"
        self.bas.mkdir()
        self.x0, self.y1 = 150000.0, 6576000.0 + n
        _skriv_ruta(self.bas / "mm_150000_6576000", self.x0, self.y1, z)
        self.xm = self.x0 + n / 2  # gatans mitt (x) = cellmittpunkt i kolumn 120
        self.ym = self.y1 - n / 2

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _punkter(self, rader: list[list[Any]]) -> Path:
        sokvag = self.tmp / "fasader.csv"
        with sokvag.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["fasad_id", "x_m", "y_m", "z_m", "normal_azimut_grader"])
            w.writerows(rader)
        return sokvag

    def test_vsc_vid_vaggfot_mot_kvadratur_och_fel_hanteras(self) -> None:
        # Fasadytan på västra väggens östsida (x = xm - 10,5), normal mot öster (90 grader).
        # Punkt på själva fasadytan; lösaren flyttar den utanför med utanfor_m = 0,5 (en halv cell).
        d = 21.0  # avstånd från punkten (xm - 10) till östra väggytan (xm + 10,5) = 20,5
        d = 20.5
        rader = [
            ["a", self.xm - 10.5, self.ym, 0.0, 90.0],
            ["fri_sida", self.xm - 10.5, self.ym, 0.0, 270.0],  # normal mot väggen: inuti hinder
            ["ute", self.xm + 1000.0, self.ym, 0.0, 90.0],
        ]
        radie = dagsljus_paverkansradie_m(20.0, 15.0)
        par = {"hojd_max_m": 20.0, "h_min_grader": 15.0, "halvplansriktningar": 256}
        res = kor_utredning(
            _fraga(
                self.bas,
                [
                    {
                        "id": "vsc",
                        "losare": "dagsljus.vsc_fasad",
                        "parametrar": par,
                        "indata": {"fasadpunkter": str(self._punkter(rader))},
                    }
                ],
            ),
            self.cache,
            self.tmp / "ut",
        )
        with (self.tmp / "ut" / "vsc" / "s" / "mm_150000_6576000" / "vsc.csv").open(
            encoding="utf-8"
        ) as fh:
            rd = {r["fasad_id"]: r for r in csv.DictReader(fh)}
        facit = 100.0 * vsc_kanjon_facit(20.0, d, radie)
        self.assertAlmostEqual(float(rd["a"]["vsc_procent"]), facit, delta=0.01)
        self.assertEqual(rd["a"]["status"], "ok")
        self.assertEqual(rd["fri_sida"]["status"], "inuti_hinder")
        self.assertEqual(rd["fri_sida"]["vsc_procent"], "")
        self.assertNotIn("ute", rd)  # utanför rutan: ägs inte av den
        jobb = res.sammanstallning["jobb"][0]
        self.assertTrue(any("inuti_hinder" in v or "under höjden" in v for v in jobb["varningar"]))
        self.assertLess(float(rd["a"]["vsc_procent"]), 100.0 * VSC_OSKYMD)

    def test_fasadfilsfel(self) -> None:
        d = self.tmp
        for namn, innehall in (
            ("a.csv", "fasad_id,x_m\n1,2\n"),
            ("b.csv", "fasad_id,x_m,y_m,z_m,normal_azimut_grader\n1,1,2,3,400\n"),
            ("c.csv", "fasad_id,x_m,y_m,z_m,normal_azimut_grader\n1,1,2,3,10\n1,1,2,3,10\n"),
            ("d.csv", "fasad_id,x_m,y_m,z_m,normal_azimut_grader\n1,nan,2,3,10\n"),
        ):
            (d / namn).write_text(innehall, encoding="utf-8")
            with self.assertRaises(LosarFel, msg=namn):
                las_fasadpunkter(d / namn)
        (d / "e.gpkg").write_text("x")
        with self.assertRaises(LosarFel):
            las_fasadpunkter(d / "e.gpkg")


if __name__ == "__main__":
    unittest.main()
