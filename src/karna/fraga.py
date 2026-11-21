"""Utredningsfråga som deklarativ specifikation (kontrakt K10/v1).

Exempel (TOML; systemdesignen skriver YAML, men TOML läses med Pythons standardbibliotek och
kräver inget nytt beroende):

```toml
fraga = "dagsljus-grannar"
scenario = "ornsberg-2/alt-B"
jamfor_med = "ornsberg-2/alt-A"     # valfritt: svaret blir B minus A
utdata = ["karta", "tabell_per_fastighet", "rapportavsnitt"]

[[analys]]
typ = "vsc_fasad"
urval = "befintliga_bostader"
radie = "auto"

[[analys]]
typ = "soltimmar"
datum = ["2026-03-20", "2026-06-21"]
yta = "gardar"
```

Varje analystyp hör till en modul. Modulerna registrerar sina typer med `registrera_analystyp`;
en fråga med en okänd typ ger fel i stället för att tyst hoppas över.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from karna.graf import Nod
from karna.hashning import kanonisk_json, nyckel_for
from karna.scenario import Planscenario

KONTRAKT_K10 = "K10/v1"
UTDATATYPER: frozenset[str] = frozenset(
    {"karta", "tabell", "tabell_per_fastighet", "rapportavsnitt", "raster"}
)

_ANALYSTYPER: dict[str, str] = {}
_NODBYGGARE: dict[str, Callable[[AnalysSteg, Planscenario], Nod]] = {}


class FragaFel(ValueError):
    """Ogiltig utredningsfråga."""


def registrera_analystyp(
    typ: str,
    modul: str,
    nodbyggare: Callable[[AnalysSteg, Planscenario], Nod] | None = None,
) -> None:
    """En modul (vatten, luft, buller, dagsljus …) anmäler en analystyp den kan svara på.

    `nodbyggare(steg, scenario)` gör analyssteget till en nod i beräkningsgrafen; utan den kan
    frågan valideras men inte köras (`karna.korning.kor_fraga` ger då ett tydligt fel).
    """
    if not typ.strip() or not modul.strip():
        raise FragaFel("Analystyp och modul måste vara icke-tomma.")
    tidigare = _ANALYSTYPER.get(typ)
    if tidigare is not None and tidigare != modul:
        raise FragaFel(f"Analystypen '{typ}' är redan registrerad av modulen '{tidigare}'.")
    _ANALYSTYPER[typ] = modul
    if nodbyggare is not None:
        _NODBYGGARE[typ] = nodbyggare


def nodbyggare_for(typ: str) -> Callable[[AnalysSteg, Planscenario], Nod]:
    if typ not in _NODBYGGARE:
        modul = _ANALYSTYPER.get(typ, "okänd")
        raise FragaFel(f"Analystypen '{typ}' (modul {modul}) kan valideras men inte köras än.")
    return _NODBYGGARE[typ]


def analystyper() -> dict[str, str]:
    return dict(_ANALYSTYPER)


@dataclass(frozen=True)
class AnalysSteg:
    typ: str
    parametrar: Mapping[str, Any] = field(default_factory=dict)

    @property
    def modul(self) -> str:
        return _ANALYSTYPER[self.typ]


@dataclass(frozen=True)
class Utredningsfraga:
    fraga: str
    scenario: str
    analys: tuple[AnalysSteg, ...]
    utdata: tuple[str, ...] = ("karta",)
    jamfor_med: str | None = None

    def __post_init__(self) -> None:
        if not self.fraga.strip():
            raise FragaFel("Frågan saknar namn ('fraga').")
        if not self.scenario.strip():
            raise FragaFel(f"Frågan '{self.fraga}' saknar scenario.")
        if not self.analys:
            raise FragaFel(f"Frågan '{self.fraga}' har ingen analys.")
        okanda = sorted({a.typ for a in self.analys} - _ANALYSTYPER.keys())
        if okanda:
            raise FragaFel(
                f"Frågan '{self.fraga}' har okända analystyper: {', '.join(okanda)}. "
                "Ingen modul har registrerat dem."
            )
        fel_utdata = sorted(set(self.utdata) - UTDATATYPER)
        if fel_utdata:
            raise FragaFel(f"Okänd utdata: {', '.join(fel_utdata)}.")
        if self.jamfor_med is not None and self.jamfor_med == self.scenario:
            raise FragaFel("En fråga kan inte jämföra ett scenario med sig självt.")
        kanonisk_json(self.till_dict())

    @property
    def moduler(self) -> tuple[str, ...]:
        return tuple(sorted({a.modul for a in self.analys}))

    def till_dict(self) -> dict[str, Any]:
        return {
            "kontrakt": KONTRAKT_K10,
            "fraga": self.fraga,
            "scenario": self.scenario,
            "jamfor_med": self.jamfor_med,
            "utdata": sorted(self.utdata),
            "analys": [{"typ": a.typ, **dict(a.parametrar)} for a in self.analys],
        }

    def nyckel(self, scenario_nycklar: Mapping[str, str]) -> str:
        """Frågans nyckel med scenariernas innehållsnycklar i stället för deras namn.

        Samma fråga mot samma scenarier ger samma nyckel, även om scenariot döps om; ändras
        scenariot ändras nyckeln.
        """
        def sla_upp(namn: str) -> str:
            if namn not in scenario_nycklar:
                raise FragaFel(f"Nyckel för scenariot '{namn}' saknas.")
            return scenario_nycklar[namn]

        d = self.till_dict()
        d["scenario"] = sla_upp(self.scenario)
        if self.jamfor_med is not None:
            d["jamfor_med"] = sla_upp(self.jamfor_med)
        del d["fraga"]
        return nyckel_for(d)


def tolka_fraga(data: Mapping[str, Any]) -> Utredningsfraga:
    kanda = {"fraga", "scenario", "jamfor_med", "utdata", "analys", "kontrakt"}
    okanda = sorted(set(data) - kanda)
    if okanda:
        raise FragaFel(f"Okända nycklar i frågan: {', '.join(okanda)}.")
    if "kontrakt" in data and data["kontrakt"] != KONTRAKT_K10:
        raise FragaFel(f"Fel kontrakt {data['kontrakt']!r}, förväntade {KONTRAKT_K10}.")
    rader = data.get("analys")
    if not isinstance(rader, Sequence) or isinstance(rader, str):
        raise FragaFel("'analys' måste vara en lista.")
    steg: list[AnalysSteg] = []
    for i, rad in enumerate(rader):
        if not isinstance(rad, Mapping) or "typ" not in rad:
            raise FragaFel(f"analys[{i}] saknar 'typ'.")
        steg.append(
            AnalysSteg(str(rad["typ"]), {k: v for k, v in rad.items() if k != "typ"})
        )
    utdata = data.get("utdata", ["karta"])
    return Utredningsfraga(
        fraga=str(data.get("fraga", "")),
        scenario=str(data.get("scenario", "")),
        analys=tuple(steg),
        utdata=tuple(str(u) for u in utdata),
        jamfor_med=None if data.get("jamfor_med") is None else str(data["jamfor_med"]),
    )


def las_fraga(sokvag: str | Path) -> Utredningsfraga:
    with Path(sokvag).open("rb") as fh:
        return tolka_fraga(tomllib.load(fh))
