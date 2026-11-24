# ruff: noqa: E501
"""Valfritt lager för inventerade ädellövträd (punkter som noder även på öppen mark). Alla punkter är påhittade."""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tests.demovarld.test_demo import Demo

from data_inlasning.nmd import NmdLager, skriv_nmd
from ekologi import (
    EkologiFel,
    berakna_konnektivitet,
    extrahera_habitatflackar,
    hamta_natverk,
    las_natverk,
    till_artprofil,
)
from ekologi.natverk import NATVERK_MAPP
from ekologi.tradlager import (
    KOLUMNER,
    TradFel,
    Tradpunkt,
    filtrera_trad,
    klipp_punkter,
    lagg_tradnoder,
    las_tradlager,
    las_tradpunkter,
    skriv_tradlager,
)
from geografi.markunderlag import bygg_markunderlag, las_markunderlag
from geografi.modell import KONTRAKT_GEOGRAFI, GeografiFel, las_manifest, skriv_manifest

KARNA = (151_300.0, 6_580_300.0, 151_400.0, 6_580_400.0)


def _konfig() -> Any:
    (lager,) = hamta_natverk("adellov-nacka").valfria_lager
    assert lager.trad is not None
    return lager.trad


def _gdf(crs: int = 3011) -> Any:
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString, Point

    punkter = [
        (151_320.0, 6_580_320.0, "Ek", 310.0, None),
        (151_340.0, 6_580_340.0, "Skogsek", 150.0, None),
        (151_360.0, 6_580_360.0, "Tall", 200.0, None),  # inte ädellöv
        (151_380.0, 6_580_380.0, "Bok", None, 80.0),  # bara diameter
        (151_390.0, 6_580_390.0, "Quercus robur", None, None),  # saknar mått
    ]
    t = Transformer.from_crs(3011, crs, always_xy=True)
    geom = [Point(*t.transform(e, n)) for e, n, *_ in punkter]
    d = {
        "Tradslag": [p[2] for p in punkter],
        "Stamomkrets": [p[3] for p in punkter],
        "Diameter": [p[4] for p in punkter],
        "Adress": ["Påhittad gata 1"] * len(punkter),
        "Fastighet": ["Påhittad 1:1"] * len(punkter),
        "Inventerare": ["Påhittad Person"] * len(punkter),
    }
    gdf = gpd.GeoDataFrame(d, geometry=geom, crs=crs)
    # en linje i filen: inte en punkt, ska avvisas med varning
    extra = gpd.GeoDataFrame({k: [None] for k in d}, geometry=[LineString([(0, 0), (1, 1)])], crs=crs)
    import pandas as pd

    return gpd.GeoDataFrame(pd.concat([gdf, extra], ignore_index=True), geometry="geometry", crs=crs)


# ------------------------------------------------------------------ konfiguration


def test_adellov_nacka_har_ett_byggt_tradlager_med_kalla_for_varje_varde() -> None:
    a = hamta_natverk("adellov-nacka")
    (lager,) = a.valfria_lager
    assert lager.status == "byggt" and lager.lager_id == "inventerade_adellovtrad"
    k = _konfig()
    assert k.nod.buffert_m == 20.0 and k.nod.buffert_belagd.kalla == "N" and k.nod.buffert_belagd.typ == "litteratur"
    assert k.nod.klass == 116 and k.nod.klass in a.habitat_klasser and k.nod.undantar_min_area
    assert k.nod.belagd.typ == "antagande" and k.filter.belagd.typ == "antagande"
    assert set(k.nod.ej_pa_klasser) == {51, 52, 53, 61, 62}
    d = a.som_dict()["valfria_lager"][0]
    assert d["status"] == "byggt" and d["roll"] == "tradpunkter"
    # Andra nätverk har inget trädlager.
    assert hamta_natverk("barrskog-nacka").valfria_lager == ()


def _med_andring(tmp: Path, ersatt: tuple[str, str]) -> Path:
    text = (NATVERK_MAPP / "adellov-nacka.toml").read_text(encoding="utf-8")
    ny = text.replace(*ersatt)
    assert ny != text
    f = tmp / "adellov-nacka.toml"
    f.write_text(ny, encoding="utf-8")
    return f


@pytest.mark.parametrize(
    ("ersatt", "fel"),
    [
        (("klass = 116\nundantar", "klass = 41\nundantar"), "habitatklasser"),
        (("varde_m = 20.0", "varde_m = 0.0"), "> 0"),
        (("ej_pa_klasser = [51, 52, 53, 61, 62]", "ej_pa_klasser = [51, 116]"), "habitatklasser"),
        (("[valfritt_lager.buffert]\n", "[valfritt_lager.buffert]\nextra = 1\n"), "okända nycklar"),
        (("stamomkrets_enhet = \"cm\"", "stamomkrets_enhet = \"tum\""), "enhet"),
        (('status = "byggt"', 'status = "klar"'), "status"),
        (('roll = "tradpunkter"\n', ""), "roll"),
        (("[valfritt_lager.nod]\nklass", "[valfritt_lager.nod_fel]\nklass"), "saknas"),
    ],
)
def test_tradlagrets_konfiguration_ar_strikt(tmp_path: Path, ersatt: tuple[str, str], fel: str) -> None:
    with pytest.raises(EkologiFel, match=fel):
        las_natverk(_med_andring(tmp_path, ersatt))


# ------------------------------------------------------------------ inläsning och dataminimering


@pytest.mark.parametrize(("fmt", "namn", "crs"), [("GPKG", "trad.gpkg", 3011), ("GeoJSON", "trad.geojson", 4326), ("ESRI Shapefile", "trad.shp", 3006)])
def test_inlasning_ger_bara_punkt_trädslag_och_matt_i_sweref(tmp_path: Path, fmt: str, namn: str, crs: int) -> None:
    gdf = _gdf(crs)
    linje = fmt != "ESRI Shapefile"  # en Shapefile kan inte blanda punkter och linjer
    if not linje:
        gdf = gdf.iloc[:-1].rename(columns={"Stamomkrets": "Omkrets"})
    gdf.to_file(tmp_path / namn, driver=fmt)
    las = las_tradpunkter(tmp_path / namn, _konfig().falt)
    assert las.antal_i_filen == (6 if linje else 5) and las.antal_avvisade == (1 if linje else 0)
    assert any("inte punkter" in v for v in las.varningar) == linje
    assert [p.tradslag for p in las.punkter] == ["Ek", "Skogsek", "Tall", "Bok", "Quercus robur"]
    p0 = las.punkter[0]
    assert (p0.e_m, p0.n_m) == pytest.approx((151_320.0, 6_580_320.0), abs=0.05)
    assert p0.stamomkrets_cm == 310.0 and p0.diameter_cm is None
    assert las.punkter[3].diameter_cm == 80.0 and las.punkter[4].stamomkrets_cm is None
    # Dataminimering: inget annat än de fem fälten finns i resultatet.
    assert set(p0.__dataclass_fields__) == {"e_m", "n_m", "tradslag", "stamomkrets_cm", "diameter_cm"}
    assert "Påhittad" not in repr(las)


def test_inlasning_laser_bara_de_kolumner_konfigurationen_pekar_ut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import geopandas as gpd

    _gdf().to_file(tmp_path / "trad.gpkg", driver="GPKG")
    last: dict[str, Any] = {}
    original = gpd.read_file

    def spion(*a: Any, **k: Any) -> Any:
        last.update(k)
        return original(*a, **k)

    monkeypatch.setattr(gpd, "read_file", spion)
    las_tradpunkter(tmp_path / "trad.gpkg", _konfig().falt)
    assert sorted(last["columns"]) == ["Diameter", "Stamomkrets", "Tradslag"]


def test_inlasning_fel_utan_trädslagsfalt_koordinatsystem_eller_fil(tmp_path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import Point

    gpd.GeoDataFrame({"namn": ["x"]}, geometry=[Point(1, 1)], crs=3011).to_file(tmp_path / "a.gpkg", driver="GPKG")
    with pytest.raises(TradFel, match="inget fält för trädslag"):
        las_tradpunkter(tmp_path / "a.gpkg", _konfig().falt)
    gpd.GeoDataFrame({"tradslag": ["Ek"]}, geometry=[Point(1, 1)]).to_file(tmp_path / "b.geojson", driver="GeoJSON")
    ny = json.loads((tmp_path / "b.geojson").read_text(encoding="utf-8"))
    ny.pop("crs", None)
    (tmp_path / "b.geojson").write_text(json.dumps(ny), encoding="utf-8")
    # GeoJSON utan crs tolkas som WGS 84 av GDAL; en GeoPackage utan crs är däremot ett fel.
    gpd.GeoDataFrame({"tradslag": ["Ek"]}, geometry=[Point(1, 1)]).to_file(tmp_path / "c.gpkg", driver="GPKG")
    with pytest.raises(TradFel, match="saknar koordinatsystem"):
        las_tradpunkter(tmp_path / "c.gpkg", _konfig().falt)
    with pytest.raises(TradFel, match="finns inte"):
        las_tradpunkter(tmp_path / "saknas.gpkg", _konfig().falt)
    (tmp_path / "x.csv").write_text("a\n", encoding="utf-8")
    with pytest.raises(TradFel, match="format som inte stöds"):
        las_tradpunkter(tmp_path / "x.csv", _konfig().falt)


def test_lagring_sparar_bara_fem_kolumner_deterministiskt_och_laser_tillbaka(tmp_path: Path) -> None:
    p = (Tradpunkt(2.0, 1.0, "Ek", 300.0, None), Tradpunkt(1.0, 2.0, "Bok", None, 90.5))
    fil = skriv_tradlager(p, tmp_path / "a" / "tradpunkter.csv", antal_i_filen=7)
    skriv_tradlager(tuple(reversed(p)), tmp_path / "b" / "tradpunkter.csv", antal_i_filen=7)
    assert fil.read_bytes() == (tmp_path / "b" / "tradpunkter.csv").read_bytes()
    assert fil.read_text(encoding="utf-8").splitlines()[0] == ",".join(KOLUMNER)
    meta = json.loads(fil.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["sparade_falt"] == list(KOLUMNER) and meta["antal_punkter"] == 2
    las = las_tradlager(fil)
    assert set(las.punkter) == set(p)
    (tmp_path / "fel.csv").write_text("e_m,n_m,adress\n1,2,Gata 1\n", encoding="utf-8")
    with pytest.raises(TradFel, match="kolumnerna"):
        las_tradlager(tmp_path / "fel.csv")


# ------------------------------------------------------------------ urval


def test_urval_pa_tradslag_och_matt_med_diameter_som_ersattning_och_tydlig_rakning() -> None:
    ek, tall = Tradpunkt(0, 0, "Ek", 310.0), Tradpunkt(1, 1, "Tall", 400.0)
    bok_d = Tradpunkt(2, 2, "Rödbok", None, 40.0)  # omkrets ca 125,7 cm via diametern
    quercus = Tradpunkt(3, 3, "Quercus robur")
    tunn_ek = Tradpunkt(4, 4, "ek", 100.0)
    punkter = (ek, tall, bok_d, quercus, tunn_ek)
    u = filtrera_trad(punkter, _konfig().filter)
    assert set(u.punkter) == {ek, bok_d, quercus, tunn_ek} and u.bortfiltrerade_tradslag == 1 and u.antal_fore == 5
    # Förvalet har ingen måttgräns: träd utan mått följer med (ingen tyst gissning behövs).
    assert u.saknar_matt == 0
    from dataclasses import replace

    filt = replace(_konfig().filter, min_stamomkrets_cm=125.0)
    u2 = filtrera_trad(punkter, filt)
    assert set(u2.punkter) == {ek, bok_d}
    assert u2.bortfiltrerade_matt == 1 and u2.saknar_matt == 1 and u2.varningar and "saknar stamomfång" in u2.varningar[0]
    # Tomt trädslagsurval = alla trädslag.
    assert len(filtrera_trad(punkter, replace(filt, tradslag=(), min_stamomkrets_cm=None)).punkter) == 5


def test_klipp_punkter_tar_kanterna_med() -> None:
    p = (Tradpunkt(0, 0, "Ek"), Tradpunkt(10, 10, "Ek"), Tradpunkt(11, 5, "Ek"))
    assert klipp_punkter(p, (0, 0, 10, 10)) == p[:2]


# ------------------------------------------------------------------ noder i rastret


def _oppen(n: int = 40, kod: int = 42) -> np.ndarray[Any, Any]:
    return np.full((n, n), kod, dtype=np.uint8)


def test_trad_pa_oppen_mark_blir_en_nod_med_skiva_och_aldrig_pa_byggnad_vag_eller_vatten() -> None:
    nod = _konfig().nod
    k = _oppen()
    k[10:14, 10:14] = 51  # hus
    k[:, 30:32] = 53  # väg
    k[:, 36:] = 61  # vatten
    # x0 = 0, y1 = 400, cell 10: cell (rad r, kol c) har mittpunkt (10c+5, 400-10r-5)
    punkter = (
        Tradpunkt(125.0, 275.0, "Ek"),  # på huset (kol 12, rad 12): cellen själv är förbjuden men grannar blir nod
        Tradpunkt(315.0, 205.0, "Ek"),  # på vägen (kol 31): skivan når gräsceller
        Tradpunkt(2000.0, 2000.0, "Ek"),  # utanför rastret
    )
    r = lagg_tradnoder(k, punkter, x_min_m=0.0, y_max_m=400.0, cell_m=10.0, nod=nod)
    assert r.antal_trad == 3 and r.antal_trad_i_rastret == 2 and r.antal_utan_nod == 1
    assert (r.klasser[10:14, 10:14] == 51).all() and (r.klasser[:, 30:32] == 53).all() and (r.klasser[:, 36:] == 61).all()
    assert (r.klasser == 116).sum() == r.antal_celler_nod and r.antal_celler_nod == r.antal_celler_andrade >= 5
    assert r.undantag_min_area.sum() == r.antal_celler_nod
    assert (k == 42).sum() > (r.klasser == 42).sum() and (k == 116).sum() == 0  # indata orörd
    # Skivans omfång: inga celler längre bort än radien (+ en cell) från punkterna.
    rr, cc = np.nonzero(r.klasser == 116)
    e, n = 10.0 * cc + 5.0, 400.0 - 10.0 * rr - 5.0
    d = np.minimum.reduce([np.hypot(e - p.e_m, n - p.n_m) for p in punkter[:2]])
    assert d.max() <= nod.buffert_m + 1e-9


def test_trad_som_bara_har_forbjudna_celler_runt_sig_ger_ingen_nod() -> None:
    nod = _konfig().nod
    k = _oppen(10, 51)
    r = lagg_tradnoder(k, (Tradpunkt(55.0, 55.0, "Ek"),), x_min_m=0.0, y_max_m=100.0, cell_m=10.0, nod=nod)
    assert r.antal_utan_nod == 1 and r.antal_celler_nod == 0 and (r.klasser == 51).all()


def test_liten_cell_och_stor_cell_ger_skiva_av_samma_ungefarliga_area() -> None:
    nod = _konfig().nod
    for cell, n in ((2.0, 200), (10.0, 40)):
        r = lagg_tradnoder(_oppen(n), (Tradpunkt(200.0, 200.0, "Ek"),), x_min_m=0.0, y_max_m=400.0, cell_m=cell, nod=nod)
        assert r.antal_celler_nod * cell * cell == pytest.approx(math.pi * nod.buffert_m**2, rel=0.12)


def test_nod_undantas_fran_minsta_area_men_vanlig_flack_under_minsta_area_gor_det_inte() -> None:
    a = hamta_natverk("adellov-nacka")
    profil = till_artprofil(a)
    assert profil.min_area_m2 == 2500.0
    r = lagg_tradnoder(_oppen(), (Tradpunkt(200.0, 200.0, "Ek"),), x_min_m=0.0, y_max_m=400.0, cell_m=10.0, nod=_konfig().nod)
    skiva_m2 = r.antal_celler_nod * 100.0
    assert skiva_m2 < profil.min_area_m2  # 20 m-skivan är mindre än Nackas 0,25 ha
    utan = extrahera_habitatflackar(r.klasser, profil, cell_m=10.0)
    assert utan.flackar == ()
    med = extrahera_habitatflackar(r.klasser, profil, cell_m=10.0, undantag_min_area=r.undantag_min_area)
    assert len(med.flackar) == 1 and med.flackar[0].area_m2 == skiva_m2
    # En liten skogsfläck utan träd undantas inte, även om en trädfläck på annat håll gör det.
    k = r.klasser.copy()
    k[2:4, 2:4] = 116
    assert len(extrahera_habitatflackar(k, profil, cell_m=10.0, undantag_min_area=r.undantag_min_area).flackar) == 1
    with pytest.raises(EkologiFel, match="undantag_min_area"):
        extrahera_habitatflackar(k, profil, cell_m=10.0, undantag_min_area=np.zeros((3, 3), dtype=bool))


def test_tradnoder_pa_oppen_mark_ger_eca_och_fler_nara_trad_ger_hogre_eca_an_isolerade() -> None:
    a = hamta_natverk("adellov-nacka")
    profil = till_artprofil(a, "1000")
    nod = _konfig().nod

    def eca(punkter: tuple[Tradpunkt, ...]) -> float:
        r = lagg_tradnoder(_oppen(80), punkter, x_min_m=0.0, y_max_m=800.0, cell_m=10.0, nod=nod)
        return berakna_konnektivitet(r.klasser, profil, cell_m=10.0, undantag_min_area=r.undantag_min_area).eca_m2

    assert berakna_konnektivitet(_oppen(80), profil, cell_m=10.0).eca_m2 == 0.0  # utan träd: ingen livsmiljö
    ett = eca((Tradpunkt(400.0, 400.0, "Ek"),))
    nara = eca((Tradpunkt(400.0, 400.0, "Ek"), Tradpunkt(480.0, 400.0, "Ek")))
    langt = eca((Tradpunkt(100.0, 100.0, "Ek"), Tradpunkt(700.0, 700.0, "Ek")))
    assert ett > 0.0 and nara > langt > 0.0 and nara > ett


# ------------------------------------------------------------------ geografins lager (K46)


def test_tradlager_i_geografin_ar_stangt_klippt_och_sha_verifierat(tmp_path: Path) -> None:
    _gdf().to_file(tmp_path / "trad.gpkg", driver="GPKG")
    geo = tmp_path / "geo"
    lager, kallor, varn = bygg_markunderlag(geo, geo / ".b", KARNA, nmd_fil=None, vmi_fil=None, troskel_m=500.0, trad_fil=tmp_path / "trad.gpkg")
    assert set(lager) == {"tradpunkter"} and kallor["tradpunkter"]["licens"] == "se källan"
    t = lager["tradpunkter"]
    assert t["antal_i_filen"] == 6 and t["antal_punkter"] == 5  # alla fem punkterna ligger i kärnan; linjen läses inte
    assert t["sparade_falt"] == ["e_m", "n_m", "tradslag", "stamomkrets_cm", "diameter_cm"]
    assert any("inte punkter" in v for v in varn)
    # Stängt för MCP tills villkoren är kontrollerade (som VMI).
    oppenhet = (geo / "_lager" / t["sha256"] / "oppenhet.toml").read_text(encoding="utf-8")
    assert "oppen = false" in oppenhet
    lager2, _, _ = bygg_markunderlag(geo, geo / ".c", KARNA, nmd_fil=None, vmi_fil=None, troskel_m=500.0, trad_fil=tmp_path / "trad.gpkg", villkor_kontrollerade=True)
    assert "oppen = true" in (geo / "_lager" / lager2["tradpunkter"]["sha256"] / "oppenhet.toml").read_text(encoding="utf-8")
    # Inga personuppgifter i det som lagrats.
    for f in (geo / "_lager" / t["sha256"]).iterdir():
        assert not re.search("Påhittad|Adress|Fastighet|Inventerare", f.read_text(encoding="utf-8"))
    # Manifestet kräver NMD för markunderlaget men trädlagret går att läsa ensamt via las_markunderlag? Nej: NMD krävs.
    skriv_manifest(geo, {"kontrakt": KONTRAKT_GEOGRAFI, "id": "testgeo", "version": "v1", "lager": lager})
    las_manifest(geo, "testgeo@v1")
    (geo / "_lager" / t["sha256"] / "tradpunkter.csv").write_text("andrat", encoding="utf-8")
    with pytest.raises(GeografiFel, match="tradpunkter"):
        las_manifest(geo, "testgeo@v1")


def test_las_markunderlag_ger_trad_klippta_till_utsnittet_och_fel_om_lager_saknas(tmp_path: Path) -> None:
    from tests.ekologi.test_geografi_markunderlag import _nmd_3006

    nmd = tmp_path / "nmd.tif"
    _nmd_3006(nmd)
    _gdf().to_file(tmp_path / "trad.gpkg", driver="GPKG")
    geo = tmp_path / "geo"
    lager, _, _ = bygg_markunderlag(geo, geo / ".b", KARNA, nmd_fil=nmd, vmi_fil=None, troskel_m=500.0, trad_fil=tmp_path / "trad.gpkg")
    skriv_manifest(geo, {"kontrakt": KONTRAKT_GEOGRAFI, "id": "testgeo", "version": "v1", "lager": lager})
    mf = geo / "testgeo" / "v1" / "manifest.json"
    mu = las_markunderlag(mf, KARNA, 500.0, med_trad=True)
    assert mu.tradpunkter is not None and len(mu.tradpunkter) == 5 and mu.trad_sha256 == lager["tradpunkter"]["sha256"]
    assert mu.som_dict()["tradpunkter_lager_sha256"] == mu.trad_sha256
    assert las_markunderlag(mf, KARNA, 500.0).tradpunkter is None
    lager_utan, _, _ = bygg_markunderlag(geo, geo / ".d", KARNA, nmd_fil=nmd, vmi_fil=None, troskel_m=500.0)
    skriv_manifest(geo, {"kontrakt": KONTRAKT_GEOGRAFI, "id": "utan", "version": "v1", "lager": lager_utan})
    with pytest.raises(GeografiFel, match="inget trädlager"):
        las_markunderlag(geo / "utan" / "v1" / "manifest.json", KARNA, 500.0, med_trad=True)


def test_tradfil_for_natverk_utan_tradlager_ger_fel(tmp_path: Path) -> None:
    _gdf().to_file(tmp_path / "trad.gpkg", driver="GPKG")
    geo = tmp_path / "geo"
    with pytest.raises(GeografiFel, match="inget byggt trädlager"):
        bygg_markunderlag(geo, geo / ".b", KARNA, nmd_fil=None, vmi_fil=None, troskel_m=500.0, trad_fil=tmp_path / "trad.gpkg", trad_natverk="barrskog-nacka")
    with pytest.raises(GeografiFel, match="Träd-filen"):
        bygg_markunderlag(geo, geo / ".b", KARNA, nmd_fil=None, vmi_fil=None, troskel_m=500.0, trad_fil=tmp_path / "saknas.gpkg")


# ------------------------------------------------------------------ lösaren (demovärlden, påhittad)


@pytest.fixture(scope="module")
def demo() -> Iterator[Demo]:
    rot = Path(tempfile.mkdtemp())
    try:
        yield Demo(rot)
    finally:
        shutil.rmtree(rot, ignore_errors=True)


def _oppen_varld(rot: Path) -> Path:
    """Hela demovärlden (700 m) som övrig öppen mark: utan träd finns ingen livsmiljö för ädellövnätverket."""
    fil = rot / "oppen_nmd.tif"
    skriv_nmd(NmdLager(np.full((70, 70), 42, dtype=np.uint8), 151_000.0, 6_580_700.0, 10.0), fil)
    return fil


def _trad_gpkg(fil: Path, punkter: list[tuple[float, float, str]]) -> Path:
    import geopandas as gpd
    from shapely.geometry import Point

    gpd.GeoDataFrame(
        {"Tradslag": [p[2] for p in punkter], "Stamomkrets": [250.0] * len(punkter), "Adress": ["Påhittad gata 1"] * len(punkter)},
        geometry=[Point(p[0], p[1]) for p in punkter], crs=3011,
    ).to_file(fil, driver="GPKG")
    return fil


def _fraga(demo: Demo, namn: str, parametrar: str, indata: str) -> str:
    text = (demo.fragor / "demo_ekologi.toml").read_text(encoding="utf-8")
    ny = re.sub(
        r'parametrar = \{ natverk = "barrskog-nacka" \}\nindata = \{ nmd = "[^"]*" \}',
        f"parametrar = {{ {parametrar} }}\nindata = {{ {indata} }}",
        text,
    ).replace('fraga_id = "demo-ekologi"', f'fraga_id = "{namn}"')
    assert ny != text
    (demo.fragor / f"{namn}.toml").write_text(ny, encoding="utf-8")
    return namn


def _tabell(demo: Demo, korning: str) -> dict[str, dict[str, Any]]:
    res = demo.tjanst.resultat(korning)
    tab = next(t for t in res["tabeller"] if t["losare"] == "ekologi.konnektivitet")
    return {r["scenario"]: r["varden"] for r in tab["rader"]}


def test_losaren_gor_trad_pa_oppen_mark_till_noder_och_ett_nytt_hus_tar_bort_noden(demo: Demo, tmp_path: Path) -> None:
    nmd = _oppen_varld(tmp_path)
    trad = _trad_gpkg(
        tmp_path / "trad.gpkg",
        [(151_303.0, 6_580_239.0, "Ek"), (151_260.0, 6_580_239.0, "Bok"), (151_330.0, 6_580_330.0, "Gran"), (151_120.0, 6_580_120.0, "Ek")],
    )
    # Utan träd: ingen livsmiljö alls i en öppen värld (ECA noll).
    namn0 = _fraga(demo, "trad-0", 'natverk = "adellov-nacka"', f'nmd = "{nmd.as_posix()}"')
    demo.tjanst.kor_synkront(namn0, namn0)
    assert _tabell(demo, namn0)["fore"]["eca_m2"] == 0.0
    namn = _fraga(demo, "trad-1", 'natverk = "adellov-nacka", anvand_trad = true', f'nmd = "{nmd.as_posix()}", tradpunkter = "{trad.as_posix()}"')
    status, _ = demo.tjanst.kor_synkront(namn, namn)
    assert status["status"] == "klar"
    t = _tabell(demo, namn)
    assert t["fore"]["eca_m2"] > 0.0 and t["fore"]["antal_flackar"] >= 2
    assert t["efter"]["total_habitat_area_m2"] < t["fore"]["total_habitat_area_m2"]  # huset ligger i skivan
    d = json.loads((demo.tjanst.korningsmapp / namn / "resultat" / "konnektivitet" / "fore" / "ekologi.json").read_text(encoding="utf-8"))
    tl = d["natverk"]["tradlager"]
    assert tl["id"] == "inventerade_adellovtrad" and tl["status"] == "använt"
    assert tl["antal_i_filen"] == 4 and tl["antal_efter_urval"] == 3 and tl["bortfiltrerade_tradslag"] == 1
    assert tl["buffert_m"] == 20.0 and tl["klass"] == 116 and tl["undantar_min_area"] is True
    assert tl["ursprung"]["roll"] == "tradpunkter" and len(tl["ursprung"]["fil_sha256"]) == 64
    assert tl["kalla"]["buffert"]["kalla"] == "N" and tl["kalla"]["nod"]["typ"] == "antagande"
    assert any("antagande" in a for a in tl["antaganden"])
    assert d["natverk"]["valfria_lager"][0]["anvands"] is True
    assert d["resultat_etikett"] == "Potentiell livsmiljö, okänd kvalitet"
    # Ingen personuppgift eller sökväg i resultatet.
    assert "Påhittad" not in json.dumps(d) and str(tmp_path) not in json.dumps(d)
    # Samma fråga igen: cacheträff. Ändrad trädfil: ny körning (filens sha256 ingår i nyckeln).
    _s, oforandrad = demo.tjanst.kor_synkront(namn, namn)
    assert oforandrad


def test_losaren_nekar_trad_utan_parameter_utan_data_och_for_natverk_utan_lager(demo: Demo, tmp_path: Path) -> None:
    nmd = _oppen_varld(tmp_path)
    trad = _trad_gpkg(tmp_path / "trad.gpkg", [(151_300.0, 6_580_300.0, "Ek")])
    n1 = _fraga(demo, "trad-fel1", 'natverk = "adellov-nacka"', f'nmd = "{nmd.as_posix()}", tradpunkter = "{trad.as_posix()}"')
    with pytest.raises(Exception, match="kräver parametern anvand_trad"):
        demo.tjanst.kor_synkront(n1, n1)
    n2 = _fraga(demo, "trad-fel2", 'natverk = "adellov-nacka", anvand_trad = true', f'nmd = "{nmd.as_posix()}"')
    with pytest.raises(Exception, match="kräver indata 'tradpunkter'"):
        demo.tjanst.kor_synkront(n2, n2)
    n3 = _fraga(demo, "trad-fel3", 'natverk = "barrskog-nacka", anvand_trad = true', f'nmd = "{nmd.as_posix()}", tradpunkter = "{trad.as_posix()}"')
    with pytest.raises(Exception, match="byggt trädlager"):
        demo.tjanst.kor_synkront(n3, n3)


def test_losaren_laser_trad_ur_geografins_lager(demo: Demo, tmp_path: Path) -> None:
    import shutil as sh

    from geografi.modell import lagra

    nmd = _oppen_varld(tmp_path)
    geo = tmp_path / "geo"
    mapp = tmp_path / "b_nmd"
    mapp.mkdir()
    sh.copy(nmd, mapp / "nmd.tif")
    sh.copy(nmd.with_suffix(".json"), mapp / "nmd.json")
    lg = lagra(geo, mapp)
    trad = _trad_gpkg(tmp_path / "trad.gpkg", [(151_303.0, 6_580_239.0, "Ek"), (151_260.0, 6_580_239.0, "Ek")])
    skriv_tradlager(
        las_tradpunkter(trad, _konfig().falt).punkter, tmp_path / "b_trad" / "tradpunkter.csv", antal_i_filen=2
    )
    lt = lagra(geo, tmp_path / "b_trad")
    fil, _ = skriv_manifest(
        geo,
        {
            "kontrakt": KONTRAKT_GEOGRAFI, "id": "testgeo", "version": "v1",
            "lager": {
                "nmd": {"sha256": lg.sha256, "buffert_m": 3000.0, "cell_m": 10.0},
                "tradpunkter": {"sha256": lt.sha256, "buffert_m": 3000.0, "antal_punkter": 2},
            },
        },
    )  # fmt: skip
    namn = _fraga(demo, "trad-geo", 'natverk = "adellov-nacka", anvand_trad = true', f'geografi = "{fil.as_posix()}"')
    demo.tjanst.kor_synkront(namn, namn)
    d = json.loads((demo.tjanst.korningsmapp / namn / "resultat" / "konnektivitet" / "fore" / "ekologi.json").read_text(encoding="utf-8"))
    assert d["natverk"]["tradlager"]["ursprung"] == {"roll": "geografi", "lager_sha256": lt.sha256}
    assert d["natverk"]["klassraster"]["markunderlag"]["tradpunkter_lager_sha256"] == lt.sha256
    assert _tabell(demo, namn)["fore"]["eca_m2"] > 0.0
