"""Deterministiska regressionstester för B1 i `tvad.rannsten_1d.kor_kopplad_1d_2d`.

Verifierar fysik- och numeriktilläggen enligt `docs/BESLUT.md` (2026-11-23):
- CFL-villkor med `+ u_max` (Courant-tal `u_max * dt / dx <= cfl`).
- `theta`-utjämning (de Almeida 2012) och parametervalidering.
- `takvatten`, `kalltermer` (avtappning + infiltration), `vattenniva_rh2000_m` och `oppna_rander`
  med exakt volymbalans och konvertering till K6-kompatibelt `TvadResultat`.
"""

from __future__ import annotations

import numpy as np
import pytest

from sankgraf.takvatten import TakPolygon, bygg_takvatten
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo
from tvad.kalltermer import Avtappning, Infiltration2D, Kalltermer
from tvad.lokal_troghet import MARKSLAG_VATTEN
from tvad.rannsten_1d import (
    RannstenFel,
    bygg_a5b_sned_gata,
    diskretisera_rannsten_1d,
    kor_kopplad_1d_2d,
)


def _liten_gata_med_hus_och_kanal(
    *,
    med_vatten: bool = False,
) -> tuple[
    np.ndarray[tuple[int, int], np.dtype[np.float32]],
    np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    GeoTransformInfo,
    list[BrytlinjePost],
    list[TakPolygon],
]:
    """24×16 m lutande gata (1 m celler) med byggnad, 3D-kantsten och valfri vattenyta."""
    h, b = 16, 24
    dx = 1.0
    x_min, y_max = 150000.0, 6580016.0
    geo = GeoTransformInfo(
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        bredd=b,
        hojd=h,
        epsg=3011,
        dtype="float32",
    )
    # Lutning mot öster (2 %) och tvärfall ned mot kantstenen vid rad 8 (y = y_max - 8)
    xx = np.arange(b, dtype=np.float64)[None, :]
    yy = np.arange(h, dtype=np.float64)[:, None]
    z = (12.0 - 0.02 * xx + 0.025 * np.abs(yy - 7.5)).astype(np.float32)
    # Gångbana söder om kantstenen (rader 8..15) något lägre så sänkt infart rinner ut dit
    z[8:, :] = (11.96 - 0.02 * xx - 0.005 * (yy[8:, :] - 8.0)).astype(np.float32)
    markslag = np.full((h, b), 1, dtype=np.uint8)
    markslag[8:, :] = 4  # gräs/gångbana söder om kantstenen

    # Byggnad i norra delen (rader 1..4, kol 4..10)
    markslag[1:5, 4:10] = MARKSLAG_BYGGNAD
    z[1:5, 4:10] = np.float32(20.0)
    poly = (
        (x_min + 4.0, y_max - 5.0),
        (x_min + 10.0, y_max - 5.0),
        (x_min + 10.0, y_max - 1.0),
        (x_min + 4.0, y_max - 1.0),
    )
    byggnader = [TakPolygon(byggnad_id="b_hus1", polygon=poly, takform="platt")]

    if med_vatten:
        markslag[:, 20:] = MARKSLAG_VATTEN
        z[:, 20:] = np.float32(11.20)

    y_kant = y_max - 8.0
    brytlinjer = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min + 1.0, y_kant), (x_min + 12.0, y_kant)),
            z_kron_rh2000=(12.10, 11.88),
            z_botten_rh2000=(11.98, 11.76),
            tvarfall=0.025,
            kalla="baskarta_3d",
        ),
        BrytlinjePost(
            typ="sankt_kantsten",
            hojd_m=0.02,
            koordinater=((x_min + 12.0, y_kant), (x_min + 15.0, y_kant)),
            z_kron_rh2000=(11.78, 11.72),
            z_botten_rh2000=(11.76, 11.70),
            tvarfall=0.025,
            kalla="baskarta_3d",
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min + 15.0, y_kant), (x_min + 19.0, y_kant)),
            z_kron_rh2000=(11.82, 11.74),
            z_botten_rh2000=(11.70, 11.62),
            tvarfall=0.025,
            kalla="baskarta_3d",
        ),
    ]
    return z, markslag, geo, brytlinjer, byggnader


@pytest.mark.parametrize("cfl", [0.5, 0.3])
def test_kopplad_1d_2d_cfl_med_u_max_haller_courantgrans(cfl: float) -> None:
    """CFL-villkoret med `+ u_max` garanterar `u_max * dt / dx <= cfl` i alla tidssteg."""
    mz, ms, rm, geo, br = bygg_a5b_sned_gata(
        0.0, langd_m=40.0, lutning_langs=0.03, infart_s0_m=24.0, infart_s1_m=28.0
    )
    lank = diskretisera_rannsten_1d(br, mz, ms, geo, ds_mal_m=2.0, korbana_sida="vanster")
    res = kor_kopplad_1d_2d(
        mz,
        ms,
        geo,
        [0.0, 40.0],
        [600.0, 600.0],
        lank,
        brytlinjer=br,
        slut_tid_s=50.0,
        cfl=cfl,
        dt_max_s=10.0,
        oppna_rander=False,
        regn_mask=rm,
    )
    assert res.max_hastighet_2d_m_s is not None
    assert float(np.max(res.max_hastighet_2d_m_s)) > 0.2
    assert res.courant_hast_max > 0.02
    assert res.courant_hast_max <= cfl + 1e-12
    assert res.volymfel_rel < 1e-11
    # Många CFL-styrda steg (inte begränsat av dt_max_s = 10 s)
    assert res.antal_steg > 50


def test_kopplad_1d_2d_med_takvatten_kalltermer_och_theta_massbalans() -> None:
    """`takvatten`, `kalltermer` (avtappning + infiltration) och `theta` ger exakt volymbalans."""
    z, ms, geo, br, byggnader = _liten_gata_med_hus_och_kanal(med_vatten=False)
    tak = bygg_takvatten(z, ms, geo, byggnader=byggnader, andel_ledning=0.2)
    lank = diskretisera_rannsten_1d(
        br, z, ms, geo, ds_mal_m=1.5, korbana_sida="vanster", oppen_nedstroms=False
    )
    kallt = Kalltermer(
        avtappning=Avtappning.likformig(z.shape, 20.0),
        infiltration=Infiltration2D.fran_markslag(ms, "sand", initial_mm=1.0),
    )

    res = kor_kopplad_1d_2d(
        z,
        ms,
        geo,
        [0.0, 120.0],
        [120.0, 120.0],
        lank,
        brytlinjer=br,
        slut_tid_s=150.0,
        oppna_rander=False,
        theta=0.9,
        kalltermer=kallt,
        takvatten=tak,
    )
    assert res.volymfel_rel < 1e-12
    assert res.utflode_2d_m3 == pytest.approx(0.0, abs=1e-12)
    assert res.utflode_1d_m3 == pytest.approx(0.0, abs=1e-12)
    assert res.avtappat_m3 > 0.0
    assert res.infiltrerat_m3 > 0.0
    assert res.initial_forlust_m3 > 0.0
    assert res.takvatten is True
    assert res.takvatten_till_ledning_m3 == pytest.approx(
        tak.ledning_area_m2 * (120.0 / 3600.0 * 120.0 / 1000.0), rel=1e-12
    )
    bal = res.volymbalans()
    assert float(bal["volymfel_rel"]) < 1e-12
    assert float(bal["regn_totalt_m3"]) == pytest.approx(
        float(z.size) * 1.0 * (120.0 / 3600.0 * 120.0 / 1000.0), rel=1e-12
    )

    # Konvertering till TvadResultat ger konsistenta K6-fält
    tv = res.till_tvad_resultat()
    assert tv.niva == "full2d"
    assert tv.volymfel_rel < 1e-12
    assert float(tv.volymbalans()["volymfel_rel"]) < 1e-12
    assert tv.lagrat_m3 == pytest.approx(res.lagrat_2d_m3 + res.lagrat_1d_m3, rel=1e-12)
    assert tv.max_hastighet_m_s.shape == z.shape
    assert tv.fara_m2_s.shape == z.shape
    assert tv.tid_till_0_1m_s.shape == z.shape
    assert tv.tid_till_0_2m_s.shape == z.shape

    # Ogiltigt theta ska ge tydligt fel
    with pytest.raises(RannstenFel, match="theta"):
        kor_kopplad_1d_2d(z, ms, geo, [0.0, 10.0], [10.0, 10.0], lank, theta=0.0)


def test_kopplad_1d_2d_med_vattenniva_rh2000_och_oppna_rander() -> None:
    """`vattenniva_rh2000_m` dämmer/dränerar vattenyta (markslag 8) med exakt massbalans."""
    z, ms, geo, br, _ = _liten_gata_med_hus_och_kanal(med_vatten=True)
    lank = diskretisera_rannsten_1d(
        br, z, ms, geo, ds_mal_m=1.5, korbana_sida="vanster", oppen_nedstroms=False
    )

    # Högvattenstånd +11.80 m RH 2000 (0.60 m i kanalen, högre än låga gatan vid kol 18..19)
    res_hog = kor_kopplad_1d_2d(
        z,
        ms,
        geo,
        [0.0, 60.0],
        [0.0, 0.0],
        lank,
        brytlinjer=br,
        oppna_rander=False,
        vattenniva_rh2000_m=11.80,
    )
    assert res_hog.volymfel_rel < 1e-11
    assert float(np.mean(res_hog.slut_djup_2d_m[:, 20:])) == pytest.approx(0.60, abs=1e-5)
    # Vattnet ska ha trängt in på den låga gatan närmast kanalen
    assert float(np.max(res_hog.max_djup_2d_m[:, 16:20])) > 0.05

    # Utan högvatten dränerar vattenytan bort inkommande regnavrinning
    res_dran = kor_kopplad_1d_2d(
        z,
        ms,
        geo,
        [0.0, 60.0],
        [100.0, 100.0],
        lank,
        brytlinjer=br,
        oppna_rander=False,
        vattenniva_rh2000_m=None,
    )
    assert res_dran.volymfel_rel < 1e-11
    assert float(np.max(res_dran.slut_djup_2d_m[:, 20:])) == 0.0
    assert res_dran.utflode_2d_m3 > 0.0
