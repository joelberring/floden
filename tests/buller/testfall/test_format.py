"""Tester för testfallsformatet: validering, rundtur och de digitaliserade fallen."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from buller.testfall import hitta_fall, las_facit, las_fall, skriv_fall
from buller.testfall.format import FACITFIL, FALLFIL, fall_till_toml
from buller.testfall.index import las_index
from buller.testfall.kontroll import kontrollutskrift_md
from buller.typer import ANTAL_BAND, BullerFel
from tests.buller.testfall.hjalp import exempel_facit, exempel_fall
from testsvit.korare import hitta_testfall

REPO = Path(__file__).resolve().parents[3]
N_ROT = REPO / "testsvit" / "N_nord2000"


class Tempmapp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mapp = Path(self._tmp.name)

    def _skriv_toml(self, text: str) -> Path:
        (self.mapp / FALLFIL).write_text(text, encoding="utf-8")
        return self.mapp


class TestRundtur(Tempmapp):
    def test_skriv_och_las_ger_samma_fall_och_facit(self) -> None:
        fall = exempel_fall()
        spektrum = {"Leq_24h": np.linspace(40.0, 14.0, ANTAL_BAND), "LE": np.arange(27.0) - 3.5}
        facit = exempel_facit(True, {"LAeq_24h": 39.4, "LAE": 48.76}, spektrum, {"vind_m_s": 0.0})
        skriv_fall(fall, self.mapp / "NT1", facit)

        last = las_fall(self.mapp / "NT1")
        for namn in (
            "id", "namn", "grupp", "typ", "beskrivning", "status", "indata_fullstandig",
            "indata_saknas", "vag", "mottagare_x_m", "mottagare_hojd_m", "terrang", "trafik",
            "vader",
        ):  # fmt: skip
            self.assertEqual(getattr(last, namn), getattr(fall, namn), namn)
        self.assertEqual(dict(last.kalla), dict(fall.kalla))
        self.assertEqual(dict(last.perioder_h), dict(fall.perioder_h))
        self.assertEqual(dict(last.vader_tillagg), dict(fall.vader_tillagg))
        self.assertEqual(dict(last.ovrigt), dict(fall.ovrigt))
        self.assertEqual(last.mapp, self.mapp / "NT1")

        last_facit = las_facit(self.mapp / "NT1")
        self.assertTrue(last_facit.ar_facit)
        totaler = dict(last_facit.serier[0].totalnivaer_db)
        self.assertEqual(totaler, {"LAeq_24h": 39.4, "LAE": 48.76})
        self.assertEqual(dict(last_facit.serier[0].villkor), {"vind_m_s": 0.0})
        for namn, varden in spektrum.items():
            np.testing.assert_array_equal(last_facit.serier[0].spektrum_db[namn], varden)
        # En andra rundtur ger bytevis samma filer.
        skriv_fall(last, self.mapp / "kopia", last_facit)
        for fil in (FALLFIL, FACITFIL):
            self.assertEqual(
                (self.mapp / "kopia" / fil).read_bytes(), (self.mapp / "NT1" / fil).read_bytes()
            )

    def test_ofullstandigt_fall_gar_rundtur(self) -> None:
        fall = exempel_fall(
            indata_fullstandig=False, indata_saknas=("koordinater",), vag=None, terrang=None,
            mottagare_x_m=None, status="ofullstandig",
        )  # fmt: skip
        skriv_fall(fall, self.mapp)
        last = las_fall(self.mapp)
        self.assertFalse(last.indata_fullstandig)
        self.assertEqual(last.indata_saknas, ("koordinater",))
        self.assertIsNone(last.terrang)


class TestValidering(Tempmapp):
    def test_saknad_fil_och_trasig_toml(self) -> None:
        with self.assertRaises(BullerFel):
            las_fall(self.mapp)
        with self.assertRaises(BullerFel):
            las_fall(self._skriv_toml("id = "))

    def test_saknade_huvudfalt(self) -> None:
        with self.assertRaises(BullerFel) as ctx:
            las_fall(self._skriv_toml('id = "N9"\nnamn = "x"\n'))
        self.assertIn("grupp", str(ctx.exception))

    def test_fel_i_enskilda_falt(self) -> None:
        bra = fall_till_toml(exempel_fall())
        fel = {
            "fel grupp": bra.replace('grupp = "N"', 'grupp = "A"'),
            "okänd typ": bra.replace('typ = "nord2000_rak_vag"', 'typ = "syntetisk"'),
            "okänd status": bra.replace('status = "redo"', 'status = "kanske"'),
            "fel kontrakt": bra.replace('"N-fall/v1"', '"N-fall/v0"'),
            "text i tal": bra.replace("hojd_over_mark_m = 1.5", 'hojd_over_mark_m = "1,5"'),
            "okänt väderfält": bra.replace("cv2 = 0.0", "cv2 = 0.0\nvindriktning = 3.0"),
            "icke växande x": bra.replace("x_m = [0.0, 5.0, 60.0]", "x_m = [0.0, 5.0, 5.0]"),
            "fel antal markvärden": bra.replace("rahet_m = [0.0, 0.25]", "rahet_m = [0.0]"),
            "negativ resistivitet": bra.replace("[20000.0, 200.0]", "[20000.0, -1.0]"),
            "okänd period": bra.replace('period = "T"', 'period = "natt"'),
            "noll hastighet": bra.replace("hastighet_km_h = 80.0", "hastighet_km_h = 0.0"),
            "mottagare utanför": bra.replace("x_m = 50.0", "x_m = 500.0"),
            "källa efter mottagare": bra.replace("kalla_x_m = 2.0", "kalla_x_m = 55.0"),
            "för många källpunkter": bra.replace("punkter = 179", "punkter = 181"),
            "decimalt antal": bra.replace("punkter = 179", "punkter = 179.5"),
            "saknas trots fullständig": bra.replace("saknas = []", 'saknas = ["något"]'),
            "ingen källa": bra.replace('dokument = "påhittat"', 'titel = "påhittat"'),
        }
        self.assertEqual(las_fall(self._skriv_toml(bra)).id, "NT1")
        for namn, text in fel.items():
            with self.subTest(namn):
                self.assertNotEqual(text, bra, "ersättningen träffade inte")
                with self.assertRaises(BullerFel):
                    las_fall(self._skriv_toml(text))

    def test_fullstandigt_fall_utan_terrang_eller_vader(self) -> None:
        for saknat in ("terrang", "vader", "vag", "mottagare_hojd_m"):
            with self.subTest(saknat), self.assertRaises(BullerFel):
                exempel_fall(**{saknat: None})
        with self.assertRaises(BullerFel):
            exempel_fall(trafik=())
        with self.assertRaises(BullerFel):
            exempel_fall(indata_fullstandig=False)  # utan lista över vad som saknas

    def test_facitfel(self) -> None:
        fall = exempel_fall()
        facit = exempel_facit(False, {"LAeq_24h": 40.0}, {"Leq_24h": np.zeros(ANTAL_BAND)})
        skriv_fall(fall, self.mapp, facit)
        fil = self.mapp / FACITFIL
        bra = json.loads(fil.read_text(encoding="utf-8"))

        def med(andring: dict[str, object]) -> None:
            fil.write_text(json.dumps({**bra, **andring}), encoding="utf-8")

        fel: dict[str, dict[str, object]] = {
            "ar_facit som text": {"ar_facit": "nej"},
            "tomt skäl": {"ar_facit_skal": " "},
            "fel kontrakt": {"kontrakt": "x"},
            "fel band": {"tersband_hz": bra["tersband_hz"][:-1]},
            "inga serier": {"serier": []},
            "kort spektrum": {
                "serier": [{"id": "s", "spektrum_db": {"Leq_24h": [1.0, 2.0]}}]
            },
            "tom serie": {"serier": [{"id": "s"}]},
            "dubbla id": {"serier": [bra["serier"][0], bra["serier"][0]]},
        }
        for namn, andring in fel.items():
            with self.subTest(namn):
                med(andring)
                with self.assertRaises(BullerFel):
                    las_facit(self.mapp)
        fil.write_text("{", encoding="utf-8")
        with self.assertRaises(BullerFel):
            las_facit(self.mapp)
        fil.unlink()
        with self.assertRaises(BullerFel):
            las_facit(self.mapp)

    def test_profil_klipps_och_stracks(self) -> None:
        fall = exempel_fall()
        profil = fall.profil(kalla_hojd_m=0.3, stracka=2.0)
        np.testing.assert_allclose(profil.x_m, [0.0, 6.0, 96.0])
        np.testing.assert_allclose(profil.z_m, [0.0, 0.0, 45.0 / 55.0])
        np.testing.assert_allclose(profil.flodesresistivitet_kNsm4, [20000.0, 200.0])
        np.testing.assert_allclose(profil.rahet_m, [0.0, 0.25])
        self.assertEqual((profil.kalla_hojd_m, profil.mottagare_hojd_m), (0.3, 1.5))
        with self.assertRaises(BullerFel):
            fall.profil(kalla_hojd_m=0.3, stracka=0.5)


class TestDigitaliseradeFall(unittest.TestCase):
    def test_alla_fall_laser_och_ar_inte_facit(self) -> None:
        fall = [f for f in hitta_fall(N_ROT) if not f.id.startswith("N18")]
        self.assertEqual([f.id for f in fall], ["N01", "N02", "N03", "N04"])
        for f in fall:
            assert f.mapp is not None
            facit = las_facit(f.mapp)
            self.assertFalse(facit.ar_facit, f.id)
            self.assertIn("äldre version", facit.ar_facit_skal)
            self.assertEqual(f.status, "ej_facit")
        self.assertEqual([f.indata_fullstandig for f in fall], [True, False, False, False])
        self.assertEqual([len(las_facit(f.mapp).serier) for f in fall if f.mapp], [1, 3, 6, 1])

    def test_bilaga_a_ar_inbordes_konsekvent(self) -> None:
        # L_E − L_eq,24h ska vara 10·lg(86 400 / 10 000) inom avrundningen i tabellen.
        serie = las_facit(N_ROT / "N01_mp1335_bilaga_a_rak_vag").serier[0]
        skillnad = serie.spektrum_db["LE"] - serie.spektrum_db["Leq_24h"]
        np.testing.assert_allclose(skillnad, 10.0 * np.log10(8.64), atol=0.0101)
        self.assertAlmostEqual(
            serie.totalnivaer_db["LAE"] - serie.totalnivaer_db["LAeq_24h"], 9.36, places=2
        )

    def test_gamla_testsvitskoraren_ser_inte_n_fallen(self) -> None:
        gamla = hitta_testfall(REPO / "testsvit")
        self.assertTrue(gamla)
        self.assertNotIn("N", {f.grupp for f in gamla})

    def test_index_har_62_fall(self) -> None:
        poster = las_index(N_ROT / "index_mp1335_rak_vag.toml")
        self.assertEqual(len(poster), 62)
        self.assertEqual([p.nr for p in poster if p.grupp_nr == 12], [121, 122, 123, 124])
        self.assertEqual(sum(1 for p in poster if p.grupp_nr == 1), 18)
        self.assertEqual({p.nr: p.fordonskategori for p in poster if p.fordonskategori != 1},
                         {17: 2, 18: 3})

    def test_rl18_fallen_ar_facit_med_regelverk_och_kalla(self) -> None:
        fall = [f for f in hitta_fall(N_ROT) if f.id.startswith("N18")]
        self.assertEqual(len(fall), 80)
        antal = {"R": 0, "K": 0, "S": 0, "A": 0}
        for f in fall:
            assert f.mapp is not None
            facit = las_facit(f.mapp)
            antal[f.id[3]] += 1
            self.assertEqual(facit.regelverk, "rl18_18")
            self.assertIn("RL 18-18", str(facit.kalla["dokument"]))
            self.assertIn("20181101", str(facit.kalla["excelfil"]))
            self.assertIn("december 2018", facit.metodversion)
            self.assertTrue(facit.ar_facit, f.id)
        self.assertEqual(antal, {"R": 62, "K": 10, "S": 4, "A": 4})
        korbara = [f.id for f in fall if f.indata_fullstandig]
        self.assertEqual(len(korbara), 58)  # rak väg utom de fyra skogsfallen
        tunga = {f.id: f.trafik[0].kategori for f in fall if f.trafik and f.id[3] == "R"}
        self.assertEqual((tunga["N18R017"], tunga["N18R018"], tunga["N18R016"]), (2, 3, 1))

    def test_bilaga_a_och_rl18_fall_1_har_samma_tal(self) -> None:
        # Bilagans exempel (avläst för hand, två decimaler) mot Excel-bladet för fall 1.
        bilaga = las_facit(N_ROT / "N01_mp1335_bilaga_a_rak_vag").serier[0]
        blad = las_facit(N_ROT / "N18R001_rak_vag").serier[0]
        for namn in ("Leq_24h", "LE", "dL"):
            np.testing.assert_allclose(
                bilaga.spektrum_db[namn], blad.spektrum_db[namn], atol=0.0051
            )
        for namn, varde in bilaga.totalnivaer_db.items():
            self.assertAlmostEqual(varde, blad.totalnivaer_db[namn], delta=0.0051)

    def test_trasigt_index_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "index.toml"
            fil.write_text("[meta]\nantal_fall = 3\n", encoding="utf-8")
            with self.assertRaises(BullerFel):
                las_index(fil)

    def test_kontrollutskrift_innehaller_tabellvarden(self) -> None:
        par = [
            (f, las_facit(f.mapp))
            for f in hitta_fall(N_ROT)
            if f.mapp is not None and not f.id.startswith("N18")
        ]
        text = kontrollutskrift_md(par)
        self.assertIn("| 25 | 45.58 | 54.95 | 4.89 | ☐ |", text)
        self.assertIn("| 3.25 | 0 | 20000 | 0 | ☐ |", text)
        self.assertEqual(
            text, (REPO / "docs/buller/kontroll/testfall_tabeller.md").read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()
