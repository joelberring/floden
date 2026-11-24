# ruff: noqa: E501
"""Kontroll inför export av ett skissunderlag (rapportstod.md, tabellen "Kontroll inför export").

Deterministisk: inga modeller, inga uppskattningar. Varje kontroll ger en punkt med id, rubrik,
godkänd och felmeddelanden. **Ett enda fel stoppar exporten** (`rapport.skissexport.ExportStoppad`).

| Punkt | Vad som kontrolleras med dagens data |
| --- | --- |
| `sparbarhet` | Varje belägg hämtas på nytt ur Floden: samma värde (före avrundning), samma sha256 för resultatfil och K0-manifest. Inga siffror utanför platshållare. Kartorna ritas om och ska ge samma PNG (sha256). |
| `scenario` | Jämförelser och påståenden bygger på scenarier med samma analysparametrar (regn, avtappning, takvatten); ingen bedömning mot regel i nivå 1. |
| `mottagare` | Ett påstående gäller en nämnare (hela området på mark, en namngiven mottagare, en körning); områdesbelägg pekar på rapportens mottagare. |
| `matt` | Storhet, tidsdefinition, statistik och enhet finns; platshållarens enhet och avrundning följer belägget; ett maxdjup kallas inte vattennivå, ett största värde inte medelvärde. Luft: ett påstående om att normen klaras eller överskrids kräver att intervallet ligger helt under respektive över högsta antalet dygn; korsar det gränsen stoppas påståendet. |
| `osakerhet` | Antagandena (regn, takvatten, avtappning) finns med; skissmetoder beskrivs inte som verifierade; villkorade påståenden har förutsättning och ansvarig. Luft: serien är extern, bakgrunden ett antagande, intervallet en känslighet och före/efter två serier. |
| `karta` | Teckenförklaring, enhet, tomvärde, norr, skalstock och scenario i bildtexten; före och efter har samma färgskala; filen är oförändrad. |
| `status` | Statusreglerna; varje fråga i rapporten har minst ett påstående; nivå 1 är sållning (inga lämplighets- eller kravformuleringar). |
| `dataskydd` | Rapportens dataklass är `oppet`; inga e-postadresser eller personnummer i texterna. |
| `metodstatus` | Varje lösare som ett belägg kommer ur har sin valideringsstatus i rapporten, oförändrad sedan rapporten skapades, och den syns i dokumentet. |
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from gemensamt.hash import sha256_fil
from rapport.belaggskalla import BelaggFel
from rapport.pastaende import (
    Belagg,
    Pastaende,
    PastaendeFel,
    statusfel,
    text_utan_platshallare,
    textfel,
)

if TYPE_CHECKING:
    from rapport.skissrapport import Rapport, RapportTjanst

PUNKTER: Final[tuple[tuple[str, str], ...]] = (
    ("sparbarhet", "Spåra tal och figurer"),
    ("scenario", "Scenario och regelår"),
    ("mottagare", "Mottagare och nämnare"),
    ("matt", "Mått och tidsstatistik"),
    ("osakerhet", "Osäkerhet"),
    ("karta", "Kartan"),
    ("status", "Status per fråga"),
    ("dataskydd", "Dataskydd"),
    ("metodstatus", "Metodernas valideringsstatus"),
)

_PARAMETERSTAT: Final[tuple[str, ...]] = ("parameter i måttets definition", "parameter (indata)")
"""Beläggens statistik för parametrar i dagsljusmåtten: de gäller alla nämnare."""
_NIVA1_FORBJUDET: Final[tuple[str, ...]] = (
    "uppfyller kravet",
    "uppfyller kraven",
    "klarar kravet",
    "klarar kraven",
    "klarar riktvärdet",
    "är godkänd",
    "godkänns",
    "marken är lämplig",
    "lämplig för bebyggelse",
    "är lämplig",
    "är olämplig",
    "inga risker",
    "ingen risk",
)
_SAKERHETSORD: Final[tuple[str, ...]] = (
    "verifierad",
    "verifierat",
    "säkerställd",
    "säkerställt",
    "bekräftad",
    "bekräftat",
    "validerad",
    "validerat",
)
_NIVAORD: Final[re.Pattern[str]] = re.compile(r"\b(vattennivå\w*|vattenyta\w*|nivå\w*)\b", re.I)
_MEDELORD: Final[re.Pattern[str]] = re.compile(r"\b(medel\w*|genomsnitt\w*|i snitt)\b", re.I)
_MAXORD: Final[re.Pattern[str]] = re.compile(r"\b(störst\w*|högst\w*|max\w*)\b", re.I)
_EPOST: Final[re.Pattern[str]] = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PERSONNR: Final[re.Pattern[str]] = re.compile(r"\b(?:19|20)?\d{6}[-+]\d{4}\b")


def _pid(p: Pastaende) -> str:
    return f"{p.pastaende_id} ({p.fraga_id})"


# ---------------------------------------------------------------------- per påstående


def mattfel(p: Pastaende) -> list[str]:
    fel: list[str] = []
    text = text_utan_platshallare(p.text)
    for b in p.belagg:
        for falt in ("storhet", "tidsdefinition", "statistik"):
            if not str(getattr(b, falt)).strip():
                fel.append(f"{_pid(p)}: belägget {b.adress} saknar {falt}.")
        if b.upplosning <= 0 or not math.isfinite(b.upplosning):
            fel.append(f"{_pid(p)}: belägget {b.adress} saknar upplösning (precision).")
    if any("vattendjup" in b.storhet for b in p.belagg) and _NIVAORD.search(text):
        ord_ = _NIVAORD.search(text)
        fel.append(
            f"{_pid(p)}: texten säger '{ord_.group(0) if ord_ else ''}' men belägget är ett "
            "vattendjup över markytan, inte en vattennivå i RH 2000."
        )
    stat = " ".join(b.statistik for b in p.belagg)
    if ("största" in stat or "max" in stat) and "medel" not in stat and _MEDELORD.search(text):
        fel.append(f"{_pid(p)}: texten talar om medelvärde, men beläggen är största värden (max).")
    if "medelvärde" in stat and "största" not in stat and _MAXORD.search(text):
        fel.append(f"{_pid(p)}: texten talar om största värde, men belägget är ett medelvärde.")
    fel += dagsljusfel(p, text)
    fel += ekologifel(p, text)
    fel += luftfel(p, text)
    try:
        p.fylld_text()
    except PastaendeFel as exc:
        fel.append(f"{_pid(p)}: {exc}")
    return fel


# "dagsljusfaktor" (även med stor bokstav) om ett VSC-belägg, utom när texten säger att den inte är
# beräknad ("inte dagsljusfaktor", "ingen dagsljusfaktor").
_DF_ORD: Final[re.Pattern[str]] = re.compile(
    r"(?<!inte )(?<!ingen )(?<!Inte )(?<!Ingen )\b(dagsljusfaktor\w*|Dagsljusfaktor\w*|DF)\b"
)
_FASADSOL_ORD: Final[re.Pattern[str]] = re.compile(
    r"\b(sol|soltimmar|solljus)\s+(på|vid|mot|i)\s+(fasad\w*|fönster\w*|rum\w*)", re.I
)
_SAMMANHANGANDE_ORD: Final[re.Pattern[str]] = re.compile(r"\bsammanhängande\b", re.I)
_VERKLIGA_FONSTER: Final[re.Pattern[str]] = re.compile(
    r"\b(grannens|grannarnas|bostadens|lägenhetens)\s+fönster", re.I
)


def dagsljusfel(p: Pastaende, text: str) -> list[str]:
    """Mått och tidsstatistik för dagsljus: VSC är ingen dagsljusfaktor, soltimmar gäller marken
    och är en summa (inte sammanhängande tid), fasadpunkter är inga verkliga fönster."""
    fel: list[str] = []
    vsc = any(b.matt.startswith("vsc:") for b in p.belagg)
    sol = any(b.losare == "dagsljus.soltimmar_mark" and "soltim" in b.matt for b in p.belagg)
    if vsc and (m := _DF_ORD.search(text)):
        fel.append(
            f"{_pid(p)}: texten säger '{m.group(0)}' men belägget är vertikal himmelskomponent "
            "(VSC) vid fasadpunkt, inte dagsljusfaktor i rum."
        )
    if vsc and (m := _VERKLIGA_FONSTER.search(text)):
        fel.append(
            f"{_pid(p)}: texten säger '{m.group(0)}' men fasadpunkterna är genererade punkter, "
            "inte verkliga fönster."
        )
    if sol and (m := _FASADSOL_ORD.search(text)):
        fel.append(
            f"{_pid(p)}: texten säger '{m.group(0)}' men soltimmarna gäller markytan, inte "
            "fasad eller fönster."
        )
    if sol and _SAMMANHANGANDE_ORD.search(text):
        fel.append(
            f"{_pid(p)}: soltimmarna är en summa per dygn, inte sammanhängande tid."
        )
    return fel


_LIVSMILJO: Final[re.Pattern[str]] = re.compile(r"(?<!potentiell )(?<!Potentiell )\blivsmilj\w*", re.I)
_EKO_FORBJUDET: Final[tuple[str, ...]] = (
    "arten förekommer",
    "arterna förekommer",
    "arten finns",
    "arterna finns",
    "häckar i",
    "livskraftig",
    "livskraftiga",
    "hög kvalitet",
    "god kvalitet",
    "högt naturvärde",
    "värdefull livsmiljö",
    "säkrar spridningen",
    "säkrat samband",
    "sambandet är säkrat",
    "sambandet bevaras",
)


_EKO_BORTTAGNING: Final[re.Pattern[str]] = re.compile(
    r"\b(tas bort|tas ned|tagits bort|avverkas|fälls|försvinner|går förlorad\w*|förloras|kopplas bort|"
    r"kapas|bryts|isoleras|kopplar ihop|kopplas ihop|förbinder)\b|\bdungen\b",
    re.I,
)
_EKO_KONTAKTMATT: Final[re.Pattern[str]] = re.compile(r"\.(eca_m2|pc|delta_eca_m2|delta_eca_procent|delta_pc)$")
_EKO_ALTERNATIV: Final[re.Pattern[str]] = re.compile(r"\balternativ\w* (väg|vägar|passage\w*|förbindelse\w*)\b|\bomväg\w*", re.I)


def ekologifel(p: Pastaende, text: str) -> list[str]:
    """Ekologi (skiss): NMD ger potentiell livsmiljö med okänd kvalitet; inget om verklig förekomst."""
    if not any(b.losare == "ekologi.konnektivitet" for b in p.belagg):
        return []
    fel: list[str] = []
    if m := _LIVSMILJO.search(text):
        fel.append(
            f"{_pid(p)}: texten säger '{m.group(0)}' om ett habitat ur NMD. Skriv 'potentiell "
            "livsmiljö, okänd kvalitet': NMD säger inget om ålder, struktur eller naturvärde."
        )
    low = text.lower()
    for fras in _EKO_FORBJUDET:
        if fras in low:
            fel.append(
                f"{_pid(p)}: '{fras}' är en slutsats om verklig förekomst eller kvalitet. "
                "Beräkningen visar potentiell livsmiljö och en modell för spridning, inte arternas förekomst."
            )
    fel += _borttagningsfel(p, text)
    return fel


_NORMORD: Final[str] = r"(?:mkn|miljökvalitetsnorm\w*|normen|normerna|dygnsnormen|årsnormen|gränsvärde\w*|mkn-gräns\w*)"
_KLARAR_VERB: Final[str] = r"(?:klarar|klaras|klarade|uppfyller|uppfylls|innehålls|underskrider|underskrids|understiger)"
_OVERSKRIDER_VERB: Final[str] = r"(?:överskrider|överskrids|överskred|överstiger|överträder|överträds|bryter mot)"
_LUFT_PAASTAENDE: Final[re.Pattern[str]] = re.compile(
    rf"\b({_KLARAR_VERB}|{_OVERSKRIDER_VERB})\b", re.I
)
_LUFT_NEGATION: Final[re.Pattern[str]] = re.compile(r"\b(inte|ej|icke|aldrig|knappast)\b", re.I)
_LUFT_FORBEHALL: Final[re.Pattern[str]] = re.compile(
    r"\b(om|huruvida|ifall|vare sig|varken|oklart|oavgjort|avgör\w*|prövas|prövar|bör prövas|ska prövas|kan ingen säga|går inte att säga|"
    r"kan inte (?:säga|avgöra|bedöma)|går inte att (?:säga|avgöra|bedöma))\b",
    re.I,
)
_LUFT_BERAKNAT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:beräknad\w*|framräknad\w*|modellerad\w*)\s+(?:lokal\w*\s+)?(?:halt\w*|spridning\w*)\b|"
    r"\b(?:halt\w*|spridning\w*|haltbidrag\w*)\s+(?:är\s+|har\s+)?(?:beräknad\w*|beräknats|framräknad\w*|modellerad\w*|modellerats)\b|"
    r"\bfloden\s+(?:har\s+|kan\s+)?(?:beräknat|beräknar|räknat|räknar|modellerat|modellerar)\s+(?:ut\s+)?(?:halt\w*|spridning\w*|haltbidrag\w*)\b",
    re.I,
)
_LUFT_EXTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(inte|ej|ingen|extern\w*|given\w*|utanför floden|annat verktyg|annan modell)\b", re.I
)


def _meningar(text: str) -> list[str]:
    return [m.strip() for m in re.split(r"(?<=[.!?])\s+|\n+", text) if m.strip()]


def luftfel(p: Pastaende, text: str) -> list[str]:
    """Luft (skiss): MKN-intervallet är en känslighet för två faktorer ur en given serie och en antagen bakgrund.

    Ett påstående om att normen klaras eller överskrids kräver att intervallet (lägst, högst och högsta tillåtna
    antal dygn, alla tre som belägg i påståendet) ligger helt under respektive helt över gränsen och att texten
    säger att det gäller i intervallet. Korsar intervallet gränsen stoppas påståendet. Haltbidraget är en given
    serie: texten får inte säga att Floden har beräknat halten eller spridningen, och intervallet är ingen konfidens."""
    if not any(b.losare == "luft.mkn" for b in p.belagg):
        return []
    from rapport.luftbelagg import intervallrelation

    fel: list[str] = []
    ren = text_utan_platshallare(text)
    for mening in _meningar(ren):
        if (m := _LUFT_BERAKNAT.search(mening)) and not _LUFT_EXTERN.search(mening):
            fel.append(
                f"{_pid(p)}: texten säger '{m.group(0)}', men det lokala haltbidraget är en given timserie som "
                "har räknats fram utanför Floden. Floden har inte beräknat halten eller spridningen."
            )
    low = ren.lower()
    if "konfidens" in low and not re.search(r"\b(inte|ingen|ej)\b[^.]{0,60}konfidens", low):
        fel.append(
            f"{_pid(p)}: intervallet är en känslighet för två faktorer, inte en statistisk konfidens. "
            "Skriv att det inte är en konfidens, eller använd ordet känslighet."
        )
    relation = intervallrelation(p.belagg)
    for mening in _meningar(ren):
        if not re.search(_NORMORD, mening, re.I):
            continue
        verb = _LUFT_PAASTAENDE.search(mening)
        if verb is None or _LUFT_FORBEHALL.search(mening):
            continue
        negerad = bool(_LUFT_NEGATION.search(mening))
        klarar_verb = re.fullmatch(_KLARAR_VERB, verb.group(1), re.I) is not None
        pastar = "klarar" if klarar_verb != negerad else "overskrids"
        pastar_text = "att normen klaras" if pastar == "klarar" else "att normen överskrids"
        if re.search(r"årsmedel|årsnorm", mening, re.I):
            fel.append(
                f"{_pid(p)}: '{mening}' jämför årsmedel mot en norm, men körningen har ingen gräns för årsmedel "
                "(luft.mkn prövar dygnsgränsen och högsta antal dygn). Skriv årsmedlet som ett intervall utan jämförelse."
            )
            continue
        luft = relation
        if not luft or any(v is None for v in luft.values()):
            fel.append(
                f"{_pid(p)}: texten säger {pastar_text}, men påståendets belägg visar inte intervallet mot högsta "
                "antalet dygn (lägst och högst antal dygn samt högsta tillåtna antal behövs som belägg)."
            )
            continue
        korsar = sorted(f"{a}/{s}" for (a, s), v in luft.items() if v == "korsar")
        if korsar:
            fel.append(
                f"{_pid(p)}: texten säger {pastar_text}, men intervallet korsar gränsen för {', '.join(korsar)}: "
                "antal dygn över gränsen ligger både under och över högsta antalet i intervallet. Skriv att "
                "underlaget varken kan säga att normen klaras eller att den överskrids, och att frågan behöver utredas vidare."
            )
            continue
        motsatt = sorted(
            f"{a}/{s}" for (a, s), v in luft.items() if v == ("over" if pastar == "klarar" else "under")
        )
        if motsatt:
            fel.append(
                f"{_pid(p)}: texten säger {pastar_text}, men beläggen för {', '.join(motsatt)} visar motsatsen "
                "(intervallet ligger helt på andra sidan om högsta antalet dygn)."
            )
            continue
        if not re.search(r"intervall", ren, re.I):
            fel.append(
                f"{_pid(p)}: texten säger {pastar_text} utan att säga att det gäller i hela intervallet (givet "
                "bakgrunden och den givna serien). Skriv det, eller skriv bara hur intervallet förhåller sig "
                "till högsta antalet dygn."
            )
    return fel


def _borttagningsfel(p: Pastaende, text: str) -> list[str]:
    """Kontrollfallet i `docs/utredningar-stockholm/ekologi.md`: en dunge tas bort och underlaget är en habitatkarta.

    Ett påstående om vad som händer när ett habitat tas bort eller en förbindelse bryts kräver
    kontakten (ECA eller PC) före och efter, eller en jämförelse av dem, och att en alternativ väg
    redovisas (finns den inte i underlaget ska texten säga det). Med bara habitatkartan
    (fläckar och area) får påståendet säga att planen berör ett modellerat samband."""
    m = _EKO_BORTTAGNING.search(text_utan_platshallare(text))
    if m is None:
        return []
    kontakt = [b for b in p.belagg if _EKO_KONTAKTMATT.search(b.adress)]
    fore_efter = any(b.adress.startswith("ekojamforelse.") for b in kontakt) or (
        len({s for b in kontakt for s in b.scenarier}) >= 2
    )
    fel: list[str] = []
    if not fore_efter:
        fel.append(
            f"{_pid(p)}: texten säger '{m.group(0)}', men beläggen visar inte kontakten (ECA eller PC) "
            "före och efter. Med bara habitatkartan kan rapporten säga att planen berör ett modellerat "
            "samband; frågan om förändrad konnektivitet får status underlag saknas."
        )
    if not _EKO_ALTERNATIV.search(text):
        fel.append(
            f"{_pid(p)}: texten säger '{m.group(0)}' utan att redovisa en alternativ väg. Redovisa "
            "alternativa vägar för arten, eller skriv att de inte är undersökta."
        )
    return fel


def mottagarfel(p: Pastaende, r: Rapport) -> list[str]:
    fel: list[str] = []
    # parametrar (BRE:s gräns, datum) är indata till måttet och gäller alla nämnare
    namnare = sorted({b.namnare for b in p.belagg if not b.statistik.startswith(_PARAMETERSTAT)})
    if len(namnare) > 1:
        fel.append(
            f"{_pid(p)}: beläggen gäller olika nämnare ({'; '.join(namnare)}). Ett påstående "
            "ska gälla en mottagare och en nämnare; dela upp det i flera påståenden."
        )
    for b in p.belagg:
        if b.adress.startswith("omrade."):
            namn = b.adress.split(".")[1]
            if namn not in r.mottagare_per_namn and namn != "hela_omradet":
                fel.append(f"{_pid(p)}: mottagaren '{namn}' finns inte i rapporten.")
            if p.mottagare and p.mottagare != namn:
                fel.append(
                    f"{_pid(p)}: påståendet anger mottagaren '{p.mottagare}', men belägget "
                    f"{b.adress} gäller '{namn}'."
                )
    if p.mottagare and p.mottagare not in r.mottagare_per_namn and p.mottagare != "hela_omradet":
        fel.append(
            f"{_pid(p)}: mottagaren '{p.mottagare}' finns inte i rapporten (eller 'hela_omradet')."
        )
    return fel


def scenariofel(p: Pastaende, r: Rapport) -> list[str]:
    fel: list[str] = []
    for b in p.belagg:
        if b.jamforbarhet.startswith("EJ_JAMFORBAR"):
            fel.append(
                f"{_pid(p)}: {b.adress} jämför scenarier med olika analysparametrar "
                f"({b.jamforbarhet.removeprefix('EJ_JAMFORBAR:')}); samma regn, domän och villkor "
                "krävs för en jämförelse."
            )
    per_analys: dict[str, set[str]] = {}
    for b in p.belagg:
        per_analys.setdefault(f"{b.losare}", set()).add(b.jamforbarhet)
    for losare, hashar in per_analys.items():
        if len(hashar) > 1:
            fel.append(
                f"{_pid(p)}: beläggen från {losare} kommer ur scenarier med olika analysparametrar "
                "(t.ex. olika regn); de kan inte jämföras i samma påstående."
            )
    if r.niva == 1 and (p.bedomningsgrund or any(b.bedomningsgrund for b in p.belagg)):
        fel.append(
            f"{_pid(p)}: bedömning mot regel eller krav (bedomningsgrund) hör till nivå 2; "
            "nivå 1 är sållning."
        )
    return fel


def sprakfel(p: Pastaende, r: Rapport) -> list[str]:
    fel: list[str] = []
    texter = " ".join(
        t for t in (p.text, p.forutsattning, p.motivering, *p.saknat_underlag) if t
    ).lower()
    if r.niva == 1:
        for fras in _NIVA1_FORBJUDET:
            if fras in texter:
                fel.append(
                    f"{_pid(p)}: '{fras}' är en bedömning mot krav eller av lämplighet. Ett "
                    "skissunderlag (nivå 1) sållar: skriv vad Floden visar och om frågan behöver "
                    "fördjupas."
                )
    skiss = {m["losare"] for m in r.metoder if m.get("status") != "rapport"}
    if any(b.losare in skiss for b in p.belagg):
        for o in _SAKERHETSORD:
            if re.search(rf"\b{o}\b", texter):
                fel.append(
                    f"{_pid(p)}: '{o}' passar inte för ett resultat från en metod som är "
                    "klassad som skiss."
                )
    for monster, vad in ((_EPOST, "e-postadress"), (_PERSONNR, "personnummer")):
        if monster.search(texter):
            fel.append(f"{_pid(p)}: texten innehåller något som liknar en {vad}.")
    return fel


def pastaendefel(p: Pastaende, r: Rapport) -> list[str]:
    """Kontroller som görs redan när ett påstående läggs till (utöver status- och textregler)."""
    return mattfel(p) + mottagarfel(p, r) + scenariofel(p, r) + sprakfel(p, r)


# ---------------------------------------------------------------------- hela rapporten


def _jamfor_belagg(lagrat: Belagg, nu: Belagg, pid: str) -> list[str]:
    fel: list[str] = []
    if not math.isclose(lagrat.varde, nu.varde, rel_tol=1e-12, abs_tol=1e-15):
        fel.append(
            f"{pid}: talet för {lagrat.adress} stämmer inte med körningen {lagrat.korning_id}: "
            f"rapporten har {lagrat.varde!r}, Floden ger {nu.varde!r}."
        )
    if lagrat.fil_sha256 != nu.fil_sha256:
        fel.append(
            f"{pid}: resultatfilen {lagrat.fil} har ändrats sedan belägget hämtades "
            f"(sha256 {str(lagrat.fil_sha256)[:12]} -> {str(nu.fil_sha256)[:12]})."
        )
    if lagrat.manifest_sha256 != nu.manifest_sha256:
        fel.append(f"{pid}: K0-manifestet {lagrat.manifest} har ändrats sedan belägget hämtades.")
    for falt in ("enhet", "namnare", "tidsdefinition", "statistik", "jamforbarhet"):
        if getattr(lagrat, falt) != getattr(nu, falt):
            fel.append(
                f"{pid}: beläggets {falt} för {lagrat.adress} stämmer inte med Floden "
                f"('{getattr(lagrat, falt)}' mot '{getattr(nu, falt)}')."
            )
    return fel


def _sparbarhet(r: Rapport, tj: RapportTjanst) -> tuple[list[str], int]:
    from rapport.skissrapport import KART_PX

    fel: list[str] = []
    antal = 0
    mott = r.mottagare_per_namn
    for p in r.pastaenden:
        fel += textfel(p)
        for b in p.belagg:
            antal += 1
            try:
                nu = tj.kalla.hamta(b.adress, b.korning_id, mott)
            except (BelaggFel, PastaendeFel) as exc:
                fel.append(f"{_pid(p)}: belägget {b.adress} går inte att hämta: {exc}")
                continue
            fel += _jamfor_belagg(b, nu, _pid(p))
    mapp = tj.arkiv.rapportmapp(r.rapport_id)
    for k in r.kartor:
        antal += 1
        fil = mapp / k.fil
        if not fil.is_file():
            fel.append(f"Kartan {k.kart_id}: filen {k.fil} saknas.")
            continue
        if sha256_fil(fil) != k.png_sha256:
            fel.append(f"Kartan {k.kart_id}: filen har ändrats (sha256 stämmer inte).")
        try:
            png, _meta = tj.tjanst.karta(
                k.korning_id,
                k.analys,
                typ=k.typ,
                scenario=k.scenario,
                utsnitt=k.meta.get("utsnitt"),
                max_px=KART_PX,
                rubrik=k.meta.get("rubrik"),
            )
        except Exception as exc:  # FlodenFel
            fel.append(f"Kartan {k.kart_id} kan inte ritas om ur körningen: {exc}")
            continue
        if hashlib.sha256(png).hexdigest() != k.png_sha256:
            fel.append(
                f"Kartan {k.kart_id} ritas inte likadant ur körningen i dag (figuren har ersatts "
                "eller körningen ändrats)."
            )
    for kp in r.korningar:
        try:
            utmapp = tj.kalla.utmapp(kp["korning_id"])
        except BelaggFel as exc:
            fel.append(str(exc))
            continue
        if sha256_fil(utmapp / "run.json") != kp["manifest_sha256"]:
            fel.append(f"Körningen {kp['korning_id']}: K0-manifestet har ändrats.")
    return fel, antal


def _scenario(r: Rapport, tj: RapportTjanst) -> tuple[list[str], int]:
    fel: list[str] = []
    for p in r.pastaenden:
        fel += scenariofel(p, r)
    for k in r.korningar:
        for j in k["jamforelser"]:
            h = tj.kalla._jamforbarhet(  # noqa: SLF001
                k["korning_id"], j["analys"], (j["fore"], j["efter"])
            )
            if h.startswith("EJ_JAMFORBAR"):
                fel.append(
                    f"Jämförelsen {j['namn']} i {k['korning_id']} har olika analysparametrar i "
                    f"före och efter ({h.removeprefix('EJ_JAMFORBAR:')})."
                )
    return fel, len(r.pastaenden)


def _karta(r: Rapport, tj: RapportTjanst) -> tuple[list[str], int]:
    from rapport.amnestext import tomvarde_lagrad, tomvarde_sbk
    from rapport.skissmall import bildtext
    from rapport.sprak import scenarionamn

    fel: list[str] = []
    for k in r.kartor:
        m = k.meta
        if m.get("norr") != "uppat":
            fel.append(f"Kartan {k.kart_id}: norr är inte uppåt (eller anges inte).")
        if not m.get("skalstock_m") or float(m["skalstock_m"]) <= 0:
            fel.append(f"Kartan {k.kart_id}: skalstock saknas.")
        if not m.get("enhet"):
            fel.append(f"Kartan {k.kart_id}: enheten saknas i teckenförklaringen.")
        if not m.get("skala"):
            fel.append(f"Kartan {k.kart_id}: värdeskalan (teckenförklaringen) saknas.")
        # Bildtexten som Floden lagrade (K33) och bildtexten som står i dokumentet (språkprofil).
        visad = bildtext(k, r)
        for text, tomvarde, scen in (
            (k.bildtext, tomvarde_lagrad(r.amne), k.scenario),
            (visad, tomvarde_sbk(r.amne) if r.installningar.sbk else tomvarde_lagrad(r.amne),
             scenarionamn(k.scenario) if r.installningar.sbk else k.scenario),
        ):  # fmt: skip
            if tomvarde not in text:
                fel.append(
                    f"Kartan {k.kart_id}: bildtexten förklarar inte tomvärdet (tak och celler "
                    "utan värde får inte se torra ut)."
                )
            if k.typ != "skillnad" and scen and scen not in text:
                fel.append(f"Kartan {k.kart_id}: scenariot '{scen}' står inte i bildtexten.")
            if m.get("enhet") and str(m["enhet"]) not in text:
                fel.append(f"Kartan {k.kart_id}: enheten står inte i bildtexten.")
    for kp in r.korningar:
        for j in kp["jamforelser"]:
            bas = next(
                (
                    k
                    for k in r.kartor
                    if k.typ == "bas" and k.analys == j["analys"] and k.scenario == j["fore"]
                ),
                None,
            )
            efter = next(
                (k for k in r.kartor if k.typ == "scenario" and k.jamforelse == j["namn"]), None
            )
            skillnad = next(
                (k for k in r.kartor if k.typ == "skillnad" and k.jamforelse == j["namn"]), None
            )
            for typ, kart in (("bas", bas), ("scenario", efter), ("skillnad", skillnad)):
                if kart is None:
                    fel.append(f"Kartan för {j['namn']} ({typ}) saknas i rapporten.")
            if bas is not None and efter is not None and bas.meta.get("skala") != efter.meta.get("skala"):
                fel.append(
                    f"Kartorna före och efter för {j['namn']} har olika färgskalor; samma skala "
                    "krävs för att en förändring ska synas."
                )
    return fel, len(r.kartor)


def _status(r: Rapport) -> tuple[list[str], int]:
    fel: list[str] = []
    for p in r.pastaenden:
        fel += statusfel(p)
        fel += [f for f in sprakfel(p, r) if "nivå 1" in f]
        if p.fraga_id not in r.fragor:
            fel.append(f"{_pid(p)}: frågan ingår inte i rapporten.")
    besvarade = {p.fraga_id for p in r.pastaenden}
    for f in r.fragor:
        if f not in besvarade:
            fel.append(
                f"Frågan {f} saknar påstående. Ge den en status (t.ex. 'underlag saknas' med vad "
                "som saknas, eller 'ej tillämpligt' med motivering)."
            )
    return fel, len(r.fragor)


def _dataskydd(r: Rapport, tj: RapportTjanst) -> tuple[list[str], int]:
    from floden_mcp.dataklass import DataKlass

    fel: list[str] = []
    klass = tj.policy.klass_for(DataKlass.OPPET, r.projekt).value
    if klass != r.dataklass:
        fel.append(f"Rapportens dataklass '{r.dataklass}' stämmer inte med projektets '{klass}'.")
    for kl in {klass, r.dataklass}:
        if kl != DataKlass.OPPET.value:
            fel.append(
                f"Rapporten har dataklassen '{kl}'. Ett skissunderlag exporteras bara med "
                "dataklassen 'oppet' (projektets data är inte öppna)."
            )
    for p in r.pastaenden:
        fel += [f for f in sprakfel(p, r) if "e-post" in f or "personnummer" in f]
    if r.bestallning is not None and any(
        mo.search(r.bestallning.syfte) for mo in (_EPOST, _PERSONNR)
    ):
        fel.append("Beställningens syfte liknar en e-postadress eller ett personnummer.")
    for m in r.mottagare:
        if _EPOST.search(m.beskrivning) or _PERSONNR.search(m.beskrivning):
            fel.append(f"Mottagaren {m.namn}: beskrivningen liknar en personuppgift.")
    return fel, 1 + len(r.pastaenden)


def _ekologitext(r: Rapport, text: str) -> list[str]:
    """Ekologirapportens fasta villkor: signalart och källa, etikett för NMD-habitat och d50-antagandet."""
    fel: list[str] = []
    low = text.lower()
    if "potentiell livsmiljö, okänd kvalitet" not in low:
        fel.append("Dokumentet säger inte 'potentiell livsmiljö, okänd kvalitet' (resultatets etikett).")
    if "signalart" not in low:
        fel.append("Dokumentet anger ingen signalart.")
    if "källa:" not in low:
        fel.append("Dokumentet anger ingen källa för nätverket (signalart och källa ska stå).")
    antagen = any("d50 = tröskel/2" in a for k in r.korningar for a in k.get("antaganden", []))
    if antagen and "p saknas i källan; d50 = tröskel/2" not in low:
        fel.append("Antagandet 'p saknas i källan; d50 = tröskel/2' syns inte i dokumentet.")
    kartor = {k.typ for k in r.kartor}
    if not {"bas", "scenario", "skillnad"} <= kartor:
        fel.append("Flaskhalskartan (strömtäthet) saknas före, efter eller som skillnad.")
    return fel


def _lufttext(r: Rapport, text: str) -> list[str]:
    """Luftrapportens fasta villkor: serien är extern, bakgrunden ett antagande, intervallet en känslighet, före/efter."""
    fel: list[str] = []
    low = " ".join(text.lower().split())
    for fras, vad in (
        ("räknats fram utanför floden", "att haltbidraget är en given serie som räknats fram utanför Floden"),
        ("bakgrunden är ett antagande", "att bakgrunden är ett antagande"),
        ("inte en statistisk konfidens", "att intervallet inte är en statistisk konfidens"),
        ("metoden är en skiss", "att metoden är en skiss"),
    ):
        if fras not in low:
            fel.append(f"Dokumentet säger inte {vad}.")
    analyser = [a for k in r.korningar for a in k["analyser"] if a["losare"] == "luft.mkn"]
    if len(analyser) >= 2 and "två separata serier" not in low:
        fel.append("Dokumentet säger inte att före och efter bygger på två separata serier.")
    if len(analyser) == 1 and "kräver två serier" not in low:
        fel.append("Dokumentet säger inte att före och efter kräver två serier (underlaget har bara en).")
    if not analyser:
        fel.append("Rapporten har ingen analys med lösaren luft.mkn.")
    if any("bakgrund" in a.lower() and "olika" in a.lower() and "varning" in a.lower() for k in r.korningar for a in k.get("antaganden", [])):
        fel.append("Analyserna för före och efter har olika bakgrund, faktorintervall eller gräns: de kan inte jämföras.")
    return fel


def _osakerhet(r: Rapport, dokumenttext: Callable[[], str] | None = None) -> tuple[list[str], int]:
    fel: list[str] = []
    if r.amne == "ekologi" and dokumenttext is not None:
        fel += _ekologitext(r, dokumenttext())
    if r.amne == "luft" and dokumenttext is not None:
        fel += _lufttext(r, dokumenttext())
    for k in r.korningar:
        if not k.get("antaganden"):
            fel.append(
                f"Körningen {k['korning_id']} saknar antaganden (regn, takvatten, avtappning) i "
                "rapporten."
            )
    for p in r.pastaenden:
        fel += [f for f in sprakfel(p, r) if "skiss" in f]
    return fel, len(r.korningar)


def _metodstatus(
    r: Rapport, tj: RapportTjanst, dokumenttext: Callable[[], str]
) -> tuple[list[str], int]:
    fel: list[str] = []
    kanda = {m["losare"]: m for m in r.metoder}
    anvanda = {b.losare for p in r.pastaenden for b in p.belagg} | {
        a["losare"] for k in r.korningar for a in k["analyser"]
    }
    for losare in sorted(anvanda):
        m = kanda.get(losare)
        if m is None:
            fel.append(f"Metoden {losare} används men saknar valideringsstatus i rapporten.")
            continue
        nu = tj.tjanst.metodstatus(losare)["status"]
        if nu != m.get("status"):
            fel.append(
                f"Metoden {losare}: valideringsstatus har ändrats ({m.get('status')} -> {nu}); "
                "skapa rapporten på nytt."
            )
    text = dokumenttext()
    for losare, m in kanda.items():
        if losare not in text:
            fel.append(f"Metoden {losare} syns inte i dokumentet.")
        elif str(m.get("status")) not in text:
            fel.append(f"Valideringsstatus för {losare} syns inte i dokumentet.")
    for p in r.pastaenden:
        try:
            fylld = p.fylld_text()
        except PastaendeFel:
            continue
        if " ".join(fylld.split()) not in text:
            fel.append(f"{_pid(p)}: påståendets text står inte oförändrad i dokumentet.")
    return fel, len(anvanda)


def kontrollera_skissrapport(r: Rapport, tj: RapportTjanst) -> dict[str, Any]:
    """Alla kontroller inför export. `godkand` är sant bara om ingen punkt har fel."""
    from rapport.skissmall import bygg_dokument, dokumentets_text

    cache: dict[str, str] = {}

    def dokumenttext() -> str:
        if "t" not in cache:
            try:
                dok = bygg_dokument(r, tj, korlogg_rader=[])
                cache["t"] = " ".join(dokumentets_text(dok).split())
            except (PastaendeFel, KeyError, ValueError, LookupError) as exc:
                cache["t"] = f"[dokumentet kunde inte byggas: {exc}]"
        return cache["t"]

    resultat: dict[str, tuple[list[str], int]] = {
        "sparbarhet": _sparbarhet(r, tj),
        "scenario": _scenario(r, tj),
        "mottagare": (
            [f for p in r.pastaenden for f in mottagarfel(p, r)],
            len(r.pastaenden),
        ),
        "matt": ([f for p in r.pastaenden for f in mattfel(p)], len(r.pastaenden)),
        "osakerhet": _osakerhet(r, dokumenttext),
        "karta": _karta(r, tj),
        "status": _status(r),
        "dataskydd": _dataskydd(r, tj),
        "metodstatus": _metodstatus(r, tj, dokumenttext),
    }
    punkter = []
    for pid, rubrik in PUNKTER:
        fel, antal = resultat[pid]
        unika = list(dict.fromkeys(fel))
        punkter.append(
            {"id": pid, "rubrik": rubrik, "godkand": not unika, "granskat": antal, "fel": unika}
        )
    return {
        "godkand": all(p["godkand"] for p in punkter),
        "tid": datetime.now(UTC).isoformat(timespec="seconds"),
        "punkter": punkter,
    }


def kontroll_text(resultat: dict[str, Any]) -> str:
    """Kontrollresultatet som klartext (för felmeddelanden och CLI)."""
    rader = ["Kontrollen inför export: " + ("godkänd." if resultat["godkand"] else "UNDERKÄND.")]
    for p in resultat["punkter"]:
        rader.append(f"- {p['rubrik']}: {'ok' if p['godkand'] else 'FEL'}")
        rader.extend(f"    {f}" for f in p["fel"])
    return "\n".join(rader)


__all__ = [
    "PUNKTER",
    "kontroll_text",
    "kontrollera_skissrapport",
    "mattfel",
    "ekologifel",
    "luftfel",
    "mottagarfel",
    "pastaendefel",
    "scenariofel",
    "sprakfel",
]
