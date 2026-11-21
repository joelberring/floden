"""Käll-/sänktermer i 2D-lösaren och Hunter-facit (fas 3)."""

from __future__ import annotations

import math
import unittest

import numpy as np

from mark_forluster.infiltration import green_ampt_tidsserie
from testsvit.hunter import hunter_djup_m, kor_hunter
from testsvit.k1_io import GeoTransformInfo
from tvad.kalltermer import (
    Avtappning,
    Infiltration2D,
    Kalltermer,
    KalltermFel,
    kapacitet_fran_omraden,
)
from tvad.lokal_troghet import kor_2d_lokal_troghet

GRAS = 4


def _bassang(n: int = 6, dx: float = 2.0) -> tuple[np.ndarray, np.ndarray, GeoTransformInfo]:
    z = np.zeros((n, n), dtype=np.float32)
    ms = np.full((n, n), GRAS, dtype=np.uint8)
    geo = GeoTransformInfo(bredd=n, hojd=n, x_min=0.0, y_max=n * dx, pixelstorlek_x=dx,
                           pixelstorlek_y=dx, epsg=3011, dtype="float32")
    return z, ms, geo


def _kor(kalltermer: Kalltermer | None, *, mm_h: float = 36.0, t: float = 3600.0,
         subgrid: int = 1, n: int = 6) -> object:
    z, ms, geo = _bassang(n)
    return kor_2d_lokal_troghet(z, ms, geo, [0.0, t], [mm_h, mm_h], oppna_rander=False,
                                subgrid_faktor=subgrid, kalltermer=kalltermer)


class TestAvtappning(unittest.TestCase):
    def test_kapacitet_lika_med_intensitet_ger_torr_bassang(self) -> None:
        r = _kor(Kalltermer(avtappning=Avtappning.likformig((6, 6), 36.0)))
        self.assertLess(float(np.max(r.slut_djup_m)), 1e-9)
        self.assertAlmostEqual(r.avtappat_m3, r.in_m3, delta=1e-9 * r.in_m3)
        self.assertLess(r.volymfel_rel, 1e-12)

    def test_halva_kapaciteten_ger_halva_djupet(self) -> None:
        r = _kor(Kalltermer(avtappning=Avtappning.likformig((6, 6), 18.0)))
        np.testing.assert_allclose(r.slut_djup_m, 0.018, atol=1e-9)
        self.assertAlmostEqual(r.avtappat_m3, 0.5 * r.in_m3, delta=1e-9 * r.in_m3)
        self.assertLess(r.volymfel_rel, 1e-12)

    def test_igensatta_brunnar_minskar_kapaciteten(self) -> None:
        avt = Avtappning.likformig((6, 6), 36.0, brunnar_igensatta_andel=0.5)
        r = _kor(Kalltermer(avtappning=avt))
        np.testing.assert_allclose(r.slut_djup_m, 0.018, atol=1e-9)

    def test_subgrid_aggregerar_kapaciteten(self) -> None:
        r = _kor(Kalltermer(avtappning=Avtappning.likformig((8, 8), 18.0)), subgrid=2, n=8)
        np.testing.assert_allclose(r.slut_djup_m, 0.018, atol=1e-6)
        self.assertLess(r.volymfel_rel, 1e-12)

    def test_utan_termer_identiskt_resultat(self) -> None:
        bas = _kor(None)
        tom = _kor(Kalltermer())
        np.testing.assert_array_equal(bas.slut_djup_m, tom.slut_djup_m)
        self.assertEqual((bas.in_m3, bas.lagrat_m3), (tom.in_m3, tom.lagrat_m3))
        self.assertIsNone(bas.sanktermer)
        assert tom.sanktermer is not None
        self.assertIn("saknas", str(tom.sanktermer["avtappning"]))

    def test_omraden_till_raster_och_fel(self) -> None:
        poly = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 12], [0, 12], [0, 0]]]}
        kap = kapacitet_fran_omraden((6, 6), 0.0, 12.0, 2.0, [(poly, 10.0)])
        np.testing.assert_array_equal(kap[:, :2], 10.0)
        np.testing.assert_array_equal(kap[:, 2:], 0.0)
        with self.assertRaises(KalltermFel):
            Avtappning(np.full((2, 2), -1.0))
        with self.assertRaises(KalltermFel):
            Avtappning.likformig((2, 2), 1.0, brunnar_igensatta_andel=1.5)


class TestInfiltration(unittest.TestCase):
    def test_green_ampt_som_0d_modellen(self) -> None:
        # Blockregn: 2D-lösaren tolkar K4 styckvis linjärt och 0D-modellen som trappsteg;
        # med konstant intensitet är tolkningarna identiska. 0D körs med samma 1 s-steg som 2D
        # (dt_max_s=1): med ett enda timsteg lägger 0D den initiala förlusten vid t=0 och ger
        # ~2 % mer infiltration vid 50 mm/h – en egenskap hos 0D-stegningen, inte hos 2D.
        ksat, psi, dth, init = 10.0, 0.11, 0.3, 2.0
        for mm_h, t in ((50.0, 3600.0), (15.0, 1800.0), (8.0, 3600.0)):
            steg = list(np.arange(0.0, t + 0.5, 1.0))
            noll = green_ampt_tidsserie(steg, [mm_h] * len(steg), ksat, psi, dth,
                                        initial_mm=init)
            z, ms, geo = _bassang(4)
            f = np.full((4, 4), 1.0)
            inf = Infiltration2D(f * ksat, f * psi, f * dth, f * init)
            r = kor_2d_lokal_troghet(z, ms, geo, [0.0, t], [mm_h, mm_h], oppna_rander=False,
                                     dt_max_s=1.0, kalltermer=Kalltermer(infiltration=inf))
            assert r.infiltration_m is not None
            f2d = float(np.mean(r.infiltration_m))
            self.assertLess(abs(f2d - noll.infiltration_m), 1e-6 * noll.infiltration_m,
                            f"{mm_h} mm/h: 2D {f2d:.6f} m, 0D {noll.infiltration_m:.6f} m")
            djup = float(np.mean(r.slut_djup_m))
            self.assertAlmostEqual(djup, noll.netto_regn_m, delta=0.002 * noll.regn_totalt_m)
            self.assertAlmostEqual(r.initial_forlust_m3 / (16 * 4.0), init / 1000.0, places=12)
            self.assertLess(r.volymfel_rel, 1e-12)

    def test_hardgjort_infiltrerar_inte(self) -> None:
        z, ms, geo = _bassang(4)
        ms[:] = 1
        inf = Infiltration2D.fran_markslag(ms, "sand", initial_mm=0.0)
        r = kor_2d_lokal_troghet(z, ms, geo, [0.0, 600.0], [30.0, 30.0], oppna_rander=False,
                                 kalltermer=Kalltermer(infiltration=inf))
        self.assertEqual(r.infiltrerat_m3, 0.0)
        np.testing.assert_allclose(r.slut_djup_m, 0.005, atol=1e-9)


class TestHunter(unittest.TestCase):
    def test_analytisk_losning_uppfyller_manning(self) -> None:
        n, u, t = 0.05, 0.2, 1800.0
        x = np.linspace(10.0, 300.0, 50)
        h = hunter_djup_m(x, t, n=n, u=u)
        s = -np.gradient(h, x)
        u_manning = h ** (2.0 / 3.0) * np.sqrt(s) / n
        np.testing.assert_allclose(u_manning[2:-2], u, rtol=2e-3)
        self.assertEqual(float(hunter_djup_m(u * t + 1.0, t, n=n, u=u)), 0.0)

    def test_konvergens_mot_hunter(self) -> None:
        grov = kor_hunter(20.0, 1800.0, n=0.05, u=0.2)
        fin = kor_hunter(5.0, 1800.0, n=0.05, u=0.2)
        self.assertLess(fin.rmse_m, 0.5 * grov.rmse_m)
        self.assertLess(fin.rmse_m, 0.01)
        self.assertLessEqual(abs(fin.front_fel_m), 10.0)
        for m in (grov, fin):
            self.assertLess(m.volymfel_rel, 1e-12)
            self.assertTrue(math.isfinite(m.max_fel_m))


if __name__ == "__main__":
    unittest.main()
