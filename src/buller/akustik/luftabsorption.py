"""Luftens absorption av ljud enligt ISO 9613-1:1993.

Källa: ISO 9613-1:1993, "Acoustics – Attenuation of sound during propagation outdoors –
Part 1: Calculation of the absorption of sound by the atmosphere", ekv. (3)–(5) samt
bilaga B (omräkning från relativ fuktighet till molkoncentration vattenånga).
Kontrollvärden: ISO 9613-2:1996 tabell 2 (se testerna).

Standardens angivna giltighetsområde för formlerna (avsnitt 7): −20 … +50 °C,
molkoncentration vattenånga 0,05 … 5 %, lufttryck under 200 kPa, och kvoten frekvens/tryck
4·10⁻⁴ … 10 Hz/Pa. Funktionerna räknar även utanför området men då utan standardens
noggrannhetsangivelse.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike

from buller.typer import Flyttal

REFERENSTRYCK_KPA = 101.325  # p_r, en standardatmosfär
REFERENSTEMPERATUR_K = 293.15  # T_0 = 20 °C
TRIPPELPUNKT_K = 273.16  # T_01, vattnets trippelpunkt
NOLL_CELSIUS_K = 273.15


def ljudhastighet_m_s(temperatur_c: float) -> float:
    """Ljudhastighet i luft, m/s: `c = 343,2 · √(T/T_0)` med `T_0 = 293,15 K`.

    Idealgassambandet c ∝ √T med 343,2 m/s vid 20 °C (det värde ISO 9613-1 använder).
    Fuktens inverkan på ljudhastigheten (några tiondels procent) försummas.
    """
    temperatur_k = temperatur_c + NOLL_CELSIUS_K
    if temperatur_k <= 0.0:
        raise ValueError("Temperaturen ligger under absoluta nollpunkten.")
    return 343.2 * math.sqrt(temperatur_k / REFERENSTEMPERATUR_K)


def molkoncentration_vattenanga_procent(
    temperatur_c: float, relativ_fuktighet_procent: float, lufttryck_kpa: float = REFERENSTRYCK_KPA
) -> float:
    """Molkoncentration vattenånga h i procent, ISO 9613-1 bilaga B.

    `h = h_r · (p_sat/p_r) / (p_a/p_r)` med `p_sat/p_r = 10^C` och
    `C = −6,8346 · (T_01/T)^1,261 + 4,6151`.
    """
    temperatur_k = temperatur_c + NOLL_CELSIUS_K
    exponent = -6.8346 * (TRIPPELPUNKT_K / temperatur_k) ** 1.261 + 4.6151
    mattnadstryck_rel = 10.0**exponent
    h = relativ_fuktighet_procent * mattnadstryck_rel / (lufttryck_kpa / REFERENSTRYCK_KPA)
    return float(h)


def luftabsorption_db_per_m(
    frekvens_hz: ArrayLike,
    temperatur_c: float,
    relativ_fuktighet_procent: float,
    lufttryck_kpa: float = REFERENSTRYCK_KPA,
) -> Flyttal:
    """Dämpningskoefficient α för ren ton i dB/m, samma form som `frekvens_hz`.

    ISO 9613-1 ekv. (3)–(5):

    - syrets relaxationsfrekvens
      `f_rO = (p_a/p_r)·(24 + 4,04·10⁴·h·(0,02 + h)/(0,391 + h))`
    - kvävets relaxationsfrekvens
      `f_rN = (p_a/p_r)·(T/T_0)^(−1/2)·(9 + 280·h·exp(−4,170·((T/T_0)^(−1/3) − 1)))`
    - `α = 8,686·f²·{1,84·10⁻¹¹·(p_a/p_r)⁻¹·(T/T_0)^(1/2) + (T/T_0)^(−5/2)·
      [0,01275·exp(−2239,1/T)/(f_rO + f²/f_rO) + 0,1068·exp(−3352,0/T)/(f_rN + f²/f_rN)]}`

    Kastar `ValueError` för fysikaliskt omöjlig indata (temperatur ≤ 0 K, tryck ≤ 0,
    fuktighet utanför 0–100 %, negativ frekvens).
    """
    f = np.asarray(frekvens_hz, dtype=np.float64)
    temperatur_k = temperatur_c + NOLL_CELSIUS_K
    if not math.isfinite(temperatur_k) or temperatur_k <= 0.0:
        raise ValueError("Temperaturen ska vara ändlig och över absoluta nollpunkten.")
    if not math.isfinite(lufttryck_kpa) or lufttryck_kpa <= 0.0:
        raise ValueError("Lufttrycket ska vara större än 0 kPa.")
    if not 0.0 <= relativ_fuktighet_procent <= 100.0:
        raise ValueError("Relativ fuktighet ska ligga i 0–100 %.")
    if not bool(np.all(f >= 0.0)):
        raise ValueError("Frekvensen får inte vara negativ.")

    tryck_rel = lufttryck_kpa / REFERENSTRYCK_KPA
    temp_rel = temperatur_k / REFERENSTEMPERATUR_K
    h = molkoncentration_vattenanga_procent(temperatur_c, relativ_fuktighet_procent, lufttryck_kpa)

    f_ro = tryck_rel * (24.0 + 4.04e4 * h * (0.02 + h) / (0.391 + h))
    f_rn = (
        tryck_rel
        * temp_rel ** (-0.5)
        * (9.0 + 280.0 * h * math.exp(-4.170 * (temp_rel ** (-1.0 / 3.0) - 1.0)))
    )
    f2 = np.square(f)
    klassisk = 1.84e-11 / tryck_rel * math.sqrt(temp_rel)
    syre = 0.01275 * math.exp(-2239.1 / temperatur_k) / (f_ro + f2 / f_ro)
    kvave = 0.1068 * math.exp(-3352.0 / temperatur_k) / (f_rn + f2 / f_rn)
    resultat: Flyttal = 8.686 * f2 * (klassisk + temp_rel ** (-2.5) * (syre + kvave))
    return resultat
