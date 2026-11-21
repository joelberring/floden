"""Delmodell 3 i Nord2000: icke-plan terräng utan skärm.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.12 (ekv. 134–156), 5.21.4 (ekv. 318–319) och
hjälpfunktionerna MinConcaveHeight (5.23.12, ekv. 373–375), SegmentVariables (5.23.15,
ekv. 382–383) och WedgeCross (5.23.19, ekv. 391–392). FORCE TC-101327 (2019) ändrar bara
två saker som rör avsnittet: 2.3 (ekv. 80 gäller efter 2018 enbart delmodell 3, alltså
`kildiffraktion.kiltryck_impedans`) och 2.7 (h''_SCR definieras av ekv. 22; storheten
används bara i refraktionsräkningen, ekv. 147 och 149).

Varje terrängsegment får en markeffekt ΔL_i(f) och en Fresnelzonsvikt w_i(f). Segmentet är
konkavt (delmodell 1 över segmentets förlängning), konvext (kildiffraktion över en
ekvivalent kil) eller ett övergångssegment (båda, sammanvägda). Summan vägs enligt ekv. (156).

Räta strålar (homogen atmosfär utan fluktuerande refraktion) räknas som tidigare: de
refraktionskorrigerade storheterna i ekv. (134) är då lika med de okorrigerade, Δθ_S = Δθ_R = 0
i ekv. (146), och R_S, R_R i ekv. (148) och (150) är de räta sträckorna med τ = R/c.

Med refraktion eller fluktuerande refraktion (P-S2, formlerna avlästa från sidbild s. 61–66):

* ekv. (134)–(135): Fresnelzonsvikten med krökta strålar (FresnelZoneW, ekv. 346–351) i
  profilen CalcEqSSP(h′_S, h′_R), och de modifierade storheterna d̆′, h̆′_S, h̆′_R, d̆_refl
  (ekv. 348) i segmentklassningen (ekv. 136–139);
* ekv. (140): delmodell 1 med refraktion för konkava segment (`delmodell1`);
* ekv. (147)–(150): strålarna till den ekvivalenta kilens topp med CalcEqSSP mot h″_SCR
  (ekv. 22, TC-101327 avsnitt 2.7) och DirectRay, och Δθ_S, Δθ_R i ekv. (146);
* ekv. (153)–(154): det konvexa segmentets Fresnelzonsvikt i profilen CalcEqSSP(h′_S, h′_R)
  med höjderna ur ekv. (152).

Val som avviker från eller fyller ut den tryckta texten står under "Tolkningar" i
`docs/buller/logg/P-3.md` och, för refraktionen, i `docs/buller/logg/P-S2.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from buller.nord2000.atmosfar import HomogenAtmosfar
from buller.nord2000.delmodell1 import Ct2Form, markeffekt_plan_mark_db
from buller.nord2000.delmodell4 import ekvivalent_skarmhojd_m
from buller.nord2000.fresnel import (
    Reflexgeometri,
    fresnelzon_langs_mark,
    fresnelzon_langs_mark_refraktion,
    reflexgeometri,
)
from buller.nord2000.geometri import MINSTA_HOJD_M, langd, normlinje
from buller.nord2000.kildiffraktion import Kil, kil_rata_stralar, kiltryck_impedans
from buller.nord2000.mark import markimpedans
from buller.nord2000.refraktion import Atmosfar, calc_eq_ssp
from buller.nord2000.refraktion_mark import Skuggzonslasning
from buller.nord2000.stralar_krokta import Uppatmetod, direkt_strale
from buller.nord2000.terrang import (
    Terranggeometri,
    ekvivalent_plan_mark,
    geometri_over_plan_mark,
    planhetsparameter_rf,
)
from buller.nord2000.terrang_skarmar import vertikalt_avstand_m
from buller.typer import BullerFel, Flyttal

F_LAMBDA_DELMODELL3 = 1.0 / 16.0  # AV 1106/07 avsnitt 5.8 och ekv. (134), (153), (374)

# Numeriskt skydd, inte en del av metoden: en höjd som ligger inom denna relativa marginal
# från gränsen i ekv. (136)–(137) räknas som lika med gränsen. Utan den blir ett segment på
# exakt plan mark ett "övergångssegment" med r = 1 − 10⁻¹⁶ genom avrundning i NormLine.
RELATIV_TOLERANS = 1.0e-9

Segmenttyp = Literal["konkavt", "konvext", "övergång"]
Fresnelzonsregel = Literal["klippt", "oklippt"]
# Höjderna i delmodell 1 för den konkava delen av ett övergångssegment (ekv. 140): "prim" är
# h′_S, h′_R som AV 1106/07 är tryckt (förval), "bis" ersätter en höjd under h″ med h″
# (AV 1849/00 avsnitt 4.4.2). Se tolkning 9 i loggen P-3 och utfallet i loggen P-S2.
Overgangshojd = Literal["prim", "bis"]

KONKAVT: Segmenttyp = "konkavt"
KONVEXT: Segmenttyp = "konvext"
OVERGANG: Segmenttyp = "övergång"


# ------------------------------------------------------------------------- hjälpfunktioner


@dataclass(frozen=True)
class Segmentvariabler:
    """Utdata från SegmentVariables, AV 1106/07 avsnitt 5.23.15, ekv. (382)."""

    d_m: float  # d': avståndet mellan fotpunkterna för S och R på segmentets förlängning
    h_s_m: float  # h'_S: källans teckensatta höjd över förlängningen
    h_r_m: float  # h'_R: mottagarens teckensatta höjd över förlängningen
    d1_m: float  # d'_1: teckensatt avstånd längs förlängningen från S:s fotpunkt till (x1, z1)
    d2_m: float  # d'_2: motsvarande till (x2, z2)


def _teckensatt_avstand(avstand_kalla_m: float, avstand_mottagare_m: float, d_m: float) -> float:
    """Tecknet på d'_1 och d'_2 enligt sista delen av AV 1106/07 ekv. (383)."""
    if avstand_kalla_m < avstand_mottagare_m and avstand_mottagare_m > d_m:
        return -avstand_kalla_m
    return avstand_kalla_m


def segmentvariabler(
    x_s_m: float,
    z_s_m: float,
    x_r_m: float,
    z_r_m: float,
    x1_m: float,
    z1_m: float,
    x2_m: float,
    z2_m: float,
) -> Segmentvariabler:
    """Funktionen SegmentVariables, AV 1106/07 avsnitt 5.23.15, ekv. (383).

    Segmentet går från (x1, z1) till (x2, z2) med x2 > x1. En ändpunkt som projiceras bakom
    källans fotpunkt får negativt avstånd.
    """
    if not x2_m > x1_m:
        raise BullerFel("SegmentVariables kräver x2 > x1.")
    xs0, zs0, h_s = normlinje(x1_m, z1_m, x2_m, z2_m, x_s_m, z_s_m)
    xr0, zr0, h_r = normlinje(x1_m, z1_m, x2_m, z2_m, x_r_m, z_r_m)
    d = langd(xs0, zs0, xr0, zr0)
    d1 = _teckensatt_avstand(langd(xs0, zs0, x1_m, z1_m), langd(xr0, zr0, x1_m, z1_m), d)
    d2 = _teckensatt_avstand(langd(xs0, zs0, x2_m, z2_m), langd(xr0, zr0, x2_m, z2_m), d)
    return Segmentvariabler(d_m=d, h_s_m=h_s, h_r_m=h_r, d1_m=d1, d2_m=d2)


def minsta_konkava_hojd_m(
    motstaende_hojd_m: float, d1_m: float, d_m: float, f_lambda_m: float
) -> float:
    """Funktionen MinConcaveHeight, AV 1106/07 avsnitt 5.23.12, ekv. (373)–(375), figur 40.

    Den källhöjd h_S,Fz vid vilken segmentets ändpunkt P_S (på avståndet `d1_m` från källans
    fotpunkt) ligger på Fresnelzonens gräns: |SP_S| + |P_S R'| − |SR'| = F_λ·λ, där R' är
    mottagarens spegelbild på höjden `motstaende_hojd_m` under marken och `d_m` = d₁ + d₂.
    `f_lambda_m` är produkten F_λ·λ. Returnerar `math.inf` när ekv. (374) är uppfylld.

    Samma funktion ger h_R,Fz med källa och mottagare ombytta (d₁ räknas då från mottagaren).
    """
    if not (motstaende_hojd_m >= 0.0 and d_m > 0.0 and f_lambda_m > 0.0):
        raise BullerFel(
            "MinConcaveHeight kräver höjd ≥ 0 samt avstånd och F·λ större än 0 "
            f"(fick h = {motstaende_hojd_m}, d = {d_m}, F·λ = {f_lambda_m})."
        )
    h_r = motstaende_hojd_m
    d2 = d_m - d1_m
    rot = math.sqrt(h_r * h_r + d2 * d2)
    if rot - h_r <= f_lambda_m:  # ekv. (374)
        return math.inf
    x1 = (rot - f_lambda_m) ** 2
    x2 = h_r * h_r + d_m * d_m - d1_m * d1_m - x1
    a = 4.0 * h_r * h_r - 4.0 * x1
    b = 4.0 * h_r * x2
    c = x2 * x2 - 4.0 * d1_m * d1_m * x1
    diskriminant = b * b - 4.0 * a * c
    hojd = (-b - math.sqrt(diskriminant)) / (2.0 * a) if diskriminant >= 0.0 else math.nan
    if hojd >= 0.0 and _pa_zongransen(hojd, h_r, d1_m, d_m, f_lambda_m):
        return hojd
    if d1_m < 0.0 or d1_m > d_m:
        # Ändpunkten ligger bakom källans fotpunkt eller bortom mottagarens (figur 40
        # förutsätter 0 ≤ d₁ ≤ d) och utanför zonen för varje höjd: ekv. (375) har då ingen
        # giltig rot. Se tolkning 4 i loggen P-3; fallet d₁ > d (branta segment, t.ex. en
        # skärms sidor, och starkt krökta strålar) är tillagt i P-S2 med samma skäl.
        return 0.0
    raise BullerFel(
        "MinConcaveHeight saknar giltig lösning (ekv. 375) för "
        f"h = {h_r}, d1 = {d1_m}, d = {d_m}, F·λ = {f_lambda_m}."
    )


def fresnelgransavvikelse_m(
    hojd_m: float, motstaende_hojd_m: float, d1_m: float, d_m: float, f_lambda_m: float
) -> float:
    """|SP_S| + |P_S R'| − |SR'| − F_λ·λ i figur 40 (AV 1849/00 bilaga E, ekv. E1).

    Noll när ändpunkten P_S ligger på Fresnelzonens gräns; positiv utanför zonen.
    """
    d2 = d_m - d1_m
    return (
        math.hypot(d1_m, hojd_m)
        + math.hypot(d2, motstaende_hojd_m)
        - math.hypot(d_m, hojd_m + motstaende_hojd_m)
        - f_lambda_m
    )


def _pa_zongransen(hojd_m: float, h_r: float, d1_m: float, d_m: float, f_lambda_m: float) -> bool:
    """Sant när roten ur ekv. (375) uppfyller den okvadrerade ekvationen (ingen falsk rot)."""
    skala = max(f_lambda_m, 1.0e-6 * (abs(d1_m) + d_m + hojd_m + h_r))
    return abs(fresnelgransavvikelse_m(hojd_m, h_r, d1_m, d_m, f_lambda_m)) <= 1.0e-6 * skala


def kilkryss(
    x1_m: float,
    z1_m: float,
    x2_m: float,
    z2_m: float,
    x3_m: float,
    z3_m: float,
    x4_m: float,
    z4_m: float,
) -> tuple[float, float]:
    """Funktionen WedgeCross, AV 1106/07 avsnitt 5.23.19, ekv. (391)–(392), figur 43.

    Toppen (x0, z0) på den ekvivalenta kil som bildas av två segment som inte ligger intill
    varandra: källsidans segment (x1, z1)–(x2, z2) och mottagarsidans (x3, z3)–(x4, z4).
    Förlängningarnas skärningspunkt används när den ligger mellan segmenten (s ≥ 1 och
    t ≥ 1); annars segmentändpunkten (x3, z3) när t < 1 och (x2, z2) när s < 1.

    D i ekv. (392) är tryckt med sista faktorn "(x4, z4)"; här används (z2 − z1), som i
    AV 1849/00 bilaga F ekv. (F1) och som följer av att lösa skärningen. Parallella segment
    (D = 0) ger `BullerFel`.
    """
    if not x1_m < x2_m <= x3_m < x4_m:
        raise BullerFel("WedgeCross kräver x1 < x2 ≤ x3 < x4.")
    d = (x2_m - x1_m) * (z4_m - z3_m) - (x4_m - x3_m) * (z2_m - z1_m)
    if d == 0.0:
        raise BullerFel("WedgeCross: segmenten är parallella och bildar ingen kil.")
    s = ((x4_m - x1_m) * (z4_m - z3_m) - (x4_m - x3_m) * (z4_m - z1_m)) / d
    t = ((x2_m - x1_m) * (z4_m - z1_m) - (x4_m - x1_m) * (z2_m - z1_m)) / d
    if s >= 1.0 and t >= 1.0:
        return x1_m + (x2_m - x1_m) * s, z1_m + (z2_m - z1_m) * s
    if t < 1.0:
        return x3_m, z3_m
    return x2_m, z2_m


def relativ_hojd(hojd_over_segment_m: float, minsta_konkava_hojd_m: float) -> float:
    """h_S,rel eller h_R,rel, AV 1106/07 avsnitt 5.12, ekv. (136)–(137).

    `hojd_over_segment_m` är h'_S (eller h'_R) och `minsta_konkava_hojd_m` är h''_S (h''_R).
    Inom `RELATIV_TOLERANS` från gränsen räknas höjden som lika med gränsen.
    """
    if hojd_over_segment_m <= 0.0:
        return 0.0
    if hojd_over_segment_m >= minsta_konkava_hojd_m * (1.0 - RELATIV_TOLERANS):
        return 1.0
    return hojd_over_segment_m / minsta_konkava_hojd_m


def _zonandel(
    d1_m: float, d2_m: float, start: float, slut: float, avstand_m: float, regel: Fresnelzonsregel
) -> float:
    """Andelen av Fresnelzonen [start, slut] som segmentet [d₁, d₂] täcker, ekv. (351).

    Med `regel="klippt"` begränsas zonen först till [0, d] (avsnitt 5.8).
    """
    if regel == "klippt":
        start, slut = max(start, 0.0), min(slut, avstand_m)
    elif regel != "oklippt":
        raise BullerFel(f"Okänd Fresnelzonsregel: {regel!r}.")
    # Fallindelningen i ekv. (351).
    if d1_m >= slut or d2_m <= start:
        return 0.0
    if d1_m <= start and d2_m >= slut:
        return 1.0
    if d1_m > start and d2_m < slut:
        return (d2_m - d1_m) / (slut - start)
    if d1_m <= start:
        return (d2_m - start) / (slut - start)
    return (slut - d1_m) / (slut - start)


def fresnelzonsvikt_delmodell3(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    d1_m: float,
    d2_m: float,
    f_lambda_m: float,
    regel: Fresnelzonsregel = "klippt",
) -> float:
    """FresnelZoneW för ξ = 0, AV 1106/07 avsnitt 5.23.6, ekv. (349)–(351), med regeln i 5.8.

    Med `regel="klippt"` (förval) begränsas zonen [d_1,Fz, d_2,Fz] till sträckan mellan
    källans och mottagarens fotpunkter, [0, d]: avsnitt 5.8 säger att bara den del av zonen
    som ligger mellan källa och mottagare tas med. `regel="oklippt"` är ekv. (350) utan den
    begränsningen (det `fresnel.fresnelzonsvikt` räknar). Se tolkning 1 i loggen.
    """
    zon = fresnelzon_langs_mark(avstand_m, kallhojd_m, mottagarhojd_m, f_lambda_m)
    return _zonandel(d1_m, d2_m, zon.d1_fz_m, zon.d2_fz_m, avstand_m, regel)


def fresnelzonsvikt_delmodell3_refraktion(
    avstand_m: float,
    geometri: Reflexgeometri,
    d1_m: float,
    d2_m: float,
    f_lambda_m: float,
    regel: Fresnelzonsregel = "klippt",
) -> float:
    """FresnelZoneW med krökta strålar, AV 1106/07 avsnitt 5.23.6, ekv. (346)–(351).

    `geometri` är första steget i FresnelZoneW (`fresnel.reflexgeometri`) för segmentets
    förlängning. Zonens gränser läggs kring den verkliga reflexpunkten (ekv. 350) och klipps
    med `regel="klippt"` till sträckan [0, d] som i `fresnelzonsvikt_delmodell3`.
    """
    zon = fresnelzon_langs_mark_refraktion(geometri, f_lambda_m)
    return _zonandel(d1_m, d2_m, zon.d1_fz_m, zon.d2_fz_m, avstand_m, regel)


# ------------------------------------------------------------------------- segmentklassning


def _segmentpunkter(geo: Terranggeometri, index: int) -> tuple[float, float, float, float]:
    antal = geo.x_m.shape[0] - 1
    if not 0 <= index < antal:
        raise BullerFel(f"Segmentindex {index} finns inte (profilen har {antal} segment).")
    return (
        float(geo.x_m[index]),
        float(geo.z_m[index]),
        float(geo.x_m[index + 1]),
        float(geo.z_m[index + 1]),
    )


def segmentvariabler_for(geo: Terranggeometri, index: int) -> Segmentvariabler:
    """SegmentVariables för terrängsegment nr `index` (0-baserat) i `geo`."""
    x1, z1, x2, z2 = _segmentpunkter(geo, index)
    return segmentvariabler(geo.x_s_m, geo.z_s_m, geo.x_r_m, geo.z_r_m, x1, z1, x2, z2)


@dataclass(frozen=True)
class Segmentklass:
    """Ett segments typ vid en frekvens, AV 1106/07 avsnitt 5.12, ekv. (136)–(139)."""

    h_s_rel: float
    h_r_rel: float
    h_s_fz_m: float  # h_S,Fz, `math.inf` när ekv. (374) är uppfylld
    h_r_fz_m: float

    @property
    def typ(self) -> Segmenttyp:
        """Konkavt när båda relativa höjderna är 1, konvext när någon är 0, annars övergång."""
        if self.h_s_rel == 0.0 or self.h_r_rel == 0.0:
            return KONVEXT
        if self.h_s_rel == 1.0 and self.h_r_rel == 1.0:
            return KONKAVT
        return OVERGANG

    @property
    def r(self) -> float:
        """Andelen konkav lösning r = min(h_S,rel, h_R,rel), ekv. (155)."""
        return min(self.h_s_rel, self.h_r_rel)


def klassa_segment(geo: Terranggeometri, index: int, vaglangd_m: float) -> Segmentklass:
    """Relativa höjder och typ för ett segment vid en frekvens, AV 1106/07 ekv. (136)–(139).

    h''_S = min(h_S, h_S,Fz) och h''_R = min(h_R, h_R,Fz), med h_S,Fz och h_R,Fz ur
    MinConcaveHeight (F_λ = 1/16). För räta strålar är d_x i ekv. (138) lika med d'_1 och i
    ekv. (139) lika med d' − d'_2. Den motstående höjden i MinConcaveHeight är h'_R (h'_S),
    minst 0,01 m (regeln i FresnelZoneW, avsnitt 5.23.6; se tolkning 3 i loggen).
    """
    if not vaglangd_m > 0.0:
        raise BullerFel("Våglängden ska vara större än 0.")
    v = segmentvariabler_for(geo, index)
    if not v.d_m > 0.0:
        raise BullerFel(
            f"Segment {index}: källa och mottagare har samma fotpunkt på segmentets förlängning."
        )
    f_lambda = F_LAMBDA_DELMODELL3 * vaglangd_m
    h_s_fz = minsta_konkava_hojd_m(max(v.h_r_m, MINSTA_HOJD_M), v.d1_m, v.d_m, f_lambda)
    h_r_fz = minsta_konkava_hojd_m(max(v.h_s_m, MINSTA_HOJD_M), v.d_m - v.d2_m, v.d_m, f_lambda)
    return Segmentklass(
        h_s_rel=relativ_hojd(v.h_s_m, min(geo.h_s_m, h_s_fz)),
        h_r_rel=relativ_hojd(v.h_r_m, min(geo.h_r_m, h_r_fz)),
        h_s_fz_m=h_s_fz,
        h_r_fz_m=h_r_fz,
    )


def segmentrefraktion(
    variabler: Segmentvariabler,
    atmosfar: Atmosfar,
    uppat: Uppatmetod = "exakt",
    skuggzon: Skuggzonslasning | None = None,
) -> Reflexgeometri | None:
    """Reflexgeometrin med krökta strålar över ett segment, AV 1106/07 ekv. (134)–(135).

    (ξ, c₀) = CalcEqSSP(h′_S, h′_R, z₀, A, B, C) (ekv. 135) och första steget i FresnelZoneW
    (ekv. 346–347): R_S, R_R, ψ_G och d_refl, ur vilka d̆′, h̆′_S, h̆′_R och d̆_refl följer
    (ekv. 348). Returnerar `None` när ξ = 0 (räta strålar: de modifierade storheterna är lika
    med de omodifierade) och när källan eller mottagaren ligger på eller under segmentets
    förlängning: FresnelZoneW är då inte definierad, och avsnitt 5.12 sätter h̆′ = h′ för
    konvexa segment.
    """
    if not (variabler.h_s_m > 0.0 and variabler.h_r_m > 0.0 and variabler.d_m > 0.0):
        return None
    h_s = max(variabler.h_s_m, MINSTA_HOJD_M)
    h_r = max(variabler.h_r_m, MINSTA_HOJD_M)
    profil = calc_eq_ssp(h_s, h_r, atmosfar)
    if profil.xi_per_m == 0.0:
        return None
    radie = Skuggzonslasning().radie if skuggzon is None else skuggzon.radie
    return reflexgeometri(variabler.d_m, h_s, h_r, profil.xi_per_m, profil.c0_m_s, uppat, radie)


def klassa_segment_refraktion(
    geo: Terranggeometri,
    variabler: Segmentvariabler,
    geometri: Reflexgeometri,
    vaglangd_m: float,
) -> Segmentklass:
    """Relativa höjder med krökta strålar, AV 1106/07 avsnitt 5.12, ekv. (136)–(139).

    h̆′_S = R_S·sin ψ_G, h̆′_R = R_R·sin ψ_G, d̆′ = (R_S + R_R)·cos ψ_G och d̆_refl = R_S·cos ψ_G
    (ekv. 348). h_S,Fz = MinConcaveHeight(h̆′_R, d̆_x, d̆′, λ) med d̆_x = d_(i−1) − d_refl + d̆_refl
    (ekv. 138) och h_R,Fz = MinConcaveHeight(h̆′_S, d̆_x, d̆′, λ) med
    d̆_x = d′ − d_i + d_refl − d̆_refl (ekv. 139, som tryckt med det omodifierade d′).

    I skuggzon (ekv. 347) är ψ_G = 0, så h̆′_S = h̆′_R = 0 och båda relativa höjderna 0:
    segmentet räknas då som konvext med vikten 0 enligt ekv. (153). Se loggen P-S2.
    """
    if not vaglangd_m > 0.0:
        raise BullerFel("Våglängden ska vara större än 0.")
    psi = geometri.strykvinkel_rad
    h_s_mod = geometri.kallhojd_modifierad_m
    h_r_mod = geometri.mottagarhojd_modifierad_m
    if not (h_s_mod > 0.0 and h_r_mod > 0.0):
        return Segmentklass(h_s_rel=0.0, h_r_rel=0.0, h_s_fz_m=math.inf, h_r_fz_m=math.inf)
    d_mod = geometri.avstand_modifierat_m
    d_refl_mod = geometri.vag_kalla_m * math.cos(psi)
    f_lambda = F_LAMBDA_DELMODELL3 * vaglangd_m
    d_x_kalla = variabler.d1_m - geometri.d_refl_m + d_refl_mod
    d_x_mottagare = variabler.d_m - variabler.d2_m + geometri.d_refl_m - d_refl_mod
    h_s_fz = minsta_konkava_hojd_m(max(h_r_mod, MINSTA_HOJD_M), d_x_kalla, d_mod, f_lambda)
    h_r_fz = minsta_konkava_hojd_m(max(h_s_mod, MINSTA_HOJD_M), d_x_mottagare, d_mod, f_lambda)
    return Segmentklass(
        h_s_rel=relativ_hojd(h_s_mod, min(geo.h_s_m, h_s_fz)),
        h_r_rel=relativ_hojd(h_r_mod, min(geo.h_r_m, h_r_fz)),
        h_s_fz_m=h_s_fz,
        h_r_fz_m=h_r_fz,
    )


# --------------------------------------------------------------------------- konvexa segment


@dataclass(frozen=True)
class EkvivalentKil:
    """Den kil som ersätter ett konvext segment, AV 1106/07 avsnitt 5.12, ekv. (141)–(146)."""

    index_primar: int  # det betraktade segmentet
    index_sekundar: int  # segmentet som bestämmer den andra kilytan
    x_t_m: float
    z_t_m: float
    beta_rad: float
    theta_s_rad: float
    theta_r_rad: float
    r_s_m: float  # sträckan S–T
    r_r_m: float  # sträckan T–R


def _sekundart_segment(geo: Terranggeometri, index: int, mot_kallan: bool) -> int:
    """Närmaste segment vars förlängning ligger på eller under markpunkten under S (eller R).

    AV 1106/07 avsnitt 5.12: h'_Sv ≥ h_Sv (källsidan) respektive h'_Rv ≥ h_Rv (mottagarsidan),
    där h'_Sv är källans lodräta höjd över segmentets förlängning och h_Sv dess lodräta höjd
    över terrängen. h_Sv räknas över första segmentets förlängning, så första segmentet
    uppfyller alltid villkoret (motsvarande för sista segmentet och mottagaren).
    """
    antal = geo.x_m.shape[0] - 1
    if mot_kallan:
        x, z, kandidater = geo.x_s_m, geo.z_s_m, range(index - 1, -1, -1)
        referens = vertikalt_avstand_m(*_segmentpunkter(geo, 0), x, z)
    else:
        x, z, kandidater = geo.x_r_m, geo.z_r_m, range(index + 1, antal)
        referens = vertikalt_avstand_m(*_segmentpunkter(geo, antal - 1), x, z)
    for j in kandidater:
        hojd = vertikalt_avstand_m(*_segmentpunkter(geo, j), x, z)
        if hojd >= referens * (1.0 - RELATIV_TOLERANS):
            return j
    sida = "källan" if mot_kallan else "mottagaren"
    raise BullerFel(
        f"Segment {index}: inget segment mot {sida} kan bilda den sekundära kilytan "
        "(AV 1106/07 avsnitt 5.12)."
    )


def ekvivalent_kil(geo: Terranggeometri, index: int, sekundar_mot_kallan: bool) -> EkvivalentKil:
    """Kilen för ett konvext segment, AV 1106/07 avsnitt 5.12, ekv. (141)–(146) med ξ = 0.

    `sekundar_mot_kallan` är sant när h_S,rel < h_R,rel: den sekundära kilytan söks då mellan
    källan och segmentet, annars mellan segmentet och mottagaren. Ligger segmenten intill
    varandra är toppen deras gemensamma punkt, annars ges den av WedgeCross.

    β₂ i ekv. (146) är tryckt med (x2, z2); här används mottagarsidans yttre punkt (x4, z4),
    se tolkning 5 i loggen. Utan refraktion är Δθ_S = Δθ_R = 0, och R_S, R_R är de räta
    sträckorna från S och R till toppen (ekv. 142–145, 148 och 150 med ξ = 0).
    """
    j = _sekundart_segment(geo, index, sekundar_mot_kallan)
    forsta, andra = (j, index) if sekundar_mot_kallan else (index, j)
    x1, z1, x2, z2 = _segmentpunkter(geo, forsta)
    x3, z3, x4, z4 = _segmentpunkter(geo, andra)
    if andra == forsta + 1:
        x_t, z_t = x2, z2
    else:
        x_t, z_t = kilkryss(x1, z1, x2, z2, x3, z3, x4, z4)
    if not (x1 < x_t < x4 and geo.x_s_m < x_t < geo.x_r_m):
        raise BullerFel(
            f"Segment {index}: kilens topp (x = {x_t:.3f} m) ligger inte mellan kilytornas "
            "ändpunkter och mellan källa och mottagare; ekv. (146) är inte definierad."
        )
    halv_pi = math.pi / 2.0
    beta_1 = math.atan((z1 - z_t) / (x_t - x1)) + halv_pi
    beta_2 = math.atan((z4 - z_t) / (x4 - x_t)) + halv_pi
    theta_1 = math.atan((geo.z_s_m - z_t) / (x_t - geo.x_s_m)) + halv_pi
    theta_2 = math.atan((geo.z_r_m - z_t) / (geo.x_r_m - x_t)) + halv_pi
    return EkvivalentKil(
        index_primar=index,
        index_sekundar=j,
        x_t_m=x_t,
        z_t_m=z_t,
        beta_rad=2.0 * math.pi - beta_1 - beta_2,
        theta_s_rad=2.0 * math.pi - theta_1 - beta_2,
        theta_r_rad=theta_2 - beta_2,
        r_s_m=langd(geo.x_s_m, geo.z_s_m, x_t, z_t),
        r_r_m=langd(geo.x_r_m, geo.z_r_m, x_t, z_t),
    )


def markeffekt_konvext_segment_db(
    frekvens_hz: float,
    kil: EkvivalentKil,
    r_sr_m: float,
    flodesresistivitet_kNsm4: float,
    ljudhastighet_m_s: float,
) -> float:
    """ΔL_i för ett konvext segment, AV 1106/07 avsnitt 5.12, ekv. (151).

    ΔL_i = 20·lg(|p|·R_SR) med p ur pwedge (avsnitt 5.7.1, ekv. 80 enligt TC-101327 2.3) och
    segmentets impedans på båda kilytorna.
    """
    impedans = markimpedans(frekvens_hz, flodesresistivitet_kNsm4)
    indata = kil_rata_stralar(
        kil.beta_rad, kil.theta_s_rad, kil.theta_r_rad, kil.r_s_m, kil.r_r_m, ljudhastighet_m_s
    )
    tryck = abs(kiltryck_impedans(frekvens_hz, indata, impedans, impedans))
    if not tryck > 0.0:
        raise BullerFel(f"Segment {kil.index_primar}: kiltrycket är noll vid {frekvens_hz:.1f} Hz.")
    return 20.0 * math.log10(tryck * r_sr_m)


def kil_med_refraktion(
    geo: Terranggeometri,
    kil: EkvivalentKil,
    atmosfar: Atmosfar,
    uppat: Uppatmetod = "exakt",
) -> Kil:
    """Indata till pwedge för ett konvext segment med krökta strålar, ekv. (142)–(150).

    h′_S1 och d′₁ är källans höjd över källsidans kilyta och avståndet längs ytan från källans
    fotpunkt till toppen (ekv. 142–143); h′_R2 och d′₂ motsvarande för mottagaren (ekv.
    144–145). (ξ_S, c_0S) = CalcEqSSP(h′_S1, h″_SCR) och (ξ_R, c_0R) = CalcEqSSP(h″_SCR, h′_R2)
    med h″_SCR ur ekv. (22) (TC-101327 avsnitt 2.7); DirectRay(d′₁, h′_S1, 0, ξ_S, c_0S) ger
    τ_S, R_S, Δθ_S och DirectRay(d′₂, 0, h′_R2, ξ_R, c_0R) ger τ_R, R_R, Δθ_R (ekv. 148, 150;
    ekv. 150 är tryckt med ξ_S, c_0S). θ_S = 2π − θ₁ − β₂ − Δθ_S och θ_R = θ₂ − β₂ + Δθ_R
    (ekv. 146).

    En källa eller mottagare under sin kilytas förlängning (h′ < 0, som för det konvexa
    segmentets egen yta) räknas med beloppet av höjden: för ξ = 0 ger det den räta sträckan
    till toppen, som i den homogena räkningen. Se loggen P-S2, Tolkningar.
    """
    forsta = min(kil.index_primar, kil.index_sekundar)
    andra = max(kil.index_primar, kil.index_sekundar)
    x1, z1, _, _ = _segmentpunkter(geo, forsta)
    _, _, x4, z4 = _segmentpunkter(geo, andra)
    xs0, zs0, h_s1 = normlinje(x1, z1, kil.x_t_m, kil.z_t_m, geo.x_s_m, geo.z_s_m)  # ekv. (142)
    d1 = langd(xs0, zs0, kil.x_t_m, kil.z_t_m)  # ekv. (143)
    xr0, zr0, h_r2 = normlinje(kil.x_t_m, kil.z_t_m, x4, z4, geo.x_r_m, geo.z_r_m)  # ekv. (144)
    d2 = langd(xr0, zr0, kil.x_t_m, kil.z_t_m)  # ekv. (145)
    if not (d1 > 0.0 and d2 > 0.0):
        raise BullerFel(
            f"Segment {kil.index_primar}: källans eller mottagarens fotpunkt på kilytan "
            "sammanfaller med kilens topp; ekv. (148) och (150) är inte definierade."
        )
    h_s1, h_r2 = abs(h_s1), abs(h_r2)
    h_bis = ekvivalent_skarmhojd_m(geo.r_sr_m, kil.r_s_m, kil.r_r_m)  # ekv. (22)
    profil_s = calc_eq_ssp(h_s1, h_bis, atmosfar)  # ekv. (147)
    profil_r = calc_eq_ssp(h_bis, h_r2, atmosfar)  # ekv. (149)
    strale_s = direkt_strale(d1, h_s1, 0.0, profil_s.xi_per_m, profil_s.c0_m_s, uppat)
    strale_r = direkt_strale(d2, 0.0, h_r2, profil_r.xi_per_m, profil_r.c0_m_s, uppat)
    return Kil(
        beta_rad=kil.beta_rad,
        theta_s_rad=kil.theta_s_rad - strale_s.vinkelandring_rad,
        theta_r_rad=kil.theta_r_rad + strale_r.vinkelandring_rad,
        gangtid_s=strale_s.gangtid_s + strale_r.gangtid_s,
        gangtid_kalla_s=strale_s.gangtid_s,
        gangtid_mottagare_s=strale_r.gangtid_s,
        vag_m=strale_s.vagstracka_m + strale_r.vagstracka_m,
        vag_kalla_m=strale_s.vagstracka_m,
        vag_mottagare_m=strale_r.vagstracka_m,
    )


def markeffekt_konvext_segment_refraktion_db(
    frekvens_hz: float, kil: Kil, index: int, r_sr_m: float, flodesresistivitet_kNsm4: float
) -> float:
    """ΔL_i för ett konvext segment med krökta strålar, AV 1106/07 ekv. (151).

    `kil` kommer ur `kil_med_refraktion`. Som `markeffekt_konvext_segment_db` i övrigt.
    """
    impedans = markimpedans(frekvens_hz, flodesresistivitet_kNsm4)
    tryck = abs(kiltryck_impedans(frekvens_hz, kil, impedans, impedans))
    if not tryck > 0.0:
        raise BullerFel(f"Segment {index}: kiltrycket är noll vid {frekvens_hz:.1f} Hz.")
    return 20.0 * math.log10(tryck * r_sr_m)


def konvex_fresnelgeometri(geo: Terranggeometri, index: int) -> Segmentvariabler:
    """Geometrin för ett konvext segments Fresnelzonsvikt, AV 1106/07 ekv. (152), figur 19.

    Segmentet projiceras på terrängens baslinje P_S–P_R, eller på en linje parallell med den
    genom segmentets högsta ändpunkt när den ligger under baslinjen. Returnerar d', h'_S,
    h'_R, d'_(i−1) och d'_i i samma form som SegmentVariables.
    """
    x_ps, z_ps = (
        geo.x_s_m,
        geo.z_s_m - vertikalt_avstand_m(*_segmentpunkter(geo, 0), geo.x_s_m, geo.z_s_m),
    )
    sista = geo.x_m.shape[0] - 2
    x_pr, z_pr = (
        geo.x_r_m,
        geo.z_r_m - vertikalt_avstand_m(*_segmentpunkter(geo, sista), geo.x_r_m, geo.z_r_m),
    )
    x1, z1, x2, z2 = _segmentpunkter(geo, index)
    xs0, zs0, h_sb = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_s_m, geo.z_s_m)
    xr0, zr0, h_rb = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_r_m, geo.z_r_m)
    x1p, z1p, h1 = normlinje(x_ps, z_ps, x_pr, z_pr, x1, z1)
    x2p, z2p, h2 = normlinje(x_ps, z_ps, x_pr, z_pr, x2, z2)
    h_m = max(h1, h2)
    delta_h = abs(h_m) if h_m < 0.0 else 0.0
    return Segmentvariabler(
        d_m=langd(xs0, zs0, xr0, zr0),
        h_s_m=h_sb + delta_h,
        h_r_m=h_rb + delta_h,
        d1_m=langd(xs0, zs0, x1p, z1p),
        d2_m=langd(xs0, zs0, x2p, z2p),
    )


# ------------------------------------------------------------------------------ delmodellen


@dataclass(frozen=True)
class Delmodell3Resultat:
    """Delmodell 3 per tersband, med segmentens delresultat (form (antal segment, antal band)).

    `vikt` är w_i(f) före normeringen i ekv. (156) och `markeffekt_segment_db` är ΔL_i(f)
    (för övergångssegment kvoten mellan högerledet i ekv. 155 och w_i; 0 när w_i = 0).
    `konkav_vikt` är w_con,i(f) i ekv. (318).
    """

    frekvenser_hz: Flyttal
    markeffekt_db: Flyttal  # ΔL₃(f)
    markeffekt_fore_modifiering_db: Flyttal  # ΔL₀(f)
    vikt: Flyttal
    markeffekt_segment_db: Flyttal
    konkav_vikt: Flyttal
    h_s_rel: Flyttal
    h_r_rel: Flyttal
    # Besked per band när en kil inte gick att bilda och felet sköts upp (annars None).
    fel: tuple[str | None, ...] = ()

    @property
    def summa_konkav_vikt(self) -> Flyttal:
        """w_con(f) = Σ w_con,i(f), AV 1106/07 ekv. (318)."""
        summa: Flyttal = np.sum(self.konkav_vikt, axis=0)
        return summa

    def segmenttyper(self, band: int) -> list[Segmenttyp]:
        """Segmentens typ i tersband nr `band` (0-baserat)."""
        return [
            Segmentklass(float(s), float(r), math.inf, math.inf).typ
            for s, r in zip(self.h_s_rel[:, band], self.h_r_rel[:, band], strict=True)
        ]


def vag_samman_segment_db(vikter: Flyttal, markeffekter_db: Flyttal) -> tuple[float, float]:
    """Den modifierade Fresnelzonsinterpolationen, AV 1106/07 avsnitt 5.12, ekv. (156).

    Returnerar (ΔL₀, ΔL₃) för en frekvens. Vikterna normeras till summan 2 när summan
    överstiger 2; för ΔL₀ < 0 går resultatet gradvis från vägd summa till vägt medelvärde
    (helt vid ΔL₀ ≤ −20 dB).
    """
    w_t = float(np.sum(vikter))
    w_prim = vikter * (2.0 / w_t) if w_t > 2.0 else vikter
    dl0 = float(np.sum(w_prim * markeffekter_db))
    if dl0 >= 0.0:
        return dl0, dl0
    r_prim = 1.0 if dl0 <= -20.0 else -dl0 / 20.0
    summa = float(np.sum(w_prim))
    if not summa > 0.0:
        raise BullerFel("Ekv. (156): viktsumman är 0 fast ΔL₀ < 0.")
    return dl0, dl0 * (1.0 - r_prim + r_prim / summa)


def _homogen(atmosfar: HomogenAtmosfar | Atmosfar) -> HomogenAtmosfar | None:
    """Atmosfären som `HomogenAtmosfar` när den saknar refraktion och fluktuationer, annars None."""
    if isinstance(atmosfar, HomogenAtmosfar):
        return atmosfar
    if atmosfar.har_refraktion or atmosfar.har_fluktuerande_refraktion:
        return None
    return HomogenAtmosfar(
        temperatur_c=atmosfar.temperatur_mark_c,
        relativ_fuktighet_procent=atmosfar.relativ_fuktighet_procent,
        lufttryck_kpa=atmosfar.lufttryck_kpa,
        cv2=atmosfar.cv2,
        ct2=atmosfar.ct2,
    )


def delmodell3(
    frekvenser_hz: Flyttal,
    geo: Terranggeometri,
    atmosfar: HomogenAtmosfar | Atmosfar,
    fresnelzon: Fresnelzonsregel = "klippt",
    *,
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    skuggzon: Skuggzonslasning | None = None,
    uppskjut_fel: bool = False,
    overgangshojd: Overgangshojd = "prim",
) -> Delmodell3Resultat:
    """Delmodell 3 med alla delresultat, AV 1106/07 avsnitt 5.12, ekv. (134)–(156).

    `geo` kommer ur `terrang.terranggeometri(profil)`. Segmentens flödesresistivitet och
    råhet tas ur `geo`. Utan refraktion och fluktuerande refraktion räknas räta strålar; annars
    krökta strålar enligt modulens docstring, med `uppat`, `ct2_form` och `skuggzon` som i
    `delmodell1.markeffekt_plan_mark_db`.

    Ett konvext segment eller övergångssegment vars ekvivalenta kil inte går att bilda ger
    `BullerFel`. Med `uppskjut_fel=True` avbryts inte räkningen: ΔL₃ blir då NaN i de band
    där felet uppstod och beskedet sparas i `Delmodell3Resultat.fel`, så att sammansättningen
    kan avgöra om bandet alls används (r_flat < 1 och r_scr1 < 1) innan den ger felet vidare.
    Vikterna, de relativa höjderna och w_con räknas alltid. `overgangshojd` väljer höjderna i
    ekv. (140) för övergångssegment (`Overgangshojd`).
    """
    if overgangshojd not in ("prim", "bis"):
        raise BullerFel(f"Okänt val för höjderna i ekv. (140): {overgangshojd!r} (prim eller bis).")
    luft = _homogen(atmosfar)
    bryter = luft is None
    atm = atmosfar if isinstance(atmosfar, Atmosfar) else None
    c = atmosfar.c_m_s if isinstance(atmosfar, Atmosfar) else atmosfar.ljudhastighet_m_s
    antal_segment = geo.x_m.shape[0] - 1
    antal_band = frekvenser_hz.shape[0]
    vikt = np.zeros((antal_segment, antal_band), dtype=np.float64)
    effekt = np.zeros((antal_segment, antal_band), dtype=np.float64)
    konkav_vikt = np.zeros((antal_segment, antal_band), dtype=np.float64)
    h_s_rel = np.zeros((antal_segment, antal_band), dtype=np.float64)
    h_r_rel = np.zeros((antal_segment, antal_band), dtype=np.float64)
    fel: list[str | None] = [None] * antal_band

    for i in range(antal_segment):
        sigma = float(geo.flodesresistivitet_kNsm4[i])
        rahet = float(geo.rahet_m[i])
        v = segmentvariabler_for(geo, i)
        konkav_effekt: Flyttal | None = None  # ekv. (140), räknas bara om något band behöver den
        kilar: dict[bool, EkvivalentKil] = {}
        krokta_kilar: dict[bool, Kil] = {}
        konvex_geometri: Segmentvariabler | None = None
        konvex_reflex: Reflexgeometri | None = None
        # Ekv. (134)–(135): krökta strålar över segmentets förlängning (None = räta).
        reflex = segmentrefraktion(v, atm, uppat, skuggzon) if bryter and atm is not None else None
        for b in range(antal_band):
            f = float(frekvenser_hz[b])
            f_lambda = F_LAMBDA_DELMODELL3 * c / f
            if reflex is None:
                klass = klassa_segment(geo, i, c / f)
            else:
                klass = klassa_segment_refraktion(geo, v, reflex, c / f)
            h_s_rel[i, b], h_r_rel[i, b] = klass.h_s_rel, klass.h_r_rel
            r = klass.r

            w_konkav, dl_konkav = 0.0, 0.0
            if r > 0.0:
                if reflex is None:
                    w_konkav = fresnelzonsvikt_delmodell3(
                        v.d_m, v.h_s_m, v.h_r_m, v.d1_m, v.d2_m, f_lambda, fresnelzon
                    )  # ekv. (134)
                else:
                    w_konkav = fresnelzonsvikt_delmodell3_refraktion(
                        v.d_m, reflex, v.d1_m, v.d2_m, f_lambda, fresnelzon
                    )  # ekv. (134) med ekv. (346)–(350)
                if w_konkav > 0.0:
                    if konkav_effekt is None:
                        if luft is not None:
                            konkav_effekt = markeffekt_plan_mark_db(
                                frekvenser_hz, v.d_m, v.h_s_m, v.h_r_m, sigma, rahet, luft
                            )
                        else:
                            konkav_effekt = markeffekt_plan_mark_db(
                                frekvenser_hz,
                                v.d_m,
                                v.h_s_m,
                                v.h_r_m,
                                sigma,
                                rahet,
                                atmosfar,
                                uppat,
                                ct2_form,
                                skuggzon,
                            )  # ekv. (140)
                    dl_konkav = float(konkav_effekt[b])
                    if overgangshojd == "bis" and r < 1.0:
                        # AV 1849/00 avsnitt 4.4.2: h′ ersätts med h″ när h′ < h″.
                        h_s_bis = max(v.h_s_m, min(geo.h_s_m, klass.h_s_fz_m))
                        h_r_bis = max(v.h_r_m, min(geo.h_r_m, klass.h_r_fz_m))
                        dl_konkav = float(
                            markeffekt_plan_mark_db(
                                frekvenser_hz[b : b + 1],
                                v.d_m,
                                h_s_bis,
                                h_r_bis,
                                sigma,
                                rahet,
                                atmosfar if luft is None else luft,
                                uppat,
                                ct2_form,
                                skuggzon,
                            )[0]
                        )

            w_konvex, dl_konvex = 0.0, 0.0
            if r < 1.0:
                if konvex_geometri is None:
                    konvex_geometri = konvex_fresnelgeometri(geo, i)
                    if bryter and atm is not None:
                        konvex_reflex = segmentrefraktion(
                            konvex_geometri, atm, uppat, skuggzon
                        )  # ekv. (154)
                g = konvex_geometri
                if konvex_reflex is None:
                    w_x = fresnelzonsvikt_delmodell3(
                        g.d_m, g.h_s_m, g.h_r_m, g.d1_m, g.d2_m, f_lambda, fresnelzon
                    )
                else:
                    w_x = fresnelzonsvikt_delmodell3_refraktion(
                        g.d_m, konvex_reflex, g.d1_m, g.d2_m, f_lambda, fresnelzon
                    )
                w_konvex = w_x * max(klass.h_s_rel, klass.h_r_rel)  # ekv. (153)
                if w_konvex > 0.0:
                    mot_kallan = klass.h_s_rel < klass.h_r_rel
                    try:
                        if mot_kallan not in kilar:
                            kilar[mot_kallan] = ekvivalent_kil(geo, i, mot_kallan)
                        if atm is not None and atm.har_refraktion:
                            if mot_kallan not in krokta_kilar:
                                krokta_kilar[mot_kallan] = kil_med_refraktion(
                                    geo, kilar[mot_kallan], atm, uppat
                                )
                            dl_konvex = markeffekt_konvext_segment_refraktion_db(
                                f, krokta_kilar[mot_kallan], i, geo.r_sr_m, sigma
                            )
                        else:
                            dl_konvex = markeffekt_konvext_segment_db(
                                f, kilar[mot_kallan], geo.r_sr_m, sigma, c
                            )
                    except BullerFel as kilfel:
                        if not uppskjut_fel:
                            raise
                        if fel[b] is None:
                            fel[b] = f"segment {i} (konvex del, vikt {w_konvex:.3g}): {kilfel}"
                        dl_konvex = math.nan

            # Ekv. (155); för konkava (r = 1) och konvexa (r = 0) segment återstår en term.
            vikt[i, b] = w_konkav * r + w_konvex * (1.0 - r)
            produkt = w_konkav * dl_konkav * r + w_konvex * dl_konvex * (1.0 - r)
            effekt[i, b] = produkt / vikt[i, b] if vikt[i, b] > 0.0 else 0.0
            konkav_vikt[i, b] = w_konkav * r

    dl0 = np.empty(antal_band, dtype=np.float64)
    dl3 = np.empty(antal_band, dtype=np.float64)
    for b in range(antal_band):
        if fel[b] is not None:
            dl0[b], dl3[b] = math.nan, math.nan
        else:
            dl0[b], dl3[b] = vag_samman_segment_db(vikt[:, b], effekt[:, b])
    return Delmodell3Resultat(
        frekvenser_hz=np.array(frekvenser_hz, dtype=np.float64),
        markeffekt_db=dl3,
        markeffekt_fore_modifiering_db=dl0,
        vikt=vikt,
        markeffekt_segment_db=effekt,
        konkav_vikt=konkav_vikt,
        h_s_rel=h_s_rel,
        h_r_rel=h_r_rel,
        fel=tuple(fel),
    )


def markeffekt_icke_plan_terrang_db(
    frekvenser_hz: Flyttal,
    geo: Terranggeometri,
    atmosfar: HomogenAtmosfar | Atmosfar,
    fresnelzon: Fresnelzonsregel = "klippt",
) -> Flyttal:
    """ΔL₃(f) per tersband, AV 1106/07 avsnitt 5.12, ekv. (156)."""
    return delmodell3(frekvenser_hz, geo, atmosfar, fresnelzon).markeffekt_db


# ----------------------------------------------------------------- övergången plan/icke-plan


def planhetsparameter_rflat(r_f: float, summa_konkav_vikt: float) -> float:
    """Övergångsparametern r_flat(f), AV 1106/07 avsnitt 5.21.4, ekv. (318).

    `r_f` är planhetsparametern i ekv. (317) och `summa_konkav_vikt` är w_con: summan av
    delmodell 3:s vikter för konkava segment och den konkava delen av övergångssegmenten.
    """
    if summa_konkav_vikt <= 1.05:
        return r_f
    if summa_konkav_vikt < 1.4:
        return (1.4 - summa_konkav_vikt) / 0.35 * r_f
    return 0.0


def overgang_rf(frekvenser_hz: Flyttal, geo: Terranggeometri, ljudhastighet_m_s: float) -> Flyttal:
    """r_f(f) för profilen, AV 1106/07 ekv. (316)–(317), med ekvivalent plan mark ur 5.21.5."""
    plan = ekvivalent_plan_mark(geo.x_m, geo.z_m)
    over_plan = geometri_over_plan_mark(geo, plan)
    return np.array(
        [
            planhetsparameter_rf(float(f), over_plan, plan.delta_h_m, ljudhastighet_m_s)
            for f in frekvenser_hz
        ],
        dtype=np.float64,
    )


def overgang_rflat(
    resultat: Delmodell3Resultat, geo: Terranggeometri, ljudhastighet_m_s: float
) -> Flyttal:
    """r_flat(f) per tersband ur delmodell 3:s vikter, AV 1106/07 ekv. (317)–(318)."""
    r_f = overgang_rf(resultat.frekvenser_hz, geo, ljudhastighet_m_s)
    w_con = resultat.summa_konkav_vikt
    return np.array(
        [planhetsparameter_rflat(float(a), float(w)) for a, w in zip(r_f, w_con, strict=True)],
        dtype=np.float64,
    )


def markeffekt_utan_skarm_db(
    r_flat: Flyttal, markeffekt_plan_db: Flyttal, markeffekt_delmodell3_db: Flyttal
) -> Flyttal:
    """ΔL_noscr = r_flat·ΔL_flat + (1 − r_flat)·ΔL₃, AV 1106/07 avsnitt 5.21.4, ekv. (319)."""
    if not (r_flat.shape == markeffekt_plan_db.shape == markeffekt_delmodell3_db.shape):
        raise BullerFel("Ekv. (319): r_flat, ΔL_flat och ΔL₃ ska ha ett värde per frekvens.")
    resultat: Flyttal = r_flat * markeffekt_plan_db + (1.0 - r_flat) * markeffekt_delmodell3_db
    return resultat
