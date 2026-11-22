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

AMNESPREFIX: Final[dict[str, str]] = {
    "skyfall": "SK",
    "dagsljus": "DL",
    "sol": "SO",
    "luft": "LU",
    "buller": "BU",
}
"""Ämne -> frågornas id-prefix. Bara skyfall har en skissrapport i R1."""


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

    def kalla_text(self, nyckel: str) -> str:
        k = self.kallor.get(nyckel)
        return str(k.get("titel", nyckel)) if isinstance(k, dict) else nyckel


def formaga_for(fraga_id: str) -> Formaga | None:
    """Flodens förmåga för frågan, eller None om den inte är kartlagd (andra ämnen än skyfall)."""
    return FLODEN_FORMAGA.get(fraga_id)


def formagetabell_markdown() -> str:
    """Tabellen över skyfallsfrågorna som den står i `docs/RAPPORTER.md`."""
    katalog = Fragekatalog.las()
    rader = [
        "| Fråga | Floden i dag | Belägg Floden kan ge | Saknas och varför | Status i demon |",
        "| --- | --- | --- | --- | --- |",
    ]
    niva_text = {"kan": "kan belägga", "delvis": "delvis", "saknas": "underlag saknas"}
    status_text = {
        "visat": "visat",
        "villkorat": "villkorat",
        "underlag_saknas": "underlag saknas",
        "ej_tillampligt": "ej tillämpligt",
        "avvikelse": "avvikelse",
    }
    for fid, fm in FLODEN_FORMAGA.items():
        f = katalog.hamta(fid)
        rader.append(
            f"| {fid} {f.fraga} | {niva_text[fm.niva]} | {fm.belagg or '–'} | {fm.saknat} | "
            f"{status_text[fm.forval_status]} |"
        )
    return "\n".join(rader) + "\n"


__all__ = [
    "AMNESPREFIX",
    "FLODEN_FORMAGA",
    "STANDARD_KATALOG",
    "Formaga",
    "Fraga",
    "Fragekatalog",
    "FragekatalogFel",
    "formaga_for",
    "formagetabell_markdown",
]
