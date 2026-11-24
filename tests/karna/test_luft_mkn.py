"""`luft.mkn` (K21): monotont MKN-intervall ur en given timserie, med känt svar och belägg i K0-manifestet.

Serien är konstruerad så att svaret går att räkna för hand: bakgrund 10 µg/m³ och lokalt bidrag L ger
dygnsvärdet 10 + L·f, där f går från 0,8/1,25 = 0,64 till 1,25/0,8 = 1,5625.
"""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dagsljus.exempelkvarter import bygg_rutnat, rektangel
from karna.analyslosare import LosarFel, hamta_losare
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict

REPO = Path(__file__).resolve().parents[2]


def _serie(sokvag: Path, lokal_per_dygn: list[float]) -> Path:
    with sokvag.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["tid", "lokal_ug_m3"])
        for d, v in enumerate(lokal_per_dygn):
            for t in range(24):
                w.writerow([d * 24 + t, v])
    return sokvag


class TestLuftMkn(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        hus = [rektangel(150008.0, 150028.0, 6576008.0, 6576020.0, 12.0, "a")]
        cls.bas = bygg_rutnat(
            cls.tmp / "bas", hus, e_min_m=150000.0, n_min_m=6576000.0, antal=1, rutstorlek_m=40.0
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _kor(self, namn: str, serie: Path, **par: Any) -> dict[str, Any]:
        d = {
            "kontrakt": "K20/v1",
            "fraga_id": f"mkn-{namn}",
            "scenarier": {"bas": {"bas": str(self.bas)}},
            "analyser": [
                {
                    "id": "mkn",
                    "losare": "luft.mkn",
                    "parametrar": {"bakgrund_ug_m3": 10.0, **par},
                    "indata": {"timserie": str(serie)},
                }
            ],
        }
        ut = self.tmp / f"ut-{namn}"
        kor_utredning(fraga_fran_dict(d, REPO), ResultatCache(self.tmp / "cache"), ut)
        sam = json.loads((ut / "sammanstallning.json").read_text(encoding="utf-8"))
        return dict(sam["jobb"][0])

    def test_klarar_garanterat(self) -> None:
        # 10 dygn med L = 40 (osäkra: 35,6 under, 72,5 över 50), 20 dygn med L = 100 (74 och 166: över),
        # 335 dygn med L = 5 (13,2 och 17,8: under).
        serie = _serie(self.tmp / "a.csv", [40.0] * 10 + [100.0] * 20 + [5.0] * 335)
        j = self._kor("a", serie)
        m = j["matvarden"]
        self.assertEqual((m["lagst_antal_dygn"], m["hogst_antal_dygn"]), (20, 30))
        self.assertEqual((m["klarar_garanterat"], m["bryter_garanterat"]), (1, 0))
        self.assertEqual(m["antal_hela_dygn"], 365)
        lag, hog = 0.8 / 1.25, 1.25 / 0.8
        medel_l = (40.0 * 10 + 100.0 * 20 + 5.0 * 335) / 365
        self.assertAlmostEqual(m["arsmedel_lagst_ug_m3"], 10.0 + medel_l * lag, places=9)
        self.assertAlmostEqual(m["arsmedel_hogst_ug_m3"], 10.0 + medel_l * hog, places=9)
        self.assertFalse(j["varningar"])  # helt år: ingen varning
        self.assertTrue((self.tmp / "ut-a" / "mkn" / "bas" / "mkn.json").is_file())

    def test_bryter_garanterat_och_osaker(self) -> None:
        j = self._kor("b", _serie(self.tmp / "b.csv", [100.0] * 40 + [5.0] * 325))
        self.assertEqual((j["matvarden"]["bryter_garanterat"], j["matvarden"]["klarar_garanterat"]), (1, 0))
        j = self._kor("c", _serie(self.tmp / "c.csv", [40.0] * 36 + [5.0] * 329))
        m = j["matvarden"]
        self.assertEqual((m["lagst_antal_dygn"], m["hogst_antal_dygn"]), (0, 36))
        self.assertEqual((m["klarar_garanterat"], m["bryter_garanterat"]), (0, 0))

    def test_kortare_serie_varnar(self) -> None:
        j = self._kor("d", _serie(self.tmp / "d.csv", [5.0] * 30))
        self.assertTrue(any("inte ett helt år" in v for v in j["varningar"]))

    def test_parametrar_utan_gissat_forval(self) -> None:
        losare = hamta_losare("luft.mkn")
        with self.assertRaises(LosarFel):
            losare.validera_parametrar({})  # bakgrund saknas
        for fel in ({"bakgrund_ug_m3": -1.0}, {"bakgrund_ug_m3": 5.0, "x": 1},
                    {"bakgrund_ug_m3": 5.0, "emissionsfaktor_intervall": [1.0]}):
            with self.subTest(fel=fel), self.assertRaises(LosarFel):
                losare.validera_parametrar(fel)
        self.assertIsNone(losare.paverkansradie_m(losare.validera_parametrar({"bakgrund_ug_m3": 5.0})))
