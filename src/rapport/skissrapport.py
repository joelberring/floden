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

import json
import os
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from floden_mcp.dataklass import DataKlass, Policy
from floden_mcp.karna_api import FlodenFel, FlodenTjanst
from floden_mcp.korlogg import Korlogg, hash_av
from gemensamt.hash import sha256_bytes, sha256_fil
from rapport.amnestext import EKOLOGI_ENHET, TITEL, bildtext_lagrad, tomvarde_lagrad
from rapport.amnestext import kartrubrik as kartrubrik_text
from rapport.belaggskalla import BelaggFel, FlodenBelaggKalla
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
AMNEN_NIVA1: Final[tuple[str, ...]] = ("skyfall", "dagsljus", "ekologi")
"""Ämnen med skissrapport: skyfall (R1), dagsljus/sol och ekologi (R2). Ordning enligt Joel 2026-11-22:
skyfall, dagsljus/sol, luft, buller."""
_AMNESDOMAN: Final[dict[str, str]] = {"skyfall": "vatten", "dagsljus": "dagsljus", "ekologi": "ekologi"}
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


def metodnotis_text(
    notis: Mapping[str, Any],
    *,
    sbk: bool = False,
    korningar: Sequence[str] = (),
    bilaga: str = "bilaga",
) -> str:
    """Metodnotisen som den står i rapporten (förval enligt Joels beslut 2026-11-22).

    `sbk` ger språkprofilen "sbk": ursprunget (Floden, körningar, AI-stöd) i klarspråk."""
    modeller = [m for m in notis.get("modeller", []) if m]
    ansvarig = notis.get("ansvarig_utredare")
    if sbk:
        bas = "Underlaget är framtaget med beräkningsverktyget Floden"
        if korningar:
            bas += f" (beräkning {', '.join(korningar)})"
        if notis.get("ai_stod", True):
            bas += " och AI-stöd" + (f" ({', '.join(modeller)})" if modeller else "") + "."
            bas += (
                " AI har använts för att välja beräkningar och formulera texten. Alla tal och "
                "kartor kommer ur beräkningarna och har kontrollerats automatiskt mot dem."
            )
        else:
            bas += (
                ". AI har inte använts: texten följer fasta formuleringar. Alla tal och kartor "
                "kommer ur beräkningarna och har kontrollerats automatiskt mot dem."
            )
        bas += f" Körloggen finns i {bilaga}."
        bas += (
            f" Ansvarig utredare: {ansvarig}."
            if ansvarig
            else " Ansvarig utredare är inte angiven."
        )
        return bas
    if notis.get("ai_stod", True):
        bas = "Underlaget har tagits fram med Floden och AI-stöd"
        bas += f" ({', '.join(modeller)})." if modeller else "."
        bas += (
            " AI:n har valt körningar och skrivit text; alla tal och kartor kommer ur Flodens "
            "körningar och har kontrollerats automatiskt mot dem."
        )
    else:
        bas = (
            "Underlaget har tagits fram med Floden utan AI-stöd (referensflöde med fasta "
            "formuleringar). Alla tal och kartor kommer ur Flodens körningar och har "
            "kontrollerats automatiskt mot dem."
        )
    bas += " Körloggen finns som bilaga."
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


class RapportTjanst:
    """Skapa rapporter, lägg till påståenden, kontrollera och exportera (enda vägen in)."""

    def __init__(
        self,
        tjanst: FlodenTjanst,
        *,
        rapportmapp: str | Path | None = None,
        policy: Policy | None = None,
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
        self.policy = policy or Policy()
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

    def _korningspost(
        self, korning_id: str, amne: str, scenario: Mapping[str, Any] | None = None
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
        if scenario is not None:
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
        antaganden = _antaganden(res, [t["analys"] for t in analyser])
        if amne == "ekologi":
            from rapport.ekologibelagg import ekologiantaganden

            antaganden = ekologiantaganden(
                self.kalla,
                korning_id,
                {
                    t["analys"]: sorted({r["scenario"] for r in t["rader"]})
                    for t in analyser
                    if t["losare"] == "ekologi.konnektivitet"
                },
            )
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

    def _metoder(self, korningar: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
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
        return list(losare.values())

    def _kartor(
        self, rapport_id: str, amne: str, korningar: Sequence[Mapping[str, Any]]
    ) -> list[Karta]:
        mapp = self.arkiv.rapportmapp(rapport_id) / "kartor"
        mapp.mkdir(parents=True, exist_ok=True)
        ut: list[Karta] = []
        gjorda_bas: set[tuple[Any, ...]] = set()
        for k in korningar:
            res = self.kalla._resultat(k["korning_id"])  # noqa: SLF001
            losare_per_analys = {a["analys"]: a["losare"] for a in k["analyser"]}
            for j in k["jamforelser"]:
                utsnitt = kartutsnitt(res, j)
                for typ, scen in (("bas", j["fore"]), ("scenario", j["efter"]), ("skillnad", j["efter"])):
                    if typ == "bas":
                        # Före-kartan är densamma för alla alternativ med samma utsnitt: en bild.
                        nyckel = (k["korning_id"], j["analys"], j["fore"], tuple(utsnitt or ()))
                        if nyckel in gjorda_bas:
                            continue
                        gjorda_bas.add(nyckel)
                    try:
                        png, meta = self.tjanst.karta(
                            k["korning_id"],
                            j["analys"],
                            typ=typ,
                            scenario=scen,
                            utsnitt=utsnitt,
                            max_px=KART_PX,
                            rubrik=kartrubrik_text(amne, typ, scen, j["fore"], j["efter"]),
                        )
                    except FlodenFel as exc:
                        raise RapportFel(f"Kartan kunde inte ritas: {exc}") from exc
                    kart_id = f"{j['namn']}.{typ}"
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
                                amne, typ, j, meta, losare_per_analys.get(j["analys"], ""),
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
                            | ({"enhet": EKOLOGI_ENHET} if amne == "ekologi" else {})
                            | {
                                "utsnitt": utsnitt,
                                "rubrik": kartrubrik_text(amne, typ, scen, j["fore"], j["efter"]),
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
            if amne in ("luft", "buller"):
                extra = (
                    " Luft och buller har inga körningar med belägg via FlodenTjanst än "
                    "(docs/RAPPORTER.md, avsnittet om luft och buller)"
                    + ("; Nord2000 är inte konformt och spärrat för rapport." if amne == "buller" else ".")
                )
            raise RapportFel(
                f"Ämnet '{amne}' har ingen skissrapport än. Finns: {', '.join(AMNEN_NIVA1)} "
                f"(ordning: skyfall, dagsljus/sol, ekologi, luft, buller).{extra}"
            )
        if not korningar:
            raise RapportFel("Ange minst en körning (korningar).")
        if not self.tjanst.rot.is_dir():
            raise RapportFel("Flodens rotmapp finns inte.")
        self.tjanst._kravs_projekt(projekt)  # noqa: SLF001 - samma regel som webben
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
            self._korningspost(k, amne, None if best is None else best.scenario)
            for k in dict.fromkeys(korningar)
        ]
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
        rapport = Rapport(
            rapport_id=rid,
            niva=niva,
            amne=amne,
            projekt=projekt,
            titel=titel or f"{TITEL[amne]} – {projekt}",
            skapad=skapad,
            fragor=tuple(f.id for f in valda),
            korningar=tuple(posts),
            mottagare=mott,
            pastaenden=(),
            kartor=tuple(self._kartor(rid, amne, posts)),
            metoder=tuple(self._metoder(posts)),
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
        self.tjanst._kravs_projekt(projekt)  # noqa: SLF001 - samma regel som webben
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
        namn = f"export/{rapport_id}.{'docx' if fmt == 'word' else 'pdf'}"
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
        post: dict[str, Any] = {
            "format": fmt,
            "fil": namn,
            "sha256": sha256_fil(ut),
            "tid": _nu(),
        }
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

    def adresser(self, r: Rapport) -> list[str]:
        """Beläggsadresser som går att använda i rapporten (jämförelser, mätvärden, mottagare)."""
        from rapport.belaggskalla import (
            JAMFORELSEMATT,
            OMRADESSTATISTIK,
            VATTEN_MATVARDEN,
        )

        if r.amne == "dagsljus":
            return self._adresser_dagsljus(r)
        if r.amne == "ekologi":
            return self._adresser_ekologi(r)
        ut: list[str] = []
        for k in r.korningar:
            for j in k["jamforelser"]:
                ut += [f"jamforelse.{j['namn']}.{m}" for m in JAMFORELSEMATT]
            for a in k["analyser"]:
                for s in a["scenarier"]:
                    ut += [f"matvarde.{a['analys']}.{s}.{n}" for n in VATTEN_MATVARDEN]
            for m in r.mottagare:
                for j in k["jamforelser"]:
                    for s in (j["fore"], j["efter"]):
                        ut += [
                            f"omrade.{m.namn}.{j['analys']}.{s}.{j['utdata']}.{st}"
                            for st in ("max", "medel")
                        ]
                    ut += [f"omrade.{m.namn}.skillnad.{j['namn']}.{st}" for st in OMRADESSTATISTIK]
        return list(dict.fromkeys(ut))

    def _adresser_ekologi(self, r: Rapport) -> list[str]:
        """Beläggsadresser för ekologi (se `rapport.ekologibelagg` och `belaggskalla.EKOLOGI_MATT`)."""
        from rapport.belaggskalla import EKOLOGI_MATT
        from rapport.ekologibelagg import DELTAMATT, NATVERKSPARAMETRAR

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
        return list(dict.fromkeys(ut))

    def _adresser_dagsljus(self, r: Rapport) -> list[str]:
        """Beläggsadresser för dagsljus och sol (se `rapport.fasadbelagg` och `belaggskalla`)."""
        from rapport.fasadbelagg import JAMFORMATT, PARAMETRAR, PUNKTMATT

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
        return list(dict.fromkeys(ut))

    def lista(self, projekt: str | None = None) -> list[dict[str, Any]]:
        return [sammanfattning(r, self.arkiv) for r in self.arkiv.lista(projekt)]


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


def _antaganden(res: Mapping[str, Any], analyser: Iterable[str]) -> list[str]:
    """Antaganden som Floden själv har skrivit (regn, takvatten, avtappning; för dagsljus datum,
    tidssteg och avgränsningar ur lösarnas parametrar) för analyserna."""
    valda = set(analyser)
    ut: list[str] = []
    for a in res.get("antaganden", {}).get("analyser", []):
        if a.get("analys") in valda and str(a.get("losare", "")).startswith("dagsljus."):
            ut.append(_dagsljusantagande(a))
    for r in res.get("antaganden", {}).get("regn", []):
        if r.get("analys") in valda:
            ut.append(
                f"Regn: {r.get('rapport')} ({r.get('tabell')}), återkomsttid "
                f"{_sv(r.get('aterkomsttid_ar'))} år, varaktighet {_sv(r.get('varaktighet_min'))} "
                f"min, {r.get('regntyp')}regn, klimatfaktor {_sv(r.get('klimatfaktor'))} "
                f"(uttrycklig parameter), {_sv(round(float(r.get('regn_mm', 0.0)), 1))} mm."
            )
    for v in res.get("antaganden", {}).get("vatten", []):
        if v.get("analys") in valda:
            for nyckel in ("takvatten_text", "avtappning_text"):
                if v.get(nyckel):
                    ut.append(str(v[nyckel]) + ".")
    return list(dict.fromkeys(ut))


def _dagsljusantagande(a: Mapping[str, Any]) -> str:
    """Dagsljuslösarens parametrar som en mening (Floden skriver, ur K0-manifestets parametrar)."""
    from rapport.sprak import datum_text

    p = a.get("parametrar", {})
    losare = str(a["losare"])
    delar: list[str] = []
    if losare == "dagsljus.soltimmar_mark":
        datum = p.get("datum") or []
        delar.append(
            "Soltimmar på mark för "
            + ", ".join(datum_text(str(d), True) for d in datum)
            + f" (tidssteg {_sv(p.get('steg_min'))} min, {_sv(p.get('azimutriktningar'))} "
            "riktningar)"
        )
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
    "Karta",
    "Mottagare",
    "Rapport",
    "RapportArkiv",
    "RapportFel",
    "RapportTjanst",
    "metodnotis_text",
    "sammanfattning",
]
