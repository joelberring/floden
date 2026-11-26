"""Händelsestyrd exakt fyllningslösare för sänkgrafdynamik (T03 steg 4, Q1)."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from dynamik.nodmodell import (
    DynamikFel,
    DynamikResultat,
    NodTidsserie,
    RegnIntervall,
    aktiv_forfader,
    applicera_initial_forlust,
    berakna_doman_areor,
    berakna_raster_och_ankomsttider,
    hitta_mottagare,
    regn_ytor_m2,
    skapa_regn_intervall,
)
from sankgraf.typer import MARKSLAG_BYGGNAD, SankGraf

_VOLYM_TOL_M3 = 1e-12
_TID_TOL_S = 1e-12
_STANDARD_ANTAL_SAMPEL = 120


@dataclass(frozen=True)
class GrafStrukturCache:
    """Förberäknad grafstruktur som återanvänds över många ensemblekörningar."""

    sorterade_noder: tuple[int, ...]
    lov_noder: tuple[int, ...]
    lov_under: dict[int, tuple[int, ...]]
    direkt_area_m2: dict[int, float]
    max_volym_m3: dict[int, float]
    total_aktiv_area_m2: float
    hav_area_m2: float
    total_lov_area_m2: float


@dataclass(frozen=True)
class HandelseKardinalResultat:
    """Lättviktsresultat från händelsestyrd körning (för snabb 500-scenariers ensemble)."""

    nod_max_niva_rh2000: dict[int, float]
    nod_max_volym_m3: dict[int, float]
    lov_max_niva_rh2000: dict[int, float]
    lov_har_vatten: dict[int, bool]
    overfallsordning: tuple[int, ...]
    t_overfall_s: dict[int, float | None]
    t_sammanslagning_s: dict[int, float | None]
    in_m3: float
    lagrat_m3: float
    ut_m3: float
    volymfel_rel: float
    kortid_s: float
    avtappat_m3: float = 0.0


def avtappning_per_nod_m3_s(
    graf: SankGraf,
    cache: GrafStrukturCache,
    avtappning_mm_h: float | NDArray[np.float64] | None,
) -> dict[int, float]:
    """Avtappningskapacitet (m³/s) per nod ur kapacitet i mm/h per cell (fas 3).

    Samma storhet som 2D-lösarens `tvad.kalltermer.Avtappning`: kapaciteten gäller cellens
    yta. Ett löv får summan över sina avrinningsceller, en sammansatt nod summan över sina löv.
    Celler som rinner direkt ut ur området (HAV) och byggnader bidrar inte. I grafen tappas
    noden av så länge den har vatten eller tillrinning (begränsat av det som finns), oavsett
    vilka celler som är våta; se `docs/MATEMATIK.md`.
    """
    if avtappning_mm_h is None:
        return {}
    form = graf.cell_till_nod.shape
    kap = np.asarray(
        np.broadcast_to(np.asarray(avtappning_mm_h, dtype=np.float64), form), dtype=np.float64
    )
    if kap.shape != form:
        raise DynamikFel(f"avtappning_mm_h har form {kap.shape}, grafen {form}.")
    kap = np.where(np.isfinite(kap), kap, 0.0)
    if np.any(kap < 0.0):
        raise DynamikFel("avtappning_mm_h får inte vara negativ.")
    z64 = np.asarray(graf.mark_z, dtype=np.float64)
    aktiv = (graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64)
    ct = graf.cell_till_nod
    mask = aktiv & (ct >= 0)
    if not np.any(mask) or not cache.sorterade_noder:
        return {}
    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)
    max_id = max(cache.sorterade_noder)
    per_lov = np.bincount(
        ct[mask], weights=kap[mask] * cell_area_m2 / 3.6e6, minlength=max_id + 1
    )
    return {
        nid: float(sum(per_lov[lid] for lid in cache.lov_under[nid]))
        for nid in cache.sorterade_noder
    }


def bygg_graf_struktur_cache(graf: SankGraf) -> GrafStrukturCache:
    """Förberäknar nodareor, delträdslöv och maxvolymer en gång per `SankGraf`."""
    sorterade = tuple(sorted(graf.noder))
    lov = tuple(nid for nid in sorterade if graf.noder[nid].ar_lov)
    lov_under = {nid: tuple(graf.lov_under_nod(nid)) for nid in sorterade}
    total_aktiv_area, hav_area, lov_areor = berakna_doman_areor(graf)
    direkt_area: dict[int, float] = {
        nid: (
            lov_areor[nid]
            if graf.noder[nid].ar_lov
            else sum(lov_areor[lid] for lid in lov_under[nid])
        )
        for nid in sorterade
    }
    max_vol = {nid: float(graf.noder[nid].max_volym_m3) for nid in sorterade}
    total_lov_area = sum(lov_areor.values())
    return GrafStrukturCache(
        sorterade_noder=sorterade,
        lov_noder=lov,
        lov_under=lov_under,
        direkt_area_m2=direkt_area,
        max_volym_m3=max_vol,
        total_aktiv_area_m2=total_aktiv_area,
        hav_area_m2=hav_area,
        total_lov_area_m2=total_lov_area,
    )


def _los_kvadratisk_tid(
    a_koeff: float,
    b_koeff: float,
    c_mal: float,
    u_max: float,
) -> float | None:
    """Löser `a_koeff * u^2 + b_koeff * u = c_mal` för minsta `u` i `(0, u_max]`."""
    if u_max <= 0.0:
        return None
    if abs(a_koeff) <= 1e-20:
        if abs(b_koeff) <= 1e-20:
            return None
        u = c_mal / b_koeff
        if _TID_TOL_S < u <= u_max + _TID_TOL_S:
            return min(u_max, max(0.0, u))
        return None

    # a u^2 + b u - c = 0
    diskr = b_koeff * b_koeff + 4.0 * a_koeff * c_mal
    if diskr < 0.0:
        if diskr > -1e-18:
            diskr = 0.0
        else:
            return None
    rot = math.sqrt(diskr)
    kandidater = [
        (-b_koeff - rot) / (2.0 * a_koeff),
        (-b_koeff + rot) / (2.0 * a_koeff),
    ]
    giltiga = [u for u in kandidater if _TID_TOL_S < u <= u_max + _TID_TOL_S]
    if not giltiga:
        return None
    return min(u_max, max(0.0, min(giltiga)))


def _berakna_effektiva_areor(
    graf: SankGraf,
    cache: GrafStrukturCache,
    aktiv: dict[int, bool],
    full: dict[int, bool],
    mergad: dict[int, bool],
    brunn_per_nod: dict[int, float],
    r_c: float,
    s_lutn: float,
) -> tuple[dict[int, float], dict[int, float], float, float, list[tuple[float, float]]]:
    """Beräknar effektiv avrinningsarea och brunnsavdrag per aktiv ej-full nod samt randutlopp.

    Utvärderar i topologisk ordning (uppströms före nedströms) så att en full nod med brunnar
    aldrig suger lagrat vatten ur en nedströms nod, och returnerar `(eff_area, eff_drain,
    ut_area, ut_drain_direkt, full_villkor)` där `full_villkor` innehåller `(a_tot, d_tot)` för
    aktiva fulla noder som kan släppa sitt full-tillstånd vid fallande regn.
    """
    aktiva_noder = [nid for nid in cache.sorterade_noder if aktiv[nid]]
    mottagare: dict[int, int | None] = {}
    indegree: dict[int, int] = {nid: 0 for nid in aktiva_noder}
    for nid in aktiva_noder:
        if not full[nid]:
            mottagare[nid] = None
            continue
        mott = hitta_mottagare(graf, aktiv, mergad, nid)
        mottagare[nid] = mott
        if mott is not None and mott in indegree:
            indegree[mott] += 1

    ko = [nid for nid in aktiva_noder if indegree[nid] == 0]
    topo_noder: list[int] = []
    idx_ko = 0
    while idx_ko < len(ko):
        nid = ko[idx_ko]
        idx_ko += 1
        topo_noder.append(nid)
        mott = mottagare[nid]
        if mott is not None and mott in indegree:
            indegree[mott] -= 1
            if indegree[mott] == 0:
                ko.append(mott)
    if len(topo_noder) < len(aktiva_noder):
        sedda = set(topo_noder)
        topo_noder.extend(nid for nid in aktiva_noder if nid not in sedda)

    eff_area: dict[int, float] = {}
    eff_drain: dict[int, float] = {}
    in_area: dict[int, float] = {}
    in_drain: dict[int, float] = {}
    ut_area = cache.hav_area_m2
    ut_drain_direkt = 0.0
    full_villkor: list[tuple[float, float]] = []

    for nid in topo_noder:
        a_tot = cache.direkt_area_m2[nid] + in_area.get(nid, 0.0)
        d_up = in_drain.get(nid, 0.0)
        d_dir = brunn_per_nod[nid]
        d_tot = d_up + d_dir

        if not full[nid]:
            eff_area[nid] = a_tot
            eff_drain[nid] = d_tot
            continue

        # Om en full nod tappas av brunnar snabbare än sitt totala inflöde (direktregn +
        # uppströms överfall) släpper den sitt full-tillstånd.
        q_net = a_tot * r_c - d_tot
        if d_tot > 0.0 and (q_net < -1e-18 or (abs(q_net) <= 1e-18 and a_tot * s_lutn < -1e-18)):
            full[nid] = False
            eff_area[nid] = a_tot
            eff_drain[nid] = d_tot
            continue

        if d_tot > 0.0 and a_tot > 0.0:
            full_villkor.append((a_tot, d_tot))

        mott = mottagare[nid]
        if mott is None:
            ut_area += a_tot
            ut_drain_direkt += d_tot
        else:
            in_area[mott] = in_area.get(mott, 0.0) + a_tot
            in_drain[mott] = in_drain.get(mott, 0.0) + d_tot

    return eff_area, eff_drain, ut_area, ut_drain_direkt, full_villkor


def _berakna_nod_ogonblicksbild(
    graf: SankGraf,
    cache: GrafStrukturCache,
    aktiv: dict[int, bool],
    mergad: dict[int, bool],
    volym: dict[int, float],
) -> tuple[dict[int, float], dict[int, float]]:
    """Beräknar `(niva_rh2000, volym_m3)` per nod för aktuellt aktivt tillstånd."""
    aktiv_niva: dict[int, float] = {}
    for nid in cache.sorterade_noder:
        if aktiv[nid]:
            nod = graf.noder[nid]
            v = volym[nid]
            aktiv_niva[nid] = nod.niva_vid_volym(v) if v > 0.0 else nod.botten_rh2000

    niva_ut: dict[int, float] = {}
    vol_ut: dict[int, float] = {}
    for nid in cache.sorterade_noder:
        nod = graf.noder[nid]
        if aktiv[nid]:
            niva_ut[nid] = aktiv_niva[nid]
            vol_ut[nid] = volym[nid]
        elif mergad[nid]:
            ctrl = aktiv_forfader(graf, mergad, nid)
            niva_ut[nid] = aktiv_niva[ctrl]
            vol_ut[nid] = cache.max_volym_m3[nid]
        else:
            barn_nivaer = [
                aktiv_niva[aktiv_forfader(graf, mergad, b)]
                for b in nod.barn_id
                if aktiv_forfader(graf, mergad, b) in aktiv_niva
            ]
            niva_ut[nid] = max(barn_nivaer) if barn_nivaer else nod.botten_rh2000
            aktiva_under = {aktiv_forfader(graf, mergad, lid) for lid in cache.lov_under[nid]}
            vol_ut[nid] = sum(volym[aid] for aid in aktiva_under)
    return niva_ut, vol_ut


@dataclass(frozen=True)
class _BalansHistorik:
    """Exakt massbalans per loggad tidpunkt i `tider_hist`."""

    in_kum_m3: list[float]
    lagrat_m3: list[float]
    utflode_kum_m3: list[float]
    avtappning_kum_m3: list[float]


def _kumulativt_regn_m(intervall: Sequence[RegnIntervall], t_val: float) -> float:
    """Exakt integrerat regn (m) i `intervall` upp till `t_val`."""
    kum = 0.0
    for iv in intervall:
        if t_val >= iv.t_slut_s:
            kum += iv.regn_m
            continue
        if t_val <= iv.t_start_s:
            break
        u = t_val - iv.t_start_s
        r0 = iv.intensitet_start_m_s
        r1 = iv.intensitet_slut_m_s
        lutn = (r1 - r0) / iv.dt_s if iv.dt_s > 0.0 else 0.0
        kum += r0 * u + 0.5 * lutn * u * u
        break
    return kum


def _kor_handelsestyrd_karna(
    graf: SankGraf,
    intervall: Sequence[RegnIntervall],
    t_start: float,
    total_regn_m: float,
    *,
    cache: GrafStrukturCache,
    avrinning_faktor: float,
    brunnar_avdrag_m3_s: float,
    spara_tidsserie: bool,
    utdata_tider_s: Sequence[float] | None,
    avtappning_m3_s: dict[int, float] | None = None,
    balans_ut: _BalansHistorik | None = None,
) -> tuple[
    HandelseKardinalResultat,
    list[float],
    dict[int, list[float]],
    dict[int, list[float]],
]:
    """Kör den händelsestyrda motorn och returnerar både kardinal- och ev. tidsseriedata."""
    t_wall_start = time.perf_counter()
    in_m3 = cache.total_aktiv_area_m2 * total_regn_m * float(avrinning_faktor)
    t_slut = intervall[-1].t_slut_s if intervall else t_start

    if not cache.sorterade_noder:
        if utdata_tider_s is not None:
            tider_tom = sorted(
                {
                    float(t)
                    for t in utdata_tider_s
                    if t_start - _TID_TOL_S <= float(t) <= t_slut + _TID_TOL_S
                }
            )
            if not tider_tom:
                tider_tom = [t_start, t_slut] if t_slut > t_start else [t_start]
        elif spara_tidsserie:
            tom_set: set[float] = {t_start, t_slut}
            for iv in intervall:
                tom_set.add(iv.t_start_s)
                tom_set.add(iv.t_slut_s)
            if t_slut > t_start:
                steg_s = (t_slut - t_start) / _STANDARD_ANTAL_SAMPEL
                for idx in range(1, _STANDARD_ANTAL_SAMPEL):
                    tom_set.add(t_start + idx * steg_s)
            tider_tom = sorted(tom_set)
        else:
            tider_tom = [t_start, t_slut] if t_slut > t_start else [t_start]
        if balans_ut is not None:
            for t in tider_tom:
                in_t = (
                    cache.total_aktiv_area_m2
                    * _kumulativt_regn_m(intervall, t)
                    * float(avrinning_faktor)
                )
                balans_ut.in_kum_m3.append(in_t)
                balans_ut.lagrat_m3.append(0.0)
                balans_ut.utflode_kum_m3.append(in_t)
                balans_ut.avtappning_kum_m3.append(0.0)
        kortid = time.perf_counter() - t_wall_start
        return (
            HandelseKardinalResultat(
                nod_max_niva_rh2000={},
                nod_max_volym_m3={},
                lov_max_niva_rh2000={},
                lov_har_vatten={},
                overfallsordning=(),
                t_overfall_s={},
                t_sammanslagning_s={},
                in_m3=in_m3,
                lagrat_m3=0.0,
                ut_m3=in_m3,
                volymfel_rel=0.0,
                kortid_s=kortid,
            ),
            tider_tom,
            {},
            {},
        )

    volym: dict[int, float] = {nid: 0.0 for nid in cache.sorterade_noder}
    aktiv: dict[int, bool] = {nid: graf.noder[nid].ar_lov for nid in cache.sorterade_noder}
    full: dict[int, bool] = {nid: False for nid in cache.sorterade_noder}
    mergad: dict[int, bool] = {nid: False for nid in cache.sorterade_noder}

    t_overfall: dict[int, float | None] = {nid: None for nid in cache.sorterade_noder}
    t_sammanslagning: dict[int, float | None] = {nid: None for nid in cache.sorterade_noder}
    overfallsordning: list[int] = []

    nod_max_niva: dict[int, float] = {
        nid: graf.noder[nid].botten_rh2000 for nid in cache.sorterade_noder
    }
    nod_max_volym: dict[int, float] = {nid: 0.0 for nid in cache.sorterade_noder}
    lov_har_vatten: dict[int, bool] = {lid: False for lid in cache.lov_noder}

    brunn_per_nod: dict[int, float] = {
        nid: (
            float(brunnar_avdrag_m3_s) * (cache.direkt_area_m2[nid] / cache.total_lov_area_m2)
            if brunnar_avdrag_m3_s > 0.0 and cache.total_lov_area_m2 > 0.0
            else 0.0
        )
        for nid in cache.sorterade_noder
    }
    # Avtappning per område (fas 3), läggs på brunnsavdraget per nod.
    avtapp = avtappning_m3_s or {}
    har_avtappning = any(v > 0.0 for v in avtapp.values())
    if har_avtappning:
        for nid, d in avtapp.items():
            brunn_per_nod[nid] += d
    har_avdrag = brunnar_avdrag_m3_s > 0.0 or har_avtappning
    avtappat_m3 = 0.0
    in_kum_cur = 0.0
    ut_yta_m3 = 0.0

    # Förbered planerade sampeltider när full tidsserie begärs
    planerade_tider: list[float] = []
    if spara_tidsserie:
        if utdata_tider_s is not None:
            planerade_tider = sorted(
                {
                    float(t)
                    for t in utdata_tider_s
                    if t_start - _TID_TOL_S <= float(t) <= t_slut + _TID_TOL_S
                }
            )
            if not planerade_tider:
                planerade_tider = [t_start, t_slut] if t_slut > t_start else [t_start]
        else:
            bas_set: set[float] = {t_start, t_slut}
            for iv in intervall:
                bas_set.add(iv.t_start_s)
                bas_set.add(iv.t_slut_s)
            if t_slut > t_start:
                steg_s = (t_slut - t_start) / _STANDARD_ANTAL_SAMPEL
                for idx in range(1, _STANDARD_ANTAL_SAMPEL):
                    bas_set.add(t_start + idx * steg_s)
            planerade_tider = sorted(bas_set)

    tider_hist: list[float] = []
    nod_niva_hist: dict[int, list[float]] = {nid: [] for nid in cache.sorterade_noder}
    nod_volym_hist: dict[int, list[float]] = {nid: [] for nid in cache.sorterade_noder}
    balans_hist = (
        balans_ut
        if balans_ut is not None
        else _BalansHistorik(
            in_kum_m3=[],
            lagrat_m3=[],
            utflode_kum_m3=[],
            avtappning_kum_m3=[],
        )
    )
    nasta_plan_idx = 0

    def _uppdatera_maxima(niva_nu: dict[int, float], vol_nu: dict[int, float]) -> None:
        for nid in cache.sorterade_noder:
            if niva_nu[nid] > nod_max_niva[nid]:
                nod_max_niva[nid] = niva_nu[nid]
            if vol_nu[nid] > nod_max_volym[nid]:
                nod_max_volym[nid] = vol_nu[nid]
        for lid in cache.lov_noder:
            if vol_nu[lid] > 0.0:
                lov_har_vatten[lid] = True

    def _logga_punkt(
        t_val: float,
        vol_snapshot: dict[int, float],
        *,
        in_kum_val: float,
        ut_yta_val: float,
        avtapp_val: float,
    ) -> None:
        niva_nu, vol_nu = _berakna_nod_ogonblicksbild(graf, cache, aktiv, mergad, vol_snapshot)
        _uppdatera_maxima(niva_nu, vol_nu)
        if not spara_tidsserie:
            return
        lagrat_val = sum(vol_snapshot[nid] for nid in cache.sorterade_noder if aktiv[nid])
        if tider_hist and abs(t_val - tider_hist[-1]) <= 1e-9:
            tider_hist[-1] = t_val
            for nid in cache.sorterade_noder:
                nod_niva_hist[nid][-1] = niva_nu[nid]
                nod_volym_hist[nid][-1] = vol_nu[nid]
            balans_hist.in_kum_m3[-1] = in_kum_val
            balans_hist.lagrat_m3[-1] = lagrat_val
            balans_hist.utflode_kum_m3[-1] = ut_yta_val
            balans_hist.avtappning_kum_m3[-1] = avtapp_val
            return
        tider_hist.append(t_val)
        for nid in cache.sorterade_noder:
            nod_niva_hist[nid].append(niva_nu[nid])
            nod_volym_hist[nid].append(vol_nu[nid])
        balans_hist.in_kum_m3.append(in_kum_val)
        balans_hist.lagrat_m3.append(lagrat_val)
        balans_hist.utflode_kum_m3.append(ut_yta_val)
        balans_hist.avtappning_kum_m3.append(avtapp_val)

    def _hantera_fulla_och_sammanslagningar(t_nu: float, kandidater: list[int]) -> None:
        """Markerar noder som nått maxvolym, slår ihop fulla syskonpar rekursivt."""
        att_bearbeta = list(kandidater)
        while att_bearbeta:
            # 1. Först: kontrollera om något syskonpar nu har samtliga barn aktiva och fulla
            sammanslogs = True
            while sammanslogs:
                sammanslogs = False
                kontrollerade_foraldrar: set[int] = set()
                for nid in list(att_bearbeta):
                    if not aktiv[nid]:
                        continue
                    p_id = graf.noder[nid].forald_id
                    if p_id is None or p_id in kontrollerade_foraldrar:
                        continue
                    kontrollerade_foraldrar.add(p_id)
                    barn = graf.noder[p_id].barn_id
                    if not barn or not all(aktiv[c] for c in barn):
                        continue
                    alla_fulla = all(
                        full[c] or volym[c] >= cache.max_volym_m3[c] - _VOLYM_TOL_M3 for c in barn
                    )
                    if not alla_fulla:
                        continue

                    # Samtliga syskon vid sadeln -> slå ihop till föräldernod p_id
                    total_v = sum(volym[c] for c in barn)
                    for c in barn:
                        mergad[c] = True
                        aktiv[c] = False
                        full[c] = False
                        volym[c] = cache.max_volym_m3[c]
                        if t_sammanslagning[c] is None:
                            t_sammanslagning[c] = t_nu
                    aktiv[p_id] = True
                    full[p_id] = False
                    volym[p_id] = total_v
                    if t_sammanslagning[p_id] is None:
                        t_sammanslagning[p_id] = t_nu

                    att_bearbeta = [c for c in att_bearbeta if c not in barn]
                    if volym[p_id] >= cache.max_volym_m3[p_id] - _VOLYM_TOL_M3:
                        volym[p_id] = cache.max_volym_m3[p_id]
                        att_bearbeta.append(p_id)
                    sammanslogs = True
                    break

            # 2. Kvarvarande aktiva kandidater som inte kunde slås ihop börjar svämma över
            nasta_kand: list[int] = []
            for nid in att_bearbeta:
                if not aktiv[nid] or full[nid]:
                    continue
                if volym[nid] >= cache.max_volym_m3[nid] - _VOLYM_TOL_M3:
                    volym[nid] = cache.max_volym_m3[nid]
                    full[nid] = True
                    if t_overfall[nid] is None:
                        t_overfall[nid] = t_nu
                        overfallsordning.append(nid)
                    # Kolla om syskonet råkar vara fullt redan (säkerhetskontroll)
                    p_id = graf.noder[nid].forald_id
                    if p_id is not None:
                        barn = graf.noder[p_id].barn_id
                        if barn and all(aktiv[c] and full[c] for c in barn):
                            nasta_kand.append(nid)
            att_bearbeta = nasta_kand

    if spara_tidsserie:
        while (
            nasta_plan_idx < len(planerade_tider)
            and planerade_tider[nasta_plan_idx] <= t_start + _TID_TOL_S
        ):
            _logga_punkt(
                planerade_tider[nasta_plan_idx],
                volym,
                in_kum_val=0.0,
                ut_yta_val=0.0,
                avtapp_val=0.0,
            )
            nasta_plan_idx += 1
        if utdata_tider_s is None and not tider_hist:
            _logga_punkt(t_start, volym, in_kum_val=0.0, ut_yta_val=0.0, avtapp_val=0.0)

    ut_m3 = 0.0
    max_handelser = max(10_000, len(cache.sorterade_noder) * 50 + len(intervall) * 20)
    antal_handelser = 0

    for iv in intervall:
        t_cur = iv.t_start_s
        r_start_eff = iv.intensitet_start_m_s * float(avrinning_faktor)
        r_slut_eff = iv.intensitet_slut_m_s * float(avrinning_faktor)
        s_lutn = (r_slut_eff - r_start_eff) / iv.dt_s if iv.dt_s > 0.0 else 0.0

        while t_cur < iv.t_slut_s - _TID_TOL_S:
            antal_handelser += 1
            if antal_handelser > max_handelser:
                raise DynamikFel("Händelsestyrd lösare överskred maximalt antal händelser.")

            dt_kvar = iv.t_slut_s - t_cur
            r_c = r_start_eff + s_lutn * (t_cur - iv.t_start_s)

            # Hantera eventuella nolllagringsnoder vid aktivt regn
            noll_kand = [
                nid
                for nid in cache.sorterade_noder
                if aktiv[nid] and not full[nid] and cache.max_volym_m3[nid] <= _VOLYM_TOL_M3
            ]
            if noll_kand and (r_c > 0.0 or s_lutn > 0.0):
                _hantera_fulla_och_sammanslagningar(t_cur, noll_kand)

            eff_area, eff_drain, ut_area, ut_drain_direkt, full_villkor = _berakna_effektiva_areor(
                graf, cache, aktiv, full, mergad, brunn_per_nod, r_c, s_lutn
            )

            # Hitta minsta tid till nästa diskreta händelse (fyllning, tömning eller nollgenomgång)
            u_min = dt_kvar
            if s_lutn < 0.0:
                for a_tot_f, d_tot_f in full_villkor:
                    u_slapp = (a_tot_f * r_c - d_tot_f) / (-a_tot_f * s_lutn)
                    if _TID_TOL_S < u_slapp < u_min:
                        u_min = u_slapp

            for nid, a_eff in eff_area.items():
                d_eff = eff_drain.get(nid, 0.0)
                a_kv = 0.5 * a_eff * s_lutn
                b_kv = a_eff * r_c - d_eff

                # Nollgenomgång för torr nod som börjar fyllas (stigande regn) eller inre topp
                # vid avtagande regn så att varken avtappning eller toppvärde förvanskas.
                if d_eff > 0.0 and a_eff > 0.0:
                    if s_lutn > 0.0 and volym[nid] <= _VOLYM_TOL_M3 and b_kv < -1e-18:
                        u_start = -b_kv / (a_eff * s_lutn)
                        if _TID_TOL_S < u_start < u_min:
                            u_min = u_start
                    elif s_lutn < 0.0 and b_kv > 1e-18:
                        u_topp = -b_kv / (a_eff * s_lutn)
                        if _TID_TOL_S < u_topp < u_min:
                            u_min = u_topp

                v_kvar = cache.max_volym_m3[nid] - volym[nid]
                if v_kvar > _VOLYM_TOL_M3:
                    u_fyll = _los_kvadratisk_tid(a_kv, b_kv, v_kvar, u_min)
                    if u_fyll is not None and u_fyll < u_min:
                        u_min = u_fyll

                if d_eff > 0.0 and volym[nid] > _VOLYM_TOL_M3:
                    u_tom = _los_kvadratisk_tid(a_kv, b_kv, -volym[nid], u_min)
                    if u_tom is not None and u_tom < u_min:
                        u_min = u_tom

            t_ny = t_cur + u_min

            # Utvärdera eventuella planerade sampeltider inuti (t_cur, t_ny) analytiskt
            if spara_tidsserie:
                while (
                    nasta_plan_idx < len(planerade_tider)
                    and planerade_tider[nasta_plan_idx] < t_ny - _TID_TOL_S
                ):
                    t_samp = planerade_tider[nasta_plan_idx]
                    u_s = t_samp - t_cur
                    dr_s = r_c * u_s + 0.5 * s_lutn * u_s * u_s
                    in_samp = in_kum_cur + cache.total_aktiv_area_m2 * dr_s
                    dv_avt_ut_s = (
                        min(ut_area * dr_s, ut_drain_direkt * u_s)
                        if ut_drain_direkt > 0.0
                        else 0.0
                    )
                    ut_yta_samp = ut_yta_m3 + (ut_area * dr_s - dv_avt_ut_s)
                    avtapp_samp = avtappat_m3 + dv_avt_ut_s
                    vol_samp = dict(volym)
                    for nid, a_eff in eff_area.items():
                        d_eff = eff_drain.get(nid, 0.0)
                        dv_r = a_eff * dr_s
                        if d_eff == 0.0:
                            ny_v = volym[nid] + dv_r
                            if ny_v > cache.max_volym_m3[nid]:
                                ut_yta_samp += ny_v - cache.max_volym_m3[nid]
                                ny_v = cache.max_volym_m3[nid]
                            vol_samp[nid] = ny_v
                        else:
                            tillg = volym[nid] + dv_r
                            dv_b = min(tillg, d_eff * u_s)
                            avtapp_samp += dv_b
                            ny_v = tillg - dv_b
                            if ny_v > cache.max_volym_m3[nid]:
                                ut_yta_samp += ny_v - cache.max_volym_m3[nid]
                                ny_v = cache.max_volym_m3[nid]
                            vol_samp[nid] = ny_v
                    _logga_punkt(
                        t_samp,
                        vol_samp,
                        in_kum_val=in_samp,
                        ut_yta_val=ut_yta_samp,
                        avtapp_val=avtapp_samp,
                    )
                    nasta_plan_idx += 1

            # Avancera till t_ny med exakt massbalans
            dr = r_c * u_min + 0.5 * s_lutn * u_min * u_min
            in_kum_cur += cache.total_aktiv_area_m2 * dr
            ut_m3 += ut_area * dr
            dv_avt_ut = (
                min(ut_area * dr, ut_drain_direkt * u_min) if ut_drain_direkt > 0.0 else 0.0
            )
            avtappat_m3 += dv_avt_ut
            ut_yta_m3 += ut_area * dr - dv_avt_ut

            nyfulla: list[int] = []
            for nid, a_eff in eff_area.items():
                d_eff = eff_drain.get(nid, 0.0)
                dv_r = a_eff * dr
                if d_eff == 0.0:
                    volym[nid] += dv_r
                else:
                    tillg = volym[nid] + dv_r
                    dv_brunn = min(tillg, d_eff * u_min)
                    volym[nid] = tillg - dv_brunn
                    ut_m3 += dv_brunn
                    avtappat_m3 += dv_brunn

                if volym[nid] >= cache.max_volym_m3[nid] - _VOLYM_TOL_M3:
                    # Eventuell avrundningsrest (< 1e-12) rinner vidare eller räknas exakt
                    rest = max(0.0, volym[nid] - cache.max_volym_m3[nid])
                    volym[nid] = cache.max_volym_m3[nid]
                    if rest > 0.0:
                        mott = hitta_mottagare(graf, aktiv, mergad, nid)
                        if mott is None:
                            ut_m3 += rest
                            ut_yta_m3 += rest
                        else:
                            volym[mott] += rest
                    nyfulla.append(nid)

            # Uppdatera toppar före ev. tömning/sammanslagning
            if har_avdrag:
                niva_nu, vol_nu = _berakna_nod_ogonblicksbild(graf, cache, aktiv, mergad, volym)
                _uppdatera_maxima(niva_nu, vol_nu)

            if nyfulla:
                _hantera_fulla_och_sammanslagningar(t_ny, nyfulla)

            if spara_tidsserie:
                if utdata_tider_s is None:
                    # Logga händelsetidpunkten och konsumera ev. matchande planerad tid
                    _logga_punkt(
                        t_ny,
                        volym,
                        in_kum_val=in_kum_cur,
                        ut_yta_val=ut_yta_m3,
                        avtapp_val=avtappat_m3,
                    )
                    while (
                        nasta_plan_idx < len(planerade_tider)
                        and planerade_tider[nasta_plan_idx] <= t_ny + _TID_TOL_S
                    ):
                        nasta_plan_idx += 1
                else:
                    while (
                        nasta_plan_idx < len(planerade_tider)
                        and planerade_tider[nasta_plan_idx] <= t_ny + _TID_TOL_S
                    ):
                        _logga_punkt(
                            planerade_tider[nasta_plan_idx],
                            volym,
                            in_kum_val=in_kum_cur,
                            ut_yta_val=ut_yta_m3,
                            avtapp_val=avtappat_m3,
                        )
                        nasta_plan_idx += 1

            t_cur = t_ny

    # Avslutande uppdatering av maxima och massbalans
    niva_slut, vol_slut = _berakna_nod_ogonblicksbild(graf, cache, aktiv, mergad, volym)
    _uppdatera_maxima(niva_slut, vol_slut)

    if spara_tidsserie:
        while nasta_plan_idx < len(planerade_tider):
            _logga_punkt(
                planerade_tider[nasta_plan_idx],
                volym,
                in_kum_val=in_kum_cur,
                ut_yta_val=ut_yta_m3,
                avtapp_val=avtappat_m3,
            )
            nasta_plan_idx += 1

    lagrat_m3 = sum(volym[nid] for nid in cache.sorterade_noder if aktiv[nid])
    volymfel_rel = abs(in_m3 - ut_m3 - lagrat_m3) / in_m3 if in_m3 > 0.0 else 0.0

    lov_max_niva = {lid: nod_max_niva[lid] for lid in cache.lov_noder}
    kortid = time.perf_counter() - t_wall_start

    kardinal = HandelseKardinalResultat(
        nod_max_niva_rh2000=nod_max_niva,
        nod_max_volym_m3=nod_max_volym,
        lov_max_niva_rh2000=lov_max_niva,
        lov_har_vatten=lov_har_vatten,
        overfallsordning=tuple(overfallsordning),
        t_overfall_s=t_overfall,
        t_sammanslagning_s=t_sammanslagning,
        in_m3=in_m3,
        lagrat_m3=lagrat_m3,
        ut_m3=ut_m3,
        volymfel_rel=volymfel_rel,
        kortid_s=kortid,
        avtappat_m3=avtappat_m3,
    )
    return kardinal, tider_hist, nod_niva_hist, nod_volym_hist


def kor_handelsestyrd_kardinal(
    graf: SankGraf,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    cache: GrafStrukturCache | None = None,
    slut_tid_s: float | None = None,
    avrinning_faktor: float = 1.0,
    initial_forlust_mm: float = 0.0,
    brunnar_avdrag_m3_s: float = 0.0,
    avtappning_mm_h: float | NDArray[np.float64] | None = None,
) -> HandelseKardinalResultat:
    """Snabb händelsestyrd körning som endast returnerar toppvärden, tider och massbalans."""
    if not math.isfinite(avrinning_faktor) or avrinning_faktor < 0.0:
        raise DynamikFel(f"avrinning_faktor måste vara ändlig och >= 0, fick {avrinning_faktor}.")
    if not math.isfinite(brunnar_avdrag_m3_s) or brunnar_avdrag_m3_s < 0.0:
        raise DynamikFel(
            f"brunnar_avdrag_m3_s måste vara ändlig och >= 0, fick {brunnar_avdrag_m3_s}."
        )

    raw_iv, _ = skapa_regn_intervall(regn_tid_s, regn_intensitet_mm_h, slut_tid_s=slut_tid_s)
    netto_iv, netto_regn_m = applicera_initial_forlust(raw_iv, initial_forlust_mm)
    graf_cache = cache if cache is not None else bygg_graf_struktur_cache(graf)

    kardinal, _, _, _ = _kor_handelsestyrd_karna(
        graf,
        netto_iv,
        float(regn_tid_s[0]),
        netto_regn_m,
        cache=graf_cache,
        avrinning_faktor=float(avrinning_faktor),
        brunnar_avdrag_m3_s=float(brunnar_avdrag_m3_s),
        spara_tidsserie=False,
        utdata_tider_s=None,
        avtappning_m3_s=avtappning_per_nod_m3_s(graf, graf_cache, avtappning_mm_h),
    )
    return kardinal


def kor_dynamik_handelsestyrd(
    graf: SankGraf,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    slut_tid_s: float | None = None,
    utdata_tider_s: Sequence[float] | None = None,
    avrinning_faktor: float = 1.0,
    initial_forlust_mm: float = 0.0,
    brunnar_avdrag_m3_s: float = 0.0,
    avtappning_mm_h: float | NDArray[np.float64] | None = None,
) -> DynamikResultat:
    """Kör exakt händelsestyrd fyllning–överfall–sammanslagning på en `SankGraf`.

    Hoppar direkt mellan diskreta händelser (regnändringar, överfallsstart och
    syskonsammanslagningar). För styckvis konstant eller styckvis linjärt regn
    är fyllnads-, överfalls- och sammanslagningstiderna analytiskt exakta.

    `avtappning_mm_h` (skalär eller raster, fas 3) ger varje nod en avtappning till
    dagvattennätet (se `avtappning_per_nod_m3_s`); utan den stannar vattnet i sänkorna. En
    slutsänka (K2/v2) töms bara genom avtappning; når den sitt hinder rinner överskottet ut
    ur modellen (`t_overfall_s` för slutsänkan är då satt).
    """
    t_wall_start = time.perf_counter()

    if not math.isfinite(avrinning_faktor) or avrinning_faktor < 0.0:
        raise DynamikFel(f"avrinning_faktor måste vara ändlig och >= 0, fick {avrinning_faktor}.")
    if not math.isfinite(brunnar_avdrag_m3_s) or brunnar_avdrag_m3_s < 0.0:
        raise DynamikFel(
            f"brunnar_avdrag_m3_s måste vara ändlig och >= 0, fick {brunnar_avdrag_m3_s}."
        )

    raw_iv, _ = skapa_regn_intervall(regn_tid_s, regn_intensitet_mm_h, slut_tid_s=slut_tid_s)
    netto_iv, netto_regn_m = applicera_initial_forlust(raw_iv, initial_forlust_mm)
    cache = bygg_graf_struktur_cache(graf)
    balans_hist = _BalansHistorik(
        in_kum_m3=[],
        lagrat_m3=[],
        utflode_kum_m3=[],
        avtappning_kum_m3=[],
    )

    kardinal, tider_hist, nod_niva_hist, nod_volym_hist = _kor_handelsestyrd_karna(
        graf,
        netto_iv,
        float(regn_tid_s[0]),
        netto_regn_m,
        cache=cache,
        avrinning_faktor=float(avrinning_faktor),
        brunnar_avdrag_m3_s=float(brunnar_avdrag_m3_s),
        spara_tidsserie=True,
        utdata_tider_s=utdata_tider_s,
        avtappning_m3_s=avtappning_per_nod_m3_s(graf, cache, avtappning_mm_h),
        balans_ut=balans_hist,
    )

    lov_niva_matris: dict[int, NDArray[np.float64]] = {
        lid: np.asarray(nod_niva_hist[lid], dtype=np.float64) for lid in cache.lov_noder
    }
    max_djup, tid_01, tid_02 = berakna_raster_och_ankomsttider(
        graf, tider_hist, lov_niva_matris, kardinal.lov_har_vatten
    )

    tider_tuple = tuple(tider_hist)
    noder_ut: dict[int, NodTidsserie] = {
        nid: NodTidsserie(
            nod_id=nid,
            tider_s=tider_tuple,
            niva_rh2000=tuple(nod_niva_hist[nid]),
            volym_m3=tuple(nod_volym_hist[nid]),
            t_overfall_s=kardinal.t_overfall_s[nid],
            t_sammanslagning_s=kardinal.t_sammanslagning_s[nid],
        )
        for nid in cache.sorterade_noder
    }

    a_mark, a_tak, a_led = regn_ytor_m2(graf)
    r_eff = netto_regn_m * float(avrinning_faktor)
    kortid = time.perf_counter() - t_wall_start
    return DynamikResultat(
        tider_s=tider_tuple,
        noder=noder_ut,
        max_djup_m=max_djup,
        tid_till_0_1m_s=tid_01,
        tid_till_0_2m_s=tid_02,
        overfallsordning=list(kardinal.overfallsordning),
        in_m3=kardinal.in_m3,
        lagrat_m3=kardinal.lagrat_m3,
        ut_m3=kardinal.ut_m3,
        volymfel_rel=kardinal.volymfel_rel,
        kortid_s=kortid,
        metod="handelsestyrd",
        avtappat_m3=kardinal.avtappat_m3,
        slutsankor=tuple(n.nod_id for n in graf.slutsankor()),
        regn_mark_m3=a_mark * r_eff,
        regn_tak_m3=a_tak * r_eff,
        takvatten=graf.takvatten is not None,
        takvatten_till_ledning_m3=a_led * r_eff,
        in_kum_m3_serie=tuple(balans_hist.in_kum_m3),
        lagrat_m3_serie=tuple(balans_hist.lagrat_m3),
        utflode_kum_m3_serie=tuple(balans_hist.utflode_kum_m3),
        avtappning_kum_m3_serie=tuple(balans_hist.avtappning_kum_m3),
    )
