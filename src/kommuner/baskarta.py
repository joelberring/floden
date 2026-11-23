# ruff: noqa: E501
"""Byggnader ur kommunens baskarta (filpaket): takkonturerna klipps ut och sparas som `byggnader.gpkg`.

Göteborgs baskarta (CC0, EPSG:3007, RH 2000, 3D) har lagren TAK (takkonturer), FASAD och BYGGNADSLINJE.
Inget lager har ett attribut för byggnadshöjd. Hur höjden fås:

1. Takkonturens z-värden är takets höjd i RH 2000 (medelvärdet av konturens hörn, `takhojd_rh2000`).
   Floden drar markhöjden (kommunens höjdmodell, K1:s `mark.tif`) ur den vid inbränningen: byggnadshöjden
   är taknivå minus mark under huset.
2. Saknar konturen z (2D-ritning) får byggnaden ingen höjd här: den får standardhöjd med en varning
   (kommunens höjdmodell är en markmodell och innehåller ingen takhöjd). Är Lantmäteriets laser hämtad
   kan höjden i stället tas ur den (se `mappa_geografi`, reserven).

Format, i prioritetsordning: GeoPackage och Shapefile (läses direkt ur ZIP:en), dwgread-JSON och sist
DWG (kräver LibreDWG:s dwgread, `FLODEN_DWGREAD`). Bara geometri och takhöjd sparas (dataminimering).
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kommuner.filpaket import packa_upp_medlem, vsizip, zip_medlemmar
from kommuner.wfs import KommunKallaFel

FORMAT_ORDNING = (".gpkg", ".shp", ".json", ".dwg")


def _id_for(cx: float, cy: float, anvanda: set[str]) -> str:
    bas = f"tak-{round(cx)}-{round(cy)}"
    n, kandidat = 1, bas
    while kandidat in anvanda:
        n += 1
        kandidat = f"{bas}-{n}"
    anvanda.add(kandidat)
    return kandidat


def _polygoner(geom: Any) -> list[tuple[list[tuple[float, float]], float | None]]:
    """Konturer som (ring i xy, z-medel). Slutna linjer och polygoner accepteras."""
    ut: list[tuple[list[tuple[float, float]], float | None]] = []
    if geom is None or geom.is_empty:
        return ut
    delar = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    for d in delar:
        if d.geom_type == "Polygon":
            ring = list(d.exterior.coords)
        elif d.geom_type in ("LineString", "LinearRing"):
            ring = list(d.coords)
            if len(ring) < 4 or ring[0][:2] != ring[-1][:2]:
                continue  # inte sluten: ingen byggnadskontur
        else:
            continue
        xy = [(float(p[0]), float(p[1])) for p in ring]
        zs = [float(p[2]) for p in ring[:-1] if len(p) > 2]
        z = sum(zs) / len(zs) if zs and not all(v == 0.0 for v in zs) else None
        if len(set(xy)) >= 3:
            ut.append((xy, z))
    return ut


def _ur_vektorfiler(
    paket: Sequence[Path], monster: re.Pattern[str], bbox: tuple[float, float, float, float]
) -> tuple[list[tuple[list[tuple[float, float]], float | None]], list[str]]:
    import pyogrio

    ut: list[tuple[list[tuple[float, float]], float | None]] = []
    anv: list[str] = []
    for p in paket:
        for andelse in (".gpkg", ".shp"):
            for m in zip_medlemmar(p, (andelse,)):
                sokv = vsizip(p, m)
                if andelse == ".gpkg":
                    lager = [str(r[0]) for r in pyogrio.list_layers(sokv) if monster.search(str(r[0]))]
                elif monster.search(Path(m).stem):
                    lager = [None]  # type: ignore[list-item]
                else:
                    lager = []
                for lg in lager:
                    gdf = pyogrio.read_dataframe(sokv, layer=lg, bbox=bbox, columns=[]) if lg else pyogrio.read_dataframe(sokv, bbox=bbox, columns=[])
                    anv.append(f"{p.name}:{m}" + (f"[{lg}]" if lg else ""))
                    for g in gdf.geometry:
                        ut.extend(_polygoner(g))
        if anv:
            break
    return ut, anv


def _ur_dwg(
    paket: Sequence[Path], monster: re.Pattern[str], bbox: tuple[float, float, float, float], dwgread: str | None
) -> tuple[list[tuple[list[tuple[float, float]], float | None]], list[str], list[str]]:
    from markmodell.dwg import _las_fil_i_bitar, kor_dwgread, las_dwg_json

    ut: list[tuple[list[tuple[float, float]], float | None]] = []
    anv: list[str] = []
    varn: list[str] = []
    for p in paket:
        medlemmar = zip_medlemmar(p, (".json", ".dwg"))
        medlemmar.sort(key=lambda m: (not m.lower().endswith(".json"), m))
        for m in medlemmar:
            with tempfile.TemporaryDirectory(prefix="floden-baskarta-") as td:
                lokal = packa_upp_medlem(p, m, Path(td))
                json_fil = lokal if lokal.suffix == ".json" else kor_dwgread(lokal, dwgread=dwgread, json_fil=Path(td) / "ritning.json")
                try:
                    objekt, _, trasiga = las_dwg_json(_las_fil_i_bitar(json_fil))
                except Exception as exc:  # GrundkartaFel m.fl.
                    raise KommunKallaFel(f"{m}: kunde inte läsa baskartans DWG/JSON: {exc}") from exc
            if trasiga:
                varn.append(f"{m}: {trasiga} DWG-objekt gick inte att tolka och hoppades över.")
            anv.append(f"{p.name}:{m}")
            for o in objekt:
                if not monster.search(o.lager):
                    continue
                if o.typ == "yta":
                    ringar = [list(r) for r in o.ringar]
                elif o.typ == "linje":
                    ringar = [list(o.punkter)]
                else:
                    continue
                for r in ringar:
                    if len(r) < 4 or r[0] != r[-1] or len(set(r)) < 3:
                        continue
                    if (
                        max(x for x, _ in r) < bbox[0] or min(x for x, _ in r) > bbox[2]
                        or max(y for _, y in r) < bbox[1] or min(y for _, y in r) > bbox[3]
                    ):  # fmt: skip
                        continue
                    ut.append(([(float(x), float(y)) for x, y in r], o.z))
            break  # en ritning räcker
        if anv:
            break
    return ut, anv, varn


def tak_till_gpkg(
    paket: Sequence[Path],
    *,
    bbox_publicering: tuple[float, float, float, float],
    epsg_publicering: int,
    lager_monster: str,
    ut: Path,
    dwgread: str | None = None,
) -> tuple[Path, int, list[str]]:
    """Klipper baskartans takkonturer till `bbox_publicering` och skriver `ut` (GeoPackage, EPSG:3011).

    Kolumner: `byggnad_id` (stabilt, ur konturens mittpunkt), `takhojd_rh2000` (m, tomt utan z).
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    monster = re.compile(lager_monster)
    varningar: list[str] = []
    ringar, anv = _ur_vektorfiler(paket, monster, bbox_publicering)
    if not anv:
        ringar, anv, v = _ur_dwg(paket, monster, bbox_publicering, dwgread)
        varningar.extend(v)
    if not anv:
        raise KommunKallaFel(
            f"Baskartans paket har inget lager som matchar '{lager_monster}' i GeoPackage, Shapefile, "
            "dwgread-JSON eller DWG. Kontrollera paketets innehåll (zip_medlemmar) och [filpaket.baskarta]."
        )
    e0, n0, e1, n1 = bbox_publicering
    rader: list[dict[str, Any]] = []
    geom: list[Any] = []
    anvanda: set[str] = set()
    utan_z = 0
    for xy, z in sorted(ringar, key=lambda r: (round(r[0][0][0], 3), round(r[0][0][1], 3))):
        pol = Polygon(xy)
        if not pol.is_valid:
            pol = pol.buffer(0)
        if pol.is_empty or pol.geom_type != "Polygon" or pol.area < 1.0:
            continue
        c = pol.representative_point()
        if not (e0 <= c.x <= e1 and n0 <= c.y <= n1):
            continue  # hör till en granne: ingår i grannens klippning
        utan_z += z is None
        rader.append({"byggnad_id": _id_for(c.x, c.y, anvanda), "takhojd_rh2000": z})
        geom.append(pol)
    if not geom:
        raise KommunKallaFel(
            f"Inga takkonturer i området (lager '{lager_monster}', E {e0:.0f}-{e1:.0f}, N {n0:.0f}-{n1:.0f}, "
            f"EPSG:{epsg_publicering}). Fel område eller fel lager?"
        )
    gdf = gpd.GeoDataFrame(rader, geometry=geom, crs=f"EPSG:{epsg_publicering}").to_crs(epsg=3011)
    ut.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(ut, driver="GPKG", layer="byggnader")
    varningar.append(f"Byggnader ur baskartans {lager_monster}: {', '.join(anv)}.")
    if utan_z:
        varningar.append(
            f"{utan_z} av {len(rader)} takkonturer saknar höjd (2D): de får standardhöjd i markmodellen."
        )
    return ut, len(rader), varningar
