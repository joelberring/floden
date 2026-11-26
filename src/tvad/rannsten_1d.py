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
from collections.abc import Mapping, Sequence
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
from sankgraf.hierarki import _berakna_kantbarriarer
from sankgraf.takvatten import TakvattenFordelning
from sankgraf.typer import MARKSLAG_BYGGNAD, volymbalans_dict
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo
from tvad.kalltermer import Kalltermer, SanktermTillstand
from tvad.lokal_troghet import (
    GRAVITATION_M_S2,
    MARKSLAG_VATTEN,
    TvadFel,
    TvadResultat,
    berakna_rand_q,
    berakna_rand_utflode_konfig,
    bygg_manning_raster,
    integrera_regn_m,
    nasta_regnbrytpunkt,
    takvatten_regnyta,
    utjamna_flode_x,
    utjamna_flode_y,
    validera_regnserie,
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
    max_hastighet_2d_m_s: NDArray[np.float32] | None = None
    fara_2d_m2_s: NDArray[np.float32] | None = None
    tid_till_0_1m_s: NDArray[np.float32] | None = None
    tid_till_0_2m_s: NDArray[np.float32] | None = None
    in_m3: float | None = None
    avtappat_m3: float = 0.0
    infiltrerat_m3: float = 0.0
    initial_forlust_m3: float = 0.0
    infiltration_m: NDArray[np.float32] | None = None
    sanktermer: dict[str, Any] | None = None
    regn_mark_m3: float = 0.0
    regn_tak_m3: float = 0.0
    takvatten: bool = False
    takvatten_ut_rand_m3: float = 0.0
    takvatten_till_ledning_m3: float = 0.0
    courant_hast_max: float = 0.0
    aktiv_cell_andel: float = 0.0

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

    def volymbalans(self) -> dict[str, float | bool]:
        """Sammanlagd volymbalans för 1D-rännsten + 2D lokal tröghet."""
        return volymbalans_dict(
            regn_mark_m3=self.regn_mark_m3,
            regn_tak_m3=self.regn_tak_m3,
            takvatten=self.takvatten,
            lagrat_m3=self.lagrat_2d_m3 + self.lagrat_1d_m3,
            ut_m3=self.utflode_2d_m3 + self.utflode_1d_m3,
            infiltrerat_m3=self.infiltrerat_m3 + self.initial_forlust_m3,
            avtappat_m3=self.avtappat_m3,
            tak_ledning_m3=self.takvatten_till_ledning_m3,
        )

    def till_tvad_resultat(self) -> TvadResultat:
        """Konverterar den kopplade 1D/2D-körningen till ett K6-kompatibelt `TvadResultat`."""
        form = self.max_djup_2d_m.shape
        return TvadResultat(
            max_djup_m=self.max_djup_2d_m,
            max_hastighet_m_s=(
                self.max_hastighet_2d_m_s
                if self.max_hastighet_2d_m_s is not None
                else np.zeros(form, dtype=np.float32)
            ),
            fara_m2_s=(
                self.fara_2d_m2_s
                if self.fara_2d_m2_s is not None
                else np.zeros(form, dtype=np.float32)
            ),
            tid_till_0_1m_s=(
                self.tid_till_0_1m_s
                if self.tid_till_0_1m_s is not None
                else np.full(form, np.nan, dtype=np.float32)
            ),
            tid_till_0_2m_s=(
                self.tid_till_0_2m_s
                if self.tid_till_0_2m_s is not None
                else np.full(form, np.nan, dtype=np.float32)
            ),
            slut_djup_m=self.slut_djup_2d_m,
            in_m3=self.in_m3 if self.in_m3 is not None else self.total_regn_m3,
            lagrat_m3=self.lagrat_2d_m3 + self.lagrat_1d_m3,
            ut_m3=self.utflode_2d_m3 + self.utflode_1d_m3,
            volymfel_rel=self.volymfel_rel,
            antal_tidssteg=self.antal_steg,
            kortid_s=self.kortid_s,
            niva="full2d",
            aktiv_cell_andel=self.aktiv_cell_andel,
            avtappat_m3=self.avtappat_m3,
            infiltrerat_m3=self.infiltrerat_m3,
            initial_forlust_m3=self.initial_forlust_m3,
            infiltration_m=self.infiltration_m,
            sanktermer=self.sanktermer,
            regn_mark_m3=self.regn_mark_m3,
            regn_tak_m3=self.regn_tak_m3,
            takvatten=self.takvatten,
            takvatten_ut_rand_m3=self.takvatten_ut_rand_m3,
            takvatten_till_ledning_m3=self.takvatten_till_ledning_m3,
            courant_hast_max=self.courant_hast_max,
            berakningsplats="cpu_f64",
            anvande_rust=False,
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

    # Koppla ALLA aktiva körbaneceller inom ett enhetligt band (2*cell, oberoende av vinkel)
    # längs rännstenskanten till sitt närmaste 1D-segment så att 0°, 20° och 45° får samma
    # fysiska kopplingsbredd utan 2D-trappstegsartefakter.
    hojd, bredd = mark_z.shape
    max_band = 2.0 * max(dx, dy)
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
    dt_min_s: float = 1e-4,
    min_djup_m: float = MIN_DJUP_M,
    froude_max: float = 1.0,
    oppna_rander: bool = True,
    regn_mask: NDArray[np.bool_] | None = None,
    ankomst_tr_djup_m: float = 0.01,
    theta: float = 0.9,
    manning_tabell: Mapping[int, float] | None = None,
    kalltermer: Kalltermer | None = None,
    takvatten: TakvattenFordelning | None = None,
    vattenniva_rh2000_m: float | None = None,
) -> Kopplad1D2DResultat:
    """Kör massbevarande kopplad 1D-rännsten + 2D lokal tröghet med täta 3D-kantbarriärer."""
    t_start = time.perf_counter()
    if mark_z.ndim != 2 or markslag.ndim != 2 or mark_z.shape != markslag.shape:
        raise RannstenFel("mark_z och markslag måste vara 2D-matriser med samma form.")
    hojd, bredd = mark_z.shape
    if hojd != geo.hojd or bredd != geo.bredd:
        raise RannstenFel(
            f"GeoTransformInfo ({geo.hojd}x{geo.bredd}) matchar inte raster ({hojd}x{bredd})."
        )
    if not math.isfinite(cfl) or cfl <= 0.0 or cfl > 1.0:
        raise RannstenFel(f"cfl måste ligga i (0, 1], fick {cfl}.")
    if not math.isfinite(theta) or theta <= 0.0 or theta > 1.0:
        raise RannstenFel(f"theta måste ligga i (0, 1], fick {theta}.")
    if not math.isfinite(min_djup_m) or min_djup_m <= 0.0:
        raise RannstenFel(f"min_djup_m måste vara > 0, fick {min_djup_m}.")
    if not math.isfinite(dt_max_s) or dt_max_s <= 0.0:
        raise RannstenFel(f"dt_max_s måste vara > 0, fick {dt_max_s}.")
    if not math.isfinite(dt_min_s) or dt_min_s <= 0.0:
        raise RannstenFel(f"dt_min_s måste vara > 0, fick {dt_min_s}.")
    if not math.isfinite(froude_max) or froude_max <= 0.0:
        raise RannstenFel(f"froude_max måste vara > 0, fick {froude_max}.")
    if vattenniva_rh2000_m is not None and not math.isfinite(float(vattenniva_rh2000_m)):
        raise RannstenFel(
            f"vattenniva_rh2000_m måste vara ett ändligt tal, fick {vattenniva_rh2000_m}."
        )

    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    cell_area = dx * dy
    min_dx = min(dx, dy)

    try:
        tider, intens_m_s, t_regn_start, slut = validera_regnserie(
            regn_tid_s, regn_intensitet_mm_h, slut_tid_s
        )
    except TvadFel as exc:
        raise RannstenFel(str(exc)) from exc

    aktiv = np.asarray((markslag != MARKSLAG_BYGGNAD) & np.isfinite(mark_z), dtype=np.bool_)
    z_bed = np.where(aktiv, np.asarray(mark_z, dtype=np.float64), 0.0)
    aktiv_area = np.where(aktiv, cell_area, 0.0)
    total_doman_area = float(hojd * bredd) * cell_area
    total_aktiv_area = float(np.sum(aktiv_area))
    regn_aktiv = aktiv & np.asarray(regn_mask, dtype=bool) if regn_mask is not None else aktiv
    regn_mark_area_m2 = float(np.count_nonzero(regn_aktiv)) * cell_area

    tak_extra, tak_ut_m2, tak_ledning_m2, tak_m2 = takvatten_regnyta(
        takvatten,
        markslag,
        np.asarray(mark_z, dtype=np.float32),
        cell_area,
        1,
        (hojd, bredd),
    )
    har_tak = takvatten is not None and not takvatten.tom
    regn_area = np.where(regn_aktiv, cell_area, 0.0) + (tak_extra if har_tak else 0.0)
    total_regn_area = float(np.sum(regn_area)) + (tak_ut_m2 if har_tak else 0.0)

    manning = bygg_manning_raster(markslag, manning_tabell)
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

    rand_konfig = berakna_rand_utflode_konfig(
        z_bed, aktiv, dx, dy, oppna_rander=oppna_rander
    )

    vatten_mask = np.asarray(aktiv & (markslag == MARKSLAG_VATTEN), dtype=np.bool_)
    har_vatten_rand = bool(np.any(vatten_mask))
    if har_vatten_rand and vattenniva_rh2000_m is not None:
        mal_h = np.maximum(0.0, float(vattenniva_rh2000_m) - z_bed)
        vatten_mal_vol = np.where(vatten_mask, mal_h * aktiv_area, 0.0)
    else:
        vatten_mal_vol = np.zeros((hojd, bredd), dtype=np.float64)

    sank: SanktermTillstand | None = None
    if kalltermer is not None:
        sank = SanktermTillstand.bygg(
            kalltermer, (hojd, bredd), aktiv_area_fin=aktiv_area, subgrid_faktor=1
        )
    tvingat = kalltermer.tvingat_djup if kalltermer is not None else None
    if tvingat is not None and tvingat.mask.shape != (hojd, bredd):
        raise RannstenFel(
            f"tvingat_djup.mask har form {tvingat.mask.shape}, lösaren {(hojd, bredd)}."
        )

    # 2D-tillstånd
    volym_2d = np.zeros((hojd, bredd), dtype=np.float64)
    h2d = np.zeros((hojd, bredd), dtype=np.float64)
    qx = np.zeros((hojd, max(0, bredd - 1)), dtype=np.float64)
    qy = np.zeros((max(0, hojd - 1), bredd), dtype=np.float64)
    max_h2d = np.zeros((hojd, bredd), dtype=np.float64)
    max_hast_2d = np.zeros((hojd, bredd), dtype=np.float64)
    max_fara_2d = np.zeros((hojd, bredd), dtype=np.float64)
    tid_01_2d = np.full((hojd, bredd), np.nan, dtype=np.float32)
    tid_02_2d = np.full((hojd, bredd), np.nan, dtype=np.float32)
    ut_2d = np.zeros((hojd, bredd), dtype=np.float64)
    q_cell_x = np.zeros((hojd, bredd), dtype=np.float64)
    q_cell_y = np.zeros((hojd, bredd), dtype=np.float64)

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
            bredd_per_cell = s.ds_m / len(celler)
            for r_k, c_k in celler:
                kor_koppling.append((i, r_k, c_k, bredd_per_cell))
    gang_koppling = [
        (i, s.gangbana_rc[0], s.gangbana_rc[1])
        for i, s in enumerate(lank.segment)
        if s.gangbana_rc is not None
    ]

    tid = t_regn_start
    antal_steg = 0
    in_m3 = 0.0
    total_regn_m3 = 0.0
    regn_summa_m = 0.0
    utflode_2d_m3 = 0.0
    utflode_1d_m3 = 0.0
    g = GRAVITATION_M_S2
    n1d_2 = lank.manning_n * lank.manning_n
    ds_min = float(np.min(ds_1d))
    u_max_2d = 0.0
    u_max_1d = 0.0
    courant_hast_max = 0.0

    aktiv_u = (
        np.asarray(aktiv[:, :-1] & aktiv[:, 1:], dtype=np.bool_)
        if bredd > 1
        else np.zeros((hojd, 0), dtype=np.bool_)
    )
    aktiv_v = (
        np.asarray(aktiv[:-1, :] & aktiv[1:, :], dtype=np.bool_)
        if hojd > 1
        else np.zeros((0, bredd), dtype=np.bool_)
    )

    if har_vatten_rand and bool(np.any(vatten_mal_vol > 0.0)):
        volym_2d = np.where(vatten_mask, vatten_mal_vol, volym_2d)
        in_m3 += float(np.sum(vatten_mal_vol))
        h2d = np.where(aktiv, volym_2d / cell_area, 0.0)
        np.maximum(max_h2d, h2d, out=max_h2d)

    while tid < slut - 1e-12:
        h_max_2d = float(np.max(h2d)) if h2d.size > 0 else 0.0
        h_max_1d = float(np.max(h_1d)) if h_1d.size > 0 else 0.0
        # CFL-villkor med våghastighet + strömningshastighet u_max (BESLUT.md 2026-11-23)
        c_2d = math.sqrt(g * max(h_max_2d, min_djup_m)) + u_max_2d
        c_1d = math.sqrt(g * max(h_max_1d, min_djup_m)) + u_max_1d
        dt_2d = cfl * min_dx / c_2d
        dt_1d = cfl * ds_min / c_1d
        dt = min(dt_max_s, dt_2d, dt_1d)
        nasta_bryt = nasta_regnbrytpunkt(tid, tider, slut)
        dt = min(dt, nasta_bryt - tid)
        if dt < dt_min_s and (slut - tid) > dt_min_s:
            dt = min(dt_min_s, nasta_bryt - tid)
        if dt <= 0.0:
            break
        courant_hast_max = max(
            courant_hast_max,
            u_max_2d * dt / min_dx,
            u_max_1d * dt / ds_min,
        )

        # 1. Regn + takvatten på aktiva 2D-regnceller
        regn_m = integrera_regn_m(tid, tid + dt, tider, intens_m_s)
        regn_summa_m += regn_m
        if regn_m > 0.0 and total_regn_area > 0.0:
            volym_2d += regn_area * regn_m
            dv_regn = total_regn_area * regn_m
            in_m3 += dv_regn
            total_regn_m3 += dv_regn
            utflode_2d_m3 += tak_ut_m2 * regn_m
            if har_vatten_rand:
                diff_v = np.where(vatten_mask, vatten_mal_vol - volym_2d, 0.0)
                in_m3 += float(np.sum(np.maximum(diff_v, 0.0)))
                utflode_2d_m3 += float(np.sum(np.maximum(-diff_v, 0.0)))
                volym_2d = np.where(vatten_mask, vatten_mal_vol, volym_2d)
            h2d = np.where(aktiv, volym_2d / cell_area, 0.0)

        # 1b. Sänktermer per cell (initial förlust, infiltration, avtappning)
        if sank is not None:
            sank.tillampa(volym_2d, aktiv_area, dt)
            h2d = np.where(aktiv, volym_2d / cell_area, 0.0)

        # 2. 2D lokal tröghet (Bates 2010 / de Almeida 2012 theta-utjämning)
        h2d_fore = h2d.copy()
        zs2d = z_bed + h2d
        if bredd > 1:
            hf_u = np.maximum(zs2d[:, :-1], zs2d[:, 1:]) - z_kant_u
            vat_u = aktiv_u & (hf_u > min_djup_m)
            su = (zs2d[:, :-1] - zs2d[:, 1:]) / dx
            qx_bar = utjamna_flode_x(qx, aktiv_u, theta)
            hv = np.where(vat_u, hf_u, 1.0)
            q_rhs = qx_bar + g * hv * dt * su
            c_fr = g * dt * (manning_u * manning_u) / (hv * hv * np.cbrt(hv))
            q_ny = (2.0 * q_rhs) / (1.0 + np.sqrt(1.0 + 4.0 * c_fr * np.abs(q_rhs)))
            q_lim = froude_max * hv * np.sqrt(g * hv)
            qx = np.where(vat_u, np.clip(q_ny, -q_lim, q_lim), 0.0)
        if hojd > 1:
            hf_v = np.maximum(zs2d[:-1, :], zs2d[1:, :]) - z_kant_v
            vat_v = aktiv_v & (hf_v > min_djup_m)
            sv = (zs2d[:-1, :] - zs2d[1:, :]) / dy
            qy_bar = utjamna_flode_y(qy, aktiv_v, theta)
            hv = np.where(vat_v, hf_v, 1.0)
            q_rhs = qy_bar + g * hv * dt * sv
            c_fr = g * dt * (manning_v * manning_v) / (hv * hv * np.cbrt(hv))
            q_ny = (2.0 * q_rhs) / (1.0 + np.sqrt(1.0 + 4.0 * c_fr * np.abs(q_rhs)))
            q_lim = froude_max * hv * np.sqrt(g * hv)
            qy = np.where(vat_v, np.clip(q_ny, -q_lim, q_lim), 0.0)

        # Öppna randutflöden (samma utåtlutande randmodell som kor_2d_lokal_troghet)
        q_vast = berakna_rand_q(
            h2d[:, 0],
            manning[:, 0],
            rand_konfig.vast_mask,
            rand_konfig.vast_lutning,
            rand_konfig.vast_bredd_andel,
            min_djup_m,
        )
        q_ost = berakna_rand_q(
            h2d[:, -1],
            manning[:, -1],
            rand_konfig.ost_mask,
            rand_konfig.ost_lutning,
            rand_konfig.ost_bredd_andel,
            min_djup_m,
        )
        q_norr = berakna_rand_q(
            h2d[0, :],
            manning[0, :],
            rand_konfig.norr_mask,
            rand_konfig.norr_lutning,
            rand_konfig.norr_bredd_andel,
            min_djup_m,
        )
        q_soder = berakna_rand_q(
            h2d[-1, :],
            manning[-1, :],
            rand_konfig.soder_mask,
            rand_konfig.soder_lutning,
            rand_konfig.soder_bredd_andel,
            min_djup_m,
        )

        # Massbevarande utflödesbegränsning i 2D (inre flöden + öppna ränder)
        ut_2d.fill(0.0)
        if bredd > 1:
            ut_2d[:, :-1] += np.maximum(qx, 0.0) * (dy * dt)
            ut_2d[:, 1:] += np.maximum(-qx, 0.0) * (dy * dt)
        if hojd > 1:
            ut_2d[:-1, :] += np.maximum(qy, 0.0) * (dx * dt)
            ut_2d[1:, :] += np.maximum(-qy, 0.0) * (dx * dt)
        ut_2d[:, 0] += q_vast * (dy * dt)
        ut_2d[:, -1] += q_ost * (dy * dt)
        ut_2d[0, :] += q_norr * (dx * dt)
        ut_2d[-1, :] += q_soder * (dx * dt)

        over_ut = ut_2d > volym_2d
        if bool(np.any(over_ut)):
            skala = np.ones((hojd, bredd), dtype=np.float64)
            skala[over_ut] = volym_2d[over_ut] / ut_2d[over_ut]
            if bredd > 1:
                qx = np.where(qx >= 0.0, qx * skala[:, :-1], qx * skala[:, 1:])
            if hojd > 1:
                qy = np.where(qy >= 0.0, qy * skala[:-1, :], qy * skala[1:, :])
            q_vast = q_vast * skala[:, 0]
            q_ost = q_ost * skala[:, -1]
            q_norr = q_norr * skala[0, :]
            q_soder = q_soder * skala[-1, :]

        if bredd > 1:
            fx = qx * (dy * dt)
            volym_2d[:, :-1] -= fx
            volym_2d[:, 1:] += fx
        if hojd > 1:
            fy = qy * (dx * dt)
            volym_2d[:-1, :] -= fy
            volym_2d[1:, :] += fy

        dv_vast = q_vast * (dy * dt)
        dv_ost = q_ost * (dy * dt)
        dv_norr = q_norr * (dx * dt)
        dv_soder = q_soder * (dx * dt)
        volym_2d[:, 0] -= dv_vast
        volym_2d[:, -1] -= dv_ost
        volym_2d[0, :] -= dv_norr
        volym_2d[-1, :] -= dv_soder
        utflode_2d_m3 += float(
            np.sum(dv_vast) + np.sum(dv_ost) + np.sum(dv_norr) + np.sum(dv_soder)
        )

        np.maximum(volym_2d, 0.0, out=volym_2d)
        volym_2d[~aktiv] = 0.0

        # Tvingat djup (randvillkor) och vattenyta (markslag == 8)
        if tvingat is not None:
            mal_v = np.where(tvingat.mask & aktiv, tvingat.djup_m(tid + dt) * aktiv_area, 0.0)
            diff = np.where(tvingat.mask & aktiv, mal_v - volym_2d, 0.0)
            in_m3 += float(np.sum(np.maximum(diff, 0.0)))
            utflode_2d_m3 += float(np.sum(np.maximum(-diff, 0.0)))
            volym_2d = np.where(tvingat.mask & aktiv, mal_v, volym_2d)

        if har_vatten_rand:
            diff_v = np.where(vatten_mask, vatten_mal_vol - volym_2d, 0.0)
            in_m3 += float(np.sum(np.maximum(diff_v, 0.0)))
            utflode_2d_m3 += float(np.sum(np.maximum(-diff_v, 0.0)))
            volym_2d = np.where(vatten_mask, vatten_mal_vol, volym_2d)

        h2d = np.where(aktiv, volym_2d / cell_area, 0.0)

        # 2D-hastighet v = |q| / max(h_före, h_efter) och fara i våta celler (BESLUT.md 2026-11-23).
        # Vid en torr vätfront (h_före == 0) och litet dt begränsas cellhastigheten av den fysiska
        # kanthastigheten |q_kant| / h_flöde så att ingen artificiell dx/(2·dt)-spik uppstår.
        q_cell_x.fill(0.0)
        u_kant_cell = np.zeros((hojd, bredd), dtype=np.float64)
        u_max_kant = 0.0
        if bredd > 1:
            q_cell_x[:, :-1] += 0.5 * qx
            q_cell_x[:, 1:] += 0.5 * qx
            ux_kant = np.where(vat_u, np.abs(qx) / np.where(vat_u, hf_u, 1.0), 0.0)
            np.maximum(u_kant_cell[:, :-1], ux_kant, out=u_kant_cell[:, :-1])
            np.maximum(u_kant_cell[:, 1:], ux_kant, out=u_kant_cell[:, 1:])
            if bool(np.any(vat_u)):
                u_max_kant = max(u_max_kant, float(np.max(ux_kant)))
        q_cell_x[:, 0] -= 0.5 * q_vast
        q_cell_x[:, -1] += 0.5 * q_ost

        q_cell_y.fill(0.0)
        if hojd > 1:
            q_cell_y[:-1, :] += 0.5 * qy
            q_cell_y[1:, :] += 0.5 * qy
            uy_kant = np.where(vat_v, np.abs(qy) / np.where(vat_v, hf_v, 1.0), 0.0)
            np.maximum(u_kant_cell[:-1, :], uy_kant, out=u_kant_cell[:-1, :])
            np.maximum(u_kant_cell[1:, :], uy_kant, out=u_kant_cell[1:, :])
            if bool(np.any(vat_v)):
                u_max_kant = max(u_max_kant, float(np.max(uy_kant)))
        q_cell_y[0, :] -= 0.5 * q_norr
        q_cell_y[-1, :] += 0.5 * q_soder

        for sl_r, q_r in (
            ((slice(None), 0), q_vast),
            ((slice(None), -1), q_ost),
            ((0, slice(None)), q_norr),
            ((-1, slice(None)), q_soder),
        ):
            m_r = h2d_fore[sl_r] > min_djup_m
            if bool(np.any(m_r)):
                ur = np.where(m_r, q_r / np.where(m_r, h2d_fore[sl_r], 1.0), 0.0)
                np.maximum(u_kant_cell[sl_r], ur, out=u_kant_cell[sl_r])
                u_max_kant = max(u_max_kant, float(np.max(ur)))

        q_mag = np.hypot(q_cell_x, q_cell_y)
        h_vel = np.maximum(h2d_fore, h2d)
        vat_nu = aktiv & (h_vel > min_djup_m)
        if bool(np.any(vat_nu)):
            hast = np.zeros((hojd, bredd), dtype=np.float64)
            hast[vat_nu] = np.minimum(q_mag[vat_nu] / h_vel[vat_nu], u_kant_cell[vat_nu])
            u_max_2d = max(float(np.max(hast)), u_max_kant)
            np.maximum(max_hast_2d, hast, out=max_hast_2d)
            fara_steg = np.where(vat_nu, q_mag, 0.0)
            np.maximum(max_fara_2d, fara_steg, out=max_fara_2d)
        else:
            u_max_2d = u_max_kant

        # 3. 1D-flöde längs rännstenen (exakta vektorlängder ds_m)
        eta_1d = zb_1d + h_1d
        u_max_1d = 0.0
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

            vat_face_vel = vat_1d & (area_f > 1e-12)
            if bool(np.any(vat_face_vel)):
                u_max_1d = float(np.max(np.abs(q_1d[vat_face_vel]) / area_f[vat_face_vel]))

        q_end_eff = 0.0
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
            q_end_eff = dv_end / dt if dt > 0.0 else 0.0
            if float(a_end[0]) > 1e-12:
                u_max_1d = max(u_max_1d, q_end_eff / float(a_end[0]))

        h_1d = djup_fran_area(vol_1d / ds_1d, tf_1d, lank.max_bredd_m)

        q_seg = np.zeros(n_seg, dtype=np.float64)
        if n_seg > 1:
            q_seg[:-1] = np.maximum(q_seg[:-1], np.abs(q_1d))
            q_seg[1:] = np.maximum(q_seg[1:], np.abs(q_1d))
        if q_end_eff > 0.0:
            q_seg[-1] = max(float(q_seg[-1]), q_end_eff)

        # 4. Lateral 1D <-> 2D koppling (exakt massbevarande!)
        # 4a. Körbanesidan: när 1D-rännstenen ligger under sin övre kant (z_tr_ut) är vattnet
        # i de kopplade körbanecellerna tillrinning som tas upp direkt av 1D-kanalen oberoende
        # av gatans vinkel mot 2D-rutnätet. När 1D-ytan överstiger z_tr_ut bräddar rännstenen
        # tillbaka upp på 2D-körbanan (eller tar emot djupt vatten om eta_2 > eta_1).
        for seg_i, r_k, c_k, b_eff in kor_koppling:
            eta_2 = float(z_bed[r_k, c_k] + h2d[r_k, c_k])
            eta_1 = float(zb_1d[seg_i] + h_1d[seg_i])
            z_tr_in = max(float(z_bed[r_k, c_k]), float(zb_1d[seg_i]))
            z_tr_ut = max(
                z_tr_in,
                float(zb_1d[seg_i]) + float(tf_1d[seg_i]) * lank.max_bredd_m,
            )
            if eta_1 <= z_tr_ut and h2d[r_k, c_k] > min_djup_m:
                dh = float(h2d[r_k, c_k])
                q_lat = OVERFALL_KOEFF_CW * b_eff * (dh**1.5)
                v_max = float(volym_2d[r_k, c_k])
                dv = min(q_lat * dt, v_max)
                volym_2d[r_k, c_k] -= dv
                h2d[r_k, c_k] = volym_2d[r_k, c_k] / cell_area
                vol_1d[seg_i] += dv
            elif eta_2 > eta_1 and h2d[r_k, c_k] > min_djup_m and eta_2 > z_tr_in:
                dh = min(eta_2 - eta_1, eta_2 - z_tr_in)
                q_lat = OVERFALL_KOEFF_CW * b_eff * (dh**1.5)
                v_max = min(float(volym_2d[r_k, c_k]), 0.45 * dh * cell_area)
                dv = min(q_lat * dt, v_max)
                volym_2d[r_k, c_k] -= dv
                h2d[r_k, c_k] = volym_2d[r_k, c_k] / cell_area
                vol_1d[seg_i] += dv
            elif eta_1 > max(eta_2, z_tr_ut) and h_1d[seg_i] > min_djup_m:
                dh = eta_1 - max(eta_2, z_tr_ut)
                q_lat = OVERFALL_KOEFF_CW * b_eff * (dh**1.5)
                v_max = min(float(vol_1d[seg_i]), 0.45 * dh * b_eff * 0.5)
                dv = min(q_lat * dt, v_max)
                vol_1d[seg_i] -= dv
                volym_2d[r_k, c_k] += dv
                h2d[r_k, c_k] = volym_2d[r_k, c_k] / cell_area

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
                    volym_2d[r_g, c_g] += dv
                    h2d[r_g, c_g] = volym_2d[r_g, c_g] / cell_area
                    overfall_gang[seg_i] += dv
            elif eta_2 > eta_1 and eta_2 > zk:
                dh = eta_2 - zk
                v_tillg = max(0.0, (eta_2 - max(zk, float(z_bed[r_g, c_g]))) * cell_area)
                v_tillg = min(v_tillg, float(volym_2d[r_g, c_g]))
                if v_tillg > 0.0:
                    q_lat = OVERFALL_KOEFF_CW * float(ds_1d[seg_i]) * (dh**1.5)
                    dv = min(q_lat * dt, 0.5 * v_tillg)
                    volym_2d[r_g, c_g] -= dv
                    h2d[r_g, c_g] = volym_2d[r_g, c_g] / cell_area
                    vol_1d[seg_i] += dv

        if har_vatten_rand:
            diff_v = np.where(vatten_mask, vatten_mal_vol - volym_2d, 0.0)
            in_m3 += float(np.sum(np.maximum(diff_v, 0.0)))
            utflode_2d_m3 += float(np.sum(np.maximum(-diff_v, 0.0)))
            volym_2d = np.where(vatten_mask, vatten_mal_vol, volym_2d)
            h2d = np.where(aktiv, volym_2d / cell_area, 0.0)

        h_1d = djup_fran_area(vol_1d / ds_1d, tf_1d, lank.max_bredd_m)

        # Uppdatera statistik
        tid += dt
        antal_steg += 1
        np.maximum(max_h2d, h2d, out=max_h2d)
        np.maximum(max_h1d, h_1d, out=max_h1d)
        np.maximum(max_q1d, q_seg, out=max_q1d)
        ny_01 = aktiv & np.isnan(tid_01_2d) & (h2d >= 0.1)
        if bool(np.any(ny_01)):
            tid_01_2d[ny_01] = np.float32(tid)
        ny_02 = aktiv & np.isnan(tid_02_2d) & (h2d >= 0.2)
        if bool(np.any(ny_02)):
            tid_02_2d[ny_02] = np.float32(tid)
        ny_ank = np.isnan(ankomst_1d) & (h_1d >= ankomst_tr_djup_m)
        if np.any(ny_ank):
            ankomst_1d[ny_ank] = tid

    lagrat_2d = float(np.sum(volym_2d))
    lagrat_1d = float(np.sum(vol_1d))
    sankt_m3 = (
        sank.avtappat_m3 + sank.infiltrerat_m3 + sank.initial_m3 if sank is not None else 0.0
    )
    tot_ut_och_lagrat = lagrat_2d + lagrat_1d + utflode_2d_m3 + utflode_1d_m3 + sankt_m3
    volymfel_rel = abs(tot_ut_och_lagrat - in_m3) / in_m3 if in_m3 > 1e-12 else 0.0
    aktiv_andel = total_aktiv_area / total_doman_area if total_doman_area > 0.0 else 0.0

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
        max_hastighet_2d_m_s=max_hast_2d.astype(np.float32),
        fara_2d_m2_s=max_fara_2d.astype(np.float32),
        tid_till_0_1m_s=tid_01_2d,
        tid_till_0_2m_s=tid_02_2d,
        in_m3=in_m3,
        avtappat_m3=sank.avtappat_m3 if sank is not None else 0.0,
        infiltrerat_m3=sank.infiltrerat_m3 if sank is not None else 0.0,
        initial_forlust_m3=sank.initial_m3 if sank is not None else 0.0,
        infiltration_m=(
            np.asarray(sank.infiltrerat_m, dtype=np.float32)
            if sank is not None and sank.ksat_m_s is not None
            else None
        ),
        sanktermer=kalltermer.beskrivning() if kalltermer is not None else None,
        regn_mark_m3=regn_mark_area_m2 * regn_summa_m,
        regn_tak_m3=tak_m2 * regn_summa_m,
        takvatten=takvatten is not None,
        takvatten_ut_rand_m3=tak_ut_m2 * regn_summa_m,
        takvatten_till_ledning_m3=tak_ledning_m2 * regn_summa_m,
        courant_hast_max=courant_hast_max,
        aktiv_cell_andel=aktiv_andel,
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
