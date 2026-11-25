"""Utökade solstudier (P1-2): tidsfönster, sammanhängande soltid, klockslags-skuggmask och fasadsol.

Verifierar vektoriserade funktioner i `dagsljus.matt` samt K21-lösarna i `dagsljus.floden_losare`
mot analytiska fall (plan yta och axelparallell skuggvägg).
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from dagsljus.floden_losare import (
    SammanhangandeSoltimmarMark,
    SkuggmaskKlockslag,
    SoltimmarFasad,
    SoltimmarMark,
)
from dagsljus.matt import (
    MattFel,
    SolsynlighetAckumulator,
    sammanhangande_soltimmar_fran_synlighet,
    sammanhangande_soltimmar_mark,
    skuggmask_vid_klockslag,
    soltimmar_fasad,
    soltimmar_fran_profiler,
    tidsfonster_mask,
)
from dagsljus.solposition import dygnsfonster_utc, solposition
from karna.analyslosare import LosarFel, LosarKontext, hamta_losare
from karna.rutscenario import RutScenario, _las_ruta
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from testsvit.k1_io import skriv_geotiff

# Stockholm (ungefärligt centrum i WGS84).
LAT_STHLM = 59.33
LON_STHLM = 18.07


def _dag_sol(
    datum: str = "2026-03-20", steg_min: float = 10.0
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    tider = dygnsfonster_utc(datum, LON_STHLM, steg_min)
    return solposition(tider, LAT_STHLM, LON_STHLM)


def _skapa_k1_v2_ruta(mapp: Path, *, med_byggnad: bool = True) -> Path:
    """Skapar en enkel K1 v2-ruta (40 × 40 m, 2 m celler) med plan mark och ev. en skuggvägg."""
    mapp.mkdir(parents=True, exist_ok=True)
    cell = 2.0
    x_min, y_max = 150000.0, 6580080.0
    n = 20  # 40 m × 40 m
    terrang = np.full((n, n), 10.0, dtype=np.float32)
    mark = terrang.copy()
    markslag = np.full((n, n), 4, dtype=np.uint8)
    poster: list[ByggnadsPost] = []
    if med_byggnad:
        # En öst-västlig byggnad (vägg) i rader 8..11 (N = 6580056..6580064), höjd 12 m.
        mark[8:12, 4:16] = 22.0
        markslag[8:12, 4:16] = 9
        x0, x1 = x_min + 4 * cell, x_min + 16 * cell
        y1, y0 = y_max - 8 * cell, y_max - 12 * cell
        poster.append(
            ByggnadsPost(
                byggnad_id="vagg_1",
                polygon=((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
                takhojd_rh2000=22.0,
                markhojd_rh2000=10.0,
                hojd_m=12.0,
                kalla="syntetisk",
                hojdkalla="laser_klass6",
                osakerhet_hojd_m=0.2,
            )
        )
    skriv_geotiff(mapp / "mark.tif", mark, x_min=x_min, y_max=y_max, pixelstorlek=cell)
    skriv_geotiff(mapp / "terrang.tif", terrang, x_min=x_min, y_max=y_max, pixelstorlek=cell)
    skriv_geotiff(mapp / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=cell)
    skriv_geotiff(
        mapp / "osakerhet.tif",
        np.full((n, n), 0.1, dtype=np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=cell,
    )
    bbox = (x_min, y_max - n * cell, x_min + n * cell, y_max)
    skriv_byggnader_gpkg(mapp / "byggnader.gpkg", poster, bbox)
    (mapp / "recept.json").write_text(
        json.dumps({"format": "k1/v2", "k1_version": 2}) + "\n", encoding="utf-8"
    )
    return mapp


# ------------------------------------------------------------------ 1. Tidsfönster (brukstid)


def test_tidsfonster_mask_brukstid_och_validering() -> None:
    mask = tidsfonster_mask(10.0, 9.0, 17.0)
    assert mask.shape == (144,)
    assert int(mask.sum()) == 48  # 8 h × 6 steg/h
    # Plan yta vid vårdagjämning: solen är uppe hela 09–17 -> exakt 8,0 h.
    sol_h, sol_az = _dag_sol("2026-03-20", 10.0)
    h_plan = np.zeros((180, 2, 2), dtype=np.float64)
    timmar_brukstid = soltimmar_fran_profiler(h_plan, sol_h, sol_az, 10.0 / 60.0, tidsmask=mask)
    np.testing.assert_allclose(timmar_brukstid, 8.0, atol=1e-9)
    # Smalare fönster 10–14 ger exakt 4,0 h.
    mask_10_14 = tidsfonster_mask(10.0, 10.0, 14.0)
    timmar_4h = soltimmar_fran_profiler(h_plan, sol_h, sol_az, 10.0 / 60.0, tidsmask=mask_10_14)
    np.testing.assert_allclose(timmar_4h, 4.0, atol=1e-9)
    with pytest.raises(MattFel):
        tidsfonster_mask(10.0, 17.0, 9.0)
    with pytest.raises(MattFel):
        tidsfonster_mask(10.0, -1.0, 12.0)


# ------------------------------------------------------------------ 2. Sammanhängande solperiod


def test_sammanhangande_soltimmar_plan_yta_och_delad_dag() -> None:
    sol_h, sol_az = _dag_sol("2026-03-20", 10.0)
    steg_h = 10.0 / 60.0
    k = 180
    # Cell 0: plan yta (h = 0). Cell 1: smal pelare i söder (azimut 170..190 grader, höjd 60 grader)
    # som delar dagen i två perioder (förmiddag + eftermiddag).
    h_k = np.zeros((k, 2), dtype=np.float64)
    phi_deg = np.arange(k) * (360.0 / k)
    pelare = (phi_deg >= 170.0) & (phi_deg <= 190.0)
    h_k[pelare, 1] = math.radians(60.0)

    tot = soltimmar_fran_profiler(h_k, sol_h, sol_az, steg_h)
    sam = sammanhangande_soltimmar_mark(h_k, sol_h, sol_az, steg_h)

    # Plan yta: sammanhängande == totala soltimmar (~12 h vid dagjämning).
    assert sam[0] == pytest.approx(tot[0], abs=1e-9)
    assert 11.5 <= sam[0] <= 12.5
    # Delad dag: sammanhängande period är strikt kortare än totalen (ungefär hälften).
    assert sam[1] < tot[1] - 3.0
    assert 4.0 <= sam[1] <= 7.0

    # Strömmande ackumulator ger exakt samma svar som direkt vektoriserad funktion.
    ack = SolsynlighetAckumulator(k, sol_h, sol_az, (2,))
    for i in range(k):
        ack.mata(i, h_k[i] if i in ack.aktiva_riktningar() else None)
    sam_ack = sammanhangande_soltimmar_fran_synlighet(ack.synlighet(), steg_h)
    np.testing.assert_allclose(sam_ack, sam, atol=1e-12)

    # Med brukstidsfönster 09–17 kan sammanhängande soltid aldrig överstiga 8,0 h.
    mask = tidsfonster_mask(10.0, 9.0, 17.0)
    sam_9_17 = sammanhangande_soltimmar_mark(h_k, sol_h, sol_az, steg_h, tidsmask=mask)
    assert sam_9_17[0] == pytest.approx(8.0, abs=1e-9)
    assert sam_9_17[1] < 4.5


# ------------------------------------------------------------------ 3. Klockslags-skuggmask


def test_skuggmask_vid_klockslag_analytisk_skuggvagg() -> None:
    k = 180
    # Tre punkter norr om en lång öst-västlig vägg (höjd 10 m) på avstånden 5 m, 20 m, 100 m.
    # Vid sann solmiddag vid vårdagjämning i Stockholm är solhöjden ≈ 90° - 59,33° = 30,67°.
    # atan(10/5) = 63,4° (> 30,7° -> skugga)
    # atan(10/20) = 26,6° (< 30,7° -> sol)
    # atan(10/100) = 5,7° (< 30,7° -> sol)
    avstand = np.array([5.0, 20.0, 100.0])
    phi = np.arange(k) * (2.0 * math.pi / k)
    # Vägg söderut (-cos phi > 0, pi/2 < phi < 3pi/2): h(phi) = atan(H * (-cos phi) / d)
    soder = -np.cos(phi)
    h_k = np.zeros((k, 3), dtype=np.float64)
    for j, d in enumerate(avstand):
        h_k[:, j] = np.where(soder > 0.0, np.arctan(10.0 * soder / d), 0.0)

    # Klockslag 09, 12, 15, 22 (natt) i lokal medelsoltid vid vårdagjämning.
    bas_s = np.datetime64("2026-03-20", "s").astype(np.int64)
    offset_h = LON_STHLM / 15.0
    tider = np.array(
        [
            np.datetime64(0, "s") + np.timedelta64(int(round(bas_s + (h - offset_h) * 3600.0)), "s")
            for h in (9.0, 12.0, 15.0, 22.0)
        ]
    )
    sol_h, sol_az = solposition(tider, LAT_STHLM, LON_STHLM)

    mask = skuggmask_vid_klockslag(h_k, sol_h, sol_az)
    assert mask.dtype == np.uint8
    assert mask.shape == (4, 3)
    # Kl. 12: 5 m skugga (0), 20 m sol (1), 100 m sol (1).
    assert mask[1].tolist() == [0, 1, 1]
    # Kl. 22 (natt): alla 0.
    assert mask[3].tolist() == [0, 0, 0]
    # Skalärt anrop för kl. 12 ger samma resultat med form (3,).
    mask_12 = skuggmask_vid_klockslag(h_k, float(sol_h[1]), float(sol_az[1]))
    assert mask_12.shape == (3,)
    assert mask_12.tolist() == [0, 1, 1]


# ---------------------------------------------------------------- 4. Soltimmar på K11-fasadpunkter


def test_soltimmar_fasad_analytiska_vaderstreck_och_motstaende_vagg() -> None:
    sol_h, sol_az = _dag_sol("2026-03-20", 10.0)
    steg_h = 10.0 / 60.0
    kf = 90
    # Fyra oskymda fasader: Norr (0), Öster (pi/2), Söder (pi), Väster (3pi/2).
    normaler = np.array([0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0])
    h_fri = np.zeros((4, kf), dtype=np.float64)

    timmar = soltimmar_fasad(h_fri, normaler, sol_h, sol_az, steg_h)
    assert timmar.shape == (4,)
    # Norrfasad vid vårdagjämning: solen befinner sig alltid på södra himmelshalvan -> 0 h.
    assert timmar[0] == pytest.approx(0.0, abs=1e-9)
    # Söderfasad: solen i södra halvplanet hela dagen -> ~12 h.
    assert 11.5 <= timmar[2] <= 12.5
    # Öster- och västerfasad delar dagen ungefär lika (~6 h var), summa == söderfasad.
    assert 5.5 <= timmar[1] <= 6.5
    assert 5.5 <= timmar[3] <= 6.5
    assert timmar[1] + timmar[3] == pytest.approx(timmar[2], abs=steg_h + 1e-9)

    # Hel 360-graders horisontprofil ger samma resultat som halvplansprofil.
    h_hel = np.zeros((180, 4), dtype=np.float64)
    timmar_hel = soltimmar_fasad(h_hel, normaler, sol_h, sol_az, steg_h, hel_horisont=True)
    np.testing.assert_allclose(timmar_hel, timmar, atol=1e-9)

    # Motstående hög skuggvägg (45°) framför söderfasaden blockerar hela vårdagjämningen
    # eftersom max solhöjd i Stockholm 20 mars är ~30,7° < 45°.
    h_skymd = np.full((1, kf), math.radians(45.0), dtype=np.float64)
    assert soltimmar_fasad(h_skymd, np.array([math.pi]), sol_h, sol_az, steg_h)[0] == 0.0


# ------------------------------------------------------------------ 5. K21-lösare end-to-end


def test_k21_losare_utokade_solstudier_kor_och_registreras(tmp_path: Path) -> None:
    for namn in (
        "dagsljus.soltimmar_mark",
        "dagsljus.sammanhangande_soltimmar_mark",
        "dagsljus.skuggmask_vid_klockslag",
        "dagsljus.soltimmar_fasad",
    ):
        assert hamta_losare(namn).namn == namn

    ruta_mapp = _skapa_k1_v2_ruta(tmp_path / "ruta", med_byggnad=True)
    ruta = _las_ruta(ruta_mapp, "r0")
    sc = RutScenario(id="test", nyckel="k", rutor=(ruta,))

    # 1) SoltimmarMark med brukstid 09–17.
    los_sol = SoltimmarMark()
    p_sol = los_sol.validera_parametrar(
        {
            "datum": ["2026-03-20"],
            "start_timme": 9.0,
            "slut_timme": 17.0,
            "cellstorlek_m": 2.0,
            "azimutriktningar": 90,
        }
    )
    ut_sol = los_sol.kor(LosarKontext(tmp_path / "ut_sol", sc, p_sol, {}, {}, ruta=ruta))
    assert ut_sol.matvarden["soltimmar_max_h"] <= 8.0 + 1e-6
    assert ut_sol.matvarden["soltimmar_min_h"] < ut_sol.matvarden["soltimmar_max_h"]

    # 2) SammanhangandeSoltimmarMark med brukstid 09–17.
    los_sam = SammanhangandeSoltimmarMark()
    p_sam = los_sam.validera_parametrar(
        {
            "datum": ["2026-03-20"],
            "start_timme": 9.0,
            "slut_timme": 17.0,
            "cellstorlek_m": 2.0,
            "azimutriktningar": 90,
        }
    )
    ut_sam = los_sam.kor(LosarKontext(tmp_path / "ut_sam", sc, p_sam, {}, {}, ruta=ruta))
    assert (tmp_path / "ut_sam" / "sammanhangande_soltimmar.tif").is_file()
    assert ut_sam.matvarden["sammanhangande_max_h"] <= ut_sol.matvarden["soltimmar_max_h"] + 1e-6

    # 3) SkuggmaskKlockslag för kl. 09, 12, 15, 18.
    los_sk = SkuggmaskKlockslag()
    p_sk = los_sk.validera_parametrar(
        {
            "datum": "2026-03-20",
            "klockslag": [9.0, 12.0, 15.0, 18.0],
            "cellstorlek_m": 2.0,
            "azimutriktningar": 90,
        }
    )
    ut_sk = los_sk.kor(LosarKontext(tmp_path / "ut_sk", sc, p_sk, {}, {}, ruta=ruta))
    mask = np.load(tmp_path / "ut_sk" / "skuggmask.npy")
    assert mask.dtype == np.uint8 and mask.shape[0] == 4
    assert set(np.unique(mask).tolist()) <= {0, 1}
    # Kl. 12: skuggväggen ger både solbelysta och skuggade celler.
    assert mask[1].min() == 0 and mask[1].max() == 1
    assert ut_sk.matvarden["antal_klockslag"] == 4

    # 4) SoltimmarFasad på K11-punkter från byggnad `vagg_1`.
    los_fas = SoltimmarFasad()
    p_fas = los_fas.validera_parametrar(
        {"datum": ["2026-03-20"], "cellstorlek_m": 2.0, "azimutriktningar": 90, "fasad_steg_m": 8.0}
    )
    ut_fas = los_fas.kor(LosarKontext(tmp_path / "ut_fas", sc, p_fas, {}, {}, ruta=ruta))
    assert ut_fas.matvarden["fasadpunkter"] > 0
    with (tmp_path / "ut_fas" / "soltimmar_fasad.csv").open(encoding="utf-8", newline="") as fh:
        rader = list(csv.DictReader(fh))
    assert rader
    soder_rader = [r for r in rader if abs(float(r["normal_azimut_grader"]) - 180.0) < 5.0]
    norr_rader = [
        r
        for r in rader
        if float(r["normal_azimut_grader"]) < 5.0 or float(r["normal_azimut_grader"]) > 355.0
    ]
    assert soder_rader and norr_rader
    assert float(soder_rader[0]["soltimmar_h"]) > 10.0
    assert float(norr_rader[0]["soltimmar_h"]) == pytest.approx(0.0, abs=1e-4)

    # Ogiltigt tidsfönster ger LosarFel.
    with pytest.raises(LosarFel):
        los_sol.validera_parametrar({"start_timme": 18.0, "slut_timme": 9.0})
