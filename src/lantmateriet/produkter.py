"""Register över Lantmäteriets produkter som Floden använder (`data/lantmateriet/produkter.toml`).

Används av sidan Nycklar (tabellen "Produkter att beställa på Geotorget") och av felmeddelandena
när Lantmäteriet svarar 401/403 för en viss produkt. Registret har bara avgiftsfria produkter och
inga med personuppgiftsnära innehåll (docs/BESLUT.md 2026-11-24, AGENTS.md).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

REGISTER_FIL = Path(__file__).resolve().parents[2] / "data" / "lantmateriet" / "produkter.toml"

BESTALLNING_TID_TEXT = (
    "En beställning på Geotorget kan ta tid, ibland dagar, innan produkten är aktiv. Tills dess "
    "svarar Lantmäteriet 401 eller 403, precis som när nyckeln eller inloggningssättet är fel."
)
GEOTORGET_URL = "https://www.geotorget.lantmateriet.se"


class ProduktRegisterFel(Exception):
    """Registret saknas eller har fel format."""


@dataclass(frozen=True)
class Produkt:
    roll: str
    namn: str
    stac: str
    samling: str
    anvands_till: str
    behovs: bool
    anmarkning: str
    avgiftsfri: bool

    def till_dict(self) -> dict[str, Any]:
        return {
            "roll": self.roll,
            "namn": self.namn,
            "stac": self.stac,
            "anvands_till": self.anvands_till,
            "behovs": self.behovs,
            "anmarkning": self.anmarkning,
        }


@cache
def _las(sokvag: str) -> tuple[Produkt, ...]:
    try:
        data = tomllib.loads(Path(sokvag).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProduktRegisterFel(f"Produktregistret {sokvag} går inte att läsa: {exc}") from exc
    rader = data.get("produkt")
    if not isinstance(rader, list) or not rader:
        raise ProduktRegisterFel(f"{sokvag} har inga [[produkt]].")
    ut: list[Produkt] = []
    for r in rader:
        try:
            p = Produkt(
                roll=str(r["roll"]),
                namn=str(r["namn"]),
                stac=str(r["stac"]),
                samling=str(r["samling"]),
                anvands_till=str(r["anvands_till"]),
                behovs=bool(r["behovs"]),
                anmarkning=str(r.get("anmarkning", "")),
                avgiftsfri=bool(r["avgiftsfri"]),
            )
        except (KeyError, TypeError) as exc:
            raise ProduktRegisterFel(f"En produkt i {sokvag} saknar fält: {exc}") from exc
        if not p.avgiftsfri:
            raise ProduktRegisterFel(
                f"Produkten '{p.namn}' är inte avgiftsfri; avgiftsbelagda produkter används inte."
            )
        ut.append(p)
    return tuple(ut)


def produkter(sokvag: Path | None = None) -> tuple[Produkt, ...]:
    return _las(str(sokvag or REGISTER_FIL))


def produkt_for_roll(roll: str) -> Produkt | None:
    return next((p for p in produkter() if p.roll == roll), None)


def tabell_for_api() -> list[dict[str, Any]]:
    """Raderna i tabellen Produkt | Används till | Behövs/Valfri på sidan Nycklar."""
    return [p.till_dict() for p in produkter()]


def produkt_fel_text(
    roll: str, status: int, *, samling: str | None = None, metod: str | None = None
) -> str:
    """Meddelandet när Lantmäteriet sa nej (401/403) för en viss produkt. Ingen nyckel i texten."""
    p = produkt_for_roll(roll)
    namn = p.namn if p else roll
    delar = [
        f"Lantmäteriet sa nej ({status}) till produkten {namn}"
        + (f" (samling '{samling}')" if samling else "")
        + (f", som Floden använder till: {p.anvands_till}" if p else "")
        + ".",
        f"Antingen saknas produkten för kontot eller är inte aktiv ännu: beställ den på Geotorget "
        f"({GEOTORGET_URL}). En beställning kan ta tid, ibland dagar, innan den är aktiv.",
        "Annars stämmer inte uppgifterna eller inloggningssättet på sidan Nycklar"
        + (
            ": nu är API-portalens nyckel och hemlighet valda, så välj Geotorgets användarnamn och "
            "lösenord (privatperson) om det är dem du har."
            if metod == "oauth2"
            else "."
        ),
        "Lantmäteriet svarar likadant i alla fallen, så Floden kan inte se vilket.",
    ]
    return " ".join(delar)


def alla_produkter_text() -> str:
    return ", ".join(p.namn for p in produkter())
