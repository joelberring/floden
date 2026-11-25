"""Lager 3: Lokal LRU-cache och tidsbaserad städning på webbservern (VPS).

Håller lokal S3-cache under konfigurerad maxstorlek (`max_gb`) och maxålder (`max_alder_dagar`).
Rör aldrig filer utanför den konfigurerade cachemappen och följer aldrig symlänkar.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BYTES_PER_MB = 1024.0 * 1024.0
BYTES_PER_GB = 1024.0 * 1024.0 * 1024.0
LRU_MAL_ANDEL = 0.85


class LokalCacheFel(Exception):
    """Fel vid konfiguration eller städning av lokal LRU-cache."""


@dataclass(frozen=True)
class LokalCacheKonfig:
    """Konfiguration för `[lagring.lokal_cache]`."""

    mapp: Path = Path("data/cache/s3_lru")
    max_gb: float = 10.0
    max_alder_dagar: float = 14.0

    def __post_init__(self) -> None:
        if self.max_gb <= 0.0:
            raise LokalCacheFel(f"max_gb måste vara > 0, fick {self.max_gb}.")
        if self.max_alder_dagar <= 0.0:
            raise LokalCacheFel(f"max_alder_dagar måste vara > 0, fick {self.max_alder_dagar}.")


@dataclass(frozen=True)
class StadaResultat:
    """Resultat från `stada_lokal_cache` (både skarp städning och `--torrkor`)."""

    mapp: Path
    torrkor: bool
    antal_raderade: int
    antal_raderade_alder: int
    antal_raderade_lru: int
    frigjorda_bytes: int
    kvarvarande_bytes: int
    raderade_filer: tuple[str, ...]

    @property
    def frigjorda_mb(self) -> float:
        return self.frigjorda_bytes / BYTES_PER_MB

    @property
    def kvarvarande_mb(self) -> float:
        return self.kvarvarande_bytes / BYTES_PER_MB


def las_lokal_cache_konfig(
    konfig: Mapping[str, Any] | None = None,
    *,
    miljo: Mapping[str, str] | None = None,
) -> LokalCacheKonfig:
    """Läser `[lagring.lokal_cache]` ur konfiguration och valfria `FLODEN_LOKAL_CACHE_*`."""
    env = os.environ if miljo is None else miljo
    lagring = konfig.get("lagring", {}) if konfig is not None else {}
    sektion = lagring.get("lokal_cache", {}) if isinstance(lagring, Mapping) else {}
    if not isinstance(sektion, Mapping):
        raise LokalCacheFel("[lagring.lokal_cache] måste vara ett TOML-objekt.")

    mapp_raw = env.get("FLODEN_LOKAL_CACHE_MAPP") or sektion.get("mapp") or "data/cache/s3_lru"
    max_gb_raw = env.get("FLODEN_LOKAL_CACHE_MAX_GB") or sektion.get("max_gb") or 10.0
    max_alder_raw = (
        env.get("FLODEN_LOKAL_CACHE_MAX_ALDER_DAGAR") or sektion.get("max_alder_dagar") or 14.0
    )
    try:
        max_gb = float(max_gb_raw)
        max_alder = float(max_alder_raw)
    except (TypeError, ValueError) as exc:
        raise LokalCacheFel(f"Ogiltiga tal i [lagring.lokal_cache]: {exc}") from exc

    return LokalCacheKonfig(
        mapp=Path(str(mapp_raw)),
        max_gb=max_gb,
        max_alder_dagar=max_alder,
    )


def markera_cache_anvand(fil: str | Path) -> None:
    """Uppdaterar `atime`/`mtime` på en cachad fil vid åtkomst för korrekt LRU-ordning."""
    sokvag = Path(fil)
    if sokvag.is_file() and not sokvag.is_symlink():
        os.utime(sokvag, None)


def _rensa_tomma_undermappar(rot: Path) -> None:
    """Tar bort tomma underkataloger under `rot` (rör aldrig `rot` själv eller symlänkar)."""
    for rot_str, mappar, _filer in os.walk(rot, topdown=False, followlinks=False):
        aktuell = Path(rot_str)
        for m in mappar:
            kandidat = aktuell / m
            if kandidat.is_symlink():
                continue
            try:
                resolved = kandidat.resolve()
                if resolved != rot and resolved.is_relative_to(rot):
                    kandidat.rmdir()
            except OSError:
                pass


def stada_lokal_cache(
    mapp: str | Path,
    max_gb: float = 10.0,
    max_alder_dagar: float = 14.0,
    *,
    torrkor: bool = False,
    nu_ts: float | None = None,
) -> StadaResultat:
    """Städar lokal LRU-cache efter ålder och maxstorlek (ned till 0,85 * max_gb).

    1. Raderar alla vanliga filer vars `mtime` (och `atime`) är äldre än `max_alder_dagar`.
    2. Om total storlek fortfarande > `max_gb`: raderar äldst använda filer (LRU) tills total
       storlek är <= `0,85 * max_gb`.
    3. Rör aldrig filer utanför `mapp` och följer aldrig symlänkar.
    """
    if max_gb <= 0.0:
        raise LokalCacheFel(f"max_gb måste vara > 0, fick {max_gb}.")
    if max_alder_dagar <= 0.0:
        raise LokalCacheFel(f"max_alder_dagar måste vara > 0, fick {max_alder_dagar}.")

    mapp_path = Path(mapp)
    if mapp_path.is_symlink():
        raise LokalCacheFel(f"Cachemappen får inte vara en symlänk: {mapp_path}")
    if not mapp_path.exists():
        return StadaResultat(
            mapp=mapp_path,
            torrkor=torrkor,
            antal_raderade=0,
            antal_raderade_alder=0,
            antal_raderade_lru=0,
            frigjorda_bytes=0,
            kvarvarande_bytes=0,
            raderade_filer=(),
        )
    if not mapp_path.is_dir():
        raise LokalCacheFel(f"Cachemappen är inte en katalog: {mapp_path}")

    rot = mapp_path.resolve()
    nu = time.time() if nu_ts is None else float(nu_ts)
    alder_grans_s = max_alder_dagar * 86400.0
    max_bytes = int(max_gb * BYTES_PER_GB)
    mal_bytes = int(LRU_MAL_ANDEL * max_gb * BYTES_PER_GB)

    # Samla in alla vanliga filer utan att följa symlänkar
    poster: list[tuple[Path, str, int, float, float]] = []
    for aktuell_str, mappar, filer in os.walk(rot, topdown=True, followlinks=False):
        aktuell = Path(aktuell_str)
        # Filtrera bort symlänkade undermappar så os.walk aldrig går in i dem
        mappar[:] = [m for m in mappar if not (aktuell / m).is_symlink()]
        for filnamn in filer:
            fil = aktuell / filnamn
            if fil.is_symlink():
                continue
            try:
                resolved = fil.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(rot):
                continue
            try:
                st = fil.lstat()
            except OSError:
                continue
            rel = fil.relative_to(rot).as_posix()
            poster.append((fil, rel, int(st.st_size), float(st.st_mtime), float(st.st_atime)))

    raderade_alder: list[tuple[Path, str, int]] = []
    kvar_efter_alder: list[tuple[Path, str, int, float, float]] = []

    for post in poster:
        fil, rel, storlek, mtime, atime = post
        if (nu - mtime) > alder_grans_s:
            raderade_alder.append((fil, rel, storlek))
        else:
            kvar_efter_alder.append(post)

    kvarvarande_bytes = sum(p[2] for p in kvar_efter_alder)
    raderade_lru: list[tuple[Path, str, int]] = []

    if kvarvarande_bytes > max_bytes:
        # Sortera äldst använda först (LRU via max(atime, mtime), sekundärt relativ sökväg)
        kvar_efter_alder.sort(key=lambda p: (max(p[4], p[3]), p[1]))
        for fil, rel, storlek, _mtime, _atime in kvar_efter_alder:
            if kvarvarande_bytes <= mal_bytes:
                break
            raderade_lru.append((fil, rel, storlek))
            kvarvarande_bytes -= storlek

    alla_raderade = [*raderade_alder, *raderade_lru]
    if not torrkor:
        for fil, _rel, _storlek in alla_raderade:
            if not fil.is_symlink() and fil.resolve().is_relative_to(rot):
                fil.unlink(missing_ok=True)
        _rensa_tomma_undermappar(rot)

    frigjorda_bytes = sum(p[2] for p in alla_raderade)
    return StadaResultat(
        mapp=rot,
        torrkor=torrkor,
        antal_raderade=len(alla_raderade),
        antal_raderade_alder=len(raderade_alder),
        antal_raderade_lru=len(raderade_lru),
        frigjorda_bytes=frigjorda_bytes,
        kvarvarande_bytes=kvarvarande_bytes,
        raderade_filer=tuple(p[1] for p in alla_raderade),
    )
