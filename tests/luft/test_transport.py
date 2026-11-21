# SPDX-License-Identifier: MIT
"""Tester för `luft.transport` (testgrupp L: L4, L5, L6, L8, projektion, positivitet).

Vindfälten byggs för hand; LBM-lösaren behövs inte.
"""

from __future__ import annotations

import numpy as np
import pytest

from luft.transport import Amne, TransportOperator
from luft.typer import FloatArray, Inflode, LuftFel, Vindfalt, Voxelgrid


def _likformigt(
    form: tuple[int, int, int], u_m_s: float, nu_t_m2_s: float, dx_m: float = 1.0
) -> Vindfalt:
    fast = np.zeros(form, dtype=np.bool_)
    grid = Voxelgrid(dx_m=dx_m, fast=fast, lad_m2_m3=np.zeros(form))
    u = np.zeros((3, *form))
    u[0] = u_m_s
    return Vindfalt(grid, Inflode(u_m_s), u, np.full(form, nu_t_m2_s), medelperiod_s=100.0)


def _kub(u_ref_m_s: float = 2.0) -> Vindfalt:
    """24 × 12 × 10 celler (dx = 2 m) med en fast kub 4 × 4 × 4; logprofil, varierande ν_t.

    Fältet är inte divergensfritt (u = profil överallt i luften, 0 i kuben):
    projektionen måste göra jobbet.
    """
    form = (24, 12, 10)
    fast = np.zeros(form, dtype=np.bool_)
    fast[8:12, 4:8, 0:4] = True
    grid = Voxelgrid(dx_m=2.0, fast=fast, lad_m2_m3=np.zeros(form))
    inflode = Inflode(u_ref_m_s, z_ref_m=10.0, z0_m=0.3)
    z = grid.cellcentrum_z_m()
    u = np.zeros((3, *form))
    u[0] = inflode.profil_m_s(z)[None, None, :]
    rng = np.random.default_rng(1)
    u[1] = 0.1 * rng.standard_normal(form)
    u[2] = 0.05 * rng.standard_normal(form)
    u[:, fast] = 0.0
    nu_t = 0.2 + 0.05 * z[None, None, :] + 0.05 * rng.random(form)
    nu_t = nu_t * (u_ref_m_s / 2.0)
    return Vindfalt(grid, inflode, u, nu_t, medelperiod_s=600.0)


def _slumpkallor(vind: Vindfalt, antal: int, frö: int) -> FloatArray:
    rng = np.random.default_rng(frö)
    s = np.zeros(vind.grid.form)
    luft = np.argwhere(vind.grid.luft)
    for i, j, k in luft[rng.choice(len(luft), size=antal, replace=False)]:
        s[i, j, k] = rng.random() + 0.1
    return s


# ---------------------------------------------------------------- Amne


def test_amne_validering() -> None:
    Amne("NOx")
    with pytest.raises(LuftFel):
        Amne("")
    with pytest.raises(LuftFel):
        Amne("x", sc_t=0.0)
    with pytest.raises(LuftFel):
        Amne("x", deposition_m_s=-1e-3)
    with pytest.raises(LuftFel):
        Amne("x", nedbrytning_1_s=float("nan"))


# ------------------------------------------------------------ projektion


@pytest.mark.parametrize("periodisk_y", [True, False])
def test_projektion_divergensfri(periodisk_y: bool) -> None:
    op = TransportOperator(_kub(), Amne("x"), periodisk_y=periodisk_y)
    print(f"divergens_rel (periodisk_y={periodisk_y}): {op.divergens_rel:.2e}")
    assert op.divergens_rel < 1e-10
    # oberoende kontroll av nettoflödet per luftcell
    div = op._divergens(op._inre_f_projicerat, op._rand_f_projicerat)
    skala = np.max(np.abs(op._inre_f_projicerat))
    assert np.max(np.abs(div)) / skala < 1e-10


def test_projektion_iterativ_cg() -> None:
    """Poissonlösningen med CG (troskel_direkt=0) ger också divergensfritt fält."""
    op = TransportOperator(_kub(), Amne("x"), troskel_direkt=0)
    print(f"divergens_rel (CG): {op.divergens_rel:.2e}")
    assert op.divergens_rel < 1e-10


def test_m_matris_och_positivitet() -> None:
    vind = _kub()
    amne = Amne("PM", deposition_m_s=0.01, sedimentation_m_s=0.02, nedbrytning_1_s=1e-4)
    op = TransportOperator(vind, amne)
    marg = op.m_matris_marginal()
    print(f"M-matris: {marg}")
    assert marg["max_offdiagonal"] <= 0.0
    assert marg["min_kolumnmarginal_rel"] >= -1e-12
    s = _slumpkallor(vind, 40, 2)
    c = op.los(s)
    c_luft = c[vind.grid.luft]
    print(f"min c / max c i luft = {c_luft.min() / c_luft.max():.2e}")
    assert c_luft.min() >= -1e-14 * c_luft.max()
    assert np.all(c[vind.grid.fast] == 0.0)


# ------------------------------------------------------------------- L4


def test_l4_punktkalla_mot_analytisk() -> None:
    """Punktkälla i likformigt flöde med mark, mot analytisk lösning.

    Uppströmsschemat är diskret identiskt med centrala differenser plus
    diffusionen U·dx/2 i x-led. Vi jämför därför mot den analytiska lösningen
    för *anisotrop* diffusion med K_x = K + U dx/2, K_y = K_z = K:
        c = Q / (4π K r') · exp(−U (r' − x) / (2 K_x)),
        r'² = x² + (K_x/K)(y² + z²),
    med spegelkällor i marken (z = −h), i det reflekterande taket (w = 0 och
    nollgradient) och periodiska bilder i y. Cellpecletal U dx / K = 1.
    Jämförelseområde: > 3 celler från källan, nedströms källplanet
    (i ≥ i_källa), minst 3 celler från sidor och tak och 8 från utflödet, och
    ca ≥ 1e-3 · max (uppströms/svansarna är exponentiellt små och domineras av
    randeffekter, se slutrapport). Felet mot isotropt K skrivs ut för jämförelse.
    """
    nx, ny, nz = 48, 28, 16
    dx, u, k = 1.0, 1.0, 1.0
    vind = _likformigt((nx, ny, nz), u, k, dx)
    op = TransportOperator(vind, Amne("spar", sc_t=1.0, diffusivitet_m2_s=0.0))
    i0, j0, k0, q = 12, ny // 2, 2, 1.0
    s = np.zeros((nx, ny, nz))
    s[i0, j0, k0] = q
    c = op.los(s)

    x, y, z = np.meshgrid(
        (np.arange(nx) - i0) * dx,
        (np.arange(ny) - j0) * dx,
        (np.arange(nz) + 0.5) * dx,
        indexing="ij",
    )
    h, tak = (k0 + 0.5) * dx, nz * dx

    @np.errstate(divide="ignore", invalid="ignore")
    def analytisk(kx: float) -> FloatArray:
        tot = np.zeros_like(x)
        for zs in (h, -h, 2 * tak - h, 2 * tak + h, -2 * tak + h, -2 * tak - h):
            for ys in (0.0, ny * dx, -ny * dx):
                r = np.sqrt(x**2 + (kx / k) * ((y - ys) ** 2 + (z - zs) ** 2))
                tot += q / (4 * np.pi * k * r) * np.exp(-u * (r - x) / (2 * kx))
        return tot

    ii, jj, kk = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
    avst = np.sqrt((ii - i0) ** 2 + (jj - j0) ** 2 + (kk - k0) ** 2)
    omr = (avst > 3) & (ii >= i0) & (ii < nx - 8) & (jj >= 3) & (jj < ny - 3) & (kk < nz - 3)
    resultat = {}
    for namn, kx in (("K_eff", k + u * dx / 2), ("K", k)):
        ca = analytisk(kx)
        m = omr & (ca >= 1e-3 * ca[omr].max())
        with np.errstate(divide="ignore", invalid="ignore"):
            fel = np.abs(c - ca) / ca
        resultat[namn] = (
            float(fel[m].max()),
            float(np.linalg.norm((c - ca)[m]) / np.linalg.norm(ca[m])),
            int(m.sum()),
        )
    print(f"L4 max rel fel / L2 / antal celler: {resultat}")
    assert resultat["K_eff"][0] < 0.10
    mb = op.massbalans(s, c)
    assert mb["rel_fel"] < 1e-10


# ------------------------------------------------------------------- L5


def test_l5_adjungerad_dualitet() -> None:
    vind = _kub()
    amne = Amne("NOx", deposition_m_s=0.005, sedimentation_m_s=0.01, nedbrytning_1_s=1e-4)
    op = TransportOperator(vind, amne)
    s = _slumpkallor(vind, 30, 3)
    rng = np.random.default_rng(4)
    w = np.zeros(vind.grid.form)
    w[14:18, 3:9, 0:3] = rng.random((4, 6, 3))
    c = op.los(s)
    c_r = float((w * c).sum() / w.sum())
    y = op.los_adjungerad(w)
    c_r_adj = float((y * s).sum())
    fel = abs(c_r - c_r_adj) / abs(c_r)
    print(f"L5 rel fel {fel:.2e}")
    assert fel < 1e-8
    assert np.all(y[vind.grid.fast] == 0.0)
    assert y.min() >= -1e-14 * y.max()


@pytest.mark.parametrize("forkond", ["auto", "sgs", "ilu"])
def test_l5_iterativ_samma_som_direkt(forkond: str) -> None:
    vind = _kub()
    amne = Amne("NOx", deposition_m_s=0.005)
    direkt = TransportOperator(vind, amne)
    iterativ = TransportOperator(vind, amne, troskel_direkt=0, forkonditionering=forkond)
    s = np.stack([_slumpkallor(vind, 10, 5), _slumpkallor(vind, 10, 6)])
    cd, ci = direkt.los(s), iterativ.los(s)
    assert cd.shape == s.shape
    fel = float(np.max(np.abs(cd - ci)) / np.max(np.abs(cd)))
    w = np.zeros(vind.grid.form)
    w[20, 6, 1] = 1.0
    fel_adj = float(
        np.max(np.abs(direkt.los_adjungerad(w) - iterativ.los_adjungerad(w)))
        / np.max(np.abs(direkt.los_adjungerad(w)))
    )
    print(f"iterativ ({forkond}) mot direkt: {fel:.2e}, adjungerad {fel_adj:.2e}")
    assert fel < 1e-8
    assert fel_adj < 1e-8
    assert iterativ._forkond_typ == ("ilu" if forkond == "ilu" else "sgs")


def test_okand_forkonditionering_kastar() -> None:
    with pytest.raises(LuftFel):
        TransportOperator(_kub(), Amne("x"), forkonditionering="amg")


# ------------------------------------------------------------------- L6


def test_l6_massbalans() -> None:
    vind = _kub()
    amne = Amne("PM10", deposition_m_s=0.01, sedimentation_m_s=0.02, nedbrytning_1_s=2e-4)
    op = TransportOperator(vind, amne)
    s = _slumpkallor(vind, 30, 7)
    c = op.los(s)
    mb = op.massbalans(s, c)
    print(f"L6 {mb}")
    assert mb["rel_fel"] < 1e-8
    assert mb["deposition_g_s"] > 0 and mb["nedbrytning_g_s"] > 0 and mb["utflode_g_s"] > 0


# ------------------------------------------------------------------- L8


def test_l8_hastighetsskalning() -> None:
    vind = _kub(u_ref_m_s=2.0)
    amne = Amne("spar", diffusivitet_m2_s=0.0)  # D skalar inte med U, se los_skalad
    op = TransportOperator(vind, amne)
    s = _slumpkallor(vind, 20, 8)
    c = op.los(s)
    c2 = op.los_skalad(s, 2.0)
    c2_ny = TransportOperator(vind.skalat(4.0), amne).los(s)
    fel_skalad = float(np.max(np.abs(c2 - c / 2)) / np.max(c))
    fel_ny = float(np.max(np.abs(c2_ny - c / 2)) / np.max(c / 2))
    print(f"L8 los_skalad {fel_skalad:.2e}, ny operator ur vind.skalat {fel_ny:.2e}")
    assert fel_skalad < 1e-8
    assert fel_ny < 1e-8


def test_l8_skalning_med_deposition_kastar() -> None:
    vind = _kub()
    op = TransportOperator(vind, Amne("PM", deposition_m_s=0.01))
    with pytest.raises(LuftFel):
        op.los_skalad(_slumpkallor(vind, 3, 9), 2.0)
    op2 = op.skalad_operator(4.0)
    assert op2.vind.inflode.u_ref_m_s == 4.0


# ------------------------------------------------------------- felfall


def test_kalla_i_fast_cell_kastar() -> None:
    vind = _kub()
    op = TransportOperator(vind, Amne("x"))
    s = np.zeros(vind.grid.form)
    s[9, 5, 1] = 1.0
    with pytest.raises(LuftFel):
        op.los(s)
    with pytest.raises(LuftFel):
        op.los(np.zeros((3, 3, 3)))


def test_sluten_ficka_kastar() -> None:
    form = (10, 6, 6)
    fast = np.zeros(form, dtype=np.bool_)
    fast[3:8, 1:5, 0:4] = True
    fast[5, 3, 1] = False  # luftcell inne i byggnaden
    grid = Voxelgrid(1.0, fast, np.zeros(form))
    u = np.zeros((3, *form))
    u[0] = 1.0
    u[:, fast] = 0.0
    vind = Vindfalt(grid, Inflode(1.0), u, np.full(form, 0.5), 10.0)
    with pytest.raises(LuftFel, match="slutna fickor"):
        TransportOperator(vind, Amne("x"))
