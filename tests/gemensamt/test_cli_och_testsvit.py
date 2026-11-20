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

    def test_cli_regn_lista_och_export(self) -> None:
        ut_lista = io.StringIO()
        with redirect_stdout(ut_lista):
            kod_lista = main(["regn", "--lista", "--region", "Stockholm"])
        self.assertEqual(kod_lista, 0)
        text = ut_lista.getvalue()
        self.assertIn("stockholm_cds_6h_100ar_kf124", text)
        self.assertIn("stockholm_cds_6h_100ar_kf140", text)
        self.assertIn("stockholm_cds_6h_500ar_kf140", text)

        with tempfile.TemporaryDirectory() as tmp:
            ut_fil = Path(tmp) / "sthlm100.csv"
            ut_exp = io.StringIO()
            with redirect_stdout(ut_exp):
                kod_exp = main(
                    ["regn", "--id", "stockholm_cds_6h_100ar_kf140", "--ut", str(ut_fil)]
                )
            self.assertEqual(kod_exp, 0)
            self.assertTrue(ut_fil.is_file())
            self.assertIn("tid_s,intensitet_mm_h", ut_fil.read_text(encoding="utf-8"))

    def test_cli_risk_a3(self) -> None:
        ut = io.StringIO()
        with redirect_stdout(ut):
            kod = main(
                [
                    "risk",
                    "--omrade",
                    "A3",
                    "--gransdjup-m",
                    "0.20",
                    "--max-aterkomsttid-ar",
                    "100",
                    "--klimatfaktor",
                    "1.24",
                    "--tidshorisont-ar",
                    "50",
                ]
            )
        self.assertEqual(kod, 0)
        text = ut.getvalue()
        self.assertIn("Kumulativ översvämningsrisk", text)
        self.assertIn("Yta över gränsdjup", text)
        self.assertIn("| 100 |", text)


if __name__ == "__main__":
    unittest.main()
