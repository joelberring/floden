"""Enhetstester för Jacobi-CG SPD-lösare och celltak (`FLODEN_MAX_EKOLOGI_CELLER`)."""

from __future__ import annotations

import math
import tracemalloc
from typing import Any

import numpy as np
import pytest

from ekologi import (
    MARKSLAG_BYGGNAD,
    MARKSLAG_OPPEN,
    MARKSLAG_SKOG,
    STANDARD_ARTPROFILER,
    EkologiFel,
    MotstandsYta,
    berakna_konnektivitet,
    berakna_kostnadsavstand,
    bygg_kantlista,
    extrahera_habitatflackar,
    los_kretspar,
)
from ekologi import krets as krets_mod
from ekologi.krets import (
    STANDARD_MAX_EKOLOGI_CELLER,
    kontrollera_ekologi_celler,
    max_ekologi_celler,
)


def _heterogent_motstand(
    ny: int,
    nx: int,
    *,
    seed: int = 123,
) -> tuple[MotstandsYta, np.ndarray, np.ndarray]:
    """Skapar ett heterogent 2D-motståndsraster med barriärvägg och två öppningar."""
    rng = np.random.default_rng(seed)
    r_arr = np.exp(rng.uniform(0.0, math.log(50.0), size=(ny, nx))).astype(np.float64)
    # Barriärvägg i mitten med två passager för icke-trivial strömbild
    mitt = nx // 2
    r_arr[:, mitt] = math.inf
    r_arr[ny // 4 : ny // 4 + max(2, ny // 12), mitt] = 3.0
    r_arr[(3 * ny) // 4 : (3 * ny) // 4 + max(2, ny // 12), mitt] = 8.0

    g_arr = np.where(np.isfinite(r_arr), 1.0 / r_arr, 0.0)
    myta = MotstandsYta(motstand=r_arr, konduktans=g_arr, cell_m=10.0, profil_id="test_cg")

    mask_a = np.zeros((ny, nx), dtype=np.bool_)
    mask_b = np.zeros((ny, nx), dtype=np.bool_)
    mask_a[ny // 5 : (4 * ny) // 5, 0:2] = True
    mask_b[ny // 5 : (4 * ny) // 5, nx - 2 : nx] = True
    return myta, mask_a, mask_b


@pytest.mark.parametrize("grannskap", [4, 8])
def test_cg_matchar_spsolve_pa_heterogent_2d_motstand(grannskap: int) -> None:
    """Jacobi-CG (`cg_troskel=10`) ska matcha exakt `spsolve` inom < 1e-6 på alla fält."""
    myta, mask_a, mask_b = _heterogent_motstand(40, 50, seed=42 + grannskap)

    res_sps = los_kretspar(myta, mask_a, mask_b, grannskap=grannskap, cg_troskel=100_000)
    res_cg = los_kretspar(myta, mask_a, mask_b, grannskap=grannskap, cg_troskel=10)

    assert res_sps.sammankopplad is True
    assert res_cg.sammankopplad is True
    assert res_cg.strombalans_fel_rel < 1e-6
    assert res_cg.effektivt_motstand == pytest.approx(res_sps.effektivt_motstand, rel=1e-6)
    assert res_cg.total_strom == pytest.approx(res_sps.total_strom, rel=1e-6)
    np.testing.assert_allclose(res_cg.potential, res_sps.potential, rtol=1e-6, atol=1e-6)
    np.testing.assert_allclose(res_cg.stromtathet, res_sps.stromtathet, rtol=1e-6, atol=1e-6)
    np.testing.assert_allclose(
        res_cg.kanslighet_cell, res_sps.kanslighet_cell, rtol=1e-6, atol=1e-6
    )


def test_cg_200x200_stort_rutnat_rent_via_cg_vagen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ett 200x200 (40 000 celler) rutnät löses rent via CG-vägen utan `spsolve`-reserv."""
    myta, mask_a, mask_b = _heterogent_motstand(200, 200, seed=7)

    def _forbjuden_spsolve(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("spsolve ska inte anropas på 200x200 rutnät (CG ska konvergera).")

    monkeypatch.setattr(krets_mod, "spsolve", _forbjuden_spsolve)

    tracemalloc.start()
    try:
        res = los_kretspar(myta, mask_a, mask_b, grannskap=4)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert res.sammankopplad is True
    assert math.isfinite(res.effektivt_motstand) and res.effektivt_motstand > 0.0
    assert res.strombalans_fel_rel < 1e-6
    assert float(np.min(res.potential)) >= 0.0
    assert float(np.max(res.potential)) <= 1.0
    assert float(np.min(res.stromtathet)) >= 0.0
    assert float(np.max(res.stromtathet)) <= 1.0
    assert float(np.max(res.kanslighet_cell)) <= 0.0
    # O(N) RAM för 40 000 celler ska ligga långt under 100 MB
    assert peak_bytes < 100 * 1024 * 1024


def test_floden_max_ekologi_celler_validering_och_celltak(monkeypatch: pytest.MonkeyPatch) -> None:
    """`FLODEN_MAX_EKOLOGI_CELLER` valideras strikt och ger tydligt `EkologiFel` över taket."""
    monkeypatch.delenv("FLODEN_MAX_EKOLOGI_CELLER", raising=False)
    assert max_ekologi_celler() == STANDARD_MAX_EKOLOGI_CELLER

    for standard_val in ("", "   ", "0"):
        monkeypatch.setenv("FLODEN_MAX_EKOLOGI_CELLER", standard_val)
        assert max_ekologi_celler() == STANDARD_MAX_EKOLOGI_CELLER

    monkeypatch.setenv("FLODEN_MAX_EKOLOGI_CELLER", "12345")
    assert max_ekologi_celler() == 12345

    for ogiltigt in ("-1", "abc", "12.5"):
        monkeypatch.setenv("FLODEN_MAX_EKOLOGI_CELLER", ogiltigt)
        with pytest.raises(EkologiFel, match="FLODEN_MAX_EKOLOGI_CELLER"):
            max_ekologi_celler()

    # Fail-fast vid överskridet tak i både krets och konnektivitet
    monkeypatch.setenv("FLODEN_MAX_EKOLOGI_CELLER", "100")
    with pytest.raises(EkologiFel, match="FLODEN_MAX_EKOLOGI_CELLER"):
        kontrollera_ekologi_celler(101)

    myta, mask_a, mask_b = _heterogent_motstand(15, 15, seed=1)  # 225 > 100
    with pytest.raises(EkologiFel, match="FLODEN_MAX_EKOLOGI_CELLER"):
        los_kretspar(myta, mask_a, mask_b)
    with pytest.raises(EkologiFel, match="FLODEN_MAX_EKOLOGI_CELLER"):
        bygg_kantlista(myta.konduktans)

    ms = np.full((15, 15), MARKSLAG_OPPEN, dtype=np.uint8)
    ms[1:5, 1:5] = MARKSLAG_SKOG
    ms[10:14, 10:14] = MARKSLAG_SKOG
    ms[:, 7] = MARKSLAG_BYGGNAD
    profil = STANDARD_ARTPROFILER["pollinerare"]
    with pytest.raises(EkologiFel, match="FLODEN_MAX_EKOLOGI_CELLER"):
        berakna_konnektivitet(ms, profil, cell_m=10.0)

    hab = extrahera_habitatflackar(ms, profil, cell_m=10.0)
    with pytest.raises(EkologiFel, match="FLODEN_MAX_EKOLOGI_CELLER"):
        berakna_kostnadsavstand(myta, hab)

    # Exakt vid gränsen ska det gå igenom
    monkeypatch.setenv("FLODEN_MAX_EKOLOGI_CELLER", "225")
    res_ok = los_kretspar(myta, mask_a, mask_b)
    assert res_ok.sammankopplad is True

    with pytest.raises(EkologiFel, match="cg_troskel"):
        los_kretspar(myta, mask_a, mask_b, cg_troskel=-1)


def test_cg_ej_konvergerad_faller_tillbaka_eller_ger_fel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Icke-konvergerad CG faller tillbaka på spsolve (<= 100k) eller ger tydligt EkologiFel."""
    myta, mask_a, mask_b = _heterogent_motstand(16, 20, seed=99)
    ref = los_kretspar(myta, mask_a, mask_b, cg_troskel=100_000)

    def _misslyckad_cg(
        a: Any,
        b: np.ndarray,
        **_kwargs: Any,
    ) -> tuple[np.ndarray, int]:
        return np.zeros_like(b), 5000

    monkeypatch.setattr(krets_mod, "cg", _misslyckad_cg)

    # Under _CG_FALLBACK_MAX_N: säker reserv till exakt spsolve
    res_reserv = los_kretspar(myta, mask_a, mask_b, cg_troskel=10)
    assert res_reserv.sammankopplad is True
    assert res_reserv.effektivt_motstand == pytest.approx(ref.effektivt_motstand, rel=1e-12)
    assert res_reserv.strombalans_fel_rel < 1e-12

    # Över _CG_FALLBACK_MAX_N: kasta tydligt EkologiFel (aldrig okonvergerat svar!)
    monkeypatch.setattr(krets_mod, "_CG_FALLBACK_MAX_N", 10)
    with pytest.raises(EkologiFel, match="konvergerade inte"):
        los_kretspar(myta, mask_a, mask_b, cg_troskel=10)

    # Även om info == 0 men residualen är stor (falsk konvergens) ska felet fångas
    def _falsk_cg(
        a: Any,
        b: np.ndarray,
        **_kwargs: Any,
    ) -> tuple[np.ndarray, int]:
        return np.zeros_like(b), 0

    monkeypatch.setattr(krets_mod, "cg", _falsk_cg)
    with pytest.raises(EkologiFel, match="konvergerade inte"):
        los_kretspar(myta, mask_a, mask_b, cg_troskel=10)


def test_forbered_kretsgraf_och_snabb_konnektivitet_320x320_med_80_flackar() -> None:
    """Profil: `320×320`-rutnät med `80` fläckar kör < 25 s och < 600 MB RSS (exakt ECA/PC/dPC)."""
    import resource
    import sys
    import time

    from ekologi import (
        berakna_pc_eca,
        forbered_kretsgraf,
        los_kretspar_forberedd,
    )

    rss_div = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0

    # 1. Förberäknad graf ska ge bitidentiskt resultat mot `los_kretspar`
    myta, mask_a, mask_b = _heterogent_motstand(24, 30, seed=11)
    ref = los_kretspar(myta, mask_a, mask_b, grannskap=4)
    graf = forbered_kretsgraf(myta, grannskap=4)
    snabb = los_kretspar_forberedd(graf, mask_a, mask_b)
    assert snabb.effektivt_motstand == pytest.approx(ref.effektivt_motstand, rel=1e-12)
    np.testing.assert_allclose(snabb.stromtathet, ref.stromtathet, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(snabb.kanslighet_cell, ref.kanslighet_cell, rtol=1e-12, atol=1e-12)

    # 2. Stort tätortsfönster: 320×320 celler med 80 habitatfläckar och 20 % byggnadsbarriärer
    rng = np.random.default_rng(42)
    ny, nx = 320, 320
    profil = STANDARD_ARTPROFILER["pollinerare"]
    hab_kod = min(profil.habitat_markslag)
    bakgrund_kod = next(
        kod for kod in (MARKSLAG_SKOG, MARKSLAG_OPPEN, 3) if kod not in profil.habitat_markslag
    )
    ms = np.full((ny, nx), bakgrund_kod, dtype=np.uint8)
    ms[rng.random((ny, nx)) < 0.2] = MARKSLAG_BYGGNAD
    for k in range(80):
        r0 = (k // 10) * 38 + 4
        c0 = (k % 10) * 30 + 4
        h = 4 + (k % 5)
        w = 4 + ((k * 3) % 5)
        ms[r0 : r0 + h, c0 : c0 + w] = hab_kod

    rss_fore_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rss_div
    t0 = time.perf_counter()
    res = berakna_konnektivitet(
        ms,
        profil,
        cell_m=10.0,
        extra_barriar_mask=(ms == MARKSLAG_BYGGNAD),
        berakna_flaskhalsar=True,
    )
    dt = time.perf_counter() - t0
    rss_efter_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rss_div
    delta_rss_mb = max(0.0, rss_efter_mb - rss_fore_mb)

    assert res.antal_flackar == 80
    assert dt < 25.0, f"berakna_konnektivitet (320×320, 80 fläckar) tog {dt:.2f} s (> 25 s)"
    assert delta_rss_mb < 600.0, f"RSS-ökning blev {delta_rss_mb:.1f} MB (> 600 MB)"
    assert res.stromtathet_sammanlagd is not None
    assert res.kanslighet_sammanlagd is not None
    assert res.stromtathet_sammanlagd.shape == (ny, nx)
    assert res.kanslighet_sammanlagd.shape == (ny, nx)
    assert 1 <= res.antal_kretspar_beraknade <= 60
    assert res.antal_kretspar_totalt > res.antal_kretspar_beraknade
    assert 0.0 < res.kretspar_vikt_andel <= 1.0
    assert res.krets_upplosningsfaktor == 2
    assert float(np.max(res.stromtathet_sammanlagd[ms == MARKSLAG_BYGGNAD])) == 0.0
    assert float(np.max(res.kanslighet_sammanlagd)) <= 0.0

    # 3. ECA, PC och dPC är 100 % exakta över samtliga 80 fläckar (identiska mot berakna_pc_eca)
    hab = extrahera_habitatflackar(ms, profil, cell_m=10.0)
    areor = np.array([f.effektiv_area_m2 for f in hab.flackar], dtype=np.float64)
    pc_ex, eca_ex, _, bet_ex = berakna_pc_eca(
        areor,
        res.kostnadsavstand_m,
        profil.spridningsavstand_m,
        hab.analys_area_m2,
        flack_ids=tuple(f.flack_id for f in hab.flackar),
    )
    assert res.eca_m2 == pytest.approx(eca_ex, rel=1e-12)
    assert res.pc == pytest.approx(pc_ex, rel=1e-12)
    assert res.flack_betydelse == bet_ex
