"""Tester för terrängtolkningen i `terrang_skarmar` (AV 1106/07 avsnitt 5.21.1–5.21.3).

Svaren följer av definitionerna i dokumentet (konstruerade profiler räknade för hand med
`math.hypot`) eller av oberoende geometri: den verksammaste kanten över siktlinjen ligger på
profilens konvexa hölje, och spegling av profilen (källa och mottagare byter plats) ska ge
samma kanter och samma övergångsparametrar.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import numpy as np

from buller.nord2000.frekvenser import ljudhastighet_m_s, mittfrekvenser_hz
from buller.nord2000.fresnel import calc_fzd
from buller.nord2000.terrang import (
    Terranggeometri,
    gangvagsskillnad_m,
    primar_skarmkant,
    skarmparameter_rscr1,
    terranggeometri,
)
from buller.nord2000.terrang_skarmar import (
    EN_SKARM_EN_KANT,
    EN_SKARM_TVA_KANTER,
    INGEN_SKARM,
    TVA_SKARMAR,
    Terrangtolkning,
    ersattningspunkt_t1,
    gangvagsskillnad_mellan_m,
    gangvagsskillnad_prim_m,
    gangvagsskillnad_sekundar_m,
    konvex,
    overgang_fresnelzon,
    overgang_gangvag,
    overgang_hojd,
    overgang_kantavstand,
    sekundar_kant,
    sekundar_skarmkant,
    sekundarkantgeometri,
    skarmform,
    skarmparameter_rscr1_allman,
    skarmparameter_rscr2,
    skarmparameter_rscr12,
    tolka_terrang,
    tolka_terrang_homogen,
    valj_skarmkanter,
    vertikalt_avstand_m,
)
from buller.testfall.format import las_fall
from buller.typer import BullerFel, Terrangprofil

C = ljudhastighet_m_s(15.0)
F = mittfrekvenser_hz()
TESTSVIT = Path(__file__).resolve().parents[3] / "testsvit" / "N_nord2000"


def profil(x: list[float], z: list[float], h_s: float = 0.5, h_r: float = 1.5) -> Terrangprofil:
    n = len(x)
    return Terrangprofil(
        x_m=np.array(x, dtype=np.float64),
        z_m=np.array(z, dtype=np.float64),
        flodesresistivitet_kNsm4=np.full(n - 1, 200.0),
        rahet_m=np.zeros(n - 1),
        kalla_hojd_m=h_s,
        mottagare_hojd_m=h_r,
    )


def geo_av(x: list[float], z: list[float], h_s: float = 0.5, h_r: float = 1.5) -> Terranggeometri:
    return terranggeometri(profil(x, z, h_s, h_r))


def speglad(p: Terrangprofil) -> Terrangprofil:
    """Samma terräng sedd från mottagaren: källa och mottagare byter plats."""
    d = float(p.x_m[-1])
    return Terrangprofil(
        x_m=(d - p.x_m)[::-1].copy(),
        z_m=p.z_m[::-1].copy(),
        flodesresistivitet_kNsm4=p.flodesresistivitet_kNsm4[::-1].copy(),
        rahet_m=p.rahet_m[::-1].copy(),
        kalla_hojd_m=p.mottagare_hojd_m,
        mottagare_hojd_m=p.kalla_hojd_m,
    )


def ovre_holje(punkter: list[tuple[float, float]]) -> set[tuple[float, float]]:
    """Övre konvexa höljet av punkter sorterade i x (Andrews monotona kedja)."""
    kedja: list[tuple[float, float]] = []
    for p in punkter:
        while len(kedja) >= 2:
            (x1, z1), (x2, z2) = kedja[-2], kedja[-1]
            if (x2 - x1) * (p[1] - z1) - (z2 - z1) * (p[0] - x1) >= 0.0:
                kedja.pop()
            else:
                break
        kedja.append(p)
    return set(kedja)


TUNN = ([0.0, 40.0, 40.01, 40.02, 100.0], [0.0, 0.0, 3.0, 0.0, 0.0])
TJOCK = ([0.0, 30.0, 30.01, 45.0, 45.01, 100.0], [0.0, 0.0, 2.5, 2.5, 0.0, 0.0])
VALL = ([0.0, 60.0, 62.0, 66.0, 70.0, 72.0, 100.0], [0.0, 0.0, 2.5, 3.0, 2.5, 0.0, 0.0])
TVA = (
    [0.0, 29.99, 30.0, 30.01, 69.99, 70.0, 70.01, 100.0],
    [0.0, 0.0, 5.0, 0.0, 0.0, 4.0, 0.0, 0.0],
)


class TestHjalpfunktioner(unittest.TestCase):
    def test_vertdist_ekv_390(self) -> None:
        self.assertAlmostEqual(vertikalt_avstand_m(0.0, 0.0, 10.0, 10.0, 5.0, 7.0), 2.0, places=12)
        self.assertAlmostEqual(vertikalt_avstand_m(0.0, 0.0, 10.0, 10.0, 5.0, 3.0), -2.0, places=12)
        self.assertAlmostEqual(vertikalt_avstand_m(2.0, 1.0, 6.0, 3.0, 4.0, 2.0), 0.0, places=12)
        # Lodrät höjd, inte vinkelrät: linjen z = x, punkten (0, 1) ligger 1 över.
        self.assertAlmostEqual(vertikalt_avstand_m(-1.0, -1.0, 1.0, 1.0, 0.0, 1.0), 1.0, places=12)
        with self.assertRaises(BullerFel):
            vertikalt_avstand_m(1.0, 0.0, 1.0, 2.0, 1.0, 1.0)

    def test_convex_ekv_336(self) -> None:
        geo = geo_av([0.0, 10.0, 20.0, 30.0, 40.0], [0.0, 1.0, 0.0, 0.0, 0.0])
        self.assertTrue(konvex(geo, 1))  # topp
        self.assertFalse(konvex(geo, 2))  # svacka
        self.assertFalse(konvex(geo, 3))  # rak
        for index in (0, 4, -1, 5):
            with self.assertRaises(BullerFel):
                konvex(geo, index)

    def test_convex_marginal_0_0001_m(self) -> None:
        under = geo_av([0.0, 10.0, 20.0], [0.0, 0.00005, 0.0])
        over = geo_av([0.0, 10.0, 20.0], [0.0, 0.0002, 0.0])
        self.assertFalse(konvex(under, 1))
        self.assertTrue(konvex(over, 1))

    def test_gangvagsskillnad_mellan_s_och_r_ar_ekv_300(self) -> None:
        for x, z in (TUNN, TJOCK, VALL, TVA):
            geo = geo_av(x, z)
            for i in range(1, len(x) - 1):
                egen = gangvagsskillnad_mellan_m(
                    geo.x_s_m, geo.z_s_m, geo.x_r_m, geo.z_r_m, float(geo.x_m[i]), float(geo.z_m[i])
                )
                self.assertEqual(egen, gangvagsskillnad_m(geo, i))

    def test_gangvagsskillnad_tecken_och_varde(self) -> None:
        # A = (0, 0), B = (8, 0), P = (4, ±3): omväg 5 + 5 − 8 = 2.
        self.assertAlmostEqual(
            gangvagsskillnad_mellan_m(0.0, 0.0, 8.0, 0.0, 4.0, 3.0), 2.0, places=12
        )
        self.assertAlmostEqual(
            gangvagsskillnad_mellan_m(0.0, 0.0, 8.0, 0.0, 4.0, -3.0), -2.0, places=12
        )
        self.assertEqual(gangvagsskillnad_mellan_m(0.0, 0.0, 8.0, 0.0, 4.0, 0.0), 0.0)


class TestSkarmform(unittest.TestCase):
    def test_tunn_skarm_tre_punkter(self) -> None:
        geo = geo_av(*TUNN)
        form = skarmform(geo, 2)
        self.assertEqual((form.index_w1, form.index_kant, form.index_w2), (1, 2, 3))
        self.assertEqual(form.antal_punkter, 3)

    def test_tjock_skarm_fyra_punkter(self) -> None:
        geo = geo_av(*TJOCK)
        for kant in (2, 3):
            form = skarmform(geo, kant)
            self.assertEqual((form.index_w1, form.index_w2), (1, 4))
            self.assertEqual(form.antal_punkter, 4)

    def test_vall_fem_punkter(self) -> None:
        geo = geo_av(*VALL)
        form = skarmform(geo, 3)
        self.assertEqual((form.index_w1, form.index_w2), (1, 5))
        self.assertTrue(form.innehaller(1) and form.innehaller(5) and not form.innehaller(0))

    def test_formen_slutar_i_profilens_andpunkter(self) -> None:
        # Konvex rygg hela vägen: inga icke-konvexa inre punkter, formen når båda ändarna.
        geo = geo_av([0.0, 10.0, 20.0, 30.0, 40.0], [0.0, 2.0, 3.0, 2.0, 0.0])
        form = skarmform(geo, 2)
        self.assertEqual((form.index_w1, form.index_w2), (0, 4))

    def test_kanten_ska_vara_inre_punkt(self) -> None:
        geo = geo_av(*TUNN)
        for index in (0, 4):
            with self.assertRaises(BullerFel):
                skarmform(geo, index)


class TestValAvKanter(unittest.TestCase):
    def test_t1_over_siktlinjen_ligger_pa_konvexa_holjet(self) -> None:
        rng = np.random.default_rng(20261121)
        provade = 0
        for _ in range(300):
            n = int(rng.integers(4, 14))
            x = np.concatenate(([0.0], np.sort(rng.uniform(1.0, 199.0, n - 2)), [200.0]))
            if bool(np.any(np.diff(x) < 0.05)):
                continue
            z = rng.uniform(0.0, 6.0, n)
            geo = terranggeometri(profil(list(x), list(z), 0.5, 1.5))
            kant = primar_skarmkant(geo)
            if kant is None or kant.gangvagsskillnad_m <= 0.0:
                continue
            provade += 1
            punkter = [(geo.x_s_m, geo.z_s_m)]
            punkter += [(float(geo.x_m[i]), float(geo.z_m[i])) for i in range(1, n - 1)]
            punkter.append((geo.x_r_m, geo.z_r_m))
            holje = ovre_holje(punkter)
            self.assertIn((float(geo.x_m[kant.index]), float(geo.z_m[kant.index])), holje)
        self.assertGreater(provade, 100)

    def test_sekundar_skarm_raknas_mot_t1(self) -> None:
        geo = geo_av(*TVA, h_s=1.0, h_r=1.0)
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.sekundar is not None
        assert kanter.primar_form is not None and kanter.sekundar_form is not None
        self.assertEqual(kanter.primar.index, 2)  # den högre skärmen, symmetriskt placerad
        self.assertEqual(kanter.sekundar.index, 5)
        self.assertEqual((kanter.primar_form.index_w1, kanter.primar_form.index_w2), (1, 3))
        self.assertEqual((kanter.sekundar_form.index_w1, kanter.sekundar_form.index_w2), (4, 6))
        # T₂ på mottagarsidan: S ersätts med T₁ = (30, 5); R = (100, 1); T₂ = (70, 4).
        forvantat = math.hypot(40.0, 1.0) + math.hypot(30.0, 3.0) - math.hypot(70.0, 4.0)
        self.assertAlmostEqual(kanter.sekundar.gangvagsskillnad_m, forvantat, places=12)
        self.assertGreater(forvantat, 0.0)
        self.assertEqual(ersattningspunkt_t1(geo, kanter.primar), (30.0, 5.0))
        self.assertIsNone(kanter.sekundar_kant)  # tre punkter i formen

    def test_sekundar_skarm_pa_kallsidan(self) -> None:
        x, z = TVA
        geo = geo_av(x, [0.0, 0.0, 4.0, 0.0, 0.0, 5.0, 0.0, 0.0], h_s=1.0, h_r=1.0)
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.sekundar is not None
        self.assertEqual((kanter.primar.index, kanter.sekundar.index), (5, 2))
        # R ersätts med T₁ = (70, 5); S = (0, 1); T₂ = (30, 4).
        forvantat = math.hypot(30.0, 3.0) + math.hypot(40.0, 1.0) - math.hypot(70.0, 4.0)
        self.assertAlmostEqual(kanter.sekundar.gangvagsskillnad_m, forvantat, places=12)

    def test_t1_under_siktlinjen_ersatts_med_t1_prim(self) -> None:
        # S = (0, 2), R = (100, 2). T₁ = (50, 0,5) under siktlinjen; T₁' = (50, 2).
        geo = geo_av(
            [0.0, 19.99, 20.0, 20.01, 49.99, 50.0, 50.01, 100.0],
            [0.0, 0.0, 0.4, 0.0, 0.0, 0.5, 0.0, 0.0],
            h_s=2.0,
            h_r=2.0,
        )
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.sekundar is not None
        self.assertEqual(kanter.primar.index, 5)
        self.assertLess(kanter.primar.gangvagsskillnad_m, 0.0)
        x_e, z_e = ersattningspunkt_t1(geo, kanter.primar)
        self.assertEqual(x_e, 50.0)
        self.assertAlmostEqual(z_e, 2.0, places=12)
        self.assertEqual(kanter.sekundar.index, 2)
        # Punkten (20, 0,4) mot siktlinjen S–T₁' (z = 2): under linjen, alltså negativt.
        forvantat = -(math.hypot(20.0, 1.6) + math.hypot(30.0, 1.6) - 50.0)
        self.assertAlmostEqual(kanter.sekundar.gangvagsskillnad_m, forvantat, places=12)
        # Mot T₁ själv (utan T₁') blir värdet mindre negativt – regeln med T₁' gör skillnad.
        self.assertGreater(
            gangvagsskillnad_mellan_m(0.0, 2.0, 50.0, 0.5, 20.0, 0.4), -abs(forvantat)
        )

    def test_primarformens_punkter_ar_inte_sekundar_skarm(self) -> None:
        geo = geo_av(*VALL)
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.primar_form is not None
        self.assertEqual(kanter.primar.index, 3)
        self.assertEqual(kanter.primar_form.antal_punkter, 5)
        self.assertIsNone(kanter.sekundar)  # vallens skuldror hör till primärskärmen
        assert kanter.sekundar_kant is not None
        self.assertIn(kanter.sekundar_kant.index, (2, 4))
        with self.assertRaises(BullerFel):
            gangvagsskillnad_sekundar_m(geo, kanter.primar, kanter.primar.index)

    def test_sekundar_kant_har_storst_omvag_bland_formens_punkter(self) -> None:
        geo = geo_av(*VALL)
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.sekundar_kant is not None
        varden = {i: gangvagsskillnad_sekundar_m(geo, kanter.primar, i) for i in (2, 4)}
        self.assertEqual(kanter.sekundar_kant.index, max(varden, key=lambda i: varden[i]))
        self.assertEqual(kanter.sekundar_kant.gangvagsskillnad_m, max(varden.values()))

    def test_punkter_under_baslinjen_ar_inte_kandidater(self) -> None:
        # Tunn skärm och en gupp i en sänka som ligger under baslinjen.
        geo = geo_av(
            [0.0, 20.0, 20.01, 20.02, 50.0, 60.0, 70.0, 80.0, 100.0],
            [0.0, 0.0, 3.0, 0.0, -2.0, -1.0, -2.0, 0.0, 0.0],
        )
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.primar_form is not None
        self.assertEqual(kanter.primar.index, 2)
        self.assertIsNone(sekundar_skarmkant(geo, kanter.primar, kanter.primar_form))
        self.assertIsNone(sekundar_kant(geo, kanter.primar, kanter.primar_form))

    def test_plan_mark_har_inga_kanter(self) -> None:
        kanter = valj_skarmkanter(geo_av([0.0, 10.0, 50.0, 100.0], [0.0, 0.0, 0.0, 0.0]))
        self.assertIsNone(kanter.primar)
        self.assertIsNone(kanter.primar_form)
        self.assertIsNone(kanter.sekundar)
        self.assertIsNone(kanter.sekundar_kant)


class TestOvergangar(unittest.TestCase):
    def test_r_delta_l_ekv_302(self) -> None:
        self.assertEqual(overgang_gangvag(0.0, 1.0), 1.0)
        self.assertEqual(overgang_gangvag(0.5, 2.0), 1.0)
        self.assertAlmostEqual(overgang_gangvag(-1.0 / 30.0, 1.0), 0.5, places=12)
        self.assertEqual(overgang_gangvag(-0.133, 1.0), 0.0)
        self.assertEqual(overgang_gangvag(-1.0, 1.0), 0.0)
        # Skalning: bara kvoten Δℓ'/λ spelar roll.
        self.assertAlmostEqual(
            overgang_gangvag(-0.02, 0.4), overgang_gangvag(-0.05, 1.0), places=12
        )
        # Nära −0,133 är formeln nästan 0 (1 − √(7,5·0,133) = 0,0013): ingen större diskontinuitet.
        self.assertLess(overgang_gangvag(-0.1329999, 1.0), 0.002)

    def test_r_lambda_ekv_303(self) -> None:
        self.assertEqual(overgang_hojd(0.3, 1.0), 1.0)
        self.assertAlmostEqual(overgang_hojd(0.2, 1.0), 0.5, places=12)
        self.assertEqual(overgang_hojd(0.1, 1.0), 0.0)
        self.assertEqual(overgang_hojd(-1.0, 1.0), 0.0)
        self.assertAlmostEqual(overgang_hojd(0.2999999, 1.0), 1.0, places=5)

    def test_r_fz_ekv_304(self) -> None:
        d1, d2, vaglangd = 30.0, 70.0, 0.68
        h_fz = calc_fzd(d1, d2, math.pi / 2.0, 0.5 * vaglangd)
        self.assertEqual(overgang_fresnelzon(0.082 * h_fz, d1, d2, vaglangd), 1.0)
        self.assertAlmostEqual(overgang_fresnelzon(0.054 * h_fz, d1, d2, vaglangd), 0.5, places=12)
        self.assertEqual(overgang_fresnelzon(0.026 * h_fz, d1, d2, vaglangd), 0.0)
        self.assertEqual(overgang_fresnelzon(-1.0, d1, d2, vaglangd), 0.0)

    def test_r_w_ekv_308(self) -> None:
        self.assertEqual(overgang_kantavstand(1.0, 1.0), 1.0)
        self.assertAlmostEqual(overgang_kantavstand(0.65, 1.0), 0.5, places=12)
        self.assertEqual(overgang_kantavstand(0.3, 1.0), 0.0)
        self.assertAlmostEqual(overgang_kantavstand(0.9999999, 1.0), 1.0, places=6)
        self.assertAlmostEqual(overgang_kantavstand(1.3, 2.0), 0.5, places=12)

    def test_delta_l_prim_ar_storsta_vardet(self) -> None:
        self.assertEqual(gangvagsskillnad_prim_m(-0.02, None), -0.02)
        self.assertEqual(gangvagsskillnad_prim_m(-0.02, 0.01), 0.01)
        self.assertEqual(gangvagsskillnad_prim_m(-0.02, -0.05), -0.02)
        with self.assertRaises(BullerFel):
            gangvagsskillnad_prim_m(-0.02, math.nan)

    def test_rscr1_allman_utan_refraktion_ar_terrangmodulens(self) -> None:
        for x, z in (TUNN, TJOCK, VALL, TVA, ([0.0, 50.0, 100.0], [0.0, 0.3, 0.0])):
            geo = geo_av(x, z)
            kant = primar_skarmkant(geo)
            assert kant is not None
            for f in F:
                self.assertEqual(
                    skarmparameter_rscr1_allman(
                        float(f), kant, geo.d_bas_tot_m, C, gangvagsskillnad_refraktion_m=None
                    ),
                    skarmparameter_rscr1(float(f), kant, geo.d_bas_tot_m, C),
                )

    def test_kroken_for_refraktion_i_rscr1(self) -> None:
        # Låg kant under siktlinjen: r_Δℓ < 1 vid höga frekvenser utan refraktion.
        geo = geo_av([0.0, 49.99, 50.0, 50.01, 100.0], [0.0, 0.0, 1.0, 0.0, 0.0], h_s=1.0, h_r=1.5)
        kant = primar_skarmkant(geo)
        assert kant is not None
        self.assertLess(kant.gangvagsskillnad_m, 0.0)
        f = 4000.0
        vaglangd = C / f

        def r(dl: float | None) -> float:
            return skarmparameter_rscr1_allman(
                f, kant, geo.d_bas_tot_m, C, gangvagsskillnad_refraktion_m=dl
            )

        utan = r(None)
        hojdfaktorer = overgang_hojd(1.0, vaglangd) * overgang_fresnelzon(
            kant.hojd_over_baslinje_m, kant.d_bas_m, geo.d_bas_tot_m - kant.d_bas_m, vaglangd
        )
        self.assertLess(utan, hojdfaktorer)
        self.assertEqual(r(0.01), hojdfaktorer)  # Δℓ ≥ 0: r_Δℓ = 1
        self.assertEqual(r(kant.gangvagsskillnad_m - 1.0), utan)  # max(Δℓ, Δℓ₀) = Δℓ₀
        with self.assertRaises(BullerFel):
            skarmparameter_rscr1_allman(
                0.0, kant, geo.d_bas_tot_m, C, gangvagsskillnad_refraktion_m=None
            )

    def test_rscr2_ar_produkten_i_ekv_309(self) -> None:
        geo = geo_av(*TVA, h_s=1.0, h_r=1.0)
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.sekundar is not None
        t2 = kanter.sekundar
        d12 = math.hypot(40.0, 1.0)
        for f in F:
            vaglangd = C / float(f)
            forvantat = (
                overgang_gangvag(t2.gangvagsskillnad_m, vaglangd)
                * overgang_hojd(4.0, vaglangd)
                * overgang_fresnelzon(4.0, 70.0, 30.0, vaglangd)
                * overgang_kantavstand(d12, vaglangd)
            )
            r2 = skarmparameter_rscr2(
                float(f), geo, kanter.primar, t2, C, gangvagsskillnad_refraktion_m=None
            )
            self.assertAlmostEqual(r2, forvantat, places=12)
        # Två höga skärmar 40 m isär, T₂ över siktlinjen: full verkan vid 1 kHz.
        self.assertEqual(
            skarmparameter_rscr2(
                1000.0, geo, kanter.primar, t2, C, gangvagsskillnad_refraktion_m=None
            ),
            1.0,
        )

    def test_rscr2_noll_nar_kanterna_ligger_tatt(self) -> None:
        # Två tunna skärmar 0,5 m isär: d₁₂ < 0,3·λ under ca 200 Hz ger r_w = 0.
        geo = geo_av(
            [0.0, 49.99, 50.0, 50.01, 50.49, 50.5, 50.51, 100.0],
            [0.0, 0.0, 3.0, 0.0, 0.0, 2.9, 0.0, 0.0],
        )
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.sekundar is not None
        d12 = math.hypot(0.5, 0.1)
        for f in F:
            r2 = skarmparameter_rscr2(
                float(f), geo, kanter.primar, kanter.sekundar, C, gangvagsskillnad_refraktion_m=None
            )
            if d12 <= 0.3 * C / float(f):
                self.assertEqual(r2, 0.0)
        self.assertEqual(
            skarmparameter_rscr2(
                25.0, geo, kanter.primar, kanter.sekundar, C, gangvagsskillnad_refraktion_m=None
            ),
            0.0,
        )


class TestSekundarKant(unittest.TestCase):
    def test_geometrin_i_figur_34(self) -> None:
        # Källsidan: W₁ = (30, 0), T₁₂ = (32, 2), T₁ = (40, 3), W₂ = (42, 0).
        geo = geo_av([0.0, 30.0, 32.0, 40.0, 42.0, 100.0], [0.0, 0.0, 2.0, 3.0, 0.0, 0.0])
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.primar_form is not None
        assert kanter.sekundar_kant is not None
        self.assertEqual((kanter.primar.index, kanter.sekundar_kant.index), (3, 2))
        g = sekundarkantgeometri(geo, kanter.primar, kanter.primar_form, kanter.sekundar_kant)
        # |P₀T₁₂| = |ST₁₂| och P₀ på strålen från T₁₂ genom W₁ (ekv. 311).
        avstand_s = math.hypot(32.0 - geo.x_s_m, 2.0 - geo.z_s_m)
        self.assertAlmostEqual(math.hypot(g.x0_m - 32.0, g.z0_m - 2.0), avstand_s, places=10)
        kryss = (g.x0_m - 32.0) * (0.0 - 2.0) - (g.z0_m - 2.0) * (30.0 - 32.0)
        self.assertAlmostEqual(kryss, 0.0, places=9)
        self.assertLess(g.x0_m, 30.0)
        # T₁₂' ligger på linjen P₀T₁ och T₁₂T₁₂' är vinkelrät mot den (ekv. 312).
        rx, rz = 40.0 - g.x0_m, 3.0 - g.z0_m
        self.assertAlmostEqual((g.x_fot_m - g.x0_m) * rz - (g.z_fot_m - g.z0_m) * rx, 0.0, places=8)
        self.assertAlmostEqual((32.0 - g.x_fot_m) * rx + (2.0 - g.z_fot_m) * rz, 0.0, places=8)
        self.assertAlmostEqual(g.hojd_m, math.hypot(32.0 - g.x_fot_m, 2.0 - g.z_fot_m), places=10)
        self.assertGreater(g.hojd_m, 0.0)
        # d₁ och d₂ räknas till fotpunkten T₁₂' (ekv. 313) och delar sträckan P₀T₁.
        self.assertAlmostEqual(g.d1_m + g.d2_m, math.hypot(rx, rz), places=10)

    def test_spegling_ger_samma_hojd_pa_mottagarsidan(self) -> None:
        p = profil([0.0, 30.0, 32.0, 40.0, 42.0, 100.0], [0.0, 0.0, 2.0, 3.0, 0.0, 0.0])
        geo, geo_sp = terranggeometri(p), terranggeometri(speglad(p))
        k, k_sp = valj_skarmkanter(geo), valj_skarmkanter(geo_sp)
        assert k.primar is not None and k.primar_form is not None and k.sekundar_kant is not None
        assert k_sp.primar is not None and k_sp.primar_form is not None
        assert k_sp.sekundar_kant is not None
        self.assertGreater(k_sp.sekundar_kant.index, k_sp.primar.index)  # mottagarsidan
        g = sekundarkantgeometri(geo, k.primar, k.primar_form, k.sekundar_kant)
        g_sp = sekundarkantgeometri(geo_sp, k_sp.primar, k_sp.primar_form, k_sp.sekundar_kant)
        self.assertAlmostEqual(g.hojd_m, g_sp.hojd_m, places=10)
        self.assertAlmostEqual(g.d1_m, g_sp.d1_m, places=10)
        self.assertAlmostEqual(g.d2_m, g_sp.d2_m, places=10)

    def test_nastan_rak_kant_ar_inte_verksam(self) -> None:
        # T₁₂ ligger 1 mm över linjen W₁–T₁: konvex enligt ekv. (336) men h'_SCR12 ≈ 0.
        geo = geo_av([0.0, 30.0, 35.0, 40.0, 40.01, 100.0], [0.0, 0.0, 1.501, 3.0, 0.0, 0.0])
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.primar_form is not None
        assert kanter.sekundar_kant is not None
        self.assertEqual(kanter.sekundar_kant.index, 2)
        g = sekundarkantgeometri(geo, kanter.primar, kanter.primar_form, kanter.sekundar_kant)
        self.assertLess(abs(g.hojd_m), 0.002)
        for f in F:
            self.assertEqual(
                skarmparameter_rscr12(
                    float(f),
                    geo,
                    kanter.primar,
                    kanter.primar_form,
                    kanter.sekundar_kant,
                    C,
                    gangvagsskillnad_refraktion_m=None,
                ),
                0.0,
            )
        self.assertEqual(tolka_terrang_homogen(geo, F, C).delmodeller, (4,))

    def test_rscr12_ar_produkten_i_ekv_314(self) -> None:
        geo = geo_av(*TJOCK)
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.primar_form is not None
        assert kanter.sekundar_kant is not None
        t12 = kanter.sekundar_kant
        g = sekundarkantgeometri(geo, kanter.primar, kanter.primar_form, t12)
        d12 = abs(float(geo.x_m[t12.index]) - float(geo.x_m[kanter.primar.index]))
        for f in F:
            vaglangd = C / float(f)
            forvantat = (
                overgang_gangvag(t12.gangvagsskillnad_m, vaglangd)
                * overgang_hojd(t12.hojd_over_baslinje_m, vaglangd)
                * overgang_fresnelzon(
                    t12.hojd_over_baslinje_m, t12.d_bas_m, geo.d_bas_tot_m - t12.d_bas_m, vaglangd
                )
                * overgang_kantavstand(d12, vaglangd)
                * overgang_hojd(g.hojd_m, vaglangd)
                * overgang_fresnelzon(g.hojd_m, g.d1_m, g.d2_m, vaglangd)
            )
            r12 = skarmparameter_rscr12(
                float(f),
                geo,
                kanter.primar,
                kanter.primar_form,
                t12,
                C,
                gangvagsskillnad_refraktion_m=None,
            )
            self.assertAlmostEqual(r12, forvantat, places=12)

    def test_fel_kant_ger_bullerfel(self) -> None:
        geo = geo_av(*TJOCK)
        kanter = valj_skarmkanter(geo)
        assert kanter.primar is not None and kanter.primar_form is not None
        with self.assertRaises(BullerFel):
            sekundarkantgeometri(geo, kanter.primar, kanter.primar_form, kanter.primar)


class TestTolkning(unittest.TestCase):
    def test_plan_mark(self) -> None:
        t = tolka_terrang_homogen(geo_av([0.0, 10.0, 100.0], [0.0, 0.0, 0.0]), F, C)
        self.assertEqual(t.skarmtyp, INGEN_SKARM)
        self.assertEqual(t.delmodeller, ())
        self.assertTrue(bool(np.all(t.r_scr1 == 0.0)))
        self.assertIsNone(t.primar)

    def test_tunn_skarm(self) -> None:
        t = tolka_terrang_homogen(geo_av(*TUNN), F, C)
        self.assertEqual(t.skarmtyp, EN_SKARM_EN_KANT)
        self.assertEqual(t.delmodeller, (4,))
        assert t.primar is not None and t.primar_form is not None
        self.assertEqual(t.primar.index, 2)
        self.assertEqual(t.primar_form.antal_punkter, 3)
        self.assertIsNone(t.sekundar)
        self.assertIsNone(t.sekundar_kant)
        self.assertTrue(bool(np.all(t.r_scr2 == 0.0)) and bool(np.all(t.r_scr12 == 0.0)))
        self.assertEqual(float(t.r_scr1[-1]), 1.0)

    def test_tjock_skarm(self) -> None:
        t = tolka_terrang_homogen(geo_av(*TJOCK), F, C)
        self.assertEqual(t.skarmtyp, EN_SKARM_TVA_KANTER)
        self.assertIn(5, t.delmodeller)
        self.assertNotIn(6, t.delmodeller)
        assert t.primar is not None and t.sekundar_kant is not None
        self.assertEqual({t.primar.index, t.sekundar_kant.index}, {2, 3})

    def test_tva_skarmar(self) -> None:
        t = tolka_terrang_homogen(geo_av(*TVA, h_s=1.0, h_r=1.0), F, C)
        self.assertEqual(t.skarmtyp, TVA_SKARMAR)
        self.assertIn(6, t.delmodeller)
        assert t.sekundar is not None and t.sekundar_form is not None
        self.assertEqual(t.sekundar.index, 5)
        self.assertEqual(float(t.r_scr2[-1]), 1.0)

    def test_overksam_primarskarm_ger_ingen_sekundar(self) -> None:
        # Två gupp på 2 cm: r_λ = 0 upp till ca 1,7 kHz och r_Fz = 0 däröver.
        geo = geo_av(
            [0.0, 30.0, 30.5, 31.0, 60.0, 60.5, 61.0, 100.0],
            [0.0, 0.0, 0.02, 0.0, 0.0, 0.02, 0.0, 0.0],
            h_s=0.5,
            h_r=1.5,
        )
        kanter = valj_skarmkanter(geo)
        self.assertIsNotNone(kanter.primar)
        self.assertIsNotNone(kanter.sekundar)
        t = tolka_terrang_homogen(geo, F, C)
        self.assertTrue(bool(np.all(t.r_scr1 == 0.0)))
        self.assertEqual(t.skarmtyp, INGEN_SKARM)
        self.assertIsNone(t.primar)
        self.assertIsNone(t.sekundar)
        self.assertTrue(bool(np.all(t.r_scr2 == 0.0)))

    def test_parametrarna_ligger_mellan_0_och_1(self) -> None:
        rng = np.random.default_rng(7)
        for _ in range(200):
            n = int(rng.integers(3, 12))
            x = np.concatenate(([0.0], np.sort(rng.uniform(1.0, 299.0, n - 2)), [300.0]))
            if bool(np.any(np.diff(x) < 0.05)):
                continue
            z = rng.uniform(0.0, 4.0, n)
            t = tolka_terrang_homogen(terranggeometri(profil(list(x), list(z))), F, C)
            for r in (t.r_scr1, t.r_scr2, t.r_scr12):
                self.assertEqual(r.shape, F.shape)
                self.assertTrue(bool(np.all((r >= 0.0) & (r <= 1.0))))
            self.assertEqual(t.primar is None, not bool(np.any(t.r_scr1 > 0.0)))
            self.assertEqual(t.sekundar is None, not bool(np.any(t.r_scr2 > 0.0)))
            self.assertEqual(t.sekundar_kant is None, not bool(np.any(t.r_scr12 > 0.0)))

    def _jamfor_speglad(self, p: Terrangprofil) -> None:
        n = p.x_m.shape[0]
        t = tolka_terrang_homogen(terranggeometri(p), F, C)
        t_sp = tolka_terrang_homogen(terranggeometri(speglad(p)), F, C)
        self.assertEqual(t.delmodeller, t_sp.delmodeller)
        for namn in ("r_scr1", "r_scr2", "r_scr12"):
            np.testing.assert_allclose(getattr(t, namn), getattr(t_sp, namn), rtol=0.0, atol=1e-9)
        for namn in ("primar", "sekundar", "sekundar_kant"):
            kant, kant_sp = getattr(t, namn), getattr(t_sp, namn)
            self.assertEqual(kant is None, kant_sp is None)
            if kant is not None:
                self.assertEqual(kant.index, n - 1 - kant_sp.index)

    def test_spegling_ger_samma_tolkning(self) -> None:
        for x, z in (TUNN, TJOCK, VALL, TVA):
            self._jamfor_speglad(profil(x, z, 0.3, 4.0))
        rng = np.random.default_rng(11)
        for _ in range(100):
            n = int(rng.integers(4, 12))
            xs = np.concatenate(([0.0], np.sort(rng.uniform(1.0, 199.0, n - 2)), [200.0]))
            if bool(np.any(np.diff(xs) < 0.05)):
                continue
            self._jamfor_speglad(profil(list(xs), list(rng.uniform(0.0, 5.0, n)), 0.7, 2.0))

    def test_kroken_per_frekvens(self) -> None:
        geo = geo_av([0.0, 49.99, 50.0, 50.01, 100.0], [0.0, 0.0, 1.0, 0.0, 0.0], h_s=1.0, h_r=1.5)
        kanter = valj_skarmkanter(geo)

        def tolka(dl1: float | np.ndarray | None) -> Terrangtolkning:
            return tolka_terrang(
                geo,
                kanter,
                F,
                C,
                gangvagsskillnad_t1_m=dl1,
                gangvagsskillnad_t2_m=None,
                gangvagsskillnad_t12_m=None,
            )

        homogen = tolka(None)
        np.testing.assert_array_equal(homogen.r_scr1, tolka_terrang_homogen(geo, F, C).r_scr1)
        medvind = tolka(0.05)  # skuggzon: Δℓ > 0 ger r_Δℓ = 1 i alla band
        self.assertTrue(bool(np.all(medvind.r_scr1 >= homogen.r_scr1)))
        self.assertGreater(float(medvind.r_scr1[-1]), float(homogen.r_scr1[-1]))
        per_band = tolka(np.full(F.shape[0], 0.05))
        np.testing.assert_array_equal(per_band.r_scr1, medvind.r_scr1)
        with self.assertRaises(BullerFel):
            tolka(np.zeros(3))


class TestTestfallensProfiler(unittest.TestCase):
    """Profilerna i RL 18/18 (rak väg). Bara det som följer av definitionerna påstås."""

    def _tolka(self, fall_id: str, h_s: float) -> tuple[Terranggeometri, Terrangtolkning]:
        mapp = TESTSVIT / f"{fall_id}_rak_vag"
        if not mapp.is_dir():
            self.skipTest(f"Testfallet {fall_id} saknas i testsviten.")
        geo = terranggeometri(las_fall(mapp).profil(h_s))
        return geo, tolka_terrang_homogen(geo, F, C)

    def test_dal_och_plan_mark_har_ingen_skarm(self) -> None:
        # Inga inre punkter över baslinjen: ingen kandidat (avsnitt 5.21.1).
        for fall_id in ("N18R004", "N18R021", "N18R051", "N18R061", "N18R101"):
            for h_s in (0.01, 0.30, 0.75):
                geo, t = self._tolka(fall_id, h_s)
                self.assertTrue(bool(np.all(geo.h_bas_m[1:-1] <= 0.0)), fall_id)
                self.assertEqual(t.skarmtyp, INGEN_SKARM, fall_id)

    def test_tunn_skarm_fall_71_och_74(self) -> None:
        for fall_id in ("N18R071", "N18R074"):
            for h_s in (0.01, 0.30, 0.75):
                geo, t = self._tolka(fall_id, h_s)
                assert t.primar is not None and t.primar_form is not None
                self.assertEqual(float(geo.z_m[t.primar.index]), 3.0)  # enda punkten över baslinjen
                self.assertEqual(t.primar_form.antal_punkter, 3)
                self.assertEqual(t.delmodeller, (4,))
                self.assertGreater(t.primar.gangvagsskillnad_m, 0.0)

    def test_tjock_skarm_fall_81(self) -> None:
        for h_s in (0.01, 0.30, 0.75):
            geo, t = self._tolka("N18R081", h_s)
            assert t.primar is not None and t.primar_form is not None
            assert t.sekundar_kant is not None
            horn = [i for i in range(geo.x_m.shape[0]) if float(geo.z_m[i]) == 2.0]
            self.assertEqual(len(horn), 2)
            omvag = {
                i: math.hypot(float(geo.x_m[i]) - geo.x_s_m, 2.0 - geo.z_s_m)
                + math.hypot(geo.x_r_m - float(geo.x_m[i]), geo.z_r_m - 2.0)
                for i in horn
            }
            self.assertEqual(t.primar.index, max(omvag, key=lambda i: omvag[i]))
            self.assertEqual({t.primar.index, t.sekundar_kant.index}, set(horn))
            self.assertEqual(t.primar_form.antal_punkter, 4)
            self.assertEqual(t.skarmtyp, EN_SKARM_TVA_KANTER)
            self.assertIsNone(t.sekundar)

    def test_tva_skarmar_fall_91(self) -> None:
        for h_s in (0.01, 0.30, 0.75):
            geo, t = self._tolka("N18R091", h_s)
            assert t.primar is not None and t.sekundar is not None
            assert t.primar_form is not None and t.sekundar_form is not None
            toppar = {float(geo.z_m[t.primar.index]), float(geo.z_m[t.sekundar.index])}
            self.assertEqual(toppar, {2.0, 3.0})  # skärmens topp och vallens krön
            self.assertEqual(t.skarmtyp, TVA_SKARMAR)
            former = {t.primar_form.antal_punkter, t.sekundar_form.antal_punkter}
            self.assertEqual(former, {3, 5})


if __name__ == "__main__":
    unittest.main()
