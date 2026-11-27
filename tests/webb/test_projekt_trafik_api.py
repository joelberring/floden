"""Tester för `hamta_projekt_trafik` och `spara_projekt_trafik` i `webb.cad_buller_api`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_inlasning.trafikverket import TrafikLank
from gemensamt.hash import sha256_fil
from geografi.modell import KONTRAKT_GEOGRAFI, lager_mapp, skriv_manifest
from geografi.trafik_lager import TRAFIK_GPKG_FIL, bygg_trafik_lager
from webb.cad_buller_api import (
    FORMAT_PROJEKT_TRAFIK_WEBB,
    hamta_projekt_trafik,
    spara_projekt_trafik,
)
from webb.fel import FlodenWebbFel
from webb.projektkalla import KONTRAKT_PROJEKT

KARNA_3011 = (150000.0, 6580000.0, 150300.0, 6580300.0)


def _skapa_projekt_med_trafik(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    geo_rot = tmp_path / "geo"
    lankar = [
        TrafikLank(
            lank_id="nvdb_1",
            koordinater_3011=((150020.0, 6580100.0), (150280.0, 6580100.0)),
            gatunamn="Sprängkullsgatan",
            vaghallare="kommunal",
            hastighet_km_h=40.0,
            korfalt=2,
            adt_fordon_dygn=11000.0,
            andel_tung_pct=9.0,
            matar=2025,
            adt_kalla="Trafikverket Lastkajen",
            osaker=False,
        ),
        TrafikLank(
            lank_id="kom_2",
            koordinater_3011=((150100.0, 6580020.0), (150100.0, 6580280.0)),
            gatunamn="Vasagatan",
            vaghallare="kommunal",
            hastighet_km_h=30.0,
            korfalt=2,
            adt_fordon_dygn=8400.0,
            andel_tung_pct=5.5,
            matar=2024,
            adt_kalla="Göteborgs Stad trafikmängdskatalog",
            osaker=True,
        ),
        TrafikLank(
            lank_id="lokal_3",
            koordinater_3011=((150200.0, 6580050.0), (150200.0, 6580250.0)),
            gatunamn="Haga Nygata",
            vaghallare="kommunal",
            hastighet_km_h=30.0,
            adt_fordon_dygn=None,
            andel_tung_pct=None,
            adt_kalla=None,
            osaker=False,
        ),
    ]
    lager_post, kalla_post, _ = bygg_trafik_lager(
        geo_rot,
        tmp_path / "bygge_trafik",
        KARNA_3011,
        lankar=lankar,
        kalla_beskrivning="NVDB + Göteborgs Stad",
        licens="CC0-1.0",
        villkor_kontrollerade=True,
    )
    manifest_fil, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "goteborg-haga",
            "version": "v1",
            "kommun": "goteborg",
            "karna_3011": list(KARNA_3011),
            "gransen_3011": list(KARNA_3011),
            "lager": {"trafik": lager_post},
            "kallor": {"trafik": kalla_post},
            "oppen_for_mcp": True,
        },
    )
    projekt_mapp = tmp_path / "projekt" / "haga-plan"
    projekt_mapp.mkdir(parents=True)
    (projekt_mapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": "haga-plan",
                "register_id": "proj-haga-1",
                "geografi": "goteborg-haga@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": [150040.0, 6580040.0, 150260.0, 6580260.0],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    gpkg_fil = lager_mapp(geo_rot, lager_post["sha256"]) / TRAFIK_GPKG_FIL
    return projekt_mapp, geo_rot, manifest_fil, sha256_fil(gpkg_fil)


def test_hamta_och_spara_projekt_trafik_geojson_och_skydd_av_radata(tmp_path: Path) -> None:
    """GET/POST-hjälpare ger EPSG:3011 GeoJSON med nuläge, prognos, källmärke och orörd rådata."""
    projekt_mapp, geo_rot, manifest_fil, gpkg_sha_fore = _skapa_projekt_med_trafik(tmp_path)

    fc = hamta_projekt_trafik(projekt_mapp, geo_rot=geo_rot)
    assert fc["type"] == "FeatureCollection"
    assert fc["format"] == FORMAT_PROJEKT_TRAFIK_WEBB
    assert fc["koordinatsystem"] == "EPSG:3011"
    assert fc["projekt_id"] == "proj-haga-1"
    assert fc["har_trafiklager"] is True
    assert fc["sammanfattning"]["antal_lankar"] == 3
    assert fc["sammanfattning"]["antal_med_adt"] == 2
    assert fc["sammanfattning"]["antal_utan_adt"] == 1
    assert fc["sammanfattning"]["antal_osakra"] == 1

    per_id = {f["id"]: f for f in fc["features"]}
    assert per_id["nvdb_1"]["properties"]["kallmarke"] == "NVDB"
    assert per_id["nvdb_1"]["properties"]["har_adt"] is True
    assert per_id["nvdb_1"]["geometry"]["coordinates"][0][0] == pytest.approx(150040.0)
    assert per_id["nvdb_1"]["geometry"]["coordinates"][-1][0] == pytest.approx(150260.0)

    assert per_id["kom_2"]["properties"]["kallmarke"] == "Kommun"
    assert per_id["kom_2"]["properties"]["osaker"] is True

    assert per_id["lokal_3"]["properties"]["adt_fordon_dygn"] is None
    assert per_id["lokal_3"]["properties"]["andel_tung_pct"] is None
    assert per_id["lokal_3"]["properties"]["kallmarke"] is None
    assert per_id["lokal_3"]["properties"]["har_adt"] is False

    # Spara egna justeringar (nuläge + prognos på lokal_3 samt prognos på nvdb_1).
    kropp = json.dumps(
        {
            "justeringar": [
                {
                    "lank_id": "lokal_3",
                    "adt_fordon_dygn": 3200.0,
                    "prognos_adt_fordon_dygn": 4800.0,
                    "hastighet_km_h": 30.0,
                    "andel_tung_pct": 4.0,
                },
                {
                    "lank_id": "nvdb_1",
                    "prognos_adt_fordon_dygn": 14500.0,
                },
            ]
        }
    ).encode("utf-8")
    uppd = spara_projekt_trafik(projekt_mapp, kropp, geo_rot=geo_rot)
    assert uppd["sammanfattning"]["antal_med_adt"] == 3
    assert uppd["sammanfattning"]["antal_med_prognos"] == 2
    assert uppd["sammanfattning"]["antal_egna_justeringar"] == 2

    u_per_id = {f["id"]: f["properties"] for f in uppd["features"]}
    assert u_per_id["lokal_3"]["adt_fordon_dygn"] == 3200.0
    assert u_per_id["lokal_3"]["prognos_adt_fordon_dygn"] == 4800.0
    assert u_per_id["lokal_3"]["kallmarke"] == "Egen"
    assert u_per_id["lokal_3"]["egen_justering"] is True
    assert u_per_id["lokal_3"]["original_adt_fordon_dygn"] is None

    assert u_per_id["nvdb_1"]["adt_fordon_dygn"] == 11000.0
    assert u_per_id["nvdb_1"]["prognos_adt_fordon_dygn"] == 14500.0
    assert u_per_id["nvdb_1"]["kallmarke"] == "Egen"

    # Ta bort egen justering på lokal_3 -> återgår till null (aldrig påhittat!).
    ater = spara_projekt_trafik(
        projekt_mapp,
        {"justeringar": [{"lank_id": "lokal_3", "ta_bort": True}]},
        manifest_fil=manifest_fil,
    )
    a_per_id = {f["id"]: f["properties"] for f in ater["features"]}
    assert a_per_id["lokal_3"]["adt_fordon_dygn"] is None
    assert a_per_id["lokal_3"]["kallmarke"] is None

    # Geografins rådatafil är bitidentiskt orörd.
    mf = json.loads(manifest_fil.read_text(encoding="utf-8"))
    gpkg_fil = lager_mapp(geo_rot, mf["lager"]["trafik"]["sha256"]) / TRAFIK_GPKG_FIL
    assert sha256_fil(gpkg_fil) == gpkg_sha_fore


def test_projekt_trafik_felhantering_och_avsaknad_av_trafiklager(tmp_path: Path) -> None:
    """Validerar 400/404/409-fel och graciöst tomt svar när geografin saknar lager.trafik."""
    with pytest.raises(FlodenWebbFel) as exc_404:
        hamta_projekt_trafik(tmp_path / "finns_inte")
    assert exc_404.value.status_kod == 404

    tomt_proj = tmp_path / "projekt_utan_trafik"
    tomt_proj.mkdir()
    tomt_fc = hamta_projekt_trafik(tomt_proj)
    assert tomt_fc["type"] == "FeatureCollection"
    assert tomt_fc["har_trafiklager"] is False
    assert tomt_fc["features"] == []

    # Ogiltiga trafikvärden ger HTTP 400.
    for dalig in (
        {"justeringar": [{"lank_id": "a", "adt_fordon_dygn": -5}]},
        {"justeringar": [{"lank_id": "a", "hastighet_km_h": 0}]},
        {"justeringar": [{"lank_id": "a", "andel_tung_pct": 120}]},
        {"justeringar": [{"lank_id": "a", "prognos_adt_fordon_dygn": float("nan")}]},
        {},
    ):
        with pytest.raises(FlodenWebbFel) as exc_400:
            spara_projekt_trafik(tomt_proj, dalig)
        assert exc_400.value.status_kod == 400

    # Manipulerat trafiklager ger HTTP 409 (inga tysta fel).
    projekt_mapp, geo_rot, manifest_fil, _ = _skapa_projekt_med_trafik(tmp_path / "manip")
    mf = json.loads(manifest_fil.read_text(encoding="utf-8"))
    gpkg_fil = lager_mapp(geo_rot, mf["lager"]["trafik"]["sha256"]) / TRAFIK_GPKG_FIL
    gpkg_fil.write_bytes(b"korrupt")
    with pytest.raises(FlodenWebbFel) as exc_409:
        hamta_projekt_trafik(projekt_mapp, geo_rot=geo_rot)
    assert exc_409.value.status_kod == 409
