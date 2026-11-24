# ruff: noqa: E501
"""Referensflödet för rapporter som skapas i webben (utan AI): påståenden ur körningens egna scenarier.

`rapport.referensflode` (skyfall) och `rapport.referensflode_dagsljus` är skrivna för demokvarteret
(scenarierna `fore`/`efter`, jämförelsen `skyfall_max_djup_efter_minus_fore`, mottagaren gården). Här läses analys,
före-scenario, efter-scenario och jämförelsens namn ur körningen, så att samma påståenden gäller för ett projekt
i en geografi (scenarierna `bas` och projektets egna). Texterna och beläggsformen är oförändrade; ingen modell
används och inget tal skrivs för hand (talen är platshållare som Floden fyller).

Ämnen som saknar rapportmall (`AMNEN_MED_MALL`) kan inte skapas här; `saknad_mall` förklarar varför.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from rapport.referensflode import JFR, gardens_mottagare, pastaenden
from rapport.referensflode_dagsljus import kontekst_fran_rapport
from rapport.referensflode_dagsljus import pastaenden as pastaenden_dagsljus
from rapport.skissrapport import AMNEN_NIVA1

if TYPE_CHECKING:
    from rapport.skissrapport import Rapport, RapportTjanst

AMNEN_MED_MALL: Final[tuple[str, ...]] = ("skyfall", "dagsljus")
"""Ämnen som webbflödet har påståenden för (R1 och R2). Ekologi har en R2-mall via MCP men inga påståenden i webbflödet än."""

AMNESNAMN: Final[dict[str, str]] = {"skyfall": "Skyfall", "dagsljus": "Dagsljus och sol", "ekologi": "Ekologi"}


def saknad_mall(amne: str) -> str | None:
    """Text som förklarar varför ämnet inte kan bli en rapport än, annars None."""
    if amne not in AMNESNAMN:
        return f"Okänt ämne '{amne}'."
    if amne not in AMNEN_NIVA1:
        return (
            f"Rapportmall (R2) för {AMNESNAMN[amne].lower()} saknas än: rapporten kan inte skapas förrän mallen "
            "finns. Beräkningsresultaten finns i steg 4."
        )
    if amne not in AMNEN_MED_MALL:
        return (
            f"Webbflödet har inga påståenden för {AMNESNAMN[amne].lower()} än. Skissunderlaget (R2) kan "
            "skapas via MCP (rapport_skapa)."
        )
    return None


def skyfall_pastaenden(r: Rapport, *, demo: bool, regn_ur_idf: bool = True) -> list[Mapping[str, Any]]:
    k = r.korningar[0]
    analys = next((a["analys"] for a in k["analyser"] if str(a["losare"]).startswith("vatten.")), None)
    jf = next((j for j in k["jamforelser"] if j["analys"] == analys and j["utdata"] == "max_djup"), None)
    if analys is None or jf is None:
        raise ValueError(
            "Körningen har ingen skyfallsanalys med jämförelse av maxdjup före och efter (K20 [[jamforelser]])."
        )
    fore, efter, namn = str(jf["fore"]), str(jf["efter"]), str(jf["namn"])

    def byt(t: str) -> str:
        return (
            t.replace(f"matvarde.{analys}.fore.", f"matvarde.{analys}.{fore}.")
            .replace(f"matvarde.{analys}.efter.", f"matvarde.{analys}.{efter}.")
            .replace(f".{analys}.efter.", f".{analys}.{efter}.")
            .replace(JFR, namn)
        )

    ut: list[Mapping[str, Any]] = []
    for p in pastaenden(analys, r.installningar.sprakprofil):
        d = dict(p)
        if d["fraga_id"] == "SK-07" and not demo:
            # Mottagare (platser som påverkas) anges inte i webbflödet: säg det i stället för att gissa.
            d = {
                "fraga_id": "SK-07",
                "status": "underlag_saknas",
                "fordjupning": "kan_inte_avgoras",
                "text": "Platser som påverkas (mottagare) är inte angivna för projektet, så skadeverkan på enskilda platser bedöms inte.",
                "saknat_underlag": ["Mottagare: platser som påverkas, som polygoner i EPSG:3011."],
            }
        elif d["fraga_id"] == "SK-02" and not regn_ur_idf:
            # Projektets körning har ett fast regndjup (`regn_mm`), inte en IDF-källa: regnet finns då inte som
            # mätvärden alls, så ingen siffra påstås.
            d = {
                "fraga_id": "SK-02",
                "status": "underlag_saknas",
                "fordjupning": "kan_inte_avgoras",
                "text": "Körningen använder ett fast regndjup utan återkomsttid och varaktighet som mätvärden, så regnet "
                "kan inte redovisas här. Vilket regn och vilken säkerhetsnivå som ska gälla behöver bestämmas i planarbetet.",
                "saknat_underlag": ["Dimensionerande regn (återkomsttid, varaktighet, klimatfaktor) för projektet."],
            }
        else:
            d["text"] = byt(str(d["text"]))
            if "belagg" in d:
                d["belagg"] = [byt(b) for b in d["belagg"]]
        ut.append(d)
    return ut


def mottagare_for(demo: bool) -> list[dict[str, Any]]:
    return [gardens_mottagare()] if demo else []


def _regn_ur_idf(rt: RapportTjanst, r: Rapport) -> bool:
    """Har körningens efter-scenario regnets återkomsttid som mätvärde (IDF-källa), inte bara ett fast regndjup?"""
    k = r.korningar[0]
    analys = next((a["analys"] for a in k["analyser"] if str(a["losare"]).startswith("vatten.")), None)
    jf = next((j for j in k["jamforelser"] if j["analys"] == analys and j["utdata"] == "max_djup"), None)
    if analys is None or jf is None:
        return True
    try:
        svar = rt.tjanst.resultat_sammanfattning(
            str(k["korning_id"]), analys=analys, scenario=str(jf["efter"]), ruta=None, max_rader=500
        )
    except Exception:  # noqa: BLE001 - beläggskontrollen ger det riktiga felet
        return True
    rader = [x for t in svar["matvarden"] for x in t["rader"]]
    return bool(rader) and "aterkomsttid_ar" in rader[0]["varden"]


def pastaenden_for(rt: RapportTjanst, r: Rapport) -> list[Mapping[str, Any]]:
    """Påståendena för rapportens ämne och frågor. Kastar `ValueError` om en fråga saknar påstående."""
    demo = r.projekt == "demokvarteret"
    if r.amne == "skyfall":
        alla = skyfall_pastaenden(r, demo=demo, regn_ur_idf=_regn_ur_idf(rt, r))
    elif r.amne == "dagsljus":
        alla = pastaenden_dagsljus(kontekst_fran_rapport(rt, r), r.fragor)
    else:
        raise ValueError(saknad_mall(r.amne) or "Ämnet saknar påståenden.")
    valda = [p for p in alla if p["fraga_id"] in set(r.fragor)]
    saknas: Sequence[str] = [f for f in r.fragor if f not in {p["fraga_id"] for p in valda}]
    if saknas:
        raise ValueError(f"Webbflödet har inga påståenden för frågorna {', '.join(saknas)}.")
    return valda
