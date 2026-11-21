"""Fresnelzoner och Fresnelzonsvikter i Nord2000 (homogen atmosfär).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.8 samt hjälpfunktionerna i avsnitt 5.23.4
(CalcFZd, ekv. 338–339), 5.23.5 (FresnelZoneSize, ekv. 340–344), 5.23.6 (FresnelZoneW,
ekv. 345–351) och 5.23.7 (FresnelZoneWm, ekv. 352–353).

Refraktionskorrektionen i avsnitt 5.23.6 (ekv. 346–348) är inte implementerad: funktionerna
här gäller ξ = 0, där de korrigerade storheterna (d, h_S, h_R, d_refl med båge över) är lika
med de okorrigerade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from buller.nord2000.geometri import MINSTA_HOJD_M
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
