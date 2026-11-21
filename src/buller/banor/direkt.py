"""Direkta banor: vilka (mottagare, källsegment) som ligger inom en maxradie."""

from __future__ import annotations

import math

import numpy as np

from buller.scen.bullerscen import Heltal
from buller.typer import BullerFel, Flyttal

# Största antal avstånd som räknas åt gången (begränsar minnet).
_MAX_PAR_PER_DEL = 4_000_000


def par_inom_radie(
    mottagare_e_m: Flyttal,
    mottagare_n_m: Flyttal,
    kalla_e_m: Flyttal,
    kalla_n_m: Flyttal,
    maxradie_m: float,
) -> tuple[Heltal, Heltal]:
    """Index (mottagare, källa) för alla par med planavstånd ≤ `maxradie_m`.

    Paren är sorterade på mottagarindex och därefter källindex.
    """
    if not (math.isfinite(maxradie_m) and maxradie_m > 0.0):
        raise BullerFel(f"Maxradien ska vara > 0 m, fick {maxradie_m}.")
    antal_m = int(mottagare_e_m.shape[0])
    antal_k = int(kalla_e_m.shape[0])
    if antal_m == 0 or antal_k == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    steg = max(1, _MAX_PAR_PER_DEL // antal_k)
    m_delar: list[Heltal] = []
    k_delar: list[Heltal] = []
    for start in range(0, antal_m, steg):
        slut = min(antal_m, start + steg)
        avstand2 = (mottagare_e_m[start:slut, None] - kalla_e_m[None, :]) ** 2 + (
            mottagare_n_m[start:slut, None] - kalla_n_m[None, :]
        ) ** 2
        mi, ki = np.nonzero(avstand2 <= maxradie_m**2)
        m_delar.append(mi.astype(np.int64) + start)
        k_delar.append(ki.astype(np.int64))
    return np.concatenate(m_delar), np.concatenate(k_delar)
