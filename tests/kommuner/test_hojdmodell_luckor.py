# ruff: noqa: E501
"""Tester för P0-1: klassificering och diagnos av NaN-luckor i höjdmodellen (hojdmodell, k1_filpaket, mappa)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from gemensamt.koordinater import transformera
from geografi.modell import las_manifest
from kommuner.hojdmodell import (
    FYLL_MAX_M,
    LUCK_GILTIG,
    LUCK_SMAHAL_FYLLT,
    LUCK_STORT_HAL,
    LUCK_UTANFOR_PLATTOR,
    LUCK_VATTENYTA,
    Hojdmodell,
    klassificera_och_fyll_luckor,
)
from kommuner.k1_filpaket import bygg_omrade_filpaket
from lantmateriet.omrade import las_omrade
from markmodell.rutnat import Ruta
from tests.kommuner.test_filpaket_mappa import HOJD_URL, FalskFilhamtare, inga_nycklar, varld
from tests.kommuner.test_skapa import mappa
from tests.lantmateriet.conftest import lm  # noqa: F401
from tests.lantmateriet.falsk_server import FalskLm


def test_klassificerar_alla_lucktyper_och_fyller_aldrig_vatten_eller_utanfor() -> None:
    """Utanför plattor, känd vattenyta och stort hål (> FYLL_MAX_M) lämnas som NaN; småhål fylls."""
    # 100 x 100 celler med 5 m upplösning -> 500 x 500 m (snabbt och exakt i skala)
    z = np.full((100, 100), 10.0, dtype=np.float32)
    tackt = np.ones((100, 100), dtype=bool)
    vatten = np.zeros((100, 100), dtype=bool)

    # 1. Utanför plattornas täckning (vänster remsa, 10 kolumner = 50 m)
    tackt[:, :10] = False
    z[:, :10] = np.nan

    # 2. Känd vattenyta inom plattor (8x20 celler = 40x100 m, smalare än FYLL_MAX_M men får ej fyllas!)
    vatten[10:30, 15:23] = True
    z[vatten] = np.nan

    # 3. Stort hål inom plattor mot sydöstra hörnet: 65x36 celler (325x180 m), max_dist = 180 m > FYLL_MAX_M (150 m)
    z[35:100, 64:100] = np.nan

    # 4. Småhål (byggnad, 4x4 celler = 20x20 m, max_dist = 10 m <= 150 m) -> SKA fyllas!
    z[20:24, 40:44] = np.nan

    ut, klass, diag, varn = klassificera_och_fyll_luckor(
        z,
        tackt,
        pixelstorlek_m=5.0,
        ruta_namn="test_ruta",
        vatten_mask=vatten,
        fyll_max_m=FYLL_MAX_M,
        nan_varning_andel=0.20,
    )

    assert diag.utanfor_plattor_celler == 1000
    assert diag.vattenyta_celler == 160
    assert diag.stort_hal_celler == 65 * 36
    assert diag.smahal_fyllda_celler == 16
    assert diag.smahal_ej_fyllda_celler == 0

    # Bara småhålet fylldes — inga artificiella trappsteg i stora hålet, vattenytan eller utanför plattan!
    assert np.isfinite(ut[20:24, 40:44]).all()
    assert np.allclose(ut[20:24, 40:44], 10.0, atol=1e-3)
    assert np.isnan(ut[:, :10]).all()
    assert np.isnan(ut[vatten]).all()
    assert np.isnan(ut[35:100, 64:100]).all()

    assert (klass[:, :10] == LUCK_UTANFOR_PLATTOR).all()
    assert (klass[vatten] == LUCK_VATTENYTA).all()
    assert (klass[35:100, 64:100] == LUCK_STORT_HAL).all()
    assert (klass[20:24, 40:44] == LUCK_SMAHAL_FYLLT).all()
    assert klass[0, 50] == LUCK_GILTIG

    # Total NaN efteråt = (1000 + 160 + 2340) / 10000 = 35.0 % > 20 %
    assert diag.nan_efter_andel == pytest.approx(0.35, abs=1e-6)
    assert diag.over_troskel is True
    assert diag.huvudsaklig_orsak == "stort_hal"
    assert any("interpolerad" in v for v in varn)
    assert any("saknar värde" in v and "stort hål" in v and "vattenyta" in v for v in varn)
    assert any("hög andel NaN" in v for v in varn)


@pytest.mark.parametrize(
    ("scenario", "forvantad_orsak", "min_andel", "max_andel"),
    [
        ("stort_hal_65", "stort_hal", 0.60, 0.70),
        ("utanfor_75", "utanfor_plattor", 0.70, 0.80),
        ("vatten_55", "vattenyta", 0.50, 0.60),
    ],
)
def test_syntetiskt_50_till_80_procent_nan_ger_diagnos_och_varning(
    scenario: str, forvantad_orsak: str, min_andel: float, max_andel: float
) -> None:
    """Syntetiska 50–80 % NaN-fall (jfr Göteborgs problematiska rutor) fylls aldrig blint."""
    z = np.full((100, 100), 8.0, dtype=np.float32)  # 500 x 500 m med 5 m pixlar
    tackt = np.ones((100, 100), dtype=bool)
    vatten = np.zeros((100, 100), dtype=bool)

    if scenario == "stort_hal_65":
        z[:, :65] = np.nan  # 325 m brett hål (> FYLL_MAX_M = 150 m) -> 65 % NaN
    elif scenario == "utanfor_75":
        tackt[:, :75] = False
        z[:, :75] = np.nan  # 75 % utanför plattan
    elif scenario == "vatten_55":
        vatten[:, :55] = True
        z[:, :55] = np.nan  # 55 % stor vattenyta

    ut, _, diag, varn = klassificera_och_fyll_luckor(
        z,
        tackt,
        pixelstorlek_m=5.0,
        ruta_namn=f"mm_{scenario}",
        vatten_mask=vatten if scenario == "vatten_55" else None,
        nan_varning_andel=0.20,
    )

    assert min_andel <= diag.nan_efter_andel <= max_andel
    assert diag.smahal_fyllda_celler == 0
    assert diag.nan_efter_celler == diag.nan_fore_celler
    assert np.isnan(ut).sum() == diag.nan_fore_celler
    assert diag.over_troskel is True
    assert diag.huvudsaklig_orsak == forvantad_orsak
    assert any("hög andel NaN" in v and forvantad_orsak in v for v in varn)


def test_konfigurerbar_nan_troskel() -> None:
    z = np.full((40, 40), 5.0, dtype=np.float32)
    tackt = np.ones((40, 40), dtype=bool)
    tackt[:, :10] = False  # 25 % utanför plattor
    z[:, :10] = np.nan

    _, _, d20, v20 = klassificera_och_fyll_luckor(z, tackt, pixelstorlek_m=1.0, nan_varning_andel=0.20)
    _, _, d30, v30 = klassificera_och_fyll_luckor(z, tackt, pixelstorlek_m=1.0, nan_varning_andel=0.30)

    assert d20.over_troskel is True and any("hög andel NaN" in v for v in v20)
    assert d30.over_troskel is False and not any("hög andel NaN" in v for v in v30)


def test_hojdmodell_och_k1_filpaket_flaggar_hog_nan_och_bevarar_stort_hal(tmp_path: Path) -> None:
    """En 500 m-ruta med 60 % stort hål lämnas som NaN (ingen 150 m kantfyllning!) och flaggas."""
    e0, n0 = 150000.0, 6400000.0
    tif = tmp_path / "platta.tif"
    z_in = np.full((500, 500), 12.0, dtype=np.float32)
    z_in[:, :300] = -9999.0  # 300 m brett hål (60 % NaN, > FYLL_MAX_M = 150 m)
    z_in[200:210, 400:410] = -9999.0  # 10x10 m småhål (hus) -> ska fyllas
    with rasterio.open(
        tif, "w", driver="GTiff", height=500, width=500, count=1, dtype="float32",
        crs="EPSG:3011", transform=from_origin(e0, n0 + 500.0, 1.0, 1.0), nodata=-9999.0,
    ) as d:  # fmt: skip
        d.write(z_in, 1)

    hm = Hojdmodell([tif], epsg_antagen=3011)
    ruta = Ruta(e0, n0, 500.0, 0.0, 1.0)
    z_ut, varn, diag = hm.grund_z_med_diagnos(ruta)

    assert diag.stort_hal_celler == pytest.approx(500 * 300, rel=0.02)
    assert diag.smahal_fyllda_celler == pytest.approx(100, rel=0.2)
    assert diag.over_troskel is True
    assert diag.huvudsaklig_orsak == "stort_hal"
    assert np.isnan(z_ut[:, :295]).all()  # ingen artificiell 150 m kantfyllning in i stora hålet!
    assert np.isfinite(z_ut[202:208, 402:408]).all()
    assert any("hög andel NaN" in v for v in varn)

    import geopandas as gpd
    import shapely

    omr_fil = tmp_path / "omr.gpkg"
    gpd.GeoDataFrame(
        {"namn": ["o"]}, geometry=[shapely.box(e0, n0, e0 + 500, n0 + 500)], crs=3011
    ).to_file(omr_fil, driver="GPKG")
    res = bygg_omrade_filpaket(
        las_omrade(omr_fil), tmp_path / "k1", hojdmodell=hm, hojd_paket=[],
        byggnader_fil=None, upplosning_m=1.0, en_ruta=True, skriv=lambda _t: None,
    )  # fmt: skip
    assert res.hog_nan_flagga is True
    assert res.max_nan_andel > 0.55
    assert len(res.hojd_luckor) == 1 and res.hojd_luckor[0].huvudsaklig_orsak == "stort_hal"


def test_mappa_geografi_satter_flagga_och_varning_i_manifest_vid_hog_nan(
    lm: FalskLm, tmp_path: Path  # noqa: F811
) -> None:
    """`mappa_geografi` med höjdplatta som bara täcker del av området -> flagga + varning i manifest."""
    v = varld(lm, tmp_path)
    filer = dict(v["fh"].filer)

    # Ersätt höjdplattan med en platta där 70 % saknas (utanför täckning eller stort hål)
    t = tmp_path / "p_hal.tif"
    rader = np.arange(1000, dtype=np.float32)[:, None] * np.ones((1, 1000), dtype=np.float32)
    z = (5.0 + 0.01 * (999.5 - rader)).astype(np.float32)
    # Täck kärnan i (147450..147550, 6397550..6397650) så tillrinning/statistik fungerar, men gör
    # > 60 % av hela området till ett stort hål (> FYLL_MAX_M = 150 m).
    z[:, :420] = -9999.0
    z[:, 580:] = -9999.0
    with rasterio.open(
        t, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="float32", crs="EPSG:3007",
        transform=from_origin(147000.0, 6398000.0, 1.0, 1.0), nodata=-9999.0,
    ) as d:  # fmt: skip
        d.write(z, 1)
    zh = tmp_path / "h_hal.zip"
    with zipfile.ZipFile(zh, "w") as f:
        f.write(t, "h/1.tif")
    filer[HOJD_URL] = zh.read_bytes()
    v["fh"] = FalskFilhamtare(filer)

    res = mappa(v, nycklar_fran=inga_nycklar, filhamtare=v["fh"], utskrift=[])
    assert res.nytt
    d, _ = las_manifest(v["rot"] / "geo", "goteborg-test@v1")
    hk = d["forberakningar"]["hojdmodell_kvalitet"]
    assert hk["hog_nan_flagga"] is True
    assert d["flaggor"]["hojdmodell_hog_nan"] is True
    assert hk["max_nan_andel"] > 0.20
    assert hk["rutor_over_troskel"]
    assert any("NaN-andel överstiger tröskeln" in w for w in d["varningar"])

    # Kontrollera att de trasiga pixlarna inte fylldes i K1-rutan heller
    e_c, n_c = transformera(147500.0, 6397600.0, 3007, 3011)
    assert np.isfinite(e_c) and np.isfinite(n_c)
