"""Flodens moduler för webben (`GET /api/moduler`): en enda källa till vad som finns och inte.

Statusen sätts på ett ställe och visas likadant överallt i gränssnittet:

- **Vad som finns** läses ur kärnan: en modul (domän) finns när minst en lösare (K21, upptäckt via
  `<paket>/floden_losare.py`, se `karna.analyslosare.lista_losare`) eller en analystyp i
  frågelagret (K10) hör till den.
- **Vad som är förkört i demon** läses ur demons översikt (`demo.json`, skriven av `floden demo`).
- **Det som inte går att läsa ur kärnan** (luftens körtid, bullrets metodkontroll) står i
  `data/webb/modulstatus.toml` med källa; ett test jämför siffrorna med källan.

Domäner som kärnan känner till men som inte är produktmoduler (t.ex. `geometri`) är verktyg, inte
moduler, och listas inte. Det enda som står i webben är presentationen (namn, beskrivning,
kartfärg, vardagsnamn på lösarna).
"""

from __future__ import annotations

import tomllib
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from karna.analyslosare import Analyslosare, lista_losare
from karna.fraga import analystyper

_MODULSTATUS = Path(__file__).resolve().parents[2] / "data" / "webb" / "modulstatus.toml"


@dataclass(frozen=True)
class ModulInfo:
    """Presentation av en domän. Status och lösare kommer från kärnan, inte härifrån."""

    modul_id: str
    namn: str
    beskrivning: str
    kartfarg: str
    """Modulens färg i kartdata (tema.css). Gränssnittet i övrigt är svartvitt."""


MODULER: tuple[ModulInfo, ...] = (
    ModulInfo(
        "vatten", "Vatten", "Skyfall på gatunivå: var vattnet samlas och hur djupt.", "#2b5d8c"
    ),
    ModulInfo("luft", "Luft", "Vind och luftkvalitet längs gator och på gårdar.", "#6b6b6b"),
    ModulInfo("buller", "Buller", "Ljudnivåer vid fasad och på gård.", "#b4462b"),
    ModulInfo("dagsljus", "Dagsljus", "Soltimmar på mark och dagsljus på fasader.", "#bdbdbd"),
)

LOSARNAMN: dict[str, str] = {
    "dagsljus.soltimmar_mark": "Soltimmar på mark",
    "dagsljus.vsc_fasad": "Dagsljus på fasad",
    "vatten.statisk_fyllning": "Maxdjup vid skyfall",
    "vatten.dynamik": "Skyfall i tid (dynamisk)",
    "vatten.2d": "Skyfall 2D (lokal tröghet)",
    "luft.vindkomfort": "Vindkomfort",
    "ekologi.konnektivitet": "Ekologisk konnektivitet",
}


def visningsnamn(losarnamn: str) -> str:
    """Vardagsnamn på en lösare; okända namn görs läsbara (`a.b_c` blir `B c`)."""
    if losarnamn in LOSARNAMN:
        return LOSARNAMN[losarnamn]
    sista = losarnamn.split(".")[-1].replace("_", " ").strip()
    return sista[:1].upper() + sista[1:]


def las_modulstatus(sokvag: Path = _MODULSTATUS) -> dict[str, dict[str, Any]]:
    """Läser `data/webb/modulstatus.toml` (tom om filen saknas)."""
    if not sokvag.is_file():
        return {}
    with sokvag.open("rb") as f:
        return {k: dict(v) for k, v in tomllib.load(f).items() if isinstance(v, dict)}


def losarinfo(los: Analyslosare) -> dict[str, Any]:
    """Läsbar beskrivning av en lösare (samma uppgifter som `floden fraga losare`)."""
    radie: float | None
    radie_kalla = "standardparametrar"
    try:
        radie = los.paverkansradie_m(los.validera_parametrar({}))
    except Exception:  # noqa: BLE001 - en lösare som kräver parametrar får inte stoppa modullistan
        radie = None
        radie_kalla = "kraver_parametrar"
    return {
        "namn": los.namn,
        "visningsnamn": visningsnamn(los.namn),
        "version": los.version,
        "doman": los.doman,
        "beskrivning": los.beskrivning,
        "indataroller": list(los.indataroller),
        "valfria_indataroller": list(los.valfria_indataroller),
        "utdataroller": list(los.utdataroller),
        "radie_m": radie,
        "radie_kalla": radie_kalla,
    }


def _status(
    modul_id: str, finns: bool, demo_moduler: Collection[str] | None, meta: dict[str, Any]
) -> tuple[str, str]:
    """(status, text) för en modul. `demo_moduler` är domänerna som är förkörda i demon."""
    if meta.get("status") == "under_utveckling":
        godkanda, totalt = meta.get("testfall_godkanda"), meta.get("testfall_totalt")
        if isinstance(godkanda, int) and isinstance(totalt, int):
            return (
                "under_utveckling",
                f"Under utveckling – metodkontroll pågår ({godkanda} av {totalt} officiella "
                "testfall godkända)",
            )
        return "under_utveckling", "Under utveckling"
    if not finns:
        return "under_utveckling", "Under utveckling"
    if demo_moduler is not None and modul_id in demo_moduler:
        return "finns_i_demon", "Finns i demon"
    if meta.get("status") == "finns_ej_forberaknad":
        return (
            "finns_ej_forberaknad",
            "Finns, men en beräkning tar mer än tio minuter – inte förberäknad i demon",
        )
    return "finns", "Finns, men är inte förberäknad i demon"


def lista_moduler(demo_moduler: Collection[str] | None = None) -> dict[str, Any]:
    """Svar för `GET /api/moduler`. `demo_moduler`: domäner som är förkörda i demon."""
    typer = analystyper()
    losare = lista_losare()
    meta = las_modulstatus()
    poster: list[dict[str, Any]] = []
    for nr, m in enumerate(MODULER, start=1):
        modulens_typer = sorted(t for t, modul in typer.items() if modul == m.modul_id)
        modulens_losare = [losarinfo(los) for los in losare if los.doman == m.modul_id]
        finns = bool(modulens_typer) or bool(modulens_losare)
        status, text = _status(m.modul_id, finns, demo_moduler, meta.get(m.modul_id, {}))
        poster.append(
            {
                "modul_id": m.modul_id,
                "nr": f"{nr:02d}",
                "namn": m.namn,
                "beskrivning": m.beskrivning,
                "status": status,
                "status_text": text,
                "finns_i_karnan": finns,
                "analystyper": modulens_typer,
                "losare": modulens_losare,
                "kartfarg": m.kartfarg,
            }
        )
    return {"moduler": poster}
