"""Tester för delmodell 10 (spridningszoner) i Nord2000-kärnan, AV 1106/07 avsnitt 5.19."""

from __future__ import annotations

import importlib.util
import math
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from buller.nord2000.delmodell10 import (
    ANTAL_REMSOR,
    DATAFIL,
    RatStrale,
    Spridningszon,
    Stralbana,
    Stralpunkt,
    Zonbalte,
    Zonsnitt,
    frekvensvikt_kf,
    fresnelradie_m,
    genomslag_t,
    koherens_spridningszon,
    las_spridningstabeller,
    modifierad_stracka_m,
    naturlig_kubisk_spline,
    nivakorrektion_ae_db,
    spridningseffekt_db,
    spridningszon,
    stamtathet_nq_per_m,
    standardtabeller,
    stracka_i_zon_m,
    tabellvarde_dl_db,
    volymkvot,
    zonsnitt,
)
from buller.nord2000.frekvenser import ljudhastighet_m_s, mittfrekvenser_hz
from buller.typer import BullerFel

HAR_SCIPY = importlib.util.find_spec("scipy") is not None
C = ljudhastighet_m_s(15.0)
HOG = 1.0e4  # överkant långt över varje Fresnelform: "tillräckligt hög" zon


def zon(
    x1: float,
    x2: float,
    topp: float = HOG,
    nq: float = 0.075,
    h: float = 10.0,
    alfa: float = 0.1,
    a: float = 0.15,
) -> Spridningszon:
    return Spridningszon((Zonbalte(x1, x2, topp),), nq, h, alfa, a)


class TestDatafil(unittest.TestCase):
    def test_form_och_kalla(self) -> None:
        tab = las_spridningstabeller()
        self.assertEqual(tab.dataversion, "1")
        self.assertEqual(tab.ka.shape, (8,))
        self.assertEqual(tab.dl_db.shape, (12, 3, 3))
        self.assertEqual(tab.r_norm.tolist()[0], 0.0625)
        self.assertEqual(tab.r_norm.tolist()[-1], 10.0)
        self.assertEqual(tab.h_norm.tolist(), [0.01, 0.1, 1.0])
        self.assertEqual(tab.alfa.tolist(), [0.0, 0.2, 0.4])
        text = DATAFIL.read_text(encoding="utf-8")
        for krav in ("AV 1106/07", "tabell 8, s. 125", "tabell 9, s. 126"):
            self.assertIn(krav, text)

    def test_inre_samband_i_tabell_9(self) -> None:
        """Samband som följer av tabellen själv, inte av avskriften.

        Rad R' = 0,125 är 0 i alla kolumner, och 20·lg(8·0,125) = 0, så A_e = 0 där. Rad
        R' = 0,0625 är 6,0 i alla kolumner, och 20·lg(0,5) = −6,02. ΔL avtar med R' i varje
        kolumn och med α på varje rad.
        """
        tab = standardtabeller()
        self.assertTrue(bool(np.all(tab.dl_db[1] == 0.0)))
        self.assertTrue(bool(np.all(tab.dl_db[0] == 6.0)))
        self.assertTrue(bool(np.all(np.diff(tab.dl_db, axis=0) < 0.0)))
        self.assertTrue(bool(np.all(np.diff(tab.dl_db, axis=2) <= 0.0)))
        for h in (0.01, 0.1, 1.0):
            for alfa in (0.0, 0.2, 0.4):
                self.assertAlmostEqual(nivakorrektion_ae_db(h, alfa, 0.125), 0.0, places=12)

    def test_stickprov_ur_tabell_9(self) -> None:
        """Enstaka värden, avlästa en gång till ur sidbilden (AV 1106/07 s. 126)."""
        tab = standardtabeller()
        self.assertEqual(float(tab.dl_db[3, 0, 1]), -14.25)  # R' = 0,5, h' = 0,01, α = 0,2
        self.assertEqual(float(tab.dl_db[11, 0, 2]), -122.5)  # R' = 10, h' = 0,01, α = 0,4
        self.assertEqual(float(tab.dl_db[11, 2, 0]), -78.8)  # R' = 10, h' = 1, α = 0
        self.assertEqual(float(tab.dl_db[5, 1, 1]), -21.6)  # R' = 1, h' = 0,1, α = 0,2
        self.assertEqual(float(tab.dl_db[8, 2, 2]), -37.8)  # R' = 3, h' = 1, α = 0,4

    def _las_andrad(self, gammalt: str, nytt: str) -> None:
        text = DATAFIL.read_text(encoding="utf-8")
        self.assertIn(gammalt, text)
        with tempfile.TemporaryDirectory() as katalog:
            fil = Path(katalog) / "spridningszon_test.toml"
            fil.write_text(text.replace(gammalt, nytt, 1), encoding="utf-8")
            las_spridningstabeller(fil)

    def test_tomt_eller_felaktigt_varde_ger_fel(self) -> None:
        andringar = (
            ("[-7.5, -7.5, -7.5, -6.0,", '[-7.5, "", -7.5, -6.0,'),  # obekräftat värde
            ("[-7.5, -7.5, -7.5, -6.0,", "[-7.5, -7.5, -6.0,"),  # ett värde för lite
            ("[-7.5, -7.5, -7.5, -6.0,", "[-7.5, nan, -7.5, -6.0,"),
            ("    [6.0, 6.0, 6.0, 6.0, 6.0, 6.0, 6.0, 6.0, 6.0],\n", ""),  # en rad saknas
            ("kf = [0.00, 0.00, 0.05,", 'kf = [0.00, "", 0.05,'),
            ("r_norm = [0.0625, 0.125,", "r_norm = [0.125, 0.0625,"),
            ('kalla = "AV 1106/07 avsnitt 5.19, tabell 9, s. 126"', 'kalla = ""'),
            ("[tabell9]", "[tabell_9]"),
        )
        for gammalt, nytt in andringar:
            with self.subTest(nytt=nytt), self.assertRaises(BullerFel):
                self._las_andrad(gammalt, nytt)

    def test_saknad_fil_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            las_spridningstabeller("/finns/inte/spridningszon.toml")


class TestTabell8OchKoherens(unittest.TestCase):
    def test_knutpunkter_och_linjar_interpolation(self) -> None:
        tab = standardtabeller()
        for ka, kf in zip(tab.ka, tab.kf, strict=True):
            self.assertEqual(frekvensvikt_kf(float(ka)), float(kf))
        self.assertEqual(frekvensvikt_kf(0.35), 0.0)
        self.assertAlmostEqual(frekvensvikt_kf(0.85), 0.025, places=12)
        self.assertAlmostEqual(frekvensvikt_kf(2.25), 0.45, places=12)
        self.assertAlmostEqual(frekvensvikt_kf(15.0), 0.975, places=12)
        self.assertEqual(frekvensvikt_kf(20.0), 1.0)
        self.assertEqual(frekvensvikt_kf(250.0), 1.0)
        with self.assertRaises(BullerFel):
            frekvensvikt_kf(-0.1)
        with self.assertRaises(BullerFel):
            frekvensvikt_kf(math.nan)

    def test_nq_ekv_290(self) -> None:
        # a = 0,15 m och n″ = 0,25 m⁻² ger testfallens n_Q = 0,075 m⁻¹ (RL 18-18 tabell 2).
        self.assertAlmostEqual(stamtathet_nq_per_m(0.15, 0.25), 0.075, places=15)
        with self.assertRaises(BullerFel):
            stamtathet_nq_per_m(0.0, 0.25)

    def test_t_ekv_289(self) -> None:
        self.assertEqual(genomslag_t(0.0, 0.075), 0.0)
        self.assertAlmostEqual(genomslag_t(10.0, 0.0875), 0.25, places=15)  # (0,875/1,75)²
        self.assertEqual(genomslag_t(1.75 / 0.075, 0.075), 1.0)
        self.assertEqual(genomslag_t(500.0, 0.075), 1.0)
        with self.assertRaises(BullerFel):
            genomslag_t(-1.0, 0.075)

    def test_fs_ekv_288(self) -> None:
        self.assertEqual(koherens_spridningszon(0.0, 1.0), 1.0)
        self.assertEqual(koherens_spridningszon(1.0, 0.0), 1.0)
        self.assertEqual(koherens_spridningszon(1.0, 1.0), 0.0)
        self.assertAlmostEqual(koherens_spridningszon(0.7, 0.25), 0.825, places=15)

    def test_dl10_ekv_291(self) -> None:
        self.assertAlmostEqual(spridningseffekt_db(0.5, 0.5, -8.0), -2.5, places=15)
        self.assertEqual(spridningseffekt_db(1.0, 1.0, -12.0), -15.0)  # 1,25·12 = 15
        self.assertEqual(spridningseffekt_db(1.0, 1.0, -60.0), -15.0)
        self.assertEqual(spridningseffekt_db(0.0, 1.0, -60.0), 0.0)


class TestTabell9Interpolation(unittest.TestCase):
    def test_knutpunkter_aterges_exakt(self) -> None:
        tab = standardtabeller()
        for i, r in enumerate(tab.r_norm):
            for j, h in enumerate(tab.h_norm):
                for k, alfa in enumerate(tab.alfa):
                    self.assertEqual(
                        tabellvarde_dl_db(float(h), float(alfa), float(r)),
                        float(tab.dl_db[i, j, k]),
                    )

    def test_knutpunkter_langs_varje_axel_for_sig(self) -> None:
        """I en knutpunkt på två axlar beror värdet bara på splinen längs den tredje."""
        tab = standardtabeller()
        v = tabellvarde_dl_db(0.1, 0.2, 2.5)
        self.assertAlmostEqual(
            v, naturlig_kubisk_spline(tab.r_norm, tab.dl_db[:, 1, 1], 2.5), places=12
        )
        v = tabellvarde_dl_db(0.1, 0.3, 2.0)
        self.assertAlmostEqual(
            v, naturlig_kubisk_spline(tab.alfa, tab.dl_db[7, 1, :], 0.3), places=12
        )
        v = tabellvarde_dl_db(0.3, 0.2, 2.0)
        lg_h = np.log10(tab.h_norm)
        self.assertAlmostEqual(
            v, naturlig_kubisk_spline(lg_h, tab.dl_db[7, :, 1], math.log10(0.3)), places=12
        )

    def test_axlarnas_ordning_spelar_ingen_roll(self) -> None:
        tab = standardtabeller()
        lg_h = np.log10(tab.h_norm)
        for h, alfa, r in ((0.75, 0.1, 5.25), (0.03, 0.33, 0.4), (0.5, 0.05, 8.0)):
            per_r = np.empty(12)
            for i in range(12):
                per_alfa = np.array(
                    [
                        naturlig_kubisk_spline(lg_h, tab.dl_db[i, :, k], math.log10(h))
                        for k in range(3)
                    ]
                )
                per_r[i] = naturlig_kubisk_spline(tab.alfa, per_alfa, alfa)
            omvand = naturlig_kubisk_spline(tab.r_norm, per_r, r)
            self.assertAlmostEqual(tabellvarde_dl_db(h, alfa, r), omvand, places=10)

    def test_spline_ar_exakt_for_rat_linje_och_har_naturliga_andar(self) -> None:
        x = np.array([0.0, 0.5, 2.0, 3.0, 7.0])
        y = 3.0 - 2.0 * x
        for xq in (0.0, 0.2, 1.7, 2.0, 6.9, 7.0):
            self.assertAlmostEqual(naturlig_kubisk_spline(x, y, xq), 3.0 - 2.0 * xq, places=12)
        # Andraderivatan är noll i ändpunkterna: andra differensen går mot noll som steget.
        z = np.array([0.0, 1.0, 0.5, 2.0, 1.0])
        e = 1.0e-3
        andra = (
            naturlig_kubisk_spline(x, z, 2 * e)
            - 2.0 * naturlig_kubisk_spline(x, z, e)
            + naturlig_kubisk_spline(x, z, 0.0)
        ) / e**2
        inre = (
            naturlig_kubisk_spline(x, z, 1.0 + e)
            - 2.0 * naturlig_kubisk_spline(x, z, 1.0)
            + naturlig_kubisk_spline(x, z, 1.0 - e)
        ) / e**2
        self.assertLess(abs(andra), 0.05)
        self.assertGreater(abs(inre), 1.0)

    @unittest.skipUnless(HAR_SCIPY, "scipy saknas")
    def test_mot_scipy_naturlig_spline(self) -> None:
        from scipy.interpolate import CubicSpline  # type: ignore[import-untyped]

        tab = standardtabeller()
        for j in range(3):
            for k in range(3):
                referens = CubicSpline(tab.r_norm, tab.dl_db[:, j, k], bc_type="natural")
                for r in np.linspace(0.0625, 10.0, 211):
                    egen = naturlig_kubisk_spline(tab.r_norm, tab.dl_db[:, j, k], float(r))
                    self.assertAlmostEqual(egen, float(referens(r)), places=9)

    def test_kontinuitet_over_knutpunkter(self) -> None:
        e = 1.0e-9
        for r in (0.125, 0.75, 4.0):
            under = tabellvarde_dl_db(0.75, 0.1, r - e)
            over = tabellvarde_dl_db(0.75, 0.1, r + e)
            self.assertAlmostEqual(under, over, places=6)
        self.assertAlmostEqual(
            tabellvarde_dl_db(0.1 - e, 0.1, 2.0), tabellvarde_dl_db(0.1 + e, 0.1, 2.0), places=6
        )

    def test_mellan_grannarna_i_h_och_alfa(self) -> None:
        """Mellan två knutpunkter i h' eller α hamnar värdet mellan knutpunkternas värden."""
        tab = standardtabeller()
        for i in range(2, 12):
            r = float(tab.r_norm[i])
            for h in (0.02, 0.05, 0.3, 0.75):
                j = 0 if h < 0.1 else 1
                lagre, hogre = sorted((float(tab.dl_db[i, j, 1]), float(tab.dl_db[i, j + 1, 1])))
                v = tabellvarde_dl_db(h, 0.2, r)
                self.assertTrue(lagre - 0.35 <= v <= hogre + 0.35, (r, h, v, lagre, hogre))

    def test_utanfor_tabellen_ger_fel(self) -> None:
        for h, alfa, r in (
            (1.01, 0.1, 1.0),
            (0.009, 0.1, 1.0),
            (0.5, 0.41, 1.0),
            (0.5, -0.01, 1.0),
            (0.5, 0.1, 10.01),
            (0.5, 0.1, 0.06),
            (0.5, 0.1, math.nan),
        ):
            with self.subTest(h=h, alfa=alfa, r=r), self.assertRaises(BullerFel):
                tabellvarde_dl_db(h, alfa, r)

    def test_ae_under_tabellens_minsta_stracka(self) -> None:
        ae_min = nivakorrektion_ae_db(0.5, 0.1, 0.0625)
        self.assertAlmostEqual(ae_min, 6.0 + 20.0 * math.log10(0.5), places=12)
        self.assertEqual(nivakorrektion_ae_db(0.5, 0.1, 0.001), ae_min)
        with self.assertRaises(BullerFel):
            nivakorrektion_ae_db(0.5, 0.1, 0.0)


class TestStraleOchSnitt(unittest.TestCase):
    def test_rat_strale(self) -> None:
        s = RatStrale(3.0, 1.0, 103.0, 6.0)
        langd = math.hypot(100.0, 5.0)
        self.assertEqual(s.punkt(3.0), Stralpunkt(1.0, 0.0, langd))
        mitt = s.punkt(53.0)
        self.assertAlmostEqual(mitt.z_m, 3.5, places=12)
        self.assertAlmostEqual(mitt.r1_m + mitt.r2_m, langd, places=12)
        self.assertAlmostEqual(s.punkt(103.0).r2_m, 0.0, places=12)
        self.assertIsInstance(s, object)
        with self.assertRaises(BullerFel):
            RatStrale(5.0, 1.0, 5.0, 1.0)

    def test_stracka_i_zon(self) -> None:
        s = RatStrale(0.0, 1.0, 100.0, 5.0)
        snitt = zonsnitt(s, (Zonbalte(10.0, 80.0, HOG),))
        self.assertEqual(len(snitt), 1)
        self.assertAlmostEqual(stracka_i_zon_m(s, snitt), 70.0 * math.hypot(100.0, 4.0) / 100.0, 10)
        # Två bälten: summan av delsträckorna (R_sc = ΣR_sc,i).
        tva = zonsnitt(s, (Zonbalte(10.0, 30.0, HOG), Zonbalte(50.0, 60.0, HOG)))
        self.assertAlmostEqual(stracka_i_zon_m(s, tva), 30.0 * math.hypot(100.0, 4.0) / 100.0, 10)
        # Bälten utanför sträckan källa–mottagare klipps bort.
        klippt = zonsnitt(s, (Zonbalte(-50.0, 20.0, HOG), Zonbalte(90.0, 500.0, HOG)))
        self.assertAlmostEqual(
            stracka_i_zon_m(s, klippt), 30.0 * math.hypot(100.0, 4.0) / 100.0, 10
        )
        self.assertEqual(zonsnitt(s, (Zonbalte(-50.0, 0.0, HOG), Zonbalte(100.0, 120.0, HOG))), [])

    def test_strale_delvis_over_zonen(self) -> None:
        """Stigande stråle z = 1 + 0,04·x och överkant 3 m: inne i zonen för x < 50."""
        s = RatStrale(0.0, 1.0, 100.0, 5.0)
        snitt = zonsnitt(s, (Zonbalte(10.0, 80.0, 3.0),))
        self.assertEqual(len(snitt), 1)
        self.assertAlmostEqual(snitt[0].x1_m, 10.0, places=12)
        self.assertAlmostEqual(snitt[0].x2_m, 50.0, places=9)
        # Fallande stråle: inne i zonen för x > 50.
        fallande = RatStrale(0.0, 5.0, 100.0, 1.0)
        snitt = zonsnitt(fallande, (Zonbalte(10.0, 80.0, 3.0),))
        self.assertAlmostEqual(snitt[0].x1_m, 50.0, places=9)
        self.assertAlmostEqual(snitt[0].x2_m, 80.0, places=12)

    def test_zon_och_balte_kontrolleras(self) -> None:
        with self.assertRaises(BullerFel):
            Zonbalte(10.0, 10.0, 5.0)
        with self.assertRaises(BullerFel):
            Zonbalte(10.0, 20.0, math.inf)
        with self.assertRaises(BullerFel):
            Zonbalte(10.0, 20.0, 5.0, y_min_m=2.0, y_max_m=1.0)
        balten = (Zonbalte(30.0, 50.0, 5.0), Zonbalte(40.0, 60.0, 5.0))
        with self.assertRaises(BullerFel):
            Spridningszon(balten, 0.075, 10.0, 0.1, 0.15)
        for nq, h, alfa, a in (
            (0.0, 10.0, 0.1, 0.15),
            (0.075, 0.0, 0.1, 0.15),
            (0.075, 10.0, -0.1, 0.15),
            (0.075, 10.0, 0.1, 0.0),
        ):
            with self.assertRaises(BullerFel):
                Spridningszon((Zonbalte(10.0, 20.0, 5.0),), nq, h, alfa, a)
        with self.assertRaises(BullerFel):
            Spridningszon((), 0.075, 10.0, 0.1, 0.15)


@dataclass(frozen=True)
class Parabelstrale:
    """Krökt provstråle z = z0 + pil·4·u·(1 − u), u = x/d. Väglängden räknas som om den var rät.

    Finns bara för att pröva att gränssnittet `Stralbana` tar en godtycklig höjdkurva.
    """

    d_m: float
    z0_m: float
    pil_m: float

    @property
    def x_start_m(self) -> float:
        return 0.0

    @property
    def x_slut_m(self) -> float:
        return self.d_m

    def punkt(self, x_m: float) -> Stralpunkt:
        u = x_m / self.d_m
        return Stralpunkt(self.z0_m + self.pil_m * 4.0 * u * (1.0 - u), x_m, self.d_m - x_m)


class TestBegransadHojd(unittest.TestCase):
    def test_fresnelradie_uppfyller_ellipsens_ekvation(self) -> None:
        """Punkten på avståndet r_Fz vinkelrätt från strålen har vägskillnaden λ/8 (ekv. 108)."""
        for r1, r2, vaglangd in ((30.0, 70.0, 1.36), (5.0, 95.0, 0.034), (50.0, 50.0, 13.6)):
            r = fresnelradie_m(Stralpunkt(0.0, r1, r2), vaglangd)
            vagskillnad = math.hypot(r1, r) + math.hypot(r2, r) - (r1 + r2)
            self.assertAlmostEqual(vagskillnad, vaglangd / 8.0, places=9)

    def test_tillrackligt_hog_zon_ger_hela_strackan(self) -> None:
        s = RatStrale(0.0, 2.0, 100.0, 2.0)
        snitt = zonsnitt(s, (Zonbalte(10.0, 80.0, HOG),))
        for vaglangd in (13.6, 1.0, 0.034):
            self.assertAlmostEqual(volymkvot(s, snitt, vaglangd), 1.0, places=14)
        self.assertAlmostEqual(modifierad_stracka_m(s, snitt, 1000.0, C), 70.0, places=10)

    def test_strale_over_zonen_ger_inget_bidrag(self) -> None:
        f = mittfrekvenser_hz()
        for topp in (1.99, 2.0, -5.0):  # strålen går på 2 m; överkant under eller i strålen
            with self.subTest(topp=topp):
                res = spridningszon(f, RatStrale(0.0, 2.0, 100.0, 2.0), zon(10.0, 80.0, topp), C)
                self.assertEqual(res.stracka_m, 0.0)
                self.assertTrue(bool(np.all(res.dampning_db == 0.0)))
                self.assertTrue(bool(np.all(res.koherens == 1.0)))
                self.assertTrue(bool(np.all(res.modifierad_stracka_m == 0.0)))

    def test_lag_overkant_mot_oberoende_integration(self) -> None:
        """Vågrät stråle, överkant δ över strålen: V_sc/V_Fz = ∫Min(δ, r)·2r dx / ∫2r² dx."""
        s = RatStrale(0.0, 2.0, 100.0, 2.0)
        delta, vaglangd = 0.8, 1.36
        snitt = zonsnitt(s, (Zonbalte(10.0, 80.0, 2.0 + delta),))
        x = np.linspace(10.0, 80.0, 200001)
        # r_Fz löst ur ellipsens ekvation i sluten form, oberoende av calc_fzd:
        # √(R₁² + r²) + √(R₂² + r²) = L, L = R₁ + R₂ + λ/8.
        ell = 100.0 + vaglangd / 8.0
        r2 = ((ell**2 + x**2 - (100.0 - x) ** 2) / (2.0 * ell)) ** 2 - x**2
        r = np.sqrt(r2)
        steg = float(x[1] - x[0])

        def trapets(y: np.ndarray) -> float:
            return steg * float(0.5 * y[0] + y[1:-1].sum() + 0.5 * y[-1])

        referens = trapets(np.minimum(delta, r) * 2.0 * r) / trapets(2.0 * r * r)
        self.assertAlmostEqual(volymkvot(s, snitt, vaglangd), referens, places=5)
        self.assertTrue(0.0 < referens < 1.0)

    def test_overgangen_ar_kontinuerlig_och_monoton_i_overkantens_hojd(self) -> None:
        f = mittfrekvenser_hz()
        s = RatStrale(0.0, 2.0, 100.0, 2.0)
        foregaende = np.zeros(f.shape[0])
        foregaende_stracka = np.zeros(f.shape[0])
        for delta in (1.0e-6, 0.01, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0):
            res = spridningszon(f, s, zon(10.0, 80.0, 2.0 + delta), C)
            self.assertAlmostEqual(res.stracka_m, 70.0, places=10)
            self.assertTrue(bool(np.all(res.modifierad_stracka_m <= 70.0 + 1e-9)))
            self.assertTrue(bool(np.all(res.modifierad_stracka_m >= foregaende_stracka)))
            self.assertTrue(bool(np.all(res.dampning_db <= foregaende + 0.01)))
            if delta == 1.0e-6:
                # Överkant en mikrometer över strålen: ingen märkbar effekt (inget språng).
                self.assertLess(float(np.abs(res.dampning_db).max()), 1.0e-6)
                self.assertGreater(float(res.koherens.min()), 1.0 - 1.0e-9)
            foregaende, foregaende_stracka = res.dampning_db, res.modifierad_stracka_m
        full = spridningszon(f, s, zon(10.0, 80.0, HOG), C)
        np.testing.assert_allclose(foregaende, full.dampning_db, atol=1e-12)

    def test_begransad_bredd(self) -> None:
        s = RatStrale(0.0, 2.0, 100.0, 2.0)
        hel = (Zonbalte(10.0, 80.0, HOG),)
        halv = (Zonbalte(10.0, 80.0, HOG, y_min_m=0.0),)
        smal = (Zonbalte(10.0, 80.0, HOG, y_min_m=-0.1, y_max_m=0.1),)
        vid_sidan = (Zonbalte(10.0, 80.0, HOG, y_min_m=500.0, y_max_m=600.0),)
        vaglangd = 1.36
        self.assertAlmostEqual(volymkvot(s, zonsnitt(s, hel), vaglangd), 1.0, places=14)
        self.assertAlmostEqual(volymkvot(s, zonsnitt(s, halv), vaglangd), 0.5, places=14)
        kvot_smal = volymkvot(s, zonsnitt(s, smal), vaglangd)
        self.assertTrue(0.0 < kvot_smal < 0.1)
        self.assertEqual(volymkvot(s, zonsnitt(s, vid_sidan), vaglangd), 0.0)

    def test_krokt_hojdkurva_genom_granssnittet(self) -> None:
        """En båge som går över zonens överkant mitt i ger två snitt och kortare R_sc."""
        strale: Stralbana = Parabelstrale(100.0, 2.0, 4.0)  # z = 2 … 6 m, över 5 m för 25 < x < 75
        snitt = zonsnitt(strale, (Zonbalte(10.0, 90.0, 5.0),))
        self.assertEqual(len(snitt), 2)
        self.assertAlmostEqual(snitt[0].x1_m, 10.0, places=12)
        self.assertAlmostEqual(snitt[0].x2_m, 25.0, places=8)
        self.assertAlmostEqual(snitt[1].x1_m, 75.0, places=8)
        self.assertAlmostEqual(snitt[1].x2_m, 90.0, places=12)
        self.assertAlmostEqual(stracka_i_zon_m(strale, snitt), 30.0, places=8)
        res = spridningszon(mittfrekvenser_hz(), strale, zon(10.0, 90.0, 5.0), C)
        rak = spridningszon(
            mittfrekvenser_hz(), RatStrale(0.0, 2.0, 100.0, 2.0), zon(10.0, 90.0, 5.0), C
        )
        self.assertAlmostEqual(rak.stracka_m, 80.0, places=10)
        self.assertTrue(bool(np.all(res.dampning_db >= rak.dampning_db)))
        self.assertTrue(bool(np.all(res.koherens >= rak.koherens)))

    def test_snittens_integration_anvander_hundra_remsor(self) -> None:
        self.assertEqual(ANTAL_REMSOR, 100)  # AV 1106/07 avsnitt 5.19, texten efter ekv. (293)
        s = RatStrale(0.0, 2.0, 100.0, 2.0)
        self.assertEqual(volymkvot(s, [], 1.0), 0.0)
        self.assertEqual(stracka_i_zon_m(s, []), 0.0)
        self.assertIsInstance(zonsnitt(s, (Zonbalte(10.0, 20.0, 5.0),))[0], Zonsnitt)


class TestSpridningszon(unittest.TestCase):
    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()
        self.strale = RatStrale(0.0, 2.0, 500.0, 2.0)

    def test_ingen_zon_ger_noll(self) -> None:
        res = spridningszon(self.f, self.strale, None, C)
        self.assertEqual(res.dampning_db.shape, (27,))
        self.assertTrue(bool(np.all(res.dampning_db == 0.0)))
        self.assertTrue(bool(np.all(res.koherens == 1.0)))
        self.assertEqual(res.stracka_m, 0.0)

    def test_noll_stracka_i_zonen_ger_noll(self) -> None:
        for z in (zon(-80.0, -10.0), zon(500.0, 600.0), zon(600.0, 700.0)):
            res = spridningszon(self.f, self.strale, z, C)
            self.assertEqual(res.stracka_m, 0.0)
            self.assertTrue(bool(np.all(res.dampning_db == 0.0)))
            self.assertTrue(bool(np.all(res.koherens == 1.0)))

    def test_mycket_kort_stracka_gar_mot_noll(self) -> None:
        for langd in (1.0e-9, 1.0e-3, 0.1):
            res = spridningszon(self.f, self.strale, zon(100.0, 100.0 + langd), C)
            self.assertLess(float(np.abs(res.dampning_db).max()), 1.0e-4 * max(langd, 1e-3))
            self.assertGreater(float(res.koherens.min()), 1.0 - 1.0e-4)

    def test_dampningen_vaxer_med_strackan(self) -> None:
        """ΔL_s avtar (dämpningen växer) när sträckan i zonen ökar.

        Från R' = 0,5 är det strikt tills gränsen −15 dB nås. Under R' = 0,5 varierar A_e
        mellan knutpunkterna med några hundradels dB kring noll (tabellens avrundning och
        splinens form); eftersom T där är litet blir ΔL_s högst +0,006 dB (uppmätt +0,0054 dB
        för h' = 0,1 och α = 0), och ingen ökning mellan två steg är större än så.
        """
        strale = RatStrale(0.0, 2.0, 600.0, 2.0)  # längre än den längsta zonen (10/n_Q)
        fall = ((0.075, 10.0, 0.1), (0.05, 2.0, 0.0), (0.1, 10.0, 0.4), (0.02, 5.0, 0.2))
        for nq, h, alfa in fall:
            foregaende = np.zeros(27)
            storsta = 0.0
            storsta_okning = 0.0
            langder = np.linspace(0.0, 10.0 / nq - 0.5, 250)
            steg_m = float(langder[1])
            for langd_np in langder[1:]:
                langd = float(langd_np)
                skog = zon(20.0, 20.0 + langd, HOG, nq, h, alfa)
                res = spridningszon(self.f, strale, skog, C)
                d = res.dampning_db
                self.assertTrue(bool(np.all(d <= foregaende + 0.01)), (nq, langd))
                storsta_okning = max(storsta_okning, float((d - foregaende).max()))
                self.assertTrue(bool(np.all(np.diff(res.koherens) <= 1e-12)))
                if (langd - steg_m) * nq >= 0.5:
                    aktiva = (foregaende > -15.0) & (d < 0.0)
                    self.assertTrue(bool(np.all(d[aktiva] < foregaende[aktiva])), (nq, langd))
                storsta = max(storsta, float(d.max()))
                foregaende = d
            self.assertLess(storsta_okning, 0.006)
            self.assertLess(storsta, 0.006)
            self.assertTrue(bool(np.all(foregaende >= -15.0)))
            self.assertEqual(float(foregaende.min()), -15.0)

    def test_koherens_i_noll_till_ett_och_dampning_begransad(self) -> None:
        res = spridningszon(self.f, self.strale, zon(20.0, 150.0), C)
        self.assertTrue(bool(np.all((res.koherens >= 0.0) & (res.koherens <= 1.0))))
        self.assertTrue(bool(np.all(res.dampning_db >= -15.0)))

    def test_laga_frekvenser_paverkas_inte(self) -> None:
        """k_f = 0 för ka ≤ 0,7 (tabell 8): med a = 0,15 m alla band t.o.m. 250 Hz."""
        res = spridningszon(self.f, self.strale, zon(20.0, 150.0), C)
        ka = 2.0 * math.pi * self.f / C * 0.15
        self.assertEqual(int(np.count_nonzero(ka <= 0.7)), 11)
        self.assertTrue(bool(np.all(res.dampning_db[ka <= 0.7] == 0.0)))
        self.assertTrue(bool(np.all(res.koherens[ka <= 0.7] == 1.0)))
        self.assertTrue(bool(np.all(res.dampning_db[ka > 0.7] < 0.0)))

    def test_ett_band_mot_handrakning(self) -> None:
        """2 kHz, R_sc = 20 m, n_Q = 0,05, h' = 0,1, α = 0,2: R' = 1 är en knutpunkt (−21,6 dB)."""
        f = np.array([self.f[19]])
        res = spridningszon(f, self.strale, zon(100.0, 120.0, HOG, 0.05, 2.0, 0.2, 0.15), C)
        ka = 2.0 * math.pi * float(f[0]) / C * 0.15
        kf = 0.82 + (ka - 5.0) / 5.0 * 0.13
        t = (20.0 * 0.05 / 1.75) ** 2
        ae = -21.6 + 20.0 * math.log10(8.0)
        self.assertAlmostEqual(float(res.dampning_db[0]), 1.25 * kf * t * ae, places=9)
        self.assertAlmostEqual(float(res.koherens[0]), 1.0 - kf * t, places=12)
        self.assertAlmostEqual(float(res.modifierad_stracka_m[0]), 20.0, places=10)

    def test_reciprocitet(self) -> None:
        """Källa och mottagare byter plats (geometrin speglad): samma ΔL_s och F_s."""
        fram = spridningszon(self.f, RatStrale(0.0, 0.5, 100.0, 4.0), zon(10.0, 80.0, 6.0), C)
        bak = spridningszon(self.f, RatStrale(0.0, 4.0, 100.0, 0.5), zon(20.0, 90.0, 6.0), C)
        np.testing.assert_allclose(fram.dampning_db, bak.dampning_db, atol=1e-9)
        np.testing.assert_allclose(fram.koherens, bak.koherens, atol=1e-11)

    def test_uppdelat_balte_ger_samma_resultat(self) -> None:
        ett = spridningszon(self.f, self.strale, zon(20.0, 90.0, 4.0), C)
        delat = Spridningszon(
            (Zonbalte(20.0, 45.0, 4.0), Zonbalte(45.0, 90.0, 4.0)), 0.075, 10.0, 0.1, 0.15
        )
        tva = spridningszon(self.f, self.strale, delat, C)
        self.assertAlmostEqual(ett.stracka_m, tva.stracka_m, places=10)
        np.testing.assert_allclose(ett.dampning_db, tva.dampning_db, atol=2e-3)

    def test_testfallens_skog_gar_att_rakna(self) -> None:
        """Parametrarna i RL 18-18 tabell 2 (fall 121–124) ligger inom tabell 9.

        h' = 0,075·10 = 0,75, R' ≈ 0,075·70 = 5,25, T = 1. Ingen jämförelse mot facit: den
        kräver källmodellen och refraktion.
        """
        skog = zon(10.0, 80.0, 10.0, 0.075, 10.0, 0.1, 0.15)
        self.assertAlmostEqual(skog.normerad_hojd, 0.75, places=15)
        for h_s, h_r in ((0.01, 1.5), (0.30, 1.5), (0.01, 4.0), (0.30, 4.0)):
            res = spridningszon(self.f, RatStrale(3.25, h_s, 100.0, h_r), skog, C)
            self.assertAlmostEqual(res.stracka_m, 70.0, delta=0.06)
            self.assertEqual(genomslag_t(res.stracka_m, 0.075), 1.0)
            self.assertTrue(bool(np.all(res.dampning_db[:11] == 0.0)))
            self.assertEqual(float(res.dampning_db[-1]), -15.0)
            self.assertEqual(float(res.koherens[-1]), 0.0)

    def test_utanfor_tabell_9_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):  # h' = 0,075·20 = 1,5
            spridningszon(self.f, self.strale, zon(20.0, 90.0, HOG, 0.075, 20.0), C)
        with self.assertRaises(BullerFel):  # α = 0,5
            spridningszon(self.f, self.strale, zon(20.0, 90.0, HOG, 0.075, 10.0, 0.5), C)
        # R' > 10 (långa sneda strålar längs rak väg) begränsas till tabell 9:s övre kant (R' = 10)
        # i nivakorrektion_ae_db, där ΔL_s ändå mättas mot −15 dB vid höga frekvenser (ekv. 291).
        res_lang = spridningszon(self.f, RatStrale(0.0, 1.0, 300.0, 2.0), zon(20.0, 220.0), C)
        self.assertTrue(np.all(np.isfinite(res_lang.dampning_db)))
        self.assertAlmostEqual(float(res_lang.dampning_db[-1]), -15.0, places=6)
        with self.assertRaises(BullerFel):
            spridningszon(self.f, self.strale, zon(20.0, 90.0), 0.0)
        # h' utanför tabellen ger fel även om bara band med k_f = 0 efterfrågas.
        with self.assertRaises(BullerFel):
            spridningszon(self.f[:3], self.strale, zon(20.0, 90.0, HOG, 0.075, 20.0), C)

    def test_cirkelstrale_nedat_och_uppat(self) -> None:
        from buller.nord2000.delmodell10 import CirkelStrale

        rat = RatStrale(0.0, 1.0, 100.0, 2.0)
        noll = CirkelStrale(0.0, 1.0, 100.0, 2.0, 0.0)
        med = CirkelStrale(0.0, 1.0, 100.0, 2.0, 1.0e-4)
        mot = CirkelStrale(0.0, 1.0, 100.0, 2.0, -1.0e-4)
        self.assertAlmostEqual(noll.punkt(50.0).z_m, rat.punkt(50.0).z_m, places=9)
        # Medvind (ξ > 0) böjer strålen uppåt i mitten, motvind (ξ < 0) nedåt.
        self.assertGreater(med.punkt(50.0).z_m, rat.punkt(50.0).z_m)
        self.assertLess(mot.punkt(50.0).z_m, rat.punkt(50.0).z_m)

    def test_karna_med_spridningszon_ger_extra_dampning_vid_hoga_frekvenser(self) -> None:
        from buller.nord2000.karna import Nord2000Karna
        from buller.typer import Terrangprofil, Vader

        prof = Terrangprofil(
            x_m=np.array([0.0, 100.0]),
            z_m=np.array([0.0, 0.0]),
            flodesresistivitet_kNsm4=np.array([200.0]),
            rahet_m=np.array([0.0]),
            kalla_hojd_m=0.3,
            mottagare_hojd_m=1.5,
        )
        vader = Vader(
            temperatur_c=15.0,
            relativ_fuktighet_procent=70.0,
            vind_m_s=0.0,
            zu_m=10.0,
            z0_m=0.05,
            dt_dz_k_m=0.0,
            std_vind_m_s=0.5,
            std_dt_dz_k_m=0.0,
            cv2=0.12,
            ct2=0.008,
        )
        karna = Nord2000Karna()
        utan = karna.berakna(prof, vader)
        med = karna.berakna(prof, vader, spridningszon=zon(10.0, 80.0))
        self.assertIn("+10", med.delmodell)
        self.assertIsNotNone(med.spridningszon_db)
        # Vid 10 kHz ger 70 m skog tydlig dämpning jämfört med fri gräsmark.
        self.assertLess(float(med.overforing_db[-1]), float(utan.overforing_db[-1]) - 5.0)


if __name__ == "__main__":
    unittest.main()
