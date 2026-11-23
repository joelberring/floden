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


def test_korningsverktyg_foljer_samma_projektbehorighet_som_webben(
    tmp_path: Path, miljo: Miljo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from floden_mcp.server import Sessionstillstand
    from tests.webb.test_korningar_behorighet import skriv_korning

    projekt_id = bjud_in_anna(miljo)
    app = _app(tmp_path, miljo.adapter)
    skriv_korning(app, "k-anna", projekt_id)
    skriv_korning(app, "k-annat", "ett-annat-projekt")
    skriv_korning(app, "k-utan")
    tjanst = FlodenTjanst(app, rot=tmp_path, mcp_mapp=tmp_path / "mcp")
    server = bygg_server(
        tjanst, policy=Policy(), logg=Korlogg(tmp_path / "mcp" / "korlogg.jsonl"), miljo={}
    )
    assert miljo.webblasare().logga_in(miljo.idp, ANNA)[0] == 302
    anna = miljo.register.konto_via_epost("anna@example.test")
    assert anna is not None
    vem: dict[str, str | None] = {"konto": anna.id}
    monkeypatch.setattr(Sessionstillstand, "konto", property(lambda self: vem["konto"]))

    def anropa(namn: str, argument: dict[str, object]) -> tuple[bool, str]:
        async def _k() -> tuple[bool, str]:
            async with Client(server) as c:
                r = await c.call_tool(namn, argument)
                text = r.content[0].text if r.content else ""  # type: ignore[union-attr]
                return bool(r.is_error), text

        return anyio.run(_k)

    # Annas egen körning går, de andras är "okända": samma svar som för ett id som saknas.
    assert anropa("korning_status", {"korning_id": "k-anna"})[0] is False
    for kid in ("k-annat", "k-utan", "finns-inte"):
        fel, text = anropa("korning_status", {"korning_id": kid})
        assert fel and text == f"Okänd körning '{kid}'."
    # Id:n i listor (vanta, rapport_skapa) prövas på samma sätt.
    assert anropa("vanta", {"korning_ids": ["k-anna", "k-annat"], "tidsgrans_s": 0}) == (
        True,
        "Okänd körning 'k-annat'.",
    )
    # Listan visar bara det kontot får se.
    assert [k["korning_id"] for k in tjanst.korningar_lista(20, anna.id)["korningar"]] == ["k-anna"]
    # Utan konto (stdio mot en server med inloggning) når inget i körregistret.
    vem["konto"] = None
    assert anropa("korning_status", {"korning_id": "k-anna"})[0] is True
    assert tjanst.korningar_lista(20, None)["korningar"] == []
    # Superadmin: körningar utan projekt, men inte projektets (ingen roll där).
    sup = miljo.register.forbered_konto("super@example.test")
    miljo.register.ge_superadmin(sup)
    vem["konto"] = sup
    assert anropa("korning_status", {"korning_id": "k-utan"})[0] is False
    assert anropa("korning_status", {"korning_id": "k-anna"})[0] is True


def test_korningsverktyg_oforandrade_med_inloggning_av(tmp_path: Path) -> None:
    from konto.adapter import KontoAdapter
    from konto.konfig import KontoKonfig
    from tests.webb.test_korningar_behorighet import skriv_korning

    app = _app(tmp_path, KontoAdapter(KontoKonfig()))
    skriv_korning(app, "k-utan")
    tjanst = FlodenTjanst(app, rot=tmp_path, mcp_mapp=tmp_path / "mcp")
    assert [k["korning_id"] for k in tjanst.korningar_lista(20, None)["korningar"]] == ["k-utan"]
    assert app.konto_far_se_korning(None, "k-utan") is True


def test_mcp_kor_sparar_projektet_i_korregistret(
    tmp_path: Path, miljo: Miljo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.webb.test_korningar_behorighet import _stubba_start

    app = _app(tmp_path, miljo.adapter)
    _stubba_start(monkeypatch)
    tjanst = FlodenTjanst(app, rot=tmp_path, mcp_mapp=tmp_path / "mcp")
    svar = tjanst.kor(projekt="demokvarteret", fraga="demo_vatten")
    kid = svar["korningar"][0]["korning_id"]
    assert app.projekt_for_korning(kid) == "demokvarteret"
