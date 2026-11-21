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


def _uppdatera_narmaste_batch(
    saknad_e: NDArray[np.float64],
    saknad_n: NDArray[np.float64],
    cand_e: NDArray[np.float64],
    cand_n: NDArray[np.float64],
    cand_z: NDArray[np.float64],
    best_d2: NDArray[np.float64],
    best_z: NDArray[np.float64],
) -> None:
    """Uppdaterar närmaste punkt för saknade celler i små batchar för att undvika OOM."""
    cell_batch = 64
    pt_batch = 8192
    n_celler = saknad_e.size
    n_punkter = cand_e.size
    for c0 in range(0, n_celler, cell_batch):
        c1 = min(n_celler, c0 + cell_batch)
        ce = saknad_e[c0:c1, None]
        cn = saknad_n[c0:c1, None]
        lok_idx_arr = np.arange(c1 - c0)
        for p0 in range(0, n_punkter, pt_batch):
            p1 = min(n_punkter, p0 + pt_batch)
            de = ce - cand_e[None, p0:p1]
            dn = cn - cand_n[None, p0:p1]
            d2 = de * de + dn * dn
            min_idx = np.argmin(d2, axis=1)
            min_d2 = d2[lok_idx_arr, min_idx]
            battre = min_d2 < best_d2[c0:c1]
            best_d2[c0:c1] = np.where(battre, min_d2, best_d2[c0:c1])
            best_z[c0:c1] = np.where(battre, cand_z[p0 + min_idx], best_z[c0:c1])


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
            n_b_max = float(n_vektor[r0])
            n_b_min = float(n_vektor[r1 - 1])
            by_lo = max(0, int(math.floor((n_b_min - max_avstand_m - by0) / bucket_m)))
            by_hi = min(n_by - 1, int(math.floor((n_b_max + max_avstand_m - by0) / bucket_m)))

            for k0 in range(0, kolumner, cell_block):
                k1 = min(kolumner, k0 + cell_block)
                e_b_min = float(e_vektor[k0])
                e_b_max = float(e_vektor[k1 - 1])
                bx_lo = max(0, int(math.floor((e_b_min - max_avstand_m - bx0) / bucket_m)))
                bx_hi = min(
                    n_bx - 1, int(math.floor((e_b_max + max_avstand_m - bx0) / bucket_m))
                )

                kand_idx_delar: list[NDArray[np.int64]] = []
                if by_lo <= by_hi and bx_lo <= bx_hi:
                    for by_i in range(by_lo, by_hi + 1):
                        for bx_i in range(bx_lo, bx_hi + 1):
                            bid = by_i * n_bx + bx_i
                            s0 = int(offsets[bid])
                            s1 = int(offsets[bid + 1])
                            if s1 > s0:
                                kand_idx_delar.append(np.arange(s0, s1, dtype=np.int64))

                grid_e, grid_n = np.meshgrid(e_vektor[k0:k1], n_vektor[r0:r1])

                if kand_idx_delar:
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
                    saknad = ~har_inom
                else:
                    avst = np.full(grid_e.shape, np.inf, dtype=np.float64)
                    z_block = np.zeros(grid_e.shape, dtype=np.float64)
                    saknad = np.ones(grid_e.shape, dtype=np.bool_)

                if np.any(saknad):
                    saknad_e = grid_e[saknad]
                    saknad_n = grid_n[saknad]
                    avst_saknad = avst[saknad]
                    best_d2 = np.where(np.isfinite(avst_saknad), avst_saknad * avst_saknad, np.inf)
                    best_z = z_block[saknad].copy()

                    har_prev = by_lo <= by_hi and bx_lo <= bx_hi
                    if har_prev:
                        base_by0, base_by1 = by_lo, by_hi
                        base_bx0, base_bx1 = bx_lo, bx_hi
                        prev_by0, prev_by1 = by_lo, by_hi
                        prev_bx0, prev_bx1 = bx_lo, bx_hi
                    else:
                        mitt_by = int(math.floor((0.5 * (n_b_min + n_b_max) - by0) / bucket_m))
                        mitt_bx = int(math.floor((0.5 * (e_b_min + e_b_max) - bx0) / bucket_m))
                        base_by0 = base_by1 = max(0, min(n_by - 1, mitt_by))
                        base_bx0 = base_bx1 = max(0, min(n_bx - 1, mitt_bx))
                        prev_by0, prev_by1 = 1, 0
                        prev_bx0, prev_bx1 = 1, 0

                    radie = 1
                    while True:
                        ry0 = max(0, base_by0 - radie)
                        ry1 = min(n_by - 1, base_by1 + radie)
                        rx0 = max(0, base_bx0 - radie)
                        rx1 = min(n_bx - 1, base_bx1 + radie)
                        if (
                            har_prev
                            and ry0 == prev_by0
                            and ry1 == prev_by1
                            and rx0 == prev_bx0
                            and rx1 == prev_bx1
                        ):
                            break

                        ring_delar: list[NDArray[np.int64]] = []
                        for by_i in range(ry0, ry1 + 1):
                            for bx_i in range(rx0, rx1 + 1):
                                if (
                                    har_prev
                                    and prev_by0 <= by_i <= prev_by1
                                    and prev_bx0 <= bx_i <= prev_bx1
                                ):
                                    continue
                                bid = by_i * n_bx + bx_i
                                s0 = int(offsets[bid])
                                s1 = int(offsets[bid + 1])
                                if s1 > s0:
                                    ring_delar.append(np.arange(s0, s1, dtype=np.int64))

                        if ring_delar:
                            ring_kand = np.concatenate(ring_delar)
                            _uppdatera_narmaste_batch(
                                saknad_e,
                                saknad_n,
                                e_s[ring_kand],
                                n_s[ring_kand],
                                z_s[ring_kand],
                                best_d2,
                                best_z,
                            )

                        har_prev = True
                        prev_by0, prev_by1 = ry0, ry1
                        prev_bx0, prev_bx1 = rx0, rx1

                        if np.all(np.isfinite(best_d2)):
                            max_best = math.sqrt(float(np.max(best_d2)))
                            kant_avst: list[float] = []
                            if rx0 > 0:
                                kant_avst.append(e_b_min - (bx0 + rx0 * bucket_m))
                            if rx1 < n_bx - 1:
                                kant_avst.append((bx0 + (rx1 + 1) * bucket_m) - e_b_max)
                            if ry0 > 0:
                                kant_avst.append(n_b_min - (by0 + ry0 * bucket_m))
                            if ry1 < n_by - 1:
                                kant_avst.append((by0 + (ry1 + 1) * bucket_m) - n_b_max)
                            if not kant_avst or max_best <= min(kant_avst):
                                break
                        radie *= 2

                    if not np.all(np.isfinite(best_d2)):
                        _uppdatera_narmaste_batch(
                            saknad_e,
                            saknad_n,
                            e_s,
                            n_s,
                            z_s,
                            best_d2,
                            best_z,
                        )

                    avst[saknad] = np.sqrt(best_d2)
                    z_block[saknad] = best_z

                avstand_m[r0:r1, k0:k1] = avst.astype(np.float32)
                mark_z[r0:r1, k0:k1] = z_block.astype(np.float32)

    return mark_z, avstand_m, tathet
