"""Gemensam kärna för Floden (T20): utredningsfrågor, scenarier, lösarregister och cache.

Kärnan kopplar ihop domänerna (vatten, luft, buller, dagsljus) utan att känna till deras
fysik. En domän ansluter genom att exponera lösare enligt kontrakt K21 (se
`docs/ANSLUTA_LOSARE.md`). En utredningsfråga (K20) anger scenarier och analyser; kärnan
materialiserar scenarierna, räknar fram en innehållsnyckel per jobb och kör bara det som
inte redan finns i cachen (K22).
"""

from karna.fraga import Analys, Jamforelse, ScenarioSpec, Utredningsfraga, las_fraga
from karna.losare import (
    BeroendeResultat,
    Losare,
    LosarFel,
    LosarKontext,
    LosarUtdata,
    hamta_losare,
    lista_losare,
    registrera_losare,
)
from karna.scenario import Ruta, Scenario, materialisera_scenario

__all__ = [
    "Analys",
    "BeroendeResultat",
    "Jamforelse",
    "LosarFel",
    "LosarKontext",
    "LosarUtdata",
    "Losare",
    "Ruta",
    "Scenario",
    "ScenarioSpec",
    "Utredningsfraga",
    "hamta_losare",
    "las_fraga",
    "lista_losare",
    "materialisera_scenario",
    "registrera_losare",
]
