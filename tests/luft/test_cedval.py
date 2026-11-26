# SPDX-License-Identifier: MIT
"""Deterministiska fysik- och kontraktstester för CEDVAL A1-1-validering (`src/luft/cedval.py`).

Testar:
1. Analytisk neutral logaritmisk ABL-profil (`abl_log_profil_m_s`).
2. Publicerad CEDVAL A1-1-byggnadsgeometri och voxeltäckning (`bygg_cedval_a11_doman`).
3. Faktisk 3D-körning med Flodens vindlösare (`snabbvind` och snabb grov `lbm`)
   samt att `jamford_mot_vindtunnel_data is False` och `metodstatus == "skiss"`.
4. FAIRMODE/COST 732-valideringsmått (`RMSE`, `NMSE`, `FB`, `FAC2`, `hit_rate`)
   mot handräknade syntetiska serier + tydliga fel på saknad/ogiltig CSV-fil.
5. K37-kompatibel JSON-serialisering kontrollerad mot `benchmark.format.kontrollera`,
   Markdown-rapport och CLI-verktyget `tools/validera_cedval.py`.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pytest
from tools.validera_cedval import main as cli_main

from benchmark.format import kontrollera as kontrollera_k37
from luft.cedval import (
    CEDVAL_FALL_ID,
    K37_FORMAT,
    CedvalA11Spec,
    abl_log_profil_m_s,
    berakna_cost732_matt,
    bygg_cedval_a11_doman,
    cedval_till_dict,
    formatera_cedval_rapport_md,
    friktionshastighet_u_star_m_s,
    jamfor_mot_cedval_csv,
    kor_cedval_a11_validering,
)
from luft.lbm import LbmParametrar

# --------------------------------------------------------------------------------------
# 1. Analytisk ABL-logprofil
# --------------------------------------------------------------------------------------


class TestAblLogProfil:
    def test_referenshojd_exakt_och_strikt_okande(self) -> None:
        spec = CedvalA11Spec()
        u_vid_ref = abl_log_profil_m_s(
            spec.z_ref_m,
            u_ref_m_s=spec.u_ref_m_s,
            z_ref_m=spec.z_ref_m,
            z0_m=spec.skrovlighet_z0_m,
            kappa=spec.kappa,
        )
        assert abs(u_vid_ref - spec.u_ref_m_s) < 1e-12

        z = np.array([0.0, 1.0, 5.0, 10.0, 25.0, 50.0, 100.0], dtype=np.float64)
        u = abl_log_profil_m_s(
            z,
            u_ref_m_s=spec.u_ref_m_s,
            z_ref_m=spec.z_ref_m,
            z0_m=spec.skrovlighet_z0_m,
            kappa=spec.kappa,
        )
        assert u[0] == 0.0
        assert abs(float(u[4]) - spec.u_ref_m_s) < 1e-12
        assert np.all(np.diff(u) > 0.0)

        # Verifiera identitet mot (u_* / kappa) * ln((z + z0) / z0)
        u_star = friktionshastighet_u_star_m_s(
            u_ref_m_s=spec.u_ref_m_s,
            z_ref_m=spec.z_ref_m,
            z0_m=spec.skrovlighet_z0_m,
            kappa=spec.kappa,
        )
        for zi, ui in zip(z[1:], u[1:], strict=True):
            forvantat = (u_star / spec.kappa) * math.log(
                (float(zi) + spec.skrovlighet_z0_m) / spec.skrovlighet_z0_m
            )
            assert abs(float(ui) - forvantat) < 1e-12

    def test_noll_vid_mark_och_negativ_hojd(self) -> None:
        assert abl_log_profil_m_s(0.0, u_ref_m_s=6.0, z_ref_m=25.0, z0_m=0.7) == 0.0
        assert abl_log_profil_m_s(-10.0, u_ref_m_s=6.0, z_ref_m=25.0, z0_m=0.7) == 0.0
        u_arr = abl_log_profil_m_s(
            np.array([-5.0, -0.7, 0.0, 25.0]),
            u_ref_m_s=6.0,
            z_ref_m=25.0,
            z0_m=0.7,
        )
        assert np.array_equal(u_arr[:3], np.zeros(3))
        assert abs(float(u_arr[3]) - 6.0) < 1e-12

    def test_ogiltiga_parametrar_ger_fel(self) -> None:
        with pytest.raises(ValueError):
            abl_log_profil_m_s(10.0, u_ref_m_s=0.0, z_ref_m=25.0, z0_m=0.7)
        with pytest.raises(ValueError):
            abl_log_profil_m_s(10.0, u_ref_m_s=6.0, z_ref_m=0.5, z0_m=0.7)
        with pytest.raises(ValueError):
            abl_log_profil_m_s(10.0, u_ref_m_s=6.0, z_ref_m=25.0, z0_m=-0.1)


# --------------------------------------------------------------------------------------
# 2. CEDVAL A1-1 byggnadsgeometri och domän
# --------------------------------------------------------------------------------------


class TestCedvalGeometri:
    def test_publicerade_standardmatt_och_voxeltackning_5m(self) -> None:
        spec = CedvalA11Spec()
        assert spec.langd_x_m == 20.0
        assert spec.bredd_y_m == 30.0
        assert spec.hojd_h_m == 25.0
        assert spec.skrovlighet_z0_m == 0.7
        assert spec.u_ref_m_s == 6.0
        assert spec.z_ref_m == 25.0
        assert spec.kappa == 0.41
        assert spec.blockeringsgrad < 0.03

        doman = bygg_cedval_a11_doman(spec, cellstorlek_m=5.0)
        assert doman.byggnad_x_min_m == pytest.approx(-10.0)
        assert doman.byggnad_x_max_m == pytest.approx(10.0)
        assert doman.byggnad_y_min_m == pytest.approx(-15.0)
        assert doman.byggnad_y_max_m == pytest.approx(15.0)
        assert doman.byggnad_z_max_m == pytest.approx(25.0)
        # 20/5 * 30/5 * 25/5 = 4 * 6 * 5 = 120 byggnadsceller
        assert doman.antal_solidceller == 120
        assert doman.faktisk_blockeringsgrad < 0.03

        # Alla fasta celler ligger exakt inom byggnadsboxen
        ii, jj, kk = np.nonzero(doman.grid.fast)
        assert np.all(doman.x_m[ii] > -10.0) and np.all(doman.x_m[ii] < 10.0)
        assert np.all(doman.y_m[jj] > -15.0) and np.all(doman.y_m[jj] < 15.0)
        assert np.all(doman.z_m[kk] > 0.0) and np.all(doman.z_m[kk] < 25.0)
        # Och inloppsprofilen matchar exakt den analytiska profilen
        assert np.allclose(
            doman.inloppsprofil_m_s,
            abl_log_profil_m_s(
                doman.z_m,
                u_ref_m_s=spec.u_ref_m_s,
                z_ref_m=spec.z_ref_m,
                z0_m=spec.skrovlighet_z0_m,
            ),
            atol=1e-12,
        )

    def test_finare_cellstorlek_bevarar_exakta_byggnadsmatt(self) -> None:
        doman = bygg_cedval_a11_doman(CedvalA11Spec(), cellstorlek_m=2.5)
        assert doman.antal_solidceller == 8 * 12 * 10
        assert doman.byggnad_x_max_m - doman.byggnad_x_min_m == pytest.approx(20.0)
        assert doman.byggnad_y_max_m - doman.byggnad_y_min_m == pytest.approx(30.0)
        assert doman.byggnad_z_max_m == pytest.approx(25.0)


# --------------------------------------------------------------------------------------
# 3. Deterministisk 3D-körning (snabbvind + snabb grov LBM)
# --------------------------------------------------------------------------------------


class TestCedvalKorning:
    def test_snabbvind_fysik_och_determinism(self) -> None:
        res1 = kor_cedval_a11_validering(cellstorlek_m=5.0, losare="snabbvind")
        res2 = kor_cedval_a11_validering(cellstorlek_m=5.0, losare="snabbvind")

        assert res1.kortid_s < 10.0
        assert res1.metodstatus == "skiss"
        assert res1.jamford_mot_vindtunnel_data is False
        # No-slip i byggnadsceller
        assert res1.max_hastighet_i_solid_m_s == 0.0
        # God masskonservering
        assert res1.projektion_divergens_rel is not None
        assert res1.projektion_divergens_rel < 1e-5
        assert res1.rms_divergens_1_s < 0.01
        assert res1.max_divergens_1_s < 0.5
        # Inloppsprofil och uppströms vinddämpning
        assert res1.inloppsprofil_rel_fel < 0.01
        assert res1.uppstroms_dampning_norm < res1.uppstroms_inlopps_mark_norm
        # Tydlig fysisk vindskugga bakom byggnaden
        assert res1.min_u_x_la_norm < 0.5
        # Determinism
        assert np.array_equal(res1.vindfalt.u_m_s, res2.vindfalt.u_m_s)

    def test_lbm_grov_ger_lavirvel_och_ateranslutning(self) -> None:
        kompakt_spec = CedvalA11Spec(
            uppstroms_m=50.0,
            nedstroms_m=150.0,
            sidomarginal_m=50.0,
            domanhojd_m=75.0,
            cellstorlek_m=5.0,
        )
        param = LbmParametrar(
            u_lattice=0.06,
            upprampning_genomstromningar=0.5,
            medel_genomstromningar=0.5,
            vaggmodell="loglag",
            inflodesrand="extrapolation",
            utflodesrand="tryck",
            start="profil",
        )
        res = kor_cedval_a11_validering(
            kompakt_spec,
            cellstorlek_m=5.0,
            losare="lbm",
            lbm_parametrar=param,
        )
        assert res.kortid_s < 10.0
        assert res.metodstatus == "skiss"
        assert res.jamford_mot_vindtunnel_data is False
        assert res.max_hastighet_i_solid_m_s == 0.0
        assert res.rms_divergens_1_s < 0.02
        assert res.min_u_x_la_norm < 0.0
        assert res.har_recirkulationszon is True
        assert res.ateranslutning_x_r_m is not None
        assert res.ateranslutning_x_r_over_h is not None
        assert res.ateranslutning_x_r_over_h > 0.0

    def test_okand_losare_ger_fel(self) -> None:
        with pytest.raises(ValueError, match="Okänd lösare"):
            kor_cedval_a11_validering(losare="okand")  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# 4. COST 732 / FAIRMODE-mått och CSV-jämförelse
# --------------------------------------------------------------------------------------


class TestCost732OchCsv:
    def test_handraknade_cost732_matt(self) -> None:
        # Perfekt överensstämmelse
        perfekt = berakna_cost732_matt([0.5, 1.0, 1.5], [0.5, 1.0, 1.5])
        assert perfekt.rmse == pytest.approx(0.0)
        assert perfekt.nmse == pytest.approx(0.0)
        assert perfekt.fb == pytest.approx(0.0)
        assert perfekt.fac2 == pytest.approx(1.0)
        assert perfekt.hit_rate == pytest.approx(1.0)
        assert perfekt.korrelation_r == pytest.approx(1.0)

        # Handräknad icke-trivial serie:
        # O = [1, 2, 4, 1] -> mean_O = 2.0
        # P = [1, 1, 2, 3] -> mean_P = 1.75
        # diff = [0, -1, -2, +2], mse = (0 + 1 + 4 + 4)/4 = 2.25, rmse = 1.5
        # nmse = 2.25 / (2.0 * 1.75) = 9/14
        # fb = 2 * (2.0 - 1.75) / (2.0 + 1.75) = 2/15
        # P/O = [1.0, 0.5, 0.5, 3.0] -> 3 av 4 inom [0.5, 2.0] -> fac2 = 0.75
        # hit_rate (D=0.25, W=0.05): bara punkt 0 klarar |P-O| <= max(0.25|O|, 0.05) -> 0.25
        m = berakna_cost732_matt([1.0, 2.0, 4.0, 1.0], [1.0, 1.0, 2.0, 3.0])
        assert m.antal_punkter == 4
        assert m.rmse == pytest.approx(1.5, rel=1e-12)
        assert m.nmse == pytest.approx(9.0 / 14.0, rel=1e-12)
        assert m.fb == pytest.approx(2.0 / 15.0, rel=1e-12)
        assert m.fac2 == pytest.approx(0.75, rel=1e-12)
        assert m.hit_rate == pytest.approx(0.25, rel=1e-12)
        assert m.bias == pytest.approx(-0.25, rel=1e-12)

    def test_noll_och_negativa_vakhastigheter_i_hit_rate_och_fac2(self) -> None:
        # Punkt 0: exakt nollträff -> både FAC2 och hit_rate
        # Punkt 1: negativ återströmning inom faktor 2 och 25 % -> båda träff
        # Punkt 2: fel tecken men |diff|=0.04 <= W=0.05 -> hit_rate träff, FAC2 miss
        m = berakna_cost732_matt([0.0, -0.4, -0.02], [0.0, -0.35, 0.02])
        assert m.fac2 == pytest.approx(2.0 / 3.0, rel=1e-12)
        assert m.hit_rate == pytest.approx(1.0, rel=1e-12)

    def test_saknad_och_ogiltig_csv_ger_tydligt_fel(self, tmp_path: Path) -> None:
        res = kor_cedval_a11_validering(cellstorlek_m=5.0, losare="snabbvind")
        with pytest.raises(FileNotFoundError, match="CEDVAL"):
            jamfor_mot_cedval_csv(res, tmp_path / "finns_inte.csv")

        fel_kol = tmp_path / "fel_kolumner.csv"
        fel_kol.write_text("x_m,y_m,z_m,u_norm\n10,0,5,0.5\n", encoding="utf-8")
        with pytest.raises(ValueError, match="saknar obligatoriska kolumner"):
            jamfor_mot_cedval_csv(res, fel_kol)

        utanfor = tmp_path / "utanfor.csv"
        utanfor.write_text(
            "x_m,y_m,z_m,u_norm,v_norm,w_norm\n9999,0,5,0.5,0.0,0.0\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Inga mätpunkter"):
            jamfor_mot_cedval_csv(res, utanfor)

    def test_csv_jamforelse_mot_modellens_eget_falt(self, tmp_path: Path) -> None:
        res = kor_cedval_a11_validering(cellstorlek_m=5.0, losare="snabbvind")
        doman = res.doman
        u_ref = doman.spec.u_ref_m_s
        u_norm_3d = res.vindfalt.u_m_s / u_ref

        csv_fil = tmp_path / "syntetisk_cedval.csv"
        i_bak = doman.byggnad_i_intervall[1]
        j_mitt = doman.grid.form[1] // 2
        with csv_fil.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["x_m", "y_m", "z_m", "u_norm", "v_norm", "w_norm"])
            # 4 luftceller i lä + 1 i byggnad + 1 utanför domän
            for di in (1, 3, 6, 10):
                i = i_bak + di
                k = 1
                w.writerow(
                    [
                        f"{doman.x_m[i]:.4f}",
                        f"{doman.y_m[j_mitt]:.4f}",
                        f"{doman.z_m[k]:.4f}",
                        f"{u_norm_3d[0, i, j_mitt, k]:.8f}",
                        f"{u_norm_3d[1, i, j_mitt, k]:.8f}",
                        f"{u_norm_3d[2, i, j_mitt, k]:.8f}",
                    ]
                )
            w.writerow(["0.0", "0.0", "5.0", "0.0", "0.0", "0.0"])  # i solid byggnad
            w.writerow(["1000.0", "0.0", "5.0", "1.0", "0.0", "0.0"])  # utanför domän

        falt = jamfor_mot_cedval_csv(res, csv_fil)
        assert falt.antal_rader_totalt == 6
        assert falt.antal_jamforda == 4
        assert falt.antal_i_solid == 1
        assert falt.antal_utanfor_doman == 1
        assert falt.rmse < 1e-7
        assert falt.fac2 == pytest.approx(1.0)
        assert falt.hit_rate == pytest.approx(1.0)
        assert falt.godkand_cost732 is True


# --------------------------------------------------------------------------------------
# 5. K37-JSON, Markdown-rapport och CLI
# --------------------------------------------------------------------------------------


class TestRapporterOchCli:
    def test_k37_json_och_markdown_utan_och_med_csv(self, tmp_path: Path) -> None:
        res = kor_cedval_a11_validering(cellstorlek_m=5.0, losare="snabbvind")
        dok = cedval_till_dict(res)
        assert dok["format"] == K37_FORMAT
        kontrollera_k37(dok)
        json_str = json.dumps(dok, allow_nan=False)
        assert CEDVAL_FALL_ID in json_str
        luft_fall = dok["delar"]["luft"]["fall"][0]
        assert luft_fall["avvikelse"] is None
        assert luft_fall["extra"]["metodstatus"] == "skiss"
        assert luft_fall["extra"]["jamford_mot_vindtunnel_data"] is False

        md = formatera_cedval_rapport_md(res)
        assert "CEDVAL A1-1" in md
        assert "`skiss`" in md
        assert "jamford_mot_vindtunnel_data = False" in md

        # Skriv en giltig lokal syntetisk CSV och testa även jämfört läge + CLI
        doman = res.doman
        u_norm = res.vindfalt.u_m_s / doman.spec.u_ref_m_s
        i = doman.byggnad_i_intervall[1] + 4
        j = doman.grid.form[1] // 2
        csv_fil = tmp_path / "lokal.csv"
        csv_fil.write_text(
            "x_m,y_m,z_m,u_norm,v_norm,w_norm\n"
            f"{doman.x_m[i]},{doman.y_m[j]},{doman.z_m[1]},"
            f"{u_norm[0, i, j, 1]},{u_norm[1, i, j, 1]},{u_norm[2, i, j, 1]}\n",
            encoding="utf-8",
        )
        falt = jamfor_mot_cedval_csv(res, csv_fil)
        dok_med = cedval_till_dict(res, falt)
        kontrollera_k37(dok_med)
        json.dumps(dok_med, allow_nan=False)
        assert dok_med["delar"]["luft"]["fall"][0]["avvikelse"] is not None
        assert dok_med["delar"]["luft"]["fall"][0]["extra"]["jamford_mot_vindtunnel_data"] is True

        md_med = formatera_cedval_rapport_md(res, falt)
        assert "COST 732" in md_med
        assert falt.csv_sha256[:16] in md_med

        # CLI-verktyg: både lyckad körning och tydligt fel på saknad CSV
        ut_json = tmp_path / "ut.json"
        ut_md = tmp_path / "ut.md"
        rc = cli_main(
            [
                "--cellstorlek-m",
                "5.0",
                "--losare",
                "snabbvind",
                "--ut-json",
                str(ut_json),
                "--ut-md",
                str(ut_md),
            ]
        )
        assert rc == 0
        assert ut_json.is_file() and ut_md.is_file()
        kontrollera_k37(json.loads(ut_json.read_text(encoding="utf-8")))

        rc_fel = cli_main(["--cedval-csv", str(tmp_path / "saknas.csv")])
        assert rc_fel != 0
