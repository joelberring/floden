"""Deterministiska regressionstester för B1 och B3 i `vatten.losare_2d` (K10-grafen)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

import vatten.losare_2d as losare_2d_mod
from gemensamt.hash import sha256_fil
from karna.graf import Berakningsgraf, Cache
from karna.scenario import Planscenario
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import las_geotiff, skriv_geotiff
from vatten.blaytor import BlaytorFel, k1_filer
from vatten.losare import registrera_bas
from vatten.losare_2d import _kor_2d, nod_2d


def test_b1_losare_2d_gor_endast_en_kopplad_korning_och_avspeglar_rannsten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1: vid aktiv 1D-rännsten körs endast `kor_kopplad_1d_2d` (0 anrop till 2D-okopplad)
    och `max_djup.tif` avspeglar rännstensavledningen fysiskt."""
    a5b_indata = Path("testsvit/A5b_sned_gata_3d/indata")
    indata = k1_filer(a5b_indata)

    anrop_2d = 0
    anrop_1d = 0
    orig_2d = losare_2d_mod.kor_2d_lokal_troghet
    orig_1d = losare_2d_mod.kor_kopplad_1d_2d

    def _rakna_2d(*args: Any, **kwargs: Any) -> Any:
        nonlocal anrop_2d
        anrop_2d += 1
        return orig_2d(*args, **kwargs)

    def _rakna_1d(*args: Any, **kwargs: Any) -> Any:
        nonlocal anrop_1d
        anrop_1d += 1
        return orig_1d(*args, **kwargs)

    monkeypatch.setattr(losare_2d_mod, "kor_2d_lokal_troghet", _rakna_2d)
    monkeypatch.setattr(losare_2d_mod, "kor_kopplad_1d_2d", _rakna_1d)

    ut_med = tmp_path / "med_1d"
    mat_med = _kor_2d(
        indata,
        {
            "regn_tid_s": [0.0, 30.0],
            "regn_mm_h": [180.0, 180.0],
            "slut_tid_s": 45.0,
            "oppna_rander": False,
            "takvatten": False,
            "rannsten_1d": "auto",
        },
        ut_med,
    )
    assert anrop_2d == 0, "Okopplad kor_2d_lokal_troghet får inte köras när 1D-rännsten är aktiv."
    assert anrop_1d == 1, "Endast en kopplad 1D/2D-körning ska göras."
    assert mat_med["rannsten_1d_aktiv"] is True
    assert mat_med["volymfel_rel"] < 1e-11
    assert mat_med["ut_m3"] == pytest.approx(0.0, abs=1e-12)
    assert float(mat_med["volymbalans"]["volymfel_rel"]) < 1e-11
    assert (ut_med / "rannsten_flode.geojson").is_file()
    assert (ut_med / "max_djup.tif").is_file()

    ut_utan = tmp_path / "utan_1d"
    mat_utan = _kor_2d(
        indata,
        {
            "regn_tid_s": [0.0, 30.0],
            "regn_mm_h": [180.0, 180.0],
            "slut_tid_s": 45.0,
            "oppna_rander": False,
            "takvatten": False,
            "rannsten_1d": False,
        },
        ut_utan,
    )
    assert anrop_2d == 1
    assert anrop_1d == 1
    assert mat_utan["rannsten_1d_aktiv"] is False

    # Fysisk skillnad i max_djup.tif: rännstensavledningen omfördelar vattnet tydligt
    djup_med, _ = las_geotiff(ut_med / "max_djup.tif")
    djup_utan, _ = las_geotiff(ut_utan / "max_djup.tif")
    max_diff_m = float(np.max(np.abs(djup_med - djup_utan)))
    assert max_diff_m > 0.005, f"max_djup.tif påverkades inte av 1D-rännsten (diff={max_diff_m})"

    # Explicit rannsten_1d=True med subgrid_faktor > 1 ger tydligt fel
    with pytest.raises(BlaytorFel, match="subgrid_faktor"):
        _kor_2d(
            indata,
            {
                "regn_tid_s": [0.0, 10.0],
                "regn_mm_h": [60.0, 60.0],
                "rannsten_1d": True,
                "subgrid_faktor": 2,
            },
            tmp_path / "fel_subgrid",
        )


def test_b3_losare_2d_laser_byggnader_gpkg_platt_tak_och_radhusgrans(tmp_path: Path) -> None:
    """B3: `vatten.losare_2d` läser `byggnader.gpkg` och bevarar platt tak & radhusgräns."""
    dx = 0.5
    h, b = 36, 44  # 18 m x 22 m
    x_min, y_max = 150000.0, 6580018.0
    # Yttermark 9.0 m, 1-cells vall 10.5 m runt fyra isolerade tråg (9.5 m) på varje sida om husen
    z = np.full((h, b), 9.0, dtype=np.float32)
    z[3:10, 9:35] = np.float32(10.5)
    z[26:33, 9:35] = np.float32(10.5)
    z[9:27, 3:10] = np.float32(10.5)
    z[9:27, 34:41] = np.float32(10.5)
    z[4:10, 10:34] = np.float32(9.5)   # norra tråget
    z[26:32, 10:34] = np.float32(9.5)  # södra tråget
    z[10:26, 4:10] = np.float32(9.5)   # västra tråget
    z[10:26, 34:40] = np.float32(9.5)  # östra tråget

    markslag = np.full((h, b), 1, dtype=np.uint8)
    # Två radhus vägg i vägg: västra (6×8 m, kol 10..21) och östra (6×8 m, kol 22..33), rader 10..25
    # I rastret bildar de en 12×8 m rektangel (långsidor N/S -> okänd/raster skulle ge 50% N, 50% S,
    # 0% V/Ö!). Med byggnader.gpkg har varje radhus sin långsida i N-S (8 m > 6 m) och en brandvägg
    # mot grannen, så västra radhuset avvattnas 100 % västerut och östra 100 % österut!
    markslag[10:26, 10:34] = MARKSLAG_BYGGNAD
    z[10:26, 10:34] = np.float32(20.0)

    k1 = tmp_path / "k1_radhus"
    k1.mkdir()
    skriv_geotiff(k1 / "mark.tif", z, x_min=x_min, y_max=y_max, pixelstorlek=dx)
    skriv_geotiff(k1 / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=dx)
    skriv_geotiff(
        k1 / "osakerhet.tif", np.full_like(z, 0.02), x_min=x_min, y_max=y_max, pixelstorlek=dx
    )
    (k1 / "recept.json").write_text('{"ruta_id": "radhus"}', encoding="utf-8")

    # Skriv byggnader.gpkg med de två radhusen (båda med takform="sadel", delad brandvägg)
    p_vast = (
        (x_min + 5.0, y_max - 13.0),
        (x_min + 11.0, y_max - 13.0),
        (x_min + 11.0, y_max - 5.0),
        (x_min + 5.0, y_max - 5.0),
    )
    p_ost = (
        (x_min + 11.0, y_max - 13.0),
        (x_min + 17.0, y_max - 13.0),
        (x_min + 17.0, y_max - 5.0),
        (x_min + 11.0, y_max - 5.0),
    )
    poster = [
        ByggnadsPost(
            byggnad_id="b_radhus_vast",
            polygon=p_vast,
            takhojd_rh2000=20.0,
            markhojd_rh2000=10.5,
            hojd_m=9.5,
            kalla="test",
            takform="sadel",
        ),
        ByggnadsPost(
            byggnad_id="b_radhus_ost",
            polygon=p_ost,
            takhojd_rh2000=20.0,
            markhojd_rh2000=10.5,
            hojd_m=9.5,
            kalla="test",
            takform="sadel",
        ),
    ]
    skriv_byggnader_gpkg(
        k1 / "byggnader.gpkg", poster, (x_min, y_max - h * dx, x_min + b * dx, y_max)
    )

    bas_sha = sha256_fil(k1 / "recept.json")
    registrera_bas(bas_sha, k1)
    scen = Planscenario("radhus", (bas_sha,))
    nod = nod_2d(
        scen,
        {
            "regn_tid_s": [0.0, 120.0],
            "regn_mm_h": [180.0, 180.0],
            "slut_tid_s": 240.0,
            "oppna_rander": False,
            "takvatten": True,
        },
    )
    # byggnader.gpkg ska finnas med i nodens indata för reproducerbar cachenyckel
    assert "byggnader" in nod.indata
    res = Berakningsgraf(Cache(tmp_path / "cache")).kor(nod)
    mv = res.matvarden
    assert mv["takvatten_antal_byggnader"] == 2
    assert mv["volymfel_rel"] < 1e-11
    assert float(mv["volymbalans"]["volymfel_rel"]) < 1e-11

    djup, _ = las_geotiff(res.katalog / "max_djup.tif")
    cell_a = dx * dx
    v_norr = float(djup[4:10, 10:34].sum()) * cell_a
    v_vast = float(djup[10:26, 4:10].sum()) * cell_a
    v_ost = float(djup[10:26, 34:40].sum()) * cell_a
    # Regndjup = 6 mm = 0.006 m; varje radhus är 48 m² -> 0.288 m³ takvatten till V resp. Ö.
    # Västra och östra tråget har egen area 24 m² (0.144 m³), norra 36 m² (0.216 m³) + liten
    # valldel. Med radhusgränsen får V och Ö > 0.42 m³, medan N bara får sitt eget regn (< 0.26 m³)!
    assert v_vast > 0.42
    assert v_ost == pytest.approx(v_vast, rel=1e-2)
    assert v_norr < 0.26  # inget takvatten till norra kortsidorna!

    # Jämför också med ett platt tak med 40 % invändig avvattning till ledning:
    # ett enda 12×8 m platt tak (96 m², 57.6 m² effektivt -> 0.3456 m³ totalt takvatten) fördelar
    # jämnt runt hela fasadomkretsen (40 m) så gavlarna V/Ö får 8/40 = 20 % = 0.069 m³ vardera,
    # till skillnad från sadel-fallback utan byggnader.gpkg (0 m³ på gavlarna!).
    k1_platt = tmp_path / "k1_platt"
    k1_platt.mkdir()
    for fn in ("mark.tif", "markslag.tif", "osakerhet.tif"):
        (k1_platt / fn).write_bytes((k1 / fn).read_bytes())
    (k1_platt / "recept.json").write_text('{"ruta_id": "platt"}', encoding="utf-8")
    skriv_byggnader_gpkg(
        k1_platt / "byggnader.gpkg",
        [
            ByggnadsPost(
                byggnad_id="b_platt_1",
                polygon=(
                    (x_min + 5.0, y_max - 13.0),
                    (x_min + 17.0, y_max - 13.0),
                    (x_min + 17.0, y_max - 5.0),
                    (x_min + 5.0, y_max - 5.0),
                ),
                takhojd_rh2000=20.0,
                markhojd_rh2000=10.5,
                hojd_m=9.5,
                kalla="test",
                takform="platt",
            )
        ],
        (x_min, y_max - h * dx, x_min + b * dx, y_max),
    )
    ind_platt = k1_filer(k1_platt)
    ind_platt["byggnader"] = k1_platt / "byggnader.gpkg"
    ut_platt = tmp_path / "ut_platt"
    mv_platt = _kor_2d(
        ind_platt,
        {
            "regn_tid_s": [0.0, 120.0],
            "regn_mm_h": [180.0, 180.0],
            "slut_tid_s": 240.0,
            "oppna_rander": False,
            "takvatten": True,
            "takvatten_andel_ledning": 0.4,
        },
        ut_platt,
    )
    assert mv_platt["takvatten_antal_byggnader"] == 1
    assert mv_platt["takvatten_regler"] == {"platt_alla_fasader": 1}
    assert mv_platt["volymfel_rel"] < 1e-11
    djup_platt, _ = las_geotiff(ut_platt / "max_djup.tif")
    v_vast_platt = float(djup_platt[10:26, 4:10].sum()) * cell_a
    v_ost_platt = float(djup_platt[10:26, 34:40].sum()) * cell_a
    # Eget gavelregn + liten vallandel ≈ 0.16 m³; med platt tak tillkommer ~0.069 m³ -> > 0.21 m³
    assert v_vast_platt > 0.21
    assert v_ost_platt == pytest.approx(v_vast_platt, rel=1e-2)
