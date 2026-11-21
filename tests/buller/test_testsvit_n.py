"""Nord2000-testfallen (RL 18-18) genom referenskärnan och källmodellen.

Vakthund för överensstämmelsen: de fall som kärnan täcker ska vara godkända enligt rapportens
regler, och inget fall får vara underkänt. Fall som kärnan inte täcker ska redovisas som ej
körbara, aldrig räknas med ett närmevärde. Kärnan är ännu inte Nord2000-konform som helhet.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from buller.emission import Vagemission
from buller.nord2000 import Nord2000Karna
from buller.testfall.jamforelse import Utfall
from buller.testfall.korare import kor_alla

ROT = Path(__file__).resolve().parents[2] / "testsvit" / "N_nord2000"

# Blad i teststraightroad_20181101.xls som kärnan täcker (plan mark, homogen atmosfär).
TACKTA_FALL = {f"N18R{nr:03d}" for nr in (1, 2, 3, 4, 5, 6, 7, 8, 17, 18)}


class TestTestsvitN(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.karna = Nord2000Karna()
        cls.resultat = {r.fall_id: r for r in kor_alla(cls.karna, Vagemission(), ROT)}

    def test_karnan_ar_inte_markt_konform(self) -> None:
        self.assertFalse(self.karna.nord2000_konform)

    def test_inget_fall_ar_underkant(self) -> None:
        underkanda = [i for i, r in self.resultat.items() if r.utfall is Utfall.UNDERKAND]
        self.assertEqual(underkanda, [])

    def test_tackta_fall_ar_godkanda(self) -> None:
        for fall_id in sorted(TACKTA_FALL):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                self.assertIs(r.utfall, Utfall.GODKAND, r.kommentar)
                assert r.max_total_avvikelse_db is not None
                self.assertLessEqual(r.max_total_avvikelse_db, 0.5)

    def test_ovriga_fall_raknas_inte(self) -> None:
        godkanda = {i for i, r in self.resultat.items() if r.utfall is Utfall.GODKAND}
        self.assertEqual(godkanda, TACKTA_FALL)


if __name__ == "__main__":
    unittest.main()
