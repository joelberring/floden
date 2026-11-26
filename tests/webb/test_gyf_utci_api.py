# ruff: noqa: E501, F811
"""Deterministiska API-, fysik-, RBAC- och B11-tester för Svensk GYF (U16) och Värmestress (U15)."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from demovarld.varld import DemoVarld, bygg_ruta_vatten
from gemensamt.hash import sha256_fil
from geografi.modell import KONTRAKT_GEOGRAFI, lagra, skriv_manifest
from karna.scenario import Andring, Planscenario, skriv_scenario
from tests.konto.conftest import ANNA, ORIGIN, Miljo, Webblasare
from tests.konto.conftest import miljo as konto_miljo  # noqa: F401
from testsvit.k1_io import las_geotiff, skriv_geotiff
from webb.api import FlodenApiApp
from webb.asgi import FlodenAsgiApp
from webb.fragor import LAGER_VERSION
from webb.projektkalla import KONTRAKT_PROJEKT
from webb.resultatlager import (
    MASKADE_ROLLER,
    PALETTER,
    etikett_och_enhet,
    farg_for_roll,
    takkalla_for_roll,
)

BO = {"sub": "sub-bo-v7", "email": "bo@example.test", "email_verified": True, "name": "Bo Läsare"}
DI = {"sub": "sub-di-v7", "email": "di@example.test", "email_verified": True, "name": "Di Utomstående"}


def _get_json(app: FlodenApiApp, sokvag: str) -> tuple[int, dict[str, Any]]:
    status, _, kropp = app.hantera_forfragan("GET", sokvag, b"")
    return status, json.loads(kropp.decode("utf-8"))


def _post_json(
    app: FlodenApiApp, sokvag: str, payload: Mapping[str, Any]
) -> tuple[int, dict[str, Any]]:
    status, _, kropp = app.hantera_forfragan(
        "POST", sokvag, json.dumps(payload).encode("utf-8")
    )
    return status, json.loads(kropp.decode("utf-8"))


def _bygg_demo_app(rot: Path) -> tuple[FlodenApiApp, Path]:
    """Lokal, nätverksfri FlodenApiApp med Demovärldens K1 v2-ruta (300×300 m, 1 m-celler)."""
    varld = DemoVarld()
    bas = bygg_ruta_vatten(varld, rot / "varld" / "vatten" / "bas")
    (ruta,) = sorted(bas.glob("mm_*"))
    konfig = rot / "config.toml"
    konfig.write_text(
        '[webb]\ndemomapp = "varld"\nfragemappar = ["fragor"]\ncachemapp = "cache"\n'
        'korningsmapp = "korningar"\nklientmapp = "klient"\n',
        encoding="utf-8",
    )
    tom_testsvit = rot / "tom-testsvit"
    tom_testsvit.mkdir(exist_ok=True)
    app = FlodenApiApp(testsvit_rot=tom_testsvit, konfig_fil=konfig)
    return app, ruta


def _skriv_demo_scenario(
    app: FlodenApiApp,
    slug: str,
    namn: str,
    andringar: list[dict[str, Any]],
) -> None:
    """Skriver ett sparat kartverktygsscenario för `demokvarteret` utan att kräva full `floden demo`."""
    mapp = app.kartverktyg.mapp / "demokvarteret" / "scenarier"
    mapp.mkdir(parents=True, exist_ok=True)
    dok = {
        "format": "floden-kartverktyg/v1",
        "namn": namn,
        "slug": slug,
        "projekt": "demokvarteret",
        "skapad": "2026-11-26T12:00:00+00:00",
        "andrad": "2026-11-26T12:00:00+00:00",
        "beskrivning": namn,
        "andringar": andringar,
        "korningar": {},
    }
    (mapp / f"{slug}.json").write_text(json.dumps(dok, indent=2, ensure_ascii=False), encoding="utf-8")


class _SyntetiskGeoV7:
    """Helt lokal syntetisk geografi + registrerat projekt med konton (Anna=utredare, Bo=lasare, Di=outsider)."""

    def __init__(self, rot: Path, miljo: Miljo) -> None:
        self.rot = rot
        self.miljo = miljo
        self.reg = miljo.register
        self.geo_rot = rot / "geo"
        self.proj_rot = rot / "projekt"

        varld = DemoVarld()
        k1_tmp = rot / "bygge_k1"
        bygg_ruta_vatten(varld, k1_tmp)
        (k1_ruta,) = sorted(k1_tmp.glob("mm_*"))
        _, geo_info = las_geotiff(k1_ruta / "mark.tif")
        self.x0, self.y0 = float(geo_info.x_min), float(geo_info.y_min)
        self.x1, self.y1 = float(geo_info.x_max), float(geo_info.y_max)

        k1_lager = lagra(self.geo_rot, k1_tmp)
        _, manifest_sha = skriv_manifest(
            self.geo_rot,
            {
                "kontrakt": KONTRAKT_GEOGRAFI,
                "id": "syntgeo-v7",
                "version": "v1",
                "kommun": "stockholm",
                "karna_3011": [self.x0 + 10.0, self.y0 + 10.0, self.x1 - 10.0, self.y1 - 10.0],
                "gransen_3011": [self.x0, self.y0, self.x1, self.y1],
                "lager": {
                    "k1_rutnat": {"sha256": k1_lager.sha256},
                },
                "kallor": {
                    "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                    "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                },
                "oppen_for_mcp": True,
            },
        )

        org1 = self.reg.skapa_organisation("Exempelkommun")
        org2 = self.reg.skapa_organisation("Grannkommun")
        anna = self.reg.forbered_konto("anna@example.test")
        self.reg.gor_organisationsadmin(anna, org1)
        bo = self.reg.forbered_konto("bo@example.test")
        self.reg.lagg_till_medlem(bo, org1, None, "test")
        di = self.reg.forbered_konto("di@example.test")
        self.reg.gor_organisationsadmin(di, org2)

        self.projekt_id, _ = self.reg.registrera_projekt_for_konto(anna, org1, "Kvarteret V7")
        self.reg.bjud_in(anna, self.projekt_id, "bo@example.test", "lasare")

        self.pmapp = self.proj_rot / "kvarteret-v7"
        k1_vatten = self.pmapp / "k1_vatten"
        bygg_ruta_vatten(varld, k1_vatten)
        (v_ruta,) = sorted(k1_vatten.glob("mm_*"))
        (self.pmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
            encoding="utf-8",
        )
        (self.pmapp / "projekt.json").write_text(
            json.dumps(
                {
                    "kontrakt": KONTRAKT_PROJEKT,
                    "projekt_id": "kvarteret-v7",
                    "register_id": self.projekt_id,
                    "namn": "Kvarteret V7",
                    "kommun": "stockholm",
                    "geografi": "syntgeo-v7@v1",
                    "geografi_manifest_sha256": manifest_sha,
                    "avgransning_3011": [self.x0 + 40.0, self.y0 + 40.0, self.x1 - 40.0, self.y1 - 40.0],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Scenario 1: nytt högt hus på öppen grönyta (sänker GYF, kastar ny skugga som kyler gården)
        hx0, hy0 = self.x0 + 80.0, self.y0 + 80.0
        k9_hus = self.pmapp / "k9" / "nytt-hus_vatten.json"
        skriv_scenario(
            Planscenario(
                "nytt-hus",
                (sha256_fil(v_ruta / "recept.json"),),
                (
                    Andring.fran_dict(
                        {
                            "typ": "byggnad_ny",
                            "andring_id": "hus-v7",
                            "geometri": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [hx0, hy0],
                                        [hx0 + 30.0, hy0],
                                        [hx0 + 30.0, hy0 + 20.0],
                                        [hx0, hy0 + 20.0],
                                        [hx0, hy0],
                                    ]
                                ],
                            },
                            "egenskaper": {"hojd_m": 20.0},
                        }
                    ),
                ),
                beskrivning="Nytt hus i V7-projektet",
            ),
            k9_hus,
        )

        # Scenario 2: ny grönyta + trädplantering på hårdgjord yta (höjer GYF, kyler UTCI/T_mrt)
        gx0, gy0 = self.x0 + 110.0, self.y0 + 110.0
        k9_gron = self.pmapp / "k9" / "gronyta-trad_vatten.json"
        skriv_scenario(
            Planscenario(
                "gronyta-trad",
                (sha256_fil(v_ruta / "recept.json"),),
                (
                    Andring.fran_dict(
                        {
                            "typ": "markslag",
                            "andring_id": "gronyta-1",
                            "geometri": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [gx0, gy0],
                                        [gx0 + 60.0, gy0],
                                        [gx0 + 60.0, gy0 + 60.0],
                                        [gx0, gy0 + 60.0],
                                        [gx0, gy0],
                                    ]
                                ],
                            },
                            "egenskaper": {"kod": 4},
                        }
                    ),
                    Andring.fran_dict(
                        {
                            "typ": "vegetation",
                            "andring_id": "trad-1",
                            "geometri": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [gx0 + 5.0, gy0 + 5.0],
                                        [gx0 + 55.0, gy0 + 5.0],
                                        [gx0 + 55.0, gy0 + 55.0],
                                        [gx0 + 5.0, gy0 + 55.0],
                                        [gx0 + 5.0, gy0 + 5.0],
                                    ]
                                ],
                            },
                            "egenskaper": {"hojd_m": 10.0},
                        }
                    ),
                ),
                beskrivning="Ny grönyta och trädkronor",
            ),
            k9_gron,
        )

        (self.pmapp / "scenarier.json").write_text(
            json.dumps(
                {
                    "kontrakt": "floden-scenarier/v1",
                    "scenarier": [
                        {
                            "slug": "nytt-hus",
                            "namn": "Nytt hus",
                            "status": "forslag",
                            "k9_vatten": "k9/nytt-hus_vatten.json",
                        },
                        {
                            "slug": "gronyta-trad",
                            "namn": "Grönyta och träd",
                            "status": "forslag",
                            "k9_vatten": "k9/gronyta-trad_vatten.json",
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        fdir = self.pmapp / "fragor"
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "skyfall.toml").write_text(
            'kontrakt = "K20/v1"\n'
            'fraga_id = "kvarteret-v7-skyfall"\n'
            'beskrivning = "Skyfall Kvarteret V7"\n\n'
            '[scenarier.bas]\nbas = "../k1_vatten"\n\n'
            '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
            "parametrar = { regn_mm = 40.0 }\n",
            encoding="utf-8",
        )

        self.webb: dict[str, Webblasare] = {}
        for namn, claims in (("anna", ANNA), ("bo", BO), ("di", DI)):
            w = miljo.webblasare()
            assert w.logga_in(miljo.idp, claims)[0] == 302
            self.webb[namn] = w

        self.konfig = rot / "config.toml"
        self.konfig.write_text(
            '[webb]\nfragemappar = ["fragor"]\ncachemapp = "cache"\nkorningsmapp = "korningar"\n',
            encoding="utf-8",
        )
        tom_testsvit = rot / "tom-testsvit"
        tom_testsvit.mkdir(exist_ok=True)
        self.app = FlodenApiApp(
            testsvit_rot=tom_testsvit,
            konfig_fil=self.konfig,
            konto=miljo.adapter,
            geomapp=self.geo_rot,
            projektmapp=self.proj_rot,
            kartverktygsmapp=rot / "kartverktyg",
        )

    def http_raw(
        self, vem: str | None, metod: str, sokvag: str, kropp: Any = None
    ) -> tuple[int, dict[str, str], bytes]:
        rub: dict[str, str] = {}
        if vem is not None:
            w = self.webb[vem]
            rub["Cookie"] = "; ".join(f"{k}={v}" for k, v in w.kakor.items())
            if metod != "GET":
                rub["Origin"] = ORIGIN
                rub.update(w.csrf_headers())
        data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
        return self.app.hantera_http(metod, sokvag, rub, lambda: data)

    def http(
        self, vem: str | None, metod: str, sokvag: str, kropp: Any = None
    ) -> tuple[int, dict[str, Any]]:
        status, _, svar = self.http_raw(vem, metod, sokvag, kropp)
        return status, json.loads(svar.decode("utf-8")) if svar else {}

    def projektfiler_snapshot(self) -> dict[str, tuple[int, int]]:
        ut: dict[str, tuple[int, int]] = {}
        for p in sorted(self.pmapp.rglob("*")):
            if p.is_file():
                st = p.stat()
                ut[p.relative_to(self.pmapp).as_posix()] = (st.st_size, st.st_mtime_ns)
        return ut


# ------------------------------------------------------------------ 1. GYF API på demokvarteret


def test_gyf_get_och_post_pa_demokvarteret_alla_profiler_och_validering(tmp_path: Path) -> None:
    app, _ = _bygg_demo_app(tmp_path)

    st_g, data_sthlm = _get_json(app, "/api/analys/gyf?omrade_id=demokvarteret&profil=stockholm")
    assert st_g == 200, data_sthlm
    assert data_sthlm["format"] == "floden-gyf-webb/v1"
    assert data_sthlm["metodstatus"] == "skiss"
    assert data_sthlm["profil"] == "stockholm"
    assert data_sthlm["riktmarke"] == pytest.approx(0.60)
    assert data_sthlm["efter"] is None
    assert data_sthlm["jamforelse"] is None
    assert data_sthlm["fore"]["gyf"] > 0.0
    assert data_sthlm["fore"]["tomt_area_m2"] == pytest.approx(300.0 * 300.0)
    assert len(data_sthlm["delfaktorer"]) >= 8
    assert isinstance(data_sthlm["flaggad_area_m2"], float)
    assert isinstance(data_sthlm["varningar"], list)

    # POST med samma parametrar ger bitidentiskt resultat
    st_p, data_post = _post_json(
        app, "/api/analys/gyf", {"omrade_id": "demokvarteret", "profil": "stockholm"}
    )
    assert st_p == 200, data_post
    assert data_post == data_sthlm

    # Göteborg och Malmö fungerar och ger profilberoende GYF
    st_gbg, data_gbg = _get_json(app, "/api/analys/gyf?omrade_id=demokvarteret&profil=goteborg")
    st_mal, data_mal = _get_json(app, "/api/analys/gyf?omrade_id=demokvarteret&profil=malmo")
    assert st_gbg == 200 and st_mal == 200
    assert data_gbg["metodstatus"] == "skiss" and data_mal["metodstatus"] == "skiss"
    assert data_gbg["fore"]["gyf"] != pytest.approx(data_sthlm["fore"]["gyf"])

    # Felhantering: okänd profil -> 400, saknat omrade_id -> 400, okänt projekt -> 404, okänt scenario -> 404
    assert _get_json(app, "/api/analys/gyf?omrade_id=demokvarteret&profil=uppsala")[0] == 400
    assert _get_json(app, "/api/analys/gyf")[0] == 400
    assert _get_json(app, "/api/analys/gyf?omrade_id=finns-inte")[0] == 404
    assert _get_json(app, "/api/analys/gyf?omrade_id=demokvarteret&scenario=saknas")[0] == 404


def test_gyf_fore_efter_med_ny_byggnad_och_gronyta_pa_demokvarteret(tmp_path: Path) -> None:
    app, ruta = _bygg_demo_app(tmp_path)
    _, geo = las_geotiff(ruta / "mark.tif")
    slag, _ = las_geotiff(ruta / "markslag.tif")
    x0, y_max, px = float(geo.x_min), float(geo.y_max), float(geo.pixelstorlek_x)

    # Hitta en gräs-/skogscell för ny byggnad och en hårdgjord cell för ny grönyta + träd
    gron_r, gron_c = np.argwhere(slag == 4)[0]
    hard_r, hard_c = np.argwhere(slag == 1)[0]
    gx = x0 + float(gron_c) * px
    gy = y_max - float(gron_r) * px
    hx = x0 + float(hard_c) * px
    hy = y_max - float(hard_r) * px

    _skriv_demo_scenario(
        app,
        "nytt-hus",
        "Nytt hus på gräs",
        [
            {
                "typ": "byggnad_ny",
                "andring_id": "hus-demo",
                "geometri": {
                    "type": "Polygon",
                    "coordinates": [
                        [[gx - 10, gy - 10], [gx + 10, gy - 10], [gx + 10, gy + 10], [gx - 10, gy + 10], [gx - 10, gy - 10]]
                    ],
                },
                "egenskaper": {"hojd_m": 14.0},
            }
        ],
    )
    _skriv_demo_scenario(
        app,
        "gron-atgard",
        "Ny grönyta + träd på asfalt",
        [
            {
                "typ": "markslag",
                "andring_id": "gras-demo",
                "geometri": {
                    "type": "Polygon",
                    "coordinates": [
                        [[hx - 15, hy - 15], [hx + 15, hy - 15], [hx + 15, hy + 15], [hx - 15, hy + 15], [hx - 15, hy - 15]]
                    ],
                },
                "egenskaper": {"kod": 4},
            },
            {
                "typ": "vegetation",
                "andring_id": "trad-demo",
                "geometri": {
                    "type": "Polygon",
                    "coordinates": [
                        [[hx - 10, hy - 10], [hx + 10, hy - 10], [hx + 10, hy + 10], [hx - 10, hy + 10], [hx - 10, hy - 10]]
                    ],
                },
                "egenskaper": {"hojd_m": 9.0},
            },
        ],
    )

    st_hus, s_hus = _post_json(
        app,
        "/api/analys/gyf",
        {"omrade_id": "demokvarteret", "profil": "stockholm", "scenario": "nytt-hus"},
    )
    assert st_hus == 200, s_hus
    assert s_hus["efter"] is not None and s_hus["jamforelse"] is not None
    assert s_hus["jamforelse"]["delta_gyf"] < 0.0
    assert isinstance(s_hus["jamforelse"]["uppfyller_mal"], bool)
    assert s_hus["jamforelse"]["saknas_till_mal_m2"] >= 0.0

    st_gron, s_gron = _get_json(
        app, "/api/analys/gyf?omrade_id=demokvarteret&profil=stockholm&scenario=gron-atgard"
    )
    assert st_gron == 200, s_gron
    assert s_gron["jamforelse"]["delta_gyf"] > 0.0
    assert any(t["tillagg_id"] == "trad_krontackning" and t["area_m2"] > 0 for t in s_gron["tillagg"])


# ------------------------------------------------------------------ 2. Värmestress API (verklig SVF/skugga + raster)


def test_varmestress_verklig_svf_skugga_och_takmaskad_png_pa_demokvarteret(tmp_path: Path) -> None:
    app, ruta = _bygg_demo_app(tmp_path)

    st_g, data = _get_json(app, "/api/analys/varmestress?omrade_id=demokvarteret")
    assert st_g == 200, data
    assert data["format"] == "floden-varmestress-webb/v1"
    assert data["metodstatus"] == "skiss"
    # Verklig geometri-SVF och skuggmask (inte konstant 1.0 eller 0.5!)
    svf_medel = data["geometri"]["svf_medel_fore"]
    sol_pct = data["geometri"]["solbelyst_andel_fore_pct"]
    assert 0.3 < svf_medel < 0.98
    assert 5.0 < sol_pct < 99.0
    # Fysikaliskt rimlig UTCI och T_mrt vid 30 °C solig sommardag
    assert data["utci_medel_c"] is not None and data["utci_max_c"] is not None
    assert data["tmrt_medel_c"] is not None and data["tmrt_max_c"] is not None
    assert data["utci_max_c"] >= data["utci_medel_c"] > 28.0
    assert data["tmrt_max_c"] >= data["tmrt_medel_c"] > 35.0
    assert data["utanfor_giltighetsomrade_pct"] == pytest.approx(0.0)
    assert len(data["stressklasser"]) == 10
    assert sum(k["andel_pct"] for k in data["stressklasser"]) == pytest.approx(100.0, abs=1e-4)

    # Sol under horisonten (solhojd_grad = 0) ger kraftigt lägre T_mrt och UTCI
    st_natt, natt = _post_json(
        app,
        "/api/analys/varmestress",
        {
            "omrade_id": "demokvarteret",
            "t_luft_c": 30.0,
            "relativ_fuktighet_pct": 45.0,
            "vind_10m_m_s": 1.5,
            "sol_direkt_w_m2": 750.0,
            "sol_diffus_w_m2": 120.0,
            "solhojd_grad": 0.0,
        },
    )
    assert st_natt == 200, natt
    assert natt["geometri"]["solbelyst_andel_fore_pct"] == pytest.approx(0.0)
    assert natt["tmrt_medel_c"] < data["tmrt_medel_c"] - 10.0
    assert natt["utci_medel_c"] < data["utci_medel_c"] - 3.0

    # Vind utanför Bröde (2012) giltighetsintervall [0.5, 17.0] m/s flaggas utan tyst avklippning
    st_utanfor, utanfor = _post_json(
        app,
        "/api/analys/varmestress",
        {"omrade_id": "demokvarteret", "vind_10m_m_s": 0.2},
    )
    assert st_utanfor == 200, utanfor
    assert utanfor["utanfor_giltighetsomrade_pct"] == pytest.approx(100.0)
    assert any("vind_10m_m_s" in v for v in utanfor["varningar"])

    # Renderade PNG-lager för utci_c och t_mrt_c finns, har X-Floden-Utbredning och takmaskade byggnader
    for nyckel, forv_roll in (("utci_c_fore", "utci_c"), ("t_mrt_c_fore", "t_mrt_c")):
        lag = data["lager"][nyckel]
        assert lag is not None
        assert lag["roll"] == forv_roll
        assert lag["enhet"] == "°C"
        assert lag["tak_maskat"] is True
        st_png, hdrs, png_b = app.hantera_forfragan("GET", lag["png"], b"")
        assert st_png == 200
        assert png_b.startswith(b"\x89PNG\r\n\x1a\n")
        assert "X-Floden-Utbredning" in hdrs
        rgba = np.asarray(Image.open(BytesIO(png_b)).convert("RGBA"))
        # Verifiera att minst en byggnadscell är maskad (alfa == 0) och att markceller är synliga (alfa == 255)
        assert int(np.count_nonzero(rgba[..., 3] == 0)) > 0
        assert int(np.count_nonzero(rgba[..., 3] == 255)) > 0

    # Felhantering
    assert _post_json(app, "/api/analys/varmestress", {"omrade_id": "saknas"})[0] == 404
    assert _post_json(app, "/api/analys/varmestress", {"omrade_id": "demokvarteret", "relativ_fuktighet_pct": 150.0})[0] == 400
    assert _post_json(app, "/api/analys/varmestress", {"omrade_id": "demokvarteret", "vind_10m_m_s": -1.0})[0] == 400


def test_resultatlager_roller_och_lager_version_for_utci_och_tmrt() -> None:
    assert "utci_c" in MASKADE_ROLLER
    assert "t_mrt_c" in MASKADE_ROLLER
    assert etikett_och_enhet("utci_c") == ("Värmestress (UTCI)", "°C")
    assert etikett_och_enhet("t_mrt_c") == ("Medelstrålningstemperatur (T_mrt)", "°C")
    assert farg_for_roll("utci_c") == "varme"
    assert farg_for_roll("t_mrt_c") == "varme"
    assert "varme" in PALETTER
    assert takkalla_for_roll("utci_c") == "fotavtryck"
    assert takkalla_for_roll("t_mrt_c") == "fotavtryck"
    assert LAGER_VERSION >= 11


# ------------------------------------------------------------------ 3. Geografiprojekt + RBAC + IDOR + före/efter


def test_geografiprojekt_rbac_idor_skrivlos_get_och_fore_efter(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    geo = _SyntetiskGeoV7(tmp_path, konto_miljo)
    pid = geo.projekt_id

    # 1. Oinloggad -> 401
    assert geo.http(None, "GET", f"/api/analys/gyf?omrade_id={pid}")[0] == 401
    assert geo.http(None, "GET", f"/api/analys/varmestress?omrade_id={pid}")[0] == 401

    # 2. Okänt projekt -> 404
    assert geo.http("bo", "GET", "/api/analys/gyf?omrade_id=okant-projekt-404")[0] == 404
    assert geo.http("bo", "GET", "/api/analys/varmestress?omrade_id=okant-projekt-404")[0] == 404
    assert geo.http("di", "GET", "/api/analys/gyf?omrade_id=okant-projekt-404")[0] == 404

    # 3. Cross-tenant IDOR: utomstående (`di`) -> 403 (både GET, POST och blankstegspadding)
    assert geo.http("di", "GET", f"/api/analys/gyf?omrade_id={pid}")[0] == 403
    assert geo.http("di", "GET", f"/api/analys/gyf?omrade_id={urllib.parse.quote(' ' + pid + ' ')}")[0] == 403
    assert geo.http("di", "POST", "/api/analys/gyf", {"omrade_id": pid})[0] == 403
    assert geo.http("di", "GET", f"/api/analys/varmestress?omrade_id={pid}")[0] == 403
    assert geo.http("di", "POST", "/api/analys/varmestress", {"omrade_id": pid})[0] == 403

    # 4. `lasare` (`bo`) får 200 på GET utan att mutera projektets mapp på disk!
    fore_filer = geo.projektfiler_snapshot()
    st_gyf_bo, gyf_bo = geo.http("bo", "GET", f"/api/analys/gyf?omrade_id={pid}&profil=stockholm")
    assert st_gyf_bo == 200, gyf_bo
    assert gyf_bo["metodstatus"] == "skiss"
    assert gyf_bo["fore"]["tomt_area_m2"] == pytest.approx(220.0 * 220.0)

    st_vs_bo, vs_bo = geo.http("bo", "GET", f"/api/analys/varmestress?omrade_id={pid}")
    assert st_vs_bo == 200, vs_bo
    assert vs_bo["metodstatus"] == "skiss"
    assert 0.3 < vs_bo["geometri"]["svf_medel_fore"] < 0.99

    # PNG-lagret är läsbart för `lasare` (200) men nekas för utomstående `di` (403)
    png_url = vs_bo["lager"]["utci_c_fore"]["png"]
    st_png_bo, _, png_bytes = geo.http_raw("bo", "GET", png_url)
    assert st_png_bo == 200 and png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    st_png_di, _, _ = geo.http_raw("di", "GET", png_url)
    assert st_png_di == 403

    # Projektmappen är helt orörd efter läsande GET-anrop
    assert geo.projektfiler_snapshot() == fore_filer

    # 5. Före/efter i geografiprojektet: nytt hus sänker GYF och kyler skuggad mark; träd + grönyta höjer GYF och kyler UTCI
    st_g_hus, g_hus = geo.http(
        "anna",
        "POST",
        "/api/analys/gyf",
        {"omrade_id": pid, "profil": "stockholm", "scenario": "nytt-hus"},
    )
    assert st_g_hus == 200, g_hus
    assert g_hus["jamforelse"]["delta_gyf"] < 0.0

    st_g_gron, g_gron = geo.http(
        "anna",
        "POST",
        "/api/analys/gyf",
        {"omrade_id": pid, "profil": "stockholm", "scenario": "gronyta-trad"},
    )
    assert st_g_gron == 200, g_gron
    assert g_gron["jamforelse"]["delta_gyf"] > 0.0

    st_vs_hus, vs_hus = geo.http(
        "anna",
        "POST",
        "/api/analys/varmestress",
        {"omrade_id": pid, "scenario": "nytt-hus"},
    )
    assert st_vs_hus == 200, vs_hus
    assert vs_hus["jamforelse"] is not None
    # Det nya 20 m-huset sänker medel-SVF och kyler angränsande mark (delta_utci_medel_c < 0)
    assert vs_hus["geometri"]["svf_medel_efter"] < vs_hus["geometri"]["svf_medel_fore"]
    assert vs_hus["jamforelse"]["delta_utci_medel_c"] < 0.0
    assert vs_hus["lager"]["utci_c_efter"] is not None
    assert vs_hus["lager"]["utci_c_skillnad"] is not None

    st_vs_trad, vs_trad = geo.http(
        "anna",
        "POST",
        "/api/analys/varmestress",
        {"omrade_id": pid, "scenario": "gronyta-trad"},
    )
    assert st_vs_trad == 200, vs_trad
    # Trädkronorna (~5 % av tomten) ger påtaglig avkylning (delta_tmrt_medel_c < -0.5 °C, delta_utci_medel_c < -0.1 °C)
    assert vs_trad["jamforelse"]["delta_tmrt_medel_c"] < -0.5
    assert vs_trad["jamforelse"]["delta_utci_medel_c"] < -0.1


# ------------------------------------------------------------------ 4. B11-invariant: sektion nekar utci_c och t_mrt_c med 400


def test_b11_sektion_nekar_utci_c_och_t_mrt_c_med_400(tmp_path: Path, konto_miljo: Miljo) -> None:
    """B11-invariant: `sektion` tillåter ENDAST `roll == 'max_djup'` och nekar `utci_c`/`t_mrt_c` med 400."""
    geo = _SyntetiskGeoV7(tmp_path, konto_miljo)
    pid = geo.projekt_id

    # Skapa en syntetisk klar körning i projektets korningar-katalog med max_djup, utci_c och t_mrt_c
    kid = "k-b11-utci-test"
    kmapp = geo.app.fragor.korningsmapp / kid
    utmapp = kmapp / "resultat"
    webbmapp = kmapp / "webb"
    utmapp.mkdir(parents=True)
    webbmapp.mkdir(parents=True)

    ny, nx = 20, 20
    u = {"x_min": geo.x0 + 50.0, "y_min": geo.y0 + 50.0, "x_max": geo.x0 + 70.0, "y_max": geo.y0 + 70.0}
    arr_djup = np.full((ny, nx), 0.15, dtype=np.float32)
    arr_utci = np.full((ny, nx), 35.0, dtype=np.float32)
    arr_tmrt = np.full((ny, nx), 54.0, dtype=np.float32)
    np.save(webbmapp / "skyfall.bas.max_djup.npy", arr_djup)
    np.save(webbmapp / "mikroklimat.bas.utci_c.npy", arr_utci)
    np.save(webbmapp / "mikroklimat.bas.t_mrt_c.npy", arr_tmrt)

    # Skriv giltig sammanstallning.json + korning.json + webb/lager.json (LAGER_VERSION)
    skriv_geotiff(utmapp / "dummy.tif", arr_djup, x_min=u["x_min"], y_max=u["y_max"], pixelstorlek=1.0)
    (kmapp / "korning.json").write_text(
        json.dumps(
            {
                "korning_id": kid,
                "ref": "kvarteret-v7-skyfall",
                "fraga_id": "kvarteret-v7-skyfall",
                "fraga_fil": str(geo.pmapp / "fragor" / "skyfall.toml"),
                "status": "klar",
                "skapad": "2026-11-26T12:00:00+00:00",
                "start": "2026-11-26T12:00:00+00:00",
                "slut": "2026-11-26T12:00:01+00:00",
                "fel": None,
                "jobb_totalt": 1,
                "projekt_id": pid,
            }
        ),
        encoding="utf-8",
    )
    (utmapp / "sammanstallning.json").write_text(
        json.dumps(
            {
                "fraga_id": "kvarteret-v7-skyfall",
                "beskrivning": "B11 test",
                "sammanfattning": "",
                "scenarier": {"bas": {"nyckel": "bas", "rutor": []}},
                "analyser": [],
                "jobb": [],
                "jamforelser": [],
                "total_tid_s": 0.1,
            }
        ),
        encoding="utf-8",
    )

    def _lager_post(lid: str, roll: str, enhet: str) -> dict[str, Any]:
        return {
            "id": lid,
            "namn": lid,
            "etikett": roll,
            "analys": "test",
            "scenario": "bas",
            "roll": roll,
            "typ": "bas",
            "primar": roll == "max_djup",
            "doman": "vatten" if roll == "max_djup" else "dagsljus",
            "enhet": enhet,
            "farg": "vatten" if roll == "max_djup" else "varme",
            "skala": {"min": 0.0, "max": 1.0},
            "teckenforklaring": {"typ": "kontinuerlig", "min": 0.0, "max": 1.0, "stopp": ["#000000", "#ffffff"]},
            "utbredning": u,
            "synlig_utbredning": u,
            "pixelstorlek_m": 1.0,
            "bredd_px": nx,
            "hojd_px": ny,
            "nedsampling": 1,
            "png": f"/api/korningar/{kid}/lager/{lid}.png",
            "png_med_tak": None,
            "tak_maskat": False,
            "tak_celler": 0,
            "tak_yta_m2": 0.0,
            "geotiff": [],
        }

    (webbmapp / "lager.json").write_text(
        json.dumps(
            {
                "version": LAGER_VERSION,
                "lager": [
                    _lager_post("skyfall.bas.max_djup", "max_djup", "m"),
                    _lager_post("mikroklimat.bas.utci_c", "utci_c", "°C"),
                    _lager_post("mikroklimat.bas.t_mrt_c", "t_mrt_c", "°C"),
                ],
                "varningar": [],
            }
        ),
        encoding="utf-8",
    )
    (webbmapp / "byggnader.json").write_text(
        json.dumps({"version": LAGER_VERSION, "byggnader": None, "varning": None}),
        encoding="utf-8",
    )

    linje = [[u["x_min"] + 2.0, u["y_min"] + 10.0], [u["x_max"] - 2.0, u["y_min"] + 10.0]]

    # max_djup accepteras (HTTP 200)
    st_ok, sek_ok = geo.http(
        "anna",
        "POST",
        f"/api/kartverktyg/{pid}/sektion",
        {"punkter": linje, "korning_id": kid, "fore_lager": "skyfall.bas.max_djup"},
    )
    assert st_ok == 200, sek_ok
    assert sek_ok["kontrakt"] == "floden-sektion/v1"

    # utci_c och t_mrt_c nekas ALLTID med HTTP 400 (både som fore_lager och efter_lager)
    for nekad in ("mikroklimat.bas.utci_c", "mikroklimat.bas.t_mrt_c"):
        st_f, fel_f = geo.http(
            "anna",
            "POST",
            f"/api/kartverktyg/{pid}/sektion",
            {"punkter": linje, "korning_id": kid, "fore_lager": nekad},
        )
        assert st_f == 400, (nekad, fel_f)
        assert "max_djup" in fel_f["fel"]

        st_e, fel_e = geo.http(
            "anna",
            "POST",
            f"/api/kartverktyg/{pid}/sektion",
            {
                "punkter": linje,
                "korning_id": kid,
                "fore_lager": "skyfall.bas.max_djup",
                "efter_lager": nekad,
            },
        )
        assert st_e == 400, (nekad, fel_e)


# ------------------------------------------------------------------ 5. ASGI-semaforklassning


def test_asgi_semafor_svalter_inte_latta_gyf_och_get_lasningar() -> None:
    assert FlodenAsgiApp._ar_tung_berakning("GET", "/api/analys/gyf") is False
    assert FlodenAsgiApp._ar_tung_berakning("POST", "/api/analys/gyf") is False
    assert FlodenAsgiApp._ar_tung_berakning("GET", "/api/analys/varmestress") is False
    assert FlodenAsgiApp._ar_tung_berakning("POST", "/api/analys/varmestress") is True
