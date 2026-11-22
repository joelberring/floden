# SPDX-License-Identifier: MIT
"""Snabb massbevarande 3D-skissvind (Röckle/QUIC-typ + Poisson-projektion).

Ger ett fysiskt rimligt, diskret divergensfritt 3D-vindfält (`Vindfalt`) på
< 1 sekund på CPU utan GPU eller LBM-tidstegning. Avsett för:
- interaktivt skissläge (snabb återkoppling på vindkomfort och partikelspridning),
- förhandskontroll innan dyr 3D-LBM (`kor_les`) körs för slutrapport.

Metod
-----
1. **Startfält**: neutral log-lag `inflode.profil_m_s(z)` dämpad i vegetation
   (`lad_m2_m3`) samt empirisk Röckle-inspirerad lä-/recirkulationszon bakom
   byggnader.
2. **Massbevarande projektion**: ytflödena projiceras till exakt diskret
   divergensfrihet (`∇·u = 0`) via en 3D-Poissonekvation löst med
   aggregeringsmultigrid + CG (`_AggregeringsMg` ur `luft.transport`). Detta
   skapar automatiskt hörnacceleration, upp-/nedströmning över tak och
   kanalisering i gaturum.
3. **Turbulent viskositet**: blandningslängdsmodell (Prandtl + skjuvning) som
   skalar linjärt med `u_ref`, kompatibel med `TransportOperator` och
   `Vindfalt.skalat()`.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from luft.transport import _AggregeringsMg, _par, _skiva
from luft.typer import FloatArray, Inflode, LuftFel, Vindfalt, Voxelgrid

_KAPPA = 0.41


def _bygg_startfalt(
    grid: Voxelgrid,
    inflode: Inflode,
    cd_vegetation: float,
) -> FloatArray:
    """Log-lag med vegetationsdämpning och enkel Röckle-läzon bakom byggnader."""
    nx, ny, nz = grid.form
    dx = grid.dx_m
    fast = grid.fast
    luft = ~fast

    z = grid.cellcentrum_z_m()
    u_log = inflode.profil_m_s(z)  # (nz,)
    u = np.zeros((3, nx, ny, nz), dtype=np.float64)
    u[0] = np.where(luft, u_log[None, None, :], 0.0)

    # Vegetationsdämpning
    if cd_vegetation > 0.0 and np.any(grid.lad_m2_m3 > 0.0):
        damp = 1.0 / (1.0 + cd_vegetation * np.asarray(grid.lad_m2_m3, dtype=np.float64) * dx)
        u[0] *= damp

    # Lokal byggnadshöjd per (i, j) och enkel Röckle-läzon längs +x
    har_fast = np.any(fast, axis=2)
    if bool(np.any(har_fast)):
        # Högsta fasta lager + 1 -> höjd i celler per (i, j)
        k_idx = np.arange(1, nz + 1, dtype=np.int64)[None, None, :]
        hojd_celler = np.max(np.where(fast, k_idx, 0), axis=2)  # (nx, ny)
        # Spåra avstånd i x-celler från senaste byggnad uppströms och dess höjd
        dist_vagg = np.full((ny, nz), 10**9, dtype=np.int64)
        h_vagg = np.zeros((ny, nz), dtype=np.int64)
        for i in range(nx):
            fi = fast[i]  # (ny, nz)
            hi = hojd_celler[i][:, None]  # (ny, 1)
            # Celler som är fasta sätter ny väggkälla för nedströms luftceller
            dist_vagg = np.where(fi, 0, dist_vagg + 1)
            h_vagg = np.where(fi, hi, h_vagg)

            luft_i = ~fi
            aktiv_vak = luft_i & (h_vagg > 0) & (dist_vagg > 0)
            if not bool(np.any(aktiv_vak)):
                continue
            # Läzonens längd: ca 1,0·H recirkulation, återhämtning ut till 2,5·H
            d_rel = dist_vagg / np.maximum(h_vagg, 1)
            k_rel = (np.arange(nz, dtype=np.float64)[None, :] + 0.5) / np.maximum(h_vagg, 1)
            under_tak = k_rel <= 1.05
            i_kavitet = aktiv_vak & under_tak & (d_rel <= 1.0)
            i_vak = aktiv_vak & under_tak & (d_rel > 1.0) & (d_rel <= 2.5)
            if bool(np.any(i_kavitet)):
                # Svag återströmning i recirkulationsbubblan
                faktor = -0.15 * (1.0 - d_rel)
                u[0, i] = np.where(i_kavitet, u[0, i] * faktor, u[0, i])
            if bool(np.any(i_vak)):
                faktor = 0.2 + 0.8 * ((d_rel - 1.0) / 1.5)
                u[0, i] = np.where(i_vak, u[0, i] * faktor, u[0, i])

    return u


def kor_snabbvind(
    grid: Voxelgrid,
    inflode: Inflode,
    *,
    periodisk_y: bool = True,
    cd_vegetation: float = 0.2,
    tol: float = 1e-6,
    max_iter: int = 300,
) -> Vindfalt:
    """Beräknar ett massbevarande 3D-skissvindfält på CPU (< 1 s för typiska kvarter).

    Returnerar ett standardiserat `Vindfalt` som kan matas direkt in i
    `TransportOperator`, `vindkomfort` och `Vindbibliotek`.
    """
    t0 = time.perf_counter()
    nx, ny, nz = grid.form
    dx = grid.dx_m
    yta_m2 = dx * dx
    luft_3d = grid.luft
    luft = luft_3d.ravel()
    n = int(luft.sum())
    if n == 0:
        raise LuftFel("Rutnätet har inga luftceller.")

    idx = np.full(luft.size, -1, dtype=np.int64)
    idx[luft] = np.arange(n, dtype=np.int64)
    lin = np.arange(luft.size, dtype=np.int64).reshape(grid.form)

    u0 = _bygg_startfalt(grid, inflode, cd_vegetation)
    u_flat = [u0[a].ravel() for a in range(3)]

    # Bygg ytflöden (samma konvention som TransportOperator)
    inre_l_list: list[np.ndarray[Any, np.dtype[np.int64]]] = []
    inre_r_list: list[np.ndarray[Any, np.dtype[np.int64]]] = []
    inre_f_list: list[FloatArray] = []
    inre_axel_list: list[np.ndarray[Any, np.dtype[np.int64]]] = []

    for axel in range(3):
        periodisk = axel == 1 and periodisk_y
        if periodisk and ny == 1:
            continue
        gl, gr = _par(lin, axel, periodisk)
        bada = luft[gl] & luft[gr]
        gl_b, gr_b = gl[bada], gr[bada]
        inre_l_list.append(idx[gl_b])
        inre_r_list.append(idx[gr_b])
        inre_f_list.append(0.5 * (u_flat[axel][gl_b] + u_flat[axel][gr_b]) * yta_m2)
        inre_axel_list.append(np.full(gl_b.size, axel, dtype=np.int64))

    inre_l = np.concatenate(inre_l_list) if inre_l_list else np.empty(0, dtype=np.int64)
    inre_r = np.concatenate(inre_r_list) if inre_r_list else np.empty(0, dtype=np.int64)
    inre_f = np.concatenate(inre_f_list) if inre_f_list else np.empty(0, dtype=np.float64)
    inre_axel = np.concatenate(inre_axel_list) if inre_axel_list else np.empty(0, dtype=np.int64)

    # Domänränder: inflöde (typ 0, givet flöde), utflöde/tak/öppna sidor (typ 1, φ = 0)
    rand_p_list: list[np.ndarray[Any, np.dtype[np.int64]]] = []
    rand_f_list: list[FloatArray] = []
    rand_typ_list: list[np.ndarray[Any, np.dtype[np.int64]]] = []
    rand_axel_list: list[np.ndarray[Any, np.dtype[np.int64]]] = []
    rand_tecken_list: list[np.ndarray[Any, np.dtype[np.int64]]] = []

    def _lagg_till_rand(axel: int, index: int, tecken: int, typ: int) -> None:
        g = _skiva(lin, axel, index)
        g = g[luft[g]]
        if g.size == 0:
            return
        rand_p_list.append(idx[g])
        rand_f_list.append(tecken * u_flat[axel][g] * yta_m2)
        rand_typ_list.append(np.full(g.size, typ, dtype=np.int64))
        rand_axel_list.append(np.full(g.size, axel, dtype=np.int64))
        rand_tecken_list.append(np.full(g.size, tecken, dtype=np.int64))

    _lagg_till_rand(0, 0, -1, 0)       # inflöde (Neumann)
    _lagg_till_rand(0, nx - 1, +1, 1)  # utflöde (Dirichlet φ=0)
    _lagg_till_rand(2, nz - 1, +1, 1)  # tak (Dirichlet φ=0)
    if not periodisk_y:
        _lagg_till_rand(1, 0, -1, 1)
        _lagg_till_rand(1, ny - 1, +1, 1)

    rand_p = np.concatenate(rand_p_list)
    rand_f = np.concatenate(rand_f_list)
    rand_typ = np.concatenate(rand_typ_list)
    rand_axel = np.concatenate(rand_axel_list)
    rand_tecken = np.concatenate(rand_tecken_list)

    # Poissonekvation L φ = -div(F) på luftceller
    div = (
        np.bincount(inre_l, inre_f, minlength=n)
        - np.bincount(inre_r, inre_f, minlength=n)
        + np.bincount(rand_p, rand_f, minlength=n)
    )
    oppet = rand_typ == 1
    p_oppet = rand_p[oppet]
    diag = (
        np.bincount(inre_l, minlength=n)
        + np.bincount(inre_r, minlength=n)
        + np.bincount(p_oppet, minlength=n)
    ).astype(np.float64)
    rader = np.concatenate([inre_l, inre_r, np.arange(n)])
    kolumner = np.concatenate([inre_r, inre_l, np.arange(n)])
    varden = np.concatenate([-np.ones(inre_l.size), -np.ones(inre_r.size), diag])
    lap = sp.csr_matrix((varden, (rader, kolumner)), shape=(n, n))
    lap.sum_duplicates()

    if n <= 4000:
        phi = np.asarray(spla.spsolve(lap.tocsc(), -div), dtype=np.float64)
    else:
        koord = np.argwhere(luft_3d).astype(np.int64)
        mg = _AggregeringsMg(lap, koord)
        linop = spla.LinearOperator((n, n), matvec=mg.losa, dtype=np.float64)
        phi, info = spla.cg(lap, -div, rtol=tol, atol=0.0, maxiter=max_iter, M=linop)
        if info != 0:
            raise LuftFel(f"Snabbvind Poisson-projektion konvergerade inte (CG info={info}).")

    inre_f_proj = inre_f - (phi[inre_r] - phi[inre_l])
    rand_f_proj = rand_f.copy()
    rand_f_proj[oppet] = rand_f[oppet] + phi[p_oppet]

    div_efter = (
        np.bincount(inre_l, inre_f_proj, minlength=n)
        - np.bincount(inre_r, inre_f_proj, minlength=n)
        + np.bincount(rand_p, rand_f_proj, minlength=n)
    )
    flodesskala = max(float(np.max(np.abs(inre_f_proj), initial=0.0)), 1e-30)
    div_rel = float(np.max(np.abs(div_efter))) / flodesskala

    # Rekonstruera cellcentrumhastigheter (u_x, u_y, u_z) från projicerade ytflöden
    u_ut = np.zeros((3, nx, ny, nz), dtype=np.float64)
    for axel in range(3):
        summa_f = np.zeros(n, dtype=np.float64)
        m_inre = inre_axel == axel
        if bool(np.any(m_inre)):
            np.add.at(summa_f, inre_l[m_inre], inre_f_proj[m_inre])
            np.add.at(summa_f, inre_r[m_inre], inre_f_proj[m_inre])
        m_rand = rand_axel == axel
        if bool(np.any(m_rand)):
            # rand_f_proj har tecken +1 utåt vid övre rand och -1 inåt vid undre rand ->
            # multiplicera med rand_tecken ger fysisk riktning (+axel).
            np.add.at(
                summa_f,
                rand_p[m_rand],
                rand_tecken[m_rand] * rand_f_proj[m_rand],
            )
        u_axel = np.zeros(luft.size, dtype=np.float64)
        u_axel[luft] = 0.5 * summa_f / yta_m2
        u_ut[axel] = u_axel.reshape(grid.form)

    # Blandningslängdsmodell för turbulent viskositet nu_t (skalar linjärt med u_ref)
    z = grid.cellcentrum_z_m()
    u_star = _KAPPA * inflode.u_ref_m_s / np.log((inflode.z_ref_m + inflode.z0_m) / inflode.z0_m)
    z_eff = np.minimum(z, 0.5 * nz * dx)
    nu_bakgrund = _KAPPA * u_star * z_eff  # (nz,)

    # Skjuvningsbidrag från hastighetsgradienter (fångar förhöjd turbulens i vak & gaturum)
    du_x = np.gradient(u_ut[0], dx)
    du_y = np.gradient(u_ut[1], dx)
    du_z = np.gradient(u_ut[2], dx)
    skjuvning = np.sqrt(
        sum(g * g for g in (*du_x, *du_y, *du_z))
    )
    l_mix = np.minimum(_KAPPA * z, 2.0 * dx)[None, None, :]
    nu_t = np.where(luft_3d, nu_bakgrund[None, None, :] + (l_mix * l_mix) * skjuvning, 0.0)

    kortid_s = time.perf_counter() - t0
    return Vindfalt(
        grid=grid,
        inflode=inflode,
        u_m_s=u_ut,
        nu_t_m2_s=nu_t,
        medelperiod_s=0.0,
        metadata={
            "metod": "snabbvind_poisson",
            "divergens_rel": div_rel,
            "kortid_s": kortid_s,
            "periodisk_y": periodisk_y,
        },
    )
