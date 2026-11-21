"""Vegetationshöjd över mark (K1 v2 `vegetation.tif`).

Härledning ur laser: punkter i klass 3–5 (låg, medel, hög vegetation) ger högsta vegetationsnivå
per cell; vegetationshöjden är den nivån minus terrängen (`terrang.tif`, medel per cell).
Rutnätet är grövre än markrutnätet (standard 1 m; 1–2 punkter/m² bär inte 0,25 m).
En annan källa kan ersätta lasern, t.ex. Skogsstyrelsens Skogliga grunddata (trädhöjd), inläst
från en lokal fil (hämtas via `data_inlasning.LokalDataCache`, aldrig direkt härifrån).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from markmodell.rutnat import Ruta

LAS_VEGETATIONSKLASSER: tuple[int, ...] = (3, 4, 5)
VEGETATION_UPPLOSNING_M = 1.0
VEGETATION_MIN_HOJD_M = 0.5


class VegetationFel(Exception):
    """Fel vid härledning av vegetationshöjd."""


def faktor_mot_marknat(ruta: Ruta, upplosning_m: float) -> int:
    f = round(upplosning_m / ruta.upplosning_m)
    if f < 1 or abs(f * ruta.upplosning_m - upplosning_m) > 1e-9:
        raise VegetationFel(
            f"Vegetationens upplösning {upplosning_m} m måste vara en heltalsmultipel av "
            f"markrutnätets {ruta.upplosning_m} m."
        )
    if ruta.form_rc[0] % f or ruta.form_rc[1] % f:
        raise VegetationFel(f"Rutan {ruta.form_rc} är inte delbar med faktor {f}.")
    return f


def vegetation_fran_laser(
    e: NDArray[np.float64],
    n: NDArray[np.float64],
    z: NDArray[np.float64],
    klasser: NDArray[np.int32] | NDArray[np.uint8],
    terrang_z: NDArray[np.float32],
    ruta: Ruta,
    *,
    upplosning_m: float = VEGETATION_UPPLOSNING_M,
    min_hojd_m: float = VEGETATION_MIN_HOJD_M,
    bort_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.float32]:
    """Vegetationshöjd över mark (m) på `upplosning_m`-rutnätet; 0 där ingen vegetation finns.

    `bort_mask` (på markrutnätet, t.ex. byggnadsceller) nollställer celler där minst hälften
    av markcellerna är maskerade.
    """
    f = faktor_mot_marknat(ruta, upplosning_m)
    rader, kolumner = ruta.form_rc
    gr, gk = rader // f, kolumner // f
    if terrang_z.shape != (rader, kolumner):
        raise VegetationFel(
            f"terrang_z har form {terrang_z.shape}, förväntade {(rader, kolumner)}."
        )
    k = np.asarray(klasser)
    if not (k.shape == e.shape == n.shape == z.shape):
        raise VegetationFel("Laserpunkternas arrayer måste ha samma längd.")
    urval = np.isin(k, LAS_VEGETATIONSKLASSER) & np.isfinite(z)
    e_min, _, _, n_max = ruta.yttre_bbox
    kol = np.floor((e[urval] - e_min) / upplosning_m).astype(np.int64)
    rad = np.floor((n_max - n[urval]) / upplosning_m).astype(np.int64)
    inom = (kol >= 0) & (kol < gk) & (rad >= 0) & (rad < gr)
    topp = np.full((gr, gk), -np.inf, dtype=np.float64)
    np.maximum.at(topp, (rad[inom], kol[inom]), z[urval][inom])
    mark = terrang_z.astype(np.float64).reshape(gr, f, gk, f).mean(axis=(1, 3))
    hojd = np.where(np.isfinite(topp), topp - mark, 0.0)
    hojd = np.where(hojd >= min_hojd_m, hojd, 0.0)
    if bort_mask is not None:
        andel = bort_mask.reshape(gr, f, gk, f).mean(axis=(1, 3))
        hojd = np.where(andel >= 0.5, 0.0, hojd)
    return np.asarray(hojd, dtype=np.float32)
