"""Fas 3: 2D-lösaren som `vatten.2d` i kärnans graf, med före/efter via kor_fraga."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gemensamt.hash import sha256_fil
from karna import Berakningsgraf, Cache, Planscenario, tolka_fraga
from karna.korning import kor_fraga
from karna.scenario import Andring
from vatten import BlaytorFel, k1_filer, las_k1, nod_2d, registrera_bas

ROT = Path(__file__).resolve().parents[2] / "testsvit"
A1 = ROT / "A1_enkel_sanka" / "indata"
REGN = {"regn_tid_s": [0.0, 300.0], "regn_mm_h": [60.0, 60.0]}


def kvadrat(e0: float, n0: float, e1: float, n1: float) -> dict[str, object]:
    return {"type": "Polygon", "coordinates": [[[e0, n0], [e1, n0], [e1, n1], [e0, n1], [e0, n0]]]}


class Test2dIKarnan(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.recept = sha256_fil(A1 / "recept.json")
        registrera_bas(cls.recept, A1)
        g = las_k1(k1_filer(A1)).geo
        e0, n1 = g.x_min + 10 * 0.25, g.y_max - 10 * 0.25
        cls.fore = Planscenario("fore", (cls.recept,))
        cls.alt = cls.fore.gren("alt", [Andring("markhojd", "hog", kvadrat(e0, n1 - 5, e0 + 5, n1),
                                                {"niva_rh2000": 10.3})])

    def test_fraga_fore_efter_med_k6_och_cache(self) -> None:
        fraga = tolka_fraga({
            "fraga": "2d", "scenario": "alt", "jamfor_med": "fore",
            "analys": [{"typ": "maxdjup_2d", **REGN}],
        })
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(Path(tmp))
            svar = kor_fraga(fraga, {"alt": self.alt, "fore": self.fore}, Berakningsgraf(cache))
            s = svar.steg[0]
            assert s.jamfort_med is not None
            for r in (s.resultat, s.jamfort_med):
                for fil in ("max_djup.tif", "max_hastighet.tif", "fara.tif",
                            "tid_till_0_1m.tif", "tid_till_0_2m.tif", "run.json"):
                    self.assertTrue((r.katalog / fil).is_file(), fil)
                self.assertLess(r.matvarden["volymfel_rel"], 1e-9)
                self.assertGreater(r.matvarden["max_djup_m"], 0.0)
            self.assertEqual(s.jamfort_med.matvarden["andrade_celler"], 0)
            self.assertGreater(s.resultat.matvarden["andrade_celler"], 0)
            self.assertNotEqual(s.resultat.nyckel, s.jamfort_med.nyckel)
            # fara_2d är samma beräkning: samma nod, ur cachen.
            fara = tolka_fraga({"fraga": "f", "scenario": "fore",
                                "analys": [{"typ": "fara_2d", **REGN}]})
            svar2 = kor_fraga(fara, {"fore": self.fore}, Berakningsgraf(cache))
            self.assertTrue(svar2.steg[0].resultat.fran_cache)
            run = json.loads((s.resultat.katalog / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["parametrar"]["losare"], "vatten.2d")

    def test_avtappning_lika_med_intensitet_ger_inget_vatten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            g = Berakningsgraf(Cache(Path(tmp)))
            r = g.kor(nod_2d(self.fore, {**REGN, "avtappning_mm_h": 60.0}))
            self.assertLess(r.matvarden["max_djup_m"], 1e-9)
            self.assertAlmostEqual(r.matvarden["avtappat_m3"], r.matvarden["in_m3"],
                                   delta=1e-9 * r.matvarden["in_m3"])
            halv = g.kor(nod_2d(self.fore, {**REGN, "avtappning_mm_h": 60.0,
                                            "brunnar_igensatta_andel": 0.5}))
            self.assertAlmostEqual(halv.matvarden["avtappat_m3"], 0.5 * halv.matvarden["in_m3"],
                                   delta=1e-6 * halv.matvarden["in_m3"])
            utan = g.kor(nod_2d(self.fore, REGN))
            self.assertIn("saknas", str(utan.matvarden["sanktermer"]["avtappning"]))

    def test_fel_parametrar(self) -> None:
        with self.assertRaises(BlaytorFel):
            nod_2d(self.fore, {"regn_mm": 10.0})
        with self.assertRaises(BlaytorFel):
            nod_2d(self.fore, {"slut_tid_s": 10.0})
        with self.assertRaises(BlaytorFel):
            nod_2d(Planscenario("x", ("okand",)), REGN)


if __name__ == "__main__":
    unittest.main()
