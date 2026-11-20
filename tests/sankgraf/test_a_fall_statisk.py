"""Validering av sänkgraf och statisk fyllning mot testfallen A1–A9 (T02)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from sankgraf import (
    BYGGNAD_NOD_ID,
    berakna_flodesriktning_deg,
    bygg_sankgraf,
    filtrera_persistens,
    kor_statisk_k1_mapp,
    las_oppningar_fran_gpkg,
    statisk_fyllning,
)
from testsvit.k1_io import GeoTransformInfo, las_geotiff

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_ROT = REPO_ROT / "testsvit"


def _las_facit(fall_mapp: str) -> dict[str, Any]:
    fil = TESTSVIT_ROT / fall_mapp / "facit" / "facit.json"
    data: Any = json.loads(fil.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


class TestSyntetiskaAFallStatisk(unittest.TestCase):
    """Kör verklig T02-fysik mot samtliga syntetiska fall A1–A9."""

    def test_a1_enkel_sanka(self) -> None:
        facit = _las_facit("A1_enkel_sanka")
        indata = TESTSVIT_ROT / "A1_enkel_sanka" / "indata"

        graf, res_del = kor_statisk_k1_mapp(indata)
        self.assertEqual(len(graf.noder), 1)
        self.assertLess(res_del.volymfel_rel, 0.001)

        nod = graf.lovnoder()[0]
        self.assertAlmostEqual(nod.botten_rh2000, float(facit["botten_rh2000"]), places=3)
        self.assertAlmostEqual(nod.overfall_rh2000, float(facit["overfall_rh2000"]), places=3)
        self.assertAlmostEqual(nod.max_volym_m3, float(facit["slutvolym_m3"]), delta=0.05)

        # Fullfyllning bortom t_overfall_s -> slutvolym och maxdjup enligt facit
        res_full = statisk_fyllning(graf, 0.30)
        self.assertLess(res_full.volymfel_rel, 0.001)
        vol_rel_fel = abs(res_full.lagrat_m3 - float(facit["slutvolym_m3"])) / float(
            facit["slutvolym_m3"]
        )
        self.assertLess(vol_rel_fel, 0.001)
        max_djup_fel = abs(float(np.max(res_full.djup_m)) - float(facit["max_djup_m"]))
        self.assertLess(max_djup_fel, 0.01)

        # Kontrollera att volymkurva <-> niva_vid_volym är exakt invers
        for z_test in (10.0, 10.123, 10.37, 10.5):
            v_test = nod.volym_vid_niva(z_test)
            self.assertAlmostEqual(nod.niva_vid_volym(v_test), z_test, places=6)

    def test_a2_tva_sankor_serie(self) -> None:
        facit = _las_facit("A2_tva_sankor_serie")
        indata = TESTSVIT_ROT / "A2_tva_sankor_serie" / "indata"

        graf, res_del = kor_statisk_k1_mapp(indata)
        self.assertLess(res_del.volymfel_rel, 0.001)
        self.assertEqual(len(graf.noder), 2)
        self.assertEqual(len(graf.lovnoder()), 2)
        self.assertEqual(len(graf.rotnoder()), 2)

        lov = sorted(graf.lovnoder(), key=lambda n: n.botten_rh2000)
        nedre, ovre = lov[0], lov[1]

        # Övre sänkan ska rinna ned i nedre sänkan utan sammanslagning
        self.assertIsNone(ovre.forald_id)
        self.assertEqual(ovre.ut_till_id, nedre.nod_id)
        self.assertIsNone(nedre.forald_id)
        self.assertIsNone(nedre.ut_till_id)

        # Fullfyllning av båda sänkorna
        res_full = statisk_fyllning(graf, 0.20)
        self.assertLess(res_full.volymfel_rel, 0.001)

        ovre_facit = facit["ovre"]
        nedre_facit = facit["nedre"]
        self.assertLess(
            abs(res_full.nod_niva_rh2000[ovre.nod_id] - float(ovre_facit["overfall_rh2000"])),
            0.01,
        )
        self.assertLess(
            abs(res_full.nod_niva_rh2000[nedre.nod_id] - float(nedre_facit["overfall_rh2000"])),
            0.01,
        )
        self.assertAlmostEqual(
            res_full.nod_volym_m3[ovre.nod_id],
            float(ovre_facit["slutvolym_m3"]),
            delta=0.01 * float(ovre_facit["slutvolym_m3"]),
        )
        self.assertAlmostEqual(
            res_full.nod_volym_m3[nedre.nod_id],
            float(nedre_facit["slutvolym_m3"]),
            delta=0.01 * float(nedre_facit["slutvolym_m3"]),
        )

    def test_a3_tva_sankor_sammanslagning(self) -> None:
        facit = _las_facit("A3_tva_sankor_sammanslagning")
        indata = TESTSVIT_ROT / "A3_tva_sankor_sammanslagning" / "indata"

        graf, res_regn = kor_statisk_k1_mapp(indata)
        self.assertLess(res_regn.volymfel_rel, 0.001)
        self.assertEqual(len(graf.lovnoder()), 2)
        self.assertEqual(len(graf.rotnoder()), 1)

        rot = graf.rotnoder()[0]
        self.assertEqual(len(rot.barn_id), 2)
        self.assertAlmostEqual(rot.overfall_rh2000, float(facit["gemensam_slutniva_rh2000"]), 3)
        for barn_id in rot.barn_id:
            barn = graf.noder[barn_id]
            self.assertEqual(barn.forald_id, rot.nod_id)
            self.assertAlmostEqual(barn.overfall_rh2000, float(facit["inre_sadel_rh2000"]), 3)

        # Redan vid regn.csv (3600 s > t_sammanslagning_s) har sänkorna gemensam vattennivå
        b0, b1 = rot.barn_id
        self.assertAlmostEqual(
            res_regn.nod_niva_rh2000[b0], res_regn.nod_niva_rh2000[b1], places=6
        )
        self.assertGreater(res_regn.nod_niva_rh2000[rot.nod_id], float(facit["inre_sadel_rh2000"]))

        # Fullfyllning upp till yttre överfall
        res_full = statisk_fyllning(graf, 0.30)
        self.assertLess(res_full.volymfel_rel, 0.001)
        self.assertLess(
            abs(res_full.nod_niva_rh2000[rot.nod_id] - float(facit["gemensam_slutniva_rh2000"])),
            0.01,
        )
        vol_fel = abs(res_full.lagrat_m3 - float(facit["slutvolym_m3"])) / float(
            facit["slutvolym_m3"]
        )
        self.assertLess(vol_fel, 0.001)
        self.assertLess(abs(float(np.max(res_full.djup_m)) - float(facit["max_djup_m"])), 0.01)

    def test_a4_lutande_plan(self) -> None:
        facit = _las_facit("A4_lutande_plan")
        indata = TESTSVIT_ROT / "A4_lutande_plan" / "indata"

        graf, res = kor_statisk_k1_mapp(indata)
        self.assertEqual(len(graf.noder), 0)
        self.assertLess(res.volymfel_rel, 0.001)
        self.assertEqual(res.lagrat_m3, 0.0)
        self.assertEqual(float(np.max(res.djup_m)), 0.0)
        self.assertGreater(len(graf.flodesvagar), 0)

        langsta = max(graf.flodesvagar, key=lambda v: v.langd_m)
        self.assertAlmostEqual(langsta.langd_m, float(facit["flodeslangd_m"]), places=3)
        self.assertAlmostEqual(
            berakna_flodesriktning_deg(langsta),
            float(facit["flodesriktning_deg"]),
            places=2,
        )
        tid_rel_fel = abs(langsta.gangtid_s - float(facit["gangtid_s"])) / float(
            facit["gangtid_s"]
        )
        self.assertLess(tid_rel_fel, 0.01)

    def test_a5_gata_kantsten(self) -> None:
        facit = _las_facit("A5_gata_kantsten")
        indata = TESTSVIT_ROT / "A5_gata_kantsten" / "indata"

        # Måttligt regn (regn.csv = 7,5 mm): allt stannar i rännstenen, noll på södra gården
        graf, res_mod = kor_statisk_k1_mapp(indata)
        self.assertLess(res_mod.volymfel_rel, 0.001)
        self.assertEqual(float(np.max(res_mod.djup_m[44:, :])), 0.0)
        self.assertGreater(float(np.max(res_mod.djup_m[16:44, :])), 0.0)
        self.assertLess(float(np.max(res_mod.djup_m[16:44, :])), float(facit["kantsten_hojd_m"]))
        self.assertEqual(len(res_mod.utflodes_celler), 0)

        # Stort regn: vattnet fyller rännstenen upp till 12 cm kantsten och svämmar över
        res_stor = statisk_fyllning(graf, 0.20)
        self.assertLess(res_stor.volymfel_rel, 0.001)
        self.assertAlmostEqual(
            float(np.max(res_stor.djup_m[16:44, :])),
            float(facit["kantsten_hojd_m"]),
            places=3,
        )
        self.assertGreater(len(res_stor.utflodes_celler), 0)

    def test_a6_sankt_kantsten_infart(self) -> None:
        facit = _las_facit("A6_sankt_kantsten_infart")
        indata = TESTSVIT_ROT / "A6_sankt_kantsten_infart" / "indata"

        graf, res = kor_statisk_k1_mapp(indata)
        self.assertLess(res.volymfel_rel, 0.001)
        self.assertGreater(len(res.utflodes_celler), 0)

        x0_facit, x1_facit = float(facit["infart_x_intervall"][0]), float(
            facit["infart_x_intervall"][1]
        )
        for rad, kol in res.utflodes_celler:
            cell_x = graf.geo.x_min + (kol + 0.5) * graf.geo.pixelstorlek_x
            self.assertGreaterEqual(cell_x, x0_facit)
            self.assertLessEqual(cell_x, x1_facit)
            self.assertEqual(rad, 43)

        # Kontrollera att södra rännstenens överfallsnivå matchar sänkt kantsten (10.02 m)
        overfall_nivaer = [n.overfall_rh2000 for n in graf.rotnoder() if n.ut_till_id is None]
        self.assertTrue(
            any(abs(z - float(facit["utlopp_rh2000"])) < 0.005 for z in overfall_nivaer)
        )

    def test_a7_vagbank_trumma(self) -> None:
        facit = _las_facit("A7_vagbank_trumma")
        indata = TESTSVIT_ROT / "A7_vagbank_trumma" / "indata"

        # Med trumma: ingen falsk damm och vattennivån vid truminloppet ligger på bottennivå
        graf_med, res_med = kor_statisk_k1_mapp(indata, applicera_oppningar=True)
        self.assertLess(res_med.volymfel_rel, 0.001)
        self.assertEqual(len(graf_med.noder), 0)
        self.assertEqual(float(np.max(res_med.djup_m)), 0.0)
        inlopps_wse = float(res_med.vattenniva_rh2000[29, 43])
        self.assertLessEqual(
            inlopps_wse,
            float(facit["med_trumma"]["max_uppstroms_vattenniva_rh2000"]),
        )

        # Kontroll utan trumma: stor falsk damm uppströms vägbanken
        graf_utan, res_utan = kor_statisk_k1_mapp(indata, applicera_oppningar=False)
        self.assertLess(res_utan.volymfel_rel, 0.001)
        self.assertGreater(len(graf_utan.noder), 0)
        self.assertGreater(res_utan.lagrat_m3, 3.0)
        self.assertGreater(float(np.max(res_utan.djup_m[:, :44])), 0.30)

    def test_a8_port_byggnad(self) -> None:
        indata = TESTSVIT_ROT / "A8_port_byggnad" / "indata"

        # Med port: vatten passerar genom byggnadens port utan falsk uppdämning
        graf_med, res_med = kor_statisk_k1_mapp(indata, applicera_oppningar=True)
        self.assertLess(res_med.volymfel_rel, 0.001)
        self.assertEqual(float(np.max(res_med.djup_m)), 0.0)
        # Porten (rader ~24..36, kolumner 40..56) är öppen och aktiv (inte byggnadshinder)
        port_aktiva = int(np.count_nonzero(graf_med.cell_till_nod[:, 40:56] != BYGGNAD_NOD_ID))
        self.assertGreater(port_aktiva, 100)
        # Minst en flödesväg passerar från västsidan genom byggnaden till östsidan
        x_bygg_v = graf_med.geo.x_min + 40 * graf_med.geo.pixelstorlek_x
        x_bygg_o = graf_med.geo.x_min + 56 * graf_med.geo.pixelstorlek_x
        korsar_port = any(
            v.koordinater[0][0] < x_bygg_v and v.koordinater[-1][0] > x_bygg_o
            for v in graf_med.flodesvagar
        )
        self.assertTrue(korsar_port)

        # Utan port: hela byggnadsbandet är spärrat och inga flödesvägar korsar byggnaden
        graf_utan, res_utan = kor_statisk_k1_mapp(indata, applicera_oppningar=False)
        self.assertLess(res_utan.volymfel_rel, 0.001)
        self.assertEqual(
            int(np.count_nonzero(graf_utan.cell_till_nod[:, 40:56] != BYGGNAD_NOD_ID)), 0
        )

        # Sluten innergårdsvariant av A8: utan port däms vatten upp bakom byggnaden,
        # medan porten dränerar innergården helt.
        mark_raw, geo = las_geotiff(indata / "mark.tif")
        osak_raw, _ = las_geotiff(indata / "osakerhet.tif")
        slag_raw, _ = las_geotiff(indata / "markslag.tif")
        mark_sluten = np.asarray(mark_raw, dtype=np.float32).copy()
        osak = np.asarray(osak_raw, dtype=np.float32)
        slag = np.asarray(slag_raw, dtype=np.uint8)
        mark_sluten[0, :40] = np.float32(11.30)
        mark_sluten[-1, :40] = np.float32(11.30)
        oppningar = las_oppningar_fran_gpkg(indata / "oppningar.gpkg")

        g_sluten_utan = bygg_sankgraf(mark_sluten, osak, slag, geo)
        r_sluten_utan = statisk_fyllning(g_sluten_utan, 0.25)
        self.assertGreater(float(np.max(r_sluten_utan.djup_m[:, :40])), 0.30)

        g_sluten_med = bygg_sankgraf(mark_sluten, osak, slag, geo, oppningar=oppningar)
        r_sluten_med = statisk_fyllning(g_sluten_med, 0.25)
        self.assertEqual(float(np.max(r_sluten_med.djup_m[:, :40])), 0.0)

    def test_a9_brus_persistens(self) -> None:
        facit = _las_facit("A9_brus_persistens")
        indata = TESTSVIT_ROT / "A9_brus_persistens" / "indata"

        graf_ofilt, res_ofilt = kor_statisk_k1_mapp(indata)
        self.assertLess(res_ofilt.volymfel_rel, 0.001)
        antal_ofilt_fore = len(graf_ofilt.noder)
        self.assertGreater(len(graf_ofilt.lovnoder()), 5)

        graf_filt = filtrera_persistens(graf_ofilt, faktor=2.0)
        # Ursprunglig ofiltrerad graf får inte muteras
        self.assertEqual(len(graf_ofilt.noder), antal_ofilt_fore)
        self.assertFalse(graf_ofilt.filtrerad)
        self.assertTrue(graf_filt.filtrerad)
        self.assertEqual(graf_filt.persistens_faktor, 2.0)

        # Efter persistensfiltrering återstår exakt 1 verklig sänka
        self.assertEqual(len(graf_filt.noder), int(facit["antal_verkliga_sankor"]))
        verklig = graf_filt.lovnoder()[0]
        self.assertAlmostEqual(
            verklig.botten_rh2000,
            float(facit["verklig_sanka_botten_rh2000"]),
            places=3,
        )

        # Jämför maxdjup mot brusfri referens av samma A9-terräng (< 1 cm fel)
        hojd, bredd = 80, 80
        ii, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
        mark_brusfri = (
            10.80 - 0.0075 * jj - 0.50 * np.exp(-(((ii - 40.0) ** 2 + (jj - 40.0) ** 2) / 64.0))
        ).astype(np.float32)
        geo_ref = GeoTransformInfo(
            bredd=bredd,
            hojd=hojd,
            x_min=graf_filt.geo.x_min,
            y_max=graf_filt.geo.y_max,
            pixelstorlek_x=0.25,
            pixelstorlek_y=0.25,
            epsg=3011,
            dtype="float32",
        )
        g_ref = bygg_sankgraf(
            mark_brusfri,
            np.full((hojd, bredd), 0.03, dtype=np.float32),
            np.full((hojd, bredd), 7, dtype=np.uint8),
            geo_ref,
        )
        r_ref = statisk_fyllning(g_ref, 0.25)
        r_filt = statisk_fyllning(graf_filt, 0.25)
        self.assertLess(r_filt.volymfel_rel, 0.001)
        self.assertLess(
            abs(float(np.max(r_filt.djup_m)) - float(np.max(r_ref.djup_m))),
            0.01,
        )

    def test_rumsligt_regn_och_nollregn(self) -> None:
        indata = TESTSVIT_ROT / "A1_enkel_sanka" / "indata"
        graf, _ = kor_statisk_k1_mapp(indata)

        res_noll = statisk_fyllning(graf, 0.0)
        self.assertEqual(res_noll.in_m3, 0.0)
        self.assertEqual(res_noll.lagrat_m3, 0.0)
        self.assertEqual(res_noll.volymfel_rel, 0.0)

        regn_2d = np.full(graf.mark_z.shape, 0.05, dtype=np.float64)
        regn_2d[10:50, 10:50] = 0.20
        res_2d = statisk_fyllning(graf, regn_2d)
        self.assertLess(res_2d.volymfel_rel, 0.001)
        self.assertGreater(res_2d.lagrat_m3, 20.0)


if __name__ == "__main__":
    unittest.main()
