"""Regressionstester för Våg 4 (K-B10a & K-B10b): `jamfor` och `bifurkation`.

Verifierar:
1. K-B10a (`src/karna/jamfor.py`): `r_max` och `r_min` beräknas strikt på `diff[giltig]`,
   så extrema spikar i oägda överlappsceller eller NaN-maskade celler aldrig förvränger
   sammanfattningen, och tomma giltiga mängder hanteras rent utan NaN.
2. K-B10b (`src/sankgraf/bifurkation.py`): valfri bakåtkompatibel `brytlinjer`-parameter
   vidarebefordras till `bygg_sankgraf` och till 4-grannsadelkanterna så att 2D/3D-kantstenar
   ger fysikaliskt korrekta bifurkationsresultat och bifiltreringsexporter.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gemensamt.koordinater import EPSG_INTERN
from karna.analyslosare import hamta_losare
from karna.jamfor import kor_jamforelse
from karna.korplan import Jobb, Plan
from karna.resultatcache import ResultatCache
from karna.rutscenario import Ruta as KarnaRuta
from karna.rutscenario import RutScenario
from karna.utredning import Analys, Jamforelse, KorbarFraga
from sankgraf.bifurkation import detektera_bifurkationer, exportera_bifiltrering_json
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, skriv_geotiff


def _skriv_cache_raster(
    cache: ResultatCache,
    nyckel: str,
    arr: np.ndarray[Any, Any],
    *,
    x_min: float,
    y_max: float,
    pixelstorlek_m: float,
    filnamn: str = "djup.tif",
) -> None:
    """Lagrar ett syntetiskt GeoTIFF-resultat under ett jobb i `ResultatCache`."""
    ut_mapp = cache.jobbmapp(nyckel) / "utdata"
    ut_mapp.mkdir(parents=True, exist_ok=True)
    skriv_geotiff(
        ut_mapp / filnamn,
        np.asarray(arr, dtype=np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=pixelstorlek_m,
    )


def _bygg_tvaruts_plan(
    tmp_path: Path,
    cache: ResultatCache,
    *,
    fore_r0: np.ndarray[Any, Any],
    efter_r0: np.ndarray[Any, Any],
    fore_r1: np.ndarray[Any, Any],
    efter_r1: np.ndarray[Any, Any],
    troskel_m: float = 0.01,
) -> tuple[Jamforelse, Plan]:
    """Bygger en deterministisk `Plan` med två överlappande rutor (5 m överlapp, 1 m pixlar)."""
    px = 1.0
    # ruta_0: x in [150000, 150010], y in [6576000, 6576010] -> äger x < 150007.5 (kol 0..6)
    # ruta_1: x in [150005, 150015], y in [6576000, 6576010] -> äger x > 150007.5 (kol 3..9)
    r0 = KarnaRuta(
        ruta_id="ruta_0",
        mapp=tmp_path / "r0",
        hash="h0",
        x_min=150000.0,
        y_min=6576000.0,
        x_max=150010.0,
        y_max=6576010.0,
    )
    r1 = KarnaRuta(
        ruta_id="ruta_1",
        mapp=tmp_path / "r1",
        hash="h1",
        x_min=150005.0,
        y_min=6576000.0,
        x_max=150015.0,
        y_max=6576010.0,
    )
    scen_f = RutScenario(id="fore", nyckel="sf", rutor=(r0, r1))
    scen_e = RutScenario(id="efter", nyckel="se", rutor=(r0, r1))
    analys = Analys(id="skyfall", losare="geometri.lutning", scenarier=("fore", "efter"))
    losare = hamta_losare("geometri.lutning")

    def _jobb(scen: RutScenario, r: KarnaRuta, nyckel: str) -> Jobb:
        return Jobb(
            analys=analys,
            losare=losare,
            scenario=scen,
            parametrar={},
            radie_m=0.0,
            ruta=r,
            grannar=(r,),
            beroende_nycklar={},
            nyckel_objekt={},
            nyckel=nyckel,
            i_cache=True,
            utdata={"filer": {"max_djup": {"fil": "djup.tif"}}},
        )

    k_f0, k_e0, k_f1, k_e1 = "1" * 64, "2" * 64, "3" * 64, "4" * 64
    _skriv_cache_raster(cache, k_f0, fore_r0, x_min=r0.x_min, y_max=r0.y_max, pixelstorlek_m=px)
    _skriv_cache_raster(cache, k_e0, efter_r0, x_min=r0.x_min, y_max=r0.y_max, pixelstorlek_m=px)
    _skriv_cache_raster(cache, k_f1, fore_r1, x_min=r1.x_min, y_max=r1.y_max, pixelstorlek_m=px)
    _skriv_cache_raster(cache, k_e1, efter_r1, x_min=r1.x_min, y_max=r1.y_max, pixelstorlek_m=px)

    jf = Jamforelse(
        analys="skyfall", utdata="max_djup", fore="fore", efter="efter", troskel=troskel_m
    )
    fraga = KorbarFraga(
        fraga_id="k-b10a",
        beskrivning="K-B10a",
        scenarier={},
        analyser=(analys,),
        jamforelser=(jf,),
    )
    plan = Plan(
        fraga=fraga,
        scenarier={"fore": scen_f, "efter": scen_e},
        jobb=[
            _jobb(scen_f, r0, k_f0),
            _jobb(scen_e, r0, k_e0),
            _jobb(scen_f, r1, k_f1),
            _jobb(scen_e, r1, k_e1),
        ],
    )
    return jf, plan


def test_k_b10a_jamfor_spik_i_oagd_overlappscell_paverkar_inte_max_min(tmp_path: Path) -> None:
    """Extrem spik (+999 m / -888 m) i oägd överlappscell får INTE slå igenom i `r_max`/`r_min`."""
    cache = ResultatCache(tmp_path / "cache")
    fore_r0 = np.zeros((10, 10), dtype=np.float32)
    efter_r0 = np.zeros((10, 10), dtype=np.float32)
    # Ägda celler i ruta_0 (kol 0..6 ligger på x = 150000.5..150006.5 < 150007.5)
    efter_r0[2, 2] = 0.25
    efter_r0[3, 3] = -0.10
    # Oägda överlappsceller i ruta_0 (kol 8..9 ligger på x = 150008.5..150009.5, ägs av ruta_1!)
    efter_r0[4, 8] = 999.0
    efter_r0[5, 9] = -888.0

    fore_r1 = np.zeros((10, 10), dtype=np.float32)
    efter_r1 = np.full((10, 10), 0.05, dtype=np.float32)
    # Även en oägd överlappscell i ruta_1 (kol 0 ligger på x = 150005.5, ägs av ruta_0!)
    efter_r1[1, 0] = 777.0
    efter_r1[2, 0] = -666.0

    jf, plan = _bygg_tvaruts_plan(
        tmp_path,
        cache,
        fore_r0=fore_r0,
        efter_r0=efter_r0,
        fore_r1=fore_r1,
        efter_r1=efter_r1,
    )
    res = kor_jamforelse(jf, plan, cache, tmp_path / "ut")

    assert res["per_ruta"]["ruta_0"]["max_okning"] == pytest.approx(0.25, abs=1e-6)
    assert res["per_ruta"]["ruta_0"]["max_minskning"] == pytest.approx(0.10, abs=1e-6)
    assert res["per_ruta"]["ruta_1"]["max_okning"] == pytest.approx(0.05, abs=1e-6)
    assert res["per_ruta"]["ruta_1"]["max_minskning"] == pytest.approx(0.0, abs=1e-6)
    assert res["max_okning"] == pytest.approx(0.25, abs=1e-6)
    assert res["max_minskning"] == pytest.approx(0.10, abs=1e-6)


def test_k_b10a_jamfor_inga_giltiga_agda_celler_ger_rent_noll_utan_nan(tmp_path: Path) -> None:
    """Om alla ägda celler är NaN (bara oägt överlapp är ändligt) ska sammanfattningen bli 0,0."""
    cache = ResultatCache(tmp_path / "cache")
    fore_r0 = np.full((10, 10), np.nan, dtype=np.float32)
    efter_r0 = np.full((10, 10), np.nan, dtype=np.float32)
    # Ändlig spik enbart i oägt överlapp (kol 9 ägs av ruta_1)
    fore_r0[4, 9] = 0.0
    efter_r0[4, 9] = 500.0

    # ruta_1 har bara negativa ändringar i ägda celler (-0,30 m)
    fore_r1 = np.full((10, 10), 1.0, dtype=np.float32)
    efter_r1 = np.full((10, 10), 0.7, dtype=np.float32)

    jf, plan = _bygg_tvaruts_plan(
        tmp_path,
        cache,
        fore_r0=fore_r0,
        efter_r0=efter_r0,
        fore_r1=fore_r1,
        efter_r1=efter_r1,
    )
    res = kor_jamforelse(jf, plan, cache, tmp_path / "ut")

    assert math.isfinite(res["per_ruta"]["ruta_0"]["max_okning"])
    assert math.isfinite(res["per_ruta"]["ruta_0"]["max_minskning"])
    assert res["per_ruta"]["ruta_0"]["max_okning"] == 0.0
    assert res["per_ruta"]["ruta_0"]["max_minskning"] == 0.0
    assert res["per_ruta"]["ruta_1"]["max_okning"] == 0.0
    assert res["per_ruta"]["ruta_1"]["max_minskning"] == pytest.approx(0.3, abs=1e-5)
    assert res["max_okning"] == 0.0
    assert res["max_minskning"] == pytest.approx(0.3, abs=1e-5)


def _sadel_terrang_for_brytlinje(
    delta_z_m: float = 0.04,
    sigma_m: float = 0.05,
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], np.ndarray[Any, Any], GeoTransformInfo]:
    """Syntetisk terräng med mittsänka M (10,0 m) och två konkurrerande sadlar mot V och H.

    - Vänster sadel M <-> V vid cell (4, 4): 10,50 m
    - Höger sadel M <-> H vid cell (4, 10): 10,50 + delta_z_m
    """
    h, w = 9, 15
    z = np.full((h, w), 12.0, dtype=np.float32)
    z[2:7, 1:4] = 9.0
    z[4, 0] = 9.5
    z[2:7, 5:10] = 10.0
    z[2:7, 11:14] = 9.0
    z[4, 14] = 9.5
    z[4, 4] = 10.50
    z[4, 10] = np.float32(10.50 + delta_z_m)

    osak = np.full((h, w), sigma_m, dtype=np.float32)
    markslag = np.ones((h, w), dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=w,
        hojd=h,
        x_min=150000.0,
        y_max=6580000.0 + float(h),
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        epsg=EPSG_INTERN,
        dtype="float32",
    )
    return z, osak, markslag, geo


def test_k_b10b_bifurkation_3d_brytlinje_pa_alternativ_sadel_stabiliserar(tmp_path: Path) -> None:
    """En 3D-kantsten över den konkurrerande sadeln höjer marginalen och eliminerar instabilitet."""
    z, osak, markslag, geo = _sadel_terrang_for_brytlinje(delta_z_m=0.04, sigma_m=0.05)
    bas_graf = bygg_sankgraf(z, osak, markslag, geo)
    mitt_id = int(bas_graf.cell_till_nod[4, 7])
    vanster_id = int(bas_graf.cell_till_nod[4, 2])
    hoger_id = int(bas_graf.cell_till_nod[4, 12])

    # Utan brytlinjer: M är instabil (Δz = 0,04 m, σ_komb ≈ 0,0707 m -> säkerhetsmarginal <= 2)
    utan = detektera_bifurkationer(bas_graf, osakerhet_m=osak, k_sigma=2.0, brytlinjer=None)
    mitt_utan = [p for p in utan if p.nod_id == mitt_id]
    assert len(mitt_utan) == 1
    assert mitt_utan[0].instabil is True
    assert mitt_utan[0].primar_ut_till_id == vanster_id
    assert mitt_utan[0].alternativ_ut_till_id == hoger_id
    assert mitt_utan[0].marginal_m == pytest.approx(0.04, abs=1e-4)

    # 3D-kantsten med krönhöjd 11,20 m rakt över den högra sadelns dualkant (mellan kol 9 och 10)
    kantsten_hoger = BrytlinjePost(
        typ="kantsten",
        hojd_m=0.0,
        koordinater=((geo.x_min + 10.0, geo.y_max - 2.0), (geo.x_min + 10.0, geo.y_max - 7.0)),
        z_kron_rh2000=(11.20, 11.20),
    )

    # Testar både på basgraf (utan brytlinjer) och på graf byggd med brytlinjen
    graf_med = bygg_sankgraf(z, osak, markslag, geo, brytlinjer=[kantsten_hoger])
    for g in (bas_graf, graf_med):
        instabila_med = detektera_bifurkationer(
            g, osakerhet_m=osak, k_sigma=2.0, brytlinjer=[kantsten_hoger]
        )
        assert all(p.nod_id != mitt_id for p in instabila_med)

        alla_med = detektera_bifurkationer(
            g, osakerhet_m=osak, k_sigma=2.0, inkludera_stabila=True, brytlinjer=[kantsten_hoger]
        )
        mitt_med = next(p for p in alla_med if p.nod_id == mitt_id)
        assert mitt_med.instabil is False
        assert mitt_med.primar_ut_till_id == vanster_id
        assert mitt_med.alternativ_ut_till_id == hoger_id
        assert mitt_med.overfall_hojd_m == pytest.approx(10.50, abs=1e-4)
        assert mitt_med.alternativ_hojd_m == pytest.approx(11.20, abs=1e-4)
        assert mitt_med.marginal_m == pytest.approx(0.70, abs=1e-4)
        assert mitt_med.sakerhetsmarginal > 9.0

    # Även bifiltreringsexporten ska reflektera den höjda sadelkanten (11,20 m i st.f. 10,54 m)
    bf_fil = exportera_bifiltrering_json(
        bas_graf, tmp_path / "bifilt.json", osakerhet_m=osak, brytlinjer=[kantsten_hoger]
    )
    bf = json.loads(bf_fil.read_text(encoding="utf-8"))
    kant_mh = next(
        k
        for k in bf["sadelkanter"]
        if {k["fran_nod"], k["till_nod"]} == {mitt_id, hoger_id}
    )
    assert kant_mh["sadel_hojd_m"] == pytest.approx(11.20, abs=1e-4)


def test_k_b10b_bifurkation_3d_brytlinje_pa_primar_sadel_bytar_utlopp() -> None:
    """En 3D-kantsten över primära sadeln vänder avrinningen till motsatt bassäng."""
    z, osak, markslag, geo = _sadel_terrang_for_brytlinje(delta_z_m=0.04, sigma_m=0.05)
    bas_graf = bygg_sankgraf(z, osak, markslag, geo)

    # 3D-kantsten (krön 11,20 m) rakt över vänstra primärsadeln (x = x_min + 5.0 mellan kol 4 och 5)
    kantsten_vanster = BrytlinjePost(
        typ="kantsten",
        hojd_m=0.0,
        koordinater=((geo.x_min + 5.0, geo.y_max - 2.0), (geo.x_min + 5.0, geo.y_max - 7.0)),
        z_kron_rh2000=(11.20, 11.20),
    )

    graf_med = bygg_sankgraf(z, osak, markslag, geo, brytlinjer=[kantsten_vanster])
    mitt_id = int(graf_med.cell_till_nod[4, 7])
    vanster_id = int(graf_med.cell_till_nod[4, 2])
    hoger_id = int(graf_med.cell_till_nod[4, 12])

    # Utan brytlinje rinner M till V (10,50 m); med 3D-kantsten rinner M till H (10,54 m)
    # och det alternativa utloppet mot V hamnar på krönhöjden 11,20 m (marginal 0,66 m -> stabil).
    p_fran_bas = detektera_bifurkationer(
        bas_graf,
        osakerhet_m=osak,
        k_sigma=2.0,
        inkludera_stabila=True,
        brytlinjer=[kantsten_vanster],
    )
    p_fran_med = detektera_bifurkationer(
        graf_med,
        osakerhet_m=osak,
        k_sigma=2.0,
        inkludera_stabila=True,
        brytlinjer=[kantsten_vanster],
    )
    assert p_fran_bas == p_fran_med

    mitt = next(p for p in p_fran_bas if p.nod_id == mitt_id)
    assert mitt.primar_ut_till_id == hoger_id
    assert mitt.alternativ_ut_till_id == vanster_id
    assert mitt.overfall_hojd_m == pytest.approx(10.54, abs=1e-4)
    assert mitt.alternativ_hojd_m == pytest.approx(11.20, abs=1e-4)
    assert mitt.marginal_m == pytest.approx(0.66, abs=1e-4)
    assert mitt.instabil is False

    # Bakåtkompatibilitet: utelämnad `brytlinjer` == `brytlinjer=None` == `brytlinjer=()`
    p_default = detektera_bifurkationer(bas_graf, osakerhet_m=osak, k_sigma=2.0)
    p_none = detektera_bifurkationer(bas_graf, osakerhet_m=osak, k_sigma=2.0, brytlinjer=None)
    p_tom = detektera_bifurkationer(bas_graf, osakerhet_m=osak, k_sigma=2.0, brytlinjer=())
    assert p_default == p_none == p_tom
