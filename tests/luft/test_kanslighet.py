"""Tester för adjungerad känslighet, CELF-åtgärder och MKN-intervall (`luft.kanslighet`)."""

from __future__ import annotations

import numpy as np
import pytest

from luft.kanslighet import (
    berakna_adjungerad_kanslighet,
    kor_monoton_mkn_intervall,
    valj_basta_luftatgarder_k,
)
from luft.transport import TransportOperator, amne_partikel
from luft.typer import Inflode, Vindfalt, Voxelgrid


def _bygg_vindfalt(lad: np.ndarray | None = None) -> Vindfalt:
    nx, ny, nz = 12, 8, 6
    fast = np.zeros((nx, ny, nz), dtype=np.bool_)
    lad_arr = np.zeros((nx, ny, nz), dtype=np.float64) if lad is None else lad
    grid = Voxelgrid(dx_m=2.0, fast=fast, lad_m2_m3=lad_arr)
    u = np.zeros((3, nx, ny, nz), dtype=np.float64)
    u[0] = 2.0  # vind i +x-led
    nu_t = np.full((nx, ny, nz), 0.5, dtype=np.float64)
    return Vindfalt(
        grid=grid, inflode=Inflode(u_ref_m_s=2.0), u_m_s=u, nu_t_m2_s=nu_t, medelperiod_s=60.0
    )


def test_adjungerad_dualitet_och_vegetationsgradient_mot_finita_differenser() -> None:
    """Adjungerad vegetationsgradient ∂C_r/∂LAD ska stämma med finit differens (< 1e-3)."""
    vind = _bygg_vindfalt()
    amne = amne_partikel(
        "PM_grov",
        diameter_um=6.0,
        densitet_kg_m3=2200.0,
        deposition_m_s=0.005,
        vegetationsdeposition_m_s=0.02,
    )
    op = TransportOperator(vind, amne, schema="uppstroms")

    kallor = np.zeros(vind.grid.form, dtype=np.float64)
    kallor[2, 3:5, 0] = 0.01  # gata uppströms
    receptor = np.zeros(vind.grid.form, dtype=np.float64)
    receptor[8, 3:5, 0] = 1.0  # skolgård nedströms

    kans = berakna_adjungerad_kanslighet(op, kallor, receptor)
    assert kans.m_matris_ok is True
    assert kans.dualitetsfel_rel < 1e-12
    assert kans.receptor_halt_ug_m3 > 0.0

    # Kontrollera vegetationsgradienten i en cell mitt emellan gatan och skolgården: (5, 4, 0)
    cell = (5, 4, 0)
    grad_adj = float(kans.vegetationsgradient_g_m3_per_lad[cell])
    assert grad_adj < 0.0  # mer vegetation => lägre halt i receptorn

    eps = 1e-4
    lad_pert = np.zeros(vind.grid.form, dtype=np.float64)
    lad_pert[cell] = eps
    vind_p = _bygg_vindfalt(lad=lad_pert)
    op_p = TransportOperator(vind_p, amne, schema="uppstroms")
    c_p = op_p.los(kallor)
    c_r_p = float(np.sum(receptor * c_p) / np.sum(receptor))
    grad_fd = (c_r_p - kans.receptor_halt_g_m3) / eps

    assert grad_adj == pytest.approx(grad_fd, rel=1e-3)


def test_celf_luftatgarder_undviker_dubbelrakning() -> None:
    """Överlappande åtgärdszoner på samma vägceller får inte dubbelräknas."""
    vind = _bygg_vindfalt()
    amne = amne_partikel("PM_grov", diameter_um=6.0, densitet_kg_m3=2200.0)
    op = TransportOperator(vind, amne, schema="uppstroms")

    kallor = np.zeros(vind.grid.form, dtype=np.float64)
    kallor[2, 3, 0] = 0.02  # huvudgata
    kallor[3, 5, 0] = 0.01  # sidogata
    receptor = np.zeros(vind.grid.form, dtype=np.float64)
    receptor[8, 3:6, 0] = 1.0

    kans = berakna_adjungerad_kanslighet(op, kallor, receptor)
    z_a = np.zeros(vind.grid.form, dtype=np.bool_)
    z_a[2, 3, 0] = True
    z_b = np.zeros(vind.grid.form, dtype=np.bool_)
    z_b[2, 3, 0] = True  # helt överlappande med z_a
    z_c = np.zeros(vind.grid.form, dtype=np.bool_)
    z_c[3, 5, 0] = True  # separat sidogata

    valda = valj_basta_luftatgarder_k(
        kans, {"huvud_a": z_a, "huvud_b": z_b, "sido_c": z_c}, k=2, reduktionsandel=0.5
    )
    assert len(valda) == 2
    assert valda[0].zon_id in ("huvud_a", "huvud_b")
    assert valda[1].zon_id == "sido_c"
    assert valda[1].kumulativ_sankning_ug_m3 == pytest.approx(
        valda[0].marginal_sankning_ug_m3 + valda[1].marginal_sankning_ug_m3, rel=1e-12
    )


def test_monoton_mkn_intervall_innesluter_50_slumpade_scenarier() -> None:
    """50 slumpade (emissionsfaktor, depositionsfaktor) måste inneslutas i MKN-intervallet."""
    rng = np.random.default_rng(7)
    timmar = 365 * 24
    lokal = rng.gamma(shape=2.0, scale=15.0, size=timmar)
    bakgrund = 12.0
    e_iv = (0.8, 1.25)
    d_iv = (0.8, 1.25)

    iv = kor_monoton_mkn_intervall(
        lokal,
        bakgrund,
        emissionsfaktor_intervall=e_iv,
        depositionsfaktor_intervall=d_iv,
    )
    assert iv.min_dygn_over_grans <= iv.max_dygn_over_grans
    assert iv.min_arsmedel_ug_m3 <= iv.max_arsmedel_ug_m3

    for _ in range(50):
        e = float(rng.uniform(*e_iv))
        d = float(rng.uniform(*d_iv))
        s = bakgrund + lokal * (e / d)
        dygn = s.reshape(365, 24).mean(axis=1)
        n_over = int(np.count_nonzero(dygn > 50.0))
        ars = float(np.mean(s))
        assert iv.min_dygn_over_grans <= n_over <= iv.max_dygn_over_grans
        assert iv.min_arsmedel_ug_m3 - 1e-9 <= ars <= iv.max_arsmedel_ug_m3 + 1e-9
