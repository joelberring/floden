# ruff: noqa: E501
"""AI-fritt referensflöde: skissunderlag luft (PM10 mot dygnsnormen som intervall) för luftdemons körning (R2).

Fasta påståenden (inga modeller) för frågorna LU-01 till LU-15 (huvudkatalogen `docs/utredningar-stockholm/fragor.json`)
med belägg ur körningen `demo-luft` (lösaren `luft.mkn`, K21: två påhittade timserier, före och efter, och en påhittad
bakgrund). Används av `tools/rapport_demo.py --amne luft` och testerna. Statusen per fråga är den som
`rapport.fragekatalog.FLODEN_FORMAGA` anger som `forval_status` (testet kontrollerar att de stämmer).

Alla tal är platshållare som Floden fyller ur körningen (`matvarde.<analys>.<scenario>.<mått>`). Texten säger
aldrig att normen klaras eller överskrids: intervallets förhållande till högsta antalet dygn (`under`, `over`,
`korsar`) läses ur beläggen och styr en av tre fasta meningar. Haltbidraget är en given serie (räknad utanför
Floden) och bakgrunden är ett antagande; båda står i texten.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rapport.luftbelagg import LUFT_LOSARE, intervallrelation, luftlage

if TYPE_CHECKING:
    from rapport.skissrapport import Rapport, RapportTjanst


@dataclass(frozen=True)
class Lage:
    """Ett läge: en analys (en serie) i ett scenario, med intervallets förhållande till högsta antalet dygn."""

    analys: str
    scenario: str
    namn: str
    relation: str
    """`under`, `over` eller `korsar` (ingen siffra)."""


@dataclass(frozen=True)
class LuftKontekst:
    lagen: tuple[Lage, ...]
    """Före först, sedan efter, sedan övriga i körningens ordning."""


def kontekst_fran_rapport(rt: RapportTjanst, r: Rapport) -> LuftKontekst:
    """Läser analyser, scenario och intervallets förhållande till gränsen ur rapportens körning (inga tal)."""
    k = r.korningar[0]
    rader: list[tuple[str, str]] = [
        (a["analys"], a["scenarier"][0]) for a in k["analyser"] if a["losare"] == LUFT_LOSARE and a["scenarier"]
    ]
    if not rader:
        raise ValueError("Körningen har ingen analys med lösaren luft.mkn.")
    ordning = {"före": 0, "efter": 1}
    rader.sort(key=lambda x: ordning.get(luftlage(x[0]), 2))
    lagen: list[Lage] = []
    for analys, scen in rader:
        belagg = [
            rt.kalla.hamta(f"matvarde.{analys}.{scen}.{m}", str(k["korning_id"]), {})
            for m in ("lagst_antal_dygn", "hogst_antal_dygn", "max_dygn_ar_antal_dygn")
        ]
        rel = intervallrelation(belagg)[(analys, scen)]
        if rel is None:
            raise ValueError(f"Beläggen för {analys} räcker inte för att avgöra intervallets förhållande till gränsen.")
        lagen.append(Lage(analys, scen, luftlage(analys), rel))
    return LuftKontekst(tuple(lagen))


def _m(adress: str, opt: str = "") -> str:
    if not opt and adress.endswith(("emissionsfaktor_lagst", "emissionsfaktor_hogst", "depositionsfaktor_lagst", "depositionsfaktor_hogst")):
        opt = "avrundning=0,01"
    return "{{matt:" + adress + (f"|{opt}" if opt else "") + "}}"


_RELATION_TEXT = {
    "under": "Intervallet ligger helt under eller på högsta antalet dygn.",
    "over": "Intervallet ligger helt över högsta antalet dygn.",
    "korsar": (
        "Intervallet korsar högsta antalet dygn: underlaget kan därför varken säga att normen klaras "
        "eller att den överskrids."
    ),
}


def pastaenden(k: LuftKontekst, fragor: Sequence[str] | None = None) -> list[Mapping[str, Any]]:
    """Påståendena (argument till `RapportTjanst.lagg_till_pastaende`) för de frågor som ingår."""
    lagen = k.lagen
    f = lagen[0]

    def mv(lage: Lage, matt: str) -> str:
        return f"matvarde.{lage.analys}.{lage.scenario}.{matt}"

    ut: list[dict[str, Any]] = []

    def add(fid: str, status: str, fordj: str, text: str, **kw: Any) -> None:
        ut.append({"fraga_id": fid, "status": status, "fordjupning": fordj, "text": text, **kw})

    def intervall_text(lage: Lage) -> str:
        return (
            f"I läget {lage.namn} ligger antalet dygn över dygnsgränsen mellan {_m(mv(lage, 'lagst_antal_dygn'))} "
            f"och {_m(mv(lage, 'hogst_antal_dygn'))} per år, när källstyrkan och utspädningen varieras inom "
            f"faktorintervallet. Högsta tillåtna antal är {_m(mv(lage, 'max_dygn_ar_antal_dygn'))}. {_RELATION_TEXT[lage.relation]}"
        )

    def intervall_belagg(lage: Lage) -> list[str]:
        return [mv(lage, "lagst_antal_dygn"), mv(lage, "hogst_antal_dygn"), mv(lage, "max_dygn_ar_antal_dygn")]

    # LU-01
    add("LU-01", "ej_tillampligt", "nej",
        "Underlaget prövar bara PM10 mot dygnsnormen. Vilka miljökvalitetsnormer som behöver prövas i övrigt är en bedömning i ärendet.",
        motivering="Val av normer, normtyp och lagrum avgörs i ärendet. Floden visar högsta antal dygn över dygnsgränsen för PM10 ur en given serie; kvävedioxid, PM2,5 och årsmedelnormen prövas inte.")  # fmt: skip
    # LU-02
    bel = [mv(la, "bakgrund_ug_m3") for la in lagen]
    text = "Bakgrunden för PM10 är en konstant halt som läggs till serien. Den är ett antagande: " + " ".join(
        f"i läget {la.namn} är den {_m(mv(la, 'bakgrund_ug_m3'))}." for la in lagen
    ) + " Körningen anger inte vilken station, vilket observationsår eller vilken datatäckning bakgrunden bygger på."
    add("LU-02", "underlag_saknas", "ja", text, belagg=bel,
        saknat_underlag=["Närmaste representativa station, observationsår, datatäckning och normalår för bakgrunden, och hur bakgrunden skiljer sig från projektets gaturum."])  # fmt: skip
    # LU-03
    bel = [b for la in lagen for b in intervall_belagg(la)[:2]]
    if len(lagen) >= 2:
        text = (
            "Före och efter förslaget är två separata serier som har räknats fram utanför Floden, med samma bakgrund, "
            "samma faktorintervall och samma gräns. "
            + " ".join(
                f"I läget {la.namn} ligger antalet dygn över gränsen mellan {_m(mv(la, 'lagst_antal_dygn'))} och {_m(mv(la, 'hogst_antal_dygn'))} per år."
                for la in lagen
            )
            + " Att serierna är jämförbara i övrigt, med samma modell, väder, trafikår och bakgrundsår, går inte att kontrollera i Floden."
        )
    else:
        text = (
            f"Underlaget har en serie, läget {f.namn}: antalet dygn över gränsen ligger mellan {_m(mv(f, 'lagst_antal_dygn'))} och "
            f"{_m(mv(f, 'hogst_antal_dygn'))} per år. Före och efter förslaget kan inte jämföras: det kräver två serier från samma externa modell."
        )
    add("LU-03", "underlag_saknas", "ja", text, belagg=bel,
        saknat_underlag=["Nuläge, framtid utan plan och med plan med jämförbara trafik- och bakgrundsantaganden för samma prognosår, ur samma externa modell."])  # fmt: skip
    # LU-04
    add("LU-04", "underlag_saknas", "ja",
        "Utsläppen som driver dygnstopparna och årsmedlet är inte redovisade. Serien är given, och Floden har inte tagit del av trafik, fordonsmix, hastighet eller emissionsfaktorer.",
        saknat_underlag=["Trafik per länk, fordonsmix, hastighet, emissionsfaktorer, avgaser, slitage och uppvirvling, och källa till dubbdäcksandel och vinterdrift."])  # fmt: skip
    # LU-05
    ef = (mv(f, "emissionsfaktor_lagst"), mv(f, "emissionsfaktor_hogst"))
    df = (mv(f, "depositionsfaktor_lagst"), mv(f, "depositionsfaktor_hogst"))
    bel = [mv(f, "bakgrund_ug_m3"), *ef, *df, mv(f, "antal_hela_dygn")]
    add("LU-05", "underlag_saknas", "ja",
        f"Spridning och bakgrund är inte beskrivna: det lokala haltbidraget är en given timserie som har räknats fram utanför Floden. Underlaget visar bara hur serien används. "
        f"I läget {f.namn} är bakgrunden {_m(bel[0])}, bidraget skalas med en faktor på källstyrkan från {_m(ef[0])} till {_m(ef[1])} och en faktor på utspädning och deposition "
        f"från {_m(df[0])} till {_m(df[1])}, och serien har {_m(bel[5])}.",
        belagg=bel,
        saknat_underlag=["Modell och version, meteorologisk period, vind, gaturummets bredd och höjd, emissionsår, regional och urban bakgrund och hur dubbelräkning undviks."])  # fmt: skip
    # LU-06
    add("LU-06", "underlag_saknas", "ja",
        "Underlaget bygger på en given serie och har ingen geometri eller spridningsberäkning. Det visar därför inte om en modell kan beskriva en korsning, en tunnelmynning eller ett slutet kvarter.",
        saknat_underlag=["Sektion och beräkningsdomän, och motivering av gaturumsmodell, lokal tredimensionell studie eller kompletterande mätning för frågan."])  # fmt: skip
    # LU-07
    add("LU-07", "underlag_saknas", "ja",
        "Serien gäller en punkt vars läge inte anges i körningen, och resultatet har ingen karta. Var normen ska utvärderas är därför inte redovisat.",
        saknat_underlag=["Karta med trottoarer, korsningar, gårdar och vistelseytor, och motiverade receptorer, höjder och undantagna ytor."])  # fmt: skip
    # LU-08
    bel = []
    delar = []
    for la in lagen:
        bel += [
            *intervall_belagg(la), mv(la, "grans_dygn_ug_m3"), mv(la, "arsmedel_lagst_ug_m3"), mv(la, "arsmedel_hogst_ug_m3"),
            mv(la, "p90_4_lagst_ug_m3"), mv(la, "p90_4_hogst_ug_m3"), mv(la, "antal_hela_dygn"),
        ]  # fmt: skip
        delar.append(
            f"{intervall_text(la)} Årsmedlet för PM10 ligger mellan {_m(mv(la, 'arsmedel_lagst_ug_m3'))} och {_m(mv(la, 'arsmedel_hogst_ug_m3'))}, och "
            f"percentilen som motsvarar högsta antalet dygn mellan {_m(mv(la, 'p90_4_lagst_ug_m3'))} och {_m(mv(la, 'p90_4_hogst_ug_m3'))}."
        )
    add("LU-08", "visat", "ja",
        f"Tidsstatistiken för PM10 är kalenderdygn som dygnsmedel av timvärdena, alla hela dygn i serien ({_m(mv(f, 'antal_hela_dygn'))}), mot dygnsgränsen {_m(mv(f, 'grans_dygn_ug_m3'))}. "
        + " ".join(delar)
        + " Kvävedioxid, PM2,5 och andra ämnen är inte räknade.",
        belagg=list(dict.fromkeys(bel)))  # fmt: skip
    # LU-09
    add("LU-09", "underlag_saknas", "ja",
        "Underlaget jämför inte halterna mot miljö- och hälsomål och redovisar ingen normbedömning. Alternativa placeringar av lek eller vistelse prövas inte.",
        saknat_underlag=["Normbedömning och målbedömning i egna kolumner, och alternativa placeringar av lek eller vistelse."])  # fmt: skip
    # LU-10
    bel = [b for la in lagen for b in intervall_belagg(la)]
    text = (
        "Intervallet visar hur antalet dygn över gränsen ändras när källstyrkan och utspädningen varieras inom faktorintervallet. Det är en känslighet för två faktorer, inte en statistisk konfidens. "
        + " ".join(intervall_text(la) for la in lagen)
        + " Variation av trafik, flottans utsläpp, dubbdäck och bakgrund, och jämförelse med mätningar, ingår inte."
    )
    add("LU-10", "underlag_saknas", "ja", text, belagg=list(dict.fromkeys(bel)),
        saknat_underlag=["Variation av trafik, flottans utsläpp, dubbdäck och bakgrund, och jämförelse mellan modell och representativa mätningar."])  # fmt: skip
    # LU-11
    bel = [b for la in lagen for b in intervall_belagg(la)[:2]]
    add("LU-11", "underlag_saknas", "ja",
        "Underlaget föreslår ingen åtgärd. Det jämför bara de serier som har lagts in: "
        + " ".join(
            f"i läget {la.namn} är intervallet {_m(mv(la, 'lagst_antal_dygn'))} till {_m(mv(la, 'hogst_antal_dygn'))} dygn."
            for la in lagen
        )
        + " Ändrad trafik, gatugeometri, placering av vistelseyta och drift prövas bara om en serie per alternativ läggs in.",
        belagg=bel,
        saknat_underlag=["En serie per åtgärdsalternativ ur samma externa modell, och effekten vid samma mottagare med risk för förskjuten exponering."])  # fmt: skip
    # LU-12
    add("LU-12", "underlag_saknas", "ja",
        "Byggdamm och arbetsmaskiner ingår inte i underlaget. Serien gäller driftläget.",
        saknat_underlag=["Arbetsmoment, transportvägar, känsliga mottagare, skyddsåtgärder och uppföljning för byggperioden, med datum."])  # fmt: skip
    # LU-13
    add("LU-13", "ej_tillampligt", "nej",
        "Underlaget prövar inte om planen träffar någon av länsstyrelsens situationer för fördjupad luftbedömning.",
        motivering="Vilken situation som gäller och vilken norm som är aktuell är en bedömning i ärendet. Floden läser inte planens trafikalstring, känsliga användning eller verksamheter.")  # fmt: skip
    # LU-14
    add("LU-14", "underlag_saknas", "ja",
        "Serien gäller en punkt och inte en skol- eller förskolegård. Gårdsgräns, vistelseytor och möjliga placeringar är inte redovisade.",
        saknat_underlag=["Gårdsgräns och vistelseytor, halter per tidsmått för möjliga placeringar och källår för kartläggningen."])  # fmt: skip
    # LU-15
    add("LU-15", "underlag_saknas", "ja",
        f"Varje tal i texten har ett belägg med körning, fil och kontrollsumma i bilagan, och serie, bakgrund och faktorer står i antagandena. Talen gäller en given serie med {_m(mv(f, 'antal_hela_dygn'))}, "
        "men är inte knutna till en station, en beräknad mottagare, ett år eller en figur: serien anger inte plats eller år, och resultatet har ingen karta.",
        belagg=[mv(f, "antal_hela_dygn")],
        saknat_underlag=["Station eller beräknad mottagare, tidsmått och år för det mest utsatta läget, och den figur i planutredningen som visar det."])  # fmt: skip
    valda = None if fragor is None else set(fragor)
    return [p for p in ut if valda is None or p["fraga_id"] in valda]


__all__ = ["Lage", "LuftKontekst", "kontekst_fran_rapport", "pastaenden"]
