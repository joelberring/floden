"""1D-rännstenslänk på 3D-vektorlinje kopplad till 2D lokal tröghet (massbevarande).

Representerar en gatas rännsten som en 1D-kanal direkt längs 3D-kantstenens vektorlinje
(`LineStringZ`) med sann vektorlängd `ds_m` – helt oberoende av gatans vinkel mot
2D-rutnätet. Eliminerar därmed både trappstegsbromsning i fysiken och 90°-trappsteg i
visualiseringen.

Tvärsnittsgeometri:
    Triangulär rännsten med vertikal kantsten och körbanans tvärfall `s_t` (standard 2,5 %):
    - Våt bredd `B(h) = min(h / s_t, B_max)`
    - Våt area `A(h) = h^2 / (2 s_t)` för `h <= B_max * s_t`
    - Våt perimeter `P(h) = h * (1 + sqrt(1 + s_t^-2))`

Lateral 1D<->2D-koppling (exakt massbevarande i varje tidssteg):
    - Mot körbanesidan: fritt flödesutbyte drivet av vattenståndsskillnad `eta_1D - eta_2D`.
    - Mot gångbane-/tomtsidan: bredkrönat överfall först när vattenståndet överstiger
      segmentets 3D-krönhöjd `z_kron_m` (låg vid sänkt infart -> vatten lämnar gatan exakt där).
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from markmodell.k1_skrivare import (
    EPSG_SWEREF99_1800,
    _gpkg_linestring_z_blob,
    _initiera_gpkg_bas,
)
from sankgraf.flodesvagar import MANNING_N_PER_MARKSLAG
from sankgraf.hierarki import _berakna_kantbarriarer
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo
from tvad.lokal_troghet import (
    GRAVITATION_M_S2,
    integrera_regn_m,
    nasta_regnbrytpunkt,
)

CFL_STANDARD: float = 0.7
MIN_DJUP_M: float = 1e-4

OVERFALL_KOEFF_CW: float = 1.70  # Bredkrönat överfall: Q = Cw * b * dh^(3/2) [m^(1/2)/s]
MAX_RANNSTEN_BREDD_M: float = 3.0
STANDARD_RANNSTEN_MANNING: float = 0.015
STANDARD_TVARFALL: float = 0.025


class RannstenFel(Exception):
    """Fel vid uppbyggnad eller simulering av 1D-rännstenslänk."""


@dataclass(frozen=True)
class RannstenSegment:
    """Ett 1D-segment längs en 3D-kantstenslinje."""

    index: int
    p0: tuple[float, float]
    p1: tuple[float, float]
    ds_m: float
    s_mitt_m: float
    z_botten_m: float
    z_kron_m: float
    tvarfall: float
    ar_sankt: bool
    korbana_rc: tuple[int, int] | None
    gangbana_rc: tuple[int, int] | None
    korbana_celler: tuple[tuple[int, int], ...] = ()

    @property
    def h_kant_m(self) -> float:
        return max(0.0, self.z_kron_m - self.z_botten_m)


@dataclass(frozen=True)
class RannstenLank:
    """En sammanhängande 1D-rännstenskanal diskretiserad längs 3D-kantstenen."""

    segment: tuple[RannstenSegment, ...]
    manning_n: float = STANDARD_RANNSTEN_MANNING
    max_bredd_m: float = MAX_RANNSTEN_BREDD_M
    oppen_nedstroms: bool = True

    @property
    def antal(self) -> int:
        return len(self.segment)

    @property
    def total_langd_m(self) -> float:
        return float(sum(s.ds_m for s in self.segment))


@dataclass(frozen=True)
class Kopplad1D2DResultat:
    """Resultat från massbevarande 1D-rännsten + 2D lokal tröghet."""

    lank: RannstenLank
    max_djup_2d_m: NDArray[np.float32]
    slut_djup_2d_m: NDArray[np.float32]
    max_djup_1d_m: NDArray[np.float64]
    slut_djup_1d_m: NDArray[np.float64]
    max_flode_1d_m3_s: NDArray[np.float64]
    overfall_gangbana_m3: NDArray[np.float64]
    ankomst_tid_1d_s: NDArray[np.float64]
    total_regn_m3: float
    lagrat_2d_m3: float
    lagrat_1d_m3: float
    utflode_2d_m3: float
    utflode_1d_m3: float
    volymfel_rel: float
    antal_steg: int
    kortid_s: float

    @property
    def overfall_sankt_m3(self) -> float:
        """Total vattenvolym som lämnat 1D-rännstenen via sänkta kantstenssegment (infarter)."""
        return float(
            sum(
                self.overfall_gangbana_m3[i]
                for i, seg in enumerate(self.lank.segment)
                if seg.ar_sankt
            )
        )

    @property
    def overfall_hog_m3(self) -> float:
        """Total vattenvolym som spillt över fullhöga (ej sänkta) kantstenssegment."""
        return float(
            sum(
                self.overfall_gangbana_m3[i]
                for i, seg in enumerate(self.lank.segment)
                if not seg.ar_sankt
            )
        )


def triangular_area_och_radie(
    h_m: NDArray[np.float64],
    tvarfall: NDArray[np.float64],
    max_bredd_m: float = MAX_RANNSTEN_BREDD_M,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Exakt våt area `A(h)` och hydraulisk radie `R_h(h)` för triangulär rännsten."""
    h = np.maximum(h_m, 0.0)
    h_b = max_bredd_m * tvarfall
    a_b = 0.5 * max_bredd_m * h_b
    under = h <= h_b
    area = np.where(under, 0.5 * h * h / tvarfall, a_b + max_bredd_m * (h - h_b))
    hyp = np.sqrt(1.0 + 1.0 / (tvarfall * tvarfall))
    perim_b = h_b * (1.0 + hyp)
    perim = np.where(under, h * (1.0 + hyp), perim_b + (h - h_b))
    rh = np.where(perim > 1e-12, area / np.maximum(perim, 1e-12), 0.0)
    return area, rh


def djup_fran_area(
    area_m2: NDArray[np.float64],
    tvarfall: NDArray[np.float64],
    max_bredd_m: float = MAX_RANNSTEN_BREDD_M,
) -> NDArray[np.float64]:
    """Invers `h(A)` för triangulär rännsten (exakt sluten form)."""
    a = np.maximum(area_m2, 0.0)
    h_b = max_bredd_m * tvarfall
    a_b = 0.5 * max_bredd_m * h_b
    under = a <= a_b
    return np.where(under, np.sqrt(2.0 * tvarfall * a), h_b + (a - a_b) / max_bredd_m)


def _cell_vid_punkt(
    e_m: float,
    n_m: float,
    geo: GeoTransformInfo,
    aktiv: NDArray[np.bool_],
) -> tuple[int, int] | None:
    c = math.floor((e_m - geo.x_min) / float(geo.pixelstorlek_x))
    r = math.floor((geo.y_max - n_m) / float(geo.pixelstorlek_y))
    if 0 <= r < geo.hojd and 0 <= c < geo.bredd and bool(aktiv[r, c]):
        return (r, c)
    return None


def diskretisera_rannsten_1d(
    brytlinjer: Sequence[BrytlinjePost],
    mark_z: NDArray[np.float32] | NDArray[np.float64],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    *,
    ds_mal_m: float = 1.5,
    manning_n: float = STANDARD_RANNSTEN_MANNING,
    standard_tvarfall: float = STANDARD_TVARFALL,
    korbana_sida: str = "vanster",
    max_bredd_m: float = MAX_RANNSTEN_BREDD_M,
    oppen_nedstroms: bool = True,
) -> RannstenLank:
    """Diskretiserar en sekvens av 3D-kantstenslinjer till en sammanhängande `RannstenLank`.

    `korbana_sida` är `"vanster"` eller `"hoger"` sett i linjens riktning (från första till
    sista punkt). Varje segment får exakt sann vektorlängd `ds_m`, interpolerad `z_botten_m`
    och `z_kron_m`, samt kopplade 2D-celler på körbane- respektive gångbanesidan.
    """
    if ds_mal_m <= 0.0:
        raise RannstenFel(f"ds_mal_m måste vara > 0, fick {ds_mal_m}.")
    if korbana_sida not in ("vanster", "hoger"):
        raise RannstenFel(
            f"korbana_sida måste vara 'vanster' eller 'hoger', fick {korbana_sida!r}."
        )

    aktiv = (markslag != MARKSLAG_BYGGNAD) & np.isfinite(mark_z)
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    sid_avstand = 0.75 * max(dx, dy)
    sid_tecken = 1.0 if korbana_sida == "vanster" else -1.0

    segment: list[RannstenSegment] = []
    s_ack = 0.0
    seg_idx = 0

    for post in brytlinjer:
        if len(post.koordinater) < 2:
            continue
        ar_sankt = post.typ == "sankt_kantsten" or float(post.hojd_m) <= 0.04
        tf = float(post.tvarfall) if post.tvarfall is not None else standard_tvarfall
        zk_seq = post.z_kron_rh2000
        zb_seq = post.z_botten_rh2000

        for i in range(len(post.koordinater) - 1):
            e0, n0 = post.koordinater[i]
            e1, n1 = post.koordinater[i + 1]
            langd = math.hypot(e1 - e0, n1 - n0)
            if langd <= 1e-9:
                continue
            n_del = max(1, round(langd / ds_mal_m))
            ds = langd / n_del
            tx = (e1 - e0) / langd
            ty = (n1 - n0) / langd
            # Vänsternormal i (E, N): (-ty, tx)
            nx_v = -ty * sid_tecken
            ny_v = tx * sid_tecken

            for k in range(n_del):
                f0 = k / n_del
                f1 = (k + 1) / n_del
                fm = 0.5 * (f0 + f1)
                pe0 = e0 + f0 * (e1 - e0)
                pn0 = n0 + f0 * (n1 - n0)
                pe1 = e0 + f1 * (e1 - e0)
                pn1 = n0 + f1 * (n1 - n0)
                pem = e0 + fm * (e1 - e0)
                pnm = n0 + fm * (n1 - n0)

                kor_rc = _cell_vid_punkt(
                    pem + sid_avstand * nx_v, pnm + sid_avstand * ny_v, geo, aktiv
                )
                gang_rc = _cell_vid_punkt(
                    pem - sid_avstand * nx_v, pnm - sid_avstand * ny_v, geo, aktiv
                )

                if zb_seq is not None and len(zb_seq) == len(post.koordinater):
                    zb = float(zb_seq[i]) + fm * (float(zb_seq[i + 1]) - float(zb_seq[i]))
                elif kor_rc is not None:
                    zb = float(mark_z[kor_rc[0], kor_rc[1]])
                else:
                    zb = 0.0

                if zk_seq is not None and len(zk_seq) == len(post.koordinater):
                    zk = float(zk_seq[i]) + fm * (float(zk_seq[i + 1]) - float(zk_seq[i]))
                else:
                    zk = zb + float(post.hojd_m)

                segment.append(
                    RannstenSegment(
                        index=seg_idx,
                        p0=(pe0, pn0),
                        p1=(pe1, pn1),
                        ds_m=ds,
                        s_mitt_m=s_ack + 0.5 * ds,
                        z_botten_m=zb,
                        z_kron_m=zk,
                        tvarfall=tf,
                        ar_sankt=ar_sankt,
                        korbana_rc=kor_rc,
                        gangbana_rc=gang_rc,
                    )
                )
                s_ack += ds
                seg_idx += 1

    if not segment:
        raise RannstenFel("Inga giltiga 1D-rännstenssegment kunde skapas ur brytlinjerna.")

    # Koppla ALLA aktiva körbaneceller inom en celldiagonal (1,45*cell) längs rännstenskanten
    # till sitt närmaste 1D-segment så att inga sneda 45°-hörnceller fastnar i 2D-trappsteg.
    hojd, bredd = mark_z.shape
    max_band = 1.45 * max(dx, dy)
    cell_till_seg: dict[tuple[int, int], tuple[float, int]] = {}
    for seg in segment:
        e0, n0 = seg.p0
        e1, n1 = seg.p1
        seg_dx, seg_dy = e1 - e0, n1 - n0
        langd = math.hypot(seg_dx, seg_dy)
        if langd <= 1e-12:
            continue
        tx, ty = seg_dx / langd, seg_dy / langd
        nx_v = -ty * sid_tecken
        ny_v = tx * sid_tecken
        c_min = max(0, math.floor((min(e0, e1) - max_band - geo.x_min) / dx))
        c_max = min(bredd - 1, math.ceil((max(e0, e1) + max_band - geo.x_min) / dx))
        r_min = max(0, math.floor((geo.y_max - (max(n0, n1) + max_band)) / dy))
        r_max = min(hojd - 1, math.ceil((geo.y_max - (min(n0, n1) - max_band)) / dy))
        for r in range(r_min, r_max + 1):
            ny = geo.y_max - (r + 0.5) * dy
            for c in range(c_min, c_max + 1):
                if not aktiv[r, c]:
                    continue
                ex = geo.x_min + (c + 0.5) * dx
                rel_e, rel_n = ex - e0, ny - n0
                s_lok = rel_e * tx + rel_n * ty
                if s_lok < -0.5 * dx or s_lok > langd + 0.5 * dx:
                    continue
                d_norm = rel_e * nx_v + rel_n * ny_v
                if 0.0 < d_norm <= max_band:
                    s_kl = max(0.0, min(langd, s_lok))
                    px, py = e0 + s_kl * tx, n0 + s_kl * ty
                    d2 = (ex - px) ** 2 + (ny - py) ** 2
                    tidigare = cell_till_seg.get((r, c))
                    if tidigare is None or d2 < tidigare[0]:
                        cell_till_seg[(r, c)] = (d2, seg.index)

    celler_per_seg: list[list[tuple[int, int]]] = [[] for _ in segment]
    for rc, (_, s_idx) in cell_till_seg.items():
        celler_per_seg[s_idx].append(rc)

    slut_segment: list[RannstenSegment] = []
    for seg in segment:
        kopplade = tuple(sorted(celler_per_seg[seg.index]))
        if not kopplade and seg.korbana_rc is not None:
            kopplade = (seg.korbana_rc,)
        slut_segment.append(
            RannstenSegment(
                index=seg.index,
                p0=seg.p0,
                p1=seg.p1,
                ds_m=seg.ds_m,
                s_mitt_m=seg.s_mitt_m,
                z_botten_m=seg.z_botten_m,
                z_kron_m=seg.z_kron_m,
                tvarfall=seg.tvarfall,
                ar_sankt=seg.ar_sankt,
                korbana_rc=seg.korbana_rc,
                gangbana_rc=seg.gangbana_rc,
                korbana_celler=kopplade,
            )
        )

    return RannstenLank(
        segment=tuple(slut_segment),
        manning_n=manning_n,
        max_bredd_m=max_bredd_m,
        oppen_nedstroms=oppen_nedstroms,
    )


def kor_kopplad_1d_2d(
    mark_z: NDArray[np.float32] | NDArray[np.float64],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    lank: RannstenLank,
    *,
    brytlinjer: Sequence[BrytlinjePost] = (),
    slut_tid_s: float | None = None,
    cfl: float = CFL_STANDARD,
    dt_max_s: float = 0.5,
    dt_min_s: float = 1e-3,
    min_djup_m: float = MIN_DJUP_M,
    froude_max: float = 1.0,
    oppna_rander: bool = True,
    regn_mask: NDArray[np.bool_] | None = None,
    ankomst_tr_djup_m: float = 0.01,
) -> Kopplad1D2DResultat:
    """Kör massbevarande kopplad 1D-rännsten + 2D lokal tröghet med täta 3D-kantbarriärer."""
    t_start = time.perf_counter()
    hojd, bredd = mark_z.shape
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    cell_area = dx * dy

    tider = [float(t) for t in regn_tid_s]
    intens_m_s = [float(v) / 3_600_000.0 for v in regn_intensitet_mm_h]
    slut = float(slut_tid_s) if slut_tid_s is not None else tider[-1]

    aktiv = (markslag != MARKSLAG_BYGGNAD) & np.isfinite(mark_z)
    z_bed = np.where(aktiv, np.asarray(mark_z, dtype=np.float64), 0.0)
    regn_aktiv = aktiv & np.asarray(regn_mask, dtype=bool) if regn_mask is not None else aktiv

    manning = np.full((hojd, bredd), 0.03, dtype=np.float64)
    for kod, n_val in MANNING_N_PER_MARKSLAG.items():
        manning[markslag == kod] = n_val
    manning_u = 0.5 * (manning[:, :-1] + manning[:, 1:]) if bredd > 1 else np.zeros((hojd, 0))
    manning_v = 0.5 * (manning[:-1, :] + manning[1:, :]) if hojd > 1 else np.zeros((0, bredd))

    z_kant_u = np.maximum(z_bed[:, :-1], z_bed[:, 1:]) if bredd > 1 else np.zeros((hojd, 0))
    z_kant_v = np.maximum(z_bed[:-1, :], z_bed[1:, :]) if hojd > 1 else np.zeros((0, bredd))
    if brytlinjer:
        barriarer = _berakna_kantbarriarer(z_bed.ravel(), aktiv.ravel(), geo, brytlinjer)
        for (u_idx, v_idx), kant_z in barriarer.items():
            r0, c0 = divmod(u_idx, bredd)
            r1, c1 = divmod(v_idx, bredd)
            if r0 == r1 and c1 == c0 + 1 and bredd > 1:
                z_kant_u[r0, c0] = max(float(z_kant_u[r0, c0]), kant_z)
            elif c0 == c1 and r1 == r0 + 1 and hojd > 1:
                z_kant_v[r0, c0] = max(float(z_kant_v[r0, c0]), kant_z)

    # 2D-tillstånd
    h2d = np.zeros((hojd, bredd), dtype=np.float64)
    qx = np.zeros((hojd, max(0, bredd - 1)), dtype=np.float64)
    qy = np.zeros((max(0, hojd - 1), bredd), dtype=np.float64)
    max_h2d = np.zeros((hojd, bredd), dtype=np.float64)

    # 1D-rännstensvektorer (exakta vektorlängder ds_m, oberoende av gatans vinkel)
    n_seg = lank.antal
    ds_1d = np.array([s.ds_m for s in lank.segment], dtype=np.float64)
    zb_1d = np.array([s.z_botten_m for s in lank.segment], dtype=np.float64)
    zk_1d = np.array([s.z_kron_m for s in lank.segment], dtype=np.float64)
    tf_1d = np.array([s.tvarfall for s in lank.segment], dtype=np.float64)
    ds_face = 0.5 * (ds_1d[:-1] + ds_1d[1:]) if n_seg > 1 else np.zeros(0, dtype=np.float64)
    z_face = np.maximum(zb_1d[:-1], zb_1d[1:]) if n_seg > 1 else np.zeros(0, dtype=np.float64)
    tf_face = 0.5 * (tf_1d[:-1] + tf_1d[1:]) if n_seg > 1 else np.zeros(0, dtype=np.float64)
    lutning_nedstroms = (
        max(1e-4, (zb_1d[-2] - zb_1d[-1]) / max(ds_face[-1], 1e-6)) if n_seg > 1 else 0.01
    )

    vol_1d = np.zeros(n_seg, dtype=np.float64)
    h_1d = np.zeros(n_seg, dtype=np.float64)
    q_1d = np.zeros(max(0, n_seg - 1), dtype=np.float64)
    max_h1d = np.zeros(n_seg, dtype=np.float64)
    max_q1d = np.zeros(n_seg, dtype=np.float64)
    overfall_gang = np.zeros(n_seg, dtype=np.float64)
    ankomst_1d = np.full(n_seg, np.nan, dtype=np.float64)

    kor_koppling: list[tuple[int, int, int, float]] = []
    for i, s in enumerate(lank.segment):
        celler = (
            s.korbana_celler
            if s.korbana_celler
            else ((s.korbana_rc,) if s.korbana_rc is not None else ())
        )
        if celler:
            bredd_per_cell = max(dx, s.ds_m / len(celler))
            for r_k, c_k in celler:
                kor_koppling.append((i, r_k, c_k, bredd_per_cell))
    gang_koppling = [
        (i, s.gangbana_rc[0], s.gangbana_rc[1])
        for i, s in enumerate(lank.segment)
        if s.gangbana_rc is not None
    ]

    tid = 0.0
    antal_steg = 0
    total_regn_m3 = 0.0
    utflode_2d_m3 = 0.0
    utflode_1d_m3 = 0.0
    g = GRAVITATION_M_S2
    n1d_2 = lank.manning_n * lank.manning_n
    ds_min = float(np.min(ds_1d))

    aktiv_u = aktiv[:, :-1] & aktiv[:, 1:] if bredd > 1 else np.zeros((hojd, 0), dtype=bool)
    aktiv_v = aktiv[:-1, :] & aktiv[1:, :] if hojd > 1 else np.zeros((0, bredd), dtype=bool)

    while tid < slut - 1e-12:
        h_max = max(float(np.max(h2d)), float(np.max(h_1d)))
        if h_max > min_djup_m:
            c_v = math.sqrt(g * h_max)
            dt_2d = cfl * min(dx, dy) / c_v
            dt_1d = cfl * ds_min / c_v
            dt = min(dt_max_s, dt_2d, dt_1d)
        else:
            dt = dt_max_s
        nasta_bryt = nasta_regnbrytpunkt(tid, tider, slut)
        dt = max(dt_min_s, min(dt, nasta_bryt - tid))
        if tid + dt > slut:
            dt = slut - tid

        # 1. Regn på aktiva 2D-regnceller
        regn_m = integrera_regn_m(tid, tid + dt, tider, intens_m_s)
        if regn_m > 0.0:
            h2d[regn_aktiv] += regn_m
            total_regn_m3 += regn_m * float(np.count_nonzero(regn_aktiv)) * cell_area

        # 2. 2D lokal tröghet (Bates 2010)
        zs2d = z_bed + h2d
        if bredd > 1:
            hf_u = np.maximum(zs2d[:, :-1], zs2d[:, 1:]) - z_kant_u
            vat_u = aktiv_u & (hf_u > min_djup_m)
            su = (zs2d[:, :-1] - zs2d[:, 1:]) / dx
            hv = np.where(vat_u, hf_u, 1.0)
            q_rhs = qx + g * hv * dt * su
            c_fr = g * dt * (manning_u * manning_u) / (hv * hv * np.cbrt(hv))
            q_ny = (2.0 * q_rhs) / (1.0 + np.sqrt(1.0 + 4.0 * c_fr * np.abs(q_rhs)))
            q_lim = froude_max * hv * np.sqrt(g * hv)
            qx = np.where(vat_u, np.clip(q_ny, -q_lim, q_lim), 0.0)
        if hojd > 1:
            hf_v = np.maximum(zs2d[:-1, :], zs2d[1:, :]) - z_kant_v
            vat_v = aktiv_v & (hf_v > min_djup_m)
            sv = (zs2d[:-1, :] - zs2d[1:, :]) / dy
            hv = np.where(vat_v, hf_v, 1.0)
            q_rhs = qy + g * hv * dt * sv
            c_fr = g * dt * (manning_v * manning_v) / (hv * hv * np.cbrt(hv))
            q_ny = (2.0 * q_rhs) / (1.0 + np.sqrt(1.0 + 4.0 * c_fr * np.abs(q_rhs)))
            q_lim = froude_max * hv * np.sqrt(g * hv)
            qy = np.where(vat_v, np.clip(q_ny, -q_lim, q_lim), 0.0)

        # Massbevarande utflödesbegränsning i 2D
        ut_2d = np.zeros((hojd, bredd), dtype=np.float64)
        if bredd > 1:
            ut_2d[:, :-1] += np.maximum(qx, 0.0) * dy * dt
            ut_2d[:, 1:] += np.maximum(-qx, 0.0) * dy * dt
        if hojd > 1:
            ut_2d[:-1, :] += np.maximum(qy, 0.0) * dx * dt
            ut_2d[1:, :] += np.maximum(-qy, 0.0) * dx * dt
        tillg_2d = h2d * cell_area
        skala = np.where(ut_2d > tillg_2d, tillg_2d / np.maximum(ut_2d, 1e-18), 1.0)
        if bredd > 1:
            qx = np.where(qx > 0.0, qx * skala[:, :-1], qx * skala[:, 1:])
        if hojd > 1:
            qy = np.where(qy > 0.0, qy * skala[:-1, :], qy * skala[1:, :])

        dv_2d = np.zeros((hojd, bredd), dtype=np.float64)
        if bredd > 1:
            fx = qx * dy * dt
            dv_2d[:, :-1] -= fx
            dv_2d[:, 1:] += fx
        if hojd > 1:
            fy = qy * dx * dt
            dv_2d[:-1, :] -= fy
            dv_2d[1:, :] += fy
        h2d = np.where(aktiv, np.maximum(0.0, h2d + dv_2d / cell_area), 0.0)

        if oppna_rander:
            # Enkel fri avrinning på öppna ytterkanter
            for sl in ((0, slice(None)), (-1, slice(None)), (slice(None), 0), (slice(None), -1)):
                h_kant = h2d[sl]
                mask_k = aktiv[sl] & (h_kant > min_djup_m)
                if np.any(mask_k):
                    n_k = manning[sl]
                    q_ut = np.where(
                        mask_k,
                        (1.0 / n_k) * (np.maximum(h_kant, 0.0) ** (5.0 / 3.0)) * math.sqrt(0.01),
                        0.0,
                    )
                    dv_ut = np.minimum(h_kant * cell_area, q_ut * dx * dt)
                    h2d[sl] = np.maximum(0.0, h_kant - dv_ut / cell_area)
                    utflode_2d_m3 += float(np.sum(dv_ut))

        # 3. 1D-flöde längs rännstenen (exakta vektorlängder ds_m)
        eta_1d = zb_1d + h_1d
        if n_seg > 1:
            hf_1d = np.maximum(eta_1d[:-1], eta_1d[1:]) - z_face
            vat_1d = hf_1d > min_djup_m
            s_1d = (eta_1d[:-1] - eta_1d[1:]) / ds_face
            area_f, rh_f = triangular_area_och_radie(
                np.where(vat_1d, hf_1d, 0.0), tf_face, lank.max_bredd_m
            )
            q_rhs_1d = q_1d + g * area_f * dt * s_1d
            denom = 1.0 + g * dt * n1d_2 * np.abs(q_1d) / np.maximum(
                area_f * (np.maximum(rh_f, 1e-9) ** (4.0 / 3.0)), 1e-12
            )
            q_ny_1d = q_rhs_1d / denom
            q_lim_1d = froude_max * area_f * np.sqrt(g * np.maximum(hf_1d, 0.0))
            q_1d = np.where(vat_1d, np.clip(q_ny_1d, -q_lim_1d, q_lim_1d), 0.0)

            # Massbevarande utflödesbegränsning i 1D
            ut_1d = np.zeros(n_seg, dtype=np.float64)
            ut_1d[:-1] += np.maximum(q_1d, 0.0) * dt
            ut_1d[1:] += np.maximum(-q_1d, 0.0) * dt
            sk_1d = np.where(ut_1d > vol_1d, vol_1d / np.maximum(ut_1d, 1e-18), 1.0)
            q_1d = np.where(q_1d > 0.0, q_1d * sk_1d[:-1], q_1d * sk_1d[1:])

            dv_1d = q_1d * dt
            vol_1d[:-1] -= dv_1d
            vol_1d[1:] += dv_1d
            vol_1d = np.maximum(0.0, vol_1d)

        if lank.oppen_nedstroms and h_1d[-1] > min_djup_m:
            a_end, rh_end = triangular_area_och_radie(
                np.array([h_1d[-1]]), np.array([tf_1d[-1]]), lank.max_bredd_m
            )
            q_end = (
                (1.0 / lank.manning_n)
                * float(a_end[0])
                * (max(float(rh_end[0]), 1e-9) ** (2.0 / 3.0))
                * math.sqrt(lutning_nedstroms)
            )
            dv_end = min(float(vol_1d[-1]), q_end * dt)
            vol_1d[-1] = max(0.0, float(vol_1d[-1]) - dv_end)
            utflode_1d_m3 += dv_end

        h_1d = djup_fran_area(vol_1d / ds_1d, tf_1d, lank.max_bredd_m)

        # 4. Lateral 1D <-> 2D koppling (massbevarande!)
        # 4a. Körbanesidan: fritt inflöde från 2D-körbana -> 1D-rännsten; återflöde (bräddning
        # upp på körbanan) först när 1D-vattenytan överstiger rännstensbandets övre kant så att
        # rännstensvolymen inte dubbelräknas i 2D-trappstegen.
        for seg_i, r_k, c_k, b_eff in kor_koppling:
            eta_2 = float(z_bed[r_k, c_k] + h2d[r_k, c_k])
            eta_1 = float(zb_1d[seg_i] + h_1d[seg_i])
            z_tr_in = max(float(z_bed[r_k, c_k]), float(zb_1d[seg_i]))
            z_tr_ut = max(
                z_tr_in,
                float(zb_1d[seg_i]) + float(tf_1d[seg_i]) * lank.max_bredd_m,
            )
            if eta_2 > eta_1 and h2d[r_k, c_k] > min_djup_m and eta_2 > z_tr_in:
                dh = min(eta_2 - eta_1, eta_2 - z_tr_in)
                q_lat = OVERFALL_KOEFF_CW * b_eff * (dh**1.5)
                v_max = min(float(h2d[r_k, c_k]) * cell_area, 0.45 * dh * cell_area)
                dv = min(q_lat * dt, v_max)
                h2d[r_k, c_k] -= dv / cell_area
                vol_1d[seg_i] += dv
            elif eta_1 > max(eta_2, z_tr_ut) and h_1d[seg_i] > min_djup_m:
                dh = eta_1 - max(eta_2, z_tr_ut)
                q_lat = OVERFALL_KOEFF_CW * b_eff * (dh**1.5)
                v_max = min(float(vol_1d[seg_i]), 0.45 * dh * b_eff * 0.5)
                dv = min(q_lat * dt, v_max)
                vol_1d[seg_i] -= dv
                h2d[r_k, c_k] += dv / cell_area

        h_1d = djup_fran_area(vol_1d / ds_1d, tf_1d, lank.max_bredd_m)

        # 4b. Gångbane-/tomtsidan: bredkrönat överfall först när vattenstånd > z_kron_m
        for seg_i, r_g, c_g in gang_koppling:
            zk = float(zk_1d[seg_i])
            eta_1 = float(zb_1d[seg_i] + h_1d[seg_i])
            eta_2 = float(z_bed[r_g, c_g] + h2d[r_g, c_g])
            if eta_1 <= zk and eta_2 <= zk:
                continue
            if eta_1 > eta_2 and eta_1 > zk:
                dh = eta_1 - zk
                # Tillgänglig volym i 1D-segmentet ovanför krönet z_kron
                h_over_kron = max(0.0, zk - float(zb_1d[seg_i]))
                a_kron, _ = triangular_area_och_radie(
                    np.array([h_over_kron]), np.array([tf_1d[seg_i]]), lank.max_bredd_m
                )
                v_kron = float(a_kron[0]) * float(ds_1d[seg_i])
                v_tillg = max(0.0, float(vol_1d[seg_i]) - v_kron)
                if v_tillg > 0.0:
                    q_lat = OVERFALL_KOEFF_CW * float(ds_1d[seg_i]) * (dh**1.5)
                    dv = min(q_lat * dt, 0.5 * v_tillg)
                    vol_1d[seg_i] -= dv
                    h2d[r_g, c_g] += dv / cell_area
                    overfall_gang[seg_i] += dv
            elif eta_2 > eta_1 and eta_2 > zk:
                dh = eta_2 - zk
                v_tillg = max(0.0, (eta_2 - max(zk, float(z_bed[r_g, c_g]))) * cell_area)
                if v_tillg > 0.0:
                    q_lat = OVERFALL_KOEFF_CW * float(ds_1d[seg_i]) * (dh**1.5)
                    dv = min(q_lat * dt, 0.5 * v_tillg)
                    h2d[r_g, c_g] -= dv / cell_area
                    vol_1d[seg_i] += dv

        h_1d = djup_fran_area(vol_1d / ds_1d, tf_1d, lank.max_bredd_m)

        # Uppdatera statistik
        tid += dt
        antal_steg += 1
        max_h2d = np.maximum(max_h2d, h2d)
        max_h1d = np.maximum(max_h1d, h_1d)
        if n_seg > 1:
            q_seg = np.zeros(n_seg, dtype=np.float64)
            q_seg[:-1] = np.maximum(q_seg[:-1], np.abs(q_1d))
            q_seg[1:] = np.maximum(q_seg[1:], np.abs(q_1d))
            max_q1d = np.maximum(max_q1d, q_seg)
        ny_ank = np.isnan(ankomst_1d) & (h_1d >= ankomst_tr_djup_m)
        if np.any(ny_ank):
            ankomst_1d[ny_ank] = tid

    lagrat_2d = float(np.sum(h2d)) * cell_area
    lagrat_1d = float(np.sum(vol_1d))
    tot_ut_och_lagrat = lagrat_2d + lagrat_1d + utflode_2d_m3 + utflode_1d_m3
    volymfel_rel = (
        abs(tot_ut_och_lagrat - total_regn_m3) / total_regn_m3 if total_regn_m3 > 1e-12 else 0.0
    )

    return Kopplad1D2DResultat(
        lank=lank,
        max_djup_2d_m=max_h2d.astype(np.float32),
        slut_djup_2d_m=h2d.astype(np.float32),
        max_djup_1d_m=max_h1d,
        slut_djup_1d_m=h_1d,
        max_flode_1d_m3_s=max_q1d,
        overfall_gangbana_m3=overfall_gang,
        ankomst_tid_1d_s=ankomst_1d,
        total_regn_m3=total_regn_m3,
        lagrat_2d_m3=lagrat_2d,
        lagrat_1d_m3=lagrat_1d,
        utflode_2d_m3=utflode_2d_m3,
        utflode_1d_m3=utflode_1d_m3,
        volymfel_rel=volymfel_rel,
        antal_steg=antal_steg,
        kortid_s=time.perf_counter() - t_start,
    )


def exportera_rannsten_flode_geojson(
    resultat: Kopplad1D2DResultat,
    sokvag: str | Path,
) -> Path:
    """Exporterar 1D-rännstensflödet som släta 3D-vektorlinjer i GeoJSON (EPSG:3011)."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    features: list[dict[str, Any]] = []
    for i, seg in enumerate(resultat.lank.segment):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(seg.p0[0], 3), round(seg.p0[1], 3), round(seg.z_botten_m, 3)],
                        [round(seg.p1[0], 3), round(seg.p1[1], 3), round(seg.z_botten_m, 3)],
                    ],
                },
                "properties": {
                    "segment_id": seg.index,
                    "s_mitt_m": round(seg.s_mitt_m, 2),
                    "ds_m": round(seg.ds_m, 3),
                    "z_botten_rh2000": round(seg.z_botten_m, 3),
                    "z_kron_rh2000": round(seg.z_kron_m, 3),
                    "ar_sankt": seg.ar_sankt,
                    "max_djup_m": round(float(resultat.max_djup_1d_m[i]), 4),
                    "max_flode_m3_s": round(float(resultat.max_flode_1d_m3_s[i]), 5),
                    "overfall_gangbana_m3": round(float(resultat.overfall_gangbana_m3[i]), 5),
                },
            }
        )
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3011"}},
        "features": features,
    }
    mal.write_text(json.dumps(fc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return mal


def exportera_rannsten_flode_gpkg(
    resultat: Kopplad1D2DResultat,
    sokvag: str | Path,
) -> Path:
    """Exporterar 1D-rännstensflödet som släta 3D-vektorlinjer i GeoPackage (EPSG:3011)."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    xs = [p[0] for seg in resultat.lank.segment for p in (seg.p0, seg.p1)]
    ys = [p[1] for seg in resultat.lank.segment for p in (seg.p0, seg.p1)]
    bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)

    conn = sqlite3.connect(str(mal))
    try:
        _initiera_gpkg_bas(conn)
        conn.execute(
            """
            CREATE TABLE rannsten_flode (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                segment_id INTEGER NOT NULL,
                s_mitt_m REAL NOT NULL,
                z_botten_rh2000 REAL NOT NULL,
                z_kron_rh2000 REAL NOT NULL,
                ar_sankt INTEGER NOT NULL,
                max_djup_m REAL NOT NULL,
                max_flode_m3_s REAL NOT NULL,
                overfall_gangbana_m3 REAL NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change,
                 min_x, min_y, max_x, max_y, srs_id)
            VALUES ('rannsten_flode', 'features', 'rannsten_flode',
                    '1D-rännstensflöde längs 3D-kantsten', '2026-11-25T00:00:00.000Z',
                    ?, ?, ?, ?, ?);
            """,
            (bbox[0], bbox[1], bbox[2], bbox[3], EPSG_SWEREF99_1800),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns
                (table_name, column_name, geometry_type_name, srs_id, z, m)
            VALUES ('rannsten_flode', 'geom', 'LINESTRING', ?, 1, 0);
            """,
            (EPSG_SWEREF99_1800,),
        )
        for i, seg in enumerate(resultat.lank.segment):
            blob = _gpkg_linestring_z_blob((seg.p0, seg.p1), (seg.z_botten_m, seg.z_botten_m))
            conn.execute(
                """
                INSERT INTO rannsten_flode (
                    geom, segment_id, s_mitt_m, z_botten_rh2000, z_kron_rh2000,
                    ar_sankt, max_djup_m, max_flode_m3_s, overfall_gangbana_m3
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    blob,
                    seg.index,
                    seg.s_mitt_m,
                    seg.z_botten_m,
                    seg.z_kron_m,
                    1 if seg.ar_sankt else 0,
                    float(resultat.max_djup_1d_m[i]),
                    float(resultat.max_flode_1d_m3_s[i]),
                    float(resultat.overfall_gangbana_m3[i]),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return mal


def bygg_a5b_sned_gata(
    vinkel_grader: float,
    *,
    langd_m: float = 100.0,
    korbana_bredd_m: float = 3.0,
    gangbana_bredd_m: float = 4.0,
    lutning_langs: float = 0.01,
    tvarfall: float = 0.025,
    kantsten_hojd_m: float = 0.12,
    infart_s0_m: float = 60.0,
    infart_s1_m: float = 64.0,
    infart_hojd_m: float = 0.02,
    cell_m: float = 0.5,
) -> tuple[
    NDArray[np.float32],
    NDArray[np.uint8],
    NDArray[np.bool_],
    GeoTransformInfo,
    list[BrytlinjePost],
]:
    """Bygger syntetisk 100 m lutande gata i godtycklig vinkel (0°/20°/45°) för A5b."""
    theta = math.radians(vinkel_grader)
    tx, ty = math.cos(theta), math.sin(theta)
    nx_v, ny_v = -ty, tx

    halv_s = 0.5 * langd_m + 10.0
    halv_t = max(korbana_bredd_m, gangbana_bredd_m) + 10.0
    span_x = abs(halv_s * tx) + abs(halv_t * ty)
    span_y = abs(halv_s * ty) + abs(halv_t * tx)
    bredd = int(math.ceil(2.0 * span_x / cell_m)) + 4
    hojd = int(math.ceil(2.0 * span_y / cell_m)) + 4

    cx, cy = 150100.0, 6580100.0
    geo = GeoTransformInfo(
        x_min=cx - 0.5 * bredd * cell_m,
        y_max=cy + 0.5 * hojd * cell_m,
        pixelstorlek_x=cell_m,
        pixelstorlek_y=cell_m,
        bredd=bredd,
        hojd=hojd,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    e_start = cx - 0.5 * langd_m * tx
    n_start = cy - 0.5 * langd_m * ty

    rr, cc = np.mgrid[0:hojd, 0:bredd]
    ee = geo.x_min + (cc + 0.5) * cell_m
    nn = geo.y_max - (rr + 0.5) * cell_m
    s_coord = (ee - e_start) * tx + (nn - n_start) * ty
    d_coord = (ee - e_start) * nx_v + (nn - n_start) * ny_v

    markslag = np.full((hojd, bredd), MARKSLAG_BYGGNAD, dtype=np.uint8)
    i_langd = (s_coord >= 0.0) & (s_coord <= langd_m)
    pa_korbana = i_langd & (d_coord > 0.0) & (d_coord <= korbana_bredd_m)
    pa_gangbana = i_langd & (d_coord <= 0.0) & (d_coord >= -gangbana_bredd_m)
    markslag[pa_korbana] = 1
    markslag[pa_gangbana] = 2

    z_rannsten = 20.0 - lutning_langs * s_coord
    mark_z = np.full((hojd, bredd), 25.0, dtype=np.float32)
    mark_z[pa_korbana] = (z_rannsten[pa_korbana] + tvarfall * d_coord[pa_korbana]).astype(
        np.float32
    )
    mark_z[pa_gangbana] = (z_rannsten[pa_gangbana] - 0.02 + 0.01 * d_coord[pa_gangbana]).astype(
        np.float32
    )
    regn_mask = pa_korbana & (s_coord >= 2.0) & (s_coord <= 35.0)

    def _pt(s_m: float) -> tuple[float, float]:
        return (e_start + s_m * tx, n_start + s_m * ty)

    def _zb(s_m: float) -> float:
        return 20.0 - lutning_langs * s_m

    brytlinjer = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=kantsten_hojd_m,
            koordinater=(_pt(0.0), _pt(infart_s0_m)),
            z_kron_rh2000=(_zb(0.0) + kantsten_hojd_m, _zb(infart_s0_m) + kantsten_hojd_m),
            z_botten_rh2000=(_zb(0.0), _zb(infart_s0_m)),
            tvarfall=tvarfall,
            kalla="baskarta_3d",
        ),
        BrytlinjePost(
            typ="sankt_kantsten",
            hojd_m=infart_hojd_m,
            koordinater=(_pt(infart_s0_m), _pt(infart_s1_m)),
            z_kron_rh2000=(_zb(infart_s0_m) + infart_hojd_m, _zb(infart_s1_m) + infart_hojd_m),
            z_botten_rh2000=(_zb(infart_s0_m), _zb(infart_s1_m)),
            tvarfall=tvarfall,
            kalla="baskarta_3d",
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=kantsten_hojd_m,
            koordinater=(_pt(infart_s1_m), _pt(langd_m)),
            z_kron_rh2000=(_zb(infart_s1_m) + kantsten_hojd_m, _zb(langd_m) + kantsten_hojd_m),
            z_botten_rh2000=(_zb(infart_s1_m), _zb(langd_m)),
            tvarfall=tvarfall,
            kalla="baskarta_3d",
        ),
    ]
    return mark_z, markslag, regn_mask, geo, brytlinjer


def kor_a5b_rotationsinvarians(
    vinklar: Sequence[float] = (0.0, 20.0, 45.0),
) -> dict[float, tuple[Kopplad1D2DResultat, float, float, float]]:
    """Kör A5b för angivna vinklar; returnerar `{vinkel: (res, gangtid_s, maxdjup_m, andel)}`."""
    utfall: dict[float, tuple[Kopplad1D2DResultat, float, float, float]] = {}
    for vinkel in vinklar:
        mark_z, markslag, regn_mask, geo, brytlinjer = bygg_a5b_sned_gata(vinkel)
        lank = diskretisera_rannsten_1d(
            brytlinjer, mark_z, markslag, geo, ds_mal_m=2.0, korbana_sida="vanster"
        )
        regn_area_m2 = float(np.count_nonzero(regn_mask)) * float(
            geo.pixelstorlek_x * geo.pixelstorlek_y
        )
        intens_mm_h = (6.0 / regn_area_m2) * (3_600_000.0 / 90.0)
        res = kor_kopplad_1d_2d(
            mark_z,
            markslag,
            geo,
            [0.0, 90.0],
            [intens_mm_h, intens_mm_h],
            lank,
            brytlinjer=brytlinjer,
            slut_tid_s=180.0,
            oppna_rander=False,
            regn_mask=regn_mask,
            ankomst_tr_djup_m=0.02,
        )
        infart_idx = [i for i, s in enumerate(lank.segment) if s.ar_sankt]
        gangtid_s = float(np.nanmin(res.ankomst_tid_1d_s[infart_idx]))
        maxdjup_m = float(np.max(res.max_djup_1d_m))
        infart_andel = res.overfall_sankt_m3 / max(res.total_regn_m3, 1e-12)
        utfall[float(vinkel)] = (res, gangtid_s, maxdjup_m, infart_andel)
    return utfall
