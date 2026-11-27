# ruff: noqa: E501
"""K30 – Beställning: syfte, skede, mottagare, ämnen och frågor, projekt och scenario (R2).

En beställning föregår rapporten. Den säger vad rapporten ska hjälpa till att avgöra och för vem,
och **styr vilka frågor ur katalogen som tas med** och hur ingressen skrivs. Den lagras som en egen
fil (`<rapportmapp>/bestallningar/b-<12 hex>.json`) och kopieras in i rapporten (K33, valfritt
fält `bestallning`), så att rapporten är självbärande.

Urvalsregler (i ordning; de är förval som ska granskas av Joel, liksom katalogen):

1. `fragor` (uttryckligt urval av id) ersätter allt nedan; frågorna ska höra till `amnen`.
2. Annars: frågorna i `amnen` (`dagsljus` = DL, `sol` = SO, `skyfall` = SK, `ekologi` = EK, `luft`, `buller`).
3. `villkor` (valfri lista) filtrerar på frågans villkor (som i katalogen; "Alla …" gäller alltid).
4. Mottagaren filtrerar på frågans typ: planhandläggare alla typer; byggaktör `Föreskrift`, `Lag`,
   `Lag/norm`, `Riktvärde`, `Kommunal riktlinje`, `Verksamhetsriktlinje`, `Vägledning` och `Metod`
   (vad som gäller och hur det beräknas); granskare `Metod`, `Projektkontroll` och `Föreskrift`
   (hur resultatet kan följas och kontrolleras).
5. `bara_med_underlag` (förval nej) behåller bara frågor där Floden i dag kan ge underlag helt
   eller delvis (`visat` eller `delvis`, se `rapport.fragekatalog`). Förvalet är nej: en skiss ska
   också visa var underlag saknas.

Skedet `planunderlag` kan beställas men rapporten för det (nivå 2, R3) finns inte än.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from gemensamt.hash import sha256_bytes
from rapport.fragekatalog import (
    Fraga,
    Fragekatalog,
    FragekatalogFel,
    formaga_for,
)

KONTRAKT_K30: Final[str] = "K30/v1"
SKEDEN: Final[tuple[str, ...]] = ("skiss", "planunderlag")
MOTTAGARROLLER: Final[tuple[str, ...]] = ("planhandlaggare", "byggaktor", "granskare")
MOTTAGARTEXT: Final[dict[str, str]] = {
    "planhandlaggare": "planhandläggare",
    "byggaktor": "byggaktör",
    "granskare": "granskare",
}
BESTALLNINGSAMNEN: Final[tuple[str, ...]] = ("skyfall", "dagsljus", "sol", "ekologi", "luft", "buller")
_AMNE_OMRADE: Final[dict[str, str]] = {
    "skyfall": "skyfall",
    "dagsljus": "dagsljus",
    "sol": "solstudier",
    "ekologi": "ekologi",
    "luft": "luft",
    "buller": "buller",
}
AMNE_TILL_RAPPORTAMNE: Final[dict[str, str]] = {
    "skyfall": "skyfall",
    "dagsljus": "dagsljus",
    "sol": "dagsljus",
    "ekologi": "ekologi",
    "luft": "luft",
    "buller": "buller",
}
"""Beställningens ämne -> rapportens ämne (dagsljus och sol hör till samma rapport)."""
ROLL_TYPER: Final[dict[str, tuple[str, ...] | None]] = {
    "planhandlaggare": None,
    "byggaktor": (
        "Föreskrift", "Lag", "Lag/norm", "Riktvärde", "Kommunal riktlinje",
        "Verksamhetsriktlinje", "Vägledning", "Metod",
    ),
    "granskare": ("Metod", "Projektkontroll", "Föreskrift"),
}  # fmt: skip
_ID_RE: Final[re.Pattern[str]] = re.compile(r"^b-[0-9a-f]{12}$")
_FRAGA_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2}-\d{2}$")
_SKYDD: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    re.compile(r"\b(?:19|20)?\d{6}[-+]\d{4}\b"),
)
_SYFTE_MAX = 600
_NAMN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class BestallningFel(Exception):
    """Beställningen är ogiltig eller finns inte. Meddelandet är skrivet för användaren och AI:n."""


@dataclass(frozen=True)
class Bestallning:
    """K30/v1."""

    bestallning_id: str
    syfte: str
    skede: str
    mottagare_roll: str
    amnen: tuple[str, ...]
    projekt: str
    skapad: str
    fragor: tuple[str, ...] = ()
    villkor: tuple[str, ...] = ()
    bara_med_underlag: bool = False
    scenario: dict[str, Any] | None = None
    """`{"fore": "bas", "efter": ["hojd", "nytt_hus"]}` eller None (alla jämförelser i körningen)."""
    urval: tuple[str, ...] = ()
    """De frågor som beställningen ger (id), beräknade när beställningen skapades."""
    utelamnade: int = 0
    """Antal frågor i ämnena som inte kom med."""
    katalog: dict[str, Any] = field(default_factory=dict)
    kontrakt: str = KONTRAKT_K30

    @property
    def rapportamne(self) -> str:
        return AMNE_TILL_RAPPORTAMNE[self.amnen[0]]

    def till_dict(self) -> dict[str, Any]:
        return {
            "kontrakt": self.kontrakt,
            "bestallning_id": self.bestallning_id,
            "syfte": self.syfte,
            "skede": self.skede,
            "mottagare_roll": self.mottagare_roll,
            "amnen": list(self.amnen),
            "projekt": self.projekt,
            "skapad": self.skapad,
            "fragor": list(self.fragor),
            "villkor": list(self.villkor),
            "bara_med_underlag": self.bara_med_underlag,
            "scenario": self.scenario,
            "urval": list(self.urval),
            "utelamnade": self.utelamnade,
            "katalog": dict(self.katalog),
        }

    @classmethod
    def fran_dict(cls, d: Mapping[str, Any]) -> Bestallning:
        if d.get("kontrakt") != KONTRAKT_K30:
            raise BestallningFel(f"Beställningen har kontraktet {d.get('kontrakt')!r}, väntade K30/v1.")
        try:
            return cls(
                bestallning_id=str(d["bestallning_id"]),
                syfte=str(d["syfte"]),
                skede=str(d["skede"]),
                mottagare_roll=str(d["mottagare_roll"]),
                amnen=tuple(str(a) for a in d["amnen"]),
                projekt=str(d["projekt"]),
                skapad=str(d["skapad"]),
                fragor=tuple(str(f) for f in d.get("fragor", [])),
                villkor=tuple(str(v) for v in d.get("villkor", [])),
                bara_med_underlag=bool(d.get("bara_med_underlag", False)),
                scenario=None if d.get("scenario") is None else dict(d["scenario"]),
                urval=tuple(str(f) for f in d.get("urval", [])),
                utelamnade=int(d.get("utelamnade", 0)),
                katalog=dict(d.get("katalog", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BestallningFel(f"Beställningen är ofullständig: {exc}.") from exc


def _scenario(s: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if s is None:
        return None
    fore = s.get("fore")
    efter = s.get("efter")
    if isinstance(efter, str):
        efter = [efter]
    if not isinstance(fore, str) or not _NAMN_RE.match(fore):
        raise BestallningFel("scenario.fore ska vara ett scenario-id (t.ex. 'bas').")
    if (
        not isinstance(efter, Sequence)
        or not efter
        or not all(isinstance(e, str) and _NAMN_RE.match(e) for e in efter)
    ):
        raise BestallningFel("scenario.efter ska vara ett eller flera scenario-id.")
    return {"fore": fore, "efter": list(dict.fromkeys(efter))}


def valj_fragor(
    katalog: Fragekatalog,
    *,
    amnen: Sequence[str],
    mottagare_roll: str,
    fragor: Sequence[str] = (),
    villkor: Sequence[str] = (),
    bara_med_underlag: bool = False,
) -> tuple[list[Fraga], int]:
    """Frågorna som beställningen ger (se modulens regler) och hur många som utelämnas."""
    alla: list[Fraga] = []
    for a in amnen:
        alla += katalog.sok(omrade=_AMNE_OMRADE[a])
    if fragor:
        valda = [katalog.hamta(f) for f in dict.fromkeys(fragor)]
        utanfor = [f.id for f in valda if f not in alla]
        if utanfor:
            raise BestallningFel(
                f"Frågorna {', '.join(utanfor)} hör inte till ämnena {', '.join(amnen)}."
            )
        return valda, len(alla) - len(valda)
    valda = list(alla)
    if villkor:
        ok = {f.id for a in amnen for f in katalog.sok(omrade=_AMNE_OMRADE[a], villkor=villkor)}
        valda = [f for f in valda if f.id in ok]
    typer = ROLL_TYPER[mottagare_roll]
    if typer is not None:
        valda = [f for f in valda if f.typ in typer]
    if bara_med_underlag:
        valda = [f for f in valda if (fm := formaga_for(f.id)) is not None and fm.niva != "saknas"]
    return valda, len(alla) - len(valda)


def skapa_bestallning(
    katalog: Fragekatalog,
    *,
    syfte: str,
    skede: str = "skiss",
    mottagare_roll: str = "planhandlaggare",
    amnen: Sequence[str],
    projekt: str,
    fragor: Sequence[str] = (),
    villkor: Sequence[str] = (),
    bara_med_underlag: bool = False,
    scenario: Mapping[str, Any] | None = None,
) -> Bestallning:
    """Validerar och bygger en beställning (sparas inte)."""
    syfte = " ".join(str(syfte).split())
    if not syfte:
        raise BestallningFel("Ange syftet: vad ska rapporten hjälpa till att avgöra?")
    if len(syfte) > _SYFTE_MAX:
        raise BestallningFel(f"Syftet är för långt (högst {_SYFTE_MAX} tecken).")
    if any(m.search(syfte) for m in _SKYDD):
        raise BestallningFel("Syftet liknar en e-postadress eller ett personnummer; ta bort det.")
    if skede not in SKEDEN:
        raise BestallningFel(f"Okänt skede '{skede}'. Tillåtna: {', '.join(SKEDEN)}.")
    if mottagare_roll not in MOTTAGARROLLER:
        raise BestallningFel(
            f"Okänd mottagare '{mottagare_roll}'. Tillåtna: {', '.join(MOTTAGARROLLER)}."
        )
    amnen_t = tuple(dict.fromkeys(amnen))
    if not amnen_t:
        raise BestallningFel(f"Ange minst ett ämne ({', '.join(BESTALLNINGSAMNEN)}).")
    for a in amnen_t:
        if a not in BESTALLNINGSAMNEN:
            raise BestallningFel(f"Okänt ämne '{a}'. Tillåtna: {', '.join(BESTALLNINGSAMNEN)}.")
    if len({AMNE_TILL_RAPPORTAMNE[a] for a in amnen_t}) > 1:
        raise BestallningFel(
            "Ämnena hör till olika rapporter (dagsljus och sol kan vara i samma). Gör en "
            "beställning per rapport."
        )
    for f in fragor:
        if not _FRAGA_RE.match(f):
            raise BestallningFel(f"'{f}' är inget fråge-id (t.ex. DL-10).")
    try:
        valda, utel = valj_fragor(
            katalog,
            amnen=amnen_t,
            mottagare_roll=mottagare_roll,
            fragor=fragor,
            villkor=villkor,
            bara_med_underlag=bara_med_underlag,
        )
    except FragekatalogFel as exc:
        raise BestallningFel(str(exc)) from exc
    if not valda:
        raise BestallningFel(
            "Urvalet blev tomt: inga frågor ur katalogen matchar ämnen, villkor, mottagare och "
            "underlagskrav."
        )
    skapad = datetime.now(UTC).isoformat(timespec="seconds")
    bid = (
        "b-"
        + sha256_bytes(
            json.dumps([projekt, list(amnen_t), syfte, skapad, os.urandom(8).hex()]).encode()
        )[:12]
    )
    return Bestallning(
        bestallning_id=bid,
        syfte=syfte,
        skede=skede,
        mottagare_roll=mottagare_roll,
        amnen=amnen_t,
        projekt=projekt,
        skapad=skapad,
        fragor=tuple(dict.fromkeys(fragor)),
        villkor=tuple(v for v in villkor if v.strip()),
        bara_med_underlag=bool(bara_med_underlag),
        scenario=_scenario(scenario),
        urval=tuple(f.id for f in valda),
        utelamnade=utel,
        katalog={"granskningsdatum": katalog.granskningsdatum},
    )


class BestallningArkiv:
    """Beställningarna på disk: `<mapp>/b-<12 hex>.json`."""

    def __init__(self, mapp: str | Path) -> None:
        self.mapp = Path(mapp)
        self._lock = threading.Lock()

    def spara(self, b: Bestallning) -> Path:
        self.mapp.mkdir(parents=True, exist_ok=True)
        fil = self.mapp / f"{b.bestallning_id}.json"
        tmp = fil.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(
                json.dumps(b.till_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, fil)
        return fil

    def las(self, bestallning_id: str) -> Bestallning:
        if not _ID_RE.match(bestallning_id):
            raise BestallningFel(f"Ogiltigt beställnings-id '{bestallning_id}' (b-<12 hex>).")
        fil = self.mapp / f"{bestallning_id}.json"
        if not fil.is_file():
            raise BestallningFel(f"Beställningen '{bestallning_id}' finns inte.")
        return Bestallning.fran_dict(json.loads(fil.read_text(encoding="utf-8")))

    def lista(self, projekt: str | None = None) -> list[Bestallning]:
        if not self.mapp.is_dir():
            return []
        ut: list[Bestallning] = []
        for fil in sorted(self.mapp.glob("b-*.json")):
            try:
                b = self.las(fil.stem)
            except (BestallningFel, ValueError):
                continue
            if projekt is None or b.projekt == projekt:
                ut.append(b)
        ut.sort(key=lambda b: (b.skapad, b.bestallning_id), reverse=True)
        return ut


def sammanfattning_bestallning(b: Bestallning) -> dict[str, Any]:
    return {
        "bestallning_id": b.bestallning_id,
        "syfte": b.syfte,
        "skede": b.skede,
        "mottagare_roll": b.mottagare_roll,
        "amnen": list(b.amnen),
        "projekt": b.projekt,
        "skapad": b.skapad,
        "antal_fragor": len(b.urval),
        "utelamnade": b.utelamnade,
        "fragor": list(b.urval),
        "scenario": b.scenario,
    }


__all__ = [
    "AMNE_TILL_RAPPORTAMNE",
    "BESTALLNINGSAMNEN",
    "KONTRAKT_K30",
    "MOTTAGARROLLER",
    "MOTTAGARTEXT",
    "ROLL_TYPER",
    "SKEDEN",
    "Bestallning",
    "BestallningArkiv",
    "BestallningFel",
    "sammanfattning_bestallning",
    "skapa_bestallning",
    "valj_fragor",
]
