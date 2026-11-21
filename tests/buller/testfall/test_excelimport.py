"""Tester för importen av CSV-exporterade testfallsblad (konstruerade blad)."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import numpy as np

from buller.testfall import las_facit, las_fall, skriv_fall
from buller.testfall.excelimport import importera_rak_vag_blad, las_csv_blad
from buller.typer import TERSBAND_HZ, BullerFel

# Påhittade tal i samma uppställning som bladet i bilaga A (block sida vid sida).
_VANSTER = [
    "Nord2000 - Road Traffic - Straight Road - Case No. 99;;;",
    "Konstruerat blad, d=50 m, hr=4 m;;;",
    "Traffic parameters;;;",
    "Nvc;2500;veh. per T;",
    "v;60;km/h;",
    "T;24;hours;",
    "Propagation parameters;;;",
    "hr;4;m;",
    "t0;10;°C;",
    "z0;0,05;m;",
    "zu;10;m;",
    "u;0;m/s;",
    "φ;45;°;",
    "su;0,5;m/s;",
    "dt/dz;-0,01;°/m;",
    "sdt/dz;0;°/m;",
    "Cv2;0,12;m4/3/s2;",
    "Ct2;0,008;K/s2;",
    "Terrain profile;;;",
    "X;Z;Flow res.;Roughn.",
    "m;m;kNsm-4;m",
    "2,5;0;20000;0",
    "6;0,5;200;0,25",
    "50;1;0;0",
    "0;0;0;0",
    "0;0;0;0",
    ";;;",
    ";;;",
    ";;;",
    ";;;",
]
_HOGER_HUVUD = [
    ";;;;",
    ";;;;",
    ";Calculated sound levels;;;",
    ";LAeq,24h;41,25;dB;",
    ";LAE;56,63;dB;",
    ";LAmax;44,5;dB;",
    ";Calculated spectra;;;",
    ";Freq.;Leq,24h;LE;dL",
    ";Hz;dB;dB;dB",
]


def bladtext() -> str:
    hoger = list(_HOGER_HUVUD)
    for i, f in enumerate(TERSBAND_HZ):
        leq = 50.0 - i
        hoger.append(f";{f:g};{leq:.2f};{leq + 15.38:.2f};{-0.5 * i:.2f}".replace(".", ","))
    rader = max(len(hoger), len(_VANSTER))
    vanster = _VANSTER + [";;;"] * (rader - len(_VANSTER))
    hoger += [";;;;"] * (rader - len(hoger))
    return "\n".join(v + h for v, h in zip(vanster, hoger, strict=True)) + "\n"


class TestImport(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mapp = Path(self._tmp.name)
        self.argument = {
            "fall_id": "N999",
            "blad": "konstruerat.csv",
            "dokument": "påhittat blad",
            "metodversion": "test",
            "ar_facit": False,
            "ar_facit_skal": "konstruerat i test",
            "fordonskategori": 1,
        }

    def _celler(self, text: str | None = None) -> list[list[str]]:
        fil = self.mapp / "blad.csv"
        fil.write_text(text if text is not None else bladtext(), encoding="utf-8")
        return las_csv_blad(fil)

    def test_import_av_konstruerat_blad(self) -> None:
        fall, facit = importera_rak_vag_blad(self._celler(), **self.argument)  # type: ignore[arg-type]
        self.assertEqual(fall.namn, "Nord2000 - Road Traffic - Straight Road - Case No. 99")
        self.assertEqual(fall.beskrivning, "Konstruerat blad, d=50 m, hr=4 m")
        self.assertEqual(fall.status, "ej_facit")
        self.assertEqual(fall.trafik[0].antal_fordon, 2500.0)
        self.assertEqual(fall.trafik[0].hastighet_km_h, 60.0)
        self.assertEqual(dict(fall.perioder_h), {"T": 24.0})
        self.assertEqual(fall.mottagare_hojd_m, 4.0)
        assert fall.terrang is not None and fall.vader is not None and fall.vag is not None
        self.assertEqual(fall.terrang.x_m, (2.5, 6.0, 50.0))
        self.assertEqual(fall.terrang.z_m, (0.0, 0.5, 1.0))
        self.assertEqual(fall.terrang.flodesresistivitet_kNsm4, (20000.0, 200.0))
        self.assertEqual(fall.terrang.rahet_m, (0.0, 0.25))
        self.assertEqual((fall.vag.kalla_x_m, fall.mottagare_x_m), (2.5, 50.0))
        v = fall.vader
        self.assertEqual(
            (v.temperatur_c, v.z0_m, v.zu_m, v.vind_m_s, v.std_vind_m_s, v.dt_dz_k_m, v.cv2, v.ct2),
            (10.0, 0.05, 10.0, 0.0, 0.5, -0.01, 0.12, 0.008),
        )
        self.assertEqual(v.relativ_fuktighet_procent, 70.0)
        self.assertEqual(dict(fall.vader_tillagg), {"vindriktning_grader": 45.0})

        self.assertFalse(facit.ar_facit)
        serie = facit.serier[0]
        self.assertEqual(
            dict(serie.totalnivaer_db), {"LAeq_24h": 41.25, "LAE": 56.63, "LAmax": 44.5}
        )
        np.testing.assert_allclose(serie.spektrum_db["Leq_24h"], 50.0 - np.arange(27.0))
        np.testing.assert_allclose(serie.spektrum_db["LE"], 65.38 - np.arange(27.0))
        np.testing.assert_allclose(serie.spektrum_db["dL"], -0.5 * np.arange(27.0))

    def test_importerat_fall_gar_att_skriva_och_lasa(self) -> None:
        fall, facit = importera_rak_vag_blad(self._celler(), **self.argument)  # type: ignore[arg-type]
        skriv_fall(fall, self.mapp / "N999", facit)
        self.assertEqual(las_fall(self.mapp / "N999").terrang, fall.terrang)
        self.assertEqual(las_facit(self.mapp / "N999").serier[0].id, "blad")

    def test_komma_som_avgransare_med_decimalpunkt(self) -> None:
        # Decimalpunkt i talen; celler som själva innehåller kommatecken står inom citattecken.
        text = re.sub(r"(\d),(\d)", r"\1.\2", bladtext())
        text = text.replace("Konstruerat blad, d=50 m, hr=4 m", '"Konstruerat blad, d=50 m"')
        text = text.replace("LAeq,24h", '"LAeq,24h"').replace(";Leq,24h", ';"Leq,24h"')
        text = text.replace(";", ",")
        fall, facit = importera_rak_vag_blad(self._celler(text), **self.argument)  # type: ignore[arg-type]
        self.assertEqual(facit.serier[0].totalnivaer_db["LAeq_24h"], 41.25)
        assert fall.terrang is not None
        self.assertEqual(fall.terrang.x_m, (2.5, 6.0, 50.0))

    def test_ar_facit_satts_bara_av_anroparen(self) -> None:
        argument = {**self.argument, "ar_facit": True, "ar_facit_skal": "kontrollerad version"}
        fall, facit = importera_rak_vag_blad(self._celler(), **argument)  # type: ignore[arg-type]
        self.assertTrue(facit.ar_facit)
        self.assertEqual(fall.status, "redo")

    def test_fel_i_bladet_ger_bullerfel(self) -> None:
        bra = bladtext()
        fel = {
            "saknad parameter": bra.replace("zu;10;m;", "xx;10;m;"),
            "parameter utan tal": bra.replace("hr;4;m;", "hr;;m;"),
            "saknad terrängrubrik": bra.replace("X;Z;Flow res.;Roughn.", "X;Y;Flow res.;Roughn."),
            "en terrängpunkt": bra.replace("6;0,5;200;0,25", "0;0;0;0"),
            "saknad spektrumrubrik": bra.replace("Freq.", "Band"),
            "okänd spektrumkolumn": bra.replace("Freq.;Leq,24h;LE;dL", "Freq.;Leq,24h;LX;dL"),
            "fel frekvens": bra.replace(";31,5;", ";32;"),
            "för få band": "\n".join(r for r in bra.splitlines() if ";10000;" not in r),
            "tomt värde": bra.replace(";1000;34,00;", ";1000;;"),
        }
        importera_rak_vag_blad(self._celler(bra), **self.argument)  # type: ignore[arg-type]
        for namn, text in fel.items():
            with self.subTest(namn):
                self.assertNotEqual(text, bra, "ersättningen träffade inte")
                with self.assertRaises(BullerFel):
                    importera_rak_vag_blad(self._celler(text), **self.argument)  # type: ignore[arg-type]

    def test_tom_eller_saknad_fil(self) -> None:
        with self.assertRaises(BullerFel):
            self._celler("  \n")
        with self.assertRaises(BullerFel):
            las_csv_blad(self.mapp / "finns_inte.csv")


if __name__ == "__main__":
    unittest.main()
