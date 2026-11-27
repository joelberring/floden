"""Tester för K46 `lager.trafik` (`geografi.trafik_lager`) och projektets trafikjusteringar."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest

from data_inlasning.trafikverket import TrafikLank
from gemensamt.hash import sha256_fil
from geografi.modell import KONTRAKT_GEOGRAFI, GeografiFel, lager_mapp, las_manifest, skriv_manifest
from geografi.trafik_lager import (
    PROJEKT_JUSTERINGAR_FIL,
    STANDARD_EGEN_KALLA,
    TRAFIK_GPKG_FIL,
    TRAFIK_JSON_FIL,
    bygg_trafik_lager,
    las_projekt_trafik_justeringar,
    las_trafik_lager,
    spara_projekt_trafik_justeringar,
)

KARNA_3011 = (150000.0, 6580000.0, 150300.0, 6580300.0)


def _exempel_lankar() -> list[TrafikLank]:
    return [
        TrafikLank(
            lank_id="lank_med_adt",
            koordinater_3011=((149950.0, 6580100.0), (150350.0, 6580100.0)),
            gatunamn="Sprängkullsgatan",
            vagnummer="155",
            vaghallare="kommunal",
            hastighet_km_h=40.0,
            korfalt=2,
            adt_fordon_dygn=12500.0,
            andel_tung_pct=8.5,
            matar=2025,
            adt_kalla="Göteborgs Stad trafikmängdskatalog",
            osaker=False,
        ),
        TrafikLank(
            lank_id="lank_utan_adt",
            koordinater_3011=((150120.0, 6580050.0), (150120.0, 6580250.0)),
            gatunamn="Haga Nygata",
            vaghallare="kommunal",
            hastighet_km_h=30.0,
            korfalt=1,
            adt_fordon_dygn=None,
            andel_tung_pct=None,
            matar=None,
            adt_kalla=None,
            osaker=False,
        ),
        TrafikLank(
            lank_id="lank_utanfor",
            koordinater_3011=((151000.0, 6581000.0), (151200.0, 6581000.0)),
            gatunamn="Utanförgatan",
            adt_fordon_dygn=5000.0,
            adt_kalla="NVDB",
        ),
    ]


def _skriv_testmanifest(
    geo_rot: Path,
    lager_post: dict[str, Any],
    kalla_post: dict[str, Any],
    *,
    geo_id: str = "goteborg-test",
    version: str = "v1",
) -> Path:
    fil, _ = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": geo_id,
            "version": version,
            "kommun": "goteborg",
            "karna_3011": list(KARNA_3011),
            "gransen_3011": list(KARNA_3011),
            "lager": {"trafik": lager_post},
            "kallor": {"trafik": kalla_post},
            "oppen_for_mcp": True,
        },
    )
    return fil


def test_bygg_och_las_trafik_lager_byteidentisk_och_gpkg_kompatibel(tmp_path: Path) -> None:
    """Bygger K46 lager.trafik deterministiskt med GPKG, K38 oppenhet.toml och bbox-klippning."""
    geo_rot = tmp_path / "geo"
    lankar = _exempel_lankar()

    lager1, kalla1, varningar1 = bygg_trafik_lager(
        geo_rot,
        tmp_path / "bygge1",
        KARNA_3011,
        lankar=lankar,
        kalla_beskrivning="Trafikverket NVDB + Göteborgs Stad",
        licens="CC0-1.0",
        villkor_kontrollerade=True,
    )
    lager2, _, _ = bygg_trafik_lager(
        geo_rot,
        tmp_path / "bygge2",
        KARNA_3011,
        lankar=lankar,
        kalla_beskrivning="Trafikverket NVDB + Göteborgs Stad",
        licens="CC0-1.0",
        villkor_kontrollerade=True,
    )

    # Byte-identisk innehållsadressering över upprepade byggen.
    assert lager1["sha256"] == lager2["sha256"]
    assert lager1["trafik_gpkg_sha256"] == lager2["trafik_gpkg_sha256"]
    assert lager1["antal_lankar"] == 2
    assert lager1["antal_med_adt"] == 1
    assert lager1["antal_utan_adt"] == 1
    assert any("saknar ÅDT" in v for v in varningar1)

    mapp = lager_mapp(geo_rot, lager1["sha256"])
    assert (mapp / TRAFIK_GPKG_FIL).is_file()
    assert (mapp / TRAFIK_JSON_FIL).is_file()

    # K38 oppenhet.toml skrivs med öppen=True för CC0-1.0 + villkor_kontrollerade=True.
    with (mapp / "oppenhet.toml").open("rb") as fh:
        opp = tomllib.load(fh)
    assert opp["kontrakt"] == "floden-oppenhet/v1"
    assert opp["oppen"] is True
    assert opp["licens"] == "CC0-1.0"

    # Standardkompatibel GeoPackage läsbar av geopandas med EPSG:3011 och alla kolumner.
    gdf = gpd.read_file(mapp / TRAFIK_GPKG_FIL, layer="trafik")
    assert gdf.crs is not None and gdf.crs.to_epsg() == 3011
    for kol in (
        "lank_id",
        "gatunamn",
        "vaghallare",
        "hastighet_km_h",
        "korfalt",
        "adt_fordon_dygn",
        "andel_tung_pct",
        "matar",
        "adt_kalla",
        "osaker",
    ):
        assert kol in gdf.columns

    # Läs tillbaka via manifest med klippning till en mindre projekt-bbox.
    manifest_fil = _skriv_testmanifest(geo_rot, lager1, kalla1)
    proj_bbox = (150050.0, 6580080.0, 150250.0, 6580200.0)
    ut = las_trafik_lager(manifest_fil, bbox_3011=proj_bbox)
    assert ut.trafik_sha256 == lager1["sha256"]
    assert [lk.lank_id for lk in ut.lankar] == ["lank_med_adt", "lank_utan_adt"]

    med = ut.lankar[0]
    utan = ut.lankar[1]
    assert med.koordinater_3011[0][0] == pytest.approx(150050.0)
    assert med.koordinater_3011[-1][0] == pytest.approx(150250.0)
    assert med.adt_fordon_dygn == 12500.0
    assert med.andel_tung_pct == 8.5

    assert utan.adt_fordon_dygn is None
    assert utan.andel_tung_pct is None
    assert utan.adt_kalla is None


def test_trafik_lager_oppenhet_stangd_och_sha256_manipulationsdetektion(tmp_path: Path) -> None:
    """Stängd öppenhet för intern/okontrollerad källa och SHA-256-detektion vid manipulation."""
    geo_rot = tmp_path / "geo"
    lager, kalla, _ = bygg_trafik_lager(
        geo_rot,
        tmp_path / "bygge",
        KARNA_3011,
        lankar=_exempel_lankar(),
        kalla_beskrivning="Kommunal intern fil",
        licens="intern",
        villkor_kontrollerade=True,
    )
    mapp = lager_mapp(geo_rot, lager["sha256"])
    with (mapp / "oppenhet.toml").open("rb") as fh:
        opp = tomllib.load(fh)
    assert opp["oppen"] is False
    assert opp["licens"] == "intern"

    manifest_fil = _skriv_testmanifest(geo_rot, lager, kalla)
    # Manipulera trafik.json -> SHA-256-verifieringen måste avvisa direkt.
    (mapp / TRAFIK_JSON_FIL).write_text('{"manipulerad": true}\n', encoding="utf-8")
    with pytest.raises(GeografiFel, match="sha256"):
        las_trafik_lager(manifest_fil)


def test_tomt_trafiklager_och_additiv_k46_bakatkompatibilitet(tmp_path: Path) -> None:
    """Tomt trafiklager fungerar rent och manifest utan lager.trafik fortsätter fungera i K46."""
    geo_rot = tmp_path / "geo"
    lager, kalla, varningar = bygg_trafik_lager(
        geo_rot,
        tmp_path / "bygge_tom",
        KARNA_3011,
        lankar=[],
        kalla_beskrivning="Tom källa",
        licens="CC0-1.0",
        villkor_kontrollerade=False,
    )
    assert lager["antal_lankar"] == 0
    assert any("tomt" in v.lower() for v in varningar)
    manifest_fil = _skriv_testmanifest(geo_rot, lager, kalla, geo_id="tom-geo")
    ut = las_trafik_lager(manifest_fil)
    assert ut.lankar == ()

    # K46 manifest utan lager.trafik är giltigt men las_trafik_lager ger tydligt GeografiFel.
    mf_utan, _ = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "utan-trafik",
            "version": "v1",
            "kommun": "stockholm",
            "karna_3011": list(KARNA_3011),
            "gransen_3011": list(KARNA_3011),
            "lager": {},
            "kallor": {},
        },
    )
    las_manifest(geo_rot, "utan-trafik@v1")
    with pytest.raises(GeografiFel, match="lager.trafik"):
        las_trafik_lager(mf_utan)


def test_projekt_trafik_justeringar_atomisk_och_ror_aldrig_geografi(tmp_path: Path) -> None:
    """Sparar projektets egna justeringar med strikt validering utan att röra geografins rådata."""
    geo_rot = tmp_path / "geo"
    lager, kalla, _ = bygg_trafik_lager(
        geo_rot,
        tmp_path / "bygge",
        KARNA_3011,
        lankar=_exempel_lankar(),
        kalla_beskrivning="NVDB",
        licens="CC0-1.0",
        villkor_kontrollerade=True,
    )
    mapp = lager_mapp(geo_rot, lager["sha256"])
    gpkg_sha_fore = sha256_fil(mapp / TRAFIK_GPKG_FIL)

    projekt_mapp = tmp_path / "projekt_haga"
    projekt_mapp.mkdir()
    assert las_projekt_trafik_justeringar(projekt_mapp) == {}

    fil = spara_projekt_trafik_justeringar(
        projekt_mapp,
        [
            {
                "lank_id": "lank_utan_adt",
                "adt_fordon_dygn": 6500.0,
                "prognos_adt_fordon_dygn": 8200.0,
                "hastighet_km_h": 40.0,
                "andel_tung_pct": 7.5,
            }
        ],
    )
    assert fil == projekt_mapp / PROJEKT_JUSTERINGAR_FIL
    sparat_doc = json.loads(fil.read_text(encoding="utf-8"))
    assert sparat_doc["justeringar"][0]["kalla"] == STANDARD_EGEN_KALLA

    lasta = las_projekt_trafik_justeringar(projekt_mapp)
    assert "lank_utan_adt" in lasta
    assert lasta["lank_utan_adt"].adt_fordon_dygn == 6500.0
    assert lasta["lank_utan_adt"].prognos_adt_fordon_dygn == 8200.0
    assert lasta["lank_utan_adt"].kalla == STANDARD_EGEN_KALLA

    # Geografins rådata är 100 % orörda.
    assert sha256_fil(mapp / TRAFIK_GPKG_FIL) == gpkg_sha_fore

    # Ogiltiga värden (NaN, Inf, bool, negativ ÅDT, hastighet <= 0, tung > 100) avvisas.
    for dalig in (
        [{"lank_id": "x", "adt_fordon_dygn": float("nan")}],
        [{"lank_id": "x", "prognos_adt_fordon_dygn": float("inf")}],
        [{"lank_id": "x", "adt_fordon_dygn": -10.0}],
        [{"lank_id": "x", "adt_fordon_dygn": True}],
        [{"lank_id": "x", "hastighet_km_h": 0.0}],
        [{"lank_id": "x", "andel_tung_pct": 105.0}],
        [{"lank_id": "", "adt_fordon_dygn": 100.0}],
    ):
        with pytest.raises(GeografiFel):
            spara_projekt_trafik_justeringar(projekt_mapp, dalig)
