"""Deterministiska fysik- och invarianttester för K21-skisslösaren `buller.nord2000`.

Prövar:
1. Registrering i K21 (`karna.inbyggda`, `hamta_losare`, `lista_losare`).
2. Avståndsdämpning längs fri väg utan hinder: L(40 m) < L(20 m) < L(10 m).
3. Skärmdämpning (före/efter via K9 `brytlinje_ny` mur): ljudnivån bakom skärmen faller flera dB(A).
4. Byggnadsdämpning (före/efter via K9 `byggnad_ny`), byggnadsmaskning (`NaN` i fotavtryck)
   samt K11-fasadpunkter (`fasad_buller.geojson`) strax utanför fasad.
5. Hårda invarianter (`nord2000_konform is False`, `metodstatus == "skiss"`,
   `rapport_sparrad is True`, `ValueError` vid saknad/ogiltig trafik) och bitidentisk
   reproducerbarhet vid upprepad körning.

Inga RL 18-18-facitvärden används eller lagras.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
import shapely

from buller.losare_k21 import (
    METODSTATUS,
    NORD2000_KONFORM,
    RAPPORT_SPARRAD,
    SKISSMARKNING_K21,
    BullerK21Fel,
    BullerNord2000,
)
from buller.nord2000 import Nord2000Karna
from gemensamt.hash import sha256_fil
from karna.analyslosare import LosarFel, LosarKontext, hamta_losare, lista_losare
from karna.geometri.rasterfil import ruta_fran_geo
from karna.korplan import kor_utredning, las_jobbutdata
from karna.resultatcache import ResultatCache
from karna.rutscenario import Ruta, RutScenario
from karna.scenario import Andring, Planscenario, skriv_scenario
from karna.utredning import fraga_fran_dict
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from markmodell.hinder_oppningar import brann_in_byggnader
from testsvit.k1_io import GeoTransformInfo, las_geotiff, skriv_geotiff

E0 = 150000.0
N0 = 6580000.0


def _skapa_k1_v2_ruta(
    mapp: Path,
    *,
    sida_m: float = 60.0,
    pixel_m: float = 5.0,
    markhojd_m: float = 10.0,
    markslag_kod: int = 4,
    byggnader: tuple[ByggnadsPost, ...] = (),
) -> tuple[Path, str]:
    """Skapar en komplett K1 v2-ruta (terrang, mark, markslag, byggnader.gpkg, recept)."""
    mapp.mkdir(parents=True, exist_ok=True)
    n = round(sida_m / pixel_m)
    x_min = E0
    y_max = N0 + sida_m
    terrang = np.full((n, n), markhojd_m, dtype=np.float32)
    markslag = np.full((n, n), markslag_kod, dtype=np.uint8)
    osak = np.full((n, n), 0.05, dtype=np.float32)

    geo = GeoTransformInfo(
        bredd=n,
        hojd=n,
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=pixel_m,
        pixelstorlek_y=pixel_m,
        epsg=3011,
        dtype="float32",
    )
    mm = ruta_fran_geo(geo)
    if byggnader:
        mark, markslag = brann_in_byggnader(
            terrang,
            markslag,
            mm,
            [b.till_byggnad() for b in byggnader],
        )
        mark = np.asarray(mark, dtype=np.float32)
    else:
        mark = terrang.copy()

    skriv_geotiff(mapp / "terrang.tif", terrang, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(mapp / "mark.tif", mark, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(mapp / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_byggnader_gpkg(
        mapp / "byggnader.gpkg",
        byggnader,
        bbox_fallback=(x_min, N0, x_min + sida_m, y_max),
    )
    recept = {
        "kontrakt": "K1/v2",
        "ruta_id": "mm_150000_6580000",
        "epsg": 3011,
        "hojdsystem": "RH 2000",
        "installningar": {"k1_version": 2, "upplosning_m": pixel_m},
    }
    recept_fil = mapp / "recept.json"
    recept_fil.write_text(json.dumps(recept, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return mapp, sha256_fil(recept_fil)


def _sampla_cell(fil: Path, e_m: float, n_m: float) -> float:
    """Läser värdet i den rastercell som täcker `(e_m, n_m)` i EPSG:3011."""
    arr, geo = las_geotiff(fil)
    kol = int(np.floor((e_m - geo.x_min) / geo.pixelstorlek_x))
    rad = int(np.floor((geo.y_max - n_m) / geo.pixelstorlek_y))
    assert 0 <= rad < geo.hojd and 0 <= kol < geo.bredd
    return float(arr[rad, kol])


def _standard_vag(sida_m: float = 60.0) -> list[dict[str, Any]]:
    return [
        {
            "id": "vag_vast",
            "koordinater": [[E0 + 5.0, N0 + 2.0], [E0 + 5.0, N0 + sida_m - 2.0]],
            "adt_fordon_dygn": 10000.0,
            "hastighet_km_h": 50.0,
            "andel_tung_pct": 10.0,
        }
    ]


def test_buller_nord2000_registrerad_i_k21() -> None:
    """`buller.nord2000` är registrerad via `karna.inbyggda` med korrekta roller och invarianter."""
    namn = [los.namn for los in lista_losare()]
    assert "buller.nord2000" in namn
    los = hamta_losare("buller.nord2000")
    assert isinstance(los, BullerNord2000)
    assert los.doman == "buller"
    assert los.version == "1"
    assert los.utdataroller == ("ekvivalent_ljudniva", "fasad_buller", "buller_sammanfattning")
    assert NORD2000_KONFORM is False
    assert Nord2000Karna.nord2000_konform is False
    assert METODSTATUS == "skiss"
    assert RAPPORT_SPARRAD is True


def test_avstandsdampning_fri_vag_utan_hinder(tmp_path: Path) -> None:
    """Fri väg utan hinder: ljudnivån faller strikt och fysikaliskt med avståndet."""
    ruta, _ = _skapa_k1_v2_ruta(tmp_path / "k1_fri", sida_m=60.0, pixel_m=5.0)
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "buller-avstand-test",
            "scenarier": {"fri": {"bas": str(ruta)}},
            "analyser": [
                {
                    "id": "buller",
                    "losare": "buller.nord2000",
                    "parametrar": {
                        "vagsegment": _standard_vag(60.0),
                        "mottagarhojd_m": 2.0,
                        "raster_cell_m": 5.0,
                        "segment_langd_m": 15.0,
                    },
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    res = kor_utredning(fraga, cache, tmp_path / "ut")

    ut_mapp = tmp_path / "ut" / "buller" / "fri"
    tif_fil = ut_mapp / "ekvivalent_ljudniva_dba.tif"
    geojson_fil = ut_mapp / "fasad_buller.geojson"
    sam_fil = ut_mapp / "buller_sammanfattning.json"
    assert tif_fil.is_file() and geojson_fil.is_file() and sam_fil.is_file()

    # Verifiera ZSTD-COG float32 i EPSG:3011 med NaN nodata
    with rasterio.open(tif_fil) as ds:
        assert ds.crs.to_epsg() == 3011
        assert ds.dtypes == ("float32",)
        assert ds.compression is not None
        assert ds.nodata is not None and np.isnan(ds.nodata)

    # Väg ligger på E = E0 + 5 m; mät längs mittlinjen N = N0 + 30 m på 10 m, 20 m och 40 m.
    n_mitt = N0 + 30.0
    l_10 = _sampla_cell(tif_fil, E0 + 15.0, n_mitt)
    l_20 = _sampla_cell(tif_fil, E0 + 25.0, n_mitt)
    l_40 = _sampla_cell(tif_fil, E0 + 45.0, n_mitt)
    assert all(np.isfinite([l_10, l_20, l_40]))
    assert l_40 < l_20 < l_10
    # För en linjekälla över mark faller ljudnivån flera dB vid varje avståndsfördubbling.
    assert (l_10 - l_20) > 2.0
    assert (l_20 - l_40) > 2.0

    # Tom FeatureCollection när inga byggnader finns
    fc = json.loads(geojson_fil.read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection"
    assert fc["features"] == []
    assert fc["properties"]["nord2000_konform"] is False

    # Sammanfattning och K21-utdata
    sam = json.loads(sam_fil.read_text(encoding="utf-8"))
    assert sam["antal_fasadpunkter"] == 0
    assert sam["laeq_max_dba"] >= sam["laeq_medel_dba"] > 0.0
    assert sam["nord2000_konform"] is False
    assert sam["metodstatus"] == "skiss"
    assert sam["rapport_sparrad"] is True

    jobb = res.sammanstallning["jobb"][0]
    utdata = las_jobbutdata(cache, jobb["nyckel"])
    assert set(utdata["filer"]) == {
        "ekvivalent_ljudniva",
        "fasad_buller",
        "buller_sammanfattning",
    }


def test_skarmdampning_bullerskarm_via_k9_fore_efter(tmp_path: Path) -> None:
    """En bullerskärm (K9 `brytlinje_ny` mur) mellan väg och mottagare sänker ljudnivån flera dB."""
    ruta, bas_recept = _skapa_k1_v2_ruta(tmp_path / "k1_skarm", sida_m=60.0, pixel_m=5.0)
    k9_skarm = Planscenario(
        scenario_id="med-bullerskarm",
        bas_recept=(bas_recept,),
        andringar=(
            Andring(
                typ="brytlinje_ny",
                andring_id="skarm_1",
                geometri={
                    "type": "LineString",
                    "coordinates": [[E0 + 14.0, N0 + 6.0], [E0 + 14.0, N0 + 54.0]],
                },
                egenskaper={"typ": "mur", "hojd_m": 4.0},
            ),
        ),
    )
    k9_fil = skriv_scenario(k9_skarm, tmp_path / "k9_skarm.json")

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "buller-skarm-fore-efter",
            "scenarier": {
                "fore": {"bas": str(ruta)},
                "efter": {"bas": str(ruta), "k9": [str(k9_fil)]},
            },
            "analyser": [
                {
                    "id": "buller",
                    "losare": "buller.nord2000",
                    "parametrar": {
                        "vagsegment": [
                            {
                                "id": "vag_1",
                                "koordinater": [[E0 + 4.0, N0 + 4.0], [E0 + 4.0, N0 + 56.0]],
                                "adt_fordon_dygn": 10000.0,
                                "hastighet_km_h": 50.0,
                                "andel_tung_pct": 0.0,
                            }
                        ],
                        "mottagarhojd_m": 2.0,
                        "raster_cell_m": 5.0,
                        "segment_langd_m": 15.0,
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
    cache = ResultatCache(tmp_path / "cache")
    res = kor_utredning(fraga, cache, tmp_path / "ut")

    tif_fore = tmp_path / "ut" / "buller" / "fore" / "ekvivalent_ljudniva_dba.tif"
    tif_efter = tmp_path / "ut" / "buller" / "efter" / "ekvivalent_ljudniva_dba.tif"
    sam_efter = json.loads(
        (tmp_path / "ut" / "buller" / "efter" / "buller_sammanfattning.json").read_text(
            encoding="utf-8"
        )
    )
    assert sam_efter["antal_bullerskarmar"] == 1
    assert sam_efter["antal_fasadpunkter"] == 0

    # Bakom 4 m skärmen vid E = E0 + 14 m ska ljudnivån vid E = E0 + 27,5 m (N = N0 + 32,5 m)
    # sjunka tydligt (minst 5 dB(A) skärmdämpning enligt Nord2000).
    l_fore = _sampla_cell(tif_fore, E0 + 27.5, N0 + 32.5)
    l_efter = _sampla_cell(tif_efter, E0 + 27.5, N0 + 32.5)
    assert np.isfinite(l_fore) and np.isfinite(l_efter)
    assert l_efter < l_fore - 5.0

    # Jämförelselagret fångar minskningen i `diff.tif`.
    jamf = res.sammanstallning["jamforelser"][0]
    assert jamf["max_minskning"] > 5.0
    assert jamf["yta_minskning_m2"] > 0.0


def test_byggnadsdampning_maskning_och_fasadpunkter(tmp_path: Path) -> None:
    """Ny byggnad (K9 `byggnad_ny`) dämpar bakom huset, maskas till NaN och får K11-fasadpunkter."""
    ruta, bas_recept = _skapa_k1_v2_ruta(tmp_path / "k1_hus", sida_m=60.0, pixel_m=5.0)
    hus_ring = [
        [E0 + 20.0, N0 + 20.0],
        [E0 + 30.0, N0 + 20.0],
        [E0 + 30.0, N0 + 40.0],
        [E0 + 20.0, N0 + 40.0],
        [E0 + 20.0, N0 + 20.0],
    ]
    k9_hus = Planscenario(
        scenario_id="nytt-hus",
        bas_recept=(bas_recept,),
        andringar=(
            Andring(
                typ="byggnad_ny",
                andring_id="b_nytt_hus",
                geometri={"type": "Polygon", "coordinates": [hus_ring]},
                egenskaper={"hojd_m": 12.0},
            ),
        ),
    )
    k9_fil = skriv_scenario(k9_hus, tmp_path / "k9_hus.json")

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "buller-byggnad-mask-fasad",
            "scenarier": {
                "fore": {"bas": str(ruta)},
                "efter": {"bas": str(ruta), "k9": [str(k9_fil)]},
            },
            "analyser": [
                {
                    "id": "buller",
                    "losare": "buller.nord2000",
                    "parametrar": {
                        "vagsegment": [
                            {
                                "id": "vag_1",
                                "koordinater": [[E0 + 4.0, N0 + 4.0], [E0 + 4.0, N0 + 56.0]],
                                "adt_fordon_dygn": 12000.0,
                                "hastighet_km_h": 50.0,
                                "andel_tung_pct": 0.0,
                            }
                        ],
                        "mottagarhojd_m": 2.0,
                        "raster_cell_m": 5.0,
                        "segment_langd_m": 15.0,
                        "fasad_steg_m": 10.0,
                        "fasad_avstand_m": 0.5,
                        "max_vaningar": 2,
                        "reflexordning": 1,
                    },
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    kor_utredning(fraga, cache, tmp_path / "ut")

    tif_fore = tmp_path / "ut" / "buller" / "fore" / "ekvivalent_ljudniva_dba.tif"
    tif_efter = tmp_path / "ut" / "buller" / "efter" / "ekvivalent_ljudniva_dba.tif"
    fasad_fil = tmp_path / "ut" / "buller" / "efter" / "fasad_buller.geojson"
    sam_fil = tmp_path / "ut" / "buller" / "efter" / "buller_sammanfattning.json"

    # 1. Skärmning bakom byggnaden (E = E0 + 42,5 m, N = N0 + 32,5 m): tydlig dämpning (> 8 dB(A)).
    l_bakom_fore = _sampla_cell(tif_fore, E0 + 42.5, N0 + 32.5)
    l_bakom_efter = _sampla_cell(tif_efter, E0 + 42.5, N0 + 32.5)
    assert np.isfinite(l_bakom_fore) and np.isfinite(l_bakom_efter)
    assert l_bakom_efter < l_bakom_fore - 8.0

    # 2. Byggnadsmaskning: alla celler inuti byggnadens fotavtryck är NaN; celler utanför ändliga.
    arr_efter, geo_efter = las_geotiff(tif_efter)
    hus_poly = shapely.Polygon(hus_ring)
    antal_i_hus = 0
    for r in range(geo_efter.hojd):
        for c in range(geo_efter.bredd):
            ce = geo_efter.x_min + (c + 0.5) * geo_efter.pixelstorlek_x
            cn = geo_efter.y_max - (r + 0.5) * geo_efter.pixelstorlek_y
            if hus_poly.covers(shapely.Point(ce, cn)):
                antal_i_hus += 1
                assert np.isnan(arr_efter[r, c]), f"Byggnadscell ({r}, {c}) ska vara NaN."
            else:
                assert np.isfinite(arr_efter[r, c]), f"Fri cell ({r}, {c}) ska vara ändlig."
    assert antal_i_hus == 8

    # 3. Fasadpunkter: ligger 0,5 m utanför fasad med ändliga dB(A); västfasad > östfasad.
    fc = json.loads(fasad_fil.read_text(encoding="utf-8"))
    sam = json.loads(sam_fil.read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == sam["antal_fasadpunkter"] > 0

    vast_nivaer: list[float] = []
    ost_nivaer: list[float] = []
    for feat in fc["features"]:
        assert feat["geometry"]["type"] == "Point"
        e_pt, n_pt = feat["geometry"]["coordinates"]
        pt = shapely.Point(e_pt, n_pt)
        assert not hus_poly.covers(pt)
        assert hus_poly.distance(pt) == pytest.approx(0.5, abs=0.06)
        props = feat["properties"]
        assert props["byggnad_id"] == "b_nytt_hus"
        assert props["lafmax_dba"] is None
        assert isinstance(props["laeq_24h_dba"], float) and np.isfinite(props["laeq_24h_dba"])
        if e_pt < E0 + 20.0:
            vast_nivaer.append(props["laeq_24h_dba"])
        elif e_pt > E0 + 30.0:
            ost_nivaer.append(props["laeq_24h_dba"])

    assert vast_nivaer and ost_nivaer
    assert min(vast_nivaer) > max(ost_nivaer) + 10.0
    assert sam["antal_maskade_byggnadsceller"] == antal_i_hus


def test_invarianter_validering_och_bitidentisk_reproducerbarhet(tmp_path: Path) -> None:
    """Kontrollerar invarianter, `ValueError` vid ogiltig/saknad trafik och bitidentisk körning."""
    los = hamta_losare("buller.nord2000")

    # 1. Saknad eller ogiltig trafik kastar tydligt ValueError (och LosarFel) – aldrig tyst trafik!
    ogiltiga_parametrar: list[dict[str, Any]] = [
        {},
        {"vagsegment": []},
        {
            "vagsegment": [
                {
                    "koordinater": [[E0 + 5.0, N0 + 5.0], [E0 + 5.0, N0 + 35.0]],
                    "adt_fordon_dygn": -1.0,
                    "hastighet_km_h": 50.0,
                    "andel_tung_pct": 10.0,
                }
            ]
        },
        {
            "vagsegment": [
                {
                    "koordinater": [[E0 + 5.0, N0 + 5.0], [E0 + 5.0, N0 + 35.0]],
                    "adt_fordon_dygn": 5000.0,
                    "hastighet_km_h": 0.0,
                    "andel_tung_pct": 10.0,
                }
            ]
        },
        {
            "vagsegment": [
                {
                    "koordinater": [[E0 + 5.0, N0 + 5.0], [E0 + 5.0, N0 + 35.0]],
                    "adt_fordon_dygn": 5000.0,
                    "hastighet_km_h": -30.0,
                    "andel_tung_pct": 10.0,
                }
            ]
        },
        {
            "vagsegment": [
                {
                    "koordinater": [[E0 + 5.0, N0 + 5.0], [E0 + 5.0, N0 + 35.0]],
                    "adt_fordon_dygn": 5000.0,
                    "hastighet_km_h": 50.0,
                    "andel_tung_pct": 105.0,
                }
            ]
        },
        {
            "vagsegment": [
                {
                    "koordinater": [[E0 + 5.0, N0 + 5.0], [E0 + 5.0, N0 + 35.0]],
                    "adt_fordon_dygn": 5000.0,
                    "hastighet_km_h": 50.0,
                    "andel_tung_pct": -0.1,
                }
            ]
        },
        {
            "vagsegment": [
                {
                    "koordinater": [[E0 + 5.0, N0 + 5.0]],
                    "adt_fordon_dygn": 5000.0,
                    "hastighet_km_h": 50.0,
                    "andel_tung_pct": 10.0,
                }
            ]
        },
        # Försök att slå på rapportläge eller okänd parameter avvisas omedelbart.
        {
            "vagsegment": _standard_vag(40.0),
            "lage": "rapport",
        },
    ]
    for dalig in ogiltiga_parametrar:
        with pytest.raises((BullerK21Fel, ValueError, LosarFel)) as exc_info:
            los.validera_parametrar(dalig)
        assert isinstance(exc_info.value, ValueError)
        assert isinstance(exc_info.value, LosarFel)

    # Direkt anrop till `kor` utan trafik kastar också ValueError.
    hus = ByggnadsPost(
        byggnad_id="b_0000000000000001",
        polygon=(
            (E0 + 20.0, N0 + 12.0),
            (E0 + 32.0, N0 + 12.0),
            (E0 + 32.0, N0 + 28.0),
            (E0 + 20.0, N0 + 28.0),
        ),
        hal=(),
        takhojd_rh2000=19.0,
        markhojd_rh2000=10.0,
        hojd_m=9.0,
        kalla="test",
    )
    ruta_mapp, _ = _skapa_k1_v2_ruta(
        tmp_path / "k1_repro", sida_m=40.0, pixel_m=8.0, byggnader=(hus,)
    )
    ruta_obj = Ruta(
        ruta_id="mm_150000_6580000",
        mapp=ruta_mapp,
        hash="0" * 64,
        x_min=E0,
        y_min=N0,
        x_max=E0 + 40.0,
        y_max=N0 + 40.0,
    )
    scen_obj = RutScenario(id="test", nyckel="1" * 64, rutor=(ruta_obj,))
    with pytest.raises(ValueError):
        los.kor(
            LosarKontext(
                utmapp=tmp_path / "tom_ut",
                scenario=scen_obj,
                parametrar={},
                indata={},
                beroenden={},
            )
        )

    # 2. Nolltrafik (adt = 0) är giltigt och ger tydlig varning utan påhittade dB-tal.
    noll_fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "buller-nolltrafik",
            "scenarier": {"bas": {"bas": str(ruta_mapp)}},
            "analyser": [
                {
                    "id": "buller",
                    "losare": "buller.nord2000",
                    "parametrar": {
                        "vagsegment": [
                            {
                                "id": "stangd_vag",
                                "koordinater": [[E0 + 4.0, N0 + 4.0], [E0 + 4.0, N0 + 36.0]],
                                "adt_fordon_dygn": 0.0,
                                "hastighet_km_h": 50.0,
                                "andel_tung_pct": 0.0,
                            }
                        ],
                        "raster_cell_m": 8.0,
                    },
                }
            ],
        },
        tmp_path,
    )
    kor_utredning(noll_fraga, ResultatCache(tmp_path / "cache_noll"), tmp_path / "ut_noll")
    sam_noll = json.loads(
        (tmp_path / "ut_noll" / "buller" / "bas" / "buller_sammanfattning.json").read_text(
            encoding="utf-8"
        )
    )
    assert sam_noll["laeq_max_dba"] is None
    assert sam_noll["nord2000_konform"] is False
    assert sam_noll["metodstatus"] == "skiss"
    assert sam_noll["rapport_sparrad"] is True

    # 3. Bitidentisk reproducerbarhet över två helt separata körningar utan delad cache.
    fraga_dict: dict[str, Any] = {
        "kontrakt": "K20/v1",
        "fraga_id": "buller-repro",
        "scenarier": {"bas": {"bas": str(ruta_mapp)}},
        "analyser": [
            {
                "id": "buller",
                "losare": "buller.nord2000",
                "parametrar": {
                    "vagsegment": _standard_vag(40.0),
                    "mottagarhojd_m": 2.0,
                    "raster_cell_m": 8.0,
                    "segment_langd_m": 15.0,
                    "fasad_steg_m": 10.0,
                    "max_vaningar": 1,
                },
            }
        ],
    }
    res1 = kor_utredning(
        fraga_fran_dict(fraga_dict, tmp_path),
        ResultatCache(tmp_path / "cache_1"),
        tmp_path / "ut_1",
    )
    res2 = kor_utredning(
        fraga_fran_dict(fraga_dict, tmp_path),
        ResultatCache(tmp_path / "cache_2"),
        tmp_path / "ut_2",
    )
    mapp1 = tmp_path / "ut_1" / "buller" / "bas"
    mapp2 = tmp_path / "ut_2" / "buller" / "bas"
    for filnamn in (
        "ekvivalent_ljudniva_dba.tif",
        "fasad_buller.geojson",
        "buller_sammanfattning.json",
    ):
        assert (mapp1 / filnamn).read_bytes() == (mapp2 / filnamn).read_bytes(), (
            f"{filnamn} skilde sig mellan två identiska körningar."
        )

    sam1 = json.loads((mapp1 / "buller_sammanfattning.json").read_text(encoding="utf-8"))
    mv1 = res1.sammanstallning["jobb"][0]["matvarden"]
    mv2 = res2.sammanstallning["jobb"][0]["matvarden"]
    assert mv1 == mv2
    assert sam1["nord2000_konform"] is False
    assert sam1["metodstatus"] == "skiss"
    assert sam1["rapport_sparrad"] is True
    assert sam1["markning"] == SKISSMARKNING_K21
    assert "skiss" in sam1["skissvarning"].lower()
    assert sam1["skissvarning"] in sam1["varningar"]
    assert mv1["nord2000_konform"] is False
    assert mv1["metodstatus"] == "skiss"
    assert mv1["rapport_sparrad"] is True
    assert 0.0 <= sam1["andel_over_60_dba_pct"] <= sam1["andel_over_55_dba_pct"] <= 100.0


def test_overlappande_byggnader_klipps_med_hojdforetrade_och_bullerscen_forblir_strikt(
    tmp_path: Path,
) -> None:
    """Överlappande K1-takplan (t.ex. Göteborgs tät kvartersstad) klipps deterministiskt i
    `buller.nord2000` med höjd-företräde och tydlig varning, medan `Bullerscen` förblir strikt."""
    from buller.scen.bullerscen import Bullerscen
    from buller.typer import BullerFel, Byggnad

    # Två överlappande takvolymer (högre torn inuti/över lägre bas) + ett litet väggöverlapp.
    bas_hus = ByggnadsPost(
        byggnad_id="hus_bas",
        polygon=(
            (E0 + 18.0, N0 + 20.0),
            (E0 + 42.0, N0 + 20.0),
            (E0 + 42.0, N0 + 36.0),
            (E0 + 18.0, N0 + 36.0),
        ),
        hojd_m=12.0,
        markhojd_rh2000=10.0,
        takhojd_rh2000=22.0,
        kalla="test",
    )
    torn_hus = ByggnadsPost(
        byggnad_id="hus_torn",
        polygon=(
            (E0 + 24.0, N0 + 24.0),
            (E0 + 36.0, N0 + 24.0),
            (E0 + 36.0, N0 + 32.0),
            (E0 + 24.0, N0 + 32.0),
        ),
        hojd_m=24.0,
        markhojd_rh2000=10.0,
        takhojd_rh2000=34.0,
        kalla="test",
    )
    granne_sliver = ByggnadsPost(
        byggnad_id="hus_granne",
        polygon=(
            (E0 + 41.95, N0 + 20.0),
            (E0 + 50.0, N0 + 20.0),
            (E0 + 50.0, N0 + 36.0),
            (E0 + 41.95, N0 + 36.0),
        ),
        hojd_m=10.0,
        markhojd_rh2000=10.0,
        takhojd_rh2000=20.0,
        kalla="test",
    )

    # 1. Bullerscen ska fortfarande vara strikt och avvisa råa överlappande byggnader!
    with pytest.raises(BullerFel, match="överlappar"):
        Bullerscen(
            terrang_rh2000_m=np.full((12, 12), 10.0, dtype=np.float64),
            markslag=np.full((12, 12), 4, dtype=np.uint8),
            e_min_m=E0,
            n_max_m=N0 + 60.0,
            cell_m=5.0,
            byggnader=(
                Byggnad(
                    id="hus_bas",
                    e_m=np.array([p[0] for p in bas_hus.polygon], dtype=np.float64),
                    n_m=np.array([p[1] for p in bas_hus.polygon], dtype=np.float64),
                    tak_rh2000_m=22.0,
                ),
                Byggnad(
                    id="hus_torn",
                    e_m=np.array([p[0] for p in torn_hus.polygon], dtype=np.float64),
                    n_m=np.array([p[1] for p in torn_hus.polygon], dtype=np.float64),
                    tak_rh2000_m=34.0,
                ),
            ),
            vagar=(),
        )

    # 2. K21-lösaren buller.nord2000 ska klippa överlappen, varna öppet och räkna klart!
    ruta_mapp, _ = _skapa_k1_v2_ruta(
        tmp_path / "ruta_overlapp",
        sida_m=60.0,
        pixel_m=5.0,
        byggnader=(bas_hus, torn_hus, granne_sliver),
    )
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "buller-overlapp-kvarter",
            "scenarier": {"bas": {"bas": str(ruta_mapp)}},
            "analyser": [
                {
                    "id": "buller",
                    "losare": "buller.nord2000",
                    "parametrar": {
                        "vagsegment": _standard_vag(10.0),
                        "mottagarhojd_m": 2.0,
                        "raster_cell_m": 5.0,
                        "segment_langd_m": 15.0,
                        "fasad_steg_m": 10.0,
                        "max_vaningar": 1,
                    },
                }
            ],
        },
        tmp_path,
    )
    kor_utredning(fraga, ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    sam = json.loads(
        (tmp_path / "ut" / "buller" / "bas" / "buller_sammanfattning.json").read_text(
            encoding="utf-8"
        )
    )
    assert sam["laeq_max_dba"] is not None
    assert sam["nord2000_konform"] is False
    assert sam["metodstatus"] == "skiss"
    assert sam["rapport_sparrad"] is True
    assert any("Överlappande byggnadspolygoner" in v for v in sam["varningar"])
