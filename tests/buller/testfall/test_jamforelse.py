"""Tester för jämförelselogiken (Miljøprojekt 1335/2010, kapitel 6) på konstruerade exempel."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from buller.testfall import Facitserie, Utfall, jamfor_serie, las_regler
from buller.testfall.jamforelse import (
    REGELFIL,
    jamfor_spektrum,
    jamfor_total,
    lokala_minima,
    samsta,
)
from buller.typer import ANTAL_BAND, BullerFel, Flyttal

DIPP = 10  # bandindex för den konstruerade markeffektdippen


def spektrum_med_dipp(band: int, djup_db: float = 12.0, bredd: int = 1) -> Flyttal:
    """Svagt fallande spektrum med en V-formad dipp i `band`."""
    s = 60.0 - 0.2 * np.arange(ANTAL_BAND)
    for steg in range(-bredd + 1, bredd):
        s[band + steg] -= djup_db * (1.0 - abs(steg) / bredd)
    return np.asarray(s, dtype=np.float64)


class TestRegelfil(unittest.TestCase):
    def test_regelvarden_och_kalla(self) -> None:
        regler = las_regler()
        self.assertEqual(regler.total_grans_utredas_db, 0.5)
        self.assertEqual(regler.total_grans_underkand_db, 1.0)
        self.assertEqual(regler.band_grans_utredas_db, 1.0)
        self.assertEqual(regler.dipp_tillaten_forskjutning_band, 1)
        self.assertIn("1335", regler.kalla)
        self.assertIn("dL", regler.band_jamfors_inte)
        for storhet in ("LAeq_24h", "LAE", "LAmax"):
            self.assertIn(storhet, regler.total_storheter)

    def test_saknat_varde_ger_fel(self) -> None:
        text = REGELFIL.read_text(encoding="utf-8").replace("grans_underkand_db = 1.0", "")
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "regler.toml"
            fil.write_text(text, encoding="utf-8")
            with self.assertRaises(BullerFel):
                las_regler(fil)
            with self.assertRaises(BullerFel):
                las_regler(Path(tmp) / "saknas.toml")


class TestRegelverkRl18(unittest.TestCase):
    """RL 18-18 kapitel 6 och de källhänvisade undantagen för kända fel i testfallen."""

    def test_standard_namner_lden_men_inte_lamax(self) -> None:
        regler = las_regler(regelverk="rl18_18")
        self.assertEqual(regler.total_storheter, ("LAeq_24h", "Lden", "LAE"))
        self.assertEqual(regler.total_grans_utredas_db, 0.5)
        self.assertEqual(regler.total_grans_underkand_db, 1.0)
        self.assertIn("RL 18-18", regler.kalla)

    def test_kurvig_vag_gul_har_dubbla_granser(self) -> None:
        regler = las_regler(regelverk="rl18_18", variant="kurvig_vag_gul")
        self.assertFalse(regler.band_jamfors)
        fall = [(1.0, Utfall.GODKAND), (1.5, Utfall.SKA_UTREDAS), (2.0, Utfall.SKA_UTREDAS),
                (2.5, Utfall.UNDERKAND)]  # fmt: skip
        for avvikelse, vantat in fall:
            self.assertIs(jamfor_total("LAeq_24h", 40.0 + avvikelse, 40.0, regler).klass, vantat)

    def test_kurvig_vag_motvind_underkanns_aldrig(self) -> None:
        regler = las_regler(regelverk="rl18_18", variant="kurvig_vag_motvind")
        self.assertIsNone(regler.total_grans_underkand_db)
        self.assertIs(jamfor_total("LAeq_24h", 42.0, 40.0, regler).klass, Utfall.GODKAND)
        self.assertIs(jamfor_total("LAeq_24h", 49.0, 40.0, regler).klass, Utfall.SKA_UTREDAS)

    def test_kurvig_vag_jamfor_bara_laeq(self) -> None:
        regler = las_regler(regelverk="rl18_18", variant="kurvig_vag_gul")
        facit = Facitserie(
            "u0", {}, {"LAeq_24h": 40.0, "LAmax": 45.0}, {"Leq_24h": spektrum_med_dipp(DIPP)}
        )
        resultat = jamfor_serie({"LAeq_24h": 40.5}, {}, facit, regler, ar_facit=True)
        self.assertIs(resultat.klass, Utfall.GODKAND)
        self.assertEqual([t.storhet for t in resultat.totaler], ["LAeq_24h"])
        self.assertEqual(resultat.band, ())

    def test_undantag_lamax_jamfors_inte_och_redovisas(self) -> None:
        regler = las_regler(regelverk="rl18_18")
        self.assertEqual({u.id for u in regler.undantag}, {"KCB-2024-1", "KCB-2024-2"})
        facit = Facitserie("s", {}, {"LAeq_24h": 40.0, "LAmax": 45.0}, {})
        resultat = jamfor_serie(
            {"LAeq_24h": 40.2, "LAmax": 30.0}, {}, facit, regler, ar_facit=True
        )
        self.assertIs(resultat.klass, Utfall.GODKAND)
        self.assertEqual([t.storhet for t in resultat.totaler], ["LAeq_24h"])
        text = " ".join(resultat.anmarkningar)
        self.assertIn("LAmax jämförs inte (KCB-2024-2, Kunskapscentrum om buller", text)
        self.assertIn("KCB-2024-1", text)
        # Undantagen hör till RL 18-18 och gäller inte den äldre omgången.
        self.assertEqual(las_regler(regelverk="mp1335_2010").undantag, ())

    def test_okant_regelverk_eller_variant_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            las_regler(regelverk="finns_inte")
        with self.assertRaises(BullerFel):
            las_regler(regelverk="rl18_18", variant="finns_inte")


class TestTotalniva(unittest.TestCase):
    def setUp(self) -> None:
        self.regler = las_regler()

    def test_klasser_och_granser(self) -> None:
        fall = [
            (0.0, Utfall.GODKAND),
            (0.5, Utfall.GODKAND),  # "exceeding 0.5 dB": precis 0,5 är inte över
            (-0.5, Utfall.GODKAND),
            (0.51, Utfall.SKA_UTREDAS),
            (-0.75, Utfall.SKA_UTREDAS),
            (1.0, Utfall.SKA_UTREDAS),  # "more than 1 dB": precis 1,0 är inte underkänt
            (1.01, Utfall.UNDERKAND),
            (-3.0, Utfall.UNDERKAND),
        ]
        for avvikelse, vantat in fall:
            with self.subTest(avvikelse):
                # 0,5 och 1,0 är exakta i binär form när facit är 40.
                resultat = jamfor_total("LAeq_24h", 40.0 + avvikelse, 40.0, self.regler)
                self.assertIs(resultat.klass, vantat)
                self.assertAlmostEqual(resultat.avvikelse_db, avvikelse)

    def test_icke_andligt_varde_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            jamfor_total("LAE", float("nan"), 40.0, self.regler)


class TestTersband(unittest.TestCase):
    def setUp(self) -> None:
        self.regler = las_regler()
        self.facit = spektrum_med_dipp(DIPP)

    def test_inom_gransen_ar_godkant(self) -> None:
        ber = self.facit + np.where(np.arange(ANTAL_BAND) % 2 == 0, 0.9, -1.0)
        resultat = jamfor_spektrum("Leq_24h", ber, self.facit, self.regler)
        self.assertIs(resultat.klass, Utfall.GODKAND)
        self.assertEqual(resultat.band_over_grans, ())
        self.assertAlmostEqual(resultat.max_avvikelse_db, 1.0)

    def test_ett_band_over_gransen_ska_utredas_men_underkanns_inte(self) -> None:
        for extra in (1.2, 25.0):
            ber = self.facit.copy()
            ber[3] += extra
            resultat = jamfor_spektrum("Leq_24h", ber, self.facit, self.regler)
            self.assertIs(resultat.klass, Utfall.SKA_UTREDAS)
            self.assertEqual(resultat.band_over_grans, (3,))
            self.assertEqual(resultat.ursaktade_band, ())

    def test_dipp_forskjuten_ett_band_godtas(self) -> None:
        for riktning in (+1, -1):
            for bredd in (1, 2):
                with self.subTest(riktning=riktning, bredd=bredd):
                    facit = spektrum_med_dipp(DIPP, bredd=bredd)
                    ber = spektrum_med_dipp(DIPP + riktning, bredd=bredd)
                    self.assertEqual(lokala_minima(facit), (DIPP,))
                    resultat = jamfor_spektrum("Leq_24h", ber, facit, self.regler)
                    self.assertGreater(resultat.max_avvikelse_db, 5.0)
                    self.assertTrue(resultat.band_over_grans)
                    self.assertEqual(resultat.ursaktade_band, resultat.band_over_grans)
                    self.assertEqual(resultat.dippforskjutning_band, riktning)
                    self.assertIs(resultat.klass, Utfall.GODKAND)

    def test_dipp_forskjuten_tva_band_ska_utredas(self) -> None:
        ber = spektrum_med_dipp(DIPP + 2)
        resultat = jamfor_spektrum("Leq_24h", ber, self.facit, self.regler)
        self.assertIs(resultat.klass, Utfall.SKA_UTREDAS)
        self.assertEqual(resultat.ursaktade_band, ())
        self.assertIsNone(resultat.dippforskjutning_band)

    def test_dippforskjutning_ursaktar_inte_avvikelser_langt_fran_dippen(self) -> None:
        ber = spektrum_med_dipp(DIPP + 1)
        ber[20] += 2.0
        resultat = jamfor_spektrum("Leq_24h", ber, self.facit, self.regler)
        self.assertIs(resultat.klass, Utfall.SKA_UTREDAS)
        self.assertEqual(set(resultat.band_over_grans) - set(resultat.ursaktade_band), {20})

    def test_samma_dipplage_men_fel_djup_ar_ingen_forskjutning(self) -> None:
        ber = spektrum_med_dipp(DIPP, djup_db=6.0)
        resultat = jamfor_spektrum("Leq_24h", ber, self.facit, self.regler)
        self.assertIs(resultat.klass, Utfall.SKA_UTREDAS)
        self.assertEqual(resultat.band_over_grans, (DIPP,))

    def test_fel_form_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            jamfor_spektrum("Leq_24h", np.zeros(5), self.facit, self.regler)
        with self.assertRaises(BullerFel):
            jamfor_spektrum("Leq_24h", np.full(ANTAL_BAND, np.nan), self.facit, self.regler)


class TestSerie(unittest.TestCase):
    def setUp(self) -> None:
        self.regler = las_regler()
        self.spektrum = spektrum_med_dipp(DIPP)
        self.facit = Facitserie(
            "s1",
            {},
            {"LAeq_24h": 40.0, "LAE": 49.37},
            {"Leq_24h": self.spektrum, "dL": np.zeros(ANTAL_BAND)},
        )

    def _jamfor(
        self, d_total: float = 0.0, d_band: float = 0.0, ar_facit: bool = True
    ) -> Utfall:
        return jamfor_serie(
            {"LAeq_24h": 40.0 + d_total, "LAE": 49.37},
            {"Leq_24h": self.spektrum + d_band},
            self.facit,
            self.regler,
            ar_facit=ar_facit,
        ).klass

    def test_varje_resultatklass(self) -> None:
        self.assertIs(self._jamfor(0.2, 0.3), Utfall.GODKAND)
        self.assertIs(self._jamfor(0.7, 0.3), Utfall.SKA_UTREDAS)
        self.assertIs(self._jamfor(0.2, 1.5), Utfall.SKA_UTREDAS)
        self.assertIs(self._jamfor(1.5, 0.3), Utfall.UNDERKAND)
        self.assertIs(self._jamfor(1.5, 1.5), Utfall.UNDERKAND)

    def test_ej_facit_blir_aldrig_godkand_eller_underkand(self) -> None:
        for d_total, d_band in ((0.0, 0.0), (0.7, 0.0), (5.0, 9.0)):
            with self.subTest(d_total=d_total):
                resultat = jamfor_serie(
                    {"LAeq_24h": 40.0 + d_total, "LAE": 49.37},
                    {"Leq_24h": self.spektrum + d_band},
                    self.facit,
                    self.regler,
                    ar_facit=False,
                )
                self.assertIs(resultat.klass, Utfall.EJ_FACIT)
                self.assertTrue(all(t.klass is Utfall.EJ_FACIT for t in resultat.totaler))
                self.assertTrue(all(b.klass is Utfall.EJ_FACIT for b in resultat.band))
                # Avvikelserna redovisas ändå.
                self.assertAlmostEqual(resultat.max_total_avvikelse_db or 0.0, d_total)

    def test_dl_jamfors_inte(self) -> None:
        resultat = jamfor_serie(
            {"LAeq_24h": 40.0, "LAE": 49.37},
            {"Leq_24h": self.spektrum, "dL": np.full(ANTAL_BAND, 30.0)},
            self.facit,
            self.regler,
            ar_facit=True,
        )
        self.assertIs(resultat.klass, Utfall.GODKAND)
        self.assertEqual([b.storhet for b in resultat.band], ["Leq_24h"])

    def test_saknad_storhet_hindrar_godkant_om_den_inte_valjs_bort(self) -> None:
        argument = ({"LAeq_24h": 40.0}, {"Leq_24h": self.spektrum}, self.facit, self.regler)
        resultat = jamfor_serie(*argument, ar_facit=True)
        self.assertIs(resultat.klass, Utfall.SKA_UTREDAS)
        self.assertEqual(resultat.saknade_storheter, ("LAE",))
        urval = jamfor_serie(*argument, ar_facit=True, kravda_storheter={"LAeq_24h", "Leq_24h"})
        self.assertIs(urval.klass, Utfall.GODKAND)

    def test_dippforskjutning_ger_anmarkning(self) -> None:
        resultat = jamfor_serie(
            {"LAeq_24h": 40.0, "LAE": 49.37},
            {"Leq_24h": spektrum_med_dipp(DIPP + 1)},
            self.facit,
            self.regler,
            ar_facit=True,
        )
        self.assertIs(resultat.klass, Utfall.GODKAND)
        self.assertIn("förskjuten +1 band", " ".join(resultat.anmarkningar))

    def test_okand_totalstorhet_ger_fel(self) -> None:
        facit = Facitserie("s", {}, {"LCeq": 50.0}, {})
        with self.assertRaises(BullerFel):
            jamfor_serie({"LCeq": 50.0}, {}, facit, self.regler, ar_facit=True)

    def test_samsta(self) -> None:
        self.assertIs(samsta([Utfall.GODKAND, Utfall.SKA_UTREDAS]), Utfall.SKA_UTREDAS)
        self.assertIs(samsta([Utfall.UNDERKAND, Utfall.GODKAND]), Utfall.UNDERKAND)
        with self.assertRaises(BullerFel):
            samsta([Utfall.EJ_FACIT])
        with self.assertRaises(BullerFel):
            samsta([])


if __name__ == "__main__":
    unittest.main()
