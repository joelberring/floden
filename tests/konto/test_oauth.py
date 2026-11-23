# ruff: noqa: E501
"""OAuth 2.1 för MCP: registrering, auktorisering med PKCE, token, rotation och återkallning."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from konto.oauth import ACCESS_GILTIG_S, REFRESH_GILTIG_S, pkce_utmaning
from tests.floden_mcp.test_mcp_http import _kor_skydd
from tests.konto.conftest import ORIGIN, Miljo, Webblasare, rubrik
from tests.konto.test_nycklar import _inloggad, _skriv

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
VERIFIERARE = "v" * 43 + "-._~" + "A" * 10
IP = {"x-floden-klient-ip": "203.0.113.7"}


def _json(svar: Any) -> dict[str, Any]:
    return json.loads(svar[2])  # type: ignore[no-any-return]


def _registrera(miljo: Miljo, uris: list[str] | None = None, namn: str = "Claude") -> dict[str, Any]:
    kropp = json.dumps({"client_name": namn, "redirect_uris": uris or [CLAUDE]}).encode()
    svar = miljo.adapter.hantera_konto("POST", "/oauth/register", {}, IP, kropp)
    assert svar[0] == 201, svar
    return _json(svar)


def _fraga(klient: dict[str, Any], uri: str = CLAUDE, **extra: str) -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": klient["client_id"],
        "redirect_uri": uri,
        "code_challenge": pkce_utmaning(VERIFIERARE),
        "code_challenge_method": "S256",
        "state": "tillstand-" + "x" * 20,
        "resource": f"{ORIGIN}/mcp",
        **extra,
    }


def _authorize_get(webb: Webblasare, p: dict[str, str]) -> Any:
    return webb.anrop("GET", "/oauth/authorize", {k: [v] for k, v in p.items()})


def _godkann(webb: Webblasare, p: dict[str, str], beslut: str = "tillat") -> Any:
    form = {**p, "beslut": beslut, "csrf": webb.csrf_headers()["X-Floden-CSRF"]}
    return webb.anrop(
        "POST",
        "/oauth/authorize",
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
        kropp=urlencode(form).encode(),
    )


def _kod_fran(svar: Any) -> str:
    assert svar[0] == 302, svar
    plats = urlsplit(rubrik(svar, "Location") or "")
    q = parse_qs(plats.query)
    assert q["iss"] == [ORIGIN]
    return q["code"][0]


def _token(miljo: Miljo, **form: str) -> Any:
    return miljo.adapter.hantera_konto(
        "POST", "/oauth/token", {}, IP, urlencode(form).encode()
    )


def _byt(miljo: Miljo, klient: dict[str, Any], kod: str, **extra: str) -> Any:
    return _token(
        miljo,
        grant_type="authorization_code",
        code=kod,
        client_id=klient["client_id"],
        redirect_uri=CLAUDE,
        code_verifier=VERIFIERARE,
        **extra,
    )


def _hela_flodet(miljo: Miljo) -> tuple[Webblasare, str, dict[str, Any], dict[str, Any]]:
    webb, konto_id = _inloggad(miljo)
    klient = _registrera(miljo)
    kod = _kod_fran(_godkann(webb, _fraga(klient)))
    svar = _byt(miljo, klient, kod)
    assert svar[0] == 200, svar
    return webb, konto_id, klient, _json(svar)


def test_metadata_for_resurs_och_server(miljo: Miljo) -> None:
    for sokvag in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        svar = miljo.adapter.hantera_konto("GET", sokvag, {}, {})
        assert svar[0] == 200
        prm = _json(svar)
        assert prm["resource"] == f"{ORIGIN}/mcp" and prm["authorization_servers"] == [ORIGIN]
    svar = miljo.adapter.hantera_konto("GET", "/.well-known/oauth-authorization-server", {}, {})
    meta = _json(svar)
    assert meta["issuer"] == ORIGIN
    assert meta["authorization_endpoint"] == f"{ORIGIN}/oauth/authorize"
    assert meta["token_endpoint"] == f"{ORIGIN}/oauth/token"
    assert meta["registration_endpoint"] == f"{ORIGIN}/oauth/register"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["authorization_response_iss_parameter_supported"] is True


def test_401_pekar_pa_resursmetadata(miljo: Miljo) -> None:
    status, huvud, _, _ = _kor_skydd(miljo, "POST", {})
    assert status == 401
    assert f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/mcp"' in huvud["www-authenticate"]


def test_registrering_godtar_bara_https_och_localhost(miljo: Miljo) -> None:
    ok = _registrera(miljo, [CLAUDE, "http://localhost:6274/callback"])
    assert re.fullmatch(r"[0-9a-f]{32}", ok["client_id"]) and ok["token_endpoint_auth_method"] == "none"
    for uri in (
        "http://example.com/cb",
        "http://127.0.0.1:1234/cb",
        "javascript:alert(1)",
        "https://claude.ai/cb#frag",
        "https://user:pw@claude.ai/cb",
        "claude://callback",
        "/relativ",
    ):
        kropp = json.dumps({"client_name": "x", "redirect_uris": [uri]}).encode()
        svar = miljo.adapter.hantera_konto("POST", "/oauth/register", {}, IP, kropp)
        assert svar[0] == 400 and _json(svar)["error"] == "invalid_redirect_uri", uri
    kropp = json.dumps({"redirect_uris": [CLAUDE], "token_endpoint_auth_method": "client_secret_basic"}).encode()
    assert miljo.adapter.hantera_konto("POST", "/oauth/register", {}, IP, kropp)[0] == 400


def test_registrering_ar_hastighetsbegransad_per_ip(miljo: Miljo) -> None:
    kropp = json.dumps({"client_name": "x", "redirect_uris": [CLAUDE]}).encode()
    status = [miljo.adapter.hantera_konto("POST", "/oauth/register", {}, IP, kropp)[0] for _ in range(11)]
    assert status[:10] == [201] * 10 and status[10] == 429
    annan = {"x-floden-klient-ip": "198.51.100.9"}
    assert miljo.adapter.hantera_konto("POST", "/oauth/register", {}, annan, kropp)[0] == 201


def test_token_utbyte_ar_hastighetsbegransat(miljo: Miljo) -> None:
    klient = _registrera(miljo)
    status = [
        _token(miljo, grant_type="authorization_code", code="x", client_id=klient["client_id"])[0]
        for _ in range(61)
    ]
    assert status[-1] == 429 and set(status[:-1]) == {400}


def test_utan_session_skickas_man_till_inloggningen_och_tillbaka(miljo: Miljo) -> None:
    miljo.register.forbered_konto("anna@example.test")
    webb = miljo.webblasare()
    klient = _registrera(miljo)
    p = _fraga(klient)
    svar = _authorize_get(webb, p)
    plats = rubrik(svar, "Location") or ""
    assert svar[0] == 302 and plats.startswith("/konto/logga-in/google?atervand=")
    atervand = parse_qs(urlsplit(plats).query)["atervand"][0]
    assert atervand.startswith("/oauth/authorize?") and klient["client_id"] in atervand
    # Efter inloggningen landar man på godkännandesidan.
    from tests.konto.conftest import ANNA

    inlogg = webb.logga_in(miljo.idp, ANNA, atervand=atervand)
    assert rubrik(inlogg, "Location") == atervand
    sida = _authorize_get(webb, {k: v[0] for k, v in parse_qs(urlsplit(atervand).query).items()})
    assert sida[0] == 200 and b"Ge <strong>Claude</strong> tillg" in sida[2]
    assert "frame-ancestors 'none'" in (rubrik(sida, "Content-Security-Policy") or "")


def test_redirect_uri_maste_matcha_exakt_och_ger_sida_inte_omdirigering(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    for uri in (CLAUDE + "/", CLAUDE + "?x=1", "https://evil.example/cb", "https://claude.ai/api/mcp/auth_callbacks"):
        for svar in (
            _authorize_get(webb, _fraga(klient, uri)),
            _godkann(webb, _fraga(klient, uri)),
        ):
            assert svar[0] == 400 and rubrik(svar, "Location") is None, uri
    okand = _fraga(klient) | {"client_id": "0" * 32}
    assert _authorize_get(webb, okand)[0] == 400


def test_pkce_krav_och_fel_verifierare(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    # plain eller utan utmaning: tillbaka till klienten med invalid_request
    for andring in ({"code_challenge_method": "plain"}, {"code_challenge": "kort"}):
        svar = _authorize_get(webb, _fraga(klient) | andring)
        assert svar[0] == 302
        q = parse_qs(urlsplit(rubrik(svar, "Location") or "").query)
        assert q["error"] == ["invalid_request"] and q["iss"] == [ORIGIN]
    # fel verifierare vid utbytet: invalid_grant, och koden är förbrukad
    kod = _kod_fran(_godkann(webb, _fraga(klient)))
    fel = _token(
        miljo, grant_type="authorization_code", code=kod, client_id=klient["client_id"],
        redirect_uri=CLAUDE, code_verifier="B" * 50,
    )
    assert fel[0] == 400 and _json(fel)["error"] == "invalid_grant"
    assert _byt(miljo, klient, kod)[0] == 400  # rätt verifierare hjälper inte nu


def test_kod_med_fel_klient_eller_redirect_uri_nekas(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    a, b = _registrera(miljo), _registrera(miljo, ["https://annan.example/cb"])
    kod = _kod_fran(_godkann(webb, _fraga(a)))
    assert _byt(miljo, b, kod)[0] == 400
    kod = _kod_fran(_godkann(webb, _fraga(a)))
    svar = _token(
        miljo, grant_type="authorization_code", code=kod, client_id=a["client_id"],
        redirect_uri="https://claude.ai/annan", code_verifier=VERIFIERARE,
    )
    assert svar[0] == 400 and _json(svar)["error"] == "invalid_grant"


def test_hela_flodet_ger_bearer_som_nar_mcp(miljo: Miljo) -> None:
    webb, konto_id, klient, tok = _hela_flodet(miljo)
    assert tok["token_type"] == "Bearer" and tok["expires_in"] == ACCESS_GILTIG_S
    assert tok["access_token"].startswith("fl_oat_") and tok["refresh_token"].startswith("fl_ort_")
    status, _, _, scope = _kor_skydd(miljo, "POST", {"Authorization": f"Bearer {tok['access_token']}"})
    assert status == 200 and scope["floden_konto"] == konto_id and scope["floden_token"]
    # kontot ser anslutningen, utan token
    svar = webb.anrop("GET", "/konto/oauth")
    lista = json.loads(svar[2])["anslutningar"]
    assert [a["namn"] for a in lista] == ["Claude"] and tok["access_token"].encode() not in svar[2]


def test_access_token_gar_ut_efter_en_timme(miljo: Miljo) -> None:
    _, _, _, tok = _hela_flodet(miljo)
    auth = {"Authorization": f"Bearer {tok['access_token']}"}
    miljo.klocka.gå(ACCESS_GILTIG_S - 5)
    assert _kor_skydd(miljo, "GET", auth)[0] == 200
    miljo.klocka.gå(10)
    assert _kor_skydd(miljo, "GET", auth)[0] == 401


def test_refresh_roterar_och_gammal_refresh_ar_dod(miljo: Miljo) -> None:
    _, _, klient, tok = _hela_flodet(miljo)
    ny = _token(miljo, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=klient["client_id"])
    assert ny[0] == 200
    ny_tok = _json(ny)
    assert ny_tok["refresh_token"] != tok["refresh_token"] and ny_tok["access_token"] != tok["access_token"]
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {ny_tok['access_token']}"})[0] == 200
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {tok['access_token']}"})[0] == 401
    # Gammal refresh-token igen: nekas och hela behörigheten återkallas (stöldskydd).
    igen = _token(miljo, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=klient["client_id"])
    assert igen[0] == 400 and _json(igen)["error"] == "invalid_grant"
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {ny_tok['access_token']}"})[0] == 401
    sista = _token(miljo, grant_type="refresh_token", refresh_token=ny_tok["refresh_token"], client_id=klient["client_id"])
    assert sista[0] == 400


def test_refresh_gar_ut_efter_30_dagar_och_binds_till_klient(miljo: Miljo) -> None:
    webb, _, klient, tok = _hela_flodet(miljo)
    annan = _registrera(miljo, ["https://annan.example/cb"])
    assert _token(miljo, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=annan["client_id"])[0] == 400
    miljo.klocka.gå(REFRESH_GILTIG_S + 1)
    assert _token(miljo, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=klient["client_id"])[0] == 400


def test_aterkallad_klient_slutar_fungera(miljo: Miljo) -> None:
    _, _, klient, tok = _hela_flodet(miljo)
    assert miljo.adapter.oauth is not None
    assert miljo.adapter.oauth.aterkalla_klient(klient["client_id"])
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {tok['access_token']}"})[0] == 401
    svar = _token(miljo, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=klient["client_id"])
    assert svar[0] == 401 and _json(svar)["error"] == "invalid_client"
    webb, _ = _inloggad(miljo)
    assert _authorize_get(webb, _fraga(klient))[0] == 400


def test_kontot_kan_aterkalla_en_anslutning(miljo: Miljo) -> None:
    webb, _, klient, tok = _hela_flodet(miljo)
    (post,) = json.loads(webb.anrop("GET", "/konto/oauth")[2])["anslutningar"]
    assert _skriv(webb, "DELETE", f"/konto/oauth/{post['id']}", csrf=False)[0] == 403  # CSRF krävs
    assert _skriv(webb, "DELETE", f"/konto/oauth/{post['id']}")[0] == 200
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {tok['access_token']}"})[0] == 401
    assert _token(miljo, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=klient["client_id"])[0] == 400
    assert json.loads(webb.anrop("GET", "/konto/oauth")[2])["anslutningar"] == []
    assert _skriv(webb, "DELETE", f"/konto/oauth/{post['id']}")[0] == 404


def test_kod_som_anvands_tva_ganger_aterkallar_det_den_gav(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    kod = _kod_fran(_godkann(webb, _fraga(klient)))
    forsta = _byt(miljo, klient, kod)
    assert forsta[0] == 200
    assert _byt(miljo, klient, kod)[0] == 400
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {_json(forsta)['access_token']}"})[0] == 401


def test_koden_gar_ut_efter_en_minut(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    kod = _kod_fran(_godkann(webb, _fraga(klient)))
    miljo.klocka.gå(61)
    assert _byt(miljo, klient, kod)[0] == 400


def test_godkannandet_kraver_csrf_och_origin_och_nej_ger_access_denied(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    p = _fraga(klient)
    utan = webb.anrop(
        "POST", "/oauth/authorize", headers={"Origin": ORIGIN},
        kropp=urlencode({**p, "beslut": "tillat", "csrf": "fel"}).encode(),
    )
    assert utan[0] == 403 and rubrik(utan, "Location") is None
    form = {**p, "beslut": "tillat", "csrf": webb.csrf_headers()["X-Floden-CSRF"]}
    fel_origin = webb.anrop(
        "POST", "/oauth/authorize", headers={"Origin": "https://evil.example"},
        kropp=urlencode(form).encode(),
    )
    assert fel_origin[0] == 403
    nej = _godkann(webb, p, "neka")
    q = parse_qs(urlsplit(rubrik(nej, "Location") or "").query)
    assert q["error"] == ["access_denied"] and "code" not in q and q["state"] == [p["state"]]


def test_fel_resurs_nekas(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    svar = _authorize_get(webb, _fraga(klient, resource="https://annan.example/mcp"))
    assert parse_qs(urlsplit(rubrik(svar, "Location") or "").query)["error"] == ["invalid_target"]
    kod = _kod_fran(_godkann(webb, _fraga(klient)))
    fel = _byt(miljo, klient, kod, resource="https://annan.example/mcp")
    assert fel[0] == 400 and _json(fel)["error"] == "invalid_target"


def test_sparrat_konto_nekas_overallt(miljo: Miljo) -> None:
    _, konto_id, klient, tok = _hela_flodet(miljo)
    assert miljo.adapter.sessioner is not None
    miljo.adapter.sessioner.sparra_konto(konto_id)
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {tok['access_token']}"})[0] == 401
    assert _token(miljo, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=klient["client_id"])[0] == 400


def test_databasen_har_bara_hashar_och_svaren_ingen_hemlighet(miljo: Miljo) -> None:
    webb, _, klient, tok = _hela_flodet(miljo)
    with miljo.register.db.lasning() as c:
        allt = json.dumps({t: [dict(r) for r in c.execute(f"SELECT * FROM {t}").fetchall()] for t in ("oauth_kod", "oauth_token", "oauth_grant")})  # noqa: S608
    for hemlig in (tok["access_token"], tok["refresh_token"]):
        assert hemlig not in allt
        assert hashlib.sha256(hemlig.encode()).hexdigest() in allt
    sokvag = miljo.register.db.sokvag
    for fil in sokvag.parent.glob(sokvag.name + "*"):
        assert tok["access_token"].encode() not in fil.read_bytes()


def test_revoke_endpoint_aterkallar_utan_att_avslora(miljo: Miljo) -> None:
    _, _, klient, tok = _hela_flodet(miljo)
    okand = miljo.adapter.hantera_konto("POST", "/oauth/revoke", {}, IP, b"token=fl_oat_" + b"A" * 43)
    assert okand[0] == 200
    svar = miljo.adapter.hantera_konto("POST", "/oauth/revoke", {}, IP, urlencode({"token": tok["refresh_token"]}).encode())
    assert svar[0] == 200
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer {tok['access_token']}"})[0] == 401


def test_oauth_ar_av_nar_inloggning_ar_av(tmp_path: Any) -> None:
    from konto.adapter import KontoAdapter
    from konto.konfig import KontoKonfig

    a = KontoAdapter(KontoKonfig())
    assert a.hantera_konto("GET", "/.well-known/oauth-authorization-server", {}, {})[0] == 404
    assert a.resurs_metadata_url() is None


def test_ogiltig_oauth_token_i_bearer_ger_401(miljo: Miljo) -> None:
    assert _kor_skydd(miljo, "GET", {"Authorization": "Bearer fl_oat_" + "A" * 43})[0] == 401
    assert _kor_skydd(miljo, "GET", {"Authorization": "Bearer fl_oat_kort"})[0] == 401
    b64 = base64.urlsafe_b64encode(b"x").decode()
    assert _kor_skydd(miljo, "GET", {"Authorization": f"Bearer fl_ort_{b64}"})[0] == 401


def test_godkannandet_godtar_origin_null_fran_samma_sajt_men_inte_utan_csrf(miljo: Miljo) -> None:
    """Chrome skickar `Origin: null` för formulär på sidor med strikt referrer-policy."""
    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    p = _fraga(klient)
    form = {**p, "beslut": "tillat", "csrf": webb.csrf_headers()["X-Floden-CSRF"]}
    rubr = {"Origin": "null", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded"}
    ok = webb.anrop("POST", "/oauth/authorize", headers=rubr, kropp=urlencode(form).encode())
    assert _kod_fran(ok)
    # Origin null från en annan sajt nekas.
    korsande = webb.anrop(
        "POST", "/oauth/authorize", headers={**rubr, "Sec-Fetch-Site": "cross-site"},
        kropp=urlencode(form).encode(),
    )
    assert korsande[0] == 403
    # Origin null utan rätt CSRF-token nekas.
    utan = webb.anrop(
        "POST", "/oauth/authorize", headers=rubr,
        kropp=urlencode({**form, "csrf": "fel"}).encode(),
    )
    assert utan[0] == 403


def test_godkannandesidan_har_inte_no_referrer(miljo: Miljo) -> None:
    webb, _ = _inloggad(miljo)
    sida = _authorize_get(webb, _fraga(_registrera(miljo)))
    assert sida[0] == 200
    assert rubrik(sida, "Referrer-Policy") == "same-origin"


def test_okant_scope_fran_claude_appen_ger_kod_med_scope_mcp(miljo: Miljo) -> None:
    """Claude-appen begär ett eget scope (t.ex. `claudeai`); Floden utfärdar ändå `mcp`."""
    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    p = _fraga(klient, scope="claudeai")
    assert _authorize_get(webb, p)[0] == 200
    kod = _kod_fran(_godkann(webb, p))
    tok = _byt(miljo, klient, kod)
    assert tok[0] == 200 and _json(tok)["scope"] == "mcp"


def test_godkannandesidans_eget_formular_ger_en_kod(miljo: Miljo) -> None:
    """Postar exakt de dolda fälten som sidan innehåller (inte begärans fält direkt)."""
    import html as _html
    import re as _re

    webb, _ = _inloggad(miljo)
    klient = _registrera(miljo)
    sida = _authorize_get(webb, _fraga(klient))
    assert sida[0] == 200
    text = sida[2].decode("utf-8")
    falt = {
        _html.unescape(n): _html.unescape(v)
        for n, v in _re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', text)
    }
    assert falt["code_challenge_method"] == "S256"
    svar = webb.anrop(
        "POST",
        "/oauth/authorize",
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
        kropp=urlencode({**falt, "beslut": "tillat"}).encode(),
    )
    kod = _kod_fran(svar)
    assert _byt(miljo, klient, kod)[0] == 200
