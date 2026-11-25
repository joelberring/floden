"""Ren Python + NumPy + SQLite3 I/O för kontrakt K1 (GeoTIFF, GeoPackage, recept) och K4."""

from __future__ import annotations

import csv
import json
import sqlite3
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil

EPSG_SWEREF99_1800 = 3011
PIXELSTORLEK_M = 0.25

# OGC WKT för SWEREF 99 18 00 (EPSG:3011) enligt EPSG-registret.
WKT_EPSG_3011 = (
    'PROJCS["SWEREF99 18 00",'
    'GEOGCS["SWEREF99",'
    'DATUM["SWEREF99",'
    'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
    'AUTHORITY["EPSG","6619"]],'
    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4619"]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],'
    'PARAMETER["central_meridian",18],'
    'PARAMETER["scale_factor",1],'
    'PARAMETER["false_easting",150000],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Northing",NORTH],'
    'AXIS["Easting",EAST],'
    'AUTHORITY["EPSG","3011"]]'
)

WKT_EPSG_4326 = (
    'GEOGCS["WGS 84",'
    'DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
    'AUTHORITY["EPSG","6326"]],'
    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4326"]]'
)

# TIFF-konstanter (Classic TIFF little-endian)
_TIFF_TYPE_SHORT = 3
_TIFF_TYPE_LONG = 4
_TIFF_TYPE_DOUBLE = 12

_TAG_IMAGE_WIDTH = 256
_TAG_IMAGE_LENGTH = 257
_TAG_BITS_PER_SAMPLE = 258
_TAG_COMPRESSION = 259
_TAG_PHOTOMETRIC = 262
_TAG_STRIP_OFFSETS = 273
_TAG_SAMPLES_PER_PIXEL = 277
_TAG_ROWS_PER_STRIP = 278
_TAG_STRIP_BYTE_COUNTS = 279
_TAG_SAMPLE_FORMAT = 339
_TAG_PREDICTOR = 317
_TAG_TILE_OFFSETS = 324
_TAG_COMPRESSION_NONE = 1
_TAG_COMPRESSION_DEFLATE = (8, 32946)
_TAG_MODEL_PIXEL_SCALE = 33550
_TAG_MODEL_TIEPOINT = 33922
_TAG_GEO_KEY_DIRECTORY = 34735


class K1FormatFel(Exception):
    """Fel vid läsning eller skrivning av K1/K4-filer."""


@dataclass(frozen=True)
class GeoTransformInfo:
    """Geografisk metadata för ett enkelbands-GeoTIFF i EPSG:3011."""

    bredd: int
    hojd: int
    x_min: float
    y_max: float
    pixelstorlek_x: float
    pixelstorlek_y: float
    epsg: int
    dtype: str

    @property
    def x_max(self) -> float:
        return self.x_min + self.bredd * self.pixelstorlek_x

    @property
    def y_min(self) -> float:
        return self.y_max - self.hojd * self.pixelstorlek_y


@dataclass(frozen=True)
class BrytlinjePost:
    """En vektorpost i `brytlinjer.gpkg` (lager `brytlinjer`).

    Valfria 3D-fält (`z_kron_rh2000`, `z_botten_rh2000`, `tvarfall`, `kalla`, `osakerhet_m`)
    är bakåtkompatibla tillägg enligt K1.
    """

    typ: str
    hojd_m: float
    koordinater: tuple[tuple[float, float], ...]
    z_kron_rh2000: tuple[float, ...] | None = None
    z_botten_rh2000: tuple[float, ...] | None = None
    tvarfall: float | None = None
    kalla: str | None = None
    osakerhet_m: float | None = None


@dataclass(frozen=True)
class OppningPost:
    """En vektorpost i `oppningar.gpkg` (lager `oppningar`)."""

    typ: str
    bredd_m: float
    botten_rh2000: float
    kalla: str
    koordinater: tuple[tuple[float, float], ...]


def skriv_geotiff(
    sokvag: str | Path,
    raster: NDArray[np.float32] | NDArray[np.uint8],
    *,
    x_min: float,
    y_max: float,
    pixelstorlek: float = PIXELSTORLEK_M,
    epsg: int = EPSG_SWEREF99_1800,
) -> Path:
    """Skriver ett enkelbands-GeoTIFF (float32 eller uint8) i strikt Classic TIFF-format."""
    if raster.ndim != 2:
        raise K1FormatFel(f"Raster måste vara 2D, fick {raster.ndim}D.")
    hojd, bredd = raster.shape
    if hojd <= 0 or bredd <= 0:
        raise K1FormatFel(f"Raster måste ha positiv storlek, fick {hojd}x{bredd}.")
    if pixelstorlek <= 0.0:
        raise K1FormatFel(f"Pixelstorlek måste vara > 0, fick {pixelstorlek}.")

    if raster.dtype == np.dtype(np.float32):
        bits_per_sample = 32
        sample_format = 3  # IEEEFP
        arr = np.ascontiguousarray(raster, dtype="<f4")
    elif raster.dtype == np.dtype(np.uint8):
        bits_per_sample = 8
        sample_format = 1  # UINT
        arr = np.ascontiguousarray(raster, dtype="|u1")
    else:
        raise K1FormatFel(f"Stöd saknas för dtype '{raster.dtype}' (endast float32/uint8).")

    pixel_bytes = arr.tobytes(order="C")
    strip_byte_count = len(pixel_bytes)

    pixel_scale_bytes = struct.pack("<3d", float(pixelstorlek), float(pixelstorlek), 0.0)
    tiepoint_bytes = struct.pack("<6d", 0.0, 0.0, 0.0, float(x_min), float(y_max), 0.0)
    geokeys = (
        1, 1, 0, 3,
        1024, 0, 1, 1,  # GTModelTypeGeoKey = ModelTypeProjected
        1025, 0, 1, 1,  # GTRasterTypeGeoKey = RasterPixelIsArea
        3072, 0, 1, int(epsg),  # ProjectedCSTypeGeoKey
    )
    geokey_bytes = struct.pack(f"<{len(geokeys)}H", *geokeys)

    # 13 IFD-taggar i strikt stigande tag-ID-ordning
    ifd_offset = 8
    poster_utan_offset = [
        (_TAG_IMAGE_WIDTH, _TIFF_TYPE_LONG, 1, bredd),
        (_TAG_IMAGE_LENGTH, _TIFF_TYPE_LONG, 1, hojd),
        (_TAG_BITS_PER_SAMPLE, _TIFF_TYPE_SHORT, 1, bits_per_sample),
        (_TAG_COMPRESSION, _TIFF_TYPE_SHORT, 1, 1),
        (_TAG_PHOTOMETRIC, _TIFF_TYPE_SHORT, 1, 1),
        (_TAG_STRIP_OFFSETS, _TIFF_TYPE_LONG, 1, -1),
        (_TAG_SAMPLES_PER_PIXEL, _TIFF_TYPE_SHORT, 1, 1),
        (_TAG_ROWS_PER_STRIP, _TIFF_TYPE_LONG, 1, hojd),
        (_TAG_STRIP_BYTE_COUNTS, _TIFF_TYPE_LONG, 1, strip_byte_count),
        (_TAG_SAMPLE_FORMAT, _TIFF_TYPE_SHORT, 1, sample_format),
        (_TAG_MODEL_PIXEL_SCALE, _TIFF_TYPE_DOUBLE, 3, -2),
        (_TAG_MODEL_TIEPOINT, _TIFF_TYPE_DOUBLE, 6, -3),
        (_TAG_GEO_KEY_DIRECTORY, _TIFF_TYPE_SHORT, len(geokeys), -4),
    ]
    return _skriv_geotiff_block(
        sokvag=sokvag,
        ifd_offset=ifd_offset,
        poster_utan_offset=poster_utan_offset,
        pixel_scale_bytes=pixel_scale_bytes,
        tiepoint_bytes=tiepoint_bytes,
        geokey_bytes=geokey_bytes,
        pixel_bytes=pixel_bytes,
        ifd_poster_len=len(poster_utan_offset),
    )


def _skriv_geotiff_block(
    *,
    sokvag: str | Path,
    ifd_offset: int,
    poster_utan_offset: list[tuple[int, int, int, int]],
    pixel_scale_bytes: bytes,
    tiepoint_bytes: bytes,
    geokey_bytes: bytes,
    pixel_bytes: bytes,
    ifd_poster_len: int,
) -> Path:
    ifd_storlek = 2 + ifd_poster_len * 12 + 4
    pixel_scale_offset = ifd_offset + ifd_storlek
    tiepoint_offset = pixel_scale_offset + len(pixel_scale_bytes)
    geokey_offset = tiepoint_offset + len(tiepoint_bytes)
    strip_offset = geokey_offset + len(geokey_bytes)

    offset_karta = {
        -1: strip_offset,
        -2: pixel_scale_offset,
        -3: tiepoint_offset,
        -4: geokey_offset,
    }

    ifd_buf = bytearray(struct.pack("<H", ifd_poster_len))
    tidigare_tag = -1
    for tag, tiff_type, count, val in poster_utan_offset:
        if tag <= tidigare_tag:
            raise K1FormatFel(f"TIFF-taggar måste vara i stigande ordning: {tag} <= {tidigare_tag}")
        tidigare_tag = tag
        faktiskt_val = offset_karta.get(val, val)
        ifd_buf.extend(struct.pack("<HHII", tag, tiff_type, count, faktiskt_val))
    ifd_buf.extend(struct.pack("<I", 0))  # next IFD = 0

    header = struct.pack("<2sHI", b"II", 42, ifd_offset)
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    with mal.open("wb") as fh:
        fh.write(header)
        fh.write(bytes(ifd_buf))
        fh.write(pixel_scale_bytes)
        fh.write(tiepoint_bytes)
        fh.write(geokey_bytes)
        fh.write(pixel_bytes)
    return mal


def _las_tiff_varden(
    data: bytes,
    tiff_type: int,
    count: int,
    value_offset_bytes: bytes,
) -> tuple[float, ...]:
    typ_format = {
        _TIFF_TYPE_SHORT: ("H", 2),
        _TIFF_TYPE_LONG: ("I", 4),
        _TIFF_TYPE_DOUBLE: ("d", 8),
    }
    if tiff_type not in typ_format:
        raise K1FormatFel(f"TIFF-typ {tiff_type} stöds inte av ren-Python-läsaren.")
    fmt_char, storlek = typ_format[tiff_type]
    total_bytes = count * storlek
    if total_bytes <= 4:
        raw = value_offset_bytes[:total_bytes]
    else:
        offset = int(struct.unpack("<I", value_offset_bytes)[0])
        raw = data[offset : offset + total_bytes]
    if len(raw) != total_bytes:
        raise K1FormatFel("Trunkerad TIFF-taggdata.")
    varden = struct.unpack(f"<{count}{fmt_char}", raw)
    return tuple(float(v) for v in varden)


def las_geotiff(
    sokvag: str | Path,
) -> tuple[NDArray[np.float32] | NDArray[np.uint8], GeoTransformInfo]:
    """Läser ett little-endian Classic GeoTIFF (okomprimerat eller deflate, strips) i ren Python.

    Tiles, prediktorer och andra komprimeringar läses via rasterio om det finns installerat;
    annars ges ett tydligt `K1FormatFel`. Markmodellens skrivare skriver deflate-strips, så
    de här filerna måste gå att läsa utan rasterio.
    """
    fil = Path(sokvag)
    data = fil.read_bytes()
    if len(data) < 8 or data[:2] != b"II":
        raise K1FormatFel(f"Endast little-endian TIFF stöds: {fil}")
    magic, ifd_offset = struct.unpack("<HI", data[2:8])
    if magic != 42:
        raise K1FormatFel(f"Ogiltig TIFF-version {magic} i {fil}")

    antal_taggar = int(struct.unpack("<H", data[ifd_offset : ifd_offset + 2])[0])
    taggar: dict[int, tuple[float, ...]] = {}
    pos = ifd_offset + 2
    for _ in range(antal_taggar):
        tag, tiff_type, count = struct.unpack("<HHI", data[pos : pos + 8])
        val_bytes = data[pos + 8 : pos + 12]
        if tiff_type in (_TIFF_TYPE_SHORT, _TIFF_TYPE_LONG, _TIFF_TYPE_DOUBLE):
            taggar[tag] = _las_tiff_varden(data, tiff_type, count, val_bytes)
        pos += 12

    compression_tidig = int(taggar.get(_TAG_COMPRESSION, (1.0,))[0])
    predictor = int(taggar.get(_TAG_PREDICTOR, (1.0,))[0])
    if (
        _TAG_TILE_OFFSETS in taggar
        or predictor != 1
        or compression_tidig not in (_TAG_COMPRESSION_NONE, *_TAG_COMPRESSION_DEFLATE)
    ):
        return _las_geotiff_rasterio(fil, compression_tidig, predictor)

    obligatoriska = (
        _TAG_IMAGE_WIDTH,
        _TAG_IMAGE_LENGTH,
        _TAG_BITS_PER_SAMPLE,
        _TAG_COMPRESSION,
        _TAG_STRIP_OFFSETS,
        _TAG_STRIP_BYTE_COUNTS,
        _TAG_MODEL_PIXEL_SCALE,
        _TAG_MODEL_TIEPOINT,
        _TAG_GEO_KEY_DIRECTORY,
    )
    for tag in obligatoriska:
        if tag not in taggar:
            raise K1FormatFel(f"GeoTIFF {fil} saknar obligatorisk tagg {tag}.")

    bredd = int(taggar[_TAG_IMAGE_WIDTH][0])
    hojd = int(taggar[_TAG_IMAGE_LENGTH][0])
    bits = int(taggar[_TAG_BITS_PER_SAMPLE][0])
    compression = int(taggar[_TAG_COMPRESSION][0])
    sample_format = int(taggar.get(_TAG_SAMPLE_FORMAT, (1.0,))[0])

    offsets = [int(v) for v in taggar[_TAG_STRIP_OFFSETS]]
    counts = [int(v) for v in taggar[_TAG_STRIP_BYTE_COUNTS]]
    pixel_chunks = [data[off : off + cnt] for off, cnt in zip(offsets, counts, strict=True)]
    if compression in _TAG_COMPRESSION_DEFLATE:
        import zlib

        try:
            pixel_chunks = [zlib.decompress(c) for c in pixel_chunks]
        except zlib.error as fel:
            raise K1FormatFel(f"Trasig deflate-strip i {fil}: {fel}") from fel
    if len(b"".join(pixel_chunks)) != bredd * hojd * (bits // 8):
        raise K1FormatFel(f"Pixeldata i {fil} har fel storlek för {bredd}×{hojd}×{bits} bitar.")
    pixel_raw = b"".join(pixel_chunks)

    arr: NDArray[np.float32] | NDArray[np.uint8]
    if bits == 32 and sample_format == 3:
        arr = np.frombuffer(pixel_raw, dtype=np.dtype("<f4")).reshape((hojd, bredd)).astype(
            np.float32, copy=True
        )
        dtype_str = "float32"
    elif bits == 8 and sample_format == 1:
        arr = np.frombuffer(pixel_raw, dtype=np.dtype("|u1")).reshape((hojd, bredd)).astype(
            np.uint8, copy=True
        )
        dtype_str = "uint8"
    else:
        raise K1FormatFel(f"Stöd saknas för bits={bits}, sample_format={sample_format} i {fil}")

    scale = taggar[_TAG_MODEL_PIXEL_SCALE]
    tiepoint = taggar[_TAG_MODEL_TIEPOINT]
    geokeys = [int(v) for v in taggar[_TAG_GEO_KEY_DIRECTORY]]
    epsg = 0
    if len(geokeys) >= 4:
        antal_nycklar = geokeys[3]
        for idx in range(antal_nycklar):
            bas = 4 + idx * 4
            if bas + 3 < len(geokeys) and geokeys[bas] == 3072:
                epsg = geokeys[bas + 3]
                break

    info = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=float(tiepoint[3]),
        y_max=float(tiepoint[4]),
        pixelstorlek_x=float(scale[0]),
        pixelstorlek_y=float(scale[1]),
        epsg=epsg,
        dtype=dtype_str,
    )
    return arr, info


def _las_geotiff_rasterio(
    fil: Path, compression: int, predictor: int
) -> tuple[NDArray[np.float32] | NDArray[np.uint8], GeoTransformInfo]:
    """Fallback för tiles, prediktorer och andra komprimeringar (t.ex. COG från rasterio)."""
    try:
        import rasterio
    except ImportError as fel:
        raise K1FormatFel(
            f"{fil} är tilad eller använder komprimering {compression}/prediktor {predictor}; "
            "ren-Python-läsaren klarar bara strips utan prediktor. Installera rasterio."
        ) from fel
    with rasterio.open(fil) as ds:
        band = ds.read(1)
        tr = ds.transform
        epsg = int(ds.crs.to_epsg() or 0) if ds.crs is not None else 0
    arr: NDArray[np.float32] | NDArray[np.uint8]
    if band.dtype == np.float32:
        arr = band.astype(np.float32, copy=False)
        dtype_str = "float32"
    elif band.dtype == np.uint8:
        arr = band.astype(np.uint8, copy=False)
        dtype_str = "uint8"
    else:
        raise K1FormatFel(f"Stöd saknas för datatypen {band.dtype} i {fil}")
    info = GeoTransformInfo(
        bredd=int(arr.shape[1]),
        hojd=int(arr.shape[0]),
        x_min=float(tr.c),
        y_max=float(tr.f),
        pixelstorlek_x=float(tr.a),
        pixelstorlek_y=float(-tr.e),
        epsg=epsg,
        dtype=dtype_str,
    )
    return arr, info


def _packa_wkb_linestring(koordinater: Sequence[tuple[float, float]]) -> bytes:
    if len(koordinater) < 2:
        raise K1FormatFel("LineString måste ha minst 2 punkter.")
    buf = bytearray(struct.pack("<BII", 1, 2, len(koordinater)))
    for x, y in koordinater:
        buf.extend(struct.pack("<dd", float(x), float(y)))
    return bytes(buf)


def _packa_gp_wkb_linestring(
    koordinater: Sequence[tuple[float, float]],
    srs_id: int = EPSG_SWEREF99_1800,
) -> bytes:
    # GeoPackage Binary Header: magic 'GP', version 0, flags 0x01 (little-endian, no envelope)
    gp_header = struct.pack("<2sBBi", b"GP", 0, 1, srs_id)
    return gp_header + _packa_wkb_linestring(koordinater)


def _packa_wkb_point(punkt: tuple[float, float]) -> bytes:
    return struct.pack("<BIdd", 1, 1, float(punkt[0]), float(punkt[1]))


def _packa_gp_wkb_point(
    punkt: tuple[float, float],
    srs_id: int = EPSG_SWEREF99_1800,
) -> bytes:
    gp_header = struct.pack("<2sBBi", b"GP", 0, 1, srs_id)
    return gp_header + _packa_wkb_point(punkt)


def _skapa_gpkg_bastabeller(conn: sqlite3.Connection) -> None:
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

    srs_rader = [
        ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "Undefined cartesian SRS"),
        ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "Undefined geographic SRS"),
        ("WGS 84 geodetic", 4326, "EPSG", 4326, WKT_EPSG_4326, "WGS 84"),
        (
            "SWEREF99 18 00",
            EPSG_SWEREF99_1800,
            "EPSG",
            EPSG_SWEREF99_1800,
            WKT_EPSG_3011,
            "SWEREF 99 18 00",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO gpkg_spatial_ref_sys (
            srs_name, srs_id, organization, organization_coordsys_id, definition, description
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        srs_rader,
    )


def _berakna_bbox(
    geometrier: Sequence[Sequence[tuple[float, float]]],
) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for geom in geometrier:
        for x, y in geom:
            xs.append(float(x))
            ys.append(float(y))
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def skriv_brytlinjer_gpkg(
    sokvag: str | Path,
    poster: Sequence[BrytlinjePost],
    *,
    bbox_fallback: tuple[float, float, float, float] | None = None,
) -> Path:
    """Skriver `brytlinjer.gpkg` med lager `brytlinjer` enligt kontrakt K1 (EPSG:3011)."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    if poster:
        min_x, min_y, max_x, max_y = _berakna_bbox([p.koordinater for p in poster])
    elif bbox_fallback is not None:
        min_x, min_y, max_x, max_y = bbox_fallback
    else:
        min_x, min_y, max_x, max_y = (0.0, 0.0, 0.0, 0.0)

    conn = sqlite3.connect(str(mal))
    try:
        _skapa_gpkg_bastabeller(conn)
        conn.execute(
            """
            CREATE TABLE brytlinjer (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                typ TEXT NOT NULL,
                hojd_m REAL NOT NULL
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
                "brytlinjer",
                "features",
                "brytlinjer",
                "Brytlinjer enligt Floden K1/v1",
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
            ("brytlinjer", "geom", "LINESTRING", EPSG_SWEREF99_1800, 0, 0),
        )
        for post in poster:
            wkb = _packa_gp_wkb_linestring(post.koordinater, EPSG_SWEREF99_1800)
            conn.execute(
                "INSERT INTO brytlinjer (geom, typ, hojd_m) VALUES (?, ?, ?)",
                (sqlite3.Binary(wkb), post.typ, float(post.hojd_m)),
            )
        conn.commit()
    finally:
        conn.close()
    return mal


def skriv_oppningar_gpkg(
    sokvag: str | Path,
    poster: Sequence[OppningPost],
    *,
    bbox_fallback: tuple[float, float, float, float] | None = None,
) -> Path:
    """Skriver `oppningar.gpkg` med lager `oppningar` enligt kontrakt K1 (EPSG:3011)."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    if poster:
        min_x, min_y, max_x, max_y = _berakna_bbox([p.koordinater for p in poster])
    elif bbox_fallback is not None:
        min_x, min_y, max_x, max_y = bbox_fallback
    else:
        min_x, min_y, max_x, max_y = (0.0, 0.0, 0.0, 0.0)

    conn = sqlite3.connect(str(mal))
    try:
        _skapa_gpkg_bastabeller(conn)
        conn.execute(
            """
            CREATE TABLE oppningar (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                typ TEXT NOT NULL,
                bredd_m REAL NOT NULL,
                botten_rh2000 REAL NOT NULL,
                kalla TEXT NOT NULL
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
                "oppningar",
                "features",
                "oppningar",
                "Öppningar enligt Floden K1/v1",
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
            ("oppningar", "geom", "LINESTRING", EPSG_SWEREF99_1800, 0, 0),
        )
        for post in poster:
            wkb = (
                _packa_gp_wkb_point(post.koordinater[0], EPSG_SWEREF99_1800)
                if len(post.koordinater) == 1
                else _packa_gp_wkb_linestring(post.koordinater, EPSG_SWEREF99_1800)
            )
            conn.execute(
                """
                INSERT INTO oppningar (geom, typ, bredd_m, botten_rh2000, kalla)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sqlite3.Binary(wkb),
                    post.typ,
                    float(post.bredd_m),
                    float(post.botten_rh2000),
                    post.kalla,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return mal


def las_gpkg_tabell(sokvag: str | Path, lager: str) -> tuple[int, list[dict[str, Any]]]:
    """Läser ett lager ur en GeoPackage-fil och returnerar `(srs_id, rader)`."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise K1FormatFel(f"GeoPackage-fil saknas: {fil}")

    conn = sqlite3.connect(str(fil))
    try:
        app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
        if app_id != 1196444487:
            raise K1FormatFel(f"Ogiltigt GeoPackage application_id {app_id} i {fil}")

        rad_meta = conn.execute(
            "SELECT srs_id FROM gpkg_geometry_columns WHERE table_name = ?",
            (lager,),
        ).fetchone()
        if rad_meta is None:
            raise K1FormatFel(f"Lagret '{lager}' saknas i {fil}")
        srs_id = int(rad_meta[0])

        cur = conn.execute(f"SELECT * FROM {lager}")  # noqa: S608 - lager validerat mot metadata
        kolumner = [desc[0] for desc in cur.description]
        rader: list[dict[str, Any]] = []
        for row in cur.fetchall():
            post = dict(zip(kolumner, row, strict=True))
            geom_blob = post.get("geom")
            if isinstance(geom_blob, bytes | bytearray) and len(geom_blob) >= 8:
                if geom_blob[:2] != b"GP":
                    raise K1FormatFel(f"Ogiltig GeoPackage-geometriheader i {fil}")
                geom_srs = struct.unpack("<i", geom_blob[4:8])[0]
                if geom_srs != srs_id:
                    raise K1FormatFel(
                        f"Geometri srs_id {geom_srs} matchar inte lagrets {srs_id} i {fil}"
                    )
            rader.append(post)
        return srs_id, rader
    finally:
        conn.close()


def skriv_regn_csv(
    sokvag: str | Path,
    tidsserie: Sequence[tuple[float, float]],
) -> Path:
    """Skriver punktregn i CSV-format enligt kontrakt K4 (`tid_s,intensitet_mm_h`)."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    with mal.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["tid_s", "intensitet_mm_h"])
        for tid_s, intensitet_mm_h in tidsserie:
            writer.writerow([f"{tid_s:.1f}", f"{intensitet_mm_h:.3f}"])
    return mal


def las_regn_csv(sokvag: str | Path) -> list[tuple[float, float]]:
    """Läser punktregn enligt kontrakt K4 (`tid_s,intensitet_mm_h`)."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise K1FormatFel(f"Regnfil saknas: {fil}")
    rader: list[tuple[float, float]] = []
    with fil.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != ["tid_s", "intensitet_mm_h"]:
            raise K1FormatFel(
                f"Ogiltiga kolumner i {fil}: {reader.fieldnames}, "
                "förväntade ['tid_s', 'intensitet_mm_h']"
            )
        for row in reader:
            rader.append((float(row["tid_s"]), float(row["intensitet_mm_h"])))
    if not rader:
        raise K1FormatFel(f"Regnfilen {fil} är tom.")
    return rader


def skriv_k1_recept(
    sokvag: str | Path,
    *,
    ruta_id: str,
    filer_for_checksum: Mapping[str, Path],
    parametrar: Mapping[str, Any],
    kodversion: str = "T09a-syntetisk-v1",
) -> Path:
    """Skriver `recept.json` för en K1-markmodellruta med SHA-256-kontrollsummor."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    kallor: dict[str, dict[str, str]] = {}
    for roll, fil_sokvag in sorted(filer_for_checksum.items()):
        kallor[roll] = {
            "fil": fil_sokvag.name,
            "sha256": sha256_fil(fil_sokvag),
        }

    innehall = {
        "kontrakt": "K1/v1",
        "ruta_id": ruta_id,
        "epsg": EPSG_SWEREF99_1800,
        "hojdsystem": "RH 2000",
        "upplosning_m": PIXELSTORLEK_M,
        "kodversion": kodversion,
        "kallor": kallor,
        "parametrar": dict(parametrar),
    }
    mal.write_text(json.dumps(innehall, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return mal
