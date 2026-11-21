"""Tolkning av terrängprofilen i Nord2000: källa, mottagare, ekvivalent plan mark, skärmkant.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.4.1 (ekv. 4–9), 5.21.1 (ekv. 300–305),
5.21.4 (ekv. 316–317) och 5.21.5 (ekv. 320–327).

Refraktionens inverkan på gångvägsskillnaden (ekv. 301) ingår inte; i homogen atmosfär är
Δℓ = Δℓ₀.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.nord2000.fresnel import calc_fzd
from buller.nord2000.geometri import MINSTA_HOJD_M, langd, normlinje
from buller.typer import BullerFel, Flyttal, Terrangprofil


@dataclass(frozen=True)
class Terranggeometri:
    """Profilen med källa och mottagare utplacerade, AV 1106/07 avsnitt 5.4.1.

    `x_m`, `z_m` är terrängpunkterna efter att första och sista punkten vid behov ersatts med
    markpunkterna S_G och R_G. Terrängens baslinje går mellan indataprofilens första och sista
    punkt (P_S, P_R). `h_bas_m[i]` är punktens teckensatta avstånd till baslinjen och
    `d_bas_m[i]` avståndet från P_S till punktens projektion på baslinjen.
    """

    x_m: Flyttal
    z_m: Flyttal
    flodesresistivitet_kNsm4: Flyttal
    rahet_m: Flyttal
    x_s_m: float
    z_s_m: float
    x_r_m: float
    z_r_m: float
    h_s_m: float
    h_r_m: float
    r_sr_m: float
    d_bas_tot_m: float
    d_bas_m: Flyttal
    h_bas_m: Flyttal


def terranggeometri(profil: Terrangprofil) -> Terranggeometri:
    """Placerar källa och mottagare och räknar baslinjestorheter, AV 1106/07 ekv. (4)–(9).

    Vertikala höjder under 0,01 m ersätts med 0,01 m. h_S och h_R är avstånden till första
    respektive sista segmentets förlängning (ekv. 6–7).
    """
    x = np.array(profil.x_m, dtype=np.float64)
    z = np.array(profil.z_m, dtype=np.float64)
    n = x.shape[0]
    x_ps, z_ps, x_pr, z_pr = float(x[0]), float(z[0]), float(x[-1]), float(z[-1])
    x_s, z_s = x_ps, z_ps + max(profil.kalla_hojd_m, MINSTA_HOJD_M)
    x_r, z_r = x_pr, z_pr + max(profil.mottagare_hojd_m, MINSTA_HOJD_M)
    x_sg, z_sg, h_s = normlinje(float(x[0]), float(z[0]), float(x[1]), float(z[1]), x_s, z_s)
    x_rg, z_rg, h_r = normlinje(
        float(x[n - 2]), float(z[n - 2]), float(x[n - 1]), float(z[n - 1]), x_r, z_r
    )
    if h_s <= 0.0 or h_r <= 0.0:
        raise BullerFel("Källa eller mottagare ligger inte ovanför sitt närmaste terrängsegment.")
    if x_sg < x[0]:
        x[0], z[0] = x_sg, z_sg
    if x_rg > x[n - 1]:
        x[n - 1], z[n - 1] = x_rg, z_rg
    d_bas = np.empty(n, dtype=np.float64)
    h_bas = np.empty(n, dtype=np.float64)
    for i in range(n):
        x0, z0, h = normlinje(x_ps, z_ps, x_pr, z_pr, float(x[i]), float(z[i]))
        d_bas[i] = langd(x_ps, z_ps, x0, z0)
        h_bas[i] = h
    return Terranggeometri(
        x_m=x,
        z_m=z,
        flodesresistivitet_kNsm4=np.array(profil.flodesresistivitet_kNsm4, dtype=np.float64),
        rahet_m=np.array(profil.rahet_m, dtype=np.float64),
        x_s_m=x_s,
        z_s_m=z_s,
        x_r_m=x_r,
        z_r_m=z_r,
        h_s_m=h_s,
        h_r_m=h_r,
        r_sr_m=langd(x_s, z_s, x_r, z_r),
        d_bas_tot_m=langd(x_ps, z_ps, x_pr, z_pr),
        d_bas_m=d_bas,
        h_bas_m=h_bas,
    )


@dataclass(frozen=True)
class EkvivalentPlanMark:
    """Ekvivalent plan mark z = α + β·x och terrängens medelavvikelse Δh, avsnitt 5.21.5."""

    alfa_m: float
    beta: float
    delta_h_m: float


def ekvivalent_plan_mark(x_m: Flyttal, z_m: Flyttal) -> EkvivalentPlanMark:
    """Minsta kvadrat-anpassad plan mark, AV 1106/07 avsnitt 5.21.5, ekv. (320)–(326).

    Första termen i t₂ och i t₁ (ekv. 322) är tryckta med kvadrater men ska vara kuber för att
    uttrycken ska vara den minsta kvadrat-lösning som avsnittet anger; kuber används här
    (se `docs/buller/tolkningar.md`).
    """
    n = x_m.shape[0]
    x1 = float(x_m[0])
    xn = float(x_m[-1])
    x_medel = (xn + x1) / 2.0
    lutningar = [
        (float(z_m[i + 1]) - float(z_m[i])) / (float(x_m[i + 1]) - float(x_m[i]))
        for i in range(n - 1)
    ]
    z_medel = 0.0
    for i, gamma in enumerate(lutningar):
        xi, xi1, zi = float(x_m[i]), float(x_m[i + 1]), float(z_m[i])
        z_medel += gamma / 2.0 * (xi1**2 - xi**2) + (zi - gamma * xi) * (xi1 - xi)
    z_medel /= xn - x1
    t2 = (xn**3 - x1**3) / 3.0 - x_medel * (xn**2 - x1**2) + x_medel**2 * (xn - x1)
    t1 = 0.0
    for i, gamma in enumerate(lutningar):
        xi, xi1, zi = float(x_m[i]), float(x_m[i + 1]), float(z_m[i])
        t1 += (
            gamma / 3.0 * (xi1**3 - xi**3)
            + (zi - z_medel - gamma * (xi + x_medel)) / 2.0 * (xi1**2 - xi**2)
            + x_medel * (gamma * xi - zi + z_medel) * (xi1 - xi)
        )
    beta = t1 / t2
    alfa = z_medel - x_medel * beta

    yta = 0.0
    for i in range(n - 1):
        xi, xi1 = float(x_m[i]), float(x_m[i + 1])
        hi = float(z_m[i]) - alfa - beta * xi  # ekv. (324)
        hi1 = float(z_m[i + 1]) - alfa - beta * xi1
        if hi * hi1 >= 0.0:
            a_i = (hi1 + hi) / 2.0 * (xi1 - xi)  # ekv. (325)
        else:
            x_x = abs(hi) / (abs(hi1) + abs(hi)) * (xi1 - xi) + xi  # ekv. (326)
            a_i = hi / 2.0 * (x_x - xi) if hi > 0.0 else hi1 / 2.0 * (xi1 - x_x)
        yta += max(a_i, 0.0)
    return EkvivalentPlanMark(alfa_m=alfa, beta=beta, delta_h_m=yta / (xn - x1))  # ekv. (323)


@dataclass(frozen=True)
class GeometriOverPlanMark:
    """Källa och mottagare relativt den ekvivalenta plana marken, AV 1106/07 ekv. (316), (327)."""

    x_sge_m: float
    z_sge_m: float
    x_rge_m: float
    z_rge_m: float
    x_s_fot_m: float
    z_s_fot_m: float
    h_se_m: float
    h_re_m: float
    d_e_m: float

    def avstand_langs_mark_m(self, x_m: float, z_m: float) -> float:
        """d_e,i: avståndet från källans fotpunkt till punktens projektion, ekv. (133)."""
        x0, z0, _ = normlinje(self.x_sge_m, self.z_sge_m, self.x_rge_m, self.z_rge_m, x_m, z_m)
        return langd(self.x_s_fot_m, self.z_s_fot_m, x0, z0)


def geometri_over_plan_mark(geo: Terranggeometri, plan: EkvivalentPlanMark) -> GeometriOverPlanMark:
    """h_Se, h_Re och d_e relativt den ekvivalenta plana marken, AV 1106/07 ekv. (316), (327)."""
    x_sge, z_sge = geo.x_s_m, plan.alfa_m + plan.beta * geo.x_s_m
    x_rge, z_rge = geo.x_r_m, plan.alfa_m + plan.beta * geo.x_r_m
    xs0, zs0, h_se = normlinje(x_sge, z_sge, x_rge, z_rge, geo.x_s_m, geo.z_s_m)
    xr0, zr0, h_re = normlinje(x_sge, z_sge, x_rge, z_rge, geo.x_r_m, geo.z_r_m)
    return GeometriOverPlanMark(
        x_sge_m=x_sge,
        z_sge_m=z_sge,
        x_rge_m=x_rge,
        z_rge_m=z_rge,
        x_s_fot_m=xs0,
        z_s_fot_m=zs0,
        h_se_m=max(h_se, MINSTA_HOJD_M),
        h_re_m=max(h_re, MINSTA_HOJD_M),
        d_e_m=langd(xs0, zs0, xr0, zr0),
    )


def planhetsparameter_rf(
    frekvens_hz: float, over_plan: GeometriOverPlanMark, delta_h_m: float, ljudhastighet_m_s: float
) -> float:
    """Planhetsparametern r_f(f), AV 1106/07 avsnitt 5.21.4, ekv. (317).

    Över 2000 Hz används värdet vid 2000 Hz. r_f = 1 betyder plan terräng.
    """
    f = min(frekvens_hz, 2000.0)
    vaglangd = ljudhastighet_m_s / f
    summa = over_plan.h_se_m + over_plan.h_re_m
    delta_r2 = math.sqrt((summa + 2.0 * delta_h_m) ** 2 + over_plan.d_e_m**2) - math.sqrt(
        summa**2 + over_plan.d_e_m**2
    )
    kvot = delta_r2 / vaglangd
    if kvot <= 0.01:
        return 1.0
    if kvot < 0.03:
        return (0.03 - kvot) / 0.02
    return 0.0


@dataclass(frozen=True)
class Skarmkant:
    """Primärskärmens mest verksamma kant T₁, AV 1106/07 avsnitt 5.21.1."""

    index: int
    gangvagsskillnad_m: float  # Δℓ₀, negativ när kanten ligger under siktlinjen
    hojd_over_baslinje_m: float  # h_SCR = h_bas(i_T1)
    d_bas_m: float  # d_bas(i_T1)


def gangvagsskillnad_m(geo: Terranggeometri, index: int) -> float:
    """Δℓ₀ för terrängpunkt nr `index`, AV 1106/07 avsnitt 5.21.1, ekv. (300).

    Positiv när punkten ligger på eller över siktlinjen SR, negativ under. (Ekvationen är
    tryckt med stegfunktionen H, men texten och AV 1849/00 ekv. (114) anger teckenbyte under
    siktlinjen; se `docs/buller/tolkningar.md`.)
    """
    xi, zi = float(geo.x_m[index]), float(geo.z_m[index])
    z_sikt = (geo.z_r_m - geo.z_s_m) * (xi - geo.x_s_m) / (geo.x_r_m - geo.x_s_m) + geo.z_s_m
    omvag = langd(geo.x_s_m, geo.z_s_m, xi, zi) + langd(geo.x_r_m, geo.z_r_m, xi, zi) - geo.r_sr_m
    return omvag if zi - z_sikt >= 0.0 else -omvag


def primar_skarmkant(geo: Terranggeometri) -> Skarmkant | None:
    """Väljer T₁: den inre terrängpunkt över baslinjen som har störst Δℓ₀ (avsnitt 5.21.1).

    Returnerar `None` när ingen punkt kommer i fråga (ett enda segment, eller inga inre
    punkter med h_bas > 0).
    """
    basta: Skarmkant | None = None
    for i in range(1, geo.x_m.shape[0] - 1):
        if float(geo.h_bas_m[i]) <= 0.0:
            continue
        dl = gangvagsskillnad_m(geo, i)
        if basta is None or dl > basta.gangvagsskillnad_m:
            basta = Skarmkant(
                index=i,
                gangvagsskillnad_m=dl,
                hojd_over_baslinje_m=float(geo.h_bas_m[i]),
                d_bas_m=float(geo.d_bas_m[i]),
            )
    return basta


def skarmparameter_rscr1(
    frekvens_hz: float, kant: Skarmkant, d_bas_tot_m: float, ljudhastighet_m_s: float
) -> float:
    """Övergångsparametern r_scr1(f) för primärskärmen, AV 1106/07 ekv. (302)–(305).

    Gäller homogen atmosfär, där Δℓ' = max(Δℓ, Δℓ₀) = Δℓ₀. 0 betyder ingen skärmverkan, 1 full.
    """
    vaglangd = ljudhastighet_m_s / frekvens_hz
    kvot_dl = kant.gangvagsskillnad_m / vaglangd
    if kvot_dl >= 0.0:
        r_dl = 1.0
    elif kvot_dl > -0.133:
        r_dl = 1.0 - math.sqrt(7.5 * abs(kvot_dl))
    else:
        r_dl = 0.0
    kvot_h = kant.hojd_over_baslinje_m / vaglangd
    if kvot_h >= 0.3:
        r_lambda = 1.0
    elif kvot_h > 0.1:
        r_lambda = (kvot_h - 0.1) / 0.2
    else:
        r_lambda = 0.0
    h_fz = calc_fzd(kant.d_bas_m, d_bas_tot_m - kant.d_bas_m, math.pi / 2.0, 0.5 * vaglangd)
    kvot_fz = kant.hojd_over_baslinje_m / h_fz
    if kvot_fz >= 0.082:
        r_fz = 1.0
    elif kvot_fz > 0.026:
        r_fz = (kvot_fz - 0.026) / 0.056
    else:
        r_fz = 0.0
    return r_dl * r_lambda * r_fz
