# ruff: noqa: E501
"""Tester för geografiplaneringsverktyget (`geografi.planera` och `floden geografi planera`)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from floden.cli import main as floden_main
from gemensamt.koordinater import transformera
from geografi.planera import (
    BASLINJE_CPU_TID_1_0M_S,
    BASLINJE_DISK_KOMPRIMERAT_0_5M_MB,
    BASLINJE_DISK_RA_0_5M_MB,
    BASLINJE_E2E_TID_2_5M_S,
    K37_DEMO_LITEN_RSS_TOPP_MB,
    K37_EA8A_RSS_TOPP_MB,
    K37_EA8A_TID_S,
    K37_KVARTER300_RSS_TOPP_MB,
    K37_KVARTER300_TID_S,
    PlaneringsFel,
    formatera_plan_tabell,
    planera_geografi,
)
from karna.paverkan import dagsljus_paverkansradie_m
from kommuner.konfig import KommunKonfigFel

_REPO_ROT = Path(__file__).resolve().parents[2]


def _skriv_1km_geojson_3011(fil: Path, *, e0: float = 150000.0, n0: float = 6580000.0) -> Path:
    """Skriver en exakt 1 km × 1 km-kvadrat i SWEREF 99 18 00 (EPSG:3011)."""
    e1, n1 = e0 + 1000.0, n0 + 1000.0
    fc: dict[str, Any] = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3011"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"namn": "prov_1km"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[e0, n0], [e1, n0], [e1, n1], [e0, n1], [e0, n0]]],
                },
            }
        ],
    }
    fil.write_text(json.dumps(fc), encoding="utf-8")
    return fil


def _skriv_1km_gpkg(fil: Path, *, epsg: int = 3011, e0: float = 150000.0, n0: float = 6580000.0) -> Path:
    """Skriver en 1 km × 1 km-kvadrat i angivet EPSG som GeoPackage."""
    poly_3011 = box(e0, n0, e0 + 1000.0, n0 + 1000.0)
    gdf = gpd.GeoDataFrame({"namn": ["prov_1km"]}, geometry=[poly_3011], crs="EPSG:3011")
    if epsg != 3011:
        gdf = gdf.to_crs(epsg=epsg)
    gdf.to_file(fil, driver="GPKG")
    return fil


def _skriv_wgs84_1km_geojson(fil: Path, *, med_crs: bool = False) -> Path:
    """Konverterar en 1 km × 1 km-kvadrat i Stockholm (EPSG:3011) till WGS84 GeoJSON."""
    horn_3011 = [
        (150000.0, 6580000.0),
        (151000.0, 6580000.0),
        (151000.0, 6581000.0),
        (150000.0, 6581000.0),
        (150000.0, 6580000.0),
    ]
    horn_wgs84 = [list(transformera(e, n, 3011, 4326)) for e, n in horn_3011]
    fc: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"namn": "prov_wgs84"},
                "geometry": {"type": "Polygon", "coordinates": [horn_wgs84]},
            }
        ],
    }
    if med_crs:
        fc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    fil.write_text(json.dumps(fc), encoding="utf-8")
    return fil


def test_1km_kvadrat_geojson_och_gpkg_exakt_rutantal_och_area(tmp_path: Path) -> None:
    geojson_fil = _skriv_1km_geojson_3011(tmp_path / "omrade_1km.geojson")
    gpkg_fil = _skriv_1km_gpkg(tmp_path / "omrade_1km.gpkg", epsg=3011)

    # Utan buffert: exakt 1,0 km², 2×2 = 4 kärnrutor, 0 buffertrutor
    plan_0 = planera_geografi("stockholm", geojson_fil, upplosning_m=1.0, buffert_m=0.0)
    plan_gpkg_0 = planera_geografi("stockholm", gpkg_fil, upplosning_m=1.0, buffert_m=0.0)

    assert plan_0.karna_area_km2 == pytest.approx(1.0, rel=1e-9)
    assert plan_0.total_area_km2 == pytest.approx(1.0, rel=1e-9)
    assert plan_0.antal_karnrutor == 4
    assert plan_0.antal_buffertrutor == 0
    assert plan_0.antal_rutor_totalt == 4
    assert plan_0.karnrutor == (
        "mm_150000_6580000",
        "mm_150000_6580500",
        "mm_150500_6580000",
        "mm_150500_6580500",
    )
    assert plan_0.buffertrutor == ()
    assert plan_0.indata_epsg == 3011
    assert plan_0.internt_epsg == 3011
    assert not plan_0.konverterad_till_3011

    # GeoPackage ger exakt samma yta, rutantal och resurssiffror som GeoJSON
    assert plan_gpkg_0.karna_area_km2 == pytest.approx(plan_0.karna_area_km2, rel=1e-9)
    assert plan_gpkg_0.karnrutor == plan_0.karnrutor
    assert plan_gpkg_0.antal_rutor_totalt == plan_0.antal_rutor_totalt
    assert plan_gpkg_0.kortid_cpu_s == pytest.approx(plan_0.kortid_cpu_s, rel=1e-9)

    # Med exakt 500 m buffert: rundad buffert täcker 4×4 = 16 rutor (4 kärna + 12 buffert)
    plan_500 = planera_geografi("stockholm", geojson_fil, upplosning_m=1.0, buffert_m=500.0)
    forvantad_total_km2 = 1.0 + 4.0 * 0.5 + math.pi * (0.5**2)
    assert plan_500.karna_area_km2 == pytest.approx(1.0, rel=1e-9)
    assert plan_500.total_area_km2 == pytest.approx(forvantad_total_km2, rel=2e-3)
    assert plan_500.antal_karnrutor == 4
    assert plan_500.antal_buffertrutor == 12
    assert plan_500.antal_rutor_totalt == 16

    # Standardbuffert (None): beräknad med dagsljus_paverkansradie_m(60, 5) ≈ 685,8 m -> ceil = 686 m
    # Avstånd till de 4 ytterhörnrutorna är sqrt(500²+500²) ≈ 707,1 m > 686 m -> 36 - 4 = 32 rutor
    plan_std = planera_geografi("stockholm", geojson_fil, upplosning_m=1.0)
    assert plan_std.buffert_m == pytest.approx(math.ceil(dagsljus_paverkansradie_m(60.0, 5.0)))
    assert plan_std.antal_karnrutor == 4
    assert plan_std.antal_buffertrutor == 28
    assert plan_std.antal_rutor_totalt == 32
    assert plan_std.maskinklass == "batch"


def test_upplosningsskalning_och_kallbelagda_antaganden(tmp_path: Path) -> None:
    fil = _skriv_1km_geojson_3011(tmp_path / "skala.geojson")

    p_05 = planera_geografi("goteborg", fil, upplosning_m=0.5, buffert_m=0.0)
    p_10 = planera_geografi("goteborg", fil, upplosning_m=1.0, buffert_m=0.0)
    p_25 = planera_geografi("goteborg", fil, upplosning_m=2.5, buffert_m=0.0)

    # Cellantal ∝ (1 / upplosning_m)^2 vid konstant rutstorlek 500 m + 25 m överlapp (550 m yttre)
    assert p_05.celler_per_ruta == 1100 * 1100
    assert p_10.celler_per_ruta == 550 * 550
    assert p_25.celler_per_ruta == 220 * 220
    assert p_05.celler_per_ruta / p_10.celler_per_ruta == pytest.approx(4.0)
    assert p_10.celler_per_ruta / p_25.celler_per_ruta == pytest.approx(6.25)

    # Tid skalas exakt från #114-baslinjen 4,42 s/ruta vid 1,0 m
    assert p_10.tid_per_ruta_cpu_s == pytest.approx(BASLINJE_CPU_TID_1_0M_S)
    assert p_05.tid_per_ruta_cpu_s == pytest.approx(4.0 * BASLINJE_CPU_TID_1_0M_S)
    assert p_25.tid_per_ruta_cpu_s == pytest.approx(BASLINJE_CPU_TID_1_0M_S / 6.25)
    assert p_25.tid_per_ruta_e2e_ref_s == pytest.approx(BASLINJE_E2E_TID_2_5M_S)

    # Disk skalas exakt från #120-baslinjen 28,85 MB rå / 3,79 MB ZSTD-COG vid 0,5 m
    assert p_05.disk_ra_per_ruta_mb == pytest.approx(BASLINJE_DISK_RA_0_5M_MB)
    assert p_05.disk_komprimerat_per_ruta_mb == pytest.approx(BASLINJE_DISK_KOMPRIMERAT_0_5M_MB)
    assert p_10.disk_ra_per_ruta_mb == pytest.approx(BASLINJE_DISK_RA_0_5M_MB / 4.0)
    assert p_10.disk_komprimerat_per_ruta_mb == pytest.approx(BASLINJE_DISK_KOMPRIMERAT_0_5M_MB / 4.0)
    assert p_25.disk_komprimerat_per_ruta_mb == pytest.approx(BASLINJE_DISK_KOMPRIMERAT_0_5M_MB / 25.0)
    assert p_05.disk_komprimerat_gb < p_05.disk_ra_gb

    # RAM ökar monotont med finare upplösning
    assert p_25.ram_per_ruta_gb < p_10.ram_per_ruta_gb < p_05.ram_per_ruta_gb

    # Parallella arbetare minskar väggklockstid men ökar totalt topp-RAM proportionellt
    p_10_4w = planera_geografi("goteborg", fil, upplosning_m=1.0, buffert_m=0.0, arbetare=4)
    assert p_10_4w.effektiva_arbetare_cpu == 4
    assert p_10_4w.kortid_cpu_s == pytest.approx(p_10.kortid_cpu_s / 4.0)
    assert p_10_4w.ram_totalt_gb == pytest.approx(p_10.ram_per_ruta_gb * 4.0)

    # Explicit uppskattningsmärkning och källbelagda antaganden
    assert p_05.uppskattning is True
    assert len(p_05.antaganden) >= 7
    assert all(a.uppskattning is True and a.kalla and a.skalningsformel for a in p_05.antaganden)
    kallor = " | ".join(a.kalla for a in p_05.antaganden)
    assert "#114" in kallor
    assert "#120" in kallor
    assert "#123" in kallor
    assert "docs/benchmark/2026-11-23-container.json" in kallor
    assert "docs/ARKITEKTUR-DRIFT.md:263-288" in kallor


def test_k37_konstanter_stammer_mot_benchmarkfilen() -> None:
    """Säkerställer att källbelagda K37-siffror i planera.py matchar benchmark-JSON i repot."""
    bm_fil = _REPO_ROT / "docs" / "benchmark" / "2026-11-23-container.json"
    data = json.loads(bm_fil.read_text(encoding="utf-8"))
    skyfall = {f["id"]: f for f in data["delar"]["skyfall"]["fall"]}
    dagsljus = {f["id"]: f for f in data["delar"]["dagsljus"]["fall"]}

    assert skyfall["EA8A"]["tid_s"]["median"] == pytest.approx(K37_EA8A_TID_S, abs=0.01)
    assert skyfall["EA8A"]["minne"]["rss_topp_mb"] == pytest.approx(K37_EA8A_RSS_TOPP_MB, abs=0.1)
    assert dagsljus["kvarter300-svf-soltimmar"]["tid_s"]["median"] == pytest.approx(
        K37_KVARTER300_TID_S, abs=0.01
    )
    assert dagsljus["kvarter300-svf-soltimmar"]["minne"]["rss_topp_mb"] == pytest.approx(
        K37_KVARTER300_RSS_TOPP_MB, abs=0.1
    )
    assert dagsljus["demokvarter-liten"]["minne"]["rss_topp_mb"] == pytest.approx(
        K37_DEMO_LITEN_RSS_TOPP_MB, abs=0.1
    )


def test_wgs84_och_epsg3006_konverteras_vid_inlasning_till_3011(tmp_path: Path) -> None:
    # WGS84 utan CRS (RFC 7946) och med explicit CRS84
    wgs_fil = _skriv_wgs84_1km_geojson(tmp_path / "wgs84_rfc7946.geojson", med_crs=False)
    wgs_crs84 = _skriv_wgs84_1km_geojson(tmp_path / "wgs84_crs84.geojson", med_crs=True)

    for fil in (wgs_fil, wgs_crs84):
        plan = planera_geografi("stockholm", fil, upplosning_m=1.0, buffert_m=0.0)
        assert plan.indata_epsg == 4326
        assert plan.internt_epsg == 3011
        assert plan.konverterad_till_3011 is True
        assert plan.karna_area_km2 == pytest.approx(1.0, rel=1e-3)
        assert plan.antal_karnrutor == 4
        assert plan.karnrutor == (
            "mm_150000_6580000",
            "mm_150000_6580500",
            "mm_150500_6580000",
            "mm_150500_6580500",
        )

    # EPSG:3006 GeoPackage konverteras till EPSG:3011
    gpkg_3006 = _skriv_1km_gpkg(tmp_path / "omrade_3006.gpkg", epsg=3006)
    plan_3006 = planera_geografi("stockholm", gpkg_3006, upplosning_m=1.0, buffert_m=0.0)
    assert plan_3006.indata_epsg == 3006
    assert plan_3006.internt_epsg == 3011
    assert plan_3006.konverterad_till_3011 is True
    assert plan_3006.karna_area_km2 == pytest.approx(1.0, rel=1e-3)
    assert plan_3006.antal_karnrutor == 4


def test_stockholm_vs_goteborg_dataklass_och_k38_varning(tmp_path: Path) -> None:
    fil = _skriv_1km_geojson_3011(tmp_path / "stadsdel.geojson")

    sthlm = planera_geografi("stockholm", fil, upplosning_m=0.5, buffert_m=0.0)
    gbg = planera_geografi("goteborg", fil, upplosning_m=0.5, buffert_m=0.0)

    assert sthlm.dataklass == "stangd"
    assert sthlm.stangd_baskarta is True
    assert sthlm.k38_varning is True
    assert sthlm.moln_batch_tillaten is False
    assert sthlm.oppen_s3_tillaten is False
    assert sthlm.extern_ai_tillaten is False
    assert sthlm.driftplats == "lokal_eller_egen_server"
    assert "K38" in sthlm.dataskyddsnotis
    assert "extern AI" in sthlm.dataskyddsnotis
    assert "S3-bucket" in sthlm.dataskyddsnotis

    assert gbg.dataklass == "oppen"
    assert gbg.stangd_baskarta is False
    assert gbg.k38_varning is False
    assert gbg.moln_batch_tillaten is True
    assert gbg.oppen_s3_tillaten is True
    assert gbg.driftplats == "server_eller_moln_batch"

    # krav_oppen=True stoppar stängda kommuner men släpper igenom öppna
    with pytest.raises(PlaneringsFel, match="stangd"):
        planera_geografi("stockholm", fil, upplosning_m=1.0, buffert_m=0.0, krav_oppen=True)
    plan_ok = planera_geografi("goteborg", fil, upplosning_m=1.0, buffert_m=0.0, krav_oppen=True)
    assert plan_ok.dataklass == "oppen"


def test_flera_stadsdelar_featurecollection_och_bbox_avgransning(tmp_path: Path) -> None:
    # Två separata 500×500 m-polygoner (t.ex. två stadsdelar) -> 0,5 km², 2 kärnrutor
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3011"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"namn": "del_a"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [150000.0, 6580000.0],
                            [150500.0, 6580000.0],
                            [150500.0, 6580500.0],
                            [150000.0, 6580500.0],
                            [150000.0, 6580000.0],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {"namn": "del_b"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [152000.0, 6580000.0],
                            [152500.0, 6580000.0],
                            [152500.0, 6580500.0],
                            [152000.0, 6580500.0],
                            [152000.0, 6580000.0],
                        ]
                    ],
                },
            },
        ],
    }
    fil = tmp_path / "tva_delar.geojson"
    fil.write_text(json.dumps(fc), encoding="utf-8")

    plan = planera_geografi("stockholm", fil, upplosning_m=1.0, buffert_m=0.0)
    assert plan.karna_area_km2 == pytest.approx(0.5, rel=1e-9)
    assert plan.antal_karnrutor == 2
    assert plan.karnrutor == ("mm_150000_6580000", "mm_152000_6580000")

    # Bbox-text i EPSG:3011
    plan_bbox = planera_geografi(
        "goteborg", "150000,6580000,151000,6581000", upplosning_m=1.0, buffert_m=0.0
    )
    assert plan_bbox.karna_area_km2 == pytest.approx(1.0, rel=1e-9)
    assert plan_bbox.antal_karnrutor == 4


def test_cli_end_to_end_json_och_tabell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fil = _skriv_1km_geojson_3011(tmp_path / "cli_1km.geojson")

    # JSON-utskrift
    rc_json = floden_main(
        [
            "geografi",
            "planera",
            "stockholm",
            "--avgransning",
            str(fil),
            "--upplosning-m",
            "0.5",
            "--buffert-m",
            "500",
            "--arbetare",
            "2",
            "--json",
        ]
    )
    assert rc_json == 0
    out_json = capsys.readouterr().out
    data = json.loads(out_json)
    assert data["uppskattning"] is True
    assert data["kommun_id"] == "stockholm"
    assert data["dataklass"] == "stangd"
    assert data["k38_varning"] is True
    assert data["karna_area_km2"] == pytest.approx(1.0, rel=1e-6)
    assert data["antal_karnrutor"] == 4
    assert data["antal_buffertrutor"] == 12
    assert data["antal_rutor_totalt"] == 16
    assert data["ram_per_ruta_gb"] > 0.0
    assert data["ram_totalt_gb"] == pytest.approx(data["ram_per_ruta_gb"] * 2.0, rel=1e-3)
    assert data["disk_ra_gb"] > data["disk_komprimerat_gb"] > 0.0
    assert data["kortid_cpu_s"] > 0.0
    assert data["kortid_ccx_s"] > 0.0
    assert "skalningsformel" in data and "(1 / upplosning_m)^2" in data["skalningsformel"]
    assert all(a["uppskattning"] is True and a["kalla"] for a in data["antaganden"])

    # Tabellutskrift (default)
    rc_tab = floden_main(
        [
            "geografi",
            "planera",
            "stockholm",
            "--avgransning",
            str(fil),
            "--upplosning-m",
            "1.0",
            "--buffert-m",
            "0",
        ]
    )
    assert rc_tab == 0
    out_tab = capsys.readouterr().out
    assert "UPPSKATTNING" in out_tab
    assert "K38-VARNING" in out_tab
    assert "Stockholms stad" in out_tab
    assert "4 kärnrutor + 0 buffertrutor = 4 rutor totalt" in out_tab
    assert "(1 / upplosning_m)^2" in out_tab
    assert "STATUS.md (#114" in out_tab
    assert "Källbelagda antaganden (uppskattning=True)" in out_tab

    # Direkt formatering stämmer med CLI-utskrift
    plan = planera_geografi("goteborg", fil, upplosning_m=1.0, buffert_m=0.0)
    tab = formatera_plan_tabell(plan)
    assert "Göteborgs Stad" in tab
    assert "OPPEN" in tab
    assert "K38-VARNING" not in tab


def test_tydliga_fel_vid_ogiltiga_indata_och_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Saknad fil
    with pytest.raises(PlaneringsFel, match="finns inte"):
        planera_geografi("stockholm", tmp_path / "saknas.geojson")

    # Tom FeatureCollection
    tom_fil = tmp_path / "tom.geojson"
    tom_fil.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    with pytest.raises(PlaneringsFel, match="Inga geometrier|tom"):
        planera_geografi("stockholm", tom_fil)

    # Ogiltig självkorsande polygon (fluga)
    fluga_fil = tmp_path / "fluga.geojson"
    fluga = Polygon([(150000, 6580000), (151000, 6581000), (151000, 6580000), (150000, 6581000)])
    assert not fluga.is_valid
    fluga_fil.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [150000.0, 6580000.0],
                        [151000.0, 6581000.0],
                        [151000.0, 6580000.0],
                        [150000.0, 6581000.0],
                        [150000.0, 6580000.0],
                    ]
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PlaneringsFel, match="ogiltig geometri"):
        planera_geografi("stockholm", fluga_fil)

    # Icke-polygon (Point)
    punkt_fil = tmp_path / "punkt.geojson"
    punkt_fil.write_text(
        json.dumps({"type": "Point", "coordinates": [150000.0, 6580000.0]}),
        encoding="utf-8",
    )
    with pytest.raises(PlaneringsFel, match="Polygon"):
        planera_geografi("stockholm", punkt_fil)

    giltig = _skriv_1km_geojson_3011(tmp_path / "giltig.geojson")

    # Negativ upplösning / nollupplösning / negativ buffert / ogiltigt antal arbetare / okänd kommun
    with pytest.raises(PlaneringsFel, match="upplosning_m"):
        planera_geografi("stockholm", giltig, upplosning_m=-0.5)
    with pytest.raises(PlaneringsFel, match="upplosning_m"):
        planera_geografi("stockholm", giltig, upplosning_m=0.0)
    with pytest.raises(PlaneringsFel, match="buffert_m"):
        planera_geografi("stockholm", giltig, buffert_m=-10.0)
    with pytest.raises(PlaneringsFel, match="arbetare"):
        planera_geografi("stockholm", giltig, arbetare=0)
    with pytest.raises(KommunKonfigFel, match="finns inte"):
        planera_geografi("finns-inte", giltig)

    # CLI ger tydlig slutkod != 0 och begripligt fel på stderr utan stackspår
    rc_saknas = floden_main(
        ["geografi", "planera", "stockholm", "--avgransning", str(tmp_path / "saknas.geojson")]
    )
    err_saknas = capsys.readouterr().err
    assert rc_saknas != 0
    assert "finns inte" in err_saknas
    assert "Traceback" not in err_saknas

    rc_neg = floden_main(
        [
            "geografi",
            "planera",
            "stockholm",
            "--avgransning",
            str(giltig),
            "--upplosning-m",
            "-1",
        ]
    )
    err_neg = capsys.readouterr().err
    assert rc_neg != 0
    assert "upplosning_m" in err_neg
    assert "Traceback" not in err_neg

    rc_kommun = floden_main(
        ["geografi", "planera", "finns-inte", "--avgransning", str(giltig)]
    )
    err_kommun = capsys.readouterr().err
    assert rc_kommun != 0
    assert "finns-inte" in err_kommun
    assert "Traceback" not in err_kommun
