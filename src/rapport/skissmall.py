# ruff: noqa: E501
"""Mallen för skissunderlaget (nivå 1): planerarvänlig besluts-PM med kompakt teknisk bilaga sist.

Samma block skrivs till Word och PDF (`rapport.skissexport`), så att båda formaten har exakt samma
innehåll. Dispositionen följer `docs/utredningar-stockholm/rapportdisposition.md` i planerarens
läsordning:

1. Sammanfattning & nyckeltal (KPI-kort, faktaruta, styrande frågor)
2. Uppdrag, plats & beräkningsfall (syfte, regn och antaganden)
3. Kartor & resultat (före/efter sida-vid-sida, detaljkarta, skillnadskarta, tvärsektion,
   skyfallsmått/lågpunkter P1–P3 samt slutsatser per planeringsfråga)
4. Åtgärder, villkor & vad som saknas inför nästa skede
5. Metod, begränsningar & samlad frågekatalogsöversikt
6. Teknisk spårbarhetsbilaga (sist i samma PDF, eller separat med `separat_bilaga=True`):
   identifieringsdata, belägg (B1..Bn), filtrerad körlogg med hashkedja, K0-manifest och 9/9-kontroll.

All text här är antingen fast (mallens, i rapportens språkprofil), härledd deterministiskt ur
körningarna (antaganden, skyfallsmått, bildtexter, manifest) eller påståendenas text med
platshållarna ifyllda av Floden.
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
    Belagg,
    Pastaende,
    formatera_tal,
    ordna_fordjupning,
    ordna_fragestatus,
    standardsteg,
)
from rapport.sprak import (
    FORDJUPNING_TEXT_SBK,
    LJUS_TEXT_SBK,
    STATUS_TEXT_SBK,
    datum_text,
    scenarionamn,
)

if TYPE_CHECKING:
    from rapport.skissrapport import Karta, PlanerarFigurer, Rapport, RapportTjanst


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
class FigurPar:
    """Två kartor sida-vid-sida (Före | Efter, 81 mm + 4 mm + 81 mm = 166 mm)."""

    vanster: Figur
    hoger: Figur
    gemensam_bildtext: str = ""


@dataclass(frozen=True)
class Nyckeltal:
    """Ett KPI-kort med stort verifierat värde, kort etikett och undertitel."""

    etikett: str
    varde: str
    undertitel: str = ""
    accent: str = ""


@dataclass(frozen=True)
class Nyckeltalsrad:
    """3–4 KPI-kort sida vid sida överst på sida 1 (samtliga ur verifierade belägg/mått)."""

    kort: tuple[Nyckeltal, ...]


@dataclass(frozen=True)
class Faktaruta:
    """Diskret tonad sammanfattnings- eller rekommendationsruta (#F7F5EE + 3 pt #2F4A2C)."""

    rubrik: str
    stycken: tuple[str, ...] = ()
    punkter: tuple[str, ...] = ()


@dataclass(frozen=True)
class Sidbrytning:
    pass


Block = (
    Rubrik
    | Stycke
    | Punktlista
    | Tabell
    | Figur
    | FigurPar
    | Nyckeltalsrad
    | Faktaruta
    | Sidbrytning
)


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


def _fmt_yta(varde_m2: float, cell_m: float = 1.0) -> str:
    steg = max(10.0 if varde_m2 >= 1000.0 else 1.0, cell_m * cell_m)
    return formatera_tal(varde_m2, "m²", steg if steg in (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0) else standardsteg("m²", varde_m2, cell_m * cell_m))


def _fmt_belagg(b: Belagg, *, steg: float | None = None, tecken: bool = False) -> str:
    s = steg if steg is not None else standardsteg(b.enhet, b.varde, b.upplosning)
    return formatera_tal(b.varde, b.enhet, s, tecken=tecken)


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
        elif isinstance(b, FigurPar):
            delar.extend([b.vanster.bildtext, b.hoger.bildtext])
            if b.gemensam_bildtext:
                delar.append(b.gemensam_bildtext)
        elif isinstance(b, Nyckeltalsrad):
            for k in b.kort:
                delar.extend(x for x in (k.etikett, k.varde, k.undertitel) if x)
        elif isinstance(b, Faktaruta):
            delar.append(b.rubrik)
            delar.extend(b.stycken)
            delar.extend(b.punkter)
    return "\n".join(delar)


def huvudtext(dok: Dokument) -> str:
    """Löptexten före bilagorna (för språktestet): rubriker, stycken, punkter, rutor och bildtexter."""
    delar: list[str] = []
    for b in dok.block:
        if isinstance(b, Rubrik) and b.text.startswith(("Bilaga", "Teknisk spårbarhetsbilaga")):
            break
        if isinstance(b, Rubrik | Stycke):
            delar.append(b.text)
        elif isinstance(b, Punktlista):
            delar.extend(b.punkter)
        elif isinstance(b, Figur):
            delar.append(b.bildtext)
        elif isinstance(b, FigurPar):
            delar.extend([b.vanster.bildtext, b.hoger.bildtext])
            if b.gemensam_bildtext:
                delar.append(b.gemensam_bildtext)
        elif isinstance(b, Nyckeltalsrad):
            for k in b.kort:
                delar.extend(x for x in (k.etikett, k.varde, k.undertitel) if x)
        elif isinstance(b, Faktaruta):
            delar.append(b.rubrik)
            delar.extend(b.stycken)
            delar.extend(b.punkter)
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


def _visningsprojekt(r: Rapport, tj: RapportTjanst) -> str:
    from rapport.skissrapport import ar_ra_uuid

    if hasattr(tj, "projektnamn"):
        namn = str(tj.projektnamn(r.projekt)).strip()
        if namn and not ar_ra_uuid(namn):
            return namn
    if r.projekt == "demokvarteret":
        return "Demokvarteret"
    return "Planprojekt" if ar_ra_uuid(r.projekt) else r.projekt


def _sanera_titel(titel: str, projekt: str, visningsprojekt: str) -> str:
    """Ersätter eventuell rå UUID i lagrad titel med läsbart projektnamn."""
    from rapport.skissrapport import ar_ra_uuid

    if ar_ra_uuid(projekt) and projekt in titel:
        return titel.replace(projekt, visningsprojekt)
    return titel


def _hitta_belagg(r: Rapport, *nyckelord: str) -> Belagg | None:
    """Hittar första belägget i rapportens påståenden vars adress matchar samtliga delsträngar."""
    for p in r.pastaenden:
        for b in p.belagg:
            if all(k in b.adress for k in nyckelord):
                return b
    return None


def _bygg_nyckeltalsrad(
    r: Rapport,
    pf: PlanerarFigurer,
    antal: Mapping[str, int],
) -> Nyckeltalsrad | None:
    """Bygger 3–4 KPI-kort uteslutande ur verifierade belägg och deterministiska skyfallsmått."""
    n_tot = len(r.fragor)
    n_fordj = antal.get("ja", 0)
    n_oklar = antal.get("kan_inte_avgoras", 0)
    utr_sub_delar = []
    if n_fordj:
        utr_sub_delar.append(f"{n_fordj} utreds vidare")
    if n_oklar:
        utr_sub_delar.append(f"{n_oklar} kräver underlag")
    if not utr_sub_delar:
        utr_sub_delar.append("ingen fördjupning i skedet")
    kort_utr = Nyckeltal(
        etikett="Frågor att utreda",
        varde=f"{n_fordj + n_oklar} av {n_tot}",
        undertitel=" · ".join(utr_sub_delar),
        accent="B86A4B" if (n_fordj + n_oklar) > 0 else "2F4A2C",
    )
    kort: list[Nyckeltal] = []
    har_jfr = any(bool(k.get("jamforelser")) for k in r.korningar)

    if r.amne == "skyfall":
        b_djup_efter = _hitta_belagg(r, "matvarde.", ".max_djup_m")
        djup_fore_b = None
        djup_efter_b = None
        for p in r.pastaenden:
            for b in p.belagg:
                if b.adress.endswith(".max_djup_m"):
                    if har_jfr and any(j["efter"] in b.scenarier for k in r.korningar for j in k["jamforelser"]):
                        djup_efter_b = djup_efter_b or b
                    elif any(j["fore"] in b.scenarier for k in r.korningar for j in k["jamforelser"]):
                        djup_fore_b = djup_fore_b or b
                    else:
                        djup_fore_b = djup_fore_b or b
        if djup_efter_b is not None:
            sub = f"före förslaget: {_fmt_belagg(djup_fore_b)}" if djup_fore_b is not None else "högsta djup på mark"
            kort.append(Nyckeltal("Största djup efter", _fmt_belagg(djup_efter_b), sub, "234B66"))
        elif djup_fore_b is not None:
            kort.append(Nyckeltal("Största djup idag", _fmt_belagg(djup_fore_b), "befintligt läge", "234B66"))
        elif b_djup_efter is not None:
            kort.append(Nyckeltal("Största vattendjup", _fmt_belagg(b_djup_efter), "över markytan", "234B66"))

        tr = pf.trosklar_efter or pf.trosklar_fore
        if tr is not None:
            sub20 = f"≥ 30 cm (skaderisk): {_fmt_yta(tr.yta_over_0_3m_m2, tr.cellstorlek_m)}"
            kort.append(
                Nyckeltal(
                    "Markyta ≥ 20 cm",
                    _fmt_yta(tr.yta_over_0_2m_m2, tr.cellstorlek_m),
                    sub20,
                    "4A7D8C",
                )
            )
        b_yta_ok = _hitta_belagg(r, "jamforelse.", ".yta_okning_m2")
        b_max_ok = _hitta_belagg(r, "jamforelse.", ".max_okning")
        if b_yta_ok is not None:
            sub_ok = (
                f"största ökning: {_fmt_belagg(b_max_ok, tecken=True)}"
                if b_max_ok is not None
                else "ökning över tröskelvärde"
            )
            kort.append(Nyckeltal("Mark med ökat djup", _fmt_belagg(b_yta_ok), sub_ok, "8C5A00"))
        elif tr is not None:
            kort.append(
                Nyckeltal(
                    "Markyta ≥ 10 cm",
                    _fmt_yta(tr.yta_over_0_1m_m2, tr.cellstorlek_m),
                    "märkbart vatten (tak undantagna)",
                    "4A7D8C",
                )
            )
        elif (b_lagr := _hitta_belagg(r, ".lagrat_m3")) is not None:
            kort.append(Nyckeltal("Stående vattenvolym", _fmt_belagg(b_lagr), "i lågpunkter", "4A7D8C"))

    elif r.amne == "dagsljus":
        b_sol = _hitta_belagg(r, "omrade.hela_omradet.", ".soltimmar.medel")
        if b_sol is not None:
            kort.append(Nyckeltal("Soltimmar (medel)", _fmt_belagg(b_sol), "direkt sol på marken", "8C5A00"))
        b_forl = _hitta_belagg(r, "jamforelse.", "yta_minskning_over_1h_m2") or _hitta_belagg(
            r, "jamforelse.", "yta_minskning_m2"
        )
        if b_forl is not None:
            kort.append(Nyckeltal("Mark med minskad sol", _fmt_belagg(b_forl), "efter jämfört med före", "4A7D8C"))
        b_vsc = _hitta_belagg(r, "fasadjamforelse.", ".antal_under_grans_efter") or _hitta_belagg(
            r, "fasad.", ".antal_under_grans"
        )
        if b_vsc is not None:
            kort.append(Nyckeltal("Fasadpunkter under 27 %", _fmt_belagg(b_vsc), "vertikal himmelsandel (VSC)", "2F4A2C"))

    elif r.amne == "ekologi":
        b_eca = _hitta_belagg(r, ".eca_m2")
        if b_eca is not None:
            kort.append(Nyckeltal("Habitatyta (ECA)", _fmt_belagg(b_eca), "potentiell livsmiljö", "2F4A2C"))
        b_deca = _hitta_belagg(r, "ekojamforelse.", ".delta_eca_procent")
        if b_deca is not None:
            kort.append(Nyckeltal("Förändring av ECA", _fmt_belagg(b_deca, tecken=True), "efter mot före", "4A7D8C"))
        b_flack = _hitta_belagg(r, ".antal_flackar")
        if b_flack is not None:
            kort.append(Nyckeltal("Habitatfläckar", _fmt_belagg(b_flack), "i beräkningsområdet", "2F4A2C"))

    elif r.amne == "luft":
        b_f_lo = _hitta_belagg(r, "mkn_fore", ".lagst_antal_dygn")
        b_f_hi = _hitta_belagg(r, "mkn_fore", ".hogst_antal_dygn")
        b_gr = _hitta_belagg(r, ".max_dygn_ar_antal_dygn")
        gr_sub = f"dygnsgräns: {_fmt_belagg(b_gr)}/år" if b_gr is not None else "känslighetsintervall"
        if b_f_lo is not None and b_f_hi is not None:
            v_lo = formatera_tal(b_f_lo.varde, None, 1.0, visa_enhet=False)
            v_hi = formatera_tal(b_f_hi.varde, "dygn", 1.0)
            kort.append(Nyckeltal("PM10 över gräns (före)", f"{v_lo}–{v_hi}", gr_sub, "2F4A2C"))
        b_e_lo = _hitta_belagg(r, "mkn_efter", ".lagst_antal_dygn")
        b_e_hi = _hitta_belagg(r, "mkn_efter", ".hogst_antal_dygn")
        if b_e_lo is not None and b_e_hi is not None:
            v_lo = formatera_tal(b_e_lo.varde, None, 1.0, visa_enhet=False)
            v_hi = formatera_tal(b_e_hi.varde, "dygn", 1.0)
            korsar = b_gr is not None and b_e_lo.varde <= b_gr.varde < b_e_hi.varde
            kort.append(
                Nyckeltal(
                    "PM10 över gräns (efter)",
                    f"{v_lo}–{v_hi}",
                    "korsar dygnsgränsen" if korsar else gr_sub,
                    "B86A4B" if korsar else "4A7D8C",
                )
            )
        b_am_lo = _hitta_belagg(r, ".arsmedel_lagst_ug_m3")
        b_am_hi = _hitta_belagg(r, ".arsmedel_hogst_ug_m3")
        if b_am_lo is not None and b_am_hi is not None:
            v_lo = formatera_tal(b_am_lo.varde, None, 0.1, visa_enhet=False)
            v_hi = formatera_tal(b_am_hi.varde, "µg/m³", 0.1)
            kort.append(Nyckeltal("Årsmedel PM10", f"{v_lo}–{v_hi}", "känslighetsintervall", "4A7D8C"))

    kort.append(kort_utr)
    if len(kort) < 2:
        return None
    return Nyckeltalsrad(tuple(kort[:4]))


def _bygg_faktaruta(
    r: Rapport,
    pf: PlanerarFigurer,
    antal: Mapping[str, int],
) -> Faktaruta:
    """Kort klarspråklig beslutsruta: resultat, styrande platser, förändring och nästa skede."""
    punkter: list[str] = []
    har_jfr = any(bool(k.get("jamforelser")) for k in r.korningar)

    # 1. Vad visar beräkningen?
    if r.amne == "skyfall":
        tr = pf.trosklar_efter or pf.trosklar_fore
        if tr is not None:
            punkter.append(
                "Vad beräkningen visar: "
                f"Största vattendjup på mark är {formatera_tal(tr.max_djup_m, 'm', 0.01)}. "
                f"Markytan med minst 20 cm vattendjup (framkomlighetsgräns) uppgår till "
                f"{_fmt_yta(tr.yta_over_0_2m_m2, tr.cellstorlek_m)} och markytan med minst 30 cm "
                f"(skaderisk för byggnader) till {_fmt_yta(tr.yta_over_0_3m_m2, tr.cellstorlek_m)}."
            )
        else:
            b_d = _hitta_belagg(r, ".max_djup_m")
            punkter.append(
                f"Vad beräkningen visar: Största beräknade vattendjup är {_fmt_belagg(b_d)}."
                if b_d is not None
                else "Vad beräkningen visar: Tidig skyfallssållning över beräkningsområdet."
            )
    elif r.amne == "dagsljus":
        b_sol = _hitta_belagg(r, "omrade.hela_omradet.", ".soltimmar.medel")
        punkter.append(
            f"Vad beräkningen visar: Direkt sol på marken ligger i medel kring {_fmt_belagg(b_sol)} per dygn "
            "och kompletteras av himmelsandel (VSC) vid genererade fasadpunkter."
            if b_sol is not None
            else "Vad beräkningen visar: Tidig sållning av direkt sol på mark och vertikal himmelsandel vid fasad."
        )
    elif r.amne == "ekologi":
        b_eca = _hitta_belagg(r, ".eca_m2")
        punkter.append(
            f"Vad beräkningen visar: Modellerat spridningssamband för potentiell livsmiljö med okänd kvalitet "
            f"(sammanhängande habitatarea {_fmt_belagg(b_eca)})."
            if b_eca is not None
            else "Vad beräkningen visar: Tidig sållning av spridningssamband för potentiell livsmiljö med okänd kvalitet."
        )
    else:
        punkter.append(
            "Vad beräkningen visar: Känslighetsintervall för PM10 mot dygnsnormen ur givna externa timserier "
            "och angiven bakgrundshalt."
        )

    # 2. Vilka platser styr?
    platsdelar: list[str] = []
    if pf.lagpunkter:
        lp_txt = ", ".join(
            f"{lp.id} ({formatera_tal(lp.djup_m, 'm', 0.01)})" for lp in pf.lagpunkter
        )
        platsdelar.append(f"största lågpunkterna {lp_txt} (markerade i kartorna)")
    if r.mottagare:
        platsdelar.append(", ".join(m.beskrivning for m in r.mottagare))
    if platsdelar:
        punkter.append("Styrande platser: " + "; ".join(platsdelar) + ".")
    else:
        punkter.append("Styrande platser: Hela beräkningsområdet på mark (byggnadernas tak undantagna).")

    # 3. Vad förändrar förslaget?
    if not har_jfr and r.amne != "luft":
        punkter.append(
            "Förslagets påverkan: Underlaget avser enbart befintligt läge utan ritat förslag; "
            "ingen jämförelse mellan före och efter är gjord."
        )
    elif r.amne == "skyfall":
        b_ok = _hitta_belagg(r, "jamforelse.", ".yta_okning_m2")
        b_max = _hitta_belagg(r, "jamforelse.", ".max_okning")
        b_min = _hitta_belagg(r, "jamforelse.", ".yta_minskning_m2")
        if b_ok is not None and b_max is not None:
            min_del = f", medan djupet minskar på {_fmt_belagg(b_min)}" if b_min is not None else ""
            punkter.append(
                f"Vad förslaget förändrar: Vattendjupet ökar över tröskelvärdet på {_fmt_belagg(b_ok)} mark "
                f"(största ökning {_fmt_belagg(b_max, tecken=True)}){min_del}."
            )
    elif r.amne == "dagsljus":
        b_forl = _hitta_belagg(r, "jamforelse.", "yta_minskning_over_1h_m2") or _hitta_belagg(
            r, "jamforelse.", "yta_minskning_m2"
        )
        if b_forl is not None:
            punkter.append(
                f"Vad förslaget förändrar: Beräknad markyta med minskad direkt sol uppgår till {_fmt_belagg(b_forl)}."
            )
    elif r.amne == "ekologi":
        b_dp = _hitta_belagg(r, "ekojamforelse.", ".delta_eca_procent")
        if b_dp is not None:
            punkter.append(
                f"Vad förslaget förändrar: Den ekvivalenta sammanhängande habitatarean ändras med "
                f"{_fmt_belagg(b_dp, tecken=True)} mellan före och efter."
            )
    else:
        punkter.append(
            "Vad förslaget förändrar: Intervallet efter förslaget redovisas mot nuläget; "
            "där intervallet korsar gränsen kan utfallet inte avgöras i detta skede."
        )

    # 4. Vad behöver säkras i nästa skede?
    saknade_topp: list[str] = []
    for p in r.pastaenden:
        for s in p.saknat_underlag:
            kort_s = s.split(".")[0].strip()
            if kort_s and kort_s not in saknade_topp:
                saknade_topp.append(kort_s)
            if len(saknade_topp) >= 2:
                break
        if len(saknade_topp) >= 2:
            break
    n_tot = len(r.fragor)
    sam = sammanfattningsmening(n_tot, antal, amnestext.fragenamn(r))
    nasta = sam
    if saknade_topp:
        nasta += " Prioriterat underlag inför fortsatt prövning: " + "; ".join(saknade_topp) + "."
    punkter.append("Att säkra i nästa skede: " + nasta)

    return Faktaruta(
        rubrik="Samlad bild inför fortsatt planarbete",
        punkter=tuple(punkter),
    )


def _bygger_utseende(r: Rapport, tj: RapportTjanst, med_resurser: bool) -> Utseende:
    inst = r.installningar
    sbk = inst.sbk
    resurser = tj.resursfiler(r) if med_resurser else {}

    def typsnitt(roll: str) -> Typsnitt | None:
        res = getattr(inst, roll)
        fil = resurser.get(roll)
        if res is None or fil is None:
            return None
        return Typsnitt(fil, str(res.familj or "Typsnitt"), res.sha256)

    return Utseende(
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
    )


def _lagg_till_teknisk_bilaga(
    dok: Dokument,
    r: Rapport,
    tj: RapportTjanst,
    *,
    korlogg_rader: Sequence[Mapping[str, Any]],
    bnr: Mapping[tuple[str, str], int],
    sbk: bool,
    separat: bool,
) -> None:
    """Skriver kompakt teknisk spårbarhetsbilaga (identifiering, belägg, filtrerad körlogg, manifest, 9/9-kontroll)."""

    def bilaga(i: int) -> str:
        return f"Bilaga {i}" if sbk else f"Bilaga {'ABCD'[i - 1]}"

    kontroll = r.kontroll or {}
    ansvarig = r.metodnotis.get("ansvarig_utredare")
    if not separat:
        dok.block.append(Sidbrytning())
    if sbk:
        dok.rubrik(1, "Teknisk spårbarhetsbilaga")
        dok.stycke(
            "Denna bilaga redovisar tekniska identifierare, belägg före avrundning, filtrerad "
            "körlogg med obruten hashkedja, K0-manifest och den automatiska kontrollen (9 punkter).",
            "liten",
        )
        korningar_txt = "; ".join(
            f"{k['korning_id']} (kodversion {k.get('kodversion')})" + _underlagssuffix(k, True)
            for k in r.korningar
        )
        kontroll_txt = (
            "Godkänd " + datum_text(str(kontroll.get("tid", "")), True)
            if kontroll.get("godkand")
            else ("Inte godkänd" if kontroll else "Inte gjord")
        )
        dok.block.append(
            Tabell(
                None,
                (
                    ("Rapport-id", r.rapport_id),
                    ("Projekt-id", r.projekt),
                    ("Beräkningskörningar", korningar_txt or "–"),
                    (
                        "Frågekatalog",
                        f"{r.katalog.get('fil')} (källäge {r.katalog.get('granskningsdatum')})",
                    ),
                    ("Dataklass", {"oppet": "Öppna (oppet)"}.get(r.dataklass, r.dataklass)),
                    ("Ansvarig utredare", str(ansvarig) if ansvarig else "Anges av beställaren"),
                    ("Automatisk kontroll", kontroll_txt),
                ),
                (0.26, 0.74),
                liten=True,
            )
        )

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
                    _sv(b.varde) + (f"\u00a0{b.enhet}" if b.enhet and b.enhet != "andel" else ""),
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
                (0.05, 0.21, 0.15, 0.23, 0.16, 0.20),
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
            (0.20, 0.17, 0.20, 0.43),
            liten=True,
        )
    )
    if korlogg_rader:
        # Filtrera bort misslyckade MCP-retrier (utfall == "fel") ur läsbilagan; korlogg.jsonl ändras aldrig.
        godkanda = [x for x in korlogg_rader if str(x.get("utfall", "")).lower() != "fel"]
        utelamnade_fel = len(korlogg_rader) - len(godkanda)
        dok.stycke(
            "MCP-körloggen (godkända anrop som rör rapporten eller dess körningar; inga "
            "argumentvärden, bara hash):",
            "liten",
        )
        if godkanda:
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
                        for x in godkanda
                    ),
                    (0.18, 0.14, 0.20, 0.24, 0.08, 0.16),
                    liten=True,
                )
            )
        ok, n, fel_rad = tj.korlogg.verifiera() if tj.korlogg else (True, len(korlogg_rader), None)
        kedja_txt = (
            f"Körloggens hashkedja: {'obruten' if ok else f'bruten vid rad {fel_rad}'} "
            f"({n} rader kontrollerade över hela korlogg.jsonl)."
        )
        if utelamnade_fel > 0:
            kedja_txt += (
                f" Misslyckade anrop/retrier utelämnade ur läsbilagan: {utelamnade_fel} st "
                "(samtliga ingår oförändrade i den obrutna hashkedjan)."
            )
        dok.stycke(kedja_txt, "liten")
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
                (0.16, 0.20, 0.64),
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
                (0.40, 0.35, 0.25),
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
                (0.55, 0.25, 0.20),
                liten=True,
            )
        )


def _belaggsindex(r: Rapport) -> dict[tuple[str, str], int]:
    per_adress: dict[tuple[str, str], int] = {}
    bnr: dict[tuple[str, str], int] = {}
    for p in r.pastaenden:
        for b in p.belagg:
            nr = per_adress.setdefault((b.korning_id, b.adress), len(per_adress) + 1)
            bnr[(p.pastaende_id, b.adress)] = nr
    return bnr


def bygg_bilagedokument(
    r: Rapport,
    tj: RapportTjanst,
    *,
    korlogg_rader: Sequence[Mapping[str, Any]] = (),
    med_resurser: bool = False,
) -> Dokument:
    """Bygger en fristående teknisk spårbarhetsbilaga (används när `separat_bilaga=True`)."""
    inst = r.installningar
    sbk = inst.sbk
    vis_proj = _visningsprojekt(r, tj)
    ren_titel = _sanera_titel(r.titel, r.projekt, vis_proj)
    datum = datum_text(r.skapad, sbk)
    dok = Dokument(
        titel=f"Teknisk spårbarhetsbilaga – {ren_titel}",
        sidhuvud=" · ".join(x for x in (inst.avsandare, ren_titel, "Teknisk bilaga") if x),
        sidfot="Teknisk spårbarhetsbilaga – Floden (deterministiska fysikberäkningar).",
        titelhuvud=Titelhuvud(
            avsandare=inst.avsandare,
            dokumenttyp="Teknisk bilaga",
            datum=datum,
            diarienummer=inst.diarienummer,
            handlaggare=inst.handlaggare,
        ),
        utseende=_bygger_utseende(r, tj, med_resurser),
    )
    _lagg_till_teknisk_bilaga(
        dok,
        r,
        tj,
        korlogg_rader=korlogg_rader,
        bnr=_belaggsindex(r),
        sbk=sbk,
        separat=True,
    )
    return dok


def bygg_dokument(
    r: Rapport,
    tj: RapportTjanst,
    *,
    korlogg_rader: Sequence[Mapping[str, Any]] = (),
    med_resurser: bool = False,
) -> Dokument:
    """Rapporten som block. Kastar `PastaendeFel` om en platshållare inte kan fyllas.

    `med_resurser` slår upp logotyp och typsnitt (för export); utan dem byggs bara texten."""
    from rapport.skissrapport import PlanerarFigurer, metodnotis_text

    inst: RapportInstallningar = r.installningar
    sbk = inst.sbk
    demo = r.projekt == "demokvarteret"
    status_text = STATUS_TEXT_SBK if sbk else STATUS_TEXT
    fordj_text = FORDJUPNING_TEXT_SBK if sbk else FORDJUPNING_TEXT
    vis_proj = _visningsprojekt(r, tj)
    ren_titel = _sanera_titel(r.titel, r.projekt, vis_proj)

    def rubrik(niva: int, nr: str, text: str) -> None:
        dok.rubrik(niva, f"{nr} {text}" if inst.numrerade_rubriker and nr else text)

    def bilaga(i: int) -> str:
        return f"Bilaga {i}" if sbk else f"Bilaga {'ABCD'[i - 1]}"

    def bilref(i: int) -> str:
        return bilaga(i).lower()

    pf = (
        tj.planerarfigurer(r, skriv_filer=True)
        if hasattr(tj, "planerarfigurer")
        else PlanerarFigurer()
    )

    datum = datum_text(r.skapad, sbk)
    dok = Dokument(
        titel=ren_titel if sbk else r.titel,
        sidhuvud=(
            " · ".join(
                x
                for x in (
                    inst.avsandare,
                    ren_titel,
                    f"Dnr {inst.diarienummer}" if inst.diarienummer else "",
                )
                if x
            )
            if sbk
            else f"{r.titel} · {r.rapport_id}"
        ),
        sidfot=(
            "Skissunderlag – tidig sållning, inte formell kravprövning. Metodstatus: skiss."
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
        utseende=_bygger_utseende(r, tj, med_resurser),
    )

    per_fraga = _pastaenden_per_fraga(r)
    fordj = {f: ordna_fordjupning([p.fordjupning for p in ps]) for f, ps in per_fraga.items()}
    antal = {k: sum(1 for v in fordj.values() if v == k) for k in ("ja", "kan_inte_avgoras", "nej")}
    bnr = _belaggsindex(r)

    if sbk:
        # ------------------------------------------------------------------ SIDA 1 (Planerarens överblick)
        from rapport.korningsval import underlagstext

        underlag_beskr = "; ".join(
            dict.fromkeys(
                t
                for k in r.korningar
                if (t := underlagstext(k.get("underlag"), True))
            )
        )
        if not underlag_beskr:
            underlag_beskr = "Deterministiska fysikberäkningar (detaljer i teknisk bilaga)"
        else:
            underlag_beskr = underlag_beskr[0].upper() + underlag_beskr[1:]

        sida1_rader: tuple[tuple[str, str], ...] = (
            ("Projekt", vis_proj),
            ("Ämne", amnestext.amnesnamn(r)),
            ("Skede", "Planstart och tidig sållning (skissunderlag)"),
            ("Datum", datum),
            *_bestallningsrader(r, True),
            ("Underlag", underlag_beskr),
        )
        dok.block.append(Tabell(None, sida1_rader, (0.22, 0.78), liten=True))

        # 1. Sammanfattning & nyckeltal --------------------------------------------------------
        rubrik(1, "1", "Sammanfattning")
        kpi = _bygg_nyckeltalsrad(r, pf, antal)
        if kpi is not None:
            dok.block.append(kpi)
        dok.block.append(_bygg_faktaruta(r, pf, antal))

        # Kompakt översikt över frågor som styr fortsatt arbete (ja / kan_inte_avgoras)
        styrande = [
            (
                _fragetext(r, fid) or fid,
                status_text[ordna_fragestatus([p.status for p in ps]) if ps else "underlag_saknas"],
                fordj[fid],
                fid,
            )
            for fid, ps in per_fraga.items()
            if fordj[fid] in ("ja", "kan_inte_avgoras")
        ]
        if styrande:
            dok.stycke(
                "Styrande planeringsfrågor som behöver utredas vidare eller kompletteras med underlag "
                "(fullständig frågekatalogsöversikt finns sist i huvudtexten):",
                "liten",
            )
            dok.block.append(
                Tabell(
                    ("Planeringsfråga", "Läge i underlaget", "Fördjupning", "Ref"),
                    tuple(styrande[:8]),
                    (0.46, 0.22, 0.23, 0.09),
                    ljus_kolumn=2,
                    liten=True,
                )
            )
            if len(styrande) > 8:
                dok.stycke(
                    f"Ytterligare {len(styrande) - 8} frågor kräver fördjupning eller kompletterande "
                    "underlag och redovisas i avsnittet Kartor och resultat samt i frågekatalogsöversikten.",
                    "liten",
                )

        # 2. Uppdrag, plats och beräkningsfall -------------------------------------------------
        rubrik(1, "2", "Uppdrag, plats och beräkningsfall")
        dok.stycke(amnestext.ingress(r, True, demo), "ingress")
        for k in r.korningar:
            if r.amne == "luft":
                lagen = [luftlage(a["analys"]) for a in k["analyser"]]
                jfr = (
                    f"Underlaget gäller lägena {' och '.join(lagen)}, var och ett med en given timserie."
                    if len(lagen) > 1
                    else (f"Underlaget gäller läget {lagen[0]} med en given timserie." if lagen else "")
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
        if r.mottagare:
            dok.stycke("Underlaget redovisar särskilt resultat för följande plats:")
            dok.block.append(
                Punktlista(
                    tuple(m.beskrivning[:1].upper() + m.beskrivning[1:] + "." for m in r.mottagare)
                )
            )

        # 3. Kartor och resultat ---------------------------------------------------------------
        rubrik(1, "3", "Kartor och resultat")
        dok.stycke(amnestext.resultatintro(r, True, bilref(1)))

        # Bygg par (Före | Efter sida-vid-sida som FigurPar) + skillnadskarta + detaljkarta + sektion
        anvanda_kartor: set[str] = set()
        fig_nr = 0

        def _figur(k_obj: Karta) -> Figur:
            nonlocal fig_nr
            fig_nr += 1
            txt = bildtext(k_obj, r)
            if inst.numrerade_figurer:
                txt = f"Figur {fig_nr}. {txt}"
            fil = pf.kart_filer.get(k_obj.kart_id, k_obj.fil)
            return Figur(fil, txt, fig_nr)

        for kp in r.korningar:
            for j in kp.get("jamforelser", []):
                bas_k = next(
                    (
                        k
                        for k in r.kartor
                        if k.typ == "bas"
                        and k.korning_id == kp["korning_id"]
                        and k.analys == j["analys"]
                        and k.scenario == j["fore"]
                        and (not k.meta.get("roll") or k.meta.get("roll") == j["utdata"])
                    ),
                    None,
                )
                efter_k = next(
                    (
                        k
                        for k in r.kartor
                        if k.typ == "scenario"
                        and k.korning_id == kp["korning_id"]
                        and k.jamforelse == j["namn"]
                    ),
                    None,
                )
                diff_k = next(
                    (
                        k
                        for k in r.kartor
                        if k.typ == "skillnad"
                        and k.korning_id == kp["korning_id"]
                        and k.jamforelse == j["namn"]
                    ),
                    None,
                )
                if bas_k is not None and efter_k is not None and efter_k.kart_id not in anvanda_kartor:
                    anvanda_kartor.add(bas_k.kart_id)
                    anvanda_kartor.add(efter_k.kart_id)
                    dok.block.append(
                        FigurPar(
                            vanster=_figur(bas_k),
                            hoger=_figur(efter_k),
                            gemensam_bildtext=(
                                f"Jämförelse med gemensam färgskala: {scenarionamn(j['fore'])} "
                                f"(vänster) och {scenarionamn(j['efter'])} (höger)."
                            ),
                        )
                    )
                if diff_k is not None and diff_k.kart_id not in anvanda_kartor:
                    anvanda_kartor.add(diff_k.kart_id)
                    dok.block.append(_figur(diff_k))

        for karta in r.kartor:
            if karta.kart_id not in anvanda_kartor:
                anvanda_kartor.add(karta.kart_id)
                dok.block.append(_figur(karta))

        # Deterministisk skyfallstabell: tröskelytor (10/20/30 cm) och största lågpunkter P1–P3
        # Placeras direkt efter översikts-/skillnadskartorna så uppslagen utnyttjas balanserat.
        if r.amne == "skyfall" and (pf.trosklar_fore is not None or pf.trosklar_efter is not None):
            rubrik(2, "3.1", "Översvämmade markytor och djupaste lågpunkter")
            tr_rader: list[tuple[str, ...]] = []
            tr_f, tr_e = pf.trosklar_fore, pf.trosklar_efter
            if tr_f is not None and tr_e is not None:
                c = tr_e.cellstorlek_m
                tr_rader = [
                    (
                        "Märkbart vatten (≥ 10 cm)",
                        _fmt_yta(tr_f.yta_over_0_1m_m2, c),
                        _fmt_yta(tr_e.yta_over_0_1m_m2, c),
                        formatera_tal(tr_e.yta_over_0_1m_m2 - tr_f.yta_over_0_1m_m2, "m²", max(1.0, c * c), tecken=True),
                    ),
                    (
                        "Framkomlighetsgräns räddningstjänst (≥ 20 cm)",
                        _fmt_yta(tr_f.yta_over_0_2m_m2, c),
                        _fmt_yta(tr_e.yta_over_0_2m_m2, c),
                        formatera_tal(tr_e.yta_over_0_2m_m2 - tr_f.yta_over_0_2m_m2, "m²", max(1.0, c * c), tecken=True),
                    ),
                    (
                        "Risk för byggnadsskada (≥ 30 cm)",
                        _fmt_yta(tr_f.yta_over_0_3m_m2, c),
                        _fmt_yta(tr_e.yta_over_0_3m_m2, c),
                        formatera_tal(tr_e.yta_over_0_3m_m2 - tr_f.yta_over_0_3m_m2, "m²", max(1.0, c * c), tecken=True),
                    ),
                ]
                dok.block.append(
                    Tabell(
                        ("Djupklass (mark, tak undantagna)", "Före", "Efter", "Skillnad"),
                        tuple(tr_rader),
                        (0.49, 0.17, 0.17, 0.17),
                        liten=True,
                    )
                )
            elif (tr_en := tr_e or tr_f) is not None:
                c = tr_en.cellstorlek_m
                dok.block.append(
                    Tabell(
                        ("Djupklass (mark, tak undantagna)", "Beräknad markyta"),
                        (
                            ("Märkbart vatten (≥ 10 cm)", _fmt_yta(tr_en.yta_over_0_1m_m2, c)),
                            ("Framkomlighetsgräns räddningstjänst (≥ 20 cm)", _fmt_yta(tr_en.yta_over_0_2m_m2, c)),
                            ("Risk för byggnadsskada (≥ 30 cm)", _fmt_yta(tr_en.yta_over_0_3m_m2, c)),
                        ),
                        (0.70, 0.30),
                        liten=True,
                    )
                )

            if pf.lagpunkter:
                lp_rader = []
                for lp in pf.lagpunkter:
                    mh = (
                        f"+{formatera_tal(lp.markhojd_rh2000_m, 'm', 0.1)}"
                        if lp.markhojd_rh2000_m is not None
                        else "–"
                    )
                    vn = (
                        f"+{formatera_tal(lp.vattenniva_rh2000_m, 'm', 0.1)}"
                        if lp.vattenniva_rh2000_m is not None
                        else "–"
                    )
                    koord = f"E {round(lp.e_m)}, N {round(lp.n_m)}"
                    lp_rader.append(
                        (lp.id, formatera_tal(lp.djup_m, "m", 0.01), mh, vn, koord)
                    )
                dok.stycke(
                    "Största lokala lågpunkter (minst 15 cm djup och 30 m inbördes avstånd, markerade i kartan):",
                    "liten",
                )
                dok.block.append(
                    Tabell(
                        ("Punkt", "Största djup", "Markhöjd (RH 2000)", "Mark + djup (RH 2000)", "Läge (SWEREF 99 18 00)"),
                        tuple(lp_rader),
                        (0.10, 0.16, 0.21, 0.23, 0.30),
                        liten=True,
                    )
                )

        for extra in (*pf.detaljkartor, *pf.sektioner):
            fig_nr += 1
            txt = f"Figur {fig_nr}. {extra.bildtext}" if inst.numrerade_figurer else extra.bildtext
            dok.block.append(Figur(extra.fil, txt, fig_nr))

        # Resultat per planeringsfråga (rena rubriker utan störande kodprefix-väggar!)
        res_nr = "3.2" if (r.amne == "skyfall" and (pf.trosklar_fore or pf.trosklar_efter)) else "3.1"
        rubrik(2, res_nr, "Resultat per fråga")
        ej_tillampliga_rader: list[tuple[str, str, str, str]] = []
        for fid, ps in per_fraga.items():
            if not ps:
                continue
            if all(p.status == "ej_tillampligt" for p in ps):
                for p in ps:
                    ej_tillampliga_rader.append(
                        (
                            _fragetext(r, fid) or fid,
                            p.fylld_text(),
                            (p.motivering or "–").rstrip(".") + "." if p.motivering else "–",
                            fid,
                        )
                    )
                continue
            rubrik(3, "", _fragetext(r, fid) or fid)
            for p in ps:
                dok.stycke(p.fylld_text())
                delar = [f"Fråga {fid}", status_text[p.status], fordj_text[p.fordjupning]]
                if p.belagg:
                    delar.append(
                        "Belägg "
                        + ", ".join(f"B{bnr[(p.pastaende_id, b.adress)]}" for b in p.belagg)
                    )
                if p.forutsattning:
                    delar.append(f"Förutsättning: {p.forutsattning.rstrip('.')}")
                if p.ansvarig_funktion:
                    delar.append(f"Ansvarar: {p.ansvarig_funktion.rstrip('.')}")
                if p.motivering:
                    delar.append(f"Skäl: {p.motivering.rstrip('.')}")
                dok.stycke(" · ".join(delar) + ".", "notis")

        if ej_tillampliga_rader:
            rubrik(3, "", "Frågor som inte prövas i detta skede")
            dok.block.append(
                Tabell(
                    ("Planeringsfråga", "Besked i underlaget", "Skäl", "Ref"),
                    tuple(ej_tillampliga_rader),
                    (0.30, 0.30, 0.31, 0.09),
                    liten=True,
                )
            )

        _lagg_till_gyf_utci(dok, r, tj, sbk=True, rubrik=rubrik)

        # 4. Åtgärder, villkor och vad som saknas inför nästa skede ----------------------------
        rubrik(1, "4", "Åtgärder, villkor och vad som saknas inför nästa skede")
        saknat = []
        for p in r.pastaenden:
            for s in p.saknat_underlag:
                saknat.append((s, p.ansvarig_funktion or "Beställaren / utredare", p.fraga_id))
            if p.status == "villkorat" and p.forutsattning:
                saknat.append(
                    (f"Villkor att fastställa: {p.forutsattning}", p.ansvarig_funktion or "–", p.fraga_id)
                )
        if saknat:
            dok.stycke(
                "Följande underlag och ställningstaganden behövs inför fortsatt detaljplanering."
            )
            dok.block.append(
                Tabell(
                    ("Vad som saknas eller behöver säkras", "Ansvarar", "Ref"),
                    tuple(saknat),
                    (0.65, 0.25, 0.10),
                )
            )
        else:
            dok.stycke("Inget saknat underlag har angetts i detta skede.")

        # 5. Metod, begränsningar och frågekatalogsöversikt ------------------------------------
        rubrik(1, "5", "Metod, begränsningar och frågekatalogsöversikt")
        dok.stycke(
            metodnotis_text(
                r.metodnotis,
                sbk=True,
                korningar=[k["korning_id"] for k in r.korningar],
                bilaga=bilref(2),
            )
        )
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
                (0.28, 0.10, 0.26, 0.36),
                liten=True,
            )
        )
        _lagg_till_begransningar(dok, r, sbk=True, bilaga1=bilaga(1), bilref1=bilref(1))

        rubrik(2, "5.1", "Samlad frågekatalogsöversikt")
        dok.stycke(
            f"Fullständig förteckning över samtliga {len(r.fragor)} utredningsfrågor i underlaget "
            "och huruvida de behöver utredas vidare:",
            "liten",
        )
        sam_rader = []
        for fid, ps in per_fraga.items():
            status = ordna_fragestatus([p.status for p in ps]) if ps else "underlag_saknas"
            sam_rader.append((fid, _fragetext(r, fid), status_text[status], fordj[fid]))
        dok.block.append(
            Tabell(
                ("Ref", "Planeringsfråga", "Svar i underlaget", "Fördjupning"),
                tuple(sam_rader),
                (0.09, 0.45, 0.23, 0.23),
                ljus_kolumn=3,
                liten=True,
            )
        )

        # 6. Teknisk spårbarhetsbilaga (sist i samma PDF, eller hänvisning vid separat_bilaga=True)
        if inst.separat_bilaga:
            dok.stycke(
                "Teknisk spårbarhetsbilaga (belägg före avrundning, filtrerad körlogg med "
                "hashkedja, K0-manifest och 9-punktskontroll) exporteras som separat fil.",
                "liten",
            )
        else:
            _lagg_till_teknisk_bilaga(
                dok,
                r,
                tj,
                korlogg_rader=korlogg_rader,
                bnr=bnr,
                sbk=True,
                separat=False,
            )
        return dok

    # ------------------------------------------------------------------ NEUTRAL PROFIL (R1-bakåtkompatibel)
    dok.stycke(amnestext.ingress(r, False, demo), "ingress")
    kontroll = r.kontroll or {}
    ansvarig = r.metodnotis.get("ansvarig_utredare")
    korningar = "; ".join(
        f"{k['korning_id']} (kod {k.get('kodversion')})" + _underlagssuffix(k, False)
        for k in r.korningar
    )
    rader: tuple[tuple[str, str], ...] = (
        ("Handling", "Skissunderlag, nivå 1 (sållning)"),
        ("Projekt", vis_proj),
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
            else ("inte godkänd" if kontroll else "inte gjord"),
        ),
    )
    dok.block.append(Tabell(None, rader, (0.26, 0.74), liten=True))

    rubrik(1, "1", "Sammanfattning")
    dok.stycke(
        f"Sållningen omfattar {len(r.fragor)} frågor ur frågekatalogen. "
        f"{antal['ja']} behöver fördjupas, {antal['kan_inte_avgoras']} kan inte avgöras med "
        f"dagens underlag och {antal['nej']} behöver ingen fördjupning i detta skede. "
        "Trafikljuset svarar på frågan om frågan behöver fördjupas: grönt nej, gult ja, rött "
        "kan inte avgöras."
    )
    sam_rader_n = []
    for fid, ps in per_fraga.items():
        status = ordna_fragestatus([p.status for p in ps]) if ps else "underlag_saknas"
        sam_rader_n.append((f"{fid} {_fragetext(r, fid)}", status_text[status], fordj[fid]))
    dok.block.append(
        Tabell(
            ("Fråga", "Status", "Fördjupning"),
            tuple(sam_rader_n),
            (0.56, 0.17, 0.27),
            ljus_kolumn=2,
        )
    )

    rubrik(1, "2", "Uppdrag och beräkningsfall")
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

    rubrik(1, "3", "Resultat")
    dok.stycke(amnestext.resultatintro(r, False, bilref(1)))
    for nr, karta in enumerate(r.kartor, start=1):
        text = bildtext(karta, r)
        if inst.numrerade_figurer:
            text = f"Figur {nr}. {text}"
        dok.block.append(Figur(karta.fil, text, nr))

    rubrik(2, "", "Påståenden per fråga")
    for fid, ps in per_fraga.items():
        if not ps:
            continue
        rubrik(3, "", f"{fid} {_fragetext(r, fid)}")
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

    _lagg_till_gyf_utci(dok, r, tj, sbk=False, rubrik=rubrik)

    rubrik(1, "4", "Saknat underlag och villkor")
    saknat_n = []
    for p in r.pastaenden:
        for s in p.saknat_underlag:
            saknat_n.append((p.fraga_id, s, p.ansvarig_funktion or "–"))
        if p.status == "villkorat" and p.forutsattning:
            saknat_n.append(
                (p.fraga_id, f"Förutsättning: {p.forutsattning}", p.ansvarig_funktion or "–")
            )
    if saknat_n:
        dok.block.append(
            Tabell(
                ("Fråga", "Vad som saknas eller förutsätts", "Ansvarig funktion"),
                tuple(saknat_n),
                (0.12, 0.62, 0.26),
            )
        )
    else:
        dok.stycke("Inget saknat underlag har angetts.")

    rubrik(1, "5", "Metod, osäkerhet och metodnotis")
    dok.stycke(
        metodnotis_text(
            r.metodnotis,
            sbk=False,
            korningar=[k["korning_id"] for k in r.korningar],
            bilaga=bilref(2),
        )
    )
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
            (0.34, 0.10, 0.18, 0.38),
            liten=True,
        )
    )
    dok.stycke(
        "Valideringsstatus 'skiss' betyder att metoden duger för att utforska och jämföra "
        "alternativ men inte är granskad för en formell rapport. Bara en människa kan klassa "
        "en metod som 'rapport'."
    )
    _lagg_till_begransningar(dok, r, sbk=False, bilaga1=bilaga(1), bilref1=bilref(1))

    if not inst.separat_bilaga:
        _lagg_till_teknisk_bilaga(
            dok,
            r,
            tj,
            korlogg_rader=korlogg_rader,
            bnr=bnr,
            sbk=False,
            separat=False,
        )
    return dok


def _lagg_till_gyf_utci(
    dok: Dokument,
    r: Rapport,
    tj: RapportTjanst,
    *,
    sbk: bool,
    rubrik: Any,
) -> None:
    from rapport.gyf_utci_belagg import gyf_tabellrader, utci_tabellrader

    gyf_rader = gyf_tabellrader(r, tj)
    if gyf_rader:
        rubrik(2, "3.3" if sbk else "", "Grönytefaktor (GYF, skiss)")
        dok.stycke(
            "Sammanställning av deterministiskt beräknad grönytefaktor (GYF) ur körningens "
            "resultatfiler. Metodstatus: skiss.",
            "liten",
        )
        dok.block.append(
            Tabell(
                (
                    "Alternativ" if sbk else "Scenario",
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
        utci_nr = "3.4" if (sbk and gyf_rader) else ("3.3" if sbk else "")
        rubrik(2, utci_nr, "Termiskt mikroklimat (UTCI, skiss)")
        dok.stycke(
            "Sammanställning av deterministiskt beräknad termisk stress (UTCI och medelstrålningstemperatur) ur "
            "körningens resultatfiler. Metodstatus: skiss.",
            "liten",
        )
        dok.block.append(
            Tabell(
                (
                    "Alternativ" if sbk else "Scenario",
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


def _lagg_till_begransningar(
    dok: Dokument,
    r: Rapport,
    *,
    sbk: bool,
    bilaga1: str,
    bilref1: str,
) -> None:
    if r.amne in ("dagsljus", "ekologi", "luft"):
        inledning, osak = amnestext.osakerhet(r, sbk, bilaga1 if sbk else bilref1)
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
                f"{bilaga1} anger för varje tal vad det mäter och vid vilken tidpunkt.",
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
                f"Varje beläggs tidsdefinition och statistik står i {bilref1}.",
            ]
    dok.block.append(Punktlista(tuple(osak)))


__all__ = [
    "TRAFIKLJUS",
    "Block",
    "Dokument",
    "Faktaruta",
    "Figur",
    "FigurPar",
    "Nyckeltal",
    "Nyckeltalsrad",
    "Punktlista",
    "Rubrik",
    "Sidbrytning",
    "Stycke",
    "Tabell",
    "Titelhuvud",
    "Typsnitt",
    "Utseende",
    "bildtext",
    "bygg_bilagedokument",
    "bygg_dokument",
    "dokumentets_text",
    "huvudtext",
    "kartrubrik",
    "sammanfattningsmening",
]
