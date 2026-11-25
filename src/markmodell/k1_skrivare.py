"""Självständig K1-skrivare för GeoTIFF (EPSG:3011) och GeoPackage (SQLite WKB)."""

from __future__ import annotations

import importlib.util
import math
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from markmodell.brytlinjer import Brytlinje
from markmodell.hinder_oppningar import Oppning
from markmodell.rutnat import Ruta

EPSG_SWEREF99_1800 = 3011

_HAR_RASTERIO = importlib.util.find_spec("rasterio") is not None


class K1SkrivFel(Exception):
    """Fel vid skrivning av K1-raster eller K1-vektorlager."""


def _skriv_geotiff_ren_python(
    sokvag: Path,
    matris: NDArray[np.float32] | NDArray[np.uint8],
    ruta: Ruta,
    nodata: float | int,
) -> Path:
    """Skriver en standardkompatibel tiled GeoTIFF (Little-Endian, DEFLATE) med EPSG:3011.

    Används som deterministisk reserv när `rasterio` inte är installerat. Följer
    TIFF 6.0 + GeoTIFF 1.0 (ModelTiepointTag 33922, ModelPixelScaleTag 33550,
    GeoKeyDirectoryTag 34735 med ProjectedCSTypeGeoKey=3011).
    """
    import zlib

    arr = np.ascontiguousarray(matris)
    rader, kolumner = arr.shape
    if arr.dtype == np.float32:
        sample_format = 3  # IEEE float
        bits_per_sample = 32
        nodata_str = f"{float(nodata):.6g}\x00".encode("ascii")
    elif arr.dtype == np.uint8:
        sample_format = 1  # unsigned int
        bits_per_sample = 8
        nodata_str = f"{int(nodata)}\x00".encode("ascii")
    else:
        raise K1SkrivFel(f"Ostödd datatyp för GeoTIFF: {arr.dtype}")

    # Skriv som strip-baserad GeoTIFF med rader per strip (lättläst av GDAL/rasterio/PIL)
    rader_per_strip = min(64, rader)
    strips: list[bytes] = []
    for r0 in range(0, rader, rader_per_strip):
        r1 = min(rader, r0 + rader_per_strip)
        raw_strip = arr[r0:r1, :].tobytes()
        strips.append(zlib.compress(raw_strip, level=6))

    e_min, _, _, n_max = ruta.yttre_bbox
    pixel_scale = struct.pack("<3d", ruta.upplosning_m, ruta.upplosning_m, 0.0)
    tiepoint = struct.pack("<6d", 0.0, 0.0, 0.0, e_min, n_max, 0.0)
    # GeoKeyDirectoryTag: Version 1.1.0, 3 nycklar:
    # 1024 (GTModelTypeGeoKey) = 1 (Projected)
    # 1025 (GTRasterTypeGeoKey) = 1 (PixelIsArea)
    # 3072 (ProjectedCSTypeGeoKey) = 3011 (SWEREF 99 18 00)
    geo_keys = struct.pack(
        "<16H",
        1, 1, 0, 3,
        1024, 0, 1, 1,
        1025, 0, 1, 1,
        3072, 0, 1, EPSG_SWEREF99_1800,
    )

    antal_strips = len(strips)
    if antal_strips == 1:
        strip_offsets_typ = 4  # LONG
        strip_counts_typ = 4
    else:
        strip_offsets_typ = 4
        strip_counts_typ = 4

    # IFD-poster (måste vara sorterade i stigande taggordning!)
    # 256 ImageWidth
    # 257 ImageLength
    # 258 BitsPerSample
    # 259 Compression (8 = Deflate/Adobe)
    # 262 PhotometricInterpretation (1 = BlackIsZero)
    # 273 StripOffsets
    # 277 SamplesPerPixel
    # 278 RowsPerStrip
    # 279 StripByteCounts
    # 339 SampleFormat
    # 33550 ModelPixelScaleTag
    # 33922 ModelTiepointTag
    # 34735 GeoKeyDirectoryTag
    # 42113 GDAL_NODATA
    antal_taggar = 14
    header_size = 8
    ifd_offset = header_size
    ifd_size = 2 + antal_taggar * 12 + 4
    extra_offset = ifd_offset + ifd_size

    offsets_bytes_len = antal_strips * 4 if antal_strips > 1 else 0
    counts_bytes_len = antal_strips * 4 if antal_strips > 1 else 0

    strip_offsets_pos = extra_offset
    strip_counts_pos = strip_offsets_pos + offsets_bytes_len
    pixel_scale_pos = strip_counts_pos + counts_bytes_len
    tiepoint_pos = pixel_scale_pos + len(pixel_scale)
    geo_keys_pos = tiepoint_pos + len(tiepoint)
    nodata_pos = geo_keys_pos + len(geo_keys)
    # GDAL_NODATA ≤ 4 byte lagras direkt i IFD-posten och tar då ingen plats här.
    data_start = nodata_pos + (len(nodata_str) if len(nodata_str) > 4 else 0)

    strip_offsets: list[int] = []
    strip_counts: list[int] = []
    cursor = data_start
    for s in strips:
        strip_offsets.append(cursor)
        strip_counts.append(len(s))
        cursor += len(s)

    def _entry_short(tag: int, val: int) -> bytes:
        return struct.pack("<HHII", tag, 3, 1, val)

    def _entry_long(tag: int, val: int) -> bytes:
        return struct.pack("<HHII", tag, 4, 1, val)

    def _entry_ptr(tag: int, typ: int, count: int, offset: int) -> bytes:
        return struct.pack("<HHII", tag, typ, count, offset)

    ifd_entries: list[bytes] = [
        _entry_long(256, kolumner),
        _entry_long(257, rader),
        _entry_short(258, bits_per_sample),
        _entry_short(259, 8),
        _entry_short(262, 1),
        (
            _entry_long(273, strip_offsets[0])
            if antal_strips == 1
            else _entry_ptr(273, strip_offsets_typ, antal_strips, strip_offsets_pos)
        ),
        _entry_short(277, 1),
        _entry_long(278, rader_per_strip),
        (
            _entry_long(279, strip_counts[0])
            if antal_strips == 1
            else _entry_ptr(279, strip_counts_typ, antal_strips, strip_counts_pos)
        ),
        _entry_short(339, sample_format),
        _entry_ptr(33550, 12, 3, pixel_scale_pos),
        _entry_ptr(33922, 12, 6, tiepoint_pos),
        _entry_ptr(34735, 3, 16, geo_keys_pos),
        (
            struct.pack("<HHI4s", 42113, 2, len(nodata_str), nodata_str.ljust(4, b"\x00"))
            if len(nodata_str) <= 4
            else _entry_ptr(42113, 2, len(nodata_str), nodata_pos)
        ),
    ]

    with sokvag.open("wb") as fh:
        fh.write(struct.pack("<2sHI", b"II", 42, ifd_offset))
        fh.write(struct.pack("<H", antal_taggar))
        for e in ifd_entries:
            fh.write(e)
        fh.write(struct.pack("<I", 0))  # next IFD = 0
        if antal_strips > 1:
            fh.write(struct.pack(f"<{antal_strips}I", *strip_offsets))
            fh.write(struct.pack(f"<{antal_strips}I", *strip_counts))
        fh.write(pixel_scale)
        fh.write(tiepoint)
        fh.write(geo_keys)
        if len(nodata_str) > 4:
            fh.write(nodata_str)
        for s in strips:
            fh.write(s)

    return sokvag


def skriv_geotiff(
    sokvag: str | Path,
    matris: NDArray[np.float32] | NDArray[np.uint8],
    ruta: Ruta,
    nodata: float | int,
) -> Path:
    """Skriver K1-raster till GeoTIFF/COG i EPSG:3011."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if matris.shape != ruta.form_rc:
        raise K1SkrivFel(f"Rasterform {matris.shape} matchar inte {ruta.form_rc}.")

    if _HAR_RASTERIO:
        from gemensamt.cog import skriv_komprimerad_cog

        e_min, _, _, n_max = ruta.yttre_bbox
        return skriv_komprimerad_cog(
            mal,
            matris,
            x_min=e_min,
            y_max=n_max,
            pixelstorlek=ruta.upplosning_m,
            epsg=EPSG_SWEREF99_1800,
            nodata=nodata,
        )

    return _skriv_geotiff_ren_python(mal, matris, ruta, nodata=nodata)


def _gpkg_linestring_blob(punkter: Sequence[tuple[float, float]]) -> bytes:
    """Kodar en 2D LineString som GeoPackageBinary (GP) enligt OGC GeoPackage 1.2."""
    xs = [p[0] for p in punkter]
    ys = [p[1] for p in punkter]
    # Header: magic "GP", version 0, flags=0x03 (little-endian + XY envelope), srs_id=3011
    envelope = struct.pack("<4d", min(xs), max(xs), min(ys), max(ys))
    gp_header = struct.pack("<2sBBi", b"GP", 0, 0x03, EPSG_SWEREF99_1800) + envelope
    # WKB LineString: byte_order=1 (LE), wkbType=2 (LineString), numPoints, coords...
    wkb = bytearray(struct.pack("<BII", 1, 2, len(punkter)))
    for x, y in punkter:
        wkb.extend(struct.pack("<2d", float(x), float(y)))
    return gp_header + bytes(wkb)


def _gpkg_linestring_z_blob(
    punkter: Sequence[tuple[float, float]],
    z_varden: Sequence[float],
) -> bytes:
    """Kodar en 3D LineStringZ (ISO WKB 1002) som GeoPackageBinary (GP) enligt OGC GeoPackage."""
    xs = [p[0] for p in punkter]
    ys = [p[1] for p in punkter]
    zs = [float(z) for z in z_varden]
    # flags=0x05: little-endian (bit 0 = 1) + XYZ envelope (bits 1-3 = 2 -> 0x04)
    envelope = struct.pack("<6d", min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    gp_header = struct.pack("<2sBBi", b"GP", 0, 0x05, EPSG_SWEREF99_1800) + envelope
    # ISO WKB LineStringZ = 1002
    wkb = bytearray(struct.pack("<BII", 1, 1002, len(punkter)))
    for (x, y), z in zip(punkter, zs, strict=True):
        wkb.extend(struct.pack("<3d", float(x), float(y), z))
    return gp_header + bytes(wkb)


def _initiera_gpkg_bas(conn: sqlite3.Connection) -> None:
    """Skapar GeoPackage-standardtabeller inklusive EPSG:3011 i `gpkg_spatial_ref_sys`."""
    conn.execute("PRAGMA application_id = 1196444487;")  # 'GPKG'
    conn.execute("PRAGMA user_version = 10200;")
    conn.execute(
        """
        CREATE TABLE gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL,
            description TEXT
        );
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
        );
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
        );
        """
    )
    wkt_3011 = (
        'PROJCS["SWEREF99 18 00",'
        'GEOGCS["SWEREF99",'
        'DATUM["SWEREF99",SPHEROID["GRS 1980",6378137,298.257222101]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",18],'
        'PARAMETER["scale_factor",1],PARAMETER["false_easting",150000],'
        'PARAMETER["false_northing",0],UNIT["metre",1],'
        'AUTHORITY["EPSG","3011"]]'
    )
    conn.executemany(
        "INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("Undefined Cartesian", -1, "NONE", -1, "undefined", "undefined"),
            ("Undefined Geographic", 0, "NONE", 0, "undefined", "undefined"),
            ("SWEREF99 18 00", 3011, "EPSG", 3011, wkt_3011, "SWEREF 99 18 00"),
        ],
    )


def _berakna_bbox_linjer(
    linjer: Sequence[Sequence[tuple[float, float]]],
    ruta: Ruta,
) -> tuple[float, float, float, float]:
    if not linjer:
        return ruta.yttre_bbox
    xs = [p[0] for seg in linjer for p in seg]
    ys = [p[1] for seg in linjer for p in seg]
    return (min(xs), min(ys), max(xs), max(ys))


def skriv_brytlinjer_gpkg(
    sokvag: str | Path,
    linjer: Sequence[Brytlinje],
    ruta: Ruta,
) -> Path:
    """Skriver `brytlinjer.gpkg` (lager `brytlinjer`, EPSG:3011) enligt K1.

    Valfria 3D-kolumner (`z_kron_rh2000`, `z_botten_rh2000`, `tvarfall`, `kalla`,
    `osakerhet_m`) skrivs bakåtkompatibelt; linjer med 3D-krönhöjd kodas som `LineStringZ`.
    """
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    har_3d = any(b.z_kron_rh2000 is not None for b in linjer)
    min_x, min_y, max_x, max_y = _berakna_bbox_linjer([b.punkter for b in linjer], ruta)
    conn = sqlite3.connect(str(mal))
    try:
        _initiera_gpkg_bas(conn)
        conn.execute(
            """
            CREATE TABLE brytlinjer (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                typ TEXT NOT NULL,
                hojd_m REAL NOT NULL,
                z_kron_rh2000 REAL,
                z_botten_rh2000 REAL,
                tvarfall REAL,
                kalla TEXT,
                osakerhet_m REAL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change,
                 min_x, min_y, max_x, max_y, srs_id)
            VALUES (?, 'features', ?, '', '2026-01-01T00:00:00.000Z', ?, ?, ?, ?, ?);
            """,
            ("brytlinjer", "brytlinjer", min_x, min_y, max_x, max_y, EPSG_SWEREF99_1800),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns
                (table_name, column_name, geometry_type_name, srs_id, z, m)
            VALUES ('brytlinjer', 'geom', 'LINESTRING', ?, ?, 0);
            """,
            (EPSG_SWEREF99_1800, 1 if har_3d else 0),
        )
        for b in linjer:
            if b.z_kron_rh2000 is not None:
                blob = _gpkg_linestring_z_blob(b.punkter, b.z_kron_rh2000)
                z_kron_medel: float | None = float(sum(b.z_kron_rh2000) / len(b.z_kron_rh2000))
            else:
                blob = _gpkg_linestring_blob(b.punkter)
                z_kron_medel = None
            z_botten_medel = (
                float(sum(b.z_botten_rh2000) / len(b.z_botten_rh2000))
                if b.z_botten_rh2000 is not None
                else None
            )
            conn.execute(
                """
                INSERT INTO brytlinjer (
                    geom, typ, hojd_m, z_kron_rh2000, z_botten_rh2000,
                    tvarfall, kalla, osakerhet_m
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    blob,
                    b.typ,
                    float(b.hojd_m),
                    z_kron_medel,
                    z_botten_medel,
                    b.tvarfall,
                    b.kalla,
                    b.osakerhet_m,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return mal


def skriv_oppningar_gpkg(
    sokvag: str | Path,
    oppningar: Sequence[Oppning],
    ruta: Ruta,
) -> Path:
    """Skriver `oppningar.gpkg` (lager `oppningar`, EPSG:3011) enligt K1."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    min_x, min_y, max_x, max_y = _berakna_bbox_linjer([o.linje for o in oppningar], ruta)
    conn = sqlite3.connect(str(mal))
    try:
        _initiera_gpkg_bas(conn)
        conn.execute(
            """
            CREATE TABLE oppningar (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                typ TEXT NOT NULL,
                bredd_m REAL NOT NULL,
                botten_rh2000 REAL NOT NULL,
                kalla TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change,
                 min_x, min_y, max_x, max_y, srs_id)
            VALUES (?, 'features', ?, '', '2026-01-01T00:00:00.000Z', ?, ?, ?, ?, ?);
            """,
            ("oppningar", "oppningar", min_x, min_y, max_x, max_y, EPSG_SWEREF99_1800),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns
                (table_name, column_name, geometry_type_name, srs_id, z, m)
            VALUES ('oppningar', 'geom', 'LINESTRING', ?, 0, 0);
            """,
            (EPSG_SWEREF99_1800,),
        )
        for o in oppningar:
            if not all(math.isfinite(c) for p in o.linje for c in p):
                raise K1SkrivFel("Öppning innehåller ogiltiga koordinater.")
            blob = _gpkg_linestring_blob(o.linje)
            conn.execute(
                """
                INSERT INTO oppningar (geom, typ, bredd_m, botten_rh2000, kalla)
                VALUES (?, ?, ?, ?, ?)
                """,
                (blob, o.typ, float(o.bredd_m), float(o.botten_rh2000), o.kalla),
            )
        conn.commit()
    finally:
        conn.close()
    return mal
