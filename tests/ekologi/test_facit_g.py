"""Analytiska facitfall (Grupp G: G1–G6) och enhetstester för `src/ekologi/`."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ekologi import (
    MARKSLAG_BYGGNAD,
    MARKSLAG_HARDGJORD,
    MARKSLAG_OPPEN,
    MARKSLAG_SKOG,
    STANDARD_ARTPROFILER,
    ArtProfil,
    EkologiAtgard,
    EkologiFel,
    MotstandsYta,
    berakna_konnektivitet,
    berakna_pc_eca,
    bygg_kantlista,
    hamta_artprofil,
    jamfor_fore_efter_ekologi,
    kor_monoton_ekologi_intervall,
    los_kretspar,
    valj_basta_ekologi_atgarder_k,
)


def _liten_skogsprofil(min_area_m2: float = 100.0, d_disp_m: float = 100.0) -> ArtProfil:
    return ArtProfil(
        profil_id="test_skog",
        namn="Testprofil skog",
        beskrivning="Syntetisk testprofil för analytisk validering.",
        kalla="Analytiskt facitfall G1–G6.",
        habitat_markslag=frozenset({MARKSLAG_SKOG}),
        min_area_m2=min_area_m2,
        spridningsavstand_m=d_disp_m,
        motstand_per_markslag={
            MARKSLAG_SKOG: 1.0,
            MARKSLAG_OPPEN: 5.0,
            MARKSLAG_HARDGJORD: 25.0,
            MARKSLAG_BYGGNAD: math.inf,
        },
        standard_motstand=10.0,
        motstand_osakerhet_faktor=(0.6, 1.5),
    )


def test_g1_homogen_korridor_exakt_motstand_och_stromtathet() -> None:
    """G1: Homogen W x L-korridor med motstånd R0 -> exakt R_eff = R0 * (L - 1) / W."""
    w, length, r0 = 5, 11, 4.0
    r_arr = np.full((w, length), r0, dtype=np.float64)
    g_arr = 1.0 / r_arr
    myta = MotstandsYta(motstand=r_arr, konduktans=g_arr, cell_m=10.0, profil_id="g1")

    mask_a = np.zeros((w, length), dtype=np.bool_)
    mask_b = np.zeros((w, length), dtype=np.bool_)
    mask_a[:, 0] = True
    mask_b[:, -1] = True

    res = los_kretspar(myta, mask_a, mask_b, grannskap=4)
    facit_r = r0 * (length - 1) / w
    assert res.sammankopplad is True
    assert res.effektivt_motstand == pytest.approx(facit_r, rel=1e-12)
    assert res.strombalans_fel_rel < 1e-12
    # Varje inre cell bär exakt andelen 1 / W av totalströmmen
    assert np.allclose(res.stromtathet[:, 1:-1], 1.0 / w, rtol=1e-12, atol=1e-12)


def test_g2_parallella_korridorer_exakt_parallellkoppling() -> None:
    """G2: Två parallella korridorer åtskilda av barriär -> exakt 1/R_eff = 1/R1 + 1/R2."""
    w1, w2, length = 3, 2, 9
    r1, r2 = 2.0, 5.0
    ny = w1 + 1 + w2
    r_arr = np.full((ny, length), math.inf, dtype=np.float64)
    r_arr[:w1, :] = r1
    r_arr[w1 + 1 :, :] = r2
    g_arr = np.where(np.isfinite(r_arr), 1.0 / r_arr, 0.0)
    myta = MotstandsYta(motstand=r_arr, konduktans=g_arr, cell_m=10.0, profil_id="g2")

    mask_a = np.zeros((ny, length), dtype=np.bool_)
    mask_b = np.zeros((ny, length), dtype=np.bool_)
    mask_a[:, 0] = np.isfinite(r_arr[:, 0])
    mask_b[:, -1] = np.isfinite(r_arr[:, -1])

    res = los_kretspar(myta, mask_a, mask_b, grannskap=4)
    g1_tot = w1 / (r1 * (length - 1))
    g2_tot = w2 / (r2 * (length - 1))
    facit_r = 1.0 / (g1_tot + g2_tot)

    assert res.sammankopplad is True
    assert res.effektivt_motstand == pytest.approx(facit_r, rel=1e-12)
    assert res.strombalans_fel_rel < 1e-12
    # Total ström i korridor 1 och 2 förhåller sig exakt som g1_tot : g2_tot
    i1_andel = float(np.sum(res.stromtathet[:w1, length // 2]))
    i2_andel = float(np.sum(res.stromtathet[w1 + 1 :, length // 2]))
    assert i1_andel == pytest.approx(g1_tot / (g1_tot + g2_tot), rel=1e-12)
    assert i2_andel == pytest.approx(g2_tot / (g1_tot + g2_tot), rel=1e-12)


def test_g3_barriar_med_ekodukt_och_stangd_barriar() -> None:
    """G3: Ogenomtränglig vägg med 1-cells port -> J_port == 1.0; stängd -> R_eff == inf."""
    ny, nx = 7, 11
    ms = np.full((ny, nx), MARKSLAG_OPPEN, dtype=np.uint8)
    ms[1:6, 0:2] = MARKSLAG_SKOG
    ms[1:6, 9:11] = MARKSLAG_SKOG
    ms[:, 5] = MARKSLAG_BYGGNAD
    ms[3, 5] = MARKSLAG_OPPEN  # 1-cells ekodukt/port

    profil = _liten_skogsprofil(min_area_m2=100.0, d_disp_m=200.0)
    res_oppen = berakna_konnektivitet(ms, profil, cell_m=10.0, berakna_flaskhalsar=True)
    assert res_oppen.antal_flackar == 2
    assert res_oppen.stromtathet_sammanlagd is not None
    # All ström mellan fläck 1 och 2 måste passera portcellen (3, 5)
    assert res_oppen.stromtathet_sammanlagd[3, 5] == pytest.approx(1.0, abs=1e-12)
    # Väggceller bär exakt 0 ström
    assert res_oppen.stromtathet_sammanlagd[0, 5] == 0.0

    # Stäng porten -> total barriär: R_eff = inf och PC = (a1^2 + a2^2) / A_L^2
    ms_stangd = ms.copy()
    ms_stangd[3, 5] = MARKSLAG_BYGGNAD
    res_stangd = berakna_konnektivitet(ms_stangd, profil, cell_m=10.0, berakna_flaskhalsar=True)
    a1 = 10.0 * 100.0  # 5x2 celler * 100 m2
    a_tot = ny * nx * 100.0
    facit_pc = (a1 * a1 + a1 * a1) / (a_tot * a_tot)
    assert res_stangd.pc == pytest.approx(facit_pc, rel=1e-12)
    assert math.isinf(res_stangd.kostnadsavstand_m[0, 1])


def test_g4_exakt_pc_eca_och_dpc_for_tre_flackar_pa_linje() -> None:
    """G4: Analytisk kontroll av PC, ECA och dPC_intra/flux/connector på 3 fläckar på linje."""
    areor = np.array([1000.0, 500.0, 2000.0], dtype=np.float64)
    d_disp = 100.0
    d_mat = np.array(
        [
            [0.0, 100.0, math.inf],
            [100.0, 0.0, 100.0],
            [math.inf, 100.0, 0.0],
        ],
        dtype=np.float64,
    )
    a_l = 10_000.0
    pc, eca, p_stj, bet = berakna_pc_eca(areor, d_mat, d_disp, a_l)

    # Vid d = d_disp och p0 = 0.5: p12 = 0.5, p23 = 0.5, p*_13 = 0.5 * 0.5 = 0.25
    assert p_stj[0, 1] == pytest.approx(0.5, rel=1e-12)
    assert p_stj[0, 2] == pytest.approx(0.25, rel=1e-12)
    pc_num_facit = (
        1000.0**2
        + 500.0**2
        + 2000.0**2
        + 2.0 * 1000.0 * 500.0 * 0.5
        + 2.0 * 500.0 * 2000.0 * 0.5
        + 2.0 * 1000.0 * 2000.0 * 0.25
    )
    assert pc == pytest.approx(pc_num_facit / (a_l**2), rel=1e-12)
    assert eca == pytest.approx(math.sqrt(pc_num_facit), rel=1e-12)

    # Fläck 2 (index 1) är enda länken mellan 1 och 3 -> dPC_connector exakt 2*a1*a3*0.25 / pc_num
    conn2_facit = 100.0 * (2.0 * 1000.0 * 2000.0 * 0.25) / pc_num_facit
    assert bet[1].dpc_connector_procent == pytest.approx(conn2_facit, rel=1e-12)
    # Ändfläckarna 1 och 3 är inte språngbrädor -> dPC_connector == 0
    assert bet[0].dpc_connector_procent == pytest.approx(0.0, abs=1e-12)
    assert bet[2].dpc_connector_procent == pytest.approx(0.0, abs=1e-12)


def test_g5_adjungerad_derivata_mot_finita_differenser() -> None:
    """G5: 1-lösnings derivata dR_eff/dG_uv = -((v_u - v_v)/I_tot)^2 mot central FD (< 1e-6)."""
    rng = np.random.default_rng(42)
    ny, nx = 6, 8
    r_arr = rng.uniform(1.0, 10.0, size=(ny, nx))
    g_arr = 1.0 / r_arr
    myta = MotstandsYta(motstand=r_arr, konduktans=g_arr, cell_m=10.0, profil_id="g5")

    mask_a = np.zeros((ny, nx), dtype=np.bool_)
    mask_b = np.zeros((ny, nx), dtype=np.bool_)
    mask_a[1:5, 0] = True
    mask_b[1:5, -1] = True

    u_idx, v_idx, g_kant = bygg_kantlista(g_arr, grannskap=4)
    bas = los_kretspar(
        myta, mask_a, mask_b, grannskap=4, extra_kant_konduktans=(u_idx, v_idx, g_kant)
    )
    v_flat = bas.potential.ravel()
    dr_dg_analytisk = -(((v_flat[u_idx] - v_flat[v_idx]) / bas.total_strom) ** 2)

    eps = 1e-5
    urval = rng.choice(u_idx.size, size=min(15, int(u_idx.size)), replace=False)
    for k in urval:
        g_plus = g_kant.copy()
        g_minus = g_kant.copy()
        g_plus[k] += eps
        g_minus[k] = max(1e-12, g_minus[k] - eps)
        r_plus = los_kretspar(
            myta, mask_a, mask_b, grannskap=4, extra_kant_konduktans=(u_idx, v_idx, g_plus)
        ).effektivt_motstand
        r_minus = los_kretspar(
            myta, mask_a, mask_b, grannskap=4, extra_kant_konduktans=(u_idx, v_idx, g_minus)
        ).effektivt_motstand
        fd = (r_plus - r_minus) / (g_plus[k] - g_minus[k])
        assert dr_dg_analytisk[k] == pytest.approx(fd, rel=1e-6, abs=1e-9)


def test_g6_monotonitet_intervallinneslutning_fore_efter_och_celf() -> None:
    """G6: 50 slumpade scenarier 100 % inneslutna, nytt hus minskar ECA, CELF väljer ekodukt."""
    ny, nx = 9, 13
    ms = np.full((ny, nx), MARKSLAG_OPPEN, dtype=np.uint8)
    ms[1:4, 1:3] = MARKSLAG_SKOG
    ms[5:8, 10:12] = MARKSLAG_SKOG
    ms[:, 6] = MARKSLAG_HARDGJORD
    ms[4, 6] = MARKSLAG_OPPEN

    profil = _liten_skogsprofil(min_area_m2=100.0, d_disp_m=150.0)
    iv = kor_monoton_ekologi_intervall(
        ms,
        profil,
        cell_m=10.0,
        motstand_faktor_intervall=(0.6, 1.5),
        spridning_faktor_intervall=(0.8, 1.25),
    )
    assert iv.eca_min_m2 <= iv.eca_max_m2

    rng = np.random.default_rng(7)
    for _ in range(50):
        rf = float(rng.uniform(0.6, 1.5))
        df = float(rng.uniform(0.8, 1.25))
        r = berakna_konnektivitet(ms, profil, cell_m=10.0, motstand_faktor=rf, spridning_faktor=df)
        assert iv.eca_min_m2 - 1e-9 <= r.eca_m2 <= iv.eca_max_m2 + 1e-9
        assert iv.pc_min - 1e-12 <= r.pc <= iv.pc_max + 1e-12

    # Nytt hus rakt i öppningen får aldrig öka ECA
    ms_efter = ms.copy()
    ms_efter[3:6, 6] = MARKSLAG_BYGGNAD
    fe = jamfor_fore_efter_ekologi(ms, ms_efter, profil, cell_m=10.0)
    assert fe.delta_eca_m2 <= 0.0

    # CELF ska prioritera en grön passage genom barriären framför en perifer åtgärd
    ms_barriar = ms.copy()
    ms_barriar[:, 6] = MARKSLAG_BYGGNAD
    mask_ekodukt = np.zeros((ny, nx), dtype=np.bool_)
    mask_ekodukt[4, 6] = True
    mask_perifer = np.zeros((ny, nx), dtype=np.bool_)
    mask_perifer[0, 0] = True
    val = valj_basta_ekologi_atgarder_k(
        ms_barriar,
        profil,
        [
            EkologiAtgard("perifer_trad", mask_perifer, nytt_motstand=1.0),
            EkologiAtgard("ekodukt_mitt", mask_ekodukt, nytt_motstand=1.0),
        ],
        cell_m=10.0,
        k=2,
    )
    assert len(val) >= 1
    assert val[0].atgard_id == "ekodukt_mitt"
    assert val[0].marginal_eca_vinst_m2 > 0.0


def test_standard_artprofiler_giltiga_och_okand_ger_fel() -> None:
    """Alla källbelagda svenska skissprofiler ska ha giltiga parametrar och källhänvisning."""
    assert len(STANDARD_ARTPROFILER) >= 4
    for pid, p in STANDARD_ARTPROFILER.items():
        assert hamta_artprofil(pid) == p
        assert p.metodstatus == "skiss"
        assert p.kalla
        assert p.spridningsavstand_m > 0.0
        assert p.min_area_m2 > 0.0
    with pytest.raises(EkologiFel):
        hamta_artprofil("finns_inte")
