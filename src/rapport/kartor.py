"""Serverrenderad kartografi med fast färgskala och källtext (T08)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

_RAND_PX = 24
_LEGEND_BREDD_PX = 36
_LEGEND_MELLANRUM_PX = 16
_TITEL_HOJD_PX = 34
_FOT_HOJD_PX = 28


class RapportKartaFel(Exception):
    """Fel vid validering eller rendering av rapportkarta."""


def _validera_raster(raster: NDArray[np.floating[Any]], namn: str) -> NDArray[np.float64]:
    arr = np.asarray(raster, dtype=np.float64)
    if arr.ndim != 2:
        raise RapportKartaFel(f"{namn} måste vara ett 2D-raster, fick {arr.ndim}D.")
    if arr.shape[0] < 1 or arr.shape[1] < 1:
        raise RapportKartaFel(f"{namn} får inte ha tomma dimensioner: {arr.shape}.")
    return arr


def _djup_fargkarta(djup_m: NDArray[np.float64]) -> NDArray[np.uint8]:
    """Mappar vattendjup (0..1 m) till RGB med fast blå färgskala."""
    hojd, bredd = djup_m.shape
    rgb = np.full((hojd, bredd, 3), 245, dtype=np.uint8)

    giltig = np.isfinite(djup_m)
    norm = np.zeros_like(djup_m)
    norm[giltig] = np.clip(djup_m[giltig] / 1.0, 0.0, 1.0)

    # Ljus bakgrund vid 0 m -> klarblå vid 0.3 m -> mörkblå vid >= 1.0 m
    r = np.where(giltig, np.round(235.0 - 215.0 * np.power(norm, 0.75)), 220.0)
    g = np.where(giltig, np.round(244.0 - 174.0 * np.power(norm, 0.85)), 220.0)
    b = np.where(giltig, np.round(252.0 - 112.0 * np.power(norm, 1.15)), 220.0)

    rgb[:, :, 0] = np.clip(r, 0, 255).astype(np.uint8)
    rgb[:, :, 1] = np.clip(g, 0, 255).astype(np.uint8)
    rgb[:, :, 2] = np.clip(b, 0, 255).astype(np.uint8)
    return rgb


def _diff_fargkarta(diff_m: NDArray[np.float64], max_abs_m: float) -> NDArray[np.uint8]:
    """Mappar differens (-max..+max m) till blå-vit-röd skala."""
    hojd, bredd = diff_m.shape
    rgb = np.full((hojd, bredd, 3), 255, dtype=np.uint8)

    giltig = np.isfinite(diff_m)
    norm = np.zeros_like(diff_m)
    norm[giltig] = np.clip(diff_m[giltig] / max_abs_m, -1.0, 1.0)

    neg = giltig & (norm < 0.0)
    pos = giltig & (norm > 0.0)
    ogiltig = ~giltig

    mag = np.abs(norm)
    # Negativ (minskat djup): vit -> blå
    rgb[neg, 0] = np.clip(np.round(255.0 - 220.0 * mag[neg]), 0, 255).astype(np.uint8)
    rgb[neg, 1] = np.clip(np.round(255.0 - 155.0 * mag[neg]), 0, 255).astype(np.uint8)
    rgb[neg, 2] = 255

    # Positiv (ökat djup): vit -> röd
    rgb[pos, 0] = 255
    rgb[pos, 1] = np.clip(np.round(255.0 - 205.0 * mag[pos]), 0, 255).astype(np.uint8)
    rgb[pos, 2] = np.clip(np.round(255.0 - 215.0 * mag[pos]), 0, 255).astype(np.uint8)

    rgb[ogiltig] = np.array([220, 220, 220], dtype=np.uint8)
    return rgb


def _komponera_karta_med_legend(
    rgb: NDArray[np.uint8],
    legend_rgb: NDArray[np.uint8],
    *,
    titel: str,
    kalla: str,
    legend_topp: str,
    legend_mitt: str,
    legend_botten: str,
) -> Image.Image:
    karta_h, karta_w, _ = rgb.shape
    skala = 1
    if karta_w < 200 or karta_h < 200:
        skala = max(1, min(400 // max(karta_w, 1), 400 // max(karta_h, 1)))

    karta_img = Image.fromarray(rgb, mode="RGB")
    if skala > 1:
        karta_img = karta_img.resize(
            (karta_w * skala, karta_h * skala), resample=Image.Resampling.NEAREST
        )

    vis_w, vis_h = karta_img.size
    legend_img = Image.fromarray(legend_rgb, mode="RGB").resize(
        (_LEGEND_BREDD_PX, vis_h), resample=Image.Resampling.BILINEAR
    )

    total_w = _RAND_PX + vis_w + _LEGEND_MELLANRUM_PX + _LEGEND_BREDD_PX + 72 + _RAND_PX
    total_h = _TITEL_HOJD_PX + vis_h + _FOT_HOJD_PX

    duk = Image.new("RGB", (total_w, total_h), (255, 255, 255))
    duk.paste(karta_img, (_RAND_PX, _TITEL_HOJD_PX))
    legend_x = _RAND_PX + vis_w + _LEGEND_MELLANRUM_PX
    duk.paste(legend_img, (legend_x, _TITEL_HOJD_PX))

    rit = ImageDraw.Draw(duk)
    rit.rectangle(
        [_RAND_PX - 1, _TITEL_HOJD_PX - 1, _RAND_PX + vis_w, _TITEL_HOJD_PX + vis_h],
        outline=(60, 60, 60),
    )
    rit.rectangle(
        [legend_x - 1, _TITEL_HOJD_PX - 1, legend_x + _LEGEND_BREDD_PX, _TITEL_HOJD_PX + vis_h],
        outline=(60, 60, 60),
    )

    rit.text((_RAND_PX, 10), titel, fill=(20, 20, 20))
    etikett_x = legend_x + _LEGEND_BREDD_PX + 6
    rit.text((etikett_x, _TITEL_HOJD_PX), legend_topp, fill=(30, 30, 30))
    rit.text((etikett_x, _TITEL_HOJD_PX + vis_h // 2 - 5), legend_mitt, fill=(30, 30, 30))
    rit.text((etikett_x, _TITEL_HOJD_PX + vis_h - 12), legend_botten, fill=(30, 30, 30))

    fot_text = f"EPSG:3011 / RH 2000 | Källa: {kalla}"
    rit.text((_RAND_PX, _TITEL_HOJD_PX + vis_h + 8), fot_text, fill=(70, 70, 70))
    return duk


def rendera_djupkarta_png(
    djup_m: NDArray[np.floating[Any]],
    ut_fil: str | Path,
    *,
    titel: str,
    kalla: str = "©Lantmäteriet / Floden",
) -> Path:
    """Renderar maximalt vattendjup (0–1 m) till PNG med fast blå färgskala och legend."""
    arr = _validera_raster(djup_m, "djup_m")
    rgb = _djup_fargkarta(arr)
    legend_steg = np.linspace(1.0, 0.0, 128, dtype=np.float64)[:, None]
    legend_rgb = _djup_fargkarta(np.repeat(legend_steg, 16, axis=1))

    bild = _komponera_karta_med_legend(
        rgb,
        legend_rgb,
        titel=titel,
        kalla=kalla,
        legend_topp=">= 1.0 m",
        legend_mitt="0.5 m",
        legend_botten="0.0 m",
    )

    mal = Path(ut_fil)
    mal.parent.mkdir(parents=True, exist_ok=True)
    bild.save(mal, format="PNG")
    return mal


def rendera_diffkarta_png(
    diff_m: NDArray[np.floating[Any]],
    ut_fil: str | Path,
    *,
    titel: str,
    kalla: str = "©Lantmäteriet / Floden",
    max_abs_m: float = 0.5,
) -> Path:
    """Renderar differenskarta före/efter till PNG med blå-vit-röd skala och legend."""
    if max_abs_m <= 0.0:
        raise RapportKartaFel("max_abs_m måste vara > 0.")
    arr = _validera_raster(diff_m, "diff_m")
    rgb = _diff_fargkarta(arr, float(max_abs_m))
    legend_steg = np.linspace(float(max_abs_m), -float(max_abs_m), 128, dtype=np.float64)[:, None]
    legend_rgb = _diff_fargkarta(np.repeat(legend_steg, 16, axis=1), float(max_abs_m))

    bild = _komponera_karta_med_legend(
        rgb,
        legend_rgb,
        titel=titel,
        kalla=kalla,
        legend_topp=f"+{max_abs_m:.2f} m",
        legend_mitt="0.00 m",
        legend_botten=f"-{max_abs_m:.2f} m",
    )

    mal = Path(ut_fil)
    mal.parent.mkdir(parents=True, exist_ok=True)
    bild.save(mal, format="PNG")
    return mal
