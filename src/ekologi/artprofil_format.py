"""Artprofilens filformat `floden-artprofil/v1` (kontrakt K44): JSON in och ut.

Formatet bär exakt fälten i `ArtProfil`. Ett totalt hinder (motstånd oändligt) skrivs som `null`,
eftersom JSON saknar oändlighet. Läsaren är sträng: okända fält, saknade fält och värden utanför
kärnans regler ger `EkologiFel`, inget gissas. Profilen är en artgrupp eller en generell
spridningsprofil, aldrig fynd av enskilda arter eller lokaler (skyddsklassade artfynd får inte
förekomma i Floden).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from ekologi.typer import ArtProfil, EkologiFel

FORMAT_ARTPROFIL: Final[str] = "floden-artprofil/v1"
PROFIL_ID_MONSTER: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PARAMETERKALLA_STANDARD: Final[str] = (
    "Artprofilernas parametrar saknar exakta källhänvisningar (källa, tabell, sida). De är "
    "skissparametrar för att jämföra planalternativ, inte värden ur en granskad källa."
)
PARAMETERKALLA_EGEN: Final[str] = (
    "Profilen kommer ur en indatafil. Floden har inte kontrollerat dess källor; "
    "parametrarna är skissparametrar."
)
_FALT: Final[frozenset[str]] = frozenset(
    {
        "format",
        "profil_id",
        "namn",
        "beskrivning",
        "kalla",
        "habitat_markslag",
        "min_area_m2",
        "spridningsavstand_m",
        "motstand_per_markslag",
        "standard_motstand",
        "min_kronhojd_m",
        "motstand_osakerhet_faktor",
        "metodstatus",
    }
)


def _tal(d: Mapping[str, Any], namn: str) -> float:
    v = d[namn]
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise EkologiFel(f"Artprofil: '{namn}' måste vara ett ändligt tal, fick {v!r}.")
    return float(v)


def _text(d: Mapping[str, Any], namn: str) -> str:
    v = d[namn]
    if not isinstance(v, str) or not v.strip():
        raise EkologiFel(f"Artprofil: '{namn}' måste vara en icke-tom text.")
    return v


def _markslagskod(nyckel: Any, falt: str) -> int:
    try:
        kod = int(nyckel)
    except (TypeError, ValueError) as exc:
        raise EkologiFel(f"Artprofil: ogiltig markslagskod {nyckel!r} i '{falt}'.") from exc
    if isinstance(nyckel, bool) or not 0 <= kod <= 255 or str(kod) != str(nyckel):
        raise EkologiFel(
            f"Artprofil: markslagskod i '{falt}' ska vara ett heltal 0-255: {nyckel!r}."
        )
    return kod


def artprofil_till_dict(profil: ArtProfil) -> dict[str, Any]:
    """Profilen som JSON-vänlig dict i formatet `floden-artprofil/v1` (sorterade koder)."""
    motstand: dict[str, float | None] = {}
    for kod in sorted(profil.motstand_per_markslag):
        r = float(profil.motstand_per_markslag[kod])
        motstand[str(kod)] = None if math.isinf(r) else r
    return {
        "format": FORMAT_ARTPROFIL,
        "profil_id": profil.profil_id,
        "namn": profil.namn,
        "beskrivning": profil.beskrivning,
        "kalla": profil.kalla,
        "habitat_markslag": sorted(profil.habitat_markslag),
        "min_area_m2": float(profil.min_area_m2),
        "spridningsavstand_m": float(profil.spridningsavstand_m),
        "motstand_per_markslag": motstand,
        "standard_motstand": float(profil.standard_motstand),
        "min_kronhojd_m": float(profil.min_kronhojd_m),
        "motstand_osakerhet_faktor": [float(x) for x in profil.motstand_osakerhet_faktor],
        "metodstatus": profil.metodstatus,
    }


def artprofil_fran_dict(d: Mapping[str, Any]) -> ArtProfil:
    """Tolkar och validerar en profil i formatet `floden-artprofil/v1`."""
    if not isinstance(d, Mapping):
        raise EkologiFel("Artprofil: måste vara ett JSON-objekt.")
    okanda = sorted(set(d) - _FALT)
    saknas = sorted(_FALT - set(d))
    if okanda:
        raise EkologiFel(f"Artprofil: okända fält {okanda}.")
    if saknas:
        raise EkologiFel(f"Artprofil: fält saknas {saknas}.")
    if d["format"] != FORMAT_ARTPROFIL:
        raise EkologiFel(f"Artprofil: format ska vara '{FORMAT_ARTPROFIL}', fick {d['format']!r}.")
    profil_id = _text(d, "profil_id")
    if not PROFIL_ID_MONSTER.match(profil_id):
        raise EkologiFel(
            f"Artprofil: profil_id '{profil_id}' ska matcha {PROFIL_ID_MONSTER.pattern}."
        )
    if d["metodstatus"] != "skiss":
        raise EkologiFel("Artprofil: metodstatus ska vara 'skiss' (ingen profil är granskad).")
    hab = d["habitat_markslag"]
    if not isinstance(hab, list) or not hab:
        raise EkologiFel("Artprofil: 'habitat_markslag' måste vara en icke-tom lista av koder.")
    habitat = frozenset(_markslagskod(k, "habitat_markslag") for k in hab)
    rader = d["motstand_per_markslag"]
    if not isinstance(rader, Mapping) or not rader:
        raise EkologiFel("Artprofil: 'motstand_per_markslag' måste vara ett icke-tomt objekt.")
    motstand: dict[int, float] = {}
    for nyckel, v in rader.items():
        kod = _markslagskod(nyckel, "motstand_per_markslag")
        if v is None:
            motstand[kod] = math.inf
            continue
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
            raise EkologiFel(f"Artprofil: motstånd för kod {kod} ska vara tal eller null: {v!r}.")
        if float(v) < 1.0:
            raise EkologiFel(f"Artprofil: motstånd för kod {kod} ska vara >= 1, fick {v}.")
        motstand[kod] = float(v)
    min_area = _tal(d, "min_area_m2")
    spridning = _tal(d, "spridningsavstand_m")
    standard = _tal(d, "standard_motstand")
    kron = _tal(d, "min_kronhojd_m")
    if min_area <= 0.0 or spridning <= 0.0:
        raise EkologiFel("Artprofil: 'min_area_m2' och 'spridningsavstand_m' ska vara > 0.")
    if standard < 1.0:
        raise EkologiFel("Artprofil: 'standard_motstand' ska vara >= 1.")
    if kron < 0.0:
        raise EkologiFel("Artprofil: 'min_kronhojd_m' ska vara >= 0.")
    fak = d["motstand_osakerhet_faktor"]
    if (
        not isinstance(fak, list)
        or len(fak) != 2
        or any(isinstance(x, bool) or not isinstance(x, int | float) for x in fak)
    ):
        raise EkologiFel("Artprofil: 'motstand_osakerhet_faktor' ska vara [lo, hi].")
    lo, hi = float(fak[0]), float(fak[1])
    if not (math.isfinite(lo) and math.isfinite(hi) and 0.0 < lo <= hi):
        raise EkologiFel("Artprofil: 'motstand_osakerhet_faktor' ska uppfylla 0 < lo <= hi.")
    return ArtProfil(
        profil_id=profil_id,
        namn=_text(d, "namn"),
        beskrivning=_text(d, "beskrivning"),
        kalla=_text(d, "kalla"),
        habitat_markslag=habitat,
        min_area_m2=min_area,
        spridningsavstand_m=spridning,
        motstand_per_markslag=motstand,
        standard_motstand=standard,
        min_kronhojd_m=kron,
        motstand_osakerhet_faktor=(lo, hi),
        metodstatus="skiss",
    )


def las_artprofil_fil(sokvag: str | Path) -> ArtProfil:
    """Läser en profilfil (`floden-artprofil/v1`, JSON)."""
    fil = Path(sokvag)
    try:
        data = json.loads(fil.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EkologiFel(f"Artprofilfilen {fil.name} går inte att läsa: {exc}") from exc
    return artprofil_fran_dict(data)
