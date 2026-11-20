"""Markklassfiltrering och deterministisk markraster-interpolering (IDW + avstånd + täthet)."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from markmodell.rutnat import Ruta

LAS_MARKKLASS = 2
LAS_BROKLASS = 17


class InterpoleringFel(Exception):
    """Fel vid filtrering eller interpolering av markpunkter."""


def filtrera_laser_klasser(
    punkter_e: NDArray[np.float64],
    punkter_n: NDArray[np.float64],
    punkter_z: NDArray[np.float64],
    klasser: NDArray[np.int32] | NDArray[np.uint8] | None = None,
    behall_klass: int = LAS_MARKKLASS,
    exkludera_klasser: tuple[int, ...] = (LAS_BROKLASS,),
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Filtrerar punktmoln till rena markpunkter (t.ex. klass 2, utan broar klass 17)."""
    e = np.asarray(punkter_e, dtype=np.float64)
    n = np.asarray(punkter_n, dtype=np.float64)
    z = np.asarray(punkter_z, dtype=np.float64)
    if not (e.ndim == 1 and n.ndim == 1 and z.ndim == 1 and e.size == n.size == z.size):
        raise InterpoleringFel(
            "punkter_e, punkter_n och punkter_z måste vara 1D-vektorer av samma längd."
        )

    giltig = np.isfinite(e) & np.isfinite(n) & np.isfinite(z)
    if klasser is not None:
        k = np.asarray(klasser)
        if k.shape != e.shape:
            raise InterpoleringFel("klasser måste ha samma längd som punktkoordinaterna.")
        giltig &= k == behall_klass
        for exkl in exkludera_klasser:
            giltig &= k != exkl

    return e[giltig], n[giltig], z[giltig]


def _berakna_tathet_och_celler(
    e: NDArray[np.float64],
    n: NDArray[np.float64],
    z: NDArray[np.float64],
    ruta: Ruta,
    sokradie_m: float,
) -> tuple[NDArray[np.float32], NDArray[np.float64], NDArray[np.int32]]:
    """Bygger cellvis medelhöjd, antal punkter per cell och punkttäthet per m²."""
    rader, kolumner = ruta.form_rc
    e_min, _, _, n_max = ruta.yttre_bbox
    uppl = ruta.upplosning_m

    kol_idx = np.floor((e - e_min) / uppl).astype(np.int64)
    rad_idx = np.floor((n_max - n) / uppl).astype(np.int64)

    inom = (kol_idx >= 0) & (kol_idx < kolumner) & (rad_idx >= 0) & (rad_idx < rader)
    kol_g = kol_idx[inom]
    rad_g = rad_idx[inom]
    z_g = z[inom]

    antal_per_cell = np.zeros((rader, kolumner), dtype=np.int32)
    summa_z = np.zeros((rader, kolumner), dtype=np.float64)
    if z_g.size > 0:
        np.add.at(antal_per_cell, (rad_g, kol_idx[inom]), 1)
        np.add.at(summa_z, (rad_g, kol_g), z_g)

    cell_z = np.full((rader, kolumner), np.nan, dtype=np.float64)
    har_punkt = antal_per_cell > 0
    cell_z[har_punkt] = summa_z[har_punkt] / antal_per_cell[har_punkt]

    # Punkttäthet i punkter/m² beräknad med 2D-boxsumma i fönster runt varje cell
    halv_fonster = max(1, int(round(sokradie_m / uppl)))
    if 2 * halv_fonster + 1 > min(rader, kolumner):
        halv_fonster = max(1, (min(rader, kolumner) - 1) // 2)
    area_fonster_m2 = ((2 * halv_fonster + 1) * uppl) ** 2

    # Integralbild (summed area table) i numpy för O(R*C) punkttäthet utan scipy
    padded = np.pad(antal_per_cell.astype(np.float64), 1, mode="constant", constant_values=0.0)
    sat = np.cumsum(np.cumsum(padded, axis=0), axis=1)

    r_indices = np.arange(rader)
    k_indices = np.arange(kolumner)
    r0 = np.clip(r_indices - halv_fonster, 0, rader - 1)
    r1 = np.clip(r_indices + halv_fonster + 1, 1, rader)
    k0 = np.clip(k_indices - halv_fonster, 0, kolumner - 1)
    k1 = np.clip(k_indices + halv_fonster + 1, 1, kolumner)

    box_sum = (
        sat[np.ix_(r1, k1)]
        - sat[np.ix_(r0, k1)]
        - sat[np.ix_(r1, k0)]
        + sat[np.ix_(r0, k0)]
    )
    tathet = (box_sum / area_fonster_m2).astype(np.float32)

    return tathet, cell_z, antal_per_cell


def interpolera_markpunkter(
    punkter_e: NDArray[np.float64],
    punkter_n: NDArray[np.float64],
    punkter_z: NDArray[np.float64],
    ruta: Ruta,
    max_avstand_m: float = 3.0,
    klasser: NDArray[np.int32] | NDArray[np.uint8] | None = None,
    idw_potens: float = 2.0,
    tathet_radie_m: float = 1.0,
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.float32]]:
    """Interpolerar markpunkter till ett 0,25 m markraster samt avstånds- och täthetskarta.

    Algoritmen är helt deterministisk och kräver bara NumPy:
    1. Filtrerar bort icke-markpunkter (och broar klass 17) om `klasser` ges.
    2. Använder blockbaserad lokal IDW (invers avståndsviktning) mot cellcentra.
       För celler utan punkter inom `max_avstand_m` sker reservinterpolering mot
       närmaste punkt inom ett vidgat sökfönster eller globalt medelvärde, samtidigt
       som exakt euklidiskt avstånd `avstand_m` sparas så att osäkerhetsmodellen kan
       flagga celler > `max_avstand_m`.

    Returns:
        `(mark_z, avstand_till_punkt_m, punkt_tathet_per_m2)` som float32-matriser.
    """
    if not math.isfinite(max_avstand_m) or max_avstand_m <= 0.0:
        raise InterpoleringFel(f"Ogiltigt max_avstand_m: {max_avstand_m}")
    if not math.isfinite(idw_potens) or idw_potens <= 0.0:
        raise InterpoleringFel(f"Ogiltig idw_potens: {idw_potens}")

    e, n, z = filtrera_laser_klasser(punkter_e, punkter_n, punkter_z, klasser=klasser)
    if e.size == 0:
        raise InterpoleringFel("Inga giltiga markpunkter kvar efter klassfiltrering.")

    # Begränsa punktmängden till punkter inom rimlig buffert runt yttre bbox
    e_min_y, n_min_y, e_max_y, n_max_y = ruta.yttre_bbox
    buffert = max(max_avstand_m * 2.0, 10.0)
    buf_mask = (
        (e >= e_min_y - buffert)
        & (e <= e_max_y + buffert)
        & (n >= n_min_y - buffert)
        & (n <= n_max_y + buffert)
    )
    if np.any(buf_mask):
        e = e[buf_mask]
        n = n[buf_mask]
        z = z[buf_mask]

    # Sortera punkter deterministiskt på (e, n, z)
    ordning = np.lexsort((z, n, e))
    e = e[ordning]
    n = n[ordning]
    z = z[ordning]

    tathet, _, _ = _berakna_tathet_och_celler(e, n, z, ruta, sokradie_m=tathet_radie_m)

    rader, kolumner = ruta.form_rc
    e_vektor, n_vektor = ruta.cell_centra_en()
    mark_z = np.empty((rader, kolumner), dtype=np.float32)
    avstand_m = np.empty((rader, kolumner), dtype=np.float32)

    # Använd blockindelning i rader för att hålla minnesåtgången låg även för 2200x2200
    # samt rumsligt rutindex över punkter.
    max_r2 = max_avstand_m * max_avstand_m
    halv_potens = 0.5 * idw_potens
    block_storlek = 64

    # Rumslig bucketing av punkter i rutstorlek = max(max_avstand_m, 1.0)
    bucket_m = max(max_avstand_m, 1.0)
    bx0 = float(np.min(e))
    by0 = float(np.min(n))
    p_bx = np.floor((e - bx0) / bucket_m).astype(np.int64)
    p_by = np.floor((n - by0) / bucket_m).astype(np.int64)
    n_bx = int(np.max(p_bx)) + 1
    n_by = int(np.max(p_by)) + 1

    # Bygg CSR-liknande index för snabb uppslagning av punkter i närliggande buckets
    flat_b = p_by * n_bx + p_bx
    b_ordning = np.argsort(flat_b, kind="mergesort")
    e_s = e[b_ordning]
    n_s = n[b_ordning]
    z_s = z[b_ordning]
    flat_s = flat_b[b_ordning]
    counts = np.bincount(flat_s, minlength=n_bx * n_by)
    offsets = np.zeros(n_bx * n_by + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])

    # Använd direkt vektorisering om antalet punkter är litet/måttligt, annars per bucket-block
    if e_s.size <= 4096:
        for r0 in range(0, rader, block_storlek):
            r1 = min(rader, r0 + block_storlek)
            grid_e, grid_n = np.meshgrid(e_vektor, n_vektor[r0:r1])
            de = grid_e[..., None] - e_s[None, None, :]
            dn = grid_n[..., None] - n_s[None, None, :]
            d2 = de * de + dn * dn

            min_d2 = np.min(d2, axis=2)
            min_idx = np.argmin(d2, axis=2)
            avst = np.sqrt(min_d2)
            avstand_m[r0:r1, :] = avst.astype(np.float32)

            inom = d2 <= max_r2
            har_inom = np.any(inom, axis=2)

            d2_safe = np.where(d2 > 1e-12, d2, 1e-12)
            vikter = np.where(inom, np.power(d2_safe, -halv_potens), 0.0)
            vikt_sum = np.sum(vikter, axis=2)
            z_idw = np.sum(vikter * z_s[None, None, :], axis=2) / np.where(
                vikt_sum > 0.0, vikt_sum, 1.0
            )
            z_reserv = z_s[min_idx]
            mark_z[r0:r1, :] = np.where(har_inom, z_idw, z_reserv).astype(np.float32)
    else:
        # Bucket-baserad exakt sökning för stora punktmoln (500 m-ruta = ~500k punkter)
        # Dela upp i rader och kolumner om 32x32 celler (~8x8 m)
        cell_block = 32
        for r0 in range(0, rader, cell_block):
            r1 = min(rader, r0 + cell_block)
            n_b_max = n_vektor[r0]
            n_b_min = n_vektor[r1 - 1]
            by_lo = max(0, int(math.floor((n_b_min - max_avstand_m - by0) / bucket_m)))
            by_hi = min(n_by - 1, int(math.floor((n_b_max + max_avstand_m - by0) / bucket_m)))

            for k0 in range(0, kolumner, cell_block):
                k1 = min(kolumner, k0 + cell_block)
                e_b_min = e_vektor[k0]
                e_b_max = e_vektor[k1 - 1]
                bx_lo = max(0, int(math.floor((e_b_min - max_avstand_m - bx0) / bucket_m)))
                bx_hi = min(n_bx - 1, int(math.floor((e_b_max + max_avstand_m - bx0) / bucket_m)))

                kand_idx_delar: list[NDArray[np.int64]] = []
                for by_i in range(by_lo, by_hi + 1):
                    for bx_i in range(bx_lo, bx_hi + 1):
                        bid = by_i * n_bx + bx_i
                        s0 = int(offsets[bid])
                        s1 = int(offsets[bid + 1])
                        if s1 > s0:
                            kand_idx_delar.append(np.arange(s0, s1, dtype=np.int64))

                grid_e, grid_n = np.meshgrid(e_vektor[k0:k1], n_vektor[r0:r1])

                if not kand_idx_delar:
                    # Fallback mot hela punktmängden för avstånd och närmaste punkt
                    de_all = grid_e[..., None] - e_s[None, None, :]
                    dn_all = grid_n[..., None] - n_s[None, None, :]
                    d2_all = de_all * de_all + dn_all * dn_all
                    min_idx = np.argmin(d2_all, axis=2)
                    avstand_m[r0:r1, k0:k1] = np.sqrt(np.min(d2_all, axis=2)).astype(np.float32)
                    mark_z[r0:r1, k0:k1] = z_s[min_idx].astype(np.float32)
                    continue

                kand = np.concatenate(kand_idx_delar)
                ek = e_s[kand]
                nk = n_s[kand]
                zk = z_s[kand]

                de = grid_e[..., None] - ek[None, None, :]
                dn = grid_n[..., None] - nk[None, None, :]
                d2 = de * de + dn * dn

                min_d2 = np.min(d2, axis=2)
                min_idx = np.argmin(d2, axis=2)
                avst = np.sqrt(min_d2)
                inom = d2 <= max_r2
                har_inom = np.any(inom, axis=2)

                d2_safe = np.where(d2 > 1e-12, d2, 1e-12)
                vikter = np.where(inom, np.power(d2_safe, -halv_potens), 0.0)
                vikt_sum = np.sum(vikter, axis=2)
                z_idw = np.sum(vikter * zk[None, None, :], axis=2) / np.where(
                    vikt_sum > 0.0, vikt_sum, 1.0
                )
                z_block = np.where(har_inom, z_idw, zk[min_idx])

                # För celler där inte ens den vidgade bucketen hade punkt inom max_avstand_m
                # används avstånd till alla punkter i den vidgade sökningen om den täcker.
                if not np.all(har_inom):
                    de_all = grid_e[..., None] - e_s[None, None, :]
                    dn_all = grid_n[..., None] - n_s[None, None, :]
                    d2_all = de_all * de_all + dn_all * dn_all
                    saknad = ~har_inom
                    min_d2_all = np.min(d2_all, axis=2)
                    min_idx_all = np.argmin(d2_all, axis=2)
                    avst = np.where(saknad, np.sqrt(min_d2_all), avst)
                    z_block = np.where(saknad, z_s[min_idx_all], z_block)

                avstand_m[r0:r1, k0:k1] = avst.astype(np.float32)
                mark_z[r0:r1, k0:k1] = z_block.astype(np.float32)

    return mark_z, avstand_m, tathet
