# ruff: noqa: E501
"""AI-fritt referensflöde: skissunderlag skyfall för demokvarterets vattenfråga (R1).

Fasta påståenden (inga modeller) för frågorna SK-01 till SK-15, med belägg ur körningen
`demo-vatten`. Används av `tools/rapport_demo.py` och testerna. Formuleringarna är neutrala:
riktningen i ett tal (ökar/minskar) skrivs inte i texten, eftersom värdet kommer ur körningen.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from demovarld.varld import LAMELL_DJUP_M, DemoVarld

JFR: Final[str] = "skyfall_max_djup_efter_minus_fore"


def gardens_mottagare(v: DemoVarld | None = None) -> dict[str, Any]:
    """Gården i det sydvästra kvarteret (där det nya huset byggs), som mottagare."""
    v = v or DemoVarld()
    kv = next(k for k in v.kvarter() if k[0] == 1 and k[1] == 1)
    _i, _j, x0, x1, y0, y1 = kv
    d = LAMELL_DJUP_M
    ring = [
        [v.e0_m + x0 + d, v.n0_m + y0 + d],
        [v.e0_m + x1 - d, v.n0_m + y0 + d],
        [v.e0_m + x1 - d, v.n0_m + y1 - d],
        [v.e0_m + x0 + d, v.n0_m + y1 - d],
    ]
    return {
        "namn": "garden",
        "polygon": ring,
        "beskrivning": "gården i det sydvästra kvarteret, där det nya huset byggs",
    }


def _m(adress: str, opt: str = "") -> str:
    return "{{matt:" + adress + (f"|{opt}" if opt else "") + "}}"


def pastaenden(analys: str = "skyfall") -> list[Mapping[str, Any]]:
    """De fasta påståendena (argument till `RapportTjanst.lagg_till_pastaende`)."""
    mv = f"matvarde.{analys}"
    jf = f"jamforelse.{JFR}"
    return [
        {
            "fraga_id": "SK-01",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": f"Största vattendjup i beräkningsområdet är {_m(mv + '.fore.max_djup_m')} "
            f"före och {_m(mv + '.efter.max_djup_m')} efter förslaget.",
            "belagg": [f"{mv}.fore.max_djup_m", f"{mv}.efter.max_djup_m"],
            "saknat_underlag": [
                "Markanvändning, känsliga byggnader, entréer och riskkällor finns inte i "
                "underlaget."
            ],
            "ansvarig_funktion": "Beställaren (planhandläggaren)",
        },
        {
            "fraga_id": "SK-02",
            "status": "villkorat",
            "fordjupning": "ja",
            "text": "Sållningen är gjord med Stockholmsregn 2024 (Tabell 7): återkomsttid "
            f"{_m(mv + '.efter.aterkomsttid_ar')}, varaktighet "
            f"{_m(mv + '.efter.varaktighet_min')}, klimatfaktor "
            f"{_m(mv + '.efter.klimatfaktor', 'avrundning=0,1')} och regnmängd "
            f"{_m(mv + '.efter.regn_mm', 'avrundning=1')}, som blockregn.",
            "belagg": [
                f"{mv}.efter.aterkomsttid_ar",
                f"{mv}.efter.varaktighet_min",
                f"{mv}.efter.klimatfaktor",
                f"{mv}.efter.regn_mm",
            ],
            "forutsattning": "Att beställaren fastställer dimensionerande regn och "
            "säkerhetsnivå, med högre nivå om samhällsviktig verksamhet berörs.",
            "ansvarig_funktion": "Beställaren med stadens skyfallsfunktion",
        },
        {
            "fraga_id": "SK-03",
            "status": "underlag_saknas",
            "fordjupning": "kan_inte_avgoras",
            "text": f"Under regnet rinner {_m(mv + '.fore.ut_m3')} ut över beräkningsområdets "
            f"kant före förslaget och {_m(mv + '.efter.ut_m3')} efter.",
            "belagg": [f"{mv}.fore.ut_m3", f"{mv}.efter.ut_m3"],
            "saknat_underlag": [
                "Tillrinningsområde, plangräns och nedströms mottagare; randvillkoret är fritt "
                "utflöde över rutans kant."
            ],
        },
        {
            "fraga_id": "SK-04",
            "status": "underlag_saknas",
            "fordjupning": "kan_inte_avgoras",
            "text": "Floden redovisar inte höjder och öppningar per objekt i skissen.",
            "saknat_underlag": [
                "Versionerad terräng med portar, trummor, kantstenar och entrétrösklar i "
                "RH 2000, kontrollerade på plats."
            ],
        },
        {
            "fraga_id": "SK-05",
            "status": "visat",
            "fordjupning": "nej",
            "text": "Ledningsnätet antas fullt under skyfallet: avtappningen är "
            f"{_m(mv + '.efter.avtappning_mm_h')}. Regnet på taken leds till marken längs "
            "fasaderna; andelen som går direkt till ledningsnätet är "
            f"{_m(mv + '.efter.takvatten_andel_ledning')}. Statisk fyllning räknar ingen "
            "infiltration.",
            "belagg": [f"{mv}.efter.avtappning_mm_h", f"{mv}.efter.takvatten_andel_ledning"],
        },
        {
            "fraga_id": "SK-06",
            "status": "visat",
            "fordjupning": "ja",
            "text": f"Med förslaget blir vattnet djupare än tröskeln på {_m(jf + '.yta_okning_m2')} "
            f"mark, och den största ökningen i en cell är {_m(jf + '.max_okning')}. Ytan där "
            f"djupet minskar mer än tröskeln är {_m(jf + '.yta_minskning_m2')}.",
            "belagg": [f"{jf}.yta_okning_m2", f"{jf}.max_okning", f"{jf}.yta_minskning_m2"],
            "mottagare": "hela_omradet",
        },
        {
            "fraga_id": "SK-07",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": f"Den stående vattenvolymen i sänkorna är {_m(mv + '.fore.lagrat_m3')} före "
            f"och {_m(mv + '.efter.lagrat_m3')} efter förslaget.",
            "belagg": [f"{mv}.fore.lagrat_m3", f"{mv}.efter.lagrat_m3"],
            "saknat_underlag": [
                "Flöde och varaktighet vid grannar och nedströms mottagare (statisk fyllning ger "
                "inget förlopp)."
            ],
        },
        {
            "fraga_id": "SK-07",
            "status": "visat",
            "fordjupning": "ja",
            "mottagare": "garden",
            "text": "I gården där huset byggs är största djupet på mark "
            f"{_m('omrade.garden.' + analys + '.efter.max_djup.max')} efter förslaget, och "
            "djupet ökar mer än tröskeln på "
            f"{_m('omrade.garden.skillnad.' + JFR + '.yta_okning_m2')}.",
            "belagg": [
                f"omrade.garden.{analys}.efter.max_djup.max",
                f"omrade.garden.skillnad.{JFR}.yta_okning_m2",
            ],
        },
        {
            "fraga_id": "SK-08",
            "status": "underlag_saknas",
            "fordjupning": "kan_inte_avgoras",
            "text": "Framkomligheten är inte bedömd.",
            "saknat_underlag": [
                "Rutt till entréerna i frågan (K7), fastställd fordonsgräns och tidsserie för "
                "djup och hastighet."
            ],
        },
        {
            "fraga_id": "SK-09",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Ingen skyddsåtgärd har prövats i skissen.",
            "motivering": "Frågan gäller genomförande och drift av en skyddsåtgärd; skissen "
            "prövar bara ett nytt hus utan åtgärd.",
        },
        {
            "fraga_id": "SK-10",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": "Volymbalansens relativa fel är "
            f"{_m(mv + '.fore.volymfel_rel', 'format=exp')} före och "
            f"{_m(mv + '.efter.volymfel_rel', 'format=exp')} efter förslaget. Känslighetsfall "
            "har inte körts.",
            "belagg": [f"{mv}.fore.volymfel_rel", f"{mv}.efter.volymfel_rel"],
            "saknat_underlag": [
                "Känslighetsfall för terräng, regn och avledning, och jämförelse mot oberoende "
                "underlag."
            ],
        },
        {
            "fraga_id": "SK-11",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Skissen är ingen kombinerad dagvattenutredning.",
            "motivering": "Vardagsregn och rening räknas inte i Floden; frågan prövas i "
            "dagvattenutredningen.",
        },
        {
            "fraga_id": "SK-12",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": "Kartorna visar största vattendjup i meter med teckenförklaring och "
            "tomvärde; det största djupet efter förslaget är "
            f"{_m(mv + '.efter.max_djup_m')}.",
            "belagg": [f"{mv}.efter.max_djup_m"],
            "saknat_underlag": [
                "Vattenyta i RH 2000, sektioner genom kritiska passager och tidsserie."
            ],
        },
        {
            "fraga_id": "SK-13",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Inga åtgärder utanför beräkningsområdet ingår.",
            "motivering": "Skissen prövar inga åtgärder utanför området och ingen etappvis "
            "utbyggnad.",
        },
        {
            "fraga_id": "SK-14",
            "status": "underlag_saknas",
            "fordjupning": "kan_inte_avgoras",
            "text": "Räddningsväg och insatsplats är inte bedömda.",
            "saknat_underlag": [
                "Fordonsgräns ur brandunderlaget och hydrauliskt underlag längs räddningsvägen."
            ],
        },
        {
            "fraga_id": "SK-15",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Skissen omfattar ett beräkningsområde och en utredning.",
            "motivering": "Ingen sammanvägning av flera delområden eller utredningar behövs.",
        },
    ]


__all__ = ["JFR", "gardens_mottagare", "pastaenden"]
