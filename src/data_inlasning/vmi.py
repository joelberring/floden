# ruff: noqa: E501
"""Våtmarksinventeringen (VMI) som lager "känd våtmark" (äldre kvalitetsinformation).

Källa: Naturvårdsverket, Våtmarksinventeringen (VMI), nationella GIS-skikt, inventerad 1981-2005
(per län under https://geodata.naturvardsverket.se/nedladdning/VMI/, mappen `5_GIS_skikt`; exakta
filnamn är inte kontrollerade härifrån, containern når inte Naturvårdsverket). Lagret är **äldre** än
dagens verklighet: det visas och används som kvalitetsinformation och som valbart habitat för groddjurs-
och våtmarksnätverk, aldrig som aktuell utbredning. Alla polygoner i det inlästa skiktet räknas som
känd våtmark; inga attributvärden tolkas.

Kommando (ett per rad, se docs/DRIFT.md):
    python -m data_inlasning.vmi klipp --vmi VMI.gpkg --kaerna E0 N0 E1 N1 --buffert-m 3000 --ut ekologi/
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from data_inlasning.nmd import EPSG_ANALYS, NmdFel, NmdLager
from gemensamt.hash import sha256_fil
from testsvit.k1_io import las_geotiff, skriv_geotiff

VMI_KALLA = "Naturvårdsverket, Våtmarksinventeringen (VMI), inventerad 1981-2005"
VMI_ALDER_TEXT = "Äldre inventering (1981-2005); visar känd våtmark som kvalitetsinformation, inte aktuell utbredning."


def rastrera_vmi(
    vmi_fil: str | Path,
    kaerna_3011: tuple[float, float, float, float],
    buffert_m: float,
    *,
    cell_m: float = 10.0,
    lagernamn: str | None = None,
    ut_fil: str | Path | None = None,
) -> NmdLager:
    """Rasterar VMI-polygoner (valfri CRS) till en 0/1-mask i EPSG:3011 på samma rutnät som NMD."""
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    e0, n0, e1, n1 = kaerna_3011
    x0 = math.floor((e0 - buffert_m) / cell_m) * cell_m
    x1 = math.ceil((e1 + buffert_m) / cell_m) * cell_m
    y0 = math.floor((n0 - buffert_m) / cell_m) * cell_m
    y1 = math.ceil((n1 + buffert_m) / cell_m) * cell_m
    bredd, hojd = round((x1 - x0) / cell_m), round((y1 - y0) / cell_m)
    try:
        gdf = gpd.read_file(vmi_fil, layer=lagernamn)
    except Exception as exc:  # noqa: BLE001 - fiona/pyogrio ger många feltyper
        raise NmdFel(f"VMI-filen {Path(vmi_fil).name} går inte att läsa: {exc}") from exc
    if gdf.crs is None:
        raise NmdFel("VMI-filen saknar koordinatsystem.")
    gdf = gdf.to_crs(EPSG_ANALYS)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if not set(gdf.geometry.geom_type.unique()) <= {"Polygon", "MultiPolygon"}:
        raise NmdFel("VMI-skiktet ska innehålla polygoner.")
    from shapely.geometry import box

    gdf = gdf[gdf.intersects(box(x0, y0, x1, y1))]
    mask = np.zeros((hojd, bredd), dtype=np.uint8)
    if len(gdf):
        mask = rasterize(
            ((g, 1) for g in gdf.geometry),
            out_shape=(hojd, bredd),
            transform=from_origin(x0, y1, cell_m, cell_m),
            fill=0,
            dtype="uint8",
            all_touched=False,
        )
    lager = NmdLager(mask.astype(np.uint8), x0, y1, cell_m)
    if ut_fil is not None:
        skriv_vmi(lager, ut_fil, vmi_fil=Path(vmi_fil), antal_polygoner=int(len(gdf)))
    return lager


def skriv_vmi(
    lager: NmdLager, ut_fil: str | Path, *, vmi_fil: Path | None = None, antal_polygoner: int | None = None
) -> Path:
    fil = Path(ut_fil)
    fil.parent.mkdir(parents=True, exist_ok=True)
    skriv_geotiff(fil, lager.klasser, x_min=lager.x_min_m, y_max=lager.y_max_m, pixelstorlek=lager.cell_m)
    meta: dict[str, Any] = {
        "format": "floden-kand-vatmark/v1",
        "lager": "känd våtmark",
        "kalla": VMI_KALLA,
        "alder": "äldre",
        "alder_text": VMI_ALDER_TEXT,
        "epsg": EPSG_ANALYS,
        "cellstorlek_m": lager.cell_m,
        "utbredning_3011": list(lager.utbredning),
        "antal_polygoner_i_utsnittet": antal_polygoner,
        "kallfil": vmi_fil.name if vmi_fil else None,
        "kallfil_sha256": sha256_fil(vmi_fil) if vmi_fil and vmi_fil.is_file() else None,
        "sha256": sha256_fil(fil),
    }
    fil.with_suffix(".json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return fil


def las_vmi_mask(sokvag: str | Path) -> NmdLager:
    """Läser `kand_vatmark.tif` (uint8, 0/1, EPSG:3011)."""
    arr, geo = las_geotiff(sokvag)
    if geo.epsg != EPSG_ANALYS or arr.dtype != np.uint8:
        raise NmdFel(f"{Path(sokvag).name} ska vara uint8 i EPSG:{EPSG_ANALYS}.")
    if int(arr.max(initial=0)) > 1:
        raise NmdFel("Känd våtmark ska vara en 0/1-mask.")
    return NmdLager(np.asarray(arr, dtype=np.uint8), geo.x_min, geo.y_max, geo.pixelstorlek_x)


def stampla_pa_grid(
    mask: NmdLager, x_min_m: float, y_max_m: float, cell_m: float, form: tuple[int, int]
) -> NDArray[np.bool_]:
    """Maskens värden vid mittpunkten av varje cell i ett annat rutnät (närmaste granne)."""
    ny, nx = form
    e = x_min_m + (np.arange(nx) + 0.5) * cell_m
    n = y_max_m - (np.arange(ny) + 0.5) * cell_m
    kol = np.floor((e - mask.x_min_m) / mask.cell_m).astype(int)
    rad = np.floor((mask.y_max_m - n) / mask.cell_m).astype(int)
    ok_k = (kol >= 0) & (kol < mask.klasser.shape[1])
    ok_r = (rad >= 0) & (rad < mask.klasser.shape[0])
    ut = np.zeros(form, dtype=bool)
    rr, kk = np.meshgrid(np.flatnonzero(ok_r), np.flatnonzero(ok_k), indexing="ij")
    ut[rr, kk] = mask.klasser[rad[rr], kol[kk]] > 0
    return ut


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m data_inlasning.vmi")
    sub = p.add_subparsers(dest="kommando", required=True)
    k = sub.add_parser("klipp", help="Rastrera VMI-polygoner för kärna plus buffert.")
    k.add_argument("--vmi", required=True, help="Nedladdat VMI-skikt (GeoPackage/Shape).")
    k.add_argument("--lager", default=None, help="Lagernamn i filen (om flera).")
    k.add_argument("--kaerna", required=True, nargs=4, type=float, metavar=("E0", "N0", "E1", "N1"))
    k.add_argument("--buffert-m", type=float, default=3000.0)
    k.add_argument("--ut", required=True)
    a = p.parse_args(argv)
    try:
        lager = rastrera_vmi(a.vmi, tuple(a.kaerna), a.buffert_m, lagernamn=a.lager,
                             ut_fil=Path(a.ut) / "kand_vatmark.tif")
    except NmdFel as exc:
        print(f"Fel: {exc}", file=sys.stderr)
        return 2
    print(f"Skrev {Path(a.ut) / 'kand_vatmark.tif'} ({int(lager.klasser.sum())} vattmarksceller). {VMI_ALDER_TEXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
