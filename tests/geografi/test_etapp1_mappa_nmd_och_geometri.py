# ruff: noqa: E501
"""End-to-end-test för `mappa_geografi` med automatiskt NMD (CC0), 3D-brytlinjer, gator och träd."""

from __future__ import annotations

import tomllib
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds
from shapely.geometry import LineString, Point
from tests.data_inlasning.test_nmd_esri_wkt1 import ESRI_WKT1_SWEREF99_TM
from tests.kommuner.test_filpaket_mappa import BASK_URL, HOJD_URL, FalskFilhamtare, inga_nycklar
from tests.kommuner.test_skapa import REPO, bygg_varld, mappa
from tests.lantmateriet.conftest import lm  # noqa: F401
from tests.lantmateriet.falsk_server import FalskLm

from geografi.modell import las_manifest
from kommuner.konfig import tolka_kommun
from kommuner.wfs import KommunKallaFel
from markmodell.hinder_oppningar import BYGGNAD, SKOG
from sankgraf.typer import las_brytlinjer_fran_gpkg
from testsvit.k1_io import las_geotiff

NMD_URL = "https://geodata.naturvardsverket.se/nedladdning/marktacke/NMD2018/bas_lan_ogen/O_lan_nmd2018bas_ogeneraliserad_v1_1.zip"


def _bygg_kompletta_paket(tmp: Path) -> dict[str, bytes]:
    # 1) Höjdmodell (EPSG:3007)
    t = tmp / "p.tif"
    rader = np.arange(1000, dtype=np.float32)[:, None] * np.ones((1, 1000), dtype=np.float32)
    with rasterio.open(
        t, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="float32", crs="EPSG:3007",
        transform=from_origin(147000.0, 6398000.0, 1.0, 1.0), nodata=-9999.0,
    ) as d:
        d.write((5.0 + 0.01 * (999.5 - rader)).astype(np.float32), 1)
    zh = tmp / "h.zip"
    with zipfile.ZipFile(zh, "w") as f:
        f.write(t, "h/1.tif")

    # 2) Baskarta med tak, kantsten, mur, gator, träd
    sd = tmp / "shp"
    sd.mkdir()
    hus = [(147480, 6397600, 25.0), (147490, 6397600, 25.0), (147490, 6397610, 25.0), (147480, 6397610, 25.0), (147480, 6397600, 25.0)]
    gpd.GeoDataFrame({"typ": ["Takkontur"]}, geometry=[LineString(hus)], crs="EPSG:3007").to_file(sd / "byggnad_l.shp")
    gpd.GeoDataFrame(
        {"typ": ["Vägkant, med kantsten", "Vägkant, ej kantsten"]},
        geometry=[
            LineString([(147460, 6397580, 10.12), (147520, 6397580, 10.15)]),
            LineString([(147460, 6397585, 10.00), (147520, 6397585, 10.00)]),
        ],
        crs="EPSG:3007",
    ).to_file(sd / "kommunikation.shp")
    gpd.GeoDataFrame(
        {"typ": ["Mur"]},
        geometry=[LineString([(147465, 6397590, 11.0), (147475, 6397590, 11.0)])],
        crs="EPSG:3007",
    ).to_file(sd / "markdetaljer.shp")
    gpd.GeoDataFrame(
        {"typ": ["Lövträd"]},
        geometry=[Point(147510, 6397610, 10.0)],
        crs="EPSG:3007",
    ).to_file(sd / "markanvandning_p.shp")
    zb = tmp / "b.zip"
    with zipfile.ZipFile(zb, "w") as f:
        for fil in sorted(sd.iterdir()):
            f.write(fil, f"baskarta/{fil.name}")

    # 3) NMD ZIP med Esri WKT1 (täcker Haga + 5 km buffert)
    sb = transform_bounds(3007, 3006, 142000.0, 6392000.0, 153000.0, 6403000.0, densify_pts=21)
    nmd_tif = tmp / "O_lan_nmd2018bas_ogeneraliserad_v1_1.tif"
    with rasterio.open(
        nmd_tif, "w", driver="GTiff", height=120, width=120, count=1, dtype="uint8",
        crs=CRS.from_wkt(ESRI_WKT1_SWEREF99_TM), transform=from_origin(sb[0], sb[3], 100.0, 100.0),
    ) as ds:
        ds.write(np.full((120, 120), 111, dtype=np.uint8), 1)
    zn = tmp / "nmd.zip"
    with zipfile.ZipFile(zn, "w") as f:
        f.write(nmd_tif, "O_lan/O_lan_nmd2018bas_ogeneraliserad_v1_1.tif")

    return {HOJD_URL: zh.read_bytes(), BASK_URL: zb.read_bytes(), NMD_URL: zn.read_bytes()}


def test_mappa_goteborg_med_automatisk_nmd_brytlinjer_gator_och_trad(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811
    v = bygg_varld(lm, tmp_path)
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh:
        d = tomllib.load(fh)
    d["omraden"]["haga"]["bbox"] = [147450.0, 6397550.0, 147550.0, 6397650.0]
    d["lantmateriet"].update(kontext_m=20.0, upplosning_m=2.0)
    d["dagsljus"].update(azimutriktningar=32, hojd_max_m=3, cellstorlek_m=2)
    d["geografier"]["goteborg-test"]["max_buffert_m"] = 400.0
    d["filpaket"]["hojdmodell"]["urlar"] = [HOJD_URL]
    v["kommun"] = tolka_kommun(d)
    fh = FalskFilhamtare(_bygg_kompletta_paket(tmp_path))

    res = mappa(v, nycklar_fran=inga_nycklar, filhamtare=fh)
    assert res.nytt

    manifest, _ = las_manifest(v["rot"] / "geo", "goteborg-test@v1")
    assert "nmd" in manifest["lager"]
    assert "nmd" in manifest["kallor"]

    k1_mapp = v["rot"] / "geo" / "_lager" / manifest["lager"]["k1_rutnat"]["sha256"]
    ra_mapp = v["rot"] / "geo" / "_lager" / manifest["lager"]["radata"]["sha256"]
    assert (ra_mapp / "kommun" / "gator.gpkg").is_file()
    assert (k1_mapp / "gator.gpkg").is_file()
    assert (ra_mapp / "kommun" / "brytlinjer.gpkg").is_file()

    # Minst en K1-ruta ska ha kantsten+mur i brytlinjer.gpkg, vegetation.tif och NMD-markslag (skog + byggnad)
    rutor = sorted(k1_mapp.glob("mm_*"))
    assert rutor
    hittade_bryt = []
    hittade_veg = False
    hittade_skog_och_bygg = False
    for r in rutor:
        hittade_bryt.extend(las_brytlinjer_fran_gpkg(r / "brytlinjer.gpkg"))
        if (r / "vegetation.tif").is_file():
            hittade_veg = True
        ms, _ = las_geotiff(r / "markslag.tif")
        if SKOG in np.unique(ms) and BYGGNAD in np.unique(ms):
            hittade_skog_och_bygg = True
    assert {p.typ for p in hittade_bryt} == {"kantsten", "mur"}
    assert hittade_veg
    assert hittade_skog_och_bygg
    assert any("antagen kronhöjd" in w for w in manifest["varningar"])


def test_mappa_goteborg_fortsatter_med_varning_vid_nmd_natfel(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811
    v = bygg_varld(lm, tmp_path)
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh:
        d = tomllib.load(fh)
    d["omraden"]["haga"]["bbox"] = [147450.0, 6397550.0, 147550.0, 6397650.0]
    d["lantmateriet"].update(kontext_m=20.0, upplosning_m=2.0)
    d["dagsljus"].update(azimutriktningar=32, hojd_max_m=3, cellstorlek_m=2)
    d["geografier"]["goteborg-test"]["max_buffert_m"] = 400.0
    d["filpaket"]["hojdmodell"]["urlar"] = [HOJD_URL]
    v["kommun"] = tolka_kommun(d)
    fh = FalskFilhamtare(_bygg_kompletta_paket(tmp_path))
    fh.fel = KommunKallaFel("timeout mot geodata.naturvardsverket.se")
    fh.fel_for = NMD_URL

    res = mappa(v, nycklar_fran=inga_nycklar, filhamtare=fh)
    assert res.nytt
    assert any("NMD (CC0) kunde inte hämtas" in w for w in res.varningar)


def test_mappa_filpaket_snappar_hojdmodell_till_500m_rutor_noll_utanfor_plattor(
    lm: FalskLm, tmp_path: Path  # noqa: F811
) -> None:
    """Höjdmodell ur filpaket ska täcka hela 500 m-K1-rutnätet även när extent inte är 500 m-justerat."""
    v = bygg_varld(lm, tmp_path)
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh:
        d = tomllib.load(fh)
    # Icke-500 m-justerad bbox (t.ex. Lindholmen/Guldheden-mönster)
    d["omraden"]["haga"]["bbox"] = [147420.0, 6397530.0, 147570.0, 6397680.0]
    d["lantmateriet"].update(kontext_m=30.0, upplosning_m=2.0)
    d["dagsljus"].update(azimutriktningar=32, hojd_max_m=3, cellstorlek_m=2)
    d["geografier"]["goteborg-test"]["max_buffert_m"] = 200.0
    d["filpaket"]["hojdmodell"]["urlar"] = [HOJD_URL]
    v["kommun"] = tolka_kommun(d)

    paket = _bygg_kompletta_paket(tmp_path)
    # Ersätt höjdmodellen med en 2,5 x 2,5 km platta som täcker alla 500 m-snäppta rutor runt Haga
    stor_t = tmp_path / "p_stor.tif"
    with rasterio.open(
        stor_t, "w", driver="GTiff", height=500, width=500, count=1, dtype="float32", crs="EPSG:3007",
        transform=from_origin(146500.0, 6399000.0, 5.0, 5.0), nodata=-9999.0,
    ) as ds:
        ds.write(np.full((500, 500), 12.5, dtype=np.float32), 1)
    zh_stor = tmp_path / "h_stor.zip"
    with zipfile.ZipFile(zh_stor, "w") as zf:
        zf.write(stor_t, "h/stor.tif")
    paket[HOJD_URL] = zh_stor.read_bytes()

    res = mappa(v, nycklar_fran=inga_nycklar, filhamtare=FalskFilhamtare(paket))
    assert res.nytt
    manifest, _ = las_manifest(v["rot"] / "geo", "goteborg-test@v1")
    k1_mapp = v["rot"] / "geo" / "_lager" / manifest["lager"]["k1_rutnat"]["sha256"]
    rutor = sorted(k1_mapp.glob("mm_*"))
    assert rutor
    for r in rutor:
        terr, _ = las_geotiff(r / "terrang.tif")
        assert int(np.count_nonzero(np.isnan(terr))) == 0, f"Oväntad NaN i {r.name}/terrang.tif"
    assert not any("utanför höjdmodellens plattor" in w for w in manifest["varningar"])
