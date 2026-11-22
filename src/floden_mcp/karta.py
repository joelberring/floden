"""Serverrenderad karta med fast kartografi för MCP-verktyget `karta_bild`.

Kartan ritas av Floden ur lagrets eget raster (samma raster, färgskala och takmask som webbens
kartlager, `webb.resultatlager`); AI:n väljer bara vilket lager och vilket utsnitt. Kartografin är
fast och oberoende av datorns typsnitt: Pillows inbyggda bitmapptypsnitt (Latin-1) i dubbel storlek.

Innehåll: titel, kartbild (norr uppåt), byggnader (konturer; ny byggnad orange, ändrad brun),
koordinatticks i SWEREF 99 18 00 (EPSG:3011), skalstock, nordpil, teckenförklaring med enhet och
värdeskala, och fottext med körnings-id, kodversion och markering när metoden bara är en skiss.
"""

from __future__ import annotations

import io
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from webb.resultatlager import rita_bild

_BG = (255, 255, 255)
_TOM = (236, 236, 236)
_RAM = (60, 60, 60)
_TEXT = (25, 25, 25)
_SVAG = (95, 95, 95)
_BYGGNAD = (70, 70, 70)
_NY = (180, 70, 43)
_ANDRAD = (140, 90, 0)
_TEXTSKALA = 2
_LEGEND_B = 270


class KartaFel(Exception):
    """Kartan kan inte ritas."""


def _latin1(text: str) -> str:
    s = (
        text.replace("−", "-")
        .replace("–", "-")
        .replace("≥", ">=")
        .replace("≤", "<=")
        .replace("→", "->")
    )
    return s.encode("latin-1", errors="replace").decode("latin-1")


_FONT = ImageFont.load_default_imagefont()  # bitmapp, Latin-1, samma på alla datorer


def _text(img: Image.Image, xy: tuple[int, int], s: str, fill: tuple[int, int, int] = _TEXT) -> int:
    """Ritar text i dubbel storlek; returnerar textens bredd i pixlar."""
    s = _latin1(s)
    vl, _t, r, b = _FONT.getbbox(s)
    if r <= vl:
        return 0
    mask = Image.new("L", (r, b + 2), 0)
    ImageDraw.Draw(mask).text((0, 0), s, fill=255, font=_FONT)
    mask = mask.resize(
        (mask.width * _TEXTSKALA, mask.height * _TEXTSKALA), Image.Resampling.NEAREST
    )
    img.paste(Image.new("RGB", mask.size, fill), xy, mask)
    return int(mask.width)


def _radbryt(s: str, tecken: int) -> list[str]:
    """Radbryter vid blanksteg så att varje rad får högst `tecken` tecken."""
    rader: list[str] = []
    rad = ""
    for ord_ in s.split():
        if rad and len(rad) + 1 + len(ord_) > tecken:
            rader.append(rad)
            rad = ord_
        else:
            rad = f"{rad} {ord_}".strip()
    if rad:
        rader.append(rad)
    return rader


def _textbredd(s: str) -> int:
    return int(_FONT.getbbox(_latin1(s))[2]) * _TEXTSKALA


def _fint(v: float) -> str:
    """Tal utan onödiga decimaler (skalmärken)."""
    return f"{v:.3g}".replace(".", ",")


def _fint_tal(v: float, steg: float) -> str:
    dec = max(0, 1 - int(math.floor(math.log10(steg)))) if steg > 0 else 2
    return f"{v:.{min(dec, 3)}f}".replace(".", ",")


def _fint_steg(spann_m: float) -> float:
    """Ett runt tickavstånd (1-2-5) som ger omkring fem ticks."""
    grov = spann_m / 5.0
    exp = 10 ** math.floor(math.log10(grov))
    for m in (1, 2, 5, 10):
        if m * exp >= grov:
            return float(m * exp)
    return float(10 * exp)


def _rgba(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _farg(hexa: str) -> tuple[int, int, int]:
    return (int(hexa[1:3], 16), int(hexa[3:5], 16), int(hexa[5:7], 16))


def _legend(duk: Image.Image, x: int, y: int, h: int, lager: Mapping[str, Any]) -> int:
    """Ritar teckenförklaringen och returnerar nästa lediga y."""
    tf = lager["teckenforklaring"]
    enhet = lager["enhet"]
    d = ImageDraw.Draw(duk)
    rubrik = _radbryt(f"{lager['etikett']}" + (f" ({enhet})" if enhet else ""), 21)
    for i, r in enumerate(rubrik):
        _text(duk, (x, y + i * 18), r)
    y0 = y + 18 * len(rubrik) + 12
    hojd = max(120, min(h - 160, 240))
    bredd = 26
    if tf["typ"] == "klasser":
        for i, k in enumerate(tf["klasser"]):
            yy = y0 + i * 26
            d.rectangle([x, yy, x + bredd, yy + 20], fill=_farg(k["farg"]), outline=_RAM)
            _text(duk, (x + bredd + 8, yy + 3), k["etikett"] + (f" {enhet}" if enhet else ""))
        _text(duk, (x, y0 + len(tf["klasser"]) * 26 + 6), "Under 0,02 m: ej markerat")
        return y0 + len(tf["klasser"]) * 26 + 34
    if tf["typ"] == "kontinuerlig":
        stopp = [_farg(s) for s in tf["stopp"]]
        lo, hi = float(tf["min"]), float(tf["max"])
        rader = np.linspace(1.0, 0.0, hojd)
        farg = np.zeros((hojd, 1, 3), dtype=np.float64)
        pos = rader * (len(stopp) - 1)
        for i, p in enumerate(pos):
            i0 = min(int(p), len(stopp) - 2)
            f = p - i0
            farg[i, 0] = np.array(stopp[i0]) * (1 - f) + np.array(stopp[i0 + 1]) * f
        rampbild = Image.fromarray(
            np.repeat(np.rint(farg).astype(np.uint8), bredd, axis=1), mode="RGB"
        )
        duk.paste(rampbild, (x, y0))
        d.rectangle([x, y0, x + bredd, y0 + hojd - 1], outline=_RAM)
        steg = _fint_steg(hi - lo) if hi > lo else 1.0
        v = math.ceil(lo / steg) * steg
        while v <= hi + 1e-9:
            frac = 0.0 if hi <= lo else (v - lo) / (hi - lo)
            yy = y0 + int(round((1 - frac) * (hojd - 1)))
            d.line([x + bredd, yy, x + bredd + 5, yy], fill=_RAM)
            _text(duk, (x + bredd + 9, yy - 6), _fint_tal(v, steg))
            v += steg
        _text(duk, (x, y0 + hojd + 8), f"Skala {_fint(lo)} till {_fint(hi)}")
        return y0 + hojd + 40
    # divergerande (skillnad)
    hi = float(tf["max"])
    minsk, okn = _farg(tf["farg_minskning"]), _farg(tf["farg_okning"])
    vit = np.array([245.0, 245.0, 245.0])
    rampa = np.zeros((hojd, bredd, 3), dtype=np.uint8)
    for i in range(hojd):
        t = 1.0 - 2.0 * i / max(1, hojd - 1)  # +1 överst (ökning) .. -1 nederst (minskning)
        m = abs(t) * 0.75 + (0.25 if t != 0 else 0.0)
        mal = np.array(okn if t > 0 else minsk, dtype=np.float64)
        rampa[i, :, :] = np.rint(vit * (1 - m) + mal * m).astype(np.uint8)
    duk.paste(Image.fromarray(rampa, mode="RGB"), (x, y0))
    d.rectangle([x, y0, x + bredd, y0 + hojd - 1], outline=_RAM)
    steg = _fint_steg(2 * hi) / 1.0 if hi > 0 else 1.0
    v = -math.floor(hi / steg) * steg
    while v <= hi + 1e-9:
        frac = 0.5 if hi <= 0 else 0.5 + v / (2 * hi)
        yy = y0 + int(round((1 - frac) * (hojd - 1)))
        d.line([x + bredd, yy, x + bredd + 5, yy], fill=_RAM)
        _text(duk, (x + bredd + 9, yy - 6), ("+" if v > 0 else "") + _fint_tal(v, steg))
        v += steg
    _text(duk, (x, y0 + hojd + 8), "Rött: ökning")
    _text(duk, (x, y0 + hojd + 26), "Blått: minskning")
    tr = float(tf.get("troskel", 0.0))
    if tr > 0:
        _text(duk, (x, y0 + hojd + 44), f"Under +-{_fint(tr)}: ej")
        _text(duk, (x, y0 + hojd + 62), "markerat")
        return y0 + hojd + 90
    return y0 + hojd + 50


def _byggnader(
    kartbild: Image.Image,
    byggnader: Sequence[Mapping[str, Any]],
    x_min: float,
    y_max: float,
    px_per_m: float,
) -> None:
    """Byggnadskonturer direkt i kartbilden (klipps av bildens kant)."""
    d = ImageDraw.Draw(kartbild)
    for status, farg, bredd in (
        ("oforandrad", _BYGGNAD, 1),
        ("andrad", _ANDRAD, 2),
        ("ny", _NY, 3),
    ):
        for b in byggnader:
            if b.get("status", "oforandrad") != status:
                continue
            for ring in [b["ring"], *b.get("hal", [])]:
                pts = [((e - x_min) * px_per_m, (y_max - n) * px_per_m) for e, n in ring]
                if len(pts) >= 2:
                    d.line([*pts, pts[0]], fill=farg, width=bredd)


def rendera_karta(
    *,
    raster: NDArray[np.float32],
    tak: NDArray[np.bool_] | None,
    lager: Mapping[str, Any],
    byggnader: Sequence[Mapping[str, Any]] | None,
    korning_id: str,
    kodversion: str,
    skiss: bool,
    utsnitt: tuple[float, float, float, float] | None = None,
    max_px: int = 900,
) -> tuple[bytes, dict[str, Any]]:
    """Ritar lagret som PNG. Returnerar (png, meta) där meta beskriver utsnitt och upplösning."""
    u = lager["utbredning"]
    px = float(lager["pixelstorlek_m"])
    ox_m, oy_m = float(u["x_min"]), float(u["y_max"])
    hojd_c, bredd_c = raster.shape
    c0, c1, r0, r1 = 0, bredd_c, 0, hojd_c
    if utsnitt is not None:
        x0, y0, x1, y1 = utsnitt
        c0 = max(0, int(math.floor((x0 - ox_m) / px)))
        c1 = min(bredd_c, int(math.ceil((x1 - ox_m) / px)))
        r0 = max(0, int(math.floor((oy_m - y1) / px)))
        r1 = min(hojd_c, int(math.ceil((oy_m - y0) / px)))
        if c1 <= c0 or r1 <= r0:
            raise KartaFel("Utsnittet ligger utanför lagrets utsträckning.")
    sub = raster[r0:r1, c0:c1]
    tak_sub = None if tak is None else tak[r0:r1, c0:c1]
    tf = lager["teckenforklaring"]
    bild = rita_bild(
        sub,
        lager["farg"],
        lo=float(lager["skala"]["min"]),
        hi=float(lager["skala"]["max"]),
        troskel=float(tf.get("troskel", 0.0)) if tf["typ"] == "divergerande" else 0.0,
        dold=tak_sub,
    )
    rgba = _rgba(bild.png)
    # förhandsrastret kan vara nedsamplat; kartan ritas i dess upplösning
    px_bild = px * bild.faktor if bild.faktor > 1 else px
    h_c, b_c = rgba.height, rgba.width
    zoom = max(1, min(8, max_px // max(h_c, b_c)))
    kart_b, kart_h = b_c * zoom, h_c * zoom
    px_per_m = zoom / px_bild

    rand_v, rand_t, rand_n = 110, 56, 100
    duk_b = rand_v + kart_b + 24 + _LEGEND_B
    duk_h = rand_t + kart_h + rand_n
    duk = Image.new("RGB", (duk_b, max(duk_h, rand_t + 460)), _BG)
    # bakgrund (saknat värde och tak syns som ljusgrått)
    duk.paste(Image.new("RGB", (kart_b, kart_h), _TOM), (rand_v, rand_t))
    stor = rgba.resize((kart_b, kart_h), Image.Resampling.NEAREST)
    duk.paste(stor.convert("RGB"), (rand_v, rand_t), stor.split()[3])
    d = ImageDraw.Draw(duk)
    d.rectangle([rand_v - 1, rand_t - 1, rand_v + kart_b, rand_t + kart_h], outline=_RAM)

    x_min = ox_m + c0 * px
    y_max = oy_m - r0 * px
    x_max = x_min + b_c * px_bild
    y_min = y_max - h_c * px_bild
    if byggnader:
        kb = duk.crop((rand_v, rand_t, rand_v + kart_b, rand_t + kart_h))
        _byggnader(kb, byggnader, x_min, y_max, px_per_m)
        duk.paste(kb, (rand_v, rand_t))
        d = ImageDraw.Draw(duk)

    # koordinatticks (EPSG:3011)
    steg = _fint_steg(max(x_max - x_min, y_max - y_min))
    e = math.ceil(x_min / steg) * steg
    while e <= x_max:
        xx = rand_v + int(round((e - x_min) * px_per_m))
        d.line([xx, rand_t + kart_h, xx, rand_t + kart_h + 5], fill=_RAM)
        _text(duk, (xx - _textbredd(f"{e:.0f}") // 2, rand_t + kart_h + 8), f"{e:.0f}", _SVAG)
        e += steg
    n = math.ceil(y_min / steg) * steg
    while n <= y_max:
        yy = rand_t + int(round((y_max - n) * px_per_m))
        d.line([rand_v - 5, yy, rand_v, yy], fill=_RAM)
        _text(duk, (rand_v - 8 - _textbredd(f"{n:.0f}"), yy - 6), f"{n:.0f}", _SVAG)
        n += steg

    # skalstock
    langd_m = _fint_steg((x_max - x_min) / 2.5) / 1.0
    sb = int(round(langd_m * px_per_m))
    sx, sy = rand_v + 12, rand_t + kart_h - 22
    d.rectangle([sx - 6, sy - 24, sx + sb + 70, sy + 12], fill=(255, 255, 255))
    d.line([sx, sy, sx + sb, sy], fill=_TEXT, width=3)
    d.line([sx, sy - 5, sx, sy + 5], fill=_TEXT, width=2)
    d.line([sx + sb, sy - 5, sx + sb, sy + 5], fill=_TEXT, width=2)
    _text(duk, (sx, sy - 22), f"{_fint(langd_m)} m")

    # nordpil (norr är uppåt i kartan)
    nx, ny = rand_v + kart_b - 28, rand_t + 18
    d.rectangle([nx - 16, ny - 8, nx + 16, ny + 62], fill=(255, 255, 255))
    d.polygon([(nx, ny + 4), (nx - 9, ny + 38), (nx, ny + 30), (nx + 9, ny + 38)], fill=_TEXT)
    _text(duk, (nx - 6, ny + 42), "N")

    # titel, teckenförklaring, fot
    scen = (
        f"{lager['efter']} - {lager['fore']}" if lager["typ"] == "skillnad" else lager["scenario"]
    )
    typtext = {"bas": "bas", "scenario": "scenario", "skillnad": "skillnad (efter minus före)"}[
        lager["typ"]
    ]
    rubrik = f"{lager['etikett']}, {typtext}: {scen}"
    _text(duk, (rand_v, 10), rubrik.replace("_", " "))
    _text(duk, (rand_v, 32), f"Körning {korning_id}  |  EPSG:3011", _SVAG)
    ly = _legend(duk, rand_v + kart_b + 24, rand_t, kart_h, lager) + 4
    if byggnader:
        for i, (txt, farg) in enumerate(
            (("Byggnad (tak)", _BYGGNAD), ("Ny byggnad", _NY), ("Ändrad byggnad", _ANDRAD))
        ):
            yy = ly + i * 22
            lx = rand_v + kart_b + 24
            d.rectangle([lx, yy + 2, lx + 22, yy + 14], outline=farg, width=2)
            _text(duk, (lx + 30, yy), txt)
    fy = rand_t + kart_h + 34
    for k, rad in enumerate(
        (
            f"Floden {kodversion}. Ritad ur körningens GeoTIFF.",
            "Ingen AI har räknat eller ritat talen.",
        )
    ):
        _text(duk, (rand_v, fy + k * 20), rad, _SVAG)
    if skiss:
        _text(duk, (rand_v, fy + 40), "SKISS: metoden är inte klassad för formell rapport.", _NY)
    ut = io.BytesIO()
    duk.save(ut, format="PNG", optimize=True)
    return ut.getvalue(), {
        "bild_px": [duk.width, duk.height],
        "kartutsnitt": [x_min, y_min, x_max, y_max],
        "kartutsnitt_beskrivning": "[e_min, n_min, e_max, n_max] i EPSG:3011 (m)",
        "cellstorlek_m": px_bild,
        "enhet": lager["enhet"] or None,
        "skala": lager["skala"],
        "nedsampling": bild.faktor,
        "norr": "uppat",
        "skalstock_m": langd_m,
    }
