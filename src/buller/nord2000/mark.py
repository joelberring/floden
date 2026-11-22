"""Markens akustiska egenskaper i Nord2000: impedans och reflektionskoefficienter.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.6.2–5.6.5, ekv. (57)–(77).

Tidskonvention: metoden skriver fasfaktorn för den reflekterade strålen som exp(+j·2πf·Δτ)
(ekv. 120) och markimpedansen med positiv imaginärdel (ekv. 57). Samma konvention används här.
"""

from __future__ import annotations

import cmath
import math

import numpy as np

from buller.akustik.faddeeva import faddeeva
from buller.typer import BullerFel

_ROT_PI = math.sqrt(math.pi)
_H_STEG = 0.8  # ekv. (65)
_PI_DIV_H = math.pi / _H_STEG
_H_DIV_PI = _H_STEG / math.pi
_W_TERMER: tuple[tuple[float, float], ...] = tuple(
    (n * n * _H_STEG * _H_STEG, math.exp(-(n * n * _H_STEG * _H_STEG))) for n in range(1, 6)
)
_POLAVSTAND = 1.0e-3  # närmare än så till en pol i ekv. (70)–(73) används inte närmemetoden


def markimpedans(frekvens_hz: float, flodesresistivitet_kNsm4: float) -> complex:
    """Normaliserad markimpedans Z_G enligt AV 1106/07 avsnitt 5.6.2, ekv. (57).

    Ekvationen skrivs i rapporten med 1000·f/σ och σ i Pa·s/m². Här anges σ i kN·s/m⁴
    (= kPa·s/m²), som i terrängprofilen, så kvoten blir f/σ.
    """
    if frekvens_hz <= 0.0:
        raise BullerFel("Frekvensen ska vara större än 0 Hz.")
    if not (flodesresistivitet_kNsm4 > 0.0 and math.isfinite(flodesresistivitet_kNsm4)):
        raise BullerFel("Flödesresistiviteten ska vara ändlig och större än 0 kNs/m⁴.")
    kvot = frekvens_hz / flodesresistivitet_kNsm4
    return complex(1.0 + 9.08 * kvot**-0.75, 11.9 * kvot**-0.73)


def planvagsreflektion(impedans: complex, strykvinkel_rad: float) -> complex:
    """Reflektionskoefficient för plan våg, AV 1106/07 avsnitt 5.6.3 ekv. (59) = 5.6.5 ekv. (77)."""
    s = math.sin(strykvinkel_rad)
    admittans = 1.0 / impedans
    return (s - admittans) / (s + admittans)


def _w_stora_argument(rho_plus: complex) -> complex:
    """w₊(ρ₊) för |x| > 3,9 eller |y| > 3, AV 1106/07 avsnitt 5.6.3, ekv. (63)–(64)."""
    r2 = rho_plus * rho_plus
    if rho_plus.real > 6.0 or rho_plus.imag > 6.0:
        return 1j * rho_plus * (0.5124242 / (r2 - 0.2752551) + 0.05176536 / (r2 - 2.724745))
    return (
        1j
        * rho_plus
        * (
            0.4613135 / (r2 - 0.1901635)
            + 0.09999216 / (r2 - 1.7844927)
            + 0.002883894 / (r2 - 5.5253437)
        )
    )


def _w_sma_argument(x: float, y: float) -> complex:
    """w(ρ) för |x| ≤ 3,9 och |y| ≤ 3, AV 1106/07 avsnitt 5.6.3, ekv. (65)–(74)."""
    two_xy = 2.0 * x * y
    a1 = math.cos(two_xy)
    b1 = math.sin(two_xy)
    two_y_pi_h = 2.0 * y * _PI_DIV_H
    two_x_pi_h = 2.0 * x * _PI_DIV_H
    c1 = math.exp(-two_y_pi_h) - math.cos(two_x_pi_h)
    d1 = math.sin(two_x_pi_h)
    namnare = c1 * c1 + d1 * d1
    x2 = x * x
    y2 = y * y
    forfaktor = 2.0 * math.exp(-(x2 + two_y_pi_h - y2))
    inv_namnare = forfaktor / namnare
    p2 = inv_namnare * (a1 * c1 - b1 * d1)
    q2 = inv_namnare * (a1 * d1 + b1 * c1)
    x2py2 = x2 + y2
    y2mx2 = y2 - x2
    f4x2y2 = 4.0 * x2 * y2
    summa_h = 0.0
    summa_k = 0.0
    for n2h2, exp_neg in _W_TERMER:
        d = y2mx2 + n2h2
        gemensam = exp_neg / (d * d + f4x2y2)
        summa_h += gemensam * (x2py2 + n2h2)
        summa_k += gemensam * (x2py2 - n2h2)
    inv_x2py2 = 1.0 / x2py2
    stor_h = _H_DIV_PI * y * (inv_x2py2 + 2.0 * summa_h)
    stor_k = _H_DIV_PI * x * (inv_x2py2 + 2.0 * summa_k)
    return complex(stor_h + p2, stor_k - q2)


def w_funktion(rho: complex) -> complex:
    """w(ρ) = exp(−ρ²)·erfc(−jρ) med rapportens närmemetod, AV 1106/07 ekv. (61)–(74).

    Rapportens indelning efter tecken på x och y (ekv. 62) lämnar fallen x = 0 och y = 0 samt
    kvadranten x < 0, y < 0 obestämda. Ekv. (70)–(73) har dessutom poler på reella axeln i
    x = n·0,8 (n = 0, ±1, …) som bara tar ut varandra analytiskt; inom 0,001 från en pol
    räknas w därför med `buller.akustik.faddeeva`, som beräknar samma funktion. Båda valen
    står i `docs/buller/tolkningar.md`.
    """
    x = rho.real
    y = rho.imag
    if abs(x) > 3.9 or abs(y) > 3.0:
        rho_plus = complex(abs(x), abs(y))
        w_plus = _w_stora_argument(rho_plus)
        if y >= 0.0:
            return w_plus if x >= 0.0 else w_plus.conjugate()
        nedre = 2.0 * cmath.exp(-(rho_plus.conjugate() ** 2)) - w_plus.conjugate()
        return nedre if x >= 0.0 else nedre.conjugate()
    narmaste_pol = round(x / _H_STEG) * _H_STEG
    if math.hypot(x - narmaste_pol, y) < _POLAVSTAND:
        return complex(faddeeva(np.array([rho], dtype=np.complex128))[0])
    return _w_sma_argument(x, y)


def sfarisk_reflektion(
    frekvens_hz: float, gangtid_reflekterad_s: float, strykvinkel_rad: float, impedans: complex
) -> complex:
    """Sfärisk reflektionskoefficient Q, AV 1106/07 avsnitt 5.6.3, ekv. (58)–(60).

    `gangtid_reflekterad_s` är gångtiden τ₂ längs den reflekterade strålen.
    """
    rp = planvagsreflektion(impedans, strykvinkel_rad)
    omega = 2.0 * math.pi * frekvens_hz
    rho = (
        (1.0 + 1.0j)
        / 2.0
        * math.sqrt(omega * gangtid_reflekterad_s)
        * (math.sin(strykvinkel_rad) + 1.0 / impedans)
    )
    e = 1.0 + 1j * _ROT_PI * rho * w_funktion(rho)
    return rp + (1.0 - rp) * e


def slumpinfallsabsorption(impedans: complex) -> float:
    """Absorptionskoefficient för slumpvis infall α_ri, AV 1106/07 avsnitt 5.6.4, ekv. (76)."""
    x = impedans.real
    y = impedans.imag
    m2 = x * x + y * y
    return (
        8.0
        * x
        / m2
        * (
            1.0
            - x / m2 * math.log((1.0 + x) ** 2 + y * y)
            + (x * x - y * y) / (m2 * y) * math.atan(y / (1.0 + x))
        )
    )


def inkoherent_reflektion(impedans: complex) -> float:
    """Inkoherent reflektionskoefficient ℜ_i = √(1 − α_ri), AV 1106/07 avsnitt 5.6.4, ekv. (75)."""
    alfa = slumpinfallsabsorption(impedans)
    if alfa > 1.0 + 1e-9 or alfa < -1e-9:
        raise BullerFel(f"Absorptionskoefficienten {alfa} ligger utanför [0, 1].")
    return math.sqrt(max(0.0, 1.0 - alfa))
