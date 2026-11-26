"""Tester för inkrementell horisontuppdatering i K21 (`dagsljus.horisont`)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import dagsljus.floden_losare as floden_losare_mod
import dagsljus.horisont as horisont_mod
from dagsljus.exempelkvarter import bygg_ruta, byggnads_id, rektangel
from dagsljus.floden_losare import rensa_horisont_cache
from gemensamt.hash import sha256_fil
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.scenario import Andring, Planscenario, skriv_scenario
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import las_geotiff

_PAR = {"hojd_max_m": 20.0, "h_min_grader": 10.0, "azimutriktningar": 32}


def _rekt_poly(x0: float, x1: float, y0: float, y1: float) -> dict[str, object]:
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def _skapa_k1_v2_bas(rot: Path, *, sida_m: float = 100.0) -> tuple[Path, Path, str]:
    """Skapar en K1 v2-ruta (100×100 m, 1 m celler) med ett befintligt hus `hus_bas`."""
    hus = [rektangel(150020.0, 150032.0, 6576020.0, 6576035.0, 12.0, "hus_bas")]
    ruta_mapp = bygg_ruta(rot, 150000.0, 6576000.0, sida_m, hus)
    bas_sha = sha256_fil(ruta_mapp / "recept.json")
    return rot, ruta_mapp, bas_sha


def _skriv_k9(sokvag: Path, scenario_id: str, bas_sha: str, andringar: list[Andring]) -> Path:
    scen = Planscenario(
        scenario_id=scenario_id,
        bas_recept=(bas_sha,),
        andringar=tuple(andringar),
    )
    return skriv_scenario(scen, sokvag)


@pytest.mark.parametrize(
    ("fall", "andringar"),
    [
        (
            "nytt_hus",
            [
                Andring(
                    typ="byggnad_ny",
                    andring_id="hus_nytt",
                    geometri=_rekt_poly(150060.0, 150070.0, 6576060.0, 6576072.0),
                    egenskaper={"hojd_m": 15.0},
                )
            ],
        ),
        (
            "hojt_hus",
            [
                Andring(
                    typ="byggnad_hojd",
                    andring_id="hojd_upp",
                    egenskaper={"byggnads_id": byggnads_id("hus_bas"), "hojd_m": 18.0},
                )
            ],
        ),
        (
            "borttaget_hus",
            [
                Andring(
                    typ="byggnad_bort",
                    andring_id="riv_hus",
                    egenskaper={"byggnads_id": byggnads_id("hus_bas")},
                )
            ],
        ),
    ],
)
def test_horisont_inkrementell_identisk_med_full(
    tmp_path: Path, fall: str, andringar: list[Andring]
) -> None:
    """Nytt hus, höjt hus och rivet hus ger bit-/numerisk identitet mot full horisontberäkning."""
    rensa_horisont_cache()
    bas_rot, ruta_mapp, bas_sha = _skapa_k1_v2_bas(tmp_path / "bas")
    rid = ruta_mapp.name
    k9_fil = _skriv_k9(tmp_path / f"{fall}.k9.json", fall, bas_sha, andringar)

    # 1. Kör före + efter i samma utredning -> efter uppdateras inkrementellt
    fraga_inkr = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": f"inkr-{fall}",
            "scenarier": {
                "fore": {"bas": str(bas_rot)},
                "efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]},
            },
            "analyser": [{"id": "hor", "losare": "dagsljus.horisont", "parametrar": _PAR}],
        },
        tmp_path,
    )
    res_inkr = kor_utredning(fraga_inkr, ResultatCache(tmp_path / "cache_i"), tmp_path / "ut_i")
    jobb_i = {j["scenario"]: j for j in res_inkr.sammanstallning["jobb"]}

    assert jobb_i["fore"]["matvarden"]["inkrementell"] is False
    assert jobb_i["efter"]["matvarden"]["inkrementell"] is True
    assert jobb_i["efter"]["matvarden"]["andrade_celler"] > 0

    # 2. Tvinga full horisontberäkning för efter (rensad cache)
    rensa_horisont_cache()
    fraga_full = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": f"full-{fall}",
            "scenarier": {"efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]}},
            "analyser": [{"id": "hor", "losare": "dagsljus.horisont", "parametrar": _PAR}],
        },
        tmp_path,
    )
    res_full = kor_utredning(fraga_full, ResultatCache(tmp_path / "cache_f"), tmp_path / "ut_f")
    mv_f = res_full.sammanstallning["jobb"][0]["matvarden"]
    assert mv_f["inkrementell"] is False

    mapp_i = tmp_path / "ut_i" / "hor" / "efter" / rid
    mapp_f = tmp_path / "ut_f" / "hor" / "efter" / rid

    h_i = np.load(mapp_i / "horisont.npy").astype(np.float64) / 100.0
    h_f = np.load(mapp_f / "horisont.npy").astype(np.float64) / 100.0
    np.testing.assert_allclose(h_i, h_f, atol=1e-6)

    for tif in ("trunkering.tif", "standardhojd.tif"):
        a_i, _ = las_geotiff(mapp_i / tif)
        a_f, _ = las_geotiff(mapp_f / tif)
        np.testing.assert_allclose(a_i, a_f, atol=1e-6)


def test_horisont_fallback_nar_over_25_procent_andras(tmp_path: Path) -> None:
    """Ändring som täcker >= 25 % av rutan faller tillbaka transparent till full beräkning."""
    rensa_horisont_cache()
    bas_rot, ruta_mapp, bas_sha = _skapa_k1_v2_bas(tmp_path / "bas", sida_m=60.0)
    rid = ruta_mapp.name

    # Stort kvarter 35×35 m = 1225 celler av 3600 (~34 % > 25 %)
    k9_fil = _skriv_k9(
        tmp_path / "stor.k9.json",
        "stor-andring",
        bas_sha,
        [
            Andring(
                typ="byggnad_ny",
                andring_id="jattehus",
                geometri=_rekt_poly(150020.0, 150055.0, 6576020.0, 6576055.0),
                egenskaper={"hojd_m": 15.0},
            )
        ],
    )
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "stor-fallback",
            "scenarier": {
                "fore": {"bas": str(bas_rot)},
                "efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]},
            },
            "analyser": [{"id": "hor", "losare": "dagsljus.horisont", "parametrar": _PAR}],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "cache_s"), tmp_path / "ut_s")
    jobb = {j["scenario"]: j for j in res.sammanstallning["jobb"]}

    assert jobb["efter"]["matvarden"]["inkrementell"] is False
    assert jobb["efter"]["matvarden"]["inkrementell_orsak"] == "for_stor_andring"
    assert jobb["efter"]["matvarden"]["andrade_celler"] >= 0.25 * 3600

    # Jämför mot ren full körning
    rensa_horisont_cache()
    fraga_ref = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "stor-ref",
            "scenarier": {"efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]}},
            "analyser": [{"id": "hor", "losare": "dagsljus.horisont", "parametrar": _PAR}],
        },
        tmp_path,
    )
    kor_utredning(fraga_ref, ResultatCache(tmp_path / "cache_r"), tmp_path / "ut_r")
    h_s = np.load(tmp_path / "ut_s" / "hor" / "efter" / rid / "horisont.npy")
    h_r = np.load(tmp_path / "ut_r" / "hor" / "efter" / rid / "horisont.npy")
    np.testing.assert_array_equal(h_s, h_r)


def test_horisont_inkrementell_speedup_matbar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lokal byggnad på en 120×120-ruta minskar antalet beräknade cell-strålar kraftigt."""
    rensa_horisont_cache()
    bas_rot, ruta_mapp, bas_sha = _skapa_k1_v2_bas(tmp_path / "bas", sida_m=120.0)
    rid = ruta_mapp.name
    k9_fil = _skriv_k9(
        tmp_path / "litet_hus.k9.json",
        "litet-hus",
        bas_sha,
        [
            Andring(
                typ="byggnad_ny",
                andring_id="litet_hus",
                geometri=_rekt_poly(150080.0, 150086.0, 6576080.0, 6576086.0),
                egenskaper={"hojd_m": 10.0},
            )
        ],
    )
    par = {"hojd_max_m": 15.0, "h_min_grader": 15.0, "azimutriktningar": 64}

    # Värm cachen med basen
    fraga_bas = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "speedup-bas",
            "scenarier": {"fore": {"bas": str(bas_rot)}},
            "analyser": [{"id": "hor", "losare": "dagsljus.horisont", "parametrar": par}],
        },
        tmp_path,
    )
    kor_utredning(fraga_bas, ResultatCache(tmp_path / "cache_bas"), tmp_path / "ut_bas")

    # Räkna deterministiskt antalet cell-strålar och ren kärntid (utan disk-I/O från kor_utredning)
    orig_horisont = horisont_mod.horisont_riktning_ruta
    matning: dict[str, float] = {"anrop": 0.0, "cell_stralar": 0.0, "karna_tid_s": 0.0}

    def _raknande_horisont(
        falt: Any,
        phi_rad: float,
        max_avstand_m: float,
        *,
        fonster: tuple[int, int, int, int] | None = None,
        **kw: Any,
    ) -> Any:
        ny, nx = falt.z_m.shape
        r_a, r_b, c_a, c_b = fonster if fonster is not None else (0, ny, 0, nx)
        matning["anrop"] += 1.0
        matning["cell_stralar"] += float((r_b - r_a) * (c_b - c_a))
        t0 = time.perf_counter()
        res = orig_horisont(falt, phi_rad, max_avstand_m, fonster=fonster, **kw)
        matning["karna_tid_s"] += time.perf_counter() - t0
        return res

    monkeypatch.setattr(horisont_mod, "horisont_riktning_ruta", _raknande_horisont)
    monkeypatch.setattr(floden_losare_mod, "horisont_riktning_ruta", _raknande_horisont)

    fraga_efter = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "speedup-efter",
            "scenarier": {"efter": {"bas": str(bas_rot), "k9": [str(k9_fil)]}},
            "analyser": [{"id": "hor", "losare": "dagsljus.horisont", "parametrar": par}],
        },
        tmp_path,
    )

    # 1. Inkrementell körning
    res_i = kor_utredning(fraga_efter, ResultatCache(tmp_path / "cache_i"), tmp_path / "ut_i")
    mv_i = res_i.sammanstallning["jobb"][0]["matvarden"]
    assert mv_i["inkrementell"] is True
    assert mv_i["andrade_celler"] == 36
    cell_stralar_inkr = int(matning["cell_stralar"])
    karna_tid_inkr = matning["karna_tid_s"]

    # 2. Full körning (rensad horisontcache)
    matning["anrop"] = 0.0
    matning["cell_stralar"] = 0.0
    matning["karna_tid_s"] = 0.0
    rensa_horisont_cache()
    res_f = kor_utredning(fraga_efter, ResultatCache(tmp_path / "cache_f"), tmp_path / "ut_f")
    mv_f = res_f.sammanstallning["jobb"][0]["matvarden"]
    assert mv_f["inkrementell"] is False
    cell_stralar_full = int(matning["cell_stralar"])
    karna_tid_full = matning["karna_tid_s"]

    # Deterministisk arbetsminskning (> 80 % färre cell-strålar, normalt ~91 %) + bitidentitet
    assert cell_stralar_full == 120 * 120 * 64
    assert cell_stralar_inkr < 0.25 * cell_stralar_full, (
        f"Inkrementell räknade om {cell_stralar_inkr}/{cell_stralar_full} cell-strålar"
    )
    h_i = np.load(tmp_path / "ut_i" / "hor" / "efter" / rid / "horisont.npy")
    h_f = np.load(tmp_path / "ut_f" / "hor" / "efter" / rid / "horisont.npy")
    np.testing.assert_array_equal(h_i, h_f)

    # Isolerad kärntid styrd av FLODEN_TIDSFAKTOR (undviker I/O-fladder på delade CI-noder)
    tidsfaktor = float(os.environ.get("FLODEN_TIDSFAKTOR", "1"))
    assert karna_tid_inkr < karna_tid_full * tidsfaktor, (
        f"Inkrementell kärntid {karna_tid_inkr:.4f} s >= "
        f"{karna_tid_full:.4f} s * {tidsfaktor:g}"
    )
