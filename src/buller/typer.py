"""Gemensamma typer för bullermodulen (kontrakt KB1/v1, se `docs/buller/KONTRAKT.md`).

Alla delmoduler i `buller` bygger mot typerna här. Ändra dem inte utan att höja
kontraktsversionen: flera agenter arbetar parallellt mot samma gränssnitt.

Enheter: meter, sekunder, hertz, decibel. Plan i SWEREF 99 18 00 (EPSG:3011), höjd i RH 2000.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

KONTRAKT_KB1 = "KB1/v1"

# Nominella mittfrekvenser för tersband 25 Hz – 10 kHz (27 band), samma omfång som Nord2000.
TERSBAND_HZ: tuple[float, ...] = (
    25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0, 250.0, 315.0, 400.0,
    500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0, 2000.0, 2500.0, 3150.0, 4000.0, 5000.0,
    6300.0, 8000.0, 10000.0,
)  # fmt: skip
ANTAL_BAND = len(TERSBAND_HZ)

Flyttal = NDArray[np.float64]
Banatyp = Literal["direkt", "reflex"]


class BullerFel(Exception):
    """Fel i bullermodulen. Hellre ett tydligt fel än ett osäkert resultat."""


@dataclass(frozen=True)
class Terrangprofil:
    """Vertikalt snitt från källa till mottagare.

    `x_m[0] = 0` ligger under källan och `x_m[-1]` under mottagaren. Punkterna beskriver
    markytan *inklusive* skärmande byggnader och murar (överkant). Segment `i` går mellan
    punkt `i` och `i + 1`.
    """

    x_m: Flyttal  # form (n,), strikt växande, x_m[0] == 0
    z_m: Flyttal  # form (n,), höjd RH 2000
    flodesresistivitet_kNsm4: Flyttal  # form (n - 1,), per segment
    rahet_m: Flyttal  # form (n - 1,), per segment
    kalla_hojd_m: float  # över marken i x_m[0]
    mottagare_hojd_m: float  # över marken i x_m[-1]

    def __post_init__(self) -> None:
        n = self.x_m.shape[0]
        if n < 2 or self.z_m.shape != (n,):
            raise BullerFel("Terrangprofil kräver minst två punkter och lika långa x_m och z_m.")
        if self.flodesresistivitet_kNsm4.shape != (n - 1,) or self.rahet_m.shape != (n - 1,):
            raise BullerFel("Markegenskaper ska anges per segment (n - 1 värden).")
        if self.x_m[0] != 0.0 or not bool(np.all(np.diff(self.x_m) > 0.0)):
            raise BullerFel("x_m ska börja på 0 och vara strikt växande.")
        if self.kalla_hojd_m < 0.0 or self.mottagare_hojd_m < 0.0:
            raise BullerFel("Höjd över mark får inte vara negativ.")
        if not bool(np.all(np.isfinite(self.z_m))):
            raise BullerFel("Terrangprofilen innehåller icke-ändliga höjder.")


@dataclass(frozen=True)
class Vader:
    """Väderparametrar för en punkt-till-punkt-beräkning (Nord2000:s parameteruppsättning).

    `vind_m_s` är vindkomponenten i utbredningsriktningen på höjden `zu_m` (medvind positiv).
    """

    temperatur_c: float = 15.0
    relativ_fuktighet_procent: float = 70.0
    lufttryck_kpa: float = 101.325
    vind_m_s: float = 0.0
    zu_m: float = 10.0
    z0_m: float = 0.05
    dt_dz_k_m: float = 0.0
    std_vind_m_s: float = 0.0
    std_dt_dz_k_m: float = 0.0
    cv2: float = 0.0
    ct2: float = 0.0
    vikt: float = 1.0  # andel av tiden för väderklassen (summerar till 1 över klasserna)


@runtime_checkable
class P2PKarna(Protocol):
    """Punkt-till-punkt-kärna: överföring från punktkälla till mottagare.

    `overforing_db` returnerar per tersband `L_p - L_W`, alltså allt mellan källans
    ljudeffektnivå och ljudtrycksnivån hos mottagaren (geometrisk spridning inräknad).
    `nord2000_konform` får bara vara sann för en kärna som passerar testfallen i
    `testsvit/` grupp N. Rapportläget vägrar andra kärnor.
    """

    namn: str
    version: str
    nord2000_konform: bool

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        """Form (ANTAL_BAND,), decibel."""
        ...


@dataclass(frozen=True)
class Kallsegment:
    """Punktkälla som representerar ett vägsegment och en delkällhöjd."""

    id: int
    e_m: float
    n_m: float
    hojd_over_mark_m: float
    langd_m: float
    vag_id: str
    korfalt: int = 0


@dataclass(frozen=True)
class Mottagare:
    """Beräkningspunkt. För fasadpunkter anges fasadens utåtriktade normal och byggnad."""

    id: int
    e_m: float
    n_m: float
    hojd_over_mark_m: float
    byggnad_id: str | None = None
    vaning: int | None = None
    fasadnormal_deg: float | None = None  # kompassriktning, 0 = norr, medurs


@dataclass(frozen=True)
class Bana:
    """En utbredningsväg mellan ett källsegment och en mottagare."""

    mottagare_id: int
    kalla_id: int
    typ: Banatyp
    profil: Terrangprofil
    # Plangeometri (E, N) för banans knäckpunkter: källa, ev. reflexpunkter, mottagare.
    plan_e_m: Flyttal
    plan_n_m: Flyttal
    # Förlust i reflexer per tersband (0 för direkt bana), form (ANTAL_BAND,).
    reflexforlust_db: Flyttal = field(default_factory=lambda: np.zeros(ANTAL_BAND))
    reflekterande_byggnader: tuple[str, ...] = ()


@dataclass(frozen=True)
class Byggnad:
    """Byggnadsvolym i scenen (LoD1): sluten polygon i plan och takhöjd."""

    id: str
    e_m: Flyttal  # form (k,), hörn i ordning, ej upprepad startpunkt
    n_m: Flyttal
    tak_rh2000_m: float
    reflektionsforlust_db: float = 1.0
