"""Lösargränssnitt (kontrakt K21/v1) och register för kärnan (T20).

En lösare är en deterministisk funktion från (scenario, parametrar, indata, beroenden)
till filer i en utmapp. Kärnan sköter nycklar, cache, manifest och jämförelser.

En domän ansluter sina lösare genom att lägga en modul `<paket>/floden_losare.py` med en
lista `LOSARE: list[Analyslosare]`. Kärnan hittar den automatiskt; ingen gemensam fil behöver
ändras. Se `docs/ANSLUTA_LOSARE.md`.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from karna.rutscenario import Ruta, RutScenario

KONTRAKT_K21 = "K21/v1"
GILTIGA_DOMANER: frozenset[str] = frozenset(
    {"vatten", "luft", "buller", "dagsljus", "geometri", "ovrigt"}
)
_NAMN_MONSTER = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_ROLL_MONSTER = re.compile(r"^[a-z][a-z0-9_]*$")


class LosarFel(Exception):
    """Fel i en lösare, i dess parametrar eller i registret."""


@dataclass(frozen=True)
class BeroendeResultat:
    """Resultat från en analys som den aktuella analysen beror på.

    `mapp` är satt för globala analyser. `per_ruta` är satt för rutvisa analyser och
    innehåller de rutor som ligger inom den beroende analysens påverkansradie.
    """

    analys_id: str
    losare: str
    mapp: Path | None = None
    per_ruta: Mapping[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class LosarKontext:
    """Allt en lösare får se under en körning. Lösaren skriver bara i `utmapp`."""

    utmapp: Path
    scenario: RutScenario
    parametrar: Mapping[str, Any]
    indata: Mapping[str, Path]
    beroenden: Mapping[str, BeroendeResultat]
    ruta: Ruta | None = None
    grannar: tuple[Ruta, ...] = ()

    def rutor_att_lasa(self) -> tuple[Ruta, ...]:
        """Rutan själv följd av grannarna inom radien, eller hela scenariot om globalt."""
        if self.ruta is None:
            return self.scenario.rutor
        return (self.ruta, *self.grannar)


@dataclass(frozen=True)
class LosarUtdata:
    """Vad en lösare lämnar tillbaka. `filer` mappar roll -> filnamn relativt `utmapp`."""

    filer: Mapping[str, str]
    matvarden: Mapping[str, Any] = field(default_factory=dict)
    varningar: tuple[str, ...] = ()


class Analyslosare(ABC):
    """Basklass för lösare enligt K21/v1.

    Klassattribut som varje lösare sätter:
    - `namn`: `<doman>.<namn>`, t.ex. `dagsljus.vsc_fasad`.
    - `doman`: en av `GILTIGA_DOMANER`.
    - `version`: höjs när resultatet kan ändras för samma indata. Ingår i cachenyckeln.
    - `beskrivning`: en mening.
    - `indataroller`: roller som frågan måste ange under `[analyser.indata]`.
    - `utdataroller`: roller som lösaren alltid lämnar.
    """

    namn: ClassVar[str]
    doman: ClassVar[str]
    version: ClassVar[str]
    beskrivning: ClassVar[str] = ""
    indataroller: ClassVar[tuple[str, ...]] = ()
    utdataroller: ClassVar[tuple[str, ...]] = ()

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        """Returnerar fullständiga parametrar (med standardvärden) eller kastar `LosarFel`.

        Resultatet ingår i cachenyckeln, så standardvärden ska fyllas i här och inte i
        `kor`. Okända parametrar ska ge fel, inte ignoreras.
        """
        if parametrar:
            raise LosarFel(f"{self.namn} tar inga parametrar, fick {sorted(parametrar)}.")
        return {}

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        """Hur långt bort en ändring kan påverka resultatet, i meter.

        `None` betyder globalt: hela scenariot körs som ett jobb (t.ex. skyfall, där ett
        avrinningsområde kan vara godtyckligt stort). Ett tal betyder att lösaren körs
        rutvis och att resultatet för en ruta bara beror på rutor inom radien.
        """
        return None

    @abstractmethod
    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        """Kör lösaren och skriver resultat i `kontext.utmapp`."""


def _validera_klass(losare: Analyslosare) -> None:
    for attr in ("namn", "doman", "version"):
        if not isinstance(getattr(type(losare), attr, None), str):
            raise LosarFel(f"Lösaren {type(losare).__name__} saknar klassattributet '{attr}'.")
    if not _NAMN_MONSTER.match(losare.namn):
        raise LosarFel(f"Ogiltigt lösarnamn '{losare.namn}', förväntade '<doman>.<namn>'.")
    if losare.doman not in GILTIGA_DOMANER:
        raise LosarFel(
            f"Okänd domän '{losare.doman}' för {losare.namn}. Giltiga: {sorted(GILTIGA_DOMANER)}."
        )
    if not losare.namn.startswith(losare.doman + "."):
        raise LosarFel(f"Lösarnamnet '{losare.namn}' måste börja med domänen '{losare.doman}.'.")
    if not losare.version.strip():
        raise LosarFel(f"Lösaren {losare.namn} har tom version.")
    for roll in (*losare.indataroller, *losare.utdataroller):
        if not _ROLL_MONSTER.match(roll):
            raise LosarFel(f"Ogiltig roll '{roll}' i {losare.namn}.")


_REGISTER: dict[str, Analyslosare] = {}
_UPPTACKT = False


def registrera_losare(losare: Analyslosare, *, ersatt: bool = False) -> None:
    """Registrerar en lösare. Två lösare med samma namn ger fel om inte `ersatt=True`."""
    _validera_klass(losare)
    finns = _REGISTER.get(losare.namn)
    if finns is not None and not ersatt and type(finns) is not type(losare):
        raise LosarFel(f"Lösaren '{losare.namn}' är redan registrerad av {type(finns).__name__}.")
    _REGISTER[losare.namn] = losare


def _upptack_losare() -> None:
    """Importerar `<paket>.floden_losare` för alla paket bredvid `karna` och registrerar."""
    global _UPPTACKT
    if _UPPTACKT:
        return
    _UPPTACKT = True

    from karna import inbyggda

    for losare in inbyggda.LOSARE:
        registrera_losare(losare)

    src_rot = Path(__file__).resolve().parent.parent
    for modul in sorted(pkgutil.iter_modules([str(src_rot)]), key=lambda m: m.name):
        if not modul.ispkg or modul.name == "karna":
            continue
        if not (src_rot / modul.name / "floden_losare.py").is_file():
            continue
        mod = importlib.import_module(f"{modul.name}.floden_losare")
        lista = getattr(mod, "LOSARE", None)
        if not isinstance(lista, list) or not all(isinstance(x, Analyslosare) for x in lista):
            raise LosarFel(f"{modul.name}.floden_losare måste exponera LOSARE: list[Analyslosare].")
        for losare in lista:
            registrera_losare(losare)


def hamta_losare(namn: str) -> Analyslosare:
    """Hämtar en registrerad lösare, med tydligt fel och förslag om den saknas."""
    _upptack_losare()
    if namn not in _REGISTER:
        kand = ", ".join(sorted(_REGISTER)) or "(inga)"
        raise LosarFel(f"Okänd lösare '{namn}'. Registrerade: {kand}.")
    return _REGISTER[namn]


def lista_losare() -> list[Analyslosare]:
    """Alla registrerade lösare, sorterade på namn."""
    _upptack_losare()
    return [_REGISTER[n] for n in sorted(_REGISTER)]
