"""Kommandoradsgränssnitt för Floden (skyfallsmodellen)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from testsvit.korare import TestsvitFel, kor_testsvit


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

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
