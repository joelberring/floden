"""Komplexa felfunktionen (Faddeevafunktionen) w(z) = exp(−z²)·erfc(−i·z) i ren numpy.

Källa: J. A. C. Weideman, "Computation of the complex error function",
SIAM Journal on Numerical Analysis 31 (1994) 1497–1518. Algoritmen är en rationell
utveckling

    w(z) ≈ 1/(√π·(L − i·z)) + 2/(L − i·z)² · Σ_{n=0}^{N−1} a_{n+1}·Z^n,   Z = (L + i·z)/(L − i·z),

med `L = √(N/√2)` och koefficienter `a_n` som fås ur en diskret Fouriertransform. Den
gäller för Im z ≥ 0. För Im z < 0 används symmetrin `w(z) = 2·exp(−z²) − w(−z)`
(Abramowitz & Stegun 7.1.11).

Uppmätt noggrannhet mot `scipy.special.wofz` redovisas i `docs/buller/logg/B4a.md` och
bevakas av `tests/buller/akustik/test_faddeeva.py`.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike, NDArray

from buller.typer import Flyttal

Komplextal = NDArray[np.complex128]

ANTAL_TERMER = 40  # N i Weidemans algoritm


def _weideman_koefficienter(antal_termer: int) -> tuple[float, Flyttal]:
    """Returnerar L och polynomkoefficienterna (högsta grad först), Weideman (1994) avsnitt 5."""
    n = antal_termer
    m = 2 * n
    m2 = 2 * m
    k = np.arange(-m + 1, m, dtype=np.float64)
    langd = math.sqrt(n / math.sqrt(2.0))
    theta = k * math.pi / m
    t = langd * np.tan(theta / 2.0)
    f = np.exp(-np.square(t)) * (langd**2 + np.square(t))
    f = np.concatenate((np.zeros(1), f))
    a = np.real(np.fft.fft(np.fft.fftshift(f))) / m2
    koefficienter: Flyttal = np.ascontiguousarray(a[1 : n + 1][::-1], dtype=np.float64)
    return langd, koefficienter


_L, _KOEFFICIENTER = _weideman_koefficienter(ANTAL_TERMER)
_INV_ROT_PI = 1.0 / math.sqrt(math.pi)


def _faddeeva_ovre(z: Komplextal) -> Komplextal:
    """Weidemans utveckling, giltig för Im z ≥ 0."""
    namnare = _L - 1j * z
    kvot = (_L + 1j * z) / namnare
    p = np.full(z.shape, _KOEFFICIENTER[0], dtype=np.complex128)
    for koefficient in _KOEFFICIENTER[1:]:
        p = p * kvot + koefficient
    resultat: Komplextal = 2.0 * p / np.square(namnare) + _INV_ROT_PI / namnare
    return resultat


def faddeeva(z: ArrayLike) -> Komplextal:
    """Faddeevafunktionen w(z) för komplexa z, samma form som indata.

    I nedre halvplanet växer |w(z)| som exp(Im(z)² − Re(z)²) och kan ge overflow för
    stora negativa imaginärdelar; det är funktionens egen egenskap, inte ett fel i metoden.
    """
    zz = np.asarray(z, dtype=np.complex128)
    nedre = zz.imag < 0.0
    if not bool(np.any(nedre)):
        return _faddeeva_ovre(zz)
    speglad = np.where(nedre, -zz, zz)
    w = _faddeeva_ovre(speglad)
    # Exponenten nollställs i övre halvplanet så att np.exp inte ger overflow i onödan.
    exponent = np.where(nedre, -np.square(zz), 0.0)
    resultat: Komplextal = np.where(nedre, 2.0 * np.exp(exponent) - w, w)
    return resultat
