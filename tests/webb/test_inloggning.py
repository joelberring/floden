"""Inloggning i webbens HTTP-väg (docs/KONTO.md avsnitt 8): av/på, 401, CSRF och CORS.

Använder den falska OIDC-leverantören i `tests/konto`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from konto.adapter import KontoAdapter
from konto.konfig import KontoKonfig
from tests.konto.conftest import ANNA, KAK, ORIGIN, Miljo, Webblasare
from tests.konto.test_inloggning import bjud_in_anna
from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]


def _app(tmp_path: Path, konto: KontoAdapter) -> FlodenApiApp:
    klient = tmp_path / "dist"
    klient.mkdir()
    (klient / "index.html").write_text("<!doctype html><title>Floden</title>", encoding="utf-8")
    (tmp_path / "hemligt.txt").write_text("nej", encoding="utf-8")
    return FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=konto,
        klientmapp=klient,
    )


def _http(
    app: FlodenApiApp,
    metod: str,
    sokvag: str,
    webb: Webblasare | None = None,
    extra: dict[str, str] | None = None,
    kropp: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    rubriker = dict(extra or {})
    if webb is not None and webb.kakor:
        rubriker["Cookie"] = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
    status, hs, k = app.hantera_http(metod, sokvag, rubriker, lambda: kropp)
    return status, {n.lower(): v for n, v in hs}, k


def test_inloggning_av_ar_oforandrat_beteende(tmp_path: Path) -> None:
    app = _app(tmp_path, KontoAdapter(KontoKonfig()))
    status, h, _ = _http(app, "GET", "/api/halsa")
    assert status == 200 and h["access-control-allow-origin"] == "*"
    status, _, k = _http(app, "GET", "/konto/jag")
    assert status == 200 and json.loads(k) == {"inloggning": "av"}
    status, h, k = _http(app, "GET", "/")
    assert status == 200 and h["content-type"].startswith("text/html") and b"Floden" in k
    # POST utan CSRF-huvud går igenom när inloggningen är av.
    status, _, _ = _http(app, "POST", "/api/fragor/finns-inte/plan")
    assert status == 404


def test_statiska_filer_stoppar_sokvagsintrang(tmp_path: Path) -> None:
    app = _app(tmp_path, KontoAdapter(KontoKonfig()))
    for s in ("/../hemligt.txt", "/%2e%2e/hemligt.txt", "/..%2fhemligt.txt"):
        status, _, k = _http(app, "GET", s)
        assert b"nej" not in k, s
        assert status != 200 or k.startswith(b"<!doctype"), s


def test_inloggning_pa_kraver_inloggning_pa_api_men_inte_pa_klienten(
    tmp_path: Path, miljo: Miljo
) -> None:
    app = _app(tmp_path, miljo.adapter)
    assert _http(app, "GET", "/")[0] == 200  # klientfilerna innehåller ingen data
    for metod, sokvag in (("GET", "/api/omraden"), ("GET", "/api/demo"), ("GET", "/api/fragor")):
        status, _, k = _http(app, metod, sokvag)
        assert status == 401, sokvag
        assert json.loads(k)["fel"]
    assert _http(app, "GET", "/konto/jag")[0] == 401
    assert _http(app, "GET", "/api/halsa")[0] == 200  # hälsokontrollen är öppen (docs/KONTO.md)


def test_inloggad_far_api_utan_cors_och_post_kraver_csrf(tmp_path: Path, miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    app = _app(tmp_path, miljo.adapter)
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 302
    origin = {"Origin": ORIGIN}

    status, h, _ = _http(app, "GET", "/api/omraden", webb)
    assert status == 200
    assert not any(n.startswith("access-control-") for n in h)  # inget Allow-Origin: *

    status, _, k = _http(app, "GET", "/konto/jag", webb)
    assert status == 200 and json.loads(k)["konto"]["namn"] == "Anna Exempel"

    # Utan CSRF-huvud: 403, och frågan har inte körts.
    status, _, k = _http(app, "POST", "/api/fragor/finns-inte/kor", webb, origin)
    assert status == 403 and json.loads(k)["fel"]
    # Fel CSRF-värde: 403.
    status, _, _ = _http(
        app, "POST", "/api/fragor/finns-inte/kor", webb, {**origin, "X-Floden-CSRF": "fel"}
    )
    assert status == 403
    # Rätt CSRF-värde men fel ursprung: 403.
    status, _, _ = _http(
        app,
        "POST",
        "/api/fragor/finns-inte/kor",
        webb,
        {"Origin": "https://annan.test", **webb.csrf_headers()},
    )
    assert status == 403
    # Rätt CSRF och ursprung: släpps igenom till API:t (frågan finns inte -> 404 från API:t).
    status, _, _ = _http(
        app, "POST", "/api/fragor/finns-inte/kor", webb, {**origin, **webb.csrf_headers()}
    )
    assert status == 404


def test_utloggad_session_far_401_igen(tmp_path: Path, miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    app = _app(tmp_path, miljo.adapter)
    webb = miljo.webblasare()
    webb.logga_in(miljo.idp, ANNA)
    assert _http(app, "GET", "/api/omraden", webb)[0] == 200
    ut = _http(app, "POST", "/konto/logga-ut", webb, {"Origin": ORIGIN, **webb.csrf_headers()})
    assert ut[0] in (200, 204, 302)
    webb.kakor.pop(KAK.session, None)
    assert _http(app, "GET", "/api/omraden", webb)[0] == 401


def test_kroppen_laeses_inte_innan_inloggningen_kontrollerats(tmp_path: Path, miljo: Miljo) -> None:
    app = _app(tmp_path, miljo.adapter)

    def las() -> bytes:
        raise AssertionError("kroppen får inte läsas för en oinloggad")

    status, _, _ = app.hantera_http("POST", "/api/fragor/x/kor", {}, las)
    assert status == 401


def test_klientbygge_hoppar_over_aktuell_klient_och_bygger_annars(tmp_path: Path) -> None:
    from webb.klientbygge import KlientByggeFel, sakerstall_klient

    webb = tmp_path / "webb"
    (webb / "src").mkdir(parents=True)
    (webb / "package.json").write_text("{}", encoding="utf-8")
    (webb / "src" / "main.ts").write_text("//", encoding="utf-8")
    anrop: list[list[str]] = []

    def kor(kommando: list[str], cwd: Path) -> int:
        anrop.append(kommando)
        if kommando[-1] == "build":
            (webb / "dist").mkdir(exist_ok=True)
            (webb / "dist" / "index.html").write_text("x", encoding="utf-8")
        return 0

    assert sakerstall_klient(webb, logg=lambda _t: None, kor=kor) is True
    assert any("build" in a for a in anrop)
    antal = len(anrop)
    assert sakerstall_klient(webb, logg=lambda _t: None, kor=kor) is False
    assert len(anrop) == antal
    with pytest.raises(KlientByggeFel):
        sakerstall_klient(tmp_path / "saknas", logg=lambda _t: None, kor=kor)
