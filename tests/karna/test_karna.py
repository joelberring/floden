"""Kärnan (fas 0): hashning, planscenario som diff, beräkningsgraf med cache, påverkan, frågor."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from gemensamt.manifest import las_manifest
from karna import (
    Andring,
    Berakningsgraf,
    Cache,
    Losare,
    Nod,
    Planscenario,
    dagsljus_paverkansradie_m,
    diff_scenarier,
    kanonisk_json,
    las_fraga,
    las_scenario,
    nyckel_for,
    ruta_inom_radie,
    skriv_scenario,
    tolka_fraga,
)
from karna.fraga import FragaFel, registrera_analystyp
from karna.graf import GrafFel
from karna.hashning import HashFel
from karna.scenario import ScenarioFel, platta_ut

POLY = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}


class TestHashning(unittest.TestCase):
    def test_ordning_och_noll_ger_samma_nyckel(self) -> None:
        self.assertEqual(nyckel_for({"a": 1, "b": [0.0]}), nyckel_for({"b": [-0.0], "a": 1}))
        self.assertNotEqual(nyckel_for({"a": 1}), nyckel_for({"a": 2}))
        self.assertEqual(kanonisk_json({"b": 1, "a": "å"}), '{"a":"å","b":1}')

    def test_ohashbart_ger_fel(self) -> None:
        for dåligt in ({"x": math.nan}, {"x": object()}, {1: 2}):
            with self.assertRaises(HashFel):
                nyckel_for(dåligt)


class TestScenario(unittest.TestCase):
    def setUp(self) -> None:
        self.fore = Planscenario("fore", ("receptB", "receptA"))
        self.alt_a = self.fore.gren(
            "alt-a",
            [
                Andring("byggnad_ny", "hus1", POLY, {"hojd_m": 18.0}),
                Andring("markslag", "gard", POLY, {"kod": 4}),
            ],
        )
        self.alt_b = self.alt_a.gren(
            "alt-b",
            [
                Andring("magasin", "mag1", POLY, {"volym_m3": 120.0}),
                Andring("byggnad_ny", "hus1", POLY, {"hojd_m": 15.0}),
            ],
        )
        self.kanda = {s.nyckel: s for s in (self.fore, self.alt_a, self.alt_b)}

    def test_nyckel_beror_pa_innehall_inte_namn_eller_ordning(self) -> None:
        a1 = Planscenario("x", ("r",), (Andring("magasin", "m", POLY, {"volym_m3": 1.0}),
                                         Andring("markslag", "g", POLY, {"kod": 4})))
        a2 = Planscenario("y", ("r",), (Andring("markslag", "g", POLY, {"kod": 4}),
                                         Andring("magasin", "m", POLY, {"volym_m3": 1.0})),
                          beskrivning="annan text")
        self.assertEqual(a1.nyckel, a2.nyckel)
        self.assertEqual(self.fore.bas_recept, ("receptA", "receptB"))

    def test_gren_arver_och_ersatter(self) -> None:
        platt = {a.andring_id: a for a in platta_ut(self.alt_b, self.kanda)}
        self.assertEqual(set(platt), {"hus1", "gard", "mag1"})
        self.assertEqual(platt["hus1"].egenskaper["hojd_m"], 15.0)

    def test_diff_b_mot_a(self) -> None:
        d = diff_scenarier(self.alt_a, self.alt_b, self.kanda)
        self.assertEqual([a.andring_id for a in d.tillagda], ["mag1"])
        self.assertEqual(d.borttagna, ())
        self.assertEqual([(x.andring_id, y.egenskaper["hojd_m"]) for x, y in d.andrade],
                         [("hus1", 15.0)])
        d0 = diff_scenarier(self.alt_b, self.fore, self.kanda)
        self.assertEqual(sorted(a.andring_id for a in d0.borttagna), ["gard", "hus1", "mag1"])

    def test_fil_rundresa_och_manipulation_upptacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = skriv_scenario(self.alt_b, Path(tmp) / "alt-b.json")
            self.assertEqual(las_scenario(p).nyckel, self.alt_b.nyckel)
            d = json.loads(p.read_text(encoding="utf-8"))
            d["andringar"][0]["egenskaper"]["volym_m3"] = 999.0
            p.write_text(json.dumps(d), encoding="utf-8")
            with self.assertRaises(ScenarioFel):
                las_scenario(p)

    def test_ogiltiga_andringar(self) -> None:
        with self.assertRaises(ScenarioFel):
            Andring("torn", "x", POLY)
        with self.assertRaises(ScenarioFel):
            Andring("byggnad_bort", "x")
        with self.assertRaises(ScenarioFel):
            Andring("magasin", "x")
        with self.assertRaises(ScenarioFel):
            dubbel = (Andring("magasin", "m", POLY), Andring("magasin", "m", POLY))
            Planscenario("s", ("r",), dubbel)
        saknad_foralder = Planscenario("s", ("r",), forald="0" * 64)
        with self.assertRaises(ScenarioFel):
            platta_ut(saknad_foralder, {})


class TestGraf(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.rot = Path(self.tmp.name)
        self.anrop: list[str] = []

        def las(indata: Mapping[str, Path], p: Mapping[str, Any], ut: Path) -> dict[str, Any]:
            self.anrop.append("las")
            tal = [float(x) for x in indata["fil"].read_text().split()]
            (ut / "tal.json").write_text(json.dumps(tal))
            return {"antal": len(tal)}

        def skala(indata: Mapping[str, Path], p: Mapping[str, Any], ut: Path) -> dict[str, Any]:
            self.anrop.append("skala")
            tal = json.loads((indata["tal"] / "tal.json").read_text())
            summa = sum(t * p["faktor"] for t in tal)
            (ut / "summa.txt").write_text(repr(summa))
            return {"summa": summa}

        self.las = Losare("las", "1", las, modul="test")
        self.skala = Losare("skala", "1", skala, modul="test")
        self.fil = self.rot / "in.txt"
        self.fil.write_text("1 2 3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def kedja(self, faktor: float, skala: Losare | None = None) -> Nod:
        inlasning = Nod(self.las, {"fil": self.fil})
        return Nod(skala or self.skala, {"tal": inlasning}, {"faktor": faktor})

    def test_andra_korningen_kommer_ur_cachen(self) -> None:
        cache = Cache(self.rot / "cache")
        r1 = Berakningsgraf(cache).kor(self.kedja(2.0))
        self.assertEqual((r1.matvarden["summa"], r1.fran_cache), (12.0, False))
        r2 = Berakningsgraf(cache).kor(self.kedja(2.0))
        self.assertEqual((r2.nyckel, r2.fran_cache, r2.matvarden["summa"]), (r1.nyckel, True, 12.0))
        self.assertEqual(self.anrop, ["las", "skala"])
        m = las_manifest(r1.katalog / "run.json")
        self.assertEqual(m.modul, "test")
        self.assertEqual(m.parametrar["nyckel"], r1.nyckel)
        self.assertEqual([f.roll for f in m.utdata], ["summa.txt"])

    def test_bara_beroende_noder_raknas_om(self) -> None:
        cache = Cache(self.rot / "cache")
        Berakningsgraf(cache).kor(self.kedja(2.0))
        self.anrop.clear()
        g = Berakningsgraf(cache)
        self.assertEqual(g.kor(self.kedja(3.0)).matvarden["summa"], 18.0)
        self.assertEqual(self.anrop, ["skala"])  # inläsningen kom ur cachen
        self.assertEqual(len(g.raknade), 1)
        self.anrop.clear()
        ny_version = Losare("skala", "2", self.skala.funktion, modul="test")
        Berakningsgraf(cache).kor(self.kedja(3.0, ny_version))
        self.assertEqual(self.anrop, ["skala"])
        self.anrop.clear()
        self.fil.write_text("1 2 3 4")
        r = Berakningsgraf(cache).kor(self.kedja(3.0))
        self.assertEqual((self.anrop, r.matvarden["summa"]), (["las", "skala"], 30.0))

    def test_fel_i_losaren_lamnar_ingen_cache(self) -> None:
        def trasig(i: Mapping[str, Path], p: Mapping[str, Any], ut: Path) -> None:
            (ut / "halvfardig.txt").write_text("x")
            raise RuntimeError("boom")

        cache = Cache(self.rot / "cache")
        nod = Nod(Losare("trasig", "1", trasig), {"fil": self.fil})
        g = Berakningsgraf(cache)
        with self.assertRaises(RuntimeError):
            g.kor(nod)
        self.assertFalse(cache.finns(g.nyckel(nod)))
        self.assertEqual([p.name for p in cache.katalog.iterdir()], [])

    def test_saknad_fil_och_ohashbar_parameter(self) -> None:
        g = Berakningsgraf(Cache(self.rot / "cache"))
        with self.assertRaises(GrafFel):
            g.kor(Nod(self.las, {"fil": self.rot / "finns-inte.txt"}))
        with self.assertRaises(HashFel):
            Nod(self.las, {}, {"x": math.inf})


class TestPaverkan(unittest.TestCase):
    def test_dagsljusradie(self) -> None:
        self.assertAlmostEqual(dagsljus_paverkansradie_m(20.0, 45.0), 20.0)
        facit = 20.0 / math.tan(math.radians(5))
        self.assertAlmostEqual(dagsljus_paverkansradie_m(20.0, 5.0), facit)
        with self.assertRaises(ValueError):
            dagsljus_paverkansradie_m(10.0, 0.0)

    def test_rutor_inom_radie(self) -> None:
        rutor = [(f"r{i}", (i * 500.0, 0.0, i * 500.0 + 500.0, 500.0)) for i in range(5)]
        self.assertEqual(ruta_inom_radie((600.0, 100.0, 700.0, 200.0), rutor, 0.0), ["r1"])
        self.assertEqual(ruta_inom_radie((600.0, 100.0, 700.0, 200.0), rutor, 400.0),
                         ["r0", "r1", "r2"])


class TestFraga(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        registrera_analystyp("test_vsc", "dagsljus")
        registrera_analystyp("test_blaytor", "vatten")

    def test_toml_fraga_och_nyckel(self) -> None:
        text = """
fraga = "grannar"
scenario = "alt-b"
jamfor_med = "alt-a"
utdata = ["karta", "tabell"]

[[analys]]
typ = "test_vsc"
radie = "auto"

[[analys]]
typ = "test_blaytor"
regn_mm = 40.0
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.toml"
            p.write_text(text, encoding="utf-8")
            f = las_fraga(p)
        self.assertEqual(f.moduler, ("dagsljus", "vatten"))
        n1 = f.nyckel({"alt-a": "a" * 64, "alt-b": "b" * 64})
        n2 = tolka_fraga({**f.till_dict(), "fraga": "annat namn"}).nyckel(
            {"alt-a": "a" * 64, "alt-b": "b" * 64}
        )
        self.assertEqual(n1, n2)
        self.assertNotEqual(n1, f.nyckel({"alt-a": "a" * 64, "alt-b": "c" * 64}))

    def test_okand_analys_och_nyckel_ger_fel(self) -> None:
        bas: dict[str, Any] = {"fraga": "f", "scenario": "s", "analys": [{"typ": "test_vsc"}]}
        with self.assertRaises(FragaFel):
            tolka_fraga({**bas, "analys": [{"typ": "finns_inte"}]})
        with self.assertRaises(FragaFel):
            tolka_fraga({**bas, "extra": 1})
        with self.assertRaises(FragaFel):
            tolka_fraga({**bas, "jamfor_med": "s"})
        with self.assertRaises(FragaFel):
            registrera_analystyp("test_vsc", "buller")


class TestKorFraga(unittest.TestCase):
    def test_fraga_kors_mot_tva_scenarier_med_cache(self) -> None:
        from karna.korning import kor_fraga

        anrop: list[int] = []

        def rakna(indata: Mapping[str, Path], p: Mapping[str, Any], ut: Path) -> dict[str, Any]:
            anrop.append(p["antal_hus"])
            return {"hus_x_faktor": p["antal_hus"] * p["faktor"]}

        losare = Losare("test.hus", "1", rakna, modul="testmodul")

        def bygg(steg: Any, scenario: Planscenario) -> Nod:
            hus = sum(1 for a in scenario.andringar if a.typ == "byggnad_ny")
            return Nod(losare, {}, {"antal_hus": hus, "faktor": steg.parametrar["faktor"],
                                    "scenario": scenario.nyckel})

        registrera_analystyp("test_husraknare", "testmodul", bygg)
        a = Planscenario("a", ("r",), (Andring("byggnad_ny", "h1", POLY, {"hojd_m": 9.0}),))
        b = Planscenario("b", ("r",), (Andring("byggnad_ny", "h1", POLY, {"hojd_m": 9.0}),
                                       Andring("byggnad_ny", "h2", POLY, {"hojd_m": 9.0})))
        f = tolka_fraga({"fraga": "hus", "scenario": "b", "jamfor_med": "a",
                         "analys": [{"typ": "test_husraknare", "faktor": 10}]})
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(Path(tmp))
            svar = kor_fraga(f, {"a": a, "b": b}, Berakningsgraf(cache))
            s = svar.steg[0]
            assert s.jamfort_med is not None
            self.assertEqual((s.resultat.matvarden["hus_x_faktor"],
                              s.jamfort_med.matvarden["hus_x_faktor"]), (20, 10))
            kor_fraga(f, {"a": a, "b": b}, Berakningsgraf(cache))
            self.assertEqual(anrop, [2, 1])  # andra gången ur cachen
            self.assertEqual(svar.nyckel, f.nyckel({"a": a.nyckel, "b": b.nyckel}))
            with self.assertRaises(FragaFel):
                kor_fraga(f, {"b": b}, Berakningsgraf(cache))

    def test_analys_utan_nodbyggare_kors_inte(self) -> None:
        from karna.korning import kor_fraga

        registrera_analystyp("test_bara_validering", "testmodul")
        f = tolka_fraga({"fraga": "x", "scenario": "s",
                         "analys": [{"typ": "test_bara_validering"}]})
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FragaFel):
            kor_fraga(f, {"s": Planscenario("s", ("r",))}, Berakningsgraf(Cache(Path(tmp))))


if __name__ == "__main__":
    unittest.main()
