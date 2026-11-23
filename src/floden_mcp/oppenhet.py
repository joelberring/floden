# ruff: noqa: E501
"""Spärren för data som inte är öppna: MCP lämnar aldrig ut innehåll härlett ur icke-öppna data.

AI-tjänsten bakom en MCP-klient är extern, och AGENTS.md förbjuder att ledningsdata och stadens icke-öppna
data skickas till en extern AI-tjänst. Spärren är en **uttrycklig kontroll i verktygslagret**
(`floden_mcp.server`), inte något som antas följa av att verktygen bara läser demokvarteret:

- Före varje anrop: `kontrollera_korning` för varje `korning_id` i argumenten.
- Efter varje anrop: varje körning som nämns i svaret kontrolleras innan svaret lämnas ut.
- `korningar_lista` döljer körningar vars indata inte är öppna.
- `fraga_skapa` kontrollerar varje sökväg i frågan (`kontrollera_sokvag`).

En körning är bara öppen om **alla** indatafiler i dess K0-manifest (`run.json`) är öppna.

**Regeln** (dokumenterad i `docs/MCP.md`, avsnitt "Data som inte är öppna", och `docs/DATAKONTRAKT.md`, K38):

1. *Alltid stängt*, utan möjlighet att öppna med metadata: ledningsdata (en sökvägsdel som börjar på
   `ledning`, `data/ledningar`, `[lokal_data] ledningar_mapp`), `data/intern`, `data/konto` och
   `config.local.toml`.
2. *Öppet utan metadata*: demon och det som följer med repot som påhittat eller öppet: `data/demo`,
   `testsvit`, `exempel`, `docs`, `data/regn`, `data/mcp`, `data/lagerprofiler`, samt de mappar som
   anroparen anger som arbetsytor (kartverktygets och MCP:s egna mappar, där AI:n själv skriver).
3. *Öppet med metadata*: en mapp är öppen om en fil `oppenhet.toml` (K38) i mappen eller en
   överordnad mapp säger `oppen = true` **och** ger en känd öppen licens (`KANDA_OPPNA_LICENSER`), eller
   om ett lager i `config.local.toml` (`[oppenhet.lager.<namn>]`: `sokvag`, `oppen`, `licens`) säger det.
   `oppen = false` stänger alltid, även inom demon.
4. *Allt annat är stängt* (`data/lokal`, `[lokal_data] baskarta_mapp`/`tat_laser_mapp` och varje mapp
   utan metadata): neka som standard.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KONTRAKT_OPPENHET = "floden-oppenhet/v1"
METADATAFIL = "oppenhet.toml"
KANDA_OPPNA_LICENSER = frozenset(
    {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "ODbL-1.0", "ODC-By-1.0", "PDDL-1.0"}
)
"""SPDX-id på licenser som tillåter öppen återanvändning. En okänd licens räknas som ingen."""

OPPNA_I_REPOT = ("data/demo", "testsvit", "exempel", "docs", "data/regn", "data/mcp", "data/lagerprofiler")
STANGDA_ALLTID_I_REPOT = ("data/ledningar", "data/intern", "data/konto", "config.local.toml")
MAX_METADATANIVAER = 12
MAX_RUN_JSON = 4000


class OppenhetNekad(Exception):
    """Innehållet är inte öppet. Texten är skriven för klienten (och AI:n) och nämner aldrig en sökväg."""


@dataclass(frozen=True)
class Bedomning:
    oppen: bool
    orsak: str


def _under(sokvag: Path, rot: Path) -> bool:
    return sokvag == rot or sokvag.is_relative_to(rot)


def _lopande(sokvag: str | Path, rot: Path) -> Path:
    p = Path(sokvag).expanduser()
    return (p if p.is_absolute() else rot / p).resolve()


def _ledningsdel(delar: Iterable[str]) -> bool:
    return any(d.lower().startswith("ledning") for d in delar)


def lasa_metadata(fil: Path) -> tuple[bool, str] | None:
    """(öppen?, orsak) ur en `oppenhet.toml`; None om filen inte går att tolka (då: stängt)."""
    try:
        with fil.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return _tolka(data, "oppenhet.toml")


def _tolka(data: Mapping[str, Any], var: str) -> tuple[bool, str]:
    if data.get("oppen") is not True:
        return False, f"{var}: markerad som inte öppen"
    licens = str(data.get("licens", "")).strip()
    if licens not in KANDA_OPPNA_LICENSER:
        return False, f"{var}: licens saknas eller är inte en känd öppen licens"
    return True, f"{var}: öppen ({licens})"


class Oppenhet:
    """Bedömer om en sökväg, en körning eller ett svar bara bygger på öppna data."""

    def __init__(
        self,
        rot: Path,
        *,
        arbetsytor: Sequence[Path] = (),
        extra_oppna: Sequence[Path] = (),
        korningsmapp: Path | None = None,
        konfig: Mapping[str, Any] | None = None,
    ) -> None:
        self.rot = rot.resolve()
        self.arbetsytor = [p.resolve() for p in arbetsytor]
        self.oppna = [(self.rot / d).resolve() for d in OPPNA_I_REPOT] + [
            p.resolve() for p in extra_oppna
        ]
        self.alltid_stangda = [(self.rot / d).resolve() for d in STANGDA_ALLTID_I_REPOT]
        self.korningsmapp = korningsmapp.resolve() if korningsmapp is not None else None
        self.lager: list[tuple[Path, tuple[bool, str]]] = []
        self._korningscache: dict[str, tuple[float, Bedomning]] = {}
        konfig = konfig or {}
        lokal = konfig.get("lokal_data")
        if isinstance(lokal, Mapping) and str(lokal.get("ledningar_mapp", "")).strip():
            self.alltid_stangda.append(_lopande(str(lokal["ledningar_mapp"]), self.rot))
        lagertabell = (konfig.get("oppenhet") or {}).get("lager", {})
        if isinstance(lagertabell, Mapping):
            for namn, post in lagertabell.items():
                if isinstance(post, Mapping) and str(post.get("sokvag", "")).strip():
                    self.lager.append(
                        (_lopande(str(post["sokvag"]), self.rot), _tolka(post, f"lager {namn}"))
                    )
        self.lager.sort(key=lambda t: len(t[0].parts), reverse=True)

    @classmethod
    def for_tjanst(cls, tjanst: Any, konfig: Mapping[str, Any] | None = None) -> Oppenhet:
        """Spärren för en `FlodenTjanst`: demon och MCP:s och kartverktygets arbetsytor är öppna,
        allt annat följer metadata (se modulens regel)."""
        fragor = tjanst.fragor
        arbetsytor = [tjanst.kv.mapp, tjanst.mcp_mapp, fragor.cachemapp, *fragor.fragemappar]
        extra = [fragor.demomapp] if fragor.demomapp is not None else []
        return cls(
            tjanst.rot,
            arbetsytor=arbetsytor,
            extra_oppna=extra,
            korningsmapp=fragor.korningsmapp,
            konfig=konfig,
        )

    # ------------------------------------------------------------------ sökvägar

    def bedom(self, sokvag: str | Path) -> Bedomning:
        p = _lopande(sokvag, self.rot)
        if _ledningsdel(p.relative_to(self.rot).parts if _under(p, self.rot) else p.parts):
            return Bedomning(False, "ledningsdata är aldrig öppna")
        if any(_under(p, s) for s in self.alltid_stangda):
            return Bedomning(False, "markerad som alltid stängd (ledningar, intern data, konto)")
        for lagerrot, (oppen, orsak) in self.lager:
            if _under(p, lagerrot):
                return Bedomning(oppen, orsak)
        meta = self._metadata(p)
        if meta is not None:
            return Bedomning(*meta)
        if any(_under(p, a) for a in self.arbetsytor):
            return Bedomning(True, "arbetsyta (AI:ns egna förslag; ärver indatans öppenhet)")
        if any(_under(p, o) for o in self.oppna):
            return Bedomning(True, "demon eller öppet material som följer med repot")
        return Bedomning(False, "öppenhet saknas: ingen metadata och inte demon")

    def _metadata(self, p: Path) -> tuple[bool, str] | None:
        """Närmaste `oppenhet.toml` i p:s mapp eller en överordnad. Otolkbar fil = stängt."""
        mapp = p if p.is_dir() else p.parent
        for nivå, d in enumerate([mapp, *mapp.parents]):
            if nivå > MAX_METADATANIVAER:
                break
            fil = d / METADATAFIL
            if fil.is_file():
                return lasa_metadata(fil) or (False, "oppenhet.toml går inte att tolka")
        return None

    def kontrollera_sokvag(self, sokvag: str | Path) -> None:
        b = self.bedom(sokvag)
        if not b.oppen:
            raise OppenhetNekad(
                "Nekad: underlaget är inte öppna data (" + b.orsak + "). Innehåll från icke-öppna "
                "data lämnas inte ut via MCP; en extern AI-tjänst får inte ta del av det. "
                "Metadata för öppenhet kan läggas i oppenhet.toml (docs/MCP.md)."
            )

    # ------------------------------------------------------------------ körningar

    def _run_json(self, korning_id: str) -> list[Path]:
        if self.korningsmapp is None:
            return []
        utmapp = self.korningsmapp / korning_id / "resultat"
        if not utmapp.is_dir():
            return []
        ut: list[Path] = []
        for fil in utmapp.rglob("run.json"):
            ut.append(fil)
            if len(ut) >= MAX_RUN_JSON:
                break
        return ut

    def _kallor_i_manifest(self, fil: Path) -> list[Path] | None:
        """Sökvägarna ett manifest pekar på: frågefilens scenarier och analysers indata (rollen
        `fraga`), och indata som är riktiga sökvägar. Jobbens rutindata anges bara med rutans namn
        (och sha256), och ärver frågans källor. None om manifestet eller frågan inte går att läsa."""
        try:
            manifest = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(manifest, dict):
            return None
        ut: list[Path] = []
        for post in manifest.get("indata", []):
            sokvag = post.get("fil") if isinstance(post, dict) else None
            if not isinstance(sokvag, str) or not sokvag:
                continue
            if isinstance(post, dict) and post.get("roll") == "fraga":
                from karna.utredning import FragaFel, las_fraga

                try:
                    fraga = las_fraga(self._relativ(sokvag, fil))
                except FragaFel:
                    return None
                for sc in fraga.scenarier.values():
                    ut.extend((sc.bas, *sc.k9, *sc.lappar))
                for an in fraga.analyser:
                    ut.extend(an.indata.values())
            elif "/" in sokvag or "\\" in sokvag:
                ut.append(self._relativ(sokvag, fil))
        return ut

    def bedom_korning(self, korning_id: str) -> Bedomning:
        """Öppen om alla källor i körningens manifest (frågans scenarier och indata) är öppna.
        Oläsbart eller okänt underlag är stängt; en körning utan resultat har inget att lämna ut."""
        if self.korningsmapp is None:
            return Bedomning(False, "körningens indata kan inte kontrolleras")
        if not korning_id or "/" in korning_id or "\\" in korning_id or ".." in korning_id:
            return Bedomning(False, "ogiltigt körnings-id")
        filer = self._run_json(korning_id)
        if not filer:
            utmapp = self.korningsmapp / korning_id / "resultat"
            if utmapp.is_dir() and any(utmapp.iterdir()):
                return Bedomning(False, "körningens manifest saknas")
            return Bedomning(True, "körningen har inga resultat än")
        stampel = max(f.stat().st_mtime for f in filer) + len(filer)
        cachad = self._korningscache.get(korning_id)
        if cachad is not None and cachad[0] == stampel:
            return cachad[1]
        utfall = Bedomning(True, "alla källor är öppna")
        for fil in sorted(filer):
            kallor = self._kallor_i_manifest(fil)
            if kallor is None:
                utfall = Bedomning(False, "ett manifest eller frågefilen går inte att läsa")
                break
            stangd = next((b for b in map(self.bedom, kallor) if not b.oppen), None)
            if stangd is not None:
                utfall = stangd
                break
        self._korningscache[korning_id] = (stampel, utfall)
        return utfall

    def _relativ(self, sokvag: str, run_json: Path) -> Path:
        """Relativa sökvägar i ett manifest: först från repots rot, annars från manifestets mapp."""
        for bas in (self.rot, run_json.parent):
            kandidat = (bas / sokvag).resolve()
            if kandidat.exists():
                return kandidat
        return (self.rot / sokvag).resolve()

    def kontrollera_korning(self, korning_id: str) -> None:
        b = self.bedom_korning(korning_id)
        if not b.oppen:
            raise OppenhetNekad(
                "Nekad: körningen bygger på data som inte är öppna (" + b.orsak + "). Innehåll "
                "härlett ur icke-öppna data lämnas inte ut via MCP. Inget har lämnats ut."
            )

    def oppen_korning(self, korning_id: str) -> bool:
        return self.bedom_korning(korning_id).oppen
