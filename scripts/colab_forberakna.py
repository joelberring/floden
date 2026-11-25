#!/usr/bin/env python3
"""Startskript för att förberäkna ett öppet område i Google Colab (eller annan GPU-maskin).

Exempel i en Colab-cell (med GPU aktiverad under Körtid -> Ändra körtidstyp -> T4/L4/A100):

    !git clone https://github.com/joelberring/floden.git
    %cd floden
    !pip install -e . cupy-cuda12x

    from google.colab import drive
    drive.mount('/content/drive')

    !python scripts/colab_forberakna.py \\
        --omrade /content/drive/MyDrive/floden/stockholm.geojson \\
        --moduler vatten2d,dagsljus \\
        --utdata /content/drive/MyDrive/floden/cache_stockholm

Endast öppen data (`--dataklass oppen`) tillåts i moln-/Colab-miljö enligt `AGENTS.md`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gemensamt.gpu import cuda_tillganglig, tillgangligt_vram_mb
from karna.batch import (
    BatchFel,
    BatchKonfig,
    ar_molnmiljo,
    generera_rutor_for_omrade,
    kontrollera_dataskydd,
    kor_batch_rutor,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Förberäkna öppna data i Google Colab eller lokal GPU-miljö."
    )
    parser.add_argument("--omrade", type=Path, required=True, help="GeoJSON eller GeoPackage.")
    parser.add_argument(
        "--moduler",
        default="vatten2d,dagsljus",
        help="Kommaseparerade moduler (standard: vatten2d,dagsljus).",
    )
    parser.add_argument("--utdata", type=Path, required=True, help="Utdatamapp (t.ex. på Drive).")
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
    args = parser.parse_args(argv)

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

    moduler = tuple(m.strip() for m in args.moduler.split(",") if m.strip())
    rutor = generera_rutor_for_omrade(
        args.omrade,
        rutstorlek_m=args.rutstorlek_m,
        overlapp_m=args.overlapp_m,
    )
    print(f"Genererade {len(rutor)} rutor för {args.omrade.name}. Startar förberäkning...")

    konfig = BatchKonfig(
        moduler=moduler,
        utdata_rot=args.utdata,
        enhet=args.enhet,
        precision=args.precision,
        dataklass="oppen",
        rutstorlek_m=args.rutstorlek_m,
        overlapp_m=args.overlapp_m,
        indata_filer=(args.omrade,),
    )
    status = kor_batch_rutor(rutor, konfig)
    print(
        f"Klart ({status.berakningsplats}): {len(status.klara)} nya, "
        f"{len(status.hoppade)} hoppade (cache), {len(status.felade)} felade "
        f"på {status.total_tid_s:.1f} s."
    )
    return 0 if not status.felade else 1


if __name__ == "__main__":
    raise SystemExit(main())
