"""Enhetstester för `markmodell.hinder_oppningar` (bl.a. A7 vägbank/trumma och A8 port)."""

from __future__ import annotations

import unittest
from collections import deque

import numpy as np

from markmodell.hinder_oppningar import (
    BYGGNAD,
    HARDGJORT,
    Byggnad,
    HinderOppningFel,
    Oppning,
    brann_in_byggnader,
    brann_in_oppningar,
)
from markmodell.rutnat import Ruta


def _finns_lag_passage(
    mark_z: np.ndarray,
    start: tuple[int, int],
    mal: tuple[int, int],
    max_z: float,
) -> bool:
    """Kontrollerar med 4-grannar om det finns en sammanhängande passage med z <= max_z."""
    rader, kolumner = mark_z.shape
    if mark_z[start] > max_z or mark_z[mal] > max_z:
        return False
    besokt = {start}
    ko = deque([start])
    while ko:
        r, k = ko.popleft()
        if (r, k) == mal:
            return True
        for dr, dk in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nk = r + dr, k + dk
            if (
                0 <= nr < rader
                and 0 <= nk < kolumner
                and (nr, nk) not in besokt
                and mark_z[nr, nk] <= max_z
            ):
                besokt.add((nr, nk))
                ko.append((nr, nk))
    return False


class TestHinderOppningar(unittest.TestCase):
    def test_a7_vagbank_med_och_utan_trumma(self) -> None:
        # Liten ruta 20 x 20 m (+2 m överlapp), dalgång i väst-östlig riktning med vägbank i mitten
        ruta = Ruta(e_min_m=150000.0, n_min_m=6576000.0, storlek_m=20.0, overlapp_m=2.0)
        e_vec, _ = ruta.cell_centra_en()
        rader, kolumner = ruta.form_rc

        # Grundhöjd 10.0 m, vägbank (2.5 m hög, dvs 12.5 m) korsar dalgången vid E = 150010 m
        mark_z = np.full((rader, kolumner), 10.0, dtype=np.float32)
        bank_mask = (e_vec >= 150008.0) & (e_vec <= 150012.0)
        mark_z[:, bank_mask] = 12.5
        markslag = np.full((rader, kolumner), HARDGJORT, dtype=np.uint8)

        start = ruta.koordinat_till_cell(150004.0, 6576010.0)
        mal = ruta.koordinat_till_cell(150016.0, 6576010.0)

        # Utan trumma är dalgången blockerad vid vattenytan 10.5 m
        self.assertFalse(_finns_lag_passage(mark_z, start, mal, max_z=10.5))

        # Med trumma (bredd 1.0 m, botten 10.0 m) skapas en kontinuerlig låg passage genom banken
        trumma = Oppning(
            typ="trumma",
            bredd_m=1.0,
            botten_rh2000=10.0,
            kalla="prov1_oppningar.gpkg",
            linje=((150006.0, 6576010.0), (150014.0, 6576010.0)),
        )
        z_med_trumma, slag_efter = brann_in_oppningar(mark_z, markslag, ruta, [trumma])
        self.assertTrue(_finns_lag_passage(z_med_trumma, start, mal, max_z=10.05))
        # Själva banken norr/söder om trumman är fortfarande 12.5 m
        nord_bank = ruta.koordinat_till_cell(150010.0, 6576016.0)
        self.assertAlmostEqual(float(z_med_trumma[nord_bank]), 12.5, places=4)
        self.assertEqual(int(slag_efter[nord_bank]), HARDGJORT)

    def test_a8_byggnad_och_port_genom_byggnad(self) -> None:
        ruta = Ruta(e_min_m=150000.0, n_min_m=6576000.0, storlek_m=20.0, overlapp_m=2.0)
        mark_z = np.full(ruta.form_rc, 10.0, dtype=np.float32)
        markslag = np.full(ruta.form_rc, HARDGJORT, dtype=np.uint8)

        # Byggnad som spärrar hela bredden från N=6576000 till N=6576020 mellan E=150008..150012
        byggnad = Byggnad(
            polygon=(
                (150008.0, 6575998.0),
                (150012.0, 6575998.0),
                (150012.0, 6576022.0),
                (150008.0, 6576022.0),
            ),
            hinderhojd_m=10.0,
        )
        z_bygg, slag_bygg = brann_in_byggnader(mark_z, markslag, ruta, [byggnad])

        bygg_cell = ruta.koordinat_till_cell(150010.0, 6576015.0)
        self.assertEqual(int(slag_bygg[bygg_cell]), BYGGNAD)
        self.assertAlmostEqual(float(z_bygg[bygg_cell]), 20.0, places=4)

        start = ruta.koordinat_till_cell(150004.0, 6576010.0)
        mal = ruta.koordinat_till_cell(150016.0, 6576010.0)
        self.assertFalse(_finns_lag_passage(z_bygg, start, mal, max_z=10.5))

        # Öppna en port (bredd 2.0 m, botten 10.0 m) mitt genom byggnaden
        port = Oppning(
            typ="port",
            bredd_m=2.0,
            botten_rh2000=10.0,
            kalla="baskarta",
            linje=((150007.0, 6576010.0), (150013.0, 6576010.0)),
        )
        z_port, slag_port = brann_in_oppningar(z_bygg, slag_bygg, ruta, [port])

        self.assertTrue(_finns_lag_passage(z_port, start, mal, max_z=10.05))
        port_cell = ruta.koordinat_till_cell(150010.0, 6576010.0)
        self.assertEqual(int(slag_port[port_cell]), HARDGJORT)
        self.assertAlmostEqual(float(z_port[port_cell]), 10.0, places=4)
        # Övriga byggnadsceller förblir BYGGNAD (9)
        self.assertEqual(int(slag_port[bygg_cell]), BYGGNAD)

    def test_validering_av_byggnad_och_oppning(self) -> None:
        with self.assertRaises(HinderOppningFel):
            Byggnad(polygon=((0.0, 0.0), (1.0, 1.0)), hinderhojd_m=10.0)
        with self.assertRaises(HinderOppningFel):
            Oppning(
                typ="okand",
                bredd_m=1.0,
                botten_rh2000=10.0,
                kalla="manuell",
                linje=((0.0, 0.0), (1.0, 0.0)),
            )


if __name__ == "__main__":
    unittest.main()
