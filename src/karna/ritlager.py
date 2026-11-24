# ruff: noqa: E501
"""Ritlager (K50 `floden-ritlager/v1`): generella lager för ritverktyget, med roll i beräkningen.

Ett ritlager är en samling objekt av samma geometrityp och roll i ett planförslag (R2 i
`docs/design/RITVERKTYG.md`). **Rollen, inte ett symbolnummer, avgör vad ett objekt blir i K9/K1**;
översättningen (`oversatt`) är en enda funktion i Python som servern kör när förslaget kontrolleras,
sparas och körs, så att webben, MCP och en handskriven fil ger samma K9 (och samma nyckel).

| Roll | Geometri | Blir i beräkningen |
| --- | --- | --- |
| `byggnad` | polygon | K9 `byggnad_ny` (`hojd_m` eller `tak_rh2000`; valfria `takform`, `andamal`, `vaningar`) |
| `mark` | polygon | K9 `markhojd`: exakt en av `niva_rh2000`, `lutning`, `delta_m`; valfria `slant`, `osakerhet_m`, `markslag` (R4) -> K3 `HojdYta` |
| `hardgjort` | polygon | K9 `markslag` kod 1 (hårdgjort); med `niva_rh2000` i stället `markhojd` med `markslag` 1 |
| `vegetation` | polygon | K9 `vegetation` (`hojd_m`) och/eller `markslag` (4 gräs, 5 skog) |
| `vatten` | polygon | K9 `markslag` kod 8 (vatten); med `volym_m3` i stället `magasin` |
| `brytlinje` | linje | K9 `brytlinje_ny` (`typ`, `hojd_m` > 0), eller med `bort` K9 `brytlinje_bort` (R3) |
| `oppning` | linje | K9 `oppning_ny` (`typ` trumma/port/passage, `bredd_m`, `botten_rh2000`) (R3) |

Brytlinjer och öppningar har K9-ändringstyper sedan R3 (`karna.k9_till_k1` -> K3 -> K1 `brytlinjer.gpkg`
och `oppningar.gpkg`) och räknas i skyfallsberäkningen: en mur leder om vatten, en öppning släpper igenom.

**Dataklass.** Ett lager från en stängd källa (Stockholms baskarta, ledningar, lokala data) är stängt
(`effektiv_dataklass`) och lämnas inte ut via MCP (`for_mcp`); servern skriver ett förslag med ett
stängt lager med objekt till `k9_stangda/`, som är markerat `oppen = false` (K38), så att körningarna
av det inte heller lämnas ut. Osäker källa räknas som stängd, inte öppen.

Stil, synlighet, låsning och tonad är bara visning och ingår aldrig i K9-nyckeln.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Polygon
from shapely.validation import explain_validity

from karna.ritat import MIN_YTA_M2, RitatFel, normalisera_ring

KONTRAKT = "floden-ritlager/v1"
STANDARDMALL = "standard"
MALLMAPP: Path = Path(__file__).resolve().parents[2] / "data" / "ritlager"

ROLLER: tuple[str, ...] = (
    "byggnad",
    "mark",
    "hardgjort",
    "vegetation",
    "vatten",
    "brytlinje",
    "oppning",
)
GEOMETRIER: tuple[str, ...] = ("polygon", "linje", "punkt")
"""`punkt` är reserverad (ingen roll tar punkt än); rollen begränsar vilka som gäller."""
ROLL_GEOMETRIER: dict[str, frozenset[str]] = {
    "byggnad": frozenset({"polygon"}),
    "mark": frozenset({"polygon"}),
    "hardgjort": frozenset({"polygon"}),
    "vegetation": frozenset({"polygon"}),
    "vatten": frozenset({"polygon"}),
    "brytlinje": frozenset({"linje"}),
    "oppning": frozenset({"linje"}),
}
GEOJSON_TYP = {"polygon": "Polygon", "linje": "LineString", "punkt": "Point"}

DATAKLASSER: tuple[str, ...] = ("oppen", "intern", "skyddad")
"""Ordnade från öppen till mest skyddad. (MCP:s klasser `oppet`, `internt`, `skyddat` motsvarar dem, se `till_mcp_klass`.)"""
STANGDA_KALLOR: tuple[str, ...] = ("baskarta:", "ledning", "lokal:")
"""Källor som aldrig är öppna: Stockholms baskarta m.fl. baskartor, ledningsdata och lokala (stängda) data."""
SKYDDADE_KALLOR: tuple[str, ...] = ("ledning",)

ROLLINFO: dict[str, dict[str, str]] = {
    "byggnad": {"namn": "Byggnad", "blir": "Nytt hus (K9 byggnad_ny) med höjd, tak och våningar."},
    "mark": {"namn": "Mark (ny nivå)", "blir": "Ny markyta med nivå (K9 markhojd)."},
    "hardgjort": {"namn": "Hårdgjort", "blir": "Hårdgjord yta (markslag 1), med nivå som markhojd."},
    "vegetation": {"namn": "Vegetation", "blir": "Växtlighetens höjd (K9 vegetation) och/eller gräs eller skog (markslag 4, 5)."},
    "vatten": {"namn": "Vatten", "blir": "Vattenyta (markslag 8), eller magasin när volym anges."},
    "brytlinje": {"namn": "Mur och kant", "blir": "Brytlinje i markmodellen (mur, kantsten, bankkant) med höjd: vattnet leds om. Med Ta bort: befintliga brytlinjer längs linjen tas bort."},
    "oppning": {"namn": "Passage och trumma", "blir": "Öppning genom hinder (trumma, port, passage): marken sänks till botten och murar klipps så att vattnet kommer igenom."},
}

BRYTLINJE_TYPER: dict[str, str] = {
    "kantsten": "kantsten",
    "mur": "mur",
    "stodmur": "stodmur",
    "stödmur": "stodmur",
    "staket": "staket",
    "bankkant": "bankkant",
}
OPPNING_TYPER: frozenset[str] = frozenset({"trumma", "port", "passage"})
MARKSLAG_VEGETATION: frozenset[int] = frozenset({4, 5})
MARKSLAG_HARDGJORT = 1
MARKSLAG_VATTEN = 8

TILLATNA_EGENSKAPER: dict[str, frozenset[str]] = {
    "byggnad": frozenset({"hojd_m", "tak_rh2000", "takform", "andamal", "vaningar"}),
    "mark": frozenset({"niva_rh2000", "lutning", "delta_m", "slant", "osakerhet_m", "markslag"}),
    "hardgjort": frozenset({"niva_rh2000", "osakerhet_m"}),
    "vegetation": frozenset({"hojd_m", "markslag"}),
    "vatten": frozenset({"volym_m3", "botten_rh2000", "markslag"}),
    "brytlinje": frozenset({"typ", "hojd_m", "bort", "tolerans_m"}),
    "oppning": frozenset({"typ", "bredd_m", "botten_rh2000"}),
}
"""Egenskaper per roll. Andra nycklar ger fel (inga tysta fel); en ny egenskap läggs till här."""

MALTYPER = frozenset({"tal", "kod", "text"})
STIL_FARGER = ("fyllning", "linje")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,59}$")
_OBJEKT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,79}$")
SEKUNDAR_SUFFIX = "~markslag"
"""Ett objekt kan ge två K9-ändringar (t.ex. vegetation med höjd och markslag): den andra får detta suffix på id."""


class RitlagerFel(RitatFel):
    """Ett ritlager eller ritat objekt som inte går att tolka. Texten är skriven för en planarkitekt."""


# ------------------------------------------------------------------ mallar


def las_mall(namn: str = STANDARDMALL, mapp: Path | None = None) -> dict[str, Any]:
    """Läser en mall (`data/ritlager/<namn>.toml`) som ett normaliserat K50-dokument utan objekt."""
    if not _ID.match(namn):
        raise RitlagerFel(f"Ogiltigt mallnamn '{namn}'.")
    fil = (mapp or MALLMAPP) / f"{namn}.toml"
    if not fil.is_file():
        raise RitlagerFel(f"Det finns ingen lagermall '{namn}'.")
    try:
        with fil.open("rb") as fh:
            d = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RitlagerFel(f"Lagermallen {fil.name} går inte att läsa: {exc}") from exc
    if d.get("kontrakt") != KONTRAKT:
        raise RitlagerFel(f"Lagermallen {fil.name} har fel kontrakt (väntade {KONTRAKT}).")
    lager = [kontrollera_lager(x) for x in d.get("lager", [])]
    _unika_id(lager)
    if not lager:
        raise RitlagerFel(f"Lagermallen {fil.name} har inga lager.")
    return {"kontrakt": KONTRAKT, "mall": str(d.get("mall", namn)), "lager": lager, "objekt": []}


def standardlager() -> list[dict[str, Any]]:
    """De förvalda lagren (standardmallen), nya kopior varje gång."""
    return [dict(x) for x in las_mall()["lager"]]


def standarddokument() -> dict[str, Any]:
    """Förvalda lager och inga objekt: det ett förslag utan lager får."""
    return {"kontrakt": KONTRAKT, "lager": standardlager(), "objekt": []}


# ------------------------------------------------------------------ lager


def _tal(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def kontrollera_lager(lager: Mapping[str, Any]) -> dict[str, Any]:
    """Kontrollerar ett lager och ger det normaliserat (alla fält ifyllda). Kastar `RitlagerFel`."""
    if not isinstance(lager, Mapping):
        raise RitlagerFel("Ett lager har fel form.")
    lid = str(lager.get("lager_id", "")).strip()
    if not _ID.match(lid):
        raise RitlagerFel(
            f"Lagrets id '{lid}' är ogiltigt: använd små bokstäver a-z, siffror, bindestreck och understreck (högst 60 tecken)."
        )
    namn = str(lager.get("namn", "")).strip()
    if not namn or len(namn) > 60:
        raise RitlagerFel(f"Lagret '{lid}' behöver ett namn på 1 till 60 tecken.")
    roll = lager.get("roll")
    if roll not in ROLLER:
        raise RitlagerFel(f"Lagret '{namn}': okänd roll '{roll}'. Tillåtna: {', '.join(ROLLER)}.")
    geometri = lager.get("geometri", next(iter(sorted(ROLL_GEOMETRIER[roll]))))
    if geometri not in GEOMETRIER:
        raise RitlagerFel(f"Lagret '{namn}': okänd geometrityp '{geometri}'. Tillåtna: {', '.join(GEOMETRIER)}.")
    if geometri not in ROLL_GEOMETRIER[roll]:
        ok = " eller ".join(sorted(ROLL_GEOMETRIER[roll]))
        raise RitlagerFel(f"Lagret '{namn}': rollen {roll} ritas som {ok}, inte som {geometri}.")
    kalla = str(lager.get("kalla", "ritat")).strip() or "ritat"
    if not re.match(r"^(ritat|fil:.+|k1:.+|baskarta:.+|ledning.*|lokal:.+)$", kalla):
        raise RitlagerFel(
            f"Lagret '{namn}': okänd källa '{kalla}'. Tillåtna: ritat, fil:<namn>, k1:<ruta>, baskarta:<profil>, lokal:<namn>."
        )
    klass = lager.get("dataklass", "intern" if kalla != "ritat" else "oppen")
    if klass == "personuppgifter":
        raise RitlagerFel(f"Lagret '{namn}': personuppgifter får inte finnas i Floden (AGENTS.md).")
    if klass not in DATAKLASSER:
        raise RitlagerFel(f"Lagret '{namn}': okänd dataklass '{klass}'. Tillåtna: {', '.join(DATAKLASSER)}.")
    opacitet = lager.get("opacitet", 1.0)
    if not _tal(opacitet) or not 0.0 <= float(opacitet) <= 1.0:
        raise RitlagerFel(f"Lagret '{namn}': opaciteten ska vara ett tal mellan 0 och 1.")
    for f in ("synlig", "last", "tonad"):
        if f in lager and not isinstance(lager[f], bool):
            raise RitlagerFel(f"Lagret '{namn}': {f} ska vara sant eller falskt.")
    stil_in = lager.get("stil") or {}
    if not isinstance(stil_in, Mapping):
        raise RitlagerFel(f"Lagret '{namn}': stilen har fel form.")
    stil: dict[str, Any] = {}
    for k, v in stil_in.items():
        if k in STIL_FARGER:
            if not (isinstance(v, str) and _HEX.match(v)):
                raise RitlagerFel(f"Lagret '{namn}': stilens {k} ska vara en färg som #rrggbb.")
            stil[k] = v.lower()
        elif k == "linjebredd_px":
            if not _tal(v) or not 0.0 <= float(v) <= 20.0:
                raise RitlagerFel(f"Lagret '{namn}': linjebredden ska vara 0 till 20 px.")
            stil[k] = float(v)
        elif k == "monster":
            if v is not None and not isinstance(v, str):
                raise RitlagerFel(f"Lagret '{namn}': stilens mönster ska vara text eller tomt.")
            stil[k] = v
        else:
            raise RitlagerFel(f"Lagret '{namn}': okänd stilegenskap '{k}'.")
    mall_in = lager.get("egenskaper_mall") or {}
    if not isinstance(mall_in, Mapping):
        raise RitlagerFel(f"Lagret '{namn}': egenskapsmallen har fel form.")
    mall: dict[str, str] = {}
    for k, v in mall_in.items():
        if k not in TILLATNA_EGENSKAPER[roll]:
            raise RitlagerFel(f"Lagret '{namn}': egenskapen '{k}' hör inte till rollen {roll}.")
        if v not in MALTYPER:
            raise RitlagerFel(f"Lagret '{namn}': egenskapens typ ska vara tal, kod eller text.")
        mall[str(k)] = str(v)
    forval_in = lager.get("egenskaper_forval") or {}
    if not isinstance(forval_in, Mapping):
        raise RitlagerFel(f"Lagret '{namn}': förvalda egenskaper har fel form.")
    forval: dict[str, Any] = {}
    for k, v in forval_in.items():
        if k not in TILLATNA_EGENSKAPER[roll]:
            raise RitlagerFel(f"Lagret '{namn}': förvald egenskap '{k}' hör inte till rollen {roll}.")
        forval[str(k)] = v
    return {
        "lager_id": lid,
        "namn": namn,
        "geometri": geometri,
        "roll": roll,
        "stil": stil,
        "synlig": bool(lager.get("synlig", True)),
        "last": bool(lager.get("last", False)),
        "tonad": bool(lager.get("tonad", False)),
        "opacitet": float(opacitet),
        "kalla": kalla,
        "dataklass": klass,
        "egenskaper_mall": mall,
        "egenskaper_forval": forval,
    }


def _unika_id(lager: Sequence[Mapping[str, Any]]) -> None:
    sett: set[str] = set()
    for x in lager:
        if x["lager_id"] in sett:
            raise RitlagerFel(f"Två lager har id '{x['lager_id']}'. Varje lager behöver ett eget id.")
        sett.add(x["lager_id"])


def effektiv_dataklass(lager: Mapping[str, Any], projekt_oppen: bool | None = None) -> str:
    """Lagrets dataklass efter källan: stängd källa ger minst `intern`, ledningar `skyddad`.

    Ett lager ritat av användaren (`ritat`) följer projektets öppenhet: i ett stängt projekt är det
    minst `intern`. Okänt (`projekt_oppen` None) räknas som öppet bara för `ritat`; en `fil:`-källa är
    aldrig öppen om lagret inte uttryckligen deklarerats så (förvalet är `intern`).
    """
    kalla = str(lager.get("kalla", "ritat"))
    klass = str(lager.get("dataklass", "intern"))
    if klass not in DATAKLASSER:
        klass = "intern"
    niva = DATAKLASSER.index(klass)
    if any(kalla.startswith(p) for p in STANGDA_KALLOR):
        niva = max(niva, DATAKLASSER.index("intern"))
    if any(kalla.startswith(p) for p in SKYDDADE_KALLOR):
        niva = max(niva, DATAKLASSER.index("skyddad"))
    if kalla == "ritat" and projekt_oppen is False:
        niva = max(niva, DATAKLASSER.index("intern"))
    return DATAKLASSER[niva]


def till_mcp_klass(klass: str) -> str:
    """K50:s dataklass som MCP:s (`floden_mcp.dataklass.DataKlass`)."""
    return {"oppen": "oppet", "intern": "internt", "skyddad": "skyddat"}[klass]


# ------------------------------------------------------------------ dokument och objekt


def kontrollera_dokument(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """Ett K50-dokument normaliserat och kontrollerat. `None` eller utan `lager` ger förvalda lager.

    Objekten kontrolleras mot sitt lager (geometrityp, id, egenskaper per roll), så att ett fel visas
    när förslaget kontrolleras och inte först vid körning.
    """
    if doc is None:
        return standarddokument()
    if not isinstance(doc, Mapping):
        raise RitlagerFel("Ritlagren har fel form.")
    if doc.get("kontrakt", KONTRAKT) != KONTRAKT:
        raise RitlagerFel(f"Okänt ritlagerkontrakt '{doc.get('kontrakt')}'. Förväntat {KONTRAKT}.")
    lager_in = doc.get("lager")
    if lager_in is None:
        lager = standardlager()
    elif isinstance(lager_in, list):
        lager = [kontrollera_lager(x) for x in lager_in]
        _unika_id(lager)
        if not lager:
            raise RitlagerFel("Det behövs minst ett lager.")
    else:
        raise RitlagerFel("Lagerlistan har fel form.")
    per_id = {x["lager_id"]: x for x in lager}
    objekt_in = doc.get("objekt", [])
    if not isinstance(objekt_in, list):
        raise RitlagerFel("Objektlistan har fel form.")
    objekt = [_kontrollera_objekt(o, per_id) for o in objekt_in]
    sett: set[str] = set()
    for o in objekt:
        if o["objekt_id"] in sett:
            raise RitlagerFel(f"Två objekt har id '{o['objekt_id']}'.")
        sett.add(o["objekt_id"])
    objekt.sort(key=lambda o: o["objekt_id"])
    return {"kontrakt": KONTRAKT, "lager": lager, "objekt": objekt}


def _kontrollera_objekt(o: Any, lager: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(o, Mapping):
        raise RitlagerFel("Ett ritat objekt har fel form.")
    oid = str(o.get("objekt_id", "")).strip()
    if not _OBJEKT_ID.match(oid):
        raise RitlagerFel(f"Objektets id '{oid}' är ogiltigt (bokstäver, siffror, bindestreck och understreck).")
    lid = str(o.get("lager_id", ""))
    if lid not in lager:
        raise RitlagerFel(f"Objektet '{oid}' ligger i lagret '{lid}', som inte finns.")
    lg = lager[lid]
    g = o.get("geometri")
    forvantad = GEOJSON_TYP[lg["geometri"]]
    if not isinstance(g, Mapping) or g.get("type") != forvantad or not g.get("coordinates"):
        raise RitlagerFel(
            f"Objektet '{oid}' i lagret '{lg['namn']}' ska vara {lg['geometri']} ({forvantad}) men har en annan form."
        )
    e_in = o.get("egenskaper") or {}
    if not isinstance(e_in, Mapping):
        raise RitlagerFel(f"Objektet '{oid}': egenskaperna har fel form.")
    tillatna = TILLATNA_EGENSKAPER[lg["roll"]]
    ovriga = sorted(set(e_in) - tillatna)
    if ovriga:
        raise RitlagerFel(
            f"Objektet '{oid}' i lagret '{lg['namn']}' (roll {lg['roll']}) har egenskaper som rollen inte tar: "
            f"{', '.join(ovriga)}. Tillåtna: {', '.join(sorted(tillatna))}."
        )
    return {
        "objekt_id": oid,
        "lager_id": lid,
        "geometri": _normaliserad_geometri(g, lg["geometri"], oid, lg["namn"]),
        "egenskaper": {str(k): v for k, v in e_in.items()},
    }


def _normaliserad_geometri(g: Mapping[str, Any], typ: str, oid: str, lagernamn: str) -> dict[str, Any]:
    vad = f"Objektet '{oid}' i lagret '{lagernamn}'"
    try:
        if typ == "polygon":
            ring = g["coordinates"][0]
            if any(not (_tal(p[0]) and _tal(p[1])) for p in ring):
                raise RitlagerFel(f"{vad}: ett hörn har ogiltiga koordinater.")
            norm = normalisera_ring(ring)
            if len({tuple(p) for p in norm}) < 3:
                raise RitlagerFel(f"{vad}: ytan behöver minst tre olika hörn.")
            poly = Polygon(norm)
            if not poly.is_valid:
                raise RitlagerFel(f"{vad}: ytan har ingen giltig form ({explain_validity(poly)}). Kanterna får inte korsa varandra.")
            if poly.area < MIN_YTA_M2:
                raise RitlagerFel(f"{vad}: ytan är för liten ({poly.area:.2f} m²). Minsta yta är {MIN_YTA_M2:g} m².")
            return {"type": "Polygon", "coordinates": [norm]}
        if typ == "linje":
            pts = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in g["coordinates"]]
            if any(not (math.isfinite(p[0]) and math.isfinite(p[1])) for p in pts):
                raise RitlagerFel(f"{vad}: en punkt har ogiltiga koordinater.")
            ren: list[list[float]] = []
            for p in pts:
                if not ren or p != ren[-1]:
                    ren.append(p)
            if len(ren) < 2 or LineString(ren).length <= 0.0:
                raise RitlagerFel(f"{vad}: linjen behöver minst två olika punkter.")
            return {"type": "LineString", "coordinates": ren}
    except RitlagerFel:
        raise
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        raise RitlagerFel(f"{vad}: geometrin har fel form.") from exc
    raise RitlagerFel(f"{vad}: geometrityp '{typ}' stöds inte än.")


# ------------------------------------------------------------------ översättning roll -> K9/K1


class Oversattning:
    """Resultatet av `oversatt`: K9-ändringarna och vilka som kom ur vilket objekt."""

    def __init__(self) -> None:
        self.k9: list[dict[str, Any]] = []
        self.objekt_till_andringar: dict[str, list[str]] = {}


def _egen(
    o: Mapping[str, Any], lg: Mapping[str, Any], nyckel: str, *, krav: bool = False, vad: str = ""
) -> Any:
    """Egenskap ur objektet, annars lagrets förval. Saknas den och `krav` är satt: fel med lagrets namn."""
    if nyckel in o["egenskaper"]:
        return o["egenskaper"][nyckel]
    if nyckel in lg.get("egenskaper_forval", {}):
        return lg["egenskaper_forval"][nyckel]
    if krav:
        raise RitlagerFel(
            f"Objektet '{o['objekt_id']}' i lagret '{lg['namn']}' saknar {vad or nyckel}."
        )
    return None


def _num(
    o: Mapping[str, Any], lg: Mapping[str, Any], nyckel: str, vad: str, *, minst: float | None = None,
    storre_an: float | None = None, krav: bool = False,
) -> float | None:
    v = _egen(o, lg, nyckel, krav=krav, vad=vad)
    if v is None:
        return None
    if not _tal(v):
        raise RitlagerFel(f"Objektet '{o['objekt_id']}' i lagret '{lg['namn']}': {vad} ska vara ett tal.")
    f = float(v)
    if minst is not None and f < minst:
        raise RitlagerFel(f"Objektet '{o['objekt_id']}' i lagret '{lg['namn']}': {vad} får inte vara under {minst:g}.")
    if storre_an is not None and f <= storre_an:
        raise RitlagerFel(f"Objektet '{o['objekt_id']}' i lagret '{lg['namn']}': {vad} måste vara större än {storre_an:g}.")
    return f


def _kod(o: Mapping[str, Any], lg: Mapping[str, Any], nyckel: str, giltiga: frozenset[int]) -> int | None:
    v = _egen(o, lg, nyckel)
    if v is None:
        return None
    if isinstance(v, bool) or not _tal(v) or int(v) != v or int(v) not in giltiga:
        raise RitlagerFel(
            f"Objektet '{o['objekt_id']}' i lagret '{lg['namn']}': markslag ska vara en av {', '.join(str(k) for k in sorted(giltiga))} (K1)."
        )
    return int(v)


def _andring(typ: str, aid: str, geometri: Mapping[str, Any] | None, e: dict[str, Any]) -> dict[str, Any]:
    return {"typ": typ, "andring_id": aid, "geometri": dict(geometri) if geometri is not None else None, "egenskaper": e}


def _lutning(v: Any, plats: str) -> dict[str, Any]:
    """Lutande yta enligt K3 `normalisera_lutning` (fyra former); fel skrivs för en planarkitekt."""
    from lapp.kontrakt import LappFel, normalisera_lutning

    try:
        return normalisera_lutning(v, "lutning")
    except LappFel as exc:
        raise RitlagerFel(f"{plats}: {exc}") from exc


def _slant(v: Any, plats: str) -> dict[str, Any]:
    """Slänt: `{"forhallande": n}` (1:n) eller `{"bredd_m": b}`, valfritt `markslag`."""
    if not isinstance(v, Mapping) or not set(v) <= {"forhallande", "bredd_m", "markslag"}:
        raise RitlagerFel(f"{plats}: slänt ska vara {{forhallande | bredd_m, markslag?}}.")
    if ("forhallande" in v) == ("bredd_m" in v):
        raise RitlagerFel(f"{plats}: ange exakt en av forhallande (1:n) och bredd_m för slänten.")
    ut: dict[str, Any] = {}
    for k in ("forhallande", "bredd_m"):
        if k in v:
            if not _tal(v[k]) or float(v[k]) <= 0:
                raise RitlagerFel(f"{plats}: släntens {k} måste vara ett tal över 0.")
            ut[k] = float(v[k])
    if "markslag" in v:
        if isinstance(v["markslag"], bool) or not _tal(v["markslag"]) or int(v["markslag"]) != v["markslag"] or not 0 <= int(v["markslag"]) <= 9:
            raise RitlagerFel(f"{plats}: släntens markslag ska vara 0 till 9 (K1).")
        ut["markslag"] = int(v["markslag"])
    return ut


def oversatt_objekt(o: Mapping[str, Any], lg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ett objekt -> K9-ändringar. Rollen avgör vilka."""
    roll = lg["roll"]
    oid = o["objekt_id"]
    g = o["geometri"]
    plats = f"Objektet '{oid}' i lagret '{lg['namn']}'"
    if roll == "byggnad":
        hojd = _num(o, lg, "hojd_m", "höjden (hojd_m)", storre_an=0.0)
        tak = _num(o, lg, "tak_rh2000", "takhöjden (tak_rh2000)")
        if hojd is None and tak is None:
            raise RitlagerFel(f"{plats} saknar höjd (hojd_m, över mark) eller tak_rh2000.")
        e: dict[str, Any] = {}
        if hojd is not None:
            e["hojd_m"] = hojd
        if tak is not None:
            e["tak_rh2000"] = tak
        for k in ("takform", "andamal"):
            v = _egen(o, lg, k)
            if v is not None:
                if not isinstance(v, str) or not v.strip():
                    raise RitlagerFel(f"{plats}: {k} ska vara text.")
                e[k] = v.strip()
        van = _egen(o, lg, "vaningar")
        if van is not None:
            if isinstance(van, bool) or not _tal(van) or int(van) != van or van < 1:
                raise RitlagerFel(f"{plats}: våningar ska vara ett heltal från 1.")
            e["vaningar"] = int(van)
        return [_andring("byggnad_ny", oid, g, e)]
    if roll == "mark":
        niva = _num(o, lg, "niva_rh2000", "nivån (niva_rh2000, m RH 2000)")
        lutning = _egen(o, lg, "lutning")
        delta = _num(o, lg, "delta_m", "ändringen (delta_m, m)")
        givna = [n for n, v in (("niva_rh2000", niva), ("lutning", lutning), ("delta_m", delta)) if v is not None]
        if not givna:
            raise RitlagerFel(f"{plats} saknar nivån: ange niva_rh2000 (m RH 2000), lutning eller delta_m (höj eller sänk mot befintlig mark).")
        if len(givna) > 1:
            raise RitlagerFel(f"{plats}: ange exakt en av niva_rh2000, lutning och delta_m (fick {', '.join(givna)}).")
        e = {}
        if niva is not None:
            e["niva_rh2000"] = niva
        if delta is not None:
            e["delta_m"] = delta
        if lutning is not None:
            e["lutning"] = _lutning(lutning, plats)
        slant = _egen(o, lg, "slant")
        if slant is not None:
            e["slant"] = _slant(slant, plats)
        osak = _num(o, lg, "osakerhet_m", "osäkerheten (osakerhet_m)", minst=0.0)
        if osak is not None:
            e["osakerhet_m"] = osak
        slag = _kod(o, lg, "markslag", frozenset(range(10)))
        if slag is not None:
            e["markslag"] = slag
        return [_andring("markhojd", oid, g, e)]
    if roll == "hardgjort":
        niva = _num(o, lg, "niva_rh2000", "nivån (niva_rh2000)")
        osak = _num(o, lg, "osakerhet_m", "osäkerheten (osakerhet_m)", minst=0.0)
        if niva is None:
            if osak is not None:
                raise RitlagerFel(f"{plats}: osäkerhet gäller en höjdsatt yta; ange också nivån.")
            return [_andring("markslag", oid, g, {"kod": MARKSLAG_HARDGJORT})]
        e = {"niva_rh2000": niva, "markslag": MARKSLAG_HARDGJORT}
        if osak is not None:
            e["osakerhet_m"] = osak
        return [_andring("markhojd", oid, g, e)]
    if roll == "vegetation":
        hojd = _num(o, lg, "hojd_m", "vegetationens höjd (hojd_m)", minst=0.0)
        slag = _kod(o, lg, "markslag", MARKSLAG_VEGETATION)
        if hojd is None and slag is None:
            raise RitlagerFel(f"{plats} behöver höjd (hojd_m) och/eller markslag (4 gräs, 5 skog).")
        ut: list[dict[str, Any]] = []
        if hojd is not None:
            ut.append(_andring("vegetation", oid, g, {"hojd_m": hojd}))
        if slag is not None:
            aid = oid if hojd is None else oid + SEKUNDAR_SUFFIX
            ut.append(_andring("markslag", aid, g, {"kod": slag}))
        return ut
    if roll == "vatten":
        volym = _num(o, lg, "volym_m3", "volymen (volym_m3)", storre_an=0.0)
        slag = _kod(o, lg, "markslag", frozenset(range(10)))
        botten = _num(o, lg, "botten_rh2000", "bottennivån (botten_rh2000)")
        if volym is None:
            if botten is not None:
                raise RitlagerFel(f"{plats}: bottennivå gäller ett magasin; ange också volymen (volym_m3).")
            return [_andring("markslag", oid, g, {"kod": MARKSLAG_VATTEN if slag is None else slag})]
        e = {"volym_m3": volym}
        if botten is not None:
            e["botten_rh2000"] = botten
        if slag is not None:
            e["markslag"] = slag
        return [_andring("magasin", oid, g, e)]
    if roll == "brytlinje":
        bort = _egen(o, lg, "bort")
        if bort is not None and not isinstance(bort, bool):
            raise RitlagerFel(f"{plats}: bort ska vara sant eller falskt.")
        if bort:
            e = {}
            typ_in = _egen(o, lg, "typ")
            if typ_in is not None:
                kanonisk_bort = BRYTLINJE_TYPER.get(str(typ_in).strip().lower())
                if kanonisk_bort is None:
                    raise RitlagerFel(f"{plats}: okänd brytlinjetyp '{typ_in}'. Tillåtna: kantsten, mur, stodmur, staket, bankkant.")
                e["typ"] = kanonisk_bort
            tol = _num(o, lg, "tolerans_m", "toleransen (tolerans_m)", storre_an=0.0)
            if tol is not None:
                if tol > 5.0:
                    raise RitlagerFel(f"{plats}: toleransen får vara högst 5 m.")
                e["tolerans_m"] = tol
            return [_andring("brytlinje_bort", oid, g, e)]
        typ = _egen(o, lg, "typ", krav=True, vad="typ (kantsten, mur, stodmur, staket, bankkant)")
        kanonisk = BRYTLINJE_TYPER.get(str(typ).strip().lower())
        if kanonisk is None:
            raise RitlagerFel(f"{plats}: okänd brytlinjetyp '{typ}'. Tillåtna: kantsten, mur, stodmur, staket, bankkant.")
        hojd = _num(o, lg, "hojd_m", "höjden över marken (hojd_m, m)", storre_an=0.0, krav=True)
        return [_andring("brytlinje_ny", oid, g, {"typ": kanonisk, "hojd_m": hojd})]
    if roll == "oppning":
        typ = _egen(o, lg, "typ", krav=True, vad="typ (trumma, port, passage)")
        if str(typ).strip().lower() not in OPPNING_TYPER:
            raise RitlagerFel(f"{plats}: okänd öppningstyp '{typ}'. Tillåtna: trumma, port, passage.")
        bredd = _num(o, lg, "bredd_m", "bredden (bredd_m)", storre_an=0.0, krav=True)
        botten = _num(o, lg, "botten_rh2000", "bottennivån (botten_rh2000, m RH 2000)", krav=True)
        e = {"typ": str(typ).strip().lower(), "bredd_m": bredd, "botten_rh2000": botten}
        return [_andring("oppning_ny", oid, g, e)]
    raise RitlagerFel(f"Rollen '{roll}' stöds inte.")  # pragma: no cover - ROLLER är uttömmande


def oversatt(doc: Mapping[str, Any]) -> Oversattning:
    """Alla objekt i ett kontrollerat K50-dokument -> K9/K1. Samma dokument ger samma lista (sorterad på id)."""
    d = kontrollera_dokument(doc)
    per_id = {x["lager_id"]: x for x in d["lager"]}
    res = Oversattning()
    for o in d["objekt"]:
        lg = per_id[o["lager_id"]]
        k9 = oversatt_objekt(o, lg)
        res.k9.extend(k9)
        res.objekt_till_andringar[o["objekt_id"]] = [a["andring_id"] for a in k9]
    res.k9.sort(key=lambda a: a["andring_id"])
    return res


def fria_andringar(andringar: Sequence[Mapping[str, Any]], doc: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """K9-ändringarna som *inte* kommer ur ritlagrens objekt (t.ex. höjdändring av en befintlig byggnad)."""
    if not doc or not doc.get("objekt"):
        return [dict(a) for a in andringar]
    ids = {i for lista in oversatt(doc).objekt_till_andringar.values() for i in lista}
    return [dict(a) for a in andringar if str(a.get("andring_id")) not in ids]


def sla_ihop(
    fria: Sequence[Mapping[str, Any]], doc: Mapping[str, Any] | None
) -> tuple[list[dict[str, Any]], Oversattning]:
    """Fria K9-ändringar + översatta objekt som en lista. Dubbla andring_id ger fel (inget skrivs över tyst)."""
    res = oversatt(doc) if doc else Oversattning()
    lista = [dict(a) for a in fria]
    funna = {str(a.get("andring_id", "")) for a in lista}
    for a in res.k9:
        if a["andring_id"] in funna:
            raise RitlagerFel(
                f"Ändringen '{a['andring_id']}' finns både som fri ändring och som ritat objekt. Varje id får bara användas en gång."
            )
        funna.add(a["andring_id"])
    return lista + res.k9, res


# ------------------------------------------------------------------ dataklass och MCP


def stangda_lager(doc: Mapping[str, Any], projekt_oppen: bool | None = None) -> list[str]:
    """Id på lager vars effektiva dataklass inte är `oppen`."""
    return [x["lager_id"] for x in doc.get("lager", []) if effektiv_dataklass(x, projekt_oppen) != "oppen"]


def har_stangt_innehall(doc: Mapping[str, Any] | None, projekt_oppen: bool | None = None) -> bool:
    """Har ett stängt lager objekt? Då bygger förslagets K9 delvis på data som inte är öppna."""
    if not doc:
        return False
    stangda = set(stangda_lager(doc, projekt_oppen))
    return any(o["lager_id"] in stangda for o in doc.get("objekt", []))


def for_mcp(doc: Mapping[str, Any], projekt_oppen: bool | None = None) -> dict[str, Any]:
    """K50-dokumentet så som MCP får lämna ut det: stängda lager med bara id, namn, roll och klass, utan objekt, stil och källa."""
    stangda = set(stangda_lager(doc, projekt_oppen))
    lager: list[dict[str, Any]] = []
    for x in doc.get("lager", []):
        if x["lager_id"] in stangda:
            lager.append(
                {
                    "lager_id": x["lager_id"],
                    "namn": x["namn"],
                    "roll": x["roll"],
                    "dataklass": effektiv_dataklass(x, projekt_oppen),
                    "stangt": True,
                }
            )
        else:
            lager.append({**x, "stangt": False})
    return {
        "kontrakt": KONTRAKT,
        "lager": lager,
        "objekt": [o for o in doc.get("objekt", []) if o["lager_id"] not in stangda],
        "stangda_lager": sorted(stangda),
    }


def roller_for_api() -> list[dict[str, Any]]:
    """Rollerna med namn, tillåten geometri och vad de blir i beräkningen (för panelen i webben)."""
    return [
        {
            "roll": r,
            "namn": ROLLINFO[r]["namn"],
            "geometri": sorted(ROLL_GEOMETRIER[r]),
            "blir": ROLLINFO[r]["blir"],
            "egenskaper": sorted(TILLATNA_EGENSKAPER[r]),
        }
        for r in ROLLER
    ]
