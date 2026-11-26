"""Medelstrålningstemperatur (T_mrt) med 6-riktningsmetodik (ISO 7726 / SOLWEIG).

Implementerar deterministisk beräkning av medelstrålningstemperatur T_mrt [°C] på
2D-raster (och skalärer) från Flodens geometrifält (`svf`, `skuggmask`, valfri
`trad_mask`) samt meteorologi (`solhojd_rad`, `sol_direkt_w_m2`, `sol_diffus_w_m2`,
`t_luft_c`, `relativ_fuktighet_pct`):

  - Klar himmels långvågsemissivitet enligt Prata (1996), Q. J. R. Meteorol. Soc. 122.
  - 6-riktnings kortvågsflöden (K_ned, K_upp, 4 x K_sida) med cylindrisk direktprojektion
    för stående människa (Holmer m.fl. 2015 / Lindberg m.fl. 2008 SOLWEIG).
  - 6-riktnings Stefan–Boltzmann långvågsflöden (L_ned, L_upp, 4 x L_sida) med
    Kirchhoffs reflektionsvillkor, som uppfyller isotermiskt svartkroppsfacit exakt.
  - Standardiserade vinkelfaktorer för stående människa (ISO 7726 / Höppe 1992 / SOLWEIG):
    F_ned = 0.06, F_upp = 0.06, F_sida = 0.22 x 4 (summa = 1.00).

Licens: GPL v3. Metodstatus: `skiss` (se `docs/MIKROKLIMAT.md`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from mikroklimat.utci import SkalarEllerArray, mattat_angtryck_hpa

FloatRaster = NDArray[np.float64]

# Fysikaliska konstanter (CODATA 2018 / SI 2019 & ISO 7726 / Lindberg m.fl. 2008).
STEFAN_BOLTZMANN_W_M2_K4: float = 5.670374419e-8
KELVIN_OFFSET_C: float = 273.15

# Standardiserade vinkelfaktorer för stående människa (summa = 1.00).
VINKELFAKTOR_NED: float = 0.06
VINKELFAKTOR_UPP: float = 0.06
VINKELFAKTOR_SIDA: float = 0.22
VINKELFAKTORER_STAENDE: tuple[float, float, float, float, float, float] = (
    VINKELFAKTOR_NED,
    VINKELFAKTOR_UPP,
    VINKELFAKTOR_SIDA,
    VINKELFAKTOR_SIDA,
    VINKELFAKTOR_SIDA,
    VINKELFAKTOR_SIDA,
)

# Standardparametrar för stående människa (ISO 7726 / Höppe 1992 / Lindberg m.fl. 2008).
ALPHA_K_STANDARD: float = 0.70  # kortvågsabsorptans
EPS_P_STANDARD: float = 0.97  # långvågsemissivitet

# Dokumenterade standardvärden för stadsmiljöytor (SOLWEIG / litteratur, metodstatus `skiss`).
ALBEDO_MARK_STANDARD: float = 0.15
ALBEDO_VAGG_STANDARD: float = 0.20
EMISSIVITET_MARK_STANDARD: float = 0.95
EMISSIVITET_VAGG_STANDARD: float = 0.90
EMISSIVITET_TRAD_STANDARD: float = 0.98
T_MARK_SOLTILLAGG_STANDARD_C: float = 15.0
T_VAGG_SOLTILLAGG_STANDARD_C: float = 10.0
TRAD_TRANSMISSIVITET_STANDARD: float = 0.03
VAGG_SOLANDEL_STANDARD: float = 0.50


@dataclass(frozen=True)
class TmrtResultat:
    """Resultat från 6-riktnings beräkning av medelstrålningstemperatur T_mrt [°C]."""

    t_mrt_c: FloatRaster
    s_str_w_m2: FloatRaster
    k_ned_w_m2: FloatRaster
    k_upp_w_m2: FloatRaster
    k_sida_w_m2: FloatRaster
    l_ned_w_m2: FloatRaster
    l_upp_w_m2: FloatRaster
    l_sida_w_m2: FloatRaster
    emissivitet_himmel: FloatRaster
    vinkelfaktorer: tuple[float, float, float, float, float, float] = VINKELFAKTORER_STAENDE
    metodstatus: str = "skiss"


def _validera_intervall(
    varde: FloatRaster,
    namn: str,
    *,
    min_v: float | None = None,
    max_v: float | None = None,
    strikt_min: bool = False,
) -> None:
    """Validerar ändliga värden i ett fält mot fysiska gränser; `NaN` tillåts, `+/-inf` avvisas."""
    if np.any(np.isinf(varde)):
        raise ValueError(f"{namn} får inte innehålla oändliga värden (+/-inf).")
    andliga = varde[np.isfinite(varde)]
    if andliga.size == 0:
        return
    if min_v is not None:
        minsta = float(np.min(andliga))
        if (strikt_min and minsta <= min_v) or (not strikt_min and minsta < min_v):
            op = ">" if strikt_min else ">="
            raise ValueError(f"{namn} måste uppfylla {op} {min_v}, fick min={minsta:.6g}.")
    if max_v is not None:
        storsta = float(np.max(andliga))
        if storsta > max_v:
            raise ValueError(f"{namn} måste uppfylla <= {max_v}, fick max={storsta:.6g}.")


def klar_himmel_emissivitet_prata(
    t_luft_c: SkalarEllerArray,
    relativ_fuktighet_pct: SkalarEllerArray,
) -> FloatRaster:
    """Effektiv långvågsemissivitet för klar himmel enligt Prata (1996).

    Formel (Prata 1996, *Q. J. R. Meteorol. Soc.* 122:1127–1151):
        xi = 46.5 * (e_a / T_K)
        eps_himmel = 1 - (1 + xi) * exp(-sqrt(1.2 + 3 * xi))

    där `e_a` är vattenångans partialtryck i **hPa** (via Hardy 1998 ITS-90) och
    `T_K = t_luft_c + 273.15` [K].

    Bevarar `NaN`; avvisar `+/-inf`, `t_luft_c <= -273.15 °C` eller `RH` utanför `[0, 100]` %.
    """
    ta, rh = np.broadcast_arrays(
        np.asarray(t_luft_c, dtype=np.float64),
        np.asarray(relativ_fuktighet_pct, dtype=np.float64),
    )
    _validera_intervall(ta, "t_luft_c", min_v=-KELVIN_OFFSET_C, strikt_min=True)
    _validera_intervall(rh, "relativ_fuktighet_pct", min_v=0.0, max_v=100.0)

    giltig = np.isfinite(ta) & np.isfinite(rh)
    tk = np.where(giltig, ta + KELVIN_OFFSET_C, 293.15)
    ea_hpa = np.where(giltig, mattat_angtryck_hpa(ta) * (rh / 100.0), 0.0)
    xi = 46.5 * (ea_hpa / tk)
    eps = 1.0 - (1.0 + xi) * np.exp(-np.sqrt(1.2 + 3.0 * xi))
    resultat = np.where(giltig, eps, np.nan)
    return np.asarray(resultat, dtype=np.float64)


def berakna_tmrt(
    svf: SkalarEllerArray,
    skuggmask: SkalarEllerArray,
    solhojd_rad: SkalarEllerArray,
    sol_direkt_w_m2: SkalarEllerArray,
    sol_diffus_w_m2: SkalarEllerArray,
    t_luft_c: SkalarEllerArray,
    relativ_fuktighet_pct: SkalarEllerArray,
    *,
    albedo_mark: SkalarEllerArray = ALBEDO_MARK_STANDARD,
    albedo_vagg: SkalarEllerArray = ALBEDO_VAGG_STANDARD,
    emissivitet_mark: SkalarEllerArray = EMISSIVITET_MARK_STANDARD,
    emissivitet_vagg: SkalarEllerArray = EMISSIVITET_VAGG_STANDARD,
    t_mark_soltillagg_c: SkalarEllerArray = T_MARK_SOLTILLAGG_STANDARD_C,
    t_vagg_soltillagg_c: SkalarEllerArray = T_VAGG_SOLTILLAGG_STANDARD_C,
    trad_mask: NDArray[np.bool_] | SkalarEllerArray | None = None,
    trad_transmissivitet: float = TRAD_TRANSMISSIVITET_STANDARD,
    emissivitet_himmel: SkalarEllerArray | None = None,
    emissivitet_trad: float = EMISSIVITET_TRAD_STANDARD,
    vagg_solandel: float = VAGG_SOLANDEL_STANDARD,
    alpha_k: float = ALPHA_K_STANDARD,
    eps_p: float = EPS_P_STANDARD,
) -> TmrtResultat:
    """Beräknar 6-riktnings medelstrålningstemperatur T_mrt [°C] (ISO 7726 / SOLWEIG).

    Args:
        svf: Sky View Factor i `[0, 1]` (byggnadshorisont; `1` = fri himmel).
        skuggmask: Solbelysningsfaktor i `[0, 1]` (`1` = solbelyst, `0` = helt skuggad,
            eller delvis genomsläpplig trädkrona).
        solhojd_rad: Solens höjdvinkel [rad] i `[-pi/2, pi/2]` (`<= 0` ger noll direkt sol).
        sol_direkt_w_m2: Direkt normal solstrålning (DNI, vinkelrätt mot solstrålen) [W/m²].
        sol_diffus_w_m2: Diffus himmelsstrålning på horisontell yta [W/m²].
        t_luft_c: Lufttemperatur [°C] (`> -273.15`).
        relativ_fuktighet_pct: Relativ luftfuktighet [%] i `[0, 100]`.
        albedo_mark: Markalbedo i `[0, 1]` (skalär eller 2D-raster), standard `0.15`.
        albedo_vagg: Fasadalbedo i `[0, 1]`, standard `0.20`.
        emissivitet_mark: Markens långvågsemissivitet i `(0, 1]`, standard `0.95`.
        emissivitet_vagg: Fasadernas långvågsemissivitet i `(0, 1]`, standard `0.90`.
        t_mark_soltillagg_c: Yttemperaturtillägg över `t_luft_c` för fullt solbelyst mark [°C].
            Skalas med effektiv solbelysning och sätts till `0` när direkt sol saknas.
        t_vagg_soltillagg_c: Yttemperaturtillägg över `t_luft_c` för solbelyst fasadandel [°C].
            Sätts till `0` när direkt sol saknas.
        trad_mask: Valfri trädkronemask (bool eller täckningsgrad i `[0, 1]`).
        trad_transmissivitet: Kortvågstransmissivitet genom trädkrona i `[0, 1]`, standard `0.03`.
        emissivitet_himmel: Valfri explicit himmelsemissivitet i `(0, 1]`. Om `None` beräknas
            klar himmel enligt Prata (1996).
        emissivitet_trad: Trädkronors långvågsemissivitet i `(0, 1]`, standard `0.98`.
        vagg_solandel: Andel solbelyst omgivande fasadyta i `[0, 1]`, standard `0.50`.
        alpha_k: Människokroppens kortvågsabsorptans i `[0, 1]`, standard `0.70`.
        eps_p: Människokroppens långvågsemissivitet i `(0, 1]`, standard `0.97`.

    Returns:
        `TmrtResultat` med `t_mrt_c`, `s_str_w_m2` och alla riktade strålningsflöden.

    Raises:
        ValueError: Om något ändligt värde bryter fysiska gränser eller innehåller `+/-inf`.
    """
    # Validera skalära konstanter.
    for namn, v, mn, mx, sm in (
        ("trad_transmissivitet", trad_transmissivitet, 0.0, 1.0, False),
        ("emissivitet_trad", emissivitet_trad, 0.0, 1.0, True),
        ("vagg_solandel", vagg_solandel, 0.0, 1.0, False),
        ("alpha_k", alpha_k, 0.0, 1.0, False),
        ("eps_p", eps_p, 0.0, 1.0, True),
    ):
        if not math.isfinite(v):
            raise ValueError(f"{namn} måste vara ändlig, fick {v}.")
        if (sm and v <= mn) or (not sm and v < mn) or v > mx:
            op = ">" if sm else ">="
            raise ValueError(f"{namn} måste uppfylla {op} {mn} och <= {mx}, fick {v}.")

    trad_arr = (
        np.zeros((), dtype=np.float64)
        if trad_mask is None
        else np.asarray(trad_mask, dtype=np.float64)
    )

    eps_himmel_in = (
        klar_himmel_emissivitet_prata(t_luft_c, relativ_fuktighet_pct)
        if emissivitet_himmel is None
        else np.asarray(emissivitet_himmel, dtype=np.float64)
    )

    (
        svf_a,
        skugga_a,
        hojd_a,
        k_dir_a,
        k_diff_a,
        ta_a,
        rh_a,
        alb_m_a,
        alb_v_a,
        eps_m_a,
        eps_v_a,
        dt_m_a,
        dt_v_a,
        trad_a,
        eps_h_a,
    ) = np.broadcast_arrays(
        np.asarray(svf, dtype=np.float64),
        np.asarray(skuggmask, dtype=np.float64),
        np.asarray(solhojd_rad, dtype=np.float64),
        np.asarray(sol_direkt_w_m2, dtype=np.float64),
        np.asarray(sol_diffus_w_m2, dtype=np.float64),
        np.asarray(t_luft_c, dtype=np.float64),
        np.asarray(relativ_fuktighet_pct, dtype=np.float64),
        np.asarray(albedo_mark, dtype=np.float64),
        np.asarray(albedo_vagg, dtype=np.float64),
        np.asarray(emissivitet_mark, dtype=np.float64),
        np.asarray(emissivitet_vagg, dtype=np.float64),
        np.asarray(t_mark_soltillagg_c, dtype=np.float64),
        np.asarray(t_vagg_soltillagg_c, dtype=np.float64),
        trad_arr,
        eps_himmel_in,
    )

    # Validera fysiska gränser (NaN bevaras; +/-inf och ogiltiga värden ger ValueError).
    _validera_intervall(svf_a, "svf", min_v=0.0, max_v=1.0)
    _validera_intervall(skugga_a, "skuggmask", min_v=0.0, max_v=1.0)
    _validera_intervall(hojd_a, "solhojd_rad", min_v=-math.pi / 2.0, max_v=math.pi / 2.0)
    _validera_intervall(k_dir_a, "sol_direkt_w_m2", min_v=0.0)
    _validera_intervall(k_diff_a, "sol_diffus_w_m2", min_v=0.0)
    _validera_intervall(ta_a, "t_luft_c", min_v=-KELVIN_OFFSET_C, strikt_min=True)
    _validera_intervall(rh_a, "relativ_fuktighet_pct", min_v=0.0, max_v=100.0)
    _validera_intervall(alb_m_a, "albedo_mark", min_v=0.0, max_v=1.0)
    _validera_intervall(alb_v_a, "albedo_vagg", min_v=0.0, max_v=1.0)
    _validera_intervall(eps_m_a, "emissivitet_mark", min_v=0.0, max_v=1.0, strikt_min=True)
    _validera_intervall(eps_v_a, "emissivitet_vagg", min_v=0.0, max_v=1.0, strikt_min=True)
    _validera_intervall(dt_m_a, "t_mark_soltillagg_c")
    _validera_intervall(dt_v_a, "t_vagg_soltillagg_c")
    _validera_intervall(trad_a, "trad_mask", min_v=0.0, max_v=1.0)
    _validera_intervall(eps_h_a, "emissivitet_himmel", min_v=0.0, max_v=1.0, strikt_min=True)

    giltig = (
        np.isfinite(svf_a)
        & np.isfinite(skugga_a)
        & np.isfinite(hojd_a)
        & np.isfinite(k_dir_a)
        & np.isfinite(k_diff_a)
        & np.isfinite(ta_a)
        & np.isfinite(rh_a)
        & np.isfinite(alb_m_a)
        & np.isfinite(alb_v_a)
        & np.isfinite(eps_m_a)
        & np.isfinite(eps_v_a)
        & np.isfinite(dt_m_a)
        & np.isfinite(dt_v_a)
        & np.isfinite(trad_a)
        & np.isfinite(eps_h_a)
    )

    # Effektiv krontransmission, solbelysning och övre halvklotsuppdelning:
    # svf_eff + f_trad + f_vagg = 1.00 exakt.
    tau_eff = 1.0 - trad_a * (1.0 - trad_transmissivitet)
    psi_sol = skugga_a * tau_eff
    svf_eff = svf_a * tau_eff
    f_trad = svf_a - svf_eff
    f_vagg = 1.0 - svf_a

    # Solgeometri (noll direkt sol när solen är vid eller under horisonten).
    sol_uppe = hojd_a > 0.0
    sin_h = np.where(sol_uppe, np.sin(hojd_a), 0.0)
    cos_h = np.where(sol_uppe, np.cos(hojd_a), 0.0)
    har_direkt_sol = sol_uppe & (k_dir_a > 0.0)
    sol_ind = np.where(har_direkt_sol, 1.0, 0.0)

    # Kortvågsflöden (6 riktningar: ned, upp, 4x sida) [W/m²].
    k_dir_horisontell = k_dir_a * sin_h
    k_vagg_infall = vagg_solandel * k_dir_horisontell + k_diff_a
    k_vagg_reflekterad = alb_v_a * f_vagg * k_vagg_infall

    k_ned = psi_sol * k_dir_horisontell + svf_eff * k_diff_a + k_vagg_reflekterad
    k_upp = alb_m_a * k_ned
    # Cylindrisk projektion (Holmer m.fl. 2015): 4 * F_sida * (1/pi) = 0.88 / pi = 0.2801.
    k_sida = (
        (1.0 / math.pi) * psi_sol * k_dir_a * cos_h
        + 0.5 * svf_eff * k_diff_a
        + 0.5 * k_vagg_reflekterad
        + 0.5 * k_upp
    )

    # Långvågsflöden (Stefan–Boltzmann med Kirchhoffs reflektion) [W/m²].
    tk_luft = ta_a + KELVIN_OFFSET_C
    tk_mark = tk_luft + sol_ind * psi_sol * dt_m_a
    tk_vagg_sol = tk_luft + sol_ind * dt_v_a

    # Kontrollera att ytornas temperaturer ligger över absoluta nollpunkten på giltiga celler.
    if np.any(giltig & (tk_mark <= 0.0)):
        raise ValueError("Markens yttemperatur (t_luft_c + soltillägg) måste vara > -273.15 °C.")
    if np.any(giltig & (tk_vagg_sol <= 0.0)):
        raise ValueError("Fasadens yttemperatur (t_luft_c + soltillägg) måste vara > -273.15 °C.")

    tk_luft_s = np.where(giltig, tk_luft, 293.15)
    tk_mark_s = np.where(giltig, tk_mark, 293.15)
    tk_vagg_sol_s = np.where(giltig, tk_vagg_sol, 293.15)

    sigma_ta4 = STEFAN_BOLTZMANN_W_M2_K4 * np.power(tk_luft_s, 4)
    l_himmel = eps_h_a * sigma_ta4
    l_trad = emissivitet_trad * sigma_ta4 + (1.0 - emissivitet_trad) * l_himmel
    l_vagg_emit = eps_v_a * STEFAN_BOLTZMANN_W_M2_K4 * (
        vagg_solandel * np.power(tk_vagg_sol_s, 4)
        + (1.0 - vagg_solandel) * np.power(tk_luft_s, 4)
    )
    l_vagg = l_vagg_emit + (1.0 - eps_v_a) * l_himmel

    l_ned = svf_eff * l_himmel + f_trad * l_trad + f_vagg * l_vagg
    l_upp = eps_m_a * STEFAN_BOLTZMANN_W_M2_K4 * np.power(tk_mark_s, 4) + (1.0 - eps_m_a) * l_ned
    l_sida = 0.5 * l_ned + 0.5 * l_upp

    # Total absorberad medelstrålningstäthet S_str [W/m²] och T_mrt [°C].
    k_viktad = (
        VINKELFAKTOR_NED * k_ned
        + VINKELFAKTOR_UPP * k_upp
        + 4.0 * VINKELFAKTOR_SIDA * k_sida
    )
    l_viktad = (
        VINKELFAKTOR_NED * l_ned
        + VINKELFAKTOR_UPP * l_upp
        + 4.0 * VINKELFAKTOR_SIDA * l_sida
    )
    s_str = alpha_k * k_viktad + eps_p * l_viktad
    s_str_saker = np.where(giltig, np.maximum(s_str, 0.0), 1.0)
    t_mrt = np.power(s_str_saker / (eps_p * STEFAN_BOLTZMANN_W_M2_K4), 0.25) - KELVIN_OFFSET_C

    def _maskera(arr: FloatRaster) -> FloatRaster:
        return np.asarray(np.where(giltig, arr, np.nan), dtype=np.float64)

    return TmrtResultat(
        t_mrt_c=_maskera(t_mrt),
        s_str_w_m2=_maskera(s_str),
        k_ned_w_m2=_maskera(k_ned),
        k_upp_w_m2=_maskera(k_upp),
        k_sida_w_m2=_maskera(k_sida),
        l_ned_w_m2=_maskera(l_ned),
        l_upp_w_m2=_maskera(l_upp),
        l_sida_w_m2=_maskera(l_sida),
        emissivitet_himmel=_maskera(eps_h_a),
        vinkelfaktorer=VINKELFAKTORER_STAENDE,
    )
