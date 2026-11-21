"""Tester för källmodellens direktivitet (`buller.emission.direktivitet`, paket B5b).

Väntade tal för den medföljande datafilen står i SP Rapport 2006:12 avsnitt 2.5: ekvation
(2.11) och (2.12) (s. 14–15) och tabell 2.4 (s. 16). Övrig data här är syntetisk.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from buller.emission import (
    DATAFIL,
    Kallriktning,
    Trafikflode,
    Vagemission,
    las_emissionsdata,
    ljudeffekt_db,
)
from buller.emission.direktivitet import Direktivitetsdata
from buller.emission.kontroll import kontrollutskrift_md
from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel
from tests.buller.emission.test_modell import syntetisk_toml


def _band(frekvens_hz: float) -> int:
    return TERSBAND_HZ.index(frekvens_hz)


BAND_800_TILL_6300 = slice(_band(800.0), _band(6300.0) + 1)

SYNTETISK_DIREKTIVITET = """
[direktivitet]
kalla = "påhittad"

[[direktivitet.horisontell]]
hojd_m = 0.01
formtyp = "k0_plus_k1_abs_cos_fi"
koefficienter_db = [-1.0, 3.0]
lagsta_frekvens_hz = 1000.0
hogsta_frekvens_hz = 2000.0
bullertyp = "rull"
kalla = "påhittad"

[direktivitet.vertikal]
kalla = "påhittad"
bandgrupper_hz = [[25.0, 31.5], [10000.0]]

[[direktivitet.vertikal.delkallor]]
hojd_m = 0.01
formtyp = ["noll", "k_sin_psi"]
k_db = [0.0, -6.0]

[[direktivitet.vertikal.delkallor]]
hojd_m = 0.30
formtyp = ["k_ett_minus_cos_psi", "noll"]
k_db = [-10.0, 0.0]

[[direktivitet.vertikal.delkallor]]
hojd_m = 0.75
formtyp = ["noll", "noll"]
k_db = [0.0, 0.0]
"""


class TestKallriktning(unittest.TestCase):
    def test_ogiltiga_vinklar_ger_fel(self) -> None:
        for fi in (-0.1, math.pi + 0.1, float("nan")):
            with self.assertRaises(BullerFel):
                Kallriktning(fi)
        for psi in (2.0, -2.0, float("inf")):
            with self.assertRaises(BullerFel):
                Kallriktning(1.0, psi)
        with self.assertRaises(BullerFel):
            Kallriktning(1.0, (0.0, 2.0))

    def test_hojdvinkel_som_tal_galler_alla_delkallor(self) -> None:
        self.assertEqual(Kallriktning(1.0, 0.25).hojdvinklar_rad(3), (0.25, 0.25, 0.25))
        self.assertEqual(Kallriktning(1.0, (0.1, 0.2)).hojdvinklar_rad(2), (0.1, 0.2))
        with self.assertRaises(BullerFel):
            Kallriktning(1.0, (0.1, 0.2)).hojdvinklar_rad(3)


class TestMedfoljandeDirektivitet(unittest.TestCase):
    """Den medföljande datafilen mot SP 2006:12 ekv. (2.11), (2.12) och tabell 2.4."""

    data: Direktivitetsdata

    @classmethod
    def setUpClass(cls) -> None:
        direktivitet = las_emissionsdata(DATAFIL).direktivitet
        assert direktivitet is not None
        cls.data = direktivitet

    def test_horisontell_lagsta_kallan_ekvation_2_12(self) -> None:
        # Ekv. (2.12b): (−2,5 + 4·|cos φ|)·√cos ψ för 800–6300 Hz; (2.12a): 0 i övriga band.
        fram = self.data.horisontell_db(0.01, 0.0, 0.0)
        np.testing.assert_allclose(fram[BAND_800_TILL_6300], 1.5, atol=1e-12)
        tvars = self.data.horisontell_db(0.01, math.pi / 2.0, 0.0)
        np.testing.assert_allclose(tvars[BAND_800_TILL_6300], -2.5, atol=1e-12)
        for varde in (fram, tvars):
            np.testing.assert_array_equal(varde[: _band(800.0)], 0.0)
            np.testing.assert_array_equal(varde[_band(8000.0) :], 0.0)
        self.assertEqual(float(fram[_band(630.0)]), 0.0)
        self.assertNotEqual(float(fram[_band(800.0)]), 0.0)
        self.assertNotEqual(float(fram[_band(6300.0)]), 0.0)
        # Symmetri framåt/bakåt (absolutbeloppet) och faktorn √cos ψ.
        np.testing.assert_allclose(
            self.data.horisontell_db(0.01, math.radians(30.0), 0.0),
            self.data.horisontell_db(0.01, math.radians(150.0), 0.0),
            atol=1e-12,
        )
        snett = self.data.horisontell_db(0.01, math.radians(60.0), math.radians(60.0))
        self.assertAlmostEqual(
            float(snett[_band(1000.0)]), (-2.5 + 4.0 * 0.5) * math.sqrt(0.5), places=12
        )

    def test_horisontell_030_saknas_ekvation_2_10c(self) -> None:
        np.testing.assert_array_equal(self.data.horisontell_db(0.30, 0.3, 0.2), 0.0)

    def test_horisontell_075_ekvation_2_11(self) -> None:
        # Ekv. (2.11): (1,546·x³ − 1,425·x² + 0,22·x + 0,6)·√cos ψ med x = π/2 − φ, alla band.
        def polynom(x: float) -> float:
            return 1.546 * x**3 - 1.425 * x**2 + 0.22 * x + 0.6

        for grader in (0.0, 45.0, 90.0, 135.0, 180.0):
            fi = math.radians(grader)
            with self.subTest(grader=grader):
                np.testing.assert_allclose(
                    self.data.horisontell_db(0.75, fi, 0.0),
                    polynom(math.pi / 2.0 - fi),
                    atol=1e-12,
                )
        np.testing.assert_allclose(self.data.horisontell_db(0.75, math.pi / 2.0, 0.0), 0.6)
        # Figur 2.3: ca +3,4 dB rakt fram och ca −9,3 dB rakt bak.
        self.assertAlmostEqual(float(self.data.horisontell_db(0.75, 0.0, 0.0)[0]), 3.42, places=2)
        self.assertAlmostEqual(
            float(self.data.horisontell_db(0.75, math.pi, 0.0)[0]), -9.25, places=2
        )
        np.testing.assert_allclose(
            self.data.horisontell_db(0.75, math.pi / 2.0, math.radians(60.0)),
            0.6 * math.sqrt(0.5),
            atol=1e-12,
        )

    def test_vertikal_tabell_2_4(self) -> None:
        psi = math.radians(30.0)
        sin, cos = math.sin(psi), math.cos(psi)
        kvadrat, enkel = 1.0 - cos**2, 1.0 - cos
        # (delkällhöjd, frekvens i gruppens mitt, väntat värde) rad för rad ur tabell 2.4.
        vantat = {
            0.01: [0.0, 0.0, -2 * kvadrat, -3 * kvadrat, -4 * kvadrat, -4 * kvadrat, 0.0, 0.0],
            0.30: [
                -2 * sin,
                -4 * sin,
                -5 * kvadrat,
                -5 * kvadrat,
                -6 * kvadrat,
                -6 * kvadrat,
                -5 * kvadrat,
                -8 * enkel,
            ],
            0.75: [
                0.0,
                0.0,
                -2 * kvadrat,
                -3 * kvadrat,
                -3 * kvadrat,
                -2 * kvadrat,
                -2 * enkel,
                -2 * enkel,
            ],
        }
        grupper = [
            (50.0, 63.0, 80.0),
            (100.0, 125.0, 160.0),
            (200.0, 250.0, 315.0),
            (400.0, 500.0, 630.0),
            (800.0, 1000.0, 1250.0),
            (1600.0, 2000.0, 2500.0),
            (3150.0, 4000.0, 5000.0),
            (6300.0, 8000.0, 10000.0),
        ]
        for hojd, varden in vantat.items():
            beraknat = self.data.vertikal_db(hojd, psi)
            for grupp, varde in zip(grupper, varden, strict=True):
                for frekvens in grupp:
                    with self.subTest(hojd=hojd, frekvens=frekvens):
                        self.assertAlmostEqual(float(beraknat[_band(frekvens)]), varde, places=12)
            # Tabellen börjar på 50 Hz.
            np.testing.assert_array_equal(beraknat[: _band(50.0)], 0.0)

    def test_vertikal_ar_noll_i_horisontalplanet(self) -> None:
        for hojd in (0.01, 0.30, 0.75):
            np.testing.assert_array_equal(self.data.vertikal_db(hojd, 0.0), 0.0)
        with self.assertRaises(BullerFel):
            self.data.vertikal_db(0.5, 0.1)

    def test_kontrollutskriften_har_direktivitetens_tabeller(self) -> None:
        text = kontrollutskrift_md(las_emissionsdata(DATAFIL))
        self.assertIn("| 0.01 | `k0_plus_k1_abs_cos_fi` | -2.5, 4 | 800–6300, noll i", text)
        self.assertIn("| 0.75 | `polynom_i_pi_halva_minus_fi` | 0.6, 0.22, -1.425, 1.546 |", text)
        self.assertIn("| 50, 63, 80 | 0 | -2·sin ψ | 0 | ☐ |", text)
        self.assertIn(
            "| 6300, 8000, 10000 | 0 | -8·(1 − cos ψ) | -2·(1 − cos ψ) | ☐ |", text
        )
        self.assertNotIn("- Direktivitet:", text)


class TestModellMedRiktning(unittest.TestCase):
    """`Vagemission` med den medföljande datafilen och en riktning mot mottagaren."""

    def setUp(self) -> None:
        self.modell = Vagemission()
        self.tvars = Kallriktning(math.pi / 2.0, 0.0)

    def test_utan_riktning_ar_kallan_rundstralande(self) -> None:
        self.assertTrue(self.modell.stoder_direktivitet)
        trafik = [Trafikflode(1, 10000.0, 80.0)]
        utan = self.modell.ljudeffekt_db(trafik, 25.0, 86400.0)
        np.testing.assert_array_equal(
            utan, self.modell.ljudeffekt_db(trafik, 25.0, 86400.0, riktning=None)
        )
        # 1 kHz, kategori 1 vid 70 km/h: aR = 93,3 och aP = 79,3 (tabell A.1), som före B5b.
        latt = self.modell.ljudeffekt_per_fordon_db(1, 70.0)
        self.assertAlmostEqual(
            float(latt[0, 16]), 10.0 * math.log10(0.8 * 10.0**9.33 + 0.2 * 10.0**7.93), places=9
        )

    def test_latt_fordon_tvars_vagen(self) -> None:
        # φ = 90°, ψ = 0: bara ekv. (2.12) verkar, −2,5 dB på den lägsta källan i 800–6300 Hz.
        utan = self.modell.ljudeffekt_per_fordon_db(1, 70.0)
        med = self.modell.ljudeffekt_per_fordon_db(1, 70.0, riktning=self.tvars)
        skillnad = med[:2] - utan[:2]
        np.testing.assert_allclose(skillnad[0, BAND_800_TILL_6300], -2.5, atol=1e-9)
        np.testing.assert_allclose(skillnad[0, : _band(800.0)], 0.0, atol=1e-9)
        np.testing.assert_allclose(skillnad[0, _band(8000.0) :], 0.0, atol=1e-9)
        np.testing.assert_allclose(skillnad[1], 0.0, atol=1e-9)
        self.assertTrue(bool(np.all(np.isneginf(med[2]))))

    def test_tungt_fordon_fram_och_bak(self) -> None:
        # Kategori 2: 0,01 m får ekv. (2.12) och 0,75 m ekv. (2.11); 0,30 m används inte.
        utan = self.modell.ljudeffekt_per_fordon_db(2, 70.0)
        fram = self.modell.ljudeffekt_per_fordon_db(2, 70.0, riktning=Kallriktning(0.0))
        bak = self.modell.ljudeffekt_per_fordon_db(2, 70.0, riktning=Kallriktning(math.pi))
        np.testing.assert_allclose((fram[0] - utan[0])[BAND_800_TILL_6300], 1.5, atol=1e-9)
        np.testing.assert_allclose((bak[0] - utan[0])[BAND_800_TILL_6300], 1.5, atol=1e-9)
        x = math.pi / 2.0
        np.testing.assert_allclose(
            fram[2] - utan[2], 1.546 * x**3 - 1.425 * x**2 + 0.22 * x + 0.6, atol=1e-9
        )
        np.testing.assert_allclose(
            bak[2] - utan[2], -1.546 * x**3 - 1.425 * x**2 - 0.22 * x + 0.6, atol=1e-9
        )
        self.assertTrue(bool(np.all(np.isneginf(fram[1]))))

    def test_hojdvinkel_per_delkalla_och_radordning(self) -> None:
        trafik = [Trafikflode(1, 100.0, 80.0)]
        psi = (math.radians(10.0), math.radians(20.0), math.radians(30.0))
        utan = self.modell.ljudeffekt_db(trafik, 10.0, 3600.0)
        rak = self.modell.ljudeffekt_db(
            trafik, 10.0, 3600.0, riktning=Kallriktning(math.pi / 2.0, psi)
        )
        # 0,30 m vid 100 Hz: −4·sin ψ (tabell 2.4) med den delkällans egen vinkel, 20°.
        self.assertAlmostEqual(
            float(rak[1, _band(100.0)] - utan[1, _band(100.0)]), -4.0 * math.sin(psi[1]), places=9
        )
        # 0,01 m vid 250 Hz: −2·(1 − cos² ψ) med 10°.
        self.assertAlmostEqual(
            float(rak[0, _band(250.0)] - utan[0, _band(250.0)]),
            -2.0 * math.sin(psi[0]) ** 2,
            places=9,
        )
        omvand = self.modell.ljudeffekt_db(
            trafik,
            10.0,
            3600.0,
            [0.75, 0.30, 0.01],
            riktning=Kallriktning(math.pi / 2.0, psi[::-1]),
        )
        np.testing.assert_array_equal(omvand, rak[::-1, :])
        via_funktion = ljudeffekt_db(
            trafik, 10.0, 3600.0, riktning=Kallriktning(math.pi / 2.0, psi), modell=self.modell
        )
        np.testing.assert_array_equal(via_funktion, rak)
        with self.assertRaises(BullerFel):
            self.modell.ljudeffekt_db(
                trafik, 10.0, 3600.0, riktning=Kallriktning(1.0, (0.1, 0.2))
            )

    def test_tva_lasningar_av_vad_den_horisontella_direktiviteten_verkar_pa(self) -> None:
        # 1 kHz, kategori 1 vid 70 km/h, lägsta källan: 80 % av rullbullret (aR = 93,3) och
        # 20 % av drivlinebullret (aP = 79,3). Tvärs vägen ger ekv. (2.12) −2,5 dB.
        delkalla = Vagemission().ljudeffekt_per_fordon_db(1, 70.0, riktning=self.tvars)
        bullertyp = Vagemission(horisontell_verkan="bullertyp").ljudeffekt_per_fordon_db(
            1, 70.0, riktning=self.tvars
        )
        rull, drivlina = 0.8 * 10.0**9.33, 0.2 * 10.0**7.93
        self.assertAlmostEqual(
            float(delkalla[0, 16]), 10.0 * math.log10((rull + drivlina) * 10.0**-0.25), places=9
        )
        self.assertAlmostEqual(
            float(bullertyp[0, 16]), 10.0 * math.log10(rull * 10.0**-0.25 + drivlina), places=9
        )
        # Kategori 2, 0,75 m rakt fram: ekv. (2.11) på allt, eller bara på drivlinebullret
        # (aR = 95,6, aP = 95,5 vid 1 kHz; 20 % av rullbullret och 80 % av drivlinebullret).
        fram = Kallriktning(0.0)
        x = math.pi / 2.0
        tillagg = 10.0 ** ((1.546 * x**3 - 1.425 * x**2 + 0.22 * x + 0.6) / 10.0)
        rull, drivlina = 0.2 * 10.0**9.56, 0.8 * 10.0**9.55
        hel = Vagemission().ljudeffekt_per_fordon_db(2, 70.0, riktning=fram)
        delad = Vagemission(horisontell_verkan="bullertyp").ljudeffekt_per_fordon_db(
            2, 70.0, riktning=fram
        )
        self.assertAlmostEqual(
            float(hel[2, 16]), 10.0 * math.log10((rull + drivlina) * tillagg), places=9
        )
        self.assertAlmostEqual(
            float(delad[2, 16]), 10.0 * math.log10(rull + drivlina * tillagg), places=9
        )
        with self.assertRaises(BullerFel):
            Vagemission(horisontell_verkan="annat")  # type: ignore[arg-type]


class TestInlasningAvDirektivitet(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fil = Path(self._tmp.name) / "data.toml"

    def _modell(self, direktivitet: str) -> Vagemission:
        self.fil.write_text(syntetisk_toml() + direktivitet, encoding="utf-8")
        return Vagemission(las_emissionsdata(self.fil))

    def test_data_utan_direktivitet_vagrar_riktning(self) -> None:
        modell = self._modell("")
        self.assertFalse(modell.stoder_direktivitet)
        trafik = [Trafikflode(1, 100.0, 80.0)]
        self.assertEqual(modell.ljudeffekt_db(trafik, 10.0, 3600.0).shape, (3, ANTAL_BAND))
        with self.assertRaises(BullerFel) as ctx:
            modell.ljudeffekt_db(trafik, 10.0, 3600.0, riktning=Kallriktning(1.0))
        self.assertIn("[direktivitet]", str(ctx.exception))
        self.assertNotIn("## Direktivitet", kontrollutskrift_md(modell.data))

    def test_syntetisk_direktivitet_mot_handrakning(self) -> None:
        modell = self._modell(SYNTETISK_DIREKTIVITET)
        self.assertTrue(modell.stoder_direktivitet)
        psi = math.radians(30.0)
        tillagg = modell.direktivitet_db(Kallriktning(0.0, psi))
        for typ in ("rull", "drivlina"):
            # Horisontellt: (−1 + 3·1)·√cos 30° i 1000–2000 Hz för 0,01 m, noll annars.
            np.testing.assert_allclose(
                tillagg[typ][0, _band(1000.0) : _band(2000.0) + 1],
                2.0 * math.sqrt(math.cos(psi)),
                atol=1e-12,
            )
            self.assertEqual(float(tillagg[typ][0, _band(800.0)]), 0.0)
            self.assertEqual(float(tillagg[typ][0, _band(2500.0)]), 0.0)
            # Vertikalt: −6·sin 30° vid 10 kHz för 0,01 m; −10·(1 − cos 30°) vid 25 Hz för 0,30 m.
            self.assertAlmostEqual(float(tillagg[typ][0, _band(10000.0)]), -3.0, places=12)
            self.assertAlmostEqual(
                float(tillagg[typ][1, _band(25.0)]), -10.0 * (1.0 - math.cos(psi)), places=12
            )
            self.assertEqual(float(tillagg[typ][1, _band(40.0)]), 0.0)
            np.testing.assert_array_equal(tillagg[typ][2], 0.0)
        text = kontrollutskrift_md(modell.data)
        self.assertIn("| 0.01 | `k0_plus_k1_abs_cos_fi` | -1, 3 | 1000–2000, noll i övriga |", text)
        self.assertIn("| 25, 31.5 | 0 | -10·(1 − cos ψ) | 0 | ☐ |", text)

    def test_felaktig_direktivitet_ger_fel(self) -> None:
        fel = {
            "okänd horisontell formtyp": ('"k0_plus_k1_abs_cos_fi"', '"okand"'),
            "frekvens som inte är ett tersband": ("hz = 1000.0", "hz = 900.0"),
            "fel antal koefficienter": ("[-1.0, 3.0]", "[-1.0, 3.0, 2.0]"),
            "höjd som inte är en delkälla": (
                'hojd_m = 0.01\nformtyp = "k0',
                'hojd_m = 0.02\nformtyp = "k0',
            ),
            "okänd bullertyp": ('bullertyp = "rull"', 'bullertyp = "avgas"'),
            "okänd vertikal formtyp": ('["noll", "k_sin_psi"]', '["noll", "k_tan_psi"]'),
            "band två gånger": ("[[25.0, 31.5], [10000.0]]", "[[25.0, 31.5], [25.0]]"),
            "saknad delkälla": ("hojd_m = 0.75", "hojd_m = 0.30"),
            "noll med koefficient": ("k_db = [0.0, 0.0]", "k_db = [0.0, 1.0]"),
            "fel antal k": ("k_db = [0.0, -6.0]", "k_db = [0.0]"),
        }
        for namn, (gammalt, nytt) in fel.items():
            with self.subTest(fel=namn):
                self.assertEqual(SYNTETISK_DIREKTIVITET.count(gammalt), 1)
                with self.assertRaises(BullerFel):
                    self._modell(SYNTETISK_DIREKTIVITET.replace(gammalt, nytt))
        with self.assertRaises(BullerFel):
            self._modell("\n[direktivitet]\nkalla = \"utan vertikal\"\n")


if __name__ == "__main__":
    unittest.main()
