"""Tester för Svensk Grönytefaktor (GYF) och delfaktoranalys (`src/ekologi/gyf.py`).

Verifierar:
- handräknade analytiska fall inom `1e-12` (ren mark, byggnader, grönt tak,
  genomsläpplig beläggning, K50-överlagringar och tilläggsträd),
- exakt före/efter-jämförelse (`delta_gyf`, `delta_eko_effektiv_area_m2`,
  `saknas_till_mal_m2` och per-delfaktor-deltan),
- inga tysta fel: okända markslagskoder och `NaN`/ogiltiga celler flaggas i
  `flaggad_area_m2`, `okanda_koder` och `varningar`, samt `ValueError` på ogiltiga indata,
- alla 3 svenska kommunprofiler (`stockholm`, `goteborg`, `malmo`),
- deterministisk JSON-serialisering redo för webb-API och rapportpåståenden.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from ekologi.gyf import (
    FORMAT_GYF_JAMFORELSE,
    FORMAT_GYF_RESULTAT,
    K1_MARKSLAG_BERG,
    K1_MARKSLAG_BYGGNAD,
    K1_MARKSLAG_GRAS,
    K1_MARKSLAG_GRONT_TAK,
    K1_MARKSLAG_GRUS,
    K1_MARKSLAG_HARDGJORT,
    K1_MARKSLAG_OKANT,
    K1_MARKSLAG_SKOG,
    K1_MARKSLAG_TAK,
    K1_MARKSLAG_VATTEN,
    GyfDelfaktor,
    GyfProfil,
    GyfTillagg,
    berakna_gyf_fran_raster,
    gyf_jamforelse_till_dict,
    gyf_profil_till_dict,
    gyf_till_dict,
    hamta_gyf_profil,
    jamfor_gyf,
    ladda_gyf_profiler,
)
from ekologi.typer import EkologiFel

TOL = 1e-12


# ------------------------------------------------------------------ kommunprofiler


def test_ladda_alla_tre_kommunprofiler_och_metadata() -> None:
    profiler = ladda_gyf_profiler()
    assert set(profiler) == {"stockholm", "goteborg", "malmo"}

    for pid, profil in profiler.items():
        assert profil.profil_id == pid
        assert profil.status == "skiss"
        assert profil.riktmarke == pytest.approx(0.60, abs=TOL)
        assert profil.kalla.strip()
        assert len(profil.profilfil_sha256) == 64

        kod_karta = profil.k1_kod_till_delfaktor()
        # K1-koderna 1..9 ska vara entydigt mappade; kod 0 (okänt) får ALDRIG mappas.
        assert K1_MARKSLAG_OKANT not in kod_karta
        assert set(kod_karta) == {
            K1_MARKSLAG_HARDGJORT,
            K1_MARKSLAG_TAK,
            K1_MARKSLAG_GRONT_TAK,
            K1_MARKSLAG_GRAS,
            K1_MARKSLAG_SKOG,
            K1_MARKSLAG_BERG,
            K1_MARKSLAG_GRUS,
            K1_MARKSLAG_VATTEN,
            K1_MARKSLAG_BYGGNAD,
        }
        # K50-överlagringarna som nämns i specifikationen måste finnas i varje profil.
        for krav_id in ("gront_tak", "genomslapplig", "vaxtbadd_bjalklag", "regnbadd"):
            assert krav_id in profil.delfaktorer
        assert "trad_krontackning" in profil.tillaggsfaktorer
        assert "smabiotop_regnbadd" in profil.tillaggsfaktorer


def test_alla_tre_kommunprofiler_ger_kanda_skillnader_pa_samma_kvarter() -> None:
    # 10x10 celler à 2.0 m => 4.0 m²/cell, total tomtarea = 400.0 m²:
    # - 40 celler gräs (160 m²)
    # - 20 celler grönt tak (80 m²)
    # - 15 celler grus/genomsläpplig (60 m²)
    # - 25 celler byggnad (100 m²)
    raster = np.full((10, 10), K1_MARKSLAG_BYGGNAD, dtype=np.uint8)
    raster[:4, :] = K1_MARKSLAG_GRAS  # 40 celler
    raster[4:6, :] = K1_MARKSLAG_GRONT_TAK  # 20 celler
    raster[6, :] = K1_MARKSLAG_GRUS  # 10 celler
    raster[7, :5] = K1_MARKSLAG_GRUS  # +5 = 15 celler

    forvantad_gyf = {
        "stockholm": (160.0 * 1.0 + 80.0 * 0.4 + 60.0 * 0.4 + 100.0 * 0.0) / 400.0,
        "goteborg": (160.0 * 0.55 + 80.0 * 0.35 + 60.0 * 0.25 + 100.0 * 0.0) / 400.0,
        "malmo": (160.0 * 1.0 + 80.0 * 0.6 + 60.0 * 0.4 + 100.0 * 0.0) / 400.0,
    }
    for pid, exp in forvantad_gyf.items():
        res = berakna_gyf_fran_raster(raster, cellstorlek_m=2.0, profil=pid)
        assert res.profil_id == pid
        assert res.metodstatus == "skiss"
        assert res.tomt_area_m2 == pytest.approx(400.0, abs=TOL)
        assert res.gyf == pytest.approx(exp, abs=TOL)
        assert res.flaggad_area_m2 == 0.0
        assert res.varningar == ()

    # Malmö (0.58) > Stockholm (0.54) > Göteborg (0.3275) för just detta kvarter.
    assert forvantad_gyf["malmo"] > forvantad_gyf["stockholm"] > forvantad_gyf["goteborg"]


# ------------------------------------------------------------ analytiska fall (1e-12)


def test_analytiskt_ren_vegetation_pa_mark_och_rena_byggnader() -> None:
    cell_m = 0.25  # K1-standardupplösning: 0.0625 m²/cell
    gras = np.full((8, 8), K1_MARKSLAG_GRAS, dtype=np.uint8)  # 64 * 0.0625 = 4.0 m²
    res_gras = berakna_gyf_fran_raster(gras, cellstorlek_m=cell_m, profil="stockholm")
    assert res_gras.tomt_area_m2 == pytest.approx(4.0, abs=TOL)
    assert res_gras.eko_effektiv_area_m2 == pytest.approx(4.0, abs=TOL)
    assert res_gras.gyf == pytest.approx(1.0, abs=TOL)
    assert res_gras.uppfyller_mal is True
    assert res_gras.saknas_till_mal_m2 == pytest.approx(0.0, abs=TOL)
    assert res_gras.varningar == ()

    bygg = np.full((8, 8), K1_MARKSLAG_BYGGNAD, dtype=np.uint8)
    bygg[:2, :] = K1_MARKSLAG_TAK
    bygg[2:4, :] = K1_MARKSLAG_HARDGJORT
    res_bygg = berakna_gyf_fran_raster(bygg, cellstorlek_m=cell_m, profil="stockholm")
    assert res_bygg.tomt_area_m2 == pytest.approx(4.0, abs=TOL)
    assert res_bygg.eko_effektiv_area_m2 == pytest.approx(0.0, abs=TOL)
    assert res_bygg.gyf == pytest.approx(0.0, abs=TOL)
    assert res_bygg.uppfyller_mal is False
    assert res_bygg.saknas_till_mal_m2 == pytest.approx(0.60 * 4.0, abs=TOL)


def test_analytiskt_blandat_kvarter_med_tomtmask_k50_och_tillaggstrad() -> None:
    # 6x10 raster, cellstorlek 0.5 m => 0.25 m²/cell.
    # tomt_mask väljer ut de övre 4x10 = 40 cellerna => tomt_area = 10.0 m².
    # Raderna 4..5 ligger utanför tomt_mask och fylls avsiktligt med okänd kod 0
    # för att verifiera att celler utanför tomtgränsen aldrig påverkar eller varnar.
    raster = np.full((6, 10), K1_MARKSLAG_OKANT, dtype=np.uint8)
    raster[0, :] = K1_MARKSLAG_GRAS  # 10 celler = 2.5 m² (f = 1.0)
    raster[1, :] = K1_MARKSLAG_BYGGNAD  # 10 celler = 2.5 m² -> täcks av gront_tak (f = 0.4)
    raster[2, :] = K1_MARKSLAG_HARDGJORT  # 6 genomslapplig (1.5 m², f=0.4), 4 tät (1.0 m², f=0.0)
    raster[3, :4] = K1_MARKSLAG_VATTEN  # 4 celler = 1.0 m² (f = 1.0)
    raster[3, 4:] = K1_MARKSLAG_HARDGJORT  # 6 celler -> täcks av vaxtbadd_bjalklag (1.5 m², f=0.8)

    tomt_mask = np.zeros((6, 10), dtype=np.bool_)
    tomt_mask[:4, :] = True

    gront_tak = np.zeros((6, 10), dtype=np.bool_)
    gront_tak[1, :] = True

    genomslapplig = np.zeros((6, 10), dtype=np.bool_)
    genomslapplig[2, :6] = True

    vaxtbadd = np.zeros((6, 10), dtype=np.bool_)
    vaxtbadd[3, 4:] = True

    # Tillägg:
    # - trad_krontackning (profilens faktor 0.4 i stockholm): 3.0 m² => 1.2 m² eko
    # - eget projekt-tillägg med explicit faktor 0.25: 2.0 m² => 0.5 m² eko
    tillagg = (
        GyfTillagg(tillagg_id="trad_krontackning", area_m2=3.0),
        GyfTillagg(tillagg_id="fagelholk_biotop", area_m2=2.0, faktor=0.25, namn="Småbiotop"),
    )

    res = berakna_gyf_fran_raster(
        raster,
        cellstorlek_m=0.5,
        profil="stockholm",
        tomt_mask=tomt_mask,
        yt_overlagring={
            "gront_tak": gront_tak,
            "genomslapplig": genomslapplig,
            "vaxtbadd_bjalklag": vaxtbadd,
        },
        tillagg=tillagg,
    )

    exp_grund_eko = 2.5 * 1.0 + 2.5 * 0.4 + 1.5 * 0.4 + 1.0 * 0.0 + 1.0 * 1.0 + 1.5 * 0.8
    # = 2.5 + 1.0 + 0.6 + 0.0 + 1.0 + 1.2 = 6.3 m²
    exp_tillagg_eko = 3.0 * 0.4 + 2.0 * 0.25  # = 1.2 + 0.5 = 1.7 m²
    exp_tot_eko = exp_grund_eko + exp_tillagg_eko  # = 8.0 m²
    exp_gyf = exp_tot_eko / 10.0  # = 0.80

    assert res.tomt_celler == 40
    assert res.tomt_area_m2 == pytest.approx(10.0, abs=TOL)
    assert res.grund_eko_effektiv_area_m2 == pytest.approx(exp_grund_eko, abs=TOL)
    assert res.tillagg_eko_effektiv_area_m2 == pytest.approx(exp_tillagg_eko, abs=TOL)
    assert res.eko_effektiv_area_m2 == pytest.approx(exp_tot_eko, abs=TOL)
    assert res.gyf == pytest.approx(exp_gyf, abs=TOL)
    assert res.uppfyller_mal is True
    assert res.saknas_till_mal_m2 == pytest.approx(0.0, abs=TOL)
    assert res.flaggade_celler == 0
    assert res.okanda_koder == ()
    assert res.varningar == ()
    assert set(res.overlagring_anvand) == {"vaxtbadd_bjalklag", "gront_tak", "genomslapplig"}

    # Areabevarande: summan av alla delfaktorareor == tomt_area_m2 exakt
    sum_area = sum(d.area_m2 for d in res.delfaktorer)
    sum_pct = sum(d.andel_av_tomt_pct for d in res.delfaktorer)
    assert sum_area == pytest.approx(res.tomt_area_m2, abs=TOL)
    assert sum_pct == pytest.approx(100.0, abs=TOL)

    # Kontrollera enskilda delfaktorer
    df_karta = {d.faktor_id: d for d in res.delfaktorer}
    assert df_karta["vegetation_mark"].area_m2 == pytest.approx(2.5, abs=TOL)
    assert df_karta["vegetation_mark"].bidrag_gyf == pytest.approx(0.25, abs=TOL)
    assert df_karta["gront_tak"].area_m2 == pytest.approx(2.5, abs=TOL)
    assert df_karta["gront_tak"].eko_effektiv_area_m2 == pytest.approx(1.0, abs=TOL)
    assert df_karta["genomslapplig"].area_m2 == pytest.approx(1.5, abs=TOL)
    assert df_karta["vaxtbadd_bjalklag"].area_m2 == pytest.approx(1.5, abs=TOL)
    assert df_karta["vattenyta"].area_m2 == pytest.approx(1.0, abs=TOL)
    assert df_karta["hardgjord"].area_m2 == pytest.approx(1.0, abs=TOL)
    assert df_karta["byggnad_tak"].area_m2 == pytest.approx(0.0, abs=TOL)

    assert len(res.tillagg) == 2
    assert res.tillagg[0].eko_effektiv_area_m2 == pytest.approx(1.2, abs=TOL)
    assert res.tillagg[1].eko_effektiv_area_m2 == pytest.approx(0.5, abs=TOL)


def test_overlagring_prioritet_ar_deterministisk_och_varnar_vid_overlapp() -> None:
    # 2x2 raster à 1.0 m = 4.0 m² helt hårdgjord basyta.
    # regnbadd täcker (0,0) och (0,1); gront_tak täcker hela rad 0 och (1,0).
    # Enligt prioritetsordning vinner regnbadd över gront_tak i (0,0) och (0,1):
    # - regnbadd: 2 celler (2.0 m², f = 1.0)
    # - gront_tak: 1 cell (1.0 m², f = 0.4)
    # - hardgjord: 1 cell (1.0 m², f = 0.0)
    bas = np.full((2, 2), K1_MARKSLAG_HARDGJORT, dtype=np.uint8)
    regnbadd = np.array([[True, True], [False, False]], dtype=np.bool_)
    gront_tak = np.array([[True, True], [True, False]], dtype=np.bool_)

    # Testa båda inmatningsordningarna i dict:en -> resultatet måste vara identiskt!
    res_a = berakna_gyf_fran_raster(
        bas,
        cellstorlek_m=1.0,
        profil="stockholm",
        yt_overlagring={"gront_tak": gront_tak, "regnbadd": regnbadd},
    )
    res_b = berakna_gyf_fran_raster(
        bas,
        cellstorlek_m=1.0,
        profil="stockholm",
        yt_overlagring={"regnbadd": regnbadd, "gront_tak": gront_tak},
    )

    assert res_a == res_b
    assert res_a.eko_effektiv_area_m2 == pytest.approx(2.0 * 1.0 + 1.0 * 0.4, abs=TOL)
    assert res_a.gyf == pytest.approx(2.4 / 4.0, abs=TOL)
    assert res_a.uppfyller_mal is True  # exakt 0.60 == riktmärke 0.60
    assert res_a.saknas_till_mal_m2 == pytest.approx(0.0, abs=TOL)
    assert sum(d.area_m2 for d in res_a.delfaktorer) == pytest.approx(4.0, abs=TOL)
    assert len(res_a.varningar) == 1
    assert "Överlappande K50-överlagringar" in res_a.varningar[0]


# ----------------------------------------------------------- före/efter-jämförelse


def test_jamfor_gyf_fore_efter_exakt_delta_och_saknas_till_mal() -> None:
    # Tomt: 10x10 celler à 1.0 m => 100.0 m², riktmärke 0.60 => mål 60.0 m² eko-area.
    # Före: 70 m² gräs (f=1.0) + 30 m² hårdgjort (f=0.0) => E=70.0 m², GYF=0.70 (över mål).
    fore_raster = np.full((10, 10), K1_MARKSLAG_HARDGJORT, dtype=np.uint8)
    fore_raster[:7, :] = K1_MARKSLAG_GRAS

    # Efter: nytt hus tar 40 m² av gräset, varav 20 m² får grönt tak (f=0.4), 10 m² hårdgjort görs
    # genomsläppligt (f=0.4), plus 15 m² nya trädkronor (g=0.4):
    # - gräs: 30 m² * 1.0 = 30.0
    # - grönt tak: 20 m² * 0.4 = 8.0
    # - byggnad (tätt tak): 20 m² * 0.0 = 0.0
    # - genomsläpplig: 10 m² * 0.4 = 4.0
    # - hårdgjort: 20 m² * 0.0 = 0.0
    # Grund eko = 42.0 m², tillägg eko = 15.0 * 0.4 = 6.0 m² => E_efter = 48.0 m², GYF_efter = 0.48.
    # Saknas till mål: 60.0 - 48.0 = 12.0 m².
    efter_raster = np.full((10, 10), K1_MARKSLAG_HARDGJORT, dtype=np.uint8)
    efter_raster[:3, :] = K1_MARKSLAG_GRAS
    efter_raster[3:7, :] = K1_MARKSLAG_BYGGNAD
    efter_raster[7, :] = K1_MARKSLAG_GRUS

    gront_tak = np.zeros((10, 10), dtype=np.bool_)
    gront_tak[3:5, :] = True

    res_fore = berakna_gyf_fran_raster(fore_raster, cellstorlek_m=1.0, profil="stockholm")
    res_efter = berakna_gyf_fran_raster(
        efter_raster,
        cellstorlek_m=1.0,
        profil="stockholm",
        yt_overlagring={"gront_tak": gront_tak},
        tillagg=[GyfTillagg(tillagg_id="trad_krontackning", area_m2=15.0)],
    )

    jamf = jamfor_gyf(res_fore, res_efter)
    assert jamf.profil_id == "stockholm"
    assert jamf.uppfyller_mal_fore is True
    assert jamf.uppfyller_mal_efter is False
    assert jamf.delta_gyf == pytest.approx(0.48 - 0.70, abs=TOL)
    assert jamf.delta_eko_effektiv_area_m2 == pytest.approx(48.0 - 70.0, abs=TOL)
    assert jamf.delta_grund_eko_effektiv_area_m2 == pytest.approx(42.0 - 70.0, abs=TOL)
    assert jamf.delta_tillagg_eko_effektiv_area_m2 == pytest.approx(6.0, abs=TOL)
    assert jamf.delta_tomt_area_m2 == pytest.approx(0.0, abs=TOL)
    assert jamf.saknas_till_mal_fore_m2 == pytest.approx(0.0, abs=TOL)
    assert jamf.saknas_till_mal_m2 == pytest.approx(12.0, abs=TOL)
    assert jamf.varningar == ()

    per = {d.faktor_id: d for d in jamf.per_delfaktor}
    assert per["vegetation_mark"].delta_area_m2 == pytest.approx(-40.0, abs=TOL)
    assert per["vegetation_mark"].delta_eko_effektiv_area_m2 == pytest.approx(-40.0, abs=TOL)
    assert per["gront_tak"].delta_area_m2 == pytest.approx(20.0, abs=TOL)
    assert per["gront_tak"].delta_eko_effektiv_area_m2 == pytest.approx(8.0, abs=TOL)
    assert per["genomslapplig"].delta_area_m2 == pytest.approx(10.0, abs=TOL)
    assert per["genomslapplig"].delta_eko_effektiv_area_m2 == pytest.approx(4.0, abs=TOL)
    assert per["byggnad_tak"].delta_area_m2 == pytest.approx(20.0, abs=TOL)
    assert per["hardgjord"].delta_area_m2 == pytest.approx(-10.0, abs=TOL)
    assert sum(d.delta_eko_effektiv_area_m2 for d in jamf.per_delfaktor) == pytest.approx(
        jamf.delta_grund_eko_effektiv_area_m2, abs=TOL
    )


def test_jamfor_gyf_avvisar_olika_profiler_och_varnar_vid_olika_tomtarea() -> None:
    r = np.full((4, 4), K1_MARKSLAG_GRAS, dtype=np.uint8)
    s = berakna_gyf_fran_raster(r, cellstorlek_m=1.0, profil="stockholm")
    g = berakna_gyf_fran_raster(r, cellstorlek_m=1.0, profil="goteborg")
    with pytest.raises(ValueError, match="olika profiler"):
        jamfor_gyf(s, g)

    r2 = np.full((5, 4), K1_MARKSLAG_GRAS, dtype=np.uint8)
    s2 = berakna_gyf_fran_raster(r2, cellstorlek_m=1.0, profil="stockholm")
    jamf = jamfor_gyf(s, s2)
    assert jamf.delta_tomt_area_m2 == pytest.approx(4.0, abs=TOL)
    assert any("Tomtarean skiljer sig" in v for v in jamf.varningar)


# -------------------------------------------- inga tysta fel & ogiltiga indata


def test_okanda_markslagskoder_flaggas_aldrig_gissade() -> None:
    # 4x5 celler à 1.0 m = 20.0 m²:
    # - 12 celler gräs (12 m², f=1.0)
    # - 3 celler K1-kod 0 (okänt)
    # - 2 celler kod 42 (okänd)
    # - 3 celler kod 255 (okänd)
    raster = np.full((4, 5), K1_MARKSLAG_GRAS, dtype=np.uint8)
    raster[0, :3] = K1_MARKSLAG_OKANT
    raster[1, :2] = 42
    raster[2, :3] = 255

    res = berakna_gyf_fran_raster(raster, cellstorlek_m=1.0, profil="stockholm")
    assert res.tomt_area_m2 == pytest.approx(20.0, abs=TOL)
    assert res.flaggade_celler == 8
    assert res.flaggad_area_m2 == pytest.approx(8.0, abs=TOL)
    assert res.flaggad_andel_pct == pytest.approx(40.0, abs=TOL)
    assert res.okanda_koder == (0, 42, 255)
    assert res.ogiltiga_celler == 0
    assert res.eko_effektiv_area_m2 == pytest.approx(12.0, abs=TOL)
    assert res.gyf == pytest.approx(12.0 / 20.0, abs=TOL)
    # Areabevarande: kända + flaggade == tomtarea exakt
    assert sum(d.area_m2 for d in res.delfaktorer) + res.flaggad_area_m2 == pytest.approx(
        res.tomt_area_m2, abs=TOL
    )
    assert len(res.varningar) == 1
    assert "Okända/omappade markslagskoder" in res.varningar[0]
    assert "0 (okänt: 3 celler)" in res.varningar[0]
    assert "42" in res.varningar[0]
    assert "255" in res.varningar[0]


def test_nan_inf_och_icke_heltal_i_floatraster_flaggas() -> None:
    raster = np.full((2, 4), float(K1_MARKSLAG_GRAS), dtype=np.float64)
    raster[0, 0] = math.nan
    raster[0, 1] = math.inf
    raster[0, 2] = 4.5  # icke-heltal
    raster[0, 3] = 99.0  # okänd heltalskod

    res = berakna_gyf_fran_raster(raster, cellstorlek_m=2.0, profil="malmo")
    # 8 celler * 4.0 m² = 32.0 m²; 4 giltiga gräs (16.0 m²), 3 ogiltiga (12.0 m²), 1 okänd (4.0 m²)
    assert res.tomt_area_m2 == pytest.approx(32.0, abs=TOL)
    assert res.ogiltiga_celler == 3
    assert res.okanda_koder == (99,)
    assert res.flaggade_celler == 4
    assert res.flaggad_area_m2 == pytest.approx(16.0, abs=TOL)
    assert res.eko_effektiv_area_m2 == pytest.approx(16.0, abs=TOL)
    assert res.gyf == pytest.approx(0.5, abs=TOL)
    assert len(res.varningar) == 2
    assert any("NaN eller ogiltigt markslagsvärde" in v for v in res.varningar)
    assert any("Okända/omappade markslagskoder" in v for v in res.varningar)


@pytest.mark.parametrize(
    ("cellstorlek", "match"),
    [
        (0.0, "cellstorlek_m"),
        (-1.0, "cellstorlek_m"),
        (math.nan, "cellstorlek_m"),
        (math.inf, "cellstorlek_m"),
    ],
)
def test_ogiltig_cellstorlek_ger_value_error(cellstorlek: float, match: str) -> None:
    r = np.full((2, 2), K1_MARKSLAG_GRAS, dtype=np.uint8)
    with pytest.raises(ValueError, match=match):
        berakna_gyf_fran_raster(r, cellstorlek_m=cellstorlek, profil="stockholm")


def test_ogiltiga_indata_ger_tydliga_value_error() -> None:
    r = np.full((3, 3), K1_MARKSLAG_GRAS, dtype=np.uint8)

    # Tom tomtmask -> ValueError
    with pytest.raises(ValueError, match="tomtarea måste vara positiv"):
        berakna_gyf_fran_raster(
            r, cellstorlek_m=1.0, profil="stockholm", tomt_mask=np.zeros((3, 3), dtype=np.bool_)
        )

    # Fel form på tomtmask -> ValueError
    with pytest.raises(ValueError, match="2D-mask"):
        berakna_gyf_fran_raster(
            r, cellstorlek_m=1.0, profil="stockholm", tomt_mask=np.ones((2, 2), dtype=np.bool_)
        )

    # 1D eller tomt raster -> ValueError
    with pytest.raises(ValueError, match="2D-raster"):
        berakna_gyf_fran_raster(np.array([4, 4], dtype=np.uint8), cellstorlek_m=1.0)
    with pytest.raises(ValueError, match="2D-raster"):
        berakna_gyf_fran_raster(np.empty((0, 3), dtype=np.uint8), cellstorlek_m=1.0)

    # Okänd profil -> ValueError
    with pytest.raises(ValueError, match="Okänd GYF-profil"):
        berakna_gyf_fran_raster(r, cellstorlek_m=1.0, profil="uppsala_finns_ej")

    # Okänd överlagringsnyckel -> ValueError
    with pytest.raises(ValueError, match="okända nycklar i yt_overlagring"):
        berakna_gyf_fran_raster(
            r,
            cellstorlek_m=1.0,
            profil="stockholm",
            yt_overlagring={"okand_yta": np.ones((3, 3), dtype=np.bool_)},
        )

    # Okänd tilläggsfaktor utan explicit vikt -> ValueError
    with pytest.raises(ValueError, match="okänd tilläggsfaktor"):
        berakna_gyf_fran_raster(
            r,
            cellstorlek_m=1.0,
            profil="stockholm",
            tillagg=[GyfTillagg(tillagg_id="finns_ej", area_m2=5.0)],
        )

    # Negativ tilläggsarea eller faktor -> ValueError
    with pytest.raises(ValueError, match="area_m2"):
        GyfTillagg(tillagg_id="trad_krontackning", area_m2=-1.0)
    with pytest.raises(ValueError, match="faktor"):
        GyfTillagg(tillagg_id="trad_krontackning", area_m2=1.0, faktor=-0.2)

    # EkologiFel är subklass av ValueError
    with pytest.raises(EkologiFel):
        hamta_gyf_profil("saknas")


def test_egen_gyf_profil_och_konfliktkontroll(tmp_path: Path) -> None:
    # Två delfaktorer som delar samma K1-kod måste stoppas
    df1 = GyfDelfaktor(faktor_id="a", namn="A", faktor=1.0, k1_koder=(4,))
    df2 = GyfDelfaktor(faktor_id="b", namn="B", faktor=0.5, k1_koder=(4,))
    with pytest.raises(ValueError, match="K1-kod 4"):
        GyfProfil(
            profil_id="konflikt",
            namn="Konflikt",
            kommun="Test",
            riktmarke=0.6,
            status="skiss",
            version="1",
            beskrivning="",
            kalla="Test",
            kalla_url="",
            delfaktorer={"a": df1, "b": df2},
            tillaggsfaktorer={},
            overlagring_prioritet=("a", "b"),
        )

    # Saknad profilfil -> ValueError
    with pytest.raises(ValueError, match="saknas"):
        ladda_gyf_profiler(tmp_path / "finns_ej.toml")


# --------------------------------------------------- deterministisk JSON-export


def test_deterministisk_json_export_for_resultat_jamforelse_och_profil() -> None:
    fore_r = np.full((4, 4), K1_MARKSLAG_SKOG, dtype=np.uint8)
    efter_r = fore_r.copy()
    efter_r[:2, :] = K1_MARKSLAG_BERG

    fore = berakna_gyf_fran_raster(fore_r, cellstorlek_m=1.0, profil="goteborg")
    efter = berakna_gyf_fran_raster(
        efter_r,
        cellstorlek_m=1.0,
        profil="goteborg",
        tillagg=[GyfTillagg(tillagg_id="smabiotop_regnbadd", area_m2=4.0)],
    )
    jamf = jamfor_gyf(fore, efter)

    res_dict_1 = gyf_till_dict(efter)
    res_dict_2 = gyf_till_dict(efter)
    json_res_1 = json.dumps(res_dict_1, ensure_ascii=False, allow_nan=False)
    json_res_2 = json.dumps(res_dict_2, ensure_ascii=False, allow_nan=False)
    assert json_res_1 == json_res_2
    assert res_dict_1["format"] == FORMAT_GYF_RESULTAT
    assert res_dict_1["koordinatsystem"] == "EPSG:3011"
    assert res_dict_1["metodstatus"] == "skiss"
    assert res_dict_1["gyf"] == pytest.approx(efter.gyf, abs=TOL)

    jamf_dict_1 = gyf_jamforelse_till_dict(jamf)
    jamf_dict_2 = gyf_jamforelse_till_dict(jamf)
    json_jamf_1 = json.dumps(jamf_dict_1, ensure_ascii=False, allow_nan=False)
    json_jamf_2 = json.dumps(jamf_dict_2, ensure_ascii=False, allow_nan=False)
    assert json_jamf_1 == json_jamf_2
    assert jamf_dict_1["format"] == FORMAT_GYF_JAMFORELSE
    assert jamf_dict_1["delta_gyf"] == pytest.approx(jamf.delta_gyf, abs=TOL)
    assert jamf_dict_1["saknas_till_mal_m2"] == pytest.approx(jamf.saknas_till_mal_m2, abs=TOL)

    profil_dict = gyf_profil_till_dict(hamta_gyf_profil("goteborg"))
    json_profil = json.dumps(profil_dict, ensure_ascii=False, allow_nan=False)
    assert '"profil_id": "goteborg"' in json_profil
    assert '"status": "skiss"' in json_profil
