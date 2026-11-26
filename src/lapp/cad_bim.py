"""CAD/BIM-interoperabilitet för Floden (Uppdrag 17): GPKG, DXF, LandXML 1.2, IFC & 3D-mesh.

Deterministisk in- och utmatning mellan externa CAD/BIM/GIS-format och Flodens interna
koordinat- och höjdsystem **SWEREF 99 18 00 (EPSG:3011)** och **RH 2000** (SI-enheter:
`_m`, `_m2`, `_rh2000_m`):

1. **GeoPackage (`.gpkg`)**: `importera_gpkg_till_k50`, `exportera_k50_till_gpkg` — kräver
   explicit CRS (kastar `CadBimFel` / `ValueError` om CRS saknas) och reprojicerar automatiskt
   t.ex. EPSG:3006 eller EPSG:4326 till EPSG:3011 med bevarade Z-höjder i RH 2000.
2. **DXF (`.dxf` ASCII R12–2018)**: `importera_dxf_till_k50`, `exportera_k50_till_dxf` — ren
   Python-parser/skrivare för `LWPOLYLINE`, `POLYLINE`/`VERTEX` (2D & 3D), `LINE` (3D),
   `3DFACE` och `POINT` med bevarade lagernamn, Z-höjder och räknare över ej stödda entiteter.
3. **LandXML 1.2 TIN & brytlinjer**: `exportera_landxml_tin` — exporterar höjdraster (med
   affin transform i EPSG:3011) och valfria 3D-brytlinjer till standard LandXML 1.2, hoppar
   över `NaN`-celler explicit och är direkt återläsbar med `lapp.landxml.las_landxml_tin`.
4. **IFC (`.ifc` STEP-SPF ISO 10303-21)**: `importera_ifc_byggnader` — ren Python STEP-parser
   för `IFCSIUNIT` (mm/cm/m → meter), nästlad `IFCLOCALPLACEMENT` + `IFCAXIS2PLACEMENT3D`
   (3D-translation & rotation) samt `IFCEXTRUDEDAREASOLID` med `IFCRECTANGLEPROFILEDEF` och
   `IFCARBITRARYCLOSEDPROFILEDEF` / `IFCPOLYLINE` / `IFCCARTESIANPOINT`.
5. **3D-mesh (`.obj` & binär `.glb` glTF 2.0)**: `exportera_3d_obj`, `exportera_3d_glb` —
   terrängmesh + extruderade byggnadsvolymer centrerade kring `(ursprung_e_m, ursprung_n_m,
   ursprung_z_m)` för att undvika `float32`-jitter, med exakt EPSG:3011-ursprung i OBJ-huvud
   respektive glTF `asset.extras`.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import struct
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import geopandas as gpd
import numpy as np
import pyogrio
from numpy.typing import NDArray
from pyproj import CRS, Transformer
from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient as orientera_polygon
from shapely.ops import transform as shapely_transform

from gemensamt.koordinater import EPSG_INTERN, HOJDSYSTEM, skapa_transformator_till_3011

CRS_EPSG_3011 = f"EPSG:{EPSG_INTERN}"
K50_KONTRAKT = "floden-ritlager/v1"

_LANDXML_NS = "http://www.landxml.org/schema/LandXML-1.2"
_GLB_MAGIC = 0x46546C67  # b"glTF"
_GLB_VERSION = 2
_GLB_CHUNK_JSON = 0x4E4F534A  # b"JSON"
_GLB_CHUNK_BIN = 0x004E4942  # b"BIN\x00"

_GLTF_COMPONENT_FLOAT = 5126
_GLTF_COMPONENT_UINT = 5125
_GLTF_TARGET_ARRAY_BUFFER = 34962
_GLTF_TARGET_ELEMENT_ARRAY_BUFFER = 34963
_GLTF_MODE_TRIANGLES = 4

_EPS = 1e-9
_EAR_EPS = 1e-12

# DXF $INSUNITS -> meter
_DXF_INSUNITS_TILL_M: dict[int, float] = {
    0: 1.0,  # Enhetslös (antas meter i svenska CAD-leveranser)
    1: 0.0254,  # Tum
    2: 0.3048,  # Fot
    4: 0.001,  # Millimeter
    5: 0.01,  # Centimeter
    6: 1.0,  # Meter
    7: 1000.0,  # Kilometer
    14: 0.1,  # Decimeter
}

# IFC SI-prefix -> meter
_IFC_SI_PREFIX_TILL_M: dict[str | None, float] = {
    None: 1.0,
    "METRE": 1.0,
    "MILLI": 1e-3,
    "CENTI": 1e-2,
    "DECI": 1e-1,
    "DECA": 1e1,
    "HECTO": 1e2,
    "KILO": 1e3,
    "MICRO": 1e-6,
}

_IFC_PREFIX_ENHET_NAMN: dict[str | None, str] = {
    None: "m",
    "MILLI": "mm",
    "CENTI": "cm",
    "DECI": "dm",
    "DECA": "dam",
    "HECTO": "hm",
    "KILO": "km",
    "MICRO": "um",
}

_GILTIGA_MARK_ROLLER: frozenset[str] = frozenset({"mark", "hardgjort", "vegetation", "vatten"})
_GILTIGA_KOORDINATORDNINGAR: frozenset[str] = frozenset({"NEZ", "ENZ"})

# IFC-produkttyper som kan bära byggnadsvolymer i koordinations-/volymmodeller
_IFC_BYGGNAD_PRODUKTTYPER: frozenset[str] = frozenset(
    {
        "IFCBUILDING",
        "IFCBUILDINGELEMENTPROXY",
        "IFCSPACE",
        "IFCBUILDINGSTOREY",
        "IFCWALL",
        "IFCWALLSTANDARDCASE",
        "IFCSLAB",
        "IFCROOF",
        "IFCCOLUMN",
        "IFCBEAM",
        "IFCCOVERING",
        "IFCMEMBER",
        "IFCPROXY",
    }
)


class CadBimFel(ValueError):
    """Fel vid CAD/BIM-import, koordinatkontroll eller export (ärver från `ValueError`)."""


@runtime_checkable
class GeoTransformProtokoll(Protocol):
    """Strukturellt protokoll kompatibelt med `testsvit.k1_io.GeoTransformInfo`."""

    @property
    def bredd(self) -> int: ...

    @property
    def hojd(self) -> int: ...

    @property
    def x_min(self) -> float: ...

    @property
    def y_max(self) -> float: ...

    @property
    def pixelstorlek_x(self) -> float: ...

    @property
    def pixelstorlek_y(self) -> float: ...

    @property
    def epsg(self) -> int: ...


@runtime_checkable
class AffinTransformProtokoll(Protocol):
    """Strukturellt protokoll kompatibelt med `rasterio.transform.Affine`."""

    @property
    def a(self) -> float: ...

    @property
    def b(self) -> float: ...

    @property
    def c(self) -> float: ...

    @property
    def d(self) -> float: ...

    @property
    def e(self) -> float: ...

    @property
    def f(self) -> float: ...


AffinTransformIndata = (
    GeoTransformProtokoll
    | AffinTransformProtokoll
    | tuple[float, float, float, float, float, float]
)


def _andligt(varde: float, namn: str, *, minst: float | None = None) -> float:
    tal = float(varde)
    if not math.isfinite(tal):
        raise CadBimFel(f"Fältet '{namn}' måste vara ändligt, fick {varde!r}.")
    if minst is not None and tal < minst:
        raise CadBimFel(f"Fältet '{namn}' måste vara >= {minst}, fick {tal}.")
    return tal


def _validera_epsg_kod(epsg: int, namn: str = "epsg") -> int:
    if isinstance(epsg, bool) or not isinstance(epsg, int) or epsg <= 0:
        raise CadBimFel(f"Ogiltig EPSG-kod för '{namn}': {epsg!r}.")
    return epsg


def _validera_polygon(poly: Polygon, sammanhang: str) -> Polygon:
    if not isinstance(poly, Polygon) or poly.is_empty:
        raise CadBimFel(f"{sammanhang} måste vara en icke-tom Shapely-Polygon.")
    area_m2 = float(poly.area)
    if not math.isfinite(area_m2) or area_m2 <= 0.0:
        raise CadBimFel(f"{sammanhang} har noll eller ogiltig area ({area_m2} m²).")
    return poly


def _sanera_id(text: str, forval: str) -> str:
    rensad = re.sub(r"[^A-Za-z0-9_\-]+", "_", text.strip()).strip("_")
    if not rensad or not rensad[0].isalnum():
        return forval
    return rensad[:80]


# ---------------------------------------------------------------------------
# K50 CAD/BIM-datatyper (alltid i EPSG:3011 och RH 2000)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class K50Byggnad:
    """Byggnadsvolym i SWEREF 99 18 00 (EPSG:3011) och RH 2000."""

    objekt_id: str
    fotavtryck: Polygon
    byggnadshojd_m: float
    z_bas_rh2000_m: float = 0.0
    takform: str = "platt"
    lager_namn: str = "byggnader"
    namn: str | None = None
    egenskaper: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.objekt_id, str) or not self.objekt_id.strip():
            raise CadBimFel("K50Byggnad.objekt_id måste vara en icke-tom sträng.")
        _validera_polygon(self.fotavtryck, f"K50Byggnad '{self.objekt_id}'.fotavtryck")
        hojd = _andligt(self.byggnadshojd_m, "byggnadshojd_m", minst=0.0)
        z_bas = _andligt(self.z_bas_rh2000_m, "z_bas_rh2000_m")
        object.__setattr__(self, "byggnadshojd_m", hojd)
        object.__setattr__(self, "z_bas_rh2000_m", z_bas)
        object.__setattr__(self, "takform", str(self.takform or "platt").strip() or "platt")
        object.__setattr__(
            self, "lager_namn", str(self.lager_namn or "byggnader").strip() or "byggnader"
        )

    @property
    def area_m2(self) -> float:
        """Horisontell fotavtrycksarea i m² (EPSG:3011)."""
        return float(self.fotavtryck.area)

    @property
    def z_tak_rh2000_m(self) -> float:
        """Takfot-/takhöjd i m RH 2000 (`z_bas_rh2000_m + byggnadshojd_m`)."""
        return self.z_bas_rh2000_m + self.byggnadshojd_m


@dataclass(frozen=True)
class K50Markyta:
    """Mark-, hårdgjord-, vegetations- eller vattenyta i EPSG:3011 och RH 2000."""

    objekt_id: str
    geometri: Polygon
    niva_rh2000_m: float | None = None
    markslag: int | None = None
    roll: str = "mark"
    lager_namn: str = "markytor"
    egenskaper: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.objekt_id, str) or not self.objekt_id.strip():
            raise CadBimFel("K50Markyta.objekt_id måste vara en icke-tom sträng.")
        _validera_polygon(self.geometri, f"K50Markyta '{self.objekt_id}'.geometri")
        if self.niva_rh2000_m is not None:
            object.__setattr__(
                self, "niva_rh2000_m", _andligt(self.niva_rh2000_m, "niva_rh2000_m")
            )
        if self.markslag is not None:
            kod = int(self.markslag)
            if kod < 0 or kod > 9:
                raise CadBimFel(f"Ogiltigt markslag '{self.markslag}' (måste vara 0..9).")
            object.__setattr__(self, "markslag", kod)
        roll_norm = str(self.roll).strip().lower()
        if roll_norm not in _GILTIGA_MARK_ROLLER:
            raise CadBimFel(
                f"Ogiltig markroll '{self.roll}'. Tillåtna: {sorted(_GILTIGA_MARK_ROLLER)}."
            )
        object.__setattr__(self, "roll", roll_norm)
        object.__setattr__(
            self, "lager_namn", str(self.lager_namn or "markytor").strip() or "markytor"
        )

    @property
    def area_m2(self) -> float:
        """Horisontell area i m² (EPSG:3011)."""
        return float(self.geometri.area)


@dataclass(frozen=True)
class K50Brytlinje:
    """2D- eller 3D-brytlinje i EPSG:3011 och RH 2000."""

    objekt_id: str
    geometri: LineString
    typ: str = "kantsten"
    hojd_m: float | None = None
    lager_namn: str = "brytlinjer"
    egenskaper: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.objekt_id, str) or not self.objekt_id.strip():
            raise CadBimFel("K50Brytlinje.objekt_id måste vara en icke-tom sträng.")
        if not isinstance(self.geometri, LineString) or self.geometri.is_empty:
            raise CadBimFel(f"K50Brytlinje '{self.objekt_id}'.geometri måste vara en LineString.")
        if len(self.geometri.coords) < 2:
            raise CadBimFel(f"K50Brytlinje '{self.objekt_id}' måste ha minst två punkter.")
        if self.hojd_m is not None:
            object.__setattr__(self, "hojd_m", _andligt(self.hojd_m, "hojd_m", minst=0.0))
        object.__setattr__(self, "typ", str(self.typ or "kantsten").strip() or "kantsten")
        object.__setattr__(
            self, "lager_namn", str(self.lager_namn or "brytlinjer").strip() or "brytlinjer"
        )

    @property
    def langd_m(self) -> float:
        """Horisontell längd i meter (EPSG:3011)."""
        return float(self.geometri.length)

    @property
    def har_z(self) -> bool:
        """True om brytlinjen bär explicita 3D Z-koordinater (RH 2000)."""
        return bool(self.geometri.has_z)


@dataclass(frozen=True)
class K50Trad:
    """Träd- eller höjdpunkt i EPSG:3011 och RH 2000."""

    objekt_id: str
    position: Point
    z_mark_rh2000_m: float | None = None
    krondiameter_m: float | None = None
    tradhojd_m: float | None = None
    tradslag: str | None = None
    lager_namn: str = "trad"
    egenskaper: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.objekt_id, str) or not self.objekt_id.strip():
            raise CadBimFel("K50Trad.objekt_id måste vara en icke-tom sträng.")
        if not isinstance(self.position, Point) or self.position.is_empty:
            raise CadBimFel(f"K50Trad '{self.objekt_id}'.position måste vara en icke-tom Point.")
        z_mark = self.z_mark_rh2000_m
        if z_mark is None and self.position.has_z:
            z_mark = float(self.position.z)
        if z_mark is not None:
            object.__setattr__(self, "z_mark_rh2000_m", _andligt(z_mark, "z_mark_rh2000_m"))
        if self.krondiameter_m is not None:
            object.__setattr__(
                self, "krondiameter_m", _andligt(self.krondiameter_m, "krondiameter_m", minst=0.0)
            )
        if self.tradhojd_m is not None:
            object.__setattr__(
                self, "tradhojd_m", _andligt(self.tradhojd_m, "tradhojd_m", minst=0.0)
            )
        object.__setattr__(self, "lager_namn", str(self.lager_namn or "trad").strip() or "trad")


@dataclass(frozen=True)
class K50Modell:
    """Samling K50-geometrier i SWEREF 99 18 00 (EPSG:3011) och RH 2000."""

    byggnader: tuple[K50Byggnad, ...] = ()
    markytor: tuple[K50Markyta, ...] = ()
    brytlinjer: tuple[K50Brytlinje, ...] = ()
    trad: tuple[K50Trad, ...] = ()
    epsg: int = EPSG_INTERN
    hojdsystem: str = HOJDSYSTEM
    kalla_crs: str | None = None
    varningar: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.epsg != EPSG_INTERN:
            raise CadBimFel(
                f"K50Modell måste alltid lagras i EPSG:{EPSG_INTERN}, fick EPSG:{self.epsg}."
            )

    @property
    def antal_objekt(self) -> int:
        """Totalt antal K50-objekt (byggnader + markytor + brytlinjer + träd)."""
        return len(self.byggnader) + len(self.markytor) + len(self.brytlinjer) + len(self.trad)

    def till_k50_ritlager_dict(self, *, kalla: str = "fil:cad_bim") -> dict[str, Any]:
        """Serialiserar byggnader, markytor och brytlinjer till ett K50 `floden-ritlager/v1`-objekt.

        Notera att K50:s `punkt`-roll är reserverad; träd (`self.trad`) lagras därför separat
        och inkluderas inte som K50-ritlagerobjekt.
        """
        lager: list[dict[str, Any]] = []
        objekt: list[dict[str, Any]] = []
        if self.byggnader:
            lager.append(
                {
                    "lager_id": "hus",
                    "namn": "Hus",
                    "geometri": "polygon",
                    "roll": "byggnad",
                    "kalla": kalla,
                    "dataklass": "intern",
                    "egenskaper_mall": {"hojd_m": "tal", "takform": "kod"},
                    "egenskaper_forval": {},
                }
            )
            for idx, b in enumerate(self.byggnader):
                poly = orientera_polygon(b.fotavtryck, sign=1.0)
                coords = [[[float(c[0]), float(c[1])] for c in poly.exterior.coords]]
                eg: dict[str, Any] = {"hojd_m": b.byggnadshojd_m, "takform": b.takform}
                objekt.append(
                    {
                        "objekt_id": _sanera_id(b.objekt_id, f"byggnad_{idx + 1}"),
                        "lager_id": "hus",
                        "geometri": {"type": "Polygon", "coordinates": coords},
                        "egenskaper": eg,
                    }
                )
        if self.markytor:
            lager.append(
                {
                    "lager_id": "mark",
                    "namn": "Mark (ny nivå)",
                    "geometri": "polygon",
                    "roll": "mark",
                    "kalla": kalla,
                    "dataklass": "intern",
                    "egenskaper_mall": {"niva_rh2000": "tal"},
                    "egenskaper_forval": {},
                }
            )
            for idx, y in enumerate(self.markytor):
                poly = orientera_polygon(y.geometri, sign=1.0)
                coords = [[[float(c[0]), float(c[1])] for c in poly.exterior.coords]]
                eg_m: dict[str, Any] = {}
                if y.niva_rh2000_m is not None:
                    eg_m["niva_rh2000"] = y.niva_rh2000_m
                if y.markslag is not None:
                    eg_m["markslag"] = y.markslag
                objekt.append(
                    {
                        "objekt_id": _sanera_id(y.objekt_id, f"mark_{idx + 1}"),
                        "lager_id": "mark",
                        "geometri": {"type": "Polygon", "coordinates": coords},
                        "egenskaper": eg_m,
                    }
                )
        if self.brytlinjer:
            lager.append(
                {
                    "lager_id": "mur-och-kant",
                    "namn": "Mur och kant",
                    "geometri": "linje",
                    "roll": "brytlinje",
                    "kalla": kalla,
                    "dataklass": "intern",
                    "egenskaper_mall": {"typ": "kod", "hojd_m": "tal"},
                    "egenskaper_forval": {},
                }
            )
            for idx, br in enumerate(self.brytlinjer):
                lin_coords = [[float(c[0]), float(c[1])] for c in br.geometri.coords]
                eg_b: dict[str, Any] = {"typ": br.typ}
                if br.hojd_m is not None and br.hojd_m > 0.0:
                    eg_b["hojd_m"] = br.hojd_m
                objekt.append(
                    {
                        "objekt_id": _sanera_id(br.objekt_id, f"brytlinje_{idx + 1}"),
                        "lager_id": "mur-och-kant",
                        "geometri": {"type": "LineString", "coordinates": lin_coords},
                        "egenskaper": eg_b,
                    }
                )
        return {"kontrakt": K50_KONTRAKT, "lager": lager, "objekt": objekt}


# ---------------------------------------------------------------------------
# Spår 1: GeoPackage (.gpkg) import & export
# ---------------------------------------------------------------------------


def _ar_saknat_eller_odefinierat_crs(crs_obj: Any, sokvag: Path, lagernamn: str) -> CRS:
    """Verifierar giltig explicit CRS; kastar annars `CadBimFel` (`ValueError`)."""
    if crs_obj is None:
        raise CadBimFel(
            f"GeoPackage-lagret '{lagernamn}' i '{sokvag.name}' saknar CRS. "
            f"Ange ett explicit koordinatsystem (t.ex. EPSG:{EPSG_INTERN} eller EPSG:3006) — "
            "Floden gissar aldrig CRS."
        )
    try:
        crs = CRS.from_user_input(crs_obj)
    except Exception as exc:
        raise CadBimFel(
            f"Ogiltig CRS i GeoPackage-lagret '{lagernamn}' i '{sokvag.name}': {crs_obj!r}"
        ) from exc

    namn_low = (crs.name or "").strip().lower()
    if namn_low.startswith("undefined") or (crs.to_epsg() is None and not crs.to_wkt().strip()):
        raise CadBimFel(
            f"GeoPackage-lagret '{lagernamn}' i '{sokvag.name}' har en odefinierad CRS "
            f"({crs.name!r}). Ange explicit CRS (t.ex. EPSG:{EPSG_INTERN})."
        )

    # Extra kontroll mot GPKG-tabellen `gpkg_geometry_columns` (srs_id 0 och -1 är odefinierade)
    try:
        with sqlite3.connect(sokvag) as conn:
            rad = conn.execute(
                "SELECT srs_id FROM gpkg_geometry_columns WHERE table_name = ?",
                (lagernamn,),
            ).fetchone()
            if rad is not None and int(rad[0]) in (0, -1):
                raise CadBimFel(
                    f"GeoPackage-lagret '{lagernamn}' i '{sokvag.name}' använder odefinierat "
                    f"GPKG srs_id={rad[0]}. Ange ett explicit koordinatsystem."
                )
    except sqlite3.Error:
        pass

    return crs


def _reprojicera_geometri_bevara_z(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    """Reprojicerar (X, Y) horisontellt med `transformer` och lämnar Z (RH 2000) orörd."""

    def _fn(x: Any, y: Any, z: Any = None) -> tuple[Any, ...]:
        tx, ty = transformer.transform(x, y)
        if z is None:
            return (tx, ty)
        return (tx, ty, z)

    resultat: BaseGeometry = shapely_transform(_fn, geom)
    return resultat


def _formatera_crs_etikett(crs: CRS) -> str:
    epsg = crs.to_epsg()
    if epsg is not None:
        return f"EPSG:{epsg}"
    return str(crs.name or crs.to_string())


def _ar_na(varde: Any) -> bool:
    if varde is None:
        return True
    if isinstance(varde, float) and math.isnan(varde):
        return True
    try:
        if isinstance(varde, np.floating) and bool(np.isnan(varde)):
            return True
    except TypeError:
        pass
    return False


def _valfritt_float(rad: Mapping[str, Any], *nycklar: str) -> float | None:
    for nyckel in nycklar:
        if nyckel in rad and not _ar_na(rad[nyckel]):
            tal = float(rad[nyckel])
            if math.isfinite(tal):
                return tal
    return None


def _valfritt_int(rad: Mapping[str, Any], *nycklar: str) -> int | None:
    for nyckel in nycklar:
        if nyckel in rad and not _ar_na(rad[nyckel]):
            return int(round(float(rad[nyckel])))
    return None


def _valfritt_str(rad: Mapping[str, Any], *nycklar: str) -> str | None:
    for nyckel in nycklar:
        if nyckel in rad and not _ar_na(rad[nyckel]):
            text = str(rad[nyckel]).strip()
            if text:
                return text
    return None


def _gissa_gpkg_roll(
    lagernamn: str,
    kolumner: set[str],
    geometrier: Sequence[BaseGeometry],
    lager_roller: Mapping[str, str] | None,
) -> str:
    if lager_roller and lagernamn in lager_roller:
        return lager_roller[lagernamn].strip().lower()

    namn = lagernamn.strip().lower()
    if any(ord_ in namn for ord_ in ("byggnad", "hus", "building")):
        return "byggnad"
    if any(ord_ in namn for ord_ in ("brytlinje", "breakline", "mur", "kantsten")):
        return "brytlinje"
    if any(ord_ in namn for ord_ in ("trad", "träd", "tree")):
        return "trad"
    if "hardgjort" in namn or "hårdgjort" in namn:
        return "hardgjort"
    if "vegetation" in namn or "gronyta" in namn:
        return "vegetation"
    if "vatten" in namn:
        return "vatten"
    if "mark" in namn:
        return "mark"

    # Fallback utifrån kolumner och geometrityp
    for geom in geometrier:
        if geom is None or geom.is_empty:
            continue
        if isinstance(geom, Polygon | MultiPolygon):
            if kolumner & {"byggnadshojd_m", "hojd_m", "takform", "z_bas_rh2000_m"}:
                return "byggnad"
            return "mark"
        if isinstance(geom, LineString | MultiLineString):
            return "brytlinje"
        if isinstance(geom, Point | MultiPoint):
            return "trad"
    return "mark"


def _explodera_geometri(geom: BaseGeometry) -> list[BaseGeometry]:
    if isinstance(geom, MultiPolygon | MultiLineString | MultiPoint):
        return list(geom.geoms)
    return [geom]


def exportera_k50_till_gpkg(
    modell: K50Modell,
    sokvag: str | Path,
    *,
    crs: str = CRS_EPSG_3011,
) -> Path:
    """Exporterar en `K50Modell` (EPSG:3011) till en flerlagers GeoPackage (`.gpkg`).

    Skapar ett lager per icke-tom kategori (`byggnader`, `markytor`, `brytlinjer`, `trad`) med
    explicit CRS och fulla 3D Z-koordinater (RH 2000) där de finns. Om `crs` skiljer sig från
    `EPSG:3011` reprojiceras horisontella koordinater med `pyproj` medan Z-höjder bevaras.
    """
    if modell.antal_objekt == 0:
        raise CadBimFel("K50Modell saknar objekt att exportera till GeoPackage.")

    try:
        mal_crs = CRS.from_user_input(crs)
    except Exception as exc:
        raise CadBimFel(f"Ogiltig mål-CRS '{crs}' vid GeoPackage-export.") from exc

    intern_crs = CRS.from_epsg(EPSG_INTERN)
    reproj_transformer: Transformer | None = None
    if mal_crs != intern_crs:
        reproj_transformer = Transformer.from_crs(intern_crs, mal_crs, always_xy=True)

    def _till_mal(g: BaseGeometry) -> BaseGeometry:
        if reproj_transformer is None:
            return g
        return _reprojicera_geometri_bevara_z(g, reproj_transformer)

    fil = Path(sokvag)
    fil.parent.mkdir(parents=True, exist_ok=True)
    if fil.exists():
        fil.unlink()

    if modell.byggnader:
        rader_b: list[dict[str, Any]] = []
        geom_b: list[BaseGeometry] = []
        for b in modell.byggnader:
            rader_b.append(
                {
                    "objekt_id": b.objekt_id,
                    "byggnadshojd_m": float(b.byggnadshojd_m),
                    "z_bas_rh2000_m": float(b.z_bas_rh2000_m),
                    "takform": b.takform,
                    "lager_namn": b.lager_namn,
                    "namn": b.namn or "",
                    "area_m2": float(b.area_m2),
                }
            )
            geom_b.append(_till_mal(b.fotavtryck))
        gdf_b = gpd.GeoDataFrame(rader_b, geometry=geom_b, crs=mal_crs)
        gdf_b.to_file(fil, layer="byggnader", driver="GPKG")

    if modell.markytor:
        rader_m: list[dict[str, Any]] = []
        geom_m: list[BaseGeometry] = []
        for m in modell.markytor:
            rader_m.append(
                {
                    "objekt_id": m.objekt_id,
                    "niva_rh2000_m": (
                        float(m.niva_rh2000_m) if m.niva_rh2000_m is not None else np.nan
                    ),
                    "markslag": int(m.markslag) if m.markslag is not None else -1,
                    "roll": m.roll,
                    "lager_namn": m.lager_namn,
                    "area_m2": float(m.area_m2),
                }
            )
            geom_m.append(_till_mal(m.geometri))
        gdf_m = gpd.GeoDataFrame(rader_m, geometry=geom_m, crs=mal_crs)
        gdf_m.to_file(fil, layer="markytor", driver="GPKG")

    if modell.brytlinjer:
        rader_l: list[dict[str, Any]] = []
        geom_l: list[BaseGeometry] = []
        for br in modell.brytlinjer:
            rader_l.append(
                {
                    "objekt_id": br.objekt_id,
                    "typ": br.typ,
                    "hojd_m": float(br.hojd_m) if br.hojd_m is not None else np.nan,
                    "lager_namn": br.lager_namn,
                    "langd_m": float(br.langd_m),
                }
            )
            geom_l.append(_till_mal(br.geometri))
        gdf_l = gpd.GeoDataFrame(rader_l, geometry=geom_l, crs=mal_crs)
        gdf_l.to_file(fil, layer="brytlinjer", driver="GPKG")

    if modell.trad:
        rader_t: list[dict[str, Any]] = []
        geom_t: list[BaseGeometry] = []
        for t in modell.trad:
            rader_t.append(
                {
                    "objekt_id": t.objekt_id,
                    "z_mark_rh2000_m": (
                        float(t.z_mark_rh2000_m) if t.z_mark_rh2000_m is not None else np.nan
                    ),
                    "krondiameter_m": (
                        float(t.krondiameter_m) if t.krondiameter_m is not None else np.nan
                    ),
                    "tradhojd_m": float(t.tradhojd_m) if t.tradhojd_m is not None else np.nan,
                    "tradslag": t.tradslag or "",
                    "lager_namn": t.lager_namn,
                }
            )
            geom_t.append(_till_mal(t.position))
        gdf_t = gpd.GeoDataFrame(rader_t, geometry=geom_t, crs=mal_crs)
        gdf_t.to_file(fil, layer="trad", driver="GPKG")

    return fil


def importera_gpkg_till_k50(
    sokvag: str | Path,
    *,
    lager_roller: Mapping[str, str] | None = None,
) -> K50Modell:
    """Läser en GeoPackage (`.gpkg`) till `K50Modell` i **EPSG:3011 (SWEREF 99 18 00)** / RH 2000.

    Kräver explicit CRS på varje lager och kastar `CadBimFel` (`ValueError`) om CRS saknas eller
    är odefinierad. Lager i annat koordinatsystem (t.ex. EPSG:3006 eller EPSG:4326) reprojiceras
    automatiskt till EPSG:3011 med bevarade 3D Z-höjder.
    """
    fil = Path(sokvag)
    if not fil.is_file():
        raise FileNotFoundError(f"GeoPackage-filen saknas: {fil}")

    try:
        lager_tabell = pyogrio.list_layers(fil)
    except Exception as exc:
        raise CadBimFel(f"Kunde inte läsa lagerlista ur GeoPackage '{fil.name}': {exc}") from exc

    lagernamn_lista = [str(rad[0]) for rad in lager_tabell]
    if not lagernamn_lista:
        raise CadBimFel(f"GeoPackage-filen '{fil.name}' innehåller inga lager.")

    intern_crs = CRS.from_epsg(EPSG_INTERN)
    byggnader: list[K50Byggnad] = []
    markytor: list[K50Markyta] = []
    brytlinjer: list[K50Brytlinje] = []
    trad: list[K50Trad] = []
    varningar: list[str] = []
    kalla_crs_etiketter: list[str] = []

    for lagernamn in lagernamn_lista:
        gdf = gpd.read_file(fil, layer=lagernamn)
        kalla_crs = _ar_saknat_eller_odefinierat_crs(gdf.crs, fil, lagernamn)
        etikett = _formatera_crs_etikett(kalla_crs)
        if etikett not in kalla_crs_etiketter:
            kalla_crs_etiketter.append(etikett)

        transformer: Transformer | None = None
        if kalla_crs != intern_crs:
            transformer = Transformer.from_crs(kalla_crs, intern_crs, always_xy=True)

        kolumner = {str(k) for k in gdf.columns if k != gdf.geometry.name}
        raw_geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]
        roll = _gissa_gpkg_roll(lagernamn, kolumner, raw_geoms, lager_roller)

        for idx, (_, rad_serie) in enumerate(gdf.iterrows()):
            rad_dict = dict(rad_serie)
            raw_geom = rad_dict.get(gdf.geometry.name)
            if raw_geom is None or not isinstance(raw_geom, BaseGeometry) or raw_geom.is_empty:
                varningar.append(
                    f"Lager '{lagernamn}' rad {idx}: tom eller saknad geometri hoppades över."
                )
                continue

            geom_3011 = (
                _reprojicera_geometri_bevara_z(raw_geom, transformer)
                if transformer is not None
                else raw_geom
            )
            delar = _explodera_geometri(geom_3011)
            bas_id = _valfritt_str(rad_dict, "objekt_id", "id", "namn") or f"{lagernamn}_{idx + 1}"
            lager_attr = _valfritt_str(rad_dict, "lager_namn") or lagernamn

            for del_idx, del_geom in enumerate(delar):
                obj_id = bas_id if len(delar) == 1 else f"{bas_id}_{del_idx + 1}"
                if roll == "byggnad":
                    if not isinstance(del_geom, Polygon):
                        varningar.append(
                            f"Byggnadslager '{lagernamn}' objekt '{obj_id}': förväntade Polygon, "
                            f"fick {del_geom.geom_type} — hoppades över."
                        )
                        continue
                    hojd = _valfritt_float(rad_dict, "byggnadshojd_m", "hojd_m")
                    if hojd is None:
                        hojd = 0.0
                        varningar.append(
                            f"Byggnad '{obj_id}' i '{lagernamn}' saknar 'byggnadshojd_m'; "
                            "sattes till 0.0 m."
                        )
                    z_bas = _valfritt_float(rad_dict, "z_bas_rh2000_m", "z_bas_m") or 0.0
                    takform = _valfritt_str(rad_dict, "takform") or "platt"
                    namn = _valfritt_str(rad_dict, "namn")
                    byggnader.append(
                        K50Byggnad(
                            objekt_id=obj_id,
                            fotavtryck=del_geom,
                            byggnadshojd_m=hojd,
                            z_bas_rh2000_m=z_bas,
                            takform=takform,
                            lager_namn=lager_attr,
                            namn=namn,
                        )
                    )
                elif roll in _GILTIGA_MARK_ROLLER:
                    if not isinstance(del_geom, Polygon):
                        varningar.append(
                            f"Marklager '{lagernamn}' objekt '{obj_id}': förväntade Polygon, "
                            f"fick {del_geom.geom_type} — hoppades över."
                        )
                        continue
                    niva = _valfritt_float(rad_dict, "niva_rh2000_m", "niva_rh2000", "hojd_rh2000")
                    markslag = _valfritt_int(rad_dict, "markslag")
                    if markslag is not None and markslag < 0:
                        markslag = None
                    rad_roll = _valfritt_str(rad_dict, "roll") or roll
                    if rad_roll.lower() not in _GILTIGA_MARK_ROLLER:
                        rad_roll = roll
                    markytor.append(
                        K50Markyta(
                            objekt_id=obj_id,
                            geometri=del_geom,
                            niva_rh2000_m=niva,
                            markslag=markslag,
                            roll=rad_roll,
                            lager_namn=lager_attr,
                        )
                    )
                elif roll == "brytlinje":
                    if not isinstance(del_geom, LineString):
                        varningar.append(
                            f"Brytlinjelager '{lagernamn}' objekt '{obj_id}': förväntade "
                            f"LineString, fick {del_geom.geom_type} — hoppades över."
                        )
                        continue
                    typ = _valfritt_str(rad_dict, "typ") or "kantsten"
                    hojd_br = _valfritt_float(rad_dict, "hojd_m")
                    brytlinjer.append(
                        K50Brytlinje(
                            objekt_id=obj_id,
                            geometri=del_geom,
                            typ=typ,
                            hojd_m=hojd_br,
                            lager_namn=lager_attr,
                        )
                    )
                elif roll == "trad":
                    if not isinstance(del_geom, Point):
                        varningar.append(
                            f"Trädlager '{lagernamn}' objekt '{obj_id}': förväntade Point, "
                            f"fick {del_geom.geom_type} — hoppades över."
                        )
                        continue
                    z_mark = _valfritt_float(rad_dict, "z_mark_rh2000_m", "z_rh2000_m")
                    kron = _valfritt_float(rad_dict, "krondiameter_m")
                    thojd = _valfritt_float(rad_dict, "tradhojd_m", "hojd_m")
                    tslag = _valfritt_str(rad_dict, "tradslag")
                    trad.append(
                        K50Trad(
                            objekt_id=obj_id,
                            position=del_geom,
                            z_mark_rh2000_m=z_mark,
                            krondiameter_m=kron,
                            tradhojd_m=thojd,
                            tradslag=tslag,
                            lager_namn=lager_attr,
                        )
                    )
                else:
                    varningar.append(
                        f"Okänd roll '{roll}' för lager '{lagernamn}'; objekt '{obj_id}' "
                        "hoppades över."
                    )

    kalla_crs_str = ", ".join(kalla_crs_etiketter) if kalla_crs_etiketter else None
    return K50Modell(
        byggnader=tuple(byggnader),
        markytor=tuple(markytor),
        brytlinjer=tuple(brytlinjer),
        trad=tuple(trad),
        epsg=EPSG_INTERN,
        hojdsystem=HOJDSYSTEM,
        kalla_crs=kalla_crs_str,
        varningar=tuple(varningar),
    )


# ---------------------------------------------------------------------------
# Spår 2: DXF (.dxf ASCII R12–2018) ren Python import & export
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DxfFace3D:
    """En 3D-yta (`3DFACE`, triangel eller fyrhörning) från eller till DXF i EPSG:3011 / RH 2000."""

    objekt_id: str
    lager_namn: str
    horn_m: tuple[tuple[float, float, float], ...]
    geometri: Polygon

    def __post_init__(self) -> None:
        if len(self.horn_m) not in (3, 4):
            raise CadBimFel(
                f"DxfFace3D '{self.objekt_id}' måste ha 3 eller 4 hörn, fick {len(self.horn_m)}."
            )

    @property
    def area_2d_m2(self) -> float:
        """Projicerad horisontell area i m²."""
        return float(self.geometri.area)

    @property
    def z_medel_rh2000_m(self) -> float:
        """Medelhöjd (m RH 2000) över hörnpunkterna."""
        return sum(p[2] for p in self.horn_m) / float(len(self.horn_m))


@dataclass(frozen=True)
class DxfImportResultat:
    """Strukturerat resultat från `importera_dxf_till_k50`."""

    modell: K50Modell
    faces_3d: tuple[DxfFace3D, ...]
    lagernamn: tuple[str, ...]
    antal_entiteter_lasta: int
    ohamtade_entiteter: dict[str, int]
    varningar: tuple[str, ...]
    enhetsskalfaktor_m: float = 1.0


def _las_dxf_par(sokvag_eller_text: str | Path) -> list[tuple[int, str]]:
    """Läser ASCII DXF till en lista av `(gruppkod, varde)`-par."""
    if isinstance(sokvag_eller_text, str) and (
        "\n" in sokvag_eller_text or "\r" in sokvag_eller_text
    ):
        text = sokvag_eller_text
    else:
        fil = Path(sokvag_eller_text)
        if not fil.is_file():
            raise FileNotFoundError(f"DXF-filen saknas: {fil}")
        raa = fil.read_bytes()
        if raa.startswith(b"AutoCAD Binary DXF"):
            raise CadBimFel(
                f"Filen '{fil.name}' är binär DXF som inte stöds. "
                "Exportera som ASCII DXF (R12–2018)."
            )
        try:
            text = raa.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raa.decode("latin-1")

    if text.startswith("AutoCAD Binary DXF"):
        raise CadBimFel("Binär DXF stöds inte; använd ASCII DXF (R12–2018).")

    rader = text.splitlines()
    if len(rader) < 2:
        raise CadBimFel("DXF-innehållet är för kort för att innehålla giltiga gruppkoder.")

    par: list[tuple[int, str]] = []
    i = 0
    n = len(rader)
    while i + 1 < n:
        kod_str = rader[i].strip()
        val_str = rader[i + 1].strip()
        if not kod_str and not val_str:
            i += 2
            continue
        try:
            kod = int(kod_str)
        except ValueError as exc:
            raise CadBimFel(
                f"Ogiltig DXF-gruppkod på rad {i + 1}: {rader[i]!r} (väntade heltal)."
            ) from exc
        par.append((kod, val_str))
        i += 2
    return par


def _dxf_float(varde: str, sammanhang: str) -> float:
    try:
        tal = float(varde)
    except ValueError as exc:
        raise CadBimFel(f"Ogiltigt flyttal i DXF ({sammanhang}): {varde!r}") from exc
    if not math.isfinite(tal):
        raise CadBimFel(f"Icke-ändligt tal i DXF ({sammanhang}): {varde!r}")
    return tal


def _dxf_int(varde: str, sammanhang: str) -> int:
    try:
        return int(float(varde))
    except ValueError as exc:
        raise CadBimFel(f"Ogiltigt heltal i DXF ({sammanhang}): {varde!r}") from exc


def _gissa_dxf_polygon_roll(
    lager_namn: str,
    thickness_m: float,
    lagermappning: Mapping[str, str] | None,
) -> str:
    if lagermappning:
        if lager_namn in lagermappning:
            return lagermappning[lager_namn].strip().lower()
        for k, v in lagermappning.items():
            if k.lower() == lager_namn.lower():
                return v.strip().lower()
    low = lager_namn.strip().lower()
    if thickness_m > 0.0 or any(ord_ in low for ord_ in ("byggnad", "hus", "building")):
        return "byggnad"
    if "hardgjort" in low or "hårdgjort" in low or "gata" in low:
        return "hardgjort"
    if "vegetation" in low or "gronyta" in low or "gras" in low:
        return "vegetation"
    if "vatten" in low:
        return "vatten"
    return "mark"


def _punkter_lika_3d(
    a: tuple[float, float, float], b: tuple[float, float, float], tol: float = 1e-12
) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol and abs(a[2] - b[2]) <= tol


def _punkter_lika_2d(
    a: tuple[float, float, float], b: tuple[float, float, float], tol: float = 1e-9
) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def importera_dxf_till_k50(
    sokvag_eller_text: str | Path,
    *,
    kalla_epsg: int = EPSG_INTERN,
    lagermappning: Mapping[str, str] | None = None,
) -> DxfImportResultat:
    """Läser ASCII DXF (R12–2018) med ren Python till `DxfImportResultat` i EPSG:3011 / RH 2000.

    Stöder `LWPOLYLINE` (inkl. sluten flagga + elevation/thickness), `POLYLINE`/`VERTEX` (2D &
    3D), `LINE` (3D), `3DFACE` och `POINT`. Ej stödda DXF-entiteter räknas explicit i
    `ohamtade_entiteter` och rapporteras i `varningar` (inga tysta tapp).
    """
    _validera_epsg_kod(kalla_epsg, "kalla_epsg")
    par = _las_dxf_par(sokvag_eller_text)
    transf_2d = skapa_transformator_till_3011(kalla_epsg)

    # 1. Extrahera $INSUNITS från HEADER (om den finns) samt alla entiteter i ENTITIES
    insunits: int | None = None
    entitet_poster: list[tuple[str, list[tuple[int, str]]]] = []

    sektion: str | None = None
    i = 0
    n = len(par)
    while i < n:
        kod, val = par[i]
        if kod == 0 and val == "SECTION" and i + 1 < n and par[i + 1][0] == 2:
            sektion = par[i + 1][1].upper()
            i += 2
            continue
        if kod == 0 and val == "ENDSEC":
            sektion = None
            i += 1
            continue
        if kod == 0 and val == "EOF":
            break

        if sektion == "HEADER":
            if kod == 9 and val.upper() == "$INSUNITS" and i + 1 < n and par[i + 1][0] == 70:
                insunits = _dxf_int(par[i + 1][1], "$INSUNITS")
                i += 2
                continue
            i += 1
            continue

        if sektion == "ENTITIES":
            if kod == 0:
                ent_typ = val.upper()
                ent_data: list[tuple[int, str]] = []
                i += 1
                while i < n and par[i][0] != 0:
                    ent_data.append(par[i])
                    i += 1
                entitet_poster.append((ent_typ, ent_data))
                continue
            i += 1
            continue

        i += 1

    varningar: list[str] = []
    skala_m = 1.0
    if insunits is not None:
        if insunits in _DXF_INSUNITS_TILL_M:
            skala_m = _DXF_INSUNITS_TILL_M[insunits]
            if insunits not in (0, 6):
                varningar.append(
                    f"DXF $INSUNITS={insunits} skalades automatiskt med faktor "
                    f"{skala_m} till meter."
                )
        else:
            varningar.append(
                f"Okänt DXF $INSUNITS={insunits}; koordinater tolkades som meter (skala 1.0)."
            )

    def _till_3011_3d(x: float, y: float, z: float) -> tuple[float, float, float]:
        xm, ym, zm = x * skala_m, y * skala_m, z * skala_m
        if kalla_epsg != EPSG_INTERN:
            e_m, n_m = transf_2d(xm, ym)
            return (float(e_m), float(n_m), zm)
        return (xm, ym, zm)

    byggnader: list[K50Byggnad] = []
    markytor: list[K50Markyta] = []
    brytlinjer: list[K50Brytlinje] = []
    trad: list[K50Trad] = []
    faces_3d: list[DxfFace3D] = []
    lagernamn_set: set[str] = set()
    ohamtade: dict[str, int] = {}
    antal_lasta = 0

    def _registrera_polylinje(
        punkter: list[tuple[float, float, float]],
        sluten: bool,
        lager: str,
        thickness_m: float,
        kalla_typ: str,
    ) -> None:
        nonlocal antal_lasta
        # Rensa på varandra följande dubbletter
        rensade: list[tuple[float, float, float]] = []
        for p in punkter:
            if not rensade or not _punkter_lika_3d(rensade[-1], p):
                rensade.append(p)
        if len(rensade) < 2:
            varningar.append(f"{kalla_typ} i lager '{lager}' har färre än 2 punkter och hoppades.")
            return

        lagernamn_set.add(lager)
        ar_sluten = sluten or (len(rensade) >= 4 and _punkter_lika_2d(rensade[0], rensade[-1]))
        if ar_sluten:
            ring = list(rensade)
            if not _punkter_lika_2d(ring[0], ring[-1]):
                ring.append(ring[0])
            if len(ring) < 4:
                varningar.append(
                    f"Sluten {kalla_typ} i lager '{lager}' har färre än 3 unika hörn och hoppades."
                )
                return
            poly = Polygon(ring)
            if poly.is_empty or float(poly.area) <= 0.0:
                varningar.append(
                    f"Sluten {kalla_typ} i lager '{lager}' bildade degenererad polygon (area 0)."
                )
                return
            z_medel = sum(p[2] for p in ring[:-1]) / float(len(ring) - 1)
            roll = _gissa_dxf_polygon_roll(lager, thickness_m, lagermappning)
            if roll == "byggnad":
                byggnader.append(
                    K50Byggnad(
                        objekt_id=f"dxf_byggnad_{len(byggnader) + 1}",
                        fotavtryck=poly,
                        byggnadshojd_m=max(0.0, thickness_m),
                        z_bas_rh2000_m=z_medel,
                        lager_namn=lager,
                    )
                )
            else:
                mark_roll = roll if roll in _GILTIGA_MARK_ROLLER else "mark"
                markytor.append(
                    K50Markyta(
                        objekt_id=f"dxf_mark_{len(markytor) + 1}",
                        geometri=poly,
                        niva_rh2000_m=z_medel,
                        roll=mark_roll,
                        lager_namn=lager,
                    )
                )
            antal_lasta += 1
        else:
            brytlinjer.append(
                K50Brytlinje(
                    objekt_id=f"dxf_linje_{len(brytlinjer) + 1}",
                    geometri=LineString(rensade),
                    lager_namn=lager,
                )
            )
            antal_lasta += 1

    idx_ent = 0
    antal_poster = len(entitet_poster)
    while idx_ent < antal_poster:
        ent_typ, data = entitet_poster[idx_ent]

        if ent_typ == "LWPOLYLINE":
            lager = "0"
            flaggor = 0
            elevation = 0.0
            thickness = 0.0
            raw_xy: list[tuple[float, float]] = []
            cur_x: float | None = None
            cur_y: float | None = None
            har_bulge = False
            for k, v in data:
                if k == 8:
                    lager = v or "0"
                elif k == 70:
                    flaggor = _dxf_int(v, "LWPOLYLINE.70")
                elif k == 38:
                    elevation = _dxf_float(v, "LWPOLYLINE.38")
                elif k == 39:
                    thickness = _dxf_float(v, "LWPOLYLINE.39")
                elif k == 10:
                    if cur_x is not None and cur_y is not None:
                        raw_xy.append((cur_x, cur_y))
                    cur_x = _dxf_float(v, "LWPOLYLINE.10")
                    cur_y = None
                elif k == 20:
                    cur_y = _dxf_float(v, "LWPOLYLINE.20")
                elif k == 42 and abs(_dxf_float(v, "LWPOLYLINE.42")) > _EPS:
                    har_bulge = True
            if cur_x is not None and cur_y is not None:
                raw_xy.append((cur_x, cur_y))
            if har_bulge:
                varningar.append(
                    f"LWPOLYLINE i lager '{lager}' innehåller cirkelbåge (bulge 42) som "
                    "linjäriserades till kordor."
                )
            pts_3d = [_till_3011_3d(x, y, elevation) for x, y in raw_xy]
            _registrera_polylinje(
                pts_3d,
                sluten=bool(flaggor & 1),
                lager=lager,
                thickness_m=thickness * skala_m,
                kalla_typ="LWPOLYLINE",
            )
            idx_ent += 1
            continue

        if ent_typ == "POLYLINE":
            lager = "0"
            flaggor = 0
            default_z = 0.0
            thickness = 0.0
            for k, v in data:
                if k == 8:
                    lager = v or "0"
                elif k == 70:
                    flaggor = _dxf_int(v, "POLYLINE.70")
                elif k == 30:
                    default_z = _dxf_float(v, "POLYLINE.30")
                elif k == 39:
                    thickness = _dxf_float(v, "POLYLINE.39")

            # Samla efterföljande VERTEX fram till SEQEND
            vertices: list[tuple[float, float, float]] = []
            idx_ent += 1
            while idx_ent < antal_poster:
                sub_typ, sub_data = entitet_poster[idx_ent]
                if sub_typ == "SEQEND":
                    idx_ent += 1
                    break
                if sub_typ == "VERTEX":
                    vx = vy = None
                    vz = default_z
                    vflag = 0
                    for k, v in sub_data:
                        if k == 10:
                            vx = _dxf_float(v, "VERTEX.10")
                        elif k == 20:
                            vy = _dxf_float(v, "VERTEX.20")
                        elif k == 30:
                            vz = _dxf_float(v, "VERTEX.30")
                        elif k == 70:
                            vflag = _dxf_int(v, "VERTEX.70")
                    # Hoppa över spline-kontrollpunkter (16) och polyface-yta-poster (128 utan 64)
                    if vx is not None and vy is not None and not (vflag & 16):
                        vertices.append(_till_3011_3d(vx, vy, vz))
                    idx_ent += 1
                    continue
                break

            if flaggor & (16 | 64):
                ohamtade["POLYLINE_MESH"] = ohamtade.get("POLYLINE_MESH", 0) + 1
                varningar.append(
                    f"POLYLINE-mesh (flaggor={flaggor}) i lager '{lager}' stöds inte som "
                    "polylinje och hoppades över."
                )
                continue

            _registrera_polylinje(
                vertices,
                sluten=bool(flaggor & 1),
                lager=lager,
                thickness_m=thickness * skala_m,
                kalla_typ="POLYLINE",
            )
            continue

        if ent_typ == "LINE":
            lager = "0"
            x0 = y0 = z0 = 0.0
            x1 = y1 = z1 = 0.0
            for k, v in data:
                if k == 8:
                    lager = v or "0"
                elif k == 10:
                    x0 = _dxf_float(v, "LINE.10")
                elif k == 20:
                    y0 = _dxf_float(v, "LINE.20")
                elif k == 30:
                    z0 = _dxf_float(v, "LINE.30")
                elif k == 11:
                    x1 = _dxf_float(v, "LINE.11")
                elif k == 21:
                    y1 = _dxf_float(v, "LINE.21")
                elif k == 31:
                    z1 = _dxf_float(v, "LINE.31")
            p0 = _till_3011_3d(x0, y0, z0)
            p1 = _till_3011_3d(x1, y1, z1)
            lagernamn_set.add(lager)
            brytlinjer.append(
                K50Brytlinje(
                    objekt_id=f"dxf_line_{len(brytlinjer) + 1}",
                    geometri=LineString([p0, p1]),
                    lager_namn=lager,
                )
            )
            antal_lasta += 1
            idx_ent += 1
            continue

        if ent_typ == "3DFACE":
            lager = "0"
            horn_raw = [[0.0, 0.0, 0.0] for _ in range(4)]
            har_horn3 = False
            for k, v in data:
                if k == 8:
                    lager = v or "0"
                elif k in (10, 11, 12, 13):
                    horn_raw[k - 10][0] = _dxf_float(v, f"3DFACE.{k}")
                    if k == 13:
                        har_horn3 = True
                elif k in (20, 21, 22, 23):
                    horn_raw[k - 20][1] = _dxf_float(v, f"3DFACE.{k}")
                    if k == 23:
                        har_horn3 = True
                elif k in (30, 31, 32, 33):
                    horn_raw[k - 30][2] = _dxf_float(v, f"3DFACE.{k}")
                    if k == 33:
                        har_horn3 = True
            horn_3d = [_till_3011_3d(h[0], h[1], h[2]) for h in horn_raw]
            if not har_horn3 or _punkter_lika_3d(horn_3d[3], horn_3d[2]):
                horn_tuple: tuple[tuple[float, float, float], ...] = tuple(horn_3d[:3])
            else:
                horn_tuple = tuple(horn_3d)
            lagernamn_set.add(lager)
            face_poly = Polygon([*horn_tuple, horn_tuple[0]])
            faces_3d.append(
                DxfFace3D(
                    objekt_id=f"dxf_3dface_{len(faces_3d) + 1}",
                    lager_namn=lager,
                    horn_m=horn_tuple,
                    geometri=face_poly,
                )
            )
            antal_lasta += 1
            idx_ent += 1
            continue

        if ent_typ == "POINT":
            lager = "0"
            px = py = pz = 0.0
            for k, v in data:
                if k == 8:
                    lager = v or "0"
                elif k == 10:
                    px = _dxf_float(v, "POINT.10")
                elif k == 20:
                    py = _dxf_float(v, "POINT.20")
                elif k == 30:
                    pz = _dxf_float(v, "POINT.30")
            p3 = _till_3011_3d(px, py, pz)
            lagernamn_set.add(lager)
            trad.append(
                K50Trad(
                    objekt_id=f"dxf_punkt_{len(trad) + 1}",
                    position=Point(p3[0], p3[1], p3[2]),
                    z_mark_rh2000_m=p3[2],
                    lager_namn=lager,
                )
            )
            antal_lasta += 1
            idx_ent += 1
            continue

        # Ej stödd DXF-entitet: räkna explicit, tappa aldrig tyst
        ohamtade[ent_typ] = ohamtade.get(ent_typ, 0) + 1
        idx_ent += 1

    if ohamtade:
        detaljer = ", ".join(f"{typ}: {antal}" for typ, antal in sorted(ohamtade.items()))
        varningar.append(f"Ej stödda DXF-entiteter hoppades över ({detaljer}).")

    modell = K50Modell(
        byggnader=tuple(byggnader),
        markytor=tuple(markytor),
        brytlinjer=tuple(brytlinjer),
        trad=tuple(trad),
        epsg=EPSG_INTERN,
        hojdsystem=HOJDSYSTEM,
        kalla_crs=f"EPSG:{kalla_epsg}",
        varningar=tuple(varningar),
    )
    return DxfImportResultat(
        modell=modell,
        faces_3d=tuple(faces_3d),
        lagernamn=tuple(sorted(lagernamn_set)),
        antal_entiteter_lasta=antal_lasta,
        ohamtade_entiteter=ohamtade,
        varningar=tuple(varningar),
        enhetsskalfaktor_m=skala_m,
    )


def _dxf_pair(kod: int, varde: Any) -> list[str]:
    if isinstance(varde, float):
        return [str(kod), f"{varde:.9f}"]
    return [str(kod), str(varde)]


def _koordinater_3d(
    coords: Sequence[tuple[float, ...]], default_z: float
) -> list[tuple[float, float, float]]:
    return [
        (float(c[0]), float(c[1]), float(c[2]) if len(c) >= 3 else float(default_z))
        for c in coords
    ]


def exportera_k50_till_dxf(
    modell: K50Modell,
    sokvag: str | Path,
    *,
    extra_3dfaces: Sequence[DxfFace3D] = (),
    dxf_version: str = "AC1015",
) -> Path:
    """Exporterar en `K50Modell` (+ valfria `3DFACE`) till ASCII DXF i EPSG:3011 och RH 2000.

    Skriver `$INSUNITS = 6` (meter), deklarerar alla använda lager i `TABLES/LAYER` och
    exporterar byggnader (`LWPOLYLINE` med sluten flagga, `38=z_bas_rh2000_m`,
    `39=byggnadshojd_m`), markytor (`LWPOLYLINE` eller 3D `POLYLINE`/`VERTEX`), brytlinjer
    (3D `LINE`, `LWPOLYLINE` eller 3D `POLYLINE`/`VERTEX`), träd (`POINT`) samt `3DFACE`.
    """
    fil = Path(sokvag)
    fil.parent.mkdir(parents=True, exist_ok=True)

    lager_namn: set[str] = {"0"}
    for b in modell.byggnader:
        lager_namn.add(b.lager_namn)
    for m in modell.markytor:
        lager_namn.add(m.lager_namn)
    for br in modell.brytlinjer:
        lager_namn.add(br.lager_namn)
    for t in modell.trad:
        lager_namn.add(t.lager_namn)
    for face in extra_3dfaces:
        lager_namn.add(face.lager_namn)

    rader: list[str] = []

    # HEADER
    rader.extend(_dxf_pair(999, f"Floden CAD/BIM DXF-export ({CRS_EPSG_3011}, {HOJDSYSTEM})"))
    rader.extend(_dxf_pair(0, "SECTION"))
    rader.extend(_dxf_pair(2, "HEADER"))
    rader.extend(_dxf_pair(9, "$ACADVER"))
    rader.extend(_dxf_pair(1, dxf_version))
    rader.extend(_dxf_pair(9, "$INSUNITS"))
    rader.extend(_dxf_pair(70, 6))  # Meter
    rader.extend(_dxf_pair(0, "ENDSEC"))

    # TABLES (LAYER)
    rader.extend(_dxf_pair(0, "SECTION"))
    rader.extend(_dxf_pair(2, "TABLES"))
    rader.extend(_dxf_pair(0, "TABLE"))
    rader.extend(_dxf_pair(2, "LAYER"))
    rader.extend(_dxf_pair(70, len(lager_namn)))
    for lnamn in sorted(lager_namn):
        rader.extend(_dxf_pair(0, "LAYER"))
        rader.extend(_dxf_pair(2, lnamn))
        rader.extend(_dxf_pair(70, 0))
        rader.extend(_dxf_pair(62, 7))
        rader.extend(_dxf_pair(6, "CONTINUOUS"))
    rader.extend(_dxf_pair(0, "ENDTAB"))
    rader.extend(_dxf_pair(0, "ENDSEC"))

    # ENTITIES
    rader.extend(_dxf_pair(0, "SECTION"))
    rader.extend(_dxf_pair(2, "ENTITIES"))

    def _skriv_lwpolyline(
        punkter_xy: Sequence[tuple[float, float]],
        lager: str,
        *,
        sluten: bool,
        elevation_m: float,
        thickness_m: float = 0.0,
    ) -> None:
        rader.extend(_dxf_pair(0, "LWPOLYLINE"))
        rader.extend(_dxf_pair(8, lager))
        rader.extend(_dxf_pair(90, len(punkter_xy)))
        rader.extend(_dxf_pair(70, 1 if sluten else 0))
        rader.extend(_dxf_pair(38, float(elevation_m)))
        if thickness_m > 0.0:
            rader.extend(_dxf_pair(39, float(thickness_m)))
        for x, y in punkter_xy:
            rader.extend(_dxf_pair(10, float(x)))
            rader.extend(_dxf_pair(20, float(y)))

    def _skriv_3d_polyline(
        punkter_xyz: Sequence[tuple[float, float, float]],
        lager: str,
        *,
        sluten: bool,
    ) -> None:
        rader.extend(_dxf_pair(0, "POLYLINE"))
        rader.extend(_dxf_pair(8, lager))
        rader.extend(_dxf_pair(66, 1))
        rader.extend(_dxf_pair(70, 8 | (1 if sluten else 0)))
        rader.extend(_dxf_pair(10, 0.0))
        rader.extend(_dxf_pair(20, 0.0))
        rader.extend(_dxf_pair(30, 0.0))
        for x, y, z in punkter_xyz:
            rader.extend(_dxf_pair(0, "VERTEX"))
            rader.extend(_dxf_pair(8, lager))
            rader.extend(_dxf_pair(70, 32))
            rader.extend(_dxf_pair(10, float(x)))
            rader.extend(_dxf_pair(20, float(y)))
            rader.extend(_dxf_pair(30, float(z)))
        rader.extend(_dxf_pair(0, "SEQEND"))
        rader.extend(_dxf_pair(8, lager))

    for b in modell.byggnader:
        ring = list(b.fotavtryck.exterior.coords)[:-1]
        _skriv_lwpolyline(
            [(float(c[0]), float(c[1])) for c in ring],
            b.lager_namn,
            sluten=True,
            elevation_m=b.z_bas_rh2000_m,
            thickness_m=b.byggnadshojd_m,
        )

    for m in modell.markytor:
        ring_raw = list(m.geometri.exterior.coords)[:-1]
        z_default = m.niva_rh2000_m if m.niva_rh2000_m is not None else 0.0
        pts_3d = _koordinater_3d(ring_raw, z_default)
        z_vals = [p[2] for p in pts_3d]
        if max(z_vals) - min(z_vals) <= _EPS:
            _skriv_lwpolyline(
                [(p[0], p[1]) for p in pts_3d],
                m.lager_namn,
                sluten=True,
                elevation_m=z_vals[0],
            )
        else:
            _skriv_3d_polyline(pts_3d, m.lager_namn, sluten=True)

    for br in modell.brytlinjer:
        pts_3d = _koordinater_3d(list(br.geometri.coords), 0.0)
        if len(pts_3d) == 2:
            rader.extend(_dxf_pair(0, "LINE"))
            rader.extend(_dxf_pair(8, br.lager_namn))
            rader.extend(_dxf_pair(10, pts_3d[0][0]))
            rader.extend(_dxf_pair(20, pts_3d[0][1]))
            rader.extend(_dxf_pair(30, pts_3d[0][2]))
            rader.extend(_dxf_pair(11, pts_3d[1][0]))
            rader.extend(_dxf_pair(21, pts_3d[1][1]))
            rader.extend(_dxf_pair(31, pts_3d[1][2]))
        else:
            z_vals = [p[2] for p in pts_3d]
            if max(z_vals) - min(z_vals) <= _EPS:
                _skriv_lwpolyline(
                    [(p[0], p[1]) for p in pts_3d],
                    br.lager_namn,
                    sluten=False,
                    elevation_m=z_vals[0],
                )
            else:
                _skriv_3d_polyline(pts_3d, br.lager_namn, sluten=False)

    for t in modell.trad:
        z_m = (
            float(t.position.z)
            if t.position.has_z
            else float(t.z_mark_rh2000_m if t.z_mark_rh2000_m is not None else 0.0)
        )
        rader.extend(_dxf_pair(0, "POINT"))
        rader.extend(_dxf_pair(8, t.lager_namn))
        rader.extend(_dxf_pair(10, float(t.position.x)))
        rader.extend(_dxf_pair(20, float(t.position.y)))
        rader.extend(_dxf_pair(30, z_m))

    for face in extra_3dfaces:
        h = face.horn_m
        h3 = h[3] if len(h) == 4 else h[2]
        rader.extend(_dxf_pair(0, "3DFACE"))
        rader.extend(_dxf_pair(8, face.lager_namn))
        for k_idx, pt in enumerate((h[0], h[1], h[2], h3)):
            rader.extend(_dxf_pair(10 + k_idx, pt[0]))
            rader.extend(_dxf_pair(20 + k_idx, pt[1]))
            rader.extend(_dxf_pair(30 + k_idx, pt[2]))

    rader.extend(_dxf_pair(0, "ENDSEC"))
    rader.extend(_dxf_pair(0, "EOF"))

    fil.write_text("\n".join(rader) + "\n", encoding="utf-8")
    return fil


# ---------------------------------------------------------------------------
# Spår 3: LandXML 1.2 TIN- & brytlinje-export
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LandXmlExportResultat:
    """Sammanfattning från `exportera_landxml_tin`."""

    sokvag: Path
    yta_namn: str
    antal_punkter: int
    antal_trianglar: int
    antal_nan_celler: int
    antal_hoppade_kvadrater: int
    antal_brytlinjer: int
    koordinat_ordning: str
    epsg: int
    varningar: tuple[str, ...] = ()


def _extrahera_affin(
    transform: AffinTransformIndata,
    hojd: int,
    bredd: int,
    *,
    epsg: int = EPSG_INTERN,
) -> tuple[float, float, float, float, float, float]:
    """Normaliserar `GeoTransformInfo`, `Affine` eller 6-tupel till `(a, b, c, d, e, f)`."""
    if epsg != EPSG_INTERN:
        raise CadBimFel(
            f"Höjdraster måste vara i EPSG:{EPSG_INTERN}, fick EPSG:{epsg}."
        )
    if isinstance(transform, GeoTransformProtokoll):
        if int(transform.epsg) != EPSG_INTERN:
            raise CadBimFel(
                f"GeoTransformInfo måste ha epsg={EPSG_INTERN}, fick epsg={transform.epsg}."
            )
        if int(transform.hojd) != hojd or int(transform.bredd) != bredd:
            raise CadBimFel(
                f"Rasterdimension ({hojd}, {bredd}) matchar inte GeoTransformInfo "
                f"({transform.hojd}, {transform.bredd})."
            )
        a = _andligt(transform.pixelstorlek_x, "pixelstorlek_x")
        e = -_andligt(transform.pixelstorlek_y, "pixelstorlek_y")
        c = _andligt(transform.x_min, "x_min")
        f = _andligt(transform.y_max, "y_max")
        b = 0.0
        d = 0.0
    elif isinstance(transform, AffinTransformProtokoll):
        a = _andligt(transform.a, "transform.a")
        b = _andligt(transform.b, "transform.b")
        c = _andligt(transform.c, "transform.c")
        d = _andligt(transform.d, "transform.d")
        e = _andligt(transform.e, "transform.e")
        f = _andligt(transform.f, "transform.f")
    elif isinstance(transform, tuple) and len(transform) == 6:
        a = _andligt(transform[0], "transform[0]")
        b = _andligt(transform[1], "transform[1]")
        c = _andligt(transform[2], "transform[2]")
        d = _andligt(transform[3], "transform[3]")
        e = _andligt(transform[4], "transform[4]")
        f = _andligt(transform[5], "transform[5]")
    else:
        raise CadBimFel(
            "transform måste vara GeoTransformInfo, rasterio.transform.Affine eller 6-tupel "
            "(a, b, c, d, e, f)."
        )

    det = a * e - b * d
    if abs(det) <= _EAR_EPS:
        raise CadBimFel(f"Degenererad affin transform (det = {det}).")
    return (a, b, c, d, e, f)


def _bygg_raster_tin(
    hojd_raster_m: NDArray[np.floating[Any]],
    affin: tuple[float, float, float, float, float, float],
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[int, int, int]],
    int,
    int,
]:
    """Triangulerar 2D höjdraster till (punkter, 0-baserade trianglar, nan-celler, hopp).

    Varje giltig cellkvadrat `(r, c)..(r+1, c+1)` där samtliga fyra cellcentra är ändliga (ej NaN)
    ger exakt två moturs (CCW, normal +Z) trianglar. Kvadrat som berör `NaN` hoppas över explicit.
    Endast punkter som refereras av minst en triangel inkluderas i punktlistan.
    """
    z_arr = np.asarray(hojd_raster_m, dtype=np.float64)
    if z_arr.ndim != 2:
        raise CadBimFel(f"hojd_raster_m måste vara en 2D-matris, fick ndim={z_arr.ndim}.")
    hojd, bredd = z_arr.shape
    if hojd < 2 or bredd < 2:
        raise CadBimFel(
            f"hojd_raster_m måste vara minst 2x2 för TIN-triangulering, fick {z_arr.shape}."
        )

    a, b, c, d, e, f = affin
    giltig = np.isfinite(z_arr)
    antal_nan = int(np.sum(~giltig))

    # Giltiga 2x2-kvadrater där alla 4 hörn är ändliga
    kvad_ok = giltig[:-1, :-1] & giltig[:-1, 1:] & giltig[1:, :-1] & giltig[1:, 1:]
    antal_hoppade = int(np.sum(~kvad_ok))

    # Identifiera vilka (r, c)-noder som faktiskt används i minst en triangel
    anvand = np.zeros((hojd, bredd), dtype=np.bool_)
    anvand[:-1, :-1] |= kvad_ok
    anvand[:-1, 1:] |= kvad_ok
    anvand[1:, :-1] |= kvad_ok
    anvand[1:, 1:] |= kvad_ok

    punkt_idx = np.full((hojd, bredd), -1, dtype=np.int64)
    punkter: list[tuple[float, float, float]] = []

    anv_rader, anv_kols = np.nonzero(anvand)
    for idx_ut, (r_np, k_np) in enumerate(zip(anv_rader, anv_kols, strict=True)):
        r, k = int(r_np), int(k_np)
        e_m = c + (k + 0.5) * a + (r + 0.5) * b
        n_m = f + (k + 0.5) * d + (r + 0.5) * e
        z_m = float(z_arr[r, k])
        punkt_idx[r, k] = idx_ut
        punkter.append((e_m, n_m, z_m))

    # Standard nord-upp har det = a*e - b*d < 0, vilket ger (p00, p10, p11) moturs (CCW)
    flip_winding = (a * e - b * d) > 0.0
    trianglar: list[tuple[int, int, int]] = []
    ok_rader, ok_kols = np.nonzero(kvad_ok)
    for r_np, k_np in zip(ok_rader, ok_kols, strict=True):
        r, k = int(r_np), int(k_np)
        p00 = int(punkt_idx[r, k])
        p01 = int(punkt_idx[r, k + 1])
        p10 = int(punkt_idx[r + 1, k])
        p11 = int(punkt_idx[r + 1, k + 1])
        if flip_winding:
            trianglar.append((p00, p11, p10))
            trianglar.append((p00, p01, p11))
        else:
            trianglar.append((p00, p10, p11))
            trianglar.append((p00, p11, p01))

    return punkter, trianglar, antal_nan, antal_hoppade


def _sampla_z_ur_raster(
    z_arr: NDArray[np.float64],
    affin: tuple[float, float, float, float, float, float],
    e_m: float,
    n_m: float,
) -> float | None:
    """Bilinjär sampling i cellcentra för 2D-brytlinje utan Z; returnerar None utanför/på NaN."""
    a, b, c, d, e, f = affin
    det = a * e - b * d
    dx = e_m - c
    dy = n_m - f
    kol_f = (e * dx - b * dy) / det - 0.5
    rad_f = (-d * dx + a * dy) / det - 0.5
    hojd, bredd = z_arr.shape
    if kol_f < -0.5 or kol_f > bredd - 0.5 or rad_f < -0.5 or rad_f > hojd - 0.5:
        return None
    k0 = max(0, min(bredd - 1, int(math.floor(kol_f))))
    r0 = max(0, min(hojd - 1, int(math.floor(rad_f))))
    k1 = min(bredd - 1, k0 + 1)
    r1 = min(hojd - 1, r0 + 1)
    fx = min(1.0, max(0.0, kol_f - k0))
    fy = min(1.0, max(0.0, rad_f - r0))
    z00 = float(z_arr[r0, k0])
    z01 = float(z_arr[r0, k1])
    z10 = float(z_arr[r1, k0])
    z11 = float(z_arr[r1, k1])
    if all(math.isfinite(z) for z in (z00, z01, z10, z11)):
        return float(
            (z00 * (1.0 - fx) + z01 * fx) * (1.0 - fy)
            + (z10 * (1.0 - fx) + z11 * fx) * fy
        )
    kr = max(0, min(bredd - 1, int(round(kol_f))))
    rr = max(0, min(hojd - 1, int(round(rad_f))))
    zn = float(z_arr[rr, kr])
    return zn if math.isfinite(zn) else None


def exportera_landxml_tin(
    hojd_raster_m: NDArray[np.floating[Any]],
    transform: AffinTransformIndata,
    sokvag: str | Path,
    *,
    brytlinjer: Sequence[K50Brytlinje | LineString] = (),
    yta_namn: str = "floden-markmodell",
    koordinat_ordning: str = "NEZ",
    epsg: int = EPSG_INTERN,
    datum: str = "2026-01-01",
    tid: str = "00:00:00",
) -> LandXmlExportResultat:
    """Exporterar ett höjdraster (EPSG:3011, RH 2000) + valfria 3D-brytlinjer till LandXML 1.2.

    Standard LandXML 1.2 skriver `<Units><Metric .../>`, `<CoordinateSystem epsgCode="3011"/>`,
    `<Surfaces><Surface><Definition surfType="TIN"><Pnts>` + `<Faces>` (två trianglar per
    giltig cellkvadrat, hoppar över `NaN`-celler explicit) samt valfri `<Breaklines>`.
    Filen kan läsas direkt tillbaka med `lapp.landxml.las_landxml_tin`.
    """
    ordning = koordinat_ordning.strip().upper()
    if ordning not in _GILTIGA_KOORDINATORDNINGAR:
        raise CadBimFel(
            f"Ogiltig koordinat_ordning '{koordinat_ordning}'. "
            f"Tillåtna: {sorted(_GILTIGA_KOORDINATORDNINGAR)}."
        )
    if not yta_namn.strip():
        raise CadBimFel("yta_namn får inte vara tomt.")

    z_arr = np.asarray(hojd_raster_m, dtype=np.float64)
    if z_arr.ndim != 2:
        raise CadBimFel(f"hojd_raster_m måste vara 2D, fick ndim={z_arr.ndim}.")
    affin = _extrahera_affin(transform, z_arr.shape[0], z_arr.shape[1], epsg=epsg)
    punkter, trianglar, antal_nan, antal_hoppade = _bygg_raster_tin(z_arr, affin)

    if not trianglar:
        raise CadBimFel(
            "Höjdrastret gav inga giltiga trianglar (för många NaN-celler för LandXML TIN)."
        )

    z_min = min(p[2] for p in punkter)
    z_max = max(p[2] for p in punkter)

    rot = ET.Element(
        f"{{{_LANDXML_NS}}}LandXML",
        attrib={
            "version": "1.2",
            "date": datum,
            "time": tid,
            "readOnly": "false",
        },
    )
    units = ET.SubElement(rot, f"{{{_LANDXML_NS}}}Units")
    ET.SubElement(
        units,
        f"{{{_LANDXML_NS}}}Metric",
        attrib={
            "areaUnit": "squareMeter",
            "linearUnit": "meter",
            "volumeUnit": "cubicMeter",
            "temperatureUnit": "celsius",
            "pressureUnit": "milliBars",
            "angularUnit": "decimal degrees",
            "directionUnit": "decimal degrees",
        },
    )
    ET.SubElement(
        rot,
        f"{{{_LANDXML_NS}}}CoordinateSystem",
        attrib={
            "name": "SWEREF 99 18 00",
            "epsgCode": str(EPSG_INTERN),
            "horizontalCoordinateSystemName": "SWEREF 99 18 00",
            "verticalCoordinateSystemName": HOJDSYSTEM,
        },
    )
    ET.SubElement(
        rot,
        f"{{{_LANDXML_NS}}}Application",
        attrib={"name": "Floden", "manufacturer": "Floden", "version": "0.1.0"},
    )
    surfaces = ET.SubElement(rot, f"{{{_LANDXML_NS}}}Surfaces")
    surface = ET.SubElement(surfaces, f"{{{_LANDXML_NS}}}Surface", attrib={"name": yta_namn})
    definition = ET.SubElement(
        surface,
        f"{{{_LANDXML_NS}}}Definition",
        attrib={
            "surfType": "TIN",
            "elevMin": f"{z_min:.9f}",
            "elevMax": f"{z_max:.9f}",
        },
    )

    pnts = ET.SubElement(definition, f"{{{_LANDXML_NS}}}Pnts")
    for idx, (e_m, n_m, z_m) in enumerate(punkter, start=1):
        p_el = ET.SubElement(pnts, f"{{{_LANDXML_NS}}}P", attrib={"id": str(idx)})
        if ordning == "NEZ":
            p_el.text = f"{n_m:.9f} {e_m:.9f} {z_m:.9f}"
        else:
            p_el.text = f"{e_m:.9f} {n_m:.9f} {z_m:.9f}"

    faces = ET.SubElement(definition, f"{{{_LANDXML_NS}}}Faces")
    for i0, i1, i2 in trianglar:
        f_el = ET.SubElement(faces, f"{{{_LANDXML_NS}}}F")
        f_el.text = f"{i0 + 1} {i1 + 1} {i2 + 1}"

    varningar: list[str] = []
    antal_brk_skrivna = 0
    if brytlinjer:
        breaklines_el = ET.SubElement(definition, f"{{{_LANDXML_NS}}}Breaklines")
        for b_idx, br in enumerate(brytlinjer, start=1):
            if isinstance(br, K50Brytlinje):
                br_namn = br.objekt_id
                geom = br.geometri
            elif isinstance(br, LineString):
                br_namn = f"brytlinje_{b_idx}"
                geom = br
            else:
                raise CadBimFel(
                    f"Brytlinje #{b_idx} måste vara K50Brytlinje eller LineString, fick {type(br)}."
                )

            tripler: list[str] = []
            ogiltig_br = False
            for coord in geom.coords:
                e_m = float(coord[0])
                n_m = float(coord[1])
                if len(coord) >= 3 and math.isfinite(float(coord[2])):
                    z_m = float(coord[2])
                else:
                    samplad = _sampla_z_ur_raster(z_arr, affin, e_m, n_m)
                    if samplad is None:
                        ogiltig_br = True
                        break
                    z_m = samplad
                if ordning == "NEZ":
                    tripler.append(f"{n_m:.9f} {e_m:.9f} {z_m:.9f}")
                else:
                    tripler.append(f"{e_m:.9f} {n_m:.9f} {z_m:.9f}")

            if ogiltig_br or len(tripler) < 2:
                varningar.append(
                    f"Brytlinje '{br_namn}' saknade Z-höjd på NaN-cell eller utanför rastret "
                    "och hoppades över i LandXML."
                )
                continue

            brk_el = ET.SubElement(
                breaklines_el,
                f"{{{_LANDXML_NS}}}Breakline",
                attrib={"name": br_namn, "brkType": "standard"},
            )
            pnt_list = ET.SubElement(brk_el, f"{{{_LANDXML_NS}}}PntList3D")
            pnt_list.text = " ".join(tripler)
            antal_brk_skrivna += 1

    ET.register_namespace("", _LANDXML_NS)
    ET.indent(rot, space="  ")
    xml_bytes = ET.tostring(rot, encoding="utf-8", xml_declaration=True)

    fil = Path(sokvag)
    fil.parent.mkdir(parents=True, exist_ok=True)
    fil.write_bytes(xml_bytes + b"\n")

    return LandXmlExportResultat(
        sokvag=fil,
        yta_namn=yta_namn,
        antal_punkter=len(punkter),
        antal_trianglar=len(trianglar),
        antal_nan_celler=antal_nan,
        antal_hoppade_kvadrater=antal_hoppade,
        antal_brytlinjer=antal_brk_skrivna,
        koordinat_ordning=ordning,
        epsg=EPSG_INTERN,
        varningar=tuple(varningar),
    )


# ---------------------------------------------------------------------------
# Spår 4: IFC (.ifc STEP-SPF ISO 10303-21) ren Python byggnadsvolym-import
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StepRef:
    ent_id: int


@dataclass(frozen=True)
class _StepEnum:
    namn: str


@dataclass(frozen=True)
class _StepDerived:
    pass


@dataclass(frozen=True)
class _StepTyped:
    typ: str
    args: tuple[Any, ...]


@dataclass(frozen=True)
class _StepEntity:
    ent_id: int
    typ: str
    args: tuple[Any, ...]


@dataclass(frozen=True)
class IfcImportResultat:
    """Strukturerat resultat från `importera_ifc_byggnader`."""

    byggnader: tuple[K50Byggnad, ...]
    modell: K50Modell
    langd_skala_till_m: float
    langdenhet: str
    ostodda_representationer: dict[str, int]
    varningar: tuple[str, ...]


def _rensa_step_kommentarer(text: str) -> str:
    """Tar bort `/* ... */`-kommentarer i STEP-SPF utan att röra strängliteraler `'...'`."""
    ut: list[str] = []
    i = 0
    n = len(text)
    i_strang = False
    while i < n:
        ch = text[i]
        if i_strang:
            ut.append(ch)
            if ch == "'":
                if i + 1 < n and text[i + 1] == "'":
                    ut.append("'")
                    i += 2
                    continue
                i_strang = False
            i += 1
            continue
        if ch == "'":
            i_strang = True
            ut.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            slut = text.find("*/", i + 2)
            if slut == -1:
                raise CadBimFel("Oavslutad STEP-kommentar ('/*') i IFC-fil.")
            ut.append(" ")
            i = slut + 2
            continue
        ut.append(ch)
        i += 1
    return "".join(ut)


def _dela_step_satser(data_block: str) -> list[str]:
    """Delar STEP DATA-sektion i `;`-avslutade satser med hänsyn till `'...'`-strängar."""
    satser: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(data_block)
    i_strang = False
    while i < n:
        ch = data_block[i]
        if i_strang:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and data_block[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                i_strang = False
            i += 1
            continue
        if ch == "'":
            i_strang = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            sats = "".join(buf).strip()
            if sats:
                satser.append(sats)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    return satser


class _StepArgParser:
    """Deterministisk rekursiv parser för STEP-SPF-argumentlistor."""

    def __init__(self, text: str) -> None:
        self._s = text
        self._i = 0
        self._n = len(text)

    def _hoppa_blanka(self) -> None:
        while self._i < self._n and self._s[self._i].isspace():
            self._i += 1

    def parsa_lista(self) -> tuple[Any, ...]:
        self._hoppa_blanka()
        if self._i >= self._n or self._s[self._i] != "(":
            raise CadBimFel(f"Förväntade '(' i STEP-argument vid pos {self._i}: {self._s!r}")
        self._i += 1
        element: list[Any] = []
        self._hoppa_blanka()
        if self._i < self._n and self._s[self._i] == ")":
            self._i += 1
            return ()
        while self._i < self._n:
            element.append(self.parsa_varde())
            self._hoppa_blanka()
            if self._i >= self._n:
                raise CadBimFel(f"Oavslutad STEP-parentes i: {self._s!r}")
            ch = self._s[self._i]
            if ch == ",":
                self._i += 1
                continue
            if ch == ")":
                self._i += 1
                return tuple(element)
            raise CadBimFel(f"Oväntat tecken '{ch}' i STEP-argument: {self._s!r}")
        raise CadBimFel(f"Oavslutad STEP-lista i: {self._s!r}")

    def parsa_varde(self) -> Any:
        self._hoppa_blanka()
        if self._i >= self._n:
            raise CadBimFel(f"Oväntat slut i STEP-argument: {self._s!r}")
        ch = self._s[self._i]
        if ch == "(":
            return self.parsa_lista()
        if ch == "$":
            self._i += 1
            return None
        if ch == "*":
            self._i += 1
            return _StepDerived()
        if ch == "#":
            self._i += 1
            start = self._i
            while self._i < self._n and self._s[self._i].isdigit():
                self._i += 1
            if start == self._i:
                raise CadBimFel(f"Ogiltig STEP-referens '#' i: {self._s!r}")
            return _StepRef(int(self._s[start : self._i]))
        if ch == "'":
            self._i += 1
            delar: list[str] = []
            while self._i < self._n:
                c = self._s[self._i]
                if c == "'":
                    if self._i + 1 < self._n and self._s[self._i + 1] == "'":
                        delar.append("'")
                        self._i += 2
                        continue
                    self._i += 1
                    return "".join(delar)
                delar.append(c)
                self._i += 1
            raise CadBimFel(f"Oavslutad STEP-sträng i: {self._s!r}")
        if ch == ".":
            # Kan vara enum .MILLI. eller flyttal .5
            if self._i + 1 < self._n and self._s[self._i + 1].isdigit():
                return self._parsa_tal()
            slut = self._s.find(".", self._i + 1)
            if slut == -1:
                raise CadBimFel(f"Oavslutad STEP-enum i: {self._s!r}")
            namn = self._s[self._i + 1 : slut].strip().upper()
            self._i = slut + 1
            return _StepEnum(namn)
        if ch in "+-0123456789":
            return self._parsa_tal()
        if ch.isalpha() or ch == "_":
            start = self._i
            while self._i < self._n and (self._s[self._i].isalnum() or self._s[self._i] == "_"):
                self._i += 1
            typnamn = self._s[start : self._i].upper()
            self._hoppa_blanka()
            if self._i < self._n and self._s[self._i] == "(":
                args = self.parsa_lista()
                return _StepTyped(typnamn, args)
            return typnamn
        raise CadBimFel(f"Okänt STEP-tecken '{ch}' vid pos {self._i} i: {self._s!r}")

    def _parsa_tal(self) -> int | float:
        start = self._i
        if self._s[self._i] in "+-":
            self._i += 1
        while self._i < self._n and self._s[self._i].isdigit():
            self._i += 1
        ar_float = False
        if self._i < self._n and self._s[self._i] == ".":
            ar_float = True
            self._i += 1
            while self._i < self._n and self._s[self._i].isdigit():
                self._i += 1
        if self._i < self._n and self._s[self._i] in "eE":
            ar_float = True
            self._i += 1
            if self._i < self._n and self._s[self._i] in "+-":
                self._i += 1
            while self._i < self._n and self._s[self._i].isdigit():
                self._i += 1
        token = self._s[start : self._i]
        try:
            return float(token) if ar_float else int(token)
        except ValueError as exc:
            raise CadBimFel(f"Ogiltigt STEP-tal {token!r} i: {self._s!r}") from exc


_STEP_ENTITY_RE = re.compile(r"^#\s*(\d+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\(.*\))$", re.DOTALL)


def _parsa_step_entiteter(ifc_text: str) -> dict[int, _StepEntity]:
    """Parsar alla STEP-SPF-entiteter i `DATA; ... ENDSEC;`."""
    rensad = _rensa_step_kommentarer(ifc_text)
    if "ISO-10303-21" not in rensad[:200].upper():
        raise CadBimFel("IFC-filen saknar giltigt STEP-SPF-huvud ('ISO-10303-21;').")

    m_data = re.search(r"\bDATA\s*;", rensad, flags=re.IGNORECASE)
    if m_data is None:
        raise CadBimFel("IFC-filen saknar 'DATA;'-sektion.")
    m_end = re.search(r"\bENDSEC\s*;", rensad[m_data.end() :], flags=re.IGNORECASE)
    if m_end is None:
        raise CadBimFel("IFC-filen saknar 'ENDSEC;' efter 'DATA;'.")

    data_block = rensad[m_data.end() : m_data.end() + m_end.start()]
    entiteter: dict[int, _StepEntity] = {}
    for sats in _dela_step_satser(data_block):
        m_ent = _STEP_ENTITY_RE.match(sats)
        if m_ent is None:
            continue
        ent_id = int(m_ent.group(1))
        ent_typ = m_ent.group(2).upper()
        arg_text = m_ent.group(3)
        args = _StepArgParser(arg_text).parsa_lista()
        entiteter[ent_id] = _StepEntity(ent_id=ent_id, typ=ent_typ, args=args)
    return entiteter


def _packa_upp_tal(varde: Any, sammanhang: str) -> float:
    if isinstance(varde, _StepTyped) and len(varde.args) == 1:
        varde = varde.args[0]
    if isinstance(varde, int | float) and not isinstance(varde, bool):
        tal = float(varde)
        if math.isfinite(tal):
            return tal
    raise CadBimFel(f"Förväntade ändligt flyttal i IFC ({sammanhang}), fick {varde!r}.")


def _hamta_entitet(entiteter: Mapping[int, _StepEntity], ref: Any) -> _StepEntity | None:
    if isinstance(ref, _StepRef):
        return entiteter.get(ref.ent_id)
    return None


def _bestam_ifc_langdskala(
    entiteter: Mapping[int, _StepEntity], varningar: list[str]
) -> tuple[float, str]:
    """Bestämmer exakt skalfaktor till meter ur `IFCSIUNIT` (och ev. `IFCCONVERSIONBASEDUNIT`)."""
    # Prioritera enheter bundna via IFCPROJECT -> IFCUNITASSIGNMENT
    kandidat_enheter: list[_StepEntity] = []
    for ent in entiteter.values():
        if ent.typ == "IFCPROJECT" and len(ent.args) > 8:
            ua = _hamta_entitet(entiteter, ent.args[8])
            if ua is not None and ua.typ == "IFCUNITASSIGNMENT" and ua.args:
                raw_lista = ua.args[0]
                if isinstance(raw_lista, tuple):
                    for item in raw_lista:
                        u_ent = _hamta_entitet(entiteter, item)
                        if u_ent is not None:
                            kandidat_enheter.append(u_ent)
    if not kandidat_enheter:
        kandidat_enheter = [
            e for e in entiteter.values() if e.typ in ("IFCSIUNIT", "IFCCONVERSIONBASEDUNIT")
        ]

    for u_ent in kandidat_enheter:
        if u_ent.typ == "IFCSIUNIT" and len(u_ent.args) >= 4:
            unit_type = u_ent.args[1]
            prefix = u_ent.args[2]
            name = u_ent.args[3]
            if isinstance(unit_type, _StepEnum) and unit_type.namn == "LENGTHUNIT":
                if not (isinstance(name, _StepEnum) and name.namn == "METRE"):
                    raise CadBimFel(
                        f"IFCSIUNIT #{u_ent.ent_id} har ej stödd längdenhet {name!r} "
                        "(förväntade .METRE.)."
                    )
                prefix_namn = prefix.namn if isinstance(prefix, _StepEnum) else None
                if prefix_namn not in _IFC_SI_PREFIX_TILL_M:
                    raise CadBimFel(
                        f"IFCSIUNIT #{u_ent.ent_id} har okänt SI-prefix '{prefix_namn}'."
                    )
                return (
                    _IFC_SI_PREFIX_TILL_M[prefix_namn],
                    _IFC_PREFIX_ENHET_NAMN.get(prefix_namn, "m"),
                )
        if u_ent.typ == "IFCCONVERSIONBASEDUNIT" and len(u_ent.args) >= 4:
            unit_type = u_ent.args[1]
            if isinstance(unit_type, _StepEnum) and unit_type.namn == "LENGTHUNIT":
                mwu = _hamta_entitet(entiteter, u_ent.args[3])
                if mwu is not None and mwu.typ == "IFCMEASUREWITHUNIT" and len(mwu.args) >= 2:
                    faktor = _packa_upp_tal(mwu.args[0], f"IFCMEASUREWITHUNIT #{mwu.ent_id}")
                    bas_u = _hamta_entitet(entiteter, mwu.args[1])
                    bas_skala = 1.0
                    if bas_u is not None and bas_u.typ == "IFCSIUNIT" and len(bas_u.args) >= 4:
                        p = bas_u.args[2]
                        p_namn = p.namn if isinstance(p, _StepEnum) else None
                        bas_skala = _IFC_SI_PREFIX_TILL_M.get(p_namn, 1.0)
                    enhet_namn = str(u_ent.args[2]) if isinstance(u_ent.args[2], str) else "custom"
                    return (faktor * bas_skala, enhet_namn)

    varningar.append(
        "Ingen IFCSIUNIT (.LENGTHUNIT.) hittades i IFC-filen; längdenhet antas vara meter (1.0)."
    )
    return (1.0, "m")


def _normalisera_vektor(v: NDArray[np.float64], sammanhang: str) -> NDArray[np.float64]:
    norm = float(np.linalg.norm(v))
    if norm <= _EAR_EPS:
        raise CadBimFel(f"Nollvektor i IFC-riktning ({sammanhang}).")
    return np.asarray(v / norm, dtype=np.float64)


def _ifc_punkt_3d(
    entiteter: Mapping[int, _StepEntity], ref: Any, skala_m: float
) -> NDArray[np.float64]:
    ent = _hamta_entitet(entiteter, ref)
    if ent is None or ent.typ != "IFCCARTESIANPOINT" or not ent.args:
        return np.zeros(3, dtype=np.float64)
    coords = ent.args[0]
    if not isinstance(coords, tuple) or len(coords) < 2:
        raise CadBimFel(f"Ogiltig IFCCARTESIANPOINT #{ent.ent_id}: {coords!r}")
    x = _packa_upp_tal(coords[0], f"#{ent.ent_id}.x") * skala_m
    y = _packa_upp_tal(coords[1], f"#{ent.ent_id}.y") * skala_m
    z = _packa_upp_tal(coords[2], f"#{ent.ent_id}.z") * skala_m if len(coords) >= 3 else 0.0
    return np.array([x, y, z], dtype=np.float64)


def _ifc_riktning_3d(
    entiteter: Mapping[int, _StepEntity], ref: Any
) -> NDArray[np.float64] | None:
    ent = _hamta_entitet(entiteter, ref)
    if ent is None or ent.typ != "IFCDIRECTION" or not ent.args:
        return None
    ratios = ent.args[0]
    if not isinstance(ratios, tuple) or len(ratios) < 2:
        raise CadBimFel(f"Ogiltig IFCDIRECTION #{ent.ent_id}: {ratios!r}")
    dx = _packa_upp_tal(ratios[0], f"#{ent.ent_id}.dx")
    dy = _packa_upp_tal(ratios[1], f"#{ent.ent_id}.dy")
    dz = _packa_upp_tal(ratios[2], f"#{ent.ent_id}.dz") if len(ratios) >= 3 else 0.0
    return _normalisera_vektor(np.array([dx, dy, dz], dtype=np.float64), f"#{ent.ent_id}")


def _ifc_axis2placement3d_matris(
    entiteter: Mapping[int, _StepEntity], ref: Any, skala_m: float
) -> NDArray[np.float64]:
    """Bygger en 4x4 homogen transformationsmatris enligt IFC `IfcBuildAxes`."""
    mat = np.eye(4, dtype=np.float64)
    ent = _hamta_entitet(entiteter, ref)
    if ent is None:
        return mat

    if ent.typ == "IFCAXIS2PLACEMENT2D":
        loc = _ifc_punkt_3d(entiteter, ent.args[0] if len(ent.args) > 0 else None, skala_m)
        ref_dir = _ifc_riktning_3d(entiteter, ent.args[1] if len(ent.args) > 1 else None)
        if ref_dir is None:
            x_hat = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        else:
            x_hat = _normalisera_vektor(
                np.array([ref_dir[0], ref_dir[1], 0.0], dtype=np.float64), f"#{ent.ent_id}"
            )
        y_hat = np.array([-x_hat[1], x_hat[0], 0.0], dtype=np.float64)
        z_hat = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        mat[:3, 0] = x_hat
        mat[:3, 1] = y_hat
        mat[:3, 2] = z_hat
        mat[:3, 3] = loc
        return mat

    if ent.typ != "IFCAXIS2PLACEMENT3D":
        raise CadBimFel(
            f"Förväntade IFCAXIS2PLACEMENT3D eller IFCAXIS2PLACEMENT2D, "
            f"fick {ent.typ} #{ent.ent_id}."
        )

    loc = _ifc_punkt_3d(entiteter, ent.args[0] if len(ent.args) > 0 else None, skala_m)
    axis = _ifc_riktning_3d(entiteter, ent.args[1] if len(ent.args) > 1 else None)
    ref_dir = _ifc_riktning_3d(entiteter, ent.args[2] if len(ent.args) > 2 else None)

    if axis is None and ref_dir is None:
        mat[:3, 3] = loc
        return mat

    z_hat = axis if axis is not None else np.array([0.0, 0.0, 1.0], dtype=np.float64)
    if ref_dir is None:
        kandidat = (
            np.array([1.0, 0.0, 0.0], dtype=np.float64)
            if abs(float(z_hat[0])) < 0.99
            else np.array([0.0, 1.0, 0.0], dtype=np.float64)
        )
    else:
        kandidat = ref_dir
    x_proj = kandidat - float(np.dot(kandidat, z_hat)) * z_hat
    x_hat = _normalisera_vektor(x_proj, f"IFCAXIS2PLACEMENT3D #{ent.ent_id}.RefDirection")
    y_hat = np.cross(z_hat, x_hat)

    mat[:3, 0] = x_hat
    mat[:3, 1] = y_hat
    mat[:3, 2] = z_hat
    mat[:3, 3] = loc
    return mat


def _ifc_local_placement_matris(
    entiteter: Mapping[int, _StepEntity],
    ref: Any,
    skala_m: float,
    besokta: set[int] | None = None,
) -> NDArray[np.float64]:
    """Löser rekursiv `IFCLOCALPLACEMENT`-kedja (t.ex. Site -> Building -> Storey -> Element)."""
    if not isinstance(ref, _StepRef):
        return np.eye(4, dtype=np.float64)
    ent = entiteter.get(ref.ent_id)
    if ent is None or ent.typ != "IFCLOCALPLACEMENT":
        return np.eye(4, dtype=np.float64)

    sedda = set(besokta) if besokta is not None else set()
    if ent.ent_id in sedda:
        raise CadBimFel(f"Cirkulär IFCLOCALPLACEMENT-referens upptäckt vid #{ent.ent_id}.")
    sedda.add(ent.ent_id)

    parent_mat = (
        _ifc_local_placement_matris(entiteter, ent.args[0], skala_m, sedda)
        if len(ent.args) > 0 and ent.args[0] is not None
        else np.eye(4, dtype=np.float64)
    )
    local_mat = (
        _ifc_axis2placement3d_matris(entiteter, ent.args[1], skala_m)
        if len(ent.args) > 1 and ent.args[1] is not None
        else np.eye(4, dtype=np.float64)
    )
    return np.asarray(parent_mat @ local_mat, dtype=np.float64)


def _ifc_profil_ringar_2d(
    entiteter: Mapping[int, _StepEntity],
    profil_ref: Any,
    skala_m: float,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]] | None:
    """Extraherar yttre ring + ev. inre hålringar i profilens lokala 2D-plan (meter)."""
    ent = _hamta_entitet(entiteter, profil_ref)
    if ent is None:
        return None

    if ent.typ == "IFCRECTANGLEPROFILEDEF" and len(ent.args) >= 5:
        x_dim = _packa_upp_tal(ent.args[3], f"#{ent.ent_id}.XDim") * skala_m
        y_dim = _packa_upp_tal(ent.args[4], f"#{ent.ent_id}.YDim") * skala_m
        if x_dim <= 0.0 or y_dim <= 0.0:
            raise CadBimFel(
                f"IFCRECTANGLEPROFILEDEF #{ent.ent_id} har ogiltiga mått ({x_dim} x {y_dim} m)."
            )
        hx = 0.5 * x_dim
        hy = 0.5 * y_dim
        lokal_ring = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
        pos_mat = _ifc_axis2placement3d_matris(entiteter, ent.args[2], skala_m)
        ring: list[tuple[float, float]] = []
        for px, py in lokal_ring:
            v = pos_mat @ np.array([px, py, 0.0, 1.0], dtype=np.float64)
            ring.append((float(v[0]), float(v[1])))
        return ring, []

    def _las_kurva_2d(kurva_ref: Any) -> list[tuple[float, float]] | None:
        k_ent = _hamta_entitet(entiteter, kurva_ref)
        if k_ent is None:
            return None
        if k_ent.typ == "IFCPOLYLINE" and k_ent.args and isinstance(k_ent.args[0], tuple):
            pts: list[tuple[float, float]] = []
            for p_ref in k_ent.args[0]:
                p3 = _ifc_punkt_3d(entiteter, p_ref, skala_m)
                pts.append((float(p3[0]), float(p3[1])))
            return pts if len(pts) >= 3 else None
        if k_ent.typ == "IFCINDEXEDPOLYCURVE" and k_ent.args:
            pl_ent = _hamta_entitet(entiteter, k_ent.args[0])
            if (
                pl_ent is not None
                and pl_ent.typ in ("IFCCARTESIANPOINTLIST2D", "IFCCARTESIANPOINTLIST3D")
                and pl_ent.args
                and isinstance(pl_ent.args[0], tuple)
            ):
                pts_idx: list[tuple[float, float]] = []
                for tup in pl_ent.args[0]:
                    if isinstance(tup, tuple) and len(tup) >= 2:
                        x = _packa_upp_tal(tup[0], f"#{pl_ent.ent_id}.x") * skala_m
                        y = _packa_upp_tal(tup[1], f"#{pl_ent.ent_id}.y") * skala_m
                        pts_idx.append((x, y))
                return pts_idx if len(pts_idx) >= 3 else None
        return None

    if ent.typ == "IFCARBITRARYCLOSEDPROFILEDEF" and len(ent.args) >= 3:
        yttre = _las_kurva_2d(ent.args[2])
        if yttre is None:
            return None
        return yttre, []

    if ent.typ == "IFCARBITRARYPROFILEDEFWITHVOIDS" and len(ent.args) >= 4:
        yttre = _las_kurva_2d(ent.args[2])
        if yttre is None:
            return None
        hal: list[list[tuple[float, float]]] = []
        if isinstance(ent.args[3], tuple):
            for h_ref in ent.args[3]:
                h_ring = _las_kurva_2d(h_ref)
                if h_ring is not None:
                    hal.append(h_ring)
        return yttre, hal

    return None


def _extrahera_ifc_extruderad_solid(
    entiteter: Mapping[int, _StepEntity],
    solid_ent: _StepEntity,
    m_placement: NDArray[np.float64],
    skala_m: float,
    ursprung_e_m: float,
    ursprung_n_m: float,
    ursprung_z_m: float,
    kalla_epsg: int,
    transf_2d: Any,
    objekt_id: str,
    namn: str | None,
    varningar: list[str],
) -> K50Byggnad | None:
    if len(solid_ent.args) < 4:
        varningar.append(
            f"IFCEXTRUDEDAREASOLID #{solid_ent.ent_id} saknar obligatoriska attribut och hoppades."
        )
        return None

    profil_res = _ifc_profil_ringar_2d(entiteter, solid_ent.args[0], skala_m)
    if profil_res is None:
        p_ent = _hamta_entitet(entiteter, solid_ent.args[0])
        p_typ = p_ent.typ if p_ent is not None else "OKÄND"
        varningar.append(
            f"IFCEXTRUDEDAREASOLID #{solid_ent.ent_id} använder ej stödd profil '{p_typ}' "
            "och hoppades över."
        )
        return None

    yttre_2d, hal_2d = profil_res
    m_solid = _ifc_axis2placement3d_matris(entiteter, solid_ent.args[1], skala_m)
    m_total = np.asarray(m_placement @ m_solid, dtype=np.float64)

    extr_dir = _ifc_riktning_3d(entiteter, solid_ent.args[2])
    if extr_dir is None:
        extr_dir = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    djup_m = _packa_upp_tal(solid_ent.args[3], f"#{solid_ent.ent_id}.Depth") * skala_m
    if djup_m <= 0.0:
        varningar.append(
            f"IFCEXTRUDEDAREASOLID #{solid_ent.ent_id} har icke-positivt djup ({djup_m} m) "
            "och hoppades över."
        )
        return None

    def _omvandla_ring(
        ring_2d: Sequence[tuple[float, float]], offset_local: NDArray[np.float64]
    ) -> list[tuple[float, float, float]]:
        ut: list[tuple[float, float, float]] = []
        for px, py in ring_2d:
            p_lok = np.array(
                [px + offset_local[0], py + offset_local[1], offset_local[2], 1.0],
                dtype=np.float64,
            )
            p_v = m_total @ p_lok
            e_m = float(p_v[0]) + ursprung_e_m
            n_m = float(p_v[1]) + ursprung_n_m
            z_m = float(p_v[2]) + ursprung_z_m
            if kalla_epsg != EPSG_INTERN:
                e_t, n_t = transf_2d(e_m, n_m)
                e_m, n_m = float(e_t), float(n_t)
            ut.append((e_m, n_m, z_m))
        return ut

    noll_off = np.zeros(3, dtype=np.float64)
    topp_off = extr_dir * djup_m
    bot_yttre = _omvandla_ring(yttre_2d, noll_off)
    top_yttre = _omvandla_ring(yttre_2d, topp_off)

    extr_varld = m_total[:3, :3] @ topp_off
    z_bot_vals = [p[2] for p in bot_yttre]
    z_top_vals = [p[2] for p in top_yttre]
    plan_horisontell = (max(z_bot_vals) - min(z_bot_vals)) <= 1e-6
    extr_vertikal = math.hypot(float(extr_varld[0]), float(extr_varld[1])) <= 1e-6

    if plan_horisontell and extr_vertikal:
        yttre_xy = [(p[0], p[1]) for p in bot_yttre]
        hal_xy = [[(p[0], p[1]) for p in _omvandla_ring(h, noll_off)] for h in hal_2d]
        poly = Polygon(yttre_xy, holes=hal_xy if hal_xy else None)
        z_bas_m = min(z_bot_vals[0], z_top_vals[0])
        hojd_m = abs(z_top_vals[0] - z_bot_vals[0])
    else:
        varningar.append(
            f"IFCEXTRUDEDAREASOLID #{solid_ent.ent_id} har lutande extrudering; "
            "2D-fotavtryck projicerades som konvext hölje."
        )
        alla_xy = [(p[0], p[1]) for p in bot_yttre + top_yttre]
        hull = MultiPoint(alla_xy).convex_hull
        if not isinstance(hull, Polygon):
            return None
        poly = hull
        alla_z = z_bot_vals + z_top_vals
        z_bas_m = min(alla_z)
        hojd_m = max(alla_z) - z_bas_m

    if not poly.is_valid:
        poly = poly.buffer(0.0)
    if not isinstance(poly, Polygon) or poly.is_empty or float(poly.area) <= 0.0:
        varningar.append(
            f"IFCEXTRUDEDAREASOLID #{solid_ent.ent_id} gav ogiltigt/tomt fotavtryck."
        )
        return None

    poly_ccw = orientera_polygon(poly, sign=1.0)
    return K50Byggnad(
        objekt_id=objekt_id,
        fotavtryck=poly_ccw,
        byggnadshojd_m=hojd_m,
        z_bas_rh2000_m=z_bas_m,
        lager_namn="ifc_byggnader",
        namn=namn,
    )


def importera_ifc_byggnader(
    sokvag_eller_text: str | Path,
    *,
    ursprung_e_m: float = 0.0,
    ursprung_n_m: float = 0.0,
    ursprung_z_m: float = 0.0,
    kalla_epsg: int = EPSG_INTERN,
) -> IfcImportResultat:
    """Läser byggnadsvolymer ur en IFC STEP-SPF-fil (ISO 10303-21) med ren Python.

    Parsar `IFCSIUNIT` (mm/cm/m → meter), `IFCSITE`/`IFCBUILDING`/`IFCLOCALPLACEMENT`
    (rekursiv 3D-translation och `IFCAXIS2PLACEMENT3D`-rotation) samt `IFCEXTRUDEDAREASOLID`
    med `IFCRECTANGLEPROFILEDEF` och `IFCARBITRARYCLOSEDPROFILEDEF` (`IFCPOLYLINE` /
    `IFCCARTESIANPOINT`). Ostödda eller hoppade representationer rapporteras explicit i
    `ostodda_representationer` och `varningar`.
    """
    _andligt(ursprung_e_m, "ursprung_e_m")
    _andligt(ursprung_n_m, "ursprung_n_m")
    _andligt(ursprung_z_m, "ursprung_z_m")
    _validera_epsg_kod(kalla_epsg, "kalla_epsg")

    if isinstance(sokvag_eller_text, str) and "ISO-10303-21" in sokvag_eller_text[:200].upper():
        ifc_text = sokvag_eller_text
    else:
        fil = Path(sokvag_eller_text)
        if not fil.is_file():
            raise FileNotFoundError(f"IFC-filen saknas: {fil}")
        raa = fil.read_bytes()
        try:
            ifc_text = raa.decode("utf-8-sig")
        except UnicodeDecodeError:
            ifc_text = raa.decode("latin-1")

    entiteter = _parsa_step_entiteter(ifc_text)
    varningar: list[str] = []
    skala_m, enhet_namn = _bestam_ifc_langdskala(entiteter, varningar)
    transf_2d = skapa_transformator_till_3011(kalla_epsg)

    byggnader: list[K50Byggnad] = []
    ostodda: dict[str, int] = {}

    for ent_id in sorted(entiteter):
        ent = entiteter[ent_id]
        if ent.typ not in _IFC_BYGGNAD_PRODUKTTYPER:
            continue
        if len(ent.args) < 7:
            continue
        rep_ref = ent.args[6]
        rep_def = _hamta_entitet(entiteter, rep_ref)
        if rep_def is None or rep_def.typ != "IFCPRODUCTDEFINITIONSHAPE":
            continue

        m_placement = _ifc_local_placement_matris(entiteter, ent.args[5], skala_m)
        global_id = ent.args[0] if isinstance(ent.args[0], str) else None
        prod_namn = ent.args[2] if isinstance(ent.args[2], str) and ent.args[2].strip() else None
        bas_id = _sanera_id(prod_namn or global_id or f"ifc_{ent_id}", f"ifc_{ent_id}")

        reps = (
            rep_def.args[2]
            if len(rep_def.args) > 2 and isinstance(rep_def.args[2], tuple)
            else ()
        )
        solider_i_produkt: list[_StepEntity] = []
        for srep_ref in reps:
            srep = _hamta_entitet(entiteter, srep_ref)
            if srep is None or srep.typ != "IFCSHAPEREPRESENTATION":
                continue
            items = srep.args[3] if len(srep.args) > 3 and isinstance(srep.args[3], tuple) else ()
            for item_ref in items:
                item_ent = _hamta_entitet(entiteter, item_ref)
                if item_ent is None:
                    continue
                if item_ent.typ == "IFCEXTRUDEDAREASOLID":
                    solider_i_produkt.append(item_ent)
                else:
                    ostodda[item_ent.typ] = ostodda.get(item_ent.typ, 0) + 1
                    varningar.append(
                        f"Produkt {ent.typ} #{ent_id} ('{prod_namn or bas_id}'): "
                        f"ej stödd representation '{item_ent.typ}' (#{item_ent.ent_id}) "
                        "hoppades över."
                    )

        for s_idx, solid_ent in enumerate(solider_i_produkt):
            obj_id = bas_id if len(solider_i_produkt) == 1 else f"{bas_id}_{s_idx + 1}"
            bygg = _extrahera_ifc_extruderad_solid(
                entiteter,
                solid_ent,
                m_placement,
                skala_m,
                ursprung_e_m,
                ursprung_n_m,
                ursprung_z_m,
                kalla_epsg,
                transf_2d,
                obj_id,
                prod_namn,
                varningar,
            )
            if bygg is not None:
                byggnader.append(bygg)

    modell = K50Modell(
        byggnader=tuple(byggnader),
        epsg=EPSG_INTERN,
        hojdsystem=HOJDSYSTEM,
        kalla_crs=f"EPSG:{kalla_epsg}",
        varningar=tuple(varningar),
    )
    return IfcImportResultat(
        byggnader=tuple(byggnader),
        modell=modell,
        langd_skala_till_m=skala_m,
        langdenhet=enhet_namn,
        ostodda_representationer=ostodda,
        varningar=tuple(varningar),
    )


# ---------------------------------------------------------------------------
# Spår 5: 3D-mesh-export (.obj & binär .glb glTF 2.0)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mesh3DExportResultat:
    """Sammanfattning och metadata från `exportera_3d_obj` / `exportera_3d_glb`."""

    sokvag: Path
    format_namn: str
    epsg: int
    hojdsystem: str
    ursprung_e_m: float
    ursprung_n_m: float
    ursprung_z_m: float
    antal_horn: int
    antal_trianglar: int
    antal_terrang_trianglar: int
    antal_byggnad_trianglar: int
    filstorlek_bytes: int


@dataclass(frozen=True)
class _MeshGrupp:
    namn: str
    horn_xyz_m: NDArray[np.float64]  # (N, 3) i lokal Z-upp: (dE, dN, dZ)
    normaler: NDArray[np.float64]  # (N, 3) i lokal Z-upp
    trianglar: NDArray[np.uint32]  # (M, 3)


def _rensa_2d_ring(ring: Sequence[tuple[float, ...]]) -> list[tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in ring]
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) <= _EPS and abs(pts[0][1] - pts[-1][1]) <= _EPS:
        pts.pop()
    rensad: list[tuple[float, float]] = []
    for p in pts:
        if not rensad or math.hypot(p[0] - rensad[-1][0], p[1] - rensad[-1][1]) > _EPS:
            rensad.append(p)
    if (
        len(rensad) > 1
        and math.hypot(rensad[0][0] - rensad[-1][0], rensad[0][1] - rensad[-1][1]) <= _EPS
    ):
        rensad.pop()
    return rensad


def _kors_2d(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _punkt_i_triangel_2d(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    for v in (a, b, c):
        if abs(p[0] - v[0]) <= _EAR_EPS and abs(p[1] - v[1]) <= _EAR_EPS:
            return False
    c1 = _kors_2d(a, b, p)
    c2 = _kors_2d(b, c, p)
    c3 = _kors_2d(c, a, p)
    return c1 >= -_EAR_EPS and c2 >= -_EAR_EPS and c3 >= -_EAR_EPS


def _brygga_hal_till_ring(
    yttre: list[tuple[float, float]], hal_lista: list[list[tuple[float, float]]]
) -> list[tuple[float, float]]:
    """Kopplar inre hålringar (CW) till yttre ring (CCW) via deterministiska nollbreddsbryggor."""
    ring = list(yttre)
    # Sortera hål efter högraste punkt (störst X först)
    sorterade_hal = sorted(
        hal_lista,
        key=lambda h: max((p[0], p[1]) for p in h),
        reverse=True,
    )
    for hal in sorterade_hal:
        if len(hal) < 3:
            continue
        m_idx = max(range(len(hal)), key=lambda i: (hal[i][0], hal[i][1]))
        mx, my = hal[m_idx]

        # Hitta närmaste synliga kant till höger (+X) från M
        basta_x = math.inf
        basta_kant_idx = -1
        n_ring = len(ring)
        for i in range(n_ring):
            a = ring[i]
            b = ring[(i + 1) % n_ring]
            if (a[1] > my) == (b[1] > my):
                continue
            if abs(b[1] - a[1]) <= _EAR_EPS:
                continue
            t = (my - a[1]) / (b[1] - a[1])
            ix = a[0] + t * (b[0] - a[0])
            if ix >= mx - _EAR_EPS and ix < basta_x:
                basta_x = ix
                basta_kant_idx = i

        if basta_kant_idx == -1:
            # Fallback: närmaste hörn på yttre ringen
            p_idx = min(range(n_ring), key=lambda i: math.hypot(ring[i][0] - mx, ring[i][1] - my))
        else:
            va = ring[basta_kant_idx]
            vb = ring[(basta_kant_idx + 1) % n_ring]
            p_idx = basta_kant_idx if va[0] >= vb[0] else (basta_kant_idx + 1) % n_ring
            p_pt = ring[p_idx]
            i_pt = (basta_x, my)
            # Kontrollera reflexa hörn i triangeln (M, I, P)
            basta_vinkel = math.inf
            for j in range(n_ring):
                if j == p_idx:
                    continue
                q = ring[j]
                if _punkt_i_triangel_2d(q, (mx, my), i_pt, p_pt) or _punkt_i_triangel_2d(
                    q, (mx, my), p_pt, i_pt
                ):
                    vinkel = abs(math.atan2(q[1] - my, q[0] - mx))
                    if vinkel < basta_vinkel:
                        basta_vinkel = vinkel
                        p_idx = j

        # Skarva in hålet i yttre ringen vid p_idx
        hal_roterad = hal[m_idx:] + hal[:m_idx] + [hal[m_idx]]
        ring = ring[: p_idx + 1] + hal_roterad + [ring[p_idx]] + ring[p_idx + 1 :]
    return ring


def _triangulera_polygon_2d(
    poly: Polygon,
) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    """Deterministisk ear-clipping-triangulering för 2D-polygon (konvex/konkav/med hål)."""
    poly_ccw = orientera_polygon(poly, sign=1.0)
    yttre = _rensa_2d_ring(list(poly_ccw.exterior.coords))
    if len(yttre) < 3:
        return [], []
    hal_lista = [_rensa_2d_ring(list(h.coords)) for h in poly_ccw.interiors]
    ring = _brygga_hal_till_ring(yttre, hal_lista) if hal_lista else yttre

    aktiv = list(range(len(ring)))
    trianglar: list[tuple[int, int, int]] = []
    max_steg = len(aktiv) * len(aktiv) + 10
    steg = 0

    while len(aktiv) > 3 and steg < max_steg:
        steg += 1
        klippt = False
        m = len(aktiv)
        for pos in range(m):
            i_prev = aktiv[(pos - 1) % m]
            i_curr = aktiv[pos]
            i_next = aktiv[(pos + 1) % m]
            a, b, c = ring[i_prev], ring[i_curr], ring[i_next]
            if _kors_2d(a, b, c) <= _EAR_EPS:
                continue
            har_inre = False
            for k_idx in aktiv:
                if k_idx in (i_prev, i_curr, i_next):
                    continue
                if _punkt_i_triangel_2d(ring[k_idx], a, b, c):
                    har_inre = True
                    break
            if not har_inre:
                trianglar.append((i_prev, i_curr, i_next))
                aktiv.pop(pos)
                klippt = True
                break
        if not klippt:
            # Hantera kolinjära/degenererade hörn utan oändlig loop
            basta_pos = max(
                range(m),
                key=lambda p: _kors_2d(
                    ring[aktiv[(p - 1) % m]], ring[aktiv[p]], ring[aktiv[(p + 1) % m]]
                ),
            )
            i_prev = aktiv[(basta_pos - 1) % m]
            i_curr = aktiv[basta_pos]
            i_next = aktiv[(basta_pos + 1) % m]
            if _kors_2d(ring[i_prev], ring[i_curr], ring[i_next]) > _EAR_EPS:
                trianglar.append((i_prev, i_curr, i_next))
            aktiv.pop(basta_pos)

    if len(aktiv) == 3:
        a, b, c = ring[aktiv[0]], ring[aktiv[1]], ring[aktiv[2]]
        if _kors_2d(a, b, c) > _EAR_EPS:
            trianglar.append((aktiv[0], aktiv[1], aktiv[2]))

    return ring, trianglar


def _berakna_hornnormer(
    horn: NDArray[np.float64], trianglar: NDArray[np.uint32]
) -> NDArray[np.float64]:
    """Beräknar areaviktade enhetsnormaler per hörn i lokal Z-upp-ram."""
    normaler = np.zeros_like(horn, dtype=np.float64)
    v0 = horn[trianglar[:, 0]]
    v1 = horn[trianglar[:, 1]]
    v2 = horn[trianglar[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    np.add.at(normaler, trianglar[:, 0], fn)
    np.add.at(normaler, trianglar[:, 1], fn)
    np.add.at(normaler, trianglar[:, 2], fn)
    langd = np.linalg.norm(normaler, axis=1, keepdims=True)
    ok = langd[:, 0] > _EAR_EPS
    normaler[ok] /= langd[ok]
    normaler[~ok] = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    return normaler


def _extrudera_byggnader_mesh(
    byggnader: Sequence[K50Byggnad],
    ursprung_e_m: float,
    ursprung_n_m: float,
    ursprung_z_m: float,
    *,
    inkludera_botten: bool = True,
) -> _MeshGrupp | None:
    """Bygger platt-skuggad vattentät 3D-mesh för alla byggnader med `byggnadshojd_m > 0`."""
    horn_lista: list[tuple[float, float, float]] = []
    norm_lista: list[tuple[float, float, float]] = []
    tri_lista: list[tuple[int, int, int]] = []

    def _lagg_till_triangel(
        p0: tuple[float, float, float],
        p1: tuple[float, float, float],
        p2: tuple[float, float, float],
    ) -> None:
        v0 = np.array(p0, dtype=np.float64)
        v1 = np.array(p1, dtype=np.float64)
        v2 = np.array(p2, dtype=np.float64)
        n = np.cross(v1 - v0, v2 - v0)
        norm = float(np.linalg.norm(n))
        if norm <= _EAR_EPS:
            return
        n_hat = (float(n[0] / norm), float(n[1] / norm), float(n[2] / norm))
        bas = len(horn_lista)
        horn_lista.extend((p0, p1, p2))
        norm_lista.extend((n_hat, n_hat, n_hat))
        tri_lista.append((bas, bas + 1, bas + 2))

    for b in byggnader:
        if b.byggnadshojd_m <= 0.0:
            continue
        poly_ccw = orientera_polygon(b.fotavtryck, sign=1.0)
        zb = b.z_bas_rh2000_m - ursprung_z_m
        zt = b.z_tak_rh2000_m - ursprung_z_m

        # Väggar: yttre ring (CCW -> utåt) + ev. gårds-/hålringar (CW -> inåtriktad vägg)
        ringar_for_vaggar: list[list[tuple[float, float]]] = [
            _rensa_2d_ring(list(poly_ccw.exterior.coords))
        ]
        for hal in poly_ccw.interiors:
            ringar_for_vaggar.append(_rensa_2d_ring(list(hal.coords)))

        for ring in ringar_for_vaggar:
            n_pts = len(ring)
            for i in range(n_pts):
                e0, n0 = ring[i]
                e1, n1 = ring[(i + 1) % n_pts]
                x0, y0 = e0 - ursprung_e_m, n0 - ursprung_n_m
                x1, y1 = e1 - ursprung_e_m, n1 - ursprung_n_m
                b0 = (x0, y0, zb)
                b1 = (x1, y1, zb)
                t0 = (x0, y0, zt)
                t1 = (x1, y1, zt)
                _lagg_till_triangel(b0, b1, t1)
                _lagg_till_triangel(b0, t1, t0)

        # Tak (+Z) och botten (-Z)
        cap_pts, cap_tris = _triangulera_polygon_2d(poly_ccw)
        for i0, i1, i2 in cap_tris:
            p0 = (cap_pts[i0][0] - ursprung_e_m, cap_pts[i0][1] - ursprung_n_m, zt)
            p1 = (cap_pts[i1][0] - ursprung_e_m, cap_pts[i1][1] - ursprung_n_m, zt)
            p2 = (cap_pts[i2][0] - ursprung_e_m, cap_pts[i2][1] - ursprung_n_m, zt)
            _lagg_till_triangel(p0, p1, p2)
            if inkludera_botten:
                q0 = (p0[0], p0[1], zb)
                q1 = (p1[0], p1[1], zb)
                q2 = (p2[0], p2[1], zb)
                _lagg_till_triangel(q0, q2, q1)

    if not tri_lista:
        return None

    return _MeshGrupp(
        namn="byggnader",
        horn_xyz_m=np.asarray(horn_lista, dtype=np.float64),
        normaler=np.asarray(norm_lista, dtype=np.float64),
        trianglar=np.asarray(tri_lista, dtype=np.uint32),
    )


def _bygg_3d_scen(
    *,
    hojd_raster_m: NDArray[np.floating[Any]] | None = None,
    transform: AffinTransformIndata | None = None,
    byggnader: Sequence[K50Byggnad] = (),
    ursprung_e_m: float | None = None,
    ursprung_n_m: float | None = None,
    ursprung_z_m: float | None = None,
    epsg: int = EPSG_INTERN,
    inkludera_botten: bool = True,
) -> tuple[list[_MeshGrupp], tuple[float, float, float]]:
    """Bygger lokal-centrerade 3D-meshgrupper för terräng och byggnader i EPSG:3011 / RH 2000."""
    if epsg != EPSG_INTERN:
        raise CadBimFel(f"3D-export kräver EPSG:{EPSG_INTERN}, fick EPSG:{epsg}.")
    if hojd_raster_m is not None and transform is None:
        raise CadBimFel("transform måste anges när hojd_raster_m ges till 3D-export.")

    terrang_pts: list[tuple[float, float, float]] = []
    terrang_tris: list[tuple[int, int, int]] = []
    if hojd_raster_m is not None and transform is not None:
        z_arr = np.asarray(hojd_raster_m, dtype=np.float64)
        affin = _extrahera_affin(transform, z_arr.shape[0], z_arr.shape[1], epsg=epsg)
        terrang_pts, terrang_tris, _, _ = _bygg_raster_tin(z_arr, affin)

    # Bestäm deterministiskt ursprung om inte explicit angivet (undviker float32-jitter)
    alla_e: list[float] = [p[0] for p in terrang_pts]
    alla_n: list[float] = [p[1] for p in terrang_pts]
    alla_z: list[float] = [p[2] for p in terrang_pts]
    for b in byggnader:
        if b.byggnadshojd_m <= 0.0:
            continue
        for c in b.fotavtryck.exterior.coords:
            alla_e.append(float(c[0]))
            alla_n.append(float(c[1]))
        alla_z.append(b.z_bas_rh2000_m)
        alla_z.append(b.z_tak_rh2000_m)

    if not alla_e:
        raise CadBimFel("Ingen terräng eller giltiga byggnadsvolymer att exportera till 3D.")

    e0 = (
        _andligt(ursprung_e_m, "ursprung_e_m")
        if ursprung_e_m is not None
        else 0.5 * (min(alla_e) + max(alla_e))
    )
    n0 = (
        _andligt(ursprung_n_m, "ursprung_n_m")
        if ursprung_n_m is not None
        else 0.5 * (min(alla_n) + max(alla_n))
    )
    z0 = (
        _andligt(ursprung_z_m, "ursprung_z_m")
        if ursprung_z_m is not None
        else min(alla_z)
    )

    grupper: list[_MeshGrupp] = []
    if terrang_pts and terrang_tris:
        t_horn = np.asarray(
            [(p[0] - e0, p[1] - n0, p[2] - z0) for p in terrang_pts], dtype=np.float64
        )
        t_tris = np.asarray(terrang_tris, dtype=np.uint32)
        t_norm = _berakna_hornnormer(t_horn, t_tris)
        grupper.append(
            _MeshGrupp(namn="terrang", horn_xyz_m=t_horn, normaler=t_norm, trianglar=t_tris)
        )

    b_grupp = _extrudera_byggnader_mesh(
        byggnader, e0, n0, z0, inkludera_botten=inkludera_botten
    )
    if b_grupp is not None:
        grupper.append(b_grupp)

    if not grupper:
        raise CadBimFel("3D-exporten innehöll inga trianglar.")
    return grupper, (e0, n0, z0)


def exportera_3d_obj(
    sokvag: str | Path,
    *,
    hojd_raster_m: NDArray[np.floating[Any]] | None = None,
    transform: AffinTransformIndata | None = None,
    byggnader: Sequence[K50Byggnad] = (),
    ursprung_e_m: float | None = None,
    ursprung_n_m: float | None = None,
    ursprung_z_m: float | None = None,
    epsg: int = EPSG_INTERN,
    inkludera_botten: bool = True,
) -> Mesh3DExportResultat:
    """Exporterar terrängmesh och extruderade byggnader till Wavefront `.obj` centrerad i meter.

    Lagrar exakt EPSG:3011-ursprung (`ursprung_e_m`, `ursprung_n_m`, `ursprung_z_m`) och CRS i
    OBJ-kommentarhuvudet så att Revit, Rhino och Blender kan återställa absolut georeferens utan
    `float32`-jitter.
    """
    grupper, (e0, n0, z0) = _bygg_3d_scen(
        hojd_raster_m=hojd_raster_m,
        transform=transform,
        byggnader=byggnader,
        ursprung_e_m=ursprung_e_m,
        ursprung_n_m=ursprung_n_m,
        ursprung_z_m=ursprung_z_m,
        epsg=epsg,
        inkludera_botten=inkludera_botten,
    )

    rader: list[str] = [
        "# Floden 3D-mesh-export (Wavefront OBJ)",
        f"# CRS: {CRS_EPSG_3011} (SWEREF 99 18 00)",
        f"# Hojdsystem: {HOJDSYSTEM}",
        "# Axlar: X=Ost (E-e0), Y=Nord (N-n0), Z=Hojd (RH2000-z0)",
        f"# Ursprung_EPSG3011_E_m: {e0:.6f}",
        f"# Ursprung_EPSG3011_N_m: {n0:.6f}",
        f"# Ursprung_RH2000_Z_m: {z0:.6f}",
    ]

    horn_offset = 1
    antal_horn = 0
    antal_terrang_tri = 0
    antal_bygg_tri = 0

    for g in grupper:
        rader.append(f"o {g.namn}")
        for x, y, z in g.horn_xyz_m:
            rader.append(f"v {float(x):.6f} {float(y):.6f} {float(z):.6f}")
        for nx, ny, nz in g.normaler:
            rader.append(f"vn {float(nx):.6f} {float(ny):.6f} {float(nz):.6f}")
        for i0, i1, i2 in g.trianglar:
            a = int(i0) + horn_offset
            b = int(i1) + horn_offset
            c = int(i2) + horn_offset
            rader.append(f"f {a}//{a} {b}//{b} {c}//{c}")
        n_v = int(g.horn_xyz_m.shape[0])
        n_t = int(g.trianglar.shape[0])
        horn_offset += n_v
        antal_horn += n_v
        if g.namn == "terrang":
            antal_terrang_tri += n_t
        else:
            antal_bygg_tri += n_t

    fil = Path(sokvag)
    fil.parent.mkdir(parents=True, exist_ok=True)
    data = ("\n".join(rader) + "\n").encode("utf-8")
    fil.write_bytes(data)

    return Mesh3DExportResultat(
        sokvag=fil,
        format_namn="obj",
        epsg=EPSG_INTERN,
        hojdsystem=HOJDSYSTEM,
        ursprung_e_m=e0,
        ursprung_n_m=n0,
        ursprung_z_m=z0,
        antal_horn=antal_horn,
        antal_trianglar=antal_terrang_tri + antal_bygg_tri,
        antal_terrang_trianglar=antal_terrang_tri,
        antal_byggnad_trianglar=antal_bygg_tri,
        filstorlek_bytes=len(data),
    )


def _z_upp_till_gltf_y_upp(arr: NDArray[np.float64]) -> NDArray[np.float32]:
    """Konverterar högerhänt Z-upp `(dE, dN, dZ)` till glTF 2.0 högerhänt Y-upp `(dE, dZ, -dN)`."""
    ut = np.empty_like(arr, dtype=np.float32)
    ut[:, 0] = arr[:, 0].astype(np.float32)
    ut[:, 1] = arr[:, 2].astype(np.float32)
    ut[:, 2] = (-arr[:, 1]).astype(np.float32)
    return ut


def exportera_3d_glb(
    sokvag: str | Path,
    *,
    hojd_raster_m: NDArray[np.floating[Any]] | None = None,
    transform: AffinTransformIndata | None = None,
    byggnader: Sequence[K50Byggnad] = (),
    ursprung_e_m: float | None = None,
    ursprung_n_m: float | None = None,
    ursprung_z_m: float | None = None,
    epsg: int = EPSG_INTERN,
    inkludera_botten: bool = True,
) -> Mesh3DExportResultat:
    """Exporterar terrängmesh och extruderade byggnader till binär glTF 2.0 (`.glb`).

    All geometri centreras i `float64` kring `(ursprung_e_m, ursprung_n_m, ursprung_z_m)` innan
    konvertering till `float32` för att undvika koordinatjitter i Revit, Rhino och Blender.
    Exakt EPSG:3011-ursprung, CRS och axelkonvention lagras i glTF `asset.extras`.
    """
    grupper, (e0, n0, z0) = _bygg_3d_scen(
        hojd_raster_m=hojd_raster_m,
        transform=transform,
        byggnader=byggnader,
        ursprung_e_m=ursprung_e_m,
        ursprung_n_m=ursprung_n_m,
        ursprung_z_m=ursprung_z_m,
        epsg=epsg,
        inkludera_botten=inkludera_botten,
    )

    bin_buf = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    materials: list[dict[str, Any]] = [
        {
            "name": "terrang",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.76, 0.74, 0.68, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9,
            },
        },
        {
            "name": "byggnader",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.85, 0.55, 0.37, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.75,
            },
        },
    ]

    def _lagg_till_buffer_view(data_bytes: bytes, target: int) -> int:
        # 4-bytes alignment för varje bufferView
        while len(bin_buf) % 4 != 0:
            bin_buf.append(0)
        offset = len(bin_buf)
        bin_buf.extend(data_bytes)
        bv_idx = len(buffer_views)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(data_bytes),
                "target": target,
            }
        )
        return bv_idx

    antal_horn = 0
    antal_terrang_tri = 0
    antal_bygg_tri = 0

    for g in grupper:
        pos_f32 = np.ascontiguousarray(_z_upp_till_gltf_y_upp(g.horn_xyz_m), dtype="<f4")
        norm_f32 = np.ascontiguousarray(_z_upp_till_gltf_y_upp(g.normaler), dtype="<f4")
        idx_u32 = np.ascontiguousarray(g.trianglar.reshape(-1), dtype="<u4")

        bv_pos = _lagg_till_buffer_view(pos_f32.tobytes(), _GLTF_TARGET_ARRAY_BUFFER)
        bv_norm = _lagg_till_buffer_view(norm_f32.tobytes(), _GLTF_TARGET_ARRAY_BUFFER)
        bv_idx = _lagg_till_buffer_view(idx_u32.tobytes(), _GLTF_TARGET_ELEMENT_ARRAY_BUFFER)

        pos_min = [float(v) for v in np.min(pos_f32, axis=0)]
        pos_max = [float(v) for v in np.max(pos_f32, axis=0)]

        acc_pos = len(accessors)
        accessors.append(
            {
                "bufferView": bv_pos,
                "componentType": _GLTF_COMPONENT_FLOAT,
                "count": int(pos_f32.shape[0]),
                "type": "VEC3",
                "min": pos_min,
                "max": pos_max,
            }
        )
        acc_norm = len(accessors)
        accessors.append(
            {
                "bufferView": bv_norm,
                "componentType": _GLTF_COMPONENT_FLOAT,
                "count": int(norm_f32.shape[0]),
                "type": "VEC3",
            }
        )
        acc_idx = len(accessors)
        accessors.append(
            {
                "bufferView": bv_idx,
                "componentType": _GLTF_COMPONENT_UINT,
                "count": int(idx_u32.shape[0]),
                "type": "SCALAR",
                "min": [int(np.min(idx_u32))],
                "max": [int(np.max(idx_u32))],
            }
        )

        mat_idx = 0 if g.namn == "terrang" else 1
        mesh_idx = len(meshes)
        meshes.append(
            {
                "name": g.namn,
                "primitives": [
                    {
                        "attributes": {"POSITION": acc_pos, "NORMAL": acc_norm},
                        "indices": acc_idx,
                        "mode": _GLTF_MODE_TRIANGLES,
                        "material": mat_idx,
                    }
                ],
            }
        )
        nodes.append({"name": g.namn, "mesh": mesh_idx})

        n_v = int(pos_f32.shape[0])
        n_t = int(g.trianglar.shape[0])
        antal_horn += n_v
        if g.namn == "terrang":
            antal_terrang_tri += n_t
        else:
            antal_bygg_tri += n_t

    # 4-bytes padding av BIN-chunk med 0x00 enligt glTF 2.0-specifikationen
    while len(bin_buf) % 4 != 0:
        bin_buf.append(0)

    gltf_doc: dict[str, Any] = {
        "asset": {
            "version": "2.0",
            "generator": "Floden CAD/BIM-export",
            "extras": {
                "crs": CRS_EPSG_3011,
                "crs_namn": "SWEREF 99 18 00",
                "epsg": EPSG_INTERN,
                "hojdsystem": HOJDSYSTEM,
                "ursprung_e_m": float(e0),
                "ursprung_n_m": float(n0),
                "ursprung_z_m": float(z0),
                "axlar": "glTF Y-upp (+X=Ost, +Y=Hojd_RH2000, -Z=Nord)",
            },
        },
        "scene": 0,
        "scenes": [{"name": "Floden", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "materials": materials,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_buf)}],
    }

    json_bytes = json.dumps(gltf_doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # 4-bytes padding av JSON-chunk med ASCII space (0x20) enligt glTF 2.0-specifikationen
    json_pad = (4 - (len(json_bytes) % 4)) % 4
    json_chunk = json_bytes + (b" " * json_pad)
    bin_chunk = bytes(bin_buf)

    total_langd = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    glb_bytes = (
        struct.pack("<III", _GLB_MAGIC, _GLB_VERSION, total_langd)
        + struct.pack("<II", len(json_chunk), _GLB_CHUNK_JSON)
        + json_chunk
        + struct.pack("<II", len(bin_chunk), _GLB_CHUNK_BIN)
        + bin_chunk
    )

    fil = Path(sokvag)
    fil.parent.mkdir(parents=True, exist_ok=True)
    fil.write_bytes(glb_bytes)

    return Mesh3DExportResultat(
        sokvag=fil,
        format_namn="glb",
        epsg=EPSG_INTERN,
        hojdsystem=HOJDSYSTEM,
        ursprung_e_m=e0,
        ursprung_n_m=n0,
        ursprung_z_m=z0,
        antal_horn=antal_horn,
        antal_trianglar=antal_terrang_tri + antal_bygg_tri,
        antal_terrang_trianglar=antal_terrang_tri,
        antal_byggnad_trianglar=antal_bygg_tri,
        filstorlek_bytes=len(glb_bytes),
    )


__all__ = [
    "CRS_EPSG_3011",
    "K50_KONTRAKT",
    "AffinTransformIndata",
    "AffinTransformProtokoll",
    "CadBimFel",
    "DxfFace3D",
    "DxfImportResultat",
    "GeoTransformProtokoll",
    "IfcImportResultat",
    "K50Brytlinje",
    "K50Byggnad",
    "K50Markyta",
    "K50Modell",
    "K50Trad",
    "LandXmlExportResultat",
    "Mesh3DExportResultat",
    "exportera_3d_glb",
    "exportera_3d_obj",
    "exportera_k50_till_dxf",
    "exportera_k50_till_gpkg",
    "exportera_landxml_tin",
    "importera_dxf_till_k50",
    "importera_gpkg_till_k50",
    "importera_ifc_byggnader",
]
