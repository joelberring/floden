"""Fysik-, facit- och robusthetstester för `src/mikroklimat/` (UTCI, T_mrt och värmestress).

Verifierar:
  1. Alla 210 officiella Bröde m.fl. (2012) / `UTCI_a002.f90`-polynomtermer och Hardy (1998)
     ITS-90 mättat ångtryck.
  2. Officiella UTCI-referenspunkter (inkl. referensvillkoret `T_a=20, T_mrt=20, v=0.5, RH=50%`
     mot både exakt `UTCI_a002.f90` och Fiala-referensidentiteten, samt publicerade värme-,
     köld- och `ts_utci.json`-punkter).
  3. Fysikalisk monotoni (dUTCI/dT_mrt > 0, dUTCI/dRH > 0 i värme, vindkylning vid T_a < 32 °C).
  4. Bröde (2012) logaritmisk vindprofil och 10-gradig stresskala (`-5..+4`).
  5. Inga tysta fel: giltighetsgränser flaggas utan avklippning, `NaN` bevaras exakt.
  6. Exakt analytiskt isotermiskt svartkroppsfacit för T_mrt (`|T_mrt - T_a| < 1e-9 °C`).
  7. Vinkelfaktorsumma == 1.00 och Prata (1996) klarhimmelsemissivitet i `(0.5, 1.0)`.
  8. Före/efter trädskuggning i gaturum + deterministisk JSON-serialisering.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import numpy as np
import pytest

from mikroklimat import (
    STEFAN_BOLTZMANN_W_M2_K4,
    UTCI_KLASS_OGILTIG,
    UTCI_POLYNOM_TERMER,
    UTCI_STRESSKLASS_PER_ID,
    UTCI_STRESSKLASSER,
    VINKELFAKTORER_STAENDE,
    angtryck_kpa,
    berakna_tmrt,
    berakna_utci,
    berakna_varmestress,
    jamfor_varmestress,
    klar_himmel_emissivitet_prata,
    klassificera_utci,
    mattat_angtryck_hpa,
    stressklass_namn,
    varmestress_jamforelse_till_json,
    varmestress_till_json,
    vind_fotgangarhojd_till_10m,
)

# ---------------------------------------------------------------------------
# 1. Struktur och konstanter för UTCI-polynom, vinkelfaktorer och Hardy ITS-90
# ---------------------------------------------------------------------------


def test_utci_polynom_har_exakt_210_unika_termer_av_grad_hogst_6() -> None:
    """Alla 210 termer (C(6+4, 4) = 210) i Bröde (2012) 6:e-ordningens polynom finns."""
    assert len(UTCI_POLYNOM_TERMER) == 210
    monom: set[tuple[int, int, int, int]] = set()
    for koeff, p_ta, p_va, p_dt, p_pa in UTCI_POLYNOM_TERMER:
        assert math.isfinite(koeff)
        assert min(p_ta, p_va, p_dt, p_pa) >= 0
        assert p_ta + p_va + p_dt + p_pa <= 6
        monom.add((p_ta, p_va, p_dt, p_pa))
    assert len(monom) == 210


def test_vinkelfaktorsumma_ar_exakt_ett() -> None:
    """ISO 7726 / Lindberg (2008) vinkelfaktorer för stående människa summerar till exakt 1.0."""
    assert VINKELFAKTORER_STAENDE == (0.06, 0.06, 0.22, 0.22, 0.22, 0.22)
    assert math.isclose(sum(VINKELFAKTORER_STAENDE), 1.0, rel_tol=0.0, abs_tol=1e-15)


def test_hardy_mattat_angtryck_referensvarden() -> None:
    """Hardy (1998) ITS-90 mättat ångtryck mot tabellerade vattenångvärden."""
    # Trippelpunkt ~0 °C -> 6.1121 hPa; 20 °C -> 23.3834 hPa; 40 °C -> 73.8127 hPa.
    es_0 = float(mattat_angtryck_hpa(0.0))
    es_20 = float(mattat_angtryck_hpa(20.0))
    es_40 = float(mattat_angtryck_hpa(40.0))
    assert math.isclose(es_0, 6.11213, abs_tol=1e-3)
    assert math.isclose(es_20, 23.3834, abs_tol=1e-2)
    assert math.isclose(es_40, 73.8127, abs_tol=5e-2)
    # 50 % RH vid 20 °C -> ~1.16917 kPa
    pa_20 = float(angtryck_kpa(20.0, 50.0))
    assert math.isclose(pa_20, es_20 * 0.5 / 10.0, rel_tol=1e-12)
    # NaN och temperatur <= -273.15 °C ger NaN utan krasch.
    assert np.isnan(mattat_angtryck_hpa(np.nan))
    assert np.isnan(mattat_angtryck_hpa(-273.15))


# ---------------------------------------------------------------------------
# 2. Officiella Bröde (2012) / UTCI_a002.f90 referenspunkter & monotoni
# ---------------------------------------------------------------------------


def test_utci_brode_referensvillkor_och_officiellt_polynomfacit() -> None:
    """Verifierar referensvillkoret (20, 20, 0.5, 50 %) mot UTCI_a002.f90 och Fiala-referens.

    Notering (Bröde m.fl. 2012, Int. J. Biometeorol. 56:481–494):
    I den exakta Fiala-flernodsmodellen gäller per definition UTCI = T_a = 20.00 °C vid
    referensvillkoret (T_mrt = T_a, v_10m = 0.5 m/s, RH = 50 %). Den officiella 6:e-ordningens
    polynomregressionen (`UTCI_a002.f90`, 210 termer, globalt RMSE 1.1 °C) ger vid denna punkt
    exakt `19.8476494539 °C` (regressionsavvikelse -0.1524 °C). Matchning mot `UTCI_a002.f90`
    inom `1e-9 °C` fångar omedelbart minsta skrivfel i någon av de 210 koefficienterna.
    """
    res = berakna_utci(20.0, 20.0, 0.5, 50.0)
    utci_val = float(res.utci_c)
    assert not bool(res.utanfor_giltighetsomrade)
    assert res.varningar == ()
    assert int(res.klass_id) == 0  # Ingen termisk stress
    # Exakt överensstämmelse med officiella UTCI_a002.f90 / pythermalcomfort / ladybug-comfort:
    assert abs(utci_val - 19.8476494539) < 1e-8
    # Nära Fiala-referensidentiteten 20.00 °C (känd polynomresidual -0.152 °C << RMSE 1.1 °C):
    assert abs(utci_val - 20.00) < 0.20


@pytest.mark.parametrize(
    ("t_a", "t_mrt", "v_10", "rh", "utci_a002_c", "forvantad_klass"),
    [
        # Publicerade valideringspunkter ur UTCI_a002.f90 / pythermalcomfort (ts_utci.json):
        (25.0, 27.0, 1.0, 50.0, 25.190912, 0),  # ts_utci: 25.2, ingen stress
        (25.0, 25.0, 1.0, 50.0, 24.612126, 0),  # pythermalcomfort exempel: 24.6
        (19.0, 24.0, 1.0, 50.0, 20.028248, 0),  # ts_utci: 20.0
        (19.0, 14.0, 1.0, 50.0, 16.766112, 0),  # ts_utci: 16.8
        (27.0, 22.0, 1.0, 50.0, 25.433861, 0),  # ts_utci: 25.5
        (27.0, 22.0, 10.0, 50.0, 20.013178, 0),  # ts_utci: 20.0
        (27.0, 22.0, 16.0, 50.0, 15.795433, 0),  # ts_utci: 15.8
        (30.0, 27.0, 1.0, 50.0, 29.526872, 1),  # ts_utci: 29.6, måttlig värmestress
        (9.0, 9.0, 1.0, 50.0, 8.654022, -1),  # ts_utci: 8.7, svag köldstress
        # Publicerade värme- och köldpunkter ur UTCI_a002.f90:
        (30.0, 60.0, 1.0, 50.0, 37.923599, 2),  # stark värmestress
        (35.0, 65.0, 0.5, 40.0, 42.949248, 3),  # mycket stark värmestress
        (40.0, 40.0, 1.0, 50.0, 43.609084, 3),  # mycket stark värmestress
        (-10.0, -10.0, 10.0, 50.0, -39.657197, -4),  # mycket stark köldstress
        (-30.0, -50.0, 15.0, 20.0, -68.017374, -5),  # extrem köldstress
    ],
)
def test_utci_publicerade_varme_och_koldpunkter(
    t_a: float,
    t_mrt: float,
    v_10: float,
    rh: float,
    utci_a002_c: float,
    forvantad_klass: int,
) -> None:
    """Verifierar exakta UTCI-värden och stressklasser över hela temperaturspannet."""
    res = berakna_utci(t_a, t_mrt, v_10, rh)
    assert not bool(res.utanfor_giltighetsomrade)
    assert res.varningar == ()
    assert abs(float(res.utci_c) - utci_a002_c) < 1e-5
    assert int(res.klass_id) == forvantad_klass


def test_utci_fysikalisk_monotoni() -> None:
    """Kontrollerar fysikalisk riktning: dUTCI/dT_mrt > 0, dUTCI/dRH > 0 i värme, vindkylning."""
    # 1. dUTCI / dT_mrt > 0 över kyla, neutral och värme.
    for ta in (-10.0, 10.0, 25.0, 35.0):
        tmrt_serie = np.linspace(ta - 20.0, ta + 50.0, 15)
        res = berakna_utci(ta, tmrt_serie, 2.0, 50.0)
        assert not np.any(res.utanfor_giltighetsomrade)
        assert np.all(np.diff(res.utci_c) > 0.0)

    # 2. dUTCI / dRH > 0 i värme (högre fuktighet försvårar svettavdunstning).
    for ta, tmrt in ((28.0, 45.0), (32.0, 55.0), (35.0, 50.0)):
        rh_serie = np.array([15.0, 30.0, 45.0, 60.0, 75.0])
        res = berakna_utci(ta, tmrt, 1.0, rh_serie)
        assert not np.any(res.utanfor_giltighetsomrade)
        assert np.all(np.diff(res.utci_c) > 0.0)

    # 3. Vindkylning vid T_a < 32 °C: högre vind ger strikt lägre UTCI.
    for ta in (-15.0, 0.0, 15.0, 25.0):
        vind_serie = np.array([0.5, 1.5, 3.0, 6.0, 10.0, 15.0])
        res = berakna_utci(ta, ta, vind_serie, 50.0)
        assert not np.any(res.utanfor_giltighetsomrade)
        assert np.all(np.diff(res.utci_c) < 0.0)


def test_vind_fotgangarhojd_till_10m_logaritmisk_profil() -> None:
    """Verifierar Bröde (2012) logaritmisk vindprofil v_10 = v_z * ln(10/z0)/ln(z/z0)."""
    # Vid z = 1.1 m, z0 = 0.01 m -> ln(1000) / ln(110) ≈ 1.46958.
    faktor_1_1 = math.log(10.0 / 0.01) / math.log(1.1 / 0.01)
    assert math.isclose(
        vind_fotgangarhojd_till_10m(2.0, hojd_m=1.1), 2.0 * faktor_1_1, rel_tol=1e-14
    )
    # Standard z = 1.5 m, z0 = 0.01 m -> ln(1000) / ln(150) ≈ 1.37862.
    faktor_1_5 = math.log(10.0 / 0.01) / math.log(1.5 / 0.01)
    assert math.isclose(vind_fotgangarhojd_till_10m(2.0), 2.0 * faktor_1_5, rel_tol=1e-14)
    # Array-stöd med NaN-bevarande.
    arr = np.array([[1.0, np.nan], [3.0, 0.0]])
    ut = vind_fotgangarhojd_till_10m(arr)
    assert ut.shape == (2, 2)
    assert math.isclose(float(ut[0, 0]), faktor_1_5, rel_tol=1e-14)
    assert np.isnan(ut[0, 1])
    assert float(ut[1, 1]) == 0.0
    # Ogiltiga parametrar ger tydligt ValueError.
    with pytest.raises(ValueError):
        vind_fotgangarhojd_till_10m(-0.5)
    with pytest.raises(ValueError):
        vind_fotgangarhojd_till_10m(1.0, hojd_m=0.01, skrovlighet_z0_m=0.01)
    with pytest.raises(ValueError):
        vind_fotgangarhojd_till_10m(1.0, skrovlighet_z0_m=0.0)


def test_utci_10_gradig_stresskala_alla_klasser_och_granser() -> None:
    """Verifierar alla 10 stressklasser (-5..+4), gränsvärden och svenska/engelska namn."""
    assert len(UTCI_STRESSKLASSER) == 10
    assert [k.klass_id for k in UTCI_STRESSKLASSER] == list(range(-5, 5))

    prover = np.array(
        [
            -45.0,
            -40.0,
            -27.0,
            -13.0,
            0.0,
            9.0,
            26.0,
            26.01,
            32.0,
            32.01,
            38.0,
            38.01,
            46.0,
            46.01,
            np.nan,
        ]
    )
    forvantade = np.array(
        [-5, -4, -3, -2, -1, 0, 0, 1, 1, 2, 2, 3, 3, 4, UTCI_KLASS_OGILTIG],
        dtype=np.int8,
    )
    np.testing.assert_array_equal(klassificera_utci(prover), forvantade)

    for kid in range(-5, 5):
        assert kid in UTCI_STRESSKLASS_PER_ID
        assert stressklass_namn(kid, sprak="sv")
        assert stressklass_namn(kid, sprak="en")
    assert stressklass_namn(-5, sprak="sv") == "Extrem köldstress"
    assert stressklass_namn(0, sprak="sv") == "Ingen termisk stress"
    assert stressklass_namn(4, sprak="sv") == "Extrem värmestress"
    assert stressklass_namn(UTCI_KLASS_OGILTIG, sprak="sv") == "Ogiltig / saknas"


def test_utci_giltighetsgranser_flaggas_utan_tyst_avklippning_och_nan_bevaras() -> None:
    """Utom-intervall-celler flaggas utan avklippning; NaN bevaras; friska grannar opåverkade."""
    ta = np.array([[20.0, 55.0, np.nan], [20.0, 20.0, 40.0]])
    tmrt = np.array([[20.0, 55.0, 20.0], [20.0, 100.0, 40.0]])
    vind = np.array([[0.5, 1.0, 1.0], [0.2, 1.0, 1.0]])
    rh = np.array([[50.0, 50.0, 50.0], [50.0, 50.0, 80.0]])  # (40°C, 80% RH) -> pa=5.91 kPa > 5 kPa

    res = berakna_utci(ta, tmrt, vind, rh)
    assert res.utci_c.shape == (2, 3)

    # (0, 0): giltig referenspunkt
    assert not res.utanfor_giltighetsomrade[0, 0]
    assert abs(float(res.utci_c[0, 0]) - 19.8476494539) < 1e-8

    # (0, 2): NaN bevaras, ej flaggad som intervallbrott
    assert np.isnan(res.utci_c[0, 2])
    assert not res.utanfor_giltighetsomrade[0, 2]
    assert int(res.klass_id[0, 2]) == UTCI_KLASS_OGILTIG

    # (0, 1) Ta=55, (1, 0) v=0.2, (1, 1) dTmrt=80, (1, 2) pa>5 kPa: alla flaggas och sätts till NaN
    for r, c in ((0, 1), (1, 0), (1, 1), (1, 2)):
        assert res.utanfor_giltighetsomrade[r, c]
        assert np.isnan(res.utci_c[r, c])
        assert int(res.klass_id[r, c]) == UTCI_KLASS_OGILTIG
    assert len(res.varningar) >= 4

    # Verifiera att ingen tyst avklippning sker vid tillat_extrapolering=True (v=0.2 != v=0.5):
    res_extra = berakna_utci(20.0, 20.0, 0.2, 50.0, tillat_extrapolering=True)
    res_grans = berakna_utci(20.0, 20.0, 0.5, 50.0)
    assert bool(res_extra.utanfor_giltighetsomrade)
    assert len(res_extra.varningar) == 1
    assert abs(float(res_extra.utci_c) - float(res_grans.utci_c)) > 0.1

    # Även RH < 5 % och +/-inf flaggas tydligt.
    res_rh = berakna_utci(20.0, 20.0, 1.0, 2.0)
    assert bool(res_rh.utanfor_giltighetsomrade)
    assert any("relativ_fuktighet_pct" in v for v in res_rh.varningar)

    res_inf = berakna_utci(20.0, np.inf, 1.0, 50.0)
    assert bool(res_inf.utanfor_giltighetsomrade)
    assert np.isnan(res_inf.utci_c)
    assert any("Icke-ändliga" in v for v in res_inf.varningar)


# ---------------------------------------------------------------------------
# 3. Exakt analytiskt svartkroppsfacit & fysik för T_mrt (Prata 1996 + SOLWEIG)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("t_luft_c", [-20.0, 0.0, 20.0, 35.0])
def test_tmrt_isotermiskt_svartkroppsfacit(t_luft_c: float) -> None:
    """Analytiskt facit: K=0, eps_himmel=eps_mark=eps_vagg=1.0, yttemp=T_a => |T_mrt-T_a| < 1e-9."""
    svf = np.array([[0.0, 0.35, 0.70, 1.0], [0.15, 0.50, 0.85, 0.99]])
    skuggmask = np.array([[0.0, 0.25, 0.50, 1.0], [1.0, 0.75, 0.10, 0.0]])

    res = berakna_tmrt(
        svf=svf,
        skuggmask=skuggmask,
        solhojd_rad=0.0,
        sol_direkt_w_m2=0.0,
        sol_diffus_w_m2=0.0,
        t_luft_c=t_luft_c,
        relativ_fuktighet_pct=50.0,
        albedo_mark=0.15,
        albedo_vagg=0.20,
        emissivitet_mark=1.0,
        emissivitet_vagg=1.0,
        emissivitet_himmel=1.0,
        t_mark_soltillagg_c=0.0,
        t_vagg_soltillagg_c=0.0,
    )

    max_fel_c = float(np.max(np.abs(res.t_mrt_c - t_luft_c)))
    assert max_fel_c < 1e-9
    # Alla kortvågsflöden exakt noll, alla långvågsflöden exakt sigma * T_K^4.
    forvantad_l = STEFAN_BOLTZMANN_W_M2_K4 * (t_luft_c + 273.15) ** 4
    np.testing.assert_allclose(res.k_ned_w_m2, 0.0, atol=1e-15)
    np.testing.assert_allclose(res.k_upp_w_m2, 0.0, atol=1e-15)
    np.testing.assert_allclose(res.k_sida_w_m2, 0.0, atol=1e-15)
    np.testing.assert_allclose(res.l_ned_w_m2, forvantad_l, rtol=1e-13)
    np.testing.assert_allclose(res.l_upp_w_m2, forvantad_l, rtol=1e-13)
    np.testing.assert_allclose(res.l_sida_w_m2, forvantad_l, rtol=1e-13)

    # Samma exakta svartkroppsfacit gäller även med trädkronemask när eps_trad = 1.0.
    trad = np.array([[False, True, False, True], [True, False, True, False]])
    res_trad = berakna_tmrt(
        svf=svf,
        skuggmask=skuggmask,
        solhojd_rad=0.0,
        sol_direkt_w_m2=0.0,
        sol_diffus_w_m2=0.0,
        t_luft_c=t_luft_c,
        relativ_fuktighet_pct=50.0,
        emissivitet_mark=1.0,
        emissivitet_vagg=1.0,
        emissivitet_himmel=1.0,
        emissivitet_trad=1.0,
        trad_mask=trad,
        t_mark_soltillagg_c=0.0,
        t_vagg_soltillagg_c=0.0,
    )
    assert float(np.max(np.abs(res_trad.t_mrt_c - t_luft_c))) < 1e-9


def test_prata_emissivitet_intervall_och_monotoni() -> None:
    """Prata (1996) klarhimmelsemissivitet ligger strikt i (0.5, 1.0) och ökar med fuktighet."""
    ta_grid, rh_grid = np.meshgrid(
        np.linspace(-30.0, 45.0, 16),
        np.linspace(5.0, 100.0, 10),
    )
    eps = klar_himmel_emissivitet_prata(ta_grid, rh_grid)
    assert np.all(np.isfinite(eps))
    assert float(np.min(eps)) > 0.5
    assert float(np.max(eps)) < 1.0
    # Monotont växande i RH för fast temperatur.
    assert np.all(np.diff(eps, axis=0) > 0.0)
    # Vid 20 °C, 50 % RH: xi ≈ 1.854 -> eps ≈ 0.788.
    eps_20_50 = float(klar_himmel_emissivitet_prata(20.0, 50.0))
    assert 0.77 < eps_20_50 < 0.81


def test_tmrt_validering_och_nan_bevarande() -> None:
    """Ogiltiga fysiska indata ger tydligt ValueError; NaN bevaras genom alla utdatafält."""
    with pytest.raises(ValueError, match="svf"):
        berakna_tmrt(1.2, 1.0, 0.8, 600.0, 150.0, 25.0, 50.0)
    with pytest.raises(ValueError, match="skuggmask"):
        berakna_tmrt(0.8, -0.1, 0.8, 600.0, 150.0, 25.0, 50.0)
    with pytest.raises(ValueError, match="albedo_mark"):
        berakna_tmrt(0.8, 1.0, 0.8, 600.0, 150.0, 25.0, 50.0, albedo_mark=1.5)
    with pytest.raises(ValueError, match="emissivitet_mark"):
        berakna_tmrt(0.8, 1.0, 0.8, 600.0, 150.0, 25.0, 50.0, emissivitet_mark=0.0)
    with pytest.raises(ValueError, match="sol_direkt_w_m2"):
        berakna_tmrt(0.8, 1.0, 0.8, -10.0, 150.0, 25.0, 50.0)
    with pytest.raises(ValueError, match="oändliga"):
        berakna_tmrt(np.array([0.5, np.inf]), 1.0, 0.8, 600.0, 150.0, 25.0, 50.0)

    # NaN-celler (t.ex. byggnadsfotavtryck) bevaras exakt.
    svf = np.array([[0.8, np.nan], [0.5, 0.9]])
    skugga = np.array([[1.0, 1.0], [0.0, 1.0]])
    res = berakna_tmrt(svf, skugga, math.radians(45.0), 750.0, 140.0, 28.0, 45.0)
    assert np.isnan(res.t_mrt_c[0, 1])
    assert np.isnan(res.s_str_w_m2[0, 1])
    assert np.isfinite(res.t_mrt_c[0, 0])
    assert np.isfinite(res.t_mrt_c[1, 0])
    # Solbelyst punkt ska vara markant varmare i T_mrt än skuggad punkt.
    assert float(res.t_mrt_c[0, 0]) > float(res.t_mrt_c[1, 0]) + 15.0


# ---------------------------------------------------------------------------
# 4. Högnivå värmestress: trädskuggning före/efter, JSON och paketgränser
# ---------------------------------------------------------------------------


def test_varmestress_tradskuggning_fore_efter_och_json() -> None:
    """Ökad trädskugga i ett varmt gaturum minskar T_mrt, UTCI och andel stark värmestress."""
    # 4x4 gaturum med en byggnadscell (NaN) i hörnet (0, 0).
    svf = np.full((4, 4), 0.75, dtype=np.float64)
    svf[0, 0] = np.nan
    skuggmask = np.ones((4, 4), dtype=np.float64)
    skuggmask[0, 0] = np.nan
    skuggmask[:, 0] = np.where(np.isnan(skuggmask[:, 0]), np.nan, 0.2)  # morgonskugga längs fasad

    albedo_mark = np.full((4, 4), 0.15, dtype=np.float64)  # asfalt
    solhojd = math.radians(48.0)

    # Före: inga gatuträd, varm sommardag i Stockholm (30 °C, 45 % RH, 1.5 m/s vid 10 m).
    fore = berakna_varmestress(
        svf=svf,
        skuggmask=skuggmask,
        solhojd_rad=solhojd,
        sol_direkt_w_m2=800.0,
        sol_diffus_w_m2=150.0,
        t_luft_c=30.0,
        relativ_fuktighet_pct=45.0,
        vind_10m_m_s=1.5,
        albedo_mark=albedo_mark,
    )

    # Efter: allé med 8 nya trädkronor i solbelysta mittfältet.
    trad_efter = np.zeros((4, 4), dtype=np.bool_)
    trad_efter[1:3, 1:4] = True
    trad_efter[3, 1:3] = True

    efter = berakna_varmestress(
        svf=svf,
        skuggmask=skuggmask,
        solhojd_rad=solhojd,
        sol_direkt_w_m2=800.0,
        sol_diffus_w_m2=150.0,
        t_luft_c=30.0,
        relativ_fuktighet_pct=45.0,
        vind_10m_m_s=1.5,
        albedo_mark=albedo_mark,
        trad_mask=trad_efter,
    )

    jamf = jamfor_varmestress(fore, efter)

    assert fore.statistik.antal_nan_celler == 1
    assert fore.statistik.antal_giltiga_celler == 15
    assert jamf.antal_jamforda_utci_celler == 15
    assert jamf.delta_t_mrt_medel_c is not None and jamf.delta_t_mrt_medel_c < -5.0
    assert jamf.delta_utci_medel_c is not None and jamf.delta_utci_medel_c < -2.0
    # Under nya trädkronor sjunker både T_mrt och UTCI strikt.
    assert np.all(jamf.delta_t_mrt_c[trad_efter] < -15.0)
    assert np.all(jamf.delta_utci_c[trad_efter] < -4.0)
    # Andelen yta med stark/mycket stark värmestress minskar strikt.
    assert jamf.delta_andel_stark_varmestress < 0.0
    assert np.isnan(jamf.delta_utci_c[0, 0])

    # Deterministisk JSON-serialisering utan NaN/Inf.
    json_1 = varmestress_till_json(efter)
    json_2 = varmestress_till_json(efter)
    assert json_1 == json_2
    parsed = json.loads(json_1)
    assert parsed["metodstatus"] == "skiss"
    assert len(parsed["kontrollsummor"]["utci_c_sha256"]) == 64

    jamf_json_1 = varmestress_jamforelse_till_json(jamf)
    jamf_json_2 = varmestress_jamforelse_till_json(jamf)
    assert jamf_json_1 == jamf_json_2
    jamf_parsed = json.loads(jamf_json_1)
    assert jamf_parsed["delta_utci_medel_c"] == pytest.approx(jamf.delta_utci_medel_c)
    assert jamf_parsed["delta_andel_stark_varmestress"] == pytest.approx(
        jamf.delta_andel_stark_varmestress
    )


def test_varmestress_fotgangarvind_och_utom_intervall_utan_krasch() -> None:
    """Fotgängarvind skalas identiskt med vind_fotgangarhojd_till_10m; utom-intervall flaggas."""
    res_fot = berakna_varmestress(
        svf=0.8,
        skuggmask=1.0,
        solhojd_rad=math.radians(40.0),
        sol_direkt_w_m2=650.0,
        sol_diffus_w_m2=120.0,
        t_luft_c=24.0,
        relativ_fuktighet_pct=50.0,
        vind_fotgangarhojd_m_s=1.5,
        vind_hojd_m=1.5,
    )
    v10 = vind_fotgangarhojd_till_10m(1.5, hojd_m=1.5)
    res_10m = berakna_varmestress(
        svf=0.8,
        skuggmask=1.0,
        solhojd_rad=math.radians(40.0),
        sol_direkt_w_m2=650.0,
        sol_diffus_w_m2=120.0,
        t_luft_c=24.0,
        relativ_fuktighet_pct=50.0,
        vind_10m_m_s=v10,
    )
    assert math.isclose(float(res_fot.utci.utci_c), float(res_10m.utci.utci_c), rel_tol=1e-14)

    # Lugn vind (v_10m = 0.2 m/s < 0.5 m/s) flaggas utan krasch och ger valid JSON med null-stats.
    res_lugn = berakna_varmestress(
        svf=np.array([[0.8, np.nan]]),
        skuggmask=np.array([[1.0, 1.0]]),
        solhojd_rad=math.radians(40.0),
        sol_direkt_w_m2=650.0,
        sol_diffus_w_m2=120.0,
        t_luft_c=24.0,
        relativ_fuktighet_pct=50.0,
        vind_10m_m_s=0.2,
    )
    assert res_lugn.statistik.antal_utanfor_giltighetsomrade == 1
    assert res_lugn.statistik.antal_giltiga_celler == 0
    assert res_lugn.statistik.utci_medel_c is None
    assert len(res_lugn.varningar) > 0
    assert json.loads(varmestress_till_json(res_lugn))["statistik"]["utci_medel_c"] is None

    # Exakt en av vind_10m_m_s och vind_fotgangarhojd_m_s krävs.
    with pytest.raises(ValueError):
        berakna_varmestress(0.8, 1.0, 0.5, 500.0, 100.0, 20.0, 50.0)
    with pytest.raises(ValueError):
        berakna_varmestress(
            0.8, 1.0, 0.5, 500.0, 100.0, 20.0, 50.0, vind_10m_m_s=1.0, vind_fotgangarhojd_m_s=1.0
        )


def test_mikroklimat_paketgranser_noll_korsdomanimporter() -> None:
    """`src/mikroklimat/` får endast importera stdlib, numpy/scipy och `gemensamt`."""
    src_rot = Path(__file__).resolve().parents[2] / "src"
    alla_interna = {
        p.name for p in src_rot.iterdir() if p.is_dir() and not p.name.startswith((".", "__"))
    }
    tillatna_interna = {"mikroklimat", "gemensamt"}
    brott: list[str] = []
    for pyfil in sorted((src_rot / "mikroklimat").rglob("*.py")):
        trad = ast.parse(pyfil.read_text(encoding="utf-8"), filename=str(pyfil))
        for nod in ast.walk(trad):
            if isinstance(nod, ast.Import):
                for alias in nod.names:
                    topp = alias.name.split(".")[0]
                    if topp in alla_interna and topp not in tillatna_interna:
                        brott.append(f"{pyfil.name}:{nod.lineno} -> {topp}")
            elif isinstance(nod, ast.ImportFrom) and nod.level == 0 and nod.module:
                topp = nod.module.split(".")[0]
                if topp in alla_interna and topp not in tillatna_interna:
                    brott.append(f"{pyfil.name}:{nod.lineno} -> {topp}")
    assert not brott, f"Otillåtna korsdomänimporter i src/mikroklimat/: {brott}"
