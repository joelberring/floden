"""Klarspråk för fel i beräkningarna: vad som hände och vad användaren kan göra.

Kärnan och lösarna ger tekniska feltexter (`KlassNamn: meddelande`). Webben visar en kort
förklaring på vanlig svenska och lägger den tekniska texten under "Teknisk information".

Sedan K2/v2 är en gård som helt omges av hus inget fel: den är en slutsänka, resultatet visas
med varningen `sankgraf.typer.SLUTSANKA_VARNING`, och kartverktyget informerar före körning
(`SLUTET_INFO`). Felet finns kvar bara när ett instängt område omges av mark utan höjd.
"""

from __future__ import annotations

SLUTET_INFO = "Huset stänger in gården – vattnet blir stående där i beräkningen"
"""Information före körning: ett ritat hus gör ett område helt omgivet av byggnader."""

INSTANGT_TEXT = (
    "Ett instängt markområde gränsar till mark utan höjduppgift, så beräkningen vet inte var "
    "vattnet tar vägen. Kontrollera markmodellen där."
)

_INSTANGT_MARKORER = ("InstangtOmradeFel", "saknar utloppssadel")


def forklara_fel(teknisk_text: str | None) -> str | None:
    """Klarspråksförklaring för en teknisk feltext, eller None om felet inte är känt."""
    if not teknisk_text:
        return None
    if any(m in teknisk_text for m in _INSTANGT_MARKORER):
        return INSTANGT_TEXT
    return None
