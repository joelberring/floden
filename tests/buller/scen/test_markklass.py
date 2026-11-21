"""Tester för markklasstabellen: inläsning, struktur och fel. Inga tabellvärden upprepas här."""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

import numpy as np

from buller.scen.markklass import (
    K1_MARKSLAGSKODER,
    STANDARDTABELL,
    las_markklasstabell,
    standardtabell,
)
from buller.typer import BullerFel


class TestStandardtabell(unittest.TestCase):
    def test_struktur(self) -> None:
        tabell = standardtabell()
        self.assertEqual(tabell.version, "markklass/v1")  # buller-versionsmärkningen är kvar
        self.assertTrue(tabell.preliminar, "översättningen ska vara märkt preliminär")
        self.assertEqual(sorted(tabell.impedansklasser_kNsm4), list("ABCDEFGH"))
        self.assertEqual(sorted(tabell.rahetsklasser_m), ["L", "M", "N", "S"])
        self.assertEqual(sorted(tabell.markslag), list(K1_MARKSLAGSKODER))
        self.assertIn("Nord2000", tabell.kalla)

    def test_klasserna_ar_ordnade_fran_mjuk_till_hard(self) -> None:
        tabell = standardtabell()
        varden = [tabell.impedansklasser_kNsm4[b] for b in "ABCDEFGH"]
        self.assertEqual(varden, sorted(varden))
        self.assertEqual(len(set(varden)), 8)

    def test_uppslag_stammer_med_filen(self) -> None:
        ra = tomllib.loads(STANDARDTABELL.read_text(encoding="utf-8"))
        tabell = standardtabell()
        sigma, rahet = tabell.uppslag()
        for kod in K1_MARKSLAGSKODER:
            post = ra["markklass"][str(kod)]["buller"]
            self.assertEqual(sigma[kod], ra["buller"]["impedansklass"][post["impedansklass"]])
            self.assertEqual(rahet[kod], ra["buller"]["rahetsklass"][post["rahetsklass"]])
            self.assertEqual(tabell.klass(kod).impedansklass, post["impedansklass"])
        self.assertTrue(bool(np.isnan(sigma[0])), "kod 0 (okänt) får inte ha något värde")
        self.assertTrue(bool(np.all(np.isnan(sigma[10:]))))
        self.assertEqual(
            tabell.byggnadsyta.flodesresistivitet_kNsm4,
            ra["buller"]["impedansklass"][ra["buller"]["byggnadsyta"]["impedansklass"]],
        )

    def test_filen_anger_kalla_och_verifiering(self) -> None:
        ra = tomllib.loads(STANDARDTABELL.read_text(encoding="utf-8"))
        for nyckel in ("dokument", "url", "impedansklasser", "rahetsklasser", "verifierad"):
            self.assertTrue(ra["buller"]["kalla"][nyckel], nyckel)
        self.assertIn("Table 10", ra["buller"]["kalla"]["impedansklasser"])

    def test_okand_kod_ger_fel(self) -> None:
        for kod in (0, 10, 255):
            with self.assertRaises(BullerFel):
                standardtabell().klass(kod)
        with self.assertRaises(BullerFel):
            standardtabell().egenskaper_for_impedansklass("Z")


class TestTrasigaTabeller(unittest.TestCase):
    """Tomma eller felaktiga värden ska stoppa inläsningen."""

    def _las(self, text: str) -> None:
        with tempfile.TemporaryDirectory() as mapp:
            fil = Path(mapp) / "tabell.toml"
            fil.write_text(text, encoding="utf-8")
            las_markklasstabell(fil)

    def test_andringar_som_ska_ge_fel(self) -> None:
        original = STANDARDTABELL.read_text(encoding="utf-8")
        g_rad = next(rad for rad in original.splitlines() if rad.startswith("G = "))
        andringar = [
            (g_rad, 'G = ""'),  # tomt värde
            (g_rad, "G = -1.0"),
            (g_rad, "G = 0.0"),
            (g_rad, ""),  # klassen saknas men används
            ('version = "markklass/v1"', ""),
            ("preliminar = true", ""),
            # kod 0 (okänt) får inte översättas
            (
                "[markklass.0.buller]\n",
                '[markklass.0.buller]\nimpedansklass = "D"\nrahetsklass = "N"\n',
            ),
            # kod 7 saknar översättning
            ('[markklass.7.buller]\nimpedansklass = "F"\nrahetsklass = "N"\n', ""),
            ('impedansklass = "F"', 'impedansklass = "Z"'),  # okänd klass
            ('dokument = "', 'dokument_saknas = "'),
            ("\n[buller.byggnadsyta]\n", "\n[buller.annat]\n"),
        ]
        for gammalt, nytt in andringar:
            self.assertIn(gammalt, original)
            with self.subTest(nytt=nytt), self.assertRaises(BullerFel):
                self._las(original.replace(gammalt, nytt, 1))

    def test_originalet_gar_att_lasa_fran_kopia(self) -> None:
        self._las(STANDARDTABELL.read_text(encoding="utf-8"))

    def test_saknad_fil_och_trasig_toml(self) -> None:
        with self.assertRaises(BullerFel):
            las_markklasstabell("/finns/inte/tabell.toml")
        with self.assertRaises(BullerFel):
            self._las("detta är inte = = toml")


if __name__ == "__main__":
    unittest.main()
