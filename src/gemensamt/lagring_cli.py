"""CLI-kommandon under `floden lagring` (S3-livscykelregler och lokal cachestädning)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gemensamt.konfig import KonfigFel, las_konfig
from gemensamt.lokal_cache import LokalCacheFel, las_lokal_cache_konfig, stada_lokal_cache
from gemensamt.objektlagring import ObjektLagringFel, S3Lagring, skapa_lagring
from gemensamt.rensning import (
    STANDARD_MULTIPART_DAGAR,
    STANDARD_RADATA_DAGAR,
    STANDARD_SKISS_DAGAR,
    RensningFel,
    installera_livscykelregler,
)


def registrera(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Registrerar kommandogruppen `floden lagring`."""
    lagring_parser = subparsers.add_parser(
        "lagring",
        help="Hantera S3-objektlagring, livscykelregler (TTL) och lokal LRU-cache.",
    )
    lagring_sub = lagring_parser.add_subparsers(dest="lagring_kommando")

    lc = lagring_sub.add_parser(
        "installera-livscykel",
        help="Installera S3 Lifecycle Policy för automatisk radering av tmp/radata och tmp/skiss.",
    )
    lc.add_argument(
        "--konfig",
        type=Path,
        default=Path("config.toml"),
        help="Sökväg till config.toml (standard: config.toml).",
    )
    lc.add_argument(
        "--radata-dagar",
        type=int,
        default=STANDARD_RADATA_DAGAR,
        help=f"Antal dagar innan tmp/radata/ raderas (standard: {STANDARD_RADATA_DAGAR}).",
    )
    lc.add_argument(
        "--skiss-dagar",
        type=int,
        default=STANDARD_SKISS_DAGAR,
        help=f"Antal dagar innan tmp/skiss/ raderas (standard: {STANDARD_SKISS_DAGAR}).",
    )
    lc.add_argument(
        "--multipart-dagar",
        type=int,
        default=STANDARD_MULTIPART_DAGAR,
        help=(
            "Antal dagar innan ofullständiga multipart-uppladdningar avbryts "
            f"(standard: {STANDARD_MULTIPART_DAGAR})."
        ),
    )
    lc.add_argument("--json", action="store_true", help="Skriv ut installerade regler som JSON.")

    stada = lagring_sub.add_parser(
        "stada",
        help="Städa lokal LRU-cache efter ålder och maxstorlek.",
    )
    stada.add_argument(
        "--konfig",
        type=Path,
        default=Path("config.toml"),
        help="Sökväg till config.toml (standard: config.toml).",
    )
    stada.add_argument("--mapp", type=Path, default=None, help="Cachemapp (åsidosätter config).")
    stada.add_argument(
        "--max-gb",
        type=float,
        default=None,
        help="Maxstorlek i GB innan LRU-eviction körs (åsidosätter config).",
    )
    stada.add_argument(
        "--max-alder-dagar",
        type=float,
        default=None,
        help="Maxålder i dagar för cachade filer (åsidosätter config).",
    )
    stada.add_argument(
        "--torrkor",
        action="store_true",
        help="Visa vad som skulle raderas utan att ta bort några filer.",
    )
    stada.add_argument("--json", action="store_true", help="Skriv ut sammanfattning som JSON.")


def _las_valfri_konfig(sokvag: Path) -> dict[str, Any]:
    if not sokvag.is_file():
        return {}
    lokal = sokvag.parent / "config.local.toml"
    return las_konfig(sokvag, lokal if lokal.is_file() else None)


def kor(args: argparse.Namespace) -> int:
    """Exekverar `floden lagring <underkommando>`."""
    under = getattr(args, "lagring_kommando", None)
    if under == "installera-livscykel":
        try:
            konfig = _las_valfri_konfig(args.konfig)
            lagring = skapa_lagring(konfig)
            if not isinstance(lagring, S3Lagring):
                raise ObjektLagringFel(
                    "Livscykelregler kräver S3-lagring (typ = 's3' i [lagring] eller "
                    "FLODEN_S3_TYP=s3)."
                )
            regler = installera_livscykelregler(
                lagring,
                radata_dagar=args.radata_dagar,
                skiss_dagar=args.skiss_dagar,
                multipart_dagar=args.multipart_dagar,
            )
        except (KonfigFel, ObjektLagringFel, RensningFel) as exc:
            print(f"Fel vid livscykelinstallation: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(regler, indent=2, ensure_ascii=False))
            return 0
        print(
            f"Livscykelregler installerade på bucket '{lagring.bucket}': "
            f"tmp/radata/={args.radata_dagar} d, tmp/skiss/={args.skiss_dagar} d, "
            f"multipart={args.multipart_dagar} d."
        )
        return 0

    if under == "stada":
        try:
            konfig = _las_valfri_konfig(args.konfig)
            ck = las_lokal_cache_konfig(konfig)
            mapp = args.mapp if args.mapp is not None else ck.mapp
            max_gb = args.max_gb if args.max_gb is not None else ck.max_gb
            max_alder = (
                args.max_alder_dagar if args.max_alder_dagar is not None else ck.max_alder_dagar
            )
            res = stada_lokal_cache(mapp, max_gb, max_alder, torrkor=args.torrkor)
        except (KonfigFel, LokalCacheFel) as exc:
            print(f"Fel vid cachestädning: {exc}", file=sys.stderr)
            return 2

        if args.json:
            print(
                json.dumps(
                    {
                        "mapp": str(res.mapp),
                        "torrkor": res.torrkor,
                        "antal_raderade": res.antal_raderade,
                        "antal_raderade_alder": res.antal_raderade_alder,
                        "antal_raderade_lru": res.antal_raderade_lru,
                        "frigjorda_mb": round(res.frigjorda_mb, 3),
                        "kvarvarande_mb": round(res.kvarvarande_mb, 3),
                        "raderade_filer": list(res.raderade_filer),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0

        prefix = "[TORRKÖRNING] Skulle radera" if res.torrkor else "Raderade"
        print(
            f"{prefix} {res.antal_raderade} filer ({res.frigjorda_mb:.2f} MB; "
            f"ålder: {res.antal_raderade_alder}, LRU: {res.antal_raderade_lru}) i {res.mapp}. "
            f"Kvar: {res.kvarvarande_mb:.2f} MB."
        )
        return 0

    print("Ange underkommando: 'installera-livscykel' eller 'stada'.", file=sys.stderr)
    return 2
