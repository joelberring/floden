"""Administration i webben (`/konto/admin/...`): tillåtet för admin, nekat för alla andra."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tests.konto.conftest import ORIGIN, Miljo, Webblasare


def _claims(namn: str) -> dict[str, Any]:
    return {
        "sub": f"sub-{namn}-qz",
        "email": f"{namn}@example.test",
        "email_verified": True,
        "name": namn.capitalize(),
    }


@dataclass
class Varld:
    miljo: Miljo
    org1: str
    org2: str
    p1: str
    p2: str
    superadmin: Webblasare
    admin1: Webblasare
    admin2: Webblasare
    anvandare: Webblasare
    id: dict[str, str]


def _logga_in(miljo: Miljo, namn: str) -> tuple[Webblasare, str]:
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, _claims(namn))[0] == 302
    konto = miljo.register.konto_via_epost(f"{namn}@example.test")
    assert konto is not None
    return webb, konto.id


def bygg_varld(miljo: Miljo) -> Varld:
    r = miljo.register
    org1, org2 = r.skapa_organisation("Kommun ett"), r.skapa_organisation("Kommun två")
    p1, p2 = r.skapa_projekt(org1, "Projekt ett"), r.skapa_projekt(org2, "Projekt två")
    ids: dict[str, str] = {}
    for namn in ("super", "admin1", "admin2"):
        ids[namn] = r.forbered_konto(f"{namn}@example.test")
    r.ge_superadmin(ids["super"])
    r.gor_organisationsadmin(ids["admin1"], org1)
    r.gor_organisationsadmin(ids["admin2"], org2)
    r.bjud_in(ids["admin1"], p1, "anvandare@example.test", "lasare")
    r.bjud_in(ids["admin2"], p2, "annan@example.test", "lasare")
    webbar = {}
    for namn in ("super", "admin1", "admin2", "anvandare", "annan"):
        webbar[namn], ids[namn] = _logga_in(miljo, namn)
    return Varld(
        miljo, org1, org2, p1, p2,
        webbar["super"], webbar["admin1"], webbar["admin2"], webbar["anvandare"], ids,
    )  # fmt: skip


def anrop(
    webb: Webblasare, metod: str, sokvag: str, data: Any = None, *, csrf: bool = True
) -> tuple[int, dict[str, Any]]:
    h = {"Origin": ORIGIN, **(webb.csrf_headers() if csrf and metod != "GET" else {})}
    status, _, kropp = webb.anrop(
        metod, sokvag, headers=h, kropp=b"" if data is None else json.dumps(data).encode()
    )
    return status, json.loads(kropp)


def test_jag_visar_om_kontot_ar_admin(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    assert anrop(v.superadmin, "GET", "/konto/jag")[1]["konto"]["admin"] is True
    assert anrop(v.admin1, "GET", "/konto/jag")[1]["konto"]["admin"] is True
    assert anrop(v.anvandare, "GET", "/konto/jag")[1]["konto"]["admin"] is False


def test_admin_ser_sina_projekt_och_superadmin_alla(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    status, svar = anrop(v.admin1, "GET", "/konto/admin/projekt")
    assert status == 200 and [p["id"] for p in svar["projekt"]] == [v.p1]
    assert svar["projekt"][0]["oppna_inbjudningar"] == 0 and svar["projekt"][0]["medlemmar"] == 1
    status, svar = anrop(v.superadmin, "GET", "/konto/admin/projekt")
    assert status == 200 and {p["id"] for p in svar["projekt"]} == {v.p1, v.p2}
    # Detalj: medlemmar och inbjudningar (användaren använde sin inbjudan vid inloggningen).
    status, svar = anrop(v.admin1, "GET", f"/konto/admin/projekt/{v.p1}")
    assert status == 200 and svar["roller"] == ["lasare", "utredare"]
    assert [(m["epost"], m["roll"], m["sparrad"]) for m in svar["medlemmar"]] == [
        ("anvandare@example.test", "lasare", False)
    ]
    assert [(i["epost"], i["status"]) for i in svar["inbjudningar"]] == [
        ("anvandare@example.test", "anvand")
    ]
    # En annan organisations projekt är nekat, även för en admin.
    assert anrop(v.admin1, "GET", f"/konto/admin/projekt/{v.p2}")[0] == 403
    assert anrop(v.admin2, "GET", f"/konto/admin/projekt/{v.p1}")[0] == 403
    assert anrop(v.superadmin, "GET", f"/konto/admin/projekt/{v.p2}")[0] == 200
    assert anrop(v.superadmin, "GET", f"/konto/admin/projekt/{'0' * 32}")[0] == 404
    assert anrop(v.superadmin, "GET", "/konto/admin/projekt/inte-ett-id")[0] == 404


def test_andra_an_admin_nekas_allt_och_utloggad_far_401(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    ut = miljo.webblasare()
    mal = v.id["annan"]
    anrop_lista = [
        ("GET", "/konto/admin/projekt", None),
        ("GET", f"/konto/admin/projekt/{v.p1}", None),
        ("POST", f"/konto/admin/projekt/{v.p1}/inbjudningar", {"epost": "x@y.test", "roll": "x"}),
        ("PUT", f"/konto/admin/projekt/{v.p1}/medlemmar/{mal}", {"roll": "utredare"}),
        ("DELETE", f"/konto/admin/projekt/{v.p1}/medlemmar/{v.id['anvandare']}", None),
        ("PUT", f"/konto/admin/konton/{mal}", {"sparrad": True}),
    ]  # fmt: skip
    for metod, sokvag, data in anrop_lista:
        assert anrop(v.anvandare, metod, sokvag, data)[0] == 403, (metod, sokvag)
        assert anrop(ut, metod, sokvag, data)[0] == 401, (metod, sokvag)
    # Inget av det nekade har gjort något.
    assert miljo.register.projektroll(mal, v.p1) is None
    konto = miljo.register.konto(mal)
    assert konto is not None and not konto.sparrad
    with miljo.register.db.lasning() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM projektinbjudan WHERE projekt_id = ?", (v.p1,)
            ).fetchone()[0]
            == 1
        )


def test_bjuda_in_och_inbjudan_tas_emot_vid_inloggning(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    sokvag = f"/konto/admin/projekt/{v.p1}/inbjudningar"
    status, svar = anrop(
        v.admin1, "POST", sokvag, {"epost": " Ny.Person@Example.test ", "roll": "utredare"}
    )
    assert status == 201 and svar["status"] == "inbjuden"
    status, svar = anrop(v.admin1, "GET", f"/konto/admin/projekt/{v.p1}")
    oppna = [i for i in svar["inbjudningar"] if i["status"] == "oppen"]
    assert [(i["epost"], i["roll"]) for i in oppna] == [("ny.person@example.test", "utredare")]
    # Personen loggar in med en verifierad adress och får rollen, utan något annat steg.
    ny = miljo.webblasare()
    claims = {**_claims("ny"), "email": "ny.person@example.test"}
    assert ny.logga_in(miljo.idp, claims)[0] == 302
    status, svar = anrop(v.admin1, "GET", f"/konto/admin/projekt/{v.p1}")
    assert ("ny.person@example.test", "utredare") in [
        (m["epost"], m["roll"]) for m in svar["medlemmar"]
    ]
    # Fel indata: roll, adress och kropp.
    for data in (
        {"epost": "x@example.test", "roll": "admin"},
        {"epost": "ingen-adress", "roll": "lasare"},
        {"epost": 5, "roll": "lasare"},
        {"roll": "lasare"},
        [],
    ):
        assert anrop(v.admin1, "POST", sokvag, data)[0] == 400, data
    # Admin i en annan organisation kan inte bjuda in till projektet.
    assert anrop(v.admin2, "POST", sokvag, {"epost": "x@example.test", "roll": "lasare"})[0] == 403
    assert anrop(v.admin1, "GET", sokvag)[0] == 405


def test_andra_och_ta_bort_roll(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    mal = v.id["anvandare"]
    sokvag = f"/konto/admin/projekt/{v.p1}/medlemmar/{mal}"
    assert miljo.register.projektroll(mal, v.p1) == "lasare"
    assert anrop(v.admin1, "PUT", sokvag, {"roll": "utredare"})[0] == 200
    assert miljo.register.projektroll(mal, v.p1) == "utredare"
    assert anrop(v.admin1, "PUT", sokvag, {"roll": "superadmin"})[0] == 400
    assert anrop(v.admin1, "PUT", sokvag, {})[0] == 400
    assert anrop(v.admin2, "PUT", sokvag, {"roll": "lasare"})[0] == 403
    assert miljo.register.projektroll(mal, v.p1) == "utredare"
    assert (
        anrop(
            v.admin1, "PUT", f"/konto/admin/projekt/{v.p1}/medlemmar/{'1' * 32}", {"roll": "lasare"}
        )[0]
        == 404
    )
    assert anrop(v.admin2, "DELETE", sokvag)[0] == 403
    assert anrop(v.admin1, "DELETE", sokvag)[0] == 200
    assert miljo.register.projektroll(mal, v.p1) is None


def test_skrivande_anrop_kraver_csrf(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    mal = v.id["anvandare"]
    sokvag = f"/konto/admin/projekt/{v.p1}/medlemmar/{mal}"
    assert anrop(v.admin1, "PUT", sokvag, {"roll": "utredare"}, csrf=False)[0] == 403
    assert anrop(v.admin1, "DELETE", sokvag, csrf=False)[0] == 403
    assert (
        anrop(v.admin1, "PUT", f"/konto/admin/konton/{mal}", {"sparrad": True}, csrf=False)[0]
        == 403
    )
    assert anrop(v.admin1, "POST", f"/konto/admin/projekt/{v.p1}/inbjudningar",
                 {"epost": "x@example.test", "roll": "lasare"}, csrf=False)[0] == 403  # fmt: skip
    assert miljo.register.projektroll(mal, v.p1) == "lasare"
    konto = miljo.register.konto(mal)
    assert konto is not None and not konto.sparrad


def test_sparra_och_oppna_konto(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    mal = v.id["anvandare"]
    sokvag = f"/konto/admin/konton/{mal}"
    assert anrop(v.anvandare, "GET", "/konto/jag")[0] == 200
    assert anrop(v.admin1, "PUT", sokvag, {"sparrad": "ja"})[0] == 400
    status, svar = anrop(v.admin1, "PUT", sokvag, {"sparrad": True})
    assert status == 200 and svar["status"] == "sparrad"
    # Sessionen slutar gälla direkt och kontot kommer inte in igen.
    assert anrop(v.anvandare, "GET", "/konto/jag")[0] == 401
    ny = miljo.webblasare()
    assert ny.logga_in(miljo.idp, _claims("anvandare"))[0] == 403
    status, svar = anrop(v.admin1, "GET", f"/konto/admin/projekt/{v.p1}")
    assert svar["medlemmar"][0]["sparrad"] is True
    assert anrop(v.admin1, "PUT", sokvag, {"sparrad": False})[1]["status"] == "oppnad"
    assert miljo.webblasare().logga_in(miljo.idp, _claims("anvandare"))[0] == 302


def test_sparra_har_granser(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    # Inte sig själv, inte en superadmin, inte en annan organisationsadmin eller organisation.
    assert (
        anrop(v.admin1, "PUT", f"/konto/admin/konton/{v.id['admin1']}", {"sparrad": True})[0] == 403
    )
    assert (
        anrop(v.superadmin, "PUT", f"/konto/admin/konton/{v.id['super']}", {"sparrad": True})[0]
        == 403
    )
    assert (
        anrop(v.admin1, "PUT", f"/konto/admin/konton/{v.id['super']}", {"sparrad": True})[0] == 403
    )
    assert (
        anrop(v.admin1, "PUT", f"/konto/admin/konton/{v.id['annan']}", {"sparrad": True})[0] == 403
    )
    assert (
        anrop(v.admin1, "PUT", f"/konto/admin/konton/{v.id['admin2']}", {"sparrad": True})[0] == 403
    )
    assert anrop(v.admin1, "PUT", f"/konto/admin/konton/{'2' * 32}", {"sparrad": True})[0] == 404
    for namn in ("super", "admin1", "admin2", "annan", "anvandare"):
        konto = miljo.register.konto(v.id[namn])
        assert konto is not None and not konto.sparrad, namn
    # Superadmin får spärra en organisationsadmin och ett konto i vilken organisation som helst.
    assert (
        anrop(v.superadmin, "PUT", f"/konto/admin/konton/{v.id['admin2']}", {"sparrad": True})[0]
        == 200
    )
    assert (
        anrop(v.superadmin, "PUT", f"/konto/admin/konton/{v.id['annan']}", {"sparrad": True})[0]
        == 200
    )
    assert (
        anrop(v.superadmin, "PUT", f"/konto/admin/konton/{'2' * 32}", {"sparrad": True})[0] == 404
    )
    # En spärrad admin är inte längre admin.
    assert anrop(v.admin2, "GET", "/konto/admin/projekt")[0] == 401
