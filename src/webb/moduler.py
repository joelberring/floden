"""Flodens moduler för webben (`GET /api/moduler`).

Listan speglar vad som faktiskt är registrerat i kärnan, inte en hårdkodad lista över vad som
är i drift:

- en modul (domän) är i drift när minst en lösare (K21, upptäckt via `<paket>/floden_losare.py`,
  se `karna.analyslosare.lista_losare`) eller en analystyp i frågelagret (K10,
  `karna.fraga.analystyper`) hör till den,
- varje modul redovisar sina lösare med version, roller och påverkansradie,
- en domän som kärnan känner till men som saknar presentationstext här (t.ex. `geometri`) listas
  ändå, efter de fyra produktmodulerna.

Det enda som står i webben är presentationen (namn, beskrivning, kartfärg). Så kan luft, buller och
dagsljus slås på utan att webben ändras.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from karna.analyslosare import Analyslosare, lista_losare
from karna.fraga import analystyper


@dataclass(frozen=True)
class ModulInfo:
    """Presentation av en domän. Status och lösare kommer från kärnan, inte härifrån."""

    modul_id: str
    namn: str
    beskrivning: str
    kartfarg: str
    """Modulens färg i kartdata (tema.css). Gränssnittet i övrigt är svartvitt."""


MODULER: tuple[ModulInfo, ...] = (
    ModulInfo("vatten", "Vatten", "Skyfall på gatunivå: djup, tid och flödesvägar.", "#2b5d8c"),
    ModulInfo("luft", "Luft", "Halter längs gator och vid entréer.", "#6b6b6b"),
    ModulInfo("buller", "Buller", "Ljudnivåer vid fasad och på gård.", "#b4462b"),
    ModulInfo("dagsljus", "Dagsljus", "Skuggor och dagsljus i rum och på mark.", "#bdbdbd"),
)

_OKAND_DOMAN_FARG = "#6b6b6b"


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
        "version": los.version,
        "doman": los.doman,
        "beskrivning": los.beskrivning,
        "indataroller": list(los.indataroller),
        "valfria_indataroller": list(los.valfria_indataroller),
        "utdataroller": list(los.utdataroller),
        "radie_m": radie,
        "radie_kalla": radie_kalla,
    }


def lista_moduler() -> dict[str, Any]:
    """Svar för `GET /api/moduler`."""
    typer = analystyper()
    losare = lista_losare()
    kanda = {m.modul_id for m in MODULER}
    extra = sorted(
        ({l.doman for l in losare} | set(typer.values())) - kanda  # noqa: E741
    )
    presentation = list(MODULER) + [
        ModulInfo(d, d.capitalize(), "Verktyg som kärnan registrerat.", _OKAND_DOMAN_FARG)
        for d in extra
    ]
    poster: list[dict[str, Any]] = []
    for nr, m in enumerate(presentation, start=1):
        modulens_typer = sorted(t for t, modul in typer.items() if modul == m.modul_id)
        modulens_losare = [losarinfo(los) for los in losare if los.doman == m.modul_id]
        i_drift = bool(modulens_typer) or bool(modulens_losare)
        poster.append(
            {
                "modul_id": m.modul_id,
                "nr": f"{nr:02d}",
                "namn": m.namn,
                "beskrivning": m.beskrivning,
                "status": "i_drift" if i_drift else "kommande",
                "analystyper": modulens_typer,
                "losare": modulens_losare,
                "kartfarg": m.kartfarg,
            }
        )
    return {"moduler": poster}
