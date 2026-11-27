# ruff: noqa: E501
"""Tester för opt-in superadmin MCP-utvecklingsläge (`FLODEN_MCP_UTVECKLINGSLAGE=1`).

Verifierar:
1. Avstängt som förval (unset, `0`, `true`): 0 `utv_*`-verktyg registrerade och `/api/halsa`
   visar `"mcp_utvecklingslage": False`.
2. Dubbellås: HTTP 401 utan token, `403` för inloggad icke-superadmin (`lasare`/`utredare`) och
   spärrad superadmin, nekas även med `inloggning = "av"`, tillåts enbart för aktiv superadmin
   när `FLODEN_MCP_UTVECKLINGSLAGE=1`.
3. Alla 14 typade `utv_*`-verktyg: hälsa, loggar, geografi (lista/planera/mappa), lagring
   (lista/städa), diskstädning, kontobackup, testsvit, benchmark, anonymiserad kontolista,
   projektrollstilldelning och projektradering.
4. Icke-förbikopplingsbara skydd: 0 PII (inga e-postadresser eller personnamn), 0 API-nycklar
   eller hemligheter, K38 blockerar stängda kommuner/geografier/projekt, `demokvarteret` kan
   aldrig raderas, och varje `utv_*`-anrop loggas i den hashkedjade körloggen.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from cryptography.fernet import Fernet
from mcp import Client
from starlette.testclient import TestClient

from floden_mcp.dataklass import Policy
from floden_mcp.http import KontoSkydd
from floden_mcp.korlogg import Korlogg
from floden_mcp.montera import MonteradMcp
from floden_mcp.server import bygg_server
from floden_mcp.utveckling import UTV_PREFIX, _tolka_testsvit_utdata, redigera_text
from konto.nycklar import NyckelForrad
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp import Mcp
from tests.floden_mcp.test_mcp_http import (
    BO,
    _data,
    _initiera,
    _rpc,
    _skapa_token,
    anropa_verktyg,
)
from tests.floden_mcp.test_v1_mcp_sakerhet import SUPER, _skriv_projekt, _token
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_nycklar import _inloggad
from tests.konto.test_projekt_per_anvandare_hjalp import setup_projekt
from webb.api import FlodenApiApp
from webb.asgi import skapa_asgi_app

REPO = Path(__file__).resolve().parents[2]

ALLA_UTV_VERKTYG = frozenset(
    {
        "utv_drift_halsa",
        "utv_drift_loggar",
        "utv_geografi_lista",
        "utv_geografi_planera",
        "utv_geografi_mappa",
        "utv_lagring_lista",
        "utv_lagring_stada",
        "utv_lagring_synka_k1",
        "utv_disk_stada",
        "utv_konto_backup",
        "utv_testsvit_kor",
        "utv_benchmark_kor",
        "utv_konto_lista",
        "utv_projekt_roll_satt",
        "utv_projekt_radera",
    }
)


class UtvStack:
    """HTTP-stack för MCP med kontrollerad miljö (`FLODEN_MCP_UTVECKLINGSLAGE`)."""

    def __init__(
        self,
        demo: Demo,
        miljo: Miljo,
        rot: Path,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        self.mcp = Mcp(demo, rot, konto=miljo.adapter)
        self.mcp.app.projektmapp = rot / "projekt"
        self.mcp.app.kartverktyg.projektrot = rot / "projekt"
        self.mcp.app.projekt_skapa.geomapp = rot / "geo"
        self.mcp.app.projekt_skapa.projektmapp = rot / "projekt"
        self.mcp.app.rapportmapp = rot / "rapporter"
        self.logg = Korlogg(rot / "mcp" / "korlogg.jsonl")
        self.server = bygg_server(
            self.mcp.tjanst,
            policy=Policy(),
            logg=self.logg,
            miljo=env if env is not None else {},
            behorighet=miljo.adapter.har_roll,
        )
        self.skydd = KontoSkydd(self.server.streamable_http_app(host="0.0.0.0"), miljo.adapter)  # noqa: S104


def _skapa_superadmin(miljo: Miljo) -> tuple[str, str]:
    webb, konto_id = _inloggad(miljo, SUPER)  # type: ignore[arg-type]
    miljo.register.ge_superadmin(konto_id)
    token = str(_skapa_token(webb, "super-utv")["token"])
    return konto_id, token


def _skriv_geografi_manifest(
    geomapp: Path,
    geografi_id: str,
    version: str,
    *,
    kommun: str,
    oppen: bool,
    byggnadskalla: str,
) -> Path:
    vmapp = geomapp / geografi_id / version
    vmapp.mkdir(parents=True, exist_ok=True)
    manifest = {
        "kontrakt": "floden-geografi/v1",
        "id": geografi_id,
        "version": version,
        "namn": f"Test {geografi_id}",
        "kommun": kommun,
        "karna_3011": [1000.0, 2000.0, 1500.0, 2500.0],
        "gransen_3011": [500.0, 1500.0, 2000.0, 3000.0],
        "parametrar": {"byggnadskalla": byggnadskalla},
        "kallor": {"byggnader": {"kalla": byggnadskalla, "stangd": not oppen}},
        "lager": {},
        "oppen_for_mcp": oppen,
        "varningar": [],
    }
    (vmapp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if oppen:
        (vmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
            encoding="utf-8",
        )
    else:
        (vmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = false\nlicens = "intern"\n',
            encoding="utf-8",
        )
    return vmapp


# ------------------------------------------------------------------ 1. Avstängt som förval & /api/halsa


def test_utvecklingslage_ar_avstangt_som_forval_och_inga_utv_verktyg_finns(
    demo: Demo, miljo: Miljo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Utan FLODEN_MCP_UTVECKLINGSLAGE=1 registreras 0 `utv_*`-verktyg (även för 0/true/yes)."""
    monkeypatch.delenv("FLODEN_MCP_UTVECKLINGSLAGE", raising=False)
    _, sup_token = _skapa_superadmin(miljo)

    for miljo_dict in ({}, {"FLODEN_MCP_UTVECKLINGSLAGE": "0"}, {"FLODEN_MCP_UTVECKLINGSLAGE": "true"}, {"FLODEN_MCP_UTVECKLINGSLAGE": "yes"}):
        stack = UtvStack(demo, miljo, tmp_path / f"f_{len(miljo_dict)}_{miljo_dict.get('FLODEN_MCP_UTVECKLINGSLAGE', 'none')}", env=miljo_dict)
        with TestClient(stack.skydd, base_url="http://floden.test") as klient:
            sess = _initiera(klient, {"Authorization": f"Bearer {sup_token}"})
            tools = _data(_rpc(klient, "tools/list", {}, sess, id_=2))["result"]["tools"]
            namn = {t["name"] for t in tools}
            assert not any(n.startswith(UTV_PREFIX) for n in namn), (miljo_dict, namn)
            # Direkt anrop mot ett utv_*-verktyg nekas tydligt även om flaggan är av.
            fel, text, _ = anropa_verktyg(klient, sess, "utv_drift_halsa", {})
            assert fel and "403" in text


def test_api_halsa_och_montera_mcp_exponerar_mcp_utvecklingslage(
    miljo: Miljo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/api/halsa` exponerar `mcp_utvecklingslage` (False som förval, True vid flagga=1) utan PII."""
    rot = tmp_path / "rot"
    rot.mkdir()
    shutil.copy(REPO / "config.toml", rot / "config.toml")

    monkeypatch.delenv("FLODEN_MCP_UTVECKLINGSLAGE", raising=False)
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=rot / "config.toml",
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    try:
        st, _, kropp = app.hantera_forfragan("GET", "/api/halsa")
        assert st == 200
        data = json.loads(kropp)
        assert data["mcp_utvecklingslage"] is False
        assert str(tmp_path) not in kropp.decode("utf-8")

        monkeypatch.setenv("FLODEN_MCP_UTVECKLINGSLAGE", "1")
        st2, _, kropp2 = app.hantera_forfragan("GET", "/api/halsa")
        assert st2 == 200
        assert json.loads(kropp2)["mcp_utvecklingslage"] is True

        monkeypatch.setenv("FLODEN_MCP_UTVECKLINGSLAGE", "0")
        assert app.halsa()["mcp_utvecklingslage"] is False
    finally:
        app.stang()

    # ASGI montera_mcp: utvecklingslage=False utan flagga, True med flagga=1 + aktiv inloggning.
    monkeypatch.delenv("FLODEN_MCP_UTVECKLINGSLAGE", raising=False)
    asgi_av = skapa_asgi_app(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=rot / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        klientmapp=tmp_path / "dist",
        konto=miljo.adapter,
        mcp_host="0.0.0.0",  # noqa: S104
    )
    assert isinstance(asgi_av.mcp, MonteradMcp)
    assert asgi_av.mcp.utvecklingslage is False

    monkeypatch.setenv("FLODEN_MCP_UTVECKLINGSLAGE", "1")
    asgi_pa = skapa_asgi_app(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=rot / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        klientmapp=tmp_path / "dist",
        konto=miljo.adapter,
        mcp_host="0.0.0.0",  # noqa: S104
    )
    assert isinstance(asgi_pa.mcp, MonteradMcp)
    assert asgi_pa.mcp.utvecklingslage is True
    with TestClient(asgi_pa, base_url="https://floden.test") as klient:
        h = klient.get("/api/halsa")
        assert h.status_code == 200
        assert h.json()["mcp_utvecklingslage"] is True


# ------------------------------------------------------------------ 2. Dubbellås: 401/403 för icke-superadmin


def test_dubbellas_nekar_oautentiserad_lasare_utredare_och_inloggning_av(
    demo: Demo, miljo: Miljo, tmp_path: Path
) -> None:
    """Med flagga=1 får icke-superadmin (lasare/utredare) 403 på alla 14 `utv_*`-verktyg,
    oautentiserad HTTP får 401, spärrad superadmin nekas och `inloggning=av` nekas."""
    setup_projekt(miljo)  # Anna = lasare, Bo = utredare
    ta, tb = _token(miljo, ANNA), _token(miljo, BO)
    sup_id, ts = _skapa_superadmin(miljo)
    stack = UtvStack(demo, miljo, tmp_path / "http", env={"FLODEN_MCP_UTVECKLINGSLAGE": "1"})

    with TestClient(stack.skydd, base_url="http://floden.test") as klient:
        # Oautentiserad HTTP -> 401 med WWW-Authenticate.
        unauth = _rpc(klient, "tools/call", {"name": "utv_drift_halsa", "arguments": {}}, {})
        assert unauth.status_code == 401
        assert unauth.headers["www-authenticate"].startswith("Bearer ")

        sess_anna = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        sess_bo = _initiera(klient, {"Authorization": f"Bearer {tb}"})
        sess_sup = _initiera(klient, {"Authorization": f"Bearer {ts}"})

        # Kontrollera att alla 14 utv_*-verktyg registrerats med beskrivningar och modell/roll.
        tools = _data(_rpc(klient, "tools/list", {}, sess_sup, id_=2))["result"]["tools"]
        utv_tools = {t["name"]: t for t in tools if t["name"].startswith(UTV_PREFIX)}
        assert set(utv_tools) == ALLA_UTV_VERKTYG
        for namn, schema in utv_tools.items():
            assert len(schema.get("description", "")) > 40, namn
            assert {"modell", "roll"} <= set(schema["inputSchema"]["properties"]), namn

        # Både Anna (lasare) och Bo (utredare) får 403 på VARJE utv_*-verktyg.
        for sess in (sess_anna, sess_bo):
            for namn in sorted(ALLA_UTV_VERKTYG):
                fel, text, _ = anropa_verktyg(klient, sess, namn, {})
                assert fel, f"{namn} borde ha nekats för icke-superadmin"
                assert "403" in text and "superadmin" in text.lower(), (namn, text)

        # Spärrad superadmin nekas (401 från KontoSkydd / 403 i middleware).
        assert miljo.adapter.sessioner is not None
        miljo.adapter.sessioner.sparra_konto(sup_id)
        sparrad_svar = _rpc(klient, "tools/call", {"name": "utv_drift_halsa", "arguments": {}}, sess_sup, id_=99)
        assert sparrad_svar.status_code == 401

    # Alla nekade försök finns i den hashkedjade körloggen och kedjan är hel.
    ok_kedja, antal_rader, fel_rad = stack.logg.verifiera()
    assert ok_kedja and fel_rad is None and antal_rader >= len(ALLA_UTV_VERKTYG) * 2
    nekade = [r for r in stack.logg.las() if r.get("utfall") == "nekad_behorighet"]
    assert len(nekade) == len(ALLA_UTV_VERKTYG) * 2

    # Även utan inloggning (stdio / inloggning="av") får utv_* ALDRIG köras.
    mcp_utan = Mcp(demo, tmp_path / "utan")
    srv_utan = bygg_server(
        mcp_utan.tjanst,
        policy=Policy(),
        logg=mcp_utan.logg,
        miljo={"FLODEN_MCP_UTVECKLINGSLAGE": "1"},
    )

    async def _anropa_utan() -> Any:
        async with Client(srv_utan) as c:
            return await c.call_tool("utv_drift_halsa", {})

    r_utan = anyio.run(_anropa_utan)
    assert r_utan.is_error and "Inloggning krävs" in r_utan.content[0].text


# ------------------------------------------------------------------ 3. Superadmin-verktyg, K38, demo-skydd, 0 PII, 0 hemligheter


def test_superadmin_utv_verktyg_k38_demo_skydd_noll_pii_och_noll_hemligheter(
    demo: Demo, miljo: Miljo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heltäckande test för superadmin med flagga=1: verifierar samtliga 14 verktyg, K38,
    demoskydd, 0 PII, 0 API-nycklar/hemligheter och intakt hashkedjad körlogg."""
    ids = setup_projekt(miljo)  # Anna = lasare i anna_projekt, Bo = utredare i bo_projekt
    _, anna_id = _inloggad(miljo, ANNA)
    _, bo_id = _inloggad(miljo, BO)  # type: ignore[arg-type]
    anna_konto = miljo.register.konto(anna_id)
    bo_konto = miljo.register.konto(bo_id)
    assert anna_konto is not None and bo_konto is not None
    sup_id, ts = _skapa_superadmin(miljo)

    # Lägg in riktiga krypterade nycklar i NyckelForrad och spärra .las() så klartext aldrig nås!
    fernet_key = Fernet.generate_key().decode()
    miljo.adapter.nycklar = NyckelForrad(miljo.register, fernet_key, miljo.klocka)
    hemlig_anthropic = "sk-ant-api03-UTV-HEMLIG-9911"
    hemlig_lm_id = "lm-klient-utv-4455"
    hemlig_lm_sec = "lm-hemlighet-utv-6677"
    hemlig_s3_access = "s3-access-HEMLIG-1234"
    hemlig_s3_secret = "s3-secret-HEMLIG-5678"
    hemlig_oauth = "oauth-client-HEMLIG-9012"
    assert miljo.adapter.nycklar.spara(sup_id, "anthropic", {"nyckel": hemlig_anthropic})
    assert miljo.adapter.nycklar.spara(
        sup_id, "lantmateriet", {"klient_id": hemlig_lm_id, "klient_hemlighet": hemlig_lm_sec}
    )

    nyckel_las_anrop: list[str] = []

    def _forbjuden_las(self: NyckelForrad, *a: Any, **k: Any) -> Any:
        nyckel_las_anrop.append("las")
        raise AssertionError("NyckelForrad.las får aldrig anropas via MCP-utvecklingsläget")

    monkeypatch.setattr(NyckelForrad, "las", _forbjuden_las)

    # Skapa lokal objektlagring + lokal LRU-cache i tmp_path för lagring/disk-verktygen.
    lagring_rot = tmp_path / "objektlagring"
    (lagring_rot / "geografi" / "goteborg" / "r1").mkdir(parents=True)
    (lagring_rot / "geografi" / "goteborg" / "r1" / "mark.tif").write_bytes(b"gbg-open")
    (lagring_rot / "geografi" / "stockholm" / "r1").mkdir(parents=True)
    (lagring_rot / "geografi" / "stockholm" / "r1" / "hemlig_baskarta.tif").write_bytes(b"sthlm-closed")
    (lagring_rot / "tmp" / "skiss" / "s1").mkdir(parents=True)
    (lagring_rot / "tmp" / "skiss" / "s1" / "temp.bin").write_bytes(b"temp-data")
    (lagring_rot / "backup" / "konto").mkdir(parents=True)
    (lagring_rot / "backup" / "konto" / "konto.sqlite").write_bytes(b"secret-db")
    (lagring_rot / "projekt" / "p1").mkdir(parents=True)
    (lagring_rot / "projekt" / "p1" / "data.json").write_bytes(b"{}")

    lru_cache_mapp = tmp_path / "s3_lru"
    lru_cache_mapp.mkdir()
    gammal_lru = lru_cache_mapp / "geografi" / "goteborg" / "gammal.bin"
    gammal_lru.parent.mkdir(parents=True)
    gammal_lru.write_bytes(b"x" * 2048)
    os.utime(gammal_lru, (1_000_000, 1_000_000))  # gammal -> träffas av max_alder_dagar
    stangd_lru = lru_cache_mapp / "geografi" / "stockholm" / "hemlig_baskarta.tif"
    stangd_lru.parent.mkdir(parents=True)
    stangd_lru.write_bytes(b"s" * 512)
    os.utime(stangd_lru, (1_000_000, 1_000_000))
    backup_lru = lru_cache_mapp / "backup" / "konto" / "konto.sqlite"
    backup_lru.parent.mkdir(parents=True)
    backup_lru.write_bytes(b"b" * 256)
    os.utime(backup_lru, (1_000_000, 1_000_000))
    epost_lru = lru_cache_mapp / "tmp" / "anna@example.test_skiss.bin"
    epost_lru.parent.mkdir(parents=True)
    epost_lru.write_bytes(b"e" * 128)
    os.utime(epost_lru, (1_000_000, 1_000_000))

    env_map = {
        "FLODEN_MCP_UTVECKLINGSLAGE": "1",
        "FLODEN_LAGRING_ROT": str(lagring_rot),
        "FLODEN_LOKAL_CACHE_MAPP": str(lru_cache_mapp),
        "FLODEN_S3_ACCESS_KEY": hemlig_s3_access,
        "FLODEN_S3_SECRET_KEY": hemlig_s3_secret,
        "FLODEN_KONTO_GOOGLE_KLIENT_HEMLIGHET": hemlig_oauth,
        "FLODEN_NYCKELHEMLIGHET": fernet_key,
    }
    stack = UtvStack(demo, miljo, tmp_path / "stack", env=env_map)

    # Skapa en öppen mappad geografi (goteborg-test@v1) och en stängd (stockholm-test@v1).
    _skriv_geografi_manifest(
        stack.mcp.app.projekt_skapa.geomapp,
        "goteborg-test",
        "v1",
        kommun="goteborg",
        oppen=True,
        byggnadskalla="filpaket",
    )
    _skriv_geografi_manifest(
        stack.mcp.app.projekt_skapa.geomapp,
        "stockholm-test",
        "v1",
        kommun="stockholm",
        oppen=False,
        byggnadskalla="lokal_fil",
    )

    # Skapa ett öppet projekt (ids.anna_projekt) och ett stängt K38-projekt (ids.bo_projekt) på disk.
    _skriv_projekt(
        stack.mcp.app.projektmapp,
        "anna-oppet",
        ids.anna_projekt,
        oppen=True,
        fraga_namn="anna-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )
    _skriv_projekt(
        stack.mcp.app.projektmapp,
        "bo-stangt",
        ids.bo_projekt,
        oppen=False,
        fraga_namn="bo-sthlm",
        bas=demo.varld / "vatten" / "bas",
    )
    # Skapa även en gammal tempfil i appens cachemapp för utv_disk_stada.
    gammal_tmp = stack.mcp.app.fragor.cachemapp / ".floden_overgiven_tmp"
    gammal_tmp.write_bytes(b"z" * 1024)
    os.utime(gammal_tmp, (1_000_000, 1_000_000))

    # Stubba mappa_geografi så utv_geografi_mappa kan prövas helt utan nätverk.
    from geografi import mappa as geo_mappa_mod

    mappa_anrop: list[tuple[str, str]] = []

    def _falsk_mappa(kommun: Any, geografi_id: str, **kwargs: Any) -> geo_mappa_mod.MappaResultat:
        mappa_anrop.append((kommun.id, geografi_id))
        skriv = kwargs.get("skriv")
        if callable(skriv):
            skriv("Mappning klar utan PII (test).")
        return geo_mappa_mod.MappaResultat(
            ref=f"{geografi_id}@{kwargs.get('version', 'v1')}",
            manifest_fil=tmp_path / "manifest.json",
            manifest_sha256="a" * 64,
            nytt=True,
            varningar=[],
            oppen_for_mcp=bool(kwargs.get("villkor_kontrollerade")),
        )

    monkeypatch.setattr(geo_mappa_mod, "mappa_geografi", _falsk_mappa)

    alla_utdata: list[str] = []

    def _kor(klient: TestClient, sess: dict[str, str], namn: str, arg: dict[str, Any] | None = None) -> tuple[bool, str, dict[str, Any]]:
        fel, text, struktur = anropa_verktyg(klient, sess, namn, arg or {})
        alla_utdata.append(text + json.dumps(struktur, ensure_ascii=False))
        return fel, text, struktur

    with TestClient(stack.skydd, base_url="http://floden.test") as klient:
        sess = _initiera(klient, {"Authorization": f"Bearer {ts}"})

        # 1. utv_drift_halsa
        fel, text, halsa = _kor(klient, sess, "utv_drift_halsa")
        assert not fel, text
        assert halsa["mcp_utvecklingslage"] is True
        assert halsa["halsa"]["status"] == "ok"
        assert halsa["konto_db"]["integrity_check"] == "ok"
        assert halsa["konto_db"]["foreign_key_check_ok"] is True
        assert halsa["konto_db"]["wal_aktivt"] is True
        assert halsa["korlogg"]["ok"] is True
        assert halsa["commit"]

        # 2. utv_konto_lista: 0 PII!
        fel, text, kl = _kor(klient, sess, "utv_konto_lista")
        assert not fel, text
        assert kl["antal_totalt"] >= 3
        konto_ids = {k["konto_id"] for k in kl["konton"]}
        assert {anna_konto.id, bo_konto.id, sup_id} <= konto_ids
        for k in kl["konton"]:
            assert set(k) == {"konto_id", "superadmin", "sparrad", "skapad", "senast_inloggad", "organisationer"}
            assert "epost" not in k and "namn" not in k

        # 3. utv_geografi_lista: endast öppen kommun/geografi; Stockholm dold!
        fel, text, gl = _kor(klient, sess, "utv_geografi_lista")
        assert not fel, text
        assert [k["kommun_id"] for k in gl["oppna_kommuner"]] == ["goteborg"]
        assert gl["dolda_icke_oppna_kommuner"] >= 1
        assert [g["ref"] for g in gl["mappade_geografier"]] == ["goteborg-test@v1"]
        assert gl["dolda_icke_oppna_geografier"] == 1
        assert "stockholm" not in json.dumps(gl).lower()

        # 4. utv_geografi_planera: öppen Göteborg ok; stängd Stockholm & filsökväg nekas!
        fel, text, plan = _kor(
            klient,
            sess,
            "utv_geografi_planera",
            {
                "kommun": "goteborg",
                "avgransning": [147400.0, 6397500.0, 147900.0, 6398000.0],
                "avgransning_epsg": 3007,
            },
        )
        assert not fel, text
        assert plan["kommun_id"] == "goteborg" and plan["dataklass"] == "oppen" and plan["antal_karnrutor"] >= 1
        fel_s, text_s, _ = _kor(
            klient,
            sess,
            "utv_geografi_planera",
            {"kommun": "stockholm", "avgransning": [153400.0, 6577750.0, 153900.0, 6578250.0]},
        )
        assert fel_s and "K38" in text_s
        fel_fil, text_fil, _ = _kor(
            klient,
            sess,
            "utv_geografi_planera",
            {"kommun": "goteborg", "avgransning": "/etc/passwd"},
        )
        assert fel_fil and "bbox" in text_fil.lower()

        # 5. utv_geografi_mappa: öppen Göteborg ok; stängd Stockholm nekas!
        fel_ms, text_ms, _ = _kor(klient, sess, "utv_geografi_mappa", {"geografi": "stockholm-test"})
        assert fel_ms and "K38" in text_ms
        assert mappa_anrop == []
        fel_mg, text_mg, res_mg = _kor(
            klient,
            sess,
            "utv_geografi_mappa",
            {"geografi": "goteborg-test", "version": "v2", "villkor_kontrollerade": True},
        )
        assert not fel_mg, text_mg
        assert res_mg["ref"] == "goteborg-test@v2" and res_mg["oppen_for_mcp"] is True
        assert mappa_anrop == [("goteborg", "goteborg-test")]

        # 6. utv_lagring_lista: bara geografi/ och tmp/; filtrerar bort stängda kommuner och förbjudna prefix!
        fel, text, lg = _kor(klient, sess, "utv_lagring_lista", {"prefix": "geografi/"})
        assert not fel, text
        assert lg["objekt"] == ["geografi/goteborg/r1/mark.tif"]
        assert lg["dolda_icke_oppna"] == 1
        assert "stockholm" not in json.dumps(lg).lower()

        fel, text, lt = _kor(klient, sess, "utv_lagring_lista", {"prefix": "tmp/"})
        assert not fel, text
        assert lt["objekt"] == ["tmp/skiss/s1/temp.bin"]

        for forbjudet_prefix in ("backup/", "konto/", "projekt/", "", "../etc/", "geografi/stockholm/"):
            fel_p, _, _ = _kor(klient, sess, "utv_lagring_lista", {"prefix": forbjudet_prefix})
            assert fel_p, forbjudet_prefix

        # 7. utv_lagring_stada: torrkor=True som förval, döljer stängd kommun/backup & redigerar PII, nekar icke-tmp/-prefix.
        fel, text, st_torr = _kor(klient, sess, "utv_lagring_stada")
        assert not fel, text
        assert st_torr["torrkor"] is True and st_torr["antal_raderade"] == 4
        assert st_torr["dolda_icke_oppna"] == 2  # stockholm + backup/konto dolda
        assert "geografi/goteborg/gammal.bin" in st_torr["raderade_filer"]
        assert "tmp/[redigerad-epost]_skiss.bin" in st_torr["raderade_filer"]
        assert "stockholm" not in json.dumps(st_torr).lower()
        assert "konto.sqlite" not in json.dumps(st_torr).lower()
        assert gammal_lru.is_file()  # torrkörning rörde inte filen!
        fel, text, st_skarp = _kor(klient, sess, "utv_lagring_stada", {"torrkor": False})
        assert not fel, text
        assert st_skarp["torrkor"] is False and st_skarp["antal_raderade"] == 4
        assert st_skarp["dolda_icke_oppna"] == 2
        assert not gammal_lru.exists()

        fel_obj_torr, _, obj_torr = _kor(klient, sess, "utv_lagring_stada", {"prefix": "tmp/skiss/"})
        assert not fel_obj_torr and obj_torr["torrkor"] is True and obj_torr["antal_raderade"] == 1
        assert (lagring_rot / "tmp" / "skiss" / "s1" / "temp.bin").is_file()
        fel_obj_bad, _, _ = _kor(klient, sess, "utv_lagring_stada", {"prefix": "geografi/", "torrkor": False})
        assert fel_obj_bad
        fel_obj_del, _, obj_del = _kor(
            klient, sess, "utv_lagring_stada", {"prefix": "tmp/skiss/", "torrkor": False}
        )
        assert not fel_obj_del and obj_del["antal_raderade"] == 1
        assert not (lagring_rot / "tmp" / "skiss" / "s1" / "temp.bin").exists()

        # 8. utv_disk_stada: torrkor=True förval, skyddar projekt/demo, rensar vid torrkor=False.
        fel, text, ds_torr = _kor(klient, sess, "utv_disk_stada")
        assert not fel, text
        assert ds_torr["torrkor"] is True and ds_torr["antal_raderade"] >= 1
        assert gammal_tmp.is_file()
        fel, text, ds_skarp = _kor(klient, sess, "utv_disk_stada", {"torrkor": False})
        assert not fel, text
        assert ds_skarp["torrkor"] is False and not gammal_tmp.exists()

        # 9. utv_konto_backup: riktig online-backup + integritetskontroll, 0 PII.
        fel, text, bk = _kor(klient, sess, "utv_konto_backup", {"s3": False})
        assert not fel, text
        assert bk["ok"] is True
        assert bk["integrity_check"] == "ok"
        assert bk["foreign_key_check_ok"] is True
        assert bk["manifest_verifierat"] is True
        assert bk["tabellrader"]["konto"] >= 3

        # 10. utv_testsvit_kor: isolerad delprocess, avvisar ogiltig grupp (ingen skalinjektion).
        fel_bad_g, _, _ = _kor(klient, sess, "utv_testsvit_kor", {"grupp": "A; rm -rf /"})
        assert fel_bad_g
        fel, text, ts_res = _kor(klient, sess, "utv_testsvit_kor", {"grupp": "A"})
        assert not fel, text
        assert ts_res["ok"] is True
        assert ts_res["antal_godkanda"] == ts_res["antal_totalt"] and ts_res["antal_totalt"] >= 13
        assert ts_res["max_volymfel_rel"] == 0.0

        # 11. utv_benchmark_kor: lättviktigt isolerat benchmarkfall; ogiltig del/fall nekas.
        fel_bad_b, _, _ = _kor(klient, sess, "utv_benchmark_kor", {"del_namn": "okand"})
        assert fel_bad_b
        fel, text, bm_res = _kor(
            klient,
            sess,
            "utv_benchmark_kor",
            {"del_namn": "skyfall", "fall_id": "vatten2d-ref-64x64", "uppvarmning": 0, "upprepningar": 1},
        )
        assert not fel, text
        assert bm_res["fall_id"] == "vatten2d-ref-64x64"
        assert bm_res["resultat"]["status"] == "ok"

        # 12. utv_projekt_roll_satt: fungerar på öppet projekt; nekar stängt K38-projekt och demokvarteret!
        fel_pr_demo, _, _ = _kor(
            klient,
            sess,
            "utv_projekt_roll_satt",
            {"projekt_id": "demokvarteret", "konto_id": anna_konto.id, "projekt_roll": "utredare"},
        )
        assert fel_pr_demo
        fel_pr_stangt, text_pr_stangt, _ = _kor(
            klient,
            sess,
            "utv_projekt_roll_satt",
            {"projekt_id": ids.bo_projekt, "konto_id": anna_konto.id, "projekt_roll": "lasare"},
        )
        assert fel_pr_stangt and "öppna data" in text_pr_stangt
        assert miljo.register.projektroll(anna_konto.id, ids.bo_projekt) is None

        fel_pr_ok, text_pr_ok, pr_svar = _kor(
            klient,
            sess,
            "utv_projekt_roll_satt",
            {"projekt_id": ids.anna_projekt, "konto_id": bo_konto.id, "projekt_roll": "utredare"},
        )
        assert not fel_pr_ok, text_pr_ok
        assert pr_svar["projekt_roll"] == "utredare"
        assert miljo.register.projektroll(bo_konto.id, ids.anna_projekt) == "utredare"

        # 13. utv_projekt_radera: nekar demokvarteret och stängt K38-projekt; raderar öppet projekt!
        fel_rd_demo, text_rd_demo, _ = _kor(
            klient,
            sess,
            "utv_projekt_radera",
            {"projekt_id": "demokvarteret", "bekrafta_namn": "demokvarteret"},
        )
        assert fel_rd_demo and "demokvarteret" in text_rd_demo.lower()

        fel_rd_stangt, text_rd_stangt, _ = _kor(
            klient,
            sess,
            "utv_projekt_radera",
            {"projekt_id": ids.bo_projekt, "bekrafta_namn": "Kvarteret Beta"},
        )
        assert fel_rd_stangt and "öppna data" in text_rd_stangt
        assert (stack.mcp.app.projektmapp / "bo-stangt").is_dir()
        assert miljo.register.projekt_finns(ids.bo_projekt)

        # Hämta exakt projektnamn för det öppna projektet ur registret (endast i testsetup).
        with miljo.register.db.lasning() as c:
            oppet_namn = str(
                c.execute("SELECT namn FROM projekt WHERE id = ?", (ids.anna_projekt,)).fetchone()[0]
            )
        fel_rd_ok, text_rd_ok, rd_svar = _kor(
            klient,
            sess,
            "utv_projekt_radera",
            {"projekt_id": ids.anna_projekt, "bekrafta_namn": oppet_namn},
        )
        assert not fel_rd_ok, text_rd_ok
        assert rd_svar["status"] == "raderat"
        assert not (stack.mcp.app.projektmapp / "anna-oppet").exists()
        assert not miljo.register.projekt_finns(ids.anna_projekt)

        # 14. utv_drift_loggar: visar körloggen med verifierad hashkedja, 0 PII/tokens.
        fel, text, dl = _kor(klient, sess, "utv_drift_loggar", {"senaste": 50})
        assert not fel, text
        assert dl["hashkedja"]["ok"] is True
        assert dl["antal_rader"] >= 15
        loggade_verktyg = {r["verktyg"] for r in dl["rader"]}
        assert {"utv_drift_halsa", "utv_konto_lista", "utv_konto_backup", "utv_projekt_radera"} <= loggade_verktyg

    # Slutkontroll: NyckelForrad.las anropades ALDRIG; 0 PII och 0 hemligheter i ALLA svar + körlogg!
    assert nyckel_las_anrop == []
    korlogg_text = stack.logg.sokvag.read_text(encoding="utf-8")
    kombinerad = "\n".join(alla_utdata) + "\n" + korlogg_text

    for pii in (
        "anna@example.test",
        "bo@example.test",
        "super@example.test",
        "Anna Exempel",
        "Bo Exempel",
        "Sam Super",
    ):
        assert pii not in kombinerad, f"PII läckte: {pii!r}"

    for hemligt in (
        hemlig_anthropic,
        hemlig_lm_id,
        hemlig_lm_sec,
        hemlig_s3_access,
        hemlig_s3_secret,
        hemlig_oauth,
        fernet_key,
        ts,
        ts[-12:],
    ):
        assert hemligt not in kombinerad, f"Hemlighet läckte: {hemligt!r}"

    # Hashkedjan är helt intakt över samtliga anrop.
    ok_kedja, antal_rader, fel_rad = stack.logg.verifiera()
    assert ok_kedja and fel_rad is None and antal_rader >= 20


def test_redigera_text_tar_bort_epost_tokens_och_hemligheter() -> None:
    """Enhetstest för loggredigering (0 PII, 0 tokens)."""
    ra = (
        "Användare anna@example.test skickade Bearer fl_mcp_abc123456789 "
        "och sk-ant-api03-hemligtoken och secret_key=my_s3_secret"
    )
    ren = redigera_text(ra)
    for kansligt in ("anna@example.test", "fl_mcp_abc123456789", "sk-ant-api03-hemligtoken", "my_s3_secret"):
        assert kansligt not in ren
    assert "[redigerad-epost]" in ren
    assert "Bearer [redigerad]" in ren
    assert "[redigerad-token]" in ren
    assert "secret_key=[redigerad]" in ren
    assert "fl_mcp_xyz999" not in redigera_text("Authorization: Bearer fl_mcp_xyz999")


def test_tolka_testsvit_utdata_flera_tabeller_och_ea_volymfel() -> None:
    """Flertabellstolkning för `floden testsvit` (9-kol A/B + 11-kol EA + Hunter D + Dagsljus E)."""
    multi = "\n".join(
        [
            "| ID | Grupp | Namn | Status | Utfall | Volymfel | Maxdjupsfel | Körtid (s) | Kommentar |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            "| A0 | A | Skelettfall | skelett | SKELETT | – | – | 0.0000 | OK |",
            "| A1 | A | Enkel sänka | redo | GODKAND | 0.0000% | 0.0000 m | 0.0159 | OK |",
            "",
            "| Fall | Namn | Utfall | Volymfel | Maxdjup | MAE | IoU | 2D (s) | Hybrid (s) | Uppsnabbning | Kommentar |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            "| EA1 | EA Test 1 | GODKAND | 0.0000% | 0.405 m | 0.0053 m | 0.980 | 0.333 | – | – | OK |",
            "| EA2 | EA Test 2 | GODKAND | 0.0250% | 0.577 m | 0.0239 m | 0.850 | 1.142 | 0.657 | 1.74x | OK |",
            "| EA8A | EA Test 8A | GODKAND | 0.0000% | 0.557 m | 0.0200 m | 0.930 | 0.817 | 0.569 | 1.44x | OK |",
            "",
            "Sammanfattning: 5/5 testfall passerade.",
        ]
    )
    fall, ok, totalt, max_vol, summ = _tolka_testsvit_utdata(multi)
    assert [f["id"] for f in fall] == ["A0", "A1", "EA1", "EA2", "EA8A"]
    assert all(f["id"] != "Fall" for f in fall)
    assert ok == 5 and totalt == 5
    assert summ == "Sammanfattning: 5/5 testfall passerade."
    ea2 = next(f for f in fall if f["id"] == "EA2")
    assert ea2["utfall"] == "GODKAND"
    assert ea2["volymfel"] == "0.0250%"
    assert ea2["kortid_s"] == "1.142"
    assert max_vol == pytest.approx(0.00025)

    # Grupp D (Hunter, vetenskaplig notation utan %) + Grupp E (dagsljus med Status-kolumn)
    hunter = "\n".join(
        [
            "| Fall | dx (m) | t (s) | RMSE (m) | max fel (m) | frontfel (m) | volymfel | körtid (s) |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| D1 | 5 | 3600 | 0.0040 | 0.0120 | +0.0 | 2.5e-07 | 0.42 |",
            "Sammanfattning (Grupp D / analytiska 2D-facit): GODKAND.",
        ]
    )
    h_fall, h_ok, h_tot, h_vol, _ = _tolka_testsvit_utdata(hunter)
    assert [f["id"] for f in h_fall] == ["D1"]
    assert h_ok == 1 and h_tot == 1
    assert h_vol == pytest.approx(2.5e-7)

    dagsljus = "\n".join(
        [
            "| Fall | Beskrivning | Avvikelse | Gräns | Enhet | Körtid (s) | Status |",
            "| --- | --- | ---: | ---: | --- | ---: | --- |",
            "| E1 | Plan mark | 0 | 0.01 | – | 0.1 | GODKAND |",
            "Sammanfattning (Grupp E / dagsljus): 1/1 passerade.",
        ]
    )
    e_fall, e_ok, e_tot, e_vol, _ = _tolka_testsvit_utdata(dagsljus)
    assert [f["id"] for f in e_fall] == ["E1"]
    assert e_fall[0]["utfall"] == "GODKAND"
    assert e_ok == 1 and e_tot == 1 and e_vol is None
