# ruff: noqa: E501
"""Nationella marktäckedata (NMD) som markunderlag för habitatnätverk (kontrakt K46, K45).

Källa: Naturvårdsverket, Nationella marktäckedata (NMD), basskikt, 10 m raster, öppna data (CC0),
SWEREF 99 TM (EPSG:3006). Nedladdningssidan:
https://www.naturvardsverket.se/verktyg-och-tjanster/kartor-och-karttjanster/nationella-marktackedata/ladda-ner-nationella-marktackedata/
Filerna ligger under https://geodata.naturvardsverket.se/nedladdning/marktacke/ (NMD2018/ och NMD2023/);
produktbeskrivningar (kodnyckel) ligger bredvid. Metadata i Naturvårdsverkets geodatakatalog
(https://geodatakatalogen.naturvardsverket.se). Exakt filnamn för rikstäckande GeoTIFF är inte
kontrollerat härifrån (containern når inte Naturvårdsverket); hämta filen från nedladdningssidan och
ange sökvägen. Inget nedladdningsanrop görs i den här modulen.

`klipp_nmd` klipper kärnområdet plus en kontextbuffert (2-5 km efter nätverkets tröskel), omprojicerar
till EPSG:3011 (analysens koordinatsystem) med närmaste granne (klasskoder får aldrig interpoleras) och
skriver en uint8-GeoTIFF (K1-format) med metadata. Ritade hus (K9) läggs ovanpå som NMD 51
(`lagg_hus_som_nmd51`).

Kommando (ett per rad, se docs/DRIFT.md):
    python -m data_inlasning.nmd klipp --nmd NMD.tif --kaerna E0 N0 E1 N1 --natverk barrskog-nacka --ut ekologi/
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from testsvit.k1_io import las_geotiff, skriv_geotiff

EPSG_ANALYS = 3011
EPSG_NMD = 3006
NMD_PIXEL_M = 10.0
NMD_KALLA = "Naturvårdsverket, Nationella marktäckedata (NMD), basskikt 10 m, CC0"
NMD_SIDA = (
    "https://www.naturvardsverket.se/verktyg-och-tjanster/kartor-och-karttjanster/"
    "nationella-marktackedata/ladda-ner-nationella-marktackedata/"
)
NMD_BYGGNAD = 51
BUFFERT_MIN_M = 2000.0
BUFFERT_MAX_M = 5000.0
GILTIGA_KODER = frozenset(
    {0, 2, 3, 41, 42, 51, 52, 53, 61, 62}
    | set(range(111, 119))
    | set(range(121, 129))
)
"""NMD-basskiktets klasskoder (0 = ingen data). Andra värden ger fel; inget gissas."""


class NmdFel(Exception):
    """Fel vid inläsning eller klippning av NMD."""


@dataclass(frozen=True)
class NmdLager:
    """NMD-klasser på ett rutnät i EPSG:3011 (rad 0 = norr)."""

    klasser: NDArray[np.uint8]
    x_min_m: float
    y_max_m: float
    cell_m: float

    @property
    def utbredning(self) -> tuple[float, float, float, float]:
        r, k = self.klasser.shape
        return (self.x_min_m, self.y_max_m - r * self.cell_m, self.x_min_m + k * self.cell_m, self.y_max_m)


def buffert_for_troskel(troskel_m: float) -> float:
    """Kontextbuffert i m: tröskeln i effektiva m, begränsad till 2-5 km.

    Effektiva meter är minst lika långa som verkliga meter (motstånd >= 1), så en länk inom tröskeln
    ryms alltid inom `troskel_m` från kärnan; 2 km är golv och 5 km tak enligt uppdraget.
    """
    if not (math.isfinite(troskel_m) and troskel_m > 0):
        raise NmdFel(f"Tröskeln ska vara > 0, fick {troskel_m}.")
    return float(min(BUFFERT_MAX_M, max(BUFFERT_MIN_M, troskel_m)))


def kontrollera_koder(klasser: NDArray[Any]) -> None:
    okanda = sorted(set(np.unique(klasser).tolist()) - GILTIGA_KODER)
    if okanda:
        raise NmdFel(
            f"Okända NMD-klasskoder {okanda[:10]}: filen är inte NMD-basskiktet (eller en annan version)."
        )


def klipp_nmd(
    nmd_fil: str | Path,
    kaerna_3011: tuple[float, float, float, float],
    buffert_m: float,
    *,
    ut_fil: str | Path | None = None,
    cell_m: float = NMD_PIXEL_M,
) -> NmdLager:
    """Klipper kärnan (e_min, n_min, e_max, n_max i EPSG:3011) plus `buffert_m` ur en NMD-GeoTIFF.

    Rutnätet i EPSG:3011 ligger på multiplar av `cell_m` så att före och efter ger samma rutnät.
    Källfilens CRS ska vara EPSG:3006; annat ger fel. Områden utanför rastret får kod 0.
    """
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import from_bounds

    e0, n0, e1, n1 = kaerna_3011
    if not (e1 > e0 and n1 > n0):
        raise NmdFel(f"Kärnområdet är tomt: {kaerna_3011}.")
    if not (math.isfinite(buffert_m) and 0 <= buffert_m <= 20_000):
        raise NmdFel(f"Bufferten ska vara 0-20 000 m, fick {buffert_m}.")
    x0 = math.floor((e0 - buffert_m) / cell_m) * cell_m
    x1 = math.ceil((e1 + buffert_m) / cell_m) * cell_m
    y0 = math.floor((n0 - buffert_m) / cell_m) * cell_m
    y1 = math.ceil((n1 + buffert_m) / cell_m) * cell_m
    bredd, hojd = round((x1 - x0) / cell_m), round((y1 - y0) / cell_m)
    if bredd * hojd > 4_000_000:
        raise NmdFel(f"Området blir {bredd * hojd} celler; minska området eller bufferten.")
    with rasterio.open(nmd_fil) as ds:
        if ds.crs is None or ds.crs.to_epsg() != EPSG_NMD:
            raise NmdFel(f"NMD-filen ska ha EPSG:{EPSG_NMD}, fick {ds.crs}.")
        # Fönster med marginal i källans CRS (rektangeln roterar något mellan 3011 och 3006).
        sb = transform_bounds(EPSG_ANALYS, EPSG_NMD, x0, y0, x1, y1, densify_pts=21)
        marg = 2 * ds.res[0]
        fonster = from_bounds(sb[0] - marg, sb[1] - marg, sb[2] + marg, sb[3] + marg, ds.transform)
        fonster = fonster.round_offsets().round_lengths()
        try:
            data = ds.read(1, window=fonster, boundless=True, fill_value=0)
        except ValueError as exc:
            raise NmdFel(f"Kärnområdet ligger utanför NMD-filen: {exc}") from exc
        kontrollera_koder(data)
        src_transform = ds.window_transform(fonster)
        mal = np.zeros((hojd, bredd), dtype=np.uint8)
        reproject(
            source=data.astype(np.uint8),
            destination=mal,
            src_transform=src_transform,
            src_crs=ds.crs,
            dst_transform=from_origin(x0, y1, cell_m, cell_m),
            dst_crs=f"EPSG:{EPSG_ANALYS}",
            resampling=Resampling.nearest,
            src_nodata=None,
            dst_nodata=0,
        )
    lager = NmdLager(mal, x0, y1, cell_m)
    if ut_fil is not None:
        skriv_nmd(lager, ut_fil, nmd_fil=Path(nmd_fil), kaerna=kaerna_3011, buffert_m=buffert_m)
    return lager


def skriv_nmd(
    lager: NmdLager,
    ut_fil: str | Path,
    *,
    nmd_fil: Path | None = None,
    kaerna: tuple[float, float, float, float] | None = None,
    buffert_m: float | None = None,
) -> Path:
    """Skriver `nmd.tif` (uint8, EPSG:3011) och `nmd.json` (källa, kontrollsumma, utbredning)."""
    fil = Path(ut_fil)
    fil.parent.mkdir(parents=True, exist_ok=True)
    skriv_geotiff(fil, lager.klasser, x_min=lager.x_min_m, y_max=lager.y_max_m, pixelstorlek=lager.cell_m)
    meta = {
        "format": "floden-nmd-lager/v1",
        "kalla": NMD_KALLA,
        "kalla_sida": NMD_SIDA,
        "licens": "CC0",
        "epsg": EPSG_ANALYS,
        "cellstorlek_m": lager.cell_m,
        "utbredning_3011": list(lager.utbredning),
        "kaerna_3011": list(kaerna) if kaerna else None,
        "buffert_m": buffert_m,
        "kallfil": nmd_fil.name if nmd_fil else None,
        "kallfil_sha256": sha256_fil(nmd_fil) if nmd_fil and nmd_fil.is_file() else None,
        "sha256": sha256_fil(fil),
        "omprojicering": f"EPSG:{EPSG_NMD} till EPSG:{EPSG_ANALYS}, närmaste granne",
    }
    fil.with_suffix(".json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return fil


def las_nmd(sokvag: str | Path) -> NmdLager:
    """Läser en `nmd.tif` (uint8, EPSG:3011, kvadratiska celler). Okända koder ger fel."""
    arr, geo = las_geotiff(sokvag)
    if geo.epsg != EPSG_ANALYS:
        raise NmdFel(f"{Path(sokvag).name} ska vara EPSG:{EPSG_ANALYS}, fick {geo.epsg}.")
    if abs(geo.pixelstorlek_x - geo.pixelstorlek_y) > 1e-9:
        raise NmdFel("NMD-lagret har icke-kvadratiska celler.")
    if arr.dtype != np.uint8:
        raise NmdFel(f"{Path(sokvag).name} ska vara uint8, fick {arr.dtype}.")
    kontrollera_koder(arr)
    return NmdLager(np.asarray(arr, dtype=np.uint8), geo.x_min, geo.y_max, geo.pixelstorlek_x)


def lagg_hus_som_nmd51(
    lager: NmdLager,
    hus_mask: NDArray[np.bool_],
    hus_x_min_m: float,
    hus_y_max_m: float,
    hus_cell_m: float,
    hus_kod: int = NMD_BYGGNAD,
) -> tuple[NDArray[np.uint8], int]:
    """NMD-klasserna med byggnader (K9 efter tillämpning) ovanpå som NMD 51.

    `hus_mask` ligger på ett eget rutnät; varje NMD-cell slår upp den hus-cell som täcker dess
    mittpunkt. Returnerar (klasser, antal celler som ändrades). Rivna hus tas inte bort ur NMD.
    """
    ny, nx = lager.klasser.shape
    e = lager.x_min_m + (np.arange(nx) + 0.5) * lager.cell_m
    n = lager.y_max_m - (np.arange(ny) + 0.5) * lager.cell_m
    kol = np.floor((e - hus_x_min_m) / hus_cell_m).astype(int)
    rad = np.floor((hus_y_max_m - n) / hus_cell_m).astype(int)
    ok_k = (kol >= 0) & (kol < hus_mask.shape[1])
    ok_r = (rad >= 0) & (rad < hus_mask.shape[0])
    ut = lager.klasser.copy()
    hus = np.zeros((ny, nx), dtype=bool)
    rr, kk = np.meshgrid(np.flatnonzero(ok_r), np.flatnonzero(ok_k), indexing="ij")
    hus[rr, kk] = hus_mask[rad[rr], kol[kk]]
    andrade = int(np.count_nonzero(hus & (ut != hus_kod)))
    ut[hus] = hus_kod
    return ut, andrade


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m data_inlasning.nmd", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="kommando", required=True)
    k = sub.add_parser("klipp", help="Klipp kärna plus buffert ur en nedladdad NMD-fil.")
    k.add_argument("--nmd", required=True, help="Nedladdad NMD-GeoTIFF (EPSG:3006).")
    k.add_argument("--kaerna", required=True, nargs=4, type=float, metavar=("E0", "N0", "E1", "N1"),
                   help="Kärnområdet i EPSG:3011.")
    g = k.add_mutually_exclusive_group(required=True)
    g.add_argument("--natverk", help="Nätverks-id: bufferten väljs efter dess längsta tröskel.")
    g.add_argument("--buffert-m", type=float, help="Buffert i m (2000-5000 rekommenderas).")
    k.add_argument("--ut", required=True, help="Utmapp; nmd.tif och nmd.json skrivs dit.")
    a = p.parse_args(argv)
    buffert = a.buffert_m
    if a.natverk:
        from ekologi.natverk import hamta_natverk

        n = hamta_natverk(a.natverk)
        troskler = [s.troskel_m for s in n.scenarier if s.troskel_m]
        if not troskler:
            raise SystemExit(f"{a.natverk} har ingen tröskel att välja buffert efter.")
        buffert = buffert_for_troskel(max(troskler))
    try:
        lager = klipp_nmd(a.nmd, tuple(a.kaerna), buffert, ut_fil=Path(a.ut) / "nmd.tif")
    except NmdFel as exc:
        print(f"Fel: {exc}", file=sys.stderr)
        return 2
    print(f"Skrev {Path(a.ut) / 'nmd.tif'} ({lager.klasser.shape[1]} x {lager.klasser.shape[0]} celler, buffert {buffert:g} m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
