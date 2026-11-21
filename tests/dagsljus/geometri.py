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
    """Gaturum längs norr–söder: x i [-bredd/2, bredd/2] är gata (z=0), utanför är väggar med
    höjden `hojd_m` ut till fältets kant. Cell räknas som vägg om dess mittpunkt ligger utanför."""
    nx = int(round(2 * halv_x_m / cell_m))
    ny = int(round(2 * halv_y_m / cell_m))
    xc = -halv_x_m + (np.arange(nx) + 0.5) * cell_m
    z = np.zeros((ny, nx))
    z[:, np.abs(xc) > bredd_m / 2.0] = hojd_m
    return Hojdfalt(z, cell_m, -halv_x_m, halv_y_m)


def kvadratur_i_psi(funktion: "np.ufunc | object", n: int = 400) -> float:
    """Gauss–Legendre över psi i [-pi/2, pi/2] av funktion(psi)."""
    x, w = np.polynomial.legendre.leggauss(n)
    psi = x * math.pi / 2.0
    return float(np.sum(w * funktion(psi)) * math.pi / 2.0)  # type: ignore[operator]


def vsc_kanjon_facit(hojd_m: float, avstand_m: float) -> float:
    """VSC på en vertikal fasad mitt emot en oändlig vägg (höjd H, avstånd d), numerisk
    kvadratur av (1/N) Int I(atan(H cos psi / d)) cos psi dpsi med Gauss–Legendre."""
    from dagsljus.matt import N_HORISONTELL_OSKYMD

    def f(psi: Falt) -> Falt:
        return himmelsintegral(np.arctan(hojd_m * np.cos(psi) / avstand_m)) * np.cos(psi)

    return kvadratur_i_psi(f) / N_HORISONTELL_OSKYMD


def svf_kanjon_facit(hojd_m: float, d1_m: float, d2_m: float) -> float:
    """SVF för en punkt på gatan i ett oändligt gaturum (Oke 1981, ekv. för symmetriskt
    gaturum, här i den generella formen): SVF = (cos atan(H/d1) + cos atan(H/d2)) / 2."""
    return 0.5 * (math.cos(math.atan(hojd_m / d1_m)) + math.cos(math.atan(hojd_m / d2_m)))
