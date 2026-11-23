# ruff: noqa: E501
"""Habitatnätverk som konfiguration (K46), källbaserat intervall, ändligt motstånd, NMD- och VMI-inläsare."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import numpy as np
import pytest

from data_inlasning.nmd import (
    NmdFel,
    NmdLager,
    buffert_for_troskel,
    klipp_nmd,
    lagg_hus_som_nmd51,
    las_nmd,
)
from ekologi import (
    EkologiFel,
    alla_natverk,
    berakna_konnektivitet,
    hamta_natverk,
    kallbaserat_intervall,
    kommunforval,
    las_natverk,
    till_artprofil,
)
from ekologi.natverk import KALLOR_FIL, NATVERK_MAPP

REPO = Path(__file__).resolve().parents[2]


def _filer() -> list[Path]:
    return sorted(NATVERK_MAPP.glob("*.toml"))


# ---------------------------------------------------------------- konfigurationen


def test_alla_natverksfiler_laser_och_har_kalla_typ_och_sakerhet_for_varje_varde() -> None:
    kallor = set(tomllib.loads(KALLOR_FIL.read_text(encoding="utf-8")))
    filer = _filer()
    assert len(filer) >= 12 and set(alla_natverk()) == {f.stem for f in filer}
    for fil in filer:
        d = tomllib.loads(fil.read_text(encoding="utf-8"))
        poster = [
            d["habitat"], d["min_area"], *d["scenario"], *d.get("motstand", []),
            *d.get("kompletterande", []),
        ]
        if "d50_antagande" in d:
            poster.append(d["d50_antagande"])
        if "standard_motstand" in d:
            poster.append(d["standard_motstand"])
        for post in poster:
            if post.get("saknas") is True and "antagande_m2" not in post:
                assert post["kalla"] in kallor and post["sida"]
                continue
            assert post["kalla"] in kallor, (fil.name, post)
            assert post["sida"].strip(), (fil.name, post)
            assert post["typ"] in {"uppmätt", "litteratur", "modell", "expertbedömning", "antagande"}
            assert post["sakerhet"] in {"hög", "måttlig", "låg"}


def test_ingen_nätverksfil_har_oandligt_motstand_som_forval() -> None:
    for n in alla_natverk().values():
        assert n.oandliga_klasser() == (), n.natverk_id
        if n.korbar:
            assert all(math.isfinite(v) for v in n.motstand_per_klass().values())


def test_nackas_byggnadsmotstand_och_stockholms_langdistans_ar_enligt_kallan() -> None:
    nmd = {k: hamta_natverk(k).motstand_per_klass() for k in alla_natverk()}
    assert nmd["mindre-hackspett-nacka"][51] == 100.0
    assert nmd["barrskog-nacka"][51] == 100.0
    assert nmd["adellov-nacka"][51] == 40.0
    assert nmd["barrskog-stockholm-2022"]["byggnad"] == 200.0
    assert nmd["adellov-ek-stockholm-2022"]["byggnad"] == 200.0
    assert nmd["groddjur-stockholm-2022"]["byggnad"] == 2000.0
    # Hackspettens tabell: 1 skog, 2 våtmark, 4 vatten och övrig öppen vegetationsklädd mark, 10 åker.
    h = nmd["mindre-hackspett-nacka"]
    assert (h[111], h[2], h[128], h[61], h[42], h[118], h[3], h[41]) == (1, 2, 2, 4, 4, 4, 10, 10)


def test_sodertaljes_d50_ar_inlagda_men_natverken_ar_inte_korbara() -> None:
    forvantat = {
        "kungsfagel-sodertalje": (1250.0, 2500.0),
        "tofsmes-sodertalje": (1250.0, 2500.0),
        "brun-guldbagge-sodertalje": (750.0, 1500.0),
        "storre-vattensalamander-sodertalje": (500.0, 1000.0),
    }
    for nid, (d50, thr) in forvantat.items():
        n = hamta_natverk(nid)
        s = n.scenario()
        assert (s.d50_m, s.troskel_m) == (d50, thr) and not n.korbar
        assert n.d50_for(s) == (d50, None)  # d50 anges av källan: inget antagande
        with pytest.raises(EkologiFel, match="inte körbart"):
            till_artprofil(n)


def test_d50_antagande_ar_troskel_delat_med_tva_och_syns_som_text() -> None:
    n = hamta_natverk("barrskog-nacka")
    for sid, d50 in (("1000", 500.0), ("3000", 1500.0)):
        s = n.scenario(sid)
        assert n.d50_for(s) == (d50, "p saknas i källan; d50 = tröskel/2")
        assert till_artprofil(n, sid).spridningsavstand_m == d50
        assert any("p saknas i källan; d50 = tröskel/2" in a for a in n.antaganden(sid))
    assert n.visningsnamn() == "Barrskogssamband (Nacka/NMD)"
    assert "tofsmes" in n.kalla_rad() and "bil. 2" in n.kalla_rad()


def test_nmd_natverk_anvander_nmd_koder_och_biotopnatverk_kraver_lokal_klasskarta() -> None:
    p = till_artprofil(hamta_natverk("mindre-hackspett-nacka"))
    assert p.habitat_markslag == frozenset({114, 115, 116, 117, 124, 125, 126, 127})
    assert p.min_area_m2 == 10_000.0 and p.spridningsavstand_m == 1500.0
    s = hamta_natverk("groddjur-stockholm-2022")
    assert s.omfattning == "biotop" and "inte översättas rakt av till NMD" in s.nmd_overforing_text
    with pytest.raises(EkologiFel, match="bara lokalt"):
        till_artprofil(s)
    namn = {k for k in s.motstand_per_klass()} | set(s.habitat_klasser)
    koder = {k: i + 1 for i, k in enumerate(sorted(namn))}
    pr = till_artprofil(s, klasskoder=koder)
    assert pr.motstand_per_markslag[koder["byggnad"]] == 2000.0


def test_kommunforval_stockholm_och_goteborg() -> None:
    k = kommunforval()
    assert k["stockholm"].forval_lokalt == (
        "barrskog-stockholm-2022", "adellov-ek-stockholm-2022", "groddjur-stockholm-2022",
    )
    assert "barrskog-nacka" in k["stockholm"].forval_server
    assert k["goteborg"].kvalitetskallor["mindre-hackspett-nacka"] == "mindre-hackspett-goteborg-2023"
    assert not hamta_natverk("mindre-hackspett-goteborg-2023").korbar


def _skriv(tmp: Path, namn: str, text: str) -> Path:
    f = tmp / f"{namn}.toml"
    f.write_text(text, encoding="utf-8")
    return f


def test_laser_ar_strikt_varde_utan_kalla_ger_fel_och_oandligt_kraver_motivering(tmp_path: Path) -> None:
    kalla = (NATVERK_MAPP / "grongoling-nacka.toml").read_text(encoding="utf-8")
    ok = las_natverk(_skriv(tmp_path, "grongoling-nacka", kalla))
    assert ok.natverk_id == "grongoling-nacka"
    saknar_kalla = kalla.replace('kalla = "N"\nsida = "bil. 1 s. 20, tab. 4"\n', "", 1)
    assert saknar_kalla != kalla
    with pytest.raises(EkologiFel, match="kalla"):
        las_natverk(_skriv(tmp_path, "grongoling-nacka", saknar_kalla))
    oandligt = kalla.replace(
        'klasser = [51, 52, 53]\nnmd_namn = "byggnad, övrig exploaterad yta, väg och järnväg"\nvarde = 100\n',
        'klasser = [51, 52, 53]\nnmd_namn = "byggnad"\noandligt = true\n',
    )
    assert oandligt != kalla
    with pytest.raises(EkologiFel, match="motivering"):
        las_natverk(_skriv(tmp_path, "grongoling-nacka", oandligt))
    med = oandligt.replace('oandligt = true\n', 'oandligt = true\nmotivering = "prov"\n', 1)
    n = las_natverk(_skriv(tmp_path, "grongoling-nacka", med))
    assert n.oandliga_klasser() == (51, 52, 53)
    assert math.isinf(till_artprofil(n).motstand_per_markslag[51])
    for fel in ("varde = 0.5", "varde = -3"):
        d = kalla.replace("varde = 100", fel, 1)
        with pytest.raises(EkologiFel):
            las_natverk(_skriv(tmp_path, "grongoling-nacka", d))


# ---------------------------------------------------------------- beräkning


def _corridor(hus: bool) -> np.ndarray:
    """Två barrskogsfläckar (111) med öppen mark (42) emellan; `hus` lägger en rad NMD 51 tvärs över."""
    nmd = np.full((20, 40), 42, dtype=np.uint8)
    nmd[5:15, 2:10] = 111
    nmd[5:15, 30:38] = 111
    if hus:
        nmd[:, 19:21] = 51  # husrad över hela fältet: vägen måste gå genom husen
    return nmd


def test_hus_med_andligt_hogt_motstand_minskar_eca_men_ar_inte_en_total_barriar() -> None:
    n = hamta_natverk("barrskog-nacka")
    pr = till_artprofil(n, "3000")
    fore = berakna_konnektivitet(_corridor(False), pr, cell_m=10.0)
    efter = berakna_konnektivitet(_corridor(True), pr, cell_m=10.0)
    assert fore.antal_flackar == efter.antal_flackar == 2
    assert efter.eca_m2 < fore.eca_m2
    assert efter.p_stjarna[0, 1] > 0.0 and np.isfinite(efter.kostnadsavstand_m[0, 1])


def test_kallbaserat_intervall_ar_min_max_mellan_scenarier_och_monotont() -> None:
    n = hamta_natverk("barrskog-nacka")
    res = {
        s.scenario_id: berakna_konnektivitet(_corridor(True), till_artprofil(n, s.scenario_id), cell_m=10.0)
        for s in n.scenarier
    }
    iv = kallbaserat_intervall(n.natverk_id, res)
    assert iv.eca_min_m2 == res["1000"].eca_m2 and iv.eca_max_m2 == res["3000"].eca_m2
    assert iv.eca_min_m2 < iv.eca_max_m2 and iv.pc_min < iv.pc_max and not iv.ett_scenario
    # G6: ett hus ger lägre ECA i varje scenario, alltså även för båda gränserna.
    fore = {
        s.scenario_id: berakna_konnektivitet(_corridor(False), till_artprofil(n, s.scenario_id), cell_m=10.0)
        for s in n.scenarier
    }
    ivf = kallbaserat_intervall(n.natverk_id, fore)
    assert iv.eca_min_m2 <= ivf.eca_min_m2 and iv.eca_max_m2 <= ivf.eca_max_m2
    ett = kallbaserat_intervall("x", {"a": res["1000"]})
    assert ett.ett_scenario and ett.eca_min_m2 == ett.eca_max_m2


# ---------------------------------------------------------------- NMD och VMI


def _skriv_nmd_3006(sokvag: Path, koder: np.ndarray, e0: float, n1: float) -> None:
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.open(
        sokvag, "w", driver="GTiff", height=koder.shape[0], width=koder.shape[1], count=1,
        dtype="uint8", crs="EPSG:3006", transform=from_origin(e0, n1, 10.0, 10.0),
    ) as ds:
        ds.write(koder, 1)


def test_nmd_klipper_kaerna_plus_buffert_omprojicerar_och_lagger_hus_som_51(tmp_path: Path) -> None:
    from pyproj import Transformer

    # Ett syntetiskt NMD-raster i EPSG:3006: skog (111) i väster, åker (3) i öster.
    t = Transformer.from_crs(3011, 3006, always_xy=True)
    e3006, n3006 = t.transform(151_350.0, 6_580_350.0)
    e0, n1 = math.floor(e3006 / 10) * 10 - 1000, math.ceil(n3006 / 10) * 10 + 1000
    koder = np.full((200, 200), 3, dtype=np.uint8)
    koder[:, :100] = 111
    fil = tmp_path / "nmd_3006.tif"
    _skriv_nmd_3006(fil, koder, e0, n1)
    ut = tmp_path / "ut" / "nmd.tif"
    lager = klipp_nmd(fil, (151_300.0, 6_580_300.0, 151_400.0, 6_580_400.0), 500.0, ut_fil=ut)
    assert lager.cell_m == 10.0 and lager.klasser.dtype == np.uint8
    x0, y0, x1, y1 = lager.utbredning
    assert (x0, y0, x1, y1) == (150_800.0, 6_579_800.0, 151_900.0, 6_580_900.0)
    assert set(np.unique(lager.klasser)) <= {3, 111} and {3, 111} <= set(np.unique(lager.klasser))
    assert (tmp_path / "ut" / "nmd.json").is_file()
    igen = las_nmd(ut)
    assert np.array_equal(igen.klasser, lager.klasser) and igen.x_min_m == x0
    hus = np.zeros((10, 10), dtype=bool)
    hus[2:4, 2:4] = True  # 4 m-celler: 8 x 8 m
    ny, andrade = lagg_hus_som_nmd51(lager, hus, 151_300.0, 6_580_340.0, 4.0)
    assert andrade >= 1 and int(np.count_nonzero(ny == 51)) == andrade
    assert np.count_nonzero(ny != lager.klasser) == andrade


def test_nmd_fel_for_okanda_koder_fel_crs_och_buffertregel(tmp_path: Path) -> None:
    from pyproj import Transformer

    e, n = Transformer.from_crs(3011, 3006, always_xy=True).transform(151_310.0, 6_580_310.0)
    koder = np.full((200, 200), 200, dtype=np.uint8)
    fil = tmp_path / "x.tif"
    _skriv_nmd_3006(fil, koder, math.floor(e / 10) * 10 - 1000, math.ceil(n / 10) * 10 + 1000)
    with pytest.raises(NmdFel, match="Okända NMD-klasskoder"):
        klipp_nmd(fil, (151_300.0, 6_580_300.0, 151_320.0, 6_580_320.0), 0.0)
    assert buffert_for_troskel(500) == 2000.0
    assert buffert_for_troskel(3000) == 3000.0
    assert buffert_for_troskel(9000) == 5000.0
    with pytest.raises(NmdFel):
        buffert_for_troskel(0)
    assert isinstance(NmdLager, type)


def test_vmi_rastreras_till_kand_vatmark_och_markeras_som_aldre(tmp_path: Path) -> None:
    import json

    import geopandas as gpd
    from shapely.geometry import box

    from data_inlasning.vmi import las_vmi_mask, rastrera_vmi, stampla_pa_grid

    gdf = gpd.GeoDataFrame({"namn": ["a"]}, geometry=[box(151_100, 6_580_100, 151_200, 6_580_160)], crs=3011)
    fil = tmp_path / "vmi.gpkg"
    gdf.to_file(fil, driver="GPKG")
    ut = tmp_path / "kand_vatmark.tif"
    lager = rastrera_vmi(fil, (151_000.0, 6_580_000.0, 151_300.0, 6_580_300.0), 100.0, ut_fil=ut)
    assert int(lager.klasser.sum()) == 60  # 10 x 6 celler à 10 m
    meta = json.loads(ut.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["alder"] == "äldre" and "1981-2005" in meta["alder_text"]
    m = las_vmi_mask(ut)
    grid = stampla_pa_grid(m, 151_000.0, 6_580_300.0, 4.0, (75, 75))
    assert int(grid.sum()) > 0
