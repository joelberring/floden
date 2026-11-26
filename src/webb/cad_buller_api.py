"""Webb-API-hjälpare för CAD/BIM import & export (U17) och Buller-skissanalys (U13).

Alla beräkningar och geometriomvandlingar sker deterministiskt i SWEREF 99 18 00 (EPSG:3011)
och RH 2000 (SI-enheter: `_m`, `_m2`, `dB(A)`), utan att ändra projektets rådata på disk:

1. **CAD/BIM import (`POST /api/projekt/<id>/cad/importera`)**:
   - Accepterar base64-kodad eller rå uppladdning av `.gpkg`, `.dxf`, `.ifc` och `.xml` (LandXML)
     upp till `MAX_CAD_IMPORT_BYTES` (25 MB → HTTP 413).
   - Anropar `lapp.cad_bim` (`importera_gpkg_till_k50`, `importera_dxf_till_k50`,
     `importera_ifc_byggnader`) och `lapp.landxml` (`las_landxml_tin`).
   - Returnerar K50-kompatibla GeoJSON-features i EPSG:3011, `ritlager`, `varningar`,
     `ohamtade_entiteter` och antal objekt. Saknad CRS eller ogiltigt format ger alltid HTTP 400.
2. **CAD/BIM export (`GET /api/projekt/<id>/cad/exportera`)**:
   - Exporterar projektets K1 v2-geometri (+ valfritt sparat K9/K50-scenario) till
     `gpkg`, `dxf`, `landxml`, `obj` eller `glb` via `lapp.cad_bim`.
3. **Buller-skissanalys (`POST /api/analys/buller` + `/api/kartverktyg/<id>/buller/*.png`)**:
   - Anropar K21-skisslösaren `buller.nord2000` (`buller.losare_k21.BullerNord2000`) på projektets
     K1 v2-geometri (+ valfritt K9-scenario för före/efter skärm/hus) med uttryckliga `vagsegment`.
   - Hårda invarianter: `nord2000_konform` är alltid `False`, `metodstatus` är alltid `"skiss"`,
     `rapport_sparrad` är alltid `True`.
   - Renderar takmaskade PNG-förhandsvisningar för `ekvivalent_ljudniva_dba` (`dB(A)`) cachade
     säkert per `(projekt_id, bild_id)`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import tempfile
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient as orientera_polygon
from shapely.ops import unary_union

from buller.losare_k21 import (
    FORMAT_BULLER_K21,
    NORD2000_KONFORM,
    RAPPORT_SPARRAD,
    SKISSMARKNING_K21,
    SKISSVARNING,
    BullerK21Fel,
    BullerNord2000,
)
from buller.losare_k21 import (
    METODSTATUS as BULLER_METODSTATUS,
)
from gemensamt.hash import sha256_fil
from gemensamt.koordinater import EPSG_INTERN, HOJDSYSTEM, skapa_transformator_till_3011
from karna.analyslosare import LosarFel, LosarKontext
from karna.geometri import GeometriFel
from karna.geometri.lasa import alla_byggnader
from karna.resultatcache import ResultatCache
from karna.rutscenario import (
    Ruta,
    RutScenario,
    las_bas,
    materialisera_scenario,
)
from karna.rutscenario import (
    ScenarioFel as RutScenarioFel,
)
from karna.scenario import Andring, Planscenario, ScenarioFel, skriv_scenario
from karna.utredning import ScenarioSpec
from lapp.cad_bim import (
    CRS_EPSG_3011,
    CadBimFel,
    K50Brytlinje,
    K50Byggnad,
    K50Markyta,
    K50Modell,
    exportera_3d_glb,
    exportera_3d_obj,
    exportera_k50_till_dxf,
    exportera_k50_till_gpkg,
    exportera_landxml_tin,
    importera_dxf_till_k50,
    importera_gpkg_till_k50,
    importera_ifc_byggnader,
)
from lapp.kontrakt import LappFel
from lapp.landxml import las_landxml_tin
from testsvit.k1_io import GeoTransformInfo, las_geotiff
from webb.fel import FlodenWebbFel
from webb.gyf_utci import (
    _fonster_for_mask,
    _ladda_projekt_geometri,
    _ladda_scenario_andringar,
    _tillampa_scenario_i_minnet,
    tolka_json_kropp,
)
from webb.kartverktyg import KartverktygTjanst
from webb.projekt import PROJEKT_ID, ProjektTjanst
from webb.resultatlager import (
    LagerFel,
    Mosaik,
    Utbredning,
    etikett_och_enhet,
    farg_for_roll,
    rita_bild,
    skillnad_mosaik,
    tak_mask,
    takkalla_for_roll,
    teckenforklaring,
    varde_intervall,
)

FORMAT_CAD_IMPORT_WEBB: Final[str] = "floden-cad-import/v1"
FORMAT_BULLER_WEBB: Final[str] = "floden-buller-webb/v1"
KOORDINATSYSTEM_EPSG: Final[str] = CRS_EPSG_3011
ROLL_BULLER_LAEQ: Final[str] = "ekvivalent_ljudniva_dba"

MAX_CAD_IMPORT_BYTES: Final[int] = 25 * 1024 * 1024
MAX_EXPORT_TIN_CELLER: Final[int] = 10_000

_TILLATNA_IMPORT_ANDELSER: Final[frozenset[str]] = frozenset({".gpkg", ".dxf", ".ifc", ".xml"})
_EXPORT_FORMAT_META: Final[dict[str, tuple[str, str]]] = {
    "gpkg": ("application/geopackage+sqlite3", ".gpkg"),
    "dxf": ("image/vnd.dxf; charset=utf-8", ".dxf"),
    "landxml": ("application/xml; charset=utf-8", ".xml"),
    "xml": ("application/xml; charset=utf-8", ".xml"),
    "obj": ("model/obj; charset=utf-8", ".obj"),
    "glb": ("model/gltf-binary", ".glb"),
}

_BILD_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,120}$")
_FILNAMN_SANERING_RE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_.\-]+")


class BullerLagerCache:
    """Trådsäker, projekt-nycklad LRU-cache för renderade buller-PNG:er."""

    def __init__(self, max_poster: int = 64) -> None:
        self._max_poster = max(1, int(max_poster))
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple[str, str], tuple[bytes, Utbredning]] = OrderedDict()

    def satt(self, projekt_id: str, bild_id: str, png: bytes, utbredning: Utbredning) -> None:
        nyckel = (projekt_id.strip(), bild_id)
        with self._lock:
            self._data[nyckel] = (png, utbredning)
            self._data.move_to_end(nyckel)
            while len(self._data) > self._max_poster:
                self._data.popitem(last=False)

    def hamta(self, projekt_id: str, bild_id: str) -> tuple[bytes, Utbredning] | None:
        if not _BILD_ID_RE.match(bild_id):
            raise FlodenWebbFel("Ogiltigt bullerlager-id.", status_kod=400)
        nyckel = (projekt_id.strip(), bild_id)
        with self._lock:
            post = self._data.get(nyckel)
            if post is not None:
                self._data.move_to_end(nyckel)
            return post


# ------------------------------------------------------------------ gemensamma hjälpare


def _validera_projekt_finns(
    kartverktyg: KartverktygTjanst,
    projekt_tjanst: ProjektTjanst,
    projekt_id: str,
) -> str:
    pid = projekt_id.strip()
    if not pid:
        raise FlodenWebbFel("Projekt-id saknas eller är tomt.", status_kod=400)
    if pid == PROJEKT_ID:
        # Verifiera att demokvarteret faktiskt har byggd markmodell eller kartverktygskälla.
        try:
            kartverktyg.kalla(pid)
            return pid
        except FlodenWebbFel:
            projekt_tjanst.markmodell_rutor(pid)
            return pid
    if not projekt_tjanst.ar_projekt(pid):
        raise FlodenWebbFel(f"Okänt projekt '{pid}'.", status_kod=404)
    return pid


def _tolka_valfritt_tal(raw: Any, namn: str, standard: float = 0.0) -> float:
    if raw is None or raw == "":
        return float(standard)
    if isinstance(raw, bool):
        raise FlodenWebbFel(
            f"Fältet '{namn}' måste vara ett tal, inte sant/falskt.", status_kod=400
        )
    if isinstance(raw, int | float):
        tal = float(raw)
    elif isinstance(raw, str):
        try:
            tal = float(raw.strip().replace(",", "."))
        except ValueError as exc:
            raise FlodenWebbFel(
                f"Fältet '{namn}' måste vara ett tal, fick {raw!r}.", status_kod=400
            ) from exc
    else:
        raise FlodenWebbFel(f"Fältet '{namn}' måste vara ett tal.", status_kod=400)
    if not math.isfinite(tal):
        raise FlodenWebbFel(f"Fältet '{namn}' måste vara ett ändligt tal.", status_kod=400)
    return tal


def _tolka_valfritt_epsg(raw: Any, namn: str = "kalla_epsg") -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise FlodenWebbFel(f"Fältet '{namn}' måste vara en giltig EPSG-kod.", status_kod=400)
    if isinstance(raw, int):
        kod = raw
    elif isinstance(raw, float) and math.isfinite(raw) and raw.is_integer():
        kod = int(raw)
    elif isinstance(raw, str):
        ren = raw.strip().upper().removeprefix("EPSG:")
        try:
            kod = int(ren)
        except ValueError as exc:
            raise FlodenWebbFel(
                f"Ogiltig EPSG-kod för '{namn}': {raw!r}.", status_kod=400
            ) from exc
    else:
        raise FlodenWebbFel(f"Ogiltig EPSG-kod för '{namn}'.", status_kod=400)
    if kod <= 0:
        raise FlodenWebbFel(f"Ogiltig EPSG-kod för '{namn}': {kod}.", status_kod=400)
    return kod


def _sanera_filnamn(filnamn: str) -> str:
    bas = Path(filnamn.replace("\\", "/")).name.strip()
    if not bas or bas in (".", ".."):
        raise FlodenWebbFel("Ogiltigt eller tomt filnamn.", status_kod=400)
    return bas


def _saker_nedladdningsnamn(projekt_id: str, scenario: str | None, andelse: str) -> str:
    p = _FILNAMN_SANERING_RE.sub("_", projekt_id).strip("._-") or "projekt"
    if scenario:
        s = _FILNAMN_SANERING_RE.sub("_", scenario).strip("._-")
        if s:
            return f"{p}-{s}{andelse}"
    return f"{p}{andelse}"


def _k50_till_geojson_features(modell: K50Modell) -> list[dict[str, Any]]:
    """Serialiserar alla objekt i en `K50Modell` till K50-GeoJSON-features i EPSG:3011."""
    features: list[dict[str, Any]] = []

    for b in modell.byggnader:
        poly = orientera_polygon(b.fotavtryck, sign=1.0)
        ringar = [[[float(c[0]), float(c[1])] for c in poly.exterior.coords]]
        for hal in poly.interiors:
            ringar.append([[float(c[0]), float(c[1])] for c in hal.coords])
        features.append(
            {
                "type": "Feature",
                "id": b.objekt_id,
                "geometry": {"type": "Polygon", "coordinates": ringar},
                "properties": {
                    "objekt_id": b.objekt_id,
                    "roll": "byggnad",
                    "lager_id": "hus",
                    "lager_namn": b.lager_namn,
                    "hojd_m": float(b.byggnadshojd_m),
                    "byggnadshojd_m": float(b.byggnadshojd_m),
                    "z_bas_rh2000_m": float(b.z_bas_rh2000_m),
                    "takform": b.takform,
                    "namn": b.namn,
                    "area_m2": round(float(b.area_m2), 4),
                },
            }
        )

    for y in modell.markytor:
        poly = orientera_polygon(y.geometri, sign=1.0)
        ringar = [[[float(c[0]), float(c[1])] for c in poly.exterior.coords]]
        for hal in poly.interiors:
            ringar.append([[float(c[0]), float(c[1])] for c in hal.coords])
        props_y: dict[str, Any] = {
            "objekt_id": y.objekt_id,
            "roll": y.roll,
            "lager_id": y.roll,
            "lager_namn": y.lager_namn,
            "area_m2": round(float(y.area_m2), 4),
        }
        if y.niva_rh2000_m is not None:
            props_y["niva_rh2000"] = float(y.niva_rh2000_m)
            props_y["niva_rh2000_m"] = float(y.niva_rh2000_m)
        if y.markslag is not None:
            props_y["markslag"] = int(y.markslag)
        features.append(
            {
                "type": "Feature",
                "id": y.objekt_id,
                "geometry": {"type": "Polygon", "coordinates": ringar},
                "properties": props_y,
            }
        )

    for br in modell.brytlinjer:
        koord = [
            [float(c[0]), float(c[1]), float(c[2])] if len(c) >= 3 else [float(c[0]), float(c[1])]
            for c in br.geometri.coords
        ]
        props_br: dict[str, Any] = {
            "objekt_id": br.objekt_id,
            "roll": "brytlinje",
            "lager_id": "mur-och-kant",
            "lager_namn": br.lager_namn,
            "typ": br.typ,
            "langd_m": round(float(br.langd_m), 4),
            "har_z": bool(br.har_z),
        }
        if br.hojd_m is not None:
            props_br["hojd_m"] = float(br.hojd_m)
        features.append(
            {
                "type": "Feature",
                "id": br.objekt_id,
                "geometry": {"type": "LineString", "coordinates": koord},
                "properties": props_br,
            }
        )

    for t in modell.trad:
        pt_koord = (
            [float(t.position.x), float(t.position.y), float(t.position.z)]
            if t.position.has_z
            else [float(t.position.x), float(t.position.y)]
        )
        props_t: dict[str, Any] = {
            "objekt_id": t.objekt_id,
            "roll": "trad",
            "lager_namn": t.lager_namn,
        }
        if t.z_mark_rh2000_m is not None:
            props_t["z_mark_rh2000_m"] = float(t.z_mark_rh2000_m)
        if t.krondiameter_m is not None:
            props_t["krondiameter_m"] = float(t.krondiameter_m)
        if t.tradhojd_m is not None:
            props_t["tradhojd_m"] = float(t.tradhojd_m)
        if t.tradslag is not None:
            props_t["tradslag"] = t.tradslag
        features.append(
            {
                "type": "Feature",
                "id": t.objekt_id,
                "geometry": {"type": "Point", "coordinates": pt_koord},
                "properties": props_t,
            }
        )

    return features


def _lokalt_xml_namn(tagg: str) -> str:
    return tagg.rsplit("}", 1)[1] if "}" in tagg else tagg


def _importera_landxml_till_k50(
    fil: Path,
    *,
    kalla_epsg: int | None,
    koordinat_ordning: str,
) -> tuple[K50Modell, dict[str, int], dict[str, Any]]:
    """Läser LandXML 1.2 (`.xml`) till `K50Modell` i EPSG:3011 med strikt CRS-kontroll."""
    try:
        xml_text = fil.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        xml_text = fil.read_text(encoding="latin-1")

    try:
        rot = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise CadBimFel(f"Ogiltig LandXML-syntax: {exc}") from exc

    if _lokalt_xml_namn(rot.tag) != "LandXML":
        raise CadBimFel(
            f"XML-filen saknar <LandXML>-rotelement (fick <{_lokalt_xml_namn(rot.tag)}>)."
        )

    # Kontrollera explicit CRS i <CoordinateSystem epsgCode="..."/> eller kalla_epsg.
    xml_epsg: int | None = None
    for el in rot:
        if _lokalt_xml_namn(el.tag) == "CoordinateSystem":
            ra_kod = el.attrib.get("epsgCode")
            if ra_kod:
                xml_epsg = _tolka_valfritt_epsg(ra_kod, "CoordinateSystem.epsgCode")
            break
    eff_epsg = xml_epsg or kalla_epsg
    if eff_epsg is None:
        raise CadBimFel(
            "LandXML-filen saknar <CoordinateSystem epsgCode='...'/> och inget 'kalla_epsg' "
            "angavs — Floden gissar aldrig koordinatsystem."
        )

    try:
        yta = las_landxml_tin(fil, koordinat_ordning=koordinat_ordning)
    except LappFel as exc:
        raise CadBimFel(str(exc)) from exc

    transf = skapa_transformator_till_3011(eff_epsg)

    def _p_3011(e_m: float, n_m: float, z_m: float) -> tuple[float, float, float]:
        if eff_epsg != EPSG_INTERN:
            te, tn = transf(e_m, n_m)
            return float(te), float(tn), float(z_m)
        return float(e_m), float(n_m), float(z_m)

    tri_polys: list[Polygon] = []
    z_alla: list[float] = []
    for id0, id1, id2 in yta.trianglar:
        p0 = _p_3011(*yta.punkter[id0])
        p1 = _p_3011(*yta.punkter[id1])
        p2 = _p_3011(*yta.punkter[id2])
        poly = Polygon([(p0[0], p0[1]), (p1[0], p1[1]), (p2[0], p2[1])])
        if not poly.is_empty and float(poly.area) > 1e-9:
            tri_polys.append(poly)
            z_alla.extend((p0[2], p1[2], p2[2]))

    markytor: list[K50Markyta] = []
    varningar: list[str] = []
    if tri_polys:
        samman = unary_union(tri_polys)
        z_medel = float(sum(z_alla) / len(z_alla))
        delar = [
            g for g in shapely.get_parts(samman) if isinstance(g, Polygon) and float(g.area) > 0.0
        ]
        for idx, poly in enumerate(delar, start=1):
            oid = yta.namn if len(delar) == 1 else f"{yta.namn}_{idx}"
            markytor.append(
                K50Markyta(
                    objekt_id=oid,
                    geometri=orientera_polygon(poly, sign=1.0),
                    niva_rh2000_m=round(z_medel, 4),
                    roll="mark",
                    lager_namn="landxml_tin",
                )
            )

    # Läs även valfria 3D-brytlinjer ur <Breaklines>/<Breakline>/<PntList3D>.
    brytlinjer: list[K50Brytlinje] = []
    ordning = koordinat_ordning.strip().upper()
    for br_el in rot.iter():
        if _lokalt_xml_namn(br_el.tag) != "Breakline":
            continue
        br_namn = str(br_el.attrib.get("name") or f"landxml_brytlinje_{len(brytlinjer) + 1}")
        for barn in br_el:
            if _lokalt_xml_namn(barn.tag) != "PntList3D":
                continue
            tokens = (barn.text or "").split()
            if len(tokens) < 6 or len(tokens) % 3 != 0:
                varningar.append(
                    f"LandXML-brytlinje '{br_namn}' hade ogiltig PntList3D och hoppades."
                )
                continue
            pts: list[tuple[float, float, float]] = []
            for i in range(0, len(tokens), 3):
                v0, v1, v2 = float(tokens[i]), float(tokens[i + 1]), float(tokens[i + 2])
                e_m, n_m, z_m = (v1, v0, v2) if ordning == "NEZ" else (v0, v1, v2)
                pts.append(_p_3011(e_m, n_m, z_m))
            if len(pts) >= 2:
                brytlinjer.append(
                    K50Brytlinje(
                        objekt_id=br_namn,
                        geometri=LineString(pts),
                        typ="kantsten",
                        lager_namn="landxml_brytlinjer",
                    )
                )

    modell = K50Modell(
        markytor=tuple(markytor),
        brytlinjer=tuple(brytlinjer),
        epsg=EPSG_INTERN,
        hojdsystem=HOJDSYSTEM,
        kalla_crs=f"EPSG:{eff_epsg}",
        varningar=tuple(varningar),
    )
    extra_meta: dict[str, Any] = {
        "yta_namn": yta.namn,
        "antal_tin_punkter": int(yta.antal_punkter),
        "antal_tin_trianglar": int(yta.antal_trianglar),
        "koordinat_ordning": yta.koordinat_ordning,
    }
    return modell, {}, extra_meta


# ------------------------------------------------------------------ 1. CAD/BIM Import


def _tolka_cad_import_begaran(
    kropp_bytes: bytes,
    query: Mapping[str, Sequence[str]],
) -> tuple[str, bytes, dict[str, Any]]:
    """Tolkar antingen JSON (base64/text) eller rå binär/text-uppladdning med `?filnamn=...`."""
    if len(kropp_bytes) > MAX_CAD_IMPORT_BYTES * 2:
        # Skydd även för base64-kodad kropp (~1.33x overhead) innan avkodning.
        raise FlodenWebbFel(
            f"Uppladdningen överstiger maxgränsen ({MAX_CAD_IMPORT_BYTES} byte).",
            status_kod=413,
        )

    query_filnamn = (query.get("filnamn") or [""])[0].strip()

    # Försök tolka som JSON om inget query-filnamn gavs eller om kroppen är ett JSON-kuvert.
    ser_ut_som_json = kropp_bytes.lstrip().startswith(b"{")
    if not query_filnamn or ser_ut_som_json:
        try:
            payload = tolka_json_kropp(kropp_bytes)
        except FlodenWebbFel:
            if not query_filnamn:
                raise
            payload = None
        if payload is not None and (
            "filnamn" in payload
            or "innehall_base64" in payload
            or "data_base64" in payload
            or "fil_base64" in payload
            or "innehall_text" in payload
            or "text" in payload
        ):
            filnamn_ra = payload.get("filnamn") or query_filnamn
            if not isinstance(filnamn_ra, str) or not filnamn_ra.strip():
                raise FlodenWebbFel(
                    "Obligatoriskt fält 'filnamn' saknas "
                    "(t.ex. 'plan.dxf', 'modell.ifc' eller 'lager.gpkg').",
                    status_kod=400,
                )
            b64_ra = (
                payload.get("innehall_base64")
                if payload.get("innehall_base64") is not None
                else payload.get("data_base64")
                if payload.get("data_base64") is not None
                else payload.get("fil_base64")
            )
            text_ra = (
                payload.get("innehall_text")
                if payload.get("innehall_text") is not None
                else payload.get("text")
            )
            if b64_ra is not None:
                if not isinstance(b64_ra, str) or not b64_ra.strip():
                    raise FlodenWebbFel(
                        "Fältet 'innehall_base64' måste vara en icke-tom base64-sträng.",
                        status_kod=400,
                    )
                if len(b64_ra) > ((MAX_CAD_IMPORT_BYTES * 4) // 3 + 16):
                    raise FlodenWebbFel(
                        f"Filinnehållet överstiger maxgränsen ({MAX_CAD_IMPORT_BYTES} byte).",
                        status_kod=413,
                    )
                try:
                    fil_bytes = base64.b64decode(b64_ra.strip(), validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise FlodenWebbFel(
                        f"Ogiltig base64-kodning i 'innehall_base64': {exc}", status_kod=400
                    ) from exc
            elif text_ra is not None:
                if not isinstance(text_ra, str) or not text_ra:
                    raise FlodenWebbFel(
                        "Fältet 'innehall_text' måste vara en icke-tom textsträng.",
                        status_kod=400,
                    )
                fil_bytes = text_ra.encode("utf-8")
            else:
                raise FlodenWebbFel(
                    "Filinnehåll saknas: ange 'innehall_base64' eller 'innehall_text' "
                    "(eller skicka rå fil med ?filnamn=...).",
                    status_kod=400,
                )
            if len(fil_bytes) == 0:
                raise FlodenWebbFel("Uppladdad fil är tom (0 byte).", status_kod=400)
            if len(fil_bytes) > MAX_CAD_IMPORT_BYTES:
                raise FlodenWebbFel(
                    f"Filen ({len(fil_bytes)} byte) överstiger maxgränsen "
                    f"({MAX_CAD_IMPORT_BYTES} byte).",
                    status_kod=413,
                )
            return filnamn_ra, fil_bytes, payload

    # Rå binär/text-uppladdning med `?filnamn=...`
    if not query_filnamn:
        raise FlodenWebbFel(
            "Obligatoriskt fält 'filnamn' saknas (skicka JSON med 'filnamn' + 'innehall_base64' "
            "eller rå uppladdning med ?filnamn=...).",
            status_kod=400,
        )
    if len(kropp_bytes) == 0:
        raise FlodenWebbFel("Uppladdad fil är tom (0 byte).", status_kod=400)
    if len(kropp_bytes) > MAX_CAD_IMPORT_BYTES:
        raise FlodenWebbFel(
            f"Filen ({len(kropp_bytes)} byte) överstiger maxgränsen ({MAX_CAD_IMPORT_BYTES} byte).",
            status_kod=413,
        )
    param: dict[str, Any] = {}
    for nyckel in (
        "ursprung_e_m",
        "ursprung_n_m",
        "ursprung_z_m",
        "kalla_epsg",
        "koordinat_ordning",
    ):
        vals = query.get(nyckel)
        if vals and vals[0] != "":
            param[nyckel] = vals[0]
    return query_filnamn, kropp_bytes, param


def importera_cad_fil(
    kartverktyg: KartverktygTjanst,
    projekt_tjanst: ProjektTjanst,
    projekt_id: str,
    kropp_bytes: bytes,
    query: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """`POST /api/projekt/<id>/cad/importera`: läser GPKG/DXF/IFC/LandXML till K50 i EPSG:3011."""
    pid = _validera_projekt_finns(kartverktyg, projekt_tjanst, projekt_id)
    ra_filnamn, fil_bytes, param = _tolka_cad_import_begaran(kropp_bytes, query)
    ren_filnamn = _sanera_filnamn(ra_filnamn)
    andelse = Path(ren_filnamn).suffix.lower()
    if andelse not in _TILLATNA_IMPORT_ANDELSER:
        tillatna = ", ".join(sorted(_TILLATNA_IMPORT_ANDELSER))
        raise FlodenWebbFel(
            f"Filformatet '{andelse or '(saknas)'}' stöds inte för CAD/BIM-import. "
            f"Tillåtna format: {tillatna}.",
            status_kod=400,
        )

    ursprung_e_m = _tolka_valfritt_tal(param.get("ursprung_e_m"), "ursprung_e_m", 0.0)
    ursprung_n_m = _tolka_valfritt_tal(param.get("ursprung_n_m"), "ursprung_n_m", 0.0)
    ursprung_z_m = _tolka_valfritt_tal(param.get("ursprung_z_m"), "ursprung_z_m", 0.0)
    kalla_epsg_opt = _tolka_valfritt_epsg(param.get("kalla_epsg"), "kalla_epsg")
    koordinat_ordning = str(param.get("koordinat_ordning") or "NEZ").strip().upper()

    lagermappning_ra = param.get("lagermappning")
    lagermappning: dict[str, str] | None = None
    if lagermappning_ra is not None:
        if not isinstance(lagermappning_ra, Mapping):
            raise FlodenWebbFel("Fältet 'lagermappning' måste vara ett objekt.", status_kod=400)
        lagermappning = {str(k): str(v) for k, v in lagermappning_ra.items()}

    filtyp = andelse.lstrip(".")
    ohamtade_entiteter: dict[str, int] = {}
    extra_meta: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="floden-cad-in-") as tmp_str:
        tmp_fil = Path(tmp_str) / f"indata{andelse}"
        tmp_fil.write_bytes(fil_bytes)

        try:
            if andelse == ".gpkg":
                modell = importera_gpkg_till_k50(tmp_fil, lager_roller=lagermappning)
                varningar = list(modell.varningar)
            elif andelse == ".dxf":
                dxf_epsg = kalla_epsg_opt if kalla_epsg_opt is not None else EPSG_INTERN
                dxf_res = importera_dxf_till_k50(
                    tmp_fil,
                    kalla_epsg=dxf_epsg,
                    lagermappning=lagermappning,
                )
                modell = dxf_res.modell
                ohamtade_entiteter = dict(dxf_res.ohamtade_entiteter)
                varningar = list(dxf_res.varningar)
                extra_meta = {
                    "lagernamn": list(dxf_res.lagernamn),
                    "antal_entiteter_lasta": int(dxf_res.antal_entiteter_lasta),
                    "antal_3dfaces": len(dxf_res.faces_3d),
                    "enhetsskalfaktor_m": float(dxf_res.enhetsskalfaktor_m),
                }
            elif andelse == ".ifc":
                ifc_epsg = kalla_epsg_opt if kalla_epsg_opt is not None else EPSG_INTERN
                ifc_res = importera_ifc_byggnader(
                    tmp_fil,
                    ursprung_e_m=ursprung_e_m,
                    ursprung_n_m=ursprung_n_m,
                    ursprung_z_m=ursprung_z_m,
                    kalla_epsg=ifc_epsg,
                )
                modell = ifc_res.modell
                ohamtade_entiteter = dict(ifc_res.ostodda_representationer)
                varningar = list(ifc_res.varningar)
                extra_meta = {
                    "langdenhet": ifc_res.langdenhet,
                    "langd_skala_till_m": float(ifc_res.langd_skala_till_m),
                    "ursprung_e_m": ursprung_e_m,
                    "ursprung_n_m": ursprung_n_m,
                    "ursprung_z_m": ursprung_z_m,
                }
            else:
                modell, ohamtade_entiteter, extra_meta = _importera_landxml_till_k50(
                    tmp_fil,
                    kalla_epsg=kalla_epsg_opt,
                    koordinat_ordning=koordinat_ordning,
                )
                varningar = list(modell.varningar)
        except (CadBimFel, LappFel, ValueError, OSError) as exc:
            raise FlodenWebbFel(
                f"CAD/BIM-import av '{ren_filnamn}' misslyckades: {exc}",
                status_kod=400,
            ) from exc

    features = _k50_till_geojson_features(modell)
    return {
        "format": FORMAT_CAD_IMPORT_WEBB,
        "projekt_id": pid,
        "filnamn": ren_filnamn,
        "filtyp": filtyp,
        "koordinatsystem": KOORDINATSYSTEM_EPSG,
        "hojdsystem": HOJDSYSTEM,
        "kalla_crs": modell.kalla_crs or KOORDINATSYSTEM_EPSG,
        "antal_objekt": int(modell.antal_objekt),
        "antal_per_typ": {
            "byggnader": len(modell.byggnader),
            "markytor": len(modell.markytor),
            "brytlinjer": len(modell.brytlinjer),
            "trad": len(modell.trad),
        },
        "geojson": {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3011"}},
            "features": features,
        },
        "features": features,
        "ritlager": modell.till_k50_ritlager_dict(kalla=f"fil:{filtyp}"),
        "varningar": list(dict.fromkeys(varningar)),
        "ohamtade_entiteter": ohamtade_entiteter,
        "metadata": extra_meta,
    }


# ------------------------------------------------------------------ 2. CAD/BIM Export


def _las_projekt_brytlinjer(
    kartverktyg: KartverktygTjanst,
    projekt_id: str,
    andringar: Sequence[Mapping[str, Any]],
) -> list[K50Brytlinje]:
    """Samlar befintliga K1-brytlinjer och eventuella K9-brytlinjer från scenario i EPSG:3011."""
    ut: list[K50Brytlinje] = []
    try:
        kalla = kartverktyg.kalla(projekt_id)
        mall = (
            kalla.mallar.get("vatten")
            or kalla.mallar.get("dagsljus")
            or next(iter(kalla.mallar.values()), None)
        )
        rutor = tuple(r.mapp for r in mall.rutor) if mall is not None else ()
    except FlodenWebbFel:
        rutor = ()

    from buller.losare_k21 import _avkoda_gp_linestring
    from testsvit.k1_io import K1FormatFel, las_gpkg_tabell

    sedda: set[tuple[str, tuple[tuple[float, float], ...]]] = set()
    for mapp in rutor:
        fil = mapp / "brytlinjer.gpkg"
        if not fil.is_file():
            continue
        try:
            srs_id, rader = las_gpkg_tabell(fil, "brytlinjer")
        except K1FormatFel:
            continue
        if srs_id != EPSG_INTERN:
            continue
        for rad in rader:
            blob = rad.get("geom")
            if not isinstance(blob, bytes | bytearray):
                continue
            try:
                xy, z_vals = _avkoda_gp_linestring(blob)
            except BullerK21Fel:
                continue
            typ = str(rad.get("typ", "kantsten")).strip() or "kantsten"
            nyckel = (typ, xy)
            if nyckel in sedda:
                continue
            sedda.add(nyckel)
            hojd_ra = rad.get("hojd_m")
            hojd_m = (
                float(hojd_ra)
                if isinstance(hojd_ra, int | float)
                and not isinstance(hojd_ra, bool)
                and math.isfinite(float(hojd_ra))
                and float(hojd_ra) >= 0.0
                else None
            )
            geom = (
                LineString([(e, n, z) for (e, n), z in zip(xy, z_vals, strict=True)])
                if z_vals is not None
                else LineString(xy)
            )
            ut.append(
                K50Brytlinje(
                    objekt_id=f"k1_brytlinje_{len(ut) + 1}",
                    geometri=geom,
                    typ=typ,
                    hojd_m=hojd_m,
                    lager_namn="brytlinjer",
                )
            )

    for idx, a in enumerate(andringar, start=1):
        if a.get("typ") != "brytlinje_ny":
            continue
        g = a.get("geometri")
        if not isinstance(g, Mapping) or g.get("type") != "LineString":
            continue
        koord_ra = g.get("coordinates")
        if not isinstance(koord_ra, Sequence) or len(koord_ra) < 2:
            continue
        pts = [
            (float(p[0]), float(p[1]), float(p[2]))
            if len(p) >= 3
            else (float(p[0]), float(p[1]))
            for p in koord_ra
        ]
        eg_ra = a.get("egenskaper")
        eg: Mapping[str, Any] = eg_ra if isinstance(eg_ra, Mapping) else {}
        typ = str(eg.get("typ", "kantsten")).strip() or "kantsten"
        hojd_ra = eg.get("hojd_m")
        hojd_m = (
            float(hojd_ra)
            if isinstance(hojd_ra, int | float)
            and not isinstance(hojd_ra, bool)
            and math.isfinite(float(hojd_ra))
            and float(hojd_ra) >= 0.0
            else None
        )
        aid = str(a.get("andring_id") or f"scenario_brytlinje_{idx}")
        ut.append(
            K50Brytlinje(
                objekt_id=aid,
                geometri=LineString(pts),
                typ=typ,
                hojd_m=hojd_m,
                lager_namn="scenario_brytlinjer",
            )
        )
    return ut


def _markytor_fran_andringar(andringar: Sequence[Mapping[str, Any]]) -> list[K50Markyta]:
    """Extraherar mark- och ytobjekt ur K9/K50-ändringar för CAD/GPKG-export."""
    ut: list[K50Markyta] = []
    for idx, a in enumerate(andringar, start=1):
        typ = str(a.get("typ") or "")
        if typ not in ("markhojd", "markslag", "magasin", "vegetation"):
            continue
        g = a.get("geometri")
        if not isinstance(g, Mapping) or g.get("type") != "Polygon":
            continue
        koord = g.get("coordinates")
        if not isinstance(koord, Sequence) or not koord:
            continue
        ringar = [
            [(float(p[0]), float(p[1])) for p in r]
            for r in koord
            if isinstance(r, Sequence) and len(r) >= 3
        ]
        if not ringar:
            continue
        try:
            poly = Polygon(ringar[0], holes=ringar[1:])
        except (TypeError, ValueError):
            continue
        if poly.is_empty or float(poly.area) <= 0.0:
            continue
        eg_ra = a.get("egenskaper")
        eg: Mapping[str, Any] = eg_ra if isinstance(eg_ra, Mapping) else {}
        niva_ra = eg.get("niva_rh2000")
        niva = (
            float(niva_ra)
            if isinstance(niva_ra, int | float)
            and not isinstance(niva_ra, bool)
            and math.isfinite(float(niva_ra))
            else None
        )
        kod_ra = eg.get("kod", eg.get("markslag"))
        kod = (
            int(kod_ra)
            if isinstance(kod_ra, int) and not isinstance(kod_ra, bool) and 0 <= kod_ra <= 9
            else None
        )
        roll = "vegetation" if typ == "vegetation" else "vatten" if typ == "magasin" else "mark"
        ut.append(
            K50Markyta(
                objekt_id=str(a.get("andring_id") or f"scenario_yta_{idx}"),
                geometri=orientera_polygon(poly, sign=1.0),
                niva_rh2000_m=niva,
                markslag=kod,
                roll=roll,
                lager_namn=f"scenario_{roll}",
            )
        )
    return ut


def exportera_cad_fil(
    kartverktyg: KartverktygTjanst,
    projekt_tjanst: ProjektTjanst,
    projekt_id: str,
    query: Mapping[str, Sequence[str]],
) -> tuple[bytes, str, str, dict[str, str]]:
    """`GET /api/projekt/<id>/cad/exportera?format=gpkg|dxf|landxml|obj|glb&scenario=<slug>`.

    Returnerar `(data_bytes, content_type, filnamn, extra_headers)`.
    Helt skrivlös gentemot projektmappen.
    """
    pid = _validera_projekt_finns(kartverktyg, projekt_tjanst, projekt_id)
    fmt_ra = (query.get("format") or [""])[0].strip().lower()
    if fmt_ra not in _EXPORT_FORMAT_META:
        tillatna = "gpkg, dxf, landxml, obj, glb"
        raise FlodenWebbFel(
            f"Ogiltigt eller saknat exportformat '{fmt_ra}'. Tillåtna format: {tillatna}.",
            status_kod=400,
        )
    content_type, andelse = _EXPORT_FORMAT_META[fmt_ra]
    scenario_ra = (query.get("scenario") or [""])[0].strip()
    scenario: str | None = scenario_ra or None
    crs_mal = (query.get("crs") or [CRS_EPSG_3011])[0].strip() or CRS_EPSG_3011

    proj_geom = _ladda_projekt_geometri(
        kartverktyg, projekt_tjanst, pid, krav_v2_terrang=True
    )
    sc_pack = _ladda_scenario_andringar(kartverktyg, proj_geom, scenario)
    andringar: list[dict[str, Any]] = []
    terrang_m = proj_geom.terrang_m
    bygg_poster = proj_geom.byggnader

    if sc_pack is not None:
        andringar, sc_nyckel = sc_pack
        st_efter = _tillampa_scenario_i_minnet(proj_geom, andringar, sc_nyckel)
        terrang_m = st_efter.terrang_m
        bygg_poster = st_efter.byggnader

    # Konvertera K1 v2 + scenario-byggnader till K50Byggnad i EPSG:3011 / RH 2000.
    k50_byggnader: list[K50Byggnad] = []
    for b in bygg_poster:
        try:
            poly = Polygon(b.polygon, holes=b.hal if b.hal else None)
            if not poly.is_valid:
                poly = poly.buffer(0.0)
            if not isinstance(poly, Polygon) or poly.is_empty or float(poly.area) <= 0.0:
                continue
            hojd_m = max(0.0, float(b.hojd_m))
            k50_byggnader.append(
                K50Byggnad(
                    objekt_id=b.byggnad_id,
                    fotavtryck=orientera_polygon(poly, sign=1.0),
                    byggnadshojd_m=hojd_m,
                    z_bas_rh2000_m=float(b.markhojd_rh2000),
                    takform=str(b.takform or "platt"),
                    lager_namn="byggnader",
                )
            )
        except (CadBimFel, TypeError, ValueError):
            continue

    k50_markytor = _markytor_fran_andringar(andringar)
    k50_brytlinjer = _las_projekt_brytlinjer(kartverktyg, pid, andringar)

    # Klipp terrängrastret till projektfönstret och nedsampla deterministiskt vid stora rutnät.
    geo = proj_geom.geo
    r0, r1, c0, c1 = _fonster_for_mask(proj_geom.tomt_mask)
    px = float(geo.pixelstorlek_x)
    terr_win = np.where(
        proj_geom.har_data[r0:r1, c0:c1] & proj_geom.tomt_mask[r0:r1, c0:c1],
        terrang_m[r0:r1, c0:c1],
        np.float32(np.nan),
    )
    win_h, win_b = terr_win.shape
    steg = 1
    if win_h * win_b > MAX_EXPORT_TIN_CELLER:
        steg = max(2, math.ceil(math.sqrt((win_h * win_b) / MAX_EXPORT_TIN_CELLER)))
        terr_exp = terr_win[::steg, ::steg]
    else:
        terr_exp = terr_win
    if terr_exp.shape[0] < 2 or terr_exp.shape[1] < 2:
        terr_exp = terr_win
        steg = 1

    geo_exp = GeoTransformInfo(
        bredd=int(terr_exp.shape[1]),
        hojd=int(terr_exp.shape[0]),
        x_min=float(geo.x_min + c0 * px),
        y_max=float(geo.y_max - r0 * px),
        pixelstorlek_x=float(px * steg),
        pixelstorlek_y=float(px * steg),
        epsg=EPSG_INTERN,
        dtype="float32",
    )

    if not k50_byggnader and not k50_markytor and not k50_brytlinjer:
        # Garantera minst en giltig K50-yta (projektets utbredning) för GPKG/DXF-export.
        giltig_z = terr_win[np.isfinite(terr_win)]
        z_med = float(np.median(giltig_z)) if giltig_z.size else 0.0
        e0 = geo_exp.x_min
        e1 = geo_exp.x_min + geo_exp.bredd * geo_exp.pixelstorlek_x
        n1 = geo_exp.y_max
        n0 = geo_exp.y_max - geo_exp.hojd * geo_exp.pixelstorlek_y
        k50_markytor.append(
            K50Markyta(
                objekt_id="projektomrade",
                geometri=Polygon([(e0, n0), (e1, n0), (e1, n1), (e0, n1)]),
                niva_rh2000_m=round(z_med, 4),
                roll="mark",
                lager_namn="markytor",
            )
        )

    modell = K50Modell(
        byggnader=tuple(k50_byggnader),
        markytor=tuple(k50_markytor),
        brytlinjer=tuple(k50_brytlinjer),
    )

    filnamn = _saker_nedladdningsnamn(pid, scenario, andelse)
    extra_headers: dict[str, str] = {
        "Content-Disposition": f'attachment; filename="{filnamn}"',
        "X-Floden-EPSG": str(EPSG_INTERN),
        "X-Floden-Hojdsystem": HOJDSYSTEM,
        "X-Floden-Export-Format": "landxml" if fmt_ra == "xml" else fmt_ra,
        "X-Floden-Export-Nedsampling": str(steg),
    }

    with tempfile.TemporaryDirectory(prefix="floden-cad-ut-") as tmp_str:
        ut_fil = Path(tmp_str) / filnamn
        try:
            if fmt_ra == "gpkg":
                exportera_k50_till_gpkg(modell, ut_fil, crs=crs_mal)
            elif fmt_ra == "dxf":
                exportera_k50_till_dxf(modell, ut_fil)
            elif fmt_ra in ("landxml", "xml"):
                exportera_landxml_tin(
                    terr_exp,
                    geo_exp,
                    ut_fil,
                    brytlinjer=modell.brytlinjer,
                    yta_namn=f"{pid}-markmodell",
                )
            elif fmt_ra == "obj":
                exportera_3d_obj(
                    ut_fil,
                    hojd_raster_m=terr_exp,
                    transform=geo_exp,
                    byggnader=modell.byggnader,
                )
            else:
                exportera_3d_glb(
                    ut_fil,
                    hojd_raster_m=terr_exp,
                    transform=geo_exp,
                    byggnader=modell.byggnader,
                )
        except (CadBimFel, ValueError, OSError) as exc:
            raise FlodenWebbFel(
                f"CAD/BIM-export ({fmt_ra}) misslyckades: {exc}",
                status_kod=400,
            ) from exc
        data = ut_fil.read_bytes()

    return data, content_type, filnamn, extra_headers


# ------------------------------------------------------------------ 3. Buller-skissanalys (U13)


def _projekt_k1_v2_rutor(
    kartverktyg: KartverktygTjanst,
    projekt_tjanst: ProjektTjanst,
    omrade_id: str,
) -> tuple[Path, ...]:
    """Löser upp projektets skrivskyddade K1 v2-rutor för K21-bullerlösaren."""
    try:
        kalla = kartverktyg.kalla(omrade_id)
    except FlodenWebbFel as exc:
        if omrade_id == PROJEKT_ID and exc.status_kod == 404:
            rutor = projekt_tjanst.markmodell_rutor(omrade_id)
        else:
            raise
    else:
        mall = (
            kalla.mallar.get("vatten")
            or kalla.mallar.get("dagsljus")
            or next(iter(kalla.mallar.values()), None)
        )
        if mall is None:
            raise FlodenWebbFel(f"Projektet '{omrade_id}' saknar K1-bas.", status_kod=404)
        rutor = tuple(r.mapp for r in mall.rutor if (r.mapp / "mark.tif").is_file())

    if not rutor:
        raise FlodenWebbFel(
            f"Projektet '{omrade_id}' saknar K1 v2-rutor för bulleranalys.",
            status_kod=404,
        )
    for mapp in rutor:
        if not (mapp / "terrang.tif").is_file():
            raise FlodenWebbFel(
                f"K1-rutan '{mapp.name}' saknar terrang.tif (K1 v2 krävs för buller.nord2000).",
                status_kod=400,
            )
    return rutor


def _bygg_rutscenario_fran_rutor(
    rutor: Sequence[Path],
    scenario_id: str,
) -> RutScenario:
    """Bygger ett `RutScenario` direkt ur befintliga K1-rutor utan att skriva i dem."""
    ruta_obj: list[Ruta] = []
    for mapp in rutor:
        ruta_obj.extend(las_bas(mapp))
    ruta_obj.sort(key=lambda r: r.ruta_id)
    nyckel = hashlib.sha256(
        json.dumps([[r.ruta_id, r.hash] for r in ruta_obj], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return RutScenario(id=scenario_id, nyckel=nyckel, rutor=tuple(ruta_obj))


def _materialisera_efter_scenario(
    bas_rutor: Sequence[Path],
    andringar: Sequence[Mapping[str, Any]],
    tmp_rot: Path,
    scenario_namn: str,
) -> RutScenario:
    """Materialiserar K9-ändringar i en temporär katalog (helt skrivlös mot projektmappen)."""
    k9_andringar: list[Andring] = []
    for a in andringar:
        try:
            k9_andringar.append(Andring.fran_dict(a))
        except ScenarioFel as exc:
            raise FlodenWebbFel(
                f"Ogiltig K9-ändring för bulleranalys: {exc}", status_kod=400
            ) from exc

    bas_recept_hashes: list[str] = []
    for mapp in bas_rutor:
        rec = mapp / "recept.json"
        if rec.is_file():
            bas_recept_hashes.append(sha256_fil(rec))
    if not bas_recept_hashes:
        # Använd rutans katalog-hash om recept.json saknas i en syntetisk ruta.
        bas_recept_hashes = [r.hash for r in _bygg_rutscenario_fran_rutor(bas_rutor, "bas").rutor]

    k9_fil = skriv_scenario(
        Planscenario(
            scenario_id=scenario_namn,
            bas_recept=tuple(dict.fromkeys(bas_recept_hashes)),
            andringar=tuple(k9_andringar),
            beskrivning=f"Webb-buller scenario {scenario_namn}",
        ),
        tmp_rot / "scenario_k9.json",
    )

    if len(bas_rutor) == 1:
        bas_path = bas_rutor[0]
    else:
        bas_path = tmp_rot / "bas_rutor"
        bas_path.mkdir(parents=True, exist_ok=True)
        for mapp in bas_rutor:
            lank = bas_path / mapp.name
            if not lank.exists():
                lank.symlink_to(mapp)

    spec = ScenarioSpec(id=scenario_namn, bas=bas_path, k9=(k9_fil,))
    cache = ResultatCache(tmp_rot / "scenario_cache")
    try:
        return materialisera_scenario(spec, cache)
    except (RutScenarioFel, ScenarioFel, GeometriFel, ValueError) as exc:
        raise FlodenWebbFel(
            f"Scenariot '{scenario_namn}' kunde inte materialiseras för bulleranalys: {exc}",
            status_kod=400,
        ) from exc


def _tak_mask_for_bullermosaik(
    mosaik: Mosaik,
    rutor: Sequence[Path],
) -> NDArray[np.bool_]:
    """Bygger garanterad byggnadstakmask på bullerrastrets rutnät (med fallback vid grov offset)."""
    try:
        return tak_mask(mosaik, rutor, kalla=takkalla_for_roll(ROLL_BULLER_LAEQ))
    except LagerFel:
        pass

    h, b = mosaik.raster.shape
    u = mosaik.utbredning
    px = mosaik.pixelstorlek_m
    bygg_poster = tuple(alla_byggnader(list(rutor)).values())
    if not bygg_poster:
        return np.zeros((h, b), dtype=np.bool_)

    polys = []
    for post in bygg_poster:
        try:
            poly = Polygon(post.polygon, holes=post.hal if post.hal else None)
            if not poly.is_empty:
                polys.append(poly)
        except (TypeError, ValueError):
            continue
    if not polys:
        return np.zeros((h, b), dtype=np.bool_)

    e_kol = u.x_min + (np.arange(b, dtype=np.float64) + 0.5) * px
    n_rad = u.y_max - (np.arange(h, dtype=np.float64) + 0.5) * px
    ge, gn = np.meshgrid(e_kol, n_rad)
    pts = shapely.points(ge.ravel(), gn.ravel())
    trad = shapely.STRtree(polys)
    traff, _ = trad.query(pts, predicate="intersects")
    mask = np.zeros(h * b, dtype=np.bool_)
    if traff.size > 0:
        mask[traff] = True
    return mask.reshape((h, b))


def _rendera_buller_lager(
    *,
    omrade_id: str,
    typ: str,
    bild_id: str,
    raster_f32: NDArray[np.float32],
    tak_m: NDArray[np.bool_],
    utbredning: Utbredning,
    cell_m: float,
    lo: float,
    hi: float,
    lager_cache: BullerLagerCache | None,
) -> dict[str, Any]:
    skillnad = typ == "skillnad"
    farg = farg_for_roll(ROLL_BULLER_LAEQ, skillnad=skillnad, lo=lo, hi=hi)
    etikett, enhet = etikett_och_enhet(ROLL_BULLER_LAEQ)
    har_tak = bool(np.any(tak_m))
    troskel = 0.5 if skillnad else 0.0
    try:
        bild = rita_bild(
            raster_f32,
            farg,
            lo=lo,
            hi=hi,
            troskel=troskel,
            dold=tak_m if har_tak else None,
        )
    except LagerFel as exc:
        raise FlodenWebbFel(f"Kunde inte rendera bullerlager: {exc}", status_kod=500) from exc

    if lager_cache is not None:
        lager_cache.satt(omrade_id, bild_id, bild.png, utbredning)

    kodat_proj = urllib.parse.quote(omrade_id, safe="")
    kodat_id = urllib.parse.quote(bild_id, safe="")
    return {
        "id": bild_id,
        "roll": ROLL_BULLER_LAEQ,
        "typ": typ,
        "etikett": etikett,
        "enhet": enhet,
        "farg": farg,
        "skala": {"min": float(-hi if skillnad else lo), "max": float(hi)},
        "teckenforklaring": teckenforklaring(farg, lo, hi, troskel=troskel),
        "utbredning": utbredning.som_dict(),
        "pixelstorlek_m": float(cell_m),
        "bredd_px": int(bild.bredd_px),
        "hojd_px": int(bild.hojd_px),
        "tak_maskat": har_tak,
        "png": f"/api/kartverktyg/{kodat_proj}/buller/{kodat_id}.png",
    }


def _kor_en_buller_scen(
    losare: BullerNord2000,
    scen: RutScenario,
    parametrar: Mapping[str, Any],
    utmapp: Path,
) -> tuple[dict[str, Any], dict[str, Any], Mosaik, NDArray[np.bool_]]:
    utmapp.mkdir(parents=True, exist_ok=True)
    kontext = LosarKontext(
        utmapp=utmapp,
        scenario=scen,
        parametrar=dict(parametrar),
        indata={},
        beroenden={},
    )
    try:
        losare.kor(kontext)
    except (BullerK21Fel, LosarFel, ValueError) as exc:
        raise FlodenWebbFel(str(exc), status_kod=400) from exc

    sam = json.loads((utmapp / "buller_sammanfattning.json").read_text(encoding="utf-8"))
    fasad = json.loads((utmapp / "fasad_buller.geojson").read_text(encoding="utf-8"))
    arr, geo = las_geotiff(utmapp / "ekvivalent_ljudniva_dba.tif")
    mosaik = Mosaik(
        raster=np.asarray(arr, dtype=np.float32),
        utbredning=Utbredning(
            x_min=float(geo.x_min),
            y_min=float(geo.y_min),
            x_max=float(geo.x_max),
            y_max=float(geo.y_max),
        ),
        pixelstorlek_m=float(geo.pixelstorlek_x),
    )
    tak_m = _tak_mask_for_bullermosaik(mosaik, [r.mapp for r in scen.rutor])

    # Hård försvarskontroll av skiss- och rapportspärrsinvarianter.
    if (
        NORD2000_KONFORM is not False
        or sam.get("nord2000_konform") is not False
        or sam.get("metodstatus") != "skiss"
        or sam.get("rapport_sparrad") is not True
    ):
        raise FlodenWebbFel(
            "Hård invariant bruten i buller.nord2000: nord2000_konform måste vara False och "
            "rapport_sparrad True.",
            status_kod=500,
        )
    return sam, fasad, mosaik, tak_m


def _jamfor_buller_resultat(
    sam_fore: Mapping[str, Any],
    sam_efter: Mapping[str, Any],
    mosaik_fore: Mosaik,
    mosaik_efter: Mosaik,
    tak_fore: NDArray[np.bool_],
    tak_efter: NDArray[np.bool_],
) -> tuple[dict[str, Any], Mosaik | None]:
    diff_m = skillnad_mosaik(mosaik_fore, mosaik_efter)
    tak_gem = tak_fore | tak_efter
    min_delta: float | None = None
    max_delta: float | None = None
    medel_delta: float | None = None
    max_dampning: float | None = None
    antal_jamforda = 0
    if diff_m is not None:
        arr_ren = np.where(tak_gem, np.float32(np.nan), diff_m.raster)
        diff_m = Mosaik(arr_ren, diff_m.utbredning, diff_m.pixelstorlek_m)
        giltig = arr_ren[np.isfinite(arr_ren)]
        antal_jamforda = int(giltig.size)
        if giltig.size > 0:
            min_v = float(np.min(giltig))
            max_v = float(np.max(giltig))
            min_delta = round(min_v, 4)
            max_delta = round(max_v, 4)
            medel_delta = round(float(np.mean(giltig)), 4)
            max_dampning = round(max(0.0, -min_v), 4)

    def _delta(nyckel: str) -> float | None:
        a = sam_fore.get(nyckel)
        b = sam_efter.get(nyckel)
        if isinstance(a, int | float) and isinstance(b, int | float):
            return round(float(b) - float(a), 4)
        return None

    jamforelse: dict[str, Any] = {
        "delta_laeq_max_dba": _delta("laeq_max_dba"),
        "delta_laeq_medel_dba": _delta("laeq_medel_dba"),
        "delta_laeq_energimedel_dba": _delta("laeq_energimedel_dba"),
        "delta_andel_over_55_dba_pct": _delta("andel_over_55_dba_pct"),
        "delta_andel_over_60_dba_pct": _delta("andel_over_60_dba_pct"),
        "delta_fasad_laeq_max_dba": _delta("fasad_laeq_max_dba"),
        "delta_fasad_laeq_medel_dba": _delta("fasad_laeq_medel_dba"),
        "min_delta_dba": min_delta,
        "max_delta_dba": max_delta,
        "medel_delta_dba": medel_delta,
        "max_dampning_dba": max_dampning,
        "antal_jamforda_celler": antal_jamforda,
        "nord2000_konform": False,
        "metodstatus": BULLER_METODSTATUS,
        "rapport_sparrad": RAPPORT_SPARRAD,
    }
    return jamforelse, diff_m


def kor_buller_analys(
    kartverktyg: KartverktygTjanst,
    projekt_tjanst: ProjektTjanst,
    payload: Mapping[str, Any],
    *,
    lager_cache: BullerLagerCache | None = None,
) -> dict[str, Any]:
    """Deterministisk buller-skissanalys (U13) med `buller.nord2000` (`POST /api/analys/buller`)."""
    omrade_ra = payload.get("omrade_id")
    if omrade_ra is None or omrade_ra == "":
        omrade_ra = payload.get("projekt_id")
    if not isinstance(omrade_ra, str) or not omrade_ra.strip():
        raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.", status_kod=400)
    omrade_id = _validera_projekt_finns(kartverktyg, projekt_tjanst, omrade_ra.strip())

    scenario_ra = payload.get("scenario")
    scenario: str | None = None
    if scenario_ra is not None and scenario_ra != "":
        if not isinstance(scenario_ra, str) or not scenario_ra.strip():
            raise FlodenWebbFel(
                "Fältet 'scenario' måste vara ett sparat scenarios id (text).", status_kod=400
            )
        scenario = scenario_ra.strip()

    inline_ritlager = payload.get("ritlager")
    if inline_ritlager is not None and not isinstance(inline_ritlager, Mapping):
        raise FlodenWebbFel("Fältet 'ritlager' måste vara ett K50-JSON-objekt.", status_kod=400)
    inline_andringar = payload.get("andringar")
    if inline_andringar is not None and not isinstance(inline_andringar, list):
        raise FlodenWebbFel(
            "Fältet 'andringar' måste vara en lista av K9-ändringar.", status_kod=400
        )

    # Skilj ut webb-kuvertfält och skicka övriga parametrar rakt till K21-valideraren.
    webb_nycklar = {"omrade_id", "projekt_id", "scenario", "ritlager", "andringar"}
    losar_param_in = {k: v for k, v in payload.items() if k not in webb_nycklar}

    losare = BullerNord2000()
    try:
        validerade_param = losare.validera_parametrar(losar_param_in)
    except (BullerK21Fel, LosarFel, ValueError) as exc:
        raise FlodenWebbFel(str(exc), status_kod=400) from exc

    rutor = _projekt_k1_v2_rutor(kartverktyg, projekt_tjanst, omrade_id)

    sc_pack: tuple[list[dict[str, Any]], str] | None = None
    if scenario is not None or inline_ritlager is not None or inline_andringar is not None:
        proj_geom = _ladda_projekt_geometri(
            kartverktyg, projekt_tjanst, omrade_id, krav_v2_terrang=True
        )
        sc_pack = _ladda_scenario_andringar(
            kartverktyg,
            proj_geom,
            scenario,
            inline_ritlager=inline_ritlager,
            inline_andringar=inline_andringar,
        )

    with tempfile.TemporaryDirectory(prefix="floden-buller-") as tmp_str:
        tmp_rot = Path(tmp_str)
        fore_scen = _bygg_rutscenario_fran_rutor(rutor, "fore")
        sam_fore, fasad_fore, mosaik_fore, tak_fore = _kor_en_buller_scen(
            losare, fore_scen, validerade_param, tmp_rot / "fore"
        )

        sam_efter: dict[str, Any] | None = None
        fasad_efter: dict[str, Any] | None = None
        mosaik_efter: Mosaik | None = None
        tak_efter: NDArray[np.bool_] | None = None
        jamforelse: dict[str, Any] | None = None
        diff_mosaik: Mosaik | None = None
        sc_nyckel = "nulage"

        if sc_pack is not None:
            andringar, sc_nyckel = sc_pack
            efter_scen = _materialisera_efter_scenario(
                rutor,
                andringar,
                tmp_rot / "efter_mat",
                scenario or f"forslag-{sc_nyckel[:8]}",
            )
            sam_efter, fasad_efter, mosaik_efter, tak_efter = _kor_en_buller_scen(
                losare, efter_scen, validerade_param, tmp_rot / "efter"
            )
            jamforelse, diff_mosaik = _jamfor_buller_resultat(
                sam_fore, sam_efter, mosaik_fore, mosaik_efter, tak_fore, tak_efter
            )

    # Deterministisk bild-id-nyckel + gemensam färgskala för före & efter.
    signatur = {
        "omrade_id": omrade_id,
        "fore": fore_scen.nyckel[:16],
        "sc": sc_nyckel,
        "param": validerade_param,
    }
    bas_tag = hashlib.sha256(
        json.dumps(signatur, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]

    rastren_skala = [np.where(tak_fore, np.float32(np.nan), mosaik_fore.raster)]
    if mosaik_efter is not None and tak_efter is not None:
        rastren_skala.append(np.where(tak_efter, np.float32(np.nan), mosaik_efter.raster))
    lo, hi = varde_intervall(rastren_skala)
    if lo == hi and hi > 0.0:
        lo = max(0.0, lo - 5.0)
        hi = hi + 5.0

    lager: dict[str, dict[str, Any] | None] = {
        "ekvivalent_ljudniva_dba_fore": _rendera_buller_lager(
            omrade_id=omrade_id,
            typ="bas",
            bild_id=f"bl_{bas_tag}_laeq_fore",
            raster_f32=mosaik_fore.raster,
            tak_m=tak_fore,
            utbredning=mosaik_fore.utbredning,
            cell_m=mosaik_fore.pixelstorlek_m,
            lo=lo,
            hi=hi,
            lager_cache=lager_cache,
        ),
        "ekvivalent_ljudniva_dba_efter": None,
        "ekvivalent_ljudniva_dba_skillnad": None,
    }

    if mosaik_efter is not None and tak_efter is not None:
        lager["ekvivalent_ljudniva_dba_efter"] = _rendera_buller_lager(
            omrade_id=omrade_id,
            typ="scenario",
            bild_id=f"bl_{bas_tag}_laeq_efter",
            raster_f32=mosaik_efter.raster,
            tak_m=tak_efter,
            utbredning=mosaik_efter.utbredning,
            cell_m=mosaik_efter.pixelstorlek_m,
            lo=lo,
            hi=hi,
            lager_cache=lager_cache,
        )
    if diff_mosaik is not None and tak_efter is not None:
        d_lo, d_hi = varde_intervall([diff_mosaik.raster])
        lager["ekvivalent_ljudniva_dba_skillnad"] = _rendera_buller_lager(
            omrade_id=omrade_id,
            typ="skillnad",
            bild_id=f"bl_{bas_tag}_laeq_skillnad",
            raster_f32=diff_mosaik.raster,
            tak_m=tak_fore | tak_efter,
            utbredning=diff_mosaik.utbredning,
            cell_m=diff_mosaik.pixelstorlek_m,
            lo=0.0,
            hi=max(abs(d_lo), abs(d_hi), 1.0),
            lager_cache=lager_cache,
        )

    aktiv_sam = dict(sam_efter if sam_efter is not None else sam_fore)
    aktiv_sam["nord2000_konform"] = False
    aktiv_sam["metodstatus"] = BULLER_METODSTATUS
    aktiv_sam["rapport_sparrad"] = True
    aktiv_fasad = fasad_efter if fasad_efter is not None else fasad_fore
    aktiv_lager = (
        lager["ekvivalent_ljudniva_dba_efter"]
        if lager["ekvivalent_ljudniva_dba_efter"] is not None
        else lager["ekvivalent_ljudniva_dba_fore"]
    )
    varningar = list(
        dict.fromkeys(
            [
                SKISSVARNING,
                *(sam_fore.get("varningar") or ()),
                *((sam_efter or {}).get("varningar") or ()),
            ]
        )
    )

    return {
        "format": FORMAT_BULLER_WEBB,
        "k21_format": FORMAT_BULLER_K21,
        "koordinatsystem": KOORDINATSYSTEM_EPSG,
        "hojdsystem": HOJDSYSTEM,
        "omrade_id": omrade_id,
        "scenario": scenario,
        "nord2000_konform": False,
        "metodstatus": BULLER_METODSTATUS,
        "rapport_sparrad": True,
        "markning": SKISSMARKNING_K21,
        "skissvarning": SKISSVARNING,
        "sammanfattning": aktiv_sam,
        "fore": sam_fore,
        "efter": sam_efter,
        "jamforelse": jamforelse,
        "fasad_buller": aktiv_fasad,
        "fasad_buller_fore": fasad_fore,
        "fasad_buller_efter": fasad_efter,
        "lager": lager,
        "png": aktiv_lager["png"] if aktiv_lager is not None else None,
        "varningar": varningar,
    }


__all__ = [
    "FORMAT_BULLER_WEBB",
    "FORMAT_CAD_IMPORT_WEBB",
    "MAX_CAD_IMPORT_BYTES",
    "ROLL_BULLER_LAEQ",
    "BullerLagerCache",
    "exportera_cad_fil",
    "importera_cad_fil",
    "kor_buller_analys",
]
