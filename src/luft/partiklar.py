# SPDX-License-Identifier: MIT
"""Tvåfraktions-PM10 (PM2.5 + PM_grov), vindhastighetsklasser och svensk MKN-dygnsstatistik.

Varför två fraktioner + 2–3 vindhastighetsklasser?
--------------------------------------------------
* **PM2.5** (fin fraktion, ~2 µm): låg sedimentations- och depositionshastighet
  (w_s ≈ 0,2–0,3 mm/s); följer 1/U-skalning väl → 1 transportlösning per riktning.
* **PM_grov** (PM10 − PM2.5, ~6 µm, dubbdäcksslitage & vägdamm): sedimenterar
  märkbart (w_s ≈ 2,4 mm/s) och filtreras i häckar/träd (`lad_m2_m3`). Vid låg vind
  hinner en större andel falla ut än vid hård vind, så ``c ∝ 1/U`` gäller inte exakt.
  Genom att lösa ``PM_grov`` vid 2–3 vindhastighetsklasser (t.ex. 1,5, 4,0 och 8,0 m/s)
  och linjärinterpolera flödesprodukten ``G(U) = U · C(U)`` fångas både exakt
  passiv skalning (där G(U) är konstant) och vindberoende deposition till ~3/8760 av
  kostnaden för att lösa varje timme separat.
* **MKN (SFS 2010:477)**:
  - PM10 årsmedel ≤ 40 µg/m³
  - PM10 dygnsmedel > 50 µg/m³ högst 35 dygn/år (36:e högsta dygnet ≤ 50 µg/m³)
  - PM2.5 årsmedel ≤ 25 µg/m³ (precisering/miljömål 10 µg/m³)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from luft.transport import Amne, amne_partikel
from luft.typer import FloatArray, LuftFel
from luft.vindbibliotek import Fordelning

MKN_PM10_ARSMEDEL_UG_M3 = 40.0
MKN_PM10_DYGN_UG_M3 = 50.0
MKN_PM10_MAX_DYGN_OVER_50 = 35
MKN_PM25_ARSMEDEL_UG_M3 = 25.0


@dataclass(frozen=True)
class PartikelFraktioner:
    """Två partikelfraktioner vars summa utgör PM10 (PM2.5 + PM_grov)."""

    pm25: Amne
    pm_grov: Amne


def standard_pm10_fraktioner(
    *,
    vegetationsdeposition_pm25_m_s: float = 0.002,
    vegetationsdeposition_grov_m_s: float = 0.015,
) -> PartikelFraktioner:
    """Standardfraktioner för svensk vägmiljö (PM2.5 + grovt vägdamm 2,5–10 µm).

    Sedimentationshastigheterna beräknas deterministiskt med Stokes–Cunningham:
    - PM2.5 (d_p = 2,0 µm, ρ = 1500 kg/m³): w_s ≈ 0,19 mm/s
    - PM_grov (d_p = 6,0 µm, ρ = 2200 kg/m³): w_s ≈ 2,45 mm/s
    """
    return PartikelFraktioner(
        pm25=amne_partikel(
            "PM2.5",
            diameter_um=2.0,
            densitet_kg_m3=1500.0,
            deposition_m_s=0.0005,
            vegetationsdeposition_m_s=vegetationsdeposition_pm25_m_s,
        ),
        pm_grov=amne_partikel(
            "PM_grov",
            diameter_um=6.0,
            densitet_kg_m3=2200.0,
            deposition_m_s=0.005,
            vegetationsdeposition_m_s=vegetationsdeposition_grov_m_s,
        ),
    )


def vindklass_vikter(
    u_m_s: npt.ArrayLike,
    klasser_m_s: npt.ArrayLike,
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64], FloatArray, FloatArray]:
    """Interpolationsindex och vikter för ``G(U) = U · C(U)`` mellan vindhastighetsklasser.

    Returnerar ``(k0, k1, v0, v1)`` så att för en given vindhastighet ``U`` gäller:

        C(U) ≈ (v0 · (U_{k0} · C_{k0}) + v1 · (U_{k1} · C_{k1})) / U
             = (v0 · U_{k0} / U) · C_{k0} + (v1 · U_{k1} / U) · C_{k1}.

    För ett passivt ämne där ``U · C(U)`` är konstant ger formeln exakt ``C(U)`` för
    alla vindhastigheter (inklusive extrapolering utanför klassintervallet).
    """
    u = np.asarray(u_m_s, dtype=np.float64)
    kl = np.asarray(klasser_m_s, dtype=np.float64)
    if kl.ndim != 1 or kl.size == 0:
        raise LuftFel("klasser_m_s måste vara en icke-tom 1D-vektor.")
    if not np.all(np.isfinite(kl)) or np.any(kl <= 0.0):
        raise LuftFel("Alla vindhastighetsklasser måste vara ändliga och > 0 m/s.")
    if kl.size > 1 and not np.all(np.diff(kl) > 0.0):
        raise LuftFel("klasser_m_s måste vara strikt stigande.")

    if kl.size == 1:
        k0 = np.zeros(u.shape, dtype=np.int64)
        k1 = np.zeros(u.shape, dtype=np.int64)
        v0 = np.ones(u.shape, dtype=np.float64)
        v1 = np.zeros(u.shape, dtype=np.float64)
        return k0, k1, v0, v1

    pos = np.searchsorted(kl, u, side="right") - 1
    k0 = np.clip(pos, 0, kl.size - 2).astype(np.int64)
    k1 = k0 + 1
    u0 = kl[k0]
    u1 = kl[k1]
    t = np.clip((u - u0) / (u1 - u0), 0.0, 1.0)
    v1 = np.asarray(t, dtype=np.float64)
    v0 = np.asarray(1.0 - t, dtype=np.float64)
    return k0, k1, v0, v1


@dataclass(frozen=True)
class MknPartikelStatistik:
    """Svensk MKN-utvärdering för PM10 och PM2.5 per cell (allt i µg/m³)."""

    pm10_arsmedel_ug_m3: FloatArray
    pm25_arsmedel_ug_m3: FloatArray
    pm_grov_arsmedel_ug_m3: FloatArray
    pm10_dygn_over_50: FloatArray
    pm10_dygn_90_4_ug_m3: FloatArray
    pm10_max_dygn_ug_m3: FloatArray
    antal_dygn: int


def timvis_pm10_mkn(
    emission_pm25_g_m_s: npt.ArrayLike,
    emission_pm_grov_g_m_s: npt.ArrayLike,
    enhetshalt_pm25_g_m3: npt.ArrayLike,
    enhetshalt_pm_grov_g_m3: npt.ArrayLike,
    klasser_grov_m_s: npt.ArrayLike,
    fordelning: Fordelning,
    *,
    bakgrund_pm25_ug_m3: float | npt.ArrayLike = 0.0,
    bakgrund_pm_grov_ug_m3: float | npt.ArrayLike = 0.0,
    min_giltiga_timmar_per_dygn: int = 18,
) -> MknPartikelStatistik:
    """Beräknar helårs PM10- och PM2.5-statistik inkl. MKN dygnsmedel (> 50 µg/m³).

    Args:
        emission_pm25_g_m_s: (n_vag, n_timmar) utsläpp av PM2.5 i g/(m·s).
        emission_pm_grov_g_m_s: (n_vag, n_timmar) utsläpp av grov fraktion (PM10 − PM2.5).
        enhetshalt_pm25_g_m3: (n_vag, n_rikt, ...) halt vid ``u_ref`` per g/(m·s).
        enhetshalt_pm_grov_g_m3: (n_klasser, n_vag, n_rikt, ...) halt per vindklass.
        klasser_grov_m_s: (n_klasser,) stigande vindhastigheter (m/s) för ``PM_grov``.
        fordelning: timvis vindfördelning från ``luft.vindbibliotek.fordela``.
        bakgrund_pm25_ug_m3: konstant eller tidsserie (n_timmar,) urban bakgrund i µg/m³.
        bakgrund_pm_grov_ug_m3: konstant eller tidsserie (n_timmar,) urban bakgrund i µg/m³.
        min_giltiga_timmar_per_dygn: minsta antal modellerade timmar för giltigt dygnsmedel.
    """
    e25 = np.asarray(emission_pm25_g_m_s, dtype=np.float64)
    eg = np.asarray(emission_pm_grov_g_m_s, dtype=np.float64)
    c25 = np.asarray(enhetshalt_pm25_g_m3, dtype=np.float64)
    cg = np.asarray(enhetshalt_pm_grov_g_m3, dtype=np.float64)
    kl = np.asarray(klasser_grov_m_s, dtype=np.float64)

    if e25.ndim != 2 or eg.shape != e25.shape:
        raise LuftFel("emission_pm25 och emission_pm_grov måste ha samma 2D-form (n_vag, n_tim).")
    n_vag, n_tim = e25.shape
    n_rikt = len(fordelning.riktningar_grad)
    if n_tim != fordelning.antal_timmar:
        raise LuftFel("Antal timmar i emissioner matchar inte fordelning.")
    if c25.ndim < 2 or c25.shape[:2] != (n_vag, n_rikt):
        raise LuftFel(f"enhetshalt_pm25 ska ha form ({n_vag}, {n_rikt}, ...), fick {c25.shape}.")
    if cg.ndim < 3 or cg.shape[:3] != (kl.size, n_vag, n_rikt) or cg.shape[3:] != c25.shape[2:]:
        raise LuftFel(
            f"enhetshalt_pm_grov ska ha form ({kl.size}, {n_vag}, {n_rikt}, {c25.shape[2:]})."
        )

    bg25 = np.broadcast_to(np.asarray(bakgrund_pm25_ug_m3, dtype=np.float64), (n_tim,))
    bgg = np.broadcast_to(np.asarray(bakgrund_pm_grov_ug_m3, dtype=np.float64), (n_tim,))

    form = c25.shape[2:]
    n_cell = int(np.prod(form)) if form else 1
    c25_platt = c25.reshape(n_vag * n_rikt, n_cell)
    cg_platt = cg.reshape(kl.size * n_vag * n_rikt, n_cell)

    # Faktisk timvis vindhastighet U_h = u_ref / skalfaktor (för modellerade timmar)
    u_ref = float(fordelning.u_ref_m_s)
    u_tim = np.where(
        fordelning.modellerad & (fordelning.skalfaktor > 0.0),
        u_ref / np.where(fordelning.skalfaktor > 0.0, fordelning.skalfaktor, 1.0),
        u_ref,
    )
    k0_alla, k1_alla, v0_alla, v1_alla = vindklass_vikter(u_tim, kl)

    n_dygn = n_tim // 24
    if n_dygn == 0:
        raise LuftFel("Minst 24 timmar (1 dygn) krävs för MKN-dygnsstatistik.")

    sum_pm25 = np.zeros(n_cell, dtype=np.float64)
    sum_grov = np.zeros(n_cell, dtype=np.float64)
    n_mod_tot = 0
    dygnsmedel_pm10: list[FloatArray] = []

    for d in range(n_dygn):
        h0, h1 = d * 24, (d + 1) * 24
        mod_lok = np.flatnonzero(fordelning.modellerad[h0:h1])
        if mod_lok.size == 0:
            continue
        hb = h0 + mod_lok
        nb = hb.size
        rad = np.arange(nb)

        # PM2.5 (1/U-skalning)
        k25 = np.zeros((nb, n_vag, n_rikt), dtype=np.float64)
        e25_b = e25[:, hb].T
        for s_idx in range(2):
            vikt = fordelning.skalfaktor[hb] * fordelning.vikt[hb, s_idx]
            k25[rad, :, fordelning.sektor_index[hb, s_idx]] += vikt[:, None] * e25_b
        # Omvandla g/m³ -> µg/m³ (* 1e6) och addera bakgrund
        h_pm25 = (k25.reshape(nb, n_vag * n_rikt) @ c25_platt) * 1e6 + bg25[hb, None]

        # PM_grov (flödesinterpolering G(U) = U·C(U) mellan vindhastighetsklasser)
        kg = np.zeros((nb, kl.size, n_vag, n_rikt), dtype=np.float64)
        eg_b = eg[:, hb].T
        u_b = u_tim[hb]
        k0_b, k1_b = k0_alla[hb], k1_alla[hb]
        w_kl0 = v0_alla[hb] * (kl[k0_b] / u_b)
        w_kl1 = v1_alla[hb] * (kl[k1_b] / u_b)
        for s_idx in range(2):
            sekt = fordelning.sektor_index[hb, s_idx]
            vw = fordelning.vikt[hb, s_idx]
            kg[rad, k0_b, :, sekt] += (vw * w_kl0)[:, None] * eg_b
            kg[rad, k1_b, :, sekt] += (vw * w_kl1)[:, None] * eg_b
        h_grov = (kg.reshape(nb, kl.size * n_vag * n_rikt) @ cg_platt) * 1e6 + bgg[hb, None]

        sum_pm25 += h_pm25.sum(axis=0)
        sum_grov += h_grov.sum(axis=0)
        n_mod_tot += int(nb)

        if nb >= min_giltiga_timmar_per_dygn:
            dygnsmedel_pm10.append((h_pm25 + h_grov).mean(axis=0))

    if n_mod_tot == 0:
        raise LuftFel("Inga modellerade timmar i väderserien.")

    pm25_ar = sum_pm25 / float(n_mod_tot)
    grov_ar = sum_grov / float(n_mod_tot)
    pm10_ar = pm25_ar + grov_ar

    if dygnsmedel_pm10:
        dygn_mat = np.stack(dygnsmedel_pm10, axis=0)  # (n_giltiga_dygn, n_cell)
        dygn_over_50 = np.sum(dygn_mat > MKN_PM10_DYGN_UG_M3, axis=0).astype(np.float64)
        dygn_90_4 = np.percentile(dygn_mat, 90.4, axis=0)
        max_dygn = np.max(dygn_mat, axis=0)
    else:
        dygn_over_50 = np.full(n_cell, np.nan, dtype=np.float64)
        dygn_90_4 = np.full(n_cell, np.nan, dtype=np.float64)
        max_dygn = np.full(n_cell, np.nan, dtype=np.float64)

    nan_cell = np.isnan(pm10_ar)
    dygn_over_50[nan_cell] = np.nan
    dygn_90_4[nan_cell] = np.nan
    max_dygn[nan_cell] = np.nan

    def _forma(a: FloatArray) -> FloatArray:
        ut: FloatArray = a.reshape(form) if form else a
        return ut

    return MknPartikelStatistik(
        pm10_arsmedel_ug_m3=_forma(pm10_ar),
        pm25_arsmedel_ug_m3=_forma(pm25_ar),
        pm_grov_arsmedel_ug_m3=_forma(grov_ar),
        pm10_dygn_over_50=_forma(dygn_over_50),
        pm10_dygn_90_4_ug_m3=_forma(dygn_90_4),
        pm10_max_dygn_ug_m3=_forma(max_dygn),
        antal_dygn=len(dygnsmedel_pm10),
    )
