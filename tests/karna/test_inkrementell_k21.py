"""Tester för inkrementell sänkgraf i K21 (`vatten.statisk_fyllning` och `vatten.dynamik`)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest

from gemensamt.hash import sha256_fil
from karna.inbyggda import rensa_sankgraf_cache
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.scenario import Andring, Planscenario, skriv_scenario
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import las_geotiff, skriv_geotiff


def _skapa_k1_bas_tva_delomraden(mapp: Path) -> tuple[Path, str, float, float]:
    """Skapar en 1-ruts K1-bas (80×120 celler à 0,5 m = 40×60 m) med två skilda avrinningsområden.

    - Väst (kol 0..55): egen sänka med utlopp mot väst.
    - Mitt (kol 56..63): vattendelare med rygg som dränerar mot nord/syd.
    - Öst (kol 64..119): två kaskadkopplade sänkor (övre -> nedre -> östranden).
    """
    mapp.mkdir(parents=True, exist_ok=True)
    hojd, bredd = 80, 120
    dx = 0.5
    x_min = 150000.0
    y_max = 6580040.0

    z = np.full((hojd, bredd), 14.0, dtype=np.float32)
    z[0, :] = 12.5
    z[-1, :] = 12.5
    z[:, 0] = 12.5
    z[:, -1] = 12.5

    # Vattendelare i mitten (kol 56..63)
    for r in range(1, hojd - 1):
        dist_rand = min(r, hojd - 1 - r)
        z[r, 56:64] = np.float32(15.0 + 0.04 * dist_rand)

    # Västra sänkan (oberörd vid ändringar i öst)
    z[20:60, 12:44] = 11.0
    z[26:54, 18:38] = 10.5
    z[38:42, 1:12] = 13.0

    # Östra delområdet: övre och nedre sänka
    z[10:34, 72:104] = 12.0
    z[46:70, 72:104] = 10.0
    z[34:46, 84:92] = 13.2
    z[56:60, 104:119] = 12.8

    osak = np.full((hojd, bredd), 0.03, dtype=np.float32)
    slag = np.full((hojd, bredd), 4, dtype=np.uint8)

    skriv_geotiff(mapp / "mark.tif", z, x_min=x_min, y_max=y_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=x_min, y_max=y_max, pixelstorlek=dx)
    skriv_geotiff(mapp / "markslag.tif", slag, x_min=x_min, y_max=y_max, pixelstorlek=dx)
    recept_fil = mapp / "recept.json"
    recept_fil.write_text(
        json.dumps({"ruta_id": "mm_150000_6580000", "k1_version": 1}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return mapp, sha256_fil(recept_fil), x_min, y_max


def _rekt_poly(x0: float, x1: float, y0: float, y1: float) -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def _skriv_k9(sokvag: Path, scenario_id: str, bas_sha: str, andringar: list[Andring]) -> Path:
    scen = Planscenario(
        scenario_id=scenario_id,
        bas_recept=(bas_sha,),
        andringar=tuple(andringar),
    )
    return skriv_scenario(scen, sokvag)


def test_statisk_fyllning_inkrementell_identisk_med_full(tmp_path: Path) -> None:
    """Inkrementell K21 `vatten.statisk_fyllning` matchar tvingad full omräkning exakt."""
    rensa_sankgraf_cache()
    bas_mapp, bas_sha, x_min, y_max = _skapa_k1_bas_tva_delomraden(tmp_path / "bas")

    # Ny byggnad + markhöjning i övre östra sänkan (kol 76..92, rad 14..26)
    k9_fil = _skriv_k9(
        tmp_path / "efter.k9.json",
        "efter-ost",
        bas_sha,
        [
            Andring(
                typ="markhojd",
                andring_id="fyllnad-ost",
                geometri=_rekt_poly(x_min + 38.0, x_min + 46.0, y_max - 13.0, y_max - 7.0),
                egenskaper={"niva_rh2000": 13.5},
            ),
            Andring(
                typ="byggnad_ny",
                andring_id="hus-ost",
                geometri=_rekt_poly(x_min + 40.0, x_min + 44.0, y_max - 11.5, y_max - 8.5),
                egenskaper={
                    "hojd_m": 8.0,
                    "entre_rh2000": 13.8,
                    "entre_punkt": [x_min + 40.0, y_max - 10.0],
                },
            ),
        ],
    )

    # 1. Kör före + efter i samma utredning -> efter träffar fryst bas-sänkgraf inkrementellt
    fraga_inkr = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "inkr-statisk",
            "scenarier": {
                "fore": {"bas": str(bas_mapp)},
                "efter": {"bas": str(bas_mapp), "k9": [str(k9_fil)]},
            },
            "analyser": [
                {
                    "id": "statisk",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 120.0, "takvatten": True},
                }
            ],
        },
        tmp_path,
    )
    res_inkr = kor_utredning(fraga_inkr, ResultatCache(tmp_path / "cache_inkr"), tmp_path / "ut_i")
    jobb_inkr = {j["scenario"]: j for j in res_inkr.sammanstallning["jobb"]}

    assert jobb_inkr["fore"]["matvarden"]["inkrementell"] is False
    assert jobb_inkr["efter"]["matvarden"]["inkrementell"] is True
    assert jobb_inkr["efter"]["matvarden"]["ateranvanda_noder"] > 0
    assert jobb_inkr["efter"]["matvarden"]["omraknade_celler"] < 80 * 120
    assert jobb_inkr["efter"]["matvarden"]["volymfel_rel"] < 1e-12

    # 2. Tvinga full omräkning genom att rensa sänkgrafcachen och köra enbart efter
    rensa_sankgraf_cache()
    fraga_full = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "full-statisk",
            "scenarier": {
                "efter": {"bas": str(bas_mapp), "k9": [str(k9_fil)]},
            },
            "analyser": [
                {
                    "id": "statisk",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 120.0, "takvatten": True},
                }
            ],
        },
        tmp_path,
    )
    t0_full = time.perf_counter()
    res_full = kor_utredning(fraga_full, ResultatCache(tmp_path / "cache_full"), tmp_path / "ut_f")
    t_full = time.perf_counter() - t0_full
    mv_full = res_full.sammanstallning["jobb"][0]["matvarden"]
    mv_inkr = jobb_inkr["efter"]["matvarden"]

    assert mv_full["inkrementell"] is False
    assert mv_full["volymfel_rel"] < 1e-12
    assert mv_inkr["max_djup_m"] == pytest.approx(mv_full["max_djup_m"], abs=1e-6)
    assert mv_inkr["lagrat_m3"] == pytest.approx(mv_full["lagrat_m3"], rel=1e-10, abs=1e-9)
    assert mv_inkr["ut_m3"] == pytest.approx(mv_full["ut_m3"], rel=1e-10, abs=1e-9)

    djup_i, _ = las_geotiff(tmp_path / "ut_i" / "statisk" / "efter" / "max_djup.tif")
    djup_f, _ = las_geotiff(tmp_path / "ut_f" / "statisk" / "efter" / "max_djup.tif")
    np.testing.assert_allclose(djup_i, djup_f, atol=1e-6)
    assert t_full >= 0.0


def test_dynamik_inkrementell_identisk_med_full(tmp_path: Path) -> None:
    """Inkrementell K21 `vatten.dynamik` matchar full omräkning (djup, tider, volymbalans)."""
    rensa_sankgraf_cache()
    bas_mapp, bas_sha, x_min, y_max = _skapa_k1_bas_tva_delomraden(tmp_path / "bas")

    k9_fil = _skriv_k9(
        tmp_path / "efter_dyn.k9.json",
        "efter-dyn",
        bas_sha,
        [
            Andring(
                typ="byggnad_ny",
                andring_id="hus-dyn",
                geometri=_rekt_poly(x_min + 39.0, x_min + 45.0, y_max - 12.0, y_max - 8.0),
                egenskaper={
                    "hojd_m": 6.0,
                    "entre_rh2000": 13.8,
                    "entre_punkt": [x_min + 39.0, y_max - 10.0],
                },
            ),
        ],
    )

    param = {
        "regn_tid_s": [0.0, 600.0],
        "regn_mm_h": [120.0, 120.0],
        "takvatten": True,
    }
    fraga_inkr = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "inkr-dynamik",
            "scenarier": {
                "fore": {"bas": str(bas_mapp)},
                "efter": {"bas": str(bas_mapp), "k9": [str(k9_fil)]},
            },
            "analyser": [{"id": "dyn", "losare": "vatten.dynamik", "parametrar": param}],
        },
        tmp_path,
    )
    res_inkr = kor_utredning(fraga_inkr, ResultatCache(tmp_path / "cache_i"), tmp_path / "ut_i")
    jobb_i = {j["scenario"]: j for j in res_inkr.sammanstallning["jobb"]}
    assert jobb_i["fore"]["matvarden"]["inkrementell"] is False
    assert jobb_i["efter"]["matvarden"]["inkrementell"] is True
    assert jobb_i["efter"]["matvarden"]["volymfel_rel"] < 1e-12

    rensa_sankgraf_cache()
    fraga_full = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "full-dynamik",
            "scenarier": {"efter": {"bas": str(bas_mapp), "k9": [str(k9_fil)]}},
            "analyser": [{"id": "dyn", "losare": "vatten.dynamik", "parametrar": param}],
        },
        tmp_path,
    )
    res_full = kor_utredning(fraga_full, ResultatCache(tmp_path / "cache_f"), tmp_path / "ut_f")
    mv_f = res_full.sammanstallning["jobb"][0]["matvarden"]
    mv_i = jobb_i["efter"]["matvarden"]

    assert mv_f["inkrementell"] is False
    assert mv_i["max_djup_m"] == pytest.approx(mv_f["max_djup_m"], abs=1e-6)
    assert mv_i["lagrat_m3"] == pytest.approx(mv_f["lagrat_m3"], rel=1e-10, abs=1e-9)
    assert mv_i["ut_m3"] == pytest.approx(mv_f["ut_m3"], rel=1e-10, abs=1e-9)

    for fil in ("max_djup.tif", "tid_till_0_1m.tif", "tid_till_0_2m.tif"):
        a_i, _ = las_geotiff(tmp_path / "ut_i" / "dyn" / "efter" / fil)
        a_f, _ = las_geotiff(tmp_path / "ut_f" / "dyn" / "efter" / fil)
        np.testing.assert_allclose(a_i, a_f, atol=1e-6, equal_nan=True)


def test_fallback_vid_ny_slutsanka(tmp_path: Path) -> None:
    """Säkerhetsventil: en ny sluten innergård (slutsänka) faller tillbaka till full omräkning."""
    rensa_sankgraf_cache()
    bas_mapp, bas_sha, x_min, y_max = _skapa_k1_bas_tva_delomraden(tmp_path / "bas")

    # Bygg en sluten byggnadsring i östra området (4 väggar som stänger in en gård)
    x0, x1 = x_min + 38.0, x_min + 48.0
    y0, y1 = y_max - 15.0, y_max - 5.0
    t = 2.0
    k9_fil = _skriv_k9(
        tmp_path / "slutsanka.k9.json",
        "slutsanka",
        bas_sha,
        [
            Andring(
                typ="byggnad_ny",
                andring_id="vagg-n",
                geometri=_rekt_poly(x0, x1, y1 - t, y1),
                egenskaper={"hojd_m": 8.0, "entre_rh2000": 14.0, "entre_punkt": [x0, y1]},
            ),
            Andring(
                typ="byggnad_ny",
                andring_id="vagg-s",
                geometri=_rekt_poly(x0, x1, y0, y0 + t),
                egenskaper={"hojd_m": 8.0, "entre_rh2000": 14.0, "entre_punkt": [x0, y0]},
            ),
            Andring(
                typ="byggnad_ny",
                andring_id="vagg-v",
                geometri=_rekt_poly(x0, x0 + t, y0, y1),
                egenskaper={"hojd_m": 8.0, "entre_rh2000": 14.0, "entre_punkt": [x0, y0]},
            ),
            Andring(
                typ="byggnad_ny",
                andring_id="vagg-o",
                geometri=_rekt_poly(x1 - t, x1, y0, y1),
                egenskaper={"hojd_m": 8.0, "entre_rh2000": 14.0, "entre_punkt": [x1, y0]},
            ),
        ],
    )

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "slutsanka-fallback",
            "scenarier": {
                "fore": {"bas": str(bas_mapp)},
                "efter": {"bas": str(bas_mapp), "k9": [str(k9_fil)]},
            },
            "analyser": [
                {
                    "id": "statisk",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 100.0, "takvatten": False},
                }
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "cache_s"), tmp_path / "ut_s")
    jobb = {j["scenario"]: j for j in res.sammanstallning["jobb"]}

    assert jobb["efter"]["matvarden"]["inkrementell"] is False
    assert jobb["efter"]["matvarden"]["inkrementell_orsak"] == "ny_slutsanka"
    assert jobb["efter"]["matvarden"]["volymfel_rel"] < 1e-12


def test_fallback_vid_andrade_brytlinjer(tmp_path: Path) -> None:
    """Säkerhetsventil: ändrade brytlinjer (t.ex. ny mur) faller tillbaka till full omräkning."""
    rensa_sankgraf_cache()
    bas_mapp, bas_sha, x_min, y_max = _skapa_k1_bas_tva_delomraden(tmp_path / "bas")

    k9_fil = _skriv_k9(
        tmp_path / "mur.k9.json",
        "mur",
        bas_sha,
        [
            Andring(
                typ="brytlinje_ny",
                andring_id="mur-ost",
                geometri={
                    "type": "LineString",
                    "coordinates": [[x_min + 40.0, y_max - 15.0], [x_min + 40.0, y_max - 8.0]],
                },
                egenskaper={"typ": "mur", "hojd_m": 0.5},
            ),
        ],
    )

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "brytlinje-fallback",
            "scenarier": {
                "fore": {"bas": str(bas_mapp)},
                "efter": {"bas": str(bas_mapp), "k9": [str(k9_fil)]},
            },
            "analyser": [
                {
                    "id": "statisk",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 100.0, "takvatten": False},
                }
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "cache_b"), tmp_path / "ut_b")
    jobb = {j["scenario"]: j for j in res.sammanstallning["jobb"]}

    assert jobb["efter"]["matvarden"]["inkrementell"] is False
    assert jobb["efter"]["matvarden"]["inkrementell_orsak"] == "brytlinjer_andrade"
    assert jobb["efter"]["matvarden"]["volymfel_rel"] < 1e-12


def test_fallback_vid_flerruta(tmp_path: Path) -> None:
    """Säkerhetsventil: flerrutig K21-körning faller alltid tillbaka till full omräkning."""
    rensa_sankgraf_cache()
    bas_rot = tmp_path / "bas_2r"
    dx = 0.5
    for rid, x0 in (("mm_150000_6580000", 150000.0), ("mm_150016_6580000", 150016.0)):
        rm = bas_rot / rid
        rm.mkdir(parents=True)
        z = np.full((32, 32), 10.0, dtype=np.float32)
        z[10:22, 10:22] = 9.5
        skriv_geotiff(rm / "mark.tif", z, x_min=x0, y_max=6580016.0, pixelstorlek=dx)
        skriv_geotiff(
            rm / "osakerhet.tif",
            np.full((32, 32), 0.02, dtype=np.float32),
            x_min=x0,
            y_max=6580016.0,
            pixelstorlek=dx,
        )
        skriv_geotiff(
            rm / "markslag.tif",
            np.full((32, 32), 1, dtype=np.uint8),
            x_min=x0,
            y_max=6580016.0,
            pixelstorlek=dx,
        )
        (rm / "recept.json").write_text(
            json.dumps({"ruta_id": rid, "overlapp_m": 0.0}), encoding="utf-8"
        )

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "flerruta-fallback",
            "scenarier": {"bas": {"bas": str(bas_rot)}},
            "analyser": [
                {
                    "id": "statisk",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 80.0, "takvatten": False},
                }
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "cache_2r"), tmp_path / "ut_2r")
    mv = res.sammanstallning["jobb"][0]["matvarden"]
    assert mv["inkrementell"] is False
    assert mv["inkrementell_orsak"] == "flerruta"
    assert mv["volymfel_rel"] < 1e-12
