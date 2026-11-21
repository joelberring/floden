"""Gränssnitt som operatorlagret bygger mot (levereras av `buller.akustik`, `buller.banor` m.fl.).

Operatorlagret beror inte på de andra delmodulerna. De skickar in funktioner och objekt med
signaturerna nedan. Index är positioner (0, 1, 2 …) i de listor över mottagare och källor som
lagret byggs för. Id är `Mottagare.id` och `Kallsegment.id` enligt KB1.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from buller.typer import BullerFel, Flyttal

KONTRAKT_KB2 = "KB2/v1"

# Parberäknare: (mottagarindex, källindex) -> överföring i dB med formen
# (n_kallor, n_vaderklasser, ANTAL_BAND), energisummerad över parets banor (KB2).
# Par utan bana ger -inf i alla element.
Parberaknare = Callable[[int, Sequence[int]], Flyttal]

# Övre gräns: minsta avstånd i meter (i plan) -> garanterad övre gräns för överföringen i dB
# per band, form (ANTAL_BAND,). Kontraktet är: för varje par med avstånd >= d, varje väderklass
# och varje band gäller H_db <= ovre_grans(d). Av det följer att funktionen inte får växa med d.
OvreGrans = Callable[[float], Flyttal]


@runtime_checkable
class Korridorindex(Protocol):
    """Rumsligt index över banornas korridorer (levereras av banfinnaren, B6)."""

    def berorda_par(
        self, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
    ) -> set[tuple[int, int]]:
        """Par (mottagare_id, kalla_id) vars banor kan påverkas av en ändring i rektangeln.

        Indexet ska vara konservativt: hellre ett par för mycket än ett för lite. Det gäller
        både banor som fanns före ändringen och banor som kan uppstå efter den.
        """
        ...


@dataclass(frozen=True)
class Rektangel:
    """Axelparallell rektangel i plan (SWEREF 99 18 00), meter."""

    e_min_m: float
    n_min_m: float
    e_max_m: float
    n_max_m: float

    def __post_init__(self) -> None:
        if not (self.e_min_m <= self.e_max_m and self.n_min_m <= self.n_max_m):
            raise BullerFel("Rektangel kräver e_min_m <= e_max_m och n_min_m <= n_max_m.")

    def union(self, annan: Rektangel) -> Rektangel:
        """Minsta rektangel som täcker båda."""
        return Rektangel(
            e_min_m=min(self.e_min_m, annan.e_min_m),
            n_min_m=min(self.n_min_m, annan.n_min_m),
            e_max_m=max(self.e_max_m, annan.e_max_m),
            n_max_m=max(self.n_max_m, annan.n_max_m),
        )
