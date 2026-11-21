"""`floden demo`: bygg demovärlden och kör demofrågorna så att webben visar resultat direkt.

    floden demo                 bygger världen (700 x 700 m, 1 m-celler) och kör dagsljus och vatten
    floden demo --tvinga        bygger om världen även om den är oförändrad

Resultaten ligger som körningar `demo-dagsljus` och `demo-vatten` i webbens körningsmapp
(`[webb] korningsmapp`) och visas av `floden webb` direkt. Samma kommando igen gör ingenting nytt.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from demovarld.demo import LITEN, NORMAL, DemoFel, kor_demo
from webb.api import STANDARD_DEMOMAPP, _skapa_fragetjanst
from webb.fel import FlodenWebbFel


def registrera(subparsers: Any) -> None:
    """Lägger till `demo` i Flodens huvudparser."""
    p = subparsers.add_parser(
        "demo",
        help="Bygg demovärlden och kör demofrågorna (dagsljus, vatten) för webben.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--konfig", default="config.toml", help="Sökväg till config.toml.")
    p.add_argument(
        "--varld", default=None, help=f"Mapp för världen (standard: {STANDARD_DEMOMAPP})."
    )
    p.add_argument(
        "--tvinga", action="store_true", help="Bygg om världen även om den är oförändrad."
    )
    p.add_argument(
        "--liten",
        action="store_true",
        help="Litet läge (2 m-celler, 64 riktningar): snabbt, grövre. Frågefilerna hamnar i "
        "världsmappen, inte i exempel/fragor.",
    )


def kor(args: argparse.Namespace) -> int:
    konfig = Path(args.konfig)
    lage = LITEN if args.liten else NORMAL
    rot = konfig.resolve().parent
    varld = Path(args.varld) if args.varld else Path(STANDARD_DEMOMAPP)
    varld = varld if varld.is_absolute() else rot / varld
    if args.liten:
        varld = varld.with_name(varld.name + "-liten")
    fragedir = varld / "fragor" if args.liten else rot / "exempel" / "fragor"
    try:
        tjanst = _skapa_fragetjanst(konfig, [fragedir], None, None)
        if args.liten:
            # Det lilla läget skriver sin översikt bredvid sin värld, inte över den vanliga demon.
            tjanst.demomapp = varld
        t0 = time.perf_counter()
        kor_demo(tjanst=tjanst, varld_dir=varld, fragedir=fragedir, lage=lage, tvinga=args.tvinga)
    except (DemoFel, FlodenWebbFel) as exc:
        print(f"floden demo misslyckades: {exc}", file=sys.stderr)
        return 1
    print(f"Klart på {time.perf_counter() - t0:.1f} s. Starta webben med: floden webb")

    return 0
