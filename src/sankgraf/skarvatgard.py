"""Åtgärdsförslag för skarvkontrollens fynd (R5): jämna ut skarven.

Skarvkontrollen (`sankgraf.skarvkontroll`, K49) säger var en ny eller djupare sänka uppstår vid
kanten på ny mark. Den här modulen föreslår en åtgärd på planförslaget och **verifierar** den:
varje förslag tillämpas på K9-ändringarna och kontrollen körs om, och det första förslag som tar
bort alla fynd väljs. Räcker inget förslag redovisas det som minskar tillkommande volym mest och
att det inte löser allt. Inget tal hittas på här; alla tal i svaret kommer ur kontrollens körningar.

Förslagen prövas i fast ordning (samma indata ger samma förslag):

1. **Slänt** på markytan närmast det värsta fyndet: nästa steg i `SLANT_FORHALLANDEN` (1:3, 1:5,
   1:10) över nuvarande förhållande (K9 `markhojd.slant`, R4).
2. **Utlopp**: en passage (K9 `oppning_ny`) som sänker marken till sänkans botten från botten genom
   överfallet och ut genom ytans kant, så att vattnet kommer förbi en uppdämd yta. Först längs
   riktningen botten -> överfall, sedan mot ytans mitt.

Slänten tar bort kanten och halvcellstrappan men inte en dammvall tvärs en dalgång (se
`docs/design/RITVERKTYG.md` 6.3); därför finns utloppet.

Svaret är kontraktet `floden-skarvatgard/v1` (K49, tillägg): den föreslagna ändringen som
instruktioner på K9-ändringar (`andra`: nya egenskaper på en befintlig ändring, `nya`: nya
ändringar), kontrollens utfall före och efter, och en text. Klienten tillämpar instruktionerna på
objekten i planförslaget (efter bekräftelse) och kör kontrollen igen.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

KONTRAKT_ATGARD = "floden-skarvatgard/v1"

SLANT_FORHALLANDEN: tuple[float, ...] = (3.0, 5.0, 10.0)
"""Släntförhållanden (1:n) som prövas, i stigande ordning."""
UTLOPP_BREDD_M = 1.0
UTLOPP_FORLANGNING_M = 2.0
"""Utloppet sträcker sig så här långt förbi ytans kant (m)."""
MIN_STRACKA_M = 0.5
UTLOPP_ID = "skarv_utlopp"
MAX_FORSOK = len(SLANT_FORHALLANDEN) + 2
MAX_RUNDOR = 3
"""Högst så många steg (ett per fynd som åtgärdas) i ett förslag."""

Kor = Callable[[list[dict[str, Any]]], "dict[str, Any] | None"]
"""Kör skarvkontrollen på en lista K9-ändringar och ger K49-svaret (eller None utan fynd/mark)."""


def _sv(v: float, n: int) -> str:
    return f"{v:.{n}f}".replace(".", ",")


def _kopia(a: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [copy.deepcopy(dict(x)) for x in a]


def _ring(a: Mapping[str, Any]) -> list[tuple[float, float]]:
    g = a.get("geometri") or {}
    return [(float(x), float(y)) for x, y in (g.get("coordinates") or [[]])[0]]


def _antal(skarvar: Mapping[str, Any] | None) -> int:
    return 0 if not skarvar else int(skarvar.get("antal", 0))


def _volym(skarvar: Mapping[str, Any] | None) -> float:
    return 0.0 if not skarvar else float(skarvar.get("tillkommande_volym_m3", 0.0))


def valj_markyta(
    andringar: Sequence[Mapping[str, Any]], fynd: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Markytan (K9 `markhojd`) närmast fyndet; vid lika avstånd den med lägst id."""
    from shapely.geometry import Point, Polygon

    p = Point(float(fynd["x_m"]), float(fynd["y_m"]))
    basta: tuple[float, str, dict[str, Any]] | None = None
    for a in andringar:
        if a.get("typ") != "markhojd":
            continue
        ring = _ring(a)
        if len(ring) < 4:
            continue
        d = Polygon(ring).distance(p)
        nyckel = (round(d, 6), str(a.get("andring_id", "")))
        if basta is None or nyckel < (round(basta[0], 6), basta[1]):
            basta = (d, str(a.get("andring_id", "")), dict(a))
    return None if basta is None else basta[2]


def _nuvarande_forhallande(a: Mapping[str, Any]) -> float | None:
    s = (a.get("egenskaper") or {}).get("slant")
    if isinstance(s, Mapping) and isinstance(s.get("forhallande"), int | float):
        return float(s["forhallande"])
    return None


def _slant_kandidater(mark: Mapping[str, Any]) -> list[dict[str, Any]]:
    nu = _nuvarande_forhallande(mark)
    ut: list[dict[str, Any]] = []
    for n in SLANT_FORHALLANDEN:
        if nu is not None and n <= nu:
            continue
        gammal = dict((mark.get("egenskaper") or {}).get("slant") or {})
        ny = {k: v for k, v in gammal.items() if k == "markslag"}
        ny["forhallande"] = n
        ut.append(
            {
                "slag": "slant",
                "text": (
                    f"Ge ytan '{mark['andring_id']}' en slänt 1:{_sv(n, 0)} mot marken "
                    "i stället för en kant."
                ),
                "andra": [{"andring_id": str(mark["andring_id"]), "egenskaper": {"slant": ny}}],
                "nya": [],
            }
        )
    return ut


def _ledigt_id(andringar: Sequence[Mapping[str, Any]], bas: str) -> str:
    ids = {str(a.get("andring_id")) for a in andringar}
    if bas not in ids:
        return bas
    n = 2
    while f"{bas}_{n}" in ids:
        n += 1
    return f"{bas}_{n}"


def _strale_ut(
    ring: Sequence[tuple[float, float]], start: tuple[float, float], riktning: tuple[float, float]
) -> tuple[float, float] | None:
    """Där en stråle från `start` i `riktning` lämnar ytan (den sista skärningen), eller None."""
    from shapely.geometry import LineString, Polygon

    langd = math.hypot(*riktning)
    if langd < 1e-9 or len(ring) < 4:
        return None
    rx, ry = riktning[0] / langd, riktning[1] / langd
    stral = LineString([start, (start[0] + rx * 5000.0, start[1] + ry * 5000.0)])
    snitt = stral.intersection(Polygon(ring))
    if snitt.is_empty:
        return None
    delar = [snitt] if snitt.geom_type == "LineString" else list(getattr(snitt, "geoms", []))
    delar = [d for d in delar if d.geom_type == "LineString" and d.length > 1e-6]
    if not delar:
        return None
    sista = max(
        (c for d in delar for c in d.coords),
        key=lambda c: (c[0] - start[0]) * rx + (c[1] - start[1]) * ry,
    )
    return float(sista[0]), float(sista[1])


def _utlopp_kandidater(
    andringar: Sequence[Mapping[str, Any]], mark: Mapping[str, Any], fynd: Mapping[str, Any]
) -> list[dict[str, Any]]:
    ring = _ring(mark)
    start = (float(fynd["x_m"]), float(fynd["y_m"]))
    over = (float(fynd["overfall_x_m"]), float(fynd["overfall_y_m"]))
    cx = sum(p[0] for p in ring[:-1]) / max(1, len(ring) - 1)
    cy = sum(p[1] for p in ring[:-1]) / max(1, len(ring) - 1)
    riktningar = [
        ("överfallet", (over[0] - start[0], over[1] - start[1])),
        ("ytans mitt", (cx - start[0], cy - start[1])),
    ]
    ut: list[dict[str, Any]] = []
    sedda: list[tuple[float, float]] = []
    for namn, d in riktningar:
        avst = math.hypot(*d)
        slut = _strale_ut(ring, start, d)
        if avst < 1e-6 or slut is None:
            continue
        rx, ry = d[0] / avst, d[1] / avst
        ände = (slut[0] + rx * UTLOPP_FORLANGNING_M, slut[1] + ry * UTLOPP_FORLANGNING_M)
        if math.hypot(ände[0] - start[0], ände[1] - start[1]) < MIN_STRACKA_M:
            continue
        if any(math.hypot(ände[0] - s[0], ände[1] - s[1]) < 0.5 for s in sedda):
            continue
        sedda.append(ände)
        oid = _ledigt_id(andringar, UTLOPP_ID)
        botten = round(float(fynd["botten_rh2000"]), 2)
        langd = math.hypot(ände[0] - start[0], ände[1] - start[1])
        ut.append(
            {
                "slag": "utlopp",
                "text": (
                    f"Lägg ett utlopp (passage, {_sv(UTLOPP_BREDD_M, 1)} m bred, "
                    f"{_sv(langd, 0)} m lång) från sänkans botten mot {namn} och ut genom "
                    f"ytans kant, sänkt till {_sv(botten, 2)} m RH 2000."
                ),
                "andra": [],
                "nya": [
                    {
                        "typ": "oppning_ny",
                        "andring_id": oid,
                        "geometri": {
                            "type": "LineString",
                            "coordinates": [
                                [round(start[0], 2), round(start[1], 2)],
                                [round(ände[0], 2), round(ände[1], 2)],
                            ],
                        },
                        "egenskaper": {
                            "typ": "passage",
                            "bredd_m": UTLOPP_BREDD_M,
                            "botten_rh2000": botten,
                        },
                    }
                ],
            }
        )
    return ut


def kandidater(
    andringar: Sequence[Mapping[str, Any]], fynd: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Markytan som åtgärdas och förslagen i prövningsordning (slänt, sedan utlopp)."""
    mark = valj_markyta(andringar, fynd)
    if mark is None:
        return None, []
    return mark, [*_slant_kandidater(mark), *_utlopp_kandidater(andringar, mark, fynd)][:MAX_FORSOK]


def tillampa(
    andringar: Sequence[Mapping[str, Any]], kandidat: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """K9-ändringarna efter ett förslag (originalet ändras inte)."""
    ut = _kopia(andringar)
    for r in kandidat["andra"]:
        for a in ut:
            if a.get("andring_id") == r["andring_id"]:
                a["egenskaper"] = {**(a.get("egenskaper") or {}), **copy.deepcopy(r["egenskaper"])}
    ut.extend(copy.deepcopy(kandidat["nya"]))
    return ut


def _utfall(skarvar: Mapping[str, Any] | None) -> dict[str, Any]:
    return {"antal": _antal(skarvar), "tillkommande_volym_m3": round(_volym(skarvar), 2)}


def _slut_ihop(
    steg: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stegens `andra` (senare steg ersätter tidigare per id) och `nya` (i ordning)."""
    andra: dict[str, dict[str, Any]] = {}
    nya: list[dict[str, Any]] = []
    for k in steg:
        for r in k["andra"]:
            gammal = andra.get(r["andring_id"], {"andring_id": r["andring_id"], "egenskaper": {}})
            andra[r["andring_id"]] = {
                "andring_id": r["andring_id"],
                "egenskaper": {**gammal["egenskaper"], **copy.deepcopy(r["egenskaper"])},
            }
        nya.extend(copy.deepcopy(k["nya"]))
    return list(andra.values()), nya


def foresla(
    andringar: Sequence[Mapping[str, Any]], skarvar: Mapping[str, Any] | None, kor: Kor
) -> dict[str, Any]:
    """Föreslår och verifierar en åtgärd. `skarvar` är nuvarande K49-svar för `andringar`.

    Varje runda tar det värsta fyndet, prövar förslagen i ordning och behåller det första som tar
    bort alla fynd, annars det som ger minst (antal, tillkommande volym) om det förbättrar läget.
    Blir något kvar tas nästa runda (högst `MAX_RUNDOR`), så att flera sänkor längs samma kant får
    flera steg. Svaret: `kontrakt`, `fore` (antal och tillkommande volym nu) och antingen `atgard`
    (`slag`, `text`, `steg`, `mal_id`, `andra`, `nya`, `efter`, `loser_allt`) eller `atgard: None`
    med `skal`. Samma indata ger samma svar.
    """
    ut: dict[str, Any] = {
        "kontrakt": KONTRAKT_ATGARD,
        "fore": _utfall(skarvar),
        "atgard": None,
        "skal": "",
    }
    if not (skarvar or {}).get("fynd"):
        ut["skal"] = "Skarvkontrollen har inga fynd att åtgärda."
        return ut
    nu_andringar = _kopia(andringar)
    nu_skarvar: Mapping[str, Any] | None = skarvar
    steg: list[dict[str, Any]] = []
    mal_id = ""
    skal = ""
    for _ in range(MAX_RUNDOR):
        fynd = list((nu_skarvar or {}).get("fynd") or [])
        if not fynd:
            break
        mark, lista = kandidater(nu_andringar, fynd[0])  # störst tillkommande volym först
        if mark is None:
            skal = "Förslaget har ingen markyta att jämna ut."
            break
        mal_id = mal_id or str(mark["andring_id"])
        if not lista:
            skal = (
                f"Ytan '{mark['andring_id']}' har redan den flackaste slänten som prövas, "
                "och inget utlopp går att lägga."
            )
            break
        basta: tuple[tuple[int, float], dict[str, Any], dict[str, Any] | None] | None = None
        for k in lista:
            efter = kor(tillampa(nu_andringar, k))
            nyckel = (_antal(efter), _volym(efter))
            if basta is None or nyckel < basta[0]:
                basta = (nyckel, k, efter)
            if nyckel[0] == 0:
                break
        assert basta is not None
        nyckel, k, efter = basta
        if nyckel[0] >= _antal(nu_skarvar) and nyckel[1] >= _volym(nu_skarvar) - 1e-9:
            skal = (
                "Ingen av åtgärderna som prövades (slänt, utlopp) minskar sänkorna; "
                "ytans nivå eller läge behöver ändras för hand."
            )
            break
        steg.append(k)
        nu_andringar = tillampa(nu_andringar, k)
        nu_skarvar = efter
    if not steg:
        ut["skal"] = skal or "Ingen åtgärd hittades."
        return ut
    andra, nya = _slut_ihop(steg)
    slag = steg[0]["slag"] if len({k["slag"] for k in steg}) == 1 else "kombination"
    ut["atgard"] = {
        "slag": slag,
        "text": " ".join(f"{i + 1}. {k['text']}" for i, k in enumerate(steg))
        if len(steg) > 1
        else steg[0]["text"],
        "steg": [{"slag": k["slag"], "text": k["text"]} for k in steg],
        "mal_id": mal_id,
        "andra": andra,
        "nya": nya,
        "efter": _utfall(nu_skarvar),
        "loser_allt": _antal(nu_skarvar) == 0,
    }
    if _antal(nu_skarvar) > 0:
        ut["skal"] = skal or "Åtgärden minskar sänkorna men tar inte bort alla."
    return ut
