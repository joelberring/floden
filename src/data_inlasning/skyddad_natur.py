# ruff: noqa: E501
"""Öppna ekologiska skrivbordsdata: Naturvårdsverket WFS (Skyddad natur & Natura 2000),
Skogsstyrelsen ArcGIS REST och Naturvårdsverkets VMI läns-ZIP (CC0, 0 nycklar).

Alla externa anrop går mot en strikt HTTPS-värdallowlist (`geodata.naturvardsverket.se`
och `geodpags.skogsstyrelsen.se`), stöder injicerbar `hamtare` för 100 % nätverksfria
enhetstester och omprojicerar geometrier till EPSG:3011 (`SWEREF 99 18 00`) vid inläsning.
Inga personuppgifter eller fastighets-/ägarattribut sparas någonsin.
"""

from __future__ import annotations

import inspect
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pyproj import Transformer
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from data_inlasning.cache import DataInlasningFel
from gemensamt.hash import sha256_bytes, sha256_fil
from gemensamt.koordinater import EPSG_INTERN, transformera

EPSG_SWEREF99_TM: Final[int] = 3006
EPSG_WGS84: Final[int] = 4326

NVR_WFS_URL: Final[str] = "https://geodata.naturvardsverket.se/inspire/ps-nvr/ows"
N2K_WFS_URL: Final[str] = "https://geodata.naturvardsverket.se/inspire/ps-n2k/ows"
VMI_NEDLADDNING_BAS_URL: Final[str] = "https://geodata.naturvardsverket.se/nedladdning/VMI"
SKOGSSTYRELSEN_BAS_URL: Final[str] = "https://geodpags.skogsstyrelsen.se/arcgis/rest/services"

TILLATNA_NV_VARDAR: Final[frozenset[str]] = frozenset({"geodata.naturvardsverket.se"})
TILLATNA_SKS_VARDAR: Final[frozenset[str]] = frozenset({"geodpags.skogsstyrelsen.se"})

NV_SKYDDSFORMER: Final[dict[str, tuple[str, str]]] = {
    "NR": ("ps-nvr:PS.ProtectedSites.NR", "Naturreservat"),
    "NP": ("ps-nvr:PS.ProtectedSites.NP", "Nationalpark"),
    "KR": ("ps-nvr:PS.ProtectedSites.KR", "Kulturreservat"),
    "NM": ("ps-nvr:PS.ProtectedSites.NM", "Naturminne"),
    "NVO": ("ps-nvr:PS.ProtectedSites.NVO", "Naturvårdsområde"),
    "DVO": ("ps-nvr:PS.ProtectedSites.DVO", "Djur- och växtskyddsområde"),
    "OBO": ("ps-nvr:PS.ProtectedSites.OBO", "Övrigt biotopskyddsområde"),
    "IF": ("ps-nvr:PS.ProtectedSites.IF", "Interimistiskt förbud"),
}
N2K_LAGER: Final[str] = "ps-n2k:PS.ProtectedSites.Natura2000"
N2K_NAMN: Final[str] = "Natura 2000"

SKOGSSTYRELSEN_TJANSTER: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "nyckelbiotop",
        "Nyckelbiotop",
        "Geodataportal/GeodataportalVisaNyckelbiotop/MapServer/0/query",
    ),
    (
        "skogsbruk_nyckelbiotop",
        "Nyckelbiotop (storskogsbruk)",
        "Geodataportal/GeodataportalVisaSkogsbruknyckelbiotop/MapServer/0/query",
    ),
    (
        "naturvarde",
        "Objekt med naturvärde",
        "Geodataportal/GeodataportalVisaObjektnaturvarde/MapServer/0/query",
    ),
    (
        "sumpskog",
        "Sumpskog",
        "Geodataportal/GeodataportalVisaSumpskog/MapServer/0/query",
    ),
    (
        "biotopskydd",
        "Biotopskyddsområde (skog)",
        "Geodataportal/GeodataportalVisaBiotopskydd/MapServer/0/query",
    ),
    (
        "naturvardsavtal",
        "Naturvårdsavtal",
        "Geodataportal/GeodataportalVisaNaturvardsavtal/MapServer/0/query",
    ),
)

_LAN_KATALOG_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HREF_RE: Final[re.Pattern[str]] = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
_EPSG_RE: Final[re.Pattern[str]] = re.compile(r"EPSG[:/]+(\d{4,5})", re.I)
_XML_FARLIGT_RE: Final[re.Pattern[bytes]] = re.compile(rb"<!(?:DOCTYPE|ENTITY)\b", re.I)

HamtareFn = Callable[..., bytes]


class SkyddadNaturFel(DataInlasningFel):
    """Fel vid hämtning, SSRF-kontroll eller tolkning av skyddad natur, Skogsstyrelsen eller VMI."""


@dataclass(frozen=True)
class SkyddatOmrade:
    """Ett skyddat område (Naturvårdsverket NVR eller Natura 2000) i EPSG:3011."""

    omrade_id: str
    namn: str
    skyddsform: str
    skyddsform_namn: str
    lager: str
    beslutsdatum: str | None
    kalla: str
    geometri_3011: BaseGeometry
    area_m2: float
    overlapp_karna_m2: float
    overlappar_karna: bool
    minsta_avstand_m: float

    def som_geojson_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "id": self.omrade_id,
            "properties": {
                "omrade_id": self.omrade_id,
                "namn": self.namn,
                "skyddsform": self.skyddsform,
                "skyddsform_namn": self.skyddsform_namn,
                "lager": self.lager,
                "beslutsdatum": self.beslutsdatum,
                "kalla": self.kalla,
                "area_m2": round(self.area_m2, 2),
                "overlapp_karna_m2": round(self.overlapp_karna_m2, 2),
                "overlappar_karna": self.overlappar_karna,
                "minsta_avstand_m": round(self.minsta_avstand_m, 2),
            },
            "geometry": mapping(self.geometri_3011),
        }


@dataclass(frozen=True)
class SkyddadNaturResultat:
    """Samlat resultat från Naturvårdsverkets INSPIRE WFS (EPSG:3011)."""

    karna_3011: tuple[float, float, float, float]
    buffert_m: float
    omraden: tuple[SkyddatOmrade, ...]
    kallor_sha256: Mapping[str, str]
    varningar: tuple[str, ...] = ()
    licens: str = "CC0"
    kalla: str = "Naturvårdsverket (INSPIRE Skyddad natur & Natura 2000)"


@dataclass(frozen=True)
class SkogligtNaturvarde:
    """Ett skogligt naturvärdesobjekt från Skogsstyrelsen ArcGIS REST i EPSG:3011."""

    objekt_id: str
    kategori: str
    kategori_namn: str
    namn: str
    biotoptyp: str | None
    beslutsdatum: str | None
    tjanst: str
    kalla: str
    geometri_3011: BaseGeometry
    area_m2: float
    overlapp_karna_m2: float
    overlappar_karna: bool
    minsta_avstand_m: float

    def som_geojson_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "id": self.objekt_id,
            "properties": {
                "objekt_id": self.objekt_id,
                "kategori": self.kategori,
                "kategori_namn": self.kategori_namn,
                "namn": self.namn,
                "biotoptyp": self.biotoptyp,
                "beslutsdatum": self.beslutsdatum,
                "tjanst": self.tjanst,
                "kalla": self.kalla,
                "area_m2": round(self.area_m2, 2),
                "overlapp_karna_m2": round(self.overlapp_karna_m2, 2),
                "overlappar_karna": self.overlappar_karna,
                "minsta_avstand_m": round(self.minsta_avstand_m, 2),
            },
            "geometry": mapping(self.geometri_3011),
        }


@dataclass(frozen=True)
class SkogligaVardenResultat:
    """Samlat resultat från Skogsstyrelsens ArcGIS REST-tjänster (EPSG:3011)."""

    karna_3011: tuple[float, float, float, float]
    buffert_m: float
    objekt: tuple[SkogligtNaturvarde, ...]
    kallor_sha256: Mapping[str, str]
    varningar: tuple[str, ...] = ()
    licens: str = "CC0"
    kalla: str = "Skogsstyrelsen (Geodataportal ArcGIS REST)"


@dataclass(frozen=True)
class VmiPaketPost:
    """Hämtat och SHA-256-verifierat VMI-länspaket redo för `data_inlasning.vmi.rastrera_vmi`."""

    lan_katalog: str
    zip_fil: Path
    gis_fil: Path | None
    sha256: str
    url: str
    fran_cache: bool
    licens: str = "CC0"
    kalla: str = "Naturvårdsverket, Våtmarksinventeringen (VMI, 1981–2005)"


# ------------------------------------------------------------------ säker URL & HTTP


def _kontrollera_url(url: str, tillatna_vardar: frozenset[str], tjanstnamn: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise SkyddadNaturFel(f"Endast HTTPS tillåts för {tjanstnamn}, fick {url!r}.")
    if parsed.username or parsed.password:
        raise SkyddadNaturFel(f"Inloggningsuppgifter i URL tillåts inte för {tjanstnamn}.")
    host = (parsed.hostname or "").lower()
    if host not in tillatna_vardar:
        raise SkyddadNaturFel(
            f"Värden {host!r} hör inte till tillåtna värdar för {tjanstnamn}: {sorted(tillatna_vardar)}."
        )


def _standard_hamtare(url: str, headers: Mapping[str, str] | None = None, *, timeout_s: float = 25.0) -> bytes:
    req_headers = dict(headers) if headers is not None else {}
    req_headers.setdefault("User-Agent", "Floden/1.0 (ekologi-oppnadata)")
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - HTTPS + allowlist
            return bytes(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise SkyddadNaturFel(f"Kunde inte hämta data från {urllib.parse.urlsplit(url).hostname}: {exc}") from exc


def _anropa_hamtare(
    hamtare: HamtareFn | None,
    url: str,
    tillatna_vardar: frozenset[str],
    tjanstnamn: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_s: float = 25.0,
) -> bytes:
    _kontrollera_url(url, tillatna_vardar, tjanstnamn)
    if hamtare is None:
        return _standard_hamtare(url, headers, timeout_s=timeout_s)
    sig = inspect.signature(hamtare)
    pos_params = [
        p
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
    ra = hamtare(url, headers) if (has_varargs or len(pos_params) >= 2) else hamtare(url)
    if not isinstance(ra, bytes):
        raise SkyddadNaturFel(f"Hämtaren för {tjanstnamn} returnerade {type(ra).__name__}, förväntade bytes.")
    return ra


# ------------------------------------------------------------------ geometri & CRS


def _validera_bbox_3011(
    bbox_3011: tuple[float, float, float, float], buffert_m: float
) -> tuple[float, float, float, float]:
    if len(bbox_3011) != 4:
        raise SkyddadNaturFel("bbox_3011 måste innehålla fyra koordinater (e_min, n_min, e_max, n_max).")
    e0, n0, e1, n1 = (float(v) for v in bbox_3011)
    if not all(math.isfinite(v) for v in (e0, n0, e1, n1)):
        raise SkyddadNaturFel("bbox_3011 får enbart innehålla ändliga tal.")
    if e0 >= e1 or n0 >= n1:
        raise SkyddadNaturFel(f"Ogiltig bbox_3011: {bbox_3011}.")
    b = float(buffert_m)
    if not math.isfinite(b) or b < 0.0:
        raise SkyddadNaturFel(f"buffert_m måste vara ett ändligt tal >= 0, fick {buffert_m!r}.")
    return (e0, n0, e1, n1)


def _bbox_3011_till_3006(bbox_3011: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    e0, n0, e1, n1 = bbox_3011
    horn = [
        transformera(x, y, EPSG_INTERN, EPSG_SWEREF99_TM)
        for x, y in ((e0, n0), (e0, n1), (e1, n0), (e1, n1))
    ]
    return (
        min(p[0] for p in horn),
        min(p[1] for p in horn),
        max(p[0] for p in horn),
        max(p[1] for p in horn),
    )


def _epsg_fran_strang(text: str | None, standard: int) -> int:
    if not text:
        return standard
    s = str(text).strip()
    if "CRS84" in s.upper():
        return EPSG_WGS84
    m = _EPSG_RE.search(s)
    return int(m.group(1)) if m else standard


def _omprojicera_till_3011(geom: BaseGeometry, kall_epsg: int) -> BaseGeometry:
    if kall_epsg == EPSG_INTERN:
        return geom
    tr = Transformer.from_crs(f"EPSG:{kall_epsg}", f"EPSG:{EPSG_INTERN}", always_xy=True)
    return shapely_transform(tr.transform, geom)


def _normalisera_axelordning(par: list[tuple[float, float]], epsg: int) -> list[tuple[float, float]]:
    """Säkerställer (x=E/lon, y=N/lat) för svenska koordinater när INSPIRE GML använder (N, E) eller (lat, lon)."""
    if not par:
        return par
    x0, y0 = par[0]
    if epsg == EPSG_WGS84:
        # Sverige: lat ~ 55–70, lon ~ 10–25
        if 50.0 <= x0 <= 72.0 and 5.0 <= y0 <= 30.0:
            return [(y, x) for x, y in par]
    elif epsg == EPSG_SWEREF99_TM:
        # Sverige SWEREF 99 TM: N ~ 6_000_000–7_800_000, E ~ 200_000–1_100_000
        if x0 > 5_000_000.0 and y0 < 2_000_000.0:
            return [(y, x) for x, y in par]
    elif epsg == EPSG_INTERN and x0 > 5_000_000.0 and y0 < 2_000_000.0:
        return [(y, x) for x, y in par]
    return par


def _rensa_text(v: Any, max_len: int = 160) -> str:
    if v is None:
        return ""
    s = " ".join(str(v).split()).strip()
    return s[:max_len]


def _forsta_falt(props: Mapping[str, Any], kandidater: Sequence[str]) -> str | None:
    lag = {str(k).lower(): v for k, v in props.items()}
    for kand in kandidater:
        v = lag.get(kand.lower())
        if v is not None and str(v).strip() and str(v).strip().lower() not in ("null", "none"):
            return _rensa_text(v)
    return None


def _avstand_och_overlapp(geom_3011: BaseGeometry, karna_box: Polygon) -> tuple[float, float, bool, float]:
    area = float(geom_3011.area) if geom_3011.geom_type in ("Polygon", "MultiPolygon") else 0.0
    overlappar = bool(geom_3011.intersects(karna_box))
    overlapp_m2 = 0.0
    if overlappar and area > 0.0:
        snitt = geom_3011.intersection(karna_box)
        overlapp_m2 = float(snitt.area)
    avstand = 0.0 if overlappar else float(geom_3011.distance(karna_box))
    return area, overlapp_m2, overlappar, avstand


# ------------------------------------------------------------------ GML/XML & GeoJSON tolkning (NV WFS)


def _lokalnamn(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _tolka_pos_lista(text: str, dim: int, epsg: int) -> list[tuple[float, float]]:
    tal = [float(t) for t in text.split()]
    if dim < 2 or len(tal) < dim:
        return []
    steg = dim if len(tal) % dim == 0 else 2
    par = [(tal[i], tal[i + 1]) for i in range(0, len(tal) - 1, steg)]
    return _normalisera_axelordning(par, epsg)


def _gml_ring(el: ET.Element, epsg: int) -> list[tuple[float, float]]:
    for nod in el.iter():
        ln = _lokalnamn(nod.tag)
        if ln == "posList" and nod.text:
            dim = int(nod.attrib.get("srsDimension", "2"))
            return _tolka_pos_lista(nod.text, dim, epsg)
        if ln == "coordinates" and nod.text:
            par: list[tuple[float, float]] = []
            for pkt in nod.text.strip().split():
                delar = pkt.split(",")
                if len(delar) >= 2:
                    par.append((float(delar[0]), float(delar[1])))
            return _normalisera_axelordning(par, epsg)
    return []


def _gml_polygon(poly_el: ET.Element, epsg: int) -> Polygon | None:
    yttre: list[tuple[float, float]] = []
    inre: list[list[tuple[float, float]]] = []
    for child in poly_el:
        ln = _lokalnamn(child.tag)
        if ln in ("exterior", "outerBoundaryIs"):
            yttre = _gml_ring(child, epsg)
        elif ln in ("interior", "innerBoundaryIs"):
            ring = _gml_ring(child, epsg)
            if len(ring) >= 3:
                inre.append(ring)
    if len(yttre) < 3:
        return None
    poly = Polygon(yttre, inre)
    return poly if not poly.is_empty else None


def _gml_geometri(feature_el: ET.Element, standard_epsg: int) -> tuple[BaseGeometry | None, int]:
    epsg = standard_epsg
    for nod in feature_el.iter():
        srs = nod.attrib.get("srsName")
        if srs:
            epsg = _epsg_fran_strang(srs, epsg)
            break
    polygoner: list[Polygon] = []
    for nod in feature_el.iter():
        ln = _lokalnamn(nod.tag)
        if ln in ("Polygon", "PolygonPatch"):
            loc_epsg = _epsg_fran_strang(nod.attrib.get("srsName"), epsg)
            p = _gml_polygon(nod, loc_epsg)
            if p is not None:
                polygoner.append(p)
                epsg = loc_epsg
    if len(polygoner) == 1:
        return polygoner[0], epsg
    if len(polygoner) > 1:
        return MultiPolygon(polygoner), epsg
    for nod in feature_el.iter():
        if _lokalnamn(nod.tag) == "Point":
            loc_epsg = _epsg_fran_strang(nod.attrib.get("srsName"), epsg)
            for sub in nod.iter():
                if _lokalnamn(sub.tag) == "pos" and sub.text:
                    par = _tolka_pos_lista(sub.text, 2, loc_epsg)
                    if par:
                        return Point(par[0]), loc_epsg
    return None, epsg


def _gml_attribut(feature_el: ET.Element) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for k, v in feature_el.attrib.items():
        if _lokalnamn(k) == "id" and v:
            props["gml_id"] = v
    for nod in feature_el.iter():
        ln = _lokalnamn(nod.tag)
        if ln in ("Polygon", "PolygonPatch", "MultiSurface", "Surface", "Point", "posList", "pos", "geometry"):
            continue
        if nod.text and nod.text.strip():
            txt = nod.text.strip()
            if ln == "text" and "namn" not in props:
                props["namn"] = txt
            elif ln == "localId" and "local_id" not in props:
                props["local_id"] = txt
            elif ln == "legalFoundationDate" and "beslutsdatum" not in props:
                props["beslutsdatum"] = txt[:10]
            elif ln not in props:
                props[ln] = txt
    return props


def _tolka_wfs_svar(
    ra: bytes,
    *,
    skyddsform: str,
    skyddsform_namn: str,
    lager: str,
    karna_box: Polygon,
    kalla: str,
) -> list[SkyddatOmrade]:
    trim = ra.lstrip()
    if not trim:
        return []
    ut: list[SkyddatOmrade] = []
    if trim.startswith(b"{"):
        try:
            dok = json.loads(ra.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkyddadNaturFel(f"WFS-svaret för {lager} kunde inte tolkas som JSON: {exc}") from exc
        if not isinstance(dok, dict) or dok.get("type") != "FeatureCollection":
            raise SkyddadNaturFel(f"WFS-svaret för {lager} saknar GeoJSON FeatureCollection.")
        crs_namn = ""
        if isinstance(dok.get("crs"), Mapping):
            crs_namn = str(dok["crs"].get("properties", {}).get("name", ""))
        epsg = _epsg_fran_strang(crs_namn, EPSG_SWEREF99_TM if crs_namn else EPSG_WGS84)
        for idx, feat in enumerate(dok.get("features") or []):
            if not isinstance(feat, Mapping) or not isinstance(feat.get("geometry"), Mapping):
                continue
            props_raw = feat.get("properties")
            props: Mapping[str, Any] = props_raw if isinstance(props_raw, Mapping) else {}
            g_raw = shape(feat["geometry"])
            if g_raw.is_empty:
                continue
            # Detektera EPSG:3006 automatiskt om koordinater > 180 men ingen crs skickats
            bounds = g_raw.bounds
            eff_epsg = epsg
            if not crs_namn and (abs(bounds[0]) > 180.0 or abs(bounds[1]) > 90.0):
                eff_epsg = EPSG_SWEREF99_TM
            g_3011 = _omprojicera_till_3011(g_raw, eff_epsg)
            area, ov_m2, ov, avst = _avstand_och_overlapp(g_3011, karna_box)
            oid = (
                _forsta_falt(props, ("nvrid", "sitecode", "localid", "inspireid", "id", "objektid"))
                or _rensa_text(feat.get("id"))
                or f"{skyddsform}_{idx}"
            )
            namn = (
                _forsta_falt(props, ("namn", "sitename", "name", "omradesnamn", "text"))
                or f"{skyddsform_namn} ({oid})"
            )
            beslut = _forsta_falt(props, ("beslutsdatum", "legalfoundationdate", "ikraft", "ursprbesl"))
            ut.append(
                SkyddatOmrade(
                    omrade_id=oid,
                    namn=namn,
                    skyddsform=skyddsform,
                    skyddsform_namn=skyddsform_namn,
                    lager=lager,
                    beslutsdatum=beslut[:10] if beslut else None,
                    kalla=kalla,
                    geometri_3011=g_3011,
                    area_m2=area,
                    overlapp_karna_m2=ov_m2,
                    overlappar_karna=ov,
                    minsta_avstand_m=avst,
                )
            )
        return ut

    if trim.startswith(b"<"):
        if _XML_FARLIGT_RE.search(ra):
            raise SkyddadNaturFel(f"WFS XML-svar för {lager} innehåller otillåten DOCTYPE/ENTITY-deklaration.")
        try:
            rot = ET.fromstring(ra)
        except ET.ParseError as exc:
            raise SkyddadNaturFel(f"WFS XML/GML-svaret för {lager} kunde inte tolkas: {exc}") from exc
        if _lokalnamn(rot.tag) == "ExceptionReport":
            msg = " ".join(
                el.text.strip() for el in rot.iter() if _lokalnamn(el.tag) == "ExceptionText" and el.text
            )
            raise SkyddadNaturFel(f"WFS ExceptionReport för {lager}: {msg or 'okänt fel'}")
        medlemmar = [
            el for el in rot.iter() if _lokalnamn(el.tag) in ("member", "featureMember")
        ]
        if not medlemmar:
            medlemmar = [el for el in rot.iter() if "ProtectedSite" in _lokalnamn(el.tag)]
        for idx, medlem in enumerate(medlemmar):
            feat_el = next(iter(medlem), medlem) if _lokalnamn(medlem.tag) in ("member", "featureMember") else medlem
            g_raw, epsg = _gml_geometri(feat_el, EPSG_SWEREF99_TM)
            if g_raw is None or g_raw.is_empty:
                continue
            g_3011 = _omprojicera_till_3011(g_raw, epsg)
            area, ov_m2, ov, avst = _avstand_och_overlapp(g_3011, karna_box)
            props = _gml_attribut(feat_el)
            oid = (
                _forsta_falt(props, ("local_id", "nvrid", "sitecode", "gml_id", "id"))
                or f"{skyddsform}_{idx}"
            )
            namn = (
                _forsta_falt(props, ("namn", "sitename", "name", "text"))
                or f"{skyddsform_namn} ({oid})"
            )
            beslut = _forsta_falt(props, ("beslutsdatum", "legalfoundationdate"))
            ut.append(
                SkyddatOmrade(
                    omrade_id=oid,
                    namn=namn,
                    skyddsform=skyddsform,
                    skyddsform_namn=skyddsform_namn,
                    lager=lager,
                    beslutsdatum=beslut[:10] if beslut else None,
                    kalla=kalla,
                    geometri_3011=g_3011,
                    area_m2=area,
                    overlapp_karna_m2=ov_m2,
                    overlappar_karna=ov,
                    minsta_avstand_m=avst,
                )
            )
        return ut

    raise SkyddadNaturFel(f"Okänt svarsformat från WFS för {lager} (varken GeoJSON eller GML/XML).")


# ------------------------------------------------------------------ publika funktioner


def hamta_skyddad_natur_wfs(
    bbox_3011: tuple[float, float, float, float],
    *,
    buffert_m: float = 1000.0,
    hamtare: HamtareFn | None = None,
    skyddsformer: Sequence[str] | None = None,
    inkludera_natura2000: bool = True,
    nvr_wfs_url: str = NVR_WFS_URL,
    n2k_wfs_url: str = N2K_WFS_URL,
    max_objekt: int = 5000,
    timeout_s: float = 25.0,
) -> SkyddadNaturResultat:
    """Hämtar skyddade områden (NVR + Natura 2000) från Naturvårdsverkets INSPIRE WFS (CC0).

    Hanterar både GeoJSON och GML/XML robust, omprojicerar till EPSG:3011 och räknar
    exakt överlappsarea (m²) samt minsta avstånd (m) till kärnan `bbox_3011`.
    """
    karna = _validera_bbox_3011(bbox_3011, buffert_m)
    _kontrollera_url(nvr_wfs_url, TILLATNA_NV_VARDAR, "Naturvårdsverket WFS")
    _kontrollera_url(n2k_wfs_url, TILLATNA_NV_VARDAR, "Naturvårdsverket Natura 2000 WFS")

    e0, n0, e1, n1 = karna
    sok_3011 = (e0 - buffert_m, n0 - buffert_m, e1 + buffert_m, n1 + buffert_m)
    sx0, sy0, sx1, sy1 = _bbox_3011_till_3006(sok_3011)
    karna_box = box(*karna)

    valda_koder = list(NV_SKYDDSFORMER) if skyddsformer is None else list(skyddsformer)
    for kod in valda_koder:
        if kod not in NV_SKYDDSFORMER:
            raise SkyddadNaturFel(
                f"Okänd skyddsform {kod!r}. Giltiga: {', '.join(sorted(NV_SKYDDSFORMER))}."
            )

    anrop: list[tuple[str, str, str, str]] = [
        (kod, NV_SKYDDSFORMER[kod][1], NV_SKYDDSFORMER[kod][0], nvr_wfs_url) for kod in valda_koder
    ]
    if inkludera_natura2000:
        anrop.append(("Natura2000", N2K_NAMN, N2K_LAGER, n2k_wfs_url))

    alla: list[SkyddatOmrade] = []
    kallor_sha: dict[str, str] = {}
    varningar: list[str] = []

    for kod, sv_namn, type_name, bas_url in anrop:
        params = urllib.parse.urlencode(
            {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": type_name,
                "srsName": f"EPSG:{EPSG_SWEREF99_TM}",
                "bbox": f"{sx0:.2f},{sy0:.2f},{sx1:.2f},{sy1:.2f},EPSG:{EPSG_SWEREF99_TM}",
                "outputFormat": "application/json",
                "count": str(int(max_objekt)),
            }
        )
        url = f"{bas_url}?{params}"
        ra = _anropa_hamtare(
            hamtare,
            url,
            TILLATNA_NV_VARDAR,
            "Naturvårdsverket WFS",
            timeout_s=timeout_s,
        )
        try:
            poster = _tolka_wfs_svar(
                ra,
                skyddsform=kod,
                skyddsform_namn=sv_namn,
                lager=type_name,
                karna_box=karna_box,
                kalla="Naturvårdsverket INSPIRE WFS (CC0)",
            )
        except SkyddadNaturFel as exc:
            # Fallback utan outputFormat=application/json (ren INSPIRE GML 3.2) om servern avvisar JSON.
            if "ExceptionReport" in str(exc) or "outputFormat" in str(exc).lower():
                gml_params = urllib.parse.urlencode(
                    {
                        "service": "WFS",
                        "version": "2.0.0",
                        "request": "GetFeature",
                        "typeNames": type_name,
                        "srsName": f"EPSG:{EPSG_SWEREF99_TM}",
                        "bbox": f"{sx0:.2f},{sy0:.2f},{sx1:.2f},{sy1:.2f},EPSG:{EPSG_SWEREF99_TM}",
                        "count": str(int(max_objekt)),
                    }
                )
                ra = _anropa_hamtare(
                    hamtare,
                    f"{bas_url}?{gml_params}",
                    TILLATNA_NV_VARDAR,
                    "Naturvårdsverket WFS",
                    timeout_s=timeout_s,
                )
                poster = _tolka_wfs_svar(
                    ra,
                    skyddsform=kod,
                    skyddsform_namn=sv_namn,
                    lager=type_name,
                    karna_box=karna_box,
                    kalla="Naturvårdsverket INSPIRE WFS (CC0)",
                )
            else:
                raise
        kallor_sha[type_name] = sha256_bytes(ra)
        alla.extend(poster)

    alla.sort(key=lambda o: (round(o.minsta_avstand_m, 3), o.skyddsform, o.namn, o.omrade_id))
    return SkyddadNaturResultat(
        karna_3011=karna,
        buffert_m=float(buffert_m),
        omraden=tuple(alla),
        kallor_sha256=kallor_sha,
        varningar=tuple(varningar),
    )


def hamta_skogsstyrelsen_naturvarden(
    bbox_3011: tuple[float, float, float, float],
    *,
    buffert_m: float = 1000.0,
    hamtare: HamtareFn | None = None,
    tjanster: Sequence[tuple[str, str, str]] = SKOGSSTYRELSEN_TJANSTER,
    bas_url: str = SKOGSSTYRELSEN_BAS_URL,
    sidstorlek: int = 1000,
    max_sidor: int = 20,
    timeout_s: float = 25.0,
) -> SkogligaVardenResultat:
    """Hämtar skogliga naturvärden (nyckelbiotoper, sumpskogar, biotopskydd m.fl.) från Skogsstyrelsen ArcGIS REST.

    Stöder full `resultOffset`-paginering, omprojicerar till EPSG:3011 och räknar överlapp och avstånd till kärnan.
    """
    karna = _validera_bbox_3011(bbox_3011, buffert_m)
    _kontrollera_url(bas_url, TILLATNA_SKS_VARDAR, "Skogsstyrelsen ArcGIS REST")
    if sidstorlek < 1 or max_sidor < 1:
        raise SkyddadNaturFel("sidstorlek och max_sidor måste vara >= 1.")

    e0, n0, e1, n1 = karna
    sok_3011 = (e0 - buffert_m, n0 - buffert_m, e1 + buffert_m, n1 + buffert_m)
    sx0, sy0, sx1, sy1 = _bbox_3011_till_3006(sok_3011)
    karna_box = box(*karna)

    alla: list[SkogligtNaturvarde] = []
    kallor_sha: dict[str, str] = {}
    varningar: list[str] = []

    for kat_id, kat_namn, rel_url in tjanster:
        sida_hashar: list[str] = []
        sedda_idn: set[str] = set()
        offset = 0
        for sida_idx in range(max_sidor):
            params = urllib.parse.urlencode(
                {
                    "where": "1=1",
                    "geometry": f"{sx0:.2f},{sy0:.2f},{sx1:.2f},{sy1:.2f}",
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": str(EPSG_SWEREF99_TM),
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "*",
                    "outSR": str(EPSG_WGS84),
                    "returnGeometry": "true",
                    "f": "geojson",
                    "resultOffset": str(offset),
                    "resultRecordCount": str(int(sidstorlek)),
                }
            )
            url = f"{bas_url.rstrip('/')}/{rel_url.lstrip('/')}?{params}"
            ra = _anropa_hamtare(
                hamtare,
                url,
                TILLATNA_SKS_VARDAR,
                "Skogsstyrelsen ArcGIS REST",
                timeout_s=timeout_s,
            )
            sida_hashar.append(sha256_bytes(ra))
            try:
                dok = json.loads(ra.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SkyddadNaturFel(f"Skogsstyrelsen ({kat_id}) gav ogiltig JSON: {exc}") from exc
            if not isinstance(dok, dict):
                raise SkyddadNaturFel(f"Skogsstyrelsen ({kat_id}) gav oväntat JSON-svar.")
            if "error" in dok:
                err_msg = dok["error"].get("message", "okänt fel") if isinstance(dok["error"], Mapping) else dok["error"]
                raise SkyddadNaturFel(f"Skogsstyrelsen ({kat_id}) returnerade fel: {err_msg}")
            features = dok.get("features")
            if not isinstance(features, list):
                raise SkyddadNaturFel(f"Skogsstyrelsen ({kat_id}) saknar 'features'.")

            crs_namn = ""
            if isinstance(dok.get("crs"), Mapping):
                crs_namn = str(dok["crs"].get("properties", {}).get("name", ""))
            epsg = _epsg_fran_strang(crs_namn, EPSG_WGS84)

            nya_pa_sidan = 0
            for idx, feat in enumerate(features):
                if not isinstance(feat, Mapping) or not isinstance(feat.get("geometry"), Mapping):
                    continue
                props_raw = feat.get("properties")
                props: Mapping[str, Any] = props_raw if isinstance(props_raw, Mapping) else {}
                g_raw = shape(feat["geometry"])
                if g_raw.is_empty:
                    continue
                bounds = g_raw.bounds
                eff_epsg = epsg
                if not crs_namn and (abs(bounds[0]) > 180.0 or abs(bounds[1]) > 90.0):
                    eff_epsg = EPSG_SWEREF99_TM
                g_3011 = _omprojicera_till_3011(g_raw, eff_epsg)
                area, ov_m2, ov, avst = _avstand_och_overlapp(g_3011, karna_box)
                oid = (
                    _forsta_falt(props, ("beteckn", "beteckning", "objektid", "objectid", "fid", "id"))
                    or _rensa_text(feat.get("id"))
                    or f"{kat_id}_{offset + idx}"
                )
                if oid in sedda_idn:
                    continue
                sedda_idn.add(oid)
                nya_pa_sidan += 1
                namn = (
                    _forsta_falt(props, ("objektnamn", "namn", "biotop1", "naturtyp", "beteckn"))
                    or f"{kat_namn} ({oid})"
                )
                biotop = _forsta_falt(props, ("biotop1", "biotop", "biotoptyp", "naturtyp", "sumpskogstyp"))
                beslut = _forsta_falt(props, ("beslutsdatum", "datbeslut", "invdatum", "regdatum"))
                alla.append(
                    SkogligtNaturvarde(
                        objekt_id=oid,
                        kategori=kat_id,
                        kategori_namn=kat_namn,
                        namn=namn,
                        biotoptyp=biotop,
                        beslutsdatum=beslut[:10] if beslut else None,
                        tjanst=rel_url,
                        kalla="Skogsstyrelsen Geodataportal (CC0)",
                        geometri_3011=g_3011,
                        area_m2=area,
                        overlapp_karna_m2=ov_m2,
                        overlappar_karna=ov,
                        minsta_avstand_m=avst,
                    )
                )

            exceeded_raw = dok.get("exceededTransferLimit")
            if exceeded_raw is None and isinstance(dok.get("properties"), Mapping):
                exceeded_raw = dok["properties"].get("exceededTransferLimit")
            if exceeded_raw is False:
                break
            if exceeded_raw is None and len(features) < sidstorlek:
                break
            if len(features) == 0 or nya_pa_sidan == 0:
                break
            offset += len(features)
            if sida_idx == max_sidor - 1:
                varningar.append(
                    f"Skogsstyrelsen ({kat_namn}): nådde max_sidor={max_sidor}; urvalet kan vara avklippt."
                )

        kallor_sha[kat_id] = sha256_bytes(":".join(sida_hashar).encode("utf-8"))

    alla.sort(key=lambda o: (round(o.minsta_avstand_m, 3), o.kategori, o.namn, o.objekt_id))
    return SkogligaVardenResultat(
        karna_3011=karna,
        buffert_m=float(buffert_m),
        objekt=tuple(alla),
        kallor_sha256=kallor_sha,
        varningar=tuple(varningar),
    )


def _hitta_gis_i_zip(zip_fil: Path, ut_mapp: Path) -> Path | None:
    """Packar upp ZIP säkert (skydd mot zip-slip) och returnerar första `.gpkg` eller `.shp`."""
    try:
        with zipfile.ZipFile(zip_fil, "r") as zf:
            infos = zf.infolist()
            for info in infos:
                namn = info.filename
                p = Path(namn)
                if p.is_absolute() or ".." in p.parts:
                    raise SkyddadNaturFel(f"Ogiltig sökväg i VMI-ZIP: {namn!r} (zip-slip avvisad).")
            ut_mapp.mkdir(parents=True, exist_ok=True)
            ut_rot = ut_mapp.resolve()
            for info in infos:
                if info.is_dir():
                    continue
                mal = (ut_rot / info.filename).resolve()
                if not mal.is_relative_to(ut_rot):
                    raise SkyddadNaturFel(f"Sökväg i VMI-ZIP lämnar målmappen: {info.filename!r}.")
                mal.parent.mkdir(parents=True, exist_ok=True)
                mal.write_bytes(zf.read(info.filename))
    except zipfile.BadZipFile as exc:
        raise SkyddadNaturFel(f"VMI-filen {zip_fil.name} är inte en giltig ZIP-fil: {exc}") from exc

    for ext in (".gpkg", ".shp"):
        traffar = sorted(ut_mapp.rglob(f"*{ext}"))
        if traffar:
            return traffar[0]
    return None


def hamta_vmi_lan_zip(
    lan_katalog: str,
    cache_mapp: str | Path,
    *,
    hamtare: HamtareFn | None = None,
    filnamn: str | None = None,
    bas_url: str = VMI_NEDLADDNING_BAS_URL,
    timeout_s: float = 40.0,
) -> VmiPaketPost:
    """Listar och hämtar läns-ZIP ur Naturvårdsverkets VMI-nedladdning med SHA-256-cache.

    Returnerar `VmiPaketPost` med `zip_fil`, `gis_fil` (redo för `data_inlasning.vmi.rastrera_vmi`)
    och verifierad `sha256`.
    """
    lan = str(lan_katalog).strip().strip("/")
    if not _LAN_KATALOG_RE.fullmatch(lan):
        raise SkyddadNaturFel(
            f"Ogiltig länskatalog för VMI: {lan_katalog!r} (tillåtet: bokstäver, siffror, _, -)."
        )
    _kontrollera_url(bas_url, TILLATNA_NV_VARDAR, "Naturvårdsverket VMI")
    katalog_url = f"{bas_url.rstrip('/')}/{lan}/"

    vald_url: str
    vald_namn: str
    if filnamn is not None:
        rent = Path(filnamn).name
        if rent != filnamn or not rent.lower().endswith(".zip") or ".." in filnamn:
            raise SkyddadNaturFel(f"Ogiltigt VMI-filnamn: {filnamn!r}.")
        vald_namn = rent
        vald_url = urllib.parse.urljoin(katalog_url, rent)
    else:
        index_bytes = _anropa_hamtare(
            hamtare,
            katalog_url,
            TILLATNA_NV_VARDAR,
            "Naturvårdsverket VMI",
            timeout_s=timeout_s,
        )
        html = index_bytes.decode("utf-8", errors="replace")
        lankar = _HREF_RE.findall(html)
        zip_lankar = [
            urllib.parse.urljoin(katalog_url, h)
            for h in lankar
            if h.lower().endswith(".zip") and not h.startswith("?")
        ]
        # Om katalogen pekar på undermappen `5_GIS_skikt/`, gå in ett steg.
        if not zip_lankar:
            gis_sub = next(
                (h for h in lankar if "gis" in h.lower() and h.endswith("/") and not h.startswith("..")),
                None,
            )
            if gis_sub is not None:
                sub_url = urllib.parse.urljoin(katalog_url, gis_sub)
                sub_bytes = _anropa_hamtare(
                    hamtare,
                    sub_url,
                    TILLATNA_NV_VARDAR,
                    "Naturvårdsverket VMI",
                    timeout_s=timeout_s,
                )
                sub_html = sub_bytes.decode("utf-8", errors="replace")
                zip_lankar = [
                    urllib.parse.urljoin(sub_url, h)
                    for h in _HREF_RE.findall(sub_html)
                    if h.lower().endswith(".zip") and not h.startswith("?")
                ]
        if not zip_lankar:
            raise SkyddadNaturFel(f"Hittade ingen VMI-ZIP i katalogen {katalog_url}.")
        # Deterministiskt val: föredra fil som nämner gis/skikt/vmi, annars alfabetiskt första.
        zip_lankar_unika = sorted(set(zip_lankar))
        zip_lankar_unika.sort(
            key=lambda u: (0 if any(k in u.lower() for k in ("gis", "skikt", "vmi")) else 1, u)
        )
        vald_url = zip_lankar_unika[0]
        vald_namn = Path(urllib.parse.urlsplit(vald_url).path).name
        if not vald_namn.lower().endswith(".zip"):
            raise SkyddadNaturFel(f"Ogiltig ZIP-länk i VMI-katalogen: {vald_url!r}.")

    _kontrollera_url(vald_url, TILLATNA_NV_VARDAR, "Naturvårdsverket VMI")
    if not urllib.parse.urlsplit(vald_url).path.startswith("/nedladdning/VMI/"):
        raise SkyddadNaturFel(f"VMI-länken pekar utanför /nedladdning/VMI/: {vald_url!r}.")

    cm = Path(cache_mapp)
    cm.mkdir(parents=True, exist_ok=True)
    nyckel = sha256_bytes(vald_url.encode("utf-8"))[:16]
    zip_fil = cm / f"vmi_{lan}_{nyckel}_{vald_namn}"
    meta_fil = cm / f"{zip_fil.name}.meta.json"
    uppackad = cm / f"vmi_{lan}_{nyckel}_uppackad"

    if zip_fil.exists() or meta_fil.exists():
        if not zip_fil.exists() or not meta_fil.exists():
            raise SkyddadNaturFel(f"Korrupt VMI-cache för {lan}: saknar data- eller metadatafil.")
        meta = json.loads(meta_fil.read_text(encoding="utf-8"))
        forvantad = str(meta.get("sha256", ""))
        faktisk = sha256_fil(zip_fil)
        if not forvantad or faktisk != forvantad:
            raise SkyddadNaturFel(
                f"Korrupt VMI-cachefil {zip_fil.name}: SHA-256 stämmer inte ({faktisk[:12]} != {forvantad[:12]})."
            )
        gis_fil = _hitta_gis_i_zip(zip_fil, uppackad)
        return VmiPaketPost(
            lan_katalog=lan,
            zip_fil=zip_fil,
            gis_fil=gis_fil,
            sha256=faktisk,
            url=vald_url,
            fran_cache=True,
        )

    ra = _anropa_hamtare(
        hamtare,
        vald_url,
        TILLATNA_NV_VARDAR,
        "Naturvårdsverket VMI",
        timeout_s=timeout_s,
    )
    sha = sha256_bytes(ra)
    tmp = zip_fil.with_name(zip_fil.name + ".tmp")
    tmp.write_bytes(ra)
    tmp.replace(zip_fil)
    gis_fil = _hitta_gis_i_zip(zip_fil, uppackad)
    meta_fil.write_text(
        json.dumps(
            {
                "lan_katalog": lan,
                "url": vald_url,
                "sha256": sha,
                "storlek_bytes": len(ra),
                "licens": "CC0",
                "kalla": "Naturvårdsverket VMI",
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return VmiPaketPost(
        lan_katalog=lan,
        zip_fil=zip_fil,
        gis_fil=gis_fil,
        sha256=sha,
        url=vald_url,
        fran_cache=False,
    )


__all__ = [
    "N2K_LAGER",
    "N2K_NAMN",
    "N2K_WFS_URL",
    "NVR_WFS_URL",
    "NV_SKYDDSFORMER",
    "SKOGSSTYRELSEN_BAS_URL",
    "SKOGSSTYRELSEN_TJANSTER",
    "TILLATNA_NV_VARDAR",
    "TILLATNA_SKS_VARDAR",
    "VMI_NEDLADDNING_BAS_URL",
    "SkogligaVardenResultat",
    "SkogligtNaturvarde",
    "SkyddadNaturFel",
    "SkyddadNaturResultat",
    "SkyddatOmrade",
    "VmiPaketPost",
    "hamta_skogsstyrelsen_naturvarden",
    "hamta_skyddad_natur_wfs",
    "hamta_vmi_lan_zip",
]
