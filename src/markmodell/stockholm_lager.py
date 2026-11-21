"""Stockholms stads baskarta och parkkarta: tolkning av lagernamnen efter deras grammatik.

Grammatik och nyckelord lyfta från O-Tid (MIT, © 2026 Joel Berring, joelberring/otid,
packages/map-revision/src/stockholm-layers.ts @ 4a446af). Ett lagernamn består av grupp +
objekttyp + geometri + karta + status, avkortat: `MarkanlaggningStenmurglBOdefin` är gruppen
Markanlaggning, objekttypen Stenmur, geometrin `gl` (linje), kartan `B` (baskartan) och statusen
Odefinierad.
Geometrin är `gy` yta, `gl` linje, `gb` blandat, `glh` höjdkurva, `s` symbol eller `t` text; status
är Befintlig, Odefinierad eller Skymd (statusen kan vara avkortad: `Befintl`, `Odefin`). Parkkartan
heter `Park` + grupp + objekttyp (`ParkGrasytagyB0`, utan status).

Härledd ur O-Tid, prövad på sprintkartan Älvsjö–Solberga; ska bekräftas mot full lagerlista.

Skillnad mot O-Tid: där blir lagren symboler på en orienteringskarta; här blir de markslag (ytor)
och brytlinjer för skyfall. Därför är slänt och gräskant inte överhoppade (de är relevanta för
vattnet). Reglerna (regex mot objekttypens normaliserade text, första träff gäller) står i
lagerprofilens `[stockholm]`-tabell (`data/lagerprofiler/stockholm_baskarta.toml`), inte här.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from markmodell.grundkarta import GrundkartaFel

GEOMETRIER: tuple[str, ...] = ("glh", "gy", "gl", "gb", "s", "t")

_GEOMETRI = re.compile(
    r"^(?P<stam>[A-Z][a-z]+[A-Z][A-Za-z_]*?)(?P<geo>glh|gy|gl|gb|s|t)B(?=[A-Z0-9]|$)(?P<rest>.*)$"
)
_PARK = re.compile(r"^Park[A-Z]")

Status = Literal["befintlig", "odefinierad", "skymd", "ingen", "okand"]
_STATUSORD: tuple[tuple[str, Status], ...] = (
    ("befintlig", "befintlig"),
    ("odefinierad", "odefinierad"),
    ("skymd", "skymd"),
)

Atgard = Literal[
    "markslag", "byggnad", "byggnad_kontur", "brytlinje", "oppning", "hoppa_over", "okand_objekt"
]


def normalisera(namn: str) -> str:
    """Som O-Tids `normalized`: gemener, å/ä → a, ö → o."""
    return namn.lower().replace("å", "a").replace("ä", "a").replace("ö", "o")


@dataclass(frozen=True)
class StockholmLager:
    """Ett lagernamn tolkat efter grammatiken."""

    namn: str
    karta: Literal["baskarta", "parkkarta"]
    stam: str
    """Grupp + objekttyp (utan geometri, karta och status), t.ex. `MarkanlaggningStenmur`."""
    delar: tuple[str, ...]
    """Stammen delad vid versaler: `("Markanlaggning", "Stenmur")` (parkkartan utan `Park`)."""
    geometri: str | None
    """`gy`, `gl`, `gb`, `glh`, `s` eller `t`; None för ett `Park…`-namn utan geometrisuffix."""
    status: Status
    text: str
    """Normaliserad stam som reglerna matchas mot."""


def ar_stockholms_lager(namn: str) -> bool:
    """Följer namnet grammatiken? (O-Tids `isStockholmLayer`.)"""
    return bool(_PARK.match(namn) or _GEOMETRI.match(namn))


def _status(rest: str) -> Status:
    if not rest or rest[0].isdigit():
        return "ingen"
    liten = rest.lower()
    for ord_, status in _STATUSORD:
        if ord_.startswith(liten):
            return status
    return "okand"


def tolka_lagernamn(namn: str) -> StockholmLager | None:
    """Tolkar ett lagernamn, eller None om det inte följer grammatiken."""
    traff = _GEOMETRI.match(namn)
    if traff is not None:
        stam, geo, rest = traff["stam"], traff["geo"], traff["rest"]
        status = _status(rest)
    elif _PARK.match(namn):
        stam, geo, status = namn, None, "ingen"
    else:
        return None
    karta: Literal["baskarta", "parkkarta"] = "parkkarta" if _PARK.match(namn) else "baskarta"
    delar = tuple(re.findall(r"[A-Z][a-z0-9_]*", stam))
    if karta == "parkkarta" and delar and delar[0] == "Park":
        delar = delar[1:]
    return StockholmLager(
        namn=namn,
        karta=karta,
        stam=stam,
        delar=delar,
        geometri=geo,
        status=status,
        text=normalisera(stam),
    )


@dataclass(frozen=True)
class YtRegelGrammatik:
    objekt: re.Pattern[str]
    markslag: int | None = None
    roll: str | None = None
    standard_m: float = 0.0
    hoppa_over: bool = False
    kommentar: str = ""


@dataclass(frozen=True)
class LinjeRegelGrammatik:
    objekt: re.Pattern[str]
    roll: str
    standard_m: float
    kommentar: str = ""


@dataclass(frozen=True)
class StockholmGrammatik:
    """Reglerna ur profilens `[stockholm]`-tabell."""

    hoppa_over: re.Pattern[str]
    hoppa_over_geometri: frozenset[str]
    hoppa_over_status: frozenset[str]
    byggnad: re.Pattern[str]
    ytor: tuple[YtRegelGrammatik, ...]
    linjer: tuple[LinjeRegelGrammatik, ...]


@dataclass(frozen=True)
class Beslut:
    """Vad som görs med ett objekt på ett visst lager."""

    atgard: Atgard
    skal: str
    markslag: int | None = None
    roll: str | None = None
    standard_m: float = 0.0
    osaker: bool = False
    """Statusen är Odefinierad (eller okänd): med i resultatet men med högre osäkerhet."""


def _regex(tabell: Mapping[str, Any], nyckel: str, kontext: str) -> re.Pattern[str]:
    varde = tabell.get(nyckel)
    if not isinstance(varde, str) or not varde.strip():
        raise GrundkartaFel(f"{kontext}: '{nyckel}' måste vara ett reguljärt uttryck (sträng).")
    try:
        return re.compile(varde)
    except re.error as exc:
        raise GrundkartaFel(f"{kontext}: ogiltigt reguljärt uttryck '{varde}': {exc}") from exc


def tolka_grammatik(tabell: Mapping[str, Any], kalla: str) -> StockholmGrammatik:
    """Validerar `[stockholm]`-tabellen i en lagerprofil."""
    ok = {"hoppa_over", "hoppa_over_geometri", "hoppa_over_status", "byggnad", "ytor", "linjer"}
    extra = set(tabell) - ok
    if extra:
        raise GrundkartaFel(
            f"Lagerprofilen {kalla}: okända nycklar i [stockholm]: {sorted(extra)}."
        )
    kontext = f"Lagerprofil {kalla}, [stockholm]"
    geometri = tabell.get("hoppa_over_geometri", ["t", "glh", "s"])
    status = tabell.get("hoppa_over_status", ["skymd"])
    for namn, lista, giltiga in (
        ("hoppa_over_geometri", geometri, GEOMETRIER),
        ("hoppa_over_status", status, ("befintlig", "odefinierad", "skymd", "ingen")),
    ):
        if not isinstance(lista, list) or not all(v in giltiga for v in lista):
            raise GrundkartaFel(f"{kontext}: '{namn}' måste vara en lista med värden ur {giltiga}.")
    ytor: list[YtRegelGrammatik] = []
    for i, rad in enumerate(tabell.get("ytor", [])):
        k = f"{kontext}.ytor[{i}]"
        if not isinstance(rad, dict) or set(rad) - {
            "objekt",
            "markslag",
            "roll",
            "standard_m",
            "hoppa_over",
            "kommentar",
        }:
            raise GrundkartaFel(f"{k}: ogiltig tabell.")
        markslag, roll = rad.get("markslag"), rad.get("roll")
        hoppa = rad.get("hoppa_over", False)
        if hoppa is not True and hoppa is not False:
            raise GrundkartaFel(f"{k}: 'hoppa_over' måste vara true eller false.")
        if (markslag is not None) + (roll is not None) + hoppa != 1:
            raise GrundkartaFel(f"{k}: ange exakt en av 'markslag', 'roll' och 'hoppa_over'.")
        if markslag is not None and (
            isinstance(markslag, bool) or not isinstance(markslag, int) or not 1 <= markslag <= 8
        ):
            raise GrundkartaFel(f"{k}: 'markslag' måste vara 1–8 (byggnad 9 ges med roll).")
        if roll is not None and roll != "byggnad":
            raise GrundkartaFel(f'{k}: bara roll = "byggnad" stöds för ytor.')
        ytor.append(
            YtRegelGrammatik(
                objekt=_regex(rad, "objekt", k),
                markslag=markslag,
                roll=roll,
                standard_m=float(rad.get("standard_m", 10.0 if roll else 0.0)),
                hoppa_over=hoppa,
                kommentar=str(rad.get("kommentar", "")),
            )
        )
    linjer: list[LinjeRegelGrammatik] = []
    for i, rad in enumerate(tabell.get("linjer", [])):
        k = f"{kontext}.linjer[{i}]"
        if not isinstance(rad, dict) or set(rad) - {"objekt", "roll", "standard_m", "kommentar"}:
            raise GrundkartaFel(f"{k}: ogiltig tabell.")
        roll_ = rad.get("roll")
        sm = rad.get("standard_m")
        if not isinstance(roll_, str) or isinstance(sm, bool) or not isinstance(sm, int | float):
            raise GrundkartaFel(f"{k}: 'roll' (sträng) och 'standard_m' (tal) krävs.")
        linjer.append(
            LinjeRegelGrammatik(
                objekt=_regex(rad, "objekt", k),
                roll=roll_,
                standard_m=float(sm),
                kommentar=str(rad.get("kommentar", "")),
            )
        )
    return StockholmGrammatik(
        hoppa_over=_regex(tabell, "hoppa_over", kontext),
        hoppa_over_geometri=frozenset(geometri),
        hoppa_over_status=frozenset(status),
        byggnad=_regex(tabell, "byggnad", kontext),
        ytor=tuple(ytor),
        linjer=tuple(linjer),
    )


def bestam_atgard(
    gram: StockholmGrammatik, namn: str, objekt: Literal["yta", "linje"]
) -> Beslut | None:
    """Åtgärd för ett objekt (`yta` eller `linje`) på lagret `namn`; None om namnet inte följer
    grammatiken (då gäller profilens prefixregler).
    """
    lager = tolka_lagernamn(namn)
    if lager is None:
        return None
    osaker = lager.status in ("odefinierad", "okand")
    if lager.geometri in gram.hoppa_over_geometri:
        return Beslut("hoppa_over", f"geometri {lager.geometri} (text, höjdkurva eller symbol)")
    if lager.status in gram.hoppa_over_status:
        return Beslut("hoppa_over", f"status {lager.status} (skymt)")
    if gram.hoppa_over.search(lager.text):
        return Beslut("hoppa_over", "objekttyp som inte påverkar markslag eller vatten")
    ar_byggnad = bool(gram.byggnad.search(lager.text))
    if objekt == "yta":
        for regel in gram.ytor:
            if regel.objekt.search(lager.text):
                if regel.roll == "byggnad":
                    return Beslut(
                        "byggnad",
                        "byggnadsyta",
                        roll="byggnad",
                        standard_m=regel.standard_m,
                        osaker=osaker,
                    )
                if regel.hoppa_over or regel.markslag is None:
                    return Beslut("hoppa_over", regel.kommentar or "yta utan markslag")
                return Beslut(
                    "markslag", regel.kommentar or "yta", markslag=regel.markslag, osaker=osaker
                )
        return Beslut("okand_objekt", "ingen ytregel för objekttypen")
    # linje
    if lager.geometri == "gy":
        if ar_byggnad:
            return Beslut(
                "byggnad_kontur",
                "husets kontur på ett ytlager",
                roll="byggnad",
                standard_m=_byggnadshojd(gram),
                osaker=osaker,
            )
        return Beslut("hoppa_over", "kantlinje för en yta")
    if ar_byggnad:
        return Beslut("hoppa_over", "byggnadens linje ligger bredvid ytan med samma namn")
    for lregel in gram.linjer:
        if lregel.objekt.search(lager.text):
            atgard: Atgard = "oppning" if lregel.roll in ("trumma", "port") else "brytlinje"
            return Beslut(
                atgard,
                lregel.kommentar or "linje",
                roll=lregel.roll,
                standard_m=lregel.standard_m,
                osaker=osaker,
            )
    return Beslut("hoppa_over", "ingen brytlinje för objekttypen")


def _byggnadshojd(gram: StockholmGrammatik) -> float:
    for regel in gram.ytor:
        if regel.roll == "byggnad":
            return regel.standard_m
    return 10.0


def sammanfatta_beslut(
    rader: Sequence[tuple[str, Beslut]],
) -> list[str]:
    """Varningstexter: antal lager per hoppad orsak, okända objekttyper och osäkra lager."""
    okanda = sorted({n for n, b in rader if b.atgard == "okand_objekt"})
    osakra = sorted({n for n, b in rader if b.osaker and b.atgard != "hoppa_over"})
    ut: list[str] = []
    if okanda:
        ut.append(
            f"{len(okanda)} lager enligt Stockholms grammatik har ingen regel för objekttypen och "
            f"ignorerades: {', '.join(okanda[:8])}{' …' if len(okanda) > 8 else ''}."
        )
    if osakra:
        ut.append(
            f"{len(osakra)} lager har status Odefinierad (eller okänd) och är med med högre "
            f"osäkerhet: {', '.join(osakra[:8])}{' …' if len(osakra) > 8 else ''}."
        )
    return ut


@dataclass(frozen=True)
class LinjeUtfall:
    entiteter: list[tuple[str, list[tuple[float, float]], float | None]]
    regler: dict[str, tuple[str, float]]
    """Exakta lagerregler (lagernamn i versaler → roll, standardmått) för lager som tolkats."""
    varningar: list[str]


def tillampa_pa_linjer(
    entiteter: Sequence[tuple[str, Sequence[tuple[float, float]], float | None]],
    gram: StockholmGrammatik,
) -> LinjeUtfall:
    """Grammatiken på linjeentiteter (DXF): lager som följer den tolkas, hoppas över eller blir
    regler; övriga lager lämnas orörda (profilens prefixregler gäller).
    """
    kvar: list[tuple[str, list[tuple[float, float]], float | None]] = []
    regler: dict[str, tuple[str, float]] = {}
    rader: list[tuple[str, Beslut]] = []
    antal: dict[str, int] = {}
    hoppade: dict[str, int] = {}
    osakra = 0
    for lager, punkter, z in entiteter:
        beslut = bestam_atgard(gram, lager, "linje")
        if beslut is None:
            kvar.append((lager, list(punkter), z))
            continue
        rader.append((lager, beslut))
        antal[beslut.atgard] = antal.get(beslut.atgard, 0) + 1
        if beslut.atgard in ("hoppa_over", "okand_objekt"):
            hoppade[beslut.skal] = hoppade.get(beslut.skal, 0) + 1
            continue
        assert beslut.roll is not None
        regler.setdefault(lager.strip().upper(), (beslut.roll, beslut.standard_m))
        osakra += int(beslut.osaker)
        kvar.append((lager, list(punkter), z))
    varningar: list[str] = []
    if rader:
        varningar.append(
            "Stockholms lagergrammatik: "
            + ", ".join(f"{n} {a}" for a, n in sorted(antal.items()))
            + "."
        )
        if hoppade:
            varningar.append(
                "Hoppade över: "
                + "; ".join(f"{n} st {s}" for s, n in sorted(hoppade.items()))
                + "."
            )
        if osakra:
            varningar.append(f"{osakra} linjer ur lager med status Odefinierad (högre osäkerhet).")
        varningar.extend(sammanfatta_beslut(rader))
    return LinjeUtfall(kvar, regler, varningar)
