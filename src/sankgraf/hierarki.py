"""Priority-Flood / Fill–Spill–Merge sänkhierarki och rutgränssammansättning (T02).

Algoritmen använder strikt **4-grannskap (N, S, Ö, V)** så att 1-cellsbreda barriärer
och brytlinjer (kantstenar, murar) aldrig läcker diagonalt och så att volymer och
sadelnivåer på ortogonala rutnät (A1–A9) blir maskinexakta.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from sankgraf.flodesvagar import STANDARD_REFERENSDJUP_M, bygg_flodesvagar
from sankgraf.typer import (
    BYGGNAD_NOD_ID,
    HAV_NOD_ID,
    MARKSLAG_BYGGNAD,
    MARKSLAG_HARDGJORT,
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
    """Mappar brytlinjer till höjda tröskelnivåer på korsade 4-grannekanter.

    En brytlinje som skär dualkanten mellan två aktiva grannceller `(u, v)` ger
    kanten barriärhöjden `max(z_u, z_v) + hojd_m`, utan att förvränga cellernas
    lagringsvolym eller läcka diagonalt.
    """
    barriarer: dict[tuple[int, int], float] = {}
    if not brytlinjer:
        return barriarer

    hojd = geo.hojd
    bredd = geo.bredd
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)

    def _lagg_till_kant(u: int, v: int, hojd_m: float) -> None:
        if not (aktiv_flat[u] and aktiv_flat[v]):
            return
        nyckel = (u, v) if u < v else (v, u)
        kant_z = max(float(z_flat[u]), float(z_flat[v])) + hojd_m
        tidigare = barriarer.get(nyckel)
        if tidigare is None or kant_z > tidigare:
            barriarer[nyckel] = kant_z

    for post in brytlinjer:
        hojd_m = float(post.hojd_m)
        if hojd_m <= 0.0 or len(post.koordinater) < 2:
            continue
        for idx in range(len(post.koordinater) - 1):
            e0, n0 = post.koordinater[idx]
            e1, n1 = post.koordinater[idx + 1]
            x0 = (e0 - geo.x_min) / dx
            y0 = (geo.y_max - n0) / dy
            x1 = (e1 - geo.x_min) / dx
            y1 = (geo.y_max - n1) / dy

            # Vertikala dualkanter: (r, c) <-> (r + 1, c) vid x = c + 0.5, y in [r + 0.5, r + 1.5)
            if x1 != x0:
                c_min = max(0, math.floor(min(x0, x1) - 0.5))
                c_max = min(bredd - 1, math.ceil(max(x0, x1) - 0.5))
                for c in range(c_min, c_max + 1):
                    t = ((c + 0.5) - x0) / (x1 - x0)
                    if 0.0 <= t <= 1.0:
                        y_hit = y0 + t * (y1 - y0)
                        r = math.floor(y_hit - 0.5)
                        if 0 <= r < hojd - 1:
                            _lagg_till_kant(r * bredd + c, (r + 1) * bredd + c, hojd_m)

            # Horisontella dualkanter: (r, c) <-> (r, c + 1), y = r + 0.5, x in [c+0.5, c+1.5)
            if y1 != y0:
                r_min = max(0, math.floor(min(y0, y1) - 0.5))
                r_max = min(hojd - 1, math.ceil(max(y0, y1) - 0.5))
                for r in range(r_min, r_max + 1):
                    t = ((r + 0.5) - y0) / (y1 - y0)
                    if 0.0 <= t <= 1.0:
                        x_hit = x0 + t * (x1 - x0)
                        c = math.floor(x_hit - 0.5)
                        if 0 <= c < bredd - 1:
                            _lagg_till_kant(r * bredd + c, r * bredd + (c + 1), hojd_m)

    return barriarer


def _berakna_avrinningsomraden(
    mark_z: NDArray[np.float32],
    aktiv: NDArray[np.bool_],
    kantbarriarer: dict[tuple[int, int], float],
) -> tuple[NDArray[np.int64], NDArray[np.int64], int]:
    """Beräknar 4-granne-brantaste lutning, lövsänkor och `cell_till_nod`.

    Returnerar `(flow_to, cell_till_nod, antal_lov)`.
    """
    hojd, bredd = mark_z.shape
    n_celler = hojd * bredd
    z64 = np.asarray(mark_z, dtype=np.float64)
    z_flat = z64.ravel()
    aktiv_flat = aktiv.ravel()

    # Vektoriserad brantaste nedförsriktning i 4-grannskap (utan barriärer först)
    basta_z = z64.copy()
    flow_2d = np.full((hojd, bredd), BYGGNAD_NOD_ID, dtype=np.int64)
    idx_2d = np.arange(n_celler, dtype=np.int64).reshape((hojd, bredd))

    # Kontrollera N, S, V, Ö i deterministisk ordning
    skift = [
        ((-1, 0), slice(1, hojd), slice(0, bredd), slice(0, hojd - 1), slice(0, bredd)),
        ((1, 0), slice(0, hojd - 1), slice(0, bredd), slice(1, hojd), slice(0, bredd)),
        ((0, -1), slice(0, hojd), slice(1, bredd), slice(0, hojd), slice(0, bredd - 1)),
        ((0, 1), slice(0, hojd), slice(0, bredd - 1), slice(0, hojd), slice(1, bredd)),
    ]
    for _, sr_u, sc_u, sr_v, sc_v in skift:
        giltig = aktiv[sr_u, sc_u] & aktiv[sr_v, sc_v]
        z_v = z64[sr_v, sc_v]
        bättre = giltig & (z_v < basta_z[sr_u, sc_u])
        basta_z[sr_u, sc_u] = np.where(bättre, z_v, basta_z[sr_u, sc_u])
        flow_2d[sr_u, sc_u] = np.where(bättre, idx_2d[sr_v, sc_v], flow_2d[sr_u, sc_u])

    flow_to = flow_2d.ravel()
    flow_to[~aktiv_flat] = BYGGNAD_NOD_ID

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
    cell_till_nod = np.full(n_celler, BYGGNAD_NOD_ID, dtype=np.int64)
    besokt_platt = np.zeros(n_celler, dtype=bool)
    antal_lov = 0

    def _ar_rand(u_idx: int) -> bool:
        r, c = divmod(u_idx, bredd)
        return r == 0 or r == hojd - 1 or c == 0 or c == bredd - 1

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

    # Tilldela alla kvarvarande aktiva celler sin lövsänka (eller HAV) via vägkompression
    for u in range(n_celler):
        if not aktiv_flat[u] or cell_till_nod[u] != BYGGNAD_NOD_ID:
            continue
        kedja: list[int] = []
        curr = u
        while curr >= 0 and cell_till_nod[curr] == BYGGNAD_NOD_ID:
            kedja.append(curr)
            nxt = int(flow_to[curr])
            if nxt == HAV_NOD_ID:
                cell_till_nod[curr] = HAV_NOD_ID
                kedja.pop()
                break
            curr = nxt
        mal_nod = int(cell_till_nod[curr]) if curr >= 0 else HAV_NOD_ID
        for cell in kedja:
            cell_till_nod[cell] = mal_nod

    return (
        np.asarray(flow_to.reshape((hojd, bredd)), dtype=np.int64),
        np.asarray(cell_till_nod.reshape((hojd, bredd)), dtype=np.int64),
        antal_lov,
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


def bygg_sankgraf(
    mark_z: NDArray[np.float32],
    osakerhet_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    *,
    brytlinjer: Sequence[BrytlinjePost] = (),
    oppningar: Sequence[OppningPost] = (),
    referensdjup_m: float = STANDARD_REFERENSDJUP_M,
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
    z_flat = np.asarray(eff_z, dtype=np.float64).ravel()
    osak_flat = np.asarray(osakerhet_z, dtype=np.float64).ravel()
    aktiv_flat = aktiv.ravel()

    kantbarriarer = _berakna_kantbarriarer(z_flat, aktiv_flat, geo, brytlinjer)
    flow_to, cell_till_nod, antal_lov = _berakna_avrinningsomraden(eff_z, aktiv, kantbarriarer)

    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    cell_area_m2 = dx * dy

    flodesvagar = bygg_flodesvagar(
        eff_z,
        eff_slag,
        flow_to,
        cell_till_nod,
        geo,
        referensdjup_m=referensdjup_m,
    )

    if antal_lov == 0:
        return SankGraf(
            noder={},
            cell_till_nod=cell_till_nod,
            flodesvagar=flodesvagar,
            geo=geo,
            filtrerad=False,
            persistens_faktor=None,
            mark_z=eff_z,
            markslag=eff_slag,
            ut_lov_id={},
            overfall_celler={},
        )

    # Samla lägsta kopplingskant (+ likvärdiga sadelkanter) mellan distinkta avrinningsområden
    catch_2d = cell_till_nod
    # par -> (w_min, lista av (u, v, cu, cv))
    par_kanter: dict[tuple[int, int], tuple[float, list[tuple[int, int, int, int]]]] = {}

    def _registrera_kant(u: int, v: int, cu: int, cv: int, w: float) -> None:
        nyckel = (cu, cv) if cu < cv else (cv, cu)
        bef = par_kanter.get(nyckel)
        if bef is None or w < bef[0] - _SADEL_TIE_TOL_M:
            par_kanter[nyckel] = (w, [(u, v, cu, cv)])
        elif abs(w - bef[0]) <= _SADEL_TIE_TOL_M:
            bef[1].append((u, v, cu, cv))

    # Vertikala grannkanter där avrinningsområdena skiljer sig åt
    v_diff = (catch_2d[:-1, :] != catch_2d[1:, :]) & aktiv[:-1, :] & aktiv[1:, :]
    vr_arr, vc_arr = np.nonzero(v_diff)
    for r_np, c_np in zip(vr_arr, vc_arr, strict=True):
        r, c = int(r_np), int(c_np)
        u = r * bredd + c
        v = (r + 1) * bredd + c
        cu = int(catch_2d[r, c])
        cv = int(catch_2d[r + 1, c])
        w = kantbarriarer.get((u, v), max(float(z_flat[u]), float(z_flat[v])))
        _registrera_kant(u, v, cu, cv, w)

    # Horisontella grannkanter där avrinningsområdena skiljer sig åt
    h_diff = (catch_2d[:, :-1] != catch_2d[:, 1:]) & aktiv[:, :-1] & aktiv[:, 1:]
    hr_arr, hc_arr = np.nonzero(h_diff)
    for r_np, c_np in zip(hr_arr, hc_arr, strict=True):
        r, c = int(r_np), int(c_np)
        u = r * bredd + c
        v = r * bredd + (c + 1)
        cu = int(catch_2d[r, c])
        cv = int(catch_2d[r, c + 1])
        w = kantbarriarer.get((u, v), max(float(z_flat[u]), float(z_flat[v])))
        _registrera_kant(u, v, cu, cv, w)

    # Ytterrandskanter till HAV för randceller som dränerar inåt en sänka
    rand_mask = np.zeros((hojd, bredd), dtype=bool)
    rand_mask[0, :] = True
    rand_mask[-1, :] = True
    rand_mask[:, 0] = True
    rand_mask[:, -1] = True
    rand_intern = rand_mask & aktiv & (catch_2d >= 0)
    rr_arr, rc_arr = np.nonzero(rand_intern)
    for r_np, c_np in zip(rr_arr, rc_arr, strict=True):
        r, c = int(r_np), int(c_np)
        u = r * bredd + c
        cu = int(catch_2d[r, c])
        _registrera_kant(u, -1, cu, HAV_NOD_ID, float(z_flat[u]))

    # Sortera alla par i stigande sadelhöjd för Kruskal / Barnes (2020) sammanslagningsträd
    sorterade_par = sorted(
        ((w_min, nyckel[0], nyckel[1], kanter) for nyckel, (w_min, kanter) in par_kanter.items()),
        key=lambda item: (item[0], item[1], item[2]),
    )

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
        zu = float(z_flat[u])
        zv = float(z_flat[v])
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

    for w_min, c_min, c_max, kanter in sorterade_par:
        ra = _uf_hitta(c_min + 1)
        rb = _uf_hitta(c_max + 1)
        if ra == rb:
            continue

        kanter.sort(key=lambda e: (e[0], e[1]))
        u_rep, v_rep, cu_rep, cv_rep = kanter[len(kanter) // 2]
        sc_idx = _sadel_cell(u_rep, v_rep)
        sr, sc = divmod(sc_idx, bredd)
        sadel_x = geo.x_min + (sc + 0.5) * dx
        sadel_y = geo.y_max - (sr + 0.5) * dy
        sadel_osak = float(osak_flat[sc_idx]) if math.isfinite(float(osak_flat[sc_idx])) else 0.0

        sadel_rc_lista: list[tuple[int, int]] = []
        sedda_rc: set[tuple[int, int]] = set()
        for eu, ev, _, _ in kanter:
            rc = divmod(_sadel_cell(eu, ev), bredd)
            if rc not in sedda_rc:
                sedda_rc.add(rc)
                sadel_rc_lista.append(rc)
        sadel_rc_tup = tuple(sadel_rc_lista)

        if ar_hav[ra] or ar_hav[rb]:
            if ar_hav[ra]:
                r_int = rb
                andra_lov = cu_rep if _uf_hitta(cu_rep + 1) == ra else cv_rep
            else:
                r_int = ra
                andra_lov = cu_rep if _uf_hitta(cu_rep + 1) == rb else cv_rep

            nod_id = topp_nod[r_int]
            ut_target = None if andra_lov == HAV_NOD_ID else int(andra_lov)

            overfall_z_karta[nod_id] = float(w_min)
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
            overfall_z_karta[a_id] = float(w_min)
            overfall_z_karta[b_id] = float(w_min)
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

    # Samla cellhöjder per lövsänka i ett svep
    catch_flat = cell_till_nod.ravel()
    lov_mask = catch_flat >= 0
    lov_ids_flat = catch_flat[lov_mask]
    lov_z_flat = z_flat[lov_mask]

    ordn = np.argsort(lov_ids_flat, kind="mergesort")
    sort_ids = lov_ids_flat[ordn]
    sort_z = lov_z_flat[ordn]
    unika_lov, start_idx, antal_per_lov = np.unique(
        sort_ids, return_index=True, return_counts=True
    )

    celler_per_lov: dict[int, NDArray[np.float64]] = {}
    for lid_np, s_np, cnt_np in zip(unika_lov, start_idx, antal_per_lov, strict=True):
        lid = int(lid_np)
        s = int(s_np)
        cnt = int(cnt_np)
        celler_per_lov[lid] = sort_z[s : s + cnt]

    # Bygg SankNod-objekt nedifrån och upp (löv först, sedan föräldrar)
    noder: dict[int, SankNod] = {}
    lov_i_deltrad: dict[int, list[int]] = {}

    for nid in range(nasta_nod_id):
        barn = barn_karta[nid]
        if not barn:
            lov_i_deltrad[nid] = [nid]
            z_arr = celler_per_lov.get(nid, np.empty(0, dtype=np.float64))
            avrinning_m2 = float(z_arr.size) * cell_area_m2
        else:
            alla_lov: list[int] = []
            for b_id in barn:
                alla_lov.extend(lov_i_deltrad[b_id])
            lov_i_deltrad[nid] = alla_lov
            z_delar = [celler_per_lov[lid] for lid in alla_lov if lid in celler_per_lov]
            z_arr = (
                np.asarray(np.concatenate(z_delar), dtype=np.float64)
                if z_delar
                else np.empty(0, dtype=np.float64)
            )
            avrinning_m2 = 0.0

        overfall_z = overfall_z_karta.get(nid)
        if overfall_z is None:
            # Bör inte ske på ändlig domän med randkanter, men skydda mot tyst fel
            raise SankGrafFel(f"Nod {nid} saknar utloppssadel mot randen.")

        vol_kurva, yt_kurva, botten_z, cell_antal = berakna_kurvor_for_celler(
            z_arr, overfall_z, cell_area_m2
        )
        ox, oy = overfall_xy_karta[nid]
        noder[nid] = SankNod(
            nod_id=nid,
            forald_id=forald_karta[nid],
            barn_id=barn,
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
        )

    return SankGraf(
        noder=noder,
        cell_till_nod=cell_till_nod,
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
