"""Tester för överföringslagret: omkombination, identitet och filformat."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from buller.operator.granssnitt import Parberaknare
from buller.operator.klustring import bygg_fullt_lager
from buller.operator.lager import (
    Overforingslager,
    a_vagd_niva_db,
    las_overforingslager,
)
from buller.operator.syntetisk import (
    SyntetiskScen,
    skapa_syntetisk_scen,
    syntetisk_emission_db,
    syntetisk_vagning_db,
)
from buller.typer import ANTAL_BAND, BullerFel, Flyttal
from gemensamt.hash import sha256_fil


class RaknandeParberaknare:
    """Räknar anrop och par, för att visa när geometrin faktiskt beräknas."""

    def __init__(self, scen: SyntetiskScen) -> None:
        self.scen = scen
        self.antal_anrop = 0
        self.antal_par = 0

    def __call__(self, mottagare_index: int, kalla_index: Sequence[int]) -> Flyttal:
        self.antal_anrop += 1
        self.antal_par += len(kalla_index)
        return self.scen.overforing_db(mottagare_index, kalla_index)


def fullt_lager(
    scen: SyntetiskScen, parberaknare: Parberaknare | None = None
) -> Overforingslager:
    lager, _ = bygg_fullt_lager(
        mottagare_id=scen.mottagare_id,
        mottagare_e_m=scen.mottagare_e_m,
        mottagare_n_m=scen.mottagare_n_m,
        kalla_id=scen.kalla_id,
        kalla_e_m=scen.kalla_e_m,
        kalla_n_m=scen.kalla_n_m,
        vaderklass_vikter=scen.vaderklass_vikter,
        parberaknare=scen.parberaknare() if parberaknare is None else parberaknare,
    )
    return lager


def litet_lager(h_db: Flyttal, vikter: Sequence[float] = (1.0,)) -> Overforingslager:
    """En mottagare, en källa per rad i `h_db` (form (n_kallor, n_vaderklasser, band))."""
    n_k = h_db.shape[0]
    return Overforingslager.skapa(
        mottagare_id=np.array([7], dtype=np.int64),
        mottagare_e_m=np.array([0.0]),
        mottagare_n_m=np.array([0.0]),
        kalla_id=np.arange(100, 100 + n_k, dtype=np.int64),
        kalla_e_m=np.arange(n_k, dtype=np.float64) + 10.0,
        kalla_n_m=np.zeros(n_k),
        vaderklass_vikter=np.asarray(vikter, dtype=np.float64),
        par_mottagare_index=np.zeros(n_k, dtype=np.int64),
        par_kalla_index=np.arange(n_k, dtype=np.int64),
        h_db=h_db,
    )


class TestOmkombination(unittest.TestCase):
    scen: SyntetiskScen
    raknare: RaknandeParberaknare
    lager: Overforingslager
    lw_db: Flyttal

    @classmethod
    def setUpClass(cls) -> None:
        cls.scen = skapa_syntetisk_scen(
            antal_mottagare=40, antal_kallor=600, antal_vaderklasser=3, sida_m=800.0
        )
        cls.raknare = RaknandeParberaknare(cls.scen)
        cls.lager = fullt_lager(cls.scen, cls.raknare)
        cls.lw_db = syntetisk_emission_db(cls.scen)

    def test_kant_svar_tva_lika_kallor(self) -> None:
        lager = litet_lager(np.full((2, 1, ANTAL_BAND), -20.0))
        niva = lager.niva_db(np.full((2, ANTAL_BAND), 100.0))
        np.testing.assert_allclose(niva, 80.0 + 10.0 * np.log10(2.0), atol=1e-12)

    def test_kant_svar_vaderklassvikter(self) -> None:
        h_db = np.stack([np.full((1, ANTAL_BAND), -10.0), np.full((1, ANTAL_BAND), -20.0)], axis=1)
        lager = litet_lager(h_db, vikter=(0.25, 0.75))
        forvantad = 10.0 * np.log10(0.25 * 10.0**9.0 + 0.75 * 10.0**8.0)
        np.testing.assert_allclose(lager.niva_db(np.full((1, ANTAL_BAND), 100.0)), forvantad)

    def test_a_vagd_totalniva_kant_svar(self) -> None:
        niva = np.full((1, ANTAL_BAND), 50.0)
        total = a_vagd_niva_db(niva, np.zeros(ANTAL_BAND))
        np.testing.assert_allclose(total, 50.0 + 10.0 * np.log10(ANTAL_BAND))
        vagning = np.full(ANTAL_BAND, -np.float64(3.0))
        np.testing.assert_allclose(a_vagd_niva_db(niva, vagning), total - 3.0)

    def test_stammer_med_rak_summering_enligt_kb2(self) -> None:
        np.testing.assert_allclose(
            self.lager.niva_db(self.lw_db), self.scen.referensniva_db(self.lw_db), atol=1e-9
        )

    def test_andrad_emission_utan_ny_geometri_ger_samma_svar_som_full_korning(self) -> None:
        """Acceptans (a): trafikändring utan ny geometri = full körning inom 0,01 dB."""
        anrop_fore = self.raknare.antal_anrop
        slump = np.random.default_rng(5)
        ny_lw_db = self.lw_db + slump.uniform(-10.0, 5.0, size=self.lw_db.shape)
        ny_lw_db[::7] = -np.inf  # avstängda källor
        omkombinerad = self.lager.niva_db(ny_lw_db)
        self.assertEqual(self.raknare.antal_anrop, anrop_fore, "ingen ny geometriberäkning")

        full = fullt_lager(self.scen).niva_db(ny_lw_db)
        vagning = syntetisk_vagning_db()
        self.assertLessEqual(float(np.max(np.abs(omkombinerad - full))), 0.01)
        facit = self.scen.referensniva_db(ny_lw_db)
        self.assertLessEqual(float(np.max(np.abs(omkombinerad - facit))), 0.01)
        self.assertLessEqual(
            float(
                np.max(
                    np.abs(
                        self.lager.niva_a_db(ny_lw_db, vagning) - a_vagd_niva_db(facit, vagning)
                    )
                )
            ),
            0.01,
        )

    def test_energisumman_ar_stabil_for_extrema_nivaer(self) -> None:
        bas = self.lager.niva_db(self.lw_db)
        for forskjutning_db in (-5000.0, 5000.0):
            with np.errstate(over="raise", invalid="raise"):
                niva = self.lager.niva_db(self.lw_db + forskjutning_db)
            np.testing.assert_allclose(niva - forskjutning_db, bas, atol=1e-9)

    def test_blockvis_summering_ger_samma_svar(self) -> None:
        import buller.operator.lager as lagermodul

        hel = self.lager.niva_db(self.lw_db)
        gammal = lagermodul._MAX_ELEMENT_PER_BLOCK
        try:
            lagermodul._MAX_ELEMENT_PER_BLOCK = 1000
            np.testing.assert_array_equal(self.lager.niva_db(self.lw_db), hel)
        finally:
            lagermodul._MAX_ELEMENT_PER_BLOCK = gammal

    def test_par_utan_bana_lagras_inte_och_tom_mottagare_ger_minus_inf(self) -> None:
        h_db = np.full((3, 1, ANTAL_BAND), -30.0)
        h_db[1] = -np.inf
        lager = litet_lager(h_db)
        self.assertEqual(lager.antal_par, 2)
        np.testing.assert_array_equal(lager.par_kalla_index, [0, 2])
        tomt = litet_lager(np.full((2, 1, ANTAL_BAND), -np.inf))
        self.assertEqual(tomt.antal_par, 0)
        self.assertTrue(bool(np.all(tomt.niva_db(np.zeros((2, ANTAL_BAND))) == -np.inf)))
        np.testing.assert_array_equal(
            tomt.felgrans_db(np.zeros((2, ANTAL_BAND)), np.zeros(ANTAL_BAND)), [0.0]
        )

    def test_felaktig_indata_ger_tydligt_fel(self) -> None:
        h_db = np.full((2, 1, ANTAL_BAND), -20.0)
        with self.assertRaises(BullerFel):
            litet_lager(h_db, vikter=(0.6,))
        daligt = h_db.copy()
        daligt[0, 0, 3] = np.nan
        with self.assertRaises(BullerFel):
            litet_lager(daligt)
        daligt[0, 0, 3] = np.inf
        with self.assertRaises(BullerFel):
            litet_lager(daligt)
        lager = litet_lager(h_db)
        with self.assertRaises(BullerFel):
            lager.niva_db(np.zeros((3, ANTAL_BAND)))
        with self.assertRaises(BullerFel):
            lager.niva_db(np.full((2, ANTAL_BAND), np.nan))
        with self.assertRaises(BullerFel):
            a_vagd_niva_db(np.zeros((1, ANTAL_BAND)), np.zeros(ANTAL_BAND - 1))


class TestIdentitetOchFil(unittest.TestCase):
    scen: SyntetiskScen
    lager: Overforingslager

    @classmethod
    def setUpClass(cls) -> None:
        cls.scen = skapa_syntetisk_scen(antal_mottagare=12, antal_kallor=150, antal_vaderklasser=2)
        cls.lager = fullt_lager(cls.scen)

    def test_samma_indata_ger_samma_identitet(self) -> None:
        """Acceptans (f), lagerdelen."""
        igen = fullt_lager(
            skapa_syntetisk_scen(antal_mottagare=12, antal_kallor=150, antal_vaderklasser=2)
        )
        self.assertEqual(self.lager.identitet, igen.identitet)
        self.assertEqual(len(self.lager.identitet), 64)

    def test_andrat_innehall_ger_ny_identitet(self) -> None:
        h_db = np.array(self.lager.h_db)
        h_db[5, 0, 3] += 1e-9
        andrat = Overforingslager.skapa(
            mottagare_id=self.lager.mottagare_id,
            mottagare_e_m=self.lager.mottagare_e_m,
            mottagare_n_m=self.lager.mottagare_n_m,
            kalla_id=self.lager.kalla_id,
            kalla_e_m=self.lager.kalla_e_m,
            kalla_n_m=self.lager.kalla_n_m,
            vaderklass_vikter=self.lager.vaderklass_vikter,
            par_mottagare_index=self.lager.par_mottagare_index,
            par_kalla_index=self.lager.par_kalla_index,
            h_db=h_db,
        )
        self.assertNotEqual(andrat.identitet, self.lager.identitet)
        annan_scen = skapa_syntetisk_scen(
            antal_mottagare=12, antal_kallor=150, antal_vaderklasser=2, fro=99
        )
        self.assertNotEqual(fullt_lager(annan_scen).identitet, self.lager.identitet)

    def test_lagret_ar_skrivskyddat(self) -> None:
        with self.assertRaises(ValueError):
            self.lager.h_db[0, 0, 0] = 0.0

    def test_spara_och_lasa_ar_deterministiskt(self) -> None:
        with tempfile.TemporaryDirectory() as katalog:
            fil_a = self.lager.spara(Path(katalog) / "a.npz")
            fil_b = self.lager.spara(Path(katalog) / "b.npz")
            self.assertEqual(sha256_fil(fil_a), sha256_fil(fil_b))
            last = las_overforingslager(fil_a)
            self.assertEqual(last.identitet, self.lager.identitet)
            np.testing.assert_array_equal(last.h_db, self.lager.h_db)
            lw_db = syntetisk_emission_db(self.scen)
            np.testing.assert_array_equal(last.niva_db(lw_db), self.lager.niva_db(lw_db))

    def test_andrad_fil_upptacks(self) -> None:
        with tempfile.TemporaryDirectory() as katalog:
            fil = self.lager.spara(Path(katalog) / "lager.npz")
            with np.load(fil, allow_pickle=False) as data:
                innehall = {namn: data[namn] for namn in data.files}
            innehall["h_db"] = innehall["h_db"] + 0.5
            np.savez(Path(katalog) / "andrad.npz", **innehall)
            with self.assertRaises(BullerFel):
                las_overforingslager(Path(katalog) / "andrad.npz")
            with self.assertRaises(BullerFel):
                las_overforingslager(Path(katalog) / "saknas.npz")


if __name__ == "__main__":
    unittest.main()
