"""Enhetstester för `markmodell.brytlinjer`."""

from __future__ import annotations

import unittest
from collections import deque

import numpy as np

from markmodell.brytlinjer import (
    STANDARDHOJDER_M,
    Brytlinje,
    BrytlinjeFel,
    brann_in_brytlinjer,
    fast_losa_andar,
)
from markmodell.rutnat import Ruta


def _ar_nåbar_med_4_grannar(
    mark_z: np.ndarray,
    start: tuple[int, int],
    mal: tuple[int, int],
    max_z: float,
) -> bool:
    """Kontrollerar om vatten med nivå <= max_z kan rinna via 4-grannar från start till mål."""
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


class TestBrytlinjer(unittest.TestCase):
    def test_snapping_sluter_losa_andar_inom_0_25m_och_lamnar_langre_ororda(self) -> None:
        l1 = Brytlinje(
            typ="kantsten",
            hojd_m=STANDARDHOJDER_M["kantsten"],
            punkter=((150000.0, 6576000.0), (150005.0, 6576000.0)),
        )
        # Lös ände 0,20 m från slutet av l1 -> ska snappas ihop
        l2 = Brytlinje(
            typ="kantsten",
            hojd_m=0.12,
            punkter=((150005.16, 6576000.12), (150010.0, 6576000.0)),
        )
        # Lös ände 0,60 m från slutet av l2 -> ska INTE snappas ihop
        l3 = Brytlinje(
            typ="mur",
            hojd_m=1.0,
            punkter=((150010.60, 6576000.0), (150015.0, 6576000.0)),
        )

        snappade, stat = fast_losa_andar([l1, l2, l3], tolerans_m=0.25)
        self.assertEqual(stat.antal_linjer, 3)
        self.assertEqual(stat.antal_losa_andar_fore, 6)
        self.assertEqual(stat.antal_slutna_andar, 2)
        self.assertEqual(stat.antal_losa_andar_efter, 4)

        # l1 slut och l2 start delar nu exakt koordinat
        self.assertEqual(snappade[0].punkter[-1], snappade[1].punkter[0])
        self.assertAlmostEqual(snappade[0].punkter[-1][0], 150005.08, places=6)
        self.assertAlmostEqual(snappade[0].punkter[-1][1], 6576000.06, places=6)

        # l2 slut och l3 start är orörda
        self.assertEqual(snappade[1].punkter[-1], (150010.0, 6576000.0))
        self.assertEqual(snappade[2].punkter[0], (150010.60, 6576000.0))

    def test_diagonal_kantsten_har_ingen_diagonal_lacka(self) -> None:
        ruta = Ruta(e_min_m=150000.0, n_min_m=6576000.0, storlek_m=8.0, overlapp_m=1.0)
        mark_z = np.full(ruta.form_rc, 10.0, dtype=np.float32)

        # Diagonal kantsten från sydväst till nordost över hela yttre rastret
        e0, n0, e1, n1 = ruta.yttre_bbox
        kantsten = Brytlinje(
            typ="kantsten",
            hojd_m=0.12,
            punkter=((e0 + 0.05, n0 + 0.05), (e1 - 0.05, n1 - 0.05)),
        )
        med_kant = brann_in_brytlinjer(mark_z, ruta, [kantsten])

        # Testa både 4-granne och 8-granne: med 4-sammanhängande supercover kan vatten
        # med yta 10.05 m (under kantstenen på 10.12 m) varken rinna rakt eller diagonalt igenom.
        rader, kolumner = ruta.form_rc
        start_nordvast = (1, 1)
        mal_sydost = (rader - 2, kolumner - 2)
        self.assertFalse(_ar_nåbar_med_4_grannar(med_kant, start_nordvast, mal_sydost, 10.05))

        # Kontrollera även med 8-grannar: ingen läcka ens med diagonala steg
        besokt = {start_nordvast}
        ko = deque([start_nordvast])
        nådde_andra_sidan = False
        while ko:
            r, k = ko.popleft()
            if (r, k) == mal_sydost:
                nådde_andra_sidan = True
                break
            for dr in (-1, 0, 1):
                for dk in (-1, 0, 1):
                    if dr == 0 and dk == 0:
                        continue
                    nr, nk = r + dr, k + dk
                    if (
                        0 <= nr < rader
                        and 0 <= nk < kolumner
                        and (nr, nk) not in besokt
                        and med_kant[nr, nk] <= 10.05
                    ):
                        besokt.add((nr, nk))
                        ko.append((nr, nk))
        self.assertFalse(nådde_andra_sidan)

        # Höjs vattnet över kantstenen (10.15 m) passerar det
        self.assertTrue(_ar_nåbar_med_4_grannar(med_kant, start_nordvast, mal_sydost, 10.15))

    def test_svenskt_alias_stodmur_och_ogiltig_typ(self) -> None:
        b = Brytlinje(typ="stödmur", hojd_m=1.5, punkter=((0.0, 0.0), (2.0, 0.0)))
        self.assertEqual(b.typ, "stodmur")
        with self.assertRaises(BrytlinjeFel):
            Brytlinje(typ="okand_typ", hojd_m=0.5, punkter=((0.0, 0.0), (1.0, 0.0)))


if __name__ == "__main__":
    unittest.main()
