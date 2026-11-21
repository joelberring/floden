# SPDX-License-Identifier: MIT
"""Grov validering av LBM-LES: återanslutningslängd bakom en ytmonterad kub (L3, `docs/LUFT.md` 6).

Litteraturvärden
----------------
* **Hearst, Gomit & Ganapathisubramani**, "Effect of turbulence on the wake of a
  wall-mounted cube", J. Fluid Mech. (2016); författarversion University of Southampton
  ePrints 400432 (https://eprints.soton.ac.uk/400432/). Kub i turbulent gränsskikt,
  `h/δ ≈ 0,47`, `Re_h = 11,4–28,8 · 10³`, fyra fall med friströmsturbulens 0,5–8,9 %.
  Medelåteranslutningen i symmetriplanet är `x_R/h = 1,9` räknat från kubens *centrum*
  i alla fyra fallen, "1.4h downstream of the rear face of the cube".
  **Facit här: `x_r/H = 1,4` från bakre kubytan.**
* **Martinuzzi & Tropea** (1993), J. Fluids Eng. 115:85–92: kub i fullt utvecklad
  kanalströmning (kanalhöjd 2H, `Re_H = 40 000`). Tabell i ERCOFTAC-wikin UFR 3-14
  (https://www.kbwiki.ercoftac.org/w/index.php/UFR_3-14_Evaluation): `X_F = 1,040`
  (avlösning framför kuben) och `X_R = 1,612` (återanslutning), i enheter av H.
  Kanalfall och annat inflöde; används här bara som rimlighetskontroll för `x_F`
  (framför kuben, räknat från framsidan).

Uppställning (grovt rutnät)
---------------------------
Kub med `H = 8` celler (dx = 1 m), 3H uppströms, 7H nedströms, bredd 5H (periodisk),
höjd 3H med friktionsfritt tak (`h/δ` ≈ 0,33 i stället för 0,47). Log-profil
(`z0 = 0,1` m, `z_ref = H`), syntetiska virvlar (SEM) med `I = 0,15` och integral
längdskala `H/2` (testval, inga litteraturvärden), väggmodell `loglag`, de rekommenderade
randvillkoren (`extrapolation`/`tryck`/`profil`, se `luft.lbm`), u_lattice 0,05
(max Mach uppmätt 0,213; "auto" ger ≈ 0,03 och 1,7 gånger längre körning).
`x_r` = där medelvärdet av `u_x` längs centrumlinjen i första cellagret (z = 0,5 m =
0,06 H) byter tecken från negativt till positivt bakom kuben, linjärt interpolerat.

Uppmätt 2026-11-21 (CPU, numpy, 2 kärnor, 7040 steg på 65 s):
`x_r/H = 3,09` (faktor 2,2 mot 1,4), `x_F/H = 1,04`. Med `H = 6` celler (22 s) blev
`x_r/H = 3,9–4,3` och utan väggmodell 5,5; återcirkulationen krymper alltså mot
litteraturvärdet när rutnätet förfinas. Gränsen nedan (faktor 2,5) är satt efter vad det
grova rutnätet klarar och är **ingen skarp validering**: den kräver server (H ≥ 20–30
celler, längre medeltid) och jämförelse med CEDVAL A1-1 / Hearst m.fl. i fler punkter.

Testet tar över 20 s och körs bara med `FLODEN_LANGSAMMA_TESTER=1`.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from luft.lbm import LbmParametrar, kor_les
from luft.typer import Inflode, Voxelgrid

LANGSAM = os.environ.get("FLODEN_LANGSAMMA_TESTER", "") == "1"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not LANGSAM, reason="långsamt test: sätt FLODEN_LANGSAMMA_TESTER=1"),
]

XR_LITTERATUR = 1.4  # Hearst, Gomit & Ganapathisubramani (2016), från bakre kubytan
XF_LITTERATUR = 1.040  # Martinuzzi & Tropea (1993) via ERCOFTAC UFR 3-14
FAKTOR_GROVT = 2.5


def _teckenbyte(x: np.ndarray, u: np.ndarray, start: int, steg: int) -> float | None:
    """Första läge från `start` i riktning `steg` där `u` går från < 0 till ≥ 0."""
    i = start
    while 0 <= i + steg < u.size:
        if u[i] < 0 <= u[i + steg]:
            return float(x[i] + (x[i + steg] - x[i]) * (-u[i]) / (u[i + steg] - u[i]))
        i += steg
    return None


def test_ateranslutning_bakom_kub() -> None:
    h = 8
    uppstroms, nedstroms, bredd, hojd = 3, 7, 5, 3
    nx, ny, nz = (uppstroms + 1 + nedstroms) * h, bredd * h, hojd * h
    fast = np.zeros((nx, ny, nz), dtype=np.bool_)
    i0 = uppstroms * h
    j0 = ny // 2 - h // 2
    fast[i0 : i0 + h, j0 : j0 + h, :h] = True
    grid = Voxelgrid(dx_m=1.0, fast=fast, lad_m2_m3=np.zeros(fast.shape))
    inflode = Inflode(u_ref_m_s=5.0, z_ref_m=float(h), z0_m=0.1, turbulensintensitet=0.15)
    param = LbmParametrar(
        u_lattice=0.05,
        upprampning_genomstromningar=1.5,
        medel_genomstromningar=2.5,
        vaggmodell="loglag",
        inflodesturbulens="virvlar",
        inflode_langdskala_m=h / 2,
        inflodesrand="extrapolation",
        utflodesrand="tryck",
        start="profil",
    )
    t0 = time.perf_counter()
    vind = kor_les(grid, inflode, param)
    tid = time.perf_counter() - t0

    ux = vind.u_m_s[0]
    linje = 0.5 * (ux[:, ny // 2 - 1, 0] + ux[:, ny // 2, 0])
    x = np.arange(nx) + 0.5
    bak = i0 + h
    x_ater = _teckenbyte(x, linje, bak, 1)
    x_fram = _teckenbyte(x, linje, i0 - 1, -1)
    assert x_ater is not None, "Ingen återanslutning bakom kuben i domänen."
    assert x_fram is not None, "Ingen avlösning framför kuben."
    xr = (x_ater - bak) / h
    xf = (i0 - x_fram) / h
    print(
        f"\nKubvalidering H={h}: x_r/H = {xr:.2f} (litteratur {XR_LITTERATUR}, "
        f"kvot {xr / XR_LITTERATUR:.2f}), x_F/H = {xf:.2f} (M&T {XF_LITTERATUR}), "
        f"{vind.metadata['steg_totalt']} steg på {tid:.0f} s, "
        f"max Mach {vind.metadata['max_mach']:.3f}"
    )
    assert XR_LITTERATUR / FAKTOR_GROVT < xr < XR_LITTERATUR * FAKTOR_GROVT
    assert XF_LITTERATUR / 1.5 < xf < XF_LITTERATUR * 1.5
    assert float(linje[bak : bak + 2 * h].min()) < 0.0
