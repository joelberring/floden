"""Markslag ur flera källor: prioritetsordning, källfil, osäkerhet och recept (K1 v2-tillägg)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from markmodell.dwg import MarkslagsYta, las_baskarta_dwg
from markmodell.hinder_oppningar import (
    BYGGNAD,
    GRAS,
    HARDGJORT,
    OKANT,
    SKOG,
    VATTEN,
    Byggnad,
)
from markmodell.lagerprofil import (
    hitta_lagerprofil,
    las_lagerprofil,
    tolka_lagerprofil,
)
from markmodell.markslagskallor import (
    FLAGGA_MOTSAGELSE,
    FLAGGA_SVAG,
    KALLA_BASKARTA,
    KALLA_BELAGD_LASER,
    KALLA_MASK,
    KALLA_ORTOFOTO,
    KALLA_STANDARD,
    kombinera_markslag,
    rasterisera_ytor,
)
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.recept import las_recept
from markmodell.rutnat import Ruta

RUTA = Ruta(e_min_m=150000.0, n_min_m=6576000.0, storlek_m=20.0, overlapp_m=0.0, upplosning_m=1.0)


def _kvadrat(e0: float, n0: float, e1: float, n1: float) -> tuple[tuple[float, float], ...]:
    return ((e0, n0), (e1, n0), (e1, n1), (e0, n1), (e0, n0))


class TestPrioritet(unittest.TestCase):
    def test_syntetisk_ruta_med_fyra_kallor(self) -> None:
        """Fyra kolumnblock: baskarta/laser/ortofoto/standard, och alla tre oense i första."""
        f = (4, 16)
        bas = np.zeros(f, np.uint8)
        bel = np.zeros(f, bool)
        ort = np.zeros(f, np.uint8)
        konf = np.full(f, 0.9, np.float32)
        # block 0 (kol 0-3): baskarta=gräs, belagd=ja, ortofoto=skog -> baskarta vinner, flaggad
        bas[:, 0:4] = GRAS
        bel[:, 0:4] = True
        ort[:, 0:4] = SKOG
        # block 1 (kol 4-7): belagd=ja, ortofoto=gräs -> belagd vinner, flaggad
        bel[:, 4:8] = True
        ort[:, 4:8] = GRAS
        # block 2 (kol 8-11): bara ortofoto=skog, svag konfidens på en rad
        ort[:, 8:12] = SKOG
        konf[0, 8:12] = 0.2
        # block 3 (kol 12-15): inget -> standard
        r = kombinera_markslag(
            f, baskarta_kod=bas, belagd_mask=bel, ortofoto=ort, ortofoto_konfidens=konf
        )
        self.assertTrue(np.all(r.markslag[:, 0:4] == GRAS))
        self.assertTrue(np.all(r.markslag[:, 4:8] == HARDGJORT))
        self.assertTrue(np.all(r.markslag[:, 8:12] == SKOG))
        self.assertTrue(np.all(r.markslag[:, 12:16] == HARDGJORT))
        k = r.kalla
        self.assertTrue(np.all((k[:, 0:4] & KALLA_MASK) == KALLA_BASKARTA))
        self.assertTrue(np.all((k[:, 0:4] & FLAGGA_MOTSAGELSE) != 0))
        self.assertTrue(np.all((k[:, 4:8] & KALLA_MASK) == KALLA_BELAGD_LASER))
        self.assertTrue(np.all((k[:, 4:8] & FLAGGA_MOTSAGELSE) != 0))
        self.assertTrue(np.all((k[:, 8:12] & KALLA_MASK) == KALLA_ORTOFOTO))
        self.assertTrue(np.all((k[:, 8:12] & FLAGGA_MOTSAGELSE) == 0))
        self.assertTrue(np.all((k[1:, 8:12] & FLAGGA_SVAG) == 0))
        self.assertTrue(np.all((k[0, 8:12] & FLAGGA_SVAG) != 0))
        self.assertTrue(np.all(k[:, 12:16] == KALLA_STANDARD))
        self.assertEqual(r.antal_motsagelser(), 32)
        self.assertEqual(r.antal_svaga(), 4)

    def test_enighet_ger_ingen_flagga_och_okant_ar_ingen_uppgift(self) -> None:
        f = (2, 4)
        bas = np.zeros(f, np.uint8)
        bas[:, 0] = HARDGJORT
        bel = np.zeros(f, bool)
        bel[:, 0:2] = True
        ort = np.full(f, OKANT, np.uint8)
        ort[:, 0] = HARDGJORT
        r = kombinera_markslag(f, baskarta_kod=bas, belagd_mask=bel, ortofoto=ort)
        self.assertTrue(np.all(r.kalla[:, 0] == KALLA_BASKARTA))  # alla säger 1
        self.assertTrue(np.all(r.kalla[:, 1] == KALLA_BELAGD_LASER))  # ortofoto OKANT
        self.assertTrue(np.all(r.kalla[:, 2:] == KALLA_STANDARD))

    def test_standardklass_kan_andras(self) -> None:
        r = kombinera_markslag((2, 2), standard=GRAS)
        self.assertTrue(np.all(r.markslag == GRAS))

    def test_ytor_rasteriseras_med_prioritet_och_konflikt(self) -> None:
        a = MarkslagsYta(GRAS, "A", (_kvadrat(150000, 6576000, 150010, 6576010),))
        b = MarkslagsYta(VATTEN, "B", (_kvadrat(150005, 6576000, 150015, 6576010),))
        kod, konflikt, _ = rasterisera_ytor([a, b], RUTA)
        # RUTA: rad 0 = norr; n 6576000-6576010 är nedre halvan (rad 10-19), kol 0-9 resp 5-14
        self.assertEqual(int(kod[15, 2]), GRAS)
        self.assertEqual(int(kod[15, 12]), VATTEN)
        self.assertEqual(int(kod[15, 7]), VATTEN)  # vatten före gräs
        self.assertTrue(bool(konflikt[15, 7]))
        self.assertFalse(bool(konflikt[15, 2]))
        self.assertEqual(int(kod[2, 2]), 0)

    def test_yta_med_hal(self) -> None:
        ring = (
            _kvadrat(150000, 6576000, 150020, 6576020),
            _kvadrat(150005, 6576005, 150015, 6576015),
        )
        kod, _, _ = rasterisera_ytor([MarkslagsYta(GRAS, "A", ring)], RUTA)
        self.assertEqual(int(kod[10, 10]), 0)
        self.assertEqual(int(kod[1, 1]), GRAS)


class TestPipelineMarkslag(unittest.TestCase):
    def _bygg(self, ut: str, **kw: object) -> object:
        grund = np.full(RUTA.form_rc, 10.0, np.float32)
        return bygg_markmodell_ruta(RUTA, ut, grund_z=grund, kodversion="test", start_tid="t", **kw)  # type: ignore[arg-type]

    def test_pipeline_baskarta_laser_ortofoto_och_byggnad(self) -> None:
        f = RUTA.form_rc
        yta = MarkslagsYta(GRAS, "G", (_kvadrat(150000, 6576000, 150010, 6576020),))
        bel = np.zeros(f, bool)
        bel[:, 0:10] = True  # täcker gräsytan (baskarta ska vinna) ...
        bel[:, 10:15] = True  # ... och ytan utan baskarta (laser ska vinna över ortofoto)
        ort = np.full(f, SKOG, np.uint8)
        bygg = Byggnad(polygon=_kvadrat(150016, 6576004, 150019, 6576008)[:-1], hinderhojd_m=8.0)
        with tempfile.TemporaryDirectory() as t:
            res = self._bygg(
                t,
                markslagsytor=[yta],
                belagd_yta_mask=bel,
                ortofoto_markslag=ort,
                byggnader=[bygg],
            )
            slag = res.markslag  # type: ignore[attr-defined]
            self.assertTrue((Path(t) / RUTA.namn / "markslag_kalla.tif").is_file())
            self.assertEqual(int(slag[10, 3]), GRAS)  # baskarta över laser och ortofoto
            self.assertEqual(int(slag[10, 12]), HARDGJORT)  # laser över ortofoto
            self.assertEqual(int(slag[10, 18]), SKOG)  # bara ortofoto
            self.assertEqual(int(slag[14, 17]), BYGGNAD)  # byggnad (baskarta) över ortofoto
            run = json.loads((Path(t) / RUTA.namn / "run.json").read_text(encoding="utf-8"))
            self.assertIn("markslag_kalla", [u["roll"] for u in run["utdata"]])
            mv = run["matvarden"]
            self.assertEqual(mv["markslag_celler_belagd_laser"], 5 * 20)
            self.assertGreater(mv["markslag_motsagelser"], 0)

    def test_kalla_tif_innehaller_ratt_kalla_och_flaggor(self) -> None:
        from markmodell.k1_skrivare import skriv_geotiff  # noqa: F401
        from testsvit.k1_io import las_geotiff

        yta = MarkslagsYta(GRAS, "G", (_kvadrat(150000, 6576000, 150010, 6576020),))
        ort = np.full(RUTA.form_rc, SKOG, np.uint8)
        with tempfile.TemporaryDirectory() as t:
            self._bygg(t, markslagsytor=[yta], ortofoto_markslag=ort)
            k, _ = las_geotiff(Path(t) / RUTA.namn / "markslag_kalla.tif")
        k = np.asarray(k)
        self.assertEqual(int(k[10, 3]), KALLA_BASKARTA | FLAGGA_MOTSAGELSE)
        self.assertEqual(int(k[10, 15]), KALLA_ORTOFOTO)

    def test_utan_nya_kallor_ingen_kallfil_och_samma_recept(self) -> None:
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            a = self._bygg(t1)
            b = self._bygg(t2)
            self.assertFalse((Path(t1) / RUTA.namn / "markslag_kalla.tif").exists())
            self.assertEqual(a.recept_sha256, b.recept_sha256)  # type: ignore[attr-defined]
            self.assertNotIn("markslag_prioritet", a.recept.installningar)  # type: ignore[attr-defined]
            self.assertTrue(np.all(a.markslag == HARDGJORT))  # type: ignore[attr-defined]

    def test_markslag_grund_och_nya_kallor_kan_inte_kombineras(self) -> None:
        from markmodell.pipeline import PipelineFel

        with tempfile.TemporaryDirectory() as t, self.assertRaises(PipelineFel):
            self._bygg(
                t,
                markslag_grund=np.zeros(RUTA.form_rc, np.uint8),
                belagd_yta_mask=np.zeros(RUTA.form_rc, bool),
            )


PROFIL_A = b"""namn = "p"
version = 1
[[ytor]]
prefix = "ASFALT"
markslag = 1
"""
PROFIL_B = PROFIL_A.replace(b"markslag = 1", b"markslag = 7")


class TestLagerprofilIRecept(unittest.TestCase):
    def test_recept_far_profilens_sha256_och_andras_med_profilen(self) -> None:
        grund = np.full(RUTA.form_rc, 10.0, np.float32)
        pa, pb = (
            tolka_lagerprofil(PROFIL_A, kalla="a.toml"),
            tolka_lagerprofil(PROFIL_B, kalla="a.toml"),
        )
        self.assertNotEqual(pa.sha256, pb.sha256)
        with tempfile.TemporaryDirectory() as t:
            ra = bygg_markmodell_ruta(
                RUTA, t, grund_z=grund, lagerprofil=pa, kodversion="k", start_tid="t"
            )
            rb = bygg_markmodell_ruta(
                RUTA, t, grund_z=grund, lagerprofil=pb, kodversion="k", start_tid="t"
            )
            ingen = bygg_markmodell_ruta(RUTA, t, grund_z=grund, kodversion="k", start_tid="t")
            lasta = las_recept(Path(t) / RUTA.namn / "recept.json")
        self.assertEqual(
            [k.sha256 for k in ra.recept.kallor if k.roll == "lagerprofil"], [pa.sha256]
        )
        self.assertNotEqual(ra.recept_sha256, rb.recept_sha256)
        self.assertNotEqual(ra.recept_sha256, ingen.recept_sha256)
        self.assertFalse([k for k in lasta.kallor if k.roll == "lagerprofil"])  # senaste = ingen

    def test_lokal_profil_via_konfig_och_exempelmallen(self) -> None:
        mall = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "lagerprofiler"
            / "lokal_profil.toml.example"
        )
        profil = las_lagerprofil(mall)
        self.assertEqual({r.markslag for r in profil.ytregler}, {1, 4, 8})
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "riktig.toml").write_bytes(PROFIL_A)
            via = hitta_lagerprofil(
                None,
                {"markmodell": {"lagerprofil": "riktig.toml"}, "lokal_data": {"baskarta_mapp": t}},
            )
            self.assertEqual(via.namn, "p")
            explicit = hitta_lagerprofil(mall, {"markmodell": {"lagerprofil": "riktig.toml"}})
            self.assertEqual(explicit.namn, "stockholm_baskarta_lokal")
        self.assertEqual(hitta_lagerprofil(None, {}).namn, "stockholm_baskarta")

    def test_syntetisk_dwg_yta_ger_markslag_i_bygget_med_lokal_profil(self) -> None:
        obj = [
            {"object": "BLOCK_HEADER", "handle": [0, 1, 31], "name": "*Model_Space"},
            {"object": "LAYER", "handle": [0, 1, 41], "name": "ASFALT_1"},
            {
                "entity": "HATCH",
                "handle": [0, 1, 52],
                "entmode": 2,
                "layer": [5, 1, 41, 41],
                "paths": [
                    {
                        "flag": 1,
                        "polyline_paths": [
                            {"point": [150000, 6576000], "bulge": 0},
                            {"point": [150010, 6576000], "bulge": 0},
                            {"point": [150010, 6576020], "bulge": 0},
                            {"point": [150000, 6576020], "bulge": 0},
                        ],
                    }
                ],
            },
        ]
        dwg = json.dumps({"OBJECTS": obj})
        grund = np.full(RUTA.form_rc, 10.0, np.float32)
        resultat = {}
        for namn, data in (("a", PROFIL_A), ("b", PROFIL_B)):
            profil = tolka_lagerprofil(data, kalla=f"{namn}.toml")
            d = las_baskarta_dwg(iter([dwg]), lagerprofil=profil)
            self.assertEqual(len(d.markslagsytor), 1)
            self.assertEqual(d.lagerprofil_sha256, profil.sha256)
            with tempfile.TemporaryDirectory() as t:
                r = bygg_markmodell_ruta(
                    RUTA,
                    t,
                    grund_z=grund,
                    markslagsytor=d.markslagsytor,
                    lagerprofil=profil,
                    standard_markslag=GRAS,
                    kodversion="k",
                    start_tid="t",
                )
            resultat[namn] = r
        self.assertEqual(int(resultat["a"].markslag[10, 3]), 1)
        self.assertEqual(int(resultat["b"].markslag[10, 3]), 7)  # profilen styr klassen
        self.assertEqual(int(resultat["a"].markslag[10, 15]), GRAS)  # utanför ytan: standard
        self.assertNotEqual(resultat["a"].recept_sha256, resultat["b"].recept_sha256)


if __name__ == "__main__":
    unittest.main()


class TestCliDwg(unittest.TestCase):
    def test_markmodell_dwg_skriver_kallfil_och_profilens_sha(self) -> None:
        from floden.cli import main as cli_main

        obj = [
            {"object": "BLOCK_HEADER", "handle": [0, 1, 31], "name": "*Model_Space"},
            {"object": "LAYER", "handle": [0, 1, 41], "name": "ASFALT_1"},
            {
                "entity": "HATCH",
                "handle": [0, 1, 52],
                "entmode": 2,
                "layer": [5, 1, 41, 41],
                "paths": [
                    {
                        "flag": 1,
                        "polyline_paths": [
                            {"point": [150000, 6576000], "bulge": 0},
                            {"point": [150010, 6576000], "bulge": 0},
                            {"point": [150010, 6576020], "bulge": 0},
                            {"point": [150000, 6576020], "bulge": 0},
                        ],
                    }
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as t:
            rot = Path(t)
            (rot / "b.json").write_text(json.dumps({"OBJECTS": obj}), encoding="utf-8")
            (rot / "p.toml").write_bytes(PROFIL_B)
            rader = ["e_m,n_m,z_m"]
            for e in range(150000, 150020):
                for n in range(6576000, 6576020):
                    rader.append(f"{e + 0.5},{n + 0.5},10.0")
            (rot / "l.csv").write_text("\n".join(rader), encoding="utf-8")
            kod = cli_main(
                [
                    "markmodell", "--ut", str(rot / "ut"), "--laser-csv", str(rot / "l.csv"),
                    "--dwg", str(rot / "b.json"), "--lagerprofil", str(rot / "p.toml"),
                    "--upplosning-m", "1.0",
                ]
            )  # fmt: skip
            self.assertEqual(kod, 0)
            recept = las_recept(rot / "ut" / "recept.json")
            profil = tolka_lagerprofil(PROFIL_B)
            self.assertEqual(
                [k.sha256 for k in recept.kallor if k.roll == "lagerprofil"], [profil.sha256]
            )
            self.assertTrue((rot / "ut" / "markslag_kalla.tif").is_file())
            self.assertIn("markslag_prioritet", recept.installningar)


class TestCliBelagdYta(unittest.TestCase):
    def test_laser_las_med_intensitet_ger_belagd_yta_som_kalla(self) -> None:
        from data_inlasning.lantmateriet import skriv_syntetisk_las
        from floden.cli import main as cli_main

        # 120 x 120 m: västra halvan "asfalt" (låg intensitet), östra "gräs" (hög), bara markpunkter
        e, n, z, k, i = [], [], [], [], []
        for x in np.arange(0.25, 120.0, 0.5):
            for y in np.arange(0.25, 120.0, 0.5):
                e.append(660000.0 + x)
                n.append(6570000.0 + y)
                z.append(10.0)
                k.append(2)
                i.append(5000 if x < 60 else 30000)
        with tempfile.TemporaryDirectory() as t:
            rot = Path(t)
            skriv_syntetisk_las(
                rot / "l.las",
                np.array(e), np.array(n), np.array(z),
                np.array(k, dtype=np.uint8), np.array(i, dtype=np.uint16),
            )  # fmt: skip
            kod = cli_main(
                [
                    "markmodell", "--ut", str(rot / "ut"), "--laser-las", str(rot / "l.las"),
                    "--laser-epsg", "3011", "--belagd-yta", "--upplosning-m", "1.0",
                ]
            )  # fmt: skip
            run = json.loads((rot / "ut" / "run.json").read_text(encoding="utf-8"))
            recept = las_recept(rot / "ut" / "recept.json")
            self.assertTrue((rot / "ut" / "markslag_kalla.tif").is_file())
        self.assertEqual(kod, 0)
        self.assertIn("belagd_yta", [k.roll for k in recept.kallor])
        self.assertIn("laser_las", [k.roll for k in recept.kallor])
        self.assertIn("markslag_belagd_installningar", recept.installningar)
        mv = run["matvarden"]
        # västra halvan är belagd: ungefär halva rutan har belagd yta som avgörande källa
        self.assertGreater(mv["markslag_celler_belagd_laser"], 0.3 * 120 * 120)
        self.assertLess(mv["markslag_celler_belagd_laser"], 0.6 * 120 * 120)
        self.assertEqual(mv["markslag_celler_baskarta"], 0)
