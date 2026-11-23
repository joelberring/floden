# ruff: noqa: E501
"""Roller i MCP-verktygen: bara projekt kontot har en roll i, och skrivande verktyg kräver utredare."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from starlette.testclient import TestClient

from floden_mcp.dataklass import DataKlass, Policy
from floden_mcp.server import SKRIVVERKTYG, UTAN_ROLLKRAV
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp_http import BO, Stack, _initiera, _skapa_token, anropa_verktyg
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_nycklar import _inloggad
from tests.konto.test_projekt_per_anvandare_hjalp import setup_projekt


def _klient(
    demo: Demo, miljo: Miljo, tmp_path: Path, *projekt: str
) -> tuple[Stack, TestClient]:
    klasser = {"demokvarteret": DataKlass.OPPET, **{p: DataKlass.OPPET for p in projekt}}
    stack = Stack(demo, miljo, tmp_path, behorighet=True, policy=Policy(projektklasser=klasser))
    return stack, TestClient(stack.skydd, base_url="http://floden.test")


def _med_token(miljo: Miljo, claims: dict[str, str | bool]) -> str:
    webb, _ = _inloggad(miljo, claims)  # type: ignore[arg-type]
    return str(_skapa_token(webb)["token"])


def test_projekt_lista_visar_bara_kontots_projekt_och_andra_nekas(
    demo: Demo, miljo: Miljo, tmp_path: Path
) -> None:
    ids = setup_projekt(miljo)  # Anna: lasare i ett projekt. Bo: utredare i ett annat.
    ta, tb = _med_token(miljo, ANNA), _med_token(miljo, BO)
    stack, klient = _klient(demo, miljo, tmp_path, ids.anna_projekt, ids.bo_projekt)
    with klient:
        a = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        b = _initiera(klient, {"Authorization": f"Bearer {tb}"})
        fel, _, lista = anropa_verktyg(klient, a, "projekt_lista")
        assert not fel
        assert {p["id"] for p in lista["projekt"]} == {"demokvarteret", ids.anna_projekt}
        _, _, lista_b = anropa_verktyg(klient, b, "projekt_lista")
        assert {p["id"] for p in lista_b["projekt"]} == {"demokvarteret", ids.bo_projekt}
        # Annas projekt syns inte för Bo, och Bo får inte läsa det.
        fel, text, _ = anropa_verktyg(klient, b, "projekt_info", {"projekt": ids.anna_projekt})
        assert fel and "saknar rollen lasare" in text
        # Annas roll (lasare) räcker för att läsa sitt projekt: rollkontrollen släpper igenom och
        # verktyget säger sedan själv att projektet inte har några beräkningar.
        fel, text, _ = anropa_verktyg(klient, a, "projekt_info", {"projekt": ids.anna_projekt})
        assert fel and "saknar rollen" not in text
    nekade = [r for r in stack.logg.las() if r["utfall"] == "nekad_behorighet"]
    assert [r["verktyg"] for r in nekade] == ["projekt_info"]
    assert nekade[0]["konto"] and "@" not in str(nekade[0])


def test_skrivande_verktyg_kraver_utredare(demo: Demo, miljo: Miljo, tmp_path: Path) -> None:
    ids = setup_projekt(miljo)
    ta, tb = _med_token(miljo, ANNA), _med_token(miljo, BO)
    stack, klient = _klient(demo, miljo, tmp_path, ids.anna_projekt, ids.bo_projekt)
    with klient:
        a = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        b = _initiera(klient, {"Authorization": f"Bearer {tb}"})
        # Anna är bara lasare i sitt projekt: skrivande verktyg nekas, läsande går.
        for verktyg in ("scenario_skapa", "fraga_skapa", "kor", "bestallning_skapa"):
            fel, text, _ = anropa_verktyg(klient, a, verktyg, {"projekt": ids.anna_projekt})
            assert fel and "saknar rollen utredare" in text, verktyg
        # Bo är utredare i sitt projekt: rollkontrollen släpper igenom (verktyget klagar på annat).
        fel, text, _ = anropa_verktyg(klient, b, "scenario_skapa", {"projekt": ids.bo_projekt})
        assert "saknar rollen" not in text
        # Demokvarteret: standardrollen utredare gäller. Stängs den till lasare nekas skrivning.
        miljo.adapter.konfig = replace(miljo.adapter.konfig, demoprojekt_roll="lasare")
        fel, _, _ = anropa_verktyg(klient, a, "projekt_info")
        assert not fel
        fel, text, _ = anropa_verktyg(klient, a, "scenario_skapa", {})
        assert fel and "saknar rollen utredare" in text
        fel, text, _ = anropa_verktyg(klient, a, "kor", {})
        assert fel and "saknar rollen utredare" in text
        miljo.adapter.konfig = replace(miljo.adapter.konfig, demoprojekt_roll="av")
        fel, text, _ = anropa_verktyg(klient, a, "projekt_info")
        assert fel and "saknar rollen lasare" in text
        fel, text, _ = anropa_verktyg(klient, a, "korningar_lista")  # utan projektargument: demon
        assert fel and "saknar rollen lasare" in text
    assert any(r["utfall"] == "nekad_behorighet" for r in stack.logg.las())


def test_verktyg_utan_konto_nekas_nar_inloggning_ar_pa(demo: Demo, miljo: Miljo, tmp_path: Path) -> None:
    """stdio (inget konto) mot en server med inloggning: projektverktygen nekas, med en förklaring."""
    import anyio
    from mcp import Client

    stack = Stack(demo, miljo, tmp_path, behorighet=True)

    async def kor() -> tuple[bool, str]:
        async with Client(stack.server) as c:
            r = await c.call_tool("projekt_info", {})
            return bool(r.is_error), str(r.content[0].text)  # type: ignore[union-attr]

    fel, text = anyio.run(kor)
    assert fel and "Inloggning krävs" in text


def test_verktygsklassningen_ar_fullstandig(demo: Demo, miljo: Miljo, tmp_path: Path) -> None:
    """Varje registrerat verktyg är antingen undantaget, skrivande eller läsande: inget glöms bort."""
    import anyio
    from mcp import Client

    stack = Stack(demo, miljo, tmp_path, behorighet=True)

    async def namn() -> set[str]:
        async with Client(stack.server) as c:
            return {t.name for t in (await c.list_tools()).tools}

    alla = anyio.run(namn)
    assert alla >= SKRIVVERKTYG and alla >= UTAN_ROLLKRAV
    assert not (SKRIVVERKTYG & UTAN_ROLLKRAV)
    skrivande_ord = ("skapa", "kor", "lagg_till", "exportera", "installningar")
    for v in alla - SKRIVVERKTYG - UTAN_ROLLKRAV:
        assert not any(o == v or v.endswith("_" + o) for o in skrivande_ord), f"{v} verkar skriva"
