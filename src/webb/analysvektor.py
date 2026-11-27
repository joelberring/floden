"""Analysernas vektordata för kartan (steg 4): sänkor, flödesvägar och fasadpunkter med VSC.

Det som bara finns som tabell eller graf i en körning (sänkgrafen, VSC per fasadpunkt) görs här
om till GeoJSON i SWEREF 99 18 00 (EPSG:3011, meter; klienten räknar om med `sweref.ts`).
Inget räknas om i själva värdena: attributen är sänkgrafens och lösarens egna (kapacitet, djup,
gångtid, VSC). Det enda som görs här är geometri: sänkans yta vid fullt är de av dess celler som
ligger på eller under överfallsnivån, och cellerna förs ihop till polygoner.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from rasterio import features
from rasterio.transform import from_origin
from shapely.geometry import mapping, shape

from sankgraf.avrinningsnat import STANDARD_MIN_YTA_M2, Stracka, avrinningsnat
from sankgraf.typer import SankGraf

MAX_SANKOR = 400
"""Högst så här många sänkor (de största) följer med; resten räknas i `antal_totalt`."""
SAMMANSLAGEN_MAX_DJUP_M = 1.0
"""Sänkor som fylls ihop slås ihop till en enhet så länge den är högst så här djup vid fullt."""
MAX_FASADPUNKTER = 20000
SLAG = ("sankor", "flodesvagar", "rannsten_flode", "vsc")


def _avrunda(v: float, n: int = 2) -> float:
    return round(float(v), n)


def _ring(r: Any) -> list[list[float]]:
    return [[_avrunda(x), _avrunda(y)] for x, y in r]


def sankenheter(graf: SankGraf, max_djup_m: float = SAMMANSLAGEN_MAX_DJUP_M) -> dict[int, int]:
    """Sänkorna som visas som en enhet: `{löv-id: enhetens nod-id}`.

    Sänkgrafen slår ihop sänkor vid överfallet (föräldern är de sammanflutna barnen). Från
    rötterna och nedåt tas den översta noden vars djup vid fullt (överfall minus botten) är högst
    `max_djup_m`: en innergård som fylls ihop till en vattenyta blir en sänka, medan
    sammanslagningar som kräver högre vattenstånd (hela kvarter) hålls isär. Löv är alltid
    enheter."""
    noder = graf.noder
    enhet_av_lov: dict[int, int] = {}
    stack = [nid for nid, n in noder.items() if n.forald_id is None]
    while stack:
        nid = stack.pop()
        n = noder[nid]
        djup = n.overfall_rh2000 - n.botten_rh2000
        if n.ar_lov or (math.isfinite(djup) and djup <= max_djup_m):
            for lid in graf.lov_under_nod(nid):
                enhet_av_lov[lid] = nid
        else:
            stack.extend(n.barn_id)
    return enhet_av_lov


def sankor_geojson(
    graf: SankGraf,
    *,
    max_djup_m: float = SAMMANSLAGEN_MAX_DJUP_M,
    min_volym_m3: float = 0.0,
    min_djup_m: float = 0.0,
) -> dict[str, Any]:
    """Sänkornas yta vid fullt som polygoner med kapacitet, djup, yta och överfall.

    Sänkor som fylls ihop slås ihop (`sankenheter`): ytan är cellerna under enhetens överfallsnivå,
    kapaciteten dess volymkurva vid överfallet. Enheter under `min_volym_m3` eller `min_djup_m`
    döljs (räknas i `antal_totalt`, `dolda`)."""
    noder = graf.noder
    enhet_av_lov = sankenheter(graf, max_djup_m)
    ct = graf.cell_till_nod
    hog = int(max(noder, default=0)) + 1
    enhet_tab = np.full(hog, -1, dtype=np.int64)
    overfall = np.full(hog, -np.inf, dtype=np.float64)
    for lid, eid in enhet_av_lov.items():
        enhet_tab[lid] = eid
        overfall[lid] = noder[eid].overfall_rh2000
    giltig = (ct >= 0) & (ct < hog)
    idx = np.clip(ct, 0, hog - 1)
    enhet = np.where(giltig, enhet_tab[idx], -1)
    z_over = np.where(giltig, overfall[idx], -np.inf)
    fylld = (enhet >= 0) & (np.nan_to_num(graf.mark_z, nan=np.inf) <= z_over + 1e-6)
    narvarande = np.unique(enhet[fylld]) if np.any(fylld) else np.empty(0, dtype=np.int64)
    delsankor_per_enhet = Counter(enhet_av_lov.values())
    godkanda: list[tuple[float, int, float]] = []
    dolda = 0
    for nid_np in narvarande.tolist():
        nid = int(nid_np)
        nod = noder[nid]
        djup = max(0.0, nod.overfall_rh2000 - nod.botten_rh2000)
        if nod.max_volym_m3 < min_volym_m3 or djup < min_djup_m:
            dolda += 1
            continue
        godkanda.append((_avrunda(nod.max_volym_m3, 3), nid, djup))
    godkanda.sort(key=lambda item: (-item[0], item[1]))
    antal_godkanda = len(godkanda)
    topp_lista = godkanda[:MAX_SANKOR]
    if not topp_lista:
        return {
            "type": "FeatureCollection",
            "features": [],
            "meta": {
                "antal_totalt": antal_godkanda + dolda,
                "visade": 0,
                "dolda": dolda,
                "max_djup_m": max_djup_m,
                "min_volym_m3": min_volym_m3,
                "min_djup_m": min_djup_m,
            },
        }
    topp_mask_tab = np.zeros(hog, dtype=np.bool_)
    for _, nid, _ in topp_lista:
        topp_mask_tab[nid] = True
    fylld_topp = fylld & topp_mask_tab[np.clip(enhet, 0, hog - 1)]
    geo = graf.geo
    t = from_origin(geo.x_min, geo.y_max, geo.pixelstorlek_x, geo.pixelstorlek_y)
    arr = np.where(fylld_topp, enhet, 0).astype(np.int32)
    per_nod: dict[int, list[Any]] = {}
    for geom, v in features.shapes(arr, mask=fylld_topp, transform=t, connectivity=4):
        per_nod.setdefault(int(v), []).append(shape(geom))
    poster: list[dict[str, Any]] = []
    for kap_avrundad, nid, djup in topp_lista:
        delar = per_nod.get(nid)
        if not delar:
            continue
        nod = noder[nid]
        yta = sum(d.area for d in delar)
        delsankor = int(delsankor_per_enhet.get(nid, 1))
        poster.append(
            {
                "type": "Feature",
                "id": f"sanka-{nid}",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[_ring(r) for r in mapping(d)["coordinates"]] for d in delar],
                },
                "properties": {
                    "nod_id": nid,
                    "kapacitet_m3": kap_avrundad,
                    "djup_m": _avrunda(djup, 3),
                    "yta_m2": _avrunda(yta, 1),
                    "avrinningsyta_m2": _avrunda(nod.avrinningsyta_m2, 1),
                    "overfall_rh2000": _avrunda(nod.overfall_rh2000, 3),
                    "overfall": [_avrunda(nod.overfall_x), _avrunda(nod.overfall_y)],
                    "delsankor": delsankor,
                    "slutsanka": nod.typ != "sanka",
                    "mitt": [
                        _avrunda(delar[0].representative_point().x),
                        _avrunda(delar[0].representative_point().y),
                    ],
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "features": poster,
        "meta": {
            "antal_totalt": antal_godkanda + dolda,
            "visade": len(poster),
            "dolda": dolda,
            "max_djup_m": max_djup_m,
            "min_volym_m3": min_volym_m3,
            "min_djup_m": min_djup_m,
        },
    }


def flodesvagar_fran_strackor(
    vagar: Sequence[Stracka], min_yta_m2: float = STANDARD_MIN_YTA_M2
) -> dict[str, Any]:
    """Konverterar färdiga avrinningssträckor (`Stracka`) till GeoJSON för kartans vektorlager."""
    poster = [
        {
            "type": "Feature",
            "id": f"flode-{v.id}",
            "geometry": {"type": "LineString", "coordinates": _ring(v.koordinater)},
            "properties": {
                "flodesvag_id": int(v.id),
                "tillrinning_m2": _avrunda(v.tillrinning_m2, 1),
                "ordning": int(v.ordning),
                "langd_m": _avrunda(v.langd_m, 1),
                "lutning": _avrunda(v.lutning, 4),
                "typ": v.typ,
            },
        }
        for v in vagar
    ]
    return {
        "type": "FeatureCollection",
        "features": poster,
        "meta": {"antal_totalt": len(poster), "visade": len(poster), "min_yta_m2": min_yta_m2},
    }


def flodesvagar_geojson(graf: SankGraf, min_yta_m2: float = STANDARD_MIN_YTA_M2) -> dict[str, Any]:
    """Avrinningsnätet (`sankgraf.avrinningsnat`): linjer med tillrinning (m²) och Strahler-ordning.

    Alla stråk över tröskeln följer med, störst sist (ritas överst)."""
    return flodesvagar_fran_strackor(avrinningsnat(graf, min_yta_m2), min_yta_m2)


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


def rannsten_flode_geojson(geojson_fil: Path) -> dict[str, Any]:
    """Läser 1D-rännstensflödet (`rannsten_flode.geojson`, EPSG:3011) för kartans vektorlager."""
    raw = json.loads(geojson_fil.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("features"), list):
        raise ValueError(f"{geojson_fil.name} är inte en giltig GeoJSON FeatureCollection.")
    poster: list[dict[str, Any]] = []
    for idx, feat in enumerate(raw["features"]):
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        rena_kord: list[list[float]] = []
        giltig = True
        for pt in coords:
            if not isinstance(pt, list | tuple) or len(pt) < 2:
                giltig = False
                break
            e, n = float(pt[0]), float(pt[1])
            if not (math.isfinite(e) and math.isfinite(n)):
                giltig = False
                break
            rena_kord.append([round(e, 3), round(n, 3)])
        if not giltig or len(rena_kord) < 2:
            continue
        props_raw = feat.get("properties")
        props: dict[str, Any] = dict(props_raw) if isinstance(props_raw, dict) else {}
        if "langd_m" not in props and isinstance(props.get("ds_m"), int | float):
            props["langd_m"] = round(float(props["ds_m"]), 3)
        poster.append(
            {
                "type": "Feature",
                "id": feat.get("id", f"rannsten-{props.get('segment_id', idx)}"),
                "geometry": {"type": "LineString", "coordinates": rena_kord},
                "properties": props,
            }
        )
    return {
        "type": "FeatureCollection",
        "features": poster,
        "meta": {"antal_totalt": len(poster), "visade": len(poster)},
    }
