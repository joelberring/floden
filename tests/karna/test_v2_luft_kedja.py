"""Regressionstester för Våg 2 luftkedja: K-B5 (NaN i luftgeometri), K-B8 (`luft.halt`
validering & JSON utan NaN) och P1 intern timseriekedja `luft.halt` → `timserie.csv` → `luft.mkn`.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon

from dagsljus.exempelkvarter import bygg_rutnat, rektangel
from karna.analyslosare import LosarFel, LosarKontext, hamta_losare
from karna.geometri.luftgeometri import luft_geometri
from karna.geometri.mosaik import bygg_ytmosaik
from karna.geometri.rasterfil import GeometriFel
from karna.korplan import kor_utredning
from karna.luft_losare import LuftHalt, _las_vagar
from karna.resultatcache import ResultatCache
from karna.rutscenario import materialisera_scenario
from karna.utredning import ScenarioSpec, fraga_fran_dict
from luft.geometri import RasterRuta, voxelisera
from luft.kedja import Haltresultat
from luft.typer import LuftFel
from luft.vindbibliotek import Haltstatistik, Vaderserie, fordela
from testsvit.k1_io import las_geotiff, skriv_geotiff

REPO = Path(__file__).resolve().parents[2]
E0, N0 = 150_000.0, 6_576_000.0
PAR: dict[str, Any] = {
    "dx_m": 4.0,
    "riktningar": 2,
    "genomstromningar": 0.25,
    "buffert_uppstroms_m": 32.0,
    "buffert_nedstroms_m": 64.0,
    "buffert_sidor_m": 24.0,
}


def _skapa_k1_v2_med_sluttning(
    rot: Path,
    *,
    nan_andel: float = 0.0,
    med_veg_nan: bool = False,
) -> Path:
    """Bygger en 1×1 K1 v2-ruta (40×40 m, 1 m pixlar) med hus (tak 26 m RH 2000) och lutande
    terräng (10–14 m RH 2000), samt valfri deterministisk andel NaN-pixlar."""
    hus = [rektangel(E0 + 8.0, E0 + 20.0, N0 + 8.0, N0 + 20.0, 26.0, "a")]

    def veg_fn(ruta: Any) -> np.ndarray[Any, Any]:
        n = int(ruta.storlek_m)
        v = np.zeros((n, n), dtype=np.float32)
        v[2:10, 26:34] = 5.0
        if med_veg_nan:
            v[2:6, 26:30] = np.nan
        return v

    bas = bygg_rutnat(
        rot / "bas",
        hus,
        e_min_m=E0,
        n_min_m=N0,
        antal=1,
        rutstorlek_m=40.0,
        vegetation_hojd_m=veg_fn if med_veg_nan else None,
    )
    ruta = next(bas.glob("mm_*"))
    terr, geo = las_geotiff(ruta / "terrang.tif")
    rader, kol = terr.shape
    # Lutande terräng 10..14 m RH 2000 så att terrang_variation_m kan kontrolleras.
    slutt = np.linspace(10.0, 14.0, rader, dtype=np.float32)[:, None] * np.ones(
        (1, kol), dtype=np.float32
    )
    if nan_andel >= 1.0:
        slutt[:, :] = np.nan
    elif nan_andel > 0.0:
        rng = np.random.default_rng(42)
        mask = rng.random((rader, kol)) < nan_andel
        # Lämna minst ett helt 4×4-block helt NaN för att testa helt saknade block också.
        mask[32:36, 32:36] = True
        slutt[mask] = np.nan
    skriv_geotiff(
        ruta / "terrang.tif",
        slutt,
        x_min=geo.x_min,
        y_max=geo.y_max,
        pixelstorlek=geo.pixelstorlek_x,
    )
    return ruta


# ---------------------------------------------------------------------------
# K-B5: NaN-säker mosaik och luftgeometri
# ---------------------------------------------------------------------------


def test_k_b5_15_procent_nan_bygger_luft_geometri_utan_krasch(tmp_path: Path) -> None:
    ruta = _skapa_k1_v2_med_sluttning(tmp_path, nan_andel=0.15, med_veg_nan=True)

    m = bygg_ytmosaik([ruta], 4.0, vegetation=True)
    assert m.saknade_celler > 0
    assert m.icke_andliga_celler > 0
    assert any("NaN" in v or "icke-ändliga" in v for v in m.varningar)
    assert np.all(np.isfinite(m.terrang_rh2000_m))
    assert np.all(np.isfinite(m.byggnadshojd_m))
    assert m.vegetation_hojd_m is not None and np.all(np.isfinite(m.vegetation_hojd_m))

    g = luft_geometri([ruta], dx_m=4.0, vegetation=True)
    assert np.isfinite(g.matvarden["terrang_variation_m"])
    assert 3.0 <= g.matvarden["terrang_variation_m"] <= 4.1
    assert g.matvarden["saknade_celler"] > 0
    assert g.matvarden["icke_andliga_celler"] > 0
    assert any("NaN" in v or "icke-ändliga" in v for v in g.varningar)
    assert np.all(np.isfinite(g.spec.hojd_over_mark_m))
    assert g.spec.hojd_doman_m >= 24.0
    # Voxelisering måste fungera utan LuftFel på den rensade specifikationen.
    grid = voxelisera(
        g.spec.hojd_over_mark_m,
        g.spec.dx_m,
        g.spec.hojd_doman_m,
        raster_dx_m=g.spec.raster_dx_m,
        raster_origo_x_m=g.spec.raster_origo_x_m,
        raster_origo_y_m=g.spec.raster_origo_y_m,
        buffert_uppstroms_m=16.0,
        buffert_nedstroms_m=32.0,
        buffert_sidor_m=12.0,
        vegetation_hojd_m=g.spec.vegetation_hojd_m,
        lad_m2_m3=g.spec.lad_m2_m3,
    )
    assert int(grid.fast.sum()) > 0

    # Även f == 1 (cell_m == pixelstorlek_m) ska hantera NaN utan krasch.
    g1 = luft_geometri([ruta], dx_m=4.0, raster_cell_m=1.0)
    assert np.isfinite(g1.matvarden["terrang_variation_m"])
    assert g1.matvarden["icke_andliga_celler"] > 0


def test_k_b5_100_procent_nan_ger_tydligt_fel(tmp_path: Path) -> None:
    ruta = _skapa_k1_v2_med_sluttning(tmp_path, nan_andel=1.0)
    with pytest.raises(GeometriFel, match="ändliga|NaN"):
        bygg_ytmosaik([ruta], 4.0)
    with pytest.raises(GeometriFel, match="ändliga|NaN"):
        luft_geometri([ruta], dx_m=4.0)


# ---------------------------------------------------------------------------
# K-B8: vägvalidering och NaN-fritt manifest i luft.halt
# ---------------------------------------------------------------------------


def test_k_b8_las_vagar_avvisar_ogiltiga_geometrier_och_emission(tmp_path: Path) -> None:
    def skriv(namn: str, geoms: list[Any], emission: list[Any]) -> Path:
        fil = tmp_path / f"{namn}.gpkg"
        gpd.GeoDataFrame(
            {"emission_g_m_s": emission},
            geometry=geoms,
            crs="EPSG:3011",
        ).to_file(fil, driver="GPKG")
        return fil

    # 1. Tomt lager
    tom = skriv("tom", [], [])
    with pytest.raises(LuftFel, match="inga vägar"):
        _las_vagar(tom)

    # 2. Punkt i stället för LineString
    punkt = skriv("punkt", [Point(E0 + 10, N0 + 10)], [1e-4])
    with pytest.raises(LuftFel, match="LineString"):
        _las_vagar(punkt)

    # 3. Polygon i stället för LineString
    poly = skriv(
        "poly",
        [Polygon([(E0, N0), (E0 + 10, N0), (E0 + 10, N0 + 10), (E0, N0)])],
        [1e-4],
    )
    with pytest.raises(LuftFel, match="LineString"):
        _las_vagar(poly)

    # 4. Tom geometri (None / LineString())
    tom_geom = skriv("tom_geom", [LineString()], [1e-4])
    with pytest.raises(LuftFel, match="tom|saknas"):
        _las_vagar(tom_geom)

    # 5. Negativ emission
    neg = skriv("neg", [LineString([(E0, N0), (E0 + 20, N0)])], [-1e-4])
    with pytest.raises(LuftFel, match=">= 0"):
        _las_vagar(neg)

    # 6. NaN emission
    nan_e = skriv("nan_e", [LineString([(E0, N0), (E0 + 20, N0)])], [float("nan")])
    with pytest.raises(LuftFel, match="ändligt|saknas"):
        _las_vagar(nan_e)

    # 7. Giltig LineString + MultiLineString med noll- och positiv emission fungerar
    ok = skriv(
        "ok",
        [
            LineString([(E0, N0 + 10), (E0 + 30, N0 + 10)]),
            MultiLineString(
                [
                    [(E0, N0 + 20), (E0 + 15, N0 + 20)],
                    [(E0 + 20, N0 + 20), (E0 + 35, N0 + 20)],
                ]
            ),
        ],
        [1.5e-4, 0.0],
    )
    linjer, em = _las_vagar(ok)
    assert len(linjer) == 3 and em == [1.5e-4, 0.0, 0.0]


def test_k_b8_luft_halt_helt_nan_arsmedel_ger_giltig_json_och_varning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ruta = _skapa_k1_v2_med_sluttning(tmp_path)
    bas = ruta.parent
    vagar = tmp_path / "vagar.gpkg"
    gpd.GeoDataFrame(
        {"emission_g_m_s": [1.0e-4]},
        geometry=[LineString([(E0, N0 + 24), (E0 + 40, N0 + 24)])],
        crs="EPSG:3011",
    ).to_file(vagar, driver="GPKG")
    vader = tmp_path / "vader.csv"
    vader.write_text(
        "tid,vindriktning_grad,vindhastighet_m_s\n0,0,4.0\n1,180,4.0\n",
        encoding="utf-8",
    )

    geom = luft_geometri([ruta], dx_m=4.0)
    rr = RasterRuta(
        origo_x_m=geom.mosaik.x_min_m,
        origo_y_m=geom.mosaik.y_max_m,
        dx_m=geom.mosaik.cell_m,
        rader=geom.mosaik.form[0],
        kolumner=geom.mosaik.form[1],
    )
    vs = Vaderserie.las_csv(vader)
    fd = fordela(vs, [0.0, 180.0], 5.0)
    all_nan = [np.full(rr.form_rc, np.nan), np.full(rr.form_rc, np.nan)]
    stat = Haltstatistik(all_nan, fd)
    falsk_res = Haltresultat(
        ruta=rr,
        halter_per_riktning_g_m3=all_nan,
        fordelning=fd,
        statistik=stat,
        kallrapporter=[],
        massbalans=[{"rel_fel": 0.0}],
        matvarden={"max_rel_massbalansfel": 0.0, "vader": fd.sammanfattning()},
    )

    import karna.luft_losare as ll_mod
    import luft.kedja as kedja_mod

    monkeypatch.setattr(
        ll_mod, "_bygg_bibliotek", lambda _k: (geom, object(), list(geom.varningar))
    )
    monkeypatch.setattr(kedja_mod, "berakna_halter", lambda *a, **kw: falsk_res)

    losare = LuftHalt()
    sc = materialisera_scenario(ScenarioSpec("bas", bas), ResultatCache(tmp_path / "cache"))
    utmapp = tmp_path / "ut"
    utmapp.mkdir()
    ut = losare.kor(
        LosarKontext(
            utmapp=utmapp,
            scenario=sc,
            parametrar=losare.validera_parametrar(PAR),
            indata={"vagar": vagar, "vader": vader},
            beroenden={},
        )
    )
    assert ut.matvarden["max_arsmedel_ug_m3"] is None
    assert any("saknar ändliga celler" in v for v in ut.varningar)
    # Strikt JSON utan NaN måste gå igenom.
    raw_json = json.dumps(dict(ut.matvarden), allow_nan=False)
    assert "NaN" not in raw_json
    assert (utmapp / "timserie.csv").is_file()


# ---------------------------------------------------------------------------
# P1: Intern kedja luft.halt → timserie.csv → luft.mkn
# ---------------------------------------------------------------------------


def test_p1_intern_kedja_halt_till_mkn_fysikaliskt_konsistent(tmp_path: Path) -> None:
    hus = [rektangel(E0 + 8.0, E0 + 20.0, N0 + 8.0, N0 + 20.0, 12.0, "a")]
    bas = bygg_rutnat(tmp_path / "bas", hus, e_min_m=E0, n_min_m=N0, antal=1, rutstorlek_m=40.0)
    vagar = tmp_path / "vagar.gpkg"
    gpd.GeoDataFrame(
        {"emission_g_m_s": [5.0e-4]},
        geometry=[LineString([(E0, N0 + 28.0), (E0 + 40.0, N0 + 28.0)])],
        crs="EPSG:3011",
    ).to_file(vagar, driver="GPKG")

    # 75 timmar: 72 modellerade (3 hela dygn) + 2 svag vind (0,2 m/s) + 1 ogiltig (nan).
    vader = tmp_path / "vader.csv"
    with vader.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["tid", "vindriktning_grad", "vindhastighet_m_s"])
        for h in range(75):
            if h in (5, 30):
                w.writerow([f"2026-01-01T{h:02d}:00", 0.0, 0.2])  # svag vind
            elif h == 50:
                w.writerow([f"2026-01-01T{h:02d}:00", 180.0, "nan"])  # ogiltig
            else:
                rikt = 0.0 if h % 2 == 0 else 180.0
                hast = 2.0 + float(h % 5)
                w.writerow([f"2026-01-01T{h:02d}:00", rikt, hast])

    bg = 12.0
    rec_x, rec_y = E0 + 26.0, N0 + 30.0
    d: dict[str, Any] = {
        "kontrakt": "K20/v1",
        "fraga_id": "luft-kedja-p1",
        "scenarier": {"bas": {"bas": str(bas)}},
        "analyser": [
            {
                "id": "halt",
                "losare": "luft.halt",
                "parametrar": {
                    **PAR,
                    "amne": "PM10",
                    "receptorer": [{"namn": "fasad_nord", "x_m": rec_x, "y_m": rec_y}],
                },
                "indata": {"vagar": str(vagar), "vader": str(vader)},
            },
            {
                "id": "mkn",
                "losare": "luft.mkn",
                "beror_pa": ["halt"],
                "parametrar": {"bakgrund_ug_m3": bg},
            },
            {
                "id": "mkn_rec",
                "losare": "luft.mkn",
                "beror_pa": ["halt"],
                "parametrar": {
                    "bakgrund_ug_m3": bg,
                    "receptor": "fasad_nord",
                    "emissionsfaktor_intervall": [1.0, 1.0],
                    "depositionsfaktor_intervall": [1.0, 1.0],
                },
            },
        ],
    }
    kor_utredning(fraga_fran_dict(d, REPO), ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    sam = json.loads((tmp_path / "ut" / "sammanstallning.json").read_text(encoding="utf-8"))
    jobb = {j["analys"]: j for j in sam["jobb"]}

    # 1. timserie.csv finns och har tids-/enhets-/flaggkolumner
    ts_fil = tmp_path / "ut" / "halt" / "bas" / "timserie.csv"
    assert ts_fil.is_file()
    with ts_fil.open(encoding="utf-8", newline="") as fh:
        las = list(csv.DictReader(fh))
    assert list(las[0].keys()) == [
        "timme",
        "halt_bidrag_ug_m3",
        "svag_vind",
        "ogiltig",
        "receptor",
    ]
    max_rader = [r for r in las if r["receptor"] == "maxcell"]
    rec_rader = [r for r in las if r["receptor"] == "fasad_nord"]
    assert len(max_rader) == 75 and len(rec_rader) == 75
    assert sum(1 for r in max_rader if r["svag_vind"] == "1") == 2
    assert sum(1 for r in max_rader if r["ogiltig"] == "1") == 1
    assert all(r["halt_bidrag_ug_m3"] == "" for r in max_rader if r["svag_vind"] == "1")
    assert all(r["halt_bidrag_ug_m3"] == "" for r in max_rader if r["ogiltig"] == "1")

    # 2. Fysikalisk konsistens mellan luft.halt (årsmedel + timserie) och luft.mkn
    max_bidrag = np.array(
        [
            float(r["halt_bidrag_ug_m3"])
            for r in max_rader
            if r["svag_vind"] == "0" and r["ogiltig"] == "0"
        ],
        dtype=np.float64,
    )
    assert max_bidrag.size == 72
    halt_m = jobb["halt"]["matvarden"]
    assert float(np.mean(max_bidrag)) == pytest.approx(halt_m["max_arsmedel_ug_m3"], rel=1e-9)

    mkn_m = jobb["mkn"]["matvarden"]
    assert mkn_m["bakgrund_ug_m3"] == bg
    assert mkn_m["antal_timmar"] == 72
    assert mkn_m["antal_hela_dygn"] == 3
    assert mkn_m["antal_svag_vind_timmar"] == 2
    assert mkn_m["antal_ogiltiga_timmar"] == 1
    assert any("svag vind" in v for v in jobb["mkn"]["varningar"])
    assert any("ogiltiga vädertimmar" in v for v in jobb["mkn"]["varningar"])

    lag_f, hog_f = 0.8 / 1.25, 1.25 / 0.8
    assert mkn_m["arsmedel_lagst_ug_m3"] == pytest.approx(
        bg + float(np.mean(max_bidrag)) * lag_f, rel=1e-9
    )
    assert mkn_m["arsmedel_hogst_ug_m3"] == pytest.approx(
        bg + float(np.mean(max_bidrag)) * hog_f, rel=1e-9
    )
    dygn_lo = (bg + max_bidrag * lag_f).reshape(3, 24).mean(axis=1)
    dygn_hi = (bg + max_bidrag * hog_f).reshape(3, 24).mean(axis=1)
    assert mkn_m["p90_4_lagst_ug_m3"] == pytest.approx(
        float(np.percentile(dygn_lo, 90.4)), rel=1e-9
    )
    assert mkn_m["p90_4_hogst_ug_m3"] == pytest.approx(
        float(np.percentile(dygn_hi, 90.4)), rel=1e-9
    )
    assert mkn_m["lagst_antal_dygn"] <= mkn_m["hogst_antal_dygn"]

    # 3. Namngiven receptor i mkn_rec med enhetsintervall [1, 1] ger exakt bg + bidrag
    rec_bidrag = np.array(
        [
            float(r["halt_bidrag_ug_m3"])
            for r in rec_rader
            if r["svag_vind"] == "0" and r["ogiltig"] == "0"
        ],
        dtype=np.float64,
    )
    mkn_rec_m = jobb["mkn_rec"]["matvarden"]
    forvantad_rec_medel = bg + float(np.mean(rec_bidrag))
    assert mkn_rec_m["arsmedel_lagst_ug_m3"] == pytest.approx(forvantad_rec_medel, rel=1e-9)
    assert mkn_rec_m["arsmedel_hogst_ug_m3"] == pytest.approx(forvantad_rec_medel, rel=1e-9)


def test_p1_mkn_utan_bakgrund_eller_utan_kalla_avvisas(tmp_path: Path) -> None:
    losare = hamta_losare("luft.mkn")
    with pytest.raises(LosarFel, match="bakgrund_ug_m3"):
        losare.validera_parametrar({})
    with pytest.raises(LosarFel, match=">= 0"):
        losare.validera_parametrar({"bakgrund_ug_m3": -0.5})

    hus = [rektangel(E0 + 8.0, E0 + 20.0, N0 + 8.0, N0 + 20.0, 12.0, "a")]
    bas = bygg_rutnat(tmp_path / "bas", hus, e_min_m=E0, n_min_m=N0, antal=1, rutstorlek_m=40.0)
    sc = materialisera_scenario(ScenarioSpec("bas", bas), ResultatCache(tmp_path / "cache"))
    utmapp = tmp_path / "ut_err"
    utmapp.mkdir()

    # Varken indata.timserie eller beroende -> tydligt fel
    with pytest.raises(LosarFel, match="timserie|luft.halt"):
        losare.kor(
            LosarKontext(
                utmapp=utmapp,
                scenario=sc,
                parametrar=losare.validera_parametrar({"bakgrund_ug_m3": 10.0}),
                indata={},
                beroenden={},
            )
        )
