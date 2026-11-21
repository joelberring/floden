"""Reflektion av ljud från punktkälla mot ett plant, lokalt reagerande impedansplan.

Källor (Weyl–van der Pol-lösningen):
- C. F. Chien & W. W. Soroka, "Sound propagation along an impedance plane",
  Journal of Sound and Vibration 43 (1975) 9–20.
- K. Attenborough, K. M. Li & K. Horoshenkov, "Predicting Outdoor Sound" (2007), kap. 2.
- E. M. Salomons, "Computational Atmospheric Acoustics" (2001), bilaga D.

Tidskonvention exp(−iωt) (samma som `impedans.py`): direkt våg exp(ik·R₁)/R₁.

Med infallsvinkeln θ mätt från ytnormalen, β = normaliserad admittans, R₂ = sträckan
spegelkälla–mottagare:

    R_p  = (cos θ − β)/(cos θ + β)                (planvågsreflektion)
    w    = ((1 + i)/2)·√(k·R₂)·(cos θ + β)        (numeriskt avstånd, w² = i·k·R₂/2·(cos θ + β)²)
    F(w) = 1 + i·√π·w·exp(−w²)·erfc(−i·w)         (randförlustfaktor)
    Q    = R_p + (1 − R_p)·F(w)                   (sfärisk reflektionskoefficient)

Gränsfall: F → 1 när |w| → 0 och F ≈ −1/(2w²) → 0 när |w| → ∞ (då Q → R_p).
Uttrycket förutsätter |β| ≪ 1 eller strykande infall och k·R₂ ≫ 1; det är den gängse
närmeformen, inte en exakt lösning.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike, NDArray

from buller.akustik.faddeeva import faddeeva
from buller.akustik.impedans import delany_bazley_admittans
from buller.typer import Flyttal

Komplextal = NDArray[np.complex128]

_ROT_PI = math.sqrt(math.pi)


def planvagsreflektion(cos_theta: ArrayLike, admittans: ArrayLike) -> Komplextal:
    """Planvågsreflektionskoefficient R_p = (cos θ − β)/(cos θ + β).

    Där både cos θ och β är noll (strykande infall över hård yta) sätts R_p = 1, vilket är
    gränsvärdet när β → 0 först.
    """
    c = np.asarray(cos_theta, dtype=np.float64)
    beta = np.asarray(admittans, dtype=np.complex128)
    namnare = c + beta
    noll = namnare == 0.0
    r_p: Komplextal = np.where(noll, 1.0 + 0.0j, (c - beta) / np.where(noll, 1.0, namnare))
    return r_p


def numeriskt_avstand(
    vagtal_rad_m: ArrayLike, r2_m: ArrayLike, cos_theta: ArrayLike, admittans: ArrayLike
) -> Komplextal:
    """Numeriskt avstånd w = ((1 + i)/2)·√(k·R₂)·(cos θ + β), dimensionslöst."""
    k = np.asarray(vagtal_rad_m, dtype=np.float64)
    r2 = np.asarray(r2_m, dtype=np.float64)
    c = np.asarray(cos_theta, dtype=np.float64)
    beta = np.asarray(admittans, dtype=np.complex128)
    w: Komplextal = (0.5 + 0.5j) * np.sqrt(k * r2) * (c + beta)
    return w


def randforlustfaktor(w: ArrayLike) -> Komplextal:
    """Randförlustfaktorn F(w) = 1 + i·√π·w·w_F(w), där w_F är Faddeevafunktionen.

    För stora |w| är F ≈ −1/(2w²); uttrycket förlorar då relativ (men inte absolut)
    noggrannhet genom utsläckning, ungefär maskinepsilon·2|w|².
    """
    ww = np.asarray(w, dtype=np.complex128)
    f: Komplextal = 1.0 + 1j * _ROT_PI * ww * faddeeva(ww)
    return f


def sfarisk_reflektion(
    vagtal_rad_m: ArrayLike, r2_m: ArrayLike, cos_theta: ArrayLike, admittans: ArrayLike
) -> Komplextal:
    """Sfärisk reflektionskoefficient Q = R_p + (1 − R_p)·F(w). Indata broadcastas."""
    r_p = planvagsreflektion(cos_theta, admittans)
    w = numeriskt_avstand(vagtal_rad_m, r2_m, cos_theta, admittans)
    q: Komplextal = r_p + (1.0 - r_p) * randforlustfaktor(w)
    return q


def tvastralefaktor(
    frekvens_hz: ArrayLike,
    kalla_hojd_m: float,
    mottagare_hojd_m: float,
    avstand_m: float,
    flodesresistivitet_kNsm4: float,
    ljudhastighet_m_s: float,
) -> Flyttal:
    """Kvoten |p|²/|p_fritt|² för punktkälla över plan mark vid rena toner.

    `|1 + Q·(R₁/R₂)·exp(i·k·(R₂ − R₁))|²` där R₁ är direkt sträcka och R₂ sträckan via
    spegelkällan (koherent tvåstrålesumma, t.ex. Attenborough m.fl. 2007 ekv. 2.40).
    `avstand_m` är det horisontella avståndet och höjderna mäts vinkelrätt mot planet.
    Samma form som `frekvens_hz`. 10·lg av värdet är markeffekten i dB relativt fritt fält.
    """
    if avstand_m <= 0.0 or kalla_hojd_m < 0.0 or mottagare_hojd_m < 0.0:
        raise ValueError("Avståndet ska vara positivt och höjderna icke-negativa.")
    f = np.asarray(frekvens_hz, dtype=np.float64)
    k = 2.0 * math.pi * f / ljudhastighet_m_s
    r1 = math.hypot(avstand_m, kalla_hojd_m - mottagare_hojd_m)
    r2 = math.hypot(avstand_m, kalla_hojd_m + mottagare_hojd_m)
    cos_theta = (kalla_hojd_m + mottagare_hojd_m) / r2
    beta = delany_bazley_admittans(f, flodesresistivitet_kNsm4)
    q = sfarisk_reflektion(k, r2, cos_theta, beta)
    falt = 1.0 + q * (r1 / r2) * np.exp(1j * k * (r2 - r1))
    resultat: Flyttal = np.square(np.abs(falt))
    return resultat
