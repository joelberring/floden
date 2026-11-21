"""Tester för dubbel diffraktion i Nord2000 (AV 1106/07 avsnitt 5.7.3 och 5.7.4, TC-101327 2.4–2.5).

Facit kommer ur dokumentet självt (argumentlistorna i ekv. 97–98 och 102–103, faktorn 0,5,
hård ovansida) eller ur oberoende teori:

* Reciprocitet: byter källa och mottagare plats byter också kilarna plats och den viktigaste
  kanten nummer, och trycket ska vara detsamma.
* En kant långt under siktlinjen från den andra kanten skärmar inte: fältet ska gå mot
  fältet från en ensam kil. Ligger kanten precis på siktlinjen halveras fältet (−6,02 dB),
  som på varje skugggräns.
* En punktkälla i toppen på en hård kil med yttervinkeln β ger fältet (2π/β)·exp(jkR)/R.
  Den lösningen är radiellt symmetrisk, uppfyller därför randvillkoret på båda kilytorna, och
  styrkan följer av att hela källflödet går ut genom andelen β/2π av sfären.

Geometrin i testerna har räta strålar. Vinklarna räknas av `_geometri` enligt punktlistorna
i 5.7.3 och 5.7.4.
"""

from __future__ import annotations

import cmath
import math
import unittest

import numpy as np

from buller.nord2000.kildiffraktion import (
    Kil,
    kil_rata_stralar,
    kildiffraktionskoefficient_impedans,
    kiltryck_impedans,
)
from buller.nord2000.kildiffraktion_dubbel import (
    FAKTOR_TJOCK_SKARM,
    FAKTOR_TVA_KILAR,
    TvaKanter,
    Viktigast,
    delkilar,
    dubbel_diffraktion,
    tjock_skarm_tryck_impedans,
    tjock_skarm_tryck_impedans_delar,
    tjock_skarm_tryck_reflektionskoefficienter,
    tjock_skarm_tryck_reflektionskoefficienter_delar,
    tva_kanter_rata_stralar,
    tva_kilar_tryck_impedans,
    tva_kilar_tryck_impedans_delar,
    tva_kilar_tryck_reflektionskoefficienter,
)
from buller.nord2000.mark import markimpedans
from buller.typer import BullerFel

C_M_S = 340.0
PI = math.pi
HARD = math.inf
NED = -PI / 2.0  # riktningen rakt ned (kilyta på en tunn eller rätvinklig skärm)
BADA: tuple[Viktigast, Viktigast] = ("forsta", "andra")

Punkt = tuple[float, float]


def _avstand(a: Punkt, b: Punkt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _vinkel(topp: Punkt, punkt: Punkt, yta_mottagarsida_rad: float) -> float:
    """Diffraktionsvinkeln för `punkt` sedd från `topp`, mätt från mottagarsidans yta (0–2π)."""
    riktning = math.atan2(punkt[1] - topp[1], punkt[0] - topp[0])
    vinkel = (riktning - yta_mottagarsida_rad) % (2.0 * PI)
    return 0.0 if 2.0 * PI - vinkel < 1e-13 else vinkel


def _over_linjen(punkt: Punkt, a: Punkt, b: Punkt) -> bool:
    z_linje = a[1] + (b[1] - a[1]) * (punkt[0] - a[0]) / (b[0] - a[0])
    return punkt[1] > z_linje


def _kilvinkel(yta_kallsida_rad: float, yta_mottagarsida_rad: float) -> float:
    beta = (yta_kallsida_rad - yta_mottagarsida_rad) % (2.0 * PI)
    return 2.0 * PI if beta == 0.0 else beta


def _geometri(
    kalla: Punkt,
    topp_1: Punkt,
    topp_2: Punkt,
    mottagare: Punkt,
    ytor_1: tuple[float, float],
    ytor_2: tuple[float, float],
) -> TvaKanter:
    """Indata enligt punktlistorna i 5.7.3–5.7.4 för räta strålar.

    `ytor_n` är riktningarna (rad, från x-axeln) för kil n:s yta mot mottagaren och mot källan,
    sedda från kilens topp. θ_1R mäts mot T₂ om T₂ ligger över linjen T₁–R, annars mot R;
    θ_2S mäts mot T₁ om T₁ ligger över linjen T₂–S, annars mot S.
    """
    mal_1 = topp_2 if _over_linjen(topp_2, topp_1, mottagare) else mottagare
    mal_2 = topp_1 if _over_linjen(topp_1, topp_2, kalla) else kalla
    return tva_kanter_rata_stralar(
        beta_1_rad=_kilvinkel(ytor_1[1], ytor_1[0]),
        theta_1s_rad=_vinkel(topp_1, kalla, ytor_1[0]),
        theta_1r_rad=_vinkel(topp_1, mal_1, ytor_1[0]),
        beta_2_rad=_kilvinkel(ytor_2[1], ytor_2[0]),
        theta_2s_rad=_vinkel(topp_2, mal_2, ytor_2[0]),
        theta_2r_rad=_vinkel(topp_2, mottagare, ytor_2[0]),
        vag_kalla_m=_avstand(kalla, topp_1),
        vag_mitten_m=_avstand(topp_1, topp_2),
        vag_mottagare_m=_avstand(topp_2, mottagare),
        ljudhastighet_m_s=C_M_S,
    )


def _tva_tunna(kalla: Punkt, topp_1: Punkt, topp_2: Punkt, mottagare: Punkt) -> TvaKanter:
    """Två tunna lodräta skärmar (β = 2π)."""
    return _geometri(kalla, topp_1, topp_2, mottagare, (NED, NED), (NED, NED))


def _ratvinklig(kalla: Punkt, topp_1: Punkt, topp_2: Punkt, mottagare: Punkt) -> TvaKanter:
    """Tjock skärm med lodräta sidor: ovansidan T₁–T₂ är yta i båda kilarna."""
    mot_t2 = math.atan2(topp_2[1] - topp_1[1], topp_2[0] - topp_1[0])
    mot_t1 = math.atan2(topp_1[1] - topp_2[1], topp_1[0] - topp_2[0])
    return _geometri(kalla, topp_1, topp_2, mottagare, (mot_t2, NED), (NED, mot_t1))


def _ensam_kil(
    frekvens_hz: float, kalla: Punkt, topp: Punkt, mottagare: Punkt, ytor: tuple[float, float]
) -> complex:
    kil = kil_rata_stralar(
        _kilvinkel(ytor[1], ytor[0]),
        _vinkel(topp, kalla, ytor[0]),
        _vinkel(topp, mottagare, ytor[0]),
        _avstand(kalla, topp),
        _avstand(topp, mottagare),
        C_M_S,
    )
    return kiltryck_impedans(frekvens_hz, kil, HARD, HARD)


def _rel(a: complex, b: complex) -> float:
    return abs(a - b) / abs(b)


def _db(a: complex, b: complex) -> float:
    return 20.0 * math.log10(abs(a) / abs(b))


def _spegla(g: TvaKanter) -> TvaKanter:
    """Samma geometri med källa och mottagare ombytta (kil 1 ↔ kil 2, θ_S ↔ β − θ_R)."""
    return TvaKanter(
        beta_1_rad=g.beta_2_rad,
        theta_1s_rad=g.beta_2_rad - g.theta_2r_rad,
        theta_1r_rad=g.beta_2_rad - g.theta_2s_rad,
        beta_2_rad=g.beta_1_rad,
        theta_2s_rad=g.beta_1_rad - g.theta_1r_rad,
        theta_2r_rad=g.beta_1_rad - g.theta_1s_rad,
        gangtid_kalla_s=g.gangtid_mottagare_s,
        gangtid_mitten_s=g.gangtid_mitten_s,
        gangtid_mottagare_s=g.gangtid_kalla_s,
        vag_kalla_m=g.vag_mottagare_m,
        vag_mitten_m=g.vag_mitten_m,
        vag_mottagare_m=g.vag_kalla_m,
    )


def _andra(viktigast: Viktigast) -> Viktigast:
    return "andra" if viktigast == "forsta" else "forsta"


EXEMPEL = TvaKanter(
    beta_1_rad=1.7 * PI,
    theta_1s_rad=1.45 * PI,
    theta_1r_rad=0.31 * PI,
    beta_2_rad=1.6 * PI,
    theta_2s_rad=1.38 * PI,
    theta_2r_rad=0.22 * PI,
    gangtid_kalla_s=0.031,
    gangtid_mitten_s=0.017,
    gangtid_mottagare_s=0.059,
    vag_kalla_m=10.5,
    vag_mitten_m=5.9,
    vag_mottagare_m=20.3,
)


class TestIndata(unittest.TestCase):
    def test_hela_gangtiden_och_strackan_ekv_95_96(self) -> None:
        self.assertAlmostEqual(EXEMPEL.gangtid_s, 0.031 + 0.017 + 0.059, places=15)
        self.assertAlmostEqual(EXEMPEL.vag_m, 10.5 + 5.9 + 20.3, places=12)

    def test_rata_stralar(self) -> None:
        g = tva_kanter_rata_stralar(5.0, 4.0, 1.0, 6.0, 4.5, 0.5, 34.0, 68.0, 102.0, C_M_S)
        self.assertAlmostEqual(g.gangtid_kalla_s, 0.1, places=15)
        self.assertAlmostEqual(g.gangtid_mitten_s, 0.2, places=15)
        self.assertAlmostEqual(g.gangtid_mottagare_s, 0.3, places=15)
        self.assertEqual((g.beta_1_rad, g.theta_1s_rad, g.theta_1r_rad), (5.0, 4.0, 1.0))
        self.assertEqual((g.beta_2_rad, g.theta_2s_rad, g.theta_2r_rad), (6.0, 4.5, 0.5))

    def test_argumentlistorna_i_ekv_97_och_102(self) -> None:
        """Första kanten viktigast: kil 1 över hela vägen, kil 2 från T₁ till R."""
        g = EXEMPEL
        kil_1, kil_2 = delkilar(g, "forsta")
        self.assertEqual(
            kil_1,
            Kil(
                g.beta_1_rad, g.theta_1s_rad, g.theta_1r_rad,
                0.031 + 0.017 + 0.059, 0.031, 0.017 + 0.059,
                10.5 + 5.9 + 20.3, 10.5, 5.9 + 20.3,
            ),
        )  # fmt: skip
        self.assertEqual(
            kil_2,
            Kil(
                g.beta_2_rad, g.theta_2s_rad, g.theta_2r_rad,
                0.017 + 0.059, 0.017, 0.059,
                5.9 + 20.3, 5.9, 20.3,
            ),
        )  # fmt: skip

    def test_argumentlistorna_i_ekv_98_och_103(self) -> None:
        """Andra kanten viktigast: kil 1 från S till T₂, kil 2 över hela vägen."""
        g = EXEMPEL
        kil_1, kil_2 = delkilar(g, "andra")
        self.assertEqual(
            kil_1,
            Kil(
                g.beta_1_rad, g.theta_1s_rad, g.theta_1r_rad,
                0.031 + 0.017, 0.031, 0.017,
                10.5 + 5.9, 10.5, 5.9,
            ),
        )  # fmt: skip
        self.assertEqual(
            kil_2,
            Kil(
                g.beta_2_rad, g.theta_2s_rad, g.theta_2r_rad,
                0.031 + 0.017 + 0.059, 0.031 + 0.017, 0.059,
                10.5 + 5.9 + 20.3, 10.5 + 5.9, 20.3,
            ),
        )  # fmt: skip

    def test_ogiltig_indata_ger_fel(self) -> None:
        bas = dict(
            beta_1_rad=5.0, theta_1s_rad=4.0, theta_1r_rad=1.0,
            beta_2_rad=6.0, theta_2s_rad=4.5, theta_2r_rad=0.5,
            gangtid_kalla_s=0.1, gangtid_mitten_s=0.2, gangtid_mottagare_s=0.3,
            vag_kalla_m=34.0, vag_mitten_m=68.0, vag_mottagare_m=102.0,
        )  # fmt: skip
        TvaKanter(**bas)
        for namn, varde in (
            ("vag_mitten_m", 0.0),  # sammanfallande kanter
            ("gangtid_mitten_s", 0.0),
            ("vag_kalla_m", -1.0),
            ("gangtid_mottagare_s", math.nan),
            ("theta_2s_rad", math.inf),
        ):
            with self.subTest(namn=namn), self.assertRaises(BullerFel):
                TvaKanter(**{**bas, namn: varde})
        with self.assertRaises(BullerFel):
            tva_kanter_rata_stralar(5.0, 4.0, 1.0, 6.0, 4.5, 0.5, 34.0, 68.0, 102.0, 0.0)

    def test_okand_viktigast_och_frekvens_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            delkilar(EXEMPEL, "bada")  # type: ignore[arg-type]
        for frekvens in (0.0, -100.0, math.inf, math.nan):
            with self.subTest(frekvens=frekvens), self.assertRaises(BullerFel):
                tva_kilar_tryck_impedans(frekvens, EXEMPEL, HARD, HARD, HARD, HARD, "forsta")

    def test_fel_fran_kildiffraktionen_gar_vidare(self) -> None:
        """En kilvinkel under π är utanför 5.7.1 och ska inte ge ett tyst värde."""
        g = TvaKanter(**{**EXEMPEL.__dict__, "beta_2_rad": 0.8 * PI, "theta_2s_rad": 0.7 * PI})
        with self.assertRaises(BullerFel):
            tva_kilar_tryck_impedans(500.0, g, HARD, HARD, HARD, HARD, "forsta")


class TestSammansattning(unittest.TestCase):
    """Ekv. (97)–(98) och (102)–(103) räknade för hand ur Dwedge."""

    def test_tva_kilar_ar_produkten_av_dwedge(self) -> None:
        f = 630.0
        z = [markimpedans(f, sigma) for sigma in (200.0, 12.5, 2000.0, 80.0)]
        omega = 2.0 * PI * f
        for viktigast in BADA:
            kil_1, kil_2 = delkilar(EXEMPEL, viktigast)
            d_1 = kildiffraktionskoefficient_impedans(f, kil_1, z[0], z[1])
            d_2 = kildiffraktionskoefficient_impedans(f, kil_2, z[2], z[3])
            vantat = d_1 * d_2 * cmath.exp(1j * omega * EXEMPEL.gangtid_s) / EXEMPEL.vag_m
            delar = tva_kilar_tryck_impedans_delar(f, EXEMPEL, z[0], z[1], z[2], z[3], viktigast)
            self.assertEqual(delar.d_1, d_1)
            self.assertEqual(delar.d_2, d_2)
            self.assertLess(_rel(delar.tryck, vantat), 1e-14)
            self.assertEqual(
                tva_kilar_tryck_impedans(f, EXEMPEL, z[0], z[1], z[2], z[3], viktigast),
                delar.tryck,
            )

    def test_exponenten_ar_j_omega_tau_tc_2_4(self) -> None:
        """p̂·ℓ·exp(−jωτ) = D̂₁·D̂₂ med τ = τ_S + τ_M + τ_R, även när τ ≠ ℓ/c."""
        f = 1000.0
        for viktigast in BADA:
            delar = tva_kilar_tryck_impedans_delar(f, EXEMPEL, HARD, HARD, HARD, HARD, viktigast)
            fas = cmath.exp(-1j * 2.0 * PI * f * (0.031 + 0.017 + 0.059))
            self.assertLess(
                _rel(delar.tryck * (10.5 + 5.9 + 20.3) * fas, delar.d_1 * delar.d_2), 1e-13
            )

    def test_tjock_skarm_ar_halva_tvakilstrycket_med_hard_ovansida(self) -> None:
        """Ekv. (102)–(103) är ekv. (97)–(98) med Z_1R = Z_2S = ∞ och faktorn 0,5."""
        f = 400.0
        z_s, z_r = markimpedans(f, 200.0), markimpedans(f, 20.0)
        for viktigast in BADA:
            tjock = tjock_skarm_tryck_impedans_delar(f, EXEMPEL, z_s, z_r, viktigast)
            kilar = tva_kilar_tryck_impedans_delar(f, EXEMPEL, z_s, HARD, HARD, z_r, viktigast)
            self.assertEqual(tjock.d_1, kilar.d_1)
            self.assertEqual(tjock.d_2, kilar.d_2)
            self.assertLess(_rel(tjock.tryck, 0.5 * kilar.tryck), 1e-15)
            self.assertEqual(
                tjock_skarm_tryck_impedans(f, EXEMPEL, z_s, z_r, viktigast), tjock.tryck
            )
        self.assertEqual(FAKTOR_TVA_KILAR, 1.0)
        self.assertEqual(FAKTOR_TJOCK_SKARM, 0.5)

    def test_harda_ytor_ar_q_lika_med_ett(self) -> None:
        f = 250.0
        for viktigast in BADA:
            self.assertEqual(
                tva_kilar_tryck_reflektionskoefficienter(f, EXEMPEL, 1.0, 1.0, 1.0, 1.0, viktigast),
                tva_kilar_tryck_impedans(f, EXEMPEL, HARD, HARD, HARD, HARD, viktigast),
            )
            self.assertEqual(
                tjock_skarm_tryck_reflektionskoefficienter(f, EXEMPEL, 1.0, 1.0, 1.0, viktigast),
                tjock_skarm_tryck_impedans(f, EXEMPEL, HARD, HARD, viktigast),
            )

    def test_q_mitten_gar_in_i_bada_kilarna(self) -> None:
        """TC-101327 G.3: ovansidans koefficient är Q_R i kil 1 och Q_S i kil 2."""
        f = 800.0
        q_s, q_m, q_r = 0.6, -0.2, 0.3
        for viktigast in BADA:
            tjock = tjock_skarm_tryck_reflektionskoefficienter_delar(
                f, EXEMPEL, q_s, q_m, q_r, viktigast
            )
            kilar = tva_kilar_tryck_reflektionskoefficienter(
                f, EXEMPEL, q_s, q_m, q_m, q_r, viktigast
            )
            self.assertLess(_rel(tjock.tryck, 0.5 * kilar), 1e-15)
            hard = tjock_skarm_tryck_reflektionskoefficienter(f, EXEMPEL, q_s, 1.0, q_r, viktigast)
            self.assertGreater(_rel(tjock.tryck, hard), 1e-3)

    def test_allman_form_med_egna_koefficienter(self) -> None:
        resultat = dubbel_diffraktion(
            100.0, EXEMPEL, lambda kil: 2.0 + 0.0j, lambda kil: 0.0 + 3.0j, "forsta", 0.5
        )
        vantat = 0.5 * 6.0j * cmath.exp(1j * 2.0 * PI * 100.0 * EXEMPEL.gangtid_s) / EXEMPEL.vag_m
        self.assertLess(_rel(resultat.tryck, vantat), 1e-15)


class TestReciprocitet(unittest.TestCase):
    """Ombytt källa och mottagare: kil 1 ↔ kil 2 och "forsta" ↔ "andra"."""

    def _slumpad(self, rng: np.random.Generator) -> TvaKanter:
        beta_1 = float(rng.uniform(1.02, 2.0)) * PI
        beta_2 = float(rng.uniform(1.02, 2.0)) * PI
        theta_1s = float(rng.uniform(0.05, 1.0)) * beta_1
        theta_2s = float(rng.uniform(0.05, 1.0)) * beta_2
        return TvaKanter(
            beta_1_rad=beta_1,
            theta_1s_rad=theta_1s,
            theta_1r_rad=float(rng.uniform(0.0, 0.98)) * theta_1s,
            beta_2_rad=beta_2,
            theta_2s_rad=theta_2s,
            theta_2r_rad=float(rng.uniform(0.0, 0.98)) * theta_2s,
            gangtid_kalla_s=float(rng.uniform(0.002, 0.3)),
            gangtid_mitten_s=float(rng.uniform(0.002, 0.3)),
            gangtid_mottagare_s=float(rng.uniform(0.002, 0.3)),
            vag_kalla_m=float(rng.uniform(0.5, 100.0)),
            vag_mitten_m=float(rng.uniform(0.5, 100.0)),
            vag_mottagare_m=float(rng.uniform(0.5, 100.0)),
        )

    def test_tva_kilar_med_impedans(self) -> None:
        """Uppmätt: största relativa skillnad 4,1·10⁻¹¹ på 300 slumpade fall."""
        rng = np.random.default_rng(20261121)
        storst = 0.0
        for _ in range(300):
            g = self._slumpad(rng)
            f = float(10.0 ** rng.uniform(math.log10(25.0), 4.0))
            z = [
                HARD
                if rng.uniform() < 0.2
                else markimpedans(f, float(10.0 ** rng.uniform(1.0, 4.3)))
                for _ in range(4)
            ]
            viktigast = BADA[int(rng.integers(2))]
            fram = tva_kilar_tryck_impedans(f, g, z[0], z[1], z[2], z[3], viktigast)
            bak = tva_kilar_tryck_impedans(f, _spegla(g), z[3], z[2], z[1], z[0], _andra(viktigast))
            storst = max(storst, _rel(fram, bak))
        self.assertLess(storst, 1e-9)

    def test_tjock_skarm_med_impedans_och_med_givna_q(self) -> None:
        """Uppmätt: 1,2·10⁻¹¹ (impedans) och 1,3·10⁻¹¹ (givna Q) på 300 slumpade fall."""
        rng = np.random.default_rng(1106)
        storst = 0.0
        for _ in range(300):
            g = self._slumpad(rng)
            f = float(10.0 ** rng.uniform(math.log10(25.0), 4.0))
            z_s = markimpedans(f, float(10.0 ** rng.uniform(1.0, 4.3)))
            z_r = markimpedans(f, float(10.0 ** rng.uniform(1.0, 4.3)))
            viktigast = BADA[int(rng.integers(2))]
            fram = tjock_skarm_tryck_impedans(f, g, z_s, z_r, viktigast)
            bak = tjock_skarm_tryck_impedans(f, _spegla(g), z_r, z_s, _andra(viktigast))
            storst = max(storst, _rel(fram, bak))
            q = [complex(rng.uniform(-0.369, 1.0)) for _ in range(3)]
            fram = tjock_skarm_tryck_reflektionskoefficienter(f, g, q[0], q[1], q[2], viktigast)
            bak = tjock_skarm_tryck_reflektionskoefficienter(
                f, _spegla(g), q[2], q[1], q[0], _andra(viktigast)
            )
            storst = max(storst, _rel(fram, bak))
        self.assertLess(storst, 1e-9)

    def test_symmetrisk_geometri_ger_samma_tryck_for_bada_valen(self) -> None:
        """Spegelsymmetrisk uppställning: valet av viktigaste kant ska inte spela någon roll."""
        for f in (100.0, 1000.0, 8000.0):
            g = _tva_tunna((0.0, 1.0), (20.0, 5.0), (40.0, 5.0), (60.0, 1.0))
            a = tva_kilar_tryck_impedans(f, g, HARD, HARD, HARD, HARD, "forsta")
            b = tva_kilar_tryck_impedans(f, g, HARD, HARD, HARD, HARD, "andra")
            self.assertLess(_rel(a, b), 1e-11)
            g = _ratvinklig((0.0, 1.0), (29.0, 5.0), (31.0, 5.0), (60.0, 1.0))
            a = tjock_skarm_tryck_impedans(f, g, HARD, HARD, "forsta")
            b = tjock_skarm_tryck_impedans(f, g, HARD, HARD, "andra")
            self.assertLess(_rel(a, b), 1e-11)


class TestEnKantForsumbar(unittest.TestCase):
    """Den andra kanten långt under siktlinjen från den första: fältet går mot en ensam kil."""

    KALLA: Punkt = (0.0, 1.0)
    TOPP_1: Punkt = (20.0, 5.0)
    MOTTAGARE: Punkt = (60.0, 2.0)

    def _avvikelse(self, frekvens_hz: float, djup_m: float) -> tuple[float, float]:
        z_linje = self.TOPP_1[1] + (self.MOTTAGARE[1] - self.TOPP_1[1]) * 0.5
        topp_2 = (40.0, z_linje - djup_m)
        g = _tva_tunna(self.KALLA, self.TOPP_1, topp_2, self.MOTTAGARE)
        dubbel = tva_kilar_tryck_impedans(frekvens_hz, g, HARD, HARD, HARD, HARD, "forsta")
        ensam = _ensam_kil(frekvens_hz, self.KALLA, self.TOPP_1, self.MOTTAGARE, (NED, NED))
        return _rel(dubbel, ensam), _db(dubbel, ensam)

    def test_tva_skarmar_gar_mot_en_skarm(self) -> None:
        """Uppmätt relativ avvikelse för djupet 1, 2, 4, 8 m under linjen T₁–R:
        100 Hz 0,354 0,269 0,163 0,054; 1 kHz 0,230 0,128 0,062 0,015;
        8 kHz 0,098 0,047 0,023 0,007. Vid 16 m växer den igen (0,068 0,034 0,038), se
        `test_resten_ar_omvagen_over_den_andra_kanten`."""
        granser = {100.0: 0.06, 1000.0: 0.02, 8000.0: 0.01}
        for frekvens, grans in granser.items():
            avvikelser = [self._avvikelse(frekvens, djup)[0] for djup in (1.0, 2.0, 4.0, 8.0)]
            with self.subTest(frekvens=frekvens):
                self.assertEqual(avvikelser, sorted(avvikelser, reverse=True))
                self.assertLess(avvikelser[-1], grans)
        self.assertLess(abs(self._avvikelse(8000.0, 8.0)[1]), 0.06)  # uppmätt 0,050 dB

    def test_resten_ar_omvagen_over_den_andra_kanten(self) -> None:
        """Ekv. (97) sätter mottagaren på avståndet R_M + R_R från T₁, inte |T₁R|. Räknas den
        ensamma kilen på samma sätt och skalas med (R_M + R_R)/|T₁R| och fasen för skillnaden
        (det som D̂₂:s direkta stråle ger), går avvikelsen mot noll utan att vända.
        Uppmätt för djupet 1, 2, 4, 8, 16, 32 m: 100 Hz 0,354 0,269 0,161 0,069 0,019 0,003;
        1 kHz 0,230 0,128 0,059 0,022 0,006 0,001; 8 kHz 0,098 0,047 0,021 0,008 0,002 0,0004."""
        for frekvens, grans in ((100.0, 0.005), (1000.0, 0.002), (8000.0, 0.001)):
            avvikelser = []
            for djup in (1.0, 2.0, 4.0, 8.0, 16.0, 32.0):
                topp_2 = (40.0, 3.5 - djup)
                g = _tva_tunna(self.KALLA, self.TOPP_1, topp_2, self.MOTTAGARE)
                dubbel = tva_kilar_tryck_impedans(frekvens, g, HARD, HARD, HARD, HARD, "forsta")
                omvag_m = g.vag_mitten_m + g.vag_mottagare_m
                rak_m = _avstand(self.TOPP_1, self.MOTTAGARE)
                kil = kil_rata_stralar(
                    2.0 * PI,
                    _vinkel(self.TOPP_1, self.KALLA, NED),
                    _vinkel(self.TOPP_1, self.MOTTAGARE, NED),
                    g.vag_kalla_m,
                    omvag_m,
                    C_M_S,
                )
                fas = cmath.exp(1j * 2.0 * PI * frekvens * (rak_m - omvag_m) / C_M_S)
                vantat = kiltryck_impedans(frekvens, kil, HARD, HARD) * omvag_m / rak_m * fas
                avvikelser.append(_rel(dubbel, vantat))
            with self.subTest(frekvens=frekvens):
                self.assertEqual(avvikelser, sorted(avvikelser, reverse=True))
                self.assertLess(avvikelser[-1], grans)

    def test_kant_pa_siktlinjen_halverar_faltet(self) -> None:
        """T₂ precis på linjen T₁–R är skugggräns för den andra kilen: −6,02 dB.
        Uppmätt: −5,44 dB (100 Hz), −5,84 dB (1 kHz), −5,96 dB (8 kHz)."""
        self.assertAlmostEqual(self._avvikelse(8000.0, 0.0)[1], -6.02, delta=0.1)
        self.assertAlmostEqual(self._avvikelse(1000.0, 0.0)[1], -6.02, delta=0.25)

    def test_forsta_kanten_forsumbar_nar_andra_ar_viktigast(self) -> None:
        """Spegelbilden av fallet ovan med ekv. (98): T₁ långt under linjen S–T₂."""
        kalla, topp_2, mottagare = (0.0, 2.0), (40.0, 5.0), (60.0, 1.0)
        topp_1 = (20.0, 3.5 - 8.0)
        g = _tva_tunna(kalla, topp_1, topp_2, mottagare)
        self.assertAlmostEqual(g.theta_2s_rad, _vinkel(topp_2, kalla, NED), places=12)
        dubbel = tva_kilar_tryck_impedans(8000.0, g, HARD, HARD, HARD, HARD, "andra")
        ensam = _ensam_kil(8000.0, kalla, topp_2, mottagare, (NED, NED))
        self.assertLess(_rel(dubbel, ensam), 0.01)

    def test_tjock_skarm_gar_mot_kilen_i_forsta_kanten(self) -> None:
        """Mottagaren ser över T₂: tjock skärm → hård kil i T₁ med ovansidan som yta.
        Källa (15; 0), T₁ (20; 5), mottagare (60; 30), bredd 2, 4, 8, 16 m vid 8 kHz.
        Uppmätt relativ avvikelse: 0,038 0,026 0,015 0,008."""
        kalla, topp_1, mottagare = (15.0, 0.0), (20.0, 5.0), (60.0, 30.0)
        avvikelser = []
        for bredd in (2.0, 4.0, 8.0, 16.0):
            g = _ratvinklig(kalla, topp_1, (20.0 + bredd, 5.0), mottagare)
            self.assertGreater(g.theta_1r_rad, 0.0)  # T₂ under linjen T₁–R
            self.assertAlmostEqual(g.theta_2s_rad, g.beta_2_rad, places=12)
            dubbel = tjock_skarm_tryck_impedans(8000.0, g, HARD, HARD, "forsta")
            ensam = _ensam_kil(8000.0, kalla, topp_1, mottagare, (0.0, NED))
            avvikelser.append(_rel(dubbel, ensam))
        self.assertEqual(avvikelser, sorted(avvikelser, reverse=True))
        self.assertLess(avvikelser[-1], 0.01)


class TestSmalTjockSkarm(unittest.TestCase):
    """Bredden mot noll. Avsnitt 5.7.4 gör inget anspråk på att ge tunn skärm i gränsen;
    övergången mellan en och två kanter görs med r_scr12 (5.21.3, ekv. 314–315)."""

    KALLA: Punkt = (0.0, 1.0)
    MOTTAGARE: Punkt = (60.0, 2.0)

    def test_kalla_i_toppen_pa_hard_kil(self) -> None:
        """Den korta kilens D̂ när R_M → 0 mot exakt värde 2π/β för källa i kilens topp.
        Uppmätt: +1,20 dB (β = 1,5π), +0,56 dB (1,8π), 0,00 dB (2π) för θ_R = 0."""
        for beta, grans_db in ((1.5 * PI, 1.25), (1.8 * PI, 0.7), (2.0 * PI, 0.2)):
            for theta_r in (0.0, 0.3, 1.0):
                kil = kil_rata_stralar(beta, beta, theta_r, 1e-9, 30.0, C_M_S)
                d = kildiffraktionskoefficient_impedans(1000.0, kil, HARD, HARD)
                with self.subTest(beta=beta, theta_r=theta_r):
                    self.assertLess(abs(d.imag), 1e-3)
                    self.assertLess(abs(20.0 * math.log10(abs(d) * beta / (2.0 * PI))), grans_db)

    def test_bredd_mot_noll_ger_inte_tunn_skarm(self) -> None:
        """Metodens egenskap, inte ett fel: med bredden 1 mm ligger ekv. (102) 3–5 dB och
        ekv. (103) 6–12 dB över den tunna skärmen i samma läge (uppmätt: 3,5/4,3/3,8 dB
        respektive 6,3/10,7/11,8 dB vid 100 Hz, 1 kHz, 8 kHz)."""
        for frekvens in (100.0, 1000.0, 8000.0):
            tunn = _ensam_kil(frekvens, self.KALLA, (20.0, 5.0), self.MOTTAGARE, (NED, NED))
            g = _ratvinklig(self.KALLA, (19.9995, 5.0), (20.0005, 5.0), self.MOTTAGARE)
            self.assertEqual(g.theta_1r_rad, 0.0)  # T₂ över linjen T₁–R
            self.assertAlmostEqual(g.theta_2s_rad, g.beta_2_rad, places=12)
            forsta = _db(tjock_skarm_tryck_impedans(frekvens, g, HARD, HARD, "forsta"), tunn)
            andra = _db(tjock_skarm_tryck_impedans(frekvens, g, HARD, HARD, "andra"), tunn)
            with self.subTest(frekvens=frekvens):
                self.assertTrue(3.0 < forsta < 5.0, forsta)
                self.assertTrue(6.0 < andra < 12.5, andra)

    def test_bredd_noll_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            _ratvinklig(self.KALLA, (20.0, 5.0), (20.0, 5.0 + 0.0), (60.0, 2.0))


class TestKontinuitet(unittest.TestCase):
    """Fältet över de gränser där punktlistorna i 5.7.3–5.7.4 byter mätriktning för θ_1R
    eller θ_2S. Där går också en skugggräns i den ena kilen. Uppmätta språng: ≤ 1,1·10⁻⁴
    relativt, samma storlek som för en ensam kil (tabellernas f(0), g(0) ≠ ½)."""

    FREKVENSER = (25.0, 250.0, 2000.0, 10000.0)
    STEG_M = 1e-6
    GRANS = 2.5e-4

    def _sprang_tva_kilar(self, f: float, a: TvaKanter, b: TvaKanter, v: Viktigast) -> float:
        z = (markimpedans(f, 200.0), HARD, HARD, markimpedans(f, 12.5))
        return _rel(
            tva_kilar_tryck_impedans(f, a, z[0], z[1], z[2], z[3], v),
            tva_kilar_tryck_impedans(f, b, z[0], z[1], z[2], z[3], v),
        )

    def test_mottagaren_passerar_linjen_genom_kanterna(self) -> None:
        kalla, topp_1, topp_2 = (0.0, 1.0), (20.0, 5.0), (40.0, 4.0)
        z_grans = 3.0  # linjen T₁–T₂ i x = 60
        under = _tva_tunna(kalla, topp_1, topp_2, (60.0, z_grans - self.STEG_M))
        over = _tva_tunna(kalla, topp_1, topp_2, (60.0, z_grans + self.STEG_M))
        # Under linjen mäts θ_1R mot T₂, över den mot R.
        self.assertLess(under.theta_1r_rad, over.theta_1r_rad)
        for viktigast in BADA:
            for f in self.FREKVENSER:
                with self.subTest(viktigast=viktigast, f=f):
                    self.assertLess(self._sprang_tva_kilar(f, under, over, viktigast), self.GRANS)

    def test_kallan_passerar_linjen_genom_kanterna(self) -> None:
        topp_1, topp_2, mottagare = (20.0, 5.0), (40.0, 4.0), (60.0, 1.0)
        z_grans = 6.0  # linjen T₂–T₁ i x = 0
        under = _tva_tunna((0.0, z_grans - self.STEG_M), topp_1, topp_2, mottagare)
        over = _tva_tunna((0.0, z_grans + self.STEG_M), topp_1, topp_2, mottagare)
        self.assertGreater(under.theta_2s_rad, over.theta_2s_rad)
        for viktigast in BADA:
            for f in self.FREKVENSER:
                with self.subTest(viktigast=viktigast, f=f):
                    self.assertLess(self._sprang_tva_kilar(f, under, over, viktigast), self.GRANS)

    def test_tjock_skarm_mottagaren_passerar_ovansidans_plan(self) -> None:
        """θ_1R går från 0 (T₂ över linjen T₁–R) till en positiv vinkel."""
        kalla, topp_1, topp_2 = (0.0, 1.0), (20.0, 5.0), (22.0, 5.0)
        under = _ratvinklig(kalla, topp_1, topp_2, (60.0, 5.0 - self.STEG_M))
        over = _ratvinklig(kalla, topp_1, topp_2, (60.0, 5.0 + self.STEG_M))
        self.assertEqual(under.theta_1r_rad, 0.0)
        self.assertGreater(over.theta_1r_rad, 0.0)
        for viktigast in BADA:
            for f in self.FREKVENSER:
                z_s, z_r = markimpedans(f, 200.0), markimpedans(f, 12.5)
                sprang = _rel(
                    tjock_skarm_tryck_impedans(f, under, z_s, z_r, viktigast),
                    tjock_skarm_tryck_impedans(f, over, z_s, z_r, viktigast),
                )
                with self.subTest(viktigast=viktigast, f=f):
                    self.assertLess(sprang, self.GRANS)

    def test_tjock_skarm_kallan_passerar_ovansidans_plan(self) -> None:
        topp_1, topp_2, mottagare = (20.0, 5.0), (22.0, 5.0), (60.0, 1.0)
        under = _ratvinklig((0.0, 5.0 - self.STEG_M), topp_1, topp_2, mottagare)
        over = _ratvinklig((0.0, 5.0 + self.STEG_M), topp_1, topp_2, mottagare)
        self.assertAlmostEqual(under.theta_2s_rad, under.beta_2_rad, places=12)
        self.assertLess(over.theta_2s_rad, over.beta_2_rad)
        for viktigast in BADA:
            for f in self.FREKVENSER:
                sprang = _rel(
                    tjock_skarm_tryck_impedans(f, under, HARD, HARD, viktigast),
                    tjock_skarm_tryck_impedans(f, over, HARD, HARD, viktigast),
                )
                with self.subTest(viktigast=viktigast, f=f):
                    self.assertLess(sprang, self.GRANS)

    def test_slat_overgang_langs_ett_svep(self) -> None:
        """Mottagaren sveps 0–12 m i höjd förbi båda gränserna i steg om 1 mm: inget steg
        ändrar |p̂| mer än 0,03 dB (uppmätt 0,006 dB vid 2 kHz)."""
        kalla, topp_1, topp_2 = (0.0, 1.0), (20.0, 5.0), (40.0, 4.0)
        for viktigast in BADA:
            forra = None
            storst = 0.0
            for z_mm in range(0, 12001):
                g = _tva_tunna(kalla, topp_1, topp_2, (60.0, z_mm / 1000.0))
                niva = 20.0 * math.log10(
                    abs(tva_kilar_tryck_impedans(2000.0, g, HARD, HARD, HARD, HARD, viktigast))
                )
                if forra is not None:
                    storst = max(storst, abs(niva - forra))
                forra = niva
            self.assertLess(storst, 0.03)


if __name__ == "__main__":
    unittest.main()
