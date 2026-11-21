"""Flodens moduler för webben (`GET /api/moduler`).

Webben listar modulerna härifrån och ritar inget eget om dem. En modul räknas som i drift om
den står som det här, eller om den har registrerat minst en analystyp i kärnans frågelager
(`karna.fraga.registrera_analystyp`). Så kan luft, buller och dagsljus slås på utan att webben
ändras.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from karna.fraga import analystyper


@dataclass(frozen=True)
class ModulInfo:
    modul_id: str
    namn: str
    beskrivning: str
    i_drift: bool
    kartfarg: str
    """Modulens färg i kartdata (tema.css). Gränssnittet i övrigt är svartvitt."""


MODULER: tuple[ModulInfo, ...] = (
    ModulInfo(
        "vatten", "Vatten", "Skyfall på gatunivå: djup, tid och flödesvägar.", True, "#2b5d8c"
    ),
    ModulInfo("luft", "Luft", "Halter längs gator och vid entréer.", False, "#6b6b6b"),
    ModulInfo("buller", "Buller", "Ljudnivåer vid fasad och på gård.", False, "#b4462b"),
    ModulInfo("dagsljus", "Dagsljus", "Skuggor och dagsljus i rum och på mark.", False, "#bdbdbd"),
)


def lista_moduler() -> dict[str, Any]:
    """Svar för `GET /api/moduler`."""
    typer = analystyper()
    poster: list[dict[str, Any]] = []
    for nr, m in enumerate(MODULER, start=1):
        modulens_typer = sorted(t for t, modul in typer.items() if modul == m.modul_id)
        i_drift = m.i_drift or bool(modulens_typer)
        poster.append(
            {
                "modul_id": m.modul_id,
                "nr": f"{nr:02d}",
                "namn": m.namn,
                "beskrivning": m.beskrivning,
                "status": "i_drift" if i_drift else "kommande",
                "analystyper": modulens_typer,
                "kartfarg": m.kartfarg,
            }
        )
    return {"moduler": poster}
