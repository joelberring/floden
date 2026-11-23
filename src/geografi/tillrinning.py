# ruff: noqa: E501
"""Tillrinningsområdet uppströms en kärna, ur höjdmodellen (D8 med fyllning av sänkor).

Priority-flood (Barnes m.fl. 2014): alla celler dräneras mot rutans kant, sänkor fylls till sin
utloppsnivå, och varje cell får en mottagare. En cell hör till kärnans tillrinningsområde om den själv
eller dess mottagarkedja når en kärncell. Det är en skiss för att välja en buffert (inte en
hydrologisk karta): sänkor i kärnan fylls och rinner ut åt sitt lägsta utlopp, och celler utan höjd
behandlas som höga. Når tillrinningsområdet rutans kant är bufferten för liten (`nar_kanten`).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

_GRANNAR = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


@dataclass(frozen=True)
class Tillrinning:
    maske: NDArray[np.bool_]
    nar_kanten: bool
    antal_celler: int


def nedsampla(z: NDArray[np.float64], faktor: int) -> NDArray[np.float64]:
    """Blockmedelvärde (ignorerar NaN); ett block utan värden blir NaN. Kanten som inte fyller ett block hoppas över."""
    if faktor <= 1:
        return z
    h, b = (z.shape[0] // faktor) * faktor, (z.shape[1] // faktor) * faktor
    blk = z[:h, :b].reshape(h // faktor, faktor, b // faktor, faktor)
    ok = np.isfinite(blk)
    s = np.where(ok, blk, 0.0).sum(axis=(1, 3))
    n = ok.sum(axis=(1, 3))
    return np.where(n > 0, s / np.maximum(n, 1), np.nan)


def tillrinning_uppstroms(z: NDArray[np.float64], karna: NDArray[np.bool_]) -> Tillrinning:
    """Cellerna vars vatten rinner till kärnan (eller till en sänka som kärnan ligger i)."""
    from scipy import ndimage

    h, b = z.shape
    if karna.shape != z.shape or not karna.any():
        raise ValueError("Kärnan ska vara en icke-tom mask i samma form som höjdmodellen.")
    hog = np.where(np.isfinite(z), z, 1.0e9)
    zf = hog.ravel()
    # 1. Priority-flood med liten lutning i sänkorna (Barnes m.fl. 2014): zz är terrängen med sänkor fyllda
    #    så att varje cell har en strikt lägre granne på vägen mot kanten. `ordning` är stigande zz.
    zz = zf.copy()
    sedd = np.zeros(h * b, dtype=bool)
    ko: list[tuple[float, int]] = []
    for r in range(h):
        for c in ((0, b - 1) if 0 < r < h - 1 else range(b)):
            i = r * b + c
            if not sedd[i]:
                sedd[i] = True
                heapq.heappush(ko, (float(zf[i]), i))
    ordning: list[int] = []
    while ko:
        niva, i = heapq.heappop(ko)
        ordning.append(i)
        r, c = divmod(i, b)
        for dr, dc in _GRANNAR:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < b:
                j = rr * b + cc
                if not sedd[j]:
                    sedd[j] = True
                    zz[j] = max(float(zf[j]), float(np.nextafter(niva, np.inf)) if zf[j] <= niva else float(zf[j]))
                    heapq.heappush(ko, (zz[j], j))
    zg = zz.reshape(h, b)
    # 2. Brantaste nedåt (D8) på den fyllda terrängen. Randcellerna är utlopp.
    bast = np.zeros((h, b))
    mot = np.full((h, b), -1, dtype=np.int64)
    for k, (dr, dc) in enumerate(_GRANNAR):
        avst = float(np.hypot(dr, dc))
        gran = np.full((h, b), np.inf)
        r0, r1 = max(0, -dr), h - max(0, dr)
        c0, c1 = max(0, -dc), b - max(0, dc)
        gran[r0:r1, c0:c1] = zg[r0 + dr : r1 + dr, c0 + dc : c1 + dc]
        lutning = (zg - gran) / avst
        battre = lutning > bast
        bast = np.where(battre, lutning, bast)
        mot = np.where(battre, k, mot)
    rr_, cc_ = np.mgrid[0:h, 0:b]
    dr_arr = np.array([g[0] for g in _GRANNAR])
    dc_arr = np.array([g[1] for g in _GRANNAR])
    ned = np.where(mot >= 0, (rr_ + dr_arr[np.maximum(mot, 0)]) * b + (cc_ + dc_arr[np.maximum(mot, 0)]), -1).ravel()
    # 3. Frön: kärnan och sänkorna (fyllda celler) som kärnan ligger i. Sedan uppströms: en cell är med om
    #    cellen nedströms är det (stigande zz ger nedströms först).
    sank = (zg > hog + 1e-9)
    delar, _ = ndimage.label(sank, structure=np.ones((3, 3)))
    med_karna = np.unique(delar[karna & sank])
    froen = karna | (np.isin(delar, med_karna[med_karna > 0]) & sank)
    fro = froen.ravel()
    i_fang = np.zeros(h * b, dtype=bool)
    for i in ordning:
        n = ned[i]
        i_fang[i] = bool(fro[i]) or (n >= 0 and bool(i_fang[n]))
    maske = i_fang.reshape(h, b)
    # Randcellerna är utlopp och ingår aldrig själva; "når kanten" betyder att något når cellen innanför.
    kant = np.zeros_like(maske)
    kant[1, :] = kant[-2, :] = True
    kant[:, 1] = kant[:, -2] = True
    return Tillrinning(maske, bool((maske & kant & ~karna).any()), int(maske.sum()))
