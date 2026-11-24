# ruff: noqa: E501
"""K32 – Påstående och belägg: rapportens tal är typade platshållare som Floden fyller (R1).

Ett **påstående** är en mening (eller några) som besvarar en fråga ur frågekatalogen, med status,
fördjupningsbehov och **belägg**. Ett **belägg** binder ett tal till en körning: körnings-id,
K0-manifest och dess sha256, resultatfil, mottagare och nämnare (område), mått, enhet,
tidsdefinition, statistik och värdet före avrundning. Belägg skapas bara av Floden
(`rapport.belaggskalla`), aldrig av AI:n: AI:n anger beläggets *adress*.

Tal i texten skrivs som platshållare, aldrig som siffror:

    {{matt:jamforelse.skyfall_max_djup_efter_minus_fore.yta_okning_m2|avrundning=10}}

Platshållaren pekar på ett belägg i påståendet (adressen). Floden fyller värdet med enhet och en
precision som följer underlaget (`avrundning` får inte vara finare än beläggets upplösning).
Format: se `docs/DATAKONTRAKT.md` (K32) och `docs/RAPPORTER.md`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

KONTRAKT_K32: Final[str] = "K32/v1"

STATUSAR: Final[tuple[str, ...]] = (
    "visat",
    "avvikelse",
    "villkorat",
    "underlag_saknas",
    "ej_tillampligt",
)
STATUS_TEXT: Final[dict[str, str]] = {
    "visat": "Visat",
    "avvikelse": "Avvikelse",
    "villkorat": "Villkorat",
    "underlag_saknas": "Underlag saknas",
    "ej_tillampligt": "Ej tillämpligt",
}
FORDJUPNING: Final[tuple[str, ...]] = ("nej", "ja", "kan_inte_avgoras")
FORDJUPNING_TEXT: Final[dict[str, str]] = {
    "nej": "Ingen fördjupning i detta skede",
    "ja": "Behöver fördjupas",
    "kan_inte_avgoras": "Kan inte avgöras",
}
TRAFIKLJUS: Final[dict[str, str]] = {"nej": "grön", "ja": "gul", "kan_inte_avgoras": "röd"}
"""Trafikljuset per fråga i skissunderlaget svarar på "behöver frågan fördjupas?"."""

FORFATTARE: Final[tuple[str, ...]] = ("ai", "manniska", "referensflode")

_PLATS_RE: Final[re.Pattern[str]] = re.compile(
    r"\{\{\s*([a-z_]+)\s*:\s*([^}|]+?)\s*((?:\|[^}]*)?)\}\}"
)
_SIFFRA_RE: Final[re.Pattern[str]] = re.compile(r"\d")
# Beteckningar som innehåller siffror men inte är tal ur en körning.
_TILLATNA_BETECKNINGAR: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b[A-Z]{2}-\d{2}\b"),  # frågans id, t.ex. SK-06
    re.compile(r"\bEPSG\s*:\s*3011\b", re.IGNORECASE),
    re.compile(r"\bSWEREF\s*99\s*18\s*00\b", re.IGNORECASE),
    re.compile(r"\bRH\s*2000\b", re.IGNORECASE),
    re.compile(r"\bK\d{1,2}(?:/v\d+)?\b"),  # kontrakt (K7, K0/v1)
    re.compile(r"\b(?:PBL|BBR)\s+\d+\s+kap\.(?:\s*\d+\s*[a-z]?\s*§+)?", re.IGNORECASE),
    re.compile(r"\bStockholmsregn\s+2024\b"),
    re.compile(r"\bTabell\s+\d+\b"),
    re.compile(r"\bd50\b"),  # spridningsavståndet där sannolikheten är hälften (ekologi)
    re.compile(r"\bNMD\s*\d{1,3}\b"),  # NMD-klasskod (identifierare, inte ett tal)
    re.compile(r"\bPM\s?(?:10|2,5)\b"),  # partikelfraktion (beteckning, inte ett tal)
)
_OPTIONER: Final[frozenset[str]] = frozenset({"avrundning", "enhet", "tecken", "format"})


class PastaendeFel(Exception):
    """Ett påstående eller belägg är ogiltigt. Meddelandet är skrivet för den som skrev det."""


# ---------------------------------------------------------------------- belägg


@dataclass(frozen=True)
class Belagg:
    """Ett tal ur en körning med allt som behövs för att spåra och tolka det (K32).

    Fält för nivå 2 (bedömning mot krav) finns förberedda: `bedomningsgrund` (källa, paragraf,
    tabell) är `None` i nivå 1.
    """

    adress: str
    korning_id: str
    fil: str
    fil_sha256: str | None
    manifest: str
    manifest_sha256: str
    analys: str
    losare: str
    scenarier: tuple[str, ...]
    mottagare: str
    namnare: str
    matt: str
    storhet: str
    enhet: str | None
    tidsdefinition: str
    statistik: str
    varde: float
    upplosning: float
    jamforbarhet: str
    """sha256 av analysens parametrar (regn, avtappning, takvatten) utan scenariots nyckel."""
    regn: str | None = None
    bedomningsgrund: dict[str, Any] | None = None

    def till_dict(self) -> dict[str, Any]:
        return {
            "adress": self.adress,
            "korning_id": self.korning_id,
            "fil": self.fil,
            "fil_sha256": self.fil_sha256,
            "manifest": self.manifest,
            "manifest_sha256": self.manifest_sha256,
            "analys": self.analys,
            "losare": self.losare,
            "scenarier": list(self.scenarier),
            "mottagare": self.mottagare,
            "namnare": self.namnare,
            "matt": self.matt,
            "storhet": self.storhet,
            "enhet": self.enhet,
            "tidsdefinition": self.tidsdefinition,
            "statistik": self.statistik,
            "varde": self.varde,
            "upplosning": self.upplosning,
            "jamforbarhet": self.jamforbarhet,
            "regn": self.regn,
            "bedomningsgrund": self.bedomningsgrund,
        }

    @classmethod
    def fran_dict(cls, d: Mapping[str, Any]) -> Belagg:
        try:
            return cls(
                adress=str(d["adress"]),
                korning_id=str(d["korning_id"]),
                fil=str(d["fil"]),
                fil_sha256=None if d.get("fil_sha256") is None else str(d["fil_sha256"]),
                manifest=str(d["manifest"]),
                manifest_sha256=str(d["manifest_sha256"]),
                analys=str(d["analys"]),
                losare=str(d["losare"]),
                scenarier=tuple(str(s) for s in d["scenarier"]),
                mottagare=str(d["mottagare"]),
                namnare=str(d["namnare"]),
                matt=str(d["matt"]),
                storhet=str(d["storhet"]),
                enhet=None if d.get("enhet") is None else str(d["enhet"]),
                tidsdefinition=str(d["tidsdefinition"]),
                statistik=str(d["statistik"]),
                varde=float(d["varde"]),
                upplosning=float(d["upplosning"]),
                jamforbarhet=str(d["jamforbarhet"]),
                regn=None if d.get("regn") is None else str(d["regn"]),
                bedomningsgrund=d.get("bedomningsgrund"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PastaendeFel(f"Belägget är ofullständigt: {exc}.") from exc


# ---------------------------------------------------------------------- platshållare


@dataclass(frozen=True)
class Platshallare:
    """En typad platshållare i en text: `{{matt:<adress>|avrundning=10|enhet=nej}}`."""

    typ: str
    adress: str
    optioner: dict[str, str]
    start: int
    slut: int


def las_platshallare(text: str) -> list[Platshallare]:
    """Platshållarna i texten. Okänd typ eller option ger `PastaendeFel`."""
    ut: list[Platshallare] = []
    for m in _PLATS_RE.finditer(text):
        typ, adress, rest = m.group(1), m.group(2).strip(), m.group(3)
        if typ != "matt":
            raise PastaendeFel(
                f"Okänd platshållartyp '{typ}' i {m.group(0)}. Tal skrivs som "
                "{{matt:<beläggsadress>}}."
            )
        optioner: dict[str, str] = {}
        for del_ in [d for d in rest.split("|") if d.strip()]:
            if "=" not in del_:
                raise PastaendeFel(f"Optionen '{del_.strip()}' i {m.group(0)} saknar '='.")
            k, v = (s.strip() for s in del_.split("=", 1))
            if k not in _OPTIONER:
                raise PastaendeFel(
                    f"Okänd option '{k}' i {m.group(0)}. Tillåtna: "
                    + ", ".join(sorted(_OPTIONER))
                    + "."
                )
            optioner[k] = v
        ut.append(Platshallare(typ, adress, optioner, m.start(), m.end()))
    rest_text = _PLATS_RE.sub(" ", text)
    if "{{" in rest_text or "}}" in rest_text:
        raise PastaendeFel("Texten har en trasig platshållare (obalanserade {{ }}).")
    return ut


def text_utan_platshallare(text: str) -> str:
    return _PLATS_RE.sub(" ", text)


def siffror_utanfor_platshallare(text: str) -> list[str]:
    """Siffror i texten som inte står i en platshållare eller en tillåten beteckning."""
    rest = text_utan_platshallare(text)
    for monster in _TILLATNA_BETECKNINGAR:
        rest = monster.sub(" ", rest)
    return [m.group(0) for m in re.finditer(r"[-+−]?\d+(?:[.,]\d+)?", rest)]


def _tolka_steg(s: str) -> float:
    try:
        v = float(s.replace(",", ".").replace("−", "-"))
    except ValueError as exc:
        raise PastaendeFel(f"avrundning='{s}' är inget tal.") from exc
    if not (v > 0 and math.isfinite(v)):
        raise PastaendeFel(f"avrundning='{s}' måste vara större än noll.")
    # bara 1, 2, 5 gånger en tiopotens (som en avläsning)
    exp = math.floor(math.log10(v))
    mant = round(v / 10**exp, 6)
    if mant not in (1.0, 2.0, 5.0):
        raise PastaendeFel(f"avrundning='{s}' ska vara 1, 2 eller 5 gånger en tiopotens.")
    return v


def standardsteg(enhet: str | None, varde: float, upplosning: float) -> float:
    """Förvald avrundning: efter enhet och storlek, aldrig finare än underlagets upplösning."""
    a = abs(varde)
    tabell: dict[str | None, float] = {
        "m": 0.01,
        "mm": 0.1,
        "mm/h": 0.1,
        "år": 1.0,
        "min": 1.0,
        "h": 0.1,
        "grader": 1.0,
        "%": 1.0,
        "st": 1.0,
    }
    if enhet in ("m²", "m³"):
        steg = 10.0 if a >= 1000 else 1.0
    elif enhet == "andel":
        steg = 0.01
    else:
        steg = tabell.get(enhet, 0.01)
    return max(steg, minsta_steg(upplosning))


def minsta_steg(upplosning: float) -> float:
    """Minsta tillåtna avrundning: upplösningen avrundad uppåt till 1, 2 eller 5 · 10^n."""
    if upplosning <= 0:
        return 0.0
    exp = math.floor(math.log10(upplosning))
    for m in (1.0, 2.0, 5.0, 10.0):
        if m * 10**exp >= upplosning * (1 - 1e-9):
            return float(m * 10**exp)
    return float(10 ** (exp + 1))


def _gruppera(heltal: str) -> str:
    """Tusentalsavgränsning med smalt hårt mellanslag (U+202F) från fyra siffror: "7 430".

    Hårt, så att talet aldrig bryts över två rader; smalt, som i tryckt svensk sifferform."""
    if len(heltal) < 4:
        return heltal
    delar = []
    while heltal:
        delar.append(heltal[-3:])
        heltal = heltal[:-3]
    return "\u202f".join(reversed(delar))


_UPPHOJT: Final[dict[str, str]] = dict(zip("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹", strict=True))


def formatera_tal(
    varde: float,
    enhet: str | None,
    steg: float,
    *,
    visa_enhet: bool = True,
    tecken: bool = False,
    exponent: bool = False,
) -> str:
    """Svensk talform: decimalkomma, smalt hårt mellanslag mellan tusental, hårt blanksteg före
    enheten ("7 430 m³")."""
    if not math.isfinite(varde):
        raise PastaendeFel("Värdet är inte ändligt.")
    if exponent:
        if varde == 0:
            text = "0"
        else:
            exp = math.floor(math.log10(abs(varde)))
            mant = varde / 10**exp
            text = (
                f"{mant:.1f}".replace(".", ",") + " × 10" + "".join(_UPPHOJT[c] for c in str(exp))
            )
    else:
        avrundat = round(varde / steg) * steg
        dec = max(0, -math.floor(math.log10(steg) + 1e-9))
        text = f"{abs(avrundat):.{dec}f}"
        heltal, _, decimaler = text.partition(".")
        text = _gruppera(heltal) + ("," + decimaler if decimaler else "")
        noll = float(f"{avrundat:.{dec}f}") == 0.0
        if avrundat < 0 and not noll:
            text = "−" + text
        elif tecken and avrundat > 0:
            text = "+" + text
    if visa_enhet and enhet and enhet != "andel":
        text += " " + enhet
    return text


def fyll_text(text: str, belagg: Mapping[str, Belagg]) -> str:
    """Ersätter varje platshållare med Flodens värde. Okänd adress eller för fin precision ger fel."""
    ut: list[str] = []
    pos = 0
    for p in las_platshallare(text):
        b = belagg.get(p.adress)
        if b is None:
            raise PastaendeFel(
                f"Platshållaren {{{{matt:{p.adress}}}}} pekar inte på något av påståendets belägg."
            )
        ut.append(text[pos : p.start])
        ut.append(fyll_platshallare(p, b))
        pos = p.slut
    ut.append(text[pos:])
    return "".join(ut)


def fyll_platshallare(p: Platshallare, b: Belagg) -> str:
    fmt = p.optioner.get("format", "")
    if fmt not in ("", "exp"):
        raise PastaendeFel(f"format='{fmt}' är okänt (bara 'exp').")
    if "avrundning" in p.optioner:
        steg = _tolka_steg(p.optioner["avrundning"])
        minst = minsta_steg(b.upplosning)
        if steg < minst * (1 - 1e-9):
            raise PastaendeFel(
                f"avrundning={p.optioner['avrundning']} för {p.adress} är finare än underlaget "
                f"(upplösning {b.upplosning:g} {b.enhet or ''}): "
                "precisionen ska följa underlaget."
            )
    else:
        steg = standardsteg(b.enhet, b.varde, b.upplosning)
    enhet_opt = p.optioner.get("enhet", "ja")
    if enhet_opt not in ("ja", "nej"):
        if enhet_opt != (b.enhet or ""):
            raise PastaendeFel(
                f"Platshållaren för {p.adress} anger enheten '{enhet_opt}', men belägget har "
                f"'{b.enhet}'."
            )
        enhet_opt = "ja"
    return formatera_tal(
        b.varde,
        b.enhet,
        steg,
        visa_enhet=enhet_opt == "ja",
        tecken=p.optioner.get("tecken", "nej") == "ja",
        exponent=fmt == "exp",
    )


# ---------------------------------------------------------------------- påstående


@dataclass(frozen=True)
class Pastaende:
    """Ett påstående (K32) med status, fördjupningsbehov och belägg."""

    pastaende_id: str
    fraga_id: str
    status: str
    text: str
    belagg: tuple[Belagg, ...]
    fordjupning: str
    mottagare: str | None = None
    forutsattning: str | None = None
    ansvarig_funktion: str | None = None
    motivering: str | None = None
    saknat_underlag: tuple[str, ...] = ()
    forfattare: str = "ai"
    modell: str | None = None
    tid: str = ""
    # nivå 2 (förberett): bedömning mot krav och granskning
    bedomningsgrund: dict[str, Any] | None = None
    granskning: dict[str, Any] | None = None
    kontrakt: str = KONTRAKT_K32
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def belagg_per_adress(self) -> dict[str, Belagg]:
        return {b.adress: b for b in self.belagg}

    def fylld_text(self) -> str:
        return fyll_text(self.text, self.belagg_per_adress)

    def till_dict(self) -> dict[str, Any]:
        return {
            "kontrakt": self.kontrakt,
            "pastaende_id": self.pastaende_id,
            "fraga_id": self.fraga_id,
            "status": self.status,
            "text": self.text,
            "belagg": [b.till_dict() for b in self.belagg],
            "fordjupning": self.fordjupning,
            "mottagare": self.mottagare,
            "forutsattning": self.forutsattning,
            "ansvarig_funktion": self.ansvarig_funktion,
            "motivering": self.motivering,
            "saknat_underlag": list(self.saknat_underlag),
            "forfattare": self.forfattare,
            "modell": self.modell,
            "tid": self.tid,
            "bedomningsgrund": self.bedomningsgrund,
            "granskning": self.granskning,
        }

    @classmethod
    def fran_dict(cls, d: Mapping[str, Any]) -> Pastaende:
        if d.get("kontrakt") != KONTRAKT_K32:
            raise PastaendeFel(f"Påståendet har kontraktet {d.get('kontrakt')!r}, väntade K32/v1.")
        try:
            return cls(
                pastaende_id=str(d["pastaende_id"]),
                fraga_id=str(d["fraga_id"]),
                status=str(d["status"]),
                text=str(d["text"]),
                belagg=tuple(Belagg.fran_dict(b) for b in d.get("belagg", [])),
                fordjupning=str(d["fordjupning"]),
                mottagare=d.get("mottagare"),
                forutsattning=d.get("forutsattning"),
                ansvarig_funktion=d.get("ansvarig_funktion"),
                motivering=d.get("motivering"),
                saknat_underlag=tuple(str(s) for s in d.get("saknat_underlag", [])),
                forfattare=str(d.get("forfattare", "ai")),
                modell=d.get("modell"),
                tid=str(d.get("tid", "")),
                bedomningsgrund=d.get("bedomningsgrund"),
                granskning=d.get("granskning"),
            )
        except KeyError as exc:
            raise PastaendeFel(f"Påståendet saknar fältet {exc}.") from exc

    def med(self, **andringar: Any) -> Pastaende:
        return replace(self, **andringar)


def _tom(s: str | None) -> bool:
    return s is None or not str(s).strip()


def statusfel(p: Pastaende) -> list[str]:
    """Statusreglerna (rapportstod.md). Tom lista = reglerna följs."""
    fel: list[str] = []
    pid = f"{p.pastaende_id} ({p.fraga_id})"
    if p.status not in STATUSAR:
        fel.append(f"{pid}: okänd status '{p.status}'. Tillåtna: {', '.join(STATUSAR)}.")
        return fel
    if p.fordjupning not in FORDJUPNING:
        fel.append(
            f"{pid}: okänt fördjupningsbehov '{p.fordjupning}'. Tillåtna: {', '.join(FORDJUPNING)}."
        )
    if _tom(p.text):
        fel.append(f"{pid}: texten är tom.")
    if p.status in ("visat", "avvikelse") and not p.belagg:
        fel.append(
            f"{pid}: status '{p.status}' kräver minst ett belägg ur en körning; utan belägg är "
            "status 'underlag saknas'."
        )
    if p.status in ("visat", "avvikelse") and not las_platshallare(p.text):
        fel.append(
            f"{pid}: status '{p.status}' kräver att texten redovisar minst ett tal ur beläggen "
            "(platshållare {{matt:...}})."
        )
    if p.status == "villkorat" and (_tom(p.forutsattning) or _tom(p.ansvarig_funktion)):
        fel.append(
            f"{pid}: status 'villkorat' kräver en uttrycklig förutsättning och den funktion som "
            "ansvarar för den (forutsattning, ansvarig_funktion)."
        )
    if p.status == "ej_tillampligt" and _tom(p.motivering):
        fel.append(f"{pid}: status 'ej tillämpligt' kräver en saklig motivering.")
    if p.status == "underlag_saknas" and not p.saknat_underlag:
        fel.append(f"{pid}: status 'underlag saknas' kräver att det saknade underlaget anges.")
    if p.status == "underlag_saknas" and p.fordjupning == "nej":
        fel.append(
            f"{pid}: när underlag saknas kan sållningen inte säga att ingen fördjupning behövs "
            "(fordjupning 'ja' eller 'kan_inte_avgoras')."
        )
    if p.status == "avvikelse" and p.fordjupning != "ja":
        fel.append(f"{pid}: en avvikelse behöver fördjupas (fordjupning 'ja').")
    if p.status == "ej_tillampligt" and p.fordjupning != "nej":
        fel.append(f"{pid}: en fråga som inte är tillämplig fördjupas inte (fordjupning 'nej').")
    if p.status == "visat" and p.fordjupning == "kan_inte_avgoras":
        fel.append(
            f"{pid}: 'visat' och 'kan inte avgöras' motsäger varandra; välj 'underlag saknas'."
        )
    if p.forfattare not in FORFATTARE:
        fel.append(f"{pid}: okänd författare '{p.forfattare}'.")
    return fel


def textfel(p: Pastaende) -> list[str]:
    """Tal i texten: bara platshållare, och varje platshållare pekar på ett belägg."""
    fel: list[str] = []
    pid = f"{p.pastaende_id} ({p.fraga_id})"
    for falt, varde in (
        ("text", p.text),
        ("forutsattning", p.forutsattning),
        ("motivering", p.motivering),
        *(("saknat_underlag", s) for s in p.saknat_underlag),
    ):
        if not varde:
            continue
        try:
            platser = las_platshallare(varde)
        except PastaendeFel as exc:
            fel.append(f"{pid}: {exc}")
            continue
        if falt != "text" and platser:
            fel.append(f"{pid}: platshållare får bara stå i texten, inte i '{falt}'.")
        siffror = siffror_utanfor_platshallare(varde)
        if siffror:
            fel.append(
                f"{pid}: talet {', '.join(repr(s) for s in siffror)} i '{falt}' kommer inte ur "
                "något belägg. Skriv tal som platshållare {{matt:<adress>}} så att Floden fyller "
                "dem; AI:n och skribenten äger inga tal."
            )
    adresser = {b.adress for b in p.belagg}
    try:
        for pl in las_platshallare(p.text):
            if pl.adress not in adresser:
                fel.append(f"{pid}: platshållaren för '{pl.adress}' saknar belägg i påståendet.")
    except PastaendeFel:
        pass
    return fel


def ordna_fragestatus(statusar: Sequence[str]) -> str:
    """Frågans samlade status när den har flera påståenden (den allvarligaste vinner)."""
    ordning = ["avvikelse", "underlag_saknas", "villkorat", "visat", "ej_tillampligt"]
    for s in ordning:
        if s in statusar:
            return s
    return "underlag_saknas"


def ordna_fordjupning(varden: Sequence[str]) -> str:
    for s in ("kan_inte_avgoras", "ja", "nej"):
        if s in varden:
            return s
    return "kan_inte_avgoras"


__all__ = [
    "FORDJUPNING",
    "FORDJUPNING_TEXT",
    "FORFATTARE",
    "KONTRAKT_K32",
    "STATUSAR",
    "STATUS_TEXT",
    "TRAFIKLJUS",
    "Belagg",
    "Pastaende",
    "PastaendeFel",
    "Platshallare",
    "formatera_tal",
    "fyll_platshallare",
    "fyll_text",
    "las_platshallare",
    "minsta_steg",
    "ordna_fordjupning",
    "ordna_fragestatus",
    "siffror_utanfor_platshallare",
    "standardsteg",
    "statusfel",
    "textfel",
]
