"""`floden benchmark`: körtid, minne och avvikelse mot facit för fasta fall.

    floden benchmark [--del skyfall|dagsljus|luft|buller|alla] [--ut fil.json]
                     [--jamfor gammal.json [--troskel-procent 25]] [--upprepningar 5]
    floden benchmark --ny ny.json --jamfor gammal.json     (jämför två filer utan att köra)

Slutkod: 0 = klart, 1 = något mått har försämrats mer än tröskeln, 2 = fel.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchmark.format import (
    DELAR,
    STANDARD_TROSKEL_PROCENT,
    BenchmarkFormatFel,
    jamfor_dokument,
    jamforelsetabell,
    kontrollera,
    tabell,
)
from benchmark.korare import BenchmarkFel, kor_svit, skriv_dokument


def registrera(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "benchmark",
        help="Benchmarksvit: körtid, minne och avvikelse mot facit för fasta fall.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--del", dest="del_", choices=(*DELAR, "alla"), default="alla")
    p.add_argument("--ut", default=None, help="Skriv dokumentet (floden-benchmark/v1) hit.")
    p.add_argument("--uppvarmning", type=int, default=1, help="Körningar som kastas (förval 1).")
    p.add_argument("--upprepningar", type=int, default=5, help="Tidtagna körningar (förval 5).")
    p.add_argument(
        "--jamfor",
        default=None,
        metavar="GAMMAL.json",
        help="Jämför med ett tidigare dokument och flagga försämringar.",
    )
    p.add_argument(
        "--troskel-procent",
        type=float,
        default=STANDARD_TROSKEL_PROCENT,
        help=f"Försämring över så här många procent flaggas (förval "
        f"{STANDARD_TROSKEL_PROCENT:g}).",
    )
    p.add_argument(
        "--ny",
        default=None,
        metavar="NY.json",
        help="Läs ett redan skrivet dokument i stället för att köra (för --jamfor).",
    )


def _las(sokvag: str) -> dict[str, Any]:
    try:
        dok = json.loads(Path(sokvag).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchmarkFel(f"Kunde inte läsa {sokvag}: {exc}") from exc
    try:
        kontrollera(dok)
    except BenchmarkFormatFel as exc:
        raise BenchmarkFel(f"{sokvag} följer inte floden-benchmark/v1: {exc}") from exc
    assert isinstance(dok, dict)
    return dok


def kor(args: argparse.Namespace) -> int:
    try:
        if args.ny:
            if not args.jamfor:
                raise BenchmarkFel("--ny kräver --jamfor.")
            ny = _las(args.ny)
        else:
            delar = DELAR if args.del_ == "alla" else (args.del_,)
            print(
                f"Kör benchmark ({', '.join(delar)}), uppvärmning {args.uppvarmning}, "
                f"upprepningar {args.upprepningar} ...",
                file=sys.stderr,
            )
            ny = kor_svit(
                delar,
                uppvarmning=args.uppvarmning,
                upprepningar=args.upprepningar,
                logg=lambda t: print(t, file=sys.stderr),
            )
            if args.ut:
                skriv_dokument(ny, Path(args.ut))
                print(f"Skrev {args.ut}", file=sys.stderr)
        print(tabell(ny))
        m = ny["maskin"]
        print(
            f"\nMaskin: {m['cpu']}, {m['karnor']} kärnor, Python {m['python']}, "
            f"numpy {m['numpy']}, Rust-kärnan används: "
            f"{'ja' if m['rust_karna']['anvands_i_svit'] else 'nej'} "
            f"(binär {'finns' if m['rust_karna']['tillganglig'] else 'saknas'})."
        )
        if args.jamfor:
            j = jamfor_dokument(ny, _las(args.jamfor), args.troskel_procent)
            print("\nJämförelse med " + args.jamfor + ":\n" + jamforelsetabell(j))
            if j["forsamringar"]:
                return 1
    except (BenchmarkFel, BenchmarkFormatFel) as exc:
        print(f"floden benchmark misslyckades: {exc}", file=sys.stderr)
        return 2
    return 0
