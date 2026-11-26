"""Kretsteoretisk konnektivitet (McRae m.fl. 2008): Laplacian, R_eff, flaskhalsar och dR/dG.

Bygger den symmetriska positivt semidefinita graf-Laplacianen L över aktiva celler med
harmonisk kantkonduktans G_uv = 2 g_u g_v / ((g_u + g_v) * l_uv). Efter Dirichlet-låsning
av käll- och målfläck är delsystemet L_II en symmetrisk positivt definit M-matris (Stieltjes).
"""

from __future__ import annotations

import math
import os

import numpy as np
from numpy.typing import NDArray
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import LinearOperator, cg, spsolve

from ekologi.typer import EkologiFel, FloatRaster, KretsParResultat, MotstandsYta

STANDARD_MAX_EKOLOGI_CELLER = 1_000_000
_FLODEN_MAX_EKOLOGI_CELLER_ENV = "FLODEN_MAX_EKOLOGI_CELLER"
STANDARD_CG_TROSKEL = 25_000
_CG_FALLBACK_MAX_N = 100_000
_CG_MAX_REL_RESIDUAL = 1e-6
_CG_MAX_STROMBALANS_FEL = 1e-6

_STEG_4: tuple[tuple[int, int, float], ...] = ((0, 1, 1.0), (1, 0, 1.0))
_STEG_8: tuple[tuple[int, int, float], ...] = (
    (0, 1, 1.0),
    (1, 0, 1.0),
    (1, 1, math.sqrt(2.0)),
    (1, -1, math.sqrt(2.0)),
)


def max_ekologi_celler() -> int:
    """Läser celltaket för ekologiberäkningar ur `FLODEN_MAX_EKOLOGI_CELLER`.

    Tomt eller `0` ger standard (`STANDARD_MAX_EKOLOGI_CELLER` = 1 000 000 celler).
    Ogiltigt värde (icke-heltal eller negativt) ger `EkologiFel` (inga tysta fel).
    """
    raw = os.environ.get(_FLODEN_MAX_EKOLOGI_CELLER_ENV)
    if raw is None or not raw.strip():
        return STANDARD_MAX_EKOLOGI_CELLER
    text = raw.strip()
    try:
        val = int(text)
    except ValueError as exc:
        raise EkologiFel(
            f"Ogiltigt värde i {_FLODEN_MAX_EKOLOGI_CELLER_ENV}={raw!r}: "
            "ange ett icke-negativt heltal (0 eller tomt = standard)."
        ) from exc
    if val < 0:
        raise EkologiFel(
            f"Ogiltigt värde i {_FLODEN_MAX_EKOLOGI_CELLER_ENV}={raw!r}: "
            "värdet får inte vara negativt (0 eller tomt = standard)."
        )
    return STANDARD_MAX_EKOLOGI_CELLER if val == 0 else val


def kontrollera_ekologi_celler(n_celler: int, *, max_celler: int | None = None) -> None:
    """Fail-fast kontroll av cellantalet innan glesa krets- eller kostnadsmatriser byggs."""
    grans = max_ekologi_celler() if max_celler is None else max_celler
    if n_celler > grans:
        raise EkologiFel(
            f"Ekologirastret omfattar {n_celler} celler, vilket överstiger "
            f"minnesgränsen på {grans} celler ({_FLODEN_MAX_EKOLOGI_CELLER_ENV}). "
            "Välj ett mindre område, öka cellstorleken eller höj "
            f"{_FLODEN_MAX_EKOLOGI_CELLER_ENV}."
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
    kontrollera_ekologi_celler(int(ny * nx))
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


def _berakna_strom_och_balans(
    v_flat: NDArray[np.float64],
    u_idx: NDArray[np.int32],
    v_idx: NDArray[np.int32],
    g_kant: NDArray[np.float64],
    a_aktiv: NDArray[np.bool_],
    b_aktiv: NDArray[np.bool_],
) -> tuple[NDArray[np.float64], NDArray[np.float64], float, float, float]:
    """Beräknar kantspänningsfall, kantströmmar, utström ur A, inström i B och balansfel."""
    dv_kant = v_flat[u_idx] - v_flat[v_idx]
    strom_kant = g_kant * dv_kant  # positiv från u till v

    # Utström från A: kanter med en ändpunkt i A och andra ej i A
    a_u = a_aktiv[u_idx]
    a_v = a_aktiv[v_idx]
    i_ut_a = float(np.sum(strom_kant[a_u & ~a_v]) - np.sum(strom_kant[a_v & ~a_u]))

    b_u = b_aktiv[u_idx]
    b_v = b_aktiv[v_idx]
    i_in_b = float(np.sum(strom_kant[~b_u & b_v]) - np.sum(strom_kant[~b_v & b_u]))

    balans_fel = abs(i_ut_a - i_in_b) / max(abs(i_ut_a), 1e-30)
    return dv_kant, strom_kant, i_ut_a, i_in_b, balans_fel


def _los_inre_med_jacobi_cg(
    l_ii: sparse.csr_matrix,
    rhs: NDArray[np.float64],
) -> tuple[NDArray[np.float64], int, float, bool]:
    """Löser SPD-systemet `L_II v = rhs` med Jacobi-förkonditionerad CG i O(N) minne."""
    n = int(rhs.size)
    diag_ii = np.asarray(l_ii.diagonal(), dtype=np.float64)
    if np.any(~np.isfinite(diag_ii)) or np.any(diag_ii <= 0.0):
        return np.zeros(n, dtype=np.float64), -1, math.inf, False

    inv_diag = 1.0 / diag_ii

    def _jacobi_matvec(x: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.asarray(inv_diag * x, dtype=np.float64)

    precond = LinearOperator((n, n), matvec=_jacobi_matvec, dtype=np.float64)
    maxiter = max(5000, 10 * int(math.sqrt(n)))
    v_cg, info_raw = cg(
        l_ii,
        rhs,
        rtol=1e-10,
        atol=0.0,
        maxiter=maxiter,
        M=precond,
    )
    info = int(info_raw)
    v_inre = np.asarray(v_cg, dtype=np.float64)
    if info != 0 or not np.all(np.isfinite(v_inre)):
        return v_inre, info, math.inf, False

    res_norm = float(np.linalg.norm(l_ii @ v_inre - rhs))
    rhs_norm = float(np.linalg.norm(rhs))
    rel_res = res_norm / max(rhs_norm, 1e-30)
    ok = math.isfinite(rel_res) and rel_res < _CG_MAX_REL_RESIDUAL
    return v_inre, info, rel_res, ok


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
    cg_troskel: int = STANDARD_CG_TROSKEL,
) -> KretsParResultat:
    """Löser Dirichlet-kretsekvationen `L v = 0` med `v = 1` på fläck A och `v = 0` på fläck B.

    För `inre_idx.size <= cg_troskel` (standard 25 000) används direkt `spsolve`. För större
    SPD-system används Jacobi-förkonditionerad `cg` i O(N) RAM med strikt kontroll av
    konvergens, relativ residual (< 1e-6) och strömbalansfel (< 1e-6).

    Returnerar effektivt motstånd `R_eff = 1 / I_tot`, normaliserad strömtäthet `J_u` i `[0, 1]`
    och exakt 1-lösnings adjungerad känslighet `dR_eff / dG` summerad per cell.
    """
    if cg_troskel < 0:
        raise EkologiFel(f"cg_troskel får inte vara negativ, fick {cg_troskel}.")

    g = motstand.konduktans
    ny, nx = g.shape
    n_tot = ny * nx
    kontrollera_ekologi_celler(n_tot)

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
    del rader_sym, kols_sym, data_sym

    # Kontrollera vilka sammanhängande komponenter som förbinder A och B
    _, komp = connected_components(adj, directed=False)
    komp_a = set(int(c) for c in komp[a_flat])
    komp_b = set(int(c) for c in komp[b_flat])
    gemensamma = komp_a & komp_b
    if not gemensamma:
        return _ej_sammankopplad(g.shape, fran_flack_id, till_flack_id)

    i_komp = np.isin(komp, list(gemensamma))
    del komp
    a_aktiv = a_flat & i_komp
    b_aktiv = b_flat & i_komp
    inre = i_komp & (~a_aktiv) & (~b_aktiv)
    del i_komp

    # Bygg graf-Laplacian L = diag(sum_v G_uv) - A_mat
    diag = np.asarray(adj.sum(axis=1)).ravel()
    lap = sparse.diags(diag, format="csr") - adj
    del adj, diag

    v_flat = np.zeros(n_tot, dtype=np.float64)
    v_flat[a_aktiv] = 1.0
    v_flat[b_aktiv] = 0.0

    inre_idx = np.flatnonzero(inre)
    del inre
    n_inre = int(inre_idx.size)

    if n_inre == 0:
        del lap
        dv_kant, strom_kant, i_ut_a, _, balans_fel = _berakna_strom_och_balans(
            v_flat, u_idx, v_idx, g_kant, a_aktiv, b_aktiv
        )
    else:
        a_idx = np.flatnonzero(a_aktiv)
        l_ii = lap[inre_idx][:, inre_idx].tocsr()
        rhs = -np.asarray(lap[inre_idx][:, a_idx].sum(axis=1)).ravel()
        del lap, a_idx

        if n_inre <= cg_troskel:
            v_inre = np.asarray(spsolve(l_ii.tocsc(), rhs), dtype=np.float64)
            del l_ii, rhs
            v_flat[inre_idx] = np.clip(v_inre, 0.0, 1.0)
            dv_kant, strom_kant, i_ut_a, _, balans_fel = _berakna_strom_och_balans(
                v_flat, u_idx, v_idx, g_kant, a_aktiv, b_aktiv
            )
        else:
            v_inre, cg_info, rel_res, cg_ok = _los_inre_med_jacobi_cg(l_ii, rhs)
            balans_fel = math.inf
            i_ut_a = 0.0
            dv_kant = np.empty(0, dtype=np.float64)
            strom_kant = np.empty(0, dtype=np.float64)

            if cg_ok:
                v_flat[inre_idx] = np.clip(v_inre, 0.0, 1.0)
                dv_kant, strom_kant, i_ut_a, _, balans_fel = _berakna_strom_och_balans(
                    v_flat, u_idx, v_idx, g_kant, a_aktiv, b_aktiv
                )
                if not (
                    i_ut_a > 0.0
                    and math.isfinite(i_ut_a)
                    and math.isfinite(balans_fel)
                    and balans_fel < _CG_MAX_STROMBALANS_FEL
                ):
                    cg_ok = False

            if not cg_ok:
                if n_inre <= _CG_FALLBACK_MAX_N:
                    v_inre = np.asarray(spsolve(l_ii.tocsc(), rhs), dtype=np.float64)
                    del l_ii, rhs
                    v_flat[inre_idx] = np.clip(v_inre, 0.0, 1.0)
                    dv_kant, strom_kant, i_ut_a, _, balans_fel = _berakna_strom_och_balans(
                        v_flat, u_idx, v_idx, g_kant, a_aktiv, b_aktiv
                    )
                else:
                    del l_ii, rhs
                    raise EkologiFel(
                        f"Iterativ SPD-lösare (cg) konvergerade inte för {n_inre} inre celler "
                        f"(info={cg_info}, relativ residual={rel_res:.3e}, "
                        f"strömbalansfel={balans_fel:.3e}) och systemet är för stort för "
                        f"direkt spsolve-reserv (> {_CG_FALLBACK_MAX_N} celler)."
                    )
            else:
                del l_ii, rhs

    if i_ut_a <= 0.0 or not math.isfinite(i_ut_a):
        return _ej_sammankopplad(g.shape, fran_flack_id, till_flack_id)

    r_eff = 1.0 / i_ut_a

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
