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
    """Höjdfält (DSM) över rutan och dess grannar, och rutans eget fönster däri."""

    falt: Hojdfalt
    fonster: tuple[int, int, int, int]  # (rad0, rad1, kol0, kol1) för den egna rutan
    saknade_celler: int  # celler i mosaikens bbox utan höjddata (satta under alla hinder)
    hojdspann_m: float  # största minus minsta höjd i data (utan de saknade cellerna)
    # Samma fält men utan byggnader vars höjd är en standardgissning (`hojdkalla = "standard"`).
    # None om inga sådana byggnader finns i mosaiken. Används för att flagga resultat som
    # beror på en gissad höjd.
    falt_utan_standard: Hojdfalt | None = None
    standard_byggnader: frozenset[str] = frozenset()  # byggnad_id i hela mosaiken
    standard_byggnader_egen_ruta: int = 0  # av dem: byggnader i den egna rutans register
    vegetation: bool = False


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


_STANDARD = frozenset({"standard"})


@dataclass(frozen=True)
class _Tile:
    z: NDArray[np.float64]
    geo: Any
    z_utan_standard: NDArray[np.float64] | None
    standard_ids: frozenset[str]


def _finit(z: NDArray[Any], sokvag: Path) -> NDArray[np.float64]:
    z64 = np.asarray(z, dtype=np.float64)
    ej = int(z64.size - np.count_nonzero(np.isfinite(z64)))
    if ej:
        raise RutaFel(
            f"{sokvag} har {ej} celler som inte är ändliga tal (NaN/nodata). Dagsljus gissar inte "
            "höjd för dem; fyll dem i markmodellen."
        )
    return z64


def _lasa_tile(mapp: Path, vegetation: bool) -> _Tile:
    """Ytmodellen (DSM) för en K1 v2-ruta ur `karna.geometri.las_ytmodell`.

    K1 v1-rutor vägras: `mark.tif` anger där "takhöjd eller en fast hinderhöjd", och med en fast
    hinderhöjd är alla dagsljusmått fel i proportion mot verkligheten utan att det syns i
    resultatet. Dagsljus kräver verkliga takhöjder ur byggnadsregistret.
    """
    from karna.geometri import GeometriFel, k1_version, las_byggnader, las_ytmodell

    try:
        version = k1_version(mapp)
    except GeometriFel as exc:
        raise RutaFel(str(exc)) from exc
    if version != 2:
        raise RutaFel(
            f"Rutan {mapp.name} är K1 v1 (saknar byggnader.gpkg och terrang.tif). Dagsljus kräver "
            "verkliga takhöjder: i v1 är byggnadscellerna i mark.tif en takhöjd eller en fast "
            "hinderhöjd, och med en fast hinderhöjd blir soltimmar, SVF och VSC fel i proportion "
            "utan att det syns. Bygg om rutan med K1 v2 (markmodellen skriver byggnadsregistret)."
        )
    try:
        z, geo = las_ytmodell(mapp, vegetation=vegetation)
        poster = las_byggnader(mapp)
        standard = frozenset(b.byggnad_id for b in poster if b.hojdkalla == "standard")
        z_utan = (
            las_ytmodell(mapp, vegetation=vegetation, utan_hojdkalla=_STANDARD)[0]
            if standard
            else None
        )
    except GeometriFel as exc:
        raise RutaFel(f"Rutan {mapp.name}: {exc}") from exc
    sokvag = mapp / "terrang.tif"
    if abs(geo.pixelstorlek_x - geo.pixelstorlek_y) > _TOL:
        raise RutaFel(f"{sokvag}: icke-kvadratiska celler stöds inte.")
    return _Tile(
        _finit(z, sokvag),
        geo,
        None if z_utan is None else _finit(z_utan, sokvag),
        standard,
    )


def bygg_mosaik(
    egen: Path,
    grannar: tuple[Path, ...],
    cell_m: float,
    metod: Omsampling,
    *,
    vegetation: bool = False,
) -> Mosaik:
    """Sammanfogar `egen` (skrivs sist, så den vinner i överlapp) och `grannar` till ett fält
    med cellstorleken `cell_m`. Rutor som inte ligger på samma cellraster, och rutor som inte är
    K1 v2, ger fel."""
    tiles = [_lasa_tile(p, vegetation) for p in (*grannar, egen)]
    p0 = tiles[0].geo.pixelstorlek_x
    faktor_f = cell_m / p0
    if abs(faktor_f - round(faktor_f)) > _TOL or round(faktor_f) < 1:
        raise RutaFel(
            f"cellstorlek_m={cell_m} måste vara en heltalsmultipel av rutornas pixelstorlek {p0}."
        )
    faktor = round(faktor_f)
    for t in tiles:
        if abs(t.geo.pixelstorlek_x - p0) > _TOL:
            raise RutaFel("Rutorna har olika pixelstorlek.")
    x0 = min(t.geo.x_min for t in tiles)
    y1 = max(t.geo.y_max for t in tiles)
    x1 = max(t.geo.x_max for t in tiles)
    y0 = min(t.geo.y_min for t in tiles)
    bredd = round((x1 - x0) / cell_m)
    hojd = round((y1 - y0) / cell_m)
    if abs(bredd * cell_m - (x1 - x0)) > _TOL or abs(hojd * cell_m - (y1 - y0)) > _TOL:
        raise RutaFel("Mosaikens utsträckning är inte en heltalsmultipel av cellstorleken.")
    z = np.full((hojd, bredd), np.nan)
    har_standard = any(t.z_utan_standard is not None for t in tiles)
    z_utan = np.full((hojd, bredd), np.nan) if har_standard else None
    fonster = (0, 0, 0, 0)
    for t in tiles:
        g = t.geo
        j0f, i0f = (g.x_min - x0) / cell_m, (y1 - g.y_max) / cell_m
        j0, i0 = round(j0f), round(i0f)
        if abs(j0f - j0) > _TOL or abs(i0f - i0) > _TOL:
            raise RutaFel("En ruta ligger inte på mosaikens cellraster (förskjutning ej heltal).")
        zc = omsampla(t.z, faktor, metod)
        z[i0 : i0 + zc.shape[0], j0 : j0 + zc.shape[1]] = zc
        if z_utan is not None:
            zu = t.z if t.z_utan_standard is None else t.z_utan_standard
            z_utan[i0 : i0 + zc.shape[0], j0 : j0 + zc.shape[1]] = omsampla(zu, faktor, metod)
        fonster = (i0, i0 + zc.shape[0], j0, j0 + zc.shape[1])
    saknas = ~np.isfinite(z)
    antal_saknas = int(saknas.sum())
    spann = float(np.nanmax(z) - np.nanmin(z))
    if antal_saknas:
        under = float(np.nanmin(z)) - 1000.0  # under allt: ger inga hinder, redovisas
        z[saknas] = under
        if z_utan is not None:
            z_utan[~np.isfinite(z_utan)] = under
    return Mosaik(
        Hojdfalt(z, cell_m, x0, y1),
        fonster,
        antal_saknas,
        spann,
        None if z_utan is None else Hojdfalt(z_utan, cell_m, x0, y1),
        frozenset().union(*(t.standard_ids for t in tiles)),
        len(tiles[-1].standard_ids),
        vegetation,
    )


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
