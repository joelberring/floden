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
FARG_VATTEN: Final[tuple[int, int, int]] = (0xB8, 0xD4, 0xE3)  # #B8D4E3 permanent vattenyta
FARG_VATTEN_KANT: Final[tuple[int, int, int]] = (0x8B, 0xAF, 0xC4)  # #8BAFC4 vattenyta ram
FARG_BADGE_BG: Final[tuple[int, int, int]] = (0xE6, 0xEA, 0xE1)  # #E6EAE1 statusmärke bakgrund
FARG_BADGE_KANT: Final[tuple[int, int, int]] = (0xB8, 0xC4, 0xB0)  # #B8C4B0 statusmärke ram
FARG_SWATCH_KANT: Final[tuple[int, int, int]] = (0xB8, 0xB3, 0xA4)  # #B8B3A4 färgruta ram
FARG_VAGKANT: Final[tuple[int, int, int]] = (0xBD, 0xB7, 0xA8)  # #BDB7A8 vägkant / kantsten
FARG_HOJDKURVA: Final[tuple[int, int, int]] = (0x94, 0x8D, 0x7E)  # #948D7E subtil höjdkurva
FARG_FLODESVAG: Final[tuple[int, int, int]] = (0x2B, 0x5B, 0x7A)  # #2B5B7A flödesväg med pil
FARG_MARKOR: Final[tuple[int, int, int]] = (0x9E, 0x2A, 0x2B)  # #9E2A2B lågpunktsmarkör P1–P3
FARG_MARK_FYLL: Final[tuple[int, int, int]] = (0xE5, 0xDF, 0xD2)  # #E5DFD2 markfyllning i sektion
FARG_VATTEN_FORE: Final[tuple[int, int, int]] = (0x8F, 0xB9, 0xAC)  # #8FB9AC vatten före i sektion
FARG_VATTEN_EFTER: Final[tuple[int, int, int]] = (0x3D, 0x76, 0x8F)  # #3D768F vatten efter
FARG_VATTEN_EFTER_LINJE: Final[tuple[int, int, int]] = (0x1E, 0x45, 0x60)  # #1E4560 vattenyta efter

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
    "mikroklimat_utci": FigurStil(
        namn="mikroklimat_utci",
        doman="mikroklimat",
        storhet="Termisk stress (UTCI)",
        enhet="°C",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Utanför giltighetsområde / inget värde",
        klasser=(
            FargKlass(None, 9.0, hex_till_rgb("#4A7D8C"), "< 9 °C (köldstress)"),
            FargKlass(9.0, 26.0, hex_till_rgb("#DCE6D5"), "9 – 26 °C (ingen stress)"),
            FargKlass(26.0, 32.0, hex_till_rgb("#E8D48B"), "26 – 32 °C (måttlig värmestress)"),
            FargKlass(32.0, 38.0, hex_till_rgb("#D69853"), "32 – 38 °C (stark värmestress)"),
            FargKlass(38.0, 46.0, hex_till_rgb("#B85A3A"), "38 – 46 °C (mycket stark värmestress)"),
            FargKlass(46.0, None, hex_till_rgb("#7A2820"), "≥ 46 °C (extrem värmestress)"),
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
    "utci": "mikroklimat_utci",
    "utci_c": "mikroklimat_utci",
}


def _formatera_tal_sv(varde: float, *, fasta_dec: int | None = None) -> str:
    """Formaterar ett skaltal med svenskt decimalkomma utan onödiga nollor."""
    av = abs(varde)
    if av == 0.0:
        return "0"
    if fasta_dec is not None:
        return f"{varde:.{fasta_dec}f}".replace(".", ",")
    if av >= 100.0:
        return f"{varde:.0f}"
    if av >= 10.0:
        return f"{varde:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    if av >= 0.01:
        return f"{varde:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{varde:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def _runda_125(varde: float, *, golv: float = 0.01) -> float:
    """Avrundar uppåt till snyggt 1–2–5-tal för kartans teckenförklaring."""
    v = max(abs(float(varde)), float(golv))
    if not math.isfinite(v):
        return float(golv)
    exp = math.floor(math.log10(v))
    bas = 10.0**exp
    mant = v / bas
    for steg in (1.0, 2.0, 2.5, 5.0, 10.0):
        if mant <= steg + 1e-9:
            return round(steg * bas, 6)
    return round(10.0 * bas, 6)


def _runda_skillnadsgranser(
    *,
    roll: str,
    doman: str,
    enhet: str,
    max_abs: float,
    troskel: float,
) -> tuple[float, float, float, str | None, int | None]:
    """Väljer runda, läsbara klassgränser ``(g1, g2, g3, neutral_text, fasta_dec)``."""
    hi = abs(float(max_abs)) if math.isfinite(max_abs) else 0.0
    tr = max(0.0, float(troskel)) if math.isfinite(troskel) else 0.0
    ar_skyfall = enhet == "m" or doman in ("vatten", "skyfall") or roll == "max_djup"
    ar_sol = enhet in ("h", "tim") or roll in ("soltimmar", "sammanhangande_soltimmar")
    ar_eko = roll in ("stromtathet", "kansighet") or doman == "ekologi"

    if ar_skyfall:
        if hi < 0.02:
            return 0.02, 0.05, 0.15, "Ingen förändring ≥ 2 cm", 2
        if hi <= 0.12:
            return 0.02, 0.05, 0.10, None, 2
        if hi <= 0.28:
            return 0.02, 0.10, 0.20, None, 2
        if hi <= 0.75:
            return 0.05, 0.20, 0.50, None, 2
        return 0.10, 0.20, 0.50, None, 2

    if ar_sol:
        enh = enhet or "tim"
        if hi < 0.5:
            return 0.5, 1.0, 2.0, f"Ingen förändring ≥ 0,5 {enh}", None
        if hi <= 2.5:
            return 0.5, 1.0, 2.0, None, None
        return 0.5, 2.0, 4.0, None, None

    if ar_eko:
        if hi < 0.02:
            return 0.02, 0.10, 0.25, "Ingen förändring ≥ 0,02", 2
        if hi <= 0.30:
            return 0.02, 0.10, 0.20, None, 2
        return 0.05, 0.20, 0.50, None, 2

    golv = max(tr, 0.02) if hi <= 2.0 else max(tr, 0.5)
    if hi < golv:
        g1 = _runda_125(golv, golv=0.01)
        g2 = _runda_125(g1 * 2.5, golv=g1 * 2.0)
        g3 = _runda_125(g2 * 2.5, golv=g2 * 2.0)
        return g1, g2, g3, f"Ingen förändring ≥ {_formatera_tal_sv(g1)}", None
    g1 = _runda_125(max(tr, hi * 0.08), golv=golv)
    g2 = _runda_125(max(g1 * 2.0, hi * 0.35), golv=g1 * 2.0)
    g3 = _runda_125(max(g2 * 2.0, hi * 0.72), golv=g2 * 2.0)
    return g1, g2, g3, None, None


def _bygg_skillnadsstil(
    *,
    roll: str,
    doman: str,
    storhet: str,
    enhet: str,
    max_abs: float,
    troskel: float = 0.0,
) -> FigurStil:
    """Bygger en symmetrisk, färgblindsvänlig divergerande skala med rundade klassgränser."""
    g1, g2, g3, neutral_text, fasta_dec = _runda_skillnadsgranser(
        roll=roll,
        doman=doman,
        enhet=enhet,
        max_abs=max_abs,
        troskel=troskel,
    )
    enhet_visa = enhet or ("relativ, 0–1" if roll in ("stromtathet", "kansighet") else "")
    suf = f" {enhet}" if enhet else ""
    s1 = _formatera_tal_sv(g1, fasta_dec=fasta_dec)
    s2 = _formatera_tal_sv(g2, fasta_dec=fasta_dec)
    s3 = _formatera_tal_sv(g3, fasta_dec=fasta_dec)
    noll_etikett = (
        f"−{s1} – +{s1}{suf} ({neutral_text})"
        if neutral_text
        else f"−{s1} – +{s1}{suf} (oförändrat)"
    )
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
            FargKlass(-g1, g1, FARG_KARTYTA, noll_etikett),
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
    vatten_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
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

    if vatten_mask is not None:
        vmask = np.asarray(vatten_mask, dtype=bool)
        if vmask.shape != arr.shape:
            raise FigurFel(
                f"vatten_mask har form {vmask.shape}, men rastret har form {arr.shape}."
            )
        rgb[vmask] = FARG_VATTEN

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


_MS_KANTER: Final[dict[int, tuple[tuple[int, int], ...]]] = {
    1: ((3, 2),),
    2: ((2, 1),),
    3: ((3, 1),),
    4: ((0, 1),),
    5: ((3, 0), (2, 1)),
    6: ((0, 2),),
    7: ((3, 0),),
    8: ((3, 0),),
    9: ((0, 2),),
    10: ((3, 2), (0, 1)),
    11: ((0, 1),),
    12: ((3, 1),),
    13: ((2, 1),),
    14: ((3, 2),),
}


def _slata_mark(z: NDArray[np.float64]) -> NDArray[np.float64]:
    """Lätt 3×3-utjämning av markhöjd (bara ändliga grannar) för rena höjdkurvor."""
    hojd, bredd = z.shape
    if hojd < 3 or bredd < 3:
        return z
    giltig = np.isfinite(z)
    fyll = np.where(giltig, z, 0.0)
    summa = np.zeros_like(fyll)
    antal = np.zeros_like(fyll)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            r0s, r1s = max(0, dr), hojd + min(0, dr)
            c0s, c1s = max(0, dc), bredd + min(0, dc)
            r0d, r1d = max(0, -dr), hojd + min(0, -dr)
            c0d, c1d = max(0, -dc), bredd + min(0, -dc)
            summa[r0d:r1d, c0d:c1d] += fyll[r0s:r1s, c0s:c1s]
            antal[r0d:r1d, c0d:c1d] += giltig[r0s:r1s, c0s:c1s]
    ut = np.full_like(z, np.nan)
    ok = giltig & (antal > 0.0)
    ut[ok] = summa[ok] / antal[ok]
    return ut


def _rita_hojdkurvor(
    karta_img: Image.Image,
    markhojd_m: NDArray[np.floating[Any]],
    geo: GeoTransformInfo,
    vis_w: int,
    vis_h: int,
    *,
    byggnad_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    vatten_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    ekvidistans_m: float | None = None,
) -> tuple[Image.Image, float | None]:
    """Ritar subtila höjdkurvor ur ``markhojd_m`` via ren NumPy marching squares."""
    z = np.asarray(markhojd_m, dtype=np.float64).copy()
    if z.ndim != 2 or z.shape[0] < 2 or z.shape[1] < 2:
        return karta_img, None
    if byggnad_mask is not None and np.asarray(byggnad_mask).shape == z.shape:
        z[np.asarray(byggnad_mask, dtype=bool)] = np.nan
    if vatten_mask is not None and np.asarray(vatten_mask).shape == z.shape:
        z[np.asarray(vatten_mask, dtype=bool)] = np.nan
    z = _slata_mark(z)
    giltiga = z[np.isfinite(z)]
    if giltiga.size < 4:
        return karta_img, None
    z_min = float(np.min(giltiga))
    z_max = float(np.max(giltiga))
    spann = z_max - z_min
    if not math.isfinite(spann) or spann < 0.25:
        return karta_img, None

    if ekvidistans_m is not None and ekvidistans_m > 0.0:
        ekv = float(ekvidistans_m)
    elif spann <= 15.0:
        ekv = 1.0
    elif spann <= 40.0:
        ekv = 2.0
    else:
        ekv = 5.0
    if math.floor(z_max / ekv) < math.ceil(z_min / ekv) and ekvidistans_m is None:
        ekv = 0.5
    n_start = math.ceil(z_min / ekv)
    n_slut = math.floor(z_max / ekv)
    if n_slut < n_start:
        return karta_img, None
    while (n_slut - n_start + 1) > 25 and ekvidistans_m is None:
        ekv *= 2.0
        n_start = math.ceil(z_min / ekv)
        n_slut = math.floor(z_max / ekv)

    nivaer = [k * ekv for k in range(n_start, n_slut + 1)]
    if not nivaer:
        return karta_img, None

    # Nedsampla lätt för mycket stora raster så kurvorna är snabba och rena.
    steg = max(1, int(math.ceil(max(z.shape) / 220.0)))
    zg = z[::steg, ::steg] if steg > 1 else z
    gh, gb = zg.shape
    if gh < 2 or gb < 2:
        return karta_img, None

    z00 = zg[:-1, :-1]
    z01 = zg[:-1, 1:]
    z11 = zg[1:, 1:]
    z10 = zg[1:, :-1]
    kvad_ok = np.isfinite(z00) & np.isfinite(z01) & np.isfinite(z11) & np.isfinite(z10)
    if not bool(np.any(kvad_ok)):
        return karta_img, None

    sx = vis_w / max(geo.bredd, 1)
    sy = vis_h / max(geo.hojd, 1)
    overlay = Image.new("RGBA", karta_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def _interp(va: float, vb: float, lv: float) -> float:
        d = vb - va
        if abs(d) < 1e-9:
            return 0.5
        return max(0.0, min(1.0, (lv - va) / d))

    def _kant_pt(
        r: int, c: int, kant: int, a: float, b: float, d: float, e: float, lv: float
    ) -> tuple[float, float]:
        if kant == 0:
            t = _interp(a, b, lv)
            return ((c + t) * steg + 0.5) * sx, (r * steg + 0.5) * sy
        if kant == 1:
            t = _interp(b, d, lv)
            return ((c + 1) * steg + 0.5) * sx, ((r + t) * steg + 0.5) * sy
        if kant == 2:
            t = _interp(e, d, lv)
            return ((c + t) * steg + 0.5) * sx, ((r + 1) * steg + 0.5) * sy
        t = _interp(a, e, lv)
        return (c * steg + 0.5) * sx, ((r + t) * steg + 0.5) * sy

    ritade = 0
    for lv in nivaer:
        kod = (
            ((z00 >= lv).astype(np.uint8) << 3)
            | ((z01 >= lv).astype(np.uint8) << 2)
            | ((z11 >= lv).astype(np.uint8) << 1)
            | (z10 >= lv).astype(np.uint8)
        )
        aktiv = kvad_ok & (kod > 0) & (kod < 15)
        rs, cs = np.nonzero(aktiv)
        if rs.size == 0:
            continue
        huvud = abs(lv % (5.0 * ekv)) < 1e-6 or abs((5.0 * ekv) - (lv % (5.0 * ekv))) < 1e-6
        farg = (*FARG_HOJDKURVA, 165 if huvud else 115)
        bredd = 2 if huvud else 1
        for r_i, c_i in zip(rs.tolist(), cs.tolist(), strict=True):
            a = float(z00[r_i, c_i])
            b = float(z01[r_i, c_i])
            d = float(z11[r_i, c_i])
            e = float(z10[r_i, c_i])
            for k1, k2 in _MS_KANTER[int(kod[r_i, c_i])]:
                p1 = _kant_pt(r_i, c_i, k1, a, b, d, e, lv)
                p2 = _kant_pt(r_i, c_i, k2, a, b, d, e, lv)
                draw.line([p1, p2], fill=farg, width=bredd)
                ritade += 1

    if ritade == 0:
        return karta_img, None
    return Image.alpha_composite(karta_img.convert("RGBA"), overlay).convert("RGB"), ekv


def _rita_baskarta_linjer_3011(
    karta_img: Image.Image,
    linjer_3011: Sequence[Sequence[Sequence[float]]],
    geo: GeoTransformInfo,
    vis_w: int,
    vis_h: int,
) -> tuple[Image.Image, int]:
    """Ritar vägkanter / kantstenar (EPSG:3011) i mjuk varmgrå linje (#BDB7A8)."""
    skala_x = vis_w / (geo.bredd * geo.pixelstorlek_x)
    skala_y = vis_h / (geo.hojd * geo.pixelstorlek_y)
    m_per_px = (geo.bredd * geo.pixelstorlek_x) / max(vis_w, 1)
    linjebredd = 2 if m_per_px <= 0.6 else 1
    overlay = Image.new("RGBA", karta_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    ritade = 0
    for linje in linjer_3011:
        if len(linje) < 2:
            continue
        pts = [
            ((float(p[0]) - geo.x_min) * skala_x, (geo.y_max - float(p[1])) * skala_y)
            for p in linje
            if len(p) >= 2
        ]
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) < 0 or min(xs) > vis_w or max(ys) < 0 or min(ys) > vis_h:
            continue
        draw.line(pts, fill=(*FARG_VAGKANT, 235), width=linjebredd)
        ritade += 1
    if ritade == 0:
        return karta_img, 0
    return Image.alpha_composite(karta_img.convert("RGBA"), overlay).convert("RGB"), ritade


def _rita_flodesvagar_3011(
    karta_img: Image.Image,
    flodesvagar_3011: Sequence[Sequence[Sequence[float]]],
    geo: GeoTransformInfo,
    vis_w: int,
    vis_h: int,
) -> tuple[Image.Image, int]:
    """Ritar flödesvägar med riktningspilar i dämpad skifferblå ton (#2B5B7A)."""
    skala_x = vis_w / (geo.bredd * geo.pixelstorlek_x)
    skala_y = vis_h / (geo.hojd * geo.pixelstorlek_y)
    overlay = Image.new("RGBA", karta_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    ritade = 0
    for linje in flodesvagar_3011:
        if len(linje) < 2:
            continue
        pts = [
            ((float(p[0]) - geo.x_min) * skala_x, (geo.y_max - float(p[1])) * skala_y)
            for p in linje
            if len(p) >= 2
        ]
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) < 0 or min(xs) > vis_w or max(ys) < 0 or min(ys) > vis_h:
            continue
        tot_px = sum(
            math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            for i in range(len(pts) - 1)
        )
        if tot_px < 14.0:
            continue
        draw.line(pts, fill=(*FARG_FLODESVAG, 210), width=2)
        ritade += 1
        # Rita riktningspilar längs flödesvägen (nedströms) med jämnt avstånd.
        ack = 0.0
        nasta_pil = min(tot_px * 0.55, 42.0)
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            dx, dy = x1 - x0, y1 - y0
            seg = math.hypot(dx, dy)
            if seg < 1e-3:
                continue
            ux, uy = dx / seg, dy / seg
            while ack + seg >= nasta_pil and nasta_pil <= tot_px - 6.0:
                t = (nasta_pil - ack) / seg
                px, py = x0 + dx * t, y0 + dy * t
                if 6 <= px <= vis_w - 6 and 6 <= py <= vis_h - 6:
                    pil_l = 7.0
                    pil_b = 4.2
                    spets = (px + ux * 2.0, py + uy * 2.0)
                    v_h = (px - ux * pil_l - uy * pil_b, py - uy * pil_l + ux * pil_b)
                    h_h = (px - ux * pil_l + uy * pil_b, py - uy * pil_l - ux * pil_b)
                    draw.polygon([spets, v_h, h_h], fill=(*FARG_FLODESVAG, 240))
                nasta_pil += 78.0
            ack += seg
    if ritade == 0:
        return karta_img, 0
    return Image.alpha_composite(karta_img.convert("RGBA"), overlay).convert("RGB"), ritade


def _rita_markorer_3011(
    karta_img: Image.Image,
    markorer_3011: Sequence[Mapping[str, Any]],
    geo: GeoTransformInfo,
    vis_w: int,
    vis_h: int,
) -> tuple[Image.Image, int]:
    """Ritar numrerade lågpunktsmarkörer (P1–P3) med tydlig kontrastbricka."""
    skala_x = vis_w / (geo.bredd * geo.pixelstorlek_x)
    skala_y = vis_h / (geo.hojd * geo.pixelstorlek_y)
    overlay = Image.new("RGBA", karta_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_p = _typsnitt("LiberationSans-Bold.ttf", 11)
    ritade = 0
    for m in markorer_3011:
        pid = str(m.get("id") or "").strip()
        if not pid:
            continue
        e = float(m["e_m"])
        n = float(m["n_m"])
        px = (e - geo.x_min) * skala_x
        py = (geo.y_max - n) * skala_y
        if px < 4 or px > vis_w - 4 or py < 4 or py > vis_h - 4:
            continue
        # Håll brickan inne i kartytan så texten aldrig klipps mot kanten.
        cx = max(14.0, min(vis_w - 14.0, px))
        cy = max(14.0, min(vis_h - 14.0, py))
        draw.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], fill=(*FARG_BAKGRUND, 245))
        draw.ellipse(
            [cx - 11, cy - 11, cx + 11, cy + 11],
            fill=(*FARG_MARKOR, 255),
            outline=(*FARG_BAKGRUND, 255),
            width=1,
        )
        tw, th = _text_storlek(draw, pid, font_p)
        draw.text((cx - tw / 2.0, cy - th / 2.0 - 1.5), pid, fill=(255, 255, 255, 255), font=font_p)
        ritade += 1
    if ritade == 0:
        return karta_img, 0
    return Image.alpha_composite(karta_img.convert("RGBA"), overlay).convert("RGB"), ritade


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


def _sanera_fot_kalla(kalla: str | None, befintlig_fot: str) -> str:
    """Tar bort dubbletter av CRS/höjdsystem som redan står i spårbarhetsfoten."""
    if not kalla:
        return ""
    bas_lag = befintlig_fot.lower()
    delar: list[str] = []
    for raw in kalla.split("·"):
        del_ = raw.strip()
        if not del_:
            continue
        if del_.lower() in bas_lag:
            continue
        if del_ not in delar:
            delar.append(del_)
    return " · ".join(delar)


def rendera_kartfigur(
    raster: NDArray[np.floating[Any]],
    stil: str | FigurStil,
    *,
    titel: str,
    undertitel: str = "",
    metodstatus: str = "Skissunderlag",
    geo: GeoTransformInfo | None = None,
    byggnad_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    vatten_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    byggnader_3011: Sequence[Mapping[str, Any]] | None = None,
    projektpolygon_3011: Sequence[Sequence[float]] | None = None,
    baskarta_linjer_3011: Sequence[Sequence[Sequence[float]]] | None = None,
    markhojd_m: NDArray[np.floating[Any]] | None = None,
    hojdkurvor_ekvidistans_m: float | None = None,
    flodesvagar_3011: Sequence[Sequence[Sequence[float]]] | None = None,
    markorer_3011: Sequence[Mapping[str, Any]] | None = None,
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
    if baskarta_linjer_3011 and geo is None:
        raise FigurFel("geo (GeoTransformInfo) krävs för att rita baskarta_linjer_3011.")
    if flodesvagar_3011 and geo is None:
        raise FigurFel("geo (GeoTransformInfo) krävs för att rita flodesvagar_3011.")
    if markorer_3011 and geo is None:
        raise FigurFel("geo (GeoTransformInfo) krävs för att rita markorer_3011.")

    fstil = hamta_figurstil(stil)
    rgb = klassificera_raster(raster, fstil, byggnad_mask=byggnad_mask, vatten_mask=vatten_mask)
    har_vatten = vatten_mask is not None and bool(np.any(np.asarray(vatten_mask, dtype=bool)))
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

    ritad_ekv: float | None = None
    if markhojd_m is not None and geo is not None:
        karta_img, ritad_ekv = _rita_hojdkurvor(
            karta_img,
            markhojd_m,
            geo,
            vis_w,
            vis_h,
            byggnad_mask=byggnad_mask,
            vatten_mask=vatten_mask,
            ekvidistans_m=hojdkurvor_ekvidistans_m,
        )
    antal_baskarta = 0
    if baskarta_linjer_3011 and geo is not None:
        karta_img, antal_baskarta = _rita_baskarta_linjer_3011(
            karta_img, baskarta_linjer_3011, geo, vis_w, vis_h
        )
    antal_flode = 0
    if flodesvagar_3011 and geo is not None:
        karta_img, antal_flode = _rita_flodesvagar_3011(
            karta_img, flodesvagar_3011, geo, vis_w, vis_h
        )
    if byggnader_3011 and geo is not None:
        karta_img = _rita_byggnader_3011(karta_img, byggnader_3011, geo, vis_w, vis_h)
    if projektpolygon_3011 is not None and geo is not None:
        karta_img = _rita_projektpolygon(karta_img, projektpolygon_3011, geo, vis_w, vis_h)
    antal_markorer = 0
    if markorer_3011 and geo is not None:
        karta_img, antal_markorer = _rita_markorer_3011(
            karta_img, markorer_3011, geo, vis_w, vis_h
        )
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
    if har_vatten:
        extra_poster.append(("vatten", "Vattenyta"))
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
    if antal_baskarta > 0:
        extra_poster.append(("vagkant", "Vägkant / kantsten"))
    if ritad_ekv is not None:
        ekv_txt = _formatera_tal_sv(ritad_ekv)
        extra_poster.append(("hojdkurva", f"Höjdkurvor ({ekv_txt} m)"))
    if antal_flode > 0:
        extra_poster.append(("flodesvag", "Flödesväg"))
    if antal_markorer > 0:
        extra_poster.append(("markor", "Djupaste lågpunkt (P1–P3)"))

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
    kalla_ren = _sanera_fot_kalla(kalla, fot_v)
    if kalla_ren:
        fot_v = f"{fot_v} · Källa: {kalla_ren}"
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
        elif typ == "vatten":
            rit.rectangle(
                [x, sy0, x + swatch_w, sy1],
                fill=FARG_VATTEN,
                outline=FARG_VATTEN_KANT,
                width=1,
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
        elif typ == "vagkant":
            ym = (sy0 + sy1) // 2
            rit.line([(x, ym), (x + swatch_w, ym)], fill=FARG_VAGKANT, width=2)
        elif typ == "hojdkurva":
            ym = (sy0 + sy1) // 2
            rit.line([(x, ym), (x + swatch_w, ym)], fill=FARG_HOJDKURVA, width=1)
        elif typ == "flodesvag":
            ym = (sy0 + sy1) // 2
            rit.line([(x, ym), (x + swatch_w - 4, ym)], fill=FARG_FLODESVAG, width=2)
            rit.polygon(
                [(x + swatch_w, ym), (x + swatch_w - 6, ym - 3), (x + swatch_w - 6, ym + 3)],
                fill=FARG_FLODESVAG,
            )
        elif typ == "markor":
            cx = x + swatch_w // 2
            cy = (sy0 + sy1) // 2
            rit.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=FARG_MARKOR)
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
    vatten_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    byggnader_3011: Sequence[Mapping[str, Any]] | None = None,
    projektpolygon_3011: Sequence[Sequence[float]] | None = None,
    baskarta_linjer_3011: Sequence[Sequence[Sequence[float]]] | None = None,
    markhojd_m: NDArray[np.floating[Any]] | None = None,
    hojdkurvor_ekvidistans_m: float | None = None,
    flodesvagar_3011: Sequence[Sequence[Sequence[float]]] | None = None,
    markorer_3011: Sequence[Mapping[str, Any]] | None = None,
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
        vatten_mask=vatten_mask,
        byggnader_3011=byggnader_3011,
        projektpolygon_3011=projektpolygon_3011,
        baskarta_linjer_3011=baskarta_linjer_3011,
        markhojd_m=markhojd_m,
        hojdkurvor_ekvidistans_m=hojdkurvor_ekvidistans_m,
        flodesvagar_3011=flodesvagar_3011,
        markorer_3011=markorer_3011,
        ut_fil=mal,
        run_hash=run_hash,
        anmarkning=anmarkning,
        kalla=kalla,
        karta_max_px=karta_max_px,
        legend_placering=legend_placering,
    )
    return mal


def rita_sektionsfigur(
    avstand_m: Sequence[float] | NDArray[np.floating[Any]],
    mark_rh2000_m: Sequence[float] | NDArray[np.floating[Any]],
    *,
    vatten_fore_m: Sequence[float] | NDArray[np.floating[Any]] | None = None,
    vatten_efter_m: Sequence[float] | NDArray[np.floating[Any]] | None = None,
    byggnad_mask: Sequence[bool] | NDArray[np.bool_] | None = None,
    byggnad_hojd_rh2000_m: Sequence[float] | NDArray[np.floating[Any]] | None = None,
    byggnad_ny_mask: Sequence[bool] | NDArray[np.bool_] | None = None,
    titel: str = "Tvärsektion genom djupaste lågpunkt",
    undertitel: str = "",
    metodstatus: str = "Skissunderlag",
    markor_avstand_m: float | None = None,
    markor_etikett: str = "P1",
    ut_fil: str | Path | None = None,
    run_hash: str | None = None,
    kalla: str | None = None,
    bredd_px: int = 1120,
    hojd_px: int = 440,
) -> Image.Image:
    """Ritar en höjd- och vattensektion i RH 2000 (marklinje, byggnader, vattenyta före/efter)."""
    dist = np.asarray(avstand_m, dtype=np.float64)
    mark = np.asarray(mark_rh2000_m, dtype=np.float64)
    if dist.ndim != 1 or mark.ndim != 1 or dist.size < 2 or dist.size != mark.size:
        raise FigurFel("avstand_m och mark_rh2000_m måste vara 1D-vektorer med samma längd (≥ 2).")
    giltig_mark = np.isfinite(dist) & np.isfinite(mark)
    if int(np.count_nonzero(giltig_mark)) < 2:
        raise FigurFel("Tvärsektionen saknar tillräckligt många giltiga markhöjder.")

    def _opt_f(
        seq: Sequence[float] | NDArray[np.floating[Any]] | None, namn: str
    ) -> NDArray[np.float64] | None:
        if seq is None:
            return None
        arr = np.asarray(seq, dtype=np.float64)
        if arr.shape != dist.shape:
            raise FigurFel(f"{namn} har längd {arr.shape}, väntade {dist.shape}.")
        return arr

    def _opt_b(
        seq: Sequence[bool] | NDArray[np.bool_] | None, namn: str
    ) -> NDArray[np.bool_] | None:
        if seq is None:
            return None
        arr = np.asarray(seq, dtype=bool)
        if arr.shape != dist.shape:
            raise FigurFel(f"{namn} har längd {arr.shape}, väntade {dist.shape}.")
        return arr

    vf = _opt_f(vatten_fore_m, "vatten_fore_m")
    ve = _opt_f(vatten_efter_m, "vatten_efter_m")
    bmask = _opt_b(byggnad_mask, "byggnad_mask")
    bhojd = _opt_f(byggnad_hojd_rh2000_m, "byggnad_hojd_rh2000_m")
    bny = _opt_b(byggnad_ny_mask, "byggnad_ny_mask")

    x_min = float(np.nanmin(dist[giltig_mark]))
    x_max = float(np.nanmax(dist[giltig_mark]))
    if x_max <= x_min:
        raise FigurFel("avstand_m måste ha positiv utsträckning.")

    # Höjdskala styrs av mark + vattenyta (byggnader klipps vid taket så vattendjup syns tydligt).
    y_kand = [mark[giltig_mark]]
    if vf is not None:
        ok = giltig_mark & np.isfinite(vf) & (vf > 0.0)
        if bool(np.any(ok)):
            y_kand.append(mark[ok] + vf[ok])
    if ve is not None:
        ok = giltig_mark & np.isfinite(ve) & (ve > 0.0)
        if bool(np.any(ok)):
            y_kand.append(mark[ok] + ve[ok])
    alla_y = np.concatenate(y_kand)
    z_lo = float(np.min(alla_y))
    z_hi = float(np.max(alla_y))
    spann_z = max(z_hi - z_lo, 0.8)
    y_bot = z_lo - max(0.35, spann_z * 0.18)
    y_top = z_hi + max(0.75, spann_z * 0.45)

    # Snygga Y-axelsteg i RH 2000
    y_spann = y_top - y_bot
    if y_spann <= 3.5:
        y_steg = 0.5
    elif y_spann <= 8.0:
        y_steg = 1.0
    elif y_spann <= 18.0:
        y_steg = 2.0
    else:
        y_steg = 5.0
    y_bot = math.floor(y_bot / y_steg) * y_steg
    y_top = math.ceil(y_top / y_steg) * y_steg

    font_titel = _typsnitt("Gelasio-Bold.ttf", 20)
    font_under = _typsnitt("LiberationSans-Regular.ttf", 12)
    font_badge = _typsnitt("LiberationSans-Bold.ttf", 11)
    font_ax = _typsnitt("LiberationSans-Regular.ttf", 11)
    font_ax_b = _typsnitt("LiberationSans-Bold.ttf", 11)
    font_fot = _typsnitt("LiberationSans-Regular.ttf", 10)

    duk = Image.new("RGB", (bredd_px, hojd_px), FARG_BAKGRUND)
    rit = ImageDraw.Draw(duk)

    marg_x = 24
    marg_topp = 18
    titel_h = _text_storlek(rit, titel, font_titel)[1]
    under_h = _text_storlek(rit, undertitel, font_under)[1] if undertitel else 0
    huvud_h = titel_h + (5 + under_h if undertitel else 0) + 10

    rit.text((marg_x, marg_topp), titel, fill=FARG_TEXT, font=font_titel)
    if undertitel:
        rit.text((marg_x, marg_topp + titel_h + 5), undertitel, fill=FARG_TEXT_SEK, font=font_under)
    if metodstatus:
        bw, bh = _text_storlek(rit, metodstatus, font_badge)
        bx1 = bredd_px - marg_x
        bx0 = bx1 - (bw + 18)
        by0 = marg_topp + 1
        by1 = by0 + bh + 9
        rit.rounded_rectangle(
            [bx0, by0, bx1, by1], radius=9, fill=FARG_BADGE_BG, outline=FARG_BADGE_KANT, width=1
        )
        rit.text((bx0 + 9, by0 + 4), metodstatus, fill=FARG_POLYGON, font=font_badge)

    huvud_y = marg_topp + huvud_h
    rit.line([(marg_x, huvud_y), (bredd_px - marg_x, huvud_y)], fill=FARG_LINJE, width=1)

    # Plotruta
    plot_x0 = marg_x + 76
    plot_x1 = bredd_px - marg_x - 16
    plot_y0 = huvud_y + 24
    plot_y1 = hojd_px - 86
    plot_w = max(plot_x1 - plot_x0, 100)
    plot_h = max(plot_y1 - plot_y0, 80)

    def _px(x_m: float) -> float:
        return plot_x0 + (x_m - x_min) / (x_max - x_min) * plot_w

    def _py(z_m: float) -> float:
        t = (z_m - y_bot) / max(y_top - y_bot, 1e-6)
        return plot_y1 - max(0.0, min(1.0, t)) * plot_h

    rit.rectangle(
        [plot_x0, plot_y0, plot_x1, plot_y1], fill=(255, 255, 255), outline=FARG_LINJE, width=1
    )

    # Horisontella höjdlinjer (RH 2000)
    n_y0 = int(round(y_bot / y_steg))
    n_y1 = int(round(y_top / y_steg))
    for k in range(n_y0, n_y1 + 1):
        zv = k * y_steg
        yy = _py(zv)
        rit.line([(plot_x0, yy), (plot_x1, yy)], fill=(0xEA, 0xE6, 0xDC), width=1)
        if zv >= 0:
            etik = f"+{_formatera_tal_sv(zv, fasta_dec=1)} m"
        else:
            etik = f"−{_formatera_tal_sv(abs(zv), fasta_dec=1)} m"
        tw, th = _text_storlek(rit, etik, font_ax)
        rit.text((plot_x0 - tw - 8, yy - th / 2.0), etik, fill=FARG_TEXT_SEK, font=font_ax)
    rit.text((marg_x, plot_y0 - 17), "Höjd (RH 2000)", fill=FARG_TEXT_SEK, font=font_ax_b)

    # Vertikala meterlinjer
    x_spann = x_max - x_min
    if x_spann <= 35.0:
        x_steg = 5.0
    elif x_spann <= 90.0:
        x_steg = 10.0
    elif x_spann <= 220.0:
        x_steg = 20.0
    else:
        x_steg = 50.0
    xv = math.ceil(x_min / x_steg) * x_steg
    while xv <= x_max + 1e-6:
        xx = _px(xv)
        rit.line([(xx, plot_y0), (xx, plot_y1)], fill=(0xF0, 0xED, 0xE5), width=1)
        etik = f"{_formatera_meter(xv)} m"
        tw, _ = _text_storlek(rit, etik, font_ax)
        rit.text((xx - tw / 2.0, plot_y1 + 5), etik, fill=FARG_TEXT_SEK, font=font_ax)
        xv += x_steg

    # Vattenlager (före + efter) som sammanhängande polygoner ovanför marklinjen
    ov = Image.new("RGBA", duk.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)

    def _rita_vatten(
        v_arr: NDArray[np.float64],
        fyll: tuple[int, int, int, int],
        linje: tuple[int, int, int, int],
    ) -> None:
        n = dist.size
        i = 0
        while i < n:
            if not (giltig_mark[i] and math.isfinite(float(v_arr[i])) and float(v_arr[i]) > 0.01):
                i += 1
                continue
            j = i
            while (
                j < n
                and giltig_mark[j]
                and math.isfinite(float(v_arr[j]))
                and float(v_arr[j]) > 0.01
            ):
                j += 1
            topp = [(_px(float(dist[k])), _py(float(mark[k] + v_arr[k]))) for k in range(i, j)]
            botten = [(_px(float(dist[k])), _py(float(mark[k]))) for k in range(j - 1, i - 1, -1)]
            if len(topp) == 1:
                x_c = topp[0][0]
                hw = max(3.0, plot_w / max(n * 2.0, 1.0))
                od.rectangle(
                    [x_c - hw, topp[0][1], x_c + hw, botten[0][1]], fill=fyll, outline=linje
                )
            else:
                od.polygon([*topp, *botten], fill=fyll)
                od.line(topp, fill=linje, width=2)
            i = j

    har_v_fore = vf is not None and bool(np.any(giltig_mark & np.isfinite(vf) & (vf > 0.01)))
    har_v_efter = ve is not None and bool(np.any(giltig_mark & np.isfinite(ve) & (ve > 0.01)))
    if vf is not None and har_v_fore:
        _rita_vatten(vf, (*FARG_VATTEN_FORE, 130), (*FARG_VATTEN_FORE, 240))
    if ve is not None and har_v_efter:
        _rita_vatten(ve, (*FARG_VATTEN_EFTER, 155), (*FARG_VATTEN_EFTER_LINJE, 255))
    duk = Image.alpha_composite(duk.convert("RGBA"), ov).convert("RGB")
    rit = ImageDraw.Draw(duk)

    # Markfyllning och marklinje
    idx_ok = np.nonzero(giltig_mark)[0]
    mark_pts = [(_px(float(dist[i])), _py(float(mark[i]))) for i in idx_ok]
    if len(mark_pts) >= 2:
        fyll_poly = [(mark_pts[0][0], float(plot_y1)), *mark_pts, (mark_pts[-1][0], float(plot_y1))]
        rit.polygon(fyll_poly, fill=FARG_MARK_FYLL)
        rit.line(mark_pts, fill=FARG_TEXT_SEK, width=3)

    # Byggnadsblock (befintliga + nya i terrakotta)
    har_bygg = False
    har_bygg_ny = False
    if bmask is not None and bool(np.any(bmask & giltig_mark)):
        n = dist.size
        i = 0
        while i < n:
            if not (giltig_mark[i] and bool(bmask[i])):
                i += 1
                continue
            ar_ny = bny is not None and bool(bny[i])
            j = i
            while (
                j < n
                and giltig_mark[j]
                and bool(bmask[j])
                and (bny is None or bool(bny[j]) == ar_ny)
            ):
                j += 1
            x_l = _px(float(dist[i]))
            x_r = _px(float(dist[j - 1]))
            if x_r - x_l < 4.0:
                x_l -= 2.0
                x_r += 2.0
            bas_y = min(_py(float(mark[k])) for k in range(i, j))
            if bhojd is not None and np.any(np.isfinite(bhojd[i:j])):
                tak_z = float(np.nanmax(bhojd[i:j]))
                tak_y = max(float(plot_y0 + 3), _py(tak_z))
            else:
                tak_y = float(plot_y0 + 6)
            if ar_ny:
                har_bygg_ny = True
                fyll_b = (0xE8, 0xC6, 0xBC)
                kant_b = FARG_BYGGNAD_NY
            else:
                har_bygg = True
                fyll_b = FARG_BYGGNAD
                kant_b = FARG_BYGGNAD_KANT
            rit.rectangle([x_l, tak_y, x_r, bas_y], fill=fyll_b, outline=kant_b, width=2)
            etik_b = "Ny byggnad" if ar_ny else "Byggnad"
            bw_t, bh_t = _text_storlek(rit, etik_b, font_fot)
            if (x_r - x_l) >= bw_t + 8 and (bas_y - tak_y) >= bh_t + 6:
                rit.text(
                    ((x_l + x_r - bw_t) / 2.0, (tak_y + bas_y - bh_t) / 2.0),
                    etik_b,
                    fill=kant_b if ar_ny else FARG_TEXT_SEK,
                    font=font_fot,
                )
            i = j

    # Markör för djupaste lågpunkt (P1)
    if markor_avstand_m is not None and x_min <= markor_avstand_m <= x_max:
        mx = _px(float(markor_avstand_m))
        rit.line([(mx, plot_y0 + 22), (mx, plot_y1)], fill=FARG_MARKOR, width=1)
        bw_m, bh_m = _text_storlek(rit, markor_etikett, font_badge)
        pw = max(24, bw_m + 10)
        rit.rounded_rectangle(
            [mx - pw / 2.0, plot_y0 + 4, mx + pw / 2.0, plot_y0 + 4 + bh_m + 6],
            radius=6,
            fill=FARG_MARKOR,
        )
        rit.text(
            (mx - bw_m / 2.0, plot_y0 + 6),
            markor_etikett,
            fill=(255, 255, 255),
            font=font_badge,
        )

    # Ram överst så kanterna förblir knivskarpa
    rit.rectangle([plot_x0, plot_y0, plot_x1, plot_y1], outline=FARG_LINJE, width=1)

    # Kompakt horisontell teckenförklaring under X-axeln
    leg_y = plot_y1 + 26
    lx = plot_x0
    leg_poster: list[tuple[str, tuple[int, int, int], tuple[int, int, int]]] = [
        ("Marklinje (RH 2000)", FARG_MARK_FYLL, FARG_TEXT_SEK),
    ]
    if har_v_fore:
        leg_poster.append(("Vattenyta före", FARG_VATTEN_FORE, FARG_VATTEN_FORE))
    if har_v_efter:
        leg_poster.append(("Vattenyta efter", FARG_VATTEN_EFTER, FARG_VATTEN_EFTER_LINJE))
    if har_bygg:
        leg_poster.append(("Befintlig byggnad", FARG_BYGGNAD, FARG_BYGGNAD_KANT))
    if har_bygg_ny:
        leg_poster.append(("Ny byggnad", (0xE8, 0xC6, 0xBC), FARG_BYGGNAD_NY))
    for namn, fyll_l, kant_l in leg_poster:
        rit.rectangle([lx, leg_y + 2, lx + 16, leg_y + 12], fill=fyll_l, outline=kant_l, width=1)
        rit.text((lx + 22, leg_y), namn, fill=FARG_TEXT, font=font_ax)
        lx += 22 + _text_storlek(rit, namn, font_ax)[0] + 18

    # Spårbarhetsfot + vertikal överförhöjning
    px_per_m_x = plot_w / max(x_max - x_min, 1e-6)
    px_per_m_y = plot_h / max(y_top - y_bot, 1e-6)
    overforh = px_per_m_y / max(px_per_m_x, 1e-6)
    fot_y = hojd_px - 32
    rit.line([(marg_x, fot_y), (bredd_px - marg_x, fot_y)], fill=FARG_LINJE, width=1)
    overf_s = f"{overforh:.1f}".replace(".", ",")
    fot_v = f"SWEREF 99 18 00 · RH 2000 · Vertikal överförhöjning: {overf_s}×"
    kalla_ren = _sanera_fot_kalla(kalla, fot_v)
    if kalla_ren:
        fot_v = f"{fot_v} · Källa: {kalla_ren}"
    rit.text((marg_x, fot_y + 8), fot_v, fill=FARG_TEXT_SEK, font=font_fot)
    if run_hash:
        fh_txt = f"run.json: {run_hash[:12]}"
        fhw, _ = _text_storlek(rit, fh_txt, font_fot)
        rit.text((bredd_px - marg_x - fhw, fot_y + 8), fh_txt, fill=FARG_TEXT_SEK, font=font_fot)

    if ut_fil is not None:
        mal = Path(ut_fil)
        mal.parent.mkdir(parents=True, exist_ok=True)
        duk.save(mal, format="PNG")
    return duk


__all__ = [
    "FARG_BAKGRUND",
    "FARG_BYGGNAD",
    "FARG_BYGGNAD_ANDRAD",
    "FARG_BYGGNAD_KANT",
    "FARG_BYGGNAD_NY",
    "FARG_FLODESVAG",
    "FARG_HOJDKURVA",
    "FARG_KARTYTA",
    "FARG_LINJE",
    "FARG_MARKOR",
    "FARG_POLYGON",
    "FARG_TEXT",
    "FARG_TEXT_SEK",
    "FARG_VAGKANT",
    "FARG_VATTEN",
    "FARG_VATTEN_KANT",
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
    "rita_sektionsfigur",
    "spara_kartfigur",
]
