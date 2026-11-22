# ruff: noqa: E501
"""Streamable HTTP för MCP-servern, bakom samma inloggning som webben.

`KontoSkydd` är en ASGI-middleware som ställer varje förfrågan mot kontosystemet
(`konto.KontoAdapter.kraver_inloggning`, se `docs/KONTO.md`) innan MCP-servern ser den. Med
`inloggning = "av"` (standard) släpps allt igenom, och servern vägrar då att lyssna på något annat än
loopback. Med inloggning på krävs en giltig session (kakan, och CSRF-huvud för POST) på varje anrop;
kontots id (aldrig namn eller e-post) följer med till körloggen.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import parse_qs

from konto import Forfragan, KontoAdapter

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def ar_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class KontoSkydd:
    """ASGI-middleware: släpper bara igenom förfrågningar som kontosystemet tillåter."""

    def __init__(self, app: ASGIApp, konto: KontoAdapter) -> None:
        self.app = app
        self.konto = konto

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rubriker = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        req = Forfragan(
            metod=str(scope["method"]).upper(),
            sokvag=str(scope["path"]),
            query=parse_qs(scope.get("query_string", b"").decode("latin-1")),
            headers=rubriker,
        )
        kontroll = self.konto.kraver_inloggning(req)
        if not kontroll.tillaten:
            assert kontroll.svar is not None  # noqa: S101 - nekad => svar finns
            status, huvud, kropp = kontroll.svar
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in huvud
                    ]
                    + [(b"content-length", str(len(kropp)).encode("ascii"))],
                }
            )
            await send({"type": "http.response.body", "body": kropp})
            return
        if kontroll.inloggad is not None:
            scope["floden_konto"] = kontroll.inloggad.konto_id
        await self.app(scope, receive, send)
