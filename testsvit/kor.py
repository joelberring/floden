"""Körskript för Flodens testsvit. Kan anropas direkt med `python3 testsvit/kor.py`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

_REPO_ROT = Path(__file__).resolve().parent.parent
_SRC_MAPP = _REPO_ROT / "src"
if str(_SRC_MAPP) not in sys.path:
    sys.path.insert(0, str(_SRC_MAPP))

from testsvit.korare import TestsvitFel, kor_testsvit  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    """Kör testsviten från kommandoraden."""
    parser = argparse.ArgumentParser(description="Kör Flodens testsvit.")
    parser.add_argument(
        "--grupp",
        type=str,
        default=None,
        help="Filtrera på grupp, t.ex. A, B eller C.",
    )
    parser.add_argument(
        "--rot",
        type=str,
        default=str(Path(__file__).resolve().parent),
        help="Rotmapp för testsviten.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        resultat, tabell = kor_testsvit(rot=args.rot, grupp=args.grupp)
    except TestsvitFel as exc:
        print(f"Fel i testsvit: {exc}", file=sys.stderr)
        return 2

    if not resultat:
        print(f"Inga testfall hittades under '{args.rot}'.")
        return 1

    print(tabell)
    antal_ok = sum(1 for r in resultat if r.godkand)
    return 0 if antal_ok == len(resultat) else 1


if __name__ == "__main__":
    raise SystemExit(main())
