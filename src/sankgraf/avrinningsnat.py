"""Avrinningsnätet: flödesvägar som ett dendritiskt träd (rännilar som går ihop till stråk).

Metod (alla steg deterministiska, inget slumptal):

1. **Sänkfyllning** med prioritetsfyllning (Barnes m.fl., "Priority-Flood") över aktiva markceller.
   Rutans kant är utlopp (som sänkgrafens `HAV`); byggnader är hinder. Slutna områden som byggnader
   stänger in (sänkgrafens slutsänkor) får sin lägsta cell som utlopp. Vattnet går därmed över
   den lägsta tröskeln ur varje sänka, samma sak som sänkgrafens överfall, och flacka ytor och
   fyllda sänkor får en riktning mot utloppet i stället för att sluta blint.
2. **D8-flödesriktning** (brantast nedåt, diagonal viktad med avstånd) på den fyllda ytan; på plana
   ytor följs fyllningens väg mot utloppet.
3. **Tillrinningsyta** per cell (m²): cellens egen yta plus takvatten som sänkgrafen leder till
   cellen (`graf.takvatten`, körningens takantagande; utan takvatten räknas inte taken alls),
   summerad nedströms.
4. **Stråk**: celler med tillrinning över tröskeln, men bara där flödet samlas. En lutande yta ger
   i D8 parallella banor som alla växer med avståndet; det är bladflöde och inte ett stråk. En
   cell räknas som stråk först när dess tillrinning är minst dubbelt så stor som medelvärdet i
   7×7 celler runt den; nedströms ett stråk fortsätter det så länge tröskeln är nådd.
5. **Linjer** från källa (eller sammanflöde) till nästa sammanflöde, med Strahler-ordning, jämnade
   (Chaikin) och förenklade (Douglas-Peucker).

Takvatten: byggnadscellerna är hinder; deras takyta räknas in i den markcell sänkgrafen leder
den till (`TakvattenFordelning.extra_area_m2`).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import uniform_filter
from shapely.geometry import LineString

from sankgraf.typer import SankGraf

STANDARD_MIN_YTA_M2 = 500.0
FORENKLING_M = 0.5
KONCENTRATION_FONSTER = 7
KONCENTRATION_FAKTOR = 2.0

_GRANNAR: tuple[tuple[int, int], ...] = (
    (-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1),
)  # fmt: skip


@dataclass(frozen=True)
class Stracka:
    """En rännil/ett stråk från källa eller sammanflöde till nästa sammanflöde (SWEREF 99 18 00)."""

    id: int
    koordinater: tuple[tuple[float, float], ...]
    tillrinning_m2: float
    ordning: int
    langd_m: float
    lutning: float


def fyll_och_riktning(
    z: NDArray[np.float64], aktiv: NDArray[np.bool_]
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Prioritetsfyllning och D8. Returnerar `(flow_to, uppstroms_ordning)`.

    `flow_to` är platt cellindex nedströms, -1 för utlopp (rutkant eller lägsta cell i ett slutet
    område) och -2 för inaktiva celler. `uppstroms_ordning` listar de aktiva cellerna så att
    varje cell kommer före sin mottagare i omvänd ordning (bearbetas bakifrån för att summera)."""
    h, b = z.shape
    n = h * b
    zf = np.where(aktiv, z, np.inf).ravel().copy()
    akt = aktiv.ravel()
    parent = np.full(n, -2, dtype=np.int64)
    klar = ~akt.copy()
    ko: list[tuple[float, int, int]] = []
    rakn = 0
    idx = np.arange(n).reshape(h, b)
    kant = np.zeros((h, b), dtype=bool)
    kant[0, :] = kant[-1, :] = kant[:, 0] = kant[:, -1] = True
    for u in idx[kant & aktiv].tolist():
        klar[u] = True
        parent[u] = -1
        heapq.heappush(ko, (float(zf[u]), rakn, u))
        rakn += 1
    ordning: list[int] = []
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
        ok = aktiv[rs, cs] & aktiv[rv, cv] & (fall > basta[rs, cs] + 1e-12)
        basta[rs, cs] = np.where(ok, fall, basta[rs, cs])
        mal[rs, cs] = np.where(ok, idx[rv, cv], mal[rs, cs])
    flow = mal.ravel().copy()
    platt = akt & (flow < 0)
    flow[platt] = parent[platt]
    flow[~akt] = -2
    flow[(kant.ravel()) & akt] = -1  # rutkanten är utlopp: vattnet rinner inte längs kanten
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


def _chaikin(p: list[tuple[float, float]], varv: int = 2) -> list[tuple[float, float]]:
    for _ in range(varv):
        if len(p) < 3:
            break
        ny = [p[0]]
        for (x0, y0), (x1, y1) in zip(p[:-1], p[1:], strict=True):
            ny.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            ny.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        ny.append(p[-1])
        p = ny
    return p


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
) -> list[Stracka]:
    """Avrinningsnätet ur en höjdmodell som linjer med tillrinning och Strahler-ordning."""
    if min_yta_m2 <= 0 or not math.isfinite(min_yta_m2):
        raise ValueError("min_yta_m2 måste vara ett positivt tal.")
    h, b = z.shape
    flow, ordn = fyll_och_riktning(z, aktiv)
    egen = np.where(aktiv, pixel_m * pixel_m, 0.0)
    if extra_yta_m2 is not None:
        egen = egen + np.where(aktiv, extra_yta_m2, 0.0)
    a = tillrinning(flow, ordn, egen)
    a2 = a.reshape(h, b)
    medel = uniform_filter(a2, size=KONCENTRATION_FONSTER, mode="constant")
    samlar = (a2 >= min_yta_m2) & (a2 >= KONCENTRATION_FAKTOR * medel)
    # Ett stråk fortsätter nedströms så länge tröskeln är nådd.
    strak = np.zeros(h * b, dtype=bool)
    sf = samlar.ravel()
    af = a
    fl = flow.tolist()
    # ordn är nedströms först (pop-ordning): gå baklänges för uppströms först
    for u in ordn[::-1].tolist():
        if sf[u]:
            strak[u] = True
        if strak[u]:
            v = fl[u]
            if v >= 0 and af[v] >= min_yta_m2:
                strak[v] = True
    # Inflöden av stråkceller
    inflod = np.zeros(h * b, dtype=np.int64)
    for u in np.flatnonzero(strak).tolist():
        v = fl[u]
        if v >= 0 and strak[v]:
            inflod[v] += 1
    # Strahler: uppströms först
    strahler = np.zeros(h * b, dtype=np.int64)
    max_in = np.zeros(h * b, dtype=np.int64)
    antal_max = np.zeros(h * b, dtype=np.int64)
    for u in ordn[::-1].tolist():
        if not strak[u]:
            continue
        if inflod[u] == 0:
            strahler[u] = 1
        else:
            strahler[u] = max_in[u] + (1 if antal_max[u] >= 2 else 0)
        v = fl[u]
        if v >= 0 and strak[v]:
            s = strahler[u]
            if s > max_in[v]:
                max_in[v], antal_max[v] = s, 1
            elif s == max_in[v]:
                antal_max[v] += 1

    def xy(c: int) -> tuple[float, float]:
        r, k = divmod(c, b)
        return (x_min + (k + 0.5) * pixel_m, y_max - (r + 0.5) * pixel_m)

    z_flat = np.asarray(z, dtype=np.float64).ravel()
    huvuden = [u for u in np.flatnonzero(strak).tolist() if inflod[u] != 1]
    ut: list[Stracka] = []
    for h0 in huvuden:
        celler = [h0]
        c = h0
        while True:
            v = fl[c]
            if v < 0 or not strak[v]:
                break
            celler.append(v)
            if inflod[v] >= 2:
                break
            c = v
        if len(celler) < 2:
            continue
        slut_ar_sammanflode = inflod[celler[-1]] >= 2
        a_slut = float(af[celler[-2] if slut_ar_sammanflode else celler[-1]])
        pts = _chaikin([xy(q) for q in celler])
        lin = LineString(pts).simplify(forenkling_m, preserve_topology=False)
        k = [(float(px), float(py)) for px, py in lin.coords]
        langd = float(lin.length)
        fall = float(z_flat[celler[0]] - z_flat[celler[-1]])
        ut.append(
            Stracka(
                id=0,
                koordinater=tuple(k),
                tillrinning_m2=a_slut,
                ordning=int(strahler[h0]),
                langd_m=langd,
                lutning=max(0.0, fall / langd) if langd > 0 else 0.0,
            )
        )
    ut.sort(key=lambda s: (s.tillrinning_m2, s.koordinater))
    return [Stracka(i, s.koordinater, s.tillrinning_m2, s.ordning, s.langd_m, s.lutning)
            for i, s in enumerate(ut)]  # fmt: skip


def avrinningsnat(graf: SankGraf, min_yta_m2: float = STANDARD_MIN_YTA_M2) -> list[Stracka]:
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
        z, aktiv, extra, x_min=geo.x_min, y_max=geo.y_max, pixel_m=pixel, min_yta_m2=min_yta_m2
    )
