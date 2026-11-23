# ruff: noqa: E501
"""Belägg för VSC på fasadpunkter (dagsljus), ur körningens `vsc.csv` (R2).

`dagsljus.vsc_fasad` skriver en CSV per ruta och scenario (`vsc/<scenario>/<ruta>/vsc.csv`, en rad
per fasadpunkt ur K11 med `fasad_id`, `vsc_procent` och `status`). Rapporten räknar inget själv:
Floden (den här modulen, i beläggskällan) läser samma filer som webben visar i resultattabellen och
räknar antal och extremvärden. Kontrollsumman är en hash av alla filer som ingår (`sha256` per fil,
sorterade efter sökväg), så att kontrollen inför export upptäcker ett ändrat resultat.

Adresser (`FlodenBelaggKalla.hamta`):

| Adress | Mått |
| --- | --- |
| `fasad.<analys>.grans_procent`, `fasad.<analys>.kvot` | BRE:s gräns för VSC (27 %) och kvoten mot läget före (0,8): parametrar i måttet, inte resultat |
| `fasad.<analys>.<scenario>.<mått>` | `antal_punkter`, `antal_utan_varde`, `vsc_min_procent`, `vsc_medel_procent`, `antal_under_grans` |
| `fasadjamforelse.<analys>.<fore>.<efter>.<mått>` | punkter som finns i båda scenarierna (samma `fasad_id`): `antal_jamforbara`, `antal_nya`, `antal_borta`, `antal_under_grans_fore`, `antal_under_grans_efter`, `antal_nytt_under_grans`, `antal_under_kvot`, `antal_under_bada`, `max_minskning_procentenheter`, `min_kvot`, `antal_nya_under_grans` |

Gränsen och kvoten (BRE:s riktlinjer för grannpåverkan) är kriterier i *måttets definition*. Floden
anger bara hur många punkter som ligger under dem; ingen bedömning mot krav görs i nivå 1.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from gemensamt.hash import sha256_fil
from rapport.pastaende import Belagg

if TYPE_CHECKING:
    from rapport.belaggskalla import FlodenBelaggKalla

BRE_VSC_GRANS_PROCENT: Final[float] = 27.0
"""BRE:s gräns för vertikal himmelskomponent (VSC) vid fasad: 27 %."""
BRE_VSC_KVOT: Final[float] = 0.8
"""Under gränsen: kvoten mot läget före (efter / före) som BRE kräver, 0,8."""

_TID_VSC: Final[str] = "ingen tidsdimension (mulen himmel)"
_NAMNARE_PUNKTER: Final[str] = (
    "fasadpunkter (K11) i hela området; inte verkliga fönster"
)
_NAMNARE_JFR: Final[str] = _NAMNARE_PUNKTER
"""Samma nämnare för enskilda scenarier och jämförelser (påståenden blandar dem); jämförelsens
urval (punkter som finns i både före och efter) står i måttets statistik."""

PUNKTMATT: Final[tuple[str, ...]] = (
    "antal_punkter",
    "antal_utan_varde",
    "vsc_min_procent",
    "vsc_medel_procent",
    "antal_under_grans",
)
JAMFORMATT: Final[tuple[str, ...]] = (
    "antal_jamforbara",
    "antal_nya",
    "antal_borta",
    "antal_under_grans_fore",
    "antal_under_grans_efter",
    "antal_nytt_under_grans",
    "antal_under_kvot",
    "antal_under_bada",
    "max_minskning_procentenheter",
    "min_kvot",
    "antal_nya_under_grans",
)
PARAMETRAR: Final[tuple[str, ...]] = ("grans_procent", "kvot")


class FasadFel(Exception):
    """Fasadpunkterna kan inte läsas ur körningen."""


@dataclass(frozen=True)
class Fasadvarden:
    """VSC per `fasad_id` för ett scenario, och vilka filer det kommer ur."""

    vsc: Mapping[str, float]
    antal_utan_varde: int
    filer: tuple[str, ...]
    sha256: str


def las_fasadvarden(utmapp: Path, analys: str, scenario: str) -> Fasadvarden:
    """Alla `vsc.csv` för analysen och scenariot (en per ruta), sammanslagna per `fasad_id`."""
    filer = sorted((utmapp / analys / scenario).glob("*/vsc.csv"))
    if not filer:
        raise FasadFel(f"Inga vsc.csv för {analys}/{scenario} i körningen (är analysen VSC?).")
    vsc: dict[str, float] = {}
    utan = 0
    delar: list[str] = []
    for fil in filer:
        rel = fil.relative_to(utmapp).as_posix()
        delar.append(f"{rel}:{sha256_fil(fil)}")
        with fil.open(encoding="utf-8", newline="") as fh:
            for rad in csv.DictReader(fh):
                fid = rad.get("fasad_id", "")
                text = (rad.get("vsc_procent") or "").strip()
                if not fid:
                    raise FasadFel(f"{rel}: en rad saknar fasad_id.")
                if fid in vsc:
                    raise FasadFel(
                        f"{rel}: fasadpunkten {fid} finns i flera rutor (varje punkt ska ägas av "
                        "en ruta)."
                    )
                if rad.get("status") != "ok" or not text:
                    utan += 1
                    continue
                v = float(text)
                if not math.isfinite(v):
                    raise FasadFel(f"{rel}: VSC för {fid} är inte ett ändligt tal.")
                vsc[fid] = v
    return Fasadvarden(
        vsc, utan, tuple(f.relative_to(utmapp).as_posix() for f in filer),
        hashlib.sha256("\n".join(delar).encode("utf-8")).hexdigest(),
    )  # fmt: skip


def _kombinerad(*delar: Fasadvarden) -> str:
    return delar[0].sha256 if len(delar) == 1 else hashlib.sha256(
        "\n".join(d.sha256 for d in delar).encode("utf-8")
    ).hexdigest()


def punktmatt(v: Fasadvarden, matt: str) -> tuple[float, str, float, str, str]:
    """(värde, enhet, upplösning, storhet, statistik) för ett mått på ett scenario."""
    vals = list(v.vsc.values())
    if matt == "antal_punkter":
        return float(len(vals)), "st", 1.0, "antal fasadpunkter med beräknat VSC", "antal"
    if matt == "antal_utan_varde":
        return (
            float(v.antal_utan_varde), "st", 1.0,
            "antal fasadpunkter utan VSC (inuti hinder eller utanför data)", "antal",
        )  # fmt: skip
    if not vals:
        raise FasadFel("Inga fasadpunkter med VSC i scenariot.")
    if matt == "vsc_min_procent":
        return (
            min(vals), "%", 0.1, "vertikal himmelskomponent (VSC) vid fasad",
            "minsta värde bland fasadpunkterna",
        )  # fmt: skip
    if matt == "vsc_medel_procent":
        return (
            sum(vals) / len(vals), "%", 0.1, "vertikal himmelskomponent (VSC) vid fasad",
            "medelvärde över fasadpunkterna (inte viktat efter fasadlängd)",
        )  # fmt: skip
    if matt == "antal_under_grans":
        n = sum(1 for x in vals if x < BRE_VSC_GRANS_PROCENT)
        return (
            float(n), "st", 1.0,
            "antal fasadpunkter med VSC under BRE:s gräns (parametern grans_procent)", "antal",
        )  # fmt: skip
    raise FasadFel(f"Okänt mått '{matt}'. Tillåtna: {', '.join(PUNKTMATT)}.")


def jamformatt(
    fore: Fasadvarden, efter: Fasadvarden, matt: str
) -> tuple[float, str, float, str, str]:
    """(värde, enhet, upplösning, storhet, statistik) för ett mått över före och efter."""
    gemensamma = sorted(set(fore.vsc) & set(efter.vsc))
    par = [(fore.vsc[i], efter.vsc[i]) for i in gemensamma]
    g = BRE_VSC_GRANS_PROCENT
    k = BRE_VSC_KVOT
    antal = "antal"
    if matt == "antal_jamforbara":
        return float(len(par)), "st", 1.0, "antal fasadpunkter som finns både före och efter", antal
    if matt == "antal_nya":
        n = len(set(efter.vsc) - set(fore.vsc))
        return float(n), "st", 1.0, "antal fasadpunkter som bara finns efter (nya fasader)", antal
    if matt == "antal_borta":
        n = len(set(fore.vsc) - set(efter.vsc))
        return float(n), "st", 1.0, "antal fasadpunkter som bara finns före (borttagna fasader)", antal
    if matt == "antal_nya_under_grans":
        n = sum(1 for i in set(efter.vsc) - set(fore.vsc) if efter.vsc[i] < g)
        return (
            float(n), "st", 1.0,
            "antal nya fasadpunkter med VSC under BRE:s gräns (parametern grans_procent)", antal,
        )  # fmt: skip
    if not par:
        raise FasadFel("Inga fasadpunkter finns i både före och efter.")
    if matt == "antal_under_grans_fore":
        n = sum(1 for a, _b in par if a < g)
        return float(n), "st", 1.0, "antal jämförbara punkter med VSC före under BRE:s gräns", antal
    if matt == "antal_under_grans_efter":
        n = sum(1 for _a, b in par if b < g)
        return float(n), "st", 1.0, "antal jämförbara punkter med VSC efter under BRE:s gräns", antal
    if matt == "antal_nytt_under_grans":
        n = sum(1 for a, b in par if a >= g > b)
        return (
            float(n), "st", 1.0,
            "antal jämförbara punkter som går från över till under BRE:s gräns", antal,
        )  # fmt: skip
    if matt == "antal_under_kvot":
        n = sum(1 for a, b in par if a > 0 and b < k * a)
        return (
            float(n), "st", 1.0,
            "antal jämförbara punkter där VSC efter är lägre än kvoten (parametern kvot) av VSC före",
            antal,
        )  # fmt: skip
    if matt == "antal_under_bada":
        n = sum(1 for a, b in par if a > 0 and b < g and b < k * a)
        return (
            float(n), "st", 1.0,
            "antal jämförbara punkter som ligger under både BRE:s gräns och kvoten mot före", antal,
        )  # fmt: skip
    if matt == "max_minskning_procentenheter":
        return (
            max(0.0, max(a - b for a, b in par)), "procentenheter", 0.1,
            "största minskning av VSC i en fasadpunkt (före minus efter)",
            "största värde bland de jämförbara punkterna",
        )  # fmt: skip
    if matt == "min_kvot":
        kv = [b / a for a, b in par if a > 0]
        if not kv:
            raise FasadFel("Inga jämförbara punkter med VSC över noll före.")
        return (
            min(kv), "andel", 0.01, "kvot mellan VSC efter och före i en fasadpunkt",
            "minsta värde bland de jämförbara punkterna",
        )  # fmt: skip
    raise FasadFel(f"Okänt mått '{matt}'. Tillåtna: {', '.join(JAMFORMATT)}.")


def _forsta_scenario(kalla: FlodenBelaggKalla, korning_id: str, analys: str) -> str:
    for t in kalla._resultat(korning_id)["tabeller"]:  # noqa: SLF001
        if t["analys"] == analys and t["rader"]:
            return str(t["rader"][0]["scenario"])
    raise FasadFel(f"Analysen '{analys}' har inga resultat i körningen.")


def parameter_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, analys: str, namn: str
) -> Belagg:
    """BRE:s gräns eller kvoten som belägg (parametrar i måttets definition, inte resultat)."""
    losare = kalla._losare(korning_id, analys)  # noqa: SLF001
    _m, msha = kalla._manifest(korning_id, "run.json")  # noqa: SLF001
    if namn == "grans_procent":
        varde, enhet, upp = BRE_VSC_GRANS_PROCENT, "%", 0.1
        storhet = "BRE:s gräns för vertikal himmelskomponent (VSC) vid fasad"
    else:
        varde, enhet, upp = BRE_VSC_KVOT, "andel", 0.01
        storhet = "BRE:s kvot mellan VSC efter och före när VSC efter är under gränsen"
    return Belagg(
        adress=adress,
        korning_id=korning_id,
        fil="run.json",
        fil_sha256=msha,
        manifest="run.json",
        manifest_sha256=msha,
        analys=analys,
        losare=losare,
        scenarier=(),
        mottagare="måttets definition",
        namnare="parameter i måttet (gäller alla fasadpunkter)",
        matt=f"vsc:{namn}",
        storhet=storhet,
        enhet=enhet,
        tidsdefinition=_TID_VSC,
        statistik="parameter i måttets definition (indata, ingen statistik)",
        varde=varde,
        upplosning=upp,
        jamforbarhet=kalla._jamforbarhet(korning_id, analys, (_forsta_scenario(kalla, korning_id, analys),)),  # noqa: SLF001
    )


def fasad_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, delar: list[str]
) -> Belagg:
    """Belägg för adresserna `fasad.…` och `fasadjamforelse.…` (se modulens beskrivning)."""
    typ = delar[0]
    utmapp = kalla.utmapp(korning_id)
    try:
        if typ == "fasad" and len(delar) == 3 and delar[2] in PARAMETRAR:
            return parameter_belagg(kalla, adress, korning_id, delar[1], delar[2])
        if typ == "fasad" and len(delar) == 4:
            _t, analys, scenario, matt = delar
            v = las_fasadvarden(utmapp, analys, scenario)
            varde, enhet, upp, storhet, stat = punktmatt(v, matt)
            scen: tuple[str, ...] = (scenario,)
            sha, filer = v.sha256, f"{analys}/{scenario}/*/vsc.csv"
            namnare, mott = _NAMNARE_PUNKTER, f"scenario {scenario}"
        elif typ == "fasadjamforelse" and len(delar) == 5:
            _t, analys, fore_id, efter_id, matt = delar
            fore = las_fasadvarden(utmapp, analys, fore_id)
            efter = las_fasadvarden(utmapp, analys, efter_id)
            varde, enhet, upp, storhet, stat = jamformatt(fore, efter, matt)
            scen = (fore_id, efter_id)
            sha = _kombinerad(fore, efter)
            filer = f"{analys}/{fore_id}/*/vsc.csv + {analys}/{efter_id}/*/vsc.csv"
            namnare, mott = _NAMNARE_JFR, "hela beräkningsområdet"
        else:
            raise FasadFel(
                f"Adressen '{adress}' ska vara fasad.<analys>.<scenario>.<mått>, "
                "fasad.<analys>.grans_procent|kvot eller "
                "fasadjamforelse.<analys>.<fore>.<efter>.<mått>."
            )
    except FasadFel as exc:
        from rapport.belaggskalla import BelaggFel

        raise BelaggFel(str(exc)) from exc
    losare = kalla._losare(korning_id, analys)  # noqa: SLF001
    _m, msha = kalla._manifest(korning_id, "run.json")  # noqa: SLF001
    if losare != "dagsljus.vsc_fasad":
        from rapport.belaggskalla import BelaggFel

        raise BelaggFel(f"Analysen '{analys}' är inte VSC på fasadpunkter ({losare}).")
    return Belagg(
        adress=adress,
        korning_id=korning_id,
        fil=filer,
        fil_sha256=sha,
        manifest="run.json",
        manifest_sha256=msha,
        analys=analys,
        losare=losare,
        scenarier=scen,
        mottagare=mott,
        namnare=namnare,
        matt=f"vsc:{matt}",
        storhet=storhet,
        enhet=enhet,
        tidsdefinition=_TID_VSC,
        statistik=stat,
        varde=float(varde),
        upplosning=upp,
        jamforbarhet=kalla._jamforbarhet(korning_id, analys, scen),  # noqa: SLF001
    )


__all__ = [
    "BRE_VSC_GRANS_PROCENT",
    "BRE_VSC_KVOT",
    "JAMFORMATT",
    "PARAMETRAR",
    "PUNKTMATT",
    "FasadFel",
    "Fasadvarden",
    "fasad_belagg",
    "jamformatt",
    "las_fasadvarden",
    "punktmatt",
]
