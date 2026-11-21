"""DWG-baskartor via LibreDWG:s `dwgread -O JSON`.

Python-port av O-Tids `scripts/geo-dwg.ts` (ADR-0219 K6b), https://github.com/joelberring/otid,
commit 1d63297, MIT-licens, © 2026 Joel Berring. Ändringar mot originalet: Flodens namn och typer,
z-värden behålls där de finns, trasiga objekt räknas och redovisas i stället för att hoppas över
tyst, och resultatet matas in i Flodens baskartekedja (`grundkarta.bygg_baskarta_fran_entiteter`)
med en lagerprofil.

LibreDWG (GPL-3, https://www.gnu.org/software/libredwg/) körs som ett separat program (`dwgread`);
ingen kod ur LibreDWG finns här. Omvandling DWG → DXF tappar skraffyrerna, där baskartans ytor
ligger, därför läses dwgreads JSON direkt. JSON-utdata kan bli mycket stort, så objekten läses ett
i taget ur strömmen.

Modellrymdens objekt blir `DwgObjekt` med lagrets namn: linjer, polylinjer (med bågar), cirklar och
bågar, skraffyrernas kanter som ytor, fyllda fyrhörningar, splines (genom sina passpunkter),
punkter och blockens insättningspunkter (med blockets namn). Texter hoppas över.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from markmodell.grundkarta import (
    BaskartaResultat,
    GrundkartaFel,
    LagerMappning,
    _bygg_lagerregler,
    _matcha_lager,
    bygg_baskarta_fran_entiteter,
)
from markmodell.lagerprofil import Lagerprofil
from markmodell.stockholm_lager import Beslut, bestam_atgard, sammanfatta_beslut

Xy = tuple[float, float]
Json = dict[str, Any]
GeometriTyp = Literal["punkt", "linje", "yta"]

_BAGSTEG_RAD = math.pi / 18  # högst 10° mellan punkterna längs en båge
_OBJEKT_START = re.compile(r'"OBJECTS"\s*:\s*\[')


@dataclass(frozen=True)
class DwgObjekt:
    """Ett objekt i ritningens modellrymd.

    `punkter` för punkt (en punkt) och linje; `ringar` för yta (skraffyrens kanter i ritningens
    ordning,
    tolkas med jämn–udda-regeln vid rastrering). `z` är medelhöjden när ritningen har höjder,
    annars None.
    """

    lager: str
    typ: GeometriTyp
    entitet: str
    punkter: tuple[Xy, ...] = ()
    ringar: tuple[tuple[Xy, ...], ...] = ()
    z: float | None = None
    block: str | None = None


@dataclass(frozen=True)
class MarkslagsYta:
    """En skraffyryta som matchar en ytregel i lagerprofilen."""

    markslag: int
    lager: str
    ringar: tuple[tuple[Xy, ...], ...]
    osaker: bool = False
    """Lagret har status Odefinierad (Stockholms grammatik): med, men med högre osäkerhet."""


@dataclass(frozen=True)
class DwgBaskartaResultat:
    """Baskartan ur en DWG: samma linjeresultat som DXF plus markslagsytor ur skraffyrerna."""

    baskarta: BaskartaResultat
    markslagsytor: tuple[MarkslagsYta, ...]
    antal_objekt: int
    antal_overhoppade: int
    antal_trasiga_json: int
    lagerprofil_sha256: str | None


class DwgJsonStrom:
    """Läser objekten i dwgreads JSON-lista `OBJECTS` ett i taget ur en ström av textbitar.

    Klamrarna räknas utanför strängar så att varje objekt kan tolkas för sig; bara ett objekt
    hålls i minnet.
    Objekt som inte går att tolka räknas i `antal_trasiga` (inga tysta fel).
    """

    def __init__(self, bitar: Iterable[str]) -> None:
        self._bitar = bitar
        self.antal_trasiga = 0
        self.hittade_lista = False

    def __iter__(self) -> Iterator[Json]:
        fore = True
        text = ""
        djup = 0
        i_strang = False
        escape = False
        start = -1
        skannat = 0
        for bit in self._bitar:
            text += bit
            if fore:
                traff = _OBJEKT_START.search(text)
                if traff is None:
                    text = text[-64:]
                    continue
                text = text[traff.end() :]
                fore = False
                self.hittade_lista = True
                skannat = 0
            i = skannat
            while i < len(text):
                c = text[i]
                if i_strang:
                    if escape:
                        escape = False
                    elif c == "\\":
                        escape = True
                    elif c == '"':
                        i_strang = False
                elif c == '"':
                    i_strang = True
                elif c == "{":
                    if djup == 0:
                        start = i
                    djup += 1
                elif c == "}":
                    djup -= 1
                    if djup == 0 and start >= 0:
                        try:
                            objekt = json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            self.antal_trasiga += 1
                        else:
                            if isinstance(objekt, dict):
                                yield objekt
                            else:
                                self.antal_trasiga += 1
                        start = -1
                elif c == "]" and djup == 0:
                    return
                i += 1
            # Det som redan lästs klart släpps; ett påbörjat objekt behålls.
            if start >= 0:
                text = text[start:]
                skannat = len(text)
                start = 0
            else:
                text = ""
                skannat = 0


def _handtag(varde: Any) -> int | None:
    """Handtagets värde ur [kod, storlek, värde] eller referensen [kod, storlek, värde, absolut]."""
    if not isinstance(varde, list) or len(varde) < 3:
        return None
    sista = varde[-1]
    return sista if isinstance(sista, int) and not isinstance(sista, bool) else None


def _lista(varde: Any) -> list[Any]:
    return varde if isinstance(varde, list) else []


def _tal(varde: Any, reserv: float = 0.0) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float) or not math.isfinite(varde):
        return reserv
    return float(varde)


def _xy(varde: Any) -> Xy | None:
    if (
        isinstance(varde, list)
        and len(varde) >= 2
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in varde[:2])
        and math.isfinite(varde[0])
        and math.isfinite(varde[1])
    ):
        return (float(varde[0]), float(varde[1]))
    return None


def _z(varde: Any) -> float | None:
    if isinstance(varde, list) and len(varde) >= 3:
        z = varde[2]
        if isinstance(z, int | float) and not isinstance(z, bool) and math.isfinite(z):
            return float(z)
    return None


def _medel(zs: Iterable[float | None]) -> float | None:
    """Medelhöjden; None om ritningen saknar höjd (bara z = 0 räknas som 2D-ritning)."""
    varden = [z for z in zs if z is not None]
    if not varden or all(z == 0.0 for z in varden):
        return None
    return sum(varden) / len(varden)


def bagpunkter(centrum: Xy, radie: float, start: float, slut: float) -> list[Xy]:
    """Punkterna längs en båge från `start` till `slut` (radianer, moturs), högst 10° isär."""
    svep = slut - start
    while svep <= 0:
        svep += 2 * math.pi
    steg = max(2, math.ceil(svep / _BAGSTEG_RAD))
    return [
        (
            centrum[0] + radie * math.cos(start + svep * i / steg),
            centrum[1] + radie * math.sin(start + svep * i / steg),
        )
        for i in range(steg + 1)
    ]


def bulge_segment(a: Xy, b: Xy, bulge: float) -> list[Xy]:
    """Sträckan a→b med båge (`bulge` = tan(vinkel/4), positiv moturs); utan `a`."""
    if not bulge or abs(bulge) < 1e-9:
        return [b]
    vinkel = 4 * math.atan(bulge)
    korda = math.hypot(b[0] - a[0], b[1] - a[1])
    if korda == 0:
        return [b]
    radie = korda / (2 * math.sin(abs(vinkel) / 2))
    mitt = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    till_centrum = radie * math.cos(abs(vinkel) / 2)
    tecken = 1.0 if bulge > 0 else -1.0
    normal = (-(b[1] - a[1]) / korda * tecken, (b[0] - a[0]) / korda * tecken)
    centrum = (mitt[0] + normal[0] * till_centrum, mitt[1] + normal[1] * till_centrum)
    fran = math.atan2(a[1] - centrum[1], a[0] - centrum[0])
    steg = max(2, math.ceil(abs(vinkel) / _BAGSTEG_RAD))
    ut: list[Xy] = []
    for i in range(steg):
        if i == steg - 1:
            ut.append(b)
        else:
            t = fran + vinkel * (i + 1) / steg
            ut.append((centrum[0] + radie * math.cos(t), centrum[1] + radie * math.sin(t)))
    return ut


def _med_bagar(punkter: list[tuple[Xy, float]], sluten: bool) -> list[Xy]:
    """Punkterna med bågarna; sluten = tillbaka till den första."""
    if not punkter:
        return []
    ut = [punkter[0][0]]
    antal = len(punkter) if sluten else len(punkter) - 1
    for i in range(antal):
        ut.extend(bulge_segment(punkter[i][0], punkter[(i + 1) % len(punkter)][0], punkter[i][1]))
    return ut


def _ring(punkter: list[Xy]) -> list[Xy] | None:
    if len(punkter) < 3:
        return None
    return punkter if punkter[0] == punkter[-1] else [*punkter, punkter[0]]


def _skraffyr_ring(bana: Json) -> list[Xy] | None:
    """En skraffyrs kant: polylinje (med bågar) eller kanter (raka och bågar)."""
    if isinstance(bana.get("polyline_paths"), list):
        horn: list[tuple[Xy, float]] = []
        for p in bana["polyline_paths"]:
            at = _xy(p.get("point")) if isinstance(p, dict) else None
            if at:
                horn.append((at, _tal(p.get("bulge"))))
        return _ring(_med_bagar(horn, True))
    segment = bana.get("segs")
    if not isinstance(segment, list):
        return None
    ut: list[Xy] = []
    for seg in segment:
        if not isinstance(seg, dict):
            continue
        if seg.get("curve_type") == 1:
            a, b = _xy(seg.get("first_endpoint")), _xy(seg.get("second_endpoint"))
            if a and b:
                if not ut:
                    ut.append(a)
                ut.append(b)
        elif seg.get("curve_type") == 2:
            centrum, radie = _xy(seg.get("center")), _tal(seg.get("radius"))
            if not centrum or not radie > 0:
                continue
            start, slut = _tal(seg.get("start_angle")), _tal(seg.get("end_angle"))
            # Medurs: vinklarna är speglade i x-axeln (som i DXF), bågen går −start → −slut.
            if seg.get("is_ccw") == 0:
                punkter = list(reversed(bagpunkter(centrum, radie, -slut, -start)))
            else:
                punkter = bagpunkter(centrum, radie, start, slut)
            ut.extend(punkter if not ut else punkter[1:])
    return _ring(ut)


@dataclass
class _Vantande:
    lager: int | None
    bygg: Callable[[], DwgObjekt | None]
    block: int | None = None
    agare: int | None = None
    entitet: str = ""


def dwg_objekt(objekt: Iterable[Json]) -> tuple[list[DwgObjekt], int]:
    """Ritningens modellrymd som `DwgObjekt` med lagrets namn (och blockets för insättningar).

    Returnerar (objekt, antal överhoppade). Överhoppade är texter och andra okända entiteter,
    blockens
    innehåll, pappersrymden och objekt utan giltig geometri.
    """
    lager_namn: dict[int, str] = {}
    block_namn: dict[int, str] = {}
    vantande: list[_Vantande] = []
    horn: dict[int, list[tuple[Xy, float, float | None]]] = {}
    overhoppade = 0

    for obj in objekt:
        eget = _handtag(obj.get("handle"))
        if obj.get("object") == "LAYER" and eget is not None and isinstance(obj.get("name"), str):
            lager_namn[eget] = obj["name"]
            continue
        if (
            obj.get("object") == "BLOCK_HEADER"
            and eget is not None
            and isinstance(obj.get("name"), str)
        ):
            block_namn[eget] = obj["name"]
            continue
        sort = obj.get("entity")
        if not isinstance(sort, str):
            continue
        if sort in ("VERTEX_2D", "VERTEX_3D"):
            agare, at = _handtag(obj.get("ownerhandle")), _xy(obj.get("point"))
            if agare is not None and at:
                horn.setdefault(agare, []).append(
                    (at, _tal(obj.get("bulge")), _z(obj.get("point")))
                )
            continue
        # Bara modellrymden: `entmode` 2, eller 0 med ägaren *Model_Space (prövas sist).
        entmode = obj.get("entmode")
        if entmode not in (0, 2):
            overhoppade += 1
            continue
        agare_handtag = (_handtag(obj.get("ownerhandle")) or -1) if entmode == 0 else None
        lager = _handtag(obj.get("layer"))
        bygg = _byggare(sort, obj, eget, horn)
        if bygg is None:
            overhoppade += 1
            continue
        block = _handtag(obj.get("block_header")) if sort == "INSERT" else None
        vantande.append(
            _Vantande(lager=lager, bygg=bygg, block=block, agare=agare_handtag, entitet=sort)
        )

    modellrymd = {k for k, namn in block_namn.items() if namn.upper() == "*MODEL_SPACE"}
    ut: list[DwgObjekt] = []
    for post in vantande:
        if post.agare is not None and post.agare not in modellrymd:
            overhoppade += 1
            continue
        geometri = post.bygg()
        if geometri is None:
            overhoppade += 1
            continue
        namn = lager_namn.get(post.lager, "0") if post.lager is not None else "0"
        blocknamn = block_namn.get(post.block) if post.block is not None else None
        ut.append(
            DwgObjekt(
                lager=namn,
                typ=geometri.typ,
                entitet=post.entitet,
                punkter=geometri.punkter,
                ringar=geometri.ringar,
                z=geometri.z,
                block=blocknamn,
            )
        )
    return ut, overhoppade


def _linje(entitet: str, punkter: list[Xy], z: float | None) -> DwgObjekt | None:
    return (
        DwgObjekt("", "linje", entitet, punkter=tuple(punkter), z=z) if len(punkter) >= 2 else None
    )


def _byggare(
    sort: str, obj: Json, eget: int | None, horn: dict[int, list[tuple[Xy, float, float | None]]]
) -> Callable[[], DwgObjekt | None] | None:
    """Byggare för objektets geometri (körs när strömmen är läst); None för okänd sort."""
    if sort == "LINE":
        a, b = _xy(obj.get("start")), _xy(obj.get("end"))
        z = _medel([_z(obj.get("start")), _z(obj.get("end"))])
        return lambda: _linje(sort, [a, b], z) if a and b else None
    if sort == "LWPOLYLINE":
        punkter = [p for p in (_xy(r) for r in _lista(obj.get("points"))) if p]
        bulges = _lista(obj.get("bulges"))
        flagga = int(_tal(obj.get("flag")))
        sluten = bool(flagga & 512) or bool(flagga & 1)
        hojd = obj.get("elevation")
        z = _medel([_tal(hojd)]) if isinstance(hojd, int | float) else None
        return lambda: _linje(
            sort,
            _med_bagar(
                [(at, _tal(bulges[i]) if i < len(bulges) else 0.0) for i, at in enumerate(punkter)],
                sluten,
            ),
            z,
        )
    if sort in ("POLYLINE_2D", "POLYLINE_3D"):
        sluten = bool(int(_tal(obj.get("flag"))) & 1)

        def polylinje() -> DwgObjekt | None:
            if eget is None:
                return None
            rader = horn.get(eget, [])
            return _linje(
                sort,
                _med_bagar([(at, b) for at, b, _ in rader], sluten),
                _medel(z for _, _, z in rader),
            )

        return polylinje
    if sort == "ARC":
        centrum, radie = _xy(obj.get("center")), _tal(obj.get("radius"))
        start, slut = _tal(obj.get("start_angle")), _tal(obj.get("end_angle"))
        z = _medel([_z(obj.get("center"))])
        return lambda: (
            _linje(sort, bagpunkter(centrum, radie, start, slut), z)
            if centrum and radie > 0
            else None
        )
    if sort == "CIRCLE":
        centrum, radie = _xy(obj.get("center")), _tal(obj.get("radius"))
        z = _medel([_z(obj.get("center"))])

        def cirkel() -> DwgObjekt | None:
            ring = (
                _ring(bagpunkter(centrum, radie, 0, 2 * math.pi)) if centrum and radie > 0 else None
            )
            return DwgObjekt("", "yta", sort, ringar=(tuple(ring),), z=z) if ring else None

        return cirkel
    if sort == "HATCH":
        banor = _lista(obj.get("paths"))
        ringar = [r for r in (_skraffyr_ring(b) for b in banor if isinstance(b, dict)) if r]
        return lambda: (
            DwgObjekt("", "yta", sort, ringar=tuple(tuple(r) for r in ringar)) if ringar else None
        )
    if sort == "SOLID":
        horn_xy = [
            p for p in (_xy(obj.get(k)) for k in ("corner1", "corner2", "corner4", "corner3")) if p
        ]

        def solid() -> DwgObjekt | None:
            unika = [p for i, p in enumerate(horn_xy) if i == 0 or p != horn_xy[i - 1]]
            ring = _ring(unika)
            return (
                DwgObjekt("", "yta", sort, ringar=(tuple(ring),))
                if ring and len(ring) >= 4
                else None
            )

        return solid
    if sort == "SPLINE":
        passpunkter = [p for p in (_xy(r) for r in _lista(obj.get("fit_pts"))) if p]
        return lambda: _linje(sort, passpunkter, None)
    if sort == "POINT":
        x, y = _tal(obj.get("x"), math.nan), _tal(obj.get("y"), math.nan)
        z_p = obj.get("z")
        z = _medel([_tal(z_p)]) if isinstance(z_p, int | float) else None
        return lambda: (
            DwgObjekt("", "punkt", sort, punkter=((x, y),), z=z)
            if math.isfinite(x) and math.isfinite(y)
            else None
        )
    if sort == "INSERT":
        at = _xy(obj.get("ins_pt"))
        z = _medel([_z(obj.get("ins_pt"))])
        return lambda: DwgObjekt("", "punkt", sort, punkter=(at,), z=z) if at else None
    return None


def las_dwg_json(bitar: Iterable[str]) -> tuple[list[DwgObjekt], int, int]:
    """Läser dwgreads JSON (textbitar) → (objekt, antal överhoppade, antal trasiga objekt)."""
    strom = DwgJsonStrom(bitar)
    objekt, overhoppade = dwg_objekt(strom)
    if not strom.hittade_lista:
        raise GrundkartaFel(
            'dwgreads JSON saknar listan "OBJECTS" – är det verkligen utdata från dwgread -O JSON?'
        )
    return objekt, overhoppade, strom.antal_trasiga


def _las_fil_i_bitar(sokvag: Path, storlek: int = 1 << 20) -> Iterator[str]:
    with sokvag.open("r", encoding="utf-8", errors="replace") as fil:
        while bit := fil.read(storlek):
            yield bit


def hitta_dwgread(dwgread: str | Path | None = None) -> Path:
    """LibreDWG:s dwgread: argumentet, annars FLODEN_DWGREAD, annars `dwgread` på PATH."""
    kandidat = (
        str(dwgread)
        if dwgread is not None
        else os.environ.get("FLODEN_DWGREAD") or shutil.which("dwgread")
    )
    if not kandidat or not Path(kandidat).is_file():
        raise GrundkartaFel(
            "LibreDWG:s dwgread hittades inte. Installera LibreDWG (GPL-3) och ange sökvägen med "
            "FLODEN_DWGREAD eller argumentet dwgread."
        )
    return Path(kandidat)


def kor_dwgread(
    dwg_fil: str | Path,
    *,
    dwgread: str | Path | None = None,
    json_fil: str | Path | None = None,
    tidsgrans_s: float = 20 * 60,
) -> Path:
    """Kör `dwgread -O JSON -o <json> <dwg>`; returnerar JSON-filen (förval: tillfällig fil)."""
    program = hitta_dwgread(dwgread)
    indata = Path(dwg_fil)
    if not indata.is_file():
        raise GrundkartaFel(f"DWG-filen hittades inte: {indata}")
    utdata = (
        Path(json_fil)
        if json_fil is not None
        else Path(tempfile.mkdtemp(prefix="floden-dwg-")) / "ritning.json"
    )
    try:
        resultat = subprocess.run(
            [str(program), "-O", "JSON", "-o", str(utdata), str(indata)],
            capture_output=True,
            text=True,
            timeout=tidsgrans_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GrundkartaFel(
            f"dwgread tog längre tid än {tidsgrans_s:.0f} s för {indata.name}."
        ) from exc
    if resultat.returncode != 0 or not utdata.is_file():
        svans = " ".join((resultat.stderr or "").strip().splitlines()[-3:])
        raise GrundkartaFel(
            f"dwgread avslutades med kod {resultat.returncode} för {indata.name}: {svans}"
        )
    return utdata


def _grammatikvarningar(
    antal: Counter[str], hoppade: Counter[str], osakra_linjer: int
) -> list[str]:
    delar = [
        f"{antal['markslag']} ytor som markslag",
        f"{antal['byggnad']} byggnadsytor",
        f"{antal['byggnad_kontur']} byggnadskonturer",
        f"{antal['brytlinje'] + antal['oppning']} linjer som brytlinjer/öppningar",
        f"{antal['hoppa_over']} hoppade över",
        f"{antal['okand_objekt']} utan regel",
    ]
    ut = ["Stockholms lagergrammatik: " + ", ".join(delar) + "."]
    if hoppade:
        ut.append(
            "Hoppade över: "
            + "; ".join(f"{n} st {skal}" for skal, n in sorted(hoppade.items()))
            + "."
        )
    if osakra_linjer:
        ut.append(f"{osakra_linjer} linjer ur lager med status Odefinierad (högre osäkerhet).")
    return ut


def las_baskarta_dwg(
    kalla: str | Path | Iterable[str],
    *,
    lagerprofil: Lagerprofil | None = None,
    snap_tolerans_m: float = 0.25,
    ar_dwg: bool | None = None,
    dwgread: str | Path | None = None,
) -> DwgBaskartaResultat:
    """Läser en baskarta ur DWG (via dwgread), ur dwgreads JSON-fil eller ur textbitar av sådan
    JSON.

    Linjer och polylinjer (även ytors ytterkanter) matas genom samma kedja som `las_baskarta_dxf`
    med
    profilens linjeregler. Ytor (skraffyrer, cirklar, solider) på lager som matchar profilens
    ytregler blir
    markslagsytor. Utan profil används `STANDARD_STOCKHOLM_LAGERMAPPNING` och inga ytregler.
    """
    if isinstance(kalla, str | Path):
        fil = Path(kalla)
        if not fil.is_file():
            raise GrundkartaFel(f"Filen hittades inte: {fil}")
        om_dwg = fil.suffix.lower() == ".dwg" if ar_dwg is None else ar_dwg
        json_fil = kor_dwgread(fil, dwgread=dwgread) if om_dwg else fil
        objekt, overhoppade, trasiga = las_dwg_json(_las_fil_i_bitar(json_fil))
    else:
        objekt, overhoppade, trasiga = las_dwg_json(kalla)

    regler: tuple[LagerMappning, ...] = (
        lagerprofil.linjeregler if lagerprofil is not None else _bygg_lagerregler(None)
    )
    gram = lagerprofil.stockholm if lagerprofil is not None else None
    lager_med_yta = {o.lager for o in objekt if o.typ == "yta"}
    grammatikregler: dict[str, LagerMappning] = {}
    beslutsrader: list[tuple[str, Beslut]] = []
    antal_beslut: Counter[str] = Counter()
    skal_hoppade: Counter[str] = Counter()
    antal_osakra_linjer = 0
    entiteter: list[tuple[str, list[Xy], float | None]] = []
    ytor: list[MarkslagsYta] = []
    varningar: list[str] = []
    punkter_utan_roll = 0
    for o in objekt:
        beslut = (
            bestam_atgard(gram, o.lager, "yta" if o.typ == "yta" else "linje")
            if gram is not None and o.typ in ("yta", "linje")
            else None
        )
        if beslut is not None:  # lagret följer Stockholms grammatik
            if beslut.atgard == "byggnad_kontur" and o.lager in lager_med_yta:
                beslut = Beslut("hoppa_over", "husets kontur: ytan med samma namn används")
            beslutsrader.append((o.lager, beslut))
            antal_beslut[beslut.atgard] += 1
            if beslut.atgard in ("hoppa_over", "okand_objekt"):
                skal_hoppade[beslut.skal] += 1
                continue
            if beslut.atgard == "markslag":
                assert beslut.markslag is not None
                ytor.append(
                    MarkslagsYta(
                        markslag=beslut.markslag,
                        lager=o.lager,
                        ringar=o.ringar,
                        osaker=beslut.osaker,
                    )
                )
                continue
            assert beslut.roll is not None
            nyckel = o.lager.strip().upper()
            grammatikregler.setdefault(
                nyckel, LagerMappning(prefix=nyckel, roll=beslut.roll, standard_m=beslut.standard_m)
            )
            if o.typ == "yta":
                entiteter.extend((o.lager, list(r), o.z) for r in o.ringar)
            else:
                entiteter.append((o.lager, list(o.punkter), o.z))
                antal_osakra_linjer += int(beslut.osaker)
            continue
        if o.typ == "yta":
            ytregel = lagerprofil.matcha_yta(o.lager) if lagerprofil is not None else None
            if ytregel is not None:
                ytor.append(MarkslagsYta(markslag=ytregel.markslag, lager=o.lager, ringar=o.ringar))
                continue
            # En yta på ett linjelager (t.ex. ett husliv ritat som skraffyr) blir sina kanter.
            if _matcha_lager(o.lager, regler) is not None:
                entiteter.extend((o.lager, list(r), o.z) for r in o.ringar)
            else:
                entiteter.append((o.lager, [], o.z))  # ger "okänt lager" i kedjan
        elif o.typ == "linje":
            entiteter.append((o.lager, list(o.punkter), o.z))
        else:
            punkter_utan_roll += 1

    if grammatikregler:
        regler = (*grammatikregler.values(), *regler)
    if beslutsrader:
        varningar.extend(_grammatikvarningar(antal_beslut, skal_hoppade, antal_osakra_linjer))
        varningar.extend(sammanfatta_beslut(beslutsrader))
    if trasiga:
        varningar.append(f"{trasiga} objekt i dwgreads JSON gick inte att tolka och hoppades över.")
    if punkter_utan_roll:
        varningar.append(
            f"{punkter_utan_roll} punkter/blockinsättningar har ingen roll i Floden "
            "och användes inte."
        )
    baskarta = bygg_baskarta_fran_entiteter(
        entiteter,
        regler=regler,
        snap_tolerans_m=snap_tolerans_m,
        kalla_prefix="baskarta_dwg",
        varningar=varningar,
        z_kalla="DWG",
    )
    return DwgBaskartaResultat(
        baskarta=baskarta,
        markslagsytor=tuple(ytor),
        antal_objekt=len(objekt),
        antal_overhoppade=overhoppade,
        antal_trasiga_json=trasiga,
        lagerprofil_sha256=lagerprofil.sha256 if lagerprofil is not None else None,
    )
