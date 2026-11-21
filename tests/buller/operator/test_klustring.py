"""Tester för källklustring med garanterad felgräns."""

from __future__ import annotations

import unittest

import numpy as np

from buller.operator.kalltrad import bygg_kalltrad
from buller.operator.klustring import (
    Klustringsstatistik,
    bygg_fullt_lager,
    bygg_overforingslager,
    omklustra_mottagare,
)
from buller.operator.lager import Overforingslager, a_vagd_niva_db
from buller.operator.syntetisk import (
    SyntetiskScen,
    skapa_syntetisk_scen,
    syntetisk_emission_db,
    syntetisk_vagning_db,
)
from buller.typer import BullerFel, Flyttal

# Marginal för avrundning i jämförelser av decibelvärden.
_AVRUNDNING_DB = 1e-9


def klustrat_lager(
    scen: SyntetiskScen,
    lw_db: Flyttal,
    felgrans_db: float,
    *,
    forskjutning_db: float = 0.0,
    maxradie_m: float | None = None,
    kontrollera_ovre_grans: bool = True,
) -> tuple[Overforingslager, Klustringsstatistik]:
    return bygg_overforingslager(
        mottagare_id=scen.mottagare_id,
        mottagare_e_m=scen.mottagare_e_m,
        mottagare_n_m=scen.mottagare_n_m,
        kalla_id=scen.kalla_id,
        kalla_e_m=scen.kalla_e_m,
        kalla_n_m=scen.kalla_n_m,
        vaderklass_vikter=scen.vaderklass_vikter,
        parberaknare=scen.parberaknare(),
        ovre_grans=scen.ovre_grans(forskjutning_db),
        lw_db=lw_db,
        a_vagning_db=syntetisk_vagning_db(),
        felgrans_db=felgrans_db,
        maxradie_m=maxradie_m,
        kontrollera_ovre_grans=kontrollera_ovre_grans,
    )


def oklustrat_lager(scen: SyntetiskScen) -> Overforingslager:
    lager, _ = bygg_fullt_lager(
        mottagare_id=scen.mottagare_id,
        mottagare_e_m=scen.mottagare_e_m,
        mottagare_n_m=scen.mottagare_n_m,
        kalla_id=scen.kalla_id,
        kalla_e_m=scen.kalla_e_m,
        kalla_n_m=scen.kalla_n_m,
        vaderklass_vikter=scen.vaderklass_vikter,
        parberaknare=scen.parberaknare(),
    )
    return lager


class TestKalltrad(unittest.TestCase):
    def test_tradet_delar_kallorna_i_lov_med_ratt_grans(self) -> None:
        slump = np.random.default_rng(3)
        e_m, n_m = slump.uniform(0, 500, 301), slump.uniform(0, 200, 301)
        e_m[10:20] = e_m[10]  # sammanfallande koordinater
        trad = bygg_kalltrad(e_m, n_m, lovstorlek=4)
        np.testing.assert_array_equal(np.sort(trad.ordning), np.arange(301))
        lov = np.flatnonzero(trad.nod_vanster < 0)
        self.assertEqual(int(np.sum(trad.nod_slut[lov] - trad.nod_start[lov])), 301)
        self.assertLessEqual(int(np.max(trad.nod_slut[lov] - trad.nod_start[lov])), 4)
        for nod in range(trad.antal_noder):
            urval = trad.ordning[trad.nod_start[nod] : trad.nod_slut[nod]]
            self.assertEqual(trad.nod_e_min_m[nod], e_m[urval].min())
            self.assertEqual(trad.nod_n_max_m[nod], n_m[urval].max())
            if trad.nod_vanster[nod] >= 0:
                v, h = trad.nod_vanster[nod], trad.nod_hoger[nod]
                self.assertEqual(trad.nod_start[v], trad.nod_start[nod])
                self.assertEqual(trad.nod_slut[v], trad.nod_start[h])
                self.assertEqual(trad.nod_slut[h], trad.nod_slut[nod])
        self.assertEqual(trad.minsta_avstand_m(0, 250.0, 100.0), 0.0)
        igen = bygg_kalltrad(e_m, n_m, lovstorlek=4)
        np.testing.assert_array_equal(igen.ordning, trad.ordning)


class TestKlustringStorScen(unittest.TestCase):
    """Acceptans (b) och (g) på 200 mottagare och 5 000 källor."""

    scen: SyntetiskScen
    lw_db: Flyttal
    facit_a_db: Flyttal

    @classmethod
    def setUpClass(cls) -> None:
        cls.scen = skapa_syntetisk_scen(antal_mottagare=200, antal_kallor=5000)
        cls.lw_db = syntetisk_emission_db(cls.scen)
        cls.facit_a_db = a_vagd_niva_db(
            cls.scen.referensniva_db(cls.lw_db), syntetisk_vagning_db()
        )

    def test_uppmatt_fel_ar_hogst_garanterad_grans_i_varje_mottagare(self) -> None:
        andelar: dict[float, float] = {}
        for felgrans_db in (0.1, 0.5):
            with self.subTest(felgrans_db=felgrans_db):
                lager, statistik = klustrat_lager(self.scen, self.lw_db, felgrans_db)
                vagning = syntetisk_vagning_db()
                uppmatt_fel_db = self.facit_a_db - lager.niva_a_db(self.lw_db, vagning)
                garanterad_db = lager.felgrans_db(self.lw_db, vagning)
                self.assertEqual(uppmatt_fel_db.shape, (200,))
                # Felet är ensidigt (utelämnade par), högst den redovisade gränsen och högst ε.
                self.assertTrue(bool(np.all(uppmatt_fel_db >= -_AVRUNDNING_DB)))
                self.assertTrue(bool(np.all(uppmatt_fel_db <= garanterad_db + _AVRUNDNING_DB)))
                self.assertTrue(bool(np.all(garanterad_db <= felgrans_db + _AVRUNDNING_DB)))
                self.assertTrue(bool(np.all(uppmatt_fel_db <= felgrans_db)))
                # Redovisning av arbetet.
                self.assertEqual(statistik.antal_mojliga_par, 200 * 5000)
                self.assertEqual(
                    statistik.antal_beraknade_par + lager.antal_restpar, 200 * 5000
                )
                self.assertLess(statistik.andel_beraknade_par, 1.0)
                self.assertGreater(statistik.andel_beraknade_par, 0.0)
                andelar[felgrans_db] = statistik.andel_beraknade_par
                print(
                    f"\n[B8] ε = {felgrans_db} dB: andel beräknade par "
                    f"{statistik.andel_beraknade_par:.4f}, största uppmätta fel "
                    f"{float(np.max(uppmatt_fel_db)):.4f} dB, största garanterade gräns "
                    f"{float(np.max(garanterad_db)):.4f} dB"
                )
        self.assertLess(andelar[0.5], andelar[0.1])

    def test_felaktig_ovre_grans_kan_ge_fel_over_epsilon(self) -> None:
        """Acceptans (g): garantin vilar på antagandet att övre gränsen håller.

        Med en gräns som är 30 dB för låg slutar klustringen för tidigt. Lagret redovisar
        fortfarande en "garanterad" gräns under ε, men det verkliga felet är större.
        """
        felgrans_db = 0.1
        vagning = syntetisk_vagning_db()
        lager, _ = klustrat_lager(
            self.scen,
            self.lw_db,
            felgrans_db,
            forskjutning_db=-30.0,
            kontrollera_ovre_grans=False,
        )
        uppmatt_fel_db = self.facit_a_db - lager.niva_a_db(self.lw_db, vagning)
        pastadd_db = lager.felgrans_db(self.lw_db, vagning)
        self.assertTrue(bool(np.all(pastadd_db <= felgrans_db + _AVRUNDNING_DB)))
        self.assertGreater(float(np.max(uppmatt_fel_db)), felgrans_db)
        self.assertTrue(bool(np.any(uppmatt_fel_db > pastadd_db + 0.01)))

    def test_felaktig_ovre_grans_upptacks_pa_beraknade_par(self) -> None:
        with self.assertRaisesRegex(BullerFel, "Övre gränsen håller inte"):
            klustrat_lager(self.scen, self.lw_db, 0.1, forskjutning_db=-30.0)


class TestKlustringLitenScen(unittest.TestCase):
    scen: SyntetiskScen
    lw_db: Flyttal

    @classmethod
    def setUpClass(cls) -> None:
        cls.scen = skapa_syntetisk_scen(
            antal_mottagare=30, antal_kallor=700, antal_vaderklasser=2, sida_m=1500.0
        )
        cls.lw_db = syntetisk_emission_db(cls.scen)

    def test_epsilon_noll_ger_exakt_samma_resultat_som_oklustrad_korning(self) -> None:
        """Acceptans (c)."""
        oklustrat = oklustrat_lager(self.scen)
        vagning = syntetisk_vagning_db()
        for felgrans_db in (0.0, 1e-300):
            with self.subTest(felgrans_db=felgrans_db):
                lager, statistik = klustrat_lager(self.scen, self.lw_db, felgrans_db)
                if felgrans_db == 0.0:
                    self.assertEqual(statistik.andel_beraknade_par, 1.0)
                    self.assertFalse(lager.har_rest)
                    np.testing.assert_array_equal(lager.h_db, oklustrat.h_db)
                np.testing.assert_array_equal(
                    lager.niva_db(self.lw_db), oklustrat.niva_db(self.lw_db)
                )
                np.testing.assert_array_equal(
                    lager.niva_a_db(self.lw_db, vagning),
                    oklustrat.niva_a_db(self.lw_db, vagning),
                )
        lager, _ = klustrat_lager(self.scen, self.lw_db, 0.0)
        np.testing.assert_array_equal(lager.felgrans_db(self.lw_db, vagning), 0.0)

    def test_fel_minskar_mot_noll_nar_epsilon_minskar(self) -> None:
        vagning = syntetisk_vagning_db()
        facit = oklustrat_lager(self.scen).niva_a_db(self.lw_db, vagning)
        for felgrans_db in (1.0, 0.1, 0.01, 0.001):
            lager, _ = klustrat_lager(self.scen, self.lw_db, felgrans_db)
            fel = facit - lager.niva_a_db(self.lw_db, vagning)
            self.assertLessEqual(float(np.max(fel)), felgrans_db)
            self.assertGreaterEqual(float(np.min(fel)), -_AVRUNDNING_DB)

    def test_felgransen_galler_aven_for_ny_emission(self) -> None:
        """Lagrets redovisade gräns räknas om för emissionen och håller, även om den då > ε."""
        vagning = syntetisk_vagning_db()
        lager, _ = klustrat_lager(self.scen, self.lw_db, 0.3)
        slump = np.random.default_rng(11)
        ny_lw_db = self.lw_db + slump.uniform(-15.0, 15.0, size=(self.scen.antal_kallor, 1))
        facit = oklustrat_lager(self.scen).niva_a_db(ny_lw_db, vagning)
        fel = facit - lager.niva_a_db(ny_lw_db, vagning)
        grans = lager.felgrans_db(ny_lw_db, vagning)
        self.assertTrue(bool(np.all(fel <= grans + _AVRUNDNING_DB)))
        self.assertTrue(bool(np.all(fel >= -_AVRUNDNING_DB)))
        self.assertGreater(float(np.max(grans)), 0.3, "testet ska pröva fallet gräns > ε")

    def test_omklustring_aterstaller_felgransen_for_utvalda_mottagare(self) -> None:
        vagning = syntetisk_vagning_db()
        lager, _ = klustrat_lager(self.scen, self.lw_db, 0.3)
        slump = np.random.default_rng(11)
        ny_lw_db = self.lw_db + slump.uniform(-15.0, 15.0, size=(self.scen.antal_kallor, 1))
        over = np.flatnonzero(lager.felgrans_db(ny_lw_db, vagning) > 0.3)
        self.assertGreater(over.size, 0)
        nytt, statistik = omklustra_mottagare(
            lager,
            over.tolist(),
            parberaknare=self.scen.parberaknare(),
            ovre_grans=self.scen.ovre_grans(),
            lw_db=ny_lw_db,
            a_vagning_db=vagning,
            felgrans_db=0.3,
        )
        self.assertEqual(statistik.antal_mottagare, over.size)
        self.assertNotEqual(nytt.identitet, lager.identitet)
        self.assertLessEqual(float(np.max(nytt.felgrans_db(ny_lw_db, vagning))), 0.3 + 1e-9)
        facit = oklustrat_lager(self.scen).niva_a_db(ny_lw_db, vagning)
        self.assertLessEqual(float(np.max(facit - nytt.niva_a_db(ny_lw_db, vagning))), 0.3)
        # Övriga mottagares rader är orörda.
        ovriga = np.setdiff1d(np.arange(self.scen.antal_mottagare), over)
        np.testing.assert_array_equal(
            nytt.niva_db(self.lw_db)[ovriga], lager.niva_db(self.lw_db)[ovriga]
        )

    def test_maxradie_ingar_i_redovisad_felgrans(self) -> None:
        vagning = syntetisk_vagning_db()
        lager, statistik = klustrat_lager(self.scen, self.lw_db, 0.0, maxradie_m=300.0)
        self.assertLess(statistik.andel_beraknade_par, 1.0)
        facit = oklustrat_lager(self.scen).niva_a_db(self.lw_db, vagning)
        fel = facit - lager.niva_a_db(self.lw_db, vagning)
        grans = lager.felgrans_db(self.lw_db, vagning)
        self.assertGreater(float(np.max(fel)), 0.0)
        self.assertTrue(bool(np.all(fel <= grans + _AVRUNDNING_DB)))
        # Med ε = 0 är det bara maxradien som lämnar rest: inget restpar ligger inom radien.
        self.assertTrue(lager.har_rest)
        for m, start, slut in zip(
            lager.rest_mottagare_index.tolist(),
            lager.rest_start.tolist(),
            lager.rest_slut.tolist(),
            strict=True,
        ):
            kallor = lager.kall_ordning[start:slut]
            avstand_m = np.hypot(
                self.scen.kalla_e_m[kallor] - self.scen.mottagare_e_m[m],
                self.scen.kalla_n_m[kallor] - self.scen.mottagare_n_m[m],
            )
            self.assertGreater(float(np.min(avstand_m)), 300.0)

    def test_blockerande_byggnad_och_avstangda_kallor(self) -> None:
        scen = skapa_syntetisk_scen(
            antal_mottagare=25, antal_kallor=400, sida_m=600.0, byggnad_blockerar=True
        )
        lw_db = syntetisk_emission_db(scen)
        lw_db[::3] = -np.inf
        vagning = syntetisk_vagning_db()
        facit = oklustrat_lager(scen).niva_a_db(lw_db, vagning)
        lager, _ = klustrat_lager(scen, lw_db, 0.2)
        klustrad = lager.niva_a_db(lw_db, vagning)
        # En mottagare inne i byggnaden saknar bana till alla källor: -inf i båda.
        tyst = np.isneginf(facit)
        self.assertTrue(bool(np.any(tyst)), "testet ska pröva en mottagare utan någon bana")
        np.testing.assert_array_equal(klustrad[tyst], -np.inf)
        fel = facit[~tyst] - klustrad[~tyst]
        grans = lager.felgrans_db(lw_db, vagning)
        np.testing.assert_array_equal(grans[tyst], 0.0)
        self.assertTrue(bool(np.all(fel <= grans[~tyst] + _AVRUNDNING_DB)))
        self.assertLessEqual(float(np.max(fel)), 0.2)

    def test_ogiltiga_parametrar(self) -> None:
        with self.assertRaises(BullerFel):
            klustrat_lager(self.scen, self.lw_db, -0.1)
        with self.assertRaises(BullerFel):
            klustrat_lager(self.scen, self.lw_db, 0.1, maxradie_m=0.0)
        with self.assertRaises(BullerFel):
            klustrat_lager(self.scen, self.lw_db[:-1], 0.1)

    def test_samma_indata_ger_samma_lager(self) -> None:
        a, _ = klustrat_lager(self.scen, self.lw_db, 0.25)
        b, _ = klustrat_lager(self.scen, self.lw_db, 0.25)
        self.assertEqual(a.identitet, b.identitet)


if __name__ == "__main__":
    unittest.main()
