# ruff: noqa: E501
"""AI-fritt referensflöde: skissunderlag ekologi (grön infrastruktur, spridningssamband) för demokvarterets körning (R2).

Fasta påståenden (inga modeller) för frågorna EK-01 till EK-15 (huvudkatalogen `docs/utredningar-stockholm/fragor.json`)
med belägg ur körningen `demo-ekologi` (habitatnätverket och NMD-underlaget, före och efter ett nytt hus).
Används av `tools/rapport_demo.py --amne ekologi` och testerna. Statusen per fråga är den som
`rapport.fragekatalog.FLODEN_FORMAGA` anger som `forval_status` (testet kontrollerar att de stämmer).

Alla tal är platshållare som Floden fyller ur körningen (`matvarde`, `ekojamforelse`, `ekonatverk`).
Texten anger inte om ett tal ökar eller minskar (tecknet kommer ur värdet). Signalart och källa står i
texten; källans år och sidor står i antagandena (ett årtal i en text vore ett tal utan belägg).
Resultatet kallas *potentiell livsmiljö, okänd kvalitet*: NMD säger inget om ålder, struktur eller naturvärde.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rapport.sprak import scenarionamn

if TYPE_CHECKING:
    from rapport.skissrapport import Rapport, RapportTjanst


@dataclass(frozen=True)
class EkologiKontekst:
    """Det referensflödet behöver veta om körningen (inga tal)."""

    analys: str
    fore: str
    efter: tuple[tuple[str, str], ...]
    """(scenario, jämförelsens namn) för varje alternativ som jämförs med `fore`."""
    ruta: str | None
    signalarter: tuple[str, ...]
    natverk_namn: str
    kalla_kort: str
    d50_antagen: bool


def _kalla_kort(referens: str) -> str:
    """Källans referens utan årtal, sidor och bilagor (siffror i en löptext är tal utan belägg)."""
    r = re.sub(r"\s*\b(?:19|20)\d{2}\b", "", referens.split(", bilaga")[0])
    delar = [d.strip() for d in re.split(r",(?![^()]*\))", r) if d.strip() and not re.search(r"\d", d)]
    return ", ".join(delar)


def kontekst_fran_rapport(rt: RapportTjanst, r: Rapport) -> EkologiKontekst:
    """Läser analys, scenarier, ruta, signalart och källa ur rapportens körning (inga tal)."""
    from rapport.ekologibelagg import EKOLOGI_LOSARE, _forsta_dok

    k = r.korningar[0]
    analys = next((a["analys"] for a in k["analyser"] if a["losare"] == EKOLOGI_LOSARE), None)
    if analys is None:
        raise ValueError("Körningen har ingen ekologianalys (ekologi.konnektivitet).")
    jf = [j for j in k["jamforelser"] if j["analys"] == analys]
    if not jf:
        raise ValueError("Körningen har ingen jämförelse före och efter för ekologianalysen.")
    fore = jf[0]["fore"]
    svar = rt.tjanst.resultat_sammanfattning(k["korning_id"], analys=analys, scenario=fore, max_rader=1)
    rad = next((x for t in svar["matvarden"] for x in t["rader"]), None)
    dok = _forsta_dok(rt.kalla, k["korning_id"], analys, fore)
    if dok is None:
        raise ValueError("Körningen saknar ekologi.json.")
    nv = dok.get("natverk", {})
    return EkologiKontekst(
        analys=analys,
        fore=fore,
        efter=tuple((j["efter"], j["namn"]) for j in jf if j["fore"] == fore),
        ruta=None if rad is None else rad["ruta"],
        signalarter=tuple(str(a) for a in nv.get("signalarter", [])),
        natverk_namn=str(nv.get("namn", "")),
        kalla_kort=_kalla_kort(str(nv.get("kalla", {}).get("referens", ""))),
        d50_antagen=bool(nv.get("d50_antagen")),
    )


def _m(adress: str, opt: str = "") -> str:
    if not opt:
        if adress.endswith((".antal_flackar", ".delta_antal_flackar")):
            opt = "enhet=nej"
        elif adress.endswith((".pc", ".pc_min", ".pc_max", ".delta_pc")):
            opt = "avrundning=0,00001|enhet=nej"
        elif adress.endswith(".cellstorlek_m"):
            opt = "avrundning=1"
        elif adress.endswith(".delta_eca_procent"):
            opt = "avrundning=0,01"
    return "{{matt:" + adress + (f"|{opt}" if opt else "") + "}}"


def _lage(scenario: str) -> str:
    n = scenarionamn(scenario)
    return f"{n} förslaget" if n in ("före", "efter") else f"i alternativet {n}"


def pastaenden(k: EkologiKontekst, fragor: Sequence[str] | None = None) -> list[Mapping[str, Any]]:
    """Påståendena (argument till `RapportTjanst.lagg_till_pastaende`) för de frågor som ingår."""
    a, f = k.analys, k.fore
    ruta = f".{k.ruta}" if k.ruta else ""

    def mv(s: str, matt: str) -> str:
        return f"matvarde.{a}.{s}{ruta}.{matt}"

    def nv(matt: str) -> str:
        return f"ekonatverk.{a}.{f}{ruta}.{matt}"

    def jf(e: str, matt: str) -> str:
        return f"ekojamforelse.{a}.{f}.{e}{ruta}.{matt}"

    arter = " och ".join(k.signalarter) or "signalarten"
    ut: list[dict[str, Any]] = []

    def add(fid: str, status: str, fordj: str, text: str, **kw: Any) -> None:
        ut.append({"fraga_id": fid, "status": status, "fordjupning": fordj, "text": text, **kw})

    def efter_text(fn: Any) -> str:
        return " ".join(fn(e) for e, _n in k.efter)

    # EK-01
    add("EK-01", "underlag_saknas", "ja",
        f"Beräkningen avser habitatnätverket {k.natverk_namn} för signalarterna {arter}. Det är ett enda nätverk, räknat i en yta på "
        f"{_m(nv('analys_area_m2'))} (rutan med kontext runt den).",
        belagg=[nv("analys_area_m2")], mottagare="hela_omradet",
        saknat_underlag=["Andra kända nätverk, värdekärnor och regionala samband i och kring området. Floden läser inga länsstyrelse- eller kommunlager."])  # fmt: skip
    # EK-02
    add("EK-02", "underlag_saknas", "ja",
        f"Sambandet är analyserat i en yta på {_m(nv('analys_area_m2'))} med cellstorleken {_m(nv('cellstorlek_m'))}. Källans längsta spridningströskel är "
        f"{_m(nv('troskel_hog_m'))}. Habitatfläckar utanför ytan ingår inte.",
        belagg=[nv("analys_area_m2"), nv("cellstorlek_m"), nv("troskel_hog_m")], mottagare="hela_omradet",
        saknat_underlag=["Motivering av kontextens storlek för platsen och fläckar utanför markunderlaget."])  # fmt: skip
    # EK-03
    add("EK-03", "visat", "ja",
        f"Signalart: {arter}. Källa: {k.kalla_kort}; årtal och sidor står i antagandena. Habitatdefinitionen i NMD-klasser är ett antagande i Floden "
        f"och den minsta habitatfläckens area är {_m(nv('min_area_m2'))}.",
        belagg=[nv("min_area_m2")])  # fmt: skip
    # EK-04
    add("EK-04", "underlag_saknas", "ja",
        "Beräkningen visar potentiell livsmiljö, okänd kvalitet. Signalartens förekomst, habitatets kvalitet och barriärer som markunderlaget saknar är inte undersökta.",
        saknat_underlag=["Fältinventering av signalarten, habitatets ålder, struktur och naturvärde samt barriärer (vägklass, stängsel, ljus, buller) som NMD saknar."])  # fmt: skip
    # EK-05
    add("EK-05", "ej_tillampligt", "nej", "Underlaget prövar inte förbud, föreskrifter eller tillståndsfrågor för arter eller områdesskydd.",
        motivering="Vilka bestämmelser som gäller och om tillstånd krävs är en bedömning i ärendet. Floden läser inga artskydds- eller områdesskyddsuppgifter; beräkningen visar ett skissat samband.")  # fmt: skip
    # EK-06
    bel = [mv(f, "antal_flackar"), mv(f, "total_habitat_area_m2"), nv("min_area_m2")]
    text = (
        f"Habitatet är potentiell livsmiljö, okänd kvalitet: NMD visar markslag men inte skogens ålder, struktur eller naturvärde. "
        f"{_lage(f).capitalize()} finns {_m(bel[0])} habitatfläckar med en sammanlagd area på {_m(bel[1])}, och en fläck är minst {_m(bel[2])}."
    )
    for e, _n in k.efter:
        bel += [mv(e, "antal_flackar"), mv(e, "total_habitat_area_m2")]
        text += f" {_lage(e).capitalize()} finns {_m(bel[-2])} fläckar med en sammanlagd area på {_m(bel[-1])}."
    add("EK-06", "underlag_saknas", "ja", text, belagg=bel, mottagare="hela_omradet",
        saknat_underlag=["Skogens ålder, struktur och naturvärde samt om signalarten finns i fläckarna."])  # fmt: skip
    # EK-07
    bel = [nv("troskel_m"), nv("d50_m"), nv("troskel_hog_m"), nv("d50_hog_m")]
    p_text = (
        "Sannolikheten p saknas i källan, så d50 antas vara hälften av tröskeln (ett antagande i Floden)."
        if k.d50_antagen
        else "Källan anger d50."
    )
    add("EK-07", "underlag_saknas", "ja",
        f"Spridningströskeln är {_m(bel[0])} (effektiva meter: avstånd viktat med motståndet i marken) och d50 är {_m(bel[1])}. {p_text} "
        f"Källans längsta tröskel är {_m(bel[2])} med d50 {_m(bel[3])}. Motståndet per markslag står i nätverkets fil (bilagan).",
        belagg=bel,
        saknat_underlag=["Hur murar, öppningar och absoluta barriärer hanteras som egna objekt; markunderlaget har bara markslag, och mätt spridning för platsens arter saknas om källan inte anger den."])  # fmt: skip
    # EK-08
    bel = []
    text = (
        f"ECA, den ekvivalenta sammanhängande habitatarean för potentiell livsmiljö, okänd kvalitet, är {_m(mv(f, 'eca_m2'))} {_lage(f)}"
    )
    bel += [mv(f, "eca_m2")]
    for e, _n in k.efter:
        bel += [mv(e, "eca_m2"), jf(e, "delta_eca_m2"), jf(e, "delta_eca_procent"), mv(f, "pc"), mv(e, "pc"), jf(e, "delta_pc"),
                mv(f, "antal_flackar"), mv(e, "antal_flackar"), mv(f, "eca_min_m2"), mv(f, "eca_max_m2"), mv(e, "eca_min_m2"), mv(e, "eca_max_m2")]  # fmt: skip
        text += (
            f" och {_m(mv(e, 'eca_m2'))} {_lage(e)}. Skillnaden efter minus före är {_m(jf(e, 'delta_eca_m2'))} ({_m(jf(e, 'delta_eca_procent'))}). "
            f"PC är {_m(mv(f, 'pc'))} före och {_m(mv(e, 'pc'))} efter, en skillnad på {_m(jf(e, 'delta_pc'))}. "
            f"Antalet habitatfläckar är {_m(mv(f, 'antal_flackar'))} före och {_m(mv(e, 'antal_flackar'))} efter. "
            f"Mellan källans tröskelscenarier ligger ECA mellan {_m(mv(f, 'eca_min_m2'))} och {_m(mv(f, 'eca_max_m2'))} före, "
            f"och mellan {_m(mv(e, 'eca_min_m2'))} och {_m(mv(e, 'eca_max_m2'))} efter."
        )
    add("EK-08", "visat", "ja", text, belagg=list(dict.fromkeys(bel)), mottagare="hela_omradet")
    # EK-09
    add("EK-09", "underlag_saknas", "ja",
        "Förslaget är beräknat ensamt mot nuläget. Hur nätverket förändras tillsammans med andra utbyggnader ingår inte, och Floden läser inga andra planer.",
        saknat_underlag=["Vilka planer och program som påverkar samma samband, deras beslutsstatus och tidshorisont, och ett gemensamt scenario med samma signalart."])  # fmt: skip
    # EK-10
    bel = [mv(f, "cellstorlek_m"), mv(f, "antal_flackar")]
    text = (
        f"Kartorna visar strömtäthet (flaskhalsar) före, efter och som skillnad, med samma färgskala. Strömtätheten är normaliserad och visar var rörelsen "
        f"mellan habitatfläckarna är koncentrerad i modellen. Rastrets cellstorlek är {_m(bel[0])}. I skillnadskartan syns bara förändringar över tröskelvärdet."
    )
    for e, _n in k.efter:
        bel += [mv(e, "antal_flackar"), jf(e, "delta_antal_flackar"), jf(e, "delta_total_habitat_area_m2")]
        text += (
            f" {_lage(e).capitalize()} är antalet habitatfläckar {_m(mv(e, 'antal_flackar'))} mot {_m(mv(f, 'antal_flackar'))} före (skillnad {_m(jf(e, 'delta_antal_flackar'))}) "
            f"och habitatarean skiljer {_m(jf(e, 'delta_total_habitat_area_m2'))}."
        )
    text += " Fläckarnas betydelse för sambandet (dPC) finns i körningens resultatfil men redovisas inte som tal här."
    add("EK-10", "underlag_saknas", "ja", text, belagg=list(dict.fromkeys(bel)), mottagare="hela_omradet",
        saknat_underlag=["Vilken enskild livsmiljö eller passage som bär förändringen och en möjlig alternativ väg, flaskhalsarnas barriärverkan i fält och fläckvis betydelse (dPC) som tabell."])  # fmt: skip
    # EK-11
    bel = [nv("troskel_m"), nv("troskel_hog_m")]
    text = f"Det källbaserade intervallet följer källans två spridningströsklar, {_m(bel[0])} och {_m(bel[1])}."
    for e, _n in k.efter:
        for s in (f, e):
            bel += [mv(s, "pc_min"), mv(s, "pc_max")]
        text += (
            f" PC ligger mellan {_m(mv(f, 'pc_min'))} och {_m(mv(f, 'pc_max'))} före och mellan {_m(mv(e, 'pc_min'))} och {_m(mv(e, 'pc_max'))} efter."
        )
    text += " Intervallet visar inte osäkerheten i habitatval, motstånd per markslag eller signalartens verkliga spridning."
    add("EK-11", "underlag_saknas", "ja", text, belagg=list(dict.fromkeys(bel)),
        saknat_underlag=["Känslighet för habitatval och motstånd per markslag, och fältdata som visar signalartens verkliga spridning."])  # fmt: skip
    # EK-12
    bel = []
    text = "Jämförelsen gäller de alternativ som lagts in som scenario, var och ett mot nuläget med samma signalart och samma mått."
    for e, _n in k.efter:
        bel += [jf(e, "delta_eca_m2"), jf(e, "delta_pc")]
        text += f" {_lage(e).capitalize()} skiljer ECA {_m(bel[-2])} och PC {_m(bel[-1])}."
    add("EK-12", "underlag_saknas", "ja", text, belagg=bel, mottagare="hela_omradet",
        saknat_underlag=["Flyttad eller minskad byggrätt, bevarade värdträd och fungerande passager prövas bara om de lagts in som scenario. Återstående förlust redovisas inte."])  # fmt: skip
    # EK-13
    add("EK-13", "ej_tillampligt", "nej", "Underlaget prövar ingen skydds- eller kompensationsåtgärd.",
        motivering="Läge, markåtkomst, tidsförskjutning, drift och uppföljning av en åtgärd avgörs i genomförandet. Floden beräknar bara det skissade sambandet före och efter förslaget.")  # fmt: skip
    # EK-14
    add("EK-14", "underlag_saknas", "ja",
        f"Beräkningen kan återskapas ur körningens uppgifter (bilagan): nätverkets och markunderlagets kontrollsummor, cellstorleken {_m(nv('cellstorlek_m'))} och kodversionen står i körningens manifest.",
        belagg=[nv("cellstorlek_m")],
        saknat_underlag=["Markunderlagets källa, version och licens i det verkliga projektet. I demon är lagret påhittat."])  # fmt: skip
    # EK-15
    add("EK-15", "ej_tillampligt", "nej", "Underlaget skiljer inte observation, beräknat samband och sakkunnig bedömning.",
        motivering="Beräkningen är en modell för potentiell livsmiljö, okänd kvalitet. Vilka slutsatser som stöds av observation eller sakkunnig bedömning avgörs i ärendet.")  # fmt: skip
    valda = None if fragor is None else set(fragor)
    return [p for p in ut if valda is None or p["fraga_id"] in valda]


__all__ = ["EkologiKontekst", "kontekst_fran_rapport", "pastaenden"]
