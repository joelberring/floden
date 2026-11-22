"""Klarspråk för fel i beräkningarna: vad som hände och vad användaren kan göra.

Kärnan och lösarna ger tekniska feltexter (`KlassNamn: meddelande`). Webben visar en kort
förklaring på vanlig svenska och lägger den tekniska texten under "Teknisk information".
"""

from __future__ import annotations

INSTANGT_TEXT = (
    "Huset stänger in en lågpunkt så att vattnet inte har någon väg ut i beräkningen. "
    "Flytta huset eller lägg det så att gatan förblir öppen."
)

_INSTANGT_MARKORER = ("InstangtOmradeFel", "saknar utloppssadel")


def forklara_fel(teknisk_text: str | None) -> str | None:
    """Klarspråksförklaring för en teknisk feltext, eller None om felet inte är känt."""
    if not teknisk_text:
        return None
    if any(m in teknisk_text for m in _INSTANGT_MARKORER):
        return INSTANGT_TEXT
    return None
