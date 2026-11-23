"""Kretsteoretisk konnektivitet (McRae m.fl. 2008): Laplacian, R_eff, flaskhalsar och dR/dG.

Bygger den symmetriska positivt semidefinita graf-Laplacianen L över aktiva celler med
harmonisk kantkonduktans G_uv = 2 g_u g_v / ((g_u + g_v) * l_uv). Efter Dirichlet-låsning
av käll- och målfläck är delsystemet L_II en symmetrisk positivt definit M-matris (Stieltjes).
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import spsolve

from ekologi.typer import EkologiFel, FloatRaster, KretsParResultat, MotstandsYta

_STEG_4: tuple[tuple[int, int, float], ...] = ((0, 1, 1.0), (1, 0, 1.0))
_STEG_8: tuple[tuple[int, int, float], ...] = (
    (0, 1, 1.0),
    (1, 0, 1.0),
    (1, 1, math.sqrt(2.0)),
    (1, -1, math.sqrt(2.0)),
)


def bygg_kantlista(
    konduktans: FloatRaster,
    *,
    grannskap: int = 4,
) -> tuple[NDArray[np.int32], NDArray[np.int32], NDArray[np.float64]]:
    """Returnerar riktade oriktade par `(u, v, G_uv)` med `u < v` för alla aktiva grannkanter."""
    if grannskap not in (4, 8):
        raise EkologiFel(f"grannskap måste vara 4 eller 8, fick {grannskap}.")
    g = np.asarray(konduktans, dtype=np.float64)
    ny, nx = g.shape
    steg = _STEG_4 if grannskap == 4 else _STEG_8

    u_delar: list[NDArray[np.int32]] = []
    v_delar: list[NDArray[np.int32]] = []
    w_delar: list[NDArray[np.float64]] = []

    for dr, dc, langd in steg:
        r0 = max(0, -dr)
        r1 = min(ny, ny - dr)
        c0 = max(0, -dc)
        c1 = min(nx, nx - dc)
        if r0 >= r1 or c0 >= c1:
            continue
        ga = g[r0:r1, c0:c1]
        gb = g[r0 + dr : r1 + dr, c0 + dc : c1 + dc]
        aktiv = (ga > 0.0) & (gb > 0.0)
        if not np.any(aktiv):
            continue
        rr, cc = np.nonzero(aktiv)
        rr_g = rr + r0
        cc_g = cc + c0
        u_idx = (rr_g * nx + cc_g).astype(np.int32)
        v_idx = ((rr_g + dr) * nx + (cc_g + dc)).astype(np.int32)
        ga_a = ga[rr, cc]
        gb_a = gb[rr, cc]
        g_kant = (2.0 * ga_a * gb_a) / ((ga_a + gb_a) * langd)
        u_delar.append(u_idx)
        v_delar.append(v_idx)
        w_delar.append(g_kant)

    if not u_delar:
        return (
            np.zeros(0, dtype=np.int32),
            np.zeros(0, dtype=np.int32),
            np.zeros(0, dtype=np.float64),
        )
    return np.concatenate(u_delar), np.concatenate(v_delar), np.concatenate(w_delar)


def los_kretspar(
    motstand: MotstandsYta,
    flack_a_mask: NDArray[np.bool_],
    flack_b_mask: NDArray[np.bool_],
    *,
    grannskap: int = 4,
    fran_flack_id: int = 1,
    till_flack_id: int = 2,
    extra_kant_konduktans: tuple[NDArray[np.int32], NDArray[np.int32], NDArray[np.float64]]
    | None = None,
) -> KretsParResultat:
    """Löser Dirichlet-kretsekvationen `L v = 0` med `v = 1` på fläck A och `v = 0` på fläck B.

    Returnerar effektivt motstånd `R_eff = 1 / I_tot`, normaliserad strömtäthet `J_u` i `[0, 1]`
    och exakt 1-lösnings adjungerad känslighet `dR_eff / dG` summerad per cell.
    """
    g = motstand.konduktans
    ny, nx = g.shape
    n_tot = ny * nx

    mask_a = np.asarray(flack_a_mask, dtype=np.bool_)
    mask_b = np.asarray(flack_b_mask, dtype=np.bool_)
    if mask_a.shape != g.shape or mask_b.shape != g.shape:
        raise EkologiFel("flack_a_mask och flack_b_mask måste ha samma form som motståndsrastret.")
    if np.any(mask_a & mask_b):
        raise EkologiFel("flack_a_mask och flack_b_mask får inte överlappa.")

    a_flat = mask_a.ravel() & (g.ravel() > 0.0)
    b_flat = mask_b.ravel() & (g.ravel() > 0.0)
    if not np.any(a_flat) or not np.any(b_flat):
        return _ej_sammankopplad(g.shape, fran_flack_id, till_flack_id)

    if extra_kant_konduktans is not None:
        u_idx, v_idx, g_kant = extra_kant_konduktans
    else:
        u_idx, v_idx, g_kant = bygg_kantlista(g, grannskap=grannskap)

    if u_idx.size == 0:
        return _ej_sammankopplad(g.shape, fran_flack_id, till_flack_id)

    # Bygg symmetrisk grannmatris A_mat med kantkonduktanser
    rader_sym = np.concatenate([u_idx, v_idx])
    kols_sym = np.concatenate([v_idx, u_idx])
    data_sym = np.concatenate([g_kant, g_kant])
    adj = sparse.csr_matrix((data_sym, (rader_sym, kols_sym)), shape=(n_tot, n_tot))

    # Kontrollera vilka sammanhängande komponenter som förbinder A och B
    _, komp = connected_components(adj, directed=False)
    komp_a = set(int(c) for c in komp[a_flat])
    komp_b = set(int(c) for c in komp[b_flat])
    gemensamma = komp_a & komp_b
    if not gemensamma:
        return _ej_sammankopplad(g.shape, fran_flack_id, till_flack_id)

    i_komp = np.isin(komp, list(gemensamma))
    a_aktiv = a_flat & i_komp
    b_aktiv = b_flat & i_komp
    inre = i_komp & (~a_aktiv) & (~b_aktiv)

    # Bygg graf-Laplacian L = diag(sum_v G_uv) - A_mat
    diag = np.asarray(adj.sum(axis=1)).ravel()
    lap = sparse.diags(diag, format="csr") - adj

    v_flat = np.zeros(n_tot, dtype=np.float64)
    v_flat[a_aktiv] = 1.0
    v_flat[b_aktiv] = 0.0

    inre_idx = np.flatnonzero(inre)
    if inre_idx.size > 0:
        a_idx = np.flatnonzero(a_aktiv)
        l_ii = lap[inre_idx][:, inre_idx].tocsc()
        rhs = -np.asarray(lap[inre_idx][:, a_idx].sum(axis=1)).ravel()
        v_inre = np.asarray(spsolve(l_ii, rhs), dtype=np.float64)
        v_flat[inre_idx] = np.clip(v_inre, 0.0, 1.0)

    # Total utström ur A och inström i B (strömkonserveringskontroll)
    dv_kant = v_flat[u_idx] - v_flat[v_idx]
    strom_kant = g_kant * dv_kant  # positiv från u till v

    # Utström från A: kanter med en ändpunkt i A och andra ej i A
    a_u = a_aktiv[u_idx]
    a_v = a_aktiv[v_idx]
    i_ut_a = float(np.sum(strom_kant[a_u & ~a_v]) - np.sum(strom_kant[a_v & ~a_u]))

    b_u = b_aktiv[u_idx]
    b_v = b_aktiv[v_idx]
    i_in_b = float(np.sum(strom_kant[~b_u & b_v]) - np.sum(strom_kant[~b_v & b_u]))

    if i_ut_a <= 0.0 or not math.isfinite(i_ut_a):
        return _ej_sammankopplad(g.shape, fran_flack_id, till_flack_id)

    r_eff = 1.0 / i_ut_a
    balans_fel = abs(i_ut_a - i_in_b) / max(abs(i_ut_a), 1e-30)

    # Normaliserad strömtäthet per cell: J_u = (1 / (2 * I_tot)) * sum_{w~u} |I_uw|
    abs_i = np.abs(strom_kant)
    j_flat = np.zeros(n_tot, dtype=np.float64)
    np.add.at(j_flat, u_idx, abs_i)
    np.add.at(j_flat, v_idx, abs_i)
    j_flat *= 0.5 / i_ut_a
    # Käll- och målceller har bara ena sidans flöde i J_u; skala inte upp över 1
    j_flat = np.clip(j_flat, 0.0, 1.0)

    # Exakt adjungerad kantkänslighet: dR_eff / dG_uv = -((v_u - v_v) / I_tot)^2 <= 0
    dr_dg_kant = -((dv_kant / i_ut_a) ** 2)
    kansl_flat = np.zeros(n_tot, dtype=np.float64)
    np.add.at(kansl_flat, u_idx, 0.5 * dr_dg_kant)
    np.add.at(kansl_flat, v_idx, 0.5 * dr_dg_kant)

    return KretsParResultat(
        fran_flack_id=fran_flack_id,
        till_flack_id=till_flack_id,
        effektivt_motstand=r_eff,
        total_strom=i_ut_a,
        strombalans_fel_rel=balans_fel,
        potential=v_flat.reshape(g.shape),
        stromtathet=j_flat.reshape(g.shape),
        kanslighet_cell=kansl_flat.reshape(g.shape),
        sammankopplad=True,
    )


def _ej_sammankopplad(
    form: tuple[int, int], fran_flack_id: int, till_flack_id: int
) -> KretsParResultat:
    noll = np.zeros(form, dtype=np.float64)
    return KretsParResultat(
        fran_flack_id=fran_flack_id,
        till_flack_id=till_flack_id,
        effektivt_motstand=math.inf,
        total_strom=0.0,
        strombalans_fel_rel=0.0,
        potential=noll.copy(),
        stromtathet=noll.copy(),
        kanslighet_cell=noll.copy(),
        sammankopplad=False,
    )
