# ruff: noqa: E501
"""Floden som MCP-server: protokollet in-process (SDK:ns klient mot servern), utan nätverk.

Delar demovärlden (litet läge) med `tests/demovarld`. Allt som skrivs hamnar i tillfälliga mappar.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tomllib
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import anyio
import pytest
import rasterio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import Implementation

from demovarld.demo import LITEN
from demovarld.varld import nytt_hus_polygon
from floden_mcp.dataklass import DataKlass, Policy, PolicyFel, PolicyNekad, hogsta
from floden_mcp.http import KontoSkydd, ar_loopback
from floden_mcp.karna_api import FlodenFel, FlodenTjanst
from floden_mcp.korlogg import Korlogg, hash_av
from floden_mcp.server import bygg_server
from tests.demovarld.test_demo import Demo
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_inloggning import bjud_in_anna
from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]
VERKTYG = {
    "projekt_lista", "projekt_info", "geometri_sammanfattning", "byggnader_lista", "fragor_lista",
    "korningar_lista", "korning_status", "resultat_sammanfattning", "resultat_varde", "karta_bild",
    "metoder_lista", "scenario_skapa", "fraga_skapa", "korplan", "kor", "vanta", "jamfor",
    "korlogg_hamta", "session_deklarera",
}  # fmt: skip


class Mcp:
    """Servern, tjänsten och en hjälpare som anropar verktyg via protokollet."""

    def __init__(
        self, demo: Demo, rot: Path, policy: Policy | None = None, konto: Any = None
    ) -> None:
        self.rot = rot
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=demo.rot / "korningar",
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot / "data"],
            konto=konto,
        )
        self.app.fragor.demomapp = demo.varld
        self.tjanst = FlodenTjanst(self.app, rot=REPO, mcp_mapp=rot / "mcp")
        self.logg = Korlogg(rot / "mcp" / "korlogg.jsonl")
        self.server = bygg_server(self.tjanst, policy=policy or Policy(), logg=self.logg, miljo={})

    def anropa(
        self, namn: str, argument: dict[str, Any] | None = None, klient: str = "testklient"
    ) -> Any:
        async def _k() -> Any:
            async with Client(
                self.server, client_info=Implementation(name=klient, version="1.0")
            ) as c:
                return await c.call_tool(namn, argument or {})

        return anyio.run(_k)

    def ok(self, namn: str, argument: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.anropa(namn, argument)
        assert not r.is_error, r.content[0].text
        assert r.structured_content is not None
        return dict(r.structured_content)


@pytest.fixture(scope="module")
def mcp(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Mcp]:
    yield Mcp(demo, tmp_path_factory.mktemp("mcp"))


def hus(hojd: float = 18.0) -> dict[str, Any]:
    ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
    return {"id": "nytt_hus", "polygon": ring, "hojd_m": hojd}


# ------------------------------------------------------------------ protokoll och beskrivningar


def test_verktygslistan_har_alla_verktyg_med_beskrivning_och_schema(mcp: Mcp) -> None:
    async def _k() -> Any:
        async with Client(mcp.server) as c:
            return await c.list_tools(), await c.list_resources(), await c.list_prompts()

    tools, resurser, promptar = anyio.run(_k)
    namn = {t.name for t in tools.tools}
    assert namn >= VERKTYG
    for t in tools.tools:
        assert len(t.description or "") > 40, t.name
        assert t.input_schema["type"] == "object"
        assert {"modell", "roll"} <= set(
            t.input_schema["properties"]
        ) or t.name == "session_deklarera"
    uris = {str(r.uri) for r in resurser.resources}
    assert {"floden://metod/vatten", "floden://metod/dagsljus", "floden://kontrakt/k9"} <= uris
    assert {p.name for p in promptar.prompts} == {
        "utforska_demokvarteret",
        "prova_nytt_hus",
        "skissunderlag_skyfall",
        "skissunderlag_dagsljus",
        "skissunderlag_ekologi",
    }


def test_resurser_och_promptar_gar_att_lasa(mcp: Mcp) -> None:
    async def _k() -> Any:
        async with Client(mcp.server) as c:
            r = await c.read_resource("floden://kontrakt/k9")
            p = await c.get_prompt("prova_nytt_hus", {"hojd_m": "21"})
            return r, p

    r, p = anyio.run(_k)
    assert "Planscenario" in r.contents[0].text
    assert "21 m" in p.messages[0].content.text


# ------------------------------------------------------------------ läsa


def test_projekt_geometri_och_byggnader_hanger_ihop(mcp: Mcp) -> None:
    info = mcp.ok("projekt_info")
    assert info["dataklass"] == "oppet" and info["epsg"] == 3011 and info["paahittat"] is True
    assert {a["id"] for a in info["tillgangliga_analyser"]} >= {"dagsljus.sol", "vatten.skyfall"}
    geo = mcp.ok("geometri_sammanfattning")
    lista = mcp.ok("byggnader_lista", {"max_antal": 500})
    assert geo["byggnader"]["antal"] == lista["antal_traffar"] == info["antal_byggnader"] > 0
    u = info["utbredning"]
    mitten = [u[0], u[1], (u[0] + u[2]) / 2, (u[1] + u[3]) / 2]
    delvis = mcp.ok("byggnader_lista", {"omrade": mitten, "max_antal": 500})
    assert 0 < delvis["antal_traffar"] < lista["antal_traffar"]
    b = lista["byggnader"][0]
    assert b["hojd_m"] > 0 and b["yta_m2"] > 0 and len(b["centrum"]) == 2
    fel = mcp.anropa("byggnader_lista", {"omrade": [1, 1, 0, 0]})
    assert fel.is_error and "e_max" in fel.content[0].text


def test_metoder_ar_skisser_med_version(mcp: Mcp) -> None:
    m = mcp.ok("metoder_lista")["metoder"]
    by = {x["namn"]: x for x in m}
    assert by["dagsljus.soltimmar_mark"]["version"] and by["vatten.statisk_fyllning"]["version"]
    assert all(x["validering"]["status"] in ("skiss", "rapport") for x in m)
    assert all(x["validering"]["status"] == "skiss" for x in m)  # ingen människa har klassat någon


def test_fragor_och_korningar_listas(mcp: Mcp) -> None:
    f = {x["ref"]: x for x in mcp.ok("fragor_lista")["fragor"]}
    assert f["demo_vatten"]["giltig"] and f["demo_vatten"]["i_demon"]
    k = {x["korning_id"]: x for x in mcp.ok("korningar_lista")["korningar"]}
    assert k["demo-vatten"]["status"] == "klar"
    assert mcp.ok("korning_status", {"korning_id": "demo-vatten"})["status"] == "klar"
    assert mcp.anropa("korning_status", {"korning_id": "finns-inte"}).is_error


# ------------------------------------------------------------------ resultat med belägg


def test_jamforelse_har_belagg_och_talen_ar_flodens(mcp: Mcp) -> None:
    s = mcp.ok("resultat_sammanfattning", {"korning_id": "demo-vatten"})
    sam = json.loads(
        (mcp.app.fragor.korningsmapp / "demo-vatten/resultat/sammanstallning.json").read_text()
    )
    j = s["jamforelser"][0]
    # Markens tal (tak undantagna) kommer ur webbens funktion; kärnans omaskade finns bredvid.
    res = mcp.app.fragor.resultat("demo-vatten")
    la = next(x for x in res["lager"] if x["typ"] == "skillnad" and x["primar"])
    ms = mcp.app.fragor.marksiffror("demo-vatten", la, j["troskel"])
    assert ms is not None and j["max_okning"]["varde"] == ms["max_okning"]
    assert j["karnans_omaskade"]["max_okning"]["varde"] == sam["jamforelser"][0]["max_okning"]
    b = j["max_okning"]["belagg"]
    assert b["korning_id"] == "demo-vatten" and b["fil"].endswith("diff.tif")
    assert b["matt"] == "max_djup:max_okning:mark"
    assert b["enhet"] == "m" and j["yta_okning_m2"]["enhet"] == "m²"
    rad = s["matvarden"][0]["rader"][0]
    assert rad["belagg"]["fil"].endswith("run.json")
    run = json.loads(
        (mcp.app.fragor.korningsmapp / "demo-vatten/resultat" / rad["belagg"]["fil"]).read_text()
    )
    assert rad["varden"]["max_djup_m"] == run["matvarden"]["max_djup_m"]
    assert s["matvarden"][0]["enheter"]["max_djup_m"] == "m"
    assert s["matvarden"][0]["metod"] == "skiss"


def test_punktvarde_motsvarar_cellen_i_filen_det_hanvisar_till(mcp: Mcp) -> None:
    sam = mcp.ok("resultat_sammanfattning", {"korning_id": "demo-vatten"})
    info = mcp.ok("projekt_info")
    u = info["baser"]["vatten"]["rutor"][0]
    e, n = u[0] + 60.5, u[1] + 60.5
    r = mcp.ok("resultat_varde", {"korning_id": "demo-vatten", "analys": "skyfall", "e": e, "n": n})
    assert {v["typ"] for v in r["varden"]} == {"bas", "scenario", "skillnad"}
    assert sam["korning"]["status"] == "klar"
    for v in r["varden"]:
        if v["varde"] is None:
            continue
        b = v["varde"]["belagg"]
        fil = mcp.app.fragor.korningsmapp / "demo-vatten" / "resultat" / b["fil"]
        assert fil.is_file()
        with rasterio.open(fil) as ds:
            ex = list(ds.sample([(b["cell"]["e_mitt"], b["cell"]["n_mitt"])]))[0][0]
        assert v["varde"]["varde"] == pytest.approx(float(ex), abs=1e-6)
        assert len(b["fil_sha256"]) == 64
    skill = next(v for v in r["varden"] if v["typ"] == "skillnad")
    bas = next(v for v in r["varden"] if v["typ"] == "bas")
    sc = next(v for v in r["varden"] if v["typ"] == "scenario")
    if skill["varde"] and bas["varde"] and sc["varde"]:
        assert skill["varde"]["varde"] == pytest.approx(
            sc["varde"]["varde"] - bas["varde"]["varde"], abs=1e-5
        )
    utanfor = mcp.ok(
        "resultat_varde", {"korning_id": "demo-vatten", "analys": "skyfall", "e": 1.0, "n": 1.0}
    )
    assert all(v["varde"] is None and "utanför" in v["orsak"] for v in utanfor["varden"])


def test_polygonstatistik_exkluderar_tak_och_ger_belagg(mcp: Mcp) -> None:
    u = mcp.ok("projekt_info")["baser"]["vatten"]["rutor"][0]
    ring = [[u[0], u[1]], [u[2], u[1]], [u[2], u[3]], [u[0], u[3]]]
    r = mcp.ok("resultat_varde", {"korning_id": "demo-vatten", "analys": "skyfall", "omrade": ring})
    st = {x["typ"]: x for x in r["statistik"]}
    assert st["bas"]["antal_celler_tak_uteslutna"] > 0
    assert st["bas"]["max"]["belagg"]["matt"] == "max_djup:max"
    assert st["skillnad"]["max"]["varde"] >= st["skillnad"]["min"]["varde"]
    jf = mcp.ok("jamfor", {"korning_id": "demo-vatten", "omrade": ring})
    assert jf["jamforelser"][0]["betydelse"] == "skillnad = efter minus fore"
    assert jf["inom_omrade"][0]["typ"] == "skillnad"
    assert mcp.anropa("resultat_varde", {"korning_id": "demo-vatten", "analys": "skyfall"}).is_error
    assert mcp.anropa(
        "resultat_varde", {"korning_id": "demo-vatten", "analys": "finns-ej", "e": 1.0, "n": 1.0}
    ).is_error


def test_karta_bild_ger_png_med_belagg(mcp: Mcp) -> None:
    r = mcp.anropa("karta_bild", {"korning_id": "demo-vatten", "analys": "skyfall"})
    assert not r.is_error
    bild = next(c for c in r.content if c.type == "image")
    png = base64.b64decode(bild.data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and bild.mime_type == "image/png"
    meta = r.structured_content
    assert (
        meta["dataklass"] == "oppet" and meta["metodstatus"] == "skiss" and meta["norr"] == "uppat"
    )
    assert meta["belagg"]["korning_id"] == "demo-vatten" and meta["enhet"] == "m"
    assert Path(meta["sparad_som"]).read_bytes() == png
    # Samma lager och utsnitt ger samma bild (fast kartografi).
    r2 = mcp.anropa("karta_bild", {"korning_id": "demo-vatten", "analys": "skyfall"})
    assert r2.structured_content["png_sha256"] == meta["png_sha256"]
    zoom = mcp.anropa(
        "karta_bild",
        {"korning_id": "demo-vatten", "analys": "skyfall", "typ": "bas", "scenario": "fore"},
    )
    assert not zoom.is_error and zoom.structured_content["typ"] == "bas"
    assert mcp.anropa(
        "karta_bild", {"korning_id": "demo-vatten", "analys": "skyfall", "typ": "nej"}
    ).is_error


# ------------------------------------------------------------------ planera, kör, jämför


def test_ritat_hus_fran_scenario_till_jamforelse(mcp: Mcp) -> None:
    # Ogiltiga hus nekas av kärnans kontroll och sparas inte.
    u = mcp.ok("projekt_info")["utbredning"]
    for dalig, delstr in (
        ({"rektangel": [u[0] - 500, u[1] - 500, u[0] - 450, u[1] - 450], "hojd_m": 12}, "utanför"),
        ({**hus(), "hojd_m": 500}, "för stor"),
        ({"polygon": [[u[0] + 10, u[1] + 10], [u[0] + 20, u[1] + 20], [u[0] + 10, u[1] + 20], [u[0] + 20, u[1] + 10]], "hojd_m": 10}, "korsar"),
    ):  # fmt: skip
        r = mcp.ok("scenario_skapa", {"namn": "dalig", "hus": [dalig]})
        assert r["giltig"] is False and r["sparat"] is False, delstr
        assert delstr in " ".join(r["fel"])
    assert not mcp.app.kartverktyg.lista_scenarier("demokvarteret")["scenarier"]
    assert mcp.anropa("scenario_skapa", {"namn": "tom"}).is_error

    # Ett giltigt hus: kontrollera, spara, planera.
    kolla = mcp.ok("scenario_skapa", {"namn": "mitt hus", "hus": [hus(24)], "bara_kontroll": True})
    assert kolla["giltig"] and kolla["sparat"] is False and kolla["hus"][0]["ar_uppskattning"]
    # Samma hus som demons (18 m) har samma K9-nyckel och träffar cachen: inget körs om.
    demohus = mcp.ok("scenario_skapa", {"namn": "demohus", "hus": [hus(18)]})
    assert demohus["sparat"]
    cachat = mcp.ok("korplan", {"scenario": "demohus"})
    assert all(f["plan"]["sammanfattning"]["att_kora"] == 0 for f in cachat["fragor"])
    sc = mcp.ok("scenario_skapa", {"namn": "mitt hus", "hus": [hus(24)]})
    assert sc["sparat"] and sc["scenario"] == "mitt-hus" and sc["antal_andringar"] == 1
    plan = mcp.ok("korplan", {"scenario": "mitt-hus"})
    assert {f["mall"] for f in plan["fragor"]} == {"dagsljus", "vatten", "ekologi"}
    assert all(f["plan"]["sammanfattning"]["att_kora"] > 0 for f in plan["fragor"]), [
        (f["mall"], f["plan"]["sammanfattning"]) for f in plan["fragor"]
    ]
    assert all(len(f["k9_nyckel"]) == 64 for f in plan["fragor"])
    # Samma K9 som webbens ritverktyg ger (en sanning): nyckeln är kärnans.
    web = mcp.app.kartverktyg.planera("demokvarteret", "mitt-hus", plan["valda_analyser"], None)
    assert {f["mall"]: f["k9_nyckel"] for f in web["fragor"]} == {
        f["mall"]: f["k9_nyckel"] for f in plan["fragor"]
    }

    # Kör asynkront, vänta, läs.
    kor = mcp.ok("kor", {"scenario": "mitt-hus"})
    ids = [k["korning_id"] for k in kor["korningar"]]
    assert len(ids) == 3
    v = mcp.ok("vanta", {"korning_ids": ids, "tidsgrans_s": 600})
    assert v["alla_klara"], v
    jam: dict[str, dict[str, Any]] = {}
    for kid in ids:
        j = mcp.ok("jamfor", {"korning_id": kid})["jamforelser"][0]
        jam[j["analys"]] = j
        assert j["max_okning"]["belagg"]["korning_id"] == kid
        sam = mcp.ok("resultat_sammanfattning", {"korning_id": kid})
        assert sam["jamforelser"] and sam["varningar"] is not None and sam["antaganden"]
    assert set(jam) == {"sol", "skyfall", "konnektivitet"}
    assert (
        jam["sol"]["max_minskning"]["enhet"] == "h" and jam["skyfall"]["max_okning"]["enhet"] == "m"
    )
    assert jam["skyfall"]["max_okning"]["varde"] > 0  # huset spärrar avrinningen: vatten ökar
    # Ett nytt hus kan aldrig ge mer sol på mark, och byggnadens eget tak är inte mark.
    sol_id = next(k for k in ids if "dagsljus" in k)
    sol = jam["sol"]
    assert sol["tak_maskat"] and sol["yta_mark_enbart"]
    assert sol["max_okning"]["varde"] == 0.0 and sol["yta_okning_m2"]["varde"] == 0.0
    assert sol["max_okning"]["belagg"]["matt"].endswith(":mark")
    assert sol["karnans_omaskade"]["yta_okning_m2"]["varde"] > 0  # kärnans tal räknar taket
    assert sol["yta_minskning_m2"]["varde"] < sol["karnans_omaskade"]["yta_minskning_m2"]["varde"]
    # Samma funktion och mask som webben: jämförelsens tal = FragaTjanst.marksiffror = polygonen
    # över hela utbredningen (statistik) = projektsidans räkning.
    res = mcp.app.fragor.resultat(sol_id)
    la = next(x for x in res["lager"] if x["typ"] == "skillnad" and x["primar"])
    ms = mcp.app.fragor.marksiffror(sol_id, la, sol["troskel"])
    assert ms is not None and ms["yta_minskning_m2"] == sol["yta_minskning_m2"]["varde"]
    u = la["utbredning"]
    hela = [[u["x_min"], u["y_min"]], [u["x_max"], u["y_min"]], [u["x_max"], u["y_max"]], [u["x_min"], u["y_max"]]]  # fmt: skip
    st = mcp.ok(
        "resultat_varde",
        {"korning_id": sol_id, "analys": "sol", "roll": "soltimmar", "omrade": hela},
    )
    skill = next(x for x in st["statistik"] if x["typ"] == "skillnad")
    assert skill["yta_minskning_m2"]["varde"] == ms["yta_minskning_m2"]
    assert skill["yta_okning_m2"]["varde"] == 0.0 and skill["max"]["varde"] <= 0.0
    assert skill["antal_celler_tak_uteslutna"] > 0
    pr = mcp.app.projekt._marksiffror(sol_id, la, sol["troskel"])  # noqa: SLF001
    assert pr == ms
    # Cellen mitt i det nya huset är tak: inget markvärde, värdet märkt som tak.
    ring = nytt_hus_polygon(LITEN.varld())
    e = sum(p[0] for p in ring) / len(ring)
    n = sum(p[1] for p in ring) / len(ring)
    pv = mcp.ok(
        "resultat_varde",
        {"korning_id": sol_id, "analys": "sol", "roll": "soltimmar", "e": e, "n": n},
    )
    per_typ = {post["typ"]: post for post in pv["varden"]}
    assert per_typ["bas"]["ar_tak"] is False and per_typ["bas"]["varde"] is not None  # öppen mark
    for typ in ("scenario", "skillnad"):  # huset finns i efter-scenariot och därmed i skillnaden
        post = per_typ[typ]
        assert post["ar_tak"] is True, post
        assert post["varde"] is None and post["tak_varde"]["varde"] is not None
        assert "tak" in post["orsak"]
    # Vatten: bara byggnad i markslaget maskas; ökningen finns på marken utanför huset.
    vat = jam["skyfall"]
    assert vat["tak_maskat"] and vat["yta_okning_m2"]["varde"] > 0
    # Körloggen vet vem som körde vad.
    rader = mcp.logg.las(verktyg="kor")
    assert rader and set(ids) <= set(rader[-1]["korning_id"]) or rader[-1]["utfall"] == "ok"
    for kid in ids:
        assert any(x["verktyg"] == "jamfor" for x in mcp.logg.las(korning_id=kid))
    # Andra körningen är cachad.
    igen = mcp.ok("korplan", {"scenario": "mitt-hus"})
    assert all(f["plan"]["sammanfattning"]["att_kora"] == 0 for f in igen["fragor"])


def test_fraga_skapa_validerar_med_karnan_och_begransar_sokvagar(mcp: Mcp, demo: Demo) -> None:
    mall = tomllib.loads((demo.fragor / "demo_vatten.toml").read_text(encoding="utf-8"))
    mall["fraga_id"] = "mcp-egen-fraga"
    for sc in mall["scenarier"].values():
        sc["bas"] = str((demo.fragor / sc["bas"]).resolve())
        if "k9" in sc:
            sc["k9"] = [str((demo.fragor / p).resolve()) for p in sc["k9"]]
    r = mcp.ok("fraga_skapa", {"fraga": mall})
    assert r["ref"] == "mcp-egen-fraga" and r["giltig"] and r["analyser"] == ["skyfall"]
    plan = mcp.ok("korplan", {"fraga": r["ref"]})
    assert plan["plan"]["sammanfattning"]["jobb"] > 0

    okand = {**mall, "okant_falt": 1}
    e = mcp.anropa("fraga_skapa", {"fraga": okand})
    assert e.is_error and "okant_falt" in e.content[0].text
    utanfor = json.loads(json.dumps(mall))
    utanfor["fraga_id"] = "mcp-utanfor"
    utanfor["scenarier"]["fore"]["bas"] = str(REPO / "testsvit")
    e = mcp.anropa("fraga_skapa", {"fraga": utanfor})
    assert e.is_error and "nekad_sokvag" in e.content[0].text
    dublett = {**mall, "fraga_id": "demo_vatten"}
    e = mcp.anropa("fraga_skapa", {"fraga": dublett})
    assert e.is_error and "redan" in e.content[0].text


def test_kor_kraver_ett_av_scenario_eller_fraga(mcp: Mcp) -> None:
    assert mcp.anropa("kor", {}).is_error
    assert mcp.anropa("kor", {"scenario": "a", "fraga": "b"}).is_error
    assert mcp.anropa("korplan", {"scenario": "finns-inte"}).is_error
    assert mcp.anropa("vanta", {"korning_ids": []}).is_error


# ------------------------------------------------------------------ dataklass och policy


def test_policy_standard_ar_bara_oppet_och_okanda_projekt_ar_interna() -> None:
    p = Policy()
    assert p.tillatna_for("vad-som-helst") == frozenset({DataKlass.OPPET})
    assert p.projektklass("demokvarteret") is DataKlass.OPPET
    assert p.projektklass("ett-annat") is DataKlass.INTERNT
    assert hogsta(DataKlass.OPPET, DataKlass.SKYDDAT, DataKlass.INTERNT) is DataKlass.SKYDDAT
    p.kontrollera("x", DataKlass.OPPET)
    with pytest.raises(PolicyNekad, match="internt"):
        p.kontrollera("x", DataKlass.INTERNT)
    konf = {
        "mcp": {
            "tillatna_klasser": ["oppet"],
            "projektklasser": {"p1": "skyddat"},
            "klienter": {"Claude-Desktop": {"tillatna_klasser": ["oppet", "internt"]}},
        }
    }
    p2 = Policy.fran_konfig(konf, {})
    assert p2.projektklass("p1") is DataKlass.SKYDDAT
    p2.kontrollera("claude-desktop", DataKlass.INTERNT)
    with pytest.raises(PolicyNekad):
        p2.kontrollera("annan", DataKlass.INTERNT)
    assert Policy.fran_konfig({}, {"FLODEN_MCP_TILLATNA_KLASSER": "oppet,internt"}).standard == {
        DataKlass.OPPET, DataKlass.INTERNT,
    }  # fmt: skip
    with pytest.raises(PolicyFel):
        Policy.fran_konfig({"mcp": {"tillatna_klasser": ["hemligt"]}}, {})


def test_policyn_vagrar_en_klass_som_inte_ar_tillaten_och_loggar_det(
    demo: Demo, tmp_path: Path
) -> None:
    policy = Policy.fran_konfig({"mcp": {"projektklasser": {"demokvarteret": "skyddat"}}}, {})
    m = Mcp(demo, tmp_path, policy)
    r = m.anropa("projekt_info")
    assert (
        r.is_error
        and "Nekad av datapolicyn" in r.content[0].text
        and "skyddat" in r.content[0].text
    )
    rad = m.logg.las()[-1]
    assert rad["utfall"] == "nekad_policy" and rad["dataklass"] == "skyddat"
    assert rad["verktyg"] == "projekt_info" and rad["svar_sha256"] is None
    # Inget har körts: inga scenarier eller körningar skapades.
    assert m.anropa("scenario_skapa", {"namn": "x", "hus": [hus()]}).is_error
    assert not m.app.kartverktyg.lista_scenarier("demokvarteret")["scenarier"]
    # Verktyg utan projekt (metoder) berörs inte av projektets klass.
    assert not m.anropa("metoder_lista").is_error


def test_policyn_kan_oppna_en_klass_for_en_namngiven_klient(demo: Demo, tmp_path: Path) -> None:
    policy = Policy.fran_konfig(
        {
            "mcp": {
                "projektklasser": {"demokvarteret": "internt"},
                "klienter": {"intern-klient": {"tillatna_klasser": ["oppet", "internt"]}},
            }
        },
        {},
    )
    m = Mcp(demo, tmp_path, policy)
    assert m.anropa("projekt_info", klient="annan").is_error
    r = m.anropa("projekt_info", klient="intern-klient")
    assert not r.is_error and r.structured_content["dataklass"] == "internt"


# ------------------------------------------------------------------ körlogg


def test_korloggen_har_klient_modell_roll_hash_och_inga_varden(demo: Demo, tmp_path: Path) -> None:
    m = Mcp(demo, tmp_path)
    arg = {"korning_id": "demo-vatten", "analys": "skyfall", "e": 151260.123, "n": 6580260.456}
    m.ok("resultat_varde", {**arg, "modell": "testmodell-1", "roll": "utforskare"})
    m.ok("session_deklarera", {"modell": "deklarerad-modell", "roll": "skribent"})
    m.ok("korning_status", {"korning_id": "demo-vatten"})
    m.anropa("korning_status", {"korning_id": "finns-inte"})
    rader = m.logg.las()
    assert [r["verktyg"] for r in rader] == [
        "resultat_varde", "session_deklarera", "korning_status", "korning_status",
    ]  # fmt: skip
    a, _, c, d = rader
    assert a["klient"] == {"namn": "testklient", "version": "1.0"}
    assert (a["modell"], a["roll"]) == ("testmodell-1", "utforskare")
    assert (c["modell"], c["roll"]) == ("deklarerad-modell", "skribent")  # sessionsmetadata
    assert a["indata_hash"] == hash_av(arg) and a["indata_nycklar"] == sorted(arg)
    assert a["korning_id"] == ["demo-vatten"] and a["utfall"] == "ok" and a["dataklass"] == "oppet"
    assert d["utfall"] == "fel" and d["korning_id"] == ["finns-inte"]
    assert len(a["svar_sha256"]) == 64 and a["tid_ms"] >= 0 and a["session"].startswith("s-")
    # Inga argumentvärden (och inga hemligheter) i filen.
    text = m.logg.sokvag.read_text(encoding="utf-8")
    assert "151260" not in text and "6580260" not in text
    # Hashkedjan stämmer och en ändring syns.
    assert m.logg.verifiera() == (True, 4, None)
    rader_text = text.splitlines()
    ändrad = json.loads(rader_text[1])
    ändrad["modell"] = "annan"
    rader_text[1] = json.dumps(ändrad, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    m.logg.sokvag.write_text("\n".join(rader_text) + "\n", encoding="utf-8")
    assert m.logg.verifiera()[:1] == (False,) and m.logg.verifiera()[2] == 2


def test_korlogg_hamta_filtrerar_och_visar_kedjan(demo: Demo, tmp_path: Path) -> None:
    m = Mcp(demo, tmp_path)
    m.ok("korning_status", {"korning_id": "demo-vatten"})
    m.ok("projekt_lista")
    r = m.ok("korlogg_hamta", {"korning_id": "demo-vatten"})
    assert [x["verktyg"] for x in r["rader"]] == ["korning_status"]
    assert r["hashkedja"]["ok"] is True
    alla = m.ok("korlogg_hamta", {})
    assert {x["verktyg"] for x in alla["rader"]} >= {"korning_status", "projekt_lista"}


def test_en_logg_som_inte_gar_att_skriva_lamnar_inte_ut_resultat(
    demo: Demo, tmp_path: Path
) -> None:
    m = Mcp(demo, tmp_path)
    m.logg.sokvag.parent.mkdir(parents=True, exist_ok=True)
    m.logg.sokvag.write_text("inte json\n", encoding="utf-8")
    r = m.anropa("projekt_lista")
    assert r.is_error and "Körloggen" in r.content[0].text


# ------------------------------------------------------------------ HTTP bakom inloggningen


def _asgi(
    app: Any, metod: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    svar: dict[str, Any] = {"status": None, "scope": None}

    async def inre(scope: Any, receive: Any, send: Any) -> None:
        svar["scope"] = dict(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    skydd = KontoSkydd(inre, app)

    async def kor() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m: MutableMapping[str, Any]) -> None:
            if m["type"] == "http.response.start":
                svar["status"] = m["status"]

        h = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        await skydd(
            {"type": "http", "method": metod, "path": "/mcp", "query_string": b"", "headers": h},
            receive,
            send,
        )

    anyio.run(kor)
    return svar["status"], svar


def test_http_ar_bara_loopback_utan_inloggning_och_kraver_session_med() -> None:
    from konto.adapter import KontoAdapter
    from konto.konfig import KontoKonfig

    assert ar_loopback("127.0.0.1") and ar_loopback("::1") and ar_loopback("localhost")
    assert not ar_loopback("0.0.0.0") and not ar_loopback("192.168.1.5")
    status, _ = _asgi(KontoAdapter(KontoKonfig()), "POST")
    assert status == 200  # inloggning av: släpps igenom


def test_http_med_inloggning_ger_401_utan_session_och_konto_id_med_session(miljo: Miljo) -> None:
    bjud_in_anna(miljo)
    status, _ = _asgi(miljo.adapter, "GET")
    assert status == 401
    webb = miljo.webblasare()
    assert webb.logga_in(miljo.idp, ANNA)[0] == 302
    kaka = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
    status, svar = _asgi(miljo.adapter, "GET", {"Cookie": kaka})
    assert status == 200
    konto_id = svar["scope"]["floden_konto"]
    assert (
        konto_id and "@" not in konto_id and "Anna" not in konto_id
    )  # opakt id, inga personuppgifter
    # POST utan CSRF-huvud nekas (samma regler som webben).
    status, _ = _asgi(miljo.adapter, "POST", {"Cookie": kaka, "Origin": "https://floden.test"})
    assert status == 403


# ------------------------------------------------------------------ stdio på riktigt (delprocess)


def test_floden_mcp_over_stdio_i_delprocess(demo: Demo, tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        "[webb]\n"
        f'fragemappar = ["{demo.fragor}"]\n'
        f'cachemapp = "{demo.rot / "cache"}"\n'
        f'korningsmapp = "{demo.rot / "korningar"}"\n'
        f'demomapp = "{demo.varld}"\n'
        f'kartverktygsmapp = "{tmp_path / "kv"}"\n',
        encoding="utf-8",
    )

    async def _k() -> Any:
        par = StdioServerParameters(
            command=sys.executable,
            args=["-m", "floden.cli", "mcp", "--rot", str(tmp_path)],
            env={
                **os.environ,
                "PYTHONPATH": str(REPO / "src"),
                "FLODEN_MCP_MODELL": "miljo-modell",
            },
        )
        async with Client(par, client_info=Implementation(name="stdio-klient", version="9")) as c:
            tools = await c.list_tools()
            r = await c.call_tool("projekt_info", {})
            return tools, r

    tools, r = anyio.run(_k)
    assert {t.name for t in tools.tools} >= VERKTYG
    assert not r.is_error and r.structured_content["dataklass"] == "oppet"
    rader = Korlogg(tmp_path / "data" / "lokal" / "mcp" / "korlogg.jsonl").las()
    assert rader[-1]["klient"]["namn"] == "stdio-klient" and rader[-1]["modell"] == "miljo-modell"


def test_felklass_ar_ett_riktigt_undantag() -> None:
    assert issubclass(FlodenFel, Exception) and FlodenFel("x", "k").kod == "k"
