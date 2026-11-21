"""Inläsning och mappning av SGU jordartsdata samt NVDB vägnätsdata."""

from __future__ import annotations

import json
import math
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from data_inlasning.cache import DataInlasningFel, Hamtare, LokalDataCache
from gemensamt.koordinater import EPSG_INTERN, KoordinatFel, transformera_till_3011
from mark_forluster.parametrar import STANDARD_JORDARTER
from rutt.vagnat import VagKant, VagNat, VagNatFel, VagNod

SGU_JORDARTER_URL = (
    "https://api.sgu.se/oppnadata/jordarter25k-100k/ogc/features/v1/collections/jordarter/items"
)

# Kanonisk mappning från vanliga svenska SGU-jordartsnamn/nyckelord till Flodens jordarter.
SGU_NAMN_TILL_FLODEN_JORDART: dict[str, str] = {
    "urberg": "berg",
    "berg": "berg",
    "häll": "berg",
    "hall": "berg",
    "berg i dagen": "berg",
    "granit": "berg",
    "gnejs": "berg",
    "morän": "moran",
    "moran": "moran",
    "sandig morän": "moran",
    "sandig-siltig morän": "moran",
    "lerig morän": "moran",
    "grusig morän": "moran",
    "svallad morän": "moran",
    "sand": "sand",
    "isälvssediment": "sand",
    "isalvssediment": "sand",
    "isälvsavlagring": "sand",
    "grus": "sand",
    "sand-grus": "sand",
    "svallsand": "sand",
    "flygsand": "sand",
    "grovsilt": "silt",
    "silt": "silt",
    "finsilt": "silt",
    "finmo": "silt",
    "grovmo": "silt",
    "lera": "lera",
    "glacial lera": "lera",
    "postglacial lera": "lera",
    "lättlera": "lera",
    "styv lera": "lera",
    "gyttjelera": "lera",
    "gyttja": "lera",
    "fyllning": "fyllning",
    "fyllnadsmaterial": "fyllning",
    "schaktmassor": "fyllning",
    "torv": "torv",
    "kärrtorv": "torv",
    "karrtorv": "torv",
    "mossetorv": "torv",
    "bleke": "torv",
}

_NYCKELORD_PRIORITET: tuple[tuple[str, str], ...] = (
    ("fyllning", "fyllning"),
    ("fyllnad", "fyllning"),
    ("schakt", "fyllning"),
    ("torv", "torv"),
    ("morän", "moran"),
    ("moran", "moran"),
    ("lera", "lera"),
    ("gyttj", "lera"),
    ("silt", "silt"),
    ("finmo", "silt"),
    ("isälv", "sand"),
    ("isalv", "sand"),
    ("sand", "sand"),
    ("grus", "sand"),
    ("berg", "berg"),
    ("häll", "berg"),
    ("gnejs", "berg"),
    ("granit", "berg"),
)

TransformatorPunkt = Callable[[float, float], tuple[float, float]]


@dataclass(frozen=True)
class SguJordartYta:
    """En tolkad SGU-jordartsyta kopplad till Flodens kanoniska jordartsklass."""

    yta_id: str
    sgu_namn: str
    floden_jordart: str
    osaker: bool
    polygon: tuple[tuple[float, float], ...]


def _normalisera_sgu_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def mappa_sgu_jordart_detaljerad(sgu_beskrivning: str) -> tuple[str, bool]:
    """Mappar SGU-jordartsbeskrivning till `(floden_jordart, exakt_eller_kand_match)`.

    Kastar `DataInlasningFel` vid tom sträng. Vid okänd jordart faller mappningen
    tillbaka på `"moran"` (vanligaste svenska jordarten) med `kand_match=False` så
    att anroparen kan flagga osäkerheten utan tysta fel.
    """
    if not isinstance(sgu_beskrivning, str) or not sgu_beskrivning.strip():
        raise DataInlasningFel("SGU-jordartsbeskrivning får inte vara tom.")

    norm = _normalisera_sgu_text(sgu_beskrivning)
    if norm in SGU_NAMN_TILL_FLODEN_JORDART:
        klass = SGU_NAMN_TILL_FLODEN_JORDART[norm]
        return klass, True

    norm_utan_sv = norm.replace("ä", "a").replace("ö", "o").replace("å", "a")
    if norm_utan_sv in SGU_NAMN_TILL_FLODEN_JORDART:
        klass = SGU_NAMN_TILL_FLODEN_JORDART[norm_utan_sv]
        return klass, True
    if norm_utan_sv in STANDARD_JORDARTER:
        return norm_utan_sv, True

    for nyckelord, klass in _NYCKELORD_PRIORITET:
        if nyckelord in norm:
            return klass, True

    return "moran", False


def mappa_sgu_jordart(sgu_beskrivning: str) -> str:
    """Mappar SGU-jordartsbeskrivning till Flodens kanoniska jordartsklass."""
    klass, _ = mappa_sgu_jordart_detaljerad(sgu_beskrivning)
    return klass


def _transformera_punkt(
    x: float,
    y: float,
    kall_epsg: int,
    transformator: TransformatorPunkt | None,
) -> tuple[float, float]:
    if kall_epsg == EPSG_INTERN:
        return float(x), float(y)
    if transformator is not None:
        ex, ny = transformator(float(x), float(y))
        return float(ex), float(ny)
    try:
        ex, ny = transformera_till_3011(float(x), float(y), kall_epsg)
    except KoordinatFel as exc:
        raise DataInlasningFel(str(exc)) from exc
    return float(ex), float(ny)


def _extrahera_yttre_ring(geometri: Mapping[str, Any]) -> list[Any]:
    geom_typ = geometri.get("type")
    coords = geometri.get("coordinates")
    if not isinstance(coords, list) or not coords:
        raise DataInlasningFel(f"Ogiltig eller tom geometri för SGU-jordartsyta: {geom_typ!r}.")
    if geom_typ == "Polygon":
        ring = coords[0]
    elif geom_typ == "MultiPolygon":
        ring = coords[0][0] if isinstance(coords[0], list) and coords[0] else []
    else:
        raise DataInlasningFel(
            f"Stöder endast Polygon/MultiPolygon för SGU-jordarter, fick '{geom_typ}'."
        )
    if not isinstance(ring, list) or len(ring) < 3:
        raise DataInlasningFel("Polygonring för SGU-jordartsyta måste ha minst 3 punkter.")
    return ring


def tolka_sgu_geojson_features(
    geojson: Mapping[str, Any],
    *,
    kall_epsg: int = EPSG_INTERN,
    transformator: TransformatorPunkt | None = None,
) -> list[SguJordartYta]:
    """Tolkar SGU OGC API Features GeoJSON till en lista av `SguJordartYta`."""
    features_raw = geojson.get("features")
    if not isinstance(features_raw, list):
        raise DataInlasningFel("SGU GeoJSON saknar lista 'features'.")

    ytor: list[SguJordartYta] = []
    for idx, feat in enumerate(features_raw):
        if not isinstance(feat, dict):
            raise DataInlasningFel(f"SGU-feature på index {idx} är inte ett JSON-objekt.")
        props = feat.get("properties")
        if not isinstance(props, dict):
            raise DataInlasningFel(f"SGU-feature på index {idx} saknar 'properties'.")

        yta_id_raw = feat.get("id") or props.get("id") or props.get("objectid") or f"sgu_{idx}"
        yta_id = str(yta_id_raw)

        sgu_namn: str | None = None
        for falt in ("jordart", "jg2_tx", "beskrivning", "namn", "jordart_namn"):
            varde = props.get(falt)
            if isinstance(varde, str) and varde.strip():
                sgu_namn = varde.strip()
                break
        if sgu_namn is None:
            raise DataInlasningFel(f"SGU-feature '{yta_id}' saknar jordartsbeskrivning.")

        geom = feat.get("geometry")
        if not isinstance(geom, dict):
            raise DataInlasningFel(f"SGU-feature '{yta_id}' saknar 'geometry'.")
        ring_raw = _extrahera_yttre_ring(geom)

        ring_3011: list[tuple[float, float]] = []
        for pt in ring_raw:
            if not isinstance(pt, list | tuple) or len(pt) < 2:
                raise DataInlasningFel(f"Ogiltig koordinatpunkt i SGU-feature '{yta_id}'.")
            e_m, n_m = _transformera_punkt(float(pt[0]), float(pt[1]), kall_epsg, transformator)
            ring_3011.append((e_m, n_m))

        floden_jordart, kand_match = mappa_sgu_jordart_detaljerad(sgu_namn)
        ytor.append(
            SguJordartYta(
                yta_id=yta_id,
                sgu_namn=sgu_namn,
                floden_jordart=floden_jordart,
                osaker=not kand_match,
                polygon=tuple(ring_3011),
            )
        )
    return ytor


def hamta_sgu_jordarter(
    bbox_wgs84: tuple[float, float, float, float],
    *,
    cache: LokalDataCache | None = None,
    hamtare: Hamtare | None = None,
    kall_epsg: int = 4326,
    transformator: TransformatorPunkt | None = None,
    limit: int = 1000,
) -> list[SguJordartYta]:
    """Hämtar SGU jordarter via OGC API Features och mappar till Flodens jordarter."""
    if len(bbox_wgs84) != 4:
        raise DataInlasningFel("bbox_wgs84 måste innehålla 4 värden.")
    min_x, min_y, max_x, max_y = (float(v) for v in bbox_wgs84)
    if not all(math.isfinite(v) for v in (min_x, min_y, max_x, max_y)):
        raise DataInlasningFel("bbox_wgs84 får endast innehålla ändliga tal.")
    if min_x >= max_x or min_y >= max_y:
        raise DataInlasningFel(f"Ogiltig bbox_wgs84: {bbox_wgs84}.")

    params = urllib.parse.urlencode(
        {
            "bbox": f"{min_x},{min_y},{max_x},{max_y}",
            "limit": str(int(limit)),
            "f": "json",
        }
    )
    url = f"{SGU_JORDARTER_URL}?{params}"
    eff_cache = cache if cache is not None else LokalDataCache(hamtare=hamtare)

    geojson: dict[str, Any]
    if cache is not None:
        geojson, _ = eff_cache.hamta_json(
            url,
            filnamn="sgu_jordarter.geojson",
            kalla="SGU Jordarter 25k-100k",
            headers={"Accept": "application/geo+json, application/json"},
        )
    else:
        raw = (
            eff_cache._hamtare(url, {"Accept": "application/geo+json, application/json"})
            if hamtare is None
            else hamtare(url, {"Accept": "application/geo+json, application/json"})
        )
        try:
            tolkat = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataInlasningFel(f"Kunde inte tolka SGU-svar från '{url}': {exc}") from exc
        if not isinstance(tolkat, dict):
            raise DataInlasningFel("SGU-svar måste vara ett JSON-objekt.")
        geojson = tolkat

    return tolka_sgu_geojson_features(
        geojson,
        kall_epsg=kall_epsg,
        transformator=transformator,
    )


def _berakna_linjelangd_m(punkter: tuple[tuple[float, float], ...]) -> float:
    langd = 0.0
    for (x0, y0), (x1, y1) in zip(punkter[:-1], punkter[1:], strict=True):
        langd += math.hypot(x1 - x0, y1 - y0)
    return langd


def _tolka_hastighet_km_h(props: Mapping[str, Any], kant_id: str) -> float:
    for falt in ("hastighet_km_h", "HTHAST", "hthast", "hastighet", "maxspeed"):
        if falt in props and props[falt] is not None:
            varde = props[falt]
            if isinstance(varde, bool):
                continue
            try:
                tal = float(varde)
            except (TypeError, ValueError) as exc:
                raise DataInlasningFel(
                    f"Ogiltig hastighet '{varde}' i fält '{falt}' för '{kant_id}'."
                ) from exc
            if math.isfinite(tal) and tal > 0.0:
                return tal
    return 50.0


def _tolka_kant_typ(props: Mapping[str, Any], kant_id: str) -> str:
    typ_raw = props.get("typ")
    if isinstance(typ_raw, str) and typ_raw.strip():
        norm = typ_raw.strip().lower()
        if norm in ("mark", "bro", "tunnel"):
            return norm
        raise DataInlasningFel(f"Ogiltig vägtyp '{typ_raw}' för '{kant_id}'.")

    if bool(props.get("bro")) or str(props.get("bro_och_tunnel", "")).lower() == "bro":
        return "bro"
    if bool(props.get("tunnel")) or str(props.get("bro_och_tunnel", "")).lower() == "tunnel":
        return "tunnel"
    return "mark"


def _tolka_enkelriktad(props: Mapping[str, Any]) -> bool:
    for falt in ("enkelriktad", "oneway"):
        if falt in props and props[falt] is not None:
            val = props[falt]
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() in ("true", "ja", "yes", "1", "med", "mot")
            if isinstance(val, int | float):
                return bool(val)
    return False


def _nod_nyckel(e_m: float, n_m: float) -> tuple[int, int]:
    return (round(e_m * 1000.0), round(n_m * 1000.0))


def las_nvdb_vagnat_geojson(
    geojson: Mapping[str, Any],
    *,
    kall_epsg: int = EPSG_INTERN,
    transformator: TransformatorPunkt | None = None,
) -> VagNat:
    """Bygger ett validerat `rutt.vagnat.VagNat` från NVDB-liknande GeoJSON."""
    features_raw = geojson.get("features")
    if not isinstance(features_raw, list):
        raise DataInlasningFel("NVDB GeoJSON saknar lista 'features'.")

    graf = VagNat()
    nod_index: dict[tuple[int, int], str] = {}
    nod_raknare = 0

    def _hamta_eller_skapa_nod(e_m: float, n_m: float, z_rh2000: float) -> str:
        nonlocal nod_raknare
        nyckel = _nod_nyckel(e_m, n_m)
        if nyckel in nod_index:
            return nod_index[nyckel]
        nod_raknare += 1
        nod_id = f"n_{nod_raknare}"
        graf.lagg_till_nod(VagNod(nod_id=nod_id, e_m=e_m, n_m=n_m, z_rh2000=z_rh2000))
        nod_index[nyckel] = nod_id
        return nod_id

    for idx, feat in enumerate(features_raw):
        if not isinstance(feat, dict):
            raise DataInlasningFel(f"NVDB-feature på index {idx} är inte ett JSON-objekt.")
        props = feat.get("properties")
        if not isinstance(props, dict):
            props = {}
        geom = feat.get("geometry")
        if not isinstance(geom, dict):
            raise DataInlasningFel(f"NVDB-feature på index {idx} saknar 'geometry'.")
        if geom.get("type") != "LineString":
            raise DataInlasningFel(
                f"NVDB-feature på index {idx} måste ha geometri 'LineString', "
                f"fick '{geom.get('type')}'."
            )

        coords_raw = geom.get("coordinates")
        if not isinstance(coords_raw, list) or len(coords_raw) < 2:
            raise DataInlasningFel(
                f"NVDB-feature på index {idx} måste ha minst 2 koordinater."
            )

        kant_id_raw = feat.get("id") or props.get("id") or props.get("rlid") or f"kant_{idx}"
        kant_id = str(kant_id_raw)

        punkter_3011: list[tuple[float, float]] = []
        z_lista: list[float] = []
        for pt in coords_raw:
            if not isinstance(pt, list | tuple) or len(pt) < 2:
                raise DataInlasningFel(f"Ogiltig koordinat i NVDB-feature '{kant_id}'.")
            e_m, n_m = _transformera_punkt(float(pt[0]), float(pt[1]), kall_epsg, transformator)
            punkter_3011.append((e_m, n_m))
            z_lista.append(float(pt[2]) if len(pt) >= 3 and pt[2] is not None else 0.0)

        vagbana_raw = props.get("vagbana_rh2000")
        vagbana_rh2000: float | None = None
        if vagbana_raw is not None and not isinstance(vagbana_raw, bool):
            try:
                vagbana_rh2000 = float(vagbana_raw)
            except (TypeError, ValueError) as exc:
                raise DataInlasningFel(
                    f"Ogiltig vagbana_rh2000 '{vagbana_raw}' för '{kant_id}'."
                ) from exc

        z_start = vagbana_rh2000 if vagbana_rh2000 is not None else z_lista[0]
        z_slut = vagbana_rh2000 if vagbana_rh2000 is not None else z_lista[-1]

        fran_nod = _hamta_eller_skapa_nod(punkter_3011[0][0], punkter_3011[0][1], z_start)
        till_nod = _hamta_eller_skapa_nod(punkter_3011[-1][0], punkter_3011[-1][1], z_slut)

        langd_raw = props.get("langd_m")
        if langd_raw is not None and not isinstance(langd_raw, bool):
            langd_m = float(langd_raw)
        else:
            langd_m = _berakna_linjelangd_m(tuple(punkter_3011))

        typ = _tolka_kant_typ(props, kant_id)
        hastighet_km_h = _tolka_hastighet_km_h(props, kant_id)
        enkelriktad = _tolka_enkelriktad(props)

        try:
            kant = VagKant(
                kant_id=kant_id,
                fran_nod=fran_nod,
                till_nod=till_nod,
                langd_m=langd_m,
                normal_hastighet_km_h=hastighet_km_h,
                typ=typ,
                vagbana_rh2000=vagbana_rh2000,
                koordinater=tuple(punkter_3011),
                enkelriktad=enkelriktad,
            )
            graf.lagg_till_kant(kant)
        except VagNatFel as exc:
            raise DataInlasningFel(f"Kunde inte skapa vägnätskant '{kant_id}': {exc}") from exc

    return graf


def hamta_nvdb_vagnat(
    url: str,
    *,
    cache: LokalDataCache | None = None,
    hamtare: Hamtare | None = None,
    kall_epsg: int = EPSG_INTERN,
    transformator: TransformatorPunkt | None = None,
    headers: Mapping[str, str] | None = None,
) -> VagNat:
    """Hämtar NVDB GeoJSON (via cache om angiven) och bygger `VagNat` i EPSG:3011."""
    if cache is not None:
        geojson, _ = cache.hamta_json(
            url,
            filnamn="nvdb_vagnat.geojson",
            kalla="Trafikverket NVDB",
            headers=headers,
        )
    else:
        eff_cache = LokalDataCache(hamtare=hamtare)
        raw = eff_cache._hamtare(url, headers) if hamtare is None else hamtare(url, headers)
        try:
            tolkat = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataInlasningFel(f"Kunde inte tolka NVDB GeoJSON från '{url}': {exc}") from exc
        if not isinstance(tolkat, dict):
            raise DataInlasningFel("NVDB-svar måste vara ett JSON-objekt.")
        geojson = tolkat

    return las_nvdb_vagnat_geojson(
        geojson,
        kall_epsg=kall_epsg,
        transformator=transformator,
    )
