"""Enhetligt system för tryckfärdiga rapportfigurer i ren Pillow + NumPy.

Färgpaletten bygger på Flodens nordiska naturkänsla (varm linnebakgrund,
skogsbläckstypografi och varsamma, färgblindsvänliga naturtoner) och använder
paketets inbäddade typsnitt i ``src/rapport/typsnitt/`` (Gelasio för rubrik,
Liberation Sans för teckenförklaring, skalstock och spårbarhetsfot).

Alla tal, klassgränser och skalor är deterministiska och härleds direkt ur
rasterdata och ``GeoTransformInfo`` i SWEREF 99 18 00 (EPSG:3011) / RH 2000.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from testsvit.k1_io import GeoTransformInfo

# ---------------------------------------------------------------------- Palett

_TYPSNITT_MAPP: Final[Path] = Path(__file__).parent / "typsnitt"

FARG_BAKGRUND: Final[tuple[int, int, int]] = (0xF7, 0xF5, 0xEE)  # #F7F5EE varmt linnepapper
FARG_KARTYTA: Final[tuple[int, int, int]] = (0xEF, 0xEC, 0xE3)  # #EFECE3 ram/kartyta
FARG_LINJE: Final[tuple[int, int, int]] = (0xD6, 0xD2, 0xC4)  # #D6D2C4 fin linje
FARG_TEXT: Final[tuple[int, int, int]] = (0x1D, 0x24, 0x1C)  # #1D241C djup skogsbläck
FARG_TEXT_SEK: Final[tuple[int, int, int]] = (0x4A, 0x54, 0x47)  # #4A5447 sekundär text
FARG_POLYGON: Final[tuple[int, int, int]] = (0x2F, 0x4A, 0x2C)  # #2F4A2C projektpolygon
FARG_BYGGNAD: Final[tuple[int, int, int]] = (0xD4, 0xCF, 0xC2)  # #D4CFC2 byggnadskropp
FARG_BYGGNAD_KANT: Final[tuple[int, int, int]] = (0x9B, 0x95, 0x86)  # #9B9586 byggnadskontur
FARG_BYGGNAD_NY: Final[tuple[int, int, int]] = (0xB4, 0x46, 0x2B)  # #B4462B ny byggnad
FARG_BYGGNAD_ANDRAD: Final[tuple[int, int, int]] = (0x8C, 0x5A, 0x00)  # #8C5A00 ändrad byggnad
FARG_BADGE_BG: Final[tuple[int, int, int]] = (0xE6, 0xEA, 0xE1)  # #E6EAE1 statusmärke bakgrund
FARG_BADGE_KANT: Final[tuple[int, int, int]] = (0xB8, 0xC4, 0xB0)  # #B8C4B0 statusmärke ram
FARG_SWATCH_KANT: Final[tuple[int, int, int]] = (0xB8, 0xB3, 0xA4)  # #B8B3A4 färgruta ram

_SKALSTOCK_VAL_M: Final[tuple[int, ...]] = (
    2,
    4,
    5,
    10,
    20,
    25,
    40,
    50,
    100,
    200,
    250,
    400,
    500,
    1000,
    2000,
    5000,
)


def hex_till_rgb(hexa: str) -> tuple[int, int, int]:
    """Konverterar ``#RRGGBB`` (eller ``RRGGBB``) till ``(r, g, b)``."""
    s = hexa.lstrip("#")
    if len(s) != 6:
        raise FigurFel(f"Ogiltig hexfärg '{hexa}'; förväntade 6 hex-tecken.")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


class FigurFel(Exception):
    """Fel vid validering eller rendering av en rapportfigur."""


@dataclass(frozen=True)
class FargKlass:
    """En klass i en deklarativ färgskala.

    En cell tillhör klassen om ``min_varde <= x < max_varde`` (med ``None`` som
    öppen gräns; sista klassen med ``max_varde=None`` fångar ``x >= min_varde``).
    """

    min_varde: float | None
    max_varde: float | None
    farg: tuple[int, int, int]
    etikett: str


@dataclass(frozen=True)
class FigurStil:
    """Deklarativ kartstil för en domän och storhet."""

    namn: str
    doman: str
    storhet: str
    enhet: str
    klasser: tuple[FargKlass, ...]
    tomvarde_farg: tuple[int, int, int] = FARG_KARTYTA
    tomvarde_etikett: str = "Inget värde"


# ---------------------------------------------------------------------- Stilar

FIGURSTILAR: Final[dict[str, FigurStil]] = {
    "skyfall_djup": FigurStil(
        namn="skyfall_djup",
        doman="skyfall",
        storhet="Vattendjup",
        enhet="m",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Torrt (< 0,05 m)",
        klasser=(
            FargKlass(0.05, 0.15, hex_till_rgb("#DCEBE6"), "0,05 – 0,15 m"),
            FargKlass(0.15, 0.30, hex_till_rgb("#8FB9AC"), "0,15 – 0,30 m"),
            FargKlass(0.30, 0.50, hex_till_rgb("#4A7D8C"), "0,30 – 0,50 m"),
            FargKlass(0.50, None, hex_till_rgb("#234B66"), "≥ 0,50 m"),
        ),
    ),
    "skyfall_ankomsttid": FigurStil(
        namn="skyfall_ankomsttid",
        doman="skyfall",
        storhet="Ankomsttid",
        enhet="s",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Ej översvämmad",
        klasser=(
            FargKlass(0.0, 600.0, hex_till_rgb("#234B66"), "0 – 600 s (0 – 10 min)"),
            FargKlass(600.0, 1200.0, hex_till_rgb("#4A7D8C"), "600 – 1 200 s (10 – 20 min)"),
            FargKlass(1200.0, 2400.0, hex_till_rgb("#8FB9AC"), "1 200 – 2 400 s (20 – 40 min)"),
            FargKlass(2400.0, None, hex_till_rgb("#DCEBE6"), "≥ 2 400 s (≥ 40 min)"),
        ),
    ),
    "skyfall_fara": FigurStil(
        namn="skyfall_fara",
        doman="skyfall",
        storhet="Skyfallsfara (djup × hastighet)",
        enhet="m²/s",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Låg / torr (< 0,05 m²/s)",
        klasser=(
            FargKlass(0.05, 0.25, hex_till_rgb("#E8DEC8"), "0,05 – 0,25 m²/s"),
            FargKlass(0.25, 0.50, hex_till_rgb("#D6B463"), "0,25 – 0,50 m²/s"),
            FargKlass(0.50, 1.00, hex_till_rgb("#B86A4B"), "0,50 – 1,00 m²/s"),
            FargKlass(1.00, None, hex_till_rgb("#8C3B2B"), "≥ 1,00 m²/s"),
        ),
    ),
    "skyfall_skillnad": FigurStil(
        namn="skyfall_skillnad",
        doman="skyfall",
        storhet="Skillnad i vattendjup",
        enhet="m",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Inget värde",
        klasser=(
            FargKlass(None, -0.20, hex_till_rgb("#234B66"), "≤ −0,20 m (minskning)"),
            FargKlass(-0.20, -0.05, hex_till_rgb("#4A7D8C"), "−0,20 – −0,05 m"),
            FargKlass(-0.05, -0.01, hex_till_rgb("#8FB9AC"), "−0,05 – −0,01 m"),
            FargKlass(-0.01, 0.01, FARG_KARTYTA, "−0,01 – +0,01 m (oförändrat)"),
            FargKlass(0.01, 0.05, hex_till_rgb("#D6B463"), "+0,01 – +0,05 m"),
            FargKlass(0.05, 0.20, hex_till_rgb("#B86A4B"), "+0,05 – +0,20 m"),
            FargKlass(0.20, None, hex_till_rgb("#8C3B2B"), "≥ +0,20 m (ökning)"),
        ),
    ),
    "dagsljus_soltimmar": FigurStil(
        namn="dagsljus_soltimmar",
        doman="dagsljus",
        storhet="Direkt sol på mark",
        enhet="tim",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Inget värde",
        klasser=(
            FargKlass(0.0, 2.0, hex_till_rgb("#4A524C"), "0 – 2 tim"),
            FargKlass(2.0, 4.0, hex_till_rgb("#6B7D6D"), "2 – 4 tim"),
            FargKlass(4.0, 6.0, hex_till_rgb("#8CA88E"), "4 – 6 tim"),
            FargKlass(6.0, 8.0, hex_till_rgb("#D9CF6A"), "6 – 8 tim"),
            FargKlass(8.0, None, hex_till_rgb("#F2E89B"), "≥ 8 tim"),
        ),
    ),
    "dagsljus_svf": FigurStil(
        namn="dagsljus_svf",
        doman="dagsljus",
        storhet="Himmelsfaktor (SVF)",
        enhet="andel, 0–1",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Inget värde",
        klasser=(
            FargKlass(0.0, 0.2, hex_till_rgb("#4A524C"), "0,0 – 0,2 (0 – 20 %)"),
            FargKlass(0.2, 0.4, hex_till_rgb("#6B7D6D"), "0,2 – 0,4 (20 – 40 %)"),
            FargKlass(0.4, 0.6, hex_till_rgb("#8CA88E"), "0,4 – 0,6 (40 – 60 %)"),
            FargKlass(0.6, 0.8, hex_till_rgb("#D9CF6A"), "0,6 – 0,8 (60 – 80 %)"),
            FargKlass(0.8, None, hex_till_rgb("#F2E89B"), "0,8 – 1,0 (80 – 100 %)"),
        ),
    ),
    "ekologi_stromtathet": FigurStil(
        namn="ekologi_stromtathet",
        doman="ekologi",
        storhet="Strömtäthet",
        enhet="relativ, 0–1",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Ingen ström / hinder",
        klasser=(
            FargKlass(0.02, 0.25, hex_till_rgb("#DCE6D5"), "0,02 – 0,25"),
            FargKlass(0.25, 0.50, hex_till_rgb("#9CB88A"), "0,25 – 0,50"),
            FargKlass(0.50, 0.75, hex_till_rgb("#3E6334"), "0,50 – 0,75"),
            FargKlass(0.75, None, hex_till_rgb("#1F3B1E"), "≥ 0,75"),
        ),
    ),
    "luft_vindkomfort": FigurStil(
        namn="luft_vindkomfort",
        doman="luft",
        storhet="Vindkomfort (NEN 8100)",
        enhet="klass",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Byggnad / inget värde",
        klasser=(
            FargKlass(-0.5, 0.5, hex_till_rgb("#DCEBE6"), "Klass A (0 · sitta länge)"),
            FargKlass(0.5, 1.5, hex_till_rgb("#9CB88A"), "Klass B (1 · sitta kort)"),
            FargKlass(1.5, 2.5, hex_till_rgb("#D6B463"), "Klass C (2 · promenad)"),
            FargKlass(2.5, 3.5, hex_till_rgb("#B86A4B"), "Klass D (3 · snabb gång)"),
            FargKlass(3.5, 4.5, hex_till_rgb("#8C3B2B"), "Klass E (4 · obehaglig)"),
        ),
    ),
    "buller_dba": FigurStil(
        namn="buller_dba",
        doman="buller",
        storhet="Ekvivalent ljudnivå",
        enhet="dBA",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Inget värde",
        klasser=(
            FargKlass(None, 50.0, hex_till_rgb("#E8DEC8"), "< 50 dBA"),
            FargKlass(50.0, 55.0, hex_till_rgb("#DFCA95"), "50 – 55 dBA"),
            FargKlass(55.0, 60.0, hex_till_rgb("#D6B463"), "55 – 60 dBA"),
            FargKlass(60.0, 65.0, hex_till_rgb("#B86A4B"), "60 – 65 dBA"),
            FargKlass(65.0, None, hex_till_rgb("#8C3B2B"), "≥ 65 dBA"),
        ),
    ),
}


def hamta_figurstil(stil: str | FigurStil) -> FigurStil:
    """Returnerar ``FigurStil`` från namn eller befintligt stilobjekt."""
    if isinstance(stil, FigurStil):
        return stil
    if stil not in FIGURSTILAR:
        tillgangliga = ", ".join(sorted(FIGURSTILAR))
        raise FigurFel(f"Okänd figurstil '{stil}'. Tillgängliga stilar: {tillgangliga}.")
    return FIGURSTILAR[stil]


_ROLL_TILL_STIL: Final[dict[str, str]] = {
    "max_djup": "skyfall_djup",
    "ankomsttid": "skyfall_ankomsttid",
    "tid_till_0_1m": "skyfall_ankomsttid",
    "tid_till_0_2m": "skyfall_ankomsttid",
    "fara": "skyfall_fara",
    "soltimmar": "dagsljus_soltimmar",
    "sammanhangande_soltimmar": "dagsljus_soltimmar",
    "svf": "dagsljus_svf",
    "stromtathet": "ekologi_stromtathet",
    "kansighet": "ekologi_stromtathet",
    "komfortklass": "luft_vindkomfort",
    "buller": "buller_dba",
    "ljudniva": "buller_dba",
    "laeq": "buller_dba",
}


def _formatera_tal_sv(varde: float) -> str:
    """Formaterar ett skaltal med svenskt decimalkomma utan onödiga nollor."""
    av = abs(varde)
    if av == 0.0:
        return "0"
    if av >= 100.0:
        return f"{varde:.0f}"
    if av >= 10.0:
        return f"{varde:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    if av >= 0.1:
        return f"{varde:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{varde:.3g}".replace(".", ",")


def _bygg_skillnadsstil(
    *,
    roll: str,
    doman: str,
    storhet: str,
    enhet: str,
    max_abs: float,
    troskel: float = 0.0,
) -> FigurStil:
    """Bygger en symmetrisk, färgblindsvänlig divergerande skala för skillnadslager."""
    hi = abs(float(max_abs))
    tr = max(0.0, float(troskel))
    if not math.isfinite(hi) or hi <= 0.0:
        hi = max(tr * 5.0, 0.5)
    g1 = min(tr, hi * 0.35) if tr > 0.0 else hi * 0.04
    if g1 <= 0.0:
        g1 = hi * 0.04
    if hi <= g1:
        hi = g1 * 4.0
    g2 = g1 + (hi - g1) * 0.30
    g3 = g1 + (hi - g1) * 0.68
    enhet_visa = enhet or ("relativ, 0–1" if roll in ("stromtathet", "kansighet") else "")
    suf = f" {enhet}" if enhet else ""
    s1, s2, s3 = _formatera_tal_sv(g1), _formatera_tal_sv(g2), _formatera_tal_sv(g3)
    return FigurStil(
        namn=f"{doman or roll}_skillnad",
        doman=doman or roll,
        storhet=f"Skillnad i {storhet.lower()}" if storhet else "Skillnad",
        enhet=enhet_visa,
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Inget värde / tak",
        klasser=(
            FargKlass(None, -g3, hex_till_rgb("#234B66"), f"≤ −{s3}{suf} (minskning)"),
            FargKlass(-g3, -g2, hex_till_rgb("#4A7D8C"), f"−{s3} – −{s2}{suf}"),
            FargKlass(-g2, -g1, hex_till_rgb("#8FB9AC"), f"−{s2} – −{s1}{suf}"),
            FargKlass(-g1, g1, FARG_KARTYTA, f"−{s1} – +{s1}{suf} (oförändrat)"),
            FargKlass(g1, g2, hex_till_rgb("#D6B463"), f"+{s1} – +{s2}{suf}"),
            FargKlass(g2, g3, hex_till_rgb("#B86A4B"), f"+{s2} – +{s3}{suf}"),
            FargKlass(g3, None, hex_till_rgb("#8C3B2B"), f"≥ +{s3}{suf} (ökning)"),
        ),
    )


def _interpolera_stopp(stopp: Sequence[str], antal: int) -> list[tuple[int, int, int]]:
    rgb_stopp = [hex_till_rgb(s) for s in stopp]
    if not rgb_stopp:
        return [hex_till_rgb("#8FB9AC")] * antal
    if len(rgb_stopp) == 1 or antal <= 1:
        return [rgb_stopp[0]] * max(antal, 1)
    ut: list[tuple[int, int, int]] = []
    for idx in range(antal):
        pos = idx * (len(rgb_stopp) - 1) / (antal - 1)
        i0 = min(int(pos), len(rgb_stopp) - 2)
        f = pos - i0
        c0, c1 = rgb_stopp[i0], rgb_stopp[i0 + 1]
        ut.append(
            (
                int(round(c0[0] * (1.0 - f) + c1[0] * f)),
                int(round(c0[1] * (1.0 - f) + c1[1] * f)),
                int(round(c0[2] * (1.0 - f) + c1[2] * f)),
            )
        )
    return ut


def figurstil_for_lager(lager: Mapping[str, Any]) -> FigurStil:
    """Väljer en ämnesanpassad ``FigurStil`` för ett Floden-resultatlager (K20/webb/MCP).

    Kända roller mappas direkt mot ``FIGURSTILAR`` med fysikaliskt korrekta enheter;
    skillnadslager får en symmetrisk divergerande skala; övriga roller får en
    deterministisk stil byggd ur lagrets egen teckenförklaring och skala.
    """
    roll = str(lager.get("roll") or "")
    doman = str(lager.get("doman") or "")
    etikett = str(lager.get("etikett") or roll.replace("_", " ").capitalize() or "Värde")
    enhet = str(lager.get("enhet") or "")
    typ = str(lager.get("typ") or "")
    farg = str(lager.get("farg") or "")
    tf = lager.get("teckenforklaring") or {}
    skala = lager.get("skala") or {}

    if typ == "skillnad" or farg == "skillnad" or tf.get("typ") == "divergerande":
        max_abs = float(tf.get("max", skala.get("max", 0.5)))
        troskel = float(tf.get("troskel", 0.0))
        return _bygg_skillnadsstil(
            roll=roll,
            doman=doman,
            storhet=etikett,
            enhet=enhet,
            max_abs=max_abs,
            troskel=troskel,
        )

    stilnamn = _ROLL_TILL_STIL.get(roll)
    if stilnamn is not None:
        bas = FIGURSTILAR[stilnamn]
        if roll in ("kansighet", "sammanhangande_soltimmar", "tid_till_0_1m", "tid_till_0_2m"):
            return replace(bas, storhet=etikett)
        return bas

    tf_typ = str(tf.get("typ") or "")
    if tf_typ == "kategorier" and tf.get("kategorier"):
        klasser_kat: list[FargKlass] = []
        for kat in tf["kategorier"]:
            v = float(kat["varde"])
            kat_et = str(kat.get("etikett") or "").strip()
            v_str = _formatera_tal_sv(v)
            et = f"Klass {v_str} ({kat_et})" if kat_et else f"Klass {v_str}"
            klasser_kat.append(FargKlass(v - 0.5, v + 0.5, hex_till_rgb(str(kat["farg"])), et))
        return FigurStil(
            namn=f"lager_{roll or 'kategorier'}",
            doman=doman or roll,
            storhet=etikett,
            enhet=enhet or "klass",
            tomvarde_farg=FARG_KARTYTA,
            tomvarde_etikett="Inget värde",
            klasser=tuple(klasser_kat),
        )

    if tf_typ == "klasser" and tf.get("klasser"):
        klista = list(tf["klasser"])
        klasser_k: list[FargKlass] = []
        for i, post in enumerate(klista):
            lo_k = float(post["fran"])
            hi_k = float(klista[i + 1]["fran"]) if i + 1 < len(klista) else None
            et_k = str(post.get("etikett") or "").replace(".", ",")
            klasser_k.append(FargKlass(lo_k, hi_k, hex_till_rgb(str(post["farg"])), et_k))
        g0 = _formatera_tal_sv(float(klista[0]["fran"]))
        suf = f" {enhet}" if enhet else ""
        return FigurStil(
            namn=f"lager_{roll or 'klasser'}",
            doman=doman or roll,
            storhet=etikett,
            enhet=enhet,
            tomvarde_farg=FARG_KARTYTA,
            tomvarde_etikett=f"Under {g0}{suf}",
            klasser=tuple(klasser_k),
        )

    lo = float(tf.get("min", skala.get("min", 0.0)))
    hi = float(tf.get("max", skala.get("max", 1.0)))
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        hi = lo + 1.0
    n_klasser = 5
    farger = _interpolera_stopp(
        tf.get("stopp") or ("#4A524C", "#6B7D6D", "#8CA88E", "#D9CF6A", "#F2E89B"),
        n_klasser,
    )
    granser = [lo + i * (hi - lo) / n_klasser for i in range(n_klasser + 1)]
    suf = f" {enhet}" if enhet else ""
    klasser_kont: list[FargKlass] = []
    for i in range(n_klasser):
        a, b = granser[i], granser[i + 1]
        hi_v = None if i == n_klasser - 1 else b
        et = f"{_formatera_tal_sv(a)} – {_formatera_tal_sv(b)}{suf}"
        klasser_kont.append(FargKlass(a, hi_v, farger[i], et))
    return FigurStil(
        namn=f"lager_{roll or 'kontinuerlig'}",
        doman=doman or roll,
        storhet=etikett,
        enhet=enhet,
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Inget värde",
        klasser=tuple(klasser_kont),
    )


# ---------------------------------------------------------------------- Typsnitt


@lru_cache(maxsize=32)
def _typsnitt(filnamn: str, storlek_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    fil = _TYPSNITT_MAPP / filnamn
    if fil.is_file():
        return ImageFont.truetype(str(fil), size=storlek_px)
    return ImageFont.load_default()


def _text_storlek(
    rit: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> tuple[int, int]:
    bbox = rit.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])


# ---------------------------------------------------------------------- Raster → RGB


def klassificera_raster(
    raster: NDArray[np.floating[Any]],
    stil: FigurStil,
    byggnad_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
) -> NDArray[np.uint8]:
    """Mappar ett 2D-raster vektoriserat till RGB enligt ``stil``."""
    arr = np.asarray(raster, dtype=np.float64)
    if arr.ndim != 2:
        raise FigurFel(f"Rastret måste vara 2D, fick {arr.ndim}D.")
    if arr.shape[0] < 1 or arr.shape[1] < 1:
        raise FigurFel(f"Rastret får inte ha tomma dimensioner: {arr.shape}.")

    hojd, bredd = arr.shape
    rgb = np.empty((hojd, bredd, 3), dtype=np.uint8)
    rgb[:, :, 0] = stil.tomvarde_farg[0]
    rgb[:, :, 1] = stil.tomvarde_farg[1]
    rgb[:, :, 2] = stil.tomvarde_farg[2]

    giltig = np.isfinite(arr)
    for k in stil.klasser:
        mask = giltig.copy()
        if k.min_varde is not None:
            mask &= arr >= k.min_varde
        if k.max_varde is not None:
            mask &= arr < k.max_varde
        rgb[mask] = k.farg

    if byggnad_mask is not None:
        bmask = np.asarray(byggnad_mask, dtype=bool)
        if bmask.shape != arr.shape:
            raise FigurFel(
                f"byggnad_mask har form {bmask.shape}, men rastret har form {arr.shape}."
            )
        rgb[bmask] = FARG_BYGGNAD
        # Subtil 1-cellskontur längs byggnadernas ytterkant
        kant = bmask.copy()
        inre = bmask.copy()
        inre[1:, :] &= bmask[:-1, :]
        inre[:-1, :] &= bmask[1:, :]
        inre[:, 1:] &= bmask[:, :-1]
        inre[:, :-1] &= bmask[:, 1:]
        inre[0, :] = False
        inre[-1, :] = False
        inre[:, 0] = False
        inre[:, -1] = False
        kant &= ~inre
        rgb[kant] = FARG_BYGGNAD_KANT

    return rgb


# ---------------------------------------------------------------------- Överlagringar på kartyta


def berakna_skalstock_m(geo: GeoTransformInfo, vis_w: int) -> tuple[int, int]:
    """Väljer en avrundad längd i meter och motsvarande pixelbredd för skalstocken."""
    m_per_px = (geo.bredd * geo.pixelstorlek_x) / max(vis_w, 1)
    max_px = max(72, min(210, int(vis_w * 0.34)))
    valt_m = _SKALSTOCK_VAL_M[0]
    for m in _SKALSTOCK_VAL_M:
        if m / m_per_px <= max_px:
            valt_m = m
        else:
            break
    bredd_px = max(24, int(round(valt_m / m_per_px)))
    return valt_m, bredd_px


_berakna_skalstock_m = berakna_skalstock_m


def _rita_byggnader_3011(
    karta_img: Image.Image,
    byggnader_3011: Sequence[Mapping[str, Any]],
    geo: GeoTransformInfo,
    vis_w: int,
    vis_h: int,
) -> Image.Image:
    """Ritar byggnadskonturer i EPSG:3011 på kartytan (oförändrad, ändrad, ny)."""
    skala_x = vis_w / (geo.bredd * geo.pixelstorlek_x)
    skala_y = vis_h / (geo.hojd * geo.pixelstorlek_y)
    overlay = Image.new("RGBA", karta_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for status, farg, bredd in (
        ("oforandrad", FARG_BYGGNAD_KANT, 1),
        ("andrad", FARG_BYGGNAD_ANDRAD, 2),
        ("ny", FARG_BYGGNAD_NY, 3),
    ):
        for b in byggnader_3011:
            if b.get("status", "oforandrad") != status:
                continue
            for ring in [b["ring"], *b.get("hal", ())]:
                pts = [
                    ((float(e) - geo.x_min) * skala_x, (geo.y_max - float(n)) * skala_y)
                    for e, n in ring
                ]
                if len(pts) >= 2:
                    draw.line([*pts, pts[0]], fill=(*farg, 255), width=bredd)
    return Image.alpha_composite(karta_img.convert("RGBA"), overlay).convert("RGB")


def _formatera_meter(varde: float) -> str:
    if abs(varde - round(varde)) < 1e-6:
        return str(int(round(varde)))
    return f"{varde:.1f}".replace(".", ",")


def _rita_projektpolygon(
    karta_img: Image.Image,
    polygon_3011: Sequence[Sequence[float]],
    geo: GeoTransformInfo,
    vis_w: int,
    vis_h: int,
) -> Image.Image:
    """Ritar projektpolygonens kontur i EPSG:3011 på kartytan med diskret ljus halo."""
    if len(polygon_3011) < 3:
        raise FigurFel("projektpolygon_3011 måste ha minst 3 hörn.")
    skala_x = vis_w / (geo.bredd * geo.pixelstorlek_x)
    skala_y = vis_h / (geo.hojd * geo.pixelstorlek_y)

    punkter: list[tuple[float, float]] = []
    for pt in polygon_3011:
        if len(pt) < 2:
            raise FigurFel(
                "Varje punkt i projektpolygon_3011 måste ha minst två koordinater (E, N)."
            )
        e, n = float(pt[0]), float(pt[1])
        px = (e - geo.x_min) * skala_x
        py = (geo.y_max - n) * skala_y
        punkter.append((px, py))
    if punkter[0] != punkter[-1]:
        punkter.append(punkter[0])

    overlay = Image.new("RGBA", karta_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # Ljus halo så konturen alltid syns mot mörka rasterceller
    draw.line(punkter, fill=(*FARG_BAKGRUND, 205), width=6, joint="curve")
    draw.line(punkter, fill=(*FARG_POLYGON, 255), width=3, joint="curve")
    return Image.alpha_composite(karta_img.convert("RGBA"), overlay).convert("RGB")


def _rita_norrpil(karta_img: Image.Image) -> Image.Image:
    """Ritar en ren, nordisk norrpil (N) i kartytans övre högra hörn."""
    overlay = Image.new("RGBA", karta_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_n = _typsnitt("LiberationSans-Bold.ttf", 12)

    box_w, box_h = 34, 46
    marg = 12
    x0 = karta_img.width - marg - box_w
    y0 = marg
    x1, y1 = x0 + box_w, y0 + box_h

    draw.rounded_rectangle(
        [x0, y0, x1, y1],
        radius=6,
        fill=(*FARG_BAKGRUND, 228),
        outline=(*FARG_LINJE, 255),
        width=1,
    )

    cx = (x0 + x1) / 2.0
    nw, nh = _text_storlek(draw, "N", font_n)
    draw.text((cx - nw / 2.0, y0 + 4), "N", fill=(*FARG_TEXT, 255), font=font_n)

    # Tvådelad geometrisk kompasspil
    spets_y = y0 + nh + 10
    bas_y = y1 - 7
    mitt_y = bas_y - 4
    halv_b = 7.0
    draw.polygon(
        [(cx, spets_y), (cx - halv_b, bas_y), (cx, mitt_y)],
        fill=(*FARG_TEXT, 255),
    )
    draw.polygon(
        [(cx, spets_y), (cx + halv_b, bas_y), (cx, mitt_y)],
        fill=(*FARG_KARTYTA, 255),
        outline=(*FARG_TEXT, 255),
    )
    return Image.alpha_composite(karta_img.convert("RGBA"), overlay).convert("RGB")


def _rita_skalstock(karta_img: Image.Image, geo: GeoTransformInfo) -> Image.Image:
    """Ritar en automatiskt avrundad skalstock i meter i nedre vänstra hörnet."""
    langd_m, bar_w = _berakna_skalstock_m(geo, karta_img.width)
    halv_m = langd_m / 2.0

    overlay = Image.new("RGBA", karta_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_s = _typsnitt("LiberationSans-Regular.ttf", 11)

    t0 = "0"
    t1 = _formatera_meter(halv_m)
    t2 = f"{_formatera_meter(langd_m)} m"
    w2, h2 = _text_storlek(draw, t2, font_s)

    pad_x, pad_y = 10, 7
    bar_h = 6
    box_w = pad_x + bar_w + max(pad_x, w2 // 2 + 8)
    box_h = pad_y + bar_h + 6 + h2 + pad_y

    marg = 12
    x0 = marg
    y1 = karta_img.height - marg
    y0 = y1 - box_h
    x1 = x0 + box_w

    draw.rounded_rectangle(
        [x0, y0, x1, y1],
        radius=6,
        fill=(*FARG_BAKGRUND, 232),
        outline=(*FARG_LINJE, 255),
        width=1,
    )

    bx0 = x0 + pad_x
    by0 = y0 + pad_y
    bx_mid = bx0 + bar_w // 2
    bx1 = bx0 + bar_w
    by1 = by0 + bar_h

    # Vänster halva fylld, höger halva ljus med ram
    draw.rectangle([bx0, by0, bx_mid, by1], fill=(*FARG_TEXT, 255), outline=(*FARG_TEXT, 255))
    draw.rectangle([bx_mid, by0, bx1, by1], fill=(*FARG_BAKGRUND, 255), outline=(*FARG_TEXT, 255))

    ty = by1 + 4
    w0, _ = _text_storlek(draw, t0, font_s)
    w1, _ = _text_storlek(draw, t1, font_s)
    draw.text((bx0 - w0 // 2 + 2, ty), t0, fill=(*FARG_TEXT, 255), font=font_s)
    draw.text((bx_mid - w1 // 2, ty), t1, fill=(*FARG_TEXT, 255), font=font_s)
    draw.text((bx1 - w2 // 2, ty), t2, fill=(*FARG_TEXT, 255), font=font_s)

    return Image.alpha_composite(karta_img.convert("RGBA"), overlay).convert("RGB")


# ---------------------------------------------------------------------- Huvudfunktion


def rendera_kartfigur(
    raster: NDArray[np.floating[Any]],
    stil: str | FigurStil,
    *,
    titel: str,
    undertitel: str = "",
    metodstatus: str = "Skissunderlag",
    geo: GeoTransformInfo | None = None,
    byggnad_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    byggnader_3011: Sequence[Mapping[str, Any]] | None = None,
    projektpolygon_3011: Sequence[Sequence[float]] | None = None,
    ut_fil: str | Path | None = None,
    run_hash: str | None = None,
    anmarkning: str | None = None,
    kalla: str | None = None,
    karta_max_px: int = 640,
    legend_placering: Literal["sida", "botten"] = "sida",
) -> Image.Image:
    """Renderar en enhetlig, tryckfärdig rapportfigur med huvud, kartyta, teckenförklaring och fot.

    Returnerar ``PIL.Image.Image`` i RGB-läge. Om ``ut_fil`` anges sparas figuren som PNG.
    """
    if karta_max_px < 120:
        raise FigurFel(f"karta_max_px måste vara minst 120, fick {karta_max_px}.")
    if projektpolygon_3011 is not None and geo is None:
        raise FigurFel("geo (GeoTransformInfo) krävs för att rita projektpolygon_3011.")
    if byggnader_3011 and geo is None:
        raise FigurFel("geo (GeoTransformInfo) krävs för att rita byggnader_3011.")

    fstil = hamta_figurstil(stil)
    rgb = klassificera_raster(raster, fstil, byggnad_mask=byggnad_mask)
    karta_h, karta_w, _ = rgb.shape

    if geo is not None and (geo.hojd != karta_h or geo.bredd != karta_w):
        raise FigurFel(
            f"GeoTransformInfo ({geo.hojd}×{geo.bredd}) matchar inte rastret ({karta_h}×{karta_w})."
        )

    # Skala kartytan med NEAREST så cellgränser och klassfärger bevaras exakt.
    skala = karta_max_px / max(karta_w, karta_h)
    vis_w = max(200, int(round(karta_w * skala)))
    vis_h = max(180, int(round(karta_h * skala)))
    karta_img = Image.fromarray(rgb, mode="RGB").resize(
        (vis_w, vis_h), resample=Image.Resampling.NEAREST
    )

    if byggnader_3011 and geo is not None:
        karta_img = _rita_byggnader_3011(karta_img, byggnader_3011, geo, vis_w, vis_h)
    if projektpolygon_3011 is not None and geo is not None:
        karta_img = _rita_projektpolygon(karta_img, projektpolygon_3011, geo, vis_w, vis_h)
    karta_img = _rita_norrpil(karta_img)
    if geo is not None:
        karta_img = _rita_skalstock(karta_img, geo)

    # Typsnitt
    font_titel = _typsnitt("Gelasio-Bold.ttf", 22)
    font_under = _typsnitt("LiberationSans-Regular.ttf", 13)
    font_badge = _typsnitt("LiberationSans-Bold.ttf", 11)
    font_leg_h = _typsnitt("LiberationSans-Bold.ttf", 13)
    font_leg_sub = _typsnitt("LiberationSans-Regular.ttf", 11)
    font_leg = _typsnitt("LiberationSans-Regular.ttf", 12)
    font_fot = _typsnitt("LiberationSans-Regular.ttf", 11)
    font_anm = _typsnitt("LiberationSans-Italic.ttf", 10)

    # Mät text på liten hjälpduk
    mat_rit = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    # Bygg legendposter
    extra_poster: list[tuple[str, str]] = []
    if fstil.tomvarde_etikett:
        extra_poster.append(("tom", fstil.tomvarde_etikett))
    if byggnad_mask is not None:
        extra_poster.append(("byggnad", "Byggnad"))
    if byggnader_3011:
        statusar = {str(b.get("status", "oforandrad")) for b in byggnader_3011}
        if byggnad_mask is None and "oforandrad" in statusar:
            extra_poster.append(("byggnad_kontur", "Byggnad (kontur)"))
        if "ny" in statusar:
            extra_poster.append(("byggnad_ny", "Ny byggnad"))
        if "andrad" in statusar:
            extra_poster.append(("byggnad_andrad", "Ändrad byggnad"))
    if projektpolygon_3011 is not None:
        extra_poster.append(("polygon", "Projektområde"))

    # Layoutdimensioner
    marg_x = 26
    marg_topp = 22
    marg_botten = 18
    gap = 20

    titel_w, titel_h = _text_storlek(mat_rit, titel, font_titel)
    under_w, under_h = _text_storlek(mat_rit, undertitel, font_under) if undertitel else (0, 0)
    huvud_h = titel_h + (6 + under_h if undertitel else 0) + 14

    swatch_w, swatch_h = 20, 13
    rad_h = 22
    leg_pad = 14
    leg_rubrik_h = _text_storlek(mat_rit, "Teckenförklaring", font_leg_h)[1]
    leg_sub_text = f"{fstil.storhet} ({fstil.enhet})" if fstil.enhet else fstil.storhet
    leg_sub_h = _text_storlek(mat_rit, leg_sub_text, font_leg_sub)[1]

    if legend_placering == "sida":
        alla_etiketter = [k.etikett for k in fstil.klasser] + [t for _, t in extra_poster]
        max_et_w = max(
            (_text_storlek(mat_rit, t, font_leg)[0] for t in alla_etiketter),
            default=120,
        )
        leg_sub_w = _text_storlek(mat_rit, leg_sub_text, font_leg_sub)[0]
        leg_w = max(216, leg_pad * 2 + max(swatch_w + 10 + max_et_w, leg_sub_w))
        leg_inre_h = (
            leg_pad
            + leg_rubrik_h
            + 4
            + leg_sub_h
            + 12
            + len(fstil.klasser) * rad_h
            + (10 + len(extra_poster) * rad_h if extra_poster else 0)
            + leg_pad
        )
        innehall_w = vis_w + gap + leg_w
        innehall_h = max(vis_h, leg_inre_h)
    else:
        leg_w = vis_w
        kolumner = 2 if len(fstil.klasser) + len(extra_poster) > 4 else 1
        rader_klass = (len(fstil.klasser) + kolumner - 1) // kolumner
        rader_extra = (len(extra_poster) + kolumner - 1) // kolumner if extra_poster else 0
        leg_inre_h = (
            leg_pad
            + leg_rubrik_h
            + 4
            + leg_sub_h
            + 10
            + rader_klass * rad_h
            + (8 + rader_extra * rad_h if rader_extra else 0)
            + leg_pad
        )
        innehall_w = vis_w
        innehall_h = vis_h + gap + leg_inre_h

    fot_v = "SWEREF 99 18 00 · RH 2000"
    if kalla:
        fot_v = f"{fot_v} · Källa: {kalla}"
    fot_h_text = f"run.json: {run_hash[:12]}" if run_hash else ""

    # Se till att titel + statusmärke, undertitel och sidfot får plats utan klippning
    badge_w = (_text_storlek(mat_rit, metodstatus, font_badge)[0] + 20) if metodstatus else 0
    fot_v_w = _text_storlek(mat_rit, fot_v, font_fot)[0]
    fot_h_w = (_text_storlek(mat_rit, fot_h_text, font_fot)[0] + 16) if fot_h_text else 0
    anm_w = _text_storlek(mat_rit, anmarkning, font_anm)[0] if anmarkning else 0
    min_bredd = max(
        titel_w + (16 + badge_w if badge_w else 0),
        under_w,
        fot_v_w + fot_h_w,
        anm_w,
    )
    innehall_w = max(innehall_w, min_bredd)

    fot_h = 12 + _text_storlek(mat_rit, "SWEREF 99 18 00", font_fot)[1]
    if anmarkning:
        fot_h += 6 + _text_storlek(mat_rit, anmarkning, font_anm)[1]

    total_w = marg_x * 2 + innehall_w
    total_h = marg_topp + huvud_h + 14 + innehall_h + 16 + fot_h + marg_botten

    duk = Image.new("RGB", (total_w, total_h), FARG_BAKGRUND)
    rit = ImageDraw.Draw(duk)

    # 1. Huvud
    rit.text((marg_x, marg_topp), titel, fill=FARG_TEXT, font=font_titel)
    if undertitel:
        rit.text((marg_x, marg_topp + titel_h + 6), undertitel, fill=FARG_TEXT_SEK, font=font_under)

    if metodstatus:
        bw, bh = _text_storlek(rit, metodstatus, font_badge)
        bx1 = total_w - marg_x
        bx0 = bx1 - (bw + 18)
        by0 = marg_topp + 2
        by1 = by0 + bh + 10
        rit.rounded_rectangle(
            [bx0, by0, bx1, by1],
            radius=10,
            fill=FARG_BADGE_BG,
            outline=FARG_BADGE_KANT,
            width=1,
        )
        rit.text((bx0 + 9, by0 + 4), metodstatus, fill=FARG_POLYGON, font=font_badge)

    huvud_linje_y = marg_topp + huvud_h
    rit.line([(marg_x, huvud_linje_y), (total_w - marg_x, huvud_linje_y)], fill=FARG_LINJE, width=1)

    # 2. Kartyta
    karta_x = marg_x
    karta_y = huvud_linje_y + 14
    duk.paste(karta_img, (karta_x, karta_y))
    rit.rectangle(
        [karta_x - 1, karta_y - 1, karta_x + vis_w, karta_y + vis_h],
        outline=FARG_LINJE,
        width=1,
    )

    # 3. Teckenförklaring
    if legend_placering == "sida":
        lx0 = karta_x + vis_w + gap
        ly0 = karta_y
        lx1 = total_w - marg_x
        ly1 = karta_y + innehall_h
    else:
        lx0 = karta_x
        ly0 = karta_y + vis_h + gap
        lx1 = total_w - marg_x
        ly1 = ly0 + leg_inre_h

    rit.rounded_rectangle(
        [lx0, ly0, lx1, ly1],
        radius=6,
        fill=FARG_KARTYTA,
        outline=FARG_LINJE,
        width=1,
    )

    cy = ly0 + leg_pad
    rit.text((lx0 + leg_pad, cy), "Teckenförklaring", fill=FARG_TEXT, font=font_leg_h)
    cy += leg_rubrik_h + 4
    rit.text((lx0 + leg_pad, cy), leg_sub_text, fill=FARG_TEXT_SEK, font=font_leg_sub)
    cy += leg_sub_h + 10

    def _rita_post(x: int, y: int, typ: str, farg: tuple[int, int, int], etikett: str) -> None:
        sy0 = y + 2
        sy1 = sy0 + swatch_h
        if typ == "polygon":
            rit.rectangle(
                [x, sy0, x + swatch_w, sy1],
                fill=FARG_KARTYTA,
                outline=FARG_POLYGON,
                width=2,
            )
        elif typ == "byggnad":
            rit.rectangle(
                [x, sy0, x + swatch_w, sy1],
                fill=FARG_BYGGNAD,
                outline=FARG_BYGGNAD_KANT,
                width=1,
            )
        elif typ == "byggnad_kontur":
            rit.rectangle(
                [x, sy0, x + swatch_w, sy1],
                fill=FARG_KARTYTA,
                outline=FARG_BYGGNAD_KANT,
                width=2,
            )
        elif typ == "byggnad_ny":
            rit.rectangle(
                [x, sy0, x + swatch_w, sy1],
                fill=FARG_KARTYTA,
                outline=FARG_BYGGNAD_NY,
                width=2,
            )
        elif typ == "byggnad_andrad":
            rit.rectangle(
                [x, sy0, x + swatch_w, sy1],
                fill=FARG_KARTYTA,
                outline=FARG_BYGGNAD_ANDRAD,
                width=2,
            )
        else:
            rit.rectangle(
                [x, sy0, x + swatch_w, sy1],
                fill=farg,
                outline=FARG_SWATCH_KANT,
                width=1,
            )
        rit.text((x + swatch_w + 10, y + 1), etikett, fill=FARG_TEXT, font=font_leg)

    if legend_placering == "sida":
        for k in fstil.klasser:
            _rita_post(lx0 + leg_pad, cy, "klass", k.farg, k.etikett)
            cy += rad_h
        if extra_poster:
            cy += 3
            rit.line([(lx0 + leg_pad, cy), (lx1 - leg_pad, cy)], fill=FARG_LINJE, width=1)
            cy += 7
            for typ, etikett in extra_poster:
                _rita_post(lx0 + leg_pad, cy, typ, fstil.tomvarde_farg, etikett)
                cy += rad_h
    else:
        kol_w = (lx1 - lx0 - leg_pad * 2) // kolumner
        for idx, k in enumerate(fstil.klasser):
            kol = idx % kolumner
            rad = idx // kolumner
            _rita_post(lx0 + leg_pad + kol * kol_w, cy + rad * rad_h, "klass", k.farg, k.etikett)
        cy += rader_klass * rad_h
        if extra_poster:
            cy += 3
            rit.line([(lx0 + leg_pad, cy), (lx1 - leg_pad, cy)], fill=FARG_LINJE, width=1)
            cy += 5
            for idx, (typ, etikett) in enumerate(extra_poster):
                kol = idx % kolumner
                rad = idx // kolumner
                _rita_post(
                    lx0 + leg_pad + kol * kol_w,
                    cy + rad * rad_h,
                    typ,
                    fstil.tomvarde_farg,
                    etikett,
                )

    # 4. Spårbarhetsfot
    fot_y = karta_y + innehall_h + 16
    rit.line([(marg_x, fot_y), (total_w - marg_x, fot_y)], fill=FARG_LINJE, width=1)

    rit.text((marg_x, fot_y + 8), fot_v, fill=FARG_TEXT_SEK, font=font_fot)

    if fot_h_text:
        fhw, _ = _text_storlek(rit, fot_h_text, font_fot)
        rit.text((total_w - marg_x - fhw, fot_y + 8), fot_h_text, fill=FARG_TEXT_SEK, font=font_fot)

    if anmarkning:
        anm_y = fot_y + 8 + _text_storlek(rit, fot_v, font_fot)[1] + 5
        rit.text((marg_x, anm_y), anmarkning, fill=FARG_TEXT_SEK, font=font_anm)

    if ut_fil is not None:
        mal = Path(ut_fil)
        mal.parent.mkdir(parents=True, exist_ok=True)
        duk.save(mal, format="PNG")

    return duk


def spara_kartfigur(
    raster: NDArray[np.floating[Any]],
    stil: str | FigurStil,
    ut_fil: str | Path,
    *,
    titel: str,
    undertitel: str = "",
    metodstatus: str = "Skissunderlag",
    geo: GeoTransformInfo | None = None,
    byggnad_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    byggnader_3011: Sequence[Mapping[str, Any]] | None = None,
    projektpolygon_3011: Sequence[Sequence[float]] | None = None,
    run_hash: str | None = None,
    anmarkning: str | None = None,
    kalla: str | None = None,
    karta_max_px: int = 640,
    legend_placering: Literal["sida", "botten"] = "sida",
) -> Path:
    """Renderar och sparar en rapportfigur som PNG och returnerar sökvägen."""
    mal = Path(ut_fil)
    rendera_kartfigur(
        raster,
        stil,
        titel=titel,
        undertitel=undertitel,
        metodstatus=metodstatus,
        geo=geo,
        byggnad_mask=byggnad_mask,
        byggnader_3011=byggnader_3011,
        projektpolygon_3011=projektpolygon_3011,
        ut_fil=mal,
        run_hash=run_hash,
        anmarkning=anmarkning,
        kalla=kalla,
        karta_max_px=karta_max_px,
        legend_placering=legend_placering,
    )
    return mal


__all__ = [
    "FARG_BAKGRUND",
    "FARG_BYGGNAD",
    "FARG_BYGGNAD_ANDRAD",
    "FARG_BYGGNAD_KANT",
    "FARG_BYGGNAD_NY",
    "FARG_KARTYTA",
    "FARG_LINJE",
    "FARG_POLYGON",
    "FARG_TEXT",
    "FARG_TEXT_SEK",
    "FIGURSTILAR",
    "FargKlass",
    "FigurFel",
    "FigurStil",
    "berakna_skalstock_m",
    "figurstil_for_lager",
    "hamta_figurstil",
    "hex_till_rgb",
    "klassificera_raster",
    "rendera_kartfigur",
    "spara_kartfigur",
]
