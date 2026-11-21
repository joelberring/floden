"""Läser tillbaka en sänkgraf från en K2-mapp, exakt som den skrevs av `skriv_sankgraf_k2`.

Kontraktets filer (`sankgraf.json`, `cell_till_nod.tif`, `flodesvagar.gpkg`) räcker inte för att
fylla grafen igen: fyllningen behöver också överfallscellerna, vilket löv ett överfall rinner
till, och den bearbetade markhöjden (med brytlinjer och öppningar inbrända). Därför skriver
`skriv_sankgraf_k2` tre tilläggsfiler: `sankgraf_topologi.json`, `mark_bearbetad.tif` och
`markslag.tif`. Saknas någon av dem blir det ett fel, inte en halv graf.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from sankgraf.typer import (
    KONTRAKT_K2,
    FlodesVag,
    SankGraf,
    SankGrafFel,
    SankNod,
    VolymPunkt,
    YtPunkt,
    avkoda_gp_wkb,
    las_geotiff_int64,
)
from testsvit.k1_io import las_geotiff, las_gpkg_tabell

FILER = (
    "sankgraf.json",
    "sankgraf_topologi.json",
    "cell_till_nod.tif",
    "mark_bearbetad.tif",
    "markslag.tif",
    "flodesvagar.gpkg",
)


def _nod(d: dict[str, Any]) -> SankNod:
    return SankNod(
        nod_id=int(d["nod_id"]),
        forald_id=None if d["forald_id"] is None else int(d["forald_id"]),
        barn_id=tuple(int(b) for b in d["barn_id"]),
        overfall_rh2000=float(d["overfall_rh2000"]),
        overfall_x=float(d["overfall_x"]),
        overfall_y=float(d["overfall_y"]),
        ut_till_id=None if d["ut_till_id"] is None else int(d["ut_till_id"]),
        volymkurva=tuple(VolymPunkt(float(p["z"]), float(p["v_m3"])) for p in d["volymkurva"]),
        ytkurva=tuple(YtPunkt(float(p["z"]), float(p["a_m2"])) for p in d["ytkurva"]),
        avrinningsyta_m2=float(d["avrinningsyta_m2"]),
        persistens_m=float(d["persistens_m"]),
        osakerhet_m=float(d["osakerhet_m"]),
        botten_rh2000=float(d["botten_rh2000"]),
        cell_antal=int(d["cell_antal"]),
    )


def las_sankgraf_k2(mapp: str | Path) -> SankGraf:
    """Sänkgrafen i `mapp` (K2/v1 med tilläggsfilerna)."""
    m = Path(mapp)
    saknas = [f for f in FILER if not (m / f).is_file()]
    if saknas:
        raise SankGrafFel(
            f"K2-mappen '{m}' saknar {', '.join(saknas)}. Skriv om grafen med skriv_sankgraf_k2."
        )
    huvud = json.loads((m / "sankgraf.json").read_text(encoding="utf-8"))
    topo = json.loads((m / "sankgraf_topologi.json").read_text(encoding="utf-8"))
    for namn, d in (("sankgraf.json", huvud), ("sankgraf_topologi.json", topo)):
        if d.get("kontrakt") != KONTRAKT_K2:
            raise SankGrafFel(f"{namn}: fel kontrakt {d.get('kontrakt')!r}.")

    noder = {n.nod_id: n for n in (_nod(d) for d in huvud["noder"])}
    if len(noder) != int(huvud["antal_noder"]):
        raise SankGrafFel("sankgraf.json: antal_noder stämmer inte med nodlistan.")

    cell_till_nod, _ = las_geotiff_int64(m / "cell_till_nod.tif")
    mark_raw, geo = las_geotiff(m / "mark_bearbetad.tif")
    slag_raw, _ = las_geotiff(m / "markslag.tif")
    if cell_till_nod.shape != mark_raw.shape or slag_raw.shape != mark_raw.shape:
        raise SankGrafFel("K2-rastren har olika storlek.")
    # Negativa värden är markörer (ingen nod, byggnad m.m.), inte nod-id.
    okanda = {v for v in np.unique(cell_till_nod).tolist() if v >= 0} - set(noder)
    if okanda:
        raise SankGrafFel(f"cell_till_nod pekar på okända noder: {sorted(okanda)[:5]}.")

    _, rader = las_gpkg_tabell(m / "flodesvagar.gpkg", "flodesvagar")
    vagar: list[FlodesVag] = []
    for rad in sorted(rader, key=lambda r: int(r["flodesvag_id"])):
        geom = rad.get("geom")
        if not isinstance(geom, bytes | bytearray):
            raise SankGrafFel("flodesvagar.gpkg: post utan geometri.")
        vagar.append(
            FlodesVag(
                id=int(rad["flodesvag_id"]),
                nod_id=int(rad["nod_id"]),
                koordinater=avkoda_gp_wkb(geom),
                langd_m=float(rad["langd_m"]),
                lutning=float(rad["lutning"]),
                gangtid_s=float(rad["gangtid_s"]),
                referensdjup_m=float(rad["referensdjup_m"]),
            )
        )

    return SankGraf(
        noder=noder,
        cell_till_nod=np.asarray(cell_till_nod, dtype=np.int64),
        flodesvagar=vagar,
        geo=geo,
        filtrerad=bool(huvud["filtrerad"]),
        persistens_faktor=(
            None if huvud["persistens_faktor"] is None else float(huvud["persistens_faktor"])
        ),
        mark_z=np.asarray(mark_raw, dtype=np.float32),
        markslag=np.asarray(slag_raw, dtype=np.uint8),
        ut_lov_id={
            int(k): (None if v is None else int(v)) for k, v in topo["ut_lov_id"].items()
        },
        overfall_celler={
            int(k): tuple((int(r), int(c)) for r, c in v)
            for k, v in topo["overfall_celler"].items()
        },
    )
