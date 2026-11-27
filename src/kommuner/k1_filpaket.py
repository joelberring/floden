# ruff: noqa: E501
"""K1 v2-rutor ur kommunens filpaket: höjdmodell (markhöjd) och baskartans takkonturer (byggnader).

Motsvarar `lantmateriet.bygg.bygg_omrade` men utan laserpunktmoln: markhöjden är kommunens höjdmodell
(`Hojdmodell.grund_z`), byggnaderna kommer ur `byggnader.gpkg` (se `kommuner.baskarta`), och takhöjden
är takkonturens z (RH 2000). Ingen höjd ur laser (`byggnadshojd_fran_laser=False`). Byggnader utan z får
standardhöjd med varning. Källorna skrivs som `KallPost` i varje rutas `recept.json`/`run.json`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from data_inlasning.nmd import EPSG_NMD, las_crs_epsg
from gemensamt.hash import sha256_fil
from kommuner.baskarta import rasterisera_trad_vegetation
from kommuner.filpaket import Filpaket
from kommuner.hojdmodell import (
    NAN_VARNING_TROSKEL_ANDEL,
    HojdLuckDiagnos,
    Hojdmodell,
)
from kommuner.vattenytor import Linje, vattenytor_for_rutor
from lantmateriet.omrade import Omrade, rutor_for_omrade
from markmodell.brytlinjer import Brytlinje
from markmodell.byggnadskallor import las_byggnadskalla
from markmodell.hinder_oppningar import BERG, GRAS, HARDGJORT, SKOG, VATTEN
from markmodell.pipeline import PipelineFel, bygg_markmodell_ruta
from markmodell.recept import KallPost
from markmodell.rutnat import Ruta


def nmd_raster_for_ruta(nmd_fil: Path, ruta: Ruta) -> NDArray[np.uint8]:
    """Omprojicerar NMD-basskiktet (EPSG:3006) med närmaste granne till `ruta` (EPSG:3011)."""
    import rasterio
    from affine import Affine
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import from_bounds

    e0, n0, e1, n1 = ruta.yttre_bbox
    with rasterio.open(nmd_fil) as ds:
        src_epsg = las_crs_epsg(ds.crs) or EPSG_NMD
        sb = transform_bounds(3011, src_epsg, e0, n0, e1, n1, densify_pts=21)
        marg = 2.0 * max(abs(float(ds.res[0])), abs(float(ds.res[1])))
        fonster = from_bounds(sb[0] - marg, sb[1] - marg, sb[2] + marg, sb[3] + marg, ds.transform)
        fonster = fonster.round_offsets().round_lengths()
        data = ds.read(1, window=fonster, boundless=True, fill_value=0)
        src_transform = ds.window_transform(fonster)
        mal = np.zeros(ruta.form_rc, dtype=np.uint8)
        reproject(
            source=data.astype(np.uint8),
            destination=mal,
            src_transform=src_transform,
            src_crs=ds.crs if ds.crs is not None else f"EPSG:{src_epsg}",
            dst_transform=Affine(*ruta.affine_parametrar),
            dst_crs="EPSG:3011",
            resampling=Resampling.nearest,
            src_nodata=None,
            dst_nodata=0,
        )
    return mal


def nmd_till_k1_markslag(nmd_koder: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Översätter NMD-basskiktets klasskoder till K1-markslag (byggnader bränns in ovanpå senare)."""
    ut = np.full(nmd_koder.shape, HARDGJORT, dtype=np.uint8)
    ut[(nmd_koder == 2) | (nmd_koder == 41) | (nmd_koder == 42)] = GRAS
    ut[((nmd_koder >= 111) & (nmd_koder <= 118)) | ((nmd_koder >= 121) & (nmd_koder <= 128))] = SKOG
    ut[nmd_koder == 3] = BERG
    ut[(nmd_koder == 61) | (nmd_koder == 62)] = VATTEN
    return ut


@dataclass
class FilpaketByggResultat:
    ut: Path
    rutor: list[str] = field(default_factory=list)
    varningar: list[str] = field(default_factory=list)
    vatten_m2: float = 0.0
    vatten_niva_m: tuple[float, float] | None = None
    hojd_luckor: list[HojdLuckDiagnos] = field(default_factory=list)
    hog_nan_flagga: bool = False
    max_nan_andel: float = 0.0


def bygg_omrade_filpaket(
    omrade: Omrade,
    ut: Path,
    *,
    hojdmodell: Hojdmodell,
    hojd_paket: Sequence[Filpaket],
    byggnader_fil: Path | None,
    byggnader_paket: Sequence[Filpaket] = (),
    upplosning_m: float = 1.0,
    ruta_storlek_m: float = 500.0,
    en_ruta: bool = False,
    skriv: Callable[[str], None] = print,
    strandlinjer: Sequence[Linje] = (),
    vatten_barriarer: Sequence[Linje] = (),
    vatten_marginal_m: float = 0.2,
    vatten_max_avstand_m: float = 2.0,
    nan_varning_andel: float = NAN_VARNING_TROSKEL_ANDEL,
    brytlinjer: Sequence[Brytlinje] = (),
    nmd_fil: Path | None = None,
    trad_punkter: Sequence[tuple[float, float, str]] = (),
    trad_kronradie_m: float = 3.5,
    trad_hojd_m: float = 8.0,
) -> FilpaketByggResultat:
    res = FilpaketByggResultat(ut=ut)
    kandidater = []
    bygg_sha = None
    if byggnader_fil is not None and byggnader_fil.is_file():
        bk = las_byggnadskalla(byggnader_fil)
        kandidater = list(bk.kandidater)
        bygg_sha = bk.sha256
        res.varningar.extend(bk.varningar)
        skriv(f"Byggnader: {len(kandidater)} ur {byggnader_fil.name}")
    else:
        res.varningar.append("Inga byggnader: marken byggs utan hus.")
    if any(k.tak_rh2000 is None and k.hojd_m is None for k in kandidater):
        n = sum(1 for k in kandidater if k.tak_rh2000 is None and k.hojd_m is None)
        res.varningar.append(f"{n} byggnader saknar takhöjd och får standardhöjd.")

    nmd_sha = sha256_fil(nmd_fil) if nmd_fil is not None and nmd_fil.is_file() else None

    if en_ruta:
        e0, n0, e1, n1 = omrade.bbox
        e_min, n_min = math.floor(e0), math.floor(n0)
        sida = max(e1 - e_min, n1 - n_min)
        storlek = math.ceil(sida / upplosning_m) * upplosning_m
        rutlista = [Ruta(float(e_min), float(n_min), storlek, 0.0, upplosning_m)]
    else:
        rutlista = [Ruta(e, n, ruta_storlek_m, 0.0, upplosning_m) for e, n in rutor_for_omrade(omrade.polygon, ruta_storlek_m)]
    skriv(f"{len(rutlista)} ruta/rutor, upplösning {upplosning_m:g} m (kommunens höjdmodell)")
    ut.mkdir(parents=True, exist_ok=True)
    def _las_ruta_z(ruta: Ruta) -> tuple[NDArray[np.float32], list[str], HojdLuckDiagnos]:
        if hasattr(hojdmodell, "grund_z_med_diagnos"):
            return hojdmodell.grund_z_med_diagnos(ruta, nan_varning_andel=nan_varning_andel)
        z_ra, v_ra = hojdmodell.grund_z(ruta)
        n_nan = int(np.count_nonzero(np.isnan(z_ra)))
        diag = HojdLuckDiagnos(
            ruta=ruta.namn,
            totalt_celler=int(z_ra.size),
            nan_fore_celler=n_nan,
            nan_efter_celler=n_nan,
            utanfor_plattor_celler=n_nan,
            vattenyta_celler=0,
            stort_hal_celler=0,
            smahal_fyllda_celler=0,
            smahal_ej_fyllda_celler=0,
            max_hal_avstand_m=0.0,
            nan_troskel_andel=nan_varning_andel,
        )
        return z_ra, v_ra, diag

    def _bygg_ruta(
        ruta: Ruta,
        z: NDArray[np.float32],
        vatten: tuple[NDArray[np.bool_], NDArray[np.float32]] | None,
        nmd_arr: NDArray[np.uint8] | None,
    ) -> None:
        yb = ruta.yttre_bbox
        ruta_kand = [
            k for k in kandidater
            if not (
                max(p[0] for p in k.polygon) < yb[0] or min(p[0] for p in k.polygon) > yb[2]
                or max(p[1] for p in k.polygon) < yb[1] or min(p[1] for p in k.polygon) > yb[3]
            )
        ]  # fmt: skip
        ruta_bryt = [
            b for b in brytlinjer
            if not (
                max(p[0] for p in b.punkter) < yb[0] or min(p[0] for p in b.punkter) > yb[2]
                or max(p[1] for p in b.punkter) < yb[1] or min(p[1] for p in b.punkter) > yb[3]
            )
        ]  # fmt: skip
        slag_grund: NDArray[np.uint8] | None = (
            nmd_till_k1_markslag(nmd_arr) if nmd_arr is not None else None
        )
        if vatten is not None:
            vmask, vniva = vatten
            if vmask.any():
                z = np.array(z, dtype=np.float32, copy=True)
                z[vmask] = vniva[vmask]
                if slag_grund is None:
                    slag_grund = np.where(vmask, VATTEN, HARDGJORT).astype(np.uint8)
                else:
                    slag_grund = slag_grund.copy()
                    slag_grund[vmask] = VATTEN
                # Bara rutans inre yta räknas (överlappet ingår i grannen).
                o = int(round(ruta.overlapp_m / ruta.upplosning_m))
                inre = vmask[o : vmask.shape[0] - o, o : vmask.shape[1] - o] if o else vmask
                inre_niva = vniva[o : vniva.shape[0] - o, o : vniva.shape[1] - o] if o else vniva
                res.vatten_m2 += float(inre.sum()) * ruta.upplosning_m**2
                if inre.any():
                    lo, hi = float(np.nanmin(inre_niva[inre])), float(np.nanmax(inre_niva[inre]))
                    a, b = res.vatten_niva_m or (lo, hi)
                    res.vatten_niva_m = (min(a, lo), max(b, hi))
        veg_px = max(1.0, float(ruta.upplosning_m))
        veg_hojd = (
            rasterisera_trad_vegetation(
                trad_punkter,
                ruta,
                kronradie_m=trad_kronradie_m,
                antagen_hojd_m=trad_hojd_m,
                upplosning_m=veg_px,
            )
            if trad_punkter
            else None
        )
        kallor = [
            KallPost("omrade", omrade.fil.name, omrade.sha256),
            *(KallPost("hojdmodell_filpaket", p.namn, p.sha256) for p in hojd_paket),
            *(
                KallPost("hojdmodell_klippt", pl.paket.name, sha256_fil(pl.paket))
                for pl in hojdmodell.plattor if pl.medlem is None
            ),
            *(KallPost("baskarta_filpaket", p.namn, p.sha256) for p in byggnader_paket),
            *([KallPost("byggnader_gpkg", "byggnader.gpkg", bygg_sha)] if bygg_sha else []),
            *([KallPost("nmd_markslag", nmd_fil.name, nmd_sha)] if nmd_fil is not None and nmd_sha else []),
        ]
        rr = bygg_markmodell_ruta(
            ruta, ut, grund_z=z, brytlinjer=ruta_bryt, byggnadskandidater=ruta_kand,
            byggnadshojd_fran_laser=False, extra_kallor=kallor, markslag_grund=slag_grund,
            vegetation_hojd_m=veg_hojd, vegetation_upplosning_m=veg_px,
        )  # fmt: skip
        res.varningar.extend(f"{ruta.namn}: {w}" for w in rr.varningar)
        skriv(f"{ruta.namn}: {len(ruta_kand)} byggnader")
        res.rutor.append(ruta.namn)

    if strandlinjer:
        # Läs markhöjd för alla rutor först så att sammanhängande vattenytor över rutgränser
        # får en gemensam global RH 2000-nivå utan steg längs skarven.
        laddade: list[tuple[Ruta, NDArray[np.float32]]] = []
        nmd_per_ruta: list[NDArray[np.uint8] | None] = []
        for ruta in rutlista:
            try:
                z, v, diagnos = _las_ruta_z(ruta)
            except Exception as exc:
                res.varningar.append(f"{ruta.namn}: {exc}")
                continue
            res.hojd_luckor.append(diagnos)
            res.max_nan_andel = max(res.max_nan_andel, diagnos.nan_efter_andel)
            if diagnos.over_troskel:
                res.hog_nan_flagga = True
            res.varningar.extend(v)
            laddade.append((ruta, z))
            nmd_per_ruta.append(
                nmd_raster_for_ruta(nmd_fil, ruta) if nmd_fil is not None and nmd_fil.is_file() else None
            )
        vatten_per_ruta = (
            vattenytor_for_rutor(
                laddade,
                strandlinjer,
                marginal_m=vatten_marginal_m,
                max_avstand_m=vatten_max_avstand_m,
                barriar_linjer=vatten_barriarer,
                nmd_per_ruta=nmd_per_ruta if nmd_fil is not None else None,
            )
            if laddade
            else []
        )
        for (ruta, z), vatten, nmd_arr in zip(laddade, vatten_per_ruta, nmd_per_ruta, strict=True):
            _bygg_ruta(ruta, z, vatten, nmd_arr)
    else:
        for ruta in rutlista:
            try:
                z, v, diagnos = _las_ruta_z(ruta)
            except Exception as exc:
                res.varningar.append(f"{ruta.namn}: {exc}")
                continue
            res.hojd_luckor.append(diagnos)
            res.max_nan_andel = max(res.max_nan_andel, diagnos.nan_efter_andel)
            if diagnos.over_troskel:
                res.hog_nan_flagga = True
            res.varningar.extend(v)
            nmd_arr = nmd_raster_for_ruta(nmd_fil, ruta) if nmd_fil is not None and nmd_fil.is_file() else None
            _bygg_ruta(ruta, z, None, nmd_arr)
    if strandlinjer:
        if res.vatten_niva_m is not None:
            lo, hi = res.vatten_niva_m
            res.varningar.append(
                f"{res.vatten_m2:.0f} m² vattenyta ur strandlinjer, nivå {lo:.1f}–{hi:.1f} m RH 2000. Ytan är plan "
                "(global mediannivå per sammanhängande vattenyta) med markslag vatten; lösaren har inget fast "
                "randvillkor för vattennivå eller utlopp, så regn på ytan stannar på ytan."
            )
        else:
            res.varningar.append("Strandlinjer lästes men inga celler under strandnivån nådde en strandlinje: inga vattenytor.")
        skriv(res.varningar[-1])
    if not res.rutor:
        raise PipelineFel("Inga rutor byggdes: täcker höjdmodellen området? Se varningarna.")
    return res
