"""Tester för inkrementell omräkning efter en ändrad yta."""

from __future__ import annotations

import unittest

import numpy as np

from buller.operator.granssnitt import Korridorindex, Rektangel
from buller.operator.inkrementell import rakna_om_inkrementellt
from buller.operator.syntetisk import (
    SyntetiskScen,
    skapa_syntetisk_scen,
    syntetisk_emission_db,
    syntetisk_vagning_db,
)
from buller.typer import BullerFel, Flyttal
from tests.buller.operator.test_klustring import klustrat_lager, oklustrat_lager
from tests.buller.operator.test_lager import RaknandeParberaknare


class TestInkrementellOmrakning(unittest.TestCase):
    scen_fore: SyntetiskScen
    scen_efter: SyntetiskScen
    andrad_yta: Rektangel
    lw_db: Flyttal

    @classmethod
    def setUpClass(cls) -> None:
        cls.scen_fore = skapa_syntetisk_scen(
            antal_mottagare=60, antal_kallor=900, antal_vaderklasser=2, sida_m=1000.0
        )
        gammal = cls.scen_fore.byggnad
        ny = Rektangel(
            gammal.e_min_m + 140.0, gammal.n_min_m + 90.0, gammal.e_max_m + 140.0,
            gammal.n_max_m + 90.0,
        )  # fmt: skip
        cls.scen_efter = cls.scen_fore.med_byggnad(ny)
        cls.andrad_yta = gammal.union(ny)
        cls.lw_db = syntetisk_emission_db(cls.scen_fore)

    def test_korridorindexet_uppfyller_protokollet(self) -> None:
        self.assertIsInstance(self.scen_efter.korridorindex(), Korridorindex)

    def test_flyttad_byggnad_inkrementellt_ar_lika_med_full_omrakning(self) -> None:
        """Acceptans (d)."""
        fore = oklustrat_lager(self.scen_fore)
        facit = oklustrat_lager(self.scen_efter)
        self.assertNotEqual(fore.identitet, facit.identitet, "flytten ska påverka överföringen")
        raknare = RaknandeParberaknare(self.scen_efter)
        nytt, statistik = rakna_om_inkrementellt(
            fore, self.andrad_yta, self.scen_efter.korridorindex(), raknare
        )
        np.testing.assert_array_equal(nytt.h_db, facit.h_db)
        np.testing.assert_array_equal(nytt.niva_db(self.lw_db), facit.niva_db(self.lw_db))
        self.assertEqual(nytt.identitet, facit.identitet)
        self.assertNotEqual(nytt.identitet, fore.identitet)

        totalt = self.scen_fore.antal_mottagare * self.scen_fore.antal_kallor
        self.assertEqual(statistik.antal_mojliga_par, totalt)
        self.assertEqual(statistik.antal_omraknade_par, raknare.antal_par)
        self.assertGreater(statistik.antal_omraknade_par, 0)
        self.assertLess(statistik.antal_omraknade_par, totalt)
        self.assertLess(statistik.andel_omraknade_par, 0.5)
        print(
            f"\n[B8] inkrementellt: {statistik.antal_omraknade_par} av {totalt} par omräknade "
            f"({statistik.andel_omraknade_par:.4f})"
        )

    def test_blockerande_byggnad_lagger_till_och_tar_bort_par(self) -> None:
        fore_scen = skapa_syntetisk_scen(
            antal_mottagare=40, antal_kallor=500, sida_m=700.0, byggnad_blockerar=True
        )
        gammal = fore_scen.byggnad
        ny = Rektangel(gammal.e_min_m, gammal.n_min_m + 120.0, gammal.e_max_m, gammal.n_max_m + 120)
        efter_scen = fore_scen.med_byggnad(ny)
        fore = oklustrat_lager(fore_scen)
        facit = oklustrat_lager(efter_scen)
        self.assertLess(fore.antal_par, 40 * 500, "några par ska sakna bana")
        nytt, statistik = rakna_om_inkrementellt(
            fore, gammal.union(ny), efter_scen.korridorindex(), efter_scen.parberaknare()
        )
        self.assertEqual(nytt.identitet, facit.identitet)
        self.assertEqual(statistik.antal_par_efter, facit.antal_par)
        self.assertFalse(
            np.array_equal(nytt.par_kalla_index, fore.par_kalla_index),
            "uppsättningen lagrade par ska ha ändrats",
        )

    def test_klustrat_lager_haller_felgransen_efter_omrakning(self) -> None:
        vagning = syntetisk_vagning_db()
        fore, _ = klustrat_lager(self.scen_fore, self.lw_db, 0.5)
        nytt, statistik = rakna_om_inkrementellt(
            fore,
            self.andrad_yta,
            self.scen_efter.korridorindex(),
            self.scen_efter.parberaknare(),
        )
        self.assertGreater(statistik.antal_hoppade_restpar, 0)
        self.assertEqual(
            statistik.antal_omraknade_par + statistik.antal_hoppade_restpar,
            statistik.antal_berorda_par,
        )
        facit = oklustrat_lager(self.scen_efter).niva_a_db(self.lw_db, vagning)
        fel = facit - nytt.niva_a_db(self.lw_db, vagning)
        grans = nytt.felgrans_db(self.lw_db, vagning)
        self.assertTrue(bool(np.all(fel >= -1e-9)))
        self.assertTrue(bool(np.all(fel <= grans + 1e-9)))
        # De beräknade paren är desamma som i ett lager byggt direkt på den nya scenen.
        full = oklustrat_lager(self.scen_efter)
        nyckel_full = full.par_mottagare_index * full.antal_kallor + full.par_kalla_index
        nyckel = nytt.par_mottagare_index * nytt.antal_kallor + nytt.par_kalla_index
        np.testing.assert_array_equal(nytt.h_db, full.h_db[np.searchsorted(nyckel_full, nyckel)])

    def test_yta_utan_berorda_par_lamnar_lagret_orort(self) -> None:
        fore = oklustrat_lager(self.scen_fore)
        raknare = RaknandeParberaknare(self.scen_fore)
        utanfor = Rektangel(-500.0, -500.0, -400.0, -400.0)
        nytt, statistik = rakna_om_inkrementellt(
            fore, utanfor, self.scen_fore.korridorindex(), raknare
        )
        self.assertIs(nytt, fore)
        self.assertEqual(statistik.antal_omraknade_par, 0)
        self.assertEqual(raknare.antal_anrop, 0)

    def test_okanda_id_fran_indexet_raknas_men_stoppar_inte(self) -> None:
        class IndexMedOkanda:
            def berorda_par(
                self, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
            ) -> set[tuple[int, int]]:
                return {(0, 1), (0, 99_999), (77_777, 2), (3, 5)}

        fore = oklustrat_lager(self.scen_fore)
        nytt, statistik = rakna_om_inkrementellt(
            fore, self.andrad_yta, IndexMedOkanda(), self.scen_fore.parberaknare()
        )
        self.assertEqual(statistik.antal_okanda_par, 2)
        self.assertEqual(statistik.antal_omraknade_par, 2)
        self.assertEqual(nytt.identitet, fore.identitet, "samma scen ger samma värden")

    def test_ogiltig_rektangel(self) -> None:
        with self.assertRaises(BullerFel):
            Rektangel(1.0, 0.0, 0.0, 1.0)


if __name__ == "__main__":
    unittest.main()
