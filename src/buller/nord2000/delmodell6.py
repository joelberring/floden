"""Delmodell 6 i Nord2000: terräng med två skärmar som har var sin diffrakterande kant.

Källor (varje formel avläst från sidbild, se `docs/buller/logg/P-6.md`):

* DELTA AV 1106/07 (rev. 2014) avsnitt 5.15.1 (basmodellen, ekv. 222–267) och 5.15.2 (den
  allmänna modellen, ekv. 268–270), avsnitt 5.7.3 (p2wedge, punktlistan om vinklarna) och
  ekv. (307) i avsnitt 5.21.2.
* FORCE TC-101327 (2019) avsnitt 2.3, 2.10 och bilaga G.4: kilytornas reflektionskoefficienter
  (`delmodell6_kilben.py`).
* DELTA AV 1849/00 (rev. 2006) avsnitt 4.4.3.5, ekv. (75)–(90), för att avgöra tryckfel i
  ekv. (259) och viktnormeringen i ekv. (269).

Den första skärmen (W₁₁, T₁, W₁₂) är den närmast källan och den andra (W₂₁, T₂, W₂₂) den
närmast mottagaren. Marken delas i tre områden: före den första skärmen (index 1), mellan
skärmarna (index 2) och efter den andra (index 3). Åtta strålar räknas med spegelmetoden
(ekv. 222): med eller utan markreflex i vart och ett av de tre områdena.

Vad som räknas:

* **Skärmarnas verkan i fritt fält**, |p̂_SCR| = |p̂_1,ff|·R_SR (ekv. 225–229), med räta eller
  krökta strålar, och gångvägsskillnaden Δℓ för var och en av kanterna (ekv. 307) till kroken
  i `terrang_skarmar.tolka_terrang`.
* **Markens verkan utöver skärmarna**, |p̂_G| (ekv. 230–260, 268–269) och ΔL₆ (ekv. 270).
  I homogen atmosfär utan fluktuerande refraktion med räta strålar; annars (P-S2) med krökta
  strålar i de tre områdena som i delmodell 4 (`delmodell4.marksidor_refraktion`), Δθ i
  spegelvinklarna (ekv. 231–232), F_Δτ (ekv. 249–258) och skuggzonsfallen (ekv. 261–267).
  Refraktion när en skärmtopp ligger under linjen från den andra ger `BullerFel`
  (`skarmstralar_tva`).

Val som avviker från eller fyller ut den tryckta texten står under "Tolkningar" i loggen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from buller.nord2000.delmodell1 import Ct2Form
from buller.nord2000.delmodell4 import (
    Marksida,
    Segmentvariabler,
    Viktregel,
    bildpunkt,
    ekvivalent_skarmhojd_m,
    gangvagsskillnad_refraktion_m,
    hojdmodifierare,
    kontrollera_frekvenser,
    marksidor,
    neutral_sida,
    per_frekvens,
    segmentvariabler,
    storsta_hojd_m,
    vinkel_fran_lodlinjen_rad,
)
from buller.nord2000.delmodell6_kilben import (
    KilytorTvaSkarmar,
    kilytornas_reflektion_tva_skarmar,
    kontrollera_skarmformer,
)
from buller.nord2000.geometri import MINSTA_HOJD_M, langd, normlinje
from buller.nord2000.kildiffraktion_dubbel import (
    TvaKanter,
    Viktigast,
    tva_kanter_rata_stralar,
    tva_kilar_tryck_reflektionskoefficienter,
)
from buller.nord2000.koherens import koherens_bandmedelvarde, koherens_fluktuerande_refraktion
from buller.nord2000.refraktion import Atmosfar, calc_eq_ssp
from buller.nord2000.refraktion_mark import Skuggzonslasning
from buller.nord2000.stralar_krokta import Uppatmetod, direkt_strale
from buller.nord2000.terrang import Terranggeometri, terranggeometri
from buller.nord2000.terrang_skarmar import Skarmform, Terrangtolkning, vertikalt_avstand_m
from buller.typer import BullerFel, Flyttal, Terrangprofil

Vinkelregel = Literal["avsnitt_5_7_3", "ekv_227_228"]
"""Mot vilken punkt θ_1R och θ_2S mäts för strålarna utan reflex mellan skärmarna.

`"avsnitt_5_7_3"` (förval): mot den andra skärmtoppen när den ligger över linjen till
mottagaren (källan), annars mot mottagaren (källan), som punktlistan i avsnitt 5.7.3 säger.
`"ekv_227_228"`: alltid mot mottagaren respektive källan, som ekv. (227)–(228) är tryckta.
"""

# AV 1106/07 ekv. (269): w_Q = w_t³ när viktsumman är under 1 (AV 1849/00 ekv. 88–90).
VIKTEXPONENT_WQ = 3

# De sju strålarna utöver den direkta i ekv. (222): (reflex före första skärmen, reflex mellan
# skärmarna, reflex efter andra skärmen) för p̂₂ … p̂₈ i den ordningen.
STRALAR_2_TILL_8: tuple[tuple[bool, bool, bool], ...] = (
    (True, False, False),  # p̂₂: Q̂₁
    (False, False, True),  # p̂₃: Q̂₃
    (True, False, True),  # p̂₄: Q̂₁·Q̂₃
    (False, True, False),  # p̂₅: Q̂₂
    (True, True, False),  # p̂₆: Q̂₁·Q̂₂
    (False, True, True),  # p̂₇: Q̂₂·Q̂₃
    (True, True, True),  # p̂₈: Q̂₁·Q̂₂·Q̂₃
)


# --------------------------------------------------------------------------- skärmarna


def _vinkel_kallsida_rad(
    x_t: float, z_t: float, beta_mottagarsida: float, x: float, z: float
) -> float:
    """θ_S = 2π − θ − β_x2 för riktningen från T mot punkten (x, z) på källsidan.

    Formen i AV 1106/07 ekv. (227), (228), (231) och (232): θ = arctan((z − z_T)/(x_T − x)) + π/2
    är riktningens vinkel från lodlinjen nedåt och β_x2 kilens vinkel på mottagarsidan.
    """
    return 2.0 * math.pi - vinkel_fran_lodlinjen_rad(z - z_t, x_t - x) - beta_mottagarsida


def _vinkel_mottagarsida_rad(
    x_t: float, z_t: float, beta_mottagarsida: float, x: float, z: float
) -> float:
    """θ_R = θ − β_x2 för riktningen från T mot punkten (x, z) på mottagarsidan.

    Formen i AV 1106/07 ekv. (227), (228), (231) och (232) med
    θ = arctan((z − z_T)/(x − x_T)) + π/2.
    """
    return vinkel_fran_lodlinjen_rad(z - z_t, x - x_t) - beta_mottagarsida


@dataclass(frozen=True)
class Kilvinklar:
    """En kils vinklar ur terrängen, AV 1106/07 ekv. (227) eller (228)."""

    beta_kallsida_rad: float  # β₁₁ eller β₂₁
    beta_mottagarsida_rad: float  # β₁₂ eller β₂₂
    beta_rad: float  # β₁ eller β₂ = 2π − β_x1 − β_x2


def kilvinklar(geo: Terranggeometri, form: Skarmform) -> Kilvinklar:
    """β_x1, β_x2 och β för en skärm reducerad till W₁, T, W₂, AV 1106/07 ekv. (227)–(228)."""
    x_t, z_t = float(geo.x_m[form.index_kant]), float(geo.z_m[form.index_kant])
    beta_k = vinkel_fran_lodlinjen_rad(
        float(geo.z_m[form.index_w1]) - z_t, x_t - float(geo.x_m[form.index_w1])
    )
    beta_m = vinkel_fran_lodlinjen_rad(
        float(geo.z_m[form.index_w2]) - z_t, float(geo.x_m[form.index_w2]) - x_t
    )
    return Kilvinklar(beta_k, beta_m, 2.0 * math.pi - beta_k - beta_m)


@dataclass(frozen=True)
class SkarmstralarTva:
    """Geometri och strålar för p̂_1,ff över två skärmar, AV 1106/07 ekv. (226)–(228), (307)."""

    forsta: Skarmform
    andra: Skarmform
    kil_1: Kilvinklar
    kil_2: Kilvinklar
    kanter: TvaKanter  # indata till p2wedge för p̂_1,ff och p̂₁
    andra_over_linjen_t1_r: bool  # T₂ över linjen T₁–R: θ_1R mäts mot T₂ (avsnitt 5.7.3)
    forsta_over_linjen_t2_s: bool  # T₁ över linjen T₂–S: θ_2S mäts mot T₁
    vinkelandring_kalla_rad: float  # Δθ_S
    vinkelandring_mitten_rad: float  # Δθ_M
    vinkelandring_mottagare_rad: float  # Δθ_R
    gangvagsskillnad_forsta_m: float  # Δℓ för T₁ mot S och T₂, ekv. (307)
    gangvagsskillnad_andra_m: float  # Δℓ för T₂ mot T₁ och R, ekv. (307)


def _topp(geo: Terranggeometri, form: Skarmform) -> tuple[float, float]:
    return float(geo.x_m[form.index_kant]), float(geo.z_m[form.index_kant])


def skarmstralar_tva(
    profil: Terrangprofil,
    forsta: Skarmform,
    andra: Skarmform,
    atmosfar: Atmosfar,
    *,
    vinkelregel: Vinkelregel = "avsnitt_5_7_3",
    uppat: Uppatmetod = "bokstavlig",
) -> SkarmstralarTva:
    """Variablerna för p̂_1,ff och Δℓ, AV 1106/07 avsnitt 5.15.1, ekv. (226)–(228), och ekv. (307).

    Strålarna S–T₁, T₁–T₂ och T₂–R räknas med DirectRay i baslinjens system (vinkelräta höjder,
    avstånd mellan fotpunkterna), på samma sätt som `delmodell4.skarmstralar`; för ξ = 0 är det
    de räta sträckorna. Den ekvivalenta profilen är oberoende av frekvensen: CalcEqSSP(h_Sv,
    h″_SCR), CalcEqSSP(h″_SCR, h″_SCR) och CalcEqSSP(h_Rv, h″_SCR), med h″_SCR ur ekv. (22) räknad
    för respektive skärmtopp.

    Vinklarna: θ_1S = 2π − θ₁₁ − β₁₂ − Δθ_S och θ_2R = θ₂₂ − β₂₂ + Δθ_R. θ_1R och θ_2S mäts
    enligt `vinkelregel`. Med förvalet mäts θ_1R mot T₂ (θ₁₂ mot T₂, + Δθ_M) när T₂ ligger över
    linjen T₁–R och annars mot R; θ_2S mot T₁ (− Δθ_M) när T₁ ligger över linjen T₂–S och annars
    mot S. Fallet "annars" är bara definierat för räta strålar och ger `BullerFel` med refraktion.

    Δℓ (ekv. 307) räknas alltid mot grannkanten: för T₁ med R_S, R_M, θ_1S och θ_1R mot T₂, för
    T₂ med R_M, R_R, θ_2S mot T₁ och θ_2R, i den rättade formen i
    `delmodell4.gangvagsskillnad_refraktion_m` (ekv. 307 är tryckt som ekv. 301).
    """
    if vinkelregel not in ("avsnitt_5_7_3", "ekv_227_228"):
        raise BullerFel(f"Okänd vinkelregel: {vinkelregel!r} (avsnitt_5_7_3 eller ekv_227_228).")
    geo = terranggeometri(profil)
    kontrollera_skarmformer(geo, forsta, andra)
    x_t1, z_t1 = _topp(geo, forsta)
    x_t2, z_t2 = _topp(geo, andra)
    if not geo.x_s_m < x_t1 < x_t2 < geo.x_r_m:
        raise BullerFel("Skärmkanterna ska ligga mellan källan och mottagaren, T₁ före T₂.")
    kil_1 = kilvinklar(geo, forsta)
    kil_2 = kilvinklar(geo, andra)

    x_ps, z_ps = float(profil.x_m[0]), float(profil.z_m[0])
    x_pr, z_pr = float(profil.x_m[-1]), float(profil.z_m[-1])
    xs0, zs0, h_sb = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_s_m, geo.z_s_m)
    xr0, zr0, h_rb = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_r_m, geo.z_r_m)
    x10, z10, h_scr1 = normlinje(x_ps, z_ps, x_pr, z_pr, x_t1, z_t1)
    x20, z20, h_scr2 = normlinje(x_ps, z_ps, x_pr, z_pr, x_t2, z_t2)
    if not (h_scr1 > 0.0 and h_scr2 > 0.0):
        raise BullerFel(
            "En skärmkant ligger inte över terrängens baslinje (h_SCR ≤ 0): den kan inte vara "
            "kant i delmodell 6 (AV 1106/07 avsnitt 5.21.1–5.21.2)."
        )
    d1 = langd(xs0, zs0, x10, z10)
    d2 = langd(x10, z10, x20, z20)
    d3 = langd(x20, z20, xr0, zr0)

    if atmosfar.har_refraktion:
        h_sv = max(profil.kalla_hojd_m, MINSTA_HOJD_M)
        h_rv = max(profil.mottagare_hojd_m, MINSTA_HOJD_M)
        h_bis1 = ekvivalent_skarmhojd_m(
            geo.r_sr_m,
            langd(geo.x_s_m, geo.z_s_m, x_t1, z_t1),
            langd(geo.x_r_m, geo.z_r_m, x_t1, z_t1),
        )
        h_bis2 = ekvivalent_skarmhojd_m(
            geo.r_sr_m,
            langd(geo.x_s_m, geo.z_s_m, x_t2, z_t2),
            langd(geo.x_r_m, geo.z_r_m, x_t2, z_t2),
        )
        profil_s = calc_eq_ssp(h_sv, h_bis1, atmosfar)
        profil_m = calc_eq_ssp(h_bis1, h_bis2, atmosfar)
        profil_r = calc_eq_ssp(h_rv, h_bis2, atmosfar)
        xi_s, c_s = profil_s.xi_per_m, profil_s.c0_m_s
        xi_m, c_m = profil_m.xi_per_m, profil_m.c0_m_s
        xi_r, c_r = profil_r.xi_per_m, profil_r.c0_m_s
    else:
        c = atmosfar.c_m_s
        xi_s, c_s, xi_m, c_m, xi_r, c_r = 0.0, c, 0.0, c, 0.0, c
    strale_s = direkt_strale(d1, h_sb, h_scr1, xi_s, c_s, uppat)
    strale_m = direkt_strale(d2, h_scr1, h_scr2, xi_m, c_m, uppat)
    strale_r = direkt_strale(d3, h_rb, h_scr2, xi_r, c_r, uppat)

    theta_1s = (
        _vinkel_kallsida_rad(x_t1, z_t1, kil_1.beta_mottagarsida_rad, geo.x_s_m, geo.z_s_m)
        - strale_s.vinkelandring_rad
    )
    theta_2r = (
        _vinkel_mottagarsida_rad(x_t2, z_t2, kil_2.beta_mottagarsida_rad, geo.x_r_m, geo.z_r_m)
        + strale_r.vinkelandring_rad
    )
    theta_1r_mot_t2 = (
        _vinkel_mottagarsida_rad(x_t1, z_t1, kil_1.beta_mottagarsida_rad, x_t2, z_t2)
        + strale_m.vinkelandring_rad
    )
    theta_2s_mot_t1 = (
        _vinkel_kallsida_rad(x_t2, z_t2, kil_2.beta_mottagarsida_rad, x_t1, z_t1)
        - strale_m.vinkelandring_rad
    )
    andra_over = vertikalt_avstand_m(x_t1, z_t1, geo.x_r_m, geo.z_r_m, x_t2, z_t2) > 0.0
    forsta_over = vertikalt_avstand_m(x_t2, z_t2, geo.x_s_m, geo.z_s_m, x_t1, z_t1) > 0.0
    mot_t2 = andra_over and vinkelregel == "avsnitt_5_7_3"
    mot_t1 = forsta_over and vinkelregel == "avsnitt_5_7_3"
    if atmosfar.har_refraktion and not (mot_t2 and mot_t1):
        raise BullerFel(
            "Delmodell 6 med refraktion: θ_1R mot mottagaren eller θ_2S mot källan (en skärmtopp "
            "under linjen från den andra, eller vinkelregeln ekv_227_228) är bara definierat för "
            "räta strålar; Δθ för strålarna T₁–R och S–T₂ ges inte av AV 1106/07 ekv. (226)."
        )
    theta_1r = (
        theta_1r_mot_t2
        if mot_t2
        else _vinkel_mottagarsida_rad(x_t1, z_t1, kil_1.beta_mottagarsida_rad, geo.x_r_m, geo.z_r_m)
    )
    theta_2s = (
        theta_2s_mot_t1
        if mot_t1
        else _vinkel_kallsida_rad(x_t2, z_t2, kil_2.beta_mottagarsida_rad, geo.x_s_m, geo.z_s_m)
    )
    kanter = TvaKanter(
        beta_1_rad=kil_1.beta_rad,
        theta_1s_rad=theta_1s,
        theta_1r_rad=theta_1r,
        beta_2_rad=kil_2.beta_rad,
        theta_2s_rad=theta_2s,
        theta_2r_rad=theta_2r,
        gangtid_kalla_s=strale_s.gangtid_s,
        gangtid_mitten_s=strale_m.gangtid_s,
        gangtid_mottagare_s=strale_r.gangtid_s,
        vag_kalla_m=strale_s.vagstracka_m,
        vag_mitten_m=strale_m.vagstracka_m,
        vag_mottagare_m=strale_r.vagstracka_m,
    )
    return SkarmstralarTva(
        forsta=forsta,
        andra=andra,
        kil_1=kil_1,
        kil_2=kil_2,
        kanter=kanter,
        andra_over_linjen_t1_r=andra_over,
        forsta_over_linjen_t2_s=forsta_over,
        vinkelandring_kalla_rad=strale_s.vinkelandring_rad,
        vinkelandring_mitten_rad=strale_m.vinkelandring_rad,
        vinkelandring_mottagare_rad=strale_r.vinkelandring_rad,
        gangvagsskillnad_forsta_m=gangvagsskillnad_refraktion_m(
            strale_s.vagstracka_m, strale_m.vagstracka_m, theta_1s, theta_1r_mot_t2
        ),
        gangvagsskillnad_andra_m=gangvagsskillnad_refraktion_m(
            strale_m.vagstracka_m, strale_r.vagstracka_m, theta_2s_mot_t1, theta_2r
        ),
    )


def skarmar_ur_tolkning(tolkning: Terrangtolkning) -> tuple[Skarmform, Skarmform, Viktigast]:
    """Första och andra skärmens form och vilken som är viktigast, ur terrängtolkningen.

    Den primära skärmen (avsnitt 5.21.1) är den viktigaste kilen i ekv. (97)–(98) (avsnitt
    5.7.3 hänvisar till 5.21). Ligger den närmast källan är den "första", annars "andra".
    Primär och sekundär skärm kan byta plats mellan delkällor i samma fall (loggen P-T).
    """
    if tolkning.primar_form is None or tolkning.sekundar_form is None:
        raise BullerFel(
            "Delmodell 6 kräver en primär och en sekundär skärm (r_scr1 > 0 och r_scr2 > 0 i "
            "något band)."
        )
    if tolkning.primar_form.index_kant < tolkning.sekundar_form.index_kant:
        return tolkning.primar_form, tolkning.sekundar_form, "forsta"
    return tolkning.sekundar_form, tolkning.primar_form, "andra"


def skarmeffekt_fritt_falt_db(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    forsta: Skarmform,
    andra: Skarmform,
    viktigast: Viktigast,
    atmosfar: Atmosfar,
    *,
    q_1_kalla: Flyttal | float | None = None,
    q_1_mottagare: Flyttal | float | None = None,
    q_2_kalla: Flyttal | float | None = None,
    q_2_mottagare: Flyttal | float | None = None,
    vinkelregel: Vinkelregel = "avsnitt_5_7_3",
    uppat: Uppatmetod = "bokstavlig",
) -> tuple[Flyttal, SkarmstralarTva]:
    """20·lg|p̂_SCR| = 20·lg(|p̂_1,ff|·R_SR) per frekvens, AV 1106/07 ekv. (225)–(229).

    Gäller även med refraktion (krökta strålar, ekv. 226). p̂_1,ff = p2wedge(…) (ekv. 97–98 med
    TC-101327 2.4) med kilytornas koefficienter ur TC-101327 bilaga G.4. `viktigast` anger om
    den primära skärmen är den första eller den andra. Returnerar också `SkarmstralarTva`, där
    `gangvagsskillnad_forsta_m` och `gangvagsskillnad_andra_m` är Δℓ enligt ekv. (307); den som
    hör till den sekundära skärmen går till kroken `gangvagsskillnad_t2_m` i
    `terrang_skarmar.tolka_terrang`.
    """
    frekvenser = kontrollera_frekvenser(frekvenser_hz)
    stralar = skarmstralar_tva(
        profil, forsta, andra, atmosfar, vinkelregel=vinkelregel, uppat=uppat
    )
    geo = terranggeometri(profil)
    ytor = kilytornas_reflektion_tva_skarmar(
        frekvenser,
        geo,
        forsta,
        andra,
        atmosfar.c_m_s,
        q_1_kalla=q_1_kalla,
        q_1_mottagare=q_1_mottagare,
        q_2_kalla=q_2_kalla,
        q_2_mottagare=q_2_mottagare,
    )
    niva = np.empty(frekvenser.shape[0], dtype=np.float64)
    for i, f_np in enumerate(frekvenser):
        p = tva_kilar_tryck_reflektionskoefficienter(
            float(f_np),
            stralar.kanter,
            complex(ytor.q_1_kalla[i]),
            complex(ytor.q_1_mottagare[i]),
            complex(ytor.q_2_kalla[i]),
            complex(ytor.q_2_mottagare[i]),
            viktigast,
        )
        if not abs(p) > 0.0:
            raise BullerFel("Skärmarnas tryck |p_1,ff| blev 0: skärmeffekten är inte definierad.")
        niva[i] = 20.0 * math.log10(abs(p) * geo.r_sr_m)
    return niva, stralar


# --------------------------------------------------------------------------- basmodellen


def markfaktor_tre_ytor(
    tryck: tuple[complex, complex, complex, complex, complex, complex, complex, complex],
    reflektion: tuple[complex, complex, complex],
    vikt_q: tuple[float, float, float],
    koherens: tuple[float, float, float, float, float, float, float],
    inkoherent: tuple[float, float, float],
) -> float:
    """|p̂_G| för en kombination av tre marksegment, AV 1106/07 avsnitt 5.15.1, ekv. (259).

    `tryck` är p̂₁ … p̂₈ i ekv. (222), `reflektion` Q̂₁, Q̂₂, Q̂₃ (före första skärmen, mellan
    skärmarna, efter andra skärmen), `vikt_q` w_Q1, w_Q2, w_Q3 (ekv. 269), `koherens` F₂ … F₈
    (ekv. 249–258) och `inkoherent` ℜ₁, ℜ₂, ℜ₃ (ekv. 260). Med n = 2 … 8:

        |p̂_G|² = |1 + Σ F_n·K_n·p̂_n/p̂₁|² + Σ (1 − F_n²)·|I_n·p̂_n/p̂₁|²,

    där K_n är produkten av w_Q·Q̂ och I_n produkten av w_Q·ℜ för de områden där stråle n
    reflekteras, enligt ekv. (224). Två termer i den tryckta ekv. (259) avviker från ekv. (224)
    och räknas som ekv. (224) anger: F₇-termen har p̂₇ (tryckt p̂₄) och den inkoherenta
    F₆-termen har w_Q1·ℜ₁·w_Q2·ℜ₂ (tryckt w_Q2·ℜ₂·w_Q3·ℜ₃). Med alla F = 1 är resultatet
    beloppet av parentesen i ekv. (224).
    """
    p1 = tryck[0]
    if p1 == 0.0:
        raise BullerFel("Det diffrakterade trycket p₁ är 0: |p_G| i ekv. (259) är odefinierat.")
    koherent = 1.0 + 0.0j
    inkoherent_summa = 0.0
    for nr, reflexer in enumerate(STRALAR_2_TILL_8):
        k = 1.0 + 0.0j
        i = 1.0
        for omrade, med in enumerate(reflexer):
            if med:
                k *= vikt_q[omrade] * reflektion[omrade]
                i *= vikt_q[omrade] * inkoherent[omrade]
        kvot = tryck[nr + 1] / p1
        f_n = koherens[nr]
        koherent += f_n * k * kvot
        inkoherent_summa += (1.0 - f_n * f_n) * abs(i * kvot) ** 2
    return math.sqrt(abs(koherent) ** 2 + inkoherent_summa)


def normerade_vikter_tre(
    vikter_bis: tuple[list[float], list[float], list[float]], viktregel: Viktregel = "av1849"
) -> tuple[tuple[list[float], list[float], list[float]], tuple[float, float, float]]:
    """w′_i1, w′_i2, w′_i3 och w_Q1, w_Q2, w_Q3, AV 1106/07 avsnitt 5.15.2, ekv. (268)–(269).

    w_kt = Σ w″_ik för område k. w_Q = 1 när summan är minst 1, annars summan i kubik. För
    0 < w_t ≤ 1 är w′ = w″/w_t. Grenarna w_t > 1 och w_t = 0 räknas enligt `viktregel`, med
    samma val och samma alternativ som `delmodell4.normerade_vikter` (loggen P-4, tolkning 1–2):

    * `"av1849"` (förval): w′ = w″ när w_t > 1 (AV 1849/00 ekv. 85–87); ett område med w_t = 0
      får w′ = 0 och w_Q = 0 här och ersätts i `tva_skarmar` av ett neutralt område med vikten 1,
      så att de andra områdenas exponenter är orörda.
    * `"tryckt"`: ekv. (269) bokstavligt, w′ = w″/w_t·(Δw_kt/Δw_t + 1) för w_t > 1 och w′ = 0
      för w_t = 0.

    Den tryckta grenen för w_t > 1 har samma språng som ekv. (187): när bara ett område
    överskrider 1 är Δw_kt/Δw_t = 1 och Σw′ hoppar från 1 till 2 i w_t = 1.
    """
    if viktregel not in ("av1849", "tryckt"):
        raise BullerFel(f"Okänd viktregel: {viktregel!r} (av1849 eller tryckt).")
    summor = [sum(v) for v in vikter_bis]
    overskott = [s - 1.0 if s > 1.0 else 0.0 for s in summor]
    dw = sum(overskott)

    def normera(vikter: list[float], summa: float, over: float) -> list[float]:
        if summa > 1.0:
            if viktregel == "av1849":
                return list(vikter)
            return [w / summa * (over / dw + 1.0) for w in vikter]
        if summa > 0.0:
            return [w / summa for w in vikter]
        return [0.0 for _ in vikter]

    def w_q(summa: float) -> float:
        return 1.0 if summa >= 1.0 else summa**VIKTEXPONENT_WQ

    return (
        (
            normera(vikter_bis[0], summor[0], overskott[0]),
            normera(vikter_bis[1], summor[1], overskott[1]),
            normera(vikter_bis[2], summor[2], overskott[2]),
        ),
        (w_q(summor[0]), w_q(summor[1]), w_q(summor[2])),
    )


# --------------------------------------------------------------------------- den allmänna modellen


@dataclass(frozen=True)
class _Segment:
    """Frekvensoberoende geometri för ett reflekterande segment i ett av de tre områdena."""

    variabler: Segmentvariabler  # SegmentVariables, ekv. (230)
    hojdfaktor: float  # r_S·r_R, ekv. (246)–(248)
    sigma: float
    rahet_m: float
    vinkel_1_rad: float  # θ_Si (område 1), θ_M1i (område 2) eller θ_Ri (område 3)
    vinkel_2_rad: float  # θ_M2i (område 2); används inte i område 1 och 3


@dataclass(frozen=True)
class _Yta:
    """Ett verksamt segment vid en frekvens: `Marksida` ur delmodell 4 och bildvinklarna."""

    sida: Marksida
    vinkel_1_rad: float
    vinkel_2_rad: float
    neutral: bool = False


@dataclass(frozen=True)
class Delmodell6Resultat:
    """Utdata från delmodell 6, ett värde per frekvens där inget annat sägs."""

    frekvenser_hz: Flyttal
    delta_l_db: Flyttal  # ΔL₆, ekv. (270)
    skarmeffekt_db: Flyttal  # 20·lg|p̂_SCR|, ekv. (229)
    markfaktor_pg: Flyttal  # p_G = ∏∏∏|p̂_G|^(w′₁·w′₂·w′₃), före 20·lg (till delmodell 7)
    kilytor: KilytorTvaSkarmar  # Q_avS,1, Q′_avR,1, Q′_avS,2, Q_avR,2 och r_QM
    viktsumma_kalla: Flyttal  # w_1t, ekv. (268)
    viktsumma_mitten: Flyttal  # w_2t
    viktsumma_mottagare: Flyttal  # w_3t
    viktigast: Viktigast
    stralar: SkarmstralarTva  # geometri, indata till p2wedge och Δℓ (ekv. 307)


def _bildyta(
    omrade: int,
    segment: _Segment,
    sida: Marksida,
    direkta: tuple[float, float],
    rata: bool,
) -> _Yta:
    """Spegelpunkternas diffraktionsvinklar för ett verksamt segment, ekv. (231)–(232).

    `omrade` är 0 (före första skärmen), 1 (mellan skärmarna) eller 2 (efter andra skärmen)
    och `direkta` den direkta strålens vinklar i samma läge. Med räta strålar är vinklarna
    segmentets geometriska. Med krökta läggs den reflekterade strålens vinkeländring till:
    θ_Si − Δθ (område 0, i T₁), θ_M1i + Δθ (i T₁) och θ_M2i − Δθ (i T₂) i område 1, θ_Ri + Δθ
    (område 2, i T₂). En vinkel som då hamnar på fel sida om den direkta strålens ersätts av
    den, liksom när den reflekterade strålen saknas (skuggzon).
    """
    if rata:
        return _Yta(sida, segment.vinkel_1_rad, segment.vinkel_2_rad)
    if sida.bildvinkel_som_direkt:
        return _Yta(sida, direkta[0], direkta[1])
    if omrade == 0:
        vinkel = segment.vinkel_1_rad - sida.vinkelandring_mottagare_rad
        return _Yta(sida, max(vinkel, direkta[0]), 0.0)
    if omrade == 1:
        vinkel_t1 = segment.vinkel_1_rad + sida.vinkelandring_kalla_rad
        vinkel_t2 = segment.vinkel_2_rad - sida.vinkelandring_mottagare_rad
        return _Yta(sida, min(vinkel_t1, direkta[0]), max(vinkel_t2, direkta[1]))
    vinkel = segment.vinkel_1_rad + sida.vinkelandring_kalla_rad
    return _Yta(sida, min(vinkel, direkta[0]), 0.0)


def _segment_i_omrade(
    geo: Terranggeometri,
    index: range,
    kalla: tuple[float, float],
    mottagare: tuple[float, float],
    grans_kalla_m: float,
    grans_mottagare_m: float,
) -> list[tuple[int, Segmentvariabler, float]]:
    """SegmentVariables (ekv. 230) och r_S·r_R (ekv. 246–248) för segmenten i ett område."""
    ut: list[tuple[int, Segmentvariabler, float]] = []
    for i in index:
        v = segmentvariabler(
            kalla[0],
            kalla[1],
            mottagare[0],
            mottagare[1],
            float(geo.x_m[i]),
            float(geo.z_m[i]),
            float(geo.x_m[i + 1]),
            float(geo.z_m[i + 1]),
        )
        faktor = hojdmodifierare(v.hojd_kalla_m, grans_kalla_m) * hojdmodifierare(
            v.hojd_mottagare_m, grans_mottagare_m
        )
        ut.append((i, v, faktor))
    return ut


def tva_skarmar(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    forsta: Skarmform,
    andra: Skarmform,
    viktigast: Viktigast,
    atmosfar: Atmosfar,
    *,
    q_1_kalla: Flyttal | float | None = None,
    q_1_mottagare: Flyttal | float | None = None,
    q_2_kalla: Flyttal | float | None = None,
    q_2_mottagare: Flyttal | float | None = None,
    koherens_spridningszon: Flyttal | float | None = None,
    fullt_koherent: bool = False,
    viktregel: Viktregel = "av1849",
    vinkelregel: Vinkelregel = "avsnitt_5_7_3",
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    skuggzon: Skuggzonslasning | None = None,
) -> Delmodell6Resultat:
    """Delmodell 6: ΔL₆(f) för terräng med två skärmar, AV 1106/07 avsnitt 5.15.

    `forsta` är formen W₁₁–T₁–W₁₂ för skärmen närmast källan och `andra` formen W₂₁–T₂–W₂₂ för
    skärmen närmast mottagaren (`skarmar_ur_tolkning`); `viktigast` säger vilken av dem som är
    den primära skärmen. Segmenten före W₁₁, mellan W₁₂ och W₂₁ och efter W₂₂ är reflekterande
    mark. Basmodellen (5.15.1) räknas för varje kombination av ett segment i vart område och
    resultaten vägs ihop med Fresnelzonsvikter (5.15.2, ekv. 268–270):

        ΔL₆ = 20·lg(|p̂_SCR| · ∏_i1 ∏_i2 ∏_i3 |p̂_G,i1,i2,i3|^(w′_i1·w′_i2·w′_i3)).

    De åtta trycken räknas med p2wedge (ekv. 225) och spegelpunkternas vinklar (ekv. 231–232):
    spegelkällan i ett segment före första skärmen (θ_Si), spegelbilden av T₂ respektive T₁ i
    ett segment mellan skärmarna (θ_M1i, θ_M2i) och spegelmottagaren i ett segment efter andra
    skärmen (θ_Ri). Sträckorna är de direkta och reflekterade strålarnas längder i respektive
    område. Kilytornas koefficienter i alla anrop är Q_avS,1, Q′_avR,1, Q′_avS,2 och Q_avR,2
    (TC-101327 bilaga G.4) eller de givna värdena (bilaga G.5).

    `koherens_spridningszon` är F_s (avsnitt 5.19). `fullt_koherent=True` sätter F₂ … F₈ = 1
    (ekv. 224 utan medelvärdesbildning) och finns för prövning mot spegelkällemetoden.
    `viktregel` som i delmodell 4 (`normerade_vikter_tre`); `vinkelregel` se `Vinkelregel`.

    I homogen atmosfär utan fluktuerande refraktion är strålarna räta och F_Δτ = 1. Annars
    (P-S2, formlerna avlästa från sidbild s. 99–110) räknas markdelen i de tre områdena med
    krökta strålar på samma sätt som i delmodell 4 (`delmodell4.marksidor_refraktion`;
    ekv. 233–245 har samma form som ekv. 166–174): spegelvinklarna får den reflekterade strålens
    vinkeländring i respektive skärmtopp (ekv. 231–232), F₂–F₈ får F_Δτ med summan av Δτ och
    Δτ₊ över de områden där strålen reflekteras (ekv. 249–258), och ett område i skuggzon
    räknas enligt ekv. (261)–(267): den reflekterade strålen ersätts av den direkta,
    Q̂ = Q̂(f, τ₁, 0, Ẑ) och |p̂_G| multipliceras med 10^(ΔL_SZ/20) för området. `uppat`,
    `ct2_form` och `skuggzon` läses som i delmodell 1. En spegelvinkel som med refraktion
    hamnar på fel sida om den direkta strålens vinkel ersätts av den (texten efter ekv. 230
    och 231).

    I mittområdet är ekv. (231)–(232) tryckta med samma Δθ_Mi i båda skärmtopparna. Här används
    den reflekterade strålens vinkeländring i den topp vinkeln hör till (Δθ_S i T₁, Δθ_R i T₂),
    som i områdena utanför skärmarna; se loggen P-S2, Tolkningar.
    """
    frekvenser = kontrollera_frekvenser(frekvenser_hz)
    antal = frekvenser.shape[0]
    f_s_lista = per_frekvens(koherens_spridningszon, antal, "koherens_spridningszon")
    geo = terranggeometri(profil)
    stralar = skarmstralar_tva(
        profil, forsta, andra, atmosfar, vinkelregel=vinkelregel, uppat=uppat
    )
    c = atmosfar.c_m_s
    rata = not (atmosfar.har_refraktion or atmosfar.har_fluktuerande_refraktion)
    n = geo.x_m.shape[0]
    t1 = _topp(geo, forsta)
    t2 = _topp(geo, andra)
    s = (geo.x_s_m, geo.z_s_m)
    r = (geo.x_r_m, geo.z_r_m)
    beta_12 = stralar.kil_1.beta_mottagarsida_rad
    beta_22 = stralar.kil_2.beta_mottagarsida_rad
    ytor = kilytornas_reflektion_tva_skarmar(
        frekvenser,
        geo,
        forsta,
        andra,
        c,
        q_1_kalla=q_1_kalla,
        q_1_mottagare=q_1_mottagare,
        q_2_kalla=q_2_kalla,
        q_2_mottagare=q_2_mottagare,
    )

    # Frekvensoberoende geometri per reflekterande segment (ekv. 230–232, 246–248).
    h_max1 = storsta_hojd_m(t1[0] - s[0])
    h_max2 = storsta_hojd_m(t2[0] - t1[0])
    h_max3 = storsta_hojd_m(r[0] - t2[0])
    omrade_1: list[_Segment] = []
    for i, v, faktor in _segment_i_omrade(
        geo, range(forsta.index_w1), s, t1, min(geo.h_s_m, h_max1), h_max1
    ):
        linje = (float(geo.x_m[i]), float(geo.z_m[i]), float(geo.x_m[i + 1]), float(geo.z_m[i + 1]))
        bild = bildpunkt(*linje, *s)
        theta_si = _vinkel_kallsida_rad(*t1, beta_12, *bild)  # ekv. (231)
        omrade_1.append(
            _Segment(
                v,
                faktor,
                float(geo.flodesresistivitet_kNsm4[i]),
                float(geo.rahet_m[i]),
                theta_si,
                0.0,
            )
        )
    omrade_2: list[_Segment] = []
    for i, v, faktor in _segment_i_omrade(
        geo, range(forsta.index_w2, andra.index_w1), t1, t2, h_max2, h_max2
    ):
        linje = (float(geo.x_m[i]), float(geo.z_m[i]), float(geo.x_m[i + 1]), float(geo.z_m[i + 1]))
        bild_t2 = bildpunkt(*linje, *t2)
        bild_t1 = bildpunkt(*linje, *t1)
        theta_m1i = _vinkel_mottagarsida_rad(*t1, beta_12, *bild_t2)  # ekv. (231)
        theta_m2i = _vinkel_kallsida_rad(*t2, beta_22, *bild_t1)  # ekv. (232)
        omrade_2.append(
            _Segment(
                v,
                faktor,
                float(geo.flodesresistivitet_kNsm4[i]),
                float(geo.rahet_m[i]),
                theta_m1i,
                theta_m2i,
            )
        )
    omrade_3: list[_Segment] = []
    for i, v, faktor in _segment_i_omrade(
        geo, range(andra.index_w2, n - 1), t2, r, h_max3, min(geo.h_r_m, h_max3)
    ):
        linje = (float(geo.x_m[i]), float(geo.z_m[i]), float(geo.x_m[i + 1]), float(geo.z_m[i + 1]))
        bild = bildpunkt(*linje, *r)
        theta_ri = _vinkel_mottagarsida_rad(*t2, beta_22, *bild)  # ekv. (232)
        omrade_3.append(
            _Segment(
                v,
                faktor,
                float(geo.flodesresistivitet_kNsm4[i]),
                float(geo.rahet_m[i]),
                theta_ri,
                0.0,
            )
        )
    omraden = (omrade_1, omrade_2, omrade_3)
    k0 = stralar.kanter
    # Neutrala områden (ingen verksam markreflex): den direkta strålens sträckor och vinklar.
    neutrala = (
        _Yta(
            neutral_sida(k0.vag_kalla_m, 0.0, k0.gangtid_kalla_s),
            k0.theta_1s_rad,
            0.0,
            neutral=True,
        ),
        _Yta(
            neutral_sida(k0.vag_mitten_m, 0.0, k0.gangtid_mitten_s),
            k0.theta_1r_rad,
            k0.theta_2s_rad,
            neutral=True,
        ),
        _Yta(
            neutral_sida(k0.vag_mottagare_m, 0.0, k0.gangtid_mottagare_s),
            k0.theta_2r_rad,
            0.0,
            neutral=True,
        ),
    )
    # Ekv. (233)–(245): varje segment vid alla frekvenser (räta eller krökta strålar).
    alla_sidor = [
        [
            marksidor(
                frekvenser, seg.variabler, seg.hojdfaktor, seg.sigma, seg.rahet_m, 0.0, atmosfar,
                uppat=uppat, ct2_form=ct2_form, skuggzon=skuggzon,
            )
            for seg in omrade
        ]
        for omrade in omraden
    ]  # fmt: skip
    direkta_vinklar = (
        (k0.theta_1s_rad, 0.0),
        (k0.theta_1r_rad, k0.theta_2s_rad),
        (k0.theta_2r_rad, 0.0),
    )

    delta_l = np.empty(antal, dtype=np.float64)
    skarm_db = np.empty(antal, dtype=np.float64)
    markfaktor = np.empty(antal, dtype=np.float64)
    viktsummor = np.empty((3, antal), dtype=np.float64)
    for nr, f_np in enumerate(frekvenser):
        f = float(f_np)
        f_s = 1.0 if f_s_lista is None else f_s_lista[nr]
        q_ytor = (
            complex(ytor.q_1_kalla[nr]),
            complex(ytor.q_1_mottagare[nr]),
            complex(ytor.q_2_kalla[nr]),
            complex(ytor.q_2_mottagare[nr]),
        )

        def tryck(
            theta_1s: float,
            theta_1r: float,
            theta_2s: float,
            theta_2r: float,
            vag: tuple[float, float, float],
            tid: tuple[float, float, float],
            f: float = f,
            q_ytor: tuple[complex, complex, complex, complex] = q_ytor,
        ) -> complex:
            """p̂ = p2wedge(…), ekv. (225), för ett av trycken p̂₁–p̂₈ (räta strålar: τ = R/c)."""
            if rata:
                kanter = tva_kanter_rata_stralar(
                    k0.beta_1_rad, theta_1s, theta_1r, k0.beta_2_rad, theta_2s, theta_2r,
                    vag[0], vag[1], vag[2], c,
                )  # fmt: skip
            else:
                kanter = TvaKanter(
                    beta_1_rad=k0.beta_1_rad,
                    theta_1s_rad=theta_1s,
                    theta_1r_rad=theta_1r,
                    beta_2_rad=k0.beta_2_rad,
                    theta_2s_rad=theta_2s,
                    theta_2r_rad=theta_2r,
                    gangtid_kalla_s=tid[0],
                    gangtid_mitten_s=tid[1],
                    gangtid_mottagare_s=tid[2],
                    vag_kalla_m=vag[0],
                    vag_mitten_m=vag[1],
                    vag_mottagare_m=vag[2],
                )
            return tva_kilar_tryck_reflektionskoefficienter(f, kanter, *q_ytor, viktigast)

        p_ff = tva_kilar_tryck_reflektionskoefficienter(f, k0, *q_ytor, viktigast)  # ekv. (225)
        p_scr = abs(p_ff) * geo.r_sr_m  # ekv. (229)
        if not p_scr > 0.0:
            raise BullerFel("Skärmarnas tryck |p_SCR| blev 0: ΔL₆ är inte definierad.")

        sidor: list[list[_Yta | None]] = []
        for omr, omrade in enumerate(omraden):
            rad: list[_Yta | None] = []
            for seg, per_band in zip(omrade, alla_sidor[omr], strict=True):
                sida = per_band[nr]
                if sida is None:
                    rad.append(None)
                    continue
                rad.append(_bildyta(omr, seg, sida, direkta_vinklar[omr], rata))
            sidor.append(rad)
        vikter_bis = (
            [0.0 if y is None else y.sida.vikt_bis for y in sidor[0]],
            [0.0 if y is None else y.sida.vikt_bis for y in sidor[1]],
            [0.0 if y is None else y.sida.vikt_bis for y in sidor[2]],
        )
        w_prim, w_q = normerade_vikter_tre(vikter_bis, viktregel)

        # Verksamma segment per område. Ett område utan verksam markreflex (w_t = 0, eller inga
        # segment) ersätts enligt AV 1849/00 ekv. (85)–(87) av ett neutralt område med vikten 1.
        par: list[list[tuple[_Yta, float]]] = []
        for omr in range(3):
            verksamma = [
                (y, w) for y, w in zip(sidor[omr], w_prim[omr], strict=True) if y and w > 0.0
            ]
            if not verksamma and viktregel == "av1849":
                verksamma = [(neutrala[omr], 1.0)]
            par.append(verksamma)

        lg_pg = 0.0  # Σ w′_i1·w′_i2·w′_i3·ln|p̂_G,i1,i2,i3|
        for y1, w1 in par[0]:
            for y2, w2 in par[1]:
                for y3, w3 in par[2]:
                    yt = (y1, y2, y3)
                    direkt_vag = (
                        y1.sida.vag_direkt_m, y2.sida.vag_direkt_m, y3.sida.vag_direkt_m
                    )  # fmt: skip
                    direkt_tid = (
                        y1.sida.gangtid_direkt_s,
                        y2.sida.gangtid_direkt_s,
                        y3.sida.gangtid_direkt_s,
                    )
                    p_alla: list[complex] = [
                        tryck(
                            k0.theta_1s_rad, k0.theta_1r_rad, k0.theta_2s_rad, k0.theta_2r_rad,
                            direkt_vag, direkt_tid,
                        )
                    ]  # fmt: skip
                    koherens: list[float] = []
                    for med_1, med_2, med_3 in STRALAR_2_TILL_8:
                        med = (med_1, med_2, med_3)
                        if any(m and y.neutral for m, y in zip(med, yt, strict=True)):
                            # Reflex i ett neutralt område: Q̂ = ℜ = 0, strålen bidrar inte.
                            p_alla.append(0.0j)
                            koherens.append(1.0)
                            continue
                        p_alla.append(
                            tryck(
                                y1.vinkel_1_rad if med_1 else k0.theta_1s_rad,
                                y2.vinkel_1_rad if med_2 else k0.theta_1r_rad,
                                y2.vinkel_2_rad if med_2 else k0.theta_2s_rad,
                                y3.vinkel_1_rad if med_3 else k0.theta_2r_rad,
                                (
                                    y1.sida.vag_reflekterad_m if med_1 else direkt_vag[0],
                                    y2.sida.vag_reflekterad_m if med_2 else direkt_vag[1],
                                    y3.sida.vag_reflekterad_m if med_3 else direkt_vag[2],
                                ),
                                (
                                    y1.sida.gangtid_reflekterad_s if med_1 else direkt_tid[0],
                                    y2.sida.gangtid_reflekterad_s if med_2 else direkt_tid[1],
                                    y3.sida.gangtid_reflekterad_s if med_3 else direkt_tid[2],
                                ),
                            )
                        )
                        if fullt_koherent:
                            koherens.append(1.0)
                            continue
                        # Ekv. (249)–(258): F_f(ΣΔτ)·F_Δτ(ΣΔτ, ΣΔτ₊)·∏F_c·∏F_r·F_s över de
                        # områden där strålen reflekteras.
                        dtau = 0.0
                        dtau_ovre = 0.0
                        f_n = f_s
                        for m, y in zip(med, yt, strict=True):
                            if m:
                                dtau += y.sida.gangtidsskillnad_s
                                dtau_ovre += y.sida.gangtidsskillnad_ovre_s
                                f_n *= y.sida.koherens_turbulens * y.sida.koherens_rahet
                        koherens.append(
                            koherens_bandmedelvarde(f, dtau)
                            * f_n
                            * koherens_fluktuerande_refraktion(f, dtau, dtau_ovre)
                        )
                    p_g = markfaktor_tre_ytor(
                        (
                            p_alla[0], p_alla[1], p_alla[2], p_alla[3],
                            p_alla[4], p_alla[5], p_alla[6], p_alla[7],
                        ),
                        (y1.sida.reflektion, y2.sida.reflektion, y3.sida.reflektion),
                        w_q,
                        (
                            koherens[0], koherens[1], koherens[2], koherens[3],
                            koherens[4], koherens[5], koherens[6],
                        ),
                        (y1.sida.inkoherent, y2.sida.inkoherent, y3.sida.inkoherent),
                    )  # fmt: skip
                    skuggzon_db = y1.sida.skuggzon_db + y2.sida.skuggzon_db + y3.sida.skuggzon_db
                    if skuggzon_db != 0.0:  # ekv. (261)–(267)
                        p_g *= 10.0 ** (skuggzon_db / 20.0)
                    if not p_g > 0.0:
                        raise BullerFel(
                            "Markfaktorn |p_G| blev 0 för en segmentkombination: ΔL₆ är inte "
                            "definierad."
                        )
                    lg_pg += w1 * w2 * w3 * math.log(p_g)

        pg_tot = math.exp(lg_pg)
        skarm_db[nr] = 20.0 * math.log10(p_scr)
        markfaktor[nr] = pg_tot
        delta_l[nr] = 20.0 * math.log10(p_scr * pg_tot)  # ekv. (270)
        for omr in range(3):
            viktsummor[omr, nr] = sum(vikter_bis[omr])

    return Delmodell6Resultat(
        frekvenser_hz=frekvenser,
        delta_l_db=delta_l,
        skarmeffekt_db=skarm_db,
        markfaktor_pg=markfaktor,
        kilytor=ytor,
        viktsumma_kalla=viktsummor[0].copy(),
        viktsumma_mitten=viktsummor[1].copy(),
        viktsumma_mottagare=viktsummor[2].copy(),
        viktigast=viktigast,
        stralar=stralar,
    )
