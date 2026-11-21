"""K2-läsaren: skriv → läs ger samma graf och samma fyllning på A1–A9."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from sankgraf.fyllning import statisk_fyllning
from sankgraf.k2_las import las_sankgraf_k2
from sankgraf.typer import SankGraf, SankGrafFel, skriv_sankgraf_k2
from vatten.blaytor import bygg_graf, k1_filer, las_k1

ROT = Path(__file__).resolve().parents[2] / "testsvit"
FALL = sorted(p for p in ROT.glob("A[1-9]_*") if (p / "indata" / "mark.tif").is_file())


def _lika(a: SankGraf, b: SankGraf) -> None:
    assert a.noder == b.noder
    np.testing.assert_array_equal(a.cell_till_nod, b.cell_till_nod)
    np.testing.assert_array_equal(a.mark_z, b.mark_z)
    np.testing.assert_array_equal(a.markslag, b.markslag)
    assert a.flodesvagar == b.flodesvagar
    assert a.ut_lov_id == b.ut_lov_id
    assert a.overfall_celler == b.overfall_celler
    assert (a.filtrerad, a.persistens_faktor) == (b.filtrerad, b.persistens_faktor)
    for f in ("bredd", "hojd", "x_min", "y_max", "pixelstorlek_x", "pixelstorlek_y", "epsg"):
        assert getattr(a.geo, f) == getattr(b.geo, f), f


class TestK2Las(unittest.TestCase):
    def test_finns_fall(self) -> None:
        self.assertGreaterEqual(len(FALL), 9)

    def test_rundresa_och_samma_fyllning(self) -> None:
        for fall in FALL:
            for faktor in (None, 2.0):
                with self.subTest(fall=fall.name, persistens=faktor):
                    graf = bygg_graf(las_k1(k1_filer(fall / "indata")), faktor)
                    with tempfile.TemporaryDirectory() as tmp:
                        skriv_sankgraf_k2(graf, tmp, kodversion="test")
                        last = las_sankgraf_k2(tmp)
                    _lika(graf, last)
                    for regn_m in (0.01, 0.08, 0.3):
                        r1 = statisk_fyllning(graf, regn_m)
                        r2 = statisk_fyllning(last, regn_m)
                        np.testing.assert_array_equal(r1.djup_m, r2.djup_m)

    def test_saknad_tillaggsfil_ger_fel(self) -> None:
        graf = bygg_graf(las_k1(k1_filer(FALL[0] / "indata")))
        with tempfile.TemporaryDirectory() as tmp:
            skriv_sankgraf_k2(graf, tmp, kodversion="test")
            (Path(tmp) / "sankgraf_topologi.json").unlink()
            with self.assertRaises(SankGrafFel):
                las_sankgraf_k2(tmp)


if __name__ == "__main__":
    unittest.main()
