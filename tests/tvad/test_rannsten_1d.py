"""Tester för 1D-rännstenslänk kopplad till 2D (`tvad.rannsten_1d`), inkl. A5b."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from testsvit.k1_io import BrytlinjePost, las_gpkg_tabell
from tvad.rannsten_1d import (
    bygg_a5b_sned_gata,
    diskretisera_rannsten_1d,
    exportera_rannsten_flode_geojson,
    exportera_rannsten_flode_gpkg,
    kor_a5b_rotationsinvarians,
    kor_kopplad_1d_2d,
)


def test_a5b_rotationsinvarians_0_20_45_och_volymbalans(tmp_path: Path) -> None:
    """A5b: 0°, 20° och 45° ska överensstämma inom < 5 % med volymfel < 0,1 %."""
    utfall = kor_a5b_rotationsinvarians((0.0, 20.0, 45.0))
    for vinkel, (res, gangtid, _maxdjup, _andel) in utfall.items():
        assert res.volymfel_rel < 1e-3, f"Volymfel {res.volymfel_rel:.4%} vid {vinkel}°"
        assert res.overfall_hog_m3 < 1e-6, f"Överfall över hög kantsten vid {vinkel}°"
        assert res.overfall_sankt_m3 > 0.5, f"Inget överfall via infarten vid {vinkel}°"
        assert math.isfinite(gangtid) and gangtid > 10.0

    _, t0, h0, a0 = utfall[0.0]
    for vinkel in (20.0, 45.0):
        _, tv, hv, av = utfall[vinkel]
        rel_t = abs(tv - t0) / t0
        rel_h = abs(hv - h0) / h0
        rel_a = abs(av - a0) / a0
        assert rel_t < 0.05, f"Gångtid avviker {rel_t:.2%} vid {vinkel}° ({tv:.2f} vs {t0:.2f} s)"
        assert rel_h < 0.05, f"Maxdjup avviker {rel_h:.2%} vid {vinkel}° ({hv:.4f} vs {h0:.4f} m)"
        assert rel_a < 0.05, f"Infart avviker {rel_a:.2%} vid {vinkel}° ({av:.4f} vs {a0:.4f})"

    res_20 = utfall[20.0][0]
    gj_fil = exportera_rannsten_flode_geojson(res_20, tmp_path / "rannsten_flode.geojson")
    gj = json.loads(gj_fil.read_text(encoding="utf-8"))
    assert len(gj["features"]) == res_20.lank.antal

    gpkg_fil = exportera_rannsten_flode_gpkg(res_20, tmp_path / "rannsten_flode.gpkg")
    srs_id, rader = las_gpkg_tabell(gpkg_fil, "rannsten_flode")
    assert srs_id == 3011
    assert len(rader) == res_20.lank.antal


def test_a5_och_a6_med_1d_rannsten_stannar_och_lamnar_vid_infart() -> None:
    """Verifierar A5 (hel 12 cm kantsten: 0 överfall till gård) och A6 (sänkt infart)."""
    mz5, ms5, rm5, geo5, br5 = bygg_a5b_sned_gata(
        0.0, infart_hojd_m=0.12, langd_m=60.0, infart_s0_m=30.0, infart_s1_m=34.0
    )
    br5_hel = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=p.koordinater,
            z_kron_rh2000=p.z_kron_rh2000,
            z_botten_rh2000=p.z_botten_rh2000,
            tvarfall=p.tvarfall,
            kalla="baskarta_3d",
        )
        for p in br5
    ]
    lank5 = diskretisera_rannsten_1d(br5_hel, mz5, ms5, geo5, ds_mal_m=2.0, korbana_sida="vanster")
    res5 = kor_kopplad_1d_2d(
        mz5,
        ms5,
        geo5,
        [0.0, 60.0],
        [1800.0, 1800.0],
        lank5,
        brytlinjer=br5_hel,
        slut_tid_s=100.0,
        oppna_rander=False,
        regn_mask=rm5,
    )
    assert res5.volymfel_rel < 1e-3
    assert float(np.sum(res5.overfall_gangbana_m3)) == 0.0
    assert float(np.max(res5.max_djup_1d_m)) > 0.02

    mz6, ms6, rm6, geo6, br6 = bygg_a5b_sned_gata(
        0.0, infart_hojd_m=0.02, langd_m=60.0, infart_s0_m=30.0, infart_s1_m=34.0
    )
    lank6 = diskretisera_rannsten_1d(br6, mz6, ms6, geo6, ds_mal_m=2.0, korbana_sida="vanster")
    res6 = kor_kopplad_1d_2d(
        mz6,
        ms6,
        geo6,
        [0.0, 60.0],
        [1800.0, 1800.0],
        lank6,
        brytlinjer=br6,
        slut_tid_s=100.0,
        oppna_rander=False,
        regn_mask=rm6,
    )
    assert res6.volymfel_rel < 1e-3
    assert res6.overfall_hog_m3 == 0.0
    assert res6.overfall_sankt_m3 > 0.2
