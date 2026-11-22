"""Tester för Dozier–Frews O(N) horisont och monotona SVF-intervall (`dagsljus.intervall`)."""

from __future__ import annotations

import math
import time

import numpy as np

from dagsljus.horisont import Hojdfalt, horisont_riktning_ruta, riktningar_rad
from dagsljus.intervall import horisont_1d_konvext_holje, kor_monoton_svf_intervall
from dagsljus.matt import svf_fran_horisont


def _horisont_1d_facit_n2(z: np.ndarray, dx: float, riktning: int = 1) -> np.ndarray:
    n = int(z.size)
    ut = np.zeros(n, dtype=np.float64)
    for i in range(n):
        best = 0.0
        if riktning == 1:
            for j in range(i + 1, n):
                dz = float(z[j] - z[i])
                if dz > 0.0:
                    ang = math.atan(dz / ((j - i) * dx))
                    if ang > best:
                        best = ang
        else:
            for j in range(i - 1, -1, -1):
                dz = float(z[j] - z[i])
                if dz > 0.0:
                    ang = math.atan(dz / ((i - j) * dx))
                    if ang > best:
                        best = ang
        ut[i] = best
    return ut


def test_dozier_frew_exakt_mot_n2_och_snabb() -> None:
    """Dozier–Frews O(N) ger maskinprecision (< 1e-14) mot O(N²) i båda riktningarna."""
    rng = np.random.default_rng(123)
    z = rng.uniform(0.0, 25.0, size=120)
    dx = 2.0

    for rikt in (1, -1):
        h_fast = horisont_1d_konvext_holje(z, dx, riktning=rikt)
        h_ref = _horisont_1d_facit_n2(z, dx, riktning=rikt)
        assert float(np.max(np.abs(h_fast - h_ref))) < 1e-14

    # Prestandatest på lång 1D-profil (N = 2000): ska gå på några millisekunder
    z_stor = rng.uniform(0.0, 30.0, size=2000)
    t0 = time.perf_counter()
    h_stor = horisont_1d_konvext_holje(z_stor, 1.0, riktning=1)
    dt_s = time.perf_counter() - t0
    assert h_stor.shape == (2000,)
    assert dt_s < 0.05


def test_monoton_svf_intervall_innesluter_slumpade_hushojder() -> None:
    """20 slumpade hushöjdsstörningar inom ±σ måste inneslutas av [svf_min, svf_max]."""
    mark = np.zeros((16, 16), dtype=np.float64)
    bygg = np.zeros((16, 16), dtype=np.float64)
    bygg[3:7, 3:7] = 12.0
    bygg[9:13, 9:13] = 15.0
    sigma = 2.0

    iv = kor_monoton_svf_intervall(
        mark,
        bygg,
        sigma,
        cell_m=2.0,
        antal_riktningar=16,
        max_avstand_m=50.0,
        svf_troskel=0.70,
    )
    mark_mask = bygg == 0.0
    assert iv.bredd_max > 0.0
    assert np.all(iv.svf_min[mark_mask] <= iv.svf_max[mark_mask] + 1e-12)

    rng = np.random.default_rng(99)
    rikt = riktningar_rad(16)
    for _ in range(20):
        delta = rng.uniform(-sigma, sigma, size=bygg.shape)
        z_pert = mark + np.where(bygg > 0.0, np.maximum(0.0, bygg + delta), 0.0)
        hf = Hojdfalt(z_m=z_pert, cell_m=2.0, x_min_m=0.0, y_max_m=0.0)
        h = np.stack([horisont_riktning_ruta(hf, float(phi), 50.0)[0] for phi in rikt], axis=0)
        svf = svf_fran_horisont(h, axel=0)
        assert np.all(svf[mark_mask] >= iv.svf_min[mark_mask] - 1e-12)
        assert np.all(svf[mark_mask] <= iv.svf_max[mark_mask] + 1e-12)
