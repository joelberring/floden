# ruff: noqa: E501
"""Projekt i en geografi: `floden projekt skapa --geografi goteborg-test@v1 --avgransning ...`.

Ett projekt är en mapp `data/lokal/projekt/<id>/` med
- `projekt.json` (kontrakt `floden-projekt/v1`): geografin som `id@version` och manifestets sha256 (projektet
  låser versionen), avgränsningen (EPSG:3011: `avgransning_3011` är rektangeln som rutnätet och bufferten
  utgår från, och `avgransning_polygon_3011` (valfritt, additivt) är den riktiga polygonen som avgör vad som
  räknas till projektet i resultat, nyckeltal och rapporter), varningar och skapare,
- `scenarier.json`: scenarier med `status` (`forslag` eller `planerat`; planerat läge förbereds i datamodellen,
  men räknas inte som bas än), K9-filer per bas i `k9/`,
- `fragor/*.toml`: K20-frågor (skyfall, dagsljus och – när geografin har NMD-lager – ekologi) för nuläget och
  varje scenario.

Samtliga analyser räknar direkt på geografins flerruts-K1-rutnät (innehållsadresserat lager som delas mellan
projekt och versioner, dimensionerat efter geografins förberäknade tillrinningsområde `forberakningar.tillrinning`
och dagsljusets påverkansradie). Dagsljus använder `fokus` på projektets avgränsning medan skyfall och ekologi
räknar globalt över hela rutnätet så att tillrinning och habitatkontext utanför projektgränsen ingår.
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

from shapely.geometry import Polygon, box

from gemensamt import avgransning as avgr_mod
from gemensamt.hash import sha256_fil
from geografi.modell import (
    MANIFEST_NAMN,
    GeografiFel,
    lager_mapp,
    las_manifest,
    tolka_ref,
    versionsmapp,
)
from kommuner.konfig import _REPO_ROT, KommunKonfig, LuftKonfig
from kommuner.skapa import (
    SkapaFel,
    _oppna_register,
    _toml_str,
    _valj_organisation,
    regn_for_kommun,
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
    """`xmin,ymin,xmax,ymax` eller en GeoJSON-/WKT-polygon som text; returnerar den omslutande rektangeln i EPSG:3011.
    Polygonen själv ger `tolka_avgransning_form`. En polygon som korsar sig själv eller har hål ger `SkapaFel`."""
    from gemensamt.koordinater import transformera

    t = text.strip()
    pts: list[tuple[float, float]]
    if t.startswith("{") or t.upper().startswith("POLYGON"):
        try:
            poly = avgr_mod.tolka_polygon(t, epsg)
        except avgr_mod.AvgransningFel as exc:
            raise SkapaFel(f"--avgransning: {exc}") from exc
        assert poly is not None
        r = avgr_mod.omslutande(poly)
        if not (r[2] > r[0] and r[3] > r[1]):
            raise SkapaFel("--avgransning: området har ingen yta.")
        return r
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


def tolka_avgransning_objekt_form(avgransning: Any, epsg: int = 3011) -> tuple[Rektangel, Polygon | None]:
    """Avgränsningen som ett API-anrop ger den: en GeoJSON-polygon (objekt, `Feature` eller geometri), en
    lista `[xmin, ymin, xmax, ymax]` eller samma text som `--avgransning`. Returnerar rektangeln (beräkningsrutnätet
    och bufferten) och polygonen (vad som räknas till projektet), båda i EPSG:3011. Polygonen är `None` när indata är
    en rektangel eller en polygon som är sin egen rektangel; då gäller rektangeln och allt beter sig som förut.
    Ogiltiga tal (NaN, oändligt), en yta utan utsträckning, hål och självkorsning ger `SkapaFel` med svensk text."""
    import math

    if isinstance(avgransning, str):
        text = avgransning
    elif isinstance(avgransning, Mapping):
        text = json.dumps(avgransning)
    elif isinstance(avgransning, Sequence) and len(avgransning) == 4:
        try:
            text = ",".join(repr(float(v)) for v in avgransning)
        except (TypeError, ValueError) as exc:
            raise SkapaFel("avgransning: koordinaterna ska vara tal.") from exc
    else:
        raise SkapaFel(
            "avgransning: ange en GeoJSON-polygon eller en lista [xmin, ymin, xmax, ymax] (meter, EPSG:3011)."
        )
    try:
        poly = avgr_mod.tolka_polygon(text, epsg)
        r = tolka_avgransning(text, epsg) if poly is None else avgr_mod.omslutande(poly)
    except avgr_mod.AvgransningFel as exc:
        raise SkapaFel(f"avgransning: {exc}") from exc
    except (OverflowError, ValueError) as exc:
        raise SkapaFel("avgransning: koordinaterna ska vara ändliga tal.") from exc
    if not all(math.isfinite(v) for v in r):
        raise SkapaFel("avgransning: koordinaterna ska vara ändliga tal.")
    if poly is not None and avgr_mod.ar_rektangel(poly):
        poly = None
    return r, poly


def tolka_avgransning_objekt(avgransning: Any, epsg: int = 3011) -> Rektangel:
    """Som `tolka_avgransning_objekt_form` men bara den omslutande rektangeln (äldre anrop)."""
    return tolka_avgransning_objekt_form(avgransning, epsg)[0]


def projekt_polygon(projekt: Mapping[str, Any]) -> Polygon | None:
    """Projektets avgränsningspolygon ur `projekt.json`, eller `None` (då gäller `avgransning_3011`)."""
    return avgr_mod.fran_projekt(projekt)


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


def _fokus(rutor: Sequence[tuple[str, Rektangel]], avgr: Rektangel, polygon: Polygon | None = None) -> list[float]:
    """Fokus som tar de rutor vars mittpunkt ligger inom rutorna som skär avgränsningen (polygonen om projektet har
    en, annars rektangeln)."""
    form = polygon if polygon is not None else box(*avgr)
    valda = [r for _, r in rutor if box(*r).intersects(form)]
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
    polygon: Polygon | None = None,
) -> ProjektResultat:
    from lantmateriet.nycklar import inloggning_pa

    try:
        manifest, manifest_sha = las_manifest(geo_rot, ref)
    except GeografiFel as exc:
        raise SkapaFel(str(exc)) from exc
    pid = slug(namn)
    mapp = projektrot / pid
    karna = tuple(manifest["karna_3011"])
    gransen = tuple(manifest["gransen_3011"])
    varningar: list[str] = []
    if polygon is not None:
        # Avgränsningen är polygonen; rektangeln är dess omslutande (beräkningsrutnätet och bufferten).
        try:
            avgr_mod.kontrollera_yta(polygon)
        except avgr_mod.AvgransningFel as exc:
            raise SkapaFel(str(exc)) from exc
        if any(abs(a - b) > 1e-6 for a, b in zip(avgr_mod.omslutande(polygon), avgransning, strict=True)):
            raise SkapaFel("Avgränsningens rektangel är inte polygonens omslutande rektangel.")
    form = polygon if polygon is not None else box(*avgransning)
    if not box(*gransen).covers(form):
        raise SkapaFel(
            f"Avgränsningen ligger utanför geografin {ref}, som saknar data där. Geografins yttre gräns "
            f"(EPSG:3011): {', '.join(f'{v:.0f}' for v in gransen)}."
        )
    if not box(*karna).covers(form):
        andel = 100.0 * form.difference(box(*karna)).area / form.area
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

    # Skyfall, dagsljus och (när NMD-lager finns) ekologi använder geografins flerruts-K1-rutnät
    # (innehållsadresserat och dimensionerat efter förberäknad tillrinning i manifestet).
    geo_k1 = _geo_k1(geo_rot, manifest)
    if not _rutor(geo_k1):
        raise SkapaFel(f"Geografin {ref} saknar K1-rutor i lagret k1_rutnat.")
    raw_buffert = manifest.get("buffert")
    buffert: Mapping[str, Any] = raw_buffert if isinstance(raw_buffert, Mapping) else {}
    raw_forb = manifest.get("forberakningar")
    forberakningar: Mapping[str, Any] = raw_forb if isinstance(raw_forb, Mapping) else {}
    if bool(buffert.get("tillrinning_nar_kanten")):
        varningar.append(
            "Geografins förberäknade tillrinningsområde når kanten av det mappade området "
            "(forberakningar.tillrinning): skyfallsresultat nära uppströmsgränsen kan sakna tillrinning."
        )
    elif not isinstance(forberakningar.get("tillrinning"), Mapping):
        varningar.append(
            "Geografin saknar förberäknad tillrinning i manifestet (forberakningar.tillrinning): "
            "kontrollera att bufferten täcker uppströms avrinningsområde."
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
        **({"avgransning_polygon_3011": avgr_mod.till_geojson(polygon)} if polygon is not None else {}),
        "vatten_ruta_3011": list(gransen),
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


def _skyfall_parametrar(kommun: KommunKonfig) -> tuple[str, str]:
    """Returnerar `(toml_parametrar, lasbar_regn_text)` för projektets `skyfall.toml`.

    Använder kommunens registrerade IDF-källa (t.ex. `stockholmsregn-2024` för Stockholm) när
    regnförvalet hör till en registrerad IDF-tabell, och annars total regnmängd + varaktighet ur
    `regn_for_kommun` (t.ex. Dahlström 2010 / P110 för övriga kommuner). `takvatten` följer
    kommunfilens `[skyfall]`.
    """
    from gemensamt.regn import (
        IDF_KALLOR,
        IDF_STOCKHOLMSREGN_2024,
        RegnFel,
        hamta_regnforval,
        skapa_regn_fran_idf_kalla,
    )

    takvatten = bool(kommun.skyfall.get("takvatten", True))
    tak_toml = "true" if takvatten else "false"
    try:
        forval = hamta_regnforval(str(kommun.skyfall["regn_id"]))
    except RegnFel as exc:
        raise SkapaFel(f"[skyfall] regn_id i kommunfilen: {exc}") from exc
    if forval.region == "Stockholm" and IDF_STOCKHOLMSREGN_2024 in IDF_KALLOR:
        try:
            serie = skapa_regn_fran_idf_kalla(
                IDF_STOCKHOLMSREGN_2024,
                forval.aterkomsttid_ar,
                klimatfaktor=forval.klimatfaktor,
                typ=forval.typ,
                varaktighet_min=forval.varaktighet_min,
            )
        except RegnFel as exc:
            raise SkapaFel(f"[skyfall] IDF-källa {IDF_STOCKHOLMSREGN_2024}: {exc}") from exc
        kalla = IDF_KALLOR[IDF_STOCKHOLMSREGN_2024]
        par = (
            f"idf = {_toml_str(IDF_STOCKHOLMSREGN_2024)}, "
            f"aterkomsttid_ar = {forval.aterkomsttid_ar:g}, "
            f"varaktighet_min = {forval.varaktighet_min:g}, "
            f"klimatfaktor = {forval.klimatfaktor:g}, "
            f"regntyp = {_toml_str(forval.typ)}, "
            f"takvatten = {tak_toml}"
        )
        text = (
            f"{kalla.namn}, {forval.aterkomsttid_ar:g} år, {forval.varaktighet_min:g} min, "
            f"klimatfaktor {forval.klimatfaktor:g} = {serie.total_djup_mm:.1f} mm"
        )
        return par, text
    regn_mm, regn_text, var_min = regn_for_kommun(kommun)
    par = f"regn_mm = {regn_mm:.3f}, varaktighet_min = {var_min:g}, takvatten = {tak_toml}"
    return par, regn_text


def _har_ekologi_underlag(manifest: Mapping[str, Any]) -> bool:
    """Sant när geografin har NMD-lager (`lager.nmd`) för serverns ekologianalys."""
    lager = manifest.get("lager")
    return isinstance(lager, Mapping) and isinstance(lager.get("nmd"), Mapping)


def _ekologi_natverk(kommun: KommunKonfig) -> str:
    """Förvalt serverkörbart habitatnätverk (NMD) för kommunen."""
    if kommun.ekologi is not None and kommun.ekologi.forval_server:
        return str(kommun.ekologi.forval_server[0])
    return "barrskog-nacka"


def _los_luftfil(sokvag_str: str, kommun: KommunKonfig, geo_rot: Path) -> Path:
    p = Path(sokvag_str)
    if p.is_absolute():
        return p
    kandidater: list[Path] = []
    if kommun.fil != Path("."):
        kandidater.append(kommun.fil.parent / p)
    kandidater.extend([geo_rot / p, _REPO_ROT / p])
    for kand in kandidater:
        if kand.is_file():
            return kand
    return kandidater[0]


def _luft_underlag(
    kommun: KommunKonfig, geo_rot: Path, manifest: Mapping[str, Any]
) -> tuple[LuftKonfig, Path, Path | None] | None:
    """Returnerar `(luft_konfig, vader_path, vagar_path)` när kommun eller geografi har luftunderlag."""
    luft = kommun.luft
    if luft is None:
        raw = manifest.get("luft")
        if isinstance(raw, Mapping) and isinstance(raw.get("vader_fil"), str) and raw["vader_fil"].strip():
            vagar_raw = raw.get("vagar_fil")
            bg_raw = raw.get("bakgrund_ug_m3")
            bg = (
                float(bg_raw)
                if isinstance(bg_raw, int | float) and not isinstance(bg_raw, bool)
                else None
            )
            luft = LuftKonfig(
                vader_fil=str(raw["vader_fil"]).strip(),
                vagar_fil=str(vagar_raw).strip() if isinstance(vagar_raw, str) else "",
                bakgrund_ug_m3=bg,
                bakgrund_kalla=str(raw.get("bakgrund_kalla", "")).strip(),
            )
    if luft is None:
        return None
    vader = _los_luftfil(luft.vader_fil, kommun, geo_rot)
    vagar = _los_luftfil(luft.vagar_fil, kommun, geo_rot) if luft.vagar_fil else None
    return luft, vader, vagar


def skriv_fragor(mapp: Path, kommun: KommunKonfig, geo_rot: Path, manifest: Mapping[str, Any]) -> list[Path]:
    """(Åter)skriver projektets K20-frågor: nuläget och ett scenario per post i scenarier.json."""
    projekt = las_projekt(mapp)
    avgr: Rektangel = tuple(projekt["avgransning_3011"])
    scen = json.loads((mapp / "scenarier.json").read_text(encoding="utf-8"))["scenarier"]
    fdir = mapp / "fragor"
    fdir.mkdir(parents=True, exist_ok=True)
    geo = _geo_k1(geo_rot, manifest)
    fokus = _fokus(_rutor(geo), avgr, projekt_polygon(projekt))
    par_v, regn_text = _skyfall_parametrar(kommun)
    d = kommun.dagsljus
    pid = str(projekt["projekt_id"])
    fok = "[" + ", ".join(f"{v:.1f}" for v in fokus) + "]"

    def bas(sc: Path, gren: str) -> str:
        return f'bas = {_toml_str(_rel(fdir, sc))}\n'

    vatten = [
        f"# Skyfall i projektet {pid} (geografin {projekt['geografi']}): nuläget och scenarier. Regn: {regn_text}.",
        'kontrakt = "K20/v1"',
        f'fraga_id = {_toml_str(pid + "-skyfall")}',
        f'beskrivning = {_toml_str("Blåytor (maxdjup) i " + pid + ": " + regn_text)}',
        "",
        "[scenarier.bas]",
        bas(geo, "bas").rstrip(),
    ]
    dag = [
        f"# Dagsljus i projektet {pid} (geografin {projekt['geografi']}): nuläget och scenarier.",
        'kontrakt = "K20/v1"',
        f'fraga_id = {_toml_str(pid + "-dagsljus")}',
        f'beskrivning = {_toml_str("Soltimmar, himmelsexponering (SVF) och dagsljus på fasader i " + pid)}',
        "",
        "[scenarier.bas]",
        bas(geo, "bas").rstrip(),
    ]
    for s in scen:
        slugg = str(s["slug"])
        k9_v = str(s.get("k9_vatten") or s["k9_dagsljus"])
        vatten += ["", f"[scenarier.{slugg}]", bas(geo, slugg).rstrip(), f'k9 = [{_toml_str(_rel(fdir, mapp / k9_v))}]']
        dag += ["", f"[scenarier.{slugg}]", bas(geo, slugg).rstrip(), f'k9 = [{_toml_str(_rel(fdir, mapp / s["k9_dagsljus"]))}]']
    vatten += ["", "[[analyser]]", 'id = "skyfall"', 'losare = "vatten.statisk_fyllning"', f"parametrar = {{ {par_v} }}"]
    dagpar = (
        f'datum = {json.dumps(list(d["datum"]))}, steg_min = {int(d["steg_min"])}, azimutriktningar = {int(d["azimutriktningar"])}, '
        f'cellstorlek_m = {d["cellstorlek_m"]}, hojd_max_m = {d["hojd_max_m"]}, h_min_grader = {d["h_min_grader"]}'
    )
    svfpar = (
        f'azimutriktningar = {int(d["azimutriktningar"])}, '
        f'cellstorlek_m = {d["cellstorlek_m"]}, hojd_max_m = {d["hojd_max_m"]}, h_min_grader = {d["h_min_grader"]}'
    )
    vscpar = (
        f'cellstorlek_m = {d["cellstorlek_m"]}, hojd_max_m = {d["hojd_max_m"]}, h_min_grader = {d["h_min_grader"]}, '
        f'fasad_steg_m = {d["fasad_steg_m"]}, vaningshojd_m = {d["vaningshojd_m"]}'
    )
    dag += [
        "", "[[analyser]]", 'id = "sol"', 'losare = "dagsljus.soltimmar_mark"', f"fokus = {fok}", f"parametrar = {{ {dagpar} }}",
        "", "[[analyser]]", 'id = "svf"', 'losare = "dagsljus.svf_mark"', f"fokus = {fok}", f"parametrar = {{ {svfpar} }}",
        "", "[[analyser]]", 'id = "vsc"', 'losare = "dagsljus.vsc_fasad"', f"fokus = {fok}", f"parametrar = {{ {vscpar} }}",
    ]  # fmt: skip
    for s in scen:
        slugg = str(s["slug"])
        vatten += ["", "[[jamforelser]]", 'analys = "skyfall"', 'utdata = "max_djup"', 'fore = "bas"', f'efter = "{slugg}"', "troskel = 0.01"]
        dag += [
            "", "[[jamforelser]]", 'analys = "sol"', 'utdata = "soltimmar"', 'fore = "bas"', f'efter = "{slugg}"', "troskel = 0.1",
            "", "[[jamforelser]]", 'analys = "svf"', 'utdata = "svf"', 'fore = "bas"', f'efter = "{slugg}"', "troskel = 0.01",
        ]  # fmt: skip
    fv, fd = fdir / "skyfall.toml", fdir / "dagsljus.toml"
    fv.write_text("\n".join(vatten) + "\n", encoding="utf-8")
    fd.write_text("\n".join(dag) + "\n", encoding="utf-8")
    skrivna = [fv, fd]
    if _har_ekologi_underlag(manifest):
        gid, gver = tolka_ref(str(projekt["geografi"]))
        manifest_fil = versionsmapp(geo_rot, gid, gver) / MANIFEST_NAMN
        natverk = _ekologi_natverk(kommun)
        eko = [
            f"# Ekologi i projektet {pid} (geografin {projekt['geografi']}): nuläget och scenarier.",
            'kontrakt = "K20/v1"',
            f'fraga_id = {_toml_str(pid + "-ekologi")}',
            f'beskrivning = {_toml_str("Ekologisk konnektivitet (skiss) i " + pid)}',
            "",
            "[scenarier.bas]",
            bas(geo, "bas").rstrip(),
        ]
        for s in scen:
            slugg = str(s["slug"])
            eko += ["", f"[scenarier.{slugg}]", bas(geo, slugg).rstrip(), f'k9 = [{_toml_str(_rel(fdir, mapp / s["k9_dagsljus"]))}]']
        eko += [
            "",
            "[[analyser]]",
            'id = "konnektivitet"',
            'losare = "ekologi.konnektivitet"',
            f"parametrar = {{ natverk = {_toml_str(natverk)} }}",
            f"indata = {{ geografi = {_toml_str(_rel(fdir, manifest_fil))} }}",
        ]
        for s in scen:
            slugg = str(s["slug"])
            eko += [
                "", "[[jamforelser]]", 'analys = "konnektivitet"', 'utdata = "stromtathet"',
                'fore = "bas"', f'efter = "{slugg}"', "troskel = 0.01",
                "", "[[jamforelser]]", 'analys = "konnektivitet"', 'utdata = "kansighet"',
                'fore = "bas"', f'efter = "{slugg}"', "troskel = 0.01",
            ]
        fe = fdir / "ekologi.toml"
        fe.write_text("\n".join(eko) + "\n", encoding="utf-8")
        skrivna.append(fe)
    luft_info = _luft_underlag(kommun, geo_rot, manifest)
    if luft_info is not None:
        lk, vader_fil, vagar_fil = luft_info
        veg_toml = "true" if lk.vegetation else "false"
        luft_gem = (
            f"dx_m = {lk.dx_m:g}, riktningar = {lk.riktningar}, "
            f"genomstromningar = {lk.genomstromningar:g}, "
            f"buffert_uppstroms_m = {lk.buffert_uppstroms_m:g}, "
            f"buffert_nedstroms_m = {lk.buffert_nedstroms_m:g}, "
            f"buffert_sidor_m = {lk.buffert_sidor_m:g}, "
            f"omsampling = {_toml_str(lk.omsampling)}, vegetation = {veg_toml}"
        )
        beskr = f"Vindkomfort och luftkvalitet i {pid}"
        if lk.bakgrund_ug_m3 is not None and lk.bakgrund_kalla:
            beskr += f" (bakgrund: {lk.bakgrund_ug_m3:g} µg/m³, {lk.bakgrund_kalla})"
        luft_rader = [
            f"# Luft i projektet {pid} (geografin {projekt['geografi']}): nuläget och scenarier.",
            'kontrakt = "K20/v1"',
            f'fraga_id = {_toml_str(pid + "-luft")}',
            f"beskrivning = {_toml_str(beskr)}",
            "",
            "[scenarier.bas]",
            bas(geo, "bas").rstrip(),
        ]
        for s in scen:
            slugg = str(s["slug"])
            luft_rader += [
                "",
                f"[scenarier.{slugg}]",
                bas(geo, slugg).rstrip(),
                f'k9 = [{_toml_str(_rel(fdir, mapp / s["k9_dagsljus"]))}]',
            ]
        vader_rel = _toml_str(_rel(fdir, vader_fil))
        luft_rader += [
            "",
            "[[analyser]]",
            'id = "vindkomfort"',
            'losare = "luft.vindkomfort"',
            f"parametrar = {{ {luft_gem}, kriterium = {_toml_str(lk.kriterium)} }}",
            f"indata = {{ vader = {vader_rel} }}",
        ]
        if vagar_fil is not None:
            vagar_rel = _toml_str(_rel(fdir, vagar_fil))
            luft_rader += [
                "",
                "[[analyser]]",
                'id = "halt"',
                'losare = "luft.halt"',
                f"parametrar = {{ {luft_gem}, amne = {_toml_str(lk.amne)}, utvardering_hojd_m = {lk.utvardering_hojd_m:g} }}",
                f"indata = {{ vagar = {vagar_rel}, vader = {vader_rel} }}",
            ]
            if lk.bakgrund_ug_m3 is not None:
                luft_rader += [
                    "",
                    "[[analyser]]",
                    'id = "mkn"',
                    'losare = "luft.mkn"',
                    'beror_pa = ["halt"]',
                    f"parametrar = {{ bakgrund_ug_m3 = {lk.bakgrund_ug_m3:g} }}",
                ]
        for s in scen:
            slugg = str(s["slug"])
            luft_rader += [
                "",
                "[[jamforelser]]",
                'analys = "vindkomfort"',
                'utdata = "forstarkning"',
                'fore = "bas"',
                f'efter = "{slugg}"',
                "troskel = 0.05",
            ]
            if vagar_fil is not None:
                luft_rader += [
                    "",
                    "[[jamforelser]]",
                    'analys = "halt"',
                    'utdata = "arsmedel"',
                    'fore = "bas"',
                    f'efter = "{slugg}"',
                    "troskel = 0.1",
                ]
        fl = fdir / "luft.toml"
        fl.write_text("\n".join(luft_rader) + "\n", encoding="utf-8")
        skrivna.append(fl)
    return skrivna


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
    geo_k1 = _geo_k1(geo_rot, manifest)
    rutor_geo = [r for _, r in _rutor(geo_k1)]
    try:
        kontroll = kontrollera_andringar([andring], rutor_geo)
    except RitatFel as exc:
        raise SkapaFel(str(exc)) from exc

    k9 = mapp / "k9"
    ut: dict[str, Any] = {}
    recept = tuple(sha256_fil(r / "recept.json") for r in sorted(geo_k1.glob("mm_*")) if (r / "recept.json").is_file())
    if not recept:
        raise SkapaFel(f"Basen {geo_k1} saknar recept.json.")
    nulage = Planscenario("nulage", recept, ())
    sc = Planscenario(slugg, recept, (Andring.fran_dict(andring),), beskrivning=f"{namn} ({status})")
    for kort in ("dagsljus", "vatten"):
        skriv_scenario(sc, k9 / f"{slugg}_{kort}.json")
    diff = diff_scenarier(nulage, sc)
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
    """Kör skyfall, dagsljus (och ekologi när `ekologi.toml` finns) för nuläget och scenarierna."""
    from webb.api import STANDARD_CACHEMAPP, STANDARD_KORNINGSMAPP
    from webb.fragor import FragaTjanst

    projekt = las_projekt(mapp)
    pid = str(projekt["projekt_id"])
    t = tjanst or FragaTjanst(
        rot=rot, fragemappar=[mapp / "fragor"], cachemapp=rot / STANDARD_CACHEMAPP,
        korningsmapp=rot / STANDARD_KORNINGSMAPP, projektrot=mapp.parent,
    )  # fmt: skip
    fragor = ["skyfall", "dagsljus"]
    if (mapp / "fragor" / "ekologi.toml").is_file():
        fragor.append("ekologi")
    if (mapp / "fragor" / "luft.toml").is_file():
        fragor.append("luft")
    resultat: dict[str, dict[str, Any]] = {}
    for fraga in fragor:
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
