# ruff: noqa: E501 - testdata med långa tabellrader (K9-ändringar på en rad vardera)
"""R3: K9 `brytlinje_ny`, `brytlinje_bort` och `oppning_ny` översätts till K1 och räknas i vattnet.

Fysiken testas på testsvitens A1 (en skålformad sänka, 15 x 15 m): en sluten mur runt sänkan stänger
ute allt regn utanför muren, så att sänkan bara får regnet på sin egen yta (100 m² x 0,1 m = 10 m³,
exakt); en öppning i muren släpper igenom vatten igen. Samma ändringar prövas på K1 v2 och v1 via
materialiseringen (`materialisera_scenario`).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from gemensamt.hash import sha256_fil
from karna.geometri import k1_version
from karna.resultatcache import ResultatCache
from karna.rutscenario import materialisera_scenario
from karna.scenario import Andring, Planscenario, ScenarioFel, skriv_scenario
from karna.utredning import ScenarioSpec
from lapp.kontrakt import LappFel, ProjektLapp
from lapp.tillampa import tillampa_lapp
from sankgraf.typer import las_brytlinjer_fran_gpkg, las_oppningar_fran_gpkg
from testsvit.k1_io import BrytlinjePost
from vatten import k1_filer, las_k1, scenario_till_lapp, tillampa_scenario
from vatten.blaytor import blaytor_vid_mm, bygg_graf

A1 = Path(__file__).resolve().parents[2] / "testsvit" / "A1_enkel_sanka" / "indata"
REGN_MM = 100.0


def linje(*punkter: tuple[float, float]) -> dict[str, Any]:
    return {"type": "LineString", "coordinates": [list(p) for p in punkter]}


class _A1:
    def setUp(self) -> None:
        self.k1 = las_k1(k1_filer(A1))
        self.bas = bygg_graf(self.k1)
        self.recept = sha256_fil(A1 / "recept.json")
        self.g = self.k1.geo

    def xy(self, kol: int, rad: int) -> tuple[float, float]:
        return (self.g.x_min + kol * 0.25, self.g.y_max - rad * 0.25)

    def ring_runt_sankan(self, hojd_m: float = 2.0) -> Andring:
        """Sluten mur runt sänkan: 10 x 10 m (celler 10..50), regn utanför ska inte komma in."""
        h = [self.xy(10, 10), self.xy(50, 10), self.xy(50, 50), self.xy(10, 50), self.xy(10, 10)]
        return Andring("brytlinje_ny", "mur", linje(*h), {"typ": "mur", "hojd_m": hojd_m})

    def oppning(self, bredd_m: float = 2.0) -> Andring:
        return Andring(
            "oppning_ny",
            "port",
            linje(self.xy(30, 6), self.xy(30, 14)),
            {"typ": "passage", "bredd_m": bredd_m, "botten_rh2000": 10.0},
        )

    def kor(self, andringar: list[Andring]) -> Any:
        sc = Planscenario("s", (self.recept,), tuple(andringar))
        tl = tillampa_scenario(self.bas, self.k1, sc, bas_recept_sha256=self.recept)
        return tl, blaytor_vid_mm(tl.graf, REGN_MM)


class TestVattnetRaknar(_A1, unittest.TestCase):
    def test_mur_runt_sankan_leder_om_vattnet_exakt(self) -> None:
        fore = blaytor_vid_mm(self.bas, REGN_MM)
        tl, efter = self.kor([self.ring_runt_sankan()])
        # Utan mur samlar sänkan regnet från hela 15 x 15 m; med muren bara från ytan innanför (10 x 10 m).
        self.assertGreater(float(fore.lagrat_m3), 20.0)
        self.assertAlmostEqual(float(efter.lagrat_m3), 10.0, places=4)
        self.assertLess(float(np.max(efter.djup_m)), float(np.max(fore.djup_m)))
        self.assertEqual(len(tl.tillampning.alla_brytlinjer), 1)
        self.assertTrue(tl.tillampning.andrad_mask.any())  # den lokala omräkningen får veta vad som rörts

    def test_oppning_i_muren_slapper_igenom_vatten(self) -> None:
        _, bara_mur = self.kor([self.ring_runt_sankan()])
        tl, med_oppning = self.kor([self.ring_runt_sankan(), self.oppning()])
        self.assertGreater(float(med_oppning.lagrat_m3), float(bara_mur.lagrat_m3) + 1.0)
        # Muren är klippt: två delar, och öppningen ligger kvar som K1-post med sin botten.
        delar = tl.tillampning.alla_brytlinjer
        self.assertEqual(len(delar), 2)
        (opp,) = tl.tillampning.oppningar
        self.assertEqual((opp.typ, opp.bredd_m, opp.botten_rh2000), ("passage", 2.0, 10.0))
        # Ingen del av muren ligger kvar inom öppningens bredd.
        from shapely.geometry import LineString

        zon = LineString(opp.koordinater).buffer(1.0 - 1e-3, cap_style="flat")
        for d in delar:
            self.assertFalse(LineString(d.koordinater).intersects(zon))

    def test_oppning_som_inte_korsar_muren_andrar_inget_i_muren(self) -> None:
        bort_fran = Andring(
            "oppning_ny",
            "trumma",
            linje(self.xy(5, 20), self.xy(5, 30)),
            {"typ": "trumma", "bredd_m": 1.0, "botten_rh2000": 10.5},
        )
        tl, _ = self.kor([self.ring_runt_sankan(), bort_fran])
        self.assertEqual(len(tl.tillampning.alla_brytlinjer), 1)

    def test_lagre_mur_an_marken_andrar_inget_men_hog_mur_gor(self) -> None:
        # Barriärens höjd är över den högre cellen: 0,05 m rymmer sänkans eget regn men inte mer.
        _, lag = self.kor([self.ring_runt_sankan(0.05)])
        _, hog = self.kor([self.ring_runt_sankan(2.0)])
        self.assertGreater(float(lag.lagrat_m3), float(hog.lagrat_m3) - 1e-9)

    def test_samma_scenario_ger_samma_resultat(self) -> None:
        _, a = self.kor([self.ring_runt_sankan(), self.oppning()])
        _, b = self.kor([self.oppning(), self.ring_runt_sankan()])
        self.assertEqual(float(a.lagrat_m3), float(b.lagrat_m3))
        np.testing.assert_array_equal(a.djup_m, b.djup_m)


class TestBrytlinjeBort(_A1, unittest.TestCase):
    def _med_bas_mur(self) -> list[BrytlinjePost]:
        h = (self.xy(10, 10), self.xy(50, 10), self.xy(50, 50), self.xy(10, 50), self.xy(10, 10))
        return [BrytlinjePost(typ="mur", hojd_m=2.0, koordinater=h)]

    def _lapp(self, andringar: list[Andring]) -> ProjektLapp:
        return scenario_till_lapp(Planscenario("s", ("r",), tuple(andringar))).lapp

    def test_bort_klipper_bara_delen_langs_valjaren(self) -> None:
        bas = self._med_bas_mur()
        v = Andring(
            "brytlinje_bort", "ta", linje(self.xy(25, 10), self.xy(35, 10)), {"tolerans_m": 0.5}
        )
        res = tillampa_lapp(
            self.k1.mark_z, self.k1.osakerhet_z, self.k1.markslag, self.g, self._lapp([v]),
            bas_recept_sha256=self.recept, bas_brytlinjer=bas,
        )
        delar = res.alla_brytlinjer
        self.assertEqual(len(delar), 2)  # luckan delar muren i två
        langd = sum(_langd(d) for d in delar)
        self.assertAlmostEqual(langd, 40.0 - (35 - 25) * 0.25 - 2 * 0.5, places=3)
        self.assertTrue(res.andrad_mask.any())

    def test_bort_med_fel_typ_lamnar_muren(self) -> None:
        bas = self._med_bas_mur()
        v = Andring(
            "brytlinje_bort", "ta", linje(self.xy(0, 10), self.xy(60, 10)), {"typ": "kantsten"}
        )
        res = tillampa_lapp(
            self.k1.mark_z, self.k1.osakerhet_z, self.k1.markslag, self.g, self._lapp([v]),
            bas_recept_sha256=self.recept, bas_brytlinjer=bas,
        )
        self.assertEqual(list(res.alla_brytlinjer), bas)
        self.assertFalse(res.andrad_mask.any())

    def test_bort_av_hela_muren_oppnar_vattnet_igen(self) -> None:
        bas = self._med_bas_mur()
        v = Andring(
            "brytlinje_bort", "ta", linje(*bas[0].koordinater), {"tolerans_m": 0.3}
        )
        res = tillampa_lapp(
            self.k1.mark_z, self.k1.osakerhet_z, self.k1.markslag, self.g, self._lapp([v]),
            bas_recept_sha256=self.recept, bas_brytlinjer=bas,
        )
        self.assertEqual(len(res.alla_brytlinjer), 0)


def _langd(p: BrytlinjePost) -> float:
    from shapely.geometry import LineString

    return float(LineString(p.koordinater).length)


class TestK9ToK3(unittest.TestCase):
    def test_lapp_utan_nya_falt_har_oforandrad_kontrollsumma(self) -> None:
        a = scenario_till_lapp(Planscenario("s", ("r",), ())).lapp.till_dict()
        for k in ("brytlinjer_nya", "brytlinjer_bort", "oppningar_nya"):
            self.assertNotIn(k, a)

    def test_rundtur_via_json(self) -> None:
        sc = Planscenario(
            "s",
            ("r",),
            (
                Andring("brytlinje_ny", "m", linje((0, 0), (5, 0)), {"typ": "mur", "hojd_m": 1.0}),
                Andring("brytlinje_bort", "b", linje((0, 1), (5, 1)), {}),
                Andring(
                    "oppning_ny", "o", linje((2, -1), (2, 1)),
                    {"typ": "port", "bredd_m": 1.5, "botten_rh2000": 3.0},
                ),
            ),
        )
        lapp = scenario_till_lapp(sc).lapp
        igen = ProjektLapp.fran_dict(lapp.till_dict())
        self.assertEqual(igen, lapp)
        self.assertEqual(igen.lapp_sha256(), lapp.lapp_sha256())
        self.assertTrue(igen.har_vektorandringar)

    def test_fel_ar_tydliga(self) -> None:
        for andring, text in (
            (Andring("brytlinje_ny", "m", linje((0, 0), (5, 0)), {"typ": "mur"}), "hojd_m"),
            (Andring("brytlinje_ny", "m", linje((0, 0), (5, 0)), {"typ": "mur", "hojd_m": 0}), "större än 0"),
            (Andring("brytlinje_ny", "m", linje((0, 0), (5, 0)), {"typ": "vägg", "hojd_m": 1}), "brytlinjetyp"),
            (Andring("brytlinje_bort", "b", linje((0, 0), (5, 0)), {"tolerans_m": 9}), "tolerans_m"),
            (Andring("oppning_ny", "o", linje((0, 0), (5, 0)), {"typ": "port", "bredd_m": 1}), "botten_rh2000"),
            (Andring("oppning_ny", "o", linje((0, 0), (5, 0)), {"typ": "tunnel", "bredd_m": 1, "botten_rh2000": 1}), "öppningstyp"),
            (Andring("oppning_ny", "o", {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}, {"typ": "port", "bredd_m": 1, "botten_rh2000": 1}), "LineString"),
        ):
            with self.assertRaisesRegex((ScenarioFel, LappFel), text):
                scenario_till_lapp(Planscenario("s", ("r",), (andring,)))


class TestMaterialisering(unittest.TestCase):
    """Samma K9-fil genom `materialisera_scenario`: K1-filerna får muren och öppningen (v1-ruta, A1)."""

    def test_k1_filer_far_mur_och_oppning(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            bas = tmp / "bas"
            shutil.copytree(A1, bas)
            self.assertEqual(k1_version(bas), 1)
            g = las_k1(k1_filer(bas)).geo
            xy = lambda k, r: (g.x_min + k * 0.25, g.y_max - r * 0.25)  # noqa: E731
            ring = [xy(10, 10), xy(50, 10), xy(50, 50), xy(10, 50), xy(10, 10)]
            sc = Planscenario(
                "s",
                (sha256_fil(bas / "recept.json"),),
                (
                    Andring("brytlinje_ny", "mur", linje(*ring), {"typ": "mur", "hojd_m": 2.0}),
                    Andring(
                        "oppning_ny", "port", linje(xy(30, 6), xy(30, 14)),
                        {"typ": "passage", "bredd_m": 2.0, "botten_rh2000": 10.0},
                    ),
                ),
            )
            f = skriv_scenario(sc, tmp / "s.json")
            res = materialisera_scenario(
                ScenarioSpec(id="s", bas=bas, k9=(f,)), ResultatCache(tmp / "cache")
            )
            ny = res.rutor[0].mapp
            self.assertNotEqual(ny, bas)
            self.assertEqual(len(las_brytlinjer_fran_gpkg(bas / "brytlinjer.gpkg")), 0)
            bryt = las_brytlinjer_fran_gpkg(ny / "brytlinjer.gpkg")
            opp = las_oppningar_fran_gpkg(ny / "oppningar.gpkg")
            self.assertEqual(len(bryt), 2)  # muren klippt av öppningen
            self.assertEqual([(o.typ, o.bredd_m) for o in opp], [("passage", 2.0)])
            # Vattnet läser samma K1-filer och får samma svar som den direkta tillämpningen.
            k1 = las_k1(k1_filer(ny))
            lagrat = float(blaytor_vid_mm(bygg_graf(k1), REGN_MM).lagrat_m3)
            k1b = las_k1(k1_filer(bas))
            direkt = tillampa_scenario(
                bygg_graf(k1b), k1b, sc, bas_recept_sha256=sha256_fil(bas / "recept.json")
            )
            self.assertAlmostEqual(lagrat, float(blaytor_vid_mm(direkt.graf, REGN_MM).lagrat_m3), places=3)
            self.assertLess(lagrat, float(blaytor_vid_mm(bygg_graf(k1b), REGN_MM).lagrat_m3))


if __name__ == "__main__":
    unittest.main()
