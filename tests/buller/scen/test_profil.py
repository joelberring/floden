"""Tester för profilextraktionen (B3): referensjämförelse, kända svar, fel och determinism."""

from __future__ import annotations

import unittest

import numpy as np

from buller.scen.bullerscen import Bullerscen
from buller.scen.markklass import standardtabell
from buller.scen.profil import (
    Profilbatch,
    extrahera_profil,
    profiler_batch,
    profiler_langs_polylinjer,
)
from buller.scen.syntetisk import syntetisk_scen
from buller.typer import BullerFel, Byggnad, Flyttal
from tests.buller.scen.referens import fria_punkter, referensprofil, ythojd_punktvis_m

ANTAL_SNITT = 10_000
ANTAL_SNITT_PUNKTLISTA = 1_500
TOLERANS_HOJD_M = 0.01
STEG_M = 1.0
DX_M = 0.01


def _plan_scen(
    byggnader: tuple[Byggnad, ...] = (), markslag_kod: int = 4, lutning: float = 0.0
) -> Bullerscen:
    """100 × 100 m, 1 m celler, ursprung (0, 100); höjd = 10 + lutning · E i cellmitt."""
    e_mitt = np.arange(100) + 0.5
    terrang = np.tile(10.0 + lutning * e_mitt, (100, 1))
    return Bullerscen(
        terrang_rh2000_m=terrang,
        markslag=np.full((100, 100), markslag_kod, dtype=np.uint8),
        e_min_m=0.0,
        n_max_m=100.0,
        cell_m=1.0,
        byggnader=byggnader,
    )


def _hus(namn: str, e0: float, n0: float, e1: float, n1: float, tak: float) -> Byggnad:
    return Byggnad(namn, np.array([e0, e1, e1, e0]), np.array([n0, n0, n1, n1]), tak)


class TestProfilMotReferens(unittest.TestCase):
    """Acceptans (a): 10 000 slumpade snitt mot oberoende referens, högst 1 cm i höjd."""

    scen: Bullerscen
    se: Flyttal
    sn: Flyttal
    te: Flyttal
    tn: Flyttal
    batch: Profilbatch

    @classmethod
    def setUpClass(cls) -> None:
        cls.scen = syntetisk_scen(sida_m=300.0, cell_m=0.5, tomt_m=50.0, fro=11)
        slump = np.random.default_rng(2026)
        cls.se, cls.sn = fria_punkter(cls.scen, slump, ANTAL_SNITT)
        cls.te, cls.tn = fria_punkter(cls.scen, slump, ANTAL_SNITT)
        cls.batch = profiler_batch(
            cls.scen, cls.se, cls.sn, 0.5, cls.te, cls.tn, 2.0, steg_m=STEG_M, skarm_dx_m=DX_M
        )

    def test_scenen_har_lutning_och_flera_byggnader(self) -> None:
        self.assertGreater(len(self.scen.byggnader), 20)
        z = self.scen.terrang_rh2000_m
        self.assertGreater(float(z.max() - z.min()), 5.0)

    def test_punktvis_mot_analytisk_yta_pa_10000_snitt(self) -> None:
        """Profilens höjd i täta stationer mot punktvis utvärderad yta (tak eller mark)."""
        slump = np.random.default_rng(7)
        b = self.batch
        self.assertEqual(b.antal, ANTAL_SNITT)
        e_delar, n_delar, z_delar = [], [], []
        antal_korsande = 0
        for i in range(b.antal):
            x = b.x_m[b.start[i] : b.start[i + 1]]
            z = b.z_m[b.start[i] : b.start[i + 1]]
            langd = float(x[-1])
            stationer = np.arange(float(slump.uniform(0.0, 0.5)), langd, 0.5)
            # Stationer mitt i en vägg (segment om högst 1,5 · dx) har ingen entydig höjd.
            seg = np.clip(np.searchsorted(x, stationer, side="right") - 1, 0, x.shape[0] - 2)
            stationer = stationer[(x[seg + 1] - x[seg]) > 1.5 * DX_M]
            antal_korsande += int(np.any(np.diff(x) < 1.5 * DX_M))
            z_delar.append(np.interp(stationer, x, z))
            e_delar.append(self.se[i] + (self.te[i] - self.se[i]) * stationer / langd)
            n_delar.append(self.sn[i] + (self.tn[i] - self.sn[i]) * stationer / langd)
        z_profil = np.concatenate(z_delar)
        z_referens = ythojd_punktvis_m(self.scen, np.concatenate(e_delar), np.concatenate(n_delar))
        storsta_fel_m = float(np.max(np.abs(z_profil - z_referens)))
        print(
            f"\n[B3 a] {b.antal} snitt, {z_profil.shape[0]} stationer, {antal_korsande} snitt "
            f"genom byggnad, största höjdfel {storsta_fel_m * 1000:.3f} mm"
        )
        self.assertGreater(antal_korsande, ANTAL_SNITT // 4)
        self.assertLessEqual(storsta_fel_m, TOLERANS_HOJD_M)

    def test_punktlista_mot_skalar_referens(self) -> None:
        """Samma punkter (antal, x och z) som den skalära referensen på en delmängd."""
        storsta_dz = 0.0
        storsta_dx = 0.0
        for i in range(ANTAL_SNITT_PUNKTLISTA):
            x_ref, z_ref = referensprofil(
                self.scen,
                float(self.se[i]),
                float(self.sn[i]),
                float(self.te[i]),
                float(self.tn[i]),
                STEG_M,
                DX_M,
            )
            profil = self.batch.profil(i)
            self.assertEqual(profil.x_m.shape[0], len(x_ref), f"snitt {i}")
            storsta_dx = max(storsta_dx, float(np.max(np.abs(profil.x_m - np.array(x_ref)))))
            storsta_dz = max(storsta_dz, float(np.max(np.abs(profil.z_m - np.array(z_ref)))))
        print(
            f"\n[B3 a] punktlista på {ANTAL_SNITT_PUNKTLISTA} snitt: största avvikelse "
            f"x {storsta_dx * 1000:.6f} mm, z {storsta_dz * 1000:.6f} mm"
        )
        self.assertLessEqual(storsta_dx, 1e-6)
        self.assertLessEqual(storsta_dz, TOLERANS_HOJD_M)

    def test_enstaka_snitt_lika_med_batch(self) -> None:
        for i in (0, 17, 4711):
            ensam = extrahera_profil(
                self.scen,
                float(self.se[i]),
                float(self.sn[i]),
                0.5,
                float(self.te[i]),
                float(self.tn[i]),
                2.0,
                steg_m=STEG_M,
                skarm_dx_m=DX_M,
            )
            i_batch = self.batch.profil(i)
            np.testing.assert_array_equal(ensam.x_m, i_batch.x_m)
            np.testing.assert_array_equal(ensam.z_m, i_batch.z_m)
            np.testing.assert_array_equal(
                ensam.flodesresistivitet_kNsm4, i_batch.flodesresistivitet_kNsm4
            )

    def test_determinism(self) -> None:
        igen = profiler_batch(
            self.scen, self.se, self.sn, 0.5, self.te, self.tn, 2.0, steg_m=STEG_M, skarm_dx_m=DX_M
        )
        for namn in ("x_m", "z_m", "flodesresistivitet_kNsm4", "rahet_m", "start"):
            np.testing.assert_array_equal(getattr(igen, namn), getattr(self.batch, namn))


class TestProfilKandaSvar(unittest.TestCase):
    """Små fall där svaret går att räkna för hand."""

    def test_lutande_plan_utan_byggnader(self) -> None:
        scen = _plan_scen(lutning=0.1)
        profil = extrahera_profil(scen, 10.5, 50.0, 0.5, 40.5, 50.0, 4.0, steg_m=2.0)
        np.testing.assert_allclose(profil.x_m, np.arange(0.0, 30.1, 2.0), atol=1e-12)
        # Höjden i cellmitt är 10 + 0,1 · E, och bilinjär interpolation återger planet exakt.
        np.testing.assert_allclose(profil.z_m, 10.0 + 0.1 * (10.5 + profil.x_m), atol=1e-12)
        self.assertEqual(profil.kalla_hojd_m, 0.5)
        self.assertEqual(profil.mottagare_hojd_m, 4.0)
        gras = standardtabell().klass(4)
        np.testing.assert_array_equal(
            profil.flodesresistivitet_kNsm4, np.full(15, gras.flodesresistivitet_kNsm4)
        )

    def test_byggnad_blir_skarm_med_exakta_vaggar(self) -> None:
        scen = _plan_scen((_hus("a", 30.25, 40.0, 42.75, 60.0, 22.0),))
        profil = extrahera_profil(
            scen, 10.0, 50.0, 0.5, 60.0, 50.0, 2.0, steg_m=5.0, skarm_dx_m=0.01
        )
        vantat_x = [0, 5, 10, 15, 20, 20.25, 20.26, 32.74, 32.75, 35, 40, 45, 50]
        vantat_z = [10, 10, 10, 10, 10, 10, 22, 22, 10, 10, 10, 10, 10]
        np.testing.assert_allclose(profil.x_m, vantat_x, atol=1e-9)
        np.testing.assert_allclose(profil.z_m, vantat_z, atol=1e-12)
        tabell = standardtabell()
        hard = tabell.byggnadsyta.flodesresistivitet_kNsm4
        gras = tabell.klass(4).flodesresistivitet_kNsm4
        np.testing.assert_array_equal(
            profil.flodesresistivitet_kNsm4, [gras] * 5 + [hard] * 3 + [gras] * 4
        )
        self.assertTrue(bool(np.all(np.diff(profil.x_m) > 0.0)))

    def test_sammanbyggda_hus_fogas_utan_markpunkt(self) -> None:
        scen = _plan_scen(
            (_hus("a", 30.0, 40.0, 40.0, 60.0, 20.0), _hus("b", 40.0, 40.0, 50.0, 60.0, 25.0))
        )
        profil = extrahera_profil(scen, 10.0, 50.0, 0.5, 70.0, 50.0, 2.0, steg_m=10.0)
        vantat_x = [0, 10, 20, 20.01, 29.99, 30, 39.99, 40, 50, 60]
        vantat_z = [10, 10, 10, 20, 20, 25, 25, 10, 10, 10]
        np.testing.assert_allclose(profil.x_m, vantat_x, atol=1e-9)
        np.testing.assert_allclose(profil.z_m, vantat_z, atol=1e-12)

    def test_vinkelhus_ger_tva_skarmar(self) -> None:
        vinkel = Byggnad(
            "L",
            np.array([30.0, 60.0, 60.0, 50.0, 50.0, 40.0, 40.0, 30.0]),
            np.array([30.0, 30.0, 70.0, 70.0, 40.0, 40.0, 70.0, 70.0]),
            18.0,
        )
        profil = extrahera_profil(
            _plan_scen((vinkel,)), 10.0, 60.0, 0.5, 80.0, 60.0, 2.0, steg_m=100.0
        )
        vantat_x = [0, 20, 20.01, 29.99, 30, 40, 40.01, 49.99, 50, 70]
        vantat_z = [10, 10, 18, 18, 10, 10, 18, 18, 10, 10]
        np.testing.assert_allclose(profil.x_m, vantat_x, atol=1e-9)
        np.testing.assert_allclose(profil.z_m, vantat_z, atol=1e-12)

    def test_markslag_per_segment(self) -> None:
        scen = _plan_scen()
        scen.markslag[:, :50] = 1  # hårdgjort väster om E = 50
        profil = extrahera_profil(scen, 40.0, 50.0, 0.5, 60.0, 50.0, 2.0, steg_m=1.0)
        tabell = standardtabell()
        vantat = [tabell.klass(1).flodesresistivitet_kNsm4] * 10 + [
            tabell.klass(4).flodesresistivitet_kNsm4
        ] * 10
        np.testing.assert_array_equal(profil.flodesresistivitet_kNsm4, vantat)
        np.testing.assert_array_equal(profil.rahet_m, np.zeros(20))

    def test_polylinje_med_knack_har_lopande_langd(self) -> None:
        scen = _plan_scen(lutning=0.1)
        batch = profiler_langs_polylinjer(
            scen,
            np.array([[10.5, 40.5, 40.5]]),
            np.array([[50.0, 50.0, 90.0]]),
            0.5,
            2.0,
            steg_m=10.0,
        )
        profil = batch.profil(0)
        np.testing.assert_allclose(profil.x_m, [0, 10, 20, 30, 40, 50, 60, 70], atol=1e-12)
        np.testing.assert_allclose(
            profil.z_m, [11.05, 12.05, 13.05, 14.05, 14.05, 14.05, 14.05, 14.05], atol=1e-12
        )


class TestProfilFel(unittest.TestCase):
    """Acceptans (d): fel i indata ger `BullerFel`."""

    def setUp(self) -> None:
        self.scen = _plan_scen((_hus("a", 30.0, 40.0, 40.0, 60.0, 20.0),))

    def test_kalla_i_byggnad(self) -> None:
        with self.assertRaisesRegex(BullerFel, "Källan"):
            extrahera_profil(self.scen, 35.0, 50.0, 0.5, 80.0, 50.0, 2.0)

    def test_mottagare_i_byggnad(self) -> None:
        with self.assertRaisesRegex(BullerFel, "Mottagaren"):
            extrahera_profil(self.scen, 10.0, 50.0, 0.5, 35.0, 50.0, 2.0)

    def test_bada_i_samma_byggnad(self) -> None:
        with self.assertRaises(BullerFel):
            extrahera_profil(self.scen, 32.0, 50.0, 0.5, 38.0, 50.0, 2.0)

    def test_mottagare_pa_bortre_fasaden(self) -> None:
        with self.assertRaisesRegex(BullerFel, "Mottagaren"):
            extrahera_profil(self.scen, 35.0, 20.0, 0.5, 35.0, 60.0, 2.0)

    def test_mottagare_pa_hitre_fasaden_ar_tillaten(self) -> None:
        # Snittet nuddar bara väggen utifrån (som en reflexpunkt): ingen skärm, inget fel.
        profil = extrahera_profil(self.scen, 35.0, 80.0, 0.5, 35.0, 60.0, 2.0, steg_m=5.0)
        np.testing.assert_allclose(profil.z_m, np.full(5, 10.0), atol=1e-12)

    def test_utanfor_raster(self) -> None:
        with self.assertRaisesRegex(BullerFel, "utanför"):
            extrahera_profil(self.scen, 10.0, 50.0, 0.5, 120.0, 50.0, 2.0)

    def test_samma_punkt(self) -> None:
        with self.assertRaises(BullerFel):
            extrahera_profil(self.scen, 10.0, 50.0, 0.5, 10.0, 50.0, 2.0)

    def test_ogiltiga_parametrar(self) -> None:
        for steg_m, skarm_dx_m in ((0.0, 0.01), (1.0, -1.0), (float("nan"), 0.01)):
            with self.assertRaises(BullerFel):
                extrahera_profil(
                    self.scen,
                    10.0,
                    50.0,
                    0.5,
                    20.0,
                    50.0,
                    2.0,
                    steg_m=steg_m,
                    skarm_dx_m=skarm_dx_m,
                )
        with self.assertRaises(BullerFel):
            extrahera_profil(self.scen, 10.0, 50.0, -0.5, 20.0, 50.0, 2.0)
        with self.assertRaises(BullerFel):
            extrahera_profil(self.scen, float("nan"), 50.0, 0.5, 20.0, 50.0, 2.0)
        with self.assertRaises(BullerFel):
            profiler_batch(self.scen, np.zeros(2), np.zeros(2), 0.5, np.zeros(3), np.zeros(3), 2.0)

    def test_okant_markslag_flaggas_inte_gissas(self) -> None:
        scen = _plan_scen(markslag_kod=0)
        with self.assertRaisesRegex(BullerFel, "utan impedansklass"):
            extrahera_profil(scen, 10.0, 50.0, 0.5, 20.0, 50.0, 2.0)
        batch = profiler_batch(
            scen, np.array([10.0]), np.array([50.0]), 0.5, np.array([20.0]), np.array([50.0]), 2.0,
            okand_klass="G",
        )  # fmt: skip
        self.assertTrue(bool(batch.okant_markslag[0]))
        tabell = standardtabell()
        np.testing.assert_array_equal(
            batch.profil(0).flodesresistivitet_kNsm4,
            np.full(10, tabell.impedansklasser_kNsm4["G"]),
        )
        with self.assertRaisesRegex(BullerFel, "Impedansklass"):
            extrahera_profil(scen, 10.0, 50.0, 0.5, 20.0, 50.0, 2.0, okand_klass="Q")

    def test_kant_markslag_flaggas_inte(self) -> None:
        batch = profiler_batch(
            self.scen,
            np.array([10.0]),
            np.array([50.0]),
            0.5,
            np.array([20.0]),
            np.array([50.0]),
            2.0,
        )
        self.assertFalse(bool(batch.okant_markslag[0]))

    def test_tom_batch(self) -> None:
        batch = profiler_batch(
            self.scen, np.zeros(0), np.zeros(0), 0.5, np.zeros(0), np.zeros(0), 2.0
        )
        self.assertEqual(batch.antal, 0)
        with self.assertRaises(BullerFel):
            batch.profil(0)


if __name__ == "__main__":
    unittest.main()
