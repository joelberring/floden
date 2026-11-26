# SPDX-License-Identifier: MIT
"""CLI-verktyg för CEDVAL A1-1-vindtunnelvalidering av Flodens 3D-vindlösare.

Exempel (grov CPU-körning utan lokal vindtunnel-CSV):
    python tools/validera_cedval.py --cellstorlek-m 5.0 --losare snabbvind \\
        --ut-json utdata/cedval_a11.json --ut-md utdata/cedval_a11.md

Exempel med lokalt nedladdad CEDVAL A1-1-mätfil:
    python tools/validera_cedval.py --cellstorlek-m 2.5 --losare lbm \\
        --cedval-csv data/lokal/cedval_a11.csv --ut-json utdata/cedval_a11.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from luft.cedval import (
    CedvalA11Spec,
    CedvalFaltJamforelse,
    LosareNamn,
    cedval_till_dict,
    formatera_cedval_rapport_md,
    jamfor_mot_cedval_csv,
    kor_cedval_a11_validering,
)
from luft.lbm import LbmParametrar


def bygg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="validera_cedval",
        description=(
            "Kör Flodens 3D-vindlösare på den publicerade CEDVAL A1-1-geometrin "
            "(EWTL, Universität Hamburg) och jämför valfritt mot lokal mät-CSV."
        ),
    )
    ap.add_argument(
        "--cellstorlek-m",
        type=float,
        default=5.0,
        help="Rutnätets cellstorlek i meter (förval: 5.0 m för snabb CPU-körning).",
    )
    ap.add_argument(
        "--losare",
        choices=("snabbvind", "lbm"),
        default="snabbvind",
        help="3D-vindlösare: snabbvind (skiss/Poisson) eller lbm (D3Q19 LES).",
    )
    ap.add_argument(
        "--cedval-csv",
        type=Path,
        default=None,
        help=(
            "Valfri sökväg till lokalt nedladdad CEDVAL A1-1 CSV-mätfil "
            "(kolumner: x_m,y_m,z_m,u_norm,v_norm,w_norm)."
        ),
    )
    ap.add_argument(
        "--ut-json",
        type=Path,
        default=None,
        help="Skriv K37-kompatibel JSON-rapport till denna fil.",
    )
    ap.add_argument(
        "--ut-md",
        type=Path,
        default=None,
        help="Skriv Markdown-rapport till denna fil.",
    )
    ap.add_argument(
        "--lbm-upprampning",
        type=float,
        default=1.0,
        help="Antal genomströmningar för LBM-upprampning (endast vid --losare lbm).",
    )
    ap.add_argument(
        "--lbm-medel",
        type=float,
        default=1.0,
        help="Antal genomströmningar för LBM-medelvärdesbildning (endast vid --losare lbm).",
    )
    ap.add_argument(
        "--backend",
        choices=("numpy", "cupy"),
        default="numpy",
        help="Beräkningsbackend för LBM (numpy för CPU, cupy för GPU).",
    )
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = bygg_parser()
    args = ap.parse_args(argv)

    losare = cast(LosareNamn, args.losare)
    lbm_param: LbmParametrar | None = None
    if losare == "lbm":
        lbm_param = LbmParametrar(
            u_lattice="auto",
            upprampning_genomstromningar=args.lbm_upprampning,
            medel_genomstromningar=args.lbm_medel,
            vaggmodell="loglag",
            inflodesrand="extrapolation",
            utflodesrand="tryck",
            start="profil",
        )

    if args.cedval_csv is not None and not args.cedval_csv.is_file():
        print(
            f"Fel: CEDVAL-mätfil saknas: {args.cedval_csv}. "
            "Ladda ned officiella EWTL CEDVAL A1-1-data lokalt innan jämförelse.",
            file=sys.stderr,
        )
        return 2

    try:
        resultat = kor_cedval_a11_validering(
            CedvalA11Spec(cellstorlek_m=args.cellstorlek_m),
            cellstorlek_m=args.cellstorlek_m,
            losare=losare,
            lbm_parametrar=lbm_param,
            backend=args.backend,
        )
        falt: CedvalFaltJamforelse | None = None
        if args.cedval_csv is not None:
            falt = jamfor_mot_cedval_csv(resultat, args.cedval_csv)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Fel: {exc}", file=sys.stderr)
        return 2

    rapport_md = formatera_cedval_rapport_md(resultat, falt)
    rapport_dict = cedval_till_dict(resultat, falt)

    if args.ut_json is not None:
        args.ut_json.parent.mkdir(parents=True, exist_ok=True)
        args.ut_json.write_text(
            json.dumps(rapport_dict, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    if args.ut_md is not None:
        args.ut_md.parent.mkdir(parents=True, exist_ok=True)
        args.ut_md.write_text(rapport_md, encoding="utf-8")

    print(rapport_md)
    if args.ut_json is not None:
        print(f"JSON skriven till: {args.ut_json}")
    if args.ut_md is not None:
        print(f"Markdown skriven till: {args.ut_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
