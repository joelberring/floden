# ruff: noqa: E501
"""Kommandona `floden geografi mappa`, `floden geografi visa`, `floden projekt skapa|scenario-hus|kor|kommun-lager`.

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
    m.add_argument("--byggnader-kalla", choices=("wfs", "lantmateriet"), default=None)
    m.add_argument(
        "--villkor-kontrollerade", action="store_true",
        help="Jag har läst Lantmäteriets och kommunens villkor: lagret öppnas för MCP (annars stängt).",
    )  # fmt: skip
    m.add_argument("--felsok", action="store_true", help="Skriv stackspår vid oväntat fel.")
    v = gs.add_parser("visa", help="Visa en geografis manifest (kontrollerar sha256 för alla filer).")
    v.add_argument("ref", help="id@version, t.ex. goteborg-test@v1")
    v.add_argument("--georot", default=STANDARD_GEOROT)

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
    s.add_argument("--avgransning", required=True, help="xmin,ymin,xmax,ymax (EPSG:3011, m), GeoJSON- eller WKT-polygon. E är negativt i Göteborg: skriv --avgransning=-210000,...")
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
    if kommando == "geografi mappa":
        from geografi.mappa import mappa_geografi

        kommun = hitta_kommun_for_geografi(args.geografi, kommunfil_sokvag=_kommunfil(args))
        mappa_geografi(
            kommun, args.geografi, konto_id=args.konto, geo_rot=_abs(rot, args.georot), konfig=konfig,
            konfig_fil=rot / "config.toml", version=args.version,
            villkor_kontrollerade=args.villkor_kontrollerade, byggnadslager=args.byggnadslager,
            byggnader_kalla=args.byggnader_kalla,
        )  # fmt: skip
        return 0
    if kommando == "projekt skapa":
        from kommuner.projekt import skapa_projekt, tolka_avgransning

        gid, _ = tolka_ref(args.geografi)
        kommun = hitta_kommun_for_geografi(gid, kommunfil_sokvag=_kommunfil(args))
        skapa_projekt(
            kommun, ref=args.geografi, avgransning=tolka_avgransning(args.avgransning, args.avgransning_epsg),
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
