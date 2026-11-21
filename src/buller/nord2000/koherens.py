"""Koherenskoefficienter i Nord2000.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.9, ekv. (110)–(114), samt ekv. (119) i
avsnitt 5.10 för den transversella separationen över plan mark.
"""

from __future__ import annotations

import math

from buller.nord2000.frekvenser import exp_modifierad


def _sinc_avklippt(x: float) -> float:
    """Gemensam form för ekv. (111) och (112): 1 för x = 0, sin(x)/x för 0 < x < π, annars 0."""
    if x <= 0.0:
        return 1.0
    if x < math.pi:
        return math.sin(x) / x
    return 0.0


def koherens_bandmedelvarde(frekvens_hz: float, gangtidsskillnad_s: float) -> float:
    """F_f: medelvärdesbildning inom tersbandet, AV 1106/07 avsnitt 5.9.1, ekv. (111)."""
    return _sinc_avklippt(0.23 * math.pi * frekvens_hz * gangtidsskillnad_s)


def koherens_fluktuerande_refraktion(
    frekvens_hz: float, gangtidsskillnad_s: float, gangtidsskillnad_ovre_s: float
) -> float:
    """F_Δτ: fluktuerande refraktion, AV 1106/07 avsnitt 5.9.2, ekv. (112).

    `gangtidsskillnad_ovre_s` är Δτ₊, räknad med A₊ och B₊ enligt ekv. (10).
    """
    return _sinc_avklippt(
        2.0 * math.pi * frekvens_hz * abs(gangtidsskillnad_ovre_s - gangtidsskillnad_s)
    )


def koherens_turbulens(
    frekvens_hz: float,
    cv2: float,
    ct2: float,
    medeltemperatur_c: float,
    medelljudhastighet_m_s: float,
    separation_m: float,
    avstand_m: float,
) -> float:
    """F_c: turbulens, AV 1106/07 avsnitt 5.9.3, ekv. (113).

    Temperaturtermen är C_T²/T² med T = t̄ + 273,15 K, som i AV 1849/00 ekv. (141). I
    AV 1106/07 ekv. (113) är nämnaren tryckt utan kvadrat (se `docs/buller/tolkningar.md`).
    """
    t_k = medeltemperatur_c + 273.15
    x = (
        -5.3888
        * (ct2 / (t_k * t_k) + 22.0 / 3.0 * cv2 / medelljudhastighet_m_s**2)
        * (frekvens_hz / medelljudhastighet_m_s) ** 2
        * separation_m ** (5.0 / 3.0)
        * avstand_m
    )
    return exp_modifierad(x)


def transversell_separation_m(kallhojd_m: float, mottagarhojd_m: float) -> float:
    """ρ = 2·h_S·h_R/(h_S + h_R) över plan mark, AV 1106/07 avsnitt 5.10, ekv. (119)."""
    return 2.0 * kallhojd_m * mottagarhojd_m / (kallhojd_m + mottagarhojd_m)


def koherens_markrahet(vagtal_per_m: float, strykvinkel_rad: float, rahet_m: float) -> float:
    """F_r: markytans råhet, AV 1106/07 avsnitt 5.9.4, ekv. (114)."""
    x = vagtal_per_m * rahet_m * math.sin(strykvinkel_rad)
    if x <= 0.026686:
        g = 0.0
    elif x < 0.115448:
        g = 0.55988 * (0.115448 - x) - 0.049696
    else:
        g = -0.066 + 1.066 * x - 8.543 * x**2 + 4.71 * x**3 - 0.83 * x**4
    return exp_modifierad(0.5 * g)
