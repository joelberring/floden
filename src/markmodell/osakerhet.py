"""Fysikaliskt motiverad osäkerhetsmodell i höjd (standardavvikelse sigma_z_m)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


class OsakerhetFel(Exception):
    """Fel vid beräkning av markmodellens höjdosäkerhet."""


@dataclass(frozen=True)
class OsakerhetParametrar:
    """Parametrar för beräkning av cellvis höjdosäkerhet i meter."""

    grund_sigma_m: float = 0.05
    tathet_koeff_m: float = 0.04
    avstand_koeff_m_per_m: float = 0.03
    byggnad_tillagg_m: float = 0.20
    vegetation_tillagg_m: float = 0.08
    alder_koeff_m_per_ar: float = 0.005
    max_avstand_varning_m: float = 3.0


@dataclass(frozen=True)
class OsakerhetResultat:
    """Resultat från osäkerhetsberäkningen inklusive varningar till `run.json`."""

    sigma_z_m: NDArray[np.float32]
    antal_utan_markpunkt_inom_grans: int
    varningar: list[str]
    max_sigma_m: float
    medel_sigma_m: float


def berakna_osakerhet(
    punkt_tathet_per_m2: NDArray[np.float32] | NDArray[np.float64],
    avstand_till_punkt_m: NDArray[np.float32] | NDArray[np.float64],
    under_byggnad: NDArray[np.bool_],
    under_vegetation: NDArray[np.bool_],
    skanning_alder_ar: float = 0.0,
    parametrar: OsakerhetParametrar | None = None,
) -> OsakerhetResultat:
    """Beräknar uppskattad standardavvikelse `sigma_z_m` i höjd per cell (kvadratur).

    Komponenterna summeras i varians (kvadratur) enligt felpropagering:
    1. Grundosäkerhet från laserskanning (`grund_sigma_m`).
    2. Låg punkttäthet: `tathet_koeff_m / sqrt(max(punkt_tathet_per_m2, 0.25))`.
    3. Interpoleringsavstånd: `avstand_koeff_m_per_m * avstand_till_punkt_m`.
    4. Interpolering under byggnad eller tät vegetation.
    5. Skanningens ålder (`alder_koeff_m_per_ar * skanning_alder_ar`).

    Celler med `avstand_till_punkt_m > parametrar.max_avstand_varning_m` (eller NaN/Inf)
    räknas och genererar en tydlig varning enligt formatet i kontrakt K0/v1.
    """
    p = parametrar if parametrar is not None else OsakerhetParametrar()
    if not math.isfinite(skanning_alder_ar) or skanning_alder_ar < 0.0:
        raise OsakerhetFel(f"Ogiltig skanningsålder: {skanning_alder_ar} år.")

    form = punkt_tathet_per_m2.shape
    if (
        avstand_till_punkt_m.shape != form
        or under_byggnad.shape != form
        or under_vegetation.shape != form
    ):
        raise OsakerhetFel(
            "Alla inmatriser till osäkerhetsberäkningen måste ha identisk form."
        )

    tathet = np.asarray(punkt_tathet_per_m2, dtype=np.float64)
    avstand = np.asarray(avstand_till_punkt_m, dtype=np.float64)
    bygg_mask = np.asarray(under_byggnad, dtype=bool)
    veg_mask = np.asarray(under_vegetation, dtype=bool)

    if np.any(tathet < 0.0):
        raise OsakerhetFel("Punkttäthet får inte vara negativ.")
    if np.any(avstand < 0.0):
        raise OsakerhetFel("Avstånd till närmaste markpunkt får inte vara negativt.")

    effektiv_tathet = np.maximum(tathet, 0.25)
    effektiv_avstand = np.where(np.isfinite(avstand), avstand, p.max_avstand_varning_m + 1.0)

    varians = np.full(form, p.grund_sigma_m**2, dtype=np.float64)
    varians += (p.tathet_koeff_m / np.sqrt(effektiv_tathet)) ** 2
    varians += (p.avstand_koeff_m_per_m * effektiv_avstand) ** 2
    varians += np.where(bygg_mask, p.byggnad_tillagg_m**2, 0.0)
    varians += np.where(veg_mask, p.vegetation_tillagg_m**2, 0.0)
    varians += (p.alder_koeff_m_per_ar * skanning_alder_ar) ** 2

    sigma = np.sqrt(varians).astype(np.float32)

    utan_markpunkt = (~np.isfinite(avstand)) | (avstand > p.max_avstand_varning_m)
    antal_utan = int(np.count_nonzero(utan_markpunkt))

    varningar: list[str] = []
    if antal_utan > 0:
        grans_str = (
            f"{int(p.max_avstand_varning_m)}"
            if p.max_avstand_varning_m.is_integer()
            else f"{p.max_avstand_varning_m:g}"
        )
        varningar.append(
            f"{antal_utan} celler utan markpunkter inom {grans_str} m"
        )

    max_sigma = float(np.max(sigma)) if sigma.size > 0 else 0.0
    medel_sigma = float(np.mean(sigma)) if sigma.size > 0 else 0.0

    return OsakerhetResultat(
        sigma_z_m=sigma,
        antal_utan_markpunkt_inom_grans=antal_utan,
        varningar=varningar,
        max_sigma_m=max_sigma,
        medel_sigma_m=medel_sigma,
    )
