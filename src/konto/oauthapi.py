# ruff: noqa: E501
"""HTTP-delen av OAuth 2.1 för MCP (anropas av `KontoAdapter.hantera_konto`).

Ändpunkter (se `oauth.py` för reglerna):

- `GET  /.well-known/oauth-protected-resource[/mcp]`  metadata för resursen (RFC 9728)
- `GET  /.well-known/oauth-authorization-server`      metadata för servern (RFC 8414)
- `POST /oauth/register`    dynamisk klientregistrering (RFC 7591), hastighetsbegränsad
- `GET  /oauth/authorize`   saknas session: till inloggningen och tillbaka hit; annars en sida
                            "Ge <app> tillgång till Floden som <konto>?"
- `POST /oauth/authorize`   svaret från sidan (session, CSRF och Origin krävs)
- `POST /oauth/token`       `authorization_code` (PKCE) och `refresh_token` (rotation)
- `POST /oauth/revoke`      RFC 7009
- `GET/DELETE /konto/oauth[/<id>]`  kontots egna anslutningar (session + CSRF, som nyckelsidan)

Svar på fel visar aldrig kod eller token. Fel i `redirect_uri` eller `client_id` ger en felsida,
aldrig en omdirigering (RFC 6749 4.1.2.1).
"""

from __future__ import annotations

import html
import json
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from konto.adapter import Forfragan, Svar, _fel, _json
from konto.oauth import (
    OauthFel,
    OauthForrad,
    pkce_utmaning,
    utmaning_ok,
)
from konto.sessioner import CSRF_HEADER

if TYPE_CHECKING:
    from konto.adapter import KontoAdapter

logg = logging.getLogger("konto")

MAX_KROPP = 8192
SCOPE = "mcp"
KLIENT_IP_RUBRIK = "x-floden-klient-ip"
_FORMFALT = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "state",
    "resource",
    "scope",
)
_HTML_RUBRIKER = [
    ("Content-Type", "text/html; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    # same-origin, inte no-referrer: med no-referrer skickar Chrome `Origin: null` när formuläret postas.
    ("Referrer-Policy", "same-origin"),
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
    ),
]
_CORS = [("Access-Control-Allow-Origin", "*")]


def _oauth_fel(
    status: int, kod: str, text: str, extra: Sequence[tuple[str, str]] = ()
) -> Svar:
    return _json(
        status,
        {"error": kod, "error_description": text},
        [("Pragma", "no-cache"), *extra],
    )


def _sida(status: int, rubrik: str, innehall: str) -> Svar:
    kropp = (
        "<!doctype html><html lang=\"sv\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(rubrik)} - Floden</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:3rem auto;"
        "padding:0 1rem;color:#1b1b1b}button{font:inherit;padding:.5rem 1rem;margin-right:.5rem}"
        "code{background:#eee;padding:0 .25rem;word-break:break-all}"
        ".a{display:inline-block;padding:.5rem 1rem;border:1px solid #444;border-radius:4px;"
        "margin:.25rem .5rem .25rem 0;color:inherit}</style></head><body>"
        f"<h1>{html.escape(rubrik)}</h1>{innehall}</body></html>"
    ).encode()
    return status, list(_HTML_RUBRIKER), kropp


def _felsida(text: str, status: int = 400) -> Svar:
    return _sida(status, "Det gick inte att ansluta", f"<p>{html.escape(text)}</p>")


def _form(req: Forfragan) -> dict[str, str]:
    if len(req.kropp) > MAX_KROPP:
        return {}
    try:
        text = req.kropp.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    return {k: v for k, v in parse_qsl(text, keep_blank_values=True, max_num_fields=30)}


def _fran_fraga(req: Forfragan) -> dict[str, str]:
    return {k: v[0] for k, v in req.query.items() if v}


def _med_parametrar(uri: str, parametrar: Mapping[str, str]) -> str:
    d = urlsplit(uri)
    fraga = urlencode([*parse_qsl(d.query, keep_blank_values=True), *parametrar.items()])
    return urlunsplit((d.scheme, d.netloc, d.path, fraga, ""))


def _klient_ip(req: Forfragan) -> str:
    return req.headers.get(KLIENT_IP_RUBRIK, "okand")[:64] or "okand"


class OauthApi:
    """Ändpunkterna ovan. Skapas av `KontoAdapter` när inloggning är på."""

    def __init__(self, adapter: KontoAdapter, forrad: OauthForrad) -> None:
        from konto.oauth import Hastighetsgrans

        self.a = adapter
        self.f = forrad
        klocka = adapter._klocka  # noqa: SLF001 - samma klocka som resten av kontosystemet
        self.reg_grans = Hastighetsgrans(10, 3600, klocka)
        self.token_grans = Hastighetsgrans(60, 600, klocka)
        self.origin = adapter.konfig.origin
        self.resurs = forrad.resurs

    # --- metadata -----------------------------------------------------------------------------

    def resurs_metadata(self) -> dict[str, Any]:
        return {
            "resource": self.resurs,
            "authorization_servers": [self.origin],
            "bearer_methods_supported": ["header"],
            "scopes_supported": [SCOPE],
            "resource_name": "Floden",
        }

    def server_metadata(self) -> dict[str, Any]:
        return {
            "issuer": self.origin,
            "authorization_endpoint": f"{self.origin}/oauth/authorize",
            "token_endpoint": f"{self.origin}/oauth/token",
            "registration_endpoint": f"{self.origin}/oauth/register",
            "revocation_endpoint": f"{self.origin}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE],
            "authorization_response_iss_parameter_supported": True,
        }

    def hantera(self, req: Forfragan) -> Svar | None:
        """Svar för OAuth-sökvägar, eller None om sökvägen inte är OAuth. Varje anrop loggas med
        metod, sökväg, status och klienttyp (aldrig kod, token, verifierare eller kakor)."""
        svar = self._hantera(req)
        if svar is not None:
            ua = (req.headers.get("user-agent") or "-")[:60]
            logg.info("oauth anrop %s %s -> %s ua=%s", req.metod, req.sokvag, svar[0], ua)
        return svar

    def _hantera(self, req: Forfragan) -> Svar | None:
        s = req.sokvag.rstrip("/")
        if s in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            if req.metod not in ("GET", "OPTIONS"):
                return _fel(405, "metod", "Metod ej tillåten")
            return _json(200, self.resurs_metadata(), _CORS)
        if s == "/.well-known/oauth-authorization-server":
            if req.metod not in ("GET", "OPTIONS"):
                return _fel(405, "metod", "Metod ej tillåten")
            return _json(200, self.server_metadata(), _CORS)
        if s == "/oauth/register":
            return self._registrera(req)
        if s == "/oauth/authorize":
            if req.metod == "GET":
                return self._auktorisera(req)
            if req.metod == "POST":
                return self._besluta(req)
            return _fel(405, "metod", "Metod ej tillåten")
        if s == "/oauth/token":
            return self._token(req)
        if s == "/oauth/revoke":
            return self._aterkalla(req)
        return None

    # --- registrering -------------------------------------------------------------------------

    def _registrera(self, req: Forfragan) -> Svar:
        if req.metod != "POST":
            return _fel(405, "metod", "Metod ej tillåten")
        if not self.reg_grans.tillat(_klient_ip(req)):
            return _oauth_fel(429, "temporarily_unavailable", "För många försök", [("Retry-After", "600")])
        try:
            data = json.loads(req.kropp[:MAX_KROPP] or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return _oauth_fel(400, "invalid_client_metadata", "Kroppen måste vara ett JSON-objekt")
        logg.info(
            "oauth registrering begärd auth=%s grant=%s resp=%s scope=%s redirect=%s",
            data.get("token_endpoint_auth_method", "-"), data.get("grant_types", "-"),
            data.get("response_types", "-"), str(data.get("scope", "-"))[:60],
            str(data.get("redirect_uris", "-"))[:200],
        )
        metod = data.get("token_endpoint_auth_method", "none")
        if metod != "none":
            return _oauth_fel(400, "invalid_client_metadata", "Bara publika klienter (none)")
        for falt, tillatna in (
            ("grant_types", {"authorization_code", "refresh_token"}),
            ("response_types", {"code"}),
        ):
            varde = data.get(falt)
            if varde is not None and (
                not isinstance(varde, list) or not set(varde) <= tillatna
            ):
                return _oauth_fel(400, "invalid_client_metadata", f"{falt} stöds inte")
        try:
            klient = self.f.registrera(data.get("client_name"), data.get("redirect_uris"))
        except OauthFel as exc:
            return _oauth_fel(exc.status, exc.kod, exc.text)
        logg.info("oauth klient registrerad klient=%s", klient["id"])
        return _json(
            201,
            {
                "client_id": klient["id"],
                "client_name": klient["namn"],
                "redirect_uris": klient["redirect_uris"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_id_issued_at": klient["skapad"],
            },
            [("Pragma", "no-cache")],
        )

    # --- auktorisering ------------------------------------------------------------------------

    def _kontrollera_begaran(
        self, p: Mapping[str, str]
    ) -> tuple[dict[str, Any] | None, Svar | None, str | None]:
        """(klient, felsida, fel-kod). Fel i klient eller redirect_uri ger en sida; övriga fel ger
        en felkod som skickas tillbaka till klienten."""
        klient = self.f.klient(p.get("client_id", ""))
        uri = p.get("redirect_uri", "")
        if klient is None:
            return None, _felsida("Okänd eller återkallad app."), None
        if uri not in klient["redirect_uris"]:
            return None, _felsida("Appens redirect_uri stämmer inte med dess registrering."), None
        if p.get("response_type", "code") != "code":
            return klient, None, "unsupported_response_type"
        if p.get("code_challenge_method") != "S256" or not utmaning_ok(p.get("code_challenge", "")):
            return klient, None, "invalid_request"
        resurs = p.get("resource")
        if resurs is not None and resurs.rstrip("/") != self.resurs:
            return klient, None, "invalid_target"
        # Okända scope (Claude-appen begär t.ex. `claudeai`) avvisas inte: Floden har en enda
        # behörighet och utfärdar alltid `mcp` (RFC 6749 3.3 tillåter ett annat scope än det begärda).
        return klient, None, None

    def _tillbaka(self, p: Mapping[str, str], parametrar: dict[str, str]) -> Svar:
        if p.get("state"):
            parametrar["state"] = p["state"]
        parametrar["iss"] = self.origin  # RFC 9207
        url = _med_parametrar(p["redirect_uri"], parametrar)
        return 302, [("Location", url), ("Cache-Control", "no-store")], b""

    def _auktorisera(self, req: Forfragan) -> Svar:
        p = _fran_fraga(req)
        klient, sida, fel = self._kontrollera_begaran(p)
        if sida is not None:
            logg.warning("oauth auktorisering avvisad (sida) klient_kand=%s", klient is not None)
            return sida
        assert klient is not None  # noqa: S101
        if fel is not None:
            logg.warning(
                "oauth auktorisering avvisad klient=%s fel=%s scope=%s metod=%s",
                klient["id"], fel, p.get("scope", "-")[:60], p.get("code_challenge_method", "-"),
            )
            return self._tillbaka(p, {"error": fel})
        kontroll = self.a.kraver_inloggning(req)
        if not kontroll.tillaten or kontroll.inloggad is None:
            return self._till_inloggning(req)
        i = kontroll.inloggad
        falt = "".join(
            f'<input type="hidden" name="{n}" value="{html.escape(p[n], quote=True)}">'
            for n in _FORMFALT
            if n in p
        )
        csrf = html.escape(self._csrf_fran_kaka(req), quote=True)
        inneh = (
            f"<p>Ge <strong>{html.escape(klient['namn'])}</strong> tillgång till Floden som "
            f"<strong>{html.escape(i.namn or i.epost or 'ditt konto')}</strong>?</p>"
            "<p>Appen får använda Floden med de projekt och roller som ditt konto har, och "
            "inget mer. Du kan ta bort tillgången under Nycklar.</p>"
            f"<p>Appen skickas tillbaka till <code>{html.escape(urlsplit(p['redirect_uri']).netloc)}"
            "</code>.</p>"
            '<form method="post" action="/oauth/authorize">'
            f'{falt}<input type="hidden" name="csrf" value="{csrf}">'
            '<button type="submit" name="beslut" value="tillat">Ge tillgång</button>'
            '<button type="submit" name="beslut" value="neka">Avbryt</button></form>'
        )
        return _sida(200, "Ge tillgång till Floden", inneh)

    def _csrf_fran_kaka(self, req: Forfragan) -> str:
        from konto.sessioner import las_kakor

        return las_kakor(req.headers.get("cookie")).get(self.a._kak.csrf, "")  # noqa: SLF001

    def _till_inloggning(self, req: Forfragan) -> Svar:
        namn = sorted(self.a._klienter)  # noqa: SLF001
        if not namn:
            return _felsida("Ingen inloggningsleverantör är påslagen.", 503)
        atervand = req.sokvag + ("?" + urlencode(_fran_fraga(req)) if req.query else "")
        val = [f"/konto/logga-in/{n}?atervand={quote(atervand, safe='')}" for n in namn]
        if len(val) == 1:
            return 302, [("Location", val[0]), ("Cache-Control", "no-store")], b""
        lankar = "".join(
            f'<a class="a" href="{html.escape(v, quote=True)}">{html.escape(n)}</a>'
            for n, v in zip(namn, val, strict=True)
        )
        return _sida(200, "Logga in på Floden", f"<p>Logga in för att fortsätta.</p>{lankar}")

    def _besluta(self, req: Forfragan) -> Svar:
        p = _form(req)
        klient, sida, fel = self._kontrollera_begaran(p)
        if sida is not None:
            return sida
        assert klient is not None  # noqa: S101
        # CSRF och Origin som för övriga skrivande anrop: formulärets fält följer med som header.
        huvud = {**req.headers, CSRF_HEADER: p.get("csrf", "")}
        # Vissa webbläsare (och appars inbyggda webbvyer) skickar `Origin: null` för ett formulär på
        # en sida med strikt referrer-policy. Då räknas Origin som saknad, men bara när webbläsaren
        # själv intygar att anropet kommer från samma sajt (Sec-Fetch-Site). CSRF-token krävs alltid.
        if huvud.get("origin") == "null" and huvud.get("sec-fetch-site") in (None, "same-origin"):
            huvud.pop("origin")
        kontroll = self.a.kraver_inloggning(
            Forfragan(req.metod, req.sokvag, req.query, huvud, req.kropp)
        )
        if not kontroll.tillaten or kontroll.inloggad is None:
            logg.warning(
                "oauth godkännande nekat klient=%s origin=%s sec_fetch_site=%s",
                klient["id"], "saknas" if "origin" not in huvud else ("null" if huvud["origin"] == "null" else "annan"),
                huvud.get("sec-fetch-site", "saknas"),
            )
            return kontroll.svar or _felsida("Inloggning krävs.", 401)
        if fel is not None:
            logg.warning("oauth godkännande avvisat klient=%s fel=%s", klient["id"], fel)
            return self._tillbaka(p, {"error": fel})
        konto_id = kontroll.inloggad.konto_id
        if p.get("beslut") != "tillat":
            logg.info("oauth nekad av konto=%s klient=%s", konto_id, klient["id"])
            return self._tillbaka(p, {"error": "access_denied"})
        kod = self.f.skapa_kod(
            klient["id"], konto_id, p["redirect_uri"], p["code_challenge"], self.resurs
        )
        logg.info("oauth kod utfärdad konto=%s klient=%s", konto_id, klient["id"])
        return self._tillbaka(p, {"code": kod})

    # --- token --------------------------------------------------------------------------------

    def _token(self, req: Forfragan) -> Svar:
        if req.metod != "POST":
            return _fel(405, "metod", "Metod ej tillåten")
        if not self.token_grans.tillat(_klient_ip(req)):
            return _oauth_fel(429, "temporarily_unavailable", "För många försök", [("Retry-After", "60")])
        p = _form(req)
        klient_id = p.get("client_id", "")
        if self.f.klient(klient_id) is None:
            return _oauth_fel(401, "invalid_client", "Okänd eller återkallad klient")
        try:
            typ = p.get("grant_type")
            if typ == "authorization_code":
                svar = self.f.byt_kod(
                    p.get("code", ""),
                    klient_id,
                    p.get("redirect_uri", ""),
                    p.get("code_verifier", ""),
                    p.get("resource"),
                )
            elif typ == "refresh_token":
                svar = self.f.forny(p.get("refresh_token", ""), klient_id, p.get("resource"))
            else:
                return _oauth_fel(400, "unsupported_grant_type", "grant_type stöds inte")
        except OauthFel as exc:
            logg.info("oauth token nekat klient=%s kod=%s", klient_id, exc.kod)
            return _oauth_fel(exc.status, exc.kod, exc.text)
        logg.info("oauth token utfärdad grant=%s typ=%s", svar["grant_id"], typ)
        return _json(
            200,
            {
                "access_token": svar["access_token"],
                "token_type": "Bearer",
                "expires_in": svar["expires_in"],
                "refresh_token": svar["refresh_token"],
                "scope": SCOPE,
            },
            [("Pragma", "no-cache")],
        )

    def _aterkalla(self, req: Forfragan) -> Svar:
        if req.metod != "POST":
            return _fel(405, "metod", "Metod ej tillåten")
        if not self.token_grans.tillat(_klient_ip(req)):
            return _oauth_fel(429, "temporarily_unavailable", "För många försök", [("Retry-After", "60")])
        self.f.aterkalla_med_token(_form(req).get("token", ""))
        return _json(200, {}, [("Pragma", "no-cache")])


# --------------------------------------------------------------- /konto/oauth (anslutna appar)


def hantera_oauth_anslutningar(adapter: KontoAdapter, req: Forfragan, delar: list[str]) -> Svar:
    """`delar` är sökvägen efter `/konto/oauth`. Bara session och CSRF: en token kan inte
    ändra sina egna anslutningar."""
    from konto.kontoapi import _id_ok, _inloggad, _metod_ej_tillaten

    konto_id, nekad = _inloggad(adapter, req)
    if konto_id is None:
        assert nekad is not None  # noqa: S101
        return nekad
    forrad = adapter.oauth
    assert forrad is not None  # noqa: S101
    if not delar:
        if req.metod != "GET":
            return _metod_ej_tillaten()
        return _json(200, {"anslutningar": forrad.lista(konto_id)})
    if len(delar) != 1 or not _id_ok(delar[0]):
        return _fel(404, "okand", "Okänd ändpunkt")
    if req.metod != "DELETE":
        return _metod_ej_tillaten()
    if not forrad.aterkalla_grant(konto_id, delar[0]):
        return _fel(404, "okand", "Okänd anslutning")
    logg.info("oauth anslutning aterkallad konto=%s grant=%s", konto_id, delar[0])
    return _json(200, {"status": "aterkallad", "id": delar[0]})


__all__ = ["OauthApi", "hantera_oauth_anslutningar", "pkce_utmaning"]
