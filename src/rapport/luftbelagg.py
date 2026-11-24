# ruff: noqa: E501
"""Luft (R2): antaganden, beläggsadresser och intervallets förhållande till normen, ur lösaren `luft.mkn` (K21).

`luft.mkn` räknar **inte** fram något haltbidrag. Den tar en given timserie av lokalt PM10-bidrag
(CSV `tid,lokal_ug_m3`, räknad i ett annat verktyg), lägger till en uttryckligt angiven bakgrund och
skalar bidraget med ett faktorintervall för källstyrka och deposition. Intervallet är en känslighet
för de två faktorerna, inte en statistisk konfidens. Före och efter är **två analyser** (en serie var).

Alla tal i rapporten är belägg på den vanliga adressen `matvarde.<analys>.<scenario>.<mått>`
(`belaggskalla.LUFT_MATT`); den här modulen lägger inga nya adresstyper till. Här finns:

- `luftantaganden`: antagandelistan som Floden skriver ur jobbens K0-manifest (serien är extern,
  bakgrunden är ett antagande, vad intervallet är, om före och efter går att jämföra);
- `LUFT_ADRESSMATT`: måtten som en rapport kan använda;
- `intervallrelation`: hur intervallet [lägst, högst] antal dygn förhåller sig till högsta tillåtna
  antal dygn (`under`, `over`, `korsar`) för ett påståendes belägg. Kontrollen använder den för att
  stoppa påståenden om att normen klaras eller överskrids när intervallet korsar gränsen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from rapport.pastaende import Belagg

if TYPE_CHECKING:
    from rapport.belaggskalla import FlodenBelaggKalla

LUFT_LOSARE: Final[str] = "luft.mkn"
LUFT_ADRESSMATT: Final[tuple[str, ...]] = (
    "lagst_antal_dygn",
    "hogst_antal_dygn",
    "arsmedel_lagst_ug_m3",
    "arsmedel_hogst_ug_m3",
    "p90_4_lagst_ug_m3",
    "p90_4_hogst_ug_m3",
    "bakgrund_ug_m3",
    "grans_dygn_ug_m3",
    "max_dygn_ar_antal_dygn",
    "antal_hela_dygn",
    "emissionsfaktor_lagst",
    "emissionsfaktor_hogst",
    "depositionsfaktor_lagst",
    "depositionsfaktor_hogst",
)
"""Måtten (ur `belaggskalla.LUFT_MATT`) som kan användas i `matvarde.<analys>.<scenario>.<mått>`."""
INTERVALLMATT: Final[tuple[str, str, str]] = ("lagst_antal_dygn", "hogst_antal_dygn", "max_dygn_ar_antal_dygn")
"""Måtten som krävs för att säga hur intervallet förhåller sig till högsta antalet dygn."""
KANSLIGHETSTEXT: Final[str] = (
    "Intervallet är en känslighet för två faktorer på det lokala bidraget (källstyrka och utspädning "
    "eller deposition). Det är inte en statistisk konfidens och täcker inte osäkerheten i serien, "
    "bakgrunden eller vädret."
)
EXTERN_SERIE_TEXT: Final[str] = (
    "Det lokala haltbidraget är en given timserie som har räknats fram utanför Floden. Floden har "
    "inte räknat fram det och har inte granskat hur det togs fram."
)
BAKGRUND_TEXT: Final[str] = (
    "Bakgrunden är ett antagande: en konstant halt som läggs till hela serien. Den som angav "
    "bakgrunden ansvarar för dess källa; Floden prövar den inte."
)
EN_SERIE_TEXT: Final[str] = (
    "Underlaget har en serie. Före och efter förslaget kan inte jämföras: det kräver två serier "
    "från samma externa modell."
)
TVA_SERIER_TEXT: Final[str] = (
    "Före och efter förslaget bygger på två separata serier som har räknats fram utanför Floden. "
    "Floden kontrollerar bara att bakgrund, faktorintervall och gräns är lika i de två; att serierna "
    "är jämförbara i övrigt (samma modell, väder, trafikår och bakgrundsår) går inte att kontrollera."
)


def _sv(v: Any) -> str:
    try:
        return f"{float(v):g}".replace(".", ",")
    except (TypeError, ValueError):
        return str(v)


def luftlage(analys: str) -> str:
    """Analysens läge i löptext: `mkn_fore` är före, `mkn_efter` efter, annars id:t utan understreck."""
    for suffix, namn in (("fore", "före"), ("efter", "efter")):
        if analys == suffix or analys.endswith("_" + suffix):
            return namn
    return analys.replace("_", " ")


def _jobb(kalla: FlodenBelaggKalla, korning_id: str, analys: str, scenario: str) -> dict[str, Any]:
    rel = f"{analys}/{scenario}/run.json"
    manifest, _sha = kalla._manifest(korning_id, rel)  # noqa: SLF001
    return manifest


def luftantaganden(
    kalla: FlodenBelaggKalla, korning_id: str, analyser: Mapping[str, list[str]]
) -> list[str]:
    """Antaganden och förutsättningar som Floden själv skriver, ur jobbens K0-manifest.

    `analyser`: analysens id -> scenarier. Texten står i rapportens antagandelista (inte som
    påstående): serien är extern, bakgrunden ett antagande, vad intervallet är, gräns och
    högsta antal dygn, och om före och efter kan jämföras. Lösarens varningar följer med."""
    ut: list[str] = [EXTERN_SERIE_TEXT, BAKGRUND_TEXT, KANSLIGHETSTEXT]
    varningar: list[str] = []
    param_per_analys: dict[str, dict[str, Any]] = {}
    for analys, scenarier in analyser.items():
        if not scenarier:
            continue
        m = _jobb(kalla, korning_id, analys, scenarier[0])
        p = m.get("parametrar", {})
        v = m.get("matvarden", {})
        param_per_analys[analys] = {
            k: p.get(k)
            for k in ("bakgrund_ug_m3", "emissionsfaktor_intervall", "depositionsfaktor_intervall", "grans_dygn_ug_m3", "max_dygn_ar")
        }
        ef, df = p.get("emissionsfaktor_intervall", []), p.get("depositionsfaktor_intervall", [])
        ut.append(
            f"Läget {luftlage(analys)}: PM10, bakgrund {_sv(p.get('bakgrund_ug_m3'))} µg/m³ (konstant), "
            f"faktor på källstyrkan {_sv(ef[0]) if ef else '?'} till {_sv(ef[1]) if len(ef) > 1 else '?'}, "
            f"faktor på utspädning och deposition {_sv(df[0]) if df else '?'} till {_sv(df[1]) if len(df) > 1 else '?'}, "
            f"dygnsgräns {_sv(p.get('grans_dygn_ug_m3'))} µg/m³ och högst {_sv(p.get('max_dygn_ar'))} dygn över gränsen per år; "
            f"serien har {_sv(v.get('antal_hela_dygn'))} hela dygn."
        )
        varningar += [f"Läget {luftlage(analys)}: {x}" for x in m.get("varningar", [])]
    ut.append(TVA_SERIER_TEXT if len(param_per_analys) >= 2 else EN_SERIE_TEXT)
    if len(param_per_analys) >= 2:
        forsta = next(iter(param_per_analys.values()))
        if any(p != forsta for p in param_per_analys.values()):
            ut.append(
                "Varning: analyserna har olika bakgrund, faktorintervall eller gräns. Då kan läget före och efter "
                "inte jämföras: skillnaden kommer ur antagandena, inte ur förslaget."
            )
    ut.extend(varningar)
    return list(dict.fromkeys(ut))


def intervallrelation(belagg: Sequence[Belagg]) -> dict[tuple[str, str], str | None]:
    """Intervallets förhållande till högsta antalet dygn per (analys, scenario) bland beläggen.

    `under`: högsta antalet dygn är högst lika med gränsen (klaras i hela intervallet); `over`: lägsta
    antalet är över gränsen (överskrids i hela intervallet); `korsar`: gränsen ligger i intervallet;
    `None`: beläggen räcker inte (lägst, högst och högsta tillåtna antal måste alla vara med)."""
    grupper: dict[tuple[str, str], dict[str, float]] = {}
    for b in belagg:
        if b.losare != LUFT_LOSARE:
            continue
        delar = b.adress.split(".")
        if len(delar) != 4 or delar[0] != "matvarde":
            continue
        grupper.setdefault((delar[1], delar[2]), {})[delar[3]] = b.varde
    ut: dict[tuple[str, str], str | None] = {}
    for nyckel, v in grupper.items():
        if not all(m in v for m in INTERVALLMATT):
            ut[nyckel] = None
        elif v["hogst_antal_dygn"] <= v["max_dygn_ar_antal_dygn"]:
            ut[nyckel] = "under"
        elif v["lagst_antal_dygn"] > v["max_dygn_ar_antal_dygn"]:
            ut[nyckel] = "over"
        else:
            ut[nyckel] = "korsar"
    return ut


__all__ = [
    "BAKGRUND_TEXT",
    "EN_SERIE_TEXT",
    "EXTERN_SERIE_TEXT",
    "INTERVALLMATT",
    "KANSLIGHETSTEXT",
    "LUFT_ADRESSMATT",
    "LUFT_LOSARE",
    "TVA_SERIER_TEXT",
    "intervallrelation",
    "luftantaganden",
    "luftlage",
]
