"""Tester för köraren: konstgjord kärna och emission, kända svar och resultatklasser."""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import numpy as np

from buller.akustik.band import a_vagning_db
from buller.emission import Vagemission, Vagforhallanden, las_emissionsdata
from buller.testfall import (
    Terrangdata,
    Trafikpost,
    Utfall,
    hitta_fall,
    kor_alla,
    kor_fall,
    sammanstallning_md,
)
from buller.testfall.korare import berakna_rak_vag
from buller.typer import ANTAL_BAND, BullerFel, Flyttal, P2PKarna, Terrangprofil, Vader
from tests.buller.testfall.hjalp import (
    Frifaltskarna,
    Konstantemission,
    exempel_facit,
    exempel_fall,
)

N_ROT = Path(__file__).resolve().parents[3] / "testsvit" / "N_nord2000"
NOLLVAGNING = np.zeros(ANTAL_BAND)
TOM_DATA = Path(__file__).parent / "tom_emission.toml"


class TestBerakning(unittest.TestCase):
    def setUp(self) -> None:
        self.karna = Frifaltskarna()
        self.emission = Konstantemission(100.0)
        # Källa och mottagare på samma höjd över plan mark: avståndet blir d / cos θ exakt.
        self.fall = exempel_fall(
            mottagare_hojd_m=0.5,
            terrang=Terrangdata((0.0, 60.0), (0.0, 0.0), (200.0,), (0.0,)),
        )

    def test_karnan_ar_en_p2pkarna(self) -> None:
        self.assertIsInstance(self.karna, P2PKarna)

    def test_linjekalla_i_fritt_falt_mot_analytiskt_svar(self) -> None:
        # Frifältskärnan ger 1 / (4π·r²) med r = d / cos θ. Källpunkten i vinkeln θ står för
        # sträckan d·(tan(θ + Δ/2) − tan(θ − Δ/2)). Summan över 179 punkter räknas här för
        # hand och ska stämma exakt; den ska också ligga nära integralen för en linjekälla
        # sedd under 179°, W'·θ / (4π·d).
        d = 48.0
        ber = berakna_rak_vag(self.karna, self.emission, self.fall, NOLLVAGNING)
        effekt_per_meter_db = 100.0 + 10.0 * math.log10(10000.0 / (80.0 / 3.6 * 86400.0))
        steg = math.radians(1.0)
        summa = sum(
            d * (math.tan(t + steg / 2) - math.tan(t - steg / 2)) * math.cos(t) ** 2 / d**2
            for t in (math.radians(k - 89.0) for k in range(179))
        )
        vantat = effekt_per_meter_db + 10.0 * math.log10(summa / (4.0 * math.pi))
        np.testing.assert_allclose(ber.spektrum_db["Leq_24h"], vantat, atol=1e-9)
        # De yttersta punkterna (±89°) gör att summan inte är exakt lika med integralen.
        integral = effekt_per_meter_db + 10.0 * math.log10(math.radians(179.0) / (4 * math.pi * d))
        self.assertAlmostEqual(float(ber.spektrum_db["Leq_24h"][0]), integral, delta=0.05)
        self.assertEqual(self.karna.anrop, 179)

    def test_exponeringsniva_och_a_vagda_totaler(self) -> None:
        ber = berakna_rak_vag(self.karna, self.emission, self.fall, a_vagning_db())
        skillnad = ber.spektrum_db["LE"] - ber.spektrum_db["Leq_24h"]
        np.testing.assert_allclose(skillnad, 10.0 * math.log10(86400.0 / 10000.0))
        vantad_total = 10.0 * np.log10(
            np.sum(10.0 ** ((ber.spektrum_db["Leq_24h"] + a_vagning_db()) / 10.0))
        )
        self.assertAlmostEqual(ber.totalnivaer_db["LAeq_24h"], float(vantad_total))
        self.assertAlmostEqual(
            ber.totalnivaer_db["LAE"] - ber.totalnivaer_db["LAeq_24h"],
            10.0 * math.log10(8.64),
        )

    def test_dubblad_trafik_ger_tre_decibel_i_mottagaren(self) -> None:
        en = berakna_rak_vag(self.karna, self.emission, self.fall, NOLLVAGNING)
        dubbel = exempel_fall(
            mottagare_hojd_m=0.5,
            terrang=self.fall.terrang,
            trafik=(Trafikpost(1, 20000.0, 80.0, "T"),),
        )
        tva = berakna_rak_vag(self.karna, self.emission, dubbel, NOLLVAGNING)
        np.testing.assert_allclose(
            tva.spektrum_db["Leq_24h"] - en.spektrum_db["Leq_24h"], 10.0 * math.log10(2.0)
        )
        # Exponeringsnivån för en passage beror inte på trafikmängden.
        np.testing.assert_allclose(tva.spektrum_db["LE"], en.spektrum_db["LE"])

    def test_vind_utan_riktning_och_fel_typ_ger_fel(self) -> None:
        # Sedan P-R2 räknas fall med vind när bladets vindriktning φ finns; utan den går
        # vindkomponenten längs utbredningsvägarna inte att bestämma.
        with self.assertRaises(BullerFel) as ctx:
            berakna_rak_vag(
                self.karna,
                self.emission,
                exempel_fall(vader=Vader(vind_m_s=3.0), vader_tillagg={}),
                NOLLVAGNING,
            )
        self.assertIn("vind", str(ctx.exception))
        with self.assertRaises(BullerFel):
            berakna_rak_vag(self.karna, self.emission, self.fall, np.zeros(3))

    def test_vind_projiceras_pa_varje_utbredningsvag(self) -> None:
        """Kärnan får u·cos(φ − θ) för källpunkten i vinkeln θ (se `vindkomponent_m_s`)."""

        class Vindlogg(Frifaltskarna):
            def __init__(self) -> None:
                super().__init__()
                self.vindar: list[float] = []

            def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
                self.vindar.append(vader.vind_m_s)
                return super().overforing_db(profil, vader)

        for riktning, tecken in ((0.0, 1.0), (180.0, -1.0)):
            karna = Vindlogg()
            fall = exempel_fall(
                vader=Vader(vind_m_s=3.0), vader_tillagg={"vindriktning_grader": riktning}
            )
            berakna_rak_vag(karna, self.emission, fall, NOLLVAGNING)
            vindar = np.array(karna.vindar)
            # Medvind (φ = 0) eller motvind (φ = 180) från alla källpunkter, full styrka mitt för.
            self.assertTrue(bool(np.all(tecken * vindar > 0.0)))
            self.assertAlmostEqual(float(np.max(tecken * vindar)), 3.0, 9)
            np.testing.assert_allclose(vindar, vindar[::-1], atol=1e-12)
        karna = Vindlogg()
        sidvind = exempel_fall(
            vader=Vader(vind_m_s=3.0), vader_tillagg={"vindriktning_grader": 90.0}
        )
        berakna_rak_vag(karna, self.emission, sidvind, NOLLVAGNING)
        vindar = np.array(karna.vindar)
        np.testing.assert_allclose(vindar, -vindar[::-1], atol=1e-12)  # medvind ena hållet


class TestUtfall(unittest.TestCase):
    def setUp(self) -> None:
        self.fall = exempel_fall()
        self.emission = Konstantemission(100.0)
        self.grund = berakna_rak_vag(Frifaltskarna(), self.emission, self.fall, a_vagning_db())

    def _kor(self, tillagg_db: float | np.ndarray, ar_facit: bool = True, **kw: object) -> Utfall:
        tillagg = np.zeros(ANTAL_BAND) + tillagg_db
        facit = exempel_facit(ar_facit, self.grund.totalnivaer_db, self.grund.spektrum_db)
        resultat = kor_fall(Frifaltskarna(tillagg), self.emission, self.fall, facit)
        self.resultat = resultat
        return resultat.utfall

    def test_samma_karna_ger_godkant(self) -> None:
        self.assertIs(self._kor(0.0), Utfall.GODKAND)
        self.assertAlmostEqual(self.resultat.max_total_avvikelse_db or 0.0, 0.0)
        self.assertIn("inte Nord2000-konform", self.resultat.kommentar)

    def test_resultatklasser(self) -> None:
        self.assertIs(self._kor(0.4), Utfall.GODKAND)
        self.assertIs(self._kor(0.7), Utfall.SKA_UTREDAS)
        self.assertIs(self._kor(1.4), Utfall.UNDERKAND)
        ett_band = np.zeros(ANTAL_BAND)
        ett_band[0] = 3.0  # 25 Hz väger nästan inget i A-nivån men bandet ska utredas
        self.assertIs(self._kor(ett_band), Utfall.SKA_UTREDAS)
        self.assertLess(self.resultat.max_total_avvikelse_db or 9.0, 0.1)
        self.assertAlmostEqual(self.resultat.max_band_avvikelse_db or 0.0, 3.0)

    def test_ej_facit_oavsett_avvikelse(self) -> None:
        for tillagg in (0.0, 0.7, 12.0):
            with self.subTest(tillagg):
                self.assertIs(self._kor(tillagg, ar_facit=False), Utfall.EJ_FACIT)
                self.assertTrue(all(s.klass is Utfall.EJ_FACIT for s in self.resultat.serier))
                self.assertIn("Ej facit", self.resultat.kommentar)

    def test_ej_facit_aven_nar_fallet_inte_gar_att_rakna(self) -> None:
        facit = exempel_facit(False, {"LAeq_24h": 1.0}, {})
        resultat = kor_fall(Frifaltskarna(), Konstantemission(fel="tom tabell"), self.fall, facit)
        self.assertIs(resultat.utfall, Utfall.EJ_FACIT)
        self.assertIn("tom tabell", resultat.kommentar)

    def test_tom_kallmodell_ger_ej_korbar(self) -> None:
        facit = exempel_facit(True, self.grund.totalnivaer_db, self.grund.spektrum_db)
        resultat = kor_fall(
            Frifaltskarna(), Vagemission(las_emissionsdata(TOM_DATA)), self.fall, facit
        )
        self.assertIs(resultat.utfall, Utfall.EJ_KORBAR)
        self.assertIn("kategorier.1.rull.a_db", resultat.kommentar)
        self.assertEqual(resultat.serier, ())

    def test_ofullstandig_indata_ger_ej_korbar(self) -> None:
        fall = exempel_fall(
            indata_fullstandig=False, indata_saknas=("koordinater",), terrang=None, vag=None
        )
        facit = exempel_facit(True, {"LAeq_24h": 50.0}, {})
        resultat = kor_fall(Frifaltskarna(), self.emission, fall, facit)
        self.assertIs(resultat.utfall, Utfall.EJ_KORBAR)
        self.assertIn("koordinater", resultat.kommentar)

    def test_villkor_styr_mottagarhojd_och_okanda_villkor_stoppar(self) -> None:
        hog = berakna_rak_vag(
            Frifaltskarna(), self.emission, exempel_fall(mottagare_hojd_m=4.0), a_vagning_db()
        )
        facit = exempel_facit(True, hog.totalnivaer_db, hog.spektrum_db, {"mottagare_hojd_m": 4.0})
        self.assertIs(
            kor_fall(Frifaltskarna(), self.emission, self.fall, facit).utfall, Utfall.GODKAND
        )
        okant = exempel_facit(True, hog.totalnivaer_db, hog.spektrum_db, {"reflektion": 0.7})
        resultat = kor_fall(Frifaltskarna(), self.emission, self.fall, okant)
        self.assertIs(resultat.utfall, Utfall.EJ_KORBAR)

    def test_lamax_i_facit_hindrar_godkant(self) -> None:
        totaler = {**self.grund.totalnivaer_db, "LAmax": 60.0}
        facit = exempel_facit(True, totaler, self.grund.spektrum_db)
        resultat = kor_fall(Frifaltskarna(), self.emission, self.fall, facit)
        self.assertIs(resultat.utfall, Utfall.SKA_UTREDAS)
        self.assertIn("LAmax", resultat.kommentar)
        urval = kor_fall(
            Frifaltskarna(), self.emission, self.fall, facit,
            kravda_storheter={"LAeq_24h", "LAE", "Leq_24h", "LE"},
        )  # fmt: skip
        self.assertIs(urval.utfall, Utfall.GODKAND)


class TestDigitaliseradeFallIKoraren(unittest.TestCase):
    def setUp(self) -> None:
        self.resultat = kor_alla(Frifaltskarna(), Konstantemission(100.0), N_ROT)
        self.per_id = {r.fall_id: r for r in self.resultat}

    def test_bilagornas_fall_blir_ej_facit(self) -> None:
        for fall_id in ("N01", "N02", "N03", "N04"):
            self.assertIs(self.per_id[fall_id].utfall, Utfall.EJ_FACIT)
        # N01 går att räkna (avvikelser redovisas), de övriga saknar geometri.
        self.assertIsNotNone(self.per_id["N01"].max_band_avvikelse_db)
        self.assertEqual([len(self.per_id[i].serier) for i in ("N02", "N03", "N04")], [0, 0, 0])

    def test_rl18_med_konstgjord_karna_blir_aldrig_godkant(self) -> None:
        # Frifältskärnan och den konstgjorda emissionen har inget med Nord2000 att göra, så
        # inget fall får bli godkänt; fall utan vind ska gå att räkna och underkännas.
        rl18 = [r for r in self.resultat if r.fall_id.startswith("N18")]
        self.assertEqual(len(rl18), 80)
        self.assertFalse([r.fall_id for r in rl18 if r.utfall is Utfall.GODKAND])
        self.assertFalse([r.fall_id for r in rl18 if r.utfall is Utfall.EJ_FACIT])
        self.assertIs(self.per_id["N18R001"].utfall, Utfall.UNDERKAND)
        # Fall med vind räknas sedan P-R2 (vindriktningen φ är tolkad) och underkänns också.
        self.assertIs(self.per_id["N18R012"].utfall, Utfall.UNDERKAND)
        self.assertIs(self.per_id["N18R121"].utfall, Utfall.EJ_KORBAR)  # skog
        self.assertIs(self.per_id["N18K1"].utfall, Utfall.EJ_KORBAR)

    def test_kanda_fel_redovisas_som_undantag(self) -> None:
        kommentar = self.per_id["N18R001"].kommentar
        self.assertIn("KCB-2024-1", kommentar)
        self.assertIn("LAmax jämförs inte (KCB-2024-2", kommentar)
        self.assertNotIn("KCB-2024", self.per_id["N01"].kommentar)

    def test_tom_kallmodell_ger_aldrig_godkant(self) -> None:
        resultat = kor_alla(Frifaltskarna(), Vagemission(las_emissionsdata(TOM_DATA)), N_ROT)
        self.assertEqual({r.utfall for r in resultat}, {Utfall.EJ_FACIT, Utfall.EJ_KORBAR})

    def test_medfoljande_kallmodell_gar_att_kora_och_far_belaggning_och_temperatur(self) -> None:
        emission = Konstantemission(100.0)
        fall = next(f for f in hitta_fall(N_ROT) if f.id == "N18R004")
        kor_fall(Frifaltskarna(), emission, fall)
        self.assertEqual(emission.forhallanden, Vagforhallanden("DAC", 12.0, None, 15.0))
        riktig = kor_fall(Frifaltskarna(), Vagemission(), fall)
        # Frifältskärnan saknar mark- och luftdämpning, så fallet ska inte bli godkänt, men
        # det ska gå att räkna med den medföljande källmodellen.
        self.assertIn(riktig.utfall, (Utfall.UNDERKAND, Utfall.SKA_UTREDAS))
        self.assertEqual(len(riktig.serier), 1)

    def test_sammanstallning(self) -> None:
        text = sammanstallning_md(self.resultat)
        self.assertIn("Antal fall: 84.", text)
        self.assertIn("godkänd: 0", text)
        self.assertIn("ej facit: 4", text)
        self.assertIn("| N01 | Rak väg, fall 1", text)
        self.assertEqual(text.count("| ej facit |"), 4)
        self.assertNotIn("| godkänd |", text)


if __name__ == "__main__":
    unittest.main()
