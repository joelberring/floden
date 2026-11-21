"""Utredningsfråga enligt kontrakt K20/v1 (T20).

En fråga skrivs i TOML (eller JSON med samma struktur):

```toml
kontrakt = "K20/v1"
fraga_id = "ornsberg2-grannar"
beskrivning = "Påverkan på grannarna av alternativ B"

[scenarier.fore]
bas = "data/k1/aspudden"          # K1-ruta eller katalog med K1-rutor (mm_E_N)

[scenarier.alt_b]
bas = "data/k1/aspudden"
lappar = ["lappar/alt-b/lapp.json"] # K3, tillämpas i ordning

[[analyser]]
id = "skyfall"
losare = "vatten.statisk_fyllning"
scenarier = ["fore", "alt_b"]      # utelämnat = alla
parametrar = { regn_mm = 60.0 }

[[jamforelser]]
analys = "skyfall"
utdata = "max_djup"
fore = "fore"
efter = "alt_b"
troskel = 0.05
```

Relativa sökvägar tolkas relativt frågefilens katalog.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KONTRAKT_K20 = "K20/v1"
_ID_MONSTER = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")


class FragaFel(Exception):
    """Fel i en utredningsfråga (K20)."""


@dataclass(frozen=True)
class ScenarioSpec:
    """Ett scenario: bas (K1) och projektlappar (K3) som tillämpas i ordning."""

    id: str
    bas: Path
    lappar: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Analys:
    """En analys: en lösare körd på ett eller flera scenarier."""

    id: str
    losare: str
    scenarier: tuple[str, ...]
    parametrar: Mapping[str, Any] = field(default_factory=dict)
    indata: Mapping[str, Path] = field(default_factory=dict)
    beror_pa: tuple[str, ...] = ()


@dataclass(frozen=True)
class Jamforelse:
    """Skillnad `efter − fore` i ett rasterresultat från en analys."""

    analys: str
    utdata: str
    fore: str
    efter: str
    troskel: float = 0.0


@dataclass(frozen=True)
class Utredningsfraga:
    """En validerad utredningsfråga enligt K20/v1."""

    fraga_id: str
    beskrivning: str
    scenarier: Mapping[str, ScenarioSpec]
    analyser: tuple[Analys, ...]
    jamforelser: tuple[Jamforelse, ...] = ()
    kalla: Path | None = None
    kontrakt: str = KONTRAKT_K20

    def analys(self, analys_id: str) -> Analys:
        for a in self.analyser:
            if a.id == analys_id:
                return a
        raise FragaFel(f"Okänd analys '{analys_id}'.")

    def analysordning(self) -> list[Analys]:
        """Analyserna i beroendeordning (topologisk, stabil). Cykler ger fel."""
        kvar = {a.id: a for a in self.analyser}
        klara: list[Analys] = []
        klara_id: set[str] = set()
        while kvar:
            redo = [a for a in kvar.values() if all(b in klara_id for b in a.beror_pa)]
            if not redo:
                raise FragaFel(f"Cykliskt beroende mellan analyserna: {sorted(kvar)}.")
            for a in sorted(redo, key=lambda x: [y.id for y in self.analyser].index(x.id)):
                klara.append(a)
                klara_id.add(a.id)
                del kvar[a.id]
        return klara


def _krav_str(data: Mapping[str, Any], nyckel: str, sammanhang: str) -> str:
    varde = data.get(nyckel)
    if not isinstance(varde, str) or not varde.strip():
        raise FragaFel(f"{sammanhang}: '{nyckel}' måste vara en icke-tom sträng.")
    return varde


def _krav_id(varde: str, sammanhang: str) -> str:
    if not _ID_MONSTER.match(varde):
        raise FragaFel(
            f"{sammanhang}: id '{varde}' får bara innehålla a–z, 0–9, '-' och '_'."
        )
    return varde


def _sokvag(varde: Any, rot: Path, sammanhang: str) -> Path:
    if not isinstance(varde, str) or not varde.strip():
        raise FragaFel(f"{sammanhang}: sökväg måste vara en icke-tom sträng.")
    p = Path(varde)
    return p if p.is_absolute() else (rot / p)


def _strlista(varde: Any, sammanhang: str) -> tuple[str, ...]:
    if varde is None:
        return ()
    if not isinstance(varde, list) or not all(isinstance(v, str) for v in varde):
        raise FragaFel(f"{sammanhang} måste vara en lista av strängar.")
    return tuple(varde)


def _troskel(varde: Any, sammanhang: str) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise FragaFel(f"{sammanhang}.troskel måste vara ett tal, fick {varde!r}.")
    if not (float(varde) >= 0.0 and float(varde) != float("inf")):
        raise FragaFel(f"{sammanhang}.troskel måste vara ändlig och >= 0, fick {varde!r}.")
    return float(varde)


_TILLATNA_TOPP = {"kontrakt", "fraga_id", "beskrivning", "scenarier", "analyser", "jamforelser"}
_TILLATNA_SCENARIO = {"bas", "lappar"}
_TILLATNA_ANALYS = {"id", "losare", "scenarier", "parametrar", "indata", "beror_pa"}
_TILLATNA_JAMFOR = {"analys", "utdata", "fore", "efter", "troskel"}


def _okanda(data: Mapping[str, Any], tillatna: set[str], sammanhang: str) -> None:
    okanda = sorted(set(data) - tillatna)
    if okanda:
        raise FragaFel(f"{sammanhang}: okända fält {okanda} (stavfel?).")


def fraga_fran_dict(data: Any, rot: Path, kalla: Path | None = None) -> Utredningsfraga:
    """Validerar en deserialiserad fråga. Inga tysta fel: okända fält ger fel."""
    if not isinstance(data, dict):
        raise FragaFel("Frågan måste vara ett objekt.")
    _okanda(data, _TILLATNA_TOPP, "fråga")
    if data.get("kontrakt") != KONTRAKT_K20:
        raise FragaFel(f"Ogiltigt kontrakt {data.get('kontrakt')!r}, förväntade '{KONTRAKT_K20}'.")
    fraga_id = _krav_id(_krav_str(data, "fraga_id", "fråga"), "fråga")
    beskrivning = str(data.get("beskrivning", ""))

    raw_scen = data.get("scenarier")
    if not isinstance(raw_scen, dict) or not raw_scen:
        raise FragaFel("fråga: 'scenarier' måste innehålla minst ett scenario.")
    scenarier: dict[str, ScenarioSpec] = {}
    for sid, spec in raw_scen.items():
        _krav_id(sid, "scenario")
        if not isinstance(spec, dict):
            raise FragaFel(f"scenario '{sid}' måste vara ett objekt.")
        _okanda(spec, _TILLATNA_SCENARIO, f"scenario '{sid}'")
        bas = _sokvag(spec.get("bas"), rot, f"scenario '{sid}'.bas")
        lappar = tuple(
            _sokvag(p, rot, f"scenario '{sid}'.lappar")
            for p in _strlista(spec.get("lappar"), f"scenario '{sid}'.lappar")
        )
        scenarier[sid] = ScenarioSpec(id=sid, bas=bas, lappar=lappar)

    raw_analyser = data.get("analyser")
    if not isinstance(raw_analyser, list) or not raw_analyser:
        raise FragaFel("fråga: 'analyser' måste innehålla minst en analys.")
    analyser: list[Analys] = []
    sedda: set[str] = set()
    for idx, a in enumerate(raw_analyser):
        ctx = f"analyser[{idx}]"
        if not isinstance(a, dict):
            raise FragaFel(f"{ctx} måste vara ett objekt.")
        _okanda(a, _TILLATNA_ANALYS, ctx)
        aid = _krav_id(_krav_str(a, "id", ctx), ctx)
        if aid in sedda:
            raise FragaFel(f"{ctx}: analys-id '{aid}' förekommer flera gånger.")
        sedda.add(aid)
        scen = _strlista(a.get("scenarier"), f"{ctx}.scenarier") or tuple(scenarier)
        if len(set(scen)) != len(scen):
            raise FragaFel(f"{ctx}: samma scenario anges flera gånger: {list(scen)}.")
        for s in scen:
            if s not in scenarier:
                raise FragaFel(f"{ctx}: okänt scenario '{s}'.")
        param = a.get("parametrar", {})
        if not isinstance(param, dict):
            raise FragaFel(f"{ctx}.parametrar måste vara ett objekt.")
        raw_indata = a.get("indata", {})
        if not isinstance(raw_indata, dict):
            raise FragaFel(f"{ctx}.indata måste vara ett objekt roll -> sökväg.")
        indata = {
            roll: _sokvag(p, rot, f"{ctx}.indata.{roll}") for roll, p in raw_indata.items()
        }
        analyser.append(
            Analys(
                id=aid,
                losare=_krav_str(a, "losare", ctx),
                scenarier=scen,
                parametrar=dict(param),
                indata=indata,
                beror_pa=_strlista(a.get("beror_pa"), f"{ctx}.beror_pa"),
            )
        )
    for a in analyser:
        for b in a.beror_pa:
            if b not in sedda:
                raise FragaFel(f"Analys '{a.id}' beror på okänd analys '{b}'.")
            ba = next(x for x in analyser if x.id == b)
            saknas = sorted(set(a.scenarier) - set(ba.scenarier))
            if saknas:
                raise FragaFel(
                    f"Analys '{a.id}' beror på '{b}', som inte körs för scenarierna {saknas}."
                )

    jamforelser: list[Jamforelse] = []
    raw_jamf = data.get("jamforelser", [])
    if not isinstance(raw_jamf, list):
        raise FragaFel("fråga: 'jamforelser' måste vara en lista.")
    for idx, j in enumerate(raw_jamf):
        ctx = f"jamforelser[{idx}]"
        if not isinstance(j, dict):
            raise FragaFel(f"{ctx} måste vara ett objekt.")
        _okanda(j, _TILLATNA_JAMFOR, ctx)
        jf = Jamforelse(
            analys=_krav_str(j, "analys", ctx),
            utdata=_krav_str(j, "utdata", ctx),
            fore=_krav_str(j, "fore", ctx),
            efter=_krav_str(j, "efter", ctx),
            troskel=_troskel(j.get("troskel", 0.0), ctx),
        )
        if jf.analys not in sedda:
            raise FragaFel(f"{ctx}: okänd analys '{jf.analys}'.")
        a_scen = next(x for x in analyser if x.id == jf.analys).scenarier
        for s in (jf.fore, jf.efter):
            if s not in a_scen:
                raise FragaFel(f"{ctx}: analysen '{jf.analys}' körs inte för scenario '{s}'.")
        jamforelser.append(jf)

    fraga = Utredningsfraga(
        fraga_id=fraga_id,
        beskrivning=beskrivning,
        scenarier=scenarier,
        analyser=tuple(analyser),
        jamforelser=tuple(jamforelser),
        kalla=kalla,
    )
    fraga.analysordning()  # fångar cykler direkt
    return fraga


def las_fraga(sokvag: str | Path) -> Utredningsfraga:
    """Läser en fråga från `.toml` eller `.json`."""
    fil = Path(sokvag)
    try:
        text = fil.read_text(encoding="utf-8")
    except OSError as exc:
        raise FragaFel(f"Kunde inte läsa frågan {fil}: {exc}") from exc
    try:
        data = json.loads(text) if fil.suffix.lower() == ".json" else tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise FragaFel(f"Kunde inte tolka {fil}: {exc}") from exc
    return fraga_fran_dict(data, rot=fil.resolve().parent, kalla=fil.resolve())
