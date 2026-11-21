"""Vattenmodulen: blåytor vid X mm mot analytiskt facit (A1, A3), scenario → lapp, kärnkoppling."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from floden.cli import main as cli_main
from gemensamt.hash import sha256_fil
from karna import Berakningsgraf, Cache, Planscenario, tolka_fraga
from karna.korning import kor_fraga
from karna.scenario import Andring
from vatten import (
    Blaytemodell,
    BlaytorFel,
    ScenarioLappFel,
    blaytor_nod,
    k1_filer,
    las_k1,
    registrera_bas,
    registrera_scenarier,
    scenario_till_lapp,
    tillampa_scenario,
)
from vatten.blaytor import blaytor_vid_mm, bygg_graf

ROT = Path(__file__).resolve().parents[2] / "testsvit"
A1 = ROT / "A1_enkel_sanka" / "indata"
A3 = ROT / "A3_tva_sankor_sammanslagning" / "indata"
VOLYM_TOL_REL = 1e-9


def facit(mapp: Path) -> dict[str, float]:
    return dict(json.loads((mapp.parent / "facit" / "facit.json").read_text(encoding="utf-8")))


def kvadrat(e0: float, n0: float, e1: float, n1: float) -> dict[str, object]:
    return {"type": "Polygon", "coordinates": [[[e0, n0], [e1, n0], [e1, n1], [e0, n1], [e0, n0]]]}


class TestBlaytorA1(unittest.TestCase):
    """A1: vertikalväggad sänka 100 m², botten 10,00, överfall 10,50, avrinningsyta 222,5 m²."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.m = Blaytemodell.fran_k1_mapp(A1)
        cls.f = facit(A1)

    def sanka(self, mm: float) -> tuple[float, float, bool, float]:
        r = self.m.vid_mm(mm)
        s = max(r.sankor, key=lambda s: s.max_volym_m3)
        self.assertLess(abs(r.in_m3 - r.lagrat_m3 - r.ut_m3) / max(r.in_m3, 1e-12), VOLYM_TOL_REL)
        self.assertLess(r.volymfel_rel, VOLYM_TOL_REL)
        return s.niva_rh2000, s.volym_m3, s.fylld, r.lagrat_m3

    def test_niva_foljer_volymen_exakt(self) -> None:
        for mm in (10.0, 40.0, 80.0, 200.0):
            niva, vol, fylld, _ = self.sanka(mm)
            self.assertAlmostEqual(niva, self.f["botten_rh2000"] + vol / self.f["sanka_area_m2"], 9)
            self.assertFalse(fylld)
            analytisk = self.f["botten_rh2000"] + mm / 1000 * self.f["avrinningsyta_m2"] / 100.0
            self.assertLess(abs(niva - analytisk), self.f["toleranser"]["maxdjupsfel_m_max"])

    def test_full_sanka(self) -> None:
        niva, vol, fylld, lagrat = self.sanka(300.0)
        self.assertTrue(fylld)
        self.assertAlmostEqual(niva, self.f["overfall_rh2000"], 6)
        self.assertAlmostEqual(vol, self.f["slutvolym_m3"], 4)
        self.assertAlmostEqual(lagrat, self.f["slutvolym_m3"], 4)

    def test_inget_regn(self) -> None:
        r = self.m.vid_mm(0.0)
        self.assertEqual((r.in_m3, r.lagrat_m3, float(np.max(r.djup_m))), (0.0, 0.0, 0.0))

    def test_initialforlust(self) -> None:
        with self.assertRaises(BlaytorFel):
            self.m.vid_mm(40.0, initialforlust_mm={4: 5.0})  # A1 är hårdgjort (1)
        utan = self.m.vid_mm(40.0)
        med = self.m.vid_mm(40.0, initialforlust_mm={1: 10.0})
        self.assertAlmostEqual(med.lagrat_m3, utan.lagrat_m3 * 30.0 / 40.0, 9)
        self.assertEqual(self.m.vid_mm(40.0, initialforlust_mm={1: 40.0}).lagrat_m3, 0.0)
        with self.assertRaises(BlaytorFel):
            self.m.vid_mm(-1.0)


class TestBlaytorA3(unittest.TestCase):
    """A3: två sänkor (botten 10,0 och 10,1) slås ihop över sadeln 10,3; gemensamt överfall 10,6."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.m = Blaytemodell.fran_k1_mapp(A3)
        cls.f = facit(A3)

    def test_sammanslagning_och_slutvolym(self) -> None:
        under = self.m.vid_mm(40.0)
        self.assertTrue(all(s.niva_rh2000 < self.f["inre_sadel_rh2000"] for s in under.sankor))
        over = self.m.vid_mm(80.0)
        lov = [s for s in over.sankor if s.max_volym_m3 < 20]
        self.assertEqual(len(lov), 2)
        self.assertTrue(all(s.fylld for s in lov))
        self.assertTrue(all(s.niva_rh2000 > self.f["inre_sadel_rh2000"] for s in over.sankor))
        full = self.m.vid_mm(300.0)
        self.assertAlmostEqual(full.lagrat_m3, self.f["slutvolym_m3"], 3)
        self.assertTrue(all(s.fylld for s in full.sankor))
        self.assertTrue(
            all(abs(s.niva_rh2000 - self.f["gemensam_slutniva_rh2000"]) < 1e-6 for s in full.sankor)
        )
        for r in (under, over, full):
            self.assertLess(r.volymfel_rel, VOLYM_TOL_REL)


class TestScenarioLapp(unittest.TestCase):
    def test_typer_mappas_och_okanda_ger_fel(self) -> None:
        s = Planscenario(
            "alt-a",
            ("r",),
            (
                Andring("byggnad_ny", "hus", kvadrat(0, 0, 4, 4),
                        {"entre_rh2000": 11.0, "entre_punkt": [2, 0], "hojd_m": 18.0}),
                Andring("markhojd", "plan", kvadrat(5, 5, 8, 8), {"niva_rh2000": 10.4}),
                Andring("markslag", "gard", kvadrat(0, 5, 3, 8), {"kod": 4}),
                Andring("magasin", "mag", kvadrat(9, 0, 12, 3), {"volym_m3": 12.0}),
                Andring("gata", "g", {"type": "LineString", "coordinates": [[0, 9], [10, 9]]},
                        {"sektion": "standard-12m"}),
                Andring("vegetation", "trad", kvadrat(1, 1, 2, 2), {"hojd_m": 8.0}),
            ),
        )
        with self.assertRaises(ScenarioLappFel):
            scenario_till_lapp(s)
        som = scenario_till_lapp(s, ignorera_typer={"vegetation"})
        lapp = som.lapp
        self.assertEqual(som.ignorerade, ("trad",))
        self.assertEqual(lapp.bas_recept, ("r",))
        self.assertEqual(lapp.byggnader[0].hinderhojd_m, 18.0)
        self.assertEqual(len(lapp.byggnader[0].polygon), 4)
        self.assertEqual((lapp.hojdytor[0].hojd_rh2000, lapp.markslag_andringar[0].markslag),
                         (10.4, 4))
        self.assertEqual((lapp.magasin[0].volym_m3, lapp.gator[0].sektion), (12.0, "standard-12m"))
        for andring in (
            Andring("byggnad_bort", "x", None, {"byggnads_id": "b1"}),
            Andring("markhojd", "d", kvadrat(0, 0, 1, 1), {"delta_m": 0.2}),
            Andring("byggnad_ny", "h", kvadrat(0, 0, 1, 1), {"hojd_m": 3.0}),
        ):
            with self.assertRaises(ScenarioLappFel):
                scenario_till_lapp(Planscenario("s", ("r",), (andring,)))


class TestScenarioMotFacit(unittest.TestCase):
    """En upphöjd yta i A1:s sänka minskar volymen; lokal omräkning = full omräkning."""

    def test_upphojd_botten_lokalt_som_fullt(self) -> None:
        filer = k1_filer(A1)
        k1 = las_k1(filer)
        bas = bygg_graf(k1)
        recept = sha256_fil(A1 / "recept.json")
        g = k1.geo
        # Sänkan: rader 10..50, kol 10..50 (0,25 m). Höj en 5 x 5 m ruta till 10,30.
        e0, n1 = g.x_min + 10 * 0.25, g.y_max - 10 * 0.25
        yta = kvadrat(e0, n1 - 5.0, e0 + 5.0, n1)
        hog = Andring("markhojd", "hog", yta, {"niva_rh2000": 10.3})
        s = Planscenario("hojd", (recept,), (hog,))
        tl = tillampa_scenario(bas, k1, s, bas_recept_sha256=recept)
        self.assertIsNotNone(tl.omrakning)
        full = bygg_graf(
            type(k1)(tl.tillampning.mark_z, tl.tillampning.osakerhet_z, tl.tillampning.markslag,
                     k1.geo, tl.tillampning.alla_brytlinjer, tl.tillampning.oppningar, {})
        )
        for mm in (40.0, 300.0):
            lokal = blaytor_vid_mm(tl.graf, mm)
            ref = blaytor_vid_mm(full, mm)
            np.testing.assert_allclose(lokal.djup_m, ref.djup_m, atol=1e-6)
        full300 = blaytor_vid_mm(tl.graf, 300.0)
        # 25 m² av 100 m² höjd 0,30 m: volymen minskar med exakt 25 · 0,30 = 7,5 m³.
        self.assertAlmostEqual(full300.lagrat_m3, 50.0 - 7.5, 3)


class TestKarnkoppling(unittest.TestCase):
    def test_graf_cache_och_fraga_med_jamforelse(self) -> None:
        recept = sha256_fil(A1 / "recept.json")
        registrera_bas(recept, A1)
        fore = Planscenario("fore", (recept,))
        g = las_k1(k1_filer(A1)).geo
        e0, n1 = g.x_min + 10 * 0.25, g.y_max - 10 * 0.25
        alt = fore.gren("alt", [Andring("markhojd", "hog", kvadrat(e0, n1 - 5, e0 + 5, n1),
                                        {"niva_rh2000": 10.3})])
        registrera_scenarier([fore, alt])
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(Path(tmp) / "cache")
            graf = Berakningsgraf(cache)
            r1 = graf.kor(blaytor_nod(fore, 300.0))
            self.assertAlmostEqual(r1.matvarden["lagrat_m3"], 50.0, 3)
            self.assertTrue((r1.katalog / "max_djup.tif").is_file())
            graf2 = Berakningsgraf(cache)
            r2 = graf2.kor(blaytor_nod(fore, 40.0))
            self.assertEqual(len(graf2.raknade), 1)  # sänkgrafen kom ur cachen
            self.assertFalse(r2.fran_cache)

            fraga = tolka_fraga({
                "fraga": "hojd-i-sankan", "scenario": "alt", "jamfor_med": "fore",
                "analys": [{"typ": "blaytor_mm", "regn_mm": 300.0}],
            })
            svar = kor_fraga(fraga, {"alt": alt, "fore": fore}, Berakningsgraf(cache))
            steg = svar.steg[0]
            self.assertEqual(steg.modul, "vatten")
            assert steg.jamfort_med is not None
            self.assertTrue(steg.jamfort_med.fran_cache)  # före = samma nod som r1
            self.assertAlmostEqual(steg.resultat.matvarden["lagrat_m3"], 42.5, 3)
            with self.assertRaises(BlaytorFel):
                Berakningsgraf(cache).kor(blaytor_nod(Planscenario("x", ("okand",)), 1.0))


class TestCli(unittest.TestCase):
    def test_blaytor_kommando(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kod = cli_main(["blaytor", "--k1", "A1", "--rot", str(ROT), "--mm", "300",
                            "--ut", tmp, "--json"])
            self.assertEqual(kod, 0)
            run = json.loads((Path(tmp) / "run.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(run["matvarden"]["lagrat_m3"], 50.0, 3)
            self.assertTrue((Path(tmp) / "max_djup.tif").is_file())
            self.assertTrue((Path(tmp) / "blaytor.json").is_file())


if __name__ == "__main__":
    unittest.main()
