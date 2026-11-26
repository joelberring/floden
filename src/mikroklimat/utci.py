"""Universal Thermal Climate Index (UTCI) enligt Bröde m.fl. (2012).

Implementerar den officiella 6:e-ordningens polynomapproximation (210 koefficienter)
ur Bröde m.fl. (2012), *Int. J. Biometeorol.* 56:481–494 och referenskoden
`UTCI_a002.f90` (Version a 0.002, Peter Bröde / COST Action 730), samt Hardy (1998)
ITS-90-ekvation för mättat vattenångtryck över vatten och logaritmisk vindprofil.

Licens: GPL v3. Metodstatus: `skiss` (se `docs/MIKROKLIMAT.md`).
Alla enheter anges i variabelnamn (`_c`, `_m`, `_m_s`, `_pct`, `_hpa`, `_kpa`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, overload

import numpy as np
from numpy.typing import NDArray

FloatRaster = NDArray[np.float64]
Int8Raster = NDArray[np.int8]
BoolRaster = NDArray[np.bool_]
SkalarEllerArray = float | int | NDArray[np.floating[Any]] | NDArray[np.integer[Any]]

# Giltighetsgränser för Bröde m.fl. (2012) / UTCI_a002.f90 (inklusiva).
UTCI_TA_MIN_C: float = -50.0
UTCI_TA_MAX_C: float = 50.0
UTCI_D_TMRT_MIN_C: float = -30.0
UTCI_D_TMRT_MAX_C: float = 70.0
UTCI_VIND_10M_MIN_M_S: float = 0.5
UTCI_VIND_10M_MAX_M_S: float = 17.0
UTCI_RH_MIN_PCT: float = 5.0
UTCI_RH_MAX_PCT: float = 100.0
UTCI_PA_MIN_KPA: float = 0.0
UTCI_PA_MAX_KPA: float = 5.0

# Sentinelvärde i `klass_id` (int8) för NaN / ogiltiga celler.
UTCI_KLASS_OGILTIG: int = -99

_KELVIN_OFFSET_C: float = 273.15

# Hardy (1998) ITS-90-koefficienter för mättat ångtryck över vatten (Pa), som i UTCI_a002.f90.
_HARDY_G: tuple[float, ...] = (
    -2836.5744,
    -6028.076559,
    19.54263612,
    -0.02737830188,
    0.000016261698,
    7.0229056e-10,
    -1.8680009e-13,
)
_HARDY_G7: float = 2.7150305


@dataclass(frozen=True)
class UtciStressklass:
    """En kategori i Bröde m.fl. (2012) Tabell 1: 10-gradig termisk stresskala."""

    klass_id: int
    namn_sv: str
    namn_en: str
    nedre_grans_c: float | None
    ovre_grans_c: float | None


# Officiell 10-gradig UTCI-stresskala (Bröde m.fl. 2012, Tabell 1).
# Centrerad kring 0 = "Ingen termisk stress" (+9 till +26 °C):
# 5 köldklasser (-5..-1), 1 neutral (0) och 4 värmeklasser (+1..+4).
UTCI_STRESSKLASSER: tuple[UtciStressklass, ...] = (
    UtciStressklass(-5, "Extrem köldstress", "extreme cold stress", None, -40.0),
    UtciStressklass(-4, "Mycket stark köldstress", "very strong cold stress", -40.0, -27.0),
    UtciStressklass(-3, "Stark köldstress", "strong cold stress", -27.0, -13.0),
    UtciStressklass(-2, "Måttlig köldstress", "moderate cold stress", -13.0, 0.0),
    UtciStressklass(-1, "Svag köldstress", "slight cold stress", 0.0, 9.0),
    UtciStressklass(0, "Ingen termisk stress", "no thermal stress", 9.0, 26.0),
    UtciStressklass(1, "Måttlig värmestress", "moderate heat stress", 26.0, 32.0),
    UtciStressklass(2, "Stark värmestress", "strong heat stress", 32.0, 38.0),
    UtciStressklass(3, "Mycket stark värmestress", "very strong heat stress", 38.0, 46.0),
    UtciStressklass(4, "Extrem värmestress", "extreme heat stress", 46.0, None),
)

UTCI_STRESSKLASS_PER_ID: dict[int, UtciStressklass] = {
    k.klass_id: k for k in UTCI_STRESSKLASSER
}

# Alla 210 officiella koefficienter ur Bröde m.fl. (2012) / UTCI_a002.f90 (Version a 0.002).
# Format: (koefficient, p_ta, p_va, p_d_tmrt, p_pa) för termen:
#   koefficient * Ta^p_ta * va^p_va * (Tmrt - Ta)^p_d_tmrt * Pa^p_pa
# där Ta [°C], va = vind vid 10 m [m/s], D_Tmrt = Tmrt - Ta [°C], Pa = ångtryck [kPa].
UTCI_POLYNOM_TERMER: tuple[tuple[float, int, int, int, int], ...] = (
    (6.07562052e-01, 0, 0, 0, 0),
    (-2.27712343e-02, 1, 0, 0, 0),
    (8.06470249e-04, 2, 0, 0, 0),
    (-1.54271372e-04, 3, 0, 0, 0),
    (-3.24651735e-06, 4, 0, 0, 0),
    (7.32602852e-08, 5, 0, 0, 0),
    (1.35959073e-09, 6, 0, 0, 0),
    (-2.25836520e+00, 0, 1, 0, 0),
    (8.80326035e-02, 1, 1, 0, 0),
    (2.16844454e-03, 2, 1, 0, 0),
    (-1.53347087e-05, 3, 1, 0, 0),
    (-5.72983704e-07, 4, 1, 0, 0),
    (-2.55090145e-09, 5, 1, 0, 0),
    (-7.51269505e-01, 0, 2, 0, 0),
    (-4.08350271e-03, 1, 2, 0, 0),
    (-5.21670675e-05, 2, 2, 0, 0),
    (1.94544667e-06, 3, 2, 0, 0),
    (1.14099531e-08, 4, 2, 0, 0),
    (1.58137256e-01, 0, 3, 0, 0),
    (-6.57263143e-05, 1, 3, 0, 0),
    (2.22697524e-07, 2, 3, 0, 0),
    (-4.16117031e-08, 3, 3, 0, 0),
    (-1.27762753e-02, 0, 4, 0, 0),
    (9.66891875e-06, 1, 4, 0, 0),
    (2.52785852e-09, 2, 4, 0, 0),
    (4.56306672e-04, 0, 5, 0, 0),
    (-1.74202546e-07, 1, 5, 0, 0),
    (-5.91491269e-06, 0, 6, 0, 0),
    (3.98374029e-01, 0, 0, 1, 0),
    (1.83945314e-04, 1, 0, 1, 0),
    (-1.73754510e-04, 2, 0, 1, 0),
    (-7.60781159e-07, 3, 0, 1, 0),
    (3.77830287e-08, 4, 0, 1, 0),
    (5.43079673e-10, 5, 0, 1, 0),
    (-2.00518269e-02, 0, 1, 1, 0),
    (8.92859837e-04, 1, 1, 1, 0),
    (3.45433048e-06, 2, 1, 1, 0),
    (-3.77925774e-07, 3, 1, 1, 0),
    (-1.69699377e-09, 4, 1, 1, 0),
    (1.69992415e-04, 0, 2, 1, 0),
    (-4.99204314e-05, 1, 2, 1, 0),
    (2.47417178e-07, 2, 2, 1, 0),
    (1.07596466e-08, 3, 2, 1, 0),
    (8.49242932e-05, 0, 3, 1, 0),
    (1.35191328e-06, 1, 3, 1, 0),
    (-6.21531254e-09, 2, 3, 1, 0),
    (-4.99410301e-06, 0, 4, 1, 0),
    (-1.89489258e-08, 1, 4, 1, 0),
    (8.15300114e-08, 0, 5, 1, 0),
    (7.55043090e-04, 0, 0, 2, 0),
    (-5.65095215e-05, 1, 0, 2, 0),
    (-4.52166564e-07, 2, 0, 2, 0),
    (2.46688878e-08, 3, 0, 2, 0),
    (2.42674348e-10, 4, 0, 2, 0),
    (1.54547250e-04, 0, 1, 2, 0),
    (5.24110970e-06, 1, 1, 2, 0),
    (-8.75874982e-08, 2, 1, 2, 0),
    (-1.50743064e-09, 3, 1, 2, 0),
    (-1.56236307e-05, 0, 2, 2, 0),
    (-1.33895614e-07, 1, 2, 2, 0),
    (2.49709824e-09, 2, 2, 2, 0),
    (6.51711721e-07, 0, 3, 2, 0),
    (1.94960053e-09, 1, 3, 2, 0),
    (-1.00361113e-08, 0, 4, 2, 0),
    (-1.21206673e-05, 0, 0, 3, 0),
    (-2.18203660e-07, 1, 0, 3, 0),
    (7.51269482e-09, 2, 0, 3, 0),
    (9.79063848e-11, 3, 0, 3, 0),
    (1.25006734e-06, 0, 1, 3, 0),
    (-1.81584736e-09, 1, 1, 3, 0),
    (-3.52197671e-10, 2, 1, 3, 0),
    (-3.36514630e-08, 0, 2, 3, 0),
    (1.35908359e-10, 1, 2, 3, 0),
    (4.17032620e-10, 0, 3, 3, 0),
    (-1.30369025e-09, 0, 0, 4, 0),
    (4.13908461e-10, 1, 0, 4, 0),
    (9.22652254e-12, 2, 0, 4, 0),
    (-5.08220384e-09, 0, 1, 4, 0),
    (-2.24730961e-11, 1, 1, 4, 0),
    (1.17139133e-10, 0, 2, 4, 0),
    (6.62154879e-10, 0, 0, 5, 0),
    (4.03863260e-13, 1, 0, 5, 0),
    (1.95087203e-12, 0, 1, 5, 0),
    (-4.73602469e-12, 0, 0, 6, 0),
    (5.12733497e+00, 0, 0, 0, 1),
    (-3.12788561e-01, 1, 0, 0, 1),
    (-1.96701861e-02, 2, 0, 0, 1),
    (9.99690870e-04, 3, 0, 0, 1),
    (9.51738512e-06, 4, 0, 0, 1),
    (-4.66426341e-07, 5, 0, 0, 1),
    (5.48050612e-01, 0, 1, 0, 1),
    (-3.30552823e-03, 1, 1, 0, 1),
    (-1.64119440e-03, 2, 1, 0, 1),
    (-5.16670694e-06, 3, 1, 0, 1),
    (9.52692432e-07, 4, 1, 0, 1),
    (-4.29223622e-02, 0, 2, 0, 1),
    (5.00845667e-03, 1, 2, 0, 1),
    (1.00601257e-06, 2, 2, 0, 1),
    (-1.81748644e-06, 3, 2, 0, 1),
    (-1.25813502e-03, 0, 3, 0, 1),
    (-1.79330391e-04, 1, 3, 0, 1),
    (2.34994441e-06, 2, 3, 0, 1),
    (1.29735808e-04, 0, 4, 0, 1),
    (1.29064870e-06, 1, 4, 0, 1),
    (-2.28558686e-06, 0, 5, 0, 1),
    (-3.69476348e-02, 0, 0, 1, 1),
    (1.62325322e-03, 1, 0, 1, 1),
    (-3.14279680e-05, 2, 0, 1, 1),
    (2.59835559e-06, 3, 0, 1, 1),
    (-4.77136523e-08, 4, 0, 1, 1),
    (8.64203390e-03, 0, 1, 1, 1),
    (-6.87405181e-04, 1, 1, 1, 1),
    (-9.13863872e-06, 2, 1, 1, 1),
    (5.15916806e-07, 3, 1, 1, 1),
    (-3.59217476e-05, 0, 2, 1, 1),
    (3.28696511e-05, 1, 2, 1, 1),
    (-7.10542454e-07, 2, 2, 1, 1),
    (-1.24382300e-05, 0, 3, 1, 1),
    (-7.38584400e-09, 1, 3, 1, 1),
    (2.20609296e-07, 0, 4, 1, 1),
    (-7.32469180e-04, 0, 0, 2, 1),
    (-1.87381964e-05, 1, 0, 2, 1),
    (4.80925239e-06, 2, 0, 2, 1),
    (-8.75492040e-08, 3, 0, 2, 1),
    (2.77862930e-05, 0, 1, 2, 1),
    (-5.06004592e-06, 1, 1, 2, 1),
    (1.14325367e-07, 2, 1, 2, 1),
    (2.53016723e-06, 0, 2, 2, 1),
    (-1.72857035e-08, 1, 2, 2, 1),
    (-3.95079398e-08, 0, 3, 2, 1),
    (-3.59413173e-07, 0, 0, 3, 1),
    (7.04388046e-07, 1, 0, 3, 1),
    (-1.89309167e-08, 2, 0, 3, 1),
    (-4.79768731e-07, 0, 1, 3, 1),
    (7.96079978e-09, 1, 1, 3, 1),
    (1.62897058e-09, 0, 2, 3, 1),
    (3.94367674e-08, 0, 0, 4, 1),
    (-1.18566247e-09, 1, 0, 4, 1),
    (3.34678041e-10, 0, 1, 4, 1),
    (-1.15606447e-10, 0, 0, 5, 1),
    (-2.80626406e+00, 0, 0, 0, 2),
    (5.48712484e-01, 1, 0, 0, 2),
    (-3.99428410e-03, 2, 0, 0, 2),
    (-9.54009191e-04, 3, 0, 0, 2),
    (1.93090978e-05, 4, 0, 0, 2),
    (-3.08806365e-01, 0, 1, 0, 2),
    (1.16952364e-02, 1, 1, 0, 2),
    (4.95271903e-04, 2, 1, 0, 2),
    (-1.90710882e-05, 3, 1, 0, 2),
    (2.10787756e-03, 0, 2, 0, 2),
    (-6.98445738e-04, 1, 2, 0, 2),
    (2.30109073e-05, 2, 2, 0, 2),
    (4.17856590e-04, 0, 3, 0, 2),
    (-1.27043871e-05, 1, 3, 0, 2),
    (-3.04620472e-06, 0, 4, 0, 2),
    (5.14507424e-02, 0, 0, 1, 2),
    (-4.32510997e-03, 1, 0, 1, 2),
    (8.99281156e-05, 2, 0, 1, 2),
    (-7.14663943e-07, 3, 0, 1, 2),
    (-2.66016305e-04, 0, 1, 1, 2),
    (2.63789586e-04, 1, 1, 1, 2),
    (-7.01199003e-06, 2, 1, 1, 2),
    (-1.06823306e-04, 0, 2, 1, 2),
    (3.61341136e-06, 1, 2, 1, 2),
    (2.29748967e-07, 0, 3, 1, 2),
    (3.04788893e-04, 0, 0, 2, 2),
    (-6.42070836e-05, 1, 0, 2, 2),
    (1.16257971e-06, 2, 0, 2, 2),
    (7.68023384e-06, 0, 1, 2, 2),
    (-5.47446896e-07, 1, 1, 2, 2),
    (-3.59937910e-08, 0, 2, 2, 2),
    (-4.36497725e-06, 0, 0, 3, 2),
    (1.68737969e-07, 1, 0, 3, 2),
    (2.67489271e-08, 0, 1, 3, 2),
    (3.23926897e-09, 0, 0, 4, 2),
    (-3.53874123e-02, 0, 0, 0, 3),
    (-2.21201190e-01, 1, 0, 0, 3),
    (1.55126038e-02, 2, 0, 0, 3),
    (-2.63917279e-04, 3, 0, 0, 3),
    (4.53433455e-02, 0, 1, 0, 3),
    (-4.32943862e-03, 1, 1, 0, 3),
    (1.45389826e-04, 2, 1, 0, 3),
    (2.17508610e-04, 0, 2, 0, 3),
    (-6.66724702e-05, 1, 2, 0, 3),
    (3.33217140e-05, 0, 3, 0, 3),
    (-2.26921615e-03, 0, 0, 1, 3),
    (3.80261982e-04, 1, 0, 1, 3),
    (-5.45314314e-09, 2, 0, 1, 3),
    (-7.96355448e-04, 0, 1, 1, 3),
    (2.53458034e-05, 1, 1, 1, 3),
    (-6.31223658e-06, 0, 2, 1, 3),
    (3.02122035e-04, 0, 0, 2, 3),
    (-4.77403547e-06, 1, 0, 2, 3),
    (1.73825715e-06, 0, 1, 2, 3),
    (-4.09087898e-07, 0, 0, 3, 3),
    (6.14155345e-01, 0, 0, 0, 4),
    (-6.16755931e-02, 1, 0, 0, 4),
    (1.33374846e-03, 2, 0, 0, 4),
    (3.55375387e-03, 0, 1, 0, 4),
    (-5.13027851e-04, 1, 1, 0, 4),
    (1.02449757e-04, 0, 2, 0, 4),
    (-1.48526421e-03, 0, 0, 1, 4),
    (-4.11469183e-05, 1, 0, 1, 4),
    (-6.80434415e-06, 0, 1, 1, 4),
    (-9.77675906e-06, 0, 0, 2, 4),
    (8.82773108e-02, 0, 0, 0, 5),
    (-3.01859306e-03, 1, 0, 0, 5),
    (1.04452989e-03, 0, 1, 0, 5),
    (2.47090539e-04, 0, 0, 1, 5),
    (1.48348065e-03, 0, 0, 0, 6),
)

# Förgruppera termer per (p_d_tmrt, p_pa) för snabb, exakt vektoriserad evaluering.
def _gruppera_termer() -> tuple[tuple[int, int, tuple[tuple[float, int, int], ...]], ...]:
    grupper: dict[tuple[int, int], list[tuple[float, int, int]]] = {}
    for koeff, p_ta, p_va, p_dt, p_pa in UTCI_POLYNOM_TERMER:
        grupper.setdefault((p_dt, p_pa), []).append((koeff, p_ta, p_va))
    return tuple((p_dt, p_pa, tuple(lista)) for (p_dt, p_pa), lista in grupper.items())


_UTCI_GRUPPERADE_TERMER: tuple[tuple[int, int, tuple[tuple[float, int, int], ...]], ...] = (
    _gruppera_termer()
)


@dataclass(frozen=True)
class UtciResultat:
    """Resultat från deterministisk UTCI-beräkning (Bröde m.fl. 2012)."""

    utci_c: FloatRaster
    pa_kpa: FloatRaster
    klass_id: Int8Raster
    utanfor_giltighetsomrade: BoolRaster
    varningar: tuple[str, ...]
    metodstatus: str = "skiss"


def mattat_angtryck_hpa(t_luft_c: SkalarEllerArray) -> FloatRaster:
    """Mättat vattenångtryck över vatten i hPa enligt Hardy (1998) ITS-90 / UTCI_a002.f90.

    Cellvärden med `NaN` eller fysiskt ogiltig temperatur (`<= -273.15 °C`) returnerar `NaN`.
    """
    ta = np.asarray(t_luft_c, dtype=np.float64)
    tk = ta + _KELVIN_OFFSET_C
    giltig = np.isfinite(tk) & (tk > 0.0)
    tk_saker = np.where(giltig, tk, 293.15)

    ln_es_pa = _HARDY_G7 * np.log(tk_saker)
    for idx, coeff in enumerate(_HARDY_G):
        ln_es_pa = ln_es_pa + coeff * np.power(tk_saker, idx - 2)

    es_hpa = np.exp(ln_es_pa) * 0.01
    resultat = np.where(giltig, es_hpa, np.nan)
    return np.asarray(resultat, dtype=np.float64)


def angtryck_kpa(
    t_luft_c: SkalarEllerArray,
    relativ_fuktighet_pct: SkalarEllerArray,
) -> FloatRaster:
    """Partiellt vattenångtryck `pa_kpa` [kPa] ur lufttemperatur [°C] och relativ fuktighet [%]."""
    ta, rh = np.broadcast_arrays(
        np.asarray(t_luft_c, dtype=np.float64),
        np.asarray(relativ_fuktighet_pct, dtype=np.float64),
    )
    es_hpa = mattat_angtryck_hpa(ta)
    pa = np.where(np.isfinite(es_hpa) & np.isfinite(rh), es_hpa * (rh / 100.0) / 10.0, np.nan)
    return np.asarray(pa, dtype=np.float64)


@overload
def vind_fotgangarhojd_till_10m(
    vind_m_s: float,
    hojd_m: float = 1.5,
    skrovlighet_z0_m: float = 0.01,
) -> float: ...


@overload
def vind_fotgangarhojd_till_10m(
    vind_m_s: NDArray[np.floating[Any]] | NDArray[np.integer[Any]],
    hojd_m: float = 1.5,
    skrovlighet_z0_m: float = 0.01,
) -> FloatRaster: ...


def vind_fotgangarhojd_till_10m(
    vind_m_s: SkalarEllerArray,
    hojd_m: float = 1.5,
    skrovlighet_z0_m: float = 0.01,
) -> float | FloatRaster:
    """Skalar vindhastighet från fotgängarhöjd `z` till 10 m via logaritmisk vindprofil.

    Formel enligt Bröde m.fl. (2012):
        v_10 = v_z * ln(10 / z_0) / ln(z / z_0)

    Args:
        vind_m_s: Vindhastighet vid `hojd_m` [m/s] (skalär eller NumPy-array, >= 0; NaN bevaras).
        hojd_m: Mäthöjd över mark [m], standard `1.5` m (Bröde 2012 använder även `1.1` m).
        skrovlighet_z0_m: Aerodynamisk skrovlighetslängd `z_0` [m], standard `0.01` m (kort gräs).

    Returns:
        Vindhastighet på 10 m höjd [m/s] (float för skalär indata, float64-array annars).

    Raises:
        ValueError: Vid ogiltig `hojd_m`, `skrovlighet_z0_m`, negativ eller oändlig vindhastighet.
    """
    if not math.isfinite(skrovlighet_z0_m) or skrovlighet_z0_m <= 0.0:
        raise ValueError(
            f"skrovlighet_z0_m måste vara ändlig och > 0 m, fick {skrovlighet_z0_m}."
        )
    if not math.isfinite(hojd_m) or hojd_m <= skrovlighet_z0_m:
        raise ValueError(
            f"hojd_m måste vara ändlig och > skrovlighet_z0_m ({skrovlighet_z0_m} m), "
            f"fick {hojd_m}."
        )
    if skrovlighet_z0_m >= 10.0:
        raise ValueError(
            f"skrovlighet_z0_m måste vara < 10.0 m för 10 m-referensprofil, "
            f"fick {skrovlighet_z0_m}."
        )

    faktor = math.log(10.0 / skrovlighet_z0_m) / math.log(hojd_m / skrovlighet_z0_m)

    if isinstance(vind_m_s, int | float) and not isinstance(vind_m_s, bool):
        v_f = float(vind_m_s)
        if math.isnan(v_f):
            return math.nan
        if not math.isfinite(v_f):
            raise ValueError(f"vind_m_s måste vara ändlig, fick {vind_m_s}.")
        if v_f < 0.0:
            raise ValueError(f"vind_m_s får inte vara negativ, fick {vind_m_s}.")
        return v_f * faktor

    arr = np.asarray(vind_m_s, dtype=np.float64)
    if np.any(np.isinf(arr)):
        raise ValueError("vind_m_s innehåller oändliga värden (+/-inf).")
    andliga = arr[np.isfinite(arr)]
    if andliga.size > 0 and float(np.min(andliga)) < 0.0:
        raise ValueError(
            f"vind_m_s får inte innehålla negativa värden (min={float(np.min(andliga)):.4g})."
        )
    return np.asarray(arr * faktor, dtype=np.float64)


def klassificera_utci(utci_c: SkalarEllerArray) -> Int8Raster:
    """Klassificerar UTCI [°C] i Bröde m.fl. (2012) Tabell 1:s 10-gradiga stresskala (`-5..+4`).

    Gränser:
        -5: utci < -40.0       (Extrem köldstress)
        -4: -40.0 <= utci < -27.0
        -3: -27.0 <= utci < -13.0
        -2: -13.0 <= utci < 0.0
        -1: 0.0 <= utci < 9.0
         0: 9.0 <= utci <= 26.0 (Ingen termisk stress)
        +1: 26.0 < utci <= 32.0
        +2: 32.0 < utci <= 38.0
        +3: 38.0 < utci <= 46.0
        +4: utci > 46.0         (Extrem värmestress)

    Cellvärden med `NaN` (eller icke-ändliga värden) får `UTCI_KLASS_OGILTIG` (`-99`).
    """
    u = np.asarray(utci_c, dtype=np.float64)
    klass = np.full(u.shape, UTCI_KLASS_OGILTIG, dtype=np.int8)
    giltig = np.isfinite(u)
    if not np.any(giltig):
        return klass

    ug = u[giltig]
    kg = np.zeros(ug.shape, dtype=np.int8)
    kg = np.where(ug < -40.0, np.int8(-5), kg)
    kg = np.where((ug >= -40.0) & (ug < -27.0), np.int8(-4), kg)
    kg = np.where((ug >= -27.0) & (ug < -13.0), np.int8(-3), kg)
    kg = np.where((ug >= -13.0) & (ug < 0.0), np.int8(-2), kg)
    kg = np.where((ug >= 0.0) & (ug < 9.0), np.int8(-1), kg)
    kg = np.where((ug >= 9.0) & (ug <= 26.0), np.int8(0), kg)
    kg = np.where((ug > 26.0) & (ug <= 32.0), np.int8(1), kg)
    kg = np.where((ug > 32.0) & (ug <= 38.0), np.int8(2), kg)
    kg = np.where((ug > 38.0) & (ug <= 46.0), np.int8(3), kg)
    kg = np.where(ug > 46.0, np.int8(4), kg)
    klass[giltig] = kg
    return klass


def stressklass_namn(klass_id: int, *, sprak: str = "sv") -> str:
    """Returnerar svenskt (`sprak='sv'`) eller engelskt (`sprak='en'`) namn för en UTCI-klass."""
    if sprak not in {"sv", "en"}:
        raise ValueError(f"Okänt språk '{sprak}', använd 'sv' eller 'en'.")
    if klass_id == UTCI_KLASS_OGILTIG:
        return "Ogiltig / saknas" if sprak == "sv" else "invalid / missing"
    if klass_id not in UTCI_STRESSKLASS_PER_ID:
        raise ValueError(f"Okänt UTCI klass_id: {klass_id} (giltiga: -5..+4 eller -99).")
    klass = UTCI_STRESSKLASS_PER_ID[klass_id]
    return klass.namn_sv if sprak == "sv" else klass.namn_en


def _evaluera_utci_polynom(
    ta: FloatRaster,
    va: FloatRaster,
    d_tmrt: FloatRaster,
    pa: FloatRaster,
) -> FloatRaster:
    """Evaluerar det exakta 210-termers 6:e-ordningens UTCI-polynomet i float64."""
    form = ta.shape
    ett = np.ones(form, dtype=np.float64)

    ta_p: list[FloatRaster] = [ett]
    va_p: list[FloatRaster] = [ett]
    dt_p: list[FloatRaster] = [ett]
    pa_p: list[FloatRaster] = [ett]
    for _ in range(6):
        ta_p.append(ta_p[-1] * ta)
        va_p.append(va_p[-1] * va)
        dt_p.append(dt_p[-1] * d_tmrt)
        pa_p.append(pa_p[-1] * pa)

    utci = ta.copy()
    for p_dt, p_pa, inre_termer in _UTCI_GRUPPERADE_TERMER:
        inre = np.zeros(form, dtype=np.float64)
        for koeff, p_ta, p_va in inre_termer:
            if p_ta == 0 and p_va == 0:
                inre = inre + koeff
            elif p_va == 0:
                inre = inre + koeff * ta_p[p_ta]
            elif p_ta == 0:
                inre = inre + koeff * va_p[p_va]
            else:
                inre = inre + koeff * ta_p[p_ta] * va_p[p_va]
        if p_dt == 0 and p_pa == 0:
            utci = utci + inre
        elif p_pa == 0:
            utci = utci + inre * dt_p[p_dt]
        elif p_dt == 0:
            utci = utci + inre * pa_p[p_pa]
        else:
            utci = utci + inre * dt_p[p_dt] * pa_p[p_pa]
    return np.asarray(utci, dtype=np.float64)


def berakna_utci(
    t_luft_c: SkalarEllerArray,
    t_mrt_c: SkalarEllerArray,
    vind_10m_m_s: SkalarEllerArray,
    relativ_fuktighet_pct: SkalarEllerArray,
    *,
    tillat_extrapolering: bool = False,
) -> UtciResultat:
    """Beräknar Universal Thermal Climate Index (UTCI) [°C] vektoriserat i float64.

    Stöder skalärer, 1D-vektorer och 2D-raster (samt blandade former via NumPy-broadcasting).
    Kontrollerar Bröde m.fl. (2012) giltighetsgränser utan tyst avklippning:
      - `t_luft_c` i `[-50, 50]` °C
      - `t_mrt_c - t_luft_c` i `[-30, 70]` °C
      - `vind_10m_m_s` i `[0.5, 17.0]` m/s
      - `relativ_fuktighet_pct` i `[5, 100]` %
      - `pa_kpa` i `[0, 5.0]` kPa (motsvarar max 50 hPa i `UTCI_a002.f90`)
      - samt ändliga värden (`+/-inf` flaggas alltid).

    Celler med `NaN` bevaras exakt som `NaN` i utdata utan att flaggas som intervallbrott.
    Celler utanför Brödes giltighetsområde flaggas alltid i `utanfor_giltighetsomrade`
    med tydliga meddelanden i `varningar`. När `tillat_extrapolering=False` (standard)
    sätts utom-intervall-celler till `NaN` för att förhindra opålitlig polynomextrapolering
    (t.ex. -326 °C vid 50 °C / 100 % RH); med `tillat_extrapolering=True` evalueras det
    oklämda polynomet för ändliga celler med `t_luft_c > -273.15 °C`, fortfarande flaggat.

    Returns:
        `UtciResultat` med `utci_c`, `pa_kpa`, `klass_id`, `utanfor_giltighetsomrade` och
        `varningar`.
    """
    ta, tmrt, va, rh = np.broadcast_arrays(
        np.asarray(t_luft_c, dtype=np.float64),
        np.asarray(t_mrt_c, dtype=np.float64),
        np.asarray(vind_10m_m_s, dtype=np.float64),
        np.asarray(relativ_fuktighet_pct, dtype=np.float64),
    )

    nan_mask = np.isnan(ta) | np.isnan(tmrt) | np.isnan(va) | np.isnan(rh)
    inf_mask = (~nan_mask) & (np.isinf(ta) | np.isinf(tmrt) | np.isinf(va) | np.isinf(rh))
    andlig_mask = (~nan_mask) & (~inf_mask)

    pa_kpa = np.full(ta.shape, np.nan, dtype=np.float64)
    kan_berakna_pa = andlig_mask & (ta > -_KELVIN_OFFSET_C)
    if np.any(kan_berakna_pa):
        pa_kpa[kan_berakna_pa] = angtryck_kpa(ta[kan_berakna_pa], rh[kan_berakna_pa])

    d_tmrt = np.where(andlig_mask, tmrt - ta, np.nan)

    varningar: list[str] = []

    if np.any(inf_mask):
        n_inf = int(np.sum(inf_mask))
        varningar.append(
            f"Icke-ändliga indatavärden (+/-inf) upptäcktes i {n_inf} cell(er) "
            "och flaggas utanför giltighetsområdet."
        )

    fel_ta = andlig_mask & ((ta < UTCI_TA_MIN_C) | (ta > UTCI_TA_MAX_C))
    if np.any(fel_ta):
        n = int(np.sum(fel_ta))
        varningar.append(
            f"t_luft_c utanför Bröde (2012) giltighetsintervall "
            f"[{UTCI_TA_MIN_C:.0f}, {UTCI_TA_MAX_C:.0f}] °C i {n} cell(er)."
        )

    fel_dt = andlig_mask & ((d_tmrt < UTCI_D_TMRT_MIN_C) | (d_tmrt > UTCI_D_TMRT_MAX_C))
    if np.any(fel_dt):
        n = int(np.sum(fel_dt))
        varningar.append(
            f"t_mrt_c - t_luft_c utanför Bröde (2012) giltighetsintervall "
            f"[{UTCI_D_TMRT_MIN_C:.0f}, {UTCI_D_TMRT_MAX_C:.0f}] °C i {n} cell(er)."
        )

    fel_va = andlig_mask & ((va < UTCI_VIND_10M_MIN_M_S) | (va > UTCI_VIND_10M_MAX_M_S))
    if np.any(fel_va):
        n = int(np.sum(fel_va))
        varningar.append(
            f"vind_10m_m_s utanför Bröde (2012) giltighetsintervall "
            f"[{UTCI_VIND_10M_MIN_M_S:.1f}, {UTCI_VIND_10M_MAX_M_S:.1f}] m/s i {n} cell(er)."
        )

    fel_rh = andlig_mask & ((rh < UTCI_RH_MIN_PCT) | (rh > UTCI_RH_MAX_PCT))
    if np.any(fel_rh):
        n = int(np.sum(fel_rh))
        varningar.append(
            f"relativ_fuktighet_pct utanför Bröde (2012) giltighetsintervall "
            f"[{UTCI_RH_MIN_PCT:.0f}, {UTCI_RH_MAX_PCT:.0f}] % i {n} cell(er)."
        )

    fel_pa = kan_berakna_pa & ((pa_kpa < UTCI_PA_MIN_KPA) | (pa_kpa > UTCI_PA_MAX_KPA))
    if np.any(fel_pa):
        n = int(np.sum(fel_pa))
        varningar.append(
            f"Ångtryck pa_kpa utanför UTCI_a002 giltighetsintervall "
            f"[{UTCI_PA_MIN_KPA:.1f}, {UTCI_PA_MAX_KPA:.1f}] kPa i {n} cell(er)."
        )

    utanfor = np.asarray(
        inf_mask | fel_ta | fel_dt | fel_va | fel_rh | fel_pa,
        dtype=np.bool_,
    )

    eval_mask = kan_berakna_pa if tillat_extrapolering else (andlig_mask & (~utanfor))

    utci_c = np.full(ta.shape, np.nan, dtype=np.float64)
    if np.any(eval_mask):
        utci_c[eval_mask] = _evaluera_utci_polynom(
            ta[eval_mask],
            va[eval_mask],
            d_tmrt[eval_mask],
            pa_kpa[eval_mask],
        )

    klass_id = klassificera_utci(utci_c)
    return UtciResultat(
        utci_c=utci_c,
        pa_kpa=pa_kpa,
        klass_id=klass_id,
        utanfor_giltighetsomrade=utanfor,
        varningar=tuple(varningar),
    )
