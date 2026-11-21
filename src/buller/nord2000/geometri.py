"""Geometriska hjälpfunktioner och strålvariabler för homogen atmosfär i Nord2000.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.23.11 (Length, ekv. 372), 5.23.13 (NormLine,
ekv. 377) och avsnitt 5.5 (strålvariabler) i gränsen ξ → 0, se figur 14 i avsnitt 5.10.

Rapporten föreskriver att ξ = 10⁻¹⁰ sätts in i cirkelstråleformlerna när atmosfären är homogen
(avsnitt 5.5.4). Här används i stället räta strålar, som är gränsvärdet av samma formler och
saknar deras avrundningsfel; valet står i `docs/buller/tolkningar.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from buller.typer import BullerFel

MINSTA_HOJD_M = 0.01  # AV 1106/07 avsnitt 5.4.1 och 5.23.6: lägre höjder ersätts med 0,01 m


def langd(x1: float, y1: float, x2: float, y2: float) -> float:
    """Funktionen Length: avståndet mellan två punkter, AV 1106/07 avsnitt 5.23.11, ekv. (372)."""
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def normlinje(
    x1: float, y1: float, x2: float, y2: float, x: float, y: float
) -> tuple[float, float, float]:
    """Funktionen NormLine, AV 1106/07 avsnitt 5.23.13, ekv. (377).

    Returnerar projektionen (x0, y0) av punkten (x, y) på linjen genom P1 och P2 samt det
    teckensatta avståndet d till den riktade linjen (positivt till vänster om P1 → P2, alltså
    ovanför linjen när x2 > x1).
    """
    x21 = x2 - x1
    y21 = y2 - y1
    x01 = x - x1
    y01 = y - y1
    kvadrat = x21 * x21 + y21 * y21
    if kvadrat <= 0.0:
        raise BullerFel("NormLine kräver två skilda punkter på linjen.")
    c = (x21 * x01 + y21 * y01) / kvadrat
    return c * x21 + x1, c * y21 + y1, (x21 * y01 - y21 * x01) / math.sqrt(kvadrat)


@dataclass(frozen=True)
class StralarPlanMark:
    """Strålvariabler för direkt och markreflekterad stråle över plan mark, homogen atmosfär.

    Beteckningar som i AV 1106/07 avsnitt 5.5.1 och figur 14: R₁, τ₁ för den direkta strålen,
    R₂, τ₂ och strykvinkeln ψ_G för den reflekterade, samt avståndet d_refl från källan till
    reflexpunkten längs marken.
    """

    r1_m: float
    r2_m: float
    tau1_s: float
    tau2_s: float
    strykvinkel_rad: float
    d_refl_m: float
    hojdprodukt_m2: float  # h_S·h_R

    @property
    def gangtidsskillnad_s(self) -> float:
        """Δτ = τ₂ − τ₁, AV 1106/07 avsnitt 5.5.6, ekv. (51), räknad utan utsläckning."""
        return self.gangvagsskillnad_m * self.tau1_s / self.r1_m

    @property
    def gangvagsskillnad_m(self) -> float:
        """R₂ − R₁ räknad som 4·h_S·h_R/(R₁ + R₂) för att undvika utsläckning (avsnitt 5.5.6)."""
        return 4.0 * self.hojdprodukt_m2 / (self.r1_m + self.r2_m)


def stralar_plan_mark(
    avstand_m: float, kallhojd_m: float, mottagarhojd_m: float, ljudhastighet_m_s: float
) -> StralarPlanMark:
    """Strålvariabler enligt AV 1106/07 avsnitt 5.5.4–5.5.6 för ξ = 0 (räta strålar, figur 14).

    R₁² = d² + (h_S − h_R)², R₂² = d² + (h_S + h_R)², tan ψ_G = (h_S + h_R)/d, τ = R/c och
    d_refl = d·h_S/(h_S + h_R).
    """
    if avstand_m <= 0.0 or kallhojd_m <= 0.0 or mottagarhojd_m <= 0.0:
        raise BullerFel("Avstånd och höjder över marken ska vara större än 0.")
    r1 = math.hypot(avstand_m, kallhojd_m - mottagarhojd_m)
    r2 = math.hypot(avstand_m, kallhojd_m + mottagarhojd_m)
    return StralarPlanMark(
        r1_m=r1,
        r2_m=r2,
        tau1_s=r1 / ljudhastighet_m_s,
        tau2_s=r2 / ljudhastighet_m_s,
        strykvinkel_rad=math.atan2(kallhojd_m + mottagarhojd_m, avstand_m),
        d_refl_m=avstand_m * kallhojd_m / (kallhojd_m + mottagarhojd_m),
        hojdprodukt_m2=kallhojd_m * mottagarhojd_m,
    )
