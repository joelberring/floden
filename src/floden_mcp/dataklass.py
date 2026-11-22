"""Dataklasser på verktygssvar och en enkel policyhook (R0; riktig datapolicy kommer i R4).

Varje verktygssvar märks med en klass:

| Klass | Betydelse |
| --- | --- |
| `oppet` | Öppna eller syntetiska data (hela demon). |
| `internt` | Stadens interna data som inte är skyddade. |
| `skyddat` | Skyddsklassade data (t.ex. ledningsnät). Lämnas aldrig till en extern AI-tjänst. |
| `personuppgifter` | Kan innehålla personuppgifter. Används inte i Floden (`AGENTS.md`). |

Policyn bestämmer vilka klasser en klient får. **Standard: bara `oppet`.** Klassen för ett anrop är
den högsta av verktygets klass och projektets klass (`[mcp.projektklasser]`; demokvarteret är
`oppet`, okända projekt räknas som `internt` tills någon klassat dem). En nekad klass ger ett
tydligt besked till klienten och en rad i körloggen med utfall `nekad_policy`; inget körs.

Konfiguration (`config.toml` eller `config.local.toml`):

```toml
[mcp]
tillatna_klasser = ["oppet"]            # standard för alla klienter

[mcp.projektklasser]
demokvarteret = "oppet"

[mcp.klienter."claude-desktop"]         # klientens clientInfo.name, gemener
tillatna_klasser = ["oppet", "internt"]
```

Miljövariabeln `FLODEN_MCP_TILLATNA_KLASSER` (kommaseparerad) ersätter standardlistan.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DataKlass(StrEnum):
    OPPET = "oppet"
    INTERNT = "internt"
    SKYDDAT = "skyddat"
    PERSONUPPGIFTER = "personuppgifter"


_ORDNING = {k: i for i, k in enumerate(DataKlass)}

STANDARD_TILLATNA: frozenset[DataKlass] = frozenset({DataKlass.OPPET})
STANDARD_PROJEKTKLASSER: dict[str, DataKlass] = {"demokvarteret": DataKlass.OPPET}
OKAND_PROJEKTKLASS = DataKlass.INTERNT
"""Ett projekt som ingen klassat räknas som internt (försiktigt), inte som öppet."""


class PolicyFel(Exception):
    """Ogiltig policykonfiguration."""


class PolicyNekad(Exception):
    """Klienten får inte ta del av klassen. Meddelandet är skrivet för klienten (och AI:n)."""

    def __init__(self, klass: DataKlass, klient: str, tillatna: Iterable[DataKlass]) -> None:
        self.klass = klass
        self.klient = klient
        lista = ", ".join(sorted(k.value for k in tillatna)) or "ingen"
        super().__init__(
            f"Nekad av datapolicyn: svaret skulle ha dataklassen '{klass.value}', men klienten "
            f"'{klient}' får bara ta del av: {lista}. Operatören kan ändra detta i [mcp] i "
            "konfigurationen. Inget har körts."
        )


def tolka_klass(varde: object, vad: str = "dataklass") -> DataKlass:
    try:
        return DataKlass(str(varde).strip().lower())
    except ValueError as exc:
        giltiga = ", ".join(k.value for k in DataKlass)
        raise PolicyFel(f"Okänd {vad} {varde!r}. Giltiga: {giltiga}.") from exc


def hogsta(*klasser: DataKlass) -> DataKlass:
    """Den mest känsliga av klasserna (öppet < internt < skyddat < personuppgifter)."""
    return max(klasser, key=lambda k: _ORDNING[k])


def _klasslista(varde: object, vad: str) -> frozenset[DataKlass]:
    if isinstance(varde, str):
        delar: list[Any] = [d for d in varde.split(",") if d.strip()]
    elif isinstance(varde, list | tuple | set | frozenset):
        delar = list(varde)
    else:
        raise PolicyFel(f"{vad} måste vara en lista med dataklasser.")
    return frozenset(tolka_klass(d, vad) for d in delar)


@dataclass(frozen=True)
class Policy:
    """Vilka dataklasser en klient får ta del av via MCP."""

    standard: frozenset[DataKlass] = STANDARD_TILLATNA
    per_klient: Mapping[str, frozenset[DataKlass]] = field(default_factory=dict)
    projektklasser: Mapping[str, DataKlass] = field(
        default_factory=lambda: dict(STANDARD_PROJEKTKLASSER)
    )

    @classmethod
    def fran_konfig(
        cls, konfig: Mapping[str, Any] | None, miljo: Mapping[str, str] | None = None
    ) -> Policy:
        """Bygger policyn ur avsnittet `[mcp]` (eller `None`) och miljön."""
        mcp: Mapping[str, Any] = (konfig or {}).get("mcp", {}) if konfig is not None else {}
        miljo = os.environ if miljo is None else miljo
        standard = (
            _klasslista(mcp["tillatna_klasser"], "tillatna_klasser")
            if "tillatna_klasser" in mcp
            else STANDARD_TILLATNA
        )
        if miljo.get("FLODEN_MCP_TILLATNA_KLASSER"):
            standard = _klasslista(
                miljo["FLODEN_MCP_TILLATNA_KLASSER"], "FLODEN_MCP_TILLATNA_KLASSER"
            )
        per_klient = {
            str(namn).lower(): _klasslista(v.get("tillatna_klasser", []), f"klienter.{namn}")
            for namn, v in dict(mcp.get("klienter", {})).items()
            if isinstance(v, Mapping)
        }
        projekt = dict(STANDARD_PROJEKTKLASSER)
        for pid, k in dict(mcp.get("projektklasser", {})).items():
            projekt[str(pid)] = tolka_klass(k, f"projektklasser.{pid}")
        return cls(standard=standard, per_klient=per_klient, projektklasser=projekt)

    def tillatna_for(self, klient: str) -> frozenset[DataKlass]:
        return self.per_klient.get(klient.strip().lower(), self.standard)

    def projektklass(self, projekt_id: str) -> DataKlass:
        return self.projektklasser.get(projekt_id, OKAND_PROJEKTKLASS)

    def klass_for(self, verktygsklass: DataKlass, projekt_id: str | None) -> DataKlass:
        """Anropets klass: den högsta av verktygets och projektets."""
        if projekt_id is None:
            return verktygsklass
        return hogsta(verktygsklass, self.projektklass(projekt_id))

    def kontrollera(self, klient: str, klass: DataKlass) -> None:
        """Kastar `PolicyNekad` om klienten inte får ta del av klassen."""
        tillatna = self.tillatna_for(klient)
        if klass not in tillatna:
            raise PolicyNekad(klass, klient, tillatna)
