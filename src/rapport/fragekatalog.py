# ruff: noqa: E501
"""Frågekatalogen (`docs/utredningar-stockholm/fragor.json`) och vad Floden kan belägga (R1).

Katalogen är redaktionellt underlag (källäge 21 november 2026, sammanställt med AI-stöd och ska
granskas). Varje påstående i en rapport hör till en fråga ur katalogen (`SK-06` för skyfall).

`FLODEN_FORMAGA` säger för varje skyfallsfråga vad Floden i dag kan ge belägg för, och varför
resten blir *underlag saknas*. Tabellen i `docs/RAPPORTER.md` skrivs ur den här tabellen
(testet kontrollerar att de stämmer). Ingen fråga får status *visat* utan belägg; det avgörs av
påståendets egna belägg, inte av tabellen.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

STANDARD_KATALOG: Final[Path] = (
    Path(__file__).resolve().parents[2] / "docs" / "utredningar-stockholm" / "fragor.json"
)

AMNESPREFIX: Final[dict[str, tuple[str, ...]]] = {
    "skyfall": ("SK",),
    "dagsljus": ("DL", "SO"),
    "ekologi": ("EK",),
    "luft": ("LU",),
    "buller": ("BU",),
}
"""Rapportämne -> frågornas id-prefix. Rapportämnet `dagsljus` omfattar katalogens områden
`dagsljus` (DL) och `solstudier` (SO). Skissrapport finns för skyfall (R1), dagsljus, ekologi och luft (R2)."""
AMNESOMRADEN: Final[dict[str, tuple[str, ...]]] = {
    "skyfall": ("skyfall",),
    "dagsljus": ("dagsljus", "solstudier"),
    "ekologi": ("ekologi",),
    "luft": ("luft",),
    "buller": ("buller",),
}
"""Rapportämne -> katalogens `omrade`."""
OMRADESALIAS: Final[dict[str, str]] = {"sol": "solstudier"}
"""Alias som tas emot i API och MCP: `sol` är katalogens område `solstudier`."""
FLODEN_MARKNING: Final[tuple[str, ...]] = ("visat", "delvis", "underlag_saknas", "ej_tillampligt")
MARKNING_TEXT: Final[dict[str, str]] = {
    "visat": "visat",
    "delvis": "delvis",
    "underlag_saknas": "underlag saknas",
    "ej_tillampligt": "ej tillämpligt",
}


class FragekatalogFel(Exception):
    """Katalogen saknas, är trasig, eller frågan finns inte."""


@dataclass(frozen=True)
class Fraga:
    """En fråga ur katalogen (fälten som i `fragor.json`)."""

    id: str
    omrade: str
    typ: str
    villkor: str
    fraga: str
    redovisning: str
    kallor: tuple[str, ...]

    def till_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "omrade": self.omrade,
            "typ": self.typ,
            "villkor": self.villkor,
            "fraga": self.fraga,
            "redovisning": self.redovisning,
            "kallor": list(self.kallor),
        }


@dataclass(frozen=True)
class Formaga:
    """Vad Floden i dag kan belägga för en fråga.

    `niva`: `kan` (Floden ger belägg för frågans kärna), `delvis` (belägg för en del; resten blir
    underlag saknas eller villkorat) eller `saknas` (Floden räknar inte det frågan gäller).
    `adresser`: beläggsadresser (mönster) som kan användas. `saknat`: vad som fattas och varför.
    `forval_status`: status i referensflödet för demokvarteret.
    """

    niva: str
    belagg: str
    saknat: str
    adresser: tuple[str, ...]
    forval_status: str

    @property
    def markning(self) -> str:
        """Kan Floden ge underlag i dag? `visat` (frågans kärna), `delvis`, `underlag_saknas`
        (Floden räknar inte det frågan gäller) eller `ej_tillampligt` (frågan avgörs utanför en
        beräkning: regelval, genomförande, sammanvägning)."""
        if self.niva == "kan":
            return "visat"
        if self.niva == "delvis":
            return "delvis"
        return "ej_tillampligt" if self.forval_status == "ej_tillampligt" else "underlag_saknas"


FLODEN_FORMAGA: Final[dict[str, Formaga]] = {
    "SK-01": Formaga(
        "delvis",
        "Största vattendjup före och efter på mark (karta och tal).",
        "Markanvändning, känsliga byggnader, entréer och riskkällor finns inte i Floden.",
        ("matvarde.<analys>.<scenario>.max_djup_m",),
        "underlag_saknas",
    ),
    "SK-02": Formaga(
        "delvis",
        "Det regn som körningen använde: källa, återkomsttid, varaktighet, klimatfaktor, mängd.",
        "Vilken säkerhetsnivå som krävs är ett beslut, inte en beräkning (villkorat).",
        (
            "matvarde.<analys>.<scenario>.aterkomsttid_ar",
            "matvarde.<analys>.<scenario>.varaktighet_min",
            "matvarde.<analys>.<scenario>.klimatfaktor",
            "matvarde.<analys>.<scenario>.regn_mm",
        ),
        "villkorat",
    ),
    "SK-03": Formaga(
        "delvis",
        "Utflöde över beräkningsområdets kant och stående vatten.",
        "Tillrinningsområde, plangräns och nedströms mottagare avgränsas inte; randvillkoret "
        "är fritt utflöde över rutans kant.",
        ("matvarde.<analys>.<scenario>.ut_m3", "matvarde.<analys>.<scenario>.lagrat_m3"),
        "underlag_saknas",
    ),
    "SK-04": Formaga(
        "saknas",
        "",
        "Terrängens version finns i rutans recept (sha256) men inga mått per höjd, öppning "
        "eller tröskel; objekten behöver kontrolleras på plats.",
        (),
        "underlag_saknas",
    ),
    "SK-05": Formaga(
        "kan",
        "Avtappning till ledningsnätet och takvatten som de räknades (statisk fyllning har "
        "ingen infiltration och inget förlopp).",
        "Markfukt, igensättning och upptryckning modelleras inte.",
        (
            "matvarde.<analys>.<scenario>.avtappning_mm_h",
            "matvarde.<analys>.<scenario>.takvatten_andel_ledning",
        ),
        "visat",
    ),
    "SK-06": Formaga(
        "kan",
        "Skillnad före/efter i största vattendjup på mark (takmask som webben), samma regn och "
        "område, med differenskarta.",
        "Planförslag med åtgärd körs bara om det finns som eget scenario.",
        (
            "jamforelse.<namn>.max_okning",
            "jamforelse.<namn>.max_minskning",
            "jamforelse.<namn>.yta_okning_m2",
            "jamforelse.<namn>.yta_minskning_m2",
        ),
        "visat",
    ),
    "SK-07": Formaga(
        "delvis",
        "Stående vattenvolym och utflöde före/efter, djup i en namngiven mottagare (polygon).",
        "Flöde och varaktighet saknas (statisk fyllning); nedströms mottagare är inte utpekade.",
        (
            "matvarde.<analys>.<scenario>.lagrat_m3",
            "matvarde.<analys>.<scenario>.ut_m3",
            "omrade.<mottagare>.<lager_id>.<statistik>",
        ),
        "underlag_saknas",
    ),
    "SK-08": Formaga(
        "saknas",
        "",
        "Framkomlighet ej bedömd: ingen rutt i frågan, fordonsgränsen (K7) är inte fastställd "
        "och tidsserie för djup och hastighet saknas i statisk fyllning.",
        (),
        "underlag_saknas",
    ),
    "SK-09": Formaga(
        "saknas",
        "",
        "Genomförande och drift är projektkontroll utanför Floden.",
        (),
        "ej_tillampligt",
    ),
    "SK-10": Formaga(
        "delvis",
        "Volymbalansens relativa fel (massbalans) per körning.",
        "Känslighetsfall (terräng, regn, avledning) kräver fler körningar; jämförelse mot "
        "oberoende underlag finns inte.",
        ("matvarde.<analys>.<scenario>.volymfel_rel",),
        "underlag_saknas",
    ),
    "SK-11": Formaga(
        "saknas",
        "",
        "Dagvattenhantering (vardagsregn, rening) räknas inte i Floden.",
        (),
        "ej_tillampligt",
    ),
    "SK-12": Formaga(
        "delvis",
        "Maxdjup i meter på karta med teckenförklaring, tomvärde och modellversion.",
        "Vattenyta i RH 2000, sektioner och tidsserie redovisas inte.",
        ("matvarde.<analys>.<scenario>.max_djup_m",),
        "underlag_saknas",
    ),
    "SK-13": Formaga(
        "saknas",
        "",
        "Åtgärder utanför planområdet och etappvis utbyggnad prövas inte i skissen.",
        (),
        "ej_tillampligt",
    ),
    "SK-14": Formaga(
        "saknas",
        "",
        "Räddningsväg och insatsplats: fordonsgräns och brandunderlag saknas (K7 tom).",
        (),
        "underlag_saknas",
    ),
    "SK-15": Formaga(
        "saknas",
        "",
        "Sammanvägning av flera utredningar görs inte i en skiss för ett beräkningsområde.",
        (),
        "ej_tillampligt",
    ),
}


_SOLMARK = "omrade.hela_omradet.<lager_id>.<statistik>"
_JFR = "jamforelse.<namn>.<mått>"
_FASAD = "fasad.<analys>.<scenario>.<mått>"
_FASADJFR = "fasadjamforelse.<analys>.<fore>.<efter>.<mått>"
_FASADPAR = "fasad.<analys>.<parameter>"
_OMR = "omrade.<mottagare>.<lager_id>.<statistik>"
_DATUM = "matvarde.<analys>.<scenario>.<ruta>.datum"
_LATLON = ("matvarde.<analys>.<scenario>.<ruta>.lat_grader", "matvarde.<analys>.<scenario>.<ruta>.lon_grader")

_VSC_ADR = (_FASAD, _FASADJFR, _FASADPAR)
_SOL_ADR = (_SOLMARK, _JFR)

FLODEN_FORMAGA.update(
    {
        "DL-01": Formaga(
            "saknas",
            "",
            "Regelval och byggåtgärd är ett beslut i ärendet, inte en beräkning. Skissen jämför "
            "före och efter ett planförslag (grannpåverkan) och väljer ingen regelversion.",
            (),
            "ej_tillampligt",
        ),
        "DL-02": Formaga(
            "delvis",
            "Himmelskomponent (VSC) på fasadpunkter före och efter: antal punkter, lägsta värde, "
            "punkter under BRE:s gräns och med stor relativ minskning.",
            "Sektioner och kritiska fönster redovisas inte; fasadpunkterna är genererade "
            "(en per våning och fasadsteg), inte verkliga fönster. Balkonger modelleras inte.",
            _VSC_ADR,
            "underlag_saknas",
        ),
        "DL-03": Formaga(
            "saknas",
            "",
            "Ingen rumsmodell: glasarea, karm, smyg, ljustransmittans, rumsdjup och reflektanser "
            "ingår inte (docs/DAGSLJUS.md avsnitt 11).",
            (),
            "underlag_saknas",
        ),
        "DL-04": Formaga(
            "saknas",
            "",
            "Dagsljusfaktor (DF) i typrum finns inte; Floden räknar VSC på fasad under mulen "
            "CIE-himmel. DF är en senare fas.",
            (),
            "underlag_saknas",
        ),
        "DL-05": Formaga(
            "saknas",
            "",
            "Ingen dagsljusfaktor och inga bostäder eller vistelseytor i modellen.",
            (),
            "underlag_saknas",
        ),
        "DL-06": Formaga(
            "saknas",
            "",
            "Ingen dagsljusfaktor och ingen uppdelning i boendeenhet och delade utrymmen.",
            (),
            "underlag_saknas",
        ),
        "DL-07": Formaga(
            "saknas",
            "",
            "Fönster, bröstningshöjd och sikthinder för utblick modelleras inte.",
            (),
            "underlag_saknas",
        ),
        "DL-08": Formaga(
            "saknas",
            "",
            "Verksamhet, arbetsplatser och bedömningsmetod för dagsljus och utblick ingår inte.",
            (),
            "underlag_saknas",
        ),
        "DL-09": Formaga(
            "saknas",
            "",
            "Anpassning av krav vid ändrad användning är en juridisk avvägning i ärendet, inte en "
            "beräkning.",
            (),
            "ej_tillampligt",
        ),
        "DL-10": Formaga(
            "kan",
            "VSC i samma fasadpunkt (samma fasad_id) före och efter: utgångsläge, minskning, "
            "antal punkter under BRE:s gräns och under kvoten mot läget före.",
            "Fasadpunkter är inte verkliga fönster och planlösningar är okända; omfattningen "
            "markeras. Reflekterat ljus, glas och balkonger ingår inte.",
            (_FASAD, _FASADJFR, _FASADPAR),
            "visat",
        ),
        "DL-11": Formaga(
            "saknas",
            "",
            "Glas, balkonger och fasadkulör modelleras inte (balkonger och utkragningar ger fel "
            "VSC, docs/DAGSLJUS.md avsnitt 11).",
            (),
            "underlag_saknas",
        ),
        "DL-12": Formaga(
            "delvis",
            "Körningens manifest, kontrollsummor och VSC per fasadpunkt (vsc.csv) gör att en "
            "annan kan återskapa fasadpunkternas värden.",
            "Ingen lägenhetslista, ritningsrevision eller bedömningsyta per bostad.",
            (_FASAD,),
            "underlag_saknas",
        ),
        "DL-13": Formaga(
            "saknas",
            "",
            "Fönstermått, golvyta, användning och utblick från barnets höjd ingår inte.",
            (),
            "underlag_saknas",
        ),
        "DL-14": Formaga(
            "saknas",
            "",
            "Att koppla hälsoskydd, byggkrav och arbetsmiljö till varsin slutsats är en "
            "bedömning i ärendet, inte en beräkning. VSC är ett geometriskt mått.",
            (),
            "ej_tillampligt",
        ),
        "DL-15": Formaga(
            "kan",
            "Förändringens storlek per fasadpunkt (samma fasad_id): största minskning, lägsta "
            "kvot, antal punkter under BRE:s gräns och under kvoten.",
            "Resultattabellen per punkt finns i körningen (vsc.csv); lägenhet och våningsplan "
            "anges bara genom fasad_id. Planlösningar är okända.",
            (_FASADJFR, _FASADPAR),
            "visat",
        ),
        "SO-01": Formaga(
            "delvis",
            "Yta på mark som förlorar mer än en viss tid sol (influensområde i differenskartan) "
            "och största förlust, efter tak undantagna.",
            "Sol vid grannfönster och på fasad räknas inte (soltimmar gäller markytan); "
            "mottagarnas funktion och våningsplan finns inte.",
            _SOL_ADR,
            "underlag_saknas",
        ),
        "SO-02": Formaga(
            "saknas",
            "",
            "Tillåten byggrätt och etapper finns inte i Floden; scenariot är den ändring "
            "(K9) som lagts in.",
            (),
            "underlag_saknas",
        ),
        "SO-03": Formaga(
            "delvis",
            "Solläge (latitud och longitud i rutans mittpunkt) och datum som beräkningen "
            "använde; solläget är mätt mot NREL SPA.",
            "Tidszon och UTC-förskjutning per datum, geografiskt norr och korrigering av "
            "kartnorr redovisas inte i resultatet.",
            (*_LATLON, _DATUM),
            "underlag_saknas",
        ),
        "SO-04": Formaga(
            "delvis",
            "De datum körningen räknade (parametern datum) och soltimmar för dem.",
            "Val av dagar och brukstider för platsens användning är ett beslut; tidsfönster "
            "(öppettid, kväll) kan inte anges, soltimmar gäller hela dygnet.",
            (_DATUM,),
            "underlag_saknas",
        ),
        "SO-05": Formaga(
            "delvis",
            "Terräng och byggnader (platta tak) kastar skugga i beräkningen; kartor och "
            "tak undantagna i talen.",
            "Balkonger och sadeltak modelleras inte; vegetation ingår inte som standard "
            "(ogenomskinliga kronor om den slås på, ingen lövstatus).",
            (_SOLMARK,),
            "underlag_saknas",
        ),
        "SO-06": Formaga(
            "delvis",
            "Soltimmar (medel, största, minsta) i varje yta som anges som mottagare (polygon), "
            "före och efter.",
            "Ytornas funktion (aktiv lek, reträtt) och bedömning mot Lekvärdesriktlinjerna "
            "ingår inte.",
            (_OMR,),
            "underlag_saknas",
        ),
        "SO-07": Formaga(
            "saknas",
            "",
            "Vegetationsskugga, trädens tillväxt och skuggad andel vid ett klockslag räknas "
            "inte (vegetation ingår inte som standard).",
            (),
            "underlag_saknas",
        ),
        "SO-08": Formaga(
            "delvis",
            "Summerad direkt sol per dygn (timmar per dygn, medel över valda datum) på "
            "marken och i en mottagare, före och efter, med tidssteg ur körningen.",
            "Sammanhängande tid och tidsfönster (brukstid) beräknas inte.",
            (_SOLMARK, _OMR),
            "underlag_saknas",
        ),
        "SO-09": Formaga(
            "kan",
            "Matchade före- och efterkartor i samma färgskala, differenskarta, yta som förlorar "
            "sol och tid per mottagare (polygon).",
            "Klockslag för när skuggan faller ingår inte (dygnssumma). Sol per fönster och "
            "sittplats anges bara som mottagare med polygon.",
            (_JFR, _SOLMARK, _OMR),
            "visat",
        ),
        "SO-10": Formaga(
            "saknas",
            "",
            "Sol vid fönster och i rum räknas inte; soltimmar gäller markytan.",
            (),
            "underlag_saknas",
        ),
        "SO-11": Formaga(
            "delvis",
            "Jämförelse av prövade alternativ (varje scenario ställs mot nuläget) med samma "
            "mått: yta som förlorar sol och största förlust.",
            "Floden föreslår ingen åtgärd; indragen våning, omplacerad lekyta och träd prövas "
            "bara om de lagts in som scenario (K9).",
            (_JFR,),
            "underlag_saknas",
        ),
        "SO-12": Formaga(
            "delvis",
            "Kartor med norrpil, skalstock, scenarionamn, enhet och teckenförklaring; varje tal "
            "har belägg med körning, fil och kontrollsumma.",
            "Tidszon och tidsdata per tidssteg redovisas inte i resultatet.",
            (_SOLMARK,),
            "underlag_saknas",
        ),
        "SO-13": Formaga(
            "saknas",
            "",
            "Skydd mot sol (skugga för lek och samling under brukstiden, vegetation) räknas "
            "inte.",
            (),
            "underlag_saknas",
        ),
        "SO-14": Formaga(
            "delvis",
            "Soltimmar och skuggor i lokal geometri för gården eller byggnaden (mottagare).",
            "Länets värmekartläggning och termisk analys (yttemperatur, värmestress) ingår "
            "inte.",
            (_OMR, _SOLMARK),
            "underlag_saknas",
        ),
        "SO-15": Formaga(
            "kan",
            "Varje karta är en dygnssumma (timmar per dygn, medel över de datum körningen "
            "räknade); datum och tidssteg står i antagandena och beläggen.",
            "Ögonblicksbilder och periodmedel för flera perioder ingår inte; vilken yta "
            "slutsatsen gäller anges med mottagare.",
            (_DATUM, _SOLMARK),
            "visat",
        ),
    }
)

_EKO_MV = "matvarde.<analys>.<scenario>[.<ruta>].<mått>"
_EKO_JF = "ekojamforelse.<analys>.<fore>.<efter>[.<ruta>].<mått>"
_EKO_NV = "ekonatverk.<analys>.<scenario>[.<ruta>].<parameter>"
FLODEN_FORMAGA.update(
    {
        "EK-01": Formaga(
            "delvis",
            "Det habitatnätverk som beräkningen avser: namn, signalart, källa och år, "
            "tröskel och minsta fläckarea (parametrar med belägg).",
            "Plangräns mot ESBO, andra kända nätverk, värdekärnor och regionala samband ingår inte; "
            "Floden läser inga länsstyrelse- eller kommunlager och bedömer inte hur ingående "
            "utredningen behöver vara.",
            (_EKO_NV,),
            "underlag_saknas",
        ),
        "EK-02": Formaga(
            "delvis",
            "Cellstorlek och längsta spridningströskel (parametrar). Kontextbufferten är "
            "tröskeln, högst 5 km, när markunderlaget läses ur geografin.",
            "Fläckar utanför markunderlaget ingår inte; bufferten är ett val i körningen. Båda "
            "ändarna av sambandet, alternativa vägar och angränsande planer redovisas inte.",
            (_EKO_NV,),
            "underlag_saknas",
        ),
        "EK-03": Formaga(
            "kan",
            "Signalart eller artgrupp, källa med sida och om habitatdefinitionen är ett "
            "antagande (ur nätverkets fil i körningen).",
            "Valet av signalart för en annan plats, och vilka andra arter eller funktioner som "
            "behöver egen bedömning, är ett val i utredningen, inte en beräkning.",
            (_EKO_NV,),
            "visat",
        ),
        "EK-04": Formaga(
            "saknas",
            "",
            "Fältinventering, habitatkvalitet, barriärer som markunderlaget saknar och "
            "förekomst av arterna ingår inte i Floden.",
            (),
            "underlag_saknas",
        ),
        "EK-05": Formaga(
            "saknas",
            "",
            "Vilka förbud, föreskrifter och tillståndsfrågor som gäller är en bedömning i "
            "ärendet; Floden läser inga artskydds- eller områdesskyddsuppgifter.",
            (),
            "ej_tillampligt",
        ),
        "EK-06": Formaga(
            "delvis",
            "Habitatklasser i markunderlaget (NMD), minsta fläckarea, antal fläckar och "
            "total habitatarea. Resultatet kallas potentiell livsmiljö, okänd kvalitet.",
            "Ålder, struktur och naturvärde finns inte i NMD; verklig kvalitet är okänd. "
            "Enskilda träd och små objekt har ingen egen identifierare och kan försvinna vid "
            "rasterisering.",
            (_EKO_MV, _EKO_NV),
            "underlag_saknas",
        ),
        "EK-07": Formaga(
            "delvis",
            "Spridningströskel och d50 (med antagandet d50 = tröskel/2 när p saknas i "
            "källan) samt motstånd per markslag med källa och säkerhet.",
            "Mätt spridningssannolikhet för platsens arter saknas när källan inte anger den. "
            "Murar, öppningar och absoluta barriärer behandlas bara som markslag i "
            "markunderlaget, inte som egna objekt.",
            (_EKO_NV,),
            "underlag_saknas",
        ),
        "EK-08": Formaga(
            "kan",
            "ECA, PC och antal habitatfläckar före och efter, skillnaden (ΔECA) och "
            "intervallet mellan källans tröskelscenarier.",
            "Resultatet gäller potentiell livsmiljö i det påhittade eller inlästa "
            "markunderlaget, inte verkliga arters förekomst. Framtid utan projekt är ett eget "
            "scenario som bara finns om det lagts in.",
            (_EKO_MV, _EKO_JF, _EKO_NV),
            "visat",
        ),
        "EK-09": Formaga(
            "saknas",
            "",
            "Andra planer och program läses inte; projektets påverkan tillsammans med andra "
            "utbyggnader räknas bara om de lagts in som scenario (K9). Hur förslaget förhåller "
            "sig till kommunens och länets planer är en bedömning i ärendet.",
            (),
            "underlag_saknas",
        ),
        "EK-10": Formaga(
            "delvis",
            "Karta över strömtäthet (flaskhalsar) före, efter och som skillnad, med samma "
            "skala, samt antal habitatfläckar och total habitatarea före och efter.",
            "Strömtätheten är normaliserad (0-1) och ger inga tal per flaskhals; dPC per fläck "
            "redovisas inte som tal. Vilken enskild livsmiljö eller passage som bär förändringen, "
            "och en alternativ väg, pekas inte ut av Floden.",
            (_EKO_MV, _EKO_JF),
            "underlag_saknas",
        ),
        "EK-11": Formaga(
            "delvis",
            "Källbaserat intervall för ECA och PC mellan nätverkets tröskelscenarier, före "
            "och efter.",
            "Intervallet täcker källans tröskelscenarier, inte all osäkerhet i habitatval, "
            "motstånd och underlagets kant.",
            (_EKO_MV,),
            "underlag_saknas",
        ),
        "EK-12": Formaga(
            "delvis",
            "Jämförelse av prövade alternativ (varje scenario ställs mot nuläget) med samma "
            "mått: ECA, PC och antal habitatfläckar.",
            "Floden föreslår inget alternativ; flyttad eller minskad byggrätt och bevarade "
            "värdträd prövas bara om de lagts in som scenario. Återstående förlust och "
            "kompensation bedöms inte.",
            (_EKO_MV, _EKO_JF),
            "underlag_saknas",
        ),
        "EK-13": Formaga(
            "saknas",
            "",
            "Hur en skydds- eller kompensationsåtgärd fungerar, markåtkomst, drift och "
            "uppföljning är genomförandefrågor som avgörs utanför beräkningen.",
            (),
            "ej_tillampligt",
        ),
        "EK-14": Formaga(
            "delvis",
            "Cellstorlek och antal fläckar; nätverkets och markunderlagets kontrollsummor "
            "står i körningens manifest (bilagan).",
            "Kontrollsummorna är fakta i bilagan, inte tal som rapporten refererar. Skyddsklassade "
            "fynduppgifter hanteras inte; rapporten är öppen (dataklass oppet).",
            (_EKO_NV,),
            "underlag_saknas",
        ),
        "EK-15": Formaga(
            "saknas",
            "",
            "Vilka slutsatser som stöds av observation, beräknat samband respektive sakkunnig "
            "bedömning är en bedömning i ärendet; beräkningen är en modell för potentiell "
            "livsmiljö.",
            (),
            "ej_tillampligt",
        ),
    }
)

_LU_MV = "matvarde.<analys>.<scenario>.<mått>"
FLODEN_FORMAGA.update(
    {
        "LU-01": Formaga(
            "saknas",
            "",
            "Vilka miljökvalitetsnormer som behöver prövas, normtyp, lagrum och beslutsdatum är en bedömning i ärendet. "
            "Underlaget visar bara PM10 mot dygnsnormen (högsta antal dygn över gränsen) ur en given serie; "
            "kvävedioxid, PM2,5, bensen och årsmedelnormen prövas inte.",
            (),
            "ej_tillampligt",
        ),
        "LU-02": Formaga(
            "delvis",
            "Bakgrundshalten (PM10, konstant) som lagts till serien och antalet hela dygn i serien "
            "(parametrar med belägg).",
            "Närmaste representativa station, observationsår, datatäckning, normalår och upplösning, och hur bakgrunden "
            "skiljer sig från projektets gaturum. Floden läser inga stationsdata eller SLB-lager; bakgrunden är en siffra "
            "som den som angav den ansvarar för.",
            (_LU_MV,),
            "underlag_saknas",
        ),
        "LU-03": Formaga(
            "delvis",
            "Intervallet för antal dygn över gränsen, årsmedel och 90,4-percentil före och efter, ur två serier med samma "
            "bakgrund, faktorintervall och gräns (jämförbarheten kontrolleras).",
            "Nuläge, framtid utan plan och med plan med jämförbara trafik- och bakgrundsantaganden för samma prognosår: "
            "serierna kommer från ett annat verktyg och Floden kan inte se vilka antaganden de bygger på. Framtid utan plan "
            "är en tredje serie som bara finns om den lagts in. Före och efter kräver två serier.",
            (_LU_MV,),
            "underlag_saknas",
        ),
        "LU-04": Formaga(
            "saknas",
            "",
            "Trafik per länk, fordonsmix, hastighet, emissionsfaktorer, avgaser, slitage och uppvirvling ingår inte: serien "
            "är given. Emissionsmodellen i luftpaketet (L-g) har ingen körning med belägg i rapporten och väntar på trafik- "
            "och mätdata.",
            (),
            "underlag_saknas",
        ),
        "LU-05": Formaga(
            "delvis",
            "Bakgrunden (konstant, parameter), faktorintervallen för källstyrka och deposition och antalet hela dygn i serien.",
            "Modell och version, meteorologisk period, vind, gaturummets bredd och höjd, emissionsår, regional eller urban "
            "bakgrund och hur dubbelräkning undviks: serien är räknad utanför Floden och bakgrunden är en enda konstant.",
            (_LU_MV,),
            "underlag_saknas",
        ),
        "LU-06": Formaga(
            "saknas",
            "",
            "Lösaren luft.mkn har ingen geometri och ingen spridningsberäkning, så sektion, beräkningsdomän och valet mellan "
            "gaturumsmodell, lokal 3D-studie och mätning redovisas inte. 3D-modellen (luft.halt, luft.vindkomfort) har ingen "
            "rapportväg än.",
            (),
            "underlag_saknas",
        ),
        "LU-07": Formaga(
            "saknas",
            "",
            "Receptorer, höjder och undantagna ytor ingår inte: serien gäller en punkt vars läge körningen inte anger, och "
            "luft-MKN har ingen karta.",
            (),
            "underlag_saknas",
        ),
        "LU-08": Formaga(
            "kan",
            "Antal dygn över dygnsgränsen (lägst och högst i intervallet), årsmedel och 90,4-percentil för PM10, med härledning: "
            "kalenderdygn som medel av 24 timmar, alla hela dygn i serien, gräns och högsta antal som parametrar.",
            "Svensk NO₂ (båda timvillkoren), PM2,5 och andra ämnen räknas inte. För PM10 gäller dygnsstatistiken bara om "
            "serien är ett helt år (annars varnar lösaren och antalet gäller perioden).",
            (_LU_MV,),
            "visat",
        ),
        "LU-09": Formaga(
            "saknas",
            "",
            "Normbedömning och målbedömning (miljö- och hälsomål, WHO) görs inte: nivå 1 jämför inte mot normer, och inga mål "
            "eller alternativa placeringar av lek eller vistelse ingår.",
            (),
            "underlag_saknas",
        ),
        "LU-10": Formaga(
            "delvis",
            "Intervallet för antal dygn och årsmedel ur faktorerna för källstyrka och för utspädning eller deposition, och om "
            "intervallet ligger helt under, helt över eller korsar högsta antalet dygn.",
            "Variation av trafik, flottans utsläpp, dubbdäck och bakgrund, och jämförelse mot representativa observationer "
            "ingår inte. Intervallet är en känslighet för två faktorer, inte en statistisk konfidens.",
            (_LU_MV,),
            "underlag_saknas",
        ),
        "LU-11": Formaga(
            "delvis",
            "Jämförelse av prövade alternativ (varje serie ställs mot nuläget) med samma mått: intervallet för antal dygn och "
            "årsmedel.",
            "Floden föreslår ingen åtgärd; ändrad trafik, gatugeometri, placering av vistelseyta eller drift prövas bara om en "
            "serie per alternativ lagts in som analys. Effekt vid samma mottagare och förskjuten exponering redovisas inte.",
            (_LU_MV,),
            "underlag_saknas",
        ),
        "LU-12": Formaga(
            "saknas",
            "",
            "Byggdamm och arbetsmaskiner, arbetsmoment, transportvägar och skyddsåtgärder ingår inte; serien gäller driftläget.",
            (),
            "underlag_saknas",
        ),
        "LU-13": Formaga(
            "saknas",
            "",
            "Vilken av länsstyrelsens fem situationer planen träffar är en bedömning i ärendet; Floden läser inte planens "
            "trafikalstring, känsliga användning, vägplanering eller verksamheter.",
            (),
            "ej_tillampligt",
        ),
        "LU-14": Formaga(
            "saknas",
            "",
            "Gårdsgräns, vistelseytor och möjliga placeringar ingår inte: serien gäller en punkt, och källår för kartläggning "
            "och mål om låg halt redovisas inte.",
            (),
            "underlag_saknas",
        ),
        "LU-15": Formaga(
            "delvis",
            "Varje tal har belägg med körning, fil och kontrollsumma, tidsdefinition och statistik; serie, bakgrund och faktorer "
            "står i antagandena och bilagorna.",
            "Station eller beräknad mottagare, tidsmått och år knyts inte till en figur: luft-MKN har ingen karta, och serien "
            "anger varken plats eller år.",
            (_LU_MV,),
            "underlag_saknas",
        ),
    }
)


def _fraga(data: Any, nr: int) -> Fraga:
    if not isinstance(data, dict):
        raise FragekatalogFel(f"fragor[{nr}] är inte ett objekt.")
    try:
        return Fraga(
            id=str(data["id"]),
            omrade=str(data["omrade"]),
            typ=str(data["typ"]),
            villkor=str(data["villkor"]),
            fraga=str(data["fraga"]),
            redovisning=str(data["redovisning"]),
            kallor=tuple(str(k) for k in data.get("kallor", [])),
        )
    except KeyError as exc:
        raise FragekatalogFel(f"fragor[{nr}] saknar fältet {exc}.") from exc


@dataclass(frozen=True)
class Fragekatalog:
    """Katalogens frågor och källor, med sökning."""

    fragor: tuple[Fraga, ...]
    kallor: dict[str, dict[str, Any]]
    granskningsdatum: str
    fil: str

    @classmethod
    def las(cls, fil: str | Path = STANDARD_KATALOG) -> Fragekatalog:
        sokvag = Path(fil)
        try:
            data = json.loads(sokvag.read_text(encoding="utf-8"))
        except OSError as exc:
            raise FragekatalogFel(f"Frågekatalogen kan inte läsas: {sokvag} ({exc}).") from exc
        except ValueError as exc:
            raise FragekatalogFel(f"Frågekatalogen är inte giltig JSON: {exc}.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("fragor"), list):
            raise FragekatalogFel("Frågekatalogen saknar listan 'fragor'.")
        fragor = tuple(_fraga(f, i) for i, f in enumerate(data["fragor"]))
        ids = [f.id for f in fragor]
        if len(set(ids)) != len(ids):
            raise FragekatalogFel("Frågekatalogen har dubbla id.")
        return cls(
            fragor=fragor,
            kallor=dict(data.get("kallor", {})),
            granskningsdatum=str(data.get("granskningsdatum", "")),
            fil=str(sokvag),
        )

    @classmethod
    def las_alla(cls, fil: str | Path = STANDARD_KATALOG) -> Fragekatalog:
        """Samma som `las`: huvudkatalogen `fragor.json` är den enda källan (även EK-01 till EK-15).

        Metoden finns kvar för anropare som skrevs när ekologifrågorna låg i en egen fil."""
        return cls.las(fil)

    def hamta(self, fraga_id: str) -> Fraga:
        for f in self.fragor:
            if f.id == fraga_id:
                return f
        raise FragekatalogFel(f"Frågan '{fraga_id}' finns inte i katalogen.")

    def sok(
        self,
        *,
        omrade: str | None = None,
        villkor: Iterable[str] | None = None,
        text: str | None = None,
        ids: Iterable[str] | None = None,
    ) -> list[Fraga]:
        """Frågor som matchar alla angivna filter.

        `omrade`: exakt (t.ex. `skyfall`). `villkor`: frågan tas med om dess villkor innehåller
        någon av texterna (gemener), eller är "Alla ..." (gäller alltid). `text`: delsträng i
        frågan eller redovisningen. `ids`: bara dessa id.
        """
        if omrade is not None:
            omrade = OMRADESALIAS.get(omrade, omrade)
        villkor_l = [v.strip().lower() for v in villkor or [] if v.strip()]
        text_l = text.strip().lower() if text else None
        id_set = set(ids) if ids is not None else None
        ut: list[Fraga] = []
        for f in self.fragor:
            if omrade is not None and f.omrade != omrade:
                continue
            if id_set is not None and f.id not in id_set:
                continue
            if villkor_l:
                fv = f.villkor.lower()
                if not (fv.startswith("alla") or any(v in fv for v in villkor_l)):
                    continue
            if text_l and text_l not in f.fraga.lower() and text_l not in f.redovisning.lower():
                continue
            ut.append(f)
        return ut

    def for_amne(self, amne: str, villkor: Iterable[str] | None = None) -> list[Fraga]:
        """Frågorna för ett rapportämne (`dagsljus` = DL- och SO-frågorna), i katalogens ordning."""
        omraden = AMNESOMRADEN.get(OMRADESALIAS.get(amne, amne))
        if omraden is None:
            raise FragekatalogFel(
                f"Okänt ämne '{amne}'. Ämnen: {', '.join(AMNESOMRADEN)} (och 'sol')."
            )
        ut: list[Fraga] = []
        for o in omraden:
            ut += self.sok(omrade=o, villkor=villkor)
        return ut

    def kalla_text(self, nyckel: str) -> str:
        k = self.kallor.get(nyckel)
        return str(k.get("titel", nyckel)) if isinstance(k, dict) else nyckel


def formaga_for(fraga_id: str) -> Formaga | None:
    """Flodens förmåga för frågan, eller None om den inte är kartlagd (andra ämnen än skyfall)."""
    return FLODEN_FORMAGA.get(fraga_id)


def formagetabell_markdown(amne: str = "skyfall") -> str:
    """Tabellen över ämnets frågor som den står i `docs/RAPPORTER.md`."""
    katalog = Fragekatalog.las_alla()
    rader = [
        "| Fråga | Floden i dag | Belägg Floden kan ge | Saknas och varför | Status i demon |",
        "| --- | --- | --- | --- | --- |",
    ]
    status_text = {
        "visat": "visat",
        "villkorat": "villkorat",
        "underlag_saknas": "underlag saknas",
        "ej_tillampligt": "ej tillämpligt",
        "avvikelse": "avvikelse",
    }
    for f in katalog.for_amne(amne):
        fm = FLODEN_FORMAGA.get(f.id)
        if fm is None:
            continue
        rader.append(
            f"| {f.id} {f.fraga} | {MARKNING_TEXT[fm.markning]} | {fm.belagg or '–'} | "
            f"{fm.saknat} | {status_text[fm.forval_status]} |"
        )
    return "\n".join(rader) + "\n"


def fraga_med_formaga(f: Fraga) -> dict[str, Any]:
    """Frågan som JSON (API och MCP): katalogens fält och vad Floden kan ge i dag (om kartlagt)."""
    post = f.till_dict()
    fm = formaga_for(f.id)
    post["floden"] = (
        None
        if fm is None
        else {
            "markning": fm.markning,
            "niva": fm.niva,
            "belagg": fm.belagg,
            "saknat": fm.saknat,
            "adresser": list(fm.adresser),
            "status_i_demon": fm.forval_status,
        }
    )
    return post


__all__ = [
    "AMNESOMRADEN",
    "AMNESPREFIX",
    "FLODEN_FORMAGA",
    "FLODEN_MARKNING",
    "MARKNING_TEXT",
    "OMRADESALIAS",
    "STANDARD_KATALOG",
    "Formaga",
    "Fraga",
    "Fragekatalog",
    "FragekatalogFel",
    "formaga_for",
    "formagetabell_markdown",
    "fraga_med_formaga",
]
