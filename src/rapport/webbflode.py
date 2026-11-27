# ruff: noqa: E501
"""Referensflödet för rapporter som skapas i webben (utan AI): påståenden ur körningens egna scenarier.

`rapport.referensflode` (skyfall) och `rapport.referensflode_dagsljus` är skrivna för demokvarteret
(scenarierna `fore`/`efter`, jämförelsen `skyfall_max_djup_efter_minus_fore`, mottagaren gården). Här läses analys,
före-scenario, efter-scenario och jämförelsens namn ur körningen, så att samma påståenden gäller för ett projekt
i en geografi (scenarierna `bas` och projektets egna) och även när körningen enbart visar **läget idag** (nuläge
utan ritförslag). Texterna och beläggsformen är oförändrade; ingen modell används och inget tal skrivs för hand
(talen är platshållare som Floden fyller).

När projektet har en avgränsningspolygon ersätts `"hela beräkningsområdet"` med `"inom projektets polygon"`.
Ämnen som saknar rapportmall (`AMNEN_MED_MALL`) kan inte skapas här; `saknad_mall` förklarar varför.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from rapport.referensflode import JFR, gardens_mottagare, pastaenden
from rapport.referensflode_dagsljus import DagsljusKontekst, kontekst_fran_rapport
from rapport.referensflode_dagsljus import pastaenden as pastaenden_dagsljus
from rapport.referensflode_ekologi import EkologiKontekst, _kalla_kort
from rapport.referensflode_ekologi import pastaenden as pastaenden_ekologi
from rapport.referensflode_luft import kontekst_fran_rapport as kontekst_luft_fran_rapport
from rapport.referensflode_luft import pastaenden as pastaenden_luft
from rapport.skissrapport import AMNEN_NIVA1

if TYPE_CHECKING:
    from rapport.skissrapport import Rapport, RapportTjanst

AMNEN_MED_MALL: Final[tuple[str, ...]] = ("skyfall", "dagsljus", "ekologi", "luft")
"""Ämnen som webbflödet har påståenden för (R1 och R2)."""

AMNESNAMN: Final[dict[str, str]] = {"skyfall": "Skyfall", "dagsljus": "Dagsljus och sol", "ekologi": "Ekologi", "luft": "Luft"}

INOM_PROJEKTETS_POLYGON: Final[str] = "inom projektets polygon"
"""Ersätter 'hela beräkningsområdet' i webbflödets påståenden när projektet har en avgränsningspolygon."""


def _m(adress: str, opt: str = "") -> str:
    return "{{matt:" + adress + (f"|{opt}" if opt else "") + "}}"


def anpassa_omradestext(text: str, *, har_avgransning: bool) -> str:
    """Ersätter 'hela beräkningsområdet' med 'inom projektets polygon' när en projektpolygon finns."""
    if not har_avgransning:
        return text
    return (
        text.replace("i hela beräkningsområdet", INOM_PROJEKTETS_POLYGON)
        .replace("hela beräkningsområdet", INOM_PROJEKTETS_POLYGON)
    )


def _anpassa_pastaende(p: Mapping[str, Any], *, har_avgransning: bool) -> dict[str, Any]:
    d = dict(p)
    if not har_avgransning:
        return d
    if "text" in d and isinstance(d["text"], str):
        d["text"] = anpassa_omradestext(d["text"], har_avgransning=True)
    if "motivering" in d and isinstance(d["motivering"], str):
        d["motivering"] = anpassa_omradestext(d["motivering"], har_avgransning=True)
    if "saknat_underlag" in d and isinstance(d["saknat_underlag"], list):
        d["saknat_underlag"] = [
            anpassa_omradestext(s, har_avgransning=True) if isinstance(s, str) else s
            for s in d["saknat_underlag"]
        ]
    return d


def har_avgransningspolygon(rt: RapportTjanst | None, r: Rapport) -> bool:
    """Avgör om projektet / körningen har en avgränsningspolygon i EPSG:3011."""
    if not r.korningar:
        return False
    k = r.korningar[0]
    if k.get("har_avgransning") or k.get("avgransning_polygon_3011"):
        return True
    if rt is not None:
        kalla = getattr(rt, "kalla", None)
        if kalla is not None and hasattr(kalla, "har_avgransning"):
            korning_id = k.get("korning_id")
            if korning_id:
                try:
                    return bool(kalla.har_avgransning(str(korning_id)))
                except Exception:  # noqa: BLE001 - hellre False än oväntad krasch
                    return False
    return False


def _bas_scenario(k: Mapping[str, Any], analys: str) -> str | None:
    """Läser bas-/nulägesscenariot ur körningsposten utan att gissa om inga scenarier finns."""
    kandidater: list[str] = []
    for a in k.get("analyser", ()):
        if a.get("analys") == analys and a.get("scenarier"):
            kandidater.extend(str(s) for s in a["scenarier"])
    if not kandidater and k.get("scenarier"):
        kandidater.extend(str(s) for s in k["scenarier"])
    if not kandidater:
        return None
    for forval in ("fore", "bas", "nulage", "lage_idag"):
        if forval in kandidater:
            return forval
    return kandidater[0]


def ar_lage_idag(r: Rapport) -> bool:
    """Sant om rapportens körning enbart avser läget idag (nuläge utan jämförelse mot ritförslag)."""
    if not r.korningar:
        return False
    k = r.korningar[0]
    underlag = k.get("underlag")
    if isinstance(underlag, Mapping) and underlag.get("slag") == "lage_idag":
        return True
    return not k.get("jamforelser")


def saknad_mall(amne: str) -> str | None:
    """Text som förklarar varför ämnet inte kan bli en rapport än, annars None."""
    if amne not in AMNESNAMN:
        return f"Okänt ämne '{amne}'."
    if amne not in AMNEN_NIVA1:
        return (
            f"Rapportmall (R2) för {AMNESNAMN[amne].lower()} saknas än: rapporten kan inte skapas förrän mallen "
            "finns. Beräkningsresultaten finns i steg 2."
        )
    if amne not in AMNEN_MED_MALL:
        return (
            f"Webbflödet har inga påståenden för {AMNESNAMN[amne].lower()} än. Skissunderlaget (R2) kan "
            "skapas via MCP (rapport_skapa)."
        )
    return None


def _regnkalla_och_typ(
    r: Rapport,
    *,
    rt: RapportTjanst | None = None,
    analys: str | None = None,
) -> tuple[str, str | None]:
    """Läser faktisk IDF-källa och regntyp ur körningens antaganden (med bakåtkompatibel reserv)."""
    standard = ("Stockholmsregn 2024 (Tabell 7)", "block")
    if not r.korningar:
        return standard
    k = r.korningar[0]
    if rt is not None:
        kalla = getattr(rt, "kalla", None)
        if kalla is not None and hasattr(kalla, "_resultat"):
            try:
                res = kalla._resultat(str(k["korning_id"]))  # noqa: SLF001
                for post in res.get("antaganden", {}).get("regn", []):
                    if analys is not None and post.get("analys") != analys:
                        continue
                    rapport = post.get("rapport") or post.get("kalla")
                    if rapport:
                        tabell = post.get("tabell")
                        namn = f"{rapport} ({tabell})" if tabell else str(rapport)
                        rtyp = str(post["regntyp"]) if post.get("regntyp") else None
                        return namn, rtyp
            except Exception:  # noqa: BLE001 - faller tillbaka till lagrade antaganden
                pass
    for rad in k.get("antaganden", ()):
        s = str(rad).strip()
        if not s.startswith("Regn:"):
            continue
        efter_prefix = s.removeprefix("Regn:").strip()
        forsta = efter_prefix.split(",", 1)[0].strip().rstrip(".")
        if not forsta or forsta.lower().startswith(("angiven regnmängd", "angivet regn")):
            continue
        rtyp_text: str | None = None
        if "blockregn" in efter_prefix:
            rtyp_text = "block"
        elif "cdsregn" in efter_prefix:
            rtyp_text = "cds"
        return forsta, rtyp_text
    return standard


def _skyfall_pastaenden_lage_idag(
    analys: str,
    bas: str,
    *,
    demo: bool,
    regn_ur_idf: bool,
    sprakprofil: str,
    regnkalla: str = "Stockholmsregn 2024 (Tabell 7)",
    regntyp: str | None = "block",
) -> list[Mapping[str, Any]]:
    """Påståenden för en skyfallsrapport som enbart redovisar läget idag (utan ritförslag)."""
    mv = f"matvarde.{analys}.{bas}"
    sbk = sprakprofil == "sbk"
    regntyp_del = f" som {regntyp}regn" if regntyp else ""
    ut: list[Mapping[str, Any]] = [
        {
            "fraga_id": "SK-01",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": (
                f"I läget idag är det största vattendjupet i hela beräkningsområdet {_m(mv + '.max_djup_m')}. "
                "Rapporten avser enbart befintligt läge utan ritförslag; underlag saknas för att bedöma hur "
                "översvämningen påverkar markens användning."
                if sbk
                else f"Största vattendjup i hela beräkningsområdet i läget idag är {_m(mv + '.max_djup_m')} "
                "(inget ritförslag ingår)."
            ),
            "belagg": [f"{mv}.max_djup_m"],
            "saknat_underlag": [
                "Markanvändning, känsliga byggnader, entréer och riskkällor ingår inte i underlaget."
            ],
            "ansvarig_funktion": "Beställaren (planhandläggaren)",
        },
        (
            {
                "fraga_id": "SK-02",
                "status": "villkorat",
                "fordjupning": "ja",
                "text": (
                    f"Sållningen av läget idag är gjord med {regnkalla}{regntyp_del}: "
                    f"återkomsttid {_m(mv + '.aterkomsttid_ar')}, varaktighet "
                    f"{_m(mv + '.varaktighet_min')}, klimatfaktor "
                    f"{_m(mv + '.klimatfaktor', 'avrundning=0,1')} och regnmängd "
                    f"{_m(mv + '.regn_mm', 'avrundning=1')}. Vilket regn och vilken säkerhetsnivå som ska "
                    "gälla behöver bestämmas i planarbetet."
                ),
                "belagg": [
                    f"{mv}.aterkomsttid_ar",
                    f"{mv}.varaktighet_min",
                    f"{mv}.klimatfaktor",
                    f"{mv}.regn_mm",
                ],
                "forutsattning": "Beställaren bestämmer dimensionerande regn och säkerhetsnivå, "
                "med högre nivå om samhällsviktig verksamhet berörs.",
                "ansvarig_funktion": "Beställaren tillsammans med stadens skyfallsfunktion",
            }
            if regn_ur_idf
            else {
                "fraga_id": "SK-02",
                "status": "underlag_saknas",
                "fordjupning": "kan_inte_avgoras",
                "text": "Körningen använder ett fast regndjup utan återkomsttid och varaktighet som mätvärden, så regnet "
                "kan inte redovisas här. Vilket regn och vilken säkerhetsnivå som ska gälla behöver bestämmas i planarbetet.",
                "saknat_underlag": ["Dimensionerande regn (återkomsttid, varaktighet, klimatfaktor) för projektet."],
            }
        ),
        {
            "fraga_id": "SK-03",
            "status": "underlag_saknas",
            "fordjupning": "kan_inte_avgoras",
            "text": (
                f"Under regnet rinner {_m(mv + '.ut_m3')} vatten ut över beräkningsområdets kant i läget idag. "
                "Underlag saknas för att bedöma vart vattnet tar vägen efter plangränsen."
            ),
            "belagg": [f"{mv}.ut_m3"],
            "saknat_underlag": [
                "Tillrinningsområde, plangräns och mottagare nedströms. Beräkningen låter vattnet rinna fritt ut över områdets kant."
            ],
        },
        {
            "fraga_id": "SK-04",
            "status": "underlag_saknas",
            "fordjupning": "kan_inte_avgoras",
            "text": "Beräkningen redovisar inte höjder och öppningar för enskilda objekt. Frågan behöver utredas vidare i planarbetet.",
            "saknat_underlag": [
                "Terrängmodell med portar, trummor, kantstenar och trösklar vid entréer i höjdsystemet RH 2000, kontrollerad på plats."
            ],
        },
        {
            "fraga_id": "SK-05",
            "status": "visat",
            "fordjupning": "nej",
            "text": (
                "Beräkningen förutsätter att ledningsnätet är fullt under skyfallet. "
                f"Avtappningen till nätet är {_m(mv + '.avtappning_mm_h')}. Regnet på taken rinner ned till "
                "marken längs fasaderna, och andelen takvatten som leds direkt till ledningsnätet är "
                f"{_m(mv + '.takvatten_andel_ledning')}. Beräkningen tar inte hänsyn till infiltration i marken."
            ),
            "belagg": [f"{mv}.avtappning_mm_h", f"{mv}.takvatten_andel_ledning"],
        },
        {
            "fraga_id": "SK-06",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Rapporten redovisar enbart läget idag utan ritförslag; ingen förändring före och efter har beräknats.",
            "motivering": "Inget ritförslag är prövat i körningen (enbart nuläge).",
        },
        (
            {
                "fraga_id": "SK-07",
                "status": "underlag_saknas",
                "fordjupning": "ja",
                "text": (
                    f"Den stående vattenvolymen i lågpunkterna är {_m(mv + '.lagrat_m3')} i läget idag. "
                    "Underlag saknas för att bedöma skaderisk för grannar eller nedströms."
                ),
                "belagg": [f"{mv}.lagrat_m3"],
                "saknat_underlag": [
                    "Flöde och varaktighet vid grannar och mottagare nedströms samt jämförelse mot ett ritförslag."
                ],
            }
            if demo
            else {
                "fraga_id": "SK-07",
                "status": "underlag_saknas",
                "fordjupning": "kan_inte_avgoras",
                "text": "Platser som påverkas (mottagare) är inte angivna för projektet, så skadeverkan på enskilda platser bedöms inte.",
                "saknat_underlag": ["Mottagare: platser som påverkas, som polygoner i EPSG:3011."],
            }
        ),
        {
            "fraga_id": "SK-08",
            "status": "underlag_saknas",
            "fordjupning": "kan_inte_avgoras",
            "text": "Framkomligheten är inte bedömd. Underlag saknas för att bedöma när tillträdet begränsas.",
            "saknat_underlag": [
                "Väg till entréerna, fastställd gräns för hur djupt vatten fordon klarar och tidsserie för vattendjup och hastighet."
            ],
        },
        {
            "fraga_id": "SK-09",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Skissen prövar ingen skyddsåtgärd.",
            "motivering": "Frågan gäller genomförande och drift av en skyddsåtgärd; körningen visar enbart läget idag.",
        },
        {
            "fraga_id": "SK-10",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": (
                f"Det relativa felet i beräkningens vattenbalans är {_m(mv + '.volymfel_rel', 'format=exp')} i läget idag. "
                "Känslighetsberäkningar har inte gjorts."
            ),
            "belagg": [f"{mv}.volymfel_rel"],
            "saknat_underlag": [
                "Känslighetsberäkningar för terräng, regn och avledning, och jämförelse med ett oberoende underlag."
            ],
        },
        {
            "fraga_id": "SK-11",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Underlaget är ingen dagvattenutredning.",
            "motivering": "Floden räknar inte vardagsregn och rening. Frågan prövas i dagvattenutredningen.",
        },
        {
            "fraga_id": "SK-12",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": (
                "Kartorna visar det största vattendjupet i meter i läget idag, med teckenförklaring och "
                f"förklaring av ytor utan värde. Det största djupet i hela beräkningsområdet är {_m(mv + '.max_djup_m')}."
            ),
            "belagg": [f"{mv}.max_djup_m"],
            "saknat_underlag": [
                "Vattenytans höjd i höjdsystemet RH 2000, sektioner genom kritiska passager och tidsserie."
            ],
        },
        {
            "fraga_id": "SK-13",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Inga åtgärder utanför beräkningsområdet ingår.",
            "motivering": "Skissen redovisar enbart läget idag utan åtgärder utanför området.",
        },
        {
            "fraga_id": "SK-14",
            "status": "underlag_saknas",
            "fordjupning": "kan_inte_avgoras",
            "text": "Räddningsväg och insatsplats är inte bedömda.",
            "saknat_underlag": [
                "Gräns för fordonens framkomlighet ur brandunderlaget och beräkning av vattendjup och hastighet längs räddningsvägen."
            ],
        },
        {
            "fraga_id": "SK-15",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Underlaget omfattar ett beräkningsområde och en utredning.",
            "motivering": "Ingen sammanvägning av flera delområden eller utredningar behövs.",
        },
    ]
    return ut


def skyfall_pastaenden(
    r: Rapport,
    *,
    demo: bool,
    regn_ur_idf: bool = True,
    har_avgransning: bool = False,
    regnkalla: str | None = None,
    regntyp: str | None = None,
) -> list[Mapping[str, Any]]:
    k = r.korningar[0]
    analys = next((a["analys"] for a in k["analyser"] if str(a["losare"]).startswith("vatten.")), None)
    if analys is None:
        raise ValueError("Körningen har ingen skyfallsanalys (vatten.*).")
    if regnkalla is None:
        hittad_kalla, hittad_typ = _regnkalla_och_typ(r, analys=analys)
        regnkalla = hittad_kalla
        if regntyp is None:
            regntyp = hittad_typ
    jf = next((j for j in k["jamforelser"] if j["analys"] == analys and j["utdata"] == "max_djup"), None)
    if jf is None:
        bas = _bas_scenario(k, analys)
        if bas is None:
            raise ValueError(
                "Körningen har ingen skyfallsanalys med jämförelse av maxdjup före och efter (K20 [[jamforelser]]) "
                "och saknar angivet scenario för läget idag."
            )
        alla = _skyfall_pastaenden_lage_idag(
            analys,
            bas,
            demo=demo,
            regn_ur_idf=regn_ur_idf,
            sprakprofil=r.installningar.sprakprofil,
            regnkalla=regnkalla,
            regntyp=regntyp,
        )
        return [_anpassa_pastaende(p, har_avgransning=har_avgransning) for p in alla]

    fore, efter, namn = str(jf["fore"]), str(jf["efter"]), str(jf["namn"])

    def byt(t: str) -> str:
        return (
            t.replace(f"matvarde.{analys}.fore.", f"matvarde.{analys}.{fore}.")
            .replace(f"matvarde.{analys}.efter.", f"matvarde.{analys}.{efter}.")
            .replace(f".{analys}.efter.", f".{analys}.{efter}.")
            .replace(JFR, namn)
        )

    ut: list[Mapping[str, Any]] = []
    for p in pastaenden(analys, r.installningar.sprakprofil, regnkalla=regnkalla, regntyp=regntyp):
        d = dict(p)
        if d["fraga_id"] == "SK-07" and not demo:
            # Mottagare (platser som påverkas) anges inte i webbflödet: säg det i stället för att gissa.
            d = {
                "fraga_id": "SK-07",
                "status": "underlag_saknas",
                "fordjupning": "kan_inte_avgoras",
                "text": "Platser som påverkas (mottagare) är inte angivna för projektet, så skadeverkan på enskilda platser bedöms inte.",
                "saknat_underlag": ["Mottagare: platser som påverkas, som polygoner i EPSG:3011."],
            }
        elif d["fraga_id"] == "SK-02" and not regn_ur_idf:
            # Projektets körning har ett fast regndjup (`regn_mm`), inte en IDF-källa: regnet finns då inte som
            # mätvärden alls, så ingen siffra påstås.
            d = {
                "fraga_id": "SK-02",
                "status": "underlag_saknas",
                "fordjupning": "kan_inte_avgoras",
                "text": "Körningen använder ett fast regndjup utan återkomsttid och varaktighet som mätvärden, så regnet "
                "kan inte redovisas här. Vilket regn och vilken säkerhetsnivå som ska gälla behöver bestämmas i planarbetet.",
                "saknat_underlag": ["Dimensionerande regn (återkomsttid, varaktighet, klimatfaktor) för projektet."],
            }
        else:
            d["text"] = byt(str(d["text"]))
            if "belagg" in d:
                d["belagg"] = [byt(b) for b in d["belagg"]]
        ut.append(_anpassa_pastaende(d, har_avgransning=har_avgransning))
    return ut


def dagsljus_pastaenden(
    rt: RapportTjanst,
    r: Rapport,
    *,
    har_avgransning: bool = False,
) -> list[Mapping[str, Any]]:
    """Påståenden för dagsljus och sol — stöder både före/efter och enbart läget idag."""
    k = r.korningar[0]
    sol = next((a["analys"] for a in k["analyser"] if a["losare"] == "dagsljus.soltimmar_mark"), None)
    if sol is None:
        raise ValueError("Körningen har ingen soltimmar-analys (dagsljus.soltimmar_mark).")
    jf = [j for j in k["jamforelser"] if j["analys"] == sol]
    if jf:
        alla = pastaenden_dagsljus(kontekst_fran_rapport(rt, r), r.fragor)
        return [_anpassa_pastaende(p, har_avgransning=har_avgransning) for p in alla]

    bas = _bas_scenario(k, sol)
    if bas is None:
        raise ValueError(
            "Körningen har ingen jämförelse av soltimmar före och efter och saknar angivet scenario för läget idag."
        )
    vsc = next((a["analys"] for a in k["analyser"] if a["losare"] == "dagsljus.vsc_fasad"), None)
    ruta: str | None = None
    try:
        svar = rt.tjanst.resultat_sammanfattning(str(k["korning_id"]), analys=sol, scenario=bas, max_rader=1)
        rad = next((x for t in svar["matvarden"] for x in t["rader"]), None)
        ruta = None if rad is None else rad["ruta"]
    except Exception:  # noqa: BLE001 - ruta kan saknas i syntetiska tester
        ruta = None

    kontekst = DagsljusKontekst(
        sol=sol,
        vsc=vsc,
        fore=bas,
        efter=(),
        ruta=ruta,
        mottagare=r.mottagare[0].namn if r.mottagare else None,
    )
    ersatt_lage_idag: dict[str, dict[str, Any]] = {
        "DL-10": {
            "fraga_id": "DL-10",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Rapporten redovisar enbart läget idag utan ritförslag; ingen jämförelse före och efter har gjorts.",
            "motivering": "Inget ritförslag är prövat i körningen (enbart nuläge).",
        },
        "DL-15": {
            "fraga_id": "DL-15",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Rapporten redovisar enbart läget idag utan ritförslag; ingen kvot före och efter har beräknats.",
            "motivering": "Inget ritförslag är prövat i körningen (enbart nuläge).",
        },
        "SO-01": {
            "fraga_id": "SO-01",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Rapporten redovisar enbart läget idag utan ritförslag; ingen solförlust mot nuläget har beräknats.",
            "motivering": "Inget ritförslag är prövat i körningen (enbart nuläge).",
        },
        "SO-09": {
            "fraga_id": "SO-09",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Rapporten redovisar enbart läget idag utan ritförslag; inga differenskartor ingår.",
            "motivering": "Inget ritförslag är prövat i körningen (enbart nuläge).",
        },
        "SO-11": {
            "fraga_id": "SO-11",
            "status": "ej_tillampligt",
            "fordjupning": "nej",
            "text": "Rapporten redovisar enbart läget idag utan ritförslag; inga alternativ jämförs.",
            "motivering": "Inget ritförslag är prövat i körningen (enbart nuläge).",
        },
    }
    ut: list[Mapping[str, Any]] = []
    for p in pastaenden_dagsljus(kontekst, r.fragor):
        fid = str(p["fraga_id"])
        if fid in ersatt_lage_idag:
            d = dict(ersatt_lage_idag[fid])
        elif fid == "SO-15" and ruta is not None:
            d = dict(p)
            d["text"] = (
                f"Kartorna visar dygnssumman av direkt sol på marken i läget idag, som medel över "
                f"{_m(f'matvarde.{sol}.{bas}.{ruta}.datum', 'enhet=nej')} datum i hela beräkningsområdet. "
                "Det är ingen ögonblicksbild."
            )
        else:
            d = dict(p)
        ut.append(_anpassa_pastaende(d, har_avgransning=har_avgransning))
    return ut


def mottagare_for(demo: bool) -> list[dict[str, Any]]:
    return [gardens_mottagare()] if demo else []


def _regn_ur_idf(rt: RapportTjanst, r: Rapport) -> bool:
    """Har körningens scenario regnets återkomsttid som mätvärde (IDF-källa), inte bara ett fast regndjup?"""
    k = r.korningar[0]
    analys = next((a["analys"] for a in k["analyser"] if str(a["losare"]).startswith("vatten.")), None)
    if analys is None:
        return True
    jf = next((j for j in k["jamforelser"] if j["analys"] == analys and j["utdata"] == "max_djup"), None)
    scenario = str(jf["efter"]) if jf is not None else _bas_scenario(k, analys)
    if scenario is None:
        return True
    try:
        svar = rt.tjanst.resultat_sammanfattning(
            str(k["korning_id"]), analys=analys, scenario=scenario, ruta=None, max_rader=500
        )
    except Exception:  # noqa: BLE001 - beläggskontrollen ger det riktiga felet
        return True
    rader = [x for t in svar["matvarden"] for x in t["rader"]]
    return bool(rader) and "aterkomsttid_ar" in rader[0]["varden"]


def ekologi_pastaenden(
    rt: RapportTjanst,
    r: Rapport,
    *,
    demo: bool,
    har_avgransning: bool = False,
) -> list[Mapping[str, Any]]:
    """Påståenden för ekologi (EK-01–EK-15) ur `referensflode_ekologi`.

    Deduplicerar efter-scenarier när körningen har flera utdataroller (t.ex. `stromtathet` och
    `kansighet`), anpassar EK-14 när projektet inte är demokvarteret, och stöder även nuläge
    (`lage_idag` utan jämförelse) när sådant underlag skickas in direkt."""
    from rapport.ekologibelagg import EKOLOGI_LOSARE, _forsta_dok, skrivbord_pastaendeforslag

    k = r.korningar[0]
    analys = next((a["analys"] for a in k["analyser"] if a["losare"] == EKOLOGI_LOSARE), None)
    if analys is None:
        raise ValueError("Körningen har ingen ekologianalys (ekologi.konnektivitet).")
    jf = [j for j in k["jamforelser"] if j["analys"] == analys]
    if jf:
        ordnade_jf = sorted(jf, key=lambda j: 0 if j.get("utdata") == "stromtathet" else 1)
        fore = str(ordnade_jf[0]["fore"])
        sedda_efter: set[str] = set()
        efter_par: list[tuple[str, str]] = []
        for j in ordnade_jf:
            if str(j["fore"]) != fore:
                continue
            efter_id = str(j["efter"])
            if efter_id in sedda_efter:
                continue
            sedda_efter.add(efter_id)
            efter_par.append((efter_id, str(j["namn"])))
    else:
        bas = _bas_scenario(k, analys)
        if bas is None:
            raise ValueError(
                "Körningen har ingen ekologianalys med jämförelse före och efter och saknar scenario för läget idag."
            )
        fore = bas
        efter_par = []

    ruta: str | None = None
    try:
        svar = rt.tjanst.resultat_sammanfattning(str(k["korning_id"]), analys=analys, scenario=fore, max_rader=1)
        rad = next((x for t in svar["matvarden"] for x in t["rader"]), None)
        ruta = None if rad is None else rad["ruta"]
    except Exception:  # noqa: BLE001 - ruta kan saknas i syntetiska tester
        ruta = None

    dok = _forsta_dok(rt.kalla, str(k["korning_id"]), analys, fore)
    if dok is None:
        raise ValueError("Körningen saknar ekologi.json.")
    nv = dok.get("natverk", {})
    kontekst = EkologiKontekst(
        analys=analys,
        fore=fore,
        efter=tuple(efter_par),
        ruta=ruta,
        signalarter=tuple(str(a) for a in nv.get("signalarter", [])),
        natverk_namn=str(nv.get("namn", "")),
        kalla_kort=_kalla_kort(str(nv.get("kalla", {}).get("referens", ""))),
        d50_antagen=bool(nv.get("d50_antagen")),
    )
    ruta_sfx = f".{ruta}" if ruta else ""

    def mv(matt: str) -> str:
        return f"matvarde.{analys}.{fore}{ruta_sfx}.{matt}"

    def nv_adr(matt: str) -> str:
        return f"ekonatverk.{analys}.{fore}{ruta_sfx}.{matt}"

    skrivbord_forslag: dict[str, dict[str, Any]] = {}
    try:
        skrivbord_forslag = skrivbord_pastaendeforslag(rt.kalla.utmapp(str(k["korning_id"])))
    except Exception:  # noqa: BLE001 - saknad eller ogiltig skrivbordsmapp faller tillbaka rent
        skrivbord_forslag = {}

    ut: list[Mapping[str, Any]] = []
    for p in pastaenden_ekologi(kontekst, r.fragor):
        d = dict(p)
        fid = str(d["fraga_id"])
        if fid in skrivbord_forslag:
            d = dict(skrivbord_forslag[fid])
        if not efter_par:
            if fid == "EK-08":
                bel = [
                    mv("eca_m2"),
                    mv("pc"),
                    mv("antal_flackar"),
                    mv("eca_min_m2"),
                    mv("eca_max_m2"),
                ]
                d["text"] = (
                    f"I läget idag är ECA, den ekvivalenta sammanhängande habitatarean för potentiell livsmiljö, "
                    f"okänd kvalitet, {_m(bel[0])}. PC är {_m(bel[1], 'avrundning=0,00001|enhet=nej')} och antalet "
                    f"habitatfläckar är {_m(bel[2], 'enhet=nej')}. Mellan källans tröskelscenarier ligger ECA mellan "
                    f"{_m(bel[3])} och {_m(bel[4])}. Rapporten redovisar enbart läget idag utan ritförslag."
                )
                d["belagg"] = bel
            elif fid == "EK-10":
                bel = [mv("cellstorlek_m"), mv("antal_flackar")]
                d["text"] = (
                    "Kartan visar strömtäthet (flaskhalsar) i läget idag. Strömtätheten är normaliserad och visar var "
                    f"rörelsen mellan habitatfläckarna är koncentrerad i modellen. Rastrets cellstorlek är "
                    f"{_m(bel[0], 'avrundning=1')} och antalet habitatfläckar är {_m(bel[1], 'enhet=nej')}. "
                    "Fläckarnas betydelse för sambandet (dPC) finns i körningens resultatfil men redovisas inte som tal här."
                )
                d["belagg"] = bel
            elif fid == "EK-11":
                bel = [nv_adr("troskel_m"), nv_adr("troskel_hog_m"), mv("pc_min"), mv("pc_max")]
                d["text"] = (
                    f"Det källbaserade intervallet följer källans två spridningströsklar, {_m(bel[0])} och {_m(bel[1])}. "
                    f"I läget idag ligger PC mellan {_m(bel[2], 'avrundning=0,00001|enhet=nej')} och "
                    f"{_m(bel[3], 'avrundning=0,00001|enhet=nej')}. Intervallet visar inte osäkerheten i habitatval, "
                    "motstånd per markslag eller signalartens verkliga spridning."
                )
                d["belagg"] = bel
            elif fid == "EK-12":
                d = {
                    "fraga_id": "EK-12",
                    "status": "ej_tillampligt",
                    "fordjupning": "nej",
                    "text": "Rapporten redovisar enbart läget idag utan ritförslag; inga alternativ jämförs.",
                    "motivering": "Inget ritförslag är prövat i körningen (enbart nuläge).",
                }
        if fid == "EK-14" and not demo:
            d["saknat_underlag"] = [
                "Markunderlagets källa, version och licensvillkor för projektet samt eventuella lokala uppdateringar."
            ]
        ut.append(_anpassa_pastaende(d, har_avgransning=har_avgransning))
    return ut


def luft_pastaenden(
    rt: RapportTjanst,
    r: Rapport,
    *,
    har_avgransning: bool = False,
) -> list[Mapping[str, Any]]:
    """Påståenden för luft (LU-01–LU-15) ur `referensflode_luft` (lösaren `luft.mkn`)."""
    alla = pastaenden_luft(kontekst_luft_fran_rapport(rt, r), r.fragor)
    return [_anpassa_pastaende(p, har_avgransning=har_avgransning) for p in alla]


def pastaenden_for(rt: RapportTjanst, r: Rapport) -> list[Mapping[str, Any]]:
    """Påståendena för rapportens ämne och frågor. Kastar `ValueError` om en fråga saknar påstående."""
    demo = r.projekt == "demokvarteret"
    avgransad = har_avgransningspolygon(rt, r)
    if r.amne == "skyfall":
        analys = next(
            (a["analys"] for a in r.korningar[0]["analyser"] if str(a["losare"]).startswith("vatten.")),
            None,
        ) if r.korningar else None
        regnkalla, regntyp = _regnkalla_och_typ(r, rt=rt, analys=analys)
        alla = skyfall_pastaenden(
            r,
            demo=demo,
            regn_ur_idf=_regn_ur_idf(rt, r),
            har_avgransning=avgransad,
            regnkalla=regnkalla,
            regntyp=regntyp,
        )
    elif r.amne == "dagsljus":
        alla = dagsljus_pastaenden(rt, r, har_avgransning=avgransad)
    elif r.amne == "ekologi":
        alla = ekologi_pastaenden(rt, r, demo=demo, har_avgransning=avgransad)
    elif r.amne == "luft":
        alla = luft_pastaenden(rt, r, har_avgransning=avgransad)
    else:
        raise ValueError(saknad_mall(r.amne) or "Ämnet saknar påståenden.")
    valda = [p for p in alla if p["fraga_id"] in set(r.fragor)]
    saknas: Sequence[str] = [f for f in r.fragor if f not in {p["fraga_id"] for p in valda}]
    if saknas:
        raise ValueError(f"Webbflödet har inga påståenden för frågorna {', '.join(saknas)}.")
    return valda
