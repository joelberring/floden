"""Enhetstester för `gemensamt.koordinater`."""

from __future__ import annotations

import builtins
import importlib.util
import sys
import unittest
from unittest.mock import patch

from gemensamt.koordinater import (
    EPSG_INTERN,
    EPSG_LANTMATERIET,
    HOJDSYSTEM,
    KoordinatFel,
    skapa_transformator_till_3011,
    transformera_till_3011,
)

HAR_PYPROJ = importlib.util.find_spec("pyproj") is not None


class TestKoordinater(unittest.TestCase):
    def test_konstanter(self) -> None:
        self.assertEqual(EPSG_INTERN, 3011)
        self.assertEqual(EPSG_LANTMATERIET, 3006)
        self.assertEqual(HOJDSYSTEM, "RH 2000")

    def test_identitet_for_3011_utan_pyproj(self) -> None:
        e, n = transformera_till_3011(150123.45, 6576789.12, 3011)
        self.assertEqual((e, n), (150123.45, 6576789.12))

    def test_ogiltig_epsg_ger_tydligt_fel(self) -> None:
        for ogiltig in (0, -3006, True):
            with self.assertRaises(KoordinatFel):
                skapa_transformator_till_3011(ogiltig)  # type: ignore[arg-type]

    def test_saknad_pyproj_ger_tydligt_fel(self) -> None:
        riktig_import = builtins.__import__

        def mock_import(
            name: str,
            globals_: dict[str, object] | None = None,
            locals_: dict[str, object] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name == "pyproj" or name.startswith("pyproj."):
                raise ImportError("No module named 'pyproj'")
            return riktig_import(name, globals_, locals_, fromlist, level)

        with (
            patch.dict(sys.modules, {"pyproj": None}),
            patch("builtins.__import__", side_effect=mock_import),
        ):
            with self.assertRaises(KoordinatFel) as ctx:
                transformera_till_3011(674000.0, 6580000.0, 3006)
            self.assertIn("pyproj", str(ctx.exception))

    @unittest.skipUnless(HAR_PYPROJ, "pyproj är inte installerat i denna miljö")
    def test_transformera_fran_3006_till_3011_med_pyproj(self) -> None:
        # Ungefärliga koordinater i Stockholm (Hägersten-Älvsjö):
        # SWEREF 99 TM (EPSG:3006): E ~ 671000, N ~ 6576000
        # SWEREF 99 18 00 (EPSG:3011): E ~ 150000, N ~ 6576000
        e_3011, n_3011 = transformera_till_3011(671000.0, 6576000.0, EPSG_LANTMATERIET)
        self.assertGreater(e_3011, 140000.0)
        self.assertLess(e_3011, 165000.0)
        self.assertGreater(n_3011, 6570000.0)
        self.assertLess(n_3011, 6585000.0)


if __name__ == "__main__":
    unittest.main()
