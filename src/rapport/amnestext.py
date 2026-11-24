# ruff: noqa: E501
"""Mallens ämnesspecifika text: skyfall (R1) samt dagsljus/sol och ekologi (R2).

Allt här är fast text (utan tal ur körningar) som skissmallen, kartorna och kontrollen delar.
Skyfalls texter är oförändrade sedan R1. Beställningen (K30) styr ingressen: syfte, skede och
mottagare. Texten säger aldrig att förvaltningen tar ställning.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from rapport.bestallning import MOTTAGARTEXT
from rapport.sprak import TOMVARDE_SBK, scenarionamn

if TYPE_CHECKING:
    from rapport.skissrapport import Rapport

_TOMVARDE_LAGRAD: Final[dict[str, str]] = {
    "skyfall": (
        "Vitt: mark under teckenförklaringens lägsta klass. Ljusgrått: byggnadernas tak eller "
        "celler utan värde."
    ),
    "dagsljus": (
        "Ljusgrått: byggnadernas tak eller celler utan värde. I skillnadskartan är marken vit "
        "där förändringen är mindre än tröskeln."
    ),
    "ekologi": (
        "Ljusgrått: byggnadernas tak eller celler utan värde. I skillnadskartan är marken vit "
        "där förändringen är mindre än tröskeln."
    ),
}
_TOMVARDE_SBK: Final[dict[str, str]] = {
    "skyfall": TOMVARDE_SBK,
    "dagsljus": (
        "Ljusgrått: tak och ytor utan beräknat värde. I skillnadskartan är marken vit där "
        "förändringen är mindre än tröskelvärdet."
    ),
    "ekologi": (
        "Ljusgrått: tak, byggnader och ytor utan beräknat värde. I skillnadskartan är marken "
        "vit där förändringen är mindre än tröskelvärdet."
    ),
}
EKOLOGI_ENHET: Final[str] = "normaliserad strömtäthet (0-1)"
"""Strömtätheten saknar fysikalisk enhet (normaliserad 0-1); kartor och bildtexter säger det."""
EKOLOGI_ETIKETT: Final[str] = "potentiell livsmiljö, okänd kvalitet"
TITEL: Final[dict[str, str]] = {
    "skyfall": "Skissunderlag skyfall",
    "dagsljus": "Skissunderlag dagsljus och sol",
    "ekologi": "Skissunderlag ekologi: grön infrastruktur och spridningssamband",
}


def tomvarde_lagrad(amne: str) -> str:
    return _TOMVARDE_LAGRAD[amne]


def tomvarde_sbk(amne: str) -> str:
    return _TOMVARDE_SBK[amne]


def lage(amne: str, scenario: str) -> str:
    """Scenariots läge i löptext: "före förslaget" (skyfall) eller "i nuläget" (dagsljus)."""
    namn = scenarionamn(scenario)
    if amne == "dagsljus":
        return "i nuläget" if namn == "nuläge" else f"i alternativet {namn}"
    if namn in ("före", "efter"):
        return f"{namn} förslaget"
    return f"i alternativet {namn}"


def kartrubrik(amne: str, typ: str, scenario: str | None, fore: str, efter: str) -> str:
    """Kartans rubrik i bilden (ritas av Floden), i klarspråk."""
    if amne == "ekologi":
        if typ == "skillnad":
            return f"Skillnad i strömtäthet (flaskhalsar), {scenarionamn(efter)} minus {scenarionamn(fore)}"
        return f"Strömtäthet (flaskhalsar) {lage(amne, scenario or '')}"
    if amne == "dagsljus":
        if typ == "skillnad":
            return f"Skillnad i soltimmar på marken, {scenarionamn(efter)} minus {scenarionamn(fore)}"
        return f"Soltimmar på marken {lage(amne, scenario or '')}"
    if typ == "skillnad":
        return f"Skillnad i största vattendjup, {scenarionamn(efter)} minus {scenarionamn(fore)}"
    return f"Största vattendjup {lage(amne, scenario or '')}"


def bildtext_lagrad(
    amne: str, typ: str, j: Mapping[str, Any], meta: Mapping[str, Any], losare: str, korning: str
) -> str:
    """Flodens neutrala bildtext, lagrad i K33 (kontrollen läser den)."""
    vad = {
        "bas": f"före (scenario {j['fore']})",
        "scenario": f"efter (scenario {j['efter']})",
        "skillnad": f"skillnad, efter minus före ({j['efter']} − {j['fore']})",
    }[typ]
    enhet = meta.get("enhet") or ""
    if amne == "ekologi":
        return (
            f"Strömtäthet (flaskhalsar) för {EKOLOGI_ETIKETT}, {vad}, i {EKOLOGI_ENHET}. "
            "Hög strömtäthet: rörelsen genom sambandet är koncentrerad till få passager. "
            f"Körning {korning}. Norr uppåt; skalstock i kartan. {tomvarde_lagrad(amne)}"
        )
    if amne == "dagsljus":
        return (
            f"Direkt sol på marken, {vad}, i {enhet} per dygn. Dygnssumma: timmar per dygn, medel "
            f"över de valda datumen. Körning {korning}. Norr uppåt; skalstock i kartan. "
            f"{tomvarde_lagrad(amne)}"
        )
    tid = (
        "Statisk fyllning: läget vid regnets slut."
        if losare == "vatten.statisk_fyllning"
        else "Högsta djup per cell under händelsen."
    )
    return (
        f"Största vattendjup över mark, {vad}, i {enhet}. {tid} Körning {korning}. "
        f"Norr uppåt; skalstock i kartan. {tomvarde_lagrad(amne)}"
    )


def bildtext_sbk(
    amne: str, typ: str, scenario: str, fore: str, efter: str, korning: str, losare: str
) -> str:
    """Bildtexten i språkprofilen sbk (utan figurnummer)."""
    if amne == "ekologi":
        if typ == "skillnad":
            vad = (
                f"Skillnad i strömtäthet (flaskhalsar), {scenarionamn(efter)} minus "
                f"{scenarionamn(fore)}, i {EKOLOGI_ENHET}. Rött: mer koncentrerad rörelse, blått: mindre."
            )
        else:
            vad = f"Strömtäthet (flaskhalsar) {lage(amne, scenario)}, i {EKOLOGI_ENHET}."
        return (
            f"{vad} Gäller {EKOLOGI_ETIKETT} för signalarten. Beräkning {korning}. Norr uppåt. "
            f"{tomvarde_sbk(amne)}"
        )
    if amne == "dagsljus":
        if typ == "skillnad":
            vad = (
                f"Skillnad i direkt sol på marken, {scenarionamn(efter)} minus "
                f"{scenarionamn(fore)}, i timmar (h) per dygn. Blått: färre soltimmar, rött: fler."
            )
        else:
            vad = f"Direkt sol på marken {lage(amne, scenario)}, i timmar (h) per dygn."
        return (
            f"{vad} Dygnssumma: medel över de valda datumen. Beräkning {korning}. Norr uppåt. "
            f"{tomvarde_sbk(amne)}"
        )
    statisk = losare == "vatten.statisk_fyllning"
    tid = (
        "Läget när regnet har slutat (statisk fyllning)."
        if statisk
        else "Det största djupet under regnet."
    )
    if typ == "skillnad":
        vad = (
            f"Skillnad i största vattendjup på marken, {scenarionamn(efter)} minus "
            f"{scenarionamn(fore)} förslaget, i meter (m). Rött: djupare, blått: grundare."
        )
    else:
        vad = f"Största vattendjup på marken {lage(amne, scenario)}, i meter (m)."
    return f"{vad} {tid} Beräkning {korning}. Norr uppåt. {tomvarde_sbk(amne)}"


def fragenamn(r: Rapport) -> str:
    """"skyfallsfrågorna", "dagsljus- och solfrågorna" …"""
    if r.amne == "skyfall":
        return "skyfallsfrågorna"
    if r.amne == "ekologi":
        return "ekologifrågorna"
    prefix = {f[:2] for f in r.fragor}
    if prefix == {"DL"}:
        return "dagsljusfrågorna"
    if prefix == {"SO"}:
        return "solfrågorna"
    return "dagsljus- och solfrågorna"


def amnesnamn(r: Rapport) -> str:
    return {"skyfall": "Skyfall", "dagsljus": "Dagsljus och sol", "ekologi": "Ekologi"}[r.amne]


def ingress(r: Rapport, sbk: bool, demo: bool) -> str:
    """Ingressen. Beställningen (K30) styr: syfte, mottagare och hur urvalet gjordes."""
    b = r.bestallning
    if r.amne == "skyfall" and b is None:
        if sbk:
            text = (
                "Underlaget är en tidig sållning av skyfallsfrågorna för förslaget. Det visar "
                "vilka frågor som behöver utredas vidare och vilket underlag som saknas. "
                "Underlaget prövar inte förslaget mot krav."
            )
            if demo:
                text += " Demokvarteret är påhittat. Inga verkliga data eller byggnader ingår."
        else:
            text = (
                "Skissunderlag för tidig sållning: vilka skyfallsfrågor behöver fördjupas, och "
                "vad saknas för att kunna svara? Underlaget bedömer inte förslaget mot krav."
            )
            if demo:
                text += " Demokvarteret är påhittat; inga verkliga data eller byggnader."
        return text
    namn = fragenamn(r)
    if r.amne == "dagsljus":
        sak = "sol på marken och dagsljus vid fasad före och efter förslaget"
    elif r.amne == "ekologi":
        sak = "grön infrastruktur och spridningssamband för en signalart före och efter förslaget"
    else:
        sak = "skyfall före och efter förslaget"
    if b is None:
        delar = [
            f"Underlaget är en tidig sållning av {namn} för förslaget: {sak}."
            if sbk
            else f"Skissunderlag för tidig sållning av {namn}: {sak}.",
            "Det visar vilka frågor som behöver utredas vidare och vilket underlag som saknas. "
            "Underlaget prövar inte förslaget mot krav.",
        ]
    else:
        roll = MOTTAGARTEXT[b.mottagare_roll]
        syfte = b.syfte.rstrip(".")
        syfte = syfte[:1].lower() + syfte[1:]
        delar = [
            (
                f"Underlaget är beställt för att {syfte}."
                if sbk
                else f"Skissunderlag beställt för att {syfte}."
            ),
            {
                "planhandlaggare": (
                    "Det är skrivet för planhandläggaren: det visar vilka frågor som behöver "
                    "utredas vidare i planarbetet och vilket underlag som saknas."
                ),
                "byggaktor": (
                    "Det är skrivet för byggaktören: det visar vad beräkningen säger om "
                    "förslaget och vilket underlag byggaktören behöver ta fram i det fortsatta "
                    "arbetet."
                ),
                "granskare": (
                    "Det är skrivet för granskaren: varje tal har ett belägg i bilagan, så att "
                    "beräkningen kan följas från karta och tabell till körning och fil."
                ),
            }[b.mottagare_roll],
        ]
        delar.append(_urvalstext(r, roll))
        delar.append("Underlaget prövar inte förslaget mot krav.")
    text = " ".join(delar)
    if demo:
        text += (
            " Demokvarteret är påhittat. Inga verkliga data eller byggnader ingår."
            if sbk
            else " Demokvarteret är påhittat; inga verkliga data eller byggnader."
        )
    return text


def _urvalstext(r: Rapport, roll: str) -> str:
    b = r.bestallning
    assert b is not None
    tot = len(b.urval) + b.utelamnade
    if b.fragor:
        grund = "valda uttryckligen i beställningen"
    elif b.bara_med_underlag:
        grund = "de frågor där beräkningen kan ge underlag helt eller delvis"
    elif b.mottagare_roll != "planhandlaggare":
        grund = f"de frågor som är relevanta för en {roll}"
    elif b.villkor:
        grund = "de frågor som matchar beställningens villkor"
    else:
        grund = "alla frågor i ämnet"
    if b.utelamnade == 0:
        return f"Alla {tot} frågor i frågekatalogen för ämnet ingår ({grund})."
    return (
        f"Underlaget tar upp {len(b.urval)} av {tot} frågor i frågekatalogen: {grund}."
    )


def resultatintro(r: Rapport, sbk: bool, bilref: str) -> str:
    if r.amne == "ekologi":
        if sbk:
            return (
                "Kartorna nedan visar var rörelsen genom sambandet är koncentrerad (flaskhalsar) "
                "före och efter förslaget, med samma färgskala, och skillnaden mellan dem. "
                f"Resultatet gäller {EKOLOGI_ETIKETT}, inte verkliga arters förekomst. Talen i "
                f"texten är framräknade av Floden ur beräkningen. Varje tal har ett belägg i {bilref}."
            )
        return (
            "Kartorna visar strömtäthet (flaskhalsar) före och efter förslaget, med samma "
            "färgskala, och skillnaden efter minus före. Resultatet gäller "
            f"{EKOLOGI_ETIKETT}. Talen i texten är ifyllda av Floden ur körningen; varje tal har "
            f"ett belägg i {bilref}."
        )
    if r.amne == "dagsljus":
        if sbk:
            return (
                "Kartorna nedan visar direkt sol på marken före och efter förslaget, med samma "
                "färgskala, och skillnaden mellan dem. Talen i texten är framräknade av Floden "
                f"ur beräkningen. Varje tal har ett belägg i {bilref}."
            )
        return (
            "Kartorna visar direkt sol på marken (timmar per dygn) före och efter förslaget, med "
            "samma färgskala, och skillnaden efter minus före. Talen i texten är ifyllda av "
            f"Floden ur körningen; varje tal har ett belägg i {bilref}."
        )
    if sbk:
        return (
            "Kartorna nedan visar det största vattendjupet på marken före och efter förslaget, "
            "med samma färgskala, och skillnaden mellan dem. Talen i texten är framräknade av "
            f"Floden ur beräkningen. Varje tal har ett belägg i {bilref}."
        )
    return (
        "Kartorna visar största vattendjup över mark före och efter förslaget, med samma "
        "färgskala, och skillnaden efter minus före. Talen i texten är ifyllda av Floden ur "
        f"körningen; varje tal har ett belägg i {bilref}."
    )


def osakerhet(r: Rapport, sbk: bool, bilaga1: str) -> tuple[str, list[str]]:
    """Inledning och punkter under "Metod och osäkerhet" (utöver metodnotis och metodtabell)."""
    if r.amne == "ekologi":
        if sbk:
            return "Resultatet ska läsas med följande begränsningar:", [
                f"Markunderlaget (NMD) visar {EKOLOGI_ETIKETT}: det säger inget om skogens ålder, "
                "struktur eller naturvärde och inte om signalarten finns där.",
                "Sambandet räknas för en signalart eller artgrupp med den spridningsförmåga och det "
                "motstånd per markslag som nätverkets källa anger. Där källan saknar sannolikheten p "
                "är det ett antagande i Floden (d50 = tröskel/2).",
                "Intervallet är källbaserat: det går mellan nätverkets tröskelscenarier. Det är "
                "inte en statistisk konfidens och täcker inte osäkerheten i habitatvalet.",
                "Strömtätheten är normaliserad (0-1) och visar flaskhalsar i modellen, inte "
                "barriärer som är mätta i fält. Ändringar under tröskelvärdet markeras inte i kartan.",
                f"{bilaga1} anger för varje tal vad det mäter och vilken körning och fil det kommer ur.",
            ]
        return "Begränsningar:", [
            f"NMD ger {EKOLOGI_ETIKETT}: ålder, struktur och naturvärde saknas; förekomst av arten är okänd.",
            "Signalart, spridningströskel och motstånd per markslag är nätverkets källa; där p saknas "
            "gäller antagandet d50 = tröskel/2.",
            "Intervallet är källbaserat (mellan tröskelscenarier), ingen statistisk konfidens.",
            "Strömtätheten är normaliserad (0-1); flaskhalsar är modellresultat, inte fältmätta barriärer.",
            f"Varje beläggs tidsdefinition och statistik står i {bilaga1}.",
        ]
    if r.amne == "dagsljus":
        if sbk:
            return "Resultatet ska läsas med följande begränsningar:", [
                "Soltimmarna är direkt sol på markytan, som dygnssumma (timmar per dygn, medel "
                "över de valda datumen). Sol vid fönster och på fasad räknas inte.",
                "Dagsljuset är den vertikala himmelskomponenten (VSC) vid fasadpunkter under "
                "mulen himmel. Det är inte dagsljusfaktor i rum. Fasadpunkterna är genererade, "
                "inte verkliga fönster.",
                "Taken är platta på sin höjd. Balkonger, takutsprång och sadeltak ingår inte, "
                "och vegetation ingår inte. Reflekterat ljus ingår inte.",
                "Taken räknas inte som mark när läget före och efter förslaget jämförs.",
                f"{bilaga1} anger för varje tal vad det mäter och vilka punkter eller celler "
                "det gäller.",
            ]
        return "Begränsningar:", [
            "Soltimmar är direkt sol på markytan (dygnssumma, medel över valda datum), inte sol "
            "på fasad eller vid fönster.",
            "VSC är himmelskomponent vid fasadpunkter under mulen CIE-himmel, inte "
            "dagsljusfaktor i rum; fasadpunkterna är inte verkliga fönster.",
            "Platta tak; balkonger, utsprång och vegetation ingår inte; ingen reflektion.",
            "Byggnadernas tak räknas inte som mark i jämförelserna (samma takmask som webben).",
            f"Varje beläggs tidsdefinition och statistik står i {bilaga1}.",
        ]
    raise KeyError(r.amne)


__all__ = [
    "EKOLOGI_ENHET",
    "EKOLOGI_ETIKETT",
    "TITEL",
    "amnesnamn",
    "bildtext_lagrad",
    "bildtext_sbk",
    "fragenamn",
    "ingress",
    "kartrubrik",
    "lage",
    "osakerhet",
    "resultatintro",
    "tomvarde_lagrad",
    "tomvarde_sbk",
]
