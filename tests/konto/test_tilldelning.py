"""Organisation, förvaltning och projekt: vad som ges automatiskt och vad som inte ges."""

from __future__ import annotations

import json
from typing import Any

import pytest

from konto.adapter import Forfragan
from konto.register import BehorighetFel
from tests.konto.conftest import (
    ANNA,
    KAK,
    TENANT_A,
    TENANT_B,
    Miljo,
    Webblasare,
    entra_claims,
)


def medlemskap(miljo: Miljo, konto_id: str) -> list[tuple[str | None, str, str]]:
    with miljo.register.db.transaktion() as c:
        return [
            (r["forvaltning_id"], r["roll"], r["kalla"])
            for r in c.execute(
                "SELECT forvaltning_id, roll, kalla FROM medlemskap WHERE konto_id = ? ORDER BY id",
                (konto_id,),
            )
        ]


def konto_id(miljo: Miljo, webb: Webblasare) -> str:
    return str(json.loads(webb.anrop("GET", "/konto/jag")[2])["konto"]["id"])


def uppsattning(miljo: Miljo) -> dict[str, str]:
    """Exempelkommun med tenant A, två förvaltningar och en grupp per förvaltning (påhittat)."""
    r = miljo.register
    org = r.skapa_organisation("Exempelkommun")
    r.registrera_tenant(org, "entra", TENANT_A)
    sbk = r.skapa_forvaltning(org, "Stadsbyggnad")
    miljo_f = r.skapa_forvaltning(org, "Miljö")
    regel_sbk = r.mappa_grupp(org, sbk, "grupp-sbk")
    r.mappa_grupp(org, miljo_f, "grupp-miljo")
    projekt = r.skapa_projekt(org, "Provområdet", sbk)
    return {
        "org": org,
        "sbk": sbk,
        "miljo": miljo_f,
        "projekt": projekt,
        "regel_sbk": str(regel_sbk),
    }


# --- Entra: tenant och grupper --------------------------------------------------------------


def test_entra_okand_tenant_nekas_trots_giltig_token(entra_miljo: Miljo) -> None:
    uppsattning(entra_miljo)
    webb = entra_miljo.webblasare()
    svar = webb.logga_in(entra_miljo.idp, entra_claims(TENANT_B), lev="entra")
    assert svar[0] == 403 and '"okand-tenant"' in svar[2].decode()
    with entra_miljo.register.db.transaktion() as c:
        assert c.execute("SELECT COUNT(*) FROM konto").fetchone()[0] == 0


def test_entra_tenant_ger_organisation_och_grupp_ger_forvaltning(entra_miljo: Miljo) -> None:
    u = uppsattning(entra_miljo)
    webb = entra_miljo.webblasare()
    svar = webb.logga_in(entra_miljo.idp, entra_claims(groups=["grupp-sbk", "annan"]), lev="entra")
    assert svar[0] == 302
    kid = konto_id(entra_miljo, webb)
    assert medlemskap(entra_miljo, kid) == [
        (None, "medlem", "idp-tenant"),
        (u["sbk"], "medlem", "idp-grupp"),
    ]
    with entra_miljo.register.db.transaktion() as c:
        notis = [
            r["detalj"]
            for r in c.execute("SELECT detalj FROM handelse WHERE handelse = 'forvaltning-tillagd'")
        ]
    assert notis == [
        f"forvaltning={u['sbk']} regel={u['regel_sbk']}"
    ]  # konto-id och regel, ingen e-post


def test_entra_gruppbyte_flyttar_medlemskapet_vid_nasta_inloggning(entra_miljo: Miljo) -> None:
    u = uppsattning(entra_miljo)
    webb = entra_miljo.webblasare()
    webb.logga_in(entra_miljo.idp, entra_claims(groups=["grupp-sbk"]), lev="entra")
    kid = konto_id(entra_miljo, webb)
    entra_miljo.register.lagg_till_medlem(kid, u["org"], u["miljo"], bekraftad_av=kid)  # manuellt
    entra_miljo.klocka.gå(10)
    webb2 = entra_miljo.webblasare()
    webb2.logga_in(entra_miljo.idp, entra_claims(groups=["grupp-miljo"]), lev="entra")
    kallor = {(f, k) for f, _, k in medlemskap(entra_miljo, kid)}
    assert (u["sbk"], "idp-grupp") not in kallor  # borta
    assert (u["miljo"], "manuell") in kallor  # manuella medlemskap rörs inte (gäller först)
    assert (None, "idp-tenant") in kallor


def test_entra_gruppbyte_utan_manuellt_medlemskap(entra_miljo: Miljo) -> None:
    u = uppsattning(entra_miljo)
    webb = entra_miljo.webblasare()
    webb.logga_in(entra_miljo.idp, entra_claims(groups=["grupp-sbk"]), lev="entra")
    kid = konto_id(entra_miljo, webb)
    entra_miljo.klocka.gå(10)
    entra_miljo.webblasare().logga_in(
        entra_miljo.idp, entra_claims(groups=["grupp-miljo"]), lev="entra"
    )
    assert medlemskap(entra_miljo, kid) == [
        (None, "medlem", "idp-tenant"),
        (u["miljo"], "medlem", "idp-grupp"),
    ]
    # Inga grupper alls: förvaltningen tas bort, organisationen finns kvar.
    entra_miljo.klocka.gå(10)
    entra_miljo.webblasare().logga_in(entra_miljo.idp, entra_claims(groups=[]), lev="entra")
    assert medlemskap(entra_miljo, kid) == [(None, "medlem", "idp-tenant")]


def test_entra_grupp_overflod_lamnar_medlemskap_oforandrat(entra_miljo: Miljo) -> None:
    u = uppsattning(entra_miljo)
    webb = entra_miljo.webblasare()
    webb.logga_in(entra_miljo.idp, entra_claims(groups=["grupp-sbk"]), lev="entra")
    kid = konto_id(entra_miljo, webb)
    entra_miljo.klocka.gå(10)
    overflod = entra_claims(_claim_names={"groups": "src1"})
    entra_miljo.webblasare().logga_in(entra_miljo.idp, overflod, lev="entra")
    assert (u["sbk"], "medlem", "idp-grupp") in medlemskap(entra_miljo, kid)


def test_entra_fel_issuer_for_tenant_nekas(entra_miljo: Miljo) -> None:
    """Token från tenant B som påstår sig vara A (eller tvärtom) godtas inte."""
    uppsattning(entra_miljo)
    claims = entra_claims(TENANT_A)
    claims["iss"] = f"https://login.entra.test/{TENANT_B}/v2.0"
    svar = entra_miljo.webblasare().logga_in(entra_miljo.idp, claims, lev="entra")
    assert svar[0] == 403 and '"id-token"' in svar[2].decode()
    ogiltig = entra_claims("inte-en-guid")
    assert entra_miljo.webblasare().logga_in(entra_miljo.idp, ogiltig, lev="entra")[0] == 403
    saknas = entra_claims()
    del saknas["tid"]
    assert entra_miljo.webblasare().logga_in(entra_miljo.idp, saknas, lev="entra")[0] == 403


def test_entra_epost_ar_overifierad_och_ger_varken_koppling_eller_inbjudan(
    entra_miljo: Miljo,
) -> None:
    u = uppsattning(entra_miljo)
    r = entra_miljo.register
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, u["org"])
    r.bjud_in(admin, u["projekt"], "bo@kommun.test")
    offer = r.forbered_konto("bo@kommun.test")  # ett befintligt konto med samma adress
    webb = entra_miljo.webblasare()
    assert webb.logga_in(entra_miljo.idp, entra_claims(groups=["grupp-sbk"]), lev="entra")[0] == 302
    kid = konto_id(entra_miljo, webb)
    assert kid != offer  # inte kopplat via e-post (nOAuth)
    assert r.projektroll(kid, u["projekt"]) is None  # inbjudan gäller inte en overifierad adress
    assert r.projektroll(offer, u["projekt"]) is None
    konto = r.konto(kid)
    assert konto is not None and not konto.epost_verifierad


def test_overifierad_entra_epost_kan_inte_blockera_verifierad_adress(entra_miljo: Miljo) -> None:
    """Någon som påstår en annan persons adress i sin tenant får inte uppta den: den overifierade
    adressen ligger utanför det unika indexet för verifierade adresser."""
    uppsattning(entra_miljo)
    entra_miljo.webblasare().logga_in(
        entra_miljo.idp, entra_claims(email="anna@example.test"), lev="entra"
    )
    with entra_miljo.register.db.transaktion() as c:
        c.execute(
            "INSERT INTO konto (id, epost, epost_verifierad, namn, skapad)"
            " VALUES ('g1', 'anna@example.test', 1, 'Anna', 1)"
        )  # verifierad adress går att lägga
    assert entra_miljo.register.konto("g1") is not None


# --- Google: domän, inbjudan och att inget ges automatiskt --------------------------------------


def test_google_utan_inbjudan_eller_organisation_nekas_och_inget_sparas(miljo: Miljo) -> None:
    webb = miljo.webblasare()
    svar = webb.logga_in(miljo.idp, ANNA)
    assert svar[0] == 403 and '"ingen-inbjudan"' in svar[2].decode()
    with miljo.register.db.transaktion() as c:
        assert c.execute("SELECT COUNT(*) FROM konto").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 0


def test_oppna_nya_konton_ger_konto_utan_nagon_behorighet(
    tmp_path: Any, klocka: Any, idp: Any
) -> None:
    from konto.adapter import KontoAdapter
    from konto.leverantor import google
    from tests.konto.conftest import bygg_konfig

    lev = google(
        idp.klient_id, idp.hemlighet, discovery_url=idp.discovery_url, issuers=(idp.issuer,)
    )
    a = KontoAdapter(
        bygg_konfig(tmp_path, nya_konton="oppen"),
        hamtare=idp,
        klocka=klocka,
        leverantorer={"google": lev},
    )
    webb = Webblasare(a)
    assert webb.logga_in(idp, ANNA)[0] == 302
    data = json.loads(webb.anrop("GET", "/konto/jag")[2])
    assert data["medlemskap"] == [] and data["projekt"] == []


def test_doman_ger_bara_organisation_aldrig_forvaltning_eller_projekt(miljo: Miljo) -> None:
    u = uppsattning(miljo)
    miljo.register.registrera_doman(u["org"], "kommun.test")
    webb = miljo.webblasare()
    claims = {**ANNA, "email": "anna@kommun.test", "hd": "kommun.test"}
    assert webb.logga_in(miljo.idp, claims)[0] == 302
    kid = konto_id(miljo, webb)
    assert medlemskap(miljo, kid) == [(None, "medlem", "doman")]
    assert miljo.register.projektroll(kid, u["projekt"]) is None
    jag = Forfragan.fran_ra(
        "GET",
        f"/api/projekt/{u['projekt']}",
        {"cookie": "; ".join(f"{k}={v}" for k, v in webb.kakor.items())},
    )
    kontroll = miljo.adapter.kraver_projektbehorighet(jag, u["projekt"])
    assert not kontroll.tillaten and kontroll.svar is not None and kontroll.svar[0] == 403


def test_doman_utan_hd_intyg_ger_ingen_organisation(miljo: Miljo) -> None:
    """En Google-adress på kommunens domän som inte är ett Workspace-konto bevisar ingenting."""
    u = uppsattning(miljo)
    miljo.register.registrera_doman(u["org"], "kommun.test")
    webb = miljo.webblasare()
    claims = {**ANNA, "email": "anna@kommun.test"}  # ingen hd
    svar = webb.logga_in(miljo.idp, claims)
    assert svar[0] == 403 and '"ingen-inbjudan"' in svar[2].decode()
    annan = {**ANNA, "email": "anna@annan.test", "hd": "kommun.test"}  # hd och adress stämmer inte
    assert miljo.webblasare().logga_in(miljo.idp, annan)[0] == 403


def test_inbjudan_ger_projektroll_en_gang_och_bara_till_ratt_adress(miljo: Miljo) -> None:
    u = uppsattning(miljo)
    r = miljo.register
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, u["org"])
    r.bjud_in(admin, u["projekt"], "ANNA@example.test", "utredare")  # versaler normaliseras
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 302
    kid = konto_id(miljo, webb)
    assert r.projektroll(kid, u["projekt"]) == "utredare"
    assert r.har_projektroll(kid, u["projekt"], "lasare")
    assert r.har_projektroll(kid, u["projekt"], "utredare")
    krav = Forfragan.fran_ra(
        "GET", "/api/x", {"cookie": "; ".join(f"{k}={v}" for k, v in webb.kakor.items())}
    )
    assert miljo.adapter.kraver_projektbehorighet(krav, u["projekt"], "utredare").tillaten
    assert not miljo.adapter.kraver_projektbehorighet(krav, "annat-projekt").tillaten
    with r.db.transaktion() as c:
        assert c.execute("SELECT anvand_av FROM projektinbjudan").fetchone()[0] == kid


def test_inbjudan_till_annan_adress_eller_utgangen_ger_ingenting(miljo: Miljo) -> None:
    u = uppsattning(miljo)
    r = miljo.register
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, u["org"])
    r.bjud_in(admin, u["projekt"], "nagon.annan@example.test")
    r.bjud_in(admin, u["projekt"], "anna@example.test", giltig_dagar=14)
    miljo.klocka.gå(15 * 86400)
    # Den utgångna inbjudan till Anna kan inte tas emot, och hon har ingen annan väg in.
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 403


def test_konto_med_organisation_men_utan_inbjudan_har_ingen_projektdata(miljo: Miljo) -> None:
    """Förvaltningsmedlemskap ger projektlistan i bästa fall, aldrig projektroll."""
    u = uppsattning(miljo)
    r = miljo.register
    anna = r.forbered_konto("anna@example.test")
    r.lagg_till_medlem(anna, u["org"], u["sbk"], bekraftad_av=anna)
    r.ge_superadmin(anna)
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 302  # kopplas till det förberedda kontot
    assert konto_id(miljo, webb) == anna
    assert r.projektroll(anna, u["projekt"]) is None  # inte ens en superadmin får projektdata
    krav = Forfragan.fran_ra(
        "GET", "/api/x", {"cookie": "; ".join(f"{k}={v}" for k, v in webb.kakor.items())}
    )
    assert not miljo.adapter.kraver_projektbehorighet(krav, u["projekt"]).tillaten


def test_inbjudan_och_tilldelning_kraver_organisationsadmin(miljo: Miljo) -> None:
    u = uppsattning(miljo)
    r = miljo.register
    vanlig = r.forbered_konto("vanlig@example.test")
    r.lagg_till_medlem(vanlig, u["org"], u["sbk"], bekraftad_av=vanlig)
    andra_org = r.skapa_organisation("Grannkommun")
    grann_admin = r.forbered_konto("grann@example.test")
    r.gor_organisationsadmin(grann_admin, andra_org)
    for utforare in (vanlig, grann_admin):
        with pytest.raises(BehorighetFel):
            r.bjud_in(utforare, u["projekt"], "x@example.test")
        with pytest.raises(BehorighetFel):
            r.ge_projektroll(utforare, vanlig, u["projekt"], "lasare")
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, u["org"])
    r.ge_projektroll(admin, vanlig, u["projekt"], "lasare")
    assert r.projektroll(vanlig, u["projekt"]) == "lasare"
    r.aterkalla_projektroll(admin, vanlig, u["projekt"])
    assert r.projektroll(vanlig, u["projekt"]) is None


def test_forberett_konto_kopplas_vid_forsta_verifierade_inloggning(miljo: Miljo) -> None:
    """Bootstrap av första superadmin: kontot finns före inloggningen och får då sin identitet."""
    r = miljo.register
    kid = r.forbered_konto("anna@example.test")
    r.ge_superadmin(kid)
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 302
    data = json.loads(webb.anrop("GET", "/konto/jag")[2])
    assert data["konto"]["id"] == kid and data["konto"]["superadmin"] is True
    # Kontot har exakt en identitet (leverantör, tenant, subject).
    with r.db.transaktion() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM identitet WHERE konto_id = ?", (kid,)).fetchone()[0]
            == 1
        )
    assert KAK.session in webb.kakor
