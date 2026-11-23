"""MCP och kontosystemet: projektlistan per konto, och att AI:n aldrig når nycklar eller admin."""

from __future__ import annotations

import re
from pathlib import Path

import anyio
import pytest
from mcp import Client

from floden_mcp.dataklass import Policy
from floden_mcp.karna_api import FlodenTjanst
from floden_mcp.korlogg import Korlogg
from floden_mcp.server import bygg_server
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_inloggning import bjud_in_anna
from tests.webb.test_inloggning import _app
from tests.webb.test_projekt_per_anvandare import DEMO, _med_demo

MCP_KALLKOD = Path(__file__).resolve().parents[2] / "src" / "floden_mcp"


def test_projekt_lista_ar_per_konto_nar_inloggning_ar_pa(
    tmp_path: Path, miljo: Miljo, monkeypatch: pytest.MonkeyPatch
) -> None:
    projekt_id = bjud_in_anna(miljo)
    app = _app(tmp_path, miljo.adapter)
    _med_demo(app, monkeypatch)
    tjanst = FlodenTjanst(app, rot=tmp_path, mcp_mapp=tmp_path / "mcp")
    webb = miljo.webblasare()
    webb.logga_in(miljo.idp, ANNA)
    konto = miljo.register.konto_via_epost("anna@example.test")
    assert konto is not None

    svar = tjanst.projekt_lista(konto.id)
    assert [(p["id"], p["roll"]) for p in svar["projekt"]] == [
        (DEMO["id"], "utredare"),
        (projekt_id, "lasare"),
    ]
    # Utan konto (stdio mot en server med inloggning): inga projekt och en förklaring.
    tom = tjanst.projekt_lista(None)
    assert tom["projekt"] == [] and "Inloggning krävs" in str(tom["tips"])
    # Ett annat konto får inte Annas projekt.
    bo = miljo.register.forbered_konto("bo@example.test")
    assert [p["id"] for p in tjanst.projekt_lista(bo)["projekt"]] == [DEMO["id"]]


def test_ai_kan_aldrig_lasa_eller_skriva_nycklar_eller_administrera(
    tmp_path: Path, miljo: Miljo
) -> None:
    app = _app(tmp_path, miljo.adapter)
    tjanst = FlodenTjanst(app, rot=tmp_path, mcp_mapp=tmp_path / "mcp")
    server = bygg_server(
        tjanst, policy=Policy(), logg=Korlogg(tmp_path / "mcp" / "korlogg.jsonl"), miljo={}
    )

    async def _lista() -> list[tuple[str, str]]:
        async with Client(server) as c:
            return [(t.name, t.description or "") for t in (await c.list_tools()).tools]

    verktyg = anyio.run(_lista)
    # Verktygens namn: inget som rör nycklar, konton eller administration. (Beskrivningarna får
    # tala om cache-nycklar; där letas bara efter API-nycklar, hemligheter och administration.)
    namn_forbjudet = re.compile(
        r"nyckel|nycklar|hemlighet|api.?key|anthropic|konto|admin|inbjud|sparr", re.I
    )
    text_forbjudet = re.compile(r"hemlighet|api.?key|anthropic|inbjud|administrat", re.I)
    for namn, beskrivning in verktyg:
        assert not namn_forbjudet.search(namn), namn
        assert not text_forbjudet.search(beskrivning), (namn, beskrivning)
    # Koden i MCP-servern rör inte nyckelförrådet eller administrationen alls.
    for fil in MCP_KALLKOD.glob("*.py"):
        text = fil.read_text(encoding="utf-8")
        for tabu in ("konto.nycklar", "NyckelForrad", "konto_nyckel", "kontoapi", "satt_sparrad"):
            assert tabu not in text, (fil.name, tabu)
