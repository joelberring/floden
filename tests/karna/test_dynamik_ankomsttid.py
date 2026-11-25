"""Tester för P1-1: `vatten.dynamik` exporterar ankomsttider (`tid_till_0_1m/0_2m.tif`, COG)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from karna.analyslosare import hamta_losare
from karna.inbyggda import Dynamik
from karna.korplan import kor_utredning, las_jobbutdata
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from sankgraf.fyllning import kor_statisk_k1_mapp
from testsvit.k1_io import las_geotiff, skriv_geotiff


def _skapa_sank_ruta(mapp: Path) -> Path:
    """30 x 30 m (1 m celler) skål i mitten med botten 9.5 m, krön 10.5 m och plan omgivning."""
    mapp.mkdir(parents=True, exist_ok=True)
    h, b = 30, 30
    yy, xx = np.mgrid[0:h, 0:b]
    r = np.hypot(yy - 14.5, xx - 14.5)
    # Skål med radie 8 m: botten 9.5 m i mitten, stiger till 10.5 m vid r=8 och sjunker mot kanten
    z = np.where(r <= 8.0, 9.5 + (r / 8.0) * 1.0, np.maximum(9.8, 10.5 - 0.1 * (r - 8.0))).astype(
        np.float32
    )
    markslag = np.full((h, b), 1, dtype=np.uint8)
    osak = np.full((h, b), 0.05, dtype=np.float32)
    skriv_geotiff(mapp / "mark.tif", z, x_min=150000.0, y_max=6580030.0, pixelstorlek=1.0)
    skriv_geotiff(
        mapp / "markslag.tif", markslag, x_min=150000.0, y_max=6580030.0, pixelstorlek=1.0
    )
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=150000.0, y_max=6580030.0, pixelstorlek=1.0)
    return mapp


def test_dynamik_exporterar_ankomsttider_som_cog_med_nan(tmp_path: Path) -> None:
    """`vatten.dynamik` skriver `tid_till_0_1m.tif` och `tid_till_0_2m.tif` med NaN."""
    ruta_mapp = _skapa_sank_ruta(tmp_path / "k1")
    los = hamta_losare("vatten.dynamik")
    assert isinstance(los, Dynamik)
    assert los.utdataroller == ("max_djup", "tid_till_0_1m", "tid_till_0_2m")

    tider = [0.0, 600.0]
    mm_h = [120.0, 120.0]  # 20 mm över hela 900 m² -> räcker gott till > 0.25 m i skålens mitt
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "ankomsttid-test",
            "scenarier": {"bas": {"bas": str(ruta_mapp)}},
            "analyser": [
                {
                    "id": "dyn",
                    "losare": "vatten.dynamik",
                    "parametrar": {
                        "regn_tid_s": tider,
                        "regn_mm_h": mm_h,
                        "takvatten": False,
                    },
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    res = kor_utredning(fraga, cache, tmp_path / "ut")

    ut_mapp = tmp_path / "ut" / "dyn" / "bas"
    fil_djup = ut_mapp / "max_djup.tif"
    fil_t01 = ut_mapp / "tid_till_0_1m.tif"
    fil_t02 = ut_mapp / "tid_till_0_2m.tif"
    assert fil_djup.is_file() and fil_t01.is_file() and fil_t02.is_file()

    # Verifiera COG-komprimering och nodata=NaN
    for fil in (fil_t01, fil_t02):
        with rasterio.open(fil) as ds:
            assert ds.compression is not None
            assert ds.nodata is not None and np.isnan(ds.nodata)
            assert ds.crs.to_epsg() == 3011

    djup, _ = las_geotiff(fil_djup)
    t01, _ = las_geotiff(fil_t01)
    t02, _ = las_geotiff(fil_t02)

    # Jämför mot direkt körning av `kor_dynamik_handelsestyrd`
    graf, _ = kor_statisk_k1_mapp(ruta_mapp, regn_m=0.0, takvatten=False)
    direkt = kor_dynamik_handelsestyrd(graf, tider, mm_h)
    np.testing.assert_allclose(t01, direkt.tid_till_0_1m_s, equal_nan=True)
    np.testing.assert_allclose(t02, direkt.tid_till_0_2m_s, equal_nan=True)

    # Fysiska egenskaper: djupa celler i mitten når både 0,1 m och 0,2 m, höga celler har NaN
    nar_01 = djup >= 0.10
    nar_02 = djup >= 0.20
    assert bool(np.any(nar_01)) and bool(np.any(nar_02))
    assert bool(np.any(~nar_01))
    assert np.isfinite(t01[nar_01]).all()
    assert np.isnan(t01[~nar_01]).all()
    assert np.isfinite(t02[nar_02]).all()
    assert np.isnan(t02[~nar_02]).all()
    assert (t02[nar_02] >= t01[nar_02] - 1e-4).all()

    # Kontrollera run.json och utdata.json
    nyckel = res.sammanstallning["jobb"][0]["nyckel"]
    utdata = las_jobbutdata(cache, nyckel)
    assert set(utdata["filer"]) == {"max_djup", "tid_till_0_1m", "tid_till_0_2m"}
    run = json.loads((ut_mapp / "run.json").read_text(encoding="utf-8"))
    ut_roller = {u["roll"] for u in run["utdata"]}
    assert {"max_djup", "tid_till_0_1m", "tid_till_0_2m"} <= ut_roller


def test_bakatkompatibilitet_for_gamla_korningar_utan_ankomsttider(tmp_path: Path) -> None:
    """Gammal cachad körning med enbart `max_djup.tif` fungerar fortfarande att läsa/jämföra."""
    ruta_mapp = _skapa_sank_ruta(tmp_path / "k1")
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "gammal-korning",
            "scenarier": {"fore": {"bas": str(ruta_mapp)}, "efter": {"bas": str(ruta_mapp)}},
            "analyser": [
                {
                    "id": "dyn",
                    "losare": "vatten.dynamik",
                    "parametrar": {
                        "regn_tid_s": [0.0, 300.0],
                        "regn_mm_h": [60.0, 60.0],
                        "takvatten": False,
                    },
                }
            ],
            "jamforelser": [
                {
                    "analys": "dyn",
                    "utdata": "max_djup",
                    "fore": "fore",
                    "efter": "efter",
                    "troskel": 0.01,
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    res1 = kor_utredning(fraga, cache, tmp_path / "ut1")

    # Simulera ett gammalt cache-objekt (från v1) som saknade ankomsttidsfilerna helt
    nyckel = res1.sammanstallning["jobb"][0]["nyckel"]
    jmapp = cache.jobbmapp(nyckel)
    (jmapp / "utdata" / "tid_till_0_1m.tif").unlink()
    (jmapp / "utdata" / "tid_till_0_2m.tif").unlink()
    ut_json = json.loads((jmapp / "utdata.json").read_text(encoding="utf-8"))
    ut_json["filer"] = {"max_djup": ut_json["filer"]["max_djup"]}
    (jmapp / "utdata.json").write_text(json.dumps(ut_json), encoding="utf-8")

    # Körningen från cachen + jämförelsen på `max_djup` måste fortfarande gå igenom utan fel!
    res2 = kor_utredning(fraga, cache, tmp_path / "ut2")
    assert res2.sammanstallning["jobb"][0]["fran_cache"] is True
    assert res2.sammanstallning["jamforelser"][0]["max_okning"] == pytest.approx(0.0)
    assert (tmp_path / "ut2" / "dyn" / "fore" / "max_djup.tif").is_file()
    assert not (tmp_path / "ut2" / "dyn" / "fore" / "tid_till_0_1m.tif").exists()
