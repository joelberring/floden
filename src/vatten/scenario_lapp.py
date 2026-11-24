"""Planscenario (K9/v1) → projektlapp (K3/v1), och scenariot tillämpat med lokal omräkning.

Själva K9→K3-översättningen finns bara i kärnan (`karna.k9_till_k1.k9_till_lapp`); den här modulen
är ett tunt lager som behåller vattnets publika namn: den väljer ut ändringarna (och vilka typer
en vattenfråga får bortse från), anropar kärnan och ger lappen vattnets id och beskrivning. Typerna
som stöds och deras egenskaper står i `karna.k9_till_k1`.

`byggnad_bort`, `byggnad_hojd` och `vegetation` har ingen motsvarighet i K3 och ger fel
(`markhojd` med `lutning`, `delta_m` och `slant` översätts, R4). Typer som en vattenfråga
uttryckligen kan bortse från (t.ex. `vegetation`, som bara rör luft, buller och dagsljus)
anges i `ignorera_typer`; de listas i resultatet.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace

import numpy as np

from karna.k9_till_k1 import STODDA_TYPER, k9_till_lapp
from karna.scenario import Planscenario, ScenarioFel, platta_ut
from lapp.kontrakt import Gatusektion, ProjektLapp
from lapp.lokal_omrakning import LokalOmrakningResultat, uppdatera_sankgraf_lokalt
from lapp.tillampa import LappTillampning, tillampa_lapp
from sankgraf.typer import SankGraf
from vatten.blaytor import K1Indata

ScenarioLappFel = ScenarioFel
"""Kompatibilitetsnamn: scenariot kan inte uttryckas som K3-lapp (kärnans `ScenarioFel`)."""


@dataclass(frozen=True)
class ScenarioSomLapp:
    lapp: ProjektLapp
    ignorerade: tuple[str, ...]
    """andring_id för ändringar som avsiktligt lämnats utanför (enligt `ignorera_typer`)."""


def scenario_till_lapp(
    scenario: Planscenario,
    kanda: Mapping[str, Planscenario] | None = None,
    *,
    ignorera_typer: Collection[str] = (),
) -> ScenarioSomLapp:
    """Plattar ut scenariot mot basen och bygger en K3-lapp med samma bas_recept (via kärnan)."""
    k = dict(kanda or {})
    k.setdefault(scenario.nyckel, scenario)
    kvar = []
    ignorerade: list[str] = []
    for a in platta_ut(scenario, k):
        if a.typ in ignorera_typer:
            ignorerade.append(a.andring_id)
        elif a.typ not in STODDA_TYPER:
            raise ScenarioFel(
                f"Ändringstypen '{a.typ}' ('{a.andring_id}') har ingen motsvarighet i K3. "
                "Ange den i ignorera_typer om vattenfrågan ska bortse från den."
            )
        else:
            kvar.append(a)
    lapp = k9_till_lapp(kvar, scenario)  # kärnans ordning: andring_id
    lapp = replace(
        lapp,
        lapp_id=scenario.scenario_id,
        beskrivning=f"Ur planscenario {scenario.nyckel[:12]} (K9/v1)",
    )
    return ScenarioSomLapp(lapp, tuple(ignorerade))


@dataclass(frozen=True)
class ScenarioTillampat:
    tillampning: LappTillampning
    omrakning: LokalOmrakningResultat | None
    """None när scenariot inte ändrar någon cell (då gäller basgrafen)."""
    graf: SankGraf
    ignorerade: tuple[str, ...]


def tillampa_scenario(
    bas_graf: SankGraf,
    k1: K1Indata,
    scenario: Planscenario,
    kanda: Mapping[str, Planscenario] | None = None,
    *,
    bas_recept_sha256: str,
    sektioner: Mapping[str, Gatusektion] | None = None,
    ignorera_typer: Collection[str] = (),
) -> ScenarioTillampat:
    """Tillämpar scenariot på K1 (`tillampa_lapp`) och räknar om sänkgrafen lokalt."""
    som = scenario_till_lapp(scenario, kanda, ignorera_typer=ignorera_typer)
    t = tillampa_lapp(
        k1.mark_z, k1.osakerhet_z, k1.markslag, k1.geo, som.lapp,
        bas_recept_sha256=bas_recept_sha256,
        bas_brytlinjer=k1.brytlinjer,
        bas_oppningar=k1.oppningar,
        sektioner=sektioner,
    )
    if not np.any(t.andrad_mask):
        return ScenarioTillampat(t, None, bas_graf, som.ignorerade)
    om = uppdatera_sankgraf_lokalt(
        bas_graf, t.mark_z, t.osakerhet_z, t.markslag, t.andrad_mask,
        brytlinjer=t.alla_brytlinjer, oppningar=t.oppningar,
    )
    return ScenarioTillampat(t, om, om.ny_graf, som.ignorerade)
