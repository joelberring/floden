"""Tidsstegad massbevarande referenslösare för sänkgrafdynamik (T03 steg 1–3)."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from dynamik.nodmodell import (
    STANDARD_CD,
    STANDARD_OVERFALL_BREDD_M,
    DynamikFel,
    DynamikResultat,
    NodTidsserie,
    aktiv_forfader,
    berakna_doman_areor,
    berakna_raster_och_ankomsttider,
    hitta_mottagare,
    niva_inkl_overhojd,
    nod_medel_gangtid_s,
    overfallsflode_m3_s,
    skapa_regn_intervall,
)
from sankgraf.typer import SankGraf

_VOLYM_TOL_M3 = 1e-12
_MIN_DELSTEG_S = 1e-4


def _kan_sla_ihop_barn(
    graf: SankGraf,
    aktiv: dict[int, bool],
    volym: dict[int, float],
    max_volym: dict[int, float],
    p_id: int,
    extra_overskott: float = 0.0,
) -> bool:
    """Kontrollerar om samtliga barn till `p_id` är aktiva och fulla vid den inre sadeln."""
    barn = graf.noder[p_id].barn_id
    if not barn:
        return False
    if not all(aktiv[c] for c in barn):
        return False
    total_v = sum(volym[c] for c in barn) + max(0.0, extra_overskott)
    total_vmax = sum(max_volym[c] for c in barn)
    if total_v < total_vmax - _VOLYM_TOL_M3:
        return False
    return all(volym[c] + max(0.0, extra_overskott) >= max_volym[c] - _VOLYM_TOL_M3 for c in barn)


def _interpolera_tid(t0: float, dt: float, v_fore: float, v_efter: float, v_mal: float) -> float:
    dv = v_efter - v_fore
    if dv <= 1e-18:
        return t0 + dt
    andel = min(1.0, max(0.0, (v_mal - v_fore) / dv))
    return t0 + andel * dt


def _registrera_tillstand(
    graf: SankGraf,
    aktiv: dict[int, bool],
    mergad: dict[int, bool],
    volym: dict[int, float],
    max_volym: dict[int, float],
    cell_area_m2: float,
    sorterade_noder: list[int],
    lov_under: dict[int, list[int]],
    nod_niva_hist: dict[int, list[float]],
    nod_volym_hist: dict[int, list[float]],
    lov_har_vatten: dict[int, bool],
) -> None:
    """Sparar ögonblicksbild av nivå (RH 2000) och volym (m³) för alla noder."""
    aktiv_niva: dict[int, float] = {}
    for nid, ar_aktiv in aktiv.items():
        if ar_aktiv:
            nod = graf.noder[nid]
            v = volym[nid]
            aktiv_niva[nid] = (
                niva_inkl_overhojd(nod, v, min_area_m2=cell_area_m2)
                if v > 0.0
                else nod.botten_rh2000
            )

    for nid in sorterade_noder:
        nod = graf.noder[nid]
        if aktiv[nid]:
            niva_val = aktiv_niva[nid]
            vol_val = volym[nid]
        elif mergad[nid]:
            ctrl = aktiv_forfader(graf, mergad, nid)
            niva_val = aktiv_niva[ctrl]
            vol_val = max_volym[nid]
        else:
            barn_nivaer = [
                aktiv_niva[aktiv_forfader(graf, mergad, b)]
                for b in nod.barn_id
                if aktiv_forfader(graf, mergad, b) in aktiv_niva
            ]
            niva_val = max(barn_nivaer) if barn_nivaer else nod.botten_rh2000
            aktiva_under = {aktiv_forfader(graf, mergad, lov) for lov in lov_under[nid]}
            vol_val = sum(volym[aid] for aid in aktiva_under)

        nod_niva_hist[nid].append(float(niva_val))
        nod_volym_hist[nid].append(float(vol_val))
        if nod.ar_lov and vol_val > 0.0:
            lov_har_vatten[nid] = True


def _steg_linjar_reservoar(
    q_in_m3_s: float,
    s_nu_m3: float,
    tau_s: float,
    dt_s: float,
) -> tuple[float, float]:
    """Exakt analytiskt steg för linjär reservoar `dS/dt = q_in - S/tau`.

    Returnerar `(nytt_magasin_m3, utflode_volym_m3)` med exakt massbalans.
    """
    if tau_s <= 1e-9 or dt_s <= 0.0:
        return 0.0, q_in_m3_s * dt_s + s_nu_m3
    faktor = math.exp(-dt_s / tau_s)
    s_ny = s_nu_m3 * faktor + q_in_m3_s * tau_s * (1.0 - faktor)
    dv_ut = max(0.0, q_in_m3_s * dt_s + s_nu_m3 - s_ny)
    return s_ny, dv_ut


def kor_dynamik_tidssteg(
    graf: SankGraf,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    dt_s: float = 1.0,
    slut_tid_s: float | None = None,
    overfall_lage: str = "omedelbar",
    overfall_bredd_m: float = STANDARD_OVERFALL_BREDD_M,
    cd: float = STANDARD_CD,
    gangtid_fordrojning: bool = False,
    avrinning_faktor: float = 1.0,
) -> DynamikResultat:
    """Kör tidsstegad massbevarande dynamik på en `SankGraf`.

    Args:
        graf: Byggd (ofiltrerad eller persistensfiltrerad) `SankGraf`.
        regn_tid_s: Tidpunkter (s) för regnserien (K4).
        regn_intensitet_mm_h: Regnintensitet (mm/h) vid motsvarande tidpunkter.
        dt_s: Yttre tidssteg i sekunder (> 0).
        slut_tid_s: Valfri sluttid i sekunder (standard: sista regntid).
        overfall_lage: `"omedelbar"` (kinematisk fyll-överfall-sammanslagning) eller
            `"skibord"` (bredskronsöverfall `Q = Cd * B * (h - h_overfall)^1.5` med
            adaptiva delsteg).
        overfall_bredd_m: Överfallsbredd i meter för `"skibord"`.
        cd: Överfallskoefficient `Cd` (m^(1/2)/s) för `"skibord"`.
        gangtid_fordrojning: Om True fördröjs avrinningen till varje lövnod med en
            massbevarande linjär reservoar baserad på nodens medelgångtid i `graf.flodesvagar`.
        avrinning_faktor: Andel av nederbörden som når ytan (0..1, eller > 0).

    Returns:
        `DynamikResultat` med tidsserier, maxdjup, ankomsttider och volymkontroll.
    """
    t_wall_start = time.perf_counter()

    dt_val = float(dt_s)
    if not math.isfinite(dt_val) or dt_val <= 0.0:
        raise DynamikFel(f"dt_s måste vara ändlig och > 0, fick {dt_val}.")
    if overfall_lage not in ("omedelbar", "skibord"):
        raise DynamikFel(
            f"Ogiltigt overfall_lage '{overfall_lage}', förväntade 'omedelbar' eller 'skibord'."
        )
    if not math.isfinite(overfall_bredd_m) or overfall_bredd_m < 0.0:
        raise DynamikFel(f"Ogiltig overfall_bredd_m: {overfall_bredd_m}")
    if not math.isfinite(cd) or cd < 0.0:
        raise DynamikFel(f"Ogiltigt Cd: {cd}")
    if not math.isfinite(avrinning_faktor) or avrinning_faktor < 0.0:
        raise DynamikFel(f"avrinning_faktor måste vara ändlig och >= 0, fick {avrinning_faktor}.")

    intervall, total_regn_raw_m = skapa_regn_intervall(
        regn_tid_s, regn_intensitet_mm_h, slut_tid_s=slut_tid_s
    )
    t_start = float(regn_tid_s[0])
    total_aktiv_area_m2, hav_area_m2, lov_areor = berakna_doman_areor(graf)
    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)

    # Bygg steglista som respekterar varje regnintervalls gränser exakt
    steg_lista: list[tuple[float, float, float]] = []
    for iv in intervall:
        t_cur = iv.t_start_s
        while t_cur < iv.t_slut_s - 1e-12:
            t_nasta = min(iv.t_slut_s, t_cur + dt_val)
            dt_steg = t_nasta - t_cur
            t_mitt = 0.5 * (t_cur + t_nasta)
            r_mitt_m_s = iv.intensitet_vid_tid_m_s(t_mitt) * float(avrinning_faktor)
            steg_lista.append((t_cur, dt_steg, r_mitt_m_s))
            t_cur = t_nasta

    in_m3 = total_aktiv_area_m2 * total_regn_raw_m * float(avrinning_faktor)

    # Tom graf (t.ex. A4 lutande plan)
    if not graf.noder:
        tider_tom: list[float] = [t_start]
        for t0, dt_steg, _ in steg_lista:
            tider_tom.append(t0 + dt_steg)
        hojd, bredd = graf.mark_z.shape
        kortid = time.perf_counter() - t_wall_start
        return DynamikResultat(
            tider_s=tuple(tider_tom),
            noder={},
            max_djup_m=np.zeros((hojd, bredd), dtype=np.float32),
            tid_till_0_1m_s=np.full((hojd, bredd), np.nan, dtype=np.float32),
            tid_till_0_2m_s=np.full((hojd, bredd), np.nan, dtype=np.float32),
            overfallsordning=[],
            in_m3=in_m3,
            lagrat_m3=0.0,
            ut_m3=in_m3,
            volymfel_rel=0.0,
            kortid_s=kortid,
            metod="tidssteg",
        )

    sorterade_noder = sorted(graf.noder)
    lov_noder = [nid for nid in sorterade_noder if graf.noder[nid].ar_lov]
    lov_under: dict[int, list[int]] = {
        nid: graf.lov_under_nod(nid) for nid in sorterade_noder
    }
    tau_per_lov: dict[int, float] = (
        nod_medel_gangtid_s(graf) if gangtid_fordrojning else {lid: 0.0 for lid in lov_noder}
    )

    volym: dict[int, float] = {nid: 0.0 for nid in sorterade_noder}
    max_volym: dict[int, float] = {nid: graf.noder[nid].max_volym_m3 for nid in sorterade_noder}
    a_top: dict[int, float] = {
        nid: max(
            float(graf.noder[nid].ytkurva[-1].a_m2) if graf.noder[nid].ytkurva else 0.0,
            cell_area_m2,
        )
        for nid in sorterade_noder
    }
    aktiv: dict[int, bool] = {nid: graf.noder[nid].ar_lov for nid in sorterade_noder}
    mergad: dict[int, bool] = {nid: False for nid in sorterade_noder}
    transit: dict[int, float] = {lid: 0.0 for lid in lov_noder}

    t_overfall: dict[int, float | None] = {nid: None for nid in sorterade_noder}
    t_sammanslagning: dict[int, float | None] = {nid: None for nid in sorterade_noder}
    overfallsordning: list[int] = []

    tider_hist: list[float] = [t_start]
    nod_niva_hist: dict[int, list[float]] = {nid: [] for nid in sorterade_noder}
    nod_volym_hist: dict[int, list[float]] = {nid: [] for nid in sorterade_noder}
    lov_har_vatten: dict[int, bool] = {lid: False for lid in lov_noder}

    _registrera_tillstand(
        graf,
        aktiv,
        mergad,
        volym,
        max_volym,
        cell_area_m2,
        sorterade_noder,
        lov_under,
        nod_niva_hist,
        nod_volym_hist,
        lov_har_vatten,
    )

    ut_m3 = 0.0

    def _utfor_sammanslagning(p_id: int, t_merge: float) -> None:
        barn = graf.noder[p_id].barn_id
        total_v = sum(volym[c] for c in barn)
        for c in barn:
            mergad[c] = True
            aktiv[c] = False
            volym[c] = max_volym[c]
            if t_sammanslagning[c] is None:
                t_sammanslagning[c] = t_merge
        aktiv[p_id] = True
        volym[p_id] = total_v
        if t_sammanslagning[p_id] is None:
            t_sammanslagning[p_id] = t_merge

    def _steg_omedelbar(t0: float, dt_steg: float, r_m_s: float) -> None:
        nonlocal ut_m3
        v_fore = dict(volym)
        ut_m3 += hav_area_m2 * r_m_s * dt_steg

        for lid in lov_noder:
            q_in = lov_areor[lid] * r_m_s
            s_ny, dv_till_nod = _steg_linjar_reservoar(
                q_in, transit[lid], tau_per_lov[lid], dt_steg
            )
            transit[lid] = s_ny
            if dv_till_nod > 0.0:
                ctrl = aktiv_forfader(graf, mergad, lid)
                volym[ctrl] += dv_till_nod

        ko: deque[int] = deque()
        i_ko: set[int] = set()

        def _lagg_i_ko(nid: int) -> None:
            if nid not in i_ko:
                i_ko.add(nid)
                ko.append(nid)

        for nid in sorterade_noder:
            if aktiv[nid] and volym[nid] >= max_volym[nid] - _VOLYM_TOL_M3 and (
                volym[nid] > max_volym[nid] + _VOLYM_TOL_M3 or graf.noder[nid].forald_id is not None
            ):
                _lagg_i_ko(nid)

        max_iter = max(1000, len(sorterade_noder) * 50)
        it = 0
        while ko:
            it += 1
            if it > max_iter:
                raise DynamikFel(
                    "Tidsstegning (omedelbar) konvergerade inte inom förväntat antal steg."
                )
            nid = ko.popleft()
            i_ko.discard(nid)
            if not aktiv[nid]:
                continue

            nod = graf.noder[nid]
            v_max = max_volym[nid]
            overskott = volym[nid] - v_max

            if nod.forald_id is None:
                if overskott <= _VOLYM_TOL_M3:
                    continue
                if t_overfall[nid] is None:
                    t_overfall[nid] = _interpolera_tid(t0, dt_steg, v_fore[nid], volym[nid], v_max)
                    overfallsordning.append(nid)
                volym[nid] = v_max
                mottagare = hitta_mottagare(graf, aktiv, mergad, nid)
                if mottagare is None:
                    ut_m3 += overskott
                else:
                    volym[mottagare] += overskott
                    if volym[mottagare] >= max_volym[mottagare] - _VOLYM_TOL_M3:
                        _lagg_i_ko(mottagare)
            else:
                p_id = nod.forald_id
                if _kan_sla_ihop_barn(graf, aktiv, volym, max_volym, p_id, extra_overskott=0.0):
                    barn = graf.noder[p_id].barn_id
                    sum_fore = sum(v_fore[c] for c in barn)
                    sum_efter = sum(volym[c] for c in barn)
                    sum_vmax = sum(max_volym[c] for c in barn)
                    t_merge = _interpolera_tid(t0, dt_steg, sum_fore, sum_efter, sum_vmax)
                    v_fore[p_id] = sum_fore
                    _utfor_sammanslagning(p_id, t_merge)
                    if volym[p_id] >= max_volym[p_id] - _VOLYM_TOL_M3:
                        _lagg_i_ko(p_id)
                elif overskott > _VOLYM_TOL_M3:
                    if t_overfall[nid] is None:
                        t_overfall[nid] = _interpolera_tid(
                            t0, dt_steg, v_fore[nid], volym[nid], v_max
                        )
                        overfallsordning.append(nid)
                    volym[nid] = v_max
                    mottagare = hitta_mottagare(graf, aktiv, mergad, nid)
                    if mottagare is None:
                        ut_m3 += overskott
                    else:
                        volym[mottagare] += overskott
                        if volym[mottagare] >= max_volym[mottagare] - _VOLYM_TOL_M3:
                            _lagg_i_ko(mottagare)

    def _steg_skibord(t0: float, dt_steg: float, r_m_s: float) -> None:
        nonlocal ut_m3
        t_sub = t0
        t_mal = t0 + dt_steg

        while t_sub < t_mal - 1e-12:
            dt_kvar = t_mal - t_sub
            dt_sub = dt_kvar

            if overfall_bredd_m > 0.0 and cd > 0.0:
                for nid in sorterade_noder:
                    if not aktiv[nid]:
                        continue
                    overskott = volym[nid] - max_volym[nid]
                    if overskott > _VOLYM_TOL_M3:
                        dh = overskott / a_top[nid]
                        dq_dv = 1.5 * cd * overfall_bredd_m * math.sqrt(dh) / a_top[nid]
                        if dq_dv > 0.0:
                            dt_sub = min(dt_sub, 0.25 / dq_dv)
            dt_sub = min(dt_kvar, max(_MIN_DELSTEG_S, dt_sub))

            v_fore = dict(volym)
            ut_m3 += hav_area_m2 * r_m_s * dt_sub

            for lid in lov_noder:
                q_in = lov_areor[lid] * r_m_s
                s_ny, dv_till_nod = _steg_linjar_reservoar(
                    q_in, transit[lid], tau_per_lov[lid], dt_sub
                )
                transit[lid] = s_ny
                if dv_till_nod > 0.0:
                    ctrl = aktiv_forfader(graf, mergad, lid)
                    volym[ctrl] += dv_till_nod

            # Kontrollera sammanslagning innan och efter skibordsutflöde
            sammanslogs = True
            while sammanslogs:
                sammanslogs = False
                for nid in sorterade_noder:
                    if not aktiv[nid]:
                        continue
                    p_id = graf.noder[nid].forald_id
                    if p_id is not None and _kan_sla_ihop_barn(
                        graf, aktiv, volym, max_volym, p_id
                    ):
                        barn = graf.noder[p_id].barn_id
                        sum_fore = sum(v_fore[c] for c in barn)
                        sum_efter = sum(volym[c] for c in barn)
                        sum_vmax = sum(max_volym[c] for c in barn)
                        t_merge = _interpolera_tid(t_sub, dt_sub, sum_fore, sum_efter, sum_vmax)
                        v_fore[p_id] = sum_fore
                        _utfor_sammanslagning(p_id, t_merge)
                        sammanslogs = True
                        break

            utflode_in: dict[int, float] = {}
            for nid in sorterade_noder:
                if not aktiv[nid]:
                    continue
                v_max = max_volym[nid]
                if volym[nid] > v_max + _VOLYM_TOL_M3:
                    if t_overfall[nid] is None:
                        t_overfall[nid] = _interpolera_tid(
                            t_sub, dt_sub, v_fore[nid], volym[nid], v_max
                        )
                        overfallsordning.append(nid)
                    nod = graf.noder[nid]
                    h_nu = niva_inkl_overhojd(nod, volym[nid], min_area_m2=cell_area_m2)
                    q_ut = overfallsflode_m3_s(h_nu, nod.overfall_rh2000, overfall_bredd_m, cd)
                    dv_ut = min(q_ut * dt_sub, max(0.0, volym[nid] - v_max))
                    if dv_ut > 0.0:
                        volym[nid] -= dv_ut
                        mottagare = hitta_mottagare(graf, aktiv, mergad, nid)
                        if mottagare is None:
                            ut_m3 += dv_ut
                        else:
                            utflode_in[mottagare] = utflode_in.get(mottagare, 0.0) + dv_ut

            for mottagare, dv_in in utflode_in.items():
                ctrl = aktiv_forfader(graf, mergad, mottagare)
                volym[ctrl] += dv_in

            sammanslogs = True
            while sammanslogs:
                sammanslogs = False
                for nid in sorterade_noder:
                    if not aktiv[nid]:
                        continue
                    p_id = graf.noder[nid].forald_id
                    if p_id is not None and _kan_sla_ihop_barn(
                        graf, aktiv, volym, max_volym, p_id
                    ):
                        barn = graf.noder[p_id].barn_id
                        sum_fore = sum(v_fore[c] for c in barn)
                        sum_efter = sum(volym[c] for c in barn)
                        sum_vmax = sum(max_volym[c] for c in barn)
                        t_merge = _interpolera_tid(t_sub, dt_sub, sum_fore, sum_efter, sum_vmax)
                        _utfor_sammanslagning(p_id, t_merge)
                        sammanslogs = True
                        break

            t_sub += dt_sub

    for t0, dt_steg, r_m_s in steg_lista:
        if overfall_lage == "omedelbar":
            _steg_omedelbar(t0, dt_steg, r_m_s)
        else:
            _steg_skibord(t0, dt_steg, r_m_s)

        tider_hist.append(t0 + dt_steg)
        _registrera_tillstand(
            graf,
            aktiv,
            mergad,
            volym,
            max_volym,
            cell_area_m2,
            sorterade_noder,
            lov_under,
            nod_niva_hist,
            nod_volym_hist,
            lov_har_vatten,
        )

    lagrat_sankor_m3 = sum(volym[nid] for nid in sorterade_noder if aktiv[nid])
    lagrat_transit_m3 = sum(transit.values())
    lagrat_m3 = lagrat_sankor_m3 + lagrat_transit_m3
    volymfel_rel = abs(in_m3 - ut_m3 - lagrat_m3) / in_m3 if in_m3 > 0.0 else 0.0

    lov_niva_matris: dict[int, NDArray[np.float64]] = {
        lid: np.asarray(nod_niva_hist[lid], dtype=np.float64) for lid in lov_noder
    }
    max_djup, tid_01, tid_02 = berakna_raster_och_ankomsttider(
        graf, tider_hist, lov_niva_matris, lov_har_vatten
    )

    tider_tuple = tuple(tider_hist)
    noder_ut: dict[int, NodTidsserie] = {
        nid: NodTidsserie(
            nod_id=nid,
            tider_s=tider_tuple,
            niva_rh2000=tuple(nod_niva_hist[nid]),
            volym_m3=tuple(nod_volym_hist[nid]),
            t_overfall_s=t_overfall[nid],
            t_sammanslagning_s=t_sammanslagning[nid],
        )
        for nid in sorterade_noder
    }

    kortid = time.perf_counter() - t_wall_start
    return DynamikResultat(
        tider_s=tider_tuple,
        noder=noder_ut,
        max_djup_m=max_djup,
        tid_till_0_1m_s=tid_01,
        tid_till_0_2m_s=tid_02,
        overfallsordning=overfallsordning,
        in_m3=in_m3,
        lagrat_m3=lagrat_m3,
        ut_m3=ut_m3,
        volymfel_rel=volymfel_rel,
        kortid_s=kortid,
        metod="tidssteg",
    )
