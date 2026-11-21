"""Hela kedjan: scen → banor → kärna → operatorlager → körning (KB1–KB4).

Emissionen är syntetisk och kärnan är baslinjen (inte Nord2000). Testet prövar att delarna
hänger ihop och att den garanterade felgränsen håller mot en oklustrad summa.
"""

from __future__ import annotations

import unittest

import numpy as np

from buller.akustik import BaslinjeKarna
from buller.akustik.band import a_vagning_db
from buller.banor.banfinnare import bygg_banuppsattning
from buller.lagen.korning import kor_buller
from buller.lagen.profil import las_inbyggd_profil
from buller.scen.fasad import fasadpunkter
from buller.scen.kallsegment import kallsegment_fran_vagar
from buller.scen.syntetisk import syntetisk_scen
from buller.typer import ANTAL_BAND, BullerFel, Vader


class TestHelkedja(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scen = syntetisk_scen(sida_m=150.0, cell_m=1.0, fro=3)
        punkter = fasadpunkter(
            scen,
            avstand_fran_fasad_m=2.0,
            punktavstand_m=10.0,
            forsta_vaning_hojd_m=2.0,
            vaningshojd_m=3.0,
            max_vaningar=1,
        )
        cls.mottagare = list(punkter.mottagare)[::9][:6]
        cls.kallor = kallsegment_fran_vagar(
            scen.vagar, max_langd_m=10.0, delkallhojder_m=(0.3,)
        )
        cls.karna = BaslinjeKarna()
        cls.vader = (Vader(),)
        cls.banor = bygg_banuppsattning(scen, cls.mottagare, cls.kallor, maxradie_m=400.0)
        cls.parberaknare = staticmethod(cls.banor.parberaknare(cls.karna, cls.vader))
        cls.lw_db = np.full((len(cls.kallor), ANTAL_BAND), 90.0)  # syntetisk emission

    def _kor(self, lage: str):  # type: ignore[no-untyped-def]
        return kor_buller(
            mottagare=self.mottagare,
            kallor=self.kallor,
            lw_db=self.lw_db,
            vaderklasser=self.vader,
            parberaknare=self.parberaknare,
            ovre_grans=self.banor.ovre_grans_par(self.karna.ovre_grans_db),
            a_vagning_db=a_vagning_db(),
            karna=self.karna,
            profil=las_inbyggd_profil(lage),  # type: ignore[arg-type]
            reflexordning=las_inbyggd_profil(lage).reflexordning,  # type: ignore[arg-type]
            antal_banor=len(self.banor.banor),
        )

    def test_skiss_haller_garanterad_felgrans(self) -> None:
        korning = self._kor("skiss")
        resultat = korning.resultat
        alla = list(range(len(self.kallor)))
        for m in range(len(self.mottagare)):
            h_db = self.parberaknare(m, alla)[:, 0, :]
            with np.errstate(divide="ignore"):
                energi = np.sum(10.0 ** ((self.lw_db + h_db + a_vagning_db()) / 10.0))
                sann_db = 10.0 * np.log10(energi)
            fel_db = sann_db - resultat.niva_a_db[m]
            self.assertGreaterEqual(fel_db, -1e-9)
            self.assertLessEqual(fel_db, resultat.felgrans_db[m] + 1e-9)
        self.assertEqual(resultat.markning, "Skiss – ej för beslut")
        self.assertFalse(korning.manifest.parametrar["karna"]["nord2000_konform"])

    def test_mottagare_inom_kallkluster_ger_inget_fel(self) -> None:
        # Fasadpunkterna ligger inom källornas utbredning: minsta avstånd till trädets rot är 0.
        self.assertTrue(np.all(np.isfinite(self._kor("skiss").resultat.niva_a_db)))

    def test_pargrans_tar_hansyn_till_reflexer(self) -> None:
        antal = self.banor.storsta_antal_banor_per_par()
        self.assertGreaterEqual(antal, 2)
        enskild = self.karna.ovre_grans_db(50.0)
        par = self.banor.ovre_grans_par(self.karna.ovre_grans_db)(50.0)
        np.testing.assert_allclose(par - enskild, 10.0 * np.log10(antal))

    def test_rapportlaget_vagrar_baslinjekarnan(self) -> None:
        with self.assertRaises(BullerFel):
            self._kor("rapport")


if __name__ == "__main__":
    unittest.main()
