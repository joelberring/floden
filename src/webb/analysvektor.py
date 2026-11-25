"""Analysernas vektordata för kartan (steg 4): sänkor, flödesvägar och fasadpunkter med VSC.

Det som bara finns som tabell eller graf i en körning (sänkgrafen, VSC per fasadpunkt) görs här
om till GeoJSON i SWEREF 99 18 00 (EPSG:3011, meter; klienten räknar om med `sweref.ts`).
Inget räknas om i själva värdena: attributen är sänkgrafens och lösarens egna (kapacitet, djup,
gångtid, VSC). Det enda som görs här är geometri: sänkans yta vid fullt är de av dess celler som
ligger på eller under överfallsnivån, och cellerna förs ihop till polygoner.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import numpy as np
from rasterio import features
from rasterio.transform import from_origin
from shapely.geometry import mapping, shape

from sankgraf.typer import SankGraf

MAX_SANKOR = 400
"""Högst så här många sänkor (de största) följer med; resten räknas i `antal_totalt`."""
MAX_FLODESVAGAR = 1000
MAX_FASADPUNKTER = 20000
SLAG = ("sankor", "flodesvagar", "vsc")


def _avrunda(v: float, n: int = 2) -> float:
    return round(float(v), n)


def _ring(r: Any) -> list[list[float]]:
    return [[_avrunda(x), _avrunda(y)] for x, y in r]


def sankor_geojson(graf: SankGraf) -> dict[str, Any]:
    """Sänkornas yta vid fullt (lövnoderna) som polygoner med kapacitet, djup och yta."""
    noder = graf.noder
    lov = {nid: n for nid, n in noder.items() if n.ar_lov}
    ct = graf.cell_till_nod
    hog = int(max(lov, default=0)) + 1
    overfall = np.full(hog, -np.inf, dtype=np.float64)
    for nid, n in lov.items():
        overfall[nid] = n.overfall_rh2000
    giltig = (ct >= 0) & (ct < hog)
    z_over = np.where(giltig, overfall[np.clip(ct, 0, hog - 1)], -np.inf)
    fylld = giltig & (np.nan_to_num(graf.mark_z, nan=np.inf) <= z_over + 1e-6)
    geo = graf.geo
    t = from_origin(geo.x_min, geo.y_max, geo.pixelstorlek_x, geo.pixelstorlek_y)
    arr = np.where(fylld, ct, 0).astype(np.int32)
    per_nod: dict[int, list[Any]] = {}
    for geom, v in features.shapes(arr, mask=fylld, transform=t, connectivity=4):
        per_nod.setdefault(int(v), []).append(shape(geom))
    poster: list[dict[str, Any]] = []
    for nid, delar in per_nod.items():
        nod = lov[nid]
        yta = sum(d.area for d in delar)
        poster.append(
            {
                "type": "Feature",
                "id": f"sanka-{nid}",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [_ring(r) for r in mapping(d)["coordinates"]] for d in delar
                    ],
                },
                "properties": {
                    "nod_id": nid,
                    "kapacitet_m3": _avrunda(nod.max_volym_m3, 3),
                    "djup_m": _avrunda(max(0.0, nod.overfall_rh2000 - nod.botten_rh2000), 3),
                    "yta_m2": _avrunda(yta, 1),
                    "avrinningsyta_m2": _avrunda(nod.avrinningsyta_m2, 1),
                    "slutsanka": nod.typ != "sanka",
                    "mitt": [
                        _avrunda(delar[0].representative_point().x),
                        _avrunda(delar[0].representative_point().y),
                    ],
                },
            }
        )
    poster.sort(key=lambda f: (-f["properties"]["kapacitet_m3"], f["properties"]["nod_id"]))
    return {
        "type": "FeatureCollection",
        "features": poster[:MAX_SANKOR],
        "meta": {"antal_totalt": len(poster), "visade": min(len(poster), MAX_SANKOR)},
    }


def flodesvagar_geojson(graf: SankGraf) -> dict[str, Any]:
    """Flödesvägarna som linjer med längd, lutning och gångtid (sänkgrafens egna värden)."""
    vagar = [v for v in graf.flodesvagar if len(v.koordinater) >= 2]
    vagar.sort(key=lambda v: (-v.langd_m, v.id))
    poster = [
        {
            "type": "Feature",
            "id": f"flode-{v.id}",
            "geometry": {"type": "LineString", "coordinates": _ring(v.koordinater)},
            "properties": {
                "flodesvag_id": int(v.id),
                "nod_id": int(v.nod_id),
                "langd_m": _avrunda(v.langd_m, 1),
                "lutning": _avrunda(v.lutning, 4),
                "gangtid_s": _avrunda(v.gangtid_s, 1),
            },
        }
        for v in vagar[:MAX_FLODESVAGAR]
    ]
    return {
        "type": "FeatureCollection",
        "features": poster,
        "meta": {"antal_totalt": len(vagar), "visade": len(poster)},
    }


def vsc_geojson(csv_fil: Path) -> dict[str, Any]:
    """Fasadpunkterna (vsc.csv) som punkter med VSC i procent (saknas där horisonten avskurits)."""
    poster: list[dict[str, Any]] = []
    totalt = 0
    with csv_fil.open("r", encoding="utf-8", newline="") as fh:
        for rad in csv.DictReader(fh):
            totalt += 1
            if len(poster) >= MAX_FASADPUNKTER:
                continue
            try:
                x, y = float(rad["x_m"]), float(rad["y_m"])
            except (KeyError, ValueError):
                continue
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            try:
                vsc: float | None = float(rad["vsc_procent"]) if rad.get("vsc_procent") else None
            except ValueError:
                vsc = None
            poster.append(
                {
                    "type": "Feature",
                    "id": f"fasad-{rad.get('fasad_id', len(poster))}",
                    "geometry": {"type": "Point", "coordinates": [_avrunda(x), _avrunda(y)]},
                    "properties": {
                        "fasad_id": rad.get("fasad_id", ""),
                        "vsc_procent": None if vsc is None else _avrunda(vsc, 2),
                        "z_m": _avrunda(float(rad.get("z_m") or 0.0), 2),
                        "status": rad.get("status", ""),
                    },
                }
            )
    return {
        "type": "FeatureCollection",
        "features": poster,
        "meta": {"antal_totalt": totalt, "visade": len(poster)},
    }
