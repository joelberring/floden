"""Kontrollsummor (SHA-256) för indata, utdata och reproducerbarhet."""

from __future__ import annotations

import hashlib
from pathlib import Path

_BLOCKSTORLEK_BYTES = 1024 * 1024  # 1 MiB


def sha256_bytes(data: bytes) -> str:
    """Beräknar hexadecimal SHA-256-kontrollsumma för en bytesekvens."""
    return hashlib.sha256(data).hexdigest()


def sha256_fil(sokvag: str | Path) -> str:
    """Beräknar hexadecimal SHA-256-kontrollsumma för en fil genom strömmande läsning.

    Args:
        sokvag: Sökväg till filen.

    Returns:
        64 tecken lång gemen hexsträng.

    Raises:
        FileNotFoundError: Om filen inte finns.
        IsADirectoryError: Om sökvägen pekar på en katalog.
    """
    fil_sokvag = Path(sokvag)
    if not fil_sokvag.exists():
        raise FileNotFoundError(f"Kan inte beräkna sha256, filen saknas: {fil_sokvag}")
    if fil_sokvag.is_dir():
        raise IsADirectoryError(f"Kan inte beräkna sha256 för en katalog: {fil_sokvag}")

    hashare = hashlib.sha256()
    with fil_sokvag.open("rb") as fh:
        while block := fh.read(_BLOCKSTORLEK_BYTES):
            hashare.update(block)
    return hashare.hexdigest()
