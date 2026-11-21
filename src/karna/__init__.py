"""Flodens gemensamma kärna (systemdesign fas 0).

Delas av alla moduler: vatten, luft, buller och dagsljus.

- `karna.hashning` – kanonisk JSON och sha256-nycklar (samma kontrollsumma som K0).
- `karna.scenario` – planscenario som bas + diff (K9/v1), med förgreningar för alternativ.
- `karna.graf` – beräkningsgraf: resultat = f(indatanycklar, lösarversion, parametrar),
  cachat på nyckeln.
- `karna.paverkan` – påverkansradie för lokal omräkning.
- `karna.fraga` – utredningsfråga som deklarativ specifikation (K10/v1).
- `karna.korning` – kör en fråga mot ett eller två scenarier via grafen.

Körmotorn för utredningsfrågor (T20), byggd ovanpå samma scenarier:

- `karna.utredning` – körbar utredningsfråga i fil (K20/v1) med scenarier (K1-bas + K9/K3).
- `karna.analyslosare` – rutvisa lösare med påverkansradie (K21/v1), upptäcks via
  `<paket>/floden_losare.py`.
- `karna.rutscenario` / `karna.k9_till_k1` – K9/K3 tillämpat ruta för ruta på K1.
- `karna.korplan` – jobbnycklar per ruta inom radien, körning med cache (K22/v1).
- `karna.jamfor` – före/efter för K1-raster. CLI: `floden fraga {kor,plan,losare}`.

Kärnan räknar inget själv. En modul registrerar en `Losare` och får cache, spårbarhet och
jämförelse mellan alternativ gratis.
"""

from __future__ import annotations

# T20-körmotorn. Egna namn, så att inget ovan ändras.
from karna.analyslosare import Analyslosare, LosarKontext, LosarUtdata, registrera_losare
from karna.fraga import AnalysSteg, Utredningsfraga, las_fraga, registrera_analystyp, tolka_fraga
from karna.graf import Berakningsgraf, Cache, Losare, Nod, Resultat
from karna.hashning import kanonisk_json, nyckel_for
from karna.korning import FragaSvar, StegSvar, kor_fraga
from karna.korplan import kor_utredning, planera
from karna.paverkan import dagsljus_paverkansradie_m, ruta_inom_radie
from karna.resultatcache import ResultatCache
from karna.scenario import Andring, Planscenario, diff_scenarier, las_scenario, skriv_scenario
from karna.utredning import KorbarFraga
from karna.utredning import las_fraga as las_utredning

__all__ = [
    "Analyslosare",
    "KorbarFraga",
    "LosarKontext",
    "LosarUtdata",
    "ResultatCache",
    "kor_utredning",
    "las_utredning",
    "planera",
    "registrera_losare",
    "AnalysSteg",
    "Andring",
    "Berakningsgraf",
    "Cache",
    "FragaSvar",
    "Losare",
    "Nod",
    "Planscenario",
    "Resultat",
    "StegSvar",
    "Utredningsfraga",
    "dagsljus_paverkansradie_m",
    "diff_scenarier",
    "kanonisk_json",
    "kor_fraga",
    "las_fraga",
    "las_scenario",
    "nyckel_for",
    "registrera_analystyp",
    "ruta_inom_radie",
    "skriv_scenario",
    "tolka_fraga",
]
