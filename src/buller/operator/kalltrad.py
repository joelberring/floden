"""Kd-träd över källornas lägen i plan, för klustring med felgräns."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from buller.typer import BullerFel, Flyttal

Heltal = NDArray[np.int64]


@dataclass(frozen=True, eq=False)
class Kalltrad:
    """Binärt kd-träd. Nod 0 är roten. Nod `j` omfattar `ordning[nod_start[j]:nod_slut[j]]`.

    Barnens intervall delar förälderns intervall i två sammanhängande delar, så varje nod är
    ett intervall i `ordning`. Löv har `nod_vanster == -1`.
    """

    ordning: Heltal  # form (n_kallor,), permutation av källindex
    nod_start: Heltal
    nod_slut: Heltal
    nod_vanster: Heltal
    nod_hoger: Heltal
    nod_e_min_m: Flyttal
    nod_e_max_m: Flyttal
    nod_n_min_m: Flyttal
    nod_n_max_m: Flyttal

    @property
    def antal_noder(self) -> int:
        return int(self.nod_start.shape[0])

    def minsta_avstand_m(self, nod: int, e_m: float, n_m: float) -> float:
        """Minsta avstånd i plan från punkten till nodens omslutande rektangel (0 om inuti)."""
        de = max(self.nod_e_min_m[nod] - e_m, 0.0, e_m - self.nod_e_max_m[nod])
        dn = max(self.nod_n_min_m[nod] - n_m, 0.0, n_m - self.nod_n_max_m[nod])
        return float(np.hypot(de, dn))


def bygg_kalltrad(e_m: Flyttal, n_m: Flyttal, lovstorlek: int) -> Kalltrad:
    """Bygger trädet deterministiskt: delning vid medianen längs den längsta sidan.

    Lika koordinater ordnas efter källindex, så samma indata ger alltid samma träd.
    """
    if lovstorlek < 1:
        raise BullerFel("lovstorlek måste vara minst 1.")
    e = np.asarray(e_m, dtype=np.float64)
    n = np.asarray(n_m, dtype=np.float64)
    if e.ndim != 1 or e.shape != n.shape:
        raise BullerFel("Källornas koordinater ska vara endimensionella och lika långa.")
    antal = int(e.shape[0])
    ordning = np.arange(antal, dtype=np.int64)
    start: list[int] = []
    slut: list[int] = []
    vanster: list[int] = []
    hoger: list[int] = []
    grans: list[tuple[float, float, float, float]] = []

    def ny_nod(a: int, b: int) -> int:
        urval = ordning[a:b]
        start.append(a)
        slut.append(b)
        vanster.append(-1)
        hoger.append(-1)
        grans.append(
            (
                float(e[urval].min()),
                float(e[urval].max()),
                float(n[urval].min()),
                float(n[urval].max()),
            )
        )
        return len(start) - 1

    if antal:
        stack = [ny_nod(0, antal)]
        while stack:
            nod = stack.pop()
            a, b = start[nod], slut[nod]
            if b - a <= lovstorlek:
                continue
            e_min, e_max, n_min, n_max = grans[nod]
            urval = ordning[a:b]
            koordinat = e[urval] if (e_max - e_min) >= (n_max - n_min) else n[urval]
            ordning[a:b] = urval[np.lexsort((urval, koordinat))]
            mitt = a + (b - a) // 2
            vanster[nod] = ny_nod(a, mitt)
            hoger[nod] = ny_nod(mitt, b)
            stack.extend((hoger[nod], vanster[nod]))

    granser = np.asarray(grans, dtype=np.float64).reshape(-1, 4)
    return Kalltrad(
        ordning=ordning,
        nod_start=np.asarray(start, dtype=np.int64),
        nod_slut=np.asarray(slut, dtype=np.int64),
        nod_vanster=np.asarray(vanster, dtype=np.int64),
        nod_hoger=np.asarray(hoger, dtype=np.int64),
        nod_e_min_m=np.ascontiguousarray(granser[:, 0]),
        nod_e_max_m=np.ascontiguousarray(granser[:, 1]),
        nod_n_min_m=np.ascontiguousarray(granser[:, 2]),
        nod_n_max_m=np.ascontiguousarray(granser[:, 3]),
    )
