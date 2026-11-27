"""Deterministisk inläsning av vägnät och trafikmängder (Trafikverket API, Lastkajen & kommunfiler).

Modulen tillhandahåller tre källor som alla levererar `TrafikLank` i SWEREF 99 18 00 (EPSG:3011):

1. **Trafikverket Open API v2** (`https://api.trafikinfo.trafikverket.se/v2/data.json`):
   - Strikt HTTPS + värd-allowlist (`TILLATNA_TRAFIKVERKET_VARDAR`).
   - API-nyckel tas emot som explicit parameter, skickas enbart i XML-anropets `<LOGIN>` och
     läcker aldrig till loggar, felmeddelanden, cache-nycklar eller utdata.
   - Injicerbar `hamtare` för 100 % nätverksfria och deterministiska enhetstester.
2. **Lastkajen-filinläsning (nyckelfri):** `las_lastkajen_trafik_gpkg(...)` läser GeoPackage,
   Shapefile eller GeoJSON från Trafikverkets Lastkajen (inkl. `ÅDT_total`, `ÅDT_tung` →
   `andel_tung_pct`, `matar`, `adt_kalla`) och omprojicerar vid inläsning till EPSG:3011.
3. **Kommunal trafikfil (nyckelfri):** `las_kommunal_trafik_fil(...)` läser GeoPackage,
   GeoJSON eller CSV med kommunala trafikmängder (t.ex. Göteborgs trafikmängdskatalog med
   ÅMVD, tung % och hastighet) och matchar deterministiskt mot väglänkar via normaliserat
   gatunamn + rumslig närhet (med `osaker=True` vid tvetydig matchning).

Hård regel (`AGENTS.md`):
- Hitta ALDRIG på ÅDT eller tung andel! Saknas ÅDT sätts `adt_fordon_dygn=None`,
  `andel_tung_pct=None` och `adt_kalla=None`.
- Gissa ALDRIG koordinatsystem; saknas CRS i en rumslig fil utan `kall_epsg` kastas
  `DataInlasningFel`.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import shapely
from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.geometry.base import BaseGeometry

from data_inlasning.cache import DataInlasningFel
from gemensamt.koordinater import (
    EPSG_INTERN,
    EPSG_LANTMATERIET,
    KoordinatFel,
    skapa_transformator,
    transformera,
)

TRAFIKVERKET_API_URL: Final[str] = "https://api.trafikinfo.trafikverket.se/v2/data.json"
TILLATNA_TRAFIKVERKET_VARDAR: Final[tuple[str, ...]] = ("api.trafikinfo.trafikverket.se",)
EPSG_WGS84: Final[int] = 4326

STANDARD_TIMEOUT_S: Final[float] = 25.0
STANDARD_MAX_MATCH_AVSTAND_M: Final[float] = 40.0
_TVETYDIG_AVSTANDSMARGINAL_M: Final[float] = 5.0
_MIN_LINJELANGD_M: Final[float] = 1.0e-3

PostHamtare = Callable[[str, bytes, Mapping[str, str]], bytes]
Rektangel = tuple[float, float, float, float]


@dataclass(frozen=True)
class TrafikLank:
    """En väglänk i SWEREF 99 18 00 (EPSG:3011) med NVDB-/kommunala trafikattribut.

    Hård regel (`AGENTS.md`): När `adt_fordon_dygn` är `None` tvingas även
    `andel_tung_pct=None` och `adt_kalla=None` så trafikdata aldrig antas.
    """

    lank_id: str
    koordinater_3011: tuple[tuple[float, float], ...]
    gatunamn: str | None = None
    vagnummer: str | None = None
    vaghallare: str | None = None
    hastighet_km_h: float | None = None
    korfalt: int | None = None
    adt_fordon_dygn: float | None = None
    andel_tung_pct: float | None = None
    matar: int | None = None
    adt_kalla: str | None = None
    osaker: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.lank_id, str) or not self.lank_id.strip():
            raise DataInlasningFel("lank_id får inte vara tomt.")
        if len(self.koordinater_3011) < 2:
            raise DataInlasningFel(
                f"Väglänk '{self.lank_id}' måste ha minst två koordinater i EPSG:3011."
            )
        for e, n in self.koordinater_3011:
            if not (math.isfinite(e) and math.isfinite(n)):
                raise DataInlasningFel(
                    f"Väglänk '{self.lank_id}' har icke-ändlig koordinat ({e!r}, {n!r})."
                )
        if self.hastighet_km_h is not None and (
            not math.isfinite(self.hastighet_km_h) or self.hastighet_km_h <= 0.0
        ):
            raise DataInlasningFel(
                f"Väglänk '{self.lank_id}': hastighet_km_h måste vara > 0, "
                f"fick {self.hastighet_km_h!r}."
            )
        if self.korfalt is not None and (isinstance(self.korfalt, bool) or self.korfalt < 1):
            raise DataInlasningFel(
                f"Väglänk '{self.lank_id}': korfalt måste vara >= 1, fick {self.korfalt!r}."
            )
        if self.adt_fordon_dygn is None:
            object.__setattr__(self, "andel_tung_pct", None)
            object.__setattr__(self, "adt_kalla", None)
        else:
            if not math.isfinite(self.adt_fordon_dygn) or self.adt_fordon_dygn < 0.0:
                raise DataInlasningFel(
                    f"Väglänk '{self.lank_id}': adt_fordon_dygn måste vara >= 0, "
                    f"fick {self.adt_fordon_dygn!r}."
                )
            if self.andel_tung_pct is not None and (
                not math.isfinite(self.andel_tung_pct)
                or self.andel_tung_pct < 0.0
                or self.andel_tung_pct > 100.0
            ):
                raise DataInlasningFel(
                    f"Väglänk '{self.lank_id}': andel_tung_pct måste ligga i [0, 100], "
                    f"fick {self.andel_tung_pct!r}."
                )


# ------------------------------------------------------------------ URL- & valideringshjälpare


def kontrollera_trafikverket_url(url: str) -> None:
    """Kontrollerar strikt HTTPS och värd-allowlist för Trafikverket Open API (SSRF-skydd)."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise DataInlasningFel(
            "Endast HTTPS tillåts mot Trafikverket Open API "
            f"(fick schema {parsed.scheme or '(tomt)'!r})."
        )
    if parsed.username is not None or parsed.password is not None:
        raise DataInlasningFel("Inloggningsuppgifter i URL tillåts inte mot Trafikverket API.")
    host = (parsed.hostname or "").lower()
    if host not in TILLATNA_TRAFIKVERKET_VARDAR:
        raise DataInlasningFel(
            f"Värden {host!r} är inte tillåten för Trafikverket Open API "
            f"(tillåtna: {', '.join(TILLATNA_TRAFIKVERKET_VARDAR)})."
        )


def _sanera_feltext(text: str, hemlighet: str | None) -> str:
    """Tar bort eventuell API-nyckel ur felmeddelanden innan de lyfts vidare."""
    if not hemlighet:
        return text
    return text.replace(hemlighet, "***")


def _validera_bbox_3011(bbox: Rektangel) -> Rektangel:
    if len(bbox) != 4:
        raise DataInlasningFel(
            "bbox_3011 måste innehålla exakt 4 tal (e_min, n_min, e_max, n_max)."
        )
    e0, n0, e1, n1 = (float(v) for v in bbox)
    if not all(math.isfinite(v) for v in (e0, n0, e1, n1)):
        raise DataInlasningFel("bbox_3011 får bara innehålla ändliga tal.")
    if e0 >= e1 or n0 >= n1:
        raise DataInlasningFel(f"Ogiltig bbox_3011 (min >= max): {bbox!r}.")
    return (e0, n0, e1, n1)


def _bbox_3011_till_3006(bbox_3011: Rektangel) -> Rektangel:
    e0, n0, e1, n1 = _validera_bbox_3011(bbox_3011)
    horn = [
        transformera(e, n, EPSG_INTERN, EPSG_LANTMATERIET)
        for e, n in ((e0, n0), (e0, n1), (e1, n0), (e1, n1))
    ]
    return (
        min(p[0] for p in horn),
        min(p[1] for p in horn),
        max(p[0] for p in horn),
        max(p[1] for p in horn),
    )


def _standard_post_hamtare(
    url: str,
    kropp: bytes,
    headers: Mapping[str, str],
    *,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> bytes:
    kontrollera_trafikverket_url(url)
    req_headers = dict(headers)
    req_headers.setdefault("User-Agent", "Floden/1.0 (trafikverket-nvdb-client)")
    req = urllib.request.Request(url, data=kropp, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - HTTPS + allowlist
            return bytes(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise DataInlasningFel(f"Trafikverket Open API-anrop misslyckades: {exc}") from exc


# ------------------------------------------------------------------ Normalisering & fälttolkning


_BLANK_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
_GATUNAMN_SKRAP_RE: Final[re.Pattern[str]] = re.compile(r"[^\w\såäöéü]", re.UNICODE)


def normalisera_gatunamn(namn: str | None) -> str:
    """Deterministisk normalisering av svenska gatunamn för matchning mot kommunfiler."""
    if namn is None:
        return ""
    s = unicodedata.normalize("NFKC", str(namn)).strip().lower()
    s = s.replace("-", " ")
    s = _GATUNAMN_SKRAP_RE.sub(" ", s)
    s = _BLANK_RE.sub(" ", s).strip()
    return s


def _ascii_vik_gatunamn(norm: str) -> str:
    return (
        norm.replace("å", "a")
        .replace("ä", "a")
        .replace("ö", "o")
        .replace("é", "e")
        .replace("ü", "u")
    )


def _ar_saknat(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and v.strip() in ("", "-", "–", "null", "None", "NaN", "nan"):
        return True
    # pandas.NA / numpy.nan
    try:
        if v != v:  # noqa: PLR0124 - NaN-kontroll utan extra pandas-import
            return True
    except (TypeError, ValueError):
        pass
    return False


def _ren_str(v: Any) -> str | None:
    if _ar_saknat(v):
        return None
    s = str(v).strip()
    return s if s else None


def _tolka_valfritt_flyttal(v: Any, *, falt: str, kontext: str) -> float | None:
    if _ar_saknat(v):
        return None
    if isinstance(v, bool):
        raise DataInlasningFel(f"{kontext}: '{falt}' får inte vara booleskt.")
    if isinstance(v, int | float):
        tal = float(v)
    elif isinstance(v, str):
        ren = v.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        ren = ren.rstrip("%").strip()
        if not ren:
            return None
        try:
            tal = float(ren)
        except ValueError as exc:
            raise DataInlasningFel(
                f"{kontext}: kunde inte tolka talet {v!r} i fält '{falt}'."
            ) from exc
    else:
        raise DataInlasningFel(f"{kontext}: ogiltigt värde {v!r} i fält '{falt}'.")
    if not math.isfinite(tal):
        raise DataInlasningFel(f"{kontext}: icke-ändligt tal i fält '{falt}'.")
    return tal


def _tolka_valfritt_heltal(v: Any, *, falt: str, kontext: str) -> int | None:
    tal = _tolka_valfritt_flyttal(v, falt=falt, kontext=kontext)
    if tal is None:
        return None
    if abs(tal - round(tal)) > 1.0e-6:
        raise DataInlasningFel(f"{kontext}: förväntade heltal i fält '{falt}', fick {v!r}.")
    return int(round(tal))


def _normalisera_kolumnnyckel(k: str) -> str:
    return unicodedata.normalize("NFKC", k).strip().lower()


def _hamta_falt(props_norm: Mapping[str, Any], kandidater: Sequence[str]) -> Any:
    for k in kandidater:
        kn = _normalisera_kolumnnyckel(k)
        if kn in props_norm and not _ar_saknat(props_norm[kn]):
            return props_norm[kn]
    return None


def _berakna_adt_och_tung(
    props_norm: Mapping[str, Any],
    *,
    kontext: str,
    standard_kalla: str | None,
) -> tuple[float | None, float | None, int | None, str | None, bool]:
    """Tolkar ÅDT, tung andel (%), mätår, källa och osäkerhet utan att hitta på ÅDT."""
    adt_ra = _hamta_falt(
        props_norm,
        (
            "adt_fordon_dygn",
            "ådt_total",
            "adt_total",
            "ådt_fordon",
            "adt_fordon",
            "ådt",
            "adt",
            "åmvd",
            "amvd",
            "amvd_fordon",
            "trafikmangd",
            "averagedailytraffic",
        ),
    )
    adt = _tolka_valfritt_flyttal(adt_ra, falt="adt_fordon_dygn", kontext=kontext)
    if adt is not None and adt < 0.0:
        raise DataInlasningFel(f"{kontext}: negativ ÅDT ({adt}) är ogiltig.")

    matar_ra = _hamta_falt(
        props_norm,
        (
            "matar",
            "mätår",
            "mat_ar",
            "mät_år",
            "measurementyear",
            "measurement_year",
            "ar",
            "år",
            "year",
        ),
    )
    matar = _tolka_valfritt_heltal(matar_ra, falt="matar", kontext=kontext)

    osaker_ra = _hamta_falt(props_norm, ("osaker", "osäker", "uncertain"))
    osaker = (
        bool(osaker_ra)
        if isinstance(osaker_ra, bool)
        else str(osaker_ra or "").strip().lower() in ("true", "1", "ja")
    )

    # Hård regel: saknas ÅDT sätts adt_fordon_dygn=None, andel_tung_pct=None, adt_kalla=None.
    if adt is None:
        return None, None, matar, None, osaker

    tung_pct_ra = _hamta_falt(
        props_norm,
        (
            "andel_tung_pct",
            "tung_andel_pct",
            "andel_tung",
            "tung_pct",
            "tung_procent",
            "heavyvehiclepercentage",
            "heavy_pct",
            "tungtrafik_pct",
        ),
    )
    tung_pct = _tolka_valfritt_flyttal(tung_pct_ra, falt="andel_tung_pct", kontext=kontext)
    if tung_pct is None:
        adt_tung_ra = _hamta_falt(
            props_norm,
            (
                "ådt_tung",
                "adt_tung",
                "ådt_tunga",
                "adt_tunga",
                "åmvd_tung",
                "amvd_tung",
                "tung_adt",
            ),
        )
        adt_tung = _tolka_valfritt_flyttal(adt_tung_ra, falt="adt_tung", kontext=kontext)
        if adt_tung is not None:
            if adt_tung < 0.0:
                raise DataInlasningFel(f"{kontext}: negativ ÅDT_tung ({adt_tung}) är ogiltig.")
            if adt == 0.0:
                tung_pct = 0.0 if adt_tung == 0.0 else None
                if adt_tung > 0.0:
                    osaker = True
            else:
                if adt_tung > adt:
                    osaker = True
                    tung_pct = 100.0
                else:
                    tung_pct = round(100.0 * adt_tung / adt, 2)
    else:
        if tung_pct < 0.0 or tung_pct > 100.0:
            raise DataInlasningFel(
                f"{kontext}: andel_tung_pct måste ligga i [0, 100], fick {tung_pct}."
            )
        tung_pct = round(tung_pct, 2)

    kalla_ra = _ren_str(_hamta_falt(props_norm, ("adt_kalla", "kalla", "källa", "source")))
    matmetod_ra = _ren_str(_hamta_falt(props_norm, ("matmetod", "mätmetod", "measurementmethod")))
    if kalla_ra is not None:
        adt_kalla: str | None = kalla_ra
    elif standard_kalla is not None:
        adt_kalla = f"{standard_kalla} ({matmetod_ra})" if matmetod_ra else standard_kalla
    else:
        adt_kalla = matmetod_ra

    return round(adt, 2), tung_pct, matar, adt_kalla, osaker


def _linjedelar_3011(
    geom: BaseGeometry,
    bbox_box: BaseGeometry | None,
) -> list[tuple[tuple[float, float], ...]]:
    """Extraherar giltiga LineString-koordinattupplar i EPSG:3011, valfritt klippta till bbox."""
    if geom.is_empty:
        return []
    eff = geom.intersection(bbox_box) if bbox_box is not None else geom
    if eff.is_empty:
        return []
    ut: list[tuple[tuple[float, float], ...]] = []
    for del_g in shapely.get_parts(eff):
        if isinstance(del_g, LineString) and float(del_g.length) > _MIN_LINJELANGD_M:
            pts = tuple((float(c[0]), float(c[1])) for c in del_g.coords)
            if len(pts) >= 2:
                ut.append(pts)
        elif isinstance(del_g, MultiLineString):
            for sub in del_g.geoms:
                if float(sub.length) > _MIN_LINJELANGD_M:
                    pts = tuple((float(c[0]), float(c[1])) for c in sub.coords)
                    if len(pts) >= 2:
                        ut.append(pts)
    return ut


def _transformera_geometri_till_3011(geom: BaseGeometry, kall_epsg: int) -> BaseGeometry:
    if kall_epsg == EPSG_INTERN:
        return geom
    try:
        transf = skapa_transformator(kall_epsg, EPSG_INTERN)
    except KoordinatFel as exc:
        raise DataInlasningFel(str(exc)) from exc

    def _tr_coords(coords: Sequence[Any]) -> list[tuple[float, float]]:
        return [(float(e), float(n)) for e, n in (transf(float(c[0]), float(c[1])) for c in coords)]

    if isinstance(geom, LineString):
        return LineString(_tr_coords(list(geom.coords)))
    if isinstance(geom, MultiLineString):
        return MultiLineString([LineString(_tr_coords(list(g.coords))) for g in geom.geoms])
    if isinstance(geom, Point):
        e, n = transf(float(geom.x), float(geom.y))
        return Point(float(e), float(n))
    raise DataInlasningFel(f"Ostödd geometrityp för trafikdata: {geom.geom_type!r}.")


# ------------------------------------------------------------------ 1. Trafikverket Open API


def bygg_trafikverket_xml_fraga(
    bbox_3011: Rektangel,
    *,
    api_nyckel: str,
    objekttyp: str = "RoadData",
    schemaversion: str = "1",
    limit: int = 5000,
) -> bytes:
    """Bygger säker XML-begäran mot Trafikverket Open API v2 med SWEREF 99 TM-ruta."""
    if not isinstance(api_nyckel, str) or not api_nyckel.strip():
        raise DataInlasningFel("Trafikverket API-nyckel får inte vara tom.")
    if not isinstance(objekttyp, str) or not objekttyp.strip():
        raise DataInlasningFel("objekttyp får inte vara tom.")
    e0, n0, e1, n1 = _bbox_3011_till_3006(bbox_3011)

    rot = ET.Element("REQUEST")
    ET.SubElement(rot, "LOGIN", {"authenticationkey": api_nyckel.strip()})
    query = ET.SubElement(
        rot,
        "QUERY",
        {
            "objecttype": objekttyp.strip(),
            "schemaversion": str(schemaversion).strip() or "1",
            "limit": str(max(1, int(limit))),
        },
    )
    flt = ET.SubElement(query, "FILTER")
    ET.SubElement(
        flt,
        "WITHIN",
        {
            "name": "Geometry.SWEREF99TM",
            "shape": "box",
            "value": f"{e0:.2f} {n0:.2f}, {e1:.2f} {n1:.2f}",
        },
    )
    return bytes(ET.tostring(rot, encoding="utf-8", xml_declaration=False))


def _extrakt_tv_geometri(post: Mapping[str, Any], kontext: str) -> tuple[BaseGeometry, int] | None:
    """Extraherar geometri + EPSG från ett Trafikverket API-objekt (WKT eller GeoJSON)."""
    geom_obj = post.get("Geometry") or post.get("geometry")
    if isinstance(geom_obj, Mapping):
        if isinstance(geom_obj.get("SWEREF99TM"), str) and geom_obj["SWEREF99TM"].strip():
            try:
                return shapely.from_wkt(geom_obj["SWEREF99TM"].strip()), EPSG_LANTMATERIET
            except Exception as exc:  # noqa: BLE001
                raise DataInlasningFel(
                    f"{kontext}: ogiltig SWEREF99TM WKT-geometri: {exc}"
                ) from exc
        if isinstance(geom_obj.get("WGS84"), str) and geom_obj["WGS84"].strip():
            try:
                return shapely.from_wkt(geom_obj["WGS84"].strip()), EPSG_WGS84
            except Exception as exc:  # noqa: BLE001
                raise DataInlasningFel(f"{kontext}: ogiltig WGS84 WKT-geometri: {exc}") from exc
        if geom_obj.get("type") in ("LineString", "MultiLineString"):
            try:
                return shapely.geometry.shape(geom_obj), EPSG_WGS84
            except Exception as exc:  # noqa: BLE001
                raise DataInlasningFel(f"{kontext}: ogiltig GeoJSON-geometri: {exc}") from exc
    if isinstance(geom_obj, str) and geom_obj.strip():
        try:
            return shapely.from_wkt(geom_obj.strip()), EPSG_LANTMATERIET
        except Exception as exc:  # noqa: BLE001
            raise DataInlasningFel(f"{kontext}: ogiltig WKT-geometri: {exc}") from exc
    return None


def _tolka_tv_post(
    post: Mapping[str, Any],
    idx: int,
    bbox_box: BaseGeometry | None,
) -> list[TrafikLank]:
    kontext = f"Trafikverket-post[{idx}]"
    geom_res = _extrakt_tv_geometri(post, kontext)
    if geom_res is None:
        return []
    geom_ra, kall_epsg = geom_res
    geom_3011 = _transformera_geometri_till_3011(geom_ra, kall_epsg)
    delar = _linjedelar_3011(geom_3011, bbox_box)
    if not delar:
        return []

    props_norm: dict[str, Any] = {_normalisera_kolumnnyckel(str(k)): v for k, v in post.items()}
    bas_id = (
        _ren_str(
            _hamta_falt(
                props_norm,
                ("lank_id", "linkid", "rlid", "elementid", "roadgeometryid", "id", "objectid"),
            )
        )
        or f"nvdb_{idx + 1}"
    )
    gatunamn = _ren_str(
        _hamta_falt(props_norm, ("gatunamn", "streetname", "roadname", "namn", "vagnamn"))
    )
    vagnr_ra = _hamta_falt(
        props_norm,
        ("vagnummer", "roadmainnumber", "roadnumber", "vagnr", "huvudnummer"),
    )
    vagnummer = _ren_str(vagnr_ra)
    if vagnummer is not None and vagnummer.endswith(".0"):
        vagnummer = vagnummer[:-2]
    vaghallare = _ren_str(
        _hamta_falt(
            props_norm,
            ("vaghallare", "väghållare", "roadowner", "roadauthority", "vaghallartyp", "huvudman"),
        )
    )
    hast_ra = _hamta_falt(
        props_norm,
        ("hastighet_km_h", "speedlimit", "hthast", "hastighetsgrans", "hastighet", "maxspeed"),
    )
    hast = _tolka_valfritt_flyttal(hast_ra, falt="hastighet_km_h", kontext=kontext)
    if hast is not None and hast <= 0.0:
        hast = None

    korfalt_ra = _hamta_falt(
        props_norm, ("korfalt", "körfält", "numberoflanes", "antal_korfalt", "lanes")
    )
    korfalt = _tolka_valfritt_heltal(korfalt_ra, falt="korfalt", kontext=kontext)
    if korfalt is not None and korfalt < 1:
        korfalt = None

    adt, tung_pct, matar, adt_kalla, osaker = _berakna_adt_och_tung(
        props_norm,
        kontext=kontext,
        standard_kalla="Trafikverket NVDB",
    )

    ut: list[TrafikLank] = []
    for del_nr, pts in enumerate(delar):
        lid = bas_id if len(delar) == 1 else f"{bas_id}#{del_nr}"
        ut.append(
            TrafikLank(
                lank_id=lid,
                koordinater_3011=pts,
                gatunamn=gatunamn,
                vagnummer=vagnummer,
                vaghallare=vaghallare,
                hastighet_km_h=hast,
                korfalt=korfalt,
                adt_fordon_dygn=adt,
                andel_tung_pct=tung_pct,
                matar=matar,
                adt_kalla=adt_kalla,
                osaker=osaker,
            )
        )
    return ut


def tolka_trafikverket_svar(
    svar: Mapping[str, Any],
    *,
    bbox_3011: Rektangel | None = None,
) -> list[TrafikLank]:
    """Tolkar JSON-svar från Trafikverket Open API v2 (`RESPONSE.RESULT`) till `TrafikLank`."""
    bbox_box = box(*_validera_bbox_3011(bbox_3011)) if bbox_3011 is not None else None
    resp = svar.get("RESPONSE")
    if not isinstance(resp, Mapping):
        raise DataInlasningFel("Trafikverket-svar saknar objektet 'RESPONSE'.")
    resultat = resp.get("RESULT")
    if not isinstance(resultat, list):
        raise DataInlasningFel("Trafikverket-svar saknar listan 'RESPONSE.RESULT'.")

    lankar: list[TrafikLank] = []
    idx = 0
    for block in resultat:
        if not isinstance(block, Mapping):
            continue
        if "ERROR" in block and isinstance(block["ERROR"], Mapping):
            meddelande = str(block["ERROR"].get("MESSAGE") or "Okänt API-fel")
            raise DataInlasningFel(f"Trafikverket Open API returnerade fel: {meddelande}")
        for nyckel, poster in block.items():
            if nyckel == "INFO" or not isinstance(poster, list):
                continue
            for post in poster:
                if not isinstance(post, Mapping):
                    continue
                lankar.extend(_tolka_tv_post(post, idx, bbox_box))
                idx += 1

    lankar.sort(key=lambda lk: (lk.lank_id, lk.koordinater_3011))
    return lankar


def hamta_trafikverket_vagnat(
    bbox_3011: Rektangel,
    *,
    api_nyckel: str,
    hamtare: PostHamtare | None = None,
    url: str = TRAFIKVERKET_API_URL,
    objekttyp: str = "RoadData",
    schemaversion: str = "1",
    limit: int = 5000,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> list[TrafikLank]:
    """Hämtar NVDB-väglänkar från Trafikverket Open API v2 och omprojicerar till EPSG:3011.

    Säkerhetsinvarianter:
    - Strikt HTTPS + värd-allowlist (`api.trafikinfo.trafikverket.se`).
    - `api_nyckel` förekommer aldrig i felmeddelanden, loggar eller returnerade objekt.
    - Helt nätverksfri i tester via injicerbar `hamtare(url, body_bytes, headers) -> bytes`.
    """
    kontrollera_trafikverket_url(url)
    ren_nyckel = api_nyckel.strip() if isinstance(api_nyckel, str) else ""
    if not ren_nyckel:
        raise DataInlasningFel("Trafikverket API-nyckel saknas eller är tom.")

    bbox_ok = _validera_bbox_3011(bbox_3011)
    xml_kropp = bygg_trafikverket_xml_fraga(
        bbox_ok,
        api_nyckel=ren_nyckel,
        objekttyp=objekttyp,
        schemaversion=schemaversion,
        limit=limit,
    )
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "Accept": "application/json",
    }

    try:
        if hamtare is not None:
            ra_svar = hamtare(url, xml_kropp, headers)
        else:
            ra_svar = _standard_post_hamtare(url, xml_kropp, headers, timeout_s=timeout_s)
    except DataInlasningFel as exc:
        raise DataInlasningFel(_sanera_feltext(str(exc), ren_nyckel)) from None
    except Exception as exc:  # noqa: BLE001 - skydda så nyckeln aldrig läcker i oväntade undantag
        raise DataInlasningFel(
            _sanera_feltext(f"Trafikverket Open API-anrop misslyckades: {exc}", ren_nyckel)
        ) from None

    if not isinstance(ra_svar, bytes | bytearray):
        raise DataInlasningFel("Trafikverket-hämtare måste returnera bytes.")
    try:
        tolkat = json.loads(bytes(ra_svar).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataInlasningFel(
            _sanera_feltext(f"Kunde inte tolka JSON-svar från Trafikverket API: {exc}", ren_nyckel)
        ) from None
    if not isinstance(tolkat, dict):
        raise DataInlasningFel("Trafikverket Open API-svar måste vara ett JSON-objekt.")
    try:
        return tolka_trafikverket_svar(tolkat, bbox_3011=bbox_ok)
    except DataInlasningFel as exc:
        raise DataInlasningFel(_sanera_feltext(str(exc), ren_nyckel)) from None


# ------------------------------------------------------------------ 2. Lastkajen-filinläsare


def _las_rumslig_tabell_3011(
    fil: Path,
    *,
    kall_epsg: int | None = None,
    lager: str | None = None,
) -> gpd.GeoDataFrame:
    if not fil.is_file():
        raise DataInlasningFel(f"Trafikfilen '{fil}' finns inte.")
    try:
        gdf = gpd.read_file(fil, layer=lager) if lager else gpd.read_file(fil)
    except Exception as exc:  # noqa: BLE001
        raise DataInlasningFel(f"Kunde inte läsa geodatafilen '{fil.name}': {exc}") from exc

    if gdf.crs is None:
        if kall_epsg is None:
            raise DataInlasningFel(
                f"Filen '{fil.name}' saknar koordinatsystem (CRS) och inget 'kall_epsg' angavs "
                "— Floden gissar aldrig koordinatsystem."
            )
        gdf = gdf.set_crs(epsg=int(kall_epsg))

    epsg_in = gdf.crs.to_epsg()
    if epsg_in != EPSG_INTERN:
        try:
            gdf = gdf.to_crs(epsg=EPSG_INTERN)
        except Exception as exc:  # noqa: BLE001
            raise DataInlasningFel(
                f"Kunde inte omprojicera '{fil.name}' till EPSG:{EPSG_INTERN}: {exc}"
            ) from exc
    return gdf


def _lankar_fran_gdf(
    gdf: gpd.GeoDataFrame,
    *,
    bbox_3011: Rektangel | None,
    id_prefix: str,
    standard_kalla: str | None,
    standard_vaghallare: str | None = None,
) -> list[TrafikLank]:
    bbox_box = box(*_validera_bbox_3011(bbox_3011)) if bbox_3011 is not None else None
    kolumner = [c for c in gdf.columns if c != gdf.geometry.name]
    lankar: list[TrafikLank] = []

    for idx, (_, rad) in enumerate(gdf.iterrows()):
        geom = rad.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type not in ("LineString", "MultiLineString"):
            continue
        delar = _linjedelar_3011(geom, bbox_box)
        if not delar:
            continue
        kontext = f"{id_prefix}[{idx}]"
        props_norm = {_normalisera_kolumnnyckel(str(c)): rad[c] for c in kolumner}

        bas_id = (
            _ren_str(
                _hamta_falt(
                    props_norm,
                    (
                        "lank_id",
                        "rlid",
                        "element_id",
                        "elementid",
                        "objektid",
                        "objectid",
                        "id",
                        "fid",
                    ),
                )
            )
            or f"{id_prefix}_{idx + 1}"
        )
        gatunamn = _ren_str(
            _hamta_falt(
                props_norm,
                ("gatunamn", "namn", "vagnamn", "gata", "streetname", "street_name"),
            )
        )
        vagnr_ra = _hamta_falt(
            props_norm,
            ("vagnummer", "vagnr", "vag_nr", "roadmainnumber", "road_number", "huvudnummer"),
        )
        vagnummer = _ren_str(vagnr_ra)
        if vagnummer is not None and vagnummer.endswith(".0"):
            vagnummer = vagnummer[:-2]
        vaghallare = (
            _ren_str(
                _hamta_falt(
                    props_norm,
                    (
                        "vaghallare",
                        "väghållare",
                        "vaghallartyp",
                        "väghållartyp",
                        "roadowner",
                        "huvudman",
                    ),
                )
            )
            or standard_vaghallare
        )
        hast_ra = _hamta_falt(
            props_norm,
            (
                "hastighet_km_h",
                "hthast",
                "hastighetsgrans",
                "hastighet",
                "maxspeed",
                "skyltad_hastighet",
                "speedlimit",
            ),
        )
        hast = _tolka_valfritt_flyttal(hast_ra, falt="hastighet_km_h", kontext=kontext)
        if hast is not None and hast <= 0.0:
            hast = None
        korfalt_ra = _hamta_falt(
            props_norm,
            ("korfalt", "körfält", "antal_korfalt", "antal_körfält", "lanes", "numberoflanes"),
        )
        korfalt = _tolka_valfritt_heltal(korfalt_ra, falt="korfalt", kontext=kontext)
        if korfalt is not None and korfalt < 1:
            korfalt = None

        adt, tung_pct, matar, adt_kalla, osaker = _berakna_adt_och_tung(
            props_norm,
            kontext=kontext,
            standard_kalla=standard_kalla,
        )

        for del_nr, pts in enumerate(delar):
            lid = bas_id if len(delar) == 1 else f"{bas_id}#{del_nr}"
            lankar.append(
                TrafikLank(
                    lank_id=lid,
                    koordinater_3011=pts,
                    gatunamn=gatunamn,
                    vagnummer=vagnummer,
                    vaghallare=vaghallare,
                    hastighet_km_h=hast,
                    korfalt=korfalt,
                    adt_fordon_dygn=adt,
                    andel_tung_pct=tung_pct,
                    matar=matar,
                    adt_kalla=adt_kalla,
                    osaker=osaker,
                )
            )

    lankar.sort(key=lambda lk: (lk.lank_id, lk.koordinater_3011))
    return lankar


def las_lastkajen_trafik_gpkg(
    fil: str | Path,
    *,
    bbox_3011: Rektangel | None = None,
    kall_epsg: int | None = None,
    lager: str | None = None,
    adt_kalla_standard: str = "Trafikverket Lastkajen",
) -> list[TrafikLank]:
    """Läser Trafikverkets Lastkajen-utdrag (GeoPackage/Shapefile/GeoJSON) till `TrafikLank`.

    Omprojicerar vid inläsning till SWEREF 99 18 00 (EPSG:3011). Beräknar `andel_tung_pct`
    ur `ÅDT_tung / ÅDT_total * 100` när båda finns. Saknas `ÅDT_total` sätts alltid
    `adt_fordon_dygn=None`, `andel_tung_pct=None`, `adt_kalla=None`.
    """
    sokvag = Path(fil)
    gdf = _las_rumslig_tabell_3011(sokvag, kall_epsg=kall_epsg, lager=lager)
    return _lankar_fran_gdf(
        gdf,
        bbox_3011=bbox_3011,
        id_prefix="lastkajen",
        standard_kalla=adt_kalla_standard,
    )


# ------------------------------------------------------------------ 3. Kommunal trafikfil


@dataclass(frozen=True)
class _KommunalPost:
    post_id: str
    gatunamn: str | None
    gatunamn_norm: str
    gatunamn_ascii: str
    adt_fordon_dygn: float | None
    andel_tung_pct: float | None
    hastighet_km_h: float | None
    matar: int | None
    adt_kalla: str | None
    osaker: bool
    geometri_3011: BaseGeometry | None


def _las_csv_poster(
    fil: Path,
    *,
    kall_epsg: int | None,
    adt_kalla_standard: str,
) -> list[_KommunalPost]:
    try:
        text = fil.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = fil.read_text(encoding="latin-1")

    rader_icke_tom = [r for r in text.splitlines() if r.strip()]
    if not rader_icke_tom:
        return []
    forsta = rader_icke_tom[0]
    avgr = ";" if forsta.count(";") >= forsta.count(",") else ","
    lasare = csv.DictReader(io.StringIO(text), delimiter=avgr)

    poster: list[_KommunalPost] = []
    for idx, rad in enumerate(lasare):
        if not rad:
            continue
        props_norm = {
            _normalisera_kolumnnyckel(str(k)): v
            for k, v in rad.items()
            if k is not None
        }
        kontext = f"{fil.name}[{idx + 2}]"
        gatunamn = _ren_str(
            _hamta_falt(
                props_norm,
                ("gatunamn", "gata", "namn", "vagnamn", "stracka", "sträcka", "plats"),
            )
        )
        adt, tung_pct, matar, kalla, osaker = _berakna_adt_och_tung(
            props_norm,
            kontext=kontext,
            standard_kalla=adt_kalla_standard,
        )
        hast_ra = _hamta_falt(
            props_norm, ("hastighet_km_h", "hastighet", "skyltad_hastighet", "hthast", "maxspeed")
        )
        hast = _tolka_valfritt_flyttal(hast_ra, falt="hastighet_km_h", kontext=kontext)
        if hast is not None and hast <= 0.0:
            hast = None

        geom_3011: BaseGeometry | None = None
        x_ra = _hamta_falt(
            props_norm, ("e_3011", "e_m", "ost", "easting", "x", "lon", "lng", "longitud")
        )
        y_ra = _hamta_falt(props_norm, ("n_3011", "n_m", "nord", "northing", "y", "lat", "latitud"))
        if x_ra is not None and y_ra is not None:
            xv = _tolka_valfritt_flyttal(x_ra, falt="x", kontext=kontext)
            yv = _tolka_valfritt_flyttal(y_ra, falt="y", kontext=kontext)
            if xv is not None and yv is not None:
                if "e_3011" in props_norm or "n_3011" in props_norm:
                    eff_epsg = EPSG_INTERN
                elif "lon" in props_norm or "lat" in props_norm or "longitud" in props_norm:
                    eff_epsg = EPSG_WGS84
                elif kall_epsg is not None:
                    eff_epsg = int(kall_epsg)
                else:
                    raise DataInlasningFel(
                        f"CSV-filen '{fil.name}' har koordinatkolumner men saknar känt CRS — "
                        "ange 'kall_epsg' (t.ex. 3007 eller 3011)."
                    )
                geom_3011 = _transformera_geometri_till_3011(Point(xv, yv), eff_epsg)

        norm = normalisera_gatunamn(gatunamn)
        post_id_val = (
            _ren_str(_hamta_falt(props_norm, ("id", "punkt_id", "matpunkt_id", "lank_id")))
            or f"kom_{idx + 1}"
        )
        poster.append(
            _KommunalPost(
                post_id=post_id_val,
                gatunamn=gatunamn,
                gatunamn_norm=norm,
                gatunamn_ascii=_ascii_vik_gatunamn(norm),
                adt_fordon_dygn=adt,
                andel_tung_pct=tung_pct,
                hastighet_km_h=hast,
                matar=matar,
                adt_kalla=kalla,
                osaker=osaker,
                geometri_3011=geom_3011,
            )
        )
    return poster


def _las_rumsliga_kommunposter(
    gdf: gpd.GeoDataFrame,
    *,
    adt_kalla_standard: str,
) -> list[_KommunalPost]:
    kolumner = [c for c in gdf.columns if c != gdf.geometry.name]
    poster: list[_KommunalPost] = []
    for idx, (_, rad) in enumerate(gdf.iterrows()):
        geom = rad.geometry
        if geom is None or geom.is_empty:
            continue
        props_norm = {_normalisera_kolumnnyckel(str(c)): rad[c] for c in kolumner}
        kontext = f"kommunal_post[{idx}]"
        gatunamn = _ren_str(
            _hamta_falt(
                props_norm,
                ("gatunamn", "gata", "namn", "vagnamn", "stracka", "sträcka", "plats"),
            )
        )
        adt, tung_pct, matar, kalla, osaker = _berakna_adt_och_tung(
            props_norm,
            kontext=kontext,
            standard_kalla=adt_kalla_standard,
        )
        hast_ra = _hamta_falt(
            props_norm, ("hastighet_km_h", "hastighet", "skyltad_hastighet", "hthast", "maxspeed")
        )
        hast = _tolka_valfritt_flyttal(hast_ra, falt="hastighet_km_h", kontext=kontext)
        if hast is not None and hast <= 0.0:
            hast = None
        norm = normalisera_gatunamn(gatunamn)
        post_id_val = (
            _ren_str(_hamta_falt(props_norm, ("id", "punkt_id", "matpunkt_id", "lank_id")))
            or f"kom_{idx + 1}"
        )
        poster.append(
            _KommunalPost(
                post_id=post_id_val,
                gatunamn=gatunamn,
                gatunamn_norm=norm,
                gatunamn_ascii=_ascii_vik_gatunamn(norm),
                adt_fordon_dygn=adt,
                andel_tung_pct=tung_pct,
                hastighet_km_h=hast,
                matar=matar,
                adt_kalla=kalla,
                osaker=osaker,
                geometri_3011=geom,
            )
        )
    return poster


def _namn_matchar(lank_norm: str, lank_ascii: str, post: _KommunalPost) -> bool:
    if not lank_norm or not post.gatunamn_norm:
        return False
    return lank_norm == post.gatunamn_norm or lank_ascii == post.gatunamn_ascii


def _valj_match(
    lank: TrafikLank,
    poster: Sequence[_KommunalPost],
    *,
    max_avstand_m: float,
) -> tuple[_KommunalPost | None, bool]:
    """Matchar en väglänk deterministiskt mot kommunala poster. Returnerar `(post, tvetydig)`."""
    lank_geom = LineString(lank.koordinater_3011)
    lank_norm = normalisera_gatunamn(lank.gatunamn)
    lank_ascii = _ascii_vik_gatunamn(lank_norm)

    # 1. Samla rumsliga kandidater (inom max_avstand_m) och rena namn-kandidater (utan geometri).
    rumsliga_namn: list[tuple[float, _KommunalPost]] = []
    rumsliga_utan_namnkonflikt: list[tuple[float, _KommunalPost]] = []
    tabell_namn: list[_KommunalPost] = []

    for p in poster:
        if p.adt_fordon_dygn is None:
            continue
        if p.geometri_3011 is not None:
            avst = float(lank_geom.distance(p.geometri_3011))
            if avst > max_avstand_m:
                continue
            if _namn_matchar(lank_norm, lank_ascii, p):
                rumsliga_namn.append((avst, p))
            elif not lank_norm or not p.gatunamn_norm:
                rumsliga_utan_namnkonflikt.append((avst, p))
        elif _namn_matchar(lank_norm, lank_ascii, p):
            tabell_namn.append(p)

    # Prioritet 1: Både gatunamn och rumslig närhet matchar.
    if rumsliga_namn:
        rumsliga_namn.sort(
            key=lambda x: (x[0], -(x[1].matar or -1), x[1].post_id)
        )
        basta_avst, basta = rumsliga_namn[0]
        nara_andra = [
            p
            for av, p in rumsliga_namn[1:]
            if av - basta_avst <= _TVETYDIG_AVSTANDSMARGINAL_M
            and p.adt_fordon_dygn != basta.adt_fordon_dygn
        ]
        return basta, bool(nara_andra)

    # Prioritet 2: Tabellmatchning på gatunamn (CSV utan geometri).
    if tabell_namn:
        tabell_namn.sort(key=lambda p: (-(p.matar or -1), p.post_id))
        unika_adt = {p.adt_fordon_dygn for p in tabell_namn}
        tvetydig = len(unika_adt) > 1
        return tabell_namn[0], tvetydig

    # Prioritet 3: Enbart rumslig närhet när minst ena sidan saknar gatunamn.
    if rumsliga_utan_namnkonflikt:
        rumsliga_utan_namnkonflikt.sort(
            key=lambda x: (x[0], -(x[1].matar or -1), x[1].post_id)
        )
        basta_avst, basta = rumsliga_utan_namnkonflikt[0]
        # Utan namnmatchning flaggas tvetydighet om flera olika mätvärden ligger nära.
        nara_andra = [
            p
            for av, p in rumsliga_utan_namnkonflikt[1:]
            if av - basta_avst <= _TVETYDIG_AVSTANDSMARGINAL_M
            and p.adt_fordon_dygn != basta.adt_fordon_dygn
        ]
        return basta, bool(nara_andra)

    return None, False


def las_kommunal_trafik_fil(
    fil: str | Path,
    *,
    vagnat_lankar: Sequence[TrafikLank] | None = None,
    bbox_3011: Rektangel | None = None,
    kall_epsg: int | None = None,
    lager: str | None = None,
    max_avstand_m: float = STANDARD_MAX_MATCH_AVSTAND_M,
    adt_kalla: str = "Kommunal trafikmängdskatalog",
) -> list[TrafikLank]:
    """Läser kommunal trafikfil (GeoPackage/GeoJSON/CSV) och matchar mot väglänkar i EPSG:3011.

    - Om `vagnat_lankar` är `None`: filen måste innehålla LineString/MultiLineString-geometrier
      (GeoPackage/GeoJSON), som läses direkt till `TrafikLank` i EPSG:3011.
    - Om `vagnat_lankar` anges: matchar kommunala poster deterministiskt via normaliserat
      gatunamn + rumslig närhet (inom `max_avstand_m`). Vid tvetydig matchning (flera
      motstridiga poster på samma gata eller inom 5 m från bästa träff) sätts `osaker=True`.
    - Länkar utan matchning behåller sina befintliga värden (saknas ÅDT förblir
      `adt_fordon_dygn=None`, `andel_tung_pct=None`, `adt_kalla=None`).
    """
    sokvag = Path(fil)
    if not sokvag.is_file():
        raise DataInlasningFel(f"Kommunal trafikfil '{sokvag}' finns inte.")
    if not math.isfinite(max_avstand_m) or max_avstand_m <= 0.0:
        raise DataInlasningFel(f"max_avstand_m måste vara > 0, fick {max_avstand_m!r}.")

    andelse = sokvag.suffix.lower()
    if andelse == ".csv":
        if vagnat_lankar is None:
            raise DataInlasningFel(
                "CSV-fil med kommunala trafikmängder kräver 'vagnat_lankar' för att kopplas "
                "till väglänkar i EPSG:3011."
            )
        poster = _las_csv_poster(sokvag, kall_epsg=kall_epsg, adt_kalla_standard=adt_kalla)
    elif andelse in (".gpkg", ".geojson", ".json", ".shp"):
        gdf = _las_rumslig_tabell_3011(sokvag, kall_epsg=kall_epsg, lager=lager)
        if vagnat_lankar is None:
            har_linjer = any(
                g is not None
                and not g.is_empty
                and g.geom_type in ("LineString", "MultiLineString")
                for g in gdf.geometry
            )
            if not har_linjer:
                raise DataInlasningFel(
                    f"Filen '{sokvag.name}' saknar LineString-geometrier; ange 'vagnat_lankar' "
                    "för att matcha punktdata mot ett vägnät."
                )
            return _lankar_fran_gdf(
                gdf,
                bbox_3011=bbox_3011,
                id_prefix="kommun",
                standard_kalla=adt_kalla,
                standard_vaghallare="kommunal",
            )
        poster = _las_rumsliga_kommunposter(gdf, adt_kalla_standard=adt_kalla)
    else:
        raise DataInlasningFel(
            f"Filformatet '{andelse or '(saknas)'}' stöds inte för kommunala trafikdata "
            "(tillåtna: .gpkg, .geojson, .json, .shp, .csv)."
        )

    bbox_box = box(*_validera_bbox_3011(bbox_3011)) if bbox_3011 is not None else None
    ut: list[TrafikLank] = []
    for lank in vagnat_lankar:
        delar = _linjedelar_3011(LineString(lank.koordinater_3011), bbox_box)
        if not delar:
            continue
        match, tvetydig = _valj_match(lank, poster, max_avstand_m=max_avstand_m)
        for del_nr, pts in enumerate(delar):
            lid = lank.lank_id if len(delar) == 1 else f"{lank.lank_id}#{del_nr}"
            if match is None or match.adt_fordon_dygn is None:
                ut.append(replace(lank, lank_id=lid, koordinater_3011=pts))
            else:
                ut.append(
                    TrafikLank(
                        lank_id=lid,
                        koordinater_3011=pts,
                        gatunamn=lank.gatunamn or match.gatunamn,
                        vagnummer=lank.vagnummer,
                        vaghallare=lank.vaghallare or "kommunal",
                        hastighet_km_h=(
                            lank.hastighet_km_h
                            if lank.hastighet_km_h is not None
                            else match.hastighet_km_h
                        ),
                        korfalt=lank.korfalt,
                        adt_fordon_dygn=match.adt_fordon_dygn,
                        andel_tung_pct=(
                            match.andel_tung_pct
                            if match.andel_tung_pct is not None
                            else lank.andel_tung_pct
                        ),
                        matar=match.matar if match.matar is not None else lank.matar,
                        adt_kalla=match.adt_kalla or adt_kalla,
                        osaker=bool(lank.osaker or match.osaker or tvetydig),
                    )
                )

    ut.sort(key=lambda lk: (lk.lank_id, lk.koordinater_3011))
    return ut


__all__ = [
    "EPSG_WGS84",
    "STANDARD_MAX_MATCH_AVSTAND_M",
    "TILLATNA_TRAFIKVERKET_VARDAR",
    "TRAFIKVERKET_API_URL",
    "PostHamtare",
    "TrafikLank",
    "bygg_trafikverket_xml_fraga",
    "hamta_trafikverket_vagnat",
    "kontrollera_trafikverket_url",
    "las_kommunal_trafik_fil",
    "las_lastkajen_trafik_gpkg",
    "normalisera_gatunamn",
    "tolka_trafikverket_svar",
]
