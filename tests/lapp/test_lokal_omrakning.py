"""Tester för inkrementell / lokal omräkning av sänkgraf (T04, Q4)."""

from __future__ import annotations

import unittest

import numpy as np
from numpy.typing import NDArray

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from lapp.kontrakt import HojdYta, LappByggnad, LappMagasin, ProjektLapp
from lapp.lokal_omrakning import hitta_berorda_noder, uppdatera_sankgraf_lokalt
from lapp.tillampa import tillampa_lapp
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo


def _skapa_tva_delavrinningsomraden() -> tuple[
    NDArray[np.float32],
    NDArray[np.float32],
    NDArray[np.uint8],
    GeoTransformInfo,
]:
    """Skapar 64x96 (16 m x 24 m) terräng med två separata delavrinningsområden (väst + öst).

    - Väst (kol 0..43): en fristående djup sänka omgiven av hög rygg och randutlopp mot väst.
    - Mitt (kol 44..51): vattendelare med rygg som dränerar ut vid nord/syd-rand (HAV).
    - Öst (kol 52..95): två kaskadkopplade sänkor (övre öst -> nedre öst -> rand).
    """
    hojd, bredd = 64, 96
    geo = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150000.0,
        y_max=6580016.0,
        pixelstorlek_x=0.25,
        pixelstorlek_y=0.25,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    z = np.full((hojd, bredd), 14.0, dtype=np.float32)

    # Nord-, syd-, väst- och östränder dränerar fritt (lägre ytterrand)
    z[0, :] = 12.5
    z[-1, :] = 12.5
    z[:, 0] = 12.5
    z[:, -1] = 12.5

    # Vattendelare i mitten (kol 44..51): rygg på 16,0 m som sluttar ut mot nord/syd-randen
    for r in range(1, hojd - 1):
        dist_rand = min(r, hojd - 1 - r)
        z[r, 44:52] = np.float32(15.0 + 0.04 * dist_rand)

    # Västra delavrinningsområdet: sänka (rad 16..48, kol 10..34) med botten 11,0 m och tröskel 13,0
    z[16:48, 10:34] = 11.0
    z[20:44, 14:30] = 10.5
    # Tröskel ut mot västra randen (rad 30..34, kol 1..10) på 13,0 m
    z[30:34, 1:10] = 13.0

    # Östra delavrinningsområdet:
    # Övre öst (rad 8..28, kol 58..84): botten 12,0 m
    z[8:28, 58:84] = 12.0
    # Nedre öst (rad 36..56, kol 58..84): botten 10,0 m
    z[36:56, 58:84] = 10.0
    # Överfallssadel mellan övre och nedre öst (rad 28..36, kol 68..74): 13,2 m
    z[28:36, 68:74] = 13.2
    # Utlopp från nedre öst mot östranden (rad 44..48, kol 84..95): 12,8 m
    z[44:48, 84:95] = 12.8

    osak = np.full((hojd, bredd), 0.03, dtype=np.float32)
    slag = np.full((hojd, bredd), 4, dtype=np.uint8)
    return z, osak, slag, geo


class TestLokalOmrakning(unittest.TestCase):
    """Verifierar exakt ekvivalens mellan lokal och full sänkgrafomräkning."""

    def test_lokal_omrakning_ateranvander_vast_och_matchar_full_exakt(self) -> None:
        """Lapp i östra kaskaden ska återanvända väst och ge max_abs_diff < 1e-6 m mot full."""
        bas_z, bas_osak, bas_slag, geo = _skapa_tva_delavrinningsomraden()
        bas_graf = bygg_sankgraf(bas_z, bas_osak, bas_slag, geo)

        # Identifiera västra lövnoden (mitt i västra sänkan: rad 32, kol 22)
        vast_nod = int(bas_graf.cell_till_nod[32, 22])
        ovre_ost_nod = int(bas_graf.cell_till_nod[18, 70])
        nedre_ost_nod = int(bas_graf.cell_till_nod[46, 70])
        self.assertGreaterEqual(vast_nod, 0)
        self.assertGreaterEqual(ovre_ost_nod, 0)
        self.assertGreaterEqual(nedre_ost_nod, 0)
        self.assertNotEqual(vast_nod, ovre_ost_nod)
        self.assertNotEqual(vast_nod, nedre_ost_nod)

        # Projektlapp ändrar ENDAST övre östra sänkan (delvis uppfyllnad + ny byggnad)
        # E för kol 60..76 -> 150015.0 .. 150019.0, N för rad 10..24 -> y_max - 6.0 .. y_max - 2.5
        lapp = ProjektLapp(
            lapp_id="ost-projekt",
            bas_recept=("a" * 64,),
            hojdytor=(
                HojdYta(
                    yta_id="fyllnad-ovre-ost",
                    polygon=(
                        (150015.0, geo.y_max - 6.0),
                        (150019.0, geo.y_max - 6.0),
                        (150019.0, geo.y_max - 2.5),
                        (150015.0, geo.y_max - 2.5),
                    ),
                    hojd_rh2000=13.5,
                ),
            ),
            byggnader=(
                LappByggnad(
                    byggnad_id="ost-hus",
                    polygon=(
                        (150016.0, geo.y_max - 5.0),
                        (150018.0, geo.y_max - 5.0),
                        (150018.0, geo.y_max - 3.5),
                        (150016.0, geo.y_max - 3.5),
                    ),
                    entre_rh2000=13.8,
                    entre_punkt=(150015.5, geo.y_max - 4.25),
                ),
            ),
        )
        till = tillampa_lapp(
            bas_z, bas_osak, bas_slag, geo, lapp, bas_recept_sha256="a" * 64
        )

        # 1. Kontrollera berörda noder: fångar övre öst + nedströms nedre öst, EJ väst!
        berorda = hitta_berorda_noder(bas_graf, till.andrad_mask)
        self.assertIn(ovre_ost_nod, berorda)
        self.assertIn(nedre_ost_nod, berorda)
        self.assertNotIn(vast_nod, berorda)

        # 2. Kör lokal omräkning vs full omräkning
        lokal_res = uppdatera_sankgraf_lokalt(
            bas_graf, till.mark_z, till.osakerhet_z, till.markslag, till.andrad_mask
        )
        full_graf = bygg_sankgraf(till.mark_z, till.osakerhet_z, till.markslag, geo)

        self.assertGreaterEqual(lokal_res.antal_ateranvanda_noder, 1)
        self.assertIn(vast_nod, lokal_res.ny_graf.noder)
        self.assertLess(lokal_res.antal_omraknade_celler, bas_z.size)

        # 3. Verifiera statisk fyllning: exakt identiskt djup och massbalans
        for regn_m in (0.15, 0.80, 2.50):
            stat_lokal = statisk_fyllning(lokal_res.ny_graf, regn_m)
            stat_full = statisk_fyllning(full_graf, regn_m)
            max_abs_diff = float(np.max(np.abs(stat_lokal.djup_m - stat_full.djup_m)))
            self.assertLess(max_abs_diff, 1e-6)
            self.assertAlmostEqual(stat_lokal.in_m3, stat_full.in_m3, places=6)
            self.assertAlmostEqual(stat_lokal.lagrat_m3, stat_full.lagrat_m3, places=5)
            self.assertAlmostEqual(stat_lokal.ut_m3, stat_full.ut_m3, places=5)

        # 4. Verifiera händelsestyrd dynamik: exakt identiskt maxdjup och ankomsttider
        regn_t = [0.0, 1800.0, 3600.0]
        regn_i = [250.0, 400.0, 0.0]
        dyn_lokal = kor_dynamik_handelsestyrd(lokal_res.ny_graf, regn_t, regn_i)
        dyn_full = kor_dynamik_handelsestyrd(full_graf, regn_t, regn_i)

        max_dyn_diff = float(np.max(np.abs(dyn_lokal.max_djup_m - dyn_full.max_djup_m)))
        self.assertLess(max_dyn_diff, 1e-6)
        self.assertAlmostEqual(dyn_lokal.in_m3, dyn_full.in_m3, places=6)
        self.assertAlmostEqual(dyn_lokal.lagrat_m3, dyn_full.lagrat_m3, places=5)
        self.assertAlmostEqual(dyn_lokal.ut_m3, dyn_full.ut_m3, places=5)

        # Ankomsttider (NaN på exakt samma celler och lika värden där ändliga)
        fin_lokal = np.isfinite(dyn_lokal.tid_till_0_1m_s)
        fin_full = np.isfinite(dyn_full.tid_till_0_1m_s)
        np.testing.assert_array_equal(fin_lokal, fin_full)
        if np.any(fin_lokal):
            tid_diff = float(
                np.max(
                    np.abs(
                        dyn_lokal.tid_till_0_1m_s[fin_lokal] - dyn_full.tid_till_0_1m_s[fin_full]
                    )
                )
            )
            self.assertLess(tid_diff, 1e-3)

    def test_fixpunktsexpansion_vid_genombrott_mellan_omraden(self) -> None:
        """Om en lapp gräver igenom vattendelaren ska fixpunktsexpansion ändå matcha full."""
        bas_z, bas_osak, bas_slag, geo = _skapa_tva_delavrinningsomraden()
        bas_graf = bygg_sankgraf(bas_z, bas_osak, bas_slag, geo)

        # Gräv ett djupt dike genom vattendelaren (kol 40..60) så väst och öst kopplas ihop
        lapp = ProjektLapp(
            lapp_id="genombrott",
            bas_recept=("b" * 64,),
            magasin=(
                LappMagasin(
                    magasin_id="kanal",
                    polygon=(
                        (150010.0, geo.y_max - 12.0),
                        (150015.0, geo.y_max - 12.0),
                        (150015.0, geo.y_max - 10.0),
                        (150010.0, geo.y_max - 10.0),
                    ),
                    volym_m3=0.0,
                    botten_rh2000=11.5,
                ),
            ),
        )
        till = tillampa_lapp(
            bas_z, bas_osak, bas_slag, geo, lapp, bas_recept_sha256="b" * 64
        )
        lokal_res = uppdatera_sankgraf_lokalt(
            bas_graf, till.mark_z, till.osakerhet_z, till.markslag, till.andrad_mask
        )
        full_graf = bygg_sankgraf(till.mark_z, till.osakerhet_z, till.markslag, geo)

        stat_lokal = statisk_fyllning(lokal_res.ny_graf, 1.5)
        stat_full = statisk_fyllning(full_graf, 1.5)
        self.assertLess(float(np.max(np.abs(stat_lokal.djup_m - stat_full.djup_m))), 1e-6)

    def test_tom_andrad_mask_ateranvander_allt(self) -> None:
        """Utan ändrade celler ska noll celler räknas om och alla noder återanvändas."""
        bas_z, bas_osak, bas_slag, geo = _skapa_tva_delavrinningsomraden()
        bas_graf = bygg_sankgraf(bas_z, bas_osak, bas_slag, geo)
        tom_mask = np.zeros(bas_z.shape, dtype=bool)

        res = uppdatera_sankgraf_lokalt(bas_graf, bas_z, bas_osak, bas_slag, tom_mask)
        self.assertEqual(res.antal_omraknade_celler, 0)
        self.assertEqual(res.antal_ateranvanda_noder, len(bas_graf.noder))
        self.assertEqual(res.berorda_bas_noder, set())


if __name__ == "__main__":
    unittest.main()
