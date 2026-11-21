"""Tester för toleransprofilerna."""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from buller.lagen.profil import (
    PROFILKATALOG,
    SKISSMARKNING,
    las_inbyggd_profil,
    las_profil,
)
from buller.typer import BullerFel
from gemensamt.hash import sha256_fil

_SKISS = """
[profil]
namn = "egen"
version = "1"
lage = "{lage}"
markning = "{markning}"

[klustring]
felgrans_db = {felgrans}
kontrollmottagare_antal = 0

[banor]
reflexordning = 1
{extra}
"""


def skriv(katalog: str, **falt: object) -> Path:
    varden = {
        "lage": "skiss",
        "markning": SKISSMARKNING,
        "felgrans": 0.5,
        "extra": "",
        **falt,
    }
    fil = Path(katalog) / "profil.toml"
    fil.write_text(_SKISS.format(**varden), encoding="utf-8")
    return fil


class TestToleransprofil(unittest.TestCase):
    def test_inbyggda_profiler(self) -> None:
        skiss = las_inbyggd_profil("skiss")
        rapport = las_inbyggd_profil("rapport")
        self.assertEqual(skiss.lage, "skiss")
        self.assertEqual(skiss.markning, "Skiss – ej för beslut")
        self.assertEqual(skiss.klustring_felgrans_db, 0.5)
        self.assertEqual(rapport.lage, "rapport")
        self.assertEqual(rapport.klustring_felgrans_db, 0.1)
        self.assertNotEqual(rapport.markning, SKISSMARKNING)
        self.assertIsNone(rapport.maxradie_m)
        self.assertEqual(skiss.sha256, sha256_fil(PROFILKATALOG / "skiss.toml"))
        self.assertEqual(rapport.sha256, sha256_fil(PROFILKATALOG / "rapport.toml"))
        self.assertNotEqual(skiss.sha256, rapport.sha256)
        self.assertEqual(skiss.till_manifest()["sha256"], skiss.sha256)
        self.assertEqual(skiss.till_manifest()["namn"], "skiss")

    def test_profilerna_innehaller_inga_riktvarden_eller_vaderdata(self) -> None:
        for namn in ("skiss", "rapport"):
            with (PROFILKATALOG / f"{namn}.toml").open("rb") as fil:
                data = tomllib.load(fil)
            self.assertEqual(set(data), {"profil", "klustring", "banor"})
            nycklar = {nyckel for sektion in data.values() for nyckel in sektion}
            for nyckel in nycklar:
                self.assertNotRegex(nyckel, "riktvarde|vader|vind|temperatur|trafik")

    def test_sha256_foljer_filens_innehall(self) -> None:
        with tempfile.TemporaryDirectory() as katalog:
            a = las_profil(skriv(katalog))
            b = las_profil(skriv(katalog, felgrans=0.4))
            self.assertNotEqual(a.sha256, b.sha256)
            self.assertEqual(b.klustring_felgrans_db, 0.4)

    def test_skiss_maste_markas(self) -> None:
        with tempfile.TemporaryDirectory() as katalog, self.assertRaises(BullerFel):
            las_profil(skriv(katalog, markning="Klar"))

    def test_rapport_tillater_inte_felgrans_over_en_tiondels_db(self) -> None:
        with tempfile.TemporaryDirectory() as katalog:
            las_profil(skriv(katalog, lage="rapport", markning="Rapport", felgrans=0.1))
            with self.assertRaises(BullerFel):
                las_profil(skriv(katalog, lage="rapport", markning="Rapport", felgrans=0.11))
            with self.assertRaises(BullerFel):
                las_profil(skriv(katalog, lage="rapport", felgrans=0.1))

    def test_okanda_och_saknade_nycklar_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as katalog:
            with self.assertRaises(BullerFel):
                las_profil(skriv(katalog, extra="riktvarde_db = 55"))
            with self.assertRaises(BullerFel):
                las_profil(skriv(katalog, extra="[riktvarden]\nfasad_db = 60"))
            with self.assertRaises(BullerFel):
                las_profil(skriv(katalog, lage="utkast"))
            with self.assertRaises(BullerFel):
                las_profil(skriv(katalog, felgrans='"0.5"'))
            with self.assertRaises(BullerFel):
                las_profil(skriv(katalog, felgrans=-1))
            with self.assertRaises(BullerFel):
                las_profil(skriv(katalog, extra="maxradie_m = 0"))
            fil = Path(katalog) / "trasig.toml"
            fil.write_text("[profil\n", encoding="utf-8")
            with self.assertRaises(BullerFel):
                las_profil(fil)
            with self.assertRaises(BullerFel):
                las_profil(Path(katalog) / "saknas.toml")


if __name__ == "__main__":
    unittest.main()
