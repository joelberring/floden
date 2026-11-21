# SPDX-License-Identifier: MIT
"""Tester för andra ordningens advektion (``schema="tvd"``) i `luft.transport` (uppdrag L-f).

TVD-begränsaren fryses ur ett referensfält så att operatorn är linjär; med
``m_matris_tak=True`` (standard) är den dessutom en M-matris. Testerna mäter
noggrannhet mot analytisk lösning (L4 med fysikaliskt K), massbalans,
positivitet, superposition, adjungerad dualitet och skalning.
"""

from __future__ import annotations

import numpy as np
import pytest

from luft.transport import Amne, TransportOperator, begransare_psi
from luft.typer import FloatArray, LuftFel, Vindfalt

from .test_transport import _kub, _likformigt, _slumpkallor

# ------------------------------------------------------------- begränsare


@pytest.mark.parametrize("namn", ["van_leer", "koren"])
def test_begransare_tvd_omrade(namn: str) -> None:
    r = np.linspace(-5.0, 20.0, 2001)
    psi = begransare_psi(r, namn)
    assert np.all(psi[r <= 0] == 0.0)
    assert np.all(psi >= 0.0)
    assert np.all(psi <= np.minimum(2.0 * np.maximum(r, 0.0), 2.0) + 1e-15)
    assert float(begransare_psi(np.array([1.0]), namn)[0]) == pytest.approx(1.0)
    with pytest.raises(LuftFel):
        begransare_psi(r, "minmod_finns_inte")


def test_ogiltiga_parametrar() -> None:
    vind = _kub()
    with pytest.raises(LuftFel):
        TransportOperator(vind, Amne("x"), schema="central")
    with pytest.raises(LuftFel):
        TransportOperator(vind, Amne("x"), schema="tvd", begransare="superbee")
    with pytest.raises(LuftFel):
        TransportOperator(vind, Amne("x"), schema="tvd", tvd_pass=0)
    with pytest.raises(LuftFel):  # referenskällor betyder inget för uppströms
        TransportOperator(vind, Amne("x"), referenskallor_g_s=np.zeros(vind.grid.form))
    with pytest.raises(LuftFel):
        TransportOperator(vind, Amne("x")).frys_begransare(np.zeros(vind.grid.form))


def test_standard_ar_uppstroms() -> None:
    op = TransportOperator(_kub(), Amne("x"))
    assert op.schema == "uppstroms" and op.psi is None and op.begransare_fryst
    assert op.matris is op.matris_uppstroms


# ------------------------------------------------------------------- L4


def _l4(
    k: float = 1.0,
) -> tuple[TransportOperator, FloatArray, FloatArray, np.ndarray, Vindfalt, Amne]:
    """Samma uppsättning som L4 i test_transport.py; analytiskt med *fysikaliskt* K."""
    nx, ny, nz = 48, 28, 16
    dx, u = 1.0, 1.0
    vind = _likformigt((nx, ny, nz), u, k, dx)
    i0, j0, k0, q = 12, ny // 2, 2, 1.0
    s = np.zeros((nx, ny, nz))
    s[i0, j0, k0] = q
    x, y, z = np.meshgrid(
        (np.arange(nx) - i0) * dx,
        (np.arange(ny) - j0) * dx,
        (np.arange(nz) + 0.5) * dx,
        indexing="ij",
    )
    h, tak = (k0 + 0.5) * dx, nz * dx
    ca = np.zeros_like(x)
    with np.errstate(divide="ignore", invalid="ignore"):
        for zs in (h, -h, 2 * tak - h, 2 * tak + h, -2 * tak + h, -2 * tak - h):
            for ys in (0.0, ny * dx, -ny * dx):
                r = np.sqrt(x**2 + (y - ys) ** 2 + (z - zs) ** 2)
                ca += q / (4 * np.pi * k * r) * np.exp(-u * (r - x) / (2 * k))
    ii, jj, kk = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
    avst = np.sqrt((ii - i0) ** 2 + (jj - j0) ** 2 + (kk - k0) ** 2)
    omr = (avst > 3) & (ii >= i0) & (ii < nx - 8) & (jj >= 3) & (jj < ny - 3) & (kk < nz - 3)
    mask = omr & (ca >= 1e-3 * ca[omr].max())
    amne = Amne("spar", sc_t=1.0, diffusivitet_m2_s=0.0)
    return TransportOperator(vind, amne), s, ca, mask, vind, amne


def _fel(c: FloatArray, ca: FloatArray, mask: np.ndarray) -> tuple[float, float]:
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.abs(c - ca) / ca
    return float(rel[mask].max()), float(
        np.linalg.norm((c - ca)[mask]) / np.linalg.norm(ca[mask])
    )


def test_l4_tvd_mot_analytisk_fysikaliskt_k() -> None:
    """Punktkälla, cellpecletal 1: TVD ska ha klart mindre fel än uppströms mot fysikaliskt K.

    Uppmätt 2026-11-21 (≥ 1e-3·max, > 3 celler från källan, 7858 celler):
    uppströms max 1,44 / L2 0,0365; van Leer max 0,218 / L2 0,0115; Koren
    max 0,217 / L2 0,0101; ren centraldifferens (ψ ≡ 1, andra ordningens
    gräns här) L2 0,0116. Största felet ligger i källplanet långt från
    plymaxet där c ≈ 2e-3·max.
    """
    upp, s, ca, mask, vind, amne = _l4()
    c_upp = upp.los(s)
    fel_upp = _fel(c_upp, ca, mask)
    resultat = {"uppstroms": fel_upp}
    for begr in ("van_leer", "koren"):
        op = TransportOperator(vind, amne, schema="tvd", begransare=begr)
        c = op.los(s)
        resultat[begr] = _fel(c, ca, mask)
        mb = op.massbalans(s, c)
        c_luft = c[op.vind.grid.luft]
        assert mb["rel_fel"] < 1e-8
        assert c_luft.min() >= -1e-12 * c_luft.max()
        assert op.m_matris_marginal()["max_offdiagonal"] <= 0.0
        # klart bättre än uppströms: minst 4× i max och 2,5× i L2
        assert resultat[begr][0] < 0.25 * fel_upp[0]
        assert resultat[begr][1] < 0.4 * fel_upp[1]
        assert resultat[begr][0] < 0.25
        assert resultat[begr][1] < 0.015
    print(f"L4 TVD (max rel fel, L2) mot fysikaliskt K: {resultat}")


# ------------------------------------------------------- kubfallet (Pe ≈ 0–7)


def _kubdelar() -> tuple[Vindfalt, Amne, FloatArray, FloatArray, FloatArray]:
    vind = _kub()
    amne = Amne("NOx", deposition_m_s=0.005, sedimentation_m_s=0.01, nedbrytning_1_s=1e-4)
    s = _slumpkallor(vind, 30, 3)
    s2 = _slumpkallor(vind, 5, 11)
    rng = np.random.default_rng(4)
    w = np.zeros(vind.grid.form)
    w[14:18, 3:9, 0:3] = rng.random((4, 6, 3))
    return vind, amne, s, s2, w


@pytest.mark.parametrize("begr", ["van_leer", "koren"])
def test_tvd_dualitet_superposition_positivitet(begr: str) -> None:
    vind, amne, s, s2, w = _kubdelar()
    op = TransportOperator(vind, amne, schema="tvd", begransare=begr)
    with pytest.raises(LuftFel, match="inte fryst"):
        op.los_adjungerad(w)
    c = op.los(s)  # fryser begränsaren ur s
    assert op.begransare_fryst and op.psi is not None
    luft = op.vind.grid.luft
    # dualitet för den frysta operatorn
    c_r = float((w * c).sum() / w.sum())
    y = op.los_adjungerad(w)
    dual = abs(c_r - float((y * s).sum())) / abs(c_r)
    # superposition med ett annat källfält
    c2 = op.los(s2)
    sup = float(np.max(np.abs(op.los(s + s2) - c - c2)) / np.max(c))
    mb = op.massbalans(s, c)["rel_fel"]
    marg = op.m_matris_marginal()
    print(
        f"TVD {begr}: dualitet {dual:.1e}, superposition {sup:.1e}, massbalans {mb:.1e}, "
        f"min/max c {c[luft].min() / c.max():.1e}, c2 {c2[luft].min() / c2.max():.1e}, "
        f"y {y[luft].min() / y.max():.1e}, M-matris {marg}"
    )
    assert dual < 1e-8
    assert sup < 1e-10
    assert mb < 1e-8
    for falt in (c, c2, y):
        assert falt[luft].min() >= -1e-12 * falt.max()
        assert np.all(falt[~luft] == 0.0)
    assert marg["max_offdiagonal"] <= 0.0
    assert marg["min_kolumnmarginal_rel"] >= -1e-12
    # TVD ska faktiskt skilja sig från uppströms
    c_upp = TransportOperator(vind, amne).los(s)
    assert float(np.max(np.abs(c - c_upp)) / np.max(c_upp)) > 1e-2


@pytest.mark.parametrize("forkond", ["sgs", "ilu"])
def test_tvd_iterativ_samma_som_direkt(forkond: str) -> None:
    vind, amne, s, _, w = _kubdelar()
    direkt = TransportOperator(vind, amne, schema="tvd", referenskallor_g_s=s)
    iterativ = TransportOperator(
        vind, amne, schema="tvd", referenskallor_g_s=s, troskel_direkt=0,
        forkonditionering=forkond,
    )
    assert iterativ.psi is not None and direkt.psi is not None
    assert float(np.max(np.abs(iterativ.psi - direkt.psi))) < 1e-6
    cd, ci = direkt.los(s), iterativ.los(s)
    fel = float(np.max(np.abs(cd - ci)) / np.max(cd))
    yd, yi = direkt.los_adjungerad(w), iterativ.los_adjungerad(w)
    fel_adj = float(np.max(np.abs(yd - yi)) / np.max(yd))
    print(f"TVD iterativ ({forkond}) mot direkt: {fel:.1e}, adjungerad {fel_adj:.1e}")
    assert fel < 1e-8
    assert fel_adj < 1e-8


def test_tvd_utan_tak_kastar_vid_negativ_halt() -> None:
    """Utan M-matristak är den frysta operatorn positiv bara nära referensfältet.

    Uppmätt 2026-11-21: fryst ur s ger min/max ≈ −0,38 för källorna s2 och
    ≈ −0,08 för den adjungerade; det ska ge LuftFel, inte ett tyst fel.
    """
    vind, amne, s, s2, w = _kubdelar()
    op = TransportOperator(vind, amne, schema="tvd", m_matris_tak=False)
    c = op.los(s)
    assert c[op.vind.grid.luft].min() >= -1e-10 * c.max()
    assert op.m_matris_marginal()["max_offdiagonal"] > 0.0  # inte en M-matris
    with pytest.raises(LuftFel, match="negativa"):
        op.los(s2)
    with pytest.raises(LuftFel, match="negativa"):
        op.los_adjungerad(w)
    # skillnadsscenarier (källor med båda tecknen) kontrolleras inte
    op.los(s - s2)


def test_tvd_hastighetsskalning() -> None:
    vind = _kub(u_ref_m_s=2.0)
    amne = Amne("spar", diffusivitet_m2_s=0.0)
    s = _slumpkallor(vind, 20, 8)
    op = TransportOperator(vind, amne, schema="tvd")
    c = op.los(s)
    c2 = op.los_skalad(s, 2.0)
    ny = op.skalad_operator(4.0)
    assert ny.schema == "tvd" and ny.begransare_fryst
    c2_ny = ny.los(s)
    fel_skalad = float(np.max(np.abs(c2 - c / 2)) / np.max(c))
    fel_ny = float(np.max(np.abs(c2_ny - c / 2)) / np.max(c / 2))
    print(f"TVD L8 los_skalad {fel_skalad:.1e}, ny operator {fel_ny:.1e}")
    assert fel_skalad < 1e-8
    assert fel_ny < 1e-8


def test_tvd_flera_kolumner_fryser_gemensamt() -> None:
    """Flera källkolumner i ett anrop fryser ur Σ|s_j| och löses med samma operator."""
    vind, amne, s, s2, _ = _kubdelar()
    op = TransportOperator(vind, amne, schema="tvd")
    c = op.los(np.stack([s, s2]))
    ref = TransportOperator(vind, amne, schema="tvd", referenskallor_g_s=s + s2)
    assert float(np.max(np.abs(ref.los(s) - c[0])) / np.max(c[0])) < 1e-12
    assert float(np.max(np.abs(op.los(s) - c[0])) / np.max(c[0])) < 1e-12
