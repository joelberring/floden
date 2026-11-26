# ruff: noqa: E501
"""Regressionstester för Våg 1 MCP-säkerhet (W-B1 och W-B2):

- W-B1: Korsprojekts-IDOR och K38-öppenhet på `fragor_lista`, `korplan` och `kor` (både med
  inloggning på — inklusive fristående frågor för superadmin — och med inloggning av).
- W-B2:
  1. K38-spärr på `resources/read` (`floden://rapport/{id}`) och `rapport_hamta` (stängd rapport
     via stängd körning eller stängt projekt blockeras och läcker aldrig JSON; öppen rapport går).
  2. `rapport_kontrollera` ligger i `SKRIVVERKTYG`: nekas för `lasare` utan att mutera `rapport.json`,
     tillåts för `utredare`.
  3. `karta_bild` härleder körningens verkliga projekt-id i `policy.klass_for` i stället för att
     hårdkoda `demokvarteret`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from starlette.testclient import TestClient

from floden_mcp.dataklass import DataKlass, Policy
from floden_mcp.server import SKRIVVERKTYG, bygg_server
from rapport.skissrapport import RapportTjanst
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp import Mcp
from tests.floden_mcp.test_mcp_http import (
    BO,
    Stack,
    _data,
    _initiera,
    _rpc,
    _skapa_token,
    anropa_verktyg,
)
from tests.floden_mcp.test_oppenhet import _fraga_dict, _kopiera_korning
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_nycklar import _inloggad
from tests.konto.test_projekt_per_anvandare_hjalp import setup_projekt

SUPER = {
    "sub": "sub-super-v1",
    "email": "super@example.test",
    "email_verified": True,
    "name": "Sam Super",
}


def _token(miljo: Miljo, claims: dict[str, Any]) -> str:
    webb, _ = _inloggad(miljo, claims)  # type: ignore[arg-type]
    return str(_skapa_token(webb)["token"])


def _skriv_projekt(
    projektrot: Path,
    slug: str,
    register_id: str,
    *,
    oppen: bool,
    fraga_namn: str,
    bas: Path,
    k9: list[Path] | None = None,
) -> Path:
    """Skapar en minimal projektmapp med `projekt.json`, `oppenhet.toml` och en K20-fråga."""
    pmapp = projektrot / slug
    (pmapp / "fragor").mkdir(parents=True, exist_ok=True)
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": "floden-projekt/v1",
                "projekt_id": slug,
                "register_id": register_id,
                "namn": slug,
            }
        ),
        encoding="utf-8",
    )
    if oppen:
        (pmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\nkalla = "test"\n',
            encoding="utf-8",
        )
    else:
        (pmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = false\nlicens = "intern"\nkalla = "Stockholms baskarta"\n',
            encoding="utf-8",
        )
    fraga = _fraga_dict(bas, fraga_id=fraga_namn)
    if k9:
        fraga["scenarier"]["efter"] = {"bas": str(bas), "k9": [str(p) for p in k9]}
    (pmapp / "fragor" / f"{fraga_namn}.json").write_text(json.dumps(fraga), encoding="utf-8")
    return pmapp


# ------------------------------------------------------------------ W-B2: K38 på floden://rapport/{id}


def test_rapportresurs_blockerar_stangd_korning_och_stangt_projekt_men_tillater_oppen(
    demo: Demo, tmp_path: Path
) -> None:
    """W-B2: `resources/read` på `floden://rapport/{id}` blockeras av K38 både när rapporten bygger
    på en stängd körning och när rapportens projekt har `oppen = false`; öppen rapport fungerar."""
    mcp = Mcp(demo, tmp_path / "m")
    mcp.app.rapportmapp = tmp_path / "rapporter"
    mcp.app.projektmapp = tmp_path / "projekt"
    mcp.app.kartverktyg.projektrot = tmp_path / "projekt"
    mcp.server = bygg_server(mcp.tjanst, policy=Policy(), logg=mcp.logg, miljo={})

    # 1. Öppen rapport (demokvarteret + demo-vatten).
    oppen_r = mcp.ok("rapport_skapa", {"korningar": ["demo-vatten"]})
    oppen_id = oppen_r["rapport_id"]

    # 2. Stängd rapport via stängd körning (skriven direkt i arkivet, som webben kan göra).
    stangd_bas = tmp_path / "data" / "lokal" / "baskarta_sthlm" / "k1"
    stangd_bas.mkdir(parents=True)
    _kopiera_korning(demo, mcp, "stangd-r-1", stangd_bas)
    arkiv = RapportTjanst(mcp.tjanst, rapportmapp=mcp.app.rapportmapp).arkiv
    oppen_obj = arkiv.las(oppen_id)

    stangd_korning_id = "r-aaaa00001111"
    stangd_korning_rapport = oppen_obj.med(
        rapport_id=stangd_korning_id,
        titel="HEMLIG_KORNING_RAPPORT_TEXT",
        korningar=tuple({**k, "korning_id": "stangd-r-1"} for k in oppen_obj.korningar),
    )
    arkiv.spara(stangd_korning_rapport)

    # 3. Stängd rapport via stängt projekt (oppen = false i projektets oppenhet.toml).
    stangt_pid = "1" * 32
    _skriv_projekt(
        mcp.app.projektmapp,
        "stockholm-stangt",
        stangt_pid,
        oppen=False,
        fraga_namn="sthlm-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )
    stangt_proj_id = "r-bbbb00002222"
    stangt_proj_rapport = oppen_obj.med(
        rapport_id=stangt_proj_id,
        projekt=stangt_pid,
        titel="HEMLIG_BASKARTA_PROJEKT_TEXT",
    )
    arkiv.spara(stangt_proj_rapport)

    async def las_resurs(uri: str) -> tuple[Any | None, MCPError | None]:
        async with Client(mcp.server) as c:
            try:
                return await c.read_resource(uri), None
            except MCPError as exc:
                return None, exc

    # Öppen rapport går att läsa via resursen.
    res_ok, fel_ok = anyio.run(lambda: las_resurs(f"floden://rapport/{oppen_id}"))
    assert fel_ok is None and res_ok is not None
    assert json.loads(res_ok.contents[0].text)["rapport_id"] == oppen_id

    # Stängd körning i rapport: resursen blockeras och läcker aldrig JSON.
    res_k, exc_k = anyio.run(lambda: las_resurs(f"floden://rapport/{stangd_korning_id}"))
    assert res_k is None and exc_k is not None
    assert "inte är öppna" in str(exc_k)
    assert "HEMLIG_KORNING_RAPPORT_TEXT" not in str(exc_k)

    # Stängt projekt i rapport: resursen blockeras och läcker aldrig JSON.
    res_p, exc_p = anyio.run(lambda: las_resurs(f"floden://rapport/{stangt_proj_id}"))
    assert res_p is None and exc_p is not None
    assert "inte är öppna" in str(exc_p) or "inte öppna data" in str(exc_p)
    assert "HEMLIG_BASKARTA_PROJEKT_TEXT" not in str(exc_p)

    # Även `rapport_hamta` blockerar enskild stängd rapport och döljer stängda rapporter i listan.
    for rid, hemlig in (
        (stangd_korning_id, "HEMLIG_KORNING_RAPPORT_TEXT"),
        (stangt_proj_id, "HEMLIG_BASKARTA_PROJEKT_TEXT"),
    ):
        r = mcp.anropa("rapport_hamta", {"rapport_id": rid})
        assert r.is_error and hemlig not in str(r.content)
    lista = mcp.ok("rapport_hamta", {})
    assert [x["rapport_id"] for x in lista["rapporter"]] == [oppen_id]
    assert lista["dolda_icke_oppna"] == 2

    # Körloggen har registrerat nekad_oppenhet för båda resursförsöken.
    nekade = [rad for rad in mcp.logg.las() if rad["utfall"] == "nekad_oppenhet"]
    assert any(rad["verktyg"] == f"resurs:floden://rapport/{stangd_korning_id}" for rad in nekade)
    assert any(rad["verktyg"] == f"resurs:floden://rapport/{stangt_proj_id}" for rad in nekade)


def test_efterkontroll_blockerar_stangd_korning_i_resurstext(demo: Demo, tmp_path: Path) -> None:
    """W-B2 efterkontroll: även en resurs utan rapport-id som returnerar `contents[].text` med
    en stängd `korning_id` stoppas innan svaret lämnas ut."""
    mcp = Mcp(demo, tmp_path / "m")
    stangd_bas = tmp_path / "data" / "lokal" / "hemlig" / "k1"
    stangd_bas.mkdir(parents=True)
    _kopiera_korning(demo, mcp, "stangd-efter", stangd_bas)

    @mcp.server.resource("floden://test/lacka", mime_type="application/json")
    def lackande_resurs() -> str:
        return json.dumps({"korning_id": "stangd-efter", "hemligt_tal": 99.9})

    async def las() -> tuple[Any | None, MCPError | None]:
        async with Client(mcp.server) as c:
            try:
                return await c.read_resource("floden://test/lacka"), None
            except MCPError as exc:
                return None, exc

    res, exc = anyio.run(las)
    assert res is None and exc is not None
    assert "inte är öppna" in str(exc)
    assert "99.9" not in str(exc)


# ------------------------------------------------------------------ W-B2: rapport_kontrollera i SKRIVVERKTYG


def test_rapport_kontrollera_nekas_for_lasare_utan_diskmutation_och_tillats_for_utredare(
    demo: Demo, miljo: Miljo, tmp_path: Path
) -> None:
    """W-B2: `rapport_kontrollera` ligger i `SKRIVVERKTYG`, nekas för `lasare` utan att skriva
    till `rapport.json`, och tillåts för `utredare` (som uppdaterar `rapport.json`)."""
    assert "rapport_kontrollera" in SKRIVVERKTYG
    ids = setup_projekt(miljo)  # Anna: lasare i A. Bo: utredare i B.
    ta, tb = _token(miljo, ANNA), _token(miljo, BO)
    klasser = {
        "demokvarteret": DataKlass.OPPET,
        ids.anna_projekt: DataKlass.OPPET,
        ids.bo_projekt: DataKlass.OPPET,
    }
    stack = Stack(demo, miljo, tmp_path, behorighet=True, policy=Policy(projektklasser=klasser))
    stack.mcp.app.rapportmapp = tmp_path / "rapporter"
    stack.server = bygg_server(
        stack.mcp.tjanst,
        policy=Policy(projektklasser=klasser),
        logg=stack.logg,
        miljo={},
        behorighet=miljo.adapter.har_roll,
    )
    from floden_mcp.http import KontoSkydd

    skydd = KontoSkydd(stack.server.streamable_http_app(host="0.0.0.0"), miljo.adapter)  # noqa: S104
    rt = RapportTjanst(stack.mcp.tjanst, rapportmapp=stack.mcp.app.rapportmapp, policy=Policy(projektklasser=klasser))
    ra = rt.skapa(projekt=ids.anna_projekt, amne="skyfall", korningar=["demo-vatten"])
    rb = rt.skapa(projekt=ids.bo_projekt, amne="skyfall", korningar=["demo-vatten"])
    fil_a = rt.arkiv.rapportmapp(ra.rapport_id) / "rapport.json"
    fil_b = rt.arkiv.rapportmapp(rb.rapport_id) / "rapport.json"
    fore_a = fil_a.read_text(encoding="utf-8")
    assert ra.kontroll is None and rb.kontroll is None

    with TestClient(skydd, base_url="http://floden.test") as klient:
        a = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        b = _initiera(klient, {"Authorization": f"Bearer {tb}"})
        # Anna (lasare i A) nekas och rapport.json är bitidentisk på disk.
        fel, text, _ = anropa_verktyg(klient, a, "rapport_kontrollera", {"rapport_id": ra.rapport_id})
        assert fel and text == f"Okänd rapport '{ra.rapport_id}'."
        assert fil_a.read_text(encoding="utf-8") == fore_a
        # Bo (utredare i B) tillåts och kontrollen skrivs till rapport.json.
        fel_b, text_b, svar_b = anropa_verktyg(klient, b, "rapport_kontrollera", {"rapport_id": rb.rapport_id})
        assert not fel_b, text_b
        assert svar_b["rapport_id"] == rb.rapport_id and "punkter" in svar_b
        assert rt.arkiv.las(rb.rapport_id).kontroll is not None
        assert fil_b.read_text(encoding="utf-8") != ""
        # HTTP resources/read: Anna får läsa sin egen öppna rapport, men nekas Bos rapport.
        ok_res = _data(_rpc(klient, "resources/read", {"uri": f"floden://rapport/{ra.rapport_id}"}, a))
        assert "result" in ok_res and json.loads(ok_res["result"]["contents"][0]["text"])["rapport_id"] == ra.rapport_id
        nek_res = _data(_rpc(klient, "resources/read", {"uri": f"floden://rapport/{rb.rapport_id}"}, a))
        assert "error" in nek_res and f"Okänd rapport '{rb.rapport_id}'." in nek_res["error"]["message"]
        # Stänger vi Annas projekt (K38) blockeras även Annas egen rapport via HTTP resources/read.
        projektrot = tmp_path / "projekt"
        stack.mcp.app.projektmapp = projektrot
        stack.mcp.app.kartverktyg.projektrot = projektrot
        _skriv_projekt(
            projektrot,
            "anna-stangt",
            ids.anna_projekt,
            oppen=False,
            fraga_namn="anna-sthlm",
            bas=demo.varld / "vatten" / "bas",
        )
        stangd_res = _data(_rpc(klient, "resources/read", {"uri": f"floden://rapport/{ra.rapport_id}"}, a))
        assert "error" in stangd_res and "inte öppna data" in stangd_res["error"]["message"]
        assert ra.titel not in json.dumps(stangd_res)


# ------------------------------------------------------------------ W-B2: karta_bild använder körningens projekt-id


def test_karta_bild_anvander_korningens_verkliga_projekt_id_i_policy(
    demo: Demo, tmp_path: Path
) -> None:
    """W-B2: `karta_bild` härleder projektet ur den begärda körningen i `policy.klass_for`,
    i stället för att hårdkoda `demokvarteret`."""
    policy = Policy(
        standard=frozenset({DataKlass.OPPET}),
        per_klient={"intern-klient": frozenset({DataKlass.OPPET, DataKlass.INTERNT})},
        projektklasser={"demokvarteret": DataKlass.OPPET, "proj-internt": DataKlass.INTERNT},
    )
    mcp = Mcp(demo, tmp_path / "m", policy=policy)
    # Kopiera demo-vatten (öppet underlag så K38 släpper igenom) och märk den med projekt_id = "proj-internt".
    k_mapp = demo.rot / "korningar"
    ny = k_mapp / "k-intern-karta"
    shutil.copytree(k_mapp / "demo-vatten", ny)
    status = json.loads((ny / "korning.json").read_text(encoding="utf-8"))
    status["korning_id"] = "k-intern-karta"
    status["projekt_id"] = "proj-internt"
    (ny / "korning.json").write_text(json.dumps(status), encoding="utf-8")

    # Extern klient som bara får 'oppet' nekas av policyn.
    nekad = mcp.anropa(
        "karta_bild", {"korning_id": "k-intern-karta", "analys": "skyfall"}, klient="extern-klient"
    )
    assert nekad.is_error and "internt" in nekad.content[0].text
    # Intern klient som får 'internt' släpps igenom och svaret bär dataklass 'internt' (inte hårdkodat 'oppet').
    ok = mcp.anropa(
        "karta_bild", {"korning_id": "k-intern-karta", "analys": "skyfall"}, klient="intern-klient"
    )
    assert not ok.is_error, ok.content[0].text
    assert ok.structured_content["dataklass"] == "internt"
    # Demokörningen har fortfarande dataklass 'oppet'.
    demo_ok = mcp.anropa(
        "karta_bild", {"korning_id": "demo-vatten", "analys": "skyfall"}, klient="extern-klient"
    )
    assert not demo_ok.is_error and demo_ok.structured_content["dataklass"] == "oppet"

    # korplan(fraga=...) utan explicit `projekt` för en fråga i ett 'internt'-klassat projekt:
    # både policyn och svarets `dataklass`-fält ska använda ägarprojektet ('internt'), inte 'demokvarteret'.
    projektrot = tmp_path / "projekt"
    mcp.app.projektmapp = projektrot
    mcp.app.fragor.projektrot = projektrot
    mcp.app.kartverktyg.projektrot = projektrot
    _skriv_projekt(
        projektrot,
        "proj-internt-mapp",
        "proj-internt",
        oppen=True,
        fraga_namn="fraga-intern-klass",
        bas=demo.varld / "vatten" / "bas",
    )
    mcp.server = bygg_server(mcp.tjanst, policy=policy, logg=mcp.logg, miljo={})
    nekad_plan = mcp.anropa("korplan", {"fraga": "fraga-intern-klass"}, klient="extern-klient")
    assert nekad_plan.is_error and "internt" in nekad_plan.content[0].text
    ok_plan = mcp.anropa("korplan", {"fraga": "fraga-intern-klass"}, klient="intern-klient")
    assert not ok_plan.is_error, ok_plan.content[0].text
    assert ok_plan.structured_content["dataklass"] == "internt"


# ------------------------------------------------------------------ W-B1: MCP fragor_lista / korplan / kor


def test_mcp_fragor_blockerar_korsprojekt_och_stangda_fragor_med_inloggning_pa(
    demo: Demo, miljo: Miljo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W-B1: `fragor_lista`, `korplan` och `kor` binder projektfrågor till ägarprojektet,
    kräver lasare (`korplan`) resp. utredare + matchande ägarprojekt (`kor`), kräver superadmin
    för fristående frågor och tillämpar K38-öppenhet."""
    from floden_mcp.http import KontoSkydd
    from webb.fragor import FragaTjanst

    ids = setup_projekt(miljo)  # Anna: lasare i A. Bo: utredare i B.
    sup_id = miljo.register.forbered_konto("super@example.test")
    miljo.register.ge_superadmin(sup_id)
    ta, tb, ts = _token(miljo, ANNA), _token(miljo, BO), _token(miljo, SUPER)

    klasser = {
        "demokvarteret": DataKlass.OPPET,
        ids.anna_projekt: DataKlass.OPPET,
        ids.bo_projekt: DataKlass.OPPET,
    }
    stack = Stack(demo, miljo, tmp_path, behorighet=True, policy=Policy(projektklasser=klasser))
    projektrot = tmp_path / "projekt"
    extern_fragemapp = tmp_path / "externa_fragor"
    extern_fragemapp.mkdir()
    stack.mcp.app.projektmapp = projektrot
    stack.mcp.app.fragor.projektrot = projektrot
    stack.mcp.app.fragor.fragemappar.append(extern_fragemapp)
    stack.mcp.app.kartverktyg.projektrot = projektrot

    oppen_bas = demo.varld / "vatten" / "bas"
    stangd_bas = tmp_path / "data" / "lokal" / "hemlig_baskarta" / "k1"
    stangd_bas.mkdir(parents=True)

    # Projekt A (Anna = lasare): en öppen fråga och en fråga vars källor är stängda.
    _skriv_projekt(
        projektrot, "proj-a", ids.anna_projekt, oppen=True, fraga_namn="fraga-a-oppen", bas=oppen_bas
    )
    (projektrot / "proj-a" / "fragor" / "fraga-a-stangd.json").write_text(
        json.dumps(_fraga_dict(stangd_bas, fraga_id="fraga-a-stangd")), encoding="utf-8"
    )
    # Projekt B (Bo = utredare): en öppen fråga och en stängd fråga (k9_stangda).
    _skriv_projekt(
        projektrot, "proj-b", ids.bo_projekt, oppen=True, fraga_namn="fraga-b-oppen", bas=oppen_bas
    )
    stangd_k9_mapp = stack.mcp.app.kartverktyg.mapp / ids.bo_projekt / "k9_stangda"
    stangd_k9_mapp.mkdir(parents=True)
    (stangd_k9_mapp / "oppenhet.toml").write_text("oppen = false\n", encoding="utf-8")
    stangd_k9 = stangd_k9_mapp / "forslag.json"
    stangd_k9.write_text(
        json.dumps({"kontrakt": "K9/v1", "namn": "stangd", "andringar": []}), encoding="utf-8"
    )
    fraga_b_stangd = _fraga_dict(oppen_bas, fraga_id="fraga-b-stangd")
    fraga_b_stangd["scenarier"]["efter"] = {"bas": str(oppen_bas), "k9": [str(stangd_k9)]}
    (projektrot / "proj-b" / "fragor" / "fraga-b-stangd.json").write_text(
        json.dumps(fraga_b_stangd), encoding="utf-8"
    )
    # Fristående frågor utanför projekt (en öppen och en stängd).
    (extern_fragemapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n', encoding="utf-8"
    )
    (extern_fragemapp / "fristaende-oppen.json").write_text(
        json.dumps(_fraga_dict(oppen_bas, fraga_id="fristaende-oppen")), encoding="utf-8"
    )
    (extern_fragemapp / "fristaende-stangd.json").write_text(
        json.dumps(_fraga_dict(stangd_bas, fraga_id="fristaende-stangd")), encoding="utf-8"
    )

    # Bygg om servern så Oppenhet plockar upp nya fragemappar och projektrot.
    stack.server = bygg_server(
        stack.mcp.tjanst,
        policy=Policy(projektklasser=klasser),
        logg=stack.logg,
        miljo={},
        behorighet=miljo.adapter.har_roll,
    )
    skydd = KontoSkydd(stack.server.streamable_http_app(host="0.0.0.0"), miljo.adapter)  # noqa: S104

    # Stubba själva bakgrundskörningen så `kor` bara skriver körregistret snabbt och deterministiskt.
    startade: list[tuple[str, str | None]] = []

    def falsk_start(self: FragaTjanst, ref: str, projekt_id: str | None = None) -> dict[str, Any]:
        startade.append((ref, projekt_id))
        return {
            "korning_id": f"k-{ref}",
            "status": "klar",
            "fraga_id": ref,
            "jobb_totalt": 1,
            "ateranvand": False,
        }

    monkeypatch.setattr(FragaTjanst, "starta_korning", falsk_start)

    with TestClient(skydd, base_url="http://floden.test") as klient:
        a = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        b = _initiera(klient, {"Authorization": f"Bearer {tb}"})
        s = _initiera(klient, {"Authorization": f"Bearer {ts}"})

        # 1. fragor_lista: varje konto ser bara demo + egna öppna projektfrågor; superadmin ser fristående.
        fel_a, _, la = anropa_verktyg(klient, a, "fragor_lista")
        assert not fel_a
        refs_a = {f["ref"] for f in la["fragor"]}
        assert "fraga-a-oppen" in refs_a and "demo_vatten" in refs_a
        assert "fraga-a-stangd" not in refs_a
        assert "fraga-b-oppen" not in refs_a and "fraga-b-stangd" not in refs_a
        assert "fristaende-oppen" not in refs_a and "fristaende-stangd" not in refs_a
        assert la.get("dolda_icke_oppna") == 1  # bara Annas egen stängda fråga räknas

        fel_b, _, lb = anropa_verktyg(klient, b, "fragor_lista")
        assert not fel_b
        refs_b = {f["ref"] for f in lb["fragor"]}
        assert "fraga-b-oppen" in refs_b and "demo_vatten" in refs_b
        assert "fraga-b-stangd" not in refs_b
        assert "fraga-a-oppen" not in refs_b and "fristaende-oppen" not in refs_b
        assert lb.get("dolda_icke_oppna") == 1

        fel_s, _, ls = anropa_verktyg(klient, s, "fragor_lista")
        assert not fel_s
        refs_s = {f["ref"] for f in ls["fragor"]}
        assert "fristaende-oppen" in refs_s and "demo_vatten" in refs_s
        assert "fristaende-stangd" not in refs_s
        assert "fraga-a-oppen" not in refs_s and "fraga-b-oppen" not in refs_s

        # 2. korplan: lasare får planera egen öppen fråga; korsprojekt, fristående och stängda nekas.
        fel, text, plan_a = anropa_verktyg(
            klient, a, "korplan", {"projekt": ids.anna_projekt, "fraga": "fraga-a-oppen"}
        )
        assert not fel, text
        assert plan_a["fraga"] == "fraga-a-oppen"
        # Utan explicit projekt fungerar korplan för Annas egen fråga också.
        fel, text, _ = anropa_verktyg(klient, a, "korplan", {"fraga": "fraga-a-oppen"})
        assert not fel, text
        # Anna får inte planera Bos fråga eller fristående fråga (ser ut som okänd).
        for ref in ("fraga-b-oppen", "fristaende-oppen"):
            fel, text, _ = anropa_verktyg(klient, a, "korplan", {"fraga": ref})
            assert fel and text == f"Okänd fråga '{ref}'.", (ref, text)
        # Bo får inte planera Annas fråga, inte ens med sitt eget projekt angivet.
        fel, text, _ = anropa_verktyg(
            klient, b, "korplan", {"projekt": ids.bo_projekt, "fraga": "fraga-a-oppen"}
        )
        assert fel and text == "Okänd fråga 'fraga-a-oppen'."
        # Stängd fråga i eget projekt blockeras av K38.
        fel, text, _ = anropa_verktyg(
            klient, a, "korplan", {"projekt": ids.anna_projekt, "fraga": "fraga-a-stangd"}
        )
        assert fel and "inte öppna data" in text
        # Superadmin kan planera fristående öppen fråga men inte stängd.
        fel, text, _ = anropa_verktyg(klient, s, "korplan", {"fraga": "fristaende-oppen"})
        assert not fel, text
        fel, text, _ = anropa_verktyg(klient, s, "korplan", {"fraga": "fristaende-stangd"})
        assert fel and "inte öppna data" in text

        # 3. kor: kräver utredare + matchande ägarprojekt + K38-öppenhet.
        # Anna är bara lasare i A -> nekas på sin egen fråga.
        fel, text, _ = anropa_verktyg(
            klient, a, "kor", {"projekt": ids.anna_projekt, "fraga": "fraga-a-oppen"}
        )
        assert fel and "utredare" in text
        # Bo (utredare i B) försöker IDOR: köra Annas fråga med sitt eget projekt_id B -> nekas!
        fel, text, _ = anropa_verktyg(
            klient, b, "kor", {"projekt": ids.bo_projekt, "fraga": "fraga-a-oppen"}
        )
        assert fel and text == "Okänd fråga 'fraga-a-oppen'."
        # Bo försöker köra sin egen projektfråga utan matchande projekt (standard: demokvarteret) -> nekas!
        fel, text, _ = anropa_verktyg(klient, b, "kor", {"fraga": "fraga-b-oppen"})
        assert fel and "hör till projektet" in text
        # Bo försöker köra sin egen stängda k9_stangda-fråga -> nekas av K38!
        fel, text, _ = anropa_verktyg(
            klient, b, "kor", {"projekt": ids.bo_projekt, "fraga": "fraga-b-stangd"}
        )
        assert fel and "inte öppna data" in text
        # Bo kör sin egen öppna fråga med matchande projekt -> tillåts och märks med Bos projekt!
        fel, text, _ = anropa_verktyg(
            klient, b, "kor", {"projekt": ids.bo_projekt, "fraga": "fraga-b-oppen"}
        )
        assert not fel, text
        assert startade[-1] == ("fraga-b-oppen", ids.bo_projekt)
        # Vanlig användare nekas på fristående fråga; superadmin får köra öppen fristående (projekt_id=None).
        fel, text, _ = anropa_verktyg(klient, b, "kor", {"fraga": "fristaende-oppen"})
        assert fel and text == "Okänd fråga 'fristaende-oppen'."
        fel, text, _ = anropa_verktyg(klient, s, "kor", {"fraga": "fristaende-oppen"})
        assert not fel, text
        assert startade[-1] == ("fristaende-oppen", None)
        fel, text, _ = anropa_verktyg(klient, s, "kor", {"fraga": "fristaende-stangd"})
        assert fel and "inte öppna data" in text


def test_mcp_fragor_bevarar_beteende_med_inloggning_av_men_sparrar_stangda_fragor(
    demo: Demo, tmp_path: Path
) -> None:
    """W-B1 med inloggning = 'av': öppna frågor (även fristående) fungerar som förut, medan
    icke-öppna frågefiler fortfarande blockeras av K38 i `fragor_lista`, `korplan` och `kor`."""
    mcp = Mcp(demo, tmp_path / "m")
    stangd_mapp = tmp_path / "data" / "lokal" / "stangd_fraga"
    stangd_mapp.mkdir(parents=True)
    oppen_bas = demo.varld / "vatten" / "bas"
    stangd_bas = tmp_path / "data" / "lokal" / "baskarta" / "k1"
    stangd_bas.mkdir(parents=True)
    # Fråga i stängd mapp (utan oppenhet.toml) och fråga i öppen mapp men med stängd bas.
    (demo.fragor / "stangd-bas-fraga.json").write_text(
        json.dumps(_fraga_dict(stangd_bas, fraga_id="stangd-bas-fraga")), encoding="utf-8"
    )
    (demo.fragor / "oppen-lokal-fraga.json").write_text(
        json.dumps(_fraga_dict(oppen_bas, fraga_id="oppen-lokal-fraga")), encoding="utf-8"
    )
    mcp.server = bygg_server(mcp.tjanst, policy=Policy(), logg=mcp.logg, miljo={})

    lista = mcp.ok("fragor_lista")
    refs = {f["ref"] for f in lista["fragor"]}
    assert "demo_vatten" in refs and "oppen-lokal-fraga" in refs
    assert "stangd-bas-fraga" not in refs
    assert lista["dolda_icke_oppna"] >= 1

    # Öppen fristående fråga går att planera utan inloggning.
    assert not mcp.anropa("korplan", {"fraga": "oppen-lokal-fraga"}).is_error
    # Icke-öppen fråga blockeras i både korplan och kor även när inloggning är av.
    for verktyg in ("korplan", "kor"):
        r = mcp.anropa(verktyg, {"fraga": "stangd-bas-fraga"})
        assert r.is_error and "inte öppna data" in r.content[0].text, verktyg
