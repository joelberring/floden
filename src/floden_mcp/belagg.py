"""Belägg: varje tal i ett svar ska gå att peka ut i en fil som Floden skrev.

Ett belägg är `{korning_id, fil, mottagare, cell, matt, enhet}`:

- `korning_id`: körningen talet kommer ur.
- `fil`: filen i körningens resultatmapp (relativ), t.ex. `skyfall/efter/max_djup.tif`,
  `jamforelser/<namn>/diff.tif` eller `<analys>/<scenario>/run.json` (K0-manifestet) för mätvärden.
- `mottagare`: vad värdet gäller (scenario och ruta, jämförelse, polygon, fasadpunkt).
- `cell`: för ett rastervärde cellens mittpunkt (EPSG:3011) och storlek.
- `matt`: måttets namn (rastrets roll eller mätvärdets namn).
- `enhet`: enheten, eller `null` när Floden inte anger någon.

`fil_sha256` finns med när filen är en rasterutdata (kontrollsumman ur körningens K0-manifest).
Verktygen konstruerar belägg ur kärnans svar; de räknar aldrig fram ett värde.
"""

from __future__ import annotations

from typing import Any

# Enheter ur variabelnamnens suffix (AGENTS.md: enheten står i namnet). Längst suffix först.
_SUFFIX: tuple[tuple[str, str], ...] = (
    ("_mm_h", "mm/h"),
    ("_m3", "m³"),
    ("_m2", "m²"),
    ("_grader", "grader"),
    ("_procent", "%"),
    ("_rel", "andel"),
    ("_mm", "mm"),
    ("_min", "min"),
    ("_ar", "år"),
    ("_h", "h"),
    ("_s", "s"),
    ("_m", "m"),
)


def enhet_for(namn: str) -> str | None:
    """Enheten ur måttets namn (`max_djup_m` -> `m`), eller None när namnet inte anger någon."""
    for suffix, enhet in _SUFFIX:
        if namn.endswith(suffix):
            return enhet
    return None


def belagg(
    korning_id: str,
    fil: str,
    matt: str,
    enhet: str | None,
    *,
    mottagare: str | None = None,
    cell: dict[str, Any] | None = None,
    fil_sha256: str | None = None,
) -> dict[str, Any]:
    """Ett belägg (se modulens beskrivning)."""
    ut: dict[str, Any] = {
        "korning_id": korning_id,
        "fil": fil,
        "mottagare": mottagare,
        "cell": cell,
        "matt": matt,
        "enhet": enhet,
    }
    if fil_sha256 is not None:
        ut["fil_sha256"] = fil_sha256
    return ut


def varde_med_belagg(varde: Any, belagg_: dict[str, Any]) -> dict[str, Any]:
    """Ett tal tillsammans med sitt belägg: `{varde, enhet, belagg}`."""
    return {"varde": varde, "enhet": belagg_["enhet"], "belagg": belagg_}
