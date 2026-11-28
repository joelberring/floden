"""Grafkonnektivitet: kostnadsavstånd, PC, ECA, dPC-dekomponering och före/efter."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import sparse
from scipy.sparse.csgraph import dijkstra

from ekologi.habitat import extrahera_habitatflackar
from ekologi.krets import (
    STANDARD_CG_TROSKEL,
    forbered_kretsgraf,
    kontrollera_ekologi_celler,
    los_kretspar_forberedd,
)
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

STANDARD_MAX_EXAKTA_FLACKAR = 20
STANDARD_MAX_KRETSPAR = 60
STANDARD_KRETSPAR_VIKTMAL = 0.98
STANDARD_ADAPTIV_KRETS_MAX_CELLER = 30_000
_STOR_KRETS_CG_RTOL = 1e-7
_STOR_KRETS_CG_TROSKEL = 10_000


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
    kontrollera_ekologi_celler(int(motstand.motstand.size))
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


def _grovsampla_konduktans(
    g: FloatRaster,
    faktor: int,
) -> FloatRaster:
    """Deterministisk blockmedelkonduktans över `faktor × faktor`-block (bevarar barriärer)."""
    ny, nx = g.shape
    ny_c = math.ceil(ny / faktor)
    nx_c = math.ceil(nx / faktor)
    pad_y = ny_c * faktor - ny
    pad_x = nx_c * faktor - nx
    if pad_y == 0 and pad_x == 0:
        return np.asarray(
            g.reshape(ny_c, faktor, nx_c, faktor).mean(axis=(1, 3)),
            dtype=np.float64,
        )
    g_pad = np.pad(g, ((0, pad_y), (0, pad_x)), mode="constant", constant_values=0.0)
    giltig = np.ones((ny, nx), dtype=np.float64)
    giltig_pad = np.pad(giltig, ((0, pad_y), (0, pad_x)), mode="constant", constant_values=0.0)
    sum_g = g_pad.reshape(ny_c, faktor, nx_c, faktor).sum(axis=(1, 3))
    antal = giltig_pad.reshape(ny_c, faktor, nx_c, faktor).sum(axis=(1, 3))
    return np.asarray(sum_g / np.maximum(antal, 1.0), dtype=np.float64)


def _grovsampla_mask(mask: NDArray[np.bool_], faktor: int) -> NDArray[np.bool_]:
    """Returnerar grov boolesk mask: `faktor × faktor`-block är sant om någon cell är sann."""
    ny, nx = mask.shape
    ny_c = math.ceil(ny / faktor)
    nx_c = math.ceil(nx / faktor)
    pad_y = ny_c * faktor - ny
    pad_x = nx_c * faktor - nx
    m_pad = (
        mask
        if (pad_y == 0 and pad_x == 0)
        else np.pad(mask, ((0, pad_y), (0, pad_x)), mode="constant", constant_values=False)
    )
    return np.asarray(
        m_pad.reshape(ny_c, faktor, nx_c, faktor).any(axis=(1, 3)),
        dtype=np.bool_,
    )


def _uppsampla_kretsraster(
    grov: FloatRaster,
    form: tuple[int, int],
    faktor: int,
    aktiv_fin: NDArray[np.bool_],
) -> FloatRaster:
    """Återställer grovt kretsraster till fin form med barriärceller (`g == 0`) exakt nollade."""
    ny, nx = form
    fin = np.repeat(np.repeat(grov, faktor, axis=0), faktor, axis=1)[:ny, :nx]
    return np.asarray(np.where(aktiv_fin, fin, 0.0), dtype=np.float64)


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
    undantag_min_area: NDArray[np.bool_] | None = None,
    berakna_flaskhalsar: bool = False,
    grannskap_kostnad: int = 8,
    grannskap_krets: int = 4,
    max_kretspar: int | None = None,
    kretspar_viktmal: float = STANDARD_KRETSPAR_VIKTMAL,
) -> KonnektivitetsResultat:
    """Helhetsberäkning av ekologisk konnektivitet (PC, ECA, dPC och valfri flaskhalskarta).

    ECA, PC och dPC beräknas alltid exakt över samtliga habitatfläckar i full upplösning.
    För små fläcknät (`<= 20` fläckar, utan explicit `max_kretspar`) beräknas samtliga fläckpar
    i full upplösning (bitidentiskt med facit G1–G6). För stora tätortsfönster (`> 20` fläckar)
    prioriteras de flödestyngsta paren (`A_i * A_j * p*_ij`, upp till 98 % ackumulerad flödesvikt
    och högst `STANDARD_MAX_KRETSPAR = 60` par) på förberäknad Laplacian i adaptiv kretsupplösning.
    """
    if not (math.isfinite(spridning_faktor) and spridning_faktor > 0.0):
        raise EkologiFel(f"spridning_faktor måste vara > 0, fick {spridning_faktor}.")
    if max_kretspar is not None and (isinstance(max_kretspar, bool) or int(max_kretspar) < 1):
        raise EkologiFel(f"max_kretspar måste vara >= 1, fick {max_kretspar!r}.")
    if not (math.isfinite(kretspar_viktmal) and 0.0 < kretspar_viktmal <= 1.0):
        raise EkologiFel(f"kretspar_viktmal måste ligga i (0, 1], fick {kretspar_viktmal!r}.")
    kontrollera_ekologi_celler(int(markslag.size))

    habitat = extrahera_habitatflackar(
        markslag,
        profil,
        cell_m=cell_m,
        x_min_m=x_min_m,
        y_max_m=y_max_m,
        kronhojd_m=kronhojd_m,
        kvalitet=kvalitet,
        undantag_min_area=undantag_min_area,
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
    kansl_sam: FloatRaster | None = None
    antal_par_beraknade = 0
    antal_par_totalt = 0
    vikt_andel = 1.0
    krets_faktor = 1

    if berakna_flaskhalsar and len(habitat.flackar) >= 2:
        m = len(habitat.flackar)
        alla_par: list[tuple[float, int, int]] = []
        for i in range(m):
            for j in range(i + 1, m):
                vikt = float(areor[i] * areor[j] * p_stjarna[i, j])
                if vikt > 0.0:
                    alla_par.append((vikt, i, j))
        antal_par_totalt = len(alla_par)
        stort_problem = m > STANDARD_MAX_EXAKTA_FLACKAR or max_kretspar is not None

        if not alla_par:
            strom_sam = np.zeros(motstand.motstand.shape, dtype=np.float64)
            kansl_sam = np.zeros(motstand.motstand.shape, dtype=np.float64)
        elif not stort_problem:
            # Små problem (<= 20 fläckar): lös samtliga par i full upplösning på förberäknad graf.
            graf = forbered_kretsgraf(motstand, grannskap=grannskap_krets)
            flack_masker = [habitat.flack_raster == f.flack_id for f in habitat.flackar]
            strom_acc = np.zeros(motstand.motstand.shape, dtype=np.float64)
            kansl_acc = np.zeros(motstand.motstand.shape, dtype=np.float64)
            vikt_sum = 0.0
            for vikt, i, j in alla_par:
                par = los_kretspar_forberedd(
                    graf,
                    flack_masker[i],
                    flack_masker[j],
                    fran_flack_id=habitat.flackar[i].flack_id,
                    till_flack_id=habitat.flackar[j].flack_id,
                    cg_troskel=STANDARD_CG_TROSKEL,
                    cg_rtol=1e-10,
                )
                antal_par_beraknade += 1
                if par.sammankopplad:
                    strom_acc += vikt * par.stromtathet
                    kansl_acc += vikt * par.kanslighet_cell
                    vikt_sum += vikt
            strom_sam = strom_acc / vikt_sum if vikt_sum > 0.0 else strom_acc
            kansl_sam = kansl_acc / vikt_sum if vikt_sum > 0.0 else kansl_acc
        else:
            # Stora tätortsfönster: topp-parbudget + förberäknad Laplacian + adaptiv skala.
            alla_par.sort(key=lambda rad: (-rad[0], rad[1], rad[2]))
            total_vikt = float(sum(rad[0] for rad in alla_par))
            tak = STANDARD_MAX_KRETSPAR if max_kretspar is None else int(max_kretspar)
            valda_par: list[tuple[float, int, int]] = []
            ack_vikt = 0.0
            for rad in alla_par:
                valda_par.append(rad)
                ack_vikt += rad[0]
                if len(valda_par) >= tak or (
                    total_vikt > 0.0 and ack_vikt >= kretspar_viktmal * total_vikt
                ):
                    break
            vikt_andel = min(1.0, ack_vikt / total_vikt) if total_vikt > 0.0 else 1.0

            n_tot = int(motstand.konduktans.size)
            if n_tot > STANDARD_ADAPTIV_KRETS_MAX_CELLER:
                krets_faktor = max(
                    2, math.ceil(math.sqrt(n_tot / STANDARD_ADAPTIV_KRETS_MAX_CELLER))
                )
                g_grov = _grovsampla_konduktans(motstand.konduktans, krets_faktor)
                r_grov = np.where(g_grov > 0.0, 1.0 / np.where(g_grov > 0.0, g_grov, 1.0), math.inf)
                motstand_krets = MotstandsYta(
                    motstand=r_grov,
                    konduktans=g_grov,
                    cell_m=motstand.cell_m * krets_faktor,
                    profil_id=motstand.profil_id,
                )
            else:
                motstand_krets = motstand

            graf = forbered_kretsgraf(motstand_krets, grannskap=grannskap_krets)
            anvanda_idx = sorted({idx for _, i, j in valda_par for idx in (i, j)})
            mask_per_idx: dict[int, NDArray[np.bool_]] = {}
            for idx in anvanda_idx:
                fin_mask = (habitat.flack_raster == habitat.flackar[idx].flack_id) & (
                    motstand.konduktans > 0.0
                )
                mask_per_idx[idx] = (
                    _grovsampla_mask(fin_mask, krets_faktor) if krets_faktor > 1 else fin_mask
                )

            strom_acc = np.zeros(motstand_krets.motstand.shape, dtype=np.float64)
            kansl_acc = np.zeros(motstand_krets.motstand.shape, dtype=np.float64)
            vikt_sum = 0.0
            for vikt, i, j in valda_par:
                mask_i = mask_per_idx[i]
                mask_j = mask_per_idx[j]
                if krets_faktor > 1 and np.any(mask_i & mask_j):
                    overlapp = mask_i & mask_j
                    mi_ren = mask_i & ~overlapp
                    mj_ren = mask_j & ~overlapp
                    if np.any(mi_ren) and np.any(mj_ren):
                        mask_i, mask_j = mi_ren, mj_ren
                    elif np.any(mi_ren):
                        mask_i = mi_ren
                    elif np.any(mj_ren):
                        mask_j = mj_ren
                    else:
                        continue
                par = los_kretspar_forberedd(
                    graf,
                    mask_i,
                    mask_j,
                    fran_flack_id=habitat.flackar[i].flack_id,
                    till_flack_id=habitat.flackar[j].flack_id,
                    cg_troskel=min(STANDARD_CG_TROSKEL, _STOR_KRETS_CG_TROSKEL),
                    cg_rtol=_STOR_KRETS_CG_RTOL,
                )
                antal_par_beraknade += 1
                if par.sammankopplad:
                    strom_acc += vikt * par.stromtathet
                    kansl_acc += vikt * par.kanslighet_cell
                    vikt_sum += vikt

            strom_grov = strom_acc / vikt_sum if vikt_sum > 0.0 else strom_acc
            kansl_grov = kansl_acc / vikt_sum if vikt_sum > 0.0 else kansl_acc
            if krets_faktor > 1:
                aktiv_fin = motstand.konduktans > 0.0
                strom_sam = _uppsampla_kretsraster(
                    strom_grov, motstand.motstand.shape, krets_faktor, aktiv_fin
                )
                kansl_sam = _uppsampla_kretsraster(
                    kansl_grov, motstand.motstand.shape, krets_faktor, aktiv_fin
                )
            else:
                strom_sam = strom_grov
                kansl_sam = kansl_grov

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
        kanslighet_sammanlagd=kansl_sam,
        antal_kretspar_beraknade=antal_par_beraknade,
        antal_kretspar_totalt=antal_par_totalt,
        kretspar_vikt_andel=vikt_andel,
        krets_upplosningsfaktor=krets_faktor,
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
