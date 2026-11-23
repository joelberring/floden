"""Konstruktion av motstånds- och konduktansraster för en given artprofil."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ekologi.typer import ArtProfil, EkologiFel, MotstandsYta


def bygg_motstandsyta(
    markslag: NDArray[np.uint8] | NDArray[np.integer[Any]],
    profil: ArtProfil,
    *,
    cell_m: float,
    motstand_faktor: float = 1.0,
    extra_barriar_mask: NDArray[np.bool_] | None = None,
    extra_motstand: NDArray[np.float64] | NDArray[np.float32] | None = None,
) -> MotstandsYta:
    """Bygger motståndsraster `R(x, y) >= 1` och konduktansraster `g = 1 / R` i `[0, 1]`.

    `motstand_faktor > 0` skalar icke-habitatmotstånd (med klämning till >= 1.0) för
    monoton osäkerhetsanalys utan att göra habitat sämre än 1.0 eller öppna totala barriärer.
    """
    if not (math.isfinite(cell_m) and cell_m > 0.0):
        raise EkologiFel(f"cell_m måste vara ändligt och > 0, fick {cell_m}.")
    if not (math.isfinite(motstand_faktor) and motstand_faktor > 0.0):
        raise EkologiFel(f"motstand_faktor måste vara ändligt och > 0, fick {motstand_faktor}.")

    ms = np.asarray(markslag)
    if ms.ndim != 2 or ms.size == 0:
        raise EkologiFel(f"markslag måste vara ett icke-tomt 2D-raster, fick form {ms.shape}.")

    motstand = np.full(ms.shape, float(profil.standard_motstand), dtype=np.float64)
    for kod, r_val in profil.motstand_per_markslag.items():
        if r_val < 1.0 or (not math.isfinite(r_val) and r_val != math.inf):
            raise EkologiFel(f"Ogiltigt motstånd {r_val} för markslag {kod} (måste vara >= 1).")
        motstand[ms == kod] = float(r_val)

    if motstand_faktor != 1.0:
        andliga = np.isfinite(motstand) & (motstand > 1.0)
        motstand = np.where(andliga, np.maximum(1.0, motstand * float(motstand_faktor)), motstand)

    if extra_motstand is not None:
        ext = np.asarray(extra_motstand, dtype=np.float64)
        if ext.shape != ms.shape:
            raise EkologiFel(f"extra_motstand har form {ext.shape}, förväntade {ms.shape}.")
        if np.any(np.isnan(ext)) or np.any(ext < 1.0):
            raise EkologiFel("extra_motstand måste ha värden >= 1.0 (eller inf).")
        motstand = np.maximum(motstand, ext)

    if extra_barriar_mask is not None:
        bmask = np.asarray(extra_barriar_mask, dtype=np.bool_)
        if bmask.shape != ms.shape:
            raise EkologiFel(f"extra_barriar_mask har form {bmask.shape}, förväntade {ms.shape}.")
        motstand = np.where(bmask, math.inf, motstand)

    konduktans = np.where(np.isfinite(motstand), 1.0 / motstand, 0.0)
    return MotstandsYta(
        motstand=motstand,
        konduktans=konduktans,
        cell_m=float(cell_m),
        profil_id=profil.profil_id,
    )
