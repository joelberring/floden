"""STAC-sökning mot Lantmäteriets `stac-hojd`, `stac-bild` och `stac-vektor`.

Protokoll och logik porterade från O-Tid (MIT, joelberring/otid @ 4a446af): sökningen är
`POST <tjänst>/search` med `{"bbox": [väst, syd, öst, nord], "collections": [id], "limit": 200}`
där bbox är **WGS 84 (EPSG:4326, grader)** (O-Tids `Wgs84Box`); samlingarna listas med
`GET <tjänst>/collections`. Laserns samling är den vars id eller titel nämner både "laser" och
"skog" (O-Tid `laserCollectionOf`); byggnadssamlingen den vars namn nämner "byggnad" utan
"inspire", "visning" eller "direkt" (`searchVectorExtras`). Nyaste skanning per ruta väljs som i
O-Tids `newestLaserTiles`.

Sidhantering följer STAC API:s `links` med `rel = "next"` (GET eller POST med `body`/`merge`),
vilket O-Tid inte behöver (en sida om 200). Inget av detta är provat mot den riktiga tjänsten.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry

from lantmateriet.klient import LantmaterietFel, LmKlient

ROLL_LASER = "laser"
ROLL_ORTO = "orto"
ROLL_BYGGNADER = "byggnader"

SIDSTORLEK = 200  # som O-Tid
MAX_SIDOR = 100

_LAS = re.compile(r"\.la[sz]($|\?)", re.IGNORECASE)
_LAS_TYP = re.compile(r"las|laz|laszip", re.IGNORECASE)
_TIFF = re.compile(r"\.tiff?($|\?)", re.IGNORECASE)
_ZIP = re.compile(r"\.zip($|\?)", re.IGNORECASE)
_ZIP_TYP = re.compile(r"zip|geopackage", re.IGNORECASE)


def wgs84_bbox(
    geometri: BaseGeometry | tuple[float, float, float, float], fran_epsg: int = 3011
) -> tuple[float, float, float, float]:
    """`(väst, syd, öst, nord)` i WGS 84 för en geometri eller bbox i `fran_epsg`.

    Kanterna täths upp (så att projektionens krökning inte ger en för liten ruta) och bounds tas
    över alla punkter. STAC vill ha grader med lon först (`always_xy`).
    """
    if isinstance(geometri, tuple):
        e0, n0, e1, n1 = geometri
        geometri = shapely.box(e0, n0, e1, n1)
    tät = shapely.segmentize(geometri.envelope.exterior, max(1.0, _max_sida(geometri) / 20.0))
    xy = np.asarray(tät.coords, dtype=np.float64)
    t = Transformer.from_crs(fran_epsg, 4326, always_xy=True)
    lon, lat = t.transform(xy[:, 0], xy[:, 1])
    return (float(np.min(lon)), float(np.min(lat)), float(np.max(lon)), float(np.max(lat)))


def _max_sida(g: BaseGeometry) -> float:
    x0, y0, x1, y1 = g.bounds
    return float(max(x1 - x0, y1 - y0))


def geometri_till_wgs84(g: BaseGeometry, fran_epsg: int = 3011) -> BaseGeometry:
    t = Transformer.from_crs(fran_epsg, 4326, always_xy=True)
    return shapely.transform(g, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))


@dataclass(frozen=True)
class StacObjekt:
    """En träff i STAC med den tillgång (fil) vi vill hämta."""

    id: str
    samling: str
    roll: str
    href: str
    bbox_wgs84: tuple[float, float, float, float] | None
    tidpunkt: str | None
    storlek: int | None
    epsg: int | None
    titel: str | None = None


def lista_samlingar(klient: LmKlient, bas_url: str) -> list[dict[str, str]]:
    """`GET <bas>/collections` som `[{id, title}]`."""
    data = klient.hamta_json(bas_url.rstrip("/") + "/collections")
    rå = data.get("collections")
    if not isinstance(rå, list):
        raise LantmaterietFel(f"{bas_url}/collections svarade utan 'collections'.")
    ut: list[dict[str, str]] = []
    for c in rå:
        if isinstance(c, dict) and isinstance(c.get("id"), str):
            ut.append({"id": c["id"], "title": str(c.get("title") or "")})
    return ut


def valj_samling(
    samlingar: Sequence[dict[str, str]], roll: str, *, tvingad: str | None = None
) -> str:
    """Samlingen för `roll` (se modulens text), eller `tvingad` om den anges (måste finnas)."""
    ids = [s["id"] for s in samlingar]
    if tvingad:
        if tvingad not in ids:
            raise LantmaterietFel(
                f"Samlingen '{tvingad}' finns inte. Tillgängliga: "
                f"{', '.join(sorted(ids)) or '(inga)'}."
            )
        return tvingad
    ord_ = [f"{s['id']} {s['title']}" for s in samlingar]

    def traff(i: int) -> bool:
        t = ord_[i]
        if roll == ROLL_LASER:
            return bool(re.search("laser", t, re.I) and re.search("skog", t, re.I))
        if roll == ROLL_ORTO:
            return bool(re.search("ortofoto|orto", t, re.I)) and not re.search(
                "histor|inspire|visning", t, re.I
            )
        if roll == ROLL_BYGGNADER:
            return bool(re.search("byggnad", t, re.I)) and not re.search(
                "inspire|visning|direkt", t, re.I
            )
        return False

    traffar = [ids[i] for i in range(len(ids)) if traff(i)]
    if not traffar:
        raise LantmaterietFel(
            f"Hittar ingen samling för '{roll}'. Tillgängliga: "
            f"{', '.join(sorted(ids)) or '(inga)'}. "
            "Ange den med --laser-samling, --orto-samling eller --byggnads-samling."
        )
    return sorted(traffar)[0]


def _tillgang(feature: dict[str, Any], roll: str) -> tuple[str, dict[str, Any]] | None:
    assets = feature.get("assets")
    if not isinstance(assets, dict):
        return None
    for namn in sorted(assets):
        a = assets[namn]
        if not isinstance(a, dict) or not isinstance(a.get("href"), str):
            continue
        href, typ = a["href"], str(a.get("type") or "")
        if roll == ROLL_LASER and (_LAS.search(href) or _LAS_TYP.search(typ)):
            return href, a
        if roll == ROLL_ORTO and (_TIFF.search(href) or "image/tiff" in typ):
            return href, a
        if roll == ROLL_BYGGNADER and (_ZIP.search(href) or _ZIP_TYP.search(typ)):
            return href, a
    return None


def _epsg(props: dict[str, Any]) -> int | None:
    kod = props.get("proj:epsg", props.get("proj:code"))
    if isinstance(kod, str):
        kod = kod.upper().removeprefix("EPSG:")
    try:
        return int(kod) if kod is not None else None
    except (TypeError, ValueError):
        return None


def tolka_objekt(feature: dict[str, Any], roll: str, samling: str) -> StacObjekt | None:
    """Ett STAC-objekt som `StacObjekt`, eller `None` om det saknar id eller rätt sorts fil."""
    fid = feature.get("id")
    t = _tillgang(feature, roll)
    if not isinstance(fid, str) or not fid or t is None:
        return None
    href, asset = t
    rå_props = feature.get("properties")
    props: dict[str, Any] = rå_props if isinstance(rå_props, dict) else {}
    bb = feature.get("bbox")
    bbox: tuple[float, float, float, float] | None = None
    if isinstance(bb, list) and len(bb) >= 4 and all(isinstance(v, int | float) for v in bb[:4]):
        bbox = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
    dt = props.get("datetime")
    size = asset.get("file:size")
    titel = asset.get("title")
    return StacObjekt(
        id=fid,
        samling=str(feature.get("collection") or samling),
        roll=roll,
        href=href,
        bbox_wgs84=bbox,
        tidpunkt=dt if isinstance(dt, str) else None,
        storlek=size if isinstance(size, int) and not isinstance(size, bool) else None,
        epsg=_epsg(props),
        titel=titel[:200] if isinstance(titel, str) else None,
    )


def sok(
    klient: LmKlient,
    bas_url: str,
    bbox_wgs84: tuple[float, float, float, float],
    samling: str,
    roll: str,
    *,
    sidstorlek: int = SIDSTORLEK,
    max_sidor: int = MAX_SIDOR,
) -> Iterator[StacObjekt]:
    """Alla träffar för bbox och samling, sida för sida (följer `rel = next`)."""
    url = bas_url.rstrip("/") + "/search"
    kropp: dict[str, Any] = {
        "bbox": list(bbox_wgs84),
        "collections": [samling],
        "limit": sidstorlek,
    }
    steg: tuple[str, str, dict[str, Any]] = ("POST", url, kropp)
    sett: set[tuple[str, str, str]] = {("POST", url, json.dumps(kropp, sort_keys=True))}
    for _ in range(max_sidor):
        metod, u, k = steg
        svar = klient.post_json(u, k) if metod == "POST" else klient.hamta_json(u)
        feats = svar.get("features")
        if not isinstance(feats, list):
            raise LantmaterietFel("STAC-svaret saknar 'features'.")
        for f in feats:
            if isinstance(f, dict):
                obj = tolka_objekt(f, roll, samling)
                if obj is not None:
                    yield obj
        nasta = _nasta(svar, u, k)
        if nasta is None:
            return
        nyckel = (nasta[0], nasta[1], json.dumps(nasta[2], sort_keys=True))
        if nyckel in sett:
            return  # samma sida igen: stoppa i stället för att loopa
        sett.add(nyckel)
        steg = nasta
    raise LantmaterietFel(f"Fler än {max_sidor} sidor i STAC-svaret; avbryter.")


def _nasta(
    svar: dict[str, Any], nuvarande_url: str, nuvarande_kropp: dict[str, Any]
) -> tuple[str, str, dict[str, Any]] | None:
    """Nästa sida ur `links` (rel = next), som (metod, url, kropp), eller `None`."""
    for lank in svar.get("links") or []:
        if not (isinstance(lank, dict) and lank.get("rel") == "next"):
            continue
        href = lank.get("href")
        if not isinstance(href, str) or not href:
            href = nuvarande_url
        metod = str(lank.get("method") or "GET").upper()
        if metod == "POST":
            kropp = lank.get("body")
            ny = dict(nuvarande_kropp) if lank.get("merge") else {}
            if isinstance(kropp, dict):
                ny.update(kropp)
            return "POST", href, ny or dict(nuvarande_kropp)
        return "GET", href, {}
    return None


def nyaste_per_ruta(objekt: Sequence[StacObjekt]) -> list[StacObjekt]:
    """Nyaste skanningen för varje ruta (nyckel: bbox på tre decimaler, annars id), som O-Tid."""
    basta: dict[str, StacObjekt] = {}
    for o in objekt:
        nyckel = ",".join(f"{v:.3f}" for v in o.bbox_wgs84) if o.bbox_wgs84 else o.id
        tidigare = basta.get(nyckel)
        if tidigare is None or (o.tidpunkt or "") > (tidigare.tidpunkt or ""):
            basta[nyckel] = o
    return sorted(basta.values(), key=lambda o: o.id)


def tacka_nyast_forst(
    objekt: Sequence[StacObjekt], omrade_wgs84: BaseGeometry, *, min_ny_yta_grader2: float = 1e-10
) -> list[StacObjekt]:
    """Girig täckning: nyaste först; ett objekt tas med om det täcker något otäckt av området."""
    kvar = omrade_wgs84
    valda: list[StacObjekt] = []
    for o in sorted(objekt, key=lambda x: (x.tidpunkt or "", x.id), reverse=True):
        if kvar.is_empty or kvar.area <= min_ny_yta_grader2:
            break
        if o.bbox_wgs84 is None:
            continue
        ruta = shapely.box(*o.bbox_wgs84)
        if ruta.intersection(kvar).area > min_ny_yta_grader2:
            valda.append(o)
            kvar = kvar.difference(ruta)
    return sorted(valda, key=lambda o: o.id)
