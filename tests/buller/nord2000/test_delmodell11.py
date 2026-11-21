"""Tester för delmodell 11 (reflexer mot vertikala ytor) i Nord2000-kärnan."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.nord2000.delmodell11 import (
    F_LAMBDA_REFLEX,
    Reflektor,
    Reflexgeometri,
    energireflektionskoefficient,
    fresnelzon_reflektor,
    fresnelzonsandel,
    reflexeffekt_db,
    reflexgeometri_rak_strale,
    reflexpunkt_godtas,
    storlekskorrektion_db,
    vagda_vaderkoefficienter,
)
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.fresnel import calc_fzd, fresnelzonsstorlek
from buller.typer import BullerFel

C_M_S = 340.0


def _fasad(bakat_m: float, framat_m: float, hojd_m: float = 12.0) -> Reflektor:
    """Fasad som står på marken (z = 0)."""
    return Reflektor(
        z_ovre_m=hojd_m,
        z_nedre_m=0.0,
        kant_bakat_m=bakat_m,
        kant_framat_m=framat_m,
        markhojd_m=0.0,
    )


class TestEnergireflektionOchVader(unittest.TestCase):
    def test_ekv_297(self) -> None:
        alfa = np.array([0.0, 0.3, 1.0])
        np.testing.assert_allclose(energireflektionskoefficient(alfa), [1.0, 0.7, 0.0])
        with self.assertRaises(BullerFel):
            energireflektionskoefficient(np.array([1.2]))

    def test_ekv_295_langdvagt_medel(self) -> None:
        a, b = vagda_vaderkoefficienter([30.0, 10.0], [1.0, -1.0], [0.02, 0.06])
        self.assertAlmostEqual(a, 0.5, places=12)
        self.assertAlmostEqual(b, 0.03, places=12)
        # Samma väder på båda delsträckorna ändras inte, och ordningen spelar ingen roll.
        self.assertEqual(vagda_vaderkoefficienter([7.0, 3.0], [0.4, 0.4], [0.1, 0.1]), (0.4, 0.1))
        a2, b2 = vagda_vaderkoefficienter([10.0, 30.0], [-1.0, 1.0], [0.06, 0.02])
        self.assertAlmostEqual(a2, a, places=12)
        self.assertAlmostEqual(b2, b, places=12)
        with self.assertRaises(BullerFel):
            vagda_vaderkoefficienter([10.0, 0.0], [1.0, 1.0], [0.0, 0.0])
        with self.assertRaises(BullerFel):
            vagda_vaderkoefficienter([10.0], [1.0, 1.0], [0.0])


class TestReflexgeometri(unittest.TestCase):
    def test_rak_strale(self) -> None:
        g = reflexgeometri_rak_strale(30.0, 10.0, 0.5, 4.5, 1.0)
        self.assertAlmostEqual(g.z_o_m, 3.5, places=12)
        self.assertAlmostEqual(g.r1_m + g.r2_m, math.hypot(40.0, 4.0), places=12)
        self.assertAlmostEqual(g.r1_m / g.r2_m, 3.0, places=12)
        self.assertEqual(g.theta_r_rad, 1.0)

    def test_brant_strale_vagras(self) -> None:
        with self.assertRaises(BullerFel):
            reflexgeometri_rak_strale(13.0, 20.0, 0.3, 8.0, 1.0)  # 7,7 m på 33 m > 1/5
        g = reflexgeometri_rak_strale(13.0, 20.0, 0.3, 8.0, 1.0, storsta_lutning=0.5)
        self.assertAlmostEqual(g.z_o_m, 0.3 + 7.7 * 13.0 / 33.0, places=12)

    def test_ogiltig_indata(self) -> None:
        with self.assertRaises(BullerFel):
            reflexgeometri_rak_strale(0.0, 10.0, 1.0, 1.0, 1.0)
        with self.assertRaises(BullerFel):
            Reflexgeometri(r1_m=10.0, r2_m=10.0, theta_r_rad=0.0, z_o_m=1.0)
        with self.assertRaises(BullerFel):
            Reflexgeometri(r1_m=10.0, r2_m=-1.0, theta_r_rad=1.0, z_o_m=1.0)
        with self.assertRaises(BullerFel):
            Reflektor(z_ovre_m=1.0, z_nedre_m=2.0, kant_bakat_m=1.0, kant_framat_m=1.0)
        with self.assertRaises(BullerFel):
            Reflektor(z_ovre_m=3.0, z_nedre_m=0.0, kant_bakat_m=1.0, kant_framat_m=-1.0)
        with self.assertRaises(BullerFel):
            Reflektor(
                z_ovre_m=3.0, z_nedre_m=0.0, kant_bakat_m=1.0, kant_framat_m=1.0, markhojd_m=1.0
            )


class TestFresnelzonReflektor(unittest.TestCase):
    def test_vinkelratt_infall_ger_symmetrisk_zon(self) -> None:
        """θ_r = π/2: zonen är lika åt alla håll och lika med CalcFZd(π/2) (ekv. 299)."""
        g = Reflexgeometri(r1_m=20.0, r2_m=35.0, theta_r_rad=math.pi / 2.0, z_o_m=2.0)
        zon = fresnelzon_reflektor(g, 0.2)
        d = calc_fzd(20.0, 35.0, math.pi / 2.0, 0.2)
        self.assertAlmostEqual(zon.bakat_m, d, places=12)
        self.assertAlmostEqual(zon.framat_m, d, places=12)
        self.assertAlmostEqual(zon.halvhojd_m, d, places=12)
        self.assertAlmostEqual(zon.area_m2, 4.0 * d * d, places=10)

    def test_lika_med_fresnelzonesize(self) -> None:
        """Samma geometri som FresnelZoneSize (ekv. 340–344) med reflektorn som plan."""
        r1, r2, theta, fl = 18.0, 42.0, 0.6, 0.15
        g = Reflexgeometri(r1_m=r1, r2_m=r2, theta_r_rad=theta, z_o_m=2.0)
        zon = fresnelzon_reflektor(g, fl)
        a1, a2, b = fresnelzonsstorlek(
            (r1 + r2) * math.cos(theta), r1 * math.sin(theta), r2 * math.sin(theta), fl
        )
        self.assertAlmostEqual(zon.bakat_m, a1, places=9)
        self.assertAlmostEqual(zon.framat_m, a2, places=9)
        self.assertAlmostEqual(zon.halvhojd_m, b, places=9)

    def test_rektangeln_omskriver_ellipsen(self) -> None:
        """Hörnens mittpunkter ligger på ellipsen |S'P| + |RP| = R₁ + R₂ + F·λ (ekv. 108)."""
        r1, r2, theta, fl = 12.0, 50.0, 0.4, 0.3
        zon = fresnelzon_reflektor(Reflexgeometri(r1, r2, theta, 0.0), fl)
        # Koordinater: s längs reflektorn (framåt positiv), n vinkelrätt ut, v uppåt.
        kalla = (-r1 * math.cos(theta), -r1 * math.sin(theta), 0.0)
        mottagare = (r2 * math.cos(theta), r2 * math.sin(theta), 0.0)

        def summa(s: float, v: float) -> float:
            p = (s, 0.0, v)
            return math.dist(p, kalla) + math.dist(p, mottagare)

        mal = r1 + r2 + fl
        self.assertAlmostEqual(summa(zon.framat_m, 0.0), mal, places=9)
        self.assertAlmostEqual(summa(-zon.bakat_m, 0.0), mal, places=9)
        mitt = 0.5 * (zon.framat_m - zon.bakat_m)
        self.assertAlmostEqual(summa(mitt, zon.halvhojd_m), mal, places=9)
        # Ingen punkt på ellipsen ligger högre än rektangeln: över mittpunkten är summan störst.
        for s in np.linspace(-zon.bakat_m, zon.framat_m, 41):
            self.assertGreaterEqual(summa(float(s), zon.halvhojd_m), mal - 1e-9)

    def test_zonen_vaxer_med_vaglangden_och_speglas(self) -> None:
        g = Reflexgeometri(15.0, 40.0, 0.7, 1.0)
        liten, stor = fresnelzon_reflektor(g, 0.02), fresnelzon_reflektor(g, 0.4)
        self.assertGreater(stor.bakat_m, liten.bakat_m)
        self.assertGreater(stor.framat_m, liten.framat_m)
        self.assertGreater(stor.halvhojd_m, liten.halvhojd_m)
        # Reciprocitet: byt källa och mottagare (R₁ ↔ R₂). Strålen går då åt andra hållet
        # längs reflektorn, så θ_r → π − θ_r medan "framåt" är samma håll i rummet: samma zon.
        zon = fresnelzon_reflektor(g, 0.1)
        omvand = fresnelzon_reflektor(Reflexgeometri(40.0, 15.0, math.pi - 0.7, 1.0), 0.1)
        self.assertAlmostEqual(zon.bakat_m, omvand.bakat_m, places=10)
        self.assertAlmostEqual(zon.framat_m, omvand.framat_m, places=10)
        self.assertAlmostEqual(zon.halvhojd_m, omvand.halvhojd_m, places=10)
        with self.assertRaises(BullerFel):
            fresnelzon_reflektor(g, 0.0)


class TestStorlekskorrektion(unittest.TestCase):
    def setUp(self) -> None:
        self.g = Reflexgeometri(r1_m=20.0, r2_m=30.0, theta_r_rad=1.1, z_o_m=2.0)
        self.fl = 0.25
        self.zon = fresnelzon_reflektor(self.g, self.fl)

    def test_stor_reflektor_ger_ingen_korrektion(self) -> None:
        """Reflektor mycket större än Fresnelzonen: S_refl = S_Fz, 0 dB."""
        fritt = Reflektor(
            z_ovre_m=1000.0, z_nedre_m=-1000.0, kant_bakat_m=1000.0, kant_framat_m=1000.0
        )
        self.assertEqual(fresnelzonsandel(fritt, self.g, self.fl), 1.0)
        self.assertEqual(storlekskorrektion_db(fritt, self.g, self.fl), 0.0)
        self.assertEqual(
            storlekskorrektion_db(_fasad(1000.0, 1000.0, 1000.0), self.g, self.fl), 0.0
        )

    def test_precis_tackt_zon(self) -> None:
        z = self.zon
        precis = Reflektor(
            z_ovre_m=self.g.z_o_m + z.halvhojd_m,
            z_nedre_m=self.g.z_o_m - z.halvhojd_m,
            kant_bakat_m=z.bakat_m,
            kant_framat_m=z.framat_m,
        )
        self.assertAlmostEqual(fresnelzonsandel(precis, self.g, self.fl), 1.0, places=12)

    def test_liten_fri_reflektor_gar_mot_areakvoten(self) -> None:
        """Reflektor helt inne i zonen: S_refl är reflektorns area, 20·lg(A/S_Fz) (ekv. 296)."""
        for sida in (1.0, 0.1, 0.001):
            liten = Reflektor(
                z_ovre_m=self.g.z_o_m + sida / 2.0,
                z_nedre_m=self.g.z_o_m - sida / 2.0,
                kant_bakat_m=sida / 2.0,
                kant_framat_m=sida / 2.0,
            )
            vantat = 20.0 * math.log10(sida * sida / self.zon.area_m2)
            self.assertAlmostEqual(storlekskorrektion_db(liten, self.g, self.fl), vantat, places=9)

        # Halverad sida ger fjärdedels area och −12,04 dB.
        def db(sida: float) -> float:
            r = Reflektor(self.g.z_o_m + sida / 2, self.g.z_o_m - sida / 2, sida / 2, sida / 2)
            return storlekskorrektion_db(r, self.g, self.fl)

        self.assertAlmostEqual(db(0.2) - db(0.4), -20.0 * math.log10(4.0), places=9)

    def test_spegelbild_i_marken_dubblar_liten_reflektor(self) -> None:
        """Liten reflektor på marken nära O: spegelbilden ger dubbla ytan, +6,02 dB."""
        g = Reflexgeometri(r1_m=20.0, r2_m=30.0, theta_r_rad=1.1, z_o_m=0.2)
        pa_mark = Reflektor(0.3, 0.0, 0.1, 0.1, markhojd_m=0.0)
        utan = Reflektor(0.3, 0.0, 0.1, 0.1)
        skillnad = storlekskorrektion_db(pa_mark, g, self.fl) - storlekskorrektion_db(
            utan, g, self.fl
        )
        self.assertAlmostEqual(skillnad, 20.0 * math.log10(2.0), places=9)
        # Upphöjd reflektor: spegelbilden är ett eget band under marken.
        zon = fresnelzon_reflektor(g, self.fl)
        upphojd = Reflektor(0.5, 0.3, 0.1, 0.1, markhojd_m=0.0)
        self.assertGreater(zon.halvhojd_m, 0.8)  # både bandet och spegelbandet ryms i zonen
        self.assertAlmostEqual(
            fresnelzonsandel(upphojd, g, self.fl), 2.0 * 0.2 * 0.2 / zon.area_m2, places=12
        )

    def test_hog_fasad_pa_mark_tacker_zonen_nedat(self) -> None:
        """Fasad på marken som når över zonens överkant täcker zonen även under marken."""
        g = Reflexgeometri(r1_m=20.0, r2_m=30.0, theta_r_rad=1.1, z_o_m=0.5)
        zon = fresnelzon_reflektor(g, self.fl)
        self.assertGreater(zon.halvhojd_m, 0.5)  # zonen når under marken
        self.assertEqual(fresnelzonsandel(_fasad(100.0, 100.0, 12.0), g, self.fl), 1.0)
        # Utan spegelbild täcks bara delen över marken.
        utan = Reflektor(12.0, 0.0, 100.0, 100.0)
        vantat = (0.5 + zon.halvhojd_m) / (2.0 * zon.halvhojd_m)
        self.assertAlmostEqual(fresnelzonsandel(utan, g, self.fl), vantat, places=12)

    def test_monoton_i_storlek(self) -> None:
        """Korrektionen växer (svagt) monotont med reflektorns bredd och höjd, upp till 0 dB."""
        g = Reflexgeometri(r1_m=20.0, r2_m=30.0, theta_r_rad=0.5, z_o_m=1.5)
        fl = 1.2
        forra = -math.inf
        for bredd in np.geomspace(0.01, 500.0, 60):
            nu = storlekskorrektion_db(_fasad(0.3 * bredd, 0.7 * bredd, 3.0), g, fl)
            self.assertGreaterEqual(nu, forra)
            self.assertLessEqual(nu, 0.0)
            forra = nu
        forra = -math.inf
        for hojd in np.geomspace(0.01, 500.0, 60):
            nu = storlekskorrektion_db(_fasad(2.0, 5.0, float(hojd)), g, fl)
            self.assertGreaterEqual(nu, forra)
            self.assertLessEqual(nu, 0.0)
            forra = nu
        # Strikt växande så länge reflektorn ligger inne i zonen.
        self.assertGreater(
            storlekskorrektion_db(_fasad(1.0, 1.0, 1.0), g, fl),
            storlekskorrektion_db(_fasad(0.5, 0.5, 1.0), g, fl),
        )

    def test_halva_zonen_ger_minus_sex_db(self) -> None:
        """Kant genom O vid vinkelrätt infall: halva zonen täckt, 20·lg(1/2) = −6,02 dB."""
        g = Reflexgeometri(r1_m=20.0, r2_m=30.0, theta_r_rad=math.pi / 2.0, z_o_m=500.0)
        halv = Reflektor(2000.0, -2000.0, kant_bakat_m=0.0, kant_framat_m=1000.0)
        self.assertAlmostEqual(
            storlekskorrektion_db(halv, g, 0.3), 20.0 * math.log10(0.5), places=10
        )

    def test_reflektor_utanfor_zonen(self) -> None:
        z = self.zon
        utanfor = Reflektor(
            z_ovre_m=10.0, z_nedre_m=-10.0, kant_bakat_m=-(z.framat_m + 0.5), kant_framat_m=50.0
        )
        self.assertEqual(fresnelzonsandel(utanfor, self.g, self.fl), 0.0)
        self.assertEqual(storlekskorrektion_db(utanfor, self.g, self.fl), -math.inf)


class TestReflexeffekt(unittest.TestCase):
    def setUp(self) -> None:
        self.f = mittfrekvenser_hz()
        self.g = reflexgeometri_rak_strale(13.0, 20.0, 0.3, 1.5, 1.2)

    def test_stor_hard_reflektor_ger_noll(self) -> None:
        """ρ_E = 1 och reflektor större än zonen: ingen förlust i något band."""
        stor = _fasad(5000.0, 5000.0, 5000.0)
        dl = reflexeffekt_db(self.f, np.ones_like(self.f), stor, self.g, C_M_S)
        np.testing.assert_array_equal(dl, np.zeros_like(self.f))

    def test_forsta_termen_ar_tio_lg_rho(self) -> None:
        stor = _fasad(5000.0, 5000.0, 5000.0)
        rho = np.linspace(0.2, 1.0, self.f.shape[0])
        dl = reflexeffekt_db(self.f, rho, stor, self.g, C_M_S)
        np.testing.assert_allclose(dl, 10.0 * np.log10(rho), atol=1e-12)
        dl07 = reflexeffekt_db(self.f, np.full_like(self.f, 0.7), stor, self.g, C_M_S)
        np.testing.assert_allclose(dl07, -1.549, atol=5e-4)

    def test_rho_ett_ger_enbart_storlekskorrektionen(self) -> None:
        fasad = _fasad(3.0, 8.0, 6.0)
        dl = reflexeffekt_db(self.f, np.ones_like(self.f), fasad, self.g, C_M_S)
        for i, f in enumerate(self.f):
            vantat = storlekskorrektion_db(fasad, self.g, F_LAMBDA_REFLEX * C_M_S / float(f))
            self.assertEqual(dl[i], vantat)
        # Zonen krymper med frekvensen: korrektionen avtar monotont mot 0 dB och är 0 högst upp.
        self.assertTrue(bool(np.all(np.diff(dl) >= 0.0)))
        self.assertLess(dl[0], -1.0)
        self.assertEqual(dl[-1], 0.0)

    def test_termerna_adderas(self) -> None:
        fasad = _fasad(3.0, 8.0, 6.0)
        ett = reflexeffekt_db(self.f, np.ones_like(self.f), fasad, self.g, C_M_S)
        rho = np.full_like(self.f, 0.4)
        dl = reflexeffekt_db(self.f, rho, fasad, self.g, C_M_S)
        np.testing.assert_allclose(dl - ett, 10.0 * math.log10(0.4), atol=1e-12)

    def test_tva_meter_utanfor_kant(self) -> None:
        """Reflexpunkten får ligga högst 2 m utanför en kant (avsnitt 5.20)."""
        inom = _fasad(-1.99, 50.0)
        utom = _fasad(-2.01, 50.0)
        self.assertTrue(reflexpunkt_godtas(inom, self.g.z_o_m))
        self.assertFalse(reflexpunkt_godtas(utom, self.g.z_o_m))
        self.assertFalse(reflexpunkt_godtas(_fasad(50.0, -2.01), self.g.z_o_m))
        self.assertTrue(reflexpunkt_godtas(_fasad(50.0, 50.0, 12.0), 13.99))
        self.assertFalse(reflexpunkt_godtas(_fasad(50.0, 50.0, 12.0), 14.01))
        upphangd = Reflektor(8.0, 5.0, 10.0, 10.0)
        self.assertTrue(reflexpunkt_godtas(upphangd, 3.01))
        self.assertFalse(reflexpunkt_godtas(upphangd, 2.99))
        ett = np.ones_like(self.f)
        dl_utom = reflexeffekt_db(self.f, ett, utom, self.g, C_M_S)
        self.assertTrue(bool(np.all(np.isneginf(dl_utom))))
        # Strax innanför gränsen: bidrag i låga band (stor zon), inget i höga (zonen når inte).
        dl_inom = reflexeffekt_db(self.f, ett, inom, self.g, C_M_S)
        self.assertTrue(math.isfinite(dl_inom[0]))
        self.assertLess(dl_inom[0], 0.0)
        self.assertTrue(math.isinf(dl_inom[-1]))

    def test_rho_noll_och_fel_indata(self) -> None:
        stor = _fasad(5000.0, 5000.0, 5000.0)
        rho = np.ones_like(self.f)
        rho[3] = 0.0
        dl = reflexeffekt_db(self.f, rho, stor, self.g, C_M_S)
        self.assertTrue(math.isinf(dl[3]) and dl[3] < 0.0)
        self.assertEqual(dl[4], 0.0)
        with self.assertRaises(BullerFel):
            reflexeffekt_db(self.f, np.full_like(self.f, 1.1), stor, self.g, C_M_S)
        with self.assertRaises(BullerFel):
            reflexeffekt_db(self.f, np.ones(3), stor, self.g, C_M_S)
        with self.assertRaises(BullerFel):
            reflexeffekt_db(self.f, np.ones_like(self.f), stor, self.g, 0.0)

    def test_reciprocitet(self) -> None:
        """Byt källa och mottagare (d₁ ↔ d₂, θ_r → π − θ_r, samma reflektor): samma effekt."""
        fasad = _fasad(3.0, 8.0, 6.0)
        omvand_g = reflexgeometri_rak_strale(20.0, 13.0, 1.5, 0.3, math.pi - 1.2)
        ett = np.ones_like(self.f)
        np.testing.assert_allclose(
            reflexeffekt_db(self.f, ett, fasad, self.g, C_M_S),
            reflexeffekt_db(self.f, ett, fasad, omvand_g, C_M_S),
            atol=1e-9,
        )


if __name__ == "__main__":
    unittest.main()
