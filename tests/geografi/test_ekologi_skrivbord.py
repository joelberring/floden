# ruff: noqa: E501
"""Tester för `geografi.ekologi_skrivbord`: additiva K46-lager (`lager.skyddad_natur`, `lager.artfynd_publika`),
SHA-256-verifiering, K38 `oppenhet.toml`, OSL/0-PII-garantier samt projektets fältlagerimport."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from data_inlasning.artdatabanken import (
    OSL_VARNING,
    tolka_och_filtrera_sos_svar,
)
from data_inlasning.skyddad_natur import SkogligtNaturvarde, SkyddatOmrade
from geografi.ekologi_skrivbord import (
    ARTLISTA_FIL,
    FALTLAGER_GEOJSON,
    FALTLAGER_MAPP,
    MAX_FALTLAGER_BYTES,
    PUBLIKA_FYND_FIL,
    SKRIVBORD_SAMMANFATTNING_FIL,
    SPARADE_FALT_KOLUMNER,
    bygg_artfynd_publika_lager,
    bygg_skyddad_natur_lager,
    importera_ekologi_faltlager,
    las_ekologi_faltlager,
    las_ekologi_skrivbord,
)
from geografi.modell import KONTRAKT_GEOGRAFI, GeografiFel, lager_mapp, skriv_manifest

KARNA_3011 = (-210_000.0, 6_413_500.0, -209_500.0, 6_414_000.0)


def _skapa_skydd_och_skog() -> tuple[list[SkyddatOmrade], list[SkogligtNaturvarde]]:
    nr = SkyddatOmrade(
        omrade_id="NV-1",
        namn="Änggårdsbergen",
        skyddsform="NR",
        skyddsform_namn="Naturreservat",
        lager="ps-nvr:PS.ProtectedSites.NR",
        beslutsdatum="1975-06-12",
        kalla="Naturvårdsverket INSPIRE WFS (CC0)",
        geometri_3011=Polygon(
            [
                (-210_000.0, 6_413_500.0),
                (-209_900.0, 6_413_500.0),
                (-209_900.0, 6_413_600.0),
                (-210_000.0, 6_413_600.0),
            ]
        ),
        area_m2=10_000.0,
        overlapp_karna_m2=10_000.0,
        overlappar_karna=True,
        minsta_avstand_m=0.0,
    )
    nb = SkogligtNaturvarde(
        objekt_id="N-42",
        kategori="nyckelbiotop",
        kategori_namn="Nyckelbiotop",
        namn="Haga ekbacke",
        biotoptyp="Ädellövskog",
        beslutsdatum="2012-09-01",
        tjanst="Geodataportal/GeodataportalVisaNyckelbiotop/MapServer/0/query",
        kalla="Skogsstyrelsen Geodataportal (CC0)",
        geometri_3011=Polygon(
            [
                (-209_400.0, 6_413_600.0),
                (-209_300.0, 6_413_600.0),
                (-209_300.0, 6_413_700.0),
                (-209_400.0, 6_413_700.0),
            ]
        ),
        area_m2=10_000.0,
        overlapp_karna_m2=0.0,
        overlappar_karna=False,
        minsta_avstand_m=100.0,
    )
    return [nr], [nb]


def test_k46_additiva_ekologilager_sha256_oppenhet_och_bakatkompatibilitet(tmp_path: Path) -> None:
    geo_rot = tmp_path / "geo"
    bygge = tmp_path / "bygge"

    # 1. Bakåtkompatibilitet: befintlig geografi helt utan skrivbordslager ska gå att läsa
    manifest_tom = {
        "kontrakt": KONTRAKT_GEOGRAFI,
        "id": "gbg-tom",
        "version": "v1",
        "lager": {},
        "kallor": {},
    }
    mfil_tom, _ = skriv_manifest(geo_rot, manifest_tom)
    tom = las_ekologi_skrivbord(mfil_tom)
    assert tom.skyddad_natur_sha256 is None
    assert tom.artfynd_sha256 is None
    assert tom.sammanfattning["skyddad_natur_tillganglig"] is False
    assert tom.sammanfattning["artfynd_tillganglig"] is False
    assert tom.osl_varning == OSL_VARNING
    assert len(tom.faltchecklista) == 4

    # 2. Full geografi med både lager.skyddad_natur och lager.artfynd_publika
    skydd, skog = _skapa_skydd_och_skog()
    lg_skydd, kl_skydd, _ = bygg_skyddad_natur_lager(
        geo_rot,
        bygge,
        KARNA_3011,
        skyddade_omraden=skydd,
        skogliga_varden=skog,
        vmi_sammanfattning={"tillganglig": True, "antal_polygoner": 1, "overlapp_karna_m2": 500.0},
        buffert_m=1000.0,
        kallor_sha256={"nv": "abc123"},
        villkor_kontrollerade=True,
    )
    art_res = tolka_och_filtrera_sos_svar(
        [
            {
                "taxon": {"id": 1001, "vernacularName": "Ekoxe", "scientificName": "Lucanus cervus", "redListCategory": "NT"},
                "event": {"startDate": "2024-07-01"},
                "location": {"e_m": -209_700.0, "n_m": 6_413_700.0, "coordinateUncertaintyInMeters": 10},
                "recordedBy": "HEMLIG OBSERVATÖR",
            },
            {
                "taxon": {"id": 1002, "vernacularName": "Kungsörn", "scientificName": "Aquila chrysaetos", "redListCategory": "NT"},
                "event": {"startDate": "2024-07-02"},
                "location": {"e_m": -209_600.0, "n_m": 6_413_600.0, "coordinateUncertaintyInMeters": 5000},
            },
        ],
        karna_3011=KARNA_3011,
    )
    lg_art, kl_art, _ = bygg_artfynd_publika_lager(
        geo_rot, bygge, KARNA_3011, resultat=art_res, villkor_kontrollerade=False
    )

    # Kontrollera K38 oppenhet.toml på båda lagren
    skydd_mapp = lager_mapp(geo_rot, lg_skydd["sha256"])
    art_mapp = lager_mapp(geo_rot, lg_art["sha256"])
    assert "oppen = true" in (skydd_mapp / "oppenhet.toml").read_text(encoding="utf-8")
    assert "oppen = false" in (art_mapp / "oppenhet.toml").read_text(encoding="utf-8")

    # Kontrollera 0 PII och 0 skyddsklassade koordinater på disk
    fynd_text = (art_mapp / PUBLIKA_FYND_FIL).read_text(encoding="utf-8")
    artlista_text = (art_mapp / ARTLISTA_FIL).read_text(encoding="utf-8")
    assert "HEMLIG OBSERVATÖR" not in fynd_text and "HEMLIG OBSERVATÖR" not in artlista_text
    assert "Kungsörn" not in fynd_text and "Kungsörn" not in artlista_text
    assert json.loads(artlista_text)["antal_skyddsklassade_filtrerade"] == 1

    manifest = {
        "kontrakt": KONTRAKT_GEOGRAFI,
        "id": "gbg-haga",
        "version": "v1",
        "lager": {"skyddad_natur": lg_skydd, "artfynd_publika": lg_art},
        "kallor": {"skyddad_natur": kl_skydd, "artfynd_publika": kl_art},
    }
    mfil, _ = skriv_manifest(geo_rot, manifest)

    # Läs med originalkärna
    underlag = las_ekologi_skrivbord(mfil)
    assert underlag.sammanfattning["skyddade_omraden"]["antal"] == 1
    assert underlag.sammanfattning["skyddade_omraden"]["antal_overlappar_karna"] == 1
    assert underlag.sammanfattning["skogliga_varden"]["antal_nyckelbiotoper"] == 1
    assert underlag.sammanfattning["artfynd_publika"]["antal_publika_fynd"] == 1
    assert underlag.sammanfattning["artfynd_publika"]["antal_skyddsklassade_filtrerade"] == 1
    assert len(underlag.skyddade_omraden_geojson["features"]) == 1
    assert len(underlag.publika_fynd_geojson["features"]) == 1

    # Läs med en förskjuten projektkärna (räknar om avstånd deterministiskt!)
    underlag_proj = las_ekologi_skrivbord(
        mfil, karna_3011=(-209_800.0, 6_413_500.0, -209_500.0, 6_414_000.0)
    )
    assert underlag_proj.sammanfattning["skyddade_omraden"]["antal_overlappar_karna"] == 0
    assert underlag_proj.sammanfattning["skyddade_omraden"]["minsta_avstand_m"] == pytest.approx(100.0, abs=0.5)

    # SHA-256-manipulering måste ge GeografiFel
    sam_fil = skydd_mapp / SKRIVBORD_SAMMANFATTNING_FIL
    sam_fil.write_text(sam_fil.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(GeografiFel, match="sha256"):
        las_ekologi_skrivbord(mfil)


def test_ekologi_faltlager_import_strip_pii_crs_storlek_och_sha256(tmp_path: Path) -> None:
    proj = tmp_path / "projekt_haga"
    proj.mkdir()
    assert las_ekologi_faltlager(proj) is None

    # Skapa GeoPackage i EPSG:3007 (Göteborg) med både NVI-polygon, skyddsvärt träd och PII-kolumner
    gpkg_fil = tmp_path / "inventering.gpkg"
    gdf = gpd.GeoDataFrame(
        {
            "objekt_typ": ["naturvardesobjekt", "skyddsvart_trad"],
            "nvi_klass": [2, None],
            "biotop": ["Ädellövskog", None],
            "tradslag": [None, "Ek"],
            "stamomkrets_cm": [None, 315.5],
            "inventerare": ["Karin Ekolog", "Karin Ekolog"],
            "epost": ["karin@example.se", "karin@example.se"],
            "fastighet": ["Haga 1:23", "Haga 1:24"],
            "kommentar": ["Privat anteckning 19790101-1234", "Ring ägaren"],
        },
        geometry=[
            Polygon([(147500.0, 6397600.0), (147550.0, 6397600.0), (147550.0, 6397650.0), (147500.0, 6397650.0)]),
            Point(147525.0, 6397625.0),
        ],
        crs=3007,
    )
    gdf.to_file(gpkg_fil, driver="GPKG")

    post = importera_ekologi_faltlager(proj, "inventering.gpkg", gpkg_fil.read_bytes())
    assert post.antal_objekt == 2
    assert post.antal_polygoner == 1
    assert post.antal_punkter == 1
    assert post.per_nvi_klass == {"2": 1}

    # Läs tillbaka och verifiera SHA-256 + 0 PII + EPSG:3011
    last = las_ekologi_faltlager(proj)
    assert last is not None
    assert last.geojson_sha256 == post.geojson_sha256
    for feat in last.geojson["features"]:
        assert set(feat["properties"]) == set(SPARADE_FALT_KOLUMNER)
    disk_text = (proj / FALTLAGER_MAPP / FALTLAGER_GEOJSON).read_text(encoding="utf-8")
    for pii in ("Karin Ekolog", "karin@example.se", "Haga 1:23", "19790101-1234", "Ring ägaren", "inventerare"):
        assert pii not in disk_text

    # Manipulering av lagrad GeoJSON måste fångas
    (proj / FALTLAGER_MAPP / FALTLAGER_GEOJSON).write_text(disk_text + "\n", encoding="utf-8")
    with pytest.raises(GeografiFel, match="SHA-256"):
        las_ekologi_faltlager(proj)

    # Negativa kontroller: > 10 MB, fel filändelse, path traversal, tom fil, saknad CRS
    with pytest.raises(GeografiFel, match="10 MB"):
        importera_ekologi_faltlager(proj, "stor.geojson", b"x" * (MAX_FALTLAGER_BYTES + 1))
    with pytest.raises(GeografiFel, match="stöds inte"):
        importera_ekologi_faltlager(proj, "bad.shp", b"abc")
    with pytest.raises(GeografiFel, match="Ogiltigt filnamn"):
        importera_ekologi_faltlager(proj, "../escape.geojson", b"{}")
    with pytest.raises(GeografiFel, match="tom"):
        importera_ekologi_faltlager(proj, "tom.geojson", b"")

    utan_crs = tmp_path / "utan_crs.gpkg"
    gpd.GeoDataFrame({"biotop": ["Park"]}, geometry=[Point(100.0, 200.0)], crs=None).to_file(
        utan_crs, driver="GPKG"
    )
    with pytest.raises(GeografiFel, match="koordinatsystem"):
        importera_ekologi_faltlager(proj, "utan_crs.gpkg", utan_crs.read_bytes())
