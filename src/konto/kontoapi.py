"""API:t under `/konto/nycklar` och `/konto/admin` (anropas av `KontoAdapter.hantera_konto`).

Båda kräver inloggning, och PUT/POST/DELETE kräver CSRF (kaka + `X-Floden-CSRF`) och rätt `Origin`,
precis som övriga skrivande anrop (`KontoAdapter.kraver_inloggning`).

Nycklar (`/konto/nycklar`, bara kontots egna; ingen väg att läsa en annans):

- `GET    /konto/nycklar`          alla typer med status och om funktionen är påslagen
- `GET    /konto/nycklar/<typ>`    en typ: `sparad`, `sparad_datum`, `slutar_pa` (aldrig nyckeln)
- `PUT    /konto/nycklar/<typ>`    sparar eller ersätter (kryptering krävs: annars 503 `nycklar-av`)
- `DELETE /konto/nycklar/<typ>`    tar bort

MCP-token (`/konto/mcp-token`, bara kontots egna; kräver session, aldrig bearer):

- `GET    /konto/mcp-token`        aktiva token (namn, skapad, senast använd, fyra sista tecken)
- `POST   /konto/mcp-token`        `{"namn"}` skapar; svaret (201) har `token` en enda gång
- `DELETE /konto/mcp-token/<id>`   återkallar

Administration (`/konto/admin`, bara superadmin och organisationsadmin, annars 403):

- `GET    /konto/admin/projekt`                              projekt som får administreras
- `GET    /konto/admin/projekt/<id>`                         projekt, medlemmar och inbjudningar
- `POST   /konto/admin/projekt/<id>/inbjudningar`   `{"epost", "roll"}`, roll `lasare`/`utredare`
- `PUT    /konto/admin/projekt/<id>/medlemmar/<konto>`       `{"roll"}` ändrar (eller ger) roll
- `DELETE /konto/admin/projekt/<id>/medlemmar/<konto>`       tar bort projektrollen
- `PUT    /konto/admin/konton/<konto>`                       `{"sparrad": true|false}`

Organisation (samma 403 för alla utom superadmin, organisationsadmin och ägare; vad var och en får
avgörs i `Register`, se `docs/KONTO.md` avsnitt 5 och 11):

- `GET    /konto/admin/organisationer`                       organisationer som får administreras
- `GET    /konto/admin/organisationer/<id>`                  organisation, medlemmar, inbjudningar
- `PUT    /konto/admin/organisationer/<id>`                  `{"namn"}` byter namn (ägare)
- `POST   /konto/admin/organisationer/<id>/inbjudningar`     `{"epost", "roll"}`: medlem, ledare,
  organisationsadmin eller ägare (de två sista bara av ägare och superadmin)
- `PUT    /konto/admin/organisationer/<id>/medlemmar/<konto>` `{"roll"}` ändrar rollen
- `DELETE /konto/admin/organisationer/<id>/medlemmar/<konto>` tar bort medlemskapet

Projektinställningar (`/konto/projekt/<id>/...`, inloggad; vem som får avgörs i `Register`):

- `GET    .../installningar`   underlaget till sidan (404 utan rätt att se projektet)
- `PUT    .../delning`   `{"organisation_synlig": true|false}`: projektets utredare, ägare och
  skaparen; 403 för alla andra (även organisationens ägare och admin, och superadmin)
- `PUT    .../arkiv`   `{"arkiverad": true|false}`: projektets ägare och superadmin
- `POST   .../inbjudningar` `{"epost", "roll"}` (lasare, utredare, agare) och
  `DELETE .../inbjudningar/<id>`
- `PUT    .../medlemmar/<konto>` `{"roll"}` och `DELETE .../medlemmar/<konto>`
  (inbjudningar och medlemmar: projektets ägare, organisationsadmin och ägare i organisationen,
  superadmin)
Namnbyte och radering rör filer och finns i webb-API:t (`PUT .../namn`, `POST .../radera`).

Svaren har aldrig en nyckel, och felen nämner fält men aldrig värden.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from konto.adapter import Forfragan, Svar, _fel, _json
from konto.mcptoken import MAX_PER_KONTO, McpTokenFel
from konto.nycklar import NyckelForradFel
from konto.register import ORGROLLER, PROJEKTROLLER, BehorighetFel, RegisterFel

if TYPE_CHECKING:
    from konto.adapter import KontoAdapter

logg = logging.getLogger("konto")

MAX_KROPP = 4096
_OKANDA = ("Okänt projekt", "Okänt konto", "Okänd organisation", "Okänt medlemskap")
_ID = re.compile(r"^[0-9a-f]{32}$")


class _Avvisad(Exception):
    def __init__(self, svar: Svar) -> None:
        super().__init__("avvisad")
        self.svar = svar


def _metod_ej_tillaten() -> Svar:
    return _fel(405, "metod", "Metod ej tillåten")


def _json_kropp(req: Forfragan) -> dict[str, Any]:
    if len(req.kropp) > MAX_KROPP:
        raise _Avvisad(_fel(413, "for-stor", "Förfrågan är för stor"))
    try:
        data = json.loads(req.kropp.decode("utf-8")) if req.kropp else None
    except (UnicodeDecodeError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise _Avvisad(_fel(400, "ogiltigt", "Kroppen måste vara ett JSON-objekt"))
    return data


def _inloggad(adapter: KontoAdapter, req: Forfragan) -> tuple[str | None, Svar | None]:
    """(konto-id, None) för en inloggad anropare, annars (None, svaret som ska skickas)."""
    kontroll = adapter.kraver_inloggning(req)
    if not kontroll.tillaten or kontroll.inloggad is None:
        svar = kontroll.svar or _fel(401, "ej-inloggad", "Inloggning krävs")
        return None, svar
    return kontroll.inloggad.konto_id, None


# ------------------------------------------------------------------------------- nycklar


def hantera_nycklar(adapter: KontoAdapter, req: Forfragan, delar: list[str]) -> Svar:
    """`delar` är sökvägen efter `/konto/nycklar` (tom eller `[typ]`)."""
    konto_id, nekad = _inloggad(adapter, req)
    if konto_id is None:
        assert nekad is not None  # noqa: S101
        return nekad
    forrad = adapter.nycklar
    assert forrad is not None  # noqa: S101 - aktiv => skapad
    try:
        if not delar:
            if req.metod != "GET":
                return _metod_ej_tillaten()
            return _json(200, forrad.oversikt(konto_id))
        if len(delar) != 1:
            return _fel(404, "okand", "Okänd ändpunkt")
        typ = delar[0]
        forrad.typ(typ)  # 404 för en okänd typ, före allt annat
        if req.metod == "GET":
            return _json(200, {**forrad.status(konto_id, typ), "paslagen": forrad.paslagen})
        if req.metod == "PUT":
            status = forrad.spara(konto_id, typ, _json_kropp(req))
            logg.info("nyckel sparad konto=%s typ=%s", konto_id, typ)
            return _json(200, {"status": "sparad", **status})
        if req.metod == "DELETE":
            fanns = forrad.ta_bort(konto_id, typ)
            logg.info("nyckel borttagen konto=%s typ=%s", konto_id, typ)
            return _json(200, {"status": "borttagen" if fanns else "fanns-inte", "typ": typ})
        return _metod_ej_tillaten()
    except _Avvisad as exc:
        return exc.svar
    except NyckelForradFel as exc:
        return _fel(exc.status, exc.kod, exc.text)
    except Exception as exc:  # noqa: BLE001 - aldrig ett undantag (kan bära data) till klienten
        logg.error("nyckelhantering misslyckades konto=%s fel=%s", konto_id, type(exc).__name__)
        return _fel(500, "internt", "Internt fel")


# ------------------------------------------------------------------------------- MCP-token


def hantera_mcp_token(adapter: KontoAdapter, req: Forfragan, delar: list[str]) -> Svar:
    """`delar` är sökvägen efter `/konto/mcp-token` (tom eller `[id]`). Bara session och CSRF:
    en MCP-token kan varken skapa eller återkalla token."""
    konto_id, nekad = _inloggad(adapter, req)
    if konto_id is None:
        assert nekad is not None  # noqa: S101
        return nekad
    forrad = adapter.mcp_tokens
    assert forrad is not None  # noqa: S101 - aktiv => skapad
    try:
        if not delar:
            if req.metod == "GET":
                return _json(
                    200,
                    {
                        "tokens": forrad.lista(konto_id),
                        "max": MAX_PER_KONTO,
                        "mcp_url": f"{adapter.konfig.origin}/mcp",
                    },
                )
            if req.metod == "POST":
                ny = forrad.skapa(konto_id, _json_kropp(req).get("namn"))
                logg.info("mcp-token skapad konto=%s token=%s", konto_id, ny["id"])
                return _json(201, {"status": "skapad", **ny})
            return _metod_ej_tillaten()
        if len(delar) != 1 or not _id_ok(delar[0]):
            return _fel(404, "okand", "Okänd ändpunkt")
        if req.metod != "DELETE":
            return _metod_ej_tillaten()
        if not forrad.aterkalla(konto_id, delar[0]):
            return _fel(404, "okand", "Okänd token")
        logg.info("mcp-token aterkallad konto=%s token=%s", konto_id, delar[0])
        return _json(200, {"status": "aterkallad", "id": delar[0]})
    except _Avvisad as exc:
        return exc.svar
    except McpTokenFel as exc:
        return _fel(exc.status, exc.kod, exc.text)
    except Exception as exc:  # noqa: BLE001 - aldrig ett undantag till klienten
        logg.error("mcp-token misslyckades konto=%s fel=%s", konto_id, type(exc).__name__)
        return _fel(500, "internt", "Internt fel")


# ------------------------------------------------------------------------------- admin


_FEL_DELNING = "Bara projektets utredare, ägare och skaparen ändrar delningen"
_FEL_INGEN_RATT = "Du har inte rätt att göra det här i projektet"


def hantera_projekt(adapter: KontoAdapter, req: Forfragan, delar: list[str]) -> Svar:
    """`delar` är sökvägen efter `/konto/projekt`: `[<id>, <del>, ...]`. Se modulens text för
    ändpunkterna. Vem som får vad avgörs i `Register`; en okänd id och saknad rätt ger samma 403
    vid ändring (och 404 vid läsning), så att id:n inte går att pröva fram."""
    konto_id, nekad = _inloggad(adapter, req)
    if konto_id is None:
        assert nekad is not None  # noqa: S101
        return nekad
    register = adapter.register
    assert register is not None  # noqa: S101
    if len(delar) < 2 or not _id_ok(delar[0]):
        return _fel(404, "okand", "Okänd ändpunkt")
    pid, del_, resten = delar[0], delar[1], delar[2:]
    metod = req.metod
    try:
        if del_ == "delning" and not resten:
            if metod != "PUT":
                return _metod_ej_tillaten()
            synlig = _json_kropp(req).get("organisation_synlig")
            if not isinstance(synlig, bool):
                return _fel(400, "ogiltigt", "organisation_synlig måste vara true eller false")
            try:
                register.satt_organisation_synlig(konto_id, pid, synlig)
            except (BehorighetFel, RegisterFel):
                return _fel(403, "ingen-behorighet", _FEL_DELNING)
            return _json(200, {"status": "delning-andrad", "organisation_synlig": synlig})
        if del_ == "installningar" and not resten:
            if metod != "GET":
                return _metod_ej_tillaten()
            try:
                return _json(200, register.projektinstallningar(konto_id, pid))
            except (BehorighetFel, RegisterFel):
                return _fel(404, "okand", "Okänt projekt")
        if del_ == "arkiv" and not resten:
            if metod != "PUT":
                return _metod_ej_tillaten()
            arkiverad = _json_kropp(req).get("arkiverad")
            if not isinstance(arkiverad, bool):
                return _fel(400, "ogiltigt", "arkiverad måste vara true eller false")
            register.satt_arkiverad(konto_id, pid, arkiverad)
            return _json(200, {"status": "arkiverat" if arkiverad else "aterstallt"})
        if del_ == "inbjudningar":
            if len(resten) == 0 and metod == "POST":
                kropp = _json_kropp(req)
                epost, roll = kropp.get("epost"), kropp.get("roll", "lasare")
                if not isinstance(epost, str) or not isinstance(roll, str):
                    return _fel(400, "ogiltigt", "epost och roll måste vara text")
                inbjudan_id = register.bjud_in(konto_id, pid, epost, roll)
                return _json(201, {"status": "inbjuden", "id": inbjudan_id, "roll": roll})
            if len(resten) == 1 and _id_ok(resten[0]) and metod == "DELETE":
                register.aterkalla_inbjudan(konto_id, pid, resten[0])
                return _json(200, {"status": "inbjudan-borttagen", "id": resten[0]})
            return (
                _metod_ej_tillaten() if len(resten) <= 1 else _fel(404, "okand", "Okänd ändpunkt")
            )
        if del_ == "medlemmar" and len(resten) == 1:
            kid = resten[0]
            if not _id_ok(kid):
                return _fel(404, "okand", "Okänt konto")
            if metod == "PUT":
                roll = _json_kropp(req).get("roll")
                if not isinstance(roll, str) or roll not in PROJEKTROLLER:
                    return _fel(400, "ogiltigt", "roll måste vara lasare, utredare eller agare")
                register.projekt_medlemmar(konto_id, pid)  # behörighet och projekt före kontot
                if register.konto(kid) is None:
                    return _fel(404, "okand", "Okänt konto")
                register.ge_projektroll(konto_id, kid, pid, roll)
                return _json(200, {"status": "roll-satt", "konto_id": kid, "roll": roll})
            if metod == "DELETE":
                register.aterkalla_projektroll(konto_id, kid, pid)
                return _json(200, {"status": "roll-borttagen", "konto_id": kid})
            return _metod_ej_tillaten()
    except _Avvisad as exc:
        return exc.svar
    except BehorighetFel:
        return _fel(403, "ingen-behorighet", _FEL_INGEN_RATT)
    except RegisterFel as exc:
        text = str(exc)
        if text == "Okänt projekt":
            return _fel(403, "ingen-behorighet", _FEL_INGEN_RATT)
        return _fel(404 if text in _OKANDA else 400, "ogiltigt", text)
    return _fel(404, "okand", "Okänd ändpunkt")


def _id_ok(*varden: str) -> bool:
    return all(_ID.match(v) for v in varden)


def hantera_admin(adapter: KontoAdapter, req: Forfragan, delar: list[str]) -> Svar:
    """`delar` är sökvägen efter `/konto/admin`."""
    konto_id, nekad = _inloggad(adapter, req)
    if konto_id is None:
        assert nekad is not None  # noqa: S101
        return nekad
    register = adapter.register
    assert register is not None  # noqa: S101
    if not register.ar_admin(konto_id):
        logg.info("admin nekat konto=%s", konto_id)
        return _fel(403, "ingen-behorighet", "Kräver administratör")
    try:
        return _admin(adapter, req, delar, konto_id)
    except _Avvisad as exc:
        return exc.svar
    except BehorighetFel as exc:
        return _fel(403, "ingen-behorighet", str(exc))
    except RegisterFel as exc:
        text = str(exc)
        return _fel(404 if text in _OKANDA else 400, "ogiltigt", text)


def _admin(adapter: KontoAdapter, req: Forfragan, delar: list[str], utforare: str) -> Svar:
    register = adapter.register
    assert register is not None  # noqa: S101
    metod = req.metod
    if delar in ([], ["projekt"]):
        if metod != "GET":
            return _metod_ej_tillaten()
        return _json(200, {"projekt": register.admin_projekt(utforare)})
    if delar[0] == "projekt" and len(delar) >= 2:
        pid = delar[1]
        if not _id_ok(pid):
            return _fel(404, "okand", "Okänt projekt")
        if len(delar) == 2:
            if metod != "GET":
                return _metod_ej_tillaten()
            admin_lista = register.admin_projekt(utforare)
            rad = next((p for p in admin_lista if p["id"] == pid), None)
            if rad is None:
                register.projekt_medlemmar(utforare, pid)  # ger rätt fel (404 eller 403)
                return _fel(404, "okand", "Okänt projekt")
            return _json(
                200,
                {
                    "projekt": rad,
                    "roller": list(PROJEKTROLLER),
                    "medlemmar": register.projekt_medlemmar(utforare, pid),
                    "inbjudningar": register.projekt_inbjudningar(utforare, pid),
                },
            )
        if len(delar) == 3 and delar[2] == "inbjudningar":
            if metod != "POST":
                return _metod_ej_tillaten()
            kropp = _json_kropp(req)
            epost, roll = kropp.get("epost"), kropp.get("roll", "lasare")
            if not isinstance(epost, str) or not isinstance(roll, str):
                return _fel(400, "ogiltigt", "epost och roll måste vara text")
            inbjudan_id = register.bjud_in(utforare, pid, epost, roll)
            return _json(201, {"status": "inbjuden", "id": inbjudan_id, "roll": roll})
        if len(delar) == 4 and delar[2] == "medlemmar":
            kid = delar[3]
            if not _id_ok(kid):
                return _fel(404, "okand", "Okänt konto")
            if metod == "PUT":
                kropp = _json_kropp(req)
                roll = kropp.get("roll")
                if not isinstance(roll, str) or roll not in PROJEKTROLLER:
                    return _fel(400, "ogiltigt", "roll måste vara lasare, utredare eller agare")
                register.projekt_medlemmar(utforare, pid)  # behörighet och projekt före kontot
                if register.konto(kid) is None:
                    return _fel(404, "okand", "Okänt konto")
                register.ge_projektroll(utforare, kid, pid, roll)
                return _json(200, {"status": "roll-satt", "konto_id": kid, "roll": roll})
            if metod == "DELETE":
                register.aterkalla_projektroll(utforare, kid, pid)
                return _json(200, {"status": "roll-borttagen", "konto_id": kid})
            return _metod_ej_tillaten()
    if delar[0] == "organisationer":
        return _admin_organisation(adapter, req, delar, utforare)
    if delar[0] == "konton" and len(delar) == 2:
        kid = delar[1]
        if not _id_ok(kid):
            return _fel(404, "okand", "Okänt konto")
        if metod != "PUT":
            return _metod_ej_tillaten()
        sparrad = _json_kropp(req).get("sparrad")
        if not isinstance(sparrad, bool):
            return _fel(400, "ogiltigt", "sparrad måste vara true eller false")
        register.satt_sparrad(utforare, kid, sparrad)
        return _json(200, {"status": "sparrad" if sparrad else "oppnad", "konto_id": kid})
    return _fel(404, "okand", "Okänd ändpunkt")


def _admin_organisation(
    adapter: KontoAdapter, req: Forfragan, delar: list[str], utforare: str
) -> Svar:
    """`delar` börjar med `organisationer`."""
    register = adapter.register
    assert register is not None  # noqa: S101
    metod = req.metod
    if len(delar) == 1:
        if metod != "GET":
            return _metod_ej_tillaten()
        return _json(200, {"organisationer": register.admin_organisationer(utforare)})
    oid = delar[1]
    if not _id_ok(oid):
        return _fel(404, "okand", "Okänd organisation")
    if len(delar) == 2:
        if metod == "PUT":
            namn = _json_kropp(req).get("namn")
            if not isinstance(namn, str):
                return _fel(400, "ogiltigt", "namn måste vara text")
            register.byt_organisationsnamn(utforare, oid, namn)
            return _json(200, {"status": "namn-andrat", "id": oid})
        if metod != "GET":
            return _metod_ej_tillaten()
        # Medlemmar och inbjudningar ger rätt fel (404 okänd, 403 saknad behörighet) före listan.
        medlemmar = register.organisation_medlemmar(utforare, oid)
        rad = next((o for o in register.admin_organisationer(utforare) if o["id"] == oid), None)
        if rad is None:
            return _fel(404, "okand", "Okänd organisation")
        return _json(
            200,
            {
                "organisation": rad,
                "roller": list(ORGROLLER),
                "medlemmar": medlemmar,
                "inbjudningar": register.organisation_inbjudningar(utforare, oid),
            },
        )
    if len(delar) == 3 and delar[2] == "inbjudningar":
        if metod != "POST":
            return _metod_ej_tillaten()
        kropp = _json_kropp(req)
        epost, roll = kropp.get("epost"), kropp.get("roll", "medlem")
        if not isinstance(epost, str) or not isinstance(roll, str):
            return _fel(400, "ogiltigt", "epost och roll måste vara text")
        if roll not in ORGROLLER:
            return _fel(
                400, "ogiltigt", "roll måste vara medlem, ledare, organisationsadmin eller agare"
            )
        inbjudan_id = register.bjud_in_organisation(utforare, oid, epost, roll)
        return _json(201, {"status": "inbjuden", "id": inbjudan_id, "roll": roll})
    if len(delar) == 4 and delar[2] == "medlemmar":
        kid = delar[3]
        if not _id_ok(kid):
            return _fel(404, "okand", "Okänt konto")
        if metod == "PUT":
            roll = _json_kropp(req).get("roll")
            if not isinstance(roll, str) or roll not in ORGROLLER:
                return _fel(
                    400,
                    "ogiltigt",
                    "roll måste vara medlem, ledare, organisationsadmin eller agare",
                )
            register.satt_organisationsroll(utforare, kid, oid, roll)
            return _json(200, {"status": "roll-satt", "konto_id": kid, "roll": roll})
        if metod == "DELETE":
            register.ta_bort_organisationsmedlem(utforare, kid, oid)
            return _json(200, {"status": "medlem-borttagen", "konto_id": kid})
        return _metod_ej_tillaten()
    return _fel(404, "okand", "Okänd ändpunkt")


__all__ = ["hantera_admin", "hantera_projekt", "hantera_mcp_token", "hantera_nycklar"]
