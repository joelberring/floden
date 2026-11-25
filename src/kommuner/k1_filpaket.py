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

from gemensamt.hash import sha256_fil
from kommuner.filpaket import Filpaket
from kommuner.hojdmodell import Hojdmodell
from kommuner.vattenytor import Linje, vattenytor_i_ruta
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
    for ruta in rutlista:
        yb = ruta.yttre_bbox
        ruta_kand = [
            k for k in kandidater
            if not (
                max(p[0] for p in k.polygon) < yb[0] or min(p[0] for p in k.polygon) > yb[2]
                or max(p[1] for p in k.polygon) < yb[1] or min(p[1] for p in k.polygon) > yb[3]
            )
        ]  # fmt: skip
        try:
            z, v = hojdmodell.grund_z(ruta)
        except Exception as exc:
            res.varningar.append(f"{ruta.namn}: {exc}")
            continue
        res.varningar.extend(v)
        slag_grund = None
        if strandlinjer:
            vmask, vniva = vattenytor_i_ruta(
                z, ruta, strandlinjer, marginal_m=vatten_marginal_m, max_avstand_m=vatten_max_avstand_m
            )
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
        if res.vatten_niva_m is not None:
            lo, hi = res.vatten_niva_m
            res.varningar.append(
                f"{res.vatten_m2:.0f} m² vattenyta ur strandlinjer, nivå {lo:.1f}–{hi:.1f} m RH 2000. Ytan är plan "
                "(nivå = närmaste strandlinjes z) med markslag vatten; lösaren har inget fast randvillkor för "
                "vattennivå eller utlopp, så regn på ytan stannar på ytan."
            )
        else:
            res.varningar.append("Strandlinjer lästes men inga celler under strandnivån nådde en strandlinje: inga vattenytor.")
        skriv(res.varningar[-1])
    if not res.rutor:
        raise PipelineFel("Inga rutor byggdes: täcker höjdmodellen området? Se varningarna.")
    return res
