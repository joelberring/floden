# ruff: noqa: E501
"""K46-lager för trafikdata (`lager.trafik`) samt projektspecifika trafikjusteringar.

Bygger det valfria, 100 % additiva K46-lagret `lager.trafik` under `<geo-rot>/_lager/<sha256>/`:
- `trafik.gpkg`: standardkompatibel, byte-identiskt reproducerbar GeoPackage (LineString i
  SWEREF 99 18 00 / EPSG:3011) med kolumnerna `lank_id`, `gatunamn`, `vagnummer`, `vaghallare`,
  `hastighet_km_h`, `korfalt`, `adt_fordon_dygn`, `andel_tung_pct`, `matar`, `adt_kalla`, `osaker`.
- `trafik.json`: sammanfattning (antal länkar med/utan ÅDT, källbeskrivning, licens, SHA-256 av
  `trafik.gpkg`).
- `oppenhet.toml` (K38): lagrets öppenhet för MCP enligt licens och `villkor_kontrollerade`.

Projektets egna redigeringar (nuläge ÅDT, prognos-ÅDT, hastighet, tung andel) sparas separat och
atomiskt i `<projekt_mapp>/trafik_justeringar.json` och skriver aldrig över geografins rådata.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import struct
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import shapely
from shapely.geometry import LineString, box

from data_inlasning.trafikverket import TrafikLank
from gemensamt.hash import sha256_fil
from gemensamt.koordinater import EPSG_INTERN
from geografi.modell import (
    KONTRAKT_GEOGRAFI,
    MANIFEST_NAMN,
    GeografiFel,
    lager_mapp,
    lagra,
    las_manifest,
    trad_filer,
    trad_hash,
    versionsmapp,
)
from kommuner.skapa import skriv_oppenhet_toml

KONTRAKT_TRAFIK_LAGER: Final[str] = "floden-trafik-lager/v1"
KONTRAKT_PROJEKT_TRAFIK_JUSTERINGAR: Final[str] = "floden-projekt-trafik-justeringar/v1"
LAGER_TRAFIK_TABELL: Final[str] = "trafik"
TRAFIK_GPKG_FIL: Final[str] = "trafik.gpkg"
TRAFIK_JSON_FIL: Final[str] = "trafik.json"
PROJEKT_JUSTERINGAR_FIL: Final[str] = "trafik_justeringar.json"
STANDARD_EGEN_KALLA: Final[str] = "Egen uppgift"

_GPKG_TIDSSTAMPEL: Final[str] = "2026-01-01T00:00:00.000Z"  # fast för byte-identisk GeoPackage
_MIN_LINJELANGD_M: Final[float] = 1.0e-3
_KANDA_OPPNA_LICENSER: Final[frozenset[str]] = frozenset(
    {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "ODbL-1.0", "ODC-By-1.0", "PDDL-1.0"}
)
_WKT_EPSG_3011: Final[str] = (
    'PROJCS["SWEREF99 18 00",GEOGCS["SWEREF99",DATUM["SWEREF99",'
    'SPHEROID["GRS 1980",6378137,298.257222101]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",18],'
    'PARAMETER["scale_factor",1],PARAMETER["false_easting",150000],'
    'PARAMETER["false_northing",0],UNIT["metre",1],AUTHORITY["EPSG","3011"]]'
)

Rektangel = tuple[float, float, float, float]


@dataclass(frozen=True)
class GeografiTrafikLager:
    """SHA-256-verifierat `lager.trafik` utläst ur ett K46-geografimanifest."""

    ref: str
    manifest_sha256: str
    trafik_sha256: str
    gpkg_sha256: str
    kalla: str
    licens: str
    lankar: tuple[TrafikLank, ...]
    sammanfattning: dict[str, Any]
    klippt_utsnitt: Rektangel | None


@dataclass(frozen=True)
class ProjektTrafikJustering:
    """En användarredigerad trafikuppgift sparad i ett projekt (utan att ändra geografins rådata)."""

    lank_id: str
    adt_fordon_dygn: float | None = None
    prognos_adt_fordon_dygn: float | None = None
    hastighet_km_h: float | None = None
    andel_tung_pct: float | None = None
    kalla: str = STANDARD_EGEN_KALLA
    kommentar: str | None = None

    def som_dict(self) -> dict[str, Any]:
        ut: dict[str, Any] = {
            "lank_id": self.lank_id,
            "adt_fordon_dygn": self.adt_fordon_dygn,
            "prognos_adt_fordon_dygn": self.prognos_adt_fordon_dygn,
            "hastighet_km_h": self.hastighet_km_h,
            "andel_tung_pct": self.andel_tung_pct,
            "kalla": self.kalla,
        }
        if self.kommentar is not None:
            ut["kommentar"] = self.kommentar
        return ut


# ------------------------------------------------------------------ Deterministisk GeoPackage I/O


def _gpkg_linestring_blob(pts: tuple[tuple[float, float], ...]) -> bytes:
    """Kodar en LineString som OGC GeoPackageBinary (little-endian, envelope XY, EPSG:3011)."""
    linje = LineString(pts)
    wkb = bytes(shapely.to_wkb(linje, byte_order=1))
    e0, n0, e1, n1 = linje.bounds
    # Flaggor: byte_order=1 (little-endian), envelope=1 (XY, 32 byte) -> 0b00000011 = 0x03
    header = struct.pack("<2sBBi4d", b"GP", 0, 0x03, EPSG_INTERN, e0, e1, n0, n1)
    return header + wkb


def _avkoda_gpkg_linestring(blob: bytes) -> tuple[tuple[float, float], ...]:
    if len(blob) < 8 or blob[:2] != b"GP":
        raise GeografiFel("Ogiltig GeoPackageBinary-geometri i trafik.gpkg (saknar 'GP'-huvud).")
    flaggor = blob[3]
    env_typ = (flaggor >> 1) & 0x07
    env_bytes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env_typ)
    if env_bytes is None:
        raise GeografiFel(f"Okänd envelope-kod {env_typ} i trafik.gpkg.")
    geom = shapely.from_wkb(blob[8 + env_bytes :])
    if geom is None or geom.is_empty or geom.geom_type != "LineString":
        raise GeografiFel(
            f"Väglänk i trafik.gpkg måste vara LineString, fick {getattr(geom, 'geom_type', None)!r}."
        )
    pts = tuple((float(c[0]), float(c[1])) for c in geom.coords)
    if len(pts) < 2:
        raise GeografiFel("Väglänk i trafik.gpkg har färre än 2 koordinater.")
    return pts


def _skriv_trafik_gpkg(
    fil: Path,
    lankar: Sequence[TrafikLank],
    bbox_fallback: Rektangel,
) -> Path:
    """Skriver en byte-identisk OGC GeoPackage (`trafik.gpkg`, lager `trafik`, EPSG:3011)."""
    fil.parent.mkdir(parents=True, exist_ok=True)
    if fil.exists():
        fil.unlink()

    sorterade = sorted(lankar, key=lambda lk: (lk.lank_id, lk.koordinater_3011))
    if sorterade:
        xs = [e for lk in sorterade for e, _ in lk.koordinater_3011]
        ys = [n for lk in sorterade for _, n in lk.koordinater_3011]
        min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
    else:
        min_x, min_y, max_x, max_y = bbox_fallback

    conn = sqlite3.connect(str(fil))
    try:
        # OGC GeoPackage 1.2/1.3 application_id & user_version (krävs av GDAL/pyogrio/QGIS)
        conn.execute("PRAGMA application_id = 1196444487;")  # 'GPKG'
        conn.execute("PRAGMA user_version = 10300;")
        conn.executescript(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            );
            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT UNIQUE,
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT '2026-01-01T00:00:00.000Z',
                min_x DOUBLE,
                min_y DOUBLE,
                max_x DOUBLE,
                max_y DOUBLE,
                srs_id INTEGER,
                CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                    REFERENCES gpkg_spatial_ref_sys(srs_id)
            );
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL,
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
                CONSTRAINT uk_gc_table_name UNIQUE (table_name),
                CONSTRAINT fk_gc_tn FOREIGN KEY (table_name)
                    REFERENCES gpkg_contents(table_name),
                CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id)
                    REFERENCES gpkg_spatial_ref_sys(srs_id)
            );
            CREATE TABLE trafik (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB NOT NULL,
                lank_id TEXT NOT NULL,
                gatunamn TEXT,
                vagnummer TEXT,
                vaghallare TEXT,
                hastighet_km_h REAL,
                korfalt INTEGER,
                adt_fordon_dygn REAL,
                andel_tung_pct REAL,
                matar INTEGER,
                adt_kalla TEXT,
                osaker INTEGER NOT NULL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO gpkg_spatial_ref_sys
                (srs_name, srs_id, organization, organization_coordsys_id, definition, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "undefined cartesian"),
                ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "undefined geographic"),
                (
                    "SWEREF99 18 00",
                    EPSG_INTERN,
                    "EPSG",
                    EPSG_INTERN,
                    _WKT_EPSG_3011,
                    "SWEREF 99 18 00",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change,
                 min_x, min_y, max_x, max_y, srs_id)
            VALUES (?, 'features', ?, 'Floden K46 lager.trafik (EPSG:3011)', ?, ?, ?, ?, ?, ?)
            """,
            (
                LAGER_TRAFIK_TABELL,
                LAGER_TRAFIK_TABELL,
                _GPKG_TIDSSTAMPEL,
                float(min_x),
                float(min_y),
                float(max_x),
                float(max_y),
                EPSG_INTERN,
            ),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns
                (table_name, column_name, geometry_type_name, srs_id, z, m)
            VALUES (?, 'geom', 'LINESTRING', ?, 0, 0)
            """,
            (LAGER_TRAFIK_TABELL, EPSG_INTERN),
        )
        for lk in sorterade:
            conn.execute(
                """
                INSERT INTO trafik (
                    geom, lank_id, gatunamn, vagnummer, vaghallare,
                    hastighet_km_h, korfalt, adt_fordon_dygn, andel_tung_pct,
                    matar, adt_kalla, osaker
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _gpkg_linestring_blob(lk.koordinater_3011),
                    lk.lank_id,
                    lk.gatunamn,
                    lk.vagnummer,
                    lk.vaghallare,
                    lk.hastighet_km_h,
                    lk.korfalt,
                    lk.adt_fordon_dygn,
                    lk.andel_tung_pct,
                    lk.matar,
                    lk.adt_kalla,
                    1 if lk.osaker else 0,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return fil


def _las_trafik_gpkg(fil: Path) -> list[TrafikLank]:
    if not fil.is_file():
        raise GeografiFel(f"Saknar {fil.name} i trafiklager.")
    conn = sqlite3.connect(f"file:{fil}?mode=ro", uri=True)
    try:
        srs_rad = conn.execute(
            "SELECT srs_id FROM gpkg_geometry_columns WHERE table_name = ?",
            (LAGER_TRAFIK_TABELL,),
        ).fetchone()
        if srs_rad is None:
            raise GeografiFel(f"{fil.name} saknar lagret '{LAGER_TRAFIK_TABELL}'.")
        if int(srs_rad[0]) != EPSG_INTERN:
            raise GeografiFel(
                f"{fil.name} har srs_id={srs_rad[0]}, förväntade EPSG:{EPSG_INTERN}."
            )
        rader = conn.execute(
            """
            SELECT geom, lank_id, gatunamn, vagnummer, vaghallare,
                   hastighet_km_h, korfalt, adt_fordon_dygn, andel_tung_pct,
                   matar, adt_kalla, osaker
            FROM trafik
            ORDER BY lank_id, fid
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise GeografiFel(f"Kunde inte läsa {fil.name}: {exc}") from exc
    finally:
        conn.close()

    ut: list[TrafikLank] = []
    for (
        geom_blob,
        lank_id,
        gatunamn,
        vagnummer,
        vaghallare,
        hast,
        korfalt,
        adt,
        tung_pct,
        matar,
        adt_kalla,
        osaker,
    ) in rader:
        pts = _avkoda_gpkg_linestring(bytes(geom_blob))
        ut.append(
            TrafikLank(
                lank_id=str(lank_id),
                koordinater_3011=pts,
                gatunamn=None if gatunamn is None else str(gatunamn),
                vagnummer=None if vagnummer is None else str(vagnummer),
                vaghallare=None if vaghallare is None else str(vaghallare),
                hastighet_km_h=None if hast is None else float(hast),
                korfalt=None if korfalt is None else int(korfalt),
                adt_fordon_dygn=None if adt is None else float(adt),
                andel_tung_pct=None if tung_pct is None else float(tung_pct),
                matar=None if matar is None else int(matar),
                adt_kalla=None if adt_kalla is None else str(adt_kalla),
                osaker=bool(osaker),
            )
        )
    return ut


def _validera_rektangel(r: Rektangel, namn: str) -> Rektangel:
    if len(r) != 4:
        raise GeografiFel(f"{namn} måste ha 4 tal (e_min, n_min, e_max, n_max).")
    e0, n0, e1, n1 = (float(v) for v in r)
    if not all(math.isfinite(v) for v in (e0, n0, e1, n1)) or e0 >= e1 or n0 >= n1:
        raise GeografiFel(f"Ogiltig {namn}: {r!r}.")
    return (e0, n0, e1, n1)


def _klipp_lankar_till_bbox(
    lankar: Sequence[TrafikLank],
    bbox_3011: Rektangel,
) -> list[TrafikLank]:
    klippbox = box(*_validera_rektangel(bbox_3011, "bbox_3011"))
    ut: list[TrafikLank] = []
    for lk in lankar:
        klippt = LineString(lk.koordinater_3011).intersection(klippbox)
        if klippt.is_empty:
            continue
        delar = [
            tuple((float(c[0]), float(c[1])) for c in g.coords)
            for g in shapely.get_parts(klippt)
            if isinstance(g, LineString) and float(g.length) > _MIN_LINJELANGD_M
        ]
        for del_nr, pts in enumerate(delar):
            lid = lk.lank_id if len(delar) == 1 else f"{lk.lank_id}#{del_nr}"
            ut.append(
                TrafikLank(
                    lank_id=lid,
                    koordinater_3011=pts,
                    gatunamn=lk.gatunamn,
                    vagnummer=lk.vagnummer,
                    vaghallare=lk.vaghallare,
                    hastighet_km_h=lk.hastighet_km_h,
                    korfalt=lk.korfalt,
                    adt_fordon_dygn=lk.adt_fordon_dygn,
                    andel_tung_pct=lk.andel_tung_pct,
                    matar=lk.matar,
                    adt_kalla=lk.adt_kalla,
                    osaker=lk.osaker,
                )
            )
    ut.sort(key=lambda lk: (lk.lank_id, lk.koordinater_3011))
    return ut


# ------------------------------------------------------------------ 1. Bygg & läs K46 lager.trafik


def bygg_trafik_lager(
    geo_rot: Path,
    bygge: Path,
    karna_3011: Rektangel,
    *,
    lankar: Sequence[TrafikLank],
    kalla_beskrivning: str,
    licens: str,
    villkor_kontrollerade: bool = False,
    buffert_m: float = 0.0,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Bygger och innehållsadresserar K46-lagret `lager.trafik` + K38 `oppenhet.toml`.

    Skriver `trafik.gpkg` (EPSG:3011 LineString) och `trafik.json` (sammanfattning + SHA-256),
    lagrar via `geografi.modell.lagra` och returnerar `(lager_post, kalla_post, varningar)`.
    """
    e0, n0, e1, n1 = _validera_rektangel(karna_3011, "karna_3011")
    if not math.isfinite(buffert_m) or buffert_m < 0.0:
        raise GeografiFel(f"buffert_m måste vara >= 0, fick {buffert_m!r}.")
    if not isinstance(kalla_beskrivning, str) or not kalla_beskrivning.strip():
        raise GeografiFel("kalla_beskrivning får inte vara tom.")
    if not isinstance(licens, str) or not licens.strip():
        raise GeografiFel("licens får inte vara tom.")

    ren_kalla = kalla_beskrivning.strip()
    ren_licens = licens.strip()
    utsnitt: Rektangel = (e0 - buffert_m, n0 - buffert_m, e1 + buffert_m, n1 + buffert_m)
    klippta = _klipp_lankar_till_bbox(lankar, utsnitt)

    mapp = Path(bygge) / LAGER_TRAFIK_TABELL
    if mapp.exists():
        shutil.rmtree(mapp)
    mapp.mkdir(parents=True, exist_ok=True)

    gpkg_fil = _skriv_trafik_gpkg(mapp / TRAFIK_GPKG_FIL, klippta, utsnitt)
    gpkg_sha = sha256_fil(gpkg_fil)

    antal_med_adt = sum(1 for lk in klippta if lk.adt_fordon_dygn is not None)
    antal_utan_adt = len(klippta) - antal_med_adt
    antal_osakra = sum(1 for lk in klippta if lk.osaker)

    sammanfattning: dict[str, Any] = {
        "kontrakt": KONTRAKT_TRAFIK_LAGER,
        "koordinatsystem": f"EPSG:{EPSG_INTERN}",
        "karna_3011": [e0, n0, e1, n1],
        "buffert_m": float(buffert_m),
        "utsnitt_3011": [utsnitt[0], utsnitt[1], utsnitt[2], utsnitt[3]],
        "kalla": ren_kalla,
        "licens": ren_licens,
        "antal_lankar": len(klippta),
        "antal_med_adt": antal_med_adt,
        "antal_utan_adt": antal_utan_adt,
        "antal_osakra": antal_osakra,
        "trafik_gpkg_sha256": gpkg_sha,
    }
    (mapp / TRAFIK_JSON_FIL).write_text(
        json.dumps(sammanfattning, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    lg = lagra(Path(geo_rot), mapp)

    oppen = bool(villkor_kontrollerade) and ren_licens in _KANDA_OPPNA_LICENSER
    if ren_licens == "intern":
        oppen = False
        anledning = "Trafikdata med licens 'intern' är aldrig öppna för MCP."
    elif not villkor_kontrollerade:
        anledning = (
            "Trafiklagrets villkor är inte kontrollerade av Floden: stängt tills "
            "--villkor-kontrollerade."
        )
    elif ren_licens not in _KANDA_OPPNA_LICENSER:
        anledning = (
            f"Licensen '{ren_licens}' ingår inte i Flodens kända öppna SPDX-licenser: "
            "stängt för MCP."
        )
    else:
        anledning = f"Öppen licens ({ren_licens}) och villkor kontrollerade (--villkor-kontrollerade)."

    skriv_oppenhet_toml(
        lg.mapp,
        oppen=oppen,
        licens=ren_licens,
        kalla=ren_kalla,
        anledning=anledning,
    )

    varningar: list[str] = []
    if not klippta:
        varningar.append("Trafik: inga väglänkar inom kärnan plus bufferten; lagret är tomt.")
    elif antal_utan_adt > 0:
        varningar.append(
            f"Trafik: {antal_utan_adt} av {len(klippta)} väglänkar saknar ÅDT "
            "(lämnas som null — trafik hittas aldrig på)."
        )

    lager_post: dict[str, Any] = {
        "sha256": lg.sha256,
        "buffert_m": float(buffert_m),
        "antal_lankar": len(klippta),
        "antal_med_adt": antal_med_adt,
        "antal_utan_adt": antal_utan_adt,
        "trafik_gpkg_sha256": gpkg_sha,
        "filer": [{"fil": f, "sha256": s} for f, s in lg.filer],
    }
    kalla_post: dict[str, Any] = {
        "kalla": ren_kalla,
        "licens": ren_licens,
    }
    return lager_post, kalla_post, varningar


def _geografins_manifest(manifest_fil: Path) -> tuple[dict[str, Any], str, Path]:
    fil = Path(manifest_fil)
    if not fil.is_file():
        raise GeografiFel(f"Geografins manifest {fil.name} finns inte.")
    try:
        d = json.loads(fil.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GeografiFel(f"Manifestet {fil.name} går inte att läsa.") from exc
    if not isinstance(d, dict) or d.get("kontrakt") != KONTRAKT_GEOGRAFI:
        raise GeografiFel(f"{fil.name} är inte ett geografimanifest ({KONTRAKT_GEOGRAFI}).")
    geo_rot = fil.resolve().parents[2]
    ref = f"{d.get('id')}@{d.get('version')}"
    if (
        versionsmapp(geo_rot, str(d.get("id")), str(d.get("version"))).resolve()
        != fil.resolve().parent
    ):
        raise GeografiFel(
            f"Manifestet ligger inte på {MANIFEST_NAMN}s plats "
            f"<geo-rot>/{d.get('id')}/{d.get('version')}/."
        )
    manifest, sha = las_manifest(geo_rot, ref, verifiera=True)
    return manifest, sha, geo_rot


def las_trafik_lager(
    manifest_fil: str | Path,
    bbox_3011: Rektangel | None = None,
) -> GeografiTrafikLager:
    """Verifierar SHA-256 och läser `lager.trafik` ur ett K46-manifest, valfritt klippt till `bbox_3011`."""
    manifest, manifest_sha, geo_rot = _geografins_manifest(Path(manifest_fil))
    ref = f"{manifest['id']}@{manifest['version']}"
    lager = manifest.get("lager", {})
    if "trafik" not in lager:
        raise GeografiFel(
            f"Geografin {ref} saknar trafiklager (lager.trafik). "
            "Mappa geografin med --trafik-fil eller --trafikverket-api."
        )
    trafik_post = lager["trafik"]
    forvantad_sha = str(trafik_post["sha256"])
    mapp = lager_mapp(geo_rot, forvantad_sha)
    faktisk_sha = trad_hash(trad_filer(mapp))
    if faktisk_sha != forvantad_sha:
        raise GeografiFel(
            f"Lagret 'trafik' i {ref} har ändrats efter mappningen (sha256 stämmer inte)."
        )

    json_fil = mapp / TRAFIK_JSON_FIL
    gpkg_fil = mapp / TRAFIK_GPKG_FIL
    if not json_fil.is_file() or not gpkg_fil.is_file():
        raise GeografiFel(f"Lagret 'trafik' i {ref} saknar {TRAFIK_GPKG_FIL} eller {TRAFIK_JSON_FIL}.")
    try:
        sammanfattning = json.loads(json_fil.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GeografiFel(f"Kunde inte läsa {TRAFIK_JSON_FIL} i {ref}: {exc}") from exc
    if not isinstance(sammanfattning, dict):
        raise GeografiFel(f"{TRAFIK_JSON_FIL} i {ref} är inte ett JSON-objekt.")

    faktisk_gpkg_sha = sha256_fil(gpkg_fil)
    forvantad_gpkg_sha = str(sammanfattning.get("trafik_gpkg_sha256") or "")
    if forvantad_gpkg_sha and faktisk_gpkg_sha != forvantad_gpkg_sha:
        raise GeografiFel(
            f"{TRAFIK_GPKG_FIL} i {ref} matchar inte kontrollsumman i {TRAFIK_JSON_FIL}."
        )

    lankar = _las_trafik_gpkg(gpkg_fil)
    klippt_utsnitt: Rektangel | None = None
    if bbox_3011 is not None:
        klippt_utsnitt = _validera_rektangel(bbox_3011, "bbox_3011")
        lankar = _klipp_lankar_till_bbox(lankar, klippt_utsnitt)

    kalla_meta = manifest.get("kallor", {}).get("trafik", {})
    kalla = str(sammanfattning.get("kalla") or kalla_meta.get("kalla") or "Okänd trafikkälla")
    licens = str(sammanfattning.get("licens") or kalla_meta.get("licens") or "se källan")

    return GeografiTrafikLager(
        ref=ref,
        manifest_sha256=manifest_sha,
        trafik_sha256=forvantad_sha,
        gpkg_sha256=faktisk_gpkg_sha,
        kalla=kalla,
        licens=licens,
        lankar=tuple(lankar),
        sammanfattning=sammanfattning,
        klippt_utsnitt=klippt_utsnitt,
    )


# ------------------------------------------------------------------ 2. Projektets egna trafikjusteringar


def _validera_valfritt_tal(
    raw: Any,
    namn: str,
    *,
    minst: float | None = None,
    over: float | None = None,
    hogst: float | None = None,
) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise GeografiFel(f"Trafikjustering: '{namn}' måste vara ett ändligt tal, fick {raw!r}.")
    v = float(raw)
    if not math.isfinite(v):
        raise GeografiFel(f"Trafikjustering: '{namn}' måste vara ett ändligt tal, fick {raw!r}.")
    if minst is not None and v < minst:
        raise GeografiFel(f"Trafikjustering: '{namn}' måste vara >= {minst:g}, fick {v:g}.")
    if over is not None and v <= over:
        raise GeografiFel(f"Trafikjustering: '{namn}' måste vara > {over:g}, fick {v:g}.")
    if hogst is not None and v > hogst:
        raise GeografiFel(f"Trafikjustering: '{namn}' måste vara <= {hogst:g}, fick {v:g}.")
    return v


def validera_trafik_justering(
    post: Mapping[str, Any],
    *,
    fallback_lank_id: str | None = None,
) -> ProjektTrafikJustering:
    """Validerar en enskild projektjustering för en väglänk."""
    if not isinstance(post, Mapping):
        raise GeografiFel("Varje trafikjustering måste vara ett JSON-objekt.")
    lid_ra = post.get("lank_id", fallback_lank_id)
    if not isinstance(lid_ra, str) or not lid_ra.strip() or len(lid_ra.strip()) > 200:
        raise GeografiFel("Trafikjustering saknar giltigt 'lank_id'.")
    lid = lid_ra.strip()

    adt = _validera_valfritt_tal(post.get("adt_fordon_dygn"), "adt_fordon_dygn", minst=0.0)
    prognos = _validera_valfritt_tal(
        post.get("prognos_adt_fordon_dygn"), "prognos_adt_fordon_dygn", minst=0.0
    )
    hast = _validera_valfritt_tal(post.get("hastighet_km_h"), "hastighet_km_h", over=0.0)
    tung = _validera_valfritt_tal(
        post.get("andel_tung_pct"), "andel_tung_pct", minst=0.0, hogst=100.0
    )

    kalla_ra = post.get("kalla", STANDARD_EGEN_KALLA)
    if kalla_ra is None or (isinstance(kalla_ra, str) and not kalla_ra.strip()):
        kalla = STANDARD_EGEN_KALLA
    elif isinstance(kalla_ra, str):
        kalla = kalla_ra.strip()[:120]
    else:
        raise GeografiFel("Trafikjustering: 'kalla' måste vara en textsträng.")

    komm_ra = post.get("kommentar")
    kommentar: str | None = None
    if komm_ra is not None and komm_ra != "":
        if not isinstance(komm_ra, str):
            raise GeografiFel("Trafikjustering: 'kommentar' måste vara en textsträng.")
        kommentar = komm_ra.strip()[:300] or None

    return ProjektTrafikJustering(
        lank_id=lid,
        adt_fordon_dygn=adt,
        prognos_adt_fordon_dygn=prognos,
        hastighet_km_h=hast,
        andel_tung_pct=tung,
        kalla=kalla,
        kommentar=kommentar,
    )


def _skriv_atomiskt_text(mal: Path, text: str) -> None:
    mal.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_namn = tempfile.mkstemp(prefix=f".{mal.name}.", suffix=".tmp", dir=mal.parent)
    tmp = Path(tmp_namn)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, mal)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def las_projekt_trafik_justeringar(
    projekt_mapp: str | Path,
) -> dict[str, ProjektTrafikJustering]:
    """Läser projektets egna trafikjusteringar ur `<projekt_mapp>/trafik_justeringar.json`."""
    fil = Path(projekt_mapp) / PROJEKT_JUSTERINGAR_FIL
    if not fil.is_file():
        return {}
    try:
        data = json.loads(fil.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GeografiFel(f"Kunde inte läsa {PROJEKT_JUSTERINGAR_FIL}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise GeografiFel(f"{PROJEKT_JUSTERINGAR_FIL} måste vara ett JSON-objekt.")
    poster_ra = data.get("justeringar", data.get("poster", []))
    ut: dict[str, ProjektTrafikJustering] = {}
    if isinstance(poster_ra, Mapping):
        for lid, obj in poster_ra.items():
            just = validera_trafik_justering(obj, fallback_lank_id=str(lid))
            ut[just.lank_id] = just
    elif isinstance(poster_ra, list):
        for obj in poster_ra:
            just = validera_trafik_justering(obj)
            ut[just.lank_id] = just
    else:
        raise GeografiFel(f"{PROJEKT_JUSTERINGAR_FIL} har ogiltigt fält 'justeringar'.")
    return ut


def spara_projekt_trafik_justeringar(
    projekt_mapp: str | Path,
    poster: Sequence[Mapping[str, Any] | ProjektTrafikJustering]
    | Mapping[str, Mapping[str, Any] | ProjektTrafikJustering],
) -> Path:
    """Validerar och sparar projektets egna trafikjusteringar atomiskt i projektmappen.

    Skriver aldrig över eller rör geografins rådata i `_lager/<sha256>/`.
    """
    mapp = Path(projekt_mapp)
    validerade: dict[str, ProjektTrafikJustering] = {}

    if isinstance(poster, Mapping):
        for lid, obj in poster.items():
            if isinstance(obj, ProjektTrafikJustering):
                validerade[obj.lank_id] = obj
            else:
                just = validera_trafik_justering(obj, fallback_lank_id=str(lid))
                validerade[just.lank_id] = just
    else:
        for obj in poster:
            if isinstance(obj, ProjektTrafikJustering):
                validerade[obj.lank_id] = obj
            else:
                just = validera_trafik_justering(obj)
                validerade[just.lank_id] = just

    sorterade = [validerade[k].som_dict() for k in sorted(validerade)]
    dokument = {
        "kontrakt": KONTRAKT_PROJEKT_TRAFIK_JUSTERINGAR,
        "koordinatsystem": f"EPSG:{EPSG_INTERN}",
        "justeringar": sorterade,
    }
    fil = mapp / PROJEKT_JUSTERINGAR_FIL
    _skriv_atomiskt_text(
        fil,
        json.dumps(dokument, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return fil


__all__ = [
    "KONTRAKT_PROJEKT_TRAFIK_JUSTERINGAR",
    "KONTRAKT_TRAFIK_LAGER",
    "LAGER_TRAFIK_TABELL",
    "PROJEKT_JUSTERINGAR_FIL",
    "STANDARD_EGEN_KALLA",
    "TRAFIK_GPKG_FIL",
    "TRAFIK_JSON_FIL",
    "GeografiTrafikLager",
    "ProjektTrafikJustering",
    "bygg_trafik_lager",
    "las_projekt_trafik_justeringar",
    "las_trafik_lager",
    "spara_projekt_trafik_justeringar",
    "validera_trafik_justering",
]
