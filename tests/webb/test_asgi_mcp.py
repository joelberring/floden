# ruff: noqa: E501
"""`/mcp` i webbens ASGI-server: samma process, kontoskydd, av utan inloggning på icke-loopback."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from floden_mcp.montera import McpAvstangd, MonteradMcp
from tests.floden_mcp.test_mcp_http import _data, _initiera, _rpc, _skapa_token
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_nycklar import _inloggad
from webb.asgi import FlodenAsgiApp, skapa_asgi_app

REPO = Path(__file__).resolve().parents[2]


def _rot(tmp_path: Path) -> Path:
    """En tom rot med egen config.toml: körloggen och data hamnar i tmp, inte i repot."""
    rot = tmp_path / "rot"
    rot.mkdir(exist_ok=True)
    shutil.copy(REPO / "config.toml", rot / "config.toml")
    return rot


def _app(tmp_path: Path, host: str | None, konto: Any = None) -> FlodenAsgiApp:
    rot = _rot(tmp_path)
    return skapa_asgi_app(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=rot / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        klientmapp=tmp_path / "dist",
        konto=konto,
        mcp_host=host,
    )


def test_mcp_ar_av_nar_inloggningen_ar_av_och_vardet_inte_ar_loopback(tmp_path: Path) -> None:
    for host in ("0.0.0.0", "192.168.1.5", "floden.example.test"):  # noqa: S104
        with pytest.raises(McpAvstangd) as fel:
            _app(tmp_path, host)
        assert "inloggningen är av" in str(fel.value)
    # Utan montering finns ingen /mcp: förfrågan går till webbens API (404).
    app = _app(tmp_path, None)
    assert app.mcp is None
    with TestClient(app) as klient:
        assert klient.post("/mcp", json={}).status_code == 404
        assert klient.get("/api/halsa").status_code == 200


def test_mcp_pa_loopback_utan_inloggning_och_webben_funkar_bredvid(tmp_path: Path) -> None:
    app = _app(tmp_path, "127.0.0.1")
    assert isinstance(app.mcp, MonteradMcp)
    with TestClient(app, base_url="http://127.0.0.1:8765") as klient:
        assert klient.get("/api/halsa").status_code == 200
        sess = _initiera(klient, {})
        svar = _rpc(klient, "tools/list", {}, sess, id_=2)
        namn = {t["name"] for t in _data(svar)["result"]["tools"]}
        assert {"projekt_lista", "kor", "korlogg_hamta"} <= namn


def test_mcp_med_inloggning_kraver_token_eller_session_pa_icke_loopback(miljo: Miljo, tmp_path: Path) -> None:
    webb, konto_id = _inloggad(miljo, ANNA)
    token = _skapa_token(webb)["token"]
    app = _app(tmp_path, "0.0.0.0", konto=miljo.adapter)  # noqa: S104
    assert isinstance(app.mcp, MonteradMcp)
    with TestClient(app, base_url="https://floden.test") as klient:
        # Webben fungerar fortfarande bredvid (halsa är öppen, projekt kräver inloggning).
        assert klient.get("/api/halsa").status_code == 200
        assert klient.get("/api/projekt").status_code == 401
        # /mcp: 401 med WWW-Authenticate utan token, och med en felaktig.
        for rubriker in ({}, {"Authorization": "Bearer fl_mcp_" + "A" * 43}):
            svar = _rpc(klient, "tools/list", {}, rubriker)
            assert svar.status_code == 401
            assert svar.headers["www-authenticate"].startswith("Bearer ")
        sess = _initiera(klient, {"Authorization": f"Bearer {token}"})
        svar = _rpc(klient, "tools/call", {"name": "projekt_lista", "arguments": {}}, sess, id_=3)
        assert svar.status_code == 200
        res = _data(svar)["result"]
        assert not res.get("isError")
        # En främmande Host nekas av MCP:s skydd mot DNS rebinding.
        fel = _rpc(klient, "tools/list", {}, {**sess, "Host": "angripare.example"}, id_=4)
        assert fel.status_code == 421
    assert konto_id  # kontot följde med in i körloggen (se test_mcp_http)
    loggfil = _rot(tmp_path) / "data" / "lokal" / "mcp" / "korlogg.jsonl"
    assert loggfil.is_file() and konto_id in loggfil.read_text(encoding="utf-8")
