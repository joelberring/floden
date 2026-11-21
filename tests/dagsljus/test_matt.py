"""Dagsljusmått mot analytiska facit: fri plan, gaturum, skuggstudie och konvergens."""

from __future__ import annotations

import math
import unittest

import numpy as np

from dagsljus.horisont import (
    Hojdfalt,
    horisont_mipmap,
    horisont_referens,
    riktningar_rad,
)
from dagsljus.matt import (
    N_HORISONTELL_OSKYMD,
    VSC_OSKYMD,
    MattFel,
    SoltimmarAckumulator,
    halvplansriktningar,
    himmelsintegral,
    interpolera_horisont,
    soltimmar_fran_profiler,
    svf_fran_horisont,
    vsc_fran_halvplansprofil,
)
from dagsljus.solposition import dygnsfonster_utc, solposition

from .geometri import (
    kanjon_falt,
    plan,
    svf_kanjon_facit,
    vsc_kanjon_facit,
)

HOJD = 20.0
R = 250.0


def _vsc_i_kanjon(cell_m: float, bredd_m: float, kf: int, falt: Hojdfalt | None = None) -> float:
    """VSC vid fasadfoten (på gatan 0,5 cell från väggen, z = 0), fasadens normal mot öster."""
    f = falt or kanjon_falt(cell_m, bredd_m, HOJD, halv_x_m=R, halv_y_m=R)
    phi = halvplansriktningar(np.array([math.pi / 2]), kf)[0]
    x0 = -bredd_m / 2.0 + 0.5 * cell_m
    r = horisont_mipmap(f, np.full(kf, x0), np.zeros(kf), np.zeros(kf), phi, R)
    return float(vsc_fran_halvplansprofil(r.h_rad))


class TestFriPlan(unittest.TestCase):
    def test_svf_ar_exakt_ett(self) -> None:
        self.assertEqual(float(svf_fran_horisont(np.zeros(64))), 1.0)

    def test_normeringen_oskymd_fasad_ar_39_6_procent(self) -> None:
        """BRE: VSC för en oskymd vertikal fasad under CIE-mulen himmel ≈ 39,6 %."""
        self.assertAlmostEqual(VSC_OSKYMD, 0.3962, places=4)
        self.assertAlmostEqual(N_HORISONTELL_OSKYMD, 7 * math.pi / 9, places=14)
        # Mittpunktsregeln konvergerar kvadratiskt mot normeringen: fel = 4e-5 vid 64 riktningar.
        for k, tol in ((16, 7e-4), (32, 2e-4), (64, 5e-5), (256, 3e-6)):
            fel = abs(float(vsc_fran_halvplansprofil(np.zeros(k))) - VSC_OSKYMD)
            self.assertLess(fel, tol, msg=f"K={k}, fel {fel:.2e}")

    def test_fri_plan_ger_normeringen_genom_hela_kedjan(self) -> None:
        f = plan(300, 1.0)
        phi = halvplansriktningar(np.array([1.0]), 64)[0]
        r = horisont_mipmap(f, np.full(64, 150.5), np.full(64, 150.5), np.zeros(64), phi, 100.0)
        self.assertLess(abs(float(vsc_fran_halvplansprofil(r.h_rad)) - VSC_OSKYMD), 5e-5)


class TestHimmelsintegral(unittest.TestCase):
    def test_slutenform_mot_numerisk_kvadratur(self) -> None:
        """I(h) = Int_h^{pi/2} (1+2 sin g)/3 cos^2 g dg mot Gauss–Legendre med 200 noder."""
        x, w = np.polynomial.legendre.leggauss(200)
        fel = 0.0
        for h in np.linspace(0.0, math.pi / 2, 31):
            g = 0.5 * (math.pi / 2 - h) * x + 0.5 * (math.pi / 2 + h)
            num = float(
                np.sum(w * (1 + 2 * np.sin(g)) / 3 * np.cos(g) ** 2) * 0.5 * (math.pi / 2 - h)
            )
            fel = max(fel, abs(float(himmelsintegral(np.array(h))) - num))
        self.assertLess(fel, 1e-14)

    def test_randvarden(self) -> None:
        self.assertAlmostEqual(float(himmelsintegral(np.array(0.0))), (math.pi / 4 + 2 / 3) / 3, 14)
        self.assertAlmostEqual(float(himmelsintegral(np.array(math.pi / 2))), 0.0, 14)

    def test_horisontell_belysning_ar_7pi_over_9(self) -> None:
        x, w = np.polynomial.legendre.leggauss(200)
        g = 0.5 * (math.pi / 2) * (x + 1)
        e = (
            2
            * math.pi
            * float(np.sum(w * (1 + 2 * np.sin(g)) / 3 * np.sin(g) * np.cos(g)) * math.pi / 4)
        )
        self.assertAlmostEqual(e, N_HORISONTELL_OSKYMD, 13)

    def test_ogiltiga_vinklar_ger_fel(self) -> None:
        with self.assertRaises(MattFel):
            svf_fran_horisont(np.array([0.1, 2.0]))
        with self.assertRaises(MattFel):
            vsc_fran_halvplansprofil(np.array([0.1, -0.5, 0.0]))


class TestGaturum(unittest.TestCase):
    """Oändligt gaturum (höjd H = 20 m, bredd W) mellan två långa väggar.

    SVF på gatan: (cos atan(H/d1) + cos atan(H/d2)) / 2 (Oke 1981; härledning i
    docs/DAGSLJUS.md). VSC vid fasadfoten: kvadratur av I(atan(H cos psi / d)) cos psi.
    Räckvidden är R = 250 m, så riktningar nära gatans längdaxel (träff bortom R) ger h = 0;
    facit "med räckvidd" integrerar det exakt, facit "oändligt" gör det inte.
    """

    def test_svf_mitt_i_gatan_mot_oke(self) -> None:
        for cell_m, bredd in ((2.0, 22.0), (1.0, 21.0), (0.5, 21.5)):
            f = kanjon_falt(cell_m, bredd, HOJD, halv_x_m=R, halv_y_m=R)
            k = 64
            r = horisont_mipmap(f, np.zeros(k), np.zeros(k), np.zeros(k), riktningar_rad(k), R)
            self.assertFalse(r.trunkerad.any())
            svf = float(svf_fran_horisont(r.h_rad))
            oke = svf_kanjon_facit(HOJD, bredd / 2, bredd / 2)
            self.assertLess(abs(svf - oke), 1e-9, msg=f"cell {cell_m}: {svf - oke:.2e}")

    def test_svf_med_rackvidd_konvergerar_i_k(self) -> None:
        f = kanjon_falt(1.0, 21.0, HOJD, halv_x_m=R, halv_y_m=R)
        facit = svf_kanjon_facit(HOJD, 10.5, 10.5, R)
        fel = {}
        for k in (16, 64, 1024):
            r = horisont_mipmap(f, np.zeros(k), np.zeros(k), np.zeros(k), riktningar_rad(k), R)
            fel[k] = abs(float(svf_fran_horisont(r.h_rad)) - facit)
        self.assertLess(fel[16], 3e-4)
        self.assertLess(fel[64], 1e-4)
        self.assertLess(fel[1024], 1e-5)
        self.assertLess(fel[1024], fel[16])

    def test_svf_symmetrisk_gata_oke_formel_ar_cos_atan_2h_over_w(self) -> None:
        self.assertAlmostEqual(
            svf_kanjon_facit(20.0, 10.5, 10.5), math.cos(math.atan(2 * 20.0 / 21.0)), 14
        )

    def test_vsc_vid_fasadfot_mot_kvadratur(self) -> None:
        for cell_m, bredd in ((2.0, 22.0), (1.0, 21.0), (0.5, 21.5)):
            d = bredd - 0.5 * cell_m
            facit = vsc_kanjon_facit(HOJD, d, R)
            fel = abs(_vsc_i_kanjon(cell_m, bredd, 1024) - facit)
            self.assertLess(fel, 1e-5, msg=f"cell {cell_m}: {fel:.2e}")
            # Normal användning (K/2 = 32 riktningar): fel < 3e-4 av ett värde på 0,16.
            self.assertLess(abs(_vsc_i_kanjon(cell_m, bredd, 32) - facit), 3e-4)

    def test_vsc_foten_ar_mycket_under_oskymd(self) -> None:
        self.assertLess(_vsc_i_kanjon(1.0, 21.0, 128), 0.5 * VSC_OSKYMD)

    def test_vsc_aven_mot_oandlig_kvadratur_inom_trunkeringsbias(self) -> None:
        """R = 250 m kapar riktningar som träffar väggen bortom 250 m: bias ≈ 5e-5 (0,03 %)."""
        d = 21.0 - 0.5
        v = _vsc_i_kanjon(1.0, 21.0, 1024)
        self.assertLess(abs(v - vsc_kanjon_facit(HOJD, d)), 1.5e-4)


class TestKonvergensRutstorlek(unittest.TestCase):
    """Väggytor som inte ligger på cellgränser flyttas av rastreringen högst cell/2 i läge, så
    felet i SVF/VSC avtar med cellstorleken. Gaturum W = 21,3 m, H = 20 m, punkt x = 0."""

    def _svf_vsc(self, cell_m: float) -> tuple[float, float]:
        w = 21.3
        f = kanjon_falt(cell_m, w, HOJD, halv_x_m=R, halv_y_m=R)
        k = 128
        r = horisont_mipmap(f, np.zeros(k), np.zeros(k), np.zeros(k), riktningar_rad(k), R)
        svf = float(svf_fran_horisont(r.h_rad))
        kf = 128
        phi = halvplansriktningar(np.array([math.pi / 2]), kf)[0]
        # fasaden ligger vid x = -w/2; punkten 0,25 m utanför den (ej på cellgräns)
        rv = horisont_mipmap(
            f, np.full(kf, -w / 2 + 0.25), np.zeros(kf) + 0.1, np.zeros(kf), phi, R
        )
        return svf, float(vsc_fran_halvplansprofil(rv.h_rad))

    def test_fel_avtar_med_cellstorlek(self) -> None:
        svf_facit = svf_kanjon_facit(HOJD, 21.3 / 2, 21.3 / 2, R)
        vsc_facit = vsc_kanjon_facit(HOJD, 21.3 - 0.25, R)
        fel = {}
        for c in (4.0, 2.0, 1.0, 0.5, 0.25):
            s, v = self._svf_vsc(c)
            fel[c] = (abs(s - svf_facit), abs(v - vsc_facit))
        # Raster-flytten av väggytan är högst c/2, vilket ger SVF-fel <= ~ 0,0095 c / 0,5 ...
        for c, (es, ev) in fel.items():
            self.assertLess(es, 0.012 * c, msg=f"SVF c={c}: {es:.2e}")
            self.assertLess(ev, 0.014 * c, msg=f"VSC c={c}: {ev:.2e}")
        self.assertLess(fel[0.25][0], 3e-3)
        self.assertLess(fel[0.25][1], 4e-3)


def _blocktest_geometri() -> tuple[Hojdfalt, np.ndarray, np.ndarray]:
    """Block 30 m (Ö–V) x 20 m (N–S), höjd 15 m, centrerat i origo; punkter norr, öster och väster
    om det (där skuggan faller ett vårdagjämningsdygn i Stockholm)."""
    n, cell = 801, 1.0
    xc = (np.arange(n) - n // 2) * cell
    xx, yy = np.meshgrid(xc, -xc)
    z = np.zeros((n, n))
    z[(np.abs(xx) <= 15) & (np.abs(yy) <= 10)] = 15.0
    f = Hojdfalt(z, cell, -n * cell / 2, n * cell / 2)
    pts = (
        [(0.0, y) for y in (11.5, 14.5, 18.5, 25.5, 35.5, 50.5)]
        + [(x, 18.5) for x in (-20.5, -15.5, -10.5, 10.5, 20.5, 25.5)]
        + [(-30.5, 8.5), (30.5, -12.5)]
    )
    return f, np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def _bruteforce_timmar(
    f: Hojdfalt, px: float, py: float, sol_h: np.ndarray, sol_az: np.ndarray, steg_h: float
) -> float:
    """Oberoende strålgång: för varje tidssteg, gå mot solen i steg om 0,02 m och kontrollera
    om någon cell sticker upp över solstrålen. Delar ingen kod med horisontberäkningen."""
    s = np.arange(0.02, 300.0, 0.02)
    antal = 0
    for e, a in zip(sol_h, sol_az, strict=True):
        if e <= 0:
            continue
        xs, ys = px + s * math.sin(a), py + s * math.cos(a)
        j = np.floor((xs - f.x_min_m) / f.cell_m).astype(int)
        i = np.floor((f.y_max_m - ys) / f.cell_m).astype(int)
        ok = (j >= 0) & (j < f.bredd) & (i >= 0) & (i < f.hojd)
        z = np.where(ok, f.z_m[np.clip(i, 0, f.hojd - 1), np.clip(j, 0, f.bredd - 1)], -1e9)
        if np.all(z <= s * math.tan(e)):
            antal += 1
    return antal * steg_h


class TestSoltimmar(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.f, cls.x, cls.y = _blocktest_geometri()
        cls.steg_min = 5.0
        t = dygnsfonster_utc("2026-03-20", 18.07, cls.steg_min)
        cls.sh, cls.sa = solposition(t, 59.33, 18.07)
        cls.steg_h = cls.steg_min / 60.0
        cls.bf = np.array(
            [
                _bruteforce_timmar(cls.f, px, py, cls.sh, cls.sa, cls.steg_h)
                for px, py in zip(cls.x, cls.y, strict=True)
            ]
        )

    def _timmar(self, k: int) -> np.ndarray:
        hk = np.empty((k, self.x.size))
        for i, phi in enumerate(riktningar_rad(k)):
            r = horisont_mipmap(
                self.f, self.x, self.y, np.zeros(self.x.size), np.full(self.x.size, phi), 300.0
            )
            hk[i] = r.h_rad
        return soltimmar_fran_profiler(hk, self.sh, self.sa, self.steg_h)

    def test_dagens_langd_och_skuggade_punkter(self) -> None:
        dag = float(np.count_nonzero(self.sh > 0) * self.steg_h)
        self.assertAlmostEqual(dag, 12.0, delta=0.1)  # dagjämning: ca 12 h över horisonten
        self.assertEqual(float(self.bf.max()), dag)  # fria punkter får hela dagen
        self.assertLess(
            float(self.bf.min()), 1.0
        )  # punkten närmast norr om blocket sitter i skugga

    def test_mot_bruteforce_och_konvergens_i_k(self) -> None:
        medel: dict[int, float] = {}
        for k in (16, 64, 512):
            d = self._timmar(k) - self.bf
            medel[k] = float(np.abs(d).mean())
            # Skuggkanten tolkas via linjär interpolation mellan riktningar: fel ~ azimutsteget.
            tak = {16: 2.7, 64: 0.6, 512: 0.12}[k]
            self.assertLess(float(np.abs(d).max()), tak, msg=f"K={k}")
        self.assertLess(medel[64], medel[16])
        self.assertLess(medel[512], medel[64])
        self.assertLess(medel[512], 0.03)

    def test_fria_punkter_ger_hel_dag_utan_skuggor(self) -> None:
        h = np.zeros((64, 3))
        t = soltimmar_fran_profiler(h, self.sh, self.sa, self.steg_h)
        self.assertTrue(np.allclose(t, np.count_nonzero(self.sh > 0) * self.steg_h))

    def test_horisont_over_solen_hela_dagen_ger_noll(self) -> None:
        h = np.full((64, 2), math.radians(80.0))
        self.assertEqual(
            float(soltimmar_fran_profiler(h, self.sh, self.sa, self.steg_h).max()), 0.0
        )


class TestAckumulator(unittest.TestCase):
    def test_ordning_och_form_kontrolleras(self) -> None:
        a = SoltimmarAckumulator(8, np.array([0.5]), np.array([1.0]), 1.0, (2,))
        with self.assertRaises(MattFel):
            a.mata(1, np.zeros(2))
        a.mata(0, np.zeros(2))
        with self.assertRaises(MattFel):
            a.mata(1, np.zeros(3))
        with self.assertRaises(MattFel):
            a.timmar()

    def test_interpolation_ar_cirkulär(self) -> None:
        h = np.array([0.0, 0.0, 0.0, 1.0])
        # Mellan riktning 3 (1,0 rad) och riktning 0 (0,0 rad) över 2pi-gränsen.
        v = interpolera_horisont(h, np.array([1.75 * math.pi]))
        self.assertAlmostEqual(float(v[0]), 0.5, 12)

    def test_referens_och_mipmap_ger_samma_svf(self) -> None:
        f = kanjon_falt(1.0, 21.0, HOJD, halv_x_m=60.0, halv_y_m=60.0)
        k = 32
        a = horisont_referens(f, np.zeros(k), np.zeros(k), np.zeros(k), riktningar_rad(k), 60.0)
        b = horisont_mipmap(f, np.zeros(k), np.zeros(k), np.zeros(k), riktningar_rad(k), 60.0)
        self.assertLess(float(np.abs(a.h_rad - b.h_rad).max()), 1e-9)


if __name__ == "__main__":
    unittest.main()
