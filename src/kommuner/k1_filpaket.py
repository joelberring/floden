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

from gemensamt.hash import sha256_fil
from kommuner.filpaket import Filpaket
from kommuner.hojdmodell import (
    NAN_VARNING_TROSKEL_ANDEL,
    HojdLuckDiagnos,
    Hojdmodell,
)
from kommuner.vattenytor import Linje, vattenytor_for_rutor
from lantmateriet.omrade import Omrade, rutor_for_omrade
from markmodell.byggnadskallor import las_byggnadskalla
from markmodell.hinder_oppningar import HARDGJORT, VATTEN
from markmodell.pipeline import PipelineFel, bygg_markmodell_ruta
from markmodell.recept import KallPost
from markmodell.rutnat import Ruta


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
    vatten_marginal_m: float = 0.2,
    vatten_max_avstand_m: float = 2.0,
    nan_varning_andel: float = NAN_VARNING_TROSKEL_ANDEL,
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
    ) -> None:
        yb = ruta.yttre_bbox
        ruta_kand = [
            k for k in kandidater
            if not (
                max(p[0] for p in k.polygon) < yb[0] or min(p[0] for p in k.polygon) > yb[2]
                or max(p[1] for p in k.polygon) < yb[1] or min(p[1] for p in k.polygon) > yb[3]
            )
        ]  # fmt: skip
        slag_grund = None
        if vatten is not None:
            vmask, vniva = vatten
            if vmask.any():
                z = np.array(z, dtype=np.float32, copy=True)
                z[vmask] = vniva[vmask]
                slag_grund = np.where(vmask, VATTEN, HARDGJORT).astype(np.uint8)
                # Bara rutans inre yta räknas (överlappet ingår i grannen).
                o = int(round(ruta.overlapp_m / ruta.upplosning_m))
                inre = vmask[o : vmask.shape[0] - o, o : vmask.shape[1] - o] if o else vmask
                inre_niva = vniva[o : vniva.shape[0] - o, o : vniva.shape[1] - o] if o else vniva
                res.vatten_m2 += float(inre.sum()) * ruta.upplosning_m**2
                if inre.any():
                    lo, hi = float(np.nanmin(inre_niva[inre])), float(np.nanmax(inre_niva[inre]))
                    a, b = res.vatten_niva_m or (lo, hi)
                    res.vatten_niva_m = (min(a, lo), max(b, hi))
        kallor = [
            KallPost("omrade", omrade.fil.name, omrade.sha256),
            *(KallPost("hojdmodell_filpaket", p.namn, p.sha256) for p in hojd_paket),
            *(
                KallPost("hojdmodell_klippt", pl.paket.name, sha256_fil(pl.paket))
                for pl in hojdmodell.plattor if pl.medlem is None
            ),
            *(KallPost("baskarta_filpaket", p.namn, p.sha256) for p in byggnader_paket),
            *([KallPost("byggnader_gpkg", "byggnader.gpkg", bygg_sha)] if bygg_sha else []),
        ]
        rr = bygg_markmodell_ruta(
            ruta, ut, grund_z=z, byggnadskandidater=ruta_kand,
            byggnadshojd_fran_laser=False, extra_kallor=kallor, markslag_grund=slag_grund,
        )  # fmt: skip
        res.varningar.extend(f"{ruta.namn}: {w}" for w in rr.varningar)
        skriv(f"{ruta.namn}: {len(ruta_kand)} byggnader")
        res.rutor.append(ruta.namn)

    if strandlinjer:
        # Läs markhöjd för alla rutor först så att sammanhängande vattenytor över rutgränser
        # får en gemensam global RH 2000-nivå utan steg längs skarven.
        laddade: list[tuple[Ruta, NDArray[np.float32]]] = []
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
        vatten_per_ruta = (
            vattenytor_for_rutor(
                laddade,
                strandlinjer,
                marginal_m=vatten_marginal_m,
                max_avstand_m=vatten_max_avstand_m,
            )
            if laddade
            else []
        )
        for (ruta, z), vatten in zip(laddade, vatten_per_ruta, strict=True):
            _bygg_ruta(ruta, z, vatten)
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
            _bygg_ruta(ruta, z, None)
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
