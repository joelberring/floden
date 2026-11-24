# ruff: noqa: E501
"""`floden geografi mappa` ur kommunens filpaket (Göteborg): inga nycklar, ingen WFS, reserv till Lantmäteriet.

SYNTETISKT: höjdmodellen (ett lutande plan) och baskartans taklinjer (dwgread-JSON med ett hus) är påhittade.
"""

from __future__ import annotations

import json
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from geografi.modell import las_manifest
from kommuner.konfig import KommunKonfig, tolka_kommun
from kommuner.skapa import SkapaFel
from kommuner.wfs import KommunKallaFel
from lantmateriet.nycklar import NyckelFel
from tests.kommuner.test_skapa import REPO, bygg_varld, mappa
from tests.lantmateriet.conftest import lm  # noqa: F401
from tests.lantmateriet.falsk_server import FalskLm

HOJD_URL = "https://opengeodata.goteborg.se/downloads/hojdmodell/test_1.zip"
BASK_URL = "https://opengeodata.goteborg.se/downloads/baskarta/baskarta_shp_v2.zip"


def filkommun() -> KommunKonfig:
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh:
        d = tomllib.load(fh)
    d["omraden"]["haga"]["bbox"] = [147450.0, 6397550.0, 147550.0, 6397650.0]
    d["lantmateriet"].update(kontext_m=20.0, upplosning_m=2.0)
    d["dagsljus"].update(azimutriktningar=32, hojd_max_m=3, cellstorlek_m=2)
    d["geografier"]["goteborg-test"]["max_buffert_m"] = 400.0
    d["filpaket"]["hojdmodell"]["urlar"] = [HOJD_URL]
    return tolka_kommun(d)


def paket(tmp: Path) -> dict[str, bytes]:
    t = tmp / "p.tif"
    rader = np.arange(1000, dtype=np.float32)[:, None] * np.ones((1, 1000), dtype=np.float32)
    with rasterio.open(
        t, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="float32", crs="EPSG:3007",
        transform=from_origin(147000.0, 6398000.0, 1.0, 1.0), nodata=-9999.0,
    ) as d:  # fmt: skip
        d.write((5.0 + 0.01 * (999.5 - rader)).astype(np.float32), 1)
    zh = tmp / "h.zip"
    with zipfile.ZipFile(zh, "w") as f:
        f.write(t, "h/1.tif")
    # Shapefile som Göteborgs baskarta_shp_v2: lagret byggnad_l (LineString Z) med fältet typ.
    from shapely.geometry import LineString

    hus = [(147480, 6397600, 25.0), (147490, 6397600, 25.0), (147490, 6397610, 25.0), (147480, 6397610, 25.0), (147480, 6397600, 25.0)]
    fasad = [(147460, 6397600, 12.0), (147470, 6397600, 12.0), (147470, 6397610, 12.0), (147460, 6397610, 12.0), (147460, 6397600, 12.0)]
    sd = tmp / "shp"
    sd.mkdir()
    gpd.GeoDataFrame(
        {"typ": ["Takkontur", "Fasadlinje"]}, geometry=[LineString(hus), LineString(fasad)], crs="EPSG:3007"
    ).to_file(sd / "byggnad_l.shp")
    zb = tmp / "b.zip"
    with zipfile.ZipFile(zb, "w") as f:
        for fil in sorted(sd.iterdir()):
            f.write(fil, f"baskarta/{fil.name}")
    return {HOJD_URL: zh.read_bytes(), BASK_URL: zb.read_bytes()}


class FalskFilhamtare:
    def __init__(self, filer: dict[str, bytes]) -> None:
        self.filer = filer
        self.anrop: list[str] = []
        self.fel: Exception | None = None
        self.fel_for: str = HOJD_URL

    def __call__(self, url: str, mal: Path) -> None:
        self.anrop.append(url)
        if self.fel is not None and url == self.fel_for:
            raise self.fel
        mal.write_bytes(self.filer[url])


def varld(lm: FalskLm, tmp: Path) -> dict[str, Any]:  # noqa: F811
    v = bygg_varld(lm, tmp)
    v["kommun"] = filkommun()
    v["fh"] = FalskFilhamtare(paket(tmp))
    return v


def inga_nycklar(_k: str | None) -> Any:
    raise NyckelFel("Kontot har ingen Lantmäteri-nyckel.")


def test_filpaket_ger_geografi_utan_nyckel_wfs_eller_lantmateriet(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811
    v = varld(lm, tmp_path)
    ut: list[str] = []
    res = mappa(v, nycklar_fran=inga_nycklar, filhamtare=v["fh"], utskrift=ut)
    assert res.nytt
    assert lm.antal("GET", "/stac") == 0
    assert v["fh"].anrop == [HOJD_URL, BASK_URL]
    d, _ = las_manifest(v["rot"] / "geo", "goteborg-test@v1")
    assert d["parametrar"]["hojdkalla"] == "filpaket" and d["parametrar"]["byggnadskalla"] == "filpaket"
    assert d["kallor"]["hojd"]["kalla"] == "filpaket" and d["kallor"]["byggnader"]["licens"] == "CC0-1.0"
    radata = v["rot"] / "geo" / "_lager" / d["lager"]["radata"]["sha256"]
    assert (radata / "hojd" / "hojdmodell_klippt.tif").is_file()
    kallor = json.loads((radata / "filpaket" / "kallor.json").read_text(encoding="utf-8"))
    assert len(kallor["hojdmodell"][0]["sha256"]) == 64 and kallor["baskarta"][0]["url"] == BASK_URL
    gdf = gpd.read_file(radata / "kommun" / "byggnader.gpkg")
    assert len(gdf) == 1 and float(gdf["takhojd_rh2000"].iloc[0]) == pytest.approx(25.0)
    assert any("takkonturer" in x for x in ut)
    # Cachen: en andra version hämtar inget om.
    mappa(v, nycklar_fran=inga_nycklar, filhamtare=v["fh"], version="v2")
    assert v["fh"].anrop == [HOJD_URL, BASK_URL]


def test_hojdmodellen_fallerar_och_nyckel_finns_ger_reserv_med_varning(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811
    v = varld(lm, tmp_path)
    v["fh"].fel = KommunKallaFel("opengeodata.goteborg.se svarade med HTTP 404")
    res = mappa(v, filhamtare=v["fh"])
    assert res.nytt
    d, _ = las_manifest(v["rot"] / "geo", "goteborg-test@v1")
    assert d["parametrar"]["hojdkalla"] == "lantmateriet"
    assert any("reserv" in x.lower() for x in d["varningar"])


def test_baskartans_objektfilter_utan_traff_visar_vardena(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811
    v = varld(lm, tmp_path)
    d = v["kommun"]
    import dataclasses

    fk = d.filpaket
    assert fk is not None and fk.baskarta is not None
    v["kommun"] = dataclasses.replace(
        d, filpaket=dataclasses.replace(fk, baskarta=dataclasses.replace(fk.baskarta, objekt_monster="(?i)^finns_inte"))
    )
    with pytest.raises(SkapaFel, match="Takkontur"):
        mappa(v, nycklar_fran=inga_nycklar, filhamtare=v["fh"])
