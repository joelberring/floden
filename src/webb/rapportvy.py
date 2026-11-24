"""Rapportens innehåll för förhandsvisningen i webben (Rapport-steget).

Läser en befintlig rapport (K33) ur arkivet och ger rubriker, påståenden och kontrollens punkter som
JSON. Inget räknas här: talen i texten är Flodens värden ur påståendets belägg (`fylld_text`), och
varje tal är en egen del (`tal: true`) så att klienten kan markera det. Skriver ingenting.
"""

from __future__ import annotations

from typing import Any

from rapport.fragekatalog import Fragekatalog, FragekatalogFel
from rapport.pastaende import (
    FORDJUPNING_TEXT,
    STATUS_TEXT,
    Pastaende,
    fyll_platshallare,
    las_platshallare,
)
from rapport.skissrapport import Rapport, RapportArkiv, metodnotis_text, sammanfattning


def _delar(p: Pastaende) -> list[dict[str, Any]]:
    """Påståendets text i delar: vanlig text och tal (platshållare ifyllda med Flodens värde)."""
    text = p.text
    per_adress = p.belagg_per_adress
    ut: list[dict[str, Any]] = []
    pos = 0
    for ph in las_platshallare(text):
        if ph.start > pos:
            ut.append({"text": text[pos : ph.start], "tal": False})
        ut.append({"text": fyll_platshallare(ph, per_adress[ph.adress]), "tal": True})
        pos = ph.slut
    if pos < len(text):
        ut.append({"text": text[pos:], "tal": False})
    return ut


def _fragetexter(r: Rapport) -> dict[str, str]:
    try:
        katalog = Fragekatalog.las_alla()
        return {f.id: f.fraga for f in katalog.fragor}
    except FragekatalogFel:
        return {}


def rapport_innehall(r: Rapport, arkiv: RapportArkiv) -> dict[str, Any]:
    """Rapporten som JSON för förhandsvisning: frågor i rapportens ordning med sina påståenden."""
    texter = _fragetexter(r)
    per_fraga: dict[str, list[Pastaende]] = {}
    for p in r.pastaenden:
        per_fraga.setdefault(p.fraga_id, []).append(p)
    fragor: list[dict[str, Any]] = []
    for fid in r.fragor:
        ps = per_fraga.get(fid, [])
        fragor.append(
            {
                "id": fid,
                "fraga": texter.get(fid, ""),
                "pastaenden": [
                    {
                        "pastaende_id": p.pastaende_id,
                        "status": p.status,
                        "status_text": STATUS_TEXT.get(p.status, p.status),
                        "fordjupning": p.fordjupning,
                        "fordjupning_text": FORDJUPNING_TEXT.get(p.fordjupning, p.fordjupning),
                        "delar": _delar(p),
                        "belagg": [b.adress for b in p.belagg],
                        "forutsattning": p.forutsattning,
                        "saknat_underlag": list(p.saknat_underlag),
                    }
                    for p in ps
                ],
            }
        )
    k = r.kontroll or {}
    return {
        **sammanfattning(r, arkiv),
        "skede": r.skede,
        "avsandare": r.installningar.avsandare,
        "fragor": fragor,
        "kontroll_punkter": [
            {"id": q["id"], "rubrik": q["rubrik"], "godkand": bool(q["godkand"]), "fel": q["fel"]}
            for q in k.get("punkter", [])
        ],
        "metodnotis": metodnotis_text(
            r.metodnotis,
            sbk=r.installningar.sprakprofil == "sbk",
            korningar=[str(k["korning_id"]) for k in r.korningar],
        ),
    }
