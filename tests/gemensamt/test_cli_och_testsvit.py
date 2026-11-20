"""Enhetstester för CLI (`floden.cli`) och testsvitsköraren (`testsvit.korare`)."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from floden.cli import main
from testsvit.korare import TestsvitFel, formatera_tabell, hitta_testfall, kor_testsvit


class TestCliOchTestsvit(unittest.TestCase):
    def test_cli_help_avslutar_med_noll(self) -> None:
        ut = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(ut):
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        hjalptext = ut.getvalue()
        for kommando in ("markmodell", "sankgraf", "kor", "testsvit"):
            self.assertIn(kommando, hjalptext)

    def test_cli_skelettkommandon(self) -> None:
        for kommando, uppdrag in (("markmodell", "T01"), ("sankgraf", "T02"), ("kor", "T03")):
            ut = io.StringIO()
            with redirect_stdout(ut):
                kod = main([kommando])
            self.assertEqual(kod, 0)
            self.assertIn(uppdrag, ut.getvalue())

    def test_cli_testsvit_grupp_a(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(["testsvit", "--grupp", "A", "--rot", "testsvit"])
        self.assertEqual(kod, 0)
        text = ut.getvalue()
        self.assertIn("A0", text)
        self.assertIn("SKELETT", text)

    def test_kor_testsvit_och_tabell(self) -> None:
        fall = hitta_testfall(rot="testsvit", grupp="A")
        self.assertGreaterEqual(len(fall), 1)
        self.assertEqual(fall[0].id, "A0")

        resultat, tabell = kor_testsvit(rot="testsvit", grupp="A")
        self.assertTrue(all(r.godkand for r in resultat))
        self.assertIn("| ID | Grupp |", tabell)
        self.assertEqual(tabell, formatera_tabell(resultat))

    def test_kor_testsvit_saknad_rot_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(TestsvitFel):
            kor_testsvit(rot=Path(tmp) / "finns_ej")


if __name__ == "__main__":
    unittest.main()
