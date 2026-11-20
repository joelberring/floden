"""Enhetstester för `gemensamt.regn` (Sverige/Stockholm IDF, CDS och K4-serier)."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from dynamik.nodmodell import skapa_regn_intervall
from gemensamt.regn import (
    KONTRAKT_K4,
    REGN_FORVAL,
    STANDARD_ATERKOMSTTIDER_AR,
    STANDARD_CDS_TOPPFAKTOR,
    STANDARD_KLIMATFAKTORER,
    STANDARD_TIDSSTEG_MIN,
    STANDARD_VARAKTIGHET_MIN,
    IdfTabell,
    RegnFel,
    RegnForval,
    RegnSerieK4,
    dahlstrom_2010_intensitet_l_s_ha,
    dahlstrom_2010_intensitet_mm_h,
    generera_blockregn,
    generera_cds_regn,
    hamta_regnforval,
    las_idf_tabell_csv,
    las_regn_k4_csv,
    lista_regnforval,
    skapa_regn_fran_spec,
    skriv_idf_tabell_csv,
    skriv_regn_k4_csv,
)
from sankgraf.fyllning import berakna_total_regn_m


class TestDahlstrom2010(unittest.TestCase):
    def test_publicerade_p110_varden(self) -> None:
        # Kända värden enligt Dahlström (2010) / Svenskt Vatten P104 & P110 (l/(s*ha)):
        fall = [
            (10.0, 10.0, 228.0),
            (15.0, 10.0, 180.6),
            (15.0, 2.0, 106.5),
            (15.0, 100.0, 386.8),
        ]
        for d_min, t_ar, forvantad_l_s_ha in fall:
            ber_l_s_ha = dahlstrom_2010_intensitet_l_s_ha(d_min, t_ar)
            self.assertAlmostEqual(ber_l_s_ha, forvantad_l_s_ha, delta=0.1)
            ber_mm_h = dahlstrom_2010_intensitet_mm_h(d_min, t_ar)
            self.assertAlmostEqual(ber_mm_h, ber_l_s_ha * 0.36, places=12)

    def test_klimatfaktor_och_monotonitet(self) -> None:
        tidigare_t = 0.0
        for t_ar in (20.0, 50.0, 100.0, 200.0, 500.0):
            val = dahlstrom_2010_intensitet_mm_h(360.0, t_ar)
            self.assertGreater(val, tidigare_t)
            tidigare_t = val

        tidigare_kf = 0.0
        for kf in STANDARD_KLIMATFAKTORER:
            val = dahlstrom_2010_intensitet_mm_h(60.0, 100.0, klimatfaktor=kf)
            self.assertGreater(val, tidigare_kf)
            tidigare_kf = val

        bas = dahlstrom_2010_intensitet_mm_h(30.0, 100.0, klimatfaktor=1.0)
        skalad = dahlstrom_2010_intensitet_mm_h(30.0, 100.0, klimatfaktor=1.24)
        self.assertAlmostEqual(skalad, bas * 1.24, places=12)

    def test_ogiltiga_parametrar_kastar_regnfel(self) -> None:
        for d_ogiltig in (4.99, 1440.01, 0.0, -10.0, math.nan, math.inf):
            with self.assertRaises(RegnFel):
                dahlstrom_2010_intensitet_mm_h(d_ogiltig, 100.0)
        for t_ogiltig in (0.0, -5.0, math.nan, math.inf):
            with self.assertRaises(RegnFel):
                dahlstrom_2010_intensitet_mm_h(15.0, t_ogiltig)
        for kf_ogiltig in (0.0, -1.0, math.nan, math.inf):
            with self.assertRaises(RegnFel):
                dahlstrom_2010_intensitet_mm_h(15.0, 100.0, klimatfaktor=kf_ogiltig)


class TestCdsOchBlockRegn(unittest.TestCase):
    def test_cds_exakt_volymkonservering_mot_fyllning_och_dynamik(self) -> None:
        for t_ar in (20.0, 100.0, 500.0):
            for kf in (1.0, 1.24, 1.4):
                serie = generera_cds_regn(
                    t_ar,
                    klimatfaktor=kf,
                    varaktighet_min=360.0,
                    tidssteg_min=5.0,
                    toppfaktor=STANDARD_CDS_TOPPFAKTOR,
                )
                forvantat_djup_mm = (
                    dahlstrom_2010_intensitet_mm_h(360.0, t_ar, klimatfaktor=kf)
                    * (360.0 / 60.0)
                )
                forvantat_djup_m = forvantat_djup_mm / 1000.0

                self.assertLess(abs(serie.total_djup_mm - forvantat_djup_mm), 1e-11)

                fyll_m = berakna_total_regn_m(serie.till_k4_rader())
                self.assertLess(abs(fyll_m - forvantat_djup_m), 1e-11)

                intervall, dyn_m = skapa_regn_intervall(
                    serie.tid_s,
                    serie.intensitet_mm_h,
                )
                self.assertEqual(len(intervall), 72)
                self.assertLess(abs(dyn_m - forvantat_djup_m), 1e-11)

    def test_cds_topposition_asymmetri_och_determinism(self) -> None:
        s1 = generera_cds_regn(100.0, klimatfaktor=1.24, varaktighet_min=360.0, tidssteg_min=5.0)
        s2 = generera_cds_regn(100.0, klimatfaktor=1.24, varaktighet_min=360.0, tidssteg_min=5.0)
        self.assertEqual(s1, s2)
        self.assertEqual(len(s1.tid_s), 2 * 72)

        # Varje 5-minutersblock har två identiska intensitetsändpunkter
        block_intens = [s1.intensitet_mm_h[2 * k] for k in range(72)]
        for k in range(72):
            self.assertEqual(s1.intensitet_mm_h[2 * k], s1.intensitet_mm_h[2 * k + 1])
            self.assertEqual(s1.tid_s[2 * k], k * 300.0)
            self.assertEqual(s1.tid_s[2 * k + 1], (k + 1) * 300.0)

        max_idx = block_intens.index(max(block_intens))
        self.assertEqual(max_idx, round(STANDARD_CDS_TOPPFAKTOR * (72 - 1)))
        # Före toppen ska intensiteten stiga monotont och efter toppen avta monotont
        for k in range(max_idx):
            self.assertLessEqual(block_intens[k], block_intens[k + 1])
        for k in range(max_idx, 71):
            self.assertGreaterEqual(block_intens[k], block_intens[k + 1])

    def test_blockregn_och_till_dict(self) -> None:
        serie = generera_blockregn(50.0, klimatfaktor=1.2, varaktighet_min=60.0)
        self.assertEqual(serie.typ, "block")
        self.assertEqual(serie.tid_s, (0.0, 3600.0))
        forvantad_i = dahlstrom_2010_intensitet_mm_h(60.0, 50.0, klimatfaktor=1.2)
        self.assertAlmostEqual(serie.intensitet_mm_h[0], forvantad_i, places=12)
        self.assertAlmostEqual(serie.total_djup_mm, forvantad_i, places=12)
        self.assertAlmostEqual(
            berakna_total_regn_m(serie.till_k4_rader()) * 1000.0,
            forvantad_i,
            places=12,
        )
        data = serie.till_dict()
        self.assertEqual(data["kontrakt"], KONTRAKT_K4)
        self.assertEqual(data["regn_id"], serie.regn_id)

    def test_cds_valideringsfel(self) -> None:
        with self.assertRaises(RegnFel):
            generera_cds_regn(100.0, varaktighet_min=62.0, tidssteg_min=5.0)
        with self.assertRaises(RegnFel):
            generera_cds_regn(100.0, varaktighet_min=60.0, tidssteg_min=3.0)
        with self.assertRaises(RegnFel):
            generera_cds_regn(100.0, toppfaktor=0.0)
        with self.assertRaises(RegnFel):
            generera_cds_regn(100.0, toppfaktor=1.0)


class TestIdfTabellOchCsv(unittest.TestCase):
    def _bygg_testtabell(self) -> IdfTabell:
        varakt = (5.0, 15.0, 60.0, 360.0)
        aterkomst = (10.0, 50.0, 100.0, 200.0)
        rader = tuple(
            tuple(
                dahlstrom_2010_intensitet_mm_h(d, t)
                for t in aterkomst
            )
            for d in varakt
        )
        return IdfTabell(
            namn="Syntetisk referenstabell",
            kalla="Test-IDF",
            varaktigheter_min=varakt,
            aterkomsttider_ar=aterkomst,
            intensiteter_mm_h=rader,
        )

    def test_exakt_knutpunkt_interpolation_och_extrapoleringsfel(self) -> None:
        tab = self._bygg_testtabell()
        self.assertAlmostEqual(
            tab.intensitet_mm_h(15.0, 100.0, klimatfaktor=1.25),
            dahlstrom_2010_intensitet_mm_h(15.0, 100.0, klimatfaktor=1.25),
            places=12,
        )

        # Mellanliggande punkt: 30 min (geometriskt mitt mellan 15 och 60) och 20 år
        interp = tab.intensitet_mm_h(30.0, 20.0)
        self.assertGreater(interp, tab.intensitet_mm_h(60.0, 20.0))
        self.assertLess(interp, tab.intensitet_mm_h(15.0, 20.0))
        self.assertGreater(interp, tab.intensitet_mm_h(30.0, 10.0))
        self.assertLess(interp, tab.intensitet_mm_h(30.0, 50.0))

        # Ingen tyst extrapolering utanför tabellens intervall
        for d, t in ((4.0, 100.0), (400.0, 100.0), (15.0, 5.0), (15.0, 500.0)):
            with self.assertRaises(RegnFel):
                tab.intensitet_mm_h(d, t)

    def test_idf_tabell_csv_rundtur_och_cds(self) -> None:
        tab = self._bygg_testtabell()
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "idf" / "stockholm_test.csv"
            skriv_idf_tabell_csv(fil, tab)
            last = las_idf_tabell_csv(fil, namn="Lokal test", kalla="Lokal testkälla")
            self.assertEqual(last.namn, "Lokal test")
            self.assertEqual(last.kalla, "Lokal testkälla")
            self.assertEqual(last.varaktigheter_min, tab.varaktigheter_min)
            self.assertEqual(last.aterkomsttider_ar, tab.aterkomsttider_ar)
            self.assertAlmostEqual(
                last.intensitet_mm_h(60.0, 100.0),
                tab.intensitet_mm_h(60.0, 100.0),
                places=5,
            )

            cds = generera_cds_regn(
                100.0,
                klimatfaktor=1.24,
                varaktighet_min=360.0,
                tidssteg_min=5.0,
                idf_tabell=last,
            )
            self.assertEqual(cds.statistikkalla, "Lokal testkälla")
            forvantat = last.intensitet_mm_h(360.0, 100.0, klimatfaktor=1.24) * 6.0
            self.assertLess(abs(cds.total_djup_mm - forvantat), 1e-11)

    def test_k4_csv_rundtur(self) -> None:
        serie = hamta_regnforval("stockholm_cds_6h_100ar_kf124").generera()
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "k4" / "regn.csv"
            skriv_regn_k4_csv(fil, serie)
            last = las_regn_k4_csv(fil, regn_id="rundtur", namn="Rundtur K4")
            self.assertEqual(last.typ, "csv")
            self.assertEqual(len(last.tid_s), len(serie.tid_s))
            self.assertLess(abs(last.total_djup_mm - serie.total_djup_mm), 1e-5)
            fyll_mm = berakna_total_regn_m(last.till_k4_rader()) * 1000.0
            self.assertLess(abs(fyll_mm - serie.total_djup_mm), 1e-5)

    def test_ogiltig_idf_tabell_avvisas(self) -> None:
        with self.assertRaises(RegnFel):
            IdfTabell(
                namn="Felaktig",
                kalla="Test",
                varaktigheter_min=(10.0, 10.0),
                aterkomsttider_ar=(10.0, 100.0),
                intensiteter_mm_h=((50.0, 90.0), (40.0, 80.0)),
            )


class TestRegnForvalOchSpec(unittest.TestCase):
    def test_alla_inbyggda_forval_kan_genereras_och_monotonitet(self) -> None:
        self.assertGreaterEqual(len(REGN_FORVAL), 40)
        stockholm = lista_regnforval(region="Stockholm")
        sverige = lista_regnforval(region="Sverige")
        self.assertEqual(len(stockholm), 10)  # 5 återkomsttider * 2 klimatfaktorer
        self.assertEqual(len(sverige), 30)  # 6 återkomsttider * 5 klimatfaktorer

        for forval in lista_regnforval():
            self.assertIsInstance(forval, RegnForval)
            serie = forval.generera()
            self.assertIsInstance(serie, RegnSerieK4)
            self.assertGreater(serie.total_djup_mm, 10.0)
            self.assertLess(serie.total_djup_mm, 400.0)
            self.assertGreater(serie.max_intensitet_mm_h, 0.0)
            spec_dict = forval.till_dict()
            self.assertAlmostEqual(spec_dict["total_djup_mm"], serie.total_djup_mm, places=12)

        # Stockholm-förval är transparenta med sin metodik och P110-bas
        sthlm_100 = hamta_regnforval("stockholm_cds_6h_100ar_kf124")
        self.assertIn("Stockholmsregn 2024", sthlm_100.statistikkalla)
        self.assertIn("Dahlström (2010)", sthlm_100.statistikkalla)

        # Monotonitet över återkomsttid för Stockholm kf=1.24
        djup_sthlm = [
            hamta_regnforval(f"stockholm_cds_6h_{t}ar_kf124").generera().total_djup_mm
            for t in (20, 50, 100, 200, 500)
        ]
        for idx in range(len(djup_sthlm) - 1):
            self.assertLess(djup_sthlm[idx], djup_sthlm[idx + 1])

        # Monotonitet över klimatfaktor för Sverige 100-årsregn
        djup_kf = [
            hamta_regnforval(f"sverige_cds_6h_100ar_{tag}").generera().total_djup_mm
            for tag in ("kf100", "kf120", "kf125", "kf130", "kf140")
        ]
        for idx in range(len(djup_kf) - 1):
            self.assertLess(djup_kf[idx], djup_kf[idx + 1])

    def test_skapa_regn_fran_spec(self) -> None:
        s_forval = skapa_regn_fran_spec(regn_id="stockholm_cds_6h_100ar_kf124")
        self.assertAlmostEqual(s_forval.klimatfaktor, 1.24, places=12)
        self.assertEqual(s_forval.aterkomsttid_ar, 100.0)

        s_override = skapa_regn_fran_spec(
            regn_id="stockholm_cds_6h_100ar_kf124",
            klimatfaktor=1.4,
            aterkomsttid_ar=200.0,
        )
        self.assertAlmostEqual(s_override.klimatfaktor, 1.4, places=12)
        self.assertEqual(s_override.aterkomsttid_ar, 200.0)

        s_fri = skapa_regn_fran_spec(
            aterkomsttid_ar=50.0,
            klimatfaktor=1.25,
            varaktighet_min=180.0,
            typ="block",
        )
        self.assertEqual(s_fri.typ, "block")
        self.assertEqual(s_fri.varaktighet_min, 180.0)

        with self.assertRaises(RegnFel):
            skapa_regn_fran_spec()
        with self.assertRaises(RegnFel):
            hamta_regnforval("finns_inte")
        with self.assertRaises(RegnFel):
            lista_regnforval(region="Malmo")

    def test_standardkonstanter(self) -> None:
        self.assertEqual(
            STANDARD_ATERKOMSTTIDER_AR,
            (2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0),
        )
        self.assertEqual(STANDARD_KLIMATFAKTORER, (1.0, 1.2, 1.24, 1.25, 1.3, 1.4))
        self.assertEqual(STANDARD_CDS_TOPPFAKTOR, 0.37)
        self.assertEqual(STANDARD_VARAKTIGHET_MIN, 360.0)
        self.assertEqual(STANDARD_TIDSSTEG_MIN, 5.0)


if __name__ == "__main__":
    unittest.main()
