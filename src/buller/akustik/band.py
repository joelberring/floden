"""Tersband och A-vägning.

Källor:
- IEC 61260-1:2014, avsnitt 5.2–5.4: exakta mittfrekvenser `f_m = 1000 · G^(x/3)` med
  `G = 10^(3/10)` (bas 10) och bandgränser `f_m · G^(±1/6)`.
- IEC 61672-1:2013, avsnitt 5.5 och bilaga E: analytiskt uttryck för A-vägningen och
  härledning av polfrekvenserna.

Banden är de 27 i `buller.typer.TERSBAND_HZ` (nominellt 25 Hz – 10 kHz), alltså bandindex
x = −16 … 10 räknat från 1 kHz.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike

from buller.typer import ANTAL_BAND, Flyttal

# Bandindex relativt 1 kHz: 25 Hz ↔ −16, 10 kHz ↔ +10.
_FORSTA_BANDINDEX = -16
_REFERENSFREKVENS_HZ = 1000.0


def exakta_mittfrekvenser_hz() -> Flyttal:
    """Exakta tersbandsmittfrekvenser (bas 10), form (27,), hertz.

    IEC 61260-1: `f_m = 1000 · 10^(x/10)` för bandindex x = −16 … 10.
    """
    index = np.arange(_FORSTA_BANDINDEX, _FORSTA_BANDINDEX + ANTAL_BAND, dtype=np.float64)
    resultat: Flyttal = _REFERENSFREKVENS_HZ * np.power(10.0, index / 10.0)
    return resultat


def bandgranser_hz() -> tuple[Flyttal, Flyttal]:
    """Undre och övre bandgräns per tersband, två fält med form (27,), hertz.

    IEC 61260-1: `f_1 = f_m · 10^(−1/20)` och `f_2 = f_m · 10^(+1/20)`.
    """
    mitt_hz = exakta_mittfrekvenser_hz()
    faktor = 10.0 ** (1.0 / 20.0)
    return mitt_hz / faktor, mitt_hz * faktor


def delfrekvenser_hz(antal_per_band: int) -> Flyttal:
    """Frekvenser för medelvärdesbildning inom varje band, form (27, antal_per_band), hertz.

    Bandet delas i `antal_per_band` delband med lika logaritmisk bredd och delbandens
    (geometriska) mittfrekvenser returneras. Med ett udda antal ingår bandets mittfrekvens.
    Detta är ett eget val för numerisk integration över bandet, inte hämtat ur någon standard.
    """
    if antal_per_band < 1:
        raise ValueError("antal_per_band ska vara minst 1.")
    mitt_hz = exakta_mittfrekvenser_hz()
    j = np.arange(antal_per_band, dtype=np.float64)
    exponent = ((j + 0.5) / antal_per_band - 0.5) / 10.0
    resultat: Flyttal = mitt_hz[:, np.newaxis] * np.power(10.0, exponent)[np.newaxis, :]
    return resultat


def _a_vagningens_polfrekvenser_hz() -> tuple[float, float, float, float]:
    """Polfrekvenserna f1–f4 enligt härledningen i IEC 61672-1 bilaga E.

    Avrundade värden i standarden: 20,60 Hz, 107,7 Hz, 737,9 Hz och 12 194 Hz.
    """
    f_r = 1000.0
    f_l = 10.0**1.5
    f_h = 10.0**3.9
    f_a = 10.0**2.45
    d2 = 0.5  # D^2 = 1/2
    d = math.sqrt(d2)
    c = f_l**2 * f_h**2
    b = (f_r**2 + c / f_r**2 - d * (f_l**2 + f_h**2)) / (1.0 - d)
    rot = math.sqrt(b * b - 4.0 * c)
    f1 = math.sqrt((-b - rot) / 2.0)
    f4 = math.sqrt((-b + rot) / 2.0)
    f2 = (3.0 - math.sqrt(5.0)) / 2.0 * f_a
    f3 = (3.0 + math.sqrt(5.0)) / 2.0 * f_a
    return f1, f2, f3, f4


def _a_onormerad_db(frekvens_hz: Flyttal) -> Flyttal:
    f1, f2, f3, f4 = _a_vagningens_polfrekvenser_hz()
    f2kv = np.square(frekvens_hz)
    overforing = (f4**2 * np.square(f2kv)) / (
        (f2kv + f1**2) * np.sqrt(f2kv + f2**2) * np.sqrt(f2kv + f3**2) * (f2kv + f4**2)
    )
    resultat: Flyttal = 20.0 * np.log10(overforing)
    return resultat


def a_vagning_vid_frekvens_db(frekvens_hz: ArrayLike) -> Flyttal:
    """A-vägning i dB vid godtyckliga frekvenser (> 0 Hz), samma form som indata.

    IEC 61672-1 ekv. (6):
    `A(f) = 20·lg[f4²·f⁴ / ((f² + f1²)·√(f² + f2²)·√(f² + f3²)·(f² + f4²))] − A_1000`.
    Standarden anger normeringskonstanten avrundad till `A_1000 = −2,000 dB`. Här räknas
    den ut exakt (≈ −1,9997 dB) så att vägningen blir exakt 0 dB vid 1 kHz; skillnaden mot
    den avrundade konstanten är mindre än 0,001 dB.
    """
    f = np.asarray(frekvens_hz, dtype=np.float64)
    if not bool(np.all(f > 0.0)):
        raise ValueError("A-vägningen är bara definierad för frekvenser större än 0 Hz.")
    a_1000 = _a_onormerad_db(np.asarray(_REFERENSFREKVENS_HZ, dtype=np.float64))
    resultat: Flyttal = _a_onormerad_db(f) - a_1000
    return resultat


def a_vagning_db() -> Flyttal:
    """A-vägning per tersband, form (27,), decibel.

    Den analytiska formeln i IEC 61672-1 utvärderad på de exakta mittfrekvenserna
    (`exakta_mittfrekvenser_hz`), på samma sätt som standardens tabell 3 är framräknad.
    Värdena är inte avrundade till 0,1 dB.
    """
    return a_vagning_vid_frekvens_db(exakta_mittfrekvenser_hz())
