"""Tester för luftabsorption enligt ISO 9613-1 (B4a)."""

from __future__ import annotations

import unittest

import numpy as np

from buller.akustik.luftabsorption import (
    ljudhastighet_m_s,
    luftabsorption_db_per_m,
    molkoncentration_vattenanga_procent,
)

# ISO 9613-2:1996 tabell 2: dämpningskoefficient i dB/km för oktavband 63 Hz – 8 kHz.
# Nyckel: (temperatur °C, relativ fuktighet %). Värdena kontrollerade 2026-11-21 mot en
# skannad kopia av standarden (puc.sd.gov, docket EL19-003, KMExhibit9.pdf).
ISO_9613_2_TABELL_2_DB_KM: dict[tuple[float, float], tuple[float, ...]] = {
    (10.0, 70.0): (0.1, 0.4, 1.0, 1.9, 3.7, 9.7, 32.8, 117.0),
    (20.0, 70.0): (0.1, 0.3, 1.1, 2.8, 5.0, 9.0, 22.9, 76.6),
    (30.0, 70.0): (0.1, 0.3, 1.0, 3.1, 7.4, 12.7, 23.1, 59.3),
    (15.0, 20.0): (0.3, 0.6, 1.2, 2.7, 8.2, 28.2, 88.8, 202.0),
    (15.0, 50.0): (0.1, 0.5, 1.2, 2.2, 4.2, 10.8, 36.2, 129.0),
    (15.0, 80.0): (0.1, 0.3, 1.1, 2.4, 4.1, 8.3, 23.7, 82.8),
}
# Exakta oktavbandsmittfrekvenser 63 Hz – 8 kHz (bas 10).
OKTAVBAND_HZ = 1000.0 * 10.0 ** (0.3 * np.arange(-4, 4))

# ISO 9613-1:1993 tabell 1, 10 °C och 70 %, dB/km (tre siffror) vid exakta tersbandsfrekvenser.
# Avläst 2026-11-21 ur iteh:s förhandsvisning av standarden; bara dessa rader var läsbara.
ISO_9613_1_TABELL_1_10C_70: dict[int, float] = {
    -13: 0.0785,
    -12: 0.122,
    -11: 0.186,
    -10: 0.280,
    -3: 1.93,
}


class TestLuftabsorption(unittest.TestCase):
    """Dämpningskoefficienten mot standardernas tabellvärden."""

    def test_iso_9613_2_tabell_2(self) -> None:
        for (temperatur_c, fuktighet), tabell in ISO_9613_2_TABELL_2_DB_KM.items():
            alfa_db_km = 1000.0 * luftabsorption_db_per_m(OKTAVBAND_HZ, temperatur_c, fuktighet)
            for beraknat, facit in zip(alfa_db_km, tabell, strict=True):
                # Tabellen har en decimal under 100 dB/km och heltal däröver. Toleransen är
                # halva sista siffran plus 20 %: ett värde (15 °C, 80 %, 1 kHz) räknas till
                # 4,151 mot tabellens 4,1, alltså 0,051 från tabellvärdet. Det tyder på att
                # tabell 2 är avrundad från ISO 9613-1:s tresiffriga tabell (4,15) och inte
                # från oavrundade värden. Formeln har inte justerats.
                tolerans = 0.6 if facit >= 100.0 else 0.06
                self.assertLessEqual(
                    abs(float(beraknat) - facit),
                    tolerans,
                    msg=f"{temperatur_c} °C, {fuktighet} %: {beraknat:.4f} mot {facit}",
                )

    def test_iso_9613_1_tabell_1(self) -> None:
        for bandindex, facit in ISO_9613_1_TABELL_1_10C_70.items():
            f_hz = 1000.0 * 10.0 ** (bandindex / 10.0)
            alfa_db_km = 1000.0 * float(luftabsorption_db_per_m(f_hz, 10.0, 70.0))
            self.assertAlmostEqual(alfa_db_km / facit, 1.0, delta=0.004)

    def test_form_och_monotoni(self) -> None:
        f_hz = np.logspace(1.3, 4.0, 50)
        alfa = luftabsorption_db_per_m(f_hz, 15.0, 70.0)
        self.assertEqual(alfa.shape, f_hz.shape)
        self.assertTrue(bool(np.all(alfa > 0.0)))
        self.assertTrue(bool(np.all(np.diff(alfa) > 0.0)))
        self.assertEqual(float(luftabsorption_db_per_m(0.0, 15.0, 70.0)), 0.0)

    def test_tryckskalning(self) -> None:
        # ISO 9613-1: vid samma molkoncentration h beror α/p_a bara på f/p_a.
        # Halverat tryck och halverad frekvens med oförändrat h ska ge halva α.
        h_ref = molkoncentration_vattenanga_procent(20.0, 50.0, 101.325)
        h_halv = molkoncentration_vattenanga_procent(20.0, 25.0, 50.6625)
        self.assertAlmostEqual(h_ref, h_halv, places=12)
        alfa_ref = float(luftabsorption_db_per_m(2000.0, 20.0, 50.0, 101.325))
        alfa_halv = float(luftabsorption_db_per_m(1000.0, 20.0, 25.0, 50.6625))
        self.assertAlmostEqual(alfa_halv / alfa_ref, 0.5, places=12)

    def test_ogiltig_indata(self) -> None:
        with self.assertRaises(ValueError):
            luftabsorption_db_per_m(1000.0, 15.0, 120.0)
        with self.assertRaises(ValueError):
            luftabsorption_db_per_m(1000.0, 15.0, 70.0, 0.0)
        with self.assertRaises(ValueError):
            luftabsorption_db_per_m(-1.0, 15.0, 70.0)
        with self.assertRaises(ValueError):
            luftabsorption_db_per_m(1000.0, -300.0, 70.0)

    def test_ljudhastighet(self) -> None:
        self.assertAlmostEqual(ljudhastighet_m_s(20.0), 343.2, places=9)
        self.assertAlmostEqual(ljudhastighet_m_s(0.0), 343.2 * (273.15 / 293.15) ** 0.5, places=9)


if __name__ == "__main__":
    unittest.main()
