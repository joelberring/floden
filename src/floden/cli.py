"""Kommandoradsgränssnitt för Floden (skyfallsmodellen)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from dynamik.nodmodell import DynamikFel
from dynamik.risk import berakna_kumulativ_oversvamningsrisk
from gemensamt.regn import (
    STANDARD_CDS_TOPPFAKTOR,
    STANDARD_TIDSSTEG_MIN,
    STANDARD_VARAKTIGHET_MIN,
    RegnFel,
    lista_regnforval,
    skapa_regn_fran_spec,
    skriv_regn_k4_csv,
)
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.typer import SankGrafFel, las_brytlinjer_fran_gpkg, las_oppningar_fran_gpkg
from testsvit.k1_io import K1FormatFel, las_geotiff
from testsvit.korare import TestsvitFel, hitta_testfall, kor_testsvit


def _hamta_ej_implementerad_meddelande(kommando: str, uppdrag: str) -> str:
    return (
        f"Kommandot 'floden {kommando}' är ett skelett och implementeras i uppdrag {uppdrag}."
    )


def _kor_skelettkommando(kommando: str, uppdrag: str) -> int:
    print(_hamta_ej_implementerad_meddelande(kommando, uppdrag))
    return 0


def _kor_testsvit_kommando(rot: str, grupp: str | None) -> int:
    try:
        resultat, tabell = kor_testsvit(rot=rot, grupp=grupp)
    except TestsvitFel as exc:
        print(f"Fel i testsvit: {exc}", file=sys.stderr)
        return 2

    if not resultat:
        filter_info = f" för grupp {grupp.upper()}" if grupp else ""
        print(f"Inga testfall hittades under '{rot}'{filter_info}.")
        return 1

    print(tabell)
    antal_ok = sum(1 for r in resultat if r.godkand)
    antal_totalt = len(resultat)
    print(f"\nSammanfattning: {antal_ok}/{antal_totalt} testfall passerade.")
    return 0 if antal_ok == antal_totalt else 1


def _kor_regn_kommando(args: argparse.Namespace) -> int:
    try:
        if args.lista or (args.id is None and args.aterkomsttid_ar is None):
            forval = lista_regnforval(region=args.region)
            if args.json:
                print(json.dumps([f.till_dict() for f in forval], indent=2, ensure_ascii=False))
                return 0
            print(
                "| Regn-ID | Region | T (år) | kf | Varaktighet (min) | Totalt (mm) | Max (mm/h) |"
            )
            print("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
            for item in forval:
                info = item.till_dict()
                print(
                    f"| {item.regn_id} | {item.region} | {item.aterkomsttid_ar:g} "
                    f"| {item.klimatfaktor:.2f} | {item.varaktighet_min:g} "
                    f"| {info['total_djup_mm']:.1f} | {info['max_intensitet_mm_h']:.1f} |"
                )
            return 0

        serie = skapa_regn_fran_spec(
            regn_id=args.id,
            aterkomsttid_ar=args.aterkomsttid_ar,
            klimatfaktor=args.klimatfaktor,
            varaktighet_min=args.varaktighet_min,
            typ=args.typ,
            tidssteg_min=args.tidssteg_min,
            toppfaktor=args.toppfaktor,
        )
        if args.ut is not None:
            skriv_regn_k4_csv(args.ut, serie)

        if args.json:
            print(json.dumps(serie.till_dict(), indent=2, ensure_ascii=False))
        else:
            print(f"Regn: {serie.namn} ({serie.regn_id})")
            print(f"Typ: {serie.typ}, varaktighet: {serie.varaktighet_min:g} min")
            print(f"Klimatfaktor: {serie.klimatfaktor:.2f}, totalt: {serie.total_djup_mm:.2f} mm")
            print(f"Maxintensitet: {serie.max_intensitet_mm_h:.2f} mm/h")
            print(f"Källa: {serie.statistikkalla}")
            if args.ut is not None:
                print(f"Skrev K4-CSV till: {args.ut}")
        return 0
    except (RegnFel, OSError) as exc:
        print(f"Fel i regnkommando: {exc}", file=sys.stderr)
        return 2


def _hitta_omrade_mapp(omrade_arg: str, rot: str) -> Path:
    direkt = Path(omrade_arg)
    if (direkt / "mark.tif").is_file():
        return direkt
    if (direkt / "indata" / "mark.tif").is_file():
        return direkt / "indata"

    fall_lista = hitta_testfall(rot=rot)
    for fall in fall_lista:
        if fall.id.upper() == omrade_arg.upper() or fall.mapp.name == omrade_arg:
            indata = fall.mapp / "indata"
            if (indata / "mark.tif").is_file():
                return indata
    raise TestsvitFel(
        f"Hittade inget K1-område för '{omrade_arg}' (sökte direkt sökväg och under '{rot}')."
    )


def _kor_risk_kommando(args: argparse.Namespace) -> int:
    try:
        indata = _hitta_omrade_mapp(args.omrade, args.rot)
        mark_raw, geo = las_geotiff(indata / "mark.tif")
        osak_raw, _ = las_geotiff(indata / "osakerhet.tif")
        slag_raw, _ = las_geotiff(indata / "markslag.tif")
        mark_z = np.asarray(mark_raw, dtype=np.float32)
        osak_z = np.asarray(osak_raw, dtype=np.float32)
        markslag = np.asarray(slag_raw, dtype=np.uint8)

        bryt_fil = indata / "brytlinjer.gpkg"
        brytlinjer = tuple(las_brytlinjer_fran_gpkg(bryt_fil)) if bryt_fil.is_file() else ()
        opp_fil = indata / "oppningar.gpkg"
        oppningar = tuple(las_oppningar_fran_gpkg(opp_fil)) if opp_fil.is_file() else ()

        graf = bygg_sankgraf(
            mark_z,
            osak_z,
            markslag,
            geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
        )

        aterkomst_lista: list[float] | None = None
        if args.aterkomsttider:
            aterkomst_lista = [
                float(x.strip()) for x in args.aterkomsttider.split(",") if x.strip()
            ]

        res = berakna_kumulativ_oversvamningsrisk(
            graf,
            gransdjup_m=args.gransdjup_m,
            max_aterkomsttid_ar=args.max_aterkomsttid_ar,
            aterkomsttider_ar=aterkomst_lista,
            klimatfaktor=args.klimatfaktor,
            varaktighet_min=args.varaktighet_min,
            regntyp=args.regntyp,
            tidshorisont_ar=args.tidshorisont_ar,
        )

        if args.json:
            print(json.dumps(res.sammanfattning_dict(), indent=2, ensure_ascii=False))
            return 0

        min_t = (
            f"{res.min_kritisk_aterkomsttid_ar:.1f} år"
            if math.isfinite(res.min_kritisk_aterkomsttid_ar)
            else f">{res.max_aterkomsttid_ar:g} år (ej uppnådd)"
        )
        print(
            f"Kumulativ översvämningsrisk (gränsdjup >= {res.gransdjup_m:.2f} m, "
            f"upp till {res.max_aterkomsttid_ar:g}-årsregn, kf={res.klimatfaktor:.2f}, "
            f"horisont {res.tidshorisont_ar:g} år)"
        )
        print(
            f"Yta över gränsdjup: {res.area_over_grans_m2:.1f} m² "
            f"({res.andel_over_grans * 100:.1f} %)"
        )
        print(f"Lägsta kritiska återkomsttid: {min_t}")
        print(f"Max årlig överskridandesannolikhet: {res.max_arlig_sannolikhet * 100:.2f} %/år")
        print(
            f"Max kumulativ sannolikhet ({res.tidshorisont_ar:g} år): "
            f"{res.max_kumulativ_sannolikhet * 100:.1f} %"
        )
        print(f"Max förväntat årligt djup: {res.max_forvantat_arligt_djup_m:.4f} m/år")
        print("")
        print("| Återkomsttid (år) | Regndjup (mm) | Maxdjup (m) | Yta >= gräns (m²) |")
        print("| ---: | ---: | ---: | ---: |")
        for steg in res.steg:
            print(
                f"| {steg.aterkomsttid_ar:g} | {steg.regn.total_djup_mm:.1f} "
                f"| {steg.max_djup_m:.3f} | {steg.area_over_grans_m2:.1f} |"
            )
        return 0
    except (
        TestsvitFel,
        K1FormatFel,
        SankGrafFel,
        DynamikFel,
        RegnFel,
        ValueError,
        OSError,
    ) as exc:
        print(f"Fel i riskkommando: {exc}", file=sys.stderr)
        return 2


def bygg_parser() -> argparse.ArgumentParser:
    """Bygger huvudparsern för kommandoradsverktyget `floden`."""
    parser = argparse.ArgumentParser(
        prog="floden",
        description="Floden – öppen dynamisk skyfallsmodell på gatunivå.",
    )
    subparsers = parser.add_subparsers(dest="kommando", metavar="KOMMANDO")

    subparsers.add_parser(
        "markmodell",
        help="Bygg markmodell-ruta (K1). Skelett – implementeras i T01.",
        description="Bygg markmodell-ruta enligt kontrakt K1 (skelett, implementeras i T01).",
    )
    subparsers.add_parser(
        "sankgraf",
        help="Bygg sänkgraf från markmodell (K2). Skelett – implementeras i T02.",
        description="Bygg sänkgraf enligt kontrakt K2 (skelett, implementeras i T02).",
    )
    subparsers.add_parser(
        "kor",
        help="Kör skyfallsberäkning (K6). Skelett – implementeras i T03.",
        description="Kör skyfallsberäkning enligt kontrakt K6 (skelett, implementeras i T03).",
    )

    testsvit_parser = subparsers.add_parser(
        "testsvit",
        help="Kör valideringstestsviten och visa en tabell med mått.",
        description="Kör testfall i testsviten och skriver ut en tabell med mätvärden.",
    )
    testsvit_parser.add_argument(
        "--grupp",
        type=str,
        default=None,
        help="Filtrera på testgrupp, t.ex. A, B eller C.",
    )
    testsvit_parser.add_argument(
        "--rot",
        type=str,
        default="testsvit",
        help="Rotmapp för testsviten (standard: testsvit).",
    )

    regn_parser = subparsers.add_parser(
        "regn",
        help="Lista eller exportera svenska och stockholmska regn (K4/v1).",
        description="Lista inbyggda Sverige/Stockholm-regn eller generera K4-CSV.",
    )
    regn_parser.add_argument(
        "--lista",
        action="store_true",
        help="Lista alla inbyggda regnförval.",
    )
    regn_parser.add_argument(
        "--region",
        type=str,
        default=None,
        help="Filtrera förval på region ('Stockholm' eller 'Sverige').",
    )
    regn_parser.add_argument(
        "--id",
        type=str,
        default=None,
        help="Inbyggt regn-ID, t.ex. stockholm_cds_6h_100ar_kf140.",
    )
    regn_parser.add_argument(
        "--aterkomsttid-ar",
        type=float,
        default=None,
        help="Återkomsttid i år (t.ex. 20, 50, 100, 200, 500).",
    )
    regn_parser.add_argument(
        "--klimatfaktor",
        type=float,
        default=1.0,
        help="Klimatfaktor (t.ex. 1.0, 1.24, 1.25, 1.4). Standard: 1.0 (eller förvalets).",
    )
    regn_parser.add_argument(
        "--varaktighet-min",
        type=float,
        default=STANDARD_VARAKTIGHET_MIN,
        help="Regnvaraktighet i minuter (standard: 360 min = 6 h).",
    )
    regn_parser.add_argument(
        "--tidssteg-min",
        type=float,
        default=STANDARD_TIDSSTEG_MIN,
        help="Tidssteg för CDS-regn i minuter (standard: 5 min).",
    )
    regn_parser.add_argument(
        "--toppfaktor",
        type=float,
        default=STANDARD_CDS_TOPPFAKTOR,
        help="Toppfaktor r för CDS-regn (standard: 0.37).",
    )
    regn_parser.add_argument(
        "--typ",
        type=str,
        default="cds",
        choices=("cds", "block"),
        help="Regnprofil: 'cds' (standard) eller 'block'.",
    )
    regn_parser.add_argument(
        "--ut",
        type=str,
        default=None,
        help="Sökväg för att skriva K4-regnfil (.csv).",
    )
    regn_parser.add_argument(
        "--json",
        action="store_true",
        help="Skriv ut resultat som JSON.",
    )

    risk_parser = subparsers.add_parser(
        "risk",
        help="Beräkna kumulativ översvämningsrisk upp till gränsdjup och max återkomsttid.",
        description="Beräknar kumulativ översvämningsrisk över återkomsttider för ett K1-område.",
    )
    risk_parser.add_argument(
        "--omrade",
        type=str,
        required=True,
        help="Områdes-ID (t.ex. A3) eller sökväg till K1-indatamapp.",
    )
    risk_parser.add_argument(
        "--gransdjup-m",
        type=float,
        required=True,
        help="Tröskeldjup i meter (t.ex. 0.20 eller 0.30 för räddningstjänst).",
    )
    risk_parser.add_argument(
        "--max-aterkomsttid-ar",
        type=float,
        default=100.0,
        help="Högsta återkomsttid i år (t.ex. 100 eller 500). Standard: 100.",
    )
    risk_parser.add_argument(
        "--aterkomsttider",
        type=str,
        default=None,
        help="Kommaseparerade återkomsttider i år, t.ex. '2,5,10,20,50,100'.",
    )
    risk_parser.add_argument(
        "--klimatfaktor",
        type=float,
        default=1.24,
        help="Klimatfaktor (t.ex. 1.0, 1.24, 1.25, 1.4). Standard: 1.24.",
    )
    risk_parser.add_argument(
        "--tidshorisont-ar",
        type=float,
        default=50.0,
        help="Tidshorisont i år för kumulativ sannolikhet (standard: 50).",
    )
    risk_parser.add_argument(
        "--varaktighet-min",
        type=float,
        default=STANDARD_VARAKTIGHET_MIN,
        help="Regnvaraktighet i minuter (standard: 360).",
    )
    risk_parser.add_argument(
        "--regntyp",
        type=str,
        default="cds",
        choices=("cds", "block"),
        help="Regntyp: 'cds' (standard) eller 'block'.",
    )
    risk_parser.add_argument(
        "--rot",
        type=str,
        default="testsvit",
        help="Rotmapp för testsviten (standard: testsvit).",
    )
    risk_parser.add_argument(
        "--json",
        action="store_true",
        help="Skriv ut sammanfattning som JSON.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Startpunkt för CLI:t `floden`."""
    parser = bygg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.kommando is None:
        parser.print_help()
        return 0
    if args.kommando == "markmodell":
        return _kor_skelettkommando("markmodell", "T01")
    if args.kommando == "sankgraf":
        return _kor_skelettkommando("sankgraf", "T02")
    if args.kommando == "kor":
        return _kor_skelettkommando("kor", "T03")
    if args.kommando == "testsvit":
        return _kor_testsvit_kommando(rot=args.rot, grupp=args.grupp)
    if args.kommando == "regn":
        return _kor_regn_kommando(args)
    if args.kommando == "risk":
        return _kor_risk_kommando(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
