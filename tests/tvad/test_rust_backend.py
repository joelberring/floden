"""Rust-kärnan (`rust/tvad_karna`) mot Python-lösaren, som är facit (fas 3, del 1)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from sankgraf.typer import MARKSLAG_HARDGJORT
from testsvit.ea_fall import ea_test8a_urbant_regn
from testsvit.k1_io import las_geotiff, las_regn_csv
from tvad import rust_backend
from tvad.lokal_troghet import TvadFel, TvadResultat, kor_2d_lokal_troghet
from tvad.rust_backend import hitta_tvad_karna, kor_2d_lokal_troghet_rust

TESTSVIT = Path(__file__).resolve().parents[2] / "testsvit"
RASTER = ("max_djup_m", "slut_djup_m", "max_hastighet_m_s", "fara_m2_s",
          "tid_till_0_1m_s", "tid_till_0_2m_s")


def _binar_finns() -> bool:
    try:
        hitta_tvad_karna()
    except TvadFel:
        return False
    return True


def _a_fall(namn: str) -> tuple[np.ndarray, np.ndarray, object]:
    mapp = TESTSVIT / namn / "indata"
    mark, geo = las_geotiff(mapp / "mark.tif")
    slag, _ = las_geotiff(mapp / "markslag.tif")
    return np.asarray(mark, np.float32), np.asarray(slag, np.uint8), geo


def _max_skillnad(a: np.ndarray, b: np.ndarray) -> float:
    a64, b64 = a.astype(np.float64), b.astype(np.float64)
    np.testing.assert_array_equal(np.isnan(a64), np.isnan(b64))
    ok = ~np.isnan(a64)
    return float(np.max(np.abs(a64[ok] - b64[ok]))) if ok.any() else 0.0


class TestUtanBinar(unittest.TestCase):
    def test_tydligt_fel_utan_binar(self) -> None:
        with (
            mock.patch.dict(os.environ, {"FLODEN_TVAD_KARNA": ""}),
            mock.patch.object(rust_backend, "_REPO_BINAR", Path("/finns/inte")),
            mock.patch.object(rust_backend.shutil, "which", return_value=None),
            self.assertRaises(TvadFel) as ctx,
        ):
            hitta_tvad_karna("/finns/inte/heller")
        self.assertIn("cargo build", str(ctx.exception))

    def test_subgrid_stods_inte(self) -> None:
        mark, slag, geo = _a_fall("A1_enkel_sanka")
        with self.assertRaises(TvadFel):
            kor_2d_lokal_troghet_rust(mark, slag, geo, [0.0, 60.0], [10.0, 10.0],  # type: ignore[arg-type]
                                      subgrid_faktor=2)


@unittest.skipUnless(_binar_finns(), "tvad_karna är inte byggd (cargo build i rust/tvad_karna)")
class TestRustMotPython(unittest.TestCase):
    def _jamfor(self, py: TvadResultat, rs: TvadResultat) -> None:
        self.assertEqual(py.antal_tidssteg, rs.antal_tidssteg)
        for namn in RASTER:
            self.assertLess(_max_skillnad(getattr(py, namn), getattr(rs, namn)), 1e-6, namn)
        self.assertLess(rs.volymfel_rel, 1e-12)
        self.assertAlmostEqual(py.in_m3, rs.in_m3, delta=1e-12 * py.in_m3)
        # NumPy använder på AVX-512-processorer en vektoriserad pow som kan skilja 1 ulp från
        # libm (som Rust använder). Det ger skillnader på ulp-nivå som växer lite genom randflödet
        # (A4: 7e-9 relativt i lagrad volym). Båda lösarna har volymfel < 1e-12.
        self.assertAlmostEqual(py.lagrat_m3, rs.lagrat_m3, delta=1e-7 * max(py.in_m3, 1.0))
        self.assertAlmostEqual(py.aktiv_cell_andel, rs.aktiv_cell_andel, places=12)

    def test_a1_gles_och_tat(self) -> None:
        mark, slag, geo = _a_fall("A1_enkel_sanka")
        rt, ri = [0.0, 120.0, 121.0, 180.0], [360.0, 360.0, 0.0, 0.0]
        for gles in (True, False):
            py = kor_2d_lokal_troghet(mark, slag, geo, rt, ri, gles_front=gles)  # type: ignore[arg-type]
            rs = kor_2d_lokal_troghet_rust(mark, slag, geo, rt, ri, gles_front=gles)  # type: ignore[arg-type]
            self._jamfor(py, rs)

    def test_a4_oppna_rander(self) -> None:
        mark, slag, geo = _a_fall("A4_lutande_plan")
        regn = las_regn_csv(TESTSVIT / "A4_lutande_plan" / "indata" / "regn.csv")
        rt, ri = [p[0] for p in regn], [p[1] for p in regn]
        py = kor_2d_lokal_troghet(mark, slag, geo, rt, ri, slut_tid_s=180.0)  # type: ignore[arg-type]
        rs = kor_2d_lokal_troghet_rust(mark, slag, geo, rt, ri, slut_tid_s=180.0)  # type: ignore[arg-type]
        self._jamfor(py, rs)
        self.assertGreater(rs.ut_m3, 0.0)

    def test_ea8a_byggnader_och_samma_svar_oavsett_tradar(self) -> None:
        g = ea_test8a_urbant_regn(slut_tid_s=200.0)
        man = {MARKSLAG_HARDGJORT: g.manning_n, 0: g.manning_n}
        kw = dict(slut_tid_s=g.slut_tid_s, oppna_rander=False, manning_tabell=man,
                  min_djup_m=2e-3)
        py = kor_2d_lokal_troghet(g.mark_z, g.markslag, g.geo, g.regn_tid_s,
                                  g.regn_intensitet_mm_h, **kw)  # type: ignore[arg-type]
        r1 = kor_2d_lokal_troghet_rust(g.mark_z, g.markslag, g.geo, g.regn_tid_s,
                                       g.regn_intensitet_mm_h, tradar=1, **kw)  # type: ignore[arg-type]
        r3 = kor_2d_lokal_troghet_rust(g.mark_z, g.markslag, g.geo, g.regn_tid_s,
                                       g.regn_intensitet_mm_h, tradar=3, **kw)  # type: ignore[arg-type]
        self._jamfor(py, r1)
        for namn in RASTER:
            np.testing.assert_array_equal(getattr(r1, namn), getattr(r3, namn))
        self.assertEqual((r1.ut_m3, r1.lagrat_m3), (r3.ut_m3, r3.lagrat_m3))


if __name__ == "__main__":
    unittest.main()
