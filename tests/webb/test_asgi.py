"""Tester för ASGI 3.0-adaptern (`src/webb/asgi.py`)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from webb.asgi import FlodenAsgiApp, skapa_asgi_app


async def _anropa_asgi(
    asgi_app: FlodenAsgiApp,
    metod: str,
    path: str,
    *,
    query: bytes = b"",
    headers: Sequence[tuple[bytes, bytes]] = (),
    chunks: Sequence[bytes] = (b"",),
) -> tuple[int, dict[str, str], bytes]:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "2",
        "method": metod,
        "path": path,
        "query_string": query,
        "headers": list(headers),
    }
    chunk_iter = iter(chunks)
    skickade: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        try:
            del_b = next(chunk_iter)
            return {"type": "http.request", "body": del_b, "more_body": True}
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}

    async def send(meddelande: Mapping[str, Any]) -> None:
        skickade.append(dict(meddelande))

    await asgi_app(scope, receive, send)
    start = next(m for m in skickade if m["type"] == "http.response.start")
    kropp = b"".join(m.get("body", b"") for m in skickade if m["type"] == "http.response.body")
    ut_headers = {
        k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start.get("headers", ())
    }
    return int(start["status"]), ut_headers, kropp


class _SpyApiApp:
    """Minimal stub som implementerar `hantera_http` för deterministiska ASGI-tester."""

    def __init__(self, *, fordrojning_s: float = 0.0) -> None:
        self.fordrojning_s = fordrojning_s
        self.anrop: list[tuple[str, str, dict[str, str], bytes]] = []
        self.startad = threading.Event()

    def hantera_http(
        self,
        metod: str,
        sokvag: str,
        rubriker: Mapping[str, str],
        las_kropp: Callable[[], bytes],
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        kropp = las_kropp()
        self.anrop.append((metod, sokvag, dict(rubriker), kropp))
        if metod == "POST" and self.fordrojning_s > 0:
            self.startad.set()
            time.sleep(self.fordrojning_s)
        svar = json.dumps({"ok": True, "cookie": rubriker.get("cookie", "")}).encode("utf-8")
        return 200, [("Content-Type", "application/json; charset=utf-8")], svar


def test_asgi_halsa_och_head(tmp_path: Path) -> None:
    klient = tmp_path / "klient"
    klient.mkdir()
    (klient / "index.html").write_text("<!doctype html><title>Floden</title>", encoding="utf-8")
    asgi_app = skapa_asgi_app(
        testsvit_rot=Path("testsvit"),
        konfig_fil=tmp_path / "saknas.toml",
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        klientmapp=klient,
    )

    async def _kor() -> None:
        status, headers, kropp = await _anropa_asgi(asgi_app, "GET", "/api/halsa")
        assert status == 200
        assert "application/json" in headers["content-type"]
        data = json.loads(kropp.decode("utf-8"))
        assert data["status"] == "ok"

        g_status, _, g_kropp = await _anropa_asgi(asgi_app, "GET", "/")
        assert g_status == 200
        h_status, h_headers, h_kropp = await _anropa_asgi(asgi_app, "HEAD", "/")
        assert h_status == 200
        assert h_kropp == b""
        assert int(h_headers["content-length"]) == len(g_kropp)

    asyncio.run(_kor())


def test_asgi_avvisar_for_stor_kropp_via_header_och_strom() -> None:
    spy = _SpyApiApp()
    asgi_app = FlodenAsgiApp(spy, max_kropp_bytes=16)  # type: ignore[arg-type]

    async def _kor() -> None:
        # 1) Via Content-Length innan kroppen ens läses
        status, _, kropp = await _anropa_asgi(
            asgi_app,
            "POST",
            "/api/analys/bifurkationer",
            headers=[(b"content-length", b"64")],
            chunks=(b"x" * 64,),
        )
        assert status == 413
        assert json.loads(kropp)["status_kod"] == 413
        assert len(spy.anrop) == 0

        # 2) Utan Content-Length (chunked-ström) som överstiger maxgränsen
        status2, _, kropp2 = await _anropa_asgi(
            asgi_app,
            "POST",
            "/api/analys/bifurkationer",
            chunks=(b"a" * 10, b"b" * 10),
        )
        assert status2 == 413
        assert json.loads(kropp2)["status_kod"] == 413
        assert len(spy.anrop) == 0

    asyncio.run(_kor())


def test_asgi_http2_cookie_sammanslagning_och_query() -> None:
    spy = _SpyApiApp()
    asgi_app = FlodenAsgiApp(spy)  # type: ignore[arg-type]

    async def _kor() -> None:
        status, _, kropp = await _anropa_asgi(
            asgi_app,
            "GET",
            "/api/omraden",
            query=b"sort=namn",
            headers=[
                (b"cookie", b"a=1"),
                (b"cookie", b"floden_session=xyz"),
                (b"x-test", b"ett"),
                (b"x-test", b"tva"),
            ],
        )
        assert status == 200
        data = json.loads(kropp)
        assert data["cookie"] == "a=1; floden_session=xyz"
        assert spy.anrop[0][1] == "/api/omraden?sort=namn"
        assert spy.anrop[0][2]["x-test"] == "ett, tva"

    asyncio.run(_kor())


def test_asgi_samtidighetsgrans_ger_503_men_blockerar_inte_get() -> None:
    spy = _SpyApiApp(fordrojning_s=0.15)
    asgi_app = FlodenAsgiApp(
        spy,  # type: ignore[arg-type]
        max_samtidiga_berakningar=1,
        ko_timeout_s=0.02,
    )

    async def _kor() -> None:
        tung1 = asyncio.create_task(
            _anropa_asgi(asgi_app, "POST", "/api/simulera", chunks=(b"{}",))
        )
        while not spy.startad.is_set():
            await asyncio.sleep(0.005)

        # Samtidig GET ska inte blockeras av beräkningssemaforen
        get_status, _, _ = await _anropa_asgi(asgi_app, "GET", "/api/halsa")
        assert get_status == 200

        # Andra samtidiga tunga POST ska avvisas med 503 + Retry-After
        p2_status, p2_headers, p2_kropp = await _anropa_asgi(
            asgi_app, "POST", "/api/simulera", chunks=(b"{}",)
        )
        assert p2_status == 503
        assert p2_headers["retry-after"] == "5"
        assert json.loads(p2_kropp)["status_kod"] == 503

        p1_status, _, _ = await tung1
        assert p1_status == 200

    asyncio.run(_kor())


def test_asgi_lifespan() -> None:
    spy = _SpyApiApp()
    asgi_app = FlodenAsgiApp(spy)  # type: ignore[arg-type]

    async def _kor() -> None:
        in_meddelanden = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        ut: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return next(in_meddelanden)

        async def send(m: Mapping[str, Any]) -> None:
            ut.append(dict(m))

        await asgi_app({"type": "lifespan"}, receive, send)
        assert ut == [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ]

    asyncio.run(_kor())
