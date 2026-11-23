"""Språkprofiler för skissunderlaget: "sbk" (förval) och "neutral" (tonen i R1).

**sbk** skriver som en utredningstext eller planbeskrivning från stadsbyggnadskontoret: klarspråk,
sakligt, opersonligt och bestämt, korta meningar och slutsatsen först. Rapporten är ett
beräkningsunderlag; texten säger aldrig att förvaltningen har tagit ställning ("bedömer"), bara
vad beräkningen visar, vad som saknas och vad som behöver utredas vidare. Ursprunget (Floden,
körningar, AI-stöd) står i metodavsnittet.

**neutral** är den ursprungliga tonen (R1), oförändrad.

Mallarna här innehåller inga tal ur körningar: de fylls av Floden (`{{matt:…}}` i påståendena,
antal frågor och datum i mallen). Texterna används av `rapport.skissmall` och
`rapport.skissrapport` (bildtexter, metodnotis).
"""

from __future__ import annotations

from datetime import date
from typing import Final

MANADER: Final[tuple[str, ...]] = (
    "januari",
    "februari",
    "mars",
    "april",
    "maj",
    "juni",
    "juli",
    "augusti",
    "september",
    "oktober",
    "november",
    "december",
)

SMALT_HART: Final[str] = " "
HART: Final[str] = " "


def datum_text(iso: str, sbk: bool) -> str:
    """ "2026-11-22…" → "22 november 2026" (sbk) eller "2026-11-22" (neutral)."""
    kort = str(iso)[:10]
    if not sbk:
        return kort
    try:
        d = date.fromisoformat(kort)
    except ValueError:
        return kort
    return f"{d.day} {MANADER[d.month - 1]} {d.year}"


def scenarionamn(scenario_id: str) -> str:
    """Scenariots namn i löptext: "fore" → "före"; andra id med blanksteg i stället för '_'."""
    return {
        "fore": "före",
        "efter": "efter",
        "bas": "nuläge",
        "hojd": "höjt hus",
        "nytt_hus": "nytt hus",
    }.get(
        scenario_id, scenario_id.replace("_", " ")
    )


# Status och trafikljus, skrivna för en planhandläggare (sbk) respektive R1 (neutral).
STATUS_TEXT_SBK: Final[dict[str, str]] = {
    "visat": "Besvaras av beräkningen",
    "avvikelse": "Avvikelse",
    "villkorat": "Kräver ställningstagande",
    "underlag_saknas": "Underlag saknas",
    "ej_tillampligt": "Inte aktuell i skedet",
}
FORDJUPNING_TEXT_SBK: Final[dict[str, str]] = {
    "nej": "Ingen fördjupning i detta skede",
    "ja": "Behöver utredas vidare",
    "kan_inte_avgoras": "Kan inte bedömas med nuvarande underlag",
}
LJUS_TEXT_SBK: Final[dict[str, str]] = {
    "nej": "Ingen fördjupning",
    "ja": "Utreds vidare",
    "kan_inte_avgoras": "Kan inte bedömas",
}

TOMVARDE_SBK: Final[str] = (
    "Vitt: mark där vattendjupet är mindre än den lägsta klassen. Ljusgrått: tak och ytor utan "
    "beräknat värde."
)

SBK_FORBJUDET: Final[tuple[str, ...]] = (
    "stadsbyggnadskontoret bedömer",
    "förvaltningen bedömer",
    "kontoret bedömer",
    "vi bedömer",
    "bedömningen är att",
    "scenario fore",
    "scenario efter",
)
"""Får inte stå i sbk-löptext: förvaltningen tar inte ställning, och inga variabelnamn."""

ENGELSKA_ORD: Final[tuple[str, ...]] = (
    "scenario",
    "status:",
    "output",
    "input",
    "default",
    "max_djup",
    "fore",
    "run",
    "check",
    "export",
    "layout",
)
"""Engelska lån och variabelnamn som språktestet letar efter i sbk-löptexten (hela ord)."""


__all__ = [
    "ENGELSKA_ORD",
    "FORDJUPNING_TEXT_SBK",
    "HART",
    "LJUS_TEXT_SBK",
    "MANADER",
    "SBK_FORBJUDET",
    "SMALT_HART",
    "STATUS_TEXT_SBK",
    "TOMVARDE_SBK",
    "datum_text",
    "scenarionamn",
]
