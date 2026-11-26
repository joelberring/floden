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
    """Grovare raster med heltalsfaktor. `punkt` samplar exakt i den grova cellens mittpunkt."""
    if faktor == 1:
        return z
    h, b = z.shape
    if h % faktor or b % faktor:
        raise RutaFel(f"Rastret {h}x{b} kan inte omsamplas med faktor {faktor} (inte delbart).")
    if metod == "punkt":
        o = faktor // 2
        if faktor % 2 == 1:
            return np.ascontiguousarray(z[o::faktor, o::faktor])
        # Jämn faktor: cellens mittpunkt ligger exakt mellan de 4 centrala fina cellerna.
        return np.ascontiguousarray(
            0.25
            * (
                z[o - 1 :: faktor, o - 1 :: faktor]
                + z[o - 1 :: faktor, o::faktor]
                + z[o::faktor, o - 1 :: faktor]
                + z[o::faktor, o::faktor]
            )
        )
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


def _las_tile_geo(mapp: Path) -> Any:
    """Läser enbart K1 v2-georeferens för `mapp` ur `terrang.tif`-huvudet (utan pixelinläsning)."""
    from karna.geometri import GeometriFel, k1_version
    from testsvit.k1_io import GeoTransformInfo, las_geotiff

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
    sokvag = mapp / "terrang.tif"
    if not sokvag.is_file():
        raise RutaFel(f"Rutan {mapp.name}: Saknar rasterfil: {sokvag}")
    try:
        import rasterio
    except ImportError:  # pragma: no cover - rasterio är ett beroende
        try:
            _, geo = las_geotiff(sokvag)
        except Exception as exc:  # noqa: BLE001
            raise RutaFel(f"Rutan {mapp.name}: {exc}") from exc
    else:
        try:
            with rasterio.open(sokvag) as src:
                t = src.transform
                epsg = src.crs.to_epsg() if src.crs is not None else 0
                geo = GeoTransformInfo(
                    bredd=int(src.width),
                    hojd=int(src.height),
                    x_min=float(t.c),
                    y_max=float(t.f),
                    pixelstorlek_x=float(t.a),
                    pixelstorlek_y=float(-t.e),
                    epsg=int(epsg or 0),
                    dtype=str(src.dtypes[0]),
                )
        except (OSError, rasterio.errors.RasterioError) as exc:
            raise RutaFel(f"Rutan {mapp.name}: {exc}") from exc
    if abs(geo.pixelstorlek_x - geo.pixelstorlek_y) > _TOL:
        raise RutaFel(f"{sokvag}: icke-kvadratiska celler stöds inte.")
    return geo


def _planera_mosaik(
    egen: Path,
    grannar: tuple[Path, ...],
    cell_m: float,
) -> tuple[
    tuple[Path, ...],
    int,
    float,
    float,
    int,
    int,
    tuple[tuple[int, int], ...],
]:
    """Validerar rutgeometri och beräknar mosaikens form och rutplaceringar utan pixelinläsning."""
    mappar = (*grannar, egen)
    geos = [_las_tile_geo(p) for p in mappar]
    p0 = geos[0].pixelstorlek_x
    faktor_f = cell_m / p0
    if abs(faktor_f - round(faktor_f)) > _TOL or round(faktor_f) < 1:
        raise RutaFel(
            f"cellstorlek_m={cell_m} måste vara en heltalsmultipel av rutornas pixelstorlek {p0}."
        )
    faktor = round(faktor_f)
    for g in geos:
        if abs(g.pixelstorlek_x - p0) > _TOL:
            raise RutaFel("Rutorna har olika pixelstorlek.")
    x0 = min(g.x_min for g in geos)
    y1 = max(g.y_max for g in geos)
    x1 = max(g.x_max for g in geos)
    y0 = min(g.y_min for g in geos)
    bredd = round((x1 - x0) / cell_m)
    hojd = round((y1 - y0) / cell_m)
    if abs(bredd * cell_m - (x1 - x0)) > _TOL or abs(hojd * cell_m - (y1 - y0)) > _TOL:
        raise RutaFel("Mosaikens utsträckning är inte en heltalsmultipel av cellstorleken.")
    placeringar: list[tuple[int, int]] = []
    for g in geos:
        j0f, i0f = (g.x_min - x0) / cell_m, (y1 - g.y_max) / cell_m
        j0, i0 = round(j0f), round(i0f)
        if abs(j0f - j0) > _TOL or abs(i0f - i0) > _TOL:
            raise RutaFel("En ruta ligger inte på mosaikens cellraster (förskjutning ej heltal).")
        placeringar.append((i0, j0))
    return mappar, faktor, x0, y1, bredd, hojd, tuple(placeringar)


def uppskatta_mosaik_form(
    egen: Path,
    grannar: tuple[Path, ...],
    cell_m: float,
) -> tuple[int, int]:
    """Returnerar mosaikens exakta `(hojd, bredd)` i celler ur GeoTIFF-huvuden (O(1) RAM)."""
    _, _, _, _, bredd, hojd, _ = _planera_mosaik(egen, grannar, cell_m)
    return hojd, bredd


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
    K1 v2, ger fel.

    Varje enskild `_Tile` läses en i taget och frigörs omedelbart efter att dess omsamplade
    värden kopierats in i mosaiken, så toppminnet vid flerrutsbygge hålls nere.
    """
    mappar, faktor, x0, y1, bredd, hojd, placeringar = _planera_mosaik(egen, grannar, cell_m)
    z = np.full((hojd, bredd), np.nan)
    z_utan: NDArray[np.float64] | None = None
    fonster = (0, 0, 0, 0)
    alla_standard: set[str] = set()
    egen_standard_antal = 0
    sista_idx = len(mappar) - 1

    for idx, (mapp, (i0, j0)) in enumerate(zip(mappar, placeringar, strict=True)):
        t = _lasa_tile(mapp, vegetation)
        zc = omsampla(t.z, faktor, metod)
        h_c, b_c = zc.shape
        if t.z_utan_standard is not None:
            if z_utan is None:
                # Föregående rutor saknade standardhöjd, så z_utan var identisk med z hittills.
                z_utan = z.copy()
            zuc = omsampla(t.z_utan_standard, faktor, metod)
            z_utan[i0 : i0 + h_c, j0 : j0 + b_c] = zuc
            del zuc
        elif z_utan is not None:
            z_utan[i0 : i0 + h_c, j0 : j0 + b_c] = zc
        z[i0 : i0 + h_c, j0 : j0 + b_c] = zc
        fonster = (i0, i0 + h_c, j0, j0 + b_c)
        if t.standard_ids:
            alla_standard.update(t.standard_ids)
        if idx == sista_idx:
            egen_standard_antal = len(t.standard_ids)
        # Frigör tile-arrayerna direkt så max en tile + mosaiken lever i RAM samtidigt.
        del zc, t

    saknas = ~np.isfinite(z)
    antal_saknas = int(saknas.sum())
    spann = float(np.nanmax(z) - np.nanmin(z))
    if antal_saknas:
        under = float(np.nanmin(z)) - 1000.0  # under allt: ger inga hinder, redovisas
        z[saknas] = under
        if z_utan is not None:
            z_utan[~np.isfinite(z_utan)] = under
    del saknas
    return Mosaik(
        Hojdfalt(z, cell_m, x0, y1),
        fonster,
        antal_saknas,
        spann,
        None if z_utan is None else Hojdfalt(z_utan, cell_m, x0, y1),
        frozenset(alla_standard),
        egen_standard_antal,
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
