# ruff: noqa: E501
"""Monterar MCP:s Streamable HTTP på `/mcp` i webbens ASGI-server (`floden webb --server asgi`).

Samma process och samma `FlodenApiApp` som webben: kontodatabasen, körningarna och cachen delas, och
varje förfrågan går genom `KontoSkydd` (bearer-token eller session). **`/mcp` är av när inloggningen
är av och värden inte är loopback** (som `floden mcp --http`): `montera_mcp` kastar då `McpAvstangd`.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from floden_mcp.http import ASGIApp, KontoSkydd, ar_loopback

MCP_SOKVAG = "/mcp"


class McpAvstangd(Exception):
    """`/mcp` monteras inte. Texten säger varför (visas vid start)."""


@dataclass
class MonteradMcp:
    """Den monterade MCP-appen och dess livscykel (sessionshanteraren måste köras)."""

    app: ASGIApp
    livscykel: Callable[[], AbstractAsyncContextManager[Any]]

    @staticmethod
    def ager(sokvag: str) -> bool:
        return sokvag == MCP_SOKVAG or sokvag.startswith(MCP_SOKVAG + "/")


def montera_mcp(webb_app: Any, rot: Path, *, host: str) -> MonteradMcp:
    """Bygger MCP-servern ovanpå `webb_app` (en `FlodenApiApp`) och lindar den i `KontoSkydd`."""
    from floden_mcp.cli import bygg

    konto = webb_app.konto
    if not konto.aktiv and not ar_loopback(host):
        raise McpAvstangd(
            f"/mcp är av: inloggningen är av och värden är {host}, inte loopback. Slå på inloggning "
            "([konto] inloggning = \"pa\", docs/KONTO.md) för att nå /mcp utifrån."
        )
    server, _policy, _logg, _app = bygg(rot, app=webb_app)
    transport = None
    if konto.aktiv:
        from mcp.server.transport_security import TransportSecuritySettings

        delar = urlsplit(konto.konfig.offentlig_url)
        vard = delar.netloc
        transport = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[vard, f"{vard}:*", "127.0.0.1:*", "localhost:*"],
            allowed_origins=[konto.konfig.origin],
        )
    starlette = server.streamable_http_app(host=host, transport_security=transport)
    return MonteradMcp(
        app=KontoSkydd(starlette, konto),
        livscykel=lambda: starlette.router.lifespan_context(starlette),
    )
