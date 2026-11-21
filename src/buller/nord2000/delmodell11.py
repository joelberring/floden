"""Delmodell 11 i Nord2000: reflexer mot vertikala ytor med ändlig storlek.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.20, ekv. (295)–(299), med indata enligt
avsnitt 5.3.4 (figur 1) och Fresnelzonen enligt avsnitt 5.8 och 5.23.4–5.23.5
(ekv. 338–344). TC-101327 (2019) ändrar inget i dessa avsnitt.

Reflexeffekten är ΔL₁₁ = 10·lg ρ_E + 20·lg(S_refl/S_Fz) (ekv. 296), där S_Fz är arean av
Fresnelzonen i reflektorns plan (den omskrivna rektangeln, F_λ = 1/8) och S_refl den del av
rektangeln som täcks av den "effektiva" reflektorn: reflektorn själv och, om den står över
mark, dess spegelbild i markytan.

Täckning:

- Vertikal reflektor och stråle som kan räknas som vågrät (avsnitt 5.20 sista stycket).
  Lutande reflektorer beskrivs inte i metoden och finns inte här.
- Reflexpunktens höjd z_O och strållängderna R₁, R₂ (ekv. 298) är indata (`Reflexgeometri`).
  För homogen atmosfär ger `reflexgeometri_rak_strale` dem ur en rät stråle. Med refraktion
  ska de komma ur RayCurvature och HeightOfCircularRay (avsnitt 5.23.9), som inte finns här.

Riktningar längs reflektorn: metoden talar om reflektorns vänstra och högra kant (d_r,l och
d_r,r) utan att säga från vilket håll. Här heter de "bakåt" och "framåt": framåt är den
riktning längs reflektorn som bildar vinkeln θ_r med strålens riktning S'→R i figur 1
(i figuren åt höger), bakåt den motsatta (vinkeln π − θ_r).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from buller.nord2000.frekvenser import vaglangd_m
from buller.nord2000.fresnel import calc_fzd
from buller.typer import BullerFel, Flyttal

# Andelen F_λ av våglängden för reflexer mot vertikala ytor, AV 1106/07 avsnitt 5.8 och 5.20.
F_LAMBDA_REFLEX = 1.0 / 8.0
# En reflektor räknas inte om reflexpunkten ligger mer än 2 m utanför en kant (avsnitt 5.20).
STORSTA_AVSTAND_UTANFOR_KANT_M = 2.0
# Strålen får räknas som vågrät när utbredningssträckan är mer än 5 gånger höjdskillnaden
# mellan källa och mottagare: AV 1849/00 avsnitt 6 (bakgrundsrapport). AV 1106/07 avsnitt 5.20
# anger bara d >> h_S + h_R utan tal.
STORSTA_STRALLUTNING = 1.0 / 5.0


@dataclass(frozen=True)
class Reflektor:
    """Reflektorns utsträckning kring reflexpunkten O, AV 1106/07 avsnitt 5.3.4.

    `kant_bakat_m` och `kant_framat_m` är d_r,l och d_r,r: avstånden längs reflektorn från O
    till kanterna (riktningarna förklaras i modulens dokumentation). Ett negativt värde
    betyder att O ligger utanför reflektorn, bortom den kanten. `z_ovre_m` och `z_nedre_m` är
    z_r,upp och z_r,low. `markhojd_m` är markytans höjd vid reflektorn, i samma höjdsystem;
    då räknas reflektorns spegelbild i marken in i den effektiva ytan (avsnitt 5.20).
    `None` betyder fritt upphängd reflektor utan spegelbild.
    """

    z_ovre_m: float
    z_nedre_m: float
    kant_bakat_m: float
    kant_framat_m: float
    markhojd_m: float | None = None

    def __post_init__(self) -> None:
        varden = (self.z_ovre_m, self.z_nedre_m, self.kant_bakat_m, self.kant_framat_m)
        if not all(math.isfinite(v) for v in varden):
            raise BullerFel("Reflektorns mått ska vara ändliga tal.")
        if self.z_ovre_m <= self.z_nedre_m:
            raise BullerFel("Reflektorns övre kant ska ligga över den nedre.")
        if self.kant_bakat_m + self.kant_framat_m <= 0.0:
            raise BullerFel("Reflektorns bredd (kant_bakat_m + kant_framat_m) ska vara > 0.")
        if self.markhojd_m is not None:
            if not math.isfinite(self.markhojd_m):
                raise BullerFel("Markhöjden vid reflektorn ska vara ett ändligt tal.")
            if self.z_nedre_m < self.markhojd_m:
                raise BullerFel("Reflektorns nedre kant får inte ligga under marken.")


@dataclass(frozen=True)
class Reflexgeometri:
    """Strålen i reflexpunkten O, AV 1106/07 avsnitt 5.20 (figur 32) och 5.3.4 (figur 1).

    `r1_m` och `r2_m` är strålens längd från O till källan resp. mottagaren (R₁, R₂ i
    ekv. 298), `theta_r_rad` vinkeln i plan mellan riktningen S'→R och reflektorns riktning
    (0 < θ_r < π) och `z_o_m` reflexpunktens höjd i samma höjdsystem som reflektorns kanter.
    """

    r1_m: float
    r2_m: float
    theta_r_rad: float
    z_o_m: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.r1_m) and math.isfinite(self.r2_m)):
            raise BullerFel("Strållängderna R1 och R2 ska vara ändliga.")
        if self.r1_m <= 0.0 or self.r2_m <= 0.0:
            raise BullerFel("Strållängderna R1 och R2 ska vara större än 0.")
        if not 0.0 < self.theta_r_rad < math.pi:
            raise BullerFel(
                "Vinkeln θ_r mot reflektorn ska ligga mellan 0 och π (strykande infall ger "
                "ingen reflex)."
            )
        if not math.isfinite(self.z_o_m):
            raise BullerFel("Reflexpunktens höjd ska vara ett ändligt tal.")


@dataclass(frozen=True)
class Fresnelrektangel:
    """Fresnelzonen i reflektorns plan: omskriven rektangel kring O (avsnitt 5.8 och 5.20)."""

    bakat_m: float  # från O längs reflektorn, riktningen π − θ_r mot strålen
    framat_m: float  # från O längs reflektorn, riktningen θ_r mot strålen
    halvhojd_m: float  # från O uppåt och nedåt

    @property
    def area_m2(self) -> float:
        """S_Fz i ekv. (296)."""
        return (self.bakat_m + self.framat_m) * 2.0 * self.halvhojd_m


def energireflektionskoefficient(absorptionsfaktor: Flyttal) -> Flyttal:
    """Energireflektionskoefficienten ρ_E = 1 − α per band, AV 1106/07 avsnitt 5.20, ekv. (297)."""
    if not bool(np.all((absorptionsfaktor >= 0.0) & (absorptionsfaktor <= 1.0))):
        raise BullerFel("Absorptionsfaktorn ska ligga mellan 0 och 1.")
    rho: Flyttal = 1.0 - absorptionsfaktor
    return rho


def vagda_vaderkoefficienter(
    delstrackor_m: Sequence[float], a_m_s: Sequence[float], b_per_s: Sequence[float]
) -> tuple[float, float]:
    """Väderkoefficienterna A och B för en reflexbana, AV 1106/07 avsnitt 5.20, ekv. (295).

    Delsträckornas värden vägs med sträckans längd d. Ekvationen är skriven för två
    delsträckor (före och efter reflexpunkten); avsnittet anger att samma princip gäller för
    fler reflexer.
    """
    if not (len(delstrackor_m) == len(a_m_s) == len(b_per_s)) or len(delstrackor_m) < 1:
        raise BullerFel("Ange lika många delsträckor, A-värden och B-värden (minst en).")
    if any(not (math.isfinite(d) and d > 0.0) for d in delstrackor_m):
        raise BullerFel("Reflexbanans delsträckor ska vara större än 0.")
    total = math.fsum(delstrackor_m)
    a = math.fsum(d * v for d, v in zip(delstrackor_m, a_m_s, strict=True)) / total
    b = math.fsum(d * v for d, v in zip(delstrackor_m, b_per_s, strict=True)) / total
    return a, b


def reflexgeometri_rak_strale(
    d1_m: float,
    d2_m: float,
    z_kalla_m: float,
    z_mottagare_m: float,
    theta_r_rad: float,
    *,
    storsta_lutning: float = STORSTA_STRALLUTNING,
) -> Reflexgeometri:
    """z_O, R₁ och R₂ för homogen atmosfär: gränsfallet ξ → 0 av ekv. (298), AV 1106/07 5.20.

    Strålen är den räta linjen från källan till mottagaren över den utvikta banan
    (figur 32), så reflexpunkten ligger på höjden z_S + (z_R − z_S)·d₁/(d₁ + d₂) och R₁, R₂
    är linjens delar. `d1_m` och `d2_m` är de vågräta avstånden källa–O och O–mottagare
    (avsnitt 5.3.4), höjderna anges i samma höjdsystem som reflektorns kanter.

    Metoden beskriver Fresnelzonen bara för en stråle som kan räknas som vågrät. Om
    |z_R − z_S|/(d₁ + d₂) överstiger `storsta_lutning` ges `BullerFel`; standardvärdet 1/5
    kommer ur AV 1849/00 avsnitt 6. Anroparen kan uttryckligen ange en annan gräns.
    """
    if not (math.isfinite(d1_m) and math.isfinite(d2_m)) or d1_m <= 0.0 or d2_m <= 0.0:
        raise BullerFel("Avstånden d1 och d2 till reflexpunkten ska vara större än 0.")
    d = d1_m + d2_m
    hojdskillnad = z_mottagare_m - z_kalla_m
    if abs(hojdskillnad) > storsta_lutning * d:
        raise BullerFel(
            f"Reflexbanan lutar {abs(hojdskillnad) / d:.3f} (höjdskillnad/sträcka), mer än "
            f"{storsta_lutning:.3f}: Fresnelzonen för en reflex är i Nord2000 bara beskriven "
            "för en stråle som kan räknas som vågrät (AV 1106/07 avsnitt 5.20)."
        )
    langd = math.hypot(d, hojdskillnad)
    return Reflexgeometri(
        r1_m=langd * d1_m / d,
        r2_m=langd * d2_m / d,
        theta_r_rad=theta_r_rad,
        z_o_m=z_kalla_m + hojdskillnad * d1_m / d,
    )


def reflexpunkt_godtas(reflektor: Reflektor, z_o_m: float) -> bool:
    """Villkoret i AV 1106/07 avsnitt 5.20: reflexpunkten högst 2 m utanför reflektorns kanter.

    Reflexpunkten är skärningen mellan linjen S'R och reflektorns plan och får därför ligga
    utanför den verkliga ytan. Prövas mot reflektorns fyra kanter: sidokanterna, överkanten
    och underkanten. För en reflektor som står på marken ligger O aldrig under underkanten.
    """
    grans = STORSTA_AVSTAND_UTANFOR_KANT_M
    return (
        reflektor.kant_bakat_m >= -grans
        and reflektor.kant_framat_m >= -grans
        and z_o_m <= reflektor.z_ovre_m + grans
        and z_o_m >= reflektor.z_nedre_m - grans
    )


def fresnelzon_reflektor(geometri: Reflexgeometri, f_lambda_m: float) -> Fresnelrektangel:
    """Fresnelzonens mått kring O i reflektorns plan, AV 1106/07 ekv. (299) och (341)–(343).

    Längs reflektorn är måtten d_FZ = CalcFZd(R₁, R₂, θ, F_λ·λ) med θ = θ_r (framåt) och
    θ = π − θ_r (bakåt); det är ekv. (299) och samma anrop som a₂ och a₁ i ekv. (341)–(342).
    Halva höjden är den omskrivna rektangelns, ekv. (343): CalcFZd i riktningen π/2 delat med
    roten ur 1 − ((a₂ − a₁)/(a₂ + a₁))². Ellipsens axlar antas vågräta och lodräta
    (avsnitt 5.20 sista stycket). `f_lambda_m` är produkten F_λ·λ.
    """
    if not (math.isfinite(f_lambda_m) and f_lambda_m > 0.0):
        raise BullerFel("F·λ ska vara större än 0.")
    r1, r2, theta = geometri.r1_m, geometri.r2_m, geometri.theta_r_rad
    bakat = calc_fzd(r1, r2, math.pi - theta, f_lambda_m)
    framat = calc_fzd(r1, r2, theta, f_lambda_m)
    vid_o = calc_fzd(r1, r2, math.pi / 2.0, f_lambda_m)
    snedhet = (framat - bakat) / (framat + bakat)
    halvhojd = math.sqrt(vid_o**2 / (1.0 - snedhet**2))
    return Fresnelrektangel(bakat_m=bakat, framat_m=framat, halvhojd_m=halvhojd)


def _overlapp(a1: float, a2: float, b1: float, b2: float) -> float:
    """Längden av snittet mellan intervallen [a1, a2] och [b1, b2]."""
    return max(0.0, min(a2, b2) - max(a1, b1))


def fresnelzonsandel(reflektor: Reflektor, geometri: Reflexgeometri, f_lambda_m: float) -> float:
    """Kvoten S_refl/S_Fz i ekv. (296), AV 1106/07 avsnitt 5.20 och 5.8.

    S_refl är arean av den effektiva reflektorn inom Fresnelrektangeln. Den effektiva
    reflektorn är reflektorn själv och, när `reflektor.markhojd_m` är angiven, dess spegelbild
    i markytan (höjdintervallet [2·z_mark − z_r,upp, 2·z_mark − z_r,low]). Kvoten ligger
    mellan 0 och 1.
    """
    zon = fresnelzon_reflektor(geometri, f_lambda_m)
    bredd = _overlapp(-zon.bakat_m, zon.framat_m, -reflektor.kant_bakat_m, reflektor.kant_framat_m)
    # Höjder räknas från O, så att zonen är [−b, b] och en helt täckt zon ger exakt kvoten 1.
    nedre = reflektor.z_nedre_m - geometri.z_o_m
    ovre = reflektor.z_ovre_m - geometri.z_o_m
    b = zon.halvhojd_m
    if reflektor.markhojd_m is None:
        hojd = _overlapp(-b, b, nedre, ovre)
    else:
        mark = reflektor.markhojd_m - geometri.z_o_m
        spegel_nedre, spegel_ovre = 2.0 * mark - ovre, 2.0 * mark - nedre
        if reflektor.z_nedre_m == reflektor.markhojd_m:
            # Reflektorn står på marken: ytan och spegelbilden är ett sammanhängande band.
            hojd = _overlapp(-b, b, spegel_nedre, ovre)
        else:
            hojd = _overlapp(-b, b, nedre, ovre) + _overlapp(-b, b, spegel_nedre, spegel_ovre)
    return (bredd / (zon.bakat_m + zon.framat_m)) * (hojd / (2.0 * b))


def storlekskorrektion_db(
    reflektor: Reflektor, geometri: Reflexgeometri, f_lambda_m: float
) -> float:
    """Andra termen i ekv. (296): 20·lg(S_refl/S_Fz), AV 1106/07 avsnitt 5.20.

    0 dB när reflektorn täcker hela Fresnelzonen, −∞ när ingen del av den effektiva
    reflektorn ligger i zonen (reflexen bidrar då inte).
    """
    andel = fresnelzonsandel(reflektor, geometri, f_lambda_m)
    return 20.0 * math.log10(andel) if andel > 0.0 else -math.inf


def reflexeffekt_db(
    frekvenser_hz: Flyttal,
    energireflektion: Flyttal,
    reflektor: Reflektor,
    geometri: Reflexgeometri,
    ljudhastighet_m_s: float,
) -> Flyttal:
    """Reflexeffekten ΔL₁₁ per band, AV 1106/07 avsnitt 5.20, ekv. (296), med F_λ = 1/8.

    `energireflektion` är ρ_E per band (0 ≤ ρ_E ≤ 1), samma form som `frekvenser_hz`.
    Resultatet är ΔL_r i ekv. (329)/(334) för en reflexbana med en reflex; för flera reflexer
    läggs effekterna ihop i dB, en per reflektor.

    Band utan bidrag får −∞: där ρ_E = 0, där ingen del av reflektorn ligger i Fresnelzonen,
    och alla band när reflexpunkten ligger mer än 2 m utanför en kant (`reflexpunkt_godtas`).
    """
    if energireflektion.shape != frekvenser_hz.shape:
        raise BullerFel("energireflektion ska ha ett värde per frekvens.")
    if not bool(np.all((energireflektion >= 0.0) & (energireflektion <= 1.0))):
        raise BullerFel("Energireflektionskoefficienten ska ligga mellan 0 och 1.")
    if not (math.isfinite(ljudhastighet_m_s) and ljudhastighet_m_s > 0.0):
        raise BullerFel("Ljudhastigheten ska vara större än 0.")
    resultat = np.full(frekvenser_hz.shape[0], -math.inf, dtype=np.float64)
    if not reflexpunkt_godtas(reflektor, geometri.z_o_m):
        return resultat
    for i, f_np in enumerate(frekvenser_hz):
        rho = float(energireflektion[i])
        if rho <= 0.0:
            continue
        f_lambda = F_LAMBDA_REFLEX * vaglangd_m(float(f_np), ljudhastighet_m_s)
        resultat[i] = 10.0 * math.log10(rho) + storlekskorrektion_db(reflektor, geometri, f_lambda)
    return resultat
