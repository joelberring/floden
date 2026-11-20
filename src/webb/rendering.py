"""Deterministisk rasterrendering (hillshade, djup, differens) och GeoJSON för webbklient (T10)."""

from __future__ import annotations

import base64
import io
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from sankgraf.typer import MARKSLAG_BYGGNAD, SankGraf
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo

DEFAULT_KALLOR: tuple[str, ...] = ("©Lantmäteriet", "SGU", "SMHI", "Floden")

# Enkel deterministisk lokal linjärprojektion från SWEREF 99 18 00 (EPSG:3011)
# omkring Stockholms centrum (E=150 000 m, N=6 580 000 m -> ~18.0 E, 59.33 N)
# för visning i MapLibre utan externa beroenden.
_REF_E_M = 150000.0
_REF_N_M = 6580000.0
_REF_LON_DEG = 18.0
_REF_LAT_DEG = 59.33
_METERS_PER_DEG_LAT = 111390.0
_METERS_PER_DEG_LON = 111320.0 * math.cos(math.radians(_REF_LAT_DEG))


class RenderingFel(Exception):
    """Fel vid rasterrendering eller GeoJSON-konvertering."""


def koordinat_3011_till_wgs84(e_m: float, n_m: float) -> tuple[float, float]:
    """Konverterar en (E, N)-koordinat i EPSG:3011 till ungefärlig (lon, lat) WGS84."""
    e_val = float(e_m)
    n_val = float(n_m)
    if not (math.isfinite(e_val) and math.isfinite(n_val)):
        raise RenderingFel(f"Icke-ändliga koordinater: ({e_m!r}, {n_m!r}).")
    lon = _REF_LON_DEG + (e_val - _REF_E_M) / _METERS_PER_DEG_LON
    lat = _REF_LAT_DEG + (n_val - _REF_N_M) / _METERS_PER_DEG_LAT
    return (lon, lat)


def bbox_3011_till_wgs84(
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
) -> tuple[float, float, float, float]:
    """Konverterar en EPSG:3011-bbox `[x_min, y_min, x_max, y_max]` till WGS84."""
    lon_min, lat_min = koordinat_3011_till_wgs84(x_min, y_min)
    lon_max, lat_max = koordinat_3011_till_wgs84(x_max, y_max)
    return (lon_min, lat_min, lon_max, lat_max)


def _array_till_png_base64(rgba: NDArray[np.uint8]) -> tuple[str, str]:
    """Kodar ett (H, W, 4) RGBA-array till `(base64_str, data_uri)`."""
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise RenderingFel(f"Förväntade RGBA-array med form (H, W, 4), fick {rgba.shape}.")
    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return b64, f"data:image/png;base64,{b64}"


def _validera_2d_raster(raster: NDArray[np.floating[Any]], namn: str) -> NDArray[np.float64]:
    arr = np.asarray(raster, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] < 1:
        raise RenderingFel(f"{namn} måste vara ett icke-tomt 2D-raster, fick {arr.shape}.")
    return arr


def berakna_hillshade_rgba(
    mark_z: NDArray[np.floating[Any]],
    markslag: NDArray[np.uint8] | None = None,
    *,
    pixelstorlek_m: float = 0.25,
    azimuth_deg: float = 315.0,
    altitude_deg: float = 45.0,
    z_faktor: float = 1.5,
) -> NDArray[np.uint8]:
    """Beräknar deterministisk skuggning (hillshade) + höjdtoning som RGBA-raster."""
    z = _validera_2d_raster(mark_z, "mark_z")
    hojd, bredd = z.shape
    if pixelstorlek_m <= 0.0:
        raise RenderingFel(f"pixelstorlek_m måste vara > 0, fick {pixelstorlek_m}.")

    giltig = np.isfinite(z)
    ersatt = np.where(giltig, z, float(np.mean(z[giltig]))) if np.any(giltig) else np.zeros_like(z)

    if markslag is not None:
        bygg_mask = np.asarray(markslag, dtype=np.uint8) == MARKSLAG_BYGGNAD
        if np.any(~bygg_mask):
            z_grad = np.where(bygg_mask, float(np.median(ersatt[~bygg_mask])), ersatt)
        else:
            z_grad = ersatt
    else:
        bygg_mask = np.zeros((hojd, bredd), dtype=bool)
        z_grad = ersatt

    dy, dx = np.gradient(
        np.asarray(z_grad * float(z_faktor), dtype=np.float64),
        float(pixelstorlek_m),
        float(pixelstorlek_m),
    )
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dy, dx)

    zenith = math.radians(90.0 - float(altitude_deg))
    azimuth = math.radians(360.0 - float(azimuth_deg) + 90.0)
    shade = np.clip(
        math.cos(zenith) * np.cos(slope)
        + math.sin(zenith) * np.sin(slope) * np.cos(azimuth - aspect),
        0.0,
        1.0,
    )

    # Mjuk nordisk topografisk palett (ljust sand/salvia -> skiffer)
    mark_varden = z_grad[giltig]
    if mark_varden.size > 0:
        z_min = float(np.min(mark_varden))
        z_max = float(np.max(mark_varden))
    else:
        z_min, z_max = 0.0, 1.0
    spann = z_max - z_min
    if spann > 1e-6:
        z_norm = np.clip((z_grad - z_min) / spann, 0.0, 1.0)
    else:
        z_norm = np.full_like(z_grad, 0.5)

    base_r = 212.0 + 26.0 * z_norm
    base_g = 218.0 + 20.0 * z_norm
    base_b = 204.0 + 14.0 * (1.0 - z_norm)

    ljus_faktor = 0.55 + 0.48 * shade
    rgba = np.zeros((hojd, bredd, 4), dtype=np.uint8)
    rgba[:, :, 0] = np.clip(np.round(base_r * ljus_faktor), 0, 255).astype(np.uint8)
    rgba[:, :, 1] = np.clip(np.round(base_g * ljus_faktor), 0, 255).astype(np.uint8)
    rgba[:, :, 2] = np.clip(np.round(base_b * ljus_faktor), 0, 255).astype(np.uint8)
    rgba[:, :, 3] = np.where(giltig, 255, 0).astype(np.uint8)

    if np.any(bygg_mask):
        rgba[bygg_mask, 0] = 186
        rgba[bygg_mask, 1] = 191
        rgba[bygg_mask, 2] = 198
        rgba[bygg_mask, 3] = 255

    return np.asarray(rgba, dtype=np.uint8)


def rendera_hillshade_png_base64(
    mark_z: NDArray[np.floating[Any]],
    markslag: NDArray[np.uint8] | None = None,
    *,
    pixelstorlek_m: float = 0.25,
) -> dict[str, Any]:
    """Renderar hillshade till base64-kodad PNG + data-URI och min/max-höjd."""
    rgba = berakna_hillshade_rgba(mark_z, markslag, pixelstorlek_m=pixelstorlek_m)
    b64, data_uri = _array_till_png_base64(rgba)
    z = _validera_2d_raster(mark_z, "mark_z")
    giltig = np.isfinite(z)
    z_min = float(np.min(z[giltig])) if np.any(giltig) else 0.0
    z_max = float(np.max(z[giltig])) if np.any(giltig) else 0.0
    return {
        "png_base64": b64,
        "data_uri": data_uri,
        "bredd_px": int(rgba.shape[1]),
        "hojd_px": int(rgba.shape[0]),
        "z_min_rh2000": z_min,
        "z_max_rh2000": z_max,
    }


def rendera_djup_png_base64(
    djup_m: NDArray[np.floating[Any]],
    *,
    max_skala_m: float = 0.5,
    min_synligt_djup_m: float = 0.002,
) -> dict[str, Any]:
    """Renderar vattendjup till transparent RGBA-PNG (djup = 0 är helt transparent)."""
    if max_skala_m <= 0.0:
        raise RenderingFel(f"max_skala_m måste vara > 0, fick {max_skala_m}.")
    arr = _validera_2d_raster(djup_m, "djup_m")
    hojd, bredd = arr.shape

    giltig = np.isfinite(arr) & (arr > float(min_synligt_djup_m))
    norm = np.zeros_like(arr)
    norm[giltig] = np.clip(arr[giltig] / float(max_skala_m), 0.0, 1.0)

    # Ljus turkosblå vid grunt vatten -> djup koboltblå vid >= max_skala_m
    r = np.round(132.0 - 118.0 * np.power(norm, 0.75))
    g = np.round(208.0 - 142.0 * np.power(norm, 0.85))
    b = np.round(248.0 - 94.0 * np.power(norm, 1.15))
    a = np.where(giltig, np.round(135.0 + 105.0 * np.power(norm, 0.6)), 0.0)

    rgba = np.zeros((hojd, bredd, 4), dtype=np.uint8)
    rgba[:, :, 0] = np.clip(r, 0, 255).astype(np.uint8)
    rgba[:, :, 1] = np.clip(g, 0, 255).astype(np.uint8)
    rgba[:, :, 2] = np.clip(b, 0, 255).astype(np.uint8)
    rgba[:, :, 3] = np.clip(a, 0, 255).astype(np.uint8)

    b64, data_uri = _array_till_png_base64(rgba)
    varden = arr[np.isfinite(arr)]
    max_djup = float(np.max(varden)) if varden.size > 0 else 0.0
    return {
        "png_base64": b64,
        "data_uri": data_uri,
        "bredd_px": int(bredd),
        "hojd_px": int(hojd),
        "max_djup_m": max_djup,
    }


def rendera_diff_png_base64(
    diff_m: NDArray[np.floating[Any]],
    *,
    max_abs_m: float = 0.25,
    min_synlig_diff_m: float = 0.001,
) -> dict[str, Any]:
    """Renderar djupdifferens (efter - före) till transparent PNG (0 är transparent)."""
    if max_abs_m <= 0.0:
        raise RenderingFel(f"max_abs_m måste vara > 0, fick {max_abs_m}.")
    arr = _validera_2d_raster(diff_m, "diff_m")
    hojd, bredd = arr.shape

    giltig = np.isfinite(arr)
    abs_arr = np.abs(arr)
    synlig = giltig & (abs_arr > float(min_synlig_diff_m))
    mag = np.zeros_like(arr)
    mag[synlig] = np.clip(abs_arr[synlig] / float(max_abs_m), 0.0, 1.0)

    pos = synlig & (arr > 0.0)
    neg = synlig & (arr < 0.0)

    rgba = np.zeros((hojd, bredd, 4), dtype=np.uint8)
    # Positiv diff (försämring / djupare vatten): orange -> röd
    rgba[pos, 0] = 224
    rgba[pos, 1] = np.clip(np.round(165.0 - 135.0 * mag[pos]), 0, 255).astype(np.uint8)
    rgba[pos, 2] = np.clip(np.round(80.0 - 60.0 * mag[pos]), 0, 255).astype(np.uint8)

    # Negativ diff (förbättring / lägre djup): ljus cyan -> smaragd/blå
    rgba[neg, 0] = np.clip(np.round(85.0 - 65.0 * mag[neg]), 0, 255).astype(np.uint8)
    rgba[neg, 1] = np.clip(np.round(190.0 - 50.0 * mag[neg]), 0, 255).astype(np.uint8)
    rgba[neg, 2] = 220

    rgba[:, :, 3] = np.where(
        synlig, np.clip(np.round(125.0 + 115.0 * np.power(mag, 0.55)), 0, 255), 0
    ).astype(np.uint8)

    b64, data_uri = _array_till_png_base64(rgba)
    varden = arr[giltig]
    min_diff = float(np.min(varden)) if varden.size > 0 else 0.0
    max_diff = float(np.max(varden)) if varden.size > 0 else 0.0
    return {
        "png_base64": b64,
        "data_uri": data_uri,
        "bredd_px": int(bredd),
        "hojd_px": int(hojd),
        "min_diff_m": min_diff,
        "max_diff_m": max_diff,
    }


def _linje_till_geojson_feature(
    koordinater_3011: Sequence[tuple[float, float]],
    egenskaper: dict[str, Any],
    *,
    feature_id: str | int,
) -> dict[str, Any]:
    wgs84_koord = [list(koordinat_3011_till_wgs84(x, y)) for x, y in koordinater_3011]
    koord_3011 = [[float(x), float(y)] for x, y in koordinater_3011]
    props = dict(egenskaper)
    props["koordinater_3011"] = koord_3011
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {
            "type": "LineString",
            "coordinates": wgs84_koord,
        },
        "properties": props,
    }


def brytlinjer_till_geojson(brytlinjer: Sequence[BrytlinjePost]) -> dict[str, Any]:
    """Konverterar K1-brytlinjer till GeoJSON FeatureCollection (WGS84 + EPSG:3011 i props)."""
    features: list[dict[str, Any]] = []
    for idx, post in enumerate(brytlinjer):
        if len(post.koordinater) < 2:
            continue
        features.append(
            _linje_till_geojson_feature(
                post.koordinater,
                {
                    "typ": post.typ,
                    "hojd_m": float(post.hojd_m),
                },
                feature_id=f"bryt-{idx}",
            )
        )
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def byggnader_till_geojson(
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
) -> dict[str, Any]:
    """Konverterar byggnadsceller (markslag=9) till sammanhängande rutrektanglar i GeoJSON."""
    slag = np.asarray(markslag, dtype=np.uint8)
    if slag.shape != (geo.hojd, geo.bredd):
        raise RenderingFel(
            f"markslag form {slag.shape} matchar inte GeoTransformInfo {(geo.hojd, geo.bredd)}."
        )
    bygg_mask = slag == MARKSLAG_BYGGNAD
    features: list[dict[str, Any]] = []
    if not np.any(bygg_mask):
        return {"type": "FeatureCollection", "features": features}

    # Sammanfoga sammanhängande horisontella körningar per rad för kompakt GeoJSON
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    fid = 0
    for rad in range(geo.hojd):
        kol = 0
        while kol < geo.bredd:
            if not bool(bygg_mask[rad, kol]):
                kol += 1
                continue
            start_k = kol
            while kol < geo.bredd and bool(bygg_mask[rad, kol]):
                kol += 1
            slut_k = kol
            x0 = geo.x_min + start_k * dx
            x1 = geo.x_min + slut_k * dx
            y1 = geo.y_max - rad * dy
            y0 = geo.y_max - (rad + 1) * dy
            ring_3011 = [
                (x0, y0),
                (x1, y0),
                (x1, y1),
                (x0, y1),
                (x0, y0),
            ]
            ring_wgs84 = [list(koordinat_3011_till_wgs84(x, y)) for x, y in ring_3011]
            features.append(
                {
                    "type": "Feature",
                    "id": f"bygg-{fid}",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [ring_wgs84],
                    },
                    "properties": {
                        "markslag": int(MARKSLAG_BYGGNAD),
                        "koordinater_3011": [[float(x), float(y)] for x, y in ring_3011],
                    },
                }
            )
            fid += 1

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def sankgraf_noder_till_geojson(graf: SankGraf) -> dict[str, Any]:
    """Konverterar sänkgrafens noder till GeoJSON-punkter (centrum / överfallspunkt)."""
    geo = graf.geo
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    ct = graf.cell_till_nod
    features: list[dict[str, Any]] = []

    for nid in sorted(graf.noder):
        nod = graf.noder[nid]
        lov_ids = graf.lov_under_nod(nid)
        mask = np.isin(ct, lov_ids) if lov_ids else np.zeros(ct.shape, dtype=bool)
        if np.any(mask):
            rader, kolumner = np.nonzero(mask)
            cx = geo.x_min + (float(np.mean(kolumner)) + 0.5) * dx
            cy = geo.y_max - (float(np.mean(rader)) + 0.5) * dy
        else:
            cx = float(nod.overfall_x)
            cy = float(nod.overfall_y)

        lon, lat = koordinat_3011_till_wgs84(cx, cy)
        features.append(
            {
                "type": "Feature",
                "id": f"nod-{nid}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
                "properties": {
                    "nod_id": int(nod.nod_id),
                    "ar_lov": bool(nod.ar_lov),
                    "forald_id": int(nod.forald_id) if nod.forald_id is not None else None,
                    "ut_till_id": int(nod.ut_till_id) if nod.ut_till_id is not None else None,
                    "botten_rh2000": float(nod.botten_rh2000),
                    "overfall_rh2000": float(nod.overfall_rh2000),
                    "max_volym_m3": float(nod.max_volym_m3),
                    "avrinningsyta_m2": float(nod.avrinningsyta_m2),
                    "persistens_m": float(nod.persistens_m),
                    "osakerhet_m": float(nod.osakerhet_m),
                    "e_3011": float(cx),
                    "n_3011": float(cy),
                    "overfall_3011": [float(nod.overfall_x), float(nod.overfall_y)],
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def sankgraf_flodesvagar_till_geojson(
    graf: SankGraf,
    *,
    max_antal: int = 300,
) -> dict[str, Any]:
    """Konverterar sänkgrafens flödesvägar till GeoJSON LineStrings."""
    features: list[dict[str, Any]] = []
    for vag in graf.flodesvagar[:max_antal]:
        if len(vag.koordinater) < 2:
            continue
        features.append(
            _linje_till_geojson_feature(
                vag.koordinater,
                {
                    "flodesvag_id": int(vag.id),
                    "nod_id": int(vag.nod_id),
                    "langd_m": float(vag.langd_m),
                    "lutning": float(vag.lutning),
                    "gangtid_s": float(vag.gangtid_s),
                },
                feature_id=f"flode-{vag.id}",
            )
        )
    return {
        "type": "FeatureCollection",
        "features": features,
    }
