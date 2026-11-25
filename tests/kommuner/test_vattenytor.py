# ruff: noqa: E501
"""Vattenytor ur strandlinjer (kommuner.vattenytor). SYNTETISKT: en kanal (botten -5, strand +1) i ett lutande plan."""

from __future__ import annotations

import copy
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

from kommuner.k1_filpaket import bygg_omrade_filpaket
from kommuner.konfig import KommunKonfigFel, VattenKonfig, tolka_kommun
from kommuner.vattenytor import (
    las_strandlinjer,
    las_strandlinjer_fil,
    skriv_strandlinjer,
    vattenytor_i_ruta,
)
from lantmateriet.omrade import las_omrade
from markmodell.rutnat import Ruta
from testsvit.k1_io import las_geotiff

REPO = Path(__file__).resolve().parents[2]
E0, N0 = 150000.0, 6400000.0  # EPSG:3011 (testet läser i 3011 direkt)
VATTEN = VattenKonfig("(?i)^hydrografi_l$", "typ", "(?i)^strandlinje")


def _varld() -> tuple[Ruta, np.ndarray[Any, Any], list[Any]]:
    """100 x 100 m, 1 m-celler. Land +3 m. Kanal x 40-60 (botten -5) hela vägen genom rutan, strandlinjer på +1
    längs x=40 och x=60. En sänka på land (x 10-20, y 10-20, botten -2) som inte når någon strandlinje."""
    ruta = Ruta(E0, N0, 100.0, 0.0, 1.0)
    z = np.full(ruta.form_rc, 3.0, dtype=np.float32)
    z[:, 40:60] = -5.0
    z[10:20, 10:20] = -2.0
    linjer = [
        np.array([[E0 + 40.0, N0, 1.0], [E0 + 40.0, N0 + 100.0, 1.0]]),
        np.array([[E0 + 60.0, N0, 1.0], [E0 + 60.0, N0 + 100.0, 1.0]]),
    ]
    return ruta, z, linjer


def test_kanalen_blir_plan_pa_strandnivan_land_orort_och_landssankan_fylls_inte() -> None:
    ruta, z, linjer = _varld()
    mask, niva = vattenytor_i_ruta(z, ruta, linjer, marginal_m=0.2, max_avstand_m=2.0)
    assert mask[:, 41:59].all()  # hela kanalen, även mitten långt från strandlinjen
    assert not mask[:, :38].any() and not mask[:, 62:].any()  # land (inkl. cellerna +3 m)
    assert not mask[10:20, 10:20].any()  # sänkan på land når ingen strandlinje
    assert np.allclose(niva[mask], 1.0)
    assert np.isnan(niva[~mask]).all()


def test_marginalen_och_avstandet() -> None:
    ruta, z, linjer = _varld()
    assert not vattenytor_i_ruta(z, ruta, linjer, marginal_m=7.0, max_avstand_m=2.0)[0].any()  # inget ligger 7 m under +1
    assert not vattenytor_i_ruta(z, ruta, linjer, marginal_m=0.2, max_avstand_m=2.0)[0][:, :40].any()
    z[:, 40:60] = -5.0
    z[:, 48:52] = 3.0  # en bank mitt i: fortfarande två vattenytor som var och en når en strandlinje
    mask, _ = vattenytor_i_ruta(z, ruta, linjer, marginal_m=0.2, max_avstand_m=2.0)
    assert mask[:, 41:47].all() and not mask[:, 49:51].any()
    # Strandlinjer långt från kanalen: ingen yta når en strandlinje.
    langt = [np.array([[E0 + 5.0, N0, 1.0], [E0 + 5.0, N0 + 100.0, 1.0]])]
    assert not vattenytor_i_ruta(z, ruta, langt, marginal_m=0.2, max_avstand_m=2.0)[0].any()


def _zip_med_hydrografi(tmp: Path, linjer: list[np.ndarray[Any, Any]]) -> Path:
    sd = tmp / "shp"
    sd.mkdir()
    geom = [LineString(c) for c in linjer] + [LineString([(E0 + 1, N0 + 1, 0.0), (E0 + 2, N0 + 2, 0.0)])]
    typ = ["Strandlinje"] * len(linjer) + ["Bäck"]
    gpd.GeoDataFrame({"typ": typ}, geometry=geom, crs="EPSG:3011").to_file(sd / "hydrografi_l.shp")
    z = tmp / "b.zip"
    with zipfile.ZipFile(z, "w") as f:
        for fil in sorted(sd.iterdir()):
            f.write(fil, f"baskarta/{fil.name}")
    return z


def test_strandlinjer_las_ur_shapefile_i_zip_bara_rätt_typ(tmp_path: Path) -> None:
    _, _, linjer = _varld()
    z = _zip_med_hydrografi(tmp_path, linjer)
    ut, varn = las_strandlinjer([z], VATTEN, bbox_publicering=(E0 - 10, N0 - 10, E0 + 110, N0 + 110), epsg_publicering=3011)
    assert len(ut) == 2 and not varn  # Bäck utesluts
    assert all(np.allclose(c[:, 2], 1.0) for c in ut)
    fil = skriv_strandlinjer(ut, tmp_path / "k" / "strandlinjer.gpkg")
    igen = las_strandlinjer_fil(fil)
    assert len(igen) == 2 and np.allclose(igen[0], ut[0])
    # Fel typ eller lager ger en varning, inget fel.
    _, v2 = las_strandlinjer([z], VattenKonfig(VATTEN.lager_monster, "typ", "(?i)^finns_inte"), bbox_publicering=(E0 - 10, N0 - 10, E0 + 110, N0 + 110), epsg_publicering=3011)
    assert "Bäck" in v2[0] and "Strandlinje" in v2[0]
    _, v3 = las_strandlinjer([z], VattenKonfig("(?i)^finns_inte$", "typ", "x"), bbox_publicering=(E0 - 10, N0 - 10, E0 + 110, N0 + 110), epsg_publicering=3011)
    assert "inget lager" in v3[0]


class _Hojd:
    plattor: list[Any] = []

    def __init__(self, z: np.ndarray[Any, Any]) -> None:
        self.z = z

    def grund_z(self, ruta: Ruta) -> tuple[np.ndarray[Any, Any], list[str]]:
        return self.z.copy(), []


def test_k1_ruta_far_plan_vattenyta_markslag_8_och_manifestvarning(tmp_path: Path) -> None:
    ruta, z, linjer = _varld()
    omr_fil = tmp_path / "omr.gpkg"
    import shapely

    gpd.GeoDataFrame({"namn": ["o"]}, geometry=[shapely.box(E0, N0, E0 + 100, N0 + 100)], crs=3011).to_file(omr_fil, driver="GPKG")
    omr = las_omrade(omr_fil)
    res = bygg_omrade_filpaket(
        omr, tmp_path / "k1", hojdmodell=_Hojd(z), hojd_paket=[], byggnader_fil=None,  # type: ignore[arg-type]
        upplosning_m=1.0, en_ruta=True, skriv=lambda _t: None, strandlinjer=linjer,
    )  # fmt: skip
    assert res.vatten_m2 == pytest.approx(20.0 * 100.0, rel=0.05)
    assert res.vatten_niva_m == (1.0, 1.0)
    assert any("m² vattenyta ur strandlinjer, nivå 1,0" in v.replace(".", ",") for v in res.varningar)
    mark = next((tmp_path / "k1").glob("*/mark.tif"))
    zz, _ = las_geotiff(mark)
    slag, _ = las_geotiff(mark.parent / "markslag.tif")
    assert float(zz.min()) >= -2.0 - 1e-3  # kanalbottnen på -5 finns inte kvar; sänkan på land (-2) finns kvar
    assert np.allclose(zz[:, 45:55], 1.0)
    assert (slag[:, 45:55] == 8).all() and not (slag[:, :35] == 8).any()


def _kommun_dict() -> dict[str, Any]:
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_goteborgs_fil_har_vattenavsnittet() -> None:
    k = tolka_kommun(_kommun_dict())
    assert k.filpaket is not None and k.filpaket.baskarta is not None
    v = k.filpaket.baskarta.vatten
    assert v is not None and v.objekt_falt == "typ" and v.marginal_m == 0.2 and v.max_avstand_m == 2.0


@pytest.mark.parametrize(
    "andring, text",
    [
        ({"okand": 1}, "okända nycklar"),
        ({"objekt_monster": "("}, "giltigt uttryck"),
        ({"lager_monster": "["}, "giltigt uttryck"),
        ({"objekt_falt": ""}, "objekt_falt"),
        ({"marginal_m": -1.0}, "marginal_m"),
        ({"marginal_m": "0,2"}, "marginal_m"),
        ({"max_avstand_m": 0.0}, "max_avstand_m"),
    ],
)
def test_vattenavsnittet_valideras_strikt(andring: dict[str, Any], text: str) -> None:
    d = copy.deepcopy(_kommun_dict())
    d["filpaket"]["baskarta"]["vatten"].update(andring)
    with pytest.raises(KommunKonfigFel, match=text):
        tolka_kommun(d)


def test_vatten_ar_valfritt_och_inte_i_hojdmodellen() -> None:
    d = copy.deepcopy(_kommun_dict())
    del d["filpaket"]["baskarta"]["vatten"]
    k = tolka_kommun(d)
    assert k.filpaket is not None and k.filpaket.baskarta is not None and k.filpaket.baskarta.vatten is None
    d = copy.deepcopy(_kommun_dict())
    d["filpaket"]["hojdmodell"]["vatten"] = {"lager_monster": "x", "objekt_falt": "typ", "objekt_monster": "x"}
    with pytest.raises(KommunKonfigFel, match="okända nycklar"):
        tolka_kommun(d)
