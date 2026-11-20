"""Flödesriktningar, flödesvägar och Manning-gångtid vid referensdjup (K2)."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from sankgraf.typer import (
    BYGGNAD_NOD_ID,
    HAV_NOD_ID,
    MARKSLAG_BYGGNAD,
    FlodesVag,
    SankGrafFel,
)
from testsvit.k1_io import GeoTransformInfo

# Mannings råhetstal n (s / m^(1/3)) per K1-markslagskod
MANNING_N_PER_MARKSLAG: dict[int, float] = {
    0: 0.030,  # okänt
    1: 0.015,  # hårdgjort (slät asfalt/betong)
    2: 0.015,  # tak
    3: 0.060,  # grönt tak
    4: 0.050,  # gräs
    5: 0.100,  # skog
    6: 0.030,  # berg i dagen
    7: 0.025,  # grus
    8: 0.020,  # vatten
    9: 0.015,  # byggnad (används ej som aktiv markcell)
}

STANDARD_REFERENSDJUP_M = 0.05
MIN_LUTNING = 1e-4


def manning_hastighet_m_s(
    lutning: float,
    manning_n: float,
    referensdjup_m: float = STANDARD_REFERENSDJUP_M,
    *,
    min_lutning: float = MIN_LUTNING,
) -> float:
    """Beräknar strömningshastighet (m/s) med Manning-formeln: v = (1/n) * h^(2/3) * S^(1/2)."""
    if manning_n <= 0.0 or not math.isfinite(manning_n):
        raise SankGrafFel(f"Ogiltigt Manning-tal n: {manning_n}")
    if referensdjup_m <= 0.0 or not math.isfinite(referensdjup_m):
        raise SankGrafFel(f"Ogiltigt referensdjup: {referensdjup_m}")
    eff_lutning = max(float(lutning), float(min_lutning))
    return (
        (1.0 / float(manning_n))
        * math.pow(float(referensdjup_m), 2.0 / 3.0)
        * math.sqrt(eff_lutning)
    )


def berakna_flodesriktning_deg(vag: FlodesVag) -> float:
    """Beräknar huvudriktning i grader medurs från norr (90° = öster) för en flödesväg."""
    if len(vag.koordinater) < 2:
        raise SankGrafFel("Flödesvägen måste ha minst två punkter för riktningsberäkning.")
    x0, y0 = vag.koordinater[0]
    x1, y1 = vag.koordinater[-1]
    dx = x1 - x0
    dy = y1 - y0
    if dx == 0.0 and dy == 0.0:
        return 0.0
    return float(math.degrees(math.atan2(dx, dy)) % 360.0)


def _cell_centrum_xy(rad: int, kol: int, geo: GeoTransformInfo) -> tuple[float, float]:
    x = geo.x_min + (kol + 0.5) * geo.pixelstorlek_x
    y = geo.y_max - (rad + 0.5) * geo.pixelstorlek_y
    return (x, y)


def _forenkla_kollineara(
    punkter: list[tuple[float, float]],
    tol: float = 1e-9,
) -> tuple[tuple[float, float], ...]:
    """Tar bort kollineära mellanpunkter längs raka delsträckor."""
    if len(punkter) <= 2:
        return tuple(punkter)
    ut: list[tuple[float, float]] = [punkter[0]]
    for idx in range(1, len(punkter) - 1):
        x0, y0 = ut[-1]
        x1, y1 = punkter[idx]
        x2, y2 = punkter[idx + 1]
        kors = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
        if abs(kors) > tol:
            ut.append(punkter[idx])
    ut.append(punkter[-1])
    return tuple(ut)


def berakna_gangtidsraster(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    flow_to: NDArray[np.int64],
    geo: GeoTransformInfo,
    *,
    referensdjup_m: float = STANDARD_REFERENSDJUP_M,
    min_lutning: float = MIN_LUTNING,
    manning_tabell: Mapping[int, float] | None = None,
) -> NDArray[np.float64]:
    """Beräknar ackumulerad Manning-gångtid (s) från varje cell till dess sänka/utlopp."""
    hojd, bredd = mark_z.shape
    n_celler = hojd * bredd
    z_flat = np.asarray(mark_z, dtype=np.float64).ravel()
    slag_flat = np.asarray(markslag, dtype=np.uint8).ravel()
    flode_flat = np.asarray(flow_to, dtype=np.int64).ravel()
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)

    tabell = manning_tabell if manning_tabell is not None else MANNING_N_PER_MARKSLAG

    # Beräkna lokal stegtid (s) för varje cell som rinner vidare till en granne
    steg_tid = np.zeros(n_celler, dtype=np.float64)
    in_grad = np.zeros(n_celler, dtype=np.int64)
    in_lutning = np.full(n_celler, min_lutning, dtype=np.float64)

    for u in range(n_celler):
        v = int(flode_flat[u])
        if v >= 0:
            in_grad[v] += 1
            ur, uc = divmod(u, bredd)
            vr, vc = divmod(v, bredd)
            ds = math.hypot((vc - uc) * dx, (vr - ur) * dy)
            lutning = max((float(z_flat[u]) - float(z_flat[v])) / ds, min_lutning)
            n_val = tabell.get(int(slag_flat[u]), 0.030)
            v_hast = manning_hastighet_m_s(
                lutning, n_val, referensdjup_m, min_lutning=min_lutning
            )
            steg_tid[u] = ds / v_hast
            if lutning > in_lutning[v]:
                in_lutning[v] = lutning

    # Randutloppsceller (flow_to == HAV_NOD_ID) passerar även sin egen cellängd ut över randen
    for u in range(n_celler):
        if int(flode_flat[u]) == HAV_NOD_ID and int(slag_flat[u]) != MARKSLAG_BYGGNAD:
            n_val = tabell.get(int(slag_flat[u]), 0.030)
            v_hast = manning_hastighet_m_s(
                float(in_lutning[u]), n_val, referensdjup_m, min_lutning=min_lutning
            )
            steg_tid[u] = dx / v_hast

    # Topologisk ordning nedströms -> uppströms via omvänd traversering med memoization
    gangtid = np.full(n_celler, -1.0, dtype=np.float64)
    for u in range(n_celler):
        if gangtid[u] >= 0.0:
            continue
        kedja: list[int] = []
        curr = u
        while curr >= 0 and gangtid[curr] < 0.0:
            kedja.append(curr)
            nxt = int(flode_flat[curr])
            if nxt < 0:
                gangtid[curr] = float(steg_tid[curr])
                kedja.pop()
                break
            curr = nxt
        bas_tid = float(gangtid[curr]) if curr >= 0 else 0.0
        for nod in reversed(kedja):
            bas_tid += float(steg_tid[nod])
            gangtid[nod] = bas_tid

    return np.asarray(gangtid.reshape((hojd, bredd)), dtype=np.float64)


def bygg_flodesvagar(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    flow_to: NDArray[np.int64],
    cell_till_nod: NDArray[np.int64],
    geo: GeoTransformInfo,
    *,
    referensdjup_m: float = STANDARD_REFERENSDJUP_M,
    min_lutning: float = MIN_LUTNING,
    grupp_steg: int | None = None,
    manning_tabell: Mapping[int, float] | None = None,
) -> list[FlodesVag]:
    """Spårar representativa cellgrupps-flödesvägar till lövsänkor och randutlopp.

    Väljer deterministiskt startceller (vattendelare/källceller på ett regelbundet
    cellgruppsglesnät samt längsta vägen per nod/utlopp) och beräknar längd,
    medellutning och Manning-gångtid vid `referensdjup_m`.
    """
    hojd, bredd = mark_z.shape
    n_celler = hojd * bredd
    z_flat = np.asarray(mark_z, dtype=np.float64).ravel()
    slag_flat = np.asarray(markslag, dtype=np.uint8).ravel()
    flode_flat = np.asarray(flow_to, dtype=np.int64).ravel()
    nod_flat = np.asarray(cell_till_nod, dtype=np.int64).ravel()
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    tabell = manning_tabell if manning_tabell is not None else MANNING_N_PER_MARKSLAG

    in_grad = np.zeros(n_celler, dtype=np.int64)
    for u in range(n_celler):
        v = int(flode_flat[u])
        if v >= 0:
            in_grad[v] += 1

    gangtid_matris = berakna_gangtidsraster(
        mark_z,
        markslag,
        flow_to,
        geo,
        referensdjup_m=referensdjup_m,
        min_lutning=min_lutning,
        manning_tabell=tabell,
    )
    gangtid_flat = gangtid_matris.ravel()

    # Bestäm cellgruppssteg så vi får lagom många men heltäckande linjer
    steg = grupp_steg if grupp_steg is not None else max(4, min(hojd, bredd) // 8)
    startceller: set[int] = set()

    # 1. Källceller (in_grad == 0) på glesnätet som faktiskt rinner minst ett steg
    for r in range(0, hojd, steg):
        for c in range(0, bredd, steg):
            u = r * bredd + c
            if (
                int(slag_flat[u]) != MARKSLAG_BYGGNAD
                and math.isfinite(float(z_flat[u]))
                and int(flode_flat[u]) >= 0
            ):
                startceller.add(u)

    # 2. Längsta gångtiden för varje unik mottagande nod (inkl. HAV_NOD_ID)
    basta_per_nod: dict[int, tuple[float, int]] = {}
    for u in range(n_celler):
        nid = int(nod_flat[u])
        if nid == BYGGNAD_NOD_ID or int(flode_flat[u]) < 0:
            continue
        t_val = float(gangtid_flat[u])
        tidigare = basta_per_nod.get(nid)
        if tidigare is None or t_val > tidigare[0]:
            basta_per_nod[nid] = (t_val, u)

    for _, u_max in basta_per_nod.values():
        startceller.add(u_max)

    flodesvagar: list[FlodesVag] = []
    vag_id = 0
    for start_u in sorted(startceller):
        # Följ flödesvägen nedströms till sänkbotten eller randutlopp
        punkter: list[tuple[float, float]] = []
        langd_m = 0.0
        tid_s = 0.0
        sista_lutning = min_lutning
        curr = start_u
        steg_raknare = 0
        max_steg = n_celler + 1

        while steg_raknare < max_steg:
            steg_raknare += 1
            cr, cc = divmod(curr, bredd)
            punkter.append(_cell_centrum_xy(cr, cc, geo))
            nxt = int(flode_flat[curr])
            if nxt < 0:
                if nxt == HAV_NOD_ID:
                    # Lägg till cellens utloppssteg över ytterranden (ger exakt 30.0 m på A4!)
                    n_val = tabell.get(int(slag_flat[curr]), 0.030)
                    v_ut = manning_hastighet_m_s(
                        sista_lutning, n_val, referensdjup_m, min_lutning=min_lutning
                    )
                    langd_m += dx
                    tid_s += dx / v_ut
                break

            nr, nc = divmod(nxt, bredd)
            ds = math.hypot((nc - cc) * dx, (nr - cr) * dy)
            lutning = max((float(z_flat[curr]) - float(z_flat[nxt])) / ds, min_lutning)
            n_val = tabell.get(int(slag_flat[curr]), 0.030)
            v_hast = manning_hastighet_m_s(
                lutning, n_val, referensdjup_m, min_lutning=min_lutning
            )
            langd_m += ds
            tid_s += ds / v_hast
            sista_lutning = lutning
            curr = nxt

        if len(punkter) < 2 or langd_m <= 0.0:
            continue

        z_start = float(z_flat[start_u])
        z_slut = float(z_flat[curr])
        if int(flode_flat[curr]) == HAV_NOD_ID:
            # Extrapolera höjdfallet över utloppssteget för exakt medellutning
            z_slut -= sista_lutning * dx
        medel_lutning = max((z_start - z_slut) / langd_m, min_lutning)

        forenklad = _forenkla_kollineara(punkter)
        if len(forenklad) < 2:
            forenklad = (punkter[0], punkter[-1])

        flodesvagar.append(
            FlodesVag(
                id=vag_id,
                nod_id=int(nod_flat[start_u]),
                koordinater=forenklad,
                langd_m=langd_m,
                lutning=medel_lutning,
                gangtid_s=tid_s,
                referensdjup_m=float(referensdjup_m),
            )
        )
        vag_id += 1

    return flodesvagar
