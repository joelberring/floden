# ruff: noqa: E501
"""AI-fritt referensflöde: skissunderlag dagsljus och sol för demokvarterets körning (R2).

Fasta påståenden (inga modeller) för frågorna DL-01 till DL-15 och SO-01 till SO-15 ur
frågekatalogen, med belägg ur körningen `demo-dagsljus` (soltimmar på mark, VSC på fasadpunkter,
före och efter två ändringar). Används av `tools/rapport_demo.py --amne dagsljus` och testerna.
Statusen per fråga är den som `rapport.fragekatalog.FLODEN_FORMAGA` anger som `forval_status`
(testet kontrollerar att de stämmer). Talen är platshållare som Floden fyller; texten anger inte
om ett tal ökar eller minskar (värdet kommer ur körningen).

Samma texter används i språkprofilerna "sbk" och "neutral": påståendena är skrivna som
utredningstext, och profilen styr mallens text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rapport.sprak import scenarionamn

if TYPE_CHECKING:
    from rapport.skissrapport import Rapport, RapportTjanst


@dataclass(frozen=True)
class DagsljusKontekst:
    """Det referensflödet behöver veta om körningen."""

    sol: str
    """Analysens id för `dagsljus.soltimmar_mark`."""
    vsc: str | None
    """Analysens id för `dagsljus.vsc_fasad`, om körningen har den."""
    fore: str
    efter: tuple[tuple[str, str], ...]
    """(scenario, jämförelsens namn) för varje alternativ som jämförs med `fore`."""
    ruta: str | None
    mottagare: str | None


def kontekst_fran_rapport(rt: RapportTjanst, r: Rapport) -> DagsljusKontekst:
    """Läser analyser, scenarier och en ruta ur rapportens körning (inga tal)."""
    k = r.korningar[0]
    sol = next((a["analys"] for a in k["analyser"] if a["losare"] == "dagsljus.soltimmar_mark"), None)
    if sol is None:
        raise ValueError("Körningen har ingen soltimmar-analys (dagsljus.soltimmar_mark).")
    vsc = next((a["analys"] for a in k["analyser"] if a["losare"] == "dagsljus.vsc_fasad"), None)
    jf = [j for j in k["jamforelser"] if j["analys"] == sol]
    if not jf:
        raise ValueError("Körningen har ingen jämförelse av soltimmar före och efter.")
    fore = jf[0]["fore"]
    svar = rt.tjanst.resultat_sammanfattning(k["korning_id"], analys=sol, scenario=fore, max_rader=1)
    rad = next((x for t in svar["matvarden"] for x in t["rader"]), None)
    return DagsljusKontekst(
        sol=sol,
        vsc=vsc,
        fore=fore,
        efter=tuple((j["efter"], j["namn"]) for j in jf if j["fore"] == fore),
        ruta=None if rad is None else rad["ruta"],
        mottagare=r.mottagare[0].namn if r.mottagare else None,
    )


def _m(adress: str, opt: str = "") -> str:
    if not opt and (".antal_" in adress or adress.endswith(".datum")):
        opt = "enhet=nej"  # antal står utan enhet i löptexten ("3 553 fasadpunkter")
    if not opt and adress.endswith((".vsc_min_procent", ".kvot")):
        opt = "avrundning=0,1"
    return "{{matt:" + adress + (f"|{opt}" if opt else "") + "}}"


def _efter(scenario: str) -> str:
    n = scenarionamn(scenario)
    return f"i alternativet {n}"


def pastaenden(k: DagsljusKontekst, fragor: Sequence[str] | None = None) -> list[Mapping[str, Any]]:
    """Påståendena (argument till `RapportTjanst.lagg_till_pastaende`) för de frågor som ingår."""
    sol, vsc, fore = k.sol, k.vsc, k.fore
    har_vsc = vsc is not None
    v = vsc or ""

    def solmedel(s: str) -> str:
        return f"omrade.hela_omradet.{sol}.{s}.soltimmar.medel"

    def solyta(s: str) -> str:
        return f"omrade.hela_omradet.{sol}.{s}.soltimmar.yta_m2"

    def jf(namn: str, matt: str) -> str:
        return f"jamforelse.{namn}.{matt}"

    def fj(e: str, matt: str) -> str:
        return f"fasadjamforelse.{v}.{fore}.{e}.{matt}"

    def fa(s: str, matt: str) -> str:
        return f"fasad.{v}.{s}.{matt}"

    gr, kv = f"fasad.{v}.grans_procent", f"fasad.{v}.kvot"
    ut: list[dict[str, Any]] = []

    def add(fid: str, status: str, fordj: str, text: str, **kw: Any) -> None:
        ut.append({"fraga_id": fid, "status": status, "fordjupning": fordj, "text": text, **kw})

    def saknas(fid: str, text: str, saknat: str, fordj: str = "kan_inte_avgoras") -> None:
        add(fid, "underlag_saknas", fordj, text, saknat_underlag=[saknat])

    # ------------------------------------------------------------------ dagsljus
    add("DL-01", "ej_tillampligt", "nej", "Underlaget väljer ingen regelversion.",
        motivering="Regelval och byggåtgärd avgörs i ärendet. Skissen jämför läget före och efter ett förslag (grannpåverkan).")  # fmt: skip
    if har_vsc:
        bel = [fa(fore, "vsc_min_procent"), fa(fore, "antal_punkter"), fa(fore, "antal_under_grans"), gr, kv]
        text = (
            f"Vid fasadpunkterna är den lägsta vertikala himmelskomponenten (VSC) {_m(bel[0])} i "
            f"nuläget. Av {_m(bel[1])} fasadpunkter ligger {_m(bel[2])} under BRE:s gräns på {_m(gr)}."
        )
        for e, _n in k.efter:
            for a in (fj(e, "antal_under_grans_efter"), fj(e, "antal_jamforbara"), fj(e, "antal_under_kvot")):
                bel.append(a)
            text += (
                f" {_efter(e).capitalize()} ligger {_m(fj(e, 'antal_under_grans_efter'))} av "
                f"{_m(fj(e, 'antal_jamforbara'))} jämförbara fasadpunkter under gränsen, och "
                f"{_m(fj(e, 'antal_under_kvot'))} har en VSC under {_m(kv)} av värdet i nuläget."
            )
        add("DL-02", "underlag_saknas", "ja", text, belagg=bel, mottagare="hela_omradet",
            saknat_underlag=["Sektioner och kritiska fönster. Fasadpunkterna är genererade (en per våning och fasadsteg), inte verkliga fönster."])  # fmt: skip
    else:
        saknas("DL-02", "Körningen har ingen beräkning av dagsljus vid fasad.", "Beräkning av vertikal himmelskomponent (VSC) på fasadpunkter.", "ja")
    for fid, text, saknat in (
        ("DL-03", "Beräkningen har ingen rumsmodell.", "Fönstrets glasarea, karm, smyg, ljustransmittans, rumsdjup och reflektanser."),
        ("DL-04", "Beräkningen ger himmelskomponent vid fasad, inte dagsljusfaktor.", "Dagsljusfaktor i typrum med dokumenterad himmelsmodell, rutnät och analysplan."),
        ("DL-05", "Dagsljusfaktor i bostäder är inte beräknad.", "Dagsljusfaktor per bostad och bedömd vistelseyta."),
        ("DL-06", "Dagsljusfaktor i boendeenheter och delade utrymmen är inte beräknad.", "Dagsljusfaktor och ritnings-ID för boendeenhet och delade utrymmen."),
        ("DL-07", "Utblick är inte bedömd.", "Fönster, bröstningshöjd, sikthinder och plats för samvaro och måltid."),
        ("DL-08", "Dagsljus och utblick för en verksamhet är inte bedömda.", "Vistelsetyp, arbetsplatser och bedömningsmetod för verksamheten."),
    ):
        saknas(fid, text, saknat)
    add("DL-09", "ej_tillampligt", "nej", "Skissen prövar ingen ändrad användning.",
        motivering="Frågan gäller om kravet får anpassas vid en ändring. Det är en juridisk avvägning i ärendet, inte en beräkning.")  # fmt: skip
    if har_vsc:
        bel = []
        text = "Samma fasadpunkter jämförs före och efter förslaget (samma beteckning för punkten i båda lägena)."
        for e, _n in k.efter:
            adr = [fj(e, "antal_jamforbara"), fj(e, "antal_nytt_under_grans"), fj(e, "max_minskning_procentenheter"), gr]
            bel += adr
            text += (
                f" {_efter(e).capitalize()} går {_m(adr[1])} av {_m(adr[0])} jämförbara punkter från över till under "
                f"BRE:s gräns på {_m(gr)}, och den största minskningen i en punkt är {_m(adr[2])}."
            )
        text += " Planlösningarna är okända. Resultatet gäller fasadpunkter, inte rum."
        add("DL-10", "visat", "ja", text, belagg=list(dict.fromkeys(bel)), mottagare="hela_omradet")
    else:
        saknas("DL-10", "Körningen har ingen beräkning av dagsljus vid fasad.", "Beräkning av vertikal himmelskomponent (VSC) på fasadpunkter.", "ja")
    saknas("DL-11", "Glas, balkong och fasadkulör ingår inte i beräkningen.", "Prövade alternativ för glas, balkong och fasadkulör i de kritiska rummen. Balkonger modelleras inte.")
    if har_vsc:
        add("DL-12", "underlag_saknas", "ja",
            f"Värdet för var och en av de {_m(fa(fore, 'antal_punkter'))} fasadpunkterna i nuläget finns i körningens resultatfil och kan återskapas ur körningens uppgifter (bilaga).",
            belagg=[fa(fore, "antal_punkter")], mottagare="hela_omradet",
            saknat_underlag=["Lägenhetslista med ritningsrevision, bedömningsyta och godkänd delarea per bostad."])  # fmt: skip
    else:
        saknas("DL-12", "Beräkningen kan inte återskapas för fasad: körningen saknar den.", "Lägenhetslista med ritningsrevision och beräkning av dagsljus vid fasad.", "ja")
    saknas("DL-13", "Fönstermått och utblick för skol- och förskolelokaler är inte bedömda.", "Faktiska fönstermått, golvyta, användning, våningsläge och utblick från barnets höjd.")
    add("DL-14", "ej_tillampligt", "nej", "Underlaget kopplar inga instanser till slutsatser.",
        motivering="Att skilja hälsoskydd, byggkrav och arbetsmiljö är en bedömning i ärendet. Himmelskomponenten är ett geometriskt mått.")  # fmt: skip
    if har_vsc:
        bel, text = [], "För samma fasadpunkter före och efter förslaget:"
        for e, _n in k.efter:
            adr = [fj(e, "min_kvot"), fj(e, "antal_under_kvot"), fj(e, "antal_under_bada"), fj(e, "antal_nya"), kv]
            bel += adr
            text += (
                f" {_efter(e)} är den lägsta kvoten mellan VSC efter och före {_m(adr[0])}, {_m(adr[1])} punkter ligger under {_m(kv)} "
                f"av värdet före och {_m(adr[2])} punkter ligger under både BRE:s gräns och kvoten. Dessutom tillkommer {_m(adr[3])} nya fasadpunkter."
            )
        add("DL-15", "visat", "ja", text, belagg=list(dict.fromkeys(bel)), mottagare="hela_omradet")
    else:
        saknas("DL-15", "Körningen har ingen beräkning av dagsljus vid fasad.", "Beräkning av vertikal himmelskomponent (VSC) på fasadpunkter före och efter.", "ja")

    # ------------------------------------------------------------------ sol
    bel, text = [], ""
    for e, n in k.efter:
        adr = [jf(n, "yta_minskning_over_1h_m2"), jf(n, "yta_minskning_over_2h_m2"), jf(n, "max_minskning")]
        bel += adr
        text += (
            f" {_efter(e).capitalize()} förlorar {_m(adr[0])} av marken mer än en timme direkt sol per dygn och {_m(adr[1])} mer än två timmar. "
            f"Den största förlusten i en cell är {_m(adr[2])}."
        )
    add("SO-01", "underlag_saknas", "ja", "Ytan som förlorar sol ser ut så här:" + text + " Underlag saknas för att bedöma sol vid grannfönster och mottagarnas funktion.",
        belagg=bel, mottagare="hela_omradet",
        saknat_underlag=["Grannfönster och vistelseytor med funktion, läge och våningsplan. Soltimmarna gäller markytan, inte fasad."])  # fmt: skip
    saknas("SO-02", "Modellen visar de ändringar som lagts in som alternativ.", "Tillåten byggrätt med höjd, placering, tak och etapper, och ritningsrevision.")
    if k.ruta is not None:
        adr = [f"matvarde.{sol}.{fore}.{k.ruta}.lat_grader", f"matvarde.{sol}.{fore}.{k.ruta}.lon_grader"]
        add("SO-03", "underlag_saknas", "ja",
            f"Solläget beräknas för mittpunkten i varje ruta, till exempel latitud {_m(adr[0], 'avrundning=0,01')} och longitud {_m(adr[1], 'avrundning=0,01')}. Solpositionen är kontrollerad mot en referensalgoritm från NREL.",
            belagg=adr,
            saknat_underlag=["Tidszon och förskjutning mot UTC per datum, geografiskt norr och korrigering av kartnorr."])  # fmt: skip
        add("SO-04", "underlag_saknas", "ja",
            f"Soltimmarna är beräknade för {_m(f'matvarde.{sol}.{fore}.{k.ruta}.datum')} datum och gäller hela dygnet.",
            belagg=[f"matvarde.{sol}.{fore}.{k.ruta}.datum"],
            saknat_underlag=["Val av dagar och brukstider för platsens användning, till exempel öppettid. Tidsfönster kan inte anges i beräkningen."])  # fmt: skip
        add("SO-15", "visat", "ja",
            f"Kartorna visar dygnssumman av direkt sol på marken, som medel över {_m(f'matvarde.{sol}.{fore}.{k.ruta}.datum')} datum. Det är ingen ögonblicksbild. Skillnadskartorna visar förändringen i förhållande till nuläget i hela beräkningsområdet.",
            belagg=[f"matvarde.{sol}.{fore}.{k.ruta}.datum"])  # fmt: skip
    else:
        saknas("SO-03", "Solläget är inte redovisat.", "Latitud, longitud, tidszon och datum för beräkningen.", "ja")
        saknas("SO-04", "Datum och brukstider är inte redovisade.", "Val av dagar och brukstider.", "ja")
        saknas("SO-15", "Perioden för kartorna är inte redovisad.", "Datum och tidssteg för kartorna.", "ja")
    add("SO-05", "underlag_saknas", "ja",
        f"I beräkningen kastar terräng och byggnader skugga. Direkt sol på marken är {_m(solmedel(fore))} per dygn i nuläget. Balkonger, sadeltak och vegetation ingår inte.",
        belagg=[solmedel(fore)], mottagare="hela_omradet",
        saknat_underlag=["Balkonger, sadeltak och vegetation med krongeometri, lövstatus och genomsläpplighet."])  # fmt: skip
    if k.mottagare is not None:
        m = k.mottagare
        bel = [f"omrade.{m}.{sol}.{fore}.soltimmar.medel"]
        text = f"På den angivna platsen är direkt sol på marken {_m(bel[0])} per dygn i nuläget"
        for e, _n in k.efter:
            adr_s = f"omrade.{m}.{sol}.{e}.soltimmar.medel"
            bel.append(adr_s)
            text += f" och {_m(adr_s)} {_efter(e)}"
        text += ". Byggnadernas tak räknas inte som mark."
        add("SO-06", "underlag_saknas", "ja", text, belagg=bel, mottagare=m,
            saknat_underlag=["Gårdens funktionsytor (aktiv lek och reträtt) som egna ytor, och bedömning mot lekvärdesriktlinjerna."])  # fmt: skip
    else:
        saknas("SO-06", "Ingen plats med funktion är angiven som mottagare.", "Gårdens funktionsytor (aktiv lek och reträtt) som egna ytor med polygon.", "ja")
    saknas("SO-07", "Vegetationsskugga och skuggad friyta räknas inte.", "Gårdens zoner, friyta, träd med antagen storlek och skuggad andel vid angivet klockslag.")
    bel = [solmedel(fore), solyta(fore)]
    text = f"Direkt sol på marken är {_m(solmedel(fore))} per dygn i nuläget (summerad tid över dygnet)"
    for e, _n in k.efter:
        bel += [solmedel(e), solyta(e)]
        text += f" och {_m(solmedel(e))} {_efter(e)}"
    text += f". Medelvärdet gäller markytan, som är {_m(solyta(fore))} i nuläget"
    for e, _n in k.efter:
        text += f" och {_m(solyta(e))} {_efter(e)}"
    text += "."
    add("SO-08", "underlag_saknas", "ja", text, belagg=bel, mottagare="hela_omradet",
        saknat_underlag=["Sammanhängande tid och tid inom ett tidsfönster (brukstid) för varje mottagare."])  # fmt: skip
    bel, text = [], "Skuggan tillkommer där differenskartorna är blå."
    for e, n in k.efter:
        adr = [jf(n, "yta_minskning_m2"), jf(n, "yta_minskning_over_1h_m2"), jf(n, "max_minskning"), jf(n, "yta_okning_m2")]
        bel += adr
        text += (
            f" {_efter(e).capitalize()} förlorar {_m(adr[0])} av marken sol över tröskelvärdet, varav {_m(adr[1])} förlorar mer än en timme per dygn. "
            f"Den största förlusten i en cell är {_m(adr[2])}. Ytan som får mer sol är {_m(adr[3])}."
        )
    text += " Kartorna före och efter har samma färgskala, så att platser som redan låg i skugga syns."
    add("SO-09", "visat", "ja", text, belagg=bel, mottagare="hela_omradet")
    saknas("SO-10", "Soltimmarna gäller marken. Sol vid fönster och inne i rum är inte beräknad.", "Referenspunkt, fasadriktning, smyg och avskärmning för sol vid fönster och i rum.")
    bel = []
    text = "Alternativen jämförs med samma mått, förlorad markyta med mer än en timme sol per dygn:"
    for e, n in k.efter:
        adr_s = jf(n, "yta_minskning_over_1h_m2")
        bel.append(adr_s)
        text += f" {_efter(e)} {_m(adr_s)};"
    text = text.rstrip(";") + ". Beräkningen föreslår ingen åtgärd."
    add("SO-11", "underlag_saknas", "ja", text, belagg=bel, mottagare="hela_omradet",
        saknat_underlag=["Åtgärdsförslag som egna alternativ: indragen våning, ändrad volym, omplacerad lekyta eller skuggande träd."])  # fmt: skip
    add("SO-12", "underlag_saknas", "ja",
        f"Kartorna har norrpil, skalstock, teckenförklaring, enhet och namn på alternativet, och varje tal har ett belägg i bilagan. Direkt sol på marken är {_m(solmedel(fore))} per dygn i nuläget.",
        belagg=[solmedel(fore)], mottagare="hela_omradet",
        saknat_underlag=["Tidszon och tidsdata per tidssteg för slutsatser om timmar."])  # fmt: skip
    saknas("SO-13", "Skydd för stillasittande lek och samling är inte bedömt.", "Funktionsytor med skugga under brukstiden och vegetation eller konstruktion som ger skydd.")
    saknas("SO-14", "Beräkningen visar sol och skugga i lokal geometri men ingen termisk analys.", "Värmekartläggningens yttemperatur som bakgrund och en termisk analys om frågan gäller värmestress.", "ja")
    valda = None if fragor is None else set(fragor)
    return [p for p in ut if valda is None or p["fraga_id"] in valda]


__all__ = ["DagsljusKontekst", "kontekst_fran_rapport", "pastaenden"]
