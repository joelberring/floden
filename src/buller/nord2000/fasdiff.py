"""Hjälpfunktionen PhaseDiffFreq i Nord2000.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.23.14, ekv. (378)–(381).
"""

from __future__ import annotations

import cmath
import math
from collections.abc import Sequence

from buller.nord2000.mark import planvagsreflektion
from buller.typer import BullerFel, Flyttal

_HOGSTA_FREKVENS_HZ = 100_000.0  # avsnitt 5.23.14: över 100 kHz används värdet vid 100 kHz


def fasskillnad_rad(
    frekvens_hz: float,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    impedans: complex,
    ljudhastighet_mark_m_s: float,
) -> float:
    """Fasskillnaden Δα(f) mellan direkt och markreflekterat ljud, AV 1106/07 ekv. (379)."""
    summa = kallhojd_m + mottagarhojd_m
    r2 = math.sqrt(avstand_m**2 + summa**2)
    delta_r = r2 - math.sqrt(avstand_m**2 + (kallhojd_m - mottagarhojd_m) ** 2)
    psi = math.asin(summa / r2)
    return 2.0 * math.pi * frekvens_hz / ljudhastighet_mark_m_s * delta_r + cmath.phase(
        planvagsreflektion(impedans, psi)
    )


def fasdifferensfrekvens_hz(
    frekvenser_hz: Flyttal,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    impedanser: Sequence[complex],
    ljudhastighet_mark_m_s: float,
    fasskillnad_sokt_rad: float,
) -> float:
    """Funktionen PhaseDiffFreq, AV 1106/07 avsnitt 5.23.14, ekv. (378), (380)–(381).

    Δα räknas vid tersbandens mittfrekvenser (`frekvenser_hz`, med markimpedansen
    `impedanser[i]` vid frekvens nr i). Frekvensen där Δα når det sökta värdet fås genom
    interpolation i logaritmisk frekvens mellan de två grannband som omsluter värdet (ekv. 380).
    Under Δα(25 Hz) används ekv. (381); över Δα(10 kHz) linjär extrapolation ur de två högsta
    banden, dock högst 100 kHz. Tolkningarna (första omslutande bandpar, extrapolation linjär
    i frekvens) står i `docs/buller/tolkningar.md`.
    """
    n = frekvenser_hz.shape[0]
    if n < 2 or len(impedanser) != n:
        raise BullerFel("PhaseDiffFreq kräver en impedans per frekvens och minst två frekvenser.")
    alfa = [
        fasskillnad_rad(
            float(frekvenser_hz[i]),
            avstand_m,
            kallhojd_m,
            mottagarhojd_m,
            impedanser[i],
            ljudhastighet_mark_m_s,
        )
        for i in range(n)
    ]
    if fasskillnad_sokt_rad < alfa[0]:
        if alfa[0] <= 0.0:
            raise BullerFel("Δα(25 Hz) ≤ 0: AV 1106/07 ekv. (381) är inte definierad.")
        return float(float(frekvenser_hz[0]) ** (fasskillnad_sokt_rad / alfa[0]))
    for i in range(n - 1):
        if alfa[i] <= fasskillnad_sokt_rad <= alfa[i + 1] and alfa[i + 1] > alfa[i]:
            lg1 = math.log10(float(frekvenser_hz[i]))
            lg2 = math.log10(float(frekvenser_hz[i + 1]))
            andel = (fasskillnad_sokt_rad - alfa[i]) / (alfa[i + 1] - alfa[i])
            return float(10.0 ** (andel * (lg2 - lg1) + lg1))
    f1 = float(frekvenser_hz[n - 2])
    f2 = float(frekvenser_hz[n - 1])
    if alfa[n - 1] <= alfa[n - 2]:
        raise BullerFel("Δα växer inte mellan de två högsta banden; extrapolation omöjlig.")
    f = f2 + (fasskillnad_sokt_rad - alfa[n - 1]) * (f2 - f1) / (alfa[n - 1] - alfa[n - 2])
    return float(min(f, _HOGSTA_FREKVENS_HZ))
