"""Vägnätsgraf och effektivt vattendjup för mark, broar och tunnlar (T07 steg 1)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

GILTIGA_KANTTYPER: frozenset[str] = frozenset({"mark", "bro", "tunnel"})


class VagNatFel(Exception):
    """Fel vid konstruktion eller uppslag i vägnätsgraf."""


@dataclass(frozen=True)
class VagNod:
    """En korsning eller ändpunkt i vägnätet (SWEREF 99 18 00, RH 2000)."""

    nod_id: str
    e_m: float
    n_m: float
    z_rh2000: float


@dataclass(frozen=True)
class VagKant:
    """Ett riktat eller dubbelriktat gatuavsnitt i vägnätet."""

    kant_id: str
    fran_nod: str
    till_nod: str
    langd_m: float
    normal_hastighet_km_h: float
    typ: str = "mark"
    vagbana_rh2000: float | None = None
    koordinater: tuple[tuple[float, float], ...] = ()
    enkelriktad: bool = False

    def __post_init__(self) -> None:
        if self.typ not in GILTIGA_KANTTYPER:
            raise VagNatFel(
                f"Ogiltig kanttyp '{self.typ}' för {self.kant_id}. "
                f"Tillåtna: {sorted(GILTIGA_KANTTYPER)}."
            )
        if not math.isfinite(self.langd_m) or self.langd_m <= 0.0:
            raise VagNatFel(
                f"Kantlängd för {self.kant_id} måste vara > 0, fick {self.langd_m}."
            )
        if not math.isfinite(self.normal_hastighet_km_h) or self.normal_hastighet_km_h <= 0.0:
            raise VagNatFel(
                f"Normalhastighet för {self.kant_id} måste vara > 0, "
                f"fick {self.normal_hastighet_km_h}."
            )
        if self.vagbana_rh2000 is not None and not math.isfinite(self.vagbana_rh2000):
            raise VagNatFel(
                f"Ogiltig vägbanehöjd för {self.kant_id}: {self.vagbana_rh2000}."
            )

    @property
    def normal_hastighet_m_s(self) -> float:
        """Normalhastighet omräknad från km/h till m/s."""
        return float(self.normal_hastighet_km_h) / 3.6


@dataclass(frozen=True)
class UtgaendeKant:
    """En riktad gångbar båge från en nod i grannlistan."""

    kant: VagKant
    mal_nod: str


@dataclass
class VagNat:
    """Vägnätsgraf med noder, kanter och riktade grannlistor."""

    noder: dict[str, VagNod] = field(default_factory=dict)
    kanter: dict[str, VagKant] = field(default_factory=dict)
    grannar: dict[str, list[UtgaendeKant]] = field(default_factory=dict)

    @classmethod
    def fran_komponenter(
        cls,
        noder: Iterable[VagNod],
        kanter: Iterable[VagKant],
    ) -> VagNat:
        """Bygger ett validerat `VagNat` från samlingar av noder och kanter."""
        graf = cls()
        for nod in noder:
            graf.lagg_till_nod(nod)
        for kant in kanter:
            graf.lagg_till_kant(kant)
        return graf

    def lagg_till_nod(self, nod: VagNod) -> None:
        """Lägger till en nod i grafen (kastar `VagNatFel` vid dubblett)."""
        if nod.nod_id in self.noder:
            raise VagNatFel(f"Nod '{nod.nod_id}' finns redan i vägnätet.")
        self.noder[nod.nod_id] = nod
        self.grannar.setdefault(nod.nod_id, [])

    def lagg_till_kant(self, kant: VagKant) -> None:
        """Lägger till en kant (och dess omvända båge om `enkelriktad=False`)."""
        if kant.kant_id in self.kanter:
            raise VagNatFel(f"Kant '{kant.kant_id}' finns redan i vägnätet.")
        if kant.fran_nod not in self.noder:
            raise VagNatFel(
                f"Kant '{kant.kant_id}' refererar okänd startnod '{kant.fran_nod}'."
            )
        if kant.till_nod not in self.noder:
            raise VagNatFel(
                f"Kant '{kant.kant_id}' refererar okänd slutnod '{kant.till_nod}'."
            )

        self.kanter[kant.kant_id] = kant
        self.grannar[kant.fran_nod].append(
            UtgaendeKant(kant=kant, mal_nod=kant.till_nod)
        )
        if not kant.enkelriktad and kant.fran_nod != kant.till_nod:
            self.grannar[kant.till_nod].append(
                UtgaendeKant(kant=kant, mal_nod=kant.fran_nod)
            )

    def kant_koordinater(self, kant_id: str) -> tuple[tuple[float, float], ...]:
        """Returnerar linjekoordinater för en kant (faller tillbaka till ändnoderna)."""
        kant = self.kanter[kant_id]
        if len(kant.koordinater) >= 2:
            return kant.koordinater
        n0 = self.noder[kant.fran_nod]
        n1 = self.noder[kant.till_nod]
        return ((n0.e_m, n0.n_m), (n1.e_m, n1.n_m))

    def grannlista(self, nod_id: str) -> Sequence[UtgaendeKant]:
        """Returnerar utgående bågar från `nod_id`."""
        if nod_id not in self.noder:
            raise VagNatFel(f"Okänd nod '{nod_id}' i vägnätet.")
        return self.grannar[nod_id]


def effektivt_vagdjup_m(
    kant: VagKant,
    mark_vattendjup_m: float,
    vattenniva_rh2000: float | None = None,
    *,
    mark_hojd_rh2000: float | None = None,
) -> float:
    """Beräknar effektivt vattendjup på vägbanan för mark, bro eller tunnel.

    - `"bro"`: Om `vagbana_rh2000` och `vattenniva_rh2000` ges blir djupet
      `max(0.0, vattenniva_rh2000 - vagbana_rh2000)`. Så länge vägbanan ligger
      över vattenytan är djupet `0.0 m` (farbar!) även om marken under bron
      är djupt översvämmad. Om endast `mark_hojd_rh2000` + `mark_vattendjup_m`
      ges härleds vattenytan som `mark_hojd_rh2000 + mark_vattendjup_m`.
      Om varken vattenyta eller markhöjd finns för en bro som saknar överspolning
      returneras `0.0` när `vagbana_rh2000` är angiven och djupet mäts under bron.
    - `"tunnel"`: Om `vagbana_rh2000` och `vattenniva_rh2000` ges returneras
      `max(mark_vattendjup_m, vattenniva_rh2000 - vagbana_rh2000, 0.0)`.
    - `"mark"`: Returnerar `max(0.0, mark_vattendjup_m)`.
    """
    if not math.isfinite(mark_vattendjup_m):
        raise VagNatFel(
            f"Ogiltigt markvattendjup för kant '{kant.kant_id}': {mark_vattendjup_m}."
        )
    if vattenniva_rh2000 is not None and not math.isfinite(vattenniva_rh2000):
        raise VagNatFel(
            f"Ogiltig vattennivå för kant '{kant.kant_id}': {vattenniva_rh2000}."
        )

    eff_vattenniva = vattenniva_rh2000
    if eff_vattenniva is None and mark_hojd_rh2000 is not None:
        if not math.isfinite(mark_hojd_rh2000):
            raise VagNatFel(
                f"Ogiltig markhöjd för kant '{kant.kant_id}': {mark_hojd_rh2000}."
            )
        if mark_vattendjup_m > 0.0:
            eff_vattenniva = float(mark_hojd_rh2000) + float(mark_vattendjup_m)

    if kant.typ == "bro":
        if kant.vagbana_rh2000 is not None and eff_vattenniva is not None:
            return max(0.0, float(eff_vattenniva) - float(kant.vagbana_rh2000))
        if kant.vagbana_rh2000 is not None:
            # Markdjupet avser ytan under bron; utan angiven överstigande vattennivå
            # förutsätts bron gå fri över markens vattenspegel.
            return 0.0
        return max(0.0, float(mark_vattendjup_m))

    if kant.typ == "tunnel":
        djup_mark = max(0.0, float(mark_vattendjup_m))
        if kant.vagbana_rh2000 is not None and eff_vattenniva is not None:
            djup_niva = max(0.0, float(eff_vattenniva) - float(kant.vagbana_rh2000))
            return max(djup_mark, djup_niva)
        return djup_mark

    return max(0.0, float(mark_vattendjup_m))
