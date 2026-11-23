"""API:t under `/konto/nycklar` och `/konto/admin` (anropas av `KontoAdapter.hantera_konto`).

Båda kräver inloggning, och PUT/POST/DELETE kräver CSRF (kaka + `X-Floden-CSRF`) och rätt `Origin`,
precis som övriga skrivande anrop (`KontoAdapter.kraver_inloggning`).

Nycklar (`/konto/nycklar`, bara kontots egna; ingen väg att läsa en annans):

- `GET    /konto/nycklar`          alla typer med status och om funktionen är påslagen
- `GET    /konto/nycklar/<typ>`    en typ: `sparad`, `sparad_datum`, `slutar_pa` (aldrig nyckeln)
- `PUT    /konto/nycklar/<typ>`    sparar eller ersätter (kryptering krävs: annars 503 `nycklar-av`)
- `DELETE /konto/nycklar/<typ>`    tar bort

Administration (`/konto/admin`, bara superadmin och organisationsadmin, annars 403):

- `GET    /konto/admin/projekt`                              projekt som får administreras
- `GET    /konto/admin/projekt/<id>`                         projekt, medlemmar och inbjudningar
- `POST   /konto/admin/projekt/<id>/inbjudningar`   `{"epost", "roll"}`, roll `lasare`/`utredare`
- `PUT    /konto/admin/projekt/<id>/medlemmar/<konto>`       `{"roll"}` ändrar (eller ger) roll
- `DELETE /konto/admin/projekt/<id>/medlemmar/<konto>`       tar bort projektrollen
- `PUT    /konto/admin/konton/<konto>`                       `{"sparrad": true|false}`

Svaren har aldrig en nyckel, och felen nämner fält men aldrig värden.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from konto.adapter import Forfragan, Svar, _fel, _json
from konto.nycklar import NyckelForradFel
from konto.register import PROJEKTROLLER, BehorighetFel, RegisterFel

if TYPE_CHECKING:
    from konto.adapter import KontoAdapter

logg = logging.getLogger("konto")

MAX_KROPP = 4096
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


# ------------------------------------------------------------------------------- admin


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
        return _fel(404 if text.startswith("Okänt") else 400, "ogiltigt", text)


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
                    return _fel(400, "ogiltigt", "roll måste vara lasare eller utredare")
                register.projekt_medlemmar(utforare, pid)  # behörighet och projekt före kontot
                if register.konto(kid) is None:
                    return _fel(404, "okand", "Okänt konto")
                register.ge_projektroll(utforare, kid, pid, roll)
                return _json(200, {"status": "roll-satt", "konto_id": kid, "roll": roll})
            if metod == "DELETE":
                register.aterkalla_projektroll(utforare, kid, pid)
                return _json(200, {"status": "roll-borttagen", "konto_id": kid})
            return _metod_ej_tillaten()
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


__all__ = ["hantera_admin", "hantera_nycklar"]
