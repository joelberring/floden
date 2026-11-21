"""Delmodell 1 i Nord2000: plan mark med en marktyp (homogen atmosfär).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.10, ekv. (115)–(123), med rättelsen i
TC-101327 (2019) avsnitt 2.6: 10·lg ska stå först i högerledet i ekv. (120) och (121).

Skuggzonsgrenen (ekv. 121–122) förekommer bara vid uppåtbrytande refraktion och ingår inte.
"""

from __future__ import annotations

import cmath
import math

import numpy as np

from buller.nord2000.atmosfar import HomogenAtmosfar
from buller.nord2000.frekvenser import vagtal_per_m
from buller.nord2000.geometri import MINSTA_HOJD_M, stralar_plan_mark
from buller.nord2000.koherens import (
    koherens_bandmedelvarde,
    koherens_fluktuerande_refraktion,
    koherens_markrahet,
    koherens_turbulens,
    transversell_separation_m,
)
from buller.nord2000.mark import inkoherent_reflektion, markimpedans, sfarisk_reflektion
from buller.typer import BullerFel, Flyttal


def koherens_plan_mark(
    frekvens_hz: float,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    rahet_m: float,
    atmosfar: HomogenAtmosfar,
    koherens_spridningszon: float = 1.0,
) -> float:
    """Koherenskoefficienten F(f) för plan mark, AV 1106/07 avsnitt 5.10, ekv. (118)–(119).

    I homogen atmosfär utan fluktuationer är Δτ₊ = Δτ, så F_Δτ = 1 (ekv. 112); t_mean = t₀ och
    c̄ = c₀ = C. `koherens_spridningszon` är F_s (avsnitt 5.19), 1 utan spridningszon.
    """
    c = atmosfar.ljudhastighet_m_s
    stralar = stralar_plan_mark(avstand_m, kallhojd_m, mottagarhojd_m, c)
    dtau = stralar.gangtidsskillnad_s
    return (
        koherens_bandmedelvarde(frekvens_hz, dtau)
        * koherens_fluktuerande_refraktion(frekvens_hz, dtau, dtau)
        * koherens_turbulens(
            frekvens_hz,
            atmosfar.cv2,
            atmosfar.ct2,
            atmosfar.temperatur_c,
            c,
            transversell_separation_m(kallhojd_m, mottagarhojd_m),
            avstand_m,
        )
        * koherens_markrahet(vagtal_per_m(frekvens_hz, c), stralar.strykvinkel_rad, rahet_m)
        * koherens_spridningszon
    )


def markeffekt_plan_mark_db(
    frekvenser_hz: Flyttal,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    flodesresistivitet_kNsm4: float,
    rahet_m: float,
    atmosfar: HomogenAtmosfar,
) -> Flyttal:
    """Funktionen SubModel1: markeffekten ΔL_flat per tersband, AV 1106/07 ekv. (120) och (123).

    ΔL = 10·lg(|1 + F·(R₁/R₂)·exp(j·2πf·Δτ)·Q|² + (1 − F²)·((R₁/R₂)·ℜ_i)²), där Q är den
    sfäriska reflektionskoefficienten (ekv. 58) och ℜ_i den inkoherenta (ekv. 75).
    `avstand_m` mäts längs marken och höjderna vinkelrätt mot den; höjder under 0,01 m
    ersätts med 0,01 m (avsnitt 5.4.1).
    """
    if avstand_m <= 0.0:
        raise BullerFel("Avståndet längs marken ska vara större än 0.")
    if rahet_m < 0.0:
        raise BullerFel("Markens råhet får inte vara negativ.")
    h_s = max(kallhojd_m, MINSTA_HOJD_M)
    h_r = max(mottagarhojd_m, MINSTA_HOJD_M)
    stralar = stralar_plan_mark(avstand_m, h_s, h_r, atmosfar.ljudhastighet_m_s)
    kvot = stralar.r1_m / stralar.r2_m
    resultat = np.empty(frekvenser_hz.shape[0], dtype=np.float64)
    for i, f_np in enumerate(frekvenser_hz):
        f = float(f_np)
        z = markimpedans(f, flodesresistivitet_kNsm4)
        q = sfarisk_reflektion(f, stralar.tau2_s, stralar.strykvinkel_rad, z)
        koherens = koherens_plan_mark(f, avstand_m, h_s, h_r, rahet_m, atmosfar)
        fas = cmath.exp(1j * 2.0 * math.pi * f * stralar.gangtidsskillnad_s)
        koherent = abs(1.0 + koherens * kvot * fas * q) ** 2
        inkoherent = (1.0 - koherens**2) * (kvot * inkoherent_reflektion(z)) ** 2
        resultat[i] = 10.0 * math.log10(koherent + inkoherent)
    return resultat
