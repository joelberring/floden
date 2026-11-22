"""Hybrid 2D + sänkgraf: djupa sänkor som grafnoder och skiktflöde i 2D (T05 / Q3).

Delar upp området i:
1. Grafnoder (djupa samlingar från T02 `SankGraf` med `persistens_m >= nod_persistens_grans_m`)
   modellerade med plan vattenyta via sin exakta lagringskurva `volymkurva` / `niva_vid_volym`.
2. 2D-domän (sluttningar, gator, grunda stråk) löst med lokal tröghet (Bates 2010),
   valfri subgrid-aggregering (`subgrid_faktor`) och gles aktiv front (`gles_front=True`).

Kopplingen mellan 2D-randceller och angränsande grafnoder sker vid varje tidssteg med
fysikalisk Manning-/överfallsbegränsning samt jämvikts- och positivitetsbegränsare,
vilket garanterar strikt massbalans (< 0,1 %) och svängningsfri stabilitet.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from sankgraf.hierarki import bygg_sankgraf
from sankgraf.takvatten import TakvattenFordelning
from sankgraf.typer import MARKSLAG_BYGGNAD, SankGraf, SankNod
from testsvit.k1_io import GeoTransformInfo
from tvad.lokal_troghet import (
    FROUDE_MAX,
    GRAVITATION_M_S2,
    TvadFel,
    TvadResultat,
    _SubgridAnkomstSparare,
    berakna_aktiv_front,
    berakna_rand_q,
    berakna_rand_utflode_konfig,
    bygg_manning_raster,
    integrera_regn_m,
    nasta_regnbrytpunkt,
    uppdatera_bates_flode_1d,
    utjamna_flode_x,
    utjamna_flode_y,
    validera_regnserie,
)
from tvad.subgrid import (
    SubgridTabell,
    bygg_subgrid_tabell,
    kant_flodesdjup_u,
    kant_flodesdjup_v,
    projicera_djup_till_fin,
    projicera_falt_till_fin,
    vattenniva_fran_volym,
)

_DT_MIN_S = 1e-4


@dataclass(frozen=True)
class HybridJamforelse:
    """Valideringsmått mellan full 2D och hybrid enligt `docs/VALIDERING.md`."""

    maxdjupsfel_mae_m: float
    maxdjupsfel_max_m: float
    traffad_yta_iou: float
    ankomsttid_mae_s: float | None
    volymfel_full2d_rel: float
    volymfel_hybrid_rel: float
    kortid_full2d_s: float
    kortid_hybrid_s: float
    speedup_faktor: float
    antal_vata_celler_union: int
    antal_ankomst_celler: int


@dataclass
class _NodTillstand:
    """Förberäknad geometri och ankomsttidsspårning för en grafnod i hybriden."""

    nod: SankNod
    cell_rader: NDArray[np.intp]
    cell_kolumner: NDArray[np.intp]
    area_m2: float
    z_sorterad: NDArray[np.float64]
    r_sorterad: NDArray[np.intp]
    c_sorterad: NDArray[np.intp]
    ptr_01: int = 0
    ptr_02: int = 0

    def niva_vid_volym(self, volym_m3: float) -> float:
        """Exakt vattennivå (RH 2000), inklusive linjär stigning ovanför överfallsnivån."""
        if volym_m3 <= 0.0:
            return self.nod.botten_rh2000
        v_max = self.nod.max_volym_m3
        if volym_m3 <= v_max:
            return self.nod.niva_vid_volym(volym_m3)
        return self.nod.overfall_rh2000 + (volym_m3 - v_max) / self.area_m2

    def vat_area_vid_niva(self, z_rh2000: float, cell_area_fin: float) -> float:
        """Våt vattenyta (m²) vid `z_rh2000`, begränsad nedåt av en cellarea."""
        if z_rh2000 >= self.nod.overfall_rh2000:
            return self.area_m2
        return max(cell_area_fin, self.nod.area_vid_niva(z_rh2000))

    def uppdatera_ankomst(
        self,
        z_s: float,
        t_nu: float,
        tid_01: NDArray[np.float32],
        tid_02: NDArray[np.float32],
    ) -> None:
        """Uppdaterar exakta fina ankomsttider (0,1 m och 0,2 m) i O(1) amorterad tid."""
        n = int(self.z_sorterad.size)
        while self.ptr_01 < n and (z_s - float(self.z_sorterad[self.ptr_01])) >= 0.1:
            r = int(self.r_sorterad[self.ptr_01])
            c = int(self.c_sorterad[self.ptr_01])
            if math.isnan(float(tid_01[r, c])):
                tid_01[r, c] = np.float32(t_nu)
            self.ptr_01 += 1
        while self.ptr_02 < n and (z_s - float(self.z_sorterad[self.ptr_02])) >= 0.2:
            r = int(self.r_sorterad[self.ptr_02])
            c = int(self.c_sorterad[self.ptr_02])
            if math.isnan(float(tid_02[r, c])):
                tid_02[r, c] = np.float32(t_nu)
            self.ptr_02 += 1


@dataclass(frozen=True)
class _KopplingsKanter:
    """Vektoriserade fina kopplingskanter mellan 2D-domänens grova celler och grafnoder."""

    rg: NDArray[np.intp]
    cg: NDArray[np.intp]
    nod_idx: NDArray[np.intp]
    z_kant: NDArray[np.float64]
    ds: NDArray[np.float64]
    dl: NDArray[np.float64]
    manning: NDArray[np.float64]
    grad_2d: NDArray[np.float64]
    dir_x: NDArray[np.float64]
    dir_y: NDArray[np.float64]

    @property
    def antal(self) -> int:
        return int(self.rg.size)


@dataclass(frozen=True)
class _NodTillNodKanter:
    """Eventuella direkta fina kopplingskanter mellan två angränsande grafnoder."""

    k1: NDArray[np.intp]
    k2: NDArray[np.intp]
    z_kant: NDArray[np.float64]
    ds: NDArray[np.float64]
    dl: NDArray[np.float64]
    manning: NDArray[np.float64]
    grad_nn: NDArray[np.float64]

    @property
    def antal(self) -> int:
        return int(self.k1.size)


def _bygg_kopplingskanter(
    eff_z: NDArray[np.float64],
    aktiv_2d_fin: NDArray[np.bool_],
    cell_till_hyb_nod: NDArray[np.int64],
    manning_fin: NDArray[np.float64],
    dx_fin: float,
    dy_fin: float,
    dx_grov: float,
    dy_grov: float,
    faktor: int,
    hojd_grov: int,
    bredd_grov: int,
) -> tuple[_KopplingsKanter, _NodTillNodKanter]:
    """Extraherar alla 4-granne-kopplingskanter mellan 2D-celler och grafnoder."""
    hojd_fin, bredd_fin = eff_z.shape

    rg_l: list[int] = []
    cg_l: list[int] = []
    nod_l: list[int] = []
    zk_l: list[float] = []
    ds_l: list[float] = []
    dl_l: list[float] = []
    man_l: list[float] = []
    dx_dir_l: list[float] = []
    dy_dir_l: list[float] = []

    nn_k1: list[int] = []
    nn_k2: list[int] = []
    nn_zk: list[float] = []
    nn_ds: list[float] = []
    nn_dl: list[float] = []
    nn_man: list[float] = []

    # Horisontella 4-grannekanter: (r, c) <-> (r, c + 1)
    if bredd_fin > 1:
        n_l = cell_till_hyb_nod[:, :-1]
        n_r = cell_till_hyb_nod[:, 1:]
        a_l = aktiv_2d_fin[:, :-1]
        a_r = aktiv_2d_fin[:, 1:]

        # 2D vänster -> Nod höger (flöde i +x-riktning)
        rr, cc = np.nonzero(a_l & (n_r >= 0))
        for r_np, c_np in zip(rr, cc, strict=True):
            r, c = int(r_np), int(c_np)
            rg_l.append(r // faktor)
            cg_l.append(c // faktor)
            nod_l.append(int(n_r[r, c]))
            zk_l.append(max(float(eff_z[r, c]), float(eff_z[r, c + 1])))
            ds_l.append(dy_fin)
            dl_l.append(0.5 * (dx_fin + dx_grov))
            man_l.append(float(manning_fin[r, c]))
            dx_dir_l.append(1.0)
            dy_dir_l.append(0.0)

        # Nod vänster <- 2D höger (flöde 2D -> Nod i -x-riktning)
        rr, cc = np.nonzero((n_l >= 0) & a_r)
        for r_np, c_np in zip(rr, cc, strict=True):
            r, c = int(r_np), int(c_np)
            rg_l.append(r // faktor)
            cg_l.append((c + 1) // faktor)
            nod_l.append(int(n_l[r, c]))
            zk_l.append(max(float(eff_z[r, c]), float(eff_z[r, c + 1])))
            ds_l.append(dy_fin)
            dl_l.append(0.5 * (dx_fin + dx_grov))
            man_l.append(float(manning_fin[r, c + 1]))
            dx_dir_l.append(-1.0)
            dy_dir_l.append(0.0)

        # Direkt nod-till-nod-gräns
        rr, cc = np.nonzero((n_l >= 0) & (n_r >= 0) & (n_l != n_r))
        for r_np, c_np in zip(rr, cc, strict=True):
            r, c = int(r_np), int(c_np)
            nn_k1.append(int(n_l[r, c]))
            nn_k2.append(int(n_r[r, c]))
            nn_zk.append(max(float(eff_z[r, c]), float(eff_z[r, c + 1])))
            nn_ds.append(dy_fin)
            nn_dl.append(dx_fin)
            nn_man.append(0.5 * (float(manning_fin[r, c]) + float(manning_fin[r, c + 1])))

    # Vertikala 4-grannekanter: (r, c) <-> (r + 1, c)
    if hojd_fin > 1:
        n_t = cell_till_hyb_nod[:-1, :]
        n_b = cell_till_hyb_nod[1:, :]
        a_t = aktiv_2d_fin[:-1, :]
        a_b = aktiv_2d_fin[1:, :]

        # 2D norr -> Nod söder (flöde i +y-riktning)
        rr, cc = np.nonzero(a_t & (n_b >= 0))
        for r_np, c_np in zip(rr, cc, strict=True):
            r, c = int(r_np), int(c_np)
            rg_l.append(r // faktor)
            cg_l.append(c // faktor)
            nod_l.append(int(n_b[r, c]))
            zk_l.append(max(float(eff_z[r, c]), float(eff_z[r + 1, c])))
            ds_l.append(dx_fin)
            dl_l.append(0.5 * (dy_fin + dy_grov))
            man_l.append(float(manning_fin[r, c]))
            dx_dir_l.append(0.0)
            dy_dir_l.append(1.0)

        # Nod norr <- 2D söder (flöde 2D -> Nod i -y-riktning)
        rr, cc = np.nonzero((n_t >= 0) & a_b)
        for r_np, c_np in zip(rr, cc, strict=True):
            r, c = int(r_np), int(c_np)
            rg_l.append((r + 1) // faktor)
            cg_l.append(c // faktor)
            nod_l.append(int(n_t[r, c]))
            zk_l.append(max(float(eff_z[r, c]), float(eff_z[r + 1, c])))
            ds_l.append(dx_fin)
            dl_l.append(0.5 * (dy_fin + dy_grov))
            man_l.append(float(manning_fin[r + 1, c]))
            dx_dir_l.append(0.0)
            dy_dir_l.append(-1.0)

        # Direkt nod-till-nod-gräns
        rr, cc = np.nonzero((n_t >= 0) & (n_b >= 0) & (n_t != n_b))
        for r_np, c_np in zip(rr, cc, strict=True):
            r, c = int(r_np), int(c_np)
            nn_k1.append(int(n_t[r, c]))
            nn_k2.append(int(n_b[r, c]))
            nn_zk.append(max(float(eff_z[r, c]), float(eff_z[r + 1, c])))
            nn_ds.append(dx_fin)
            nn_dl.append(dy_fin)
            nn_man.append(0.5 * (float(manning_fin[r, c]) + float(manning_fin[r + 1, c])))

    rg_arr = np.asarray(rg_l, dtype=np.intp)
    cg_arr = np.asarray(cg_l, dtype=np.intp)
    grad_karta = np.zeros((hojd_grov, bredd_grov), dtype=np.float64)
    if rg_arr.size > 0:
        np.add.at(grad_karta, (rg_arr, cg_arr), 1.0)
        grad_2d = np.maximum(1.0, grad_karta[rg_arr, cg_arr])
    else:
        grad_2d = np.zeros(0, dtype=np.float64)

    kop = _KopplingsKanter(
        rg=rg_arr,
        cg=cg_arr,
        nod_idx=np.asarray(nod_l, dtype=np.intp),
        z_kant=np.asarray(zk_l, dtype=np.float64),
        ds=np.asarray(ds_l, dtype=np.float64),
        dl=np.asarray(dl_l, dtype=np.float64),
        manning=np.asarray(man_l, dtype=np.float64),
        grad_2d=grad_2d,
        dir_x=np.asarray(dx_dir_l, dtype=np.float64),
        dir_y=np.asarray(dy_dir_l, dtype=np.float64),
    )
    nn_k1_arr = np.asarray(nn_k1, dtype=np.intp)
    nn_k2_arr = np.asarray(nn_k2, dtype=np.intp)
    if nn_k1_arr.size > 0:
        par_antal: dict[tuple[int, int], int] = {}
        for a, b in zip(nn_k1, nn_k2, strict=True):
            nyckel = (a, b) if a <= b else (b, a)
            par_antal[nyckel] = par_antal.get(nyckel, 0) + 1
        grad_nn = np.asarray(
            [
                float(par_antal[(a, b) if a <= b else (b, a)])
                for a, b in zip(nn_k1, nn_k2, strict=True)
            ],
            dtype=np.float64,
        )
    else:
        grad_nn = np.zeros(0, dtype=np.float64)

    nn = _NodTillNodKanter(
        k1=nn_k1_arr,
        k2=nn_k2_arr,
        z_kant=np.asarray(nn_zk, dtype=np.float64),
        ds=np.asarray(nn_ds, dtype=np.float64),
        dl=np.asarray(nn_dl, dtype=np.float64),
        manning=np.asarray(nn_man, dtype=np.float64),
        grad_nn=grad_nn,
    )
    return kop, nn


def kor_hybrid_2d_graf(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    graf: SankGraf | None = None,
    osakerhet_z: NDArray[np.float32] | None = None,
    nod_persistens_grans_m: float = 0.15,
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
    takvatten: TakvattenFordelning | None = None,
) -> TvadResultat:
    """Kör hybrid 2D + sänkgraf med massbevarande kantkoppling till K6-format.

    `takvatten` (eller grafens `graf.takvatten` om argumentet saknas) lägger regnet på
    byggnadscellerna i markcellerna utanför fasaderna, både i 2D-celler och i grafnoder.

    Djupa sänkor (`persistens_m >= nod_persistens_grans_m`) ur `SankGraf` räknas
    som grafnoder med plan vattenyta, medan övriga celler (sluttningar, gator,
    trösklar och grunda stråk) löses med lokal tröghet (och valfri subgrid + gles front).
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
    if not math.isfinite(nod_persistens_grans_m) or nod_persistens_grans_m < 0.0:
        raise TvadFel(f"nod_persistens_grans_m måste vara >= 0, fick {nod_persistens_grans_m}.")
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

    if graf is None:
        osak = (
            osakerhet_z
            if osakerhet_z is not None
            else np.zeros((hojd_fin, bredd_fin), dtype=np.float32)
        )
        graf = bygg_sankgraf(mark_z, osak, markslag, geo)

    eff_z_f32 = graf.mark_z
    eff_slag = graf.markslag
    if eff_z_f32.shape != (hojd_fin, bredd_fin):
        raise TvadFel(
            f"SankGraf har rasterform {eff_z_f32.shape}, förväntade {(hojd_fin, bredd_fin)}."
        )
    eff_z64 = np.asarray(eff_z_f32, dtype=np.float64)
    aktiv_tot_fin = np.asarray(
        (eff_slag != MARKSLAG_BYGGNAD) & np.isfinite(eff_z64),
        dtype=np.bool_,
    )
    dx_fin = float(geo.pixelstorlek_x)
    dy_fin = float(geo.pixelstorlek_y)
    cell_area_fin = dx_fin * dy_fin

    # 1. Välj djupa lövsänkor som grafnoder (garanterat disjunkta fotavtryck)
    cell_till_hyb_nod = np.full((hojd_fin, bredd_fin), -1, dtype=np.int64)
    noder: list[_NodTillstand] = []
    for nod in graf.lovnoder():
        if nod.persistens_m < nod_persistens_grans_m or nod.cell_antal <= 0:
            continue
        nod_mask = (
            aktiv_tot_fin
            & (graf.cell_till_nod == nod.nod_id)
            & (eff_z64 < nod.overfall_rh2000)
        )
        rr, cc = np.nonzero(nod_mask)
        if rr.size == 0:
            continue
        k = len(noder)
        cell_till_hyb_nod[rr, cc] = k
        z_celler = eff_z64[rr, cc]
        ordn = np.argsort(z_celler, kind="mergesort")
        noder.append(
            _NodTillstand(
                nod=nod,
                cell_rader=rr,
                cell_kolumner=cc,
                area_m2=float(rr.size) * cell_area_fin,
                z_sorterad=z_celler[ordn],
                r_sorterad=rr[ordn],
                c_sorterad=cc[ordn],
            )
        )

    n_noder = len(noder)
    slag_2d = np.array(eff_slag, dtype=np.uint8, copy=True)
    slag_2d[cell_till_hyb_nod >= 0] = np.uint8(MARKSLAG_BYGGNAD)
    aktiv_2d_fin = np.asarray(aktiv_tot_fin & (cell_till_hyb_nod < 0), dtype=np.bool_)
    manning_fin = bygg_manning_raster(eff_slag, manning_tabell)

    # 2. Bygg 2D-domän (grov subgrid eller fin 2D) utan grafnodceller
    subgrid: SubgridTabell | None = None
    if subgrid_faktor > 1:
        subgrid = bygg_subgrid_tabell(
            eff_z_f32,
            slag_2d,
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
        aktiv_2d = subgrid.aktiv_mask.copy()
        aktiv_area_2d = subgrid.aktiv_area_m2.copy()
        manning_2d = subgrid.manning_n.copy()
        u_giltig = np.asarray(subgrid.oppningsgrad_u > 0.0, dtype=np.bool_)
        v_giltig = np.asarray(subgrid.oppningsgrad_v > 0.0, dtype=np.bool_)
    else:
        hojd = hojd_fin
        bredd = bredd_fin
        dx = dx_fin
        dy = dy_fin
        z_bed = eff_z64.copy()
        aktiv_2d = aktiv_2d_fin.copy()
        aktiv_area_2d = np.where(aktiv_2d, dx * dy, 0.0)
        manning_2d = manning_fin.copy()
        u_giltig = np.asarray(aktiv_2d[:, :-1] & aktiv_2d[:, 1:], dtype=np.bool_)
        v_giltig = np.asarray(aktiv_2d[:-1, :] & aktiv_2d[1:, :], dtype=np.bool_)

    kop, nn_kop = _bygg_kopplingskanter(
        eff_z64,
        aktiv_2d_fin,
        cell_till_hyb_nod,
        manning_fin,
        dx_fin,
        dy_fin,
        dx,
        dy,
        subgrid_faktor,
        hojd,
        bredd,
    )

    total_doman_area = float(hojd_fin * bredd_fin) * cell_area_fin
    total_aktiv_2d_area = float(np.sum(aktiv_area_2d))
    nod_areor = np.asarray([n.area_m2 for n in noder], dtype=np.float64)
    total_nod_area = float(np.sum(nod_areor))
    regn_mark_area = total_aktiv_2d_area + total_nod_area
    regn_area_2d = aktiv_area_2d
    tv = takvatten if takvatten is not None else graf.takvatten
    har_tak = tv is not None and not tv.tom
    tak_ut_m2 = tak_ledning_m2 = 0.0
    tak_m2 = float(np.count_nonzero(eff_slag == MARKSLAG_BYGGNAD)) * cell_area_fin
    if tv is not None and har_tak:
        tv.kontrollera_mot(eff_slag, eff_z_f32)
        extra_fin = tv.extra_area_m2
        tak_ut_m2, tak_ledning_m2 = float(tv.ut_area_m2), float(tv.ledning_area_m2)
        nod_areor = nod_areor + np.asarray(
            [float(np.sum(extra_fin[n.cell_rader, n.cell_kolumner])) for n in noder],
            dtype=np.float64,
        )
        extra_2d = np.where(cell_till_hyb_nod < 0, extra_fin, 0.0)
        if subgrid_faktor > 1:
            f = int(subgrid_faktor)
            pad = np.zeros((hojd * f, bredd * f), dtype=np.float64)
            pad[:hojd_fin, :bredd_fin] = extra_2d
            extra_2d = pad.reshape(hojd, f, bredd, f).sum(axis=(1, 3))
        regn_area_2d = aktiv_area_2d + extra_2d
        total_aktiv_2d_area = float(np.sum(regn_area_2d))
        total_nod_area = float(np.sum(nod_areor))
    regn_summa_m = 0.0

    manning_u = 0.5 * (manning_2d[:, :-1] + manning_2d[:, 1:]) if bredd > 1 else np.zeros((hojd, 0))
    manning_v = 0.5 * (manning_2d[:-1, :] + manning_2d[1:, :]) if hojd > 1 else np.zeros((0, bredd))
    z_kant_fin_u = (
        np.maximum(z_bed[:, :-1], z_bed[:, 1:]) if bredd > 1 else np.zeros((hojd, 0))
    )
    z_kant_fin_v = (
        np.maximum(z_bed[:-1, :], z_bed[1:, :]) if hojd > 1 else np.zeros((0, bredd))
    )

    rand_konfig = berakna_rand_utflode_konfig(
        z_bed, aktiv_2d, dx, dy, oppna_rander=oppna_rander, subgrid=subgrid
    )

    # 3. Tillstånd för 2D-domän och grafnoder
    volym_2d = np.zeros((hojd, bredd), dtype=np.float64)
    h_eff_2d = np.zeros((hojd, bredd), dtype=np.float64)
    zs_2d = z_bed.copy()
    qx = np.zeros((hojd, max(0, bredd - 1)), dtype=np.float64)
    qy = np.zeros((max(0, hojd - 1), bredd), dtype=np.float64)

    nod_volym = np.zeros(n_noder, dtype=np.float64)
    nod_niva = np.asarray([n.nod.botten_rh2000 for n in noder], dtype=np.float64)
    nod_max_niva = nod_niva.copy()
    nod_akt_area = np.asarray(
        [n.vat_area_vid_niva(n.nod.botten_rh2000, cell_area_fin) for n in noder],
        dtype=np.float64,
    )

    max_zs_2d = z_bed.copy()
    max_djup_2d = np.zeros((hojd, bredd), dtype=np.float64)
    max_hast_2d = np.zeros((hojd, bredd), dtype=np.float64)
    max_fara_2d = np.zeros((hojd, bredd), dtype=np.float64)

    tid_01_fin = np.full((hojd_fin, bredd_fin), np.nan, dtype=np.float32)
    tid_02_fin = np.full((hojd_fin, bredd_fin), np.nan, dtype=np.float32)
    subgrid_ankomst = _SubgridAnkomstSparare(subgrid) if subgrid is not None else None

    in_m3 = 0.0
    ut_m3 = 0.0
    antal_steg = 0
    sum_aktiv_andel = 0.0
    t_nu = t_start
    min_dx = min(dx, dy)
    safe_area_2d = np.where(aktiv_2d, aktiv_area_2d, 1.0)
    sqrt_g = math.sqrt(GRAVITATION_M_S2)

    while t_nu < t_slut - 1e-12:
        h_max_2d = float(np.max(h_eff_2d)) if h_eff_2d.size > 0 else 0.0
        vaghast = math.sqrt(GRAVITATION_M_S2 * max(h_max_2d, min_djup_m))
        dt_cfl = cfl * min_dx / vaghast
        nasta_t = nasta_regnbrytpunkt(t_nu, tider, t_slut)
        dt = min(dt_cfl, dt_max_s, nasta_t - t_nu)
        if dt < _DT_MIN_S and (t_slut - t_nu) > _DT_MIN_S:
            dt = min(_DT_MIN_S, nasta_t - t_nu)
        if dt <= 0.0:
            break

        # 3.1 Direkt regn till 2D-celler och grafnoder
        regn_m = integrera_regn_m(t_nu, t_nu + dt, tider, intens_m_s)
        regn_summa_m += regn_m
        if regn_m > 0.0:
            if har_tak and tak_ut_m2 > 0.0:
                in_m3 += tak_ut_m2 * regn_m
                ut_m3 += tak_ut_m2 * regn_m
            if total_aktiv_2d_area > 0.0:
                volym_2d += regn_area_2d * regn_m
                in_m3 += total_aktiv_2d_area * regn_m
                if subgrid is not None:
                    zs_2d = vattenniva_fran_volym(volym_2d, subgrid)
                    h_eff_2d = np.where(aktiv_2d, volym_2d / safe_area_2d, 0.0)
                else:
                    h_eff_2d = np.where(aktiv_2d, volym_2d / safe_area_2d, 0.0)
                    zs_2d = z_bed + h_eff_2d
            if n_noder > 0:
                nod_volym += nod_areor * regn_m
                in_m3 += total_nod_area * regn_m
                for k in range(n_noder):
                    z_k = noder[k].niva_vid_volym(float(nod_volym[k]))
                    nod_niva[k] = z_k
                    nod_akt_area[k] = noder[k].vat_area_vid_niva(z_k, cell_area_fin)

        # 3.2 Gles aktiv front på 2D-domänen
        vat_2d = aktiv_2d & (h_eff_2d > min_djup_m)
        front_2d = berakna_aktiv_front(vat_2d, aktiv_2d) if gles_front else aktiv_2d
        if total_doman_area > 0.0:
            sum_aktiv_andel += float(np.sum(aktiv_area_2d[front_2d])) / total_doman_area

        # 3.3 2D lokal tröghet (Bates 2010 / de Almeida 2012) på u- och v-kanter
        if bredd > 1:
            u_akt = (vat_2d[:, :-1] | vat_2d[:, 1:]) & u_giltig if gles_front else u_giltig
            n_u_akt = int(np.count_nonzero(u_akt))
            if n_u_akt == 0:
                qx.fill(0.0)
            else:
                h_u = (
                    kant_flodesdjup_u(zs_2d, subgrid)
                    if subgrid is not None
                    else np.asarray(
                        np.maximum(0.0, np.maximum(zs_2d[:, :-1], zs_2d[:, 1:]) - z_kant_fin_u),
                        dtype=np.float64,
                    )
                )
                sx = np.asarray((zs_2d[:, 1:] - zs_2d[:, :-1]) / dx, dtype=np.float64)
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
                    )
                    qx = q_ny_u.reshape((hojd, bredd - 1))

        if hojd > 1:
            v_akt = (vat_2d[:-1, :] | vat_2d[1:, :]) & v_giltig if gles_front else v_giltig
            n_v_akt = int(np.count_nonzero(v_akt))
            if n_v_akt == 0:
                qy.fill(0.0)
            else:
                h_v = (
                    kant_flodesdjup_v(zs_2d, subgrid)
                    if subgrid is not None
                    else np.asarray(
                        np.maximum(0.0, np.maximum(zs_2d[:-1, :], zs_2d[1:, :]) - z_kant_fin_v),
                        dtype=np.float64,
                    )
                )
                sy = np.asarray((zs_2d[1:, :] - zs_2d[:-1, :]) / dy, dtype=np.float64)
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
                    )
                    qy = q_ny_v.reshape((hojd - 1, bredd))

        # 3.4 Öppna randutflöden på 2D-domänen
        q_vast = berakna_rand_q(
            h_eff_2d[:, 0],
            manning_2d[:, 0],
            rand_konfig.vast_mask,
            rand_konfig.vast_lutning,
            rand_konfig.vast_bredd_andel,
            min_djup_m,
        )
        q_ost = berakna_rand_q(
            h_eff_2d[:, -1],
            manning_2d[:, -1],
            rand_konfig.ost_mask,
            rand_konfig.ost_lutning,
            rand_konfig.ost_bredd_andel,
            min_djup_m,
        )
        q_norr = berakna_rand_q(
            h_eff_2d[0, :],
            manning_2d[0, :],
            rand_konfig.norr_mask,
            rand_konfig.norr_lutning,
            rand_konfig.norr_bredd_andel,
            min_djup_m,
        )
        q_soder = berakna_rand_q(
            h_eff_2d[-1, :],
            manning_2d[-1, :],
            rand_konfig.soder_mask,
            rand_konfig.soder_lutning,
            rand_konfig.soder_bredd_andel,
            min_djup_m,
        )

        # 3.5 Massbevarande och svängningsfri koppling mellan 2D-celler och grafnoder
        dv_kop = np.zeros(kop.antal, dtype=np.float64)
        if kop.antal > 0:
            zs_c = zs_2d[kop.rg, kop.cg]
            zs_n = nod_niva[kop.nod_idx]
            h_k = np.maximum(0.0, np.maximum(zs_c, zs_n) - kop.z_kant)
            dz = zs_c - zs_n
            akt_k = (h_k > min_djup_m) & (np.abs(dz) > 1e-12)
            if bool(np.any(akt_k)):
                hk_a = h_k[akt_k]
                dz_a = dz[akt_k]
                abs_dz = np.abs(dz_a)
                ds_a = kop.ds[akt_k]
                dl_a = kop.dl[akt_k]
                man_a = kop.manning[akt_k]

                q_man = ds_a * (1.0 / man_a) * np.power(hk_a, 5.0 / 3.0) * np.sqrt(abs_dz / dl_a)
                q_froude = ds_a * FROUDE_MAX * sqrt_g * np.power(hk_a, 1.5)
                dv_fys = dt * np.minimum(q_man, q_froude)

                a_c = aktiv_area_2d[kop.rg[akt_k], kop.cg[akt_k]]
                a_n = nod_akt_area[kop.nod_idx[akt_k]]
                a_eff = (a_c * a_n) / np.maximum(a_c + a_n, 1e-12)
                # Begränsa effektiv fallhöjd till givarens höjd ovanför tröskeln
                givare_over = np.where(
                    dz_a > 0.0,
                    np.maximum(0.0, zs_c[akt_k] - kop.z_kant[akt_k]),
                    np.maximum(0.0, zs_n[akt_k] - kop.z_kant[akt_k]),
                )
                dz_eff = np.minimum(abs_dz, givare_over)
                dv_eq = (a_eff * dz_eff) / kop.grad_2d[akt_k]
                safe_eq = np.maximum(dv_eq, 1e-18)
                dv_rel = dv_eq * (1.0 - np.exp(-dv_fys / safe_eq))

                dv_kop[akt_k] = np.sign(dz_a) * dv_rel

        # Eventuellt direkt flöde mellan två angränsande grafnoder
        dv_nn = np.zeros(nn_kop.antal, dtype=np.float64)
        if nn_kop.antal > 0:
            z1 = nod_niva[nn_kop.k1]
            z2 = nod_niva[nn_kop.k2]
            h_nn = np.maximum(0.0, np.maximum(z1, z2) - nn_kop.z_kant)
            dz_nn = z1 - z2
            akt_nn = (h_nn > min_djup_m) & (np.abs(dz_nn) > 1e-12)
            if bool(np.any(akt_nn)):
                hn = h_nn[akt_nn]
                dzn = dz_nn[akt_nn]
                abs_dzn = np.abs(dzn)
                q_man_nn = (
                    nn_kop.ds[akt_nn]
                    * (1.0 / nn_kop.manning[akt_nn])
                    * np.power(hn, 5.0 / 3.0)
                    * np.sqrt(abs_dzn / nn_kop.dl[akt_nn])
                )
                q_froude_nn = nn_kop.ds[akt_nn] * FROUDE_MAX * sqrt_g * np.power(hn, 1.5)
                dv_fys_nn = dt * np.minimum(q_man_nn, q_froude_nn)
                a1 = nod_akt_area[nn_kop.k1[akt_nn]]
                a2 = nod_akt_area[nn_kop.k2[akt_nn]]
                givare_over_nn = np.where(
                    dzn > 0.0,
                    np.maximum(0.0, z1[akt_nn] - nn_kop.z_kant[akt_nn]),
                    np.maximum(0.0, z2[akt_nn] - nn_kop.z_kant[akt_nn]),
                )
                dz_eff_nn = np.minimum(abs_dzn, givare_over_nn)
                dv_eq_nn = (
                    ((a1 * a2) / np.maximum(a1 + a2, 1e-12)) * dz_eff_nn / nn_kop.grad_nn[akt_nn]
                )
                safe_eq_nn = np.maximum(dv_eq_nn, 1e-18)
                dv_nn[akt_nn] = np.sign(dzn) * dv_eq_nn * (1.0 - np.exp(-dv_fys_nn / safe_eq_nn))

        # 3.6 Gemensam positivitetsbevarande flödesbegränsare för 2D-celler och grafnoder
        ut_vol_2d = np.zeros((hojd, bredd), dtype=np.float64)
        if bredd > 1:
            ut_vol_2d[:, :-1] += np.maximum(0.0, qx) * (dy * dt)
            ut_vol_2d[:, 1:] += np.maximum(0.0, -qx) * (dy * dt)
        if hojd > 1:
            ut_vol_2d[:-1, :] += np.maximum(0.0, qy) * (dx * dt)
            ut_vol_2d[1:, :] += np.maximum(0.0, -qy) * (dx * dt)
        ut_vol_2d[:, 0] += q_vast * (dy * dt)
        ut_vol_2d[:, -1] += q_ost * (dy * dt)
        ut_vol_2d[0, :] += q_norr * (dx * dt)
        ut_vol_2d[-1, :] += q_soder * (dx * dt)
        if kop.antal > 0:
            np.add.at(ut_vol_2d, (kop.rg, kop.cg), np.maximum(0.0, dv_kop))

        over_2d = ut_vol_2d > volym_2d
        if bool(np.any(over_2d)):
            skala_2d = np.ones((hojd, bredd), dtype=np.float64)
            skala_2d[over_2d] = volym_2d[over_2d] / ut_vol_2d[over_2d]
            if bredd > 1:
                qx = np.where(qx >= 0.0, qx * skala_2d[:, :-1], qx * skala_2d[:, 1:])
            if hojd > 1:
                qy = np.where(qy >= 0.0, qy * skala_2d[:-1, :], qy * skala_2d[1:, :])
            q_vast = q_vast * skala_2d[:, 0]
            q_ost = q_ost * skala_2d[:, -1]
            q_norr = q_norr * skala_2d[0, :]
            q_soder = q_soder * skala_2d[-1, :]
            if kop.antal > 0:
                dv_kop = np.where(dv_kop > 0.0, dv_kop * skala_2d[kop.rg, kop.cg], dv_kop)

        if n_noder > 0:
            ut_vol_nod = np.zeros(n_noder, dtype=np.float64)
            if kop.antal > 0:
                np.add.at(ut_vol_nod, kop.nod_idx, np.maximum(0.0, -dv_kop))
            if nn_kop.antal > 0:
                np.add.at(ut_vol_nod, nn_kop.k1, np.maximum(0.0, dv_nn))
                np.add.at(ut_vol_nod, nn_kop.k2, np.maximum(0.0, -dv_nn))
            over_nod = ut_vol_nod > nod_volym
            if bool(np.any(over_nod)):
                skala_nod = np.ones(n_noder, dtype=np.float64)
                skala_nod[over_nod] = nod_volym[over_nod] / ut_vol_nod[over_nod]
                if kop.antal > 0:
                    dv_kop = np.where(dv_kop < 0.0, dv_kop * skala_nod[kop.nod_idx], dv_kop)
                if nn_kop.antal > 0:
                    dv_nn = np.where(
                        dv_nn >= 0.0,
                        dv_nn * skala_nod[nn_kop.k1],
                        dv_nn * skala_nod[nn_kop.k2],
                    )

        # 3.7 Exakt massbevarande volymuppdatering
        if bredd > 1:
            dv_x = qx * (dy * dt)
            volym_2d[:, :-1] -= dv_x
            volym_2d[:, 1:] += dv_x
        if hojd > 1:
            dv_y = qy * (dx * dt)
            volym_2d[:-1, :] -= dv_y
            volym_2d[1:, :] += dv_y

        dv_vast = q_vast * (dy * dt)
        dv_ost = q_ost * (dy * dt)
        dv_norr = q_norr * (dx * dt)
        dv_soder = q_soder * (dx * dt)
        volym_2d[:, 0] -= dv_vast
        volym_2d[:, -1] -= dv_ost
        volym_2d[0, :] -= dv_norr
        volym_2d[-1, :] -= dv_soder
        ut_m3 += float(
            np.sum(dv_vast) + np.sum(dv_ost) + np.sum(dv_norr) + np.sum(dv_soder)
        )

        if kop.antal > 0:
            np.add.at(volym_2d, (kop.rg, kop.cg), -dv_kop)
            np.add.at(nod_volym, kop.nod_idx, dv_kop)
        if nn_kop.antal > 0:
            np.add.at(nod_volym, nn_kop.k1, -dv_nn)
            np.add.at(nod_volym, nn_kop.k2, dv_nn)

        np.maximum(volym_2d, 0.0, out=volym_2d)
        volym_2d[~aktiv_2d] = 0.0
        np.maximum(nod_volym, 0.0, out=nod_volym)

        # 3.8 Uppdatera vattennivåer, djup, hastighet, fara och ankomsttider
        h_fore_2d = h_eff_2d
        if subgrid is not None:
            zs_2d = vattenniva_fran_volym(volym_2d, subgrid)
            h_eff_2d = np.where(aktiv_2d, volym_2d / safe_area_2d, 0.0)
        else:
            h_eff_2d = np.where(aktiv_2d, volym_2d / safe_area_2d, 0.0)
            zs_2d = z_bed + h_eff_2d

        t_nu += dt
        antal_steg += 1

        for k in range(n_noder):
            z_k = noder[k].niva_vid_volym(float(nod_volym[k]))
            nod_niva[k] = z_k
            if z_k > float(nod_max_niva[k]):
                nod_max_niva[k] = z_k
            nod_akt_area[k] = noder[k].vat_area_vid_niva(z_k, cell_area_fin)
            noder[k].uppdatera_ankomst(z_k, t_nu, tid_01_fin, tid_02_fin)

        np.maximum(max_zs_2d, zs_2d, out=max_zs_2d)
        np.maximum(max_djup_2d, h_eff_2d, out=max_djup_2d)

        q_cell_x = np.zeros((hojd, bredd), dtype=np.float64)
        if bredd > 1:
            q_cell_x[:, :-1] += 0.5 * qx
            q_cell_x[:, 1:] += 0.5 * qx
        q_cell_x[:, 0] -= 0.5 * q_vast
        q_cell_x[:, -1] += 0.5 * q_ost

        q_cell_y = np.zeros((hojd, bredd), dtype=np.float64)
        if hojd > 1:
            q_cell_y[:-1, :] += 0.5 * qy
            q_cell_y[1:, :] += 0.5 * qy
        q_cell_y[0, :] -= 0.5 * q_norr
        q_cell_y[-1, :] += 0.5 * q_soder

        if kop.antal > 0 and dt > 0.0:
            q_kop_x = 0.5 * (dv_kop / (dy * dt)) * kop.dir_x
            q_kop_y = 0.5 * (dv_kop / (dx * dt)) * kop.dir_y
            np.add.at(q_cell_x, (kop.rg, kop.cg), q_kop_x)
            np.add.at(q_cell_y, (kop.rg, kop.cg), q_kop_y)

        q_mag = np.hypot(q_cell_x, q_cell_y)
        h_vel_2d = np.maximum(h_fore_2d, h_eff_2d)
        vat_nu = aktiv_2d & (h_vel_2d > min_djup_m)
        if bool(np.any(vat_nu)):
            hast = np.zeros((hojd, bredd), dtype=np.float64)
            hast[vat_nu] = q_mag[vat_nu] / h_vel_2d[vat_nu]
            np.maximum(max_hast_2d, hast, out=max_hast_2d)
            fara_steg = np.where(vat_nu, q_mag, 0.0)
            np.maximum(max_fara_2d, fara_steg, out=max_fara_2d)

        if subgrid_ankomst is not None:
            subgrid_ankomst.uppdatera(zs_2d, t_nu)
        else:
            ny_01 = aktiv_2d & np.isnan(tid_01_fin) & (h_eff_2d >= 0.1)
            if bool(np.any(ny_01)):
                tid_01_fin[ny_01] = np.float32(t_nu)
            ny_02 = aktiv_2d & np.isnan(tid_02_fin) & (h_eff_2d >= 0.2)
            if bool(np.any(ny_02)):
                tid_02_fin[ny_02] = np.float32(t_nu)

    # 4. Sammanfoga 2D-domänens och grafnodernas resultat till gemensamt K6-raster
    if subgrid is not None:
        max_djup_ut = projicera_djup_till_fin(max_zs_2d, subgrid)
        slut_djup_ut = projicera_djup_till_fin(zs_2d, subgrid)
        max_hast_ut = projicera_falt_till_fin(max_hast_2d, subgrid, byggnad_varde=0.0)
        fara_ut = projicera_falt_till_fin(max_fara_2d, subgrid, byggnad_varde=0.0)
        assert subgrid_ankomst is not None
        tid_01_ut = np.where(aktiv_2d_fin, subgrid_ankomst.tid_01, tid_01_fin)
        tid_02_ut = np.where(aktiv_2d_fin, subgrid_ankomst.tid_02, tid_02_fin)
    else:
        max_djup_ut = np.asarray(max_djup_2d, dtype=np.float32)
        slut_djup_ut = np.asarray(h_eff_2d, dtype=np.float32)
        max_hast_ut = np.asarray(max_hast_2d, dtype=np.float32)
        fara_ut = np.asarray(max_fara_2d, dtype=np.float32)
        tid_01_ut = tid_01_fin
        tid_02_ut = tid_02_fin

    for k in range(n_noder):
        rr = noder[k].cell_rader
        cc = noder[k].cell_kolumner
        z_sub = eff_z64[rr, cc]
        max_djup_ut[rr, cc] = np.asarray(
            np.maximum(0.0, float(nod_max_niva[k]) - z_sub),
            dtype=np.float32,
        )
        slut_djup_ut[rr, cc] = np.asarray(
            np.maximum(0.0, float(nod_niva[k]) - z_sub),
            dtype=np.float32,
        )
        max_hast_ut[rr, cc] = np.float32(0.0)
        fara_ut[rr, cc] = np.float32(0.0)

    lagrat_m3 = float(np.sum(volym_2d)) + float(np.sum(nod_volym))
    volymfel_rel = abs(in_m3 - ut_m3 - lagrat_m3) / in_m3 if in_m3 > 0.0 else 0.0
    aktiv_cell_andel = sum_aktiv_andel / float(antal_steg) if antal_steg > 0 else 0.0
    kortid_s = time.perf_counter() - t_klocka_0

    return TvadResultat(
        max_djup_m=np.asarray(max_djup_ut, dtype=np.float32),
        max_hastighet_m_s=np.asarray(max_hast_ut, dtype=np.float32),
        fara_m2_s=np.asarray(fara_ut, dtype=np.float32),
        tid_till_0_1m_s=np.asarray(tid_01_ut, dtype=np.float32),
        tid_till_0_2m_s=np.asarray(tid_02_ut, dtype=np.float32),
        slut_djup_m=np.asarray(slut_djup_ut, dtype=np.float32),
        in_m3=in_m3,
        lagrat_m3=lagrat_m3,
        ut_m3=ut_m3,
        volymfel_rel=volymfel_rel,
        antal_tidssteg=antal_steg,
        kortid_s=kortid_s,
        niva="hybrid",
        aktiv_cell_andel=aktiv_cell_andel,
        regn_mark_m3=regn_mark_area * regn_summa_m,
        regn_tak_m3=tak_m2 * regn_summa_m,
        takvatten=tv is not None,
        takvatten_ut_rand_m3=tak_ut_m2 * regn_summa_m,
        takvatten_till_ledning_m3=tak_ledning_m2 * regn_summa_m,
    )


def jamfor_hybrid_mot_full2d(
    full2d: TvadResultat,
    hybrid: TvadResultat,
    *,
    vat_grans_m: float = 0.10,
) -> HybridJamforelse:
    """Beräknar valideringsmått enligt `docs/VALIDERING.md` för hybrid mot full 2D."""
    if full2d.max_djup_m.shape != hybrid.max_djup_m.shape:
        raise TvadFel(
            f"Oförenliga rasterformer: full2d={full2d.max_djup_m.shape}, "
            f"hybrid={hybrid.max_djup_m.shape}."
        )
    if not math.isfinite(vat_grans_m) or vat_grans_m <= 0.0:
        raise TvadFel(f"vat_grans_m måste vara > 0, fick {vat_grans_m}.")

    d_full = np.asarray(full2d.max_djup_m, dtype=np.float64)
    d_hyb = np.asarray(hybrid.max_djup_m, dtype=np.float64)

    vat_full = d_full > vat_grans_m
    vat_hyb = d_hyb > vat_grans_m
    union = vat_full | vat_hyb
    snitt = vat_full & vat_hyb

    n_union = int(np.count_nonzero(union))
    n_snitt = int(np.count_nonzero(snitt))
    traffad_yta_iou = float(n_snitt) / float(n_union) if n_union > 0 else 1.0

    if n_union > 0:
        diff_vat = np.abs(d_hyb[union] - d_full[union])
        maxdjupsfel_mae_m = float(np.mean(diff_vat))
        maxdjupsfel_max_m = float(np.max(diff_vat))
    else:
        svag_vat = (d_full > 1e-4) | (d_hyb > 1e-4)
        if bool(np.any(svag_vat)):
            diff_svag = np.abs(d_hyb[svag_vat] - d_full[svag_vat])
            maxdjupsfel_mae_m = float(np.mean(diff_svag))
            maxdjupsfel_max_m = float(np.max(diff_svag))
        else:
            maxdjupsfel_mae_m = 0.0
            maxdjupsfel_max_m = 0.0

    t_full = np.asarray(full2d.tid_till_0_1m_s, dtype=np.float64)
    t_hyb = np.asarray(hybrid.tid_till_0_1m_s, dtype=np.float64)
    bada_ank = np.isfinite(t_full) & np.isfinite(t_hyb)
    n_ank = int(np.count_nonzero(bada_ank))
    ankomsttid_mae_s: float | None = (
        float(np.mean(np.abs(t_hyb[bada_ank] - t_full[bada_ank]))) if n_ank > 0 else None
    )

    speedup = (
        float(full2d.kortid_s) / float(hybrid.kortid_s)
        if hybrid.kortid_s > 0.0
        else math.inf
    )

    return HybridJamforelse(
        maxdjupsfel_mae_m=maxdjupsfel_mae_m,
        maxdjupsfel_max_m=maxdjupsfel_max_m,
        traffad_yta_iou=traffad_yta_iou,
        ankomsttid_mae_s=ankomsttid_mae_s,
        volymfel_full2d_rel=float(full2d.volymfel_rel),
        volymfel_hybrid_rel=float(hybrid.volymfel_rel),
        kortid_full2d_s=float(full2d.kortid_s),
        kortid_hybrid_s=float(hybrid.kortid_s),
        speedup_faktor=speedup,
        antal_vata_celler_union=n_union,
        antal_ankomst_celler=n_ank,
    )
