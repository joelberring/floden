# ruff: noqa: E501
"""Vattenytor ur baskartans strandlinjer: kanaler och sjöar som en plan yta på en känd nivå.

Kommunens höjdmodell (Göteborg 2022) innehåller bottnen i kanaler (Rosenlundskanalen ned till -7 m RH 2000).
Räknar skyfallet på den fylls kanalen med regnvatten som en grop. Här läses strandlinjerna (hydrografi_l,
typ Strandlinje, PolylineZ med z = vattenytans nivå) och markmodellen får en plan vattenyta:

1. Varje cell får närmaste strandlinjepunkts nivå (linjen förtätas så att nivån interpoleras längs den).
2. Celler vars markhöjd ligger minst `marginal_m` under den nivån är kandidater.
3. Fyllning från strandlinjen: bara kandidatområden (4-sammanhängande, även över rutgränser när flera
   rutor beräknas tillsammans via `vattenytor_for_rutor`) som har en cell högst `max_avstand_m`
   från en strandlinje blir vatten. En sänka på land som inte når någon strandlinje fylls inte.
4. Varje sammanhängande vattenyta får en gemensam plan nivå (global median av strandlinjens z längs
   hela ytans strand, i RH 2000), och markslaget sätts till vatten (8).

Lösaren (K1/K6) har inget randvillkor för fast vattennivå eller utlopp: ytan är en plan yta med markslag 8, och
manifestet anmärker det.
"""

from __future__ import annotations

import math
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
        hittat_i_paket = False
        for andelse in (".gpkg", ".shp"):
            for m in zip_medlemmar(p, (andelse,)):
                sokv = vsizip(p, m)
                if andelse == ".gpkg":
                    lager: list[str | None] = [str(r[0]) for r in pyogrio.list_layers(sokv) if monster.search(str(r[0]))]
                else:
                    lager = [None] if monster.search(Path(m).stem) else []
                for lg in lager:
                    hittat_i_paket = True
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
            if hittat_i_paket:
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


def _sammanfoga_komponentpar(
    a_arr: NDArray[np.int32],
    b_arr: NDArray[np.int32],
    off_a: int,
    off_b: int,
    uf_parent: list[int],
) -> None:
    """Slår samman lokala vattenkomponenter som möts i en rutskarv eller ett överlapp."""
    gem = (a_arr > 0) & (b_arr > 0)
    if not np.any(gem):
        return
    par = np.unique(np.column_stack([a_arr[gem], b_arr[gem]]), axis=0)

    def _hitta(x: int) -> int:
        while uf_parent[x] != x:
            uf_parent[x] = uf_parent[uf_parent[x]]
            x = uf_parent[x]
        return x

    for ca, cb in par.tolist():
        ra = _hitta(off_a + int(ca) - 1)
        rb = _hitta(off_b + int(cb) - 1)
        if ra != rb:
            if ra < rb:
                uf_parent[rb] = ra
            else:
                uf_parent[ra] = rb


def vattenytor_for_rutor(
    rutor_och_z: Sequence[tuple[Ruta, NDArray[np.float32]]],
    linjer: Sequence[Linje],
    *,
    marginal_m: float,
    max_avstand_m: float,
) -> list[tuple[NDArray[np.bool_], NDArray[np.float32]]]:
    """Beräknar vattenmask och global plan RH 2000-nivå skarvlöst över en eller flera rutor.

    Sammanhängande vattenytor som korsar rutgränser eller överlapp slås ihop till en gemensam
    komponent över hela mappningsområdet och får en enda global mediannivå (RH 2000) från
    alla unika strandnära celler längs vattenytan, så att grannrutor får exakt samma nivå (0 m steg).
    """
    from scipy import ndimage
    from scipy.spatial import cKDTree

    if not rutor_och_z:
        return []

    px = float(rutor_och_z[0][0].upplosning_m)
    for ruta, z in rutor_och_z:
        if not math.isclose(ruta.upplosning_m, px, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(
                f"Rutorna måste ha samma upplösning för global vattenyta ({ruta.upplosning_m} != {px})."
            )
        if z.shape != ruta.form_rc:
            raise ValueError(
                f"Höjdraster för {ruta.namn} har form {z.shape}, förväntade {ruta.form_rc}."
            )

    tomma = [
        (np.zeros(z.shape, dtype=bool), np.full(z.shape, np.nan, dtype=np.float32))
        for _, z in rutor_och_z
    ]
    punkter = _punkter(linjer, 0.5 * px)
    if len(punkter) == 0:
        return tomma
    punkter = punkter[np.all(np.isfinite(punkter), axis=1)]
    if len(punkter) == 0:
        return tomma

    ue0 = min(r.yttre_bbox[0] for r, _ in rutor_och_z)
    un0 = min(r.yttre_bbox[1] for r, _ in rutor_och_z)
    ue1 = max(r.yttre_bbox[2] for r, _ in rutor_och_z)
    un1 = max(r.yttre_bbox[3] for r, _ in rutor_och_z)
    marg = max(max_avstand_m, 5.0 * px) + 50.0
    punkter = punkter[
        (punkter[:, 0] > ue0 - marg)
        & (punkter[:, 0] < ue1 + marg)
        & (punkter[:, 1] > un0 - marg)
        & (punkter[:, 1] < un1 + marg)
    ]
    if len(punkter) == 0:
        return tomma

    trad = cKDTree(punkter[:, :2])

    delar_lista: list[NDArray[np.int32]] = []
    antal_lista: list[int] = []
    offset_lista: list[int] = []
    strand_prover: list[tuple[NDArray[np.int32], NDArray[np.int64], NDArray[np.int64], NDArray[np.float64]]] = []
    tot_komp = 0

    for ruta, z in rutor_och_z:
        e0, _, _, n1 = ruta.yttre_bbox
        rader, kol = z.shape
        ex = e0 + (np.arange(kol, dtype=np.float64) + 0.5) * px
        ny = n1 - (np.arange(rader, dtype=np.float64) + 0.5) * px
        gx, gy = np.meshgrid(ex, ny)
        d, idx = trad.query(np.column_stack([gx.ravel(), gy.ravel()]))
        avst = np.asarray(d, dtype=np.float64).reshape(z.shape)
        lokal_niva = punkter[np.asarray(idx, dtype=np.intp), 2].reshape(z.shape)
        with np.errstate(invalid="ignore"):
            kandidat = np.isfinite(z) & (z < lokal_niva - marginal_m)
        delar_ra, antal_ra = ndimage.label(kandidat)  # 4-sammanhängande
        delar = np.asarray(delar_ra, dtype=np.int32)
        antal = int(antal_ra)
        delar_lista.append(delar)
        antal_lista.append(antal)
        offset_lista.append(tot_komp)
        tot_komp += antal

        fron = kandidat & (avst <= max_avstand_m)
        fr_r, fr_c = np.nonzero(fron)
        if fr_r.size > 0:
            fr_comp = delar[fr_r, fr_c]
            # Cellcentra ligger på (heltal + 0,5) * px; dra bort 0,5 före rint så varje cell får unik nyckel.
            fr_e = np.rint((ex[fr_c] - ue0) / px - 0.5).astype(np.int64)
            fr_n = np.rint((ny[fr_r] - un0) / px - 0.5).astype(np.int64)
            fr_z = np.asarray(lokal_niva[fr_r, fr_c], dtype=np.float64)
            strand_prover.append((fr_comp, fr_e, fr_n, fr_z))
        else:
            strand_prover.append(
                (
                    np.empty(0, dtype=np.int32),
                    np.empty(0, dtype=np.int64),
                    np.empty(0, dtype=np.int64),
                    np.empty(0, dtype=np.float64),
                )
            )

    if tot_komp == 0:
        return tomma

    uf_parent = list(range(tot_komp))
    tol = 1e-6 * max(1.0, px)
    n_rutor = len(rutor_och_z)
    for ia in range(n_rutor):
        if antal_lista[ia] == 0:
            continue
        da = delar_lista[ia]
        off_a = offset_lista[ia]
        ea0, na0, ea1, na1 = rutor_och_z[ia][0].yttre_bbox
        for ib in range(ia + 1, n_rutor):
            if antal_lista[ib] == 0:
                continue
            db = delar_lista[ib]
            off_b = offset_lista[ib]
            eb0, nb0, eb1, nb1 = rutor_och_z[ib][0].yttre_bbox
            gem_e0 = max(ea0, eb0)
            gem_e1 = min(ea1, eb1)
            gem_n0 = max(na0, nb0)
            gem_n1 = min(na1, nb1)

            if gem_e1 > gem_e0 + tol and gem_n1 > gem_n0 + tol:
                # Överlappande rutor: sammanfoga lokala komponenter i gemensamma celler
                ka0 = max(0, round((gem_e0 - ea0) / px))
                ka1 = min(da.shape[1], round((gem_e1 - ea0) / px))
                ra0 = max(0, round((na1 - gem_n1) / px))
                ra1 = min(da.shape[0], round((na1 - gem_n0) / px))
                kb0 = max(0, round((gem_e0 - eb0) / px))
                kb1 = min(db.shape[1], round((gem_e1 - eb0) / px))
                rb0 = max(0, round((nb1 - gem_n1) / px))
                rb1 = min(db.shape[0], round((nb1 - gem_n0) / px))
                if (ra1 - ra0) == (rb1 - rb0) > 0 and (ka1 - ka0) == (kb1 - kb0) > 0:
                    _sammanfoga_komponentpar(
                        da[ra0:ra1, ka0:ka1], db[rb0:rb1, kb0:kb1], off_a, off_b, uf_parent
                    )
            elif gem_n1 > gem_n0 + tol and (abs(ea1 - eb0) <= tol or abs(eb1 - ea0) <= tol):
                # Öst-västlig rutskarv (4-grannar över den vertikala gränsen)
                ra0 = max(0, round((na1 - gem_n1) / px))
                ra1 = min(da.shape[0], round((na1 - gem_n0) / px))
                rb0 = max(0, round((nb1 - gem_n1) / px))
                rb1 = min(db.shape[0], round((nb1 - gem_n0) / px))
                if (ra1 - ra0) == (rb1 - rb0) > 0:
                    if abs(ea1 - eb0) <= tol:
                        _sammanfoga_komponentpar(
                            da[ra0:ra1, -1], db[rb0:rb1, 0], off_a, off_b, uf_parent
                        )
                    else:
                        _sammanfoga_komponentpar(
                            da[ra0:ra1, 0], db[rb0:rb1, -1], off_a, off_b, uf_parent
                        )
            elif gem_e1 > gem_e0 + tol and (abs(na1 - nb0) <= tol or abs(nb1 - na0) <= tol):
                # Syd-nordlig rutskarv (4-grannar över den horisontella gränsen; rad 0 är längst norrut)
                ka0 = max(0, round((gem_e0 - ea0) / px))
                ka1 = min(da.shape[1], round((gem_e1 - ea0) / px))
                kb0 = max(0, round((gem_e0 - eb0) / px))
                kb1 = min(db.shape[1], round((gem_e1 - eb0) / px))
                if (ka1 - ka0) == (kb1 - kb0) > 0:
                    if abs(na1 - nb0) <= tol:
                        _sammanfoga_komponentpar(
                            da[0, ka0:ka1], db[-1, kb0:kb1], off_a, off_b, uf_parent
                        )
                    else:
                        _sammanfoga_komponentpar(
                            da[-1, ka0:ka1], db[0, kb0:kb1], off_a, off_b, uf_parent
                        )

    def _hitta(x: int) -> int:
        while uf_parent[x] != x:
            uf_parent[x] = uf_parent[uf_parent[x]]
            x = uf_parent[x]
        return x

    # Samla unika strandnära celler per global komponent så överlapp aldrig dubbelräknas i medianen.
    strand_per_rot: dict[int, dict[tuple[int, int], float]] = {}
    for ti, (fr_comp, fr_e, fr_n, fr_z) in enumerate(strand_prover):
        off_ti = offset_lista[ti]
        for c_id, e_i, n_i, z_v in zip(
            fr_comp.tolist(), fr_e.tolist(), fr_n.tolist(), fr_z.tolist(), strict=True
        ):
            rot = _hitta(off_ti + int(c_id) - 1)
            strand_per_rot.setdefault(rot, {})[(int(e_i), int(n_i))] = float(z_v)

    global_niva: dict[int, np.float32] = {
        rot: np.float32(np.median(np.fromiter(celler.values(), dtype=np.float64, count=len(celler))))
        for rot, celler in strand_per_rot.items()
    }

    resultat: list[tuple[NDArray[np.bool_], NDArray[np.float32]]] = []
    for ti in range(n_rutor):
        antal = antal_lista[ti]
        delar = delar_lista[ti]
        if antal == 0 or not global_niva:
            resultat.append(tomma[ti])
            continue
        off_ti = offset_lista[ti]
        lut_mask = np.zeros(antal + 1, dtype=bool)
        lut_niva = np.full(antal + 1, np.nan, dtype=np.float32)
        for c_id in range(1, antal + 1):
            rot = _hitta(off_ti + c_id - 1)
            if rot in global_niva:
                lut_mask[c_id] = True
                lut_niva[c_id] = global_niva[rot]
        resultat.append((lut_mask[delar], lut_niva[delar]))
    return resultat


def vattenytor_i_ruta(
    z: NDArray[np.float32],
    ruta: Ruta,
    linjer: Sequence[Linje],
    *,
    marginal_m: float,
    max_avstand_m: float,
) -> tuple[NDArray[np.bool_], NDArray[np.float32]]:
    """(vattenmask, nivå) för en enskild rutas raster: nivån är satt (annars NaN) där masken är sann."""
    return vattenytor_for_rutor(
        [(ruta, z)], linjer, marginal_m=marginal_m, max_avstand_m=max_avstand_m
    )[0]
