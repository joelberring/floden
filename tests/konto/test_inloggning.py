"""Kodflödet mot en falsk Google: lyckad inloggning och varje sätt det ska misslyckas på."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import jwt
import pytest

from konto.leverantor import google
from konto.oidc import KLOCKTOLERANS_S, OidcFel, OidcKlient, pkce_challenge
from tests.konto.conftest import ANNA, KAK, Miljo, rubrik
from tests.konto.fake_idp import FalskIdp, Klocka, rsa_nyckel


def bjud_in_anna(miljo: Miljo, epost: str = "anna@example.test") -> str:
    """En organisation, en admin och en inbjudan till `epost`. Returnerar projektets id."""
    r = miljo.register
    org = r.skapa_organisation("Exempelkommun")
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, org)
    projekt = r.skapa_projekt(org, "Provområdet")
    r.bjud_in(admin, projekt, epost)
    return projekt


def test_lyckad_inloggning_ger_session_med_ratt_kakattribut(miljo: Miljo) -> None:
    projekt = bjud_in_anna(miljo)
    webb = miljo.webblasare()
    svar = webb.logga_in(miljo.idp, ANNA, atervand="/omraden")
    assert svar[0] == 302
    assert rubrik(svar, "Location") == "/omraden"
    session = webb.satta[KAK.session]
    assert session.startswith(f"{KAK.session}=fs1.")
    for attribut in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=43200"):
        assert attribut in session
    assert "Domain" not in session
    csrf = webb.satta[KAK.csrf]
    assert "HttpOnly" not in csrf and "Secure" in csrf and "SameSite=Lax" in csrf
    assert "Max-Age=0" in webb.satta[KAK.inloggning]  # bindningskakan är förbrukad

    jag = webb.anrop("GET", "/konto/jag")
    assert jag[0] == 200
    data = json.loads(jag[2])
    assert data["inloggning"] == "pa" and data["konto"]["namn"] == "Anna Exempel"
    assert data["projekt"] == [{"id": projekt, "namn": "Provområdet", "roll": "lasare"}]


def test_cookie_utan_secure_pa_localhost(tmp_path: Any, klocka: Klocka, idp: FalskIdp) -> None:
    from konto.adapter import KontoAdapter
    from tests.konto.conftest import Webblasare, bygg_konfig

    lev = google(
        idp.klient_id, idp.hemlighet, discovery_url=idp.discovery_url, issuers=(idp.issuer,)
    )
    konf = bygg_konfig(
        tmp_path, cookie_secure=False, offentlig_url="http://localhost:8765", nya_konton="oppen"
    )
    adapter = KontoAdapter(konf, hamtare=idp, klocka=klocka, leverantorer={"google": lev})
    webb = Webblasare(adapter)
    assert webb.logga_in(idp, ANNA)[0] == 302
    session = webb.satta["floden_session"]
    assert "HttpOnly" in session and "Secure" not in session and "SameSite=Lax" in session


def test_pkce_s256_och_auktoriseringsurl(miljo: Miljo) -> None:
    webb = miljo.webblasare()
    start = webb.anrop("GET", "/konto/logga-in/google")
    url = rubrik(start, "Location") or ""
    assert "code_challenge_method=S256" in url and "scope=openid+email+profile" in url
    assert "redirect_uri=https%3A%2F%2Ffloden.test%2Fkonto%2Fcallback%2Fgoogle" in url
    assert re.search(r"state=[A-Za-z0-9_-]{43}&nonce=[A-Za-z0-9_-]{43}", url)
    # RFC 7636 appendix B
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_fel_state_nekas_och_state_ar_engangs(miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    webb = miljo.webblasare()
    start = webb.anrop("GET", "/konto/logga-in/google")
    kod, state = miljo.idp.godkann(rubrik(start, "Location") or "", ANNA)
    fel = webb.anrop("GET", "/konto/callback/google", {"code": [kod], "state": ["x" * 43]})
    assert fel[0] == 400 and KAK.session not in webb.kakor
    # Engångs: ett lyckat callback förbrukar state, och samma länk spelas upp igen.
    webb = miljo.webblasare()
    start = webb.anrop("GET", "/konto/logga-in/google")
    kod, state = miljo.idp.godkann(rubrik(start, "Location") or "", ANNA)
    kakor_fore = dict(webb.kakor)
    assert webb.anrop("GET", "/konto/callback/google", {"code": [kod], "state": [state]})[0] == 302
    webb.kakor = kakor_fore  # en angripare som spelar upp samma länk igen
    kod2, _ = miljo.idp.godkann(rubrik(start, "Location") or "", ANNA)
    igen = webb.anrop("GET", "/konto/callback/google", {"code": [kod2], "state": [state]})
    assert igen[0] == 400


def test_callback_utan_bindningskaka_nekas(miljo: Miljo) -> None:
    """Login-CSRF: en länk med angriparens state/kod i en annan webbläsare ger ingen session."""
    bjud_in_anna(miljo)
    angripare = miljo.webblasare()
    start = angripare.anrop("GET", "/konto/logga-in/google")
    kod, state = miljo.idp.godkann(rubrik(start, "Location") or "", ANNA)
    offer = miljo.webblasare()  # saknar angriparens bindningskaka
    svar = offer.anrop("GET", "/konto/callback/google", {"code": [kod], "state": [state]})
    assert svar[0] == 400 and KAK.session not in offer.kakor
    offer.kakor[KAK.inloggning] = "A" * 43  # fel bindning
    svar = offer.anrop("GET", "/konto/callback/google", {"code": [kod], "state": [state]})
    assert svar[0] == 400 and KAK.session not in offer.kakor


def test_state_for_gammalt_nekas(miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    webb = miljo.webblasare()
    start = webb.anrop("GET", "/konto/logga-in/google")
    kod, state = miljo.idp.godkann(rubrik(start, "Location") or "", ANNA)
    miljo.klocka.gå(601)
    svar = webb.anrop("GET", "/konto/callback/google", {"code": [kod], "state": [state]})
    assert svar[0] == 400


@pytest.mark.parametrize(
    ("paverka", "kod", "status"),
    [
        ({"nonce": "fel-nonce-" + "x" * 33}, "nonce", 403),
        ({"nonce": None}, "nonce", 403),
        ({"aud": "annan-klient"}, "id-token", 403),
        ({"aud": ["klient-123", "annan"]}, "id-token", 403),  # azp saknas vid flera aud
        ({"azp": "annan-klient"}, "id-token", 403),
        ({"iss": "https://idp.example.evil"}, "id-token", 403),
        ({"iss": None}, "id-token", 403),
        ({"exp": 1_799_990_000}, "utgangen", 403),
        ({"iat": 1_800_000_000 + 3600, "exp": 1_800_000_000 + 7200}, "id-token", 403),
        ({"sub": None}, "id-token", 403),
        ({"email_verified": False}, "epost-overifierad", 403),
        ({"email_verified": None}, "epost-overifierad", 403),
        ({"email": None}, "epost-overifierad", 403),
    ],
)
def test_felaktig_id_token_nekas(
    miljo: Miljo, caplog: pytest.LogCaptureFixture, paverka: dict[str, Any], kod: str, status: int
) -> None:
    bjud_in_anna(miljo)
    miljo.idp.paverka = paverka
    webb = miljo.webblasare()
    with caplog.at_level(logging.INFO, logger="konto"):
        svar = webb.logga_in(miljo.idp, ANNA)
    assert svar[0] == status, svar
    assert f'"kod": "{kod}"' in svar[2].decode()
    assert KAK.session not in webb.kakor and KAK.csrf not in webb.kakor
    with miljo.register.db.transaktion() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM konto WHERE epost = 'anna@example.test'").fetchone()[0]
            == 0
        )
    assert f"kod={kod}" in caplog.text


def test_email_verified_som_text_true_godtas(miljo: Miljo) -> None:
    """Google skickar ibland `"true"` som text (O-Tid godtar båda)."""
    bjud_in_anna(miljo)
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, {**ANNA, "email_verified": "true"})[0] == 302
    miljo.klocka.gå(1)
    webb2 = miljo.webblasare()
    assert webb2.logga_in(miljo.idp, {**ANNA, "email_verified": "false"})[0] == 403


def test_nyckelrotation_och_begransad_omhamtning(miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    idp = miljo.idp
    assert miljo.webblasare().logga_in(idp, ANNA)[0] == 302  # k1 hämtad och cachad
    antal = idp.anrop.count(idp.jwks_url)
    assert antal == 1

    # Leverantören roterar: k2 publiceras, k1 tas bort, nya token signeras med k2.
    idp.nycklar = {"k2": rsa_nyckel("k2")}
    idp.signera_med = "k2"
    miljo.klocka.gå(5)
    assert (
        miljo.webblasare().logga_in(idp, ANNA)[0] == 403
    )  # <60 s sedan hämtningen: ingen omhämtning
    assert idp.anrop.count(idp.jwks_url) == antal
    miljo.klocka.gå(60)
    assert miljo.webblasare().logga_in(idp, ANNA)[0] == 302  # okänd kid -> omhämtning -> k2
    assert idp.anrop.count(idp.jwks_url) == antal + 1

    # En token med den gamla (borttagna) nyckeln godtas inte längre.
    idp.signera_med = "k1"
    miljo.klocka.gå(61)
    assert miljo.webblasare().logga_in(idp, ANNA)[0] == 403
    assert idp.anrop.count(idp.jwks_url) == antal + 2


def test_okand_kid_kan_inte_driva_hamtningar(miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    idp = miljo.idp
    idp.signera_med = "okand"
    for _ in range(5):
        assert miljo.webblasare().logga_in(idp, ANNA)[0] == 403
    assert idp.anrop.count(idp.jwks_url) == 1


def test_signatur_med_fel_nyckel_nekas(miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    miljo.idp.falsk_nyckel = rsa_nyckel("angriparen")
    assert miljo.webblasare().logga_in(miljo.idp, ANNA)[0] == 403


def test_algoritm_none_och_hs256_nekas(miljo: Miljo) -> None:
    lev = google(
        miljo.idp.klient_id,
        miljo.idp.hemlighet,
        discovery_url=miljo.idp.discovery_url,
        issuers=(miljo.idp.issuer,),
    )
    klient = OidcKlient(lev, "https://floden.test/konto/callback/google", miljo.idp, miljo.klocka)
    nu = int(miljo.klocka())
    nyttlast = {
        "iss": miljo.idp.issuer,
        "aud": lev.klient_id,
        "sub": "1",
        "iat": nu,
        "exp": nu + 600,
        "nonce": "n" * 43,
        "email": "a@example.test",
        "email_verified": True,
    }
    nycklar: list[tuple[str, Any]] = [("none", None), ("HS256", "h" * 40)]
    for alg, nyckel in nycklar:
        token = jwt.encode(nyttlast, nyckel, algorithm=alg, headers={"kid": "k1"})
        with pytest.raises(OidcFel) as fel:
            klient.kontrollera_id_token(token, "n" * 43)
        assert fel.value.kod == "id-token"
    # Kontroll: samma nyttolast signerad riktigt godtas.
    riktig = jwt.encode(nyttlast, rsa_nyckel("k1"), algorithm="RS256", headers={"kid": "k1"})
    assert klient.kontrollera_id_token(riktig, "n" * 43).subject == "1"


def test_klocktolerans_vid_exp(miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    nu = int(miljo.klocka())
    miljo.idp.paverka = {"exp": nu - KLOCKTOLERANS_S + 5}  # inom toleransen
    assert miljo.webblasare().logga_in(miljo.idp, ANNA)[0] == 302


def test_kodutbyte_fel_ger_502_utan_session(miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    miljo.idp.hemlighet = "andra-hemligheten"  # leverantören avvisar klienten
    webb = miljo.webblasare()
    svar = webb.logga_in(miljo.idp, ANNA)
    assert svar[0] == 502 and KAK.session not in webb.kakor


def test_idp_fel_vid_avbruten_inloggning(miljo: Miljo) -> None:
    webb = miljo.webblasare()
    start = webb.anrop("GET", "/konto/logga-in/google")
    _, state = miljo.idp.godkann(rubrik(start, "Location") or "", ANNA)
    svar = webb.anrop(
        "GET", "/konto/callback/google", {"error": ["access_denied"], "state": [state]}
    )
    assert svar[0] == 400 and '"idp-fel"' in svar[2].decode()


def test_discovery_med_http_eller_fel_issuer_nekas(miljo: Miljo) -> None:
    miljo.idp.discovery_issuer = "https://annan-utfardare.test"
    assert miljo.webblasare().anrop("GET", "/konto/logga-in/google")[0] == 502


@pytest.mark.parametrize(
    "farligt", ["//evil.test", "https://evil.test/x", "/ok\\evil", "/a\r\nSet-Cookie: x=1", "x"]
)
def test_atervand_kan_inte_bli_omdirigering_till_annan_sajt(miljo: Miljo, farligt: str) -> None:
    bjud_in_anna(miljo)
    svar = miljo.webblasare().logga_in(miljo.idp, ANNA, atervand=farligt)
    assert svar[0] == 302 and rubrik(svar, "Location") == "/"


def test_inga_personuppgifter_i_loggar_eller_handelser(
    miljo: Miljo, caplog: pytest.LogCaptureFixture
) -> None:
    bjud_in_anna(miljo)
    webb = miljo.webblasare()
    with caplog.at_level(logging.DEBUG):
        webb.logga_in(miljo.idp, ANNA)
        miljo.idp.paverka = {"email_verified": False}
        miljo.webblasare().logga_in(miljo.idp, ANNA)
        miljo.idp.paverka = {}
        webb.anrop("POST", "/konto/logga-ut", headers=webb.csrf_headers())
    text = caplog.text
    assert "konto=" in text
    for hemligt in ("anna@example.test", "Anna", "Exempel", "1001", miljo.idp.hemlighet):
        assert hemligt not in text
    with miljo.register.db.transaktion() as c:
        detaljer = " ".join(r["detalj"] for r in c.execute("SELECT detalj FROM handelse"))
    assert "@" not in detaljer and "Anna" not in detaljer
