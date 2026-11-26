"""Enhetstester för lokal tröghet (`lokal_troghet.py`), hybrid (`hybrid.py`) och K6-export (T05)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from gemensamt.manifest import las_manifest
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import (
    EPSG_SWEREF99_1800,
    PIXELSTORLEK_M,
    GeoTransformInfo,
    las_geotiff,
    las_regn_csv,
)
from tvad.hybrid import jamfor_hybrid_mot_full2d, kor_hybrid_2d_graf
from tvad.lokal_troghet import kor_2d_lokal_troghet, skriv_tvad_resultat_k6

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_ROT = REPO_ROT / "testsvit"


def _skapa_kombinerad_sluttning_och_sanka() -> tuple[
    NDArray[np.float32],
    NDArray[np.uint8],
    GeoTransformInfo,
]:
    """Bygger syntetisk terräng (48×80 celler = 12×20 m): lutande plan + djup sänka + utlopp."""
    hojd, bredd = 48, 80
    dx = PIXELSTORLEK_M
    geo = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150500.0,
        y_max=6576000.0 + hojd * dx,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )

    ii, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
    # Lutande plan från väster (11,30 m) mot öster (10,50 m), med svagt skålformade sidokanter
    sidolut = 0.015 * np.abs(ii - (hojd - 1) / 2.0)
    mark = (11.30 - 0.01 * jj + sidolut).astype(np.float32)

    # Höga sidovallar längs norr, söder och väster så allt flöde leds österut
    mark[0:2, :] = np.float32(11.80)
    mark[-2:, :] = np.float32(11.80)
    mark[:, 0:2] = np.float32(11.80)

    # Djup rektangulär sänka nedströms (rader 12..36, kol 40..64 -> 24x24 = 36 m², botten 9,90 m)
    mark[12:36, 40:64] = np.float32(9.90)
    # Överfallströskel vid östra sidan av sänkan (z = 10,45 m -> persistens 0,55 m)
    mark[12:36, 64:68] = np.float32(10.65)
    mark[20:28, 64:68] = np.float32(10.45)
    for j in range(68, bredd):
        mark[20:28, j] = np.float32(10.44 - 0.015 * (j - 68))

    markslag = np.full((hojd, bredd), 1, dtype=np.uint8)
    return mark, markslag, geo


class TestLokalTroghet(unittest.TestCase):
    """Verifierar 2D-lösaren (Bates 2010) mot A1, A4, byggnader och gles front."""

    def test_a1_enkel_sanka_massbalans_och_slutdjup(self) -> None:
        a1_mapp = TESTSVIT_ROT / "A1_enkel_sanka" / "indata"
        mark_raw, geo = las_geotiff(a1_mapp / "mark.tif")
        slag_raw, _ = las_geotiff(a1_mapp / "markslag.tif")
        mark = np.asarray(mark_raw, dtype=np.float32)
        markslag = np.asarray(slag_raw, dtype=np.uint8)

        # Kraftigt regn i 120 s (360 mm/h = 12 mm -> ~2,7 m³ över 225 m² -> ~2,7 cm i bassängen)
        # följt av 60 s avrinning så nästan allt vatten samlas i 100 m²-bassängen
        regn_tid = [0.0, 120.0, 121.0, 180.0]
        regn_mm_h = [360.0, 360.0, 0.0, 0.0]

        res_gles = kor_2d_lokal_troghet(mark, markslag, geo, regn_tid, regn_mm_h, gles_front=True)
        res_tat = kor_2d_lokal_troghet(mark, markslag, geo, regn_tid, regn_mm_h, gles_front=False)

        self.assertEqual(res_gles.niva, "full2d")
        self.assertLess(res_gles.volymfel_rel, 1e-3)
        self.assertLess(res_tat.volymfel_rel, 1e-3)
        self.assertGreater(res_gles.antal_tidssteg, 0)

        # Gles front och tät beräkning ska ge bitnära samma resultat
        np.testing.assert_allclose(res_gles.slut_djup_m, res_tat.slut_djup_m, atol=1e-6)
        np.testing.assert_allclose(res_gles.max_djup_m, res_tat.max_djup_m, atol=1e-6)

        # Kontrollera slutdjup i 100 m²-bassängen (rader 10..50, kol 10..50):
        # Total in ≈ 2,71 m³; ca 2,65 m³ når bassängen -> ~0,0265 m
        forv_djup = (res_gles.in_m3 - res_gles.ut_m3) / 100.0
        bassang_djup = float(np.mean(res_gles.slut_djup_m[12:48, 12:48]))
        self.assertLess(abs(bassang_djup - forv_djup), 0.01)

    def test_a4_lutande_plan_hastighet_fara_och_massbalans(self) -> None:
        a4_mapp = TESTSVIT_ROT / "A4_lutande_plan" / "indata"
        mark_raw, geo = las_geotiff(a4_mapp / "mark.tif")
        slag_raw, _ = las_geotiff(a4_mapp / "markslag.tif")
        regn = las_regn_csv(a4_mapp / "regn.csv")
        mark = np.asarray(mark_raw, dtype=np.float32)
        markslag = np.asarray(slag_raw, dtype=np.uint8)
        rt = [p[0] for p in regn]
        ri = [p[1] for p in regn]

        res = kor_2d_lokal_troghet(mark, markslag, geo, rt, ri, slut_tid_s=180.0)

        self.assertEqual(res.niva, "full2d")
        self.assertLess(res.volymfel_rel, 1e-3)
        self.assertGreater(float(np.max(res.max_hastighet_m_s)), 0.05)
        self.assertGreater(float(np.max(res.fara_m2_s)), 0.0)
        self.assertGreater(res.ut_m3, 0.0)
        self.assertTrue(bool(np.all(res.slut_djup_m >= 0.0)))

    def test_byggnad_ar_helt_ogenomtranglig_och_subgrid_bevarar_massa(self) -> None:
        hojd, bredd = 16, 24
        dx = PIXELSTORLEK_M
        geo = GeoTransformInfo(
            bredd=bredd,
            hojd=hojd,
            x_min=150000.0,
            y_max=6576000.0 + hojd * dx,
            pixelstorlek_x=dx,
            pixelstorlek_y=dx,
            epsg=EPSG_SWEREF99_1800,
            dtype="float32",
        )
        mark = np.full((hojd, bredd), 10.0, dtype=np.float32)
        for j in range(bredd):
            mark[:, j] = np.float32(10.5 - 0.02 * j)
        markslag = np.full((hojd, bredd), 1, dtype=np.uint8)
        # Tvärgående byggnadsvägg i kol 8..12 tvärs hela domänen
        markslag[:, 8:12] = np.uint8(MARKSLAG_BYGGNAD)

        res_fin = kor_2d_lokal_troghet(
            mark, markslag, geo, [0.0, 60.0], [120.0, 120.0], oppna_rander=False
        )
        res_sg4 = kor_2d_lokal_troghet(
            mark,
            markslag,
            geo,
            [0.0, 60.0],
            [120.0, 120.0],
            oppna_rander=False,
            subgrid_faktor=4,
        )

        self.assertLess(res_fin.volymfel_rel, 1e-3)
        self.assertLess(res_sg4.volymfel_rel, 1e-3)
        self.assertTrue(bool(np.all(res_fin.max_djup_m[:, 8:12] == 0.0)))
        self.assertTrue(bool(np.all(res_sg4.max_djup_m[:, 8:12] == 0.0)))
        self.assertAlmostEqual(res_fin.in_m3, res_sg4.in_m3, places=5)
        self.assertAlmostEqual(res_fin.lagrat_m3, res_sg4.lagrat_m3, places=5)


class TestHybridOchK6Export(unittest.TestCase):
    """Verifierar hybrid 2D + sänkgraf mot full 2D samt K6-export och reproducerbarhet."""

    def test_hybrid_mot_full2d_pa_kombinerad_sluttning_och_djup_sanka(self) -> None:
        mark, markslag, geo = _skapa_kombinerad_sluttning_och_sanka()
        osak = np.full(mark.shape, 0.01, dtype=np.float32)
        graf = bygg_sankgraf(mark, osak, markslag, geo)

        # Kraftigt regn som fyller sänkan över 0,20 m (och utlöser ankomsttider) + avrinning
        regn_tid = [0.0, 240.0, 241.0, 300.0]
        regn_mm_h = [900.0, 900.0, 0.0, 0.0]

        res_full = kor_2d_lokal_troghet(mark, markslag, geo, regn_tid, regn_mm_h)
        res_hyb = kor_hybrid_2d_graf(
            mark,
            markslag,
            geo,
            regn_tid,
            regn_mm_h,
            graf=graf,
            nod_persistens_grans_m=0.15,
            subgrid_faktor=4,
        )

        jamf = jamfor_hybrid_mot_full2d(res_full, res_hyb, vat_grans_m=0.10)

        self.assertEqual(res_hyb.niva, "hybrid")
        self.assertLess(jamf.volymfel_full2d_rel, 1e-3)
        self.assertLess(jamf.volymfel_hybrid_rel, 1e-3)
        self.assertGreater(jamf.antal_vata_celler_union, 100)
        self.assertGreater(jamf.traffad_yta_iou, 0.90)
        self.assertLess(jamf.maxdjupsfel_mae_m, 0.05)
        self.assertIsNotNone(jamf.ankomsttid_mae_s)
        assert jamf.ankomsttid_mae_s is not None
        self.assertLess(jamf.ankomsttid_mae_s, 20.0)
        tidsfaktor = float(os.environ.get("FLODEN_TIDSFAKTOR", "1"))
        self.assertGreater(jamf.speedup_faktor, 2.0 / tidsfaktor)
        # Inga negativa djup eller numerisk instabilitet
        self.assertTrue(bool(np.all(np.isfinite(res_hyb.max_djup_m))))
        self.assertTrue(bool(np.all(res_hyb.slut_djup_m >= 0.0)))
        self.assertLess(res_hyb.aktiv_cell_andel, res_full.aktiv_cell_andel)

    def test_hybrid_mot_full2d_pa_a1_fyllning_och_overfall(self) -> None:
        a1_mapp = TESTSVIT_ROT / "A1_enkel_sanka" / "indata"
        mark_raw, geo = las_geotiff(a1_mapp / "mark.tif")
        slag_raw, _ = las_geotiff(a1_mapp / "markslag.tif")
        osak_raw, _ = las_geotiff(a1_mapp / "osakerhet.tif")
        mark = np.asarray(mark_raw, dtype=np.float32)
        markslag = np.asarray(slag_raw, dtype=np.uint8)
        osak = np.asarray(osak_raw, dtype=np.float32)
        graf = bygg_sankgraf(mark, osak, markslag, geo)

        # Fyller sänkan över 0,25 m i 300 s (1200 mm/h) så 0,1 m och 0,2 m ankomsttider testas
        regn_tid = [0.0, 300.0]
        regn_mm_h = [1200.0, 1200.0]

        res_full = kor_2d_lokal_troghet(mark, markslag, geo, regn_tid, regn_mm_h)
        res_hyb = kor_hybrid_2d_graf(
            mark,
            markslag,
            geo,
            regn_tid,
            regn_mm_h,
            graf=graf,
            nod_persistens_grans_m=0.15,
            subgrid_faktor=4,
        )

        jamf = jamfor_hybrid_mot_full2d(res_full, res_hyb, vat_grans_m=0.10)
        self.assertLess(jamf.volymfel_full2d_rel, 1e-3)
        self.assertLess(jamf.volymfel_hybrid_rel, 1e-3)
        self.assertGreaterEqual(jamf.traffad_yta_iou, 0.98)
        self.assertLess(jamf.maxdjupsfel_mae_m, 0.02)
        self.assertIsNotNone(jamf.ankomsttid_mae_s)
        assert jamf.ankomsttid_mae_s is not None
        self.assertLess(jamf.ankomsttid_mae_s, 10.0)
        tidsfaktor = float(os.environ.get("FLODEN_TIDSFAKTOR", "1"))
        self.assertGreater(jamf.speedup_faktor, 3.0 / tidsfaktor)
        self.assertLess(res_hyb.aktiv_cell_andel, res_full.aktiv_cell_andel)

    def test_k6_export_och_reproducerbarhet(self) -> None:
        mark, markslag, geo = _skapa_kombinerad_sluttning_och_sanka()
        regn_tid = [0.0, 60.0]
        regn_mm_h = [300.0, 300.0]

        res_1 = kor_hybrid_2d_graf(mark, markslag, geo, regn_tid, regn_mm_h, subgrid_faktor=4)
        res_2 = kor_hybrid_2d_graf(mark, markslag, geo, regn_tid, regn_mm_h, subgrid_faktor=4)

        with tempfile.TemporaryDirectory() as tmp:
            mapp_1 = Path(tmp) / "korning_1"
            mapp_2 = Path(tmp) / "korning_2"
            filer_1 = skriv_tvad_resultat_k6(
                res_1,
                geo,
                mapp_1,
                parametrar={"test": True},
                kodversion="fast-sha",
                start="2026-11-20T12:00:00+01:00",
            )
            filer_2 = skriv_tvad_resultat_k6(
                res_2,
                geo,
                mapp_2,
                parametrar={"test": True},
                kodversion="fast-sha",
                start="2026-11-20T12:00:00+01:00",
            )

            for nyckel in (
                "max_djup",
                "max_hastighet",
                "fara",
                "tid_till_0_1m",
                "tid_till_0_2m",
                "run",
            ):
                self.assertIn(nyckel, filer_1)
                self.assertTrue(filer_1[nyckel].is_file())

            # Deterministisk reproducerbarhet: identisk sha256 för alla K6-raster
            for nyckel in ("max_djup", "max_hastighet", "fara", "tid_till_0_1m", "tid_till_0_2m"):
                self.assertEqual(sha256_fil(filer_1[nyckel]), sha256_fil(filer_2[nyckel]))

            manifest = las_manifest(filer_1["run"])
            self.assertEqual(manifest.modul, "tvad")
            self.assertEqual(manifest.parametrar["kontrakt_ut"], "K6/v1")
            self.assertEqual(manifest.parametrar["niva"], "hybrid")
            self.assertEqual(len(manifest.utdata), 5)
            self.assertLess(float(manifest.matvarden["volymfel_rel"]), 1e-3)


if __name__ == "__main__":
    unittest.main()
