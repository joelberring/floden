"""Stockholms lagergrammatik: namn ur O-Tids tester klassade efter objekttypen (förväntningarna är
skrivna här efter vad objekttypen betyder, inte körda mot O-Tid), avkortade statussuffix och
hela vägen DWG/DXF → markslagsytor, brytlinjer och källfil."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from markmodell.dwg import las_baskarta_dwg
from markmodell.grundkarta import las_baskarta_dxf
from markmodell.lagerprofil import las_lagerprofil
from markmodell.markslagskallor import FLAGGA_OSAKER_KALLA, KALLA_BASKARTA, KALLA_MASK
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta
from markmodell.stockholm_lager import ar_stockholms_lager, bestam_atgard, tolka_lagernamn

PROFIL = las_lagerprofil()
GRAM = PROFIL.stockholm
assert GRAM is not None

# (namn, objekt, åtgärd, markslag eller roll)
YTOR = [
    ("BostadsbyggnadFlerbostadshusgyBBefintl", "byggnad", None),
    ("BostadsbyggnadBostadsbyggnadgyBBefintl", "byggnad", None),
    ("ByggnadsanlaggningSkarmtakgyBBefintl", "markslag", 2),
    ("ByggnadsanlaggningAltangyBBefintl", "hoppa_over", None),
    ("ByggnadsanlaggningOvriggyBBefintl", "hoppa_over", None),
    ("VagutbredningVagutbredninggyBBefintl", "markslag", 1),
    ("ParkBelaggningAsfaltparkvaggyB", "markslag", 1),
    ("ParkBelaggningAsfaltparkvaggyB0", "markslag", 1),
    ("ParkBelaggningGrusgyB0", "markslag", 7),
    ("ParkGrasytagyB0", "markslag", 4),
    ("ParkNaturmarkTradbevuxengyB0", "markslag", 5),
    ("ParkNaturmarkOppennaturmarkgyB0", "markslag", 4),
    ("ParkPlanteringBuskagegyB0", "markslag", 4),
    ("TeknikanlaggningBassanggyBBefintl", "markslag", 8),
    ("TrafikanlaggningTerrangtrappagyBBefintl", "markslag", 1),
    ("TrafikanlaggningBrogyBBefintl", "hoppa_over", None),
]
LINJER = [
    ("BostadsbyggnadBostadsbyggnadglBBefintl", "hoppa_over", None),
    ("ByggnadsanlaggningTrappaglBBefintl", "hoppa_over", None),
    ("TrafikanlaggningTerrangtrappaglBBefintl", "hoppa_over", None),
    ("TrafikanlaggningTerrangtrappaglBBefin", "hoppa_over", None),
    ("MarkanlaggningBullerskyddglBBefintl", "brytlinje", "mur"),
    ("MarkanlaggningSlantglBBefintl", "brytlinje", "bankkant"),
    ("MarkanlaggningSlantglBBefintlig", "brytlinje", "bankkant"),
    ("MarkanlaggningStaketglBBefintl", "brytlinje", "staket"),
    ("MarkanlaggningStaketglBBefintlig", "brytlinje", "staket"),
    ("MarkanlaggningStenmurglBBefintl", "brytlinje", "mur"),
    ("MarkanlaggningStenmurglBOdefin", "brytlinje", "mur"),
    ("MarkanlaggningStodmurglBBefintl", "brytlinje", "stodmur"),
    ("MarkanlaggningBruksmurglBBefintl", "brytlinje", "mur"),
    ("MarkanlaggningKallmurglBBefintl", "brytlinje", "mur"),
    ("MarkanlaggningGraskantglBBefintl", "brytlinje", "bankkant"),
    ("TrafikanlaggningBroglBBefintl", "hoppa_over", None),
    ("TrafikanlaggningBroglBSkymd", "hoppa_over", None),
    ("VagavgransningInfartglBBefintl", "brytlinje", "kantsten"),
    ("VagavgransningRefugglBBefintl", "brytlinje", "kantsten"),
    ("VagavgransningKantstenglBBefintl", "brytlinje", "kantsten"),
    ("VagutbredningVagutbredningglBBefintl", "hoppa_over", None),
    ("VagutbredningParkvagsutbredningglBBefintl", "hoppa_over", None),
    ("VattendragDikeglBBefintl", "brytlinje", "bankkant"),
    ("VattendragBackglBBefintlig", "brytlinje", "bankkant"),
    ("ElnatHogspanningsledningglBBefint", "hoppa_over", None),
    ("SparnatSJNormalsparglBBefintlig", "hoppa_over", None),
    ("HojdkurvaHojdkurvaglhBBefintlig", "hoppa_over", None),
    ("RegisterenhetFastighetglBBefintlig", "hoppa_over", None),
    ("ParkSnorojningglB0", "hoppa_over", None),
    ("AdressTextBefintligtB0", "hoppa_over", None),
    ("ParkTradsB0", "hoppa_over", None),
    ("VegetationTradsBBefintl", "hoppa_over", None),
]
INTE_GRAMMATIK = [
    "Parkering",
    "Parkeringen",
    "Parkeringshus",
    "Parktr",
    "Parkv",
    "HusB",
    "KANTSTEN",
]


class TestTolkning(unittest.TestCase):
    def test_delar_och_status_inklusive_avkortade(self) -> None:
        t = tolka_lagernamn("MarkanlaggningStenmurglBOdefin")
        assert t is not None
        self.assertEqual(
            (t.karta, t.delar, t.geometri, t.status),
            ("baskarta", ("Markanlaggning", "Stenmur"), "gl", "odefinierad"),
        )
        for namn, status in (
            ("MarkanlaggningStenmurglBBefintl", "befintlig"),
            ("MarkanlaggningStenmurglBBefintlig", "befintlig"),
            ("TrafikanlaggningTerrangtrappaglBBefin", "befintlig"),
            ("ElnatHogspanningsledningglBBefint", "befintlig"),
            ("MarkanlaggningStenmurglBOdefinierad", "odefinierad"),
            ("MarkanlaggningStenmurglBOd", "odefinierad"),
            ("TrafikanlaggningBroglBSkymd", "skymd"),
            ("TrafikanlaggningBroglBSky", "skymd"),
            ("ParkGrasytagyB0", "ingen"),
            ("ParkBelaggningAsfaltparkvaggyB", "ingen"),
            ("MarkanlaggningStenmurglBXyz", "okand"),
        ):
            t = tolka_lagernamn(namn)
            assert t is not None, namn
            self.assertEqual(t.status, status, namn)

    def test_geometrier(self) -> None:
        for namn, geo in (
            ("BostadsbyggnadFlerbostadshusgyBBefintl", "gy"),
            ("VattendragDikeglBBefintl", "gl"),
            ("HojdkurvaHojdkurvaglhBBefintlig", "glh"),
            ("AdressTextBefintligtB0", "t"),
            ("VegetationTradsBBefintl", "s"),
            ("ParkTradsB0", "s"),
        ):
            t = tolka_lagernamn(namn)
            assert t is not None
            self.assertEqual(t.geometri, geo, namn)

    def test_parkkarta_utan_status_och_karta(self) -> None:
        t = tolka_lagernamn("ParkGrasytagyB0")
        assert t is not None
        self.assertEqual((t.karta, t.delar), ("parkkarta", ("Grasyta",)))
        # ett Park-namn utan geometrisuffix följer ändå grammatiken (O-Tids isStockholmLayer)
        self.assertTrue(ar_stockholms_lager("ParkGrasyta"))

    def test_namn_utanfor_grammatiken(self) -> None:
        for namn in INTE_GRAMMATIK:
            self.assertFalse(ar_stockholms_lager(namn), namn)
            self.assertIsNone(bestam_atgard(GRAM, namn, "linje"), namn)


class TestAtgard(unittest.TestCase):
    def test_ytor(self) -> None:
        for namn, atgard, kod in YTOR:
            b = bestam_atgard(GRAM, namn, "yta")
            assert b is not None, namn
            self.assertEqual(b.atgard, atgard, namn)
            if atgard == "markslag":
                self.assertEqual(b.markslag, kod, namn)

    def test_linjer(self) -> None:
        for namn, atgard, roll in LINJER:
            b = bestam_atgard(GRAM, namn, "linje")
            assert b is not None, namn
            self.assertEqual(b.atgard, atgard, namn)
            if roll is not None:
                self.assertEqual(b.roll, roll, namn)

    def test_matt_for_skyfall(self) -> None:
        for namn, roll, hojd in (
            ("MarkanlaggningStodmurglBBefintl", "stodmur", 1.5),
            ("MarkanlaggningStenmurglBBefintl", "mur", 1.0),
            ("MarkanlaggningSlantglBBefintl", "bankkant", 0.30),
            ("VagavgransningInfartglBBefintl", "kantsten", 0.12),
        ):
            b = bestam_atgard(GRAM, namn, "linje")
            assert b is not None
            self.assertEqual((b.roll, b.standard_m), (roll, hojd), namn)

    def test_odefinierad_flaggas_skymd_hoppas_over(self) -> None:
        sak = bestam_atgard(GRAM, "MarkanlaggningStenmurglBBefintl", "linje")
        osak = bestam_atgard(GRAM, "MarkanlaggningStenmurglBOdefin", "linje")
        skymd = bestam_atgard(GRAM, "MarkanlaggningStenmurglBSkymd", "linje")
        assert sak and osak and skymd
        self.assertFalse(sak.osaker)
        self.assertTrue(osak.osaker)
        self.assertEqual(skymd.atgard, "hoppa_over")

    def test_slant_och_graskant_ar_inte_overhoppade_som_i_otid(self) -> None:
        self.assertEqual(
            bestam_atgard(GRAM, "MarkanlaggningSlantglBBefintl", "linje").atgard,  # type: ignore[union-attr]
            "brytlinje",
        )

    def test_byggnadens_kontur_pa_ytlager_ar_husliv(self) -> None:
        b = bestam_atgard(GRAM, "BostadsbyggnadFlerbostadshusgyBBefintl", "linje")
        assert b is not None
        self.assertEqual((b.atgard, b.roll), ("byggnad_kontur", "byggnad"))

    def test_tolkaren_laser_regler_ur_profilen(self) -> None:
        self.assertEqual(PROFIL.version, 2)
        text = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "lagerprofiler"
            / "stockholm_baskarta.toml"
        ).read_text(encoding="utf-8")
        self.assertIn("stockholm-layers.ts @ 4a446af", text)
        self.assertIn("ska bekräftas mot full lagerlista", text)


def _hatch(
    handtag: int, lager: int, e0: float, n0: float, e1: float, n1: float
) -> dict[str, object]:
    return {
        "entity": "HATCH",
        "handle": [0, 1, handtag],
        "entmode": 2,
        "layer": [5, 1, lager, lager],
        "paths": [
            {
                "flag": 1,
                "polyline_paths": [
                    {"point": [e0, n0], "bulge": 0},
                    {"point": [e1, n0], "bulge": 0},
                    {"point": [e1, n1], "bulge": 0},
                    {"point": [e0, n1], "bulge": 0},
                ],
            }
        ],
    }


def _linje(handtag: int, lager: int, pts: list[list[float]]) -> dict[str, object]:
    return {
        "entity": "LWPOLYLINE",
        "handle": [0, 1, handtag],
        "entmode": 2,
        "layer": [5, 1, lager, lager],
        "flag": 0,
        "points": pts,
        "bulges": [0] * len(pts),
    }


def _lager(handtag: int, namn: str) -> dict[str, object]:
    return {"object": "LAYER", "handle": [0, 1, handtag], "name": namn}


E0, N0 = 150000.0, 6576000.0


class TestHelaKedjan(unittest.TestCase):
    def test_dwg_ytor_linjer_och_osakerhet_genom_pipelinen(self) -> None:
        objekt = [
            {"object": "BLOCK_HEADER", "handle": [0, 1, 31], "name": "*Model_Space"},
            _lager(41, "ParkGrasytagyB0"),
            _lager(42, "VagutbredningVagutbredninggyBBefintl"),
            _lager(43, "TeknikanlaggningBassanggyBOdefin"),
            _lager(44, "BostadsbyggnadFlerbostadshusgyBBefintl"),
            _lager(45, "MarkanlaggningStenmurglBBefintl"),
            _lager(46, "VattendragDikeglBOdefin"),
            _lager(47, "TrafikanlaggningBroglBSkymd"),
            _lager(48, "HojdkurvaHojdkurvaglhBBefintlig"),
            _hatch(60, 41, E0, N0, E0 + 10, N0 + 20),  # gräs, väster
            _hatch(61, 42, E0 + 10, N0, E0 + 20, N0 + 10),  # väg, sydöst
            _hatch(62, 43, E0 + 10, N0 + 10, E0 + 20, N0 + 20),  # bassäng, odefinierad, nordöst
            _hatch(63, 44, E0 + 2, N0 + 2, E0 + 6, N0 + 6),  # hus på gräset
            _linje(64, 45, [[E0 + 1, N0 + 15], [E0 + 9, N0 + 15]]),
            _linje(65, 46, [[E0 + 11, N0 + 5], [E0 + 19, N0 + 5]]),
            _linje(66, 47, [[E0, N0 + 18], [E0 + 5, N0 + 18]]),
            _linje(67, 48, [[E0, N0 + 19], [E0 + 5, N0 + 19]]),
        ]
        res = las_baskarta_dwg(iter([json.dumps({"OBJECTS": objekt})]), lagerprofil=PROFIL)
        self.assertEqual(sorted(y.markslag for y in res.markslagsytor), [1, 4, 8])
        self.assertEqual(
            [y.osaker for y in sorted(res.markslagsytor, key=lambda y: y.markslag)],
            [False, False, True],
        )
        self.assertEqual([b.typ for b in res.baskarta.brytlinjer], ["mur", "bankkant"])
        self.assertEqual(len(res.baskarta.byggnader), 1)
        self.assertTrue(any("Hoppade över" in v for v in res.baskarta.varningar))
        self.assertTrue(any("Odefinierad" in v for v in res.baskarta.varningar))

        ruta = Ruta(e_min_m=E0, n_min_m=N0, storlek_m=20.0, overlapp_m=0.0, upplosning_m=1.0)
        grund = np.full(ruta.form_rc, 10.0, np.float32)
        with tempfile.TemporaryDirectory() as t:
            r = bygg_markmodell_ruta(
                ruta, t, grund_z=grund, markslagsytor=res.markslagsytor,
                brytlinjer=res.baskarta.brytlinjer, byggnader=res.baskarta.byggnader,
                lagerprofil=PROFIL, standard_markslag=0, kodversion="k", start_tid="t",
            )  # fmt: skip
            run = json.loads((Path(t) / ruta.namn / "run.json").read_text(encoding="utf-8"))
        # rad 0 = norr: n = N0+19.5 är rad 0; n = N0+0.5 är rad 19
        self.assertEqual(int(r.markslag[19 - 15, 3]), 4)  # gräs (väster)
        self.assertEqual(int(r.markslag[19 - 5, 15]), 1)  # väg
        self.assertEqual(int(r.markslag[19 - 15, 15]), 8)  # bassäng
        self.assertEqual(int(r.markslag[19 - 3, 3]), 9)  # huset över gräset
        self.assertEqual(run["matvarden"]["markslag_osakra_kallor"], 100)  # 10 x 10 bassängceller
        self.assertTrue(any(k.roll == "lagerprofil" and k.sha256 == PROFIL.sha256
                            for k in r.recept.kallor))  # fmt: skip

    def test_osaker_flagga_i_kallfilen(self) -> None:
        from markmodell.markslagskallor import kombinera_markslag, rasterisera_ytor

        ruta = Ruta(e_min_m=E0, n_min_m=N0, storlek_m=20.0, overlapp_m=0.0, upplosning_m=1.0)
        objekt = [
            {"object": "BLOCK_HEADER", "handle": [0, 1, 31], "name": "*Model_Space"},
            _lager(43, "TeknikanlaggningBassanggyBOdefin"),
            _hatch(62, 43, E0 + 10, N0 + 10, E0 + 20, N0 + 20),
        ]
        res = las_baskarta_dwg(iter([json.dumps({"OBJECTS": objekt})]), lagerprofil=PROFIL)
        kod, konflikt, osaker = rasterisera_ytor(res.markslagsytor, ruta)
        k = kombinera_markslag(ruta.form_rc, baskarta_kod=kod, baskarta_konflikt=konflikt,
                               baskarta_osaker=osaker)  # fmt: skip
        self.assertEqual(int(k.kalla[2, 15]), KALLA_BASKARTA | FLAGGA_OSAKER_KALLA)
        self.assertEqual(int(k.kalla[15, 3] & KALLA_MASK), 0)
        self.assertEqual(k.antal_osakra(), 100)

    def test_dxf_med_grammatik_ger_brytlinjer_och_hoppar_over_resten(self) -> None:
        def line(lager: str, x0: float, y0: float, x1: float, y1: float) -> str:
            return f"0\nLINE\n8\n{lager}\n10\n{x0}\n20\n{y0}\n30\n0\n11\n{x1}\n21\n{y1}\n31\n0\n"

        dxf = (
            "0\nSECTION\n2\nENTITIES\n"
            + line("MarkanlaggningStenmurglBBefintl", 0, 0, 10, 0)
            + line("VagavgransningInfartglBBefintl", 0, 5, 10, 5)
            + line("HojdkurvaHojdkurvaglhBBefintlig", 0, 8, 10, 8)
            + line("TrafikanlaggningBroglBSkymd", 0, 9, 10, 9)
            + line("KANTSTEN", 0, 12, 10, 12)
            + "0\nENDSEC\n0\nEOF\n"
        )
        res = las_baskarta_dxf(dxf, ar_text=True, lagerprofil=PROFIL)
        self.assertEqual(sorted(b.typ for b in res.brytlinjer), ["kantsten", "kantsten", "mur"])
        self.assertEqual(res.okanda_lager, ())
        # utan profil är Stockholms lager okända
        utan = las_baskarta_dxf(dxf, ar_text=True)
        self.assertEqual(len(utan.okanda_lager), 4)


if __name__ == "__main__":
    unittest.main()
