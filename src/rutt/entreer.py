"""Analys av entréers nåbarhet via farbar gångväg på raster (T07 steg 4, K8)."""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from rutt.parametrar import RuttParametrarFel, RuttParametrarK7, validera_parametrar

STATUS_NABAR = "nabar"
STATUS_LANGRE_GANGVAG = "langre_gangvag"
STATUS_EJ_NABAR = "ej_nabar"

_GRANNAR_8: tuple[tuple[int, int, float], ...] = (
    (-1, 0, 1.0),
    (1, 0, 1.0),
    (0, -1, 1.0),
    (0, 1, 1.0),
    (-1, -1, math.sqrt(2.0)),
    (-1, 1, math.sqrt(2.0)),
    (1, -1, math.sqrt(2.0)),
    (1, 1, math.sqrt(2.0)),
)
_GRANNAR_4: tuple[tuple[int, int, float], ...] = (
    (-1, 0, 1.0),
    (1, 0, 1.0),
    (0, -1, 1.0),
    (0, 1, 1.0),
)


class EntreAnalysFel(Exception):
    """Fel vid analys av entrénåbarhet på raster."""


@dataclass(frozen=True)
class EntreNabarhetPost:
    """En rad i K8-tabellen `entreer_nabarhet.parquet`."""

    entre_id: str
    t_s: float
    status: str
    gangvag_m: float

    def till_dict(self) -> dict[str, str | float]:
        """Konverterar posten till K8/v1-lexikon."""
        return {
            "entre_id": self.entre_id,
            "t_s": float(self.t_s),
            "status": self.status,
            "gangvag_m": float(self.gangvag_m),
        }


def _dijkstra_gangvag(
    start: tuple[int, int],
    malset: set[tuple[int, int]],
    farbar_mask: NDArray[np.bool_],
    pixelstorlek_m: float,
    max_avstand_m: float,
    grannar: tuple[tuple[int, int, float], ...],
) -> float | None:
    """Returnerar kortaste gångavstånd (m) inom `max_avstand_m` på `farbar_mask`."""
    hojd, bredd = farbar_mask.shape
    sr, sc = start
    if not farbar_mask[sr, sc]:
        return None
    if start in malset:
        return 0.0

    avstand: dict[tuple[int, int], float] = {start: 0.0}
    ko: list[tuple[float, tuple[int, int]]] = [(0.0, start)]

    while ko:
        dist, pos = heapq.heappop(ko)
        if dist > avstand.get(pos, math.inf):
            continue
        if dist > max_avstand_m + 1e-9:
            return None
        if pos in malset:
            return float(dist)

        r, c = pos
        for dr, dc, steg_faktor in grannar:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < hojd and 0 <= nc < bredd):
                continue
            if not farbar_mask[nr, nc]:
                continue
            ny_dist = dist + steg_faktor * pixelstorlek_m
            if ny_dist > max_avstand_m + 1e-9:
                continue
            nxt = (nr, nc)
            if ny_dist < avstand.get(nxt, math.inf):
                avstand[nxt] = ny_dist
                heapq.heappush(ko, (ny_dist, nxt))

    return None


def analysera_entre_nabarhet(
    entre_id: str,
    entre_cell: tuple[int, int],
    uppstallning_celler: Sequence[tuple[int, int]],
    djup_m: NDArray[np.float32] | NDArray[np.float64],
    fara_m2_s: NDArray[np.float32] | NDArray[np.float64],
    pixelstorlek_m: float,
    parametrar: RuttParametrarK7,
    *,
    t_s: float = 0.0,
    skarp: bool = True,
    tillat_testvarden: bool = False,
    attagrannskap: bool = True,
    torr_tolerans_faktor: float = 1.05,
) -> EntreNabarhetPost:
    """Analyserar entréns nåbarhet från framkomlig uppställningsplats via gångväg.

    Status enligt K8/v1:
    - `"nabar"`: Entrédjup < `entre_gransdjup_m` och gångväg finns inom
      `gangavstand_max_m` utan omväg utöver den torra gångvägen
      (inom `torr_tolerans_faktor * torr_gangvag_m + 0.5 * pixelstorlek_m`).
    - `"langre_gangvag"`: Entrédjup < `entre_gransdjup_m` och farbar gångväg
      finns inom `gangavstand_max_m`, men längre än torr gångväg.
    - `"ej_nabar"`: Entrédjup >= `entre_gransdjup_m`, inga giltiga
      uppställningsplatser, eller ingen farbar gångväg inom `gangavstand_max_m`.
    """
    validera_parametrar(
        parametrar,
        skarp=skarp,
        tillat_testvarden=tillat_testvarden,
    )
    if (
        parametrar.entre_gransdjup_m is None
        or parametrar.gangavstand_max_m is None
        or parametrar.gangvag_djup_max_m is None
        or parametrar.gangvag_fara_max_m2_s is None
    ):
        raise RuttParametrarFel("Entré- och gångvägsparametrar saknas i K7.")

    djup_arr = np.asarray(djup_m, dtype=np.float64)
    fara_arr = np.asarray(fara_m2_s, dtype=np.float64)
    if djup_arr.ndim != 2 or djup_arr.shape != fara_arr.shape:
        raise EntreAnalysFel(
            f"djup_m {djup_arr.shape} och fara_m2_s {fara_arr.shape} måste vara 2D med samma form."
        )
    if not math.isfinite(pixelstorlek_m) or pixelstorlek_m <= 0.0:
        raise EntreAnalysFel(f"Ogiltig pixelstorlek: {pixelstorlek_m}")

    hojd, bredd = djup_arr.shape
    er, ec = entre_cell
    if not (0 <= er < hojd and 0 <= ec < bredd):
        raise EntreAnalysFel(f"Entrécell {entre_cell} ligger utanför rastret {(hojd, bredd)}.")

    giltiga_uppstallning: set[tuple[int, int]] = set()
    for ur, uc in uppstallning_celler:
        if 0 <= ur < hojd and 0 <= uc < bredd:
            giltiga_uppstallning.add((ur, uc))

    if not giltiga_uppstallning:
        return EntreNabarhetPost(
            entre_id=entre_id,
            t_s=float(t_s),
            status=STATUS_EJ_NABAR,
            gangvag_m=math.inf,
        )

    entre_djup = float(djup_arr[er, ec])
    if not math.isfinite(entre_djup) or entre_djup >= parametrar.entre_gransdjup_m:
        return EntreNabarhetPost(
            entre_id=entre_id,
            t_s=float(t_s),
            status=STATUS_EJ_NABAR,
            gangvag_m=math.inf,
        )

    grannar = _GRANNAR_8 if attagrannskap else _GRANNAR_4
    max_avstand = float(parametrar.gangavstand_max_m)

    # Torr referensväg (alla celler med ändlig data antas torra)
    torr_mask = np.isfinite(djup_arr) & np.isfinite(fara_arr)
    torr_dist = _dijkstra_gangvag(
        entre_cell,
        giltiga_uppstallning,
        torr_mask,
        float(pixelstorlek_m),
        max_avstand,
        grannar,
    )

    farbar_mask = (
        np.isfinite(djup_arr)
        & np.isfinite(fara_arr)
        & (djup_arr <= float(parametrar.gangvag_djup_max_m))
        & (fara_arr <= float(parametrar.gangvag_fara_max_m2_s))
    )
    blott_dist = _dijkstra_gangvag(
        entre_cell,
        giltiga_uppstallning,
        farbar_mask,
        float(pixelstorlek_m),
        max_avstand,
        grannar,
    )

    if blott_dist is None:
        return EntreNabarhetPost(
            entre_id=entre_id,
            t_s=float(t_s),
            status=STATUS_EJ_NABAR,
            gangvag_m=math.inf,
        )

    if torr_dist is None or blott_dist > (
        torr_dist * float(torr_tolerans_faktor) + 0.5 * float(pixelstorlek_m)
    ):
        status = STATUS_LANGRE_GANGVAG
    else:
        status = STATUS_NABAR

    return EntreNabarhetPost(
        entre_id=entre_id,
        t_s=float(t_s),
        status=status,
        gangvag_m=float(blott_dist),
    )
