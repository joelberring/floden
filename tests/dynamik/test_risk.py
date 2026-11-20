"""Enhetstester för deterministisk kumulativ översvämningsrisk (`dynamik.risk`)."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from dynamik.nodmodell import DynamikFel, EntrePunkt
from dynamik.risk import (
    AterkomstStegResultat,
    EntreKumulativRisk,
    KumulativRiskResultat,
    NodKumulativRisk,
    berakna_kumulativ_oversvamningsrisk,
)
from gemensamt.regn import IdfTabell
from sankgraf.fyllning import kor_statisk_k1_mapp
from sankgraf.typer import SankGraf

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_MAPP = REPO_ROT / "testsvit"


def _ladda_graf(mappnamn: str) -> SankGraf:
    graf, _ = kor_statisk_k1_mapp(TESTSVIT_MAPP / mappnamn / "indata", regn_m=0.05)
    return graf


def _cell_centrum_en(graf: SankGraf, rad: int, kol: int) -> tuple[float, float]:
    geo = graf.geo
    e_m = geo.x_min + (kol + 0.5) * geo.pixelstorlek_x
    n_m = geo.y_max - (rad + 0.5) * geo.pixelstorlek_y
    return e_m, n_m


class TestKumulativRisk(unittest.TestCase):
    """Validerar log-interpolation, sannolikhetsmatematik, monotonitet och felhantering."""

    graf_a1: SankGraf
    graf_a3: SankGraf

    @classmethod
    def setUpClass(cls) -> None:
        cls.graf_a1 = _ladda_graf("A1_enkel_sanka")
        cls.graf_a3 = _ladda_graf("A3_tva_sankor_sammanslagning")

    def test_a1_analytisk_log_interpolation_sannolikhet_ead_och_volymfel(self) -> None:
        """Verifierar T*, P_N = 1 - (1 - p_ar)^N, EAD och volymfel < 1e-6 på A1."""
        grans_m = 0.10
        horisont_ar = 50.0
        res = berakna_kumulativ_oversvamningsrisk(
            self.graf_a1,
            gransdjup_m=grans_m,
            max_aterkomsttid_ar=100.0,
            klimatfaktor=1.0,
            tidshorisont_ar=horisont_ar,
            regntyp="cds",
        )

        self.assertIsInstance(res, KumulativRiskResultat)
        self.assertEqual(res.aterkomsttider_ar, (2.0, 5.0, 10.0, 20.0, 50.0, 100.0))
        self.assertEqual(len(res.steg), 6)
        self.assertTrue(all(isinstance(s, AterkomstStegResultat) for s in res.steg))

        # Volymfel < 1e-6 i varje återkomststeg
        for steg in res.steg:
            self.assertLess(steg.dynamik.volymfel_rel, 1e-6)
        self.assertLess(res.max_volymfel_rel, 1e-6)

        # Välj djupaste cellen i sänkan och kontrollera varje formel analytiskt
        djup_vid_tmax = res.max_djup_vid_tmax_m
        self.assertGreater(float(np.max(djup_vid_tmax)), grans_m)
        rad_i, kol_i = np.unravel_index(int(np.argmax(djup_vid_tmax)), djup_vid_tmax.shape)
        rad, kol = int(rad_i), int(kol_i)

        t_lista = list(res.aterkomsttider_ar)
        h_lista = [float(s.dynamik.max_djup_m[rad, kol]) for s in res.steg]

        # Manuell log-interpolation av T*
        self.assertLess(h_lista[0], grans_m)
        self.assertGreaterEqual(h_lista[-1], grans_m)
        forv_t_krit = math.inf
        for idx in range(1, len(t_lista)):
            if h_lista[idx] >= grans_m:
                alpha = (grans_m - h_lista[idx - 1]) / (h_lista[idx] - h_lista[idx - 1])
                forv_t_krit = math.exp(
                    math.log(t_lista[idx - 1])
                    + alpha * (math.log(t_lista[idx]) - math.log(t_lista[idx - 1]))
                )
                break

        forv_p_ar = 1.0 / forv_t_krit
        forv_p_kum = 1.0 - math.pow(1.0 - forv_p_ar, horisont_ar)

        self.assertFalse(bool(res.overskriden_vid_min_t[rad, kol]))
        self.assertAlmostEqual(
            float(res.kritisk_aterkomsttid_ar[rad, kol]),
            forv_t_krit,
            places=4,
        )
        self.assertAlmostEqual(
            float(res.arlig_overskridande_sannolikhet[rad, kol]),
            forv_p_ar,
            places=6,
        )
        self.assertAlmostEqual(
            float(res.kumulativ_sannolikhet[rad, kol]),
            forv_p_kum,
            places=6,
        )

        # Verifiera P_N = 1 - (1 - p_ar)^N över hela rutnätet
        p_ar_arr = np.asarray(res.arlig_overskridande_sannolikhet, dtype=np.float64)
        p_kum_arr = np.asarray(res.kumulativ_sannolikhet, dtype=np.float64)
        forv_p_kum_arr = 1.0 - np.power(1.0 - p_ar_arr, horisont_ar)
        np.testing.assert_allclose(p_kum_arr, forv_p_kum_arr, atol=1e-6, rtol=1e-6)

        # Verifiera trapetsintegral för förväntat årligt djup och överdjup
        p_stig = [1.0 / t for t in reversed(t_lista)]
        h_stig = list(reversed(h_lista))
        forv_ead = sum(
            0.5 * (h_stig[i] + h_stig[i + 1]) * (p_stig[i + 1] - p_stig[i])
            for i in range(len(p_stig) - 1)
        )
        o_stig = [max(0.0, h - grans_m) for h in h_stig]
        forv_ead_over = sum(
            0.5 * (o_stig[i] + o_stig[i + 1]) * (p_stig[i + 1] - p_stig[i])
            for i in range(len(p_stig) - 1)
        )
        self.assertAlmostEqual(float(res.forvantat_arligt_djup_m[rad, kol]), forv_ead, places=6)
        self.assertAlmostEqual(
            float(res.forvantat_arligt_overdjup_m[rad, kol]),
            forv_ead_over,
            places=6,
        )
        self.assertGreater(forv_ead, forv_ead_over)

        # Nodrisk matchar djupaste cellen i A1:s enda lövnod
        self.assertEqual(len(res.noder), len(self.graf_a1.noder))
        nod_risk = next(iter(res.noder.values()))
        self.assertIsInstance(nod_risk, NodKumulativRisk)
        self.assertAlmostEqual(nod_risk.kritisk_aterkomsttid_ar, forv_t_krit, places=5)
        self.assertAlmostEqual(nod_risk.arlig_overskridande_sannolikhet, forv_p_ar, places=6)
        self.assertAlmostEqual(nod_risk.kumulativ_sannolikhet, forv_p_kum, places=6)

    def test_a3_sammanslagning_noder_och_volymfel(self) -> None:
        """Verifierar riskberäkning på A3 (två sänkor + föräldernod) med volymfel < 1e-6."""
        res = berakna_kumulativ_oversvamningsrisk(
            self.graf_a3,
            gransdjup_m=0.15,
            max_aterkomsttid_ar=200.0,
            klimatfaktor=1.24,
            tidshorisont_ar=30.0,
            regntyp="cds",
        )

        self.assertEqual(res.aterkomsttider_ar, (2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0))
        self.assertLess(res.max_volymfel_rel, 1e-6)
        self.assertEqual(set(res.noder.keys()), set(self.graf_a3.noder.keys()))
        self.assertGreater(res.area_over_grans_m2, 0.0)
        self.assertGreater(res.andel_over_grans, 0.0)
        self.assertLessEqual(res.andel_over_grans, 1.0)

        # Alla noder har konsistent P_N = 1 - (1 - p_ar)^N
        for nr in res.noder.values():
            forv_pk = 1.0 - math.pow(1.0 - nr.arlig_overskridande_sannolikhet, 30.0)
            self.assertAlmostEqual(nr.kumulativ_sannolikhet, forv_pk, places=12)

        # Föräldernoden (om den finns) ska ha minst lika högt maxdjup som sina barn
        for nid, nod in self.graf_a3.noder.items():
            if nod.barn_id:
                barn_max = max(res.noder[b].max_djup_vid_tmax_m for b in nod.barn_id)
                self.assertAlmostEqual(res.noder[nid].max_djup_vid_tmax_m, barn_max, places=12)

    def test_klimatfaktor_monotonitet(self) -> None:
        """Klimatfaktor 1.4 ger >= kumulativ sannolikhet och >= area över gräns än 1.24 >= 1.0."""
        res_100 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a3,
            gransdjup_m=0.15,
            max_aterkomsttid_ar=100.0,
            klimatfaktor=1.0,
            tidshorisont_ar=50.0,
        )
        res_124 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a3,
            gransdjup_m=0.15,
            max_aterkomsttid_ar=100.0,
            klimatfaktor=1.24,
            tidshorisont_ar=50.0,
        )
        res_140 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a3,
            gransdjup_m=0.15,
            max_aterkomsttid_ar=100.0,
            klimatfaktor=1.4,
            tidshorisont_ar=50.0,
        )

        self.assertGreaterEqual(res_140.area_over_grans_m2, res_124.area_over_grans_m2)
        self.assertGreaterEqual(res_124.area_over_grans_m2, res_100.area_over_grans_m2)
        self.assertGreater(res_140.max_kumulativ_sannolikhet, res_124.max_kumulativ_sannolikhet)
        self.assertGreater(res_124.max_kumulativ_sannolikhet, res_100.max_kumulativ_sannolikhet)

        # Cellvis monotonitet i kumulativ sannolikhet och maxdjup
        self.assertTrue(
            bool(np.all(res_140.kumulativ_sannolikhet >= res_124.kumulativ_sannolikhet - 1e-6))
        )
        self.assertTrue(
            bool(np.all(res_124.kumulativ_sannolikhet >= res_100.kumulativ_sannolikhet - 1e-6))
        )
        self.assertTrue(
            bool(np.all(res_140.max_djup_vid_tmax_m >= res_124.max_djup_vid_tmax_m - 1e-6))
        )

    def test_troskel_monotonitet(self) -> None:
        """Högre gransdjup_m (0.30 m vs 0.10 m) ger <= kumulativ sannolikhet och <= area."""
        res_010 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a3,
            gransdjup_m=0.10,
            max_aterkomsttid_ar=500.0,
            klimatfaktor=1.25,
            tidshorisont_ar=50.0,
        )
        res_030 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a3,
            gransdjup_m=0.30,
            max_aterkomsttid_ar=500.0,
            klimatfaktor=1.25,
            tidshorisont_ar=50.0,
        )

        self.assertLessEqual(res_030.area_over_grans_m2, res_010.area_over_grans_m2)
        self.assertLess(res_030.max_kumulativ_sannolikhet, res_010.max_kumulativ_sannolikhet)
        self.assertTrue(
            bool(np.all(res_030.kumulativ_sannolikhet <= res_010.kumulativ_sannolikhet + 1e-6))
        )
        self.assertTrue(
            bool(np.all(res_030.kritisk_aterkomsttid_ar >= res_010.kritisk_aterkomsttid_ar - 1e-4))
        )
        # EAD av djup beror inte på tröskeln, medan överdjup minskar med högre tröskel
        np.testing.assert_allclose(
            res_030.forvantat_arligt_djup_m,
            res_010.forvantat_arligt_djup_m,
            atol=1e-6,
            rtol=1e-6,
        )
        self.assertTrue(
            bool(
                np.all(
                    res_030.forvantat_arligt_overdjup_m
                    <= res_010.forvantat_arligt_overdjup_m + 1e-6
                )
            )
        )

    def test_max_aterkomsttid_500_vs_100_och_avklippning_over_100_ar(self) -> None:
        """Verifierar 500 vs 100 år samt att cell som översvämmas > 100 år har p=0 vid T_max=100."""
        # På A1 med kf=1.0: maxdjup vid T=100 är ~0.18 m och vid T=500 ~0.31 m.
        # Tröskeln 0.24 m nås alltså först mellan 100 och 500 år.
        grans_m = 0.24
        res_100 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a1,
            gransdjup_m=grans_m,
            max_aterkomsttid_ar=100.0,
            klimatfaktor=1.0,
            tidshorisont_ar=100.0,
        )
        res_500 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a1,
            gransdjup_m=grans_m,
            max_aterkomsttid_ar=500.0,
            klimatfaktor=1.0,
            tidshorisont_ar=100.0,
        )

        # Vid max_aterkomsttid_ar=100 överskrids inte 0.24 m någonstans -> sannolikhet 0.0
        self.assertLess(float(np.max(res_100.max_djup_vid_tmax_m)), grans_m)
        self.assertEqual(res_100.area_over_grans_m2, 0.0)
        self.assertEqual(res_100.max_arlig_sannolikhet, 0.0)
        self.assertEqual(res_100.max_kumulativ_sannolikhet, 0.0)
        self.assertTrue(math.isinf(res_100.min_kritisk_aterkomsttid_ar))
        self.assertTrue(bool(np.all(res_100.arlig_overskridande_sannolikhet == 0.0)))
        self.assertTrue(bool(np.all(res_100.kumulativ_sannolikhet == 0.0)))

        # Vid max_aterkomsttid_ar=500 fångas extremdjupet och överskridandet mellan 100 och 500 år
        self.assertGreater(
            float(np.max(res_500.max_djup_vid_tmax_m)),
            float(np.max(res_100.max_djup_vid_tmax_m)),
        )
        self.assertGreater(res_500.area_over_grans_m2, 0.0)
        self.assertGreater(res_500.min_kritisk_aterkomsttid_ar, 100.0)
        self.assertLessEqual(res_500.min_kritisk_aterkomsttid_ar, 500.0)
        self.assertGreater(res_500.max_kumulativ_sannolikhet, 0.0)

    def test_aterkomsttidsstege_anpassad_enpunkts_och_blockregn_idf(self) -> None:
        """Verifierar anpassad stege, icke-standard max_T, enpunktsstege, blockregn och IDF."""
        # Icke-standard max_T = 75 år ska lägga till 75.0 i standardstegen
        res_75 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a1,
            gransdjup_m=0.10,
            max_aterkomsttid_ar=75.0,
            regntyp="block",
            efterforlopp_s=600.0,
        )
        self.assertEqual(res_75.aterkomsttider_ar, (2.0, 5.0, 10.0, 20.0, 50.0, 75.0))
        self.assertEqual(res_75.regntyp, "block")
        self.assertLess(res_75.max_volymfel_rel, 1e-6)

        # max_T < min(STANDARD_ATERKOMSTTIDER_AR) -> enpunktsstege, EAD == 0.0
        res_1_5 = berakna_kumulativ_oversvamningsrisk(
            self.graf_a1,
            gransdjup_m=0.05,
            max_aterkomsttid_ar=1.5,
            regntyp="block",
        )
        self.assertEqual(res_1_5.aterkomsttider_ar, (1.5,))
        self.assertTrue(bool(np.all(res_1_5.forvantat_arligt_djup_m == 0.0)))
        self.assertTrue(bool(np.all(res_1_5.forvantat_arligt_overdjup_m == 0.0)))

        # Explicit aterkomsttider_ar med dubbletter och värde > max_T filtreras och sorteras
        idf = IdfTabell(
            namn="Test-IDF",
            kalla="Syntetisk testtabell",
            varaktigheter_min=(60.0, 360.0),
            aterkomsttider_ar=(2.0, 10.0, 100.0),
            intensiteter_mm_h=(
                (15.0, 30.0, 60.0),
                (5.0, 10.0, 20.0),
            ),
        )
        res_idf = berakna_kumulativ_oversvamningsrisk(
            self.graf_a1,
            gransdjup_m=0.10,
            max_aterkomsttid_ar=100.0,
            aterkomsttider_ar=(100.0, 2.0, 10.0, 10.0, 250.0),
            varaktighet_min=360.0,
            regntyp="block",
            idf_tabell=idf,
        )
        self.assertEqual(res_idf.aterkomsttider_ar, (2.0, 10.0, 100.0))
        self.assertEqual(res_idf.statistikkalla, "Syntetisk testtabell")

    def test_entreer_overskriden_vid_min_t_manifest_och_sammanfattning(self) -> None:
        """Verifierar EntreKumulativRisk, K0/v1-manifest och JSON-säker sammanfattning."""
        # Hitta en cell i A1:s sänkbotten och en torr kantcell
        z = np.asarray(self.graf_a1.mark_z, dtype=np.float64)
        botten_rad, botten_kol = np.unravel_index(int(np.argmin(z)), z.shape)
        kant_rad, kant_kol = 0, 0
        e_bot, n_bot = _cell_centrum_en(self.graf_a1, int(botten_rad), int(botten_kol))
        e_kant, n_kant = _cell_centrum_en(self.graf_a1, kant_rad, kant_kol)

        entreer = [
            EntrePunkt(entre_id="E_lag", e_m=e_bot, n_m=n_bot, gransdjup_m=0.02),
            EntrePunkt(entre_id="E_normal", e_m=e_bot, n_m=n_bot, gransdjup_m=0.10),
            EntrePunkt(entre_id="E_hog", e_m=e_bot, n_m=n_bot, gransdjup_m=0.45),
            EntrePunkt(entre_id="E_torr", e_m=e_kant, n_m=n_kant, gransdjup_m=0.10),
        ]

        res = berakna_kumulativ_oversvamningsrisk(
            self.graf_a1,
            gransdjup_m=0.10,
            max_aterkomsttid_ar=100.0,
            klimatfaktor=1.0,
            tidshorisont_ar=50.0,
            entreer=entreer,
        )

        self.assertEqual(len(res.entreer), 4)
        self.assertTrue(all(isinstance(e, EntreKumulativRisk) for e in res.entreer))
        by_id = {e.entre_id: e for e in res.entreer}

        # E_lag (0.02 m) överskrids redan vid T_1 = 2 år
        self.assertTrue(by_id["E_lag"].overskriden_vid_min_t)
        self.assertEqual(by_id["E_lag"].kritisk_aterkomsttid_ar, 2.0)
        self.assertAlmostEqual(by_id["E_lag"].arlig_overskridande_sannolikhet, 0.5, places=12)

        # E_normal (0.10 m) matchar rastercellen exakt
        self.assertFalse(by_id["E_normal"].overskriden_vid_min_t)
        self.assertAlmostEqual(
            by_id["E_normal"].kritisk_aterkomsttid_ar,
            float(res.kritisk_aterkomsttid_ar[int(botten_rad), int(botten_kol)]),
            places=4,
        )
        self.assertAlmostEqual(
            by_id["E_normal"].kumulativ_sannolikhet,
            float(res.kumulativ_sannolikhet[int(botten_rad), int(botten_kol)]),
            places=6,
        )

        # E_hog (0.45 m) och E_torr nås aldrig upp till T=100
        for eid in ("E_hog", "E_torr"):
            self.assertTrue(math.isinf(by_id[eid].kritisk_aterkomsttid_ar))
            self.assertEqual(by_id[eid].arlig_overskridande_sannolikhet, 0.0)
            self.assertEqual(by_id[eid].kumulativ_sannolikhet, 0.0)

        # K0/v1-manifest
        self.assertEqual(res.manifest.kontrakt, "K0/v1")
        res.manifest.validera()
        self.assertEqual(res.manifest.parametrar["gransdjup_m"], 0.10)
        self.assertEqual(res.manifest.parametrar["max_aterkomsttid_ar"], 100.0)

        # Strikt JSON-serialisering utan NaN/Infinity
        sammanf = res.sammanfattning_dict()
        json_text = json.dumps(sammanf, allow_nan=False)
        aterlast = json.loads(json_text)
        self.assertEqual(aterlast["manifest"]["kontrakt"], "K0/v1")
        self.assertEqual(len(aterlast["entreer"]), 4)
        self.assertIsNone(aterlast["entreer"][2]["kritisk_aterkomsttid_ar"])

    def test_felhantering_ogiltiga_parametrar(self) -> None:
        """Ogiltiga parametrar ska kasta DynamikFel utan tysta fel."""
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, gransdjup_m=0.0)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, gransdjup_m=-0.1)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, gransdjup_m=math.nan)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, max_aterkomsttid_ar=0.0)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, max_aterkomsttid_ar=-10.0)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, tidshorisont_ar=0.0)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, tidshorisont_ar=-5.0)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, klimatfaktor=0.0)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, regntyp="okand")
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(self.graf_a1, toppfaktor=1.0)
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(
                self.graf_a1,
                max_aterkomsttid_ar=10.0,
                aterkomsttider_ar=(20.0, 50.0),
            )
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(
                self.graf_a1,
                max_aterkomsttid_ar=100.0,
                aterkomsttider_ar=(-2.0, 10.0),
            )
        with self.assertRaises(DynamikFel):
            berakna_kumulativ_oversvamningsrisk(
                self.graf_a1,
                entreer=[EntrePunkt(entre_id="utanfor", e_m=0.0, n_m=0.0, gransdjup_m=0.10)],
            )
        with self.assertRaises(DynamikFel):
            e_bot, n_bot = _cell_centrum_en(self.graf_a1, 0, 0)
            berakna_kumulativ_oversvamningsrisk(
                self.graf_a1,
                entreer=[EntrePunkt(entre_id="ogiltig", e_m=e_bot, n_m=n_bot, gransdjup_m=0.0)],
            )


if __name__ == "__main__":
    unittest.main()
