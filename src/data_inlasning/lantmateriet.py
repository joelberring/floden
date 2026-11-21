"""Inläsning av Lantmäteriets STAC-höjddata och okomprimerade LAS-punktmoln."""

from __future__ import annotations

import json
import math
import struct
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from data_inlasning.cache import DataInlasningFel, Hamtare, LokalDataCache
from gemensamt.koordinater import (
    EPSG_INTERN,
    EPSG_LANTMATERIET,
    KoordinatFel,
    transformera_till_3011,
)

STAC_HOJD_URL = "https://api.lantmateriet.se/stac-hojd/v1/search"

_LAS_HEADER_MIN_BYTES = 227
_LAS_SIGNATUR = b"LASF"
_LAS_MIN_RECORD_LEN_BY_FORMAT: dict[int, int] = {
    0: 20,
    1: 28,
    2: 26,
    3: 34,
    4: 57,
    5: 63,
    6: 30,
    7: 36,
    8: 38,
    9: 59,
    10: 67,
}

TransformatorArray = Callable[
    [NDArray[np.float64], NDArray[np.float64]],
    tuple[NDArray[np.float64], NDArray[np.float64]],
]


@dataclass(frozen=True)
class StacPost:
    """En sökträff i Lantmäteriets STAC-API för höjd- eller laserdata."""

    item_id: str
    samling: str
    bbox_wgs84: tuple[float, float, float, float]
    tillgangar: dict[str, str]
    tidpunkt: str | None


@dataclass(frozen=True)
class PunktMoln:
    """Klassificerat punktmoln i SWEREF 99 18 00 (EPSG:3011) och RH 2000."""

    e_m: NDArray[np.float64]
    n_m: NDArray[np.float64]
    z_rh2000: NDArray[np.float64]
    klass: NDArray[np.uint8]
    epsg: int
    kalla: str


def _validera_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Validerar en 4-tupel med ändliga koordinater `(min_x, min_y, max_x, max_y)`."""
    if len(bbox) != 4:
        raise DataInlasningFel("bbox_wgs84 måste innehålla exakt 4 koordinater.")
    min_x, min_y, max_x, max_y = (float(v) for v in bbox)
    for tal in (min_x, min_y, max_x, max_y):
        if not math.isfinite(tal):
            raise DataInlasningFel(f"Ogiltigt koordinatvärde i bbox_wgs84: {tal}.")
    if min_x >= max_x or min_y >= max_y:
        raise DataInlasningFel(f"Ogiltig bbox_wgs84 (min >= max): {bbox}.")
    return (min_x, min_y, max_x, max_y)


def sok_lantmateriet_stac(
    bbox_wgs84: tuple[float, float, float, float],
    *,
    samling: str = "laserdata",
    cache: LokalDataCache | None = None,
    hamtare: Hamtare | None = None,
    api_nyckel: str | None = None,
) -> list[StacPost]:
    """Söker STAC-poster i Lantmäteriets höjd-API för en WGS84-boundingbox."""
    if not isinstance(samling, str) or not samling.strip():
        raise DataInlasningFel("Samling för STAC-sökning får inte vara tom.")
    min_lon, min_lat, max_lon, max_lat = _validera_bbox(bbox_wgs84)

    params = urllib.parse.urlencode(
        {
            "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "collections": samling.strip(),
        }
    )
    url = f"{STAC_HOJD_URL}?{params}"

    headers: dict[str, str] = {"Accept": "application/geo+json, application/json"}
    if api_nyckel is not None and api_nyckel.strip():
        headers["Authorization"] = f"Bearer {api_nyckel.strip()}"

    geojson: dict[str, Any]
    if cache is not None:
        geojson, _ = cache.hamta_json(
            url,
            filnamn=f"stac_{samling.strip()}.json",
            kalla="Lantmäteriet STAC höjd",
            headers=headers,
        )
    else:
        eff_cache = LokalDataCache(hamtare=hamtare)
        if hamtare is None:
            raw = eff_cache._hamtare(url, headers)
        else:
            raw = hamtare(url, headers)
        try:
            tolkat = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataInlasningFel(f"Kunde inte tolka STAC-svar från '{url}': {exc}") from exc
        if not isinstance(tolkat, dict):
            raise DataInlasningFel("STAC-svar måste vara ett JSON-objekt.")
        geojson = tolkat

    features_raw = geojson.get("features")
    if not isinstance(features_raw, list):
        raise DataInlasningFel("STAC-svar saknar lista 'features'.")

    poster: list[StacPost] = []
    for idx, feat in enumerate(features_raw):
        if not isinstance(feat, dict):
            raise DataInlasningFel(f"STAC-feature på index {idx} är inte ett JSON-objekt.")
        item_id = feat.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise DataInlasningFel(f"STAC-feature på index {idx} saknar giltigt 'id'.")

        bbox_raw = feat.get("bbox")
        if not isinstance(bbox_raw, list) or len(bbox_raw) < 4:
            raise DataInlasningFel(f"STAC-feature '{item_id}' saknar giltig 'bbox'.")
        feat_bbox = (
            float(bbox_raw[0]),
            float(bbox_raw[1]),
            float(bbox_raw[2]),
            float(bbox_raw[3]),
        )

        assets_raw = feat.get("assets")
        if not isinstance(assets_raw, dict):
            raise DataInlasningFel(f"STAC-feature '{item_id}' saknar 'assets'.")
        tillgangar: dict[str, str] = {}
        for asset_namn, asset_obj in assets_raw.items():
            if isinstance(asset_obj, dict) and isinstance(asset_obj.get("href"), str):
                tillgangar[str(asset_namn)] = str(asset_obj["href"])

        props_raw = feat.get("properties")
        tidpunkt: str | None = None
        if isinstance(props_raw, dict):
            dt_raw = props_raw.get("datetime")
            if isinstance(dt_raw, str) and dt_raw.strip():
                tidpunkt = dt_raw
        collection_val = feat.get("collection")
        post_samling = (
            str(collection_val)
            if isinstance(collection_val, str) and collection_val.strip()
            else samling.strip()
        )

        poster.append(
            StacPost(
                item_id=item_id,
                samling=post_samling,
                bbox_wgs84=feat_bbox,
                tillgangar=tillgangar,
                tidpunkt=tidpunkt,
            )
        )

    return poster


def las_las_punktmoln(
    sokvag: str | Path,
    *,
    kall_epsg: int = EPSG_LANTMATERIET,
    transformator: TransformatorArray | None = None,
) -> PunktMoln:
    """Läser okomprimerad LAS 1.2/1.3/1.4-fil och returnerar punktmoln i EPSG:3011."""
    fil = Path(sokvag)
    if not fil.exists() or fil.is_dir():
        raise DataInlasningFel(f"LAS-fil saknas eller är en katalog: '{fil}'.")

    data = fil.read_bytes()
    if len(data) < _LAS_HEADER_MIN_BYTES:
        raise DataInlasningFel(
            f"LAS-fil '{fil}' är för kort ({len(data)} bytes < {_LAS_HEADER_MIN_BYTES})."
        )
    if data[:4] != _LAS_SIGNATUR:
        raise DataInlasningFel(f"Ogiltig LAS-signatur i '{fil}': {data[:4]!r}.")

    version_major, version_minor = struct.unpack_from("<BB", data, 24)
    header_size, offset_to_point_data = struct.unpack_from("<HI", data, 94)
    point_format_raw, point_record_len = struct.unpack_from("<BH", data, 104)
    legacy_point_count = struct.unpack_from("<I", data, 107)[0]
    x_scale, y_scale, z_scale = struct.unpack_from("<ddd", data, 131)
    x_offset, y_offset, z_offset = struct.unpack_from("<ddd", data, 155)

    if point_format_raw >= 128:
        raise DataInlasningFel(
            f"Komprimerad LAZ-fil stöds ej utan extern avkodare ('{fil}')."
        )
    point_format = int(point_format_raw)
    if point_format not in _LAS_MIN_RECORD_LEN_BY_FORMAT:
        raise DataInlasningFel(
            f"LAS-punktformat {point_format} stöds ej i '{fil}' (stöder format 0–10)."
        )
    if point_record_len < _LAS_MIN_RECORD_LEN_BY_FORMAT[point_format]:
        raise DataInlasningFel(
            f"Ogiltig postlängd {point_record_len} för format {point_format} i '{fil}'."
        )

    point_count = int(legacy_point_count)
    if (version_major, version_minor) >= (1, 4) and header_size >= 375 and len(data) >= 375:
        ext_count = struct.unpack_from("<Q", data, 247)[0]
        if ext_count > 0:
            point_count = int(ext_count)

    slutet = offset_to_point_data + point_count * point_record_len
    if offset_to_point_data < header_size or slutet > len(data):
        raise DataInlasningFel(
            f"LAS-fil '{fil}' är trunkerad: förväntade minst {slutet} bytes, har {len(data)}."
        )

    if point_count == 0:
        tom_f = np.empty(0, dtype=np.float64)
        tom_u = np.empty(0, dtype=np.uint8)
        return PunktMoln(
            e_m=tom_f,
            n_m=tom_f,
            z_rh2000=tom_f,
            klass=tom_u,
            epsg=EPSG_INTERN,
            kalla=str(fil),
        )

    klass_offset = 16 if point_format >= 6 else 15
    dt = np.dtype(
        {
            "names": ["x", "y", "z", "klass"],
            "formats": ["<i4", "<i4", "<i4", "u1"],
            "offsets": [0, 4, 8, klass_offset],
            "itemsize": point_record_len,
        }
    )
    poster = np.frombuffer(
        data,
        dtype=dt,
        count=point_count,
        offset=offset_to_point_data,
    )

    x_raw = poster["x"].astype(np.float64) * x_scale + x_offset
    y_raw = poster["y"].astype(np.float64) * y_scale + y_offset
    z_m = np.ascontiguousarray(
        poster["z"].astype(np.float64) * z_scale + z_offset,
        dtype=np.float64,
    )
    klass_arr = np.ascontiguousarray(poster["klass"], dtype=np.uint8)
    if point_format <= 5:
        # I LAS 1.0–1.3 ligger syntetisk/nyckelpunkt/överlapp i bit 5–7.
        klass_arr = klass_arr & np.uint8(0x1F)

    if kall_epsg == EPSG_INTERN:
        e_arr = np.ascontiguousarray(x_raw, dtype=np.float64)
        n_arr = np.ascontiguousarray(y_raw, dtype=np.float64)
    elif transformator is not None:
        e_t, n_t = transformator(x_raw, y_raw)
        e_arr = np.asarray(e_t, dtype=np.float64)
        n_arr = np.asarray(n_t, dtype=np.float64)
    else:
        try:
            e_t, n_t = transformera_till_3011(x_raw, y_raw, kall_epsg)
        except KoordinatFel as exc:
            raise DataInlasningFel(str(exc)) from exc
        e_arr = np.asarray(e_t, dtype=np.float64)
        n_arr = np.asarray(n_t, dtype=np.float64)

    return PunktMoln(
        e_m=e_arr,
        n_m=n_arr,
        z_rh2000=z_m,
        klass=klass_arr,
        epsg=EPSG_INTERN,
        kalla=str(fil),
    )


def skriv_syntetisk_las(
    sokvag: str | Path,
    e_m: NDArray[np.float64],
    n_m: NDArray[np.float64],
    z_m: NDArray[np.float64],
    klass: NDArray[np.uint8],
) -> Path:
    """Skriver en giltig okomprimerad LAS 1.2-fil (punktformat 0) för tester."""
    e_arr = np.asarray(e_m, dtype=np.float64)
    n_arr = np.asarray(n_m, dtype=np.float64)
    z_arr = np.asarray(z_m, dtype=np.float64)
    k_arr = np.asarray(klass, dtype=np.uint8)
    if not (e_arr.ndim == n_arr.ndim == z_arr.ndim == k_arr.ndim == 1):
        raise DataInlasningFel("Alla punktmolnsvektorer måste vara 1-dimensionella.")
    antal = len(e_arr)
    if not (len(n_arr) == antal and len(z_arr) == antal and len(k_arr) == antal):
        raise DataInlasningFel("Alla punktmolnsvektorer måste ha samma längd.")
    if antal > 0 and not (
        np.all(np.isfinite(e_arr)) and np.all(np.isfinite(n_arr)) and np.all(np.isfinite(z_arr))
    ):
        raise DataInlasningFel("Punktmolnskoordinater måste vara ändliga.")

    header_size = _LAS_HEADER_MIN_BYTES
    offset_to_points = header_size
    point_format = 0
    point_record_len = 20
    scale = 0.001

    if antal > 0:
        x_off = float(np.floor(np.min(e_arr)))
        y_off = float(np.floor(np.min(n_arr)))
        z_off = float(np.floor(np.min(z_arr)))
        min_x, max_x = float(np.min(e_arr)), float(np.max(e_arr))
        min_y, max_y = float(np.min(n_arr)), float(np.max(n_arr))
        min_z, max_z = float(np.min(z_arr)), float(np.max(z_arr))
    else:
        x_off = y_off = z_off = 0.0
        min_x = max_x = min_y = max_y = min_z = max_z = 0.0

    header = bytearray(header_size)
    header[0:4] = _LAS_SIGNATUR
    struct.pack_into("<BB", header, 24, 1, 2)  # LAS 1.2
    header[26:58] = b"floden-syntetisk".ljust(32, b"\x00")
    header[58:90] = b"floden.data_inlasning".ljust(32, b"\x00")
    struct.pack_into("<HI", header, 94, header_size, offset_to_points)
    struct.pack_into("<BH", header, 104, point_format, point_record_len)
    struct.pack_into("<I", header, 107, antal)
    struct.pack_into("<ddd", header, 131, scale, scale, scale)
    struct.pack_into("<ddd", header, 155, x_off, y_off, z_off)
    struct.pack_into("<dddddd", header, 179, max_x, min_x, max_y, min_y, max_z, min_z)

    dt = np.dtype(
        {
            "names": ["x", "y", "z", "intensity", "flags", "klass", "scan", "udata", "psrc"],
            "formats": ["<i4", "<i4", "<i4", "<u2", "u1", "u1", "i1", "u1", "<u2"],
            "itemsize": point_record_len,
        }
    )
    poster = np.zeros(antal, dtype=dt)
    if antal > 0:
        poster["x"] = np.rint((e_arr - x_off) / scale).astype(np.int32)
        poster["y"] = np.rint((n_arr - y_off) / scale).astype(np.int32)
        poster["z"] = np.rint((z_arr - z_off) / scale).astype(np.int32)
        poster["flags"] = np.uint8(0x11)  # return 1 of 1
        poster["klass"] = k_arr & np.uint8(0x1F)

    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    mal.write_bytes(bytes(header) + poster.tobytes())
    return mal
