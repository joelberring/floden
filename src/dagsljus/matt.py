"""Dagsljusmått ur horisontprofiler: SVF, VSC (BRE, mulen CIE-himmel) och soltimmar.

Härledningarna står i `docs/DAGSLJUS.md`. Alla vinklar i radianer, azimut norr = 0, medurs.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

Falt = NDArray[np.float64]

# Horisontell belysning under oskymd CIE-mulen himmel med zenitluminans 1: 7 pi / 9.
N_HORISONTELL_OSKYMD = 7.0 * math.pi / 9.0
# VSC för en oskymd vertikal fasad (BRE-normeringen): (2/3) (pi/4 + 2/3) / (7 pi / 9) = 0,3962.
VSC_OSKYMD = (2.0 / 3.0) * (math.pi / 4.0 + 2.0 / 3.0) / N_HORISONTELL_OSKYMD


class MattFel(Exception):
    """Ogiltig indata till ett dagsljusmått."""


def svf_fran_horisont(h_rad: NDArray[Any], axel: int = 0) -> Falt:
    """SVF för en horisontell yta: 1 - (1/2pi) Int sin^2 h dphi, som medelvärde över K lika
    fördelade riktningar längs `axel`. Fri yta (h = 0) ger exakt 1."""
    h = np.asarray(h_rad, dtype=np.float64)
    if h.size and (h.min() < -1e-12 or h.max() > math.pi / 2 + 1e-12):
        raise MattFel("Horisontvinklar måste ligga i [0, pi/2].")
    return np.asarray(1.0 - np.mean(np.sin(h) ** 2, axis=axel), dtype=np.float64)


def himmelsintegral(h_rad: NDArray[Any]) -> Falt:
    """Inre integralen I(h) = Int_h^{pi/2} (1 + 2 sin g)/3 cos^2 g dg i sluten form:

        I(h) = (1/3) [ pi/4 - h/2 - sin(2h)/4 + (2/3) cos^3 h ]

    I(0) = (1/3)(pi/4 + 2/3), I(pi/2) = 0.
    """
    h = np.asarray(h_rad, dtype=np.float64)
    return np.asarray(
        (math.pi / 4.0 - h / 2.0 - np.sin(2.0 * h) / 4.0 + (2.0 / 3.0) * np.cos(h) ** 3) / 3.0,
        dtype=np.float64,
    )


def vsc_fran_halvplansprofil(h_rad: NDArray[Any]) -> Falt:
    """VSC (andel, 0–0,396) ur horisontvinklar i `K` mittpunktsriktningar över fasadens halvplan.

    `h_rad` har formen (..., K) där riktning j ligger i psi_j = -pi/2 + (j + 1/2) pi / K
    relativt fasadens normalazimut. Midpunktsregel för
    VSC = (1/N) Int_{-pi/2}^{pi/2} I(h(psi)) cos psi dpsi.
    """
    h = np.asarray(h_rad, dtype=np.float64)
    if h.ndim < 1 or h.shape[-1] < 2:
        raise MattFel("Halvplansprofilen måste ha minst 2 riktningar längs sista axeln.")
    if h.size and (h.min() < -1e-12 or h.max() > math.pi / 2 + 1e-12):
        raise MattFel("Horisontvinklar måste ligga i [0, pi/2].")
    k = h.shape[-1]
    psi = -math.pi / 2.0 + (np.arange(k) + 0.5) * math.pi / k
    summa = np.sum(himmelsintegral(h) * np.cos(psi), axis=-1)
    return np.asarray(summa * (math.pi / k) / N_HORISONTELL_OSKYMD, dtype=np.float64)


def halvplansriktningar(normal_azimut_rad: Falt, antal: int) -> Falt:
    """Azimuter (n, antal) för fasadens halvplan: normal + psi_j (se `vsc_fran_halvplansprofil`)."""
    psi = -math.pi / 2.0 + (np.arange(antal) + 0.5) * math.pi / antal
    n = np.atleast_1d(np.asarray(normal_azimut_rad, dtype=np.float64))
    return np.asarray((n[:, None] + psi[None, :]) % (2.0 * math.pi), dtype=np.float64)


def interpolera_horisont(h_k: NDArray[Any], azimut_rad: Falt) -> Falt:
    """Linjär interpolation (cirkulär) av en horisontprofil h_k (K riktningar, phi_k = 2pi k/K)
    till godtyckliga azimuter. Returnerar vinklar med samma form som `azimut_rad`."""
    k = len(h_k)
    pos = (np.asarray(azimut_rad, dtype=np.float64) % (2.0 * math.pi)) * (k / (2.0 * math.pi))
    i0 = np.floor(pos).astype(np.int64) % k
    i1 = (i0 + 1) % k
    f = pos - np.floor(pos)
    h = np.asarray(h_k, dtype=np.float64)
    return np.asarray(h[i0] * (1.0 - f) + h[i1] * f, dtype=np.float64)


class SoltimmarAckumulator:
    """Strömmande soltimmar: matas med horisontvinkeln för riktning 0, 1, ..., K-1 i ordning.

    Solen syns vid ett tidssteg om solhöjden (geometrisk, centrum av solskivan) är större än
    horisontvinkeln linjärt interpolerad till solens azimut mellan de två närmaste riktningarna.
    Minnet är O(punkter), oberoende av K.
    """

    def __init__(
        self,
        k: int,
        sol_hojd_rad: Falt,
        sol_azimut_rad: Falt,
        steg_h: float,
        form: tuple[int, ...],
    ) -> None:
        if k < 4:
            raise MattFel(f"K måste vara >= 4, fick {k}.")
        if not (math.isfinite(steg_h) and steg_h > 0):
            raise MattFel(f"steg_h måste vara > 0, fick {steg_h}.")
        hojd = np.asarray(sol_hojd_rad, dtype=np.float64)
        az = np.asarray(sol_azimut_rad, dtype=np.float64) % (2.0 * math.pi)
        if hojd.shape != az.shape or hojd.ndim != 1:
            raise MattFel("Solhöjd och solazimut måste vara endimensionella och lika långa.")
        self._k = k
        self._steg_h = steg_h
        upp = hojd > 0.0
        pos = az[upp] * (k / (2.0 * math.pi))
        self._hojd = hojd[upp]
        self._seg = np.floor(pos).astype(np.int64) % k
        self._frak = pos - np.floor(pos)
        akt: set[int] = set()
        for s in self._seg.tolist():
            si = int(s)
            akt.add(si)
            akt.add((si + 1) % k)
        self._aktiva = frozenset(akt)
        self._antal = np.zeros(form, dtype=np.int32)
        self._forra: NDArray[np.float64] | None = None
        self._h0: NDArray[np.float64] | None = None
        self._nasta = 0
        self._klar = False

    def aktiva_riktningar(self) -> frozenset[int]:
        """Index för de riktningar 0..K-1 som faktiskt används av någon solposition."""
        return self._aktiva

    def _segment(self, s: int, h_a: Falt | None, h_b: Falt | None) -> None:
        traffar = np.nonzero(self._seg == s)[0]
        if traffar.size == 0:
            return
        if h_a is None or h_b is None:
            raise MattFel(f"Saknar horisontdata för aktivt solsegment {s}.")
        for m in traffar:
            f = self._frak[m]
            hh = h_a * (1.0 - f) + h_b * f
            self._antal += (self._hojd[m] > hh).astype(np.int32)

    def mata(self, k: int, h_rad: NDArray[Any] | None) -> None:
        if k != self._nasta:
            raise MattFel(f"Riktningarna måste matas i ordning, väntade {self._nasta}, fick {k}.")
        if h_rad is None:
            if k in self._aktiva:
                raise MattFel(f"Riktning {k} är aktiv för solen och kan inte hoppas över.")
            h: NDArray[np.float64] | None = None
        else:
            h = np.asarray(h_rad, dtype=np.float64)
            if h.shape != self._antal.shape:
                raise MattFel(f"Formen {h.shape} stämmer inte med {self._antal.shape}.")
        if k == 0:
            self._h0 = h
        else:
            self._segment(k - 1, self._forra, h)
        self._forra = h
        self._nasta += 1

    def timmar(self) -> Falt:
        """Soltimmar (h) när alla K riktningar matats."""
        if self._nasta != self._k:
            raise MattFel(f"Bara {self._nasta} av {self._k} riktningar har matats.")
        if not self._klar:
            self._segment(self._k - 1, self._forra, self._h0)
            self._klar = True
        return np.asarray(self._antal * self._steg_h, dtype=np.float64)


def soltimmar_fran_profiler(
    h_k: NDArray[Any],
    sol_hojd_rad: Falt,
    sol_azimut_rad: Falt,
    steg_h: float,
) -> Falt:
    """Soltimmar för punkter med färdiga profiler h_k av formen (K, ...)."""
    ack = SoltimmarAckumulator(h_k.shape[0], sol_hojd_rad, sol_azimut_rad, steg_h, h_k.shape[1:])
    for k in range(h_k.shape[0]):
        ack.mata(k, h_k[k])
    return ack.timmar()
