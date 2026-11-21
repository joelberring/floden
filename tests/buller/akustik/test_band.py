"""Tester för tersband och A-vägning (B4a)."""

from __future__ import annotations

import unittest

import numpy as np

from buller.akustik.band import (
    _a_vagningens_polfrekvenser_hz,
    a_vagning_db,
    a_vagning_vid_frekvens_db,
    bandgranser_hz,
    delfrekvenser_hz,
    exakta_mittfrekvenser_hz,
)
from buller.typer import ANTAL_BAND, TERSBAND_HZ

# IEC 61672-1:2013 tabell 3, A-vägning vid nominella tersbandsfrekvenser 25 Hz – 10 kHz (dB).
# Avläst via pyfar (pyfar.constants.frequency_weighting, som anger tabell 3 som källa).
A_TABELL_DB: tuple[float, ...] = (
    -44.7, -39.4, -34.6, -30.2, -26.2, -22.5, -19.1, -16.1, -13.4, -10.9, -8.6, -6.6, -4.8,
    -3.2, -1.9, -0.8, 0.0, 0.6, 1.0, 1.2, 1.3, 1.2, 1.0, 0.5, -0.1, -1.1, -2.5,
)  # fmt: skip


class TestTersband(unittest.TestCase):
    """Exakta mittfrekvenser och bandgränser enligt IEC 61260-1 (bas 10)."""

    def test_form_och_nominella_frekvenser(self) -> None:
        mitt_hz = exakta_mittfrekvenser_hz()
        self.assertEqual(mitt_hz.shape, (ANTAL_BAND,))
        # Nominella värden avviker högst ca 1 % från de exakta.
        np.testing.assert_allclose(mitt_hz, np.array(TERSBAND_HZ), rtol=0.012)

    def test_exakta_varden(self) -> None:
        mitt_hz = exakta_mittfrekvenser_hz()
        self.assertAlmostEqual(float(mitt_hz[16]), 1000.0, places=9)
        self.assertAlmostEqual(float(mitt_hz[0]), 10.0**1.4, places=9)
        self.assertAlmostEqual(float(mitt_hz[6]), 100.0, places=9)
        self.assertAlmostEqual(float(mitt_hz[26]), 10000.0, places=6)

    def test_bandgranser_angransar_varandra(self) -> None:
        undre_hz, ovre_hz = bandgranser_hz()
        mitt_hz = exakta_mittfrekvenser_hz()
        np.testing.assert_allclose(ovre_hz[:-1], undre_hz[1:], rtol=1e-12)
        np.testing.assert_allclose(np.sqrt(undre_hz * ovre_hz), mitt_hz, rtol=1e-12)
        np.testing.assert_allclose(ovre_hz / undre_hz, 10.0**0.1, rtol=1e-12)

    def test_delfrekvenser_ligger_i_bandet(self) -> None:
        undre_hz, ovre_hz = bandgranser_hz()
        for antal in (1, 4, 7):
            f = delfrekvenser_hz(antal)
            self.assertEqual(f.shape, (ANTAL_BAND, antal))
            self.assertTrue(bool(np.all(f > undre_hz[:, None])))
            self.assertTrue(bool(np.all(f < ovre_hz[:, None])))
            self.assertTrue(bool(np.all(np.diff(f, axis=1) > 0.0)))
        np.testing.assert_allclose(
            delfrekvenser_hz(7)[:, 3], exakta_mittfrekvenser_hz(), rtol=1e-12
        )
        with self.assertRaises(ValueError):
            delfrekvenser_hz(0)


class TestAVagning(unittest.TestCase):
    """A-vägning enligt den analytiska formeln i IEC 61672-1."""

    def test_form_och_noll_vid_1_khz(self) -> None:
        a_db = a_vagning_db()
        self.assertEqual(a_db.shape, (ANTAL_BAND,))
        self.assertEqual(float(a_db[16]), 0.0)

    def test_mot_standardens_tabell(self) -> None:
        # Tabellen är avrundad till 0,1 dB: största tillåtna avvikelse 0,05 dB (+ marginal).
        avvikelse_db = np.abs(a_vagning_db() - np.array(A_TABELL_DB))
        self.assertLess(float(avvikelse_db.max()), 0.0501)

    def test_kanda_varden(self) -> None:
        a_db = a_vagning_db()
        self.assertAlmostEqual(float(a_db[6]), -19.1, delta=0.05)  # 100 Hz
        self.assertAlmostEqual(float(a_db[0]), -44.7, delta=0.05)  # 25 Hz
        self.assertAlmostEqual(float(a_db[20]), 1.3, delta=0.05)  # 2,5 kHz
        self.assertAlmostEqual(float(a_db[26]), -2.5, delta=0.05)  # 10 kHz

    def test_polfrekvenser(self) -> None:
        # Avrundade värden i IEC 61672-1: 20,60, 107,7, 737,9 och 12194 Hz. De långa
        # decimalvärdena är pyfars konstanter (hänvisar till bilaga E i standarden).
        f1, f2, f3, f4 = _a_vagningens_polfrekvenser_hz()
        self.assertAlmostEqual(f1, 20.598997057618316, places=9)
        self.assertAlmostEqual(f2, 107.65264864304629, places=9)
        self.assertAlmostEqual(f3, 737.8622307362901, places=9)
        self.assertAlmostEqual(f4, 12194.217147998012, places=7)

    def test_normeringskonstant_nara_minus_2_db(self) -> None:
        # Standardens avrundade konstant A_1000 = −2,000 dB.
        from buller.akustik.band import _a_onormerad_db

        a_1000 = float(_a_onormerad_db(np.asarray(1000.0)))
        self.assertAlmostEqual(a_1000, -2.0, delta=0.001)

    def test_godtycklig_frekvens_och_fel(self) -> None:
        np.testing.assert_array_equal(
            a_vagning_vid_frekvens_db(exakta_mittfrekvenser_hz()), a_vagning_db()
        )
        with self.assertRaises(ValueError):
            a_vagning_vid_frekvens_db([0.0, 100.0])


if __name__ == "__main__":
    unittest.main()
