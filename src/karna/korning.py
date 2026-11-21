"""Kör en utredningsfråga (K10) mot ett eller två planscenarier (K9) via beräkningsgrafen.

Varje analyssteg görs till en nod av sin moduls nodbyggare (`registrera_analystyp`). Har frågan
`jamfor_med` körs samma steg mot båda scenarierna; jämförelsen (B minus A) är modulens sak,
eftersom bara modulen vet hur dess resultat ska dras ifrån varandra. Kärnan levererar båda
resultaten och nycklarna, så att jämförelsen alltid är spårbar.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from karna.fraga import FragaFel, Utredningsfraga, nodbyggare_for
from karna.graf import Berakningsgraf, Resultat
from karna.scenario import Planscenario


@dataclass(frozen=True)
class StegSvar:
    typ: str
    modul: str
    resultat: Resultat
    jamfort_med: Resultat | None = None


@dataclass(frozen=True)
class FragaSvar:
    fraga: str
    nyckel: str
    steg: tuple[StegSvar, ...]


def kor_fraga(
    fraga: Utredningsfraga,
    scenarier: Mapping[str, Planscenario],
    graf: Berakningsgraf,
) -> FragaSvar:
    """Kör alla analyssteg. `scenarier` mappar frågans scenarionamn till scenarier."""
    def hamta(namn: str) -> Planscenario:
        if namn not in scenarier:
            raise FragaFel(f"Scenariot '{namn}' i frågan '{fraga.fraga}' finns inte.")
        return scenarier[namn]

    huvud = hamta(fraga.scenario)
    jamfor = hamta(fraga.jamfor_med) if fraga.jamfor_med is not None else None
    if jamfor is not None and jamfor.bas_recept != huvud.bas_recept:
        raise FragaFel("Scenarier med olika bas kan inte jämföras i samma fråga.")

    # Alla byggare hämtas innan något körs: en fråga som inte kan köras helt körs inte alls.
    byggare = [nodbyggare_for(steg.typ) for steg in fraga.analys]
    svar: list[StegSvar] = []
    for steg, bygg in zip(fraga.analys, byggare, strict=True):
        r = graf.kor(bygg(steg, huvud))
        rj = graf.kor(bygg(steg, jamfor)) if jamfor is not None else None
        svar.append(StegSvar(steg.typ, steg.modul, r, rj))

    nycklar = {fraga.scenario: huvud.nyckel}
    if jamfor is not None and fraga.jamfor_med is not None:
        nycklar[fraga.jamfor_med] = jamfor.nyckel
    return FragaSvar(fraga.fraga, fraga.nyckel(nycklar), tuple(svar))
