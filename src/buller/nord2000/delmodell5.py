"""Delmodell 5 i Nord2000: terräng med en skärm som har två diffrakterande kanter.

Tjock skärm, vall eller kulle: skärmformen W₁–T₁–T₂–W₂ med kanten T₁ närmast källan och T₂
närmast mottagaren (figur 22 och 23).

Källor (varje formel avläst från sidbild, se `docs/buller/logg/P-5.md`):

* DELTA AV 1106/07 (rev. 2014) avsnitt 5.14.1 (basmodellen, ekv. 189–219) och 5.14.2 (den
  allmänna modellen, ekv. 220–221), med p2edge ur avsnitt 5.7.4 (`kildiffraktion_dubbel.py`).
* FORCE TC-101327 (2019) avsnitt 2.3 och 2.9 med bilaga G.3 (ekv. 422–426): kilytornas
  reflektionskoefficienter Q_avS, Q′_avM och Q_avR. De ersätter Z_S = Z_iSCR,T1−1,
  Z_R = Z_iSCR,T2 och den hårda ovansidan i avsnitt 5.14.2 och 5.7.4.

Vad som räknas:

* skärmens verkan i fritt fält, |p̂_SCR| = |p̂_1,ff|·R_SR (ekv. 192–196),
* markens verkan utöver skärmen, |p̂_G| (ekv. 197–216), vägd över alla par av marksegment
  före W₁ och efter W₂ (ekv. 220), och ΔL₅ (ekv. 221).

I homogen atmosfär utan fluktuerande refraktion räknas räta strålar. Annars (P-S2, formlerna
avlästa från sidbild s. 82–89) räknas skärmens tre delsträckor med CalcEqSSP och DirectRay
(ekv. 193–195) och markdelen med krökta strålar som i delmodell 4
(`delmodell4.marksidor_refraktion`): Δθ i spegelvinklarna (ekv. 198), F_Δτ (ekv. 210–214)
och skuggzonsfallen (ekv. 217–219).

Mycket är gemensamt med delmodell 4 och hämtas därifrån: SegmentVariables, ImagePoint,
höjdfaktorerna (ekv. 208–209 = 175–176), marksidans strålar och koherens (ekv. 200–214 =
167–181), |p̂_G| för ett segmentpar (ekv. 215 = 182) och viktnormeringen (ekv. 220 = 187) med
samma valbara `viktregel`.

Vilken kant som är den viktigaste (ekv. 102 eller 103) följer av terrängtolkningen: den
primära kanten T₁ i avsnitt 5.21.1 är den viktigaste, den sekundära kanten T₁₂ i avsnitt
5.21.3 den andra. Val som avviker från eller fyller ut den tryckta texten står under
"Tolkningar" i loggen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.nord2000.delmodell1 import Ct2Form
from buller.nord2000.delmodell4 import (
    Segmentvariabler,
    Viktregel,
    bildpunkt,
    ekvivalent_skarmhojd_m,
    hojdmodifierare,
    koherenser_tva_sidor,
    kontrollera_frekvenser,
    markfaktor_basmodell,
    marksidor,
    neutral_sida,
    normerade_vikter,
    per_frekvens,
    segmentvariabler,
    skuggzonsfaktor,
    storsta_hojd_m,
    vinkel_fran_lodlinjen_rad,
)
from buller.nord2000.delmodell4_kilben import Kilben, kilben, medelreflektion_kilben
from buller.nord2000.geometri import MINSTA_HOJD_M, langd, normlinje
from buller.nord2000.kildiffraktion_dubbel import (
    TvaKanter,
    Viktigast,
    tjock_skarm_tryck_reflektionskoefficienter,
    tva_kanter_rata_stralar,
)
from buller.nord2000.refraktion import Atmosfar, calc_eq_ssp
from buller.nord2000.refraktion_mark import Skuggzonslasning
from buller.nord2000.stralar_krokta import Uppatmetod, direkt_strale
from buller.nord2000.terrang import Terranggeometri, terranggeometri
from buller.nord2000.terrang_skarmar import Skarmform
from buller.typer import BullerFel, Flyttal, Terrangprofil

# TC-101327 bilaga G.3, ekv. (425): gränser för den relativa skärmbredden r₁₂.
R12_SMAL = 0.2
R12_BRED = 0.8
# TC-101327 bilaga G.3: Q_hard, reflektionskoefficienten för en hård yta.
Q_HARD = 1.0


# --------------------------------------------------------------------------- kanternas ordning


@dataclass(frozen=True)
class Tvakantsform:
    """Skärmformen W₁–T₁–T₂–W₂ i AV 1106/07 avsnitt 5.14.2 (index i `Terranggeometri.x_m`).

    `index_t1` är kanten närmast källan (iSCR,T1) och `index_t2` kanten närmast mottagaren
    (iSCR,T2). `viktigast` anger vilken av dem som är den viktigaste kanten i avsnitt 5.7.4:
    `"forsta"` (T₁, ekv. 102) eller `"andra"` (T₂, ekv. 103).
    """

    index_w1: int
    index_t1: int
    index_t2: int
    index_w2: int
    viktigast: Viktigast


def tvakantsform(form: Skarmform, index_sekundar_kant: int) -> Tvakantsform:
    """Ordnar primär och sekundär kant till T₁ och T₂, AV 1106/07 avsnitt 5.14.2 och 5.21.3.

    `form` är primärskärmens form med den primära kanten (avsnitt 5.21.1) som `index_kant`, och
    `index_sekundar_kant` den sekundära kanten T₁₂ ur avsnitt 5.21.3 (`Skarmkant.index`).
    Delmodellen numrerar kanterna efter läge: T₁ närmast källan, T₂ närmast mottagaren. Den
    primära kanten är den viktigaste (avsnitt 5.7.4 hänvisar till 5.21), så `viktigast` blir
    `"forsta"` när den primära kanten ligger närmast källan och annars `"andra"`.
    """
    primar = form.index_kant
    if not form.index_w1 < primar < form.index_w2:
        raise BullerFel("Skärmformen kräver index W₁ < primär kant < W₂.")
    if not form.index_w1 < index_sekundar_kant < form.index_w2 or index_sekundar_kant == primar:
        raise BullerFel(
            "Den sekundära kanten ska ligga inne i skärmformen (mellan W₁ och W₂) och vara en "
            "annan terrängpunkt än den primära kanten (AV 1106/07 avsnitt 5.21.3)."
        )
    if primar < index_sekundar_kant:
        return Tvakantsform(form.index_w1, primar, index_sekundar_kant, form.index_w2, "forsta")
    return Tvakantsform(form.index_w1, index_sekundar_kant, primar, form.index_w2, "andra")


# --------------------------------------------------------------------------- skärmens geometri


@dataclass(frozen=True)
class Tvakantsvinklar:
    """Kilarnas vinklar ur terrängen, AV 1106/07 ekv. (194)–(195), räta strålar (Δθ = 0).

    β₁₁, β₁₂, β₂₁, β₂₂ är kilytornas riktningar och θ₁₁, θ₁₂, θ₂₁, θ₂₂ strålarnas riktningar,
    alla mätta från lodlinjen nedåt från respektive kant. `theta_1s_rad` … `theta_2r_rad` är
    diffraktionsvinklarna till p2edge.
    """

    beta11_rad: float
    beta12_rad: float
    beta21_rad: float
    beta22_rad: float
    theta11_rad: float
    theta12_rad: float
    theta21_rad: float
    theta22_rad: float
    beta1_rad: float
    beta2_rad: float
    theta_1s_rad: float
    theta_1r_rad: float
    theta_2s_rad: float
    theta_2r_rad: float


def tvakantsvinklar(geo: Terranggeometri, form: Tvakantsform) -> Tvakantsvinklar:
    """β₁, θ_1S, θ_1R (ekv. 194) och β₂, θ_2S, θ_2R (ekv. 195), AV 1106/07 avsnitt 5.14.1.

    Skärmformen är reducerad till de fyra punkterna W₁, T₁, T₂ och W₂ (avsnitt 5.14.2);
    punkter däremellan påverkar inte vinklarna. Max-uttrycken gör att θ_1R mäts mot T₂ när T₂
    ligger på eller över linjen T₁–R (θ_1R = 0) och annars mot R, och att θ_2S mäts mot T₁ när
    T₁ ligger på eller över linjen T₂–S (θ_2S = β₂) och annars mot S, som punktlistan i
    avsnitt 5.7.4 föreskriver. (Ekv. 194 är tryckt med ett överflödigt arctan-uttryck efter
    Max-parentesen i θ₁₂; det läses som ekv. 195, Max(…) + π/2.)
    """
    n = geo.x_m.shape[0]
    if not 0 <= form.index_w1 < form.index_t1 < form.index_t2 < form.index_w2 <= n - 1:
        raise BullerFel("Skärmen kräver index W₁ < T₁ < T₂ < W₂ inom terrängprofilen.")
    x_1, z_1 = float(geo.x_m[form.index_w1]), float(geo.z_m[form.index_w1])
    x_t1, z_t1 = float(geo.x_m[form.index_t1]), float(geo.z_m[form.index_t1])
    x_t2, z_t2 = float(geo.x_m[form.index_t2]), float(geo.z_m[form.index_t2])
    x_2, z_2 = float(geo.x_m[form.index_w2]), float(geo.z_m[form.index_w2])
    if not geo.x_s_m < x_t1 or not x_t2 < geo.x_r_m:
        raise BullerFel("Skärmkanterna ska ligga mellan källan och mottagaren.")

    beta11 = vinkel_fran_lodlinjen_rad(z_1 - z_t1, x_t1 - x_1)
    beta12 = vinkel_fran_lodlinjen_rad(z_t2 - z_t1, x_t2 - x_t1)
    theta11 = vinkel_fran_lodlinjen_rad(geo.z_s_m - z_t1, x_t1 - geo.x_s_m)
    theta12 = max(beta12, vinkel_fran_lodlinjen_rad(geo.z_r_m - z_t1, geo.x_r_m - x_t1))
    beta21 = vinkel_fran_lodlinjen_rad(z_t1 - z_t2, x_t2 - x_t1)
    beta22 = vinkel_fran_lodlinjen_rad(z_2 - z_t2, x_2 - x_t2)
    theta21 = max(beta21, vinkel_fran_lodlinjen_rad(geo.z_s_m - z_t2, x_t2 - geo.x_s_m))
    theta22 = vinkel_fran_lodlinjen_rad(geo.z_r_m - z_t2, geo.x_r_m - x_t2)
    return Tvakantsvinklar(
        beta11_rad=beta11,
        beta12_rad=beta12,
        beta21_rad=beta21,
        beta22_rad=beta22,
        theta11_rad=theta11,
        theta12_rad=theta12,
        theta21_rad=theta21,
        theta22_rad=theta22,
        beta1_rad=2.0 * math.pi - beta11 - beta12,
        beta2_rad=2.0 * math.pi - beta21 - beta22,
        theta_1s_rad=2.0 * math.pi - theta11 - beta12,
        theta_1r_rad=theta12 - beta12,
        theta_2s_rad=2.0 * math.pi - theta21 - beta22,
        theta_2r_rad=theta22 - beta22,
    )


@dataclass(frozen=True)
class Tvakantsstralar:
    """Skärmens geometri och strålar för p̂_1,ff, AV 1106/07 ekv. (193)–(195)."""

    form: Tvakantsform
    vinklar: Tvakantsvinklar  # räta linjer (utan Δθ)
    kanter: TvaKanter  # indata till p2edge: β, θ (med Δθ), τ_S, τ_M, τ_R, R_S, R_M, R_R
    hojd_over_baslinje_1_m: float  # h_SCR1
    hojd_over_baslinje_2_m: float  # h_SCR2
    vinkelandring_kalla_rad: float = 0.0  # Δθ_S
    vinkelandring_mitten_rad: float = 0.0  # Δθ_M
    vinkelandring_mottagare_rad: float = 0.0  # Δθ_R


def skarmstralar_tva_kanter(
    profil: Terrangprofil,
    form: Tvakantsform,
    atmosfar: Atmosfar,
    uppat: Uppatmetod = "exakt",
) -> Tvakantsstralar:
    """Variablerna för p̂_1,ff, AV 1106/07 avsnitt 5.14.1, ekv. (193)–(195).

    I homogen atmosfär är strålarna räta: R_S = |ST₁|, R_M = |T₁T₂|, R_R = |T₂R|, τ = R/c och
    Δθ_S = Δθ_M = Δθ_R = 0 (gränsvärdet av DirectRay, samma val som i delmodell 4).

    Med refraktion (ekv. 193, avläst från sidbild s. 82–83): profilerna CalcEqSSP(h_Sv, h″_SCR),
    CalcEqSSP(h″_SCR, h″_SCR) och CalcEqSSP(h_Rv, h″_SCR) är oberoende av frekvensen, och
    DirectRay ger (τ_S, R_S, Δθ_S) för S–T₁, (τ_M, R_M, Δθ_M) för T₁–T₂ och (τ_R, R_R, Δθ_R) för
    T₂–R. θ_1S = 2π − θ₁₁ − β₁₂ − Δθ_S, θ_1R = θ₁₂ − β₁₂ + Δθ_M, θ_2S = 2π − θ₂₁ − β₂₂ − Δθ_M och
    θ_2R = θ₂₂ − β₂₂ + Δθ_R (ekv. 194–195). Strålarna räknas i baslinjens system (vinkelräta
    höjder över baslinjen, avstånd mellan fotpunkterna), som i `delmodell4.skarmstralar`, och
    h″_SCR (ekv. 22) räknas för respektive kant, som i `delmodell6.skarmstralar_tva`.
    """
    geo = terranggeometri(profil)
    vinklar = tvakantsvinklar(geo, form)
    x_ps, z_ps = float(profil.x_m[0]), float(profil.z_m[0])
    x_pr, z_pr = float(profil.x_m[-1]), float(profil.z_m[-1])
    x_t1, z_t1 = float(geo.x_m[form.index_t1]), float(geo.z_m[form.index_t1])
    x_t2, z_t2 = float(geo.x_m[form.index_t2]), float(geo.z_m[form.index_t2])
    x10, z10, h_scr1 = normlinje(x_ps, z_ps, x_pr, z_pr, x_t1, z_t1)
    x20, z20, h_scr2 = normlinje(x_ps, z_ps, x_pr, z_pr, x_t2, z_t2)
    if not (h_scr1 > 0.0 and h_scr2 > 0.0):
        raise BullerFel(
            "En av skärmkanterna ligger inte över terrängens baslinje (h_SCR ≤ 0): den kan inte "
            "vara kant i delmodell 5 (AV 1106/07 avsnitt 5.21.1 och 5.21.3)."
        )
    r_st1 = langd(geo.x_s_m, geo.z_s_m, x_t1, z_t1)
    r_t2r = langd(x_t2, z_t2, geo.x_r_m, geo.z_r_m)
    if not atmosfar.har_refraktion:
        kanter = tva_kanter_rata_stralar(
            vinklar.beta1_rad,
            vinklar.theta_1s_rad,
            vinklar.theta_1r_rad,
            vinklar.beta2_rad,
            vinklar.theta_2s_rad,
            vinklar.theta_2r_rad,
            r_st1,
            langd(x_t1, z_t1, x_t2, z_t2),
            r_t2r,
            atmosfar.c_m_s,
        )
        return Tvakantsstralar(
            form=form,
            vinklar=vinklar,
            kanter=kanter,
            hojd_over_baslinje_1_m=h_scr1,
            hojd_over_baslinje_2_m=h_scr2,
        )

    xs0, zs0, h_sb = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_s_m, geo.z_s_m)
    xr0, zr0, h_rb = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_r_m, geo.z_r_m)
    d1 = langd(xs0, zs0, x10, z10)
    d2 = langd(x10, z10, x20, z20)
    d3 = langd(x20, z20, xr0, zr0)
    h_sv = max(profil.kalla_hojd_m, MINSTA_HOJD_M)
    h_rv = max(profil.mottagare_hojd_m, MINSTA_HOJD_M)
    h_bis1 = ekvivalent_skarmhojd_m(geo.r_sr_m, r_st1, langd(geo.x_r_m, geo.z_r_m, x_t1, z_t1))
    h_bis2 = ekvivalent_skarmhojd_m(geo.r_sr_m, langd(geo.x_s_m, geo.z_s_m, x_t2, z_t2), r_t2r)
    profil_s = calc_eq_ssp(h_sv, h_bis1, atmosfar)
    profil_m = calc_eq_ssp(h_bis1, h_bis2, atmosfar)
    profil_r = calc_eq_ssp(h_rv, h_bis2, atmosfar)
    strale_s = direkt_strale(d1, h_sb, h_scr1, profil_s.xi_per_m, profil_s.c0_m_s, uppat)
    strale_m = direkt_strale(d2, h_scr1, h_scr2, profil_m.xi_per_m, profil_m.c0_m_s, uppat)
    strale_r = direkt_strale(d3, h_rb, h_scr2, profil_r.xi_per_m, profil_r.c0_m_s, uppat)
    kanter = TvaKanter(
        beta_1_rad=vinklar.beta1_rad,
        theta_1s_rad=vinklar.theta_1s_rad - strale_s.vinkelandring_rad,
        theta_1r_rad=vinklar.theta_1r_rad + strale_m.vinkelandring_rad,
        beta_2_rad=vinklar.beta2_rad,
        theta_2s_rad=vinklar.theta_2s_rad - strale_m.vinkelandring_rad,
        theta_2r_rad=vinklar.theta_2r_rad + strale_r.vinkelandring_rad,
        gangtid_kalla_s=strale_s.gangtid_s,
        gangtid_mitten_s=strale_m.gangtid_s,
        gangtid_mottagare_s=strale_r.gangtid_s,
        vag_kalla_m=strale_s.vagstracka_m,
        vag_mitten_m=strale_m.vagstracka_m,
        vag_mottagare_m=strale_r.vagstracka_m,
    )
    return Tvakantsstralar(
        form=form,
        vinklar=vinklar,
        kanter=kanter,
        hojd_over_baslinje_1_m=h_scr1,
        hojd_over_baslinje_2_m=h_scr2,
        vinkelandring_kalla_rad=strale_s.vinkelandring_rad,
        vinkelandring_mitten_rad=strale_m.vinkelandring_rad,
        vinkelandring_mottagare_rad=strale_r.vinkelandring_rad,
    )


# --------------------------------------------------------------------------- kilytorna (bilaga G.3)


def kilben_tva_kanter(geo: Terranggeometri, form: Tvakantsform) -> tuple[Kilben, Kilben, Kilben]:
    """Kilbenen på källsidan, ovansidan och mottagarsidan, TC-101327 bilaga G.3 med G.2.

    * Källsidan: som källsidan i G.2 för en skärm med toppen T₁ – punkterna T₁, …, W₁, …, S_G,
      speglade i x.
    * Mottagarsidan: som mottagarsidan i G.2 för en skärm med toppen T₂ – punkterna T₂, …, W₂,
      …, R_G.
    * Ovansidan (mittbenet): terrängen mellan T₁ och T₂ räknad som enbart kilform utan
      terrängdel (G.2, stycket efter ekv. 420), så att hela sträckan ingår och segmentens
      ordning saknar betydelse.

    Returnerar (källsida, ovansida, mottagarsida).
    """
    n = geo.x_m.shape[0]
    t1, t2 = form.index_t1, form.index_t2
    sigma = geo.flodesresistivitet_kNsm4
    ben_kalla = kilben(
        [-float(v) for v in geo.x_m[t1::-1]],
        [float(v) for v in geo.z_m[t1::-1]],
        [float(v) for v in sigma[t1 - 1 :: -1]] if t1 >= 1 else [],
        t1 - form.index_w1,
    )
    ben_mitten = kilben(
        [float(v) for v in geo.x_m[t1 : t2 + 1]],
        [float(v) for v in geo.z_m[t1 : t2 + 1]],
        [float(v) for v in sigma[t1:t2]],
        t2 - t1,
    )
    ben_mottagare = kilben(
        [float(v) for v in geo.x_m[t2:]],
        [float(v) for v in geo.z_m[t2:]],
        [float(v) for v in sigma[t2 : n - 1]],
        form.index_w2 - t2,
    )
    return ben_kalla, ben_mitten, ben_mottagare


def relativ_skarmbredd(geo: Terranggeometri, form: Tvakantsform) -> float:
    """r₁₂ = d₁₂/d_SGRG, TC-101327 bilaga G.3, ekv. (422)–(424).

    d₁₂ = |T₁T₂| och d_SGRG = |S_G R_G|, avståndet mellan profilens första och sista punkt
    (efter ekv. 4–5). Nämnaren i ekv. (424) är tryckt d_SGSR; d_SGRG i ekv. (423) avses.
    """
    d12 = langd(
        float(geo.x_m[form.index_t1]),
        float(geo.z_m[form.index_t1]),
        float(geo.x_m[form.index_t2]),
        float(geo.z_m[form.index_t2]),
    )
    d_sgrg = langd(float(geo.x_m[0]), float(geo.z_m[0]), float(geo.x_m[-1]), float(geo.z_m[-1]))
    return d12 / d_sgrg


def overgang_hard_ovansida(r12: float) -> float:
    """r_QM(r₁₂), TC-101327 bilaga G.3, ekv. (425).

    1 för r₁₂ ≤ 0,2 (smal skärm), 0 för r₁₂ ≥ 0,8 (bred skärm), linjärt däremellan.
    """
    if not r12 >= 0.0:
        raise BullerFel("Den relativa skärmbredden r₁₂ ska vara ≥ 0.")
    if r12 <= R12_SMAL:
        return 1.0
    if r12 >= R12_BRED:
        return 0.0
    return 1.0 - (r12 - R12_SMAL) / (R12_BRED - R12_SMAL)


def modifierad_mittreflektion(q_av_mitten: float, r12: float) -> float:
    """Q′_avM = (1 − r_QM)·Q_avM + r_QM·Q_hard, TC-101327 bilaga G.3, ekv. (426).

    Smal skärm (r_QM = 1) ger hård ovansida, som i AV 1106/07 avsnitt 5.7.4; bred skärm
    (r_QM = 0) ger Q_avM. Bilagans löptext före ekv. (422) säger det omvända ("for narrow
    screens Q′_avM = Q_avM"); ekvationerna följs, se loggen under Tolkningar.
    """
    r_qm = overgang_hard_ovansida(r12)
    return (1.0 - r_qm) * q_av_mitten + r_qm * Q_HARD


def kilytornas_reflektion_tva_kanter(
    frekvenser_hz: Flyttal,
    geo: Terranggeometri,
    form: Tvakantsform,
    ljudhastighet_m_s: float,
    q_kilyta_kalla: Flyttal | float | None = None,
    q_kilyta_mitten: Flyttal | float | None = None,
    q_kilyta_mottagare: Flyttal | float | None = None,
) -> tuple[Flyttal, Flyttal, Flyttal, Flyttal]:
    """Q_avS(f), Q′_avM(f), Q_avR(f) och Q_avM(f), TC-101327 bilaga G.3 (ekv. 421–426).

    Givna värden går före bilaga G.2 och är avsedda för bilaga G.5 (Q_e = √ρ_E för en byggd
    yta). Ett givet `q_kilyta_mitten` ersätter Q_avM; övergången mot hård ovansida
    (ekv. 425–426) tillämpas även då. Returnerar (Q_avS, Q′_avM, Q_avR, Q_avM).
    """
    frekvenser = kontrollera_frekvenser(frekvenser_hz)
    antal = frekvenser.shape[0]
    givet_s = per_frekvens(q_kilyta_kalla, antal, "q_kilyta_kalla")
    givet_m = per_frekvens(q_kilyta_mitten, antal, "q_kilyta_mitten")
    givet_r = per_frekvens(q_kilyta_mottagare, antal, "q_kilyta_mottagare")
    ben_s, ben_m, ben_r = kilben_tva_kanter(geo, form)
    r12 = relativ_skarmbredd(geo, form)
    c = ljudhastighet_m_s
    q_s = np.empty(antal, dtype=np.float64)
    q_m = np.empty(antal, dtype=np.float64)
    q_m_prim = np.empty(antal, dtype=np.float64)
    q_r = np.empty(antal, dtype=np.float64)
    for i, f_np in enumerate(frekvenser):
        f = float(f_np)
        q_s[i] = medelreflektion_kilben(f, ben_s, c) if givet_s is None else givet_s[i]
        q_m[i] = medelreflektion_kilben(f, ben_m, c) if givet_m is None else givet_m[i]
        q_r[i] = medelreflektion_kilben(f, ben_r, c) if givet_r is None else givet_r[i]
        q_m_prim[i] = modifierad_mittreflektion(float(q_m[i]), r12)
    return q_s, q_m_prim, q_r, q_m


# --------------------------------------------------------------------------- skärmens verkan


def skarmeffekt_tva_kanter_fritt_falt_db(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    form: Tvakantsform,
    atmosfar: Atmosfar,
    *,
    q_kilyta_kalla: Flyttal | float | None = None,
    q_kilyta_mitten: Flyttal | float | None = None,
    q_kilyta_mottagare: Flyttal | float | None = None,
) -> tuple[Flyttal, Tvakantsstralar]:
    """20·lg|p̂_SCR| = 20·lg(|p̂_1,ff|·R_SR) per frekvens, AV 1106/07 ekv. (192)–(196).

    p̂_1,ff räknas med p2edge (avsnitt 5.7.4) där kilytornas koefficienter är Q_avS, Q′_avM och
    Q_avR ur TC-101327 bilaga G.3. Vilken av ekv. (102) och (103) som används följer av
    `form.viktigast`. Bara räta strålar.
    """
    frekvenser = kontrollera_frekvenser(frekvenser_hz)
    stralar = skarmstralar_tva_kanter(profil, form, atmosfar)
    geo = terranggeometri(profil)
    q_s, q_m, q_r, _ = kilytornas_reflektion_tva_kanter(
        frekvenser, geo, form, atmosfar.c_m_s, q_kilyta_kalla, q_kilyta_mitten, q_kilyta_mottagare
    )
    niva = np.empty(frekvenser.shape[0], dtype=np.float64)
    for i, f_np in enumerate(frekvenser):
        p = tjock_skarm_tryck_reflektionskoefficienter(
            float(f_np),
            stralar.kanter,
            complex(q_s[i]),
            complex(q_m[i]),
            complex(q_r[i]),
            form.viktigast,
        )
        if p == 0.0:
            raise BullerFel("Skärmens tryck |p_SCR| blev 0: skärmeffekten är inte definierad.")
        niva[i] = 20.0 * math.log10(abs(p) * geo.r_sr_m)
    return niva, stralar


# --------------------------------------------------------------------------- den allmänna modellen


@dataclass(frozen=True)
class Delmodell5Resultat:
    """Utdata från delmodell 5, ett värde per frekvens där inget annat sägs."""

    frekvenser_hz: Flyttal
    delta_l_db: Flyttal  # ΔL₅, ekv. (221)
    skarmeffekt_db: Flyttal  # 20·lg|p̂_SCR|, ekv. (196)
    markfaktor_pg: Flyttal  # p_G = ∏∏|p̂_G,i1,i2|^(w′_i1·w′_i2), före 20·lg (till delmodell 7)
    q_av_kalla: Flyttal  # Q_avS, TC-101327 bilaga G.3
    q_av_mitten: Flyttal  # Q′_avM, ekv. (426): det värde som används för ovansidan
    q_av_mitten_omodifierad: Flyttal  # Q_avM före ekv. (426)
    q_av_mottagare: Flyttal  # Q_avR
    viktsumma_kalla: Flyttal  # w_1t, ekv. (220)
    viktsumma_mottagare: Flyttal  # w_2t
    stralar: Tvakantsstralar  # geometri, vinklar, indata till p2edge och vald viktigaste kant


_Sidodata = tuple[Segmentvariabler, float, float, float, float]


def skarm_tva_kanter(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    form: Skarmform,
    index_sekundar_kant: int,
    atmosfar: Atmosfar,
    *,
    q_kilyta_kalla: Flyttal | float | None = None,
    q_kilyta_mitten: Flyttal | float | None = None,
    q_kilyta_mottagare: Flyttal | float | None = None,
    koherens_spridningszon: Flyttal | float | None = None,
    fullt_koherent: bool = False,
    viktregel: Viktregel = "av1849",
    viktigast: Viktigast | None = None,
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    skuggzon: Skuggzonslasning | None = None,
) -> Delmodell5Resultat:
    """Delmodell 5: ΔL₅(f) för terräng med en skärm med två kanter, AV 1106/07 avsnitt 5.14.

    `form` är primärskärmens form W₁–T–W₂ med den primära kanten som `index_kant`
    (`Terrangtolkning.primar_form`) och `index_sekundar_kant` den sekundära kanten
    (`Terrangtolkning.sekundar_kant.index`). Segmenten före W₁ och efter W₂ är reflekterande
    mark; basmodellen (5.14.1) räknas för varje par av ett segment på källsidan och ett på
    mottagarsidan och resultaten vägs ihop (5.14.2, ekv. 220–221):

        ΔL₅ = 20·lg(|p̂_SCR| · ∏_i1 ∏_i2 |p̂_G,i1,i2|^(w′_i1·w′_i2)).

    Markreflexen på källsidan räknas som om mottagaren stod i T₁ och den på mottagarsidan som
    om källan stod i T₂ (stycket före ekv. 189). De fyra trycken p̂₁–p̂₄ räknas med p2edge för
    källa/spegelkälla och mottagare/spegelmottagare (ekv. 192, 198).

    Kilytornas koefficienter i alla fem anropen av p2edge är Q_avS, Q′_avM och Q_avR enligt
    TC-101327 bilaga G.3, eller givna värden (bilaga G.5). `koherens_spridningszon` är F_s
    (avsnitt 5.19). `fullt_koherent=True` sätter F₂ = F₃ = F₄ = 1 och finns för prövning mot
    spegelkällemetoden; det är inte metodens resultat. `viktregel` väljer normeringen av
    Fresnelzonsvikterna när summan på en sida är över 1 eller 0 (ekv. 220 har samma form som
    ekv. 187, se `delmodell4.normerade_vikter`); förvalet följer AV 1849/00, `"tryckt"` är
    ekv. (220) bokstavligt. `viktigast` är till för undersökningar: `None` (förval) låter den
    primära kanten vara den viktigaste, `"forsta"` eller `"andra"` tvingar valet.

    I homogen atmosfär utan fluktuerande refraktion är strålarna räta och F_Δτ = 1. Annars
    räknas skärmens strålar enligt ekv. (193)–(195) och markdelen med krökta strålar på samma
    sätt som i delmodell 4 (`delmodell4.marksidor_refraktion`; ekv. 199–207 har samma form som
    ekv. 166–174): spegelvinklarna får Δθ_Si och Δθ_Ri (ekv. 198), F₂–F₄ får F_Δτ
    (ekv. 210–214), och skuggzonsfallen (ekv. 217–219) räknas som ekv. (184)–(186). `uppat`,
    `ct2_form` och `skuggzon` läses som i delmodell 1.
    """
    frekvenser = kontrollera_frekvenser(frekvenser_hz)
    antal = frekvenser.shape[0]
    f_s_lista = per_frekvens(koherens_spridningszon, antal, "koherens_spridningszon")
    ordnad = tvakantsform(form, index_sekundar_kant)
    if viktigast is not None:
        if viktigast not in ("forsta", "andra"):
            raise BullerFel(f'viktigast = {viktigast!r}: ska vara None, "forsta" eller "andra".')
        ordnad = Tvakantsform(
            ordnad.index_w1, ordnad.index_t1, ordnad.index_t2, ordnad.index_w2, viktigast
        )
    geo = terranggeometri(profil)
    stralar = skarmstralar_tva_kanter(profil, ordnad, atmosfar, uppat)
    vinklar = stralar.vinklar
    kanter = stralar.kanter
    c = atmosfar.c_m_s
    rata = not (atmosfar.har_refraktion or atmosfar.har_fluktuerande_refraktion)
    n = geo.x_m.shape[0]
    x_t1, z_t1 = float(geo.x_m[ordnad.index_t1]), float(geo.z_m[ordnad.index_t1])
    x_t2, z_t2 = float(geo.x_m[ordnad.index_t2]), float(geo.z_m[ordnad.index_t2])
    q_s, q_m, q_r, q_m0 = kilytornas_reflektion_tva_kanter(
        frekvenser, geo, ordnad, c, q_kilyta_kalla, q_kilyta_mitten, q_kilyta_mottagare
    )

    # Frekvensoberoende geometri per reflekterande segment (ekv. 197–198, 208–209).
    h_max1 = storsta_hojd_m(x_t1 - geo.x_s_m)
    h_max2 = storsta_hojd_m(geo.x_r_m - x_t2)
    kallsida: list[_Sidodata] = []
    for i in range(ordnad.index_w1):
        xa, za = float(geo.x_m[i]), float(geo.z_m[i])
        xb, zb = float(geo.x_m[i + 1]), float(geo.z_m[i + 1])
        v = segmentvariabler(geo.x_s_m, geo.z_s_m, x_t1, z_t1, xa, za, xb, zb)  # ekv. (197)
        faktor = hojdmodifierare(v.hojd_kalla_m, min(geo.h_s_m, h_max1)) * hojdmodifierare(
            v.hojd_mottagare_m, h_max1
        )  # r_S1·r_R1, ekv. (208)
        x_bild, z_bild = bildpunkt(xa, za, xb, zb, geo.x_s_m, geo.z_s_m)
        theta11_bild = vinkel_fran_lodlinjen_rad(z_bild - z_t1, x_t1 - x_bild)  # ekv. (198)
        kallsida.append(
            (v, faktor, float(geo.flodesresistivitet_kNsm4[i]), float(geo.rahet_m[i]), theta11_bild)
        )
    mottagarsida: list[_Sidodata] = []
    for i in range(ordnad.index_w2, n - 1):
        xa, za = float(geo.x_m[i]), float(geo.z_m[i])
        xb, zb = float(geo.x_m[i + 1]), float(geo.z_m[i + 1])
        v = segmentvariabler(x_t2, z_t2, geo.x_r_m, geo.z_r_m, xa, za, xb, zb)  # ekv. (197)
        faktor = hojdmodifierare(v.hojd_mottagare_m, min(geo.h_r_m, h_max2)) * hojdmodifierare(
            v.hojd_kalla_m, h_max2
        )  # r_R2·r_S2, ekv. (209)
        x_bild, z_bild = bildpunkt(xa, za, xb, zb, geo.x_r_m, geo.z_r_m)
        theta22_bild = vinkel_fran_lodlinjen_rad(z_bild - z_t2, x_bild - x_t2)  # ekv. (198)
        mottagarsida.append(
            (v, faktor, float(geo.flodesresistivitet_kNsm4[i]), float(geo.rahet_m[i]), theta22_bild)
        )
    # Ekv. (199)–(207): varje segment vid alla frekvenser (räta eller krökta strålar).
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
        qs, qm, qr = complex(q_s[nr]), complex(q_m[nr]), complex(q_r[nr])

        def tryck(
            theta_1s: float,
            theta_2r: float,
            vag_s: float,
            vag_r: float,
            tid_s: float,
            tid_r: float,
            f: float = f,
            qs: complex = qs,
            qm: complex = qm,
            qr: complex = qr,
        ) -> complex:
            """p̂ = p2edge(…), ekv. (192), för ett av trycken p̂₁–p̂₄.

            Bara θ_1S, θ_2R, R_S, R_R, τ_S och τ_R skiljer mellan de fyra trycken (ekv. 198);
            θ_1R, θ_2S, R_M och τ_M är skärmens egna. Räta strålar: τ = R/c.
            """
            if rata:
                geometri = tva_kanter_rata_stralar(
                    vinklar.beta1_rad,
                    theta_1s,
                    vinklar.theta_1r_rad,
                    vinklar.beta2_rad,
                    vinklar.theta_2s_rad,
                    theta_2r,
                    vag_s,
                    kanter.vag_mitten_m,
                    vag_r,
                    c,
                )
            else:
                geometri = TvaKanter(
                    beta_1_rad=kanter.beta_1_rad,
                    theta_1s_rad=theta_1s,
                    theta_1r_rad=kanter.theta_1r_rad,
                    beta_2_rad=kanter.beta_2_rad,
                    theta_2s_rad=kanter.theta_2s_rad,
                    theta_2r_rad=theta_2r,
                    gangtid_kalla_s=tid_s,
                    gangtid_mitten_s=kanter.gangtid_mitten_s,
                    gangtid_mottagare_s=tid_r,
                    vag_kalla_m=vag_s,
                    vag_mitten_m=kanter.vag_mitten_m,
                    vag_mottagare_m=vag_r,
                )
            return tjock_skarm_tryck_reflektionskoefficienter(
                f, geometri, qs, qm, qr, ordnad.viktigast
            )

        p_ff = tjock_skarm_tryck_reflektionskoefficienter(
            f, kanter, qs, qm, qr, ordnad.viktigast
        )  # ekv. (192)
        p_scr = abs(p_ff) * geo.r_sr_m  # ekv. (196)

        sidor_1 = [segment[nr] for segment in alla_1]
        sidor_2 = [segment[nr] for segment in alla_2]
        w_prim_1, w_prim_2, w_q1, w_q2 = normerade_vikter(
            [0.0 if s is None else s.vikt_bis for s in sidor_1],
            [0.0 if s is None else s.vikt_bis for s in sidor_2],
            viktregel,
        )  # ekv. (220)

        # Sidor som bidrar. En sida utan verksam markreflex (w_t = 0, eller inga segment)
        # ersätts enligt AV 1849/00 ekv. (70)–(72) av en neutral sida med vikten 1 (som i
        # delmodell 4).
        par_1 = [(s, w) for s, w in zip(sidor_1, w_prim_1, strict=True) if s and w > 0.0]
        par_2 = [(s, w) for s, w in zip(sidor_2, w_prim_2, strict=True) if s and w > 0.0]
        if viktregel == "av1849":
            if not par_1:
                par_1 = [
                    (
                        neutral_sida(
                            kanter.vag_kalla_m, vinklar.theta11_rad, kanter.gangtid_kalla_s
                        ),
                        1.0,
                    )
                ]
            if not par_2:
                par_2 = [
                    (
                        neutral_sida(
                            kanter.vag_mottagare_m, vinklar.theta22_rad, kanter.gangtid_mottagare_s
                        ),
                        1.0,
                    )
                ]

        lg_pg = 0.0  # Σ w′_i1·w′_i2·ln|p̂_G,i1,i2|
        theta_1s0 = kanter.theta_1s_rad
        theta_2r0 = kanter.theta_2r_rad
        for s1, w1 in par_1:
            # Ekv. (198): θ_1Si = 2π − θ₁₁ − β₁₂ − Δθ_Si, dock inte mindre än θ_1S.
            theta_1si = (
                2.0 * math.pi
                - s1.vinkel_bild_rad
                - vinklar.beta12_rad
                - s1.vinkelandring_mottagare_rad
            )
            if s1.bildvinkel_som_direkt or (not rata and theta_1si < theta_1s0):
                theta_1si = theta_1s0
            r1s, r2s = s1.vag_direkt_m, s1.vag_reflekterad_m
            t1s, t2s = s1.gangtid_direkt_s, s1.gangtid_reflekterad_s
            for s2, w2 in par_2:
                # Ekv. (198): θ_2Ri = θ₂₂ − β₂₂ + Δθ_Ri, dock inte större än θ_2R.
                theta_2ri = s2.vinkel_bild_rad - vinklar.beta22_rad + s2.vinkelandring_kalla_rad
                if s2.bildvinkel_som_direkt or (not rata and theta_2ri > theta_2r0):
                    theta_2ri = theta_2r0
                r1r, r2r = s2.vag_direkt_m, s2.vag_reflekterad_m
                t1r, t2r = s2.gangtid_direkt_s, s2.gangtid_reflekterad_s
                p1 = tryck(theta_1s0, theta_2r0, r1s, r1r, t1s, t1r)
                p2 = tryck(theta_1si, theta_2r0, r2s, r1r, t2s, t1r)
                p3 = tryck(theta_1s0, theta_2ri, r1s, r2r, t1s, t2r)
                p4 = tryck(theta_1si, theta_2ri, r2s, r2r, t2s, t2r)
                if fullt_koherent:
                    f2 = f3 = f4 = 1.0
                else:
                    f2, f3, f4 = koherenser_tva_sidor(f, s1, s2, f_s)  # ekv. (210)–(214)
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
                )  # ekv. (215)
                p_g *= skuggzonsfaktor(s1, s2)  # ekv. (217)–(219)
                if not p_g > 0.0:
                    raise BullerFel(
                        "Markfaktorn |p_G| blev 0 för ett segmentpar: ΔL₅ är inte definierad."
                    )
                lg_pg += w1 * w2 * math.log(p_g)

        pg_tot = math.exp(lg_pg)
        if not p_scr > 0.0:
            raise BullerFel("Skärmens tryck |p_SCR| blev 0: ΔL₅ är inte definierad.")
        skarm_db[nr] = 20.0 * math.log10(p_scr)
        markfaktor[nr] = pg_tot
        delta_l[nr] = 20.0 * math.log10(p_scr * pg_tot)  # ekv. (221)
        w1t_ut[nr] = sum(0.0 if s is None else s.vikt_bis for s in sidor_1)
        w2t_ut[nr] = sum(0.0 if s is None else s.vikt_bis for s in sidor_2)

    return Delmodell5Resultat(
        frekvenser_hz=frekvenser,
        delta_l_db=delta_l,
        skarmeffekt_db=skarm_db,
        markfaktor_pg=markfaktor,
        q_av_kalla=q_s,
        q_av_mitten=q_m,
        q_av_mitten_omodifierad=q_m0,
        q_av_mottagare=q_r,
        viktsumma_kalla=w1t_ut,
        viktsumma_mottagare=w2t_ut,
        stralar=stralar,
    )
