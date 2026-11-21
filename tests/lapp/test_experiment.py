"""Enhetstester för grundmodell-rättningar och experimentläge."""

from __future__ import annotations

import math
import unittest

import numpy as np
from numpy.typing import NDArray

from lapp.experiment import (
    DikeAtgard,
    ExperimentAtgarder,
    ExperimentFel,
    GataHojdsattningAtgard,
    GrundmodellRattning,
    HojdRattning,
    MarkJusteringAtgard,
    MarkslagRattning,
    OppnaPassage,
    VallAtgard,
    tillampa_experiment,
    tillampa_grundmodell_rattning,
)
from markmodell.hinder_oppningar import BYGGNAD as MARKSLAG_BYGGNAD
from markmodell.hinder_oppningar import GRAS, HARDGJORT
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo


def _skapa_bas_raster(
    hojd: int = 64,
    bredd: int = 64,
    *,
    bas_z: float = 10.0,
) -> tuple[
    NDArray[np.float32],
    NDArray[np.float32],
    NDArray[np.uint8],
    GeoTransformInfo,
]:
    geo = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150000.0,
        y_max=6580000.0 + hojd * 0.25,
        pixelstorlek_x=0.25,
        pixelstorlek_y=0.25,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    mark_z = np.full((hojd, bredd), bas_z, dtype=np.float32)
    osak_z = np.full((hojd, bredd), 0.03, dtype=np.float32)
    markslag = np.full((hojd, bredd), GRAS, dtype=np.uint8)
    return mark_z, osak_z, markslag, geo


def _cell_en(geo: GeoTransformInfo, rad: int, kol: int) -> tuple[float, float]:
    e = geo.x_min + (kol + 0.5) * float(geo.pixelstorlek_x)
    n = geo.y_max - (rad + 0.5) * float(geo.pixelstorlek_y)
    return (e, n)


class TestGrundmodellRattning(unittest.TestCase):
    """Tester för grundmodell-rättningar (broar, spikar, felklassade byggnader)."""

    def test_oppna_passage_utan_botten_oppnar_vagbank_och_tar_bort_falsk_damm(self) -> None:
        mark_z, osak_z, markslag, geo = _skapa_bas_raster(64, 64, bas_z=12.0)
        # Sluttning från väst (12,0 m) till öst (10,4 m), med låg ytterrand runt om
        kol_idx = np.arange(64, dtype=np.float32)[None, :]
        mark_z[:, :] = 12.0 - 0.025 * kol_idx
        mark_z[0, :] = 9.0
        mark_z[-1, :] = 9.0
        mark_z[:, 0] = 9.0
        mark_z[:, -1] = 9.0
        # Kantramar norr/söder så vattnet uppströms tvingas genom vägbanken
        mark_z[1:8, 1:63] = 14.0
        mark_z[56:63, 1:63] = 14.0

        # Falsk vägbank (1 m hög) vid kol 30..34
        mark_z[8:56, 30:35] = 13.0
        # Falsk byggnadsklassning mitt i vägbanken där bron ska öppnas
        markslag[28:36, 30:35] = np.uint8(MARKSLAG_BYGGNAD)

        bas_kopia = mark_z.copy()
        bas_graf = bygg_sankgraf(mark_z, osak_z, markslag, geo)
        bas_fyll = statisk_fyllning(bas_graf, 0.05)
        self.assertGreater(bas_fyll.lagrat_m3, 1.0)

        start_e, start_n = _cell_en(geo, 32, 26)
        slut_e, slut_n = _cell_en(geo, 32, 38)
        rattning = GrundmodellRattning(
            rattning_id="oppna-bro-1",
            passager=(
                OppnaPassage(
                    passage_id="bro-1",
                    typ="bro",
                    linje=((start_e, start_n), (slut_e, slut_n)),
                    bredd_m=4.0,
                    botten_rh2000=None,
                    aktiv=True,
                    kommentar="Öppna falsk vägbank under bro",
                ),
            ),
        )
        res = tillampa_grundmodell_rattning(mark_z, osak_z, markslag, geo, rattning)
        # Icke-destruktiv
        np.testing.assert_array_equal(mark_z, bas_kopia)
        self.assertGreater(res.antal_andrade_celler, 0)
        self.assertEqual(res.antal_aktiva_rattningar, 1)
        # Byggnad på passagen ändrad till HARDGJORT
        self.assertEqual(int(res.markslag[32, 32]), HARDGJORT)
        # Markhöjd i passagen sänkt under vägbankens 13,0 m
        self.assertLess(float(res.mark_z[32, 32]), 11.6)

        ny_graf = bygg_sankgraf(res.mark_z, res.osakerhet_z, res.markslag, geo)
        ny_fyll = statisk_fyllning(ny_graf, 0.05)
        self.assertLess(ny_fyll.lagrat_m3, 0.1 * bas_fyll.lagrat_m3)

    def test_hojd_rattning_utjamna_fran_rand_och_ta_bort_byggnad(self) -> None:
        mark_z, osak_z, markslag, geo = _skapa_bas_raster(40, 40, bas_z=15.0)
        # Sätt en spik / felklassad byggnad i mitten (rad 16..24, kol 16..24)
        mark_z[16:24, 16:24] = 24.5
        markslag[16:24, 16:24] = np.uint8(MARKSLAG_BYGGNAD)

        e0 = geo.x_min + 16 * 0.25
        e1 = geo.x_min + 24 * 0.25
        n1 = geo.y_max - 16 * 0.25
        n0 = geo.y_max - 24 * 0.25
        poly = ((e0, n0), (e1, n0), (e1, n1), (e0, n1))

        rattning = GrundmodellRattning(
            hojd_rattningar=(
                HojdRattning(
                    rattning_id="rensa-spik",
                    polygon=poly,
                    utjamna_fran_rand=True,
                    ta_bort_byggnad=True,
                ),
            )
        )
        res = tillampa_grundmodell_rattning(mark_z, osak_z, markslag, geo, rattning)
        self.assertTrue(np.allclose(res.mark_z[16:24, 16:24], 15.0, atol=1e-5))
        self.assertTrue(np.all(res.markslag[16:24, 16:24] == HARDGJORT))
        self.assertEqual(res.antal_andrade_celler, 64)

    def test_markslag_rattning_och_inaktiva_rattningar(self) -> None:
        mark_z, osak_z, markslag, geo = _skapa_bas_raster(32, 32, bas_z=10.0)
        poly = (
            (geo.x_min + 2.0, geo.y_max - 6.0),
            (geo.x_min + 6.0, geo.y_max - 6.0),
            (geo.x_min + 6.0, geo.y_max - 2.0),
            (geo.x_min + 2.0, geo.y_max - 2.0),
        )
        inaktiv = GrundmodellRattning(
            passager=(
                OppnaPassage(
                    passage_id="p-off",
                    typ="trumma",
                    linje=((geo.x_min + 1.0, geo.y_max - 4.0), (geo.x_min + 7.0, geo.y_max - 4.0)),
                    botten_rh2000=8.0,
                    aktiv=False,
                ),
            ),
            hojd_rattningar=(
                HojdRattning(rattning_id="h-off", polygon=poly, delta_z_m=-1.0, aktiv=False),
            ),
            markslag_rattningar=(
                MarkslagRattning(
                    rattning_id="m-off",
                    polygon=poly,
                    markslag_kod=HARDGJORT,
                    aktiv=False,
                ),
            ),
        )
        self.assertFalse(inaktiv.har_aktiva())
        res_off = tillampa_grundmodell_rattning(mark_z, osak_z, markslag, geo, inaktiv)
        self.assertEqual(res_off.antal_andrade_celler, 0)
        self.assertEqual(res_off.antal_aktiva_rattningar, 0)

        aktiv_mr = GrundmodellRattning(
            markslag_rattningar=(
                MarkslagRattning(
                    rattning_id="m-on",
                    polygon=poly,
                    markslag_kod=HARDGJORT,
                    aktiv=True,
                ),
            )
        )
        self.assertTrue(aktiv_mr.har_aktiva())
        res_on = tillampa_grundmodell_rattning(mark_z, osak_z, markslag, geo, aktiv_mr)
        self.assertGreater(res_on.antal_andrade_celler, 0)
        self.assertEqual(int(res_on.markslag[16, 16]), HARDGJORT)


class TestExperimentLage(unittest.TestCase):
    """Tester för experimentläge (dike, vall, gata, markjustering och volymer)."""

    def test_dike_sanker_och_ger_positiv_schaktvolym(self) -> None:
        mark_z, osak_z, markslag, geo = _skapa_bas_raster(48, 48, bas_z=12.0)
        bas_kopia = mark_z.copy()
        _, y_mitt = _cell_en(geo, 24, 24)
        atgarder = ExperimentAtgarder(
            experiment_id="exp-dike",
            diken=(
                DikeAtgard(
                    atgard_id="dike-1",
                    linje=((geo.x_min + 2.0, y_mitt), (geo.x_min + 10.0, y_mitt)),
                    botten_bredd_m=1.0,
                    slantslutning=2.0,
                    djup_m=0.50,
                ),
            ),
        )
        res = tillampa_experiment(mark_z, osak_z, markslag, geo, atgarder)
        np.testing.assert_array_equal(mark_z, bas_kopia)
        self.assertGreater(res.schakt_volym_m3, 0.0)
        self.assertEqual(res.fyll_volym_m3, 0.0)
        self.assertGreater(res.antal_andrade_celler, 0)
        # Botten nära mittlinjen ska vara exakt 12.0 - 0.50 = 11.50
        self.assertAlmostEqual(float(res.mark_z[24, 24]), 11.50, places=5)
        # Längs slänten (1,0 m från mittlinje -> 0,5 m utanför bottenhalva): 11.50 + 0.5/2.0 = 11.75
        _, n_slant = _cell_en(geo, 20, 24)
        self.assertAlmostEqual(n_slant, y_mitt + 1.0, places=5)
        self.assertAlmostEqual(float(res.mark_z[20, 24]), 11.75, places=4)

    def test_vall_hojer_ger_fyllvolym_och_stoppar_vattenflode(self) -> None:
        mark_z, osak_z, markslag, geo = _skapa_bas_raster(64, 64, bas_z=10.0)
        # Väster till öster sluttning, låg rand bara på östra/norra/södra ytterkanter förutom
        # en vall som skapar uppdämning väster om vallen
        kol_idx = np.arange(64, dtype=np.float32)[None, :]
        mark_z[:, :] = 12.0 - 0.03 * kol_idx
        mark_z[0, :] = 14.0
        mark_z[-1, :] = 14.0
        mark_z[:, 0] = 14.0
        mark_z[:, -1] = 9.0

        bas_graf = bygg_sankgraf(mark_z, osak_z, markslag, geo)
        bas_fyll = statisk_fyllning(bas_graf, 0.05)

        x_vall = geo.x_min + 36 * 0.25
        atgarder = ExperimentAtgarder(
            experiment_id="exp-vall",
            vallar=(
                VallAtgard(
                    atgard_id="vall-1",
                    linje=((x_vall, geo.y_min), (x_vall, geo.y_max)),
                    kron_bredd_m=1.0,
                    slantslutning=1.5,
                    hojd_over_mark_m=0.80,
                ),
            ),
        )
        res = tillampa_experiment(mark_z, osak_z, markslag, geo, atgarder)
        self.assertGreater(res.fyll_volym_m3, 0.0)
        self.assertEqual(res.schakt_volym_m3, 0.0)
        self.assertGreater(float(res.mark_z[32, 36]), float(mark_z[32, 36]) + 0.7)

        ny_graf = bygg_sankgraf(res.mark_z, res.osakerhet_z, res.markslag, geo)
        ny_fyll = statisk_fyllning(ny_graf, 0.05)
        self.assertGreater(ny_fyll.lagrat_m3, bas_fyll.lagrat_m3 + 5.0)
        # Vatten stannar uppströms vallen, utflöde österut minskar
        self.assertLess(ny_fyll.ut_m3, bas_fyll.ut_m3)

    def test_gata_hojdsattning_sanker_korbana_och_ger_kantsten(self) -> None:
        mark_z, osak_z, markslag, geo = _skapa_bas_raster(48, 48, bas_z=12.0)
        y_mitt = geo.y_max - 24 * 0.25
        atgarder = ExperimentAtgarder(
            experiment_id="exp-gata",
            gator=(
                GataHojdsattningAtgard(
                    atgard_id="gata-1",
                    linje=((geo.x_min + 1.0, y_mitt), (geo.x_min + 11.0, y_mitt)),
                    bredd_m=6.0,
                    delta_z_m=-0.20,
                    tvarfall=0.02,
                    kantsten_hojd_m=0.12,
                ),
            ),
        )
        res = tillampa_experiment(mark_z, osak_z, markslag, geo, atgarder)
        self.assertGreater(res.antal_andrade_celler, 0)
        # Mitt på körbanan: 12.0 - 0.20 - 0.02 * 0.125 = 11.7975
        self.assertLess(float(res.mark_z[24, 24]), 11.81)
        self.assertEqual(int(res.markslag[24, 24]), HARDGJORT)
        # Ytterkant (ca 2,875 m från mittlinjen -> kantsten):
        # z_rannsten = 11.80 - 0.02 * 3.0 = 11.74, + 0.12 = 11.86
        self.assertAlmostEqual(float(res.mark_z[12, 24]), 11.86, places=4)

    def test_mark_justering_och_aktiv_false_hoppas_over(self) -> None:
        mark_z, osak_z, markslag, geo = _skapa_bas_raster(32, 32, bas_z=10.0)
        poly = (
            (geo.x_min + 2.0, geo.y_max - 6.0),
            (geo.x_min + 6.0, geo.y_max - 6.0),
            (geo.x_min + 6.0, geo.y_max - 2.0),
            (geo.x_min + 2.0, geo.y_max - 2.0),
        )
        inaktiv = ExperimentAtgarder(
            diken=(
                DikeAtgard(
                    atgard_id="d-off",
                    linje=((geo.x_min + 1.0, geo.y_max - 4.0), (geo.x_min + 7.0, geo.y_max - 4.0)),
                    aktiv=False,
                ),
            ),
            vallar=(
                VallAtgard(
                    atgard_id="v-off",
                    linje=((geo.x_min + 1.0, geo.y_max - 2.0), (geo.x_min + 7.0, geo.y_max - 2.0)),
                    aktiv=False,
                ),
            ),
            gator=(
                GataHojdsattningAtgard(
                    atgard_id="g-off",
                    linje=((geo.x_min + 1.0, geo.y_max - 5.0), (geo.x_min + 7.0, geo.y_max - 5.0)),
                    delta_z_m=-0.2,
                    aktiv=False,
                ),
            ),
            mark_justeringar=(
                MarkJusteringAtgard(atgard_id="m-off", polygon=poly, delta_z_m=0.5, aktiv=False),
            ),
        )
        self.assertFalse(inaktiv.har_aktiva())
        res_off = tillampa_experiment(mark_z, osak_z, markslag, geo, inaktiv)
        self.assertEqual(res_off.antal_andrade_celler, 0)
        self.assertEqual(res_off.antal_aktiva_atgarder, 0)
        self.assertEqual(res_off.schakt_volym_m3, 0.0)
        self.assertEqual(res_off.fyll_volym_m3, 0.0)

        aktiv = ExperimentAtgarder(
            mark_justeringar=(
                MarkJusteringAtgard(atgard_id="m-on", polygon=poly, z_rh2000=11.5, aktiv=True),
            )
        )
        self.assertTrue(aktiv.har_aktiva())
        res_on = tillampa_experiment(mark_z, osak_z, markslag, geo, aktiv)
        self.assertEqual(res_on.antal_aktiva_atgarder, 1)
        self.assertGreater(res_on.fyll_volym_m3, 0.0)
        self.assertAlmostEqual(float(res_on.mark_z[16, 16]), 11.5, places=5)

    def test_json_rundtur_grundmodell_och_experiment(self) -> None:
        gr = GrundmodellRattning(
            rattning_id="gr-test",
            passager=(
                OppnaPassage(
                    passage_id="p1",
                    typ="bro",
                    linje=((150001.0, 6580005.0), (150010.0, 6580005.0)),
                    bredd_m=5.0,
                    botten_rh2000=None,
                    aktiv=True,
                    kommentar="Testbro",
                ),
            ),
            hojd_rattningar=(
                HojdRattning(
                    rattning_id="h1",
                    polygon=((150002.0, 6580002.0), (150004.0, 6580002.0), (150003.0, 6580004.0)),
                    utjamna_fran_rand=True,
                    ta_bort_byggnad=True,
                    aktiv=False,
                    kommentar="Spik",
                ),
            ),
            markslag_rattningar=(
                MarkslagRattning(
                    rattning_id="m1",
                    polygon=((150005.0, 6580002.0), (150007.0, 6580002.0), (150006.0, 6580004.0)),
                    markslag_kod=HARDGJORT,
                    aktiv=True,
                ),
            ),
            beskrivning="Rundturstest grundmodell",
        )
        gr_data = gr.till_dict()
        gr_igen = GrundmodellRattning.fran_dict(gr_data)
        self.assertEqual(gr_igen, gr)
        self.assertEqual(gr_igen.sha256(), gr.sha256())

        exp = ExperimentAtgarder(
            experiment_id="exp-test",
            diken=(
                DikeAtgard(
                    atgard_id="d1",
                    linje=((150001.0, 6580003.0), (150009.0, 6580003.0)),
                    botten_start_rh2000=10.5,
                    botten_slut_rh2000=10.0,
                    djup_m=None,
                    aktiv=True,
                ),
            ),
            vallar=(
                VallAtgard(
                    atgard_id="v1",
                    linje=((150001.0, 6580007.0), (150009.0, 6580007.0)),
                    kron_rh2000=13.0,
                    hojd_over_mark_m=None,
                    aktiv=False,
                ),
            ),
            gator=(
                GataHojdsattningAtgard(
                    atgard_id="g1",
                    linje=((150002.0, 6580001.0), (150002.0, 6580009.0)),
                    start_rh2000=11.8,
                    slut_rh2000=11.4,
                    aktiv=True,
                ),
            ),
            mark_justeringar=(
                MarkJusteringAtgard(
                    atgard_id="mj1",
                    polygon=((150004.0, 6580004.0), (150006.0, 6580004.0), (150005.0, 6580006.0)),
                    delta_z_m=-0.35,
                    aktiv=True,
                ),
            ),
            beskrivning="Rundturstest experiment",
        )
        exp_data = exp.till_dict()
        exp_igen = ExperimentAtgarder.fran_dict(exp_data)
        self.assertEqual(exp_igen, exp)
        self.assertEqual(exp_igen.sha256(), exp.sha256())
        self.assertFalse(math.isnan(0.0))

    def test_validering_kastar_experimentfel(self) -> None:
        with self.assertRaises(ExperimentFel):
            OppnaPassage(passage_id="p", typ="tunnel", linje=((0.0, 0.0), (1.0, 1.0)))
        with self.assertRaises(ExperimentFel):
            HojdRattning(
                rattning_id="h",
                polygon=((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
                z_rh2000=10.0,
                delta_z_m=1.0,
            )
        with self.assertRaises(ExperimentFel):
            GataHojdsattningAtgard(
                atgard_id="g",
                linje=((0.0, 0.0), (1.0, 0.0)),
                delta_z_m=-0.1,
                start_rh2000=10.0,
                slut_rh2000=10.0,
            )
        with self.assertRaises(ExperimentFel):
            MarkJusteringAtgard(
                atgard_id="m",
                polygon=((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
            )


if __name__ == "__main__":
    unittest.main()
