# ruff: noqa: E501, F811
"""Regressionstester för P0-buggarna B4, B5, B7 och B11 (geografi, resultat, sektion, status).

Alla beräkningar sker deterministiskt på syntetiska eller demokvarterets K1-rutor i EPSG:3011 / RH 2000.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from data_inlasning.nmd import NmdLager, skriv_nmd
from demovarld.demo import LITEN
from demovarld.varld import nytt_hus_polygon
from geografi.modell import KONTRAKT_GEOGRAFI, lagra, skriv_manifest
from karna.inbyggda import StatiskFyllning
from karna.utredning import las_fraga
from kommuner.konfig import las_kommun
from kommuner.projekt import kor_projekt, skapa_projekt
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from markmodell.hinder_oppningar import BYGGNAD, brann_in_byggnader
from markmodell.rutnat import Ruta as MarkRuta
from tests.webb.conftest import Demo
from testsvit.k1_io import skriv_geotiff
from webb.api import FlodenApiApp
from webb.ekologi_analys import ekologi_svar
from webb.fragor import LAGER_VERSION
from webb.resultatlager import (
    ETIKETTER,
    MASKADE_ROLLER,
    SKALA_FOR_ROLL,
    TAKKALLA_FOTAVTRYCK,
    etikett_och_enhet,
    farg_for_roll,
    rita_png,
    sammanfoga,
    tak_mask,
    takkalla_for_roll,
    teckenforklaring,
)

REPO = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ B4: takmask, enhet & klasskala


def test_b4_solroller_takmask_etiketter_och_klasskala(tmp_path: Path) -> None:
    """`sammanhangande_soltimmar` och `skuggmask` ska takmaskas, ha korrekta etiketter/enheter och klasskala."""
    assert "sammanhangande_soltimmar" in MASKADE_ROLLER
    assert "skuggmask" in MASKADE_ROLLER
    assert takkalla_for_roll("sammanhangande_soltimmar") == TAKKALLA_FOTAVTRYCK
    assert takkalla_for_roll("skuggmask") == TAKKALLA_FOTAVTRYCK
    assert ETIKETTER["sammanhangande_soltimmar"] == ("Sammanhängande soltimmar", "h")
    assert ETIKETTER["skuggmask"] == ("Solläge vid klockslag", "")
    assert etikett_och_enhet("sammanhangande_soltimmar") == ("Sammanhängande soltimmar", "h")
    assert etikett_och_enhet("skuggmask") == ("Solläge vid klockslag", "")
    assert SKALA_FOR_ROLL["skuggmask"] == "klass"
    assert farg_for_roll("skuggmask") == "klass"
    assert LAGER_VERSION >= 8

    # Bygg en syntetisk 10x10 m K1 v2-ruta med ett 4x4 m hus i mitten (EPSG:3011, RH 2000).
    ruta = tmp_path / "mm_test"
    ruta.mkdir()
    x0, y_max, px = 150_000.0, 6_580_010.0, 1.0
    mark = np.full((10, 10), 10.0, dtype=np.float32)
    skriv_geotiff(ruta / "mark.tif", mark, x_min=x0, y_max=y_max, pixelstorlek=px)
    skriv_geotiff(ruta / "terrang.tif", mark, x_min=x0, y_max=y_max, pixelstorlek=px)
    skriv_geotiff(ruta / "markslag.tif", np.ones((10, 10), dtype=np.uint8), x_min=x0, y_max=y_max, pixelstorlek=px)
    skriv_byggnader_gpkg(
        ruta / "byggnader.gpkg",
        [
            ByggnadsPost(
                byggnad_id="b_test_hus_1",
                polygon=(
                    (x0 + 3.0, y_max - 7.0),
                    (x0 + 7.0, y_max - 7.0),
                    (x0 + 7.0, y_max - 3.0),
                    (x0 + 3.0, y_max - 3.0),
                ),
                takhojd_rh2000=22.0,
                markhojd_rh2000=10.0,
                hojd_m=12.0,
                kalla="test",
                hojdkalla="kalla_hojd",
            )
        ],
        (x0, y_max - 10.0, x0 + 10.0, y_max),
    )

    # Solraster med värden på både mark och hustak.
    sam_fil = skriv_geotiff(
        tmp_path / "sammanhangande.tif",
        np.full((10, 10), 5.0, dtype=np.float32),
        x_min=x0,
        y_max=y_max,
        pixelstorlek=px,
    )
    skugg_arr = np.zeros((10, 10), dtype=np.float32)
    skugg_arr[:, 5:] = 1.0
    skugg_fil = skriv_geotiff(
        tmp_path / "skuggmask.tif",
        skugg_arr,
        x_min=x0,
        y_max=y_max,
        pixelstorlek=px,
    )

    for roll, fil, lo, hi in (
        ("sammanhangande_soltimmar", sam_fil, 0.0, 8.0),
        ("skuggmask", skugg_fil, 0.0, 1.0),
    ):
        mosaik = sammanfoga([fil])
        tak = tak_mask(mosaik, [ruta], kalla=takkalla_for_roll(roll))
        assert tak[4, 4] and tak[5, 5], f"Hustaket ska vara maskat för {roll}"
        assert not tak[0, 0] and not tak[9, 9], f"Marken ska inte vara maskad för {roll}"
        png, _, _, _ = rita_png(mosaik.raster, farg_for_roll(roll), lo=lo, hi=hi, dold=tak)
        rgba = np.asarray(Image.open(BytesIO(png)).convert("RGBA"))
        # Hustak blir transparent (alpha == 0), mark förblir synlig (alpha == 255).
        assert int(rgba[4, 4, 3]) == 0 and int(rgba[5, 5, 3]) == 0
        assert int(rgba[0, 0, 3]) == 255 and int(rgba[9, 9, 3]) == 255

    # Skuggmaskens färgskala ger diskreta klassfärger för 0 resp. 1 och kategorisk teckenförklaring.
    m_skugg = sammanfoga([skugg_fil])
    tak_skugg = tak_mask(m_skugg, [ruta], kalla=takkalla_for_roll("skuggmask"))
    png_skugg, _, _, _ = rita_png(m_skugg.raster, farg_for_roll("skuggmask"), lo=0.0, hi=1.0, dold=tak_skugg)
    rgba_skugg = np.asarray(Image.open(BytesIO(png_skugg)).convert("RGBA"))
    assert tuple(rgba_skugg[0, 0, :3]) != tuple(rgba_skugg[0, 9, :3])
    assert teckenforklaring(farg_for_roll("skuggmask"), 0.0, 1.0)["typ"] == "kategorier"


# ------------------------------------------------------------------ B5 & B11: demo-app


def _bygg_demo_app(demo: Demo, rot: Path) -> FlodenApiApp:
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[demo.fragor],
        cachemapp=demo.rot / "cache",
        korningsmapp=demo.rot / "korningar",
        kartverktygsmapp=rot / "kartverktyg",
        datamappar=[rot],
    )
    app.fragor.demomapp = demo.varld
    return app


def _vanta_klar(hamta_status: Any, *, timeout_s: float = 120.0) -> dict[str, Any]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        st = hamta_status()
        korningar = st.get("korningar", [])
        if korningar and all(k["status"]["status"] in ("klar", "fel") for k in korningar):
            return dict(st)
        time.sleep(0.1)
    raise AssertionError("Körningen blev inte klar inom timeout.")


def test_b5_status_lage_idag_och_scenariostatus_returnerar_faktiska_parametrar(
    demo: Demo, tmp_path: Path
) -> None:
    """`status_lage_idag` och `status` (scenario) returnerar strukturerade faktiska regn-, avtappnings- och solparametrar."""
    app = _bygg_demo_app(demo, tmp_path)
    kv = app.kartverktyg

    # 1. Läget idag med standardregn (inga överstyrningar).
    kv.kor_lage_idag("demokvarteret", ["vatten.skyfall"])
    st_std = _vanta_klar(lambda: kv.status_lage_idag("demokvarteret"))
    p_std = next(k for k in st_std["korningar"] if k["mall"] == "vatten")
    assert p_std["status"]["status"] == "klar"
    assert p_std["regn"] is not None
    assert p_std["regn"]["ar_standard"] is True
    assert p_std["regn"]["aterkomsttid_ar"] == pytest.approx(100.0)
    assert p_std["regn"]["varaktighet_min"] == pytest.approx(360.0)
    assert p_std["regn"]["klimatfaktor"] == pytest.approx(1.4)
    assert p_std["regn"]["berakningstyp"] == "statisk"
    assert p_std["regn"]["kalla"] == "stockholmsregn-2024"
    assert p_std["avtappning_mm_h"] is None

    # 2. Läget idag med anpassat 10-årsregn + avtappning 15 mm/h.
    kv.kor_lage_idag(
        "demokvarteret",
        ["vatten.skyfall"],
        avtappning_mm_h=15.0,
        regn={"aterkomsttid_ar": 10, "varaktighet_min": 60, "klimatfaktor": 1.25, "berakningstyp": "statisk", "regntyp": "cds"},
    )
    st_10 = _vanta_klar(lambda: kv.status_lage_idag("demokvarteret"))
    p_10 = next(k for k in st_10["korningar"] if k["mall"] == "vatten")
    assert p_10["status"]["status"] == "klar"
    assert p_10["regn"]["ar_standard"] is False
    assert p_10["regn"]["aterkomsttid_ar"] == pytest.approx(10.0)
    assert p_10["regn"]["varaktighet_min"] == pytest.approx(60.0)
    assert p_10["regn"]["klimatfaktor"] == pytest.approx(1.25)
    assert p_10["regn"]["berakningstyp"] == "statisk"
    assert p_10["regn"]["regntyp"] == "cds"
    assert p_10["avtappning_mm_h"] == pytest.approx(15.0)

    # 3. Läget idag med direkt mm-regn + explicit avtappning 0,0 mm/h.
    kv.kor_lage_idag(
        "demokvarteret",
        ["vatten.skyfall"],
        avtappning_mm_h=0.0,
        regn={"regn_mm": 80.0, "berakningstyp": "statisk", "regntyp": "block"},
    )
    st_mm = _vanta_klar(lambda: kv.status_lage_idag("demokvarteret"))
    p_mm = next(k for k in st_mm["korningar"] if k["mall"] == "vatten")
    assert p_mm["regn"]["ar_standard"] is False
    assert p_mm["regn"]["aterkomsttid_ar"] is None
    assert p_mm["regn"]["regn_mm"] == pytest.approx(80.0)
    assert p_mm["avtappning_mm_h"] == pytest.approx(0.0)

    # 4. Scenariostatus returnerar både strukturerat regn, avtappning och solparametrar.
    poly = nytt_hus_polygon(LITEN.varld())
    ring = [[float(x), float(y)] for x, y in poly]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    sc = kv.spara_scenario(
        "demokvarteret",
        None,
        {
            "namn": "B5 scenario",
            "andringar": [
                {
                    "typ": "byggnad_ny",
                    "andring_id": "hus-b5",
                    "geometri": {"type": "Polygon", "coordinates": [ring]},
                    "egenskaper": {"hojd_m": 12.0},
                }
            ],
        },
    )
    kv.kor(
        "demokvarteret",
        sc["slug"],
        ["vatten.skyfall", "dagsljus.sol"],
        avtappning_mm_h=12.0,
        regn={"aterkomsttid_ar": 10, "varaktighet_min": 60, "klimatfaktor": 1.25, "berakningstyp": "statisk", "regntyp": "cds"},
        sol={"datum_iso": "2025-06-21", "start_timme": 9, "slut_timme": 17, "sammanhangande": False, "inkludera_vsc": False},
    )
    st_sc = _vanta_klar(lambda: kv.status("demokvarteret", sc["slug"]))
    per_mall = {k["mall"]: k for k in st_sc["korningar"]}
    assert per_mall["vatten"]["status"]["status"] == "klar"
    assert per_mall["vatten"]["regn"]["ar_standard"] is False
    assert per_mall["vatten"]["regn"]["aterkomsttid_ar"] == pytest.approx(10.0)
    assert per_mall["vatten"]["avtappning_mm_h"] == pytest.approx(12.0)
    assert per_mall["vatten"]["regn_text"]
    assert per_mall["dagsljus"]["status"]["status"] == "klar"
    assert per_mall["dagsljus"]["sol"]["ar_standard"] is False
    assert per_mall["dagsljus"]["sol"]["datum_iso"] == "2025-06-21"
    assert per_mall["dagsljus"]["sol"]["start_timme"] == pytest.approx(9.0)
    assert per_mall["dagsljus"]["sol"]["slut_timme"] == pytest.approx(17.0)
    assert per_mall["dagsljus"]["sol"]["sammanhangande"] is False


def test_b11_sektion_nekar_icke_max_djup_lager_med_400(demo: Demo, tmp_path: Path) -> None:
    """Sektions-API:t ska vägra icke-`max_djup`-lager (t.ex. soltimmar, svf, strömtäthet, skillnad) med 400."""
    app = _bygg_demo_app(demo, tmp_path)
    poly = nytt_hus_polygon(LITEN.varld())
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    mid_y = 0.5 * (min(ys) + max(ys))
    linje = [[min(xs) - 20.0, mid_y], [max(xs) + 20.0, mid_y]]

    def post_sektion(kropp: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        st, _, ut = app.hantera_forfragan(
            "POST",
            "/api/kartverktyg/demokvarteret/sektion",
            json.dumps(kropp).encode("utf-8"),
        )
        return st, json.loads(ut)

    delar = app.fragor.demo()["delar"]
    dag_kid = delar["demo_dagsljus"]["korning_id"]
    dag_res = app.fragor.resultat(dag_kid)
    sol_lager = next(la["id"] for la in dag_res["lager"] if la["roll"] == "soltimmar" and la["typ"] != "skillnad")

    # Soltimmar som fore_lager eller efter_lager -> HTTP 400 (aldrig feltolkat som vattendjup!).
    st_sol_f, svar_sol_f = post_sektion({"punkter": linje, "korning_id": dag_kid, "fore_lager": sol_lager})
    assert st_sol_f == 400 and "max_djup" in svar_sol_f["fel"], svar_sol_f

    st_sol_e, svar_sol_e = post_sektion({"punkter": linje, "korning_id": dag_kid, "efter_lager": sol_lager})
    assert st_sol_e == 400 and "max_djup" in svar_sol_e["fel"], svar_sol_e

    # Samma körning UTAN vattenlager (fore_lager=None, efter_lager=None) -> 200 ren mark-/byggnadsprofil!
    st_ren, svar_ren = post_sektion({"punkter": linje, "korning_id": dag_kid, "fore_lager": None, "efter_lager": None})
    assert st_ren == 200, svar_ren
    assert all(v is None for v in svar_ren["vatten_djup_fore_m"])
    assert all(v is None for v in svar_ren["vatten_yta_fore_m"])

    # Strömtäthet (ekologi) som vattenlager -> HTTP 400.
    eko_kid = delar["demo_ekologi"]["korning_id"]
    eko_res = app.fragor.resultat(eko_kid)
    strom_lager = next(la["id"] for la in eko_res["lager"] if la["roll"] == "stromtathet" and la["typ"] != "skillnad")
    st_eko, svar_eko = post_sektion({"punkter": linje, "korning_id": eko_kid, "fore_lager": strom_lager})
    assert st_eko == 400 and "max_djup" in svar_eko["fel"], svar_eko

    # Skillnadslager (även med roll max_djup) -> HTTP 400; riktigt bas-`max_djup` -> HTTP 200.
    vat_kid = delar["demo_vatten"]["korning_id"]
    vat_res = app.fragor.resultat(vat_kid)
    diff_lager = next(la["id"] for la in vat_res["lager"] if la["roll"] == "max_djup" and la["typ"] == "skillnad")
    bas_lager = next(la["id"] for la in vat_res["lager"] if la["roll"] == "max_djup" and la["typ"] == "bas")
    assert post_sektion({"punkter": linje, "korning_id": vat_kid, "fore_lager": diff_lager})[0] == 400
    st_ok, svar_ok = post_sektion({"punkter": linje, "korning_id": vat_kid, "fore_lager": bas_lager})
    assert st_ok == 200 and any(v is not None for v in svar_ok["vatten_djup_fore_m"])


# ------------------------------------------------------------------ B7: flerruts-skyfall, IDF & ekologi


def _bygg_syntetisk_flerruts_geografi_med_nmd(
    tmp_path: Path,
) -> tuple[Path, str, tuple[float, float, float, float]]:
    """Bygger en snabb, deterministisk 2-rutors K1 v2-geografi med NMD-lager i EPSG:3011 / RH 2000."""
    geo_rot = tmp_path / "geo"
    x0, y0, px = 150_000.0, 6_580_000.0, 2.0
    ruta_n = 30  # 60 x 60 m per ruta -> 2 rutor = 120 x 60 m totalt
    sida_m = ruta_n * px
    k1_bygge = tmp_path / "bygge_k1"

    hus_post = ByggnadsPost(
        byggnad_id="b_geo_bef_1",
        polygon=(
            (x0 + 14.0, y0 + 14.0),
            (x0 + 26.0, y0 + 14.0),
            (x0 + 26.0, y0 + 24.0),
            (x0 + 14.0, y0 + 24.0),
        ),
        takhojd_rh2000=24.0,
        markhojd_rh2000=12.0,
        hojd_m=12.0,
        kalla="test",
        hojdkalla="kalla_hojd",
    )

    for idx in range(2):
        rx0 = x0 + idx * sida_m
        ry_max = y0 + sida_m
        rmapp = k1_bygge / f"mm_{idx}"
        rmapp.mkdir(parents=True)
        xs = rx0 + (np.arange(ruta_n, dtype=np.float32) + 0.5) * px
        ys = ry_max - (np.arange(ruta_n, dtype=np.float32) + 0.5) * px
        xx, yy = np.meshgrid(xs, ys)
        # Skålformad sluttning med lågpunkt i mitten så skyfall ger tydligt stående vatten över rutgränsen.
        cx, cy = x0 + sida_m, y0 + 0.5 * sida_m
        terrang = (10.0 + 0.04 * np.hypot(xx - cx, yy - cy)).astype(np.float32)
        markslag = np.ones((ruta_n, ruta_n), dtype=np.uint8)
        r_obj = MarkRuta(
            e_min_m=rx0,
            n_min_m=y0,
            storlek_m=sida_m,
            overlapp_m=0.0,
            upplosning_m=px,
        )
        bygg_i_ruta = [hus_post] if idx == 0 else []
        mark, slag = brann_in_byggnader(
            terrang.copy(),
            markslag.copy(),
            r_obj,
            [b.till_byggnad() for b in bygg_i_ruta],
        )
        skriv_geotiff(rmapp / "terrang.tif", terrang, x_min=rx0, y_max=ry_max, pixelstorlek=px)
        skriv_geotiff(rmapp / "mark.tif", mark.astype(np.float32), x_min=rx0, y_max=ry_max, pixelstorlek=px)
        skriv_geotiff(rmapp / "markslag.tif", slag.astype(np.uint8), x_min=rx0, y_max=ry_max, pixelstorlek=px)
        skriv_geotiff(rmapp / "osakerhet.tif", np.full((ruta_n, ruta_n), 0.1, dtype=np.float32), x_min=rx0, y_max=ry_max, pixelstorlek=px)
        skriv_byggnader_gpkg(rmapp / "byggnader.gpkg", bygg_i_ruta, (rx0, y0, rx0 + sida_m, ry_max))
        # Markera byggnadsceller explicit i markslag när hus finns.
        if bygg_i_ruta:
            assert bool(np.any(slag == BYGGNAD))
        (rmapp / "recept.json").write_text(
            json.dumps({"kontrakt": "K1/v2", "ruta": f"mm_{idx}"}) + "\n", encoding="utf-8"
        )

    k1_lager = lagra(geo_rot, k1_bygge)

    # Syntetiskt NMD-lager (10 m celler) med två tydliga tallskogsfläckar (kod 111, >= 0,5 ha vardera).
    gransen = (x0, y0, x0 + 2.0 * sida_m, y0 + sida_m)
    karna = (x0 + 10.0, y0 + 10.0, x0 + 2.0 * sida_m - 10.0, y0 + sida_m - 10.0)
    buff = 120.0
    cell = 10.0
    nx0, ny0 = gransen[0] - buff, gransen[1] - buff
    nx1, ny1 = gransen[2] + buff, gransen[3] + buff
    nx = int(np.ceil((nx1 - nx0) / cell))
    ny = int(np.ceil((ny1 - ny0) / cell))
    klasser = np.full((ny, nx), 41, dtype=np.uint8)  # öppen mark
    c_start = int((karna[0] - nx0) / cell)
    r_start = int((ny0 + ny * cell - karna[3]) / cell)
    # Fläck 1 (8x8 = 0,64 ha) sträcker sig in i kärnans västra halva; fläck 2 (8x8 = 0,64 ha) österut.
    klasser[r_start - 4 : r_start + 4, c_start - 4 : c_start + 4] = 111
    klasser[r_start - 4 : r_start + 4, c_start + 6 : c_start + 14] = 111

    nmd_bygge = tmp_path / "bygge_nmd"
    nmd_bygge.mkdir()
    skriv_nmd(
        NmdLager(klasser=klasser, x_min_m=nx0, y_max_m=ny0 + ny * cell, cell_m=cell),
        nmd_bygge / "nmd.tif",
        kaerna=karna,
        buffert_m=buff,
    )
    nmd_lager = lagra(geo_rot, nmd_bygge)

    manifest: dict[str, Any] = {
        "kontrakt": KONTRAKT_GEOGRAFI,
        "id": "sthlm-nmd",
        "version": "v1",
        "namn": "Syntetisk NMD-geografi",
        "kommun": "stockholm",
        "karna_3011": list(karna),
        "gransen_3011": list(gransen),
        "buffert": {"vald_m": 10.0, "tillrinning_nar_kanten": False},
        "forberakningar": {
            "tillrinning": {
                "cell_m": 2.0,
                "celler": 100,
                "yta_m2": 400.0,
                "krav_buffert_m": 10.0,
                "nar_kanten": False,
            }
        },
        "lager": {
            "k1_rutnat": {"sha256": k1_lager.sha256, "rutor": ["mm_0", "mm_1"]},
            "nmd": {"sha256": nmd_lager.sha256, "buffert_m": buff, "cell_m": cell},
        },
        "kallor": {
            "hojd": {"licens": "CC0-1.0"},
            "byggnader": {"licens": "CC0-1.0", "kalla": "wfs"},
            "nmd": {"licens": "CC0-1.0"},
        },
        "oppen_for_mcp": True,
    }
    skriv_manifest(geo_rot, manifest)
    return geo_rot, "sthlm-nmd@v1", karna


def test_b7_geografiprojekt_flerruts_skyfall_idf_och_ekologi(tmp_path: Path) -> None:
    """Geografiprojekt får flerruts-skyfall med IDF (Stockholm), genererar `ekologi.toml` och kör `kor_ekologi` utan 404."""
    geo_rot, nmd_ref, karna = _bygg_syntetisk_flerruts_geografi_med_nmd(tmp_path)
    avgr = (karna[0] + 4.0, karna[1] + 4.0, karna[2] - 4.0, karna[3] - 4.0)
    projektrot = tmp_path / "projekt"

    # 1. Stockholms kommunfil -> flerruts-skyfall med registrerad IDF-källa `stockholmsregn-2024` + ekologi.toml.
    sthlm_riktig = las_kommun("stockholm", rot=REPO)
    sthlm = replace(
        sthlm_riktig,
        dagsljus={
            **sthlm_riktig.dagsljus,
            "cellstorlek_m": 2,
            "azimutriktningar": 36,
            "steg_min": 60,
        },
    )
    res_sthlm = skapa_projekt(
        sthlm,
        ref=nmd_ref,
        avgransning=avgr,
        namn="Sthlm NMD",
        konto_id=None,
        geo_rot=geo_rot,
        projektrot=projektrot,
        konfig={},
        konfig_fil=tmp_path / "config.toml",
        skriv=lambda _: None,
    )
    assert not (res_sthlm.mapp / "k1_vatten").exists()
    assert not any("räknar på en ruta" in w for w in res_sthlm.varningar)

    f_sky = las_fraga(res_sthlm.mapp / "fragor" / "skyfall.toml")
    f_dag = las_fraga(res_sthlm.mapp / "fragor" / "dagsljus.toml")
    f_eko = las_fraga(res_sthlm.mapp / "fragor" / "ekologi.toml")
    assert f_sky.scenarier["bas"].bas.resolve() == f_dag.scenarier["bas"].bas.resolve()
    assert f_eko.scenarier["bas"].bas.resolve() == f_dag.scenarier["bas"].bas.resolve()
    assert len(list(f_sky.scenarier["bas"].bas.glob("mm_*/mark.tif"))) == 2

    sky_par = f_sky.analyser[0].parametrar
    assert sky_par["idf"] == "stockholmsregn-2024"
    assert float(sky_par["aterkomsttid_ar"]) == pytest.approx(100.0)
    assert float(sky_par["varaktighet_min"]) == pytest.approx(360.0)
    assert float(sky_par["klimatfaktor"]) == pytest.approx(1.4)
    assert sky_par["regntyp"] == "cds"
    assert sky_par["takvatten"] is True
    validerat = StatiskFyllning().validera_parametrar(sky_par)
    assert validerat["idf_kalla"] == "stockholmsregn-2024"

    # Göteborg (utan lokal IDF-tabell) använder också flerruts-K1 + varaktighet_min + takvatten.
    gbg = las_kommun("goteborg", rot=REPO)
    res_gbg = skapa_projekt(
        gbg,
        ref=nmd_ref,
        avgransning=avgr,
        namn="Gbg NMD",
        konto_id=None,
        geo_rot=geo_rot,
        projektrot=projektrot,
        konfig={},
        konfig_fil=tmp_path / "config.toml",
        skriv=lambda _: None,
    )
    f_gbg_sky = las_fraga(res_gbg.mapp / "fragor" / "skyfall.toml")
    assert len(list(f_gbg_sky.scenarier["bas"].bas.glob("mm_*/mark.tif"))) == 2
    assert float(f_gbg_sky.analyser[0].parametrar["varaktighet_min"]) == pytest.approx(360.0)
    assert f_gbg_sky.analyser[0].parametrar["takvatten"] is True

    # Ekologifrågan pekar på geografins manifest och kommunens serverförval.
    eko_analys = f_eko.analyser[0]
    assert eko_analys.losare == "ekologi.konnektivitet"
    assert eko_analys.parametrar["natverk"] == "barrskog-nacka"
    assert "geografi" in eko_analys.indata and eko_analys.indata["geografi"].is_file()

    # 2. Projektkalla & Kartverktyg: ekologi laddas och `kor_ekologi` + `/api/analys/ekologi` fungerar utan 404.
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
    info = app.kartverktyg.projektinfo(res_sthlm.projekt_id)
    assert set(info["baser"]) == {"vatten", "dagsljus", "ekologi"}
    assert info["regn_forval"] is not None
    assert info["regn_forval"]["kalla"] == "stockholmsregn-2024"
    assert info["regn_forval"]["aterkomsttid_ar"] == pytest.approx(100.0)
    assert info["regn_forval"]["varaktighet_min"] == pytest.approx(360.0)
    assert info["regn_forval"]["klimatfaktor"] == pytest.approx(1.4)

    # Nulägesanrop (utan scenario) via kartverktyg.kor_ekologi + ekologi_svar -> ingen 404!
    k_nul = app.kartverktyg.kor_ekologi(res_sthlm.projekt_id, None, {})
    assert k_nul["status"]["status"] == "klar", k_nul
    svar_nul = ekologi_svar(
        app.fragor,
        korning_id=k_nul["korning_id"],
        analys=k_nul["analys"],
        omrade_id=res_sthlm.projekt_id,
        scenario=None,
        antal_flaskhalsar=5,
        flaskhals_avstand_m=20.0,
        max_flackar=40,
    )
    assert svar_nul["format"] == "floden-ekologi/v1"
    assert svar_nul["fore"]["ECA_m2"] > 0.0
    assert svar_nul["delta_eca_m2"] == pytest.approx(0.0, abs=1e-9)
    assert svar_nul["lager"]["stromtathet_skillnad"] is not None
    assert svar_nul["lager"]["kansighet_skillnad"] is not None

    # Med ett sparat kartverktygsscenario (nytt hus inne i ena skogsfläcken) -> ingen 404 och minskad ECA.
    hx0, hy0 = karna[0] + 6.0, karna[3] - 32.0
    sc = app.kartverktyg.spara_scenario(
        res_sthlm.projekt_id,
        None,
        {
            "namn": "Skogshus",
            "andringar": [
                {
                    "typ": "byggnad_ny",
                    "andring_id": "hus-skog",
                    "geometri": {
                        "type": "Polygon",
                        "coordinates": [
                            [[hx0, hy0], [hx0 + 24.0, hy0], [hx0 + 24.0, hy0 + 24.0], [hx0, hy0 + 24.0], [hx0, hy0]]
                        ],
                    },
                    "egenskaper": {"hojd_m": 15.0},
                }
            ],
        },
    )
    k_sc = app.kartverktyg.kor_ekologi(res_sthlm.projekt_id, sc["slug"], {})
    assert k_sc["status"]["status"] == "klar", k_sc
    svar_sc = ekologi_svar(
        app.fragor,
        korning_id=k_sc["korning_id"],
        analys=k_sc["analys"],
        omrade_id=res_sthlm.projekt_id,
        scenario=sc["slug"],
        antal_flaskhalsar=5,
        flaskhals_avstand_m=20.0,
        max_flackar=40,
    )
    assert svar_sc["scenario"] == sc["slug"]
    assert svar_sc["fore"]["ECA_m2"] > 0.0
    assert svar_sc["efter"]["ECA_m2"] < svar_sc["fore"]["ECA_m2"]

    # 3. `kor_projekt` kör skyfall (flerruts med Stockholmsregn 2024), dagsljus OCH ekologi till "klar".
    kor = kor_projekt(res_sthlm.mapp, rot=tmp_path, skriv=lambda _: None, tjanst=app.fragor)
    assert {k: v["status"] for k, v in kor.items()} == {
        "skyfall": "klar",
        "dagsljus": "klar",
        "ekologi": "klar",
    }


def test_b7_kor_ekologi_med_symlankad_projekt_och_geografirot(tmp_path: Path) -> None:
    """`kartverktyg._rel` löser symlänkar i både `projektrot` och `geo_rot` så `kor_ekologi` aldrig ger trasig relativsökväg."""
    verklig = tmp_path / "verklig"
    verklig.mkdir()
    geo_rot, nmd_ref, karna = _bygg_syntetisk_flerruts_geografi_med_nmd(verklig)
    projektrot = verklig / "projekt"
    projektrot.mkdir()

    # Symlänka både geo-rot och projekt-rot (motsvarar t.ex. /var -> /private/var eller symlänkad datavolym).
    lank_mapp = tmp_path / "lank"
    lank_mapp.mkdir()
    geo_lank = lank_mapp / "geo"
    projekt_lank = lank_mapp / "projekt"
    geo_lank.symlink_to(geo_rot, target_is_directory=True)
    projekt_lank.symlink_to(projektrot, target_is_directory=True)

    sthlm = las_kommun("stockholm", rot=REPO)
    avgr = (karna[0] + 4.0, karna[1] + 4.0, karna[2] - 4.0, karna[3] - 4.0)
    res = skapa_projekt(
        sthlm,
        ref=nmd_ref,
        avgransning=avgr,
        namn="Symlank NMD",
        konto_id=None,
        geo_rot=geo_lank,
        projektrot=projekt_lank,
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
        geomapp=geo_lank,
        projektmapp=projekt_lank,
    )
    k = app.kartverktyg.kor_ekologi(res.projekt_id, None, {})
    assert k["status"]["status"] == "klar", k
    svar = ekologi_svar(
        app.fragor,
        korning_id=k["korning_id"],
        analys=k["analys"],
        omrade_id=res.projekt_id,
        scenario=None,
        antal_flaskhalsar=5,
        flaskhals_avstand_m=20.0,
        max_flackar=40,
    )
    assert svar["fore"]["ECA_m2"] > 0.0

