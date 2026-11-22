"""Mallen för skissunderlaget (nivå 1, skyfall): rapporten som en lista block (R1).

Samma block skrivs till Word och PDF (`rapport.skissexport`), så att båda formaten har exakt samma
innehåll. Dispositionen följer `docs/utredningar-stockholm/rapportdisposition.md`, förkortad för
ett skissunderlag: titel och handling, sammanfattning med trafikljus per fråga, uppdrag och
beräkningsfall, resultat (kartor före, efter och skillnad; påståenden per fråga), saknat underlag
och villkor, metod och osäkerhet med metodnotis, och bilagor (belägg, körlogg, manifest,
kontroll).

All text här är antingen fast (mallens), skriven av Floden ur körningarna (antaganden, bildtexter,
manifest) eller påståendenas text med platshållarna ifyllda av Floden.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rapport.pastaende import (
    FORDJUPNING_TEXT,
    STATUS_TEXT,
    TRAFIKLJUS,
    Pastaende,
    ordna_fordjupning,
    ordna_fragestatus,
)

if TYPE_CHECKING:
    from rapport.skissrapport import Rapport, RapportTjanst


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


@dataclass
class Dokument:
    titel: str
    sidhuvud: str
    sidfot: str
    block: list[Block] = field(default_factory=list)

    def rubrik(self, niva: int, text: str) -> None:
        self.block.append(Rubrik(niva, text))

    def stycke(self, text: str, stil: str = "normal") -> None:
        self.block.append(Stycke(text, stil))


def _datum(iso: str) -> str:
    return iso[:10]


def _kort(sha: str | None, n: int = 12) -> str:
    return "–" if not sha else sha[:n]


def _sv(v: float) -> str:
    return f"{v:.6g}".replace(".", ",").replace("e-", "e−")


def dokumentets_text(dok: Dokument) -> str:
    """Allt dokumentets text (för kontrollen att påståenden och metodstatus står i det)."""
    delar = [dok.titel, dok.sidhuvud, dok.sidfot]
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


def _fragetext(r: Rapport, fid: str) -> str:
    f = r.katalog.get("fragor", {}).get(fid, {})
    return str(f.get("fraga", ""))


def _pastaenden_per_fraga(r: Rapport) -> dict[str, list[Pastaende]]:
    ut: dict[str, list[Pastaende]] = {f: [] for f in r.fragor}
    for p in r.pastaenden:
        ut.setdefault(p.fraga_id, []).append(p)
    return ut


def bygg_dokument(
    r: Rapport, tj: RapportTjanst, *, korlogg_rader: Sequence[Mapping[str, Any]] = ()
) -> Dokument:
    """Rapporten som block. Kastar `PastaendeFel` om en platshållare inte kan fyllas."""
    from rapport.skissrapport import metodnotis_text

    demo = r.projekt == "demokvarteret"
    dok = Dokument(
        titel=r.titel,
        sidhuvud=f"{r.titel} · {r.rapport_id}",
        sidfot="Skissunderlag (nivå 1): sållning, inte bedömning mot krav. Metoderna är skisser.",
    )
    ingress = (
        "Skissunderlag för tidig sållning: vilka skyfallsfrågor behöver fördjupas, och vad "
        "saknas för att kunna svara? Underlaget bedömer inte förslaget mot krav."
    )
    if demo:
        ingress += " Demokvarteret är påhittat; inga verkliga data eller byggnader."
    dok.stycke(ingress, "ingress")

    kontroll = r.kontroll or {}
    korningar = "; ".join(f"{k['korning_id']} (kod {k.get('kodversion')})" for k in r.korningar)
    ansvarig = r.metodnotis.get("ansvarig_utredare") or "Anges inte (frivilligt i nivå 1)"
    dok.block.append(
        Tabell(
            None,
            (
                ("Handling", "Skissunderlag, nivå 1 (sållning)"),
                ("Projekt", r.projekt),
                ("Ämne", r.amne.capitalize()),
                ("Skede", "Planstart och tidig sållning"),
                ("Datum", _datum(r.skapad)),
                ("Rapport-id", r.rapport_id),
                ("Körningar", korningar),
                (
                    "Frågekatalog",
                    f"{r.katalog.get('fil')} (källäge {r.katalog.get('granskningsdatum')})",
                ),
                ("Dataklass", {"oppet": "öppet"}.get(r.dataklass, r.dataklass)),
                ("Ansvarig utredare", str(ansvarig)),
                (
                    "Kontroll inför export",
                    "godkänd " + _datum(str(kontroll.get("tid", "")))
                    if kontroll.get("godkand")
                    else "inte godkänd"
                    if kontroll
                    else "inte gjord",
                ),
            ),
            (0.28, 0.72),
            liten=True,
        )
    )

    # 1 Sammanfattning ------------------------------------------------------------------------
    per_fraga = _pastaenden_per_fraga(r)
    fordj = {f: ordna_fordjupning([p.fordjupning for p in ps]) for f, ps in per_fraga.items()}
    antal = {k: sum(1 for v in fordj.values() if v == k) for k in ("ja", "kan_inte_avgoras", "nej")}
    dok.rubrik(1, "1 Sammanfattning")
    dok.stycke(
        f"Sållningen omfattar {len(r.fragor)} frågor ur frågekatalogen. "
        f"{antal['ja']} behöver fördjupas, {antal['kan_inte_avgoras']} kan inte avgöras med "
        f"dagens underlag och {antal['nej']} behöver ingen fördjupning i detta skede. "
        "Trafikljuset svarar på frågan om frågan behöver fördjupas: grönt nej, gult ja, rött "
        "kan inte avgöras."
    )
    rader = []
    for fid, ps in per_fraga.items():
        status = ordna_fragestatus([p.status for p in ps]) if ps else "underlag_saknas"
        rader.append(
            (
                f"{fid} {_fragetext(r, fid)}",
                STATUS_TEXT[status],
                fordj[fid],
            )
        )
    dok.block.append(
        Tabell(("Fråga", "Status", "Fördjupning"), tuple(rader), (0.56, 0.17, 0.27), ljus_kolumn=2)
    )

    # 2 Uppdrag och beräkningsfall ------------------------------------------------------------
    dok.rubrik(1, "2 Uppdrag och beräkningsfall")
    for k in r.korningar:
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
        dok.stycke("Mottagare (platser som påverkas) som påståendena kan gälla:")
        dok.block.append(
            Punktlista(
                tuple(
                    f"{m.namn}: {m.beskrivning} (polygon med {len(m.polygon)} hörn, EPSG:3011)"
                    for m in r.mottagare
                )
            )
        )

    # 3 Resultat -------------------------------------------------------------------------------
    dok.rubrik(1, "3 Resultat")
    dok.stycke(
        "Kartorna visar största vattendjup över mark före och efter förslaget, med samma "
        "färgskala, och skillnaden efter minus före. Talen i texten är ifyllda av Floden ur "
        "körningen; varje tal har ett belägg i bilaga A."
    )
    for nr, karta in enumerate(r.kartor, start=1):
        dok.block.append(Figur(karta.fil, f"Figur {nr}. {karta.bildtext}", nr))

    bnr: dict[tuple[str, str], int] = {}
    for p in r.pastaenden:
        for b in p.belagg:
            bnr.setdefault((p.pastaende_id, b.adress), len(bnr) + 1)

    dok.rubrik(2, "Påståenden per fråga")
    for fid, ps in per_fraga.items():
        if not ps:
            continue
        dok.rubrik(3, f"{fid} {_fragetext(r, fid)}")
        for p in ps:
            dok.stycke(p.fylld_text())
            delar = [
                f"Status: {STATUS_TEXT[p.status]}",
                f"Fördjupning: {FORDJUPNING_TEXT[p.fordjupning]}",
            ]
            if p.belagg:
                delar.append(
                    "Belägg: " + ", ".join(f"B{bnr[(p.pastaende_id, b.adress)]}" for b in p.belagg)
                )
            if p.forutsattning:
                delar.append(f"Förutsättning: {p.forutsattning}")
            if p.ansvarig_funktion:
                delar.append(f"Ansvarig: {p.ansvarig_funktion}")
            if p.motivering:
                delar.append(f"Motivering: {p.motivering}")
            dok.stycke(" · ".join(delar), "notis")

    # 4 Saknat underlag och villkor ----------------------------------------------------------
    dok.rubrik(1, "4 Saknat underlag och villkor")
    saknat = []
    for p in r.pastaenden:
        for s in p.saknat_underlag:
            saknat.append((p.fraga_id, s, p.ansvarig_funktion or "–"))
        if p.status == "villkorat" and p.forutsattning:
            saknat.append(
                (p.fraga_id, f"Förutsättning: {p.forutsattning}", p.ansvarig_funktion or "–")
            )
    if saknat:
        dok.block.append(
            Tabell(
                ("Fråga", "Vad som saknas eller förutsätts", "Ansvarig funktion"),
                tuple(saknat),
                (0.12, 0.62, 0.26),
            )
        )
    else:
        dok.stycke("Inget saknat underlag har angetts.")

    # 5 Metod och osäkerhet -------------------------------------------------------------------
    dok.rubrik(1, "5 Metod, osäkerhet och metodnotis")
    dok.stycke(metodnotis_text(r.metodnotis))
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
        "alternativ men inte är granskad för en formell rapport. Bara en människa kan klassa en "
        "metod som 'rapport'."
    )
    osak = [
        "Statisk fyllning räknar inget förlopp i tid, ingen infiltration och ingen hastighet; "
        "kartorna visar läget vid regnets slut."
        if any(a["losare"] == "vatten.statisk_fyllning" for k in r.korningar for a in k["analyser"])
        else "Dynamisk beräkning med förenklad nätkoppling.",
        "Vattendjupet är djup över markytan, inte vattennivå i RH 2000.",
        "Byggnadernas tak räknas inte som mark i jämförelserna (samma takmask som webben).",
        "Varje beläggs tidsdefinition och statistik står i bilaga A.",
    ]
    dok.block.append(Punktlista(tuple(osak)))

    # Bilagor ---------------------------------------------------------------------------------
    dok.block.append(Sidbrytning())
    dok.rubrik(1, "Bilaga A Belägg")
    dok.stycke(
        "Varje tal i påståendena kommer ur ett belägg. Värdet står här före avrundning, med "
        "körning, fil och kontrollsumma (sha256, de första tolv tecknen).",
        "liten",
    )
    brader = []
    for p in r.pastaenden:
        for b in p.belagg:
            brader.append(
                (
                    f"B{bnr[(p.pastaende_id, b.adress)]}",
                    b.adress,
                    _sv(b.varde) + (f" {b.enhet}" if b.enhet and b.enhet != "andel" else ""),
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
                (0.05, 0.22, 0.1, 0.28, 0.15, 0.2),
                liten=True,
            )
        )

    dok.rubrik(1, "Bilaga B Körlogg")
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

    dok.rubrik(1, "Bilaga C Manifest")
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

    if kontroll:
        dok.rubrik(1, "Bilaga D Kontroll inför export")
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
    "bygg_dokument",
    "dokumentets_text",
]
