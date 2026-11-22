"""Linjära enhetssvar (impulssvar) och snabb faltning före första överfall (T03 steg 5)."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from dynamik.nodmodell import (
    DynamikFel,
    RegnIntervall,
    berakna_doman_areor,
    nod_medel_gangtid_s,
    skapa_regn_intervall,
)
from dynamik.tidsstegning import kor_dynamik_tidssteg
from sankgraf.typer import SankGraf


@dataclass(frozen=True)
class EnhetssvarTabell:
    """Förberäknade diskreta enhetssvar (impulssvar) per lövnod före första överfall."""

    dt_s: float
    horisont_s: float
    tider_s: NDArray[np.float64]
    impulssvar_per_nod: dict[int, NDArray[np.float64]]
    gangtid_per_nod_s: dict[int, float]
    max_volym_per_nod_m3: dict[int, float]
    avrinningsyta_per_nod_m2: dict[int, float]


@dataclass(frozen=True)
class FaltningResultat:
    """Resultat från snabb faltning av ny regnserie mot förberäknat enhetssvar."""

    tider_s: NDArray[np.float64]
    inflode_m3_s: dict[int, NDArray[np.float64]]
    volym_m3: dict[int, NDArray[np.float64]]
    niva_rh2000: dict[int, NDArray[np.float64]]
    t_forsta_overfall_s: dict[int, float | None]
    tidigaste_overfall_s: float | None
    kortid_s: float


@dataclass(frozen=True)
class FaltningVinstResultat:
    """Uppmätt tidsvinst och noggrannhet för faltning jämfört med full tidsstegning."""

    faltning_tid_s: float
    tidsstegning_tid_s: float
    speedup_faktor: float
    max_rel_volymfel_fore_overfall: float
    antal_regn: int


def _diskret_impulssvar(
    area_m2: float,
    tau_s: float,
    dt_s: float,
    n_steg: int,
) -> NDArray[np.float64]:
    """Exakt diskret impulssvar (m³ inflöde per m regnpuls) för linjär reservoar."""
    h = np.zeros(n_steg, dtype=np.float64)
    if n_steg == 0 or area_m2 <= 0.0:
        return h
    if tau_s <= 1e-9:
        h[0] = area_m2
        return h

    alfa = math.exp(-dt_s / tau_s)
    s0 = area_m2 * (tau_s / dt_s) * (1.0 - alfa)
    h[0] = area_m2 - s0
    if n_steg > 1:
        k_idx = np.arange(n_steg - 1, dtype=np.float64)
        h[1:] = s0 * (1.0 - alfa) * np.power(alfa, k_idx)
    return h


def bygg_enhetssvar(
    graf: SankGraf,
    dt_s: float = 60.0,
    horisont_s: float = 3600.0,
) -> EnhetssvarTabell:
    """Beräknar linjära enhetssvar per lövnod före första överfall (linjär reservoar).

    Varje lövnod får ett diskret impulssvar `h[k]` (m³ inflöde i steg `k` per meter
    enhetsregn i steg 0) baserat på nodens `avrinningsyta_m2` och medelgångtid i
    `graf.flodesvagar`.
    """
    dt_val = float(dt_s)
    hor_val = float(horisont_s)
    if not math.isfinite(dt_val) or dt_val <= 0.0:
        raise DynamikFel(f"dt_s måste vara ändlig och > 0, fick {dt_val}.")
    if not math.isfinite(hor_val) or hor_val <= 0.0:
        raise DynamikFel(f"horisont_s måste vara ändlig och > 0, fick {hor_val}.")

    n_steg = max(1, math.ceil(hor_val / dt_val))
    tider = np.arange(n_steg, dtype=np.float64) * dt_val
    gangtider = nod_medel_gangtid_s(graf)

    impulssvar: dict[int, NDArray[np.float64]] = {}
    max_volymer: dict[int, float] = {}
    areor: dict[int, float] = {}

    _, _, lov_areor = berakna_doman_areor(graf)  # med takvatten om grafen bär det
    for lov in graf.lovnoder():
        lid = lov.nod_id
        area = float(lov_areor[lid])
        tau = float(gangtider.get(lid, 0.0))
        impulssvar[lid] = _diskret_impulssvar(area, tau, dt_val, n_steg)
        max_volymer[lid] = float(lov.max_volym_m3)
        areor[lid] = area

    return EnhetssvarTabell(
        dt_s=dt_val,
        horisont_s=n_steg * dt_val,
        tider_s=tider,
        impulssvar_per_nod=impulssvar,
        gangtid_per_nod_s=gangtider,
        max_volym_per_nod_m3=max_volymer,
        avrinningsyta_per_nod_m2=areor,
    )


def _diskretisera_regn_steg_m(
    intervall: Sequence[RegnIntervall],
    t_start: float,
    dt_s: float,
    n_steg: int,
    avrinning_faktor: float,
) -> NDArray[np.float64]:
    """Integrerar regnintervall exakt till regndjup (m) per likformigt steg `dt_s`."""
    steg_m = np.zeros(n_steg, dtype=np.float64)
    if not intervall or n_steg == 0 or avrinning_faktor == 0.0:
        return steg_m

    iv_idx = 0
    n_iv = len(intervall)
    for k in range(n_steg):
        a = t_start + k * dt_s
        b = a + dt_s
        while iv_idx < n_iv and intervall[iv_idx].t_slut_s <= a:
            iv_idx += 1
        summa_m = 0.0
        j = iv_idx
        while j < n_iv and intervall[j].t_start_s < b:
            iv = intervall[j]
            lo = max(a, iv.t_start_s)
            hi = min(b, iv.t_slut_s)
            if hi > lo:
                r_lo = iv.intensitet_vid_tid_m_s(lo)
                r_hi = iv.intensitet_vid_tid_m_s(hi)
                summa_m += 0.5 * (r_lo + r_hi) * (hi - lo)
            j += 1
        steg_m[k] = summa_m * avrinning_faktor
    return steg_m


def faltning_fore_overfall(
    tabell: EnhetssvarTabell,
    graf: SankGraf,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    avrinning_faktor: float = 1.0,
    slut_tid_s: float | None = None,
) -> FaltningResultat:
    """Applicerar ny regnserie via snabb faltning (`np.convolve`) fram till första överfall."""
    t_wall_start = time.perf_counter()

    if not math.isfinite(avrinning_faktor) or avrinning_faktor < 0.0:
        raise DynamikFel(f"avrinning_faktor måste vara ändlig och >= 0, fick {avrinning_faktor}.")

    intervall, _ = skapa_regn_intervall(regn_tid_s, regn_intensitet_mm_h, slut_tid_s=slut_tid_s)
    t_start = float(regn_tid_s[0])
    t_slut = intervall[-1].t_slut_s if intervall else t_start
    dt_s = tabell.dt_s
    n_steg = max(1, math.ceil((t_slut - t_start) / dt_s)) if t_slut > t_start else 0

    regn_steg_m = _diskretisera_regn_steg_m(
        intervall, t_start, dt_s, n_steg, float(avrinning_faktor)
    )
    tider_ut = t_start + np.arange(n_steg + 1, dtype=np.float64) * dt_s

    inflode_ut: dict[int, NDArray[np.float64]] = {}
    volym_ut: dict[int, NDArray[np.float64]] = {}
    niva_ut: dict[int, NDArray[np.float64]] = {}
    t_overfall_ut: dict[int, float | None] = {}

    for lid, h_impuls in sorted(tabell.impulssvar_per_nod.items()):
        nod = graf.noder[lid]
        if n_steg == 0:
            inflode_ut[lid] = np.zeros(1, dtype=np.float64)
            volym_ut[lid] = np.zeros(1, dtype=np.float64)
            niva_ut[lid] = np.full(1, nod.botten_rh2000, dtype=np.float64)
            t_overfall_ut[lid] = None
            continue

        # Förläng impulssvaret om regnserien är längre än horisonten
        if h_impuls.size < n_steg:
            tau = tabell.gangtid_per_nod_s.get(lid, 0.0)
            area = tabell.avrinningsyta_per_nod_m2[lid]
            h_eff = _diskret_impulssvar(area, tau, dt_s, n_steg)
        else:
            h_eff = h_impuls

        dv = np.asarray(np.convolve(regn_steg_m, h_eff)[:n_steg], dtype=np.float64)
        q_serie = np.empty(n_steg + 1, dtype=np.float64)
        q_serie[0] = 0.0
        q_serie[1:] = dv / dt_s

        v_serie = np.empty(n_steg + 1, dtype=np.float64)
        v_serie[0] = 0.0
        v_serie[1:] = np.cumsum(dv)

        v_max = tabell.max_volym_per_nod_m3[lid]
        t_ov: float | None = None
        over_idx = np.flatnonzero(v_serie >= v_max - 1e-12)
        if over_idx.size > 0:
            idx = int(over_idx[0])
            if idx == 0:
                t_ov = float(tider_ut[0])
            else:
                v0 = float(v_serie[idx - 1])
                v1 = float(v_serie[idx])
                andel = (v_max - v0) / (v1 - v0) if v1 > v0 else 0.0
                t_ov = float(tider_ut[idx - 1]) + min(1.0, max(0.0, andel)) * dt_s

        v_begr = np.minimum(v_serie, v_max)
        niva_serie = np.asarray(
            [nod.niva_vid_volym(float(v)) if float(v) > 0.0 else nod.botten_rh2000 for v in v_begr],
            dtype=np.float64,
        )

        inflode_ut[lid] = q_serie
        volym_ut[lid] = v_begr
        niva_ut[lid] = niva_serie
        t_overfall_ut[lid] = t_ov

    overfall_tider = [t for t in t_overfall_ut.values() if t is not None]
    tidigaste = min(overfall_tider) if overfall_tider else None
    kortid = time.perf_counter() - t_wall_start

    return FaltningResultat(
        tider_s=tider_ut,
        inflode_m3_s=inflode_ut,
        volym_m3=volym_ut,
        niva_rh2000=niva_ut,
        t_forsta_overfall_s=t_overfall_ut,
        tidigaste_overfall_s=tidigaste,
        kortid_s=kortid,
    )


def mat_faltning_vinst(
    graf: SankGraf,
    regnserier: Sequence[tuple[Sequence[float], Sequence[float]]],
    *,
    dt_s: float = 10.0,
    horisont_s: float = 3600.0,
    avrinning_faktor: float = 1.0,
) -> FaltningVinstResultat:
    """Mäter speedup-faktor och max relativt volymfel före överfall: faltning vs tidsstegning."""
    if not regnserier:
        raise DynamikFel("regnserier måste innehålla minst en regnserie.")

    tabell = bygg_enhetssvar(graf, dt_s=dt_s, horisont_s=horisont_s)

    t0_falt = time.perf_counter()
    falt_resultat: list[FaltningResultat] = [
        faltning_fore_overfall(
            tabell, graf, tider, intens, avrinning_faktor=avrinning_faktor
        )
        for tider, intens in regnserier
    ]
    falt_tid = time.perf_counter() - t0_falt

    t0_ref = time.perf_counter()
    ref_resultat = [
        kor_dynamik_tidssteg(
            graf,
            tider,
            intens,
            dt_s=dt_s,
            overfall_lage="omedelbar",
            gangtid_fordrojning=True,
            avrinning_faktor=avrinning_faktor,
        )
        for tider, intens in regnserier
    ]
    ref_tid = time.perf_counter() - t0_ref

    max_rel_fel = 0.0
    for f_res, r_res in zip(falt_resultat, ref_resultat, strict=True):
        t_grans = f_res.tidigaste_overfall_s
        r_tider = np.asarray(r_res.tider_s, dtype=np.float64)
        for lid, f_vol in f_res.volym_m3.items():
            if lid not in r_res.noder:
                continue
            r_vol = np.asarray(r_res.noder[lid].volym_m3, dtype=np.float64)
            f_vol_interp = np.interp(r_tider, f_res.tider_s, f_vol)
            mask = (
                r_tider < t_grans - 1e-6
                if t_grans is not None
                else np.ones(r_tider.shape, dtype=bool)
            )
            if not np.any(mask):
                continue
            ref_m = r_vol[mask]
            falt_m = f_vol_interp[mask]
            skala = float(np.max(ref_m))
            if skala > 1e-12:
                rel_fel = float(np.max(np.abs(falt_m - ref_m))) / skala
                max_rel_fel = max(max_rel_fel, rel_fel)

    speedup = ref_tid / falt_tid if falt_tid > 0.0 else math.inf
    return FaltningVinstResultat(
        faltning_tid_s=falt_tid,
        tidsstegning_tid_s=ref_tid,
        speedup_faktor=speedup,
        max_rel_volymfel_fore_overfall=max_rel_fel,
        antal_regn=len(regnserier),
    )
