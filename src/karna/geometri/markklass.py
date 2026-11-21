"""Översättning av K1 markslag till egenskaper per domän (vatten, luft, buller, dagsljus).

Tabellen är en versionsbar TOML-fil (`data/markklasser.toml`). Varje markslagskod har ett namn och
en tabell per domän; domänerna fyller i sina egna kolumner (luft/buller/dagsljus är tomma tills
respektive agent lägger till dem). Filens sha256 (`Markklasstabell.sha256`) ska in i recepten för
resultat som använder den.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from karna.geometri.rasterfil import GeometriFel

STANDARD_MARKKLASSER: Path = Path(__file__).resolve().parents[3] / "data" / "markklasser.toml"
DOMANER: tuple[str, ...] = ("vatten", "luft", "buller", "dagsljus")
GILTIGA_KODER: frozenset[int] = frozenset(range(10))


@dataclass(frozen=True)
class Markklasstabell:
    version: int
    namn: Mapping[int, str]
    egenskaper: Mapping[int, Mapping[str, Mapping[str, Any]]]
    sha256: str

    def egenskaper_for(self, kod: int, doman: str) -> Mapping[str, Any]:
        """Egenskaperna för en markslagskod i en domän (tom tabell om domänen inte fyllt i)."""
        if doman not in DOMANER:
            raise GeometriFel(f"Okänd domän '{doman}'. Giltiga: {', '.join(DOMANER)}.")
        if kod not in self.namn:
            raise GeometriFel(f"Okänd markslagskod {kod}.")
        return self.egenskaper[kod].get(doman, {})

    def raster(
        self, markslag: NDArray[np.uint8], doman: str, kolumn: str
    ) -> NDArray[np.float32]:
        """Ett rasterlager av en numerisk kolumn. Saknas kolumnen för en förekommande kod: fel."""
        lut = np.full(256, np.nan, dtype=np.float32)
        for kod in self.namn:
            e = self.egenskaper_for(kod, doman)
            if kolumn in e:
                lut[kod] = float(e[kolumn])
        ut = lut[markslag]
        saknas = np.unique(markslag[np.isnan(ut)])
        if saknas.size:
            raise GeometriFel(
                f"Kolumnen {doman}.{kolumn} saknas för markslagskod "
                f"{', '.join(str(int(k)) for k in saknas)}."
            )
        return np.asarray(ut, dtype=np.float32)


def las_markklasser(sokvag: str | Path = STANDARD_MARKKLASSER) -> Markklasstabell:
    fil = Path(sokvag)
    try:
        data = tomllib.loads(fil.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GeometriFel(f"Kunde inte läsa markklasstabellen {fil}: {exc}") from exc
    rå = data.get("markklass")
    if not isinstance(rå, dict):
        raise GeometriFel(f"{fil}: saknar [markklass.<kod>].")
    namn: dict[int, str] = {}
    egenskaper: dict[int, dict[str, dict[str, Any]]] = {}
    for nyckel, post in rå.items():
        kod = int(nyckel)
        if kod not in GILTIGA_KODER:
            raise GeometriFel(f"{fil}: ogiltig markslagskod {kod}.")
        if not isinstance(post, dict) or "namn" not in post:
            raise GeometriFel(f"{fil}: markklass {kod} saknar 'namn'.")
        namn[kod] = str(post["namn"])
        egenskaper[kod] = {}
        for k, v in post.items():
            if k == "namn":
                continue
            if k not in DOMANER or not isinstance(v, dict):
                raise GeometriFel(f"{fil}: okänd tabell '{k}' i markklass {kod}.")
            egenskaper[kod][k] = dict(v)
    saknade = sorted(GILTIGA_KODER - set(namn))
    if saknade:
        raise GeometriFel(f"{fil}: markslagskoder saknas: {saknade}.")
    return Markklasstabell(
        version=int(data.get("version", 1)),
        namn=namn,
        egenskaper=egenskaper,
        sha256=sha256_fil(fil),
    )
