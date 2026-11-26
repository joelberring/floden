# ruff: noqa: E501, F811
"""Deterministiska regressionstester för Våg 2 (W-B1, W-B6, W-B8, W-B9, D-B2, D-B4, D-B5, D-B6)."""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from data_inlasning.nmd import NmdLager, skriv_nmd
from demovarld.varld import (
    KODVERSION,
    START_TID,
    DemoVarld,
    bygg_ruta_vatten,
    byggnader,
    kantsten,
    markslag_grund,
    portar,
    terrang_z,
    vegetation_hojd,
)
from gemensamt.objektlagring import LokalLagring
from geografi.modell import KONTRAKT_GEOGRAFI, lager_mapp, lagra, skriv_manifest
from konto.adapter import KontoAdapter
from konto.konfig import KontoKonfig
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta
from tests.konto.conftest import ANNA, ORIGIN, Miljo, Webblasare
from tests.konto.conftest import miljo as konto_miljo  # noqa: F401
from tests.webb.test_inloggning import _http
from tests.webb.test_projekt_per_anvandare import BO, _setup
from webb.api import FlodenApiApp, skapa_http_server
from webb.asgi import FlodenAsgiApp
from webb.fel import FlodenWebbFel
from webb.fragor import LAGER_VERSION, FragaTjanst, KoFullFel, Korning
from webb.kartverktyg import KartverktygTjanst
from webb.projektkalla import KONTRAKT_PROJEKT
from webb.resultatlager import etikett_och_enhet

REPO = Path(__file__).resolve().parents[2]
SUPER = {
    "sub": "sub-super-v2",
    "email": "super@example.test",
    "email_verified": True,
    "name": "Sam Super",
}
DI = {
    "sub": "sub-di-v2",
    "email": "di@example.test",
    "email_verified": True,
    "name": "Di Utomstaende",
}


def _skriv_min_fraga(fil: Path, fraga_id: str, bas: Path) -> None:
    fil.parent.mkdir(parents=True, exist_ok=True)
    fil.write_text(
        json.dumps(
            {
                "kontrakt": "K20/v1",
                "fraga_id": fraga_id,
                "beskrivning": f"Testfråga {fraga_id}",
                "scenarier": {"bas": {"bas": str(bas)}},
                "analyser": [
                    {
                        "id": "skyfall",
                        "losare": "vatten.statisk_fyllning",
                        "scenarier": ["bas"],
                        "parametrar": {"regn_mm": 50.0},
                    }
                ],
                "jamforelser": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _skriv_projekt_med_fraga(
    projektrot: Path, slug: str, register_id: str, fraga_namn: str, bas: Path
) -> Path:
    pmapp = projektrot / slug
    (pmapp / "fragor").mkdir(parents=True, exist_ok=True)
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": slug,
                "register_id": register_id,
                "namn": slug,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _skriv_min_fraga(pmapp / "fragor" / f"{fraga_namn}.json", fraga_namn, bas)
    return pmapp


# ------------------------------------------------------------------ W-B1: Cross-tenant IDOR på /api/fragor


def test_w_b1_fragor_blockerar_korsprojekt_idor_och_skyddar_fristaende(
    tmp_path: Path, konto_miljo: Miljo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W-B1: `GET /api/fragor`, `GET /api/fragor/<ref>`, `POST .../plan` och `POST .../kor`
    binder projektfrågor till ägarprojektet, stoppar cross-tenant IDOR (inkl. falskt `projekt_id`)
    och kräver superadmin för fristående frågor när inloggning är aktiv."""
    st = _setup(konto_miljo)  # Anna = lasare i anna_projekt, Bo = utredare i bo_projekt
    sup = konto_miljo.register.forbered_konto("super@example.test")
    konto_miljo.register.ge_superadmin(sup)

    bas = REPO / "testsvit" / "A1_enkel_sanka" / "indata"
    projektrot = tmp_path / "projekt"
    extern_mapp = tmp_path / "fristaende"
    _skriv_projekt_med_fraga(projektrot, "proj-a", st.anna_projekt, "fraga-a", bas)
    _skriv_projekt_med_fraga(projektrot, "proj-b", st.bo_projekt, "fraga-b", bas)
    _skriv_min_fraga(extern_mapp / "fristaende.json", "fristaende", bas)

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[extern_mapp],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=konto_miljo.adapter,
        projektmapp=projektrot,
        kartverktygsmapp=tmp_path / "kartverktyg",
    )

    # Stubba bakgrundskörning så `POST .../kor` är snabb och registrerar exakt (ref, projekt_id).
    startade: list[tuple[str, str | None]] = []

    def _falsk_start(self: FragaTjanst, ref: str, projekt_id: str | None = None) -> dict[str, Any]:
        startade.append((ref, projekt_id))
        return {
            "korning_id": f"k-{ref}-{len(startade)}",
            "ref": ref,
            "projekt_id": projekt_id,
            "status": "ko",
            "ateranvand": False,
        }

    monkeypatch.setattr(FragaTjanst, "starta_korning", _falsk_start)
    monkeypatch.setattr(
        FragaTjanst, "planera_fraga", lambda self, ref: {"ref": ref, "jobb": [], "sammanfattning": {}}
    )

    anna, bo, sup_w = (
        konto_miljo.webblasare(),
        konto_miljo.webblasare(),
        konto_miljo.webblasare(),
    )
    assert anna.logga_in(konto_miljo.idp, ANNA)[0] == 302
    assert bo.logga_in(konto_miljo.idp, BO)[0] == 302
    assert sup_w.logga_in(konto_miljo.idp, SUPER)[0] == 302

    def _post(w: Webblasare, sokvag: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        kropp = b"" if payload is None else json.dumps(payload).encode("utf-8")
        status, headers, raw = _http(
            app, "POST", sokvag, w, {"Origin": ORIGIN, **w.csrf_headers()}, kropp
        )
        assert not any(k.startswith("access-control-") for k in headers), headers
        return status, json.loads(raw.decode("utf-8"))

    # 1. GET /api/fragor filtrerar strikt på användarens projekt (fristående bara för superadmin).
    st_a, _, raw_a = _http(app, "GET", "/api/fragor", anna)
    assert st_a == 200
    assert {f["ref"] for f in json.loads(raw_a)["fragor"]} == {"fraga-a"}

    st_b, _, raw_b = _http(app, "GET", "/api/fragor", bo)
    assert st_b == 200
    assert {f["ref"] for f in json.loads(raw_b)["fragor"]} == {"fraga-b"}

    st_s, _, raw_s = _http(app, "GET", "/api/fragor", sup_w)
    assert st_s == 200
    assert {f["ref"] for f in json.loads(raw_s)["fragor"]} == {"fristaende"}

    # 2. GET /api/fragor/<ref> och POST .../plan: lasare i ägarprojektet OK, korsprojekt & fristående -> 404.
    assert _http(app, "GET", "/api/fragor/fraga-a", anna)[0] == 200
    assert _post(anna, "/api/fragor/fraga-a/plan")[0] == 200
    assert _http(app, "GET", "/api/fragor/fraga-b", anna)[0] == 404
    assert _post(anna, "/api/fragor/fraga-b/plan")[0] == 404
    assert _http(app, "GET", "/api/fragor/fristaende", anna)[0] == 404
    assert _post(anna, "/api/fragor/fristaende/plan")[0] == 404

    assert _http(app, "GET", "/api/fragor/fraga-b", bo)[0] == 200
    assert _post(bo, "/api/fragor/fraga-b/plan")[0] == 200
    assert _http(app, "GET", "/api/fragor/fraga-a", bo)[0] == 404
    assert _post(bo, "/api/fragor/fraga-a/plan")[0] == 404

    assert _http(app, "GET", "/api/fragor/fristaende", sup_w)[0] == 200
    assert _post(sup_w, "/api/fragor/fristaende/plan")[0] == 200
    assert _http(app, "GET", "/api/fragor/fraga-a", sup_w)[0] == 404
    assert _http(app, "GET", "/api/fragor/..%2Fconfig", anna)[0] == 400

    # 3. POST /api/fragor/<ref>/kor:
    # IDOR: Bo (utredare i B) skickar eget projekt_id för att försöka köra Annas fråga -> 404!
    assert _post(bo, "/api/fragor/fraga-a/kor", {"projekt_id": st.bo_projekt})[0] == 404
    # IDOR: Bo försöker köra fristående fråga genom att skicka eget projekt_id -> 404!
    assert _post(bo, "/api/fragor/fristaende/kor", {"projekt_id": st.bo_projekt})[0] == 404
    # Mismatchande projekt_id på egen fråga -> 403!
    assert _post(bo, "/api/fragor/fraga-b/kor", {"projekt_id": st.anna_projekt})[0] == 403
    # Lasare (Anna) i eget projekt får inte köra -> 403!
    assert _post(anna, "/api/fragor/fraga-a/kor", {"projekt_id": st.anna_projekt})[0] == 403
    assert _post(anna, "/api/fragor/fraga-a/kor")[0] == 403
    assert startade == []

    # Legitim körning: Bo kör sin egen fråga med matchande projekt_id eller utelämnat (binds till B).
    st_ok1, d_ok1 = _post(bo, "/api/fragor/fraga-b/kor", {"projekt_id": st.bo_projekt})
    assert st_ok1 == 202 and d_ok1["projekt_id"] == st.bo_projekt
    st_ok2, d_ok2 = _post(bo, "/api/fragor/fraga-b/kor")
    assert st_ok2 == 202 and d_ok2["projekt_id"] == st.bo_projekt
    # Superadmin kör fristående fråga -> 202 med projekt_id=None.
    st_sup, d_sup = _post(sup_w, "/api/fragor/fristaende/kor")
    assert st_sup == 202 and d_sup["projekt_id"] is None
    assert startade == [
        ("fraga-b", st.bo_projekt),
        ("fraga-b", st.bo_projekt),
        ("fristaende", None),
    ]


def test_w_b1_inloggning_av_bevarar_befintligt_beteende(tmp_path: Path) -> None:
    """W-B1 med `inloggning = 'av'`: fristående och projektfrågor syns och fungerar som förut."""
    bas = REPO / "testsvit" / "A1_enkel_sanka" / "indata"
    extern_mapp = tmp_path / "fristaende"
    projektrot = tmp_path / "projekt"
    _skriv_min_fraga(extern_mapp / "lokal.json", "lokal", bas)
    _skriv_projekt_med_fraga(projektrot, "proj-lokal", "reg-lokal", "proj-fraga", bas)

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[extern_mapp],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=KontoAdapter(KontoKonfig()),
        projektmapp=projektrot,
    )
    st, _, raw = _http(app, "GET", "/api/fragor")
    assert st == 200
    assert {f["ref"] for f in json.loads(raw)["fragor"]} == {"lokal", "proj-fraga"}
    assert _http(app, "GET", "/api/fragor/lokal")[0] == 200
    assert _http(app, "GET", "/api/fragor/proj-fraga")[0] == 200


# ------------------------------------------------------------------ W-B6: ankomsttid i sekunder + LAGER_VERSION


def test_w_b6_ankomsttid_enhet_och_lager_version(tmp_path: Path) -> None:
    """W-B6: `ankomsttid` har enhet `'s'` (inte `'min'`) och `LAGER_VERSION >= 9` ogiltigförklarar v8-cache."""
    assert etikett_och_enhet("ankomsttid") == ("Tid till vattnet kommer", "s")
    assert LAGER_VERSION >= 9

    tjanst = FragaTjanst(
        rot=tmp_path,
        fragemappar=[],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    kmapp = tmp_path / "korningar" / "k-v8"
    (kmapp / "webb").mkdir(parents=True)
    # Skriv en gammal version-8-cache med felaktig enhet "min" och verifiera att den byggs om.
    (kmapp / "webb" / "lager.json").write_text(
        json.dumps({"version": 8, "lager": [{"id": "gammal", "enhet": "min"}], "varningar": []}),
        encoding="utf-8",
    )
    k = Korning("k-v8", "demo", "demo", "demo.toml", kmapp, status="klar")
    s: dict[str, Any] = {"scenarier": {"fore": {}}, "jobb": [], "jamforelser": []}
    lager, _ = tjanst._lager(k, s)  # noqa: SLF001
    assert lager == []
    sparat = json.loads((kmapp / "webb" / "lager.json").read_text(encoding="utf-8"))
    assert sparat["version"] == LAGER_VERSION


# ------------------------------------------------------------------ W-B8: Flerruts sänkor/flödesvägar + läsande ekologi


def _bygg_demoruta(
    varld: DemoVarld,
    ut: Path,
    e_min_m: float,
    n_min_m: float,
    storlek_m: float,
    overlapp_m: float,
) -> Path:
    ruta = Ruta(e_min_m, n_min_m, storlek_m, overlapp_m, varld.upplosning_m)
    e, n = ruta.cell_centra_en()
    ee, nn = np.meshgrid(e, n)
    ut.mkdir(parents=True, exist_ok=True)
    res = bygg_markmodell_ruta(
        ruta,
        ut,
        grund_z=terrang_z(varld, ee, nn).astype(np.float32),
        markslag_grund=markslag_grund(varld, ruta),
        brytlinjer=kantsten(varld),
        byggnadskandidater=byggnader(varld),
        oppningar=portar(varld),
        byggnadshojd_fran_laser=False,
        vegetation_hojd_m=vegetation_hojd(varld, ruta),
        vegetation_upplosning_m=varld.vegetation_upplosning_m,
        kodversion=KODVERSION,
        start_tid=START_TID,
    )
    return Path(res.katalog)


def test_w_b8_flerruts_sankor_och_flodesvagar_ger_200_inte_409(tmp_path: Path) -> None:
    """W-B8: 2-rutors vattenkörning ska kunna leverera `sankor` och `flodesvagar` som GeoJSON (200)."""
    varld = DemoVarld()
    e0, n0, _, _ = varld.fokus_3011
    storlek_m = 150.0
    overlapp_m = 10.0

    bas = tmp_path / "k1_flerruta"
    _bygg_demoruta(varld, bas, e0, n0, storlek_m, overlapp_m)
    _bygg_demoruta(varld, bas, e0 + storlek_m, n0, storlek_m, overlapp_m)

    fragemapp = tmp_path / "fragor"
    _skriv_min_fraga(fragemapp / "flerruta_vatten.json", "flerruta-vatten", bas)

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[fragemapp],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    try:
        st_k, _ = app.fragor.kor_synkront("flerruta_vatten", "k-flerruta-vektor")
        assert st_k["status"] == "klar"

        st_s, _, kropp_s = app.hantera_forfragan(
            "GET",
            "/api/korningar/k-flerruta-vektor/vektor/bas/sankor.json?min_volym_m3=0&min_djup_m=0",
        )
        assert st_s == 200, kropp_s.decode("utf-8")
        sankor = json.loads(kropp_s)
        assert sankor["type"] == "FeatureCollection"
        assert sankor["slag"] == "sankor"
        assert sankor["meta"]["antal_totalt"] >= 1
        assert len(sankor["features"]) >= 1

        st_f, _, kropp_f = app.hantera_forfragan(
            "GET",
            "/api/korningar/k-flerruta-vektor/vektor/bas/flodesvagar.json?min_yta_m2=100",
        )
        assert st_f == 200, kropp_f.decode("utf-8")
        floden = json.loads(kropp_f)
        assert floden["type"] == "FeatureCollection"
        assert floden["slag"] == "flodesvagar"
        assert len(floden["features"]) >= 1
    finally:
        app.stang()


def test_w_b8_ekologi_get_tillater_lasare_utan_projektmutering_och_post_bevarar_b13(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    """W-B8 + B13: `GET /api/analys/ekologi` fungerar för `lasare` utan att skriva i projektmappen,
    medan `POST /api/analys/ekologi` behåller B13-kravet (`utredare`, 403 för `lasare`) och
    utomstående nekas 403 på båda."""
    varld = DemoVarld()
    geo_rot = tmp_path / "geo"
    proj_rot = tmp_path / "projekt"
    k1_tmp = tmp_path / "bygge_k1"
    bygg_ruta_vatten(varld, k1_tmp)
    (k1_ruta,) = sorted(k1_tmp.glob("mm_*"))
    from testsvit.k1_io import las_geotiff

    _, geo_info = las_geotiff(k1_ruta / "mark.tif")
    x0, y0, x1, y1 = (
        float(geo_info.x_min),
        float(geo_info.y_min),
        float(geo_info.x_max),
        float(geo_info.y_max),
    )
    k1_lager = lagra(geo_rot, k1_tmp)
    nmd_tmp = tmp_path / "bygge_nmd"
    nmd_tmp.mkdir(parents=True)
    cell_m = 10.0
    bredd = int(np.ceil((x1 - x0) / cell_m)) + 4
    hojd = int(np.ceil((y1 - y0) / cell_m)) + 4
    klasser = np.full((hojd, bredd), 53, dtype=np.uint8)
    klasser[2 : hojd // 2 - 1, 2 : bredd - 2] = 111
    klasser[hojd // 2 + 1 : hojd - 2, 2 : bredd - 2] = 111
    skriv_nmd(NmdLager(klasser, x0 - 20.0, y1 + 20.0, cell_m), nmd_tmp / "nmd.tif")
    nmd_lager = lagra(geo_rot, nmd_tmp)
    _, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "ekogeo",
            "version": "v1",
            "kommun": "goteborg",
            "karna_3011": [x0 + 10.0, y0 + 10.0, x1 - 10.0, y1 - 10.0],
            "gransen_3011": [x0, y0, x1, y1],
            "lager": {
                "k1_rutnat": {"sha256": k1_lager.sha256},
                "nmd": {"sha256": nmd_lager.sha256, "buffert_m": 200.0, "cell_m": cell_m},
            },
            "kallor": {
                "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                "nmd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
            },
            "oppen_for_mcp": True,
        },
    )

    reg = konto_miljo.register
    org1 = reg.skapa_organisation("Org1")
    org2 = reg.skapa_organisation("Org2")
    anna = reg.forbered_konto("anna@example.test")
    reg.gor_organisationsadmin(anna, org1)
    bo = reg.forbered_konto("bo@example.test")
    reg.lagg_till_medlem(bo, org1, None, "test")
    di = reg.forbered_konto("di@example.test")
    reg.gor_organisationsadmin(di, org2)
    pid, _ = reg.registrera_projekt_for_konto(anna, org1, "Eko-kvarter")
    reg.bjud_in(anna, pid, "bo@example.test", "lasare")

    pmapp = proj_rot / "eko-kvarter"
    fdir = pmapp / "fragor"
    fdir.mkdir(parents=True)
    k1_lagrad = lager_mapp(geo_rot, k1_lager.sha256)
    rel_k1 = Path(os.path.relpath(k1_lagrad.resolve(), fdir.resolve())).as_posix()
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": "eko-kvarter",
                "register_id": pid,
                "namn": "Eko-kvarter",
                "kommun": "goteborg",
                "geografi": "ekogeo@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": [x0 + 20.0, y0 + 20.0, x1 - 20.0, y1 - 20.0],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (fdir / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\nfraga_id = "eko-skyfall"\n'
        f'[scenarier.bas]\nbas = "{rel_k1}"\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 50.0 }\n",
        encoding="utf-8",
    )

    kv_mapp = tmp_path / "kartverktyg"
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=konto_miljo.adapter,
        geomapp=geo_rot,
        projektmapp=proj_rot,
        kartverktygsmapp=kv_mapp,
    )
    w_bo = konto_miljo.webblasare()
    w_di = konto_miljo.webblasare()
    assert w_bo.logga_in(konto_miljo.idp, BO)[0] == 302
    assert w_di.logga_in(konto_miljo.idp, DI)[0] == 302

    filer_fore = sorted(p.relative_to(pmapp).as_posix() for p in pmapp.rglob("*"))
    try:
        # Läsare (Bo) får 200 på GET /api/analys/ekologi utan att mutera projektmappen.
        st_get, headers_get, raw_get = _http(
            app, "GET", f"/api/analys/ekologi?omrade_id={pid}&natverk=barrskog-nacka", w_bo
        )
        assert st_get == 200, raw_get.decode("utf-8")
        assert not any(k.startswith("access-control-") for k in headers_get)
        data_get = json.loads(raw_get)
        assert data_get["format"] == "floden-ekologi/v1"
        assert data_get["fore"]["ECA_m2"] > 0.0
        filer_efter = sorted(p.relative_to(pmapp).as_posix() for p in pmapp.rglob("*"))
        assert filer_efter == filer_fore
        assert not (kv_mapp / pid).exists()

        # Läsare (Bo) nekas fortsatt 403 på POST /api/analys/ekologi (B13).
        st_post_bo, _, _ = _http(
            app,
            "POST",
            "/api/analys/ekologi",
            w_bo,
            {"Origin": ORIGIN, **w_bo.csrf_headers()},
            json.dumps({"omrade_id": pid, "natverk": "barrskog-nacka"}).encode("utf-8"),
        )
        assert st_post_bo == 403

        # Utomstående (Di) nekas 403 på både GET och POST.
        assert (
            _http(app, "GET", f"/api/analys/ekologi?omrade_id={pid}&natverk=barrskog-nacka", w_di)[
                0
            ]
            == 403
        )
        assert (
            _http(
                app,
                "POST",
                "/api/analys/ekologi",
                w_di,
                {"Origin": ORIGIN, **w_di.csrf_headers()},
                json.dumps({"omrade_id": pid}).encode("utf-8"),
            )[0]
            == 403
        )
    finally:
        app.stang()


# ------------------------------------------------------------------ W-B9: doman-mappning & _utan_cors


def test_w_b9_doman_mappning_bevarar_luft_och_utan_cors_pa_felsvar(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    """W-B9: `kartverktyg._lage_vyer`/`_vyer` använder `la['doman']` (mappar inte `luft` till
    `'dagsljus'`) och felsvar i `hantera_http` strippar CORS-rubriker när inloggning är på."""
    res_lage = {
        "lager": [
            {
                "id": "halt.fore.arsmedel",
                "analys": "halt",
                "doman": "luft",
                "scenario": "fore",
                "typ": "bas",
                "primar": True,
                "etikett": "Årsmedelhalt",
            },
            {
                "id": "skyfall.fore.max_djup",
                "analys": "skyfall",
                "doman": "vatten",
                "scenario": "fore",
                "typ": "bas",
                "primar": True,
                "etikett": "Största vattendjup",
            },
        ]
    }
    vyer_lage = KartverktygTjanst._lage_vyer(res_lage, "k-luft-1")  # noqa: SLF001
    assert [v["avsnitt"] for v in vyer_lage] == ["luft", "vatten"]

    res_diff = {
        "lager": [
            {
                "id": "halt.fore.arsmedel",
                "analys": "halt",
                "doman": "luft",
                "scenario": "fore",
                "typ": "bas",
                "primar": True,
                "roll": "arsmedel",
            },
            {
                "id": "halt.ritat.arsmedel",
                "analys": "halt",
                "doman": "luft",
                "scenario": "ritat",
                "typ": "scenario",
                "primar": True,
                "roll": "arsmedel",
            },
            {
                "id": "skillnad.halt",
                "analys": "halt",
                "doman": "luft",
                "scenario": "ritat",
                "typ": "skillnad",
                "primar": True,
                "roll": "arsmedel",
                "fore": "fore",
                "efter": "ritat",
                "etikett": "Årsmedelhalt",
            },
        ]
    }
    vyer_diff = KartverktygTjanst._vyer(res_diff, "Nytt förslag", "k-luft-2")  # noqa: SLF001
    assert len(vyer_diff) == 1 and vyer_diff[0]["avsnitt"] == "luft"

    # CORS strippas på 403 för /api/prov/* och 413 från las_kropp() när inloggning är aktiv.
    _setup(konto_miljo)
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=konto_miljo.adapter,
    )
    w_anna = konto_miljo.webblasare()
    assert w_anna.logga_in(konto_miljo.idp, ANNA)[0] == 302
    st_prov, h_prov, _ = _http(app, "GET", "/api/prov/status", w_anna)
    assert st_prov == 403
    assert not any(k.startswith("access-control-") for k in h_prov), h_prov

    def _for_stor() -> bytes:
        raise FlodenWebbFel("För stor kropp.", status_kod=413)

    rub = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in w_anna.kakor.items()),
        "Origin": ORIGIN,
        **w_anna.csrf_headers(),
    }
    st_413, hs_413, _ = app.hantera_http("POST", "/api/kor", rub, _for_stor)
    assert st_413 == 413
    assert not any(k.lower().startswith("access-control-") for k, _ in hs_413), hs_413


# ------------------------------------------------------------------ D-B2: FLODEN_LOKAL_CACHE_MAPP


def test_d_b2_api_respekterar_floden_lokal_cache_mapp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-B2: `FlodenApiApp.forberaknad_lager` skickar `cache_rot=None, bas_rot=rot` så
    `FLODEN_LOKAL_CACHE_MAPP` (både absolut och relativ) och `[lagring.lokal_cache]` respekteras."""
    import geografi.forberaknad_lager as fl_mod

    s3_rot = tmp_path / "fake_s3"
    s3_rot.mkdir()
    monkeypatch.setattr(fl_mod, "skapa_lagring", lambda lk, **kw: LokalLagring(s3_rot))
    monkeypatch.setenv("FLODEN_S3_TYP", "s3")
    monkeypatch.setenv("FLODEN_S3_BUCKET", "test-bucket")
    monkeypatch.setenv("FLODEN_S3_ENDPOINT", "https://s3.example.test")

    # 1. Absolut FLODEN_LOKAL_CACHE_MAPP
    egen_abs = tmp_path / "min_lru_cache"
    monkeypatch.setenv("FLODEN_LOKAL_CACHE_MAPP", str(egen_abs))
    app1 = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "config.toml",
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    lager1 = app1.forberaknad_lager
    assert lager1 is not None
    assert lager1.cache_rot == egen_abs

    # 2. Relativ FLODEN_LOKAL_CACHE_MAPP löses upp mot konfigurationsfilens katalog (`bas_rot=rot`)
    monkeypatch.setenv("FLODEN_LOKAL_CACHE_MAPP", "custom_cache/s3")
    app2 = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "config.toml",
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    lager2 = app2.forberaknad_lager
    assert lager2 is not None
    assert lager2.cache_rot == tmp_path.resolve() / "custom_cache" / "s3"


# ------------------------------------------------------------------ D-B4: ASGI disconnect, stang & /api/projekt


def test_d_b4_asgi_lifespan_stanger_api_app_och_rapportjobb(tmp_path: Path) -> None:
    """D-B4: `lifespan.shutdown` anropar `FlodenApiApp.stang()` som stänger både `FragaTjanst`
    och `RapportJobbTjanst.stang()`."""
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    _ = app.fragor  # initiera FragaTjanst

    rapport_stangd = threading.Event()

    class _FakeRapportJobb:
        def stang(self) -> None:
            rapport_stangd.set()

    app._rapportjobb = _FakeRapportJobb()  # type: ignore[assignment]  # noqa: SLF001
    asgi_app = FlodenAsgiApp(app)

    async def _kor() -> None:
        msgs = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        ut: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return next(msgs)

        async def send(m: Mapping[str, Any]) -> None:
            ut.append(dict(m))

        await asgi_app({"type": "lifespan"}, receive, send)
        assert ut == [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ]

    asyncio.run(_kor())
    assert rapport_stangd.is_set()
    assert app.fragor._stangd is True  # noqa: SLF001


# ------------------------------------------------------------------ D-B5: TOCTOU, kötak/503, ko_plats, per-körningslås, atomisk skrivning & låsfri /api/halsa


def test_d_b5_toctou_samtidiga_starta_korning_ger_bara_en_korning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-B5 TOCTOU: två samtidiga `starta_korning` för samma fråga som passerar första kollen
    parallellt och synkroniseras i `_planera` ska ge exakt en körning (`ateranvand=True` på den andra)."""
    tjanst = FragaTjanst(
        rot=tmp_path,
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    barriar = threading.Barrier(2, timeout=5.0)
    slapp = threading.Event()
    kor_antal = 0
    kor_lock = threading.Lock()

    monkeypatch.setattr(
        FragaTjanst, "_las", lambda self, ref: (tmp_path / f"{ref}.json", SimpleNamespace(fraga_id=ref))
    )

    def _planera_synk(self: FragaTjanst, fraga: Any) -> Any:
        barriar.wait()
        return SimpleNamespace(jobb=[])

    def _kor_block(self: FragaTjanst, k: Korning, fraga: Any, plan: Any) -> None:
        nonlocal kor_antal
        with kor_lock:
            kor_antal += 1
        with self._lock:  # noqa: SLF001
            k.status = "kor"
            self._skriv_status(k)  # noqa: SLF001
        slapp.wait(timeout=5.0)
        with self._lock:  # noqa: SLF001
            k.status = "klar"
            self._skriv_status(k)  # noqa: SLF001

    monkeypatch.setattr(FragaTjanst, "_planera", _planera_synk)
    monkeypatch.setattr(FragaTjanst, "_kor", _kor_block)

    resultat: list[dict[str, Any]] = []
    fel: list[Exception] = []

    def _anropa() -> None:
        try:
            resultat.append(tjanst.starta_korning("samma-fraga", "proj-1"))
        except Exception as exc:  # noqa: BLE001
            fel.append(exc)

    try:
        t1 = threading.Thread(target=_anropa)
        t2 = threading.Thread(target=_anropa)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        assert fel == []
        assert len(resultat) == 2
        assert {r["korning_id"] for r in resultat} == {resultat[0]["korning_id"]}
        assert sorted(bool(r["ateranvand"]) for r in resultat) == [False, True]
    finally:
        slapp.set()
        tjanst.stang()
    assert kor_antal == 1


def test_d_b5_kotak_ger_503_med_retry_after_och_ko_plats_samt_lasfri_halsa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-B5: `max_ko=1` tillåter 1 aktiv + 1 köad med `ko_plats`/`ko_langd`, avvisar jobb 3 med
    HTTP 503 + `Retry-After`, och `GET /api/halsa` är låsfri med snapshotad köstatus."""
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    # Ersätt med max_ko=1 för snabb deterministisk kötest.
    app._fragor = FragaTjanst(  # noqa: SLF001
        rot=tmp_path,
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        max_ko=1,
    )
    forsta_startad = threading.Event()
    slapp_forsta = threading.Event()

    monkeypatch.setattr(
        FragaTjanst, "_las", lambda self, ref: (tmp_path / f"{ref}.json", SimpleNamespace(fraga_id=ref))
    )
    monkeypatch.setattr(FragaTjanst, "_planera", lambda self, fraga: SimpleNamespace(jobb=[]))

    def _kontrollerad_kor(self: FragaTjanst, k: Korning, fraga: Any, plan: Any) -> None:
        with self._lock:  # noqa: SLF001
            k.status = "kor"
            self._skriv_status(k)  # noqa: SLF001
        if k.ref == "jobb-1":
            forsta_startad.set()
            slapp_forsta.wait(timeout=5.0)
        with self._lock:  # noqa: SLF001
            k.status = "klar"
            self._skriv_status(k)  # noqa: SLF001

    monkeypatch.setattr(FragaTjanst, "_kor", _kontrollerad_kor)

    try:
        # Jobb 1 startar direkt i arbetaren.
        st1, _, raw1 = app.hantera_forfragan("POST", "/api/fragor/jobb-1/kor")
        assert st1 == 202, raw1
        assert forsta_startad.wait(timeout=5.0)

        # Jobb 2 hamnar i kön på plats 1 av 1.
        st2, _, raw2 = app.hantera_forfragan("POST", "/api/fragor/jobb-2/kor")
        assert st2 == 202, raw2
        d2 = json.loads(raw2)
        assert d2["status"] == "ko"
        assert d2["ko_plats"] == 1
        assert d2["ko_langd"] == 1

        st2_get, _, raw2_get = app.hantera_forfragan("GET", f"/api/korningar/{d2['korning_id']}")
        assert st2_get == 200
        d2_get = json.loads(raw2_get)
        assert d2_get["ko_plats"] == 1 and d2_get["ko_langd"] == 1

        # Jobb 3 överskrider max_ko=1 -> HTTP 503 + Retry-After!
        st3, h3, raw3 = app.hantera_forfragan("POST", "/api/fragor/jobb-3/kor")
        assert st3 == 503, raw3
        assert h3.get("Retry-After") == "5", h3
        assert json.loads(raw3)["status"] == 503

        # Låsfri GET /api/halsa: ska svara direkt även när både app._lock och fragor._lock hålls!
        las_halls = threading.Event()
        slapp_las = threading.Event()

        fragor = app.fragor

        def _hall_las() -> None:
            with app._lock, fragor._lock:  # noqa: SLF001
                las_halls.set()
                slapp_las.wait(timeout=5.0)

        las_trad = threading.Thread(target=_hall_las)
        las_trad.start()
        assert las_halls.wait(timeout=5.0)
        try:
            t0 = time.monotonic()
            halsa = app.halsa()
            dt = time.monotonic() - t0
            assert dt < 2.0, f"GET /api/halsa blockerade i {dt:.2f}s"
            assert halsa["status"] == "ok"
            assert halsa["ko"] == {"koade": 1, "pagaende": 1, "max_ko": 1}
        finally:
            slapp_las.set()
            las_trad.join(timeout=5.0)
    finally:
        slapp_forsta.set()
        app.stang()

    # Efter stang() avvisas nya körningar med KoFullFel (503).
    with pytest.raises(KoFullFel) as exc_stangd:
        app.fragor.starta_korning("jobb-4")
    assert exc_stangd.value.status_kod == 503


def test_d_b5_per_korningslas_blockerar_inte_andra_korningar_och_atomisk_skrivning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-B5: Tung rendering i `_bygg_lager` för körning A blockerar inte `korning_status(B)`,
    och atomisk skrivning av `korning.json` ger 0 trasiga läsningar och 0 kvarlämnade `.tmp`-filer."""
    tjanst = FragaTjanst(
        rot=tmp_path,
        fragemappar=[],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    k_a = Korning("k-a", "a", "a", "a.toml", tmp_path / "korningar" / "k-a", status="klar")
    k_b = Korning("k-b", "b", "b", "b.toml", tmp_path / "korningar" / "k-b", status="klar")
    tjanst._lasta_korningar()  # noqa: SLF001
    tjanst._korningar["k-a"] = k_a  # noqa: SLF001
    tjanst._korningar["k-b"] = k_b  # noqa: SLF001
    tjanst._skriv_status(k_a)  # noqa: SLF001
    tjanst._skriv_status(k_b)  # noqa: SLF001

    i_rendering = threading.Event()
    slapp_rendering = threading.Event()

    def _langsam_bygg_lager(self: FragaTjanst, k: Korning, s: dict[str, Any]) -> tuple[list[Any], list[str]]:
        i_rendering.set()
        slapp_rendering.wait(timeout=5.0)
        return [], []

    monkeypatch.setattr(FragaTjanst, "_bygg_lager", _langsam_bygg_lager)

    rend_trad = threading.Thread(
        target=lambda: tjanst._lager(k_a, {"scenarier": {}, "jobb": [], "jamforelser": []})  # noqa: SLF001
    )
    rend_trad.start()
    try:
        assert i_rendering.wait(timeout=5.0)
        t0 = time.monotonic()
        st_b = tjanst.korning_status("k-b")
        dt = time.monotonic() - t0
        assert st_b["korning_id"] == "k-b" and st_b["status"] == "klar"
        assert dt < 2.0, f"korning_status(k-b) blockerades av k-a rendering ({dt:.2f}s)"
    finally:
        slapp_rendering.set()
        rend_trad.join(timeout=5.0)

    # Atomisk skrivning: kontinuerlig läsning under upprepade skrivningar ger 0 JSONDecodeError.
    stopp = threading.Event()
    lasfel: list[Exception] = []
    fil = k_b.mapp / "korning.json"

    def _lasare() -> None:
        while not stopp.is_set():
            try:
                d = json.loads(fil.read_text(encoding="utf-8"))
                assert d["korning_id"] == "k-b"
            except Exception as exc:  # noqa: BLE001
                lasfel.append(exc)
                return

    r_trad = threading.Thread(target=_lasare)
    r_trad.start()
    try:
        for i in range(60):
            k_b.jobb_totalt = i
            tjanst._skriv_status(k_b)  # noqa: SLF001
    finally:
        stopp.set()
        r_trad.join(timeout=5.0)
        tjanst.stang()

    assert lasfel == []
    assert list(k_b.mapp.glob("*.tmp")) == []


# ------------------------------------------------------------------ D-B6: --server trad PUT/DELETE, start.sh, Compose


def test_d_b6_trad_server_stoder_put_och_delete_utan_501(tmp_path: Path) -> None:
    """D-B6: `--server trad` (`skapa_http_server`) ska ha `do_PUT` och `do_DELETE` (inte HTTP 501)."""
    server = skapa_http_server(
        "127.0.0.1",
        0,
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "saknas.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    port = int(server.server_address[1])
    trad = threading.Thread(target=server.serve_forever, daemon=True)
    trad.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.request("PUT", "/api/projekt/demo/namn", body=b'{"namn":"Nytt"}', headers={"Content-Length": "15"})
        svar_put = conn.getresponse()
        _ = svar_put.read()
        assert svar_put.status != 501, f"PUT gav 501 i trad-läge: {svar_put.status}"

        conn.request("DELETE", "/konto/nycklar/test-id")
        svar_del = conn.getresponse()
        _ = svar_del.read()
        assert svar_del.status != 501, f"DELETE gav 501 i trad-läge: {svar_del.status}"
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        trad.join(timeout=5.0)


def test_d_b6_start_sh_och_docker_compose_prod() -> None:
    """D-B6: `deploy/start.sh` testar `demo.json` (inte bara mappen) och `docker-compose.prod.yml`
    vidarebefordrar `FLODEN_S3_PREFIX`, `FLODEN_LOKAL_CACHE_MAPP` och `FLODEN_LOKAL_CACHE_MAX_GB`."""
    start_sh = (REPO / "deploy" / "start.sh").read_text(encoding="utf-8")
    assert "[ ! -f data/demo/varld/demo.json ]" in start_sh
    assert "[ ! -d data/demo/varld ]" not in start_sh

    compose = (REPO / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for var in ("FLODEN_S3_PREFIX", "FLODEN_LOKAL_CACHE_MAPP", "FLODEN_LOKAL_CACHE_MAX_GB"):
        assert f"{var}: ${{{var}:-}}" in compose, f"{var} saknas i docker-compose.prod.yml"
