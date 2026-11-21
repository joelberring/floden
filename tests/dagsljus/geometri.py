"""Syntetiska höjdfält med känt analytiskt svar för dagsljustesterna."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from dagsljus.horisont import Hojdfalt
from dagsljus.matt import himmelsintegral

Falt = NDArray[np.float64]


def plan(n: int, cell_m: float) -> Hojdfalt:
    return Hojdfalt(np.zeros((n, n)), cell_m, 0.0, n * cell_m)


def kanjon_falt(
    cell_m: float, bredd_m: float, hojd_m: float, halv_x_m: float = 80.0, halv_y_m: float = 400.0
) -> Hojdfalt:
    """Gaturum längs norr–söder, symmetriskt kring x = 0, y = 0 (en cell är centrerad där).

    Gatan (z=0) är |x| <= bredd/2, utanför är väggar med höjden `hojd_m` ut till fältets kant.
    Fältet har udda antal celler så att x = 0 och y = 0 är cellmittpunkter. Är `bredd_m` en udda
    multipel av `cell_m` ligger väggytorna exakt på cellgränser (x = +-bredd/2).
    """
    nx = 2 * int(round(halv_x_m / cell_m)) + 1
    ny = 2 * int(round(halv_y_m / cell_m)) + 1
    xc = (np.arange(nx) - (nx - 1) / 2.0) * cell_m
    z = np.zeros((ny, nx))
    z[:, np.abs(xc) > bredd_m / 2.0 + 1e-9] = hojd_m
    return Hojdfalt(z, cell_m, -nx * cell_m / 2.0, ny * cell_m / 2.0)


def kvadratur_i_psi(funktion: np.ufunc | object, n: int = 400) -> float:
    """Gauss–Legendre över psi i [-pi/2, pi/2] av funktion(psi)."""
    x, w = np.polynomial.legendre.leggauss(n)
    psi = x * math.pi / 2.0
    return float(np.sum(w * funktion(psi)) * math.pi / 2.0)  # type: ignore[operator]


def vsc_kanjon_facit(hojd_m: float, avstand_m: float, rmax_m: float | None = None) -> float:
    """VSC på en vertikal fasad mitt emot en oändlig vägg (höjd H, avstånd d):
    (1/N) Int I(h(psi)) cos psi dpsi med h = atan(H cos psi / d), Gauss–Legendre-kvadratur.

    Med `rmax_m` ser strålarna bara väggen om d / cos psi <= rmax; för större |psi| är h = 0
    (så beter sig horisontberäkningen med begränsad räckvidd). Integralen delas vid psi_c.
    """
    from dagsljus.matt import N_HORISONTELL_OSKYMD

    x, w = np.polynomial.legendre.leggauss(300)

    def del_(a: float, b: float, med_vagg: bool) -> float:
        psi = 0.5 * (b - a) * x + 0.5 * (b + a)
        hh = np.arctan(hojd_m * np.cos(psi) / avstand_m) if med_vagg else np.zeros_like(psi)
        return float(np.sum(w * himmelsintegral(hh) * np.cos(psi)) * 0.5 * (b - a))

    if rmax_m is None or rmax_m <= avstand_m:
        psi_c = math.pi / 2.0 if rmax_m is None else 0.0
    else:
        psi_c = math.acos(avstand_m / rmax_m)
    tot = 2.0 * del_(0.0, psi_c, True) if psi_c > 0 else 0.0
    if psi_c < math.pi / 2.0:
        tot += 2.0 * del_(psi_c, math.pi / 2.0, False)
    return tot / N_HORISONTELL_OSKYMD


def svf_kanjon_facit(hojd_m: float, d1_m: float, d2_m: float, rmax_m: float | None = None) -> float:
    """SVF för en punkt på gatan i ett oändligt gaturum (Oke 1981, generell form):
    SVF = (cos atan(H/d1) + cos atan(H/d2)) / 2. Härledning i `docs/DAGSLJUS.md`.

    Med `rmax_m` ger riktningar som träffar väggen först bortom rmax h = 0. Då är
    1 - SVF = (1/2pi) Int sin^2 h dphi, summerat över båda väggarna med kvadratur.
    """
    if rmax_m is None:
        return 0.5 * (math.cos(math.atan(hojd_m / d1_m)) + math.cos(math.atan(hojd_m / d2_m)))
    x, w = np.polynomial.legendre.leggauss(400)
    forlust = 0.0
    for d in (d1_m, d2_m):
        # Väggen på sidan med avstånd d: riktning vinkel a från normalen, träff vid d / cos a.
        a_c = math.acos(d / rmax_m) if rmax_m > d else 0.0
        a = 0.5 * a_c * (x + 1.0)
        h = np.arctan(hojd_m * np.cos(a) / d)
        forlust += 2.0 * float(np.sum(w * np.sin(h) ** 2) * 0.5 * a_c)
    return 1.0 - forlust / (2.0 * math.pi)
