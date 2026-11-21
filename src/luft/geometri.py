# SPDX-License-Identifier: MIT
"""Geometri för luftdelen: höjdraster → roterat `Voxelgrid`, koordinattransformer och källor.

Koordinatsystem
---------------
* **SWEREF 99 18 00 (EPSG:3011)**: x = öst (E), y = norr (N). Indataraster är
  nordorienterade som en GeoTIFF: rad `r` ökar söderut, kolumn `c` österut,
  `(raster_origo_x_m, raster_origo_y_m)` är rastrets *övre vänstra* hörn.
  Cell `(r, c)` har centrum i
  ``x = origo_x + (c + 0,5)·dx``, ``y = origo_y − (r + 0,5)·dx``.
* **Roterat system** (x', y'): x' pekar medvinds. Meteorologisk vindriktning θ är
  riktningen vinden kommer *ifrån* (medurs från norr). Vinden blåser då mot

      e_x' = (−sin θ, −cos θ)          (öst, norr)

  och den högerhänta sidoaxeln är e_x' vriden 90° moturs:

      e_y' = ( cos θ, −sin θ).

  Med rotationscentrum C (SWEREF) är

      [x']   [Cx]   [−sin θ  −cos θ] [x − Cx]
      [y'] = [Cy] + [ cos θ  −sin θ] [y − Cy]

  och omvänt ``p = C + Rᵀ (p' − C)`` (R är ortogonal). Vid θ = 270° (västvind) är
  R identiteten, så det roterade systemet sammanfaller med SWEREF. Vid
  θ = 0° pekar x' söderut, vid 90° västerut och vid 180° norrut.
  För multiplar av 90° används exakta värden på sin/cos (0, ±1) så att
  rutnäten blir exakta permutationer av rastret.
* **Rutnätsindex** `(i, j, k)`: cell `(i, j)` har centrum i
  ``x' = origo_x + (i + 0,5)·dx``, ``y' = origo_y + (j + 0,5)·dx`` och lager `k`
  har centrum ``(k + 0,5)·dx`` över markplanet. `till_sweref`/`fran_sweref`
  arbetar med kontinuerliga index där heltal = cellcentrum.

Källor
------
`linjekalla` delar varje linjesegment exakt där det korsar cellgränser i det
roterade rutnätet (inte genom approximativ översampling) och lägger
``delängd · emission`` i den cell där delen ligger. Summan blir därför exakt
``Σ längd_inne · emission`` (upp till avrundning). Delar utanför domänen och
källor som hamnar i fast cell rapporteras i `Kallrapport` och som
`LuftVarning` – inga tysta fel.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from luft.typer import LuftFel, Voxelgrid

EPSG_SWEREF99_1800 = 3011

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

_TOL_INDEX = 1e-9


class LuftVarning(UserWarning):
    """Varning från luftdelen: resultatet är giltigt men indata har justerats."""


@dataclass(frozen=True)
class RasterRuta:
    """Nordorienterat raster i EPSG:3011 (övre vänstra hörnet + cellstorlek)."""

    origo_x_m: float  # västra kanten
    origo_y_m: float  # norra kanten
    dx_m: float
    rader: int
    kolumner: int
    epsg: int = EPSG_SWEREF99_1800

    def __post_init__(self) -> None:
        if self.dx_m <= 0:
            raise LuftFel("RasterRuta.dx_m måste vara positiv.")
        if self.rader <= 0 or self.kolumner <= 0:
            raise LuftFel("RasterRuta måste ha minst en rad och en kolumn.")

    @property
    def form_rc(self) -> tuple[int, int]:
        return self.rader, self.kolumner

    @property
    def utbredning(self) -> tuple[float, float, float, float]:
        """(x_min, y_min, x_max, y_max) i meter."""
        return (
            self.origo_x_m,
            self.origo_y_m - self.rader * self.dx_m,
            self.origo_x_m + self.kolumner * self.dx_m,
            self.origo_y_m,
        )

    def cellcentrum_m(self) -> tuple[FloatArray, FloatArray]:
        """x- och y-koordinater för alla cellcentrum, form (rader, kolumner)."""
        c = np.arange(self.kolumner, dtype=np.float64)
        r = np.arange(self.rader, dtype=np.float64)
        x = self.origo_x_m + (c + 0.5) * self.dx_m
        y = self.origo_y_m - (r + 0.5) * self.dx_m
        xx, yy = np.meshgrid(x, y)
        return xx, yy

    def gdal_transform(self) -> tuple[float, float, float, float, float, float]:
        """Affin transform i rasterio/Affine-ordning (a, b, c, d, e, f)."""
        return (self.dx_m, 0.0, self.origo_x_m, 0.0, -self.dx_m, self.origo_y_m)


@dataclass
class Kallrapport:
    """Redovisning av hur en källa fördelats på rutnätet."""

    total_g_s: float = 0.0  # angiven emission (inne + utanför)
    inne_g_s: float = 0.0  # lagd på rutnätet
    utanfor_g_s: float = 0.0  # utanför domänen, ej med
    flyttad_g_s: float = 0.0  # flyttad uppåt ur fasta celler
    antal_flyttade_celler: int = 0
    varningar: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def _sin_cos_grad(vinkel_grad: float) -> tuple[float, float]:
    """sin och cos med exakta värden för multiplar av 90°."""
    v = float(vinkel_grad) % 360.0
    exakta = {0.0: (0.0, 1.0), 90.0: (1.0, 0.0), 180.0: (0.0, -1.0), 270.0: (-1.0, 0.0)}
    if v in exakta:
        return exakta[v]
    rad = math.radians(v)
    return math.sin(rad), math.cos(rad)


def rotationsmatris(vindriktning_grad: float) -> FloatArray:
    """R så att ``p' − C = R (p − C)``; rad 0 = e_x' (medvinds), rad 1 = e_y'."""
    s, c = _sin_cos_grad(vindriktning_grad)
    r: FloatArray = np.array([[-s, -c], [c, -s]], dtype=np.float64)
    return r


def _till_roterat(
    x: FloatArray, y: FloatArray, theta_grad: float, cx: float, cy: float
) -> tuple[FloatArray, FloatArray]:
    r = rotationsmatris(theta_grad)
    dx = x - cx
    dy = y - cy
    return cx + r[0, 0] * dx + r[0, 1] * dy, cy + r[1, 0] * dx + r[1, 1] * dy


def _fran_roterat(
    xr: FloatArray, yr: FloatArray, theta_grad: float, cx: float, cy: float
) -> tuple[FloatArray, FloatArray]:
    r = rotationsmatris(theta_grad)
    dx = xr - cx
    dy = yr - cy
    return cx + r[0, 0] * dx + r[1, 0] * dy, cy + r[0, 1] * dx + r[1, 1] * dy


def till_sweref(
    grid: Voxelgrid, i: npt.ArrayLike, j: npt.ArrayLike
) -> tuple[FloatArray, FloatArray]:
    """Kontinuerligt rutnätsindex (heltal = cellcentrum) → (x, y) i EPSG:3011."""
    ia = np.asarray(i, dtype=np.float64)
    ja = np.asarray(j, dtype=np.float64)
    cx, cy = grid.rotation_centrum_x_m, grid.rotation_centrum_y_m
    # relativt rotationscentrum först, så att stora SWEREF-tal inte äter precision
    du = (ia + 0.5) * grid.dx_m + (grid.origo_x_m - cx)
    dv = (ja + 0.5) * grid.dx_m + (grid.origo_y_m - cy)
    r = rotationsmatris(grid.vindriktning_grad)
    return cx + (r[0, 0] * du + r[1, 0] * dv), cy + (r[0, 1] * du + r[1, 1] * dv)


def fran_sweref(
    grid: Voxelgrid, x: npt.ArrayLike, y: npt.ArrayLike
) -> tuple[FloatArray, FloatArray]:
    """(x, y) i EPSG:3011 → kontinuerligt rutnätsindex (i, j), heltal = cellcentrum.

    Cellen som innehåller punkten är ``floor(i + 0,5), floor(j + 0,5)``. Räkningen
    görs relativt rotationscentrum, så avstånd i indexrummet är ``avstånd_m / dx``
    med full relativ precision även för stora SWEREF-koordinater.
    """
    xa = np.asarray(x, dtype=np.float64)
    ya = np.asarray(y, dtype=np.float64)
    cx, cy = grid.rotation_centrum_x_m, grid.rotation_centrum_y_m
    r = rotationsmatris(grid.vindriktning_grad)
    dx = xa - cx
    dy = ya - cy
    u = r[0, 0] * dx + r[0, 1] * dy + (cx - grid.origo_x_m)
    v = r[1, 0] * dx + r[1, 1] * dy + (cy - grid.origo_y_m)
    return u / grid.dx_m - 0.5, v / grid.dx_m - 0.5


# ---------------------------------------------------------------------------
# Voxelisering
# ---------------------------------------------------------------------------


def _kontrollera_raster(namn: str, a: npt.ArrayLike) -> FloatArray:
    arr = np.asarray(a, dtype=np.float64)
    if arr.ndim != 2 or arr.size == 0:
        raise LuftFel(f"{namn} måste vara ett icke-tomt 2D-raster.")
    if not np.all(np.isfinite(arr)):
        raise LuftFel(f"{namn} innehåller NaN/Inf – fyll eller maska data innan voxelisering.")
    if np.any(arr < 0):
        raise LuftFel(f"{namn} får inte vara negativ (höjd över mark).")
    return arr


def _sampla_raster_narmast(
    raster: FloatArray, ruta: RasterRuta, x: FloatArray, y: FloatArray
) -> FloatArray:
    """Närmaste granne; 0 utanför rastret (tom mark)."""
    c = np.floor((x - ruta.origo_x_m) / ruta.dx_m).astype(np.int64)
    r = np.floor((ruta.origo_y_m - y) / ruta.dx_m).astype(np.int64)
    inne = (c >= 0) & (c < ruta.kolumner) & (r >= 0) & (r < ruta.rader)
    ut: FloatArray = np.zeros(x.shape, dtype=np.float64)
    ut[inne] = raster[r[inne], c[inne]]
    return ut


def voxelisera(
    hojd_over_mark_m: npt.ArrayLike,
    dx_m: float,
    hojd_doman_m: float,
    *,
    raster_dx_m: float,
    raster_origo_x_m: float,
    raster_origo_y_m: float,
    buffert_uppstroms_m: float,
    buffert_nedstroms_m: float,
    buffert_sidor_m: float,
    vegetation_hojd_m: npt.ArrayLike | None = None,
    lad_m2_m3: float = 1.0,
    vindriktning_grad: float = 270.0,
    origo_z_m: float = 0.0,
) -> Voxelgrid:
    """Byggnadshöjdraster → `Voxelgrid` roterat så att vinden blåser i +x.

    Args:
        hojd_over_mark_m: (rader, kolumner) byggnadshöjd över mark, 0 = mark.
        dx_m: cellstorlek i rutnätet (alla tre riktningar).
        hojd_doman_m: domänens höjd; ``nz = ceil(hojd_doman_m / dx_m)``.
        raster_dx_m, raster_origo_x_m, raster_origo_y_m: rastrets cellstorlek och
            övre vänstra hörn (EPSG:3011, nordorienterat).
        buffert_*: tom mark runt rastrets roterade utbredning (m).
        vegetation_hojd_m: valfritt raster (samma form) med vegetationshöjd.
        lad_m2_m3: bladytetäthet i vegetationsceller.
        vindriktning_grad: meteorologisk riktning vinden kommer ifrån.
        origo_z_m: markplanets nivå i RH 2000.

    Rotationscentrum är rastrets mittpunkt. Sampling med närmaste granne i
    cellcentrum; ``fast = z_centrum < höjd``. Vegetation: ``lad`` där
    ``z_centrum < vegetationshöjd`` och cellen inte är fast.
    """
    hojd = _kontrollera_raster("hojd_over_mark_m", hojd_over_mark_m)
    veg: FloatArray | None = None
    if vegetation_hojd_m is not None:
        veg = _kontrollera_raster("vegetation_hojd_m", vegetation_hojd_m)
        if veg.shape != hojd.shape:
            raise LuftFel("vegetation_hojd_m måste ha samma form som hojd_over_mark_m.")
    for namn, varde in (
        ("dx_m", dx_m),
        ("hojd_doman_m", hojd_doman_m),
        ("raster_dx_m", raster_dx_m),
    ):
        if not (math.isfinite(varde) and varde > 0):
            raise LuftFel(f"{namn} måste vara positiv.")
    for namn, varde in (
        ("buffert_uppstroms_m", buffert_uppstroms_m),
        ("buffert_nedstroms_m", buffert_nedstroms_m),
        ("buffert_sidor_m", buffert_sidor_m),
    ):
        if not (math.isfinite(varde) and varde >= 0):
            raise LuftFel(f"{namn} måste vara icke-negativ.")
    if lad_m2_m3 < 0 or not math.isfinite(lad_m2_m3):
        raise LuftFel("lad_m2_m3 måste vara icke-negativ.")

    nz = math.ceil(hojd_doman_m / dx_m - _TOL_INDEX)
    tak_m = nz * dx_m
    hogsta = float(max(hojd.max(), veg.max() if veg is not None else 0.0))
    if hogsta >= tak_m:
        raise LuftFel(
            f"Domänens höjd {tak_m} m räcker inte: högsta byggnad/vegetation är {hogsta} m."
        )

    rader, kolumner = hojd.shape
    ruta = RasterRuta(raster_origo_x_m, raster_origo_y_m, raster_dx_m, rader, kolumner)
    x_min, y_min, x_max, y_max = ruta.utbredning
    cx = 0.5 * (x_min + x_max)
    cy = 0.5 * (y_min + y_max)

    hx = np.array([x_min, x_max, x_max, x_min], dtype=np.float64)
    hy = np.array([y_min, y_min, y_max, y_max], dtype=np.float64)
    hxr, hyr = _till_roterat(hx, hy, vindriktning_grad, cx, cy)
    origo_x = float(hxr.min()) - buffert_uppstroms_m
    origo_y = float(hyr.min()) - buffert_sidor_m
    langd_x = float(hxr.max()) + buffert_nedstroms_m - origo_x
    langd_y = float(hyr.max()) + buffert_sidor_m - origo_y
    nx = max(1, math.ceil(langd_x / dx_m - _TOL_INDEX))
    ny = max(1, math.ceil(langd_y / dx_m - _TOL_INDEX))

    ii, jj = np.meshgrid(
        np.arange(nx, dtype=np.float64), np.arange(ny, dtype=np.float64), indexing="ij"
    )
    xr = origo_x + (ii + 0.5) * dx_m
    yr = origo_y + (jj + 0.5) * dx_m
    xs, ys = _fran_roterat(xr, yr, vindriktning_grad, cx, cy)

    h_kol = _sampla_raster_narmast(hojd, ruta, xs, ys)
    z = (np.arange(nz, dtype=np.float64) + 0.5) * dx_m
    fast = z[None, None, :] < h_kol[:, :, None]
    lad: FloatArray = np.zeros((nx, ny, nz), dtype=np.float64)
    if veg is not None and lad_m2_m3 > 0:
        v_kol = _sampla_raster_narmast(veg, ruta, xs, ys)
        veg_cell = (z[None, None, :] < v_kol[:, :, None]) & ~fast
        lad[veg_cell] = lad_m2_m3

    return Voxelgrid(
        dx_m=float(dx_m),
        fast=np.ascontiguousarray(fast, dtype=np.bool_),
        lad_m2_m3=lad,
        vindriktning_grad=float(vindriktning_grad) % 360.0,
        origo_x_m=origo_x,
        origo_y_m=origo_y,
        origo_z_m=float(origo_z_m),
        rotation_centrum_x_m=cx,
        rotation_centrum_y_m=cy,
    )


# ---------------------------------------------------------------------------
# 3D-fält → nordorienterat raster
# ---------------------------------------------------------------------------


def nivaskiva_till_raster(
    grid: Voxelgrid,
    falt_3d: npt.ArrayLike,
    hojd_over_mark_m: float,
    raster_dx_m: float,
    utbredning: tuple[float, float, float, float],
    metod: Literal["narmast", "bilinjar"] = "narmast",
) -> tuple[FloatArray, RasterRuta]:
    """Samplar ett 3D-fält (nx, ny, nz) på en höjd över mark till nordorienterat raster.

    Lagret är det som innehåller ``hojd_over_mark_m`` (``k = floor(h/dx)``).
    Horisontellt: närmaste granne eller bilinjärt mellan cellcentrum, där
    bilinjärt bara väger in luftceller (vikterna normeras om). Resultatet är
    NaN där närmaste cell är fast eller punkten ligger utanför rutnätet.

    Args:
        utbredning: (x_min, y_min, x_max, y_max) i EPSG:3011. Antal rader/kolumner
            är ``ceil(utsträckning / raster_dx_m)`` från övre vänstra hörnet.

    Returns:
        (raster, ruta) där raster har formen (rader, kolumner).
    """
    falt = np.asarray(falt_3d, dtype=np.float64)
    nx, ny, nz = grid.form
    if falt.shape != (nx, ny, nz):
        raise LuftFel(f"falt_3d ska ha formen ({nx}, {ny}, {nz}), fick {falt.shape}.")
    if not (0 <= hojd_over_mark_m < nz * grid.dx_m):
        raise LuftFel(f"hojd_over_mark_m={hojd_over_mark_m} ligger utanför domänens höjd.")
    if raster_dx_m <= 0:
        raise LuftFel("raster_dx_m måste vara positiv.")
    if metod not in ("narmast", "bilinjar"):
        raise LuftFel(f"Okänd metod '{metod}'.")
    x_min, y_min, x_max, y_max = utbredning
    if not (x_max > x_min and y_max > y_min):
        raise LuftFel("utbredning måste vara (x_min, y_min, x_max, y_max) med max > min.")
    ruta = RasterRuta(
        origo_x_m=float(x_min),
        origo_y_m=float(y_max),
        dx_m=float(raster_dx_m),
        rader=max(1, math.ceil((y_max - y_min) / raster_dx_m - _TOL_INDEX)),
        kolumner=max(1, math.ceil((x_max - x_min) / raster_dx_m - _TOL_INDEX)),
    )
    k = int(math.floor(hojd_over_mark_m / grid.dx_m))
    skiva = falt[:, :, k]
    fast = grid.fast[:, :, k]

    xs, ys = ruta.cellcentrum_m()
    a, b = fran_sweref(grid, xs, ys)
    i_n = np.floor(a + 0.5).astype(np.int64)
    j_n = np.floor(b + 0.5).astype(np.int64)
    inne = (i_n >= 0) & (i_n < nx) & (j_n >= 0) & (j_n < ny)
    ut: FloatArray = np.full(xs.shape, np.nan, dtype=np.float64)
    i_c = np.clip(i_n, 0, nx - 1)
    j_c = np.clip(j_n, 0, ny - 1)
    giltig = inne & ~fast[i_c, j_c]

    if metod == "narmast":
        ut[giltig] = skiva[i_c[giltig], j_c[giltig]]
        return ut, ruta

    i0 = np.floor(a).astype(np.int64)
    j0 = np.floor(b).astype(np.int64)
    fa = a - i0
    fb = b - j0
    summa = np.zeros(xs.shape, dtype=np.float64)
    vikt_summa = np.zeros(xs.shape, dtype=np.float64)
    for di, dj in ((0, 0), (1, 0), (0, 1), (1, 1)):
        ii = i0 + di
        jj = j0 + dj
        w: FloatArray = (fa if di else 1.0 - fa) * (fb if dj else 1.0 - fb)
        ok = (ii >= 0) & (ii < nx) & (jj >= 0) & (jj < ny)
        iic = np.clip(ii, 0, nx - 1)
        jjc = np.clip(jj, 0, ny - 1)
        ok &= ~fast[iic, jjc]
        w = np.where(ok, w, 0.0)
        summa += w * np.where(ok, skiva[iic, jjc], 0.0)
        vikt_summa += w
    giltig &= vikt_summa > 0
    ut[giltig] = summa[giltig] / vikt_summa[giltig]
    return ut, ruta


# ---------------------------------------------------------------------------
# Källor
# ---------------------------------------------------------------------------


def _kontrollera_emission(namn: str, e: npt.ArrayLike, n: int) -> FloatArray:
    arr = np.atleast_1d(np.asarray(e, dtype=np.float64))
    if arr.shape != (n,):
        raise LuftFel(f"{namn} ska ha en emission per källa ({n}), fick form {arr.shape}.")
    if not np.all(np.isfinite(arr)) or np.any(arr < 0):
        raise LuftFel(f"{namn} måste vara ändlig och icke-negativ.")
    return arr


def _lager_for_hojd(grid: Voxelgrid, hojd_m: float) -> int:
    nz = grid.form[2]
    if not (math.isfinite(hojd_m) and 0 <= hojd_m < nz * grid.dx_m):
        raise LuftFel(f"Källhöjden {hojd_m} m ligger utanför domänen (0–{nz * grid.dx_m} m).")
    return int(math.floor(hojd_m / grid.dx_m))


def _lagg_i_luft(
    grid: Voxelgrid, i: IntArray, j: IntArray, k: int, massa: FloatArray, rapport: Kallrapport
) -> npt.NDArray[np.float64]:
    """Lägger massa i (i, j, k); fasta celler flyttas till närmaste luftcell ovanför."""
    nx, ny, nz = grid.form
    kallor: FloatArray = np.zeros((nx, ny, nz), dtype=np.float64)
    if massa.size == 0:
        return kallor
    kk = np.full(i.shape, k, dtype=np.int64)
    fast_kol = grid.fast[i, j, :]  # (n, nz)
    i_fast = fast_kol[:, k]
    if np.any(i_fast):
        ovan = ~fast_kol[:, k:]
        har_luft = ovan.any(axis=1)
        if np.any(i_fast & ~har_luft):
            raise LuftFel("Källa i fast cell utan luftcell ovanför (byggnad når taket).")
        forsta = k + np.argmax(ovan, axis=1)
        kk = np.where(i_fast, forsta, kk)
        flyttad = massa[i_fast]
        rapport.flyttad_g_s += float(flyttad.sum())
        unika = {(int(a), int(b)) for a, b in zip(i[i_fast], j[i_fast], strict=True)}
        rapport.antal_flyttade_celler += len(unika)
    np.add.at(kallor, (i, j, kk), massa)
    return kallor


def _segment_delar(
    a0: float, b0: float, a1: float, b1: float, nx: int, ny: int
) -> tuple[FloatArray, IntArray, IntArray, float]:
    """Exakt uppdelning av ett segment i cellkoordinater (cellhörn på heltal).

    Returns:
        (dt, i, j, t_inne): parameterlängd per del, cellindex, och total
        parameterlängd inne i [0, nx]×[0, ny].
    """
    da = a1 - a0
    db = b1 - b0
    t_in, t_ut = 0.0, 1.0
    for p, q in ((-da, a0), (da, nx - a0), (-db, b0), (db, ny - b0)):
        if p == 0.0:
            if q < 0:
                return (
                    np.empty(0, np.float64),
                    np.empty(0, np.int64),
                    np.empty(0, np.int64),
                    0.0,
                )
        else:
            t = q / p
            if p < 0:
                t_in = max(t_in, t)
            else:
                t_ut = min(t_ut, t)
    if t_ut <= t_in:
        return np.empty(0, np.float64), np.empty(0, np.int64), np.empty(0, np.int64), 0.0

    brytpunkter = [np.array([t_in, t_ut])]
    for start, d in ((a0, da), (b0, db)):
        if d != 0.0:
            lo = start + t_in * d
            hi = start + t_ut * d
            heltal = np.arange(math.ceil(min(lo, hi)), math.floor(max(lo, hi)) + 1)
            t_kors: FloatArray = (heltal - start) / d
            brytpunkter.append(t_kors[(t_kors > t_in) & (t_kors < t_ut)])
    t_alla = np.unique(np.concatenate(brytpunkter))
    dt = np.diff(t_alla)
    tm = 0.5 * (t_alla[:-1] + t_alla[1:])
    i = np.clip(np.floor(a0 + tm * da).astype(np.int64), 0, nx - 1)
    j = np.clip(np.floor(b0 + tm * db).astype(np.int64), 0, ny - 1)
    return dt, i, j, t_ut - t_in


def linjekalla_med_rapport(
    grid: Voxelgrid,
    linjer_xy_m: Sequence[npt.ArrayLike],
    emission_g_m_s: npt.ArrayLike,
    hojd_m: float = 0.5,
) -> tuple[FloatArray, Kallrapport]:
    """Som `linjekalla` men returnerar även en `Kallrapport` (och varnar inte)."""
    nx, ny, _ = grid.form
    emission = _kontrollera_emission("emission_g_m_s", emission_g_m_s, len(linjer_xy_m))
    k = _lager_for_hojd(grid, hojd_m)
    rapport = Kallrapport()
    alla_i: list[IntArray] = []
    alla_j: list[IntArray] = []
    alla_m: list[FloatArray] = []
    for nr, (linje, e) in enumerate(zip(linjer_xy_m, emission, strict=True)):
        p = np.asarray(linje, dtype=np.float64)
        if p.ndim != 2 or p.shape[1] != 2 or p.shape[0] < 2:
            raise LuftFel(f"Linje {nr} måste vara en (N, 2)-array med N ≥ 2.")
        if not np.all(np.isfinite(p)):
            raise LuftFel(f"Linje {nr} innehåller NaN/Inf.")
        a, b = fran_sweref(grid, p[:, 0], p[:, 1])
        a = a + 0.5  # cellhörn på heltal
        b = b + 0.5
        for s in range(p.shape[0] - 1):
            langd_m = math.hypot(p[s + 1, 0] - p[s, 0], p[s + 1, 1] - p[s, 1])
            rapport.total_g_s += langd_m * float(e)
            if langd_m == 0.0:
                continue
            dt, i, j, t_inne = _segment_delar(
                float(a[s]), float(b[s]), float(a[s + 1]), float(b[s + 1]), nx, ny
            )
            rapport.utanfor_g_s += (1.0 - t_inne) * langd_m * float(e)
            if dt.size:
                alla_i.append(i)
                alla_j.append(j)
                alla_m.append(dt * (langd_m * float(e)))
    if alla_m:
        i_c = np.concatenate(alla_i)
        j_c = np.concatenate(alla_j)
        m_c = np.concatenate(alla_m)
    else:
        i_c = np.empty(0, np.int64)
        j_c = np.empty(0, np.int64)
        m_c = np.empty(0, np.float64)
    kallor = _lagg_i_luft(grid, i_c, j_c, k, m_c, rapport)
    rapport.inne_g_s = float(m_c.sum())
    _skriv_varningar(rapport, "Linjekälla")
    return kallor, rapport


def _skriv_varningar(rapport: Kallrapport, namn: str) -> None:
    if rapport.utanfor_g_s > 0:
        andel = rapport.utanfor_g_s / rapport.total_g_s if rapport.total_g_s > 0 else 1.0
        rapport.varningar.append(
            f"{namn}: {rapport.utanfor_g_s:.6g} g/s ({andel:.1%}) ligger utanför domänen "
            "och är inte med."
        )
    if rapport.antal_flyttade_celler > 0:
        rapport.varningar.append(
            f"{namn}: {rapport.flyttad_g_s:.6g} g/s i {rapport.antal_flyttade_celler} fasta "
            "celler flyttades till närmaste luftcell ovanför."
        )


def _varna(rapport: Kallrapport) -> None:
    for text in rapport.varningar:
        warnings.warn(text, LuftVarning, stacklevel=3)


def linjekalla(
    grid: Voxelgrid,
    linjer_xy_m: Sequence[npt.ArrayLike],
    emission_g_m_s: npt.ArrayLike,
    hojd_m: float = 0.5,
) -> FloatArray:
    """Linjekällor (t.ex. gator) i EPSG:3011 → källfält (nx, ny, nz) i g/s.

    Varje linje är en (N, 2)-array; `emission_g_m_s` har ett värde per linje.
    Segmenten delas exakt vid cellgränser, så ``summa = Σ längd_inne · emission``.
    Delar utanför domänen och källor i fasta celler (flyttas uppåt) ger
    `LuftVarning`. Använd `linjekalla_med_rapport` för att få siffrorna.
    """
    kallor, rapport = linjekalla_med_rapport(grid, linjer_xy_m, emission_g_m_s, hojd_m)
    _varna(rapport)
    return kallor


def punktkalla_med_rapport(
    grid: Voxelgrid,
    punkter_xy_m: npt.ArrayLike,
    emission_g_s: npt.ArrayLike,
    hojd_m: float = 0.5,
) -> tuple[FloatArray, Kallrapport]:
    """Som `punktkalla` men returnerar även en `Kallrapport` (och varnar inte)."""
    nx, ny, _ = grid.form
    p = np.atleast_2d(np.asarray(punkter_xy_m, dtype=np.float64))
    if p.ndim != 2 or p.shape[1] != 2:
        raise LuftFel("punkter_xy_m måste ha formen (M, 2).")
    if not np.all(np.isfinite(p)):
        raise LuftFel("punkter_xy_m innehåller NaN/Inf.")
    emission = _kontrollera_emission("emission_g_s", emission_g_s, p.shape[0])
    k = _lager_for_hojd(grid, hojd_m)
    rapport = Kallrapport(total_g_s=float(emission.sum()))
    a, b = fran_sweref(grid, p[:, 0], p[:, 1])
    i = np.floor(a + 0.5).astype(np.int64)
    j = np.floor(b + 0.5).astype(np.int64)
    inne = (i >= 0) & (i < nx) & (j >= 0) & (j < ny)
    rapport.utanfor_g_s = float(emission[~inne].sum())
    kallor = _lagg_i_luft(grid, i[inne], j[inne], k, emission[inne], rapport)
    rapport.inne_g_s = float(emission[inne].sum())
    _skriv_varningar(rapport, "Punktkälla")
    return kallor, rapport


def punktkalla(
    grid: Voxelgrid,
    punkter_xy_m: npt.ArrayLike,
    emission_g_s: npt.ArrayLike,
    hojd_m: float = 0.5,
) -> FloatArray:
    """Punktkällor (M, 2) i EPSG:3011 med emission (M,) g/s → källfält (nx, ny, nz) g/s."""
    kallor, rapport = punktkalla_med_rapport(grid, punkter_xy_m, emission_g_s, hojd_m)
    _varna(rapport)
    return kallor


def grid_meta(grid: Voxelgrid) -> dict[str, Any]:
    """Skalära geometriuppgifter för ett `Voxelgrid` (för KL1-/KL2-metadata)."""
    nx, ny, nz = grid.form
    return {
        "dx_m": grid.dx_m,
        "form": [nx, ny, nz],
        "vindriktning_grad": grid.vindriktning_grad,
        "origo_x_m": grid.origo_x_m,
        "origo_y_m": grid.origo_y_m,
        "origo_z_m": grid.origo_z_m,
        "rotation_centrum_x_m": grid.rotation_centrum_x_m,
        "rotation_centrum_y_m": grid.rotation_centrum_y_m,
        "epsg": EPSG_SWEREF99_1800,
    }
