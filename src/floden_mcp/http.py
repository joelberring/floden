# ruff: noqa: E501
"""Streamable HTTP för MCP-servern, bakom samma inloggning som webben.

`KontoSkydd` är en ASGI-middleware som ställer varje förfrågan mot kontosystemet
(`konto.KontoAdapter.kraver_inloggning`, se `docs/KONTO.md`) innan MCP-servern ser den. Med
`inloggning = "av"` (standard) släpps allt igenom, och servern vägrar då att lyssna på något annat än
loopback. Med inloggning på krävs på varje anrop antingen en personlig MCP-token
(`Authorization: Bearer fl_mcp_...`, ingen CSRF) eller en giltig session (kakan, och CSRF-huvud för
POST); kontots id och tokenets id (aldrig namn, e-post eller token) följer med till körloggen.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import parse_qs

from konto import Forfragan, KontoAdapter

logg = logging.getLogger("konto")

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
    """ASGI-middleware: släpper bara igenom förfrågningar som kontosystemet tillåter.

    Två sätt att legitimera sig när inloggning är på:

    - **Bearer** (`Authorization: Bearer fl_mcp_...`): en personlig MCP-token (`konto.mcptoken`).
      Ingen CSRF (inget ambient credential som en webbläsare kan skicka åt en angripare). Token
      kontrolleras mot databasen vid varje anrop, så en återkallelse eller spärr gäller direkt.
      Har förfrågan en bearer-rubrik avgör den ensam: en ogiltig token faller aldrig tillbaka på
      en kaka.
    - **Session-kaka** som webben, med CSRF-huvud för POST.

    Kontots id och tokenets id (aldrig token, namn eller e-post) läggs i `scope` och följer med till
    körloggen. Ett nekande ger 401 med `WWW-Authenticate: Bearer ...` (RFC 6750, MCP-auktoriseringen).
    En MCP-session (`Mcp-Session-Id`) binds till kontot som öppnade den: ett annat konto som
    känner till id:t nekas.
    """

    MAX_BUNDNA_SESSIONER = 2000

    def __init__(self, app: ASGIApp, konto: KontoAdapter) -> None:
        self.app = app
        self.konto = konto
        self._sessioner: dict[str, str] = {}  # Mcp-Session-Id -> konto-id

    def www_authenticate(self, fel: str | None = None) -> str:
        """`WWW-Authenticate` för ett 401. `fel` är `invalid_token` när en token skickades."""
        delar = ['realm="floden"']
        url = self.konto.resurs_metadata_url()
        if url:
            delar.append(f'resource_metadata="{url}"')
        if fel:
            delar.append(f'error="{fel}"')
        return "Bearer " + ", ".join(delar)

    async def _neka(
        self, send: Send, status: int, kod: str, text: str, extra: list[tuple[str, str]]
    ) -> None:
        kropp = json.dumps({"fel": text, "status": status, "kod": kod}).encode("utf-8")
        huvud = [
            ("content-type", "application/json; charset=utf-8"),
            ("cache-control", "no-store"),
            ("content-length", str(len(kropp))),
            *extra,
        ]
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in huvud],
            }
        )
        await send({"type": "http.response.body", "body": kropp})

    def _bunden_till_annat_konto(self, sid: str | None, konto_id: str | None) -> bool:
        return bool(sid and konto_id and self._sessioner.get(sid, konto_id) != konto_id)

    def _binda(self, sid: str | None, konto_id: str | None) -> None:
        if not sid or not konto_id or sid in self._sessioner:
            return
        if len(self._sessioner) >= self.MAX_BUNDNA_SESSIONER:
            self._sessioner.pop(next(iter(self._sessioner)))
        self._sessioner[sid] = konto_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rubriker: dict[str, str] = {}
        for k_b, v_b in scope["headers"]:
            kl = k_b.decode("latin-1").lower()
            v = v_b.decode("latin-1")
            if kl in rubriker:
                sep = "; " if kl == "cookie" else ", "
                rubriker[kl] = f"{rubriker[kl]}{sep}{v}"
            else:
                rubriker[kl] = v
        konto_id: str | None = None
        token_id: str | None = None
        auth = rubriker.get("authorization")
        if auth is not None and self.konto.aktiv:
            schema, _, varde = auth.partition(" ")
            traff = (
                await asyncio.to_thread(self.konto.kontrollera_bearer, varde.strip())
                if schema.lower() == "bearer"
                else None
            )
            if traff is None:
                logg.info("mcp anrop %s -> 401 ogiltig bearer (prefix=%s)", scope["method"], varde.strip()[:7])
                # Aldrig token, rubrik eller orsak i svaret.
                await self._neka(
                    send,
                    401,
                    "ogiltig-token",
                    "Ogiltig, återkallad eller utgången token",
                    [("www-authenticate", self.www_authenticate("invalid_token"))],
                )
                return
            konto_id, token_id = traff
        else:
            req = Forfragan(
                metod=str(scope["method"]).upper(),
                sokvag=str(scope["path"]),
                query=parse_qs(scope.get("query_string", b"").decode("latin-1")),
                headers=rubriker,
            )
            kontroll = await asyncio.to_thread(self.konto.kraver_inloggning, req)
            if not kontroll.tillaten:
                assert kontroll.svar is not None  # noqa: S101 - nekad => svar finns
                status, huvud, kropp = kontroll.svar
                extra = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in huvud]
                extra.append((b"content-length", str(len(kropp)).encode("ascii")))
                logg.info("mcp anrop %s -> %s utan bearer", scope["method"], status)
                if status == 401:
                    extra.append((b"www-authenticate", self.www_authenticate().encode("latin-1")))
                await send({"type": "http.response.start", "status": status, "headers": extra})
                await send({"type": "http.response.body", "body": kropp})
                return
            if kontroll.inloggad is not None:
                konto_id = kontroll.inloggad.konto_id
        sid = rubriker.get("mcp-session-id")
        if self._bunden_till_annat_konto(sid, konto_id):
            await self._neka(send, 404, "okand-session", "Okänd session", [])
            return
        if konto_id is not None:
            scope["floden_konto"] = konto_id
        if token_id is not None:
            scope["floden_token"] = token_id
        if konto_id is None:
            await self.app(scope, receive, send)
            return

        async def send_med_bindning(meddelande: MutableMapping[str, Any]) -> None:
            if meddelande.get("type") == "http.response.start" and meddelande.get("status") == 200:
                for k_b, v_b in meddelande.get("headers", []):
                    if bytes(k_b).lower() == b"mcp-session-id":
                        self._binda(bytes(v_b).decode("latin-1"), konto_id)
            await send(meddelande)

        await self.app(scope, receive, send_med_bindning)
