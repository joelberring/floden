"""Projektbehörighet på `/api/korningar/*` (och körningslistan) när inloggning är på."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from konto.adapter import KontoAdapter
from konto.konfig import KontoKonfig
from tests.konto.conftest import ANNA, Miljo, Webblasare
from tests.webb.test_inloggning import _app, _http
from tests.webb.test_projekt_per_anvandare import BO, _setup
from webb.api import FlodenApiApp
from webb.fragor import FragaTjanst

SUPER = {
    "sub": "sub-super-x3",
    "email": "super@example.test",
    "email_verified": True,
    "name": "Sam Super",
}
DEMO_KORNING = "demo-vatten"
UNDERRUTTER = (
    "",
    "/resultat",
    "/marksiffror?lager=x",
    "/avlasning?e=1&n=2&lager=x",
    "/lager/x.png",
    "/fil/a/b.txt",
    "/tabell/a.csv",
)


def skriv_korning(app: FlodenApiApp, korning_id: str, projekt_id: str | None = None) -> None:
    """Lägger ett körregister på disk (som `FragaTjanst` skriver det) utan att köra något."""
    mapp = app.fragor.korningsmapp / korning_id
    mapp.mkdir(parents=True)
    post: dict[str, Any] = {
        "korning_id": korning_id,
        "ref": "demo",
        "fraga_id": "demo",
        "fraga_fil": "demo.toml",
        "status": "klar",
        "skapad": "2026-11-23T10:00:00+00:00",
        "jobb_totalt": 0,
    }
    if projekt_id is not None:
        post["projekt_id"] = projekt_id
    (mapp / "korning.json").write_text(json.dumps(post), encoding="utf-8")


@pytest.fixture
def varld(
    tmp_path: Path, miljo: Miljo, monkeypatch: pytest.MonkeyPatch
) -> tuple[FlodenApiApp, dict[str, str], dict[str, Webblasare]]:
    """Anna (lasare i projekt A), Bo (utredare i projekt B), en superadmin och fyra körningar."""
    st = _setup(miljo)
    app = _app(tmp_path, miljo.adapter)
    for kid, projekt in (
        ("k-a", st.anna_projekt),
        ("k-b", st.bo_projekt),
        ("k-utan", None),
        (DEMO_KORNING, None),  # demokörning: knyts till demokvarteret via demo.json
    ):
        skriv_korning(app, kid, projekt)
    monkeypatch.setattr(
        FragaTjanst,
        "demo",
        lambda self: {"finns": True, "delar": {"demo-vatten": {"korning_id": DEMO_KORNING}}},
    )
    sup = miljo.register.forbered_konto("super@example.test")
    miljo.register.ge_superadmin(sup)
    webb = {"anna": miljo.webblasare(), "bo": miljo.webblasare(), "super": miljo.webblasare()}
    for namn, claims in (("anna", ANNA), ("bo", BO), ("super", SUPER)):
        assert webb[namn].logga_in(miljo.idp, claims)[0] == 302
    return app, {"a": "k-a", "b": "k-b", "utan": "k-utan", "demo": DEMO_KORNING}, webb


def _status(app: FlodenApiApp, w: Webblasare | None, sokvag: str) -> int:
    return _http(app, "GET", sokvag, w)[0]


def test_ratt_roll_i_projektet_ger_tillgang_till_alla_underrutter(varld: Any) -> None:
    app, k, webb = varld
    for rutt in UNDERRUTTER:
        # Tillåtet betyder "inte nekat": körningen saknar resultat, så svaret är 4xx/5xx i sak,
        # men aldrig "Okänd körning" från behörighetskontrollen.
        status, _, kropp = _http(app, "GET", f"/api/korningar/{k['a']}{rutt}", webb["anna"])
        assert "Okänd körning" not in kropp.decode(), (rutt, status)
    assert _status(app, webb["anna"], f"/api/korningar/{k['a']}") == 200


def test_fel_projekt_ger_404_som_en_okand_korning(varld: Any) -> None:
    app, k, webb = varld
    for rutt in UNDERRUTTER:
        nekad = _http(app, "GET", f"/api/korningar/{k['b']}{rutt}", webb["anna"])
        okand = _http(app, "GET", f"/api/korningar/finns-inte{rutt}", webb["super"])
        assert nekad[0] == 404, rutt
        # Samma svar som för ett id som inte finns, så att id:n inte går att pröva sig fram till.
        assert json.loads(nekad[2])["fel"] == f"Okänd körning '{k['b']}'."
        assert okand[0] == 404
    # Bos körning är läsbar för Bo, som har en roll i projektet.
    assert _status(app, webb["bo"], f"/api/korningar/{k['b']}") == 200
    assert _status(app, webb["bo"], f"/api/korningar/{k['a']}") == 404


def test_korning_utan_projekt_ar_bara_for_superadmin(varld: Any) -> None:
    app, k, webb = varld
    assert _status(app, webb["anna"], f"/api/korningar/{k['utan']}") == 404
    assert _status(app, webb["anna"], f"/api/korningar/{k['utan']}/resultat") == 404
    assert _status(app, webb["super"], f"/api/korningar/{k['utan']}") == 200
    assert _status(app, None, f"/api/korningar/{k['utan']}") == 401


def test_demokorning_foljer_demoprojekt_roll(varld: Any, miljo: Miljo) -> None:
    app, k, webb = varld
    assert _status(app, webb["anna"], f"/api/korningar/{k['demo']}") == 200
    assert _status(app, webb["bo"], f"/api/korningar/{k['demo']}") == 200
    miljo.adapter.konfig = replace(miljo.adapter.konfig, demoprojekt_roll="av")
    assert _status(app, webb["anna"], f"/api/korningar/{k['demo']}") == 404
    # Superadmin har ingen roll i demokvarteret heller: projektdata kräver en roll.
    assert _status(app, webb["super"], f"/api/korningar/{k['demo']}") == 404


def test_superadmin_utan_projektroll_ser_inte_projektets_korning(varld: Any) -> None:
    app, k, webb = varld
    assert _status(app, webb["super"], f"/api/korningar/{k['a']}") == 404


def test_lista_visar_bara_korningar_kontot_har_ratt_till(varld: Any) -> None:
    app, k, webb = varld

    def ids(w: Webblasare) -> set[str]:
        status, _, kropp = _http(app, "GET", "/api/korningar", w)
        assert status == 200
        return {p["korning_id"] for p in json.loads(kropp)["korningar"]}

    assert ids(webb["anna"]) == {k["a"], k["demo"]}
    assert ids(webb["bo"]) == {k["b"], k["demo"]}
    # Superadmin har ingen projektroll, men demokvarteret är öppet för varje inloggat konto.
    assert ids(webb["super"]) == {k["utan"], k["demo"]}
    assert _status(app, None, "/api/korningar") == 401


def test_inloggning_av_fungerar_som_forut(tmp_path: Path) -> None:
    app = _app(tmp_path, KontoAdapter(KontoKonfig()))
    skriv_korning(app, "k-utan")
    skriv_korning(app, "k-a", "nagot-projekt")
    status, _, kropp = _http(app, "GET", "/api/korningar")
    assert status == 200
    assert {p["korning_id"] for p in json.loads(kropp)["korningar"]} == {"k-utan", "k-a"}
    for kid in ("k-utan", "k-a"):
        assert _status(app, None, f"/api/korningar/{kid}") == 200
    assert _status(app, None, "/api/korningar/finns-inte") == 404


def test_projekt_id_sparas_i_korregistret(tmp_path: Path) -> None:
    app = _app(tmp_path, KontoAdapter(KontoKonfig()))
    skriv_korning(app, "k-a", "p-1")
    skriv_korning(app, "k-0")
    k = app.fragor.korning_eller_none("k-a")
    assert k is not None and k.projekt_id == "p-1"
    assert app.projekt_for_korning("k-a") == "p-1"
    assert app.projekt_for_korning("saknas") is None
    # Skrivs bara när ett projekt är satt, så äldre körregister ser ut som förut.
    assert "projekt_id" in k.som_dict()
    k0 = app.fragor.korning_eller_none("k-0")
    assert k0 is not None and "projekt_id" not in k0.som_dict()
