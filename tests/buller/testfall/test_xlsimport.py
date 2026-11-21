"""Tester för importen av RL 18-18 (`buller.testfall.xlsimport`).

Originalfilerna ligger utanför repot. Finns de (`FLODEN_BULLER_KALLMAPP` eller
`[buller] kallmapp` i `config.local.toml`) prövas att en ny import ger exakt de fall som är
incheckade; annars hoppas det testet över.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from buller.testfall import las_facit, las_fall
from buller.testfall.format import FACITFIL, FALLFIL
from buller.testfall.xlsimport import (
    ARBETSBOCKER,
    Arbetsbok,
    importera_rl18,
    importera_stadsgata,
    kallmapp_ur_konfig,
    las_arbetsbok,
    main,
)
from buller.typer import TERSBAND_HZ, BullerFel

REPO = Path(__file__).resolve().parents[3]
N_ROT = REPO / "testsvit" / "N_nord2000"


def _kallmapp() -> Path | None:
    miljo = os.environ.get("FLODEN_BULLER_KALLMAPP")
    mapp = Path(miljo) if miljo else kallmapp_ur_konfig(REPO)
    if mapp is None or not all((mapp / fil).is_file() for fil in ARBETSBOCKER.values()):
        return None
    return mapp


class TestKallmapp(unittest.TestCase):
    def test_kallmapp_lases_ur_lokal_konfiguration_om_den_finns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            self.assertIsNone(kallmapp_ur_konfig(rot))  # ingen konfiguration alls
            (rot / "config.toml").write_text("[sokvagar]\ncache = \"cache\"\n", encoding="utf-8")
            self.assertIsNone(kallmapp_ur_konfig(rot))  # inställningen är valfri
            (rot / "config.local.toml").write_text(
                "[buller]\nkallmapp = \"/data/kallor/buller\"\n", encoding="utf-8"
            )
            self.assertEqual(kallmapp_ur_konfig(rot), Path("/data/kallor/buller"))

    def test_saknad_fil_ger_bullerfel_och_kommandoraden_felkod(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BullerFel):
                las_arbetsbok(Path(tmp) / "saknas.xls")
            with self.assertRaises(BullerFel):
                importera_rl18(tmp, None)
            self.assertEqual(main(["--kallmapp", tmp, "--torrkor"]), 1)


class TestTolkningAvBlad(unittest.TestCase):
    def test_stadsgata_pa_konstruerat_blad(self) -> None:
        celler = [
            ["Nord2000 - Road Traffic - City Street"] + [""] * 6,
            ["hR (m)", "1.5", "4.0", "8.0", "1.5", "4.0", "8.0"],
            ["sE", "1.0", "1.0", "1.0", "0.7", "0.7", "0.7"],
            ["LAeq,24h", "61.0", "62.0", "63.0", "64.0", "65.0", "66.0"],
            ["Calculated spectra, Leq,24h"] + [""] * 6,
            ["Freq. (Hz)"] + ["(dB)"] * 6,
        ]
        celler += [[repr(f)] + [repr(50.0 + k + i) for k in range(6)]
                   for i, f in enumerate(TERSBAND_HZ)]
        bok = Arbetsbok(Path("konstruerad.xls"), {"2": celler, "Coordinates": []}, {})
        ((mapp, fall, facit),) = importera_stadsgata(bok)
        self.assertEqual((mapp, fall.id), ("N18S2_stadsgata", "N18S2"))
        self.assertEqual([s.id for s in facit.serier][3], "h1_5_rho0_7")
        self.assertEqual(facit.serier[5].totalnivaer_db, {"LAeq_24h": 66.0})
        self.assertEqual(dict(facit.serier[4].villkor),
                         {"mottagare_hojd_m": 4.0, "energireflektionskoefficient": 0.7})
        np.testing.assert_array_equal(facit.serier[2].spektrum_db["Leq_24h"], 52.0 + np.arange(27))
        celler[10][0] = "999.0"
        with self.assertRaises(BullerFel):
            importera_stadsgata(bok)


@unittest.skipIf(_kallmapp() is None, "originalfilerna (RL 18-18) finns inte lokalt")
class TestOmimport(unittest.TestCase):
    def test_ny_import_ger_de_incheckade_fallen(self) -> None:
        kallmapp = _kallmapp()
        assert kallmapp is not None
        with tempfile.TemporaryDirectory() as tmp:
            rapport = importera_rl18(kallmapp, tmp)
            self.assertIn("rak_vag: 62 fall", rapport[0])
            nya = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(nya, sorted(p.name for p in N_ROT.iterdir() if p.name[:3] == "N18"))
            for namn in nya:
                for fil in (FALLFIL, FACITFIL):
                    self.assertEqual(
                        (Path(tmp) / namn / fil).read_bytes(), (N_ROT / namn / fil).read_bytes()
                    )

    def test_gulmarkering_styr_regelvariant(self) -> None:
        # Bladet ToBeTested: fall 2 har u = 3 m/s omarkerad (motvind) och u = −3 m/s gul.
        serier = las_facit(N_ROT / "N18K2_kurvig_vag").serier
        villkor = {s.id: s.villkor["regelvariant"] for s in serier}
        self.assertEqual(
            villkor,
            {"u0": "kurvig_vag_gul", "u3": "kurvig_vag_motvind", "um3": "kurvig_vag_gul"},
        )
        self.assertEqual(las_fall(N_ROT / "N18K7_kurvig_vag").mottagare_hojd_m, 3.5)


if __name__ == "__main__":
    unittest.main()
