"""Tester för K7-ruttparametrar, null-avvisning och [rutt_test]-vakt (T07)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rutt import (
    FordonParametrar,
    RuttParametrarFel,
    RuttParametrarK7,
    berakna_hastighetsfaktor,
    las_ruttparametrar_k7,
    las_testparametrar_fran_konfig,
    skriv_ruttparametrar_k7,
    validera_parametrar,
)

REPO_ROT = Path(__file__).resolve().parents[2]
CONFIG_TOML = REPO_ROT / "config.toml"


class TestParametrarK7(unittest.TestCase):
    """Verifierar K7/v1-validering, JSON-I/O och hastighetskurva."""

    def test_null_varden_nekas_och_listar_alla_saknade_falt(self) -> None:
        param = RuttParametrarK7(
            fordon={
                "personbil": FordonParametrar(
                    gransdjup_m=None,
                    hastighet_per_djup=((0.0, 1.0), (0.1, 0.5)),
                ),
                "ambulans": FordonParametrar(
                    gransdjup_m=None,
                    hastighet_per_djup=(),
                ),
                "tung_raddningsbil": FordonParametrar(
                    gransdjup_m=None,
                    hastighet_per_djup=(),
                ),
            },
            entre_gransdjup_m=None,
            gangavstand_max_m=None,
            gangvag_djup_max_m=None,
            gangvag_fara_max_m2_s=None,
            insatstidsmal_min={
                "sjukhus": None,
                "vardboende": None,
                "skola": None,
                "forskola": None,
            },
        )

        with self.assertRaises(RuttParametrarFel) as ctx:
            validera_parametrar(param, skarp=True)

        feltext = str(ctx.exception)
        forvantade_falt = (
            "fordon.personbil.gransdjup_m",
            "fordon.ambulans.gransdjup_m",
            "fordon.ambulans.hastighet_per_djup",
            "fordon.tung_raddningsbil.gransdjup_m",
            "fordon.tung_raddningsbil.hastighet_per_djup",
            "entre_gransdjup_m",
            "gangavstand_max_m",
            "gangvag_djup_max_m",
            "gangvag_fara_max_m2_s",
            "insatstidsmal_min.sjukhus",
            "insatstidsmal_min.vardboende",
            "insatstidsmal_min.skola",
            "insatstidsmal_min.forskola",
        )
        for falt in forvantade_falt:
            self.assertIn(falt, feltext)

    def test_rutt_test_fran_config_nekas_skarpt_men_tillats_som_test(self) -> None:
        test_param = las_testparametrar_fran_konfig(CONFIG_TOML)
        self.assertTrue(test_param.ar_testvarde)

        with self.assertRaises(RuttParametrarFel) as ctx:
            validera_parametrar(test_param, skarp=True)
        self.assertIn("[rutt_test]", str(ctx.exception))

        # Uttryckligen tillåten testkörning
        validera_parametrar(test_param, skarp=False)
        validera_parametrar(test_param, tillat_testvarden=True)

    def test_k7_json_skriv_och_las_rundtur(self) -> None:
        test_param = las_testparametrar_fran_konfig(CONFIG_TOML)
        skarp_param = RuttParametrarK7(
            fordon=test_param.fordon,
            entre_gransdjup_m=test_param.entre_gransdjup_m,
            gangavstand_max_m=test_param.gangavstand_max_m,
            gangvag_djup_max_m=test_param.gangvag_djup_max_m,
            gangvag_fara_max_m2_s=test_param.gangvag_fara_max_m2_s,
            insatstidsmal_min=test_param.insatstidsmal_min,
            ar_testvarde=False,
        )
        validera_parametrar(skarp_param, skarp=True)

        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "ruttparametrar.json"
            skriv_ruttparametrar_k7(skarp_param, fil)
            raw = json.loads(fil.read_text(encoding="utf-8"))
            self.assertEqual(raw["kontrakt"], "K7/v1")
            inlast = las_ruttparametrar_k7(fil)
            self.assertEqual(inlast, skarp_param)

    def test_berakna_hastighetsfaktor_styckvis_linjar(self) -> None:
        ambulans = las_testparametrar_fran_konfig(CONFIG_TOML).fordon["ambulans"]
        self.assertAlmostEqual(berakna_hastighetsfaktor(ambulans, 0.0), 1.0)
        self.assertAlmostEqual(berakna_hastighetsfaktor(ambulans, 0.075), 0.8)
        self.assertAlmostEqual(berakna_hastighetsfaktor(ambulans, 0.15), 0.6)
        self.assertAlmostEqual(berakna_hastighetsfaktor(ambulans, 0.225), 0.3)
        self.assertAlmostEqual(berakna_hastighetsfaktor(ambulans, 0.30), 0.0)
        self.assertAlmostEqual(berakna_hastighetsfaktor(ambulans, 0.80), 0.0)


if __name__ == "__main__":
    unittest.main()
