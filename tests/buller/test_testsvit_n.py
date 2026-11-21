"""Nord2000-testfallen (RL 18-18) genom referenskärnan och källmodellen.

Vakthund för överensstämmelsen: de fall som kärnan täcker utan kända avvikelser ska vara
godkända enligt rapportens regler. Fall som kärnan inte täcker ska redovisas som ej körbara,
aldrig räknas med ett närmevärde. Kärnan är ännu inte Nord2000-konform som helhet.

Sedan P-R2 räknar kärnan också plan mark med refraktion. Sex sådana fall går att köra men
klarar inte reglerna; de står i `KANDA_AVVIKELSER` med det utfall de har i dag, och orsakerna
(så långt de är kända) står i `docs/buller/logg/P-R2.md` under "Avvikelser". Listan är ett
kvitto på läget, inte ett godkännande: ett fall som byter utfall ska få testet att slå larm,
åt vilket håll det än går. Inget annat fall får vara underkänt.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from buller.emission import Vagemission
from buller.nord2000 import Nord2000Karna
from buller.testfall.jamforelse import Utfall
from buller.testfall.korare import Fallresultat, kor_alla

ROT = Path(__file__).resolve().parents[2] / "testsvit" / "N_nord2000"

# Blad i teststraightroad_20181101.xls som kärnan täcker (plan mark, homogen atmosfär).
TACKTA_FALL = {f"N18R{nr:03d}" for nr in (1, 2, 3, 4, 5, 6, 7, 8, 17, 18)}

# Plan mark med refraktion som kärnan räknar men som inte klarar reglerna (P-R2, 2026-11-21).
# Värdet är (utfall, största tillåtna avvikelse i A-vägd totalnivå i dB, uppmätt + marginal).
KANDA_AVVIKELSER = {
    "N18R010": (Utfall.SKA_UTREDAS, 0.5),  # dt/dz = −0,1: tersband 500–1250 Hz upp till 2,3 dB
    "N18R011": (Utfall.SKA_UTREDAS, 0.5),  # s_u = 1: 10 kHz-bandet 2,9 dB
    "N18R021": (Utfall.SKA_UTREDAS, 0.5),  # blandad mark, s_u = 0,5: 10 kHz-bandet 2,6 dB
    "N18R024": (Utfall.SKA_UTREDAS, 0.5),  # som 21 med h_r = 4 m: 10 kHz-bandet 2,5 dB
    "N18R015": (Utfall.UNDERKAND, 2.0),  # motvind: totalnivå 1,9 dB, tersband upp till 6,9 dB
    "N18R023": (Utfall.UNDERKAND, 2.0),  # motvind, blandad mark: 1,7 dB, tersband upp till 5,4 dB
}

# Blad med nedåtbrytning där delmodell 8 (flera markreflexer) skulle bidra och saknas.
VAGRAS_FOR_DELMODELL8 = {f"N18R{nr:03d}" for nr in (9, 12, 13, 14, 16, 22)}


class TestTestsvitN(unittest.TestCase):
    karna: Nord2000Karna
    resultat: dict[str, Fallresultat]

    @classmethod
    def setUpClass(cls) -> None:
        cls.karna = Nord2000Karna()
        cls.resultat = {r.fall_id: r for r in kor_alla(cls.karna, Vagemission(), ROT)}

    def test_karnan_ar_inte_markt_konform(self) -> None:
        self.assertFalse(self.karna.nord2000_konform)

    def test_inget_ovantat_fall_ar_underkant(self) -> None:
        underkanda = {i for i, r in self.resultat.items() if r.utfall is Utfall.UNDERKAND}
        kanda = {i for i, (utfall, _) in KANDA_AVVIKELSER.items() if utfall is Utfall.UNDERKAND}
        self.assertEqual(underkanda, kanda)

    def test_kanda_avvikelser_har_oforandrat_utfall(self) -> None:
        for fall_id, (utfall, grans_db) in sorted(KANDA_AVVIKELSER.items()):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                self.assertIs(r.utfall, utfall, r.kommentar)
                assert r.max_total_avvikelse_db is not None
                self.assertLessEqual(r.max_total_avvikelse_db, grans_db)

    def test_nedatbrytning_med_flera_markreflexer_vagras(self) -> None:
        for fall_id in sorted(VAGRAS_FOR_DELMODELL8):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                self.assertIs(r.utfall, Utfall.EJ_KORBAR)
                self.assertIn("markreflexer", r.kommentar)

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
