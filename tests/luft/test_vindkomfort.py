# SPDX-License-Identifier: MIT
"""Vindkomfort: exakt överskridande, klassning enligt NEN 8100/Lawson, före/efter."""

from __future__ import annotations

import numpy as np
import pytest

from luft.geometri import voxelisera
from luft.typer import Inflode, LuftFel, Vindfalt
from luft.vindbibliotek import Vaderserie, Vindbibliotek, fordela
from luft.vindkomfort import (
    KRITERIER,
    _klassa_fara,
    _klassa_komfort,
    berakna_komfort,
    forstarkning_3d,
    jamfor,
    omrakna_vindhastighet,
    overskridande_andel,
)

DX = 2.0
OX, OY = 152_000.0, 6_577_100.0


def test_overskridande_mot_bruteforce() -> None:
    rng = np.random.default_rng(3)
    rikt = [0.0, 90.0, 180.0, 270.0]
    a = [rng.uniform(0.0, 2.0, (7, 9)) for _ in rikt]
    a[1][2, 3] = np.nan
    a[2][0, 0] = 0.0
    n = 2000
    r = rng.uniform(0, 360, n)
    u = rng.weibull(2.0, n) * 6.0
    u[:20] = 0.0
    giltig = np.ones(n, dtype=bool)
    giltig[20:40] = False
    vader = Vaderserie.skapa(r, u, giltig=giltig)
    p = overskridande_andel(a, rikt, vader, 5.0)

    f = fordela(vader, rikt, 1.0, u_min_m_s=1e-9)
    brute = np.zeros((7, 9))
    for h in range(n):
        if not f.modellerad[h]:
            continue
        brute += (a[int(f.sektor_index[h, 0])] * u[h]) > 5.0
    brute /= n - 20
    nan = np.isnan(a[1])
    assert np.all(np.isnan(p[nan]))
    np.testing.assert_allclose(p[~nan], brute[~nan], rtol=0, atol=1e-15)


def test_nen8100_klassgranser() -> None:
    k = KRITERIER["nen8100"]
    p = np.array([0.0, 0.0249, 0.025, 0.05, 0.0999, 0.10, 0.20, 0.5, np.nan])
    klass = _klassa_komfort(k, {5.0: p})
    np.testing.assert_array_equal(klass[:-1], [0, 0, 1, 2, 2, 3, 4, 4])
    assert np.isnan(klass[-1])
    pf = np.array([0.0, 0.0005, 0.00051, 0.0029, 0.003, 0.01])
    fara = _klassa_fara(k, {15.0: pf})
    np.testing.assert_array_equal(fara, [0, 0, 1, 1, 2, 2])


def test_lawson_minsta_uppfyllda_troskel() -> None:
    k = KRITERIER["lawson-lddc"]
    andel = {
        4.0: np.array([0.01, 0.20, 0.20, 0.30]),
        6.0: np.array([0.00, 0.04, 0.20, 0.30]),
        8.0: np.array([0.00, 0.00, 0.05, 0.30]),
        10.0: np.array([0.00, 0.00, 0.00, 0.06]),
    }
    np.testing.assert_array_equal(_klassa_komfort(k, andel), [0, 1, 2, 4])


def test_omrakning_identitet_och_monotonitet() -> None:
    assert float(omrakna_vindhastighet(5.0, 10.0, 0.03, 10.0, 0.03, 60.0)) == pytest.approx(5.0)
    stad = float(omrakna_vindhastighet(5.0, 10.0, 0.03, 10.0, 1.0, 60.0))
    assert stad < 5.0  # rårare terräng ger lägre vind på samma höjd
    with pytest.raises(LuftFel):
        omrakna_vindhastighet(5.0, 10.0, 0.03, 10.0, 1.0, 5.0)


def _bibliotek(std: float | None, hus_hojd: float = 10.0) -> tuple[Vindbibliotek, tuple]:
    hojd = np.zeros((20, 30))
    hojd[8:12, 12:18] = hus_hojd
    falt = []
    for r in (90.0, 270.0):
        grid = voxelisera(hojd, DX, 30.0, raster_dx_m=DX, raster_origo_x_m=OX,
                          raster_origo_y_m=OY, buffert_uppstroms_m=10.0,
                          buffert_nedstroms_m=10.0, buffert_sidor_m=10.0,
                          vindriktning_grad=r)
        u = np.zeros((3, *grid.form))
        u[0][grid.luft] = 5.0 * (1.0 if r == 270.0 else 1.5)  # förstärkning 1,0 resp. 1,5
        s = None if std is None else np.where(grid.luft, std, 0.0)
        falt.append(Vindfalt(grid, Inflode(5.0), u, np.where(grid.luft, 0.1, 0.0), 600.0,
                             u_std_m_s=s))
    return Vindbibliotek(tuple(falt)), (OX, OY - 20 * DX, OX + 30 * DX, OY)


def test_berakna_komfort_kant_svar() -> None:
    bib, utb = _bibliotek(None)
    # 100 timmar: 50 från väst med 4 m/s (lokalt 4,0), 50 från öst med 4 m/s (lokalt 6,0)
    vader = Vaderserie.skapa([270.0] * 50 + [90.0] * 50, [4.0] * 100)
    res = berakna_komfort(bib, vader, utb, DX, "nen8100")
    p = res.andel_komfort[5.0]
    luft = np.isfinite(p)
    assert np.all(np.isnan(p[8:12, 12:18]))  # huset
    np.testing.assert_allclose(p[luft], 0.5)
    assert np.all(res.komfortklass[luft] == 4)  # E


def test_gem_kraver_std_och_okar_andelen() -> None:
    bib, utb = _bibliotek(None)
    vader = Vaderserie.skapa([270.0] * 10, [3.5] * 10)
    with pytest.raises(LuftFel):
        forstarkning_3d(bib.for_riktning(270.0), 3.0)
    utan = berakna_komfort(bib, vader, utb, DX, "lawson-lddc")
    assert utan.varningar  # varnar för saknad byighet
    bib2, _ = _bibliotek(2.0)
    med = berakna_komfort(bib2, vader, utb, DX, "lawson-lddc", toppfaktor=3.0)
    luft = np.isfinite(med.andel_komfort[4.0])
    # GEM = (1 + 3·0,4)/1,85 ≈ 1,19 > 1 → 3,5·1,19 > 4 m/s alla timmar
    assert np.all(med.andel_komfort[4.0][luft] == 1.0)
    assert np.all(utan.andel_komfort[4.0][luft] == 0.0)


def test_jamfor_fore_efter() -> None:
    bib, utb = _bibliotek(None)
    vader = Vaderserie.skapa([270.0] * 100, list(np.linspace(0.5, 9.0, 100)))
    fore = berakna_komfort(bib, vader, utb, DX)
    efter = berakna_komfort(bib, vader, utb, DX)
    d = jamfor(fore, efter)
    assert d["andel_celler_forsamrade"] == 0.0
    assert np.nanmax(np.abs(d["andel_skillnad"])) == 0.0
