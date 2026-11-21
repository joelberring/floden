"""Dubbel diffraktion i Nord2000: två kilar efter varandra (p2wedge) och tjock skärm (p2edge).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.7.3 (ekv. 95–99) och 5.7.4 (ekv. 100–104), med
FORCE TC-101327 (2019) avsnitt 2.4–2.5 (exponenten i ekv. 97–98 och 102–103 ska läsas jωτ)
och 2.3 med bilaga G.3–G.4 (kilytornas reflektionskoefficienter i delmodell 5 och 6).
Diffraktionskoefficienten för en kil (Dwedge, avsnitt 5.7.2) hämtas ur
`buller.nord2000.kildiffraktion`.

Storheter som i figur 10 och 11: källa S, första kanten T₁ (närmast källan), andra kanten T₂
(närmast mottagaren) och mottagare R. Sträckorna är R_S (S–T₁), R_M (T₁–T₂) och R_R (T₂–R)
med gångtiderna τ_S, τ_M och τ_R. Varje kil har sin kilvinkel β och sina diffraktionsvinklar
θ_S och θ_R, mätta från kilens yta mot mottagaren som i figur 9. Trycket är normerat så att
fritt fält på avståndet R är exp(jωτ)/R.

Vilken kant som är den viktigaste avgörs i terrängtolkningen (avsnitt 5.21) och är indata
här. Vinklarna är också indata: punktlistorna i 5.7.3 och 5.7.4 säger mot vilken punkt
θ_1R och θ_2S ska mätas, och delmodell 5 och 6 räknar dem (ekv. 194–195 m.fl.).

Tolkningar står i `docs/buller/logg/P-K2.md`.
"""

from __future__ import annotations

import cmath
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from buller.nord2000.kildiffraktion import (
    Kil,
    kildiffraktionskoefficient_impedans,
    kildiffraktionskoefficient_reflektionskoefficienter,
)
from buller.typer import BullerFel

Viktigast = Literal["forsta", "andra"]
"""Vilken kil eller kant som är den viktigaste enligt AV 1106/07 avsnitt 5.21."""

Kilkoefficient = Callable[[Kil], complex]
"""Diffraktionskoefficienten D̂ för en kil med given geometri (Dwedge med valda ytor)."""

FAKTOR_TVA_KILAR = 1.0  # AV 1106/07 ekv. (97)–(98)
FAKTOR_TJOCK_SKARM = 0.5  # AV 1106/07 ekv. (102)–(103)


@dataclass(frozen=True)
class TvaKanter:
    """Indata för två kilar eller en tjock skärm, punktlistorna i AV 1106/07 5.7.3 och 5.7.4.

    Index 1 är kilen närmast källan och index 2 kilen närmast mottagaren. θ_1R mäts mot T₂ om
    T₂ ligger över linjen T₁–R och annars mot R; θ_2S mäts mot T₁ om T₁ ligger över linjen
    T₂–S och annars mot S. För tjock skärm är kilytan mellan kanterna gemensam, så att
    θ_1R = 0 respektive θ_2S = β₂ i det första fallet (5.7.4). Anroparen räknar vinklarna.
    """

    beta_1_rad: float
    theta_1s_rad: float
    theta_1r_rad: float
    beta_2_rad: float
    theta_2s_rad: float
    theta_2r_rad: float
    gangtid_kalla_s: float  # τ_S, S–T₁
    gangtid_mitten_s: float  # τ_M, T₁–T₂
    gangtid_mottagare_s: float  # τ_R, T₂–R
    vag_kalla_m: float  # R_S
    vag_mitten_m: float  # R_M
    vag_mottagare_m: float  # R_R

    def __post_init__(self) -> None:
        vinklar = (
            self.beta_1_rad,
            self.theta_1s_rad,
            self.theta_1r_rad,
            self.beta_2_rad,
            self.theta_2s_rad,
            self.theta_2r_rad,
        )
        positiva = (
            self.gangtid_kalla_s,
            self.gangtid_mitten_s,
            self.gangtid_mottagare_s,
            self.vag_kalla_m,
            self.vag_mitten_m,
            self.vag_mottagare_m,
        )
        if not all(math.isfinite(v) for v in vinklar + positiva):
            raise BullerFel("Vinklar, gångtider och sträckor för två kanter ska vara ändliga tal.")
        if not all(v > 0.0 for v in positiva):
            raise BullerFel(
                "Gångtider och sträckor för två kanter ska vara större än 0. Sammanfaller "
                "kanterna (R_M = 0) är det en kant och räknas med kildiffraktionen i 5.7.1."
            )

    @property
    def gangtid_s(self) -> float:
        """Hela gångtiden τ = τ_S + τ_M + τ_R, AV 1106/07 ekv. (95) och (100)."""
        return self.gangtid_kalla_s + self.gangtid_mitten_s + self.gangtid_mottagare_s

    @property
    def vag_m(self) -> float:
        """Hela sträckan ℓ = R_S + R_M + R_R, AV 1106/07 ekv. (96) och (101)."""
        return self.vag_kalla_m + self.vag_mitten_m + self.vag_mottagare_m


def tva_kanter_rata_stralar(
    beta_1_rad: float,
    theta_1s_rad: float,
    theta_1r_rad: float,
    beta_2_rad: float,
    theta_2s_rad: float,
    theta_2r_rad: float,
    vag_kalla_m: float,
    vag_mitten_m: float,
    vag_mottagare_m: float,
    ljudhastighet_m_s: float,
) -> TvaKanter:
    """Två kanter i homogen atmosfär: τ_S = R_S/c, τ_M = R_M/c och τ_R = R_R/c.

    Indata enligt punktlistorna i AV 1106/07 avsnitt 5.7.3 och 5.7.4 med räta strålar
    (figur 10 och 11).
    """
    if not ljudhastighet_m_s > 0.0:
        raise BullerFel("Ljudhastigheten ska vara större än 0 m/s.")
    return TvaKanter(
        beta_1_rad=beta_1_rad,
        theta_1s_rad=theta_1s_rad,
        theta_1r_rad=theta_1r_rad,
        beta_2_rad=beta_2_rad,
        theta_2s_rad=theta_2s_rad,
        theta_2r_rad=theta_2r_rad,
        gangtid_kalla_s=vag_kalla_m / ljudhastighet_m_s,
        gangtid_mitten_s=vag_mitten_m / ljudhastighet_m_s,
        gangtid_mottagare_s=vag_mottagare_m / ljudhastighet_m_s,
        vag_kalla_m=vag_kalla_m,
        vag_mitten_m=vag_mitten_m,
        vag_mottagare_m=vag_mottagare_m,
    )


@dataclass(frozen=True)
class Dubbeldiffraktion:
    """Resultatet av AV 1106/07 ekv. (97)–(98) eller (102)–(103) med sina delar."""

    d_1: complex  # D̂₁, första kilens diffraktionskoefficient
    d_2: complex  # D̂₂, andra kilens diffraktionskoefficient
    tryck: complex  # p̂ vid mottagaren


def _kontrollera_viktigast(viktigast: str) -> None:
    if viktigast not in ("forsta", "andra"):
        raise BullerFel(
            f'viktigast = {viktigast!r}: ska vara "forsta" eller "andra" (den viktigaste kilen '
            "eller kanten enligt AV 1106/07 avsnitt 5.21)."
        )


def delkilar(geometri: TvaKanter, viktigast: Viktigast) -> tuple[Kil, Kil]:
    """Argumenten till Dwedge för de två kilarna, AV 1106/07 ekv. (97)–(98) och (102)–(103).

    Den viktigaste kilen räknas över hela vägen: den andra kanten ligger på dess stråle, så
    att sträckan och gångtiden på den sidan är summan av två delar. Den mindre viktiga kilen
    räknas bara mellan sina grannar. Med Dwedge(f, β, θ_S, θ_R, τ, τ_S, τ_R, ℓ, R_S, R_R, …):

    * första viktigast (ekv. 97, 102):
      kil 1: (β₁, θ_1S, θ_1R, τ, τ_S, τ_M + τ_R, ℓ, R_S, R_M + R_R),
      kil 2: (β₂, θ_2S, θ_2R, τ_M + τ_R, τ_M, τ_R, R_M + R_R, R_M, R_R);
    * andra viktigast (ekv. 98, 103):
      kil 1: (β₁, θ_1S, θ_1R, τ_S + τ_M, τ_S, τ_M, R_S + R_M, R_S, R_M),
      kil 2: (β₂, θ_2S, θ_2R, τ, τ_S + τ_M, τ_R, ℓ, R_S + R_M, R_R).

    Argumentlistorna är lika för två kilar och för tjock skärm; bara impedanserna skiljer.
    """
    _kontrollera_viktigast(viktigast)
    g = geometri
    if viktigast == "forsta":
        kil_1 = Kil(
            beta_rad=g.beta_1_rad,
            theta_s_rad=g.theta_1s_rad,
            theta_r_rad=g.theta_1r_rad,
            gangtid_s=g.gangtid_s,
            gangtid_kalla_s=g.gangtid_kalla_s,
            gangtid_mottagare_s=g.gangtid_mitten_s + g.gangtid_mottagare_s,
            vag_m=g.vag_m,
            vag_kalla_m=g.vag_kalla_m,
            vag_mottagare_m=g.vag_mitten_m + g.vag_mottagare_m,
        )
        kil_2 = Kil(
            beta_rad=g.beta_2_rad,
            theta_s_rad=g.theta_2s_rad,
            theta_r_rad=g.theta_2r_rad,
            gangtid_s=g.gangtid_mitten_s + g.gangtid_mottagare_s,
            gangtid_kalla_s=g.gangtid_mitten_s,
            gangtid_mottagare_s=g.gangtid_mottagare_s,
            vag_m=g.vag_mitten_m + g.vag_mottagare_m,
            vag_kalla_m=g.vag_mitten_m,
            vag_mottagare_m=g.vag_mottagare_m,
        )
        return kil_1, kil_2
    kil_1 = Kil(
        beta_rad=g.beta_1_rad,
        theta_s_rad=g.theta_1s_rad,
        theta_r_rad=g.theta_1r_rad,
        gangtid_s=g.gangtid_kalla_s + g.gangtid_mitten_s,
        gangtid_kalla_s=g.gangtid_kalla_s,
        gangtid_mottagare_s=g.gangtid_mitten_s,
        vag_m=g.vag_kalla_m + g.vag_mitten_m,
        vag_kalla_m=g.vag_kalla_m,
        vag_mottagare_m=g.vag_mitten_m,
    )
    kil_2 = Kil(
        beta_rad=g.beta_2_rad,
        theta_s_rad=g.theta_2s_rad,
        theta_r_rad=g.theta_2r_rad,
        gangtid_s=g.gangtid_s,
        gangtid_kalla_s=g.gangtid_kalla_s + g.gangtid_mitten_s,
        gangtid_mottagare_s=g.gangtid_mottagare_s,
        vag_m=g.vag_m,
        vag_kalla_m=g.vag_kalla_m + g.vag_mitten_m,
        vag_mottagare_m=g.vag_mottagare_m,
    )
    return kil_1, kil_2


def dubbel_diffraktion(
    frekvens_hz: float,
    geometri: TvaKanter,
    koefficient_1: Kilkoefficient,
    koefficient_2: Kilkoefficient,
    viktigast: Viktigast,
    faktor: float,
) -> Dubbeldiffraktion:
    """p̂ = faktor·D̂₁·D̂₂·exp(jωτ)/ℓ, AV 1106/07 ekv. (97)–(98) och (102)–(103).

    Exponenten är jωτ enligt TC-101327 avsnitt 2.4–2.5 (tryckt j2ρfτ). τ och ℓ är hela
    gångtiden och sträckan (ekv. 95–96, 100–101). `faktor` är 1 för två kilar och 0,5 för
    tjock skärm. `koefficient_1` och `koefficient_2` ger D̂ för var sin kil ur argumenten i
    `delkilar`; de färdiga varianterna nedan väljer dem. Den här funktionen är till för
    delmodeller som behöver en annan beskrivning av kilytorna.
    """
    if not (frekvens_hz > 0.0 and math.isfinite(frekvens_hz)):
        raise BullerFel("Frekvensen ska vara ändlig och större än 0 Hz.")
    kil_1, kil_2 = delkilar(geometri, viktigast)
    d_1 = koefficient_1(kil_1)
    d_2 = koefficient_2(kil_2)
    omega = 2.0 * math.pi * frekvens_hz
    tryck = faktor * d_1 * d_2 * cmath.exp(1j * omega * geometri.gangtid_s) / geometri.vag_m
    return Dubbeldiffraktion(d_1=d_1, d_2=d_2, tryck=tryck)


def tva_kilar_tryck_impedans_delar(
    frekvens_hz: float,
    geometri: TvaKanter,
    impedans_1_kalla: complex,
    impedans_1_mottagare: complex,
    impedans_2_kalla: complex,
    impedans_2_mottagare: complex,
    viktigast: Viktigast,
) -> Dubbeldiffraktion:
    """Funktionen p2wedge med D̂₁ och D̂₂ redovisade, AV 1106/07 avsnitt 5.7.3, ekv. (95)–(99).

    Impedanserna Z_1S, Z_1R, Z_2S och Z_2R är normaliserade och gäller kilytorna i figur 10;
    `math.inf` betyder hård yta. Reflektionskoefficienterna räknas enligt ekv. (80). Enligt
    TC-101327 avsnitt 2.3 gäller den definitionen efter 2018 inte delmodell 6, som ska
    använda `tva_kilar_tryck_reflektionskoefficienter_delar`.
    """

    def d_1(kil: Kil) -> complex:
        return kildiffraktionskoefficient_impedans(
            frekvens_hz, kil, impedans_1_kalla, impedans_1_mottagare
        )

    def d_2(kil: Kil) -> complex:
        return kildiffraktionskoefficient_impedans(
            frekvens_hz, kil, impedans_2_kalla, impedans_2_mottagare
        )

    return dubbel_diffraktion(frekvens_hz, geometri, d_1, d_2, viktigast, FAKTOR_TVA_KILAR)


def tva_kilar_tryck_impedans(
    frekvens_hz: float,
    geometri: TvaKanter,
    impedans_1_kalla: complex,
    impedans_1_mottagare: complex,
    impedans_2_kalla: complex,
    impedans_2_mottagare: complex,
    viktigast: Viktigast,
) -> complex:
    """Funktionen p2wedge, AV 1106/07 avsnitt 5.7.3, ekv. (99): trycket p̂ vid mottagaren."""
    return tva_kilar_tryck_impedans_delar(
        frekvens_hz,
        geometri,
        impedans_1_kalla,
        impedans_1_mottagare,
        impedans_2_kalla,
        impedans_2_mottagare,
        viktigast,
    ).tryck


def tva_kilar_tryck_reflektionskoefficienter_delar(
    frekvens_hz: float,
    geometri: TvaKanter,
    q_1_kalla: complex,
    q_1_mottagare: complex,
    q_2_kalla: complex,
    q_2_mottagare: complex,
    viktigast: Viktigast,
) -> Dubbeldiffraktion:
    """p2wedge med givna reflektionskoefficienter för de fyra kilytorna, TC-101327 2.3.

    AV 1106/07 ekv. (95)–(98) med Dwedge räknad ur färdiga koefficienter i stället för
    impedanser. I delmodell 6 är det Q_avS,1, Q'_avR,1, Q'_avS,2 och Q_avR,2 ur TC-101327
    bilaga G.4 (räknas i delmodellpaketet). Den speglade strålen i ekv. (89)–(90) får samma
    koefficient som diffraktionstermen (förvalet i `kildiffraktion`, öppen fråga i P-K1).
    """

    def d_1(kil: Kil) -> complex:
        return kildiffraktionskoefficient_reflektionskoefficienter(
            frekvens_hz, kil, q_1_kalla, q_1_mottagare
        )

    def d_2(kil: Kil) -> complex:
        return kildiffraktionskoefficient_reflektionskoefficienter(
            frekvens_hz, kil, q_2_kalla, q_2_mottagare
        )

    return dubbel_diffraktion(frekvens_hz, geometri, d_1, d_2, viktigast, FAKTOR_TVA_KILAR)


def tva_kilar_tryck_reflektionskoefficienter(
    frekvens_hz: float,
    geometri: TvaKanter,
    q_1_kalla: complex,
    q_1_mottagare: complex,
    q_2_kalla: complex,
    q_2_mottagare: complex,
    viktigast: Viktigast,
) -> complex:
    """Trycket p̂ ur `tva_kilar_tryck_reflektionskoefficienter_delar` (p2wedge, TC 2.3)."""
    return tva_kilar_tryck_reflektionskoefficienter_delar(
        frekvens_hz, geometri, q_1_kalla, q_1_mottagare, q_2_kalla, q_2_mottagare, viktigast
    ).tryck


def tjock_skarm_tryck_impedans_delar(
    frekvens_hz: float,
    geometri: TvaKanter,
    impedans_1_kalla: complex,
    impedans_2_mottagare: complex,
    viktigast: Viktigast,
) -> Dubbeldiffraktion:
    """Funktionen p2edge med D̂₁ och D̂₂ redovisade, AV 1106/07 avsnitt 5.7.4, ekv. (100)–(104).

    Skärmens ovansida mellan T₁ och T₂ är mottagarsidans yta i den första kilen och
    källsidans yta i den andra och räknas som hård (Z = ∞ i ekv. 102–103). Z_1S är
    impedansen för skärmens yta mot källan och Z_2R för ytan mot mottagaren. Faktorn 0,5
    väger upp att speglingen i ovansidan kommer med i båda diffraktionskoefficienterna.
    Efter 2018 ska delmodell 5 använda `tjock_skarm_tryck_reflektionskoefficienter_delar`
    (TC-101327 avsnitt 2.3).
    """

    def d_1(kil: Kil) -> complex:
        return kildiffraktionskoefficient_impedans(frekvens_hz, kil, impedans_1_kalla, math.inf)

    def d_2(kil: Kil) -> complex:
        return kildiffraktionskoefficient_impedans(frekvens_hz, kil, math.inf, impedans_2_mottagare)

    return dubbel_diffraktion(frekvens_hz, geometri, d_1, d_2, viktigast, FAKTOR_TJOCK_SKARM)


def tjock_skarm_tryck_impedans(
    frekvens_hz: float,
    geometri: TvaKanter,
    impedans_1_kalla: complex,
    impedans_2_mottagare: complex,
    viktigast: Viktigast,
) -> complex:
    """Funktionen p2edge, AV 1106/07 avsnitt 5.7.4, ekv. (104): trycket p̂ vid mottagaren."""
    return tjock_skarm_tryck_impedans_delar(
        frekvens_hz, geometri, impedans_1_kalla, impedans_2_mottagare, viktigast
    ).tryck


def tjock_skarm_tryck_reflektionskoefficienter_delar(
    frekvens_hz: float,
    geometri: TvaKanter,
    q_1_kalla: complex,
    q_mitten: complex,
    q_2_mottagare: complex,
    viktigast: Viktigast,
) -> Dubbeldiffraktion:
    """p2edge med givna reflektionskoefficienter, TC-101327 avsnitt 2.3 och bilaga G.3.

    AV 1106/07 ekv. (100)–(103) med Dwedge räknad ur färdiga koefficienter. `q_mitten` gäller
    ovansidan mellan T₁ och T₂ och går in både som Q_R i den första kilen och som Q_S i den
    andra; med `q_mitten = 1` är det den hårda ovansidan i ekv. (102)–(103). I delmodell 5 är
    värdena Q_avS, Q'_avM och Q_avR ur bilaga G.3 (räknas i delmodellpaketet). Faktorn 0,5
    är oförändrad.
    """

    def d_1(kil: Kil) -> complex:
        return kildiffraktionskoefficient_reflektionskoefficienter(
            frekvens_hz, kil, q_1_kalla, q_mitten
        )

    def d_2(kil: Kil) -> complex:
        return kildiffraktionskoefficient_reflektionskoefficienter(
            frekvens_hz, kil, q_mitten, q_2_mottagare
        )

    return dubbel_diffraktion(frekvens_hz, geometri, d_1, d_2, viktigast, FAKTOR_TJOCK_SKARM)


def tjock_skarm_tryck_reflektionskoefficienter(
    frekvens_hz: float,
    geometri: TvaKanter,
    q_1_kalla: complex,
    q_mitten: complex,
    q_2_mottagare: complex,
    viktigast: Viktigast,
) -> complex:
    """Trycket p̂ ur `tjock_skarm_tryck_reflektionskoefficienter_delar` (p2edge, TC 2.3)."""
    return tjock_skarm_tryck_reflektionskoefficienter_delar(
        frekvens_hz, geometri, q_1_kalla, q_mitten, q_2_mottagare, viktigast
    ).tryck
