"""Subgrid-tabeller från K1:s fina 0,25 m-raster för 2D- och hybridlösaren (T05).

Aggregerar `faktor × faktor` fina celler till grova beräkningsceller med exakt
lagringsvolymkurva V(z_s), våt area A(z_s) och subgrid-kanttrösklar med
öppningsgrad i [0, 1] så att byggnadsväggar blockerar flöde utan att kräva fin
tidsstegning över hela området.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sankgraf.flodesvagar import MANNING_N_PER_MARKSLAG
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import GeoTransformInfo


class SubgridFel(Exception):
    """Fel vid konstruktion eller uppslagning i subgrid-tabeller."""


@dataclass(frozen=True)
class SubgridTabell:
    """Förberäknade subgrid-tabeller för grova celler och cellkanter."""

    faktor: int
    z_nivaer: int
    geo_fin: GeoTransformInfo
    geo_grov: GeoTransformInfo
    mark_z_fin: NDArray[np.float32]
    markslag_fin: NDArray[np.uint8]
    # Grova celltabeller: form (hojd_grov, bredd_grov, z_nivaer)
    z_tab: NDArray[np.float64]
    volym_tab_m3: NDArray[np.float64]
    area_tab_m2: NDArray[np.float64]
    # Grova cellaggregat: form (hojd_grov, bredd_grov)
    z_min: NDArray[np.float64]
    z_max: NDArray[np.float64]
    aktiv_area_m2: NDArray[np.float64]
    aktiv_mask: NDArray[np.bool_]
    manning_n: NDArray[np.float64]
    # Subgrid-kanter i x-riktning (u): form (hojd_grov, max(0, bredd_grov - 1))
    z_kant_u: NDArray[np.float64]
    oppningsgrad_u: NDArray[np.float64]
    kant_par_z_u: NDArray[np.float64]
    kant_par_oppen_u: NDArray[np.bool_]
    # Subgrid-kanter i y-riktning (v): form (max(0, hojd_grov - 1), bredd_grov)
    z_kant_v: NDArray[np.float64]
    oppningsgrad_v: NDArray[np.float64]
    kant_par_z_v: NDArray[np.float64]
    kant_par_oppen_v: NDArray[np.bool_]
    # Förberäknade effektiva kant-trösklar (+inf på stängda delkanter) för snabb flödesgeometri
    kant_par_z_eff_u: NDArray[np.float64]
    kant_par_z_eff_v: NDArray[np.float64]

    @property
    def hojd_grov(self) -> int:
        """Antal grova cellrader."""
        return int(self.aktiv_mask.shape[0])

    @property
    def bredd_grov(self) -> int:
        """Antal grova cellkolumner."""
        return int(self.aktiv_mask.shape[1])


def _manning_per_kod_array(manning_tabell: Mapping[int, float] | None) -> NDArray[np.float64]:
    tabell = manning_tabell if manning_tabell is not None else MANNING_N_PER_MARKSLAG
    standard_n = float(tabell.get(0, 0.030))
    if not math.isfinite(standard_n) or standard_n <= 0.0:
        raise SubgridFel(f"Ogiltigt standardvärde för Manning-n: {standard_n}")
    uppslag = np.full(256, standard_n, dtype=np.float64)
    for kod, n_val in tabell.items():
        if 0 <= int(kod) < 256:
            fval = float(n_val)
            if not math.isfinite(fval) or fval <= 0.0:
                raise SubgridFel(f"Ogiltigt Manning-n ({fval}) för markslagskod {kod}.")
            uppslag[int(kod)] = fval
    return uppslag


def bygg_subgrid_tabell(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    faktor: int = 4,
    z_nivaer: int = 24,
    *,
    manning_tabell: Mapping[int, float] | None = None,
) -> SubgridTabell:
    """Bygger subgrid-tabeller direkt från K1:s fina markmodell.

    Args:
        mark_z: Fint höjdraster (float32, m RH 2000).
        markslag: Fint markslagsraster (uint8, K1-koder; 9 = byggnad).
        geo: Geotransform för det fina rastret (EPSG:3011).
        faktor: Aggregeringsfaktor per riktning (`faktor × faktor` fina celler per grov cell).
        z_nivaer: Antal tabellerade vattennivåer per grov cell (minst 2).
        manning_tabell: Valfri översättning från markslagskod till Manning-n.

    Returns:
        En `SubgridTabell` med exakta lagringsvolymkurvor, våta areor och kanttrösklar.
    """
    if mark_z.ndim != 2 or markslag.ndim != 2:
        raise SubgridFel("mark_z och markslag måste vara 2D-matriser.")
    if mark_z.shape != markslag.shape:
        raise SubgridFel(
            f"mark_z ({mark_z.shape}) och markslag ({markslag.shape}) måste ha samma form."
        )
    hojd_fin, bredd_fin = mark_z.shape
    if hojd_fin != geo.hojd or bredd_fin != geo.bredd:
        raise SubgridFel(
            f"GeoTransformInfo ({geo.hojd}x{geo.bredd}) matchar inte "
            f"raster ({hojd_fin}x{bredd_fin})."
        )
    if faktor < 1:
        raise SubgridFel(f"Subgrid-faktor måste vara >= 1, fick {faktor}.")
    if z_nivaer < 2:
        raise SubgridFel(f"Antal z-nivåer måste vara >= 2, fick {z_nivaer}.")

    dx_fin = float(geo.pixelstorlek_x)
    dy_fin = float(geo.pixelstorlek_y)
    cell_area_fin = dx_fin * dy_fin

    hojd_grov = math.ceil(hojd_fin / faktor)
    bredd_grov = math.ceil(bredd_fin / faktor)
    pad_h = hojd_grov * faktor
    pad_b = bredd_grov * faktor

    z_pad = np.full((pad_h, pad_b), np.nan, dtype=np.float64)
    slag_pad = np.full((pad_h, pad_b), np.uint8(MARKSLAG_BYGGNAD), dtype=np.uint8)
    z_pad[:hojd_fin, :bredd_fin] = np.asarray(mark_z, dtype=np.float64)
    slag_pad[:hojd_fin, :bredd_fin] = np.asarray(markslag, dtype=np.uint8)

    aktiv_fin_pad = np.asarray(
        (slag_pad != MARKSLAG_BYGGNAD) & np.isfinite(z_pad),
        dtype=np.bool_,
    )
    manning_uppslag = _manning_per_kod_array(manning_tabell)
    manning_fin_pad = manning_uppslag[slag_pad]

    z_tab = np.zeros((hojd_grov, bredd_grov, z_nivaer), dtype=np.float64)
    volym_tab_m3 = np.zeros((hojd_grov, bredd_grov, z_nivaer), dtype=np.float64)
    area_tab_m2 = np.zeros((hojd_grov, bredd_grov, z_nivaer), dtype=np.float64)
    z_min = np.zeros((hojd_grov, bredd_grov), dtype=np.float64)
    z_max = np.zeros((hojd_grov, bredd_grov), dtype=np.float64)
    aktiv_area_m2 = np.zeros((hojd_grov, bredd_grov), dtype=np.float64)
    aktiv_mask = np.zeros((hojd_grov, bredd_grov), dtype=np.bool_)
    manning_grov = np.full((hojd_grov, bredd_grov), float(manning_uppslag[0]), dtype=np.float64)

    z_block = z_pad.reshape(hojd_grov, faktor, bredd_grov, faktor).transpose(0, 2, 1, 3)
    akt_block = aktiv_fin_pad.reshape(hojd_grov, faktor, bredd_grov, faktor).transpose(0, 2, 1, 3)
    man_block = manning_fin_pad.reshape(hojd_grov, faktor, bredd_grov, faktor).transpose(0, 2, 1, 3)

    for rg in range(hojd_grov):
        for cg in range(bredd_grov):
            akt_sub = akt_block[rg, cg].ravel()
            if not bool(np.any(akt_sub)):
                continue
            z_akt = z_block[rg, cg].ravel()[akt_sub]
            man_akt = man_block[rg, cg].ravel()[akt_sub]

            aktiv_mask[rg, cg] = True
            aktiv_area_m2[rg, cg] = float(z_akt.size) * cell_area_fin
            manning_grov[rg, cg] = float(np.mean(man_akt))

            unika_z = np.unique(z_akt)
            z_lo = float(unika_z[0])
            z_hi = float(unika_z[-1])
            z_min[rg, cg] = z_lo
            z_max[rg, cg] = z_hi

            if unika_z.size <= z_nivaer:
                nivaer = np.full(z_nivaer, z_hi, dtype=np.float64)
                nivaer[: unika_z.size] = unika_z
            else:
                nivaer = np.linspace(z_lo, z_hi, z_nivaer, dtype=np.float64)

            diff = np.maximum(0.0, nivaer[:, None] - z_akt[None, :])
            vol = cell_area_fin * np.sum(diff, axis=1)
            area = cell_area_fin * np.sum(z_akt[None, :] <= nivaer[:, None], axis=1)

            z_tab[rg, cg, :] = nivaer
            volym_tab_m3[rg, cg, :] = vol
            area_tab_m2[rg, cg, :] = area

    # Subgrid-kanter i x-riktning (u): mellan (rg, cg) och (rg, cg + 1)
    if bredd_grov > 1:
        u_l_kol = np.arange(1, bredd_grov) * faktor - 1
        u_r_kol = np.arange(1, bredd_grov) * faktor
        z_ul = z_pad[:, u_l_kol].reshape(hojd_grov, faktor, bredd_grov - 1).transpose(0, 2, 1)
        z_ur = z_pad[:, u_r_kol].reshape(hojd_grov, faktor, bredd_grov - 1).transpose(0, 2, 1)
        a_ul = (
            aktiv_fin_pad[:, u_l_kol]
            .reshape(hojd_grov, faktor, bredd_grov - 1)
            .transpose(0, 2, 1)
        )
        a_ur = (
            aktiv_fin_pad[:, u_r_kol]
            .reshape(hojd_grov, faktor, bredd_grov - 1)
            .transpose(0, 2, 1)
        )
        kant_par_oppen_u = np.asarray(a_ul & a_ur, dtype=np.bool_)
        oppningsgrad_u = np.asarray(
            np.mean(kant_par_oppen_u.astype(np.float64), axis=2),
            dtype=np.float64,
        )
        kant_par_z_u = np.asarray(
            np.where(kant_par_oppen_u, np.maximum(z_ul, z_ur), np.inf),
            dtype=np.float64,
        )
        har_oppen_u = np.any(kant_par_oppen_u, axis=2)
        z_kant_u = np.asarray(
            np.where(har_oppen_u, np.min(kant_par_z_u, axis=2), np.inf),
            dtype=np.float64,
        )
    else:
        kant_par_oppen_u = np.zeros((hojd_grov, 0, faktor), dtype=np.bool_)
        oppningsgrad_u = np.zeros((hojd_grov, 0), dtype=np.float64)
        kant_par_z_u = np.zeros((hojd_grov, 0, faktor), dtype=np.float64)
        z_kant_u = np.zeros((hojd_grov, 0), dtype=np.float64)

    # Subgrid-kanter i y-riktning (v): mellan (rg, cg) och (rg + 1, cg)
    if hojd_grov > 1:
        v_t_rad = np.arange(1, hojd_grov) * faktor - 1
        v_b_rad = np.arange(1, hojd_grov) * faktor
        z_vt = z_pad[v_t_rad, :].reshape(hojd_grov - 1, bredd_grov, faktor)
        z_vb = z_pad[v_b_rad, :].reshape(hojd_grov - 1, bredd_grov, faktor)
        a_vt = aktiv_fin_pad[v_t_rad, :].reshape(hojd_grov - 1, bredd_grov, faktor)
        a_vb = aktiv_fin_pad[v_b_rad, :].reshape(hojd_grov - 1, bredd_grov, faktor)
        kant_par_oppen_v = np.asarray(a_vt & a_vb, dtype=np.bool_)
        oppningsgrad_v = np.asarray(
            np.mean(kant_par_oppen_v.astype(np.float64), axis=2),
            dtype=np.float64,
        )
        kant_par_z_v = np.asarray(
            np.where(kant_par_oppen_v, np.maximum(z_vt, z_vb), np.inf),
            dtype=np.float64,
        )
        har_oppen_v = np.any(kant_par_oppen_v, axis=2)
        z_kant_v = np.asarray(
            np.where(har_oppen_v, np.min(kant_par_z_v, axis=2), np.inf),
            dtype=np.float64,
        )
    else:
        kant_par_oppen_v = np.zeros((0, bredd_grov, faktor), dtype=np.bool_)
        oppningsgrad_v = np.zeros((0, bredd_grov), dtype=np.float64)
        kant_par_z_v = np.zeros((0, bredd_grov, faktor), dtype=np.float64)
        z_kant_v = np.zeros((0, bredd_grov), dtype=np.float64)

    geo_grov = GeoTransformInfo(
        bredd=bredd_grov,
        hojd=hojd_grov,
        x_min=geo.x_min,
        y_max=geo.y_max,
        pixelstorlek_x=dx_fin * float(faktor),
        pixelstorlek_y=dy_fin * float(faktor),
        epsg=geo.epsg,
        dtype="float32",
    )

    return SubgridTabell(
        faktor=faktor,
        z_nivaer=z_nivaer,
        geo_fin=geo,
        geo_grov=geo_grov,
        mark_z_fin=np.array(mark_z, dtype=np.float32, copy=True),
        markslag_fin=np.array(markslag, dtype=np.uint8, copy=True),
        z_tab=z_tab,
        volym_tab_m3=volym_tab_m3,
        area_tab_m2=area_tab_m2,
        z_min=z_min,
        z_max=z_max,
        aktiv_area_m2=aktiv_area_m2,
        aktiv_mask=aktiv_mask,
        manning_n=manning_grov,
        z_kant_u=z_kant_u,
        oppningsgrad_u=oppningsgrad_u,
        kant_par_z_u=kant_par_z_u,
        kant_par_oppen_u=kant_par_oppen_u,
        z_kant_v=z_kant_v,
        oppningsgrad_v=oppningsgrad_v,
        kant_par_z_v=kant_par_z_v,
        kant_par_oppen_v=kant_par_oppen_v,
        kant_par_z_eff_u=np.where(kant_par_oppen_u, kant_par_z_u, np.inf),
        kant_par_z_eff_v=np.where(kant_par_oppen_v, kant_par_z_v, np.inf),
    )


def volym_fran_vattenniva(
    grova_zs: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
) -> NDArray[np.float64]:
    """Beräknar lagrad volym V(z_s) (m³) för varje grov cell givet vattenytan `grova_zs`."""
    zs = np.asarray(grova_zs, dtype=np.float64)
    if zs.shape != subgrid.aktiv_mask.shape:
        raise SubgridFel(
            f"grova_zs {zs.shape} matchar inte subgrid {subgrid.aktiv_mask.shape}."
        )

    volym = np.zeros_like(zs, dtype=np.float64)
    z_top = subgrid.z_tab[:, :, -1]
    v_top = subgrid.volym_tab_m3[:, :, -1]

    over = subgrid.aktiv_mask & (zs >= z_top)
    if bool(np.any(over)):
        volym[over] = v_top[over] + (zs[over] - z_top[over]) * subgrid.aktiv_area_m2[over]

    mellan = subgrid.aktiv_mask & (zs > subgrid.z_min) & (zs < z_top)
    if bool(np.any(mellan)):
        zs_m = zs[mellan]
        ztab_m = subgrid.z_tab[mellan]
        vtab_m = subgrid.volym_tab_m3[mellan]
        idx = np.sum(ztab_m <= zs_m[:, None], axis=1) - 1
        idx = np.clip(idx, 0, subgrid.z_nivaer - 2)
        r_idx = np.arange(zs_m.size)
        z0 = ztab_m[r_idx, idx]
        z1 = ztab_m[r_idx, idx + 1]
        v0 = vtab_m[r_idx, idx]
        v1 = vtab_m[r_idx, idx + 1]
        dz = z1 - z0
        safe_dz = np.where(dz > 0.0, dz, 1.0)
        andel = np.where(dz > 0.0, (zs_m - z0) / safe_dz, 0.0)
        volym[mellan] = v0 + andel * (v1 - v0)

    return volym


def area_fran_vattenniva(
    grova_zs: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
) -> NDArray[np.float64]:
    """Returnerar våt area A(z_s) (m²) per grov cell givet vattenytan `grova_zs`."""
    zs = np.asarray(grova_zs, dtype=np.float64)
    if zs.shape != subgrid.aktiv_mask.shape:
        raise SubgridFel(
            f"grova_zs {zs.shape} matchar inte subgrid {subgrid.aktiv_mask.shape}."
        )

    area = np.zeros_like(zs, dtype=np.float64)
    z_top = subgrid.z_tab[:, :, -1]

    over = subgrid.aktiv_mask & (zs >= z_top)
    if bool(np.any(over)):
        area[over] = subgrid.aktiv_area_m2[over]

    mellan = subgrid.aktiv_mask & (zs >= subgrid.z_min) & (zs < z_top)
    if bool(np.any(mellan)):
        zs_m = zs[mellan]
        ztab_m = subgrid.z_tab[mellan]
        atab_m = subgrid.area_tab_m2[mellan]
        idx = np.sum(ztab_m <= zs_m[:, None], axis=1) - 1
        idx = np.clip(idx, 0, subgrid.z_nivaer - 1)
        r_idx = np.arange(zs_m.size)
        area[mellan] = atab_m[r_idx, idx]

    return area


def vattenniva_fran_volym(
    volym_m3: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
) -> NDArray[np.float64]:
    """Inverterar lagringsvolymkurvan V(z_s) exakt till vattenyta z_s (m RH 2000)."""
    v = np.maximum(0.0, np.asarray(volym_m3, dtype=np.float64))
    if v.shape != subgrid.aktiv_mask.shape:
        raise SubgridFel(
            f"volym_m3 {v.shape} matchar inte subgrid {subgrid.aktiv_mask.shape}."
        )

    zs = subgrid.z_min.copy()
    v_top = subgrid.volym_tab_m3[:, :, -1]
    z_top = subgrid.z_tab[:, :, -1]

    over = subgrid.aktiv_mask & (v > 0.0) & (v >= v_top)
    if bool(np.any(over)):
        zs[over] = z_top[over] + (v[over] - v_top[over]) / subgrid.aktiv_area_m2[over]

    mellan = subgrid.aktiv_mask & (v > 0.0) & (v < v_top)
    mellan_idx = np.flatnonzero(mellan)
    if mellan_idx.size > 0:
        v_m = v.ravel()[mellan_idx]
        n_celler = subgrid.aktiv_mask.size
        vtab_m = subgrid.volym_tab_m3.reshape(n_celler, subgrid.z_nivaer)[mellan_idx]
        ztab_m = subgrid.z_tab.reshape(n_celler, subgrid.z_nivaer)[mellan_idx]
        idx = np.count_nonzero(vtab_m <= v_m[:, None], axis=1) - 1
        np.clip(idx, 0, subgrid.z_nivaer - 2, out=idx)
        r_idx = np.arange(v_m.size)
        v0 = vtab_m[r_idx, idx]
        v1 = vtab_m[r_idx, idx + 1]
        z0 = ztab_m[r_idx, idx]
        z1 = ztab_m[r_idx, idx + 1]
        dv = v1 - v0
        andel = np.divide(v_m - v0, dv, out=np.zeros_like(v_m), where=dv > 0.0)
        zs.ravel()[mellan_idx] = z0 + andel * (z1 - z0)

    return zs


def kant_flode_geometri_u(
    grova_zs: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Beräknar `(h_eff, bredd_andel)` över grova u-kanter.

    `h_eff` är areaviktat flödesdjup (tvärsnittsarea per grov breddenhet) och
    `bredd_andel` är andelen öppna våta delkanter (`alpha` i [0, 1]), så att
    verkligt djup i gränden är `h_vat = h_eff / alpha` och friktion/Froude skalar rätt.
    """
    if subgrid.bredd_grov <= 1:
        tom = np.zeros((subgrid.hojd_grov, 0), dtype=np.float64)
        return tom, tom.copy()
    inv_f = 1.0 / float(subgrid.faktor)
    zs_kant = np.maximum(grova_zs[:, :-1], grova_zs[:, 1:])
    diff = zs_kant[:, :, None] - subgrid.kant_par_z_eff_u
    np.maximum(diff, 0.0, out=diff)
    h_eff = np.asarray(np.sum(diff, axis=2) * inv_f, dtype=np.float64)
    bredd_andel = np.asarray(np.count_nonzero(diff > 0.0, axis=2) * inv_f, dtype=np.float64)
    return h_eff, bredd_andel


def kant_flode_geometri_v(
    grova_zs: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Beräknar `(h_eff, bredd_andel)` över grova v-kanter."""
    if subgrid.hojd_grov <= 1:
        tom = np.zeros((0, subgrid.bredd_grov), dtype=np.float64)
        return tom, tom.copy()
    inv_f = 1.0 / float(subgrid.faktor)
    zs_kant = np.maximum(grova_zs[:-1, :], grova_zs[1:, :])
    diff = zs_kant[:, :, None] - subgrid.kant_par_z_eff_v
    np.maximum(diff, 0.0, out=diff)
    h_eff = np.asarray(np.sum(diff, axis=2) * inv_f, dtype=np.float64)
    bredd_andel = np.asarray(np.count_nonzero(diff > 0.0, axis=2) * inv_f, dtype=np.float64)
    return h_eff, bredd_andel


def kant_flodesdjup_u(
    grova_zs: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
) -> NDArray[np.float64]:
    """Beräknar effektivt subgrid-flödesdjup (m) över grova u-kanter.

    Beaktar varje enskild fin delkant längs den grova cellgränsen: byggnadsceller
    ger bidrag 0, så en helt täckt vägg (`oppningsgrad_u == 0`) ger exakt 0 flödesdjup.
    """
    return kant_flode_geometri_u(grova_zs, subgrid)[0]


def kant_flodesdjup_v(
    grova_zs: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
) -> NDArray[np.float64]:
    """Beräknar effektivt subgrid-flödesdjup (m) över grova v-kanter."""
    return kant_flode_geometri_v(grova_zs, subgrid)[0]


def _expandera_grovt_raster(
    grovt: NDArray[np.float64],
    faktor: int,
    hojd_fin: int,
    bredd_fin: int,
) -> NDArray[np.float64]:
    if faktor == 1:
        return grovt
    ut = np.repeat(np.repeat(grovt, faktor, axis=0), faktor, axis=1)
    return np.asarray(ut[:hojd_fin, :bredd_fin], dtype=np.float64)


def projicera_djup_till_fin(
    grova_zs: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
) -> NDArray[np.float32]:
    """Projicerar grov vattenyta z_s tillbaka till fint 0,25 m djup (0 på byggnader)."""
    zs = np.asarray(grova_zs, dtype=np.float64)
    if zs.shape != subgrid.aktiv_mask.shape:
        raise SubgridFel(
            f"grova_zs {zs.shape} matchar inte subgrid {subgrid.aktiv_mask.shape}."
        )
    hojd_fin, bredd_fin = subgrid.mark_z_fin.shape
    zs_fin = _expandera_grovt_raster(zs, subgrid.faktor, hojd_fin, bredd_fin)
    aktiv_fin = (subgrid.markslag_fin != MARKSLAG_BYGGNAD) & np.isfinite(subgrid.mark_z_fin)
    djup = np.where(
        aktiv_fin,
        np.maximum(0.0, zs_fin - np.asarray(subgrid.mark_z_fin, dtype=np.float64)),
        0.0,
    )
    return np.asarray(djup, dtype=np.float32)


def projicera_falt_till_fin(
    grovt_falt: NDArray[np.floating[Any]],
    subgrid: SubgridTabell,
    *,
    byggnad_varde: float = 0.0,
) -> NDArray[np.float32]:
    """Projicerar ett grovt skalärfält (t.ex. hastighet eller fara) till det fina rastret."""
    falt = np.asarray(grovt_falt, dtype=np.float64)
    if falt.shape != subgrid.aktiv_mask.shape:
        raise SubgridFel(
            f"grovt_falt {falt.shape} matchar inte subgrid {subgrid.aktiv_mask.shape}."
        )
    hojd_fin, bredd_fin = subgrid.mark_z_fin.shape
    falt_fin = _expandera_grovt_raster(falt, subgrid.faktor, hojd_fin, bredd_fin)
    aktiv_fin = (subgrid.markslag_fin != MARKSLAG_BYGGNAD) & np.isfinite(subgrid.mark_z_fin)
    ut = np.where(aktiv_fin, falt_fin, float(byggnad_varde))
    return np.asarray(ut, dtype=np.float32)
