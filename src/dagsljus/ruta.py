"""Höjdfält ur K1-rutor: mosaik av en rutas `mark.tif` och grannrutorna, med omsampling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from dagsljus.horisont import Hojdfalt

Omsampling = Literal["medel", "max", "punkt"]
_TOL = 1e-6


class RutaFel(Exception):
    """Fel vid inläsning eller sammanfogning av K1-rutor."""


@dataclass(frozen=True)
class Mosaik:
    """Höjdfält över rutan och dess grannar, och rutans eget fönster däri."""

    falt: Hojdfalt
    fonster: tuple[int, int, int, int]  # (rad0, rad1, kol0, kol1) för den egna rutan
    saknade_celler: int  # celler i mosaikens bbox utan höjddata (satta under alla hinder)
    hojdspann_m: float  # största minus minsta höjd i data (utan de saknade cellerna)


def omsampla(z: NDArray[np.float64], faktor: int, metod: Omsampling) -> NDArray[np.float64]:
    """Grovare raster med heltalsfaktor. `punkt` tar cellens mittpunktsnärmaste cell."""
    if faktor == 1:
        return z
    h, b = z.shape
    if h % faktor or b % faktor:
        raise RutaFel(f"Rastret {h}x{b} kan inte omsamplas med faktor {faktor} (inte delbart).")
    if metod == "punkt":
        o = faktor // 2
        return np.ascontiguousarray(z[o::faktor, o::faktor])
    block = z.reshape(h // faktor, faktor, b // faktor, faktor)
    return np.asarray(block.max(axis=(1, 3)) if metod == "max" else block.mean(axis=(1, 3)))


def _lasa_tile(mapp: Path) -> tuple[NDArray[np.float64], Any]:
    from testsvit.k1_io import las_geotiff

    sokvag = mapp / "mark.tif"
    if not sokvag.is_file():
        raise RutaFel(f"Rutan saknar mark.tif: {mapp}")
    z, geo = las_geotiff(sokvag)
    z64 = np.asarray(z, dtype=np.float64)
    ej = int(z64.size - np.count_nonzero(np.isfinite(z64)))
    if ej:
        raise RutaFel(
            f"{sokvag} har {ej} celler som inte är ändliga tal (NaN/nodata). Dagsljus gissar inte "
            "höjd för dem; fyll dem i markmodellen."
        )
    if abs(geo.pixelstorlek_x - geo.pixelstorlek_y) > _TOL:
        raise RutaFel(f"{sokvag}: icke-kvadratiska celler stöds inte.")
    return z64, geo


def bygg_mosaik(egen: Path, grannar: tuple[Path, ...], cell_m: float, metod: Omsampling) -> Mosaik:
    """Sammanfogar `egen` (skrivs sist, så den vinner i överlapp) och `grannar` till ett fält
    med cellstorleken `cell_m`. Rutor som inte ligger på samma cellraster ger fel."""
    tiles = [_lasa_tile(p) for p in (*grannar, egen)]
    p0 = tiles[0][1].pixelstorlek_x
    faktor_f = cell_m / p0
    if abs(faktor_f - round(faktor_f)) > _TOL or round(faktor_f) < 1:
        raise RutaFel(
            f"cellstorlek_m={cell_m} måste vara en heltalsmultipel av rutornas pixelstorlek {p0}."
        )
    faktor = round(faktor_f)
    for _, g in tiles:
        if abs(g.pixelstorlek_x - p0) > _TOL:
            raise RutaFel("Rutorna har olika pixelstorlek.")
    x0 = min(g.x_min for _, g in tiles)
    y1 = max(g.y_max for _, g in tiles)
    x1 = max(g.x_max for _, g in tiles)
    y0 = min(g.y_min for _, g in tiles)
    bredd = round((x1 - x0) / cell_m)
    hojd = round((y1 - y0) / cell_m)
    if abs(bredd * cell_m - (x1 - x0)) > _TOL or abs(hojd * cell_m - (y1 - y0)) > _TOL:
        raise RutaFel("Mosaikens utsträckning är inte en heltalsmultipel av cellstorleken.")
    z = np.full((hojd, bredd), np.nan)
    fonster = (0, 0, 0, 0)
    for z_t, g in tiles:
        zc = omsampla(z_t, faktor, metod)
        j0f, i0f = (g.x_min - x0) / cell_m, (y1 - g.y_max) / cell_m
        j0, i0 = round(j0f), round(i0f)
        if abs(j0f - j0) > _TOL or abs(i0f - i0) > _TOL:
            raise RutaFel("En ruta ligger inte på mosaikens cellraster (förskjutning ej heltal).")
        z[i0 : i0 + zc.shape[0], j0 : j0 + zc.shape[1]] = zc
        fonster = (i0, i0 + zc.shape[0], j0, j0 + zc.shape[1])
    saknas = ~np.isfinite(z)
    antal_saknas = int(saknas.sum())
    spann = float(np.nanmax(z) - np.nanmin(z))
    if antal_saknas:
        z[saknas] = float(np.nanmin(z)) - 1000.0  # under allt: ger inga hinder, redovisas
    return Mosaik(Hojdfalt(z, cell_m, x0, y1), fonster, antal_saknas, spann)


def fonster_koordinater(mosaik: Mosaik) -> tuple[float, float]:
    """(x_min, y_max) för det egna fönstret i EPSG:3011."""
    r0, _, c0, _ = mosaik.fonster
    f = mosaik.falt
    return f.x_min_m + c0 * f.cell_m, f.y_max_m - r0 * f.cell_m


def mitt_3011(
    fonster_xy: tuple[float, float], form: tuple[int, int], cell_m: float
) -> tuple[float, float]:
    x_min, y_max = fonster_xy
    return x_min + form[1] * cell_m / 2.0, y_max - form[0] * cell_m / 2.0
