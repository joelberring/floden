"""Tester för banuppsättning och parberäknare (KB2) med en låtsaskärna."""

from __future__ import annotations

import math
import unittest
from dataclasses import dataclass

import numpy as np

from buller.banor.banfinnare import Banuppsattning, bygg_banuppsattning, hitta_banor
from buller.banor.direkt import par_inom_radie
from buller.scen.bullerscen import Bullerscen
from buller.scen.fasad import fasadpunkter
from buller.scen.kallsegment import kallsegment_fran_vagar
from buller.scen.syntetisk import syntetisk_scen
from buller.typer import (
    ANTAL_BAND,
    BullerFel,
    Byggnad,
    Flyttal,
    Kallsegment,
    Mottagare,
    P2PKarna,
    Terrangprofil,
    Vader,
)


@dataclass
class Latsaskarna:
    """Enkel kärna för test: sfärisk spridning över profilens längd plus påhittade termer.

    Inte akustik: den finns bara för att överföringen ska bero på profil, väder och band.
    """

    namn: str = "latsas"
    version: str = "0"
    nord2000_konform: bool = False

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        avstand = math.hypot(float(profil.x_m[-1]), profil.mottagare_hojd_m - profil.kalla_hojd_m)
        skarm = float(np.max(profil.z_m) - profil.z_m[0])
        band = np.arange(ANTAL_BAND, dtype=np.float64)
        niva: Flyttal = (
            -20.0 * math.log10(avstand) - 11.0 - 0.1 * band - 0.2 * skarm + vader.vind_m_s
        )
        return niva


@dataclass
class TrasigKarna:
    namn: str = "trasig"
    version: str = "0"
    nord2000_konform: bool = False
    nan: bool = False

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        return np.full(ANTAL_BAND, np.nan) if self.nan else np.zeros(3)


def _enkel_scen() -> Bullerscen:
    hus = Byggnad(
        "vagg", np.array([20.0, 80.0, 80.0, 20.0]), np.array([60.0, 60.0, 70.0, 70.0]), 22.0, 3.0
    )
    return Bullerscen(
        terrang_rh2000_m=np.full((100, 100), 10.0),
        markslag=np.full((100, 100), 1, dtype=np.uint8),
        e_min_m=0.0,
        n_max_m=100.0,
        cell_m=1.0,
        byggnader=(hus,),
    )


class TestParberaknare(unittest.TestCase):
    def setUp(self) -> None:
        self.scen = _enkel_scen()
        self.mottagare = [Mottagare(3, 60.0, 50.0, 4.0), Mottagare(4, 60.0, 80.0, 4.0)]
        self.kallor = [
            Kallsegment(7, 30.0, 40.0, 0.5, 10.0, "v"),
            Kallsegment(8, 10.0, 5.0, 0.5, 10.0, "v"),  # för långt bort för alla banor
            Kallsegment(9, 50.0, 40.0, 0.5, 10.0, "v"),
        ]
        self.uppsattning = bygg_banuppsattning(
            self.scen, self.mottagare, self.kallor, maxradie_m=60.0
        )
        self.karna = Latsaskarna()
        self.vader = [Vader(vind_m_s=0.0, vikt=0.7), Vader(vind_m_s=3.0, vikt=0.3)]

    def test_karnan_uppfyller_protokollet(self) -> None:
        self.assertIsInstance(self.karna, P2PKarna)

    def test_form_och_energisumma(self) -> None:
        berakna = self.uppsattning.parberaknare(self.karna, self.vader)
        resultat = berakna(0, [0, 1, 2])
        self.assertEqual(resultat.shape, (3, 2, ANTAL_BAND))
        # Par (mottagare 3, källa 7): direkt bana och en reflex med 3 dB förlust.
        banor = self.uppsattning.banor_for_par(0, 0)
        self.assertEqual([b.typ for b in banor], ["direkt", "reflex"])
        for v, klass in enumerate(self.vader):
            h_direkt = self.karna.overforing_db(banor[0].profil, klass)
            h_reflex = self.karna.overforing_db(banor[1].profil, klass) - 3.0
            vantat = 10.0 * np.log10(10.0 ** (h_direkt / 10.0) + 10.0 ** (h_reflex / 10.0))
            np.testing.assert_allclose(resultat[0, v], vantat, rtol=0, atol=1e-12)
        # Väderklasserna skiljer sig med vinden (3 dB i låtsaskärnan); vikten används inte.
        np.testing.assert_allclose(resultat[0, 1] - resultat[0, 0], 3.0, atol=1e-12)
        # Källa 8 ligger utanför maxradien: ingen bana, ingen energi.
        self.assertTrue(bool(np.all(np.isneginf(resultat[1]))))
        self.assertTrue(bool(np.all(np.isfinite(resultat[2]))))

    def test_bara_direkt_bana_ger_karnans_varde(self) -> None:
        # Mottagare 4 står norr om huset: ingen reflex, och den direkta banan går över taket.
        banor = self.uppsattning.banor_for_par(1, 2)
        self.assertEqual([b.typ for b in banor], ["direkt"])
        self.assertAlmostEqual(float(np.max(banor[0].profil.z_m)), 22.0)
        resultat = self.uppsattning.parberaknare(self.karna, self.vader[:1])(1, [2])
        np.testing.assert_allclose(
            resultat[0, 0], self.karna.overforing_db(banor[0].profil, self.vader[0]), atol=1e-12
        )

    def test_ordning_och_upprepning_av_kallindex(self) -> None:
        berakna = self.uppsattning.parberaknare(self.karna, self.vader)
        a = berakna(0, [2, 0, 2])
        b = berakna(0, [0, 2])
        np.testing.assert_array_equal(a[0], b[1])
        np.testing.assert_array_equal(a[1], b[0])
        np.testing.assert_array_equal(a[2], b[1])
        self.assertEqual(berakna(0, []).shape, (0, 2, ANTAL_BAND))

    def test_fel(self) -> None:
        with self.assertRaisesRegex(BullerFel, "P2PKarna"):
            self.uppsattning.parberaknare(object(), self.vader)  # type: ignore[arg-type]
        with self.assertRaisesRegex(BullerFel, "väderklass"):
            self.uppsattning.parberaknare(self.karna, [])
        berakna = self.uppsattning.parberaknare(self.karna, self.vader)
        for m, k in ((2, [0]), (-1, [0]), (0, [3]), (0, [-1])):
            with self.subTest(m=m, k=k), self.assertRaises(BullerFel):
                berakna(m, k)
        with self.assertRaisesRegex(BullerFel, "form"):
            self.uppsattning.parberaknare(TrasigKarna(), self.vader)(0, [0])
        with self.assertRaisesRegex(BullerFel, "NaN"):
            self.uppsattning.parberaknare(TrasigKarna(nan=True), self.vader)(0, [0])


class TestBanuppsattning(unittest.TestCase):
    def test_fel_i_indata(self) -> None:
        scen = _enkel_scen()
        m = Mottagare(1, 60.0, 50.0, 4.0)
        k = Kallsegment(1, 30.0, 40.0, 0.5, 10.0, "v")
        fall: list[tuple[list[Mottagare], list[Kallsegment], float]] = [
            ([m, m], [k], 50.0),  # dubbla id
            ([m], [k, k], 50.0),
            ([Mottagare(1, 50.0, 65.0, 4.0)], [k], 50.0),  # mottagare i huset
            ([m], [Kallsegment(1, 50.0, 65.0, 0.5, 10.0, "v")], 50.0),  # källa i huset
            ([Mottagare(1, 150.0, 50.0, 4.0)], [k], 50.0),  # utanför rastret
            ([Mottagare(1, 60.0, 50.0, -1.0)], [k], 50.0),
            ([Mottagare(1, float("nan"), 50.0, 4.0)], [k], 50.0),
            ([m], [k], 0.0),
            ([m], [k], float("inf")),
        ]
        for i, (mottagare, kallor, radie) in enumerate(fall):
            with self.subTest(fall=i), self.assertRaises(BullerFel):
                hitta_banor(scen, mottagare, kallor, maxradie_m=radie)

    def test_okant_markslag_stoppar_eller_flaggas(self) -> None:
        scen = _enkel_scen()
        scen.markslag[45:50, :] = 0  # en remsa okänt markslag mellan källa och mottagare
        m = [Mottagare(1, 60.0, 50.5, 4.0), Mottagare(2, 30.0, 30.0, 4.0)]
        k = [Kallsegment(1, 30.0, 40.0, 0.5, 10.0, "v")]
        with self.assertRaisesRegex(BullerFel, "utan impedansklass"):
            hitta_banor(scen, m, k, maxradie_m=80.0, reflexordning=0)
        uppsattning = bygg_banuppsattning(
            scen, m, k, maxradie_m=80.0, reflexordning=0, okand_klass="G"
        )
        self.assertEqual(uppsattning.okant_markslag.tolist(), [True, False])

    def test_tomma_listor(self) -> None:
        scen = _enkel_scen()
        self.assertEqual(hitta_banor(scen, [], [], maxradie_m=50.0), [])
        self.assertEqual(
            hitta_banor(scen, [Mottagare(1, 60.0, 50.0, 4.0)], [], maxradie_m=50.0), []
        )

    def test_bana_maste_hora_till_uppsattningen(self) -> None:
        scen = _enkel_scen()
        m = [Mottagare(1, 60.0, 50.0, 4.0)]
        k = [Kallsegment(1, 30.0, 40.0, 0.5, 10.0, "v")]
        banor = hitta_banor(scen, m, k, maxradie_m=80.0)
        with self.assertRaises(BullerFel):
            Banuppsattning(
                tuple(m), (Kallsegment(2, 30.0, 40.0, 0.5, 10.0, "v"),), tuple(banor),
                np.zeros(len(banor), dtype=np.bool_),
            )  # fmt: skip

    def test_par_inom_radie(self) -> None:
        me, mn = np.array([0.0, 100.0]), np.array([0.0, 0.0])
        ke, kn = np.array([30.0, 0.0, 100.0, 130.0]), np.array([40.0, 50.1, 50.0, 40.0])
        mi, ki = par_inom_radie(me, mn, ke, kn, 50.0)
        self.assertEqual(list(zip(mi.tolist(), ki.tolist(), strict=True)), [(0, 0), (1, 2), (1, 3)])

    def test_determinism_och_ordning(self) -> None:
        scen = syntetisk_scen(sida_m=200.0, cell_m=1.0, tomt_m=50.0, fro=61)
        mottagare = fasadpunkter(
            scen,
            avstand_fran_fasad_m=2.0,
            punktavstand_m=20.0,
            forsta_vaning_hojd_m=2.0,
            vaningshojd_m=3.0,
            max_vaningar=2,
        ).mottagare[::4]
        kallor = kallsegment_fran_vagar(scen.vagar, max_langd_m=25.0, delkallhojder_m=[0.01, 0.75])
        a = bygg_banuppsattning(scen, mottagare, kallor, maxradie_m=120.0)
        b = bygg_banuppsattning(scen, mottagare, kallor, maxradie_m=120.0)
        self.assertGreater(len(a.banor), 1000)
        self.assertEqual(len(a.banor), len(b.banor))
        for x, y in zip(a.banor, b.banor, strict=True):
            self.assertEqual(
                (x.mottagare_id, x.kalla_id, x.typ), (y.mottagare_id, y.kalla_id, y.typ)
            )
            np.testing.assert_array_equal(x.profil.x_m, y.profil.x_m)
            np.testing.assert_array_equal(x.profil.z_m, y.profil.z_m)
            np.testing.assert_array_equal(x.plan_e_m, y.plan_e_m)
        m_index = {m.id: i for i, m in enumerate(mottagare)}
        k_index = {k.id: i for i, k in enumerate(kallor)}
        nycklar = [
            (m_index[x.mottagare_id], k_index[x.kalla_id], x.typ != "direkt") for x in a.banor
        ]
        self.assertEqual(nycklar, sorted(nycklar))
        # Varje profil är lika lång som banans plangeometri, och höjderna kommer från indata.
        for bana in a.banor[::37]:
            plan = float(np.sum(np.hypot(np.diff(bana.plan_e_m), np.diff(bana.plan_n_m))))
            self.assertAlmostEqual(float(bana.profil.x_m[-1]), plan, places=9)
            self.assertLessEqual(plan, 120.0 + 1e-9)
            self.assertEqual(
                bana.profil.mottagare_hojd_m, mottagare[m_index[bana.mottagare_id]].hojd_over_mark_m
            )
            self.assertEqual(
                bana.profil.kalla_hojd_m, kallor[k_index[bana.kalla_id]].hojd_over_mark_m
            )
        karna = Latsaskarna()
        ra = a.parberaknare(karna, [Vader()])(5, list(range(len(kallor))))
        rb = b.parberaknare(karna, [Vader()])(5, list(range(len(kallor))))
        np.testing.assert_array_equal(ra, rb)


if __name__ == "__main__":
    unittest.main()
