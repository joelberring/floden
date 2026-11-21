"""Tester för källmodellen väg (`buller.emission`). All koefficientdata här är syntetisk."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from buller.emission import (
    DATAFIL,
    Trafikflode,
    Vagemission,
    Vagforhallanden,
    las_emissionsdata,
    ljudeffekt_db,
)
from buller.emission.kontroll import kontrollutskrift_md
from buller.typer import ANTAL_BAND, BullerFel


def _lista(varden: list[float]) -> str:
    return "[" + ", ".join(repr(v) for v in varden) + "]"


def syntetisk_toml(fordelning_rull: str = "[0.8, 0.2, 0.0]", antal_a: int = ANTAL_BAND) -> str:
    """Påhittade koefficienter (inte Nord2000) för att pröva inläsning och räkning."""
    a_rull = _lista([90.0 + 0.5 * i for i in range(antal_a)])
    b = _lista([30.0] * ANTAL_BAND)
    a_driv = _lista([85.0 - 0.25 * i for i in range(ANTAL_BAND)])
    b_driv = _lista([8.0] * ANTAL_BAND)
    return f"""
[meta]
namn = "syntetisk"
dataversion = "test"
syntetisk = true
verifierad_av_manniska = false

[delkallor]
hojder_m = [0.01, 0.30, 0.75]
kalla = "påhittad"

[kategorier.1]
namn = "syntetisk kategori"

[kategorier.1.rull]
formtyp = "a_plus_b_lg_v_genom_vref"
referenshastighet_km_h = 70.0
a_db = {a_rull}
b_db = {b}
fordelning = {fordelning_rull}
kalla = "påhittad"

[kategorier.1.drivlina]
formtyp = "a_plus_b_ganger_v_minus_vref_genom_vref"
referenshastighet_km_h = 70.0
a_db = {a_driv}
b_db = {b_driv}
fordelning = [0.0, 0.5, 0.5]
kalla = "påhittad"

[kategorier.2]
namn = "tom kategori"

[kategorier.2.rull]
formtyp = ""
a_db = []
b_db = []
fordelning = []
kalla = ""

[kategorier.2.drivlina]
formtyp = ""
a_db = []
b_db = []
fordelning = []
kalla = ""
"""


class EmissionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mapp = Path(self._tmp.name)

    def _modell(self, text: str | None = None) -> Vagemission:
        fil = self.mapp / "data.toml"
        fil.write_text(text if text is not None else syntetisk_toml(), encoding="utf-8")
        return Vagemission(las_emissionsdata(fil))


class TestTomData(EmissionsTest):
    """En datafil med tomma tabeller går att läsa men inte att räkna med."""

    def setUp(self) -> None:
        super().setUp()
        text = syntetisk_toml()
        start = text.index("[kategorier.2]")
        tom = text[start:]
        self.tom_text = text[:start].split("[kategorier.1]")[0] + tom.replace(
            "kategorier.2", "kategorier.1"
        )

    def test_tom_fil_ar_lasbar_och_listar_vad_som_saknas(self) -> None:
        data = self._modell(self.tom_text).data
        self.assertFalse(data.fullstandig)
        self.assertEqual(data.kategorier, {})
        self.assertIn("kategorier.1.rull.a_db", data.saknas)
        self.assertIn("kategorier.1.drivlina.fordelning", data.saknas)
        self.assertIn("kategorier.1.rull.referenshastighet_km_h", data.saknas)

    def test_tom_data_ger_tydligt_fel(self) -> None:
        modell = self._modell(self.tom_text)
        with self.assertRaises(BullerFel) as ctx:
            modell.ljudeffekt_db([Trafikflode(1, 10000.0, 80.0)], 10.0, 86400.0)
        text = str(ctx.exception)
        self.assertIn("kategorier.1.rull.a_db", text)
        self.assertIn("inga värden får gissas", text)

    def test_kontrollutskrift_for_tom_fil_listar_saknade_falt(self) -> None:
        text = kontrollutskrift_md(self._modell(self.tom_text).data)
        self.assertIn("`kategorier.1.drivlina.b_db`", text)
        self.assertIn("Fullständig: NEJ", text)
        self.assertIn("Att mata in för hand", text)


class TestMedfoljandeData(unittest.TestCase):
    """Den medföljande datafilen (SP Rapport 2006:12). Väntade tal står i tabell A.1–A.3."""

    def setUp(self) -> None:
        self.modell = Vagemission()

    def test_fil_ar_fullstandig_men_inte_verifierad_av_manniska(self) -> None:
        data = las_emissionsdata(DATAFIL)
        self.assertTrue(data.fullstandig)
        self.assertFalse(data.verifierad_av_manniska)
        self.assertFalse(data.syntetisk)
        self.assertEqual(data.delkallhojder_m, (0.01, 0.30, 0.75))
        self.assertEqual(sorted(data.kategorier), [1, 2, 3])
        for kategori in data.kategorier.values():
            for tabell in kategori.tabeller.values():
                self.assertEqual(tabell.referenshastighet_km_h, 70.0)
                self.assertIn("SP Rapport 2006:12, tabell A.1 (s. 46)", tabell.kalla)

    def test_inbordes_samband_i_tabell_a1(self) -> None:
        k = self.modell.data.kategorier
        np.testing.assert_array_equal(k[1].tabeller["rull"].b_db, k[2].tabeller["rull"].b_db)
        np.testing.assert_array_equal(k[2].tabeller["rull"].b_db, k[3].tabeller["rull"].b_db)
        # Ekvation (A.2) med fyra axlar: 10·lg(4/2) = 3,0 dB med tabellens en decimal.
        np.testing.assert_allclose(
            k[3].tabeller["rull"].a_db - k[2].tabeller["rull"].a_db, 3.0, atol=1e-9
        )

    def test_delkallor_per_kategori(self) -> None:
        latt = self.modell.ljudeffekt_per_fordon_db(1, 70.0)
        tung = self.modell.ljudeffekt_per_fordon_db(2, 70.0)
        self.assertTrue(bool(np.all(np.isneginf(latt[2]))))  # 0,75 m används inte av kategori 1
        self.assertTrue(bool(np.all(np.isneginf(tung[1]))))  # 0,30 m används inte av kategori 2
        # 1 kHz, kategori 1 vid 70 km/h: aR = 93,3 och aP = 79,3 (tabell A.1).
        lag = 10.0 * math.log10(0.8 * 10.0**9.33 + 0.2 * 10.0**7.93)
        hog = 10.0 * math.log10(0.2 * 10.0**9.33 + 0.8 * 10.0**7.93)
        self.assertAlmostEqual(float(latt[0, 16]), lag, places=9)
        self.assertAlmostEqual(float(latt[1, 16]), hog, places=9)

    def test_hastighetsberoende_enligt_ekvation_a1_och_a3(self) -> None:
        # 25 Hz, kategori 1: aR = 69,9, bR = 33; aP = 89,8, bP = 2 (tabell A.1).
        niva = self.modell.ljudeffekt_per_fordon_db(1, 140.0)
        rull = 69.9 + 33.0 * math.log10(2.0)
        drivlina = 89.8 + 2.0 * 1.0
        total = 10.0 * math.log10(10.0 ** (rull / 10.0) + 10.0 ** (drivlina / 10.0))
        summa = 10.0 * np.log10(np.sum(10.0 ** (niva[:2, 0] / 10.0)))
        self.assertAlmostEqual(float(summa), total, places=9)

    def test_dubblad_trafik_ger_tre_decibel(self) -> None:
        en = self.modell.ljudeffekt_db([Trafikflode(1, 5000.0, 80.0)], 25.0, 86400.0)
        tva = self.modell.ljudeffekt_db([Trafikflode(1, 10000.0, 80.0)], 25.0, 86400.0)
        self.assertEqual(tva.shape, (3, ANTAL_BAND))
        np.testing.assert_allclose(tva[:2] - en[:2], 10.0 * math.log10(2.0), atol=1e-9)

    def _skillnad(self, flode: Trafikflode, forhallanden: Vagforhallanden) -> np.ndarray:
        """Ändring i rullbullret: kategori 1/2 vid lägsta källan, höga band (rull dominerar)."""
        ref = Trafikflode(flode.kategori, flode.antal_fordon, flode.hastighet_km_h)
        ny = self.modell.rullkorrektion_db(flode, forhallanden)
        gammal = self.modell.rullkorrektion_db(ref, Vagforhallanden())
        return np.asarray(ny - gammal, dtype=np.float64) + np.zeros(ANTAL_BAND)

    def test_belaggning_alder_och_temperatur(self) -> None:
        f1, f2 = Trafikflode(1, 1.0, 70.0), Trafikflode(2, 1.0, 70.0)
        # Ekvation (2.3): DAC 12 ger −0,3 + 0,25·(12 − 11) = −0,05; SMA 16 ger 0,3 + 1,25.
        np.testing.assert_allclose(self._skillnad(f1, Vagforhallanden("DAC", 12.0)), -0.05)
        np.testing.assert_allclose(self._skillnad(f1, Vagforhallanden("SMA", 16.0)), 1.55)
        np.testing.assert_allclose(self._skillnad(f2, Vagforhallanden("DAC", 12.0)), 0.0)
        # Ekvation (2.4): ett år gammal yta ger −(0,2 − 1,2 + 1,6) = −0,6; tre år ger 0.
        np.testing.assert_allclose(
            self._skillnad(f1, Vagforhallanden("DAC", 11.0, belaggningsalder_ar=1.0)), -0.3 - 0.6
        )
        np.testing.assert_allclose(
            self._skillnad(f1, Vagforhallanden("DAC", 11.0, belaggningsalder_ar=3.0)), -0.3
        )
        # Ekvation (2.5), DAC: K = 0,10; 15 °C ger +0,5 dB för kategori 1 och hälften för 2.
        kall = Vagforhallanden("DAC", 11.0, lufttemperatur_c=15.0)
        np.testing.assert_allclose(self._skillnad(f1, kall), -0.3 + 0.5)
        np.testing.assert_allclose(self._skillnad(f2, kall), 0.25)
        with self.assertRaises(BullerFel):  # K för SMA är inte inlagd
            self.modell.rullkorrektion_db(f1, Vagforhallanden("SMA", 11.0, lufttemperatur_c=5.0))
        with self.assertRaises(BullerFel):
            self.modell.rullkorrektion_db(f1, Vagforhallanden(lufttemperatur_c=5.0))
        with self.assertRaises(BullerFel):
            self.modell.rullkorrektion_db(f1, Vagforhallanden("DAC", 20.0))
        with self.assertRaises(BullerFel):
            Vagforhallanden(belaggning="DAC")

    def test_axlar_land_och_acceleration(self) -> None:
        f3 = Trafikflode(3, 1.0, 70.0, antal_axlar=8.0)
        np.testing.assert_allclose(self._skillnad(f3, Vagforhallanden()), 10.0 * math.log10(2.0))
        with self.assertRaises(BullerFel):
            self.modell.rullkorrektion_db(Trafikflode(1, 1.0, 70.0, antal_axlar=3.0),
                                          Vagforhallanden())
        sverige = self._skillnad(Trafikflode(1, 1.0, 70.0), Vagforhallanden(land="SE"))
        # Tabell A.2: 0 under 250 Hz, +1,0 vid 250 Hz, +2,0 vid 1 kHz, −4,0 vid 4 kHz och
        # +2,0 vid 10 kHz.
        self.assertEqual(
            [float(sverige[i]) for i in (0, 9, 10, 16, 22, 26)], [0.0, 0.0, 1.0, 2.0, -4.0, 2.0]
        )
        np.testing.assert_allclose(
            self._skillnad(Trafikflode(1, 1.0, 70.0), Vagforhallanden(land="DK")), 0.0
        )
        with self.assertRaises(BullerFel):
            self.modell.rullkorrektion_db(Trafikflode(1, 1.0, 70.0), Vagforhallanden(land="XX"))
        # Ekvation (2.8), tabell 2.2: C = 4,4 för kategori 1 och 5,6 för kategori 2 och 3.
        self.assertAlmostEqual(
            self.modell.drivlinekorrektion_db(Trafikflode(1, 1.0, 50.0, acceleration_m_s2=1.0)), 4.4
        )
        self.assertAlmostEqual(
            self.modell.drivlinekorrektion_db(Trafikflode(3, 1.0, 50.0, acceleration_m_s2=-0.5)),
            -2.8,
        )
        with self.assertRaises(BullerFel):
            self.modell.drivlinekorrektion_db(Trafikflode(1, 1.0, 50.0, acceleration_m_s2=2.5))

    def test_maxniva_ar_inte_inford(self) -> None:
        with self.assertRaises(BullerFel):
            self.modell.maxniva_ljudeffekt_db(1, 80.0)
        with self.assertRaises(BullerFel):
            ljudeffekt_db([Trafikflode(9, 1.0, 50.0)], 10.0, 3600.0)

    def test_kontrollutskrift_ar_den_som_ligger_i_docs(self) -> None:
        text = kontrollutskrift_md(las_emissionsdata(DATAFIL))
        self.assertIn("| 25 | 69.9 | 33 | 0.8 | 0.2 | 0 | 89.8 | 2 | 0.2 | 0.8 | 0 | ☐ |", text)
        self.assertIn("| 10000 | 2 | 10 | -10.2 | ☐ |", text)
        self.assertIn("Inte infört i koden", text)
        dokument = Path(__file__).resolve().parents[3] / "docs/buller/kontroll/emission_tabeller.md"
        self.assertEqual(text, dokument.read_text(encoding="utf-8"))


class TestInlasning(EmissionsTest):
    def test_fel_antal_band_ger_fel(self) -> None:
        with self.assertRaises(BullerFel) as ctx:
            self._modell(syntetisk_toml(antal_a=26))
        self.assertIn("27", str(ctx.exception))

    def test_fordelning_som_inte_summerar_till_ett_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            self._modell(syntetisk_toml(fordelning_rull="[0.8, 0.3, 0.0]"))

    def test_okand_formtyp_ger_fel(self) -> None:
        text = syntetisk_toml().replace('"a_plus_b_lg_v_genom_vref"', '"gissad_form"')
        with self.assertRaises(BullerFel) as ctx:
            self._modell(text)
        self.assertIn("gissad_form", str(ctx.exception))

    def test_trasig_fil_och_saknad_fil_ger_fel(self) -> None:
        with self.assertRaises(BullerFel):
            self._modell("[meta\nnamn = ")
        with self.assertRaises(BullerFel):
            las_emissionsdata(self.mapp / "finns_inte.toml")

    def test_fordelning_per_band_godtas(self) -> None:
        per_band = "[" + ", ".join([_lista([0.5] * ANTAL_BAND)] * 2 + [_lista([0.0] * 27)]) + "]"
        modell = self._modell(syntetisk_toml(fordelning_rull=per_band))
        self.assertTrue(modell.data.kategorier[1].tabeller["rull"].fordelning.shape == (3, 27))

    def test_kontrollutskrift_har_en_rad_per_band(self) -> None:
        text = kontrollutskrift_md(self._modell().data)
        self.assertIn("| 25 | 90 | 30 | 0.8 | 0.2 | 0 | 85 | 8 | 0 | 0.5 | 0.5 | ☐ |", text)
        self.assertIn("| 10000 | 103 |", text)


class TestLjudeffekt(EmissionsTest):
    def test_form_och_andliga_varden(self) -> None:
        modell = self._modell()
        niva = modell.ljudeffekt_db([Trafikflode(1, 10000.0, 80.0)], 25.0, 86400.0)
        self.assertEqual(niva.shape, (3, ANTAL_BAND))
        self.assertTrue(bool(np.all(np.isfinite(niva))))

    def test_dubblad_trafik_ger_tre_decibel(self) -> None:
        modell = self._modell()
        en = modell.ljudeffekt_db([Trafikflode(1, 5000.0, 80.0)], 25.0, 86400.0)
        tva = modell.ljudeffekt_db([Trafikflode(1, 10000.0, 80.0)], 25.0, 86400.0)
        np.testing.assert_allclose(tva - en, 10.0 * math.log10(2.0), atol=1e-9)
        self.assertAlmostEqual(float((tva - en)[0, 0]), 3.01, places=2)

    def test_monoton_i_trafikmangd(self) -> None:
        modell = self._modell()
        nivaer = [
            modell.ljudeffekt_db([Trafikflode(1, n, 60.0)], 10.0, 3600.0)[1, 10]
            for n in (1.0, 10.0, 100.0, 1000.0)
        ]
        self.assertTrue(all(b > a for a, b in zip(nivaer, nivaer[1:], strict=False)))

    def test_dubblad_segmentlangd_och_halverad_period_ger_tre_decibel(self) -> None:
        modell = self._modell()
        trafik = [Trafikflode(1, 1000.0, 50.0)]
        bas = modell.ljudeffekt_db(trafik, 10.0, 3600.0)
        np.testing.assert_allclose(
            modell.ljudeffekt_db(trafik, 20.0, 3600.0) - bas, 10.0 * math.log10(2.0)
        )
        np.testing.assert_allclose(
            modell.ljudeffekt_db(trafik, 10.0, 1800.0) - bas, 10.0 * math.log10(2.0)
        )

    def test_segmentniva_mot_handrakning(self) -> None:
        # Vid referenshastigheten är nivån per fordon a_db. Delkälla 0 får 80 % av rull-
        # effekten och ingen drivlineeffekt: L = a + 10·lg(0,8) + 10·lg(N·Δl / (v·T)).
        modell = self._modell()
        niva = modell.ljudeffekt_db([Trafikflode(1, 3600.0, 70.0)], 70.0 / 3.6, 3600.0)
        # N·Δl / (v·T) = 3600 · (70/3,6) / ((70/3,6) · 3600) = 1
        self.assertAlmostEqual(float(niva[0, 0]), 90.0 + 10.0 * math.log10(0.8), places=9)
        per_fordon = modell.ljudeffekt_per_fordon_db(1, 70.0)
        total = 10.0 * np.log10(np.sum(10.0 ** (per_fordon[:, 4] / 10.0)))
        vantat = 10.0 * math.log10(10.0 ** (92.0 / 10.0) + 10.0 ** (84.0 / 10.0))
        self.assertAlmostEqual(float(total), vantat, places=9)

    def test_tva_kategoriposter_summeras_energimassigt(self) -> None:
        modell = self._modell()
        en = modell.ljudeffekt_db([Trafikflode(1, 100.0, 80.0)], 10.0, 3600.0)
        tva = modell.ljudeffekt_db(
            [Trafikflode(1, 100.0, 80.0), Trafikflode(1, 100.0, 80.0)], 10.0, 3600.0
        )
        np.testing.assert_allclose(tva - en, 10.0 * math.log10(2.0))

    def test_delkallhojder_styr_radordning(self) -> None:
        modell = self._modell()
        trafik = [Trafikflode(1, 100.0, 80.0)]
        rak = modell.ljudeffekt_db(trafik, 10.0, 3600.0)
        omvand = modell.ljudeffekt_db(trafik, 10.0, 3600.0, [0.75, 0.30, 0.01])
        np.testing.assert_array_equal(omvand, rak[::-1, :])
        with self.assertRaises(BullerFel):
            modell.ljudeffekt_db(trafik, 10.0, 3600.0, [0.01, 0.30])
        with self.assertRaises(BullerFel):
            modell.ljudeffekt_db(trafik, 10.0, 3600.0, [0.01, 0.30, 0.5])

    def test_ogiltig_indata_ger_fel(self) -> None:
        modell = self._modell()
        trafik = [Trafikflode(1, 100.0, 80.0)]
        for langd, period in ((0.0, 3600.0), (10.0, 0.0), (float("nan"), 3600.0)):
            with self.assertRaises(BullerFel):
                modell.ljudeffekt_db(trafik, langd, period)
        with self.assertRaises(BullerFel):
            modell.ljudeffekt_db([], 10.0, 3600.0)
        with self.assertRaises(BullerFel):
            Trafikflode(1, -1.0, 80.0)
        with self.assertRaises(BullerFel):
            Trafikflode(1, 1.0, 0.0)
        with self.assertRaises(BullerFel) as ctx:
            modell.ljudeffekt_db([Trafikflode(2, 1.0, 80.0)], 10.0, 3600.0)
        self.assertIn("kategorier.2.rull.formtyp", str(ctx.exception))
        with self.assertRaises(BullerFel):
            modell.ljudeffekt_db([Trafikflode(9, 1.0, 80.0)], 10.0, 3600.0)


if __name__ == "__main__":
    unittest.main()
