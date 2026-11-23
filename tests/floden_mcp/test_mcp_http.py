# ruff: noqa: E501
"""`/mcp` bakom kontoskyddet: bearer (personlig MCP-token) och kaka, utan nätverk.

Tre nivåer: `KontoSkydd` ensam (statuskoder och rubriker), hela stacken med Starlettes
`TestClient` (MCP-protokollet över Streamable HTTP, körlogg) och sessionsbindningen.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import anyio
import pytest
from starlette.testclient import TestClient

from floden_mcp.dataklass import Policy
from floden_mcp.http import KontoSkydd
from floden_mcp.korlogg import Korlogg
from floden_mcp.server import bygg_server
from tests.demovarld.test_demo import Demo
from tests.konto.conftest import ANNA, ORIGIN, Miljo, Webblasare
from tests.konto.test_inloggning import bjud_in_anna
from tests.konto.test_nycklar import _inloggad, _skriv
from tests.webb.test_inloggning import _app

BO = {"sub": "sub-bo-x2", "email": "bo@example.test", "email_verified": True, "name": "Bo Exempel"}


def _skapa_token(webb: Webblasare, namn: str = "Laptop") -> dict[str, Any]:
    status, svar = _skriv(webb, "POST", "/konto/mcp-token", {"namn": namn})
    assert status == 201
    return svar


def _kor_skydd(
    miljo: Miljo, metod: str, headers: dict[str, str]
) -> tuple[int, dict[str, str], bytes, dict[str, Any]]:
    """Kör `KontoSkydd` ensam mot en inre app som svarar 200. Returnerar status, rubriker, kropp, scope."""
    ut: dict[str, Any] = {"huvud": {}, "kropp": b"", "scope": None, "status": None}

    async def inre(scope: Any, receive: Any, send: Any) -> None:
        ut["scope"] = dict(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    skydd = KontoSkydd(inre, miljo.adapter)

    async def kor() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m: MutableMapping[str, Any]) -> None:
            if m["type"] == "http.response.start":
                ut["status"] = m["status"]
                ut["huvud"] = {k.decode().lower(): v.decode() for k, v in m["headers"]}
            else:
                ut["kropp"] += m.get("body", b"")

        h = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        await skydd(
            {"type": "http", "method": metod, "path": "/mcp", "query_string": b"", "headers": h},
            receive,
            send,
        )

    anyio.run(kor)
    return ut["status"], ut["huvud"], ut["kropp"], ut["scope"]


def test_bearer_ger_konto_och_token_id_utan_csrf(miljo: Miljo) -> None:
    webb, konto_id = _inloggad(miljo)
    ny = _skapa_token(webb)
    for metod in ("GET", "POST", "DELETE"):  # POST och DELETE utan CSRF-huvud, kaka eller Origin
        status, _, _, scope = _kor_skydd(miljo, metod, {"Authorization": f"Bearer {ny['token']}"})
        assert status == 200, metod
        assert scope["floden_konto"] == konto_id and scope["floden_token"] == ny["id"]
    # Schemat är skiftlägesokänsligt (RFC 7235).
    assert _kor_skydd(miljo, "POST", {"Authorization": f"bearer {ny['token']}"})[0] == 200


def test_fel_token_ger_401_med_ratt_header_och_ingen_token_i_svaret(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    ny = _skapa_token(webb)
    fel = ny["token"][:-1] + ("A" if ny["token"][-1] != "A" else "B")
    for auth in (f"Bearer {fel}", "Bearer fl_mcp_kort", "Bearer ", "Bearer", "Basic abc", f"Token {ny['token']}", "Bearer nagot-helt-annat"):
        status, huvud, kropp, scope = _kor_skydd(miljo, "POST", {"Authorization": auth})
        assert status == 401, auth
        assert scope is None  # den inre appen nåddes aldrig
        wa = huvud["www-authenticate"]
        assert wa.startswith("Bearer ") and 'realm="floden"' in wa and 'error="invalid_token"' in wa
        assert json.loads(kropp)["kod"] == "ogiltig-token"
        for hemligt in (fel, ny["token"], "fl_mcp_kort", "abc"):
            assert hemligt.encode() not in kropp and hemligt not in wa


def test_utan_inloggning_ger_401_med_www_authenticate_utan_fel(miljo: Miljo) -> None:
    status, huvud, _, _ = _kor_skydd(miljo, "POST", {})
    assert status == 401
    assert huvud["www-authenticate"].startswith("Bearer ") and "error=" not in huvud["www-authenticate"]


def test_aterkallad_och_sparrad_token_nekas(miljo: Miljo) -> None:
    webb, konto_id = _inloggad(miljo)
    a, b = _skapa_token(webb, "a"), _skapa_token(webb, "b")
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {a['token']}"})[0] == 200
    assert _skriv(webb, "DELETE", f"/konto/mcp-token/{a['id']}")[0] == 200
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {a['token']}"})[0] == 401
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {b['token']}"})[0] == 200
    assert miljo.adapter.sessioner is not None
    miljo.adapter.sessioner.sparra_konto(konto_id)  # spärrat konto: även den andra token nekas
    status, huvud, _, _ = _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {b['token']}"})
    assert status == 401 and "invalid_token" in huvud["www-authenticate"]


def test_ogiltig_bearer_faller_inte_tillbaka_pa_en_giltig_kaka(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    kaka = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
    assert _kor_skydd(miljo, "GET", {"Cookie": kaka})[0] == 200
    assert _kor_skydd(miljo, "GET", {"Cookie": kaka, "Authorization": "Bearer fl_mcp_" + "A" * 43})[0] == 401
    # Kakan kräver fortfarande CSRF för POST, som förut.
    assert _kor_skydd(miljo, "POST", {"Cookie": kaka, "Origin": ORIGIN})[0] == 403


def test_en_mcp_token_ger_inget_i_webbens_api(miljo: Miljo, tmp_path: Path) -> None:
    webb, _ = _inloggad(miljo)
    ny = _skapa_token(webb)
    app = _app(tmp_path, miljo.adapter)
    for metod, sokvag in (("GET", "/api/projekt"), ("GET", "/api/moduler"), ("POST", "/api/analys/bifurkationer")):
        status, _, _ = app.hantera_http(
            metod, sokvag, {"Authorization": f"Bearer {ny['token']}", "Origin": ORIGIN}, lambda: b"{}"
        )[0:3]
        assert status == 401, sokvag


# ------------------------------------------------------------------ hela stacken: protokoll, körlogg, session


class Stack:
    def __init__(
        self,
        demo: Demo,
        miljo: Miljo,
        rot: Path,
        *,
        behorighet: bool = False,
        policy: Policy | None = None,
    ) -> None:
        from tests.floden_mcp.test_mcp import Mcp

        self.mcp = Mcp(demo, rot, konto=miljo.adapter)
        self.logg = Korlogg(rot / "mcp" / "korlogg.jsonl")
        self.server = bygg_server(
            self.mcp.tjanst,
            policy=policy or Policy(),
            logg=self.logg,
            miljo={},
            behorighet=miljo.adapter.har_roll if behorighet else None,
        )
        self.skydd = KontoSkydd(self.server.streamable_http_app(host="0.0.0.0"), miljo.adapter)  # noqa: S104


def _rpc(klient: TestClient, metod: str, params: dict[str, Any], rubriker: dict[str, str], id_: int = 1) -> Any:
    return klient.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": id_, "method": metod, "params": params},
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **rubriker},
    )


def _data(svar: Any) -> dict[str, Any]:
    text = svar.text
    for rad in text.splitlines():
        if rad.startswith("data:"):
            return dict(json.loads(rad[5:].strip()))
    return dict(json.loads(text))


def _initiera(klient: TestClient, rubriker: dict[str, str]) -> dict[str, str]:
    svar = _rpc(
        klient,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "1"}},
        rubriker,
    )
    assert svar.status_code == 200, svar.text
    sid = svar.headers.get("mcp-session-id")
    ut = dict(rubriker)
    if sid:
        ut["Mcp-Session-Id"] = sid
    klient.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **ut},
    )
    return ut


def test_bearer_over_protokollet_loggar_konto_och_token_id_men_aldrig_token(
    demo: Demo, miljo: Miljo, tmp_path: Path
) -> None:
    bjud_in_anna(miljo)
    webb, konto_id = _inloggad(miljo)
    ny = _skapa_token(webb)
    stack = Stack(demo, miljo, tmp_path)
    auth = {"Authorization": f"Bearer {ny['token']}"}
    with TestClient(stack.skydd, base_url="http://floden.test") as klient:
        # Utan token: 401 med WWW-Authenticate.
        assert _rpc(klient, "tools/list", {}, {}).status_code == 401
        sess = _initiera(klient, auth)
        svar = _rpc(klient, "tools/call", {"name": "metoder_lista", "arguments": {}}, sess, id_=2)
        assert svar.status_code == 200, svar.text
        assert "result" in _data(svar)
        # Återkallad token mitt i sessionen: nästa anrop nekas.
        assert _skriv(webb, "DELETE", f"/konto/mcp-token/{ny['id']}")[0] == 200
        assert _rpc(klient, "tools/call", {"name": "metoder_lista", "arguments": {}}, sess, id_=3).status_code == 401
    rader = [r for r in stack.logg.las() if r.get("verktyg") == "metoder_lista"]
    assert rader and all(r["konto"] == konto_id and r["token"] == ny["id"] for r in rader)
    loggtext = stack.logg.sokvag.read_text(encoding="utf-8")
    assert ny["token"] not in loggtext and ny["token"][-8:] not in loggtext


def test_en_mcp_session_kan_inte_overtas_av_ett_annat_konto(demo: Demo, miljo: Miljo, tmp_path: Path) -> None:
    anna, _ = _inloggad(miljo, ANNA)
    bo, _ = _inloggad(miljo, BO)
    ta, tb = _skapa_token(anna), _skapa_token(bo)
    stack = Stack(demo, miljo, tmp_path)
    with TestClient(stack.skydd, base_url="http://floden.test") as klient:
        sess = _initiera(klient, {"Authorization": f"Bearer {ta['token']}"})
        if "Mcp-Session-Id" not in sess:
            pytest.skip("servern är tillståndslös")
        kapare = {**sess, "Authorization": f"Bearer {tb['token']}"}
        svar = _rpc(klient, "tools/call", {"name": "metoder_lista", "arguments": {}}, kapare, id_=2)
        assert svar.status_code == 404
        egen = _rpc(klient, "tools/call", {"name": "metoder_lista", "arguments": {}}, sess, id_=3)
        assert egen.status_code == 200


def anropa_verktyg(
    klient: TestClient, sess: dict[str, str], namn: str, argument: dict[str, Any] | None = None, id_: int = 9
) -> tuple[bool, str, dict[str, Any]]:
    """(fel?, text, structuredContent) för ett verktygsanrop över HTTP."""
    svar = _rpc(klient, "tools/call", {"name": namn, "arguments": argument or {}}, sess, id_=id_)
    assert svar.status_code == 200, svar.text
    res = _data(svar)["result"]
    text = " ".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
    return bool(res.get("isError")), text, dict(res.get("structuredContent") or {})
