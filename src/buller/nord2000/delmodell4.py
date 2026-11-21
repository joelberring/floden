"""Delmodell 4 i Nord2000: terräng med en skärm som har en diffrakterande kant.

Källor (varje formel avläst från sidbild, se `docs/buller/logg/P-4.md`):

* DELTA AV 1106/07 (rev. 2014) avsnitt 5.13.1 (basmodellen, ekv. 157–186) och 5.13.2 (den
  allmänna modellen, ekv. 187–188), hjälpfunktionerna ImagePoint (5.23.10, ekv. 369–370) och
  SegmentVariables (5.23.15, ekv. 382–383), ekv. (22) för h″_SCR samt ekv. (301) i 5.21.1.
* FORCE TC-101327 (2019) avsnitt 2.3 och 2.8 med bilaga G.1, G.2 och G.5: kilytornas
  reflektionskoefficienter (`delmodell4_kilben.py`).
* DELTA AV 1851/00 (rev. 2006) ekv. (53), för att avgöra tryckfelet i ekv. (301).

Vad som räknas:

* **Skärmens verkan i fritt fält**, |p̂_SCR| = |p̂_1,ff|·R_SR (ekv. 160–163), med räta eller
  krökta strålar (CalcEqSSP och DirectRay ur `refraktion.py` och `stralar_krokta.py`), och
  gångvägsskillnaden Δℓ med refraktion (ekv. 301) till kroken i `terrang_skarmar.py`.
* **Markens verkan utöver skärmen**, |p̂_G| (ekv. 164–183, 187) och ΔL₄ (ekv. 188). I homogen
  atmosfär utan fluktuerande refraktion räknas räta strålar. Annars (P-S2, formlerna avlästa
  från sidbild s. 71–76) räknas markdelen med krökta strålar: CalcEqSSPGround för medel- och
  övre refraktion på var sida (ekv. 166, 170), DirectRay, ReflectedRay och TravelTimeDiff
  (ekv. 167–168, 171–172), spegelvinklarnas Δθ (ekv. 165), FresnelZoneW med refraktion
  (ekv. 174), F_Δτ i koherensen (ekv. 177–181) och skuggzonsfallen (ekv. 184–186) med
  ShadowZoneShielding, allt med funktionerna i `refraktion_mark.py`, `stralar_krokta.py` och
  `fresnel.py`.

Val som avviker från eller fyller ut den tryckta texten står under "Tolkningar" i loggen
(`docs/buller/logg/P-4.md`; för refraktionen i markdelen `docs/buller/logg/P-S2.md`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from buller.nord2000.delmodell1 import Ct2Form
from buller.nord2000.delmodell4_kilben import Kilben, kilben, medelreflektion_kilben
from buller.nord2000.frekvenser import vagtal_per_m
from buller.nord2000.fresnel import fresnelzonsvikt, fresnelzonsvikt_refraktion
from buller.nord2000.geometri import MINSTA_HOJD_M, langd, normlinje, stralar_plan_mark
from buller.nord2000.kildiffraktion import (
    Kil,
    kil_rata_stralar,
    kiltryck_reflektionskoefficienter,
    tecken,
)
from buller.nord2000.koherens import (
    koherens_bandmedelvarde,
    koherens_fluktuerande_refraktion,
    koherens_markrahet,
    koherens_turbulens,
    transversell_separation_m,
)
from buller.nord2000.mark import inkoherent_reflektion, markimpedans, sfarisk_reflektion
from buller.nord2000.refraktion import Atmosfar, calc_eq_ssp, ovre_atmosfar
from buller.nord2000.refraktion_mark import (
    Skuggzonslasning,
    calc_eq_ssp_mark,
    kilavstand_skuggzon_m,
    skuggzonsavskarmning_db,
)
from buller.nord2000.stralar_krokta import (
    SKUGGZONSMARGINAL,
    Uppatmetod,
    direkt_strale,
    reflekterad_strale,
    skuggzonsavstand_m,
    stralar_krokt_plan_mark,
)
from buller.nord2000.terrang import Terranggeometri, terranggeometri
from buller.nord2000.terrang_skarmar import Skarmform
from buller.typer import BullerFel, Flyttal, Terrangprofil

# AV 1106/07 ekv. (174): Fresnelzonens andel av våglängden för markreflexerna vid en skärm.
F_LAMBDA_ANDEL_SKARM = 1.0 / 16.0
# AV 1106/07 ekv. (175)–(176): h_max = Min(0,0005·avstånd, 0,2).
HMAX_ANDEL_AV_AVSTAND = 0.0005
HMAX_STORSTA_M = 0.2
# AV 1106/07 ekv. (22): h″_SCR = 4·(R″_SCR − 75)/135 för R″_SCR > 75 m.
HBIS_GRANS_M = 75.0
HBIS_NAMNARE_M = 135.0
HBIS_FAKTOR = 4.0

Viktregel = Literal["av1849", "tryckt"]


# --------------------------------------------------------------------------- hjälpfunktioner


def bildpunkt(
    x1_m: float, z1_m: float, x2_m: float, z2_m: float, x_m: float, z_m: float
) -> tuple[float, float]:
    """Funktionen ImagePoint, AV 1106/07 avsnitt 5.23.10, ekv. (369)–(370).

    Spegelbilden av punkten P = (x, z) i linjen genom P₁ och P₂: x_i = 2·x₀ − x, z_i = 2·z₀ − z,
    där (x₀, z₀) är P:s projektion på linjen (NormLine). (Ekv. 369 är tryckt med funktionsnamnet
    Length; ImagePoint avses.)
    """
    x0, z0, _ = normlinje(x1_m, z1_m, x2_m, z2_m, x_m, z_m)
    return 2.0 * x0 - x_m, 2.0 * z0 - z_m


@dataclass(frozen=True)
class Segmentvariabler:
    """Utdata från SegmentVariables, AV 1106/07 avsnitt 5.23.15, ekv. (382)."""

    avstand_m: float  # d′: avståndet S–R längs det förlängda segmentet
    hojd_kalla_m: float  # h′_S: teckensatt vinkelrät höjd över det förlängda segmentet
    hojd_mottagare_m: float  # h′_R
    d1_m: float  # d′₁: teckensatt avstånd från S:s fotpunkt till segmentets början
    d2_m: float  # d′₂: teckensatt avstånd från S:s fotpunkt till segmentets slut


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
    """Funktionen SegmentVariables, AV 1106/07 avsnitt 5.23.15, ekv. (382)–(383).

    Källan S och mottagaren R projiceras på linjen genom segmentets ändpunkter (x₁ < x₂).
    d′₁ och d′₂ är negativa när ändpunkten ligger bakom källans fotpunkt, vilket ekv. (383)
    avgör med avstånden till mottagarens fotpunkt.
    """
    if not x2_m > x1_m:
        raise BullerFel("SegmentVariables kräver x2 > x1 för segmentets ändpunkter.")
    xs0, zs0, h_s = normlinje(x1_m, z1_m, x2_m, z2_m, x_s_m, z_s_m)
    xr0, zr0, h_r = normlinje(x1_m, z1_m, x2_m, z2_m, x_r_m, z_r_m)
    d = langd(xs0, zs0, xr0, zr0)

    def teckensatt(x: float, z: float) -> float:
        fran_kalla = langd(xs0, zs0, x, z)
        fran_mottagare = langd(xr0, zr0, x, z)
        if fran_kalla < fran_mottagare and fran_mottagare > d:
            return -fran_kalla
        return fran_kalla

    return Segmentvariabler(
        avstand_m=d,
        hojd_kalla_m=h_s,
        hojd_mottagare_m=h_r,
        d1_m=teckensatt(x1_m, z1_m),
        d2_m=teckensatt(x2_m, z2_m),
    )


def ekvivalent_skarmhojd_m(r_sr_m: float, r_st_m: float, r_rt_m: float) -> float:
    """h″_SCR, AV 1106/07 avsnitt 5.5.2, ekv. (22).

    R″_SCR = R_SR + min(R_ST, R_RT); h″_SCR = 4·(R″_SCR − 75)/135 över 75 m, annars 0. Höjden
    ersätter skärmtoppens höjd när den ekvivalenta ljudhastighetsprofilen bestäms för en
    stråle som börjar eller slutar i en skärmtopp.
    """
    r_bis = r_sr_m + min(r_st_m, r_rt_m)
    if r_bis > HBIS_GRANS_M:
        return HBIS_FAKTOR * (r_bis - HBIS_GRANS_M) / HBIS_NAMNARE_M
    return 0.0


def vinkel_fran_lodlinjen_rad(dz_m: float, dx_bort_m: float) -> float:
    """arctan(dz/dx) + π/2: vinkeln mellan lodlinjen nedåt från T och riktningen mot en punkt.

    Formen i AV 1106/07 ekv. (162) och (165), där `dx_bort_m` är punktens horisontella
    avstånd från T räknat bort från kanten (alltid ≥ 0 för terrängpunkter, källa och
    mottagare) och `dz_m` punktens höjd över T. Räknas med atan2, som är lika med ekvationen
    för dx > 0, ger gränsvärdet 0 för en lodrät kilyta (dx = 0) och fortsätter kontinuerligt
    för en spegelpunkt som hamnat på andra sidan lodlinjen (dx < 0).
    """
    return math.atan2(dz_m, dx_bort_m) + math.pi / 2.0


def gangvagsskillnad_refraktion_m(
    vag_kalla_m: float, vag_mottagare_m: float, theta_s_rad: float, theta_r_rad: float
) -> float:
    """Δℓ med krökta strålar, AV 1106/07 avsnitt 5.21.1, ekv. (301).

    R_S, R_R ur ekv. (161) och θ_S, θ_R ur ekv. (162). Räknas som

        Δℓ = Sign(θ_S − θ_R − π)·(R_S + R_R − √(R_S² + R_R² − 2·R_S·R_R·cos(θ_S − θ_R))),

    alltså negativ när kanten ligger under den (krökta) siktlinjen och positiv i skuggan, som
    Δℓ₀ i ekv. (300). Ekv. (301) är tryckt som Sign(…)·√(…) − R_S − R_R, vilket är negativt för
    alla kanter (i skuggan är roten kortare än R_S + R_R) och ca −2·(R_S + R_R) för en kant
    under siktlinjen. AV 1851/00 ekv. (53) ger för θ_S − θ_R < π uttrycket √(…) − R_S − R_R,
    som formen här är lika med; tecknet i skuggan följer av att r_Δℓ = 1 ska gälla där
    (ekv. 302). För räta strålar är resultatet lika med Δℓ₀.
    """
    if not vag_kalla_m > 0.0 or not vag_mottagare_m > 0.0:
        raise BullerFel("Strålsträckorna R_S och R_R ska vara större än 0.")
    skillnad = theta_s_rad - theta_r_rad
    korda2 = (
        vag_kalla_m**2
        + vag_mottagare_m**2
        - 2.0 * vag_kalla_m * vag_mottagare_m * math.cos(skillnad)
    )
    # R_S + R_R − korda räknad utan utsläckning: ((R_S + R_R)² − korda²)/(R_S + R_R + korda).
    summa = vag_kalla_m + vag_mottagare_m
    omvag = (
        2.0
        * vag_kalla_m
        * vag_mottagare_m
        * (1.0 + math.cos(skillnad))
        / (summa + math.sqrt(max(korda2, 0.0)))
    )
    return tecken(skillnad - math.pi) * omvag


# --------------------------------------------------------------------------- skärmen


@dataclass(frozen=True)
class Kilgeometri:
    """Kilens vinklar ur terrängen, AV 1106/07 ekv. (162): β₁, β₂ och β = 2π − β₁ − β₂.

    θ₁ och θ₂ är riktningarna från T mot källan respektive mottagaren, mätta från lodlinjen
    nedåt (räta linjer, utan Δθ).
    """

    beta1_rad: float
    beta2_rad: float
    beta_rad: float
    theta1_rad: float
    theta2_rad: float


@dataclass(frozen=True)
class Skarmstralar:
    """Skärmens geometri och strålar för p̂_1,ff, AV 1106/07 ekv. (161)–(162) och (301)."""

    index_w1: int
    index_t: int
    index_w2: int
    vinklar: Kilgeometri
    kil: Kil  # indata till pwedge: β, θ_S, θ_R, τ, τ_S, τ_R, ℓ, R_S, R_R
    vinkelandring_kalla_rad: float  # Δθ_S
    vinkelandring_mottagare_rad: float  # Δθ_R
    hojd_over_baslinje_m: float  # h_SCR
    d_scr1_m: float
    d_scr2_m: float
    gangvagsskillnad_m: float  # Δℓ enligt ekv. (301)


def kilgeometri(geo: Terranggeometri, index_w1: int, index_t: int, index_w2: int) -> Kilgeometri:
    """β₁, β₂, θ₁, θ₂ och β, AV 1106/07 avsnitt 5.13.1, ekv. (162).

    Skärmformen är reducerad till de tre punkterna W₁, T₁ och W₂ (avsnitt 5.13.2); punkter
    däremellan påverkar inte kilens vinklar.
    """
    n = geo.x_m.shape[0]
    if not 0 <= index_w1 < index_t < index_w2 <= n - 1:
        raise BullerFel("Skärmen kräver index W₁ < T < W₂ inom terrängprofilen.")
    x_t, z_t = float(geo.x_m[index_t]), float(geo.z_m[index_t])
    x_1, z_1 = float(geo.x_m[index_w1]), float(geo.z_m[index_w1])
    x_2, z_2 = float(geo.x_m[index_w2]), float(geo.z_m[index_w2])
    if not geo.x_s_m < x_t < geo.x_r_m:
        raise BullerFel("Skärmkanten ska ligga mellan källan och mottagaren.")
    beta1 = vinkel_fran_lodlinjen_rad(z_1 - z_t, x_t - x_1)
    beta2 = vinkel_fran_lodlinjen_rad(z_2 - z_t, x_2 - x_t)
    return Kilgeometri(
        beta1_rad=beta1,
        beta2_rad=beta2,
        beta_rad=2.0 * math.pi - beta1 - beta2,
        theta1_rad=vinkel_fran_lodlinjen_rad(geo.z_s_m - z_t, x_t - geo.x_s_m),
        theta2_rad=vinkel_fran_lodlinjen_rad(geo.z_r_m - z_t, geo.x_r_m - x_t),
    )


def skarmstralar(
    profil: Terrangprofil,
    form: Skarmform,
    atmosfar: Atmosfar,
    uppat: Uppatmetod = "bokstavlig",
) -> Skarmstralar:
    """Variablerna för p̂_1,ff och Δℓ, AV 1106/07 avsnitt 5.13.1, ekv. (161)–(162), och ekv. (301).

    `form` är primärskärmens form (W₁, T₁, W₂) ur `terrang_skarmar.skarmform`. Den ekvivalenta
    profilen på var sida om skärmen är oberoende av frekvensen: CalcEqSSP(h_Sv, h″_SCR) och
    CalcEqSSP(h_Rv, h″_SCR) med h″_SCR ur ekv. (22). Strålarna S–T och T–R räknas med DirectRay i
    ett system där baslinjen P_S–P_R är "marken": höjderna är de vinkelräta avstånden till
    baslinjen och avstånden mäts mellan fotpunkterna. För ξ = 0 ger det exakt de räta sträckorna
    |ST| och |TR|. (Ekv. 161 mäter d_SCR1 från P_S och anger källhöjden som "h_S,"; på en vågrät
    baslinje är det samma sak. Se loggen, Tolkningar.)

    θ_S = 2π − θ₁ − β₂ − Δθ_S och θ_R = θ₂ − β₂ + Δθ_R (ekv. 162).
    """
    geo = terranggeometri(profil)
    vinklar = kilgeometri(geo, form.index_w1, form.index_kant, form.index_w2)
    x_ps, z_ps = float(profil.x_m[0]), float(profil.z_m[0])
    x_pr, z_pr = float(profil.x_m[-1]), float(profil.z_m[-1])
    x_t, z_t = float(geo.x_m[form.index_kant]), float(geo.z_m[form.index_kant])
    xt0, zt0, h_scr = normlinje(x_ps, z_ps, x_pr, z_pr, x_t, z_t)
    xs0, zs0, h_sb = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_s_m, geo.z_s_m)
    xr0, zr0, h_rb = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_r_m, geo.z_r_m)
    if not h_scr > 0.0:
        raise BullerFel(
            "Skärmkanten ligger inte över terrängens baslinje (h_SCR ≤ 0): den kan inte vara "
            "kant i delmodell 4 (AV 1106/07 avsnitt 5.21.1)."
        )
    d1 = langd(xs0, zs0, xt0, zt0)
    d2 = langd(xt0, zt0, xr0, zr0)

    h_sv = max(profil.kalla_hojd_m, MINSTA_HOJD_M)
    h_rv = max(profil.mottagare_hojd_m, MINSTA_HOJD_M)
    r_st = langd(geo.x_s_m, geo.z_s_m, x_t, z_t)
    r_rt = langd(geo.x_r_m, geo.z_r_m, x_t, z_t)
    h_bis = ekvivalent_skarmhojd_m(geo.r_sr_m, r_st, r_rt)
    if atmosfar.har_refraktion:
        profil_s = calc_eq_ssp(h_sv, h_bis, atmosfar)
        profil_r = calc_eq_ssp(h_rv, h_bis, atmosfar)
        xi_s, c_s = profil_s.xi_per_m, profil_s.c0_m_s
        xi_r, c_r = profil_r.xi_per_m, profil_r.c0_m_s
    else:
        xi_s, c_s, xi_r, c_r = 0.0, atmosfar.c_m_s, 0.0, atmosfar.c_m_s
    strale_s = direkt_strale(d1, h_sb, h_scr, xi_s, c_s, uppat)
    strale_r = direkt_strale(d2, h_rb, h_scr, xi_r, c_r, uppat)

    theta_s = 2.0 * math.pi - vinklar.theta1_rad - vinklar.beta2_rad - strale_s.vinkelandring_rad
    theta_r = vinklar.theta2_rad - vinklar.beta2_rad + strale_r.vinkelandring_rad
    kil = Kil(
        beta_rad=vinklar.beta_rad,
        theta_s_rad=theta_s,
        theta_r_rad=theta_r,
        gangtid_s=strale_s.gangtid_s + strale_r.gangtid_s,
        gangtid_kalla_s=strale_s.gangtid_s,
        gangtid_mottagare_s=strale_r.gangtid_s,
        vag_m=strale_s.vagstracka_m + strale_r.vagstracka_m,
        vag_kalla_m=strale_s.vagstracka_m,
        vag_mottagare_m=strale_r.vagstracka_m,
    )
    return Skarmstralar(
        index_w1=form.index_w1,
        index_t=form.index_kant,
        index_w2=form.index_w2,
        vinklar=vinklar,
        kil=kil,
        vinkelandring_kalla_rad=strale_s.vinkelandring_rad,
        vinkelandring_mottagare_rad=strale_r.vinkelandring_rad,
        hojd_over_baslinje_m=h_scr,
        d_scr1_m=d1,
        d_scr2_m=d2,
        gangvagsskillnad_m=gangvagsskillnad_refraktion_m(
            strale_s.vagstracka_m, strale_r.vagstracka_m, theta_s, theta_r
        ),
    )


def kilben_ur_terrang(geo: Terranggeometri, form: Skarmform) -> tuple[Kilben, Kilben]:
    """Kilbenen på källsidan och mottagarsidan, TC-101327 bilaga G.2 (ekv. 406–420).

    Mottagarsidan: terrängpunkterna T, …, W₂, …, R_G (profilens sista punkt). Källsidan räknas
    "på samma sätt" (bilagans sista mening i G.2): punkterna T, …, W₁, …, S_G (profilens första
    punkt), speglade i x så att förloppet ser ut som mottagarsidans. Returnerar (källsida,
    mottagarsida).
    """
    n = geo.x_m.shape[0]
    t = form.index_kant
    sigma = geo.flodesresistivitet_kNsm4
    x_mottagare = [float(v) for v in geo.x_m[t:]]
    z_mottagare = [float(v) for v in geo.z_m[t:]]
    sigma_mottagare = [float(v) for v in sigma[t : n - 1]]
    x_kalla = [-float(v) for v in geo.x_m[t::-1]]
    z_kalla = [float(v) for v in geo.z_m[t::-1]]
    sigma_kalla = [float(v) for v in sigma[t - 1 :: -1]] if t >= 1 else []
    return (
        kilben(x_kalla, z_kalla, sigma_kalla, t - form.index_w1),
        kilben(x_mottagare, z_mottagare, sigma_mottagare, form.index_w2 - t),
    )


# --------------------------------------------------------------------------- basmodellen


def _ytreflektion(frekvens_hz: float, gangtid_s: float, psi_rad: float, sigma: float) -> complex:
    """Q̂(f, τ, ψ_G, Z_G), AV 1106/07 ekv. (169) och (173); σ = ∞ (hård mark) ger Q̂ = 1."""
    if math.isinf(sigma) and sigma > 0.0:
        return 1.0 + 0.0j
    return sfarisk_reflektion(frekvens_hz, gangtid_s, psi_rad, markimpedans(frekvens_hz, sigma))


def _inkoherent(frekvens_hz: float, sigma: float) -> float:
    """ℜ(f, Z_G), AV 1106/07 ekv. (183); σ = ∞ (hård mark) ger ℜ = 1."""
    if math.isinf(sigma) and sigma > 0.0:
        return 1.0
    return inkoherent_reflektion(markimpedans(frekvens_hz, sigma))


def hojdmodifierare(hojd_m: float, grans_m: float) -> float:
    """Faktorerna r_S1, r_R1, r_S2, r_R2 i AV 1106/07 ekv. (175)–(176).

    1 när höjden över det förlängda segmentet är minst `grans_m` (h″ eller h_max), 0 när
    punkten ligger på eller under segmentets förlängning, linjärt däremellan.
    """
    if hojd_m <= 0.0:
        return 0.0
    if hojd_m >= grans_m:
        return 1.0
    return hojd_m / grans_m


def storsta_hojd_m(avstand_m: float) -> float:
    """h_max = Min(0,0005·(x_T − x_S), 0,2) resp. med (x_R − x_T), AV 1106/07 ekv. (175)–(176)."""
    return min(HMAX_ANDEL_AV_AVSTAND * avstand_m, HMAX_STORSTA_M)


@dataclass(frozen=True)
class Marksida:
    """Ett reflekterande segment på ena sidan om skärmen, vid en frekvens (ekv. 164–180).

    På källsidan är "källan" S och "mottagaren" skärmtoppen T; på mottagarsidan är "källan" T
    och "mottagaren" R. `vikt_bis` är w″ i ekv. (174).
    """

    vikt_bis: float  # w″ = w·r_S·r_R
    vag_direkt_m: float  # R_1S eller R_1R
    vag_reflekterad_m: float  # R_2S eller R_2R
    gangtidsskillnad_s: float  # Δτ_S eller Δτ_R
    strykvinkel_rad: float  # ψ_GS eller ψ_GR
    reflektion: complex  # Q̂₁ eller Q̂₂
    inkoherent: float  # ℜ₁ eller ℜ₂
    koherens_turbulens: float  # F_c för sidan
    koherens_rahet: float  # F_r för sidan
    vinkel_bild_rad: float  # θ₁ eller θ₂ för spegelpunkten (ekv. 165)
    gangtid_direkt_s: float  # τ_1S eller τ_1R
    gangtid_reflekterad_s: float  # τ_2S eller τ_2R
    gangtidsskillnad_ovre_s: float  # Δτ_S+ eller Δτ_R+ (lika med Δτ utan fluktuerande refraktion)
    # Den reflekterade strålens vinkeländring i områdets "källa" (Δθ_S) och "mottagare" (Δθ_R),
    # ReflectedRay i den frekvensoberoende profilen. Δθ_Si i ekv. (165) är värdet i skärmtoppen
    # på källsidan (områdets mottagare), Δθ_Ri värdet i skärmtoppen på mottagarsidan (källan).
    vinkelandring_kalla_rad: float = 0.0
    vinkelandring_mottagare_rad: float = 0.0
    # Sant när spegelpunktens vinkel ska ersättas av den direkta strålens: i skuggzon
    # (p̂₂ = p̂₁, ekv. 184–186) och när ReflectedRay saknas i ekv. (165).
    bildvinkel_som_direkt: bool = False
    skuggzon_db: float = 0.0  # ΔL_SZ,1 eller ΔL_SZ,2


def neutral_sida(vag_direkt_m: float, vinkel_rad: float, gangtid_direkt_s: float = 0.0) -> Marksida:
    """En sida utan markreflex: Q̂ = 0 och ℜ = 0, så att bara den andra sidans termer återstår.

    `gangtid_direkt_s` behövs bara med krökta strålar (räta strålar räknar τ = R/c).
    """
    return Marksida(
        vikt_bis=0.0,
        vag_direkt_m=vag_direkt_m,
        vag_reflekterad_m=vag_direkt_m,
        gangtidsskillnad_s=0.0,
        strykvinkel_rad=0.0,
        reflektion=0.0j,
        inkoherent=0.0,
        koherens_turbulens=1.0,
        koherens_rahet=1.0,
        vinkel_bild_rad=vinkel_rad,
        gangtid_direkt_s=gangtid_direkt_s,
        gangtid_reflekterad_s=gangtid_direkt_s,
        gangtidsskillnad_ovre_s=0.0,
    )


def markfaktor_basmodell(
    p1: complex,
    p2: complex,
    p3: complex,
    p4: complex,
    q1: complex,
    q2: complex,
    vikt_q1: float,
    vikt_q2: float,
    koherens_2: float,
    koherens_3: float,
    koherens_4: float,
    inkoherent_1: float,
    inkoherent_2: float,
) -> float:
    """|p̂_G| för ett segmentpar, AV 1106/07 avsnitt 5.13.1, ekv. (182).

    p₁ … p₄ är de fyra diffrakterade trycken i ekv. (157) (källa–mottagare, spegelkälla–
    mottagare, källa–spegelmottagare, spegelkälla–spegelmottagare), Q̂₁, Q̂₂ markens sfäriska
    reflektionskoefficienter, w_Q1, w_Q2 vikterna i ekv. (187), F₂–F₄ koherenskoefficienterna
    (ekv. 177, 179, 181) och ℜ₁, ℜ₂ de inkoherenta reflektionskoefficienterna (ekv. 183). Med
    F₂ = F₃ = F₄ = 1 är resultatet beloppet av parentesen i ekv. (159).
    """
    if p1 == 0.0:
        raise BullerFel("Det diffrakterade trycket p₁ är 0: |p_G| i ekv. (182) är odefinierat.")
    k2 = vikt_q1 * p2 / p1
    k3 = vikt_q2 * p3 / p1
    k4 = vikt_q1 * vikt_q2 * p4 / p1
    koherent = abs(1.0 + koherens_2 * q1 * k2 + koherens_3 * q2 * k3 + koherens_4 * q1 * q2 * k4)
    inkoherent = (
        (1.0 - koherens_2**2) * abs(inkoherent_1 * k2) ** 2
        + (1.0 - koherens_3**2) * abs(inkoherent_2 * k3) ** 2
        + (1.0 - koherens_4**2) * abs(inkoherent_1 * inkoherent_2 * k4) ** 2
    )
    return math.sqrt(koherent**2 + inkoherent)


def normerade_vikter(
    vikter_bis_1: list[float], vikter_bis_2: list[float], viktregel: Viktregel = "av1849"
) -> tuple[list[float], list[float], float, float]:
    """w′_i1, w′_i2, w_Q1 och w_Q2 ur w″_i1 och w″_i2, AV 1106/07 avsnitt 5.13.2, ekv. (187).

    w_1t = Σw″_i1 och w_2t = Σw″_i2. w_Q = 1 när summan är minst 1, annars summan i kvadrat.
    För 0 < w_t ≤ 1 är w′ = w″/w_t. Grenarna w_t > 1 och w_t = 0 räknas enligt `viktregel`:

    * `"av1849"` (förval): w′ = w″ när w_t > 1 (AV 1849/00 ekv. 62–67, där vikterna inte
      normeras när summan är minst 1), och en sida med w_t = 0 lämnar exponenterna på den andra
      sidan orörda (AV 1849/00 ekv. 70–72). Det senare sköts av `skarm_en_kant`; här blir
      w′ = 0 och w_Q = 0 för sidan.
    * `"tryckt"`: ekv. (187) bokstavligt, w′ = w″/w_t·(Δw_t,sida/Δw_t + 1) för w_t > 1 och
      w′ = 0 för w_t = 0.

    Den tryckta grenen för w_t > 1 gör att Σw′ hoppar från 1 till 2 när w_t passerar 1 – där
    helt vanlig plan mark ligger – så att markeffekten i dB fördubblas av en avrundning. Se
    loggen P-4, Tolkningar.
    """
    if viktregel not in ("av1849", "tryckt"):
        raise BullerFel(f"Okänd viktregel: {viktregel!r} (av1849 eller tryckt).")
    w1t = sum(vikter_bis_1)
    w2t = sum(vikter_bis_2)
    dw1 = w1t - 1.0 if w1t > 1.0 else 0.0
    dw2 = w2t - 1.0 if w2t > 1.0 else 0.0
    dw = dw1 + dw2

    def normera(vikter: list[float], summa: float, overskott: float) -> list[float]:
        if summa > 1.0:
            if viktregel == "av1849":
                return list(vikter)
            return [w / summa * (overskott / dw + 1.0) for w in vikter]
        if summa > 0.0:
            return [w / summa for w in vikter]
        return [0.0 for _ in vikter]

    w_q1 = 1.0 if w1t >= 1.0 else w1t * w1t
    w_q2 = 1.0 if w2t >= 1.0 else w2t * w2t
    return normera(vikter_bis_1, w1t, dw1), normera(vikter_bis_2, w2t, dw2), w_q1, w_q2


# --------------------------------------------------------------------------- den allmänna modellen


@dataclass(frozen=True)
class Delmodell4Resultat:
    """Utdata från delmodell 4, ett värde per frekvens där inget annat sägs."""

    frekvenser_hz: Flyttal
    delta_l_db: Flyttal  # ΔL₄, ekv. (188)
    skarmeffekt_db: Flyttal  # 20·lg|p̂_SCR|, ekv. (163)
    markfaktor_pg: Flyttal  # p_G = ∏∏|p̂_G,i1,i2|^(w′_i1·w′_i2), före 20·lg (till delmodell 7)
    q_av_kalla: Flyttal  # Q_avS, TC-101327 ekv. (421) (eller det givna värdet)
    q_av_mottagare: Flyttal  # Q_avR
    viktsumma_kalla: Flyttal  # w_1t, ekv. (187)
    viktsumma_mottagare: Flyttal  # w_2t
    stralar: Skarmstralar  # geometri, kil för p̂_1,ff och Δℓ (ekv. 301)


def per_frekvens(varde: Flyttal | float | None, antal: int, namn: str) -> list[float] | None:
    if varde is None:
        return None
    falt = np.asarray(varde, dtype=np.float64)
    if falt.ndim == 0:
        return [float(falt)] * antal
    if falt.shape != (antal,):
        raise BullerFel(f"{namn} ska vara ett tal eller ha ett värde per frekvens ({antal}).")
    return [float(v) for v in falt]


def kontrollera_frekvenser(frekvenser_hz: Flyttal) -> Flyttal:
    frekvenser = np.asarray(frekvenser_hz, dtype=np.float64)
    if frekvenser.ndim != 1 or frekvenser.shape[0] == 0 or not bool(np.all(frekvenser > 0.0)):
        raise BullerFel("Frekvenserna ska vara en icke-tom vektor med värden större än 0 Hz.")
    return frekvenser


def kilytornas_reflektion(
    frekvenser_hz: Flyttal,
    geo: Terranggeometri,
    form: Skarmform,
    ljudhastighet_m_s: float,
    q_kilyta_kalla: Flyttal | float | None = None,
    q_kilyta_mottagare: Flyttal | float | None = None,
) -> tuple[Flyttal, Flyttal]:
    """Q_avS(f) och Q_avR(f) för kilens två ytor, TC-101327 bilaga G.2 (ekv. 421).

    Ett givet värde (`q_kilyta_kalla`, `q_kilyta_mottagare`) går före och är avsett för
    bilaga G.5, där Q_e = √ρ_E för en byggd yta som bara är känd genom sin
    energireflektionskoefficient (`delmodell4_kilben.ekvivalent_reflektion_ur_energireflektion`).
    """
    frekvenser = kontrollera_frekvenser(frekvenser_hz)
    antal = frekvenser.shape[0]
    givet_s = per_frekvens(q_kilyta_kalla, antal, "q_kilyta_kalla")
    givet_r = per_frekvens(q_kilyta_mottagare, antal, "q_kilyta_mottagare")
    ben_s, ben_r = kilben_ur_terrang(geo, form)
    q_s = np.empty(antal, dtype=np.float64)
    q_r = np.empty(antal, dtype=np.float64)
    for i, f_np in enumerate(frekvenser):
        f = float(f_np)
        q_s[i] = (
            medelreflektion_kilben(f, ben_s, ljudhastighet_m_s) if givet_s is None else givet_s[i]
        )
        q_r[i] = (
            medelreflektion_kilben(f, ben_r, ljudhastighet_m_s) if givet_r is None else givet_r[i]
        )
    return q_s, q_r


def skarmeffekt_fritt_falt_db(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    form: Skarmform,
    atmosfar: Atmosfar,
    *,
    q_kilyta_kalla: Flyttal | float | None = None,
    q_kilyta_mottagare: Flyttal | float | None = None,
    uppat: Uppatmetod = "bokstavlig",
) -> tuple[Flyttal, Skarmstralar]:
    """20·lg|p̂_SCR| = 20·lg(|p̂_1,ff|·R_SR) per frekvens, AV 1106/07 ekv. (160)–(163).

    Gäller även med refraktion (krökta strålar, ekv. 161). Kilytornas koefficienter är Q_avS och
    Q_avR enligt TC-101327 bilaga G; den speglade strålen i ekv. (89)–(90) får samma koefficient
    (se loggen). Returnerar också `Skarmstralar`, där `gangvagsskillnad_m` är Δℓ enligt
    ekv. (301) till kroken `gangvagsskillnad_t1_m` i `terrang_skarmar.tolka_terrang`.
    """
    frekvenser = kontrollera_frekvenser(frekvenser_hz)
    stralar = skarmstralar(profil, form, atmosfar, uppat)
    geo = terranggeometri(profil)
    q_s, q_r = kilytornas_reflektion(
        frekvenser, geo, form, atmosfar.c_m_s, q_kilyta_kalla, q_kilyta_mottagare
    )
    niva = np.empty(frekvenser.shape[0], dtype=np.float64)
    for i, f_np in enumerate(frekvenser):
        p = kiltryck_reflektionskoefficienter(
            float(f_np), stralar.kil, complex(q_s[i]), complex(q_r[i])
        )
        niva[i] = 20.0 * math.log10(abs(p) * geo.r_sr_m)
    return niva, stralar


def marksida(
    frekvens_hz: float,
    variabler: Segmentvariabler,
    hojdfaktor: float,
    sigma: float,
    rahet_m: float,
    vinkel_bild_rad: float,
    atmosfar: Atmosfar,
) -> Marksida | None:
    """Storheterna för ett reflekterande segment i homogen atmosfär (ekv. 167–180 med ξ = 0).

    `hojdfaktor` är produkten r_S·r_R i ekv. (174). Returnerar `None` när w″ = 0, eftersom
    segmentet då inte bidrar (och h′ kan vara ≤ 0, där strålarna är odefinierade).
    """
    if hojdfaktor <= 0.0:
        return None
    c = atmosfar.c_m_s
    vikt = hojdfaktor * fresnelzonsvikt(
        variabler.avstand_m,
        variabler.hojd_kalla_m,
        variabler.hojd_mottagare_m,
        variabler.d1_m,
        variabler.d2_m,
        F_LAMBDA_ANDEL_SKARM * c / frekvens_hz,
    )  # ekv. (174)
    if vikt <= 0.0:
        return None
    stralar = stralar_plan_mark(
        variabler.avstand_m, variabler.hojd_kalla_m, variabler.hojd_mottagare_m, c
    )
    return Marksida(
        vikt_bis=vikt,
        vag_direkt_m=stralar.r1_m,
        vag_reflekterad_m=stralar.r2_m,
        gangtidsskillnad_s=stralar.gangtidsskillnad_s,
        strykvinkel_rad=stralar.strykvinkel_rad,
        reflektion=_ytreflektion(frekvens_hz, stralar.tau2_s, stralar.strykvinkel_rad, sigma),
        inkoherent=_inkoherent(frekvens_hz, sigma),
        koherens_turbulens=koherens_turbulens(
            frekvens_hz,
            atmosfar.cv2,
            atmosfar.ct2,
            atmosfar.temperatur_luft_c,
            c,
            transversell_separation_m(variabler.hojd_kalla_m, variabler.hojd_mottagare_m),
            variabler.avstand_m,
        ),  # ekv. (177)–(180)
        koherens_rahet=koherens_markrahet(
            vagtal_per_m(frekvens_hz, c), stralar.strykvinkel_rad, rahet_m
        ),
        vinkel_bild_rad=vinkel_bild_rad,
        gangtid_direkt_s=stralar.r1_m / c,
        gangtid_reflekterad_s=stralar.tau2_s,
        gangtidsskillnad_ovre_s=stralar.gangtidsskillnad_s,
    )


def marksidor_refraktion(
    frekvenser_hz: Flyttal,
    variabler: Segmentvariabler,
    hojdfaktor: float,
    sigma: float,
    rahet_m: float,
    vinkel_bild_rad: float,
    atmosfar: Atmosfar,
    *,
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    skuggzon: Skuggzonslasning | None = None,
) -> list[Marksida | None]:
    """Ett reflekterande segment med krökta strålar, en post per frekvens (ekv. 165–174, 184–186).

    Området mellan "källan" (S eller en skärmtopp) och "mottagaren" (en skärmtopp eller R)
    räknas som plan mark längs segmentets förlängning, på samma sätt som delmodell 1 med
    refraktion (`delmodell1`):

    * (ξ(f), c₀(f)), c̄, (ξ, c₀) ur CalcEqSSPGround(h′_S, h′_R, Ẑ_G(f), z₀, A, B, C) och
      motsvarande för A₊, B₊ (ekv. 166, 170); f_π och f_2π räknas med avståndet d′;
    * DirectRay och ReflectedRay i den frekvensberoende profilen ger τ₁, R₁, d_SZ, τ₂, R₂, ψ_G,
      och TravelTimeDiff ger Δτ och Δτ₊ (ekv. 167–168, 171–172);
    * Q̂ = Q̂(f, τ₂, ψ_G, Ẑ_G) (ekv. 169, 173);
    * w = FresnelZoneW(d′, h′_S, h′_R, d₁, d₂, λ/16, ξ, c₀) i den frekvensoberoende profilen
      (ekv. 174);
    * spegelpunktens vinkeländring vid skärmtoppen ur ReflectedRay i den frekvensoberoende
      profilen (ekv. 165): Δθ_R när toppen är områdets "mottagare" (källsidan), Δθ_S när den
      är dess "källa" (mottagarsidan); båda lämnas ut och anroparen väljer;
    * i skuggzon (ξ < 0 och d′ > 0,95·d_SZ): ΔL_SZ = ShadowZoneShielding(f, d′, h′_S, h′_R, ξ,
      c₀, d_SZ), Q̂ = Q̂(f, τ₁, 0, Ẑ_G) och den reflekterade strålen ersätts av den direkta
      (p̂₂ = p̂₁ osv., ekv. 184–186); Δτ = 0 och ψ_G = 0 där (avsnitt 5.5.6).

    Vilken profil som avgör skuggzonen, kilens topp i ShadowZoneShielding och gångtiden i Q̂
    läses som i delmodell 1 (`skuggzon`, `refraktion_mark.Skuggzonslasning`), så att plan mark
    och mark vid en skärm behandlas lika. F_c räknas med c̄ ur CalcEqSSPGround (ekv. 166) och
    F_r med k₀ = 2πf/c₀ i den frekvensoberoende profilen. Höjder under 0,01 m ersätts med
    0,01 m i strålgången (avsnitt 5.4.1), som i delmodell 1 med refraktion.
    """
    antal = frekvenser_hz.shape[0]
    if hojdfaktor <= 0.0:
        return [None] * antal
    lasning = Skuggzonslasning() if skuggzon is None else skuggzon
    d = variabler.avstand_m
    h_s = max(variabler.hojd_kalla_m, MINSTA_HOJD_M)
    h_r = max(variabler.hojd_mottagare_m, MINSTA_HOJD_M)
    c_mark = atmosfar.c_m_s
    hard = math.isinf(sigma) and sigma > 0.0
    impedanser = [
        complex(math.inf) if hard else markimpedans(float(f), sigma) for f in frekvenser_hz
    ]
    profiler = calc_eq_ssp_mark(frekvenser_hz, d, h_s, h_r, sigma, impedanser, atmosfar)
    fluktuerar = atmosfar.har_fluktuerande_refraktion
    profiler_ovre = (
        calc_eq_ssp_mark(frekvenser_hz, d, h_s, h_r, sigma, impedanser, ovre_atmosfar(atmosfar))
        if fluktuerar
        else profiler
    )
    xi_ob = profiler.oberoende.xi_per_m
    c0_ob = profiler.oberoende.c0_m_s
    c_medel = profiler.oberoende.c_medel_m_s

    def i_skugga(xi: float) -> bool:
        return xi < 0.0 and d > SKUGGZONSMARGINAL * skuggzonsavstand_m(h_s, h_r, xi, uppat)

    en_gang = lasning.avgorande == "oberoende"
    skugga_oberoende = lasning.avgorande != "per_band" and i_skugga(xi_ob)
    skugga_alla = en_gang and skugga_oberoende
    skugga_alla_ovre = en_gang and i_skugga(profiler_ovre.oberoende.xi_per_m)

    # Ekv. (165): spegelpunktens vinkeländring vid skärmtoppen, frekvensoberoende profil.
    vinkelandring_kalla = 0.0
    vinkelandring_mottagare = 0.0
    bild_saknas = False
    if xi_ob != 0.0:
        if i_skugga(xi_ob):
            bild_saknas = True  # ingen reflekterad stråle: den direkta strålens vinkel används
        else:
            bildstrale = reflekterad_strale(d, h_s, h_r, xi_ob, c0_ob, uppat)
            vinkelandring_kalla = bildstrale.vinkelandring_kalla_rad
            vinkelandring_mottagare = bildstrale.vinkelandring_mottagare_rad

    ct2 = atmosfar.ct2
    if ct2_form == "tryckt":
        ct2 = ct2 * (atmosfar.temperatur_luft_c + 273.15)
    elif ct2_form != "kvadrat":
        raise BullerFel(f"Okänd form för C_T²-termen: {ct2_form!r} (kvadrat eller tryckt).")
    separation = transversell_separation_m(h_s, h_r)

    sidor: list[Marksida | None] = []
    for i, f_np in enumerate(frekvenser_hz):
        f = float(f_np)
        vikt = hojdfaktor * fresnelzonsvikt_refraktion(
            d,
            h_s,
            h_r,
            variabler.d1_m,
            variabler.d2_m,
            F_LAMBDA_ANDEL_SKARM * c_mark / f,
            xi_ob,
            c0_ob,
            uppat,
        )  # ekv. (174)
        if vikt <= 0.0:
            sidor.append(None)
            continue
        p = profiler.per_frekvens[i]
        stralar = stralar_krokt_plan_mark(d, h_s, h_r, p.xi_per_m, p.c0_m_s, c_mark, uppat)
        reflekterad = None if skugga_alla else stralar.reflekterad
        dtau = 0.0 if reflekterad is None else max(stralar.gangtidsskillnad_s, 0.0)
        if fluktuerar:
            po = profiler_ovre.per_frekvens[i]
            ovre = stralar_krokt_plan_mark(d, h_s, h_r, po.xi_per_m, po.c0_m_s, c_mark, uppat)
            dtau_ovre = (
                0.0
                if skugga_alla_ovre or ovre.reflekterad is None
                else max(ovre.gangtidsskillnad_s, 0.0)
            )
        else:
            dtau_ovre = dtau
        psi = 0.0 if reflekterad is None else reflekterad.strykvinkel_rad
        tau_1 = stralar.direkt.gangtid_s
        r_1 = stralar.direkt.vagstracka_m
        if reflekterad is None:  # ekv. (184)–(186): Q̂(f, τ₁, 0, Ẑ) och p̂₂ = p̂₁
            q = _ytreflektion(f, tau_1 if lasning.q_gangtid == "direkt" else 0.0, 0.0, sigma)
            tau_2, r_2 = tau_1, r_1
        else:
            q = _ytreflektion(f, reflekterad.gangtid_s, psi, sigma)
            tau_2, r_2 = reflekterad.gangtid_s, reflekterad.vagstracka_m
        skuggzon_db = 0.0
        if reflekterad is None or skugga_oberoende:  # ΔL_SZ, ShadowZoneShielding
            if lasning.kiltopp == "av1851":
                kiltopp: float | None = kilavstand_skuggzon_m(d, h_s, h_r)
            else:
                d_sz = (
                    stralar.direkt.skuggzonsavstand_m
                    if lasning.avgorande == "per_band"
                    else skuggzonsavstand_m(h_s, h_r, xi_ob, uppat)
                )
                kiltopp = d_sz if d_sz < d else None
            if kiltopp is not None:
                skuggzon_db = skuggzonsavskarmning_db(
                    f, d, h_s, h_r, xi_ob, c0_ob, kiltopp, lasning.niva
                )
        sidor.append(
            Marksida(
                vikt_bis=vikt,
                vag_direkt_m=r_1,
                vag_reflekterad_m=r_2,
                gangtidsskillnad_s=dtau,
                strykvinkel_rad=psi,
                reflektion=q,
                inkoherent=_inkoherent(f, sigma),
                koherens_turbulens=koherens_turbulens(
                    f, atmosfar.cv2, ct2, atmosfar.temperatur_luft_c, c_medel, separation, d
                ),
                koherens_rahet=koherens_markrahet(vagtal_per_m(f, c0_ob), psi, rahet_m),
                vinkel_bild_rad=vinkel_bild_rad,
                gangtid_direkt_s=tau_1,
                gangtid_reflekterad_s=tau_2,
                gangtidsskillnad_ovre_s=dtau_ovre,
                vinkelandring_kalla_rad=vinkelandring_kalla,
                vinkelandring_mottagare_rad=vinkelandring_mottagare,
                bildvinkel_som_direkt=reflekterad is None or bild_saknas,
                skuggzon_db=skuggzon_db,
            )
        )
    return sidor


def marksidor(
    frekvenser_hz: Flyttal,
    variabler: Segmentvariabler,
    hojdfaktor: float,
    sigma: float,
    rahet_m: float,
    vinkel_bild_rad: float,
    atmosfar: Atmosfar,
    *,
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    skuggzon: Skuggzonslasning | None = None,
) -> list[Marksida | None]:
    """Ett reflekterande segment vid alla frekvenser: räta eller krökta strålar efter atmosfären.

    Homogen atmosfär utan fluktuerande refraktion går genom `marksida` (räta strålar, som
    före refraktionen infördes), allt annat genom `marksidor_refraktion`.
    """
    if atmosfar.har_refraktion or atmosfar.har_fluktuerande_refraktion:
        return marksidor_refraktion(
            frekvenser_hz,
            variabler,
            hojdfaktor,
            sigma,
            rahet_m,
            vinkel_bild_rad,
            atmosfar,
            uppat=uppat,
            ct2_form=ct2_form,
            skuggzon=skuggzon,
        )
    if ct2_form == "tryckt":  # C_T²/T i stället för C_T²/T², som i `delmodell1`
        atmosfar = replace(atmosfar, ct2=atmosfar.ct2 * (atmosfar.temperatur_luft_c + 273.15))
    elif ct2_form != "kvadrat":
        raise BullerFel(f"Okänd form för C_T²-termen: {ct2_form!r} (kvadrat eller tryckt).")
    return [
        marksida(float(f), variabler, hojdfaktor, sigma, rahet_m, vinkel_bild_rad, atmosfar)
        for f in frekvenser_hz
    ]


def koherenser_tva_sidor(
    frekvens_hz: float, sida_1: Marksida, sida_2: Marksida, koherens_spridningszon: float
) -> tuple[float, float, float]:
    """F₂, F₃ och F₄, AV 1106/07 ekv. (177), (179) och (181) (i delmodell 5: 210, 212, 214).

    F₂ = F_f(Δτ_S)·F_Δτ(Δτ_S, Δτ_S+)·F_c,1·F_r,1·F_s, F₃ på samma sätt för mottagarsidan och
    F₄ = F_f(Δτ_S + Δτ_R)·F_Δτ(Δτ_S + Δτ_R, Δτ_S+ + Δτ_R+)·F_c,1·F_c,2·F_r,1·F_r,2·F_s. Utan
    fluktuerande refraktion är Δτ₊ = Δτ och F_Δτ = 1 (avsnitt 5.9.2).
    """
    dtau_s, dtau_r = sida_1.gangtidsskillnad_s, sida_2.gangtidsskillnad_s
    ovre_s, ovre_r = sida_1.gangtidsskillnad_ovre_s, sida_2.gangtidsskillnad_ovre_s
    f2 = (
        koherens_bandmedelvarde(frekvens_hz, dtau_s)
        * sida_1.koherens_turbulens
        * sida_1.koherens_rahet
        * koherens_spridningszon
    ) * koherens_fluktuerande_refraktion(frekvens_hz, dtau_s, ovre_s)
    f3 = (
        koherens_bandmedelvarde(frekvens_hz, dtau_r)
        * sida_2.koherens_turbulens
        * sida_2.koherens_rahet
        * koherens_spridningszon
    ) * koherens_fluktuerande_refraktion(frekvens_hz, dtau_r, ovre_r)
    f4 = (
        koherens_bandmedelvarde(frekvens_hz, dtau_s + dtau_r)
        * sida_1.koherens_turbulens
        * sida_2.koherens_turbulens
        * sida_1.koherens_rahet
        * sida_2.koherens_rahet
        * koherens_spridningszon
    ) * koherens_fluktuerande_refraktion(frekvens_hz, dtau_s + dtau_r, ovre_s + ovre_r)
    return f2, f3, f4


def skuggzonsfaktor(sida_1: Marksida, sida_2: Marksida) -> float:
    """10^(ΔL_SZ,1/20)·10^(ΔL_SZ,2/20), faktorn framför |p̂_G| i AV 1106/07 ekv. (184)–(186)."""
    summa_db = sida_1.skuggzon_db + sida_2.skuggzon_db
    if summa_db == 0.0:
        return 1.0
    faktor: float = 10.0 ** (summa_db / 20.0)
    return faktor


def skarm_en_kant(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    form: Skarmform,
    atmosfar: Atmosfar,
    *,
    q_kilyta_kalla: Flyttal | float | None = None,
    q_kilyta_mottagare: Flyttal | float | None = None,
    koherens_spridningszon: Flyttal | float | None = None,
    fullt_koherent: bool = False,
    viktregel: Viktregel = "av1849",
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    skuggzon: Skuggzonslasning | None = None,
) -> Delmodell4Resultat:
    """Delmodell 4: ΔL₄(f) för terräng med en skärm med en kant, AV 1106/07 avsnitt 5.13.

    `form` är primärskärmens form W₁–T₁–W₂ (`terrang_skarmar.skarmform` eller
    `Terrangtolkning.primar_form`). Segmenten före W₁ och efter W₂ är reflekterande mark;
    basmodellen (5.13.1) räknas för varje par av ett segment på källsidan och ett på
    mottagarsidan och resultaten vägs ihop med Fresnelzonsvikter (5.13.2, ekv. 187–188):

        ΔL₄ = 20·lg(|p̂_SCR| · ∏_i1 ∏_i2 |p̂_G,i1,i2|^(w′_i1·w′_i2)).

    Kilytornas reflektionskoefficienter i alla fem anropen av `pwedge` (p̂_1,ff, p̂₁–p̂₄) är
    Q_avS och Q_avR enligt TC-101327 bilaga G.2, eller de givna värdena (bilaga G.5).
    `koherens_spridningszon` är F_s (avsnitt 5.19), 1 utan spridningszon. `fullt_koherent=True`
    sätter F₂ = F₃ = F₄ = 1 (ekv. 158–159 utan medelvärdesbildning) och finns för prövning mot
    spegelkällemetoden; det är inte metodens resultat.
    `viktregel` väljer hur Fresnelzonsvikterna normeras när summan på en sida är över 1
    eller 0 (`normerade_vikter`); förvalet följer AV 1849/00, `"tryckt"` är ekv. (187) bokstavligt.

    I homogen atmosfär utan fluktuerande refraktion är strålarna räta och F_Δτ = 1. Annars
    räknas markdelen med krökta strålar (`marksidor_refraktion`): p̂₁–p̂₄ får sträckor och
    gångtider ur den frekvensberoende profilen på var sida, spegelvinklarna får Δθ_Si och Δθ_Ri
    (ekv. 165), och i skuggzon gäller ekv. (184)–(186). `uppat`, `ct2_form` och `skuggzon` läses
    som i delmodell 1. Om spegelpunktens vinkel θ₁ + Δθ_Si (θ₂ + Δθ_Ri) blir större än den
    direkta strålens används den direkta strålens vinkel (stycket före ekv. 165).
    """
    frekvenser = kontrollera_frekvenser(frekvenser_hz)
    antal = frekvenser.shape[0]
    f_s_lista = per_frekvens(koherens_spridningszon, antal, "koherens_spridningszon")
    geo = terranggeometri(profil)
    stralar = skarmstralar(profil, form, atmosfar, uppat)
    c = atmosfar.c_m_s
    rata = not (atmosfar.har_refraktion or atmosfar.har_fluktuerande_refraktion)
    vinklar = stralar.vinklar
    n = geo.x_m.shape[0]
    x_t, z_t = float(geo.x_m[form.index_kant]), float(geo.z_m[form.index_kant])
    q_s, q_r = kilytornas_reflektion(frekvenser, geo, form, c, q_kilyta_kalla, q_kilyta_mottagare)

    # Frekvensoberoende geometri per reflekterande segment (ekv. 164–165, 175–176).
    h_max1 = storsta_hojd_m(x_t - geo.x_s_m)
    h_max2 = storsta_hojd_m(geo.x_r_m - x_t)
    kallsida: list[tuple[Segmentvariabler, float, float, float, float]] = []
    for i in range(form.index_w1):
        xa, za = float(geo.x_m[i]), float(geo.z_m[i])
        xb, zb = float(geo.x_m[i + 1]), float(geo.z_m[i + 1])
        v = segmentvariabler(geo.x_s_m, geo.z_s_m, x_t, z_t, xa, za, xb, zb)
        faktor = hojdmodifierare(v.hojd_kalla_m, min(geo.h_s_m, h_max1)) * hojdmodifierare(
            v.hojd_mottagare_m, h_max1
        )  # r_S1·r_R1, ekv. (175)
        x_bild, z_bild = bildpunkt(xa, za, xb, zb, geo.x_s_m, geo.z_s_m)
        theta1_bild = vinkel_fran_lodlinjen_rad(z_bild - z_t, x_t - x_bild)
        kallsida.append(
            (v, faktor, float(geo.flodesresistivitet_kNsm4[i]), float(geo.rahet_m[i]), theta1_bild)
        )
    mottagarsida: list[tuple[Segmentvariabler, float, float, float, float]] = []
    for i in range(form.index_w2, n - 1):
        xa, za = float(geo.x_m[i]), float(geo.z_m[i])
        xb, zb = float(geo.x_m[i + 1]), float(geo.z_m[i + 1])
        v = segmentvariabler(x_t, z_t, geo.x_r_m, geo.z_r_m, xa, za, xb, zb)
        faktor = hojdmodifierare(v.hojd_mottagare_m, min(geo.h_r_m, h_max2)) * hojdmodifierare(
            v.hojd_kalla_m, h_max2
        )  # r_R2·r_S2, ekv. (176)
        x_bild, z_bild = bildpunkt(xa, za, xb, zb, geo.x_r_m, geo.z_r_m)
        theta2_bild = vinkel_fran_lodlinjen_rad(z_bild - z_t, x_bild - x_t)
        mottagarsida.append(
            (v, faktor, float(geo.flodesresistivitet_kNsm4[i]), float(geo.rahet_m[i]), theta2_bild)
        )
    # Ekv. (166)–(174): varje segment vid alla frekvenser (räta eller krökta strålar).
    alla_1 = [
        marksidor(
            frekvenser, v, fak, sig, r, th, atmosfar,
            uppat=uppat, ct2_form=ct2_form, skuggzon=skuggzon,
        )
        for v, fak, sig, r, th in kallsida
    ]  # fmt: skip
    alla_2 = [
        marksidor(
            frekvenser, v, fak, sig, r, th, atmosfar,
            uppat=uppat, ct2_form=ct2_form, skuggzon=skuggzon,
        )
        for v, fak, sig, r, th in mottagarsida
    ]  # fmt: skip

    delta_l = np.empty(antal, dtype=np.float64)
    skarm_db = np.empty(antal, dtype=np.float64)
    markfaktor = np.empty(antal, dtype=np.float64)
    w1t_ut = np.empty(antal, dtype=np.float64)
    w2t_ut = np.empty(antal, dtype=np.float64)
    for nr, f_np in enumerate(frekvenser):
        f = float(f_np)
        f_s = 1.0 if f_s_lista is None else f_s_lista[nr]
        qs, qr = complex(q_s[nr]), complex(q_r[nr])

        def tryck(
            theta_s: float,
            theta_r: float,
            vag_s: float,
            vag_r: float,
            tid_s: float,
            tid_r: float,
            f: float = f,
            qs: complex = qs,
            qr: complex = qr,
        ) -> complex:
            """p̂ = pwedge(…), ekv. (160), för ett av trycken p̂₁–p̂₄.

            Räta strålar: τ = R/c. Krökta: gångtiderna ur ekv. (167) och (171).
            """
            if rata:
                kil = kil_rata_stralar(vinklar.beta_rad, theta_s, theta_r, vag_s, vag_r, c)
            else:
                kil = Kil(
                    beta_rad=vinklar.beta_rad,
                    theta_s_rad=theta_s,
                    theta_r_rad=theta_r,
                    gangtid_s=tid_s + tid_r,
                    gangtid_kalla_s=tid_s,
                    gangtid_mottagare_s=tid_r,
                    vag_m=vag_s + vag_r,
                    vag_kalla_m=vag_s,
                    vag_mottagare_m=vag_r,
                )
            return kiltryck_reflektionskoefficienter(f, kil, qs, qr)

        p_ff = kiltryck_reflektionskoefficienter(f, stralar.kil, qs, qr)  # ekv. (160)
        p_scr = abs(p_ff) * geo.r_sr_m  # ekv. (163)

        sidor_1 = [segment[nr] for segment in alla_1]
        sidor_2 = [segment[nr] for segment in alla_2]
        w_prim_1, w_prim_2, w_q1, w_q2 = normerade_vikter(
            [0.0 if s is None else s.vikt_bis for s in sidor_1],
            [0.0 if s is None else s.vikt_bis for s in sidor_2],
            viktregel,
        )

        # Sidor som bidrar. En sida utan verksam markreflex (w_t = 0, eller inga segment)
        # ersätts enligt AV 1849/00 ekv. (70)–(72) av en neutral sida med vikten 1.
        par_1 = [(s, w) for s, w in zip(sidor_1, w_prim_1, strict=True) if s and w > 0.0]
        par_2 = [(s, w) for s, w in zip(sidor_2, w_prim_2, strict=True) if s and w > 0.0]
        if viktregel == "av1849":
            kil_ff = stralar.kil
            if not par_1:
                par_1 = [
                    (
                        neutral_sida(
                            kil_ff.vag_kalla_m, vinklar.theta1_rad, kil_ff.gangtid_kalla_s
                        ),
                        1.0,
                    )
                ]
            if not par_2:
                par_2 = [
                    (
                        neutral_sida(
                            kil_ff.vag_mottagare_m, vinklar.theta2_rad, kil_ff.gangtid_mottagare_s
                        ),
                        1.0,
                    )
                ]

        lg_pg = 0.0  # Σ w′_i1·w′_i2·ln|p̂_G,i1,i2|
        theta_s0 = stralar.kil.theta_s_rad
        theta_r0 = stralar.kil.theta_r_rad
        for s1, w1 in par_1:
            # Ekv. (165): θ_Si = 2π − θ₁ − β₂ − Δθ_Si, dock inte mindre än θ_S (se docstring).
            theta_si = (
                2.0 * math.pi
                - s1.vinkel_bild_rad
                - vinklar.beta2_rad
                - s1.vinkelandring_mottagare_rad
            )
            if s1.bildvinkel_som_direkt or (not rata and theta_si < theta_s0):
                theta_si = theta_s0
            r1s, r2s = s1.vag_direkt_m, s1.vag_reflekterad_m
            t1s, t2s = s1.gangtid_direkt_s, s1.gangtid_reflekterad_s
            for s2, w2 in par_2:
                # Ekv. (165): θ_Ri = θ₂ − β₂ + Δθ_Ri, dock inte större än θ_R.
                theta_ri = s2.vinkel_bild_rad - vinklar.beta2_rad + s2.vinkelandring_kalla_rad
                if s2.bildvinkel_som_direkt or (not rata and theta_ri > theta_r0):
                    theta_ri = theta_r0
                r1r, r2r = s2.vag_direkt_m, s2.vag_reflekterad_m
                t1r, t2r = s2.gangtid_direkt_s, s2.gangtid_reflekterad_s
                p1 = tryck(theta_s0, theta_r0, r1s, r1r, t1s, t1r)
                p2 = tryck(theta_si, theta_r0, r2s, r1r, t2s, t1r)
                p3 = tryck(theta_s0, theta_ri, r1s, r2r, t1s, t2r)
                p4 = tryck(theta_si, theta_ri, r2s, r2r, t2s, t2r)
                if fullt_koherent:
                    f2 = f3 = f4 = 1.0
                else:
                    f2, f3, f4 = koherenser_tva_sidor(f, s1, s2, f_s)  # ekv. (177)–(181)
                p_g = markfaktor_basmodell(
                    p1,
                    p2,
                    p3,
                    p4,
                    s1.reflektion,
                    s2.reflektion,
                    w_q1,
                    w_q2,
                    f2,
                    f3,
                    f4,
                    s1.inkoherent,
                    s2.inkoherent,
                )
                p_g *= skuggzonsfaktor(s1, s2)  # ekv. (184)–(186)
                if not p_g > 0.0:
                    raise BullerFel(
                        "Markfaktorn |p_G| blev 0 för ett segmentpar: ΔL₄ är inte definierad."
                    )
                lg_pg += w1 * w2 * math.log(p_g)

        pg_tot = math.exp(lg_pg)
        if not p_scr > 0.0:
            raise BullerFel("Skärmens tryck |p_SCR| blev 0: ΔL₄ är inte definierad.")
        skarm_db[nr] = 20.0 * math.log10(p_scr)
        markfaktor[nr] = pg_tot
        delta_l[nr] = 20.0 * math.log10(p_scr * pg_tot)  # ekv. (188)
        w1t_ut[nr] = sum(0.0 if s is None else s.vikt_bis for s in sidor_1)
        w2t_ut[nr] = sum(0.0 if s is None else s.vikt_bis for s in sidor_2)

    return Delmodell4Resultat(
        frekvenser_hz=frekvenser,
        delta_l_db=delta_l,
        skarmeffekt_db=skarm_db,
        markfaktor_pg=markfaktor,
        q_av_kalla=q_s,
        q_av_mottagare=q_r,
        viktsumma_kalla=w1t_ut,
        viktsumma_mottagare=w2t_ut,
        stralar=stralar,
    )
