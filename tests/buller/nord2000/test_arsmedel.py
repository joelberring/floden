"""Enhetstester för årsmedel (L_den, Met. statistics och väderklassviktning)."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.nord2000.arsmedel import (
    VaderklassBas,
    berakna_arsmedel_rak_vag,
    berakna_lden_db,
    las_met_statistik,
)
from buller.testfall.format import N2kFall, Terrangdata
from buller.typer import ANTAL_BAND, BullerFel, Flyttal, Terrangprofil, Vader


class _KonstantKarna:
    namn = "test-konstant"
    nord2000_konform = False

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        return np.full(ANTAL_BAND, -50.0, dtype=np.float64)


class _KonstantEmission:
    @property
    def delkallhojder_m(self) -> tuple[float, ...]:
        return (0.01,)

    def ljudeffekt_db(
        self,
        trafik: object,
        segment_langd_m: float,
        period_s: float,
        delkallhojder_m: object = None,
        forhallanden: object = None,
    ) -> Flyttal:
        return np.full((1, ANTAL_BAND), 100.0, dtype=np.float64)


def _syntetisk_met_statistik() -> list[list[str]]:
    """Minsta möjliga giltiga `Met. statistics` med 9 tabeller och 4 riktningar (0–270°)."""
    rader: list[list[str]] = []
    for _ in range(3):  # andel (%) dag, kväll, natt
        rader.append(["Direction", "0", "90", "180", "270"])
        rader.append(["M1", "0", "0", "0", "0"])
        rader.append(["M3", "60", "40", "20", "80"])
        rader.append(["M8", "40", "60", "80", "20"])
        rader.append([])
    for _ in range(3):  # temperatur (°C)
        rader.append(["Direction", "0", "90", "180", "270"])
        rader.append(["M1", "", "", "", ""])
        rader.append(["M3", "10", "12", "14", "8"])
        rader.append(["M8", "15", "15", "15", "15"])
        rader.append([])
    for _ in range(3):  # relativ fuktighet (%)
        rader.append(["Direction", "0", "90", "180", "270"])
        rader.append(["M1", "", "", "", ""])
        rader.append(["M3", "70", "75", "80", "65"])
        rader.append(["M8", "80", "80", "80", "80"])
        rader.append([])
    return rader


class TestArsmedel(unittest.TestCase):
    def test_lden_exakt_analytiskt_svar(self) -> None:
        # Konstant nivå 60 dB hela dygnet ger 60 + 10·lg((12 + 3·10^0,5 + 9·10)/24) ≈ 66,67 dB.
        lden = float(berakna_lden_db(60.0, 60.0, 60.0))
        vantat = 60.0 + 10.0 * math.log10((12.0 + 3.0 * math.sqrt(10.0) + 90.0) / 24.0)
        self.assertAlmostEqual(lden, vantat, places=12)

    def test_lden_vektor_och_ogiltiga_timmar(self) -> None:
        v = berakna_lden_db(
            np.full(ANTAL_BAND, 50.0), np.full(ANTAL_BAND, 50.0), np.full(ANTAL_BAND, 50.0)
        )
        self.assertEqual(v.shape, (ANTAL_BAND,))
        with self.assertRaises(BullerFel):
            berakna_lden_db(50.0, 50.0, 50.0, timmar=(12.0, 4.0, 9.0))

    def test_las_met_statistik_och_cirkular_interpolation(self) -> None:
        met = las_met_statistik(_syntetisk_met_statistik())
        self.assertEqual(met.aktiva_klasser(), ("M3", "M8"))
        # Mitt mellan 270° och 0° (315°): M3 = (0,8 + 0,6)/2 = 0,7; temp = (8 + 10)/2 = 9 °C.
        klasser = met.dag.for_riktning(315.0)
        self.assertAlmostEqual(klasser["M3"][0], 0.7, places=12)
        self.assertAlmostEqual(klasser["M3"][1], 9.0, places=12)
        self.assertAlmostEqual(klasser["M3"][2], 67.5, places=12)
        self.assertAlmostEqual(klasser["M8"][0], 0.3, places=12)

    def test_saknade_vaderklasser_ger_tydligt_fel(self) -> None:
        met = las_met_statistik(_syntetisk_met_statistik())
        fall = N2kFall(
            id="N18A1",
            namn="Test",
            typ="nord2000_arsmedel",
            beskrivning="Syntetiskt test",
            status="ofullstandig",
            kalla={"dokument": "test"},
            indata_fullstandig=False,
            indata_saknas=("test",),
            mottagare_x_m=100.0,
            mottagare_hojd_m=1.5,
            terrang=Terrangdata((3.25, 100.0), (0.0, 0.0), (200.0,), (0.0,)),
            ovrigt={
                "antal_fordon_dag": 7889.0,
                "antal_fordon_kvall": 791.0,
                "antal_fordon_natt": 1320.0,
                "hastighet_km_h": 80.0,
            },
        )
        with self.assertRaises(BullerFel) as ctx:
            berakna_arsmedel_rak_vag(
                _KonstantKarna(),
                _KonstantEmission(),
                fall,
                met,
                {},
                90.0,
                np.zeros(ANTAL_BAND),
                antal_kallpunkter=3,
                vinkelupplosning_grader=10.0,
            )
        self.assertIn("VTT-R-02530-06", str(ctx.exception))
        self.assertIn("M3", str(ctx.exception))

    def test_berakna_arsmedel_med_syntetiska_klasser(self) -> None:
        met = las_met_statistik(_syntetisk_met_statistik())
        klasser = {
            "M3": VaderklassBas("M3", 0.0, 0.0, 0.5, 0.0, 0.12, 0.008),
            "M8": VaderklassBas("M8", 3.0, 0.0, 0.5, 0.0, 0.12, 0.008),
        }
        fall = N2kFall(
            id="N18A1",
            namn="Test",
            typ="nord2000_arsmedel",
            beskrivning="Syntetiskt test",
            status="ofullstandig",
            kalla={"dokument": "test"},
            indata_fullstandig=False,
            indata_saknas=("test",),
            mottagare_x_m=100.0,
            mottagare_hojd_m=1.5,
            terrang=Terrangdata((3.25, 100.0), (0.0, 0.0), (200.0,), (0.0,)),
            ovrigt={
                "antal_fordon_dag": 7889.0,
                "antal_fordon_kvall": 791.0,
                "antal_fordon_natt": 1320.0,
                "hastighet_km_h": 80.0,
            },
        )
        res = berakna_arsmedel_rak_vag(
            _KonstantKarna(),
            _KonstantEmission(),
            fall,
            met,
            klasser,
            90.0,
            np.zeros(ANTAL_BAND),
            antal_kallpunkter=3,
            vinkelupplosning_grader=10.0,
        )
        # 3 källpunkter med L_W + H = 50 dB, vikt = 1 i varje period → 50 + 10·lg(3) per band.
        vantat_band = 50.0 + 10.0 * math.log10(3.0)
        np.testing.assert_allclose(res.ldag_spektrum_db, vantat_band, atol=1e-10)
        vantat_lden = float(berakna_lden_db(vantat_band, vantat_band, vantat_band))
        np.testing.assert_allclose(res.lden_spektrum_db, vantat_lden, atol=1e-10)


if __name__ == "__main__":
    unittest.main()
