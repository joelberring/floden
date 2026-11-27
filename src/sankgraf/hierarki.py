"""Priority-Flood / Fill–Spill–Merge sänkhierarki och rutgränssammansättning (T02).

Algoritmen använder strikt **4-grannskap (N, S, Ö, V)** så att 1-cellsbreda barriärer
och brytlinjer (kantstenar, murar) aldrig läcker diagonalt och så att volymer och
sadelnivåer på ortogonala rutnät (A1–A9) blir maskinexakta.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sankgraf.flodesvagar import STANDARD_REFERENSDJUP_M, bygg_flodesvagar
from sankgraf.rust_brygga import (
    GransparArrayer,
    kor_sankgraf_karna_rust,
    valj_sankgraf_karna,
)
from sankgraf.typer import (
    BYGGNAD_NOD_ID,
    HAV_NOD_ID,
    MARKSLAG_BYGGNAD,
    MARKSLAG_HARDGJORT,
    TYP_SANKA,
    TYP_SLUTSANKA,
    InstangtOmradeFel,
    SankGraf,
    SankGrafFel,
    SankNod,
    VolymPunkt,
    YtPunkt,
)
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, OppningPost

# Intern markör i flow_to för celler på botten av en lövsänka (skiljs från HAV=-1 och BYGGNAD=-2)
_BOTTEN_MARKOR = -3
_SADEL_TIE_TOL_M = 1e-9
_INT32_MAX = (1 << 31) - 1

IndexArray = NDArray[np.signedinteger[Any]]


def _supercover_celler(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> list[tuple[int, int]]:
    """4-sammanhängande supercover-rasterisering i kontinuerliga rutnätskoordinater."""
    kol = math.floor(x0)
    rad = math.floor(y0)
    slut_kol = math.floor(x1)
    slut_rad = math.floor(y1)
    celler: list[tuple[int, int]] = [(rad, kol)]

    dx = x1 - x0
    dy = y1 - y0
    steg_x = 1 if dx > 0.0 else (-1 if dx < 0.0 else 0)
    steg_y = 1 if dy > 0.0 else (-1 if dy < 0.0 else 0)
    if steg_x == 0 and steg_y == 0:
        return celler

    t_dx = abs(1.0 / dx) if dx != 0.0 else math.inf
    t_dy = abs(1.0 / dy) if dy != 0.0 else math.inf
    if dx > 0.0:
        t_max_x = ((kol + 1.0) - x0) * t_dx
    elif dx < 0.0:
        t_max_x = (x0 - kol) * t_dx
    else:
        t_max_x = math.inf

    if dy > 0.0:
        t_max_y = ((rad + 1.0) - y0) * t_dy
    elif dy < 0.0:
        t_max_y = (y0 - rad) * t_dy
    else:
        t_max_y = math.inf

    max_steg = abs(slut_kol - kol) + abs(slut_rad - rad) + 4
    eps = 1e-12
    for _ in range(max_steg):
        if kol == slut_kol and rad == slut_rad:
            break
        if abs(t_max_x - t_max_y) <= eps:
            if steg_x != 0:
                celler.append((rad, kol + steg_x))
            if steg_y != 0:
                celler.append((rad + steg_y, kol))
            kol += steg_x
            rad += steg_y
            t_max_x += t_dx
            t_max_y += t_dy
            celler.append((rad, kol))
        elif t_max_x < t_max_y:
            kol += steg_x
            t_max_x += t_dx
            celler.append((rad, kol))
        else:
            rad += steg_y
            t_max_y += t_dy
            celler.append((rad, kol))

    if (slut_rad, slut_kol) not in celler:
        sr, sc = celler[-1]
        if abs(slut_rad - sr) == 1 and abs(slut_kol - sc) == 1:
            celler.append((sr, slut_kol))
        celler.append((slut_rad, slut_kol))
    return celler


def _distans_till_segment(
    e_grid: NDArray[np.float64],
    n_grid: NDArray[np.float64],
    e0: float,
    n0: float,
    e1: float,
    n1: float,
) -> NDArray[np.float64]:
    de = e1 - e0
    dn = n1 - n0
    l2 = de * de + dn * dn
    if l2 == 0.0:
        return np.asarray(np.hypot(e_grid - e0, n_grid - n0), dtype=np.float64)
    t = np.clip(((e_grid - e0) * de + (n_grid - n0) * dn) / l2, 0.0, 1.0)
    return np.asarray(np.hypot(e_grid - (e0 + t * de), n_grid - (n0 + t * dn)), dtype=np.float64)


def _applicera_oppningar(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    oppningar: Sequence[OppningPost],
) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    """Bränner in trummor, portar och passager idempotent i `(mark_z, markslag)`."""
    ny_z = np.array(mark_z, dtype=np.float32, copy=True)
    ny_slag = np.array(markslag, dtype=np.uint8, copy=True)
    if not oppningar:
        return ny_z, ny_slag

    hojd, bredd = ny_z.shape
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    e_vek = geo.x_min + (np.arange(bredd, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(hojd, dtype=np.float64) + 0.5) * dy

    for opp in oppningar:
        if not opp.koordinater:
            continue
        halv_bredd = 0.5 * float(opp.bredd_m)
        le = [float(p[0]) for p in opp.koordinater]
        ln = [float(p[1]) for p in opp.koordinater]
        o_emin, o_emax = min(le) - halv_bredd, max(le) + halv_bredd
        o_nmin, o_nmax = min(ln) - halv_bredd, max(ln) + halv_bredd

        if o_emax < geo.x_min or o_emin > geo.x_max or o_nmax < geo.y_min or o_nmin > geo.y_max:
            continue

        kol0 = max(0, math.floor((o_emin - geo.x_min) / dx) - 1)
        kol1 = min(bredd, math.ceil((o_emax - geo.x_min) / dx) + 1)
        rad0 = max(0, math.floor((geo.y_max - o_nmax) / dy) - 1)
        rad1 = min(hojd, math.ceil((geo.y_max - o_nmin) / dy) + 1)
        if kol1 <= kol0 or rad1 <= rad0:
            continue

        sub_e, sub_n = np.meshgrid(e_vek[kol0:kol1], n_vek[rad0:rad1])
        mask = np.zeros(sub_e.shape, dtype=bool)

        if len(opp.koordinater) == 1:
            e0, n0 = opp.koordinater[0]
            mask |= np.hypot(sub_e - e0, sub_n - n0) <= halv_bredd
        else:
            for idx in range(len(opp.koordinater) - 1):
                e0, n0 = opp.koordinater[idx]
                e1, n1 = opp.koordinater[idx + 1]
                mask |= _distans_till_segment(sub_e, sub_n, e0, n0, e1, n1) <= halv_bredd
                x0 = (e0 - geo.x_min) / dx
                y0 = (geo.y_max - n0) / dy
                x1 = (e1 - geo.x_min) / dx
                y1 = (geo.y_max - n1) / dy
                for r, c in _supercover_celler(x0, y0, x1, y1):
                    if rad0 <= r < rad1 and kol0 <= c < kol1:
                        mask[r - rad0, c - kol0] = True

        if not np.any(mask):
            continue

        z_sub = ny_z[rad0:rad1, kol0:kol1]
        slag_sub = ny_slag[rad0:rad1, kol0:kol1]
        botten = np.float32(opp.botten_rh2000)
        z_sub[mask] = np.minimum(z_sub[mask], botten)
        slag_sub[mask & (slag_sub == MARKSLAG_BYGGNAD)] = np.uint8(MARKSLAG_HARDGJORT)

    return ny_z, ny_slag


def _berakna_kantbarriarer(
    z_flat: NDArray[np.float64],
    aktiv_flat: NDArray[np.bool_],
    geo: GeoTransformInfo,
    brytlinjer: Sequence[BrytlinjePost],
) -> dict[tuple[int, int], float]:
    """Mappar 2D- och 3D-brytlinjer till täta tröskelnivåer på korsade 4-grannekanter.

    En brytlinje som skär dualkanten mellan två aktiva grannceller `(u, v)` ger kanten:
    - vid 3D-brytlinje (`z_kron_rh2000` satt): `max(z_u, z_v, z_linje(x_snitt, y_snitt))`,
      linjärt interpolerad exakt längs `LineStringZ`;
    - vid 2D-brytlinje: `max(z_u, z_v) + hojd_m`.

    Vid exakta hörnsnitt genom cellcentrum spärras båda angränsande dualkanterna (tät
    supercover-spärr) så att vatten aldrig kan läcka diagonalt vid 20° eller 45°.
    """
    barriarer: dict[tuple[int, int], float] = {}
    if not brytlinjer:
        return barriarer

    hojd = geo.hojd
    bredd = geo.bredd
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    tol = 1e-9

    def _lagg_till_kant(u: int, v: int, hojd_m: float, z_kron_snitt: float | None) -> None:
        if not (aktiv_flat[u] and aktiv_flat[v]):
            return
        nyckel = (u, v) if u < v else (v, u)
        bas_z = max(float(z_flat[u]), float(z_flat[v]))
        kant_z = max(bas_z, z_kron_snitt) if z_kron_snitt is not None else bas_z + hojd_m
        tidigare = barriarer.get(nyckel)
        if tidigare is None or kant_z > tidigare:
            barriarer[nyckel] = kant_z

    for post in brytlinjer:
        hojd_m = float(post.hojd_m)
        z_kron = post.z_kron_rh2000
        har_3d = z_kron is not None and len(z_kron) == len(post.koordinater)
        if not har_3d and hojd_m <= 0.0:
            continue
        if len(post.koordinater) < 2:
            continue
        for idx in range(len(post.koordinater) - 1):
            e0, n0 = post.koordinater[idx]
            e1, n1 = post.koordinater[idx + 1]
            x0 = (e0 - geo.x_min) / dx
            y0 = (geo.y_max - n0) / dy
            x1 = (e1 - geo.x_min) / dx
            y1 = (geo.y_max - n1) / dy
            zk0 = float(z_kron[idx]) if (har_3d and z_kron is not None) else None
            zk1 = float(z_kron[idx + 1]) if (har_3d and z_kron is not None) else None

            def _z_vid(t_val: float, a: float | None = zk0, b: float | None = zk1) -> float | None:
                if a is None or b is None:
                    return None
                return a + t_val * (b - a)

            # Vertikala dualkanter: (r, c) <-> (r + 1, c) vid x = c + 0.5, y in [r + 0.5, r + 1.5]
            if abs(x1 - x0) > 1e-12:
                c_min = max(0, math.floor(min(x0, x1) - 0.5) - 1)
                c_max = min(bredd - 1, math.ceil(max(x0, x1) - 0.5) + 1)
                for c in range(c_min, c_max + 1):
                    t = ((c + 0.5) - x0) / (x1 - x0)
                    if -tol <= t <= 1.0 + tol:
                        t_kl = max(0.0, min(1.0, t))
                        y_hit = y0 + t_kl * (y1 - y0)
                        z_snitt = _z_vid(t_kl)
                        r_f = y_hit - 0.5
                        r = math.floor(r_f)
                        if 0 <= r < hojd - 1:
                            _lagg_till_kant(r * bredd + c, (r + 1) * bredd + c, hojd_m, z_snitt)
                        # Om linjen passerar exakt genom cellcentrum (y_hit == r_int + 0.5):
                        # spärra BÅDA vertikala dualkanterna kring r_int (noll diagonalt läckage).
                        if abs(r_f - round(r_f)) <= tol:
                            r_int = round(r_f)
                            if 0 <= r_int - 1 < hojd - 1:
                                _lagg_till_kant(
                                    (r_int - 1) * bredd + c, r_int * bredd + c, hojd_m, z_snitt
                                )
                            if 0 <= r_int < hojd - 1:
                                _lagg_till_kant(
                                    r_int * bredd + c, (r_int + 1) * bredd + c, hojd_m, z_snitt
                                )

            # Horisontella dualkanter: (r, c) <-> (r, c + 1), y = r + 0.5, x in [c+0.5, c+1.5]
            if abs(y1 - y0) > 1e-12:
                r_min = max(0, math.floor(min(y0, y1) - 0.5) - 1)
                r_max = min(hojd - 1, math.ceil(max(y0, y1) - 0.5) + 1)
                for r in range(r_min, r_max + 1):
                    t = ((r + 0.5) - y0) / (y1 - y0)
                    if -tol <= t <= 1.0 + tol:
                        t_kl = max(0.0, min(1.0, t))
                        x_hit = x0 + t_kl * (x1 - x0)
                        z_snitt = _z_vid(t_kl)
                        c_f = x_hit - 0.5
                        c = math.floor(c_f)
                        if 0 <= c < bredd - 1:
                            _lagg_till_kant(r * bredd + c, r * bredd + (c + 1), hojd_m, z_snitt)
                        if abs(c_f - round(c_f)) <= tol:
                            c_int = round(c_f)
                            if 0 <= c_int - 1 < bredd - 1:
                                _lagg_till_kant(
                                    r * bredd + (c_int - 1), r * bredd + c_int, hojd_m, z_snitt
                                )
                            if 0 <= c_int < bredd - 1:
                                _lagg_till_kant(
                                    r * bredd + c_int, r * bredd + (c_int + 1), hojd_m, z_snitt
                                )

    return barriarer


def _berakna_avrinningsomraden(
    mark_z: NDArray[np.float32],
    aktiv: NDArray[np.bool_],
    kantbarriarer: dict[tuple[int, int], float],
) -> tuple[IndexArray, IndexArray, int]:
    """Beräknar 4-granne-brantaste lutning, lövsänkor och `cell_till_nod`.

    Använder `np.int32` internt när `n_celler < 2**31 - 1` för halverad minnesåtgång.
    Returnerar `(flow_to, cell_till_nod, antal_lov)`.
    """
    hojd, bredd = mark_z.shape
    n_celler = hojd * bredd
    idx_dtype: type[np.int32] | type[np.int64] = np.int32 if n_celler < _INT32_MAX else np.int64

    # Float32->float64 är strikt ordningsbevarande för ändliga tal: undvik onödig 8-byte z64-kopia
    z_2d: NDArray[np.float32] | NDArray[np.float64] = (
        mark_z if mark_z.dtype == np.float32 else np.asarray(mark_z, dtype=np.float64)
    )
    z_flat = z_2d.ravel()
    aktiv_flat = aktiv.ravel()

    # Vektoriserad brantaste nedförsriktning i 4-grannskap (uint8-riktning i stället för idx_2d)
    basta_z = z_2d.copy()
    dir_2d = np.zeros((hojd, bredd), dtype=np.uint8)

    # Kontrollera N (1), S (2), V (3), Ö (4) i deterministisk ordning
    skift = (
        (1, slice(1, hojd), slice(0, bredd), slice(0, hojd - 1), slice(0, bredd)),
        (2, slice(0, hojd - 1), slice(0, bredd), slice(1, hojd), slice(0, bredd)),
        (3, slice(0, hojd), slice(1, bredd), slice(0, hojd), slice(0, bredd - 1)),
        (4, slice(0, hojd), slice(0, bredd - 1), slice(0, hojd), slice(1, bredd)),
    )
    for rikt_kod, sr_u, sc_u, sr_v, sc_v in skift:
        giltig = aktiv[sr_u, sc_u] & aktiv[sr_v, sc_v]
        z_v = z_2d[sr_v, sc_v]
        basta_vy = basta_z[sr_u, sc_u]
        battre = giltig & (z_v < basta_vy)
        if np.any(battre):
            basta_vy[battre] = z_v[battre]
            dir_2d[sr_u, sc_u][battre] = rikt_kod
    del basta_z

    dir_flat = dir_2d.ravel()
    del dir_2d
    flow_to: IndexArray = np.arange(n_celler, dtype=idx_dtype)
    steg_offset = np.array([0, -bredd, bredd, -1, 1], dtype=idx_dtype)
    flow_to += steg_offset[dir_flat]
    flow_to[dir_flat == 0] = BYGGNAD_NOD_ID
    del dir_flat

    # Korrigera celler som gränsar till en kantbarriär
    if kantbarriarer:
        berorda: set[int] = set()
        for u, v in kantbarriarer:
            berorda.add(u)
            berorda.add(v)
        for u in sorted(berorda):
            if not aktiv_flat[u]:
                continue
            ur, uc = divmod(u, bredd)
            zu = float(z_flat[u])
            min_z = zu
            mal = BYGGNAD_NOD_ID
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                vr, vc = ur + dr, uc + dc
                if 0 <= vr < hojd and 0 <= vc < bredd:
                    v = vr * bredd + vc
                    if not aktiv_flat[v]:
                        continue
                    nyckel = (u, v) if u < v else (v, u)
                    if nyckel in kantbarriarer and kantbarriarer[nyckel] > zu:
                        continue
                    zv = float(z_flat[v])
                    if zv < min_z:
                        min_z = zv
                        mal = v
            flow_to[u] = mal

    har_lagre = flow_to >= 0
    cell_till_nod: IndexArray = np.full(n_celler, BYGGNAD_NOD_ID, dtype=idx_dtype)
    besokt_platt = np.zeros(n_celler, dtype=bool)
    antal_lov = 0

    def _ar_rand(u_idx: int) -> bool:
        r, c = divmod(u_idx, bredd)
        return bool(r == 0 or r == hojd - 1 or c == 0 or c == bredd - 1)

    def _tillganglig_platt_kant(u_idx: int, v_idx: int, z_niv: float) -> bool:
        if not kantbarriarer:
            return True
        nyckel = (u_idx, v_idx) if u_idx < v_idx else (v_idx, u_idx)
        return kantbarriarer.get(nyckel, z_niv) <= z_niv

    # Hantera alla aktiva celler utan strikt lägre granne (plan, randutlopp och lövsänkor)
    platt_kandidater = np.flatnonzero(aktiv_flat & ~har_lagre)
    for start_u_np in platt_kandidater:
        start_u = int(start_u_np)
        if besokt_platt[start_u]:
            continue

        z_f = float(z_flat[start_u])
        # Samla hela 4-sammanhängande likahöjdsplatån (inklusive gränsceller med lägre granne)
        komponent: list[int] = []
        utlopp: list[int] = []
        ko: deque[int] = deque([start_u])
        besokt_platt[start_u] = True

        while ko:
            curr = ko.popleft()
            komponent.append(curr)
            if har_lagre[curr] or _ar_rand(curr):
                utlopp.append(curr)

            cr, cc = divmod(curr, bredd)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = cr + dr, cc + dc
                if 0 <= nr < hojd and 0 <= nc < bredd:
                    nxt = nr * bredd + nc
                    if (
                        aktiv_flat[nxt]
                        and not besokt_platt[nxt]
                        and float(z_flat[nxt]) == z_f
                        and _tillganglig_platt_kant(curr, nxt, z_f)
                    ):
                        besokt_platt[nxt] = True
                        ko.append(nxt)

        if not utlopp:
            # Ingen lägre granne och ingen randcell -> sann lövsänka!
            lov_id = antal_lov
            antal_lov += 1
            for cell in komponent:
                cell_till_nod[cell] = lov_id
                flow_to[cell] = _BOTTEN_MARKOR
        else:
            # Platån dränerar ut via `utlopp`: multi-source BFS inåt över platån
            utlopp.sort()
            bfs_ko: deque[int] = deque()
            tilldelad: set[int] = set(utlopp)
            komp_set = set(komponent)
            for u_ut in utlopp:
                if not har_lagre[u_ut]:
                    flow_to[u_ut] = HAV_NOD_ID
                    cell_till_nod[u_ut] = HAV_NOD_ID
                bfs_ko.append(u_ut)

            while bfs_ko:
                curr = bfs_ko.popleft()
                cr, cc = divmod(curr, bredd)
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nr, nc = cr + dr, cc + dc
                    if 0 <= nr < hojd and 0 <= nc < bredd:
                        nxt = nr * bredd + nc
                        if (
                            nxt in komp_set
                            and nxt not in tilldelad
                            and _tillganglig_platt_kant(curr, nxt, z_f)
                        ):
                            tilldelad.add(nxt)
                            flow_to[nxt] = curr
                            bfs_ko.append(nxt)

    del besokt_platt, platt_kandidater, har_lagre

    # Tilldela alla kvarvarande aktiva celler sin lövsänka (eller HAV) via vektoriserad
    # pekarfördubbling på `ptr` (O(log(väglängd)) steg, bara en temporär int32-array).
    ej_satt = aktiv_flat & (cell_till_nod == BYGGNAD_NOD_ID)
    if np.any(ej_satt):
        neg_flode = ej_satt & (flow_to < 0)
        if np.any(neg_flode):
            cell_till_nod[neg_flode] = HAV_NOD_ID
            ej_satt &= ~neg_flode
        del neg_flode
        if np.any(ej_satt):
            ptr: IndexArray = np.arange(n_celler, dtype=idx_dtype)
            ptr[ej_satt] = flow_to[ej_satt]
            max_steg = int(math.ceil(math.log2(max(n_celler, 2)))) + 2
            for _ in range(max_steg):
                ny_ptr = ptr[ptr]
                if np.array_equal(ny_ptr, ptr):
                    del ny_ptr
                    break
                ptr = ny_ptr
            cell_till_nod[ej_satt] = cell_till_nod[ptr[ej_satt]]
            del ptr
    del ej_satt

    return (
        flow_to.reshape((hojd, bredd)),
        cell_till_nod.reshape((hojd, bredd)),
        antal_lov,
    )


def _bygg_granspar(
    catch_2d: IndexArray,
    aktiv: NDArray[np.bool_],
    eff_z: NDArray[np.float32],
    kantbarriarer: dict[tuple[int, int], float],
) -> tuple[
    NDArray[np.float64],
    IndexArray,
    IndexArray,
    NDArray[np.int64],
    NDArray[np.int64],
    IndexArray,
    IndexArray,
    IndexArray,
    IndexArray,
]:
    """Vektoriserad gränsanalys mellan distinkta avrinningsområden (och mot HAV).

    Ersätter cell-för-cell-Python-loopen och `par_kanter`-dict med kompakta NumPy-arrayer
    sorterade med `np.lexsort` på `(c_lo, c_hi, w, u, v)`. Returnerar par sorterade i
    stigande `(w_min, c_lo, c_hi)` för Kruskal-sammanslagningsträdet.
    """
    hojd, bredd = catch_2d.shape
    n_celler = hojd * bredd
    idx_dtype: type[np.int32] | type[np.int64] = np.int32 if n_celler < _INT32_MAX else np.int64

    # Vertikala grannkanter där avrinningsområdena skiljer sig åt
    v_diff = (catch_2d[:-1, :] != catch_2d[1:, :]) & aktiv[:-1, :] & aktiv[1:, :]
    vr, vc = np.nonzero(v_diff)
    del v_diff
    vr_i: IndexArray = np.asarray(vr, dtype=idx_dtype)
    vc_i: IndexArray = np.asarray(vc, dtype=idx_dtype)
    u_v: IndexArray = vr_i * bredd + vc_i
    v_v: IndexArray = u_v + bredd
    cu_v: IndexArray = np.asarray(catch_2d[vr, vc], dtype=idx_dtype)
    cv_v: IndexArray = np.asarray(catch_2d[vr + 1, vc], dtype=idx_dtype)
    w_v = np.maximum(eff_z[vr, vc], eff_z[vr + 1, vc]).astype(np.float64)
    del vr, vc, vr_i, vc_i

    # Horisontella grannkanter där avrinningsområdena skiljer sig åt
    h_diff = (catch_2d[:, :-1] != catch_2d[:, 1:]) & aktiv[:, :-1] & aktiv[:, 1:]
    hr, hc = np.nonzero(h_diff)
    del h_diff
    hr_i: IndexArray = np.asarray(hr, dtype=idx_dtype)
    hc_i: IndexArray = np.asarray(hc, dtype=idx_dtype)
    u_h: IndexArray = hr_i * bredd + hc_i
    v_h: IndexArray = u_h + 1
    cu_h: IndexArray = np.asarray(catch_2d[hr, hc], dtype=idx_dtype)
    cv_h: IndexArray = np.asarray(catch_2d[hr, hc + 1], dtype=idx_dtype)
    w_h = np.maximum(eff_z[hr, hc], eff_z[hr, hc + 1]).astype(np.float64)
    del hr, hc, hr_i, hc_i

    u_vh: IndexArray = np.concatenate((u_v, u_h))
    v_vh: IndexArray = np.concatenate((v_v, v_h))
    cu_vh: IndexArray = np.concatenate((cu_v, cu_h))
    cv_vh: IndexArray = np.concatenate((cv_v, cv_h))
    w_vh = np.concatenate((w_v, w_h))
    del u_v, v_v, cu_v, cv_v, w_v, u_h, v_h, cu_h, cv_h, w_h

    # Lägg på eventuella 2D/3D-kantbarriärer på just de korsade gränskanterna
    if kantbarriarer and u_vh.size > 0:
        if n_celler < _INT32_MAX:
            b_keys = np.fromiter(
                ((int(bu) << 32) | int(bv) for bu, bv in kantbarriarer),
                dtype=np.int64,
                count=len(kantbarriarer),
            )
            b_vals = np.fromiter(
                kantbarriarer.values(),
                dtype=np.float64,
                count=len(kantbarriarer),
            )
            b_ord = np.argsort(b_keys)
            b_keys = b_keys[b_ord]
            b_vals = b_vals[b_ord]
            e_keys = (u_vh.astype(np.int64) << 32) | v_vh.astype(np.int64)
            pos = np.searchsorted(b_keys, e_keys)
            i_omf = pos < b_keys.size
            traff = np.zeros(e_keys.size, dtype=np.bool_)
            traff[i_omf] = b_keys[pos[i_omf]] == e_keys[i_omf]
            if np.any(traff):
                w_vh[traff] = b_vals[pos[traff]]
            del b_keys, b_vals, b_ord, e_keys, pos, i_omf, traff
        else:
            for idx_k in range(int(u_vh.size)):
                nyckel = (int(u_vh[idx_k]), int(v_vh[idx_k]))
                if nyckel in kantbarriarer:
                    w_vh[idx_k] = kantbarriarer[nyckel]

    # Ytterrandskanter till HAV för randceller som dränerar inåt en sänka (utan 2D rand_mask)
    r_delar: list[NDArray[np.intp]] = []
    c_delar: list[NDArray[np.intp]] = []
    if hojd > 0 and bredd > 0:
        m_topp = aktiv[0, :] & (catch_2d[0, :] >= 0)
        if np.any(m_topp):
            cc_t = np.flatnonzero(m_topp)
            r_delar.append(np.zeros(cc_t.size, dtype=np.intp))
            c_delar.append(cc_t)
        if hojd > 1:
            m_bot = aktiv[hojd - 1, :] & (catch_2d[hojd - 1, :] >= 0)
            if np.any(m_bot):
                cc_b = np.flatnonzero(m_bot)
                r_delar.append(np.full(cc_b.size, hojd - 1, dtype=np.intp))
                c_delar.append(cc_b)
        if hojd > 2:
            m_van = aktiv[1 : hojd - 1, 0] & (catch_2d[1 : hojd - 1, 0] >= 0)
            if np.any(m_van):
                rr_v = np.flatnonzero(m_van) + 1
                r_delar.append(rr_v)
                c_delar.append(np.zeros(rr_v.size, dtype=np.intp))
            if bredd > 1:
                m_hog = aktiv[1 : hojd - 1, bredd - 1] & (catch_2d[1 : hojd - 1, bredd - 1] >= 0)
                if np.any(m_hog):
                    rr_h = np.flatnonzero(m_hog) + 1
                    r_delar.append(rr_h)
                    c_delar.append(np.full(rr_h.size, bredd - 1, dtype=np.intp))

    if r_delar:
        rr_r = np.concatenate(r_delar)
        rc_r = np.concatenate(c_delar)
        rr_ri: IndexArray = np.asarray(rr_r, dtype=idx_dtype)
        rc_ri: IndexArray = np.asarray(rc_r, dtype=idx_dtype)
        u_r: IndexArray = rr_ri * bredd + rc_ri
        v_r: IndexArray = np.full(u_r.size, HAV_NOD_ID, dtype=idx_dtype)
        cu_r: IndexArray = np.asarray(catch_2d[rr_r, rc_r], dtype=idx_dtype)
        cv_r: IndexArray = np.full(u_r.size, HAV_NOD_ID, dtype=idx_dtype)
        w_r = eff_z[rr_r, rc_r].astype(np.float64)
        all_u: IndexArray = np.concatenate((u_vh, u_r))
        all_v: IndexArray = np.concatenate((v_vh, v_r))
        all_cu: IndexArray = np.concatenate((cu_vh, cu_r))
        all_cv: IndexArray = np.concatenate((cv_vh, cv_r))
        all_w = np.concatenate((w_vh, w_r))
        del u_vh, v_vh, cu_vh, cv_vh, w_vh, u_r, v_r, cu_r, cv_r, w_r, rr_r, rc_r, rr_ri, rc_ri
    else:
        all_u, all_v, all_cu, all_cv, all_w = u_vh, v_vh, cu_vh, cv_vh, w_vh

    if all_u.size == 0:
        tom_i: IndexArray = np.empty(0, dtype=idx_dtype)
        tom_i64 = np.empty(0, dtype=np.int64)
        tom_f = np.empty(0, dtype=np.float64)
        return tom_f, tom_i, tom_i, tom_i64, tom_i64, tom_i, tom_i, tom_i, tom_i

    c_lo: IndexArray = np.minimum(all_cu, all_cv)
    c_hi: IndexArray = np.maximum(all_cu, all_cv)
    # Sortera på (c_lo, c_hi, w, u, v): lägsta sadelvikt först, deterministisk (u, v)-ordning
    ordn = np.lexsort((all_v, all_u, all_w, c_hi, c_lo))
    c_lo_s = c_lo[ordn]
    c_hi_s = c_hi[ordn]
    w_s = all_w[ordn]
    u_s = all_u[ordn]
    v_s = all_v[ordn]
    cu_s = all_cu[ordn]
    cv_s = all_cv[ordn]
    del all_u, all_v, all_cu, all_cv, all_w, c_lo, c_hi, ordn

    n_kanter = int(c_lo_s.size)
    ny_grupp = np.empty(n_kanter, dtype=np.bool_)
    ny_grupp[0] = True
    ny_grupp[1:] = (c_lo_s[1:] != c_lo_s[:-1]) | (c_hi_s[1:] != c_hi_s[:-1])
    grupp_start = np.flatnonzero(ny_grupp)
    del ny_grupp

    par_w = w_s[grupp_start]
    par_c_lo = c_lo_s[grupp_start]
    par_c_hi = c_hi_s[grupp_start]
    del c_lo_s, c_hi_s

    grupp_slut = np.empty_like(grupp_start)
    grupp_slut[:-1] = grupp_start[1:]
    grupp_slut[-1] = n_kanter
    grupp_langd = grupp_slut - grupp_start
    del grupp_slut

    kant_w_min = np.repeat(par_w, grupp_langd)
    del grupp_langd
    behall = (w_s - kant_w_min) <= _SADEL_TIE_TOL_M
    del kant_w_min, w_s

    grupp_antal = np.add.reduceat(behall.astype(np.int64), grupp_start)
    del grupp_start

    f_u = u_s[behall]
    f_v = v_s[behall]
    f_cu = cu_s[behall]
    f_cv = cv_s[behall]
    del u_s, v_s, cu_s, cv_s, behall

    n_par = int(par_w.size)
    par_start = np.empty(n_par, dtype=np.int64)
    par_start[0] = 0
    if n_par > 1:
        par_start[1:] = np.cumsum(grupp_antal[:-1], dtype=np.int64)
    par_slut = par_start + grupp_antal
    del grupp_antal

    # Sortera paren i stigande (w_min, c_lo, c_hi) för Kruskal / Barnes (2020)
    par_ordn = np.lexsort((par_c_hi, par_c_lo, par_w))
    return (
        par_w[par_ordn],
        par_c_lo[par_ordn],
        par_c_hi[par_ordn],
        par_start[par_ordn],
        par_slut[par_ordn],
        f_u,
        f_v,
        f_cu,
        f_cv,
    )


def berakna_kurvor_for_celler(
    z_celler: NDArray[np.float64],
    overfall_z: float,
    cell_area_m2: float,
) -> tuple[tuple[VolymPunkt, ...], tuple[YtPunkt, ...], float, int]:
    """Beräknar exakta styckvis linjära volym- och ytkurvor upp till `overfall_z`.

    Returnerar `(volymkurva, ytkurva, botten_rh2000, cell_antal)`.
    """
    under = z_celler[z_celler < overfall_z]
    if under.size == 0:
        botten = float(np.min(z_celler)) if z_celler.size > 0 else float(overfall_z)
        return (
            (VolymPunkt(z=botten, v_m3=0.0), VolymPunkt(z=float(overfall_z), v_m3=0.0)),
            (YtPunkt(z=botten, a_m2=0.0), YtPunkt(z=float(overfall_z), a_m2=0.0)),
            botten,
            0,
        )

    unika_z, antal = np.unique(under, return_counts=True)
    kum_antal = np.cumsum(antal, dtype=np.int64)
    botten = float(unika_z[0])
    tot_celler = int(kum_antal[-1])

    volym_punkter: list[VolymPunkt] = [VolymPunkt(z=botten, v_m3=0.0)]
    yt_punkter: list[YtPunkt] = [YtPunkt(z=botten, a_m2=float(kum_antal[0]) * cell_area_m2)]

    v_ack = 0.0
    n_unika = int(unika_z.size)
    for idx in range(1, n_unika):
        z_fore = float(unika_z[idx - 1])
        z_nu = float(unika_z[idx])
        area_fore = float(kum_antal[idx - 1]) * cell_area_m2
        v_ack += area_fore * (z_nu - z_fore)
        volym_punkter.append(VolymPunkt(z=z_nu, v_m3=v_ack))
        yt_punkter.append(YtPunkt(z=z_nu, a_m2=float(kum_antal[idx]) * cell_area_m2))

    z_sista = float(unika_z[-1])
    max_area = float(kum_antal[-1]) * cell_area_m2
    v_ack += max_area * (float(overfall_z) - z_sista)
    volym_punkter.append(VolymPunkt(z=float(overfall_z), v_m3=v_ack))
    yt_punkter.append(YtPunkt(z=float(overfall_z), a_m2=max_area))

    return tuple(volym_punkter), tuple(yt_punkter), botten, tot_celler


def _bygg_nodkurvor_nedifran_upp(
    ordnade_noder: Sequence[int],
    forald_karta: Mapping[int, int | None],
    barn_karta: Mapping[int, Sequence[int]],
    overfall_z_karta: Mapping[int, float],
    lov_ids_flat: NDArray[np.signedinteger[Any]],
    lov_z_flat: NDArray[np.float32] | NDArray[np.float64],
    cell_area_m2: float,
) -> dict[int, tuple[tuple[VolymPunkt, ...], tuple[YtPunkt, ...], float, int, float]]:
    """Bygger exakta volym- och ytkurvor nedifrån och upp i O(N log H) tid och O(N) minne.

    Varje aktiv cell hör till exakt en unik vätande nod (den lägsta förfadern längs vägen från
    cellens lövsänka upp till roten som uppfyller ``z_cell < overfall_z[nod]``). Föräldernoder
    ärver barnens totala volym ``V_barn`` och antal våta celler ``N_barn`` vid delad sadelnivå
    ``z_sadel``, och bygger sedan sin exakta kurva på intervallet ``[z_sadel, overfall_z]``
    utan att någonsin duplicera eller konkatenera barnens cellhöjder.

    Returnerar ``nid -> (volymkurva, ytkurva, botten_rh2000, cell_antal, avrinningsyta_m2)``.
    """
    m = len(ordnade_noder)
    if m == 0:
        return {}

    max_nid = max(ordnade_noder)
    nid_till_idx = np.full(max_nid + 1, -1, dtype=np.int32)
    ov_arr = np.empty(m, dtype=np.float64)
    parent_idx = np.arange(m, dtype=np.int32)

    for i, nid in enumerate(ordnade_noder):
        nid_till_idx[nid] = i
        ov_arr[i] = float(overfall_z_karta[nid])

    for i, nid in enumerate(ordnade_noder):
        p_nid = forald_karta[nid]
        if p_nid is not None:
            parent_idx[i] = nid_till_idx[p_nid]

    lov_antal = np.zeros(m, dtype=np.int64)
    lov_min_z = np.full(m, np.inf, dtype=np.float64)
    celler_per_idx: dict[int, NDArray[np.float32] | NDArray[np.float64]] = {}

    if lov_ids_flat.size > 0:
        cur = nid_till_idx[lov_ids_flat]
        lov_antal = np.bincount(cur, minlength=m)
        np.minimum.at(lov_min_z, cur, lov_z_flat)

        # Binär lyftning: varje cell klättrar i O(log H) till sin unika vätande nod.
        up_lager: list[NDArray[np.int32]] = [parent_idx]
        while True:
            prev = up_lager[-1]
            nxt = prev[prev]
            if np.array_equal(nxt, prev):
                break
            up_lager.append(nxt)

        wet_idx = cur.copy()
        need = np.flatnonzero(lov_z_flat >= ov_arr[cur])
        del cur
        if need.size > 0:
            u = wet_idx[need]
            z_need = np.asarray(lov_z_flat[need], dtype=np.float64)
            for lager in reversed(up_lager):
                anc = lager[u]
                hopp = ov_arr[anc] <= z_need
                u[hopp] = anc[hopp]
            p_u = parent_idx[u]
            wet_idx[need] = np.where(ov_arr[p_u] > z_need, p_u, -1)
            del u, z_need, p_u
        del need, up_lager

        giltig = wet_idx >= 0
        if np.any(giltig):
            w_val = wet_idx[giltig]
            z_val = lov_z_flat[giltig]
            del wet_idx, giltig
            ordn = np.argsort(w_val, kind="quicksort")
            s_w = w_val[ordn]
            s_z = z_val[ordn]
            del w_val, z_val, ordn
            u_w, s_idx, cnts = np.unique(s_w, return_index=True, return_counts=True)
            del s_w
            for wi_np, st_np, c_np in zip(u_w, s_idx, cnts, strict=True):
                st = int(st_np)
                celler_per_idx[int(wi_np)] = s_z[st : st + int(c_np)]
            del u_w, s_idx, cnts
        else:
            del wet_idx, giltig

    resultat: dict[int, tuple[tuple[VolymPunkt, ...], tuple[YtPunkt, ...], float, int, float]] = {}
    har_celler: dict[int, bool] = {}

    for i, nid in enumerate(ordnade_noder):
        ov_z = float(ov_arr[i])
        barn = barn_karta[nid]
        z_del = celler_per_idx.pop(i, None)
        vol_k: tuple[VolymPunkt, ...]
        yt_k: tuple[YtPunkt, ...]

        if not barn:
            n_tot_lov = int(lov_antal[i])
            avrinning_m2 = float(n_tot_lov) * cell_area_m2
            har_celler[nid] = n_tot_lov > 0
            if z_del is None or z_del.size == 0:
                botten = float(lov_min_z[i]) if n_tot_lov > 0 else ov_z
                vol_k = (VolymPunkt(z=botten, v_m3=0.0), VolymPunkt(z=ov_z, v_m3=0.0))
                yt_k = (YtPunkt(z=botten, a_m2=0.0), YtPunkt(z=ov_z, a_m2=0.0))
                resultat[nid] = (vol_k, yt_k, botten, 0, avrinning_m2)
            else:
                z_arr = np.asarray(z_del, dtype=np.float64)
                vol_k, yt_k, botten, cell_antal = berakna_kurvor_for_celler(
                    z_arr, ov_z, cell_area_m2
                )
                resultat[nid] = (vol_k, yt_k, botten, cell_antal, avrinning_m2)
            continue

        avrinning_m2 = 0.0
        z_sadel = float(overfall_z_karta[barn[0]])
        v_barn = sum(resultat[b][0][-1].v_m3 for b in barn)
        n_barn = sum(resultat[b][3] for b in barn)
        barn_med_celler = [b for b in barn if har_celler[b]]
        har_celler[nid] = bool(barn_med_celler)
        botten = min(resultat[b][2] for b in barn_med_celler) if barn_med_celler else ov_z
        ny_z = (
            np.asarray(z_del, dtype=np.float64) if z_del is not None and z_del.size > 0 else None
        )

        if n_barn == 0 and ny_z is None:
            vol_k = (VolymPunkt(z=botten, v_m3=0.0), VolymPunkt(z=ov_z, v_m3=0.0))
            yt_k = (YtPunkt(z=botten, a_m2=0.0), YtPunkt(z=ov_z, a_m2=0.0))
            resultat[nid] = (vol_k, yt_k, botten, 0, avrinning_m2)
        elif n_barn == 0 and ny_z is not None:
            vol_k, yt_k, botten_ny, cell_antal = berakna_kurvor_for_celler(
                ny_z, ov_z, cell_area_m2
            )
            resultat[nid] = (vol_k, yt_k, botten_ny, cell_antal, avrinning_m2)
        elif ny_z is None:
            a_barn = float(n_barn) * cell_area_m2
            vol_pts: list[VolymPunkt] = [
                VolymPunkt(z=botten, v_m3=0.0),
                VolymPunkt(z=z_sadel, v_m3=v_barn),
            ]
            yt_pts: list[YtPunkt] = [
                YtPunkt(z=botten, a_m2=0.0),
                YtPunkt(z=z_sadel, a_m2=a_barn),
            ]
            if ov_z > z_sadel:
                v_top = v_barn + a_barn * (ov_z - z_sadel)
                vol_pts.append(VolymPunkt(z=ov_z, v_m3=v_top))
                yt_pts.append(YtPunkt(z=ov_z, a_m2=a_barn))
            resultat[nid] = (tuple(vol_pts), tuple(yt_pts), botten, n_barn, avrinning_m2)
        else:
            unika_z, antal = np.unique(ny_z, return_counts=True)
            kum_antal = n_barn + np.cumsum(antal, dtype=np.int64)
            tot_celler = int(kum_antal[-1])
            a_barn = float(n_barn) * cell_area_m2

            vol_pts = [VolymPunkt(z=botten, v_m3=0.0)]
            yt_pts = [YtPunkt(z=botten, a_m2=0.0)]
            z0 = float(unika_z[0])
            if z0 == z_sadel:
                v_ack = v_barn
                vol_pts.append(VolymPunkt(z=z_sadel, v_m3=v_ack))
                yt_pts.append(YtPunkt(z=z_sadel, a_m2=float(kum_antal[0]) * cell_area_m2))
            else:
                vol_pts.append(VolymPunkt(z=z_sadel, v_m3=v_barn))
                yt_pts.append(YtPunkt(z=z_sadel, a_m2=a_barn))
                v_ack = v_barn + a_barn * (z0 - z_sadel)
                vol_pts.append(VolymPunkt(z=z0, v_m3=v_ack))
                yt_pts.append(YtPunkt(z=z0, a_m2=float(kum_antal[0]) * cell_area_m2))

            n_unika = int(unika_z.size)
            for idx in range(1, n_unika):
                z_fore = float(unika_z[idx - 1])
                z_nu = float(unika_z[idx])
                area_fore = float(kum_antal[idx - 1]) * cell_area_m2
                v_ack += area_fore * (z_nu - z_fore)
                vol_pts.append(VolymPunkt(z=z_nu, v_m3=v_ack))
                yt_pts.append(YtPunkt(z=z_nu, a_m2=float(kum_antal[idx]) * cell_area_m2))

            z_sista = float(unika_z[-1])
            max_area = float(kum_antal[-1]) * cell_area_m2
            v_ack += max_area * (ov_z - z_sista)
            vol_pts.append(VolymPunkt(z=ov_z, v_m3=v_ack))
            yt_pts.append(YtPunkt(z=ov_z, a_m2=max_area))
            resultat[nid] = (tuple(vol_pts), tuple(yt_pts), botten, tot_celler, avrinning_m2)

    return resultat


def _instangda_etiketter(aktiv: NDArray[np.bool_]) -> tuple[NDArray[np.int32], list[int]]:
    """Fyrgrannsetiketter för aktiva områden och de etiketter som inte når rasterkanten."""
    from scipy import ndimage

    etiketter, antal = ndimage.label(aktiv)  # 4-grannskap är standard
    if antal == 0:
        return np.asarray(etiketter, dtype=np.int32), []
    kant = {
        int(x)
        for x in np.unique(
            np.concatenate([etiketter[0], etiketter[-1], etiketter[:, 0], etiketter[:, -1]])
        )
    }
    return np.asarray(etiketter, dtype=np.int32), [
        e for e in range(1, int(antal) + 1) if e not in kant
    ]


def hitta_instangda_omraden(aktiv: NDArray[np.bool_]) -> list[tuple[int, int, int]]:
    """Fyrgrannsammanhängande aktiva områden som inte når rasterkanten.

    Returnerar (antal celler, rad, kolumn) för varje sådant område, största först. Sedan K2/v2
    blir ett område som omges av byggnader en slutsänka (se `bygg_sankgraf`).
    """
    etiketter, instangda = _instangda_etiketter(aktiv)
    if not instangda:
        return []
    antal_per = np.bincount(etiketter.ravel())
    ut: list[tuple[int, int, int]] = []
    for e in instangda:
        rr, kk = np.nonzero(etiketter == e)
        ut.append((int(antal_per[e]), int(rr[0]), int(kk[0])))
    ut.sort(reverse=True)
    return ut


@dataclass(frozen=True)
class SlutetOmrade:
    """Ett instängt område och dess hinder (lägsta hindercell som gränsar till området)."""

    etikett: int
    antal_celler: int
    rad: int
    kol: int
    hinder_rh2000: float
    hinder_rad: int
    hinder_kol: int
    lov: tuple[int, ...] = ()


def slutna_omraden(
    eff_z: NDArray[np.float32],
    aktiv: NDArray[np.bool_],
) -> tuple[NDArray[np.int32], list[SlutetOmrade]]:
    """Instängda områden med hinder, i etikettordning.

    **Hindret** är den lägsta cellen i ringen av ogenomträngliga celler som gränsar till
    området (fyrgrannar), med höjden ur `mark.tif` (byggnadens takhöjd). Vägen vidare över
    taket prövas inte: på platta tak (K1 v2) är det samma nivå. Verkliga trösklar, portar och
    brunnar finns inte i markmodellen. Har ringen en cell utan höjd (NaN) är hindret okänt och
    `InstangtOmradeFel` kastas (största sådana område först).
    """
    from scipy import ndimage

    etiketter, instangda = _instangda_etiketter(aktiv)
    if not instangda:
        return etiketter, []
    hojd, bredd = aktiv.shape
    objekt = ndimage.find_objects(etiketter)
    kors = ndimage.generate_binary_structure(2, 1)
    ut: list[SlutetOmrade] = []
    okanda: list[tuple[int, int, int]] = []
    for e in instangda:
        sl = objekt[e - 1]
        assert sl is not None
        r0 = max(0, sl[0].start - 1)
        r1 = min(hojd, sl[0].stop + 1)
        c0 = max(0, sl[1].start - 1)
        c1 = min(bredd, sl[1].stop + 1)
        omr = etiketter[r0:r1, c0:c1] == e
        ring = ndimage.binary_dilation(omr, structure=kors) & ~omr
        rr, kk = np.nonzero(omr)
        antal = int(rr.size)
        ring_z = np.asarray(eff_z[r0:r1, c0:c1], dtype=np.float64)[ring]
        if not np.all(np.isfinite(ring_z)):
            okanda.append((antal, int(rr[0]) + r0, int(kk[0]) + c0))
            continue
        hr, hk = np.nonzero(ring)
        i_min = int(np.argmin(ring_z))  # första minimum i radordning: deterministiskt
        ut.append(
            SlutetOmrade(
                etikett=e,
                antal_celler=antal,
                rad=int(rr[0]) + r0,
                kol=int(kk[0]) + c0,
                hinder_rh2000=float(ring_z[i_min]),
                hinder_rad=int(hr[i_min]) + r0,
                hinder_kol=int(hk[i_min]) + c0,
            )
        )
    if okanda:
        okanda.sort(reverse=True)
        raise InstangtOmradeFel(*okanda[0])
    return etiketter, ut


def bygg_sankgraf(
    mark_z: NDArray[np.float32],
    osakerhet_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    *,
    brytlinjer: Sequence[BrytlinjePost] = (),
    oppningar: Sequence[OppningPost] = (),
    referensdjup_m: float = STANDARD_REFERENSDJUP_M,
    anvand_rust: bool | None = None,
) -> SankGraf:
    """Bygger en komplett, ofiltrerad K2-sänkgraf med Fill–Spill–Merge-hierarki."""
    if mark_z.ndim != 2 or osakerhet_z.shape != mark_z.shape or markslag.shape != mark_z.shape:
        raise SankGrafFel(
            f"Oförenliga rasterformer: mark={mark_z.shape}, "
            f"osakerhet={osakerhet_z.shape}, markslag={markslag.shape}."
        )
    hojd, bredd = mark_z.shape
    if hojd != geo.hojd or bredd != geo.bredd:
        raise SankGrafFel(
            f"GeoTransformInfo ({geo.hojd}x{geo.bredd}) matchar inte raster ({hojd}x{bredd})."
        )

    eff_z, eff_slag = _applicera_oppningar(mark_z, markslag, geo, oppningar)
    aktiv = np.asarray((eff_slag != MARKSLAG_BYGGNAD) & np.isfinite(eff_z), dtype=np.bool_)
    eff_z_flat = eff_z.ravel()
    osak_flat = osakerhet_z.ravel()
    aktiv_flat = aktiv.ravel()

    # Områden som helt omges av byggnader blir slutsänkor (K2/v2); okänt hinder ger fel.
    etiketter_tmp, slutna = slutna_omraden(eff_z, aktiv)
    etiketter: NDArray[np.int32] | None = etiketter_tmp if slutna else None
    del etiketter_tmp

    if brytlinjer:
        z64_barriar = np.asarray(eff_z, dtype=np.float64).ravel()
        kantbarriarer = _berakna_kantbarriarer(z64_barriar, aktiv_flat, geo, brytlinjer)
        del z64_barriar
    else:
        kantbarriarer = {}

    rust_program = valj_sankgraf_karna(hojd * bredd, anvand_rust=anvand_rust)
    granspar_rust: GransparArrayer | None = None
    if rust_program is not None:
        flow_to: IndexArray
        cell_till_nod_int: IndexArray
        flow_to, cell_till_nod_int, antal_lov, granspar_rust = kor_sankgraf_karna_rust(
            eff_z,
            aktiv,
            kantbarriarer,
            binar=rust_program,
        )
    else:
        flow_to, cell_till_nod_int, antal_lov = _berakna_avrinningsomraden(
            eff_z, aktiv, kantbarriarer
        )

    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    cell_area_m2 = dx * dy

    flow_to_i64 = np.asarray(flow_to, dtype=np.int64)
    del flow_to
    flodesvagar = bygg_flodesvagar(
        eff_z,
        eff_slag,
        flow_to_i64,
        np.asarray(cell_till_nod_int, dtype=np.int64),
        geo,
        referensdjup_m=referensdjup_m,
    )
    del flow_to_i64

    if antal_lov == 0:
        cell_till_nod_out = np.asarray(cell_till_nod_int, dtype=np.int64)
        del cell_till_nod_int, aktiv, kantbarriarer, etiketter, granspar_rust
        return SankGraf(
            noder={},
            cell_till_nod=cell_till_nod_out,
            flodesvagar=flodesvagar,
            geo=geo,
            filtrerad=False,
            persistens_faktor=None,
            mark_z=eff_z,
            markslag=eff_slag,
            ut_lov_id={},
            overfall_celler={},
        )

    # Vektoriserad gränsanalys utan Python-`par_kanter`-dict (eller färdig från Rust)
    if granspar_rust is not None:
        par_w, par_c_lo, par_c_hi, par_start, par_slut, f_u, f_v, f_cu, f_cv = granspar_rust
        del granspar_rust
    else:
        par_w, par_c_lo, par_c_hi, par_start, par_slut, f_u, f_v, f_cu, f_cv = _bygg_granspar(
            cell_till_nod_int,
            aktiv,
            eff_z,
            kantbarriarer,
        )
    del aktiv, kantbarriarer

    # Union-Find över {HAV_NOD_ID (-1), 0, ..., antal_lov - 1} (skiftat med +1 -> 0..antal_lov)
    uf_parent = list(range(antal_lov + 1))
    ar_hav = [False] * (antal_lov + 1)
    ar_hav[0] = True
    topp_nod: list[int] = [HAV_NOD_ID] + list(range(antal_lov))

    def _uf_hitta(x: int) -> int:
        while uf_parent[x] != x:
            uf_parent[x] = uf_parent[uf_parent[x]]
            x = uf_parent[x]
        return x

    def _sadel_cell(u: int, v: int) -> int:
        if v < 0:
            return u
        zu = float(eff_z_flat[u])
        zv = float(eff_z_flat[v])
        if zu > zv:
            return u
        if zv > zu:
            return v
        return min(u, v)

    # Temporär noddata under trädbygge
    forald_karta: dict[int, int | None] = {i: None for i in range(antal_lov)}
    barn_karta: dict[int, tuple[int, ...]] = {i: () for i in range(antal_lov)}
    overfall_z_karta: dict[int, float] = {}
    overfall_xy_karta: dict[int, tuple[float, float]] = {}
    osakerhet_karta: dict[int, float] = {}
    ut_till_karta: dict[int, int | None] = {i: None for i in range(antal_lov)}
    ut_lov_karta: dict[int, int | None] = {i: None for i in range(antal_lov)}
    overfall_celler_karta: dict[int, tuple[tuple[int, int], ...]] = {}

    nasta_nod_id = antal_lov

    for p_idx in range(int(par_w.size)):
        c_min = int(par_c_lo[p_idx])
        c_max = int(par_c_hi[p_idx])
        ra = _uf_hitta(c_min + 1)
        rb = _uf_hitta(c_max + 1)
        if ra == rb:
            continue

        w_min = float(par_w[p_idx])
        s0 = int(par_start[p_idx])
        s1 = int(par_slut[p_idx])
        if s1 - s0 == 1:
            u_rep = int(f_u[s0])
            v_rep = int(f_v[s0])
            cu_rep = int(f_cu[s0])
            cv_rep = int(f_cv[s0])
            sc_idx = _sadel_cell(u_rep, v_rep)
            sr, sc = divmod(sc_idx, bredd)
            sadel_rc_tup: tuple[tuple[int, int], ...] = ((sr, sc),)
        else:
            kanter = [
                (int(f_u[i]), int(f_v[i]), int(f_cu[i]), int(f_cv[i]))
                for i in range(s0, s1)
            ]
            kanter.sort(key=lambda e: (e[0], e[1]))
            u_rep, v_rep, cu_rep, cv_rep = kanter[len(kanter) // 2]
            sc_idx = _sadel_cell(u_rep, v_rep)
            sr, sc = divmod(sc_idx, bredd)
            sadel_rc_lista: list[tuple[int, int]] = []
            sedda_rc: set[tuple[int, int]] = set()
            for eu, ev, _, _ in kanter:
                rc = divmod(_sadel_cell(eu, ev), bredd)
                if rc not in sedda_rc:
                    sedda_rc.add(rc)
                    sadel_rc_lista.append(rc)
            sadel_rc_tup = tuple(sadel_rc_lista)

        sadel_x = geo.x_min + (sc + 0.5) * dx
        sadel_y = geo.y_max - (sr + 0.5) * dy
        sadel_osak_val = float(osak_flat[sc_idx])
        sadel_osak = sadel_osak_val if math.isfinite(sadel_osak_val) else 0.0

        if ar_hav[ra] or ar_hav[rb]:
            if ar_hav[ra]:
                r_int = rb
                andra_lov = cu_rep if _uf_hitta(cu_rep + 1) == ra else cv_rep
            else:
                r_int = ra
                andra_lov = cu_rep if _uf_hitta(cu_rep + 1) == rb else cv_rep

            nod_id = topp_nod[r_int]
            ut_target = None if andra_lov == HAV_NOD_ID else int(andra_lov)

            overfall_z_karta[nod_id] = w_min
            overfall_xy_karta[nod_id] = (sadel_x, sadel_y)
            osakerhet_karta[nod_id] = sadel_osak
            forald_karta[nod_id] = None
            ut_till_karta[nod_id] = ut_target
            ut_lov_karta[nod_id] = ut_target
            overfall_celler_karta[nod_id] = sadel_rc_tup

            uf_parent[r_int] = 0
            ar_hav[r_int] = True
        else:
            a_id = topp_nod[ra]
            b_id = topp_nod[rb]
            lov_a = cu_rep if _uf_hitta(cu_rep + 1) == ra else cv_rep
            lov_b = cv_rep if _uf_hitta(cu_rep + 1) == ra else cu_rep
            if a_id > b_id:
                a_id, b_id = b_id, a_id
                lov_a, lov_b = lov_b, lov_a
                ra, rb = rb, ra

            p_id = nasta_nod_id
            nasta_nod_id += 1
            forald_karta[p_id] = None
            barn_karta[p_id] = (a_id, b_id)
            ut_till_karta[p_id] = None
            ut_lov_karta[p_id] = None

            forald_karta[a_id] = p_id
            forald_karta[b_id] = p_id
            overfall_z_karta[a_id] = w_min
            overfall_z_karta[b_id] = w_min
            overfall_xy_karta[a_id] = (sadel_x, sadel_y)
            overfall_xy_karta[b_id] = (sadel_x, sadel_y)
            osakerhet_karta[a_id] = sadel_osak
            osakerhet_karta[b_id] = sadel_osak
            ut_till_karta[a_id] = b_id
            ut_till_karta[b_id] = a_id
            ut_lov_karta[a_id] = int(lov_b)
            ut_lov_karta[b_id] = int(lov_a)
            overfall_celler_karta[a_id] = sadel_rc_tup
            overfall_celler_karta[b_id] = sadel_rc_tup

            uf_parent[rb] = ra
            topp_nod[ra] = p_id

    del par_w, par_c_lo, par_c_hi, par_start, par_slut, f_u, f_v, f_cu, f_cv

    # Rotnoder som inte nått randen ligger i ett slutet område: de blir slutsänkor (K2/v2).
    slutsanka_hinder: dict[int, float] = {}
    if slutna and etiketter is not None:
        etikett_till_omrade = {o.etikett: o for o in slutna}
        ct_flat_tmp = cell_till_nod_int.ravel()
        et_flat = etiketter.ravel()
        lov_etikett: dict[int, int] = {}
        i_slutna = np.flatnonzero(np.isin(et_flat, np.asarray(list(etikett_till_omrade))))
        for lid_np, e_np in zip(ct_flat_tmp[i_slutna], et_flat[i_slutna], strict=True):
            if int(lid_np) >= 0:
                lov_etikett.setdefault(int(lid_np), int(e_np))
        sedda_etiketter: set[int] = set()
        for rot in sorted({_uf_hitta(x) for x in range(1, antal_lov + 1)}):
            if ar_hav[rot]:
                continue
            nod_id = topp_nod[rot]
            lov = nod_id
            while barn_karta[lov]:
                lov = barn_karta[lov][0]
            etikett = lov_etikett.get(lov)
            if etikett is None or etikett in sedda_etiketter:
                raise SankGrafFel(f"Nod {nod_id} saknar utloppssadel mot randen.")
            sedda_etiketter.add(etikett)
            omr = etikett_till_omrade[etikett]
            hinder_z = omr.hinder_rh2000
            if barn_karta[nod_id]:
                # Hindret kan ligga under en inre sadel (t.ex. en låg byggnad vid en sluttande
                # gård): roten fylls då åtminstone till sadeln så att trädet förblir monotont.
                hinder_z = max(hinder_z, overfall_z_karta[barn_karta[nod_id][0]])
            h_idx = omr.hinder_rad * bredd + omr.hinder_kol
            h_osak = float(osak_flat[h_idx])
            overfall_z_karta[nod_id] = hinder_z
            overfall_xy_karta[nod_id] = (
                geo.x_min + (omr.hinder_kol + 0.5) * dx,
                geo.y_max - (omr.hinder_rad + 0.5) * dy,
            )
            osakerhet_karta[nod_id] = h_osak if math.isfinite(h_osak) else 0.0
            forald_karta[nod_id] = None
            ut_till_karta[nod_id] = None
            ut_lov_karta[nod_id] = None
            overfall_celler_karta[nod_id] = ((omr.hinder_rad, omr.hinder_kol),)
            slutsanka_hinder[nod_id] = hinder_z
    del etiketter

    for nid in range(nasta_nod_id):
        if nid not in overfall_z_karta:
            # Bör inte ske på ändlig domän med randkanter, men skydda mot tyst fel
            raise SankGrafFel(f"Nod {nid} saknar utloppssadel mot randen.")

    # Bygg SankNod-kurvor nedifrån och upp i O(N log H) tid och O(N) minne
    catch_flat = cell_till_nod_int.ravel()
    lov_mask = catch_flat >= 0
    lov_ids_flat = catch_flat[lov_mask]
    lov_z_flat = eff_z_flat[lov_mask]
    del lov_mask

    kurv_data = _bygg_nodkurvor_nedifran_upp(
        range(nasta_nod_id),
        forald_karta,
        barn_karta,
        overfall_z_karta,
        lov_ids_flat,
        lov_z_flat,
        cell_area_m2,
    )
    del lov_ids_flat, lov_z_flat

    noder: dict[int, SankNod] = {}
    for nid in range(nasta_nod_id):
        vol_kurva, yt_kurva, botten_z, cell_antal, avrinning_m2 = kurv_data.pop(nid)
        overfall_z = overfall_z_karta[nid]
        ox, oy = overfall_xy_karta[nid]
        noder[nid] = SankNod(
            nod_id=nid,
            forald_id=forald_karta[nid],
            barn_id=barn_karta[nid],
            overfall_rh2000=overfall_z,
            overfall_x=ox,
            overfall_y=oy,
            ut_till_id=ut_till_karta[nid],
            volymkurva=vol_kurva,
            ytkurva=yt_kurva,
            avrinningsyta_m2=avrinning_m2,
            persistens_m=max(0.0, overfall_z - botten_z),
            osakerhet_m=osakerhet_karta[nid],
            botten_rh2000=botten_z,
            cell_antal=cell_antal,
            typ=TYP_SLUTSANKA if nid in slutsanka_hinder else TYP_SANKA,
            hinder_rh2000=slutsanka_hinder.get(nid),
        )

    del kurv_data
    cell_till_nod_out = np.asarray(cell_till_nod_int, dtype=np.int64)
    del cell_till_nod_int

    return SankGraf(
        noder=noder,
        cell_till_nod=cell_till_nod_out,
        flodesvagar=flodesvagar,
        geo=geo,
        filtrerad=False,
        persistens_faktor=None,
        mark_z=eff_z,
        markslag=eff_slag,
        ut_lov_id=ut_lov_karta,
        overfall_celler=overfall_celler_karta,
    )


def sammanfoga_rutor(
    rutor: Sequence[
        tuple[GeoTransformInfo, NDArray[np.float32], NDArray[np.float32], NDArray[np.uint8]]
    ],
    *,
    overlapp_m: float = 25.0,
) -> tuple[GeoTransformInfo, NDArray[np.float32], NDArray[np.float32], NDArray[np.uint8]]:
    """Klipper bort rutöverlapp och sammanfogar angränsande K1-rutor till en global matris."""
    if not rutor:
        raise SankGrafFel("Kan inte sammanfoga en tom lista av rutor.")
    if not math.isfinite(overlapp_m) or overlapp_m < 0.0:
        raise SankGrafFel(f"Ogiltigt rutöverlapp: {overlapp_m} m.")

    ref_geo = rutor[0][0]
    dx = float(ref_geo.pixelstorlek_x)
    dy = float(ref_geo.pixelstorlek_y)
    epsg = int(ref_geo.epsg)

    ov_x = round(overlapp_m / dx)
    ov_y = round(overlapp_m / dy)
    if not math.isclose(ov_x * dx, overlapp_m, rel_tol=0.0, abs_tol=1e-6) or not math.isclose(
        ov_y * dy, overlapp_m, rel_tol=0.0, abs_tol=1e-6
    ):
        raise SankGrafFel(f"Överlappet {overlapp_m} m är inte jämnt delbart med pixelstorleken.")

    klippta: list[
        tuple[GeoTransformInfo, NDArray[np.float32], NDArray[np.float32], NDArray[np.uint8]]
    ] = []

    for idx, (geo, mz, oz, ms) in enumerate(rutor):
        if (
            not math.isclose(geo.pixelstorlek_x, dx, rel_tol=0.0, abs_tol=1e-9)
            or not math.isclose(geo.pixelstorlek_y, dy, rel_tol=0.0, abs_tol=1e-9)
            or geo.epsg != epsg
        ):
            raise SankGrafFel(f"Ruta {idx} har avvikande upplösning eller EPSG.")
        if mz.shape != (geo.hojd, geo.bredd) or oz.shape != mz.shape or ms.shape != mz.shape:
            raise SankGrafFel(f"Ruta {idx} har rasterform som inte matchar GeoTransformInfo.")

        inre_h = geo.hojd - 2 * ov_y
        inre_b = geo.bredd - 2 * ov_x
        if inre_h <= 0 or inre_b <= 0:
            raise SankGrafFel(
                f"Ruta {idx} ({geo.hojd}x{geo.bredd}) är för liten för överlapp {overlapp_m} m."
            )

        r0, r1 = ov_y, geo.hojd - ov_y
        c0, c1 = ov_x, geo.bredd - ov_x
        inre_geo = GeoTransformInfo(
            bredd=inre_b,
            hojd=inre_h,
            x_min=geo.x_min + ov_x * dx,
            y_max=geo.y_max - ov_y * dy,
            pixelstorlek_x=dx,
            pixelstorlek_y=dy,
            epsg=epsg,
            dtype=geo.dtype,
        )
        klippta.append(
            (
                inre_geo,
                np.asarray(mz[r0:r1, c0:c1], dtype=np.float32),
                np.asarray(oz[r0:r1, c0:c1], dtype=np.float32),
                np.asarray(ms[r0:r1, c0:c1], dtype=np.uint8),
            )
        )

    glob_x_min = min(g.x_min for g, _, _, _ in klippta)
    glob_x_max = max(g.x_max for g, _, _, _ in klippta)
    glob_y_min = min(g.y_min for g, _, _, _ in klippta)
    glob_y_max = max(g.y_max for g, _, _, _ in klippta)

    glob_b_f = (glob_x_max - glob_x_min) / dx
    glob_h_f = (glob_y_max - glob_y_min) / dy
    glob_b = round(glob_b_f)
    glob_h = round(glob_h_f)
    if not math.isclose(glob_b_f, glob_b, rel_tol=0.0, abs_tol=1e-6) or not math.isclose(
        glob_h_f, glob_h, rel_tol=0.0, abs_tol=1e-6
    ):
        raise SankGrafFel("Rutorna ligger inte på ett gemensamt reguljärt rutnät.")

    glob_mark = np.full((glob_h, glob_b), np.nan, dtype=np.float32)
    glob_osak = np.full((glob_h, glob_b), np.nan, dtype=np.float32)
    glob_slag = np.zeros((glob_h, glob_b), dtype=np.uint8)

    for g, mz, oz, ms in klippta:
        c0_f = (g.x_min - glob_x_min) / dx
        r0_f = (glob_y_max - g.y_max) / dy
        c0 = round(c0_f)
        r0 = round(r0_f)
        if not math.isclose(c0_f, c0, rel_tol=0.0, abs_tol=1e-6) or not math.isclose(
            r0_f, r0, rel_tol=0.0, abs_tol=1e-6
        ):
            raise SankGrafFel("Rutans hörn är inte i fas med det globala rutnätet.")
        glob_mark[r0 : r0 + g.hojd, c0 : c0 + g.bredd] = mz
        glob_osak[r0 : r0 + g.hojd, c0 : c0 + g.bredd] = oz
        glob_slag[r0 : r0 + g.hojd, c0 : c0 + g.bredd] = ms

    glob_geo = GeoTransformInfo(
        bredd=glob_b,
        hojd=glob_h,
        x_min=glob_x_min,
        y_max=glob_y_max,
        pixelstorlek_x=dx,
        pixelstorlek_y=dy,
        epsg=epsg,
        dtype="float32",
    )
    return glob_geo, glob_mark, glob_osak, glob_slag
