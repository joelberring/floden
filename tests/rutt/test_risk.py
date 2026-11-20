"""Enhetstester för kumulativ översvämningsrisk i vägnät och adressnåbarhet."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from rutt.parametrar import (
    FordonParametrar,
    RuttParametrarFel,
    RuttParametrarK7,
    las_testparametrar_fran_konfig,
)
from rutt.risk import (
    berakna_kumulativ_sannolikhet,
    berakna_vag_kumulativ_risk,
    interpolera_kritisk_aterkomsttid_ar,
)
from rutt.vagnat import VagKant, VagNat, VagNatFel, VagNod

REPO_ROT = Path(__file__).resolve().parents[2]
CONFIG_TOML = REPO_ROT / "config.toml"


def _skarp_param_fran_test() -> RuttParametrarK7:
    """Bygger en fullständigt ifylld icke-test K7-parameteruppsättning för tester."""
    test_param = las_testparametrar_fran_konfig(CONFIG_TOML)
    return RuttParametrarK7(
        fordon=test_param.fordon,
        entre_gransdjup_m=test_param.entre_gransdjup_m,
        gangavstand_max_m=test_param.gangavstand_max_m,
        gangvag_djup_max_m=test_param.gangvag_djup_max_m,
        gangvag_fara_max_m2_s=test_param.gangvag_fara_max_m2_s,
        insatstidsmal_min=test_param.insatstidsmal_min,
        ar_testvarde=False,
    )


class TestKumulativVagRisk(unittest.TestCase):
    """Verifierar log-interpolation, sannolikhetsberäkning, broar, nåbarhet och K7-vakt."""

    def test_analytisk_log_interpolation_och_kumulativ_sannolikhet(self) -> None:
        # d(10) = 0.10 m, d(40) = 0.50 m, grans = 0.30 m (mittpunkt)
        # => ln(T*) = ln(10) + 0.5 * (ln(40) - ln(10)) = ln(20) => T* = 20.0 år
        t_krit, overskriden_min = interpolera_kritisk_aterkomsttid_ar(
            [10.0, 40.0, 100.0],
            [0.10, 0.50, 0.80],
            gransdjup_m=0.30,
        )
        self.assertFalse(overskriden_min)
        self.assertAlmostEqual(t_krit, 20.0, places=12)

        # P_50 = 1 - (1 - 1/20)^50 = 1 - 0.95^50
        p_ar = 1.0 / t_krit
        p_50 = berakna_kumulativ_sannolikhet(p_ar, 50.0)
        forvantad_p_50 = 1.0 - math.pow(0.95, 50.0)
        self.assertAlmostEqual(p_50, forvantad_p_50, places=12)

        # Redan överskriden vid lägsta återkomsttid T_1 = 5.0
        t_min, flagga = interpolera_kritisk_aterkomsttid_ar(
            [5.0, 20.0, 100.0],
            [0.35, 0.50, 0.90],
            gransdjup_m=0.30,
        )
        self.assertTrue(flagga)
        self.assertAlmostEqual(t_min, 5.0, places=12)

        # Exakt träff vid högsta återkomsttid (inga flyttalsfel över max_t)
        t_exakt, flagga_exakt = interpolera_kritisk_aterkomsttid_ar(
            [10.0, 100.0],
            [0.10, 0.30],
            gransdjup_m=0.30,
        )
        self.assertFalse(flagga_exakt)
        self.assertEqual(t_exakt, 100.0)

        # Aldrig överskriden
        t_inf, flagga_inf = interpolera_kritisk_aterkomsttid_ar(
            [10.0, 50.0, 100.0],
            [0.05, 0.15, 0.29],
            gransdjup_m=0.30,
        )
        self.assertFalse(flagga_inf)
        self.assertTrue(math.isinf(t_inf))

    def test_kant_overdjup_trapetsintegral_och_trunkering_vid_max_aterkomsttid(self) -> None:
        param = _skarp_param_fran_test()
        # Ambulans har gransdjup_m = 0.30 i testkonfig
        self.assertAlmostEqual(param.fordon["ambulans"].gransdjup_m or 0.0, 0.30)

        graf = VagNat.fran_komponenter(
            [
                VagNod("A", 150000.0, 6577000.0, 10.0),
                VagNod("B", 150200.0, 6577000.0, 10.0),
                VagNod("C", 150400.0, 6577000.0, 10.0),
            ],
            [
                VagKant("k_lag", "A", "B", 200.0, 36.0),
                VagKant("k_extrem", "B", "C", 200.0, 36.0),
            ],
        )

        # k_lag översvämmas vid T* = 20 år (d(10)=0.10, d(40)=0.50, d(100)=0.70)
        # k_extrem når >= 0.30 först vid 200- och 500-årsregn
        # (d(100)=0.20, d(200)=0.30, d(500)=0.60)
        djup_per_t: dict[float, dict[str, float]] = {
            10.0: {"k_lag": 0.10, "k_extrem": 0.00},
            40.0: {"k_lag": 0.50, "k_extrem": 0.10},
            100.0: {"k_lag": 0.70, "k_extrem": 0.20},
            200.0: {"k_lag": 0.85, "k_extrem": 0.30},
            500.0: {"k_lag": 1.10, "k_extrem": 0.60},
        }

        res_100 = berakna_vag_kumulativ_risk(
            graf,
            djup_per_t,
            param,
            "ambulans",
            max_aterkomsttid_ar=100.0,
            tidshorisont_ar=50.0,
            skarp=True,
        )
        self.assertEqual(res_100.aterkomsttider_ar, (10.0, 40.0, 100.0))
        self.assertEqual(res_100.antal_kanter_med_risk, 1)

        # k_lag: T* = 20.0, p = 0.05, P_50 = 1 - 0.95^50
        k_lag = res_100.kanter["k_lag"]
        self.assertAlmostEqual(k_lag.kritisk_aterkomsttid_ar, 20.0, places=12)
        self.assertAlmostEqual(k_lag.arlig_overskridande_sannolikhet, 0.05, places=12)
        self.assertAlmostEqual(
            k_lag.kumulativ_sannolikhet,
            1.0 - math.pow(0.95, 50.0),
            places=12,
        )
        self.assertAlmostEqual(
            res_100.max_kumulativ_kant_sannolikhet,
            k_lag.kumulativ_sannolikhet,
            places=12,
        )
        self.assertEqual(
            k_lag.koordinater,
            ((150000.0, 6577000.0), (150200.0, 6577000.0)),
        )
        # Trapetsintegral för k_lag över p = [0.10, 0.025, 0.01] med överdjup [0.0, 0.2, 0.4]:
        # 0.5*(0.0+0.2)*(0.10-0.025) + 0.5*(0.2+0.4)*(0.025-0.01)
        # = 0.0075 + 0.0045 = 0.0120 m/år
        self.assertAlmostEqual(k_lag.forvantat_arligt_overdjup_m, 0.012, places=12)
        self.assertAlmostEqual(k_lag.max_djup_vid_tmax_m, 0.70, places=12)

        # k_extrem har 0 risk upp till 100-årsregn
        k_ext_100 = res_100.kanter["k_extrem"]
        self.assertTrue(math.isinf(k_ext_100.kritisk_aterkomsttid_ar))
        self.assertEqual(k_ext_100.arlig_overskridande_sannolikhet, 0.0)
        self.assertEqual(k_ext_100.kumulativ_sannolikhet, 0.0)
        self.assertEqual(k_ext_100.forvantat_arligt_overdjup_m, 0.0)
        self.assertAlmostEqual(k_ext_100.max_djup_vid_tmax_m, 0.20, places=12)

        # Upp till 500-årsregn får k_extrem positiv risk med T* = 200.0 år
        res_500 = berakna_vag_kumulativ_risk(
            graf,
            djup_per_t,
            param,
            "ambulans",
            max_aterkomsttid_ar=500.0,
            tidshorisont_ar=50.0,
            skarp=True,
        )
        self.assertEqual(res_500.aterkomsttider_ar, (10.0, 40.0, 100.0, 200.0, 500.0))
        self.assertEqual(res_500.antal_kanter_med_risk, 2)

        k_ext_500 = res_500.kanter["k_extrem"]
        self.assertAlmostEqual(k_ext_500.kritisk_aterkomsttid_ar, 200.0, places=12)
        self.assertAlmostEqual(k_ext_500.arlig_overskridande_sannolikhet, 1.0 / 200.0, places=12)
        self.assertAlmostEqual(
            k_ext_500.kumulativ_sannolikhet,
            1.0 - math.pow(1.0 - 1.0 / 200.0, 50.0),
            places=12,
        )
        # Överdjup för k_extrem vid p=[0.1, 0.025, 0.01, 0.005, 0.002]: [0, 0, 0, 0, 0.30]
        # => 0.5 * (0.0 + 0.30) * (0.005 - 0.002) = 0.00045 m/år
        self.assertAlmostEqual(k_ext_500.forvantat_arligt_overdjup_m, 0.00045, places=12)
        self.assertAlmostEqual(k_ext_500.max_djup_vid_tmax_m, 0.60, places=12)

    def test_bro_over_oversvammad_terrang_far_noll_risk_under_brobana(self) -> None:
        param = _skarp_param_fran_test()
        graf = VagNat.fran_komponenter(
            [
                VagNod("S", 150000.0, 6577000.0, 15.0),
                VagNod("M", 150360.0, 6577000.0, 15.0),
            ],
            [
                VagKant(
                    kant_id="k_bro",
                    fran_nod="S",
                    till_nod="M",
                    langd_m=360.0,
                    normal_hastighet_km_h=36.0,
                    typ="bro",
                    vagbana_rh2000=15.0,
                )
            ],
        )

        # Marken under bron har 2.0–3.5 m vatten, men vattenytan stannar under 15.0 m RH2000
        djup_per_t = {
            10.0: {"k_bro": 2.0},
            50.0: {"k_bro": 2.8},
            100.0: {"k_bro": 3.5},
        }
        niva_under_bro = {
            10.0: {"k_bro": 12.5},
            50.0: {"k_bro": 13.8},
            100.0: {"k_bro": 14.9},
        }

        res_torr_bro = berakna_vag_kumulativ_risk(
            graf,
            djup_per_t,
            param,
            "ambulans",
            max_aterkomsttid_ar=100.0,
            tidshorisont_ar=50.0,
            stationer={"st1": "S"},
            adresser={"adr1": "M"},
            vattenniva_per_aterkomsttid=niva_under_bro,
        )
        bro_risk = res_torr_bro.kanter["k_bro"]
        self.assertTrue(math.isinf(bro_risk.kritisk_aterkomsttid_ar))
        self.assertEqual(bro_risk.arlig_overskridande_sannolikhet, 0.0)
        self.assertEqual(bro_risk.kumulativ_sannolikhet, 0.0)
        self.assertEqual(bro_risk.forvantat_arligt_overdjup_m, 0.0)
        self.assertEqual(bro_risk.max_djup_vid_tmax_m, 0.0)

        adr_bro = res_torr_bro.adresser["adr1"]
        self.assertTrue(math.isinf(adr_bro.kritisk_aterkomsttid_ar))
        self.assertEqual(adr_bro.kumulativ_ofarbar_sannolikhet, 0.0)
        self.assertAlmostEqual(adr_bro.varsta_insatstid_s, 36.0, places=12)

        # Om vattennivån stiger över bron (15.5 m => eff_djup = 0.50 m >= 0.30 m) vid T=100
        niva_over_bro = {
            10.0: {"k_bro": 12.5},
            50.0: {"k_bro": 15.1},  # eff_djup = 0.10 m
            100.0: {"k_bro": 15.5},  # eff_djup = 0.50 m
        }
        res_overspolad = berakna_vag_kumulativ_risk(
            graf,
            djup_per_t,
            param,
            "ambulans",
            max_aterkomsttid_ar=100.0,
            tidshorisont_ar=50.0,
            vattenniva_per_aterkomsttid=niva_over_bro,
        )
        # Mittpunkt mellan T=50 (0.10 m) och T=100 (0.50 m) => T* = sqrt(5000)
        forvantad_t = math.sqrt(50.0 * 100.0)
        self.assertAlmostEqual(
            res_overspolad.kanter["k_bro"].kritisk_aterkomsttid_ar,
            forvantad_t,
            places=10,
        )
        self.assertGreater(res_overspolad.kanter["k_bro"].kumulativ_sannolikhet, 0.0)

    def test_adressnabarhet_med_station_omvag_och_blockerad_gata(self) -> None:
        param = _skarp_param_fran_test()
        # Nätverk:
        # S --(k_sm1, 500m)--> M1 --(k_m1m2, 500m)--> M2
        # S --(k_sb, 1000m)--> B --(k_bm1, 1000m)--> M1
        # Vid T=10: allt torrt -> M1: 50s, M2: 100s
        # Vid T=50: k_sm1 och k_m1m2 blockeras (>=0.30m), k_sb/k_bm1 torra
        #           -> M1 nås via omväg (200s), M2 onåbar!
        # Vid T=100: även k_bm1 blockeras -> både M1 och M2 onåbara!
        graf = VagNat.fran_komponenter(
            [
                VagNod("S", 0.0, 0.0, 10.0),
                VagNod("B", 500.0, 500.0, 12.0),
                VagNod("M1", 500.0, 0.0, 10.0),
                VagNod("M2", 1000.0, 0.0, 10.0),
            ],
            [
                VagKant("k_sm1", "S", "M1", 500.0, 36.0),
                VagKant("k_m1m2", "M1", "M2", 500.0, 36.0),
                VagKant("k_sb", "S", "B", 1000.0, 36.0),
                VagKant("k_bm1", "B", "M1", 1000.0, 36.0),
            ],
        )
        djup_per_t = {
            10.0: {"k_sm1": 0.0, "k_m1m2": 0.0, "k_sb": 0.0, "k_bm1": 0.0},
            50.0: {"k_sm1": 0.40, "k_m1m2": 0.40, "k_sb": 0.0, "k_bm1": 0.0},
            100.0: {"k_sm1": 0.60, "k_m1m2": 0.60, "k_sb": 0.0, "k_bm1": 0.50},
        }

        res_50 = berakna_vag_kumulativ_risk(
            graf,
            djup_per_t,
            param,
            "ambulans",
            max_aterkomsttid_ar=50.0,
            tidshorisont_ar=50.0,
            stationer={"st1": "S"},
            adresser={"adr_m1": "M1", "adr_m2": "M2"},
        )
        self.assertEqual(res_50.antal_adresser_med_risk, 1)

        # adr_m1 är alltid nåbar upp till T=50, värsta insatstid = 200s via omväg
        m1_50 = res_50.adresser["adr_m1"]
        self.assertTrue(math.isinf(m1_50.kritisk_aterkomsttid_ar))
        self.assertFalse(m1_50.overskriden_vid_min_t)
        self.assertEqual(m1_50.arlig_ofarbar_sannolikhet, 0.0)
        self.assertEqual(m1_50.kumulativ_ofarbar_sannolikhet, 0.0)
        self.assertAlmostEqual(m1_50.varsta_insatstid_s, 200.0, places=12)

        # adr_m2 blir onåbar vid T=50
        m2_50 = res_50.adresser["adr_m2"]
        self.assertEqual(m2_50.kritisk_aterkomsttid_ar, 50.0)
        self.assertFalse(m2_50.overskriden_vid_min_t)
        self.assertAlmostEqual(m2_50.arlig_ofarbar_sannolikhet, 0.02, places=12)
        self.assertAlmostEqual(
            m2_50.kumulativ_ofarbar_sannolikhet,
            1.0 - math.pow(0.98, 50.0),
            places=12,
        )
        self.assertTrue(math.isinf(m2_50.varsta_insatstid_s))

        # Upp till T=100 blir även adr_m1 onåbar vid T=100
        res_100 = berakna_vag_kumulativ_risk(
            graf,
            djup_per_t,
            param,
            "ambulans",
            max_aterkomsttid_ar=100.0,
            tidshorisont_ar=50.0,
            stationer={"st1": "S"},
            adresser={"adr_m1": "M1", "adr_m2": "M2"},
        )
        self.assertEqual(res_100.antal_adresser_med_risk, 2)
        m1_100 = res_100.adresser["adr_m1"]
        self.assertEqual(m1_100.kritisk_aterkomsttid_ar, 100.0)
        self.assertAlmostEqual(m1_100.arlig_ofarbar_sannolikhet, 0.01, places=12)
        self.assertAlmostEqual(
            m1_100.kumulativ_ofarbar_sannolikhet,
            1.0 - math.pow(0.99, 50.0),
            places=12,
        )
        self.assertTrue(math.isinf(m1_100.varsta_insatstid_s))

        # Serialisering till JSON utan NaN/Infinity fungerar
        json_text = json.dumps(res_50.till_dict(), allow_nan=False)
        inlast = json.loads(json_text)
        self.assertIsNone(inlast["adresser"]["adr_m1"]["kritisk_aterkomsttid_ar"])
        self.assertIsNone(inlast["adresser"]["adr_m2"]["varsta_insatstid_s"])
        self.assertEqual(inlast["antal_adresser_med_risk"], 1)

    def test_strikt_k7_validering_och_felhantering(self) -> None:
        graf = VagNat.fran_komponenter(
            [VagNod("S", 0.0, 0.0, 10.0), VagNod("M", 100.0, 0.0, 10.0)],
            [VagKant("k1", "S", "M", 100.0, 36.0)],
        )
        djup_per_t = {10.0: {"k1": 0.1}, 100.0: {"k1": 0.4}}

        # 1. Testparametrar nekas vid skarp=True utan tillat_testvarden=True
        test_param = las_testparametrar_fran_konfig(CONFIG_TOML)
        with self.assertRaises(RuttParametrarFel):
            berakna_vag_kumulativ_risk(
                graf,
                djup_per_t,
                test_param,
                "ambulans",
                skarp=True,
            )

        # Tillåts uttryckligen med tillat_testvarden=True
        res_test = berakna_vag_kumulativ_risk(
            graf,
            djup_per_t,
            test_param,
            "ambulans",
            skarp=True,
            tillat_testvarden=True,
        )
        self.assertEqual(res_test.antal_kanter_med_risk, 1)

        # 2. Saknat gransdjup_m (None) nekas alltid
        ofullstandig = RuttParametrarK7(
            fordon={
                "personbil": FordonParametrar(gransdjup_m=0.2, hastighet_per_djup=((0.0, 1.0),)),
                "ambulans": FordonParametrar(gransdjup_m=None, hastighet_per_djup=((0.0, 1.0),)),
                "tung_raddningsbil": FordonParametrar(
                    gransdjup_m=0.5, hastighet_per_djup=((0.0, 1.0),)
                ),
            },
            entre_gransdjup_m=0.1,
            gangavstand_max_m=100.0,
            gangvag_djup_max_m=0.2,
            gangvag_fara_max_m2_s=0.5,
            insatstidsmal_min={
                "sjukhus": 10.0,
                "vardboende": 10.0,
                "skola": 10.0,
                "forskola": 10.0,
            },
            ar_testvarde=False,
        )
        with self.assertRaises(RuttParametrarFel):
            berakna_vag_kumulativ_risk(
                graf,
                djup_per_t,
                ofullstandig,
                "ambulans",
                skarp=True,
            )

        # 3. Inga återkomsttider <= max_aterkomsttid_ar ger tydligt fel
        skarp_param = _skarp_param_fran_test()
        with self.assertRaises(RuttParametrarFel):
            berakna_vag_kumulativ_risk(
                graf,
                {200.0: {"k1": 0.4}, 500.0: {"k1": 0.8}},
                skarp_param,
                "ambulans",
                max_aterkomsttid_ar=100.0,
            )

        # 4. Okänd nod i stationer eller adresser ger VagNatFel
        with self.assertRaises(VagNatFel):
            berakna_vag_kumulativ_risk(
                graf,
                djup_per_t,
                skarp_param,
                "ambulans",
                stationer={"st1": "FINNS_INTE"},
                adresser={"adr1": "M"},
            )

        # 5. Ogiltiga argument i hjälpfunktionerna ger RuttParametrarFel
        with self.assertRaises(RuttParametrarFel):
            interpolera_kritisk_aterkomsttid_ar([100.0, 50.0], [0.1, 0.4], 0.3)
        with self.assertRaises(RuttParametrarFel):
            interpolera_kritisk_aterkomsttid_ar([10.0, 50.0], [0.1], 0.3)
        with self.assertRaises(RuttParametrarFel):
            berakna_kumulativ_sannolikhet(1.2, 50.0)
        with self.assertRaises(RuttParametrarFel):
            berakna_kumulativ_sannolikhet(0.05, 0.0)


if __name__ == "__main__":
    unittest.main()
