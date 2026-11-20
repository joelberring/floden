"""Export av ruttresultat enligt kontrakt K8/v1 och K0/v1 (`docs/DATAKONTRAKT.md`)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from rutt.dijkstra import InsatstidPost
from rutt.entreer import EntreNabarhetPost
from rutt.kritiska import KritisktAvsnitt
from testsvit.k1_io import EPSG_SWEREF99_1800, WKT_EPSG_3011, WKT_EPSG_4326

KONTRAKT_K8 = "K8/v1"
HAR_PYARROW: bool = importlib.util.find_spec("pyarrow") is not None


class RuttExportFel(Exception):
    """Fel vid skrivning av K8-ruttresultat."""


def _packa_gp_wkb_linestring(
    koordinater: Sequence[tuple[float, float]],
    srs_id: int = EPSG_SWEREF99_1800,
) -> bytes:
    if len(koordinater) < 2:
        raise RuttExportFel("LineString i kritiska_avsnitt måste ha minst 2 punkter.")
    gp_header = struct.pack("<2sBBi", b"GP", 0, 1, srs_id)
    buf = bytearray(gp_header)
    buf.extend(struct.pack("<BII", 1, 2, len(koordinater)))
    for x, y in koordinater:
        buf.extend(struct.pack("<dd", float(x), float(y)))
    return bytes(buf)


def skriv_kritiska_avsnitt_gpkg(
    sokvag: str | Path,
    kritiska_avsnitt: Sequence[KritisktAvsnitt],
    *,
    bbox_fallback: tuple[float, float, float, float] | None = None,
) -> Path:
    """Skriver `kritiska_avsnitt.gpkg` (OGC GeoPackage 1.2 i EPSG:3011)."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    xs = [float(x) for av in kritiska_avsnitt for x, _ in av.koordinater]
    ys = [float(y) for av in kritiska_avsnitt for _, y in av.koordinater]
    if xs:
        min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
    elif bbox_fallback is not None:
        min_x, min_y, max_x, max_y = bbox_fallback
    else:
        min_x, min_y, max_x, max_y = (0.0, 0.0, 0.0, 0.0)

    conn = sqlite3.connect(str(mal))
    try:
        conn.execute("PRAGMA application_id = 1196444487")
        conn.execute("PRAGMA user_version = 10200")
        conn.execute(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT UNIQUE,
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                min_x DOUBLE,
                min_y DOUBLE,
                max_x DOUBLE,
                max_y DOUBLE,
                srs_id INTEGER,
                CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                    REFERENCES gpkg_spatial_ref_sys(srs_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL,
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
                CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
                CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO gpkg_spatial_ref_sys (
                srs_name, srs_id, organization, organization_coordsys_id, definition, description
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "Undefined cartesian"),
                ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "Undefined geographic"),
                ("WGS 84 geodetic", 4326, "EPSG", 4326, WKT_EPSG_4326, "WGS 84"),
                (
                    "SWEREF99 18 00",
                    EPSG_SWEREF99_1800,
                    "EPSG",
                    EPSG_SWEREF99_1800,
                    WKT_EPSG_3011,
                    "SWEREF 99 18 00",
                ),
            ],
        )
        conn.execute(
            """
            CREATE TABLE kritiska_avsnitt (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                kant_id TEXT NOT NULL,
                vinst INTEGER NOT NULL,
                djup_m REAL NOT NULL,
                normal_hastighet_km_h REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents (
                table_name, data_type, identifier, description,
                last_change, min_x, min_y, max_x, max_y, srs_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "kritiska_avsnitt",
                "features",
                "kritiska_avsnitt",
                "Kritiska gatuavsnitt enligt Floden K8/v1",
                "2026-11-20T00:00:00.000Z",
                min_x,
                min_y,
                max_x,
                max_y,
                EPSG_SWEREF99_1800,
            ),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns (
                table_name, column_name, geometry_type_name, srs_id, z, m
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("kritiska_avsnitt", "geom", "LINESTRING", EPSG_SWEREF99_1800, 0, 0),
        )
        for avsnitt in kritiska_avsnitt:
            wkb = _packa_gp_wkb_linestring(avsnitt.koordinater, EPSG_SWEREF99_1800)
            conn.execute(
                """
                INSERT INTO kritiska_avsnitt (
                    geom, kant_id, vinst, djup_m, normal_hastighet_km_h
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sqlite3.Binary(wkb),
                    avsnitt.kant_id,
                    int(avsnitt.vinst),
                    float(avsnitt.djup_m),
                    float(avsnitt.normal_hastighet_km_h),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return mal


def _skriv_insatstider_parquet(
    insatstider: Sequence[InsatstidPost],
    sokvag: Path,
) -> Path:
    pa: Any = importlib.import_module("pyarrow")
    pq: Any = importlib.import_module("pyarrow.parquet")

    schema = pa.schema(
        [
            ("adress_id", pa.string()),
            ("station_id", pa.string()),
            ("t_s", pa.float64()),
            ("insatstid_s", pa.float64()),
            ("torr_insatstid_s", pa.float64()),
        ]
    )
    data: dict[str, list[Any]] = {
        "adress_id": [p.adress_id for p in insatstider],
        "station_id": [p.station_id for p in insatstider],
        "t_s": [float(p.t_s) for p in insatstider],
        "insatstid_s": [float(p.insatstid_s) for p in insatstider],
        "torr_insatstid_s": [float(p.torr_insatstid_s) for p in insatstider],
    }
    tabell = pa.table(data, schema=schema)
    sokvag.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tabell, str(sokvag))
    return sokvag


def _skriv_entreer_parquet(
    entreer: Sequence[EntreNabarhetPost],
    sokvag: Path,
) -> Path:
    pa: Any = importlib.import_module("pyarrow")
    pq: Any = importlib.import_module("pyarrow.parquet")

    schema = pa.schema(
        [
            ("entre_id", pa.string()),
            ("t_s", pa.float64()),
            ("status", pa.string()),
            ("gangvag_m", pa.float64()),
        ]
    )
    data: dict[str, list[Any]] = {
        "entre_id": [p.entre_id for p in entreer],
        "t_s": [float(p.t_s) for p in entreer],
        "status": [p.status for p in entreer],
        "gangvag_m": [float(p.gangvag_m) for p in entreer],
    }
    tabell = pa.table(data, schema=schema)
    sokvag.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tabell, str(sokvag))
    return sokvag


def skriv_ruttresultat_k8(
    insatstider: Sequence[InsatstidPost],
    entreer: Sequence[EntreNabarhetPost],
    kritiska_avsnitt: Sequence[KritisktAvsnitt],
    ut_mapp: str | Path,
    *,
    indata: Sequence[FilPost] | None = None,
    parametrar: Mapping[str, Any] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> dict[str, Path]:
    """Skriver komplett ruttresultat enligt K8/v1 och körmanifest K0/v1."""
    mal = Path(ut_mapp)
    mal.mkdir(parents=True, exist_ok=True)
    filer: dict[str, Path] = {}
    utdata_poster: list[FilPost] = []

    if HAR_PYARROW:
        ins_pq = _skriv_insatstider_parquet(insatstider, mal / "insatstider.parquet")
        filer["insatstider_parquet"] = ins_pq
        utdata_poster.append(
            FilPost(
                roll="insatstider_parquet",
                fil=ins_pq.name,
                sha256=sha256_fil(ins_pq),
            )
        )

    ins_json = mal / "insatstider.json"
    ins_payload = {
        "kontrakt": KONTRAKT_K8,
        "kolumner": ["adress_id", "station_id", "t_s", "insatstid_s", "torr_insatstid_s"],
        "rader": [p.till_dict() for p in insatstider],
    }
    ins_json.write_text(
        json.dumps(ins_payload, indent=2, ensure_ascii=False, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    filer["insatstider_json"] = ins_json
    utdata_poster.append(
        FilPost(roll="insatstider_json", fil=ins_json.name, sha256=sha256_fil(ins_json))
    )

    if HAR_PYARROW:
        ent_pq = _skriv_entreer_parquet(entreer, mal / "entreer_nabarhet.parquet")
        filer["entreer_nabarhet_parquet"] = ent_pq
        utdata_poster.append(
            FilPost(
                roll="entreer_nabarhet_parquet",
                fil=ent_pq.name,
                sha256=sha256_fil(ent_pq),
            )
        )

    ent_json = mal / "entreer_nabarhet.json"
    ent_payload = {
        "kontrakt": KONTRAKT_K8,
        "kolumner": ["entre_id", "t_s", "status", "gangvag_m"],
        "rader": [p.till_dict() for p in entreer],
    }
    ent_json.write_text(
        json.dumps(ent_payload, indent=2, ensure_ascii=False, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    filer["entreer_nabarhet_json"] = ent_json
    utdata_poster.append(
        FilPost(
            roll="entreer_nabarhet_json",
            fil=ent_json.name,
            sha256=sha256_fil(ent_json),
        )
    )

    krit_gpkg = skriv_kritiska_avsnitt_gpkg(
        mal / "kritiska_avsnitt.gpkg",
        kritiska_avsnitt,
    )
    filer["kritiska_avsnitt"] = krit_gpkg
    utdata_poster.append(
        FilPost(
            roll="kritiska_avsnitt",
            fil=krit_gpkg.name,
            sha256=sha256_fil(krit_gpkg),
        )
    )

    param_dict: dict[str, Any] = {
        "kontrakt_ut": KONTRAKT_K8,
        "epsg": EPSG_SWEREF99_1800,
    }
    if parametrar is not None:
        param_dict.update(parametrar)

    manifest = skapa_manifest(
        modul="rutt",
        indata=list(indata) if indata is not None else [],
        parametrar=param_dict,
        utdata=utdata_poster,
        matvarden={
            "antal_insatstider": len(insatstider),
            "antal_entreer": len(entreer),
            "antal_kritiska_avsnitt": len(kritiska_avsnitt),
        },
        kodversion=kodversion,
        start=start,
    )
    run_fil = skriv_manifest(manifest, mal / "run.json")
    filer["run"] = run_fil
    return filer
