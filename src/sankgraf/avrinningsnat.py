"""Avrinningsnätet: flödesvägar som ett dendritiskt träd (rännilar som går ihop till stråk).

Metod (alla steg deterministiska, inget slumptal):

1. **Sänkfyllning** med prioritetsfyllning (Barnes m.fl., "Priority-Flood") över aktiva markceller.
   Rutans kant är utlopp (som sänkgrafens `HAV`); byggnader är hinder. Slutna områden som byggnader
   stänger in (sänkgrafens slutsänkor) får sin lägsta cell som utlopp. När `brytlinjer` /
   `kantbarriarer` (kantstenar, murar) ges körs minimax-Priority-Flood där barriärkanter höjer
   tröskeln till `max(zc, z_barriar)` och diagonala steg över blockerade barriärer förbjuds så
   att 20°- och 45°-kantstenar aldrig läcker diagonalt.
2. **D8-flödesriktning** (brantast nedåt, diagonal viktad med avstånd) på den fyllda ytan; på plana
   ytor följs fyllningens väg mot utloppet. Barriärkanter med `z_barriar > zf[u] + 1e-12` väljs
   inte som nedförsflöde utan flödet styrs längs kantstenens lågsida (rännstenen).
3. **Tillrinningsyta** per cell (m²): cellens egen yta plus takvatten som sänkgrafen leder till
   cellen (`graf.takvatten`, körningens takantagande; utan takvatten räknas inte taken alls),
   summerad nedströms.
4. **Stråk**: celler med tillrinning över tröskeln, men bara där flödet samlas. En lutande yta ger
   i D8 parallella banor som alla växer med avståndet; det är bladflöde och inte ett stråk. En
   cell räknas som stråk först när dess tillrinning är minst dubbelt så stor som medelvärdet i
   7×7 celler runt den; nedströms ett stråk fortsätter det så länge tröskeln är nådd.
5. **Linjer** från källa (eller sammanflöde) till nästa sammanflöde, med Strahler-ordning,
   rännstenssnäppning längs närliggande kantstenar (`<= 1,5 m`, `|cos θ| >= 0,7`, `0,35 m` offset),
   längdviktad Taubin-utjämning (4 pass, `λ=0,5`, `μ=-0,45`) med låsta noder och kollisionsvakt mot
   byggnader och barriärer samt skonsam bevakad förenkling (`0,20 m`).

Takvatten: byggnadscellerna är hinder; deras takyta räknas in i den markcell sänkgrafen leder
den till (`TakvattenFordelning.extra_area_m2`).
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import uniform_filter

from sankgraf.hierarki import _berakna_kantbarriarer
from sankgraf.rust_brygga import (
    kor_avrinningsnat_rust,
    kor_avrinningsnat_tillrinning_rust,
    valj_sankgraf_karna,
)
from sankgraf.typer import SankGraf
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo

STANDARD_MIN_YTA_M2 = 500.0
FORENKLING_M = 0.20
KONCENTRATION_FONSTER = 7
KONCENTRATION_FAKTOR = 2.0

RANNSTEN_MAX_AVST_M = 1.5
RANNSTEN_FULL_SNAPP_M = 1.0
RANNSTEN_OFFSET_M = 0.35
RANNSTEN_MIN_PARALLELL_COS = 0.7

UTJAMNING_PASS = 4
TAUBIN_LAMBDA = 0.5
TAUBIN_MU = -0.45

_FALL_EPS = 1e-12
_GRID_CELL_M = 5.0

_GRANNAR: tuple[tuple[int, int], ...] = (
    (-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1),
)  # fmt: skip

_GRANNAR_AVST: tuple[tuple[int, int, float], ...] = tuple(
    (dr, dc, math.hypot(dr, dc)) for dr, dc in _GRANNAR
)


@dataclass(frozen=True)
class Stracka:
    """En rännil/ett stråk från källa eller sammanflöde till nästa sammanflöde (SWEREF 99 18 00)."""

    id: int
    koordinater: tuple[tuple[float, float], ...]
    tillrinning_m2: float
    ordning: int
    langd_m: float
    lutning: float
    typ: str = "markstrak"


def _kanonisera_kantbarriarer(
    kantbarriarer: Mapping[tuple[int, int], float] | None,
) -> dict[tuple[int, int], float]:
    if not kantbarriarer:
        return {}
    ut: dict[tuple[int, int], float] = {}
    for (u, v), w in kantbarriarer.items():
        nyckel = (int(u), int(v)) if u < v else (int(v), int(u))
        wf = float(w)
        tidigare = ut.get(nyckel)
        if tidigare is None or wf > tidigare:
            ut[nyckel] = wf
    return ut


def _bygg_nar_barriar(
    hojd: int,
    bredd: int,
    n: int,
    barriarer: Mapping[tuple[int, int], float],
) -> bytearray:
    """Markerar alla celler vars 4- eller 8-grannekanter berör minst en barriärkant."""
    nar = bytearray(n)
    for bu, bv in barriarer:
        if bu < 0 or bv < 0 or bu >= n or bv >= n:
            continue
        if bv == bu + bredd:
            r, c = divmod(bu, bredd)
            r1 = r + 1
            nar[r * bredd + c] = 1
            nar[r1 * bredd + c] = 1
            if c > 0:
                nar[r * bredd + (c - 1)] = 1
                nar[r1 * bredd + (c - 1)] = 1
            if c + 1 < bredd:
                nar[r * bredd + (c + 1)] = 1
                nar[r1 * bredd + (c + 1)] = 1
        elif bv == bu + 1:
            r, c = divmod(bu, bredd)
            c1 = c + 1
            nar[r * bredd + c] = 1
            nar[r * bredd + c1] = 1
            if r > 0:
                nar[(r - 1) * bredd + c] = 1
                nar[(r - 1) * bredd + c1] = 1
            if r + 1 < hojd:
                nar[(r + 1) * bredd + c] = 1
                nar[(r + 1) * bredd + c1] = 1
        else:
            nar[bu] = 1
            nar[bv] = 1
    return nar


def _diag_barriar_hojd(
    barriar_get: Callable[[tuple[int, int]], float | None],
    akt_ba: bytearray,
    z_ref: list[float],
    bredd: int,
    cr: int,
    cc: int,
    dr: int,
    dc: int,
) -> float | None:
    """Effektiv barriärhöjd över diagonalen `(cr, cc) -> (cr+dr, cc+dc)` via 2 ortogonala vägar."""
    u = cr * bredd + cc
    v = (cr + dr) * bredd + (cc + dc)
    m1 = (cr + dr) * bredd + cc
    m2 = cr * bredd + (cc + dc)

    h1a = barriar_get((u, m1) if u < m1 else (m1, u))
    h1b = barriar_get((m1, v) if m1 < v else (v, m1))
    h2a = barriar_get((u, m2) if u < m2 else (m2, u))
    h2b = barriar_get((m2, v) if m2 < v else (v, m2))

    if h1a is None and h1b is None and h2a is None and h2b is None:
        return None

    if not akt_ba[m1]:
        b1 = math.inf
    else:
        b1 = z_ref[m1]
        if h1a is not None and h1a > b1:
            b1 = h1a
        if h1b is not None and h1b > b1:
            b1 = h1b

    if not akt_ba[m2]:
        b2 = math.inf
    else:
        b2 = z_ref[m2]
        if h2a is not None and h2a > b2:
            b2 = h2a
        if h2b is not None and h2b > b2:
            b2 = h2b

    return b1 if b1 <= b2 else b2


def fyll_och_riktning(
    z: NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    *,
    kantbarriarer: Mapping[tuple[int, int], float] | None = None,
    anvand_rust: bool | None = None,
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Prioritetsfyllning och D8. Returnerar `(flow_to, uppstroms_ordning)`.

    `flow_to` är platt cellindex nedströms, -1 för utlopp (rutkant eller lägsta cell i ett slutet
    område) och -2 för inaktiva celler. `uppstroms_ordning` listar de aktiva cellerna så att
    varje cell kommer före sin mottagare i omvänd ordning (bearbetas bakifrån för att summera)."""
    h, b = z.shape
    n = h * b
    barriarer = _kanonisera_kantbarriarer(kantbarriarer)
    rust_program = valj_sankgraf_karna(n, anvand_rust=anvand_rust)
    if rust_program is not None:
        flow_rs, ordn_rs = kor_avrinningsnat_rust(
            z,
            aktiv,
            kantbarriarer=barriarer,
            binar=rust_program,
        )
        return np.asarray(flow_rs, dtype=np.int64), np.asarray(ordn_rs, dtype=np.int64)
    return _fyll_och_riktning_python(z, aktiv, kantbarriarer=barriarer)


def _fyll_och_riktning_python(
    z: NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    kantbarriarer: Mapping[tuple[int, int], float] | None = None,
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Ren Python-referens för prioritetsfyllning och D8 (bitidentisk med Rust-kärnan)."""
    h, b = z.shape
    n = h * b
    barriarer = _kanonisera_kantbarriarer(kantbarriarer)
    zf = np.where(aktiv, z, np.inf).ravel().astype(np.float64, copy=True)
    akt = np.asarray(aktiv, dtype=np.bool_).ravel()
    parent = np.full(n, -2, dtype=np.int64)
    ko: list[tuple[float, int, int]] = []
    rakn = 0
    idx = np.arange(n).reshape(h, b)
    kant = np.zeros((h, b), dtype=bool)
    if h > 0 and b > 0:
        kant[0, :] = kant[-1, :] = kant[:, 0] = kant[:, -1] = True
    ordning: list[int] = []

    if not barriarer:
        klar = ~akt.copy()
        for u in idx[kant & aktiv].tolist():
            klar[u] = True
            parent[u] = -1
            heapq.heappush(ko, (float(zf[u]), rakn, u))
            rakn += 1
        lagsta = np.argsort(np.where(akt, zf, np.inf), kind="stable")
        pek = 0
        while True:
            while ko:
                zc, _, c = heapq.heappop(ko)
                ordning.append(c)
                cr, cc = divmod(c, b)
                for dr, dc in _GRANNAR:
                    nr, nc = cr + dr, cc + dc
                    if 0 <= nr < h and 0 <= nc < b:
                        v = nr * b + nc
                        if not klar[v]:
                            klar[v] = True
                            parent[v] = c
                            if zf[v] < zc:
                                zf[v] = zc
                            heapq.heappush(ko, (float(zf[v]), rakn, v))
                            rakn += 1
            while pek < n and (klar[lagsta[pek]] or not akt[lagsta[pek]]):
                pek += 1
            if pek >= n:
                break
            u = int(lagsta[pek])  # slutet område: lägsta cellen är utloppet
            klar[u] = True
            parent[u] = -1
            heapq.heappush(ko, (float(zf[u]), rakn, u))
            rakn += 1

        # D8: brantast nedåt på den fyllda ytan; platta celler följer fyllningens väg.
        zz = zf.reshape(h, b)
        basta = np.zeros((h, b), dtype=np.float64)
        mal = np.full((h, b), -1, dtype=np.int64)
        for dr, dc in _GRANNAR:
            avst = math.hypot(dr, dc)
            rs = slice(max(0, -dr), h - max(0, dr))
            cs = slice(max(0, -dc), b - max(0, dc))
            rv = slice(max(0, dr), h - max(0, -dr))
            cv = slice(max(0, dc), b - max(0, -dc))
            with np.errstate(invalid="ignore"):
                fall = (zz[rs, cs] - zz[rv, cv]) / avst
            ok = aktiv[rs, cs] & aktiv[rv, cv] & (fall > basta[rs, cs] + _FALL_EPS)
            basta[rs, cs] = np.where(ok, fall, basta[rs, cs])
            mal[rs, cs] = np.where(ok, idx[rv, cv], mal[rs, cs])
        flow = mal.ravel().copy()
        platt = akt & (flow < 0)
        flow[platt] = parent[platt]
        flow[~akt] = -2
        flow[(kant.ravel()) & akt] = -1  # rutkanten är utlopp: vattnet rinner inte längs kanten
        return flow, np.asarray(ordning, dtype=np.int64)

    # Barriärmedveten minimax-Priority-Flood + D8 (bitidentisk med `avrinningsnat.rs`)
    nar_barriar = _bygg_nar_barriar(h, b, n, barriarer)
    barriar_get = barriarer.get
    z_raw_list = zf.tolist()
    zf_list = [math.inf] * n
    best_z = [math.inf] * n
    akt_ba = bytearray(akt.astype(np.uint8).tobytes())
    klar_ba = bytearray(0 if a_v else 1 for a_v in akt_ba)
    parent_list = [-2] * n

    for u in idx[kant & aktiv].tolist():
        zu = z_raw_list[u]
        best_z[u] = zu
        parent_list[u] = -1
        heapq.heappush(ko, (zu, rakn, u))
        rakn += 1

    lagsta = np.argsort(np.where(akt, zf, np.inf), kind="stable")
    pek = 0
    while True:
        while ko:
            zc, _, c = heapq.heappop(ko)
            if klar_ba[c]:
                continue
            klar_ba[c] = 1
            zf_list[c] = zc
            ordning.append(c)
            cr, cc = divmod(c, b)
            c_nar = nar_barriar[c]
            for dr, dc in _GRANNAR:
                nr = cr + dr
                nc = cc + dc
                if 0 <= nr < h and 0 <= nc < b:
                    v = nr * b + nc
                    if klar_ba[v]:
                        continue
                    if not c_nar:
                        zb = None
                    elif dr == 0 or dc == 0:
                        nyckel = (c, v) if c < v else (v, c)
                        zb = barriar_get(nyckel)
                    else:
                        zb = _diag_barriar_hojd(barriar_get, akt_ba, z_raw_list, b, cr, cc, dr, dc)
                        if zb is not None:
                            max_end = (
                                z_raw_list[c]
                                if z_raw_list[c] >= z_raw_list[v]
                                else z_raw_list[v]
                            )
                            if not math.isfinite(zb) or zb > max_end + _FALL_EPS:
                                continue
                    cand = z_raw_list[v]
                    if zc > cand:
                        cand = zc
                    if zb is not None and zb > cand:
                        cand = zb
                    if cand < best_z[v]:
                        best_z[v] = cand
                        parent_list[v] = c
                        heapq.heappush(ko, (cand, rakn, v))
                        rakn += 1
        while pek < n and (klar_ba[int(lagsta[pek])] or not akt_ba[int(lagsta[pek])]):
            pek += 1
        if pek >= n:
            break
        u = int(lagsta[pek])
        zu = z_raw_list[u]
        best_z[u] = zu
        parent_list[u] = -1
        heapq.heappush(ko, (zu, rakn, u))
        rakn += 1

    zf_ny = np.asarray(zf_list, dtype=np.float64)
    parent = np.asarray(parent_list, dtype=np.int64)
    zz = zf_ny.reshape(h, b)
    basta = np.zeros((h, b), dtype=np.float64)
    mal = np.full((h, b), -1, dtype=np.int64)
    for dr, dc in _GRANNAR:
        avst = math.hypot(dr, dc)
        rs = slice(max(0, -dr), h - max(0, dr))
        cs = slice(max(0, -dc), b - max(0, dc))
        rv = slice(max(0, dr), h - max(0, -dr))
        cv = slice(max(0, dc), b - max(0, -dc))
        with np.errstate(invalid="ignore"):
            fall = (zz[rs, cs] - zz[rv, cv]) / avst
        ok = aktiv[rs, cs] & aktiv[rv, cv] & (fall > basta[rs, cs] + _FALL_EPS)
        basta[rs, cs] = np.where(ok, fall, basta[rs, cs])
        mal[rs, cs] = np.where(ok, idx[rv, cv], mal[rs, cs])
    flow = mal.ravel().copy()
    platt = akt & (flow < 0)
    flow[platt] = parent[platt]
    flow[~akt] = -2
    flow[(kant.ravel()) & akt] = -1

    # Räkna om exakt barriärmedveten D8 för alla inre celler som berör minst en barriär.
    for u in np.flatnonzero(np.frombuffer(nar_barriar, dtype=np.uint8)).tolist():
        if not akt_ba[u]:
            continue
        r, c = divmod(u, b)
        if r == 0 or r == h - 1 or c == 0 or c == b - 1:
            flow[u] = -1
            continue
        zu = zf_list[u]
        basta_u = 0.0
        mal_u = -1
        for dr, dc, avst in _GRANNAR_AVST:
            v = (r + dr) * b + (c + dc)
            if not akt_ba[v]:
                continue
            if dr == 0 or dc == 0:
                nyckel = (u, v) if u < v else (v, u)
                zb = barriar_get(nyckel)
            else:
                zb = _diag_barriar_hojd(barriar_get, akt_ba, zf_list, b, r, c, dr, dc)
            if zb is not None and (not math.isfinite(zb) or zb > zu + _FALL_EPS):
                continue
            fall_u = (zu - zf_list[v]) / avst
            if fall_u > basta_u + _FALL_EPS:
                basta_u = fall_u
                mal_u = v
        flow[u] = mal_u if mal_u >= 0 else parent_list[u]

    return flow, np.asarray(ordning, dtype=np.int64)


def tillrinning(
    flow: NDArray[np.int64],
    ordning: NDArray[np.int64],
    egen_yta_m2: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Summerar tillrinningsytan nedströms (omvänd uppströmsordning, så varje cell kommer sist)."""
    a = egen_yta_m2.ravel().astype(np.float64).copy()
    f = flow.tolist()
    for u in ordning[::-1].tolist():
        v = f[u]
        if v >= 0:
            a[v] += a[u]
    return a


@dataclass(frozen=True)
class _BrytSegment:
    x0: float
    y0: float
    x1: float
    y1: float
    tx: float
    ty: float
    langd: float
    ar_kantsten: bool


class _SegmentIndex:
    """Enkelt rumsligt rutnätsindex för brytlinjesegment (snäppning + kollisionsvakt)."""

    def __init__(self, brytlinjer: Sequence[BrytlinjePost], cell_m: float = _GRID_CELL_M) -> None:
        self.cell_m = float(cell_m)
        self.segment: list[_BrytSegment] = []
        self._rutor: dict[tuple[int, int], list[int]] = {}
        self.har_kantsten = False

        for post in brytlinjer:
            if len(post.koordinater) < 2:
                continue
            har_3d = post.z_kron_rh2000 is not None and len(post.z_kron_rh2000) == len(
                post.koordinater
            )
            if not har_3d and float(post.hojd_m) <= 0.0:
                continue
            ar_kantsten = "kantsten" in post.typ.lower()
            if ar_kantsten:
                self.har_kantsten = True
            for idx in range(len(post.koordinater) - 1):
                x0, y0 = float(post.koordinater[idx][0]), float(post.koordinater[idx][1])
                x1, y1 = float(post.koordinater[idx + 1][0]), float(post.koordinater[idx + 1][1])
                dx = x1 - x0
                dy = y1 - y0
                langd = math.hypot(dx, dy)
                if langd <= 1e-9:
                    continue
                seg_idx = len(self.segment)
                self.segment.append(
                    _BrytSegment(
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        tx=dx / langd,
                        ty=dy / langd,
                        langd=langd,
                        ar_kantsten=ar_kantsten,
                    )
                )
                gx0 = math.floor(min(x0, x1) / self.cell_m)
                gx1 = math.floor(max(x0, x1) / self.cell_m)
                gy0 = math.floor(min(y0, y1) / self.cell_m)
                gy1 = math.floor(max(y0, y1) / self.cell_m)
                for gx in range(gx0, gx1 + 1):
                    for gy in range(gy0, gy1 + 1):
                        self._rutor.setdefault((gx, gy), []).append(seg_idx)

    @property
    def tom(self) -> bool:
        return not self.segment

    def kandidater_bbox(
        self, x0: float, y0: float, x1: float, y1: float, marginal: float = 0.0
    ) -> list[int]:
        if self.tom:
            return []
        gx0 = math.floor((min(x0, x1) - marginal) / self.cell_m)
        gx1 = math.floor((max(x0, x1) + marginal) / self.cell_m)
        gy0 = math.floor((min(y0, y1) - marginal) / self.cell_m)
        gy1 = math.floor((max(y0, y1) + marginal) / self.cell_m)
        sedda: set[int] = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                lista = self._rutor.get((gx, gy))
                if lista:
                    sedda.update(lista)
        return sorted(sedda)


def _pa_segment(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> bool:
    eps = 1e-9
    return (
        min(ax, bx) - eps <= px <= max(ax, bx) + eps
        and min(ay, by) - eps <= py <= max(ay, by) + eps
    )


def _segment_korsar(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
) -> bool:
    """Deterministisk 2D-skärningstest för två slutna linjesegment."""
    eps = 1e-9
    if max(ax, bx) < min(cx, dx) - eps or max(cx, dx) < min(ax, bx) - eps:
        return False
    if max(ay, by) < min(cy, dy) - eps or max(cy, dy) < min(ay, by) - eps:
        return False

    o1 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    o2 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
    o3 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
    o4 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)

    if ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and (
        (o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)
    ):
        return True
    if abs(o1) <= eps and _pa_segment(ax, ay, bx, by, cx, cy):
        return True
    if abs(o2) <= eps and _pa_segment(ax, ay, bx, by, dx, dy):
        return True
    if abs(o3) <= eps and _pa_segment(cx, cy, dx, dy, ax, ay):
        return True
    return abs(o4) <= eps and _pa_segment(cx, cy, dx, dy, bx, by)


def _segment_korsar_strikt(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
) -> bool:
    """Strikt inre skärning (ej blott ändpunktskontakt vid t.ex. ett murhörn)."""
    eps = 1e-9
    if max(ax, bx) < min(cx, dx) - eps or max(cx, dx) < min(ax, bx) - eps:
        return False
    if max(ay, by) < min(cy, dy) - eps or max(cy, dy) < min(ay, by) - eps:
        return False
    o1 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    o2 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
    o3 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
    o4 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)
    return ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and (
        (o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)
    )


def _korsade_barriarer(
    seg_index: _SegmentIndex,
    p0: tuple[float, float],
    p1: tuple[float, float],
    *,
    strikt: bool = False,
) -> frozenset[int]:
    if seg_index.tom:
        return frozenset()
    ax, ay = p0
    bx, by = p1
    kand = seg_index.kandidater_bbox(ax, ay, bx, by, marginal=1e-6)
    if not kand:
        return frozenset()
    traffar: list[int] = []
    segs = seg_index.segment
    test_fn = _segment_korsar_strikt if strikt else _segment_korsar
    for sid in kand:
        s = segs[sid]
        if test_fn(ax, ay, bx, by, s.x0, s.y0, s.x1, s.y1):
            traffar.append(sid)
    return frozenset(traffar)


def _punkt_i_aktiv(
    aktiv_2d: NDArray[np.bool_],
    x_min: float,
    y_max: float,
    pixel_m: float,
    p: tuple[float, float],
) -> bool:
    h, b = aktiv_2d.shape
    c = math.floor((p[0] - x_min) / pixel_m)
    r = math.floor((y_max - p[1]) / pixel_m)
    return 0 <= r < h and 0 <= c < b and bool(aktiv_2d[r, c])


def _segment_fri_fran_byggnad(
    aktiv_2d: NDArray[np.bool_],
    x_min: float,
    y_max: float,
    pixel_m: float,
    p0: tuple[float, float],
    p1: tuple[float, float],
) -> bool:
    """Exakt cellgenomgång: kontrollerar att `p0 -> p1` aldrig går in i en inaktiv byggnadscell."""
    if not _punkt_i_aktiv(aktiv_2d, x_min, y_max, pixel_m, p1):
        return False
    h, b = aktiv_2d.shape
    gx0 = (p0[0] - x_min) / pixel_m
    gy0 = (y_max - p0[1]) / pixel_m
    gx1 = (p1[0] - x_min) / pixel_m
    gy1 = (y_max - p1[1]) / pixel_m
    dx = gx1 - gx0
    dy = gy1 - gy0
    ts: list[float] = [0.0, 1.0]
    if abs(dx) > 1e-12:
        c_lo = math.floor(min(gx0, gx1)) + 1
        c_hi = math.ceil(max(gx0, gx1)) - 1
        for c in range(c_lo, c_hi + 1):
            t = (c - gx0) / dx
            if 0.0 < t < 1.0:
                ts.append(t)
    if abs(dy) > 1e-12:
        r_lo = math.floor(min(gy0, gy1)) + 1
        r_hi = math.ceil(max(gy0, gy1)) - 1
        for r in range(r_lo, r_hi + 1):
            t = (r - gy0) / dy
            if 0.0 < t < 1.0:
                ts.append(t)
    ts.sort()
    for t0, t1 in zip(ts[:-1], ts[1:], strict=True):
        if t1 - t0 <= 1e-9:
            continue
        tm = 0.5 * (t0 + t1)
        c = math.floor(gx0 + tm * dx)
        r = math.floor(gy0 + tm * dy)
        if not (0 <= r < h and 0 <= c < b) or not aktiv_2d[r, c]:
            return False
    return True


def _snappa_mot_kantsten(
    ra_pts: list[tuple[float, float]],
    seg_index: _SegmentIndex,
    aktiv_2d: NDArray[np.bool_],
    *,
    x_min: float,
    y_max: float,
    pixel_m: float,
) -> tuple[list[tuple[float, float]], str]:
    """Projicerar stråkceller längs kantsten (`<= 1,5 m`, `|cos θ| >= 0,7`) till `0,35 m`."""
    m = len(ra_pts)
    if m < 2 or not seg_index.har_kantsten:
        return list(ra_pts), "markstrak"

    pts = list(ra_pts)
    kantsten_traffar = 0
    segs = seg_index.segment

    for i in range(m):
        px, py = ra_pts[i]
        i0 = max(0, i - 2)
        i1 = min(m - 1, i + 2)
        fx = ra_pts[i1][0] - ra_pts[i0][0]
        fy = ra_pts[i1][1] - ra_pts[i0][1]
        flen = math.hypot(fx, fy)
        if flen <= 1e-12:
            i0 = max(0, i - 1)
            i1 = min(m - 1, i + 1)
            fx = ra_pts[i1][0] - ra_pts[i0][0]
            fy = ra_pts[i1][1] - ra_pts[i0][1]
            flen = math.hypot(fx, fy)
        if flen <= 1e-12:
            continue
        fx /= flen
        fy /= flen

        basta_avst = RANNSTEN_MAX_AVST_M + 1.0
        basta_proj: tuple[float, float, _BrytSegment] | None = None
        for sid in seg_index.kandidater_bbox(px, py, px, py, marginal=RANNSTEN_MAX_AVST_M):
            s = segs[sid]
            if not s.ar_kantsten:
                continue
            if abs(fx * s.tx + fy * s.ty) < RANNSTEN_MIN_PARALLELL_COS:
                continue
            dx = s.x1 - s.x0
            dy = s.y1 - s.y0
            t = ((px - s.x0) * dx + (py - s.y0) * dy) / (s.langd * s.langd)
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            qx = s.x0 + t * dx
            qy = s.y0 + t * dy
            avst = math.hypot(px - qx, py - qy)
            if avst <= RANNSTEN_MAX_AVST_M and avst < basta_avst:
                basta_avst = avst
                basta_proj = (qx, qy, s)

        if basta_proj is None:
            continue
        kantsten_traffar += 1

        # Ändpunkter (källa, sammanflöden, utlopp) är låsta för obruten topologi.
        if i == 0 or i == m - 1:
            continue

        qx, qy, s = basta_proj
        nx, ny = -s.ty, s.tx
        sida_val = (px - qx) * nx + (py - qy) * ny
        if abs(sida_val) <= 1e-6:
            continue
        sida = 1.0 if sida_val > 0.0 else -1.0
        mal_x = qx + sida * RANNSTEN_OFFSET_M * nx
        mal_y = qy + sida * RANNSTEN_OFFSET_M * ny

        if basta_avst <= RANNSTEN_FULL_SNAPP_M:
            vikt = 1.0
        else:
            vikt = max(
                0.0,
                (RANNSTEN_MAX_AVST_M - basta_avst)
                / (RANNSTEN_MAX_AVST_M - RANNSTEN_FULL_SNAPP_M),
            )
        cand = (px + vikt * (mal_x - px), py + vikt * (mal_y - py))

        if not _punkt_i_aktiv(aktiv_2d, x_min, y_max, pixel_m, cand):
            continue
        if _korsade_barriarer(seg_index, (px, py), cand):
            continue
        fore_tillatna = _korsade_barriarer(seg_index, pts[i - 1], (px, py), strikt=True)
        efter_tillatna = _korsade_barriarer(seg_index, (px, py), ra_pts[i + 1], strikt=True)
        if not (_korsade_barriarer(seg_index, pts[i - 1], cand) <= fore_tillatna):
            continue
        if not (_korsade_barriarer(seg_index, cand, ra_pts[i + 1]) <= efter_tillatna):
            continue
        if not _segment_fri_fran_byggnad(aktiv_2d, x_min, y_max, pixel_m, pts[i - 1], cand):
            continue
        if not _segment_fri_fran_byggnad(aktiv_2d, x_min, y_max, pixel_m, cand, ra_pts[i + 1]):
            continue
        pts[i] = cand

    typ = "rannsten" if kantsten_traffar * 2 >= m else "markstrak"
    return pts, typ


def _taubin_utjamning_med_vakt(
    pts: list[tuple[float, float]],
    seg_index: _SegmentIndex,
    aktiv_2d: NDArray[np.bool_],
    *,
    x_min: float,
    y_max: float,
    pixel_m: float,
    antal_pass: int = UTJAMNING_PASS,
) -> list[tuple[float, float]]:
    """Längdviktad Taubin-utjämning (`λ=0,5, μ=-0,45`) med låsta ändpunkter och kollisionsvakt."""
    m = len(pts)
    if m < 3:
        return list(pts)

    akt_pts = list(pts)
    for _ in range(antal_pass):
        for faktor in (TAUBIN_LAMBDA, TAUBIN_MU):
            ny = list(akt_pts)
            for i in range(1, m - 1):
                p_prev = akt_pts[i - 1]
                p_cur = akt_pts[i]
                p_next = akt_pts[i + 1]
                d_prev = math.hypot(p_cur[0] - p_prev[0], p_cur[1] - p_prev[1])
                d_next = math.hypot(p_next[0] - p_cur[0], p_next[1] - p_cur[1])
                d_sum = d_prev + d_next
                if d_sum <= 1e-12:
                    continue
                w_prev = d_next / d_sum
                w_next = d_prev / d_sum
                avg_x = w_prev * p_prev[0] + w_next * p_next[0]
                avg_y = w_prev * p_prev[1] + w_next * p_next[1]
                cand = (
                    p_cur[0] + faktor * (avg_x - p_cur[0]),
                    p_cur[1] + faktor * (avg_y - p_cur[1]),
                )
                if math.hypot(cand[0] - p_cur[0], cand[1] - p_cur[1]) <= 1e-12:
                    continue
                if not _punkt_i_aktiv(aktiv_2d, x_min, y_max, pixel_m, cand):
                    continue
                if _korsade_barriarer(seg_index, ny[i], cand):
                    continue
                fore_tillatna = _korsade_barriarer(seg_index, ny[i - 1], ny[i], strikt=True)
                efter_tillatna = _korsade_barriarer(seg_index, ny[i], ny[i + 1], strikt=True)
                if not (_korsade_barriarer(seg_index, ny[i - 1], cand) <= fore_tillatna):
                    continue
                if not (_korsade_barriarer(seg_index, cand, ny[i + 1]) <= efter_tillatna):
                    continue
                if not _segment_fri_fran_byggnad(aktiv_2d, x_min, y_max, pixel_m, ny[i - 1], cand):
                    continue
                if not _segment_fri_fran_byggnad(aktiv_2d, x_min, y_max, pixel_m, cand, ny[i + 1]):
                    continue
                ny[i] = cand
            akt_pts = ny
    return akt_pts


def _punkt_till_segment_avst(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= 1e-18:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _forenkla_med_vakt(
    pts: list[tuple[float, float]],
    tol_m: float,
    seg_index: _SegmentIndex,
    aktiv_2d: NDArray[np.bool_],
    *,
    x_min: float,
    y_max: float,
    pixel_m: float,
) -> list[tuple[float, float]]:
    """Bevakad Douglas-Peucker: förenklar inom `tol_m` utan att skära byggnad eller barriär."""
    m = len(pts)
    if m <= 2 or tol_m <= 0.0:
        return list(pts)

    kant_barriarer = (
        [_korsade_barriarer(seg_index, pts[i], pts[i + 1], strikt=True) for i in range(m - 1)]
        if not seg_index.tom
        else []
    )

    def _rec(lo: int, hi: int) -> list[tuple[float, float]]:
        if hi <= lo + 1:
            return [pts[lo], pts[hi]]
        a = pts[lo]
        b = pts[hi]
        max_d = -1.0
        mid = lo + 1
        for k in range(lo + 1, hi):
            d = _punkt_till_segment_avst(pts[k], a, b)
            if d > max_d:
                max_d = d
                mid = k
        if max_d <= tol_m:
            bar_ok = True
            if not seg_index.tom:
                tillatna: set[int] = set()
                for idx_k in range(lo, hi):
                    tillatna.update(kant_barriarer[idx_k])
                bar_ok = _korsade_barriarer(seg_index, a, b) <= tillatna
            if bar_ok and _segment_fri_fran_byggnad(aktiv_2d, x_min, y_max, pixel_m, a, b):
                return [a, b]
        vanster = _rec(lo, mid)
        hoger = _rec(mid, hi)
        return vanster[:-1] + hoger

    return _rec(0, m - 1)


def berakna_avrinningsfalt(
    z: NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    extra_yta_m2: NDArray[np.float64] | None,
    *,
    pixel_m: float,
    x_min: float | None = None,
    y_max: float | None = None,
    brytlinjer: Sequence[BrytlinjePost] = (),
    kantbarriarer: Mapping[tuple[int, int], float] | None = None,
    anvand_rust: bool | None = None,
) -> tuple[NDArray[np.int32], NDArray[np.int32], NDArray[np.float64]]:
    """Beräknar `(flow_to, uppstroms_ordning, tillrinning_m2)` oberoende av `min_yta_m2`.

    Resultatet kan cachas per `(körning, scenario)` så att reglaget för minsta tillrinning
    bara kör den snabba stråk- och linjeextraktionen (`strackor_fran_falt`)."""
    h, b = z.shape
    n = h * b
    z64 = np.asarray(z, dtype=np.float64)
    aktiv_b = np.asarray(aktiv, dtype=np.bool_)

    barriarer: dict[tuple[int, int], float]
    if kantbarriarer is not None:
        barriarer = _kanonisera_kantbarriarer(kantbarriarer)
    elif brytlinjer:
        if x_min is None or y_max is None:
            raise ValueError(
                "x_min och y_max krävs när brytlinjer anges till berakna_avrinningsfalt."
            )
        geo = GeoTransformInfo(
            bredd=b,
            hojd=h,
            x_min=float(x_min),
            y_max=float(y_max),
            pixelstorlek_x=float(pixel_m),
            pixelstorlek_y=float(pixel_m),
            epsg=3011,
            dtype="float64",
        )
        barriarer = _berakna_kantbarriarer(z64.ravel(), aktiv_b.ravel(), geo, brytlinjer)
    else:
        barriarer = {}

    egen = np.where(aktiv_b, pixel_m * pixel_m, 0.0)
    if extra_yta_m2 is not None:
        egen = egen + np.where(aktiv_b, extra_yta_m2, 0.0)
    egen_f64 = np.asarray(egen, dtype=np.float64)
    rust_program = valj_sankgraf_karna(n, anvand_rust=anvand_rust)
    if rust_program is not None:
        return kor_avrinningsnat_tillrinning_rust(
            z64,
            aktiv_b,
            egen_f64,
            kantbarriarer=barriarer,
            binar=rust_program,
        )
    flow64, ordn64 = _fyll_och_riktning_python(z64, aktiv_b, kantbarriarer=barriarer)
    a = tillrinning(flow64, ordn64, egen_f64)
    return (
        np.asarray(flow64, dtype=np.int32),
        np.asarray(ordn64, dtype=np.int32),
        a,
    )


def strackor_fran_falt(
    z: NDArray[np.float32] | NDArray[np.float64],
    flow: NDArray[np.int32] | NDArray[np.int64],
    ordn: NDArray[np.int32] | NDArray[np.int64],
    a: NDArray[np.float64],
    *,
    x_min: float,
    y_max: float,
    pixel_m: float,
    min_yta_m2: float = STANDARD_MIN_YTA_M2,
    forenkling_m: float = FORENKLING_M,
    brytlinjer: Sequence[BrytlinjePost] = (),
    aktiv: NDArray[np.bool_] | None = None,
) -> list[Stracka]:
    """Extraherar stråk och Strahler-ordnade linjer ur färdigberäknat `(flow, ordn, a)`."""
    if min_yta_m2 <= 0 or not math.isfinite(min_yta_m2):
        raise ValueError("min_yta_m2 måste vara ett positivt tal.")
    h, b = z.shape
    n = h * b
    aktiv_2d = (
        np.asarray(aktiv, dtype=np.bool_)
        if aktiv is not None
        else (np.asarray(flow).ravel() != -2).reshape(h, b)
    )
    a2 = a.reshape(h, b)
    medel = uniform_filter(a2, size=KONCENTRATION_FONSTER, mode="constant")
    samlar = (a2 >= min_yta_m2) & (a2 >= KONCENTRATION_FAKTOR * medel)
    # Ett stråk fortsätter nedströms så länge tröskeln är nådd. Genom att enbart gå nedströms från
    # `samlar`-celler och stanna vid redan markerade celler besöks varje stråkcell exakt en gång
    # (matematiskt identiskt med fullt svep över `ordn[::-1]`, men ~200x snabbare).
    strak = np.zeros(n, dtype=bool)
    for u in np.flatnonzero(samlar.ravel()).tolist():
        c = u
        while c >= 0 and not strak[c]:
            strak[c] = True
            nxt = int(flow[c])
            if nxt < 0 or float(a[nxt]) < min_yta_m2:
                break
            c = nxt

    strak_celler = np.flatnonzero(strak)
    if strak_celler.size == 0:
        return []

    # Inflöden av stråkceller
    inflod = np.zeros(n, dtype=np.int32)
    for u in strak_celler.tolist():
        v = int(flow[u])
        if v >= 0 and strak[v]:
            inflod[v] += 1

    # Strahler: uppströms först, filtrerat till enbart stråkceller
    strahler = np.zeros(n, dtype=np.int32)
    max_in = np.zeros(n, dtype=np.int32)
    antal_max = np.zeros(n, dtype=np.int32)
    ordn_strak = ordn[strak[ordn]]
    for u in ordn_strak[::-1].tolist():
        if inflod[u] == 0:
            strahler[u] = 1
        else:
            strahler[u] = max_in[u] + (1 if antal_max[u] >= 2 else 0)
        v = int(flow[u])
        if v >= 0 and strak[v]:
            s = int(strahler[u])
            if s > max_in[v]:
                max_in[v], antal_max[v] = s, 1
            elif s == max_in[v]:
                antal_max[v] += 1

    def xy(c: int) -> tuple[float, float]:
        r, k = divmod(c, b)
        return (x_min + (k + 0.5) * pixel_m, y_max - (r + 0.5) * pixel_m)

    seg_index = _SegmentIndex(brytlinjer)
    z_flat = np.asarray(z, dtype=np.float64).ravel()
    huvuden = [u for u in strak_celler.tolist() if inflod[u] != 1]
    ut: list[Stracka] = []
    for h0 in huvuden:
        celler = [h0]
        c = h0
        while True:
            v = int(flow[c])
            if v < 0 or not strak[v]:
                break
            celler.append(v)
            if inflod[v] >= 2:
                break
            c = v
        if len(celler) < 2:
            continue
        slut_ar_sammanflode = inflod[celler[-1]] >= 2
        a_slut = float(a[celler[-2] if slut_ar_sammanflode else celler[-1]])
        ra_pts = [xy(q) for q in celler]
        snappade, stracka_typ = _snappa_mot_kantsten(
            ra_pts,
            seg_index,
            aktiv_2d,
            x_min=x_min,
            y_max=y_max,
            pixel_m=pixel_m,
        )
        utjamnade = _taubin_utjamning_med_vakt(
            snappade,
            seg_index,
            aktiv_2d,
            x_min=x_min,
            y_max=y_max,
            pixel_m=pixel_m,
        )
        k = _forenkla_med_vakt(
            utjamnade,
            forenkling_m,
            seg_index,
            aktiv_2d,
            x_min=x_min,
            y_max=y_max,
            pixel_m=pixel_m,
        )
        langd = sum(
            math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(k[:-1], k[1:], strict=True)
        )
        fall = float(z_flat[celler[0]] - z_flat[celler[-1]])
        ut.append(
            Stracka(
                id=0,
                koordinater=tuple(k),
                tillrinning_m2=a_slut,
                ordning=int(strahler[h0]),
                langd_m=langd,
                lutning=max(0.0, fall / langd) if langd > 0 else 0.0,
                typ=stracka_typ,
            )
        )
    ut.sort(key=lambda s: (s.tillrinning_m2, s.koordinater))
    return [
        Stracka(i, s.koordinater, s.tillrinning_m2, s.ordning, s.langd_m, s.lutning, s.typ)
        for i, s in enumerate(ut)
    ]


def avrinningsnat_ur_falt(
    z: NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    extra_yta_m2: NDArray[np.float64] | None,
    *,
    x_min: float,
    y_max: float,
    pixel_m: float,
    min_yta_m2: float = STANDARD_MIN_YTA_M2,
    forenkling_m: float = FORENKLING_M,
    brytlinjer: Sequence[BrytlinjePost] = (),
    anvand_rust: bool | None = None,
) -> list[Stracka]:
    """Avrinningsnätet ur en höjdmodell som linjer med tillrinning och Strahler-ordning."""
    if min_yta_m2 <= 0 or not math.isfinite(min_yta_m2):
        raise ValueError("min_yta_m2 måste vara ett positivt tal.")
    flow, ordn, a = berakna_avrinningsfalt(
        z,
        aktiv,
        extra_yta_m2,
        pixel_m=pixel_m,
        x_min=x_min,
        y_max=y_max,
        brytlinjer=brytlinjer,
        anvand_rust=anvand_rust,
    )
    return strackor_fran_falt(
        z,
        flow,
        ordn,
        a,
        x_min=x_min,
        y_max=y_max,
        pixel_m=pixel_m,
        min_yta_m2=min_yta_m2,
        forenkling_m=forenkling_m,
        brytlinjer=brytlinjer,
        aktiv=aktiv,
    )


def avrinningsnat(
    graf: SankGraf,
    min_yta_m2: float = STANDARD_MIN_YTA_M2,
    *,
    brytlinjer: Sequence[BrytlinjePost] = (),
) -> list[Stracka]:
    """Avrinningsnätet för en sänkgraf (markmodell, byggnadshinder och takvatten som grafen har)."""
    geo = graf.geo
    pixel = float(geo.pixelstorlek_x)
    z = np.asarray(graf.mark_z, dtype=np.float64)
    from sankgraf.typer import MARKSLAG_BYGGNAD

    aktiv = np.asarray(graf.markslag != MARKSLAG_BYGGNAD, dtype=bool) & np.isfinite(z)
    extra = None
    if graf.takvatten is not None and not graf.takvatten.tom:
        extra = np.asarray(graf.takvatten.extra_area_m2, dtype=np.float64)
    return avrinningsnat_ur_falt(
        z,
        aktiv,
        extra,
        x_min=geo.x_min,
        y_max=geo.y_max,
        pixel_m=pixel,
        min_yta_m2=min_yta_m2,
        brytlinjer=brytlinjer,
    )
