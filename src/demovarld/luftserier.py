# ruff: noqa: E501
"""Påhittade timserier för luftdemon: lokalt PM10-bidrag före och efter ett förslag (R2).

Serierna är **konstruerade, inte beräknade**: Floden räknar inte fram något haltbidrag i luftdemon
(`luft.mkn` tar en given timserie från ett annat verktyg). De är gjorda så att svaret är känt och
så att före- och efterläget visar de tre fall som rapporten ska kunna skilja på:

- före: intervallet för antal dygn över dygnsgränsen ligger helt under högsta antalet (6 till 20 dygn);
- efter: intervallet korsar högsta antalet (21 till 46 dygn), så underlaget varken kan säga att normen
  klaras eller att den överskrids.

Dygnsmedlet av lokalt bidrag är exakt det värde som konstruktionen anger (timprofilen har medelvärde 1),
och värdena ligger långt från tröskeln för dygnsgränsen (bakgrund 12 µg/m³, faktorintervall 0,8 till 1,25),
så antalen är stabila. Allt är heltalsaritmetik och sinus: samma fil varje gång.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Final

DAGAR: Final[int] = 365
BAKGRUND_UG_M3: Final[float] = 12.0
"""Påhittad bakgrund (konstant). I ett projekt är bakgrunden en källbelagd siffra som frågan anger."""
LAGEN: Final[dict[str, tuple[int, int]]] = {"fore": (6, 20), "efter": (21, 46)}
"""Läge -> (antal dygn med 70 µg/m³ lokalt bidrag, antal dygn till och med dem med 40 µg/m³)."""
HOG_UG_M3: Final[float] = 70.0
MELLAN_UG_M3: Final[float] = 40.0


def dygnsmedel_ug_m3(lage: str) -> list[float]:
    """Dygnsmedel av lokalt haltbidrag (µg/m³) för läget `fore` eller `efter`."""
    n_hog, n_alla = LAGEN[lage]
    ut: list[float] = []
    for d in range(DAGAR):
        rang = (d * 53) % DAGAR  # 53 saknar gemensam delare med 365: en spridd, fast ordning
        bas = 10.0 + 5.0 * math.sin(2 * math.pi * (d - 60) / DAGAR)  # 5 till 15 µg/m³ (vår: mest damm)
        if rang < n_hog:
            ut.append(HOG_UG_M3)
        elif rang < n_alla:
            ut.append(MELLAN_UG_M3)
        else:
            ut.append(round(bas, 3))
    return ut


def timserie_csv(lage: str) -> str:
    """CSV `tid,lokal_ug_m3` med 24 timmar per dygn; timprofilen har medelvärde 1 över dygnet."""
    rader = ["tid,lokal_ug_m3"]
    for d, medel in enumerate(dygnsmedel_ug_m3(lage)):
        for h in range(24):
            profil = 1.0 + 0.5 * math.sin(2 * math.pi * (h - 8) / 24)
            rader.append(f"{d * 24 + h},{medel * profil:.4f}")
    return "\n".join(rader) + "\n"


def skriv_luftserier(varld_dir: Path) -> dict[str, Path]:
    """Skriver `luft/timserie_fore.csv` och `luft/timserie_efter.csv` (bara om innehållet ändrats)."""
    mapp = varld_dir / "luft"
    mapp.mkdir(parents=True, exist_ok=True)
    ut: dict[str, Path] = {}
    for lage in LAGEN:
        fil = mapp / f"timserie_{lage}.csv"
        text = timserie_csv(lage)
        if not fil.is_file() or fil.read_text(encoding="utf-8") != text:
            fil.write_text(text, encoding="utf-8")
        ut[lage] = fil
    return ut


__all__ = ["BAKGRUND_UG_M3", "LAGEN", "dygnsmedel_ug_m3", "skriv_luftserier", "timserie_csv"]
