"""Akustiska grundfunktioner och baslinjekärna (paket B4a). Inte Nord2000.

Allt här är allmänt etablerad akustik med källa angiven i respektive funktions docstring.
Tidskonvention för komplexa storheter: exp(−iωt).
"""

from buller.akustik.band import (
    a_vagning_db,
    a_vagning_vid_frekvens_db,
    bandgranser_hz,
    delfrekvenser_hz,
    exakta_mittfrekvenser_hz,
)
from buller.akustik.baslinje import (
    ALLTID_IGNORERAT,
    BASLINJE_VERSION,
    MAX_MARKFAKTOR,
    MAX_SKARMDAMPNING_DB,
    BaslinjeKarna,
    BaslinjeResultat,
    kurze_anderson_db,
)
from buller.akustik.faddeeva import faddeeva
from buller.akustik.impedans import delany_bazley_admittans, delany_bazley_impedans
from buller.akustik.luftabsorption import (
    ljudhastighet_m_s,
    luftabsorption_db_per_m,
    molkoncentration_vattenanga_procent,
)
from buller.akustik.reflektion import (
    numeriskt_avstand,
    planvagsreflektion,
    randforlustfaktor,
    sfarisk_reflektion,
    tvastralefaktor,
)

__all__ = [
    "ALLTID_IGNORERAT",
    "BASLINJE_VERSION",
    "MAX_MARKFAKTOR",
    "MAX_SKARMDAMPNING_DB",
    "BaslinjeKarna",
    "BaslinjeResultat",
    "a_vagning_db",
    "a_vagning_vid_frekvens_db",
    "bandgranser_hz",
    "delany_bazley_admittans",
    "delany_bazley_impedans",
    "delfrekvenser_hz",
    "exakta_mittfrekvenser_hz",
    "faddeeva",
    "kurze_anderson_db",
    "ljudhastighet_m_s",
    "luftabsorption_db_per_m",
    "molkoncentration_vattenanga_procent",
    "numeriskt_avstand",
    "planvagsreflektion",
    "randforlustfaktor",
    "sfarisk_reflektion",
    "tvastralefaktor",
]
