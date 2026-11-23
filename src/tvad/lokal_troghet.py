"""Vektoriserad 2D-lösare med lokal tröghetsformulering (Bates m.fl. 2010 / SFINCS) (T05).

Implementerar:
- Bates m.fl. (2010) lokala tröghetsekvation med stabilt CFL-tidssteg och
  massbevarande flödesbegränsare (garanterar h >= 0 och volymfel << 0,1 %).
- Ogenomträngliga byggnader (`markslag == 9`).
- Valfri gles aktiv-front-beräkning (endast våta celler + närmaste frontceller).
- Valfri subgrid-aggregering (`subgrid_faktor > 1`) via `tvad.subgrid`.
- K6/v1 + K0/v1 export (`max_djup.tif`, `max_hastighet.tif`, `fara.tif`,
  `tid_till_0_1m.tif`, `tid_till_0_2m.tif`, `run.json`).
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from sankgraf.flodesvagar import MANNING_N_PER_MARKSLAG
from sankgraf.hierarki import _berakna_kantbarriarer
from sankgraf.takvatten import TakvattenFordelning
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, skriv_geotiff
from tvad.kalltermer import Kalltermer, SanktermTillstand
from tvad.subgrid import (
    SubgridTabell,
    area_fran_vattenniva,
    bygg_subgrid_tabell,
    kant_flode_geometri_u,
    kant_flode_geometri_v,
    projicera_djup_till_fin,
    projicera_falt_till_fin,
    vattenniva_fran_volym,
)

GRAVITATION_M_S2 = 9.81
KONTRAKT_K6 = "K6/v1"
FROUDE_MAX = 2.0
_MIN_RAND_LUTNING = 1e-3
_DT_MIN_S = 1e-4


class TvadFel(Exception):
    """Fel i 2D-lösaren, SFINCS-översättaren eller hybridmodellen."""


@dataclass
class TvadResultat:
    """K6-kompatibelt resultat från 2D- eller hybridkörning."""

    max_djup_m: NDArray[np.float32]
    max_hastighet_m_s: NDArray[np.float32]
    fara_m2_s: NDArray[np.float32]
    tid_till_0_1m_s: NDArray[np.float32]
    tid_till_0_2m_s: NDArray[np.float32]
    slut_djup_m: NDArray[np.float32]
    in_m3: float
    lagrat_m3: float
    ut_m3: float
    volymfel_rel: float
    antal_tidssteg: int
    kortid_s: float
    niva: str
    aktiv_cell_andel: float
    avtappat_m3: float = 0.0
    infiltrerat_m3: float = 0.0
    initial_forlust_m3: float = 0.0
    infiltration_m: NDArray[np.float32] | None = None
    """Kumulativ infiltration per cell (lösarens rutnät) när infiltration är påslagen."""
    sanktermer: dict[str, Any] | None = None
    """Vilka käll-/sänktermer som användes (`Kalltermer.beskrivning`); None = inga."""
    regn_mark_m3: float = 0.0
    regn_tak_m3: float = 0.0
    takvatten: bool = False
    takvatten_ut_rand_m3: float = 0.0
    takvatten_till_ledning_m3: float = 0.0
    courant_hast_max: float = 0.0
    """Diagnostik (Python-lösaren): största u_max · dt / dx över alla tidssteg, med den u_max
    (m/s) som tidssteget valdes med. CFL-steget garanterar att värdet är högst `cfl`.
    0,0 = inte mätt (Rust-kärnan, SFINCS-import, hybrid)."""

    def volymbalans(self) -> dict[str, float | bool]:
        """Regn på hela rutan (mark + tak) mot lagrat, utflöde, infiltration, avtappning och
        takvatten till ledningsnät (se `sankgraf.typer.volymbalans_dict`). Initial förlust
        räknas som infiltration. Tvingat djup (randvillkor) ingår inte."""
        from sankgraf.typer import volymbalans_dict

        return volymbalans_dict(
            regn_mark_m3=self.regn_mark_m3,
            regn_tak_m3=self.regn_tak_m3,
            takvatten=self.takvatten,
            lagrat_m3=self.lagrat_m3,
            ut_m3=self.ut_m3,
            infiltrerat_m3=self.infiltrerat_m3 + self.initial_forlust_m3,
            avtappat_m3=self.avtappat_m3,
            tak_ledning_m3=self.takvatten_till_ledning_m3,
        )


def takvatten_regnyta(
    takvatten: TakvattenFordelning | None,
    markslag: NDArray[np.uint8],
    mark_z: NDArray[np.float32],
    cell_area_fin_m2: float,
    subgrid_faktor: int,
    form_losare: tuple[int, int],
) -> tuple[NDArray[np.float64], float, float, float]:
    """Takyta per lösarcell som regnet läggs på utöver cellens egen yta (`sankgraf.takvatten`).

    Returnerar `(extra_m2, ut_ur_rutan_m2, till_ledning_m2, takyta_m2)`; med subgrid summeras de
    fina cellerna blockvis (samma utfyllnad som `tvad.subgrid`).
    """
    tak_m2 = float(np.count_nonzero(markslag == MARKSLAG_BYGGNAD)) * cell_area_fin_m2
    if takvatten is None:
        return np.zeros(form_losare, dtype=np.float64), 0.0, 0.0, tak_m2
    takvatten.kontrollera_mot(markslag, mark_z)
    extra = takvatten.extra_area_m2
    if subgrid_faktor > 1:
        f = int(subgrid_faktor)
        hg, bg = form_losare
        pad = np.zeros((hg * f, bg * f), dtype=np.float64)
        pad[: extra.shape[0], : extra.shape[1]] = extra
        extra = pad.reshape(hg, f, bg, f).sum(axis=(1, 3))
    return (
        np.asarray(extra, dtype=np.float64),
        float(takvatten.ut_area_m2),
        float(takvatten.ledning_area_m2),
        tak_m2,
    )


@dataclass(frozen=True)
class RandUtflodeKonfig:
    """Förberäknade öppna utflödesmasker och lutningar längs domänens fyra ytterkanter."""

    vast_mask: NDArray[np.bool_]
    vast_lutning: NDArray[np.float64]
    vast_bredd_andel: NDArray[np.float64]
    ost_mask: NDArray[np.bool_]
    ost_lutning: NDArray[np.float64]
    ost_bredd_andel: NDArray[np.float64]
    norr_mask: NDArray[np.bool_]
    norr_lutning: NDArray[np.float64]
    norr_bredd_andel: NDArray[np.float64]
    soder_mask: NDArray[np.bool_]
    soder_lutning: NDArray[np.float64]
    soder_bredd_andel: NDArray[np.float64]


def validera_regnserie(
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    slut_tid_s: float | None = None,
) -> tuple[list[float], list[float], float, float]:
    """Validerar K4-punktregn och returnerar `(tider_s, intensiteter_m_s, t_start, t_slut)`."""
    if len(regn_tid_s) != len(regn_intensitet_mm_h):
        raise TvadFel(
            f"regn_tid_s ({len(regn_tid_s)}) och regn_intensitet_mm_h "
            f"({len(regn_intensitet_mm_h)}) måste ha samma längd."
        )
    if len(regn_tid_s) < 2:
        raise TvadFel("Regntidsserien måste innehålla minst två tidpunkter.")

    tider: list[float] = []
    intens_m_s: list[float] = []
    for idx, (t_raw, i_raw) in enumerate(zip(regn_tid_s, regn_intensitet_mm_h, strict=True)):
        t_val = float(t_raw)
        i_val = float(i_raw)
        if not math.isfinite(t_val) or not math.isfinite(i_val):
            raise TvadFel(f"Icke-ändligt värde i regnserie vid index {idx}.")
        if i_val < 0.0:
            raise TvadFel(f"Negativ regnintensitet ({i_val} mm/h) vid index {idx}.")
        if idx > 0 and t_val < tider[-1]:
            raise TvadFel(f"Regntidsserien är inte sorterad: {t_val} < {tider[-1]}.")
        tider.append(t_val)
        intens_m_s.append(i_val / 1000.0 / 3600.0)

    t_start = tider[0]
    t_slut = float(slut_tid_s) if slut_tid_s is not None else tider[-1]
    if not math.isfinite(t_slut) or t_slut < t_start:
        raise TvadFel(f"Ogiltig slut_tid_s ({t_slut}) för regnstart ({t_start}).")
    return tider, intens_m_s, t_start, t_slut


def integrera_regn_m(
    t0: float,
    t1: float,
    tider: Sequence[float],
    intens_m_s: Sequence[float],
) -> float:
    """Exakt trapetsintegration av styckvis linjär regnserie på `[t0, t1]` (meter)."""
    if t1 <= t0:
        return 0.0
    regn_m = 0.0
    for idx in range(len(tider) - 1):
        a = tider[idx]
        b = tider[idx + 1]
        if b <= a or b <= t0 or a >= t1:
            continue
        lo = max(t0, a)
        hi = min(t1, b)
        dt = hi - lo
        if dt <= 0.0:
            continue
        ia = intens_m_s[idx]
        ib = intens_m_s[idx + 1]
        span = b - a
        i_lo = ia + ((lo - a) / span) * (ib - ia)
        i_hi = ia + ((hi - a) / span) * (ib - ia)
        regn_m += 0.5 * (i_lo + i_hi) * dt
    return regn_m


def nasta_regnbrytpunkt(t_nu: float, tider: Sequence[float], t_slut: float) -> float:
    """Returnerar nästa tidpunkt i regnserien som är strikt större än `t_nu` (högst `t_slut`)."""
    for t_val in tider:
        if t_val > t_nu + 1e-12:
            return min(t_val, t_slut)
    return t_slut


def bygg_manning_raster(
    markslag: NDArray[np.uint8],
    manning_tabell: Mapping[int, float] | None = None,
) -> NDArray[np.float64]:
    """Översätter K1-markslagsraster till Manning-n-raster (float64)."""
    tabell = manning_tabell if manning_tabell is not None else MANNING_N_PER_MARKSLAG
    standard_n = float(tabell.get(0, 0.030))
    if not math.isfinite(standard_n) or standard_n <= 0.0:
        raise TvadFel(f"Ogiltigt standard-Manning-n: {standard_n}")
    uppslag = np.full(256, standard_n, dtype=np.float64)
    for kod, n_val in tabell.items():
        if 0 <= int(kod) < 256:
            fval = float(n_val)
            if not math.isfinite(fval) or fval <= 0.0:
                raise TvadFel(f"Ogiltigt Manning-n ({fval}) för markslag {kod}.")
            uppslag[int(kod)] = fval
    return np.asarray(uppslag[markslag], dtype=np.float64)


def _har_lagre_granne(z_bed: NDArray[np.float64], aktiv: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """Returnerar True för varje aktiv cell som har minst en strikt lägre aktiv 4-granne."""
    hojd, bredd = z_bed.shape
    lagre = np.zeros((hojd, bredd), dtype=np.bool_)
    if hojd > 1:
        lagre[1:, :] |= aktiv[:-1, :] & (z_bed[:-1, :] < z_bed[1:, :])
        lagre[:-1, :] |= aktiv[1:, :] & (z_bed[1:, :] < z_bed[:-1, :])
    if bredd > 1:
        lagre[:, 1:] |= aktiv[:, :-1] & (z_bed[:, :-1] < z_bed[:, 1:])
        lagre[:, :-1] |= aktiv[:, 1:] & (z_bed[:, 1:] < z_bed[:, :-1])
    return lagre


def berakna_rand_utflode_konfig(
    z_bed: NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    dx: float,
    dy: float,
    *,
    oppna_rander: bool,
    subgrid: SubgridTabell | None = None,
) -> RandUtflodeKonfig:
    """Identifierar öppna utflödesceller längs domänens fyra ytterkanter.

    Inåtlutande vattendelare (där ytterkanten ligger högre än inre grannen)
    behandlas som naturliga vattendelare utan bakåtläckage, medan utåtlutande
    kanter och plana utloppskanter utan lägre inre granne släpper ut vatten.
    """
    hojd, bredd = z_bed.shape
    vast_mask = np.zeros(hojd, dtype=np.bool_)
    vast_lut = np.full(hojd, _MIN_RAND_LUTNING, dtype=np.float64)
    vast_bredd = np.ones(hojd, dtype=np.float64)
    ost_mask = np.zeros(hojd, dtype=np.bool_)
    ost_lut = np.full(hojd, _MIN_RAND_LUTNING, dtype=np.float64)
    ost_bredd = np.ones(hojd, dtype=np.float64)
    norr_mask = np.zeros(bredd, dtype=np.bool_)
    norr_lut = np.full(bredd, _MIN_RAND_LUTNING, dtype=np.float64)
    norr_bredd = np.ones(bredd, dtype=np.float64)
    soder_mask = np.zeros(bredd, dtype=np.bool_)
    soder_lut = np.full(bredd, _MIN_RAND_LUTNING, dtype=np.float64)
    soder_bredd = np.ones(bredd, dtype=np.float64)

    if not oppna_rander:
        return RandUtflodeKonfig(
            vast_mask=vast_mask,
            vast_lutning=vast_lut,
            vast_bredd_andel=vast_bredd,
            ost_mask=ost_mask,
            ost_lutning=ost_lut,
            ost_bredd_andel=ost_bredd,
            norr_mask=norr_mask,
            norr_lutning=norr_lut,
            norr_bredd_andel=norr_bredd,
            soder_mask=soder_mask,
            soder_lutning=soder_lut,
            soder_bredd_andel=soder_bredd,
        )

    har_lagre = _har_lagre_granne(z_bed, aktiv)

    # Väst (kol 0) och öst (kol -1)
    if bredd == 1:
        vast_mask = aktiv[:, 0].copy()
        ost_mask = aktiv[:, 0].copy()
    else:
        dz_v = z_bed[:, 1] - z_bed[:, 0]
        v_in_akt = aktiv[:, 1]
        vast_mask = aktiv[:, 0] & (
            (~v_in_akt) | (dz_v > 0.0) | ((dz_v == 0.0) & (~har_lagre[:, 0]))
        )
        vast_lut = np.where(v_in_akt & (dz_v > 0.0), dz_v / dx, _MIN_RAND_LUTNING)

        dz_o = z_bed[:, -2] - z_bed[:, -1]
        o_in_akt = aktiv[:, -2]
        ost_mask = aktiv[:, -1] & (
            (~o_in_akt) | (dz_o > 0.0) | ((dz_o == 0.0) & (~har_lagre[:, -1]))
        )
        ost_lut = np.where(o_in_akt & (dz_o > 0.0), dz_o / dx, _MIN_RAND_LUTNING)

    # Norr (rad 0) och söder (rad -1)
    if hojd == 1:
        norr_mask = aktiv[0, :].copy()
        soder_mask = aktiv[0, :].copy()
    else:
        dz_n = z_bed[1, :] - z_bed[0, :]
        n_in_akt = aktiv[1, :]
        norr_mask = aktiv[0, :] & (
            (~n_in_akt) | (dz_n > 0.0) | ((dz_n == 0.0) & (~har_lagre[0, :]))
        )
        norr_lut = np.where(n_in_akt & (dz_n > 0.0), dz_n / dy, _MIN_RAND_LUTNING)

        dz_s = z_bed[-2, :] - z_bed[-1, :]
        s_in_akt = aktiv[-2, :]
        soder_mask = aktiv[-1, :] & (
            (~s_in_akt) | (dz_s > 0.0) | ((dz_s == 0.0) & (~har_lagre[-1, :]))
        )
        soder_lut = np.where(s_in_akt & (dz_s > 0.0), dz_s / dy, _MIN_RAND_LUTNING)

    if subgrid is not None and subgrid.faktor > 1:
        f = subgrid.faktor
        hf, bf = subgrid.mark_z_fin.shape
        akt_fin = (subgrid.markslag_fin != MARKSLAG_BYGGNAD) & np.isfinite(subgrid.mark_z_fin)
        for rg in range(hojd):
            r0 = rg * f
            r1 = min(hf, r0 + f)
            vast_bredd[rg] = float(np.count_nonzero(akt_fin[r0:r1, 0])) / float(f)
            ost_bredd[rg] = float(np.count_nonzero(akt_fin[r0:r1, bf - 1])) / float(f)
        for cg in range(bredd):
            c0 = cg * f
            c1 = min(bf, c0 + f)
            norr_bredd[cg] = float(np.count_nonzero(akt_fin[0, c0:c1])) / float(f)
            soder_bredd[cg] = float(np.count_nonzero(akt_fin[hf - 1, c0:c1])) / float(f)
        vast_mask &= vast_bredd > 0.0
        ost_mask &= ost_bredd > 0.0
        norr_mask &= norr_bredd > 0.0
        soder_mask &= soder_bredd > 0.0

    return RandUtflodeKonfig(
        vast_mask=vast_mask,
        vast_lutning=vast_lut,
        vast_bredd_andel=vast_bredd,
        ost_mask=ost_mask,
        ost_lutning=ost_lut,
        ost_bredd_andel=ost_bredd,
        norr_mask=norr_mask,
        norr_lutning=norr_lut,
        norr_bredd_andel=norr_bredd,
        soder_mask=soder_mask,
        soder_lutning=soder_lut,
        soder_bredd_andel=soder_bredd,
    )


def utjamna_flode_x(
    qx: NDArray[np.float64],
    u_giltig: NDArray[np.bool_],
    theta: float,
) -> NDArray[np.float64]:
    """de Almeida m.fl. (2012) / SFINCS riktad theta-viktning längs x-kanter."""
    if theta >= 1.0 or qx.shape[1] < 2:
        return qx
    w = 0.5 * (1.0 - theta)
    q_vanster = np.empty_like(qx)
    q_vanster[:, 0] = qx[:, 0]
    q_vanster[:, 1:] = np.where(u_giltig[:, :-1], qx[:, :-1], qx[:, 1:])

    q_hoger = np.empty_like(qx)
    q_hoger[:, -1] = qx[:, -1]
    q_hoger[:, :-1] = np.where(u_giltig[:, 1:], qx[:, 1:], qx[:, :-1])

    return np.asarray(theta * qx + w * (q_vanster + q_hoger), dtype=np.float64)


def utjamna_flode_y(
    qy: NDArray[np.float64],
    v_giltig: NDArray[np.bool_],
    theta: float,
) -> NDArray[np.float64]:
    """de Almeida m.fl. (2012) / SFINCS riktad theta-viktning längs y-kanter."""
    if theta >= 1.0 or qy.shape[0] < 2:
        return qy
    w = 0.5 * (1.0 - theta)
    q_norr = np.empty_like(qy)
    q_norr[0, :] = qy[0, :]
    q_norr[1:, :] = np.where(v_giltig[:-1, :], qy[:-1, :], qy[1:, :])

    q_soder = np.empty_like(qy)
    q_soder[-1, :] = qy[-1, :]
    q_soder[:-1, :] = np.where(v_giltig[1:, :], qy[1:, :], qy[:-1, :])

    return np.asarray(theta * qy + w * (q_norr + q_soder), dtype=np.float64)


def uppdatera_bates_flode_1d(
    q_nu: NDArray[np.float64],
    h_flode: NDArray[np.float64],
    lutning: NDArray[np.float64],
    manning_n: NDArray[np.float64],
    dt: float,
    min_djup_m: float,
    *,
    q_bar: NDArray[np.float64] | None = None,
    bredd_andel: NDArray[np.float64] | None = None,
) -> NDArray[np.float64]:
    """Bates (2010) / de Almeida (2012) lokala tröghetsekvation på aktiva kanter.

    När `bredd_andel` (`alpha` i (0, 1]) anges från subgrid skalas friktionen och
    Froude-taket efter det faktiska vattendjupet i den öppna delen (`h_vat = h_flode / alpha`)
    så att smala gränder får exakt Manning-konveyans.
    """
    g = GRAVITATION_M_S2
    vat = h_flode > min_djup_m
    if not bool(np.any(vat)):
        return np.zeros_like(q_nu, dtype=np.float64)

    ut = np.zeros_like(q_nu, dtype=np.float64)
    hv = h_flode[vat]
    q_tr = q_bar[vat] if q_bar is not None else q_nu[vat]
    sv = lutning[vat]
    nv = manning_n[vat]

    # Punkt-implicit kvadratisk Manning-friktion: q + c*|q|*q = q_rhs
    # Löses exakt utan kancellation via q = 2*q_rhs / (1 + sqrt(1 + 4*c*|q_rhs|)).
    q_rhs = q_tr - g * hv * dt * sv
    if bredd_andel is not None:
        ba = np.maximum(bredd_andel[vat], 1e-6)
        h_vat = hv / ba
        h_73 = (h_vat * h_vat) * np.cbrt(h_vat)
        c_frikt = g * dt * (nv * nv) / (ba * h_73)
        q_ny = (2.0 * q_rhs) / (1.0 + np.sqrt(1.0 + 4.0 * c_frikt * np.abs(q_rhs)))
        q_max = FROUDE_MAX * hv * np.sqrt(g * h_vat)
    else:
        h_73 = (hv * hv) * np.cbrt(hv)
        c_frikt = g * dt * (nv * nv) / h_73
        q_ny = (2.0 * q_rhs) / (1.0 + np.sqrt(1.0 + 4.0 * c_frikt * np.abs(q_rhs)))
        q_max = FROUDE_MAX * hv * np.sqrt(g * hv)

    ut[vat] = np.clip(q_ny, -q_max, q_max)
    return ut


def berakna_rand_q(
    h_kant: NDArray[np.float64],
    manning_kant: NDArray[np.float64],
    mask: NDArray[np.bool_],
    lutning: NDArray[np.float64],
    bredd_andel: NDArray[np.float64],
    min_djup_m: float,
) -> NDArray[np.float64]:
    """Beräknar utåtriktat enhetsflöde (m²/s) över en öppen randkant."""
    ut = np.zeros_like(h_kant, dtype=np.float64)
    akt = mask & (h_kant > min_djup_m)
    if not bool(np.any(akt)):
        return ut
    ha = h_kant[akt]
    na = manning_kant[akt]
    sa = lutning[akt]
    ba = bredd_andel[akt]
    q_man = (1.0 / na) * np.power(ha, 5.0 / 3.0) * np.sqrt(sa)
    q_krit = ha * np.sqrt(GRAVITATION_M_S2 * ha)
    ut[akt] = ba * np.minimum(q_man, q_krit)
    return ut


def berakna_aktiv_front(
    vat: NDArray[np.bool_],
    aktiv: NDArray[np.bool_],
) -> NDArray[np.bool_]:
    """Returnerar mask för våta celler samt deras närmaste torra 4-grannar (fronten)."""
    hojd, bredd = vat.shape
    front = vat.copy()
    if hojd > 1:
        front[1:, :] |= vat[:-1, :]
        front[:-1, :] |= vat[1:, :]
    if bredd > 1:
        front[:, 1:] |= vat[:, :-1]
        front[:, :-1] |= vat[:, 1:]
    front &= aktiv
    return front


class _SubgridAnkomstSparare:
    """Spårar exakta fina ankomsttider (0,1 m och 0,2 m) från grov vattenyta z_s."""

    def __init__(self, subgrid: SubgridTabell) -> None:
        self._subgrid = subgrid
        self._faktor = subgrid.faktor
        hf, bf = subgrid.mark_z_fin.shape
        hg, bg = subgrid.hojd_grov, subgrid.bredd_grov
        self.tid_01 = np.full((hf, bf), np.nan, dtype=np.float32)
        self.tid_02 = np.full((hf, bf), np.nan, dtype=np.float32)
        self._nasta_01 = np.full((hg, bg), np.inf, dtype=np.float64)
        self._nasta_02 = np.full((hg, bg), np.inf, dtype=np.float64)
        self._aktiv_fin = (subgrid.markslag_fin != MARKSLAG_BYGGNAD) & np.isfinite(
            subgrid.mark_z_fin
        )
        self._z_fin = np.asarray(subgrid.mark_z_fin, dtype=np.float64)

        for rg in range(hg):
            r0 = rg * self._faktor
            r1 = min(hf, r0 + self._faktor)
            for cg in range(bg):
                if not bool(subgrid.aktiv_mask[rg, cg]):
                    continue
                c0 = cg * self._faktor
                c1 = min(bf, c0 + self._faktor)
                akt_sub = self._aktiv_fin[r0:r1, c0:c1]
                if bool(np.any(akt_sub)):
                    min_z = float(np.min(self._z_fin[r0:r1, c0:c1][akt_sub]))
                    self._nasta_01[rg, cg] = min_z + 0.1
                    self._nasta_02[rg, cg] = min_z + 0.2

    def uppdatera(self, zs_grov: NDArray[np.float64], t_nu: float) -> None:
        """Uppdaterar fina ankomsttider enbart i grova celler som passerar en ny tröskel."""
        self._uppdatera_grans(zs_grov, t_nu, 0.1, self.tid_01, self._nasta_01)
        self._uppdatera_grans(zs_grov, t_nu, 0.2, self.tid_02, self._nasta_02)

    def _uppdatera_grans(
        self,
        zs_grov: NDArray[np.float64],
        t_nu: float,
        grans_m: float,
        tid_arr: NDArray[np.float32],
        nasta_arr: NDArray[np.float64],
    ) -> None:
        traff = zs_grov >= nasta_arr
        if not bool(np.any(traff)):
            return
        hf, bf = self._z_fin.shape
        f = self._faktor
        rader, kolumner = np.nonzero(traff)
        for rg_np, cg_np in zip(rader, kolumner, strict=True):
            rg, cg = int(rg_np), int(cg_np)
            r0 = rg * f
            r1 = min(hf, r0 + f)
            c0 = cg * f
            c1 = min(bf, c0 + f)
            zs_val = float(zs_grov[rg, cg])
            t_sub = tid_arr[r0:r1, c0:c1]
            z_sub = self._z_fin[r0:r1, c0:c1]
            akt_sub = self._aktiv_fin[r0:r1, c0:c1]
            kvar = akt_sub & np.isnan(t_sub)
            nadda = kvar & ((zs_val - z_sub) >= grans_m)
            t_sub[nadda] = np.float32(t_nu)
            aterstaende = kvar & (~nadda)
            nasta_arr[rg, cg] = (
                float(np.min(z_sub[aterstaende])) + grans_m
                if bool(np.any(aterstaende))
                else math.inf
            )


def kor_2d_lokal_troghet(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    slut_tid_s: float | None = None,
    cfl: float = 0.5,
    min_djup_m: float = 1e-4,
    gles_front: bool = True,
    subgrid_faktor: int = 1,
    oppna_rander: bool = True,
    manning_tabell: Mapping[int, float] | None = None,
    dt_max_s: float = 5.0,
    z_nivaer: int = 24,
    theta: float = 0.9,
    kalltermer: Kalltermer | None = None,
    takvatten: TakvattenFordelning | None = None,
    brytlinjer: Sequence[BrytlinjePost] = (),
) -> TvadResultat:
    """Kör 2D ytvattenberäkning med Bates m.fl. (2010) lokala tröghetsformulering.

    Args:
        mark_z: Höjdraster (float32, m RH 2000).
        markslag: Markslagsraster (uint8, K1-koder; 9 = ogenomtränglig byggnad).
        geo: Geotransform i EPSG:3011.
        regn_tid_s: Tidpunkter (s) för K4-punktregn.
        regn_intensitet_mm_h: Regnintensitet (mm/h) vid motsvarande tidpunkter.
        slut_tid_s: Valfri sluttid (s); standard är sista regntidpunkten.
        cfl: Courant-faktor i (0, 1], standard 0,5.
        min_djup_m: Minsta flödesdjup (m) för aktiv strömning.
        gles_front: Om True uppdateras enbart våta kanter och aktiva frontceller.
        subgrid_faktor: `1` = full fin 2D; `> 1` = grov subgrid via `tvad.subgrid`.
        oppna_rander: Om True får utåtlutande ytterkanter släppa ut vatten.
        manning_tabell: Valfri Manning-n-tabell per K1-markslag.
        dt_max_s: Övre gräns för tidssteget (s).
        z_nivaer: Antal nivåer per cell i subgrid-tabellen när `subgrid_faktor > 1`.
        theta: de Almeida (2012) / SFINCS dämpningsfaktor i (0, 1], standard 0,9.
        kalltermer: Valfria käll-/sänktermer per cell (`tvad.kalltermer`): initial förlust,
            Green–Ampt-infiltration, avtappning och tvingat djup. `None` ger exakt samma
            beräkning som utan termerna.
        takvatten: Takvattnets fördelning (`sankgraf.takvatten`) byggd för `mark_z` och
            `markslag`: regnet på byggnadscellerna läggs som källterm i markcellerna utanför
            fasaderna. `None` = regn på tak räknas inte (som före 2026-11-22).

    Returns:
        `TvadResultat` med K6-raster, massbalans, tidssteg och gles frontstatistik.
    """
    t_klocka_0 = time.perf_counter()

    if mark_z.ndim != 2 or markslag.ndim != 2 or mark_z.shape != markslag.shape:
        raise TvadFel("mark_z och markslag måste vara 2D-matriser med samma form.")
    hojd_fin, bredd_fin = mark_z.shape
    if hojd_fin != geo.hojd or bredd_fin != geo.bredd:
        raise TvadFel(
            f"GeoTransformInfo ({geo.hojd}x{geo.bredd}) matchar inte "
            f"raster ({hojd_fin}x{bredd_fin})."
        )
    if not math.isfinite(cfl) or cfl <= 0.0 or cfl > 1.0:
        raise TvadFel(f"cfl måste ligga i (0, 1], fick {cfl}.")
    if not math.isfinite(min_djup_m) or min_djup_m <= 0.0:
        raise TvadFel(f"min_djup_m måste vara > 0, fick {min_djup_m}.")
    if subgrid_faktor < 1:
        raise TvadFel(f"subgrid_faktor måste vara >= 1, fick {subgrid_faktor}.")
    if not math.isfinite(dt_max_s) or dt_max_s <= 0.0:
        raise TvadFel(f"dt_max_s måste vara > 0, fick {dt_max_s}.")
    if not math.isfinite(theta) or theta <= 0.0 or theta > 1.0:
        raise TvadFel(f"theta måste ligga i (0, 1], fick {theta}.")

    tider, intens_m_s, t_start, t_slut = validera_regnserie(
        regn_tid_s, regn_intensitet_mm_h, slut_tid_s
    )

    subgrid: SubgridTabell | None = None
    if subgrid_faktor > 1:
        subgrid = bygg_subgrid_tabell(
            mark_z,
            markslag,
            geo,
            faktor=subgrid_faktor,
            z_nivaer=z_nivaer,
            manning_tabell=manning_tabell,
        )
        hojd = subgrid.hojd_grov
        bredd = subgrid.bredd_grov
        dx = float(subgrid.geo_grov.pixelstorlek_x)
        dy = float(subgrid.geo_grov.pixelstorlek_y)
        z_bed = subgrid.z_min.copy()
        aktiv = subgrid.aktiv_mask.copy()
        aktiv_area = subgrid.aktiv_area_m2.copy()
        manning = subgrid.manning_n.copy()
        u_giltig = np.asarray(subgrid.oppningsgrad_u > 0.0, dtype=np.bool_)
        v_giltig = np.asarray(subgrid.oppningsgrad_v > 0.0, dtype=np.bool_)
    else:
        hojd = hojd_fin
        bredd = bredd_fin
        dx = float(geo.pixelstorlek_x)
        dy = float(geo.pixelstorlek_y)
        z_bed = np.asarray(mark_z, dtype=np.float64)
        aktiv = np.asarray((markslag != MARKSLAG_BYGGNAD) & np.isfinite(z_bed), dtype=np.bool_)
        aktiv_area = np.where(aktiv, dx * dy, 0.0)
        manning = bygg_manning_raster(markslag, manning_tabell)
        u_giltig = np.asarray(aktiv[:, :-1] & aktiv[:, 1:], dtype=np.bool_)
        v_giltig = np.asarray(aktiv[:-1, :] & aktiv[1:, :], dtype=np.bool_)

    total_doman_area = float(hojd_fin * bredd_fin) * float(geo.pixelstorlek_x) * float(
        geo.pixelstorlek_y
    )
    total_aktiv_area = float(np.sum(aktiv_area))
    tak_extra, tak_ut_m2, tak_ledning_m2, tak_m2 = takvatten_regnyta(
        takvatten, markslag, mark_z, float(geo.pixelstorlek_x) * float(geo.pixelstorlek_y),
        subgrid_faktor, (hojd, bredd),
    )
    har_tak = takvatten is not None and not takvatten.tom
    regn_area = aktiv_area + tak_extra if har_tak else aktiv_area
    total_regn_area = float(np.sum(regn_area)) + tak_ut_m2 if har_tak else total_aktiv_area
    regn_summa_m = 0.0

    manning_u = 0.5 * (manning[:, :-1] + manning[:, 1:]) if bredd > 1 else np.zeros((hojd, 0))
    manning_v = 0.5 * (manning[:-1, :] + manning[1:, :]) if hojd > 1 else np.zeros((0, bredd))
    z_kant_fin_u = (
        np.maximum(z_bed[:, :-1], z_bed[:, 1:]) if bredd > 1 else np.zeros((hojd, 0))
    )
    z_kant_fin_v = (
        np.maximum(z_bed[:-1, :], z_bed[1:, :]) if hojd > 1 else np.zeros((0, bredd))
    )
    if subgrid is None and brytlinjer:
        barriarer = _berakna_kantbarriarer(z_bed.ravel(), aktiv.ravel(), geo, brytlinjer)
        for (u_idx, v_idx), kant_z in barriarer.items():
            r0, c0 = divmod(u_idx, bredd)
            r1, c1 = divmod(v_idx, bredd)
            if r0 == r1 and c1 == c0 + 1 and bredd > 1:
                z_kant_fin_u[r0, c0] = max(float(z_kant_fin_u[r0, c0]), kant_z)
            elif c0 == c1 and r1 == r0 + 1 and hojd > 1:
                z_kant_fin_v[r0, c0] = max(float(z_kant_fin_v[r0, c0]), kant_z)

    rand_konfig = berakna_rand_utflode_konfig(
        z_bed, aktiv, dx, dy, oppna_rander=oppna_rander, subgrid=subgrid
    )

    volym = np.zeros((hojd, bredd), dtype=np.float64)
    h_eff = np.zeros((hojd, bredd), dtype=np.float64)
    zs = z_bed.copy()
    qx = np.zeros((hojd, max(0, bredd - 1)), dtype=np.float64)
    qy = np.zeros((max(0, hojd - 1), bredd), dtype=np.float64)

    max_zs = z_bed.copy()
    max_djup_grov = np.zeros((hojd, bredd), dtype=np.float64)
    max_hast_grov = np.zeros((hojd, bredd), dtype=np.float64)
    max_fara_grov = np.zeros((hojd, bredd), dtype=np.float64)

    tid_01_fin = np.full((hojd_fin, bredd_fin), np.nan, dtype=np.float32)
    tid_02_fin = np.full((hojd_fin, bredd_fin), np.nan, dtype=np.float32)
    subgrid_ankomst = _SubgridAnkomstSparare(subgrid) if subgrid is not None else None

    sank: SanktermTillstand | None = None
    if kalltermer is not None:
        fin_area = np.where(
            (markslag != MARKSLAG_BYGGNAD) & np.isfinite(mark_z),
            float(geo.pixelstorlek_x) * float(geo.pixelstorlek_y),
            0.0,
        )
        sank = SanktermTillstand.bygg(
            kalltermer, (hojd, bredd), aktiv_area_fin=fin_area, subgrid_faktor=subgrid_faktor
        )
    tvingat = kalltermer.tvingat_djup if kalltermer is not None else None
    if tvingat is not None and tvingat.mask.shape != (hojd, bredd):
        raise TvadFel(f"tvingat_djup.mask har form {tvingat.mask.shape}, lösaren {(hojd, bredd)}.")

    in_m3 = 0.0
    ut_m3 = 0.0
    antal_steg = 0
    sum_aktiv_andel = 0.0
    t_nu = t_start
    min_dx = min(dx, dy)
    safe_area = np.where(aktiv, aktiv_area, 1.0)
    u_max = 0.0
    courant_hast_max = 0.0
    # Förallokerade arbetsbuffertar i tidsloopen (undviker heap-allokering per steg)
    ut_vol = np.zeros((hojd, bredd), dtype=np.float64)
    q_cell_x = np.zeros((hojd, bredd), dtype=np.float64)
    q_cell_y = np.zeros((hojd, bredd), dtype=np.float64)

    while t_nu < t_slut - 1e-12:
        h_max = float(np.max(h_eff)) if h_eff.size > 0 else 0.0
        vaghast = math.sqrt(GRAVITATION_M_S2 * max(h_max, min_djup_m)) + u_max
        dt_cfl = cfl * min_dx / vaghast
        nasta_t = nasta_regnbrytpunkt(t_nu, tider, t_slut)
        dt = min(dt_cfl, dt_max_s, nasta_t - t_nu)
        if dt < _DT_MIN_S and (t_slut - t_nu) > _DT_MIN_S:
            dt = min(_DT_MIN_S, nasta_t - t_nu)
        if dt <= 0.0:
            break
        courant_hast_max = max(courant_hast_max, u_max * dt / min_dx)

        # 1. Regntillskott under [t_nu, t_nu + dt]
        regn_m = integrera_regn_m(t_nu, t_nu + dt, tider, intens_m_s)
        regn_summa_m += regn_m
        if regn_m > 0.0 and total_regn_area > 0.0:
            volym += regn_area * regn_m
            in_m3 += total_regn_area * regn_m
            ut_m3 += tak_ut_m2 * regn_m
            if subgrid is not None:
                zs = vattenniva_fran_volym(volym, subgrid)
                h_eff = np.where(aktiv, volym / safe_area, 0.0)
            else:
                h_eff = np.where(aktiv, volym / safe_area, 0.0)
                zs = z_bed + h_eff

        # 1b. Sänktermer per cell (initial förlust, infiltration, avtappning)
        if sank is not None:
            vat_area_sank = area_fran_vattenniva(zs, subgrid) if subgrid is not None else None
            sank.tillampa(volym, aktiv_area, dt, vat_area=vat_area_sank)
            h_eff = np.where(aktiv, volym / safe_area, 0.0)
            zs = vattenniva_fran_volym(volym, subgrid) if subgrid is not None else z_bed + h_eff

        # 2. Gles aktiv front
        vat = aktiv & (h_eff > min_djup_m)
        front = berakna_aktiv_front(vat, aktiv) if gles_front else aktiv
        if total_doman_area > 0.0:
            sum_aktiv_andel += float(np.sum(aktiv_area[front])) / total_doman_area

        # 3. Bates (2010) / de Almeida (2012) lokala tröghetsflöden på u- och v-kanter
        if bredd > 1:
            u_akt = (vat[:, :-1] | vat[:, 1:]) & u_giltig if gles_front else u_giltig
            n_u_akt = int(np.count_nonzero(u_akt))
            if n_u_akt == 0:
                qx.fill(0.0)
            else:
                if subgrid is not None:
                    h_u, ba_u = kant_flode_geometri_u(zs, subgrid)
                else:
                    h_u = np.asarray(
                        np.maximum(0.0, np.maximum(zs[:, :-1], zs[:, 1:]) - z_kant_fin_u),
                        dtype=np.float64,
                    )
                    ba_u = None
                sx = np.asarray((zs[:, 1:] - zs[:, :-1]) / dx, dtype=np.float64)
                qx_bar = utjamna_flode_x(qx, u_giltig, theta)
                if gles_front and n_u_akt < (u_akt.size // 2):
                    ur, uc = np.nonzero(u_akt)
                    q_ny_u = uppdatera_bates_flode_1d(
                        qx[ur, uc],
                        h_u[ur, uc],
                        sx[ur, uc],
                        manning_u[ur, uc],
                        dt,
                        min_djup_m,
                        q_bar=qx_bar[ur, uc],
                        bredd_andel=ba_u[ur, uc] if ba_u is not None else None,
                    )
                    qx.fill(0.0)
                    qx[ur, uc] = q_ny_u
                else:
                    q_ny_u = uppdatera_bates_flode_1d(
                        qx.ravel(),
                        np.asarray(np.where(u_akt, h_u, 0.0), dtype=np.float64).ravel(),
                        sx.ravel(),
                        manning_u.ravel(),
                        dt,
                        min_djup_m,
                        q_bar=qx_bar.ravel(),
                        bredd_andel=ba_u.ravel() if ba_u is not None else None,
                    )
                    qx = q_ny_u.reshape((hojd, bredd - 1))

        if hojd > 1:
            v_akt = (vat[:-1, :] | vat[1:, :]) & v_giltig if gles_front else v_giltig
            n_v_akt = int(np.count_nonzero(v_akt))
            if n_v_akt == 0:
                qy.fill(0.0)
            else:
                if subgrid is not None:
                    h_v, ba_v = kant_flode_geometri_v(zs, subgrid)
                else:
                    h_v = np.asarray(
                        np.maximum(0.0, np.maximum(zs[:-1, :], zs[1:, :]) - z_kant_fin_v),
                        dtype=np.float64,
                    )
                    ba_v = None
                sy = np.asarray((zs[1:, :] - zs[:-1, :]) / dy, dtype=np.float64)
                qy_bar = utjamna_flode_y(qy, v_giltig, theta)
                if gles_front and n_v_akt < (v_akt.size // 2):
                    vr, vc = np.nonzero(v_akt)
                    q_ny_v = uppdatera_bates_flode_1d(
                        qy[vr, vc],
                        h_v[vr, vc],
                        sy[vr, vc],
                        manning_v[vr, vc],
                        dt,
                        min_djup_m,
                        q_bar=qy_bar[vr, vc],
                        bredd_andel=ba_v[vr, vc] if ba_v is not None else None,
                    )
                    qy.fill(0.0)
                    qy[vr, vc] = q_ny_v
                else:
                    q_ny_v = uppdatera_bates_flode_1d(
                        qy.ravel(),
                        np.asarray(np.where(v_akt, h_v, 0.0), dtype=np.float64).ravel(),
                        sy.ravel(),
                        manning_v.ravel(),
                        dt,
                        min_djup_m,
                        q_bar=qy_bar.ravel(),
                        bredd_andel=ba_v.ravel() if ba_v is not None else None,
                    )
                    qy = q_ny_v.reshape((hojd - 1, bredd))

        # 4. Öppna randutflöden
        q_vast = berakna_rand_q(
            h_eff[:, 0],
            manning[:, 0],
            rand_konfig.vast_mask,
            rand_konfig.vast_lutning,
            rand_konfig.vast_bredd_andel,
            min_djup_m,
        )
        q_ost = berakna_rand_q(
            h_eff[:, -1],
            manning[:, -1],
            rand_konfig.ost_mask,
            rand_konfig.ost_lutning,
            rand_konfig.ost_bredd_andel,
            min_djup_m,
        )
        q_norr = berakna_rand_q(
            h_eff[0, :],
            manning[0, :],
            rand_konfig.norr_mask,
            rand_konfig.norr_lutning,
            rand_konfig.norr_bredd_andel,
            min_djup_m,
        )
        q_soder = berakna_rand_q(
            h_eff[-1, :],
            manning[-1, :],
            rand_konfig.soder_mask,
            rand_konfig.soder_lutning,
            rand_konfig.soder_bredd_andel,
            min_djup_m,
        )

        # 5. Massbevarande och positivitetsbevarande flödesbegränsare
        ut_vol.fill(0.0)
        if bredd > 1:
            ut_vol[:, :-1] += np.maximum(0.0, qx) * (dy * dt)
            ut_vol[:, 1:] += np.maximum(0.0, -qx) * (dy * dt)
        if hojd > 1:
            ut_vol[:-1, :] += np.maximum(0.0, qy) * (dx * dt)
            ut_vol[1:, :] += np.maximum(0.0, -qy) * (dx * dt)
        ut_vol[:, 0] += q_vast * (dy * dt)
        ut_vol[:, -1] += q_ost * (dy * dt)
        ut_vol[0, :] += q_norr * (dx * dt)
        ut_vol[-1, :] += q_soder * (dx * dt)

        over_ut = ut_vol > volym
        if bool(np.any(over_ut)):
            skala = np.ones((hojd, bredd), dtype=np.float64)
            skala[over_ut] = volym[over_ut] / ut_vol[over_ut]
            if bredd > 1:
                qx = np.where(qx >= 0.0, qx * skala[:, :-1], qx * skala[:, 1:])
            if hojd > 1:
                qy = np.where(qy >= 0.0, qy * skala[:-1, :], qy * skala[1:, :])
            q_vast = q_vast * skala[:, 0]
            q_ost = q_ost * skala[:, -1]
            q_norr = q_norr * skala[0, :]
            q_soder = q_soder * skala[-1, :]

        # 6. Volymuppdatering (exakt massbevarande)
        if bredd > 1:
            dv_x = qx * (dy * dt)
            volym[:, :-1] -= dv_x
            volym[:, 1:] += dv_x
        if hojd > 1:
            dv_y = qy * (dx * dt)
            volym[:-1, :] -= dv_y
            volym[1:, :] += dv_y

        dv_vast = q_vast * (dy * dt)
        dv_ost = q_ost * (dy * dt)
        dv_norr = q_norr * (dx * dt)
        dv_soder = q_soder * (dx * dt)
        volym[:, 0] -= dv_vast
        volym[:, -1] -= dv_ost
        volym[0, :] -= dv_norr
        volym[-1, :] -= dv_soder
        ut_m3 += float(
            np.sum(dv_vast) + np.sum(dv_ost) + np.sum(dv_norr) + np.sum(dv_soder)
        )

        np.maximum(volym, 0.0, out=volym)
        volym[~aktiv] = 0.0

        # 6b. Tvingat djup (randvillkor): skillnaden räknas som in- eller utflöde
        if tvingat is not None:
            mal_v = np.where(tvingat.mask & aktiv, tvingat.djup_m(t_nu + dt) * aktiv_area, 0.0)
            diff = np.where(tvingat.mask & aktiv, mal_v - volym, 0.0)
            in_m3 += float(np.sum(np.maximum(diff, 0.0)))
            ut_m3 += float(np.sum(np.maximum(-diff, 0.0)))
            volym = np.where(tvingat.mask & aktiv, mal_v, volym)

        # 7. Uppdatera vattenyta och effektivt djup (spara h_fore för hastighetsberäkning)
        h_fore = h_eff
        if subgrid is not None:
            zs = vattenniva_fran_volym(volym, subgrid)
            h_eff = np.where(aktiv, volym / safe_area, 0.0)
        else:
            h_eff = np.where(aktiv, volym / safe_area, 0.0)
            zs = z_bed + h_eff

        t_nu += dt
        antal_steg += 1

        # 8. Uppdatera maxdjup, hastighet, fara och ankomsttider
        np.maximum(max_zs, zs, out=max_zs)
        np.maximum(max_djup_grov, h_eff, out=max_djup_grov)

        q_cell_x.fill(0.0)
        if bredd > 1:
            q_cell_x[:, :-1] += 0.5 * qx
            q_cell_x[:, 1:] += 0.5 * qx
        q_cell_x[:, 0] -= 0.5 * q_vast
        q_cell_x[:, -1] += 0.5 * q_ost

        q_cell_y.fill(0.0)
        if hojd > 1:
            q_cell_y[:-1, :] += 0.5 * qy
            q_cell_y[1:, :] += 0.5 * qy
        q_cell_y[0, :] -= 0.5 * q_norr
        q_cell_y[-1, :] += 0.5 * q_soder

        q_mag = np.hypot(q_cell_x, q_cell_y)
        h_vel = np.maximum(h_fore, h_eff)
        vat_nu = aktiv & (h_vel > min_djup_m)
        if bool(np.any(vat_nu)):
            hast = np.zeros((hojd, bredd), dtype=np.float64)
            hast[vat_nu] = q_mag[vat_nu] / h_vel[vat_nu]
            u_max = float(np.max(hast))
            np.maximum(max_hast_grov, hast, out=max_hast_grov)
            fara_steg = np.where(vat_nu, q_mag, 0.0)
            np.maximum(max_fara_grov, fara_steg, out=max_fara_grov)
        else:
            u_max = 0.0

        if subgrid_ankomst is not None:
            subgrid_ankomst.uppdatera(zs, t_nu)
        else:
            ny_01 = aktiv & np.isnan(tid_01_fin) & (h_eff >= 0.1)
            if bool(np.any(ny_01)):
                tid_01_fin[ny_01] = np.float32(t_nu)
            ny_02 = aktiv & np.isnan(tid_02_fin) & (h_eff >= 0.2)
            if bool(np.any(ny_02)):
                tid_02_fin[ny_02] = np.float32(t_nu)

    lagrat_m3 = float(np.sum(volym))
    sankt_m3 = (
        sank.avtappat_m3 + sank.infiltrerat_m3 + sank.initial_m3 if sank is not None else 0.0
    )
    if sank is None:
        volymfel_rel = abs(in_m3 - ut_m3 - lagrat_m3) / in_m3 if in_m3 > 0.0 else 0.0
    else:
        volymfel_rel = (
            abs(in_m3 - ut_m3 - lagrat_m3 - sankt_m3) / in_m3 if in_m3 > 0.0 else 0.0
        )
    aktiv_cell_andel = sum_aktiv_andel / float(antal_steg) if antal_steg > 0 else 0.0

    if subgrid is not None:
        max_djup_ut = projicera_djup_till_fin(max_zs, subgrid)
        slut_djup_ut = projicera_djup_till_fin(zs, subgrid)
        max_hast_ut = projicera_falt_till_fin(max_hast_grov, subgrid, byggnad_varde=0.0)
        fara_ut = projicera_falt_till_fin(max_fara_grov, subgrid, byggnad_varde=0.0)
        assert subgrid_ankomst is not None
        tid_01_ut = subgrid_ankomst.tid_01
        tid_02_ut = subgrid_ankomst.tid_02
    else:
        max_djup_ut = np.asarray(max_djup_grov, dtype=np.float32)
        slut_djup_ut = np.asarray(h_eff, dtype=np.float32)
        max_hast_ut = np.asarray(max_hast_grov, dtype=np.float32)
        fara_ut = np.asarray(max_fara_grov, dtype=np.float32)
        tid_01_ut = tid_01_fin
        tid_02_ut = tid_02_fin

    kortid_s = time.perf_counter() - t_klocka_0
    return TvadResultat(
        max_djup_m=max_djup_ut,
        max_hastighet_m_s=max_hast_ut,
        fara_m2_s=fara_ut,
        tid_till_0_1m_s=tid_01_ut,
        tid_till_0_2m_s=tid_02_ut,
        slut_djup_m=slut_djup_ut,
        in_m3=in_m3,
        lagrat_m3=lagrat_m3,
        ut_m3=ut_m3,
        volymfel_rel=volymfel_rel,
        antal_tidssteg=antal_steg,
        kortid_s=kortid_s,
        niva="full2d",
        aktiv_cell_andel=aktiv_cell_andel,
        avtappat_m3=sank.avtappat_m3 if sank is not None else 0.0,
        infiltrerat_m3=sank.infiltrerat_m3 if sank is not None else 0.0,
        initial_forlust_m3=sank.initial_m3 if sank is not None else 0.0,
        infiltration_m=(
            np.asarray(sank.infiltrerat_m, dtype=np.float32)
            if sank is not None and sank.ksat_m_s is not None
            else None
        ),
        sanktermer=kalltermer.beskrivning() if kalltermer is not None else None,
        regn_mark_m3=total_aktiv_area * regn_summa_m,
        regn_tak_m3=tak_m2 * regn_summa_m,
        takvatten=takvatten is not None,
        takvatten_ut_rand_m3=tak_ut_m2 * regn_summa_m,
        takvatten_till_ledning_m3=tak_ledning_m2 * regn_summa_m,
        courant_hast_max=courant_hast_max,
    )


def skriv_tvad_resultat_k6(
    resultat: TvadResultat,
    geo: GeoTransformInfo,
    ut_mapp: str | Path,
    *,
    indata: Sequence[FilPost] | None = None,
    parametrar: Mapping[str, Any] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> dict[str, Path]:
    """Skriver 2D- eller hybridresultat enligt kontrakt K6/v1 + K0/v1 (`run.json`)."""
    if resultat.niva not in ("full2d", "hybrid"):
        raise TvadFel(
            f"Ogiltig nivå '{resultat.niva}' i TvadResultat (förväntade 'full2d' eller 'hybrid')."
        )
    forv_form = (geo.hojd, geo.bredd)
    for namn, arr in (
        ("max_djup_m", resultat.max_djup_m),
        ("max_hastighet_m_s", resultat.max_hastighet_m_s),
        ("fara_m2_s", resultat.fara_m2_s),
        ("tid_till_0_1m_s", resultat.tid_till_0_1m_s),
        ("tid_till_0_2m_s", resultat.tid_till_0_2m_s),
    ):
        if arr.shape != forv_form:
            raise TvadFel(f"Raster '{namn}' har form {arr.shape}, förväntade {forv_form}.")

    mal = Path(ut_mapp)
    mal.mkdir(parents=True, exist_ok=True)
    filer: dict[str, Path] = {}
    utdata_poster: list[FilPost] = []

    for roll, filnamn, arr in (
        ("max_djup", "max_djup.tif", resultat.max_djup_m),
        ("max_hastighet", "max_hastighet.tif", resultat.max_hastighet_m_s),
        ("fara", "fara.tif", resultat.fara_m2_s),
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

    param_dict: dict[str, Any] = {
        "kontrakt_ut": KONTRAKT_K6,
        "niva": resultat.niva,
        "epsg": geo.epsg,
    }
    if resultat.sanktermer is not None:
        param_dict["sanktermer"] = resultat.sanktermer
    if parametrar is not None:
        param_dict.update(parametrar)

    max_djup_val = (
        float(np.nanmax(resultat.max_djup_m)) if resultat.max_djup_m.size > 0 else 0.0
    )
    max_hast_val = (
        float(np.nanmax(resultat.max_hastighet_m_s))
        if resultat.max_hastighet_m_s.size > 0
        else 0.0
    )
    max_fara_val = float(np.nanmax(resultat.fara_m2_s)) if resultat.fara_m2_s.size > 0 else 0.0

    manifest = skapa_manifest(
        modul="tvad",
        indata=list(indata) if indata is not None else [],
        parametrar=param_dict,
        utdata=utdata_poster,
        matvarden={
            "in_m3": float(resultat.in_m3),
            "lagrat_m3": float(resultat.lagrat_m3),
            "ut_m3": float(resultat.ut_m3),
            "volymfel_rel": float(resultat.volymfel_rel),
            "kortid_s": float(resultat.kortid_s),
            "antal_tidssteg": int(resultat.antal_tidssteg),
            "aktiv_cell_andel": float(resultat.aktiv_cell_andel),
            "max_djup_m": max_djup_val,
            "max_hastighet_m_s": max_hast_val,
            "max_fara_m2_s": max_fara_val,
            **(
                {
                    "avtappat_m3": float(resultat.avtappat_m3),
                    "infiltrerat_m3": float(resultat.infiltrerat_m3),
                    "initial_forlust_m3": float(resultat.initial_forlust_m3),
                }
                if resultat.sanktermer is not None
                else {}
            ),
        },
        kodversion=kodversion,
        start=start,
    )
    run_fil = skriv_manifest(manifest, mal / "run.json")
    filer["run"] = run_fil
    return filer
