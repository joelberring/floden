"""Markens akustiska impedans ur flödesresistivitet: Delany–Bazleys enparametermodell.

Källa: M. E. Delany & E. N. Bazley, "Acoustical properties of fibrous absorbent materials",
Applied Acoustics 3 (1970) 105–116. Formen med flödesresistiviteten i kPa·s/m² som används
för mark utomhus återges bl.a. i E. M. Salomons, "Computational Atmospheric Acoustics"
(Kluwer 2001), och i T. F. W. Embleton, J. E. Piercy & G. A. Daigle, J. Acoust. Soc. Am.
74 (1983) 1239–1244.

Tidskonvention: exp(−iωt), samma som i `reflektion.py`. En utgående våg är exp(+ikR)/R
och en passiv, fjäderlik markyta har Im Z > 0. (Med konventionen exp(+jωt), som i
originalartikeln, byter imaginärdelen tecken.)

Modellen är empirisk. Delany och Bazley anger giltighet för ungefär
0,01 < ρ₀·f/σ < 1 (σ i Pa·s/m²); för hård mark och låga frekvenser används den alltså
utanför sitt uppmätta område, vilket är brukligt i utomhusakustik men bör hållas i minnet.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

Komplextal = NDArray[np.complex128]


def _kvot_f_sigma(
    frekvens_hz: ArrayLike, flodesresistivitet_kNsm4: ArrayLike
) -> NDArray[np.float64]:
    f = np.asarray(frekvens_hz, dtype=np.float64)
    sigma = np.asarray(flodesresistivitet_kNsm4, dtype=np.float64)
    if not bool(np.all(f > 0.0)):
        raise ValueError("Frekvensen ska vara större än 0 Hz.")
    if bool(np.any(np.isnan(sigma))) or not bool(np.all(sigma > 0.0)):
        raise ValueError("Flödesresistiviteten ska vara större än 0 (np.inf = akustiskt hård).")
    with np.errstate(divide="ignore"):
        kvot: NDArray[np.float64] = f / sigma  # 0 för sigma = inf
    return kvot


def delany_bazley_admittans(
    frekvens_hz: ArrayLike, flodesresistivitet_kNsm4: ArrayLike
) -> Komplextal:
    """Normaliserad ytadmittans β = ρ₀c/Z_mark = 1/Z (dimensionslös), konvention exp(−iωt).

    `flodesresistivitet_kNsm4 = np.inf` ger β = 0 (akustiskt hård yta). Indata broadcastas.
    """
    kvot = _kvot_f_sigma(frekvens_hz, flodesresistivitet_kNsm4)
    hard = kvot == 0.0
    saker_kvot = np.where(hard, 1.0, kvot)
    z = 1.0 + 9.08 * saker_kvot**-0.75 + 1j * 11.9 * saker_kvot**-0.73
    beta: Komplextal = np.where(hard, 0.0 + 0.0j, 1.0 / z)
    return beta


def delany_bazley_impedans(
    frekvens_hz: ArrayLike, flodesresistivitet_kNsm4: ArrayLike
) -> Komplextal:
    """Normaliserad ytimpedans Z = Z_mark/(ρ₀c) (dimensionslös), konvention exp(−iωt).

    `Z = 1 + 9,08·(f/σ)^(−0,75) + i·11,9·(f/σ)^(−0,73)` med f i Hz och σ i kPa·s/m²
    (= kN·s/m⁴). Lokalt reagerande, halvoändlig mark. `σ = np.inf` ger Z = ∞ + ∞·i; använd
    då hellre `delany_bazley_admittans`, som ger 0.
    """
    kvot = _kvot_f_sigma(frekvens_hz, flodesresistivitet_kNsm4)
    with np.errstate(divide="ignore"):
        z: Komplextal = 1.0 + 9.08 * kvot**-0.75 + 1j * 11.9 * kvot**-0.73
    return z
