"""Markklass: K1:s markslagskoder → Nord2000:s impedansklass, flödesresistivitet och råhet.

Tabellen ligger i `data/markklass_v1.toml`. Klassernas tabellvärden kommer ur metoddokumentet
(källa i filen). Översättningen markslag → klass är ett eget, preliminärt val. Inga tal står
i koden: saknas eller är ett värde tomt i filen kastar inläsningen `BullerFel`.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from buller.typer import BullerFel, Flyttal

STANDARDTABELL = Path(__file__).resolve().parent / "data" / "markklass_v1.toml"

KOD_OKANT = 0
# Koder som K1/v1 definierar utöver 0 (okänt).
K1_MARKSLAGSKODER: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9)
_ANTAL_KODER = 256  # markslag är uint8


@dataclass(frozen=True)
class Markklass:
    """Akustiska markegenskaper för ett markslag."""

    kod: int
    namn: str
    impedansklass: str
    flodesresistivitet_kNsm4: float
    rahetsklass: str
    rahet_m: float


@dataclass(frozen=True)
class Markklasstabell:
    """Inläst och validerad markklasstabell."""

    version: str
    preliminar: bool
    kalla: str
    impedansklasser_kNsm4: dict[str, float]
    rahetsklasser_m: dict[str, float]
    markslag: dict[int, Markklass]
    byggnadsyta: Markklass

    def klass(self, kod: int) -> Markklass:
        """Markklass för en markslagskod. Okänd kod ger `BullerFel` (ingen tyst gissning)."""
        if kod not in self.markslag:
            raise BullerFel(
                f"Markslagskod {kod} saknar översättning i markklasstabellen {self.version}."
            )
        return self.markslag[kod]

    def egenskaper_for_impedansklass(self, bokstav: str) -> tuple[float, float]:
        """Flödesresistivitet (kNs/m⁴) för en impedansklass, med råhet för klass utan råhet.

        Används när anroparen uttryckligen väljer en klass för okänt markslag.
        """
        if bokstav not in self.impedansklasser_kNsm4:
            raise BullerFel(
                f"Impedansklass '{bokstav}' finns inte i tabellen {self.version} "
                f"(finns: {', '.join(sorted(self.impedansklasser_kNsm4))})."
            )
        return self.impedansklasser_kNsm4[bokstav], self.byggnadsyta.rahet_m

    def uppslag(self) -> tuple[Flyttal, Flyttal]:
        """Uppslagsvektorer (256,) för flödesresistivitet och råhet, NaN för koder utan klass."""
        sigma = np.full(_ANTAL_KODER, np.nan)
        rahet = np.full(_ANTAL_KODER, np.nan)
        for kod, klass in self.markslag.items():
            sigma[kod] = klass.flodesresistivitet_kNsm4
            rahet[kod] = klass.rahet_m
        return sigma, rahet


def _positivt_tal(varde: Any, vad: str, *, tillat_noll: bool) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise BullerFel(f"Markklasstabell: {vad} saknar talvärde (fick {varde!r}).")
    tal = float(varde)
    if not math.isfinite(tal) or tal < 0.0 or (tal == 0.0 and not tillat_noll):
        raise BullerFel(f"Markklasstabell: {vad} har ogiltigt värde {tal}.")
    return tal


def _klasstabell(ra: dict[str, Any], nyckel: str, *, tillat_noll: bool) -> dict[str, float]:
    tabell = ra.get(nyckel)
    if not isinstance(tabell, dict) or not tabell:
        raise BullerFel(f"Markklasstabell: avsnittet [{nyckel}] saknas eller är tomt.")
    return {
        str(bokstav): _positivt_tal(v, f"[{nyckel}] {bokstav}", tillat_noll=tillat_noll)
        for bokstav, v in tabell.items()
    }


def _markklass(
    kod: int,
    post: Any,
    impedans: dict[str, float],
    rahet: dict[str, float],
    vad: str,
) -> Markklass:
    if not isinstance(post, dict):
        raise BullerFel(f"Markklasstabell: {vad} är inte en tabell.")
    imp = post.get("impedansklass")
    rah = post.get("rahetsklass")
    if not isinstance(imp, str) or imp not in impedans:
        raise BullerFel(f"Markklasstabell: {vad} har okänd eller tom impedansklass ({imp!r}).")
    if not isinstance(rah, str) or rah not in rahet:
        raise BullerFel(f"Markklasstabell: {vad} har okänd eller tom råhetsklass ({rah!r}).")
    return Markklass(
        kod=kod,
        namn=str(post.get("namn", "")),
        impedansklass=imp,
        flodesresistivitet_kNsm4=impedans[imp],
        rahetsklass=rah,
        rahet_m=rahet[rah],
    )


def las_markklasstabell(sokvag: str | Path | None = None) -> Markklasstabell:
    """Läser och validerar en markklasstabell (standard: `data/markklass_v1.toml`)."""
    fil = Path(sokvag) if sokvag is not None else STANDARDTABELL
    try:
        ra = tomllib.loads(fil.read_text(encoding="utf-8"))
    except OSError as fel:
        raise BullerFel(f"Markklasstabellen gick inte att läsa: {fil} ({fel}).") from fel
    except tomllib.TOMLDecodeError as fel:
        raise BullerFel(f"Markklasstabellen är inte giltig TOML: {fil} ({fel}).") from fel

    version = ra.get("version")
    if not isinstance(version, str) or not version:
        raise BullerFel(f"Markklasstabell {fil}: 'version' saknas.")
    preliminar = ra.get("preliminar")
    if not isinstance(preliminar, bool):
        raise BullerFel(f"Markklasstabell {fil}: 'preliminar' ska vara true eller false.")
    kalla = ra.get("kalla")
    if not isinstance(kalla, dict) or not kalla.get("dokument"):
        raise BullerFel(f"Markklasstabell {fil}: källa ([kalla] dokument) saknas.")

    impedans = _klasstabell(ra, "impedansklass", tillat_noll=False)
    rahet = _klasstabell(ra, "rahetsklass", tillat_noll=True)

    poster = ra.get("markslag")
    if not isinstance(poster, dict):
        raise BullerFel(f"Markklasstabell {fil}: avsnittet [markslag.*] saknas.")
    markslag: dict[int, Markklass] = {}
    for nyckel, post in poster.items():
        try:
            kod = int(nyckel)
        except ValueError as fel:
            raise BullerFel(
                f"Markklasstabell {fil}: markslagskod '{nyckel}' är inget heltal."
            ) from fel
        if kod == KOD_OKANT:
            raise BullerFel(
                f"Markklasstabell {fil}: kod {KOD_OKANT} (okänt) får inte översättas i tabellen."
            )
        if not 0 < kod < _ANTAL_KODER:
            raise BullerFel(f"Markklasstabell {fil}: markslagskod {kod} ligger utanför 1–255.")
        markslag[kod] = _markklass(kod, post, impedans, rahet, f"[markslag.{kod}]")
    saknade = [kod for kod in K1_MARKSLAGSKODER if kod not in markslag]
    if saknade:
        raise BullerFel(f"Markklasstabell {fil}: K1-koderna {saknade} saknar översättning.")

    byggnadsyta = _markklass(-1, ra.get("byggnadsyta"), impedans, rahet, "[byggnadsyta]")
    return Markklasstabell(
        version=version,
        preliminar=preliminar,
        kalla=f"{kalla['dokument']}; {kalla.get('impedansklasser', '')}",
        impedansklasser_kNsm4=impedans,
        rahetsklasser_m=rahet,
        markslag=markslag,
        byggnadsyta=byggnadsyta,
    )


@lru_cache(maxsize=1)
def standardtabell() -> Markklasstabell:
    """Standardtabellen (`markklass_v1.toml`), inläst en gång per process."""
    return las_markklasstabell()
