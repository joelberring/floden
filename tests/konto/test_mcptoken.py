"""Personliga MCP-token: livscykel, hash i databasen, gräns, spärr, CSRF och isolering."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import pytest

from konto.mcptoken import MAX_PER_KONTO, PREFIX, McpTokenFel, hash_token
from tests.konto.conftest import ANNA, ORIGIN, Miljo, Webblasare
from tests.konto.test_nycklar import _inloggad, _las, _skriv

BO = {"sub": "sub-bo-x2", "email": "bo@example.test", "email_verified": True, "name": "Bo Exempel"}
TOKENFORM = re.compile(r"^fl_mcp_[A-Za-z0-9_-]{43}$")


def _skapa(webb: Webblasare, namn: str = "Laptop") -> dict[str, Any]:
    status, svar = _skriv(webb, "POST", "/konto/mcp-token", {"namn": namn})
    assert status == 201, svar
    return svar


def test_format_och_visas_en_gang(miljo: Miljo) -> None:
    webb, konto_id = _inloggad(miljo)
    ny = _skapa(webb)
    token = ny["token"]
    assert TOKENFORM.match(token) and token.startswith(PREFIX)
    assert ny["slutar_pa"] == token[-4:] and ny["namn"] == "Laptop"
    # Listan har aldrig token eller hash, bara namn, datum och de fyra sista tecknen.
    status, lista, rakropp = _las(webb, "/konto/mcp-token")
    assert status == 200 and lista["max"] == MAX_PER_KONTO
    assert lista["mcp_url"] == f"{ORIGIN}/mcp"
    (post,) = lista["tokens"]
    assert post["id"] == ny["id"] and post["slutar_pa"] == token[-4:]
    assert post["senast_anvand"] is None and post["skapad_datum"]
    assert token.encode() not in rakropp and hash_token(token).encode() not in rakropp
    assert "token" not in post


def test_databasen_har_bara_sha256_av_token(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    token = _skapa(webb)["token"]
    with miljo.register.db.lasning() as c:
        rader = [dict(r) for r in c.execute("SELECT * FROM mcp_token").fetchall()]
    (rad,) = rader
    assert rad["token_hash"] == hash_token(token) and len(rad["token_hash"]) == 64
    assert token not in json.dumps(rader)
    sokvag = miljo.register.db.sokvag
    for fil in sokvag.parent.glob(sokvag.name + "*"):  # inte heller i WAL-filen
        assert token.encode() not in fil.read_bytes()


def test_livscykel_skapa_anvanda_aterkalla(miljo: Miljo) -> None:
    webb, konto_id = _inloggad(miljo)
    ny = _skapa(webb)
    forrad = miljo.adapter.mcp_tokens
    assert forrad is not None
    assert forrad.verifiera(ny["token"]) == (konto_id, ny["id"])
    status, lista, _ = _las(webb, "/konto/mcp-token")
    assert lista["tokens"][0]["senast_anvand"] is not None  # användningen noterades
    status, svar = _skriv(webb, "DELETE", f"/konto/mcp-token/{ny['id']}")
    assert status == 200 and svar["status"] == "aterkallad"
    assert forrad.verifiera(ny["token"]) is None  # slutar gälla direkt
    assert _las(webb, "/konto/mcp-token")[1]["tokens"] == []
    assert _skriv(webb, "DELETE", f"/konto/mcp-token/{ny['id']}")[0] == 404  # redan återkallad


def test_hogst_fem_aktiva_och_aterkallning_frigor_plats(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    ny = [_skapa(webb, f"t{i}") for i in range(MAX_PER_KONTO)]
    status, svar = _skriv(webb, "POST", "/konto/mcp-token", {"namn": "en för mycket"})
    assert status == 409 and svar["kod"] == "for-manga"
    assert _skriv(webb, "DELETE", f"/konto/mcp-token/{ny[0]['id']}")[0] == 200
    assert _skriv(webb, "POST", "/konto/mcp-token", {"namn": "ny"})[0] == 201


def test_ogiltigt_namn_och_okanda_faltsvar(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    for kropp in ({}, {"namn": ""}, {"namn": "   "}, {"namn": 5}, {"namn": "x" * 61}):
        status, svar = _skriv(webb, "POST", "/konto/mcp-token", kropp)
        assert status == 400 and svar["kod"] == "ogiltigt", kropp
    assert _skriv(webb, "PUT", "/konto/mcp-token", {"namn": "a"})[0] == 405
    assert _skriv(webb, "DELETE", "/konto/mcp-token/inte-ett-id")[0] == 404


def test_sparrat_konto_nekas_aven_med_en_redan_skapad_token(miljo: Miljo) -> None:
    webb, konto_id = _inloggad(miljo)
    token = _skapa(webb)["token"]
    forrad = miljo.adapter.mcp_tokens
    assert forrad is not None and forrad.verifiera(token) is not None
    assert miljo.adapter.sessioner is not None
    miljo.adapter.sessioner.sparra_konto(konto_id)
    assert forrad.verifiera(token) is None
    with pytest.raises(McpTokenFel) as fel:
        forrad.skapa(konto_id, "ny")
    assert fel.value.kod == "sparrat" and fel.value.status == 403


def test_skriv_kraver_session_csrf_och_origin(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    ny = _skapa(webb)
    # Utan CSRF-huvud, och med fel Origin, nekas allt som ändrar.
    assert _skriv(webb, "POST", "/konto/mcp-token", {"namn": "x"}, csrf=False)[0] == 403
    assert _skriv(webb, "DELETE", f"/konto/mcp-token/{ny['id']}", csrf=False)[0] == 403
    status, _, _ = webb.anrop(
        "POST",
        "/konto/mcp-token",
        headers={**webb.csrf_headers(), "Origin": "https://annan.test"},
        kropp=b'{"namn":"x"}',
    )
    assert status == 403
    # Utan session: 401.
    utan = miljo.webblasare()
    assert utan.anrop("GET", "/konto/mcp-token")[0] == 401
    assert utan.anrop("POST", "/konto/mcp-token", kropp=b'{"namn":"x"}')[0] == 401


def test_en_mcp_token_kan_inte_skapa_eller_aterkalla_token(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    ny = _skapa(webb)
    bearer = {"Authorization": f"Bearer {ny['token']}", "Origin": ORIGIN}
    utan = miljo.webblasare()
    assert utan.anrop("GET", "/konto/mcp-token", headers=bearer)[0] == 401
    assert utan.anrop("POST", "/konto/mcp-token", headers=bearer, kropp=b'{"namn":"x"}')[0] == 401
    assert utan.anrop("DELETE", f"/konto/mcp-token/{ny['id']}", headers=bearer)[0] == 401
    assert len(miljo.adapter.mcp_tokens.lista(_konto(miljo))) == 1  # type: ignore[union-attr]


def _konto(miljo: Miljo) -> str:
    konto = miljo.register.konto_via_epost("anna@example.test")
    assert konto is not None
    return konto.id


def test_kontot_ser_och_aterkallar_bara_sina_egna(miljo: Miljo) -> None:
    anna, _ = _inloggad(miljo, ANNA)
    bo, _ = _inloggad(miljo, BO)
    ny = _skapa(anna)
    assert _las(bo, "/konto/mcp-token")[1]["tokens"] == []
    assert _skriv(bo, "DELETE", f"/konto/mcp-token/{ny['id']}")[0] == 404
    forrad = miljo.adapter.mcp_tokens
    assert forrad is not None and forrad.verifiera(ny["token"]) is not None


def test_ogiltiga_tokenformer_ger_none(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    token = _skapa(webb)["token"]
    forrad = miljo.adapter.mcp_tokens
    assert forrad is not None
    for fel in ("", "fl_mcp_", token[:-1], token + "x", token.upper(), "Bearer " + token):
        assert forrad.verifiera(fel) is None, fel


def test_inga_token_eller_namn_i_loggar_eller_handelser(
    miljo: Miljo, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    webb, konto_id = _inloggad(miljo)
    ny = _skapa(webb, "Joels hemliga laptop")
    forrad = miljo.adapter.mcp_tokens
    assert forrad is not None
    forrad.verifiera(ny["token"])
    _skriv(webb, "DELETE", f"/konto/mcp-token/{ny['id']}")
    with miljo.register.db.lasning() as c:
        handelser = " ".join(
            f"{r['handelse']} {r['konto_id']} {r['detalj']}"
            for r in c.execute("SELECT * FROM handelse").fetchall()
        )
    loggtext = caplog.text + handelser
    assert ny["token"] not in loggtext and ny["token"][-12:] not in loggtext
    assert "Joels hemliga laptop" not in loggtext and "anna@example.test" not in loggtext
    assert konto_id in loggtext and ny["id"] in loggtext


def test_inloggning_av_har_ingen_tokenfunktion() -> None:
    from konto.adapter import KontoAdapter
    from konto.konfig import KontoKonfig

    av = KontoAdapter(KontoKonfig())
    assert av.mcp_tokens is None and av.kontrollera_bearer("fl_mcp_" + "A" * 43) is None
    assert av.hantera_konto("GET", "/konto/mcp-token", {}, {})[0] == 404
