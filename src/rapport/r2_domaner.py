"""Rapporter R3-underlag: deterministiska tabeller för luft och buller (och skyfallets fältlista).

Tabellbyggarna gör Flodens beräkningsresultat (`sankgraf.bifurkation`, `luft.kanslighet`,
`buller.optimering`) till tabeller i svensk utredningsform: `bygg_faltmatningstabell`,
`bygg_mkn_intervallrad`, `bygg_bullerskarm_tabell` och `skapa_r2_fleromans_sammanfattning`.
Talen skrivs med `rapport.pastaende.formatera_tal` (decimalkomma, smalt hårt mellanslag mellan
tusental), alltså samma svenska sifferform som rapportkedjan.

**Status: fristående underlag för R3 (luft och buller).** Modulen är testad
(`tests/rapport/test_r2_domaner.py`) men inte inkopplad i något användarflöde (CLI, webb, MCP,
K20-lösare eller rapportkedja).

**Tal får nå en rapport bara via belägg.** Rapporttal kommer ur `FlodenTjanst` och
`rapport.belaggskalla` (mått med enhet, avrundning och belägg per körning). Luft och buller har
inga körningar med belägg än, och därför får inget i den här modulen nå en rapport direkt:
tabellerna är underlag tills de domänerna har körningar med belägg och mått i
`belaggskalla.MATTKATALOG`. Då ska talen i rapporten hämtas därifrån, inte härifrån.
Modulen har ingen egen måttkatalog och ingen egen talformatering (båda var dubbletter av
`belaggskalla.MATTKATALOG` och `pastaende.formatera_tal` och är borttagna).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from buller.optimering import BullerSkarmVal
from luft.kanslighet import MknIntervallResultat
from rapport.pastaende import formatera_tal
from sankgraf.bifurkation import BifurkationsPunkt


def _tal(varde: float, decimaler: int) -> str:
    """Talet med `decimaler` decimaler i rapportkedjans sifferform. Oändligt eller NaN blir "–"
    (en säkerhetsmarginal utan osäkerhet är oändlig och ska inte skrivas som ett tal)."""
    if not math.isfinite(varde):
        return "–"
    return formatera_tal(varde, None, 10.0**-decimaler, visa_enhet=False)


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
        sak_str = _tal(p.sakerhetsmarginal, 2)
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
            f"| {p.nod_id} | {koord_str} | {_tal(p.overfall_hojd_m, 2)} | "
            f"{_tal(p.marginal_m, 2)} | {sak_str} | "
            f"{_tal(p.paverkad_volym_m3, 0)} |"
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
            f"| {prio} | {post.skarm_id} | {_tal(post.marginal_vinst, 1)} | "
            f"{_tal(post.kumulativ_vinst, 1)} | {post.nya_godkanda} |"
        )
    return {
        "rubrik": f"Prioriterade bullerskärmar (riktvärde {riktvarde_db:.0f} dBA)",
        "antal_skarmar": len(rader),
        "rader": rader,
        "markdown": "\n".join(md_linjer),
    }


def skapa_r2_fleromans_sammanfattning(
    projektnamn: str,
    *,
    bifurkationer: Sequence[BifurkationsPunkt] | None = None,
    andel_2d_yta: float | None = None,
    svf_intervall: tuple[float, float] | None = None,
    mkn_intervall: Sequence[tuple[str, MknIntervallResultat]] | None = None,
    bullerskarmar: Sequence[BullerSkarmVal] | None = None,
    buller_riktvarde_db: float = 55.0,
) -> dict[str, Any]:
    """Bygger en samlad deterministisk R2-sammanfattning över upp till fyra fysikdomäner.

    Endast domäner med faktiskt uppmätta/beräknade indata tas med; inga tal gissas eller
    interpoleras fram.
    """
    domaner: dict[str, Any] = {}
    md: list[str] = [f"# Samlad R2-sammanfattning – {projektnamn}"]

    if bifurkationer is not None or andel_2d_yta is not None:
        skyfall_del: dict[str, Any] = {}
        md.append("\n## Skyfall")
        if andel_2d_yta is not None:
            procent = round(float(andel_2d_yta) * 100.0, 1)
            skyfall_del["andel_2d_yta_procent"] = procent
            md.append(
                f"- Andel aktiv markyta som kräver 2D-hydraulik: "
                f"**{_tal(procent, 1)} %**"
            )
        if bifurkationer is not None:
            falt = bygg_faltmatningstabell(bifurkationer)
            skyfall_del["faltmatning"] = falt
            md.append(
                f"- Instabila överfallspunkter att mäta in i fält: "
                f"**{falt['antal_instabila']} st**"
            )
            md.append("")
            md.append(falt["markdown"])
        domaner["skyfall"] = skyfall_del

    if svf_intervall is not None:
        svf_min, svf_max = float(svf_intervall[0]), float(svf_intervall[1])
        domaner["dagsljus"] = {
            "svf_intervall": [round(svf_min, 3), round(svf_max, 3)],
        }
        md.append("\n## Dagsljus och sol")
        md.append(
            f"- Monotont SVF-intervall (byggnadshöjdsosäkerhet): "
            f"**{_tal(svf_min, 2)}–{_tal(svf_max, 2)}**"
        )

    if mkn_intervall:
        mkn_rader = [bygg_mkn_intervallrad(plats, iv) for plats, iv in mkn_intervall]
        domaner["luft"] = {"mkn_platser": mkn_rader}
        md.append("\n## Luftkvalitet och MKN")
        for rad in mkn_rader:
            d_min, d_max = rad["dygn_over_50_intervall"]
            md.append(
                f"- **{rad['plats']}**: {d_min}–{d_max} dygn/år > 50 µg/m³ – {rad['status_text']}"
            )

    if bullerskarmar is not None:
        tabell = bygg_bullerskarm_tabell(bullerskarmar, riktvarde_db=buller_riktvarde_db)
        domaner["buller"] = tabell
        md.append(f"\n## Buller ({tabell['rubrik']})")
        md.append(tabell["markdown"])

    return {
        "format": "floden-r2-sammanfattning/v1",
        "projektnamn": projektnamn,
        "domaner": domaner,
        "markdown": "\n".join(md) + "\n",
    }
