"""Tester för K21-lösaren `vatten.2d` (`TvadLokalTroghet`) i `src/karna/inbyggda.py`."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio

from karna.analyslosare import LosarFel, hamta_losare, lista_losare
from karna.inbyggda import TvadLokalTroghet
from karna.korplan import kor_utredning, las_jobbutdata
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import BrytlinjePost, las_geotiff, skriv_brytlinjer_gpkg, skriv_geotiff


def _skapa_sanka_och_lutning(mapp: Path, *, med_kantsten: bool = False) -> Path:
    """16×16 m (1 m celler) deterministisk lutning ned i en 6×6 m sänka i mitten."""
    mapp.mkdir(parents=True, exist_ok=True)
    h, b = 16, 16
    yy, xx = np.mgrid[0:h, 0:b]
    # Svag lutning mot mitten + tydlig skål (botten 10.0 m, krön >= 10.6 m)
    z = (10.60 + 0.02 * np.hypot(yy - 7.5, xx - 7.5)).astype(np.float32)
    z[5:11, 5:11] = np.float32(10.00)
    markslag = np.full((h, b), 1, dtype=np.uint8)
    osak = np.full((h, b), 0.02, dtype=np.float32)
    x_min, y_max = 150000.0, 6580016.0
    skriv_geotiff(mapp / "mark.tif", z, x_min=x_min, y_max=y_max, pixelstorlek=1.0)
    skriv_geotiff(mapp / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=1.0)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=x_min, y_max=y_max, pixelstorlek=1.0)
    if med_kantsten:
        bryt = [
            BrytlinjePost(
                typ="kantsten",
                hojd_m=0.12,
                koordinater=((x_min + 2.0, y_max - 3.0), (x_min + 14.0, y_max - 3.0)),
                z_kron_rh2000=(10.82, 10.82),
            )
        ]
        skriv_brytlinjer_gpkg(
            mapp / "brytlinjer.gpkg",
            bryt,
            bbox_fallback=(x_min, y_max - h, x_min + b, y_max),
        )
    return mapp


def test_vatten_2d_registrerad_i_lista_losare() -> None:
    """`vatten.2d` finns i K21-registret med rätt domän, version och utdataroller."""
    namn = [los.namn for los in lista_losare()]
    assert "vatten.2d" in namn
    los = hamta_losare("vatten.2d")
    assert isinstance(los, TvadLokalTroghet)
    assert los.doman == "vatten"
    assert los.version == "1"
    assert los.utdataroller == ("max_djup", "hastighet", "ankomsttid")


def test_vatten_2d_korning_och_kontrakt_med_kant_massbalansfacit(tmp_path: Path) -> None:
    """Kör `vatten.2d` på en sluten sänka + lutning och verifierar ZSTD-COG, djup och massbalans."""
    ruta_mapp = _skapa_sanka_och_lutning(tmp_path / "k1")
    regn_mm = 30.0
    varaktighet_min = 5.0
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "vatten-2d-k21-test",
            "scenarier": {"bas": {"bas": str(ruta_mapp)}},
            "analyser": [
                {
                    "id": "tvad",
                    "losare": "vatten.2d",
                    "parametrar": {
                        "regn_mm": regn_mm,
                        "varaktighet_min": varaktighet_min,
                        "oppna_rander": False,
                        "takvatten": False,
                    },
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    res = kor_utredning(fraga, cache, tmp_path / "ut")

    ut_mapp = tmp_path / "ut" / "tvad" / "bas"
    fil_djup = ut_mapp / "max_djup.tif"
    fil_hast = ut_mapp / "hastighet.tif"
    fil_ank = ut_mapp / "ankomsttid.tif"
    assert fil_djup.is_file()
    assert fil_hast.is_file()
    assert fil_ank.is_file()

    # Verifiera ZSTD/DEFLATE COG och EPSG:3011 (SWEREF 99 18 00)
    for fil in (fil_djup, fil_hast, fil_ank):
        with rasterio.open(fil) as ds:
            assert ds.crs.to_epsg() == 3011
            assert ds.compression is not None
    with rasterio.open(fil_ank) as ds_ank:
        assert ds_ank.nodata is not None and np.isnan(ds_ank.nodata)

    djup, geo = las_geotiff(fil_djup)
    hast, _ = las_geotiff(fil_hast)
    ank, _ = las_geotiff(fil_ank)
    assert geo.epsg == 3011
    assert djup.shape == (16, 16)
    assert hast.shape == (16, 16)
    assert ank.shape == (16, 16)

    # Analytiskt massbalansfacit (slutna ränder): in_m3 = 16*16 m² * 0.030 m = 7.68 m³
    # Allt regn hamnar slutligen i sänkan (36 m²): maxdjup <= 7.68/36 ≈ 0.213 m, och > 0.10 m!
    jobb = res.sammanstallning["jobb"][0]
    mv = jobb["matvarden"]
    assert mv["in_m3"] == pytest.approx(16 * 16 * 0.030, rel=1e-9)
    assert mv["ut_m3"] == pytest.approx(0.0, abs=1e-12)
    assert mv["lagrat_m3"] == pytest.approx(mv["in_m3"], rel=1e-9)
    assert mv["massbalans_fel"] < 1e-11
    assert isinstance(mv["anvande_rust"], bool)
    assert mv["max_djup_m"] > 0.12
    assert mv["max_djup_m"] <= 7.68 / 36.0 + 0.02
    assert mv["max_hastighet_m_s"] > 0.0
    assert np.isfinite(ank[5:11, 5:11]).all()
    assert np.isnan(ank[0, 0])

    # Kontrollera K21 utdata.json och run.json
    utdata = las_jobbutdata(cache, jobb["nyckel"])
    assert set(utdata["filer"]) == {"max_djup", "hastighet", "ankomsttid"}
    run = json.loads((ut_mapp / "run.json").read_text(encoding="utf-8"))
    ut_roller = {u["roll"] for u in run["utdata"]}
    assert {"max_djup", "hastighet", "ankomsttid"} <= ut_roller


def test_vatten_2d_med_idf_och_1d_rannsten(tmp_path: Path) -> None:
    """`vatten.2d` stöder IDF-regn och skriver valfri `rannsten_flode.geojson` vid 3D-kantsten."""
    ruta_mapp = _skapa_sanka_och_lutning(tmp_path / "k1_kant", med_kantsten=True)
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "vatten-2d-idf-rannsten",
            "scenarier": {"bas": {"bas": str(ruta_mapp)}},
            "analyser": [
                {
                    "id": "tvad_idf",
                    "losare": "vatten.2d",
                    "parametrar": {
                        "idf": "stockholmsregn-2024",
                        "aterkomsttid_ar": 100.0,
                        "varaktighet_min": 10.0,
                        "slut_tid_s": 60.0,
                        "rannstenar": True,
                        "takvatten": False,
                    },
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    res = kor_utredning(fraga, cache, tmp_path / "ut")

    ut_mapp = tmp_path / "ut" / "tvad_idf" / "bas"
    assert (ut_mapp / "max_djup.tif").is_file()
    assert (ut_mapp / "hastighet.tif").is_file()
    assert (ut_mapp / "ankomsttid.tif").is_file()
    assert (ut_mapp / "rannsten_flode.geojson").is_file()
    mv = res.sammanstallning["jobb"][0]["matvarden"]
    assert mv["rannsten_1d_aktiv"] is True
    assert mv["rannsten_1d_segment"] > 0
    assert mv["massbalans_fel"] < 1e-11


def test_vatten_2d_validering_kastar_tydligt_fel() -> None:
    """Ogiltiga eller motstridiga parametrar ger `LosarFel`."""
    los = hamta_losare("vatten.2d")
    with pytest.raises(LosarFel):
        los.validera_parametrar({"regn_mm": 20.0, "idf": "stockholmsregn-2024"})
    with pytest.raises(LosarFel):
        los.validera_parametrar({"regn_mm": 20.0, "okand_param": 1})
    with pytest.raises(LosarFel):
        los.validera_parametrar({"regn_mm": 20.0, "varaktighet_min": 0.0})
