"""Tester för kildiffraktionen i Nord2000 (AV 1106/07 avsnitt 5.7.1, 5.7.2 och 5.7.5).

Facit kommer ur dokumentet självt (gränsvärden, schemat för vinklar, definitionen av D) eller
ur oberoende teori:

* Hjälpfunktionerna f och g jämförs med sin definition genom Fresnelintegralerna
  (Abramowitz & Stegun, Handbook of Mathematical Functions, 7.3.5–7.3.6), räknade med
  `scipy.special.fresnel`.
* Hård tunn halvplansskärm jämförs med Macdonalds exakta lösning för en punktkälla vid ett
  hårt halvplan (H. M. Macdonald, "A class of diffraction problems", Proc. London Math. Soc.
  (2) 14, 1915, s. 410–427; återgiven bl.a. i Bowman, Senior & Uslenghi, Electromagnetic and
  Acoustic Scattering by Simple Shapes, 1969, kap. 8). Formeln är här inte avläst ur källan
  utan prövas i `TestReferenslosning`: den uppfyller Helmholtz ekvation, randvillkoret på
  halvplanets båda sidor och har rätt källsingularitet, vilket bestämmer lösningen.
"""

from __future__ import annotations

import cmath
import importlib.util
import math
import unittest

import numpy as np

from buller.nord2000.kildiffraktion import (
    EPSILON_RAD,
    Kil,
    fresnel_f,
    fresnel_g,
    kil_rata_stralar,
    kildiffraktionskoefficient_icke_reflekterande,
    kildiffraktionskoefficient_impedans,
    kildiffraktionskoefficient_reflektionskoefficienter,
    kiltryck_icke_reflekterande,
    kiltryck_icke_reflekterande_delar,
    kiltryck_impedans,
    kiltryck_impedans_delar,
    kiltryck_reflektionskoefficienter,
    kiltryck_reflektionskoefficienter_delar,
    modifierade_vinklar,
    tecken,
    vinkelfunktion_a,
)
from buller.nord2000.mark import markimpedans, sfarisk_reflektion
from buller.typer import BullerFel

HAR_SCIPY = importlib.util.find_spec("scipy") is not None
C_M_S = 340.0
PI = math.pi
HARD = math.inf
STEG_RAD = 2.0 * EPSILON_RAD  # minsta steg över en gräns som ε-regeln inte jämnar ut

_GL_X, _GL_W = np.polynomial.legendre.leggauss(8)


def _macdonald_term(k: float, avstand_m: float, m: float) -> complex:
    """(jk/2)·∫ H₁⁽¹⁾(kR·cosh u) du från −∞ till m, med ∫ från −∞ till 0 = exp(jkR)/(2R)·(2/jk).

    Integralen från 0 till m räknas med sammansatt Gauss–Legendre på paneler med lika stort
    fassteg (högst 1 rad per panel, 8 punkter).
    """
    from scipy.special import hankel1  # type: ignore[import-untyped]

    cosh_m = math.cosh(m)
    antal = max(4, math.ceil(k * avstand_m * (cosh_m - 1.0)) + 4)
    kanter = np.arccosh(1.0 + np.arange(antal + 1) * (cosh_m - 1.0) / antal)
    if m < 0.0:
        kanter = -kanter
    a = kanter[:-1][:, None]
    b = kanter[1:][:, None]
    u = 0.5 * (b + a) + 0.5 * (b - a) * _GL_X[None, :]
    integral = complex(
        np.sum(hankel1(1, k * avstand_m * np.cosh(u)) * _GL_W[None, :] * 0.5 * (b - a))
    )
    return cmath.exp(1j * k * avstand_m) / (2.0 * avstand_m) + 0.5j * k * integral


def macdonald_hart_halvplan(
    k: float, r_m: float, phi_rad: float, r0_m: float, phi0_rad: float, z_m: float = 0.0
) -> complex:
    """Macdonalds lösning för punktkälla (r₀, φ₀, 0) och fältpunkt (r, φ, z) vid hårt halvplan.

    Cylinderkoordinater med kanten som axel och vinklarna mätta från halvplanets ena sida
    (0 ≤ φ ≤ 2π). Normering: fritt fält exp(jkR)/R.
    """
    summa = 0.0j
    for vinkel in (phi_rad - phi0_rad, phi_rad + phi0_rad):
        avstand = math.sqrt(
            r_m * r_m + r0_m * r0_m - 2.0 * r_m * r0_m * math.cos(vinkel) + z_m * z_m
        )
        m = math.asinh(2.0 * math.sqrt(r_m * r0_m) * math.cos(vinkel / 2.0) / avstand)
        summa += _macdonald_term(k, avstand, m)
    return summa


def _rel(a: complex, b: complex) -> float:
    return abs(a - b) / abs(b)


class TestHjalpfunktioner(unittest.TestCase):
    def test_tecken_ekv_388(self) -> None:
        self.assertEqual(tecken(2.5), 1.0)
        self.assertEqual(tecken(0.0), 0.0)
        self.assertEqual(tecken(-1e-300), -1.0)

    def test_f_och_g_i_noll_ar_en_halv(self) -> None:
        """f(0) = g(0) = 1/2 följer av definitionen (C(0) = S(0) = 0)."""
        self.assertAlmostEqual(fresnel_f(0.0), 0.5, delta=3e-5)
        self.assertAlmostEqual(fresnel_g(0.0), 0.5, delta=3e-5)

    def test_asymptot_over_fem_ekv_85_86(self) -> None:
        self.assertAlmostEqual(fresnel_f(8.0), 1.0 / (PI * 8.0), places=15)
        self.assertAlmostEqual(fresnel_g(8.0), 1.0 / (PI**2 * 512.0), places=15)

    def test_negativt_argument_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            fresnel_f(-0.1)
        with self.assertRaises(BullerFel):
            fresnel_g(-0.1)

    @unittest.skipUnless(HAR_SCIPY, "kräver scipy")
    def test_f_och_g_mot_fresnelintegralerna(self) -> None:
        """Tabell 4 och 5 mot f och g ur C och S (A&S 7.3.5–7.3.6). Uppmätt: ≤ 3,1·10⁻⁵."""
        from scipy.special import fresnel

        x = np.concatenate([np.linspace(0.0, 5.0, 5001)[:-1], np.linspace(5.0, 50.0, 2001)])
        s, c = fresnel(x)
        vinkel = PI * x * x / 2.0
        f_exakt = (0.5 - s) * np.cos(vinkel) - (0.5 - c) * np.sin(vinkel)
        g_exakt = (0.5 - c) * np.cos(vinkel) + (0.5 - s) * np.sin(vinkel)
        f_fel = max(
            abs(fresnel_f(float(xi)) - float(fi)) for xi, fi in zip(x, f_exakt, strict=True)
        )
        g_fel = max(
            abs(fresnel_g(float(xi)) - float(gi)) for xi, gi in zip(x, g_exakt, strict=True)
        )
        self.assertLess(f_fel, 4e-5)
        self.assertLess(g_fel, 4e-5)

    def test_sprang_i_x_lika_med_fem_ar_litet(self) -> None:
        """Polynomet och asymptoten möts inte exakt i x = 5. Uppmätt: 2,3·10⁻⁵ och 1,8·10⁻⁵."""
        self.assertLess(abs(fresnel_f(5.0 - 1e-12) - fresnel_f(5.0)), 3e-5)
        self.assertLess(abs(fresnel_g(5.0 - 1e-12) - fresnel_g(5.0)), 3e-5)

    def test_a_vid_skugggransen_ekv_82(self) -> None:
        """A(θ) → +π/2 när θ → π underifrån och −π/2 ovanifrån, för varje kilvinkel."""
        for beta in (PI, 1.3 * PI, 1.5 * PI, 2.0 * PI):
            self.assertAlmostEqual(vinkelfunktion_a(PI, beta), PI / 2.0, places=12)
            self.assertAlmostEqual(vinkelfunktion_a(PI + 1e-12, beta), -PI / 2.0, places=9)
            # θ = 0 ger A = (π/2)(1 − ν) och θ = 2β samma värde: |A| ≤ π/2 överallt.
            nu = PI / beta
            self.assertAlmostEqual(vinkelfunktion_a(0.0, beta), PI / 2.0 * (1.0 - nu), places=12)
            self.assertAlmostEqual(
                vinkelfunktion_a(2.0 * beta, beta), PI / 2.0 * (1.0 - nu), places=12
            )


class TestVinkelschema(unittest.TestCase):
    """Schemat sist i AV 1106/07 avsnitt 5.7.1."""

    def test_vinklar_inom_forutsattningen_andras_inte(self) -> None:
        self.assertEqual(modifierade_vinklar(4.0, 1.0, 5.0), (4.0, 1.0, 5.0))

    def test_mottagare_strax_under_ytan(self) -> None:
        beta = 1.5 * PI
        theta_s, theta_r, beta_ny = modifierade_vinklar(4.0, -0.2, beta)
        self.assertEqual(theta_r, 0.0)
        self.assertAlmostEqual(theta_s, 4.2)
        self.assertAlmostEqual(beta_ny, beta + 0.2)

    def test_mottagare_djupt_inne_i_kilen(self) -> None:
        beta = 1.5 * PI
        theta_s, theta_r, beta_ny = modifierade_vinklar(4.0, -2.0, beta)
        self.assertEqual(theta_r, 0.0)
        self.assertAlmostEqual(theta_s, 2.0 * PI - (beta - 4.0))
        self.assertEqual(beta_ny, 2.0 * PI)

    def test_kalla_under_kallsidans_yta(self) -> None:
        beta = 1.5 * PI
        self.assertEqual(modifierade_vinklar(5.0, 1.0, beta), (5.0, 1.0, 5.0))
        self.assertEqual(modifierade_vinklar(7.0, 1.0, beta), (2.0 * PI, 1.0, 2.0 * PI))

    def test_bada_stegen_i_foljd(self) -> None:
        """θ_R < 0 och därefter θ'_S > β': β' sätts till θ'_S."""
        beta = 1.2 * PI
        theta_s, theta_r, beta_ny = modifierade_vinklar(1.25 * PI, -0.1, beta)
        self.assertEqual(theta_r, 0.0)
        self.assertAlmostEqual(theta_s, 1.25 * PI + 0.1)
        self.assertAlmostEqual(beta_ny, theta_s)

    def test_fall_utanfor_schemat_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            modifierade_vinklar(1.0, 2.0, 1.5 * PI)  # θ_R > θ_S
        with self.assertRaises(BullerFel):
            modifierade_vinklar(2.0, 1.0, 0.9 * PI)  # β < π
        with self.assertRaises(BullerFel):
            modifierade_vinklar(2.0, 1.0, 2.1 * PI)  # β > 2π

    def test_trycket_anvander_de_modifierade_vinklarna(self) -> None:
        beta = 1.5 * PI
        utanfor = kil_rata_stralar(beta, 4.0, -0.2, 8.0, 12.0, C_M_S)
        inom = kil_rata_stralar(beta + 0.2, 4.2, 0.0, 8.0, 12.0, C_M_S)
        z = markimpedans(500.0, 200.0)
        self.assertEqual(
            kiltryck_impedans(500.0, utanfor, z, z), kiltryck_impedans(500.0, inom, z, z)
        )


class TestIndatakontroll(unittest.TestCase):
    def test_ogiltig_geometri_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            kil_rata_stralar(1.5 * PI, 4.0, 1.0, 0.0, 5.0, C_M_S)
        with self.assertRaises(BullerFel):
            kil_rata_stralar(1.5 * PI, 4.0, 1.0, 5.0, 5.0, 0.0)
        with self.assertRaises(BullerFel):
            Kil(1.5 * PI, 4.0, math.nan, 0.1, 0.05, 0.05, 30.0, 15.0, 15.0)

    def test_ogiltig_frekvens_och_impedans_ger_fel(self) -> None:
        kil = kil_rata_stralar(1.5 * PI, 4.0, 1.0, 5.0, 5.0, C_M_S)
        with self.assertRaises(BullerFel):
            kiltryck_icke_reflekterande(0.0, kil)
        with self.assertRaises(BullerFel):
            kiltryck_impedans(100.0, kil, 0.0, HARD)
        with self.assertRaises(BullerFel):
            kiltryck_reflektionskoefficienter(100.0, kil, math.nan, 1.0)

    def test_kalla_och_mottagare_i_samma_punkt_ger_fel(self) -> None:
        kil = kil_rata_stralar(1.5 * PI, 2.0, 2.0, 5.0, 5.0, C_M_S)
        with self.assertRaises(BullerFel):
            kiltryck_icke_reflekterande(100.0, kil)


@unittest.skipUnless(HAR_SCIPY, "kräver scipy")
class TestReferenslosning(unittest.TestCase):
    """Referensen prövas för sig: den ska vara en lösning till halvplansproblemet."""

    K = 2.0 * PI * 500.0 / C_M_S
    R0 = 3.0
    PHI0 = 1.3 * PI

    def _p(self, r: float, phi: float, z: float) -> complex:
        return macdonald_hart_halvplan(self.K, r, phi, self.R0, self.PHI0, z)

    def test_helmholtz_ekvation(self) -> None:
        """∇²p + k²p = 0 med centrala differenser (h = 2 mm) i tio punkter, även i skuggan."""
        rng = np.random.default_rng(1)
        h = 2.0e-3
        for _ in range(10):
            r = float(rng.uniform(0.5, 6.0))
            phi = float(rng.uniform(0.1, 2.0 * PI - 0.1))
            z = float(rng.uniform(-1.0, 1.0))
            p0 = self._p(r, phi, z)
            laplace = (
                (self._p(r + h, phi, z) - 2.0 * p0 + self._p(r - h, phi, z)) / h**2
                + (self._p(r + h, phi, z) - self._p(r - h, phi, z)) / (2.0 * h * r)
                + (self._p(r, phi + h, z) - 2.0 * p0 + self._p(r, phi - h, z)) / (h * r) ** 2
                + (self._p(r, phi, z + h) - 2.0 * p0 + self._p(r, phi, z - h)) / h**2
            )
            self.assertLess(abs(laplace + self.K**2 * p0) / (self.K**2 * abs(p0)), 1e-3)

    def test_randvillkor_pa_bada_sidor(self) -> None:
        """∂p/∂φ = 0 på φ = 0 och φ = 2π (ensidig differens av andra ordningen)."""
        h = 2.0e-3
        for yta, riktning in ((0.0, 1.0), (2.0 * PI, -1.0)):
            for r in (0.7, 2.0, 5.0):
                p0 = self._p(r, yta, 0.3)
                derivata = (
                    -3.0 * p0
                    + 4.0 * self._p(r, yta + riktning * h, 0.3)
                    - self._p(r, yta + 2.0 * riktning * h, 0.3)
                ) / (2.0 * h)
                self.assertLess(abs(derivata) / abs(p0), 1e-3)

    def test_kallsingularitet(self) -> None:
        """Nära källan är p − exp(jkR)/R begränsat (lika för R = 1 cm och 1 mm)."""
        rest = []
        for avstand in (1e-2, 1e-3):
            p = self._p(self.R0 + avstand, self.PHI0, 0.0)
            rest.append(p - cmath.exp(1j * self.K * avstand) / avstand)
        self.assertLess(abs(rest[0]), 0.05)
        self.assertLess(abs(rest[0] - rest[1]), 1e-3)


@unittest.skipUnless(HAR_SCIPY, "kräver scipy")
class TestHardTunnSkarm(unittest.TestCase):
    """pwedge med β = 2π och Z = ∞ mot Macdonalds exakta lösning."""

    def _storsta_avvikelse_db(self, frekvens_hz: float, antal: int) -> float:
        rng = np.random.default_rng(int(frekvens_hz))
        k = 2.0 * PI * frekvens_hz / C_M_S
        storst = 0.0
        for _ in range(antal):
            r_s = float(rng.uniform(0.5, 100.0))
            r_r = float(rng.uniform(0.5, 100.0))
            theta_s = float(rng.uniform(PI, 2.0 * PI))
            theta_r = float(rng.uniform(0.0, theta_s))
            kil = kil_rata_stralar(2.0 * PI, theta_s, theta_r, r_s, r_r, C_M_S)
            p = kiltryck_impedans(frekvens_hz, kil, HARD, HARD)
            exakt = macdonald_hart_halvplan(k, r_r, theta_r, r_s, theta_s)
            storst = max(storst, abs(20.0 * math.log10(abs(p) / abs(exakt))))
        return storst

    def test_slumpade_geometrier_i_skugga_och_ljus(self) -> None:
        """R_S, R_R i 0,5–100 m, alla vinklar. Uppmätt (400 geometrier per frekvens):
        25 Hz 0,20 dB, 100 Hz 0,14 dB, 500 Hz 0,007 dB, 2 kHz 0,003 dB, 10 kHz 0,001 dB."""
        for frekvens_hz, grans_db in (
            (25.0, 0.3),
            (100.0, 0.3),
            (500.0, 0.02),
            (2000.0, 0.02),
            (10000.0, 0.02),
        ):
            self.assertLess(self._storsta_avvikelse_db(frekvens_hz, 60), grans_db, frekvens_hz)

    def test_skarm_vid_vag(self) -> None:
        """Källa 0,5 m över mark 10 m framför en 3 m hög skärm, mottagare 1,5 m över mark 20 m
        bakom (utan mark). Uppmätt avvikelse i insättningsdämpning: högst 0,011 dB."""
        r_s = math.hypot(10.0, 2.5)
        r_r = math.hypot(20.0, 1.5)
        theta_s = 2.0 * PI - math.atan2(10.0, 2.5)
        theta_r = math.atan2(20.0, 1.5)
        kil = kil_rata_stralar(2.0 * PI, theta_s, theta_r, r_s, r_r, C_M_S)
        for frekvens_hz in (25.0, 63.0, 250.0, 1000.0, 4000.0, 10000.0):
            k = 2.0 * PI * frekvens_hz / C_M_S
            p = kiltryck_impedans(frekvens_hz, kil, HARD, HARD)
            exakt = macdonald_hart_halvplan(k, r_r, theta_r, r_s, theta_s)
            self.assertLess(abs(20.0 * math.log10(abs(p) / abs(exakt))), 0.02, frekvens_hz)
            self.assertLess(_rel(p, exakt), 5e-3, frekvens_hz)


class TestKontinuitet(unittest.TestCase):
    """Totalfältet över skugg- och reflexgränserna.

    Diffraktionstermen byter tecken över θ_n = π och den geometriska strålen tillkommer.
    Språnget som återstår är (f(0) + g(0) − 1) + j(f(0) − g(0)) gånger strålens belopp, alltså
    5·10⁻⁵ med tabellernas a₀ – det är närmepolynomens fel, inte metodens.
    """

    BETA = 1.6 * PI
    R_S = 7.0
    R_R = 11.0

    def _hopp(
        self, frekvens_hz: float, beta: float, theta_s_grans: float, theta_r: float
    ) -> tuple[float, float]:
        z_s = markimpedans(frekvens_hz, 200.0)
        z_r = markimpedans(frekvens_hz, 12.5)
        varden = []
        for steg in (-STEG_RAD, 0.0, STEG_RAD):
            kil = kil_rata_stralar(beta, theta_s_grans + steg, theta_r, self.R_S, self.R_R, C_M_S)
            varden.append(kiltryck_impedans(frekvens_hz, kil, z_s, z_r))
        return _rel(varden[2], varden[0]), _rel(varden[1], varden[0])

    def test_skugggransen(self) -> None:
        for frekvens_hz in (25.0, 250.0, 2000.0, 10000.0):
            over, pa = self._hopp(frekvens_hz, self.BETA, 0.3 + PI, 0.3)
            self.assertLess(over, 2.5e-4, frekvens_hz)
            self.assertLess(pa, 2.5e-4, frekvens_hz)

    def test_reflexgransen_i_mottagarsidans_yta(self) -> None:
        """θ₂ = θ_S + θ_R = π, olika impedans på de två ytorna."""
        for frekvens_hz in (25.0, 250.0, 2000.0, 10000.0):
            over, pa = self._hopp(frekvens_hz, self.BETA, PI - 0.3, 0.3)
            self.assertLess(over, 2.5e-4, frekvens_hz)
            self.assertLess(pa, 2.5e-4, frekvens_hz)

    def test_reflexgransen_i_kallsidans_yta(self) -> None:
        """θ₃ = 2β − (θ_S + θ_R) = π, olika impedans på de två ytorna."""
        theta_r = 0.9 * PI
        for frekvens_hz in (25.0, 250.0, 2000.0, 10000.0):
            over, pa = self._hopp(frekvens_hz, self.BETA, 2.0 * self.BETA - PI - theta_r, theta_r)
            self.assertLess(over, 2.5e-4, frekvens_hz)
            self.assertLess(pa, 2.5e-4, frekvens_hz)

    def test_tunn_hard_skarm_och_icke_reflekterande_kil(self) -> None:
        for frekvens_hz in (25.0, 1000.0):
            varden_hard = []
            varden_noll = []
            for steg in (-STEG_RAD, STEG_RAD):
                kil = kil_rata_stralar(2.0 * PI, 0.3 + PI + steg, 0.3, 7.0, 11.0, C_M_S)
                varden_hard.append(kiltryck_impedans(frekvens_hz, kil, HARD, HARD))
                varden_noll.append(kiltryck_icke_reflekterande(frekvens_hz, kil))
            self.assertLess(_rel(varden_hard[1], varden_hard[0]), 2.5e-4)
            self.assertLess(_rel(varden_noll[1], varden_noll[0]), 2.5e-4)

    def test_halva_faltet_pa_skugggransen(self) -> None:
        """På skugggränsen ger den icke-reflekterande kilen halva fria fältet (−6,02 dB):
        diffraktionstermen är −½·exp(jωτ)/ℓ och R₁ = ℓ."""
        for beta in (1.2 * PI, 2.0 * PI):
            kil = kil_rata_stralar(beta, 0.4 + PI, 0.4, 13.0, 29.0, C_M_S)
            delar = kiltryck_icke_reflekterande_delar(800.0, kil)
            fritt = cmath.exp(1j * 2.0 * PI * 800.0 * kil.gangtid_s) / kil.vag_m
            self.assertLess(_rel(delar.direkt, fritt), 1e-9)
            self.assertLess(_rel(delar.diffrakterat, -0.5 * fritt), 1e-4)
            self.assertAlmostEqual(20.0 * math.log10(abs(delar.totalt / fritt)), -6.02, delta=0.01)

    def test_givna_koefficienter_ar_kontinuerliga_over_reflexgransen(self) -> None:
        """TC-101327 2.3: med samma Q i diffraktionsterm och speglad stråle (förvalet)."""
        varden = []
        for steg in (-STEG_RAD, STEG_RAD):
            kil = kil_rata_stralar(self.BETA, PI - 0.3 + steg, 0.3, self.R_S, self.R_R, C_M_S)
            varden.append(kiltryck_reflektionskoefficienter(500.0, kil, 0.8, -0.369))
        self.assertLess(_rel(varden[1], varden[0]), 2.5e-4)

    def test_tryckt_tilldelning_i_ekv_80_ger_sprang(self) -> None:
        """Skälet till tolkningen Q₂ = Q_R, Q₃ = Q_S: med den tryckta tilldelningen (Q₂ = Q_S,
        Q₃ = Q_R) och ekv. (89) oförändrad hoppar fältet över reflexgränsen θ₂ = π.
        Uppmätt med hård källsida och σ = 12,5 på mottagarsidan vid 250 Hz: faktor 2,9 (9 dB)."""
        frekvens_hz = 250.0
        z_s = markimpedans(frekvens_hz, 20000.0)
        z_r = markimpedans(frekvens_hz, 12.5)
        valda = []
        tryckta = []
        for steg in (-STEG_RAD, STEG_RAD):
            theta_s = PI - 0.3 + steg
            kil = kil_rata_stralar(self.BETA, theta_s, 0.3, self.R_S, self.R_R, C_M_S)
            summatid = kil.gangtid_kalla_s + kil.gangtid_mottagare_s
            q_s = sfarisk_reflektion(frekvens_hz, summatid, min(self.BETA - theta_s, PI / 2), z_s)
            q_r = sfarisk_reflektion(frekvens_hz, summatid, 0.3, z_r)
            enligt_vald = kiltryck_impedans_delar(frekvens_hz, kil, z_s, z_r)
            ombytt = kiltryck_reflektionskoefficienter_delar(frekvens_hz, kil, q_r, q_s)
            valda.append(enligt_vald.totalt)
            tryckta.append(ombytt.diffrakterat + enligt_vald.direkt + enligt_vald.reflekterat)
        self.assertLess(_rel(valda[1], valda[0]), 2.5e-4)
        self.assertGreater(_rel(tryckta[1], tryckta[0]), 0.5)

    def test_exakt_pa_gransen_ar_andligt_ekv_epsilon(self) -> None:
        """θ₁ = π exakt: utan ε-regeln blir Sign(B) = 0 och termen faller bort."""
        kil = kil_rata_stralar(2.0 * PI, 1.5 * PI, 0.5 * PI, 10.0, 10.0, C_M_S)
        delar = kiltryck_icke_reflekterande_delar(1000.0, kil)
        self.assertGreater(abs(delar.diffrakterat), 0.4 / kil.vag_m)
        self.assertGreater(abs(delar.direkt), 0.0)


class TestReciprocitet(unittest.TestCase):
    """Ombytt källa och mottagare: θ_S ↔ β − θ_R, R_S ↔ R_R, τ_S ↔ τ_R, Z_S ↔ Z_R."""

    @staticmethod
    def _ombytt(kil: Kil) -> Kil:
        return Kil(
            beta_rad=kil.beta_rad,
            theta_s_rad=kil.beta_rad - kil.theta_r_rad,
            theta_r_rad=kil.beta_rad - kil.theta_s_rad,
            gangtid_s=kil.gangtid_s,
            gangtid_kalla_s=kil.gangtid_mottagare_s,
            gangtid_mottagare_s=kil.gangtid_kalla_s,
            vag_m=kil.vag_m,
            vag_kalla_m=kil.vag_mottagare_m,
            vag_mottagare_m=kil.vag_kalla_m,
        )

    def test_alla_tre_varianter(self) -> None:
        """Uppmätt största relativa skillnad över 300 slumpade fall: se loggen (≤ 10⁻¹¹)."""
        rng = np.random.default_rng(11)
        for _ in range(300):
            beta = float(rng.uniform(1.02 * PI, 2.0 * PI))
            theta_s = float(rng.uniform(0.0, beta))
            theta_r = float(rng.uniform(0.0, theta_s))
            frekvens_hz = float(10.0 ** rng.uniform(1.4, 4.0))
            kil = kil_rata_stralar(
                beta, theta_s, theta_r, float(rng.uniform(0.3, 80.0)),
                float(rng.uniform(0.3, 80.0)), C_M_S,
            )  # fmt: skip
            om = self._ombytt(kil)
            z_s = markimpedans(frekvens_hz, float(10.0 ** rng.uniform(1.0, 4.3)))
            z_r = markimpedans(frekvens_hz, float(10.0 ** rng.uniform(1.0, 4.3)))
            self.assertLess(
                _rel(
                    kiltryck_impedans(frekvens_hz, om, z_r, z_s),
                    kiltryck_impedans(frekvens_hz, kil, z_s, z_r),
                ),
                1e-9,
            )
            self.assertLess(
                _rel(
                    kiltryck_reflektionskoefficienter(frekvens_hz, om, -0.2, 0.7),
                    kiltryck_reflektionskoefficienter(frekvens_hz, kil, 0.7, -0.2),
                ),
                1e-9,
            )
            self.assertLess(
                _rel(
                    kiltryck_icke_reflekterande(frekvens_hz, om),
                    kiltryck_icke_reflekterande(frekvens_hz, kil),
                ),
                1e-9,
            )

    def test_med_skilda_gangtider_for_tva_kanter(self) -> None:
        """τ och ℓ definierade som för tjock skärm (5.7.4): τ ≠ τ_S + τ_R."""
        kil = Kil(1.5 * PI, 4.2, 0.4, 0.2, 0.03, 0.09, 68.0, 10.2, 30.6)
        om = self._ombytt(kil)
        d_fram = kildiffraktionskoefficient_impedans(630.0, kil, HARD, markimpedans(630.0, 80.0))
        d_bak = kildiffraktionskoefficient_impedans(630.0, om, markimpedans(630.0, 80.0), HARD)
        self.assertLess(_rel(d_bak, d_fram), 1e-9)


class TestIngenKil(unittest.TestCase):
    """Kilvinkeln π: de två ytorna bildar ett plan.

    AV 1106/07 anger att lösningen bara gäller för β > π. I gränsen β = π är A(θ₄) = −A(θ₁)
    och A(θ₃) = −A(θ₂), så termerna i ekv. (78) tar ut varandra parvis när Q₄ = Q₁ och
    Q₃ = Q₂. Det gäller för hårda ytor, där fältet alltså blir direkt + speglad stråle utan
    diffraktion. För ändlig impedans och för den icke-reflekterande kilen säger metoden inte
    att diffraktionen försvinner, och den gör det inte heller.
    """

    FALL = ((0.8 * PI, 0.1 * PI), (0.95 * PI, 0.02 * PI), (0.55 * PI, 0.3 * PI), (PI, 0.0))

    def test_hard_plan_yta_ger_direkt_och_speglad_strale(self) -> None:
        for frekvens_hz in (100.0, 1000.0):
            for theta_s, theta_r in self.FALL[:3]:
                kil = kil_rata_stralar(PI, theta_s, theta_r, 20.0, 30.0, C_M_S)
                delar = kiltryck_impedans_delar(frekvens_hz, kil, HARD, HARD)
                self.assertLess(abs(delar.diffrakterat) * kil.vag_m, 1e-14)
                # Spegelkällemetoden för ett hårt plan: exp(jkR₁)/R₁ + exp(jkR₂)/R₂.
                k = 2.0 * PI * frekvens_hz / C_M_S
                x_s, y_s = 20.0 * math.cos(theta_s), 20.0 * math.sin(theta_s)
                x_r, y_r = 30.0 * math.cos(theta_r), 30.0 * math.sin(theta_r)
                r_1 = math.hypot(x_s - x_r, y_s - y_r)
                r_2 = math.hypot(x_s - x_r, y_s + y_r)
                bild = cmath.exp(1j * k * r_1) / r_1 + cmath.exp(1j * k * r_2) / r_2
                self.assertLess(_rel(delar.totalt, bild), 1e-11)

    def test_gransvardet_nas_kontinuerligt(self) -> None:
        kil = kil_rata_stralar(PI + 1e-7, 0.8 * PI, 0.1 * PI, 20.0, 30.0, C_M_S)
        delar = kiltryck_impedans_delar(1000.0, kil, HARD, HARD)
        self.assertLess(abs(delar.diffrakterat) * kil.vag_m, 1e-6)

    def test_andlig_impedans_ger_en_rest(self) -> None:
        """Q_S räknas med strykvinkeln π − θ_S och Q_R med θ_R, så Q₃ ≠ Q₂ och Q₄ ≠ 1.
        Uppmätt för σ = 200 kNs/m⁴, R_S = 20 m, R_R = 30 m: |p_diffr|/|direkt + speglad| =
        0,012 (100 Hz) och 0,001 (1 kHz) vid θ_S = 0,8π, θ_R = 0,1π; 0,125 och 0,022 vid
        θ_S = 0,95π, θ_R = 0,02π (−1,16 och −0,12 dB i totalnivå)."""
        for frekvens_hz, grans in ((100.0, 0.2), (1000.0, 0.05)):
            z = markimpedans(frekvens_hz, 200.0)
            for theta_s, theta_r in self.FALL[:2]:
                kil = kil_rata_stralar(PI, theta_s, theta_r, 20.0, 30.0, C_M_S)
                delar = kiltryck_impedans_delar(frekvens_hz, kil, z, z)
                kvot = abs(delar.diffrakterat) / abs(delar.direkt + delar.reflekterat)
                self.assertGreater(kvot, 1e-4)
                self.assertLess(kvot, grans)

    def test_icke_reflekterande_kil_ger_inte_fritt_falt(self) -> None:
        """pwedge0 har bara termen n = 1, som inte tas ut av någon annan. Uppmätt vid 1 kHz:
        |p|/|direkt| = 1,023 (θ_S = 0,8π, θ_R = 0,1π) och 0,885 (0,95π, 0,02π)."""
        for theta_s, theta_r in self.FALL[:2]:
            kil = kil_rata_stralar(PI, theta_s, theta_r, 20.0, 30.0, C_M_S)
            delar = kiltryck_icke_reflekterande_delar(1000.0, kil)
            self.assertGreater(abs(delar.diffrakterat), 0.01 * abs(delar.direkt))

    def test_sammanfallande_reflexgranser_ger_fel(self) -> None:
        """β = π och θ_S + θ_R = π: θ₂ = θ₃ = π, och ε-regeln kan inte tillämpas på båda."""
        kil = kil_rata_stralar(PI, 0.6 * PI, 0.4 * PI, 20.0, 30.0, C_M_S)
        with self.assertRaises(BullerFel):
            kiltryck_impedans(1000.0, kil, HARD, HARD)
        # Den icke-reflekterande kilen har bara θ₁ och berörs inte.
        self.assertTrue(cmath.isfinite(kiltryck_icke_reflekterande(1000.0, kil)))


class TestDiffraktionskoefficient(unittest.TestCase):
    """AV 1106/07 avsnitt 5.7.2: p = D·exp(jωτ)/ℓ (ekv. 92)."""

    def test_definitionen_for_alla_varianter(self) -> None:
        kil = Kil(1.7 * PI, 4.9, 0.6, 0.25, 0.04, 0.11, 85.0, 13.6, 37.4)
        frekvens_hz = 1250.0
        fas = cmath.exp(1j * 2.0 * PI * frekvens_hz * kil.gangtid_s) / kil.vag_m
        z_s = markimpedans(frekvens_hz, 500.0)
        z_r = markimpedans(frekvens_hz, 31.5)
        par = (
            (
                kildiffraktionskoefficient_impedans(frekvens_hz, kil, z_s, z_r),
                kiltryck_impedans(frekvens_hz, kil, z_s, z_r),
            ),
            (
                kildiffraktionskoefficient_reflektionskoefficienter(frekvens_hz, kil, 0.5, 0.9),
                kiltryck_reflektionskoefficienter(frekvens_hz, kil, 0.5, 0.9),
            ),
            (
                kildiffraktionskoefficient_icke_reflekterande(frekvens_hz, kil),
                kiltryck_icke_reflekterande(frekvens_hz, kil),
            ),
        )
        for d, p in par:
            self.assertLess(_rel(d * fas, p), 1e-12)

    def test_hard_impedans_ar_samma_som_koefficienten_ett(self) -> None:
        """Z = ∞ ger Q = 1 (avsnitt 5.7.4), alltså samma som givna koefficienter 1 och 1."""
        for theta_s, theta_r in ((5.0, 0.4), (2.9, 0.1), (4.9, 4.6)):
            kil = kil_rata_stralar(1.6 * PI, theta_s, theta_r, 9.0, 14.0, C_M_S)
            self.assertEqual(
                kiltryck_impedans(400.0, kil, HARD, HARD),
                kiltryck_reflektionskoefficienter(400.0, kil, 1.0, 1.0),
            )

    def test_icke_reflekterande_ar_forsta_termen(self) -> None:
        """Koefficienterna 0 och 0 lämnar bara n = 1 i ekv. (78), alltså ekv. (105)."""
        kil = kil_rata_stralar(1.6 * PI, 5.0, 0.4, 9.0, 14.0, C_M_S)
        self.assertEqual(
            kiltryck_reflektionskoefficienter(400.0, kil, 0.0, 0.0),
            kiltryck_icke_reflekterande(400.0, kil),
        )

    def test_djup_skugga_avtar_med_frekvensen(self) -> None:
        """I skuggan avtar |D| monotont med frekvensen och närmar sig −10 dB per dekad
        (|A_D(B)| → 1/(π·B) med B ∝ √f, ekv. 83–85)."""
        kil = kil_rata_stralar(2.0 * PI, 1.8 * PI, 0.2 * PI, 15.0, 25.0, C_M_S)
        niva = [
            20.0 * math.log10(abs(kildiffraktionskoefficient_icke_reflekterande(f, kil)))
            for f in (100.0, 1000.0, 10000.0)
        ]
        self.assertLess(niva[1], niva[0])
        self.assertAlmostEqual(niva[2] - niva[1], -10.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
