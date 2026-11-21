"""Markklass: K1:s markslagskoder → Nord2000:s impedansklass, flödesresistivitet och råhet.

Tabellen ligger i den gemensamma `data/markklasser.toml` (avsnitten `[buller]` och
`[markklass.N.buller]`), läses av `karna.geometri.las_markklasser` och är EN källa för alla domäner
(flyttad hit från `buller/scen/data/markklass_v1.toml`, BESLUT 2026-11-21). Versionsmärkningen
`markklass/v1` och märkningen preliminär följer med. Klassernas tabellvärden kommer ur
metoddokumentet
(källa i filen). Översättningen markslag → klass är ett eget, preliminärt val. Inga tal står i
koden:
saknas eller är ett värde tomt i filen kastar inläsningen `BullerFel`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from buller.typer import BullerFel, Flyttal
from karna.geometri import GeometriFel, las_markklasser
from karna.geometri.markklass import STANDARD_MARKKLASSER

STANDARDTABELL = STANDARD_MARKKLASSER

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


def las_markklasstabell(sokvag: str | Path | None = None) -> Markklasstabell:
    """Läser och validerar bullrets markklasser (standard: `data/markklasser.toml`)."""
    fil = Path(sokvag) if sokvag is not None else STANDARDTABELL
    try:
        gem = las_markklasser(fil)
    except GeometriFel as fel:
        raise BullerFel(f"Markklasstabellen gick inte att läsa: {fil} ({fel}).") from fel
    meta = gem.meta.get("buller")
    if meta is None:
        raise BullerFel(f"Markklasstabell {fil}: avsnittet [buller] saknas.")
    impedans = {str(k): float(v) for k, v in meta["impedansklass"].items()}
    rahet = {str(k): float(v) for k, v in meta["rahetsklass"].items()}

    markslag: dict[int, Markklass] = {}
    for kod in sorted(gem.namn):
        e = gem.egenskaper[kod].get("buller", {})
        if kod == KOD_OKANT:
            if e:
                raise BullerFel(
                    f"Markklasstabell {fil}: kod {KOD_OKANT} (okänt) får inte översättas."
                )
            continue
        if not e:
            raise BullerFel(f"Markklasstabell {fil}: K1-koden {kod} saknar översättning.")
        markslag[kod] = Markklass(
            kod=kod,
            namn=gem.namn[kod],
            impedansklass=str(e["impedansklass"]),
            flodesresistivitet_kNsm4=float(e["flodesresistivitet_kNsm4"]),
            rahetsklass=str(e["rahetsklass"]),
            rahet_m=float(e["rahet_m"]),
        )
    yta = meta["byggnadsyta"]
    byggnadsyta = Markklass(
        kod=-1,
        namn="byggnadsyta",
        impedansklass=str(yta["impedansklass"]),
        flodesresistivitet_kNsm4=impedans[str(yta["impedansklass"])],
        rahetsklass=str(yta["rahetsklass"]),
        rahet_m=rahet[str(yta["rahetsklass"])],
    )
    kalla = meta["kalla"]
    return Markklasstabell(
        version=str(meta["version"]),
        preliminar=bool(meta["preliminar"]),
        kalla=f"{kalla['dokument']}; {kalla.get('impedansklasser', '')}",
        impedansklasser_kNsm4=impedans,
        rahetsklasser_m=rahet,
        markslag=markslag,
        byggnadsyta=byggnadsyta,
    )


@lru_cache(maxsize=1)
def standardtabell() -> Markklasstabell:
    """Standardtabellen (`data/markklasser.toml`), inläst en gång per process."""
    return las_markklasstabell()
