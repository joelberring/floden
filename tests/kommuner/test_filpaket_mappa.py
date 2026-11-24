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
from tests.lantmateriet.falsk_server import HEMLIGHET as HEMLIGHET_FALSK
from tests.lantmateriet.falsk_server import FalskLm

HOJD_URL = "https://opengeodata.goteborg.se/downloads/hojdmodell/test_1.zip"
BASK_URL = "https://opengeodata.goteborg.se/downloads/baskarta/baskarta_dwg_v2.zip"


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
    hus = [[147480, 6397600], [147490, 6397600], [147490, 6397610], [147480, 6397610]]
    dwg = {
        "OBJECTS": [
            {"object": "LAYER", "handle": [0, 1, 40], "name": "TAK"},
            {
                "entity": "LWPOLYLINE", "handle": [0, 1, 50], "entmode": 2, "layer": [5, 1, 40, 40],
                "flag": 1, "points": hus, "bulges": [], "elevation": 25.0,
            },
        ]
    }
    zb = tmp / "b.zip"
    with zipfile.ZipFile(zb, "w") as f:
        f.writestr("baskarta.json", json.dumps(dwg))
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


def test_baskartan_fallerar_ger_tydligt_fel_och_ingen_reserv(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811
    """Ett fel i baskartan är inte ett höjdfel: ingen reserv till Lantmäteriets laser, och felet säger baskartan."""
    v = varld(lm, tmp_path)
    v["fh"].fel = KommunKallaFel("opengeodata.goteborg.se svarade med HTTP 404")
    v["fh"].fel_for = BASK_URL
    with pytest.raises(SkapaFel, match="baskarta"):
        mappa(v, filhamtare=v["fh"])
    assert lm.antal("GET", "/stac") == 0


def lm_byggnadszip(tmp: Path) -> bytes:
    """Byggnad Nedladdning, vektor som en zip med en GeoPackage (EPSG:3007), med påhittade adresser som inte får sparas."""
    import io

    import shapely

    hus = shapely.box(147480, 6397600, 147490, 6397610)
    gdf = gpd.GeoDataFrame(
        {
            "objektidentitet": ["a1"], "hojd_m": [12.0], "vaningar": [4],
            "adress": ["Provgatan 1"], "fastighetsbeteckning": ["Prov 1:1"],
        },
        geometry=[hus], crs="EPSG:3007",
    )  # fmt: skip
    g = tmp / "lm_byggnad.gpkg"
    gdf.to_file(g, layer="byggnad_yta", driver="GPKG")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(g, "kommun/byggnad_kn1480.gpkg")
    return buf.getvalue()


def _lm_byggnad_feature(lm: FalskLm) -> dict[str, Any]:  # noqa: F811
    return {
        "type": "Feature", "id": "kn1480", "collection": "byggnad", "bbox": [11.0, 57.0, 12.5, 58.5],
        "properties": {"datetime": "2025-01-01T00:00:00Z", "proj:epsg": 3006},
        "assets": {"data": {"href": f"{lm.bas}/files/byggnad_kn1480.zip", "type": "application/zip"}},
    }  # fmt: skip


def test_byggnader_ur_lantmateriet_med_kommunens_hojdmodell(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811, E501
    """`--byggnader-kalla lantmateriet`: höjden ur kommunens fil, byggnaderna ur Lantmäteriets vektorprodukt (falsk server).
    Bara geometri och höjd/våningar sparas; inga adresser eller fastighetsbeteckningar; Göteborgs förval är oförändrat."""
    v = varld(lm, tmp_path)
    assert v["kommun"].byggnader.kalla == "filpaket"  # förvalet rörs inte
    lm.filer["/files/byggnad_kn1480.zip"] = lm_byggnadszip(tmp_path)
    lm.vektor_features = [_lm_byggnad_feature(lm)]
    res = mappa(v, byggnader_kalla="lantmateriet", filhamtare=v["fh"])
    assert res.nytt
    assert v["fh"].anrop == [HOJD_URL]  # baskartan hämtades inte
    assert lm.antal("POST", "/stac-vektor") >= 1 and lm.antal("POST", "/stac-hojd") == 0  # ingen laser
    d, _ = las_manifest(v["rot"] / "geo", "goteborg-test@v1")
    assert d["parametrar"]["hojdkalla"] == "filpaket" and d["parametrar"]["byggnadskalla"] == "lantmateriet"
    assert d["kallor"]["byggnader"]["kalla"] == "lantmateriet" and "kostar" not in json.dumps(d).lower()
    radata = v["rot"] / "geo" / "_lager" / d["lager"]["radata"]["sha256"]
    klippt = radata / "lantmateriet" / "byggnader" / "byggnader_klippt.gpkg"
    gdf = gpd.read_file(klippt)
    assert sorted(gdf.columns) == ["byggnad_id", "geometry", "hojd_m", "vaningar"] and len(gdf) == 1
    for f in radata.rglob("*"):
        if f.is_file() and f.suffix != ".zip":  # originalzippen är Lantmäteriets egen fil, bara i den stängda rådatan
            data = f.read_bytes()
            assert b"Provgatan" not in data and b"Prov 1:" not in data, f.name


def test_byggnader_ur_lantmateriet_ej_aktiv_produkt_namnger_produkten(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811, E501
    v = varld(lm, tmp_path)
    lm.avvisa_tjanster = {"vektor"}
    with pytest.raises(NyckelFel) as e:
        mappa(v, byggnader_kalla="lantmateriet", filhamtare=v["fh"])
    text = str(e.value)
    assert "Byggnad Nedladdning, vektor" in text and "403" in text and "ibland dagar" in text
    assert "Geotorget" in text
    assert HEMLIGHET_FALSK not in text
