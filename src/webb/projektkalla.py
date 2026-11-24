# ruff: noqa: E501
"""Projektkällan för kartverktyget: var ett projekts markmodell, byggnader och analyser kommer ifrån.

Kartverktyget (steg 2 och 3 i webben, `/api/kartverktyg/<projekt>/...` och MCP-verktygen som rita, planera och köra)
arbetar mot **ett projekt** som är en uppsättning *mallar*: en per analysfamilj (`vatten`, `dagsljus`, `ekologi`),
var och en en K20-fråga med en bas (K1-rutor) och analyser som ritade förslag byggs av. Hur mallarna hittas beror på
projektets källa:

- **Demokvarteret** (`slag = "demo"`): mallarna är demofrågorna som `floden demo` byggt (`FragaTjanst.demo()`). Det är
  det som alltid funnits och byggs i `kartverktyg.py`; ingenting här ändrar det.
- **Projekt i en geografi** (`slag = "geografi"`): ett projekt som skapats ur en mappad geografi (K46) med
  `floden projekt skapa --geografi`, `POST /api/projekt` eller MCP `projekt_skapa`. Mallarna är projektets egna
  frågor (`<projekt>/fragor/skyfall.toml` och `dagsljus.toml`, skrivna av `kommuner.projekt.skriv_fragor`):
  skyfall räknar på projektets `k1_vatten` (en ruta runt avgränsningen) och dagsljus på geografins K1-rutnät. Rutnätet
  är hela geografin, så **rutorna klipps till de som skär projektets avgränsning** (polygonen om projektet har en,
  annars rektangeln); bara de bär byggnader, terräng och det som får ritas. Beräkningsfrågorna pekar fortfarande på hela
  basen med `fokus` på projektet, som `floden projekt kor`.

Projektets id i adressen är registrets id (`register_id` i `projekt.json`, samma id som rollerna och körningarna bär).
Ett projekt som skapats lokalt utan inloggning har inget registerid och nås med sitt projektid (mappnamnet) så länge
inloggning är av; med inloggning på nås bara registrerade projekt, så att rollkontrollen alltid har något att pröva mot.

Dataklassen följer projektets `oppenhet.toml` (K38), som `skapa_projekt` skrivit ur geografins villkor: stängt (`oppen =
false`, till exempel Stockholms baskarta med licens `intern`) eller öppet. Webben lämnar ut projektet till konton med
roll oavsett; MCP lämnar inte ut något ur ett stängt projekt (`floden_mcp.server`).
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from gemensamt import avgransning as avgr_mod
from geografi.modell import GeografiFel, lager_mapp, las_manifest
from karna.rutscenario import Ruta, ScenarioFel, las_bas, las_bas_klippt
from karna.utredning import FragaFel, KorbarFraga, las_fraga
from webb.fel import FlodenWebbFel

if TYPE_CHECKING:
    from webb.fragor import FragaTjanst

PROJEKT_ID_DEMO = "demokvarteret"
KONTRAKT_PROJEKT = "floden-projekt/v1"
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_FRAGEFILER = (("skyfall.toml", "vatten"), ("dagsljus.toml", "dagsljus"))
"""Projektets egna frågefiler och mallens korta namn (samma som demons: `vatten`, `dagsljus`)."""
Rektangel = tuple[float, float, float, float]


@dataclass(frozen=True)
class Mall:
    """En fråga som mall för ritade frågor: en bas, ett scenario utan ändring, analyser."""

    ref: str
    kort: str
    """Kort namn i frågornas id: `dagsljus`, `vatten`."""
    fraga: KorbarFraga
    bas: Path
    rutor: tuple[Ruta, ...]
    """Rutorna ritade förslag och byggnader gäller. För ett projekt i en geografi: de som skär avgränsningen."""
    fore_id: str


@dataclass(frozen=True)
class Projektkalla:
    """Ett projekt som kartverktyget kan rita och räkna i."""

    id: str
    slag: str
    """`demo` eller `geografi`."""
    namn: str
    paahittat: bool
    oppen: bool
    """Projektets `oppenhet.toml` (K38): öppet bara med `oppen = true` och en känd öppen licens."""
    licens: str | None
    mallar: Mapping[str, Mall]
    fragedir: Path | None = None
    """Mappen ritade frågor skrivs i; `None`: kartverktygets egen (demokvarteret)."""
    fraga_infix: str = ""
    """Läggs in i ritade frågors id så att projekt med samma scenarionamn inte krockar (tomt för demon)."""
    mapp: Path | None = None
    geografi: str | None = None
    avgransning: Rektangel | None = None
    polygon: Polygon | None = None
    varningar: tuple[str, ...] = ()
    stampel: tuple[int, ...] = ()
    """Ändras när projektets filer ändras; cachen av byggnader och mallar går på den."""

    def projektform(self) -> BaseGeometry | None:
        """Det som räknas till projektet: polygonen, annars rektangeln; `None` för demon (hela kvarteret)."""
        if self.polygon is not None:
            return self.polygon
        if self.avgransning is not None:
            return box(*self.avgransning)
        return None


def _lasa_json(fil: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(fil.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def hitta_projektmapp(projektrot: Path | None, projekt_id: str, *, tillat_projektid: bool) -> Path | None:
    """Projektmappen för ett id ur adressen: registrets id (`register_id`), och med `tillat_projektid` (inloggning av)
    även projektid:t för ett projekt som saknar registerid. Okänt eller ogiltigt id ger `None`."""
    if projektrot is None or not projektrot.is_dir() or not _ID_RE.match(projekt_id):
        return None
    utan_register: Path | None = None
    for fil in sorted(projektrot.glob("*/projekt.json")):
        d = _lasa_json(fil)
        if d is None or d.get("kontrakt") != KONTRAKT_PROJEKT:
            continue
        rid = d.get("register_id")
        if isinstance(rid, str) and rid == projekt_id:
            return fil.parent
        if tillat_projektid and not rid and d.get("projekt_id") == projekt_id:
            utan_register = fil.parent
    return utan_register


def projekt_oppenhet(mapp: Path) -> tuple[bool, str | None]:
    """(öppen?, licens) ur projektmappens `oppenhet.toml`. Saknad eller otolkbar fil, `oppen != true` och en licens
    som inte är en känd öppen licens ger alla stängt (samma regel som `floden_mcp.oppenhet`)."""
    from floden_mcp.oppenhet import KANDA_OPPNA_LICENSER

    fil = mapp / "oppenhet.toml"
    try:
        with fil.open("rb") as fh:
            d = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return False, None
    licens = str(d.get("licens", "")).strip() or None
    return (d.get("oppen") is True and licens in KANDA_OPPNA_LICENSER), licens


def stampel_for(mapp: Path) -> tuple[int, ...]:
    ut: list[int] = []
    for rel in ("projekt.json", "oppenhet.toml", *(f"fragor/{n}" for n, _ in _FRAGEFILER)):
        try:
            ut.append((mapp / rel).stat().st_mtime_ns)
        except OSError:
            ut.append(0)
    return tuple(ut)


def _fel(text: str, status: int) -> FlodenWebbFel:
    return FlodenWebbFel(text, status_kod=status)


def bygg_geografikalla(
    projekt_id: str, mapp: Path, *, geomapp: Path | None, fragor: FragaTjanst
) -> Projektkalla:
    """Läser ett projekt i en geografi och bygger dess mallar.

    Fel: 409 om geografin saknas eller har ändrats sedan projektet skapades (projektet är låst till manifestets
    sha256), eller om projektets frågefiler inte går att läsa; 404 om projektet saknar frågor att rita mot."""
    projekt = _lasa_json(mapp / "projekt.json")
    if projekt is None:
        raise _fel(f"Projektet '{projekt_id}' går inte att läsa.", 409)
    ref = projekt.get("geografi")
    if not isinstance(ref, str) or geomapp is None:
        raise _fel(f"Projektet '{projekt_id}' saknar geografi att räkna mot.", 409)
    try:
        # Lagrens innehåll kontrollerades när projektet skapades; här räcker att manifestet är det projektet låste.
        manifest, sha = las_manifest(geomapp, ref, verifiera=False)
    except GeografiFel as exc:
        raise _fel(f"Geografin {ref} går inte att använda: {exc}", 409) from exc
    if projekt.get("geografi_manifest_sha256") not in (None, sha):
        raise _fel(
            f"Geografin {ref} har ändrats sedan projektet skapades (manifestet stämmer inte med det projektet är låst "
            "till). Skapa projektet på nytt.",
            409,
        )
    try:
        avgr = tuple(float(v) for v in projekt["avgransning_3011"])
        if len(avgr) != 4:
            raise ValueError("avgransning_3011")
    except (KeyError, TypeError, ValueError) as exc:
        raise _fel(f"Projektet '{projekt_id}' saknar en giltig avgränsning.", 409) from exc
    rektangel: Rektangel = (avgr[0], avgr[1], avgr[2], avgr[3])
    polygon = avgr_mod.fran_projekt(projekt)
    form: BaseGeometry = polygon if polygon is not None else box(*rektangel)
    try:
        k1 = lager_mapp(geomapp, str(manifest["lager"]["k1_rutnat"]["sha256"]))
    except (GeografiFel, KeyError, TypeError) as exc:
        raise _fel(f"Geografin {ref} saknar sitt K1-rutnät: {exc}", 409) from exc

    ref_for = {f.resolve(): r for r, f in fragor._fragefiler().items()}  # noqa: SLF001 - samma paket
    mallar: dict[str, Mall] = {}
    problem: list[str] = []
    for namn, kort in _FRAGEFILER:
        fil = mapp / "fragor" / namn
        if not fil.is_file():
            continue
        try:
            fraga = las_fraga(fil)
            fore = next((s for s in fraga.scenarier.values() if not s.k9 and not s.lappar), None)
            if fore is None:
                raise FragaFel(f"{namn} har inget scenario utan ändring (nuläget).")
            rutor = las_bas_klippt(fore.bas, form) if fore.bas.resolve() == k1.resolve() else las_bas(fore.bas)
        except (FragaFel, ScenarioFel) as exc:
            problem.append(f"{namn}: {exc}")
            continue
        mallar[kort] = Mall(ref_for.get(fil.resolve(), fil.stem), kort, fraga, fore.bas, rutor, fore.id)
    if not mallar:
        detalj = f" ({'; '.join(problem)})" if problem else ""
        raise _fel(
            f"Projektet '{projekt_id}' har inga frågor att rita mot: kör `floden projekt skapa` om det{detalj}.", 404
        )
    oppen, licens = projekt_oppenhet(mapp)
    return Projektkalla(
        id=projekt_id,
        slag="geografi",
        namn=str(projekt.get("namn") or projekt_id),
        paahittat=False,
        oppen=oppen,
        licens=licens,
        mallar=mallar,
        fragedir=mapp / "fragor",
        fraga_infix=re.sub(r"[^a-z0-9_\-]+", "-", mapp.name.lower()).strip("-")[:30] or "projekt",
        mapp=mapp,
        geografi=ref,
        avgransning=rektangel,
        polygon=polygon,
        varningar=tuple(str(v) for v in projekt.get("varningar", []) if isinstance(v, str)) + tuple(problem),
        stampel=stampel_for(mapp),
    )
