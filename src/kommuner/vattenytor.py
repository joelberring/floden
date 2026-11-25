# ruff: noqa: E501
"""Vattenytor ur baskartans strandlinjer: kanaler och sjöar som en plan yta på en känd nivå.

Kommunens höjdmodell (Göteborg 2022) innehåller bottnen i kanaler (Rosenlundskanalen ned till -7 m RH 2000).
Räknar skyfallet på den fylls kanalen med regnvatten som en grop. Här läses strandlinjerna (hydrografi_l,
typ Strandlinje, PolylineZ med z = vattenytans nivå) och markmodellen får en plan vattenyta:

1. Varje cell får närmaste strandlinjepunkts nivå (linjen förtätas så att nivån interpoleras längs den).
2. Celler vars markhöjd ligger minst `marginal_m` under den nivån är kandidater.
3. Fyllning från strandlinjen: bara kandidatområden (4-sammanhängande) som har en cell högst `max_avstand_m`
   från en strandlinje blir vatten. En sänka på land som inte når någon strandlinje fylls inte.
4. Markhöjden i vattencellerna sätts till nivån och markslaget till vatten (8).

Lösaren (K1/K6) har inget randvillkor för fast vattennivå eller utlopp: ytan är en plan yta med markslag 8, och
manifestet anmärker det. Nivån är den närmaste strandlinjens z per cell, inte ett medel per vattenyta.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from kommuner.filpaket import vsizip, zip_medlemmar
from kommuner.konfig import VattenKonfig
from kommuner.wfs import KommunKallaFel
from markmodell.rutnat import Ruta

Linje = NDArray[np.float64]  # (n, 3): E, N (EPSG:3011), z (RH 2000)


def las_strandlinjer(
    paket: Sequence[Path],
    vatten: VattenKonfig,
    *,
    bbox_publicering: tuple[float, float, float, float],
    epsg_publicering: int,
) -> tuple[list[Linje], list[str]]:
    """Strandlinjer med z ur Shapefile/GeoPackage i paketen, klippta till bbox och omräknade till EPSG:3011."""
    import pyogrio
    from pyproj import Transformer

    monster = re.compile(vatten.lager_monster)
    objekt = re.compile(vatten.objekt_monster)
    linjer: list[Linje] = []
    varn: list[str] = []
    utan_z = 0
    sedda: set[str] = set()
    hittat_lager = False
    tr = Transformer.from_crs(epsg_publicering, 3011, always_xy=True)
    for p in paket:
        for andelse in (".gpkg", ".shp"):
            for m in zip_medlemmar(p, (andelse,)):
                sokv = vsizip(p, m)
                if andelse == ".gpkg":
                    lager: list[str | None] = [str(r[0]) for r in pyogrio.list_layers(sokv) if monster.search(str(r[0]))]
                else:
                    lager = [None] if monster.search(Path(m).stem) else []
                for lg in lager:
                    hittat_lager = True
                    kw: dict[str, Any] = {"bbox": bbox_publicering, "columns": [vatten.objekt_falt]}
                    gdf = pyogrio.read_dataframe(sokv, layer=lg, **kw) if lg else pyogrio.read_dataframe(sokv, **kw)
                    if vatten.objekt_falt not in gdf.columns:
                        raise KommunKallaFel(f"Baskartans lager {m} saknar fältet '{vatten.objekt_falt}' ([filpaket.baskarta.vatten] objekt_falt).")
                    varden = gdf[vatten.objekt_falt].astype(str)
                    valda = varden.map(lambda x: bool(objekt.search(x)))
                    sedda.update(varden[~valda].unique().tolist()[:50])
                    for g in gdf.geometry[valda.to_numpy()]:
                        if g is None or g.is_empty:
                            continue
                        for d in (list(g.geoms) if hasattr(g, "geoms") else [g]):
                            if d.geom_type not in ("LineString", "LinearRing") or not d.has_z:
                                utan_z += d.geom_type in ("LineString", "LinearRing")
                                continue
                            c = np.asarray(d.coords, dtype=np.float64)
                            e, n = tr.transform(c[:, 0], c[:, 1])
                            linjer.append(np.column_stack([e, n, c[:, 2]]))
            if hittat_lager:
                break
        if hittat_lager:
            break
    if not hittat_lager:
        varn.append(f"Vattenytor: inget lager i baskartan matchar '{vatten.lager_monster}'; inga vattenytor.")
    elif not linjer:
        varn.append(
            f"Vattenytor: inga strandlinjer med {vatten.objekt_falt} som matchar '{vatten.objekt_monster}' i området"
            + (f" (värden som finns: {', '.join(sorted(sedda)[:20])})" if sedda else "") + "."
        )
    if utan_z:
        varn.append(f"Vattenytor: {utan_z} strandlinjer saknar höjd (2D) och hoppades över.")
    return linjer, varn


def skriv_strandlinjer(linjer: Sequence[Linje], ut: Path) -> Path:
    """Sparar strandlinjerna (EPSG:3011, med z) i geografins rådata, så att skyfallsrutan kan byggas om."""
    import geopandas as gpd
    from shapely.geometry import LineString

    ut.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(
        {"nr": list(range(len(linjer)))}, geometry=[LineString(c) for c in linjer], crs="EPSG:3011"
    ).to_file(ut, driver="GPKG", layer="strandlinjer")
    return ut


def las_strandlinjer_fil(fil: Path) -> list[Linje]:
    import geopandas as gpd

    gdf = gpd.read_file(fil, layer="strandlinjer")
    return [np.asarray(g.coords, dtype=np.float64) for g in gdf.geometry if g is not None and not g.is_empty and g.has_z]


def _punkter(linjer: Sequence[Linje], steg_m: float) -> NDArray[np.float64]:
    """Förtätar linjerna så att ingen punkt är längre än `steg_m` från nästa; z interpoleras linjärt."""
    ut: list[NDArray[np.float64]] = []
    for c in linjer:
        if len(c) < 2:
            ut.append(c)
            continue
        d = np.hypot(np.diff(c[:, 0]), np.diff(c[:, 1]))
        s = np.concatenate([[0.0], np.cumsum(d)])
        if s[-1] <= 0.0:
            ut.append(c[:1])
            continue
        t = np.arange(0.0, s[-1], steg_m)
        t = np.append(t, s[-1])
        ut.append(np.column_stack([np.interp(t, s, c[:, k]) for k in range(3)]))
    return np.vstack(ut) if ut else np.empty((0, 3))


def vattenytor_i_ruta(
    z: NDArray[np.float32],
    ruta: Ruta,
    linjer: Sequence[Linje],
    *,
    marginal_m: float,
    max_avstand_m: float,
) -> tuple[NDArray[np.bool_], NDArray[np.float32]]:
    """(vattenmask, nivå) för rutans raster: nivån är satt (annars NaN) där masken är sann."""
    from scipy import ndimage
    from scipy.spatial import cKDTree

    mask = np.zeros(z.shape, dtype=bool)
    niva = np.full(z.shape, np.nan, dtype=np.float32)
    px = ruta.upplosning_m
    e0, n0, e1, n1 = ruta.yttre_bbox
    marg = max(max_avstand_m, 5.0 * px) + 50.0
    punkter = _punkter(linjer, 0.5 * px)
    if len(punkter) == 0:
        return mask, niva
    punkter = punkter[
        (punkter[:, 0] > e0 - marg) & (punkter[:, 0] < e1 + marg) & (punkter[:, 1] > n0 - marg) & (punkter[:, 1] < n1 + marg)
    ]
    if len(punkter) == 0:
        return mask, niva
    rader, kol = z.shape
    ex = e0 + (np.arange(kol) + 0.5) * px
    ny = n1 - (np.arange(rader) + 0.5) * px
    gx, gy = np.meshgrid(ex, ny)
    d, i = cKDTree(punkter[:, :2]).query(np.column_stack([gx.ravel(), gy.ravel()]))
    avst = d.reshape(z.shape)
    lokal_niva = punkter[i, 2].reshape(z.shape)
    with np.errstate(invalid="ignore"):
        kandidat = np.isfinite(z) & (z < lokal_niva - marginal_m)
    delar, antal = ndimage.label(kandidat)  # 4-sammanhängande
    if antal == 0:
        return mask, niva
    fron = kandidat & (avst <= max_avstand_m)
    nara = np.unique(delar[fron])
    nara = nara[nara > 0]
    mask = np.isin(delar, nara)
    # En plan nivå per vattenyta: median av strandlinjens z längs ytans strand (inte närmaste punkt per
    # cell, som ger steg där strandlinjernas z varierar).
    for d_id in nara:
        del_mask = delar == d_id
        strand = del_mask & fron
        niva[del_mask] = np.float32(np.median(lokal_niva[strand]))
    return mask, niva
