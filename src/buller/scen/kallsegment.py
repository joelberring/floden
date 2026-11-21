"""Källsegment: vägpolylinjer delas i raka delar som blir punktkällor (`typer.Kallsegment`).

Två steg: `dela_vagar` ger delar med fast maxlängd, och `forfina_for_mottagare` delar dem
vidare så att ingen del upptar mer än en given vinkel sett från en mottagare. Vinkelgränsen
har inget standardvärde: Nord2000:s kriterium står i metodrapporten, som saknas
(se `docs/buller/kallor.md`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from buller.scen.bullerscen import Boolar, Vag, validera_vag
from buller.typer import BullerFel, Kallsegment

# Närmare än så räknas mottagaren som liggande på vägdelen (vinkeln sätts till 180°).
_PA_VAGEN_M = 1e-9


@dataclass(frozen=True)
class Vagdel:
    """Rak del av en väg, från A till B."""

    vag_id: str
    korfalt: int
    a_e_m: float
    a_n_m: float
    b_e_m: float
    b_n_m: float

    @property
    def langd_m(self) -> float:
        return math.hypot(self.b_e_m - self.a_e_m, self.b_n_m - self.a_n_m)

    @property
    def mitt_e_m(self) -> float:
        return 0.5 * (self.a_e_m + self.b_e_m)

    @property
    def mitt_n_m(self) -> float:
        return 0.5 * (self.a_n_m + self.b_n_m)

    def vinkel_fran_deg(self, e_m: float, n_m: float) -> float:
        """Vinkeln (grader) som delen upptar sett från punkten (E, N)."""
        ae, an = self.a_e_m - e_m, self.a_n_m - n_m
        be, bn = self.b_e_m - e_m, self.b_n_m - n_m
        if math.hypot(ae, an) < _PA_VAGEN_M or math.hypot(be, bn) < _PA_VAGEN_M:
            return 180.0
        return math.degrees(math.atan2(abs(ae * bn - an * be), ae * be + an * bn))

    def halvor(self) -> tuple[Vagdel, Vagdel]:
        """Delen kluven på mitten."""
        me, mn = self.mitt_e_m, self.mitt_n_m
        return (
            Vagdel(self.vag_id, self.korfalt, self.a_e_m, self.a_n_m, me, mn),
            Vagdel(self.vag_id, self.korfalt, me, mn, self.b_e_m, self.b_n_m),
        )


@dataclass(frozen=True, eq=False)
class Forfining:
    """Resultat av vinkelstyrd förfining för en mottagare."""

    delar: tuple[Vagdel, ...]
    # Falskt för delar som nått `min_langd_m` utan att klara vinkelgränsen.
    uppfyller_vinkel: Boolar

    @property
    def alla_uppfyller(self) -> bool:
        return bool(np.all(self.uppfyller_vinkel))


def dela_vagar(vagar: Sequence[Vag], max_langd_m: float) -> list[Vagdel]:
    """Delar varje rakt polylinjesegment i lika långa delar, högst `max_langd_m` långa."""
    if not (math.isfinite(max_langd_m) and max_langd_m > 0.0):
        raise BullerFel(f"Maxlängden för källsegment ska vara > 0 m, fick {max_langd_m}.")
    delar: list[Vagdel] = []
    for vag in vagar:
        validera_vag(vag)
        e_m = np.asarray(vag.e_m, dtype=np.float64)
        n_m = np.asarray(vag.n_m, dtype=np.float64)
        for i in range(e_m.shape[0] - 1):
            e0, n0, e1, n1 = float(e_m[i]), float(n_m[i]), float(e_m[i + 1]), float(n_m[i + 1])
            antal = max(1, math.ceil(math.hypot(e1 - e0, n1 - n0) / max_langd_m))
            for j in range(antal):
                f0, f1 = j / antal, (j + 1) / antal
                delar.append(
                    Vagdel(
                        vag_id=vag.id,
                        korfalt=vag.korfalt,
                        a_e_m=e0 + (e1 - e0) * f0,
                        a_n_m=n0 + (n1 - n0) * f0,
                        b_e_m=e0 + (e1 - e0) * f1,
                        b_n_m=n0 + (n1 - n0) * f1,
                    )
                )
    return delar


def forfina_for_mottagare(
    delar: Sequence[Vagdel],
    mottagare_e_m: float,
    mottagare_n_m: float,
    *,
    max_vinkel_deg: float,
    min_langd_m: float,
) -> Forfining:
    """Klyver delar tills ingen upptar mer än `max_vinkel_deg` sett från mottagaren.

    En del klyvs inte om halvorna skulle bli kortare än `min_langd_m`; sådana delar finns
    kvar och markeras i `uppfyller_vinkel`. Ordningen längs vägen bevaras.
    """
    if not (math.isfinite(max_vinkel_deg) and 0.0 < max_vinkel_deg < 180.0):
        raise BullerFel(f"Vinkelgränsen ska ligga mellan 0 och 180 grader, fick {max_vinkel_deg}.")
    if not (math.isfinite(min_langd_m) and min_langd_m > 0.0):
        raise BullerFel(f"Minsta dellängd ska vara > 0 m, fick {min_langd_m}.")
    if not (math.isfinite(mottagare_e_m) and math.isfinite(mottagare_n_m)):
        raise BullerFel("Mottagarens läge är inte ändligt.")
    klara: list[Vagdel] = []
    uppfyller: list[bool] = []
    # Stack med delar i omvänd ordning, så att resultatet följer vägens riktning.
    kvar = list(reversed(delar))
    while kvar:
        del_ = kvar.pop()
        ok = del_.vinkel_fran_deg(mottagare_e_m, mottagare_n_m) <= max_vinkel_deg
        if ok or del_.langd_m / 2.0 < min_langd_m:
            klara.append(del_)
            uppfyller.append(ok)
        else:
            forsta, andra = del_.halvor()
            kvar.append(andra)
            kvar.append(forsta)
    return Forfining(delar=tuple(klara), uppfyller_vinkel=np.array(uppfyller, dtype=np.bool_))


def till_kallsegment(
    delar: Sequence[Vagdel],
    delkallhojder_m: Sequence[float],
    *,
    forsta_id: int = 0,
) -> list[Kallsegment]:
    """En punktkälla per del och delkällhöjd, i delens mittpunkt.

    Id löper från `forsta_id` i ordningen (del, delkällhöjd).
    """
    hojder = [float(h) for h in delkallhojder_m]
    if not hojder:
        raise BullerFel("Minst en delkällhöjd måste anges.")
    if any(not math.isfinite(h) or h < 0.0 for h in hojder):
        raise BullerFel(f"Delkällhöjderna ska vara ändliga och inte negativa, fick {hojder}.")
    if len(set(hojder)) != len(hojder):
        raise BullerFel(f"Delkällhöjderna ska vara olika, fick {hojder}.")
    segment: list[Kallsegment] = []
    for del_ in delar:
        for hojd_m in hojder:
            segment.append(
                Kallsegment(
                    id=forsta_id + len(segment),
                    e_m=del_.mitt_e_m,
                    n_m=del_.mitt_n_m,
                    hojd_over_mark_m=hojd_m,
                    langd_m=del_.langd_m,
                    vag_id=del_.vag_id,
                    korfalt=del_.korfalt,
                )
            )
    return segment


def kallsegment_fran_vagar(
    vagar: Sequence[Vag],
    *,
    max_langd_m: float,
    delkallhojder_m: Sequence[float],
    forsta_id: int = 0,
) -> list[Kallsegment]:
    """Delar vägarna med fast maxlängd och gör punktkällor för varje delkällhöjd."""
    return till_kallsegment(dela_vagar(vagar, max_langd_m), delkallhojder_m, forsta_id=forsta_id)
