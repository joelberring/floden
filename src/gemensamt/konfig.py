"""Inläsning och sammanslagning av `config.toml` och valfri `config.local.toml`."""

from __future__ import annotations

import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any


class KonfigFel(Exception):
    """Fel vid inläsning eller validering av konfiguration."""


def _sla_ihop_dict(bas: dict[str, Any], tillagg: dict[str, Any]) -> dict[str, Any]:
    """Djup sammanslagning av två lexikon där `tillagg` skriver över `bas`."""
    resultat: dict[str, Any] = deepcopy(bas)
    for nyckel, varde in tillagg.items():
        if (
            nyckel in resultat
            and isinstance(resultat[nyckel], dict)
            and isinstance(varde, dict)
        ):
            resultat[nyckel] = _sla_ihop_dict(resultat[nyckel], varde)
        else:
            resultat[nyckel] = deepcopy(varde)
    return resultat


def _las_toml(sokvag: Path) -> dict[str, Any]:
    """Läser en TOML-fil och kastar `KonfigFel` vid fel."""
    try:
        raw = sokvag.read_bytes()
    except OSError as exc:
        raise KonfigFel(f"Kunde inte läsa konfigurationsfilen {sokvag}: {exc}") from exc

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise KonfigFel(f"Ogiltig TOML i konfigurationsfilen {sokvag}: {exc}") from exc

    if not isinstance(data, dict):
        raise KonfigFel(f"Konfigurationsfilen {sokvag} måste representera ett TOML-objekt.")
    return data


def las_konfig(
    bas_fil: str | Path = "config.toml",
    lokal_fil: str | Path | None = "config.local.toml",
) -> dict[str, Any]:
    """Läser `config.toml` och slår ihop med `config.local.toml` om den finns.

    Args:
        bas_fil: Sökväg till standardkonfigurationen (`config.toml`).
        lokal_fil: Sökväg till lokal konfiguration (`config.local.toml`) eller `None`.
            Om filen inte finns ignoreras den tyst, men om den finns och är ogiltig
            kastas `KonfigFel`.

    Returns:
        Sammanslaget konfigurationslexikon.

    Raises:
        KonfigFel: Om `bas_fil` saknas eller någon av filerna innehåller ogiltig TOML.
    """
    bas_sokvag = Path(bas_fil)
    if not bas_sokvag.is_file():
        raise KonfigFel(f"Standardkonfigurationen saknas: {bas_sokvag}")

    konfig = _las_toml(bas_sokvag)

    if lokal_fil is not None:
        lokal_sokvag = Path(lokal_fil)
        if lokal_sokvag.exists():
            if not lokal_sokvag.is_file():
                raise KonfigFel(f"Lokal konfiguration är inte en fil: {lokal_sokvag}")
            lokal_data = _las_toml(lokal_sokvag)
            konfig = _sla_ihop_dict(konfig, lokal_data)

    return konfig
