"""Tester för monoton intervallanalys i grafdynamiken (`src/dynamik/intervall.py`)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from dynamik.intervall import kor_monoton_intervallanalys
from dynamik.nodmodell import EntrePunkt
from gemensamt.koordinater import EPSG_INTERN
from sankgraf.bifurkation import detektera_bifurkationer
from sankgraf.fyllning import kor_statisk_k1_mapp
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import GeoTransformInfo

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_MAPP = REPO_ROT / "testsvit"


@pytest.mark.parametrize(
    "mappnamn",
    [
        "A1_enkel_sanka",
        "A2_tva_sankor_serie",
        "A3_tva_sankor_sammanslagning",
        "A4_lutande_plan",
    ],
)
def test_intervall_innesluter_50_slumpade_scenarier_pa_a1_a4(mappnamn: str) -> None:
    """50 slumpade scenarier inom [c_lo, c_hi] x [f_lo, f_hi] x [d_lo, d_hi] måste inneslutas."""
    graf, _ = kor_statisk_k1_mapp(TESTSVIT_MAPP / mappnamn / "indata", regn_m=0.05)

    regn_tid_s = [0.0, 600.0, 1800.0, 3600.0]
    regn_intens = [40.0, 90.0, 30.0, 0.0]
    utdata_tider = [float(t) for t in range(0, 3601, 300)]

    c_iv = (0.8, 1.3)
    f_iv = (0.0, 5.0)
    d_iv = (0.0, 10.0)

    iv_res = kor_monoton_intervallanalys(
        graf,
        regn_tid_s,
        regn_intens,
        klimatfaktor_intervall=c_iv,
        initial_forlust_mm_intervall=f_iv,
        avtappning_mm_h_intervall=d_iv,
        utdata_tider_s=utdata_tider,
    )
    assert iv_res.antal_korningar == 2
    assert iv_res.max_volymfel_rel < 1e-3
    assert all(n.monoton_garanterad for n in iv_res.noder.values())

    rng = np.random.default_rng(42)
    for _ in range(50):
        c = float(rng.uniform(*c_iv))
        f = float(rng.uniform(*f_iv))
        d = float(rng.uniform(*d_iv))
        scen = kor_dynamik_handelsestyrd(
            graf,
            regn_tid_s,
            [x * c for x in regn_intens],
            utdata_tider_s=utdata_tider,
            initial_forlust_mm=f,
            avtappning_mm_h=d if d > 0.0 else None,
        )
        for nid, nod_iv in iv_res.noder.items():
            h_scen = scen.noder[nid].niva_rh2000
            for t_idx, h_val in enumerate(h_scen):
                assert nod_iv.min_niva_rh2000_t[t_idx] - 1e-6 <= h_val
                assert h_val <= nod_iv.max_niva_rh2000_t[t_idx] + 1e-6


def test_bifurkationspaverkad_nod_flaggas_och_grenanalys_tacker_bada_utfallen() -> None:
    """En bifurkationspåverkad nod får monoton_garanterad=False och unionen täcker båda grenarna."""
    h, w = 9, 15
    z = np.full((h, w), 12.0, dtype=np.float32)
    z[2:7, 1:4] = 9.0
    z[4, 0] = 9.8
    z[2:7, 5:10] = 10.0
    z[2:7, 11:14] = 9.0
    z[4, 14] = 9.8
    z[4, 4] = 10.50
    z[4, 10] = 10.54  # Δz = 0.04 m inom 2σ för σ = 0.05 m

    osak = np.full((h, w), 0.05, dtype=np.float32)
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
    graf = bygg_sankgraf(z, osak, markslag, geo)
    mitt_id = int(graf.cell_till_nod[4, 7])
    vanster_id = int(graf.cell_till_nod[4, 2])
    hoger_id = int(graf.cell_till_nod[4, 12])

    bifs = detektera_bifurkationer(graf, osakerhet_m=osak, k_sigma=2.0)
    assert any(b.nod_id == mitt_id and b.instabil for b in bifs)

    regn_tid_s = [0.0, 1800.0]
    regn_intens = [120.0, 120.0]
    utdata_tider = [0.0, 600.0, 1200.0, 1800.0]

    entre = EntrePunkt(
        entre_id="skola_hoger",
        e_m=geo.x_min + 12.5,
        n_m=geo.y_max - 4.5,
        gransdjup_m=0.10,
    )
    iv_res = kor_monoton_intervallanalys(
        graf,
        regn_tid_s,
        regn_intens,
        klimatfaktor_intervall=(0.9, 1.1),
        bifurkationer=bifs,
        entreer=[entre],
        utdata_tider_s=utdata_tider,
    )
    assert len(iv_res.entreer) == 1
    assert iv_res.entreer[0].monoton_garanterad is False
    assert iv_res.entreer[0].min_maxdjup_m <= iv_res.entreer[0].max_maxdjup_m
    assert iv_res.antal_bifurkationsgrenar >= 1
    assert iv_res.max_volymfel_rel < 1e-3
    assert iv_res.noder[mitt_id].monoton_garanterad is False
    assert iv_res.noder[vanster_id].monoton_garanterad is False
    assert iv_res.noder[hoger_id].monoton_garanterad is False
    assert iv_res.noder[hoger_id].bifurkationspaverkad is True

    # Perturbera terrängen så högra sadeln (10.46 m) blir lägre än vänstra (10.50 m)
    z_alt = z.copy()
    z_alt[4, 10] = 10.46
    graf_alt = bygg_sankgraf(z_alt, osak, markslag, geo)
    hoger_id_alt = int(graf_alt.cell_till_nod[4, 12])
    res_alt = kor_dynamik_handelsestyrd(
        graf_alt,
        regn_tid_s,
        regn_intens,
        utdata_tider_s=utdata_tider,
    )
    max_djup_alt_hoger = max(
        0.0,
        float(np.max(res_alt.noder[hoger_id_alt].niva_rh2000))
        - float(graf_alt.noder[hoger_id_alt].botten_rh2000),
    )
    assert (
        iv_res.noder[hoger_id].min_maxdjup_m - 1e-5
        <= max_djup_alt_hoger
        <= iv_res.noder[hoger_id].max_maxdjup_m + 1e-5
    )
