"""Koppling mellan T06 markförluster (K5/v1) och T03 dynamik/riskberäkning."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from mark_forluster.grundvatten import uppskatta_dtw_fran_terrang
from mark_forluster.infiltration import (
    MM_PER_M,
    SEKUNDER_PER_TIMME,
    green_ampt_tidsserie,
)
from mark_forluster.parametrar import (
    BYGGNAD,
    VATTEN,
    ForlustFel,
    JordartParametrar,
    effektiv_markparametrar,
    normalisera_jordart,
)
from mark_forluster.scenarier import (
    ForlustInstallningar,
    ForlustResultat,
    ScenarioK5,
    berakna_forluster_for_scenario,
)


@dataclass(frozen=True)
class ForlustKopplingResultat:
    """Effektiv K4-regnserie och nyckeltal efter Green–Ampt-infiltration och förluster (T06)."""

    scenario: ScenarioK5
    jordart: str
    effektiv_regn_tid_s: tuple[float, ...]
    effektiv_regn_intensitet_mm_h: tuple[float, ...]
    brutto_regn_mm: float
    initial_forlust_medel_mm: float
    infiltration_medel_mm: float
    netto_avrinning_medel_mm: float
    effektiv_avrinningskoefficient: float
    forlust_resultat: ForlustResultat

    def till_dict(self) -> dict[str, Any]:
        """Returnerar JSON-serialiserbar sammanfattning för webb-API och rapporter."""
        return {
            "jordart": self.jordart,
            "grundvatten": self.scenario.grundvatten,
            "tjale": bool(self.scenario.tjale),
            "initial_mm": float(self.scenario.forluster.initial_mm),
            "brunnar_igensatta_andel": float(self.scenario.brunnar_igensatta_andel),
            "brutto_regn_mm": float(self.brutto_regn_mm),
            "initial_forlust_medel_mm": float(self.initial_forlust_medel_mm),
            "infiltration_medel_mm": float(self.infiltration_medel_mm),
            "netto_avrinning_medel_mm": float(self.netto_avrinning_medel_mm),
            "effektiv_avrinningskoefficient": float(self.effektiv_avrinningskoefficient),
        }


def k4_till_strikt_intervallserie(
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
) -> tuple[list[float], list[float]]:
    """Konverterar en K4-serie (inkl. styckvis konstant CDS) till strikt ökande intervallserie.

    Hoppar över nolllängdssteg (`t[i+1] <= t[i]`) och sätter intervallets intensitet till
    trapetsmedelvärdet `0.5 * (I[i] + I[i+1])`, vilket bevarar total regnvolym exakt.
    """
    if len(regn_tid_s) != len(regn_intensitet_mm_h) or len(regn_tid_s) < 2:
        raise ForlustFel("Regntidsserien måste ha minst 2 punkter och lika längd.")

    tider: list[float] = []
    intens: list[float] = []
    for idx in range(len(regn_tid_s) - 1):
        t0 = float(regn_tid_s[idx])
        t1 = float(regn_tid_s[idx + 1])
        i0 = float(regn_intensitet_mm_h[idx])
        i1 = float(regn_intensitet_mm_h[idx + 1])
        if not (
            math.isfinite(t0) and math.isfinite(t1) and math.isfinite(i0) and math.isfinite(i1)
        ):
            raise ForlustFel("Regntidsserien innehåller icke-ändliga värden.")
        if t1 < t0:
            raise ForlustFel(f"Regntidsserien är ej sorterad i tid: {t1} < {t0}.")
        if i0 < 0.0 or i1 < 0.0:
            raise ForlustFel("Regnintensitet måste vara >= 0.")
        if t1 <= t0:
            continue
        if not tider:
            tider.append(t0)
        tider.append(t1)
        intens.append(0.5 * (i0 + i1))

    if len(tider) < 2:
        raise ForlustFel("Regntidsserien saknar positiva tidsintervall.")
    intens.append(intens[-1])
    return tider, intens


def berakna_effektivt_regn_k5(
    markslag: NDArray[np.uint8],
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    jordart: str = "moran",
    grundvatten: str = "normal",
    tjale: bool = False,
    initial_mm: float = 2.0,
    brunnar_igensatta_andel: float = 0.0,
    pixelstorlek_m: float = 0.25,
    mark_z: NDArray[np.floating[Any]] | None = None,
    dtw_m: NDArray[np.floating[Any]] | None = None,
    regn_ref: str = "inline_k4",
    tabell: dict[str, JordartParametrar] | None = None,
) -> ForlustKopplingResultat:
    """Beräknar effektiv tidsberoende K4-avrinning med Green–Ampt (T06) över ett markslagsraster.

    Tidigt regn i t.ex. ett 6 h CDS-regn fyller initial magasinering och infiltrerar i grönytor,
    medan intensitetstoppen ger hög avrinning. Total avrinningsvolym över aktiva (ej byggnad)
    celler matchar cellvis Green–Ampt exakt.
    """
    ms = np.asarray(markslag, dtype=np.uint8)
    if ms.ndim != 2 or ms.size == 0:
        raise ForlustFel(f"markslag måste vara ett icke-tomt 2D-raster, fick {ms.shape}.")

    norm_jord = normalisera_jordart(jordart)
    scenario = ScenarioK5(
        regn=regn_ref,
        forluster=ForlustInstallningar(modell="green-ampt", initial_mm=float(initial_mm)),
        grundvatten=grundvatten,
        tjale=bool(tjale),
        brunnar_igensatta_andel=float(brunnar_igensatta_andel),
    )

    t_strikt, i_strikt = k4_till_strikt_intervallserie(regn_tid_s, regn_intensitet_mm_h)

    dtw_arr: NDArray[np.float32] | None = None
    if dtw_m is not None:
        dtw_arr = np.asarray(dtw_m, dtype=np.float32)
    elif mark_z is not None:
        z_arr = np.asarray(mark_z, dtype=np.float32)
        if z_arr.shape != ms.shape:
            raise ForlustFel(f"mark_z har form {z_arr.shape}, förväntade {ms.shape}.")
        giltig_z = np.isfinite(z_arr)
        if np.any(giltig_z):
            vatten_mask = np.asarray(ms == VATTEN, dtype=np.bool_)
            if not np.any(vatten_mask):
                z_min = float(np.min(z_arr[giltig_z]))
                vatten_mask = np.asarray(giltig_z & (z_arr <= z_min + 0.05), dtype=np.bool_)
            z_ren = np.where(giltig_z, z_arr, float(np.median(z_arr[giltig_z]))).astype(np.float32)
            dtw_arr = uppskatta_dtw_fran_terrang(
                z_ren,
                vatten_mask,
                pixelstorlek_m=pixelstorlek_m,
            )

    forlust_res = berakna_forluster_for_scenario(
        scenario,
        ms,
        norm_jord,
        t_strikt,
        i_strikt,
        pixelstorlek_m=pixelstorlek_m,
        dtw_m=dtw_arr,
        tabell=tabell,
    )

    aktiv = ms != BYGGNAD
    antal_aktiva = int(np.sum(aktiv))
    if antal_aktiva == 0:
        aktiv = np.ones_like(ms, dtype=np.bool_)
        antal_aktiva = int(ms.size)

    hog_markfukt = scenario.grundvatten == "hog"
    n_steg = len(t_strikt) - 1
    netto_steg_m = np.zeros(n_steg, dtype=np.float64)

    unika_klasser, antal_per_klass = np.unique(ms[aktiv], return_counts=True)
    for klass_raw, antal_raw in zip(unika_klasser, antal_per_klass, strict=True):
        klass_int = int(klass_raw)
        vikt = float(antal_raw) / float(antal_aktiva)
        eff_p = effektiv_markparametrar(
            klass_int,
            norm_jord,
            tjale=scenario.tjale,
            hog_markfukt=hog_markfukt,
            tabell=tabell,
        )
        max_lagring_m: float | None = None
        if dtw_arr is not None:
            klass_mask = aktiv & (ms == klass_int)
            max_lagring_m = float(np.mean(forlust_res.infiltration_m[klass_mask]))

        ga = green_ampt_tidsserie(
            t_strikt,
            i_strikt,
            ksat_mm_h=eff_p.ksat_mm_h,
            psi_m=eff_p.psi_m,
            delta_theta=eff_p.delta_theta,
            initial_mm=scenario.forluster.initial_mm,
            max_lagring_m=max_lagring_m,
        )
        netto_steg_m += vikt * ga.netto_regn_serie_m

    # Skala mjukt om rumsligt DTW-tak avviker något från klassmedelvärdet så att
    # totalvolymen matchar cellvis `forlust_res.netto_regn_m` exakt.
    mal_netto_m = float(np.mean(forlust_res.netto_regn_m[aktiv]))
    sum_steg_m = float(np.sum(netto_steg_m))
    if sum_steg_m > 0.0 and mal_netto_m >= 0.0:
        netto_steg_m *= mal_netto_m / sum_steg_m

    eff_t: list[float] = []
    eff_i: list[float] = []
    for steg_idx in range(n_steg):
        t0 = t_strikt[steg_idx]
        t1 = t_strikt[steg_idx + 1]
        dt_s = t1 - t0
        intens_mm_h = max(
            0.0,
            (float(netto_steg_m[steg_idx]) * MM_PER_M) * (SEKUNDER_PER_TIMME / dt_s),
        )
        eff_t.extend((t0, t1))
        eff_i.extend((intens_mm_h, intens_mm_h))

    brutto_mm = float(forlust_res.regn_totalt_m) * MM_PER_M
    init_mm = float(np.mean(forlust_res.initial_forlust_m[aktiv])) * MM_PER_M
    infil_mm = float(np.mean(forlust_res.infiltration_m[aktiv])) * MM_PER_M
    netto_mm = mal_netto_m * MM_PER_M
    koeff = (netto_mm / brutto_mm) if brutto_mm > 0.0 else 0.0

    return ForlustKopplingResultat(
        scenario=scenario,
        jordart=norm_jord,
        effektiv_regn_tid_s=tuple(eff_t),
        effektiv_regn_intensitet_mm_h=tuple(eff_i),
        brutto_regn_mm=brutto_mm,
        initial_forlust_medel_mm=init_mm,
        infiltration_medel_mm=infil_mm,
        netto_avrinning_medel_mm=netto_mm,
        effektiv_avrinningskoefficient=float(max(0.0, min(1.0, koeff))),
        forlust_resultat=forlust_res,
    )
