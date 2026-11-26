# ruff: noqa: E501
"""Regressionstester för Våg 5 Pass 4 MCP/K38-fixar (`v5-finputs`):

1. Bugg 1 (P0 · K38-läcka): `bestallning_hamta(bestallning_id=...)` utan (eller med öppet)
   `projekt`-argument löser upp beställningens faktiska projekt i `mellanvara` + `verktyg`,
   blockerar stängda projekt via K38 utan läckage, tillåter behörig användare i beställningens
   öppna projekt utan roll i `demokvarteret` med korrekt `dataklass`, samt efterkontroll i
   `_kontrollera_svar_oppenhet` över `CallToolResult.structuredContent` och `content`.
2. Bugg 3 (P1 · K38-förkontroll): `rapport_skapa(korningar=[stangd])` stoppas i K38-förkontrollen
   INNAN `rapporter.skapa()` anropas, så 0 rapportfiler skrivs på disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from floden_mcp.dataklass import DataKlass, Policy
from floden_mcp.http import KontoSkydd
from floden_mcp.server import bygg_server
from rapport.bestallning import skapa_bestallning
from rapport.skissrapport import RapportTjanst
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp import Mcp
from tests.floden_mcp.test_mcp_http import (
    BO,
    Stack,
    _initiera,
    _rpc,
    anropa_verktyg,
)
from tests.floden_mcp.test_oppenhet import _kopiera_korning
from tests.floden_mcp.test_v1_mcp_sakerhet import _skriv_projekt, _token
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_projekt_per_anvandare_hjalp import setup_projekt


def _initiera_klient(klient: TestClient, rubriker: dict[str, str], namn: str) -> dict[str, str]:
    svar = _rpc(
        klient,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": namn, "version": "1"}},
        rubriker,
    )
    assert svar.status_code == 200, svar.text
    sid = svar.headers.get("mcp-session-id")
    ut = dict(rubriker)
    if sid:
        ut["Mcp-Session-Id"] = sid
    klient.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **ut},
    )
    return ut


def test_bestallning_hamta_stangt_projekt_blockeras_av_k38_utan_projekt_argument(
    demo: Demo, tmp_path: Path
) -> None:
    """Bugg 1 (P0): `bestallning_hamta(bestallning_id=<stängt projekt>)` blockeras av K38 både
    utan `projekt` och med `projekt='demokvarteret'`; öppen beställning tillåts med `oppet`."""
    mcp = Mcp(demo, tmp_path / "m")
    mcp.app.rapportmapp = tmp_path / "rapporter"
    mcp.app.projektmapp = tmp_path / "projekt"
    mcp.app.kartverktyg.projektrot = tmp_path / "projekt"
    mcp.server = bygg_server(mcp.tjanst, policy=Policy(), logg=mcp.logg, miljo={})

    stangt_pid = "a" * 32
    oppet_pid = "b" * 32
    _skriv_projekt(
        mcp.app.projektmapp,
        "sthlm-stangt",
        stangt_pid,
        oppen=False,
        fraga_namn="sthlm-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )
    _skriv_projekt(
        mcp.app.projektmapp,
        "goteborg-oppet",
        oppet_pid,
        oppen=True,
        fraga_namn="gbg-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )

    rt = RapportTjanst(mcp.tjanst, rapportmapp=mcp.app.rapportmapp)
    stangd_b = skapa_bestallning(
        rt.katalog,
        syfte="HEMLIGT_STANGT_SYFTE_BASKARTA",
        amnen=["skyfall"],
        projekt=stangt_pid,
        fragor=["SK-01", "SK-06"],
        scenario={"fore": "bas", "efter": ["hemligt_forslag"]},
    )
    rt.bestallningar.spara(stangd_b)
    oppen_b = skapa_bestallning(
        rt.katalog,
        syfte="OPPET_PROJEKT_SYFTE",
        amnen=["skyfall"],
        projekt=oppet_pid,
        fragor=["SK-01"],
    )
    rt.bestallningar.spara(oppen_b)

    # 1. Utan `projekt` och med `projekt='demokvarteret'`: båda måste stoppas av K38 utan läckage.
    for arg in (
        {"bestallning_id": stangd_b.bestallning_id},
        {"bestallning_id": stangd_b.bestallning_id, "projekt": "demokvarteret"},
    ):
        r = mcp.anropa("bestallning_hamta", arg)
        assert r.is_error, arg
        text = str(r.content)
        assert "inte öppna data" in text
        for hemlig in (stangt_pid, "HEMLIGT_STANGT_SYFTE_BASKARTA", "hemligt_forslag", "SK-06"):
            assert hemlig not in text
        assert r.structured_content is None

    # 2. Öppen beställning utan `projekt`: släpps igenom med korrekt dataklass `oppet`.
    ok = mcp.ok("bestallning_hamta", {"bestallning_id": oppen_b.bestallning_id})
    assert ok["dataklass"] == "oppet"
    assert ok["projekt"] == oppet_pid
    assert ok["syfte"] == "OPPET_PROJEKT_SYFTE"

    # 3. Körloggen registrerade `nekad_oppenhet` för de blockerade anropen.
    nekade = [rad for rad in mcp.logg.las() if rad["utfall"] == "nekad_oppenhet"]
    assert len(nekade) == 2 and all(rad["verktyg"] == "bestallning_hamta" for rad in nekade)


def test_bestallning_hamta_behorighet_utan_demokvarteret_roll_och_korrekt_dataklass(
    demo: Demo, miljo: Miljo, tmp_path: Path
) -> None:
    """Bugg 1 (RBAC + dataklass): behörig användare i beställningens öppna projekt UTAN roll i
    `demokvarteret` släpps igenom med korrekt `dataklass`; `internt`-projekt följer policyn;
    stängt projekt blockeras av K38 även för intern klient; annat projekt ser ut som okänt."""
    ids = setup_projekt(miljo)  # Anna: lasare i anna_projekt; Bo: utredare i bo_projekt
    ta, tb = _token(miljo, ANNA), _token(miljo, BO)

    projektrot = tmp_path / "projekt"
    oppet_pid = ids.anna_projekt
    internt_pid = "c" * 32
    stangt_pid = "d" * 32
    _skriv_projekt(
        projektrot,
        "anna-oppet",
        oppet_pid,
        oppen=True,
        fraga_namn="anna-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )
    _skriv_projekt(
        projektrot,
        "anna-internt",
        internt_pid,
        oppen=True,
        fraga_namn="intern-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )
    _skriv_projekt(
        projektrot,
        "anna-stangt",
        stangt_pid,
        oppen=False,
        fraga_namn="stangt-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )

    anna_konto = miljo.register.konto_via_epost("anna@example.test")
    assert anna_konto is not None
    anna_id = anna_konto.id

    def strikt_behorighet(konto_id: str, projekt_id: str, minst: str) -> bool:
        # Ingen har roll i `demokvarteret`: tvingar `bestallning_hamta` att pröva beställningens projekt.
        if projekt_id == "demokvarteret":
            return False
        if konto_id == anna_id:
            return projekt_id in {oppet_pid, internt_pid, stangt_pid} and minst == "lasare"
        return bool(miljo.adapter.har_roll(konto_id, projekt_id, minst))

    policy = Policy(
        standard=frozenset({DataKlass.OPPET}),
        per_klient={"intern-klient": frozenset({DataKlass.OPPET, DataKlass.INTERNT})},
        projektklasser={internt_pid: DataKlass.INTERNT},
    )
    stack = Stack(demo, miljo, tmp_path, behorighet=True, policy=policy)
    stack.mcp.app.rapportmapp = tmp_path / "rapporter"
    stack.mcp.app.projektmapp = projektrot
    stack.mcp.app.kartverktyg.projektrot = projektrot
    stack.server = bygg_server(
        stack.mcp.tjanst,
        policy=policy,
        logg=stack.logg,
        miljo={},
        behorighet=strikt_behorighet,
    )
    skydd = KontoSkydd(stack.server.streamable_http_app(host="0.0.0.0"), miljo.adapter)  # noqa: S104

    rt = RapportTjanst(stack.mcp.tjanst, rapportmapp=stack.mcp.app.rapportmapp, policy=policy)
    b_oppet = skapa_bestallning(rt.katalog, syfte="Öppen beställning", amnen=["skyfall"], projekt=oppet_pid)
    b_internt = skapa_bestallning(rt.katalog, syfte="Intern beställning", amnen=["skyfall"], projekt=internt_pid)
    b_stangt = skapa_bestallning(
        rt.katalog, syfte="STANGD_HEMLIG_TEXT", amnen=["skyfall"], projekt=stangt_pid
    )
    for b in (b_oppet, b_internt, b_stangt):
        rt.bestallningar.spara(b)

    with TestClient(skydd, base_url="http://floden.test") as klient:
        a_ext = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        a_int = _initiera_klient(klient, {"Authorization": f"Bearer {ta}"}, "intern-klient")
        b_ext = _initiera(klient, {"Authorization": f"Bearer {tb}"})

        # 1. Anna saknar roll i demokvarteret, men har lasare i `oppet_pid` -> släpps igenom!
        fel, text, svar = anropa_verktyg(
            klient, a_ext, "bestallning_hamta", {"bestallning_id": b_oppet.bestallning_id}
        )
        assert not fel, text
        assert svar["dataklass"] == "oppet"
        assert svar["projekt"] == oppet_pid

        # 2. Beställning i `internt`-klassat projekt nekas extern klient men tillåts intern med `internt`.
        fel_ext, text_ext, _ = anropa_verktyg(
            klient, a_ext, "bestallning_hamta", {"bestallning_id": b_internt.bestallning_id}
        )
        assert fel_ext and "internt" in text_ext
        fel_int, text_int, svar_int = anropa_verktyg(
            klient, a_int, "bestallning_hamta", {"bestallning_id": b_internt.bestallning_id}
        )
        assert not fel_int, text_int
        assert svar_int["dataklass"] == "internt"
        assert svar_int["projekt"] == internt_pid

        # 3. Beställning i stängt K38-projekt blockeras även för intern klient utan läckage.
        fel_st, text_st, _ = anropa_verktyg(
            klient, a_int, "bestallning_hamta", {"bestallning_id": b_stangt.bestallning_id}
        )
        assert fel_st and "inte öppna data" in text_st
        assert "STANGD_HEMLIG_TEXT" not in text_st

        # 4. Bo saknar roll i Annas öppna projekt -> ser ut som okänd beställning.
        fel_bo, text_bo, _ = anropa_verktyg(
            klient, b_ext, "bestallning_hamta", {"bestallning_id": b_oppet.bestallning_id}
        )
        assert fel_bo and text_bo == f"Okänd beställning '{b_oppet.bestallning_id}'."


def test_efterkontroll_blockerar_stangt_projekt_i_structured_content(
    demo: Demo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bugg 1 (djupförsvar): `_kontrollera_svar_oppenhet` kontrollerar `projekt` även i
    `CallToolResult.structuredContent` (toppnivå och nästlat), medan `projekt_lista` fortsatt fungerar."""
    mcp = Mcp(demo, tmp_path / "m")
    mcp.app.projektmapp = tmp_path / "projekt"
    mcp.app.kartverktyg.projektrot = tmp_path / "projekt"
    stangt_pid = "e" * 32
    _skriv_projekt(
        mcp.app.projektmapp,
        "hemlig-baskarta",
        stangt_pid,
        oppen=False,
        fraga_namn="stangd-fraga",
        bas=demo.varld / "vatten" / "bas",
    )
    mcp.server = bygg_server(mcp.tjanst, policy=Policy(), logg=mcp.logg, miljo={})

    # 1. Toppnivå-`projekt` i structuredContent blockeras av efterkontrollen.
    monkeypatch.setattr(
        mcp.tjanst,
        "metoder_lista",
        lambda: {"projekt": stangt_pid, "syfte": "HEMLIG_STRUCTURED_LACKA"},
    )
    r1 = mcp.anropa("metoder_lista")
    assert r1.is_error
    assert "HEMLIG_STRUCTURED_LACKA" not in str(r1.content)
    assert "inte öppna data" in str(r1.content)

    # 2. Nästlat `projekt` i lista i structuredContent blockeras likaså.
    monkeypatch.setattr(
        mcp.tjanst,
        "metoder_lista",
        lambda: {"bestallningar": [{"projekt": stangt_pid, "syfte": "HEMLIG_NASTLAD_LACKA"}]},
    )
    r2 = mcp.anropa("metoder_lista")
    assert r2.is_error
    assert "HEMLIG_NASTLAD_LACKA" not in str(r2.content)

    # 3. `projekt_lista` (där nyckeln `projekt` är en lista med `id`, ej en projekt-sträng) fungerar.
    ok_lista = mcp.ok("projekt_lista")
    assert isinstance(ok_lista["projekt"], list)


def test_rapport_skapa_med_stangd_korning_stoppas_i_forkontroll_utan_diskskrivning(
    demo: Demo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bugg 3 (P1): `rapport_skapa(korningar=[stangd])` stoppas av K38-förkontrollen INNAN
    `rapporter.skapa()` anropas, så 0 rapportfiler eller kartor skrivs på disk."""
    mcp = Mcp(demo, tmp_path / "m")
    mcp.app.rapportmapp = tmp_path / "rapporter"
    mcp.server = bygg_server(mcp.tjanst, policy=Policy(), logg=mcp.logg, miljo={})

    stangd_bas = tmp_path / "data" / "lokal" / "baskarta_sthlm" / "k1"
    stangd_bas.mkdir(parents=True)
    _kopiera_korning(demo, mcp, "stangd-rap-1", stangd_bas)

    anrop: list[Any] = []
    orig_skapa = RapportTjanst.skapa

    def spara_anrop(self: RapportTjanst, *args: Any, **kwargs: Any) -> Any:
        anrop.append((args, kwargs))
        return orig_skapa(self, *args, **kwargs)

    monkeypatch.setattr(RapportTjanst, "skapa", spara_anrop)

    r = mcp.anropa("rapport_skapa", {"korningar": ["stangd-rap-1"]})
    assert r.is_error
    assert "inte är öppna" in str(r.content)
    # Förkontrollen stoppade anropet före `call_next`: `skapa()` kördes aldrig och 0 filer skrevs!
    assert anrop == []
    assert not mcp.app.rapportmapp.exists() or list(mcp.app.rapportmapp.rglob("*")) == []

    nekade = [rad for rad in mcp.logg.las() if rad["utfall"] == "nekad_oppenhet"]
    assert nekade and any("stangd-rap-1" in rad["korning_id"] for rad in nekade)

    # Kontroll: öppen körning skapar fortfarande rapport normalt.
    ok = mcp.ok("rapport_skapa", {"korningar": ["demo-vatten"]})
    assert ok["rapport_id"].startswith("r-")
    assert len(anrop) == 1
