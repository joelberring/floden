"""Deterministiska tester för Uppdrag 14: fördjupad 2D + 1D-rännsten (`vatten.2d`) i webbflödet."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio

from demovarld.demo import LITEN
from demovarld.varld import nytt_hus_polygon
from markmodell.brytlinjer import Brytlinje
from markmodell.k1_skrivare import skriv_brytlinjer_gpkg
from markmodell.rutnat import Ruta
from tests.webb.conftest import Demo
from testsvit.k1_io import skriv_geotiff
from webb import analysvektor
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel
from webb.kartverktyg import (
    STANDARD_MAX_2D_CELLER,
    _skyfall_metod,
    kontrollera_2d_cellgrans,
)
from webb.moduler import visningsnamn
from webb.resultatlager import (
    ETIKETTER,
    MASKADE_ROLLER,
    PALETTER,
    SKALA_FOR_ROLL,
    TAKKALLA_MARKSLAG,
    Mosaik,
    Utbredning,
    etikett_och_enhet,
    fara_mosaik,
    farg_for_roll,
    takkalla_for_roll,
)

REPO = Path(__file__).resolve().parents[2]
P = "/api/kartverktyg/demokvarteret"


class _Kv:
    def __init__(self, demo: Demo, rot: Path, *, max_2d_celler: int | None = None) -> None:
        self.demo = demo
        self.rot = rot
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=demo.rot / "korningar",
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot],
            max_2d_celler=max_2d_celler,
        )
        self.app.fragor.demomapp = demo.varld

    def json(self, verb: str, sokvag: str, kropp: Any = None) -> tuple[int, dict[str, Any]]:
        data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
        status, _, ut = self.app.hantera_forfragan(verb, sokvag, data)
        return status, json.loads(ut)

    def binart(self, sokvag: str) -> tuple[int, dict[str, str], bytes]:
        return self.app.hantera_forfragan("GET", sokvag, b"")

    def vanta_klar(self, korning_id: str, tidsgrans: float = 120.0) -> dict[str, Any]:
        slut = time.monotonic() + tidsgrans
        while time.monotonic() < slut:
            st = self.app.fragor.korning_status(korning_id)
            assert st["status"] != "fel", st
            if st["status"] == "klar":
                return self.app.fragor.resultat(korning_id)
            time.sleep(0.2)
        raise AssertionError(f"Körning {korning_id} blev inte klar i tid.")


@pytest.fixture(scope="module")
def kv(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Kv]:
    inst = _Kv(demo, tmp_path_factory.mktemp("webb-2d-flode"))
    ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
    ring.append(ring[0])
    status, _ = inst.json(
        "POST",
        f"{P}/scenarier",
        {
            "namn": "TvadProv",
            "andringar": [
                {
                    "typ": "byggnad_ny",
                    "andring_id": "nytt_hus",
                    "geometri": {"type": "Polygon", "coordinates": [ring]},
                    "egenskaper": {"hojd_m": 15},
                }
            ],
        },
    )
    assert status == 200
    yield inst


def _linje_over_demokvarter() -> list[list[float]]:
    poly = nytt_hus_polygon(LITEN.varld())
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    mid_y = 0.5 * (min(ys) + max(ys))
    return [[min(xs) - 20.0, mid_y], [max(xs) + 20.0, mid_y]]


# ------------------------------------------------------------------ enhetstester


def test_fara_etikett_mask_skala_och_nan_bevarande() -> None:
    assert ETIKETTER["fara"] == ("Fara (djup × hastighet)", "m²/s")
    assert etikett_och_enhet("fara") == ("Fara (djup × hastighet)", "m²/s")
    assert "fara" in MASKADE_ROLLER
    assert takkalla_for_roll("fara") == TAKKALLA_MARKSLAG
    assert takkalla_for_roll("hastighet") == TAKKALLA_MARKSLAG
    assert takkalla_for_roll("ankomsttid") == TAKKALLA_MARKSLAG
    assert SKALA_FOR_ROLL["fara"] == "fara"
    assert "fara" in PALETTER
    assert farg_for_roll("fara") == "fara"
    assert visningsnamn("vatten.2d") == "Skyfall 2D (lokal tröghet)"

    u = Utbredning(100.0, 200.0, 103.0, 202.0)
    h = Mosaik(np.array([[0.25, np.nan, 0.4], [0.0, 1.5, np.nan]], dtype=np.float32), u, 1.0)
    v = Mosaik(np.array([[0.8, 0.5, np.nan], [1.2, 0.6, np.nan]], dtype=np.float32), u, 1.0)
    f = fara_mosaik(h, v)
    assert f is not None
    assert f.utbredning == u
    assert f.raster.dtype == np.float32
    assert f.raster[0, 0] == pytest.approx(0.25 * 0.8, abs=1e-5)
    assert f.raster[1, 0] == pytest.approx(0.0, abs=1e-5)
    assert f.raster[1, 1] == pytest.approx(1.5 * 0.6, abs=1e-5)
    assert np.isnan(f.raster[0, 1])
    assert np.isnan(f.raster[0, 2])
    assert np.isnan(f.raster[1, 2])

    # Avvikande utbredning eller form ger None (aldrig tyst fel)
    assert fara_mosaik(h, Mosaik(v.raster, Utbredning(101.0, 200.0, 104.0, 202.0), 1.0)) is None
    assert fara_mosaik(h, Mosaik(np.ones((2, 2), dtype=np.float32), u, 1.0)) is None


def test_cellgrans_och_validering_skyfall_metod() -> None:
    assert STANDARD_MAX_2D_CELLER == 250_000
    kontrollera_2d_cellgrans(250_000, 250_000)
    with pytest.raises(ValueError, match="250001"):
        kontrollera_2d_cellgrans(250_001, 250_000)
    with pytest.raises(ValueError, match="max_2d_celler"):
        kontrollera_2d_cellgrans(10, 0)

    assert _skyfall_metod(None) == "graf"
    assert _skyfall_metod("graf") == "graf"
    assert _skyfall_metod("2d") == "2d"
    for ogiltig in ("3d", "dynamisk", "", 123, True):
        with pytest.raises(FlodenWebbFel) as exc_info:
            _skyfall_metod(ogiltig)
        assert exc_info.value.status_kod == 400


def test_rannsten_flode_geojson_lasning(tmp_path: Path) -> None:
    fil = tmp_path / "rannsten_flode.geojson"
    fil.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[150000.0, 6580000.0], [150010.0, 6580000.0]],
                        },
                        "properties": {"ds_m": 10.0, "q_max_m3_s": 0.042, "h_max_m": 0.09},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fc = analysvektor.rannsten_flode_geojson(fil)
    assert fc["type"] == "FeatureCollection"
    assert fc["meta"] == {"antal_totalt": 1, "visade": 1}
    assert fc["features"][0]["properties"]["langd_m"] == pytest.approx(10.0)
    assert fc["features"][0]["properties"]["q_max_m3_s"] == pytest.approx(0.042)


# ------------------------------------------------------------------ standard graf oförändrad


def test_standard_graf_ar_oforandrad_och_status_har_skyfall_metod(kv: _Kv) -> None:
    status_p, plan = kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["vatten.skyfall"]})
    assert status_p == 200, plan
    jobb = plan["fragor"][0]["plan"]["jobb"]
    assert all(j["losare"] == "vatten.statisk_fyllning" for j in jobb)

    status_k, kor = kv.json("POST", f"{P}/lage-idag/kor", {"analyser": ["vatten.skyfall"]})
    assert status_k == 202, kor
    kid = kor["fragor"][0]["korning"]["korning_id"]
    kv.vanta_klar(kid)

    status_s, st = kv.json("GET", f"{P}/lage-idag/status")
    assert status_s == 200, st
    vatten = next(k for k in st["korningar"] if k["mall"] == "vatten")
    assert vatten["skyfall_metod"] == "graf"
    assert vatten["regn"]["skyfall_metod"] == "graf"


# ------------------------------------------------------------------ skyfall_metod="2d" end-to-end


def test_skyfall_metod_2d_kor_vatten_2d_fara_sektion_och_status(kv: _Kv) -> None:
    regn_kort = {"regn_mm": 30, "varaktighet_min": 5}

    # 1. Planera läget idag med 2D: exakt en vatten.2d-lösare per ruta
    status_p, plan = kv.json(
        "POST",
        f"{P}/lage-idag/plan",
        {"analyser": ["vatten.skyfall"], "regn": regn_kort, "skyfall_metod": "2d"},
    )
    assert status_p == 200, plan
    jobb = plan["fragor"][0]["plan"]["jobb"]
    assert len(jobb) >= 1
    assert all(j["losare"] == "vatten.2d" for j in jobb)

    # 2. Kör läget idag med 2D
    status_k, kor = kv.json(
        "POST",
        f"{P}/lage-idag/kor",
        {"analyser": ["vatten.skyfall"], "regn": regn_kort, "skyfall_metod": "2d"},
    )
    assert status_k == 202, kor
    kid = kor["fragor"][0]["korning"]["korning_id"]
    res = kv.vanta_klar(kid)

    # Verifiera att vatten.2d kördes med rannsten_1d="auto" och Rust auto-dispatch (None)
    analys_post = next(a for a in res["antaganden"]["analyser"] if a["losare"] == "vatten.2d")
    assert analys_post["parametrar"].get("rannsten_1d") == "auto"
    assert analys_post["parametrar"].get("anvand_rust") is None

    # 3. Verifiera utdataroller i resultatlager: max_djup, hastighet, ankomsttid, fara
    roller = {la["roll"]: la for la in res["lager"] if la["typ"] == "bas"}
    assert {"max_djup", "hastighet", "ankomsttid", "fara"}.issubset(roller)
    assert roller["max_djup"]["enhet"] == "m"
    assert roller["hastighet"]["enhet"] == "m/s"
    assert roller["ankomsttid"]["enhet"] == "s"
    assert roller["fara"]["enhet"] == "m²/s"
    assert roller["fara"]["etikett"] == "Fara (djup × hastighet)"
    assert roller["fara"]["harledd"] is True

    # PNG för fara-lagret går att hämta (HTTP 200, giltig PNG-signatur)
    st_png, _, png_bytes = kv.binart(roller["fara"]["png"])
    assert st_png == 200
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    # 4. Numerisk kontroll: fara == max_djup * hastighet inom 1e-5 + exakt NaN-bevarande
    k_post = kv.app.fragor._hamta(kid)  # noqa: SLF001
    samm = kv.app.fragor._sammanstallning(k_post)  # noqa: SLF001
    j0 = next(j for j in samm["jobb"] if j["losare"] == "vatten.2d")
    jmapp = kv.app.fragor._jobbmapp(k_post, j0)  # noqa: SLF001
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

    # Läs serverns byggda fara-mosaik ur webb-cachen (`<korning>/webb/<lager_id>.npy`)
    fara_arr = np.load(k_post.mapp / "webb" / f"{roller['fara']['id']}.npy").astype(np.float64)
    forvantad = np.where(np.isfinite(h_arr) & np.isfinite(v_arr), h_arr * v_arr, np.nan)
    assert fara_arr.shape == forvantad.shape
    np.testing.assert_allclose(fara_arr, forvantad, rtol=0.0, atol=1e-5, equal_nan=True)
    # Kontrollera också att det faktiskt finns våta celler med positiv fara > 0
    giltiga = np.isfinite(fara_arr) & (h_arr > 0.01) & (v_arr > 0.0)
    assert int(np.sum(giltiga)) > 0, "2D-körningen ska ge våta celler med positiv fara"

    # Punktavläsning på en våt cell stämmer mot h * v
    idx_r, idx_c = np.argwhere(giltiga)[0]
    e_pt, n_pt = rasterio.transform.xy(tr, int(idx_r), int(idx_c))
    st_avl, avl = kv.json(
        "GET",
        f"/api/korningar/{kid}/avlasning?lager={roller['fara']['id']}&e={e_pt}&n={n_pt}",
    )
    assert st_avl == 200, avl
    v_fara = next(x for x in avl["varden"] if x["lager_id"] == roller["fara"]["id"])
    assert v_fara["inom"] is True
    forvantat_pt = float(h_arr[idx_r, idx_c] * v_arr[idx_r, idx_c])
    assert v_fara["varde"] == pytest.approx(forvantat_pt, abs=1e-4)

    # 5. B11-invariant: sektion tillåter BARA max_djup och nekar fara/hastighet/ankomsttid med 400
    linje = _linje_over_demokvarter()
    st_ok, sek_ok = kv.json(
        "POST",
        f"{P}/sektion",
        {"punkter": linje, "korning_id": kid, "fore_lager": roller["max_djup"]["id"]},
    )
    assert st_ok == 200, sek_ok
    assert sek_ok["kontrakt"] == "floden-sektion/v1"
    for nekad_roll in ("fara", "hastighet", "ankomsttid"):
        st_nej, fel_svar = kv.json(
            "POST",
            f"{P}/sektion",
            {"punkter": linje, "korning_id": kid, "fore_lager": roller[nekad_roll]["id"]},
        )
        assert st_nej == 400, (nekad_roll, fel_svar)

    # 6. Statussvar för läget idag innehåller skyfall_metod == "2d"
    st_status, status_svar = kv.json("GET", f"{P}/lage-idag/status")
    assert st_status == 200, status_svar
    vatten_st = next(k for k in status_svar["korningar"] if k["mall"] == "vatten")
    assert vatten_st["skyfall_metod"] == "2d"
    assert vatten_st["regn"]["skyfall_metod"] == "2d"

    # 7. Förslagskörning (före + efter) med skyfall_metod="2d"
    st_sk, kor_sc = kv.json(
        "POST",
        f"{P}/scenarier/tvadprov/kor",
        {
            "analyser": ["vatten.skyfall"],
            "regn": {"regn_mm": 25, "varaktighet_min": 5},
            "skyfall_metod": "2d",
        },
    )
    assert st_sk == 202, kor_sc
    kid_sc = kor_sc["fragor"][0]["korning"]["korning_id"]
    res_sc = kv.vanta_klar(kid_sc)

    fara_lager = [la for la in res_sc["lager"] if la["roll"] == "fara"]
    assert {la["typ"] for la in fara_lager} == {"bas", "scenario", "skillnad"}
    djup_fore = next(
        la["id"] for la in res_sc["lager"] if la["roll"] == "max_djup" and la["typ"] == "bas"
    )
    djup_efter = next(
        la["id"] for la in res_sc["lager"] if la["roll"] == "max_djup" and la["typ"] == "scenario"
    )
    fara_efter = next(la["id"] for la in fara_lager if la["typ"] == "scenario")
    fara_skill = next(la["id"] for la in fara_lager if la["typ"] == "skillnad")

    # Sektion med före+efter max_djup ger 200; med efter=fara eller skillnad=fara ger 400
    st_sek2, _ = kv.json(
        "POST",
        f"{P}/sektion",
        {
            "punkter": linje,
            "korning_id": kid_sc,
            "fore_lager": djup_fore,
            "efter_lager": djup_efter,
        },
    )
    assert st_sek2 == 200
    for bad_efter in (fara_efter, fara_skill):
        st_bad, _ = kv.json(
            "POST",
            f"{P}/sektion",
            {
                "punkter": linje,
                "korning_id": kid_sc,
                "fore_lager": djup_fore,
                "efter_lager": bad_efter,
            },
        )
        assert st_bad == 400

    # Scenario-status innehåller skyfall_metod == "2d"
    st_sc_st, sc_status = kv.json("GET", f"{P}/scenarier/tvadprov/status")
    assert st_sc_st == 200, sc_status
    v_sc = next(k for k in sc_status["korningar"] if k["mall"] == "vatten")
    assert v_sc["skyfall_metod"] == "2d"
    assert v_sc["regn"]["skyfall_metod"] == "2d"


# ------------------------------------------------------------------ cellgräns och ogiltig metod


def test_cellgrans_och_ogiltig_metod_ger_http_400(demo: Demo, tmp_path: Path) -> None:
    litet_kv = _Kv(demo, tmp_path / "begransat", max_2d_celler=100)
    ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
    ring.append(ring[0])
    st_sc, _ = litet_kv.json(
        "POST",
        f"{P}/scenarier",
        {
            "namn": "Grans",
            "andringar": [
                {
                    "typ": "byggnad_ny",
                    "andring_id": "hus_1",
                    "geometri": {"type": "Polygon", "coordinates": [ring]},
                    "egenskaper": {"hojd_m": 12},
                }
            ],
        },
    )
    assert st_sc == 200

    for vag in (
        f"{P}/lage-idag/plan",
        f"{P}/lage-idag/kor",
        f"{P}/scenarier/grans/plan",
        f"{P}/scenarier/grans/kor",
    ):
        st, svar = litet_kv.json(
            "POST",
            vag,
            {"analyser": ["vatten.skyfall"], "skyfall_metod": "2d"},
        )
        assert st == 400, (vag, svar)
        assert "100" in svar["fel"] and "celler" in svar["fel"]

    # Standard "graf" påverkas inte av 2D-cellgränsen
    st_graf, _ = litet_kv.json(
        "POST",
        f"{P}/lage-idag/plan",
        {"analyser": ["vatten.skyfall"], "skyfall_metod": "graf"},
    )
    assert st_graf == 200

    # Ogiltig skyfall_metod ger HTTP 400
    st_fel, fel = litet_kv.json(
        "POST",
        f"{P}/lage-idag/plan",
        {"analyser": ["vatten.skyfall"], "skyfall_metod": "ogiltig"},
    )
    assert st_fel == 400
    assert "skyfall_metod" in fel["fel"]


# ------------------------------------------------------------------ 1D-rännsten (rannsten_flode)


def test_rannsten_flode_geojson_exponeras_nar_3d_kantsten_finns(tmp_path: Path) -> None:
    """Bygger en 16×16 K1-ruta med 3D-kantsten och verifierar `rannsten_flode` via webb-API."""
    ruta_mapp = tmp_path / "bas"
    ruta_mapp.mkdir(parents=True)
    h, b = 16, 16
    yy, xx = np.mgrid[0:h, 0:b]
    z = (10.60 + 0.02 * np.hypot(yy - 7.5, xx - 7.5)).astype(np.float32)
    z[5:11, 5:11] = np.float32(10.00)
    x_min, y_max = 150000.0, 6580016.0
    skriv_geotiff(ruta_mapp / "mark.tif", z, x_min=x_min, y_max=y_max, pixelstorlek=1.0)
    skriv_geotiff(
        ruta_mapp / "markslag.tif",
        np.full((h, b), 1, dtype=np.uint8),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=1.0,
    )
    skriv_geotiff(
        ruta_mapp / "osakerhet.tif",
        np.full((h, b), 0.02, dtype=np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=1.0,
    )
    ruta = Ruta(
        e_min_m=x_min,
        n_min_m=y_max - h,
        storlek_m=float(b),
        overlapp_m=0.0,
        upplosning_m=1.0,
    )
    skriv_brytlinjer_gpkg(
        ruta_mapp / "brytlinjer.gpkg",
        [
            Brytlinje(
                typ="kantsten",
                hojd_m=0.12,
                punkter=((x_min + 2.0, y_max - 3.0), (x_min + 14.0, y_max - 3.0)),
                z_kron_rh2000=(10.82, 10.82),
                z_botten_rh2000=(10.70, 10.70),
                tvarfall=0.025,
                kalla="baskarta_3d",
                osakerhet_m=0.02,
            )
        ],
        ruta,
    )

    fragemapp = tmp_path / "fragor"
    fragemapp.mkdir()
    fraga_fil = fragemapp / "rannsten_2d.json"
    fraga_fil.write_text(
        json.dumps(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "rannsten-2d-test",
                "beskrivning": "2D + 1D-rännsten på liten syntetisk ruta",
                "scenarier": {"fore": {"bas": "../bas"}},
                "analyser": [
                    {
                        "id": "skyfall_2d",
                        "losare": "vatten.2d",
                        "scenarier": ["fore"],
                        "parametrar": {
                            "regn_mm": 30.0,
                            "varaktighet_min": 5.0,
                            "rannsten_1d": "auto",
                            "anvand_rust": False,
                        },
                    }
                ],
                "jamforelser": [],
            }
        ),
        encoding="utf-8",
    )

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[fragemapp],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        kartverktygsmapp=tmp_path / "kv",
        datamappar=[tmp_path],
    )
    svar = app.fragor.starta_korning("rannsten_2d")
    kid = svar["korning_id"]
    slut = time.monotonic() + 60.0
    while time.monotonic() < slut:
        st = app.fragor.korning_status(kid)
        assert st["status"] != "fel", st
        if st["status"] == "klar":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("Körningen hann inte bli klar.")

    res = app.fragor.resultat(kid)
    roller = {la["roll"] for la in res["lager"]}
    assert {"max_djup", "hastighet", "ankomsttid", "fara"}.issubset(roller)
    vektor_roller = [v["roll"] for v in res.get("vektorlager", [])]
    assert "rannsten_flode" in vektor_roller

    st_v, _, body = app.hantera_forfragan(
        "GET", f"/api/korningar/{kid}/vektor/fore/rannsten_flode", b""
    )
    assert st_v == 200
    fc = json.loads(body)
    assert fc["type"] == "FeatureCollection"
    assert fc["slag"] == "rannsten_flode"
    assert len(fc["features"]) > 0
    assert fc["features"][0]["geometry"]["type"] == "LineString"
