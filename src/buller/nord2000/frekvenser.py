"""Tersbandsfrekvenser, ljudhastighet och små hjälpfunktioner i Nord2000.

Källa: DELTA AV 1106/07 (rev. 2014), "Proposal for Nordtest Method: Nord2000 – Prediction of
Outdoor Sound Propagation", avsnitt 5.4.3 (ekv. 11), 5.4.4 (ekv. 13–14), 5.23.1 (ekv. 335),
5.23.3 (ekv. 337) och 5.23.8 (ekv. 354).
"""

from __future__ import annotations

import math

import numpy as np

from buller.typer import ANTAL_BAND, Flyttal


def mittfrekvenser_hz() -> Flyttal:
    """Tersbandens mittfrekvenser enligt AV 1106/07 avsnitt 5.4.3, ekv. (11).

    Band nr 1–27 har f = 10^((nr + 13)/10) Hz, alltså 25,12 Hz – 10 kHz. Det är dessa exakta
    frekvenser (inte de nominella i `buller.typer.TERSBAND_HZ`) som metoden räknar med.
    """
    bandnummer = np.arange(1, ANTAL_BAND + 1, dtype=np.float64)
    frekvenser: Flyttal = 10.0 ** ((bandnummer + 13.0) / 10.0)
    return frekvenser


def ljudhastighet_m_s(temperatur_c: float) -> float:
    """Funktionen Coft: ljudhastighet i torr luft, AV 1106/07 avsnitt 5.23.1, ekv. (335)."""
    return 20.05 * math.sqrt(temperatur_c + 273.15)


def vaglangd_m(frekvens_hz: float, ljudhastighet_mark_m_s: float) -> float:
    """Våglängd vid marken, AV 1106/07 avsnitt 5.4.4, ekv. (13)."""
    return ljudhastighet_mark_m_s / frekvens_hz


def vagtal_per_m(frekvens_hz: float, ljudhastighet_mark_m_s: float) -> float:
    """Vågtal vid marken, AV 1106/07 avsnitt 5.4.4, ekv. (14)."""
    return 2.0 * math.pi * frekvens_hz / ljudhastighet_mark_m_s


def exp_modifierad(x: float) -> float:
    """Funktionen Exp': exponentialfunktion som når noll, AV 1106/07 avsnitt 5.23.3, ekv. (337)."""
    if x >= -1.0:
        return math.exp(x)
    if x > -2.0:
        return (2.0 + x) * math.exp(-1.0)
    return 0.0


def heaviside(x: float) -> float:
    """Funktionen H: stegfunktion med H(0) = 1, AV 1106/07 avsnitt 5.23.8, ekv. (354)."""
    return 1.0 if x >= 0.0 else 0.0
