"""Deterministiska regressionstester för Våg 9: globalt skyfalls-ruturval (`fokus`),
fail-fast celltak (`FLODEN_MAX_SANKGRAF_CELLER`) och cellbudget i `_SANKGRAF_CACHE`.
"""

from __future__ import annotations

import gc
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from karna import inbyggda as inb
from karna.analyslosare import LosarFel
from karna.korplan import kor_utredning, planera
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import las_geotiff, skriv_geotiff


def _skapa_3x3_k1_rutnat(rot: Path, *, ruta_n: int = 20, dx: float = 0.5) -> Path:
    """Bygger ett 3×3-rutnät (9 K1-rutor utan överlapp) med en sänka över (0,0)-(1,0)."""
    bas = rot / "bas_3x3"
    bas.mkdir(parents=True, exist_ok=True)
    sida_m = ruta_n * dx  # 10,0 m per ruta -> 30×30 m totalt
    x_orig, y_orig = 150_000.0, 6_580_000.0

    for ix in range(3):
        for iy in range(3):
            x0 = x_orig + ix * sida_m
            y0 = y_orig + iy * sida_m
            y_max = y0 + sida_m
            rid = f"mm_{int(x0)}_{int(y0)}"
            rm = bas / rid
            rm.mkdir(parents=True)
            xs = x0 + (np.arange(ruta_n, dtype=np.float32) + 0.5) * dx
            ys = y_max - (np.arange(ruta_n, dtype=np.float32) + 0.5) * dx
            xx, yy = np.meshgrid(xs, ys)
            # Lågpunkt nära rutgränsen x = x_orig + sida_m, y = y_orig + 5.0
            cx, cy = x_orig + sida_m, y_orig + 0.5 * sida_m
            z = (10.5 + 0.04 * np.hypot(xx - cx, yy - cy)).astype(np.float32)
            mask_sanka = (np.abs(xx - cx) <= 2.0) & (np.abs(yy - cy) <= 2.0)
            z[mask_sanka] = np.float32(10.0)
            skriv_geotiff(rm / "mark.tif", z, x_min=x0, y_max=y_max, pixelstorlek=dx)
            skriv_geotiff(
                rm / "osakerhet.tif",
                np.full((ruta_n, ruta_n), 0.02, dtype=np.float32),
                x_min=x0,
                y_max=y_max,
                pixelstorlek=dx,
            )
            skriv_geotiff(
                rm / "markslag.tif",
                np.full((ruta_n, ruta_n), 1, dtype=np.uint8),
                x_min=x0,
                y_max=y_max,
                pixelstorlek=dx,
            )
            (rm / "recept.json").write_text(
                json.dumps({"ruta_id": rid, "overlapp_m": 0.0}) + "\n", encoding="utf-8"
            )
    return bas


def test_global_vattenlosare_med_fokus_laddar_bara_overlappande_rutor(tmp_path: Path) -> None:
    """Global vattenlösare med `fokus` väljer bara överlappande rutor (2 av 9, aldrig alla 9)."""
    inb.rensa_sankgraf_cache()
    bas = _skapa_3x3_k1_rutnat(tmp_path)
    cache = ResultatCache(tmp_path / "cache")

    # Fokus över rutgränsen mellan (0,0) [150000..150010] och (1,0) [150010..150020],
    # med rutans mittpunkt i (1,0) (150015) UTANFÖR fokus -> ytöverlapp tar ändå med (1,0)!
    fokus_2_rutor = [150_002.0, 6_580_002.0, 150_011.0, 6_580_008.0]

    for losare, param in (
        ("vatten.statisk_fyllning", {"regn_mm": 60.0, "takvatten": False}),
        (
            "vatten.dynamik",
            {"regn_tid_s": [0.0, 300.0], "regn_mm_h": [60.0, 60.0], "takvatten": False},
        ),
        ("vatten.2d", {"regn_mm": 40.0, "varaktighet_min": 30.0, "takvatten": False}),
    ):
        fraga = fraga_fran_dict(
            {
                "kontrakt": "K20/v1",
                "fraga_id": f"fokus-{losare.replace('.', '-')}",
                "scenarier": {"bas": {"bas": str(bas)}},
                "analyser": [
                    {
                        "id": "skyfall",
                        "losare": losare,
                        "fokus": fokus_2_rutor,
                        "parametrar": param,
                    }
                ],
            },
            tmp_path,
        )
        plan = planera(fraga, cache)
        assert len(plan.jobb) == 1
        jobb = plan.jobb[0]
        assert jobb.ruta is None
        valda_ids = sorted(r.ruta_id for r in jobb.rutor)
        assert valda_ids == ["mm_150000_6580000", "mm_150010_6580000"]
        assert sorted(rid for rid, _ in jobb.nyckel_objekt["rutor"]) == valda_ids

    # Kör statisk fyllning hela vägen: bara 2 rutor (20×40 px = 800 celler) får laddas och skrivas.
    fraga_kor = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "fokus-kor",
            "scenarier": {"bas": {"bas": str(bas)}},
            "analyser": [
                {
                    "id": "skyfall",
                    "losare": "vatten.statisk_fyllning",
                    "fokus": fokus_2_rutor,
                    "parametrar": {"regn_mm": 60.0, "takvatten": False},
                }
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga_kor, cache, tmp_path / "ut_2r")
    djup, geo = las_geotiff(tmp_path / "ut_2r" / "skyfall" / "bas" / "max_djup.tif")
    assert (geo.hojd, geo.bredd) == (20, 40)
    assert djup.shape == (20, 40)
    assert res.sammanstallning["jobb"][0]["matvarden"]["omraknade_celler"] == 800
    assert res.sammanstallning["jobb"][0]["matvarden"]["volymfel_rel"] < 1e-12

    # Utan fokus faller global lösare tillbaka till samtliga 9 rutor.
    fraga_utan = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "utan-fokus",
            "scenarier": {"bas": {"bas": str(bas)}},
            "analyser": [
                {
                    "id": "skyfall",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 60.0, "takvatten": False},
                }
            ],
        },
        tmp_path,
    )
    plan_utan = planera(fraga_utan, cache)
    assert len(plan_utan.jobb[0].rutor) == 9

    # Fokus som inte överlappar någon ruta alls (inkl. bara gränsberöring x_max == x0) ger LosarFel.
    for miss in (
        [150_030.0, 6_580_000.0, 150_040.0, 6_580_010.0],
        [200_000.0, 6_600_000.0, 200_100.0, 6_600_100.0],
    ):
        fraga_miss = fraga_fran_dict(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "miss-fokus",
                "scenarier": {"bas": {"bas": str(bas)}},
                "analyser": [
                    {
                        "id": "skyfall",
                        "losare": "vatten.statisk_fyllning",
                        "fokus": miss,
                        "parametrar": {"regn_mm": 60.0, "takvatten": False},
                    }
                ],
            },
            tmp_path,
        )
        with pytest.raises(LosarFel, match="överlappar ingen K1-ruta"):
            planera(fraga_miss, cache)


def test_max_sankgraf_celler_miljovariabel_och_fail_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FLODEN_MAX_SANKGRAF_CELLER` valideras strikt och ger tydligt `LosarFel` över taket."""
    inb.rensa_sankgraf_cache()
    monkeypatch.delenv("FLODEN_MAX_SANKGRAF_CELLER", raising=False)
    assert inb.max_sankgraf_celler() == inb.STANDARD_MAX_SANKGRAF_CELLER == 4_500_000

    for tomt in ("", "   ", "0"):
        monkeypatch.setenv("FLODEN_MAX_SANKGRAF_CELLER", tomt)
        assert inb.max_sankgraf_celler() == 4_500_000

    monkeypatch.setenv("FLODEN_MAX_SANKGRAF_CELLER", "123456")
    assert inb.max_sankgraf_celler() == 123_456

    for ogiltigt in ("-1", "-500", "abc", "1.5"):
        monkeypatch.setenv("FLODEN_MAX_SANKGRAF_CELLER", ogiltigt)
        with pytest.raises(LosarFel, match="FLODEN_MAX_SANKGRAF_CELLER"):
            inb.max_sankgraf_celler()

    bas = _skapa_3x3_k1_rutnat(tmp_path)  # 9 rutor à 20×20 = 3 600 celler totalt, 800 för 2 rutor
    cache = ResultatCache(tmp_path / "cache")

    # 2 rutor = 800 celler > 500 -> tydligt svenskt LosarFel innan bygg_sankgraf() körs.
    monkeypatch.setenv("FLODEN_MAX_SANKGRAF_CELLER", "500")
    fraga_2r = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "celltak-2r",
            "scenarier": {"bas": {"bas": str(bas)}},
            "analyser": [
                {
                    "id": "skyfall",
                    "losare": "vatten.statisk_fyllning",
                    "fokus": [150_002.0, 6_580_002.0, 150_012.0, 6_580_008.0],
                    "parametrar": {"regn_mm": 60.0, "takvatten": False},
                }
            ],
        },
        tmp_path,
    )
    with pytest.raises(LosarFel) as exc_info:
        kor_utredning(fraga_2r, cache, tmp_path / "ut_fel")
    msg = str(exc_info.value)
    assert "800 celler" in msg
    assert "2 rutor" in msg
    assert "500 celler" in msg
    assert "FLODEN_MAX_SANKGRAF_CELLER" in msg

    # 1 ruta = 400 celler > 300 -> fail-fast även för enstaka stor ruta.
    monkeypatch.setenv("FLODEN_MAX_SANKGRAF_CELLER", "300")
    fraga_1r = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "celltak-1r",
            "scenarier": {"bas": {"bas": str(bas)}},
            "analyser": [
                {
                    "id": "skyfall",
                    "losare": "vatten.statisk_fyllning",
                    "fokus": [150_002.0, 6_580_002.0, 150_008.0, 6_580_008.0],
                    "parametrar": {"regn_mm": 60.0, "takvatten": False},
                }
            ],
        },
        tmp_path,
    )
    with pytest.raises(LosarFel, match="400 celler.*1 ruta.*300 celler"):
        kor_utredning(fraga_1r, cache, tmp_path / "ut_fel_1r")

    # Höjt tak (>= 800) tillåter körningen av 2 rutor och anropar gc.collect() över tröskeln.
    monkeypatch.setenv("FLODEN_MAX_SANKGRAF_CELLER", "1000")
    monkeypatch.setattr(inb, "_SANKGRAF_GC_TROSKEL_CELLER", 500)
    gc_anrop: list[int] = []
    orig_collect = gc.collect

    def _spion_collect(*args: Any, **kwargs: Any) -> int:
        gc_anrop.append(1)
        return orig_collect(*args, **kwargs)

    monkeypatch.setattr(inb.gc, "collect", _spion_collect)
    res_ok = kor_utredning(fraga_2r, cache, tmp_path / "ut_ok")
    assert res_ok.sammanstallning["jobb"][0]["matvarden"]["volymfel_rel"] < 1e-12
    assert len(gc_anrop) >= 1


def test_sankgraf_cache_respekterar_cellbudget(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_SANKGRAF_CACHE` hoppar över för stora grafer och rensar äldsta poster över budget."""
    inb.rensa_sankgraf_cache()
    monkeypatch.setattr(inb, "_SANKGRAF_CACHE_MAX_CELLER", 1_000)

    @dataclass
    class _FalskGraf:
        mark_z: np.ndarray[Any, Any]

    def _post(n: int) -> inb._SankgrafCachePost:
        return inb._SankgrafCachePost(
            graf=_FalskGraf(np.zeros((1, n), dtype=np.float32)),
            brytlinjer=(),
            oppningar=(),
            celler=n,
        )

    # 1. En enskild graf större än hela budgeten (1 200 > 1 000) cachas inte alls.
    inb._spara_sankgraf_i_cache(["stor-h", "stor-r"], _post(1_200))
    assert inb.sankgraf_cache_celler() == 0
    assert inb._hamta_bas_sankgraf(["stor-h", "stor-r"]) is None

    # 2. En graf på 600 celler med två aliasnycklar räknas en gång (600, inte 1 200).
    inb._spara_sankgraf_i_cache(["a-hash", "a-recept"], _post(600))
    assert inb.sankgraf_cache_celler() == 600
    assert inb._hamta_bas_sankgraf(["a-hash"]) is not None
    assert inb._hamta_bas_sankgraf(["a-recept"]) is not None

    # 3. En till graf på 600 celler överskrider 1 000 -> äldsta (a + alla dess alias) rensas bort.
    inb._spara_sankgraf_i_cache(["b-hash", "b-recept"], _post(600))
    assert inb.sankgraf_cache_celler() == 600
    assert inb._hamta_bas_sankgraf(["a-hash", "a-recept"]) is None
    assert inb._hamta_bas_sankgraf(["b-hash"]) is not None

    # 4. Ytterligare en graf på 300 celler ryms tillsammans med b (600 + 300 = 900 <= 1 000).
    inb._spara_sankgraf_i_cache(["c-hash"], _post(300))
    assert inb.sankgraf_cache_celler() == 900
    assert inb._hamta_bas_sankgraf(["b-hash"]) is not None
    assert inb._hamta_bas_sankgraf(["c-hash"]) is not None

    inb.rensa_sankgraf_cache()
