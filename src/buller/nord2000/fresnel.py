"""Fresnelzoner och Fresnelzonsvikter i Nord2000.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.8 samt hjälpfunktionerna i avsnitt 5.23.4
(CalcFZd, ekv. 338–339), 5.23.5 (FresnelZoneSize, ekv. 340–344), 5.23.6 (FresnelZoneW,
ekv. 345–351) och 5.23.7 (FresnelZoneWm, ekv. 352–353), med TC-101327 (2019) avsnitt 2.13.

Funktionerna utan "refraktion" i namnet gäller ξ = 0 (räta strålar), där de korrigerade
storheterna (d, h_S, h_R, d_refl med båge över) är lika med de okorrigerade. Funktionerna
med "refraktion" i namnet inför korrektionen i ekv. (346)–(348) och anropar de förra när
ξ = 0, så att svaret då är bit för bit detsamma.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from buller.nord2000.geometri import MINSTA_HOJD_M
from buller.nord2000.stralar_krokta import (
    SKUGGZONSMARGINAL,
    Uppatmetod,
    reflekterad_strale,
    skuggzonsavstand_m,
)
from buller.typer import BullerFel


def calc_fzd(r_s_m: float, r_r_m: float, theta_rad: float, f_lambda_m: float) -> float:
    """Funktionen CalcFZd, AV 1106/07 avsnitt 5.23.4, ekv. (339).

    Avståndet från reflexpunkten O till Fresnelellipsen i riktningen θ (figur 36), där
    `f_lambda_m` är produkten F_λ·λ.
    """
    r = r_s_m + r_r_m
    ell = r + f_lambda_m
    cos_t = math.cos(theta_rad)
    a = 4.0 * (ell * ell - (r * cos_t) ** 2)
    b = 4.0 * r * cos_t * (r_r_m**2 - r_s_m**2) + 4.0 * (r_s_m - r_r_m) * ell * ell * cos_t
    c = -(ell**4) + 2.0 * (r_s_m**2 + r_r_m**2) * ell * ell - (r_s_m**2 - r_r_m**2) ** 2
    return (-b + math.sqrt(b * b - 4.0 * a * c)) / (2.0 * a)


def fresnelzonsstorlek(
    avstand_m: float, kallhojd_m: float, mottagarhojd_m: float, f_lambda_m: float
) -> tuple[float, float, float]:
    """Funktionen FresnelZoneSize, AV 1106/07 avsnitt 5.23.5, ekv. (340)–(344).

    Returnerar (a1, a2, b): zonens utsträckning från reflexpunkten mot källan och mot
    mottagaren samt halva bredden tvärs utbredningsriktningen.
    """
    psi = math.atan((kallhojd_m + mottagarhojd_m) / avstand_m)
    r = math.sqrt((kallhojd_m + mottagarhojd_m) ** 2 + avstand_m**2)
    r_s = kallhojd_m / (kallhojd_m + mottagarhojd_m) * r
    r_r = mottagarhojd_m / (kallhojd_m + mottagarhojd_m) * r
    a1 = calc_fzd(r_s, r_r, math.pi - psi, f_lambda_m)
    a2 = calc_fzd(r_s, r_r, psi, f_lambda_m)
    tvars = calc_fzd(r_s, r_r, math.pi / 2.0, f_lambda_m)
    b = math.sqrt(tvars**2 / (1.0 - ((a2 - a1) / (a2 + a1)) ** 2))
    return a1, a2, b


@dataclass(frozen=True)
class Fresnelzon:
    """Fresnelzonens läge längs marken: gränserna d_1,Fz och d_2,Fz samt reflexpunkten d_refl."""

    d1_fz_m: float
    d2_fz_m: float
    d_refl_m: float


def fresnelzon_langs_mark(
    avstand_m: float, kallhojd_m: float, mottagarhojd_m: float, f_lambda_m: float
) -> Fresnelzon:
    """Zonens gränser enligt AV 1106/07 avsnitt 5.23.6, ekv. (349)–(350), för ξ = 0.

    Höjder under 0,01 m ersätts med 0,01 m (avsnitt 5.23.6). Reflexpunkten ligger i
    d_refl = d·h_S/(h_S + h_R) (rät stråle).
    """
    if avstand_m <= 0.0 or f_lambda_m <= 0.0:
        raise BullerFel("Fresnelzonen kräver avstånd och F·λ större än 0.")
    h_s = max(kallhojd_m, MINSTA_HOJD_M)
    h_r = max(mottagarhojd_m, MINSTA_HOJD_M)
    a1, a2, _ = fresnelzonsstorlek(avstand_m, h_s, h_r, f_lambda_m)
    d_refl = avstand_m * h_s / (h_s + h_r)
    return Fresnelzon(d1_fz_m=d_refl - a1, d2_fz_m=d_refl + a2, d_refl_m=d_refl)


def _andel(d1: float, d2: float, zon_start: float, zon_slut: float) -> float:
    """Andelen av zonen [zon_start, zon_slut] som täcks av segmentet [d1, d2].

    Detta är fallindelningen i AV 1106/07 ekv. (351) (och de två halvorna av ekv. 353) skriven
    med samma fem fall som i rapporten.
    """
    if d1 >= zon_slut or d2 <= zon_start:
        return 0.0
    if d1 <= zon_start and d2 >= zon_slut:
        return 1.0
    zonlangd = zon_slut - zon_start
    if d1 > zon_start and d2 < zon_slut:
        return (d2 - d1) / zonlangd
    if d1 <= zon_start:
        return (d2 - zon_start) / zonlangd
    return (zon_slut - d1) / zonlangd


def klippt_fresnelzon(zon: Fresnelzon, avstand_m: float) -> Fresnelzon:
    """Zonen begränsad till sträckan mellan källa och mottagare, AV 1106/07 avsnitt 5.8.

    Avsnitt 5.8 anger att bara den del av Fresnelzonen som ligger mellan källa och mottagare
    tas med; ekv. (349)–(351) har ingen sådan begränsning. De två läsningarna och deras utfall
    står i `docs/buller/logg/P-R2.md`. Förvalet i modulen är ekvationerna som de är tryckta
    (oklippt); den här funktionen ger den andra läsningen: d_1,Fz ≥ 0 och d_2,Fz ≤ d.
    """
    return Fresnelzon(
        d1_fz_m=max(zon.d1_fz_m, 0.0), d2_fz_m=min(zon.d2_fz_m, avstand_m), d_refl_m=zon.d_refl_m
    )


def fresnelzonsandel(d1_m: float, d2_m: float, zon_start_m: float, zon_slut_m: float) -> float:
    """Andelen av zonen [zon_start, zon_slut] som segmentet [d1, d2] täcker, ekv. (351).

    Samma fallindelning används för de två halvorna i ekv. (353).
    """
    return _andel(d1_m, d2_m, zon_start_m, zon_slut_m)


def fresnelzonsvikt(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    d1_m: float,
    d2_m: float,
    f_lambda_m: float,
) -> float:
    """Funktionen FresnelZoneW för ξ = 0, AV 1106/07 avsnitt 5.23.6, ekv. (345), (349)–(351).

    `d1_m` och `d2_m` är avstånden från källan till segmentets ändpunkter längs marken.
    """
    zon = fresnelzon_langs_mark(avstand_m, kallhojd_m, mottagarhojd_m, f_lambda_m)
    return _andel(d1_m, d2_m, zon.d1_fz_m, zon.d2_fz_m)


def fresnelzonsvikt_modifierad(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    d1_m: float,
    d2_m: float,
    f_lambda_m: float,
) -> float:
    """Funktionen FresnelZoneWm för ξ = 0, AV 1106/07 avsnitt 5.23.7, ekv. (352)–(353).

    Medelvärdet av segmentets andel av zonens källhalva [d_1,Fz, d_refl] och mottagarhalva
    [d_refl, d_2,Fz]. Sista fallet för w_S i ekv. (353) är tryckt med täljaren d_2,Fz − d_1;
    här används d_refl − d_1 (se `docs/buller/tolkningar.md`).
    """
    zon = fresnelzon_langs_mark(avstand_m, kallhojd_m, mottagarhojd_m, f_lambda_m)
    w_s = _andel(d1_m, d2_m, zon.d1_fz_m, zon.d_refl_m)
    w_r = _andel(d1_m, d2_m, zon.d_refl_m, zon.d2_fz_m)
    return 0.5 * (w_s + w_r)


@dataclass(frozen=True)
class Reflexgeometri:
    """Reflexpunkten och strålens två delar med refraktion, AV 1106/07 ekv. (346)–(348).

    R_S och R_R är väglängderna källa–reflexpunkt och reflexpunkt–mottagare, ψ_G strykvinkeln
    och d_refl reflexpunktens verkliga avstånd från källan längs marken. Egenskaperna ger de
    modifierade storheterna i ekv. (348) (källa S′ och mottagare R′ i figur 38).
    """

    vag_kalla_m: float  # R_S
    vag_mottagare_m: float  # R_R
    strykvinkel_rad: float  # ψ_G
    d_refl_m: float
    i_skuggzon: bool

    @property
    def avstand_modifierat_m(self) -> float:
        """d med båge = (R_S + R_R)·cos ψ_G, ekv. (348)."""
        return (self.vag_kalla_m + self.vag_mottagare_m) * math.cos(self.strykvinkel_rad)

    @property
    def kallhojd_modifierad_m(self) -> float:
        """h_S med båge = R_S·sin ψ_G, ekv. (348)."""
        return self.vag_kalla_m * math.sin(self.strykvinkel_rad)

    @property
    def mottagarhojd_modifierad_m(self) -> float:
        """h_R med båge = R_R·sin ψ_G, ekv. (348)."""
        return self.vag_mottagare_m * math.sin(self.strykvinkel_rad)


def reflexgeometri_skuggzon(
    avstand_m: float, kallhojd_m: float, mottagarhojd_m: float
) -> Reflexgeometri:
    """Reflexgeometrin när mottagaren ligger i skuggzonen, AV 1106/07 ekv. (347), TC 2.13.

    ψ_G = 0, d_refl = d·√h_S/(√h_S + √h_R), R_S = R₀·arccos(1 − h_S/R₀) och motsvarande för
    R_R: bågarna på en cirkel med radien R₀ som tangerar marken i reflexpunkten. Ett argument
    under −1 till arccos ersätts med −1 (TC-101327 avsnitt 2.13).

    R₀ är tryckt som d/(2·(√h_S + √h_R)) inom parentes utan exponent, vilket inte är en längd.
    Här används R₀ = d²/(2·(√h_S + √h_R)²), den radie som gör d_refl och bågarna i samma
    ekvation förenliga (en tangerande cirkel når höjden h på avståndet √(2·R₀·h)); se
    tolkningar i `docs/buller/logg/P-R2.md`.
    """
    rotsumma = math.sqrt(kallhojd_m) + math.sqrt(mottagarhojd_m)
    r0 = avstand_m**2 / (2.0 * rotsumma**2)
    return Reflexgeometri(
        vag_kalla_m=r0 * math.acos(max(1.0 - kallhojd_m / r0, -1.0)),
        vag_mottagare_m=r0 * math.acos(max(1.0 - mottagarhojd_m / r0, -1.0)),
        strykvinkel_rad=0.0,
        d_refl_m=avstand_m * math.sqrt(kallhojd_m) / rotsumma,
        i_skuggzon=True,
    )


def reflexgeometri(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    xi_per_m: float,
    c0_m_s: float,
    uppat: Uppatmetod = "bokstavlig",
) -> Reflexgeometri:
    """Första steget i FresnelZoneW, AV 1106/07 avsnitt 5.23.6, ekv. (346)–(347).

    Utanför skuggzonen ger ReflectedRay R_S, R_R, ψ_G och d_refl (ekv. 346); i skuggzonen
    (ξ < 0 och d > 0,95·d_SZ, samma villkor som i avsnitt 5.5.4) gäller ekv. (347). Höjder
    under 0,01 m ersätts med 0,01 m (avsnitt 5.23.6). ξ och c₀ är den frekvensoberoende
    profilen (avsnitt 5.5.3).
    """
    if avstand_m <= 0.0:
        raise BullerFel("Fresnelzonen kräver avstånd större än 0.")
    h_s = max(kallhojd_m, MINSTA_HOJD_M)
    h_r = max(mottagarhojd_m, MINSTA_HOJD_M)
    if xi_per_m < 0.0 and avstand_m > SKUGGZONSMARGINAL * skuggzonsavstand_m(
        h_s, h_r, xi_per_m, uppat
    ):
        return reflexgeometri_skuggzon(avstand_m, h_s, h_r)
    stralar = reflekterad_strale(avstand_m, h_s, h_r, xi_per_m, c0_m_s, uppat)
    return Reflexgeometri(
        vag_kalla_m=stralar.vagstracka_kalla_m,
        vag_mottagare_m=stralar.vagstracka_mottagare_m,
        strykvinkel_rad=stralar.strykvinkel_rad,
        d_refl_m=stralar.d_refl_m,
        i_skuggzon=False,
    )


def fresnelzon_langs_mark_refraktion(geometri: Reflexgeometri, f_lambda_m: float) -> Fresnelzon:
    """Zonens gränser med refraktion, AV 1106/07 avsnitt 5.23.6, ekv. (348)–(350).

    FresnelZoneSize (ekv. 340–344) tillämpad på de modifierade lägena S′ och R′: där är
    ψ = ψ_G, R = R_S + R_R, och sträckorna till reflexpunkten R_S och R_R, så a₁ och a₂ är
    CalcFZd(R_S, R_R, π − ψ_G) och CalcFZd(R_S, R_R, ψ_G). Gränserna läggs kring den verkliga
    reflexpunkten d_refl (ekv. 350). Formen gäller också ψ_G = 0 (skuggzon), där de modifierade
    höjderna är 0 och ekv. (341)–(342) inte kan räknas.
    """
    if f_lambda_m <= 0.0:
        raise BullerFel("Fresnelzonen kräver F·λ större än 0.")
    psi = geometri.strykvinkel_rad
    a1 = calc_fzd(geometri.vag_kalla_m, geometri.vag_mottagare_m, math.pi - psi, f_lambda_m)
    a2 = calc_fzd(geometri.vag_kalla_m, geometri.vag_mottagare_m, psi, f_lambda_m)
    return Fresnelzon(
        d1_fz_m=geometri.d_refl_m - a1, d2_fz_m=geometri.d_refl_m + a2, d_refl_m=geometri.d_refl_m
    )


def fresnelzonsvikt_refraktion(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    d1_m: float,
    d2_m: float,
    f_lambda_m: float,
    xi_per_m: float,
    c0_m_s: float,
    uppat: Uppatmetod = "bokstavlig",
) -> float:
    """Funktionen FresnelZoneW, AV 1106/07 avsnitt 5.23.6, ekv. (345)–(351), TC 2.13.

    För ξ = 0 anropas `fresnelzonsvikt` (räta strålar).
    """
    if xi_per_m == 0.0:
        return fresnelzonsvikt(avstand_m, kallhojd_m, mottagarhojd_m, d1_m, d2_m, f_lambda_m)
    geometri = reflexgeometri(avstand_m, kallhojd_m, mottagarhojd_m, xi_per_m, c0_m_s, uppat)
    zon = fresnelzon_langs_mark_refraktion(geometri, f_lambda_m)
    return _andel(d1_m, d2_m, zon.d1_fz_m, zon.d2_fz_m)


def fresnelzonsvikt_modifierad_refraktion(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    d1_m: float,
    d2_m: float,
    f_lambda_m: float,
    xi_per_m: float,
    c0_m_s: float,
    uppat: Uppatmetod = "bokstavlig",
) -> float:
    """Funktionen FresnelZoneWm, AV 1106/07 avsnitt 5.23.7, ekv. (352)–(353), med refraktion.

    Samma förfarande som `fresnelzonsvikt_refraktion` men med vikten i ekv. (353). För ξ = 0
    anropas `fresnelzonsvikt_modifierad`.
    """
    if xi_per_m == 0.0:
        return fresnelzonsvikt_modifierad(
            avstand_m, kallhojd_m, mottagarhojd_m, d1_m, d2_m, f_lambda_m
        )
    geometri = reflexgeometri(avstand_m, kallhojd_m, mottagarhojd_m, xi_per_m, c0_m_s, uppat)
    zon = fresnelzon_langs_mark_refraktion(geometri, f_lambda_m)
    w_s = _andel(d1_m, d2_m, zon.d1_fz_m, zon.d_refl_m)
    w_r = _andel(d1_m, d2_m, zon.d_refl_m, zon.d2_fz_m)
    return 0.5 * (w_s + w_r)
