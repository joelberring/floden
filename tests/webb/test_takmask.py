"""Takmasken: dagsljus maskar byggnadens fotavtryck (en port är täckt), vatten bara byggnad i
markslaget (en port är öppen och vattnet rinner där)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from markmodell.byggnadsregister import ByggnadsKandidat
from markmodell.hinder_oppningar import Oppning
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta
from webb.resultatlager import (
    TAKKALLA_FOTAVTRYCK,
    TAKKALLA_MARKSLAG,
    LagerFel,
    Mosaik,
    Utbredning,
    tak_mask,
    takkalla_for_roll,
)

E0, N0, STORLEK = 151000.0, 6580000.0, 40.0


def _hus_med_port(ut: Path) -> Path:
    """40 x 40 m, plan mark, ett hus 10–30 m med en 4 m bred port genom södra delen vid x = 20."""
    ruta = Ruta(E0, N0, STORLEK, 0.0, 1.0)
    hus = ByggnadsKandidat(
        polygon=(
            (E0 + 10, N0 + 10),
            (E0 + 30, N0 + 10),
            (E0 + 30, N0 + 30),
            (E0 + 10, N0 + 30),
        ),
        kalla="test",
        kalla_id="hus",
        tak_rh2000=25.0,
    )
    port = Oppning(
        "port",
        4.0,
        10.0,
        "test",
        ((E0 + 20, N0 + 9), (E0 + 20, N0 + 31)),
    )
    res = bygg_markmodell_ruta(
        ruta,
        ut,
        grund_z=np.full((40, 40), 10.0, dtype=np.float32),
        byggnadskandidater=[hus],
        oppningar=[port],
        byggnadshojd_fran_laser=False,
    )
    return Path(res.katalog)


def _mosaik() -> Mosaik:
    return Mosaik(
        raster=np.zeros((40, 40), dtype=np.float32),
        utbredning=Utbredning(E0, N0, E0 + STORLEK, N0 + STORLEK),
        pixelstorlek_m=1.0,
    )


class TestTakmaskPort(unittest.TestCase):
    def test_port_ar_tak_for_dagsljus_men_mark_for_vatten(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            mapp = _hus_med_port(Path(t) / "ruta")
            m = _mosaik()
            fot = tak_mask(m, [mapp], TAKKALLA_FOTAVTRYCK)
            slag = tak_mask(m, [mapp], TAKKALLA_MARKSLAG)
        # Rad 0 är norr. Cellen mitt i porten: x = 20 m, y = 15 m (södra delen av huset).
        rad, kol = 40 - 1 - 15, 20
        assert fot[rad, kol], "dagsljus: porten är täckt av taket och maskas som tak"
        assert not slag[rad, kol], "vatten: porten är öppen och visas som mark"
        # Fotavtrycket är hela huset; markslaget har samma hus minus porten.
        assert int(fot.sum()) == 20 * 20
        assert int(slag.sum()) < int(fot.sum())
        assert not (slag & ~fot).any()
        # Husets hörn är tak i båda, och marken utanför är det inte.
        assert fot[40 - 1 - 12, 12] and slag[40 - 1 - 12, 12]
        assert not fot[2, 2] and not slag[2, 2]

    def test_roll_ger_ratt_kalla(self) -> None:
        assert takkalla_for_roll("soltimmar") == TAKKALLA_FOTAVTRYCK
        assert takkalla_for_roll("svf") == TAKKALLA_FOTAVTRYCK
        assert takkalla_for_roll("max_djup") == TAKKALLA_MARKSLAG

    def test_okand_kalla_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            mapp = _hus_med_port(Path(t) / "ruta")
            with self.assertRaises(LagerFel):
                tak_mask(_mosaik(), [mapp], "annat")
