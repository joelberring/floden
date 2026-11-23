# ruff: noqa: E501
"""Filpaket (ZIP): hämtning till cache, höjdmodell ur GeoTIFF-plattor och takkonturer ur baskartan.

SYNTETISKT: ZIP-filerna byggs i testet. Göteborgs verkliga paket har inte setts (nätet når bara GitHub);
testet visar hanteringen, inte att filerna ser ut så.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from gemensamt.koordinater import transformera
from kommuner.baskarta import tak_till_gpkg
from kommuner.filpaket import hamta_filpaket, lokalt_filpaket, packa_upp_medlem, zip_medlemmar
from kommuner.hojdmodell import Hojdmodell, skriv_klippt_hojd
from kommuner.wfs import KommunKallaFel
from markmodell.rutnat import Ruta

DOMAN = ("opengeodata.goteborg.se",)
URL = "https://opengeodata.goteborg.se/downloads/test/paket.zip"
E0, N0 = 147000.0, 6397000.0  # sydvästra hörnet i EPSG:3007 (Haga ligger inuti)


def _tif(sokv: Path, e0: float = E0, n0: float = N0) -> None:
    """1 x 1 km, 1 m-celler, EPSG:3007: z = 5 + 0,01 * (N - n0)."""
    rader = np.arange(1000, dtype=np.float32)[:, None] * np.ones((1, 1000), dtype=np.float32)
    z = 5.0 + 0.01 * (999.5 - rader)  # rad 0 är norr
    with rasterio.open(
        sokv, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="float32",
        crs="EPSG:3007", transform=from_origin(e0, n0 + 1000.0, 1.0, 1.0), nodata=-9999.0,
    ) as d:  # fmt: skip
        d.write(z.astype(np.float32), 1)


def _hojd_zip(tmp: Path) -> Path:
    t = tmp / "platta.tif"
    _tif(t)
    z = tmp / "hojd.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.write(t, "hojd/1_1.tif")
        f.writestr("hojd/index.shp", b"inte en tif")
    return z


def test_hamtar_en_gang_och_ger_sha256(tmp_path: Path) -> None:
    anrop: list[str] = []

    def hamtare(url: str, mal: Path) -> None:
        anrop.append(url)
        mal.write_bytes(b"PK-innehall")

    cache = tmp_path / "cache"
    a = hamta_filpaket(URL, cache, DOMAN, hamtare=hamtare)
    b = hamta_filpaket(URL, cache, DOMAN, hamtare=hamtare)
    assert anrop == [URL]
    assert a.hamtad_nu and not b.hamtad_nu
    assert a.sha256 == b.sha256 and a.fil == b.fil and a.fil.parent.name == a.sha256
    hamta_filpaket(URL, cache, DOMAN, hamtare=hamtare, tvinga=True)
    assert len(anrop) == 2
    with pytest.raises(KommunKallaFel, match="stämmer inte"):
        hamta_filpaket(URL, cache, DOMAN, hamtare=hamtare, forvantad_sha256="0" * 64, tvinga=True)


def test_nekar_fel_vard_http_och_tom_fil(tmp_path: Path) -> None:
    def tom(url: str, mal: Path) -> None:
        mal.write_bytes(b"")

    with pytest.raises(KommunKallaFel, match="inte tillåten"):
        hamta_filpaket("https://exempel.se/a.zip", tmp_path, DOMAN, hamtare=tom)
    with pytest.raises(KommunKallaFel, match="https"):
        hamta_filpaket("http://opengeodata.goteborg.se/a.zip", tmp_path, DOMAN, hamtare=tom)
    with pytest.raises(KommunKallaFel, match="tom fil"):
        hamta_filpaket(URL, tmp_path, DOMAN, hamtare=tom)


def test_osakra_zipsokvagar_anvands_aldrig(tmp_path: Path) -> None:
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../ond.tif", b"x")
        f.writestr("/abs.tif", b"x")
        f.writestr("ok/a.tif", b"x")
    assert zip_medlemmar(z, (".tif",)) == ["ok/a.tif"]
    with pytest.raises(KommunKallaFel):
        packa_upp_medlem(z, "../ond.tif", tmp_path / "ut")
    ej = tmp_path / "inte.zip"
    ej.write_bytes(b"inte zip")
    with pytest.raises(KommunKallaFel, match="ZIP"):
        zip_medlemmar(ej, (".tif",))


def test_hojdmodell_samplas_om_till_3011_utan_att_andra_z(tmp_path: Path) -> None:
    hm = Hojdmodell([_hojd_zip(tmp_path)], epsg_antagen=3007)  # index.shp-fusk hoppas över
    assert len(hm.plattor) == 1
    e, n = transformera(147450.0, 6397500.0, 3007, 3011)
    ruta = Ruta(float(round(e)) - 100.0, float(round(n)) - 100.0, 200.0, 0.0, 1.0)
    z, varn = hm.grund_z(ruta)
    assert z.shape == ruta.form_rc and not varn
    ce, cn = ruta.cell_till_koordinat(100, 100)
    e7, n7 = transformera(ce, cn, 3011, 3007)
    assert float(z[100, 100]) == pytest.approx(5.0 + 0.01 * (n7 - N0), abs=0.05)


def test_hojdmodell_utanfor_plattan_ger_fel_och_delvis_nan(tmp_path: Path) -> None:
    hm = Hojdmodell([_hojd_zip(tmp_path)], epsg_antagen=3007)
    e, n = transformera(300000.0, 6500000.0, 3007, 3011)
    with pytest.raises(KommunKallaFel, match="täcker inte"):
        hm.grund_z(Ruta(float(round(e)), float(round(n)), 100.0, 0.0, 1.0))
    e, n = transformera(E0 + 1000.0, 6397500.0, 3007, 3011)  # över östra kanten
    z, varn = hm.grund_z(Ruta(float(round(e)) - 50.0, float(round(n)), 100.0, 0.0, 1.0))
    assert np.isnan(z).any() and not np.isnan(z).all() and "saknar värde" in varn[0]


def test_klippt_hojd_kan_laSas_igen(tmp_path: Path) -> None:
    hm = Hojdmodell([_hojd_zip(tmp_path)], epsg_antagen=3007)
    e, n = transformera(147450.0, 6397500.0, 3007, 3011)
    ext = (float(round(e)) - 80.0, float(round(n)) - 80.0, float(round(e)) + 80.0, float(round(n)) + 80.0)
    skriv_klippt_hojd(hm, ext, 1.0, tmp_path / "klippt.tif")
    hm2 = Hojdmodell([tmp_path / "klippt.tif"], epsg_antagen=3011)
    ruta = Ruta(ext[0] + 20.0, ext[1] + 20.0, 100.0, 0.0, 1.0)
    z1, _ = hm.grund_z(ruta)
    z2, _ = hm2.grund_z(ruta)
    assert float(np.nanmax(np.abs(z1 - z2))) < 0.05


def _dwg_json(tak_z: float | None) -> str:
    def lw(handle: int, lager: int, pkt: list[list[float]], z: float | None) -> dict[str, object]:
        d: dict[str, object] = {
            "entity": "LWPOLYLINE", "handle": [0, 1, handle], "entmode": 2, "layer": [5, 1, lager, lager],
            "flag": 1, "points": pkt, "bulges": [],
        }  # fmt: skip
        if z is not None:
            d["elevation"] = z
        return d

    hus = [[147450, 6397500], [147460, 6397500], [147460, 6397510], [147450, 6397510]]
    grann = [[149450, 6397500], [149460, 6397500], [149460, 6397510], [149450, 6397510]]
    return json.dumps(
        {
            "OBJECTS": [
                {"object": "LAYER", "handle": [0, 1, 40], "name": "TAK"},
                {"object": "LAYER", "handle": [0, 1, 41], "name": "FASAD"},
                lw(50, 40, hus, tak_z),
                lw(51, 40, grann, tak_z),  # utanför området
                lw(52, 41, hus, 3.0),  # fel lager
            ]
        }
    )


@pytest.mark.parametrize("namn", ["ritning.json"])
def test_tak_ur_dwgread_json_i_zip(tmp_path: Path, namn: str) -> None:
    z = tmp_path / "baskarta.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr(namn, _dwg_json(21.5))
    ut, antal, varn = tak_till_gpkg(
        [z], bbox_publicering=(147400.0, 6397400.0, 147600.0, 6397600.0), epsg_publicering=3007,
        lager_monster="(?i)(^|[^a-z])tak($|[^a-z])", ut=tmp_path / "byggnader.gpkg",
    )  # fmt: skip
    assert antal == 1 and ut.is_file()
    import geopandas as gpd

    gdf = gpd.read_file(ut)
    assert gdf.crs.to_epsg() == 3011
    assert float(gdf["takhojd_rh2000"].iloc[0]) == pytest.approx(21.5)
    assert gdf["byggnad_id"].iloc[0].startswith("tak-1474")
    assert abs(gdf.geometry.iloc[0].area - 100.0) < 1.0  # ca 0,3 % större i 3011
    assert any("TAK" in v or "tak" in v for v in varn)
    # Och Floden läser filen som byggnadskälla med takhöjden.
    from markmodell.byggnadskallor import las_byggnadskalla

    kand = las_byggnadskalla(ut).kandidater
    assert len(kand) == 1 and kand[0].tak_rh2000 == pytest.approx(21.5)


def test_tak_utan_z_varnar_och_saknat_lager_ger_fel(tmp_path: Path) -> None:
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("r.json", _dwg_json(None))
    _, antal, varn = tak_till_gpkg(
        [z], bbox_publicering=(147400.0, 6397400.0, 147600.0, 6397600.0), epsg_publicering=3007,
        lager_monster="(?i)^tak$", ut=tmp_path / "u.gpkg",
    )  # fmt: skip
    assert antal == 1 and any("standardhöjd" in v for v in varn)
    with pytest.raises(KommunKallaFel, match="Inga takkonturer"):
        tak_till_gpkg(
            [z], bbox_publicering=(100.0, 100.0, 200.0, 200.0), epsg_publicering=3007,
            lager_monster="(?i)^tak$", ut=tmp_path / "v.gpkg",
        )  # fmt: skip
    with pytest.raises(KommunKallaFel, match="Inga takkonturer"):
        tak_till_gpkg(
            [z], bbox_publicering=(147400.0, 6397400.0, 147600.0, 6397600.0), epsg_publicering=3007,
            lager_monster="(?i)^finnsinte$", ut=tmp_path / "w.gpkg",
        )  # fmt: skip


def test_tak_ur_shapefile_i_zip(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import Polygon

    pol = Polygon([(147450, 6397500, 20.0), (147460, 6397500, 22.0), (147460, 6397510, 22.0), (147450, 6397510, 20.0)])
    d = tmp_path / "shp"
    d.mkdir()
    gpd.GeoDataFrame({"a": [1]}, geometry=[pol], crs="EPSG:3007").to_file(d / "TAK.shp")
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as f:
        for p in d.iterdir():
            f.write(p, f"shp/{p.name}")
    ut, antal, _ = tak_till_gpkg(
        [z], bbox_publicering=(147400.0, 6397400.0, 147600.0, 6397600.0), epsg_publicering=3007,
        lager_monster="(?i)(^|[^a-z])tak($|[^a-z])", ut=tmp_path / "s.gpkg",
    )  # fmt: skip
    assert antal == 1
    assert float(gpd.read_file(ut)["takhojd_rh2000"].iloc[0]) == pytest.approx(21.0)


def test_lokalt_filpaket(tmp_path: Path) -> None:
    f = tmp_path / "a.zip"
    f.write_bytes(b"abc")
    assert lokalt_filpaket(f).sha256 and lokalt_filpaket(f).storlek == 3
    with pytest.raises(KommunKallaFel):
        lokalt_filpaket(tmp_path / "saknas.zip")


def test_401_namnar_metod_basic_och_produkten() -> None:
    from lantmateriet.klient import _nyckelmeddelande

    t = _nyckelmeddelande(401)
    assert "Geotorgets användarnamn och lösenord" in t and "Metod = basic" in t
    assert "Laserdata Nedladdning, skog" in t and "STAC Höjd" in t


def test_nyckelsidan_forklarar_oauth2_och_basic() -> None:
    from konto.nycklar import NYCKELTYPER

    typ = NYCKELTYPER["lantmateriet"]
    assert "Metod = basic" in typ.beskrivning and "oauth2" in typ.beskrivning
    namn = " ".join(f.namn for f in typ.falt)
    assert "användarnamn" in namn and "lösenord" in namn and "basic" in namn


def test_goteborgs_fil_ar_filpaket_forst_med_lantmateriet_som_reserv() -> None:
    from kommuner.konfig import las_kommun

    k = las_kommun("goteborg")
    assert k.hojd.kalla == "filpaket" and k.hojd.reserv_lantmateriet
    assert k.byggnader.kalla == "filpaket"
    assert "ortofoto" not in str(k.lantmateriet.produkter) and k.lantmateriet.produkter == ("laser",)
    assert k.filpaket is not None and k.filpaket.baskarta is not None
    assert k.filpaket.baskarta.urlar == ("https://opengeodata.goteborg.se/downloads/baskarta/baskarta_dwg_v2.zip",)
