"""Korridorindex: vilka banor berörs av en ändring inom en ruta i plan?

Varje bana har en korridor: alla punkter närmare banans plangeometri än halvbredden.
Indexet är ett enhetligt rutnät över korridorerna. Det får ge falska träffar men missar
aldrig en bana vars korridor korsar frågerutan.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from buller.scen.bullerscen import Boolar, Heltal
from buller.typer import Bana, BullerFel, Flyttal

STANDARD_CELL_M = 25.0
# Marginal mot avrundningsfel; gör indexet en aning generösare, aldrig snålare.
_MARGINAL_M = 1e-6
_BEN_PER_DEL = 20_000


class Korridorindex:
    """Rumsligt index över banornas plangeometri."""

    def __init__(
        self,
        banor: Sequence[Bana],
        halvbredd_m: float,
        *,
        cell_m: float = STANDARD_CELL_M,
        forfina: bool = True,
    ) -> None:
        """Bygger indexet.

        Med `forfina` prövas varje kandidat från rutnätet dessutom med ett avståndstest
        mellan ben och frågeruta, med en marginal på den säkra sidan.
        """
        if not (math.isfinite(halvbredd_m) and halvbredd_m >= 0.0):
            raise BullerFel(f"Korridorens halvbredd får inte vara negativ, fick {halvbredd_m}.")
        if not (math.isfinite(cell_m) and cell_m > 0.0):
            raise BullerFel(f"Indexets cellstorlek ska vara > 0 m, fick {cell_m}.")
        self.halvbredd_m = float(halvbredd_m)
        self.cell_m = float(cell_m)
        self.forfina = forfina
        self.antal_banor = len(banor)
        self._mottagare_id = np.array([b.mottagare_id for b in banor], dtype=np.int64)
        self._kalla_id = np.array([b.kalla_id for b in banor], dtype=np.int64)

        e0: list[Flyttal] = []
        n0: list[Flyttal] = []
        e1: list[Flyttal] = []
        n1: list[Flyttal] = []
        agare: list[Heltal] = []
        for i, bana in enumerate(banor):
            if bana.plan_e_m.shape[0] < 2 or bana.plan_e_m.shape != bana.plan_n_m.shape:
                raise BullerFel(f"Bana {i} saknar plangeometri med minst två punkter.")
            e0.append(bana.plan_e_m[:-1])
            n0.append(bana.plan_n_m[:-1])
            e1.append(bana.plan_e_m[1:])
            n1.append(bana.plan_n_m[1:])
            agare.append(np.full(bana.plan_e_m.shape[0] - 1, i, dtype=np.int64))
        self._e0 = np.concatenate(e0) if e0 else np.zeros(0)
        self._n0 = np.concatenate(n0) if n0 else np.zeros(0)
        self._e1 = np.concatenate(e1) if e1 else np.zeros(0)
        self._n1 = np.concatenate(n1) if n1 else np.zeros(0)
        self._ben_bana = np.concatenate(agare) if agare else np.zeros(0, dtype=np.int64)
        if not bool(np.all(np.isfinite(self._e0)) and np.all(np.isfinite(self._e1))) or not bool(
            np.all(np.isfinite(self._n0)) and np.all(np.isfinite(self._n1))
        ):
            raise BullerFel("Banornas plangeometri innehåller icke-ändliga koordinater.")

        # Varje punkt på ett ben ligger inom cell/4 från ett sampel (sampelavstånd ≤ cell/2).
        # Korridoren ryms därför i kvadrater med halvsidan `rackvidd` kring samplen.
        self._rackvidd_m = self.halvbredd_m + self.cell_m / 4.0 + _MARGINAL_M
        antal_ben = int(self._e0.shape[0])
        if antal_ben == 0:
            self._e_bas = 0.0
            self._n_bas = 0.0
            self._nx = 0
            self._ny = 0
            self._nyckel = np.zeros(0, dtype=np.int64)
            self._ben = np.zeros(0, dtype=np.int64)
            return
        self._e_bas = min(float(self._e0.min()), float(self._e1.min())) - self._rackvidd_m
        self._n_bas = min(float(self._n0.min()), float(self._n1.min())) - self._rackvidd_m
        e_topp = max(float(self._e0.max()), float(self._e1.max())) + self._rackvidd_m
        n_topp = max(float(self._n0.max()), float(self._n1.max())) + self._rackvidd_m
        self._nx = int(math.floor((e_topp - self._e_bas) / self.cell_m)) + 1
        self._ny = int(math.floor((n_topp - self._n_bas) / self.cell_m)) + 1

        nycklar: list[Heltal] = []
        for start in range(0, antal_ben, _BEN_PER_DEL):
            nycklar.append(self._celler_for_ben(start, min(antal_ben, start + _BEN_PER_DEL)))
        # Sammansatt nyckel (cell, ben), unik och sorterad på cell.
        sammansatt = np.unique(np.concatenate(nycklar))
        self._nyckel = sammansatt // antal_ben
        self._ben = sammansatt % antal_ben

    def _celler_for_ben(self, start: int, slut: int) -> Heltal:
        """Sammansatta nycklar cell · antal_ben + ben för benen `start:slut`."""
        antal_ben = int(self._e0.shape[0])
        e0, n0 = self._e0[start:slut], self._n0[start:slut]
        e1, n1 = self._e1[start:slut], self._n1[start:slut]
        antal_steg = np.maximum(
            1, np.ceil(np.hypot(e1 - e0, n1 - n0) / (self.cell_m / 2.0))
        ).astype(np.int64)
        antal_sampel = antal_steg + 1
        ben = np.repeat(np.arange(slut - start, dtype=np.int64), antal_sampel)
        forsta = np.cumsum(antal_sampel) - antal_sampel
        f = (np.arange(ben.shape[0], dtype=np.int64) - forsta[ben]) / antal_steg[ben]
        pe = e0[ben] + (e1[ben] - e0[ben]) * f
        pn = n0[ben] + (n1[ben] - n0[ben]) * f
        ix0 = np.floor((pe - self._rackvidd_m - self._e_bas) / self.cell_m).astype(np.int64)
        ix1 = np.floor((pe + self._rackvidd_m - self._e_bas) / self.cell_m).astype(np.int64)
        iy0 = np.floor((pn - self._rackvidd_m - self._n_bas) / self.cell_m).astype(np.int64)
        iy1 = np.floor((pn + self._rackvidd_m - self._n_bas) / self.cell_m).astype(np.int64)
        bredd = max(int((ix1 - ix0).max()), int((iy1 - iy0).max())) + 1
        steg = np.arange(bredd, dtype=np.int64)
        ix = ix0[:, None, None] + steg[None, :, None]
        iy = iy0[:, None, None] + steg[None, None, :]
        giltig = (ix <= ix1[:, None, None]) & (iy <= iy1[:, None, None])
        ix = np.clip(ix, 0, self._nx - 1)
        iy = np.clip(iy, 0, self._ny - 1)
        cell = ix * self._ny + iy
        nyckel = cell * antal_ben + (ben[:, None, None] + start)
        unika: Heltal = np.unique(nyckel[giltig])
        return unika

    def _kandidatben(
        self, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
    ) -> Heltal:
        if self._nyckel.shape[0] == 0:
            return np.zeros(0, dtype=np.int64)
        ix0 = int(math.floor((e_min_m - self._e_bas) / self.cell_m))
        ix1 = int(math.floor((e_max_m - self._e_bas) / self.cell_m))
        iy0 = int(math.floor((n_min_m - self._n_bas) / self.cell_m))
        iy1 = int(math.floor((n_max_m - self._n_bas) / self.cell_m))
        if ix1 < 0 or iy1 < 0 or ix0 >= self._nx or iy0 >= self._ny:
            return np.zeros(0, dtype=np.int64)
        ix0, iy0 = max(ix0, 0), max(iy0, 0)
        ix1, iy1 = min(ix1, self._nx - 1), min(iy1, self._ny - 1)
        kolumner = np.arange(ix0, ix1 + 1, dtype=np.int64) * self._ny
        fran = np.searchsorted(self._nyckel, kolumner + iy0, side="left")
        till = np.searchsorted(self._nyckel, kolumner + iy1, side="right")
        delar = [self._ben[a:b] for a, b in zip(fran, till, strict=True) if b > a]
        if not delar:
            return np.zeros(0, dtype=np.int64)
        ben: Heltal = np.unique(np.concatenate(delar))
        return ben

    def _inom_halvbredd(
        self, ben: Heltal, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
    ) -> Boolar:
        """Sant om avståndet mellan benet och rutan är högst halvbredden (plus marginal)."""
        e0, n0, e1, n1 = self._e0[ben], self._n0[ben], self._e1[ben], self._n1[ben]
        # Korsar benet rutan (skivmetoden)? Då är avståndet noll.
        t0 = np.zeros(ben.shape[0])
        t1 = np.ones(ben.shape[0])
        korsar = np.ones(ben.shape[0], dtype=np.bool_)
        for p0, p1, lag, hog in ((e0, e1, e_min_m, e_max_m), (n0, n1, n_min_m, n_max_m)):
            d = p1 - p0
            parallell = d == 0.0
            saker_d = np.where(parallell, 1.0, d)
            ta = (lag - p0) / saker_d
            tb = (hog - p0) / saker_d
            t0 = np.where(parallell, t0, np.maximum(t0, np.minimum(ta, tb)))
            t1 = np.where(parallell, t1, np.minimum(t1, np.maximum(ta, tb)))
            korsar &= ~parallell | ((p0 >= lag) & (p0 <= hog))
        korsar &= t0 <= t1
        # Annars antas minsta avståndet i en ändpunkt på benet eller i ett hörn på rutan.
        avstand = np.full(ben.shape[0], np.inf)
        for pe, pn in ((e0, n0), (e1, n1)):
            de = np.maximum(np.maximum(e_min_m - pe, 0.0), pe - e_max_m)
            dn = np.maximum(np.maximum(n_min_m - pn, 0.0), pn - n_max_m)
            avstand = np.minimum(avstand, np.hypot(de, dn))
        langd2 = (e1 - e0) ** 2 + (n1 - n0) ** 2
        saker_langd2 = np.where(langd2 > 0.0, langd2, 1.0)
        for he, hn in (
            (e_min_m, n_min_m),
            (e_min_m, n_max_m),
            (e_max_m, n_min_m),
            (e_max_m, n_max_m),
        ):
            t = np.clip(((he - e0) * (e1 - e0) + (hn - n0) * (n1 - n0)) / saker_langd2, 0.0, 1.0)
            avstand = np.minimum(
                avstand, np.hypot(e0 + t * (e1 - e0) - he, n0 + t * (n1 - n0) - hn)
            )
        resultat: Boolar = korsar | (avstand <= self.halvbredd_m + _MARGINAL_M)
        return resultat

    def berorda_banor(
        self, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
    ) -> Heltal:
        """Index (i den lista indexet byggdes av) för banor vars korridor kan korsa rutan."""
        if not all(math.isfinite(v) for v in (e_min_m, n_min_m, e_max_m, n_max_m)):
            raise BullerFel("Frågerutan har icke-ändliga koordinater.")
        if e_max_m < e_min_m or n_max_m < n_min_m:
            raise BullerFel(f"Frågerutan är felvänd: E {e_min_m}–{e_max_m}, N {n_min_m}–{n_max_m}.")
        ben = self._kandidatben(e_min_m, n_min_m, e_max_m, n_max_m)
        if self.forfina and ben.shape[0]:
            ben = ben[self._inom_halvbredd(ben, e_min_m, n_min_m, e_max_m, n_max_m)]
        banor: Heltal = np.unique(self._ben_bana[ben])
        return banor

    def berorda_par(
        self, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
    ) -> set[tuple[int, int]]:
        """(mottagare_id, kalla_id) för alla banor vars korridor korsar rutan.

        Kan innehålla falska träffar men saknar aldrig ett berört par.
        """
        banor = self.berorda_banor(e_min_m, n_min_m, e_max_m, n_max_m)
        return set(
            zip(self._mottagare_id[banor].tolist(), self._kalla_id[banor].tolist(), strict=True)
        )
