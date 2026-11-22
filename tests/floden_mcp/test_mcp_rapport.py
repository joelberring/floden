"""Rapportverktygen över MCP (in-process): frågekatalog, rapport, påståenden, kontroll, export."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from rapport.referensflode import gardens_mottagare, pastaenden
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp import Mcp

RAPPORTVERKTYG = {
    "fragekatalog_sok", "rapport_skapa", "rapport_installningar", "pastaende_lagg_till",
    "rapport_kontrollera", "rapport_exportera", "rapport_hamta",
}  # fmt: skip


@pytest.fixture(scope="module")
def mcp(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Mcp]:
    rot = tmp_path_factory.mktemp("mcp_rapport")
    m = Mcp(demo, rot)
    m.app.rapportmapp = rot / "rapporter"
    from floden_mcp.server import bygg_server

    m.server = bygg_server(m.tjanst, logg=m.logg, miljo={"FLODEN_MCP_MODELL": "testmodell"})
    yield m


def _lagg(mcp: Mcp, rid: str, kw: Mapping[str, Any]) -> Any:
    arg: dict[str, Any] = {"rapport_id": rid, "text_med_platshallare": kw["text"]}
    arg.update({k: v for k, v in kw.items() if k != "text"})
    return mcp.anropa("pastaende_lagg_till", arg)


def test_verktyg_resurs_och_prompt_finns(mcp: Mcp) -> None:
    async def _k() -> Any:
        async with Client(mcp.server) as c:
            return (
                await c.list_tools(),
                await c.list_resource_templates(),
                await c.get_prompt("skissunderlag_skyfall", {"plats": "demokvarteret"}),
            )

    tools, mallar, prompt = anyio.run(_k)
    assert {t.name for t in tools.tools} >= RAPPORTVERKTYG
    assert "floden://rapport/{rapport_id}" in {m.uri_template for m in mallar.resource_templates}
    assert "{{matt:<adress>}}" in prompt.messages[0].content.text


def test_fragekatalog_sok(mcp: Mcp) -> None:
    s = mcp.ok("fragekatalog_sok", {"omrade": "skyfall"})
    assert s["antal"] == 15 and s["dataklass"] == "oppet"
    sk06 = next(f for f in s["fragor"] if f["id"] == "SK-06")
    assert sk06["floden"]["niva"] == "kan"
    assert mcp.ok("fragekatalog_sok", {"ids": ["LU-01"]})["fragor"][0]["omrade"] == "luft"


def test_hela_flodet_via_mcp_och_korloggen(mcp: Mcp) -> None:
    r = mcp.ok(
        "rapport_skapa",
        {"korningar": ["demo-vatten"], "mottagare": [gardens_mottagare()]},
    )
    rid = r["rapport_id"]
    assert len(r["fragor"]) == 15 and len(r["kartor"]) == 3
    assert "jamforelse.skyfall_max_djup_efter_minus_fore.yta_okning_m2" in r["belagg_adresser"]

    # en egen siffra vägras och lagras inte
    nej = _lagg(mcp, rid, {"fraga_id": "SK-06", "status": "visat", "fordjupning": "ja",
                           "text": "Vattnet blir 20 cm djupare.", "belagg": []})  # fmt: skip
    assert nej.is_error and "kommer inte ur något belägg" in nej.content[0].text

    # export före alla frågor är besvarade stoppas
    tidig = mcp.anropa("rapport_exportera", {"rapport_id": rid, "format": "pdf"})
    assert tidig.is_error and "saknar påstående" in tidig.content[0].text

    for kw in pastaenden():
        p = _lagg(mcp, rid, kw)
        assert not p.is_error, p.content[0].text
        sc = p.structured_content
        assert "{{" not in sc["fylld_text"]
    k = mcp.ok("rapport_kontrollera", {"rapport_id": rid})
    assert k["godkand"], k
    for fmt, slut in (("word", ".docx"), ("pdf", ".pdf")):
        e = mcp.ok("rapport_exportera", {"rapport_id": rid, "format": fmt})
        assert e["fil"].endswith(slut) and Path(e["fil"]).is_file() and e["storlek_byte"] > 10_000

    h = mcp.ok("rapport_hamta", {"rapport_id": rid})
    assert h["metodnotis"]["ai_stod"] is True
    assert h["metodnotis"]["modeller"] == ["testmodell"]
    assert len(h["pastaenden"]) == 16 and all(p["fylld_text"] for p in h["pastaenden"])
    assert {e["format"] for e in h["exporter"]} == {"word", "pdf"}
    lista = mcp.ok("rapport_hamta", {})["rapporter"]
    assert rid in {x["rapport_id"] for x in lista}

    async def _resurs() -> Any:
        async with Client(mcp.server) as c:
            return await c.read_resource(f"floden://rapport/{rid}")

    res = anyio.run(_resurs)
    assert json.loads(res.contents[0].text)["rapport_id"] == rid

    rader = [x for x in mcp.logg.las() if rid in x.get("rapport_id", [])]
    verktyg = [x["verktyg"] for x in rader]
    assert verktyg.count("pastaende_lagg_till") == 17  # 16 godkända och ett vägrat
    assert {"rapport_skapa", "rapport_kontrollera", "rapport_exportera", "rapport_hamta"} <= set(
        verktyg
    )
    assert any(x["utfall"] == "fel" for x in rader if x["verktyg"] == "pastaende_lagg_till")
    assert all(x["modell"] == "testmodell" for x in rader)
    assert mcp.logg.verifiera()[0]

    # körloggen är bilaga i PDF:en
    import pypdf

    pdf = next(Path(e["fil"]) for e in [mcp.ok("rapport_exportera", {"rapport_id": rid})])
    text = " ".join(p.extract_text() or "" for p in pypdf.PdfReader(str(pdf)).pages)
    assert "MCP-körloggen" in text and "pastaende_lagg_till" in text
    assert "AI-stöd (testmodell)" in " ".join(text.split())


def test_rapport_installningar_via_mcp_utan_sokvagar(mcp: Mcp, tmp_path: Path) -> None:
    from PIL import Image

    logo = tmp_path / "hemlig_mapp" / "logotyp.png"
    logo.parent.mkdir()
    Image.new("RGB", (60, 20), (20, 60, 120)).save(logo)
    r = mcp.ok(
        "rapport_skapa",
        {"korningar": ["demo-vatten"], "installningar": {"diarienummer": "Dnr 2026-00001"}},
    )
    rid = r["rapport_id"]
    assert r["installningar"]["sprakprofil"] == "sbk"
    assert r["installningar"]["numrerade_rubriker"] is False
    assert r["installningar"]["diarienummer"] == "Dnr 2026-00001"
    visad = mcp.ok("rapport_installningar", {"rapport_id": rid})["installningar"]
    assert visad["avsandare"] == "Stadsbyggnadskontoret" and visad["logotyp"] is None

    ny = mcp.ok(
        "rapport_installningar",
        {
            "rapport_id": rid,
            "andringar": {"numrerade_rubriker": True, "avsandare": "", "logotyp": str(logo)},
        },
    )["installningar"]
    assert ny["numrerade_rubriker"] is True and ny["avsandare"] == ""
    assert len(ny["logotyp"]["sha256"]) == 64
    fel = mcp.anropa("rapport_installningar", {"rapport_id": rid, "andringar": {"farg": "röd"}})
    assert fel.is_error and "Okänd inställning" in fel.content[0].text
    saknas = mcp.anropa(
        "rapport_installningar",
        {"rapport_id": rid, "andringar": {"logotyp": str(tmp_path / "hemlig_mapp" / "x.png")}},
    )
    assert saknas.is_error and "hemlig_mapp" not in saknas.content[0].text

    # sökvägen finns varken i rapporten, i svaren eller i körloggen
    h = json.dumps(mcp.ok("rapport_hamta", {"rapport_id": rid}), ensure_ascii=False)
    assert "hemlig_mapp" not in h and str(tmp_path) not in h
    logg = json.dumps(list(mcp.logg.las()), ensure_ascii=False)
    assert "hemlig_mapp" not in logg
