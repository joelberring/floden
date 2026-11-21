"""Enhetstester för `markmodell.dwg` (dwgread-JSON) och `markmodell.lagerprofil` (TOML-profiler).

Fallen för strömläsning, modellrymd och bågar följer O-Tids
`packages/map-revision/test/geo-dwg.test.ts` (MIT, © 2026 Joel Berring). Riktig dwgread körs inte;
JSON-utdata är syntetisk.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from markmodell.dwg import (
    DwgJsonStrom,
    bulge_segment,
    dwg_objekt,
    hitta_dwgread,
    las_baskarta_dwg,
    las_dwg_json,
)
from markmodell.grundkarta import (
    STANDARD_STOCKHOLM_LAGERMAPPNING,
    GrundkartaFel,
    _bygg_lagerregler,
)
from markmodell.lagerprofil import (
    STANDARD_LAGERPROFIL_SOKVAG,
    las_lagerprofil,
    tolka_lagerprofil,
)


def _bitar(text: str, storlek: int) -> Iterator[str]:
    for i in range(0, len(text), storlek):
        yield text[i : i + storlek]


def _json(objekt: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"created_by": 'test "}{[', "HEADER": {"x": "]"}, "OBJECTS": objekt, "THUMBNAILIMAGE": {}},
        indent=2,
    )


MODELL = {"object": "BLOCK_HEADER", "handle": [0, 1, 31], "name": "*Model_Space"}
BLOCK = {"object": "BLOCK_HEADER", "handle": [0, 1, 90], "name": "Trad"}


def _lager(handtag: int, namn: str) -> dict[str, Any]:
    return {"object": "LAYER", "handle": [0, 1, handtag], "name": namn}


def _signerad_area(ring: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:], strict=False))


class TestDwgJsonStrom(unittest.TestCase):
    def test_objekt_ett_i_taget_oavsett_delning_med_klamrar_och_citat_i_strangar(self) -> None:
        objekt = [
            {"object": "LAYER", "handle": [0, 1, 5], "name": 'a{b}"c"\\'},
            {"entity": "LINE", "handle": [0, 1, 6], "note": "]"},
        ]
        text = json.dumps({"OBJECTS": objekt})
        for storlek in (1, 3, 64, 100_000):
            strom = DwgJsonStrom(_bitar(text, storlek))
            self.assertEqual(list(strom), objekt, storlek)
            self.assertEqual(strom.antal_trasiga, 0)

    def test_trasigt_objekt_raknas(self) -> None:
        text = '{"OBJECTS": [{"a": 1}, {"b": tru}, {"c": 3}]}'
        strom = DwgJsonStrom(_bitar(text, 5))
        self.assertEqual(list(strom), [{"a": 1}, {"c": 3}])
        self.assertEqual(strom.antal_trasiga, 1)

    def test_saknad_objektlista_ger_fel(self) -> None:
        with self.assertRaises(GrundkartaFel):
            las_dwg_json(_bitar('{"HEADER": {}}', 4))


class TestDwgObjekt(unittest.TestCase):
    def test_modellrymden_med_lagernamn_men_inte_block_eller_text(self) -> None:
        objekt = [
            MODELL,
            BLOCK,
            _lager(40, "VAG"),
            _lager(41, "HUS"),
            _lager(42, "TRAD"),
            {
                "entity": "LINE",
                "handle": [0, 1, 50],
                "entmode": 2,
                "layer": [5, 1, 40, 40],
                "start": [10, 20, 0],
                "end": [30, 20, 0],
            },
            {
                "entity": "LWPOLYLINE",
                "handle": [0, 1, 51],
                "entmode": 2,
                "layer": [5, 1, 40, 40],
                "flag": 512,
                "points": [[0, 0], [4, 0], [4, 4]],
                "bulges": [],
            },
            {
                "entity": "HATCH",
                "handle": [0, 1, 52],
                "entmode": 2,
                "layer": [5, 1, 41, 41],
                "paths": [
                    {
                        "flag": 1,
                        "polyline_paths": [
                            {"point": [0, 0], "bulge": 0},
                            {"point": [10, 0], "bulge": 0},
                            {"point": [10, 10], "bulge": 0},
                            {"point": [0, 10], "bulge": 0},
                        ],
                    },
                    {
                        "flag": 0,
                        "segs": [
                            {"curve_type": 1, "first_endpoint": [2, 2], "second_endpoint": [4, 2]},
                            {"curve_type": 1, "first_endpoint": [4, 2], "second_endpoint": [4, 4]},
                            {"curve_type": 1, "first_endpoint": [4, 4], "second_endpoint": [2, 2]},
                        ],
                    },
                ],
            },
            {
                "entity": "INSERT",
                "handle": [0, 1, 53],
                "entmode": 2,
                "layer": [5, 1, 42, 42],
                "ins_pt": [7, 8, 0],
                "block_header": [5, 1, 90, 90],
            },
            {
                "entity": "POLYLINE_2D",
                "handle": [0, 1, 54],
                "entmode": 0,
                "ownerhandle": [4, 1, 31, 31],
                "layer": [5, 1, 40, 40],
                "flag": 1,
            },
            {
                "entity": "VERTEX_2D",
                "handle": [0, 1, 55],
                "entmode": 0,
                "ownerhandle": [4, 1, 54, 54],
                "point": [0, 0, 0],
                "bulge": 0,
            },
            {
                "entity": "VERTEX_2D",
                "handle": [0, 1, 56],
                "entmode": 0,
                "ownerhandle": [4, 1, 54, 54],
                "point": [5, 0, 0],
                "bulge": 0,
            },
            {
                "entity": "VERTEX_2D",
                "handle": [0, 1, 57],
                "entmode": 0,
                "ownerhandle": [4, 1, 54, 54],
                "point": [5, 5, 0],
                "bulge": 0,
            },
            # Ett blocks innehåll, pappersrymden (entmode 1) och en text hoppas över.
            {
                "entity": "LINE",
                "handle": [0, 1, 58],
                "entmode": 0,
                "ownerhandle": [4, 1, 90, 90],
                "layer": [5, 1, 40, 40],
                "start": [0, 0, 0],
                "end": [1, 1, 0],
            },
            {
                "entity": "LINE",
                "handle": [0, 1, 61],
                "entmode": 1,
                "layer": [5, 1, 40, 40],
                "start": [0, 0, 0],
                "end": [2, 2, 0],
            },
            {"entity": "TEXT", "handle": [0, 1, 59], "entmode": 2, "layer": [5, 1, 40, 40]},
            {
                "entity": "CIRCLE",
                "handle": [0, 1, 60],
                "entmode": 2,
                "layer": [5, 1, 99, 99],
                "center": [0, 0, 0],
                "radius": 1,
            },
        ]
        ut, overhoppade, trasiga = las_dwg_json(_bitar(_json(objekt), 7))
        self.assertEqual(
            [(o.lager, o.typ) for o in ut],
            [
                ("VAG", "linje"),
                ("VAG", "linje"),
                ("HUS", "yta"),
                ("TRAD", "punkt"),
                ("VAG", "linje"),
                ("0", "yta"),
            ],
        )
        self.assertEqual(ut[1].punkter, ((0, 0), (4, 0), (4, 4), (0, 0)))
        self.assertEqual([len(r) for r in ut[2].ringar], [5, 4])
        self.assertEqual((ut[3].block, ut[3].punkter), ("Trad", ((7.0, 8.0),)))
        self.assertEqual(ut[4].punkter, ((0, 0), (5, 0), (5, 5), (0, 0)))
        self.assertEqual(overhoppade, 3)  # blockets linje, pappersrymdens linje, texten
        self.assertEqual(trasiga, 0)

    def test_hojd_behalls_nar_ritningen_har_z(self) -> None:
        objekt = [
            MODELL,
            _lager(40, "KANTSTEN"),
            {
                "entity": "LINE",
                "handle": [0, 1, 50],
                "entmode": 2,
                "layer": [5, 1, 40, 40],
                "start": [0, 0, 12.0],
                "end": [10, 0, 12.4],
            },
        ]
        ut, _ = dwg_objekt(DwgJsonStrom(_bitar(_json(objekt), 11)))
        self.assertIsNotNone(ut[0].z)
        self.assertAlmostEqual(ut[0].z or 0.0, 12.2)


class TestBagar(unittest.TestCase):
    def test_halvcirkel_moturs_och_medurs(self) -> None:
        vanster, hoger = bulge_segment((0, 0), (2, 0), 1), bulge_segment((0, 0), (2, 0), -1)
        self.assertEqual(vanster[-1], (2, 0))
        # Moturs från (0,0) till (2,0) går bågen under linjen, medurs över.
        self.assertAlmostEqual(min(p[1] for p in vanster), -1.0, places=1)
        self.assertAlmostEqual(max(p[1] for p in hoger), 1.0, places=1)
        self.assertEqual(bulge_segment((0, 0), (2, 0), 0), [(2, 0)])

    def test_bulge_kand_radie(self) -> None:
        # Kvartsbåge: bulge = tan(90°/4), korda 2 → radie = korda / (2 sin 45°) = sqrt(2).
        a, b = (0.0, 0.0), (2.0, 0.0)
        punkter = [a, *bulge_segment(a, b, math.tan(math.pi / 8))]
        centrum = (1.0, 1.0)  # moturs från a till b: centrum ovanför kordan
        for p in punkter:
            self.assertAlmostEqual(
                math.hypot(p[0] - centrum[0], p[1] - centrum[1]), math.sqrt(2), places=9
            )

    def test_skraffyrbage_moturs_och_medurs(self) -> None:
        def skraffyr(is_ccw: int) -> dict[str, Any]:
            # Halvcirkel med radie 1 runt origo, sluten med en rak kant från (-1,0) till (1,0).
            return {
                "entity": "HATCH",
                "handle": [0, 1, 70 + is_ccw],
                "entmode": 2,
                "layer": [5, 1, 41, 41],
                "paths": [
                    {
                        "flag": 1,
                        "segs": [
                            {
                                "curve_type": 2,
                                "center": [0, 0],
                                "radius": 1.0,
                                "start_angle": 0.0,
                                "end_angle": math.pi,
                                "is_ccw": is_ccw,
                            },
                            {"curve_type": 1, "first_endpoint": [-1, 0], "second_endpoint": [1, 0]},
                        ],
                    }
                ],
            }

        ut, _, _ = las_dwg_json(
            _bitar(_json([MODELL, _lager(41, "YTA"), skraffyr(1), skraffyr(0)]), 9)
        )
        moturs, medurs = ut[0].ringar[0], ut[1].ringar[0]
        # Moturs: övre halvcirkeln (area +π/2). Medurs (speglad): nedre halvcirkeln.
        self.assertGreater(min(p[1] for p in moturs), -1e-9)
        self.assertLess(max(p[1] for p in medurs), 1e-9)
        self.assertAlmostEqual(_signerad_area(moturs), math.pi / 2, delta=0.02)
        self.assertAlmostEqual(abs(_signerad_area(medurs)), math.pi / 2, delta=0.02)


class TestLagerprofil(unittest.TestCase):
    def test_toml_profilen_ger_samma_linjeregler_som_dicten(self) -> None:
        profil = las_lagerprofil(STANDARD_LAGERPROFIL_SOKVAG)
        self.assertEqual(profil.som_lagermappning(), STANDARD_STOCKHOLM_LAGERMAPPNING)
        self.assertEqual(profil.linjeregler, _bygg_lagerregler(None))
        self.assertEqual(profil.ytregler, ())  # inga ytregler förrän stadens lagernamn bekräftats
        self.assertEqual(len(profil.sha256), 64)
        self.assertEqual(profil.recept_post()["sha256"], profil.sha256)

    def test_validering(self) -> None:
        bas = 'namn = "x"\nversion = 1\n'
        for felaktig in (
            "version = 1\n",
            'namn = "x"\nversion = 0\n',
            bas + "okand = 1\n",
            bas + '[[linjer]]\nprefix = "A"\nroll = "flygplats"\nstandard_m = 1\n',
            bas + '[[linjer]]\nprefix = "A"\nroll = "mur"\nstandard_m = 1\nfarg = 2\n',
            bas + '[[ytor]]\nprefix = "A"\nmarkslag = 0\n',
            bas + '[[ytor]]\nprefix = "A"\nmarkslag = 1\n[[ytor]]\nprefix = "a"\nmarkslag = 4\n',
            bas
            + '[[linjer]]\nprefix = "A"\nroll = "mur"\nstandard_m = 1\n'
            + '[[ytor]]\nprefix = "A"\nmarkslag = 1\n',
            'namn = "x\n',
        ):
            with self.subTest(felaktig=felaktig), self.assertRaises(GrundkartaFel):
                tolka_lagerprofil(felaktig.encode())

    def test_kontrollsumman_foljer_innehallet(self) -> None:
        a = tolka_lagerprofil(b'namn = "x"\nversion = 1\n')
        b = tolka_lagerprofil(b'namn = "x"\nversion = 2\n')
        self.assertNotEqual(a.sha256, b.sha256)


PROFIL_MED_YTOR = b"""
namn = "test"
version = 1
[[linjer]]
prefix = "KANTSTEN"
roll = "kantsten"
standard_m = 0.12
[[linjer]]
prefix = "HUSLIV"
roll = "byggnad"
standard_m = 10.0
[[ytor]]
prefix = "ASFALT"
markslag = 1
[[ytor]]
prefix = "GRAS"
markslag = 4
"""


class TestBaskartaDwg(unittest.TestCase):
    def _ritning(self) -> list[dict[str, Any]]:
        return [
            MODELL,
            _lager(40, "KANTSTEN_GATA"),
            _lager(41, "HUSLIV"),
            _lager(42, "ASFALT_YTA"),
            _lager(43, "GRAS"),
            _lager(44, "OKAND"),
            {
                "entity": "LWPOLYLINE",
                "handle": [0, 1, 50],
                "entmode": 2,
                "layer": [5, 1, 40, 40],
                "flag": 0,
                "points": [[0, 0], [20, 0], [20, 10]],
                "bulges": [0, 0, 0],
            },
            # Husliv som sluten polylinje och som skraffyr på husets lager.
            {
                "entity": "LWPOLYLINE",
                "handle": [0, 1, 51],
                "entmode": 2,
                "layer": [5, 1, 41, 41],
                "flag": 1,
                "points": [[30, 0], [40, 0], [40, 10], [30, 10]],
                "bulges": [],
            },
            {
                "entity": "HATCH",
                "handle": [0, 1, 52],
                "entmode": 2,
                "layer": [5, 1, 41, 41],
                "paths": [
                    {
                        "flag": 1,
                        "polyline_paths": [
                            {"point": [50, 0]},
                            {"point": [60, 0]},
                            {"point": [60, 10]},
                            {"point": [50, 10]},
                        ],
                    }
                ],
            },
            {
                "entity": "HATCH",
                "handle": [0, 1, 53],
                "entmode": 2,
                "layer": [5, 1, 42, 42],
                "paths": [
                    {
                        "flag": 1,
                        "polyline_paths": [
                            {"point": [0, 20]},
                            {"point": [100, 20]},
                            {"point": [100, 30]},
                            {"point": [0, 30]},
                        ],
                    }
                ],
            },
            {
                "entity": "SOLID",
                "handle": [0, 1, 54],
                "entmode": 2,
                "layer": [5, 1, 43, 43],
                "corner1": [0, 40],
                "corner2": [10, 40],
                "corner3": [0, 50],
                "corner4": [10, 50],
            },
            {
                "entity": "HATCH",
                "handle": [0, 1, 55],
                "entmode": 2,
                "layer": [5, 1, 44, 44],
                "paths": [
                    {
                        "flag": 1,
                        "polyline_paths": [
                            {"point": [0, 60]},
                            {"point": [5, 60]},
                            {"point": [5, 65]},
                        ],
                    }
                ],
            },
            {
                "entity": "POINT",
                "handle": [0, 1, 56],
                "entmode": 2,
                "layer": [5, 1, 44, 44],
                "x": 1.0,
                "y": 2.0,
            },
            {"entity": "TEXT", "handle": [0, 1, 57], "entmode": 2, "layer": [5, 1, 44, 44]},
        ]

    def test_samma_kedja_som_dxf_och_markslagsytor_ur_skraffyrer(self) -> None:
        profil = tolka_lagerprofil(PROFIL_MED_YTOR, kalla="test.toml")
        res = las_baskarta_dwg(_bitar(_json(self._ritning()), 13), lagerprofil=profil)
        bk = res.baskarta
        self.assertEqual([b.typ for b in bk.brytlinjer], ["kantsten"])
        self.assertAlmostEqual(bk.brytlinjer[0].hojd_m, 0.12)
        self.assertEqual(len(bk.byggnader), 2)
        self.assertEqual(
            sorted(round(abs(_signerad_area(b.polygon))) for b in bk.byggnader), [100, 100]
        )
        self.assertEqual(
            [(y.markslag, y.lager) for y in res.markslagsytor], [(1, "ASFALT_YTA"), (4, "GRAS")]
        )
        self.assertAlmostEqual(abs(_signerad_area(res.markslagsytor[0].ringar[0])), 1000.0)
        self.assertEqual(bk.okanda_lager, ("OKAND",))
        self.assertTrue(any("punkter/blockinsättningar" in v for v in bk.varningar))
        self.assertEqual(res.antal_overhoppade, 1)  # texten
        self.assertEqual(res.lagerprofil_sha256, profil.sha256)

    def test_utan_profil_standardmappning_och_inga_ytor(self) -> None:
        res = las_baskarta_dwg(_bitar(_json(self._ritning()), 64))
        self.assertEqual(res.markslagsytor, ())
        self.assertIn("ASFALT_YTA", res.baskarta.okanda_lager)
        self.assertEqual(len(res.baskarta.byggnader), 2)
        self.assertIsNone(res.lagerprofil_sha256)

    def test_json_fil_och_saknad_dwgread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "ritning.json"
            fil.write_text(_json(self._ritning()), encoding="utf-8")
            res = las_baskarta_dwg(fil)
            self.assertEqual(len(res.baskarta.brytlinjer), 1)
            dwg = Path(tmp) / "ritning.dwg"
            dwg.write_bytes(b"AC1032")
            with self.assertRaises(GrundkartaFel):
                las_baskarta_dwg(dwg, dwgread=Path(tmp) / "finns-inte")
        with self.assertRaises(GrundkartaFel):
            hitta_dwgread(Path("/finns/inte/dwgread"))


if __name__ == "__main__":
    unittest.main()
