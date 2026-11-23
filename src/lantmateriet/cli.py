"""Kommandona `floden data lantmateriet` och `floden omrade bygg`.

Protokoll och logik porterade från O-Tid (MIT, joelberring/otid @ 4a446af).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from data_inlasning.cache import DataInlasningFel
from lantmateriet.klient import LantmaterietFel, LmKlient, Tjanster
from lantmateriet.nycklar import NyckelFel, las_nycklar_for_konto


def registrera(subparsers: Any) -> None:
    data = subparsers.add_parser(
        "data",
        help="Hämta data till ett område (just nu: Lantmäteriet).",
        description="Hämta öppna data lokalt. Se docs/DATA.md.",
    )
    dsub = data.add_subparsers(dest="data_kommando", metavar="KÄLLA")
    lm = dsub.add_parser(
        "lantmateriet",
        help="Hämta laser, ortofoto och byggnader från Lantmäteriet till en lokal mapp.",
        description=(
            "Söker i Lantmäteriets STAC-tjänster för områdets yta och hämtar filerna till --ut "
            "med ett manifest (sha256). Nycklar ur LANTMATERIET_KLIENT_ID och "
            "LANTMATERIET_KLIENT_HEMLIGHET eller [lantmateriet] i config.local.toml. "
            "Redan hämtade filer hoppas över. Data hamnar bara på din dator."
        ),
    )
    lm.add_argument(
        "--omrade", type=str, default=None, help="Områdets polygon (GeoPackage/GeoJSON)."
    )
    lm.add_argument("--ut", type=str, default=None, help="Lokal datamapp (utanför repot).")
    lm.add_argument(
        "--laser", action="store_true", help="Laserdata (LAZ). Standard om inget väljs."
    )
    lm.add_argument("--orto", action="store_true", help="Ortofoto (GeoTIFF).")
    lm.add_argument(
        "--byggnader", action="store_true", help="Byggnader (stac-vektor, GeoPackage i zip)."
    )
    lm.add_argument(
        "--laser-samling", type=str, default=None, help="Tvinga STAC-samling för laser."
    )
    lm.add_argument(
        "--orto-samling", type=str, default=None, help="Tvinga STAC-samling för ortofoto."
    )
    lm.add_argument(
        "--byggnads-samling", type=str, default=None, help="Tvinga STAC-samling för byggnader."
    )
    lm.add_argument(
        "--byggnads-lager", type=str, default=None, help="Polygonlager i byggnadsfilen."
    )
    lm.add_argument(
        "--marginal-m", type=float, default=50.0, help="Marginal runt området (standard 50)."
    )
    lm.add_argument(
        "--max-km2", type=float, default=25.0, help="Största tillåtna yta (standard 25)."
    )
    lm.add_argument(
        "--konto",
        type=str,
        default=None,
        help="Konto-id vars sparade Lantmäteri-nyckel används (krävs när [konto] inloggning = "
        '"pa"; annars används nyckeln i miljön eller config.local.toml).',
    )
    lm.add_argument(
        "--lista-samlingar",
        action="store_true",
        help="Lista STAC-samlingarna i höjd, bild och vektor (kräver nycklar) och avsluta.",
    )

    om = subparsers.add_parser(
        "omrade",
        help="Bygg K1 v2-rutor för ett område ur hämtad data.",
        description="Delar området i K1-rutor och bygger markmodellen ur data från "
        "`floden data lantmateriet`.",
    )
    osub = om.add_subparsers(dest="omrade_kommando", metavar="STEG")
    b = osub.add_parser("bygg", help="Bygg K1 v2-rutor (laser, byggnader, ortofoto).")
    b.add_argument("--omrade", type=str, required=True, help="Områdets polygon (GeoPackage).")
    b.add_argument(
        "--data", type=str, required=True, help="Datamappen från `floden data lantmateriet`."
    )
    b.add_argument("--ut", type=str, required=True, help="Målmapp för rutorna (mm_<E>_<N>/).")
    b.add_argument(
        "--byggnader", type=str, default=None, help="Byggnadsfil (GeoPackage/GeoJSON, t.ex. OSM)."
    )
    b.add_argument("--byggnader-lager", type=str, default=None)
    b.add_argument("--byggnader-epsg", type=int, default=None)
    b.add_argument("--ruta-storlek-m", type=float, default=500.0)
    b.add_argument(
        "--en-ruta",
        action="store_true",
        help="Bygg området som en enda ruta (vatten.statisk_fyllning tar en K1-ruta).",
    )
    b.add_argument("--overlapp-m", type=float, default=0.0)
    b.add_argument(
        "--upplosning-m",
        type=float,
        default=1.0,
        help="Cellstorlek (standard 1 m; lasern har 1-2 punkter per m2, finare ger inget).",
    )
    b.add_argument("--utan-orto", action="store_true", help="Använd inte hämtat ortofoto.")
    b.add_argument("--belagd-yta", action="store_true", help="Belagd yta ur laserintensitet.")
    b.add_argument(
        "--utan-tak-ur-laser",
        action="store_true",
        help="Räkna inte oklassade punkter i hus som tak.",
    )
    b.add_argument("--tvinga", action="store_true", help="Bygg om rutor som redan finns.")
    b.add_argument("--verifiera", action="store_true", help="Räkna sha256 på alla indatafiler.")
    b.add_argument(
        "--lasclip", type=str, default=None, help="Sökväg till lasclip (annars FLODEN_LASCLIP)."
    )


def kor(args: argparse.Namespace) -> int:
    try:
        if args.kommando == "data":
            if getattr(args, "data_kommando", None) != "lantmateriet":
                print("Ange källa: floden data lantmateriet --hjälp", file=sys.stderr)
                return 2
            return _kor_data(args)
        if getattr(args, "omrade_kommando", None) != "bygg":
            print("Ange steg: floden omrade bygg --help", file=sys.stderr)
            return 2
        return _kor_bygg(args)
    except NyckelFel as exc:
        print(f"Nycklar: {exc}", file=sys.stderr)
        return 3
    except (LantmaterietFel, DataInlasningFel) as exc:
        print(f"Fel: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # PipelineFel m.fl. ur markmodellen
        if type(exc).__module__.startswith(("markmodell", "gemensamt")):
            print(f"Fel: {exc}", file=sys.stderr)
            return 2
        raise


def _kor_data(args: argparse.Namespace) -> int:
    from lantmateriet.hamta import hamta_omrade
    from lantmateriet.omrade import las_omrade
    from lantmateriet.stac import lista_samlingar

    klient = LmKlient(las_nycklar_for_konto(args.konto), Tjanster.fran_miljo())
    if args.lista_samlingar:
        t = klient.tjanster
        for namn, url in (("hojd", t.hojd), ("bild", t.bild), ("vektor", t.vektor)):
            print(f"[{namn}] {url}")
            for s in lista_samlingar(klient, url):
                print(f"  {s['id']}  {s['title']}")
        return 0
    if not args.omrade or not args.ut:
        print("Ange --omrade och --ut (eller --lista-samlingar).", file=sys.stderr)
        return 2
    laser = args.laser or not (args.orto or args.byggnader)
    omrade = las_omrade(args.omrade, max_km2=args.max_km2)
    print(
        f"Område: {omrade.yta_km2:.2f} km2, bbox EPSG:3011 {tuple(round(v) for v in omrade.bbox)}"
    )
    res = hamta_omrade(
        klient,
        omrade,
        Path(args.ut),
        laser=laser,
        orto=args.orto,
        byggnader=args.byggnader,
        laser_samling=args.laser_samling,
        orto_samling=args.orto_samling,
        byggnads_samling=args.byggnads_samling,
        byggnads_lager=args.byggnads_lager,
        marginal_m=args.marginal_m,
    )
    for v in res.varningar:
        print(f"Varning: {v}")
    print(f"Klart: {res.hamtade} hämtade, {res.hoppade} hoppades över. Manifest: {res.manifest}")
    print(f"Manifestets sha256: {res.manifest_sha256}")
    print("Källa: ©Lantmäteriet. Kontrollera produkternas användarvillkor (docs/DATA.md).")
    return 0


def _kor_bygg(args: argparse.Namespace) -> int:
    from lantmateriet.bygg import bygg_omrade
    from lantmateriet.omrade import las_omrade

    omrade = las_omrade(args.omrade)
    res = bygg_omrade(
        omrade,
        args.data,
        args.ut,
        byggnader_fil=args.byggnader,
        byggnader_lager=args.byggnader_lager,
        byggnader_epsg=args.byggnader_epsg,
        ruta_storlek_m=args.ruta_storlek_m,
        en_ruta=args.en_ruta,
        overlapp_m=args.overlapp_m,
        upplosning_m=args.upplosning_m,
        anvand_orto=not args.utan_orto,
        belagd_yta=args.belagd_yta,
        tak_ur_laser=not args.utan_tak_ur_laser,
        tvinga=args.tvinga,
        verifiera=args.verifiera,
        lasclip_binar=args.lasclip,
    )
    for v in res.varningar:
        print(f"Varning: {v}")
    print(
        f"Klart: {len(res.rutor)} rutor i {res.ut} "
        f"({len(res.hoppade)} fanns redan, {len(res.utan_laser)} saknade laser)"
    )
    print(f"Sammanfattning: {res.sammanfattning}")
    return 0
