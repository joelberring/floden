"""Rasterlager för webben: sammanfogar rutor och ritar PNG-förhandsvisningar (T10).

Webben räknar inget nytt här. Rastren är resultatfiler från lösare eller kärnans jämförelser
(`karna.jamfor`); det här modulen lägger bara ihop rutorna till en bild och färgsätter den.
Bilden ligger i SWEREF 99 18 00 (EPSG:3011), samma rutnät som rastret, med nordkant överst.
Klienten ritar den direkt i SWEREF-koordinater.

Färgskalor: `vatten` (klasser med temats vattenfärger, djup i meter), `skillnad` (blå = minskning,
röd = ökning, 0 genomskinlig), `klass` (heltalsklasser, en färg per klass) och de kontinuerliga
`PALETTER`: `sekventiell` (cividis-lik, färgblindsäker; soltimmar och himmelsandel), `hastighet`,
`tid`, `stromtathet` (ekologins flaskhalsar) och `luft` (halter och vindförstärkning). Vilken skala
en roll får står i `ROLLER` (en plats för etikett, enhet och skala).
"""

from __future__ import annotations

import io
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from karna.geometri.lasa import las_byggnader
from karna.geometri.rasterfil import ruta_fran_geo
from markmodell.hinder_oppningar import BYGGNAD, brann_in_byggnader
from testsvit.k1_io import las_geotiff

MAX_PX = 2048
"""Standardvärde för längsta sida i översikts-PNG. Större lager nedskalas (block-max);
rådata-GeoTIFF och pekaravläsning förblir alltid i full upplösning."""

MIN_PNG_PIXLAR = 64
"""Minsta tillåtna värde för `FLODEN_MAX_PNG_PIXLAR`."""

FLODEN_MAX_PNG_PIXLAR_ENV = "FLODEN_MAX_PNG_PIXLAR"
"""Miljövariabel som styr största bildsida (px) i översikts-PNG (standard 2048, heltal >= 64)."""

_SEKVENTIELL: tuple[str, ...] = (
    "#00204d",
    "#31446b",
    "#666970",
    "#958f78",
    "#cbba69",
    "#ffea46",
)
PALETTER: dict[str, tuple[str, ...]] = {
    "sekventiell": _SEKVENTIELL,
    "hastighet": ("#440154", "#3b528b", "#21908d", "#5dc863", "#fde725"),
    "fara": ("#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"),
    "varme": ("#f7f4e8", "#fed976", "#fd8d3c", "#e31a1c", "#800026"),
    "tid": ("#ffffcc", "#fed976", "#fd8d3c", "#bd0026", "#4d004b"),
    "stromtathet": ("#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"),
    "luft": ("#0d0887", "#7e03a8", "#cc4678", "#f89441", "#f0f921"),
}
"""Kontinuerliga färgskalor (mörkt till ljust, utom `tid` där ljust är kort tid). Valda så att
skalan går att läsa även utan färgseende (ljushet ändras monotont)."""
_KLASSFARGER: tuple[str, ...] = ("#2e7d32", "#9ccc65", "#fdd835", "#fb8c00", "#c62828")
MAX_KLASSER = 9
_VATTEN_KLASSER: tuple[tuple[float, str], ...] = (
    (0.02, "#cfe8ff"),
    (0.1, "#7ec4f8"),
    (0.2, "#2f8be6"),
    (0.3, "#1a52b8"),
    (0.5, "#182b7a"),
    (1.0, "#3b1363"),
)
_VATTEN_ETIKETTER: dict[float, str] = {
    0.02: "0,02–0,10 m · ytavrinning",
    0.1: "0,10–0,20 m · märkbart vatten",
    0.2: "0,20–0,30 m · svårframkomligt",
    0.3: "0,30–0,50 m · ofarbart / risk",
    0.5: "0,50–1,00 m · allvarligt djup",
    1.0: "> 1,00 m · mycket djupt",
}
_SKILLNAD_MINSKNING = "#2b5d8c"
_SKILLNAD_OKNING = "#b4462b"

MASKADE_ROLLER = frozenset(
    {
        "soltimmar",
        "sammanhangande_soltimmar",
        "skuggmask",
        "svf",
        "max_djup",
        "fara",
        "utci_c",
        "t_mrt_c",
        "ekvivalent_ljudniva_dba",
    }
)
"""Markrasterroller där byggnadernas fotavtryck är tak, inte mark (maskas, märks "tak")."""

TAKKALLA_FOTAVTRYCK = "fotavtryck"
TAKKALLA_MARKSLAG = "markslag"


def takkalla_for_roll(roll: str) -> str:
    """Vad som är "tak" för rollen.

    Dagsljus (soltimmar, himmelsandel) räknas på ytmodellen, där en port i ett hus är täckt av
    taket: byggnadens fotavtryck (`byggnader.gpkg`) är tak. Vatten räknas på marken, där en port
    är öppen och vattnet rinner: bara det som är byggnad i markslaget är tak.
    """
    return (
        TAKKALLA_MARKSLAG
        if roll in {"max_djup", "fara", "hastighet", "ankomsttid"}
        else TAKKALLA_FOTAVTRYCK
    )

ETIKETTER: dict[str, tuple[str, str]] = {
    "soltimmar": ("Soltimmar på marken", "h"),
    "sammanhangande_soltimmar": ("Sammanhängande soltimmar", "h"),
    "skuggmask": ("Solläge vid klockslag", ""),
    "utci_c": ("Värmestress (UTCI)", "°C"),
    "t_mrt_c": ("Medelstrålningstemperatur (T_mrt)", "°C"),
    "ekvivalent_ljudniva_dba": ("Ekvivalent ljudnivå", "dB(A)"),
    "max_djup": ("Största vattendjup", "m"),
    "fara": ("Fara (djup × hastighet)", "m²/s"),
    "rannsten_flode": ("Rännstenar (1D-flöde)", "m³/s"),
    "svf": ("Andel himmel som syns", ""),
    "stromtathet": ("Strömtäthet (flaskhalsar)", ""),
    "kansighet": ("Känslighet för motstånd", ""),
    "lutning": ("Lutning", "grader"),
    "komfortklass": ("Vindkomfort (klass)", "klass"),
    "faraklass": ("Vindfara (klass)", "klass"),
    "forstarkning": ("Vindförstärkning på gånghöjd", "gånger"),
    "arsmedel": ("Årsmedelhalt", "µg/m³"),
    "hastighet": ("Vattnets hastighet", "m/s"),
    "ankomsttid": ("Tid till vattnet kommer", "s"),
    "tid_till_0_1m": ("Tid till 10 cm djup", "s"),
    "tid_till_0_2m": ("Tid till 20 cm djup", "s"),
    "trunkering": ("Kontroll: avskuren horisont", ""),
    "standardhojd": ("Kontroll: byggnad utan känd höjd", ""),
    "slutsanka": ("Kontroll: slutet område, vattnet har ingen väg ut", ""),
}

SKALA_FOR_ROLL: dict[str, str] = {
    "max_djup": "vatten",
    "hastighet": "hastighet",
    "fara": "fara",
    "utci_c": "varme",
    "t_mrt_c": "varme",
    "ekvivalent_ljudniva_dba": "fara",
    "ankomsttid": "tid",
    "tid_till_0_1m": "tid",
    "tid_till_0_2m": "tid",
    "stromtathet": "stromtathet",
    "kansighet": "stromtathet",
    "arsmedel": "luft",
    "forstarkning": "luft",
    "komfortklass": "klass",
    "faraklass": "klass",
    "skuggmask": "klass",
    "trunkering": "klass",
    "standardhojd": "klass",
    "slutsanka": "klass",
}
"""Roller som inte ska ha den förvalda sekventiella skalan (soltimmar, himmelsandel, lutning)."""

KONTROLLROLLER = frozenset({"trunkering", "standardhojd", "slutsanka"})
"""Kontrollager: de visar var ett resultat är osäkert, inte ett mått."""


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


def farg_for_roll(
    roll: str, *, skillnad: bool = False, lo: float | None = None, hi: float | None = None
) -> str:
    """Färgskalans namn för rollen; en klassroll med många värden får en kontinuerlig skala."""
    if skillnad:
        return "skillnad"
    farg = SKALA_FOR_ROLL.get(roll, "sekventiell")
    if farg == "klass" and lo is not None and hi is not None:
        try:
            klass_farger(lo, hi)
        except LagerFel:
            return "sekventiell"
    return farg


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


def max_png_pixlar(env: Mapping[str, str] | None = None) -> int:
    """Läser maxdimension (px) för översikts-PNG ur `FLODEN_MAX_PNG_PIXLAR` (standard 2048, >= 64).

    Ogiltigt värde (icke-heltal eller < 64) ger `LagerFel` (inga tysta fel).
    """
    kalla = os.environ if env is None else env
    raw = kalla.get(FLODEN_MAX_PNG_PIXLAR_ENV)
    if raw is None or not raw.strip():
        return MAX_PX
    text = raw.strip()
    try:
        val = int(text)
    except ValueError as exc:
        raise LagerFel(
            f"Ogiltigt värde i {FLODEN_MAX_PNG_PIXLAR_ENV}={raw!r}: "
            f"ange ett heltal >= {MIN_PNG_PIXLAR}."
        ) from exc
    if val < MIN_PNG_PIXLAR:
        raise LagerFel(
            f"Ogiltigt värde i {FLODEN_MAX_PNG_PIXLAR_ENV}={raw!r}: "
            f"måste vara ett heltal >= {MIN_PNG_PIXLAR}."
        )
    return val


def _validera_max_pixlar(max_pixlar: int | None) -> int:
    if max_pixlar is None:
        return max_png_pixlar()
    if (
        isinstance(max_pixlar, bool)
        or not isinstance(max_pixlar, int)
        or max_pixlar < MIN_PNG_PIXLAR
    ):
        raise LagerFel(
            f"max_pixlar måste vara ett heltal >= {MIN_PNG_PIXLAR}, fick {max_pixlar!r}."
        )
    return max_pixlar


def _mal_dimensioner(hojd: int, bredd: int, max_px: int) -> tuple[int, int]:
    """Deterministiska måldimensioner med bibehållet bildförhållande: max == max_px, min >= 1."""
    if hojd <= 0 or bredd <= 0:
        raise LagerFel(f"Ogiltiga rasterdimensioner: {hojd}x{bredd}.")
    if max(hojd, bredd) <= max_px:
        return hojd, bredd
    if hojd >= bredd:
        ny_h = max_px
        ny_b = min(bredd, max(1, round(bredd * max_px / hojd)))
    else:
        ny_b = max_px
        ny_h = min(hojd, max(1, round(hojd * max_px / bredd)))
    return ny_h, ny_b


def _block_kanter(storlek: int, ny_storlek: int) -> NDArray[np.intp]:
    """Heltalsexakta blockgränser som delar `[0, storlek)` i `ny_storlek` icke-tomma block."""
    return ((np.arange(ny_storlek + 1, dtype=np.int64) * storlek) // ny_storlek).astype(np.intp)


def _block_nedskala(
    raster: NDArray[np.float32],
    *,
    ny_h: int,
    ny_b: int,
    dold: NDArray[np.bool_] | None = None,
    lage: str = "max",
) -> tuple[NDArray[np.float32], NDArray[np.intp], NDArray[np.intp]]:
    """Nedskalar `raster` deterministiskt i radband med block-max (eller teckenbevarande max-|v|).

    Bevarar smala översvämmade gator och lågpunkter utan att smeta värden mot 0, håller `NaN`/
    `dold` transparenta och begränsar tillfälligt arbetsminne genom bandvis beräkning.
    """
    hojd, bredd = raster.shape
    r_kanter = _block_kanter(hojd, ny_h)
    c_kanter = _block_kanter(bredd, ny_b)
    c_start = c_kanter[:-1]
    ut = np.empty((ny_h, ny_b), dtype=np.float32)
    steg_rader = 256
    for i0 in range(0, ny_h, steg_rader):
        i1 = min(ny_h, i0 + steg_rader)
        r0 = int(r_kanter[i0])
        r1 = int(r_kanter[i1])
        del_r = np.array(raster[r0:r1, :], dtype=np.float32, copy=True)
        if dold is not None:
            del_r[dold[r0:r1, :]] = np.nan
        giltig = np.isfinite(del_r)
        lokala_r = r_kanter[i0:i1] - r0
        if lage == "absmax":
            max_in = np.where(giltig, del_r, np.float32(-np.inf))
            rad_max = np.maximum.reduceat(max_in, lokala_r, axis=0)
            b_max = np.maximum.reduceat(rad_max, c_start, axis=1)
            del max_in, rad_max
            min_in = np.where(giltig, del_r, np.float32(np.inf))
            rad_min = np.minimum.reduceat(min_in, lokala_r, axis=0)
            b_min = np.minimum.reduceat(rad_min, c_start, axis=1)
            del min_in, rad_min
            har_varde = np.isfinite(b_max)
            val = np.where(np.abs(b_min) > np.abs(b_max), b_min, b_max)
            val[~har_varde] = np.nan
            ut[i0:i1, :] = val
            del b_max, b_min, har_varde, val
        else:
            del_r[~giltig] = np.float32(-np.inf)
            rad_max = np.maximum.reduceat(del_r, lokala_r, axis=0)
            block = np.maximum.reduceat(rad_max, c_start, axis=1)
            block[block == np.float32(-np.inf)] = np.nan
            ut[i0:i1, :] = block
            del rad_max, block
        del del_r, giltig
    return ut, r_kanter, c_kanter


def _hex(rgb: str) -> NDArray[np.float32]:
    return np.array([int(rgb[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.float32)


def _nedsampla(
    raster: NDArray[np.float32], *, max_pixlar: int | None = None
) -> tuple[NDArray[np.float32], int]:
    max_px = _validera_max_pixlar(max_pixlar)
    hojd, bredd = raster.shape
    if max(hojd, bredd) <= max_px:
        return raster, 1
    faktor = max(1, math.ceil(max(hojd, bredd) / max_px))
    ny_h, ny_b = _mal_dimensioner(hojd, bredd, max_px)
    ned, _, _ = _block_nedskala(raster, ny_h=ny_h, ny_b=ny_b, lage="max")
    return ned, faktor


def _sekventiell(
    v: NDArray[np.float32], lo: float, hi: float, palett: Sequence[str] = _SEKVENTIELL
) -> NDArray[np.uint8]:
    stopp = np.stack([_hex(h) for h in palett])
    if hi <= lo:
        norm = np.zeros(v.shape, dtype=np.float32)
    else:
        norm = (v - np.float32(lo)) / np.float32(hi - lo)
    t = np.clip(np.nan_to_num(norm, nan=0.0), np.float32(0.0), np.float32(1.0)) * np.float32(
        len(stopp) - 1
    )
    del norm
    i0 = np.minimum(t.astype(np.intp), len(stopp) - 2)
    f = (t - i0.astype(np.float32))[..., None]
    del t
    rgb = stopp[i0] * (np.float32(1.0) - f) + stopp[i0 + 1] * f
    del i0, f
    ut = np.asarray(np.rint(rgb), dtype=np.uint8)
    del rgb
    return ut


def _vatten(v: NDArray[np.float32]) -> tuple[NDArray[np.uint8], NDArray[np.bool_]]:
    rgb = np.zeros((*v.shape, 3), dtype=np.uint8)
    ok = np.isfinite(v)
    synlig = ok & (v >= _VATTEN_KLASSER[0][0])
    for gräns, hexfarg in _VATTEN_KLASSER:
        rgb[ok & (v >= gräns)] = _hex(hexfarg).astype(np.uint8)
    return rgb, synlig


def klass_farger(lo: float, hi: float) -> list[tuple[int, str]]:
    """(klassvärde, färg) för heltalsklasserna lo..hi (högst `MAX_KLASSER`), grönt till rött."""
    f0, f1 = round(lo), round(hi)
    if not (math.isfinite(lo) and math.isfinite(hi)) or f1 < f0 or f1 - f0 + 1 > MAX_KLASSER:
        raise LagerFel(f"Klasserna {lo}..{hi} går inte att färglägga som klasser.")
    n = f1 - f0 + 1
    steg = len(_KLASSFARGER) - 1
    return [(f0 + i, _KLASSFARGER[round(i * steg / (n - 1)) if n > 1 else 0]) for i in range(n)]


def _klass(
    v: NDArray[np.float32], lo: float, hi: float
) -> tuple[NDArray[np.uint8], NDArray[np.bool_]]:
    rgb = np.zeros((*v.shape, 3), dtype=np.uint8)
    ok = np.isfinite(v)
    rund = np.rint(np.nan_to_num(v, nan=-1e9))
    for varde, hexfarg in klass_farger(lo, hi):
        rgb[ok & (rund == varde)] = _hex(hexfarg).astype(np.uint8)
    synlig = ok & (rund >= round(lo)) & (rund <= round(hi))
    return rgb, synlig


def _skillnad(
    v: NDArray[np.float32], gransvarde: float, troskel: float
) -> tuple[NDArray[np.uint8], NDArray[np.bool_]]:
    rgb = np.zeros((*v.shape, 3), dtype=np.uint8)
    ok = np.isfinite(v)
    synlig = ok & (np.abs(v) > troskel)
    mag = (
        np.clip(
            np.abs(np.nan_to_num(v)) / np.float32(gransvarde),
            np.float32(0.0),
            np.float32(1.0),
        )
        if gransvarde > 0
        else np.zeros(v.shape, dtype=np.float32)
    )
    vit = np.array([245.0, 245.0, 245.0], dtype=np.float32)
    for tecken, hexfarg in ((v < 0, _SKILLNAD_MINSKNING), (v > 0, _SKILLNAD_OKNING)):
        mask = ok & tecken
        if not mask.any():
            continue
        m = (mag[mask] * np.float32(0.75) + np.float32(0.25))[..., None]
        farg = vit * (np.float32(1.0) - m) + _hex(hexfarg) * m
        rgb[mask] = np.rint(farg).astype(np.uint8)
        del m, farg
    del mag
    return rgb, synlig


@dataclass(frozen=True)
class Bild:
    """En ritad förhandsvisning och var den har synliga celler."""

    png: bytes
    bredd_px: int
    hojd_px: int
    faktor: int
    synlig_px: tuple[int, int, int, int] | None
    """(kol_min, rad_min, kol_max, rad_max) i bildens pixlar (exklusivt övre gräns) eller None."""


def rita_png(
    raster: NDArray[np.float32],
    farg: str,
    *,
    lo: float,
    hi: float,
    troskel: float = 0.0,
    dold: NDArray[np.bool_] | None = None,
    max_pixlar: int | None = None,
) -> tuple[bytes, int, int, int]:
    """Ritar rastret som transparent RGBA-PNG. Returnerar (png, bredd, hojd, nedsamplingsfaktor).

    `lo`/`hi` är den gemensamma skalan (samma för före och efter, annars går de inte att jämföra);
    för `skillnad` är `hi` största absolutvärdet. Saknade värden är genomskinliga, och celler i
    `dold` (t.ex. byggnadernas tak, som inte är mark) likaså.
    """
    b = rita_bild(
        raster, farg, lo=lo, hi=hi, troskel=troskel, dold=dold, max_pixlar=max_pixlar
    )
    return b.png, b.bredd_px, b.hojd_px, b.faktor


def rita_bild(
    raster: NDArray[np.float32],
    farg: str,
    *,
    lo: float,
    hi: float,
    troskel: float = 0.0,
    dold: NDArray[np.bool_] | None = None,
    max_pixlar: int | None = None,
) -> Bild:
    """Som `rita_png` men även med de synliga cellernas utsträckning (för att zooma till dem)."""
    if raster.ndim != 2:
        raise LagerFel(f"Rastret måste vara 2D, fick form {raster.shape}.")
    if dold is not None and dold.shape != raster.shape:
        raise LagerFel(f"Masken {dold.shape} passar inte rastret {raster.shape}.")
    max_px = _validera_max_pixlar(max_pixlar)
    hojd, bredd = raster.shape
    nedskalad = max(hojd, bredd) > max_px
    r_kanter: NDArray[np.intp] | None = None
    c_kanter: NDArray[np.intp] | None = None
    if nedskalad:
        faktor = max(1, math.ceil(max(hojd, bredd) / max_px))
        ny_h, ny_b = _mal_dimensioner(hojd, bredd, max_px)
        lage = "absmax" if farg == "skillnad" else "max"
        v, r_kanter, c_kanter = _block_nedskala(
            raster, ny_h=ny_h, ny_b=ny_b, dold=dold, lage=lage
        )
    else:
        faktor = 1
        v = raster

    ok = np.isfinite(v)
    if farg == "vatten":
        rgb, synlig = _vatten(v)
    elif farg == "skillnad":
        rgb, synlig = _skillnad(v, hi, troskel)
    elif farg == "klass":
        rgb, synlig = _klass(v, lo, hi)
    elif farg in PALETTER:
        rgb = _sekventiell(v, lo, hi, PALETTER[farg])
        synlig = ok
    else:
        raise LagerFel(f"Okänd färgskala '{farg}'.")
    if dold is not None and not nedskalad:
        synlig = synlig & ~dold

    bild_h, bild_b = int(v.shape[0]), int(v.shape[1])
    rader = np.flatnonzero(synlig.any(axis=1))
    kol = np.flatnonzero(synlig.any(axis=0))
    if rader.size == 0:
        bbox: tuple[int, int, int, int] | None = None
    elif nedskalad and r_kanter is not None and c_kanter is not None:
        c0_src = int(c_kanter[int(kol[0])])
        r0_src = int(r_kanter[int(rader[0])])
        c1_src = int(c_kanter[int(kol[-1]) + 1])
        r1_src = int(r_kanter[int(rader[-1]) + 1])
        bbox = (
            c0_src // faktor,
            r0_src // faktor,
            math.ceil(c1_src / faktor),
            math.ceil(r1_src / faktor),
        )
    else:
        bbox = (int(kol[0]), int(rader[0]), int(kol[-1]) + 1, int(rader[-1]) + 1)

    rgba = np.empty((bild_h, bild_b, 4), dtype=np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = np.where(synlig, 255, 0).astype(np.uint8)
    del rgb, synlig, ok
    if nedskalad:
        del v

    buf = io.BytesIO()
    with Image.fromarray(rgba, mode="RGBA") as img:
        img.save(buf, format="PNG", optimize=True)
    del rgba
    png_bytes = buf.getvalue()
    buf.close()
    return Bild(png_bytes, bild_b, bild_h, faktor, bbox)


def _fotavtryck(mapp: Path, geo: Any) -> NDArray[np.bool_]:
    """Byggnadernas fotavtryck i en K1-ruta, inbrända som i `ytmodell_fran` (även portar)."""
    nollor = np.zeros((geo.hojd, geo.bredd), dtype=np.uint8)
    _, slag = brann_in_byggnader(
        np.zeros(nollor.shape, dtype=np.float32),
        nollor,
        ruta_fran_geo(geo),
        [b.till_byggnad() for b in las_byggnader(mapp)],
    )
    return np.asarray(slag == BYGGNAD)


def tak_mask(
    mosaik: Mosaik, rutor: Sequence[Path], kalla: str = TAKKALLA_MARKSLAG
) -> NDArray[np.bool_]:
    """Celler i `mosaik` som är tak i någon av K1-rutorna `rutor`.

    På byggnadens celler visar ett markraster byggnadens tak, inte mark: soltimmar på ett tak
    och djup i en byggnad är inte markvärden. `kalla` är `markslag` (celler med markslag 9 i
    `markslag.tif`; en port i huset är öppen och räknas som mark) eller `fotavtryck` (byggnadernas
    polygoner i `byggnader.gpkg`, samma som dagsljusets ytmodell; en port är täckt och räknas som
    tak). Ingenting räknas om utöver att polygonerna rastreras.
    Ligger rutorna på ett finare rutnät än mosaiken slås cellerna ihop (en cell är tak om någon
    av dess delceller är det).
    """
    px = mosaik.pixelstorlek_m
    u = mosaik.utbredning
    h, b = mosaik.raster.shape
    ut = np.zeros((h, b), dtype=np.bool_)
    for mapp in rutor:
        fil = mapp / "markslag.tif"
        if not fil.is_file():
            continue
        arr, geo = las_geotiff(fil)
        f = px / geo.pixelstorlek_x
        if f < 1.0 - 1e-9 or abs(f - round(f)) > 1e-6:
            raise LagerFel(
                f"{fil}: cellstorleken {geo.pixelstorlek_x} m passar inte lagrets {px} m."
            )
        fi = round(f)
        if kalla == TAKKALLA_FOTAVTRYCK:
            fot = _fotavtryck(mapp, geo)
        elif kalla == TAKKALLA_MARKSLAG:
            fot = np.asarray(arr) == BYGGNAD
        else:
            raise LagerFel(f"Okänd takkälla '{kalla}'.")
        hh, bb = (fot.shape[0] // fi) * fi, (fot.shape[1] // fi) * fi
        if fi > 1:
            fot = fot[:hh, :bb].reshape(hh // fi, fi, bb // fi, fi).any(axis=(1, 3))
        c0f, r0f = (geo.x_min - u.x_min) / px, (u.y_max - geo.y_max) / px
        if abs(c0f - round(c0f)) > 1e-3 or abs(r0f - round(r0f)) > 1e-3:
            raise LagerFel(f"{fil}: rutan ligger inte på lagrets cellrutnät.")
        c0, r0 = round(c0f), round(r0f)
        rs, cs = max(0, r0), max(0, c0)
        re, ce = min(h, r0 + fot.shape[0]), min(b, c0 + fot.shape[1])
        if re <= rs or ce <= cs:
            continue
        ut[rs:re, cs:ce] |= fot[rs - r0 : re - r0, cs - c0 : ce - c0]
    return ut


def teckenforklaring(farg: str, lo: float, hi: float, troskel: float = 0.0) -> dict[str, Any]:
    """Färgstopp för klientens teckenförklaring."""
    if farg == "vatten":
        return {
            "typ": "klasser",
            "klasser": [
                {"fran": g, "farg": f, "etikett": _VATTEN_ETIKETTER[g]}
                for g, f in _VATTEN_KLASSER
            ],
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
    if farg == "klass":
        n = len(klass_farger(lo, hi))
        return {
            "typ": "kategorier",
            "kategorier": [
                {
                    "varde": v,
                    "farg": f,
                    "etikett": "lägst" if i == 0 else "högst" if i == n - 1 else "",
                }
                for i, (v, f) in enumerate(klass_farger(lo, hi))
            ],
        }
    if farg not in PALETTER:
        raise LagerFel(f"Okänd färgskala '{farg}'.")
    return {"typ": "kontinuerlig", "min": lo, "max": hi, "stopp": list(PALETTER[farg])}


def skillnad_mosaik(fore: Mosaik, efter: Mosaik) -> Mosaik | None:
    """Efter minus före, cell för cell, när lagren ligger på samma rutnät (annars None).

    Används för roller som frågan inte jämför själv. Ingen ny beräkning: samma celler som visas
    i före och efter, där ett värde som saknas i någon av dem ger ett värde som saknas."""
    if fore.raster.shape != efter.raster.shape or fore.utbredning != efter.utbredning:
        return None
    if abs(fore.pixelstorlek_m - efter.pixelstorlek_m) > 1e-9:
        return None
    return Mosaik(
        np.asarray(efter.raster - fore.raster, dtype=np.float32),
        efter.utbredning,
        efter.pixelstorlek_m,
    )


def fara_mosaik(max_djup: Mosaik, hastighet: Mosaik) -> Mosaik | None:
    """Deterministisk härledd riskroll `fara = max_djup * hastighet` (m²/s), cell för cell.

    Bevarar `NaN` exakt där antingen `max_djup` eller `hastighet` saknas (icke-ändligt värde).
    Returnerar `None` om lagren inte ligger på samma rutnät.
    """
    if (
        max_djup.raster.shape != hastighet.raster.shape
        or max_djup.utbredning != hastighet.utbredning
    ):
        return None
    if abs(max_djup.pixelstorlek_m - hastighet.pixelstorlek_m) > 1e-9:
        return None
    h = np.asarray(max_djup.raster, dtype=np.float64)
    v = np.asarray(hastighet.raster, dtype=np.float64)
    giltig = np.isfinite(h) & np.isfinite(v)
    ut = np.full(h.shape, np.nan, dtype=np.float32)
    ut[giltig] = (h[giltig] * v[giltig]).astype(np.float32)
    return Mosaik(ut, max_djup.utbredning, max_djup.pixelstorlek_m)
