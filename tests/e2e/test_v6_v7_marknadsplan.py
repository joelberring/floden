"""Korsvågs end-to-end-testsvit för Våg 6 (#165–#168) + Våg 7 (#169–#172).

Fem snabba, helt deterministiska, integrerade end-to-end-tester **utan mockad fysik**
och utan nätverkstrafik (all intern geometri i SWEREF 99 18 00 / EPSG:3011 och RH 2000):

1. **U14 Webb 2D + 1D-rännsten + B11 sektion:**
   Kör `skyfall_metod="2d"` via `FlodenApiApp` / `KartverktygTjanst` på ett litet syntetiskt
   K1 v2-kvarter med 3D-kantsten, byggnad och `NaN`-celler; verifierar `max_djup`, `hastighet`,
   `fara == max_djup * hastighet` (inom `1e-5` med `NaN`-bevarande), 1D-rännstensvektor
   (`rannsten_flode`), statussvarets `skyfall_metod == "2d"`, samt B11 `sektion` (400 på
   `fara`/`hastighet`/`ankomsttid`, 200 på `max_djup`).
2. **U15 UTCI + U16 GYF live API på 2-rutors geografiprojekt med RBAC:**
   Syntetiskt 2-rutors K1 v2-geografiprojekt med byggnader, grönyta, hårdgjord yta och
   vegetation; testar `GET /api/analys/gyf` (`lasare` 200 utan diskmutering, utomstående 403,
   oinloggad 401), `POST /api/analys/gyf` före/efter (ny byggnad → `delta_gyf < 0`, grönyta+träd
   → `delta_gyf > 0`), `POST /api/analys/varmestress` med verklig `dagsljus` SVF/skugga
   (`utci_medel_c`, `tmrt_max_c`, kylning av grönyta+träd, takmaskad PNG med RBAC), samt B11
   `sektion` (400 på `utci_c`/`t_mrt_c`, 200 på `max_djup`).
3. **U17 CAD/BIM helkedja (IFC → GPKG → DXF → LandXML 1.2 → binär GLB):**
   Importerar riktig IFC4 STEP-SPF-byggnad i mm med nästlad 90°-rotation via
   `lapp.cad_bim.importera_ifc_byggnader`, exporterar till GeoPackage i EPSG:3006 och återläser
   med automatisk reprojektion till EPSG:3011, rundtrip-exporterar till DXF, exporterar
   mark+byggnad med `NaN`-cell och brytlinje till LandXML 1.2 och återläser via
   `lapp.landxml.las_landxml_tin`, samt exporterar till binär `.glb` (glTF 2.0 magic +
   EPSG:3011 `asset.extras`) och verifierar exakt geometribevarande genom hela kedjan.
4. **U13 Buller K21 (`buller.nord2000`) före/efter bullerskärm:**
   Kör K21-lösaren `buller.nord2000` genom `kor_utredning` på K1 v2 med byggnad före/efter
   K9-bullerskärm; verifierar flera dB(A) skärmdämpning bakom skärmen, `NaN` i byggnadsceller,
   fasadpunkter samt hårda invarianter (`nord2000_konform is False`, `rapport_sparrad is True`,
   `metodstatus == "skiss"`).
5. **U12c CEDVAL A1-1 vindtunnelvalidering + syntetisk CSV + K37-JSON:**
   Kör `kor_cedval_a11_validering(cellstorlek_m=5.0, losare="snabbvind")` och
   `jamfor_mot_cedval_csv` mot syntetisk CSV i `tmp_path`; verifierar no-slip
   (`max_hastighet_i_solid_m_s == 0.0`), masskonservering, `jamford_mot_vindtunnel_data is False`,
   COST 732-mått och giltig K37-JSON kontrollerad med `benchmark.format.kontrollera`.
"""

from __future__ import annotations

import csv
import json
import math
import struct
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
import shapely
from PIL import Image
from shapely.geometry import LineString, Point, Polygon

from benchmark.format import kontrollera as kontrollera_k37
from gemensamt.hash import sha256_fil
from geografi.modell import KONTRAKT_GEOGRAFI, lager_mapp, lagra, skriv_manifest
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.scenario import Andring, Planscenario, skriv_scenario
from karna.utredning import fraga_fran_dict
from lapp.cad_bim import (
    K50Brytlinje,
    K50Markyta,
    K50Modell,
    K50Trad,
    exportera_3d_glb,
    exportera_k50_till_dxf,
    exportera_k50_till_gpkg,
    exportera_landxml_tin,
    importera_dxf_till_k50,
    importera_gpkg_till_k50,
    importera_ifc_byggnader,
)
from lapp.landxml import las_landxml_tin, rastrera_landxml_tin
from luft.cedval import (
    CEDVAL_FALL_ID,
    K37_FORMAT,
    cedval_till_dict,
    jamfor_mot_cedval_csv,
    kor_cedval_a11_validering,
)
from markmodell.brytlinjer import Brytlinje
from markmodell.byggnadsregister import ByggnadsKandidat, ByggnadsPost, skriv_byggnader_gpkg
from markmodell.hinder_oppningar import HARDGJORT, brann_in_byggnader
from markmodell.k1_skrivare import skriv_brytlinjer_gpkg
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta
from tests.konto.conftest import (  # noqa: F401
    ANNA,
    ORIGIN,
    Miljo,
    Webblasare,
    idp,
    klocka,
    miljo,
)
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo, las_geotiff, skriv_geotiff
from webb.api import FlodenApiApp
from webb.fragor import LAGER_VERSION
from webb.projektkalla import KONTRAKT_PROJEKT
from webb.resultatlager import Mosaik, Utbredning, fara_mosaik

REPO = Path(__file__).resolve().parents[2]

BO = {
    "sub": "sub-bo-v8-e2e",
    "email": "bo@example.test",
    "email_verified": True,
    "name": "Bo Lasare",
}
DI = {
    "sub": "sub-di-v8-e2e",
    "email": "di@example.test",
    "email_verified": True,
    "name": "Di Utomstaende",
}


# ---------------------------------------------------------------------------
# Hjälpare
# ---------------------------------------------------------------------------


def _json_forfragan(
    app: FlodenApiApp,
    metod: str,
    sokvag: str,
    kropp: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
    status, _, ut = app.hantera_forfragan(metod, sokvag, data)
    return status, json.loads(ut.decode("utf-8")) if ut else {}


def _http_raw(
    app: FlodenApiApp,
    webb: Webblasare | None,
    metod: str,
    sokvag: str,
    kropp: Any = None,
) -> tuple[int, dict[str, str], bytes]:
    rub: dict[str, str] = {}
    if webb is not None:
        rub["Cookie"] = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
        if metod != "GET":
            rub["Origin"] = ORIGIN
            rub.update(webb.csrf_headers())
    data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
    status, headers, raw = app.hantera_http(metod, sokvag, rub, lambda: data)
    return status, {k.lower(): v for k, v in headers}, raw


def _http_json(
    app: FlodenApiApp,
    webb: Webblasare | None,
    metod: str,
    sokvag: str,
    kropp: Any = None,
) -> tuple[int, dict[str, Any]]:
    status, _, raw = _http_raw(app, webb, metod, sokvag, kropp)
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def _vanta_klar(app: FlodenApiApp, korning_id: str, tidsgrans_s: float = 60.0) -> dict[str, Any]:
    slut = time.monotonic() + tidsgrans_s
    while time.monotonic() < slut:
        st = app.fragor.korning_status(korning_id)
        assert st["status"] != "fel", st
        if st["status"] == "klar":
            return app.fragor.resultat(korning_id)
        time.sleep(0.05)
    raise AssertionError(f"Körning {korning_id} hann inte bli klar inom {tidsgrans_s} s.")


def _projektfiler_snapshot(pmapp: Path) -> dict[str, tuple[int, int]]:
    ut: dict[str, tuple[int, int]] = {}
    for p in sorted(pmapp.rglob("*")):
        if p.is_file():
            st = p.stat()
            ut[p.relative_to(pmapp).as_posix()] = (st.st_size, st.st_mtime_ns)
    return ut


def _sampla_cell(fil: Path, e_m: float, n_m: float) -> float:
    arr, geo = las_geotiff(fil)
    kol = int(np.floor((e_m - geo.x_min) / geo.pixelstorlek_x))
    rad = int(np.floor((geo.y_max - n_m) / geo.pixelstorlek_y))
    assert 0 <= rad < geo.hojd and 0 <= kol < geo.bredd
    return float(arr[rad, kol])


# ---------------------------------------------------------------------------
# Test 1: U14 Webb 2D + 1D-rännsten + B11 sektion
# ---------------------------------------------------------------------------


def test_e2e_v6_u14_webb_2d_rannsten_fara_status_och_b11_sektion(tmp_path: Path) -> None:
    """U14 end-to-end: `skyfall_metod="2d"` genom `FlodenApiApp`/`KartverktygTjanst` på
    syntetiskt K1 v2-kvarter med 3D-kantsten, byggnad och `NaN`, verifierar `fara == h * v`
    (inom `1e-5`), `rannsten_flode`, status `skyfall_metod == "2d"` och B11 `sektion`."""
    e0, n0 = 150200.0, 6580200.0
    storlek_m, dx = 48.0, 2.0  # 24×24 = 576 celler (blixtsnabb riktig 2D+1D-fysik)
    ruta = Ruta(e_min_m=e0, n_min_m=n0, storlek_m=storlek_m, overlapp_m=0.0, upplosning_m=dx)
    ex, ny = ruta.cell_centra_en()
    gx, gy = np.meshgrid(ex, ny)

    # Skålformad innergård med tydlig lutning mot sänka (ger mätbart djup och flödeshastighet)
    z = (11.20 + 0.015 * np.hypot(gx - (e0 + 24.0), gy - (n0 + 20.0))).astype(np.float32)
    z[(gx >= e0 + 18.0) & (gx <= e0 + 30.0) & (gy >= n0 + 14.0) & (gy <= n0 + 26.0)] = np.float32(
        10.50
    )
    # Två explicita NaN-celler i sydvästra hörnet för att pröva äkta NaN-bevarande i fara-lagret
    z[-2:, 0:1] = np.nan
    slag = np.full(ruta.form_rc, HARDGJORT, dtype=np.uint8)

    hus = ByggnadsKandidat(
        byggnad_id="hus_e2e_2d",
        polygon=(
            (e0 + 32.0, n0 + 8.0),
            (e0 + 42.0, n0 + 8.0),
            (e0 + 42.0, n0 + 22.0),
            (e0 + 32.0, n0 + 22.0),
        ),
        tak_rh2000=24.0,
        hojd_m=12.0,
        kalla="syntetisk_v6_e2e",
    )

    geo_rot = tmp_path / "geo"
    proj_rot = tmp_path / "projekt"
    pmapp = proj_rot / "kvarter-v6-2d"
    k1_mapp = tmp_path / "bygge_k1_2d"
    bygg_markmodell_ruta(
        ruta,
        k1_mapp,
        grund_z=z,
        markslag_grund=slag,
        byggnadskandidater=(hus,),
        byggnadshojd_fran_laser=False,
        kodversion="v8-e2e",
        start_tid="2026-11-26T12:00:00+00:00",
    )
    ruta_mapp = k1_mapp / ruta.namn

    # 3D-kantsten i norra gatan så `vatten.2d` med `rannsten_1d="auto"` aktiverar 1D-rännstenslänken
    skriv_brytlinjer_gpkg(
        ruta_mapp / "brytlinjer.gpkg",
        [
            Brytlinje(
                typ="kantsten",
                hojd_m=0.14,
                punkter=((e0 + 6.0, n0 + 40.0), (e0 + 42.0, n0 + 40.0)),
                z_kron_rh2000=(11.74, 11.54),
                z_botten_rh2000=(11.60, 11.40),
                tvarfall=0.025,
                kalla="baskarta_3d",
                osakerhet_m=0.02,
            )
        ],
        ruta,
    )

    k1_lager = lagra(geo_rot, k1_mapp)
    k1_lagrad = lager_mapp(geo_rot, k1_lager.sha256)
    _, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "syntgeo-v6-2d",
            "version": "v1",
            "kommun": "stockholm",
            "karna_3011": [e0, n0, e0 + storlek_m, n0 + storlek_m],
            "gransen_3011": [e0, n0, e0 + storlek_m, n0 + storlek_m],
            "lager": {"k1_rutnat": {"sha256": k1_lager.sha256}},
            "kallor": {
                "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk v6"},
                "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk v6"},
            },
            "oppen_for_mcp": True,
        },
    )

    fdir = pmapp / "fragor"
    fdir.mkdir(parents=True)
    (pmapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
        encoding="utf-8",
    )
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": "kvarter-v6-2d",
                "namn": "Kvarteret V6 2D",
                "kommun": "stockholm",
                "geografi": "syntgeo-v6-2d@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": [e0, n0, e0 + storlek_m, n0 + storlek_m],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (fdir / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\n'
        'fraga_id = "kvarter-v6-2d-skyfall"\n'
        'beskrivning = "Skyfall Kvarteret V6 2D"\n\n'
        f'[scenarier.bas]\nbas = "{k1_lagrad.as_posix()}"\n\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 35.0 }\n",
        encoding="utf-8",
    )

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        kartverktygsmapp=tmp_path / "kv",
        geomapp=geo_rot,
        projektmapp=proj_rot,
        datamappar=[tmp_path],
    )
    bas_url = "/api/kartverktyg/kvarter-v6-2d"
    begaran = {
        "analyser": ["vatten.skyfall"],
        "regn": {"regn_mm": 35, "varaktighet_min": 5},
        "skyfall_metod": "2d",
    }

    # 1. Planera med skyfall_metod="2d" -> enbart vatten.2d-jobb
    st_plan, plan = _json_forfragan(app, "POST", f"{bas_url}/lage-idag/plan", begaran)
    assert st_plan == 200, plan
    jobb = plan["fragor"][0]["plan"]["jobb"]
    assert len(jobb) == 1
    assert jobb[0]["losare"] == "vatten.2d"

    # 2. Kör verklig 2D + 1D-rännsten genom KartverktygTjanst
    st_kor, kor = _json_forfragan(app, "POST", f"{bas_url}/lage-idag/kor", begaran)
    assert st_kor == 202, kor
    kid = kor["fragor"][0]["korning"]["korning_id"]
    res = _vanta_klar(app, kid)

    analys_post = next(a for a in res["antaganden"]["analyser"] if a["losare"] == "vatten.2d")
    assert analys_post["parametrar"].get("rannsten_1d") == "auto"

    # 3. Verifiera utdataroller: max_djup, hastighet, ankomsttid, fara
    roller = {la["roll"]: la for la in res["lager"] if la["typ"] == "bas"}
    assert {"max_djup", "hastighet", "ankomsttid", "fara"}.issubset(roller)
    assert roller["fara"]["enhet"] == "m²/s"
    assert roller["fara"]["harledd"] is True

    st_png, _, png_bytes = app.hantera_forfragan("GET", roller["fara"]["png"], b"")
    assert st_png == 200
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    # 4. Verifiera fara == max_djup * hastighet inom 1e-5 med exakt NaN-bevarande
    k_post = app.fragor._hamta(kid)  # noqa: SLF001 - direkt verifiering mot råa GeoTIFF/npy
    samm = app.fragor._sammanstallning(k_post)  # noqa: SLF001
    j0 = next(j for j in samm["jobb"] if j["losare"] == "vatten.2d")
    jmapp = app.fragor._jobbmapp(k_post, j0)  # noqa: SLF001
    with (
        rasterio.open(jmapp / "max_djup.tif") as ds_h,
        rasterio.open(jmapp / "hastighet.tif") as ds_v,
    ):
        h_arr = ds_h.read(1).astype(np.float64)
        v_arr = ds_v.read(1).astype(np.float64)
        if ds_h.nodata is not None:
            h_arr[h_arr == ds_h.nodata] = np.nan
        if ds_v.nodata is not None:
            v_arr[v_arr == ds_v.nodata] = np.nan
        tr = ds_h.transform

    fara_arr = np.load(k_post.mapp / "webb" / f"{roller['fara']['id']}.npy").astype(np.float64)
    forvantad = np.where(np.isfinite(h_arr) & np.isfinite(v_arr), h_arr * v_arr, np.nan)
    assert fara_arr.shape == forvantad.shape
    np.testing.assert_allclose(fara_arr, forvantad, rtol=0.0, atol=1e-5, equal_nan=True)

    # Pröva även explicit NaN-bevarande i `fara_mosaik` på de verkliga 2D-rastren
    h_med_nan = h_arr.astype(np.float32).copy()
    v_med_nan = v_arr.astype(np.float32).copy()
    h_med_nan[0, 0] = np.nan
    v_med_nan[0, 1] = np.nan
    utb = Utbredning(e0, n0, e0 + storlek_m, n0 + storlek_m)
    fara_nan = fara_mosaik(Mosaik(h_med_nan, utb, dx), Mosaik(v_med_nan, utb, dx))
    assert fara_nan is not None
    assert np.isnan(fara_nan.raster[0, 0]) and np.isnan(fara_nan.raster[0, 1])
    forv_nan = np.where(
        np.isfinite(h_med_nan) & np.isfinite(v_med_nan),
        h_med_nan.astype(np.float64) * v_med_nan.astype(np.float64),
        np.nan,
    )
    np.testing.assert_allclose(
        fara_nan.raster.astype(np.float64), forv_nan, rtol=0.0, atol=1e-5, equal_nan=True
    )

    giltiga = np.isfinite(fara_arr) & (h_arr > 0.005) & (v_arr > 0.0)
    assert int(np.sum(giltiga)) > 0, "2D-körningen ska ge våta celler med positiv fara"
    idx_r, idx_c = np.argwhere(giltiga)[0]
    e_pt, n_pt = rasterio.transform.xy(tr, int(idx_r), int(idx_c))
    st_avl, avl = _json_forfragan(
        app,
        "GET",
        f"/api/korningar/{kid}/avlasning?lager={roller['fara']['id']}&e={e_pt}&n={n_pt}",
    )
    assert st_avl == 200, avl
    v_fara = next(x for x in avl["varden"] if x["lager_id"] == roller["fara"]["id"])
    assert v_fara["inom"] is True
    assert v_fara["varde"] == pytest.approx(
        float(h_arr[idx_r, idx_c] * v_arr[idx_r, idx_c]), abs=1e-4
    )

    # 5. Verifiera 1D-rännsten (`rannsten_flode` vektorlager + GeoJSON utan påhittat `id`-fält)
    vektor_poster = [v for v in res.get("vektorlager", []) if v["roll"] == "rannsten_flode"]
    assert len(vektor_poster) == 1, res.get("vektorlager")
    assert "id" not in vektor_poster[0]
    st_vec, fc = _json_forfragan(app, "GET", vektor_poster[0]["vektor_url"])
    assert st_vec == 200, fc
    assert fc["type"] == "FeatureCollection"
    assert fc["slag"] == "rannsten_flode"
    assert len(fc["features"]) > 0
    assert fc["features"][0]["geometry"]["type"] == "LineString"
    props0 = fc["features"][0]["properties"]
    assert props0["max_flode_m3_s"] >= 0.0
    assert props0["max_djup_m"] >= 0.0
    assert props0["langd_m"] > 0.0

    # 6. Statussvar återger skyfall_metod == "2d"
    st_stat, status_svar = _json_forfragan(app, "GET", f"{bas_url}/lage-idag/status")
    assert st_stat == 200, status_svar
    vatten_st = next(k for k in status_svar["korningar"] if k["mall"] == "vatten")
    assert vatten_st["skyfall_metod"] == "2d"
    assert vatten_st["regn"]["skyfall_metod"] == "2d"

    # 7. B11 sektion: 200 på max_djup, 400 på fara, hastighet och ankomsttid
    linje = [[e0 + 4.0, n0 + 20.0], [e0 + 44.0, n0 + 20.0]]
    st_ok, sek_ok = _json_forfragan(
        app,
        "POST",
        f"{bas_url}/sektion",
        {"punkter": linje, "korning_id": kid, "fore_lager": roller["max_djup"]["id"]},
    )
    assert st_ok == 200, sek_ok
    assert sek_ok["kontrakt"] == "floden-sektion/v1"
    for nekad_roll in ("fara", "hastighet", "ankomsttid"):
        st_nej, fel = _json_forfragan(
            app,
            "POST",
            f"{bas_url}/sektion",
            {"punkter": linje, "korning_id": kid, "fore_lager": roller[nekad_roll]["id"]},
        )
        assert st_nej == 400, (nekad_roll, fel)


# ---------------------------------------------------------------------------
# Test 2: U15 UTCI + U16 GYF live API på 2-rutors geografiprojekt med RBAC
# ---------------------------------------------------------------------------


def test_e2e_v7_u15_u16_gyf_utci_tvarut_geografiprojekt_rbac_och_b11(
    tmp_path: Path,
    miljo: Miljo,  # noqa: F811
) -> None:
    """U15 + U16 end-to-end på 2-rutors geografiprojekt med verklig RBAC (`anna`/`bo`/`di`),
    skrivlös GET för `lasare`, fysikaliska före/efter-tecken i GYF och UTCI/T_mrt, takmaskad
    PNG samt B11-sektionsskydd för `utci_c` och `t_mrt_c`."""
    e0, n0 = 150400.0, 6580400.0
    storlek_m, dx = 60.0, 2.0  # Två 30×30-rutor sida vid sida -> 60×30 = 1 800 celler totalt
    r_vast = Ruta(e_min_m=e0, n_min_m=n0, storlek_m=storlek_m, overlapp_m=0.0, upplosning_m=dx)
    r_ost = Ruta(
        e_min_m=e0 + storlek_m, n_min_m=n0, storlek_m=storlek_m, overlapp_m=0.0, upplosning_m=dx
    )
    x0, y0, x1, y1 = e0, n0, e0 + 2.0 * storlek_m, n0 + storlek_m

    def _bygg_ruta_data(
        ruta: Ruta,
    ) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], np.ndarray[Any, Any]]:
        ex, ny = ruta.cell_centra_en()
        gx, gy = np.meshgrid(ex, ny)
        z = (12.0 + 0.01 * (gx - x0)).astype(np.float32)
        # Södra halvan grönyta (markslag 4), norra halvan hårdgjord (markslag 1)
        slag = np.where(gy < n0 + 30.0, np.uint8(4), np.uint8(1)).astype(np.uint8)
        veg = np.zeros(ruta.form_rc, dtype=np.float32)
        # Befintlig träddunge i sydvästra hörnet av respektive ruta
        veg[(gx < ruta.e_min_m + 18.0) & (gy < n0 + 18.0)] = np.float32(7.0)
        return z, slag, veg

    z_v, slag_v, veg_v = _bygg_ruta_data(r_vast)
    z_o, slag_o, veg_o = _bygg_ruta_data(r_ost)

    hus_v = ByggnadsKandidat(
        byggnad_id="hus_vast_v7",
        polygon=(
            (e0 + 22.0, n0 + 14.0),
            (e0 + 38.0, n0 + 14.0),
            (e0 + 38.0, n0 + 28.0),
            (e0 + 22.0, n0 + 28.0),
        ),
        tak_rh2000=28.0,
        hojd_m=16.0,
        kalla="syntetisk_v7_e2e",
    )
    hus_o = ByggnadsKandidat(
        byggnad_id="hus_ost_v7",
        polygon=(
            (e0 + 82.0, n0 + 14.0),
            (e0 + 98.0, n0 + 14.0),
            (e0 + 98.0, n0 + 28.0),
            (e0 + 82.0, n0 + 28.0),
        ),
        tak_rh2000=30.0,
        hojd_m=18.0,
        kalla="syntetisk_v7_e2e",
    )

    k1_tmp = tmp_path / "bygge_k1"
    for r, zr, sr, vr, hr in (
        (r_vast, z_v, slag_v, veg_v, hus_v),
        (r_ost, z_o, slag_o, veg_o, hus_o),
    ):
        bygg_markmodell_ruta(
            r,
            k1_tmp,
            grund_z=zr,
            markslag_grund=sr,
            byggnadskandidater=(hr,),
            byggnadshojd_fran_laser=False,
            kodversion="v8-e2e",
            start_tid="2026-11-26T12:00:00+00:00",
        )
        skriv_geotiff(
            k1_tmp / r.namn / "vegetation.tif",
            vr,
            x_min=r.e_min_m,
            y_max=r.n_min_m + r.storlek_m,
            pixelstorlek=dx,
        )

    geo_rot = tmp_path / "geo"
    proj_rot = tmp_path / "projekt"
    k1_lager = lagra(geo_rot, k1_tmp)
    k1_lagrad = lager_mapp(geo_rot, k1_lager.sha256)
    _, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "syntgeo-v8-tvarut",
            "version": "v1",
            "kommun": "stockholm",
            "karna_3011": [x0 + 4.0, y0 + 4.0, x1 - 4.0, y1 - 4.0],
            "gransen_3011": [x0, y0, x1, y1],
            "lager": {"k1_rutnat": {"sha256": k1_lager.sha256}},
            "kallor": {
                "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk v8"},
                "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk v8"},
            },
            "oppen_for_mcp": True,
        },
    )

    # RBAC-konton: anna = utredare/admin, bo = lasare, di = utomstående i annan organisation
    reg = miljo.register
    org1 = reg.skapa_organisation("V8Kommun")
    org2 = reg.skapa_organisation("AnnanKommunV8")
    anna_id = reg.forbered_konto("anna@example.test")
    reg.gor_organisationsadmin(anna_id, org1)
    bo_id = reg.forbered_konto("bo@example.test")
    reg.lagg_till_medlem(bo_id, org1, None, "test")
    di_id = reg.forbered_konto("di@example.test")
    reg.gor_organisationsadmin(di_id, org2)

    pid, _ = reg.registrera_projekt_for_konto(anna_id, org1, "Tvarut V7 E2E")
    reg.bjud_in(anna_id, pid, "bo@example.test", "lasare")

    webb: dict[str, Webblasare] = {}
    for namn, claims in (("anna", ANNA), ("bo", BO), ("di", DI)):
        w = miljo.webblasare()
        assert w.logga_in(miljo.idp, claims)[0] == 302
        webb[namn] = w

    pmapp = proj_rot / "tvarut-v7-e2e"
    fdir = pmapp / "fragor"
    fdir.mkdir(parents=True)
    # Avgränsning som spänner rakt över rutskarven (100 m × 48 m = 4 800 m²)
    avgr = [x0 + 10.0, y0 + 6.0, x1 - 10.0, y1 - 6.0]
    (pmapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
        encoding="utf-8",
    )
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": "tvarut-v7-e2e",
                "register_id": pid,
                "namn": "Tvarut V7 E2E",
                "kommun": "stockholm",
                "geografi": "syntgeo-v8-tvarut@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": avgr,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (fdir / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\n'
        'fraga_id = "tvarut-v7-skyfall"\n'
        'beskrivning = "Skyfall 2-rutors V7"\n\n'
        f'[scenarier.bas]\nbas = "{k1_lagrad.as_posix()}"\n\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 40.0 }\n",
        encoding="utf-8",
    )

    bas_recept = tuple(
        sha256_fil(r_dir / "recept.json") for r_dir in sorted(k1_lagrad.glob("mm_*"))
    )
    assert len(bas_recept) == 2

    # Scenario A: ny byggnad placerad på grönyta över rutskarven (minskar GYF)
    k9_hus = pmapp / "k9" / "nytt-hus_vatten.json"
    skriv_scenario(
        Planscenario(
            "nytt-hus",
            bas_recept,
            (
                Andring.fran_dict(
                    {
                        "typ": "byggnad_ny",
                        "andring_id": "hus-skarv",
                        "geometri": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [e0 + 50.0, n0 + 10.0],
                                    [e0 + 70.0, n0 + 10.0],
                                    [e0 + 70.0, n0 + 26.0],
                                    [e0 + 50.0, n0 + 26.0],
                                    [e0 + 50.0, n0 + 10.0],
                                ]
                            ],
                        },
                        "egenskaper": {"hojd_m": 18.0},
                    }
                ),
            ),
            beskrivning="Ny byggnad på grönyta över rutskarven",
        ),
        k9_hus,
    )

    # Scenario B: ny grönyta + 10 m höga trädkronor på norra gården (höjer GYF, kyler UTCI)
    k9_gron = pmapp / "k9" / "gronyta-trad_vatten.json"
    skriv_scenario(
        Planscenario(
            "gronyta-trad",
            bas_recept,
            (
                Andring.fran_dict(
                    {
                        "typ": "markslag",
                        "andring_id": "gronyta-skarv",
                        "geometri": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [e0 + 20.0, n0 + 32.0],
                                    [e0 + 100.0, n0 + 32.0],
                                    [e0 + 100.0, n0 + 52.0],
                                    [e0 + 20.0, n0 + 52.0],
                                    [e0 + 20.0, n0 + 32.0],
                                ]
                            ],
                        },
                        "egenskaper": {"kod": 4},
                    }
                ),
                Andring.fran_dict(
                    {
                        "typ": "vegetation",
                        "andring_id": "trad-skarv",
                        "geometri": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [e0 + 24.0, n0 + 34.0],
                                    [e0 + 96.0, n0 + 34.0],
                                    [e0 + 96.0, n0 + 50.0],
                                    [e0 + 24.0, n0 + 50.0],
                                    [e0 + 24.0, n0 + 34.0],
                                ]
                            ],
                        },
                        "egenskaper": {"hojd_m": 10.0},
                    }
                ),
            ),
            beskrivning="Ny grönyta och trädkronor över båda rutorna",
        ),
        k9_gron,
    )
    (pmapp / "scenarier.json").write_text(
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

    konfig = tmp_path / "config.toml"
    konfig.write_text(
        '[webb]\nfragemappar = ["fragor"]\ncachemapp = "cache"\nkorningsmapp = "korningar"\n',
        encoding="utf-8",
    )
    tom_testsvit = tmp_path / "tom-testsvit"
    tom_testsvit.mkdir()
    app = FlodenApiApp(
        testsvit_rot=tom_testsvit,
        konfig_fil=konfig,
        konto=miljo.adapter,
        geomapp=geo_rot,
        projektmapp=proj_rot,
        kartverktygsmapp=tmp_path / "kartverktyg",
    )

    # 1. RBAC på GET /api/analys/gyf: oinloggad 401, outsider 403, lasare 200 utan diskmutering
    assert _http_json(app, None, "GET", f"/api/analys/gyf?omrade_id={pid}")[0] == 401
    assert _http_json(app, webb["di"], "GET", f"/api/analys/gyf?omrade_id={pid}")[0] == 403
    assert _http_json(app, webb["di"], "POST", "/api/analys/gyf", {"omrade_id": pid})[0] == 403

    snap_fore = _projektfiler_snapshot(pmapp)
    st_gyf, gyf_bo = _http_json(
        app, webb["bo"], "GET", f"/api/analys/gyf?omrade_id={pid}&profil=stockholm"
    )
    assert st_gyf == 200, gyf_bo
    assert gyf_bo["format"] == "floden-gyf-webb/v1"
    assert gyf_bo["metodstatus"] == "skiss"
    assert gyf_bo["fore"]["tomt_area_m2"] == pytest.approx(100.0 * 48.0)
    assert 0.0 < gyf_bo["fore"]["gyf"] < 1.0
    assert _projektfiler_snapshot(pmapp) == snap_fore

    # 2. POST /api/analys/gyf före/efter: exakt fysikaliskt tecken på delta_gyf
    st_hus, gyf_hus = _http_json(
        app,
        webb["anna"],
        "POST",
        "/api/analys/gyf",
        {"omrade_id": pid, "profil": "stockholm", "scenario": "nytt-hus"},
    )
    assert st_hus == 200, gyf_hus
    assert gyf_hus["jamforelse"]["delta_gyf"] < 0.0
    assert gyf_hus["jamforelse"]["delta_gyf"] == pytest.approx(
        gyf_hus["efter"]["gyf"] - gyf_hus["fore"]["gyf"], abs=1e-6
    )

    st_gron, gyf_gron = _http_json(
        app,
        webb["anna"],
        "POST",
        "/api/analys/gyf",
        {"omrade_id": pid, "profil": "stockholm", "scenario": "gronyta-trad"},
    )
    assert st_gron == 200, gyf_gron
    assert gyf_gron["jamforelse"]["delta_gyf"] > 0.0
    assert gyf_gron["jamforelse"]["delta_gyf"] == pytest.approx(
        gyf_gron["efter"]["gyf"] - gyf_gron["fore"]["gyf"], abs=1e-6
    )
    assert any(
        t["tillagg_id"] == "trad_krontackning" and t["area_m2"] > 0.0 for t in gyf_gron["tillagg"]
    )

    # 3. POST /api/analys/varmestress (verklig dagsljus SVF/skugga + takmaskad PNG)
    assert (
        _http_json(app, webb["di"], "POST", "/api/analys/varmestress", {"omrade_id": pid})[0]
        == 403
    )
    st_vs, vs = _http_json(
        app,
        webb["anna"],
        "POST",
        "/api/analys/varmestress",
        {
            "omrade_id": pid,
            "scenario": "gronyta-trad",
            "t_luft_c": 30.0,
            "relativ_fuktighet_pct": 45.0,
            "vind_10m_m_s": 1.5,
            "sol_direkt_w_m2": 750.0,
            "sol_diffus_w_m2": 120.0,
            "solhojd_grad": 50.0,
        },
    )
    assert st_vs == 200, vs
    assert vs["format"] == "floden-varmestress-webb/v1"
    assert vs["metodstatus"] == "skiss"
    assert 0.3 < vs["geometri"]["svf_medel_fore"] < 0.99
    assert 5.0 < vs["geometri"]["solbelyst_andel_fore_pct"] < 99.0
    assert vs["utci_medel_c"] is not None and vs["utci_max_c"] is not None
    assert vs["tmrt_medel_c"] is not None and vs["tmrt_max_c"] is not None
    assert vs["utci_max_c"] >= vs["utci_medel_c"] > 26.0
    assert vs["tmrt_max_c"] >= vs["tmrt_medel_c"] > 35.0
    assert sum(k["andel_pct"] for k in vs["stressklasser"]) == pytest.approx(100.0, abs=1e-4)
    # Trädkronorna över båda rutorna ger mätbar kylning i T_mrt och UTCI
    assert vs["jamforelse"]["delta_tmrt_medel_c"] < -0.5
    assert vs["jamforelse"]["delta_utci_medel_c"] < -0.1

    # Takmaskad PNG: lasare 200 med alfa==0 på hustak och alfa==255 på mark; outsider 403
    for nyckel in ("utci_c_fore", "t_mrt_c_fore"):
        lag = vs["lager"][nyckel]
        assert lag["tak_maskat"] is True
        st_p, hdrs, png_b = _http_raw(app, webb["bo"], "GET", lag["png"])
        assert st_p == 200
        assert png_b.startswith(b"\x89PNG\r\n\x1a\n")
        assert "x-floden-utbredning" in hdrs
        rgba = np.asarray(Image.open(BytesIO(png_b)).convert("RGBA"))
        assert int(np.count_nonzero(rgba[..., 3] == 0)) > 0
        assert int(np.count_nonzero(rgba[..., 3] == 255)) > 0
        assert _http_raw(app, webb["di"], "GET", lag["png"])[0] == 403

    # 4. B11 sektion på 2-rutorsprojektet: 400 på utci_c och t_mrt_c, 200 på max_djup
    kid = "k-v8-b11-utci"
    kmapp = app.fragor.korningsmapp / kid
    utmapp = kmapp / "resultat"
    webbmapp = kmapp / "webb"
    utmapp.mkdir(parents=True)
    webbmapp.mkdir(parents=True)
    u = {"x_min": avgr[0], "y_min": avgr[1], "x_max": avgr[2], "y_max": avgr[3]}
    ny_px, nx_px = 24, 50
    np.save(webbmapp / "skyfall.bas.max_djup.npy", np.full((ny_px, nx_px), 0.12, dtype=np.float32))
    np.save(
        webbmapp / "mikroklimat.bas.utci_c.npy", np.full((ny_px, nx_px), 34.0, dtype=np.float32)
    )
    np.save(
        webbmapp / "mikroklimat.bas.t_mrt_c.npy", np.full((ny_px, nx_px), 52.0, dtype=np.float32)
    )
    (kmapp / "korning.json").write_text(
        json.dumps(
            {
                "korning_id": kid,
                "ref": "tvarut-v7-skyfall",
                "fraga_id": "tvarut-v7-skyfall",
                "fraga_fil": str(fdir / "skyfall.toml"),
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
                "fraga_id": "tvarut-v7-skyfall",
                "beskrivning": "B11 E2E",
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
            "teckenforklaring": {
                "typ": "kontinuerlig",
                "min": 0.0,
                "max": 1.0,
                "stopp": ["#000000", "#ffffff"],
            },
            "utbredning": u,
            "synlig_utbredning": u,
            "pixelstorlek_m": 2.0,
            "bredd_px": nx_px,
            "hojd_px": ny_px,
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

    linje = [[u["x_min"] + 4.0, u["y_min"] + 24.0], [u["x_max"] - 4.0, u["y_min"] + 24.0]]
    st_sek_ok, sek_ok = _http_json(
        app,
        webb["anna"],
        "POST",
        f"/api/kartverktyg/{pid}/sektion",
        {"punkter": linje, "korning_id": kid, "fore_lager": "skyfall.bas.max_djup"},
    )
    assert st_sek_ok == 200, sek_ok
    assert sek_ok["kontrakt"] == "floden-sektion/v1"

    for nekad in ("mikroklimat.bas.utci_c", "mikroklimat.bas.t_mrt_c"):
        st_f, fel_f = _http_json(
            app,
            webb["anna"],
            "POST",
            f"/api/kartverktyg/{pid}/sektion",
            {"punkter": linje, "korning_id": kid, "fore_lager": nekad},
        )
        assert st_f == 400, (nekad, fel_f)
        st_e, fel_e = _http_json(
            app,
            webb["anna"],
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


# ---------------------------------------------------------------------------
# Test 3: U17 CAD/BIM helkedja (IFC -> GPKG -> DXF -> LandXML 1.2 -> GLB)
# ---------------------------------------------------------------------------


def test_e2e_v7_u17_cad_bim_helkedja_ifc_gpkg_dxf_landxml_glb(tmp_path: Path) -> None:
    """U17 helkedja: IFC4 STEP-SPF (mm + 90°-rotation) → GPKG (EPSG:3006 → EPSG:3011)
    → DXF round-trip → LandXML 1.2 mark+byggnad (`las_landxml_tin`) → binär `.glb`
    (glTF 2.0 magic + EPSG:3011 `asset.extras`) med exakt geometribevarande."""
    # 1. IFC STEP-SPF med MILLI (mm) och nästlad 90° CCW-rotation kring Z:
    #    Site: (+10 m, +20 m, +2 m), Building relativt Site: (+6 m, +4 m, +0.5 m), RefDir=(0,1,0)
    #    => Total ram: origo=(16, 24, 2.5), x_hat=(0,1,0), y_hat=(-1,0,0), z_hat=(0,0,1)
    #    Lokal rektangel 12 m × 6 m ([-6,6] × [-3,3]), z_offset=+0.5 m, extrudering=14 m
    #    => Lokal till värld: (16 - py, 24 + px, 2.5 + 0.5 + pz) + ursprung
    #    => E in [13, 19] (+ursprung_e), N in [18, 30] (+ursprung_n), z_bas=13.0, z_tak=27.0
    ifc_fil = tmp_path / "kvarter_mm_rot90.ifc"
    ifc_fil.write_text(
        """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('kvarter_mm_rot90.ifc','2026-11-26T12:00:00',(''),(''),'Floden','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#10=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#11=IFCUNITASSIGNMENT((#10));
#12=IFCPROJECT('0ProjV8',$,'E2E CAD/BIM',$,$,$,$,$,#11);
#20=IFCCARTESIANPOINT((10000.,20000.,2000.));
#21=IFCDIRECTION((0.,0.,1.));
#22=IFCDIRECTION((1.,0.,0.));
#23=IFCAXIS2PLACEMENT3D(#20,#21,#22);
#24=IFCLOCALPLACEMENT($,#23);
#25=IFCSITE('0SiteV8',$,'Tomt',$,$,#24,$,$,.ELEMENT.,$,$,$,$,$);
#30=IFCCARTESIANPOINT((6000.,4000.,500.));
#31=IFCDIRECTION((0.,0.,1.));
#32=IFCDIRECTION((0.,1.,0.));
#33=IFCAXIS2PLACEMENT3D(#30,#31,#32);
#34=IFCLOCALPLACEMENT(#24,#33);
#40=IFCRECTANGLEPROFILEDEF(.AREA.,'HusProfil',$,12000.,6000.);
#41=IFCCARTESIANPOINT((0.,0.,500.));
#42=IFCAXIS2PLACEMENT3D(#41,$,$);
#43=IFCDIRECTION((0.,0.,1.));
#44=IFCEXTRUDEDAREASOLID(#40,#42,#43,14000.);
#45=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#44));
#46=IFCPRODUCTDEFINITIONSHAPE($,$,(#45));
#47=IFCBUILDING('0BldgV8',$,'Hus Rot90',$,$,#34,#46,$,.ELEMENT.,$,$,$);
ENDSEC;
END-ISO-10303-21;
""",
        encoding="utf-8",
    )

    ursprung_e, ursprung_n, ursprung_z = 150000.0, 6580000.0, 10.0
    ifc_res = importera_ifc_byggnader(
        ifc_fil,
        ursprung_e_m=ursprung_e,
        ursprung_n_m=ursprung_n,
        ursprung_z_m=ursprung_z,
    )
    assert ifc_res.langdenhet == "mm"
    assert ifc_res.langd_skala_till_m == pytest.approx(0.001)
    assert len(ifc_res.byggnader) == 1
    b_ifc = ifc_res.byggnader[0]
    assert b_ifc.area_m2 == pytest.approx(72.0, abs=1e-6)
    assert b_ifc.z_bas_rh2000_m == pytest.approx(13.0, abs=1e-6)
    assert b_ifc.byggnadshojd_m == pytest.approx(14.0, abs=1e-6)
    assert b_ifc.z_tak_rh2000_m == pytest.approx(27.0, abs=1e-6)
    ifc_bounds = b_ifc.fotavtryck.bounds
    assert ifc_bounds == pytest.approx(
        (ursprung_e + 13.0, ursprung_n + 18.0, ursprung_e + 19.0, ursprung_n + 30.0), abs=1e-6
    )

    # 2. GPKG: exportera till EPSG:3006 -> återläs med reprojektion tillbaka till EPSG:3011
    bryt_3d = K50Brytlinje(
        objekt_id="kantsten_e2e",
        geometri=LineString(
            [
                (ursprung_e + 6.0, ursprung_n + 14.0, 12.15),
                (ursprung_e + 26.0, ursprung_n + 14.0, 12.65),
            ]
        ),
        typ="kantsten",
        hojd_m=0.12,
        lager_namn="BRYT_KANT",
    )
    mark_yta = K50Markyta(
        objekt_id="gard_e2e",
        geometri=Polygon(
            [
                (ursprung_e + 2.0, ursprung_n + 2.0),
                (ursprung_e + 10.0, ursprung_n + 2.0),
                (ursprung_e + 10.0, ursprung_n + 10.0),
                (ursprung_e + 2.0, ursprung_n + 10.0),
            ]
        ),
        niva_rh2000_m=12.0,
        markslag=4,
        lager_namn="MARK_GARD",
    )
    trad = K50Trad(
        objekt_id="ek_e2e",
        position=Point(ursprung_e + 8.0, ursprung_n + 8.0, 12.05),
        z_mark_rh2000_m=12.05,
        krondiameter_m=6.0,
        tradhojd_m=11.0,
        tradslag="ek",
        lager_namn="TRAD",
    )
    k50_in = K50Modell(
        byggnader=(b_ifc,),
        markytor=(mark_yta,),
        brytlinjer=(bryt_3d,),
        trad=(trad,),
    )
    gpkg_3006 = exportera_k50_till_gpkg(k50_in, tmp_path / "kvarter_3006.gpkg", crs="EPSG:3006")
    k50_gpkg = importera_gpkg_till_k50(gpkg_3006)
    assert k50_gpkg.epsg == EPSG_SWEREF99_1800
    assert k50_gpkg.kalla_crs == "EPSG:3006"
    b_gpkg = k50_gpkg.byggnader[0]
    assert b_gpkg.area_m2 == pytest.approx(72.0, abs=1e-3)
    assert b_gpkg.z_bas_rh2000_m == pytest.approx(13.0, abs=1e-9)
    assert b_gpkg.byggnadshojd_m == pytest.approx(14.0, abs=1e-9)
    for c_orig, c_rp in zip(
        b_ifc.fotavtryck.exterior.coords, b_gpkg.fotavtryck.exterior.coords, strict=True
    ):
        assert math.hypot(c_orig[0] - c_rp[0], c_orig[1] - c_rp[1]) < 1e-3
    assert [c[2] for c in k50_gpkg.brytlinjer[0].geometri.coords] == pytest.approx(
        [12.15, 12.65], abs=1e-9
    )

    # 3. DXF: exportera reprojicerad K50Modell och återläs
    dxf_fil = exportera_k50_till_dxf(k50_gpkg, tmp_path / "kvarter.dxf")
    dxf_res = importera_dxf_till_k50(dxf_fil)
    assert dxf_res.ohamtade_entiteter == {}
    assert len(dxf_res.modell.byggnader) == 1
    b_dxf = dxf_res.modell.byggnader[0]
    assert b_dxf.area_m2 == pytest.approx(72.0, abs=1e-3)
    assert b_dxf.z_bas_rh2000_m == pytest.approx(13.0, abs=1e-6)
    assert b_dxf.byggnadshojd_m == pytest.approx(14.0, abs=1e-6)
    assert b_dxf.fotavtryck.bounds == pytest.approx(ifc_bounds, abs=1e-3)
    assert [c[2] for c in dxf_res.modell.brytlinjer[0].geometri.coords] == pytest.approx(
        [12.15, 12.65], abs=1e-6
    )

    # 4. LandXML 1.2: exportera mark + byggnad (DSM med NaN-cell + 3D-brytlinje + huskontur)
    #    och återläs direkt med `lapp.landxml.las_landxml_tin` + `rastrera_landxml_tin`
    geo = GeoTransformInfo(
        bredd=10,
        hojd=10,
        x_min=ursprung_e,
        y_max=ursprung_n + 40.0,
        pixelstorlek_x=4.0,
        pixelstorlek_y=4.0,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    e_vek = geo.x_min + (np.arange(geo.bredd, dtype=np.float64) + 0.5) * geo.pixelstorlek_x
    n_vek = geo.y_max - (np.arange(geo.hojd, dtype=np.float64) + 0.5) * geo.pixelstorlek_y
    ee, nn = np.meshgrid(e_vek, n_vek)
    z_terrang = 12.0 + 0.05 * (ee - geo.x_min) - 0.03 * (geo.y_max - nn)
    # Mark + byggnad-yta (tak = z_tak_rh2000_m inuti den rundtrippade IFC-byggnaden)
    i_hus = np.asarray(shapely.contains_xy(b_dxf.fotavtryck, ee, nn), dtype=np.bool_)
    assert int(np.count_nonzero(i_hus)) >= 3
    z_mark_och_bygg = np.where(i_hus, b_dxf.z_tak_rh2000_m, z_terrang)
    z_mark_och_bygg[0, 0] = np.nan

    hus_tak_kontur = K50Brytlinje(
        objekt_id="hus_takkontur",
        geometri=LineString(
            [
                (float(c[0]), float(c[1]), b_dxf.z_tak_rh2000_m)
                for c in b_dxf.fotavtryck.exterior.coords
            ]
        ),
        typ="byggnad",
        hojd_m=b_dxf.byggnadshojd_m,
    )
    landxml_fil = tmp_path / "mark_och_byggnad.xml"
    lx_exp = exportera_landxml_tin(
        z_mark_och_bygg,
        geo,
        landxml_fil,
        brytlinjer=[dxf_res.modell.brytlinjer[0], hus_tak_kontur],
        yta_namn="e2e-mark-byggnad",
    )
    assert lx_exp.antal_nan_celler == 1
    assert lx_exp.antal_brytlinjer == 2

    rot_xml = ET.fromstring(landxml_fil.read_text(encoding="utf-8"))
    ns = {"lx": "http://www.landxml.org/schema/LandXML-1.2"}
    crs_el = rot_xml.find("lx:CoordinateSystem", ns)
    assert crs_el is not None and crs_el.attrib["epsgCode"] == "3011"

    tin_yta = las_landxml_tin(landxml_fil)
    assert tin_yta.namn == "e2e-mark-byggnad"
    assert tin_yta.antal_trianglar == lx_exp.antal_trianglar
    z_rt, tackt = rastrera_landxml_tin(tin_yta, geo)
    assert not bool(tackt[0, 0]) and math.isnan(float(z_rt[0, 0]))
    giltiga_lx = np.isfinite(z_mark_och_bygg)
    max_lx_fel = float(
        np.max(np.abs(z_rt[giltiga_lx].astype(np.float64) - z_mark_och_bygg[giltiga_lx]))
    )
    assert max_lx_fel < 1e-5
    # Takcellerna behåller exakt 27,0 m RH 2000 genom LandXML 1.2
    assert float(np.max(np.abs(z_rt[i_hus].astype(np.float64) - 27.0))) < 1e-5

    # 5. Binär `.glb` (glTF 2.0): exportera terräng + rundtrippad IFC-byggnad och verifiera mesh
    z_terr_glb = z_terrang.copy()
    z_terr_glb[0, 0] = np.nan
    e0_glb, n0_glb, z0_glb = ursprung_e + 20.0, ursprung_n + 20.0, 12.0
    glb_fil = tmp_path / "kvarter.glb"
    glb_res = exportera_3d_glb(
        glb_fil,
        hojd_raster_m=z_terr_glb,
        transform=geo,
        byggnader=[b_dxf],
        ursprung_e_m=e0_glb,
        ursprung_n_m=n0_glb,
        ursprung_z_m=z0_glb,
    )
    assert glb_res.antal_terrang_trianglar > 0
    assert glb_res.antal_byggnad_trianglar == 12  # 4-hörns rektangel: 8 vägg + 2 tak + 2 botten

    raw = glb_fil.read_bytes()
    assert len(raw) == glb_res.filstorlek_bytes
    assert len(raw) % 4 == 0
    magic, version, total_len = struct.unpack_from("<III", raw, 0)
    assert magic == 0x46546C67  # b"glTF"
    assert version == 2
    assert total_len == len(raw)

    json_len, json_type = struct.unpack_from("<II", raw, 12)
    assert json_type == 0x4E4F534A  # b"JSON"
    gltf = json.loads(raw[20 : 20 + json_len].decode("utf-8"))
    bin_off = 20 + json_len
    bin_len, bin_type = struct.unpack_from("<II", raw, bin_off)
    assert bin_type == 0x004E4942  # b"BIN\x00"
    bin_data = raw[bin_off + 8 : bin_off + 8 + bin_len]
    assert bin_off + 8 + bin_len == total_len

    assert gltf["asset"]["version"] == "2.0"
    extras = gltf["asset"]["extras"]
    assert extras["crs"] == "EPSG:3011"
    assert extras["epsg"] == 3011
    assert extras["hojdsystem"] == "RH 2000"
    assert extras["ursprung_e_m"] == pytest.approx(e0_glb)
    assert extras["ursprung_n_m"] == pytest.approx(n0_glb)
    assert extras["ursprung_z_m"] == pytest.approx(z0_glb)

    # Packa upp byggnadsmeshens POSITION ur BIN-chunken och byt Y-upp -> EPSG:3011 / RH 2000
    bygg_mesh = next(m for m in gltf["meshes"] if m["name"] == "byggnader")
    acc_pos = gltf["accessors"][bygg_mesh["primitives"][0]["attributes"]["POSITION"]]
    bv_pos = gltf["bufferViews"][acc_pos["bufferView"]]
    pos_f32 = np.frombuffer(
        bin_data[bv_pos["byteOffset"] : bv_pos["byteOffset"] + bv_pos["byteLength"]],
        dtype="<f4",
    ).reshape(acc_pos["count"], 3)
    e_world = e0_glb + pos_f32[:, 0].astype(np.float64)
    n_world = n0_glb - pos_f32[:, 2].astype(np.float64)
    z_world = z0_glb + pos_f32[:, 1].astype(np.float64)
    assert (
        float(np.min(e_world)),
        float(np.min(n_world)),
        float(np.max(e_world)),
        float(np.max(n_world)),
    ) == pytest.approx(ifc_bounds, abs=1e-3)
    assert float(np.min(z_world)) == pytest.approx(13.0, abs=1e-4)
    assert float(np.max(z_world)) == pytest.approx(27.0, abs=1e-4)


# ---------------------------------------------------------------------------
# Test 4: U13 Buller K21 (`buller.nord2000`) före/efter bullerskärm
# ---------------------------------------------------------------------------


def test_e2e_v7_u13_buller_k21_fore_efter_skarm_maskning_och_invarianter(tmp_path: Path) -> None:
    """U13 K21 `buller.nord2000`: före/efter bullerskärm på K1 v2 med byggnad,
    verifierar dB(A)-dämpning bakom skärmen, `NaN` inuti byggnaden, fasadpunkter samt
    `nord2000_konform is False` och `rapport_sparrad is True`."""
    e0, n0 = 150000.0, 6580000.0
    sida_m, pixel_m = 48.0, 8.0  # 6×6 = 36 celler -> snabb riktig Nord2000-fysik (< 5 s)
    n = round(sida_m / pixel_m)
    x_min, y_max = e0, n0 + sida_m

    hus_ring = (
        (e0 + 32.0, n0 + 16.0),
        (e0 + 46.0, n0 + 16.0),
        (e0 + 46.0, n0 + 32.0),
        (e0 + 32.0, n0 + 32.0),
    )
    hus = ByggnadsPost(
        byggnad_id="hus_buller_e2e",
        polygon=hus_ring,
        hal=(),
        takhojd_rh2000=20.0,
        markhojd_rh2000=10.0,
        hojd_m=10.0,
        kalla="syntetisk_v8_e2e",
    )

    k1_mapp = tmp_path / "k1_buller"
    k1_mapp.mkdir(parents=True)
    terrang = np.full((n, n), 10.0, dtype=np.float32)
    markslag = np.full((n, n), 4, dtype=np.uint8)
    osak = np.full((n, n), 0.05, dtype=np.float32)
    geo = GeoTransformInfo(
        bredd=n,
        hojd=n,
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=pixel_m,
        pixelstorlek_y=pixel_m,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    from karna.geometri.rasterfil import ruta_fran_geo

    mark, markslag = brann_in_byggnader(terrang, markslag, ruta_fran_geo(geo), [hus.till_byggnad()])
    skriv_geotiff(k1_mapp / "terrang.tif", terrang, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(
        k1_mapp / "mark.tif",
        np.asarray(mark, dtype=np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=pixel_m,
    )
    skriv_geotiff(
        k1_mapp / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m
    )
    skriv_geotiff(k1_mapp / "osakerhet.tif", osak, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_byggnader_gpkg(
        k1_mapp / "byggnader.gpkg", (hus,), bbox_fallback=(x_min, n0, x_min + sida_m, y_max)
    )
    recept_fil = k1_mapp / "recept.json"
    recept_fil.write_text(
        json.dumps(
            {
                "kontrakt": "K1/v2",
                "ruta_id": "mm_150000_6580000",
                "epsg": 3011,
                "hojdsystem": "RH 2000",
                "installningar": {"k1_version": 2, "upplosning_m": pixel_m},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    bas_recept = sha256_fil(recept_fil)

    k9_skarm = skriv_scenario(
        Planscenario(
            scenario_id="med-bullerskarm",
            bas_recept=(bas_recept,),
            andringar=(
                Andring(
                    typ="brytlinje_ny",
                    andring_id="bullerskarm_e2e",
                    geometri={
                        "type": "LineString",
                        "coordinates": [[e0 + 14.0, n0 + 4.0], [e0 + 14.0, n0 + 44.0]],
                    },
                    egenskaper={"typ": "mur", "hojd_m": 4.0},
                ),
            ),
        ),
        tmp_path / "k9_skarm.json",
    )

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "v8-e2e-buller-skarm",
            "scenarier": {
                "fore": {"bas": str(k1_mapp)},
                "efter": {"bas": str(k1_mapp), "k9": [str(k9_skarm)]},
            },
            "analyser": [
                {
                    "id": "buller",
                    "losare": "buller.nord2000",
                    "parametrar": {
                        "vagsegment": [
                            {
                                "id": "vag_vast",
                                "koordinater": [[e0 + 3.0, n0 + 4.0], [e0 + 3.0, n0 + 44.0]],
                                "adt_fordon_dygn": 10000.0,
                                "hastighet_km_h": 50.0,
                                "andel_tung_pct": 10.0,
                            }
                        ],
                        "mottagarhojd_m": 2.0,
                        "raster_cell_m": 8.0,
                        "segment_langd_m": 20.0,
                        "fasad_steg_m": 12.0,
                        "max_vaningar": 1,
                        "reflexordning": 0,
                    },
                }
            ],
            "jamforelser": [
                {
                    "analys": "buller",
                    "utdata": "ekvivalent_ljudniva",
                    "fore": "fore",
                    "efter": "efter",
                    "troskel": 1.0,
                }
            ],
        },
        tmp_path,
    )

    res = kor_utredning(fraga, ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    tif_fore = tmp_path / "ut" / "buller" / "fore" / "ekvivalent_ljudniva_dba.tif"
    tif_efter = tmp_path / "ut" / "buller" / "efter" / "ekvivalent_ljudniva_dba.tif"
    fasad_efter = tmp_path / "ut" / "buller" / "efter" / "fasad_buller.geojson"
    sam_efter_fil = tmp_path / "ut" / "buller" / "efter" / "buller_sammanfattning.json"

    # 1. Tydlig dB(A)-dämpning bakom 4 m-skärmen (E = E0 + 20,0 m, N = N0 + 28,0 m)
    l_fore = _sampla_cell(tif_fore, e0 + 20.0, n0 + 28.0)
    l_efter = _sampla_cell(tif_efter, e0 + 20.0, n0 + 28.0)
    assert np.isfinite(l_fore) and np.isfinite(l_efter)
    assert l_efter < l_fore - 5.0

    jamf = res.sammanstallning["jamforelser"][0]
    assert jamf["max_minskning"] > 5.0
    assert jamf["yta_minskning_m2"] > 0.0

    # 2. NaN i byggnadsceller och ändliga värden utanför
    arr_efter, geo_efter = las_geotiff(tif_efter)
    hus_poly = shapely.Polygon(hus_ring)
    antal_i_hus = 0
    for r in range(geo_efter.hojd):
        for c in range(geo_efter.bredd):
            ce = geo_efter.x_min + (c + 0.5) * geo_efter.pixelstorlek_x
            cn = geo_efter.y_max - (r + 0.5) * geo_efter.pixelstorlek_y
            if hus_poly.covers(shapely.Point(ce, cn)):
                antal_i_hus += 1
                assert np.isnan(arr_efter[r, c])
            else:
                assert np.isfinite(arr_efter[r, c])
    assert antal_i_hus == 4

    # 3. Fasadpunkter och hårda skiss-/rapportspärr-invarianter
    fc = json.loads(fasad_efter.read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) > 0
    assert fc["properties"]["nord2000_konform"] is False

    sam = json.loads(sam_efter_fil.read_text(encoding="utf-8"))
    assert sam["antal_bullerskarmar"] == 1
    assert sam["antal_maskade_byggnadsceller"] == antal_i_hus
    assert sam["nord2000_konform"] is False
    assert sam["rapport_sparrad"] is True
    assert sam["metodstatus"] == "skiss"

    for jobb in res.sammanstallning["jobb"]:
        mv = jobb["matvarden"]
        assert mv["nord2000_konform"] is False
        assert mv["rapport_sparrad"] is True
        assert mv["metodstatus"] == "skiss"


# ---------------------------------------------------------------------------
# Test 5: U12c CEDVAL A1-1 + syntetisk CSV + K37-JSON
# ---------------------------------------------------------------------------


def test_e2e_v7_u12c_cedval_a11_snabbvind_csv_och_k37_json(tmp_path: Path) -> None:
    """U12c CEDVAL A1-1: `kor_cedval_a11_validering(cellstorlek_m=5.0, losare="snabbvind")`
    + `jamfor_mot_cedval_csv` med syntetisk CSV; verifierar `max_hastighet_i_solid_m_s == 0.0`,
    `jamford_mot_vindtunnel_data is False` och giltig K37-JSON."""
    res = kor_cedval_a11_validering(cellstorlek_m=5.0, losare="snabbvind")
    assert res.max_hastighet_i_solid_m_s == 0.0
    assert res.jamford_mot_vindtunnel_data is False
    assert res.metodstatus == "skiss"
    assert res.projektion_divergens_rel is not None and res.projektion_divergens_rel < 1e-5
    assert res.rms_divergens_1_s < 0.01
    assert res.inloppsprofil_rel_fel < 0.01
    assert res.uppstroms_dampning_norm < res.uppstroms_inlopps_mark_norm
    assert res.min_u_x_la_norm < 0.5

    # K37-JSON utan vindtunneldata
    dok_utan = cedval_till_dict(res)
    assert dok_utan["format"] == K37_FORMAT
    kontrollera_k37(dok_utan)
    assert CEDVAL_FALL_ID in json.dumps(dok_utan, allow_nan=False)
    fall_utan = dok_utan["delar"]["luft"]["fall"][0]
    assert fall_utan["avvikelse"] is None
    assert fall_utan["extra"]["jamford_mot_vindtunnel_data"] is False
    assert fall_utan["extra"]["metodstatus"] == "skiss"

    # Syntetisk CSV i tmp_path: 4 punkter i lä + 1 i solid byggnad + 1 utanför domän
    doman = res.doman
    u_norm = res.vindfalt.u_m_s / doman.spec.u_ref_m_s
    i_bak = doman.byggnad_i_intervall[1]
    j_mitt = doman.grid.form[1] // 2
    csv_fil = tmp_path / "cedval_a11_syntetisk.csv"
    with csv_fil.open("w", encoding="utf-8", newline="") as fh:
        skrivare = csv.writer(fh)
        skrivare.writerow(["x_m", "y_m", "z_m", "u_norm", "v_norm", "w_norm"])
        for steg, offset in ((1, 0.01), (3, -0.01), (6, 0.005), (10, -0.005)):
            i = i_bak + steg
            k = 1
            skrivare.writerow(
                [
                    f"{doman.x_m[i]:.4f}",
                    f"{doman.y_m[j_mitt]:.4f}",
                    f"{doman.z_m[k]:.4f}",
                    f"{u_norm[0, i, j_mitt, k] + offset:.8f}",
                    f"{u_norm[1, i, j_mitt, k]:.8f}",
                    f"{u_norm[2, i, j_mitt, k]:.8f}",
                ]
            )
        skrivare.writerow(["0.0", "0.0", "5.0", "0.0", "0.0", "0.0"])  # i byggnad (solid)
        skrivare.writerow(["1200.0", "0.0", "5.0", "1.0", "0.0", "0.0"])  # utanför domän

    falt = jamfor_mot_cedval_csv(res, csv_fil)
    assert falt.antal_rader_totalt == 6
    assert falt.antal_jamforda == 4
    assert falt.antal_i_solid == 1
    assert falt.antal_utanfor_doman == 1
    assert 0.0 < falt.rmse < 0.02
    assert falt.fac2 == pytest.approx(1.0)
    assert falt.hit_rate == pytest.approx(1.0)
    assert falt.godkand_cost732 is True

    # K37-JSON med CSV-jämförelse
    dok_med = cedval_till_dict(res, falt)
    kontrollera_k37(dok_med)
    json.dumps(dok_med, allow_nan=False)
    fall_med = dok_med["delar"]["luft"]["fall"][0]
    assert fall_med["avvikelse"]["varde"] == pytest.approx(falt.rmse)
    assert fall_med["avvikelse"]["godkand"] is True
    assert fall_med["extra"]["jamford_mot_vindtunnel_data"] is True
    assert fall_med["extra"]["metodstatus"] == "skiss"
