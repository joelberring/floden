"""Kanonisk JSON och nycklar (sha256) för innehållsadressering.

Systemdesignen föreslår BLAKE3; repot använder sha256 (K0, recept.json), så kärnan gör det också.
Byte av hashfunktion är ett beslut för `docs/BESLUT.md`.
"""

from __future__ import annotations

import json
import math
from typing import Any

from gemensamt.hash import sha256_bytes


class HashFel(ValueError):
    """Värdet kan inte hashas deterministiskt."""


def _normalisera(varde: Any, sokvag: str) -> Any:
    if varde is None or isinstance(varde, bool | str):
        return varde
    if isinstance(varde, int):
        return varde
    if isinstance(varde, float):
        if not math.isfinite(varde):
            raise HashFel(f"{sokvag}: icke-ändligt tal kan inte hashas ({varde}).")
        # -0.0 och 0.0 ska ge samma nyckel; heltalsvärda flyttal skrivs som flyttal.
        return 0.0 if varde == 0 else varde
    if isinstance(varde, dict):
        ut: dict[str, Any] = {}
        for k, v in varde.items():
            if not isinstance(k, str):
                raise HashFel(f"{sokvag}: nycklar måste vara strängar, fick {type(k).__name__}.")
            ut[k] = _normalisera(v, f"{sokvag}.{k}")
        return ut
    if isinstance(varde, list | tuple):
        return [_normalisera(v, f"{sokvag}[{i}]") for i, v in enumerate(varde)]
    raise HashFel(f"{sokvag}: typen {type(varde).__name__} kan inte hashas.")


def kanonisk_json(varde: Any) -> str:
    """JSON med sorterade nycklar och utan blanksteg: samma innehåll ger samma text."""
    return json.dumps(
        _normalisera(varde, "$"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def nyckel_for(varde: Any) -> str:
    """sha256 av den kanoniska JSON-texten (64 hextecken)."""
    return sha256_bytes(kanonisk_json(varde).encode("utf-8"))
