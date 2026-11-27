"""Nätverksfria enhets-, säkerhets- och filinläsningstester för `data_inlasning.trafikverket`."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

from data_inlasning.cache import DataInlasningFel
from data_inlasning.trafikverket import (
    TRAFIKVERKET_API_URL,
    TrafikLank,
    bygg_trafikverket_xml_fraga,
    hamta_trafikverket_vagnat,
    kontrollera_trafikverket_url,
    las_kommunal_trafik_fil,
    las_lastkajen_trafik_gpkg,
    normalisera_gatunamn,
    tolka_trafikverket_svar,
)
from gemensamt.koordinater import EPSG_INTERN, EPSG_LANTMATERIET, transformera

# Centralt område i Stockholm (EPSG:3011)
BBOX_3011 = (150000.0, 6580000.0, 150400.0, 6580400.0)


def _linje_3011_till_3006_wkt(pts_3011: list[tuple[float, float]]) -> str:
    pts_3006 = [transformera(e, n, EPSG_INTERN, EPSG_LANTMATERIET) for e, n in pts_3011]
    koord = ", ".join(f"{e:.3f} {n:.3f}" for e, n in pts_3006)
    return f"LINESTRING ({koord})"


def test_trafikverket_url_allowlist_och_ssrf_skydd() -> None:
    """Strikt HTTPS + värd-allowlist blockerar HTTP, okända värdar och inloggningsuppgifter."""
    kontrollera_trafikverket_url(TRAFIKVERKET_API_URL)

    for dalig_url in (
        "http://api.trafikinfo.trafikverket.se/v2/data.json",
        "https://evil.example.com/v2/data.json",
        "https://api.trafikinfo.trafikverket.se.evil.com/v2/data.json",
        "https://user:pass@api.trafikinfo.trafikverket.se/v2/data.json",
        "file:///etc/passwd",
    ):
        with pytest.raises(DataInlasningFel):
            kontrollera_trafikverket_url(dalig_url)


def test_trafikverket_api_klient_nätverksfri_omprojicering_och_inga_pahittade_adt() -> None:
    """Hämtar NVDB via injicerad hamtare, omprojicerar 3006->3011 och lämnar saknad ÅDT som None."""
    anrop: list[tuple[str, bytes, Mapping[str, str]]] = []
    wkt_gata1 = _linje_3011_till_3006_wkt([(150050.0, 6580100.0), (150350.0, 6580100.0)])
    wkt_gata2 = _linje_3011_till_3006_wkt([(150100.0, 6580050.0), (150100.0, 6580350.0)])
    # Länk utanför bbox ska filtreras bort
    wkt_utanfor = _linje_3011_till_3006_wkt([(152000.0, 6582000.0), (152200.0, 6582000.0)])

    svar_json = {
        "RESPONSE": {
            "RESULT": [
                {
                    "RoadData": [
                        {
                            "LinkId": "nvdb_1",
                            "StreetName": "Sveavägen",
                            "RoadMainNumber": "73",
                            "RoadOwner": "Kommunal",
                            "SpeedLimit": 50,
                            "NumberOfLanes": 4,
                            "Geometry": {"SWEREF99TM": wkt_gata1},
                        },
                        {
                            "LinkId": "nvdb_2",
                            "StreetName": "Drottninggatan",
                            "RoadOwner": "Kommunal",
                            "SpeedLimit": 30,
                            "NumberOfLanes": 2,
                            "AverageDailyTraffic": 4200,
                            "HeavyVehiclePercentage": 6.5,
                            "MeasurementYear": 2024,
                            "Geometry": {"SWEREF99TM": wkt_gata2},
                        },
                        {
                            "LinkId": "nvdb_utanfor",
                            "StreetName": "Fjärrgatan",
                            "SpeedLimit": 70,
                            "Geometry": {"SWEREF99TM": wkt_utanfor},
                        },
                    ]
                }
            ]
        }
    }

    def fake_hamtare(url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        anrop.append((url, body, headers))
        return json.dumps(svar_json).encode("utf-8")

    hemlig_nyckel = "tv-super-secret-key-987654"
    lankar = hamta_trafikverket_vagnat(
        BBOX_3011,
        api_nyckel=hemlig_nyckel,
        hamtare=fake_hamtare,
    )

    assert len(anrop) == 1
    assert anrop[0][0] == TRAFIKVERKET_API_URL
    assert b"Geometry.SWEREF99TM" in anrop[0][1]
    assert hemlig_nyckel.encode("utf-8") in anrop[0][1]

    assert [lk.lank_id for lk in lankar] == ["nvdb_1", "nvdb_2"]
    lk1, lk2 = lankar

    # Sveavägen saknar ÅDT -> hård regel: ALLA tre trafikfält är None (aldrig påhittat!).
    assert lk1.gatunamn == "Sveavägen"
    assert lk1.vagnummer == "73"
    assert lk1.vaghallare == "Kommunal"
    assert lk1.hastighet_km_h == 50.0
    assert lk1.korfalt == 4
    assert lk1.adt_fordon_dygn is None
    assert lk1.andel_tung_pct is None
    assert lk1.adt_kalla is None
    assert lk1.koordinater_3011[0][0] == pytest.approx(150050.0, abs=0.05)
    assert lk1.koordinater_3011[0][1] == pytest.approx(6580100.0, abs=0.05)

    # Drottninggatan har ÅDT och tung andel.
    assert lk2.adt_fordon_dygn == 4200.0
    assert lk2.andel_tung_pct == 6.5
    assert lk2.matar == 2024
    assert lk2.adt_kalla == "Trafikverket NVDB"
    assert hemlig_nyckel not in repr(lankar)


def test_trafikverket_nyckel_lacker_aldrig_i_felmeddelanden() -> None:
    """API-nyckeln saneras alltid ur felmeddelanden även om hämtaren kastar nyckeln i felet."""
    hemlig = "tv-secret-token-xyz-123"

    def krasch_hamtare(url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        raise RuntimeError(f"HTTP 401 för nyckel {hemlig} på {url}")

    with pytest.raises(DataInlasningFel) as exc_info:
        hamta_trafikverket_vagnat(BBOX_3011, api_nyckel=hemlig, hamtare=krasch_hamtare)
    assert hemlig not in str(exc_info.value)
    assert "***" in str(exc_info.value)

    # XML-escaping förhindrar XML-injektion i nyckel/parametrar.
    xml_bytes = bygg_trafikverket_xml_fraga(BBOX_3011, api_nyckel='nyckel"<>&')
    assert b"&quot;" in xml_bytes and b"&lt;" in xml_bytes

    # API ERROR-block ger tydligt DataInlasningFel.
    with pytest.raises(DataInlasningFel, match="Ogiltig fråga"):
        tolka_trafikverket_svar({"RESPONSE": {"RESULT": [{"ERROR": {"MESSAGE": "Ogiltig fråga"}}]}})


def test_las_lastkajen_trafik_gpkg_och_geojson(tmp_path: Path) -> None:
    """Läser Lastkajen GPKG (3006 -> 3011), räknar tung % och bevarar saknad ÅDT som None."""
    p1 = [
        transformera(e, n, EPSG_INTERN, EPSG_LANTMATERIET)
        for e, n in ((150020.0, 6580150.0), (150380.0, 6580150.0))
    ]
    p2 = [
        transformera(e, n, EPSG_INTERN, EPSG_LANTMATERIET)
        for e, n in ((150200.0, 6580020.0), (150200.0, 6580500.0))
    ]
    p3 = [
        transformera(e, n, EPSG_INTERN, EPSG_LANTMATERIET)
        for e, n in ((150050.0, 6580250.0), (150150.0, 6580250.0))
    ]

    gdf = gpd.GeoDataFrame(
        {
            "RLID": ["rlid_1", "rlid_2", "rlid_3"],
            "gatunamn": ["Haga Nygata", "Sprängkullsgatan", "Skolgatan"],
            "väghållare": ["kommunal", "statlig", "kommunal"],
            "HTHAST": [30, 50, 30],
            "antal_körfält": [2, 4, 2],
            "ÅDT_total": [10000.0, 8000.0, None],
            "ÅDT_tung": [1250.0, None, 300.0],  # rlid_3 saknar ÅDT_total -> tung måste bli None!
            "mätår": [2025, 2023, None],
            "mätmetod": ["slangmätning", None, None],
        },
        geometry=[LineString(p1), LineString(p2), MultiLineString([LineString(p3)])],
        crs=f"EPSG:{EPSG_LANTMATERIET}",
    )
    gpkg_fil = tmp_path / "lastkajen_utdrag.gpkg"
    gdf.to_file(gpkg_fil, driver="GPKG")

    lankar = las_lastkajen_trafik_gpkg(gpkg_fil, bbox_3011=BBOX_3011)
    assert [lk.lank_id for lk in lankar] == ["rlid_1", "rlid_2", "rlid_3"]

    lk1, lk2, lk3 = lankar
    assert lk1.adt_fordon_dygn == 10000.0
    assert lk1.andel_tung_pct == pytest.approx(12.5, abs=0.01)
    assert lk1.matar == 2025
    assert lk1.adt_kalla == "Trafikverket Lastkajen (slangmätning)"
    assert lk1.hastighet_km_h == 30.0
    assert lk1.korfalt == 2

    # rlid_2 klipptes vid n_max = 6580400 och saknar ÅDT_tung -> andel_tung_pct är None!
    assert lk2.adt_fordon_dygn == 8000.0
    assert lk2.andel_tung_pct is None
    assert lk2.adt_kalla == "Trafikverket Lastkajen"
    assert max(n for _, n in lk2.koordinater_3011) == pytest.approx(6580400.0, abs=0.05)

    # rlid_3 saknar ÅDT_total -> hård regel: adt, andel_tung_pct och adt_kalla ALLA None!
    assert lk3.adt_fordon_dygn is None
    assert lk3.andel_tung_pct is None
    assert lk3.adt_kalla is None

    # GeoPackage utan CRS utan kall_epsg ger tydligt DataInlasningFel (aldrig gissning).
    utan_crs = gpd.GeoDataFrame(
        {"RLID": ["x"], "ÅDT_total": [1000.0]},
        geometry=[LineString([(150050.0, 6580050.0), (150150.0, 6580050.0)])],
    )
    gpkg_utan_crs = tmp_path / "utan_crs.gpkg"
    utan_crs.to_file(gpkg_utan_crs, driver="GPKG")
    with pytest.raises(DataInlasningFel, match="saknar koordinatsystem"):
        las_lastkajen_trafik_gpkg(gpkg_utan_crs)


def test_las_kommunal_trafik_fil_csv_och_geojson_matchning_med_osakerhet(tmp_path: Path) -> None:
    """Matchar kommunala CSV/GeoJSON deterministiskt och flaggar tvetydighet med osaker=True."""
    assert normalisera_gatunamn("  Södra Allégatan - 12 ") == "södra allégatan 12"

    bas_lankar = [
        TrafikLank(
            lank_id="v_1",
            koordinater_3011=((150020.0, 6580100.0), (150180.0, 6580100.0)),
            gatunamn="Södra Allégatan",
            hastighet_km_h=40.0,
        ),
        TrafikLank(
            lank_id="v_2",
            koordinater_3011=((150020.0, 6580200.0), (150180.0, 6580200.0)),
            gatunamn="Vasagatan",
            hastighet_km_h=None,
        ),
        TrafikLank(
            lank_id="v_3",
            koordinater_3011=((150020.0, 6580300.0), (150180.0, 6580300.0)),
            gatunamn="Lilla Torggränd",
            hastighet_km_h=30.0,
        ),
    ]

    # 1. CSV med Göteborgs trafikmängdskatalog (ÅMVD, tung %, hastighet):
    #    - Södra Allégatan: entydig matchning -> osaker=False
    #    - Vasagatan: två motstridiga poster utan koordinater -> tvetydig matchning -> osaker=True!
    #    - Lilla Torggränd: saknas i CSV -> adt/tung/kalla = None
    csv_fil = tmp_path / "goteborg_trafik.csv"
    csv_fil.write_text(
        "gatunamn;ÅMVD;tung_pct;hastighet_km_h;mätår\n"
        "Södra Allégatan;14500;8,5;40;2025\n"
        "Vasagatan;9200;5,0;30;2024\n"
        "Vasagatan;11800;6,0;30;2025\n",
        encoding="utf-8",
    )

    matchade = las_kommunal_trafik_fil(
        csv_fil,
        vagnat_lankar=bas_lankar,
        bbox_3011=BBOX_3011,
        adt_kalla="Göteborgs Stad trafikmängdskatalog",
    )
    per_id = {lk.lank_id: lk for lk in matchade}
    assert per_id["v_1"].adt_fordon_dygn == 14500.0
    assert per_id["v_1"].andel_tung_pct == 8.5
    assert per_id["v_1"].osaker is False
    assert per_id["v_1"].adt_kalla == "Göteborgs Stad trafikmängdskatalog"

    assert per_id["v_2"].adt_fordon_dygn == 11800.0  # nyaste mätår deterministiskt
    assert per_id["v_2"].hastighet_km_h == 30.0  # fylldes från kommunfil när väglänken saknade
    assert per_id["v_2"].osaker is True  # tvetydig matchning tydligt flaggad!

    assert per_id["v_3"].adt_fordon_dygn is None
    assert per_id["v_3"].andel_tung_pct is None
    assert per_id["v_3"].adt_kalla is None

    # 2. Rumslig GeoJSON med mätpunkter: två punkter nära samma gata med olika ÅMVD -> osaker=True
    gdf_pkt = gpd.GeoDataFrame(
        {
            "id": ["m_a", "m_b"],
            "gatunamn": ["Södra Allégatan", "Södra Allégatan"],
            "ÅMVD": [14000.0, 15500.0],
            "tung_pct": [8.0, 9.0],
            "mätår": [2025, 2025],
        },
        geometry=[Point(150100.0, 6580102.0), Point(150105.0, 6580103.0)],
        crs=f"EPSG:{EPSG_INTERN}",
    )
    geojson_fil = tmp_path / "matpunkter.geojson"
    gdf_pkt.to_file(geojson_fil, driver="GeoJSON")
    matchade_pkt = las_kommunal_trafik_fil(
        geojson_fil,
        vagnat_lankar=[bas_lankar[0]],
        kall_epsg=EPSG_INTERN,
    )
    assert len(matchade_pkt) == 1
    assert matchade_pkt[0].osaker is True
    assert matchade_pkt[0].adt_fordon_dygn in (14000.0, 15500.0)

    # 3. Direkt inläsning av kommunal linje-GeoPackage utan vagnat_lankar
    gdf_linje = gpd.GeoDataFrame(
        {"lank_id": ["k_1"], "gatunamn": ["Linnégatan"], "ÅMVD": [12300.0], "tung_pct": [7.5]},
        geometry=[LineString([(150050.0, 6580050.0), (150250.0, 6580050.0)])],
        crs=f"EPSG:{EPSG_INTERN}",
    )
    linje_gpkg = tmp_path / "kommun_linjer.gpkg"
    gdf_linje.to_file(linje_gpkg, driver="GPKG")
    direkt = las_kommunal_trafik_fil(linje_gpkg, bbox_3011=BBOX_3011)
    assert len(direkt) == 1
    assert direkt[0].gatunamn == "Linnégatan"
    assert direkt[0].adt_fordon_dygn == 12300.0
    assert direkt[0].vaghallare == "kommunal"
