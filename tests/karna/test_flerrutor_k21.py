"""Tester för flerrutig K21-körning (`sammanfoga_rutor`) i statisk fyllning & dynamik."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import las_geotiff, skriv_geotiff


def _skapa_flerruts_och_referens_scenario(
    rot: Path, *, skriv_overlapp_i_recept: bool = True
) -> tuple[Path, Path]:
    """Bygger en 2-ruts K1-bas med sänka över rutgränsen och en sammanhängande 1-ruts-referens."""
    dx = 0.5
    overlapp_m = 4.0
    ov_c = round(overlapp_m / dx)  # 8 celler
    inre_b = 40  # 20 m per ruta
    inre_h = 40  # 20 m
    tot_b = 2 * inre_b  # 80 celler (40 m)
    tot_h = inre_h  # 40 celler (20 m)

    bas_b = tot_b + 2 * ov_c
    bas_h = tot_h + 2 * ov_c
    ii, jj = np.meshgrid(np.arange(bas_h), np.arange(bas_b), indexing="ij")
    c_rad = bas_h / 2.0
    c_kol = bas_b / 2.0  # exakt på rutgränsen mellan västra och östra rutan!

    dist = np.maximum(np.abs(ii - c_rad), np.abs(jj - c_kol))
    bas_mark = (10.60 + 0.02 * np.maximum(dist - 10.0, 0.0)).astype(np.float32)
    r_mid = bas_h // 2
    c_mid = bas_b // 2
    # Sänka 16×16 celler centrerad över rutgränsen (8 celler i varje ruta)
    bas_mark[r_mid - 8 : r_mid + 8, c_mid - 8 : c_mid + 8] = np.float32(10.00)
    # Utloppssadel mot öster vid z = 10.45 m
    bas_mark[r_mid - 1 : r_mid + 1, c_mid + 8 : c_mid + 10] = np.float32(10.45)
    for steg, col in enumerate(range(c_mid + 10, bas_b)):
        bas_mark[r_mid - 1 : r_mid + 1, col] = np.float32(10.43 - 0.005 * steg)

    bas_osak = np.full((bas_h, bas_b), 0.02, dtype=np.float32)
    bas_slag = np.full((bas_h, bas_b), 1, dtype=np.uint8)

    x_inre_min = 151000.0
    y_inre_max = 6577020.0

    # 1. Sammanhängande 1-ruts-referens (exakt det inre globala området)
    ref_mapp = rot / "ref_1ruta"
    ref_mapp.mkdir(parents=True)
    ref_mark = bas_mark[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
    ref_osak = bas_osak[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
    ref_slag = bas_slag[ov_c : ov_c + tot_h, ov_c : ov_c + tot_b]
    skriv_geotiff(
        ref_mapp / "mark.tif", ref_mark, x_min=x_inre_min, y_max=y_inre_max, pixelstorlek=dx
    )
    skriv_geotiff(
        ref_mapp / "osakerhet.tif", ref_osak, x_min=x_inre_min, y_max=y_inre_max, pixelstorlek=dx
    )
    skriv_geotiff(
        ref_mapp / "markslag.tif", ref_slag, x_min=x_inre_min, y_max=y_inre_max, pixelstorlek=dx
    )
    (ref_mapp / "recept.json").write_text(json.dumps({"ruta_id": "ref"}), encoding="utf-8")

    # 2. Två angränsande K1-rutor med 4,0 m överlapp på varje sida
    flerrut_mapp = rot / "bas_2rutor"
    r_b = inre_b + 2 * ov_c
    rutor_spec = [
        ("mm_151000_6577000", x_inre_min - overlapp_m, 0),
        ("mm_151020_6577000", x_inre_min + inre_b * dx - overlapp_m, inre_b),
    ]
    for rid, x0, c_start in rutor_spec:
        rm = flerrut_mapp / rid
        rm.mkdir(parents=True)
        skriv_geotiff(
            rm / "mark.tif",
            bas_mark[:, c_start : c_start + r_b],
            x_min=x0,
            y_max=y_inre_max + overlapp_m,
            pixelstorlek=dx,
        )
        skriv_geotiff(
            rm / "osakerhet.tif",
            bas_osak[:, c_start : c_start + r_b],
            x_min=x0,
            y_max=y_inre_max + overlapp_m,
            pixelstorlek=dx,
        )
        skriv_geotiff(
            rm / "markslag.tif",
            bas_slag[:, c_start : c_start + r_b],
            x_min=x0,
            y_max=y_inre_max + overlapp_m,
            pixelstorlek=dx,
        )
        recept: dict[str, object] = {"ruta_id": rid}
        if skriv_overlapp_i_recept:
            recept["overlapp_m"] = overlapp_m
        (rm / "recept.json").write_text(json.dumps(recept), encoding="utf-8")

    return ref_mapp, flerrut_mapp


@pytest.mark.parametrize("skriv_overlapp_i_recept", [True, False])
def test_statisk_fyllning_flerrutor_identisk_med_en_ruta(
    tmp_path: Path, skriv_overlapp_i_recept: bool
) -> None:
    """`vatten.statisk_fyllning` över 2 rutor ger identiska djup och volymbalans (< 1e-12 fel)."""
    ref_mapp, flerrut_mapp = _skapa_flerruts_och_referens_scenario(
        tmp_path, skriv_overlapp_i_recept=skriv_overlapp_i_recept
    )
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "flerrutor-statisk",
            "scenarier": {
                "ref": {"bas": str(ref_mapp)},
                "tva_rutor": {"bas": str(flerrut_mapp)},
            },
            "analyser": [
                {
                    "id": "statisk",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 120.0, "takvatten": False},
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    res = kor_utredning(fraga, cache, tmp_path / "ut")

    jobb = {j["scenario"]: j for j in res.sammanstallning["jobb"]}
    mv_ref = jobb["ref"]["matvarden"]
    mv_2r = jobb["tva_rutor"]["matvarden"]

    assert mv_2r["volymfel_rel"] < 1e-12
    assert mv_2r["lagrat_m3"] == pytest.approx(mv_ref["lagrat_m3"], rel=1e-12, abs=1e-12)
    assert mv_2r["ut_m3"] == pytest.approx(mv_ref["ut_m3"], rel=1e-12, abs=1e-12)
    assert mv_2r["max_djup_m"] == pytest.approx(mv_ref["max_djup_m"], rel=1e-12, abs=1e-12)

    djup_ref, geo_ref = las_geotiff(tmp_path / "ut" / "statisk" / "ref" / "max_djup.tif")
    djup_2r, geo_2r = las_geotiff(tmp_path / "ut" / "statisk" / "tva_rutor" / "max_djup.tif")
    assert geo_2r == geo_ref
    np.testing.assert_array_equal(djup_2r, djup_ref)


def test_dynamik_flerrutor_identisk_med_en_ruta(tmp_path: Path) -> None:
    """`vatten.dynamik` över 2 rutor ger identiska djup, ankomsttider och volymbalans (< 1e-12)."""
    ref_mapp, flerrut_mapp = _skapa_flerruts_och_referens_scenario(tmp_path)
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "flerrutor-dynamik",
            "scenarier": {
                "ref": {"bas": str(ref_mapp)},
                "tva_rutor": {"bas": str(flerrut_mapp)},
            },
            "analyser": [
                {
                    "id": "dyn",
                    "losare": "vatten.dynamik",
                    "parametrar": {
                        "regn_tid_s": [0.0, 600.0],
                        "regn_mm_h": [120.0, 120.0],
                        "takvatten": False,
                    },
                }
            ],
        },
        tmp_path,
    )
    cache = ResultatCache(tmp_path / "cache")
    res = kor_utredning(fraga, cache, tmp_path / "ut")

    jobb = {j["scenario"]: j for j in res.sammanstallning["jobb"]}
    mv_ref = jobb["ref"]["matvarden"]
    mv_2r = jobb["tva_rutor"]["matvarden"]

    assert mv_2r["volymfel_rel"] < 1e-12
    assert mv_2r["in_m3"] == pytest.approx(mv_ref["in_m3"], rel=1e-12, abs=1e-12)
    assert mv_2r["lagrat_m3"] == pytest.approx(mv_ref["lagrat_m3"], rel=1e-12, abs=1e-12)
    assert mv_2r["ut_m3"] == pytest.approx(mv_ref["ut_m3"], rel=1e-12, abs=1e-12)
    assert mv_2r["max_djup_m"] == pytest.approx(mv_ref["max_djup_m"], rel=1e-12, abs=1e-12)

    for filnamn in ("max_djup.tif", "tid_till_0_1m.tif", "tid_till_0_2m.tif"):
        a_ref, geo_ref = las_geotiff(tmp_path / "ut" / "dyn" / "ref" / filnamn)
        a_2r, geo_2r = las_geotiff(tmp_path / "ut" / "dyn" / "tva_rutor" / filnamn)
        assert geo_2r == geo_ref
        np.testing.assert_allclose(a_2r, a_ref, atol=0.0, rtol=0.0, equal_nan=True)
