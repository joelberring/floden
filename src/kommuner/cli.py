# ruff: noqa: E501
"""Kommandona `floden geografi mappa|visa|planera` och `floden projekt skapa|scenario-hus|kor|kommun-lager`.

Felen skrivs som en rad som säger vad som ska göras, utan stackspår (som kan innehålla värden) om inte
`--felsok` anges. Slutkoder: 2 = källa eller data, 3 = nyckel saknas eller nekas, 4 = konto, behörighet eller
kommunfil, 1 = oväntat. Nycklar, personuppgifter och icke-öppna data skrivs aldrig.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from data_inlasning.cache import DataInlasningFel
from geografi.modell import STANDARD_GEOROT, GeografiFel, las_manifest, tolka_ref
from kommuner.konfig import KommunKonfigFel, hitta_kommun_for_geografi, las_kommun
from kommuner.skapa import SkapaFel
from kommuner.wfs import KommunKallaFel, lista_lager
from lantmateriet.klient import LantmaterietFel
from lantmateriet.nycklar import NyckelFel, _lokal_konfig_och_rot

STANDARD_PROJEKTMAPP = "data/lokal/projekt"


def registrera(subparsers: Any) -> None:
    g = subparsers.add_parser(
        "geografi",
        help="Geografier: ett kartlagt område (kärna + buffert) som projekt byggs i.",
        description="Kommunerna konfigureras i data/kommuner/<id>.toml (docs/KOM-IGANG.md). Kontrakt K46.",
    )
    gs = g.add_subparsers(dest="geografi_kommando", metavar="STEG")
    m = gs.add_parser(
        "mappa",
        help="Hämta, bygg och förberäkna en geografi (kräver superadmin när inloggning är på).",
        description=(
            "Hämtar Lantmäteriets laser med kontots egen nyckel och kommunens öppna byggnader, bygger K1-rutnätet "
            "(500 m), räknar tillrinningen uppströms kärnan och skriver manifestet geo/<id>/<version>/manifest.json. "
            "Kan köras om: en version som finns lämnas orörd."
        ),
    )
    m.add_argument("geografi", help="Geografi-id, t.ex. goteborg-test (ur data/kommuner/<kommun>.toml).")
    m.add_argument("--konto", default=None, help="Konto-id (superadmin; äger nyckeln).")
    m.add_argument("--version", default="v1", help="Version (standard v1). Ny version = ny mappning.")
    m.add_argument("--georot", default=STANDARD_GEOROT, help="Rotmapp för geografier.")
    m.add_argument("--kommunfil", default=None, help="Annan kommunfil än data/kommuner/<kommun>.toml.")
    m.add_argument("--byggnadslager", default=None, help="Åsidosätter byggnadslagret.")
    m.add_argument("--byggnader-kalla", choices=("filpaket", "wfs", "lantmateriet", "lokal_fil"), default=None)
    m.add_argument(
        "--baskarta-fil", action="append", default=[], type=Path,
        help="Baskartan som lokal fil (ZIP, DWG eller dwgread-JSON; kan anges flera gånger), för kommuner med "
        "byggnader ur en baskarta som inte är öppna data (kalla = lokal_fil, t.ex. Stockholm). Filen ligger i serverns "
        "lokala datamapp (/app/data/lokal/), aldrig i git. Byggnaderna och det som härleds ur dem är stängda för MCP.",
    )
    m.add_argument(
        "--hojdmodell-url", action="append", default=[],
        help="Höjdmodellens ZIP-paket (https, tillåten värd). Kan anges flera gånger; ersätter kommunfilens lista.",
    )
    m.add_argument(
        "--hojdmodell-fil", action="append", default=[], type=Path,
        help="Redan nedladdat höjdmodell-paket (ZIP eller GeoTIFF). Kan anges flera gånger.",
    )
    m.add_argument("--nmd", default=None, help="NMD-GeoTIFF (EPSG:3006, CC0, hämtad av dig): valfritt marktäckelager (ekologi).")
    m.add_argument("--vmi", default=None, help="VMI-polygoner (känd våtmark, äldre inventering): valfritt lager, kräver helst --nmd.")
    m.add_argument(
        "--trad", default=None,
        help="Inventerade träd som punkter (GeoPackage, Shapefile eller GeoJSON, hämtad av dig): valfritt lager för ädellövnätverket. "
        "Bara punkt, trädslag och stamomfång/diameter sparas. Stängt för MCP tills --villkor-kontrollerade.",
    )
    m.add_argument(
        "--trad-natverk", default="adellov-nacka",
        help="Nätverket vars [valfritt_lager.falt] anger fältnamnen i trädfilen (förval adellov-nacka).",
    )
    m.add_argument(
        "--nmd-troskel-m", type=float, default=3000.0,
        help="Längsta spridningströskel (effektiva m) att pröva: NMD/VMI klipps med 2-5 km buffert runt kärnan.",
    )
    m.add_argument(
        "--villkor-kontrollerade", action="store_true",
        help="Jag har läst Lantmäteriets och kommunens villkor: lagret öppnas för MCP (annars stängt).",
    )  # fmt: skip
    m.add_argument("--felsok", action="store_true", help="Skriv stackspår vid oväntat fel.")
    v = gs.add_parser("visa", help="Visa en geografis manifest (kontrollerar sha256 för alla filer).")
    v.add_argument("ref", help="id@version, t.ex. goteborg-test@v1")
    v.add_argument("--georot", default=STANDARD_GEOROT)
    pl = gs.add_parser(
        "planera",
        help="Planera rutnät, buffert och uppskattade resurser (tid, RAM, disk) för en geografi.",
        description=(
            "Delar en avgränsning (GeoJSON, GeoPackage eller bbox) i standardiserade rutor i EPSG:3011, "
            "beräknar kärn- och buffertrutor samt uppskattar körtid, topp-RAM och diskutrymme utifrån "
            "repots källbelagda benchmarkvärden (uppskattning före S6-servermätning). Läser enbart "
            "avgränsningen och kommunfilen, aldrig stängd baskarta."
        ),
    )
    pl.add_argument("kommun", help="Kommun-id (ur data/kommuner/<id>.toml), t.ex. stockholm eller goteborg.")
    pl.add_argument(
        "--avgransning",
        required=True,
        help="Sökväg till GeoJSON/GeoPackage (.geojson, .json, .gpkg) eller bbox 'xmin,ymin,xmax,ymax'.",
    )
    pl.add_argument(
        "--avgransning-epsg",
        type=int,
        default=None,
        help="Koordinatsystem för --avgransning om filen saknar CRS eller vid bbox (t.ex. 3011, 3006, 4326).",
    )
    pl.add_argument(
        "--upplosning-m",
        type=float,
        default=None,
        help="Cellupplösning i meter (t.ex. 0.5, 1.0, 2.5; standard ur kommunfilen, annars 0.5).",
    )
    pl.add_argument(
        "--buffert-m",
        type=float,
        default=None,
        help="Buffert runt avgränsningen i meter (standard: dagsljusets påverkansradie och kommunens kontext).",
    )
    pl.add_argument("--rutstorlek-m", type=float, default=500.0, help="Inre rutstorlek i meter (standard 500.0).")
    pl.add_argument("--overlapp-m", type=float, default=25.0, help="Överlapp per ruta i meter (standard 25.0).")
    pl.add_argument(
        "--arbetare",
        type=int,
        default=1,
        help="Antal parallella CPU-arbetare för baslinjeuppskattningen (standard 1).",
    )
    pl.add_argument("--kommunfil", default=None, help="Annan kommunfil än data/kommuner/<kommun>.toml.")
    pl.add_argument(
        "--json",
        dest="utdata_json",
        action="store_true",
        help="Skriv maskinläsbar JSON i stället för tabell.",
    )
    pl.add_argument("--felsok", action="store_true", help="Skriv stackspår vid oväntat fel.")

    p = subparsers.add_parser(
        "projekt",
        help="Projekt i en geografi (låser geografins version).",
        description="Ett projekt byggs i en kartlagd geografi: floden geografi mappa ... först.",
    )
    sub = p.add_subparsers(dest="projekt_kommando", metavar="STEG")
    s = sub.add_parser(
        "skapa",
        help="Skapa ett projekt i en geografi och registrera kontot som utredare.",
        description=(
            "Avgränsningen utanför geografins giltiga kärna ger en varning, inte ett fel (utanför geografins "
            "yttre gräns, där data saknas, är ett fel). Kan köras om."
        ),
    )
    s.add_argument("--geografi", required=True, help="id@version, t.ex. goteborg-test@v1")
    s.add_argument("--avgransning", required=True, help="xmin,ymin,xmax,ymax (EPSG:3011, m), GeoJSON- eller WKT-polygon (polygonen sparas och avgör vad som räknas till projektet; rutnätet är rektangeln runt den). E är negativt i Göteborg: skriv --avgransning=-210000,...")
    s.add_argument("--avgransning-epsg", type=int, default=3011, help="Koordinatsystem för --avgransning.")
    s.add_argument("--namn", required=True)
    s.add_argument("--konto", default=None, help="Konto-id (blir utredare i projektet).")
    s.add_argument("--organisation", default=None)
    s.add_argument("--georot", default=STANDARD_GEOROT)
    s.add_argument("--projektmapp", default=STANDARD_PROJEKTMAPP)
    s.add_argument("--kommunfil", default=None)
    s.add_argument("--felsok", action="store_true")
    h = sub.add_parser("scenario-hus", help="Lägg till ett ritat hus som scenario och visa diffen mot nuläget.")
    h.add_argument("projekt", help="Projekt-id.")
    h.add_argument("--namn", required=True)
    h.add_argument("--centrum", required=True, help="E,N (EPSG:3011, m). E är negativt i Göteborg: skriv --centrum=-209834,6413830")
    h.add_argument("--bredd", type=float, default=20.0)
    h.add_argument("--djup", type=float, default=12.0)
    h.add_argument("--hojd", type=float, required=True, help="Höjd över mark (m).")
    h.add_argument("--status", choices=("forslag", "planerat"), default="forslag")
    h.add_argument("--georot", default=STANDARD_GEOROT)
    h.add_argument("--projektmapp", default=STANDARD_PROJEKTMAPP)
    h.add_argument("--kommunfil", default=None)
    h.add_argument("--felsok", action="store_true")
    r = sub.add_parser("kor", help="Kör skyfall och dagsljus för nuläget och scenarierna.")
    r.add_argument("projekt")
    r.add_argument("--projektmapp", default=STANDARD_PROJEKTMAPP)
    r.add_argument("--felsok", action="store_true")
    k = sub.add_parser("kommun-lager", help="Lista en kommuns WFS-lager (GetCapabilities).")
    k.add_argument("kommun")
    k.add_argument("--kommunfil", default=None)


def _abs(rot: Path, p: str) -> Path:
    pp = Path(p)
    return pp if pp.is_absolute() else rot / pp


def _kommunfil(args: argparse.Namespace) -> Path | None:
    return Path(args.kommunfil) if getattr(args, "kommunfil", None) else None


def _kor_inre(args: argparse.Namespace, kommando: str) -> int:
    konfig, rot = _lokal_konfig_och_rot()
    rot = rot or Path.cwd()
    if kommando == "geografi visa":
        d, sha = las_manifest(_abs(rot, args.georot), args.ref)
        print(json.dumps({"manifest_sha256": sha, **d}, indent=2, ensure_ascii=False))
        return 0
    if kommando == "geografi planera":
        from geografi.planera import formatera_plan_tabell, planera_geografi

        kommun = las_kommun(args.kommun, rot=rot, fil=_kommunfil(args))
        avgr = args.avgransning
        avgr_path = Path(avgr)
        if not avgr_path.is_absolute() and not avgr_path.is_file() and (rot / avgr_path).is_file():
            avgr = str(rot / avgr_path)
        plan = planera_geografi(
            kommun,
            avgr,
            upplosning_m=args.upplosning_m,
            buffert_m=args.buffert_m,
            rutstorlek_m=args.rutstorlek_m,
            overlapp_m=args.overlapp_m,
            arbetare=args.arbetare,
            avgransning_epsg=args.avgransning_epsg,
        )
        if args.utdata_json:
            print(json.dumps(plan.till_dict(), indent=2, ensure_ascii=False))
        else:
            print(formatera_plan_tabell(plan))
        return 0
    if kommando == "geografi mappa":
        from geografi.mappa import mappa_geografi

        kommun = hitta_kommun_for_geografi(args.geografi, kommunfil_sokvag=_kommunfil(args))
        mappa_geografi(
            kommun, args.geografi, konto_id=args.konto, geo_rot=_abs(rot, args.georot), konfig=konfig,
            konfig_fil=rot / "config.toml", version=args.version,
            villkor_kontrollerade=args.villkor_kontrollerade, byggnadslager=args.byggnadslager,
            byggnader_kalla=args.byggnader_kalla, baskarta_filer=tuple(args.baskarta_fil),
            hojdmodell_urlar=tuple(args.hojdmodell_url), hojdmodell_filer=tuple(args.hojdmodell_fil),
            nmd_fil=_abs(rot, args.nmd) if args.nmd else None,
            vmi_fil=_abs(rot, args.vmi) if args.vmi else None, nmd_troskel_m=args.nmd_troskel_m,
            trad_fil=_abs(rot, args.trad) if args.trad else None, trad_natverk=args.trad_natverk,
        )  # fmt: skip
        return 0
    if kommando == "projekt skapa":
        from kommuner.projekt import skapa_projekt, tolka_avgransning_objekt_form

        gid, _ = tolka_ref(args.geografi)
        kommun = hitta_kommun_for_geografi(gid, kommunfil_sokvag=_kommunfil(args))
        avgr, avgr_polygon = tolka_avgransning_objekt_form(args.avgransning, args.avgransning_epsg)
        skapa_projekt(
            kommun, ref=args.geografi, avgransning=avgr, polygon=avgr_polygon,
            namn=args.namn, konto_id=args.konto, geo_rot=_abs(rot, args.georot),
            projektrot=_abs(rot, args.projektmapp), konfig=konfig, konfig_fil=rot / "config.toml",
            organisation_id=args.organisation,
        )  # fmt: skip
        return 0
    if kommando == "projekt scenario-hus":
        from kommuner.projekt import las_projekt, skapa_scenario_hus

        mapp = _abs(rot, args.projektmapp) / args.projekt
        pr = las_projekt(mapp)
        gid, _ = tolka_ref(str(pr["geografi"]))
        kommun = hitta_kommun_for_geografi(gid, kommunfil_sokvag=_kommunfil(args))
        try:
            e, n = (float(x) for x in args.centrum.split(","))
        except ValueError as exc:
            raise SkapaFel("--centrum: ange 'E,N' i meter (EPSG:3011).") from exc
        skapa_scenario_hus(
            mapp, kommun=kommun, geo_rot=_abs(rot, args.georot), namn=args.namn, centrum=(e, n),
            bredd_m=args.bredd, djup_m=args.djup, hojd_m=args.hojd, status=args.status,
        )  # fmt: skip
        return 0
    if kommando == "projekt kor":
        from kommuner.projekt import kor_projekt

        kor_projekt(_abs(rot, args.projektmapp) / args.projekt, rot=rot)
        return 0
    if kommando == "projekt kommun-lager":
        kommun = las_kommun(args.kommun, fil=_kommunfil(args))
        for namn in lista_lager(kommun.byggnader.wfs_url, kommun.byggnader.tillatna_varddomaner):
            print(namn)
        return 0
    raise SkapaFel(f"Okänt kommando: {kommando}")


def kor(args: argparse.Namespace) -> int:
    """Gemensam ingång för `geografi` och `projekt`."""
    huvud = args.kommando
    steg = getattr(args, f"{huvud}_kommando", None)
    if steg is None:
        print(f"Ange steg: floden {huvud} --help", file=sys.stderr)
        return 2
    felsok = bool(getattr(args, "felsok", False))
    try:
        return _kor_inre(args, f"{huvud} {steg}")
    except NyckelFel as exc:
        print(f"Nycklar: {exc}", file=sys.stderr)
        return 3
    except KommunKonfigFel as exc:
        print(f"Kommunfilen: {exc}", file=sys.stderr)
        return 4
    except (SkapaFel, GeografiFel) as exc:
        print(f"Fel: {exc}", file=sys.stderr)
        return 4
    except (KommunKallaFel, LantmaterietFel, DataInlasningFel) as exc:
        print(f"Källa: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - ett tydligt fel, inte ett stackspår med värden
        if type(exc).__module__.startswith(("markmodell", "gemensamt")):
            print(f"Fel: {exc}", file=sys.stderr)
            return 2
        print(f"Oväntat fel ({type(exc).__name__}): {exc}. Kör med --felsok för stackspår.", file=sys.stderr)
        if felsok:
            traceback.print_exc()
        return 1
