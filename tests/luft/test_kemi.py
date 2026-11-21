# SPDX-License-Identifier: MIT
"""Tester för `luft.kemi` (L7: fotostationär NO₂)."""

from __future__ import annotations

import numpy as np
import pytest

from luft.kemi import (
    MOLMASSA_NO2_G_MOL,
    MOLMASSA_O3_G_MOL,
    no2_fotostationar,
    ppb_per_ug_m3,
)
from luft.typer import LuftFel

J = 8e-3  # 1/s, testvärde (sommardag)
K = 4.4e-4  # ppb⁻¹ s⁻¹, testvärde


def test_omrakning_ppb() -> None:
    # 1 ppb NO2 ≈ 1,88 µg/m³ vid 25 °C
    assert 1.0 / float(ppb_per_ug_m3(MOLMASSA_NO2_G_MOL, 298.15)) == pytest.approx(1.880, abs=2e-3)


def test_l7_jamvikt_och_summor() -> None:
    rng = np.random.default_rng(0)
    n = 2000
    nox = rng.random(n) * 300.0
    no2_b = rng.random(n) * 30.0
    o3_b = rng.random(n) * 100.0
    andel = rng.random(n) * 0.3
    no_b = rng.random(n) * 10.0
    t = 260.0 + rng.random(n) * 40.0
    j = rng.random(n) * 1e-2 + 1e-5
    no2 = no2_fotostationar(nox, no2_b, o3_b, andel, j, K, t, no_bakgrund_ug_m3=no_b)

    f2 = ppb_per_ug_m3(MOLMASSA_NO2_G_MOL, t)
    f3 = ppb_per_ug_m3(MOLMASSA_O3_G_MOL, t)
    f1 = ppb_per_ug_m3(30.006, t)
    nox_tot = nox * f2 + no2_b * f2 + no_b * f1
    ox_tot = andel * nox * f2 + no2_b * f2 + o3_b * f3
    x = no2 * f2
    no, o3 = nox_tot - x, ox_tot - x
    assert np.all(no >= 0) and np.all(o3 >= 0)
    fel = np.abs(j * x - K * no * o3) / np.maximum(j * x, K * no * o3)
    print(f"L7 max rel fel jämvikt {fel.max():.2e}")
    assert fel.max() < 1e-10
    # Summorna: NO och O3 ovan är definierade ur de bevarade summorna, så det
    # som återstår att visa är att roten är den fysikaliska (0 ≤ x ≤ min) och
    # att den stämmer med en oberoende lösning (bisektion i högre precision).
    assert np.all(x <= np.minimum(nox_tot, ox_tot) * (1 + 1e-12))
    lo = np.zeros(n, dtype=np.longdouble)
    hi = np.minimum(nox_tot, ox_tot).astype(np.longdouble)
    nl, ol, jl = (a.astype(np.longdouble) for a in (nox_tot, ox_tot, j))
    for _ in range(200):
        mitt = 0.5 * (lo + hi)
        g = jl * mitt - K * (nl - mitt) * (ol - mitt)
        lo = np.where(g < 0, mitt, lo)
        hi = np.where(g < 0, hi, mitt)
    fel_rot = np.abs(x - hi.astype(np.float64)) / np.maximum(x, 1e-300)
    print(f"L7 max rel fel mot bisektion {fel_rot.max():.2e}")
    assert fel_rot.max() < 1e-10


def test_l7_broadcast() -> None:
    nox = np.linspace(0, 200, 5)[:, None]
    o3 = np.array([0.0, 40.0, 80.0])[None, :]
    no2 = no2_fotostationar(nox, 15.0, o3, 0.1, J, K)
    assert no2.shape == (5, 3)
    assert np.all(np.diff(no2[:, 1]) > 0)  # mer NOx → mer NO2


def test_l7_gransfall() -> None:
    # NOx = 0 (lokalt), bakgrund i jämvikt: NO2 = bakgrundens NO2
    no2 = no2_fotostationar(0.0, 20.0, 60.0, 0.1, J, K)
    assert float(no2) == pytest.approx(20.0, rel=1e-10)
    # inget O3, ingen NO2 någonstans → NO2 = 0
    assert float(no2_fotostationar(100.0, 0.0, 0.0, 0.0, J, K)) == 0.0
    # inget O3 i bakgrunden: NO2 ≤ direkt NO2
    no2_d = float(no2_fotostationar(100.0, 0.0, 0.0, 0.2, J, K))
    assert 0.0 < no2_d <= 20.0
    # allt noll
    assert float(no2_fotostationar(0.0, 0.0, 0.0, 0.0, J, K)) == 0.0
    # natt (J = 0): titrering, NO2 = min(NOx, Ox) i ppb
    f2 = float(ppb_per_ug_m3(MOLMASSA_NO2_G_MOL, 288.15))
    f3 = float(ppb_per_ug_m3(MOLMASSA_O3_G_MOL, 288.15))
    no2_n = float(no2_fotostationar(50.0, 10.0, 20.0, 0.1, 0.0, K, no_bakgrund_ug_m3=0.0))
    ox = (0.1 * 50.0 + 10.0) * f2 + 20.0 * f3
    assert no2_n * f2 == pytest.approx(min(60.0 * f2, ox), rel=1e-12)


@pytest.mark.parametrize(
    "argument",
    [
        dict(nox_lokalt_ug_m3=-1.0),
        dict(o3_bakgrund_ug_m3=-1.0),
        dict(direkt_no2_andel=1.5),
        dict(k_ppb_1_s=0.0),
        dict(j_no2_1_s=-1e-3),
        dict(temperatur_k=0.0),
        dict(nox_lokalt_ug_m3=float("nan")),
    ],
)
def test_l7_ogiltiga_indata(argument: dict[str, float]) -> None:
    bas = dict(
        nox_lokalt_ug_m3=10.0,
        no2_bakgrund_ug_m3=10.0,
        o3_bakgrund_ug_m3=50.0,
        direkt_no2_andel=0.1,
        j_no2_1_s=J,
        k_ppb_1_s=K,
    )
    bas.update(argument)
    with pytest.raises(LuftFel):
        no2_fotostationar(**bas)
