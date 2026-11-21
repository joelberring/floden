"""Belagd yta ur laserintensitet.

Fallen `town`, `grass` m.fl. och facit är porterade ur O-Tids
`packages/map-revision/test/geo-paved.test.ts` (commit 1d63297), så att porten mäts mot
originalets egna svar.
"""

from __future__ import annotations

import math
import unittest
from collections.abc import Callable, Sequence

import numpy as np

from data_inlasning.lantmateriet import PunktMoln
from markmodell.laser_belagt import (
    Punktstatistik,
    belagd_grans,
    belagd_yta,
    mask_pa_rutnat,
    otsu_grans,
    punktstatistik,
)

NAN = math.nan
NONE = (NAN, 0, 0, NAN)  # högst, antal, mark, intensitet


def ground(v: float) -> tuple[float, float, float, float]:
    return (0.0, 10, 10, v * 64)


def grass(x: int, y: int) -> tuple[float, float, float, float]:
    return ground(100 + ((x * 7 + y * 13) % 5) * 5)


def within(x: int, y: int, x0: int, y0: int, x1: int, y1: int) -> bool:
    return x0 <= x <= x1 and y0 <= y <= y1


def town(x: int, y: int) -> tuple[float, float, float, float]:
    if within(x, y, 40, 40, 41, 41):
        return NONE
    if within(x, y, 10, 0, 14, 79) or within(x, y, 30, 30, 49, 49):
        return ground(23)
    if within(x, y, 60, 10, 65, 15):
        return (8, 10, 0, NAN)  # hus
    if within(x, y, 60, 30, 65, 35):
        return (5, 10, 9, 23 * 64)  # asfalt under trädkrona
    if within(x, y, 60, 50, 65, 55):
        return (0.2, 10, 5, 23 * 64)  # mörkt men halva markträffar
    if within(x, y, 60, 60, 62, 62):
        return ground(23)  # 36 m², för liten
    return grass(x, y)


def stats(
    b: int, h: int, cell: Callable[[int, int], Sequence[float]]
) -> Punktstatistik:
    v = np.array([[cell(x, y) for y in range(h)] for x in range(b)], dtype=np.float32)
    return Punktstatistik(
        min_e=674_000.0,
        min_n=6_570_000.0,
        cell_m=2.0,
        hogst_over_mark_m=v[:, :, 0],
        antal=v[:, :, 1],
        mark=v[:, :, 2],
        intensitet=v[:, :, 3],
    )


def hist(*toppar: tuple[int, int]) -> list[int]:
    h = [0] * 256
    for v, c in toppar:
        h[v] = c
    return h


class TestTroskel(unittest.TestCase):
    def test_otsu_och_grans_som_otid(self) -> None:
        self.assertEqual(otsu_grans(hist((23, 500), (130, 3000))), 76)
        self.assertIsNone(otsu_grans(hist((50, 10))))
        self.assertEqual(belagd_grans(hist((23, 500), (130, 3000))), (57.5, 23, 76))
        self.assertEqual(belagd_grans(hist((40, 1000), (101, 3000))), (70, 40, 70))
        self.assertIsInstance(belagd_grans(hist((60, 1000), (100, 3000))), str)
        self.assertIsInstance(belagd_grans(hist((23, 50), (130, 3000))), str)
        self.assertIsInstance(belagd_grans(hist((23, 100), (130, 1000))), str)


class TestBelagdYta(unittest.TestCase):
    def test_town_ger_gatan_och_planen(self) -> None:
        r = belagd_yta(stats(80, 80, town))
        self.assertEqual(r.mork_topp, 23)
        self.assertEqual(r.grans, 57.5)
        assert r.otsu is not None
        self.assertGreater(r.otsu, 57)
        self.assertEqual(int(r.mask.sum()), 400 + 396)
        self.assertEqual(r.belagd_ha, 0.32)
        m = r.mask
        self.assertEqual([m[12, 40], m[40, 40], m[35, 35]], [1, 1, 1])
        self.assertEqual([m[62, 12], m[62, 32], m[62, 52], m[61, 61], m[20, 20]], [0, 0, 0, 0, 0])
        self.assertIsNone(r.skal)

    def test_en_topp_ger_ingenting(self) -> None:
        ang = belagd_yta(stats(80, 80, grass))
        self.assertEqual(
            (ang.belagd_ha, ang.oppna_celler, ang.skal), (0.0, 6400, "ingen mörk topp")
        )
        self.assertIsNone(ang.grans)
        liten = belagd_yta(stats(20, 20, lambda x, y: ground(23) if x < 6 else grass(x, y)))
        self.assertEqual(liten.skal, "för få öppna celler")
        flack = belagd_yta(
            stats(80, 80, lambda x, y: ground(23) if within(x, y, 10, 10, 19, 19) else grass(x, y))
        )
        self.assertEqual(flack.skal, "för få mörka celler")

    def test_vatten_ar_inte_oppen_mark(self) -> None:
        r = belagd_yta(stats(80, 80, town), vatten=lambda e, n: e < 674_000 + 15 * 2)
        self.assertEqual(int(r.mask.sum()), 396)


class TestPunktstatistik(unittest.TestCase):
    def test_statistik_ur_punkter(self) -> None:
        # Två 2 m-celler: (0,0) asfalt med två markpunkter, (1,0) gräs + en buske 1,2 m.
        e = np.array([0.5, 1.5, 2.5, 3.5, 3.6, 0.7])
        n = np.array([0.5, 1.5, 0.5, 1.5, 1.4, 0.6])
        z = np.array([10.0, 10.0, 10.0, 10.0, 11.2, 70.5])
        k = np.array([2, 2, 2, 2, 4, 1], dtype=np.uint8)
        i = np.array([1000, 2000, 6000, 6400, 500, 9], dtype=np.uint16)
        moln = PunktMoln(e, n, z, k, 3011, "test", intensitet=i)
        s = punktstatistik(moln, (0.0, 0.0, 4.0, 2.0))
        self.assertEqual((s.bredd, s.hojd), (2, 1))
        np.testing.assert_array_equal(s.antal[:, 0], [2, 3])  # punkten 60,5 m över mark räknas inte
        np.testing.assert_array_equal(s.mark[:, 0], [2, 2])
        np.testing.assert_allclose(s.intensitet[:, 0], [1500, 6200])
        np.testing.assert_allclose(s.hogst_over_mark_m[:, 0], [0.0, 1.2], atol=1e-5)

    def test_kraver_intensitet(self) -> None:
        moln = PunktMoln(np.zeros(1), np.zeros(1), np.zeros(1), np.ones(1, np.uint8), 3011, "t")
        with self.assertRaises(ValueError):
            punktstatistik(moln, (0.0, 0.0, 1.0, 1.0))

    def test_hela_kedjan_ur_punkter_och_till_k1_rutnat(self) -> None:
        # 120 × 120 m gräs med en 10 m bred asfaltsgata i nord–sydlig riktning (e 40–50 m).
        rng = np.random.default_rng(42)
        e = rng.uniform(0, 120, 60_000)
        n = rng.uniform(0, 120, 60_000)
        gata = (e >= 40) & (e < 50)
        intens = np.where(gata, 23 * 64, 110 * 64) + rng.integers(-64, 64, e.size)
        moln = PunktMoln(
            e, n, np.full(e.size, 15.0), np.full(e.size, 2, np.uint8), 3011, "syntet",
            intensitet=intens.astype(np.uint16),
        )
        s = punktstatistik(moln, (0.0, 0.0, 120.0, 120.0))
        r = belagd_yta(s)
        self.assertIsNone(r.skal)
        kol = r.mask.sum(axis=1)
        self.assertTrue(np.all(kol[20:25] == 60))  # gatans fem 2 m-kolumner helt belagda
        self.assertEqual(int(kol[:19].sum() + kol[26:].sum()), 0)
        k1 = mask_pa_rutnat(r, s, min_e=38.0, min_n=0.0, cell_m=0.25, bredd=56, hojd=8)
        self.assertEqual(k1.shape, (8, 56))
        np.testing.assert_array_equal(k1[0, :8], 0)  # e 38–40: gräs
        np.testing.assert_array_equal(k1[0, 8:48], 1)  # e 40–50: gata
        np.testing.assert_array_equal(k1[-1, 48:], 0)


if __name__ == "__main__":
    unittest.main()
