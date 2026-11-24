"""Extraktion av sammanhängande habitatfläckar ur markslag och ev. krontakshöjd."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from ekologi.typer import ArtProfil, EkologiFel, HabitatFlack, HabitatKarta


def extrahera_habitatflackar(
    markslag: NDArray[np.uint8] | NDArray[np.integer[Any]],
    profil: ArtProfil,
    *,
    cell_m: float,
    x_min_m: float = 0.0,
    y_max_m: float = 0.0,
    kronhojd_m: NDArray[np.float64] | NDArray[np.float32] | None = None,
    kvalitet: NDArray[np.float64] | NDArray[np.float32] | None = None,
    undantag_min_area: NDArray[np.bool_] | None = None,
    grannskap: int = 8,
) -> HabitatKarta:
    """Identifierar sammanhängande habitatfläckar med area >= `profil.min_area_m2`.

    `undantag_min_area` (bool, samma form som `markslag`) pekar ut celler som är noder i sig
    (inventerade träd): en fläck som innehåller en sådan habitatcell behåller sin plats även om
    den är mindre än minsta area.

    Returnerar ett `HabitatKarta`-objekt där varje godkänd fläck har ett unikt positivt
    `flack_id` (1..m) i deterministisk ordning.
    """
    if not (math.isfinite(cell_m) and cell_m > 0.0):
        raise EkologiFel(f"cell_m måste vara ändligt och > 0, fick {cell_m}.")
    if grannskap not in (4, 8):
        raise EkologiFel(f"grannskap måste vara 4 eller 8, fick {grannskap}.")

    ms = np.asarray(markslag)
    if ms.ndim != 2 or ms.size == 0:
        raise EkologiFel(f"markslag måste vara ett icke-tomt 2D-raster, fick form {ms.shape}.")
    ny, nx = ms.shape
    cell_area_m2 = float(cell_m) * float(cell_m)
    analys_area_m2 = float(ny * nx) * cell_area_m2

    hab_mask = np.isin(ms, list(profil.habitat_markslag))
    if kronhojd_m is not None and profil.min_kronhojd_m > 0.0:
        chm = np.asarray(kronhojd_m, dtype=np.float64)
        if chm.shape != ms.shape:
            raise EkologiFel(f"kronhojd_m har form {chm.shape}, förväntade {ms.shape}.")
        hab_mask = hab_mask & np.isfinite(chm) & (chm >= profil.min_kronhojd_m)

    if kvalitet is not None:
        kval = np.asarray(kvalitet, dtype=np.float64)
        if kval.shape != ms.shape:
            raise EkologiFel(f"kvalitet har form {kval.shape}, förväntade {ms.shape}.")
        if np.any(~np.isfinite(kval)) or np.any(kval < 0.0) or np.any(kval > 1.0):
            raise EkologiFel("kvalitet måste ha ändliga värden i [0, 1].")
    else:
        kval = np.ones(ms.shape, dtype=np.float64)

    undantag: NDArray[np.bool_] | None = None
    if undantag_min_area is not None:
        undantag = np.asarray(undantag_min_area, dtype=bool)
        if undantag.shape != ms.shape:
            raise EkologiFel(f"undantag_min_area har form {undantag.shape}, förväntade {ms.shape}.")

    struktur = np.ones((3, 3), dtype=np.int32) if grannskap == 8 else np.array(
        [[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.int32
    )
    etiketter_ra, antal_ra = ndimage.label(hab_mask, structure=struktur)

    flack_raster = np.zeros(ms.shape, dtype=np.int32)
    flackar: list[HabitatFlack] = []
    nasta_id = 1
    for ra_id in range(1, int(antal_ra) + 1):
        rader, kols = np.nonzero(etiketter_ra == ra_id)
        antal_celler = int(rader.size)
        area_m2 = antal_celler * cell_area_m2
        eff_area_m2 = float(np.sum(kval[rader, kols])) * cell_area_m2
        if eff_area_m2 + 1e-9 < profil.min_area_m2 and not (
            undantag is not None and bool(undantag[rader, kols].any())
        ):
            continue
        flack_raster[rader, kols] = nasta_id
        c_rad = float(np.mean(rader))
        c_kol = float(np.mean(kols))
        c_e = float(x_min_m) + (c_kol + 0.5) * float(cell_m)
        c_n = float(y_max_m) - (c_rad + 0.5) * float(cell_m)
        flackar.append(
            HabitatFlack(
                flack_id=nasta_id,
                antal_celler=antal_celler,
                area_m2=area_m2,
                effektiv_area_m2=eff_area_m2,
                centroid_rad=c_rad,
                centroid_kol=c_kol,
                centroid_e_m=c_e,
                centroid_n_m=c_n,
            )
        )
        nasta_id += 1

    return HabitatKarta(
        flack_raster=flack_raster,
        flackar=tuple(flackar),
        cell_m=float(cell_m),
        analys_area_m2=analys_area_m2,
    )
