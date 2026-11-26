"""Nodmodell, datatyper, överfallsformler och K6-export för sänkgrafdynamik (T03)."""

from __future__ import annotations

import csv
import importlib
import importlib.util
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from sankgraf.typer import HAV_NOD_ID, MARKSLAG_BYGGNAD, SankGraf, SankNod
from testsvit.k1_io import skriv_geotiff

KONTRAKT_K6 = "K6/v1"
STANDARD_CD = 1.7
STANDARD_OVERFALL_BREDD_M = 2.0
HAR_PYARROW: bool = importlib.util.find_spec("pyarrow") is not None


class DynamikFel(Exception):
    """Fel vid dynamisk simulering, enhetssvar, ensemblekörning eller K6-I/O."""


@dataclass(frozen=True)
class RegnIntervall:
    """Ett styckvis konstant eller linjärt regnintervall `[t_start_s, t_slut_s]`."""

    t_start_s: float
    t_slut_s: float
    intensitet_start_mm_h: float
    intensitet_slut_mm_h: float

    @property
    def dt_s(self) -> float:
        """Intervallets längd i sekunder."""
        return self.t_slut_s - self.t_start_s

    @property
    def medel_intensitet_mm_h(self) -> float:
        """Medelintensitet i mm/h över intervallet."""
        return 0.5 * (self.intensitet_start_mm_h + self.intensitet_slut_mm_h)

    @property
    def intensitet_start_m_s(self) -> float:
        """Startintensitet i m/s."""
        return self.intensitet_start_mm_h / 1000.0 / 3600.0

    @property
    def intensitet_slut_m_s(self) -> float:
        """Slutintensitet i m/s."""
        return self.intensitet_slut_mm_h / 1000.0 / 3600.0

    @property
    def regn_m(self) -> float:
        """Total regnmängd i meter under intervallet (trapetsregel)."""
        return (self.medel_intensitet_mm_h / 1000.0 / 3600.0) * self.dt_s

    @property
    def ar_konstant(self) -> bool:
        """True om intensiteten är konstant inom intervallet."""
        return abs(self.intensitet_slut_mm_h - self.intensitet_start_mm_h) <= 1e-12

    def intensitet_vid_tid_m_s(self, t_s: float) -> float:
        """Returnerar linjärt interpolerad regnintensitet (m/s) vid tid `t_s`."""
        dt = self.dt_s
        if dt <= 0.0:
            return self.intensitet_start_m_s
        andel = min(1.0, max(0.0, (float(t_s) - self.t_start_s) / dt))
        i_mm_h = self.intensitet_start_mm_h + andel * (
            self.intensitet_slut_mm_h - self.intensitet_start_mm_h
        )
        return i_mm_h / 1000.0 / 3600.0


@dataclass(frozen=True)
class NodTidsserie:
    """Dynamisk tidsserie för en enskild nod i sänkgrafen enligt K6/v1."""

    nod_id: int
    tider_s: tuple[float, ...]
    niva_rh2000: tuple[float, ...]
    volym_m3: tuple[float, ...]
    t_overfall_s: float | None
    t_sammanslagning_s: float | None


@dataclass
class DynamikResultat:
    """Samlat resultat från en dynamisk körning på sänkgraf enligt K6/v1."""

    tider_s: tuple[float, ...]
    noder: dict[int, NodTidsserie]
    max_djup_m: NDArray[np.float32]
    tid_till_0_1m_s: NDArray[np.float32]
    tid_till_0_2m_s: NDArray[np.float32]
    overfallsordning: list[int]
    in_m3: float
    lagrat_m3: float
    ut_m3: float
    volymfel_rel: float
    kortid_s: float
    metod: str
    avtappat_m3: float = 0.0
    """Del av `ut_m3` som lämnat via brunnar eller avtappning (fas 3), inte över ytan."""
    slutsankor: tuple[int, ...] = ()
    """Slutsänkornas nod-id (K2/v2); de som svämmat över har nått sitt hinder."""
    regn_mark_m3: float = 0.0
    regn_tak_m3: float = 0.0
    takvatten: bool = False
    takvatten_till_ledning_m3: float = 0.0
    in_kum_m3_serie: tuple[float, ...] = ()
    """Kumulativt inflöde (m³) per tidpunkt i `tider_s` (mark + effektivt takvatten)."""
    lagrat_m3_serie: tuple[float, ...] = ()
    """Total lagrad volym i sänkgrafen (m³) per tidpunkt i `tider_s`."""
    utflode_kum_m3_serie: tuple[float, ...] = ()
    """Kumulativt verkligt ytutflöde (m³) per tidpunkt i `tider_s` (exkl. ledningsavtappning)."""
    avtappning_kum_m3_serie: tuple[float, ...] = ()
    """Kumulativ ledningsavtappning (m³) per tidpunkt i `tider_s`."""

    def volymbalans(self) -> dict[str, float | bool]:
        """Regn på mark + tak mot lagrat, utflöde, avtappning och tak till ledning
        (`sankgraf.typer.volymbalans_dict`; `ut_m3` innehåller avtappningen)."""
        from sankgraf.typer import volymbalans_dict

        return volymbalans_dict(
            regn_mark_m3=self.regn_mark_m3,
            regn_tak_m3=self.regn_tak_m3,
            takvatten=self.takvatten,
            lagrat_m3=self.lagrat_m3,
            ut_m3=self.ut_m3 - self.avtappat_m3,
            infiltrerat_m3=0.0,
            avtappat_m3=self.avtappat_m3,
            tak_ledning_m3=self.takvatten_till_ledning_m3,
        )


def regn_ytor_m2(graf: SankGraf) -> tuple[float, float, float]:
    """`(markyta, takyta, takyta till ledning)` i m² för volymbalansen."""
    z64 = np.asarray(graf.mark_z, dtype=np.float64)
    cell = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)
    mark = float(np.count_nonzero((graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64))) * cell
    tak = float(np.count_nonzero(graf.markslag == MARKSLAG_BYGGNAD)) * cell
    tv = graf.takvatten
    ledning = float(tv.ledning_area_m2) if tv is not None and not tv.tom else 0.0
    return mark, tak, ledning


@dataclass(frozen=True)
class EntrePunkt:
    """En entrépunkt i SWEREF 99 18 00 (EPSG:3011) för utvärdering enligt K6."""

    entre_id: str
    e_m: float
    n_m: float
    gransdjup_m: float = 0.10


@dataclass(frozen=True)
class EntreResultat:
    """Utvärderat maxdjup och tid till gränsdjup för en entrépunkt (`entreer.csv`)."""

    entre_id: str
    max_djup_m: float
    tid_till_gransdjup_s: float | None


def overfallsflode_m3_s(
    h_m: float,
    h_overfall_m: float,
    bredd_m: float,
    cd: float = STANDARD_CD,
) -> float:
    """Bredskronsöverfall: `Q = Cd * B * max(0, h - h_overfall)^1.5` (m³/s)."""
    h_val = float(h_m)
    h_ov = float(h_overfall_m)
    b_val = float(bredd_m)
    cd_val = float(cd)
    if not (
        math.isfinite(h_val)
        and math.isfinite(h_ov)
        and math.isfinite(b_val)
        and math.isfinite(cd_val)
    ):
        raise DynamikFel("Överfallsberäkning fick icke-ändligt indatavärde.")
    if b_val < 0.0:
        raise DynamikFel(f"Överfallsbredd får inte vara negativ: {b_val}")
    if cd_val < 0.0:
        raise DynamikFel(f"Överfallskoefficient Cd får inte vara negativ: {cd_val}")

    dh = h_val - h_ov
    if dh <= 0.0 or b_val == 0.0 or cd_val == 0.0:
        return 0.0
    return cd_val * b_val * math.pow(dh, 1.5)


def skapa_regn_intervall(
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    slut_tid_s: float | None = None,
) -> tuple[list[RegnIntervall], float]:
    """Konverterar K4-regnserie `(tid_s, intensitet_mm_h)` till regnintervall och total regnmängd.

    Returnerar `(intervall, total_regn_m)` där varje intervall har positiv längd `dt_s > 0`
    och `total_regn_m` är integrerad nederbörd i meter (trapetsregel, konsistent med
    `sankgraf.fyllning.berakna_total_regn_m`). Om `slut_tid_s` överstiger sista regntiden
    läggs ett torrt efterförloppsintervall (`0.0 mm/h`) till.
    """
    if len(regn_tid_s) != len(regn_intensitet_mm_h):
        raise DynamikFel(
            f"regn_tid_s ({len(regn_tid_s)}) och regn_intensitet_mm_h "
            f"({len(regn_intensitet_mm_h)}) måste ha samma längd."
        )
    if len(regn_tid_s) < 2:
        raise DynamikFel("Regntidsserien måste innehålla minst två tidpunkter.")

    tider = [float(t) for t in regn_tid_s]
    intensiteter = [float(i) for i in regn_intensitet_mm_h]

    for idx, (t_val, i_val) in enumerate(zip(tider, intensiteter, strict=True)):
        if not math.isfinite(t_val) or not math.isfinite(i_val):
            raise DynamikFel(f"Icke-ändligt värde i regnserie vid index {idx}.")
        if i_val < 0.0:
            raise DynamikFel(f"Negativ regnintensitet ({i_val} mm/h) vid index {idx}.")
        if idx > 0 and t_val < tider[idx - 1]:
            raise DynamikFel(
                f"Regntidsserien är inte sorterad i tid: {t_val} < {tider[idx - 1]}."
            )

    t_start = tider[0]
    eff_slut = float(slut_tid_s) if slut_tid_s is not None else tider[-1]
    if not math.isfinite(eff_slut):
        raise DynamikFel(f"Ogiltig slut_tid_s: {eff_slut}")
    if eff_slut < t_start:
        raise DynamikFel(f"slut_tid_s ({eff_slut}) får inte vara före regnstart ({t_start}).")

    intervall: list[RegnIntervall] = []
    for idx in range(len(tider) - 1):
        t0, t1 = tider[idx], tider[idx + 1]
        i0, i1 = intensiteter[idx], intensiteter[idx + 1]
        if t1 <= t0:
            continue
        if t0 >= eff_slut:
            break
        if t1 > eff_slut:
            andel = (eff_slut - t0) / (t1 - t0)
            i_slut = i0 + andel * (i1 - i0)
            intervall.append(
                RegnIntervall(
                    t_start_s=t0,
                    t_slut_s=eff_slut,
                    intensitet_start_mm_h=i0,
                    intensitet_slut_mm_h=i_slut,
                )
            )
            break
        intervall.append(
            RegnIntervall(
                t_start_s=t0,
                t_slut_s=t1,
                intensitet_start_mm_h=i0,
                intensitet_slut_mm_h=i1,
            )
        )

    sista_t = intervall[-1].t_slut_s if intervall else t_start
    if eff_slut > sista_t:
        intervall.append(
            RegnIntervall(
                t_start_s=sista_t,
                t_slut_s=eff_slut,
                intensitet_start_mm_h=0.0,
                intensitet_slut_mm_h=0.0,
            )
        )

    total_regn_m = sum(iv.regn_m for iv in intervall)
    return intervall, total_regn_m


def applicera_initial_forlust(
    intervall: Sequence[RegnIntervall],
    initial_forlust_mm: float,
) -> tuple[list[RegnIntervall], float]:
    """Drar av initial förlust (mm) från början av regnserien och returnerar nettointervall."""
    loss_mm = float(initial_forlust_mm)
    if not math.isfinite(loss_mm) or loss_mm < 0.0:
        raise DynamikFel(f"initial_forlust_mm måste vara ändlig och >= 0, fick {loss_mm}.")
    if loss_mm == 0.0:
        netto = list(intervall)
        return netto, sum(iv.regn_m for iv in netto)

    kvar_m = loss_mm / 1000.0
    netto_iv: list[RegnIntervall] = []
    for iv in intervall:
        if kvar_m <= 1e-15:
            netto_iv.append(iv)
            continue
        iv_m = iv.regn_m
        if iv_m <= kvar_m + 1e-15:
            kvar_m = max(0.0, kvar_m - iv_m)
            netto_iv.append(
                RegnIntervall(
                    t_start_s=iv.t_start_s,
                    t_slut_s=iv.t_slut_s,
                    intensitet_start_mm_h=0.0,
                    intensitet_slut_mm_h=0.0,
                )
            )
            continue

        # Initialförlusten fylls mitt i detta intervall: lös R(u) = kvar_m exakt
        r0 = iv.intensitet_start_m_s
        r1 = iv.intensitet_slut_m_s
        dt = iv.dt_s
        lutn = (r1 - r0) / dt if dt > 0.0 else 0.0
        if abs(lutn) <= 1e-18:
            u_hit = kvar_m / r0 if r0 > 0.0 else dt
        else:
            diskr = max(0.0, r0 * r0 + 2.0 * lutn * kvar_m)
            u_hit = (-r0 + math.sqrt(diskr)) / lutn
        u_hit = min(dt, max(0.0, u_hit))
        t_bryt = iv.t_start_s + u_hit
        i_bryt_mm_h = iv.intensitet_start_mm_h + (u_hit / dt) * (
            iv.intensitet_slut_mm_h - iv.intensitet_start_mm_h
        )
        kvar_m = 0.0

        if t_bryt > iv.t_start_s:
            netto_iv.append(
                RegnIntervall(
                    t_start_s=iv.t_start_s,
                    t_slut_s=t_bryt,
                    intensitet_start_mm_h=0.0,
                    intensitet_slut_mm_h=0.0,
                )
            )
        if iv.t_slut_s > t_bryt:
            netto_iv.append(
                RegnIntervall(
                    t_start_s=t_bryt,
                    t_slut_s=iv.t_slut_s,
                    intensitet_start_mm_h=i_bryt_mm_h,
                    intensitet_slut_mm_h=iv.intensitet_slut_mm_h,
                )
            )

    return netto_iv, sum(iv.regn_m for iv in netto_iv)


def nod_medel_gangtid_s(graf: SankGraf) -> dict[int, float]:
    """Beräknar representativ medelgångtid (s) per lövnod från `graf.flodesvagar`."""
    summor: dict[int, float] = {}
    antal: dict[int, int] = {}
    for vag in graf.flodesvagar:
        if vag.nod_id in graf.noder and math.isfinite(vag.gangtid_s) and vag.gangtid_s >= 0.0:
            summor[vag.nod_id] = summor.get(vag.nod_id, 0.0) + float(vag.gangtid_s)
            antal[vag.nod_id] = antal.get(vag.nod_id, 0) + 1

    resultat: dict[int, float] = {}
    for nod in graf.lovnoder():
        n_vagar = antal.get(nod.nod_id, 0)
        resultat[nod.nod_id] = summor[nod.nod_id] / n_vagar if n_vagar > 0 else 0.0
    return resultat


def niva_inkl_overhojd(nod: SankNod, volym_m3: float, min_area_m2: float = 1e-6) -> float:
    """Returnerar vattennivå (RH 2000) för `volym_m3`, inklusive överhöjd över överfall."""
    v = float(volym_m3)
    if v <= 0.0:
        return nod.botten_rh2000
    v_max = nod.max_volym_m3
    if v <= v_max:
        return nod.niva_vid_volym(v)
    a_top = nod.ytkurva[-1].a_m2 if nod.ytkurva else 0.0
    eff_area = max(float(a_top), float(min_area_m2))
    return nod.overfall_rh2000 + (v - v_max) / eff_area


def djupaste_lov_i_graf(graf: SankGraf, nod_id: int) -> int:
    """Returnerar det djupaste lövet i delträdet under `nod_id`."""
    curr = nod_id
    while graf.noder[curr].barn_id:
        curr = min(
            graf.noder[curr].barn_id,
            key=lambda b: (graf.noder[b].botten_rh2000, b),
        )
    return curr


def aktiv_forfader(graf: SankGraf, mergad: Mapping[int, bool], nod_id: int) -> int:
    """Klättrar upp till den aktiva förfader som kontrollerar `nod_id`."""
    curr = nod_id
    while mergad[curr]:
        forald = graf.noder[curr].forald_id
        if forald is None:
            break
        curr = forald
    return curr


def hitta_mottagare(
    graf: SankGraf,
    aktiv: Mapping[int, bool],
    mergad: Mapping[int, bool],
    kalla_id: int,
) -> int | None:
    """Returnerar aktiv mottagarnod för överfall från `kalla_id`, eller `None` vid randutlopp."""
    raw_lov = graf.ut_lov_id.get(kalla_id)
    if raw_lov is not None and raw_lov in graf.noder:
        return aktiv_forfader(graf, mergad, raw_lov)
    ut_id = graf.noder[kalla_id].ut_till_id
    if ut_id is None or ut_id not in graf.noder:
        return None
    if aktiv[ut_id]:
        return ut_id
    if mergad[ut_id]:
        return aktiv_forfader(graf, mergad, ut_id)
    lov_mal = djupaste_lov_i_graf(graf, ut_id)
    return aktiv_forfader(graf, mergad, lov_mal)


def berakna_doman_areor(graf: SankGraf) -> tuple[float, float, dict[int, float]]:
    """Returnerar `(total_aktiv_area_m2, hav_area_m2, lov_areor_m2)` för en `SankGraf`."""
    z64 = np.asarray(graf.mark_z, dtype=np.float64)
    aktiv = np.asarray((graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64), dtype=np.bool_)
    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)
    total_aktiv_area = float(np.sum(aktiv)) * cell_area_m2

    lov_areor: dict[int, float] = {
        nod.nod_id: float(nod.avrinningsyta_m2) for nod in graf.lovnoder()
    }
    ct = graf.cell_till_nod
    hav_area = float(np.sum(aktiv & (ct == HAV_NOD_ID))) * cell_area_m2

    # Skydda mot ev. handbyggda syntetiska grafer där cell_till_nod inte matchar lov_areor
    lov_summa = sum(lov_areor.values())
    if lov_summa > 0.0 and abs(total_aktiv_area - hav_area - lov_summa) > 1e-6:
        hav_area = max(0.0, total_aktiv_area - lov_summa)
        total_aktiv_area = lov_summa + hav_area

    # Takvatten (`sankgraf.takvatten`): takytan som avvattnas till varje markcell läggs på
    # cellens nod (eller på randutloppet), takyta med fasad utanför rutan på randutloppet.
    tv = graf.takvatten
    if tv is not None and not tv.tom:
        tv.kontrollera_mot(graf.markslag, graf.mark_z)
        extra = tv.extra_area_m2
        har = extra > 0.0
        if np.any(har & ~aktiv):
            raise DynamikFel("Takvatten till en cell som inte är aktiv mark.")
        ct_har = ct[har]
        ex_har = extra[har]
        okanda = (ct_har < 0) & (ct_har != HAV_NOD_ID)
        if np.any(okanda):
            raise DynamikFel("Takvatten till celler utan nod i sänkgrafen.")
        hav_extra = float(np.sum(ex_har[ct_har == HAV_NOD_ID])) + tv.ut_area_m2
        till_nod = ct_har >= 0
        if np.any(till_nod):
            per = np.bincount(ct_har[till_nod], weights=ex_har[till_nod])
            for nid in np.flatnonzero(per > 0.0):
                n = int(nid)
                if n not in lov_areor:
                    hav_extra += float(per[n])
                else:
                    lov_areor[n] += float(per[n])
        hav_area += hav_extra
        total_aktiv_area += float(np.sum(ex_har)) + tv.ut_area_m2
    return total_aktiv_area, hav_area, lov_areor


def berakna_raster_och_ankomsttider(
    graf: SankGraf,
    tider_s: Sequence[float],
    lov_niva_historik: Mapping[int, NDArray[np.float64]],
    lov_har_vatten: Mapping[int, bool],
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.float32]]:
    """Beräknar `max_djup_m`, `tid_till_0_1m_s` och `tid_till_0_2m_s` från lövnodernas serier."""
    hojd, bredd = graf.mark_z.shape
    max_djup = np.zeros((hojd, bredd), dtype=np.float32)
    tid_01 = np.full((hojd, bredd), np.nan, dtype=np.float32)
    tid_02 = np.full((hojd, bredd), np.nan, dtype=np.float32)

    if not graf.noder or not tider_s:
        return max_djup, tid_01, tid_02

    z64 = np.asarray(graf.mark_z, dtype=np.float64)
    aktiv = np.asarray((graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64), dtype=np.bool_)
    ct = graf.cell_till_nod
    t_arr = np.asarray(tider_s, dtype=np.float64)

    for lov in graf.lovnoder():
        lid = lov.nod_id
        if not lov_har_vatten.get(lid, False):
            continue
        w_serie = lov_niva_historik.get(lid)
        if w_serie is None or w_serie.size == 0:
            continue

        mask = aktiv & (ct == lid)
        if not np.any(mask):
            continue

        z_celler = z64[mask]
        w_max = float(np.max(w_serie))
        djup_celler = np.maximum(0.0, w_max - z_celler)
        max_djup[mask] = np.asarray(djup_celler, dtype=np.float32)

        w_mono = np.maximum.accumulate(w_serie)
        for grans_m, mal_arr in ((0.10, tid_01), (0.20, tid_02)):
            z_mal = z_celler + grans_m
            nar = z_mal <= w_mono[-1]
            if not np.any(nar):
                continue
            t_celler = np.full(z_celler.shape, np.nan, dtype=np.float64)
            z_nar = z_mal[nar]
            idxs = np.searchsorted(w_mono, z_nar, side="left")
            t_nar = np.empty(z_nar.shape, dtype=np.float64)

            direkt = idxs == 0
            t_nar[direkt] = t_arr[0]
            senare = ~direkt
            if np.any(senare):
                i1 = idxs[senare]
                i0 = i1 - 1
                w0 = w_mono[i0]
                w1 = w_mono[i1]
                dw = w1 - w0
                t0 = t_arr[i0]
                t1 = t_arr[i1]
                andel = np.where(dw > 0.0, (z_nar[senare] - w0) / np.where(dw > 0.0, dw, 1.0), 0.0)
                t_nar[senare] = t0 + np.clip(andel, 0.0, 1.0) * (t1 - t0)

            t_celler[nar] = t_nar
            mal_arr[mask] = np.asarray(t_celler, dtype=np.float32)

    return max_djup, tid_01, tid_02


def _tid_till_niva(
    tider_s: Sequence[float],
    nivaer: Sequence[float],
    mal_niva: float,
) -> float | None:
    """Linjärinterpolerar första tidpunkten då `nivaer` når `mal_niva`."""
    if not tider_s or not nivaer:
        return None
    if nivaer[0] >= mal_niva:
        return float(tider_s[0])
    for idx in range(1, len(nivaer)):
        w0 = float(nivaer[idx - 1])
        w1 = float(nivaer[idx])
        if w1 >= mal_niva:
            dw = w1 - w0
            if dw <= 0.0:
                return float(tider_s[idx])
            andel = min(1.0, max(0.0, (mal_niva - w0) / dw))
            return float(tider_s[idx - 1]) + andel * (float(tider_s[idx]) - float(tider_s[idx - 1]))
    return None


def utvardera_entreer(
    resultat: DynamikResultat,
    graf: SankGraf,
    entreer: Sequence[EntrePunkt],
) -> list[EntreResultat]:
    """Utvärderar `max_djup_m` och `tid_till_gransdjup_s` för en lista entrépunkter."""
    geo = graf.geo
    ut: list[EntreResultat] = []
    for entre in entreer:
        if not (
            math.isfinite(entre.e_m)
            and math.isfinite(entre.n_m)
            and math.isfinite(entre.gransdjup_m)
        ):
            raise DynamikFel(f"Entré '{entre.entre_id}' har icke-ändliga koordinater/gränsdjup.")
        if entre.gransdjup_m <= 0.0:
            raise DynamikFel(f"Entré '{entre.entre_id}' måste ha gransdjup_m > 0.")

        kol = math.floor((entre.e_m - geo.x_min) / geo.pixelstorlek_x)
        rad = math.floor((geo.y_max - entre.n_m) / geo.pixelstorlek_y)
        if not (0 <= rad < geo.hojd and 0 <= kol < geo.bredd):
            raise DynamikFel(
                f"Entré '{entre.entre_id}' ({entre.e_m}, {entre.n_m}) ligger utanför rutnätet."
            )

        max_djup = float(resultat.max_djup_m[rad, kol])
        tid_grans: float | None
        if abs(entre.gransdjup_m - 0.10) <= 1e-9:
            raw_t = float(resultat.tid_till_0_1m_s[rad, kol])
            tid_grans = raw_t if math.isfinite(raw_t) else None
        elif abs(entre.gransdjup_m - 0.20) <= 1e-9:
            raw_t = float(resultat.tid_till_0_2m_s[rad, kol])
            tid_grans = raw_t if math.isfinite(raw_t) else None
        else:
            nod_id = int(graf.cell_till_nod[rad, kol])
            if nod_id in resultat.noder and max_djup >= entre.gransdjup_m:
                serie = resultat.noder[nod_id]
                mal_z = float(graf.mark_z[rad, kol]) + float(entre.gransdjup_m)
                tid_grans = _tid_till_niva(serie.tider_s, serie.niva_rh2000, mal_z)
            else:
                tid_grans = None

        ut.append(
            EntreResultat(
                entre_id=entre.entre_id,
                max_djup_m=max_djup,
                tid_till_gransdjup_s=tid_grans,
            )
        )
    return ut


def _skriv_noder_parquet(resultat: DynamikResultat, sokvag: Path) -> Path:
    """Skriver `noder.parquet` enligt K6/v1 (`nod_id, t_s, niva_rh2000, volym_m3`)."""
    pa: Any = importlib.import_module("pyarrow")
    pq: Any = importlib.import_module("pyarrow.parquet")

    schema = pa.schema(
        [
            ("nod_id", pa.int64()),
            ("t_s", pa.float64()),
            ("niva_rh2000", pa.float64()),
            ("volym_m3", pa.float64()),
        ]
    )

    nod_ids: list[int] = []
    t_s_lista: list[float] = []
    niva_lista: list[float] = []
    volym_lista: list[float] = []
    for nid in sorted(resultat.noder):
        serie = resultat.noder[nid]
        for t_val, niva_val, vol_val in zip(
            serie.tider_s,
            serie.niva_rh2000,
            serie.volym_m3,
            strict=True,
        ):
            nod_ids.append(int(nid))
            t_s_lista.append(float(t_val))
            niva_lista.append(float(niva_val))
            volym_lista.append(float(vol_val))

    tabell = pa.table(
        {
            "nod_id": nod_ids,
            "t_s": t_s_lista,
            "niva_rh2000": niva_lista,
            "volym_m3": volym_lista,
        },
        schema=schema,
    )
    sokvag.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tabell, str(sokvag))
    return sokvag


def skriv_dynamik_resultat_k6(
    resultat: DynamikResultat,
    graf: SankGraf,
    ut_mapp: str | Path,
    *,
    entreer: Sequence[EntrePunkt] | None = None,
    indata: Sequence[FilPost] | None = None,
    parametrar: Mapping[str, Any] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> dict[str, Path]:
    """Skriver dynamiskt resultat enligt kontrakt K6/v1 + K0/v1 (`run.json`)."""
    mal = Path(ut_mapp)
    mal.mkdir(parents=True, exist_ok=True)
    geo = graf.geo

    filer: dict[str, Path] = {}
    utdata_poster: list[FilPost] = []

    for roll, filnamn, arr in (
        ("max_djup", "max_djup.tif", resultat.max_djup_m),
        ("tid_till_0_1m", "tid_till_0_1m.tif", resultat.tid_till_0_1m_s),
        ("tid_till_0_2m", "tid_till_0_2m.tif", resultat.tid_till_0_2m_s),
    ):
        fil = skriv_geotiff(
            mal / filnamn,
            np.asarray(arr, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
            epsg=geo.epsg,
        )
        filer[roll] = fil
        utdata_poster.append(FilPost(roll=roll, fil=fil.name, sha256=sha256_fil(fil)))

    if HAR_PYARROW:
        parquet_fil = _skriv_noder_parquet(resultat, mal / "noder.parquet")
        filer["noder_parquet"] = parquet_fil
        utdata_poster.append(
            FilPost(roll="noder_parquet", fil=parquet_fil.name, sha256=sha256_fil(parquet_fil))
        )

    noder_json_fil = mal / "noder.json"
    noder_payload = {
        "kontrakt": KONTRAKT_K6,
        "metod": resultat.metod,
        "overfallsordning": [int(nid) for nid in resultat.overfallsordning],
        "noder": [
            {
                "nod_id": int(nid),
                "t_s": [float(t) for t in resultat.noder[nid].tider_s],
                "niva_rh2000": [float(w) for w in resultat.noder[nid].niva_rh2000],
                "volym_m3": [float(v) for v in resultat.noder[nid].volym_m3],
                "t_overfall_s": resultat.noder[nid].t_overfall_s,
                "t_sammanslagning_s": resultat.noder[nid].t_sammanslagning_s,
            }
            for nid in sorted(resultat.noder)
        ],
    }
    noder_json_fil.write_text(
        json.dumps(noder_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    filer["noder_json"] = noder_json_fil
    utdata_poster.append(
        FilPost(roll="noder_json", fil=noder_json_fil.name, sha256=sha256_fil(noder_json_fil))
    )

    entre_resultat = utvardera_entreer(resultat, graf, entreer) if entreer else []
    entre_fil = mal / "entreer.csv"
    with entre_fil.open("w", encoding="utf-8", newline="") as fh:
        skrivare = csv.writer(fh)
        skrivare.writerow(["entre_id", "max_djup_m", "tid_till_gransdjup_s"])
        for er in entre_resultat:
            tid_str = (
                f"{er.tid_till_gransdjup_s:.6f}" if er.tid_till_gransdjup_s is not None else ""
            )
            skrivare.writerow([er.entre_id, f"{er.max_djup_m:.6f}", tid_str])
    filer["entreer"] = entre_fil
    utdata_poster.append(
        FilPost(roll="entreer", fil=entre_fil.name, sha256=sha256_fil(entre_fil))
    )

    param_dict: dict[str, Any] = {
        "kontrakt_ut": KONTRAKT_K6,
        "niva": "graf",
        "metod": resultat.metod,
        "epsg": geo.epsg,
    }
    if parametrar is not None:
        param_dict.update(parametrar)

    max_djup_val = float(np.max(resultat.max_djup_m)) if resultat.max_djup_m.size > 0 else 0.0
    manifest = skapa_manifest(
        modul="dynamik",
        indata=list(indata) if indata is not None else [],
        parametrar=param_dict,
        utdata=utdata_poster,
        matvarden={
            "in_m3": float(resultat.in_m3),
            "lagrat_m3": float(resultat.lagrat_m3),
            "ut_m3": float(resultat.ut_m3),
            "volymfel_rel": float(resultat.volymfel_rel),
            "kortid_s": float(resultat.kortid_s),
            "max_djup_m": max_djup_val,
            "antal_noder": len(resultat.noder),
            "overfallsordning": [int(nid) for nid in resultat.overfallsordning],
        },
        kodversion=kodversion,
        start=start,
    )
    run_fil = skriv_manifest(manifest, mal / "run.json")
    filer["run"] = run_fil
    return filer
