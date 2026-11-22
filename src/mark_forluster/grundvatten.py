"""Grundvattentak, djup-till-vatten (DTW) och klassning av in-/utströmningsområden."""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from mark_forluster.parametrar import ForlustFel

INSTROMNING = 1
UTSTROMNING = 2

GILTIGA_GRUNDVATTENLAGEN: frozenset[str] = frozenset({"normal", "hog"})


@dataclass(frozen=True)
class ObservationsRor:
    """Ett grundvattenrör i SWEREF 99 18 00 (EPSG:3011) med uppmätt nivå i RH 2000."""

    ror_id: str
    e_m: float
    n_m: float
    grundvatten_rh2000: float

    def __post_init__(self) -> None:
        if not isinstance(self.ror_id, str) or not self.ror_id.strip():
            raise ForlustFel("ror_id får inte vara tomt.")
        for falt, varde in (
            ("e_m", self.e_m),
            ("n_m", self.n_m),
            ("grundvatten_rh2000", self.grundvatten_rh2000),
        ):
            if not math.isfinite(varde):
                raise ForlustFel(f"ObservationsRor.{falt} måste vara ändligt, fick {varde}.")


def berakna_grundvattentak(
    dtw_m: NDArray[np.float32] | NDArray[np.float64] | float,
    porositet: NDArray[np.float32] | NDArray[np.float64] | float,
    *,
    lage: str = "normal",
    hog_faktor: float = 0.5,
    fukthalt: NDArray[np.float32] | NDArray[np.float64] | float = 0.0,
) -> NDArray[np.float32]:
    """Beräknar tillgänglig marklagringskapacitet (grundvattentak) i meter.

    Tillgänglig lagring = `max(0, porositet - fukthalt) * justerad_dtw_m`, där
    `justerad_dtw_m` vid `lage="hog"` multipliceras med `hog_faktor` (t.ex. 0.5).
    Utan angiven `fukthalt` (0.0) ger formeln `porositet * justerad_dtw_m`.
    """
    norm_lage = lage.strip().lower()
    if norm_lage not in GILTIGA_GRUNDVATTENLAGEN:
        raise ForlustFel(
            f"Ogiltigt grundvattenläge '{lage}'. Giltiga: 'normal' eller 'hog'."
        )
    if not math.isfinite(hog_faktor) or not (0.0 <= hog_faktor <= 1.0):
        raise ForlustFel(f"hog_faktor måste ligga i [0, 1], fick {hog_faktor}.")

    dtw_arr = np.asarray(dtw_m, dtype=np.float64)
    por_arr = np.asarray(porositet, dtype=np.float64)
    fukt_arr = np.asarray(fukthalt, dtype=np.float64)

    if not np.all(np.isfinite(dtw_arr)):
        raise ForlustFel("dtw_m får inte innehålla NaN eller oändliga värden.")
    if not np.all(np.isfinite(por_arr)) or np.any(por_arr < 0.0) or np.any(por_arr > 1.0):
        raise ForlustFel("porositet måste ligga i [0, 1] utan NaN/inf.")
    if not np.all(np.isfinite(fukt_arr)) or np.any(fukt_arr < 0.0) or np.any(fukt_arr > 1.0):
        raise ForlustFel("fukthalt måste ligga i [0, 1] utan NaN/inf.")

    justerad_dtw = np.maximum(0.0, dtw_arr)
    if norm_lage == "hog":
        justerad_dtw = justerad_dtw * float(hog_faktor)

    dranerbar_porositet = np.maximum(0.0, por_arr - fukt_arr)
    tak = np.maximum(0.0, dranerbar_porositet * justerad_dtw)
    return np.asarray(tak, dtype=np.float32)


def klassificera_in_utstromning(
    dtw_m: NDArray[np.float32] | NDArray[np.float64],
    utstromning_grans_m: float = 0.5,
) -> NDArray[np.uint8]:
    """Klassificerar raster i inströmnings- (1) och utströmningsområden (2).

    Celler med `dtw_m <= utstromning_grans_m` klassas som utströmningsområde (2),
    övriga som inströmningsområde (1).
    """
    if not math.isfinite(utstromning_grans_m) or utstromning_grans_m < 0.0:
        raise ForlustFel(
            f"utstromning_grans_m måste vara ändlig och >= 0, fick {utstromning_grans_m}."
        )
    dtw_arr = np.asarray(dtw_m, dtype=np.float64)
    if dtw_arr.ndim != 2:
        raise ForlustFel(f"dtw_m måste vara ett 2D-raster, fick form {dtw_arr.shape}.")
    if not np.all(np.isfinite(dtw_arr)):
        raise ForlustFel("dtw_m får inte innehålla NaN eller oändliga värden.")

    karta = np.where(dtw_arr <= utstromning_grans_m, UTSTROMNING, INSTROMNING)
    return np.asarray(karta, dtype=np.uint8)


def uppskatta_dtw_fran_terrang(
    mark_z: NDArray[np.float32] | NDArray[np.float64],
    vatten_mask: NDArray[np.bool_],
    pixelstorlek_m: float = 0.25,
) -> NDArray[np.float32]:
    """Deterministisk HAND-liknande DTW-approximation (höjd över närmaste vattenyta).

    Från varje vattencell (`vatten_mask == True`) propageras dräneringsnivån utåt
    längs stigande terräng (minsta topografiska barriär / närmaste hydrologiska
    vattenspegel). Där terrängen sjunker under närmaste dräneringsnivå begränsas
    DTW till 0.0 m.
    """
    z = np.asarray(mark_z, dtype=np.float64)
    mask = np.asarray(vatten_mask, dtype=bool)
    if z.ndim != 2 or mask.shape != z.shape:
        raise ForlustFel(
            f"mark_z ({z.shape}) och vatten_mask ({mask.shape}) måste vara 2D med samma form."
        )
    if not np.all(np.isfinite(z)):
        raise ForlustFel("mark_z får inte innehålla NaN eller oändliga värden.")
    if not math.isfinite(pixelstorlek_m) or pixelstorlek_m <= 0.0:
        raise ForlustFel(f"pixelstorlek_m måste vara > 0, fick {pixelstorlek_m}.")
    if not np.any(mask):
        raise ForlustFel("vatten_mask måste innehålla minst en vattencell som referensnivå.")

    rader, kolumner = z.shape
    avstand = np.full((rader, kolumner), math.inf, dtype=np.float64)
    ref_z = np.zeros((rader, kolumner), dtype=np.float64)
    ko: list[tuple[float, int, int, float]] = []

    vatten_rader, vatten_kols = np.nonzero(mask)
    for r_np, c_np in zip(vatten_rader, vatten_kols, strict=True):
        r = int(r_np)
        c = int(c_np)
        avstand[r, c] = 0.0
        ref_z[r, c] = float(z[r, c])
        heapq.heappush(ko, (0.0, r, c, float(z[r, c])))

    grannar = (
        (-1, 0, pixelstorlek_m),
        (1, 0, pixelstorlek_m),
        (0, -1, pixelstorlek_m),
        (0, 1, pixelstorlek_m),
        (-1, -1, pixelstorlek_m * math.sqrt(2.0)),
        (-1, 1, pixelstorlek_m * math.sqrt(2.0)),
        (1, -1, pixelstorlek_m * math.sqrt(2.0)),
        (1, 1, pixelstorlek_m * math.sqrt(2.0)),
    )

    while ko:
        dist, r, c, bas_z = heapq.heappop(ko)
        if dist > avstand[r, c]:
            continue
        for dr, dc, steg_m in grannar:
            nr = r + dr
            nc = c + dc
            if not (0 <= nr < rader and 0 <= nc < kolumner):
                continue
            # Preferera flöde nedför sluttning; straffa uppförsbacke hydrologiskt
            hojd_diff = max(0.0, float(z[r, c]) - float(z[nr, nc]))
            ny_dist = dist + steg_m + 5.0 * hojd_diff
            if ny_dist < avstand[nr, nc]:
                avstand[nr, nc] = ny_dist
                ref_z[nr, nc] = bas_z
                heapq.heappush(ko, (ny_dist, nr, nc, bas_z))

    dtw = np.maximum(0.0, z - ref_z)
    dtw[mask] = 0.0
    return np.asarray(dtw, dtype=np.float32)


def kalibrera_dtw_mot_ror(
    dtw_estimat_m: NDArray[np.float32] | NDArray[np.float64],
    mark_z: NDArray[np.float32] | NDArray[np.float64],
    ror: Sequence[ObservationsRor],
    e_min_m: float,
    n_max_m: float,
    pixelstorlek_m: float = 0.25,
) -> tuple[NDArray[np.float32], float]:
    """Kalibrerar uppskattad DTW mot observationsrör genom deterministisk median-offset.

    Returnerar `(kalibrerad_dtw_m, offset_m)` där positiva `offset_m` innebär att
    observationsrören visar större djup till grundvattenytan än det okalibrerade
    estimatet (median av `uppmatt_dtw - estimat_dtw`).
    """
    dtw = np.asarray(dtw_estimat_m, dtype=np.float64)
    z = np.asarray(mark_z, dtype=np.float64)
    if dtw.ndim != 2 or z.shape != dtw.shape:
        raise ForlustFel("dtw_estimat_m och mark_z måste vara 2D-raster med samma form.")
    if not np.all(np.isfinite(dtw)) or not np.all(np.isfinite(z)):
        raise ForlustFel("dtw_estimat_m och mark_z får inte innehålla NaN/inf.")
    if not ror:
        raise ForlustFel("Minst ett observationsrör krävs för kalibrering.")
    if not math.isfinite(pixelstorlek_m) or pixelstorlek_m <= 0.0:
        raise ForlustFel(f"pixelstorlek_m måste vara > 0, fick {pixelstorlek_m}.")

    rader, kolumner = dtw.shape
    avvikelser: list[float] = []
    for obs in ror:
        kol = int(math.floor((obs.e_m - e_min_m) / pixelstorlek_m))
        rad = int(math.floor((n_max_m - obs.n_m) / pixelstorlek_m))
        if not (0 <= rad < rader and 0 <= kol < kolumner):
            raise ForlustFel(
                f"Observationsrör '{obs.ror_id}' ligger utanför rastret "
                f"(rad={rad}, kol={kol})."
            )
        uppmatt_dtw = max(0.0, float(z[rad, kol]) - obs.grundvatten_rh2000)
        avvikelser.append(uppmatt_dtw - float(dtw[rad, kol]))

    offset_m = float(np.median(np.asarray(avvikelser, dtype=np.float64)))
    kalibrerad = np.maximum(0.0, dtw + offset_m)
    return np.asarray(kalibrerad, dtype=np.float32), offset_m
