# ruff: noqa: E501
"""Deterministiska regressionstester för Våg 9:
- `skyfall.toml` får `fokus` och äldre projekt utan `fokus` får automatiskt skyfallsfokus via `kartverktyg`.
- `fragor._roller` + `resultat()` + `kv.status()` överlever saknad/trasig `run.json` utan HTTP 500.
- `projektskapa.geografier()` hoppar över `_filcache@*` tyst men varnar fortsatt för trasiga riktiga geografier.
- `docker-compose.prod.yml` och `docs/DRIFT.md` konfigurerar och dokumenterar 4-lagers RAM-planen.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from geografi.modell import KONTRAKT_GEOGRAFI, lagra, skriv_manifest
from karna.korplan import planera
from karna.resultatcache import ResultatCache
from karna.utredning import las_fraga
from kommuner.konfig import las_kommun
from kommuner.projekt import skapa_projekt
from konto.databas import Databas
from konto.register import Register
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from markmodell.hinder_oppningar import brann_in_byggnader
from markmodell.rutnat import Ruta as MarkRuta
from testsvit.k1_io import skriv_geotiff
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel

REPO = Path(__file__).resolve().parents[2]


def _skapa_3x3_geografi(tmp_path: Path, *, sida_m: float = 200.0) -> tuple[Path, str, tuple[float, float, float, float]]:
    """Bygger en deterministisk 3×3-rutors (9 rutor) K1 v2-geografi i EPSG:3011."""
    geo_rot = tmp_path / "geo"
    k1_bygge = tmp_path / "k1_bygge"
    ruta_n = 20
    px = sida_m / ruta_n  # t.ex. 10 m vid 200 m ruta -> 600×600 m totalt, blixtsnabbt i test
    x_orig, y_orig = 150_000.0, 6_580_000.0
    rutor_namn: list[str] = []

    for ix in range(3):
        for iy in range(3):
            rx0 = x_orig + ix * sida_m
            ry0 = y_orig + iy * sida_m
            ry_max = ry0 + sida_m
            rid = f"mm_{int(rx0)}_{int(ry0)}"
            rutor_namn.append(rid)
            rm = k1_bygge / rid
            rm.mkdir(parents=True)
            xs = rx0 + (np.arange(ruta_n, dtype=np.float32) + 0.5) * px
            ys = ry_max - (np.arange(ruta_n, dtype=np.float32) + 0.5) * px
            xx, yy = np.meshgrid(xs, ys)
            cx, cy = x_orig + 0.5 * sida_m, y_orig + 0.5 * sida_m
            terrang = (10.0 + 0.01 * np.hypot(xx - cx, yy - cy)).astype(np.float32)
            markslag = np.ones((ruta_n, ruta_n), dtype=np.uint8)
            bygg = []
            if ix == 0 and iy == 0:
                bygg = [
                    ByggnadsPost(
                        byggnad_id="b_h1",
                        polygon=(
                            (rx0 + 20.0, ry0 + 20.0),
                            (rx0 + 40.0, ry0 + 20.0),
                            (rx0 + 40.0, ry0 + 40.0),
                            (rx0 + 20.0, ry0 + 40.0),
                        ),
                        takhojd_rh2000=22.0,
                        markhojd_rh2000=10.0,
                        hojd_m=12.0,
                        kalla="test",
                        hojdkalla="kalla_hojd",
                    )
                ]
            r_obj = MarkRuta(
                e_min_m=rx0,
                n_min_m=ry0,
                storlek_m=sida_m,
                overlapp_m=0.0,
                upplosning_m=px,
            )
            mark, slag = brann_in_byggnader(
                terrang.copy(),
                markslag.copy(),
                r_obj,
                [b.till_byggnad() for b in bygg],
            )
            skriv_geotiff(rm / "terrang.tif", terrang, x_min=rx0, y_max=ry_max, pixelstorlek=px)
            skriv_geotiff(rm / "mark.tif", mark.astype(np.float32), x_min=rx0, y_max=ry_max, pixelstorlek=px)
            skriv_geotiff(rm / "markslag.tif", slag.astype(np.uint8), x_min=rx0, y_max=ry_max, pixelstorlek=px)
            skriv_geotiff(
                rm / "osakerhet.tif",
                np.full((ruta_n, ruta_n), 0.05, dtype=np.float32),
                x_min=rx0,
                y_max=ry_max,
                pixelstorlek=px,
            )
            skriv_byggnader_gpkg(rm / "byggnader.gpkg", bygg, (rx0, ry0, rx0 + sida_m, ry_max))
            (rm / "recept.json").write_text(
                json.dumps({"kontrakt": "K1/v2", "ruta": rid, "overlapp_m": 0.0}) + "\n",
                encoding="utf-8",
            )

    k1_lager = lagra(geo_rot, k1_bygge)
    gransen = (x_orig, y_orig, x_orig + 3.0 * sida_m, y_orig + 3.0 * sida_m)
    karna = (x_orig + 10.0, y_orig + 10.0, x_orig + 3.0 * sida_m - 10.0, y_orig + 3.0 * sida_m - 10.0)
    manifest: dict[str, Any] = {
        "kontrakt": KONTRAKT_GEOGRAFI,
        "id": "gbg-9r",
        "version": "v1",
        "namn": "Syntetisk 9-rutorsgeografi",
        "kommun": "goteborg",
        "karna_3011": list(karna),
        "gransen_3011": list(gransen),
        "buffert": {"vald_m": 100.0, "tillrinning_nar_kanten": False},
        "forberakningar": {
            "tillrinning": {
                "cell_m": 10.0,
                "celler": 50,
                "yta_m2": 5000.0,
                "krav_buffert_m": 50.0,
                "nar_kanten": False,
            }
        },
        "lager": {
            "k1_rutnat": {"sha256": k1_lager.sha256, "rutor": sorted(rutor_namn)},
        },
        "kallor": {
            "hojd": {"licens": "CC0-1.0"},
            "byggnader": {"licens": "CC0-1.0", "kalla": "filpaket"},
        },
        "oppen_for_mcp": True,
    }
    skriv_manifest(geo_rot, manifest)
    return geo_rot, "gbg-9r@v1", gransen


def test_skyfall_toml_och_gammalt_projekt_far_fokus_via_kartverktyg(tmp_path: Path) -> None:
    """Nya projekt skriver `fokus` i `skyfall.toml`, och redan skapade projekt utan `fokus`
    får automatiskt skyfallsfokus via `kartverktyg` från `projekt.json` (laddar bara berörda rutor)."""
    geo_rot, ref, gransen = _skapa_3x3_geografi(tmp_path, sida_m=200.0)
    projektrot = tmp_path / "projekt"
    gbg = las_kommun("goteborg", rot=REPO)
    # 40×40 m projekt i nedre vänstra rutan [150000..150200, 6580000..6580200], kontext_m = 100 m
    # -> vatten_ruta [150000..150190, 6580000..6580190] överlappar 1 av 9 rutor (inte 9!).
    avgr = (150_050.0, 6_580_050.0, 150_090.0, 6_580_090.0)
    res = skapa_projekt(
        gbg,
        ref=ref,
        avgransning=avgr,
        namn="Haga litet",
        konto_id=None,
        geo_rot=geo_rot,
        projektrot=projektrot,
        konfig={},
        konfig_fil=tmp_path / "config.toml",
        skriv=lambda _: None,
    )

    pjson = json.loads((res.mapp / "projekt.json").read_text(encoding="utf-8"))
    assert pjson["vatten_ruta_3011"] != list(gransen)
    assert pjson["vatten_ruta_3011"] == [150_000.0, 6_580_000.0, 150_190.0, 6_580_190.0]

    # 1. Nyskapad skyfall.toml har fokus och planera laddar bara 1 av 9 rutor.
    f_sky = las_fraga(res.mapp / "fragor" / "skyfall.toml")
    assert f_sky.analyser[0].fokus is not None
    cache = ResultatCache(tmp_path / "cache")
    plan_ny = planera(f_sky, cache)
    assert len(plan_ny.jobb) == 1
    assert [r.ruta_id for r in plan_ny.jobb[0].rutor] == ["mm_150000_6580000"]

    # 2. Simulera ett redan skapat projekt på servern: ta bort `fokus` ur skyfall.toml och sätt
    #    äldre `vatten_ruta_3011 == gransen_3011` i projekt.json.
    sky_fil = res.mapp / "fragor" / "skyfall.toml"
    rader_utan_fokus = [
        rad for rad in sky_fil.read_text(encoding="utf-8").splitlines() if not rad.startswith("fokus =")
    ]
    sky_fil.write_text("\n".join(rader_utan_fokus) + "\n", encoding="utf-8")
    assert las_fraga(sky_fil).analyser[0].fokus is None

    pjson["vatten_ruta_3011"] = list(gransen)
    (res.mapp / "projekt.json").write_text(json.dumps(pjson, indent=2) + "\n", encoding="utf-8")

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache_webb",
        korningsmapp=tmp_path / "korningar",
        kartverktygsmapp=tmp_path / "kartverktyg",
        geomapp=geo_rot,
        projektmapp=projektrot,
    )
    kv = app.kartverktyg

    # Läget idag (`_skriv_lage_fragor`) injicerar skyfallsfokus och planerar bara 1 av 9 rutor!
    plan_lage = kv.planera_lage_idag(res.projekt_id, ["vatten.skyfall"])
    lage_fid = plan_lage["fragor"][0]["fraga_id"]
    lage_k20 = las_fraga(res.mapp / "fragor" / f"{lage_fid}.json")
    assert lage_k20.analyser[0].fokus is not None
    plan_lage_karna = planera(lage_k20, app.fragor.cache())
    assert [r.ruta_id for r in plan_lage_karna.jobb[0].rutor] == ["mm_150000_6580000"]

    # Scenariokörning (`_skriv_fragor`) injicerar likaså skyfallsfokus från projekt.json!
    sc = kv.spara_scenario(
        res.projekt_id,
        None,
        {
            "namn": "Nytt hus",
            "andringar": [
                {
                    "typ": "byggnad_ny",
                    "andring_id": "hus-v9",
                    "geometri": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [150_060.0, 6_580_060.0],
                                [150_075.0, 6_580_060.0],
                                [150_075.0, 6_580_075.0],
                                [150_060.0, 6_580_075.0],
                                [150_060.0, 6_580_060.0],
                            ]
                        ],
                    },
                    "egenskaper": {"hojd_m": 12.0},
                }
            ],
        },
    )
    plan_sc = kv.planera(res.projekt_id, sc["slug"], ["vatten.skyfall"])
    sc_fid = plan_sc["fragor"][0]["fraga_id"]
    sc_k20 = las_fraga(res.mapp / "fragor" / f"{sc_fid}.json")
    assert sc_k20.analyser[0].fokus is not None
    plan_sc_karna = planera(sc_k20, app.fragor.cache())
    assert all(
        [r.ruta_id for r in j.rutor] == ["mm_150000_6580000"] for j in plan_sc_karna.jobb
    )

    # 3. Med uttrycklig `vatten_ruta_3011` över 2 rutor används den direkt (2 av 9 rutor).
    pjson["vatten_ruta_3011"] = [150_050.0, 6_580_050.0, 150_250.0, 6_580_150.0]
    (res.mapp / "projekt.json").write_text(json.dumps(pjson, indent=2) + "\n", encoding="utf-8")
    plan_lage_2r = kv.planera_lage_idag(res.projekt_id, ["vatten.skyfall"])
    lage_k20_2r = las_fraga(res.mapp / "fragor" / f"{plan_lage_2r['fragor'][0]['fraga_id']}.json")
    assert lage_k20_2r.analyser[0].fokus == (150_050.0, 6_580_050.0, 150_250.0, 6_580_150.0)
    plan_2r_karna = planera(lage_k20_2r, app.fragor.cache())
    assert sorted(r.ruta_id for r in plan_2r_karna.jobb[0].rutor) == [
        "mm_150000_6580000",
        "mm_150200_6580000",
    ]


def test_fragor_roller_resultat_och_kv_status_overlever_saknad_run_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Saknad eller skadad `run.json` i en äldre körning loggar varning och ger inte HTTP 500."""
    geo_rot, ref, _ = _skapa_3x3_geografi(tmp_path, sida_m=200.0)
    projektrot = tmp_path / "projekt"
    gbg = las_kommun("goteborg", rot=REPO)
    avgr = (150_050.0, 6_580_050.0, 150_090.0, 6_580_090.0)
    res = skapa_projekt(
        gbg,
        ref=ref,
        avgransning=avgr,
        namn="Status 500 test",
        konto_id=None,
        geo_rot=geo_rot,
        projektrot=projektrot,
        konfig={},
        konfig_fil=tmp_path / "config.toml",
        skriv=lambda _: None,
    )
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        kartverktygsmapp=tmp_path / "kartverktyg",
        geomapp=geo_rot,
        projektmapp=projektrot,
    )
    kv = app.kartverktyg
    sc = kv.spara_scenario(
        res.projekt_id,
        None,
        {
            "namn": "Test 5",
            "andringar": [
                {
                    "typ": "byggnad_ny",
                    "andring_id": "hus-s500",
                    "geometri": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [150_060.0, 6_580_060.0],
                                [150_072.0, 6_580_060.0],
                                [150_072.0, 6_580_072.0],
                                [150_060.0, 6_580_072.0],
                                [150_060.0, 6_580_060.0],
                            ]
                        ],
                    },
                    "egenskaper": {"hojd_m": 12.0},
                }
            ],
        },
    )
    poster = kv._skriv_fragor(res.projekt_id, sc["slug"], ["vatten.skyfall"])  # noqa: SLF001
    p = poster[0]
    kid = "rita-test-5-vatten"
    st_kor, _ = app.fragor.kor_synkront(p["ref"], kid, res.projekt_id)
    assert st_kor["status"] == "klar"

    # Registrera körningen i scenariofilen som kv.kor() gör.
    sc_fil = kv._fil(res.projekt_id, sc["slug"])  # noqa: SLF001
    sc_data = json.loads(sc_fil.read_text(encoding="utf-8"))
    sc_data["korningar"] = {
        "vatten": {"korning_id": kid, "ref": p["ref"], "k9_nyckel": p["k9_nyckel"]}
    }
    sc_fil.write_text(json.dumps(sc_data, indent=2) + "\n", encoding="utf-8")

    # Ta bort ett deljobbs run.json + eventuell lagercache så `_roller` måste läsa från disk.
    k = app.fragor._hamta(kid)  # noqa: SLF001
    s = app.fragor._sammanstallning(k)  # noqa: SLF001
    j0 = s["jobb"][0]
    run_fil = app.fragor._jobbmapp(k, j0) / "run.json"  # noqa: SLF001
    assert run_fil.is_file()
    run_fil.unlink()
    lager_cache = k.mapp / "webb" / "lager.json"
    lager_cache.unlink(missing_ok=True)

    with caplog.at_level(logging.WARNING, logger="webb.fragor"):
        # 1. `_roller` returnerar {} och varnar i stället för FileNotFoundError.
        assert app.fragor._roller(k, j0) == {}  # noqa: SLF001
        assert any("run.json saknas" in r.getMessage() for r in caplog.records)

        # 2. `resultat()` och HTTP GET /api/korningar/<id>/resultat returnerar 200 (aldrig 500).
        status_code, _, kropp = app.hantera_forfragan("GET", f"/api/korningar/{kid}/resultat")
        assert status_code == 200, kropp.decode("utf-8", errors="replace")
        res_data = json.loads(kropp)
        assert any("run.json" in str(v.get("text", "")) for v in res_data["varningar"])

        # 3. `kv.status()` överlever utan FileNotFoundError / HTTP 500.
        st_kv = kv.status(res.projekt_id, sc["slug"])
        assert st_kv["slug"] == sc["slug"]
        assert len(st_kv["korningar"]) == 1
        assert st_kv["korningar"][0]["status"]["status"] == "klar"

        # 4. `_sankgraf_vektor` ger tydligt 409-fel (aldrig unhandled FileNotFoundError / 500).
        with pytest.raises(FlodenWebbFel) as exc_v:
            app.fragor.vektor(kid, str(j0["scenario"]), "sankor")
        assert exc_v.value.status_kod == 409

    # 5. Skadad JSON i run.json hanteras på samma sätt utan 500.
    run_fil.write_text("{trasig-json", encoding="utf-8")
    assert app.fragor._roller(k, j0) == {}  # noqa: SLF001


def test_geografier_hoppar_over_filcache_tyst_men_varnar_for_trasig_geografi(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`_filcache@*` och andra interna `_*`-kataloger hoppas över tyst, medan en riktig trasig geografi varnar."""
    geo_rot = tmp_path / "geo"
    # Interna kataloger som ska hoppas över utan varning:
    (geo_rot / "_lager" / "deadbeef").mkdir(parents=True)
    (geo_rot / "_filcache" / "0123456789abcdef").mkdir(parents=True)
    (geo_rot / "_tmp" / "del").mkdir(parents=True)
    # Riktig trasig geografi som ska logga varning:
    (geo_rot / "trasig-geo" / "v1").mkdir(parents=True)
    # Giltig geografi som ska listas:
    skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "giltig-geo",
            "version": "v1",
            "namn": "Giltig geografi",
            "kommun": "goteborg",
            "karna_3011": [150_000.0, 6_580_000.0, 150_100.0, 6_580_100.0],
            "gransen_3011": [149_900.0, 6_579_900.0, 150_200.0, 6_580_200.0],
            "lager": {},
        },
    )

    reg = Register(Databas(tmp_path / "konto.sqlite"))
    org = reg.skapa_organisation("Testorg")
    konto_id = reg.forbered_konto("admin@example.test")
    reg.gor_organisationsadmin(konto_id, org)

    class _KontoStub:
        aktiv = True
        register = reg

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=tmp_path / "config.toml",
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        konto=_KontoStub(),
        geomapp=geo_rot,
        projektmapp=tmp_path / "projekt",
    )

    with caplog.at_level(logging.WARNING, logger="webb.projektskapa"):
        svar = app.projekt_skapa.geografier(konto_id)

    assert [g["ref"] for g in svar["geografier"]] == ["giltig-geo@v1"]
    varningar = [r.getMessage() for r in caplog.records]
    assert not any("_filcache" in m for m in varningar), varningar
    assert not any("_lager" in m for m in varningar), varningar
    assert not any("_tmp" in m for m in varningar), varningar
    assert any(
        "geografi hoppas över (manifestet går inte att läsa): trasig-geo@v1" in m
        for m in varningar
    ), varningar


def test_docker_compose_prod_och_drift_ram_plan() -> None:
    """Verifierar konfigurerbart `FLODEN_MEM_LIMIT` / `FLODEN_MAX_SANKGRAF_CELLER` och RAM-planen i `docs/DRIFT.md`."""
    compose = (REPO / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "mem_limit: ${FLODEN_MEM_LIMIT:-3g}" in compose
    assert "FLODEN_MAX_SANKGRAF_CELLER: ${FLODEN_MAX_SANKGRAF_CELLER:-}" in compose

    drift = (REPO / "docs" / "DRIFT.md").read_text(encoding="utf-8")
    assert "## Minneshantering och RAM-plan (CX23 → S5k)" in drift
    for nyckel in (
        "Lager 1",
        "Lager 2",
        "Lager 3",
        "Lager 4",
        "FLODEN_MAX_SANKGRAF_CELLER",
        "FLODEN_MEM_LIMIT",
        "CX23",
        "CX33",
        "CX43",
        "S5k",
    ):
        assert nyckel in drift, nyckel
