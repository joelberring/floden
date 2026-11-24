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


class _IngaKonturer(Exception):
    """Inga konturer kvar efter klippning (intern; kallaren skriver felet)."""


def _ringar_till_gpkg(
    ringar: Sequence[tuple[list[tuple[float, float]], float | None]],
    bbox_publicering: tuple[float, float, float, float],
    epsg_publicering: int,
    ut: Path,
) -> tuple[Path, int, int]:
    """Skriver konturerna som `byggnader.gpkg` (EPSG:3011). Returnerar (fil, antal, antal utan z)."""
    from shapely.geometry import Polygon

    return _polygoner_till_gpkg(
        [(Polygon(xy), z) for xy, z in ringar], bbox_publicering, epsg_publicering, ut
    )


def _polygoner_till_gpkg(
    polygoner: Sequence[tuple[Any, float | None]],
    bbox_publicering: tuple[float, float, float, float],
    epsg_publicering: int,
    ut: Path,
) -> tuple[Path, int, int]:
    """Skriver polygonerna (med eventuella hål) som `byggnader.gpkg` (EPSG:3011): (fil, antal, antal utan z)."""
    import geopandas as gpd

    e0, n0, e1, n1 = bbox_publicering
    rader: list[dict[str, Any]] = []
    geom: list[Any] = []
    anvanda: set[str] = set()
    utan_z = 0

    def _nyckel(pz: tuple[Any, float | None]) -> tuple[float, float]:
        x, y = pz[0].exterior.coords[0][:2]
        return round(x, 3), round(y, 3)

    for pol, z in sorted(polygoner, key=_nyckel):
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
        raise _IngaKonturer
    gdf = gpd.GeoDataFrame(rader, geometry=geom, crs=f"EPSG:{epsg_publicering}").to_crs(epsg=3011)
    ut.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(ut, driver="GPKG", layer="byggnader")
    return ut, len(rader), utan_z


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
    try:
        _, antal, utan_z = _ringar_till_gpkg(ringar, bbox_publicering, epsg_publicering, ut)
    except _IngaKonturer:
        raise KommunKallaFel(
            f"Inga takkonturer i området (lager '{lager_monster}', E {e0:.0f}-{e1:.0f}, N {n0:.0f}-{n1:.0f}, "
            f"EPSG:{epsg_publicering}). Fel område eller fel lager?"
        ) from None
    varningar.append(f"Byggnader ur baskartans {lager_monster}: {', '.join(anv)}.")
    if utan_z:
        varningar.append(
            f"{utan_z} av {antal} takkonturer saknar höjd (2D): de får standardhöjd i markmodellen."
        )
    return ut, antal, varningar


# --- Lokal baskartefil (Stockholms 3D-baskarta; INTE öppen) -----------------------------------------------
# Filen ligger i serverns lokala datamapp (aldrig i git). Inget filnamn, ingen sökväg och inget lagernamn skrivs
# i varningar eller felmeddelanden: bara antal och filens nummer i anropet. Resultatet (`byggnader.gpkg`) är
# härlett direkt ur baskartan och ska alltid behandlas som stängt (geografi.mappa gör det).

Z_MIN_M = -50.0
Z_MAX_M = 500.0
HOJD_FRAN = ("takkontur_z", "laser")


def _rensa(text: str, fil: Path, nr: int) -> str:
    """Tar bort filens namn och sökväg ur en feltext."""
    for del_ in (str(fil), str(fil.resolve()), fil.name):
        if del_:
            text = text.replace(del_, f"baskartefil {nr}")
    return text


def _lokala_objekt(filer: Sequence[Path], dwgread: str | None) -> tuple[list[Any], list[str]]:
    """DwgObjekt ur alla filer (ZIP med dwgread-JSON eller DWG, en DWG, en dwgread-JSON)."""
    from markmodell.dwg import _las_fil_i_bitar, kor_dwgread, las_dwg_json

    objekt: list[Any] = []
    varn: list[str] = []
    for nr, fil in enumerate(filer, start=1):
        if not fil.is_file():
            raise KommunKallaFel(f"Baskartefil {nr} finns inte (kontrollera sökvägen, i containern t.ex. under /app/data/lokal/).")
        andelse = fil.suffix.lower()
        with tempfile.TemporaryDirectory(prefix="floden-lokal-baskarta-") as td:
            if andelse == ".zip":
                try:
                    medlemmar = zip_medlemmar(fil, (".json", ".dwg"))
                except KommunKallaFel as exc:  # texten nämner filens namn
                    raise KommunKallaFel(_rensa(str(exc), fil, nr)) from None
                if not medlemmar:
                    raise KommunKallaFel(f"Baskartefil {nr} (ZIP) har ingen .dwg eller dwgread-.json.")
                delar = [packa_upp_medlem(fil, m, Path(td)) for m in medlemmar]
            elif andelse in (".dwg", ".json"):
                delar = [fil]
            else:
                raise KommunKallaFel(f"Baskartefil {nr}: filtypen ska vara .zip, .dwg eller .json (dwgread).")
            for i, d in enumerate(delar, start=1):
                try:
                    json_fil = d if d.suffix.lower() == ".json" else kor_dwgread(d, dwgread=dwgread, json_fil=Path(td) / f"ritning-{nr}-{i}.json")
                    obj, _, trasiga = las_dwg_json(_las_fil_i_bitar(json_fil))
                except Exception as exc:  # GrundkartaFel m.fl.; texten kan nämna filnamn
                    raise KommunKallaFel(
                        f"Baskartefil {nr}: kunde inte läsas som DWG/dwgread-JSON: {_rensa(str(exc), fil, nr)}"
                    ) from None
                if trasiga:
                    varn.append(f"Baskartefil {nr}: {trasiga} DWG-objekt gick inte att tolka och hoppades över.")
                objekt.extend(obj)
    return objekt, varn


def _ringar_ur(o: Any) -> list[list[tuple[float, float]]]:
    ringar = [list(r) for r in o.ringar] if o.typ == "yta" else [list(o.punkter)]
    return [
        [(float(x), float(y)) for x, y in r]
        for r in ringar
        if len(r) >= 4 and r[0] == r[-1] and len(set(r)) >= 3
    ]


def _i_bbox(r: Sequence[tuple[float, float]], bbox: tuple[float, float, float, float]) -> bool:
    return not (
        max(x for x, _ in r) < bbox[0] or min(x for x, _ in r) > bbox[2]
        or max(y for _, y in r) < bbox[1] or min(y for _, y in r) > bbox[3]
    )  # fmt: skip


def byggnader_ur_lokala_filer(
    filer: Sequence[Path],
    *,
    bbox_publicering: tuple[float, float, float, float],
    epsg_publicering: int,
    lagerprofil: Any,
    ut: Path,
    lager_monster: str = "",
    hojd_fran: str = "takkontur_z",
    z_tillagg_m: float = 0.0,
    dwgread: str | None = None,
) -> tuple[Path, int, list[str]]:
    """Byggnadsytor ur en lokal baskartefil (Stockholms 3D-baskarta) klippta till `bbox_publicering`.

    Vilka lager som är byggnader:
    - `lager_monster` tomt: lagerprofilens Stockholmsgrammatik (`[stockholm]`, `byggnad` och ytreglerna).
      Byggnadens yta (gy) ger huset; byggnadens linje (gl) används bara för att ge ytan en höjd (z).
    - `lager_monster` satt: varje sluten linje eller yta på lager vars namn matchar uttrycket är ett hus.

    Höjd (`hojd_fran`): `takkontur_z` = medel-z för konturens hörn (+ `z_tillagg_m`) sparas som `takhojd_rh2000`;
    byggnadshöjden blir då taknivå minus mark. `laser` = ingen takhöjd sparas, höjden skattas ur Lantmäteriets laser
    (oklassade punkter inom huset). Okontrollerat: att baskartans z är RH 2000 (annars `z_tillagg_m`), att z är
    takets höjd och inte fotens, och att hörnens medel är representativt för taket.
    """
    from shapely import STRtree
    from shapely.geometry import Polygon
    from shapely.geometry.base import BaseGeometry

    from markmodell.stockholm_lager import bestam_atgard, tolka_lagernamn

    if hojd_fran not in HOJD_FRAN:
        raise KommunKallaFel(f"hojd_fran ska vara en av {HOJD_FRAN}.")
    if not filer:
        raise KommunKallaFel("Ingen baskartefil angiven (--baskarta-fil).")
    gram = getattr(lagerprofil, "stockholm", None)
    monster = re.compile(lager_monster) if lager_monster else None
    if monster is None and gram is None:
        raise KommunKallaFel("Lagerprofilen har ingen [stockholm]-grammatik och inget lagermönster är angivet.")
    objekt, varningar = _lokala_objekt(filer, dwgread)

    ytor: list[BaseGeometry] = []
    konturer: list[tuple[BaseGeometry, float | None]] = []
    for o in objekt:
        if o.typ not in ("yta", "linje"):
            continue
        if monster is not None:
            if not monster.search(o.lager):
                continue
            ar_yta = o.typ == "yta"
        else:
            assert gram is not None
            if o.typ == "yta":
                b = bestam_atgard(gram, o.lager, "yta")
                if b is None or b.atgard != "byggnad":
                    continue
                ar_yta = True
            else:
                t = tolka_lagernamn(o.lager)
                if (
                    t is None or t.geometri != "gl" or t.status in gram.hoppa_over_status
                    or gram.hoppa_over.search(t.text) or not gram.byggnad.search(t.text)
                ):  # fmt: skip
                    continue
                ar_yta = False
        ringar = [r for r in _ringar_ur(o) if _i_bbox(r, bbox_publicering)]
        if not ringar:
            continue
        if ar_yta:
            # Skraffyrens banor: jämn-udda-regeln ger hål (innergårdar).
            geom = Polygon(ringar[0]).buffer(0)
            for r in ringar[1:]:
                geom = geom.symmetric_difference(Polygon(r).buffer(0))
            if not geom.is_empty:
                ytor.append(geom)
        else:
            for r in ringar:
                p = Polygon(r).buffer(0)
                if not p.is_empty:
                    konturer.append((p, o.z))
    del objekt

    ogiltig_z = 0

    def _z(z: float | None) -> float | None:
        nonlocal ogiltig_z
        if z is None or z == 0.0:
            return None
        if not (Z_MIN_M <= z + z_tillagg_m <= Z_MAX_M):
            ogiltig_z += 1
            return None
        return float(z) + z_tillagg_m

    def _delar(g: BaseGeometry) -> list[Any]:
        return [g] if g.geom_type == "Polygon" else [d for d in getattr(g, "geoms", []) if d.geom_type == "Polygon"]

    traed = STRtree([k for k, _ in konturer]) if konturer else None
    anvanda: set[int] = set()
    polygoner: list[tuple[Any, float | None]] = []
    for yta in ytor:
        for del_ in _delar(yta):
            z_ur: list[float] = []
            if traed is not None:
                for i in traed.query(del_):
                    kontur, kz = konturer[int(i)]
                    if del_.intersection(kontur).area > 0.5 * min(del_.area, kontur.area):
                        anvanda.add(int(i))
                        z = _z(kz) if hojd_fran == "takkontur_z" else None
                        if z is not None:
                            z_ur.append(z)
            polygoner.append((del_, sum(z_ur) / len(z_ur) if z_ur else None))
    # Konturer som inte hör till någon yta är också hus (ritningen kan sakna skraffyr).
    ensamma = 0
    for i, (kontur, kz) in enumerate(konturer):
        if i in anvanda:
            continue
        for del_ in _delar(kontur):
            polygoner.append((del_, _z(kz) if hojd_fran == "takkontur_z" else None))
            ensamma += 1
    # Dubbletter (överlappande filer): samma läge och yta räknas en gång.
    sedda: set[tuple[int, int, int]] = set()
    unika: list[tuple[Any, float | None]] = []
    for pol, z in polygoner:
        c = pol.representative_point()
        nyckel = (round(c.x * 10), round(c.y * 10), round(pol.area * 10))
        if nyckel not in sedda:
            sedda.add(nyckel)
            unika.append((pol, z))
    try:
        _, antal, utan_z = _polygoner_till_gpkg(unika, bbox_publicering, epsg_publicering, ut)
    except _IngaKonturer:
        raise KommunKallaFel(
            "Inga byggnader i området ur baskartefilerna. Fel område, fel koordinatsystem "
            f"(EPSG:{epsg_publicering} antas) eller inga lager som tolkas som byggnader enligt lagerprofilen?"
        ) from None
    varningar.append(f"Byggnader ur lokal baskarta ({len(filer)} fil(er)): {antal} byggnadsytor.")
    if hojd_fran == "laser":
        varningar.append("Byggnadshöjd: ur Lantmäteriets laser (oklassade punkter), inte ur baskartans z.")
    else:
        varningar.append(
            "Byggnadshöjd: baskartans z (taknivå) minus mark. OKONTROLLERAT: att z är RH 2000 "
            f"(tillägg {z_tillagg_m:g} m), att z är takets och inte fotens höjd, och att medel av hörnen är representativt."
        )
        if utan_z:
            varningar.append(f"{utan_z} av {antal} byggnader saknar giltig takhöjd i baskartan: höjden skattas ur lasern eller får standardhöjd.")
    if ogiltig_z:
        varningar.append(f"{ogiltig_z} konturer hade z utanför {Z_MIN_M:g}-{Z_MAX_M:g} m och ignorerades.")
    if ensamma:
        varningar.append(f"{ensamma} byggnader kommer ur en kontur utan yta på byggnadslagret.")
    return ut, antal, varningar
