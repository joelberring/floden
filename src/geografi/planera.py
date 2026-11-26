# ruff: noqa: E501
"""Planeringsverktyg för geografier: `floden geografi planera <kommun> --avgransning <fil>`.

Delar upp en avgränsningspolygon (GeoJSON/GeoPackage eller bbox) i standardiserade rutor i
SWEREF 99 18 00 (EPSG:3011) via `markmodell.rutnat.skapa_rutnat`, beräknar exakt area och antal
kärn- och buffertrutor samt uppskattar körtid, topp-RAM och diskutrymme utifrån repots
källbelagda benchmarkvärden.

Viktigt (`AGENTS.md`):
- Alla tid-, RAM- och diskvärden är **uppskattningar** före skarp servermätning (S6) och märks
  alltid explicit med `uppskattning = True` och källhänvisning.
- Planeringsverktyget läser enbart den angivna avgränsningen och kommunens konfiguration, aldrig
  stängda baskartor eller skyddsklassade data. Kommunens dataklass (`oppen` / `stangd`) och en
  tydlig dataskyddsnotis (K38) redovisas alltid.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.validation import explain_validity

from gemensamt.koordinater import EPSG_INTERN, KoordinatFel, transformera
from geografi.modell import GeografiFel
from karna.batch import BatchFel, las_omradespolygon
from karna.paverkan import dagsljus_paverkansradie_m
from kommuner.konfig import KommunKonfig, las_kommun
from markmodell.rutnat import Ruta, RutnatFel, skapa_rutnat

# ---------------------------------------------------------------------------- källbelagda baslinjer
# Samtliga referensvärden kommer direkt ur repots benchmark- och driftfiler (ej S6-servermätning).

STANDARD_RUTSTORLEK_M: float = 500.0
STANDARD_OVERLAPP_M: float = 25.0
STANDARD_UPPLOSNING_RESERV_M: float = 0.5
_MIN_OVERLAPP_AREA_M2: float = 1e-6

# Referensantal celler för standardruta 500×500 m + 25 m överlapp (550×550 m yttre)
REF_SIDA_YTTRE_M: float = 550.0
REF_CELLER_1_0M: int = 550 * 550  # 302 500 celler (#114)
REF_CELLER_0_5M: int = 1100 * 1100  # 1 210 000 celler (#120)
REF_CELLER_2_5M: int = 220 * 220  # 48 400 celler (#123)
REF_CELLER_0_25M: int = 2200 * 2200  # 4 840 000 celler (docs/ARKITEKTUR-DRIFT.md:268)

# STATUS.md #114 (2026-11-25, gpu-batch): 500×500 m, 1,0 m celler, Dagsljus K=64, r=150 m, cpu_f32
BASLINJE_CPU_TID_1_0M_S: float = 4.42
KALLA_114: str = "STATUS.md (#114, 2026-11-25, gpu-batch)"

# STATUS.md #120 (2026-11-25, hetzner-object-storage): 550×550 m vid 0,5 m, 7 COG-lager + sankgraf.parquet
BASLINJE_DISK_RA_0_5M_MB: float = 28.85
BASLINJE_DISK_KOMPRIMERAT_0_5M_MB: float = 3.79
KALLA_120: str = "STATUS.md (#120, 2026-11-25, hetzner-object-storage)"

# STATUS.md #123 (2026-11-25, goteborg-batch-e2e): syntetisk 2×2-batch vid 2,5 m, cpu_f64, ZSTD-COG, 8 objekt
BASLINJE_E2E_TID_2_5M_S: float = 1.176
BASLINJE_E2E_DISK_2_5M_MB: float = 0.438
KALLA_123: str = "STATUS.md (#123, 2026-11-25, goteborg-batch-e2e)"

# docs/benchmark/2026-11-23-container.json (K37): 2 vCPU Intel Xeon, 8 024 MB RAM
K37_EA8A_TID_S: float = 1.75
K37_EA8A_RSS_TOPP_MB: float = 108.9
K37_KVARTER300_TID_S: float = 5.03
K37_KVARTER300_RSS_TOPP_MB: float = 58.5
K37_DEMO_LITEN_TID_S: float = 11.42
K37_DEMO_LITEN_RSS_TOPP_MB: float = 146.4
BAS_PROCESS_RAM_GB: float = K37_DEMO_LITEN_RSS_TOPP_MB / 1024.0
KALLA_K37: str = "docs/benchmark/2026-11-23-container.json (K37)"

# docs/ARKITEKTUR-DRIFT.md:263–288: dimensionering 0,25 m + Hetzner CCX-batchmaskin
DRIFT_RAM_0_25M_MIN_GB: float = 1.0
DRIFT_RAM_0_25M_MEDEL_GB: float = 2.0
DRIFT_RAM_0_25M_MAX_GB: float = 3.0
DRIFT_RA_FULL_0_25M_MB: float = 180.0  # K1 44 + K2 39 + K6 97 MB vid 0,25 m
CCX_KARNOR: int = 48
CCX_RAM_GB: float = 192.0
KALLA_DRIFT: str = "docs/ARKITEKTUR-DRIFT.md:263-288"

# Tröskel för maskinklassrekommendation (basserver vs batch)
_BASSERVER_MAX_RUTOR: int = 16
_BASSERVER_MAX_CPU_TID_S: float = 600.0
_BASSERVER_MAX_RAM_PER_RUTA_GB: float = 2.0

_OPPNA_LICENSER: frozenset[str] = frozenset({"cc0", "cc0-1.0", "cc-by-4.0", "cc by 4.0", "oppen"})
_FILFORMAT_SUFFIX: frozenset[str] = frozenset({".geojson", ".json", ".gpkg"})
_EPSG_RE = re.compile(r"EPSG[:/]+(\d{4,5})", re.IGNORECASE)


class PlaneringsFel(GeografiFel):
    """Ogiltig indata, avgränsning eller parameter vid geografiplanering."""


@dataclass(frozen=True)
class PlaneringsAntagande:
    """Ett källbelagt antagande i resursuppskattningen (alltid märkt uppskattning=True)."""

    id: str
    storhet: str
    varde: float | int | str
    enhet: str
    kalla: str
    skalningsformel: str
    kommentar: str
    uppskattning: bool = True

    def till_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "storhet": self.storhet,
            "varde": self.varde,
            "enhet": self.enhet,
            "kalla": self.kalla,
            "skalningsformel": self.skalningsformel,
            "kommentar": self.kommentar,
            "uppskattning": self.uppskattning,
        }


@dataclass(frozen=True)
class GeografiPlan:
    """Resultat av `planera_geografi`: exakta rut-/ytsiffror + källbelagda resursuppskattningar."""

    uppskattning: bool
    status_notis: str

    kommun_id: str
    kommun_namn: str
    publicering_epsg: int
    internt_epsg: int
    indata_epsg: int
    konverterad_till_3011: bool
    dataklass: str
    stangd_baskarta: bool
    k38_varning: bool
    moln_batch_tillaten: bool
    oppen_s3_tillaten: bool
    extern_ai_tillaten: bool
    dataskyddsnotis: str

    avgransning_kalla: str
    upplosning_m: float
    rutstorlek_m: float
    overlapp_m: float
    buffert_m: float
    buffert_kalla: str
    dagsljus_paverkansradie_m: float
    kommun_kontext_m: float
    kommun_max_buffert_m: float | None
    karna_bbox_3011_m: tuple[float, float, float, float]
    total_bbox_3011_m: tuple[float, float, float, float]
    karna_area_m2: float
    karna_area_km2: float
    total_area_m2: float
    total_area_km2: float
    antal_karnrutor: int
    antal_buffertrutor: int
    antal_rutor_totalt: int
    celler_per_ruta: int
    celler_totalt: int
    karnrutor: tuple[str, ...]
    buffertrutor: tuple[str, ...]

    skalningsformel: str
    cell_skalfaktor_vs_1_0m: float
    cell_skalfaktor_vs_0_5m: float
    arbetare: int
    effektiva_arbetare_cpu: int
    tid_per_ruta_cpu_s: float
    kortid_cpu_1_karna_s: float
    kortid_cpu_s: float
    kortid_cpu_h: float
    tid_per_ruta_e2e_ref_s: float
    kortid_e2e_ref_cpu_s: float
    ccx_karnor: int
    ccx_ram_gb: float
    ccx_effektiva_arbetare: int
    kortid_ccx_s: float
    kortid_ccx_h: float
    ram_per_ruta_gb: float
    ram_per_ruta_min_gb: float
    ram_per_ruta_max_gb: float
    ram_totalt_gb: float
    ram_totalt_ccx_gb: float
    disk_ra_per_ruta_mb: float
    disk_komprimerat_per_ruta_mb: float
    disk_ra_gb: float
    disk_komprimerat_gb: float

    maskinklass: str
    driftplats: str
    maskinklass_motivering: str

    antaganden: tuple[PlaneringsAntagande, ...]

    def till_dict(self) -> dict[str, Any]:
        return {
            "uppskattning": self.uppskattning,
            "status_notis": self.status_notis,
            "kommun_id": self.kommun_id,
            "kommun_namn": self.kommun_namn,
            "publicering_epsg": self.publicering_epsg,
            "internt_epsg": self.internt_epsg,
            "indata_epsg": self.indata_epsg,
            "konverterad_till_3011": self.konverterad_till_3011,
            "dataklass": self.dataklass,
            "stangd_baskarta": self.stangd_baskarta,
            "k38_varning": self.k38_varning,
            "moln_batch_tillaten": self.moln_batch_tillaten,
            "oppen_s3_tillaten": self.oppen_s3_tillaten,
            "extern_ai_tillaten": self.extern_ai_tillaten,
            "dataskyddsnotis": self.dataskyddsnotis,
            "avgransning_kalla": self.avgransning_kalla,
            "upplosning_m": self.upplosning_m,
            "rutstorlek_m": self.rutstorlek_m,
            "overlapp_m": self.overlapp_m,
            "buffert_m": round(self.buffert_m, 3),
            "buffert_kalla": self.buffert_kalla,
            "dagsljus_paverkansradie_m": round(self.dagsljus_paverkansradie_m, 3),
            "kommun_kontext_m": round(self.kommun_kontext_m, 3),
            "kommun_max_buffert_m": (
                round(self.kommun_max_buffert_m, 3)
                if self.kommun_max_buffert_m is not None
                else None
            ),
            "karna_bbox_3011_m": [round(v, 3) for v in self.karna_bbox_3011_m],
            "total_bbox_3011_m": [round(v, 3) for v in self.total_bbox_3011_m],
            "karna_area_m2": round(self.karna_area_m2, 2),
            "karna_area_km2": round(self.karna_area_km2, 6),
            "total_area_m2": round(self.total_area_m2, 2),
            "total_area_km2": round(self.total_area_km2, 6),
            "antal_karnrutor": self.antal_karnrutor,
            "antal_buffertrutor": self.antal_buffertrutor,
            "antal_rutor_totalt": self.antal_rutor_totalt,
            "celler_per_ruta": self.celler_per_ruta,
            "celler_totalt": self.celler_totalt,
            "karnrutor": list(self.karnrutor),
            "buffertrutor": list(self.buffertrutor),
            "skalningsformel": self.skalningsformel,
            "cell_skalfaktor_vs_1_0m": round(self.cell_skalfaktor_vs_1_0m, 6),
            "cell_skalfaktor_vs_0_5m": round(self.cell_skalfaktor_vs_0_5m, 6),
            "arbetare": self.arbetare,
            "effektiva_arbetare_cpu": self.effektiva_arbetare_cpu,
            "tid_per_ruta_cpu_s": round(self.tid_per_ruta_cpu_s, 4),
            "kortid_cpu_1_karna_s": round(self.kortid_cpu_1_karna_s, 2),
            "kortid_cpu_s": round(self.kortid_cpu_s, 2),
            "kortid_cpu_h": round(self.kortid_cpu_h, 4),
            "tid_per_ruta_e2e_ref_s": round(self.tid_per_ruta_e2e_ref_s, 4),
            "kortid_e2e_ref_cpu_s": round(self.kortid_e2e_ref_cpu_s, 2),
            "ccx_karnor": self.ccx_karnor,
            "ccx_ram_gb": self.ccx_ram_gb,
            "ccx_effektiva_arbetare": self.ccx_effektiva_arbetare,
            "kortid_ccx_s": round(self.kortid_ccx_s, 2),
            "kortid_ccx_h": round(self.kortid_ccx_h, 4),
            "ram_per_ruta_gb": round(self.ram_per_ruta_gb, 4),
            "ram_per_ruta_min_gb": round(self.ram_per_ruta_min_gb, 4),
            "ram_per_ruta_max_gb": round(self.ram_per_ruta_max_gb, 4),
            "ram_totalt_gb": round(self.ram_totalt_gb, 4),
            "ram_totalt_ccx_gb": round(self.ram_totalt_ccx_gb, 4),
            "disk_ra_per_ruta_mb": round(self.disk_ra_per_ruta_mb, 4),
            "disk_komprimerat_per_ruta_mb": round(self.disk_komprimerat_per_ruta_mb, 4),
            "disk_ra_gb": round(self.disk_ra_gb, 4),
            "disk_komprimerat_gb": round(self.disk_komprimerat_gb, 4),
            "maskinklass": self.maskinklass,
            "driftplats": self.driftplats,
            "maskinklass_motivering": self.maskinklass_motivering,
            "antaganden": [a.till_dict() for a in self.antaganden],
        }


# ---------------------------------------------------------------------------- dataklass & K38


def _ar_oppen_licens(licens: str) -> bool:
    return licens.strip().lower() in _OPPNA_LICENSER


def bedom_kommun_dataklass(kommun: KommunKonfig) -> tuple[str, bool, bool, str]:
    """Bedömer kommunens dataklass (`oppen` eller `stangd`) och skapar K38-dataskyddsnotis.

    Returnerar `(dataklass, stangd_baskarta, k38_varning, dataskyddsnotis)`.
    """
    stangd_baskarta = (
        kommun.byggnader.kalla == "lokal_fil"
        or not _ar_oppen_licens(kommun.byggnader.licens)
        or not _ar_oppen_licens(kommun.baskarta.licens)
    )
    stangt_filpaket = False
    if kommun.filpaket is not None:
        if kommun.filpaket.baskarta is not None and not _ar_oppen_licens(
            kommun.filpaket.baskarta.licens
        ):
            stangt_filpaket = True
        if kommun.filpaket.hojdmodell is not None and not _ar_oppen_licens(
            kommun.filpaket.hojdmodell.licens
        ):
            stangt_filpaket = True

    if stangd_baskarta or stangt_filpaket:
        notis = (
            f"K38-VARNING (stängd data): {kommun.namn} använder icke-öppen baskarta "
            f"(byggnader.kalla={kommun.byggnader.kalla!r}, byggnader.licens={kommun.byggnader.licens!r}, "
            f"baskarta.licens={kommun.baskarta.licens!r}). Enligt AGENTS.md och K38 får baskartan och "
            "härledda lager ALDRIG skickas till extern AI/MCP, publik molnnotebook (Colab) eller öppen "
            "S3-bucket (floden-oppen-eu); kör lokalt eller på Flodens egen server (/app/data/lokal/). "
            "Planeringsverktyget läser enbart den angivna avgränsningspolygonen."
        )
        return "stangd", stangd_baskarta, True, notis

    notis = (
        f"K38 (öppen data): {kommun.namn} använder öppna kommunala källor "
        f"(byggnader.licens={kommun.byggnader.licens!r}, baskarta.licens={kommun.baskarta.licens!r}). "
        "Batchkörning och lagring i öppen S3-bucket (floden-oppen-eu) är tillåten. Rådata från "
        "Lantmäteriet förblir stängd för MCP enligt K38 tills villkoren kontrollerats "
        "(--villkor-kontrollerade)."
    )
    return "oppen", False, False, notis


# ---------------------------------------------------------------------------- inläsning av avgränsning


def _samla_geojson_koordinater(obj: Any, ut: list[tuple[float, float]]) -> None:
    if isinstance(obj, Mapping):
        if "coordinates" in obj:
            _samla_geojson_koordinater(obj["coordinates"], ut)
        if "geometry" in obj:
            _samla_geojson_koordinater(obj["geometry"], ut)
        if "geometries" in obj:
            _samla_geojson_koordinater(obj["geometries"], ut)
        if "features" in obj:
            _samla_geojson_koordinater(obj["features"], ut)
    elif isinstance(obj, list):
        if (
            len(obj) >= 2
            and isinstance(obj[0], int | float)
            and isinstance(obj[1], int | float)
            and not isinstance(obj[0], bool)
            and not isinstance(obj[1], bool)
        ):
            ut.append((float(obj[0]), float(obj[1])))
        else:
            for item in obj:
                _samla_geojson_koordinater(item, ut)


def _detektera_geojson_epsg(data: Mapping[str, Any], angiven_epsg: int | None) -> int:
    """Fastställer käll-EPSG för en GeoJSON-struktur (stöder EPSG-kod, CRS84 och RFC 7946 WGS84)."""
    crs = data.get("crs")
    if isinstance(crs, Mapping):
        props = crs.get("properties")
        if isinstance(props, Mapping):
            namn = str(props.get("name", ""))
            if "CRS84" in namn.upper():
                return 4326
            m = _EPSG_RE.search(namn)
            if m:
                return int(m.group(1))
            for del_str in namn.replace(":", " ").replace("/", " ").split():
                if del_str.isdigit():
                    kod = int(del_str)
                    if kod in (*range(3006, 3019), 4326):
                        return kod

    if angiven_epsg is not None:
        return angiven_epsg

    # Ingen CRS i filen: kontrollera om koordinaterna är geografiska grader (RFC 7946 WGS84).
    punkter: list[tuple[float, float]] = []
    _samla_geojson_koordinater(data, punkter)
    if punkter and all(math.isfinite(x) and math.isfinite(y) for x, y in punkter):
        xs = [p[0] for p in punkter]
        ys = [p[1] for p in punkter]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        dx = max_x - min_x
        dy = max_y - min_y
        if (
            -180.0 <= min_x <= max_x <= 180.0
            and -90.0 <= min_y <= max_y <= 90.0
            and dx < 20.0
            and dy < 20.0
            and not (min_x == 0.0 and min_y == 0.0 and max(dx, dy) >= 5.0)
        ):
            return 4326

    return EPSG_INTERN


def _transformera_till_3011(geom: BaseGeometry, fran_epsg: int) -> BaseGeometry:
    if fran_epsg == EPSG_INTERN:
        return geom
    try:
        def _fn(x: Any, y: Any, z: Any = None) -> tuple[Any, ...]:
            ex, ny = transformera(x, y, fran_epsg, EPSG_INTERN)
            return (ex, ny) if z is None else (ex, ny, z)

        return shapely_transform(_fn, geom)
    except KoordinatFel as exc:
        raise PlaneringsFel(f"Ogiltigt koordinatsystem EPSG:{fran_epsg}: {exc}") from exc


def _validera_polygon(geom: BaseGeometry, kalla: str) -> BaseGeometry:
    if geom.is_empty:
        raise PlaneringsFel(f"Avgränsningen ({kalla}) är tom och innehåller ingen geometri.")
    if not geom.is_valid:
        orsak = explain_validity(geom)
        raise PlaneringsFel(f"Avgränsningen ({kalla}) har ogiltig geometri: {orsak}.")
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise PlaneringsFel(
            f"Avgränsningen ({kalla}) måste vara en Polygon eller MultiPolygon, fick {geom.geom_type}."
        )
    if not all(math.isfinite(v) for v in geom.bounds):
        raise PlaneringsFel(f"Avgränsningen ({kalla}) innehåller ogiltiga (NaN/Inf) koordinater.")
    if not math.isfinite(geom.area) or geom.area <= 0.0:
        raise PlaneringsFel(f"Avgränsningen ({kalla}) har ingen positiv area.")
    return geom


def _tolka_bbox_text(text: str) -> tuple[float, float, float, float] | None:
    delar = [p for p in re.split(r"[,\s;]+", text.strip()) if p]
    if len(delar) != 4:
        return None
    try:
        vals = tuple(float(p) for p in delar)
    except ValueError:
        return None
    return (vals[0], vals[1], vals[2], vals[3])


def _las_avgransning(
    avgransning: str | Path | Sequence[float] | BaseGeometry,
    *,
    avgransning_epsg: int | None = None,
) -> tuple[BaseGeometry, int, str]:
    """Läser avgränsning (GeoJSON/GPKG-fil, bbox eller Shapely-geometri) och konverterar till EPSG:3011.

    Returnerar `(polygon_3011, indata_epsg, beskrivning)`.
    """
    if avgransning_epsg is not None and (
        isinstance(avgransning_epsg, bool)
        or not isinstance(avgransning_epsg, int)
        or avgransning_epsg <= 0
    ):
        raise PlaneringsFel(f"Ogiltig EPSG-kod för avgränsning: {avgransning_epsg!r}.")

    if isinstance(avgransning, BaseGeometry):
        epsg = EPSG_INTERN if avgransning_epsg is None else avgransning_epsg
        geom = _validera_polygon(avgransning, "geometri")
        geom_3011 = _validera_polygon(_transformera_till_3011(geom, epsg), "geometri")
        return geom_3011, epsg, "geometri"

    if isinstance(avgransning, Sequence) and not isinstance(avgransning, str | bytes):
        if len(avgransning) != 4 or any(
            isinstance(v, bool) or not isinstance(v, int | float) for v in avgransning
        ):
            raise PlaneringsFel(
                "Bbox-avgränsning måste ha exakt 4 tal: (e_min, n_min, e_max, n_max)."
            )
        e0, n0, e1, n1 = (float(v) for v in avgransning)
        if not all(math.isfinite(v) for v in (e0, n0, e1, n1)):
            raise PlaneringsFel("Bbox-koordinater måste vara ändliga tal.")
        if e1 <= e0 or n1 <= n0:
            raise PlaneringsFel(
                f"Ogiltig bbox: e_max ({e1}) måste vara > e_min ({e0}) och n_max ({n1}) > n_min ({n0})."
            )
        epsg = EPSG_INTERN if avgransning_epsg is None else avgransning_epsg
        poly_3011 = _validera_polygon(
            _transformera_till_3011(box(e0, n0, e1, n1), epsg),
            "bbox",
        )
        delar_txt = ",".join(f"{v:.3f}".rstrip("0").rstrip(".") for v in (e0, n0, e1, n1))
        return poly_3011, epsg, f"bbox({delar_txt})"

    if isinstance(avgransning, str):
        # Om texten inte är en befintlig fil och kan tolkas som 4 bbox-tal, läs den som bbox.
        sokvag_kandidat = Path(avgransning)
        if not sokvag_kandidat.is_file() and sokvag_kandidat.suffix.lower() not in _FILFORMAT_SUFFIX:
            bbox_vals = _tolka_bbox_text(avgransning)
            if bbox_vals is not None:
                return _las_avgransning(bbox_vals, avgransning_epsg=avgransning_epsg)
        sokvag = sokvag_kandidat
    elif isinstance(avgransning, Path):
        sokvag = avgransning
    else:
        raise PlaneringsFel(f"Ogiltig typ för avgränsning: {type(avgransning).__name__}.")

    if not sokvag.is_file():
        raise PlaneringsFel(f"Avgränsningsfilen finns inte: {sokvag}")

    suffix = sokvag.suffix.lower()
    if suffix in (".geojson", ".json"):
        try:
            data = json.loads(sokvag.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PlaneringsFel(f"Kunde inte läsa GeoJSON-filen {sokvag}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise PlaneringsFel(f"GeoJSON-filen {sokvag} innehåller inget JSON-objekt.")
        indata_epsg = _detektera_geojson_epsg(data, avgransning_epsg)
        # Anropa karna.batch.las_omradespolygon med detekterat standard_epsg.
        # Om källfilen har en annan EPSG-kod utanför batch.py:s fasta lista (3006-3018, 4326)
        # omvandlar vi säkert med _transformera_till_3011.
        try:
            if indata_epsg in (*range(3006, 3019), 4326):
                poly_3011 = las_omradespolygon(sokvag, standard_epsg=indata_epsg)
            else:
                poly_ra = las_omradespolygon(sokvag, standard_epsg=EPSG_INTERN)
                poly_3011 = _transformera_till_3011(poly_ra, indata_epsg)
        except (BatchFel, KoordinatFel, ValueError, TypeError, KeyError) as exc:
            raise PlaneringsFel(f"Kunde inte tolka avgränsningen i {sokvag}: {exc}") from exc
        return _validera_polygon(poly_3011, sokvag.name), indata_epsg, str(sokvag)

    if suffix == ".gpkg":
        try:
            import geopandas as gpd
        except ImportError as exc:
            raise PlaneringsFel(
                f"GeoPackage ({sokvag.name}) kräver geopandas/pyogrio som inte är installerat."
            ) from exc
        try:
            meta = gpd.read_file(sokvag, rows=0)
            fil_epsg = meta.crs.to_epsg() if meta.crs is not None else None
            indata_epsg = (
                int(fil_epsg)
                if fil_epsg is not None
                else (EPSG_INTERN if avgransning_epsg is None else avgransning_epsg)
            )
            poly_3011 = las_omradespolygon(sokvag, standard_epsg=indata_epsg)
            if meta.crs is None and indata_epsg != EPSG_INTERN:
                poly_3011 = _transformera_till_3011(poly_3011, indata_epsg)
        except (BatchFel, KoordinatFel, ValueError, OSError, RuntimeError) as exc:
            raise PlaneringsFel(f"Kunde inte läsa GeoPackage-filen {sokvag}: {exc}") from exc
        return _validera_polygon(poly_3011, sokvag.name), indata_epsg, str(sokvag)

    raise PlaneringsFel(
        f"Stöder inte filformatet '{suffix}' för avgränsning; använd .geojson, .json eller .gpkg."
    )


# ---------------------------------------------------------------------------- huvudberäkning


def _validera_positivt_tal(varde: float, namn: str, *, tillat_noll: bool = False) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float) or not math.isfinite(float(varde)):
        raise PlaneringsFel(f"{namn} måste vara ett ändligt tal, fick {varde!r}.")
    tal = float(varde)
    if tillat_noll:
        if tal < 0.0:
            raise PlaneringsFel(f"{namn} får inte vara negativt, fick {tal:g}.")
    elif tal <= 0.0:
        raise PlaneringsFel(f"{namn} måste vara större än 0, fick {tal:g}.")
    return tal


def _berakna_standardbuffert_m(kommun: KommunKonfig) -> tuple[float, float, float, float | None, str]:
    """Beräknar standardbuffert ur dagsljusets påverkansradie och kommunfilens kontext.

    Returnerar `(buffert_m, dagsljus_radie_m, kontext_m, max_buffert_m, kalla)`.
    """
    hojd_max_m = float(kommun.dagsljus["hojd_max_m"])
    h_min_grader = float(kommun.dagsljus["h_min_grader"])
    dagsljus_radie_m = dagsljus_paverkansradie_m(hojd_max_m, h_min_grader)
    kontext_m = float(kommun.lantmateriet.kontext_m)
    max_buffert_m = (
        max(float(g.max_buffert_m) for g in kommun.geografier.values())
        if kommun.geografier
        else None
    )
    buffert_m = float(math.ceil(max(dagsljus_radie_m, kontext_m)))
    kalla = (
        f"karna.paverkan.dagsljus_paverkansradie_m({hojd_max_m:g} m, {h_min_grader:g}°) = "
        f"{dagsljus_radie_m:.1f} m och kommunfil ({kommun.id}.toml kontext_m={kontext_m:g} m)"
    )
    return buffert_m, dagsljus_radie_m, kontext_m, max_buffert_m, kalla


def _rutor_for_polygon(
    poly_3011: BaseGeometry,
    *,
    rutstorlek_m: float,
    overlapp_m: float,
    upplosning_m: float,
) -> list[Ruta]:
    """Skapar rutor via `markmodell.rutnat.skapa_rutnat` med strikt positiv yta inom `poly_3011`."""
    e0, n0, e1, n1 = (float(v) for v in poly_3011.bounds)
    try:
        kandidater = skapa_rutnat(
            (e0, n0, e1, n1),
            storlek_m=rutstorlek_m,
            overlapp_m=overlapp_m,
            upplosning_m=upplosning_m,
        )
    except RutnatFel as exc:
        raise PlaneringsFel(f"Rutnätet kunde inte skapas: {exc}") from exc
    rutor = [
        r
        for r in kandidater
        if box(*r.inre_bbox).intersection(poly_3011).area > _MIN_OVERLAPP_AREA_M2
    ]
    rutor.sort(key=lambda r: r.namn)
    return rutor


def planera_geografi(
    kommun: str | KommunKonfig,
    avgransning: str | Path | Sequence[float] | BaseGeometry,
    *,
    upplosning_m: float | None = None,
    buffert_m: float | None = None,
    rutstorlek_m: float = STANDARD_RUTSTORLEK_M,
    overlapp_m: float = STANDARD_OVERLAPP_M,
    arbetare: int = 1,
    avgransning_epsg: int | None = None,
    kommunfil: Path | None = None,
    rot: Path | None = None,
    krav_oppen: bool = False,
) -> GeografiPlan:
    """Beräknar exakt rutantal och källbelagd resursuppskattning för en geografi i EPSG:3011.

    Parametrar:
    - `kommun`: kommun-id (läses med `kommuner.konfig.las_kommun`) eller `KommunKonfig`.
    - `avgransning`: sökväg till GeoJSON/GeoPackage, bbox `(e_min, n_min, e_max, n_max)` / text
      eller Shapely-polygon. Konverteras till SWEREF 99 18 00 (EPSG:3011) vid inläsning.
    - `upplosning_m`: cellstorlek i meter (standard ur kommunfilen, annars 0,5 m).
    - `buffert_m`: buffert runt avgränsningen i meter (standard: beräknad ur dagsljusets
      påverkansradie + kommunens kontext).
    - `rutstorlek_m`: inre rutstorlek i meter (standard 500,0 m).
    - `overlapp_m`: överlapp runt varje ruta i meter (standard 25,0 m).
    - `arbetare`: antal parallella CPU-arbetare för baslinjeuppskattningen (standard 1).
    - `krav_oppen`: om `True` avvisas kommuner med stängd dataklass med `PlaneringsFel`.
    """
    kfg = (
        kommun
        if isinstance(kommun, KommunKonfig)
        else las_kommun(kommun, rot=rot, fil=kommunfil)
    )

    dataklass, stangd_baskarta, k38_varning, dataskyddsnotis = bedom_kommun_dataklass(kfg)
    if krav_oppen and dataklass != "oppen":
        raise PlaneringsFel(
            f"Kommunen '{kfg.id}' har dataklass '{dataklass}' och nekas med krav_oppen=True. "
            f"{dataskyddsnotis}"
        )

    eff_upplosning_m = _validera_positivt_tal(
        (
            kfg.lantmateriet.upplosning_m
            if kfg.lantmateriet.upplosning_m > 0.0
            else STANDARD_UPPLOSNING_RESERV_M
        )
        if upplosning_m is None
        else upplosning_m,
        "upplosning_m",
    )
    eff_rutstorlek_m = _validera_positivt_tal(rutstorlek_m, "rutstorlek_m")
    eff_overlapp_m = _validera_positivt_tal(overlapp_m, "overlapp_m", tillat_noll=True)

    if isinstance(arbetare, bool) or not isinstance(arbetare, int) or arbetare < 1:
        raise PlaneringsFel(f"arbetare måste vara ett heltal >= 1, fick {arbetare!r}.")

    std_buffert_m, dagsljus_radie_m, kommun_kontext_m, kommun_max_buffert_m, std_buffert_kalla = (
        _berakna_standardbuffert_m(kfg)
    )
    if buffert_m is None:
        eff_buffert_m = std_buffert_m
        buffert_kalla = std_buffert_kalla
    else:
        eff_buffert_m = _validera_positivt_tal(buffert_m, "buffert_m", tillat_noll=True)
        buffert_kalla = f"angiven med --buffert-m ({eff_buffert_m:g} m)"

    karna_poly, indata_epsg, avgr_kalla = _las_avgransning(
        avgransning, avgransning_epsg=avgransning_epsg
    )
    total_poly = karna_poly.buffer(eff_buffert_m) if eff_buffert_m > 0.0 else karna_poly

    karnrutor_obj = _rutor_for_polygon(
        karna_poly,
        rutstorlek_m=eff_rutstorlek_m,
        overlapp_m=eff_overlapp_m,
        upplosning_m=eff_upplosning_m,
    )
    totalrutor_obj = _rutor_for_polygon(
        total_poly,
        rutstorlek_m=eff_rutstorlek_m,
        overlapp_m=eff_overlapp_m,
        upplosning_m=eff_upplosning_m,
    )
    if not karnrutor_obj:
        raise PlaneringsFel("Avgränsningen gav 0 kärnrutor efter rutindelning.")

    karn_namn_set = {r.namn for r in karnrutor_obj}
    total_namn_set = {r.namn for r in totalrutor_obj} | karn_namn_set
    buffert_namn = tuple(sorted(total_namn_set - karn_namn_set))
    karn_namn = tuple(sorted(karn_namn_set))

    antal_karnrutor = len(karn_namn)
    antal_buffertrutor = len(buffert_namn)
    antal_rutor_totalt = antal_karnrutor + antal_buffertrutor

    rader, kolumner = karnrutor_obj[0].form_rc
    celler_per_ruta = rader * kolumner
    celler_totalt = celler_per_ruta * antal_rutor_totalt

    karna_area_m2 = float(karna_poly.area)
    karna_area_km2 = karna_area_m2 / 1_000_000.0
    total_area_m2 = float(total_poly.area)
    total_area_km2 = total_area_m2 / 1_000_000.0

    kb = karna_poly.bounds
    tb = total_poly.bounds
    karna_bbox = (float(kb[0]), float(kb[1]), float(kb[2]), float(kb[3]))
    total_bbox = (float(tb[0]), float(tb[1]), float(tb[2]), float(tb[3]))

    # --- Källbelagda resursuppskattningar (cellantal ∝ (1 / upplosning_m)^2) ---
    skalfaktor_1_0m = celler_per_ruta / REF_CELLER_1_0M
    skalfaktor_0_5m = celler_per_ruta / REF_CELLER_0_5M
    skalfaktor_2_5m = celler_per_ruta / REF_CELLER_2_5M
    skalfaktor_0_25m = celler_per_ruta / REF_CELLER_0_25M

    tid_per_ruta_cpu_s = BASLINJE_CPU_TID_1_0M_S * skalfaktor_1_0m
    kortid_cpu_1_karna_s = tid_per_ruta_cpu_s * antal_rutor_totalt
    effektiva_arbetare_cpu = min(arbetare, antal_rutor_totalt)
    kortid_cpu_s = kortid_cpu_1_karna_s / effektiva_arbetare_cpu
    kortid_cpu_h = kortid_cpu_s / 3600.0

    tid_per_ruta_e2e_ref_s = BASLINJE_E2E_TID_2_5M_S * skalfaktor_2_5m
    kortid_e2e_ref_cpu_s = tid_per_ruta_e2e_ref_s * antal_rutor_totalt

    ram_per_ruta_min_gb = BAS_PROCESS_RAM_GB + DRIFT_RAM_0_25M_MIN_GB * skalfaktor_0_25m
    ram_per_ruta_gb = BAS_PROCESS_RAM_GB + DRIFT_RAM_0_25M_MEDEL_GB * skalfaktor_0_25m
    ram_per_ruta_max_gb = BAS_PROCESS_RAM_GB + DRIFT_RAM_0_25M_MAX_GB * skalfaktor_0_25m
    ram_totalt_gb = ram_per_ruta_gb * effektiva_arbetare_cpu

    max_ram_arbetare_ccx = max(1, int(CCX_RAM_GB // max(ram_per_ruta_gb, 0.1)))
    ccx_effektiva_arbetare = max(1, min(CCX_KARNOR, antal_rutor_totalt, max_ram_arbetare_ccx))
    kortid_ccx_s = kortid_cpu_1_karna_s / ccx_effektiva_arbetare
    kortid_ccx_h = kortid_ccx_s / 3600.0
    ram_totalt_ccx_gb = ram_per_ruta_gb * ccx_effektiva_arbetare

    disk_ra_per_ruta_mb = BASLINJE_DISK_RA_0_5M_MB * skalfaktor_0_5m
    disk_komprimerat_per_ruta_mb = BASLINJE_DISK_KOMPRIMERAT_0_5M_MB * skalfaktor_0_5m
    disk_ra_gb = (disk_ra_per_ruta_mb * antal_rutor_totalt) / 1024.0
    disk_komprimerat_gb = (disk_komprimerat_per_ruta_mb * antal_rutor_totalt) / 1024.0

    # --- Maskinklass & driftplats ---
    moln_batch_tillaten = dataklass == "oppen"
    oppen_s3_tillaten = dataklass == "oppen"
    extern_ai_tillaten = False
    driftplats = "server_eller_moln_batch" if moln_batch_tillaten else "lokal_eller_egen_server"

    if (
        antal_rutor_totalt <= _BASSERVER_MAX_RUTOR
        and kortid_cpu_1_karna_s <= _BASSERVER_MAX_CPU_TID_S
        and ram_per_ruta_gb <= _BASSERVER_MAX_RAM_PER_RUTA_GB
    ):
        maskinklass = "basserver"
        maskinklass_motivering = (
            f"Litet område ({antal_rutor_totalt} rutor, uppskattat {kortid_cpu_1_karna_s:.0f} s på 1 kärna, "
            f"~{ram_per_ruta_gb:.2f} GB RAM/ruta): ryms på basserver eller lokal maskin."
        )
    else:
        maskinklass = "batch"
        if moln_batch_tillaten:
            maskinklass_motivering = (
                f"Större körning ({antal_rutor_totalt} rutor, uppskattat {kortid_cpu_1_karna_s / 3600.0:.2f} CPU-h): "
                f"rekommenderar batchmaskin (Hetzner CCX ~{CCX_KARNOR} kärnor / {CCX_RAM_GB:.0f} GB RAM "
                f"enligt {KALLA_DRIFT}) med resultat till öppen S3-bucket."
            )
        else:
            maskinklass_motivering = (
                f"Större körning ({antal_rutor_totalt} rutor, uppskattat {kortid_cpu_1_karna_s / 3600.0:.2f} CPU-h) "
                f"med stängd data (K38): kräver lokal batchmaskin eller Flodens egen dedikerade server "
                f"(motsvarande CCX {CCX_KARNOR} kärnor / {CCX_RAM_GB:.0f} GB RAM), aldrig publik molnarbetare "
                "eller öppen S3-bucket."
            )

    skalningsformel = (
        "cellantal per ruta = ((rutstorlek_m + 2 * overlapp_m) / upplosning_m)^2 "
        "∝ (1 / upplosning_m)^2; tid, disk och arbetsminne skalas linjärt med cellantalet "
        "mot källbelagda referensrutor (#114, #120, #123, ARKITEKTUR-DRIFT.md) + fast bas-RSS (K37)."
    )

    antaganden = (
        PlaneringsAntagande(
            id="cpu_baslinje_114",
            storhet="CPU-baslinje körtid per 500×500 m-ruta (1,0 m, dagsljus K=64, cpu_f32)",
            varde=BASLINJE_CPU_TID_1_0M_S,
            enhet="s/ruta",
            kalla=KALLA_114,
            skalningsformel=(
                "tid_per_ruta_cpu_s = 4.42 * (celler_per_ruta / 302500) "
                "[= 4.42 * (1.0 / upplosning_m)^2 vid 500+2*25 m]"
            ),
            kommentar="Primär CPU-baslinje före skarp servermätning S6.",
            uppskattning=True,
        ),
        PlaneringsAntagande(
            id="e2e_referens_123",
            storhet="E2E batchreferens (2,5 m, vatten2d + dagsljus, cpu_f64, ZSTD-COG, 8 objekt)",
            varde=BASLINJE_E2E_TID_2_5M_S,
            enhet="s/ruta",
            kalla=KALLA_123,
            skalningsformel=(
                "tid_per_ruta_e2e_ref_s = 1.176 * (celler_per_ruta / 48400) "
                "[= 1.176 * (2.5 / upplosning_m)^2 vid 500+2*25 m]"
            ),
            kommentar=(
                f"Syntetisk 2×2-batch med {BASLINJE_E2E_DISK_2_5M_MB:.3f} MB/ruta vid 2,5 m; "
                "visas som jämförelse mot CPU-baslinjen."
            ),
            uppskattning=True,
        ),
        PlaneringsAntagande(
            id="disk_ra_120",
            storhet="Okomprimerad rutstorlek (0,5 m, 550×550 m, 7 raster + sankgraf)",
            varde=BASLINJE_DISK_RA_0_5M_MB,
            enhet="MB/ruta",
            kalla=KALLA_120,
            skalningsformel=(
                "disk_ra_per_ruta_mb = 28.85 * (celler_per_ruta / 1210000) "
                "[= 28.85 * (0.5 / upplosning_m)^2 vid 500+2*25 m]"
            ),
            kommentar=(
                f"Jämför full K1+K2+K6-kedja i {KALLA_DRIFT}: ca {DRIFT_RA_FULL_0_25M_MB:.0f} MB/ruta "
                "okomprimerat vid 0,25 m."
            ),
            uppskattning=True,
        ),
        PlaneringsAntagande(
            id="disk_zstd_cog_120",
            storhet="Komprimerad ZSTD-COG + Parquet per ruta (0,5 m, 13,1 % av råstorlek)",
            varde=BASLINJE_DISK_KOMPRIMERAT_0_5M_MB,
            enhet="MB/ruta",
            kalla=KALLA_120,
            skalningsformel=(
                "disk_komprimerat_per_ruta_mb = 3.79 * (celler_per_ruta / 1210000) "
                "[= 3.79 * (0.5 / upplosning_m)^2 vid 500+2*25 m]"
            ),
            kommentar=(
                "Motsvarar ~2,81 GB för 760 rutor (Stockholm) vid 0,5 m; kompressionsgrad beror på "
                "terrängkomplexitet (jfr 50–70 GB vid 0,25 m inkl. K2+K6 i docs/ARKITEKTUR-DRIFT.md:272)."
            ),
            uppskattning=True,
        ),
        PlaneringsAntagande(
            id="ram_arbetsminne_drift",
            storhet="Arbetsminne per ruta vid 0,25 m (markmodell + sänkgraf, spann 1–3 GB)",
            varde=DRIFT_RAM_0_25M_MEDEL_GB,
            enhet="GB/ruta",
            kalla=KALLA_DRIFT,
            skalningsformel=(
                "ram_per_ruta_gb = (146.4 / 1024) + 2.0 * (celler_per_ruta / 4840000) "
                "[fast bas-RSS ur K37 + cellproportionellt arbetsminne ∝ (0.25 / upplosning_m)^2]"
            ),
            kommentar="Spann 1,0–3,0 GB/ruta vid 0,25 m (2 200×2 200 celler).",
            uppskattning=True,
        ),
        PlaneringsAntagande(
            id="k37_container_baslinje",
            storhet="K37 containerbaslinje (2 vCPU Xeon, 8 GB RAM): EA8A 1,75 s / 108,9 MB, kvarter300 5,03 s / 58,5 MB",
            varde=K37_DEMO_LITEN_RSS_TOPP_MB,
            enhet="MB",
            kalla=KALLA_K37,
            skalningsformel=(
                "Fast processbasminne 146,4 MB (0,143 GB, demokvarter-liten RSS-topp) per arbetare."
            ),
            kommentar=(
                f"EA8A={K37_EA8A_TID_S:.2f} s ({K37_EA8A_RSS_TOPP_MB:.1f} MB), "
                f"kvarter300={K37_KVARTER300_TID_S:.2f} s ({K37_KVARTER300_RSS_TOPP_MB:.1f} MB), "
                f"demokvarter-liten={K37_DEMO_LITEN_TID_S:.2f} s ({K37_DEMO_LITEN_RSS_TOPP_MB:.1f} MB)."
            ),
            uppskattning=True,
        ),
        PlaneringsAntagande(
            id="ccx_batchmaskin_drift",
            storhet="Batchmaskin (Hetzner CCX: 48 kärnor, 192 GB RAM)",
            varde=CCX_KARNOR,
            enhet="karnor",
            kalla=KALLA_DRIFT,
            skalningsformel=(
                "ccx_effektiva_arbetare = min(48, antal_rutor_totalt, floor(192 / ram_per_ruta_gb)); "
                "kortid_ccx_s = kortid_cpu_1_karna_s / ccx_effektiva_arbetare"
            ),
            kommentar="Ideal parallellskalning utan I/O-overhead; ersätts av S6-servermätning.",
            uppskattning=True,
        ),
        PlaneringsAntagande(
            id="buffert_dimensionering",
            storhet="Buffert runt avgränsningskärnan (dagsljus påverkansradie / kommunens kontext)",
            varde=round(eff_buffert_m, 2),
            enhet="m",
            kalla=buffert_kalla,
            skalningsformel=(
                "buffert_m = ceil(max(hojd_max_m / tan(h_min_grader), kontext_m))"
                if buffert_m is None
                else "användarangiven --buffert-m"
            ),
            kommentar=(
                "Skyfallets faktiska tillrinningsområde beräknas ur höjdmodellen vid mappning "
                + (
                    f"(kan vidgas upp till max_buffert_m = {kommun_max_buffert_m:g} m)."
                    if kommun_max_buffert_m is not None
                    else "."
                )
            ),
            uppskattning=True,
        ),
    )

    status_notis = (
        "UPPSKATTNING (ej S6-servermätning): tid, RAM och disk är skalade ur repots benchmark- och "
        "driftfiler (#114, #120, #123, K37, docs/ARKITEKTUR-DRIFT.md) och ska ersättas av skarp "
        "mätning (S6) innan bindande dimensionering."
    )

    return GeografiPlan(
        uppskattning=True,
        status_notis=status_notis,
        kommun_id=kfg.id,
        kommun_namn=kfg.namn,
        publicering_epsg=kfg.publicering_epsg,
        internt_epsg=EPSG_INTERN,
        indata_epsg=indata_epsg,
        konverterad_till_3011=indata_epsg != EPSG_INTERN,
        dataklass=dataklass,
        stangd_baskarta=stangd_baskarta,
        k38_varning=k38_varning,
        moln_batch_tillaten=moln_batch_tillaten,
        oppen_s3_tillaten=oppen_s3_tillaten,
        extern_ai_tillaten=extern_ai_tillaten,
        dataskyddsnotis=dataskyddsnotis,
        avgransning_kalla=avgr_kalla,
        upplosning_m=eff_upplosning_m,
        rutstorlek_m=eff_rutstorlek_m,
        overlapp_m=eff_overlapp_m,
        buffert_m=eff_buffert_m,
        buffert_kalla=buffert_kalla,
        dagsljus_paverkansradie_m=dagsljus_radie_m,
        kommun_kontext_m=kommun_kontext_m,
        kommun_max_buffert_m=kommun_max_buffert_m,
        karna_bbox_3011_m=karna_bbox,
        total_bbox_3011_m=total_bbox,
        karna_area_m2=karna_area_m2,
        karna_area_km2=karna_area_km2,
        total_area_m2=total_area_m2,
        total_area_km2=total_area_km2,
        antal_karnrutor=antal_karnrutor,
        antal_buffertrutor=antal_buffertrutor,
        antal_rutor_totalt=antal_rutor_totalt,
        celler_per_ruta=celler_per_ruta,
        celler_totalt=celler_totalt,
        karnrutor=karn_namn,
        buffertrutor=buffert_namn,
        skalningsformel=skalningsformel,
        cell_skalfaktor_vs_1_0m=skalfaktor_1_0m,
        cell_skalfaktor_vs_0_5m=skalfaktor_0_5m,
        arbetare=arbetare,
        effektiva_arbetare_cpu=effektiva_arbetare_cpu,
        tid_per_ruta_cpu_s=tid_per_ruta_cpu_s,
        kortid_cpu_1_karna_s=kortid_cpu_1_karna_s,
        kortid_cpu_s=kortid_cpu_s,
        kortid_cpu_h=kortid_cpu_h,
        tid_per_ruta_e2e_ref_s=tid_per_ruta_e2e_ref_s,
        kortid_e2e_ref_cpu_s=kortid_e2e_ref_cpu_s,
        ccx_karnor=CCX_KARNOR,
        ccx_ram_gb=CCX_RAM_GB,
        ccx_effektiva_arbetare=ccx_effektiva_arbetare,
        kortid_ccx_s=kortid_ccx_s,
        kortid_ccx_h=kortid_ccx_h,
        ram_per_ruta_gb=ram_per_ruta_gb,
        ram_per_ruta_min_gb=ram_per_ruta_min_gb,
        ram_per_ruta_max_gb=ram_per_ruta_max_gb,
        ram_totalt_gb=ram_totalt_gb,
        ram_totalt_ccx_gb=ram_totalt_ccx_gb,
        disk_ra_per_ruta_mb=disk_ra_per_ruta_mb,
        disk_komprimerat_per_ruta_mb=disk_komprimerat_per_ruta_mb,
        disk_ra_gb=disk_ra_gb,
        disk_komprimerat_gb=disk_komprimerat_gb,
        maskinklass=maskinklass,
        driftplats=driftplats,
        maskinklass_motivering=maskinklass_motivering,
        antaganden=antaganden,
    )


# ---------------------------------------------------------------------------- tabellformatering


def _formatera_tid(sekunder: float) -> str:
    if sekunder < 60.0:
        return f"{sekunder:.1f} s"
    if sekunder < 3600.0:
        return f"{sekunder / 60.0:.1f} min ({sekunder:.0f} s)"
    return f"{sekunder / 3600.0:.2f} h ({sekunder / 60.0:.0f} min)"


def formatera_plan_tabell(plan: GeografiPlan) -> str:
    """Formaterar en läsbar texttabell för CLI-utskrift (med källbelagda antaganden och K38)."""
    sida_celler = int(round(math.isqrt(plan.celler_per_ruta)))
    ram_spann_txt = f"{plan.ram_per_ruta_min_gb:.2f}-{plan.ram_per_ruta_max_gb:.2f} GB"
    rader: list[str] = [
        f"Geografiplanering: {plan.kommun_namn} ({plan.kommun_id}) [UPPSKATTNING — ej S6-mätning]",
        "=" * 78,
        f"Status:            {plan.status_notis}",
        f"Dataklass (K38):   {plan.dataklass.upper()}"
        + (" [K38-VARNING: STÄNGD BASKARTA]" if plan.k38_varning else ""),
        f"Dataskydd:         {plan.dataskyddsnotis}",
        "",
        "Område och rutnät (SWEREF 99 18 00 / EPSG:3011)",
        "-" * 78,
        f"  Avgränsning:     {plan.avgransning_kalla}",
        f"  Koordinatsystem: indata EPSG:{plan.indata_epsg} -> internt EPSG:{plan.internt_epsg}"
        + (" (konverterad vid inläsning)" if plan.konverterad_till_3011 else " (oförändrad)"),
        f"  Rutnät:          {plan.rutstorlek_m:g}×{plan.rutstorlek_m:g} m + "
        f"{plan.overlapp_m:g} m överlapp, upplösning {plan.upplosning_m:g} m "
        f"({sida_celler}×{sida_celler} = {plan.celler_per_ruta:,} celler/ruta)",
        f"  Buffert:         {plan.buffert_m:.1f} m ({plan.buffert_kalla})",
        f"  Kärnarea:        {plan.karna_area_km2:.3f} km² ({plan.karna_area_m2:,.0f} m²)",
        f"  Total area:      {plan.total_area_km2:.3f} km² ({plan.total_area_m2:,.0f} m², inkl. buffert)",
        f"  Rutor:           {plan.antal_karnrutor} kärnrutor + {plan.antal_buffertrutor} buffertrutor "
        f"= {plan.antal_rutor_totalt} rutor totalt ({plan.celler_totalt:,} celler)",
        "",
        "Resursuppskattning (UPPSKATTNING — källbelagda antaganden)",
        "-" * 78,
        f"  Skalningsformel: {plan.skalningsformel}",
        "",
        f"  {'Storhet':<42} {'Per ruta':>14} {'Totalt':>18}",
        f"  {'-' * 42} {'-' * 14} {'-' * 18}",
        f"  {'Körtid CPU-baslinje (1 kärna, #114)':<42} "
        f"{plan.tid_per_ruta_cpu_s:>12.2f} s {_formatera_tid(plan.kortid_cpu_1_karna_s):>18}",
        f"  {f'Körtid CPU ({plan.effektiva_arbetare_cpu} arbetare, #114)':<42} "
        f"{plan.tid_per_ruta_cpu_s:>12.2f} s {_formatera_tid(plan.kortid_cpu_s):>18}",
        f"  {'Körtid E2E-ref (vatten2d+dagsljus, #123)':<42} "
        f"{plan.tid_per_ruta_e2e_ref_s:>12.2f} s {_formatera_tid(plan.kortid_e2e_ref_cpu_s):>18}",
        f"  {f'Körtid CCX-batch ({plan.ccx_effektiva_arbetare}/{plan.ccx_karnor} kärnor)':<42} "
        f"{plan.tid_per_ruta_cpu_s:>12.2f} s {_formatera_tid(plan.kortid_ccx_s):>18}",
        f"  {'Topp-RAM (arbetsminne + K37 bas-RSS)':<42} "
        f"{plan.ram_per_ruta_gb:>11.2f} GB {plan.ram_totalt_gb:>15.2f} GB",
        f"  {'Topp-RAM på CCX-batch (parallellt)':<42} "
        f"{ram_spann_txt:>14} {plan.ram_totalt_ccx_gb:>15.2f} GB",
        f"  {'Disk rådata (okomprimerat, #120)':<42} "
        f"{plan.disk_ra_per_ruta_mb:>11.2f} MB {plan.disk_ra_gb:>15.2f} GB",
        f"  {'Disk ZSTD-COG + Parquet (#120)':<42} "
        f"{plan.disk_komprimerat_per_ruta_mb:>11.2f} MB {plan.disk_komprimerat_gb:>15.2f} GB",
        "",
        "Rekommenderad maskinklass",
        "-" * 78,
        f"  Maskinklass:     {plan.maskinklass} (driftplats: {plan.driftplats})",
        f"  Motivering:      {plan.maskinklass_motivering}",
        "",
        "Källbelagda antaganden (uppskattning=True)",
        "-" * 78,
    ]
    for a in plan.antaganden:
        rader.append(
            f"  - [{a.id}] {a.storhet}: {a.varde} {a.enhet} "
            f"(källa: {a.kalla}; uppskattning={a.uppskattning})"
        )
        rader.append(f"      Formel: {a.skalningsformel}")
    return "\n".join(rader)
