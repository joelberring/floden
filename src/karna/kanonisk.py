"""Kanonisk serialisering och innehållshashar för kärnan (T20).

Alla nycklar i kärnan är sha256 av kanonisk JSON: sorterade nycklar, inga blanksteg,
UTF-8 och flyttal skrivna med `repr` (kortaste exakta form). NaN och oändligheter
tillåts inte, eftersom de inte har en entydig JSON-form.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_fil


class KanoniskFel(Exception):
    """Ett värde kan inte serialiseras kanoniskt."""


def _normalisera(varde: Any, sokvag: str) -> Any:
    if varde is None or isinstance(varde, bool | str):
        return varde
    if isinstance(varde, int):
        return varde
    if isinstance(varde, float):
        if not math.isfinite(varde):
            raise KanoniskFel(f"Icke-ändligt flyttal i {sokvag}: {varde!r}")
        # Heltalsvärda flyttal och heltal ska inte ge olika nycklar (1.0 == 1).
        if varde.is_integer() and abs(varde) < 2**53:
            return int(varde)
        return varde
    if isinstance(varde, Path):
        return str(varde)
    if isinstance(varde, Mapping):
        ut: dict[str, Any] = {}
        for nyckel, del_varde in varde.items():
            if not isinstance(nyckel, str):
                raise KanoniskFel(f"Nycklar måste vara strängar i {sokvag}, fick {nyckel!r}")
            ut[nyckel] = _normalisera(del_varde, f"{sokvag}.{nyckel}")
        return ut
    if isinstance(varde, Sequence):
        return [_normalisera(v, f"{sokvag}[{i}]") for i, v in enumerate(varde)]
    raise KanoniskFel(f"Typen {type(varde).__name__} kan inte serialiseras i {sokvag}.")


def kanonisk_json(varde: Any) -> str:
    """Serialiserar ett JSON-kompatibelt värde till kanonisk sträng."""
    return json.dumps(
        _normalisera(varde, "$"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def kanonisk_hash(varde: Any) -> str:
    """sha256 (hex) av kanonisk JSON."""
    return hashlib.sha256(kanonisk_json(varde).encode("utf-8")).hexdigest()


def mapp_hash(mapp: str | Path, *, utelamna: Sequence[str] = ()) -> str:
    """Innehållshash för en katalog: sorterade relativa sökvägar och filernas sha256.

    Tomma kataloger påverkar inte hashen. Filnamn i `utelamna` (relativa sökvägar med `/`)
    räknas inte med, till exempel ett `run.json` med tidsstämpel.
    """
    rot = Path(mapp)
    if not rot.is_dir():
        raise KanoniskFel(f"Katalogen finns inte: {rot}")
    hoppa = set(utelamna)
    poster: list[list[str]] = []
    for fil in sorted(p for p in rot.rglob("*") if p.is_file()):
        rel = fil.relative_to(rot).as_posix()
        if rel in hoppa:
            continue
        poster.append([rel, sha256_fil(fil)])
    return kanonisk_hash({"mapp": poster})
