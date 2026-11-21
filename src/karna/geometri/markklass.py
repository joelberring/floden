"""Översättning av K1 markslag till egenskaper per domän (vatten, luft, buller, dagsljus).

Tabellen är en versionsbar TOML-fil (`data/markklasser.toml`) och den enda källan för alla domäner.
Varje markslagskod har ett namn och en tabell per domän; domänerna fyller i sina egna kolumner
(dagsljus är tom tills dess agent lägger till kolumner). Filens sha256 (`Markklasstabell.sha256`)
ska in i recepten för resultat som använder den.

Utöver koderna har filen domäntabeller på toppnivå (`[buller]`, `[luft]`) med metadata (version,
`preliminar`, källa) och för buller Nord2000:s klasstabeller (impedansklass A–H, råhetsklass). Per
markslagskod anger buller klassbokstäver; `las_markklasser` härleder de numeriska kolumnerna
`buller.flodesresistivitet_kNsm4` och `buller.rahet_m` ur klasstabellerna, så att en siffra bara
står
på ett ställe. Kod 0 (okänt) har inga värden för buller och luft (flaggas, gissas inte).
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    # Domäntabeller på toppnivå ([buller], [luft]): metadata och klasstabeller, domän -> tabell.
    meta: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def preliminar(self, doman: str) -> bool:
        """Sant om domänens värden är märkta preliminära (saknad markering räknas som
        preliminär)."""
        return bool(self.meta.get(doman, {}).get("preliminar", True))

    def domanversion(self, doman: str) -> str | None:
        """Domänens egen versionsmärkning (t.ex. `markklass/v1` för buller), om den har någon."""
        v = self.meta.get(doman, {}).get("version")
        return None if v is None else str(v)

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
    meta: dict[str, dict[str, Any]] = {}
    for doman in DOMANER:
        tabell = data.get(doman)
        if tabell is not None:
            if not isinstance(tabell, dict):
                raise GeometriFel(f"{fil}: [{doman}] ska vara en tabell.")
            meta[doman] = dict(tabell)
    if "buller" in meta:
        _harled_buller(fil, meta["buller"], egenskaper)
    return Markklasstabell(
        version=int(data.get("version", 1)),
        namn=namn,
        egenskaper=egenskaper,
        sha256=sha256_fil(fil),
        meta=meta,
    )


def _klasstabell(
    fil: Path, buller: Mapping[str, Any], nyckel: str, *, tillat_noll: bool
) -> dict[str, float]:
    tabell = buller.get(nyckel)
    if not isinstance(tabell, dict) or not tabell:
        raise GeometriFel(f"{fil}: avsnittet [buller.{nyckel}] saknas eller är tomt.")
    ut: dict[str, float] = {}
    for bokstav, v in tabell.items():
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise GeometriFel(f"{fil}: [buller.{nyckel}] {bokstav} saknar talvärde (fick {v!r}).")
        tal = float(v)
        if not math.isfinite(tal) or tal < 0.0 or (tal == 0.0 and not tillat_noll):
            raise GeometriFel(f"{fil}: [buller.{nyckel}] {bokstav} har ogiltigt värde {tal}.")
        ut[str(bokstav)] = tal
    return ut


def _harled_buller(
    fil: Path, buller: dict[str, Any], egenskaper: dict[int, dict[str, dict[str, Any]]]
) -> None:
    """Validerar buller-klasstabellerna och lägger de härledda numeriska kolumnerna på varje kod."""
    for fält in ("version", "preliminar"):
        if fält not in buller:
            raise GeometriFel(f"{fil}: [buller] saknar '{fält}'.")
    if not isinstance(buller["preliminar"], bool):
        raise GeometriFel(f"{fil}: [buller] preliminar ska vara true eller false.")
    kalla = buller.get("kalla")
    if not isinstance(kalla, dict) or not kalla.get("dokument"):
        raise GeometriFel(f"{fil}: källa ([buller.kalla] dokument) saknas.")
    impedans = _klasstabell(fil, buller, "impedansklass", tillat_noll=False)
    rahet = _klasstabell(fil, buller, "rahetsklass", tillat_noll=True)
    yta = buller.get("byggnadsyta")
    if not isinstance(yta, dict):
        raise GeometriFel(f"{fil}: [buller.byggnadsyta] saknas.")
    poster: list[tuple[str, dict[str, Any]]] = [("[buller.byggnadsyta]", yta)]
    poster += [
        (f"[markklass.{k}.buller]", e["buller"]) for k, e in egenskaper.items() if e.get("buller")
    ]
    for vad, post in poster:
        imp, rah = post.get("impedansklass"), post.get("rahetsklass")
        if not isinstance(imp, str) or imp not in impedans:
            raise GeometriFel(f"{fil}: {vad} har okänd eller tom impedansklass ({imp!r}).")
        if not isinstance(rah, str) or rah not in rahet:
            raise GeometriFel(f"{fil}: {vad} har okänd eller tom råhetsklass ({rah!r}).")
        if post is not yta:
            post["flodesresistivitet_kNsm4"] = impedans[imp]
            post["rahet_m"] = rahet[rah]
