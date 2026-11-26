#!/usr/bin/env python3
"""Startskript för att förberäkna ett öppet område i Google Colab (eller annan GPU-maskin).

Exempel i en Colab-cell (med GPU aktiverad under Körtid -> Ändra körtidstyp -> T4/L4/A100):

    !git clone https://github.com/joelberring/floden.git
    %cd floden
    !pip install -e ".[s3]" cupy-cuda12x

    # Lokal lagring (t.ex. Google Drive):
    !python scripts/colab_forberakna.py \\
        --omrade /content/drive/MyDrive/floden/stockholm.geojson \\
        --moduler vatten2d,dagsljus \\
        --upplosning-m 1.0 \\
        --utdata /content/drive/MyDrive/floden/cache_stockholm

    # Eller direkt till S3 / Hetzner Object Storage (kräver FLODEN_S3_* i miljön):
    !python scripts/colab_forberakna.py \\
        --omrade /content/drive/MyDrive/floden/goteborg.geojson \\
        --moduler vatten2d,dagsljus \\
        --lagring s3 \\
        --kommun goteborg \\
        --upplosning-m 1.0 \\
        --utdata /tmp/floden_batch_status

Endast öppen data (`--dataklass oppen`) tillåts i moln-/Colab-miljö enligt `AGENTS.md`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from gemensamt.gpu import cuda_tillganglig, tillgangligt_vram_mb
from gemensamt.konfig import KonfigFel, las_konfig
from gemensamt.objektlagring import (
    ObjektLagring,
    ObjektLagringFel,
    las_lagring_konfig,
    skapa_lagring,
)
from karna.batch import (
    BatchFel,
    BatchKonfig,
    ar_molnmiljo,
    generera_rutor_for_omrade,
    kontrollera_dataskydd,
    kor_batch_rutor,
    skapa_k1_rutkorare,
)


def bygg_parser() -> argparse.ArgumentParser:
    """Bygger argumentparser för Colab-förberäkning (matchar `floden forberakna`)."""
    parser = argparse.ArgumentParser(
        description="Förberäkna öppna data i Google Colab eller lokal GPU-miljö."
    )
    parser.add_argument("--omrade", type=Path, required=True, help="GeoJSON eller GeoPackage.")
    parser.add_argument(
        "--moduler",
        default="vatten2d,dagsljus",
        help="Kommaseparerade moduler (standard: vatten2d,dagsljus).",
    )
    parser.add_argument(
        "--utdata",
        type=Path,
        required=True,
        help="Utdatamapp för rutcache och batch_status.json (t.ex. på Drive eller /tmp).",
    )
    parser.add_argument(
        "--enhet",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="Beräkningsenhet (standard: auto).",
    )
    parser.add_argument(
        "--precision",
        choices=("f64", "f32"),
        default="f64",
        help="Flyttalsprecision (standard: f64).",
    )
    parser.add_argument(
        "--rutstorlek-m",
        type=float,
        default=500.0,
        help="Rutstorlek i meter (standard: 500).",
    )
    parser.add_argument(
        "--overlapp-m",
        type=float,
        default=25.0,
        help="Överlapp i meter (standard: 25).",
    )
    parser.add_argument(
        "--upplosning-m",
        type=float,
        default=1.0,
        help="Rasterupplösning i meter (standard: 1.0).",
    )
    parser.add_argument(
        "--k1-rot",
        type=Path,
        default=None,
        help="Valfri rotmapp med förberedda K1-rutor (<k1-rot>/mm_<E>_<N>/).",
    )
    parser.add_argument(
        "--lagring",
        choices=("lokal", "s3"),
        default="lokal",
        help="Mållagring för rutor: 'lokal' (standard) eller 's3' (Hetzner Object Storage).",
    )
    parser.add_argument(
        "--kommun",
        type=str,
        default="standard",
        help="Kommunnamn i objektlagringsprefixet geografi/<kommun>/<ruta>/ (standard: standard).",
    )
    parser.add_argument(
        "--behall-radata",
        action="store_true",
        default=False,
        help="Behåll temporär arbetskatalog efter uppladdning (för felsökning; standard: False).",
    )
    parser.add_argument(
        "--konfig",
        type=Path,
        default=Path("config.toml"),
        help="Sökväg till config.toml för [lagring] vid --lagring s3 (standard: config.toml).",
    )
    parser.add_argument(
        "--torrkor",
        action="store_true",
        default=False,
        help="Lista rutor som täcker området utan att starta beräkning.",
    )
    return parser


def skapa_batch_konfig(args: argparse.Namespace) -> BatchKonfig:
    """Bygger en deterministisk `BatchKonfig` (alltid `dataklass='oppen'`) utifrån CLI-flaggor."""
    moduler = tuple(m.strip() for m in str(args.moduler).split(",") if m.strip())
    return BatchKonfig(
        moduler=moduler,
        utdata_rot=args.utdata,
        enhet=args.enhet,
        precision=args.precision,
        dataklass="oppen",
        rutstorlek_m=args.rutstorlek_m,
        overlapp_m=args.overlapp_m,
        upplosning_m=args.upplosning_m,
        kommun=args.kommun,
        lagring_typ=args.lagring,
        behall_radata=bool(args.behall_radata),
        indata_filer=(args.omrade,),
    )


def _skapa_s3_lagring(konfig_fil: Path) -> ObjektLagring:
    app_konfig: dict[str, Any] = {}
    if konfig_fil.is_file():
        lokal_k = konfig_fil.parent / "config.local.toml"
        app_konfig = las_konfig(konfig_fil, lokal_k if lokal_k.is_file() else None)
    lk = las_lagring_konfig(app_konfig)
    if lk.typ != "s3":
        lk = replace(lk, typ="s3")
    return skapa_lagring(lk)


def main(
    argv: Sequence[str] | None = None,
    *,
    objektlagring: ObjektLagring | None = None,
) -> int:
    parser = bygg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    # Colab tillåter aldrig icke-öppen data.
    dataklass = "oppen"
    try:
        kontrollera_dataskydd(dataklass)
    except BatchFel as exc:
        print(f"Fel: {exc}", file=sys.stderr)
        return 2

    vram = tillgangligt_vram_mb()
    print(f"Molnmiljö: {'ja' if ar_molnmiljo() else 'nej'}")
    print(f"CUDA tillgänglig: {'ja' if cuda_tillganglig() else 'nej'} (VRAM: {vram} MB)")

    try:
        rutor = generera_rutor_for_omrade(
            args.omrade,
            rutstorlek_m=args.rutstorlek_m,
            overlapp_m=args.overlapp_m,
            upplosning_m=args.upplosning_m,
        )
        if args.torrkor:
            print(f"Totalt {len(rutor)} rutor för {args.omrade.name}:")
            for r in rutor:
                print(f"  {r.namn}")
            return 0

        print(f"Genererade {len(rutor)} rutor för {args.omrade.name}. Startar förberäkning...")
        konfig = skapa_batch_konfig(args)
        vald_lagring = objektlagring
        if args.lagring == "s3" and vald_lagring is None:
            vald_lagring = _skapa_s3_lagring(args.konfig)
        rutkorare = skapa_k1_rutkorare(args.k1_rot) if args.k1_rot is not None else None
        status = kor_batch_rutor(
            rutor, konfig, rutkorare=rutkorare, objektlagring=vald_lagring
        )
    except (BatchFel, KonfigFel, ObjektLagringFel) as exc:
        print(f"Fel vid förberäkning: {exc}", file=sys.stderr)
        return 2

    print(
        f"Klart ({status.berakningsplats}): {len(status.klara)} nya, "
        f"{len(status.hoppade)} hoppade (cache), {len(status.felade)} felade "
        f"på {status.total_tid_s:.1f} s."
    )
    return 0 if not status.felade else 1


if __name__ == "__main__":
    raise SystemExit(main())
