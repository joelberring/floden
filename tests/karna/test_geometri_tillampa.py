"""Deterministiska regressionstester för K-B4 (`NaN` i K1 v2 i `karna.geometri.tillampa`)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from karna.geometri.rasterfil import skriv_raster
from karna.geometri.tillampa import tillampa_pa_v2_ruta
from lapp.kontrakt import HojdYta, ProjektLapp
from markmodell.byggnadsregister import skriv_byggnader_gpkg
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo


def _skapa_v2_ruta_med_nan(mapp: Path) -> tuple[Path, GeoTransformInfo, int]:
    """Bygger en 20×20 K1 v2-ruta med 40 `NaN`-celler (t.ex. kust/saknad laser)."""
    mapp.mkdir(parents=True, exist_ok=True)
    h, b = 20, 20
    dx = 0.5
    x_min, y_max = 150000.0, 6580010.0
    geo = GeoTransformInfo(
        bredd=b,
        hojd=h,
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    mark = np.full((h, b), 10.0, dtype=np.float32)
    mark[:2, :] = np.nan  # 40 NaN-celler längs norra kanten
    terrang = mark.copy()
    osak = np.full((h, b), 0.03, dtype=np.float32)
    osak[:2, :] = np.nan
    slag = np.full((h, b), 1, dtype=np.uint8)

    skriv_raster(mapp / "mark.tif", mark, geo)
    skriv_raster(mapp / "terrang.tif", terrang, geo)
    skriv_raster(mapp / "osakerhet.tif", osak, geo)
    skriv_raster(mapp / "markslag.tif", slag, geo)
    skriv_byggnader_gpkg(
        mapp / "byggnader.gpkg",
        (),
        (geo.x_min, geo.y_min, geo.x_max, geo.y_max),
    )
    (mapp / "recept.json").write_text(
        json.dumps({"kontrakt": "K1/v2", "ruta_id": "mm_nan", "installningar": {"k1_version": 2}}),
        encoding="utf-8",
    )
    return mapp, geo, 40


def test_kb4_tom_andring_pa_v2_ruta_med_nan_ger_andrad_false(tmp_path: Path) -> None:
    """Tom K9-ändring och ändring utanför rutan på NaN-ruta ger `False` (cache bevaras)."""
    bas, _geo, _n_nan = _skapa_v2_ruta_med_nan(tmp_path / "bas")

    # 1. Helt tom ändring
    ut1 = tmp_path / "ut_tom"
    andrad1 = tillampa_pa_v2_ruta(
        bas,
        ut1,
        lapp=None,
        sektioner=None,
        geometri=(),
        bas_recept_sha256="a" * 64,
    )
    assert andrad1 is False
    assert not ut1.exists()

    # 2. Lapp med höjdyta utanför rutans bbox
    lapp_utanfor = ProjektLapp(
        lapp_id="utanfor",
        bas_recept=("a" * 64,),
        hojdytor=(
            HojdYta(
                yta_id="h-borta",
                polygon=(
                    (150500.0, 6580500.0),
                    (150510.0, 6580500.0),
                    (150510.0, 6580510.0),
                    (150500.0, 6580510.0),
                ),
                hojd_rh2000=12.0,
            ),
        ),
    )
    ut2 = tmp_path / "ut_utanfor"
    andrad2 = tillampa_pa_v2_ruta(
        bas,
        ut2,
        lapp=lapp_utanfor,
        sektioner=None,
        geometri=(),
        bas_recept_sha256="a" * 64,
    )
    assert andrad2 is False
    assert not ut2.exists()


def test_kb4_verklig_andring_pa_v2_ruta_med_nan_raknar_ej_nan_som_andrade(tmp_path: Path) -> None:
    """En 2×2 m höjdändring (16 celler) på en ruta med 40 NaN räknar exakt 16 ändrade celler."""
    bas, _geo, _n_nan = _skapa_v2_ruta_med_nan(tmp_path / "bas")
    lapp_inuti = ProjektLapp(
        lapp_id="inuti",
        bas_recept=("a" * 64,),
        hojdytor=(
            HojdYta(
                yta_id="h-inuti",
                polygon=(
                    (150002.0, 6580002.0),
                    (150004.0, 6580002.0),
                    (150004.0, 6580004.0),
                    (150002.0, 6580004.0),
                ),
                hojd_rh2000=11.5,
            ),
        ),
    )
    ut = tmp_path / "ut_inuti"
    andrad = tillampa_pa_v2_ruta(
        bas,
        ut,
        lapp=lapp_inuti,
        sektioner=None,
        geometri=(),
        bas_recept_sha256="a" * 64,
    )
    assert andrad is True
    recept = json.loads((ut / "recept.json").read_text(encoding="utf-8"))
    assert recept["antal_andrade_celler"] == 16
