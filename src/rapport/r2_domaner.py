"""Rapporter R2: måttkataloger för alla fyra domäner samt deterministiska fält-/intervallbilagor.

Bygger deterministiska tabeller och måttdefinitioner ur Flodens beräkningsresultat
(`sankgraf.bifurkation`, `luft.kanslighet`, `buller.optimering`) utan påhittade siffror
och i svensk utredningsform (decimalkomma, smalt hårt mellanslag).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from buller.optimering import BullerSkarmVal
from luft.kanslighet import MknIntervallResultat
from sankgraf.bifurkation import BifurkationsPunkt

_NBSP = "\u202f"  # smalt hårt mellanslag för tusentalsavgränsning (svensk sifferform)


@dataclass(frozen=True)
class MattSpec:
    """Kontrakt för ett rapportmått i R2: domän, enhet och finaste tillåtna avrundning."""

    matt_id: str
    doman: str  # "skyfall" | "dagsljus" | "luft" | "buller"
    visningsnamn: str
    enhet: str
    min_steg: float
    beskrivning: str


MATTKATALOG_R2: dict[str, MattSpec] = {
    # Skyfall
    "skyfall.max_djup_m": MattSpec(
        "skyfall.max_djup_m", "skyfall", "Största vattendjup", "m", 0.01,
        "Maximalt vattendjup på mark (tak undantagna).",
    ),
    "skyfall.bifurkationer_antal": MattSpec(
        "skyfall.bifurkationer_antal", "skyfall", "Instabila överfallspunkter", "st", 1.0,
        "Antal sadlar där höjdosäkerheten kan ändra vattnets flödesväg.",
    ),
    "skyfall.andel_2d_yta_procent": MattSpec(
        "skyfall.andel_2d_yta_procent", "skyfall", "Yta som kräver 2D-hydraulik", "%", 1.0,
        "Andel aktiv markyta där plan vattenyta inte räcker (a posteriori-felindikator).",
    ),
    # Dagsljus / sol
    "dagsljus.soltimmar_mark_h": MattSpec(
        "dagsljus.soltimmar_mark_h", "dagsljus", "Soltimmar på mark", "h", 0.5,
        "Direkta soltimmar vid vårdagjämning på mark (tak undantagna).",
    ),
    "dagsljus.svf_andel": MattSpec(
        "dagsljus.svf_andel", "dagsljus", "Himmelsandel (SVF)", "andel", 0.01,
        "Synlig andel av himmelskupolen (0–1).",
    ),
    "dagsljus.vsc_procent": MattSpec(
        "dagsljus.vsc_procent", "dagsljus", "Vertikal himmelskomponent (VSC)", "%", 0.5,
        "BRE-standard för dagsljus på fasad (fri horisont = 39,6 %).",
    ),
    # Luft / MKN
    "luft.pm10_arsmedel_ug_m3": MattSpec(
        "luft.pm10_arsmedel_ug_m3", "luft", "PM10 årsmedelhalt", "µg/m³", 0.5,
        "Årsmedelhalt PM10 (MKN-normvärde 40 µg/m³).",
    ),
    "luft.pm10_dygn_over_50": MattSpec(
        "luft.pm10_dygn_over_50", "luft", "PM10 dygn över 50 µg/m³", "dygn/år", 1.0,
        "Antal dygn med dygnsmedel > 50 µg/m³ (MKN-gräns högst 35 dygn/år).",
    ),
    "luft.pm25_arsmedel_ug_m3": MattSpec(
        "luft.pm25_arsmedel_ug_m3", "luft", "PM2.5 årsmedelhalt", "µg/m³", 0.5,
        "Årsmedelhalt PM2.5 (MKN-normvärde 25 µg/m³).",
    ),
    # Buller
    "buller.ekvivalent_niva_dba": MattSpec(
        "buller.ekvivalent_niva_dba", "buller", "Ekvivalent ljudnivå vid fasad", "dBA", 0.5,
        "Beräknad ekvivalent ljudnivå enligt Nord2000.",
    ),
    "buller.antal_over_riktvarde": MattSpec(
        "buller.antal_over_riktvarde", "buller", "Fasadpunkter över riktvärde", "st", 1.0,
        "Antal bostadsfasader över valt riktvärde (t.ex. 55/60 dBA).",
    ),
}


def lista_matt_for_doman(doman: str) -> list[MattSpec]:
    """Returnerar alla R2-mått för en angiven fysikdomän i deterministisk ordning."""
    return [m for _, m in sorted(MATTKATALOG_R2.items()) if m.doman == doman]


def formatera_svenskt_tal(varde: float, decimaler: int = 1) -> str:
    """Formaterar flyttal enligt svensk utredningsnorm (smalt hårt mellanslag + decimalkomma)."""
    if not math.isfinite(varde):
        return "–"
    formaterat = f"{varde:,.{decimaler}f}"
    return formaterat.replace(",", _NBSP).replace(".", ",")


def bygg_faltmatningstabell(
    punkter: Sequence[BifurkationsPunkt],
    *,
    max_rader: int = 15,
) -> dict[str, Any]:
    """Bygger en deterministisk bilagetabell för fältinmätning av instabila överfallspunkter."""
    instabila = [p for p in punkter if p.instabil][: max(0, int(max_rader))]
    rader: list[dict[str, Any]] = []
    md_linjer = [
        (
            "| Nod | Överfall (E, N) | Överfallshöjd (m) | Marginal Δz (m) | "
            "Säkerhetsmarginal | Påverkad volym (m³) |"
        ),
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for p in instabila:
        sak_str = formatera_svenskt_tal(p.sakerhetsmarginal, 2)
        koord_str = f"{round(p.overfall_x)}, {round(p.overfall_y)}"
        rader.append(
            {
                "nod_id": p.nod_id,
                "overfall_e_m": round(p.overfall_x, 1),
                "overfall_n_m": round(p.overfall_y, 1),
                "overfall_hojd_m": round(p.overfall_hojd_m, 2),
                "marginal_m": round(p.marginal_m, 3),
                "sakerhetsmarginal": round(p.sakerhetsmarginal, 2)
                if math.isfinite(p.sakerhetsmarginal)
                else None,
                "paverkad_volym_m3": round(p.paverkad_volym_m3, 1),
            }
        )
        md_linjer.append(
            f"| {p.nod_id} | {koord_str} | {formatera_svenskt_tal(p.overfall_hojd_m, 2)} | "
            f"{formatera_svenskt_tal(p.marginal_m, 2)} | {sak_str} | "
            f"{formatera_svenskt_tal(p.paverkad_volym_m3, 0)} |"
        )
    if not instabila:
        md_linjer = [
            "Inga topologiskt instabila överfallspunkter identifierades inom angiven osäkerhet."
        ]
    return {
        "rubrik": "Överfallspunkter att mäta in i fält (bifurkationsanalys)",
        "antal_instabila": len(instabila),
        "rader": rader,
        "markdown": "\n".join(md_linjer),
    }


def bygg_mkn_intervallrad(
    platsnamn: str,
    iv: MknIntervallResultat,
) -> dict[str, Any]:
    """Sammanfattar ett monotont MKN-intervall i klarspråk för skissunderlag (R2)."""
    if iv.klarar_mkn_garanterat:
        status_text = "Klarar MKN-gränsen (≤ 35 dygn/år) i hela osäkerhetsintervallet."
    elif iv.bryter_mkn_garanterat:
        status_text = "Överskrider MKN-gränsen (> 35 dygn/år) i hela osäkerhetsintervallet."
    else:
        status_text = (
            "Ligger över MKN-gränsen (35 dygn/år) i delar av osäkerhetsintervallet – "
            "fördjupad utredning krävs."
        )
    return {
        "plats": platsnamn,
        "dygn_over_50_intervall": [iv.min_dygn_over_grans, iv.max_dygn_over_grans],
        "arsmedel_ug_m3_intervall": [
            round(iv.min_arsmedel_ug_m3, 1),
            round(iv.max_arsmedel_ug_m3, 1),
        ],
        "osaker_mkn_grans": iv.osaker_mkn_grans,
        "status_text": status_text,
    }


def bygg_bullerskarm_tabell(
    val: Sequence[BullerSkarmVal],
    *,
    riktvarde_db: float = 55.0,
) -> dict[str, Any]:
    """Bygger en deterministisk prioriteringstabell för bullerskärmar ur CELF-optimering."""
    rader: list[dict[str, Any]] = []
    md_linjer = [
        (
            "| Prioritet | Skärmsegment | Marginalvinst | Kumulativ vinst | "
            f"Nya godkända (≤ {riktvarde_db:.0f} dBA) |"
        ),
        "| ---: | --- | ---: | ---: | ---: |",
    ]
    for prio, post in enumerate(val, start=1):
        rader.append(
            {
                "prioritet": prio,
                "skarm_id": post.skarm_id,
                "marginal_vinst": round(post.marginal_vinst, 2),
                "kumulativ_vinst": round(post.kumulativ_vinst, 2),
                "vinst_ensam": round(post.vinst_ensam, 2),
                "nya_godkanda": post.nya_godkanda,
            }
        )
        md_linjer.append(
            f"| {prio} | {post.skarm_id} | {formatera_svenskt_tal(post.marginal_vinst, 1)} | "
            f"{formatera_svenskt_tal(post.kumulativ_vinst, 1)} | {post.nya_godkanda} |"
        )
    return {
        "rubrik": f"Prioriterade bullerskärmar (riktvärde {riktvarde_db:.0f} dBA)",
        "antal_skarmar": len(rader),
        "rader": rader,
        "markdown": "\n".join(md_linjer),
    }
