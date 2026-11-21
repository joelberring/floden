"""Rasterlager för webben: sammanfogar rutor och ritar PNG-förhandsvisningar (T10).

Webben räknar inget nytt här. Rastren är resultatfiler från lösare eller kärnans jämförelser
(`karna.jamfor`); det här modulen lägger bara ihop rutorna till en bild och färgsätter den.
Bilden ligger i SWEREF 99 18 00 (EPSG:3011), samma rutnät som rastret, med nordkant överst.
Klienten ritar den direkt i SWEREF-koordinater.

Färgskalor: `sekventiell` (cividis-lik, färgblindsäker), `vatten` (klasser med temats
vattenfärger, djup i meter) och `skillnad` (blå = minskning, röd = ökning, 0 genomskinlig).
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from testsvit.k1_io import las_geotiff

MAX_PX = 2048
"""Längsta sida i förhandsvisningen. Större lager nedsamplas (närmaste cell); filen är full."""

_SEKVENTIELL: tuple[str, ...] = (
    "#00204d",
    "#31446b",
    "#666970",
    "#958f78",
    "#cbba69",
    "#ffea46",
)
_VATTEN_KLASSER: tuple[tuple[float, str], ...] = (
    (0.02, "#e4edf6"),
    (0.1, "#c9d9ea"),
    (0.2, "#7fa3c8"),
    (0.3, "#4f7daa"),
    (0.5, "#2b5d8c"),
)
_SKILLNAD_MINSKNING = "#2b5d8c"
_SKILLNAD_OKNING = "#b4462b"

ETIKETTER: dict[str, tuple[str, str]] = {
    "soltimmar": ("Soltimmar", "h"),
    "max_djup": ("Maxdjup, blåytor", "m"),
    "svf": ("Himmelsfaktor (SVF)", ""),
    "lutning": ("Lutning", "grader"),
    "trunkering": ("Trunkerad horisont", ""),
    "standardhojd": ("Byggnad med standardhöjd", ""),
}


class LagerFel(Exception):
    """Rastret kan inte läggas ihop eller ritas."""


@dataclass(frozen=True)
class Utbredning:
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def som_dict(self) -> dict[str, float]:
        return {
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
        }


@dataclass(frozen=True)
class Mosaik:
    raster: NDArray[np.float32]
    """Rad 0 = norr. NaN där inget raster ligger eller värdet saknas."""
    utbredning: Utbredning
    pixelstorlek_m: float


def etikett_och_enhet(roll: str) -> tuple[str, str]:
    if roll in ETIKETTER:
        return ETIKETTER[roll]
    return roll.replace("_", " ").capitalize(), ""


def farg_for_roll(roll: str, *, skillnad: bool = False) -> str:
    if skillnad:
        return "skillnad"
    if roll == "max_djup":
        return "vatten"
    return "sekventiell"


def sammanfoga(filer: Sequence[Path]) -> Mosaik:
    """Lägger ihop GeoTIFF-rutor till ett raster.

    Där rutor överlappar (K1 har 25 m överlapp) väljs rutan där cellens mittpunkt ligger
    djupast inne; samma regel som kärnans jämförelser använder för att räkna en yta en gång.
    """
    if not filer:
        raise LagerFel("Inga rasterfiler att sammanfoga.")
    delar: list[tuple[NDArray[np.float32], float, float, float]] = []
    px: float | None = None
    for fil in filer:
        arr, geo = las_geotiff(fil)
        if abs(geo.pixelstorlek_x - geo.pixelstorlek_y) > 1e-9:
            raise LagerFel(f"{fil.name}: icke kvadratiska celler stöds inte.")
        if px is None:
            px = geo.pixelstorlek_x
        elif abs(px - geo.pixelstorlek_x) > 1e-9:
            raise LagerFel(
                f"Rutorna har olika upplösning ({px} och {geo.pixelstorlek_x} m); "
                "de kan inte läggas i en bild."
            )
        delar.append((np.asarray(arr, dtype=np.float32), geo.x_min, geo.y_max, px))
    assert px is not None
    x0 = min(x for _, x, _, _ in delar)
    y1 = max(y for _, _, y, _ in delar)
    x1 = max(x + a.shape[1] * px for a, x, _, _ in delar)
    y0 = min(y - a.shape[0] * px for a, _, y, _ in delar)
    bredd = round((x1 - x0) / px)
    hojd = round((y1 - y0) / px)
    ut = np.full((hojd, bredd), np.nan, dtype=np.float32)
    djupast = np.full((hojd, bredd), -np.inf, dtype=np.float32)
    for arr, x, y, _ in delar:
        c0 = (x - x0) / px
        r0 = (y1 - y) / px
        if abs(c0 - round(c0)) > 1e-3 or abs(r0 - round(r0)) > 1e-3:
            raise LagerFel("Rutorna ligger inte på samma cellrutnät.")
        c0i, r0i = round(c0), round(r0)
        h, b = arr.shape
        rr = np.arange(h, dtype=np.float32)
        cc = np.arange(b, dtype=np.float32)
        avst = np.minimum(
            np.minimum(rr[:, None] + 0.5, h - rr[:, None] - 0.5),
            np.minimum(cc[None, :] + 0.5, b - cc[None, :] - 0.5),
        )
        fonster = (slice(r0i, r0i + h), slice(c0i, c0i + b))
        battre = avst > djupast[fonster]
        ut[fonster] = np.where(battre, arr, ut[fonster])
        djupast[fonster] = np.where(battre, avst, djupast[fonster])
    return Mosaik(ut, Utbredning(x0, y0, x1, y1), float(px))


def varde_intervall(rastren: Sequence[NDArray[np.float32]]) -> tuple[float, float]:
    """Gemensamt (min, max) över ändliga värden; (0, 0) om inget värde finns."""
    lo, hi = math.inf, -math.inf
    for a in rastren:
        ok = a[np.isfinite(a)]
        if ok.size:
            lo = min(lo, float(ok.min()))
            hi = max(hi, float(ok.max()))
    if lo > hi:
        return 0.0, 0.0
    return lo, hi


def _hex(rgb: str) -> NDArray[np.float64]:
    return np.array([int(rgb[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.float64)


def _nedsampla(raster: NDArray[np.float32]) -> tuple[NDArray[np.float32], int]:
    faktor = max(1, math.ceil(max(raster.shape) / MAX_PX))
    return (raster[::faktor, ::faktor], faktor) if faktor > 1 else (raster, 1)


def _sekventiell(v: NDArray[np.float32], lo: float, hi: float) -> NDArray[np.uint8]:
    stopp = np.stack([_hex(h) for h in _SEKVENTIELL])
    norm = np.zeros(v.shape, dtype=np.float64) if hi <= lo else (v - lo) / (hi - lo)
    t = np.clip(np.nan_to_num(norm, nan=0.0), 0.0, 1.0) * (len(stopp) - 1)
    i0 = np.minimum(t.astype(int), len(stopp) - 2)
    f = (t - i0)[..., None]
    rgb = stopp[i0] * (1.0 - f) + stopp[i0 + 1] * f
    return np.asarray(np.rint(rgb), dtype=np.uint8)


def _vatten(v: NDArray[np.float32]) -> tuple[NDArray[np.uint8], NDArray[np.bool_]]:
    rgb = np.zeros((*v.shape, 3), dtype=np.uint8)
    synlig = np.isfinite(v) & (v >= _VATTEN_KLASSER[0][0])
    for gräns, hexfarg in _VATTEN_KLASSER:
        rgb[np.isfinite(v) & (v >= gräns)] = _hex(hexfarg).astype(np.uint8)
    return rgb, synlig


def _skillnad(
    v: NDArray[np.float32], gransvarde: float, troskel: float
) -> tuple[NDArray[np.uint8], NDArray[np.bool_]]:
    rgb = np.zeros((*v.shape, 3), dtype=np.uint8)
    ok = np.isfinite(v)
    synlig = ok & (np.abs(v) > troskel)
    mag = np.clip(np.abs(np.nan_to_num(v)) / gransvarde, 0.0, 1.0) if gransvarde > 0 else 0.0 * v
    vit = np.array([245.0, 245.0, 245.0])
    for tecken, hexfarg in ((v < 0, _SKILLNAD_MINSKNING), (v > 0, _SKILLNAD_OKNING)):
        m = (np.asarray(mag, dtype=np.float64) * 0.75 + 0.25)[..., None]
        farg = vit * (1.0 - m) + _hex(hexfarg) * m
        rgb[ok & tecken] = np.rint(farg[ok & tecken]).astype(np.uint8)
    return rgb, synlig


def rita_png(
    raster: NDArray[np.float32],
    farg: str,
    *,
    lo: float,
    hi: float,
    troskel: float = 0.0,
) -> tuple[bytes, int, int, int]:
    """Ritar rastret som transparent RGBA-PNG. Returnerar (png, bredd, hojd, nedsamplingsfaktor).

    `lo`/`hi` är den gemensamma skalan (samma för före och efter, annars går de inte att jämföra);
    för `skillnad` är `hi` största absolutvärdet. Saknade värden är genomskinliga.
    """
    v, faktor = _nedsampla(raster)
    ok = np.isfinite(v)
    if farg == "vatten":
        rgb, synlig = _vatten(v)
    elif farg == "skillnad":
        rgb, synlig = _skillnad(v, hi, troskel)
    elif farg == "sekventiell":
        rgb = _sekventiell(v, lo, hi)
        synlig = ok
    else:
        raise LagerFel(f"Okänd färgskala '{farg}'.")
    rgba = np.zeros((*v.shape, 4), dtype=np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = np.where(synlig, 255, 0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue(), int(v.shape[1]), int(v.shape[0]), faktor


def teckenforklaring(farg: str, lo: float, hi: float, troskel: float = 0.0) -> dict[str, Any]:
    """Färgstopp för klientens teckenförklaring."""
    if farg == "vatten":
        return {
            "typ": "klasser",
            "klasser": [{"fran": g, "farg": f, "etikett": f"≥ {g:g}"} for g, f in _VATTEN_KLASSER],
        }
    if farg == "skillnad":
        return {
            "typ": "divergerande",
            "min": -hi,
            "max": hi,
            "troskel": troskel,
            "farg_minskning": _SKILLNAD_MINSKNING,
            "farg_okning": _SKILLNAD_OKNING,
        }
    return {"typ": "kontinuerlig", "min": lo, "max": hi, "stopp": list(_SEKVENTIELL)}
