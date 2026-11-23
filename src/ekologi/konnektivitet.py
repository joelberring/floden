"""Grafkonnektivitet: kostnadsavstånd, PC, ECA, dPC-dekomponering och före/efter."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import sparse
from scipy.sparse.csgraph import dijkstra

from ekologi.habitat import extrahera_habitatflackar
from ekologi.krets import los_kretspar
from ekologi.motstand import bygg_motstandsyta
from ekologi.typer import (
    ArtProfil,
    EkologiFel,
    FlackBetydelse,
    FloatRaster,
    ForeEfterEkologi,
    HabitatKarta,
    KonnektivitetsResultat,
    MotstandsYta,
)

_STEG_4: tuple[tuple[int, int, float], ...] = ((0, 1, 1.0), (1, 0, 1.0))
_STEG_8: tuple[tuple[int, int, float], ...] = (
    (0, 1, 1.0),
    (1, 0, 1.0),
    (1, 1, math.sqrt(2.0)),
    (1, -1, math.sqrt(2.0)),
)


def berakna_kostnadsavstand(
    motstand: MotstandsYta,
    habitat: HabitatKarta,
    *,
    grannskap: int = 8,
) -> FloatRaster:
    """Beräknar minsta kostnadsavstånd `d*_ij` (m) mellan alla par av habitatfläckar.

    Använder multi-source Dijkstra på rutnätet med kantkostnad `0.5 * (R_u + R_v) * cell_m * l_uv`.
    Avståndet från en fläck till sig själv är 0.0; oåtkomliga fläckar får `np.inf`.
    """
    m = len(habitat.flackar)
    if m == 0:
        return np.zeros((0, 0), dtype=np.float64)
    if m == 1:
        return np.zeros((1, 1), dtype=np.float64)
    if grannskap not in (4, 8):
        raise EkologiFel(f"grannskap måste vara 4 eller 8, fick {grannskap}.")

    r_arr = motstand.motstand
    ny, nx = r_arr.shape
    n_tot = ny * nx
    steg = _STEG_4 if grannskap == 4 else _STEG_8

    u_delar: list[NDArray[np.int32]] = []
    v_delar: list[NDArray[np.int32]] = []
    c_delar: list[NDArray[np.float64]] = []
    for dr, dc, langd in steg:
        r0 = max(0, -dr)
        r1 = min(ny, ny - dr)
        c0 = max(0, -dc)
        c1 = min(nx, nx - dc)
        if r0 >= r1 or c0 >= c1:
            continue
        ra = r_arr[r0:r1, c0:c1]
        rb = r_arr[r0 + dr : r1 + dr, c0 + dc : c1 + dc]
        aktiv = np.isfinite(ra) & np.isfinite(rb)
        if not np.any(aktiv):
            continue
        rr, cc = np.nonzero(aktiv)
        rr_g = rr + r0
        cc_g = cc + c0
        u_idx = (rr_g * nx + cc_g).astype(np.int32)
        v_idx = ((rr_g + dr) * nx + (cc_g + dc)).astype(np.int32)
        kostnad = 0.5 * (ra[rr, cc] + rb[rr, cc]) * motstand.cell_m * langd
        u_delar.append(u_idx)
        v_delar.append(v_idx)
        c_delar.append(kostnad)

    if not u_delar:
        dist = np.full((m, m), math.inf, dtype=np.float64)
        np.fill_diagonal(dist, 0.0)
        return dist

    rader = np.concatenate([np.concatenate(u_delar), np.concatenate(v_delar)])
    kols = np.concatenate([np.concatenate(v_delar), np.concatenate(u_delar)])
    data = np.concatenate([np.concatenate(c_delar), np.concatenate(c_delar)])
    graf = sparse.csr_matrix((data, (rader, kols)), shape=(n_tot, n_tot))

    flack_flat = habitat.flack_raster.ravel()
    flack_celler = [np.flatnonzero(flack_flat == f.flack_id) for f in habitat.flackar]

    dist = np.full((m, m), math.inf, dtype=np.float64)
    np.fill_diagonal(dist, 0.0)

    for i in range(m):
        d_fran_i = dijkstra(graf, directed=False, indices=flack_celler[i], min_only=True)
        for j in range(i + 1, m):
            d_ij = float(np.min(d_fran_i[flack_celler[j]]))
            dist[i, j] = d_ij
            dist[j, i] = d_ij

    return dist


def berakna_pc_eca(
    areor_m2: NDArray[np.float64],
    kostnadsavstand_m: FloatRaster,
    spridningsavstand_m: float,
    analys_area_m2: float,
    *,
    flack_ids: tuple[int, ...] | None = None,
    p0: float = 0.5,
) -> tuple[float, float, FloatRaster, tuple[FlackBetydelse, ...]]:
    """Beräknar PC, ECA (m²), p*_ij samt Saura & Rubio (2010) dPC_intra/flux/connector."""
    if not (math.isfinite(spridningsavstand_m) and spridningsavstand_m > 0.0):
        raise EkologiFel(f"spridningsavstand_m måste vara > 0, fick {spridningsavstand_m}.")
    if not (math.isfinite(analys_area_m2) and analys_area_m2 > 0.0):
        raise EkologiFel(f"analys_area_m2 måste vara > 0, fick {analys_area_m2}.")

    a = np.asarray(areor_m2, dtype=np.float64)
    m = int(a.size)
    if m == 0:
        return 0.0, 0.0, np.zeros((0, 0), dtype=np.float64), ()
    ids = flack_ids if flack_ids is not None else tuple(range(1, m + 1))

    alpha = -math.log(p0) / float(spridningsavstand_m)
    d = np.asarray(kostnadsavstand_m, dtype=np.float64)
    if d.shape != (m, m):
        raise EkologiFel(f"kostnadsavstand_m har form {d.shape}, förväntade {(m, m)}.")

    # Maximal produktsannolikhet p*_ij = exp(-min vägsumma av alpha * d_rs)
    neg_log_p = np.where(np.isfinite(d), alpha * np.maximum(0.0, d), math.inf)
    np.fill_diagonal(neg_log_p, 0.0)
    kortast_log = dijkstra(sparse.csr_matrix(neg_log_p), directed=False)
    p_stjarna = np.where(np.isfinite(kortast_log), np.exp(-kortast_log), 0.0)
    np.fill_diagonal(p_stjarna, 1.0)

    pc_num = float(a @ p_stjarna @ a)
    eca_m2 = math.sqrt(max(0.0, pc_num))
    pc = pc_num / (float(analys_area_m2) ** 2)

    betydelse: list[FlackBetydelse] = []
    if pc_num <= 0.0:
        for fid in ids:
            betydelse.append(FlackBetydelse(fid, 0.0, 0.0, 0.0, 0.0))
        return pc, eca_m2, p_stjarna, tuple(betydelse)

    for k in range(m):
        intra_num = float(a[k] * a[k])
        # Flux: alla par (i, j) där i == k eller j == k (exkl. k == k)
        flux_num = float(2.0 * a[k] * (np.sum(a * p_stjarna[k, :]) - a[k]))

        # Total PC utan fläck k (dPC_k) -> connector = dPC_k - intra - flux
        if m == 1:
            pc_num_utan_k = 0.0
        else:
            kvar = [idx for idx in range(m) if idx != k]
            sub_log = neg_log_p[np.ix_(kvar, kvar)]
            sub_kort = dijkstra(sparse.csr_matrix(sub_log), directed=False)
            sub_p = np.where(np.isfinite(sub_kort), np.exp(-sub_kort), 0.0)
            np.fill_diagonal(sub_p, 1.0)
            a_sub = a[kvar]
            pc_num_utan_k = float(a_sub @ sub_p @ a_sub)

        dpc_tot_num = max(0.0, pc_num - pc_num_utan_k)
        conn_num = max(0.0, dpc_tot_num - intra_num - flux_num)

        betydelse.append(
            FlackBetydelse(
                flack_id=ids[k],
                dpc_procent=100.0 * dpc_tot_num / pc_num,
                dpc_intra_procent=100.0 * intra_num / pc_num,
                dpc_flux_procent=100.0 * flux_num / pc_num,
                dpc_connector_procent=100.0 * conn_num / pc_num,
            )
        )

    return pc, eca_m2, p_stjarna, tuple(betydelse)


def berakna_konnektivitet(
    markslag: NDArray[np.uint8] | NDArray[np.integer[Any]],
    profil: ArtProfil,
    *,
    cell_m: float,
    x_min_m: float = 0.0,
    y_max_m: float = 0.0,
    kronhojd_m: NDArray[np.float64] | NDArray[np.float32] | None = None,
    kvalitet: NDArray[np.float64] | NDArray[np.float32] | None = None,
    motstand_faktor: float = 1.0,
    spridning_faktor: float = 1.0,
    extra_barriar_mask: NDArray[np.bool_] | None = None,
    extra_motstand: NDArray[np.float64] | NDArray[np.float32] | None = None,
    berakna_flaskhalsar: bool = False,
    grannskap_kostnad: int = 8,
    grannskap_krets: int = 4,
) -> KonnektivitetsResultat:
    """Helhetsberäkning av ekologisk konnektivitet (PC, ECA, dPC och valfri flaskhalskarta)."""
    if not (math.isfinite(spridning_faktor) and spridning_faktor > 0.0):
        raise EkologiFel(f"spridning_faktor måste vara > 0, fick {spridning_faktor}.")

    habitat = extrahera_habitatflackar(
        markslag,
        profil,
        cell_m=cell_m,
        x_min_m=x_min_m,
        y_max_m=y_max_m,
        kronhojd_m=kronhojd_m,
        kvalitet=kvalitet,
    )
    motstand = bygg_motstandsyta(
        markslag,
        profil,
        cell_m=cell_m,
        motstand_faktor=motstand_faktor,
        extra_barriar_mask=extra_barriar_mask,
        extra_motstand=extra_motstand,
    )
    d_mat = berakna_kostnadsavstand(motstand, habitat, grannskap=grannskap_kostnad)
    areor = np.array([f.effektiv_area_m2 for f in habitat.flackar], dtype=np.float64)
    flack_ids = tuple(f.flack_id for f in habitat.flackar)
    eff_spridning = profil.spridningsavstand_m * float(spridning_faktor)

    pc, eca_m2, p_stjarna, betydelse = berakna_pc_eca(
        areor,
        d_mat,
        eff_spridning,
        habitat.analys_area_m2,
        flack_ids=flack_ids,
    )

    strom_sam: FloatRaster | None = None
    if berakna_flaskhalsar and len(habitat.flackar) >= 2:
        strom_acc = np.zeros(motstand.motstand.shape, dtype=np.float64)
        vikt_sum = 0.0
        m = len(habitat.flackar)
        for i in range(m):
            mask_i = habitat.flack_raster == habitat.flackar[i].flack_id
            for j in range(i + 1, m):
                vikt = float(areor[i] * areor[j] * p_stjarna[i, j])
                if vikt <= 0.0:
                    continue
                mask_j = habitat.flack_raster == habitat.flackar[j].flack_id
                par = los_kretspar(
                    motstand,
                    mask_i,
                    mask_j,
                    grannskap=grannskap_krets,
                    fran_flack_id=habitat.flackar[i].flack_id,
                    till_flack_id=habitat.flackar[j].flack_id,
                )
                if par.sammankopplad:
                    strom_acc += vikt * par.stromtathet
                    vikt_sum += vikt
        strom_sam = strom_acc / vikt_sum if vikt_sum > 0.0 else strom_acc

    return KonnektivitetsResultat(
        profil_id=profil.profil_id,
        antal_flackar=len(habitat.flackar),
        total_habitat_area_m2=float(np.sum(areor)),
        analys_area_m2=habitat.analys_area_m2,
        pc=pc,
        eca_m2=eca_m2,
        kostnadsavstand_m=d_mat,
        p_stjarna=p_stjarna,
        flack_betydelse=betydelse,
        stromtathet_sammanlagd=strom_sam,
    )


def jamfor_fore_efter_ekologi(
    markslag_fore: NDArray[np.uint8] | NDArray[np.integer[Any]],
    markslag_efter: NDArray[np.uint8] | NDArray[np.integer[Any]],
    profil: ArtProfil,
    *,
    cell_m: float,
    berakna_flaskhalsar: bool = False,
) -> ForeEfterEkologi:
    """Beräknar deterministisk före/efter-skillnad i ECA, PC och habitatyta."""
    fore = berakna_konnektivitet(
        markslag_fore,
        profil,
        cell_m=cell_m,
        berakna_flaskhalsar=berakna_flaskhalsar,
    )
    efter = berakna_konnektivitet(
        markslag_efter,
        profil,
        cell_m=cell_m,
        berakna_flaskhalsar=berakna_flaskhalsar,
    )
    delta_eca = efter.eca_m2 - fore.eca_m2
    delta_pct = 100.0 * delta_eca / fore.eca_m2 if fore.eca_m2 > 0.0 else 0.0
    return ForeEfterEkologi(
        fore=fore,
        efter=efter,
        delta_eca_m2=delta_eca,
        delta_eca_procent=delta_pct,
        delta_pc=efter.pc - fore.pc,
        delta_habitat_area_m2=efter.total_habitat_area_m2 - fore.total_habitat_area_m2,
    )
