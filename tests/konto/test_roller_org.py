"""Organisationsroller: medlem < ledare < organisationsadmin < ägare.

Ledare läser, organisationsadmin och ägare utreder i alla organisationens projekt (och inga andras);
superadmin får fortfarande ingen projektdata. Inbjudan till organisationen, serverkommandona och
migreringen från en databas utan de nya rollerna.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from konto import databas as db_modul
from konto.admin import main as admin_main
from konto.databas import Databas
from konto.register import (
    ORGROLLER,
    BehorighetFel,
    Register,
    RegisterFel,
    projektroll_fran_organisationsroll,
)
from tests.konto.conftest import ORIGIN, Miljo, Webblasare


def _claims(namn: str) -> dict[str, Any]:
    return {
        "sub": f"sub-{namn}-qz",
        "email": f"{namn}@example.test",
        "email_verified": True,
        "name": namn.capitalize(),
    }


def _epost(namn: str) -> str:
    return f"{namn}@example.test"


class Varld:
    """Två organisationer med ett projekt var, och ett konto per roll i den första."""

    def __init__(self, miljo: Miljo) -> None:
        self.miljo = miljo
        r = miljo.register
        self.r = r
        self.org1, self.org2 = r.skapa_organisation("Floden"), r.skapa_organisation("Annan")
        self.p1, self.p1b = (
            r.skapa_projekt(self.org1, "Projekt ett", organisation_synlig=True),
            r.skapa_projekt(self.org1, "Ett b", organisation_synlig=True),
        )
        self.p2 = r.skapa_projekt(self.org2, "Projekt två", organisation_synlig=True)
        self.pp = r.skapa_projekt(self.org1, "Privat")  # förval: privat
        self.id: dict[str, str] = {}
        for namn in ("super", "agare", "admin", "ledare", "medlem", "utan", "agare2"):
            self.id[namn] = r.forbered_konto(_epost(namn))
        r.ge_superadmin(self.id["super"])
        # Ägaren skapas via skapa_organisation(agare=...), de andra via kommandona/registret.
        r2 = r.skapa_organisation("Tredje", agare=self.id["agare2"])
        self.org3 = r2
        self._ge(self.org1, "agare", "agare")
        self._ge(self.org1, "admin", "organisationsadmin")
        self._ge(self.org1, "ledare", "ledare")
        self._ge(self.org1, "medlem", "medlem")

    def _ge(self, org: str, namn: str, roll: str) -> None:
        with self.r.db.transaktion() as c:
            c.execute(
                "INSERT INTO medlemskap (konto_id, organisation_id, forvaltning_id, roll, kalla,"
                " skapad) VALUES (?, ?, NULL, ?, 'manuell', 0)",
                (self.id[namn], org, roll),
            )

    def webb(self, namn: str) -> Webblasare:
        w = self.miljo.webblasare()
        assert w.logga_in(self.miljo.idp, _claims(namn))[0] == 302
        konto = self.r.konto_via_epost(_epost(namn))
        assert konto is not None
        self.id[namn] = konto.id
        return w


def anrop(
    webb: Webblasare, metod: str, sokvag: str, data: Any = None
) -> tuple[int, dict[str, Any]]:
    h = {"Origin": ORIGIN, **(webb.csrf_headers() if metod != "GET" else {})}
    status, _, kropp = webb.anrop(
        metod, sokvag, headers=h, kropp=b"" if data is None else json.dumps(data).encode()
    )
    return status, json.loads(kropp)


@pytest.fixture
def v(miljo: Miljo) -> Varld:
    return Varld(miljo)


# --- rollordning och rätt i organisationens projekt ----------------------------------------


def test_rollordningen_ar_medlem_ledare_organisationsadmin_agare() -> None:
    assert ORGROLLER == ("medlem", "ledare", "organisationsadmin", "agare")
    assert [projektroll_fran_organisationsroll(r) for r in ORGROLLER] == [
        None,
        "lasare",
        "utredare",
        "utredare",
    ]
    assert projektroll_fran_organisationsroll(None) is None


def test_organisationsroller_ger_ratt_i_alla_organisationens_projekt(v: Varld) -> None:
    r = v.r
    for projekt in (v.p1, v.p1b):
        assert r.effektiv_projektroll(v.id["agare"], projekt).roll == "utredare"
        assert r.effektiv_projektroll(v.id["admin"], projekt).roll == "utredare"
        assert r.effektiv_projektroll(v.id["ledare"], projekt).roll == "lasare"
        assert r.effektiv_projektroll(v.id["medlem"], projekt).roll is None
        assert r.effektiv_projektroll(v.id["utan"], projekt).roll is None
    assert r.effektiv_projektroll(v.id["admin"], v.p1).kalla == "organisation"
    # Prövas med minsta roll: ledare läser men utreder inte.
    assert r.har_projektroll(v.id["ledare"], v.p1, "lasare")
    assert not r.har_projektroll(v.id["ledare"], v.p1, "utredare")
    assert r.har_projektroll(v.id["admin"], v.p1, "utredare")
    assert not r.har_projektroll(v.id["medlem"], v.p1, "lasare")
    # Den uttryckliga rollen finns kvar som egen sak (administrationen visar den).
    assert r.projektroll(v.id["admin"], v.p1) is None


def test_ingen_ratt_i_andra_organisationers_projekt(v: Varld) -> None:
    r = v.r
    for namn in ("agare", "admin", "ledare", "medlem"):
        assert r.effektiv_projektroll(v.id[namn], v.p2).roll is None, namn
        assert not r.har_projektroll(v.id[namn], v.p2)
    # Ägaren av den tredje organisationen har rätt där men inte i de andra.
    assert r.effektiv_projektroll(v.id["agare2"], v.p1).roll is None


def test_superadmin_far_fortfarande_ingen_projektdata(v: Varld) -> None:
    r = v.r
    assert r.ar_superadmin(v.id["super"])
    for projekt in (v.p1, v.p2):
        assert r.effektiv_projektroll(v.id["super"], projekt).roll is None
        assert not r.har_projektroll(v.id["super"], projekt)
    assert v.miljo.adapter.har_roll(v.id["super"], v.p1) is False
    assert r.projekt_med_effektiv_roll(v.id["super"]) == []
    # Inte heller som ägare-utan-roll i organisationen: superadmin administrerar men läser inte.
    assert r.tillatna_organisationsroller(v.id["super"], v.org1) == list(ORGROLLER)


def test_uttrycklig_roll_och_organisationsroll_ger_den_hogsta(v: Varld) -> None:
    r = v.r
    r.ge_projektroll(v.id["admin"], v.id["ledare"], v.p1, "utredare")
    ef = r.effektiv_projektroll(v.id["ledare"], v.p1)
    assert (ef.roll, ef.kalla) == ("utredare", "projekt")
    assert r.effektiv_projektroll(v.id["ledare"], v.p1b).roll == "lasare"  # bara org-rollen där
    # En uttrycklig roll sänker aldrig det organisationen ger.
    r.ge_projektroll(v.id["admin"], v.id["admin"], v.p1, "lasare")
    assert r.effektiv_projektroll(v.id["admin"], v.p1).roll == "utredare"
    # En medlem kan få en uttrycklig roll som förut.
    r.ge_projektroll(v.id["admin"], v.id["medlem"], v.p1, "lasare")
    assert r.effektiv_projektroll(v.id["medlem"], v.p1).roll == "lasare"


def test_spärrat_konto_har_ingen_ratt(v: Varld) -> None:
    v.r.satt_sparrad(v.id["super"], v.id["admin"], True)
    assert v.r.effektiv_projektroll(v.id["admin"], v.p1).roll is None
    assert v.r.projekt_med_effektiv_roll(v.id["admin"]) == []


def test_atkomst_via_organisationsroll_loggas_med_konto_och_projekt_id(
    v: Varld, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="konto")
    assert v.miljo.adapter.har_roll(v.id["admin"], v.p1, "utredare")
    rader = [m for m in caplog.messages if "organisationsroll" in m]
    assert rader == [
        f"projektatkomst via organisationsroll konto={v.id['admin']} projekt={v.p1} roll=utredare"
    ]
    assert "@" not in rader[0] and "example" not in rader[0]
    # Uttrycklig roll loggas inte som organisationsåtkomst.
    caplog.clear()
    v.r.ge_projektroll(v.id["admin"], v.id["utan"], v.p1, "utredare")
    assert v.miljo.adapter.har_roll(v.id["utan"], v.p1, "utredare")
    assert not [m for m in caplog.messages if "organisationsroll" in m]


def test_projektlista_och_kontrollerna_ger_samma_svar(v: Varld) -> None:
    lista = {p["id"]: (p["roll"], p["kalla"]) for p in v.r.projekt_med_effektiv_roll(v.id["admin"])}
    assert lista == {v.p1: ("utredare", "organisation"), v.p1b: ("utredare", "organisation")}
    ledare = {p["id"]: p["roll"] for p in v.r.projekt_med_effektiv_roll(v.id["ledare"])}
    assert ledare == {v.p1: "lasare", v.p1b: "lasare"}
    assert v.r.projekt_med_effektiv_roll(v.id["medlem"]) == []
    # Varje projekt i listan stämmer med har_roll, och inget annat har rätt.
    for namn in ("agare", "admin", "ledare", "medlem", "utan", "super"):
        i_listan = {p["id"] for p in v.r.projekt_med_effektiv_roll(v.id[namn])}
        med_ratt = {
            p for p in (v.p1, v.p1b, v.p2) if v.miljo.adapter.har_roll(v.id[namn], p, "lasare")
        }
        assert i_listan == med_ratt, namn


# --- API och MCP ser samma sak ---------------------------------------------------------------


def test_api_och_mcp_ser_samma_projekt_och_kontroller(
    tmp_path: Path, v: Varld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from floden_mcp.karna_api import FlodenTjanst
    from tests.webb.test_inloggning import _app, _http
    from tests.webb.test_projekt_per_anvandare import _med_demo

    app = _app(tmp_path, v.miljo.adapter)
    _med_demo(app, monkeypatch)
    tjanst = FlodenTjanst(app, rot=tmp_path, mcp_mapp=tmp_path / "mcp")
    for namn, forvantat in (
        ("admin", {v.p1: "utredare", v.p1b: "utredare"}),
        ("ledare", {v.p1: "lasare", v.p1b: "lasare"}),
        ("medlem", {}),
    ):
        webb = v.webb(namn)
        status, _, kropp = _http(app, "GET", "/api/projekt", webb)
        assert status == 200
        api = {
            p["id"]: p["roll"] for p in json.loads(kropp)["projekt"] if p["id"] != "demokvarteret"
        }
        mcp = {
            p["id"]: p["roll"]
            for p in tjanst.projekt_lista(v.id[namn])["projekt"]
            if p["id"] != "demokvarteret"
        }
        assert api == mcp == forvantat, namn
        # Projektsidan i API:t: bara de man har rätt i.
        for projekt in (v.p1, v.p2, v.pp):
            kod = _http(app, "GET", f"/api/projekt/{projekt}", webb)[0]
            assert (kod == 200) == (projekt in forvantat), (namn, projekt)
    # Körningar: samma regel för webben (`konto_far_se_korning`) och MCP-verktygen (`har_roll`).
    monkeypatch.setattr(
        type(app), "projekt_for_korning", lambda self, kid: {"k1": v.p1, "k2": v.p2}[kid]
    )
    assert app.konto_far_se_korning(v.id["admin"], "k1")
    assert app.konto_far_se_korning(v.id["ledare"], "k1")
    assert not app.konto_far_se_korning(v.id["medlem"], "k1")
    assert not app.konto_far_se_korning(v.id["admin"], "k2")
    assert not app.konto_far_se_korning(v.id["super"], "k1")
    # Samma funktion som MCP-servern får som `behorighet` (floden_mcp/cli.py).
    assert app.konto.har_roll(v.id["ledare"], v.p1, "lasare")
    assert not app.konto.har_roll(v.id["ledare"], v.p1, "utredare")


def test_kraver_projektbehorighet_foljer_organisationsrollen(v: Varld) -> None:
    from konto.adapter import Forfragan

    for namn, projekt, minst, tillaten in (
        ("admin", "p1", "utredare", True),
        ("agare", "p1", "utredare", True),
        ("ledare", "p1", "lasare", True),
        ("ledare", "p1", "utredare", False),
        ("medlem", "p1", "lasare", False),
        ("admin", "p2", "lasare", False),
        ("super", "p1", "lasare", False),
    ):
        webb = v.webb(namn) if namn not in v.miljo.adapter.__dict__ else None
        assert webb is not None
        req = Forfragan.fran_ra(
            "GET", "/x", {"Cookie": "; ".join(f"{k}={x}" for k, x in webb.kakor.items())}
        )
        kontroll = v.miljo.adapter.kraver_projektbehorighet(req, getattr(v, projekt), minst)
        assert kontroll.tillaten is tillaten, (namn, projekt, minst)
        if not tillaten:
            assert kontroll.svar is not None and kontroll.svar[0] == 403


# --- vem får vad -------------------------------------------------------------------------------


def test_organisationsadmin_bjuder_bara_in_medlem_och_ledare(v: Varld) -> None:
    r = v.r
    r.bjud_in_organisation(v.id["admin"], v.org1, _epost("ny1"), "medlem")
    r.bjud_in_organisation(v.id["admin"], v.org1, _epost("ny2"), "ledare")
    for roll in ("organisationsadmin", "agare"):
        with pytest.raises(BehorighetFel):
            r.bjud_in_organisation(v.id["admin"], v.org1, _epost("ny3"), roll)
    # Ägaren och superadmin får alla roller; ledare, medlem och andra organisationers ägare inget.
    for utforare in ("agare", "super"):
        for roll in ORGROLLER:
            r.bjud_in_organisation(v.id[utforare], v.org1, _epost("ny4"), roll)
    for utforare in ("ledare", "medlem", "utan", "agare2"):
        with pytest.raises(BehorighetFel):
            r.bjud_in_organisation(v.id[utforare], v.org1, _epost("ny5"), "medlem")
    with pytest.raises(RegisterFel):
        r.bjud_in_organisation(v.id["agare"], v.org1, _epost("ny6"), "chef")
    with pytest.raises(RegisterFel):
        r.bjud_in_organisation(v.id["agare"], v.org1, "inte-en-adress", "medlem")
    with pytest.raises(RegisterFel):
        r.bjud_in_organisation(v.id["agare"], "0" * 32, _epost("ny7"), "medlem")


def test_organisationsadmin_ror_inte_admin_eller_agare_men_agaren_gor(v: Varld) -> None:
    r = v.r
    # Organisationsadmin: medlem <-> ledare, inget annat.
    r.satt_organisationsroll(v.id["admin"], v.id["medlem"], v.org1, "ledare")
    assert r.effektiv_projektroll(v.id["medlem"], v.p1).roll == "lasare"
    r.satt_organisationsroll(v.id["admin"], v.id["medlem"], v.org1, "medlem")
    with pytest.raises(BehorighetFel):
        r.satt_organisationsroll(v.id["admin"], v.id["ledare"], v.org1, "organisationsadmin")
    with pytest.raises(BehorighetFel):
        r.satt_organisationsroll(v.id["admin"], v.id["agare"], v.org1, "medlem")
    with pytest.raises(BehorighetFel):
        r.satt_organisationsroll(v.id["admin"], v.id["admin"], v.org1, "agare")
    with pytest.raises(BehorighetFel):
        r.ta_bort_organisationsmedlem(v.id["admin"], v.id["agare"], v.org1)
    with pytest.raises(BehorighetFel):
        r.byt_organisationsnamn(v.id["admin"], v.org1, "Nytt namn")
    # Ägaren utser och tar bort organisationsadmin och byter namn.
    r.satt_organisationsroll(v.id["agare"], v.id["ledare"], v.org1, "organisationsadmin")
    assert r.effektiv_projektroll(v.id["ledare"], v.p1).roll == "utredare"
    r.satt_organisationsroll(v.id["agare"], v.id["ledare"], v.org1, "ledare")
    r.ta_bort_organisationsmedlem(v.id["agare"], v.id["admin"], v.org1)
    assert r.effektiv_projektroll(v.id["admin"], v.p1).roll is None
    r.byt_organisationsnamn(v.id["agare"], v.org1, "  Floden  nya ")
    assert next(o["namn"] for o in r.admin_organisationer(v.id["agare"]) if o["id"] == v.org1) == (
        "Floden nya"
    )
    with pytest.raises(RegisterFel):
        r.byt_organisationsnamn(v.id["agare"], v.org1, "Annan")  # upptaget
    # Andras organisation: ingen rätt alls.
    with pytest.raises(BehorighetFel):
        r.satt_organisationsroll(v.id["agare"], v.id["agare2"], v.org3, "medlem")
    with pytest.raises(BehorighetFel):
        r.organisation_medlemmar(v.id["ledare"], v.org1)


def test_sista_agaren_kan_bara_lamnas_av_superadmin(v: Varld) -> None:
    r = v.r
    with pytest.raises(BehorighetFel, match="minst en ägare"):
        r.satt_organisationsroll(v.id["agare"], v.id["agare"], v.org1, "organisationsadmin")
    with pytest.raises(BehorighetFel, match="minst en ägare"):
        r.ta_bort_organisationsmedlem(v.id["agare"], v.id["agare"], v.org1)
    r.satt_organisationsroll(v.id["agare"], v.id["admin"], v.org1, "agare")
    r.satt_organisationsroll(
        v.id["agare"], v.id["agare"], v.org1, "organisationsadmin"
    )  # nu finns en till


def test_gor_organisationsadmin_sanker_inte_en_agare(v: Varld) -> None:
    v.r.gor_organisationsadmin(v.id["agare"], v.org1)
    assert v.r.kontostatus(v.id["agare"]).endswith(f"{v.org1}:agare")


def test_orgadmin_kan_varken_ge_superadmin_spara_eller_se_nycklar(v: Varld) -> None:
    for namn in ("admin", "agare"):
        webb = v.webb(namn)
        kid = v.id["utan"]
        # Ingen väg till superadmin: kroppen avvisas och rollen är oförändrad.
        assert anrop(webb, "PUT", f"/konto/admin/konton/{kid}", {"superadmin": True})[0] == 400
        assert (
            anrop(
                webb,
                "PUT",
                f"/konto/admin/organisationer/{v.org1}/medlemmar/{kid}",
                {"roll": "superadmin"},
            )[0]
            == 400
        )
        assert anrop(webb, "POST", f"/konto/admin/organisationer/{v.org1}/superadmin", {})[0] == 404
        konto = v.r.konto(kid)
        assert konto is not None and konto.superadmin is False
        # En superadmin spärras inte heller av en organisationsägare.
        assert (
            anrop(webb, "PUT", f"/konto/admin/konton/{v.id['super']}", {"sparrad": True})[0] == 403
        )
        # Nycklar: bara de egna. Ett annat kontos id är en okänd nyckeltyp.
        assert anrop(webb, "GET", f"/konto/nycklar/{kid}")[0] == 404
        assert anrop(webb, "GET", f"/konto/admin/nycklar/{kid}")[0] == 404
        assert anrop(webb, "GET", "/konto/admin/server")[0] == 404  # inga serverinställningar här


# --- HTTP-API:t för organisationen -----------------------------------------------------------


def test_admin_api_for_organisation(v: Varld) -> None:
    agare, admin, ledare = v.webb("agare"), v.webb("admin"), v.webb("ledare")
    # Listan: ägaren och organisationsadmin ser sin organisation, ledaren får 403.
    status, svar = anrop(agare, "GET", "/konto/admin/organisationer")
    assert status == 200
    mina = {o["id"]: o for o in svar["organisationer"]}
    assert set(mina) == {v.org1} and mina[v.org1]["din_roll"] == "agare"
    assert mina[v.org1]["tillatna_roller"] == list(ORGROLLER) and mina[v.org1]["far_byta_namn"]
    status, svar = anrop(admin, "GET", "/konto/admin/organisationer")
    assert status == 200 and svar["organisationer"][0]["tillatna_roller"] == ["medlem", "ledare"]
    assert not svar["organisationer"][0]["far_byta_namn"]
    assert anrop(ledare, "GET", "/konto/admin/organisationer")[0] == 403
    # Detalj: medlemmar och roller.
    status, svar = anrop(admin, "GET", f"/konto/admin/organisationer/{v.org1}")
    assert status == 200 and svar["roller"] == list(ORGROLLER)
    assert {m["konto_id"]: m["roll"] for m in svar["medlemmar"]} == {
        v.id["agare"]: "agare",
        v.id["admin"]: "organisationsadmin",
        v.id["ledare"]: "ledare",
        v.id["medlem"]: "medlem",
    }
    assert (
        anrop(agare, "GET", f"/konto/admin/organisationer/{v.org2}")[0] == 403
    )  # annans organisation
    assert anrop(agare, "GET", f"/konto/admin/organisationer/{'0' * 32}")[0] == 404
    # Inbjudan: organisationsadmin bara medlem och ledare, ägaren alla.
    pa = f"/konto/admin/organisationer/{v.org1}/inbjudningar"
    assert (
        anrop(admin, "POST", pa, {"epost": _epost("samuel"), "roll": "organisationsadmin"})[0]
        == 403
    )
    assert anrop(admin, "POST", pa, {"epost": _epost("samuel"), "roll": "agare"})[0] == 403
    assert anrop(admin, "POST", pa, {"epost": _epost("samuel"), "roll": "ledare"})[0] == 201
    assert (
        anrop(agare, "POST", pa, {"epost": _epost("samuel"), "roll": "organisationsadmin"})[0]
        == 201
    )
    assert anrop(agare, "POST", pa, {"epost": _epost("samuel"), "roll": "chef"})[0] == 400
    assert anrop(agare, "POST", pa, {"epost": 5, "roll": "medlem"})[0] == 400
    assert anrop(ledare, "POST", pa, {"epost": _epost("samuel"), "roll": "medlem"})[0] == 403
    status, svar = anrop(agare, "GET", f"/konto/admin/organisationer/{v.org1}")
    assert sorted((i["roll"], i["status"]) for i in svar["inbjudningar"]) == [
        ("ledare", "oppen"),
        ("organisationsadmin", "oppen"),
    ]
    # Roller: organisationsadmin ändrar ledare, inte ägare; ägaren ändrar och tar bort admin.
    pm = f"/konto/admin/organisationer/{v.org1}/medlemmar"
    assert anrop(admin, "PUT", f"{pm}/{v.id['medlem']}", {"roll": "ledare"})[0] == 200
    assert anrop(admin, "PUT", f"{pm}/{v.id['medlem']}", {"roll": "organisationsadmin"})[0] == 403
    assert anrop(admin, "PUT", f"{pm}/{v.id['agare']}", {"roll": "medlem"})[0] == 403
    assert anrop(admin, "DELETE", f"{pm}/{v.id['agare']}")[0] == 403
    assert anrop(agare, "PUT", f"{pm}/{v.id['ledare']}", {"roll": "organisationsadmin"})[0] == 200
    assert anrop(agare, "PUT", f"{pm}/{v.id['ledare']}", {"roll": "superadmin"})[0] == 400
    assert anrop(agare, "PUT", f"{pm}/{v.id['utan']}", {"roll": "ledare"})[0] == 404  # ingen medlem
    assert anrop(agare, "DELETE", f"{pm}/{v.id['ledare']}")[0] == 200
    assert anrop(agare, "DELETE", f"{pm}/{v.id['ledare']}")[0] == 404
    # Namn: bara ägaren.
    assert anrop(admin, "PUT", f"/konto/admin/organisationer/{v.org1}", {"namn": "X"})[0] == 403
    assert (
        anrop(agare, "PUT", f"/konto/admin/organisationer/{v.org1}", {"namn": "Floden 2"})[0] == 200
    )
    # Utan CSRF nekas skrivande anrop som för övriga adminanrop.
    status, _, _ = agare.anrop(
        "POST",
        pa,
        headers={"Origin": ORIGIN},
        kropp=json.dumps({"epost": _epost("x"), "roll": "medlem"}).encode(),
    )
    assert status == 403


# --- inbjudningsflödet -------------------------------------------------------------------------


def test_inbjudan_till_organisationen_tas_emot_vid_inloggning_med_verifierad_adress(
    v: Varld,
) -> None:
    r = v.r
    r.bjud_in_organisation(v.id["agare"], v.org1, _epost("samuel"), "organisationsadmin")
    # Samuel har inget konto och nya konton kräver en väg in: inbjudan är den vägen.
    samuel = v.miljo.webblasare()
    assert samuel.logga_in(v.miljo.idp, _claims("samuel"))[0] == 302
    konto = r.konto_via_epost(_epost("samuel"))
    assert konto is not None
    _, jag = anrop(samuel, "GET", "/konto/jag")
    assert [(m["organisation"], m["roll"]) for m in jag["medlemskap"]] == [
        ("Floden", "organisationsadmin")
    ]
    assert jag["konto"]["admin"] is True and jag["konto"]["superadmin"] is False
    assert r.effektiv_projektroll(konto.id, v.p1).roll == "utredare"
    assert r.effektiv_projektroll(konto.id, v.p2).roll is None
    inbjudningar = r.organisation_inbjudningar(v.id["agare"], v.org1)
    assert [(i["roll"], i["status"]) for i in inbjudningar] == [("organisationsadmin", "anvand")]
    # Inbjudan är förbrukad: en andra inloggning ändrar ingenting.
    assert (
        r.kontostatus(konto.id)
        == f"superadmin=nej sparrad=nej organisationer={v.org1}:organisationsadmin"
    )


def test_inbjudan_tas_inte_emot_med_overifierad_adress_eller_efter_utgang(
    v: Varld, klocka: Any
) -> None:
    r = v.r
    r.bjud_in_organisation(v.id["agare"], v.org1, _epost("sen"), "ledare", giltig_dagar=1)
    klocka.gå(2 * 86400)
    sen = v.miljo.webblasare()
    claims = _claims("sen")
    status = sen.logga_in(v.miljo.idp, claims)[0]
    assert status == 403  # utgången: ingen väg in
    assert r.konto_via_epost(_epost("sen")) is None
    r.bjud_in_organisation(v.id["agare"], v.org1, _epost("overifierad"), "ledare")
    ov = v.miljo.webblasare()
    assert ov.logga_in(v.miljo.idp, {**_claims("overifierad"), "email_verified": False})[0] == 403
    assert r.konto_via_epost(_epost("overifierad")) is None


def test_inbjudan_sanker_aldrig_en_befintlig_hogre_roll(v: Varld) -> None:
    r = v.r
    # Admin finns redan; en inbjudan som ledare ändrar inte rollen. En som ägare höjer den.
    r.bjud_in_organisation(v.id["agare"], v.org1, _epost("admin"), "ledare")
    w = v.webb("admin")
    assert r.kontostatus(v.id["admin"]).endswith(f"{v.org1}:organisationsadmin")
    r.bjud_in_organisation(v.id["agare"], v.org1, _epost("admin"), "agare")
    w.logga_in(v.miljo.idp, _claims("admin"))
    assert r.kontostatus(v.id["admin"]).endswith(f"{v.org1}:agare")


def test_inbjudan_galler_bara_sin_organisation(v: Varld) -> None:
    v.r.bjud_in_organisation(v.id["agare"], v.org1, _epost("kollega"), "agare")
    v.webb("kollega")
    kid = v.id["kollega"]
    assert v.r.effektiv_projektroll(kid, v.p1).roll == "utredare"
    assert v.r.effektiv_projektroll(kid, v.p2).roll is None


def test_inga_personuppgifter_i_handelser_eller_loggar(
    v: Varld, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="konto")
    v.r.bjud_in_organisation(v.id["agare"], v.org1, _epost("samuel"), "organisationsadmin")
    v.webb("samuel")
    v.r.satt_organisationsroll(v.id["agare"], v.id["ledare"], v.org1, "medlem")
    v.r.ta_bort_organisationsmedlem(v.id["agare"], v.id["ledare"], v.org1)
    v.miljo.adapter.har_roll(v.id["admin"], v.p1)
    with v.r.db.lasning() as c:
        detaljer = " ".join(
            f"{h['handelse']} {h['detalj']}" for h in c.execute("SELECT * FROM handelse")
        )
    for text in (detaljer, " ".join(caplog.messages)):
        assert "@" not in text and "example.test" not in text and "samuel" not in text.lower()


# --- serverkommandon -------------------------------------------------------------------------


def _konf(tmp_path: Path) -> Path:
    konf = tmp_path / "config.toml"
    konf.write_text(
        f'[konto]\ninloggning = "av"\ndatabas = "{tmp_path / "k.sqlite"}"\n', encoding="utf-8"
    )
    return konf


def test_serverkommandona_skapa_organisation_med_agare_och_bjud_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    konf = _konf(tmp_path)

    def kor(*args: str) -> str:
        assert admin_main(["--konfig", str(konf), *args]) == 0
        return capsys.readouterr().out.strip()

    joel = kor("forbered-konto", "joel@example.test")
    kor("ge-superadmin", joel)
    assert kor("konto-id", "joel@example.test") == joel
    assert kor("kontostatus", joel) == "superadmin=ja sparrad=nej organisationer=-"
    org = kor("skapa-organisation", "Floden", "--agare", joel)
    assert kor("kontostatus", joel) == f"superadmin=ja sparrad=nej organisationer={org}:agare"
    inb = kor("bjud-in-organisation", org, "samuel@example.test", "organisationsadmin")
    assert len(inb) == 32
    r = Register(Databas(tmp_path / "k.sqlite"))
    assert [(i["roll"], i["status"]) for i in r.organisation_inbjudningar(joel, org)] == [
        ("organisationsadmin", "oppen")
    ]
    # Fel ger ett tydligt fel och felkod 1, aldrig en stacktrace eller en adress.
    for args in (
        ("bjud-in-organisation", "0" * 32, "samuel@example.test", "ledare"),
        ("bjud-in-organisation", org, "samuel@example.test", "chef"),
        ("bjud-in-organisation", org, "inte-en-adress", "ledare"),
        ("skapa-organisation", "Annan", "--agare", "0" * 32),
        ("skapa-organisation", "Floden"),  # namnet är upptaget
        ("kontostatus", "0" * 32),
    ):
        assert admin_main(["--konfig", str(konf), *args]) == 1, args
        ut = capsys.readouterr()
        assert ut.out == "" and ut.err.startswith("Fel: "), args
        assert "samuel@" not in ut.err and "Traceback" not in ut.err


# --- migrering ---------------------------------------------------------------------------------


def _gammal_databas(sokvag: Path) -> None:
    """En databas som den såg ut före organisationsrollerna (schemaversion 4) med data."""
    c = sqlite3.connect(sokvag)
    c.execute("PRAGMA foreign_keys = ON")
    for skript in db_modul.MIGRERINGAR[:4]:
        for sats in skript.split(";\n"):
            if sats.strip():
                c.execute(sats)
    c.execute("PRAGMA user_version = 4")
    c.executescript(
        """
        INSERT INTO konto (id, epost, epost_verifierad, namn, skapad, superadmin)
          VALUES ('k1', 'a@example.test', 1, 'A', 0, 0), ('k2', 'b@example.test', 1, 'B', 0, 0),
                 ('k3', 'c@example.test', 1, 'C', 0, 0);
        INSERT INTO organisation (id, namn, skapad) VALUES ('o1', 'Gammal', 0);
        INSERT INTO forvaltning (id, organisation_id, namn) VALUES ('f1', 'o1', 'Förv');
        INSERT INTO medlemskap (konto_id, organisation_id, forvaltning_id, roll, kalla, skapad,
          bekraftad_av) VALUES
          ('k1', 'o1', NULL, 'organisationsadmin', 'manuell', 5, NULL),
          ('k2', 'o1', NULL, 'medlem', 'idp-tenant', 6, NULL),
          ('k2', 'o1', 'f1', 'medlem', 'idp-grupp', 7, 'x');
        INSERT INTO projekt (id, organisation_id, namn, skapad) VALUES ('p1', 'o1', 'Gammalt', 0);
        INSERT INTO projektbehorighet (konto_id, projekt_id, roll, kalla, skapad)
          VALUES ('k2', 'p1', 'lasare', 'inbjudan', 0);
        INSERT INTO projektinbjudan (id, projekt_id, epost, roll, skapad_av, skapad, giltig_till)
          VALUES ('i1', 'p1', 'x@example.test', 'lasare', 'k1', 0, 99999999999);
        """
    )
    c.commit()
    c.close()


def test_migrering_fran_gammal_databas_behaller_data_och_ger_nya_roller(tmp_path: Path) -> None:
    sokvag = tmp_path / "gammal.sqlite"
    _gammal_databas(sokvag)
    db = Databas(sokvag)  # migrerar 4 -> 5
    with db.lasning() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(db_modul.MIGRERINGAR) == 7
        rader = c.execute(
            "SELECT id, konto_id, organisation_id, forvaltning_id, roll, kalla, skapad,"
            " bekraftad_av"
            " FROM medlemskap ORDER BY id"
        ).fetchall()
        assert [tuple(r) for r in rader] == [
            (1, "k1", "o1", None, "organisationsadmin", "manuell", 5, None),
            (2, "k2", "o1", None, "medlem", "idp-tenant", 6, None),
            (3, "k2", "o1", "f1", "medlem", "idp-grupp", 7, "x"),
        ]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        index = {r["name"] for r in c.execute("PRAGMA index_list(medlemskap)")}
        assert "medlemskap_unik" in index
        assert c.execute("SELECT COUNT(*) FROM projektbehorighet").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM projektinbjudan").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM organisationsinbjudan").fetchone()[0] == 0
    r = Register(db)
    # Befintliga värden fungerar som förut.
    assert r.ar_admin("k1") and not r.ar_admin("k2")
    # Gamla projekt är privata (förval): organisationsroller ger inget förrän projektet delas.
    assert r.projekt_med_effektiv_roll("k1") == []
    with db.lasning() as c:
        assert [
            tuple(x) for x in c.execute("SELECT organisation_synlig, skapad_av FROM projekt")
        ] == [(0, None)]
    with db.transaktion() as c:
        c.execute("UPDATE projekt SET organisation_synlig = 1")
    assert r.projekt_med_effektiv_roll("k1")[0]["roll"] == "utredare"
    assert r.effektiv_projektroll("k2", "p1").roll == "lasare"
    assert [m["roll"] for m in r.organisation_medlemmar("k1", "o1")] == [
        "organisationsadmin",
        "medlem",
    ]
    # Nya roller och källan inbjudan går att skriva, och det unika indexet gäller fortfarande.
    r.satt_organisationsroll("k1", "k2", "o1", "ledare")
    with db.transaktion() as c:
        c.execute(
            "INSERT INTO medlemskap (konto_id, organisation_id, roll, kalla, skapad)"
            " VALUES ('k3', 'o1', 'agare', 'inbjudan', 0)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO medlemskap (konto_id, organisation_id, roll, kalla, skapad)"
                " VALUES ('k3', 'o1', 'ledare', 'manuell', 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO medlemskap (konto_id, organisation_id, roll, kalla, skapad)"
                " VALUES ('k3', 'o1', 'chef', 'manuell', 1)"
            )
    # Att öppna en redan migrerad databas ändrar ingenting.
    Databas(sokvag)
    with Databas(sokvag).lasning() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 7
        assert c.execute("SELECT COUNT(*) FROM medlemskap").fetchone()[0] == 4


def test_ny_databas_och_migrerad_databas_har_samma_schema(tmp_path: Path) -> None:
    _gammal_databas(tmp_path / "gammal.sqlite")
    ny = Databas(tmp_path / "ny.sqlite")
    gammal = Databas(tmp_path / "gammal.sqlite")

    def schema(d: Databas) -> dict[str, str]:
        with d.lasning() as c:
            return {
                r["name"]: " ".join(str(r["sql"]).split())
                for r in c.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'index')"
                    " AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
                )
            }

    assert schema(ny) == schema(gammal)


# --- delning med organisationen (`organisation_synlig`) -----------------------------------------


def _privat_projekt_av(v: Varld, namn: str = "Hemligt") -> tuple[str, str]:
    """Ett nytt privat projekt som kontot `utan` har skapat (och är utredare i)."""
    pid, nytt = v.r.registrera_projekt_for_konto(v.id["utan"], v.org1, namn)
    assert nytt
    return pid, v.id["utan"]


def test_nytt_projekt_ar_privat_och_organisationen_ser_det_inte(v: Varld) -> None:
    r = v.r
    pid, skapare = _privat_projekt_av(v)
    with r.db.lasning() as c:
        rad = c.execute(
            "SELECT organisation_synlig, skapad_av FROM projekt WHERE id = ?", (pid,)
        ).fetchone()
    assert (rad["organisation_synlig"], rad["skapad_av"]) == (0, skapare)
    for projekt in (v.pp, pid):
        for namn in ("agare", "admin", "ledare", "medlem", "super"):
            assert r.effektiv_projektroll(v.id[namn], projekt).roll is None, (namn, projekt)
            assert not v.miljo.adapter.har_roll(v.id[namn], projekt, "lasare")
        assert projekt not in {p["id"] for p in r.projekt_med_effektiv_roll(v.id["admin"])}
    # De uttryckliga medlemmarna har sin roll som förut.
    assert r.effektiv_projektroll(skapare, pid).roll == "utredare"
    # Administratören ser projektet i administrationen (hantera medlemmar) men inte datat.
    rad = next(p for p in r.admin_projekt(v.id["admin"]) if p["id"] == pid)
    assert rad["organisation_synlig"] is False and rad["kan_andra_delning"] is False
    assert next(p for p in r.admin_projekt(v.id["agare"]) if p["id"] == pid)


def test_delat_projekt_ger_organisationen_ratt_och_tillbaka_tar_bort_den_direkt(
    v: Varld,
) -> None:
    r = v.r
    pid, skapare = _privat_projekt_av(v)
    adapter = v.miljo.adapter
    assert not adapter.har_roll(v.id["admin"], pid)
    r.satt_organisation_synlig(skapare, pid, True)
    assert adapter.har_roll(v.id["admin"], pid, "utredare")
    assert adapter.har_roll(v.id["agare"], pid, "utredare")
    assert adapter.har_roll(v.id["ledare"], pid, "lasare")
    assert not adapter.har_roll(v.id["ledare"], pid, "utredare")
    assert not adapter.har_roll(v.id["medlem"], pid, "lasare")
    assert not adapter.har_roll(v.id["super"], pid, "lasare")
    assert pid in {p["id"] for p in r.projekt_med_effektiv_roll(v.id["admin"])}
    assert next(p for p in r.admin_projekt(v.id["admin"]) if p["id"] == pid)["organisation_synlig"]
    # Tillbaka till privat: åtkomsten är borta vid nästa anrop.
    r.satt_organisation_synlig(skapare, pid, False)
    for namn in ("admin", "agare", "ledare"):
        assert not adapter.har_roll(v.id[namn], pid, "lasare"), namn
    assert pid not in {p["id"] for p in r.projekt_med_effektiv_roll(v.id["admin"])}
    assert adapter.har_roll(skapare, pid, "utredare")
    with r.db.lasning() as c:
        h = " ".join(x["handelse"] + " " + x["detalj"] for x in c.execute("SELECT * FROM handelse"))
    assert f"projekt-delning-andrad projekt={pid} organisation_synlig=ja" in h


def test_bara_projektets_utredare_och_skaparen_andrar_delningen(v: Varld) -> None:
    r = v.r
    pid, skapare = _privat_projekt_av(v)
    for namn in ("agare", "admin", "ledare", "medlem", "super"):
        with pytest.raises(BehorighetFel):
            r.satt_organisation_synlig(v.id[namn], pid, True)
        assert not r.kan_andra_delning(v.id[namn], pid)
    # En uttrycklig läsare får inte, en uttrycklig utredare får.
    r.ge_projektroll(v.id["admin"], v.id["medlem"], pid, "lasare")
    with pytest.raises(BehorighetFel):
        r.satt_organisation_synlig(v.id["medlem"], pid, True)
    r.ge_projektroll(v.id["admin"], v.id["medlem"], pid, "utredare")
    r.satt_organisation_synlig(v.id["medlem"], pid, True)
    # Skaparen får även utan kvarvarande roll.
    r.aterkalla_projektroll(v.id["admin"], skapare, pid)
    assert r.kan_andra_delning(skapare, pid)
    r.satt_organisation_synlig(skapare, pid, False)
    with pytest.raises(RegisterFel):
        r.satt_organisation_synlig(skapare, "0" * 32, True)
    # Ett spärrat konto får inte.
    r.satt_sparrad(v.id["super"], skapare, True)
    with pytest.raises(BehorighetFel):
        r.satt_organisation_synlig(skapare, pid, True)


def test_admin_kan_inte_bjuda_in_eller_ge_sig_sjalv_roll_i_privat_projekt(v: Varld) -> None:
    r = v.r
    for namn in ("admin", "agare"):
        with pytest.raises(BehorighetFel, match="privat"):
            r.ge_projektroll(v.id[namn], v.id[namn], v.pp, "utredare")
        with pytest.raises(BehorighetFel, match="privat"):
            r.bjud_in(v.id[namn], v.pp, _epost(namn), "utredare")
        assert r.effektiv_projektroll(v.id[namn], v.pp).roll is None
    # Andra personer kan administratören fortfarande ge en roll (hen administrerar projektet).
    r.ge_projektroll(v.id["admin"], v.id["medlem"], v.pp, "lasare")
    r.bjud_in(v.id["admin"], v.pp, _epost("ny"), "lasare")
    # I ett delat projekt ger det inget extra och blockeras inte.
    r.ge_projektroll(v.id["admin"], v.id["admin"], v.p1, "lasare")
    # Superadmin får, och det loggas.
    r.ge_projektroll(v.id["super"], v.id["super"], v.pp, "utredare")
    assert r.effektiv_projektroll(v.id["super"], v.pp).roll == "utredare"
    with r.db.lasning() as c:
        rader = [
            (x["konto_id"], x["detalj"])
            for x in c.execute(
                "SELECT * FROM handelse WHERE handelse = 'projektroll-sjalvtilldelad'"
            )
        ]
    assert rader == [(v.id["super"], f"projekt={v.pp}")]
    # En inbjudan till superadmins egen adress loggas på samma sätt.
    r.bjud_in(v.id["super"], v.pp, _epost("super"), "lasare")
    with r.db.lasning() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM handelse WHERE handelse = 'projektroll-sjalvtilldelad'"
        ).fetchone()[0]
    assert n == 2


def test_delning_via_http_och_api_och_mcp_ger_samma_resultat(
    tmp_path: Path, v: Varld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from floden_mcp.karna_api import FlodenTjanst
    from tests.webb.test_inloggning import _app, _http
    from tests.webb.test_projekt_per_anvandare import _med_demo

    pid, skapare = _privat_projekt_av(v)
    app = _app(tmp_path, v.miljo.adapter)
    _med_demo(app, monkeypatch)
    tjanst = FlodenTjanst(app, rot=tmp_path, mcp_mapp=tmp_path / "mcp")
    admin = v.webb("admin")
    ager = v.webb("utan")
    url = f"/konto/projekt/{pid}/delning"

    def lista(webb: Webblasare, konto: str) -> tuple[dict[str, Any], dict[str, Any]]:
        _, _, kropp = _http(app, "GET", "/api/projekt", webb)
        api = {p["id"]: p for p in json.loads(kropp)["projekt"] if p["id"] != "demokvarteret"}
        mcp = {
            p["id"]: p for p in tjanst.projekt_lista(konto)["projekt"] if p["id"] != "demokvarteret"
        }
        return api, mcp

    api, mcp = lista(admin, v.id["admin"])
    assert api == mcp and pid not in api
    # Administratören får inte slå på delningen i ett privat projekt; skaparen får.
    assert anrop(admin, "PUT", url, {"organisation_synlig": True})[0] == 403
    assert anrop(ager, "PUT", url, {"organisation_synlig": "ja"})[0] == 400
    assert (
        anrop(ager, "PUT", f"/konto/projekt/{'0' * 32}/delning", {"organisation_synlig": True})[0]
        == 403
    )
    assert anrop(ager, "PUT", f"/konto/projekt/{pid}/annat", {})[0] == 404
    assert anrop(ager, "GET", url)[0] == 405
    status, _, _ = ager.anrop(
        "PUT", url, headers={"Origin": ORIGIN}, kropp=b'{"organisation_synlig": true}'
    )
    assert status == 403  # CSRF krävs
    assert anrop(ager, "PUT", url, {"organisation_synlig": True}) == (
        200,
        {"status": "delning-andrad", "organisation_synlig": True},
    )
    api, mcp = lista(admin, v.id["admin"])
    assert api == mcp and api[pid]["roll"] == "utredare" and api[pid]["organisation_synlig"] is True
    assert api[pid]["kalla"] == "organisation"
    assert _http(app, "GET", f"/api/projekt/{pid}", admin)[0] == 200
    _, detalj = anrop(admin, "GET", f"/konto/admin/projekt/{pid}")
    assert (
        detalj["projekt"]["organisation_synlig"] is True
        and detalj["projekt"]["kan_andra_delning"] is False
    )
    # Tillbaka: borta direkt, i både API och MCP.
    assert anrop(ager, "PUT", url, {"organisation_synlig": False})[0] == 200
    api, mcp = lista(admin, v.id["admin"])
    assert api == mcp and pid not in api
    assert _http(app, "GET", f"/api/projekt/{pid}", admin)[0] == 403
    # Skaparens egen lista visar projektet som privat.
    api, mcp = lista(ager, v.id["utan"])
    assert (
        api == mcp and api[pid]["organisation_synlig"] is False and api[pid]["kalla"] == "projekt"
    )
