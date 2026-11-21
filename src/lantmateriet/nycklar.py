"""Nycklar till Lantmäteriets API: bara ur miljön eller `config.local.toml`, aldrig i repot.

Protokoll och logik porterade från O-Tid (MIT, joelberring/otid @ 4a446af):
`packages/application/src/map-project-lantmateriet.ts` och `map-source-credentials.ts`.

Två metoder, som i O-Tid:

- `oauth2` (förval): API-portalens nyckel och hemlighet (`apimanager.lantmateriet.se`), OAuth2
  client credentials. Organisationens systemkonto.
- `basic`: Geotorgets användarnamn och lösenord (privatperson), skickas som HTTP Basic.

Miljövariabler (vinner över filen): `LANTMATERIET_KLIENT_ID`, `LANTMATERIET_KLIENT_HEMLIGHET`,
`LANTMATERIET_METOD`. Fil: `[lantmateriet]` med `klient_id`, `klient_hemlighet`, `metod` i
`config.local.toml` (ignorerad av git). Nycklarna skrivs aldrig i loggar, manifest eller
felmeddelanden: `Nycklar` har en `repr` som döljer dem.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemensamt.konfig import KonfigFel, las_konfig

METODER = ("oauth2", "basic")
_REPO_ROT = Path(__file__).resolve().parents[2]


class NyckelFel(Exception):
    """Nycklar saknas eller Lantmäteriet godtog dem inte (401/403)."""


@dataclass(frozen=True)
class Nycklar:
    """Inloggningsuppgifter till Lantmäteriet; visas aldrig i text (`Nycklar(metod=...)`)."""

    id: str = field(repr=False)
    hemlighet: str = field(repr=False)
    metod: str = "oauth2"

    def __post_init__(self) -> None:
        if self.metod not in METODER:
            raise NyckelFel(f"Okänd metod '{self.metod}', välj {' eller '.join(METODER)}.")
        if not self.id.strip() or not self.hemlighet.strip():
            raise NyckelFel("Nyckel och hemlighet får inte vara tomma.")

    def __repr__(self) -> str:
        return f"Nycklar(metod={self.metod!r}, id=<dold>, hemlighet=<dold>)"

    __str__ = __repr__

    def basic_rubrik(self) -> str:
        """Värdet till `Authorization: Basic ...` (används för token-anropet eller direkt)."""
        rå = f"{self.id}:{self.hemlighet}".encode()
        return "Basic " + base64.b64encode(rå).decode("ascii")

    def hemligheter(self) -> tuple[str, ...]:
        """Strängar som aldrig får förekomma i loggar eller filer (till tester och rensning)."""
        return (self.id, self.hemlighet, self.basic_rubrik().removeprefix("Basic "))


def _lokal_konfig() -> dict[str, Any]:
    """`config.toml` + `config.local.toml` från arbetsmappen, annars från repots rot."""
    for rot in (Path.cwd(), _REPO_ROT):
        if (rot / "config.local.toml").is_file() or (rot / "config.toml").is_file():
            try:
                return las_konfig(rot / "config.toml", rot / "config.local.toml")
            except KonfigFel as exc:
                raise NyckelFel(str(exc)) from exc
    return {}


def las_nycklar(
    konfig: Mapping[str, Any] | None = None,
    miljo: Mapping[str, str] | None = None,
) -> Nycklar:
    """Nycklar ur miljön, annars `[lantmateriet]` i konfigurationen (saknas de: `NyckelFel`)."""
    env = os.environ if miljo is None else miljo
    avsnitt: Mapping[str, Any] = {}
    kfg = _lokal_konfig() if konfig is None else konfig
    rå = kfg.get("lantmateriet", {})
    if isinstance(rå, Mapping):
        avsnitt = rå
    klient_id = env.get("LANTMATERIET_KLIENT_ID") or str(avsnitt.get("klient_id", "") or "")
    hemlighet = env.get("LANTMATERIET_KLIENT_HEMLIGHET") or str(
        avsnitt.get("klient_hemlighet", "") or ""
    )
    metod = (env.get("LANTMATERIET_METOD") or str(avsnitt.get("metod", "") or "oauth2")).lower()
    if not klient_id.strip() or not hemlighet.strip():
        raise NyckelFel(
            "Nycklar till Lantmäteriet saknas. Lägg dem i miljön "
            "(LANTMATERIET_KLIENT_ID, LANTMATERIET_KLIENT_HEMLIGHET) eller under [lantmateriet] "
            "(klient_id, klient_hemlighet) i config.local.toml. Se docs/DATA.md, avsnittet "
            "'Hämta öppna data från Lantmäteriet'."
        )
    return Nycklar(id=klient_id.strip(), hemlighet=hemlighet.strip(), metod=metod)
