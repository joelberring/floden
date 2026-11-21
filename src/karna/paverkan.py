"""Påverkansradie: vilka rutor behöver räknas om när ett scenario ändrar något.

- Dagsljus/sol: en volym med höjden H skuggar bara punkter inom `H / tan(h_min)`, där h_min är
  den lägsta horisontvinkel som spelar roll (systemdesign 2.3).
- Skyfall: påverkan följer avrinningen nedströms; det sköts av sänkgrafen
  (`lapp.lokal_omrakning.hitta_berorda_noder`), inte av en radie.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def dagsljus_paverkansradie_m(hojd_m: float, h_min_grader: float = 5.0) -> float:
    """Avstånd inom vilket en volym med höjden `hojd_m` kan ändra horisonten med minst h_min."""
    if not (math.isfinite(hojd_m) and hojd_m >= 0):
        raise ValueError(f"Höjden måste vara ett ändligt tal ≥ 0, fick {hojd_m}.")
    if not (0 < h_min_grader < 90):
        raise ValueError(f"h_min måste ligga mellan 0 och 90 grader, fick {h_min_grader}.")
    return hojd_m / math.tan(math.radians(h_min_grader))


def ruta_inom_radie(
    bbox: tuple[float, float, float, float],
    rutor: Iterable[tuple[str, tuple[float, float, float, float]]],
    radie_m: float,
) -> list[str]:
    """Rutor (id, bbox) inom `radie_m` från ändringens bbox (min_e, min_n, max_e, max_n)."""
    e0, n0, e1, n1 = bbox
    ut: list[str] = []
    for ruta_id, (re0, rn0, re1, rn1) in rutor:
        de = max(re0 - e1, e0 - re1, 0.0)
        dn = max(rn0 - n1, n0 - rn1, 0.0)
        if math.hypot(de, dn) <= radie_m:
            ut.append(ruta_id)
    return sorted(ut)
