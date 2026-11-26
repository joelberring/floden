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

SANKGRAF_CELLTAK_TEXT = (
    "Området omfattar fler markceller än serverns maxgräns för sänkgrafen "
    "(FLODEN_MAX_SANKGRAF_CELLER). Välj en mindre avgränsning eller kör på en maskin med mer RAM."
)

MINNE_TEXT = (
    "Beräkningen krävde mer arbetsminne än serverns gräns och stoppades för att hålla servern "
    "stabil. Välj en mindre avgränsning, kör ett ämne i taget eller kör på en maskin med mer RAM."
)

TIMEOUT_TEXT = (
    "Beräkningen överskred serverns tidsgräns och avbröts automatiskt. Prova en mindre "
    "avgränsning eller färre scenarier åt gången."
)

_INSTANGT_MARKORER = ("InstangtOmradeFel", "saknar utloppssadel")
_SANKGRAF_CELLTAK_MARKORER = ("FLODEN_MAX_SANKGRAF_CELLER",)
_MINNE_MARKORER = (
    "SubprocMinneFel",
    "MemoryError",
    "mer minne än serverns gräns",
)
_TIMEOUT_MARKORER = (
    "SubprocTimeoutFel",
    "serverns tidsgräns",
)


def forklara_fel(teknisk_text: str | None) -> str | None:
    """Klarspråksförklaring för en teknisk feltext, eller None om felet inte är känt."""
    if not teknisk_text:
        return None
    if any(m in teknisk_text for m in _INSTANGT_MARKORER):
        return INSTANGT_TEXT
    if any(m in teknisk_text for m in _SANKGRAF_CELLTAK_MARKORER):
        return SANKGRAF_CELLTAK_TEXT
    if any(m in teknisk_text for m in _MINNE_MARKORER):
        return MINNE_TEXT
    if any(m in teknisk_text for m in _TIMEOUT_MARKORER):
        return TIMEOUT_TEXT
    return None
