"""Delmodell 11: reflektion mot vertikala hinder (fasader och bullerskärmar).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.3.4 (s. 17) och avsnitt 5.20 (s. 129–131),
ekv. (295)–(299).

Principer:
- Reflexpunkten O är skärningen mellan räta linjen S'–R (där S' är källans spegelbild i
  reflektorplanet) och planet som innehåller den vertikala reflektorn.
- Reflektioner där O ligger mer än 2 m utanför någon av reflektorns sidokanter förkastas
  (AV 1106/07 s. 130).
- Effektiv reflektorhöjd över mark inkluderar reflektorns spegelbild i markplanet:
  [2·z_mark − z_topp, z_topp] (AV 1106/07 s. 130).
- Fresnelzonen vid O omskrivs av en rektangel med halvdimensioner från `CalcFZd` med
  F_λ = 1/8 (ekv. 299).
- Korrektionen är ΔL_r(f) = 10·lg(ρ_E) + 20·lg(S_refl(f) / S_Fz(f)) (ekv. 296).
- Reflekterat ljud adderas inkoherent (energisumma) till direktljudet (s. 131).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.nord2000.fresnel import calc_fzd
from buller.typer import BullerFel, Flyttal

F_LAMBDA_REFLEKTION = 0.125  # F_λ = 1/8, AV 1106/07 ekv. (299)
MAX_UTANFOR_KANT_M = 2.0  # AV 1106/07 s. 130


@dataclass(frozen=True)
class VertikalReflektor:
    """En plan, vertikal reflekterande yta mellan (x1, y1) och (x2, y2) i plan.

    Om `ensidig = True` reflekterar ytan bara från den sida som ligger i höger normalriktning
    `(dy, -dx)` (dvs. utsidan av en medurs eller korrekt orienterad byggnadsvägg).
    """

    x1_m: float
    y1_m: float
    x2_m: float
    y2_m: float
    z_botten_m: float
    z_topp_m: float
    reflektionsfaktor_e: float = 1.0
    ensidig: bool = False

    def __post_init__(self) -> None:
        if math.hypot(self.x2_m - self.x1_m, self.y2_m - self.y1_m) <= 0.0:
            raise BullerFel("Reflektorn måste ha längd större än 0.")
        if self.z_topp_m <= self.z_botten_m:
            raise BullerFel("Reflektorns topphöjd måste vara större än bottenhöjden.")
        if not (0.0 < self.reflektionsfaktor_e <= 1.0):
            raise BullerFel("Reflektionsfaktorn ρ_E måste ligga i (0, 1].")

    @property
    def langd_m(self) -> float:
        return math.hypot(self.x2_m - self.x1_m, self.y2_m - self.y1_m)


@dataclass(frozen=True)
class Reflektionsgeometri:
    """Geometri i plan för en 1:a ordningens reflektion S → O → R."""

    o_x_m: float
    o_y_m: float
    d1_m: float  # |S − O| i plan
    d2_m: float  # |O − R| i plan
    s_langs_m: float  # läge längs reflektorn från (x1, y1) mot (x2, y2)
    reflektor_langd_m: float
    vinkel_mot_plan_rad: float  # vinkel θ_r ∈ (0, π/2] mellan strålen och reflektorplanet
    spegel_x_m: float
    spegel_y_m: float


def spegelpunkt_plan(
    x_m: float, y_m: float, x1_m: float, y1_m: float, x2_m: float, y2_m: float
) -> tuple[float, float]:
    """Speglar punkten (x, y) i den obegränsade linjen genom (x1, y1)–(x2, y2)."""
    dx = x2_m - x1_m
    dy = y2_m - y1_m
    l2 = dx * dx + dy * dy
    if l2 <= 0.0:
        raise BullerFel("Spegellinjen har nollängd.")
    t = ((x_m - x1_m) * dx + (y_m - y1_m) * dy) / l2
    px = x1_m + t * dx
    py = y1_m + t * dy
    return 2.0 * px - x_m, 2.0 * py - y_m


def reflektionspunkt_plan(
    s_x_m: float,
    s_y_m: float,
    r_x_m: float,
    r_y_m: float,
    reflektor: VertikalReflektor,
    *,
    max_utanfor_m: float = MAX_UTANFOR_KANT_M,
) -> Reflektionsgeometri | None:
    """Beräknar reflexpunkten O i plan för S → reflektor → R (AV 1106/07 avsnitt 5.20).

    Returnerar `None` om källa och mottagare ligger på olika sidor om reflektorplanet,
    om reflektion sker från fel sida av en ensidig fasad, eller om O ligger mer än
    `max_utanfor_m` utanför reflektorns sidokanter.
    """
    dx = reflektor.x2_m - reflektor.x1_m
    dy = reflektor.y2_m - reflektor.y1_m
    langd = math.hypot(dx, dy)
    tx, ty = dx / langd, dy / langd
    nx, ny = dy / langd, -dx / langd  # höger normal

    # Tecknade avstånd till reflektorplanet.
    ds = (s_x_m - reflektor.x1_m) * nx + (s_y_m - reflektor.y1_m) * ny
    dr = (r_x_m - reflektor.x1_m) * nx + (r_y_m - reflektor.y1_m) * ny
    if ds * dr <= 0.0:
        return None
    if reflektor.ensidig and ds <= 0.0:
        return None

    # Spegelkälla S' och skärning S'–R med reflektorplanet.
    sx_sp = s_x_m - 2.0 * ds * nx
    sy_sp = s_y_m - 2.0 * ds * ny
    # Parameter u ∈ (0, 1) längs S' → R där linjen korsar planet (avstånd 0).
    denom = abs(ds) + abs(dr)
    if denom <= 1.0e-12:
        return None
    u = abs(ds) / denom
    ox = sx_sp + u * (r_x_m - sx_sp)
    oy = sy_sp + u * (r_y_m - sy_sp)

    s_langs = (ox - reflektor.x1_m) * tx + (oy - reflektor.y1_m) * ty
    if s_langs < -max_utanfor_m or s_langs > langd + max_utanfor_m:
        return None

    d1 = math.hypot(ox - s_x_m, oy - s_y_m)
    d2 = math.hypot(r_x_m - ox, r_y_m - oy)
    if d1 <= 1.0e-9 or d2 <= 1.0e-9:
        return None

    # Vinkel mellan infallande/utgående stråle och reflektorplanet ∈ (0, π/2].
    sin_vinkel = min(1.0, abs(ds) / d1)
    vinkel = math.asin(sin_vinkel)
    if vinkel <= 1.0e-9:
        return None

    return Reflektionsgeometri(
        o_x_m=ox,
        o_y_m=oy,
        d1_m=d1,
        d2_m=d2,
        s_langs_m=s_langs,
        reflektor_langd_m=langd,
        vinkel_mot_plan_rad=vinkel,
        spegel_x_m=sx_sp,
        spegel_y_m=sy_sp,
    )


def _overlapp(a1: float, a2: float, b1: float, b2: float) -> float:
    return max(0.0, min(a2, b2) - max(a1, b1))


def reflektionskorrektion_db(
    frekvenser_hz: Flyttal,
    c0_m_s: float,
    geom: Reflektionsgeometri,
    reflektor: VertikalReflektor,
    z_kalla_m: float,
    z_mottagare_m: float,
    *,
    z_mark_vid_o_m: float | None = None,
) -> Flyttal:
    """Beräknar ΔL_r(f) enligt AV 1106/07 avsnitt 5.20, ekv. (296)–(299).

    Returnerar vektor i dB (en per tersband); `-inf` där Fresnelzonen inte överlappar
    den effektiva reflektorn.
    """
    d_tot = geom.d1_m + geom.d2_m
    z_o = z_kalla_m + (z_mottagare_m - z_kalla_m) * (geom.d1_m / d_tot)
    r1 = math.hypot(geom.d1_m, z_o - z_kalla_m)
    r2 = math.hypot(geom.d2_m, z_mottagare_m - z_o)
    psi = math.atan2(z_mottagare_m - z_kalla_m, d_tot)

    # Effektiv reflektorhöjd inklusive spegelbild i markplanet (AV 1106/07 s. 130).
    z_mark = reflektor.z_botten_m if z_mark_vid_o_m is None else z_mark_vid_o_m
    z_eff_low = 2.0 * z_mark - reflektor.z_topp_m
    z_eff_upp = reflektor.z_topp_m

    theta_h = geom.vinkel_mot_plan_rad
    theta_v_upp = math.pi * 0.5 - psi
    theta_v_ned = math.pi * 0.5 + psi
    rho_term = 10.0 * math.log10(reflektor.reflektionsfaktor_e)

    ut = np.full(frekvenser_hz.shape[0], -np.inf, dtype=np.float64)
    for i, f_hz in enumerate(frekvenser_hz):
        f_lam = F_LAMBDA_REFLEKTION * (c0_m_s / float(f_hz))
        # Horisontella halvdimensioner längs reflektorn.
        d_fz_1 = calc_fzd(r1, r2, theta_h, f_lam)
        d_fz_2 = calc_fzd(r1, r2, math.pi - theta_h, f_lam)
        # Vertikala halvdimensioner (uppåt / nedåt längs vertikal reflektor).
        d_fz_upp = calc_fzd(r1, r2, theta_v_upp, f_lam)
        d_fz_ned = calc_fzd(r1, r2, theta_v_ned, f_lam)

        s_fz = (d_fz_1 + d_fz_2) * (d_fz_upp + d_fz_ned)
        if s_fz <= 0.0:
            continue
        w_over = _overlapp(
            0.0,
            geom.reflektor_langd_m,
            geom.s_langs_m - d_fz_1,
            geom.s_langs_m + d_fz_2,
        )
        h_over = _overlapp(
            z_eff_low,
            z_eff_upp,
            z_o - d_fz_ned,
            z_o + d_fz_upp,
        )
        s_refl = w_over * h_over
        if s_refl > 0.0:
            ut[i] = rho_term + 20.0 * math.log10(s_refl / s_fz)
    return ut


def linje_korsar_segment(
    ax: float, ay: float, bx: float, by: float, cx: float, cy: float, dx: float, dy: float
) -> bool:
    """Sant om sträckan AB skär sträckan CD strikt i det inre (blockeringskontroll i plan)."""
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    denom = rx * sy - ry * sx
    if abs(denom) <= 1.0e-12:
        return False
    qpx, qpy = cx - ax, cy - ay
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    return 1.0e-6 < t < 1.0 - 1.0e-6 and 1.0e-6 < u < 1.0 - 1.0e-6
