"""Höjdfält ur K1-rutor: mosaik av en rutas `mark.tif` och grannrutorna, med omsampling."""

from __future__ import annotations

import math
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
    saknade_celler: int  # saknade höjdceller i mosaiken (inom radie_m om angiven), satta under allt
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


def _byggnad_beror_bbox(post: Any, bbox: tuple[float, float, float, float]) -> bool:
    """Sant om byggnadens polygon berör `bbox = (x_min, y_min, x_max, y_max)` i EPSG:3011."""
    mx0, my0, mx1, my1 = bbox
    xs = [p[0] for p in post.polygon]
    ys = [p[1] for p in post.polygon]
    if max(xs) < mx0 or min(xs) > mx1 or max(ys) < my0 or min(ys) > my1:
        return False
    import shapely

    return bool(post.geometri().intersects(shapely.box(mx0, my0, mx1, my1)))


def _lasa_tile(
    mapp: Path,
    vegetation: bool,
    *,
    klipp_bbox: tuple[float, float, float, float] | None = None,
) -> _Tile:
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
        standard = frozenset(
            b.byggnad_id
            for b in poster
            if b.hojdkalla == "standard"
            and (klipp_bbox is None or _byggnad_beror_bbox(b, klipp_bbox))
        )
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


@dataclass(frozen=True)
class _TilePlacering:
    """Placering av en (eventuellt beskuren) K1-ruta i mosaikens cellrutnät."""

    mapp: Path
    dst_r0: int
    dst_r1: int
    dst_c0: int
    dst_c1: int
    src_r0: int
    src_r1: int
    src_c0: int
    src_c1: int
    klippt: bool
    klipp_bbox: tuple[float, float, float, float]
    ar_egen: bool


def _planera_mosaik(
    egen: Path,
    grannar: tuple[Path, ...],
    cell_m: float,
    radie_m: float | None = None,
) -> tuple[
    int,
    float,
    float,
    int,
    int,
    tuple[int, int, int, int],
    tuple[_TilePlacering, ...],
]:
    """Validerar rutgeometri och beräknar mosaikens form och rutplaceringar utan pixelinläsning.

    När `radie_m` anges beskärs mosaiken till `[egen ± ceil(radie_m / cell_m)]`, skuret mot
    utsträckningen av de rutor som faktiskt berör detta område och inpassat exakt på `cell_m`-
    rutnätet. Rutor helt utanför utelämnas ur läsplanen.
    """
    if radie_m is not None and (
        isinstance(radie_m, bool) or not math.isfinite(radie_m) or radie_m < 0.0
    ):
        raise RutaFel(f"radie_m måste vara ett ändligt tal >= 0, fick {radie_m!r}.")
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
        if g.hojd % faktor or g.bredd % faktor:
            raise RutaFel(
                f"Rastret {g.hojd}x{g.bredd} kan inte omsamplas med faktor {faktor} (inte delbart)."
            )
    ux0 = min(g.x_min for g in geos)
    uy1 = max(g.y_max for g in geos)
    ux1 = max(g.x_max for g in geos)
    uy0 = min(g.y_min for g in geos)
    u_bredd = round((ux1 - ux0) / cell_m)
    u_hojd = round((uy1 - uy0) / cell_m)
    if abs(u_bredd * cell_m - (ux1 - ux0)) > _TOL or abs(u_hojd * cell_m - (uy1 - uy0)) > _TOL:
        raise RutaFel("Mosaikens utsträckning är inte en heltalsmultipel av cellstorleken.")

    rut_boxar: list[tuple[int, int, int, int]] = []
    for g in geos:
        j0f, i0f = (g.x_min - ux0) / cell_m, (uy1 - g.y_max) / cell_m
        j0, i0 = round(j0f), round(i0f)
        if abs(j0f - j0) > _TOL or abs(i0f - i0) > _TOL:
            raise RutaFel("En ruta ligger inte på mosaikens cellraster (förskjutning ej heltal).")
        rut_boxar.append((i0, i0 + g.hojd // faktor, j0, j0 + g.bredd // faktor))

    er0, er1, ec0, ec1 = rut_boxar[-1]
    if radie_m is None:
        kr0, kr1, kc0, kc1 = 0, u_hojd, 0, u_bredd
    else:
        marginal = math.ceil(radie_m / cell_m)
        mr0, mr1 = er0 - marginal, er1 + marginal
        mc0, mc1 = ec0 - marginal, ec1 + marginal
        aktiva = [
            b for b in rut_boxar if b[1] > mr0 and b[0] < mr1 and b[3] > mc0 and b[2] < mc1
        ]
        ar0 = min(b[0] for b in aktiva)
        ar1 = max(b[1] for b in aktiva)
        ac0 = min(b[2] for b in aktiva)
        ac1 = max(b[3] for b in aktiva)
        kr0 = max(ar0, mr0)
        kr1 = min(ar1, mr1)
        kc0 = max(ac0, mc0)
        kc1 = min(ac1, mc1)

    x0 = ux0 + kc0 * cell_m
    y1 = uy1 - kr0 * cell_m
    bredd = kc1 - kc0
    hojd = kr1 - kr0
    egen_fonster = (er0 - kr0, er1 - kr0, ec0 - kc0, ec1 - kc0)

    sista_idx = len(mappar) - 1
    placeringar: list[_TilePlacering] = []
    for idx, (mapp, (ti0, ti1, tj0, tj1)) in enumerate(zip(mappar, rut_boxar, strict=True)):
        ir0 = max(ti0, kr0)
        ir1 = min(ti1, kr1)
        ic0 = max(tj0, kc0)
        ic1 = min(tj1, kc1)
        if ir0 >= ir1 or ic0 >= ic1:
            continue
        src_r0, src_r1 = ir0 - ti0, ir1 - ti0
        src_c0, src_c1 = ic0 - tj0, ic1 - tj0
        klippt = src_r0 > 0 or src_r1 < (ti1 - ti0) or src_c0 > 0 or src_c1 < (tj1 - tj0)
        klipp_bbox = (
            ux0 + ic0 * cell_m,
            uy1 - ir1 * cell_m,
            ux0 + ic1 * cell_m,
            uy1 - ir0 * cell_m,
        )
        placeringar.append(
            _TilePlacering(
                mapp=mapp,
                dst_r0=ir0 - kr0,
                dst_r1=ir1 - kr0,
                dst_c0=ic0 - kc0,
                dst_c1=ic1 - kc0,
                src_r0=src_r0,
                src_r1=src_r1,
                src_c0=src_c0,
                src_c1=src_c1,
                klippt=klippt,
                klipp_bbox=klipp_bbox,
                ar_egen=(idx == sista_idx),
            )
        )
    return faktor, x0, y1, bredd, hojd, egen_fonster, tuple(placeringar)


def uppskatta_mosaik_form(
    egen: Path,
    grannar: tuple[Path, ...],
    cell_m: float,
    radie_m: float | None = None,
) -> tuple[int, int]:
    """Returnerar mosaikens exakta `(hojd, bredd)` i celler ur GeoTIFF-huvuden (O(1) RAM)."""
    _, _, _, bredd, hojd, _, _ = _planera_mosaik(egen, grannar, cell_m, radie_m=radie_m)
    return hojd, bredd


def bygg_mosaik(
    egen: Path,
    grannar: tuple[Path, ...],
    cell_m: float,
    metod: Omsampling,
    *,
    vegetation: bool = False,
    radie_m: float | None = None,
) -> Mosaik:
    """Sammanfogar `egen` (skrivs sist, så den vinner i överlapp) och `grannar` till ett fält
    med cellstorleken `cell_m`. Rutor som inte ligger på samma cellraster, och rutor som inte är
    K1 v2, ger fel.

    När `radie_m` anges beskärs mosaiken till `[egen ± ceil(radie_m / cell_m)]` skuret mot
    befintliga rutor, vilket ger bitidentisk horisontfysik men kapar onödiga yttre rutdelar.
    Varje enskild `_Tile` läses en i taget och frigörs omedelbart efter att dess omsamplade
    värden kopierats in i mosaiken, så toppminnet vid flerrutsbygge hålls nere.
    """
    faktor, x0, y1, bredd, hojd, fonster, placeringar = _planera_mosaik(
        egen, grannar, cell_m, radie_m=radie_m
    )
    z = np.full((hojd, bredd), np.nan)
    z_utan: NDArray[np.float64] | None = None
    alla_standard: set[str] = set()
    egen_standard_antal = 0

    for pl in placeringar:
        t = (
            _lasa_tile(pl.mapp, vegetation, klipp_bbox=pl.klipp_bbox)
            if pl.klippt
            else _lasa_tile(pl.mapp, vegetation)
        )
        z_del = (
            t.z[
                pl.src_r0 * faktor : pl.src_r1 * faktor,
                pl.src_c0 * faktor : pl.src_c1 * faktor,
            ]
            if pl.klippt
            else t.z
        )
        zc = omsampla(z_del, faktor, metod)
        if t.z_utan_standard is not None:
            if z_utan is None:
                # Föregående rutor saknade standardhöjd, så z_utan var identisk med z hittills.
                z_utan = z.copy()
            zu_del = (
                t.z_utan_standard[
                    pl.src_r0 * faktor : pl.src_r1 * faktor,
                    pl.src_c0 * faktor : pl.src_c1 * faktor,
                ]
                if pl.klippt
                else t.z_utan_standard
            )
            zuc = omsampla(zu_del, faktor, metod)
            z_utan[pl.dst_r0 : pl.dst_r1, pl.dst_c0 : pl.dst_c1] = zuc
            del zuc, zu_del
        elif z_utan is not None:
            z_utan[pl.dst_r0 : pl.dst_r1, pl.dst_c0 : pl.dst_c1] = zc
        z[pl.dst_r0 : pl.dst_r1, pl.dst_c0 : pl.dst_c1] = zc
        if t.standard_ids:
            alla_standard.update(t.standard_ids)
        if pl.ar_egen:
            egen_standard_antal = len(t.standard_ids)
        # Frigör tile-arrayerna direkt så max en tile + mosaiken lever i RAM samtidigt.
        del z_del, zc, t

    saknas = ~np.isfinite(z)
    antal_saknas_totalt = int(saknas.sum())
    spann = float(np.nanmax(z) - np.nanmin(z))
    antal_saknas = 0
    if antal_saknas_totalt:
        if radie_m is not None:
            r0_e, r1_e, c0_e, c1_e = fonster
            rader = np.arange(hojd, dtype=np.float64)
            kols = np.arange(bredd, dtype=np.float64)
            dy = np.maximum(0.0, np.maximum(r0_e - (rader + 1.0), rader - r1_e)) * cell_m
            dx = np.maximum(0.0, np.maximum(c0_e - (kols + 1.0), kols - c1_e)) * cell_m
            sr, sc = np.nonzero(saknas)
            # Räkna bara saknade celler inom påverkansradien (hörnceller bortom radie_m
            # kan aldrig träffas av någon stråle från den egna rutan).
            antal_saknas = int(np.count_nonzero(dx[sc] ** 2 + dy[sr] ** 2 <= radie_m * radie_m))
        else:
            antal_saknas = antal_saknas_totalt
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
