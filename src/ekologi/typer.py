"""Datatyper och undantag för ekologiska nätverksstudier (`src/ekologi/`).

All intern beräkning sker i SWEREF 99 med SI-enheter (`_m`, `_m2`).
Alla metoder i modulen är `skiss` tills oberoende ekologisk/matematisk verifiering är klar.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

FloatRaster = NDArray[np.float64]
IntRaster = NDArray[np.int32]
UInt8Raster = NDArray[np.uint8]
BoolRaster = NDArray[np.bool_]


class EkologiFel(ValueError):
    """Tydligt fel vid ogiltiga indata eller parametrar i ekologiska nätverksberäkningar."""


@dataclass(frozen=True)
class ArtProfil:
    """Fokusart-/artgruppsprofil för habitat och spridningsmotstånd (skiss).

    Parametrarna saknar exakta källhänvisningar.
    """

    profil_id: str
    namn: str
    beskrivning: str
    kalla: str
    habitat_markslag: frozenset[int]
    min_area_m2: float
    spridningsavstand_m: float
    motstand_per_markslag: Mapping[int, float]
    standard_motstand: float = 10.0
    min_kronhojd_m: float = 0.0
    motstand_osakerhet_faktor: tuple[float, float] = (0.7, 1.4)
    metodstatus: str = "skiss"


@dataclass(frozen=True)
class HabitatFlack:
    """En sammanhängande habitatfläck (nod i den ekologiska nätverksgrafen)."""

    flack_id: int
    antal_celler: int
    area_m2: float
    effektiv_area_m2: float
    centroid_rad: float
    centroid_kol: float
    centroid_e_m: float
    centroid_n_m: float


@dataclass(frozen=True)
class HabitatKarta:
    """Märkta habitatfläckar extraherade ur ett markslagsraster."""

    flack_raster: IntRaster  # 0 = ej habitat, 1..m = flack_id
    flackar: tuple[HabitatFlack, ...]
    cell_m: float
    analys_area_m2: float


@dataclass(frozen=True)
class MotstandsYta:
    """Motstånds- och konduktansraster för en given artprofil."""

    motstand: FloatRaster  # >= 1.0 eller np.inf (total barriär)
    konduktans: FloatRaster  # 1 / motstand i [0.0, 1.0]
    cell_m: float
    profil_id: str


@dataclass(frozen=True)
class KretsParResultat:
    """Kretsteoretisk lösning mellan två habitatfläckar (McRae m.fl. 2008)."""

    fran_flack_id: int
    till_flack_id: int
    effektivt_motstand: float  # np.inf om helt åtskilda av barriär
    total_strom: float
    strombalans_fel_rel: float
    potential: FloatRaster
    stromtathet: FloatRaster  # normaliserad strömtäthet i [0, 1] per cell
    kanslighet_cell: FloatRaster  # <= 0, exakt dR_eff / dG summerat på cellens kanter
    sammankopplad: bool


@dataclass(frozen=True)
class FlackBetydelse:
    """Saura & Rubio (2010)-dekomponering av en habitatfläcks betydelse (dPC i %)."""

    flack_id: int
    dpc_procent: float
    dpc_intra_procent: float
    dpc_flux_procent: float
    dpc_connector_procent: float


@dataclass(frozen=True)
class KonnektivitetsResultat:
    """Deterministiska konnektivitetsmått (PC, ECA, kostnadsavstånd och ev. flaskhalskarta)."""

    profil_id: str
    antal_flackar: int
    total_habitat_area_m2: float
    analys_area_m2: float
    pc: float
    eca_m2: float
    kostnadsavstand_m: FloatRaster  # (m, m) minsta kostnadsavstånd mellan fläckar
    p_stjarna: FloatRaster  # (m, m) maximal produktsannolikhet p*_ij
    flack_betydelse: tuple[FlackBetydelse, ...]
    stromtathet_sammanlagd: FloatRaster | None = None
    kanslighet_sammanlagd: FloatRaster | None = None
    """Viktat medel av dR_eff/dG (<= 0) per cell över fläckparen, samma vikter som strömtätheten."""
    antal_kretspar_beraknade: int = 0
    antal_kretspar_totalt: int = 0
    kretspar_vikt_andel: float = 1.0
    krets_upplosningsfaktor: int = 1


@dataclass(frozen=True)
class ForeEfterEkologi:
    """Före/efter-jämförelse av ekologisk konnektivitet för ett planförslag."""

    fore: KonnektivitetsResultat
    efter: KonnektivitetsResultat
    delta_eca_m2: float
    delta_eca_procent: float
    delta_pc: float
    delta_habitat_area_m2: float


@dataclass(frozen=True)
class EkologiIntervallResultat:
    """Monotont garanterat osäkerhetsintervall [eca_min, eca_max] utan Monte Carlo."""

    profil_id: str
    eca_min_m2: float
    eca_max_m2: float
    pc_min: float
    pc_max: float
    motstand_faktor_intervall: tuple[float, float]
    spridning_faktor_intervall: tuple[float, float]


@dataclass(frozen=True)
class EkologiAtgard:
    """En kandidatåtgärd (t.ex. trädplantering, äng, ekodukt) som sänker motstånd lokalt."""

    atgard_id: str
    mask: BoolRaster
    nytt_motstand: float
    gor_till_habitat: bool = False


@dataclass(frozen=True)
class EkologiAtgardVal:
    """En vald grön kompensationsåtgärd ur submodulär CELF-optimering."""

    atgard_id: str
    marginal_eca_vinst_m2: float
    kumulativ_eca_vinst_m2: float
    vinst_ensam_m2: float


@dataclass(frozen=True)
class KallbaseratIntervall:
    """Källbaserat intervall: lägsta och högsta värde mellan källor och scenarier."""

    natverk_id: str
    scenario_ids: tuple[str, ...]
    eca_per_scenario_m2: tuple[float, ...]
    pc_per_scenario: tuple[float, ...]
    eca_min_m2: float
    eca_max_m2: float
    pc_min: float
    pc_max: float
    ett_scenario: bool
