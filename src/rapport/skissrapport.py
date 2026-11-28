# ruff: noqa: E501
"""K33 – Rapport: skissunderlag (nivå 1) av påståenden med belägg, kartor och metodnotis (R1).

En rapport är en lista påståenden (K32) bundna till frågor ur frågekatalogen, plus kartor ritade
av Floden, körningarnas manifest, metodernas valideringsstatus och en händelselogg. Rapporten
sparas som `rapport.json` i en egen mapp under rapportmappen (standard `data/lokal/rapporter/`,
utanför git), med kartorna i `kartor/` och exporterna i `export/`.

**Nivå 1 (skissunderlag):** sållning, inte bedömning mot krav. 2–6 sidor: karta per ämne,
jämförelse av alternativ, trafikljus per fråga ("behöver fördjupas"), saknat underlag och alla
metoder med valideringsstatus. **Nivå 2 (publicerbart planunderlag, R3):** fälten finns
förberedda (`skede`, `revision`, `bedomningsgrunder`, `granskning`, `signering`) men används inte.

`RapportTjanst` är den enda vägen att skapa och ändra en rapport: MCP-verktygen, referensflödet
(`tools/rapport_demo.py`) och webben går genom den. Påståenden utan giltiga belägg vägras.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import re
import sqlite3
import struct
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from floden_mcp.dataklass import DataKlass, Policy
from floden_mcp.karna_api import FlodenFel, FlodenTjanst
from floden_mcp.korlogg import Korlogg, hash_av
from gemensamt.hash import sha256_bytes, sha256_fil
from rapport.amnestext import (
    EKOLOGI_ENHET,
    EKOLOGI_KANSLIGHET_ENHET,
    TITEL,
    bildtext_lagrad,
    tomvarde_lagrad,
)
from rapport.amnestext import kartrubrik as kartrubrik_text
from rapport.belaggskalla import (
    SKYFALL_TROSKEL_M,
    BelaggFel,
    FlodenBelaggKalla,
    Lagpunkt,
    SkyfallsTroskelytor,
)
from rapport.bestallning import (
    Bestallning,
    BestallningArkiv,
    BestallningFel,
    skapa_bestallning,
)
from rapport.fragekatalog import AMNESPREFIX, Fraga, Fragekatalog, FragekatalogFel
from rapport.installningar import (
    FILROLLER,
    InstallningsFel,
    RapportInstallningar,
    hitta_resurs,
    lokala_forval,
    tillampa,
)
from rapport.pastaende import (
    FORFATTARE,
    Pastaende,
    PastaendeFel,
    statusfel,
    textfel,
)

KONTRAKT_K33: Final[str] = "K33/v1"
STANDARD_RAPPORTMAPP: Final[str] = "data/lokal/rapporter"
AMNEN_NIVA1: Final[tuple[str, ...]] = ("skyfall", "dagsljus", "ekologi", "luft")
"""Ämnen med skissrapport: skyfall (R1), dagsljus/sol, ekologi och luft (R2). Ordning enligt Joel 2026-11-22:
skyfall, dagsljus/sol, luft, buller (buller är spärrat: Nord2000 är inte konformt)."""
_AMNESDOMAN: Final[dict[str, str]] = {
    "skyfall": "vatten",
    "dagsljus": "dagsljus",
    "ekologi": "ekologi",
    "luft": "luft",
}
_PRIMAR_KARTROLL: Final[dict[str, tuple[str, ...]]] = {
    "skyfall": ("max_djup",),
    "dagsljus": ("soltimmar",),
    "ekologi": ("stromtathet",),
}
_ID_RE: Final[re.Pattern[str]] = re.compile(r"^r-[0-9a-f]{12}$")
KART_PX: Final[int] = 560
TOMVARDE_TEXT: Final[str] = tomvarde_lagrad("skyfall")


class RapportFel(Exception):
    """Rapporten kan inte skapas eller ändras. Meddelandet är skrivet för användaren och AI:n."""


def _nu() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Mottagare:
    """En plats som påverkas (rapportstod.md): namn, polygon (EPSG:3011) och beskrivning."""

    namn: str
    polygon: tuple[tuple[float, float], ...]
    beskrivning: str

    def till_dict(self) -> dict[str, Any]:
        return {
            "namn": self.namn,
            "polygon": [list(p) for p in self.polygon],
            "beskrivning": self.beskrivning,
        }

    @classmethod
    def fran_dict(cls, d: Mapping[str, Any]) -> Mottagare:
        namn = str(d.get("namn", "")).strip()
        if not re.fullmatch(r"[a-z0-9_]{1,40}", namn):
            raise RapportFel(
                f"Mottagarens namn '{namn}' ska vara 1–40 tecken a–z, 0–9 eller '_' (det ingår i "
                "beläggens adress)."
            )
        try:
            poly = tuple((float(p[0]), float(p[1])) for p in d["polygon"])
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise RapportFel(f"Mottagaren '{namn}' saknar polygon [[e, n], ...].") from exc
        if len(poly) < 3:
            raise RapportFel(f"Mottagarens polygon '{namn}' behöver minst tre hörn.")
        return cls(namn, poly, str(d.get("beskrivning") or namn))


@dataclass(frozen=True)
class Karta:
    """En karta i rapporten, ritad av Floden (`FlodenTjanst.karta`, samma takmask som webben)."""

    kart_id: str
    korning_id: str
    jamforelse: str
    analys: str
    lager_id: str
    typ: str
    scenario: str
    fil: str
    png_sha256: str
    bildtext: str
    meta: dict[str, Any]

    def till_dict(self) -> dict[str, Any]:
        return {
            "kart_id": self.kart_id,
            "korning_id": self.korning_id,
            "jamforelse": self.jamforelse,
            "analys": self.analys,
            "lager_id": self.lager_id,
            "typ": self.typ,
            "scenario": self.scenario,
            "fil": self.fil,
            "png_sha256": self.png_sha256,
            "bildtext": self.bildtext,
            "meta": self.meta,
        }

    @classmethod
    def fran_dict(cls, d: Mapping[str, Any]) -> Karta:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Rapport:
    """K33/v1. Se `docs/DATAKONTRAKT.md`."""

    rapport_id: str
    niva: int
    amne: str
    projekt: str
    titel: str
    skapad: str
    fragor: tuple[str, ...]
    korningar: tuple[dict[str, Any], ...]
    mottagare: tuple[Mottagare, ...]
    pastaenden: tuple[Pastaende, ...]
    kartor: tuple[Karta, ...]
    metoder: tuple[dict[str, Any], ...]
    metodnotis: dict[str, Any]
    dataklass: str
    handelser: tuple[dict[str, Any], ...]
    kontroll: dict[str, Any] | None = None
    exporter: tuple[dict[str, Any], ...] = ()
    katalog: dict[str, Any] = field(default_factory=dict)
    # nivå 2 (publicerbart planunderlag, R3): förberett, används inte i nivå 1
    skede: str = "planstart"
    revision: int = 1
    bedomningsgrunder: tuple[dict[str, Any], ...] = ()
    granskning: dict[str, Any] | None = None
    signering: dict[str, Any] | None = None
    installningar: RapportInstallningar = field(default_factory=RapportInstallningar)
    """Språk och layout (valfritt i K33/v1; saknas fältet gäller förvalen)."""
    bestallning: Bestallning | None = None
    """Beställningen (K30) som rapporten bygger på (valfritt i K33/v1, tillagt i R2)."""
    kontrakt: str = KONTRAKT_K33

    def till_dict(self) -> dict[str, Any]:
        return {
            "kontrakt": self.kontrakt,
            "rapport_id": self.rapport_id,
            "niva": self.niva,
            "amne": self.amne,
            "projekt": self.projekt,
            "titel": self.titel,
            "skapad": self.skapad,
            "fragor": list(self.fragor),
            "korningar": [dict(k) for k in self.korningar],
            "mottagare": [m.till_dict() for m in self.mottagare],
            "pastaenden": [p.till_dict() for p in self.pastaenden],
            "kartor": [k.till_dict() for k in self.kartor],
            "metoder": [dict(m) for m in self.metoder],
            "metodnotis": dict(self.metodnotis),
            "dataklass": self.dataklass,
            "handelser": [dict(h) for h in self.handelser],
            "kontroll": self.kontroll,
            "exporter": [dict(e) for e in self.exporter],
            "katalog": dict(self.katalog),
            "skede": self.skede,
            "revision": self.revision,
            "bedomningsgrunder": [dict(b) for b in self.bedomningsgrunder],
            "granskning": self.granskning,
            "signering": self.signering,
            "installningar": self.installningar.till_dict(),
            "bestallning": None if self.bestallning is None else self.bestallning.till_dict(),
        }

    @classmethod
    def fran_dict(cls, d: Mapping[str, Any]) -> Rapport:
        if d.get("kontrakt") != KONTRAKT_K33:
            raise RapportFel(f"Rapporten har kontraktet {d.get('kontrakt')!r}, väntade K33/v1.")
        try:
            return cls(
                rapport_id=str(d["rapport_id"]),
                niva=int(d["niva"]),
                amne=str(d["amne"]),
                projekt=str(d["projekt"]),
                titel=str(d["titel"]),
                skapad=str(d["skapad"]),
                fragor=tuple(str(f) for f in d["fragor"]),
                korningar=tuple(dict(k) for k in d["korningar"]),
                mottagare=tuple(Mottagare.fran_dict(m) for m in d.get("mottagare", [])),
                pastaenden=tuple(Pastaende.fran_dict(p) for p in d.get("pastaenden", [])),
                kartor=tuple(Karta.fran_dict(k) for k in d.get("kartor", [])),
                metoder=tuple(dict(m) for m in d.get("metoder", [])),
                metodnotis=dict(d["metodnotis"]),
                dataklass=str(d["dataklass"]),
                handelser=tuple(dict(h) for h in d.get("handelser", [])),
                kontroll=d.get("kontroll"),
                exporter=tuple(dict(e) for e in d.get("exporter", [])),
                katalog=dict(d.get("katalog", {})),
                skede=str(d.get("skede", "planstart")),
                revision=int(d.get("revision", 1)),
                bedomningsgrunder=tuple(dict(b) for b in d.get("bedomningsgrunder", [])),
                granskning=d.get("granskning"),
                signering=d.get("signering"),
                installningar=RapportInstallningar.fran_dict(d.get("installningar")),
                bestallning=(
                    None if not d.get("bestallning") else Bestallning.fran_dict(d["bestallning"])
                ),
            )
        except (KeyError, TypeError, ValueError, BestallningFel) as exc:
            raise RapportFel(f"rapport.json är ofullständig: {exc}.") from exc

    @property
    def mottagare_per_namn(self) -> dict[str, Mottagare]:
        return {m.namn: m for m in self.mottagare}

    def med(self, **andringar: Any) -> Rapport:
        return replace(self, **andringar)


_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


def ar_ra_uuid(varde: str) -> bool:
    """Sant om strängen är ett rått 32-teckens hex-id eller standard-UUID."""
    return bool(_UUID_RE.fullmatch(str(varde).strip()))


@dataclass(frozen=True)
class ExtraFigur:
    """Tilläggsfigur (detaljkarta eller tvärsektion) ritad deterministiskt ur körningens data."""

    fil: str
    bildtext: str
    rubrik: str
    typ: str  # "detalj" | "sektion"
    sha256: str


@dataclass(frozen=True)
class PlanerarFigurer:
    """Berikade kartor, detaljzoom, tvärsektion och skyfallsmått för planerarvänlig export."""

    kart_filer: dict[str, str] = field(default_factory=dict)
    detaljkartor: tuple[ExtraFigur, ...] = ()
    sektioner: tuple[ExtraFigur, ...] = ()
    lagpunkter: tuple[Lagpunkt, ...] = ()
    trosklar_fore: SkyfallsTroskelytor | None = None
    trosklar_efter: SkyfallsTroskelytor | None = None
    troskel_analys: str = ""


def metodnotis_text(
    notis: Mapping[str, Any],
    *,
    sbk: bool = False,
    korningar: Sequence[str] = (),
    bilaga: str = "bilaga",
) -> str:
    """Metodnotisen som den står i rapporten (saklig, ärlig metodnotis utan AI-jargong)."""
    del korningar  # körnings-slugs redovisas i den tekniska spårbarhetsbilagan
    modeller = [m for m in notis.get("modeller", []) if m]
    ansvarig = notis.get("ansvarig_utredare")
    if sbk:
        bas = (
            "Underlaget är framtaget med Floden (deterministiska fysikberäkningar). "
            "Metodstatus: skiss — avsett för tidig sållning och alternativjämförelse, "
            "inte formell kravprövning. Alla tal och kartor kommer direkt ur beräkningarna "
            "och har kontrollerats automatiskt mot dem."
        )
        if notis.get("ai_stod", True):
            mod = f" ({', '.join(modeller)})" if modeller else ""
            bas += f" Textutkast och urval har sammanställts med AI-stöd{mod}."
        else:
            bas += " Texten följer fasta formuleringar utan AI-stöd."
        del bilaga
        bas += (
            " Fullständig spårbarhet (belägg, körlogg, manifest och kontroll) redovisas i "
            "teknisk bilaga sist i rapporten."
        )
        if ansvarig:
            bas += f" Ansvarig utredare: {ansvarig}."
        return bas
    if notis.get("ai_stod", True):
        mod = f" ({', '.join(modeller)})" if modeller else ""
        bas = (
            "Underlaget har tagits fram med Floden (deterministiska fysikberäkningar) och "
            f"AI-stöd{mod}. Textutkast har sammanställts med AI-stöd; alla tal och kartor "
            "kommer ur Flodens körningar och har kontrollerats automatiskt mot dem."
        )
    else:
        bas = (
            "Underlaget har tagits fram med Floden utan AI-stöd (referensflöde med fasta "
            "formuleringar). Alla tal och kartor kommer ur Flodens körningar och har "
            "kontrollerats automatiskt mot dem."
        )
    bas += " Fullständig spårbarhet och körlogg finns i den tekniska bilagan."
    bas += (
        f" Ansvarig utredare: {ansvarig}."
        if ansvarig
        else " Ansvarig utredare anges inte (frivilligt i ett skissunderlag)."
    )
    return bas


class RapportArkiv:
    """Rapporterna på disk: `<rapportmapp>/<rapport_id>/rapport.json`, `kartor/`, `export/`."""

    def __init__(self, mapp: str | Path) -> None:
        self.mapp = Path(mapp)
        self._lock = threading.Lock()

    def rapportmapp(self, rapport_id: str) -> Path:
        if not _ID_RE.match(rapport_id):
            raise RapportFel(f"Ogiltigt rapport-id '{rapport_id}'.")
        return self.mapp / rapport_id

    def las(self, rapport_id: str) -> Rapport:
        fil = self.rapportmapp(rapport_id) / "rapport.json"
        if not fil.is_file():
            raise RapportFel(f"Rapporten '{rapport_id}' finns inte.")
        return Rapport.fran_dict(json.loads(fil.read_text(encoding="utf-8")))

    def spara(self, rapport: Rapport) -> Path:
        mapp = self.rapportmapp(rapport.rapport_id)
        mapp.mkdir(parents=True, exist_ok=True)
        fil = mapp / "rapport.json"
        tmp = fil.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(
                json.dumps(rapport.till_dict(), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, fil)
        return fil

    def lista(self, projekt: str | None = None) -> list[Rapport]:
        if not self.mapp.is_dir():
            return []
        ut: list[Rapport] = []
        for d in sorted(self.mapp.iterdir()):
            if not (d / "rapport.json").is_file() or not _ID_RE.match(d.name):
                continue
            try:
                r = self.las(d.name)
            except (RapportFel, ValueError):
                continue
            if projekt is None or r.projekt == projekt:
                ut.append(r)
        ut.sort(key=lambda r: (r.skapad, r.rapport_id), reverse=True)
        return ut

    def exportfil(self, rapport: Rapport, fmt: str) -> Path | None:
        """Senaste exporterade filen i formatet (`word` eller `pdf`), om den finns och är oförändrad."""
        for e in reversed(rapport.exporter):
            if e.get("format") == fmt:
                fil = self.rapportmapp(rapport.rapport_id) / str(e["fil"])
                if fil.is_file() and sha256_fil(fil) == e.get("sha256"):
                    return fil
                return None
        return None


def sammanfattning(rapport: Rapport, arkiv: RapportArkiv | None = None) -> dict[str, Any]:
    """Kort om en rapport: för listor (MCP, webben)."""
    k = rapport.kontroll or {}
    filer = {}
    if arkiv is not None:
        for fmt in ("word", "pdf"):
            f = arkiv.exportfil(rapport, fmt)
            filer[fmt] = None if f is None else f.name
    return {
        "rapport_id": rapport.rapport_id,
        "titel": rapport.titel,
        "projekt": rapport.projekt,
        "amne": rapport.amne,
        "niva": rapport.niva,
        "skapad": rapport.skapad,
        "antal_pastaenden": len(rapport.pastaenden),
        "antal_fragor": len(rapport.fragor),
        "korningar": [kk["korning_id"] for kk in rapport.korningar],
        "kontroll": (
            None
            if not k
            else {
                "godkand": bool(k.get("godkand")),
                "tid": k.get("tid"),
                "antal_fel": len([f for p in k.get("punkter", []) for f in p.get("fel", [])]),
            }
        ),
        "filer": filer,
        "dataklass": rapport.dataklass,
    }


@dataclass(frozen=True)
class _ProjektOppenPolicy(Policy):
    """Policy som slår upp projektets K38-öppenhet (`projekt_oppen`) när projektet saknar explicit projektklass."""

    projekt_oppen: Callable[[str], bool | None] | None = None

    def projektklass(self, projekt_id: str) -> DataKlass:
        if projekt_id in self.projektklasser:
            return self.projektklasser[projekt_id]
        if self.projekt_oppen is not None:
            oppet = self.projekt_oppen(projekt_id)
            if oppet is not None:
                return DataKlass.OPPET if oppet else DataKlass.INTERNT
        return super().projektklass(projekt_id)


class RapportTjanst:
    """Skapa rapporter, lägg till påståenden, kontrollera och exportera (enda vägen in)."""

    def __init__(
        self,
        tjanst: FlodenTjanst,
        *,
        rapportmapp: str | Path | None = None,
        policy: Policy | None = None,
        projekt_oppen: Callable[[str], bool | None] | None = None,
        katalog: Fragekatalog | None = None,
        korlogg: Korlogg | None = None,
        lokala_forval: Mapping[str, Any] | None = None,
        tillatna_resursrotter: Sequence[str | Path] | None = None,
    ) -> None:
        """`lokala_forval`: inställningar som gäller när en rapport skapas (utöver förvalen).
        Utelämnat läses `[rapport]` i `config.local.toml` (och `config.toml`) i Flodens rot."""
        self.tjanst = tjanst
        self._lokala_forval = None if lokala_forval is None else dict(lokala_forval)
        self._tillatna_resursrotter = (
            None
            if tillatna_resursrotter is None
            else tuple(Path(p) for p in tillatna_resursrotter)
        )
        mapp = Path(rapportmapp) if rapportmapp is not None else _rapportmapp_for(tjanst)
        self.arkiv = RapportArkiv(mapp)
        self.bestallningar = BestallningArkiv(mapp / "bestallningar")
        bas_policy = policy or Policy()
        oppen_fn = projekt_oppen if projekt_oppen is not None else getattr(tjanst, "projekt_oppen", None)
        if callable(oppen_fn) and not isinstance(bas_policy, _ProjektOppenPolicy):
            self.policy: Policy = _ProjektOppenPolicy(
                standard=bas_policy.standard,
                per_klient=bas_policy.per_klient,
                projektklasser=bas_policy.projektklasser,
                projekt_oppen=oppen_fn,
            )
        else:
            self.policy = bas_policy
        self._katalog = katalog
        self.korlogg = korlogg
        self.kalla = FlodenBelaggKalla(tjanst)

    @property
    def lokala_forval(self) -> dict[str, Any]:
        if self._lokala_forval is None:
            try:
                self._lokala_forval = lokala_forval(self.tjanst.rot)
            except InstallningsFel as exc:
                raise RapportFel(str(exc)) from exc
        return self._lokala_forval

    def resursmapp(self, rapport_id: str) -> Path:
        return self.arkiv.rapportmapp(rapport_id) / "resurser"

    def resursfiler(self, r: Rapport) -> dict[str, Path]:
        """Logotyp och typsnitt för exporten: rapportens kopior (kontrollsumman stämmer)."""
        ut: dict[str, Path] = {}
        for roll, res in r.installningar.resurser().items():
            fil = hitta_resurs(res, self.resursmapp(r.rapport_id), self.lokala_forval)
            if fil is None:
                raise RapportFel(
                    f"Filen för {FILROLLER[roll]} (sha256 {res.sha256[:12]}) finns varken i "
                    "rapportens mapp eller i config.local.toml. Ange filen igen med "
                    "rapport_installningar."
                )
            ut[roll] = fil
        return ut

    @property
    def katalog(self) -> Fragekatalog:
        if self._katalog is None:
            fil = self.tjanst.rot / "docs" / "utredningar-stockholm" / "fragor.json"
            self._katalog = Fragekatalog.las_alla(fil) if fil.is_file() else Fragekatalog.las_alla()
        return self._katalog

    # ------------------------------------------------------------------ skapa

    def projektnamn(self, projekt: str) -> str:
        """Läsbart projektnamn ur ``projekt.json`` (visar aldrig en rå 32-teckens UUID)."""
        if projekt == "demokvarteret":
            return "Demokvarteret"
        fragor = getattr(self.tjanst, "fragor", None)
        app = getattr(self.tjanst, "app", None)
        rotter: list[Path] = []
        for kand in (
            getattr(fragor, "projektrot", None) if fragor is not None else None,
            getattr(app, "projektmapp", None) if app is not None else None,
        ):
            if kand is not None:
                p = Path(kand)
                if p.is_dir() and p not in rotter:
                    rotter.append(p)
        for proj_rot in rotter:
            for fil in sorted(proj_rot.glob("*/projekt.json")):
                try:
                    d = json.loads(fil.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(d, dict):
                    continue
                pid = d.get("register_id") or d.get("projekt_id")
                if pid == projekt or d.get("projekt_id") == projekt or fil.parent.name == projekt:
                    namn = str(d.get("namn") or "").strip()
                    if namn and not ar_ra_uuid(namn):
                        return namn
                    slug = str(d.get("projekt_id") or fil.parent.name).strip()
                    if slug and not ar_ra_uuid(slug):
                        return slug.replace("-", " ").replace("_", " ").strip().capitalize()
        if ar_ra_uuid(projekt):
            return "Planprojekt"
        return projekt

    def _hitta_geografi_manifest_for_korning(
        self, korning_id: str, projekt: str | None = None
    ) -> tuple[Path | None, tuple[float, float, float, float] | None]:
        """Löser upp projektets K46-geografimanifest och ev. avgränsning (EPSG:3011) för en körning."""
        fragor = getattr(self.tjanst, "fragor", None)
        app = getattr(self.tjanst, "app", None)
        proj_rotter: list[Path] = []
        for kand in (
            getattr(fragor, "projektrot", None) if fragor is not None else None,
            getattr(app, "projektmapp", None) if app is not None else None,
        ):
            if kand is not None:
                p = Path(kand)
                if p.is_dir() and p not in proj_rotter:
                    proj_rotter.append(p)
        if not proj_rotter:
            return None, None

        geo_rotter: list[Path] = []
        for gk in (
            getattr(app, "geomapp", None) if app is not None else None,
            getattr(getattr(app, "_projektskapa", None), "geomapp", None) if app is not None else None,
        ):
            if gk is not None:
                gp = Path(gk)
                if gp.is_dir() and gp not in geo_rotter:
                    geo_rotter.append(gp)

        projekt_id: str | None = None
        if fragor is not None and hasattr(fragor, "korning_eller_none"):
            try:
                k_obj = fragor.korning_eller_none(korning_id)
                if k_obj is not None and getattr(k_obj, "projekt_id", None):
                    projekt_id = str(k_obj.projekt_id)
            except Exception:  # noqa: BLE001
                projekt_id = None
        if not projekt_id:
            projekt_id = projekt
        if not projekt_id:
            return None, None

        for proj_rot in proj_rotter:
            for fil in sorted(proj_rot.glob("*/projekt.json")):
                try:
                    d = json.loads(fil.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(d, dict):
                    continue
                pid = d.get("register_id") or d.get("projekt_id")
                if pid != projekt_id and d.get("projekt_id") != projekt_id and fil.parent.name != projekt_id:
                    continue
                avgr: tuple[float, float, float, float] | None = None
                avgr_ra = d.get("avgransning_3011")
                if isinstance(avgr_ra, list) and len(avgr_ra) == 4:
                    try:
                        vals = tuple(float(v) for v in avgr_ra)
                        if all(math.isfinite(v) for v in vals) and vals[0] < vals[2] and vals[1] < vals[3]:
                            avgr = (vals[0], vals[1], vals[2], vals[3])
                    except (TypeError, ValueError):
                        avgr = None
                mf_expl = d.get("geografi_manifest")
                if isinstance(mf_expl, str) and mf_expl.strip():
                    mf_p = Path(mf_expl.strip())
                    if mf_p.is_file():
                        return mf_p, avgr
                ref = d.get("geografi")
                if isinstance(ref, str) and "@" in ref:
                    gid, ver = ref.split("@", 1)
                    kand_geo = [*geo_rotter, proj_rot.parent / "geo"]
                    for gr in kand_geo:
                        mf_p = gr / gid / ver / "manifest.json"
                        if mf_p.is_file():
                            return mf_p, avgr
                return None, avgr
        return None, None

    def _forbered_ekologi_skrivbord(
        self, korning_id: str, utmapp: Path, *, projekt: str | None = None
    ) -> None:
        """Förser ekologikörningens utmapp med SHA-256-verifierade skrivbordslager ur projektets geografi."""
        from geografi.ekologi_skrivbord import (
            ARTLISTA_FIL,
            FORMAT_SKYDDAD_NATUR,
            SKRIVBORD_SAMMANFATTNING_FIL,
            las_ekologi_skrivbord,
        )
        from geografi.modell import GeografiFel, lager_mapp

        if not utmapp.is_dir():
            return
        skrivbord_mapp = utmapp / "ekologi_skrivbord"
        skydd_mal = skrivbord_mapp / SKRIVBORD_SAMMANFATTNING_FIL
        art_mal = skrivbord_mapp / ARTLISTA_FIL
        if skydd_mal.is_file() and art_mal.is_file():
            return

        manifest_fil, avgr = self._hitta_geografi_manifest_for_korning(korning_id, projekt=projekt)
        if manifest_fil is None:
            return
        try:
            underlag = las_ekologi_skrivbord(manifest_fil, karna_3011=avgr)
        except (GeografiFel, OSError, ValueError):
            return
        if underlag.skyddad_natur_sha256 is None and underlag.artfynd_sha256 is None:
            return

        geo_rot = manifest_fil.resolve().parents[2]
        try:
            skrivbord_mapp.mkdir(parents=True, exist_ok=True)
            if underlag.skyddad_natur_sha256 is not None and not skydd_mal.is_file():
                lagrad_fil = lager_mapp(geo_rot, underlag.skyddad_natur_sha256) / SKRIVBORD_SAMMANFATTNING_FIL
                bas_dok: dict[str, Any] = {}
                if lagrad_fil.is_file():
                    ra = json.loads(lagrad_fil.read_text(encoding="utf-8"))
                    if isinstance(ra, dict):
                        bas_dok = dict(ra)
                sam_dok: dict[str, Any] = {
                    **bas_dok,
                    "format": FORMAT_SKYDDAD_NATUR,
                    "epsg": 3011,
                    "buffert_m": float(bas_dok.get("buffert_m", 1000.0)),
                    "skyddade_omraden": underlag.sammanfattning.get("skyddade_omraden") or {},
                    "skogliga_varden": underlag.sammanfattning.get("skogliga_varden") or {},
                    "vmi": underlag.sammanfattning.get("vmi") or {},
                    "geografi": underlag.ref,
                    "manifest_sha256": underlag.manifest_sha256,
                    "lager_sha256": underlag.skyddad_natur_sha256,
                }
                skydd_mal.write_text(
                    json.dumps(sam_dok, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            if underlag.artfynd_sha256 is not None and underlag.artlista is not None and not art_mal.is_file():
                art_mal.write_text(
                    json.dumps(underlag.artlista, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        except OSError:
            return

    def _geografi_koordinater(
        self, korning_id: str, projekt: str | None = None
    ) -> dict[str, Any] | None:
        """Läser geografimanifestets `koordinater` (publicering_epsg, skalfel) för projektet om tillgängligt."""
        fragor = getattr(self.tjanst, "fragor", None)
        app = getattr(self.tjanst, "app", None)
        projektrot = getattr(fragor, "projektrot", None) if fragor is not None else None
        geomapp = getattr(app, "geomapp", None) if app is not None else None
        if projektrot is None or geomapp is None:
            return None
        proj_rot = Path(projektrot)
        geo_rot = Path(geomapp)
        if not proj_rot.is_dir() or not geo_rot.is_dir():
            return None
        projekt_id: str | None = None
        if fragor is not None and hasattr(fragor, "korning_eller_none"):
            try:
                k_obj = fragor.korning_eller_none(korning_id)
                if k_obj is not None and getattr(k_obj, "projekt_id", None):
                    projekt_id = str(k_obj.projekt_id)
            except Exception:  # noqa: BLE001
                projekt_id = None
        if not projekt_id:
            projekt_id = projekt
        if not projekt_id:
            return None
        for fil in sorted(proj_rot.glob("*/projekt.json")):
            try:
                d = json.loads(fil.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict):
                continue
            pid = d.get("register_id") or d.get("projekt_id")
            if pid != projekt_id and fil.parent.name != projekt_id:
                continue
            ref = d.get("geografi")
            if not isinstance(ref, str) or not ref:
                return None
            if "@" not in ref:
                return None
            gid, ver = ref.split("@", 1)
            manifest_fil = geo_rot / gid / ver / "manifest.json"
            if not manifest_fil.is_file():
                return None
            try:
                g_manifest = json.loads(manifest_fil.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            koord = g_manifest.get("koordinater") if isinstance(g_manifest, dict) else None
            if isinstance(koord, Mapping) and koord.get("publicering_epsg") is not None:
                return dict(koord)
            return None
        return None

    def _korningspost(
        self,
        korning_id: str,
        amne: str,
        scenario: Mapping[str, Any] | None = None,
        *,
        projekt: str | None = None,
    ) -> dict[str, Any]:
        try:
            status = self.tjanst.korning_status(korning_id)
        except FlodenFel as exc:
            raise RapportFel(str(exc)) from exc
        if status["status"] != "klar":
            raise RapportFel(
                f"Körningen '{korning_id}' är inte klar (status {status['status']}); vänta med "
                "vanta innan rapporten skapas."
            )
        res = self.kalla._resultat(korning_id)  # noqa: SLF001
        doman = _AMNESDOMAN[amne]
        if amne == "luft":
            from rapport.luftbelagg import LUFT_LOSARE

            analyser = [t for t in res["tabeller"] if str(t["losare"]) == LUFT_LOSARE]
            if not analyser:
                raise RapportFel(
                    f"Körningen '{korning_id}' har ingen analys med lösaren {LUFT_LOSARE}. Skissunderlaget "
                    "för luft bygger på MKN-intervallet för PM10 ur en given timserie och en angiven bakgrund "
                    "(en analys per serie, före och efter som två analyser)."
                )
            if scenario is not None:
                raise RapportFel(
                    "Luftunderlaget har före och efter som två analyser (en serie var), inte som scenarier; "
                    "ange inget scenario i beställningen."
                )
        else:
            analyser = [t for t in res["tabeller"] if str(t["losare"]).startswith(doman + ".")]
        if not analyser:
            raise RapportFel(
                f"Körningen '{korning_id}' har ingen {doman}analys och kan inte ingå i en "
                f"rapport om {amne}."
            )
        utmapp = self.kalla.utmapp(korning_id)
        manifest = json.loads((utmapp / "run.json").read_text(encoding="utf-8"))
        jamforelser = [
            {k: j[k] for k in ("namn", "analys", "utdata", "fore", "efter", "troskel")}
            for j in res["jamforelser"]
            if any(j["analys"] == t["analys"] for t in analyser)
        ]
        if scenario is not None and amne != "luft":
            jamforelser = [
                j
                for j in jamforelser
                if j["fore"] == scenario["fore"] and j["efter"] in scenario["efter"]
            ]
            if not jamforelser:
                raise RapportFel(
                    f"Körningen '{korning_id}' har ingen jämförelse av "
                    f"{', '.join(scenario['efter'])} mot {scenario['fore']} (beställningens "
                    "scenario). Jämförelser anges i frågan ([[jamforelser]], K20)."
                )
        koord = self._geografi_koordinater(korning_id, projekt=projekt)
        antaganden = _antaganden(res, [t["analys"] for t in analyser], koordinater=koord)
        if amne == "ekologi":
            from rapport.ekologibelagg import ekologiantaganden, skrivbord_antaganden
            from rapport.gyf_utci_belagg import gyf_antaganden

            self._forbered_ekologi_skrivbord(korning_id, utmapp, projekt=projekt)
            antaganden = ekologiantaganden(
                self.kalla,
                korning_id,
                {
                    t["analys"]: sorted({r["scenario"] for r in t["rader"]})
                    for t in analyser
                    if t["losare"] == "ekologi.konnektivitet"
                },
            )
            antaganden = list(
                dict.fromkeys([*antaganden, *gyf_antaganden(utmapp), *skrivbord_antaganden(utmapp)])
            )
            skalnot = _skalfelsantagande(koord)
            if skalnot and skalnot not in antaganden:
                antaganden = [*antaganden, skalnot]
        if amne == "dagsljus":
            from rapport.gyf_utci_belagg import utci_antaganden

            utci_ant = utci_antaganden(utmapp)
            if utci_ant:
                antaganden = list(dict.fromkeys([*antaganden, *utci_ant]))
        if amne == "luft":
            from rapport.luftbelagg import luftantaganden

            antaganden = luftantaganden(
                self.kalla,
                korning_id,
                {t["analys"]: sorted({r["scenario"] for r in t["rader"]}) for t in analyser},
            )
            skalnot = _skalfelsantagande(koord)
            if skalnot and skalnot not in antaganden:
                antaganden = [*antaganden, skalnot]
        return {
            "korning_id": korning_id,
            "fraga_id": res["fraga_id"],
            "beskrivning": res["beskrivning"],
            "kodversion": manifest.get("kodversion"),
            "manifest": "run.json",
            "manifest_sha256": sha256_fil(utmapp / "run.json"),
            "start": manifest.get("start"),
            "analyser": [
                {
                    "analys": t["analys"],
                    "losare": t["losare"],
                    "scenarier": sorted({r["scenario"] for r in t["rader"]}),
                }
                for t in analyser
            ],
            "jamforelser": jamforelser,
            "antaganden": antaganden,
            "scenarier": [s["id"] for s in res["scenarier"]],
        }

    def _metoder(
        self, korningar: Sequence[Mapping[str, Any]], *, amne: str | None = None
    ) -> list[dict[str, Any]]:
        from rapport.gyf_utci_belagg import (
            GYF_LOSARE,
            UTCI_LOSARE,
            hitta_gyf_scenarier,
            hitta_utci_scenarier,
        )

        losare: dict[str, dict[str, Any]] = {}
        alla = {m["namn"]: m for m in self.tjanst.metoder_lista()["metoder"]}
        for k in korningar:
            for a in k["analyser"]:
                n = a["losare"]
                if n in losare:
                    continue
                info = alla.get(n, {})
                st = self.tjanst.metodstatus(n)
                losare[n] = {
                    "losare": n,
                    "visningsnamn": info.get("visningsnamn", n),
                    "version": info.get("version"),
                    "status": st["status"],
                    "underlag": st["underlag"],
                    "anmarkning": st.get("anmarkning") or st.get("motivering"),
                }
            try:
                utmapp = self.kalla.utmapp(str(k["korning_id"]))
            except BelaggFel:
                continue
            if amne in (None, "ekologi") and GYF_LOSARE not in losare and hitta_gyf_scenarier(utmapp):
                st = self.tjanst.metodstatus(GYF_LOSARE)
                losare[GYF_LOSARE] = {
                    "losare": GYF_LOSARE,
                    "visningsnamn": "Grönytefaktor (GYF)",
                    "version": "v1-skiss",
                    "status": st["status"],
                    "underlag": st["underlag"],
                    "anmarkning": st.get("anmarkning") or st.get("motivering"),
                }
            if amne in (None, "dagsljus") and UTCI_LOSARE not in losare and hitta_utci_scenarier(utmapp):
                st = self.tjanst.metodstatus(UTCI_LOSARE)
                losare[UTCI_LOSARE] = {
                    "losare": UTCI_LOSARE,
                    "visningsnamn": "Termiskt mikroklimat (UTCI)",
                    "version": "v1-skiss",
                    "status": st["status"],
                    "underlag": st["underlag"],
                    "anmarkning": st.get("anmarkning") or st.get("motivering"),
                }
        return list(losare.values())

    def _kartor(
        self, rapport_id: str, amne: str, korningar: Sequence[Mapping[str, Any]]
    ) -> list[Karta]:
        mapp = self.arkiv.rapportmapp(rapport_id) / "kartor"
        mapp.mkdir(parents=True, exist_ok=True)
        ut: list[Karta] = []
        gjorda_bas: set[tuple[Any, ...]] = set()
        sedda_id: set[str] = set()
        for k in korningar:
            res = self.kalla._resultat(k["korning_id"])  # noqa: SLF001
            losare_per_analys = {a["analys"]: a["losare"] for a in k["analyser"]}
            if k["jamforelser"]:
                for j in k["jamforelser"]:
                    utsnitt = kartutsnitt(res, j)
                    for typ, scen in (("bas", j["fore"]), ("scenario", j["efter"]), ("skillnad", j["efter"])):
                        if typ == "bas":
                            # Före-kartan är densamma för alla alternativ med samma utdataroll och utsnitt: en bild.
                            nyckel = (
                                k["korning_id"],
                                j["analys"],
                                j["utdata"],
                                j["fore"],
                                tuple(utsnitt or ()),
                            )
                            if nyckel in gjorda_bas:
                                continue
                            gjorda_bas.add(nyckel)
                        rubrik = kartrubrik_text(
                            amne, typ, scen, j["fore"], j["efter"], roll=str(j.get("utdata") or "")
                        )
                        try:
                            png, meta = self.tjanst.karta(
                                k["korning_id"],
                                j["analys"],
                                typ=typ,
                                scenario=scen,
                                roll=j["utdata"],
                                utsnitt=utsnitt,
                                max_px=KART_PX,
                                rubrik=rubrik,
                            )
                        except FlodenFel as exc:
                            raise RapportFel(f"Kartan kunde inte ritas: {exc}") from exc
                        kart_id = f"{j['namn']}.{typ}"
                        if kart_id in sedda_id:
                            kart_id = f"{j['namn']}_{k['korning_id']}.{typ}"
                        sedda_id.add(kart_id)
                        fil = f"kartor/{kart_id}.png"
                        (mapp / f"{kart_id}.png").write_bytes(png)
                        ut.append(
                            Karta(
                                kart_id=kart_id,
                                korning_id=str(k["korning_id"]),
                                jamforelse=str(j["namn"]),
                                analys=str(j["analys"]),
                                lager_id=str(meta["lager_id"]),
                                typ=typ,
                                scenario=str(meta["scenario"]),
                                fil=fil,
                                png_sha256=str(meta["png_sha256"]),
                                bildtext=bildtext_lagrad(
                                    amne,
                                    typ,
                                    j,
                                    meta,
                                    losare_per_analys.get(j["analys"], ""),
                                    str(k["korning_id"]),
                                    roll=str(j.get("utdata") or meta.get("roll") or ""),
                                ),
                                meta={
                                    key: meta[key]
                                    for key in (
                                        "norr",
                                        "skalstock_m",
                                        "enhet",
                                        "skala",
                                        "cellstorlek_m",
                                        "kartutsnitt",
                                        "metodstatus",
                                        "roll",
                                    )
                                }
                                | {key: meta[key] for key in ("kartmotor", "figurstil") if key in meta}
                                | (
                                    {
                                        "enhet": (
                                            EKOLOGI_KANSLIGHET_ENHET
                                            if str(j.get("utdata") or meta.get("roll") or "")
                                            == "kansighet"
                                            else EKOLOGI_ENHET
                                        )
                                    }
                                    if amne == "ekologi"
                                    else {}
                                )
                                | {
                                    "utsnitt": utsnitt,
                                    "rubrik": rubrik,
                                },
                            )
                        )
                continue

            # Läget idag (inga jämförelser): rendera nulägeskartor för ämnets primära resultatraster
            # så att nulägesrapporter alltid har kartfigurer enligt t.ex. SK-12 / SO-15.
            tillatna_roller = _PRIMAR_KARTROLL.get(amne)
            if tillatna_roller is None:
                continue
            kandidatlager = [
                la
                for la in res.get("lager", [])
                if la.get("analys") in losare_per_analys
                and la.get("typ") != "skillnad"
                and la.get("primar")
                and la.get("roll") in tillatna_roller
            ]
            baslager = [la for la in kandidatlager if la.get("typ") == "bas"]
            valda_lager = baslager or kandidatlager
            for la in valda_lager:
                analys = str(la["analys"])
                roll = str(la["roll"])
                scen = str(la["scenario"])
                typ = str(la["typ"])
                j_bas = {
                    "namn": f"{analys}_{roll}_{scen}",
                    "analys": analys,
                    "utdata": roll,
                    "fore": scen,
                    "efter": scen,
                }
                utsnitt = kartutsnitt(res, j_bas)
                nyckel = (k["korning_id"], analys, roll, scen, tuple(utsnitt or ()))
                if nyckel in gjorda_bas:
                    continue
                gjorda_bas.add(nyckel)
                rubrik = _nulagesrubrik(amne)
                try:
                    png, meta = self.tjanst.karta(
                        k["korning_id"],
                        analys,
                        typ=typ,
                        scenario=scen,
                        roll=roll,
                        utsnitt=utsnitt,
                        max_px=KART_PX,
                        rubrik=rubrik,
                    )
                except FlodenFel as exc:
                    raise RapportFel(f"Kartan kunde inte ritas: {exc}") from exc
                kart_id = f"{j_bas['namn']}.{typ}"
                if kart_id in sedda_id:
                    kart_id = f"{j_bas['namn']}_{k['korning_id']}.{typ}"
                sedda_id.add(kart_id)
                fil = f"kartor/{kart_id}.png"
                (mapp / f"{kart_id}.png").write_bytes(png)
                ut.append(
                    Karta(
                        kart_id=kart_id,
                        korning_id=str(k["korning_id"]),
                        jamforelse="",
                        analys=analys,
                        lager_id=str(meta["lager_id"]),
                        typ=typ,
                        scenario=str(meta["scenario"]),
                        fil=fil,
                        png_sha256=str(meta["png_sha256"]),
                        bildtext=bildtext_lagrad(
                            amne,
                            "bas",
                            j_bas,
                            meta,
                            losare_per_analys.get(analys, ""),
                            str(k["korning_id"]),
                        ),
                        meta={
                            key: meta[key]
                            for key in (
                                "norr",
                                "skalstock_m",
                                "enhet",
                                "skala",
                                "cellstorlek_m",
                                "kartutsnitt",
                                "metodstatus",
                                "roll",
                            )
                        }
                        | {key: meta[key] for key in ("kartmotor", "figurstil") if key in meta}
                        | ({"enhet": EKOLOGI_ENHET} if amne == "ekologi" else {})
                        | {
                            "utsnitt": utsnitt,
                            "rubrik": rubrik,
                        },
                    )
                )
        return ut

    def skapa(
        self,
        *,
        projekt: str,
        amne: str | None = None,
        korningar: Sequence[str],
        niva: int = 1,
        titel: str | None = None,
        fragor: Sequence[str] | None = None,
        villkor: Sequence[str] | None = None,
        mottagare: Sequence[Mapping[str, Any]] = (),
        ai_stod: bool = True,
        modell: str | None = None,
        ansvarig_utredare: str | None = None,
        av: str | None = None,
        installningar: Mapping[str, Any] | None = None,
        bestallning: Bestallning | str | None = None,
    ) -> Rapport:
        """Skapar rapporten. `installningar` (se `rapport.installningar.NYCKLAR`) läggs ovanpå
        förvalen och `[rapport]` i config.local.toml.

        `bestallning` (K30, objekt eller id) styr ämne, frågor, scenario och ingress; då anges
        varken `amne` eller `fragor` (de kommer ur beställningen)."""
        best: Bestallning | None = None
        if bestallning is not None:
            try:
                best = (
                    self.bestallningar.las(bestallning)
                    if isinstance(bestallning, str)
                    else bestallning
                )
            except BestallningFel as exc:
                raise RapportFel(str(exc)) from exc
            if best.skede != "skiss":
                raise RapportFel(
                    "Beställningen gäller skedet 'planunderlag' (nivå 2: bedömning mot "
                    "bedömningsgrunder, granskning och signering). Det kommer i R3; än finns "
                    "bara skissunderlag (skede 'skiss')."
                )
            if best.projekt != projekt:
                raise RapportFel(
                    f"Beställningen gäller projektet '{best.projekt}', inte '{projekt}'."
                )
            if amne is not None and amne != best.rapportamne:
                raise RapportFel(
                    f"Beställningen gäller ämnet '{best.rapportamne}', inte '{amne}'."
                )
            if fragor:
                raise RapportFel(
                    "Frågorna kommer ur beställningen; ange inte fragor när bestallning anges."
                )
            amne = best.rapportamne
            fragor = list(best.urval)
        if amne is None:
            raise RapportFel("Ange ämne (amne) eller en beställning (bestallning).")
        if niva != 1:
            raise RapportFel(
                "Bara nivå 1 (skissunderlag) finns i R1. Nivå 2 (publicerbart planunderlag med "
                "metodnotis och ansvarig utredare) kommer i R3."
            )
        if amne not in AMNEN_NIVA1:
            extra = ""
            if amne == "buller":
                from rapport.r2_domaner import buller_spar

                extra = f" Spärrat: {buller_spar() or 'inget skäl kvar; rapportmall saknas'}"
            raise RapportFel(
                f"Ämnet '{amne}' har ingen skissrapport än. Finns: {', '.join(AMNEN_NIVA1)} "
                f"(ordning: skyfall, dagsljus/sol, luft, buller).{extra}"
            )
        if not korningar:
            raise RapportFel("Ange minst en körning (korningar).")
        if not self.tjanst.rot.is_dir():
            raise RapportFel("Flodens rotmapp finns inte.")
        self.tjanst.kravs_rapportprojekt(projekt)
        prefix = AMNESPREFIX[amne]
        try:
            if fragor:
                valda: list[Fraga] = [self.katalog.hamta(f) for f in fragor]
            else:
                valda = self.katalog.for_amne(amne, villkor=villkor)
        except FragekatalogFel as exc:
            raise RapportFel(str(exc)) from exc
        for f in valda:
            if f.id[:2] not in prefix:
                raise RapportFel(
                    f"Frågan {f.id} hör inte till ämnet {amne} ({'/'.join(prefix)}-xx)."
                )
        if not valda:
            raise RapportFel("Inga frågor ur katalogen matchar urvalet.")
        posts = [
            self._korningspost(k, amne, None if best is None else best.scenario, projekt=projekt)
            for k in dict.fromkeys(korningar)
        ]
        underlag_fn = getattr(self.tjanst.app, "korningsunderlag", None)
        for post in posts:
            # Vilket slags underlag körningen är (förslag, egen, demo): står i handlingsrutan (U2, K33 additivt).
            if underlag_fn is not None:
                post["underlag"] = underlag_fn(post["korning_id"])
        mott = tuple(Mottagare.fran_dict(m) for m in mottagare)
        if len({m.namn for m in mott}) != len(mott):
            raise RapportFel("Två mottagare har samma namn.")
        skapad = _nu()
        rid = (
            "r-"
            + sha256_bytes(
                json.dumps([projekt, amne, list(korningar), skapad, os.urandom(8).hex()]).encode()
            )[:12]
        )
        klass = self.policy.klass_for(DataKlass.OPPET, projekt).value
        try:
            inst = tillampa(
                tillampa(RapportInstallningar(), self.lokala_forval, self.resursmapp(rid)),
                installningar,
                self.resursmapp(rid),
                tillatna_rotter=self._tillatna_resursrotter,
            )
        except InstallningsFel as exc:
            raise RapportFel(str(exc)) from exc
        visningsprojekt = self.projektnamn(projekt)
        rapport = Rapport(
            rapport_id=rid,
            niva=niva,
            amne=amne,
            projekt=projekt,
            titel=titel or f"{TITEL[amne]} – {visningsprojekt}",
            skapad=skapad,
            fragor=tuple(f.id for f in valda),
            korningar=tuple(posts),
            mottagare=mott,
            pastaenden=(),
            kartor=tuple(self._kartor(rid, amne, posts)),
            metoder=tuple(self._metoder(posts, amne=amne)),
            metodnotis={
                "ai_stod": bool(ai_stod),
                "modeller": [modell] if modell else [],
                "ansvarig_utredare": ansvarig_utredare,
            },
            dataklass=klass,
            handelser=(
                {
                    "tid": skapad,
                    "handelse": "skapad",
                    "av": av,
                    "modell": modell,
                    "detalj": f"{len(valda)} frågor, körningar {', '.join(korningar)}",
                },
            ),
            katalog={
                "fil": "docs/utredningar-stockholm/fragor.json",
                "granskningsdatum": self.katalog.granskningsdatum,
                "fragor": {f.id: f.till_dict() for f in valda},
            },
            installningar=inst,
            bestallning=best,
        )
        if best is not None:
            self.bestallningar.spara(best)
        self.arkiv.spara(rapport)
        return rapport

    # ------------------------------------------------------------------ beställning (K30)

    def bestallning_skapa(
        self,
        *,
        syfte: str,
        amnen: Sequence[str],
        projekt: str,
        skede: str = "skiss",
        mottagare_roll: str = "planhandlaggare",
        fragor: Sequence[str] = (),
        villkor: Sequence[str] = (),
        bara_med_underlag: bool = False,
        scenario: Mapping[str, Any] | None = None,
    ) -> Bestallning:
        """Skapar och sparar en beställning (K30). Urvalet av frågor beräknas ur katalogen."""
        self.tjanst.kravs_rapportprojekt(projekt)
        b = skapa_bestallning(
            self.katalog,
            syfte=syfte,
            skede=skede,
            mottagare_roll=mottagare_roll,
            amnen=amnen,
            projekt=projekt,
            fragor=fragor,
            villkor=villkor,
            bara_med_underlag=bara_med_underlag,
            scenario=scenario,
        )
        self.bestallningar.spara(b)
        return b

    def bestallning_hamta(self, bestallning_id: str) -> Bestallning:
        try:
            return self.bestallningar.las(bestallning_id)
        except BestallningFel as exc:
            raise RapportFel(str(exc)) from exc

    def andra_installningar(
        self, rapport_id: str, andringar: Mapping[str, Any], av: str | None = None
    ) -> RapportInstallningar:
        """Ändrar rapportens inställningar. Kontrollen nollställs; händelsen loggas utan sökvägar."""
        r = self.hamta(rapport_id)
        try:
            ny = tillampa(
                r.installningar,
                andringar,
                self.resursmapp(rapport_id),
                tillatna_rotter=self._tillatna_resursrotter,
            )
        except InstallningsFel as exc:
            raise RapportFel(str(exc)) from exc
        if ny == r.installningar:
            return ny
        andrade = [k for k, v in ny.till_dict().items() if r.installningar.till_dict()[k] != v]
        self.arkiv.spara(
            r.med(
                installningar=ny,
                kontroll=None,
                handelser=(
                    *r.handelser,
                    {
                        "tid": _nu(),
                        "handelse": "installningar_andrade",
                        "av": av,
                        "modell": None,
                        "detalj": ", ".join(andrade),
                    },
                ),
            )
        )
        return ny

    # ------------------------------------------------------------------ påståenden

    def hamta(self, rapport_id: str) -> Rapport:
        return self.arkiv.las(rapport_id)

    def lagg_till_pastaende(
        self,
        rapport_id: str,
        *,
        fraga_id: str,
        status: str,
        text: str,
        belagg: Sequence[str | Mapping[str, Any]] = (),
        fordjupning: str,
        mottagare: str | None = None,
        forutsattning: str | None = None,
        ansvarig_funktion: str | None = None,
        motivering: str | None = None,
        saknat_underlag: Sequence[str] = (),
        forfattare: str = "ai",
        modell: str | None = None,
        av: str | None = None,
    ) -> Pastaende:
        """Lägger till ett påstående. Vägrar (`PastaendeFel`) om ett belägg eller en regel brister."""
        from rapport.skisskontroll import pastaendefel  # cirkulär import undviks

        r = self.hamta(rapport_id)
        if fraga_id not in r.fragor:
            raise PastaendeFel(
                f"Frågan '{fraga_id}' ingår inte i rapporten. Rapportens frågor: "
                + ", ".join(r.fragor)
                + "."
            )
        if forfattare not in FORFATTARE:
            raise PastaendeFel(f"Okänd författare '{forfattare}'.")
        hamtade = []
        korningar = [k["korning_id"] for k in r.korningar]
        for b in belagg:
            if isinstance(b, str):
                adress, kid = b, None
            else:
                adress, kid = str(b.get("adress", "")), b.get("korning_id")
            if kid is None:
                if len(korningar) != 1:
                    raise PastaendeFel(
                        f"Belägget '{adress}' saknar korning_id och rapporten har flera körningar."
                    )
                kid = korningar[0]
            if kid not in korningar:
                raise PastaendeFel(f"Körningen '{kid}' ingår inte i rapporten.")
            if adress.startswith("gyf.") and r.amne != "ekologi":
                raise PastaendeFel(
                    f"Belägget '{adress}' hör till ämnet ekologi, inte '{r.amne}'."
                )
            if adress.startswith("utci.") and r.amne != "dagsljus":
                raise PastaendeFel(
                    f"Belägget '{adress}' hör till ämnet dagsljus, inte '{r.amne}'."
                )
            try:
                hamtade.append(self.kalla.hamta(adress, str(kid), r.mottagare_per_namn))
            except BelaggFel as exc:
                raise PastaendeFel(f"Belägget vägras: {exc}") from exc
        if len({b.adress for b in hamtade}) != len(hamtade):
            raise PastaendeFel("Samma belägg är angivet två gånger.")
        pid = f"p-{len(r.pastaenden) + 1:03d}"
        p = Pastaende(
            pastaende_id=pid,
            fraga_id=fraga_id,
            status=status,
            text=text.strip(),
            belagg=tuple(hamtade),
            fordjupning=fordjupning,
            mottagare=mottagare,
            forutsattning=forutsattning,
            ansvarig_funktion=ansvarig_funktion,
            motivering=motivering,
            saknat_underlag=tuple(s.strip() for s in saknat_underlag if s and s.strip()),
            forfattare=forfattare,
            modell=modell,
            tid=_nu(),
        )
        fel = statusfel(p) + textfel(p) + pastaendefel(p, r)
        if fel:
            raise PastaendeFel("Påståendet vägras:\n- " + "\n- ".join(fel))
        try:
            p.fylld_text()
        except PastaendeFel as exc:
            raise PastaendeFel(f"Påståendet vägras: {exc}") from exc
        modeller = list(r.metodnotis.get("modeller", []))
        if modell and modell not in modeller:
            modeller.append(modell)
        ny = r.med(
            pastaenden=(*r.pastaenden, p),
            kontroll=None,
            metodnotis={**r.metodnotis, "modeller": modeller},
            handelser=(
                *r.handelser,
                {
                    "tid": p.tid,
                    "handelse": "pastaende_tillagt",
                    "av": av,
                    "modell": modell,
                    "detalj": f"{pid} {fraga_id} {status}, {len(hamtade)} belägg, "
                    f"indata {hash_av(p.till_dict())[:12]}",
                },
            ),
        )
        self.arkiv.spara(ny)
        return p

    def ta_bort_pastaende(self, rapport_id: str, pastaende_id: str, av: str | None = None) -> None:
        r = self.hamta(rapport_id)
        kvar = tuple(p for p in r.pastaenden if p.pastaende_id != pastaende_id)
        if len(kvar) == len(r.pastaenden):
            raise RapportFel(f"Påståendet '{pastaende_id}' finns inte.")
        self.arkiv.spara(
            r.med(
                pastaenden=kvar,
                kontroll=None,
                handelser=(
                    *r.handelser,
                    {
                        "tid": _nu(),
                        "handelse": "pastaende_borttaget",
                        "av": av,
                        "modell": None,
                        "detalj": pastaende_id,
                    },
                ),
            )
        )

    # ------------------------------------------------------------------ kontroll och export

    def kontrollera(self, rapport_id: str, av: str | None = None) -> dict[str, Any]:
        from rapport.skisskontroll import kontrollera_skissrapport

        r = self.hamta(rapport_id)
        resultat = kontrollera_skissrapport(r, self)
        self.arkiv.spara(
            r.med(
                kontroll=resultat,
                handelser=(
                    *r.handelser,
                    {
                        "tid": resultat["tid"],
                        "handelse": "kontroll",
                        "av": av,
                        "modell": None,
                        "detalj": "godkänd" if resultat["godkand"] else "underkänd",
                    },
                ),
            )
        )
        return resultat

    def exportera(self, rapport_id: str, fmt: str, av: str | None = None) -> Path:
        from rapport.skissexport import ExportStoppad, TypsnittFel, skriv_pdf, skriv_word
        from rapport.skissmall import bygg_dokument

        if fmt not in ("word", "pdf"):
            raise RapportFel("format ska vara 'word' eller 'pdf'.")
        resultat = self.kontrollera(rapport_id, av=av)
        r = self.hamta(rapport_id)
        if not resultat["godkand"]:
            raise ExportStoppad(resultat)
        dok = bygg_dokument(r, self, korlogg_rader=self.korloggrader(r), med_resurser=True)
        mapp = self.arkiv.rapportmapp(rapport_id)
        andelse = "docx" if fmt == "word" else "pdf"
        namn = f"export/{rapport_id}.{andelse}"
        ut = mapp / namn
        ut.parent.mkdir(parents=True, exist_ok=True)
        tmp = ut.with_name(ut.name + ".tmp")
        try:
            if fmt == "word":
                skriv_word(dok, tmp, mapp)
            else:
                skriv_pdf(dok, tmp, mapp)
        except TypsnittFel as exc:
            tmp.unlink(missing_ok=True)
            raise RapportFel(str(exc)) from exc
        except ModuleNotFoundError as exc:
            raise RapportFel(
                f"Exporten kräver biblioteket {exc.name}. Installera det med: "
                'pip install -e ".[rapport]"'
            ) from exc
        os.replace(tmp, ut)
        bilaga_namn: str | None = None
        if r.installningar.separat_bilaga:
            from rapport.skissmall import bygg_bilagedokument

            bil_dok = bygg_bilagedokument(
                r, self, korlogg_rader=self.korloggrader(r), med_resurser=True
            )
            bilaga_namn = f"export/{rapport_id}_bilaga.{andelse}"
            bil_ut = mapp / bilaga_namn
            bil_tmp = bil_ut.with_name(bil_ut.name + ".tmp")
            try:
                if fmt == "word":
                    skriv_word(bil_dok, bil_tmp, mapp)
                else:
                    skriv_pdf(bil_dok, bil_tmp, mapp)
            except TypsnittFel as exc:
                bil_tmp.unlink(missing_ok=True)
                raise RapportFel(str(exc)) from exc
            os.replace(bil_tmp, bil_ut)
        post: dict[str, Any] = {
            "format": fmt,
            "fil": namn,
            "sha256": sha256_fil(ut),
            "tid": _nu(),
        }
        if bilaga_namn is not None:
            post["bilaga_fil"] = bilaga_namn
            post["bilaga_sha256"] = sha256_fil(mapp / bilaga_namn)
        typsnitt = [
            {"roll": roll, "familj": res.familj, "sha256": res.sha256}
            for roll, res in r.installningar.resurser().items()
            if roll.startswith("typsnitt")
        ]
        if typsnitt:
            post["typsnitt"] = typsnitt
        r = self.hamta(rapport_id)
        self.arkiv.spara(
            r.med(
                exporter=(*r.exporter, post),
                handelser=(
                    *r.handelser,
                    {
                        "tid": post["tid"],
                        "handelse": f"export_{fmt}",
                        "av": av,
                        "modell": None,
                        "detalj": f"{namn} sha256 {str(post['sha256'])[:12]}"
                        + "".join(
                            f", typsnitt {t['familj']} ({str(t['sha256'])[:12]})" for t in typsnitt
                        ),
                    },
                ),
            )
        )
        return ut

    def korloggrader(self, r: Rapport) -> list[dict[str, Any]]:
        """MCP-körloggens rader som rör rapporten (rapport-id) eller dess körningar."""
        if self.korlogg is None:
            return []
        kids = {k["korning_id"] for k in r.korningar}
        ut = []
        for rad in self.korlogg.las():
            if r.rapport_id in rad.get("rapport_id", []) or kids & set(rad.get("korning_id", [])):
                ut.append(rad)
        return ut

    def _tillgangliga_matvarden(
        self, korning_id: str, analys: str, scenario: str, kandidater: Sequence[str]
    ) -> list[str]:
        """Filtrerar mätvärdesnycklar till sådana som faktiskt finns som tal i körningen."""
        from rapport.belaggskalla import REGN_PARAMETRAR

        try:
            svar = self.tjanst.resultat_sammanfattning(
                korning_id, analys=analys, scenario=scenario, ruta=None, max_rader=500
            )
            rader = [r for t in svar["matvarden"] for r in t["rader"]]
        except Exception:  # noqa: BLE001
            return list(kandidater)
        if not rader:
            return []
        varden = rader[0].get("varden", {})
        par: Mapping[str, Any] = {}
        try:
            par = self.kalla.jobbparametrar(korning_id, analys, scenario)
        except Exception:  # noqa: BLE001
            par = {}

        def _ar_tal(val: Any) -> bool:
            return isinstance(val, int | float) and not isinstance(val, bool)

        har_raster: bool | None = None
        ut: list[str] = []
        for n in kandidater:
            if _ar_tal(varden.get(n)) or (n in REGN_PARAMETRAR and _ar_tal(par.get(n))):
                ut.append(n)
            elif n in SKYFALL_TROSKEL_M:
                if har_raster is None:
                    har_raster = self.kalla.har_max_djup_raster(korning_id, analys, scenario)
                if har_raster:
                    ut.append(n)
        return ut

    def adresser(self, r: Rapport) -> list[str]:
        """Beläggsadresser som går att använda i rapporten (jämförelser, mätvärden, mottagare)."""
        from rapport.belaggskalla import (
            JAMFORELSEMATT,
            MATTKATALOG,
            OMRADESSTATISTIK,
            VATTEN_MATVARDEN,
        )

        if r.amne == "dagsljus":
            return self._adresser_dagsljus(r)
        if r.amne == "ekologi":
            return self._adresser_ekologi(r)
        if r.amne == "luft":
            return self._adresser_luft(r)
        ut: list[str] = []
        for k in r.korningar:
            for j in k["jamforelser"]:
                ut += [f"jamforelse.{j['namn']}.{m}" for m in JAMFORELSEMATT]
            for a in k["analyser"]:
                losare = str(a.get("losare", ""))
                katalog_nycklar = [
                    n.removeprefix("matvarde:")
                    for (los, n) in MATTKATALOG
                    if los == losare and n.startswith("matvarde:")
                ]
                kandidater = katalog_nycklar or list(VATTEN_MATVARDEN)
                for s in a["scenarier"]:
                    giltiga = self._tillgangliga_matvarden(
                        str(k["korning_id"]), str(a["analys"]), str(s), kandidater
                    )
                    ut += [f"matvarde.{a['analys']}.{s}.{n}" for n in giltiga]
            for m in r.mottagare:
                for j in k["jamforelser"]:
                    for s in (j["fore"], j["efter"]):
                        ut += [
                            f"omrade.{m.namn}.{j['analys']}.{s}.{j['utdata']}.{st}"
                            for st in ("max", "medel")
                        ]
                    ut += [f"omrade.{m.namn}.skillnad.{j['namn']}.{st}" for st in OMRADESSTATISTIK]
        return list(dict.fromkeys(ut))

    def _adresser_luft(self, r: Rapport) -> list[str]:
        """Beläggsadresser för luft: `matvarde.<analys>.<scenario>.<mått>` ur `luft.mkn` (`rapport.luftbelagg`)."""
        from rapport.luftbelagg import LUFT_ADRESSMATT, LUFT_LOSARE

        ut: list[str] = []
        for k in r.korningar:
            for a in k["analyser"]:
                if a["losare"] != LUFT_LOSARE:
                    continue
                for s in a["scenarier"]:
                    ut += [f"matvarde.{a['analys']}.{s}.{m}" for m in LUFT_ADRESSMATT]
        return list(dict.fromkeys(ut))

    def _adresser_ekologi(self, r: Rapport) -> list[str]:
        """Beläggsadresser för ekologi (se `rapport.ekologibelagg`, `rapport.gyf_utci_belagg` och `belaggskalla.EKOLOGI_MATT`)."""
        from rapport.belaggskalla import EKOLOGI_MATT
        from rapport.ekologibelagg import DELTAMATT, NATVERKSPARAMETRAR, skrivbord_adresser
        from rapport.gyf_utci_belagg import gyf_adresser

        mv = [n.removeprefix("matvarde:") for (_l, n) in EKOLOGI_MATT if n.startswith("matvarde:")]
        ut: list[str] = []
        for k in r.korningar:
            for a in k["analyser"]:
                if a["losare"] != "ekologi.konnektivitet":
                    continue
                for s in a["scenarier"]:
                    ut += [f"matvarde.{a['analys']}.{s}.{m}" for m in mv]
                    ut += [f"ekonatverk.{a['analys']}.{s}.{p}" for p in NATVERKSPARAMETRAR]
                for j in k["jamforelser"]:
                    if j["analys"] == a["analys"]:
                        ut += [
                            f"ekojamforelse.{a['analys']}.{j['fore']}.{j['efter']}.{m}" for m in DELTAMATT
                        ]
            with contextlib.suppress(BelaggFel):
                kmapp = self.kalla.utmapp(str(k["korning_id"]))
                ut += gyf_adresser(kmapp)
                ut += skrivbord_adresser(kmapp)
        return list(dict.fromkeys(ut))

    def _adresser_dagsljus(self, r: Rapport) -> list[str]:
        """Beläggsadresser för dagsljus och sol (se `rapport.fasadbelagg`, `rapport.gyf_utci_belagg` och `belaggskalla`)."""
        from rapport.fasadbelagg import JAMFORMATT, PARAMETRAR, PUNKTMATT
        from rapport.gyf_utci_belagg import utci_adresser

        ut: list[str] = []
        for k in r.korningar:
            for a in k["analyser"]:
                if a["losare"] == "dagsljus.soltimmar_mark":
                    for s in a["scenarier"]:
                        ut += [
                            f"omrade.hela_omradet.{a['analys']}.{s}.soltimmar.{st}"
                            for st in ("min", "medel", "median", "max", "yta_m2")
                        ]
                        for m in r.mottagare:
                            ut += [
                                f"omrade.{m.namn}.{a['analys']}.{s}.soltimmar.{st}"
                                for st in ("medel", "max", "min")
                            ]
                if a["losare"] == "dagsljus.vsc_fasad":
                    ut += [f"fasad.{a['analys']}.{p}" for p in PARAMETRAR]
                    for s in a["scenarier"]:
                        ut += [f"fasad.{a['analys']}.{s}.{m}" for m in PUNKTMATT]
            for j in k["jamforelser"]:
                ut += [
                    f"jamforelse.{j['namn']}.{m}"
                    for m in (
                        "max_okning", "max_minskning", "yta_okning_m2", "yta_minskning_m2",
                        "yta_minskning_over_<X>h_m2 (X heltal, t.ex. 1 eller 2)",
                    )
                ]  # fmt: skip
                for a in k["analyser"]:
                    if a["losare"] == "dagsljus.vsc_fasad":
                        ut += [
                            f"fasadjamforelse.{a['analys']}.{j['fore']}.{j['efter']}.{m}"
                            for m in JAMFORMATT
                        ]
                for m in r.mottagare:
                    ut += [
                        f"omrade.{m.namn}.skillnad.{j['namn']}.{st}"
                        for st in ("medel", "min", "yta_minskning_m2")
                    ]
            with contextlib.suppress(BelaggFel):
                ut += utci_adresser(self.kalla.utmapp(str(k["korning_id"])))
        return list(dict.fromkeys(ut))

    def lista(self, projekt: str | None = None) -> list[dict[str, Any]]:
        return [sammanfattning(r, self.arkiv) for r in self.arkiv.lista(projekt)]

    def planerarfigurer(self, r: Rapport, *, skriv_filer: bool = True) -> PlanerarFigurer:
        """Deterministiska planerarfigurer: berikade kartor, detaljzoom, tvärsektion och lågpunkter.

        Rör aldrig K33-fältet ``r.kartor`` (bevarar 100 % bakåtkompatibilitet och ``9/9``-kontroll).
        Returnerar tom ``PlanerarFigurer`` om körningens raster saknas (t.ex. i rena mocktester).
        """
        from rapport.figurer import (
            figurstil_for_lager,
            rendera_kartfigur,
            rita_sektionsfigur,
        )
        from rapport.sprak import scenarionamn
        from testsvit.k1_io import GeoTransformInfo

        if not r.korningar:
            return PlanerarFigurer()
        fragor = getattr(self.tjanst, "fragor", None)
        if fragor is None or not hasattr(fragor, "lager_raster"):
            return PlanerarFigurer()

        kartmapp = self.arkiv.rapportmapp(r.rapport_id) / "kartor"
        if skriv_filer:
            kartmapp.mkdir(parents=True, exist_ok=True)

        # Identifiera par (bas + scenario) per jämförelse så de får bottenlegend för sida-vid-sida!
        par_kart_ids: set[str] = set()
        for kp in r.korningar:
            for j in kp.get("jamforelser", []):
                bas_k = next(
                    (
                        k
                        for k in r.kartor
                        if k.typ == "bas"
                        and k.korning_id == kp["korning_id"]
                        and k.analys == j["analys"]
                        and k.scenario == j["fore"]
                        and (not k.meta.get("roll") or k.meta.get("roll") == j["utdata"])
                    ),
                    None,
                )
                efter_k = next(
                    (
                        k
                        for k in r.kartor
                        if k.typ == "scenario"
                        and k.korning_id == kp["korning_id"]
                        and k.jamforelse == j["namn"]
                    ),
                    None,
                )
                if bas_k is not None and efter_k is not None:
                    par_kart_ids.add(bas_k.kart_id)
                    par_kart_ids.add(efter_k.kart_id)

        # För skyfall: deterministiska tröskelytor + lågpunkter P1–P3 + markhöjd
        trosklar_fore: SkyfallsTroskelytor | None = None
        trosklar_efter: SkyfallsTroskelytor | None = None
        troskel_analys = ""
        lagpunkter: list[Lagpunkt] = []
        kontext_cache: dict[tuple[str, str], dict[str, Any]] = {}

        def _kontext(kid: str, scen: str, la: Mapping[str, Any], h: int, b: int) -> dict[str, Any]:
            nyckel = (kid, scen)
            if nyckel in kontext_cache:
                return kontext_cache[nyckel]
            rutor = _k1_rutor_for(fragor, kid, scen)
            terrang = _las_terrang_mosaik(rutor, la, h, b)
            baskarta = _las_baskarta_linjer(rutor)
            floden = _las_flodesvagar(fragor, kid, scen) if r.amne == "skyfall" else []
            poly = _projektpolygon_ring(fragor, kid)
            ut_k = {
                "terrang": terrang,
                "baskarta": baskarta,
                "floden": floden,
                "projektpolygon": poly,
            }
            kontext_cache[nyckel] = ut_k
            return ut_k

        # Beräkna skyfalls-trösklar & lågpunkter ur första skyfallskörningen
        if r.amne == "skyfall":
            kp0 = r.korningar[0]
            kid0 = str(kp0["korning_id"])
            an0 = next(
                (a for a in kp0.get("analyser", []) if str(a.get("losare", "")).startswith("vatten.")),
                None,
            )
            if an0 is not None:
                troskel_analys = str(an0["analys"])
                jf0 = next(
                    (
                        j
                        for j in kp0.get("jamforelser", [])
                        if j.get("analys") == troskel_analys and j.get("utdata") == "max_djup"
                    ),
                    None,
                )
                fore_s = str(jf0["fore"]) if jf0 is not None else (
                    str(an0["scenarier"][0]) if an0.get("scenarier") else None
                )
                efter_s = str(jf0["efter"]) if jf0 is not None else None
                if fore_s:
                    trosklar_fore = self.kalla.skyfall_trosklar(kid0, troskel_analys, fore_s)
                if efter_s:
                    trosklar_efter = self.kalla.skyfall_trosklar(kid0, troskel_analys, efter_s)
                prim_s = efter_s or fore_s
                if prim_s:
                    pack = self.kalla._max_djup_data(kid0, troskel_analys, prim_s)  # noqa: SLF001
                    if pack is not None:
                        ras_p, _tak_p, _pm_p, _geo_p, la_p, _, _ = pack
                        ktx_p = _kontext(kid0, prim_s, la_p, ras_p.shape[0], ras_p.shape[1])
                        lagpunkter = self.kalla.skyfall_lagpunkter(
                            kid0, troskel_analys, prim_s, mark_z=ktx_p["terrang"]
                        )

        markorer_dict = [
            {"id": lp.id, "e_m": lp.e_m, "n_m": lp.n_m, "djup_m": lp.djup_m} for lp in lagpunkter
        ]

        kart_filer: dict[str, str] = {}
        detaljkartor: list[ExtraFigur] = []
        sektioner: list[ExtraFigur] = []
        gjorda_detalj: set[tuple[str, str]] = set()
        gjorda_sektion: set[str] = set()

        def _rendera_och_spara(
            *,
            filnamn: str,
            raster_sub: NDArray[np.float64],
            tak_sub: NDArray[np.bool_] | None,
            vatten_sub: NDArray[np.bool_] | None,
            mark_sub: NDArray[np.float64] | None,
            geo: GeoTransformInfo,
            la: Mapping[str, Any],
            titel: str,
            undertitel: str,
            byggnader: Sequence[Mapping[str, Any]] | None,
            baskarta: Sequence[Sequence[Sequence[float]]],
            flodesvagar: Sequence[Sequence[Sequence[float]]],
            projektpolygon: Sequence[Sequence[float]] | None,
            markorer: Sequence[Mapping[str, Any]] | None,
            skiss: bool,
            run_hash: str,
            karta_max_px: int,
            legend_placering: str,
        ) -> tuple[str, str]:
            stil = figurstil_for_lager(la)
            anm = (
                "SKISS: metoden är inte klassad för formell kravprövning. Deterministisk fysikberäkning."
                if skiss
                else "Deterministisk fysikberäkning."
            )
            duk = rendera_kartfigur(
                raster_sub,
                stil,
                titel=titel,
                undertitel=undertitel,
                metodstatus="Skissunderlag" if skiss else "Rapportmetod",
                geo=geo,
                byggnad_mask=tak_sub,
                vatten_mask=(
                    vatten_sub if (vatten_sub is not None and bool(np.any(vatten_sub))) else None
                ),
                byggnader_3011=byggnader,
                projektpolygon_3011=projektpolygon,
                baskarta_linjer_3011=baskarta or None,
                markhojd_m=mark_sub,
                flodesvagar_3011=flodesvagar or None,
                markorer_3011=markorer or None,
                run_hash=run_hash,
                anmarkning=anm,
                kalla="Floden",
                karta_max_px=karta_max_px,
                legend_placering="botten" if legend_placering == "botten" else "sida",
            )
            buf = io.BytesIO()
            duk.save(buf, format="PNG", optimize=True)
            png_bytes = buf.getvalue()
            sha = sha256_bytes(png_bytes)
            rel_fil = f"kartor/{filnamn}"
            if skriv_filer:
                _skriv_bytes_om_andrad(kartmapp / filnamn, png_bytes)
            return rel_fil, sha

        for k in r.kartor:
            try:
                res = self.kalla._resultat(k.korning_id)  # noqa: SLF001
                data = fragor.lager_raster(k.korning_id, k.lager_id)
            except Exception:  # noqa: BLE001
                continue
            la = data["lager"]
            raster = np.asarray(data["raster"], dtype=np.float64)
            tak = None if data.get("tak") is None else np.asarray(data["tak"], dtype=bool)
            h, b = raster.shape
            sc_kontext = str(la.get("efter") if la.get("typ") == "skillnad" else la.get("scenario") or k.scenario)
            ktx = _kontext(k.korning_id, sc_kontext, la, h, b)
            byggnader = None
            if res.get("byggnader"):
                byggnader = res["byggnader"]["scenarier"].get(sc_kontext)
            vatten_mask: NDArray[np.bool_] | None = None
            if r.amne == "skyfall" and hasattr(self.tjanst, "_vatten_mask_for_lager"):
                with contextlib.suppress(Exception):
                    vm = self.tjanst._vatten_mask_for_lager(k.korning_id, la, (h, b))  # noqa: SLF001
                    if vm is not None:
                        vatten_mask = np.asarray(vm, dtype=bool)

            utsnitt = k.meta.get("utsnitt") or k.meta.get("kartutsnitt")
            r_sub, t_sub, v_sub, m_sub, geo_sub = _beskar_lager(
                raster, tak, vatten_mask, ktx["terrang"], la, utsnitt
            )
            skiss = str(k.meta.get("metodstatus") or "skiss") != "rapport"
            kp_post = next((x for x in r.korningar if x["korning_id"] == k.korning_id), {})
            run_hash = str(kp_post.get("manifest_sha256") or "")
            ar_par = k.kart_id in par_kart_ids
            vis_titel = str(k.meta.get("rubrik") or k.kart_id)
            if r.installningar.sbk:
                undertitel = (
                    f"{self.projektnamn(r.projekt)} · SWEREF 99 18 00"
                    if not ar_ra_uuid(self.projektnamn(r.projekt))
                    else "SWEREF 99 18 00 · RH 2000"
                )
            else:
                undertitel = f"Körning {k.korning_id} · EPSG:3011"

            rel_fil, _sha = _rendera_och_spara(
                filnamn=f"{k.kart_id}_plan.png",
                raster_sub=r_sub,
                tak_sub=t_sub,
                vatten_sub=v_sub,
                mark_sub=m_sub,
                geo=geo_sub,
                la=la,
                titel=vis_titel,
                undertitel=undertitel,
                byggnader=byggnader,
                baskarta=ktx["baskarta"],
                flodesvagar=ktx["floden"] if k.typ != "skillnad" else [],
                projektpolygon=ktx["projektpolygon"],
                markorer=markorer_dict if r.amne == "skyfall" else None,
                skiss=skiss,
                run_hash=run_hash,
                karta_max_px=520 if ar_par else 680,
                legend_placering="botten" if ar_par else "sida",
            )
            kart_filer[k.kart_id] = rel_fil

            # Automatisk inzoomad detaljkarta runt förslagsbyggnader / projektpolygon (+40 m)
            detalj_nyckel = (k.korning_id, k.analys)
            skall_detalj = (
                detalj_nyckel not in gjorda_detalj
                and r.amne in ("skyfall", "dagsljus")
                and (k.typ == "scenario" or (k.typ == "bas" and not kp_post.get("jamforelser")))
            )
            if skall_detalj:
                det_box = _detalj_utsnitt(
                    la=la,
                    byggnader=byggnader,
                    projektpolygon=ktx["projektpolygon"],
                    mottagare=r.mottagare,
                    oversikt_utsnitt=utsnitt,
                )
                if det_box is not None:
                    gjorda_detalj.add(detalj_nyckel)
                    dr, dt, dv, dm, dgeo = _beskar_lager(
                        raster, tak, vatten_mask, ktx["terrang"], la, det_box
                    )
                    det_rubrik = (
                        f"Detaljkarta: största vattendjup {scenarionamn(sc_kontext)} förslaget"
                        if r.amne == "skyfall" and k.typ == "scenario"
                        else (
                            "Detaljkarta: största vattendjup (läget idag)"
                            if r.amne == "skyfall"
                            else f"Detaljkarta: soltimmar ({scenarionamn(sc_kontext)})"
                        )
                    )
                    det_fil, det_sha = _rendera_och_spara(
                        filnamn=f"detalj_{k.kart_id}.png",
                        raster_sub=dr,
                        tak_sub=dt,
                        vatten_sub=dv,
                        mark_sub=dm,
                        geo=dgeo,
                        la=la,
                        titel=det_rubrik,
                        undertitel="Inzoomning runt förslag och närområde (+40 m kontext)",
                        byggnader=byggnader,
                        baskarta=ktx["baskarta"],
                        flodesvagar=ktx["floden"],
                        projektpolygon=ktx["projektpolygon"],
                        markorer=markorer_dict if r.amne == "skyfall" else None,
                        skiss=skiss,
                        run_hash=run_hash,
                        karta_max_px=720,
                        legend_placering="sida",
                    )
                    det_text = (
                        "Inzoomad detaljkarta kring förslagsbyggnader och projektområde "
                        "(+40 m omgivande kontext) med vägkanter, höjdkurvor, byggnader och "
                        "markerade lågpunkter. Norr uppåt; skalstock i kartan."
                        if r.amne == "skyfall"
                        else "Inzoomad detaljkarta kring förslagsbyggnader och gårdsrum "
                        "(+40 m omgivande kontext). Norr uppåt; skalstock i kartan."
                    )
                    detaljkartor.append(
                        ExtraFigur(
                            fil=det_fil,
                            bildtext=det_text,
                            rubrik=det_rubrik,
                            typ="detalj",
                            sha256=det_sha,
                        )
                    )

            # Automatisk tvärsektion genom djupaste lågpunkt (P1) / nya byggnaden för skyfall
            if (
                r.amne == "skyfall"
                and k.analys not in gjorda_sektion
                and ktx["terrang"] is not None
                and (k.typ == "scenario" or (k.typ == "bas" and not kp_post.get("jamforelser")))
                and (lagpunkter or any(b.get("status") in ("ny", "andrad") for b in (byggnader or ())))
            ):
                gjorda_sektion.add(k.analys)
                fore_raster: NDArray[np.float64] | None = None
                efter_raster: NDArray[np.float64] | None = None
                jf_match = next(
                    (j for j in kp_post.get("jamforelser", []) if j.get("namn") == k.jamforelse),
                    None,
                )
                if jf_match is not None:
                    efter_raster = raster
                    with contextlib.suppress(Exception):
                        f_data = fragor.lager_raster(
                            k.korning_id, f"{k.analys}.{jf_match['fore']}.max_djup"
                        )
                        if np.asarray(f_data["raster"]).shape == raster.shape:
                            fore_raster = np.asarray(f_data["raster"], dtype=np.float64)
                else:
                    fore_raster = raster

                sek_data = _sampla_tvarsektion(
                    terrang=ktx["terrang"],
                    la=la,
                    djup_fore=fore_raster,
                    djup_efter=efter_raster,
                    tak=tak,
                    byggnader=byggnader,
                    lagpunkter=lagpunkter,
                )
                if sek_data is not None:
                    sek_img = rita_sektionsfigur(
                        sek_data["avstand_m"],
                        sek_data["mark_m"],
                        vatten_fore_m=sek_data["vatten_fore_m"],
                        vatten_efter_m=sek_data["vatten_efter_m"],
                        byggnad_mask=sek_data["byggnad_mask"],
                        byggnad_hojd_rh2000_m=sek_data["byggnad_hojd_m"],
                        byggnad_ny_mask=sek_data["byggnad_ny_mask"],
                        titel="Tvärsektion genom djupaste lågpunkt och bebyggelse",
                        undertitel=sek_data["undertitel"],
                        metodstatus="Skissunderlag" if skiss else "Rapportmetod",
                        markor_avstand_m=sek_data["p1_avstand_m"],
                        markor_etikett=sek_data["p1_id"],
                        run_hash=run_hash,
                        kalla="Floden",
                    )
                    buf_s = io.BytesIO()
                    sek_img.save(buf_s, format="PNG", optimize=True)
                    sek_bytes = buf_s.getvalue()
                    sek_sha = sha256_bytes(sek_bytes)
                    sek_namn = f"sektion_{k.analys}.png"
                    if skriv_filer:
                        _skriv_bytes_om_andrad(kartmapp / sek_namn, sek_bytes)
                    sektioner.append(
                        ExtraFigur(
                            fil=f"kartor/{sek_namn}",
                            bildtext=(
                                "Höjd- och vattensektion i RH 2000 genom den djupaste lågpunkten "
                                "och angränsande bebyggelse. Marklinje ur markmodellen, vattenyta "
                                "före och efter förslaget samt byggnadsblock."
                            ),
                            rubrik="Tvärsektion genom djupaste lågpunkt och bebyggelse",
                            typ="sektion",
                            sha256=sek_sha,
                        )
                    )

        return PlanerarFigurer(
            kart_filer=kart_filer,
            detaljkartor=tuple(detaljkartor),
            sektioner=tuple(sektioner),
            lagpunkter=tuple(lagpunkter),
            trosklar_fore=trosklar_fore,
            trosklar_efter=trosklar_efter,
            troskel_analys=troskel_analys,
        )


def _nulagesrubrik(amne: str) -> str:
    """Kartrubrik i bilden för nulägeskartor (läget idag utan jämförelse mot förslag)."""
    return {
        "skyfall": "Största vattendjup – läget idag",
        "dagsljus": "Soltimmar på marken – läget idag",
        "ekologi": "Strömtäthet (flaskhalsar) – läget idag",
    }.get(amne, f"{TITEL.get(amne, amne)} – läget idag")


def kartutsnitt(res: Mapping[str, Any], j: Mapping[str, Any]) -> list[float] | None:
    """Samma utsnitt för före, efter och skillnad: där något syns, med marginal (minst 120 m).

    Kvadratiskt kring de synliga cellerna i alla tre lagren, inom lagrets utbredning."""
    lager = [
        la
        for la in res["lager"]
        if la["analys"] == j["analys"]
        and la["roll"] == j["utdata"]
        and (
            la["id"] == f"skillnad.{j['namn']}"
            or (la["typ"] != "skillnad" and la["scenario"] in (j["fore"], j["efter"]))
        )
    ]
    syn = [la.get("synlig_utbredning") for la in lager if la.get("synlig_utbredning")]
    if not syn:
        return None
    x0 = min(float(u["x_min"]) for u in syn)
    x1 = max(float(u["x_max"]) for u in syn)
    y0 = min(float(u["y_min"]) for u in syn)
    y1 = max(float(u["y_max"]) for u in syn)
    sida = max(120.0, 1.4 * max(x1 - x0, y1 - y0))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    u = lager[0]["utbredning"]
    sida = min(sida, float(u["x_max"]) - float(u["x_min"]), float(u["y_max"]) - float(u["y_min"]))
    ex0 = min(max(cx - sida / 2, float(u["x_min"])), float(u["x_max"]) - sida)
    ny0 = min(max(cy - sida / 2, float(u["y_min"])), float(u["y_max"]) - sida)
    return [round(ex0), round(ny0), round(ex0 + sida), round(ny0 + sida)]


def _rapportmapp_for(tjanst: FlodenTjanst) -> Path:
    app_mapp = getattr(tjanst.app, "rapportmapp", None)
    if app_mapp is not None:
        return Path(app_mapp)
    return tjanst.rot / STANDARD_RAPPORTMAPP


def _regnantagande(r: Mapping[str, Any]) -> str:
    """Formaterar ett regnantagande ur körningen utan att någonsin skriva 'None'."""
    delar: list[str] = []
    rapport = r.get("rapport")
    kalla = r.get("kalla")
    tabell = r.get("tabell")
    if rapport or kalla:
        namn = str(rapport or kalla)
        if tabell:
            namn += f" ({tabell})"
        delar.append(namn)
        if r.get("aterkomsttid_ar") is not None:
            delar.append(f"återkomsttid {_sv(r['aterkomsttid_ar'])} år")
        if r.get("varaktighet_min") is not None:
            delar.append(f"varaktighet {_sv(r['varaktighet_min'])} min")
        if r.get("regntyp"):
            delar.append(f"{r['regntyp']}regn")
        if r.get("klimatfaktor") is not None:
            delar.append(f"klimatfaktor {_sv(r['klimatfaktor'])} (uttrycklig parameter)")
        if r.get("regn_mm") is not None:
            delar.append(f"{_sv(round(float(r['regn_mm']), 1))} mm")
    else:
        if r.get("regn_mm") is not None:
            delar.append(f"angiven regnmängd {_sv(round(float(r['regn_mm']), 1))} mm")
        else:
            delar.append("angivet regn")
        if r.get("varaktighet_min") is not None:
            delar.append(f"varaktighet {_sv(r['varaktighet_min'])} min")
        if r.get("regntyp"):
            delar.append(f"{r['regntyp']}regn")
        if r.get("klimatfaktor") is not None:
            delar.append(f"klimatfaktor {_sv(r['klimatfaktor'])} (uttrycklig parameter)")
    return "Regn: " + ", ".join(delar) + "."


def _skalfelsantagande(koordinater: Mapping[str, Any] | None) -> str | None:
    """Deterministisk skalfelsnot när kommunen publicerar i annat EPSG än internt EPSG:3011."""
    if not isinstance(koordinater, Mapping):
        return None
    pub = koordinater.get("publicering_epsg")
    if pub is None:
        return None
    try:
        pub_int = int(pub)
        intern_int = int(koordinater.get("intern_epsg", 3011))
    except (TypeError, ValueError):
        return None
    if pub_int == intern_int:
        return None
    langd = koordinater.get("skalfel_langd_procent")
    yta = koordinater.get("skalfel_yta_procent")
    if (
        isinstance(langd, int | float)
        and not isinstance(langd, bool)
        and isinstance(yta, int | float)
        and not isinstance(yta, bool)
    ):
        langd_s = f"{float(langd):.2f}".replace(".", ",")
        yta_s = f"{float(yta):.2f}".replace(".", ",")
        return (
            f"Koordinatsystem och skalfel: kommunen publicerar i EPSG:{pub_int}, "
            f"Floden räknar i EPSG:{intern_int} (SWEREF 99 18 00). "
            f"Projektionens skalfel i området är {langd_s} % för längder och {yta_s} % för ytor."
        )
    return (
        f"Koordinatsystem: kommunen publicerar i EPSG:{pub_int}, "
        f"Floden räknar i EPSG:{intern_int} (SWEREF 99 18 00)."
    )


def _antaganden(
    res: Mapping[str, Any],
    analyser: Iterable[str],
    *,
    koordinater: Mapping[str, Any] | None = None,
) -> list[str]:
    """Antaganden som Floden själv har skrivit (regn, takvatten, avtappning; för dagsljus datum,
    tidssteg och avgränsningar ur lösarnas parametrar) för analyserna."""
    valda = set(analyser)
    ut: list[str] = []
    for a in res.get("antaganden", {}).get("analyser", []):
        if a.get("analys") in valda and str(a.get("losare", "")).startswith("dagsljus."):
            ut.append(_dagsljusantagande(a))
    for r in res.get("antaganden", {}).get("regn", []):
        if r.get("analys") in valda:
            ut.append(_regnantagande(r))
    for v in res.get("antaganden", {}).get("vatten", []):
        if v.get("analys") in valda:
            for nyckel in ("takvatten_text", "avtappning_text"):
                if v.get(nyckel):
                    ut.append(str(v[nyckel]) + ".")
    koord = koordinater or res.get("koordinater") or res.get("antaganden", {}).get("koordinater")
    skalnot = _skalfelsantagande(koord)
    if skalnot:
        ut.append(skalnot)
    return list(dict.fromkeys(ut))


def _dagsljusantagande(a: Mapping[str, Any]) -> str:
    """Dagsljuslösarens parametrar som en mening (Floden skriver, ur K0-manifestets parametrar)."""
    from rapport.sprak import datum_text

    p = a.get("parametrar", {})
    losare = str(a["losare"])
    delar: list[str] = []
    if losare == "dagsljus.soltimmar_mark":
        datum = [str(d) for d in (p.get("datum") or []) if str(d).strip()]
        datum_del = (
            " för " + ", ".join(datum_text(d, True) for d in datum)
            if datum
            else ""
        )
        detaljer: list[str] = []
        if p.get("steg_min") is not None:
            detaljer.append(f"tidssteg {_sv(p.get('steg_min'))} min")
        if p.get("azimutriktningar") is not None:
            detaljer.append(f"{_sv(p.get('azimutriktningar'))} riktningar")
        parentes = f" ({', '.join(detaljer)})" if detaljer else ""
        delar.append(f"Soltimmar på mark{datum_del}{parentes}")
    elif losare == "dagsljus.vsc_fasad":
        delar.append(
            "Vertikal himmelskomponent (VSC) vid fasadpunkter, mulen CIE-himmel, en punkt var "
            f"{_sv(p.get('fasad_steg_m'))} m längs fasaden och var {_sv(p.get('vaningshojd_m'))} m "
            f"i höjd, {_sv(p.get('hojd_over_golv_m'))} m över golv"
        )
    else:
        delar.append(f"{losare}")
    delar.append(
        f"cellstorlek {_sv(p.get('cellstorlek_m'))} m, påverkansradie ur högsta hinder "
        f"{_sv(p.get('hojd_max_m'))} m och lägsta solhöjd {_sv(p.get('h_min_grader'))} grader, "
        f"vegetation {'med' if p.get('vegetation') else 'utan'}"
    )
    return "; ".join(delar) + "."


def _skriv_bytes_om_andrad(mal: Path, data: bytes) -> None:
    if mal.is_file() and mal.read_bytes() == data:
        return
    mal.parent.mkdir(parents=True, exist_ok=True)
    tmp = mal.with_name(mal.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, mal)


def _k1_rutor_for(fragor: Any, korning_id: str, scenario: str) -> list[Path]:
    """K1-rutornas kataloger för (korning_id, scenario) via FragaTjanst."""
    if not (
        hasattr(fragor, "_hamta")
        and hasattr(fragor, "_sammanstallning")
        and hasattr(fragor, "_scenariorutor")
    ):
        return []
    try:
        k_obj = fragor._hamta(korning_id)  # noqa: SLF001
        s_dok = fragor._sammanstallning(k_obj)  # noqa: SLF001
        rutor_map, _varn = fragor._scenariorutor(k_obj, s_dok)  # noqa: SLF001
        if not rutor_map:
            return []
        sc_map = rutor_map.get(scenario) or next(iter(rutor_map.values()), {})
        return [Path(p) for p in sc_map.values() if Path(p).is_dir()]
    except Exception:  # noqa: BLE001
        return []


def _las_terrang_mosaik(
    rutor: Sequence[Path], la: Mapping[str, Any], h: int, b: int
) -> NDArray[np.float64] | None:
    """Läser bar markhöjd (``terrang.tif``, annars ``mark.tif``) på lagrets exakta rutnät."""
    from testsvit.k1_io import las_geotiff

    if not rutor:
        return None
    u = la["utbredning"]
    ox = float(u["x_min"])
    oy = float(u["y_max"])
    px = float(la["pixelstorlek_m"])
    ut = np.full((h, b), np.nan, dtype=np.float64)
    hittad = False
    for rmapp in rutor:
        fil = rmapp / "terrang.tif"
        if not fil.is_file():
            fil = rmapp / "mark.tif"
        if not fil.is_file():
            continue
        try:
            arr, geo = las_geotiff(fil)
        except Exception:  # noqa: BLE001
            continue
        z = np.asarray(arr, dtype=np.float64)
        th, tb = z.shape
        if (
            abs(geo.pixelstorlek_x - px) < 1e-6
            and abs(geo.pixelstorlek_y - px) < 1e-6
        ):
            c0 = int(round((geo.x_min - ox) / px))
            r0 = int(round((oy - geo.y_max) / px))
            rs, re_ = max(0, r0), min(h, r0 + th)
            cs, ce = max(0, c0), min(b, c0 + tb)
            if rs < re_ and cs < ce:
                sub = z[rs - r0 : re_ - r0, cs - c0 : ce - c0]
                ok = np.isfinite(sub)
                ut[rs:re_, cs:ce] = np.where(ok, sub, ut[rs:re_, cs:ce])
                hittad = hittad or bool(np.any(ok))
        else:
            # Allmän omsampling till lagrets cellcentra
            c_min = max(0, int(math.floor((geo.x_min - ox) / px)))
            c_max = min(b, int(math.ceil((geo.x_max - ox) / px)))
            r_min = max(0, int(math.floor((oy - geo.y_max) / px)))
            r_max = min(h, int(math.ceil((oy - geo.y_min) / px)))
            if c_min < c_max and r_min < r_max:
                es = ox + (np.arange(c_min, c_max, dtype=np.float64) + 0.5) * px
                ns = oy - (np.arange(r_min, r_max, dtype=np.float64) + 0.5) * px
                tc = np.clip(np.floor((es - geo.x_min) / geo.pixelstorlek_x).astype(int), 0, tb - 1)
                tr = np.clip(np.floor((geo.y_max - ns) / geo.pixelstorlek_y).astype(int), 0, th - 1)
                sub = z[np.ix_(tr, tc)]
                ok = np.isfinite(sub)
                ut[r_min:r_max, c_min:c_max] = np.where(ok, sub, ut[r_min:r_max, c_min:c_max])
                hittad = hittad or bool(np.any(ok))
    return ut if hittad else None


def _avkoda_gpkg_geom(blob: bytes) -> list[list[tuple[float, float]]]:
    """Avkodar linjer ur en OGC GeoPackage-geometriblob (LineString/MultiLineString/Polygon)."""
    if not blob or len(blob) < 8 or blob[:2] != b"GP":
        return []
    flags = blob[3]
    env_ind = (flags >> 1) & 0x07
    env_bytes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env_ind, 0)
    wkb_start = 8 + env_bytes
    if wkb_start >= len(blob):
        return []
    try:
        from shapely import wkb

        geom = wkb.loads(blob[wkb_start:])
    except Exception:  # noqa: BLE001
        return []
    ut: list[list[tuple[float, float]]] = []
    gtyp = getattr(geom, "geom_type", "")
    if gtyp == "LineString":
        pts = [(float(c[0]), float(c[1])) for c in geom.coords]
        if len(pts) >= 2:
            ut.append(pts)
    elif gtyp == "MultiLineString":
        for ls in geom.geoms:
            pts = [(float(c[0]), float(c[1])) for c in ls.coords]
            if len(pts) >= 2:
                ut.append(pts)
    elif gtyp == "Polygon":
        pts = [(float(c[0]), float(c[1])) for c in geom.exterior.coords]
        if len(pts) >= 2:
            ut.append(pts)
    return ut


def _las_baskarta_linjer(rutor: Sequence[Path]) -> list[list[tuple[float, float]]]:
    """Läser vägkanter/kantstenar ur ``brytlinjer.gpkg`` och ev. ``gator.gpkg`` i K1-rutorna."""
    ut: list[list[tuple[float, float]]] = []
    for rmapp in rutor:
        for filnamn in ("brytlinjer.gpkg", "gator.gpkg"):
            gpkg = rmapp / filnamn
            if not gpkg.is_file():
                continue
            try:
                conn = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
                try:
                    tabeller = [
                        str(r[0])
                        for r in conn.execute(
                            "SELECT table_name FROM gpkg_contents WHERE data_type = 'features'"
                        ).fetchall()
                    ]
                    for tab in tabeller:
                        if not re.fullmatch(r"[A-Za-z0-9_]+", tab):
                            continue
                        geom_kol = conn.execute(
                            "SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?",
                            (tab,),
                        ).fetchone()
                        if not geom_kol:
                            continue
                        gkol = str(geom_kol[0])
                        if not re.fullmatch(r"[A-Za-z0-9_]+", gkol):
                            continue
                        for (blob,) in conn.execute(
                            f'SELECT "{gkol}" FROM "{tab}" WHERE "{gkol}" IS NOT NULL LIMIT 3000'  # noqa: S608
                        ).fetchall():
                            if isinstance(blob, bytes | bytearray):
                                ut.extend(_avkoda_gpkg_geom(bytes(blob)))
                finally:
                    conn.close()
            except (sqlite3.Error, OSError, struct.error):
                continue
    return ut


def _las_flodesvagar(fragor: Any, korning_id: str, scenario: str) -> list[list[tuple[float, float]]]:
    """Läser deterministiska flödesvägar (GeoJSON) ur FragaTjanst om tillgängligt."""
    if not hasattr(fragor, "vektor"):
        return []
    try:
        fc = fragor.vektor(korning_id, scenario, "flodesvagar", min_yta_m2=100.0)
    except Exception:  # noqa: BLE001
        return []
    feats = fc.get("features", []) if isinstance(fc, Mapping) else []
    feats = sorted(
        feats,
        key=lambda f: -float((f.get("properties") or {}).get("uppstroms_yta_m2") or 0.0),
    )[:120]
    ut: list[list[tuple[float, float]]] = []
    for f in feats:
        geom = f.get("geometry") or {}
        gtyp = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtyp == "LineString" and len(coords) >= 2:
            ut.append([(float(p[0]), float(p[1])) for p in coords])
        elif gtyp == "MultiLineString":
            for ls in coords:
                if len(ls) >= 2:
                    ut.append([(float(p[0]), float(p[1])) for p in ls])
    return ut


def _projektpolygon_ring(fragor: Any, korning_id: str) -> list[tuple[float, float]] | None:
    if not hasattr(fragor, "projekt_avgransning"):
        return None
    try:
        avgr = fragor.projekt_avgransning(korning_id)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(avgr, Mapping):
        return None
    gj = avgr.get("polygon_3011") or {}
    coords = gj.get("coordinates") if isinstance(gj, Mapping) else None
    if not coords:
        return None
    ring = coords[0] if gj.get("type") == "Polygon" else (coords[0][0] if gj.get("type") == "MultiPolygon" else None)
    if not ring or len(ring) < 3:
        return None
    return [(float(p[0]), float(p[1])) for p in ring]


def _beskar_lager(
    raster: NDArray[np.float64],
    tak: NDArray[np.bool_] | None,
    vatten: NDArray[np.bool_] | None,
    mark: NDArray[np.float64] | None,
    la: Mapping[str, Any],
    utsnitt: Sequence[float] | None,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.bool_] | None,
    NDArray[np.bool_] | None,
    NDArray[np.float64] | None,
    Any,
]:
    """Beskär raster och hjälpmasker exakt till ``utsnitt`` med nedsampling vid mycket stora raster."""
    from testsvit.k1_io import GeoTransformInfo

    u = la["utbredning"]
    px = float(la["pixelstorlek_m"])
    ox_m, oy_m = float(u["x_min"]), float(u["y_max"])
    hojd_c, bredd_c = raster.shape
    c0, c1, r0, r1 = 0, bredd_c, 0, hojd_c
    if utsnitt is not None and len(utsnitt) == 4:
        x0, y0, x1, y1 = (float(v) for v in utsnitt)
        c0 = max(0, int(math.floor((x0 - ox_m) / px)))
        c1 = min(bredd_c, int(math.ceil((x1 - ox_m) / px)))
        r0 = max(0, int(math.floor((oy_m - y1) / px)))
        r1 = min(hojd_c, int(math.ceil((oy_m - y0) / px)))
        if c1 <= c0 or r1 <= r0:
            c0, c1, r0, r1 = 0, bredd_c, 0, hojd_c

    sub = raster[r0:r1, c0:c1]
    tak_sub = None if tak is None else tak[r0:r1, c0:c1]
    vat_sub = None if vatten is None else vatten[r0:r1, c0:c1]
    mark_sub = None if mark is None else mark[r0:r1, c0:c1]

    faktor = max(1, math.ceil(max(sub.shape) / 1400))
    if faktor > 1:
        sub = sub[::faktor, ::faktor]
        tak_sub = None if tak_sub is None else tak_sub[::faktor, ::faktor]
        vat_sub = None if vat_sub is None else vat_sub[::faktor, ::faktor]
        mark_sub = None if mark_sub is None else mark_sub[::faktor, ::faktor]
    px_bild = px * faktor
    h_c, b_c = sub.shape
    geo = GeoTransformInfo(
        bredd=int(b_c),
        hojd=int(h_c),
        x_min=ox_m + c0 * px,
        y_max=oy_m - r0 * px,
        pixelstorlek_x=px_bild,
        pixelstorlek_y=px_bild,
        epsg=3011,
        dtype="float32",
    )
    return sub, tak_sub, vat_sub, mark_sub, geo


def _detalj_utsnitt(
    *,
    la: Mapping[str, Any],
    byggnader: Sequence[Mapping[str, Any]] | None,
    projektpolygon: Sequence[Sequence[float]] | None,
    mottagare: Sequence[Mottagare],
    oversikt_utsnitt: Sequence[float] | None,
) -> tuple[float, float, float, float] | None:
    """Tät beskärning (+40 m kontext) runt förslagsbyggnader eller projektpolygon när mindre än översikten."""
    u = la["utbredning"]
    rx0, ry0, rx1, ry1 = (float(u[k]) for k in ("x_min", "y_min", "x_max", "y_max"))
    if oversikt_utsnitt is not None and len(oversikt_utsnitt) == 4:
        ox0, oy0, ox1, oy1 = (float(v) for v in oversikt_utsnitt)
    else:
        ox0, oy0, ox1, oy1 = rx0, ry0, rx1, ry1
    ow, oh = max(ox1 - ox0, 1.0), max(oy1 - oy0, 1.0)

    pts: list[tuple[float, float]] = []
    if byggnader:
        for b in byggnader:
            if b.get("status") in ("ny", "andrad"):
                pts.extend((float(p[0]), float(p[1])) for p in b.get("ring", ()))
    if not pts and projektpolygon:
        pts.extend((float(p[0]), float(p[1])) for p in projektpolygon)
    if not pts and mottagare:
        for m in mottagare:
            pts.extend((float(p[0]), float(p[1])) for p in m.polygon)
    if not pts:
        return None

    fx0 = min(p[0] for p in pts) - 40.0
    fx1 = max(p[0] for p in pts) + 40.0
    fy0 = min(p[1] for p in pts) - 40.0
    fy1 = max(p[1] for p in pts) + 40.0

    # Kvadratisk / balanserad detaljruta, minst 80 m sida, inom rastrets utbredning.
    sida = max(80.0, fx1 - fx0, fy1 - fy0)
    cx, cy = (fx0 + fx1) / 2.0, (fy0 + fy1) / 2.0
    max_sida = min(rx1 - rx0, ry1 - ry0)
    sida = min(sida, max_sida)
    dx0 = min(max(cx - sida / 2.0, rx0), rx1 - sida)
    dy0 = min(max(cy - sida / 2.0, ry0), ry1 - sida)
    dx1, dy1 = dx0 + sida, dy0 + sida

    if (dx1 - dx0) >= 0.85 * ow and (dy1 - dy0) >= 0.85 * oh:
        return None
    return (dx0, dy0, dx1, dy1)


def _sampla_tvarsektion(
    *,
    terrang: NDArray[np.float64],
    la: Mapping[str, Any],
    djup_fore: NDArray[np.float64] | None,
    djup_efter: NDArray[np.float64] | None,
    tak: NDArray[np.bool_] | None,
    byggnader: Sequence[Mapping[str, Any]] | None,
    lagpunkter: Sequence[Lagpunkt],
) -> dict[str, Any] | None:
    """Samplar en 1D-tvärsektion (RH 2000) genom djupaste lågpunkt och ev. ny byggnad."""
    from shapely.geometry import Point, Polygon

    u = la["utbredning"]
    ox, oy = float(u["x_min"]), float(u["y_max"])
    rx0, ry0, rx1, ry1 = (float(u[k]) for k in ("x_min", "y_min", "x_max", "y_max"))
    px = float(la["pixelstorlek_m"])
    h, b = terrang.shape

    ny_centra: list[tuple[float, float]] = []
    poly_poster: list[tuple[Polygon, float, bool]] = []
    for bp in byggnader or ():
        ring = bp.get("ring") or ()
        if len(ring) < 3:
            continue
        poly = Polygon([(float(p[0]), float(p[1])) for p in ring])
        if not poly.is_valid or poly.is_empty:
            continue
        ar_ny = bp.get("status") in ("ny", "andrad")
        tak_rh = float(bp.get("takhojd_rh2000") or 0.0)
        poly_poster.append((poly, tak_rh, ar_ny))
        if ar_ny:
            c = poly.centroid
            ny_centra.append((float(c.x), float(c.y)))

    p1 = lagpunkter[0] if lagpunkter else None
    p1_id = p1.id if p1 is not None else "P1"
    if p1 is not None and ny_centra:
        nc = min(ny_centra, key=lambda c: math.hypot(c[0] - p1.e_m, c[1] - p1.n_m))
        d_m = math.hypot(nc[0] - p1.e_m, nc[1] - p1.n_m)
        if 4.0 <= d_m <= 110.0:
            ux, uy = (nc[0] - p1.e_m) / d_m, (nc[1] - p1.n_m) / d_m
            e0, n0 = p1.e_m - ux * 30.0, p1.n_m - uy * 30.0
            e1, n1 = nc[0] + ux * 30.0, nc[1] + uy * 30.0
            undertitel = f"Sektion genom lågpunkt {p1.id} och nytt förslag (SWEREF 99 18 00)"
        else:
            e0, n0 = p1.e_m - 45.0, p1.n_m
            e1, n1 = p1.e_m + 45.0, p1.n_m
            undertitel = f"Väst–östlig sektion genom lågpunkt {p1.id} (SWEREF 99 18 00)"
    elif p1 is not None:
        e0, n0 = p1.e_m - 45.0, p1.n_m
        e1, n1 = p1.e_m + 45.0, p1.n_m
        undertitel = f"Väst–östlig sektion genom lågpunkt {p1.id} (SWEREF 99 18 00)"
    elif ny_centra:
        cx, cy = ny_centra[0]
        e0, n0 = cx - 45.0, cy
        e1, n1 = cx + 45.0, cy
        undertitel = "Väst–östlig sektion genom nytt förslag (SWEREF 99 18 00)"
    else:
        return None

    # Klipp sektionens ändpunkter mot rastrets utbredning.
    e0 = max(rx0 + px, min(rx1 - px, e0))
    e1 = max(rx0 + px, min(rx1 - px, e1))
    n0 = max(ry0 + px, min(ry1 - px, n0))
    n1 = max(ry0 + px, min(ry1 - px, n1))
    langd = math.hypot(e1 - e0, n1 - n0)
    if langd < 10.0:
        return None

    n_steg = 140
    t_arr = np.linspace(0.0, 1.0, n_steg, dtype=np.float64)
    es = e0 + (e1 - e0) * t_arr
    ns = n0 + (n1 - n0) * t_arr
    avst = t_arr * langd

    cs = np.clip(np.floor((es - ox) / px).astype(int), 0, b - 1)
    rs = np.clip(np.floor((oy - ns) / px).astype(int), 0, h - 1)
    mark_s = terrang[rs, cs].astype(np.float64)
    if int(np.count_nonzero(np.isfinite(mark_s))) < 4:
        return None

    bmask = np.zeros(n_steg, dtype=bool)
    bny = np.zeros(n_steg, dtype=bool)
    bhojd = np.full(n_steg, np.nan, dtype=np.float64)
    if tak is not None:
        bmask |= tak[rs, cs]
    if poly_poster:
        for i in range(n_steg):
            pt = Point(float(es[i]), float(ns[i]))
            for poly, tak_rh, ar_ny in poly_poster:
                if poly.contains(pt):
                    bmask[i] = True
                    if ar_ny:
                        bny[i] = True
                    if tak_rh > 0.0:
                        bhojd[i] = tak_rh
                    break

    vf_s: NDArray[np.float64] | None = None
    if djup_fore is not None:
        vf_s = np.where(bmask, 0.0, np.nan_to_num(djup_fore[rs, cs], nan=0.0))
    ve_s: NDArray[np.float64] | None = None
    if djup_efter is not None:
        ve_s = np.where(bmask, 0.0, np.nan_to_num(djup_efter[rs, cs], nan=0.0))

    p1_avst: float | None = None
    if p1 is not None:
        dx, dy = e1 - e0, n1 - n0
        proj = ((p1.e_m - e0) * dx + (p1.n_m - n0) * dy) / max(langd * langd, 1e-9)
        if 0.0 <= proj <= 1.0:
            p1_avst = proj * langd

    return {
        "avstand_m": avst,
        "mark_m": mark_s,
        "vatten_fore_m": vf_s,
        "vatten_efter_m": ve_s,
        "byggnad_mask": bmask if bool(np.any(bmask)) else None,
        "byggnad_hojd_m": bhojd if bool(np.any(np.isfinite(bhojd))) else None,
        "byggnad_ny_mask": bny if bool(np.any(bny)) else None,
        "p1_avstand_m": p1_avst,
        "p1_id": p1_id,
        "undertitel": undertitel,
    }


def _sv(v: Any) -> str:
    try:
        return f"{float(v):g}".replace(".", ",")
    except (TypeError, ValueError):
        return str(v)


__all__ = [
    "AMNEN_NIVA1",
    "KONTRAKT_K33",
    "STANDARD_RAPPORTMAPP",
    "TOMVARDE_TEXT",
    "ExtraFigur",
    "Karta",
    "Mottagare",
    "PlanerarFigurer",
    "Rapport",
    "RapportArkiv",
    "RapportFel",
    "RapportTjanst",
    "ar_ra_uuid",
    "metodnotis_text",
    "sammanfattning",
]
