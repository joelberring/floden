# ruff: noqa: E501
"""Projekt i en geografi: `floden projekt skapa --geografi goteborg-test@v1 --avgransning ...`.

Ett projekt är en mapp `data/lokal/projekt/<id>/` med
- `projekt.json` (kontrakt `floden-projekt/v1`): geografin som `id@version` och manifestets sha256 (projektet
  låser versionen), avgränsningen (EPSG:3011), varningar och skapare,
- `k1_vatten/`: en K1-ruta beskuren ur geografins rådata (vattenlösaren tar en ruta),
- `scenarier.json`: scenarier med `status` (`forslag` eller `planerat`; planerat läge förbereds i datamodellen,
  men räknas inte som bas än), K9-filer per bas i `k9/`,
- `fragor/*.toml`: K20-frågor (skyfall och dagsljus) för nuläget och varje scenario.

Dagsljus räknar direkt på geografins K1-rutnät (innehållsadresserat lager, delas mellan projekt och versioner)
med `fokus` på projektets avgränsning, så att grannrutor och buffert ger skuggor utifrån. Skyfall räknar på
`k1_vatten` och ser därför inte tillrinning utanför rutan; det står i projektets varningar.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shapely.geometry import box

from gemensamt.hash import sha256_fil
from geografi.modell import GeografiFel, lager_mapp, las_manifest
from kommuner.konfig import KommunKonfig
from kommuner.skapa import (
    SkapaFel,
    _oppna_register,
    _toml_str,
    _valj_organisation,
    regn_for_kommun,
    skriv_omrade_gpkg,
    skriv_oppenhet_toml,
)

KONTRAKT_PROJEKT = "floden-projekt/v1"
SCENARIO_STATUS = ("forslag", "planerat")
Skriv = Callable[[str], None]
Rektangel = tuple[float, float, float, float]


@dataclass
class ProjektResultat:
    projekt_id: str
    mapp: Path
    nytt: bool
    varningar: list[str] = field(default_factory=list)
    registrerat: bool = False


def slug(namn: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", namn.lower().translate(str.maketrans("åäö", "aao"))).strip("-")
    if not s:
        raise SkapaFel("Projektnamnet ger inget giltigt id (använd bokstäver eller siffror).")
    return s[:60]


def tolka_avgransning(text: str, epsg: int = 3011) -> Rektangel:
    """`xmin,ymin,xmax,ymax` eller en GeoJSON-/WKT-polygon som text; returnerar rektangeln i EPSG:3011."""
    from gemensamt.koordinater import transformera

    t = text.strip()
    pts: list[tuple[float, float]]
    if t.startswith("{"):
        try:
            g = json.loads(t)
            ring = (g.get("geometry") or g)["coordinates"][0]
            pts = [(float(p[0]), float(p[1])) for p in ring]
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise SkapaFel("--avgransning: GeoJSON-polygonen går inte att läsa.") from exc
    elif t.upper().startswith("POLYGON"):
        tal = re.findall(r"-?\d+(?:\.\d+)?", t)
        if len(tal) < 8 or len(tal) % 2:
            raise SkapaFel("--avgransning: WKT-polygonen går inte att läsa.")
        pts = [(float(tal[i]), float(tal[i + 1])) for i in range(0, len(tal), 2)]
    else:
        delar = [p for p in re.split(r"[,\s;]+", t) if p]
        if len(delar) != 4:
            raise SkapaFel(
                "--avgransning: ange 'xmin,ymin,xmax,ymax' (meter, EPSG:3011), eller en GeoJSON- eller WKT-polygon."
            )
        try:
            x0, y0, x1, y1 = (float(p) for p in delar)
        except ValueError as exc:
            raise SkapaFel("--avgransning: koordinaterna ska vara tal.") from exc
        pts = [(x0, y0), (x1, y1)]
    if epsg != 3011:
        pts = [transformera(x, y, epsg, 3011) for x, y in pts]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    r = (min(xs), min(ys), max(xs), max(ys))
    if not (r[2] > r[0] and r[3] > r[1]):
        raise SkapaFel("--avgransning: området har ingen yta.")
    return r


def _skriv_json(fil: Path, d: Mapping[str, Any]) -> None:
    fil.parent.mkdir(parents=True, exist_ok=True)
    fil.write_text(json.dumps(d, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def las_projekt(mapp: Path) -> dict[str, Any]:
    fil = mapp / "projekt.json"
    if not fil.is_file():
        raise SkapaFel(f"{mapp} är inget Floden-projekt (projekt.json saknas).")
    d = json.loads(fil.read_text(encoding="utf-8"))
    if d.get("kontrakt") != KONTRAKT_PROJEKT:
        raise SkapaFel(f"{fil}: fel kontrakt.")
    return dict(d)


def _rutor(k1: Path) -> list[tuple[str, Rektangel]]:
    from testsvit.k1_io import las_geotiff

    ut: list[tuple[str, Rektangel]] = []
    for mark in sorted(k1.glob("mm_*/mark.tif")):
        _, g = las_geotiff(mark)
        px = g.pixelstorlek_x
        ut.append((mark.parent.name, (g.x_min, g.y_max - g.hojd * px, g.x_min + g.bredd * px, g.y_max)))
    return ut


def _fokus(rutor: Sequence[tuple[str, Rektangel]], avgr: Rektangel) -> list[float]:
    """Fokus som tar de rutor vars mittpunkt ligger inom rutorna som skär avgränsningen."""
    valda = [r for _, r in rutor if box(*r).intersects(box(*avgr))]
    if not valda:
        raise SkapaFel("Avgränsningen ligger inte i någon K1-ruta i geografin.")
    mx = [(r[0] + r[2]) / 2 for r in valda]
    my = [(r[1] + r[3]) / 2 for r in valda]
    return [min(mx) - 1.0, min(my) - 1.0, max(mx) + 1.0, max(my) + 1.0]


def _rel(fran: Path, till: Path) -> str:
    return Path(os.path.relpath(till.resolve(), fran.resolve())).as_posix()


def _geo_k1(geo_rot: Path, manifest: Mapping[str, Any]) -> Path:
    return lager_mapp(geo_rot, str(manifest["lager"]["k1_rutnat"]["sha256"]))


def _geo_radata(geo_rot: Path, manifest: Mapping[str, Any]) -> Path:
    return lager_mapp(geo_rot, str(manifest["lager"]["radata"]["sha256"]))


def skapa_projekt(
    kommun: KommunKonfig,
    *,
    ref: str,
    avgransning: Rektangel,
    namn: str,
    konto_id: str | None,
    geo_rot: Path,
    projektrot: Path,
    konfig: Mapping[str, Any],
    konfig_fil: Path,
    organisation_id: str | None = None,
    skriv: Skriv = print,
    register: Any = None,
) -> ProjektResultat:
    from lantmateriet.bygg import bygg_omrade
    from lantmateriet.nycklar import inloggning_pa
    from lantmateriet.omrade import las_omrade

    try:
        manifest, manifest_sha = las_manifest(geo_rot, ref)
    except GeografiFel as exc:
        raise SkapaFel(str(exc)) from exc
    pid = slug(namn)
    mapp = projektrot / pid
    karna = tuple(manifest["karna_3011"])
    gransen = tuple(manifest["gransen_3011"])
    varningar: list[str] = []
    if not box(*gransen).covers(box(*avgransning)):
        raise SkapaFel(
            f"Avgränsningen ligger utanför geografin {ref}, som saknar data där. Geografins yttre gräns "
            f"(EPSG:3011): {', '.join(f'{v:.0f}' for v in gransen)}."
        )
    ab = box(*avgransning)
    if not box(*karna).covers(ab):
        andel = 100.0 * ab.difference(box(*karna)).area / ab.area
        varningar.append(
            f"{andel:.0f} % av avgränsningen ligger utanför geografins giltiga kärna ({', '.join(f'{v:.0f}' for v in karna)}): "
            "där räknas med data, men buffertens villkor (full tillrinning och skuggor utifrån) är inte garanterade."
        )

    # Behörighet och register.
    reg = register
    org = None
    if inloggning_pa(konfig):
        if not konto_id:
            raise SkapaFel("Inloggning är på: ange --konto <konto-id>; kontot blir utredare i projektet.")
        reg = reg if reg is not None else _oppna_register(konfig, konfig_fil.parent)
        org = _valj_organisation(reg, konto_id, organisation_id)

    if (mapp / "projekt.json").is_file():
        gammal = las_projekt(mapp)
        if gammal.get("geografi") != ref or gammal.get("geografi_manifest_sha256") != manifest_sha:
            raise SkapaFel(
                f"Projektet '{pid}' finns redan och är låst till {gammal.get('geografi')}. Välj ett annat namn."
            )
        skriv(f"Projektet '{pid}' finns redan ({ref}). Registret kontrolleras.")
        registrerat = _registrera(reg, org, konto_id, mapp, namn)
        return ProjektResultat(pid, mapp, False, list(gammal.get("varningar", [])), registrerat)

    # K1-ruta för vattenlösaren: beskuren ur geografins rådata (lagret delas, kopieras inte).
    kontext = kommun.lantmateriet.kontext_m
    v = (avgransning[0] - kontext, avgransning[1] - kontext, avgransning[2] + kontext, avgransning[3] + kontext)
    v = (max(v[0], gransen[0]), max(v[1], gransen[1]), min(v[2], gransen[2]), min(v[3], gransen[3]))
    radata = _geo_radata(geo_rot, manifest)
    k1_v = mapp / "k1_vatten"
    omr = las_omrade(
        skriv_omrade_gpkg(v, mapp / "omrade" / "vatten.gpkg"),
        max_km2=max(25.0, (v[2] - v[0]) * (v[3] - v[1]) / 1e6 + 1.0),
    )
    bygg_fil = radata / "kommun" / "byggnader.gpkg"
    skriv(f"Bygger skyfallsrutan ({v[2]-v[0]:.0f} x {v[3]-v[1]:.0f} m) ur geografins rådata ...")
    klippt = radata / "hojd" / "hojdmodell_klippt.tif"
    if klippt.is_file():  # geografin byggdes ur kommunens höjdmodell (filpaket)
        from kommuner.hojdmodell import Hojdmodell
        from kommuner.k1_filpaket import bygg_omrade_filpaket

        bygg_omrade_filpaket(
            omr, k1_v, hojdmodell=Hojdmodell([klippt], epsg_antagen=3011), hojd_paket=[],
            byggnader_fil=bygg_fil if bygg_fil.is_file() else None,
            upplosning_m=kommun.lantmateriet.upplosning_m, en_ruta=True, skriv=skriv,
        )  # fmt: skip
    else:
        bygg_omrade(
            omr, radata / "lantmateriet", k1_v,
            byggnader_fil=bygg_fil if bygg_fil.is_file() else None,
            en_ruta=True, upplosning_m=kommun.lantmateriet.upplosning_m,
            anvand_orto="orto" in kommun.lantmateriet.produkter, skriv=skriv,
        )  # fmt: skip
    varningar.append(
        "Skyfallsfrågan räknar på en ruta runt avgränsningen (statisk fyllning): tillrinning från högre mark "
        "utanför rutan ingår inte. Geografins tillrinningsyta finns i manifestet (forberakningar.tillrinning)."
    )
    # Byggnader ur en baskarta som inte är öppna data (kalla = lokal_fil) är alltid stängda, vad manifestet än säger.
    oppen = bool(manifest.get("oppen_for_mcp")) and not (manifest.get("kallor", {}).get("byggnader") or {}).get("stangd")
    oppen = oppen and (manifest.get("kallor", {}).get("byggnader") or {}).get("kalla") != "lokal_fil"
    skriv_oppenhet_toml(
        mapp,
        oppen=oppen,
        licens=str(_licens(mapp, geo_rot, manifest)),
        kalla=f"Projekt i geografin {ref} (härlett ur Lantmäteriet och {kommun.namn}).",
        anledning=(
            "Följer geografin: "
            + ("villkoren är kontrollerade." if oppen else "villkoren är inte kontrollerade, stängt för MCP.")
        ),
    )

    projekt = {
        "kontrakt": KONTRAKT_PROJEKT,
        "projekt_id": pid,
        "namn": namn,
        "kommun": kommun.id,
        "geografi": ref,
        "geografi_manifest_sha256": manifest_sha,
        "avgransning_3011": list(avgransning),
        "vatten_ruta_3011": list(v),
        "skapad": datetime.now(UTC).isoformat(timespec="seconds"),
        "skapad_av_konto": konto_id,
        "varningar": varningar,
    }
    _skriv_json(mapp / "projekt.json", projekt)
    _skriv_json(mapp / "scenarier.json", {"kontrakt": "floden-scenarier/v1", "scenarier": []})
    skriv_fragor(mapp, kommun, geo_rot, manifest)
    registrerat = _registrera(reg, org, konto_id, mapp, namn)
    for w in varningar:
        skriv(f"Varning: {w}")
    skriv(f"Projekt '{pid}' skapat i {ref}. Frågor: {mapp / 'fragor'}")
    return ProjektResultat(pid, mapp, True, varningar, registrerat)


def _licens(mapp: Path, geo_rot: Path, manifest: Mapping[str, Any]) -> str:
    from kommuner.skapa import _strangaste_licens

    k = manifest.get("kallor", {})
    return _strangaste_licens([str((k.get("hojd") or k["lantmateriet"])["licens"]), str(k["byggnader"]["licens"])])


def _registrera(reg: Any, org: str | None, konto_id: str | None, mapp: Path, namn: str) -> bool:
    """Registrerar projektet i kontoregistret med kontot som utredare; registrets id sparas i projekt.json
    (körningarna bär det, så att /api/korningar och MCP följer rollerna)."""
    if reg is None or org is None or konto_id is None:
        return False
    rid, _ = reg.registrera_projekt_for_konto(konto_id, org, namn)
    d = las_projekt(mapp)
    if d.get("register_id") != rid:
        d["register_id"] = rid
        _skriv_json(mapp / "projekt.json", d)
    return True


# ------------------------------------------------------------------------------------- frågor


def skriv_fragor(mapp: Path, kommun: KommunKonfig, geo_rot: Path, manifest: Mapping[str, Any]) -> list[Path]:
    """(Åter)skriver projektets K20-frågor: nuläget och ett scenario per post i scenarier.json."""
    projekt = las_projekt(mapp)
    avgr: Rektangel = tuple(projekt["avgransning_3011"])
    scen = json.loads((mapp / "scenarier.json").read_text(encoding="utf-8"))["scenarier"]
    fdir = mapp / "fragor"
    fdir.mkdir(parents=True, exist_ok=True)
    geo = _geo_k1(geo_rot, manifest)
    fokus = _fokus(_rutor(geo), avgr)
    regn_mm, regn_text, _ = regn_for_kommun(kommun)
    d = kommun.dagsljus
    pid = str(projekt["projekt_id"])
    fok = "[" + ", ".join(f"{v:.1f}" for v in fokus) + "]"
    k1v = mapp / "k1_vatten"

    def bas(sc: Path, gren: str) -> str:
        return f'bas = {_toml_str(_rel(fdir, sc))}\n'

    vatten = [
        f'# Skyfall i projektet {pid}: nuläget och scenarier. Regn: {regn_text}. Statisk fyllning, grov markmodell.',
        'kontrakt = "K20/v1"',
        f'fraga_id = {_toml_str(pid + "-skyfall")}',
        f'beskrivning = {_toml_str("Blåytor (maxdjup) i " + pid + ": " + regn_text)}',
        "",
        "[scenarier.bas]",
        bas(k1v, "bas").rstrip(),
    ]
    dag = [
        f"# Dagsljus i projektet {pid} (geografin {projekt['geografi']}): nuläget och scenarier.",
        'kontrakt = "K20/v1"',
        f'fraga_id = {_toml_str(pid + "-dagsljus")}',
        f'beskrivning = {_toml_str("Soltimmar och dagsljus på fasader i " + pid)}',
        "",
        "[scenarier.bas]",
        bas(geo, "bas").rstrip(),
    ]
    for s in scen:
        slugg = str(s["slug"])
        vatten += ["", f"[scenarier.{slugg}]", bas(k1v, slugg).rstrip(), f'k9 = [{_toml_str(_rel(fdir, mapp / s["k9_vatten"]))}]']
        dag += ["", f"[scenarier.{slugg}]", bas(geo, slugg).rstrip(), f'k9 = [{_toml_str(_rel(fdir, mapp / s["k9_dagsljus"]))}]']
    par_v = f'regn_mm = {regn_mm:.3f}'
    vatten += ["", "[[analyser]]", 'id = "skyfall"', 'losare = "vatten.statisk_fyllning"', f"parametrar = {{ {par_v} }}"]
    dagpar = (
        f'datum = {json.dumps(list(d["datum"]))}, steg_min = {int(d["steg_min"])}, azimutriktningar = {int(d["azimutriktningar"])}, '
        f'cellstorlek_m = {d["cellstorlek_m"]}, hojd_max_m = {d["hojd_max_m"]}, h_min_grader = {d["h_min_grader"]}'
    )
    vscpar = (
        f'cellstorlek_m = {d["cellstorlek_m"]}, hojd_max_m = {d["hojd_max_m"]}, h_min_grader = {d["h_min_grader"]}, '
        f'fasad_steg_m = {d["fasad_steg_m"]}, vaningshojd_m = {d["vaningshojd_m"]}'
    )
    dag += [
        "", "[[analyser]]", 'id = "sol"', 'losare = "dagsljus.soltimmar_mark"', f"fokus = {fok}", f"parametrar = {{ {dagpar} }}",
        "", "[[analyser]]", 'id = "vsc"', 'losare = "dagsljus.vsc_fasad"', f"fokus = {fok}", f"parametrar = {{ {vscpar} }}",
    ]  # fmt: skip
    for s in scen:
        slugg = str(s["slug"])
        vatten += ["", "[[jamforelser]]", 'analys = "skyfall"', 'utdata = "max_djup"', 'fore = "bas"', f'efter = "{slugg}"', "troskel = 0.01"]
        dag += ["", "[[jamforelser]]", 'analys = "sol"', 'utdata = "soltimmar"', 'fore = "bas"', f'efter = "{slugg}"', "troskel = 0.1"]
    fv, fd = fdir / "skyfall.toml", fdir / "dagsljus.toml"
    fv.write_text("\n".join(vatten) + "\n", encoding="utf-8")
    fd.write_text("\n".join(dag) + "\n", encoding="utf-8")
    return [fv, fd]


# ------------------------------------------------------------------------------------ scenario


def skapa_scenario_hus(
    mapp: Path,
    *,
    kommun: KommunKonfig,
    geo_rot: Path,
    namn: str,
    centrum: tuple[float, float],
    bredd_m: float,
    djup_m: float,
    hojd_m: float,
    status: str = "forslag",
    skriv: Skriv = print,
) -> dict[str, Any]:
    """Ett ritat hus (byggnad_ny) som K9-scenario mot geografin i projektet, och diffen mot nuläget."""
    from karna.ritat import RitatFel, kontrollera_andringar, normalisera_ring
    from karna.scenario import Andring, Planscenario, diff_scenarier, skriv_scenario

    if status not in SCENARIO_STATUS:
        raise SkapaFel(f"Okänd scenariostatus '{status}'. Tillåtna: {', '.join(SCENARIO_STATUS)}.")
    projekt = las_projekt(mapp)
    manifest, _ = las_manifest(geo_rot, str(projekt["geografi"]))
    slugg = slug(namn)
    cx, cy = centrum
    ring = normalisera_ring(
        [(cx - bredd_m / 2, cy - djup_m / 2), (cx + bredd_m / 2, cy - djup_m / 2), (cx + bredd_m / 2, cy + djup_m / 2), (cx - bredd_m / 2, cy + djup_m / 2)]
    )
    ring.append(list(ring[0]))
    andring = {
        "typ": "byggnad_ny",
        "andring_id": f"hus-{slugg}",
        "geometri": {"type": "Polygon", "coordinates": [ring]},
        "egenskaper": {"hojd_m": float(hojd_m), "namn": namn},
    }
    rutor_d = [r for _, r in _rutor(_geo_k1(geo_rot, manifest))]
    rutor_v = [r for _, r in _rutor(mapp / "k1_vatten")]
    try:
        kontroll = kontrollera_andringar([andring], rutor_d)
        kontrollera_andringar([andring], rutor_v)
    except RitatFel as exc:
        raise SkapaFel(str(exc)) from exc

    k9 = mapp / "k9"
    ut: dict[str, Any] = {}
    skapade: dict[str, tuple[Planscenario, Planscenario]] = {}
    for kort, k1 in (("dagsljus", _geo_k1(geo_rot, manifest)), ("vatten", mapp / "k1_vatten")):
        recept = tuple(sha256_fil(r / "recept.json") for r in sorted(k1.glob("mm_*")) if (r / "recept.json").is_file())
        if not recept:
            raise SkapaFel(f"Basen {k1} saknar recept.json.")
        nulage = Planscenario("nulage", recept, ())
        sc = Planscenario(slugg, recept, (Andring.fran_dict(andring),), beskrivning=f"{namn} ({status})")
        skriv_scenario(sc, k9 / f"{slugg}_{kort}.json")
        skapade[kort] = (nulage, sc)
    diff = diff_scenarier(*skapade["dagsljus"])
    post = {
        "slug": slugg, "namn": namn, "status": status,
        "k9_dagsljus": f"k9/{slugg}_dagsljus.json", "k9_vatten": f"k9/{slugg}_vatten.json",
        "diff": {"tillagda": [a.andring_id for a in diff.tillagda], "borttagna": [a.andring_id for a in diff.borttagna], "andrade": [a[0].andring_id for a in diff.andrade]},
        "hus": kontroll["hus"],
        "skapad": datetime.now(UTC).isoformat(timespec="seconds"),
    }  # fmt: skip
    fil = mapp / "scenarier.json"
    d = json.loads(fil.read_text(encoding="utf-8"))
    d["scenarier"] = [s for s in d["scenarier"] if s["slug"] != slugg] + [post]
    _skriv_json(fil, d)
    skriv_fragor(mapp, kommun, geo_rot, manifest)
    ut.update(post)
    skriv(
        f"Scenario '{slugg}' ({status}): diff mot nuläget = {len(diff.tillagda)} tillagda, {len(diff.borttagna)} borttagna, "
        f"{len(diff.andrade)} ändrade."
    )
    return ut


def lista_scenarier(mapp: Path) -> list[dict[str, Any]]:
    return list(json.loads((mapp / "scenarier.json").read_text(encoding="utf-8"))["scenarier"])


# --------------------------------------------------------------------------------------- köra


def kor_projekt(
    mapp: Path, *, rot: Path, scenario: str | None = None, skriv: Skriv = print, tjanst: Any = None
) -> dict[str, dict[str, Any]]:
    """Kör skyfall och dagsljus för nuläget och scenarierna (frågefilerna räknar allt i en körning)."""
    from webb.api import STANDARD_CACHEMAPP, STANDARD_KORNINGSMAPP
    from webb.fragor import FragaTjanst

    projekt = las_projekt(mapp)
    pid = str(projekt["projekt_id"])
    t = tjanst or FragaTjanst(
        rot=rot, fragemappar=[mapp / "fragor"], cachemapp=rot / STANDARD_CACHEMAPP,
        korningsmapp=rot / STANDARD_KORNINGSMAPP, projektrot=mapp.parent,
    )  # fmt: skip
    resultat: dict[str, dict[str, Any]] = {}
    for fraga in ("skyfall", "dagsljus"):
        ref = next((r for r in t._fragefiler() if r == fraga or r.startswith(fraga)), fraga)  # noqa: SLF001
        # Frågornas ref är filnamnet; flera projekt kan ha samma filnamn: -2, -3 läggs till av tjänsten.
        for r, f in t._fragefiler().items():  # noqa: SLF001
            if f.resolve() == (mapp / "fragor" / f"{fraga}.toml").resolve():
                ref = r
        skriv(f"Kör {fraga} ({ref}) ...")
        status, _ = t.kor_synkront(ref, f"{pid}-{fraga}", projekt.get("register_id"))
        resultat[fraga] = status
        skriv(f"  {fraga}: {status.get('status')}")
    return resultat
