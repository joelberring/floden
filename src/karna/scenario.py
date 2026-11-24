"""Planscenario som bas + diff (kontrakt K9/v1).

Ett scenario är en bas (sha256 av markmodellens recept, en eller flera rutor) plus en lista
ändringar. Ett alternativ kan ärva från ett annat (`forald`), så Alt B = Alt A + magasin lagras
som en liten diff. Scenariots nyckel är sha256 av dess innehåll inklusive förälderns nyckel,
som en commit.

Ändringstyperna är gemensamma för alla moduler (vatten, luft, buller, dagsljus): de beskriver
geometri, inte en analys. K3-lappen (vatten) kan läsas in med `fran_k3`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from karna.hashning import kanonisk_json, nyckel_for

KONTRAKT_K9 = "K9/v1"

ANDRINGSTYPER: frozenset[str] = frozenset(
    {
        "byggnad_ny",  # polygon + hojd_m (takhöjd över mark) eller tak_rh2000
        "byggnad_bort",  # byggnads_id i basen
        "byggnad_hojd",  # byggnads_id + ny hojd_m
        "markhojd",  # polygon + niva_rh2000, lutning eller delta_m (+ valfri slant, R4)
        "markslag",  # polygon + kod (K1-markslag)
        "gata",  # linje + sektion
        "magasin",  # polygon + volym_m3
        "vegetation",  # polygon + hojd_m (för luft/buller/dagsljus)
        "brytlinje_ny",  # linje + typ + hojd_m: mur, kantsten ... (K1 brytlinje, R3)
        "brytlinje_bort",  # linje (väljare), valfria tolerans_m, typ: tar bort brytlinjer
        "oppning_ny",  # linje + typ + bredd_m + botten_rh2000: trumma, port, passage (R3)
    }
)


class ScenarioFel(ValueError):
    """Ogiltigt planscenario."""


@dataclass(frozen=True)
class Andring:
    """En ändring mot basen: typ, stabilt id, geometri (EPSG:3011) och egenskaper."""

    typ: str
    andring_id: str
    geometri: Mapping[str, Any] | None = None
    """GeoJSON-geometri i EPSG:3011 (None för t.ex. `byggnad_bort`)."""
    egenskaper: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.typ not in ANDRINGSTYPER:
            raise ScenarioFel(
                f"Okänd ändringstyp '{self.typ}'. Tillåtna: {', '.join(sorted(ANDRINGSTYPER))}."
            )
        if not self.andring_id.strip():
            raise ScenarioFel("Ändringen måste ha ett andring_id.")
        if self.typ in ("byggnad_bort", "byggnad_hojd") and "byggnads_id" not in self.egenskaper:
            raise ScenarioFel(f"{self.typ} '{self.andring_id}' saknar byggnads_id.")
        if self.typ not in ("byggnad_bort", "byggnad_hojd") and self.geometri is None:
            raise ScenarioFel(f"{self.typ} '{self.andring_id}' saknar geometri.")
        # Validerar att innehållet är hashbart redan här (inga tysta fel senare).
        kanonisk_json(self.till_dict())

    def till_dict(self) -> dict[str, Any]:
        return {
            "typ": self.typ,
            "andring_id": self.andring_id,
            "geometri": dict(self.geometri) if self.geometri is not None else None,
            "egenskaper": dict(self.egenskaper),
        }

    @staticmethod
    def fran_dict(d: Mapping[str, Any]) -> Andring:
        return Andring(
            typ=str(d["typ"]),
            andring_id=str(d["andring_id"]),
            geometri=d.get("geometri"),
            egenskaper=dict(d.get("egenskaper") or {}),
        )


@dataclass(frozen=True)
class Planscenario:
    """Bas + diff. `forald` = nyckeln till scenariot detta bygger på (None = direkt på basen)."""

    scenario_id: str
    bas_recept: tuple[str, ...]
    andringar: tuple[Andring, ...] = ()
    forald: str | None = None
    beskrivning: str = ""

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ScenarioFel("scenario_id saknas.")
        bas = tuple(sorted(str(b) for b in self.bas_recept))
        if not bas:
            raise ScenarioFel(f"Scenariot '{self.scenario_id}' saknar bas_recept.")
        object.__setattr__(self, "bas_recept", bas)
        andr = tuple(self.andringar)
        ids = [a.andring_id for a in andr]
        if len(ids) != len(set(ids)):
            raise ScenarioFel(f"Scenariot '{self.scenario_id}' har dubbla andring_id.")
        object.__setattr__(self, "andringar", andr)

    @property
    def nyckel(self) -> str:
        """Innehållets nyckel. Beskrivningen ingår inte: den ändrar inget resultat."""
        return nyckel_for(
            {
                "kontrakt": KONTRAKT_K9,
                "bas_recept": list(self.bas_recept),
                "forald": self.forald,
                "andringar": sorted(
                    (a.till_dict() for a in self.andringar), key=lambda d: d["andring_id"]
                ),
            }
        )

    def gren(
        self, scenario_id: str, andringar: Sequence[Andring], beskrivning: str = ""
    ) -> Planscenario:
        """Ett nytt alternativ som ärver allt i detta och lägger till `andringar`."""
        return Planscenario(
            scenario_id=scenario_id,
            bas_recept=self.bas_recept,
            andringar=tuple(andringar),
            forald=self.nyckel,
            beskrivning=beskrivning,
        )

    def till_dict(self) -> dict[str, Any]:
        return {
            "kontrakt": KONTRAKT_K9,
            "scenario_id": self.scenario_id,
            "nyckel": self.nyckel,
            "bas_recept": list(self.bas_recept),
            "forald": self.forald,
            "beskrivning": self.beskrivning,
            "andringar": [a.till_dict() for a in self.andringar],
        }


def platta_ut(scenario: Planscenario, kanda: Mapping[str, Planscenario]) -> tuple[Andring, ...]:
    """Alla ändringar från basen till `scenario`, äldst först; senare med samma id vinner."""
    kedja: list[Planscenario] = []
    nu: Planscenario | None = scenario
    sett: set[str] = set()
    while nu is not None:
        if nu.nyckel in sett:
            raise ScenarioFel("Cykel i scenariots förälderkedja.")
        sett.add(nu.nyckel)
        kedja.append(nu)
        forald = nu.forald
        if forald is None:
            nu = None
        elif forald in kanda:
            nu = kanda[forald]
            if nu.bas_recept != scenario.bas_recept:
                raise ScenarioFel("Förälder och barn har olika bas_recept.")
        else:
            raise ScenarioFel(f"Förälder {forald[:12]}… saknas bland kända scenarier.")
    samlat: dict[str, Andring] = {}
    for s in reversed(kedja):
        for a in s.andringar:
            samlat[a.andring_id] = a
    return tuple(samlat.values())


@dataclass(frozen=True)
class ScenarioDiff:
    tillagda: tuple[Andring, ...]
    borttagna: tuple[Andring, ...]
    andrade: tuple[tuple[Andring, Andring], ...]


def diff_scenarier(
    a: Planscenario, b: Planscenario, kanda: Mapping[str, Planscenario] | None = None
) -> ScenarioDiff:
    """Vad som skiljer b från a (båda plattade ut mot basen)."""
    if a.bas_recept != b.bas_recept:
        raise ScenarioFel("Scenarier med olika bas kan inte diffas.")
    k = dict(kanda or {})
    k.setdefault(a.nyckel, a)
    k.setdefault(b.nyckel, b)
    da = {x.andring_id: x for x in platta_ut(a, k)}
    db = {x.andring_id: x for x in platta_ut(b, k)}
    return ScenarioDiff(
        tillagda=tuple(db[i] for i in sorted(db.keys() - da.keys())),
        borttagna=tuple(da[i] for i in sorted(da.keys() - db.keys())),
        andrade=tuple(
            (da[i], db[i])
            for i in sorted(da.keys() & db.keys())
            if kanonisk_json(da[i].till_dict()) != kanonisk_json(db[i].till_dict())
        ),
    )


def skriv_scenario(scenario: Planscenario, sokvag: str | Path) -> Path:
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    mal.write_text(
        json.dumps(scenario.till_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return mal


def las_scenario(sokvag: str | Path) -> Planscenario:
    d = json.loads(Path(sokvag).read_text(encoding="utf-8"))
    if d.get("kontrakt") != KONTRAKT_K9:
        raise ScenarioFel(
            f"Fel kontrakt i '{sokvag}': {d.get('kontrakt')!r}, förväntade {KONTRAKT_K9}."
        )
    s = Planscenario(
        scenario_id=str(d["scenario_id"]),
        bas_recept=tuple(d["bas_recept"]),
        andringar=tuple(Andring.fran_dict(a) for a in d.get("andringar", [])),
        forald=d.get("forald"),
        beskrivning=str(d.get("beskrivning", "")),
    )
    if "nyckel" in d and d["nyckel"] != s.nyckel:
        raise ScenarioFel(f"Nyckeln i '{sokvag}' stämmer inte med innehållet (filen är ändrad).")
    return s
