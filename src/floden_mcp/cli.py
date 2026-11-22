# ruff: noqa: E501
"""`floden mcp`: startar Floden som MCP-server (stdio eller Streamable HTTP)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from floden_mcp.dataklass import Policy, PolicyFel, tolka_klass
from floden_mcp.korlogg import STANDARD_LOGG, Korlogg

STANDARD_PORT = 8766


class McpStartFel(Exception):
    """Servern kan inte startas (konfiguration eller användning)."""


def hitta_rot(rot: str | Path | None) -> Path:
    """Repots rot (där `config.toml` ligger): --rot, FLODEN_ROT, aktuell mapp, eller paketets repo."""
    kandidater: list[Path] = []
    if rot:
        kandidater.append(Path(rot).expanduser())
    elif os.environ.get("FLODEN_ROT"):
        kandidater.append(Path(os.environ["FLODEN_ROT"]).expanduser())
    else:
        kandidater += [Path.cwd(), Path(__file__).resolve().parents[2]]
    for k in kandidater:
        if (k / "config.toml").is_file():
            return k.resolve()
    raise McpStartFel(
        "Hittar inte Flodens rotmapp (mappen med config.toml). Ange --rot /sökväg/till/floden "
        "eller miljövariabeln FLODEN_ROT."
    )


def bygg(
    rot: Path, tillat_klass: str | None = None, logg: str | None = None
) -> tuple[Any, Policy, Korlogg, Any]:
    """Bygger (server, policy, logg, konfig) ur repots konfiguration. Ändrar aktuell mapp till `rot`."""
    from floden_mcp.karna_api import FlodenTjanst
    from floden_mcp.server import bygg_server
    from gemensamt.konfig import las_konfig
    from webb.api import FlodenApiApp

    os.chdir(rot)
    konfig = las_konfig(rot / "config.toml", rot / "config.local.toml")
    try:
        policy = Policy.fran_konfig(konfig)
        if tillat_klass:
            standard = frozenset(tolka_klass(k) for k in tillat_klass.split(",") if k.strip())
            policy = Policy(standard, policy.per_klient, policy.projektklasser)
    except PolicyFel as exc:
        raise McpStartFel(f"Ogiltig datapolicy: {exc}") from exc
    app = FlodenApiApp(testsvit_rot=rot / "testsvit", konfig_fil=rot / "config.toml")
    tjanst = FlodenTjanst(app, rot=rot)
    logg_fil = Path(logg or dict(konfig.get("mcp", {})).get("logg", STANDARD_LOGG))
    korlogg = Korlogg(logg_fil if logg_fil.is_absolute() else rot / logg_fil)
    server = bygg_server(tjanst, policy=policy, logg=korlogg)
    return server, policy, korlogg, app


def registrera(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "mcp",
        help="Starta Floden som MCP-server så att en AI kan styra den (stdio, eller --http).",
        description="Floden som MCP-server (Model Context Protocol). Standard är stdio, för "
        "Claude Desktop, Claude Code och Codex. Med --http lyssnar servern på Streamable HTTP "
        "(bara 127.0.0.1 om inloggningen är av). Se docs/MCP.md.",
    )
    p.add_argument("--http", action="store_true", help="Streamable HTTP i stället för stdio.")
    p.add_argument("--host", default="127.0.0.1", help="Adress för --http (standard 127.0.0.1).")
    p.add_argument(
        "--port", type=int, default=STANDARD_PORT, help=f"Port (standard {STANDARD_PORT})."
    )
    p.add_argument("--rot", default=None, help="Flodens rotmapp (där config.toml ligger).")
    p.add_argument(
        "--tillat-klass",
        default=None,
        help="Dataklasser klienter får ta del av, kommaseparerat (standard: oppet).",
    )
    p.add_argument("--logg", default=None, help=f"Körloggens fil (standard: {STANDARD_LOGG}).")


def kor(args: argparse.Namespace) -> int:
    try:
        rot = hitta_rot(args.rot)
        server, policy, _logg, app = bygg(rot, args.tillat_klass, args.logg)
    except McpStartFel as exc:
        print(f"floden mcp: {exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] in ("mcp", "mcp_types"):
            print(
                'floden mcp: MCP-biblioteket saknas. Installera det med: pip install -e ".[mcp]"',
                file=sys.stderr,
            )
            return 2
        raise
    if not args.http:
        print("Floden MCP (stdio) startad.", file=sys.stderr)
        server.run("stdio")
        return 0
    from floden_mcp.http import KontoSkydd, ar_loopback

    konto = app.konto
    if not konto.aktiv and not ar_loopback(args.host):
        print(
            f"floden mcp: inloggningen är av, så servern lyssnar bara på loopback (inte {args.host}). "
            "Slå på inloggning (docs/KONTO.md) eller använd --host 127.0.0.1.",
            file=sys.stderr,
        )
        return 2
    import uvicorn

    asgi = KontoSkydd(server.streamable_http_app(host=args.host), konto)
    print(
        f"Floden MCP (Streamable HTTP) på http://{args.host}:{args.port}/mcp  "
        f"(inloggning: {'på' if konto.aktiv else 'av'}; Ctrl+C avslutar)",
        file=sys.stderr,
    )
    uvicorn.run(asgi, host=args.host, port=args.port, log_level="warning")
    return 0
