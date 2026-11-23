"""ASGI 3.0-adapter för Flodens webb-API (T10 / produktionsdrift).

Gör det möjligt att köra `FlodenApiApp` bakom en riktig produktionsserver
(t.ex. `uvicorn` eller `hypercorn`) med:

* strikt storleksgräns på inkommande HTTP-kroppar (både via `Content-Length`
  och vid strömmad chunked-överföring → HTTP 413 innan minnet fylls),
* HTTP/2-korrekt sammanslagning av upprepade `cookie`-rubriker (`; `),
* samtidighetsbegränsning (`max_samtidiga_berakningar`) för tunga
  `POST /api/...`-beräkningar så att en minnesbegränsad VPS inte drabbas av
  OOM (returnerar HTTP 503 + `Retry-After` vid överlast),
* exekvering av synkrona fysikberäkningar i arbetstrådar (`asyncio.to_thread`)
  så att hälso- och statiska anrop aldrig blockeras.

Modulen har inga externa beroenden utöver Python-standardbiblioteket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from konto import KontoAdapter
from webb.api import FlodenApiApp

AsgiScope = Mapping[str, Any]
AsgiMessage = Mapping[str, Any]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]

STANDARD_MAX_KROPP_BYTES = 25 * 1024 * 1024
STANDARD_MAX_SAMTIDIGA_BERAKNINGAR = 4
STANDARD_KO_TIMEOUT_S = 0.25


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _sammanfoga_rubriker(
    raa_rubriker: Sequence[tuple[bytes, bytes]],
) -> dict[str, str]:
    """Konverterar ASGI:s `(bytes, bytes)`-rubriker till HTTP-dict.

    Upprepade `cookie`-rubriker (vanligt över HTTP/2) sammanfogas med `"; "`,
    övriga rubriker med `", "` enligt RFC 9110.
    """
    rubriker: dict[str, str] = {}
    for nyckel_b, varde_b in raa_rubriker:
        nyckel = nyckel_b.decode("latin-1").lower()
        varde = varde_b.decode("latin-1")
        if nyckel in rubriker:
            sep = "; " if nyckel == "cookie" else ", "
            rubriker[nyckel] = f"{rubriker[nyckel]}{sep}{varde}"
        else:
            rubriker[nyckel] = varde
    return rubriker


async def _skicka_svar(
    send: AsgiSend,
    status: int,
    headers: Sequence[tuple[str, str]],
    kropp: bytes,
    *,
    huvud: bool = False,
) -> None:
    ut_rubriker: list[tuple[bytes, bytes]] = [
        (nyckel.lower().encode("latin-1"), varde.encode("latin-1")) for nyckel, varde in headers
    ]
    if not any(n == b"content-length" for n, _ in ut_rubriker):
        ut_rubriker.append((b"content-length", str(len(kropp)).encode("ascii")))
    await send(
        {
            "type": "http.response.start",
            "status": int(status),
            "headers": ut_rubriker,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"" if huvud else kropp,
            "more_body": False,
        }
    )


async def _skicka_fel(
    send: AsgiSend,
    status: int,
    meddelande: str,
    *,
    extra_rubriker: Sequence[tuple[str, str]] = (),
    huvud: bool = False,
) -> None:
    kropp = _json_bytes({"fel": meddelande, "status_kod": status})
    headers: list[tuple[str, str]] = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(kropp))),
        ("Cache-Control", "no-store"),
        *extra_rubriker,
    ]
    await _skicka_svar(send, status, headers, kropp, huvud=huvud)


class FlodenAsgiApp:
    """ASGI 3.0-applikation som lindar `FlodenApiApp` för produktionsdrift."""

    def __init__(
        self,
        app: FlodenApiApp,
        *,
        max_kropp_bytes: int = STANDARD_MAX_KROPP_BYTES,
        max_samtidiga_berakningar: int = STANDARD_MAX_SAMTIDIGA_BERAKNINGAR,
        ko_timeout_s: float = STANDARD_KO_TIMEOUT_S,
    ) -> None:
        if max_kropp_bytes <= 0:
            raise ValueError("max_kropp_bytes måste vara > 0.")
        if max_samtidiga_berakningar <= 0:
            raise ValueError("max_samtidiga_berakningar måste vara > 0.")
        if ko_timeout_s < 0.0:
            raise ValueError("ko_timeout_s måste vara >= 0.")
        self.app = app
        self.max_kropp_bytes = int(max_kropp_bytes)
        self.max_samtidiga_berakningar = int(max_samtidiga_berakningar)
        self.ko_timeout_s = float(ko_timeout_s)
        self._semafor_per_loop: dict[int, asyncio.Semaphore] = {}

    def _semafor(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        nyckel = id(loop)
        sem = self._semafor_per_loop.get(nyckel)
        if sem is None:
            sem = asyncio.Semaphore(self.max_samtidiga_berakningar)
            self._semafor_per_loop[nyckel] = sem
        return sem

    @staticmethod
    def _ar_tung_berakning(metod: str, sokvag: str) -> bool:
        return metod == "POST" and sokvag.startswith("/api/")

    async def _las_kropp(
        self,
        receive: AsgiReceive,
        rubriker: Mapping[str, str],
    ) -> tuple[bytes | None, int | None, str | None]:
        """Läser förfrågningskropp med hård bytegräns.

        Returnerar `(kropp, None, None)` vid framgång, eller `(None, status, feltext)`.
        """
        raw_len = rubriker.get("content-length")
        if raw_len is not None and raw_len != "":
            try:
                langd = int(raw_len)
            except ValueError:
                return None, 400, f"Ogiltig Content-Length: {raw_len!r}"
            if langd < 0 or langd > self.max_kropp_bytes:
                return (
                    None,
                    413,
                    f"Förfrågningskropp överstiger maxgränsen ({self.max_kropp_bytes} byte).",
                )

        delar: list[bytes] = []
        totalt = 0
        while True:
            meddelande = await receive()
            typ = meddelande.get("type")
            if typ == "http.disconnect":
                return b"", None, None
            if typ != "http.request":
                break
            del_bytes = bytes(meddelande.get("body", b"") or b"")
            totalt += len(del_bytes)
            if totalt > self.max_kropp_bytes:
                return (
                    None,
                    413,
                    f"Förfrågningskropp överstiger maxgränsen ({self.max_kropp_bytes} byte).",
                )
            if del_bytes:
                delar.append(del_bytes)
            if not meddelande.get("more_body", False):
                break
        return b"".join(delar), None, None

    async def _hantera_lifespan(self, receive: AsgiReceive, send: AsgiSend) -> None:
        while True:
            meddelande = await receive()
            typ = meddelande.get("type")
            if typ == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif typ == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(
        self,
        scope: AsgiScope,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        scope_typ = scope.get("type")
        if scope_typ == "lifespan":
            await self._hantera_lifespan(receive, send)
            return
        if scope_typ != "http":
            raise ValueError(f"ASGI-scopetyp stöds inte: {scope_typ!r}")

        metod = str(scope.get("method", "GET")).upper()
        sokvag = str(scope.get("path", "/"))
        query_b = bytes(scope.get("query_string", b"") or b"")
        full_sokvag = f"{sokvag}?{query_b.decode('latin-1')}" if query_b else sokvag
        rubriker = _sammanfoga_rubriker(scope.get("headers", ()))
        huvud = metod == "HEAD"

        kropp_in: bytes = b""
        if metod in {"POST", "PUT", "PATCH"}:
            kropp_opt, fel_status, fel_text = await self._las_kropp(receive, rubriker)
            if fel_status is not None or kropp_opt is None:
                await _skicka_fel(
                    send,
                    fel_status or 400,
                    fel_text or "Ogiltig förfrågningskropp.",
                    huvud=huvud,
                )
                return
            kropp_in = kropp_opt

        def _hamta_kropp() -> bytes:
            return kropp_in

        if self._ar_tung_berakning(metod, sokvag):
            sem = self._semafor()
            try:
                await asyncio.wait_for(sem.acquire(), timeout=self.ko_timeout_s)
            except TimeoutError:
                await _skicka_fel(
                    send,
                    503,
                    "Servern kör redan maximalt antal samtidiga beräkningar. "
                    "Försök igen om en stund.",
                    extra_rubriker=(("Retry-After", "5"),),
                    huvud=huvud,
                )
                return
            try:
                status, ut_headers, ut_kropp = await asyncio.to_thread(
                    self.app.hantera_http,
                    metod,
                    full_sokvag,
                    rubriker,
                    _hamta_kropp,
                )
            finally:
                sem.release()
        else:
            status, ut_headers, ut_kropp = await asyncio.to_thread(
                self.app.hantera_http,
                metod,
                full_sokvag,
                rubriker,
                _hamta_kropp,
            )

        await _skicka_svar(send, status, ut_headers, ut_kropp, huvud=huvud)


def skapa_asgi_app(
    *,
    testsvit_rot: str | Path = "testsvit",
    konfig_fil: str | Path = "config.toml",
    fragemappar: Sequence[str | Path] | None = None,
    cachemapp: str | Path | None = None,
    korningsmapp: str | Path | None = None,
    klientmapp: str | Path | None = None,
    konto: KontoAdapter | None = None,
    max_kropp_bytes: int = STANDARD_MAX_KROPP_BYTES,
    max_samtidiga_berakningar: int = STANDARD_MAX_SAMTIDIGA_BERAKNINGAR,
    ko_timeout_s: float = STANDARD_KO_TIMEOUT_S,
) -> FlodenAsgiApp:
    """Skapar en produktionsklar ASGI-app för t.ex. `uvicorn --factory`."""
    api_app = FlodenApiApp(
        testsvit_rot=testsvit_rot,
        konfig_fil=konfig_fil,
        fragemappar=fragemappar,
        cachemapp=cachemapp,
        korningsmapp=korningsmapp,
        konto=konto,
        klientmapp=klientmapp,
    )
    return FlodenAsgiApp(
        api_app,
        max_kropp_bytes=max_kropp_bytes,
        max_samtidiga_berakningar=max_samtidiga_berakningar,
        ko_timeout_s=ko_timeout_s,
    )


__all__ = [
    "FlodenAsgiApp",
    "STANDARD_KO_TIMEOUT_S",
    "STANDARD_MAX_KROPP_BYTES",
    "STANDARD_MAX_SAMTIDIGA_BERAKNINGAR",
    "skapa_asgi_app",
]
