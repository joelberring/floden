"""Sessioner, CSRF, utloggning, `kraver_inloggning` och läget `inloggning = "av"`."""

from __future__ import annotations

import json
from pathlib import Path

from konto.adapter import Forfragan, KontoAdapter
from konto.konfig import KontoKonfig
from tests.konto.conftest import ANNA, KAK, ORIGIN, Miljo, Webblasare, bygg_konfig, rubrik
from tests.konto.test_inloggning import bjud_in_anna


def inloggad(miljo: Miljo) -> Webblasare:
    bjud_in_anna(miljo)
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 302
    return webb


def begaran(webb: Webblasare, metod: str, sokvag: str, **extra: str) -> Forfragan:
    h = {"cookie": "; ".join(f"{k}={v}" for k, v in webb.kakor.items()), **extra}
    return Forfragan.fran_ra(metod, sokvag, h)


def test_lage_av_ar_standard_och_allt_fungerar_utan_inloggning(tmp_path: Path) -> None:
    assert KontoKonfig().inloggning == "av"
    adapter = KontoAdapter(KontoKonfig(databas=tmp_path / "ska-inte-skapas.sqlite"))
    assert not adapter.aktiv
    kontroll = adapter.kraver_inloggning(Forfragan.fran_ra("POST", "/api/kor", {}))
    assert kontroll.tillaten and kontroll.inloggad is None and kontroll.svar is None
    assert adapter.kraver_projektbehorighet(
        Forfragan.fran_ra("GET", "/api/omraden/x/lager", {}), "p1"
    ).tillaten
    jag = adapter.hantera_konto("GET", "/konto/jag", {}, {})
    assert jag[0] == 200 and json.loads(jag[2]) == {"inloggning": "av"}
    assert adapter.hantera_konto("GET", "/konto/logga-in/google", {}, {})[0] == 404
    assert not (tmp_path / "ska-inte-skapas.sqlite").exists()  # ingen databas i läget av


def test_utan_session_ger_401_och_oppna_sokvagar_slipper_igenom(miljo: Miljo) -> None:
    a = miljo.adapter
    nekad = a.kraver_inloggning(Forfragan.fran_ra("GET", "/api/omraden", {}))
    assert not nekad.tillaten and nekad.svar is not None and nekad.svar[0] == 401
    assert a.kraver_inloggning(Forfragan.fran_ra("GET", "/api/halsa", {})).tillaten
    assert a.kraver_inloggning(Forfragan.fran_ra("OPTIONS", "/api/kor", {})).tillaten
    assert a.hantera_konto("GET", "/konto/jag", {}, {})[0] == 401


def test_get_med_session_slipper_igenom_och_ger_konto(miljo: Miljo) -> None:
    webb = inloggad(miljo)
    kontroll = miljo.adapter.kraver_inloggning(begaran(webb, "GET", "/api/omraden"))
    assert kontroll.tillaten and kontroll.inloggad is not None
    assert kontroll.inloggad.namn == "Anna Exempel"


def test_post_kraver_csrf_i_bade_kaka_och_header(miljo: Miljo) -> None:
    webb = inloggad(miljo)
    a = miljo.adapter
    # Ingen header
    saknas = a.kraver_inloggning(begaran(webb, "POST", "/api/kor"))
    assert not saknas.tillaten and saknas.svar is not None and saknas.svar[0] == 403
    # Fel header
    fel = a.kraver_inloggning(begaran(webb, "POST", "/api/kor", **{"x-floden-csrf": "A" * 43}))
    assert not fel.tillaten
    # Header utan kaka (en kaka som en annan värd skulle ha satt saknas)
    utan_kaka = Forfragan.fran_ra(
        "POST",
        "/api/kor",
        {
            "cookie": f"{KAK.session}={webb.kakor[KAK.session]}",
            "x-floden-csrf": webb.kakor[KAK.csrf],
        },
    )
    assert not a.kraver_inloggning(utan_kaka).tillaten
    # Rätt par
    ok = a.kraver_inloggning(
        begaran(webb, "POST", "/api/kor", **{"x-floden-csrf": webb.kakor[KAK.csrf]})
    )
    assert ok.tillaten
    # Rätt par men annan sajt som Origin
    cross = a.kraver_inloggning(
        begaran(
            webb,
            "POST",
            "/api/kor",
            **{"x-floden-csrf": webb.kakor[KAK.csrf], "origin": "https://evil.test"},
        )
    )
    assert not cross.tillaten and cross.svar is not None and cross.svar[0] == 403
    egen = a.kraver_inloggning(
        begaran(
            webb, "POST", "/api/kor", **{"x-floden-csrf": webb.kakor[KAK.csrf], "origin": ORIGIN}
        )
    )
    assert egen.tillaten


def test_csrf_fran_en_annan_session_duger_inte(miljo: Miljo) -> None:
    a = inloggad(miljo)
    miljo.klocka.gå(1)
    b = miljo.webblasare()
    assert b.logga_in(miljo.idp, ANNA)[0] == 302
    kors = Forfragan.fran_ra(
        "POST",
        "/api/kor",
        {
            "cookie": f"{KAK.session}={a.kakor[KAK.session]}; {KAK.csrf}={b.kakor[KAK.csrf]}",
            "x-floden-csrf": b.kakor[KAK.csrf],
        },
    )
    assert not miljo.adapter.kraver_inloggning(kors).tillaten


def test_utloggning_kraver_csrf_och_ater_kallar_sessionen(miljo: Miljo) -> None:
    webb = inloggad(miljo)
    token = webb.kakor[KAK.session]
    # Utan CSRF och utan Origin: nekas, sessionen lever
    assert webb.anrop("POST", "/konto/logga-ut")[0] == 403
    assert webb.anrop("GET", "/konto/jag")[0] == 200
    # Fel Origin
    assert webb.anrop("POST", "/konto/logga-ut", headers={"Origin": "https://evil.test"})[0] == 403
    assert webb.anrop("GET", "/konto/jag")[0] == 200
    # GET på logga-ut är inte tillåtet
    assert webb.anrop("GET", "/konto/logga-ut")[0] == 405
    # Rätt CSRF
    svar = webb.anrop("POST", "/konto/logga-ut", headers=webb.csrf_headers())
    assert svar[0] == 200
    assert "Max-Age=0" in webb.satta[KAK.session] and KAK.session not in webb.kakor
    # Den gamla kakan är ogiltig även om någon sparat den
    webb.kakor[KAK.session] = token
    assert webb.anrop("GET", "/konto/jag")[0] == 401
    assert not miljo.adapter.kraver_inloggning(begaran(webb, "GET", "/api/omraden")).tillaten


def test_utloggning_med_exakt_origin_utan_csrf(miljo: Miljo) -> None:
    """Som i O-Tid: att logga ut någon är ofarligt, exakt Origin duger."""
    webb = inloggad(miljo)
    del webb.kakor[KAK.csrf]
    assert webb.anrop("POST", "/konto/logga-ut", headers={"Origin": ORIGIN})[0] == 200
    assert webb.anrop("GET", "/konto/jag")[0] == 401


def test_session_gar_ut(miljo: Miljo) -> None:
    webb = inloggad(miljo)
    miljo.klocka.gå(12 * 3600 - 1)
    assert webb.anrop("GET", "/konto/jag")[0] == 200
    miljo.klocka.gå(2)
    assert webb.anrop("GET", "/konto/jag")[0] == 401


def test_sparrat_konto_tappar_sessionen_och_kan_inte_logga_in(miljo: Miljo) -> None:
    webb = inloggad(miljo)
    konto = miljo.register.konto_via_epost("anna@example.test")
    assert konto is not None
    assert miljo.adapter.sessioner is not None
    miljo.adapter.sessioner.sparra_konto(konto.id)
    assert webb.anrop("GET", "/konto/jag")[0] == 401
    nytt = miljo.webblasare()
    svar = nytt.logga_in(miljo.idp, ANNA)
    assert svar[0] == 403 and '"sparrad"' in svar[2].decode()


def test_forfalskad_eller_trasig_sessionskaka(miljo: Miljo) -> None:
    webb = inloggad(miljo)
    riktig = webb.kakor[KAK.session]
    for trasig in ("", "fs1.x.y", riktig[:-1] + ("A" if riktig[-1] != "A" else "B"), "A" * 200):
        webb.kakor[KAK.session] = trasig
        assert webb.anrop("GET", "/konto/jag")[0] == 401
    # Samma namn två gånger är otydligt och räknas som ingen kaka alls
    h = {"cookie": f"{KAK.session}={riktig}; {KAK.session}={riktig}"}
    assert miljo.adapter.hantera_konto("GET", "/konto/jag", {}, h)[0] == 401


def test_okanda_sokvagar_och_metoder(miljo: Miljo) -> None:
    a = miljo.adapter
    assert a.hantera_konto("GET", "/konto/finns-inte", {}, {})[0] == 404
    assert a.hantera_konto("GET", "/konto/logga-in/okand", {}, {})[0] == 404
    assert a.hantera_konto("GET", "/konto/logga-in/GOOGLE%00", {}, {})[0] == 404
    assert a.hantera_konto("POST", "/konto/logga-in/google", {}, {})[0] == 405
    assert a.hantera_konto("GET", "/api/halsa", {}, {})[0] == 404
    assert a.ager_sokvag("/konto/jag") and not a.ager_sokvag("/kontor")


def test_konfigurerbar_secure_for_localhost(tmp_path: Path) -> None:
    konf = bygg_konfig(tmp_path, cookie_secure=False, offentlig_url="http://127.0.0.1:8765")
    assert not konf.cookie_secure and konf.redirect_uri("google") == (
        "http://127.0.0.1:8765/konto/callback/google"
    )
    assert rubrik((200, [("Content-Type", "x")], b""), "content-type") == "x"
