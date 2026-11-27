# ruff: noqa: E501
"""Mallen för skissunderlaget (nivå 1, skyfall): rapporten som en lista block (R1).

Samma block skrivs till Word och PDF (`rapport.skissexport`), så att båda formaten har exakt samma
innehåll. Dispositionen följer `docs/utredningar-stockholm/rapportdisposition.md`, förkortad för
ett skissunderlag: titelhuvud och handling, sammanfattning med trafikljus per fråga, uppdrag och
beräkningsfall, resultat (kartor före, efter och skillnad; påståenden per fråga), saknat underlag
och villkor, metod och osäkerhet med metodnotis, och bilagor (belägg, körlogg, manifest,
kontroll).

All text här är antingen fast (mallens, i rapportens språkprofil, se `rapport.sprak`), skriven av
Floden ur körningarna (antaganden, bildtexter, manifest) eller påståendenas text med
platshållarna ifyllda av Floden. Rapportens inställningar (`rapport.installningar`) styr
språkprofil, numrering av rubriker och figurer, titelhuvud och utseende.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rapport import amnestext
from rapport.bestallning import MOTTAGARTEXT
from rapport.installningar import RapportInstallningar
from rapport.luftbelagg import luftlage
from rapport.pastaende import (
    FORDJUPNING_TEXT,
    STATUS_TEXT,
    TRAFIKLJUS,
    Pastaende,
    ordna_fordjupning,
    ordna_fragestatus,
)
from rapport.sprak import (
    FORDJUPNING_TEXT_SBK,
    LJUS_TEXT_SBK,
    STATUS_TEXT_SBK,
    datum_text,
    scenarionamn,
)

if TYPE_CHECKING:
    from rapport.skissrapport import Karta, Rapport, RapportTjanst


@dataclass(frozen=True)
class Rubrik:
    niva: int
    text: str


@dataclass(frozen=True)
class Stycke:
    text: str
    stil: str = "normal"  # normal | ingress | liten | notis


@dataclass(frozen=True)
class Punktlista:
    punkter: tuple[str, ...]


@dataclass(frozen=True)
class Tabell:
    kolumner: tuple[str, ...] | None
    rader: tuple[tuple[str, ...], ...]
    bredder: tuple[float, ...]
    ljus_kolumn: int | None = None
    liten: bool = False


@dataclass(frozen=True)
class Figur:
    fil: str
    bildtext: str
    nummer: int


@dataclass(frozen=True)
class Sidbrytning:
    pass


Block = Rubrik | Stycke | Punktlista | Tabell | Figur | Sidbrytning


@dataclass(frozen=True)
class Titelhuvud:
    """Avsändare, dokumenttyp, datum, diarienummer och handläggare överst på första sidan."""

    avsandare: str
    dokumenttyp: str
    datum: str
    diarienummer: str = ""
    handlaggare: str = ""

    def rader(self) -> list[tuple[str, str]]:
        ut = [("Datum", self.datum)]
        if self.diarienummer:
            ut.append(("Diarienummer", self.diarienummer))
        if self.handlaggare:
            ut.append(("Handläggare", self.handlaggare))
        return ut


@dataclass(frozen=True)
class Typsnitt:
    """Ett lokalt typsnitt (sökvägen används bara vid skrivningen och skrivs aldrig ut)."""

    fil: Path
    familj: str
    sha256: str


@dataclass(frozen=True)
class Utseende:
    accent: str = "000000"
    sidnummer: str = "Sida {sida} ({antal})"
    ljus: dict[str, str] = field(default_factory=dict)
    """Text vid trafikljuset per fördjupningsvärde (nej, ja, kan_inte_avgoras)."""
    kursiv_bildtext: bool = True
    logotyp: Path | None = None
    typsnitt_brodtext: Typsnitt | None = None
    typsnitt_rubriker: Typsnitt | None = None
    sprak: str = "sv-SE"


@dataclass
class Dokument:
    titel: str
    sidhuvud: str
    sidfot: str
    block: list[Block] = field(default_factory=list)
    titelhuvud: Titelhuvud | None = None
    utseende: Utseende = field(default_factory=Utseende)

    def rubrik(self, niva: int, text: str) -> None:
        self.block.append(Rubrik(niva, text))

    def stycke(self, text: str, stil: str = "normal") -> None:
        self.block.append(Stycke(text, stil))


def sammanfattningsmening(n_tot: int, antal: Mapping[str, int], namn: str) -> str:
    """Sammanfattningens första mening: hoppar över nollor, "alla" när alla frågor hamnar i samma."""
    delar = [
        (verb, antal.get(k, 0), rest)
        for k, verb, rest in (
            ("ja", "behöver", "utredas vidare"),
            ("kan_inte_avgoras", "kan", "inte bedömas med nuvarande underlag"),
            ("nej", "behöver", "ingen fördjupning i detta skede"),
        )
        if antal.get(k, 0)
    ]
    if not delar:
        return ""
    satser = []
    for i, (verb, n, rest) in enumerate(delar):
        tal = "alla" if n == n_tot else str(n)
        satser.append(f"{verb} {tal} {rest}" if i == 0 else f"{tal} {verb} {rest}")
    lista = satser[0] if len(satser) == 1 else ", ".join(satser[:-1]) + " och " + satser[-1]
    return f"Av de {n_tot} {namn} {lista}."


def _kort(sha: str | None, n: int = 12) -> str:
    return "–" if not sha else sha[:n]


def _sv(v: float) -> str:
    """Värde före avrundning (bilagan): sex värdesiffror, decimalkomma, smalt tusentalsmellanrum."""
    text = f"{v:.6g}"
    if "e" in text:
        mant, _, exp = text.partition("e")
        upp = str(int(exp)).translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))
        return mant.replace(".", ",").replace("-", "−") + "\u00a0×\u00a010" + upp
    tecken = "−" if text.startswith("-") else ""
    heltal, _, dec = text.lstrip("-").partition(".")
    if len(heltal) >= 4:
        heltal = " ".join(re.findall(r"\d{1,3}", heltal[::-1]))[::-1]
    return tecken + heltal + ("," + dec if dec else "")


def dokumentets_text(dok: Dokument) -> str:
    """Allt dokumentets text (för kontrollen att påståenden och metodstatus står i det)."""
    delar = [dok.titel, dok.sidhuvud, dok.sidfot]
    if dok.titelhuvud is not None:
        th = dok.titelhuvud
        delar += [th.avsandare, th.dokumenttyp, *(f"{a} {b}" for a, b in th.rader())]
    for b in dok.block:
        if isinstance(b, Rubrik | Stycke):
            delar.append(b.text)
        elif isinstance(b, Punktlista):
            delar.extend(b.punkter)
        elif isinstance(b, Tabell):
            if b.kolumner:
                delar.extend(b.kolumner)
            for rad in b.rader:
                delar.extend(rad)
        elif isinstance(b, Figur):
            delar.append(b.bildtext)
    return "\n".join(delar)


def huvudtext(dok: Dokument) -> str:
    """Löptexten före bilagorna (för språktestet): rubriker, stycken, punkter och bildtexter."""
    delar: list[str] = []
    for b in dok.block:
        if isinstance(b, Rubrik) and b.text.startswith("Bilaga"):
            break
        if isinstance(b, Rubrik | Stycke):
            delar.append(b.text)
        elif isinstance(b, Punktlista):
            delar.extend(b.punkter)
        elif isinstance(b, Figur):
            delar.append(b.bildtext)
    return "\n".join(delar)


def _fragetext(r: Rapport, fid: str) -> str:
    f = r.katalog.get("fragor", {}).get(fid, {})
    return str(f.get("fraga", ""))


def _pastaenden_per_fraga(r: Rapport) -> dict[str, list[Pastaende]]:
    ut: dict[str, list[Pastaende]] = {f: [] for f in r.fragor}
    for p in r.pastaenden:
        ut.setdefault(p.fraga_id, []).append(p)
    return ut


def _losare(r: Rapport, korning_id: str, analys: str) -> str:
    for k in r.korningar:
        if k["korning_id"] == korning_id:
            for a in k["analyser"]:
                if a["analys"] == analys:
                    return str(a["losare"])
    return ""


def bildtext(k: Karta, r: Rapport) -> str:
    """Bildtexten som den står i dokumentet (utan figurnummer), i rapportens språkprofil.

    Profilen "neutral" använder bildtexten som Floden skrev när rapporten skapades (K33)."""
    if not r.installningar.sbk:
        return k.bildtext
    jfr = next(
        (j for kp in r.korningar for j in kp["jamforelser"] if j["namn"] == k.jamforelse), None
    )
    return amnestext.bildtext_sbk(
        r.amne,
        k.typ,
        k.scenario,
        jfr["fore"] if jfr else "fore",
        jfr["efter"] if jfr else "efter",
        k.korning_id,
        _losare(r, k.korning_id, k.analys),
    )


def kartrubrik(typ: str, scenario: str | None, fore: str, efter: str) -> str:
    """Kartans rubrik för skyfall (R1; se `rapport.amnestext.kartrubrik` för alla ämnen)."""
    return amnestext.kartrubrik("skyfall", typ, scenario, fore, efter)


def _underlagssuffix(k: Mapping[str, Any], sbk: bool) -> str:
    """` – gäller förslaget …` eller `– demokörningen …` efter körningen i handlingsrutan (tom när slaget inte är känt)."""
    from rapport.korningsval import underlagstext

    t = underlagstext(k.get("underlag"), sbk)
    return f" – {t}" if t else ""


def _bestallningsrader(r: Rapport, sbk: bool) -> list[tuple[str, str]]:
    """Beställningen (K30) i handlingsrutan: syfte och mottagare."""
    b = r.bestallning
    if b is None:
        return []
    return [
        ("Syfte" if sbk else "Beställningens syfte", b.syfte),
        ("Mottagare" if sbk else "Mottagare (roll)", MOTTAGARTEXT[b.mottagare_roll].capitalize()),
    ]


def bygg_dokument(
    r: Rapport,
    tj: RapportTjanst,
    *,
    korlogg_rader: Sequence[Mapping[str, Any]] = (),
    med_resurser: bool = False,
) -> Dokument:
    """Rapporten som block. Kastar `PastaendeFel` om en platshållare inte kan fyllas.

    `med_resurser` slår upp logotyp och typsnitt (för export); utan dem byggs bara texten."""
    from rapport.skissrapport import metodnotis_text

    inst: RapportInstallningar = r.installningar
    sbk = inst.sbk
    demo = r.projekt == "demokvarteret"
    status_text = STATUS_TEXT_SBK if sbk else STATUS_TEXT
    fordj_text = FORDJUPNING_TEXT_SBK if sbk else FORDJUPNING_TEXT

    def rubrik(niva: int, nr: str, text: str) -> None:
        dok.rubrik(niva, f"{nr} {text}" if inst.numrerade_rubriker and nr else text)

    def bilaga(i: int) -> str:
        return f"Bilaga {i}" if sbk else f"Bilaga {'ABCD'[i - 1]}"

    def bilref(i: int) -> str:
        return bilaga(i).lower()

    resurser = tj.resursfiler(r) if med_resurser else {}

    def typsnitt(roll: str) -> Typsnitt | None:
        res = getattr(inst, roll)
        fil = resurser.get(roll)
        if res is None or fil is None:
            return None
        return Typsnitt(fil, str(res.familj or "Typsnitt"), res.sha256)

    datum = datum_text(r.skapad, sbk)
    dok = Dokument(
        titel=r.titel,
        sidhuvud=(
            " · ".join(
                x
                for x in (
                    inst.avsandare,
                    r.titel,
                    f"Dnr {inst.diarienummer}" if inst.diarienummer else "",
                )
                if x
            )
            if sbk
            else f"{r.titel} · {r.rapport_id}"
        ),
        sidfot=(
            "Skissunderlag – sållning, inte bedömning mot krav. Metoderna är skisser."
            if sbk
            else "Skissunderlag (nivå 1): sållning, inte bedömning mot krav. Metoderna är skisser."
        ),
        titelhuvud=Titelhuvud(
            avsandare=inst.avsandare,
            dokumenttyp="Skissunderlag",
            datum=datum,
            diarienummer=inst.diarienummer,
            handlaggare=inst.handlaggare,
        ),
        utseende=Utseende(
            accent=inst.accentfarg,
            ljus=(
                dict(LJUS_TEXT_SBK)
                if sbk
                else {
                    "nej": "Grön: Ingen fördjupning",
                    "ja": "Gul: Behöver fördjupas",
                    "kan_inte_avgoras": "Röd: Kan inte avgöras",
                }
            ),
            logotyp=resurser.get("logotyp"),
            typsnitt_brodtext=typsnitt("typsnitt_brodtext"),
            typsnitt_rubriker=typsnitt("typsnitt_rubriker"),
        ),
    )
    ingress = amnestext.ingress(r, sbk, demo)
    dok.stycke(ingress, "ingress")

    kontroll = r.kontroll or {}
    ansvarig = r.metodnotis.get("ansvarig_utredare")
    if sbk:
        korningar = "; ".join(
            f"beräkning {k['korning_id']} (Floden, kodversion {k.get('kodversion')})"
            + _underlagssuffix(k, True)
            for k in r.korningar
        )
        kontroll_text = (
            "Godkänd " + datum_text(str(kontroll.get("tid", "")), True)
            if kontroll.get("godkand")
            else "Inte godkänd"
            if kontroll
            else "Inte gjord"
        )
        rader: tuple[tuple[str, str], ...] = (
            ("Dokumenttyp", "Skissunderlag för tidig sållning"),
            ("Projekt", r.projekt),
            ("Ämne", amnestext.amnesnamn(r)),
            ("Skede", "Planstart och tidig sållning"),
            *_bestallningsrader(r, True),
            ("Underlag", korningar[0].upper() + korningar[1:] if korningar else "–"),
            (
                "Frågekatalog",
                "Utredningsfrågor för Stockholm, källäge "
                + datum_text(str(r.katalog.get("granskningsdatum")), True),
            ),
            ("Uppgifternas klass", {"oppet": "Öppna"}.get(r.dataklass, r.dataklass)),
            ("Ansvarig utredare", str(ansvarig) if ansvarig else "Anges av beställaren"),
            ("Automatisk kontroll", kontroll_text),
            ("Rapport-id", r.rapport_id),
        )
    else:
        korningar = "; ".join(
            f"{k['korning_id']} (kod {k.get('kodversion')})" + _underlagssuffix(k, False) for k in r.korningar
        )
        rader = (
            ("Handling", "Skissunderlag, nivå 1 (sållning)"),
            ("Projekt", r.projekt),
            ("Ämne", amnestext.amnesnamn(r)),
            ("Skede", "Planstart och tidig sållning"),
            *_bestallningsrader(r, False),
            ("Datum", datum),
            ("Rapport-id", r.rapport_id),
            ("Körningar", korningar),
            (
                "Frågekatalog",
                f"{r.katalog.get('fil')} (källäge {r.katalog.get('granskningsdatum')})",
            ),
            ("Dataklass", {"oppet": "öppet"}.get(r.dataklass, r.dataklass)),
            ("Ansvarig utredare", str(ansvarig or "Anges inte (frivilligt i nivå 1)")),
            (
                "Kontroll inför export",
                "godkänd " + datum_text(str(kontroll.get("tid", "")), False)
                if kontroll.get("godkand")
                else "inte godkänd"
                if kontroll
                else "inte gjord",
            ),
        )
    dok.block.append(Tabell(None, rader, (0.28, 0.72), liten=True))

    # Sammanfattning ---------------------------------------------------------------------------
    per_fraga = _pastaenden_per_fraga(r)
    fordj = {f: ordna_fordjupning([p.fordjupning for p in ps]) for f, ps in per_fraga.items()}
    antal = {k: sum(1 for v in fordj.values() if v == k) for k in ("ja", "kan_inte_avgoras", "nej")}
    rubrik(1, "1", "Sammanfattning")
    if sbk:
        n_tot = len(r.fragor)
        mening = sammanfattningsmening(n_tot, antal, amnestext.fragenamn(r))
        if mening:
            dok.stycke(mening)
        dok.stycke(
            "Tabellen visar för varje fråga om den behöver utredas vidare. Frågorna kommer ur "
            f"frågekatalogen för {({'skyfall': 'skyfall', 'ekologi': 'ekologi', 'luft': 'luft'}).get(r.amne, 'dagsljus och sol')}."
        )
    else:
        dok.stycke(
            f"Sållningen omfattar {len(r.fragor)} frågor ur frågekatalogen. "
            f"{antal['ja']} behöver fördjupas, {antal['kan_inte_avgoras']} kan inte avgöras med "
            f"dagens underlag och {antal['nej']} behöver ingen fördjupning i detta skede. "
            "Trafikljuset svarar på frågan om frågan behöver fördjupas: grönt nej, gult ja, rött "
            "kan inte avgöras."
        )
    sam_rader = []
    for fid, ps in per_fraga.items():
        status = ordna_fragestatus([p.status for p in ps]) if ps else "underlag_saknas"
        sam_rader.append((f"{fid} {_fragetext(r, fid)}", status_text[status], fordj[fid]))
    dok.block.append(
        Tabell(
            ("Fråga", "Svar i underlaget" if sbk else "Status", "Fördjupning"),
            tuple(sam_rader),
            (0.48, 0.26, 0.26) if sbk else (0.56, 0.17, 0.27),
            ljus_kolumn=2,
        )
    )

    # Uppdrag och beräkningsfall ---------------------------------------------------------------
    rubrik(1, "2", "Uppdrag och beräkningsfall")
    for k in r.korningar:
        if sbk:
            if r.amne == "luft":
                lagen = [luftlage(a["analys"]) for a in k["analyser"]]
                jfr = (
                    f"Underlaget gäller lägena {' och '.join(lagen)}, var och ett med en given timserie."
                    if len(lagen) > 1
                    else f"Underlaget gäller läget {lagen[0]} med en given timserie." if lagen else ""
                )
            elif r.amne in ("dagsljus", "ekologi"):
                jfr = " ".join(
                    f"Läget {amnestext.lage(r.amne, j['efter'])} jämförs med läget "
                    f"{amnestext.lage(r.amne, j['fore'])}."
                    for j in k["jamforelser"]
                )
            else:
                jfr = " ".join(
                    f"Läget {scenarionamn(j['efter'])} förslaget jämförs med läget "
                    f"{scenarionamn(j['fore'])} förslaget, med samma regn."
                    for j in k["jamforelser"]
                )
            dok.stycke(f"{k['beskrivning']} {jfr}".strip())
            if k.get("antaganden"):
                dok.stycke("Beräkningen bygger på följande antaganden:")
                dok.block.append(
                    Punktlista(
                        tuple(a.replace(" (uttrycklig parameter)", "") for a in k["antaganden"])
                    )
                )
        else:
            jfr = "; ".join(
                f"{j['efter']} jämförs med {j['fore']} ({j['analys']}, {j['utdata']})"
                for j in k["jamforelser"]
            )
            dok.stycke(
                f"Körning {k['korning_id']}: {k['beskrivning']} Scenarier: "
                f"{', '.join(k['scenarier'])}. {jfr + '.' if jfr else ''}"
            )
            if k.get("antaganden"):
                dok.stycke("Antaganden som Floden har räknat med:", "liten")
                dok.block.append(Punktlista(tuple(k["antaganden"])))
    if r.mottagare:
        if sbk:
            dok.stycke("Underlaget redovisar också resultat för följande plats:")
            dok.block.append(
                Punktlista(
                    tuple(m.beskrivning[:1].upper() + m.beskrivning[1:] + "." for m in r.mottagare)
                )
            )
        else:
            dok.stycke("Mottagare (platser som påverkas) som påståendena kan gälla:")
            dok.block.append(
                Punktlista(
                    tuple(
                        f"{m.namn}: {m.beskrivning} (polygon med {len(m.polygon)} hörn, EPSG:3011)"
                        for m in r.mottagare
                    )
                )
            )

    # Resultat ---------------------------------------------------------------------------------
    rubrik(1, "3", "Resultat")
    dok.stycke(amnestext.resultatintro(r, sbk, bilref(1)))
    for nr, karta in enumerate(r.kartor, start=1):
        text = bildtext(karta, r)
        if inst.numrerade_figurer:
            text = f"Figur {nr}. {text}"
        dok.block.append(Figur(karta.fil, text, nr))

    # Ett belägg-id per adress och körning: samma tal i flera påståenden ger en rad i bilagan.
    per_adress: dict[tuple[str, str], int] = {}
    bnr: dict[tuple[str, str], int] = {}
    for p in r.pastaenden:
        for b in p.belagg:
            nr = per_adress.setdefault((b.korning_id, b.adress), len(per_adress) + 1)
            bnr[(p.pastaende_id, b.adress)] = nr

    rubrik(2, "3.1" if sbk else "", "Resultat per fråga" if sbk else "Påståenden per fråga")
    for fid, ps in per_fraga.items():
        if not ps:
            continue
        rubrik(3, "", f"{fid} {_fragetext(r, fid)}")
        for p in ps:
            dok.stycke(p.fylld_text())
            if sbk:
                delar = [f"{status_text[p.status]}. {fordj_text[p.fordjupning]}."]
                if p.belagg:
                    delar.append(
                        "Belägg "
                        + ", ".join(f"B{bnr[(p.pastaende_id, b.adress)]}" for b in p.belagg)
                        + "."
                    )
                if p.forutsattning:
                    delar.append(f"Förutsättning: {p.forutsattning.rstrip('.')}.")
                if p.ansvarig_funktion:
                    delar.append(f"Ansvarar: {p.ansvarig_funktion.rstrip('.')}.")
                if p.motivering:
                    delar.append(f"Skäl: {p.motivering.rstrip('.')}.")
                dok.stycke(" ".join(delar), "notis")
            else:
                delar = [
                    f"Status: {STATUS_TEXT[p.status]}",
                    f"Fördjupning: {FORDJUPNING_TEXT[p.fordjupning]}",
                ]
                if p.belagg:
                    delar.append(
                        "Belägg: "
                        + ", ".join(f"B{bnr[(p.pastaende_id, b.adress)]}" for b in p.belagg)
                    )
                if p.forutsattning:
                    delar.append(f"Förutsättning: {p.forutsattning}")
                if p.ansvarig_funktion:
                    delar.append(f"Ansvarig: {p.ansvarig_funktion}")
                if p.motivering:
                    delar.append(f"Motivering: {p.motivering}")
                dok.stycke(" · ".join(delar), "notis")

    from rapport.gyf_utci_belagg import gyf_tabellrader, utci_tabellrader

    gyf_rader = gyf_tabellrader(r, tj)
    if gyf_rader:
        rubrik(2, "3.2" if sbk else "", "Grönytefaktor (GYF, skiss)")
        dok.stycke(
            "Sammanställning av deterministiskt beräknad grönytefaktor (GYF) ur körningens "
            "resultatfiler. Metodstatus: skiss.",
            "liten",
        )
        dok.block.append(
            Tabell(
                (
                    "Scenario",
                    "Profil",
                    "GYF",
                    "Riktmärke",
                    "Ekoeffektiv yta",
                    "Flaggad yta",
                    "Total area",
                    "Status",
                ),
                tuple(gyf_rader),
                (0.14, 0.14, 0.09, 0.11, 0.16, 0.14, 0.14, 0.08),
                liten=True,
            )
        )

    utci_rader = utci_tabellrader(r, tj)
    if utci_rader:
        utci_nr = "3.3" if (sbk and gyf_rader) else ("3.2" if sbk else "")
        rubrik(2, utci_nr, "Termiskt mikroklimat (UTCI, skiss)")
        dok.stycke(
            "Sammanställning av deterministiskt beräknad termisk stress (UTCI och T_mrt) ur "
            "körningens resultatfiler. Metodstatus: skiss.",
            "liten",
        )
        dok.block.append(
            Tabell(
                (
                    "Scenario",
                    "UTCI medel",
                    "UTCI p90",
                    "UTCI max",
                    "T_mrt medel",
                    "Stark värmestress",
                    "Utanför giltighet",
                    "Status",
                ),
                tuple(utci_rader),
                (0.14, 0.12, 0.12, 0.12, 0.13, 0.14, 0.15, 0.08),
                liten=True,
            )
        )

    # Saknat underlag och villkor --------------------------------------------------------------
    rubrik(1, "4", "Saknat underlag och villkor")
    saknat = []
    for p in r.pastaenden:
        for s in p.saknat_underlag:
            saknat.append((p.fraga_id, s, p.ansvarig_funktion or "–"))
        if p.status == "villkorat" and p.forutsattning:
            saknat.append(
                (p.fraga_id, f"Förutsättning: {p.forutsattning}", p.ansvarig_funktion or "–")
            )
    if saknat:
        if sbk:
            dok.stycke(
                "Följande underlag behövs innan frågorna kan besvaras. Frågorna behöver utredas "
                "vidare i planarbetet."
            )
        dok.block.append(
            Tabell(
                (
                    "Fråga",
                    "Vad som saknas eller förutsätts",
                    "Ansvarar" if sbk else "Ansvarig funktion",
                ),
                tuple(saknat),
                (0.12, 0.62, 0.26),
            )
        )
    else:
        dok.stycke("Inget saknat underlag har angetts.")

    # Metod och osäkerhet ----------------------------------------------------------------------
    rubrik(1, "5", "Metod och osäkerhet" if sbk else "Metod, osäkerhet och metodnotis")
    dok.stycke(
        metodnotis_text(
            r.metodnotis,
            sbk=sbk,
            korningar=[k["korning_id"] for k in r.korningar],
            bilaga=bilref(2),
        )
    )
    if sbk:
        metodrader = tuple(
            (
                str(m["visningsnamn"]),
                str(m.get("version") or "–"),
                f"{m['status']} – inte granskad för formell utredning"
                if m["status"] == "skiss"
                else str(m["status"]),
                "; ".join(m.get("underlag") or []) or "–",
            )
            for m in r.metoder
        )
        dok.block.append(
            Tabell(
                ("Metod", "Version", "Status", "Underlag för granskning"),
                metodrader,
                (0.3, 0.1, 0.24, 0.36),
                liten=True,
            )
        )
        dok.stycke(
            "Status skiss betyder att metoden duger för att jämföra alternativ i ett tidigt skede, "
            "men att den inte är granskad för en formell utredning. Bara en människa kan ändra "
            "metodens status."
        )
    else:
        dok.block.append(
            Tabell(
                ("Metod (lösare)", "Version", "Valideringsstatus", "Underlag för granskning"),
                tuple(
                    (
                        f"{m['visningsnamn']} ({m['losare']})",
                        str(m.get("version") or "–"),
                        str(m["status"]),
                        "; ".join(m.get("underlag") or []) or "–",
                    )
                    for m in r.metoder
                ),
                (0.34, 0.1, 0.18, 0.38),
                liten=True,
            )
        )
        dok.stycke(
            "Valideringsstatus 'skiss' betyder att metoden duger för att utforska och jämföra "
            "alternativ men inte är granskad för en formell rapport. Bara en människa kan klassa "
            "en metod som 'rapport'."
        )
    if r.amne in ("dagsljus", "ekologi", "luft"):
        inledning, osak = amnestext.osakerhet(r, sbk, bilaga(1) if sbk else bilref(1))
        if sbk:
            dok.stycke(inledning)
    else:
        statisk = any(
            a["losare"] == "vatten.statisk_fyllning" for k in r.korningar for a in k["analyser"]
        )
        if sbk:
            osak = [
                "Beräkningen är en statisk fyllning. Den visar läget när regnet har slutat, men inte "
                "förloppet, infiltrationen i marken eller vattnets hastighet."
                if statisk
                else "Beräkningen är dynamisk men har en förenklad koppling till ledningsnätet.",
                "Vattendjupet anges över markytan. Det är inte en vattennivå i höjdsystemet RH 2000.",
                "Taken räknas inte som mark när läget före och efter förslaget jämförs.",
                f"{bilaga(1)} anger för varje tal vad det mäter och vid vilken tidpunkt.",
            ]
            dok.stycke("Resultatet ska läsas med följande begränsningar:")
        else:
            osak = [
                "Statisk fyllning räknar inget förlopp i tid, ingen infiltration och ingen hastighet; "
                "kartorna visar läget vid regnets slut."
                if statisk
                else "Dynamisk beräkning med förenklad nätkoppling.",
                "Vattendjupet är djup över markytan, inte vattennivå i RH 2000.",
                "Byggnadernas tak räknas inte som mark i jämförelserna (samma takmask som webben).",
                f"Varje beläggs tidsdefinition och statistik står i {bilref(1)}.",
            ]
    dok.block.append(Punktlista(tuple(osak)))

    # Bilagor ----------------------------------------------------------------------------------
    dok.block.append(Sidbrytning())
    dok.rubrik(1, f"{bilaga(1)} Belägg")
    dok.stycke(
        "Varje tal i texten kommer ur ett belägg. Värdet står här före avrundning, med "
        "beräkning, fil och kontrollsumma (sha256, de första tolv tecknen)."
        if sbk
        else "Varje tal i påståendena kommer ur ett belägg. Värdet står här före avrundning, med "
        "körning, fil och kontrollsumma (sha256, de första tolv tecknen).",
        "liten",
    )
    brader = []
    sedda: set[tuple[str, str]] = set()
    for p in r.pastaenden:
        for b in p.belagg:
            if (b.korning_id, b.adress) in sedda:
                continue
            sedda.add((b.korning_id, b.adress))
            brader.append(
                (
                    f"B{bnr[(p.pastaende_id, b.adress)]}",
                    b.adress,
                    _sv(b.varde) + (f" {b.enhet}" if b.enhet and b.enhet != "andel" else ""),
                    f"{b.storhet}. {b.statistik}. {b.tidsdefinition}".strip(". ") + ".",
                    b.namnare,
                    f"{b.korning_id}/{b.fil} ({_kort(b.fil_sha256)})",
                )
            )
    if brader:
        dok.block.append(
            Tabell(
                ("", "Adress", "Värde", "Mått, statistik, tid", "Nämnare", "Körning, fil"),
                tuple(brader),
                (0.06, 0.19, 0.14, 0.27, 0.14, 0.2),
                liten=True,
            )
        )

    dok.rubrik(1, f"{bilaga(2)} Körlogg")
    dok.stycke("Händelser i rapporten (skapad, påståenden, kontroll, export):", "liten")
    dok.block.append(
        Tabell(
            ("Tid (UTC)", "Händelse", "Av, modell", "Detalj"),
            tuple(
                (
                    str(h.get("tid", "")).replace("T", " ").removesuffix("+00:00"),
                    str(h.get("handelse", "")),
                    ", ".join(x for x in (h.get("av"), h.get("modell")) if x) or "–",
                    str(h.get("detalj", "")),
                )
                for h in r.handelser
            ),
            (0.2, 0.17, 0.2, 0.43),
            liten=True,
        )
    )
    if korlogg_rader:
        dok.stycke(
            "MCP-körloggen (anrop som rör rapporten eller dess körningar; inga argumentvärden, "
            "bara hash):",
            "liten",
        )
        dok.block.append(
            Tabell(
                ("Tid (UTC)", "Klient", "Modell, roll", "Verktyg", "Utfall", "Hash"),
                tuple(
                    (
                        str(x.get("tid", ""))[:19].replace("T", " "),
                        str((x.get("klient") or {}).get("namn", "")),
                        ", ".join(str(v) for v in (x.get("modell"), x.get("roll")) if v) or "–",
                        str(x.get("verktyg", "")),
                        str(x.get("utfall", "")),
                        _kort(str(x.get("hash", "")), 10),
                    )
                    for x in korlogg_rader
                ),
                (0.18, 0.14, 0.2, 0.22, 0.1, 0.16),
                liten=True,
            )
        )
        ok, n, fel_rad = tj.korlogg.verifiera() if tj.korlogg else (True, 0, None)
        dok.stycke(
            f"Körloggens hashkedja: {'obruten' if ok else f'bruten vid rad {fel_rad}'} "
            f"({n} rader kontrollerade).",
            "liten",
        )
    else:
        dok.stycke(
            "Ingen MCP-körlogg hör till rapporten (skapad utan MCP, t.ex. i referensflödet).",
            "liten",
        )

    dok.rubrik(1, f"{bilaga(3)} Manifest")
    for k in r.korningar:
        dok.stycke(
            f"Körning {k['korning_id']} (fråga {k['fraga_id']}): K0-manifest {k['manifest']} "
            f"sha256 {k['manifest_sha256']}, kodversion {k.get('kodversion')}, start "
            f"{k.get('start')}.",
            "liten",
        )
        jobbrader = []
        for a in k["analyser"]:
            for s in a["scenarier"]:
                try:
                    param = tj.kalla.jobbparametrar(k["korning_id"], a["analys"], s)
                except Exception:  # noqa: BLE001 - manifestet visas så långt det går
                    param = {}
                ptext = ", ".join(
                    f"{n}={json.dumps(v, ensure_ascii=False)}"
                    for n, v in sorted(param.items())
                    if not isinstance(v, dict | list)
                )
                jobbrader.append((f"{a['analys']}/{s}", a["losare"], ptext))
        dok.block.append(
            Tabell(
                ("Jobb", "Lösare", "Parametrar ur K0-manifestet"),
                tuple(jobbrader),
                (0.15, 0.2, 0.65),
                liten=True,
            )
        )
    if sbk:
        dok.stycke("Metodernas beteckning och valideringsstatus:", "liten")
        dok.block.append(
            Tabell(
                ("Metod", "Lösare", "Valideringsstatus"),
                tuple(
                    (str(m["visningsnamn"]), str(m["losare"]), str(m["status"])) for m in r.metoder
                ),
                (0.4, 0.35, 0.25),
                liten=True,
            )
        )

    if kontroll:
        dok.rubrik(1, f"{bilaga(4)} Kontroll inför export")
        dok.block.append(
            Tabell(
                ("Kontroll", "Utfall", "Granskat"),
                tuple(
                    (p["rubrik"], "godkänd" if p["godkand"] else "fel", str(p["granskat"]))
                    for p in kontroll.get("punkter", [])
                ),
                (0.55, 0.25, 0.2),
                liten=True,
            )
        )
    return dok


__all__ = [
    "TRAFIKLJUS",
    "Block",
    "Dokument",
    "Figur",
    "Punktlista",
    "Rubrik",
    "Sidbrytning",
    "Stycke",
    "Tabell",
    "Titelhuvud",
    "Typsnitt",
    "Utseende",
    "bildtext",
    "bygg_dokument",
    "dokumentets_text",
    "huvudtext",
    "kartrubrik",
]
