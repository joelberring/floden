"""Enhetstester för SFINCS I/O (`sfincs_io.py`) och subgrid-tabeller (`subgrid.py`) (T05)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from gemensamt.manifest import las_manifest
from sankgraf.flodesvagar import MANNING_N_PER_MARKSLAG
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import EPSG_SWEREF99_1800, PIXELSTORLEK_M, GeoTransformInfo, las_geotiff
from tvad.lokal_troghet import TvadFel
from tvad.sfincs_io import (
    SFINCS_MASK_AKTIV,
    SFINCS_MASK_INAKTIV,
    SFINCS_MASK_OPPEN_RAND,
    SfincsSaknasFel,
    kor_sfincs_binar,
    las_sfincs_binar_float32,
    las_sfincs_binar_int32,
    las_sfincs_utdata_till_k6,
    skriv_sfincs_indata,
)
from tvad.subgrid import (
    area_fran_vattenniva,
    bygg_subgrid_tabell,
    kant_flodesdjup_u,
    kant_flodesdjup_v,
    projicera_djup_till_fin,
    vattenniva_fran_volym,
    volym_fran_vattenniva,
)


def _skapa_geo(hojd: int, bredd: int, dx: float = PIXELSTORLEK_M) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150000.0,
        y_max=6576000.0 + hojd * dx,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )


class TestSfincsIo(unittest.TestCase):
    """Verifierar översättning K1+K4+K5 -> SFINCS indata, felhantering och utdata -> K6."""

    def test_skriv_sfincs_indata_komplett_och_binarformat(self) -> None:
        hojd, bredd = 12, 16
        geo = _skapa_geo(hojd, bredd)
        mark_z = np.linspace(10.0, 12.0, hojd * bredd, dtype=np.float32).reshape((hojd, bredd))
        markslag = np.full((hojd, bredd), 1, dtype=np.uint8)
        markslag[2:5, 3:6] = np.uint8(MARKSLAG_BYGGNAD)
        markslag[6:10, 8:12] = np.uint8(4)  # gräs

        regn_tid = [0.0, 600.0, 1200.0]
        regn_mm_h = [36.0, 72.0, 0.0]
        scenario = {
            "kontrakt": "K5/v1",
            "regn": "testregn.csv",
            "forluster": {"modell": "green-ampt", "initial_mm": 2.0},
        }

        with tempfile.TemporaryDirectory() as tmp:
            mapp = Path(tmp) / "sfincs_modell"
            konfig = skriv_sfincs_indata(
                mark_z,
                markslag,
                geo,
                regn_tid,
                regn_mm_h,
                mapp,
                tstop_s=1800.0,
                dtmaxout_s=30.0,
                alpha=0.5,
                scenario=scenario,
            )

            self.assertEqual(konfig.mmax, bredd)
            self.assertEqual(konfig.nmax, hojd)
            self.assertEqual(konfig.epsg, EPSG_SWEREF99_1800)
            self.assertAlmostEqual(konfig.tstop_s, 1800.0)
            self.assertTrue((mapp / "sfincs.inp").is_file())
            self.assertTrue((mapp / "sfincs.dep").is_file())
            self.assertTrue((mapp / "sfincs.msk").is_file())
            self.assertTrue((mapp / "sfincs.man").is_file())
            self.assertTrue((mapp / "sfincs.prp").is_file())
            self.assertTrue((mapp / "sfincs_scenario.json").is_file())

            inp_text = (mapp / "sfincs.inp").read_text(encoding="utf-8")
            self.assertIn(f"mmax = {bredd}", inp_text)
            self.assertIn(f"nmax = {hojd}", inp_text)
            self.assertIn("epsg = 3011", inp_text)
            self.assertIn("depfile = sfincs.dep", inp_text)
            self.assertIn("mskfile = sfincs.msk", inp_text)
            self.assertIn("manningfile = sfincs.man", inp_text)
            self.assertIn("precipfile = sfincs.prp", inp_text)
            self.assertIn("alpha = 0.5000", inp_text)

            forv_bytes = hojd * bredd * 4
            self.assertEqual((mapp / "sfincs.dep").stat().st_size, forv_bytes)
            self.assertEqual((mapp / "sfincs.msk").stat().st_size, forv_bytes)
            self.assertEqual((mapp / "sfincs.man").stat().st_size, forv_bytes)

            dep_last = las_sfincs_binar_float32(mapp / "sfincs.dep", hojd, bredd)
            np.testing.assert_allclose(dep_last, mark_z, rtol=0.0, atol=1e-6)

            msk_last = las_sfincs_binar_int32(mapp / "sfincs.msk", hojd, bredd)
            self.assertTrue(bool(np.all(msk_last[2:5, 3:6] == SFINCS_MASK_INAKTIV)))
            self.assertEqual(int(msk_last[0, 0]), SFINCS_MASK_OPPEN_RAND)
            self.assertEqual(int(msk_last[6, 8]), SFINCS_MASK_AKTIV)

            man_last = las_sfincs_binar_float32(mapp / "sfincs.man", hojd, bredd)
            self.assertAlmostEqual(float(man_last[1, 1]), MANNING_N_PER_MARKSLAG[1], places=5)
            self.assertAlmostEqual(float(man_last[7, 9]), MANNING_N_PER_MARKSLAG[4], places=5)

            prp_rader = (mapp / "sfincs.prp").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(prp_rader), 3)
            self.assertTrue(prp_rader[1].startswith("600.000 72.000000"))

    def test_kor_sfincs_binar_kastar_tydligt_fel_nar_binar_saknas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mapp = Path(tmp)
            (mapp / "sfincs.inp").write_text("mmax = 4\n", encoding="utf-8")
            with self.assertRaises(SfincsSaknasFel):
                kor_sfincs_binar(mapp, sfincs_bin="sfincs_obefintlig_binar_xyz")

    def test_mockad_sfincs_binar_och_utdata_till_k6_bin_npz_json(self) -> None:
        hojd, bredd = 8, 8
        geo = _skapa_geo(hojd, bredd)
        mark_z = np.full((hojd, bredd), 10.0, dtype=np.float32)
        markslag = np.full((hojd, bredd), 1, dtype=np.uint8)
        markslag[2:4, 2:4] = np.uint8(MARKSLAG_BYGGNAD)

        with tempfile.TemporaryDirectory() as tmp:
            bas = Path(tmp)
            modell_mapp = bas / "modell"
            skriv_sfincs_indata(mark_z, markslag, geo, [0.0, 60.0], [50.0, 50.0], modell_mapp)

            # Mocka lyckad körning av extern SFINCS-binär
            with (
                mock.patch("tvad.sfincs_io.shutil.which", return_value="/usr/local/bin/sfincs"),
                mock.patch(
                    "tvad.sfincs_io.subprocess.run",
                    return_value=subprocess.CompletedProcess(["sfincs"], 0, "ok", ""),
                ) as mock_run,
            ):
                retur_mapp = kor_sfincs_binar(modell_mapp)
                self.assertEqual(retur_mapp, modell_mapp)
                mock_run.assert_called_once()

            # Skriv mockade binära SFINCS-resultat (söder->norr, little-endian float32)
            zsmax_norr = np.full((hojd, bredd), 10.35, dtype=np.float32)
            vmax_norr = np.full((hojd, bredd), 0.60, dtype=np.float32)
            (modell_mapp / "zsmax.bin").write_bytes(
                np.ascontiguousarray(zsmax_norr[::-1, :], dtype="<f4").tobytes()
            )
            (modell_mapp / "vmax.bin").write_bytes(
                np.ascontiguousarray(vmax_norr[::-1, :], dtype="<f4").tobytes()
            )

            k6_mapp = bas / "k6_ut"
            res = las_sfincs_utdata_till_k6(modell_mapp, geo, k6_mapp, kodversion="testsha")
            self.assertEqual(res.niva, "full2d")
            self.assertAlmostEqual(float(res.max_djup_m[0, 0]), 0.35, places=5)
            self.assertAlmostEqual(float(res.max_hastighet_m_s[0, 0]), 0.60, places=5)
            self.assertAlmostEqual(float(res.fara_m2_s[0, 0]), 0.21, places=5)
            # Byggnadsceller ska maskas till 0
            self.assertEqual(float(res.max_djup_m[2, 2]), 0.0)
            self.assertEqual(float(res.max_hastighet_m_s[2, 2]), 0.0)
            self.assertEqual(float(res.fara_m2_s[2, 2]), 0.0)

            for filnamn in (
                "max_djup.tif",
                "max_hastighet.tif",
                "fara.tif",
                "tid_till_0_1m.tif",
                "tid_till_0_2m.tif",
                "run.json",
            ):
                self.assertTrue((k6_mapp / filnamn).is_file(), f"Saknar {filnamn}")

            manifest = las_manifest(k6_mapp / "run.json")
            self.assertEqual(manifest.parametrar["niva"], "full2d")
            self.assertEqual(manifest.parametrar["kontrakt_ut"], "K6/v1")

            djup_tif, djup_geo = las_geotiff(k6_mapp / "max_djup.tif")
            self.assertEqual(djup_geo.epsg, EPSG_SWEREF99_1800)
            np.testing.assert_allclose(djup_tif, res.max_djup_m, atol=1e-6)

            # Testa även JSON-utdata och tomt fel
            json_mapp = bas / "json_modell"
            json_mapp.mkdir()
            (json_mapp / "sfincs_utdata.json").write_text(
                json.dumps(
                    {
                        "hmax": np.full((hojd, bredd), 0.20, dtype=np.float32).tolist(),
                        "vmax": np.full((hojd, bredd), 0.50, dtype=np.float32).tolist(),
                    }
                ),
                encoding="utf-8",
            )
            res_json = las_sfincs_utdata_till_k6(
                json_mapp, geo, bas / "k6_json", markslag=markslag, kodversion="testsha"
            )
            self.assertAlmostEqual(float(res_json.max_djup_m[0, 0]), 0.20, places=5)
            self.assertEqual(float(res_json.max_djup_m[2, 2]), 0.0)

            tom_mapp = bas / "tom_modell"
            tom_mapp.mkdir()
            with self.assertRaises(TvadFel):
                las_sfincs_utdata_till_k6(tom_mapp, geo, bas / "k6_tom")


class TestSubgridTabell(unittest.TestCase):
    """Verifierar exakt volymbevarande, byggnadsblockering och kantfall i `subgrid.py`."""

    def test_exakt_volymbevarande_och_invers_mot_fint_raster(self) -> None:
        hojd, bredd = 16, 16
        geo = _skapa_geo(hojd, bredd)
        rng = np.random.default_rng(42)
        mark_z = (10.0 + rng.uniform(0.0, 0.8, size=(hojd, bredd))).astype(np.float32)
        markslag = np.full((hojd, bredd), 1, dtype=np.uint8)
        markslag[0:2, 0:2] = np.uint8(MARKSLAG_BYGGNAD)
        markslag[8:12, 8:12] = np.uint8(MARKSLAG_BYGGNAD)  # hel grov byggnadscell

        sg = bygg_subgrid_tabell(mark_z, markslag, geo, faktor=4, z_nivaer=24)
        self.assertEqual(sg.hojd_grov, 4)
        self.assertEqual(sg.bredd_grov, 4)
        self.assertFalse(bool(sg.aktiv_mask[2, 2]))
        self.assertTrue(bool(sg.aktiv_mask[0, 0]))
        self.assertAlmostEqual(float(sg.aktiv_area_m2[0, 0]), 12.0 * (PIXELSTORLEK_M**2))

        # Testa både delvis fyllda (< z_max) och helt dränkta (> z_max) grova celler
        volym_in = np.where(sg.aktiv_mask, rng.uniform(0.01, 0.60, size=(4, 4)), 0.0)
        zs = vattenniva_fran_volym(volym_in, sg)
        volym_tillbaka = volym_fran_vattenniva(zs, sg)
        np.testing.assert_allclose(volym_tillbaka, volym_in, rtol=1e-12, atol=1e-12)

        # Kontrollera exakt massbalans efter projektion till fint 0,25 m raster!
        djup_fin = projicera_djup_till_fin(zs, sg)
        self.assertTrue(bool(np.all(djup_fin[markslag == MARKSLAG_BYGGNAD] == 0.0)))
        volym_fin_m3 = float(np.sum(djup_fin, dtype=np.float64)) * (PIXELSTORLEK_M**2)
        volym_grov_m3 = float(np.sum(volym_in))
        rel_fel = abs(volym_fin_m3 - volym_grov_m3) / volym_grov_m3
        self.assertLess(rel_fel, 1e-6)

        # Kontrollera att våt area ökar monotont och aldrig överstiger aktiv_area_m2
        area_lo = area_fran_vattenniva(sg.z_min, sg)
        area_hi = area_fran_vattenniva(sg.z_max + 0.5, sg)
        self.assertTrue(bool(np.all(area_hi[sg.aktiv_mask] >= area_lo[sg.aktiv_mask])))
        np.testing.assert_allclose(area_hi, sg.aktiv_area_m2, rtol=1e-12, atol=1e-12)

    def test_byggnadsvagg_blockerar_flode_och_halv_oppning_ger_0_5(self) -> None:
        hojd, bredd = 8, 12
        geo = _skapa_geo(hojd, bredd)
        mark_z = np.full((hojd, bredd), 10.0, dtype=np.float32)
        markslag = np.full((hojd, bredd), 1, dtype=np.uint8)

        # Hel byggnadsvägg mellan grov kol 0 (kol 0..3) och kol 1 (kol 4..7) i rad 0..3
        markslag[0:4, 4] = np.uint8(MARKSLAG_BYGGNAD)
        # Halv byggnadsvägg (2 av 4 fina celler) mellan grov kol 1 och kol 2 i rad 0..3
        markslag[0:2, 8] = np.uint8(MARKSLAG_BYGGNAD)
        # Horisontell byggnadsvägg mellan grov rad 0 och rad 1 i kol 0..3
        markslag[4, 0:4] = np.uint8(MARKSLAG_BYGGNAD)

        sg = bygg_subgrid_tabell(mark_z, markslag, geo, faktor=4, z_nivaer=24)

        self.assertEqual(float(sg.oppningsgrad_u[0, 0]), 0.0)
        self.assertAlmostEqual(float(sg.oppningsgrad_u[0, 1]), 0.5, places=12)
        self.assertEqual(float(sg.oppningsgrad_v[0, 0]), 0.0)
        self.assertAlmostEqual(float(sg.oppningsgrad_v[0, 1]), 0.75, places=12)
        self.assertEqual(float(sg.oppningsgrad_v[0, 2]), 1.0)

        zs_hog = np.full((sg.hojd_grov, sg.bredd_grov), 12.0, dtype=np.float64)
        hu = kant_flodesdjup_u(zs_hog, sg)
        hv = kant_flodesdjup_v(zs_hog, sg)
        self.assertEqual(float(hu[0, 0]), 0.0)
        self.assertAlmostEqual(float(hu[0, 1]), 1.0, places=12)  # 0.5 * (12.0 - 10.0)
        self.assertEqual(float(hv[0, 0]), 0.0)
        self.assertAlmostEqual(float(hv[0, 1]), 1.5, places=12)  # 0.75 * (12.0 - 10.0)
        self.assertAlmostEqual(float(hv[0, 2]), 2.0, places=12)

    def test_ej_delbara_kanter_hanteras_massbevarande(self) -> None:
        hojd, bredd = 13, 15
        geo = _skapa_geo(hojd, bredd)
        mark_z = np.full((hojd, bredd), 10.0, dtype=np.float32)
        markslag = np.full((hojd, bredd), 1, dtype=np.uint8)

        sg = bygg_subgrid_tabell(mark_z, markslag, geo, faktor=4, z_nivaer=16)
        self.assertEqual(sg.hojd_grov, 4)
        self.assertEqual(sg.bredd_grov, 4)
        # Nedre högra grova hörncellen innehåller bara 1 x 3 = 3 fina celler
        self.assertAlmostEqual(float(sg.aktiv_area_m2[3, 3]), 3.0 * (PIXELSTORLEK_M**2))

        vol = sg.aktiv_area_m2 * 0.25
        zs = vattenniva_fran_volym(vol, sg)
        djup_fin = projicera_djup_till_fin(zs, sg)
        self.assertEqual(djup_fin.shape, (hojd, bredd))
        np.testing.assert_allclose(djup_fin, 0.25, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
