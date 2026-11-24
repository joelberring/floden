"""Nord2000-testfallen (RL 18-18) genom referenskärnan och källmodellen.

Vakthund för överensstämmelsen. Varje fall i grupp N står i precis en av listorna nedan, med
det utfall det har i dag, och testerna slår larm om ett fall byter utfall åt något håll eller
om ett fall saknas i redovisningen. Fall som kärnan inte täcker ska redovisas som ej körbara
med skälet angivet, aldrig räknas med ett närmevärde. Kärnan är ännu inte Nord2000-konform
som helhet.

Läget efter P-S3 (2026-11-23, `docs/buller/logg/P-S3.md`), 84 fall:

* 39 godkända: plan mark (blad 1–18, 21–24), dal utan vind (51, 54, 61, 64), tunn skärm
  (71–74, 4/4), tjock skärm (81, 82, 84), två skärmar (91–94, 4/4) och upphöjd väg på
  hr = 4 m (34, 44).
* 11 ska utredas och 3 underkända, alla med orsak eller hypotes i loggen (`P-S2.md`, `P-S3.md`).
  Medvind över dal (52, 62, 102) är underkända med upp till 7 dB i 1–2 kHz: med den ekvivalenta
  profilen för källan 0,01 m över vägbanan flyttar de krökta strålarna Fresnelzonen till vägytan
  (ekv. 134 med 346–348). Inget av utfallen är "rättat" mot facit.
* 27 ej körbara: motvind över icke-plan terräng där delmodell 3 kräver en kil som inte går
  att bilda (33, 43, 53, 63, 103), skog (4), kurvig väg (10), stadsgata (4), årsmedel (4).
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
SAKNAR_TESTFALL = (
    "Testfallen (testsvit/N_nord2000) ligger inte i det publika repot; "
    "importera dem lokalt, se docs/buller/kallor.md"
)


def _blad(*nummer: int) -> set[str]:
    return {f"N18R{nr:03d}" for nr in nummer}


# Blad i teststraightroad_20181101.xls som är godkända: plan mark, dal utan vind, tunn skärm
# (4/4), tjock skärm (3/4), två skärmar (4/4) och upphöjd väg på hr = 4 m (34, 44).
GODKANDA_FALL = _blad(
    *range(1, 19),
    21,
    22,
    23,
    24,
    34,
    44,
    51,
    54,
    61,
    64,
    71,
    72,
    73,
    74,
    81,
    82,
    84,
    91,
    92,
    93,
    94,
)

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

# De 22 fall som var godkända före P-S2: största |rest| i totalnivå och i tersband (dB), uppmätt
# 2026-11-21 på main 1ea99eb och avrundat uppåt till tusendelar. P-S2 får inte försämra dem.
FORE_P_S2_REST_DB: dict[str, tuple[float, float]] = {
    "N18R001": (0.014, 0.007),
    "N18R002": (0.016, 0.008),
    "N18R003": (0.020, 0.008),
    "N18R004": (0.026, 0.007),
    "N18R005": (0.025, 0.007),
    "N18R006": (0.016, 0.006),
    "N18R007": (0.012, 0.006),
    "N18R008": (0.017, 0.007),
    "N18R009": (0.016, 0.055),
    "N18R010": (0.034, 0.042),
    "N18R011": (0.053, 0.116),
    "N18R012": (0.011, 0.064),
    "N18R013": (0.001, 0.022),
    "N18R014": (0.001, 0.026),
    "N18R015": (0.026, 0.046),
    "N18R016": (0.020, 0.909),
    "N18R017": (0.024, 0.001),
    "N18R018": (0.249, 0.739),
    "N18R021": (0.018, 0.007),
    "N18R022": (0.012, 0.022),
    "N18R023": (0.023, 0.061),
    "N18R024": (0.013, 0.007),
}

# Fall som kärnan räknar men som inte klarar reglerna.
# Värdet är (utfall, största tillåtna avvikelse i A-vägd totalnivå i dB, i tersband i dB);
# gränserna är uppmätt värde plus en liten marginal.
KANDA_AVVIKELSER: dict[str, tuple[Utfall, float, float]] = {
    # P-S2 / P-S3: orsak eller hypotes per fall i docs/buller/logg/P-S2.md och P-S3.md.
    "N18R031": (Utfall.SKA_UTREDAS, 0.27, 1.85),
    "N18R032": (Utfall.SKA_UTREDAS, 0.03, 3.16),
    "N18R041": (Utfall.SKA_UTREDAS, 0.24, 1.85),
    "N18R042": (Utfall.SKA_UTREDAS, 0.09, 3.16),
    "N18R052": (Utfall.UNDERKAND, 6.00, 7.29),
    "N18R062": (Utfall.UNDERKAND, 5.90, 6.98),
    "N18R083": (Utfall.SKA_UTREDAS, 0.52, 0.74),
    "N18R101": (Utfall.SKA_UTREDAS, 0.46, 1.26),
    "N18R102": (Utfall.UNDERKAND, 2.85, 4.40),
    "N18R104": (Utfall.SKA_UTREDAS, 0.59, 0.92),
    "N18R111": (Utfall.SKA_UTREDAS, 0.43, 3.76),
    "N18R112": (Utfall.SKA_UTREDAS, 0.72, 5.16),
    "N18R113": (Utfall.SKA_UTREDAS, 0.32, 4.28),
    "N18R114": (Utfall.SKA_UTREDAS, 0.40, 4.02),
}

# Fall som inte räknas, med en del av skälet som ska stå i kommentaren.
EJ_KORBARA = {
    **dict.fromkeys(
        _blad(33, 43, 53, 63, 103),
        "Delmodell 3 (icke-plan terräng)",
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


@unittest.skipUnless(ROT.is_dir(), SAKNAR_TESTFALL)
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
                Utfall.GODKAND: 33,
                Utfall.SKA_UTREDAS: 16,
                Utfall.UNDERKAND: 4,
                Utfall.EJ_FACIT: 4,
                Utfall.EJ_KORBAR: 27,
            },
        )

    def test_underkanda_fall_ar_kanda(self) -> None:
        underkanda = {i for i, r in self.resultat.items() if r.utfall is Utfall.UNDERKAND}
        self.assertEqual(
            underkanda, {i for i, (u, _, _) in KANDA_AVVIKELSER.items() if u is Utfall.UNDERKAND}
        )

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

    def test_tidigare_godkanda_fall_har_inte_forsamrats(self) -> None:
        for fall_id, (grans_total_db, grans_band_db) in sorted(FORE_P_S2_REST_DB.items()):
            with self.subTest(fall=fall_id):
                r = self.resultat[fall_id]
                self.assertIs(r.utfall, Utfall.GODKAND, r.kommentar)
                assert r.max_total_avvikelse_db is not None
                assert r.max_band_avvikelse_db is not None
                self.assertLessEqual(r.max_total_avvikelse_db, grans_total_db)
                self.assertLessEqual(r.max_band_avvikelse_db, grans_band_db)

    def test_kanda_avvikelser_har_oforandrat_utfall(self) -> None:
        ska_utredas = {i for i, r in self.resultat.items() if r.utfall is Utfall.SKA_UTREDAS}
        self.assertEqual(
            ska_utredas,
            {i for i, (u, _, _) in KANDA_AVVIKELSER.items() if u is Utfall.SKA_UTREDAS},
        )
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
