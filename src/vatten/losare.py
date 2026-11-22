"""Vattnets lösare i kärnans beräkningsgraf (`karna.Losare`) och dess analystyper (K10).

Lösare (höj `version` när koden ändras så att resultatet kan ändras):

- `vatten.sankgraf` (v2) – K1-filer → sänkgraf, skriven som K2 i `k2/` och läst tillbaka av nästa
  nod med `sankgraf.k2_las.las_sankgraf_k2` (v1 använde en intern pickle-fil).
- `vatten.blaytor` – sänkgraf + regn_mm → `max_djup.tif`, `blaytor.json`.
- `vatten.blaytor_scenario` – bas-sänkgraf + K1 + planscenario (K9, utplattat i parametrarna) →
  scenariot tillämpat med lokal omräkning, sedan blåytor.

Analystyp `blaytor_mm` (modul "vatten") med parametrarna `regn_mm`, valfritt
`initialforlust_mm` (markslagskod → mm), `persistens_faktor`, `ignorera_typer`, `takvatten`
(standard på) och `takvatten_andel_ledning` (standard 0). Blåytorna är regnmängd utan tid och
har därför ingen avtappning (den finns i `vatten.statisk_fyllning` med varaktighet, i
dynamiken och i 2D).

Basens K1-filer hittas via `registrera_bas(bas_recept_sha256, k1_mapp)`. Kärnan ger
nodbyggaren scenariot utplattat; `registrera_scenarier` behövs bara när en nod byggs direkt
med ett scenario som har förälder.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from karna.fraga import AnalysSteg, registrera_analystyp
from karna.graf import Losare, Nod
from karna.scenario import Andring, Planscenario, platta_ut
from sankgraf.k2_las import las_sankgraf_k2
from sankgraf.typer import SankGraf, skriv_sankgraf_k2
from vatten.blaytor import (
    BlaytorFel,
    blaytor_vid_mm,
    bygg_graf,
    k1_filer,
    las_k1,
    skriv_blaytor,
)
from vatten.scenario_lapp import tillampa_scenario

MODUL = "vatten"
K1_ROLLER = ("mark", "osakerhet", "markslag", "brytlinjer", "oppningar")
K2_MAPP = "k2"

_BASER: dict[str, dict[str, Path]] = {}
_SCENARIER: dict[str, Planscenario] = {}


def registrera_bas(bas_recept_sha256: str, k1_mapp: str | Path) -> None:
    """Knyter en markmodells receptnyckel till dess K1-mapp på disk."""
    _BASER[bas_recept_sha256] = k1_filer(k1_mapp)


def registrera_scenarier(scenarier: Iterable[Planscenario]) -> None:
    for s in scenarier:
        _SCENARIER[s.nyckel] = s


def _forlust(p: Mapping[str, Any]) -> dict[int, float] | None:
    raw = p.get("initialforlust_mm")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise BlaytorFel("initialforlust_mm måste vara en tabell markslag → mm.")
    return {int(k): float(v) for k, v in raw.items()}


def _tak(p: Mapping[str, Any]) -> dict[str, Any]:
    """Takvattnets parametrar med standardvärden (på, 0 till ledningsnät)."""
    tak = p.get("takvatten", True)
    andel = float(p.get("takvatten_andel_ledning", 0.0))
    if not isinstance(tak, bool) or not (0.0 <= andel <= 1.0):
        raise BlaytorFel("takvatten måste vara true/false och takvatten_andel_ledning i [0, 1].")
    return {"takvatten": tak, "takvatten_andel_ledning": andel}


def _k1_ur_indata(indata: Mapping[str, Path]) -> dict[str, Path]:
    return {r: indata[r] for r in K1_ROLLER if r in indata}


def _las_graf(katalog: Path) -> SankGraf:
    """Grafen ur föregående nods K2-mapp (K2/v1 med tilläggsfiler, `sankgraf.k2_las`)."""
    return las_sankgraf_k2(katalog / K2_MAPP)


def _kor_sankgraf(indata: Mapping[str, Path], p: Mapping[str, Any], ut: Path) -> dict[str, Any]:
    k1 = las_k1(_k1_ur_indata(indata))
    graf = bygg_graf(k1, p.get("persistens_faktor"))
    skriv_sankgraf_k2(graf, ut / K2_MAPP)
    return {
        "antal_noder": len(graf.noder),
        "antal_lovnoder": len(graf.lovnoder()),
        "max_sankvolym_m3": max((n.max_volym_m3 for n in graf.noder.values()), default=0.0),
    }


def _kor_blaytor(indata: Mapping[str, Path], p: Mapping[str, Any], ut: Path) -> dict[str, Any]:
    graf = _las_graf(indata["sankgraf"])
    r = blaytor_vid_mm(graf, float(p["regn_mm"]), initialforlust_mm=_forlust(p), **_tak(p))
    skriv_blaytor(r, graf.geo, ut, parametrar={"regn_mm": float(p["regn_mm"]), **_tak(p)})
    return r.matvarden()


def _kor_blaytor_scenario(
    indata: Mapping[str, Path], p: Mapping[str, Any], ut: Path
) -> dict[str, Any]:
    bas = _las_graf(indata["bas_sankgraf"])
    k1 = las_k1(_k1_ur_indata(indata))
    scenario = Planscenario(
        scenario_id="scenario",
        bas_recept=tuple(p["bas_recept"]),
        andringar=tuple(Andring.fran_dict(a) for a in p["andringar"]),
    )
    tl = tillampa_scenario(
        bas, k1, scenario,
        bas_recept_sha256=str(p["bas_recept"][0]),
        ignorera_typer=tuple(p.get("ignorera_typer", ())),
    )
    r = blaytor_vid_mm(tl.graf, float(p["regn_mm"]), initialforlust_mm=_forlust(p), **_tak(p))
    skriv_blaytor(r, tl.graf.geo, ut, parametrar={"regn_mm": float(p["regn_mm"]), **_tak(p)})
    m = r.matvarden()
    m["andrade_celler"] = int(np.count_nonzero(tl.tillampning.andrad_mask))
    m["ignorerade_andringar"] = list(tl.ignorerade)
    if tl.omrakning is not None:
        m["ateranvanda_noder"] = tl.omrakning.antal_ateranvanda_noder
        m["omrakning_s"] = tl.omrakning.kortid_s
    return m


LOSARE_SANKGRAF = Losare("vatten.sankgraf", "2", _kor_sankgraf, modul=MODUL)
# v2 (2026-11-22): takvatten som standard (resultaten ändras där det finns byggnader).
LOSARE_BLAYTOR = Losare("vatten.blaytor", "2", _kor_blaytor, modul=MODUL)
LOSARE_BLAYTOR_SCENARIO = Losare("vatten.blaytor_scenario", "2", _kor_blaytor_scenario, modul=MODUL)


def sankgraf_nod(k1: Mapping[str, Path], persistens_faktor: float | None = None) -> Nod:
    return Nod(LOSARE_SANKGRAF, dict(k1), {"persistens_faktor": persistens_faktor})


def blaytor_nod(
    scenario: Planscenario,
    regn_mm: float,
    *,
    initialforlust_mm: Mapping[int, float] | None = None,
    persistens_faktor: float | None = None,
    ignorera_typer: Iterable[str] = (),
    takvatten: bool = True,
    takvatten_andel_ledning: float = 0.0,
) -> Nod:
    """Nod för blåytor vid `regn_mm` i scenariot (basen om scenariot saknar ändringar)."""
    bas = scenario.bas_recept[0]
    if len(scenario.bas_recept) != 1:
        raise BlaytorFel("Blåytor stöder i dag ett bas_recept (en K1-ruta) per scenario.")
    if bas not in _BASER:
        raise BlaytorFel(f"Basen {bas[:12]}… är inte registrerad (registrera_bas).")
    k1 = _BASER[bas]
    forlust = None if initialforlust_mm is None else {
        str(k): float(v) for k, v in sorted(initialforlust_mm.items())
    }
    graf = sankgraf_nod(k1, persistens_faktor)
    kanda = dict(_SCENARIER)
    kanda[scenario.nyckel] = scenario
    andringar = sorted((a.till_dict() for a in platta_ut(scenario, kanda)),
                       key=lambda d: d["andring_id"])
    tak = _tak({"takvatten": takvatten, "takvatten_andel_ledning": takvatten_andel_ledning})
    if not andringar:
        return Nod(LOSARE_BLAYTOR, {"sankgraf": graf},
                   {"regn_mm": float(regn_mm), "initialforlust_mm": forlust, **tak})
    return Nod(
        LOSARE_BLAYTOR_SCENARIO,
        {"bas_sankgraf": graf, **k1},
        {
            "regn_mm": float(regn_mm),
            "initialforlust_mm": forlust,
            "bas_recept": list(scenario.bas_recept),
            "andringar": andringar,
            "ignorera_typer": sorted(ignorera_typer),
            **tak,
        },
    )


def _bygg_blaytor_mm(steg: AnalysSteg, scenario: Planscenario) -> Nod:
    p = dict(steg.parametrar)
    if "regn_mm" not in p:
        raise BlaytorFel("Analysen blaytor_mm kräver regn_mm.")
    okanda = set(p) - {
        "regn_mm", "initialforlust_mm", "persistens_faktor", "ignorera_typer",
        "takvatten", "takvatten_andel_ledning",
    }
    if okanda:
        raise BlaytorFel(f"Okända parametrar för blaytor_mm: {sorted(okanda)}.")
    raw = p.get("initialforlust_mm")
    forlust = None if raw is None else {int(k): float(v) for k, v in raw.items()}
    return blaytor_nod(
        scenario,
        float(p["regn_mm"]),
        initialforlust_mm=forlust,
        persistens_faktor=p.get("persistens_faktor"),
        ignorera_typer=p.get("ignorera_typer", ()),
        **_tak(p),
    )


def registrera() -> None:
    """Anmäler vattnets analystyper till kärnan (idempotent)."""
    registrera_analystyp("blaytor_mm", MODUL, _bygg_blaytor_mm)
