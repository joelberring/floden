"""Enhetstester för `gemensamt.konfig`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gemensamt.konfig import KonfigFel, las_konfig


class TestKonfig(unittest.TestCase):
    def test_laser_repo_standard_config(self) -> None:
        konfig = las_konfig("config.toml", lokal_fil=None)
        self.assertEqual(konfig["projekt"]["epsg"], 3011)
        self.assertEqual(konfig["projekt"]["hojdsystem"], "RH 2000")
        self.assertEqual(konfig["markmodell"]["ruta_storlek_m"], 500.0)
        self.assertEqual(konfig["markmodell"]["overlapp_m"], 25.0)
        self.assertEqual(konfig["markmodell"]["upplosning_m"], 0.25)
        self.assertIn("standard-12m", konfig["sektioner"])
        self.assertTrue(konfig["rutt_test"]["ar_testvarde"])

    def test_deep_merge_med_lokal_fil(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            bas = rot / "config.toml"
            lokal = rot / "config.local.toml"
            bas.write_text(
                '[projekt]\nepsg = 3011\nnamn = "floden"\n[sokvagar]\ncache = "data/cache"\n',
                encoding="utf-8",
            )
            lokal.write_text(
                '[sokvagar]\ncache = "/tmp/egen_cache"\n[lokal_data]\nbaskarta = "/data/bk"\n',
                encoding="utf-8",
            )

            konfig = las_konfig(bas, lokal)
            self.assertEqual(konfig["projekt"]["epsg"], 3011)
            self.assertEqual(konfig["projekt"]["namn"], "floden")
            self.assertEqual(konfig["sokvagar"]["cache"], "/tmp/egen_cache")
            self.assertEqual(konfig["lokal_data"]["baskarta"], "/data/bk")

    def test_saknad_basfil_ger_konfigfel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saknad = Path(tmp) / "finns_inte.toml"
            with self.assertRaises(KonfigFel):
                las_konfig(saknad, lokal_fil=None)

    def test_ogiltig_toml_ger_konfigfel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trasig = Path(tmp) / "trasig.toml"
            trasig.write_text("detta = [är inte giltig toml", encoding="utf-8")
            with self.assertRaises(KonfigFel):
                las_konfig(trasig, lokal_fil=None)

    def test_ogiltig_lokal_toml_ger_konfigfel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            bas = rot / "config.toml"
            lokal = rot / "config.local.toml"
            bas.write_text("[projekt]\nepsg = 3011\n", encoding="utf-8")
            lokal.write_text("trasig = [", encoding="utf-8")
            with self.assertRaises(KonfigFel):
                las_konfig(bas, lokal)


if __name__ == "__main__":
    unittest.main()
