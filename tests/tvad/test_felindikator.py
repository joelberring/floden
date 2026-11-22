"""Tester för automatisk a posteriori 2D-felindikator (`src/tvad/felindikator.py`)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from sankgraf.fyllning import kor_statisk_k1_mapp
from sankgraf.hierarki import bygg_sankgraf
from testsvit.ea_fall import ea_test8a_urbant_regn
from tvad.felindikator import berakna_2d_felindikator
from tvad.hybrid import jamfor_hybrid_mot_full2d, kor_hybrid_2d_graf
from tvad.lokal_troghet import kor_2d_lokal_troghet

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_MAPP = REPO_ROT / "testsvit"


def test_a1_djup_sanka_rekommenderar_noll_procent_2d() -> None:
    """A1 (djup stillastående sänka utan överfall) ska få låg indikator och 0 % 2D."""
    graf, stat = kor_statisk_k1_mapp(TESTSVIT_MAPP / "A1_enkel_sanka" / "indata", regn_m=0.05)
    ind = berakna_2d_felindikator(
        graf.mark_z,
        graf.markslag,
        graf.geo,
        graf,
        stat,
        topp_regn_mm_h=36.0,
        varaktighet_s=3600.0,
    )
    assert ind.antal_noder_2d == 0
    assert ind.andel_2d_yta == 0.0
    assert not np.any(ind.rekommenderad_2d_mask)
    for nod_ind in ind.nod_indikator.values():
        assert nod_ind.kraver_2d is False
        assert nod_ind.poang < 1.0


def test_a4_lutande_plan_rekommenderar_2d_langs_flodesstraket() -> None:
    """A4 (lutande plan med ytavrinning) ska få hög indikator och rekommendera 2D."""
    graf, stat = kor_statisk_k1_mapp(TESTSVIT_MAPP / "A4_lutande_plan" / "indata", regn_m=0.05)
    ind = berakna_2d_felindikator(
        graf.mark_z,
        graf.markslag,
        graf.geo,
        graf,
        stat,
        topp_regn_mm_h=72.0,
        varaktighet_s=3600.0,
    )
    assert ind.andel_2d_yta > 0.5
    assert np.any(ind.rekommenderad_2d_mask)


def test_automatisk_2d_zonvaljare_i_hybrid_pa_a2() -> None:
    """Valfri `automatisk_2d_zonvaljare=True` i `kor_hybrid_2d_graf` bevarar massbalans."""
    graf, _ = kor_statisk_k1_mapp(TESTSVIT_MAPP / "A2_tva_sankor_serie" / "indata", regn_m=0.05)
    dyn = kor_dynamik_handelsestyrd(graf, [0.0, 600.0], [72.0, 72.0])
    ind = berakna_2d_felindikator(graf.mark_z, graf.markslag, graf.geo, graf, dyn)
    res_auto = kor_hybrid_2d_graf(
        graf.mark_z,
        graf.markslag,
        graf.geo,
        [0.0, 600.0],
        [72.0, 72.0],
        graf=graf,
        automatisk_2d_zonvaljare=True,
        felindikator=ind,
    )
    assert res_auto.volymfel_rel < 1e-3
    assert np.all(np.isfinite(res_auto.max_djup_m[np.isfinite(graf.mark_z)]))


def test_ea8a_felindikator_och_autohybrid_mot_full_2d() -> None:
    """På EA8A ska felindikatorn flagga genomspolningsnoderna och ge hög IoU / låg MAE."""
    g = ea_test8a_urbant_regn()
    osak = np.zeros_like(g.mark_z, dtype=np.float32)
    graf = bygg_sankgraf(g.mark_z, osak, g.markslag, g.geo)
    dyn = kor_dynamik_handelsestyrd(
        graf, g.regn_tid_s, g.regn_intensitet_mm_h, slut_tid_s=g.slut_tid_s
    )
    ind = berakna_2d_felindikator(
        g.mark_z,
        g.markslag,
        g.geo,
        graf,
        dyn,
        topp_regn_mm_h=max(g.regn_intensitet_mm_h),
    )
    assert ind.andel_2d_yta > 0.8
    assert ind.antal_noder_2d == ind.antal_noder_totalt

    res_full = kor_2d_lokal_troghet(
        g.mark_z,
        g.markslag,
        g.geo,
        g.regn_tid_s,
        g.regn_intensitet_mm_h,
        slut_tid_s=g.slut_tid_s,
        gles_front=True,
    )
    res_hyb = kor_hybrid_2d_graf(
        g.mark_z,
        g.markslag,
        g.geo,
        g.regn_tid_s,
        g.regn_intensitet_mm_h,
        slut_tid_s=g.slut_tid_s,
        graf=graf,
        subgrid_faktor=2,
        automatisk_2d_zonvaljare=True,
        felindikator=ind,
    )
    j = jamfor_hybrid_mot_full2d(res_full, res_hyb)
    assert j.maxdjupsfel_mae_m < 0.02
    assert j.traffad_yta_iou > 0.90
    assert j.volymfel_hybrid_rel < 1e-3
