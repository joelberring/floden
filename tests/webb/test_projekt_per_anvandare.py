"""`GET /api/projekt` med inloggning: varje konto ser bara sina projekt (demokvarteret inräknat)."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from tests.konto.conftest import ANNA, Miljo
from tests.webb.test_inloggning import _app, _http
from webb.api import FlodenApiApp
from webb.projekt import ProjektTjanst

BO = {"sub": "sub-bo-x2", "email": "bo@example.test", "email_verified": True, "name": "Bo Exempel"}
DEMO = {
    "id": "demokvarteret",
    "namn": "Demokvarteret",
    "undertitel": "påhittat kvarter",
    "paahittat": True,
    "beskrivning": "Fyra kvarter.",
}


def _med_demo(app: FlodenApiApp, monkeypatch: pytest.MonkeyPatch) -> None:
    # `app.projekt` skapas på nytt vid varje anrop, så klassen byts ut.
    monkeypatch.setattr(ProjektTjanst, "lista", lambda self: {"projekt": [dict(DEMO)]})


def _projekt(app: FlodenApiApp, webb: Any) -> list[dict[str, Any]]:
    status, _, k = _http(app, "GET", "/api/projekt", webb)
    assert status == 200
    return list(json.loads(k)["projekt"])


@dataclass
class Setup:
    org: str
    admin: str
    anna_projekt: str
    bo_projekt: str


def _setup(miljo: Miljo) -> Setup:
    """Anna har rollen lasare i ett projekt och Bo i ett annat (båda i samma organisation)."""
    r = miljo.register
    org = r.skapa_organisation("Exempelkommun")
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, org)
    pa, pb = r.skapa_projekt(org, "Provområdet"), r.skapa_projekt(org, "Annat projekt")
    r.bjud_in(admin, pa, "anna@example.test")
    r.bjud_in(admin, pb, "bo@example.test", "utredare")
    return Setup(org, admin, pa, pb)


def test_inloggad_ser_demokvarteret_och_sina_egna_projekt(
    tmp_path: Path, miljo: Miljo, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = _setup(miljo)
    app = _app(tmp_path, miljo.adapter)
    _med_demo(app, monkeypatch)
    anna, bo = miljo.webblasare(), miljo.webblasare()
    assert anna.logga_in(miljo.idp, ANNA)[0] == 302
    assert bo.logga_in(miljo.idp, BO)[0] == 302

    lista = _projekt(app, anna)
    assert [(p["id"], p["roll"], p["finns"]) for p in lista] == [
        ("demokvarteret", "utredare", True),
        (st.anna_projekt, "lasare", False),
    ]
    assert lista[1]["namn"] == "Provområdet"
    # Bo ser demokvarteret och sitt eget projekt, aldrig Annas.
    assert [(p["id"], p["roll"]) for p in _projekt(app, bo)] == [
        ("demokvarteret", "utredare"),
        (st.bo_projekt, "utredare"),
    ]


def test_demoprojekt_roll_av_doljer_demokvarteret(
    tmp_path: Path, miljo: Miljo, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = _setup(miljo)
    miljo.adapter.konfig = replace(miljo.adapter.konfig, demoprojekt_roll="av")
    app = _app(tmp_path, miljo.adapter)
    _med_demo(app, monkeypatch)
    anna = miljo.webblasare()
    anna.logga_in(miljo.idp, ANNA)
    assert [p["id"] for p in _projekt(app, anna)] == [st.anna_projekt]


def test_projekt_ur_registret_kan_hamtas_av_den_med_roll_men_ingen_annan(
    tmp_path: Path, miljo: Miljo
) -> None:
    st = _setup(miljo)
    app = _app(tmp_path, miljo.adapter)
    anna, bo = miljo.webblasare(), miljo.webblasare()
    anna.logga_in(miljo.idp, ANNA)
    bo.logga_in(miljo.idp, BO)
    status, _, k = _http(app, "GET", f"/api/projekt/{st.anna_projekt}", anna)
    svar = json.loads(k)
    assert status == 200 and svar["namn"] == "Provområdet" and svar["finns"] is False
    # Bo har ingen roll i Annas projekt, och ett okänt projekt nekas på samma sätt.
    assert _http(app, "GET", f"/api/projekt/{st.anna_projekt}", bo)[0] == 403
    assert _http(app, "GET", f"/api/projekt/{'0' * 32}", anna)[0] == 403
    # Tas rollen bort försvinner projektet ur listan.
    anna_id = miljo.register.konto_via_epost("anna@example.test")
    assert anna_id is not None
    miljo.register.aterkalla_projektroll(st.admin, anna_id.id, st.anna_projekt)
    assert _projekt(app, anna) == []  # demokvarteret är inte byggt i det här testet
    assert _http(app, "GET", f"/api/projekt/{st.anna_projekt}", anna)[0] == 403


def test_utloggad_far_401_och_inloggning_av_ger_samma_lista_som_forut(
    tmp_path: Path, miljo: Miljo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from konto.adapter import KontoAdapter
    from konto.konfig import KontoKonfig

    app = _app(tmp_path, miljo.adapter)
    assert _http(app, "GET", "/api/projekt")[0] == 401
    (tmp_path / "lokal").mkdir()
    lokal = _app(tmp_path / "lokal", KontoAdapter(KontoKonfig()))
    _med_demo(lokal, monkeypatch)
    assert lokal.projekt_lista_for(None)["projekt"] == [DEMO]
    status, _, k = _http(lokal, "GET", "/api/projekt")
    assert status == 200 and json.loads(k)["projekt"] == [DEMO]
