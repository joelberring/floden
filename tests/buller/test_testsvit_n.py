"""Nord2000-testfallen (RL 18-18) genom referenskärnan och källmodellen.

Vakthund för överensstämmelsen. Varje fall i grupp N står i precis en av listorna nedan, med
det utfall det har i dag, och testerna slår larm om ett fall byter utfall åt något håll eller
om ett fall saknas i redovisningen. Fall som kärnan inte täcker ska redovisas som ej körbara
med skälet angivet, aldrig räknas med ett närmevärde. Kärnan är ännu inte Nord2000-konform
som helhet.

Läget efter P-S1 (2026-11-21, `docs/buller/logg/P-S1.md`), 84 fall:

* 21 godkända: plan mark, blad 1–13, 15–18 och 21–24 (homogen atmosfär, temperaturgradient,
  medvind med flera markreflexer, motvind med skuggzon, blandad mark).
* 1 ska utredas: blad 14 (sidvind). Avvikelsen ligger i 800–6300 Hz, det område där
  källmodellens horisontella direktivitet (SP 2006:12 ekv. 2.12) verkar; den är inte införd
  i `buller.emission`. Se loggen.
* 0 underkända.
* 58 ej körbara: icke-plan terräng (20), skärmar (16), skog (4), kurvig väg (10), stadsgata
  (4) och årsmedel (4).
* 4 ej facit (N01–N04, Miljøprojekt 1335/2010).

Listan `KANDA_AVVIKELSER` är ett kvitto på läget, inte ett godkännande.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from buller.emission import Vagemission
from buller.nord2000 import Nord2000Karna
from buller.testfall.jamforelse import Utfall
from buller.testfall.korare import Fallresultat, kor_alla

ROT = Path(__file__).resolve().parents[2] / "testsvit" / "N_nord2000"


def _blad(*nummer: int) -> set[str]:
    return {f"N18R{nr:03d}" for nr in nummer}


# Blad i teststraightroad_20181101.xls som är godkända: plan mark med och utan väder.
GODKANDA_FALL = _blad(*range(1, 19), 21, 22, 23, 24)

# De tio blad som var godkända redan före refraktionen (homogen atmosfär); största avvikelse i
# A-vägd totalnivå i dag, i dB (uppmätt värde avrundat uppåt till två decimaler).
HOMOGENA_FALL_TOTALAVVIKELSE_DB = {
    "N18R001": 0.30,
    "N18R002": 0.28,
    "N18R003": 0.23,
    "N18R004": 0.16,
    "N18R005": 0.11,
    "N18R006": 0.24,
    "N18R007": 0.07,
    "N18R008": 0.27,
    "N18R017": 0.19,
    "N18R018": 0.37,
}

# Fall som kärnan räknar men som inte klarar reglerna.
# Värdet är (utfall, största tillåtna avvikelse i A-vägd totalnivå i dB, i tersband i dB);
# gränserna är uppmätt värde plus en liten marginal.
KANDA_AVVIKELSER: dict[str, tuple[Utfall, float, float]] = {
    # Blad 14 (sidvind) blev godkänt när källmodellens direktivitet infördes (B5b, 2026-11-21).
}

# Fall som inte räknas, med en del av skälet som ska stå i kommentaren.
EJ_KORBARA = {
    **dict.fromkeys(
        _blad(31, 32, 33, 34, 41, 42, 43, 44, 51, 52, 53, 54, 61, 62, 63, 64, 101, 102, 103, 104),
        "icke-plan terräng",
    ),
    **dict.fromkeys(
        _blad(71, 72, 73, 74, 81, 82, 83, 84, 91, 92, 93, 94, 111, 112, 113, 114), "skärmar"
    ),
    **dict.fromkeys(_blad(121, 122, 123, 124), "spridningszonen"),
    **dict.fromkeys(
        (
            "N18K1",
            "N18K2",
            "N18K3",
            "N18K4-1",
            "N18K4-2",
            "N18K5-1",
            "N18K5-2",
            "N18K6",
            "N18K7",
            "N18K8",
        ),
        "nord2000_rak_vag",
    ),
    **dict.fromkeys(("N18S1", "N18S2", "N18S3", "N18S4"), "energireflektionskoefficient"),
    **dict.fromkeys(("N18A1", "N18A2", "N18A3", "N18A4"), "saknar väder"),
}

EJ_FACIT = {"N01", "N02", "N03", "N04"}

# Blad med nedåtbrytning där N > 4 i ekv. (279) för minst en källpunkt. Till och med P-R2
# vägrades de eftersom delmodell 8 saknades; nu ska de räknas.
BLAD_MED_FLERA_MARKREFLEXER = _blad(9, 12, 13, 14, 16, 22)


class TestTestsvitN(unittest.TestCase):
    karna: Nord2000Karna
    resultat: dict[str, Fallresultat]

    @classmethod
    def setUpClass(cls) -> None:
        cls.karna = Nord2000Karna()
        cls.resultat = {r.fall_id: r for r in kor_alla(cls.karna, Vagemission(), ROT)}

    def test_karnan_ar_inte_markt_konform(self) -> None:
        self.assertFalse(self.karna.nord2000_konform)

    def test_varje_fall_star_i_precis_en_lista(self) -> None:
        """Inget fall får tyst försvinna ur redovisningen, och inget får stå två gånger."""
        listor = [GODKANDA_FALL, set(KANDA_AVVIKELSER), set(EJ_KORBARA), EJ_FACIT]
        self.assertEqual(sum(len(lista) for lista in listor), len(set().union(*listor)))
        self.assertEqual(set().union(*listor), set(self.resultat))
        self.assertEqual(len(self.resultat), 84)

    def test_antal_per_utfall(self) -> None:
        antal = {u: sum(1 for r in self.resultat.values() if r.utfall is u) for u in Utfall}
        self.assertEqual(
            antal,
            {
                Utfall.GODKAND: 22,
                Utfall.SKA_UTREDAS: 0,
                Utfall.UNDERKAND: 0,
                Utfall.EJ_FACIT: 4,
                Utfall.EJ_KORBAR: 58,
            },
        )

    def test_inget_fall_ar_underkant(self) -> None:
        underkanda = {i for i, r in self.resultat.items() if r.utfall is Utfall.UNDERKAND}
        self.assertEqual(underkanda, set())

    def test_godkanda_fall(self) -> None:
        for fall_id in sorted(GODKANDA_FALL):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                self.assertIs(r.utfall, Utfall.GODKAND, r.kommentar)
                assert r.max_total_avvikelse_db is not None
                assert r.max_band_avvikelse_db is not None
                self.assertLessEqual(r.max_total_avvikelse_db, 0.5)
                self.assertLessEqual(r.max_band_avvikelse_db, 1.0)

    def test_inga_andra_fall_ar_godkanda(self) -> None:
        godkanda = {i for i, r in self.resultat.items() if r.utfall is Utfall.GODKAND}
        self.assertEqual(godkanda, GODKANDA_FALL)

    def test_homogena_fall_har_inte_forsamrats(self) -> None:
        for fall_id, grans_db in sorted(HOMOGENA_FALL_TOTALAVVIKELSE_DB.items()):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                assert r.max_total_avvikelse_db is not None
                self.assertLessEqual(r.max_total_avvikelse_db, grans_db)

    def test_kanda_avvikelser_har_oforandrat_utfall(self) -> None:
        ska_utredas = {i for i, r in self.resultat.items() if r.utfall is Utfall.SKA_UTREDAS}
        self.assertEqual(ska_utredas, set(KANDA_AVVIKELSER))
        for fall_id, (utfall, grans_total_db, grans_band_db) in sorted(KANDA_AVVIKELSER.items()):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                self.assertIs(r.utfall, utfall, r.kommentar)
                assert r.max_total_avvikelse_db is not None
                assert r.max_band_avvikelse_db is not None
                self.assertLessEqual(r.max_total_avvikelse_db, grans_total_db)
                self.assertLessEqual(r.max_band_avvikelse_db, grans_band_db)

    def test_nedatbrytning_med_flera_markreflexer_raknas(self) -> None:
        """Delmodell 8 är inkopplad: inget blad vägras längre för att N > 4."""
        for fall_id in sorted(BLAD_MED_FLERA_MARKREFLEXER):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                self.assertIn(r.utfall, (Utfall.GODKAND, Utfall.SKA_UTREDAS), r.kommentar)
                self.assertNotIn("markreflexer", r.kommentar)

    def test_ej_korbara_fall_har_skalet_angivet(self) -> None:
        ej_korbara = {i for i, r in self.resultat.items() if r.utfall is Utfall.EJ_KORBAR}
        self.assertEqual(ej_korbara, set(EJ_KORBARA))
        for fall_id, skal in sorted(EJ_KORBARA.items()):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                self.assertIn(skal, r.kommentar)
                self.assertIsNone(r.max_total_avvikelse_db)

    def test_ej_facit(self) -> None:
        ej_facit = {i for i, r in self.resultat.items() if r.utfall is Utfall.EJ_FACIT}
        self.assertEqual(ej_facit, EJ_FACIT)


if __name__ == "__main__":
    unittest.main()
