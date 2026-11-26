"""CLI-kommandon under `floden konto` (SQLite-backup, verifiering och driftskontroll, S4)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from gemensamt.konfig import KonfigFel, las_konfig
from gemensamt.objektlagring import (
    ObjektLagring,
    ObjektLagringFel,
    las_lagring_konfig,
    skapa_lagring,
)
from konto.backup import (
    STANDARD_BACKUP_MAPP,
    STANDARD_BEHALL_ANTAL,
    STANDARD_BEHALL_DAGAR,
    STANDARD_S3_PREFIX,
    BackupFel,
    drifts_halsa,
    skapa_backup,
    verifiera_backup,
)
from konto.konfig import KontoKonfig, KontoKonfigFel, las_konto_konfig


def registrera(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Registrerar kommandogruppen `floden konto`."""
    konto_parser = subparsers.add_parser(
        "konto",
        help="Online-backup, verifiering och driftskontroll för kontodatabasen (S4).",
        description=(
            "Skapa och verifiera online-säkra SQLite-backuper samt kontrollera driftsstatus."
        ),
    )
    konto_sub = konto_parser.add_subparsers(dest="konto_kommando")

    bp = konto_sub.add_parser(
        "backup",
        help="Skapa verifierad online-backup av kontodatabasen med SHA-256-manifest och rotation.",
    )
    bp.add_argument(
        "--db",
        type=Path,
        default=None,
        help=(
            "Sökväg till kontodatabasen (standard: [konto].databas i konfig, "
            "annars data/konto/konto.sqlite)."
        ),
    )
    bp.add_argument(
        "--ut",
        type=Path,
        default=STANDARD_BACKUP_MAPP,
        help=f"Målmapp för lokala backuper och manifest (standard: {STANDARD_BACKUP_MAPP}).",
    )
    bp.add_argument(
        "--konfig",
        type=Path,
        default=Path("config.toml"),
        help="Sökväg till config.toml för [konto] och [lagring] (standard: config.toml).",
    )
    bp.add_argument(
        "--behall-antal",
        type=int,
        default=STANDARD_BEHALL_ANTAL,
        help=f"Antal lokala backuper att behålla vid rotation (standard: {STANDARD_BEHALL_ANTAL}).",
    )
    bp.add_argument(
        "--behall-dagar",
        type=int,
        default=STANDARD_BEHALL_DAGAR,
        help=f"Maxålder i dagar för lokala backuper (standard: {STANDARD_BEHALL_DAGAR}).",
    )
    bp.add_argument(
        "--s3",
        action="store_true",
        help=(
            "Ladda även upp backup och manifest till konfigurerad objektlagring "
            "([lagring] / FLODEN_S3_*)."
        ),
    )
    bp.add_argument(
        "--s3-prefix",
        type=str,
        default=STANDARD_S3_PREFIX,
        help=f"Objektlagringsprefix för backupfiler (standard: {STANDARD_S3_PREFIX}).",
    )
    bp.add_argument("--json", action="store_true", help="Skriv ut resultatet som JSON (0 PII).")

    vp = konto_sub.add_parser(
        "verifiera",
        help=(
            "Verifiera en backupfil med SHA-256-manifest, "
            "PRAGMA integrity_check och foreign_key_check."
        ),
    )
    vp.add_argument(
        "fil",
        type=Path,
        help="Sökväg till backupfil (.sqlite) som ska verifieras.",
    )
    vp.add_argument("--json", action="store_true", help="Skriv ut resultatet som JSON (0 PII).")

    hp = konto_sub.add_parser(
        "halsa",
        help="Kontrollera driftsstatus för konto-DB (WAL/integrity), nyckelförråd och S3-konfig.",
    )
    hp.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Sökväg till kontodatabasen (åsidosätter konfig).",
    )
    hp.add_argument(
        "--konfig",
        type=Path,
        default=None,
        help="Sökväg till config.toml (standard: config.toml om den finns).",
    )
    hp.add_argument(
        "--json",
        action="store_true",
        help="Skriv ut status som JSON (0 PII, 0 hemligheter).",
    )


def _las_valfri_konfig(sokvag: Path) -> tuple[dict[str, Any], Path | None]:
    if not sokvag.is_file():
        return {}, None
    lokal = sokvag.resolve().parent / "config.local.toml"
    return las_konfig(sokvag, lokal if lokal.is_file() else None), sokvag.resolve().parent


def _bestam_db_for_backup(
    args: argparse.Namespace, app_konfig: Mapping[str, Any], basmapp: Path | None
) -> Path:
    if args.db is not None:
        return Path(args.db)
    try:
        kk = las_konto_konfig(app_konfig)
        db = kk.databas
    except KontoKonfigFel:
        konto_sektion = app_konfig.get("konto", {})
        ra_db = konto_sektion.get("databas") if isinstance(konto_sektion, Mapping) else None
        db = Path(ra_db) if isinstance(ra_db, str) and ra_db.strip() else KontoKonfig().databas
    if not db.is_absolute() and basmapp is not None:
        db = basmapp / db
    return db


def _bygg_objektlagring_for_backup(app_konfig: Mapping[str, Any]) -> ObjektLagring:
    """Skapar `ObjektLagring` för `--s3`; tillåter explicit lokal lagring via konfig/env."""
    lk = las_lagring_konfig(app_konfig)
    sektion = app_konfig.get("lagring", {}) if isinstance(app_konfig, Mapping) else {}
    explicit_lokal = (
        isinstance(sektion, Mapping) and str(sektion.get("typ", "")).strip() == "lokal"
    ) or bool((os.environ.get("FLODEN_LAGRING_ROT") or "").strip())
    if lk.typ != "s3" and not explicit_lokal:
        lk = replace(lk, typ="s3")
    return skapa_lagring(lk)


def _kor_backup(args: argparse.Namespace) -> int:
    try:
        app_konfig, basmapp = _las_valfri_konfig(args.konfig)
        db_sokvag = _bestam_db_for_backup(args, app_konfig, basmapp)
        lagring: ObjektLagring | None = (
            _bygg_objektlagring_for_backup(app_konfig) if args.s3 else None
        )
        res = skapa_backup(
            db_sokvag,
            args.ut,
            behall_antal=args.behall_antal,
            behall_dagar=args.behall_dagar,
            objektlagring=lagring,
            s3_prefix=args.s3_prefix,
        )
    except (BackupFel, KonfigFel, ObjektLagringFel, OSError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "fel": str(exc)}, indent=2, ensure_ascii=False))
        print(f"Fel vid kontobackup: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": True, **res.till_dict()}, indent=2, ensure_ascii=False))
        return 0

    print(f"Backup skapad: {res.backup_fil} ({res.storlek_byte} B)")
    print(f"SHA-256: {res.sha256}")
    print(f"Manifest: {res.manifest_fil}")
    print(f"Kontroll: integrity_check={res.integrity_check}, foreign_key_check=ok")
    if res.s3_nycklar:
        print(f"Objektlagring: {', '.join(res.s3_nycklar)}")
    if res.raderade_lokala:
        print(f"Roterade äldre backuper: {len(res.raderade_lokala)} st")
    return 0


def _kor_verifiera(args: argparse.Namespace) -> int:
    try:
        res = verifiera_backup(args.fil)
    except BackupFel as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "backup_fil": str(args.fil), "fel": str(exc)},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        print(f"Verifiering misslyckades: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(res.till_dict(), indent=2, ensure_ascii=False))
        return 0

    manifest_status = (
        "verifierat" if res.manifest_verifierat else "saknas (endast SQLite kontrollerad)"
    )
    print(f"Backup OK: {res.backup_fil} ({res.storlek_byte} B)")
    print(f"SHA-256: {res.sha256}")
    print(f"Manifest: {manifest_status}")
    print(
        f"SQLite: integrity_check={res.integrity_check}, foreign_key_check=ok, "
        f"tabeller={len(res.tabellrader)}"
    )
    return 0


def _kor_halsa(args: argparse.Namespace) -> int:
    res = drifts_halsa(konfig_fil=args.konfig, db_sokvag=args.db)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res["ok"] else 1

    db = res["databas"]
    nf = res["nyckelforrad"]
    lg = res["lagring"]
    print(f"Kontodrift status: {res['status'].upper()}")
    print(
        f"Databas: {db['sokvag']} (finns={'ja' if db['finns'] else 'nej'}, "
        f"journal_mode={db['journal_mode']}, integrity_check={db['integrity_check']})"
    )
    nyckel_text = "påslagen" if nf["paslagen"] else f"avstängd ({nf['orsak']})"
    print(f"Nyckelförråd ({nf['miljovariabel']}): {nyckel_text}")
    lagring_detalj = f"bucket={lg['bucket']}" if lg["typ"] == "s3" else f"rot={lg['rot']}"
    print(
        f"Objektlagring: typ={lg['typ']}, konfigurerad={'ja' if lg['konfigurerad'] else 'nej'} "
        f"({lagring_detalj})"
    )
    for varning in res["varningar"]:
        print(f"Varning: {varning}")
    return 0 if res["ok"] else 1


def kor(args: argparse.Namespace) -> int:
    """Exekverar `floden konto <underkommando>`."""
    under = getattr(args, "konto_kommando", None)
    if under == "backup":
        return _kor_backup(args)
    if under == "verifiera":
        return _kor_verifiera(args)
    if under == "halsa":
        return _kor_halsa(args)
    print("Ange underkommando: 'backup', 'verifiera' eller 'halsa'.", file=sys.stderr)
    return 2
