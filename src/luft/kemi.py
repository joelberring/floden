# SPDX-License-Identifier: MIT
"""Fotostationär NO₂-kemi (`docs/LUFT.md` 2.4).

Jämvikten ``NO + O₃ ⇄ NO₂`` med bevarade summor (i ppb)::

    NOx = NO + NO₂,   Ox = NO₂ + O₃,   J·[NO₂] = k·[NO]·[O₃]

ger med x = [NO₂] andragradsekvationen ``x² − B x + C = 0`` där
``B = NOx + Ox + J/k`` och ``C = NOx·Ox``. Den fysikaliska roten (0 ≤ x ≤
min(NOx, Ox)) är ``x = (B − √(B² − 4C))/2``, som beräknas i den numeriskt
stabila formen ``x = 2C / (B + √(B² − 4C))`` med diskriminanten skriven
``(NOx − Ox)² + r(2(NOx + Ox) + r)``, r = J/k (inga kancellationer).

Enheter
-------
* Halter in och ut i µg/m³. Omräkning till ppb (volymblandningsförhållande)
  vid temperaturen ``temperatur_k`` och trycket 101 325 Pa:
  ``ppb = µg/m³ · R·T / (M·p) · 10³``.
* **NOx räknas som NO₂-ekvivalenter** i µg/m³ (som i svensk/europeisk
  rapportering): ``nox_lokalt_ug_m3`` omräknas till ppb med NO₂:s molmassa.
  NO (in och ut) räknas med sin egen molmassa.
* Molmassor (g/mol): NO₂ 46,0055, NO 30,006, O₃ 47,997.

Bakgrund
--------
Bakgrundens NO anges med ``no_bakgrund_ug_m3``. Om den utelämnas antas
bakgrunden vara i fotostationär jämvikt: ``NO_b = J·NO₂_b / (k·O₃_b)`` (0 där
O₃_b = 0). Då ger noll lokalt tillskott exakt bakgrundens NO₂.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from luft.typer import FloatArray, LuftFel

R_J_MOL_K = 8.314462618
TRYCK_PA = 101325.0
MOLMASSA_NO2_G_MOL = 46.0055
MOLMASSA_NO_G_MOL = 30.006
MOLMASSA_O3_G_MOL = 47.997

ArrayLike = npt.ArrayLike


def ppb_per_ug_m3(molmassa_g_mol: float, temperatur_k: Any) -> FloatArray:
    """Omräkningsfaktor µg/m³ → ppb vid `temperatur_k` och 101 325 Pa."""
    t = np.asarray(temperatur_k, dtype=np.float64)
    f: FloatArray = R_J_MOL_K * t / (molmassa_g_mol * TRYCK_PA) * 1e3
    return f


def _kontrollera(namn: str, a: FloatArray, positiv: bool = False) -> None:
    if not np.all(np.isfinite(a)):
        raise LuftFel(f"{namn} innehåller NaN/Inf.")
    if positiv and np.any(a <= 0):
        raise LuftFel(f"{namn} måste vara positiv.")
    if np.any(a < 0):
        raise LuftFel(f"{namn} får inte vara negativ.")


def no2_fotostationar(
    nox_lokalt_ug_m3: ArrayLike,
    no2_bakgrund_ug_m3: ArrayLike,
    o3_bakgrund_ug_m3: ArrayLike,
    direkt_no2_andel: ArrayLike,
    j_no2_1_s: ArrayLike,
    k_ppb_1_s: ArrayLike,
    temperatur_k: ArrayLike = 288.15,
    no_bakgrund_ug_m3: ArrayLike | None = None,
) -> FloatArray:
    """Total NO₂-halt (µg/m³) i fotostationär jämvikt.

    nox_lokalt_ug_m3: lokalt NOx-tillskott från modellen, som NO₂-ekvivalenter.
    no2_bakgrund_ug_m3, o3_bakgrund_ug_m3: bakgrundshalter.
    direkt_no2_andel: andel av det lokala NOx som släpps ut som NO₂ (0–1).
    j_no2_1_s: fotolyshastighet J för NO₂ (≥ 0; 0 = natt).
    k_ppb_1_s: hastighetskonstant för NO + O₃ (ppb⁻¹ s⁻¹, > 0).
    temperatur_k: lufttemperatur för omräkning µg/m³ ↔ ppb.
    no_bakgrund_ug_m3: bakgrundens NO; None = jämvikt (se modultexten).

    Alla argument broadcastas mot varandra (numpy). Returnerar NO₂ inklusive
    bakgrund, i µg/m³. Negativa eller icke-ändliga indata ger LuftFel.
    """
    nox = np.asarray(nox_lokalt_ug_m3, dtype=np.float64)
    no2_b = np.asarray(no2_bakgrund_ug_m3, dtype=np.float64)
    o3_b = np.asarray(o3_bakgrund_ug_m3, dtype=np.float64)
    andel = np.asarray(direkt_no2_andel, dtype=np.float64)
    j = np.asarray(j_no2_1_s, dtype=np.float64)
    k = np.asarray(k_ppb_1_s, dtype=np.float64)
    t = np.asarray(temperatur_k, dtype=np.float64)
    _kontrollera("nox_lokalt_ug_m3", nox)
    _kontrollera("no2_bakgrund_ug_m3", no2_b)
    _kontrollera("o3_bakgrund_ug_m3", o3_b)
    _kontrollera("direkt_no2_andel", andel)
    if np.any(andel > 1):
        raise LuftFel("direkt_no2_andel måste ligga i [0, 1].")
    _kontrollera("j_no2_1_s", j)
    _kontrollera("k_ppb_1_s", k, positiv=True)
    _kontrollera("temperatur_k", t, positiv=True)

    f_no2 = ppb_per_ug_m3(MOLMASSA_NO2_G_MOL, t)
    f_no = ppb_per_ug_m3(MOLMASSA_NO_G_MOL, t)
    f_o3 = ppb_per_ug_m3(MOLMASSA_O3_G_MOL, t)
    nox_ppb = nox * f_no2
    no2_b_ppb = no2_b * f_no2
    o3_b_ppb = o3_b * f_o3
    r = j / k
    if no_bakgrund_ug_m3 is None:
        no_b_ppb = np.divide(
            r * no2_b_ppb, o3_b_ppb, out=np.zeros(np.broadcast(r, no2_b_ppb, o3_b_ppb).shape),
            where=o3_b_ppb > 0,
        )
    else:
        no_b = np.asarray(no_bakgrund_ug_m3, dtype=np.float64)
        _kontrollera("no_bakgrund_ug_m3", no_b)
        no_b_ppb = no_b * f_no

    nox_tot = nox_ppb + no2_b_ppb + no_b_ppb
    ox_tot = andel * nox_ppb + no2_b_ppb + o3_b_ppb
    nox_tot, ox_tot, r = np.broadcast_arrays(nox_tot, ox_tot, r)
    b = nox_tot + ox_tot + r
    disk = (nox_tot - ox_tot) ** 2 + r * (2.0 * (nox_tot + ox_tot) + r)
    namnare = b + np.sqrt(disk)
    x_ppb = np.divide(
        2.0 * nox_tot * ox_tot, namnare, out=np.zeros_like(namnare), where=namnare > 0
    )
    no2: FloatArray = x_ppb / np.broadcast_to(f_no2, x_ppb.shape)
    return no2
