# ruff: noqa: E501
"""Enhets- och regressionstester för Etapp 1A: 3D-brytlinjer, gator, träd, NMD-markslag, vattenytor och 5 km DEM-skarv."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import shapely
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point

from kommuner.baskarta import (
    brytlinjer_till_gpkg,
    gator_till_gpkg,
    rasterisera_trad_vegetation,
    trad_fran_baskarta,
)
from kommuner.filkallor import _tacker
from kommuner.hojdmodell import Hojdmodell, valj_overlappande_paket_urlar
from kommuner.k1_filpaket import bygg_omrade_filpaket, nmd_till_k1_markslag
from kommuner.konfig import las_kommun
from kommuner.vattenytor import vattenytor_for_rutor_med_diagnos
from lantmateriet.omrade import las_omrade
from markmodell.hinder_oppningar import BERG, BYGGNAD, GRAS, HARDGJORT, SKOG, VATTEN
from markmodell.rutnat import Ruta
from sankgraf.typer import las_brytlinjer_fran_gpkg
from testsvit.k1_io import las_geotiff

REPO = Path(__file__).resolve().parents[2]
E0, N0 = 150000.0, 6400000.0  # EPSG:3011


def test_goteborg_toml_har_brytlinjer_vektorbakgrund_och_nmd() -> None:
    k = las_kommun("goteborg", rot=REPO)
    assert k.filpaket is not None
    assert "geodata.naturvardsverket.se" in k.filpaket.tillatna_varddomaner
    assert k.filpaket.nmd is not None
    assert k.filpaket.nmd.epsg == 3006
    assert any("O_lan_nmd2018bas_ogeneraliserad_v1_1.zip" in u for u in k.filpaket.nmd.urlar)

    bk = k.filpaket.baskarta
    assert bk is not None
    assert bk.vatten is not None
    assert bk.vatten.barriar_monster != ""
    assert len(bk.brytlinjer) == 2
    assert {r.roll for r in bk.brytlinjer} == {"kantsten", "mur"}
    assert bk.vektorbakgrund is not None
    assert len(bk.vektorbakgrund.gator) == 2
    assert bk.vektorbakgrund.trad is not None
    assert bk.vektorbakgrund.trad.kronradie_m == pytest.approx(3.5)
    assert bk.vektorbakgrund.trad.antagen_hojd_m == pytest.approx(8.0)


def _skapa_baskarta_med_geometri(tmp_path: Path) -> Path:
    sd = tmp_path / "shp"
    sd.mkdir()
    # 3D kantsten & vägkanter (EPSG:3007)
    gpd.GeoDataFrame(
        {"typ": ["Vägkant, med kantsten", "Vägkant, ej kantsten", "Spårbunden trafik"]},
        geometry=[
            LineString([(147420.0, 6397520.0, 12.12), (147480.0, 6397520.0, 12.15)]),
            LineString([(147420.0, 6397530.0, 12.00), (147480.0, 6397530.0, 12.00)]),
            LineString([(147420.0, 6397540.0, 12.05), (147480.0, 6397540.0, 12.05)]),
        ],
        crs="EPSG:3007",
    ).to_file(sd / "kommunikation.shp")
    # 3D mur
    gpd.GeoDataFrame(
        {"typ": ["Mur", "Staket"]},
        geometry=[
            LineString([(147430.0, 6397550.0, 13.5), (147470.0, 6397550.0, 13.6)]),
            LineString([(147430.0, 6397560.0, 12.5), (147470.0, 6397560.0, 12.5)]),
        ],
        crs="EPSG:3007",
    ).to_file(sd / "markdetaljer.shp")
    # Kaj/strand
    gpd.GeoDataFrame(
        {"typ": ["Kaj, brygga, fundament etc", "Strandlinje"]},
        geometry=[
            LineString([(147410.0, 6397510.0, 2.0), (147490.0, 6397510.0, 2.0)]),
            LineString([(147410.0, 6397505.0, 0.2), (147490.0, 6397505.0, 0.2)]),
        ],
        crs="EPSG:3007",
    ).to_file(sd / "hydrografi_l.shp")
    # Träd (PointZ)
    gpd.GeoDataFrame(
        {"typ": ["Lövträd", "Barrträd", "Buske"]},
        geometry=[
            Point(147450.0, 6397570.0, 12.0),
            Point(147460.0, 6397575.0, 12.0),
            Point(147465.0, 6397580.0, 12.0),
        ],
        crs="EPSG:3007",
    ).to_file(sd / "markanvandning_p.shp")

    zp = tmp_path / "baskarta_test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for f in sorted(sd.iterdir()):
            zf.write(f, f"baskarta/{f.name}")
    return zp


def test_brytlinjer_gator_och_trad_ur_baskarta(tmp_path: Path) -> None:
    k = las_kommun("goteborg", rot=REPO)
    assert k.filpaket is not None and k.filpaket.baskarta is not None
    bk = k.filpaket.baskarta
    zp = _skapa_baskarta_med_geometri(tmp_path)
    bbox = (147400.0, 6397500.0, 147500.0, 6397600.0)

    # 1) Brytlinjer (kantsten + mur) i K1-format läsbart av sankgraf.typer
    ut_bryt = tmp_path / "brytlinjer.gpkg"
    _, brytlinjer, bv = brytlinjer_till_gpkg(
        [zp], regler=bk.brytlinjer, bbox_publicering=bbox, epsg_publicering=3007, ut=ut_bryt
    )
    assert len(brytlinjer) == 2
    assert {b.typ for b in brytlinjer} == {"kantsten", "mur"}
    assert all(b.z_kron_rh2000 is not None for b in brytlinjer)
    assert any("3D-höjd" in w for w in bv)

    poster = las_brytlinjer_fran_gpkg(ut_bryt)
    assert len(poster) == 2
    assert {p.typ for p in poster} == {"kantsten", "mur"}
    assert all(p.z_kron_rh2000 is not None for p in poster)

    # 2) Gator
    assert bk.vektorbakgrund is not None
    ut_gator = tmp_path / "gator.gpkg"
    _, n_gator, _ = gator_till_gpkg(
        [zp], regler=bk.vektorbakgrund.gator, bbox_publicering=bbox, epsg_publicering=3007, ut=ut_gator
    )
    assert n_gator == 5
    gdf_gator = gpd.read_file(ut_gator, layer="gator")
    assert gdf_gator.crs is not None and gdf_gator.crs.to_epsg() == 3011
    assert set(gdf_gator["kategori"]) == {"gata", "kaj"}

    # 3) Träd + vegetationsraster
    assert bk.vektorbakgrund.trad is not None
    trad, tv = trad_fran_baskarta(
        [zp], trad_konfig=bk.vektorbakgrund.trad, bbox_publicering=bbox, epsg_publicering=3007
    )
    assert len(trad) == 2  # Lövträd + Barrträd (inte Buske)
    assert any("antagen kronhöjd 8 m" in w for w in tv)

    e_med = trad[0][0]
    n_med = trad[0][1]
    ruta = Ruta(float(int(e_med) - 20), float(int(n_med) - 20), 40.0, 0.0, 1.0)
    veg = rasterisera_trad_vegetation(trad, ruta, kronradie_m=3.5, antagen_hojd_m=8.0, upplosning_m=1.0)
    assert veg is not None
    assert veg.shape == ruta.form_rc
    assert float(np.max(veg)) == pytest.approx(8.0)
    assert int(np.count_nonzero(veg > 0.0)) >= 30


def test_nmd_till_k1_markslag_och_inbranning_i_k1_filpaket(tmp_path: Path) -> None:
    koder = np.array([[51, 52, 53, 41], [42, 111, 128, 3], [61, 62, 2, 0]], dtype=np.uint8)
    slag = nmd_till_k1_markslag(koder)
    assert slag[0, 0] == HARDGJORT and slag[0, 1] == HARDGJORT and slag[0, 2] == HARDGJORT
    assert slag[0, 3] == GRAS and slag[1, 0] == GRAS and slag[2, 2] == GRAS
    assert slag[1, 1] == SKOG and slag[1, 2] == SKOG
    assert slag[1, 3] == BERG
    assert slag[2, 0] == VATTEN and slag[2, 1] == VATTEN
    assert slag[2, 3] == HARDGJORT

    # Verifiera bygg_omrade_filpaket med NMD + byggnad ovanpå
    from rasterio.warp import transform_bounds

    omr_fil = tmp_path / "omr.gpkg"
    gpd.GeoDataFrame(
        {"namn": ["test"]},
        geometry=[shapely.box(E0, N0, E0 + 50.0, N0 + 50.0)],
        crs=3011,
    ).to_file(omr_fil, driver="GPKG")
    omr = las_omrade(omr_fil)

    bygg_fil = tmp_path / "byggnader.gpkg"
    gpd.GeoDataFrame(
        {"byggnad_id": ["b1"], "takhojd_rh2000": [15.0]},
        geometry=[shapely.box(E0 + 10.0, N0 + 10.0, E0 + 20.0, N0 + 20.0)],
        crs=3011,
    ).to_file(bygg_fil, driver="GPKG", layer="byggnader")

    sb = transform_bounds(3011, 3006, E0 - 100.0, N0 - 100.0, E0 + 150.0, N0 + 150.0, densify_pts=21)
    nmd_tif = tmp_path / "nmd.tif"
    with rasterio.open(
        nmd_tif,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="uint8",
        crs="EPSG:3006",
        transform=from_origin(sb[0] - 50.0, sb[3] + 50.0, 10.0, 10.0),
    ) as ds:
        ds.write(np.full((40, 40), 115, dtype=np.uint8), 1)  # Skog

    class _PlattHojd:
        plattor: list[Any] = []

        def grund_z(self, ruta: Ruta) -> tuple[np.ndarray[Any, Any], list[str]]:
            return np.full(ruta.form_rc, 5.0, dtype=np.float32), []

    ut = tmp_path / "k1"
    res = bygg_omrade_filpaket(
        omr,
        ut,
        hojdmodell=_PlattHojd(),  # type: ignore[arg-type]
        hojd_paket=[],
        byggnader_fil=bygg_fil,
        en_ruta=True,
        nmd_fil=nmd_tif,
    )
    ruta_mapp = ut / res.rutor[0]
    ms, _ = las_geotiff(ruta_mapp / "markslag.tif")
    assert SKOG in np.unique(ms)
    assert BYGGNAD in np.unique(ms)


def test_vattenytor_lindholmen_regression_kajbarriar_och_nmd_bevarar_kanal() -> None:
    """Rosenlundskanalen planas ut korrekt, medan låglänt varvsmark bakom kajkant (Lindholmen) INTE blir Vattenyta."""
    ruta = Ruta(E0, N0, 100.0, 0.0, 1.0)
    z = np.full(ruta.form_rc, 2.5, dtype=np.float32)

    # Södra delen (rad 60..100 => N = N0..N0+40): älv/kanal med botten -4,0 m
    z[60:100, :] = -4.0
    # Norra delen (rad 10..58 => N = N0+42..N0+90): plan, låglänt f.d. varvsmark på -0,15 m bakom kajkant!
    # Utan kajbarriär & NMD skulle den ligga under strandlinjen (+0,2 m - 0,2 m marginal) och slukas som Vattenyta!
    z[10:58, :] = -0.15

    # Strandlinje vid N = N0 + 40 (rad 60) med z = +0,2 m RH 2000
    strandlinjer = [
        np.array([[E0, N0 + 40.0, 0.2], [E0 + 100.0, N0 + 40.0, 0.2]], dtype=np.float64),
    ]
    # Kajbarriär längs N = N0 + 41 (rad 59)
    barriarer = [
        np.array([[E0, N0 + 41.0, 1.8], [E0 + 100.0, N0 + 41.0, 1.8]], dtype=np.float64),
    ]
    # NMD: kod 61 (vatten) i kanalen/älven, kod 52 (exploaterad varvsmark) norr om kajen
    nmd = np.full(ruta.form_rc, 52, dtype=np.uint8)
    nmd[60:100, :] = 61

    res, diag = vattenytor_for_rutor_med_diagnos(
        [(ruta, z)],
        strandlinjer,
        marginal_m=0.2,
        max_avstand_m=3.0,
        barriar_linjer=barriarer,
        nmd_per_ruta=[nmd],
    )
    vmask, vniva = res[0]

    # Kanalen/älven planas ut korrekt vid +0,2 m
    assert vmask[65:95, 10:90].all()
    assert np.allclose(vniva[65:95, 10:90], 0.2)

    # Låglänt varvsmark norr om kajen blir INTE vattenyta
    assert not vmask[10:58, :].any()
    assert any(d.status == "godkand" for d in diag)

    # Testa även att öppen gräsmark (kod 41, ej exploaterad) bakom kaj avvisas som avvisad_land när den saknar NMD-vatten
    nmd_gras = np.full(ruta.form_rc, 41, dtype=np.uint8)
    nmd_gras[60:100, :] = 61
    res2, diag2 = vattenytor_for_rutor_med_diagnos(
        [(ruta, z)],
        strandlinjer,
        marginal_m=0.2,
        max_avstand_m=3.0,
        barriar_linjer=barriarer,
        nmd_per_ruta=[nmd_gras],
    )
    assert res2[0][0][65:95, 10:90].all()
    assert not res2[0][0][10:58, :].any()
    assert any(d.status == "avvisad_land" for d in diag2)


def test_5km_och_10km_dem_skarv_valjer_alla_overlappande_paket(tmp_path: Path) -> None:
    """5 km- och 10 km-ZIP-paket som överlappar hela buffert-bboxen väljs, och `_tacker` luras inte av roterade hörn."""
    # Lindholmen-liknande extent i EPSG:3011 med 1000 m buffert (korsar N=6400000 i EPSG:3007!)
    extent_3011 = (-212200.0, 6413800.0, -209800.0, 6416200.0)
    urlar_10km = [
        "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_639_13.zip",
        "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_639_14.zip",
        "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_640_14.zip",
        "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_641_16.zip",
    ]
    valda_10 = valj_overlappande_paket_urlar(urlar_10km, extent_3011, epsg_antagen=3007)
    assert any("639_14.zip" in u for u in valda_10)
    assert any("640_14.zip" in u for u in valda_10)
    assert not any("641_16.zip" in u for u in valda_10)

    # 5 km-rutor runt skarven 6395_145 / 6400_145
    urlar_5km = [
        "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_6395_145.zip",
        "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_6400_145.zip",
        "https://opengeodata.goteborg.se/downloads/hojdmodell/2022/hojdmodell_2022_6380_130.zip",
    ]
    valda_5 = valj_overlappande_paket_urlar(urlar_5km, extent_3011, epsg_antagen=3007)
    assert len(valda_5) == 2
    assert any("6395_145.zip" in u for u in valda_5)
    assert any("6400_145.zip" in u for u in valda_5)

    # Roterad platta i EPSG:3007 ska INTE felaktigt påstå att den täcker sin axeljusterade 3011-bbox
    t = tmp_path / "platta.tif"
    with rasterio.open(
        t,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="float32",
        crs="EPSG:3007",
        transform=from_origin(146000.0, 6399000.0, 10.0, 10.0),
    ) as ds:
        ds.write(np.ones((100, 100), dtype=np.float32), 1)
    hoj = Hojdmodell([t], epsg_antagen=3007)
    from rasterio.warp import transform_bounds

    ax_bbox_3011 = transform_bounds(3007, 3011, 146000.0, 6398000.0, 147000.0, 6399000.0)
    # Den axeljusterade 3011-boxens hörn ligger utanför den roterade 3007-kvadraten
    assert not _tacker(hoj, ax_bbox_3011)
