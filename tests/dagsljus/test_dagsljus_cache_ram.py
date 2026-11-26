# ruff: noqa: E501
"""Deterministiska RAM- och cachebudgettester för dagsljus (`ruta.py` & `floden_losare.py`)."""

from __future__ import annotations

import gc
import weakref
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import dagsljus.floden_losare as fl_mod
import dagsljus.ruta as ruta_mod
from dagsljus.exempelkvarter import bygg_ruta, byggnads_id, rektangel
from dagsljus.floden_losare import (
    STANDARD_MAX_DAGSLJUS_CELLER,
    STANDARD_MAX_HORISONT_CACHE_MB,
    _HorisontCachePost,
    horisont_cache_bytes,
    max_dagsljus_celler,
    max_horisont_cache_bytes,
    max_horisont_cache_mb,
    rensa_horisont_cache,
)
from dagsljus.horisont import Hojdfalt
from dagsljus.ruta import bygg_mosaik, uppskatta_mosaik_form
from gemensamt.hash import sha256_fil
from karna.analyslosare import LosarFel
from karna.geometri import las_ytmodell
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.scenario import Andring, Planscenario, skriv_scenario
from karna.utredning import fraga_fran_dict
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from testsvit.k1_io import las_geotiff

_PAR = {"hojd_max_m": 20.0, "h_min_grader": 10.0, "azimutriktningar": 32}


def _skapa_3x3_rutor(rot: Path, *, sida_m: float = 20.0) -> tuple[Path, tuple[Path, ...]]:
    """Bygger ett 3×3-rutnät (9 K1 v2-rutor à 1 m) där en granne har en standardhöjdsbyggnad."""
    x_orig, y_orig = 150_000.0, 6_576_000.0
    rutor: dict[tuple[int, int], Path] = {}
    for ix in range(3):
        for iy in range(3):
            x0 = x_orig + ix * sida_m
            y0 = y_orig + iy * sida_m
            hus = []
            if (ix, iy) == (1, 1):
                hus.append(rektangel(x0 + 5.0, x0 + 12.0, y0 + 5.0, y0 + 12.0, 14.0, "mitt_hus"))
            elif (ix, iy) == (0, 2):
                hus.append(rektangel(x0 + 4.0, x0 + 10.0, y0 + 4.0, y0 + 10.0, 9.0, "std_hus"))
            mapp = bygg_ruta(rot, x0, y0, sida_m, hus)
            if (ix, iy) == (0, 2):
                # Märk byggnaden som standardhöjd för att även täcka z_utan_standard-vägen.
                std_post = ByggnadsPost(
                    byggnad_id="std_hus",
                    polygon=(
                        (x0 + 4.0, y0 + 4.0),
                        (x0 + 10.0, y0 + 4.0),
                        (x0 + 10.0, y0 + 10.0),
                        (x0 + 4.0, y0 + 10.0),
                    ),
                    takhojd_rh2000=9.0,
                    markhojd_rh2000=0.0,
                    hojd_m=9.0,
                    kalla="test",
                    hojdkalla="standard",
                )
                skriv_byggnader_gpkg(
                    mapp / "byggnader.gpkg",
                    (std_post,),
                    bbox_fallback=(x0, y0, x0 + sida_m, y0 + sida_m),
                )
            rutor[(ix, iy)] = mapp
    egen = rutor[(1, 1)]
    grannar = tuple(m for k, m in sorted(rutor.items()) if k != (1, 1))
    return egen, grannar


def _syntetisk_cachepost(
    tagg: str,
    *,
    sid_px: int,
    antal_k: int,
    med_standard: bool = False,
) -> _HorisontCachePost:
    z = np.zeros((sid_px, sid_px), dtype=np.float64)
    falt = Hojdfalt(z, 1.0, 150_000.0, 6_576_000.0 + sid_px)
    h_rad = np.zeros((antal_k, sid_px, sid_px), dtype=np.float32)
    trunk = np.zeros((sid_px, sid_px), dtype=np.int32)
    falt0 = Hojdfalt(z.copy(), 1.0, 150_000.0, 6_576_000.0 + sid_px) if med_standard else None
    h0_rad = np.zeros_like(h_rad) if med_standard else None
    return _HorisontCachePost(
        dsm_sha256=f"sha-{tagg}",
        falt=falt,
        fonster=(0, sid_px, 0, sid_px),
        antal_riktningar=antal_k,
        max_avstand_m=100.0,
        h_rad=h_rad,
        trunk=trunk,
        trunk_riktningar=frozenset(range(antal_k)),
        giltiga_riktningar=frozenset(range(antal_k)),
        falt_utan_standard=falt0,
        h0_rad=h0_rad,
    )


def test_bygg_mosaik_frigor_tiles_direkt_och_bevarar_resultat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bygg_mosaik` håller högst en `_Tile` åt gången i RAM och ger bitidentisk 3×3-mosaik."""
    egen, grannar = _skapa_3x3_rutor(tmp_path / "rutor", sida_m=20.0)

    # 1. `uppskatta_mosaik_form` läser enbart GeoTIFF-huvuden utan att anropa `_lasa_tile`.
    orig_lasa = ruta_mod._lasa_tile  # noqa: SLF001
    anrop_under_uppskattning = 0

    def _forbjud_lasa(*args: Any, **kwargs: Any) -> Any:
        nonlocal anrop_under_uppskattning
        anrop_under_uppskattning += 1
        return orig_lasa(*args, **kwargs)

    monkeypatch.setattr(ruta_mod, "_lasa_tile", _forbjud_lasa)
    assert uppskatta_mosaik_form(egen, grannar, 1.0) == (60, 60)
    assert uppskatta_mosaik_form(egen, grannar, 2.0) == (30, 30)
    assert anrop_under_uppskattning == 0

    # 2. Spåra levande `_Tile`-objekt under 9-rutors `bygg_mosaik` via weakref.
    levande_refs: list[weakref.ref[Any]] = []
    max_samtidiga_tiles = 0

    def _spion_lasa_tile(mapp: Path, vegetation: bool) -> Any:
        nonlocal max_samtidiga_tiles
        tile = orig_lasa(mapp, vegetation)
        levande_refs.append(weakref.ref(tile))
        antal_nu = sum(1 for r in levande_refs if r() is not None)
        max_samtidiga_tiles = max(max_samtidiga_tiles, antal_nu)
        return tile

    monkeypatch.setattr(ruta_mod, "_lasa_tile", _spion_lasa_tile)
    mosaik = bygg_mosaik(egen, grannar, 1.0, "medel")
    gc.collect()

    assert len(levande_refs) == 9
    assert max_samtidiga_tiles == 1, f"Höll {max_samtidiga_tiles} _Tile samtidigt i RAM (förväntat 1)"
    assert sum(1 for r in levande_refs if r() is not None) == 0

    # 3. Verifiera bitidentisk mosaik, eget fönster och standardhöjdsfält.
    assert mosaik.falt.z_m.shape == (60, 60)
    assert mosaik.fonster == (20, 40, 20, 40)
    assert mosaik.saknade_celler == 0
    assert mosaik.falt_utan_standard is not None
    assert mosaik.standard_byggnader == frozenset({"std_hus"})
    assert mosaik.standard_byggnader_egen_ruta == 0

    # Jämför mittrutan och standardhöjdsrutan mot direkt `las_ytmodell`.
    z_egen, _ = las_ytmodell(egen)
    np.testing.assert_array_equal(mosaik.falt.z_m[20:40, 20:40], z_egen.astype(np.float64))
    np.testing.assert_array_equal(
        mosaik.falt_utan_standard.z_m[20:40, 20:40], z_egen.astype(np.float64)
    )
    # Standardhöjdsbyggnaden finns i nordvästra rutan (rad 0..20, kol 0..20) i `falt` men inte i `falt_utan_standard`.
    assert float(mosaik.falt.z_m[0:20, 0:20].max()) == pytest.approx(9.0)
    assert float(mosaik.falt_utan_standard.z_m[0:20, 0:20].max()) == pytest.approx(0.0)


def test_horisont_cache_miljovariabel_lru_bytebudget_och_overstor_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_HORISONT_CACHE` mäter exakt `nbytes`, evikterar LRU inom budget, hoppar över >budget och stängs av vid 0."""
    rensa_horisont_cache()
    monkeypatch.delenv("FLODEN_MAX_HORISONT_CACHE_MB", raising=False)
    assert max_horisont_cache_mb() == STANDARD_MAX_HORISONT_CACHE_MB == 128.0
    assert max_horisont_cache_bytes() == int(128.0 * 1024 * 1024)

    monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "   ")
    assert max_horisont_cache_mb() == 128.0

    monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "0")
    assert max_horisont_cache_mb() == 0.0
    assert max_horisont_cache_bytes() == 0

    for ogiltigt in ("-1", "-0.1", "abc", "nan", "inf", "-inf"):
        monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", ogiltigt)
        with pytest.raises(LosarFel, match="FLODEN_MAX_HORISONT_CACHE_MB"):
            max_horisont_cache_bytes()

    # Sätt strikt 1 MB bytebudget (= 1 048 576 byte).
    monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "1")
    budget = max_horisont_cache_bytes()
    assert budget == 1024 * 1024

    # Varje post: sid_px=64 (4096 celler), K=22 -> falt(32 KB) + h_rad(360 KB) + trunk(16 KB) = 409 600 byte (~400 KB).
    post_a = _syntetisk_cachepost("a", sid_px=64, antal_k=22)
    post_b = _syntetisk_cachepost("b", sid_px=64, antal_k=22)
    post_c = _syntetisk_cachepost("c", sid_px=64, antal_k=22)
    storlek_en = fl_mod._horisont_post_bytes(post_a)  # noqa: SLF001
    assert storlek_en == (64 * 64 * 8) + (22 * 64 * 64 * 4) + (64 * 64 * 4)
    assert storlek_en == 409_600

    # 1. A + B ryms tillsammans (819 200 <= 1 048 576).
    fl_mod._spara_horisont_i_cache(("a",), ("ra",), ("ga",), post_a, ar_andrad_fran_bas=False)  # noqa: SLF001
    fl_mod._spara_horisont_i_cache(("b",), ("rb",), ("gb",), post_b, ar_andrad_fran_bas=False)  # noqa: SLF001
    assert horisont_cache_bytes() == 2 * storlek_en
    assert len(fl_mod._HORISONT_CACHE) == 2  # noqa: SLF001

    # 2. Läs A -> A blir MRU, B blir LRU!
    assert fl_mod._hamta_exakt_horisont(("a",)) is post_a  # noqa: SLF001

    # 3. Lägg in C -> totalt 1 228 800 > 1 048 576 -> LRU-posten B (och dess index) evikteras!
    fl_mod._spara_horisont_i_cache(("c",), ("rc",), ("gc",), post_c, ar_andrad_fran_bas=False)  # noqa: SLF001
    assert horisont_cache_bytes() == 2 * storlek_en <= budget
    assert fl_mod._hamta_exakt_horisont(("b",)) is None  # noqa: SLF001
    assert ("rb",) not in fl_mod._HORISONT_RECEPT_INDEX  # noqa: SLF001
    assert ("gb",) not in fl_mod._HORISONT_GRID_INDEX  # noqa: SLF001
    assert fl_mod._hamta_exakt_horisont(("a",)) is post_a  # noqa: SLF001
    assert fl_mod._hamta_exakt_horisont(("c",)) is post_c  # noqa: SLF001

    # 4. Enskild post > budget (K=64, 64×64 + standardhöjd -> > 2 MB > 1 MB) cachas ALDRIG.
    post_stor = _syntetisk_cachepost("stor", sid_px=64, antal_k=64, med_standard=True)
    assert fl_mod._horisont_post_bytes(post_stor) > budget  # noqa: SLF001
    fl_mod._spara_horisont_i_cache(("stor",), ("rs",), ("gs",), post_stor, ar_andrad_fran_bas=False)  # noqa: SLF001
    assert fl_mod._hamta_exakt_horisont(("stor",)) is None  # noqa: SLF001
    assert horisont_cache_bytes() == 2 * storlek_en <= budget

    # 5. FLODEN_MAX_HORISONT_CACHE_MB=0 inaktiverar minnescachen helt.
    monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "0")
    assert fl_mod._hamta_exakt_horisont(("a",)) is None  # noqa: SLF001
    fl_mod._spara_horisont_i_cache(("d",), ("rd",), ("gd",), post_a, ar_andrad_fran_bas=False)  # noqa: SLF001
    assert horisont_cache_bytes() == 0
    assert len(fl_mod._HORISONT_CACHE) == 0  # noqa: SLF001

    rensa_horisont_cache()


def test_max_dagsljus_celler_fail_fast_och_gc_collect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FLODEN_MAX_DAGSLJUS_CELLER` valideras strikt, kastar `LosarFel` före pixelallokering och triggar `gc.collect()`."""
    rensa_horisont_cache()
    monkeypatch.delenv("FLODEN_MAX_DAGSLJUS_CELLER", raising=False)
    assert max_dagsljus_celler() == STANDARD_MAX_DAGSLJUS_CELLER == 4_500_000

    for tomt in ("", "   ", "0"):
        monkeypatch.setenv("FLODEN_MAX_DAGSLJUS_CELLER", tomt)
        assert max_dagsljus_celler() == 4_500_000

    monkeypatch.setenv("FLODEN_MAX_DAGSLJUS_CELLER", "75000")
    assert max_dagsljus_celler() == 75_000

    for ogiltigt in ("-1", "-100", "abc", "1.5"):
        monkeypatch.setenv("FLODEN_MAX_DAGSLJUS_CELLER", ogiltigt)
        with pytest.raises(LosarFel, match="FLODEN_MAX_DAGSLJUS_CELLER"):
            max_dagsljus_celler()

    # Bygg en 60×60 m K1 v2-ruta (= 3 600 celler vid 1 m).
    bas = tmp_path / "bas"
    hus = [rektangel(150_020.0, 150_030.0, 6_576_020.0, 6_576_030.0, 12.0, "h1")]
    bygg_ruta(bas, 150_000.0, 6_576_000.0, 60.0, hus)

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "dagsljus-celltak",
            "scenarier": {"bas": {"bas": str(bas)}},
            "analyser": [{"id": "svf", "losare": "dagsljus.svf_mark", "parametrar": _PAR}],
        },
        tmp_path,
    )

    # Fail-fast vid tak 1 000 < 3 600: `bygg_mosaik` får inte ens anropas!
    monkeypatch.setenv("FLODEN_MAX_DAGSLJUS_CELLER", "1000")
    mosaik_anrop = 0
    orig_bygg = fl_mod.bygg_mosaik

    def _spion_bygg(*args: Any, **kwargs: Any) -> Any:
        nonlocal mosaik_anrop
        mosaik_anrop += 1
        return orig_bygg(*args, **kwargs)

    monkeypatch.setattr(fl_mod, "bygg_mosaik", _spion_bygg)
    with pytest.raises(LosarFel) as exc_info:
        kor_utredning(fraga, ResultatCache(tmp_path / "cache_fel"), tmp_path / "ut_fel")
    msg = str(exc_info.value)
    assert "3600 celler" in msg
    assert "1000 celler" in msg
    assert "FLODEN_MAX_DAGSLJUS_CELLER" in msg
    assert mosaik_anrop == 0

    # Höjt tak (>= 3 600) låter körningen gå igenom och anropar `gc.collect()` över tröskeln.
    monkeypatch.setenv("FLODEN_MAX_DAGSLJUS_CELLER", "5000")
    monkeypatch.setattr(fl_mod, "_DAGSLJUS_GC_TROSKEL_CELLER", 1_000)
    gc_anrop: list[int] = []
    orig_collect = gc.collect

    def _spion_gc(*args: Any, **kwargs: Any) -> int:
        gc_anrop.append(1)
        return orig_collect(*args, **kwargs)

    monkeypatch.setattr(fl_mod.gc, "collect", _spion_gc)
    res_ok = kor_utredning(fraga, ResultatCache(tmp_path / "cache_ok"), tmp_path / "ut_ok")
    assert res_ok.sammanstallning["jobb"][0]["matvarden"]["celler"] == 3600
    assert mosaik_anrop == 1
    assert len(gc_anrop) >= 1
    rensa_horisont_cache()


def test_inkrementell_horisont_och_avstangd_cache_ger_identiska_resultat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K21 inkrementell horisont med budget (`1` MB) och helt avstängd cache (`0`) ger identiska resultat (`atol <= 1e-6`)."""
    rensa_horisont_cache()
    bas = tmp_path / "bas"
    hus = [rektangel(150_020.0, 150_030.0, 6_576_020.0, 6_576_030.0, 12.0, "hus_bas")]
    ruta_mapp = bygg_ruta(bas, 150_000.0, 6_576_000.0, 60.0, hus)
    rid = ruta_mapp.name
    bas_sha = sha256_fil(ruta_mapp / "recept.json")

    k9_fil = skriv_scenario(
        Planscenario(
            scenario_id="nytt-hus",
            bas_recept=(bas_sha,),
            andringar=(
                Andring(
                    typ="byggnad_ny",
                    andring_id="hus_nytt",
                    geometri={
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [150_040.0, 6_576_040.0],
                                [150_048.0, 6_576_040.0],
                                [150_048.0, 6_576_048.0],
                                [150_040.0, 6_576_048.0],
                                [150_040.0, 6_576_040.0],
                            ]
                        ],
                    },
                    egenskaper={"hojd_m": 15.0},
                ),
                Andring(
                    typ="byggnad_hojd",
                    andring_id="hojd_upp",
                    egenskaper={"byggnads_id": byggnads_id("hus_bas"), "hojd_m": 16.0},
                ),
            ),
        ),
        tmp_path / "nytt_hus.k9.json",
    )

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "dagsljus-budget-jfr",
            "scenarier": {
                "fore": {"bas": str(bas)},
                "efter": {"bas": str(bas), "k9": [str(k9_fil)]},
            },
            "analyser": [{"id": "hor", "losare": "dagsljus.horisont", "parametrar": _PAR}],
        },
        tmp_path,
    )

    # 1. Med 1 MB budget: 60×60×32 tar ~504 KB -> ryms, efter uppdateras inkrementellt.
    monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "1")
    res_1mb = kor_utredning(fraga, ResultatCache(tmp_path / "cache_1mb"), tmp_path / "ut_1mb")
    jobb_1mb = {j["scenario"]: j for j in res_1mb.sammanstallning["jobb"]}
    assert jobb_1mb["fore"]["matvarden"]["inkrementell"] is False
    assert jobb_1mb["efter"]["matvarden"]["inkrementell"] is True
    assert 0 < horisont_cache_bytes() <= 1024 * 1024

    # 2. Med budget = 0: cachen är helt avstängd, 0 byte i RAM, full beräkning.
    monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "0")
    res_0 = kor_utredning(fraga, ResultatCache(tmp_path / "cache_0"), tmp_path / "ut_0")
    jobb_0 = {j["scenario"]: j for j in res_0.sammanstallning["jobb"]}
    assert jobb_0["efter"]["matvarden"]["inkrementell"] is False
    assert horisont_cache_bytes() == 0

    h_1mb = np.load(tmp_path / "ut_1mb" / "hor" / "efter" / rid / "horisont.npy").astype(np.float64) / 100.0
    h_0 = np.load(tmp_path / "ut_0" / "hor" / "efter" / rid / "horisont.npy").astype(np.float64) / 100.0
    np.testing.assert_allclose(h_1mb, h_0, atol=1e-6)

    for tif in ("trunkering.tif", "standardhojd.tif"):
        a1, _ = las_geotiff(tmp_path / "ut_1mb" / "hor" / "efter" / rid / tif)
        a0, _ = las_geotiff(tmp_path / "ut_0" / "hor" / "efter" / rid / tif)
        np.testing.assert_allclose(a1, a0, atol=1e-6)

    rensa_horisont_cache()
