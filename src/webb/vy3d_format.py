"""Binärformatet `floden-3d/v1` för 3D-vyn: en rubrik i JSON och därefter råa talmatriser.

Geometri och resultat till 3D-vyn skickas aldrig som JSON med miljontals tal. Varje svar är en
behållare:

    "FL3D" | uint32 rubrikens längd N (little-endian) | rubrik (JSON, utf-8, blanksteg upp till
    jämn 8-delning) | delarna, var och en på en 8-byte-gräns

Rubriken har alltid `format`, `typ` och `delar` (namn, dtype, form och förskjutning, räknad från
den första delens början) och därtill det som just den typen behöver (utbredning, skalor). Alla tal
är little-endian. Klienten (`webb/src/vy3d/format.ts`) läser delarna som typade vyer över samma
buffert utan kopiering.

dtype: `f4` float32, `u1` uint8, `u2` uint16, `u4` uint32, `i2` int16.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

FORMAT = "floden-3d/v1"
MAGI = b"FL3D"
_DTYPER = {"f4": "<f4", "u1": "u1", "u2": "<u2", "u4": "<u4", "i2": "<i2"}
_JUSTERING = 8


class FormatFel(ValueError):
    """Behållaren är trasig eller har fel typ."""


def _kod(arr: NDArray[Any]) -> str:
    for namn, dt in _DTYPER.items():
        if arr.dtype == np.dtype(dt):
            return namn
    raise FormatFel(f"dtype {arr.dtype} stöds inte i {FORMAT}.")


def _upp(n: int) -> int:
    return -(-n // _JUSTERING) * _JUSTERING


def kapsla(typ: str, huvud: Mapping[str, Any], delar: Sequence[tuple[str, NDArray[Any]]]) -> bytes:
    """Bygger en behållare. `huvud` får inte innehålla `format`, `typ` eller `delar`."""
    for reserverat in ("format", "typ", "delar"):
        if reserverat in huvud:
            raise FormatFel(f"Rubriken får inte själv ha '{reserverat}'.")
    beskrivning: list[dict[str, Any]] = []
    kroppar: list[bytes] = []
    pos = 0
    for namn, arr in delar:
        a = np.ascontiguousarray(arr)
        kod = _kod(a)
        rader = a.astype(_DTYPER[kod], copy=False).tobytes()
        beskrivning.append(
            {"namn": namn, "dtype": kod, "form": list(a.shape), "forskjutning": pos}
        )
        kroppar.append(rader + b"\0" * (_upp(len(rader)) - len(rader)))
        pos += _upp(len(rader))
    rubrik = json.dumps(
        {"format": FORMAT, "typ": typ, **huvud, "delar": beskrivning},
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    # Rubriken fylls ut så att delarna börjar på en 8-byte-gräns (4 + 4 + rubrik).
    rubrik += b" " * (_upp(8 + len(rubrik)) - 8 - len(rubrik))
    return MAGI + struct.pack("<I", len(rubrik)) + rubrik + b"".join(kroppar)


def packa_upp(data: bytes) -> tuple[dict[str, Any], dict[str, NDArray[Any]]]:
    """Läser en behållare (för tester och verktyg). Ger rubriken och delarna som matriser."""
    if len(data) < 8 or data[:4] != MAGI:
        raise FormatFel("Inte en floden-3d-behållare (saknar FL3D).")
    (n,) = struct.unpack("<I", data[4:8])
    if 8 + n > len(data):
        raise FormatFel("Rubriken är längre än svaret.")
    huvud: dict[str, Any] = json.loads(data[8 : 8 + n].decode("utf-8"))
    if huvud.get("format") != FORMAT:
        raise FormatFel(f"Okänt format {huvud.get('format')!r}.")
    start = 8 + n
    delar: dict[str, NDArray[Any]] = {}
    for d in huvud["delar"]:
        dt = np.dtype(_DTYPER[d["dtype"]])
        antal = int(np.prod(d["form"])) if d["form"] else 1
        b0 = start + int(d["forskjutning"])
        if b0 + antal * dt.itemsize > len(data):
            raise FormatFel(f"Delen '{d['namn']}' ligger utanför svaret.")
        delar[d["namn"]] = np.frombuffer(data, dtype=dt, count=antal, offset=b0).reshape(d["form"])
    return huvud, delar
