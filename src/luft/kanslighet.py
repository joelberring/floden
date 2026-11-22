"""Adjungerad käll-/vegetationskänslighet, CELF-åtgärdsval och monotona MKN-intervall.

Utnyttjar två exakta matematiska egenskaper hos advektion–diffusion–deposition i
`luft.transport.TransportOperator`:
1. **Exakt adjungerad dualitet** (`Aᵀ y = g`): en framåtkörning + en adjungerad
   lösning ger både exakt källfördelning `∂C_r / ∂s_i = y_i` och exakt
   vegetationsgradient `∂C_r / ∂LAD_i = -v_veg · dx³ · y_i · c_i` i alla celler.
2. **M-matrismonotonitet** (`A⁻¹ >= 0`): lokalt haltbidrag är strikt monotont i
   källstyrkan och antitont i deposition/utspädning → två extremberäkningar ger
   garanterade MKN-dygnsintervall utan Monte Carlo.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from luft.transport import TransportOperator
from luft.typer import FloatArray, LuftFel


@dataclass(frozen=True)
class AdjungeradKanslighet:
    """Resultat från en framåtlösning + en adjungerad lösning."""

    receptor_halt_g_m3: float
    receptor_halt_ug_m3: float
    halt_g_m3: FloatArray
    kallkanslighet_g_m3_per_g_s: FloatArray
    kallbidrag_g_m3: FloatArray
    vegetationsgradient_g_m3_per_lad: FloatArray
    dualitetsfel_rel: float
    m_matris_ok: bool


@dataclass(frozen=True)
class LuftAtgard:
    """En vald gatu- eller källåtgärd från submodulär CELF-optimering."""

    zon_id: str
    marginal_sankning_ug_m3: float
    kumulativ_sankning_ug_m3: float
    sankning_ensam_ug_m3: float


@dataclass(frozen=True)
class MknIntervallResultat:
    """Monotont garanterat intervall för svensk MKN-dygnsstatistik (PM10)."""

    min_dygn_over_grans: int
    max_dygn_over_grans: int
    min_arsmedel_ug_m3: float
    max_arsmedel_ug_m3: float
    min_percentil_90_4_ug_m3: float
    max_percentil_90_4_ug_m3: float
    klarar_mkn_garanterat: bool
    bryter_mkn_garanterat: bool
    osaker_mkn_grans: bool
    emissionsfaktor_intervall: tuple[float, float]
    depositionsfaktor_intervall: tuple[float, float]


def berakna_adjungerad_kanslighet(
    operator: TransportOperator,
    kallor_g_s: FloatArray,
    receptor: FloatArray,
) -> AdjungeradKanslighet:
    """Beräknar exakt källfördelning och vegetationsgradient med 1 framåt- + 1 adjungerad lösning.

    För varje luftcell i gäller:
    - `kallbidrag_g_m3[i] = y_i * s_i` och `sum(kallbidrag_g_m3) == receptor_halt_g_m3`
    - `vegetationsgradient_g_m3_per_lad[i] = -v_veg * dx³ * y_i * c_i`
      (negativt värde = plantering av vegetation i cell i sänker receptorhalten).
    """
    s = np.asarray(kallor_g_s, dtype=np.float64)
    w = np.asarray(receptor, dtype=np.float64)
    if s.shape != operator.form or w.shape != operator.form:
        raise LuftFel(f"kallor och receptor ska ha formen {operator.form}.")

    c = operator.los(s)
    y = operator.los_adjungerad(w)

    luft = ~operator.vind.grid.fast
    w_luft = np.where(luft, w, 0.0)
    w_sum = float(w_luft.sum())
    if w_sum <= 0.0:
        raise LuftFel("Receptorn saknar vikt i luftceller.")

    c_r_framat = float(np.sum(w_luft * c) / w_sum)
    kallbidrag = y * s
    c_r_adj = float(np.sum(kallbidrag))
    namnare = max(abs(c_r_framat), 1e-30)
    dual_fel = abs(c_r_framat - c_r_adj) / namnare

    v_veg = float(operator.amne.vegetationsdeposition_m_s)
    cell_vol_m3 = float(operator.vind.grid.dx_m) ** 3
    veg_grad = np.where(luft, -v_veg * cell_vol_m3 * y * c, 0.0)

    marg = operator.m_matris_marginal()
    m_ok = (
        float(marg.get("max_offdiagonal", 1.0)) <= 1e-12
        and float(marg.get("min_kolumnmarginal_rel", -1.0)) >= -1e-12
    )

    return AdjungeradKanslighet(
        receptor_halt_g_m3=c_r_framat,
        receptor_halt_ug_m3=c_r_framat * 1e6,
        halt_g_m3=c,
        kallkanslighet_g_m3_per_g_s=y,
        kallbidrag_g_m3=kallbidrag,
        vegetationsgradient_g_m3_per_lad=veg_grad,
        dualitetsfel_rel=dual_fel,
        m_matris_ok=m_ok,
    )


def valj_basta_luftatgarder_k(
    kanslighet: AdjungeradKanslighet,
    zoner: Mapping[str, NDArray[np.bool_]],
    *,
    k: int = 5,
    reduktionsandel: float = 0.5,
) -> list[LuftAtgard]:
    """Submodulär CELF-optimering av topp-k gatu-/källåtgärder utan dubbelräkning.

    Om två åtgärdszoner överlappar på samma vägceller räknas varje cells haltsänkning
    bara en gång (täckningsfunktion: strikt monoton och submodulär → garanterad
    (1 - 1/e)-approximation med CELF).
    """
    if k <= 0 or not zoner:
        return []
    if not (0.0 < reduktionsandel <= 1.0):
        raise LuftFel(f"reduktionsandel måste ligga i (0, 1], fick {reduktionsandel}.")

    bidrag_ug = np.maximum(0.0, kanslighet.kallbidrag_g_m3 * 1e6 * float(reduktionsandel))
    form = bidrag_ug.shape

    zon_masker: dict[str, NDArray[np.bool_]] = {}
    for zid in sorted(zoner):
        m = np.asarray(zoner[zid], dtype=np.bool_)
        if m.shape != form:
            raise LuftFel(f"Zon '{zid}' har form {m.shape}, förväntade {form}.")
        zon_masker[zid] = m

    # Steg 0: ensamvinst + CELF max-heap (-marginal, zid, iter)
    vinst_ensam: dict[str, float] = {}
    heap: list[tuple[float, str, int]] = []
    for zid, m in zon_masker.items():
        v0 = float(bidrag_ug[m].sum())
        vinst_ensam[zid] = v0
        if v0 > 0.0:
            heapq.heappush(heap, (-v0, zid, 0))

    tackta = np.zeros(form, dtype=np.bool_)
    valda_ids: set[str] = set()
    resultat: list[LuftAtgard] = []
    kumulativ = 0.0

    for steg in range(k):
        if not heap:
            break
        vald_id: str | None = None
        vald_marg = 0.0

        while heap:
            neg_v, zid, utv_steg = heapq.heappop(heap)
            if zid in valda_ids:
                continue
            if utv_steg == steg:
                vald_id = zid
                vald_marg = -neg_v
                break
            ny_m = zon_masker[zid] & ~tackta
            ny_v = float(bidrag_ug[ny_m].sum())
            if ny_v > 0.0:
                heapq.heappush(heap, (-ny_v, zid, steg))

        if vald_id is None or vald_marg <= 0.0:
            break

        valda_ids.add(vald_id)
        tackta |= zon_masker[vald_id]
        kumulativ += vald_marg
        resultat.append(
            LuftAtgard(
                zon_id=vald_id,
                marginal_sankning_ug_m3=vald_marg,
                kumulativ_sankning_ug_m3=kumulativ,
                sankning_ensam_ug_m3=vinst_ensam[vald_id],
            )
        )

    return resultat


def kor_monoton_mkn_intervall(
    timvis_lokal_ug_m3: NDArray[np.float64] | NDArray[np.float32],
    bakgrund_ug_m3: float | NDArray[np.float64] = 0.0,
    *,
    emissionsfaktor_intervall: tuple[float, float] = (0.8, 1.25),
    depositionsfaktor_intervall: tuple[float, float] = (0.8, 1.25),
    grans_dygn_ug_m3: float = 50.0,
    max_dygn_ar: int = 35,
) -> MknIntervallResultat:
    """Beräknar monotont garanterade MKN-dygnsintervall ur käll- och depositionsosäkerhet.

    Eftersom advektion–diffusion–deposition är en positiv M-matrisoperator ökar det
    lokala haltbidraget monotont med `emissionsfaktor` och avtar monotont med
    `depositionsfaktor` (effektiv utspädning/deposition). Två extremserier ger därför
    en garanterad inneslutning av antal överskridna dygn och årsmedel.
    """
    e_lo, e_hi = float(emissionsfaktor_intervall[0]), float(emissionsfaktor_intervall[1])
    d_lo, d_hi = float(depositionsfaktor_intervall[0]), float(depositionsfaktor_intervall[1])
    if not (math.isfinite(e_lo) and math.isfinite(e_hi) and 0.0 <= e_lo <= e_hi):
        raise LuftFel(f"Ogiltigt emissionsfaktor_intervall: {emissionsfaktor_intervall}.")
    if not (math.isfinite(d_lo) and math.isfinite(d_hi) and 0.0 < d_lo <= d_hi):
        raise LuftFel(f"Ogiltigt depositionsfaktor_intervall: {depositionsfaktor_intervall}.")

    lokal = np.asarray(timvis_lokal_ug_m3, dtype=np.float64)
    if np.any(lokal < 0.0):
        raise LuftFel("timvis_lokal_ug_m3 får inte innehålla negativa halter.")
    bg = np.asarray(bakgrund_ug_m3, dtype=np.float64)

    # Undre extrem: min emission, max deposition; Övre extrem: max emission, min deposition
    serie_lo = np.maximum(0.0, bg + lokal * (e_lo / d_hi))
    serie_hi = np.maximum(0.0, bg + lokal * (e_hi / d_lo))

    def _mkn_stats(s_tim: NDArray[np.float64]) -> tuple[int, float, float]:
        n_dygn = s_tim.size // 24
        if n_dygn == 0:
            raise LuftFel("timvis_lokal_ug_m3 måste innehålla minst 24 timmar.")
        dygn = s_tim[: n_dygn * 24].reshape(n_dygn, 24).mean(axis=1)
        dygn_over = int(np.count_nonzero(dygn > grans_dygn_ug_m3))
        arsmedel = float(np.mean(s_tim))
        p90_4 = float(np.percentile(dygn, 90.4))
        return dygn_over, arsmedel, p90_4

    dygn_lo, ars_lo, p90_lo = _mkn_stats(serie_lo)
    dygn_hi, ars_hi, p90_hi = _mkn_stats(serie_hi)
    klarar = dygn_hi <= max_dygn_ar
    bryter = dygn_lo > max_dygn_ar

    return MknIntervallResultat(
        min_dygn_over_grans=dygn_lo,
        max_dygn_over_grans=dygn_hi,
        min_arsmedel_ug_m3=ars_lo,
        max_arsmedel_ug_m3=ars_hi,
        min_percentil_90_4_ug_m3=p90_lo,
        max_percentil_90_4_ug_m3=p90_hi,
        klarar_mkn_garanterat=klarar,
        bryter_mkn_garanterat=bryter,
        osaker_mkn_grans=(not klarar and not bryter),
        emissionsfaktor_intervall=(e_lo, e_hi),
        depositionsfaktor_intervall=(d_lo, d_hi),
    )
