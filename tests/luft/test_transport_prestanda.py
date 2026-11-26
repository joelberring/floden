# SPDX-License-Identifier: MIT
"""Prestanda och korrekthet för de iterativa lösarna i `luft.transport` (uppdrag L-f).

Ett syntetiskt kvarter (gator, hus 8–24 m, logprofil med brus i u, v, w) ger
ett transportsystem av samma slag som i demon. Uppmätt 2026-11-21 (2 kärnor),
1,53·10⁵ obekanta, framåt + adjungerad med varsin operator som i `luft.kedja`:
före (ILU drop_tol 1e-5 + bicgstab, Jacobi-CG i projektionen) 31,0 s, efter
(SGS + bicgstab, multigrid-CG i projektionen) 3,4 s.

Fallet med ILU (den gamla lösaren) körs bara med ``FLODEN_LANGSAMMA_TESTER=1``.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from luft.transport import Amne, TransportOperator
from luft.typer import FloatArray, Inflode, Vindfalt, Voxelgrid

LANGSAMMA = os.environ.get("FLODEN_LANGSAMMA_TESTER") == "1"


def _kvarter(form: tuple[int, int, int], dx_m: float = 2.0, frö: int = 1) -> Vindfalt:
    """Hus i rader längs x, logprofil och slumpmässiga tvär-/vertikalkomponenter."""
    fast = np.zeros(form, dtype=np.bool_)
    rng = np.random.default_rng(frö)
    for i in range(form[0] // 5, form[0] - 16, 14):
        for j in range(4, form[1] - 4, 12):
            fast[i : i + 8, j : j + 7, 0 : int(rng.integers(4, 12))] = True
    grid = Voxelgrid(dx_m, fast, np.zeros(form))
    inflode = Inflode(3.0, z_ref_m=10.0, z0_m=0.5)
    z = grid.cellcentrum_z_m()
    u = np.zeros((3, *form))
    u[0] = inflode.profil_m_s(z)[None, None, :]
    u[1] = 0.3 * rng.standard_normal(form)
    u[2] = 0.1 * rng.standard_normal(form)
    u[:, fast] = 0.0
    nu_t = 0.3 + 0.08 * z[None, None, :] + 0.1 * rng.random(form)
    return Vindfalt(grid, inflode, u, nu_t, medelperiod_s=600.0)


def _vagkalla(vind: Vindfalt) -> FloatArray:
    nx, ny, _ = vind.grid.form
    s = np.zeros(vind.grid.form)
    s[5 : nx - 5, ny // 2 - 3 : ny // 2 + 3, 0] = 1e-3
    s[vind.grid.fast] = 0.0
    return s


def _receptor(vind: Vindfalt) -> FloatArray:
    w = np.zeros(vind.grid.form)
    luft = np.argwhere(vind.grid.luft[:, :, 1])
    i, j = luft[len(luft) * 2 // 3]
    w[i, j, 1] = 1.0
    return w


def _kor(vind: Vindfalt, **kw: object) -> tuple[FloatArray, FloatArray, dict[str, float]]:
    """Framåt och adjungerad med varsin operator (som `luft.kedja`), med tider."""
    s, w = _vagkalla(vind), _receptor(vind)
    t0 = time.perf_counter()
    op = TransportOperator(vind, Amne("NOx"), **kw)  # type: ignore[arg-type]
    t1 = time.perf_counter()
    c = op.los(s)
    t2 = time.perf_counter()
    y = TransportOperator(vind, Amne("NOx"), **kw).los_adjungerad(w)  # type: ignore[arg-type]
    t3 = time.perf_counter()
    mb = op.massbalans(s, c)["rel_fel"]
    tider = {
        "bygg_s": t1 - t0, "los_s": t2 - t1, "adjungerad_s": t3 - t2, "totalt_s": t3 - t0,
        "massbalans": mb, "divergens_rel": op.divergens_rel, "obekanta": op.antal_obekanta,
    }
    return c, y, tider


def test_iterativ_mot_direkt_kvarter() -> None:
    """≈ 1,3·10⁴ obekanta: SGS-iterativ (och multigrid i projektionen) mot splu, rel < 1e-8."""
    vind = _kvarter((40, 24, 16))
    ci, yi, ti = _kor(vind, troskel_direkt=0)
    cd, yd, td = _kor(vind, troskel_direkt=10**9)
    fel = float(np.max(np.abs(ci - cd)) / np.max(cd))
    fel_adj = float(np.max(np.abs(yi - yd)) / np.max(yd))
    s, w = _vagkalla(vind), _receptor(vind)
    dual = abs(float((w * ci).sum()) - float((yi * s).sum())) / abs(float((w * ci).sum()))
    print(
        f"kvarter {ti['obekanta']} obekanta: iterativ {ti['totalt_s']:.2f} s, "
        f"direkt {td['totalt_s']:.2f} s; rel fel {fel:.1e}, adj {fel_adj:.1e}, "
        f"dualitet {dual:.1e}, massbalans {ti['massbalans']:.1e}"
    )
    assert fel < 1e-8
    assert fel_adj < 1e-8
    assert dual < 1e-8
    assert ti["massbalans"] < 1e-10
    assert ti["divergens_rel"] < 1e-10


def test_prestanda_1e5_obekanta() -> None:
    """≈ 1·10⁵ obekanta med standardinställningarna; ska ta klart under 20 s."""
    vind = _kvarter((80, 48, 28))
    c, y, tider = _kor(vind)
    print(f"prestanda standard: {tider}")
    assert tider["obekanta"] > 90_000
    assert tider["massbalans"] < 1e-10
    assert tider["divergens_rel"] < 1e-10
    assert c[vind.grid.luft].min() >= -1e-14 * c.max()
    assert y[vind.grid.luft].min() >= -1e-14 * y.max()
    if not LANGSAMMA:
        grans_s = 20.0 * float(os.environ.get("FLODEN_TIDSFAKTOR", "1"))
        assert tider["totalt_s"] < grans_s, (
            f"standardlösaren har blivit långsam ({tider['totalt_s']:.2f} s >= {grans_s:.1f} s)"
        )


@pytest.mark.skipif(not LANGSAMMA, reason="långsam (≈ 30 s): sätt FLODEN_LANGSAMMA_TESTER=1")
def test_prestanda_1e5_ilu_som_forut() -> None:
    """Jämförelse med den tidigare lösaren (ILU) på samma fall; samma svar."""
    vind = _kvarter((80, 48, 28))
    c, y, tider = _kor(vind)
    c_ilu, y_ilu, tider_ilu = _kor(vind, forkonditionering="ilu")
    fel = float(np.max(np.abs(c - c_ilu)) / np.max(c))
    fel_adj = float(np.max(np.abs(y - y_ilu)) / np.max(y))
    print(f"SGS {tider['totalt_s']:.1f} s, ILU {tider_ilu['totalt_s']:.1f} s; "
          f"rel skillnad {fel:.1e}, adj {fel_adj:.1e}")
    assert fel < 1e-8
    assert fel_adj < 1e-8


def test_prestanda_tvd_1e5() -> None:
    """TVD (två Picardpass + slutlösning) på ≈ 1·10⁵ obekanta."""
    vind = _kvarter((80, 48, 28))
    s, w = _vagkalla(vind), _receptor(vind)
    t0 = time.perf_counter()
    op = TransportOperator(vind, Amne("NOx"), schema="tvd", referenskallor_g_s=s)
    t1 = time.perf_counter()
    c = op.los(s)
    y = op.los_adjungerad(w)
    t2 = time.perf_counter()
    dual = abs(float((w * c).sum()) - float((y * s).sum())) / abs(float((w * c).sum()))
    mb = op.massbalans(s, c)["rel_fel"]
    print(f"TVD 1e5: bygg + frysning {t1 - t0:.2f} s, framåt + adjungerad {t2 - t1:.2f} s, "
          f"dualitet {dual:.1e}, massbalans {mb:.1e}, min/max {op.positivitet:.1e}")
    assert dual < 1e-8
    assert mb < 1e-10
    assert op.positivitet >= -1e-12
    if not LANGSAMMA:
        grans_s = 20.0 * float(os.environ.get("FLODEN_TIDSFAKTOR", "1"))
        assert t2 - t0 < grans_s, f"TVD tog {t2 - t0:.2f} s (gräns < {grans_s:.1f} s)"
