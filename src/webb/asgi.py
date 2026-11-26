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
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
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


def las_max_samtidiga_berakningar(varde: int | None = None) -> int:
    """Läser max antal samtidiga tunga beräkningar: argument > miljövariabel > standard (4)."""
    if varde is not None:
        if isinstance(varde, bool) or int(varde) <= 0:
            raise ValueError("max_samtidiga_berakningar måste vara > 0.")
        return int(varde)
    raw = os.environ.get("FLODEN_MAX_SAMTIDIGA_BERAKNINGAR")
    if raw is None or not raw.strip():
        return STANDARD_MAX_SAMTIDIGA_BERAKNINGAR
    try:
        antal = int(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"Ogiltigt värde i FLODEN_MAX_SAMTIDIGA_BERAKNINGAR={raw!r}: ange ett positivt heltal."
        ) from exc
    if antal <= 0:
        raise ValueError(
            f"Ogiltigt värde i FLODEN_MAX_SAMTIDIGA_BERAKNINGAR={raw!r}: måste vara > 0."
        )
    return antal


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


KLIENT_IP_RUBRIK = "x-floden-klient-ip"


def _klient_ip(scope: Mapping[str, Any], rubriker: Mapping[str, str]) -> str:
    """Klientens adress för hastighetsgränser (OAuth). En inkommande `X-Floden-Klient-Ip` räknas
    aldrig. Kommer anropet från en lokal eller privat adress (Caddy på Docker-nätet) används den
    sista adressen i `X-Forwarded-For` (Caddy skriver över den med den verkliga adressen)."""
    import ipaddress

    klient = scope.get("client")
    direkt = str(klient[0]) if klient else "okand"
    try:
        adr = ipaddress.ip_address(direkt)
    except ValueError:
        return direkt
    if adr.is_loopback or adr.is_private:
        fran = rubriker.get("x-forwarded-for", "").split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(fran))
        except ValueError:
            return direkt
    return direkt


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
        max_samtidiga_berakningar: int | None = None,
        ko_timeout_s: float = STANDARD_KO_TIMEOUT_S,
        mcp: Any = None,
    ) -> None:
        if max_kropp_bytes <= 0:
            raise ValueError("max_kropp_bytes måste vara > 0.")
        eff_max_samtidiga = las_max_samtidiga_berakningar(max_samtidiga_berakningar)
        if ko_timeout_s < 0.0:
            raise ValueError("ko_timeout_s måste vara >= 0.")
        self.app = app
        self.mcp = mcp
        """`floden_mcp.montera.MonteradMcp` eller None: MCP:s Streamable HTTP på `/mcp`."""
        self.max_kropp_bytes = int(max_kropp_bytes)
        self.max_samtidiga_berakningar = eff_max_samtidiga
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

    _TUNGA_EXAKTA: frozenset[str] = frozenset(
        {"/api/kor", "/api/risk", "/api/rapport", "/api/rutt", "/api/projekt"}
    )
    _LATTA_ANALYS_POST: frozenset[str] = frozenset({"/api/analys/gyf"})

    @staticmethod
    def _ar_tung_berakning(metod: str, sokvag: str) -> bool:
        """Avgör om en HTTP-förfrågan är en tung beräknings- eller rapport-POST.

        Snabba UI-POST:er (t.ex. `validera`, `sektion`, `gyf`, spara/döp om/ta bort scenario,
        `plan`, WMS-lager, projektinställningar) samt alla `GET`-läsningar hålls utanför
        semaforen så att de inte svälts ut av samtidiga tunga beräkningar.
        """
        if metod != "POST":
            return False
        ren = sokvag.rstrip("/") or "/"
        if ren in FlodenAsgiApp._LATTA_ANALYS_POST:
            return False
        if ren in FlodenAsgiApp._TUNGA_EXAKTA or ren.startswith("/api/analys/"):
            return True
        delar = [d for d in ren.split("/") if d]
        # POST /api/projekt/<id>/rapporter
        if len(delar) == 4 and delar[:2] == ["api", "projekt"] and delar[3] == "rapporter":
            return True
        # POST /api/fragor/<ref>/kor
        if len(delar) == 4 and delar[:2] == ["api", "fragor"] and delar[3] == "kor":
            return True
        # POST /api/kartverktyg/<projekt>/lage-idag/kor
        if (
            len(delar) == 5
            and delar[:2] == ["api", "kartverktyg"]
            and delar[3:] == ["lage-idag", "kor"]
        ):
            return True
        # POST /api/kartverktyg/<projekt>/scenarier/<slug>/kor
        return (
            len(delar) == 6
            and delar[:2] == ["api", "kartverktyg"]
            and delar[3] == "scenarier"
            and delar[5] == "kor"
        )

    async def _las_kropp(
        self,
        receive: AsgiReceive,
        rubriker: Mapping[str, str],
    ) -> tuple[bytes | None, int | None, str | None]:
        """Läser förfrågningskropp med hård bytegräns.

        Returnerar `(kropp, None, None)` vid framgång, `(None, status, feltext)` vid
        validerings-/storleksfel, eller `(None, None, None)` om klienten kopplade ner
        (`http.disconnect`) innan kroppen var färdigläst.
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
                return None, None, None
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
        stack = AsyncExitStack()
        while True:
            meddelande = await receive()
            typ = meddelande.get("type")
            if typ == "lifespan.startup":
                try:
                    # MCP:s sessionshanterare måste köra så länge servern lever.
                    if self.mcp is not None:
                        await stack.enter_async_context(self.mcp.livscykel())
                except Exception as exc:  # noqa: BLE001 - startfel rapporteras till servern
                    await send(
                        {"type": "lifespan.startup.failed", "message": f"MCP: {type(exc).__name__}"}
                    )
                    return
                await send({"type": "lifespan.startup.complete"})
            elif typ == "lifespan.shutdown":
                try:
                    await stack.aclose()
                finally:
                    stang_app = getattr(self.app, "stang", None)
                    if callable(stang_app):
                        await asyncio.to_thread(stang_app)
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

        if self.mcp is not None and self.mcp.ager(str(scope.get("path", "/"))):
            # MCP har egen kroppsgräns och sin egen inloggningskontroll (KontoSkydd); strömmen
            # (SSE) får inte läsas in eller buffras här.
            await self.mcp.app(scope, receive, send)
            return

        metod = str(scope.get("method", "GET")).upper()
        sokvag = str(scope.get("path", "/"))
        query_b = bytes(scope.get("query_string", b"") or b"")
        full_sokvag = f"{sokvag}?{query_b.decode('latin-1')}" if query_b else sokvag
        rubriker = _sammanfoga_rubriker(scope.get("headers", ()))
        rubriker[KLIENT_IP_RUBRIK] = _klient_ip(scope, rubriker)
        huvud = metod == "HEAD"

        kropp_in: bytes = b""
        if metod in {"POST", "PUT", "PATCH"}:
            kropp_opt, fel_status, fel_text = await self._las_kropp(receive, rubriker)
            if fel_status is not None:
                await _skicka_fel(
                    send,
                    fel_status,
                    fel_text or "Ogiltig förfrågningskropp.",
                    huvud=huvud,
                )
                return
            if kropp_opt is None:
                # Klienten avbröt förbindelsen (`http.disconnect`) innan kroppen levererades.
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
    testsvit_rot: str | Path | None = None,
    konfig_fil: str | Path = "config.toml",
    fragemappar: Sequence[str | Path] | None = None,
    cachemapp: str | Path | None = None,
    korningsmapp: str | Path | None = None,
    klientmapp: str | Path | None = None,
    konto: KontoAdapter | None = None,
    max_kropp_bytes: int = STANDARD_MAX_KROPP_BYTES,
    max_samtidiga_berakningar: int | None = None,
    ko_timeout_s: float = STANDARD_KO_TIMEOUT_S,
    mcp_host: str | None = None,
) -> FlodenAsgiApp:
    """Skapar en produktionsklar ASGI-app för t.ex. `uvicorn --factory`.

    Med `mcp_host` (adressen servern lyssnar på) monteras MCP:s Streamable HTTP på `/mcp` i samma
    process (`floden_mcp.montera`). Är inloggningen av och värden inte loopback monteras den inte
    (`McpAvstangd`)."""
    api_app = FlodenApiApp(
        testsvit_rot=testsvit_rot,
        konfig_fil=konfig_fil,
        fragemappar=fragemappar,
        cachemapp=cachemapp,
        korningsmapp=korningsmapp,
        konto=konto,
        klientmapp=klientmapp,
    )
    monterad = None
    if mcp_host is not None:
        from floden_mcp.montera import montera_mcp

        rot = Path(konfig_fil).resolve().parent
        monterad = montera_mcp(api_app, rot, host=mcp_host)
    return FlodenAsgiApp(
        api_app,
        max_kropp_bytes=max_kropp_bytes,
        max_samtidiga_berakningar=max_samtidiga_berakningar,
        ko_timeout_s=ko_timeout_s,
        mcp=monterad,
    )


__all__ = [
    "FlodenAsgiApp",
    "STANDARD_KO_TIMEOUT_S",
    "STANDARD_MAX_KROPP_BYTES",
    "STANDARD_MAX_SAMTIDIGA_BERAKNINGAR",
    "las_max_samtidiga_berakningar",
    "skapa_asgi_app",
]
