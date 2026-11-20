"""Rapportmallar och deterministisk platshållarifyllnad (T08)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from rapport.underlag import GILTIGA_RAPPORTTYPER, RapportUnderlag, TabellData

_PLATSHALLARE_RE: Final[re.Pattern[str]] = re.compile(r"\{\{([a-zA-Z0-9_.:-]+)\}\}")


class RapportMallFel(Exception):
    """Fel vid val av rapportmall eller ifyllnad av platshållare."""


MALLAR: Final[dict[str, str]] = {
    "skyfallsutredning": """# {{meta:titel}}

**Rapporttyp:** Skyfallsutredning för detaljplan
**Område:** {{meta:omrade}}
**Körnings-id:** `{{meta:korning_id}}`
**Kodversion:** `{{meta:kodversion}}`
**Körningstid:** {{meta:start}}
**Koordinatsystem:** SWEREF 99 18 00 (EPSG:3011), RH 2000

## 1. Sammanfattning och resultat

{{sektion:nyckeltal}}

## 2. Gränsvärdeskontroll

{{sektion:gransvarden}}

## 3. Tabeller

{{sektion:tabeller}}

## 4. Kartor

{{sektion:kartor}}

## 5. Metod

{{meta:metod}}

## 6. Osäkerhet och datakällor

{{meta:osakerhet_text}}

**Källangivelser:** {{meta:kallor}}
""",
    "ruttanalys": """# {{meta:titel}}

**Rapporttyp:** Ruttanalys och framkomlighet för räddningstjänst
**Område:** {{meta:omrade}}
**Körnings-id:** `{{meta:korning_id}}`
**Kodversion:** `{{meta:kodversion}}`
**Körningstid:** {{meta:start}}
**Koordinatsystem:** SWEREF 99 18 00 (EPSG:3011), RH 2000

## 1. Insatstider och nåbarhet

{{sektion:nyckeltal}}

## 2. Kontroll mot uppsatta gränsvärden

{{sektion:gransvarden}}

## 3. Detaljerade tabeller (K8)

{{sektion:tabeller}}

## 4. Kartunderlag

{{sektion:kartor}}

## 5. Metod

{{meta:metod}}

## 6. Osäkerhet och datakällor

{{meta:osakerhet_text}}

**Källangivelser:** {{meta:kallor}}
""",
    "grundvatten_vattenbalans": """# {{meta:titel}}

**Rapporttyp:** Grundvatten och vattenbalans
**Område:** {{meta:omrade}}
**Körnings-id:** `{{meta:korning_id}}`
**Kodversion:** `{{meta:kodversion}}`
**Körningstid:** {{meta:start}}
**Koordinatsystem:** SWEREF 99 18 00 (EPSG:3011), RH 2000

## 1. Vattenbalansposter

{{sektion:nyckeltal}}

## 2. Gränsvärden

{{sektion:gransvarden}}

## 3. Balanstabeller

{{sektion:tabeller}}

## 4. Kartor

{{sektion:kartor}}

## 5. Metod

{{meta:metod}}

## 6. Osäkerhet och datakällor

{{meta:osakerhet_text}}

**Källangivelser:** {{meta:kallor}}
""",
    "alternativjamforelse": """# {{meta:titel}}

**Rapporttyp:** Alternativjämförelse (före/efter eller alternativ A mot B)
**Område:** {{meta:omrade}}
**Körnings-id:** `{{meta:korning_id}}`
**Kodversion:** `{{meta:kodversion}}`
**Körningstid:** {{meta:start}}
**Koordinatsystem:** SWEREF 99 18 00 (EPSG:3011), RH 2000

## 1. Jämförande nyckeltal

{{sektion:nyckeltal}}

## 2. Gränsvärdesuppfyllelse

{{sektion:gransvarden}}

## 3. Jämförelsetabeller

{{sektion:tabeller}}

## 4. Differenskartor och kartunderlag

{{sektion:kartor}}

## 5. Metod

{{meta:metod}}

## 6. Osäkerhet och datakällor

{{meta:osakerhet_text}}

**Källangivelser:** {{meta:kallor}}
""",
    "planbeskrivning": """# {{meta:titel}}

**Rapporttyp:** Underlag till planbeskrivning
**Område:** {{meta:omrade}}
**Körnings-id:** `{{meta:korning_id}}`
**Kodversion:** `{{meta:kodversion}}`
**Körningstid:** {{meta:start}}
**Koordinatsystem:** SWEREF 99 18 00 (EPSG:3011), RH 2000

## 1. Planmässiga nyckeltal

{{sektion:nyckeltal}}

## 2. Bedömning mot gränsvärden

{{sektion:gransvarden}}

## 3. Sammanfattande tabeller

{{sektion:tabeller}}

## 4. Kartor

{{sektion:kartor}}

## 5. Metod och osäkerhet

{{meta:metod}}

{{meta:osakerhet_text}}

**Källangivelser:** {{meta:kallor}}
""",
}


def HAMTA_MALL(rapport_typ: str) -> str:
    """Returnerar malltext med platshållare för angiven rapporttyp."""
    if rapport_typ not in MALLAR:
        raise RapportMallFel(
            f"Okänd rapporttyp '{rapport_typ}'. Tillåtna: {', '.join(GILTIGA_RAPPORTTYPER)}."
        )
    return MALLAR[rapport_typ]


def hamta_mall(rapport_typ: str) -> str:
    """Alias för `HAMTA_MALL(rapport_typ)`."""
    return HAMTA_MALL(rapport_typ)


def _formatera_tal(varde: float | int, fmt: str | None = None) -> str:
    if fmt is not None and fmt != "":
        try:
            if fmt.endswith("d") and isinstance(varde, float):
                return format(int(round(varde)), fmt)
            return format(varde, fmt)
        except (ValueError, TypeError) as exc:
            raise RapportMallFel(
                f"Ogiltig formatsträng '{fmt}' för talvärde {varde!r}: {exc}"
            ) from exc

    if isinstance(varde, int) and not isinstance(varde, bool):
        return str(varde)

    fv = float(varde)
    if fv == 0.0:
        return "0"
    if abs(fv) < 1e-4:
        text = f"{fv:.6f}".rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text
    text = f"{fv:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def formatera_tabell_markdown(tabell: TabellData) -> str:
    """Formaterar en `TabellData` som deterministisk Markdown-tabell."""
    linjer: list[str] = [f"### {tabell.titel}", ""]
    huvud = "| " + " | ".join(tabell.kolumner) + " |"
    avskiljare = "| " + " | ".join("---" for _ in tabell.kolumner) + " |"
    linjer.append(huvud)
    linjer.append(avskiljare)
    for rad in tabell.rader:
        celler: list[str] = []
        for cell in rad:
            if isinstance(cell, int | float) and not isinstance(cell, bool):
                celler.append(_formatera_tal(cell))
            else:
                celler.append(str(cell))
        linjer.append("| " + " | ".join(celler) + " |")
    return "\n".join(linjer)


def _sektion_nyckeltal(underlag: RapportUnderlag) -> str:
    if not underlag.tal:
        return "Inga nyckeltal registrerade i underlaget."
    rader = [
        f"- **{nyckel}**: {_formatera_tal(underlag.tal[nyckel])}"
        for nyckel in sorted(underlag.tal)
    ]
    return "\n".join(rader)


def _sektion_gransvarden(underlag: RapportUnderlag) -> str:
    if not underlag.gransvarden:
        return "Inga gränsvärden angivna för kontroll."

    linjer = [
        "| Kontroll | Uppmätt | Gräns | Enhet | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    alla_ok = True
    for gv in underlag.gransvarden:
        status = "Godkänd" if gv.godkand else "Överskrids"
        if not gv.godkand:
            alla_ok = False
        linjer.append(
            f"| {gv.namn} | {_formatera_tal(gv.uppmatt)} | "
            f"{_formatera_tal(gv.grans)} | {gv.enhet} | {status} |"
        )
    sammanfattning = (
        "Samtliga gränsvärden uppfylls."
        if alla_ok
        else "Ett eller flera gränsvärden överskrids (ej godkänd)."
    )
    linjer.append("")
    linjer.append(f"**Slutsats:** {sammanfattning}")
    return "\n".join(linjer)


def _sektion_tabeller(underlag: RapportUnderlag) -> str:
    if not underlag.tabeller:
        return "Inga tabeller bifogade."
    return "\n\n".join(formatera_tabell_markdown(tab) for tab in underlag.tabeller)


def _sektion_kartor(underlag: RapportUnderlag) -> str:
    if not underlag.kartor:
        return "Inga kartor bifogade."
    delar: list[str] = []
    for karta in underlag.kartor:
        delar.append(
            f"### {karta.titel}\n\n"
            f"![{karta.titel}]({karta.fil})\n\n"
            f"*Källa: {karta.kalla}*"
        )
    return "\n\n".join(delar)


def _slagit_upp_meta(underlag: RapportUnderlag, falt: str) -> str:
    meta_karta: dict[str, str] = {
        "titel": underlag.titel,
        "omrade": underlag.omrade,
        "rapport_typ": underlag.rapport_typ,
        "korning_id": underlag.korning_id,
        "kodversion": underlag.manifest.kodversion,
        "start": underlag.manifest.start,
        "modul": underlag.manifest.modul,
        "kontrakt": underlag.manifest.kontrakt,
        "metod": underlag.metod,
        "osakerhet_text": underlag.osakerhet_text,
        "kallor": ", ".join(underlag.kalla_angivelser),
    }
    if falt not in meta_karta:
        raise RapportMallFel(f"Okänt metadatafält i platshållare: 'meta:{falt}'.")
    return meta_karta[falt]


def _ersatt_platshallare(uttryck: str, underlag: RapportUnderlag) -> str:
    delar = uttryck.split(":")
    kategori = delar[0]

    if kategori == "meta" and len(delar) == 2:
        return _slagit_upp_meta(underlag, delar[1])

    if kategori == "tal" and len(delar) in (2, 3):
        nyckel = delar[1]
        fmt = delar[2] if len(delar) == 3 else None
        if nyckel not in underlag.tal:
            raise RapportMallFel(f"Okänd talnyckel i platshållare: '{nyckel}'.")
        return _formatera_tal(underlag.tal[nyckel], fmt)

    if kategori == "karta" and len(delar) == 2:
        kart_id = delar[1]
        for karta in underlag.kartor:
            if karta.kart_id == kart_id:
                return f"![{karta.titel}]({karta.fil})\n*Källa: {karta.kalla}*"
        raise RapportMallFel(f"Okänt kart_id i platshållare: '{kart_id}'.")

    if kategori == "tabell" and len(delar) == 2:
        try:
            idx = int(delar[1])
        except ValueError as exc:
            raise RapportMallFel(f"Ogiltigt tabellindex i platshållare: '{delar[1]}'.") from exc
        if idx < 0 or idx >= len(underlag.tabeller):
            raise RapportMallFel(
                f"Tabellindex {idx} ligger utanför intervallet 0..{len(underlag.tabeller) - 1}."
            )
        return formatera_tabell_markdown(underlag.tabeller[idx])

    if kategori == "sektion" and len(delar) == 2:
        sektion = delar[1]
        if sektion == "nyckeltal":
            return _sektion_nyckeltal(underlag)
        if sektion == "gransvarden":
            return _sektion_gransvarden(underlag)
        if sektion == "tabeller":
            return _sektion_tabeller(underlag)
        if sektion == "kartor":
            return _sektion_kartor(underlag)
        raise RapportMallFel(f"Okänd sektion i platshållare: '{sektion}'.")

    raise RapportMallFel(f"Ogiltig platshållare: '{{{{{uttryck}}}}}'")


def rendera_mall(
    underlag: RapportUnderlag,
    *,
    text_leverantor: Callable[[RapportUnderlag], str] | None = None,
) -> str:
    """Renderar rapporttext genom att fylla platshållare deterministiskt från underlaget.

    En valfri `text_leverantor` (t.ex. framtida språkmodell) får skriva löptext med
    platshållare, men alla siffror fylls alltid ur `underlag` och resultatet måste
    sedan passera `kontrollera_rapport`.
    """
    mall_text = (
        text_leverantor(underlag)
        if text_leverantor is not None
        else HAMTA_MALL(underlag.rapport_typ)
    )
    if not isinstance(mall_text, str) or not mall_text.strip():
        raise RapportMallFel("Rapportmall eller text från text_leverantor får inte vara tom.")

    def _ersatt(match: re.Match[str]) -> str:
        return _ersatt_platshallare(match.group(1), underlag)

    renderad = _PLATSHALLARE_RE.sub(_ersatt, mall_text)
    if "{{" in renderad or "}}" in renderad:
        raise RapportMallFel("Oupplöst eller ogiltig platshållarsyntax kvarstår i renderad text.")
    return renderad.rstrip() + "\n"
