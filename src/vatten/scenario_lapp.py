"""Planscenario (K9/v1) → projektlapp (K3/v1), och scenariot tillämpat med lokal omräkning.

Ändringstyp i K9 → K3:

- `byggnad_ny` → `LappByggnad`: kräver `entre_rh2000`, `entre_punkt`;
  valfritt `hojd_m` (hinderhöjd), `tak_rh2000`, `gransdjup_m`.
- `markhojd` → `HojdYta`: kräver `niva_rh2000` (eller `landxml`); valfritt `markslag`,
  `osakerhet_m`.
- `markslag` → `LappMarkslag`: kräver `kod`.
- `gata` → `LappGata`: kräver `sektion`; valfritt `profil_rh2000`, `markslag_korbana`,
  `markslag_gangbana`.
- `magasin` → `LappMagasin`: kräver `volym_m3`; valfritt `botten_rh2000`, `markslag`.

`byggnad_bort`, `byggnad_hojd`, `vegetation` och `markhojd` med `delta_m` har ingen motsvarighet
i K3 och ger fel. Typer som en vattenfråga uttryckligen kan bortse från (t.ex. `vegetation`, som
bara rör luft, buller och dagsljus) anges i `ignorera_typer`; de listas i resultatet.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from karna.scenario import Andring, Planscenario, platta_ut
from lapp.kontrakt import (
    Gatusektion,
    HojdYta,
    LappByggnad,
    LappGata,
    LappMagasin,
    LappMarkslag,
    ProjektLapp,
)
from lapp.lokal_omrakning import LokalOmrakningResultat, uppdatera_sankgraf_lokalt
from lapp.tillampa import LappTillampning, tillampa_lapp
from sankgraf.typer import SankGraf
from vatten.blaytor import K1Indata


class ScenarioLappFel(ValueError):
    """Scenariot kan inte uttryckas som en K3-projektlapp."""


def _ring(a: Andring) -> tuple[tuple[float, float], ...]:
    g = a.geometri or {}
    if g.get("type") != "Polygon":
        raise ScenarioLappFel(f"{a.typ} '{a.andring_id}' kräver en Polygon, fick {g.get('type')}.")
    ring = g["coordinates"][0]
    pts = tuple((float(p[0]), float(p[1])) for p in ring)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _linje(a: Andring) -> tuple[tuple[float, float], ...]:
    g = a.geometri or {}
    if g.get("type") != "LineString":
        raise ScenarioLappFel(f"gata '{a.andring_id}' kräver en LineString, fick {g.get('type')}.")
    return tuple((float(p[0]), float(p[1])) for p in g["coordinates"])


def _krav(a: Andring, nyckel: str) -> Any:
    if nyckel not in a.egenskaper:
        raise ScenarioLappFel(f"{a.typ} '{a.andring_id}' saknar egenskapen '{nyckel}'.")
    return a.egenskaper[nyckel]


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
    """Plattar ut scenariot mot basen och bygger en K3-lapp med samma bas_recept."""
    k = dict(kanda or {})
    k.setdefault(scenario.nyckel, scenario)
    hojdytor: list[HojdYta] = []
    byggnader: list[LappByggnad] = []
    gator: list[LappGata] = []
    magasin: list[LappMagasin] = []
    markslag: list[LappMarkslag] = []
    ignorerade: list[str] = []
    for a in platta_ut(scenario, k):
        e = a.egenskaper
        if a.typ in ignorera_typer:
            ignorerade.append(a.andring_id)
        elif a.typ == "byggnad_ny":
            ep = _krav(a, "entre_punkt")
            byggnader.append(
                LappByggnad(
                    byggnad_id=a.andring_id,
                    polygon=_ring(a),
                    entre_rh2000=float(_krav(a, "entre_rh2000")),
                    entre_punkt=(float(ep[0]), float(ep[1])),
                    hinderhojd_m=float(e.get("hojd_m", 10.0)),
                    tak_rh2000=None if e.get("tak_rh2000") is None else float(e["tak_rh2000"]),
                    gransdjup_m=float(e.get("gransdjup_m", 0.10)),
                )
            )
        elif a.typ == "markhojd":
            if "delta_m" in e:
                raise ScenarioLappFel(
                    f"markhojd '{a.andring_id}' med delta_m saknar motsvarighet i K3 "
                    "(ange niva_rh2000)."
                )
            if "niva_rh2000" not in e and "landxml" not in e:
                raise ScenarioLappFel(f"markhojd '{a.andring_id}' saknar niva_rh2000/landxml.")
            hojdytor.append(
                HojdYta(
                    yta_id=a.andring_id,
                    polygon=_ring(a),
                    hojd_rh2000=None if e.get("niva_rh2000") is None else float(e["niva_rh2000"]),
                    landxml=e.get("landxml"),
                    osakerhet_m=float(e.get("osakerhet_m", 0.05)),
                    markslag=None if e.get("markslag") is None else int(e["markslag"]),
                )
            )
        elif a.typ == "markslag":
            markslag.append(LappMarkslag(a.andring_id, _ring(a), int(_krav(a, "kod"))))
        elif a.typ == "gata":
            profil = e.get("profil_rh2000")
            gator.append(
                LappGata(
                    gata_id=a.andring_id,
                    mittlinje=_linje(a),
                    sektion=str(_krav(a, "sektion")),
                    profil_rh2000=None if profil is None else tuple(float(v) for v in profil),
                    markslag_korbana=int(e.get("markslag_korbana", 1)),
                    markslag_gangbana=int(e.get("markslag_gangbana", 1)),
                )
            )
        elif a.typ == "magasin":
            magasin.append(
                LappMagasin(
                    magasin_id=a.andring_id,
                    polygon=_ring(a),
                    volym_m3=float(_krav(a, "volym_m3")),
                    botten_rh2000=None
                    if e.get("botten_rh2000") is None
                    else float(e["botten_rh2000"]),
                    markslag=int(e.get("markslag", 4)),
                )
            )
        else:
            raise ScenarioLappFel(
                f"Ändringstypen '{a.typ}' ('{a.andring_id}') har ingen motsvarighet i K3. "
                "Ange den i ignorera_typer om vattenfrågan ska bortse från den."
            )
    lapp = ProjektLapp(
        lapp_id=scenario.scenario_id,
        bas_recept=scenario.bas_recept,
        hojdytor=tuple(hojdytor),
        byggnader=tuple(byggnader),
        gator=tuple(gator),
        magasin=tuple(magasin),
        markslag_andringar=tuple(markslag),
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
