"""Index över testfallen för rak väg (grupper och fallnummer ur Miljøprojekt 1335/2010)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from buller.typer import BullerFel


@dataclass(frozen=True)
class Indexpost:
    """Ett fallnummer i indexet med det som rapporten säger om det."""

    nr: int
    grupp_nr: int
    gruppbeskrivning: str
    fordonskategori: int
    beskrivning: str  # tom om rapporten inte beskriver fallet


def las_index(fil: str | Path) -> list[Indexpost]:
    """Läser indexfilen och ger en post per fallnummer. Fel i filen ger `BullerFel`."""
    sokvag = Path(fil)
    try:
        data: dict[str, Any] = tomllib.loads(sokvag.read_text(encoding="utf-8"))
        standardkategori = int(data["gemensamt"]["fordonskategori_standard"])
        enskilda = {int(post["nr"]): post for post in data.get("fall", [])}
        poster: list[Indexpost] = []
        for grupp in data["grupp"]:
            for nr in range(int(grupp["forsta_fall"]), int(grupp["sista_fall"]) + 1):
                egen = enskilda.get(nr, {})
                poster.append(
                    Indexpost(
                        nr=nr,
                        grupp_nr=int(grupp["nr"]),
                        gruppbeskrivning=str(grupp["beskrivning"]),
                        fordonskategori=int(egen.get("fordonskategori", standardkategori)),
                        beskrivning=str(egen.get("beskrivning_original", "")),
                    )
                )
        antal = int(data["meta"]["antal_fall"])
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        raise BullerFel(f"Kunde inte läsa indexfilen {sokvag}: {exc}") from exc
    if len(poster) != antal or len({p.nr for p in poster}) != antal:
        raise BullerFel(
            f"{sokvag}: grupperna ger {len(poster)} fall men meta.antal_fall är {antal}."
        )
    return poster
