"""Automatisk lokal diskstädning (utan krav på S3) med atomiskt skydd av aktiva körningar & projekt.

Städar lokal cache under `cache_rot` (standard `data/cache`) i fyra säkra spår:
1. Övergivna tempfiler/mappar (`.floden_*`, `tmp_*`, avbrutna K22-tempmappar) > `grace_minuter`.
2. Gamla `webb/lager/v*`-versioner äldre än `grace_minuter`, följt av LRU på enskilda lagerfiler.
3. Avslutade/misslyckade `webb/korningar/<id>/` som inte skyddas av aktiv status, demo, projekt
   eller `grace_minuter` — först efter `max_alder_dagar`, sedan LRU ned till `max_gb * mal_andel`.
4. K22 `ResultatCache` (`karna/jobb/<nn>/<nyckel>/`, `karna/scenarier/<nn>/<nyckel>/`) — både
   trasiga/övergivna poster utan `.klar` äldre än `grace_minuter` och kompletta `.klar`-poster
   efter ålder och LRU — samt befintlig `stada_lokal_cache` för `s3_lru` om mappen finns.

Säkerhetsinvarianter:
- Importerar enbart standardbiblioteket och `gemensamt` (följer `test_paketgranser.py`).
- Följer aldrig symlänkar (`is_symlink()`) och raderar aldrig symlänkar eller mål utanför rot.
- Verifierar strikt `kandidat.resolve().is_relative_to(cache_rot.resolve())` före varje radering.
- Rör aldrig `data/konto/`, `data/lokal/`, `projekt.json`, `scenarier.json` eller K50-ritlager.
- Returnerar helt PII-fri driftsdata (inga användarnamn, e-postadresser eller projektnamn).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemensamt.lokal_cache import (
    BYTES_PER_GB,
    BYTES_PER_MB,
    LokalCacheFel,
    StadaResultat,
    stada_lokal_cache,
)

STANDARD_MAX_GB: float = 12.0
STANDARD_MAL_ANDEL: float = 0.85
STANDARD_MAX_ALDER_DAGAR: float = 14.0
STANDARD_GRACE_MINUTER: float = 30.0

_AKTIVA_KORNING_STATUSER: frozenset[str] = frozenset({"ko", "kor"})
_LAGER_VERSION_RE = re.compile(r"^v([0-9]+)$")
_KONFIG_LAGER_VERSION_RE = re.compile(r"^v?([0-9]+)$")
_K22_TMP_DIR_RE = re.compile(r"^\.[0-9a-f]{12}-")

# Extra djupförsvar: dessa filer/mappar får aldrig raderas även om de dyker upp oväntat.
_SKYDDADE_FILNAMN: frozenset[str] = frozenset(
    {
        "konto.sqlite",
        "konto.sqlite-wal",
        "konto.sqlite-shm",
        "projekt.json",
        "scenarier.json",
        "lage-idag.json",
        "demo.json",
    }
)
_SKYDDADE_MAPPNAMN: frozenset[str] = frozenset({"konto", "lokal"})
_KATEGORIER: tuple[str, ...] = ("temp", "lager", "korningar", "karna_cache", "s3_lru")


class LokalDiskStadareFel(Exception):
    """Fel vid konfiguration eller säkerhetskontroll för lokal diskstädning."""


def _tolka_lager_version(varde: str) -> int:
    text = varde.strip()
    traff = _KONFIG_LAGER_VERSION_RE.match(text)
    if traff is None:
        raise LokalDiskStadareFel(
            f"Ogiltig aktuell_lager_version '{varde}': förväntade t.ex. 'v12' eller '12'."
        )
    return int(traff.group(1))


@dataclass(frozen=True)
class LokalDiskKonfig:
    """Konfiguration för lokal diskstädning utan S3."""

    cache_rot: Path
    projekt_rot: Path | None = None
    demo_rot: Path | None = None
    max_gb: float = STANDARD_MAX_GB
    mal_andel: float = STANDARD_MAL_ANDEL
    max_alder_dagar: float = STANDARD_MAX_ALDER_DAGAR
    grace_minuter: float = STANDARD_GRACE_MINUTER
    aktuell_lager_version: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_gb) or self.max_gb <= 0.0:
            raise LokalDiskStadareFel(f"max_gb måste vara > 0 och ändligt, fick {self.max_gb}.")
        if not math.isfinite(self.mal_andel) or not (0.0 < self.mal_andel <= 1.0):
            raise LokalDiskStadareFel(
                f"mal_andel måste vara i intervallet (0, 1], fick {self.mal_andel}."
            )
        if not math.isfinite(self.max_alder_dagar) or self.max_alder_dagar <= 0.0:
            raise LokalDiskStadareFel(
                f"max_alder_dagar måste vara > 0 och ändligt, fick {self.max_alder_dagar}."
            )
        if not math.isfinite(self.grace_minuter) or self.grace_minuter < 0.0:
            raise LokalDiskStadareFel(
                f"grace_minuter måste vara >= 0 och ändligt, fick {self.grace_minuter}."
            )
        if self.aktuell_lager_version is not None:
            _tolka_lager_version(self.aktuell_lager_version)

    @classmethod
    def fran_miljo(
        cls,
        cache_rot: str | Path = Path("data/cache"),
        *,
        projekt_rot: str | Path | None = None,
        demo_rot: str | Path | None = None,
        aktuell_lager_version: str | None = None,
        miljo: Mapping[str, str] | None = None,
    ) -> LokalDiskKonfig:
        """Skapar `LokalDiskKonfig` från miljövariablerna `FLODEN_LOKAL_DISK_*`."""
        return las_lokal_disk_konfig(
            None,
            miljo=miljo,
            cache_rot=Path(cache_rot),
            projekt_rot=Path(projekt_rot) if projekt_rot is not None else None,
            demo_rot=Path(demo_rot) if demo_rot is not None else None,
            aktuell_lager_version=aktuell_lager_version,
            anvand_standard_rotter=False,
        )


@dataclass(frozen=True)
class DiskStadaResultat:
    """PII-fritt resultat från `stada_lokal_disk`."""

    torrkor: bool
    fore_bytes: int
    efter_bytes: int
    frigjorda_bytes: int
    antal_raderade: int
    antal_skyddade: int
    per_kategori: dict[str, dict[str, int]]
    varningar: tuple[str, ...]

    @property
    def fore_mb(self) -> float:
        return self.fore_bytes / BYTES_PER_MB

    @property
    def efter_mb(self) -> float:
        return self.efter_bytes / BYTES_PER_MB

    @property
    def frigjorda_mb(self) -> float:
        return self.frigjorda_bytes / BYTES_PER_MB

    def som_dict(self) -> dict[str, Any]:
        """Returnerar JSON-serialiserbar, PII-fri sammanfattning."""
        return {
            "torrkor": self.torrkor,
            "fore_bytes": self.fore_bytes,
            "efter_bytes": self.efter_bytes,
            "frigjorda_bytes": self.frigjorda_bytes,
            "fore_mb": round(self.fore_mb, 3),
            "efter_mb": round(self.efter_mb, 3),
            "frigjorda_mb": round(self.frigjorda_mb, 3),
            "antal_raderade": self.antal_raderade,
            "antal_skyddade": self.antal_skyddade,
            "per_kategori": {k: dict(v) for k, v in self.per_kategori.items()},
            "varningar": list(self.varningar),
        }


@dataclass(frozen=True)
class _Kandidat:
    sokvag: Path
    kategori: str
    storlek_bytes: int
    mtime: float
    nyckel: str


def las_lokal_disk_konfig(
    konfig: Mapping[str, Any] | None = None,
    *,
    miljo: Mapping[str, str] | None = None,
    cache_rot: Path | None = None,
    projekt_rot: Path | None = None,
    demo_rot: Path | None = None,
    max_gb: float | None = None,
    mal_andel: float | None = None,
    max_alder_dagar: float | None = None,
    grace_minuter: float | None = None,
    aktuell_lager_version: str | None = None,
    anvand_standard_rotter: bool = True,
) -> LokalDiskKonfig:
    """Läser konfiguration för lokal diskstädning ur argument, miljövariabler och `config.toml`.

    Prioritetsordning: uttryckliga argument > miljövariabler (`FLODEN_LOKAL_DISK_*`) >
    `[lagring.lokal_disk]` > befintliga `[sokvagar]`/`[webb]`-sökvägar > standardvärden.
    """
    env = os.environ if miljo is None else miljo
    lagring = konfig.get("lagring", {}) if konfig is not None else {}
    sektion = lagring.get("lokal_disk", {}) if isinstance(lagring, Mapping) else {}
    if not isinstance(sektion, Mapping):
        raise LokalDiskStadareFel("[lagring.lokal_disk] måste vara ett TOML-objekt.")
    sokvagar = konfig.get("sokvagar", {}) if konfig is not None else {}
    webb = konfig.get("webb", {}) if konfig is not None else {}

    def _env_eller_sektion(env_namn: str, nyckel: str, standard: Any) -> Any:
        fran_env = env.get(env_namn)
        if fran_env is not None and fran_env.strip() != "":
            return fran_env.strip()
        fran_sektion = sektion.get(nyckel)
        if fran_sektion is not None and str(fran_sektion).strip() != "":
            return fran_sektion
        return standard

    def _float_val(explicit: float | None, env_namn: str, nyckel: str, standard: float) -> float:
        if explicit is not None:
            return float(explicit)
        raw = _env_eller_sektion(env_namn, nyckel, standard)
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise LokalDiskStadareFel(
                f"Ogiltigt tal för {env_namn}/{nyckel}: {raw!r}."
            ) from exc

    if cache_rot is not None:
        vald_cache_rot = Path(cache_rot)
    else:
        default_cache = (
            sokvagar.get("cache", "data/cache") if isinstance(sokvagar, Mapping) else "data/cache"
        )
        vald_cache_rot = Path(
            str(_env_eller_sektion("FLODEN_LOKAL_DISK_CACHE_ROT", "cache_rot", default_cache))
        )

    if projekt_rot is not None:
        vald_projekt_rot: Path | None = Path(projekt_rot)
    else:
        raw_proj = _env_eller_sektion(
            "FLODEN_LOKAL_DISK_PROJEKT_ROT",
            "projekt_rot",
            "data/lokal" if anvand_standard_rotter else None,
        )
        vald_projekt_rot = Path(str(raw_proj)) if raw_proj is not None else None

    if demo_rot is not None:
        vald_demo_rot: Path | None = Path(demo_rot)
    else:
        default_demo = (
            webb.get("demomapp", "data/demo")
            if anvand_standard_rotter and isinstance(webb, Mapping)
            else ("data/demo" if anvand_standard_rotter else None)
        )
        raw_demo = _env_eller_sektion("FLODEN_LOKAL_DISK_DEMO_ROT", "demo_rot", default_demo)
        vald_demo_rot = Path(str(raw_demo)) if raw_demo is not None else None

    if aktuell_lager_version is not None:
        vald_lager_version: str | None = aktuell_lager_version
    else:
        raw_lv = _env_eller_sektion(
            "FLODEN_LOKAL_DISK_LAGER_VERSION", "aktuell_lager_version", None
        )
        vald_lager_version = str(raw_lv).strip() if raw_lv is not None else None

    return LokalDiskKonfig(
        cache_rot=vald_cache_rot,
        projekt_rot=vald_projekt_rot,
        demo_rot=vald_demo_rot,
        max_gb=_float_val(max_gb, "FLODEN_LOKAL_DISK_MAX_GB", "max_gb", STANDARD_MAX_GB),
        mal_andel=_float_val(
            mal_andel, "FLODEN_LOKAL_DISK_MAL_ANDEL", "mal_andel", STANDARD_MAL_ANDEL
        ),
        max_alder_dagar=_float_val(
            max_alder_dagar,
            "FLODEN_LOKAL_DISK_MAX_ALDER_DAGAR",
            "max_alder_dagar",
            STANDARD_MAX_ALDER_DAGAR,
        ),
        grace_minuter=_float_val(
            grace_minuter,
            "FLODEN_LOKAL_DISK_GRACE_MIN",
            "grace_minuter",
            STANDARD_GRACE_MINUTER,
        ),
        aktuell_lager_version=vald_lager_version,
    )


def _validera_cache_rot(cache_rot: Path) -> Path | None:
    """Validerar `cache_rot` utan att följa symlänkar eller tillåta skyddade datamappar.

    Returnerar resolved `Path` om katalogen finns, annars `None`.
    """
    if cache_rot.is_symlink():
        raise LokalDiskStadareFel("Cache-roten får inte vara en symlänk.")
    if not cache_rot.exists():
        return None
    if not cache_rot.is_dir():
        raise LokalDiskStadareFel("Cache-roten är inte en katalog.")
    rot = cache_rot.resolve()
    if rot.name in _SKYDDADE_MAPPNAMN:
        raise LokalDiskStadareFel(
            f"Vägrar städa skyddad katalog '{rot.name}': ange en riktig cache-rot."
        )
    for skyddad_fil in ("konto.sqlite", "projekt.json", "scenarier.json"):
        if (rot / skyddad_fil).exists():
            raise LokalDiskStadareFel(
                f"Vägrar städa katalog som innehåller skyddad fil '{skyddad_fil}'."
            )
    return rot


def _har_symlank_i_kedja(sokvag: Path, rot: Path) -> bool:
    """Sant om `sokvag` eller någon föräldramapp upp till `rot` är en symlänk."""
    aktuell = sokvag
    while True:
        if aktuell.is_symlink():
            return True
        if aktuell == rot or aktuell.parent == aktuell:
            return False
        aktuell = aktuell.parent


def _ar_saker_inom_rot(kandidat: Path, rot: Path) -> bool:
    """Strikt säkerhetskontroll: ingen symlänk i sökvägen och resolved ligger inuti `rot`."""
    if kandidat.is_symlink() or _har_symlank_i_kedja(kandidat, rot):
        return False
    try:
        resolved = kandidat.resolve()
    except OSError:
        return False
    return resolved != rot and resolved.is_relative_to(rot)


def _ar_skyddat_namn(kandidat: Path) -> bool:
    return kandidat.name in _SKYDDADE_FILNAMN or kandidat.name in _SKYDDADE_MAPPNAMN


def _ar_inom_exkluderad(sokvag: Path, exkluderade: set[Path]) -> bool:
    if not exkluderade:
        return False
    aktuell = sokvag
    while True:
        if aktuell in exkluderade:
            return True
        if aktuell.parent == aktuell:
            return False
        aktuell = aktuell.parent


def _mat_trad(
    sokvag: Path,
    rot: Path,
    *,
    exkluderade: set[Path] | None = None,
) -> tuple[int, float, bool]:
    """Mäter storlek, senaste innehålls-mtime och ev. symlänk utan att följa länkar.

    För en icke-tom katalog används senaste mtime bland ingående filer och tomma undermappar så att
    pågående skrivningar i underkataloger alltid skyddas, samtidigt som katalogens egen skapandetid
    inte maskerar äldre filers mtime.
    """
    exkl = exkluderade or set()
    if not _ar_saker_inom_rot(sokvag, rot):
        return 0, 0.0, sokvag.is_symlink()
    try:
        st_rot = sokvag.lstat()
    except OSError:
        return 0, 0.0, False
    if not sokvag.is_dir():
        return int(st_rot.st_size), float(st_rot.st_mtime), False

    total_bytes = 0
    senaste_fil_mtime: float | None = None
    har_symlank = False

    for aktuell_str, mappar, filer in os.walk(sokvag, topdown=True, followlinks=False):
        aktuell = Path(aktuell_str)
        if aktuell != sokvag and _ar_inom_exkluderad(aktuell, exkl):
            mappar[:] = []
            continue

        sakra_mappar: list[str] = []
        for m in mappar:
            m_stig = aktuell / m
            if m_stig.is_symlink():
                har_symlank = True
                continue
            if _ar_inom_exkluderad(m_stig, exkl):
                continue
            if not _ar_saker_inom_rot(m_stig, rot):
                har_symlank = True
                continue
            sakra_mappar.append(m)
        mappar[:] = sakra_mappar

        har_nagon_post = bool(sakra_mappar)
        for filnamn in filer:
            f_stig = aktuell / filnamn
            if f_stig.is_symlink():
                har_symlank = True
                continue
            if _ar_inom_exkluderad(f_stig, exkl):
                continue
            if not _ar_saker_inom_rot(f_stig, rot):
                har_symlank = True
                continue
            try:
                st = f_stig.lstat()
            except OSError:
                continue
            har_nagon_post = True
            total_bytes += int(st.st_size)
            mt = float(st.st_mtime)
            if senaste_fil_mtime is None or mt > senaste_fil_mtime:
                senaste_fil_mtime = mt

        if not har_nagon_post and aktuell != sokvag:
            try:
                mt_dir = float(aktuell.lstat().st_mtime)
                if senaste_fil_mtime is None or mt_dir > senaste_fil_mtime:
                    senaste_fil_mtime = mt_dir
            except OSError:
                pass

    senaste = float(st_rot.st_mtime) if senaste_fil_mtime is None else senaste_fil_mtime
    return total_bytes, senaste, har_symlank


def _rakna_total_bytes(rot: Path) -> int:
    """Summerar storleken i bytes av alla vanliga icke-symlänkade filer strikt under `rot`."""
    total = 0
    for aktuell_str, mappar, filer in os.walk(rot, topdown=True, followlinks=False):
        aktuell = Path(aktuell_str)
        mappar[:] = [m for m in mappar if _ar_saker_inom_rot(aktuell / m, rot)]
        for filnamn in filer:
            fil = aktuell / filnamn
            if not _ar_saker_inom_rot(fil, rot):
                continue
            try:
                total += int(fil.lstat().st_size)
            except OSError:
                continue
    return total


def _extrahera_korning_ids(data: Any) -> set[str]:
    """Extraherar rekursivt alla `korning_id` (och `korning_ids`) ur ett JSON-objekt."""
    hittade: set[str] = set()
    stack: list[Any] = [data]
    while stack:
        aktuell = stack.pop()
        if isinstance(aktuell, Mapping):
            for nyckel, varde in aktuell.items():
                if nyckel == "korning_id" and isinstance(varde, str):
                    ren = varde.strip()
                    if ren:
                        hittade.add(ren)
                elif nyckel == "korning_ids" and isinstance(varde, list):
                    for post in varde:
                        if isinstance(post, str) and post.strip():
                            hittade.add(post.strip())
                elif isinstance(varde, Mapping | list):
                    stack.append(varde)
        elif isinstance(aktuell, list):
            for post in aktuell:
                if isinstance(post, Mapping | list):
                    stack.append(post)
    return hittade


def _las_json_sakert(
    fil: Path,
    rot: Path,
    etikett: str,
    varningar: list[str],
) -> Any | None:
    """Läser JSON utan att följa symlänkar eller krascha vid trasig JSON."""
    if fil.is_symlink() or _har_symlank_i_kedja(fil, rot):
        varningar.append(f"Hoppar över symlänkad JSON-fil ({etikett}).")
        return None
    try:
        resolved = fil.resolve()
    except OSError:
        varningar.append(f"Kunde inte lösa sökväg för JSON-fil ({etikett}).")
        return None
    if not resolved.is_relative_to(rot):
        varningar.append(f"Hoppar över JSON-fil utanför rot ({etikett}).")
        return None
    try:
        raw = fil.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError):
        varningar.append(f"Ogiltig eller oläsbar JSON i {etikett}; lämnas orörd.")
        return None


def _samla_projekt_json_filer(projekt_rot: Path) -> list[tuple[Path, str]]:
    """Hittar `lage-idag.json`, `scenarier.json` och `scenarier/*.json` på djup 0–2."""
    if projekt_rot.is_symlink() or not projekt_rot.is_dir():
        return []
    rot = projekt_rot.resolve()
    filer: list[tuple[Path, str]] = []

    def _samla_i_mapp(mapp: Path) -> None:
        for namn in ("lage-idag.json", "scenarier.json"):
            f = mapp / namn
            if not f.is_symlink() and f.is_file():
                filer.append((f, namn))
        sc_mapp = mapp / "scenarier"
        if not sc_mapp.is_symlink() and sc_mapp.is_dir():
            try:
                poster = sorted(sc_mapp.iterdir(), key=lambda p: p.name)
            except OSError:
                return
            for p in poster:
                if not p.is_symlink() and p.is_file() and p.suffix == ".json":
                    filer.append((p, "scenarier/*.json"))

    def _undermappar(mapp: Path) -> list[Path]:
        try:
            poster = sorted(mapp.iterdir(), key=lambda p: p.name)
        except OSError:
            return []
        return [
            p
            for p in poster
            if not p.is_symlink()
            and p.is_dir()
            and not p.name.startswith((".", "_"))
            and _ar_saker_inom_rot(p, rot)
        ]

    _samla_i_mapp(rot)
    for niva1 in _undermappar(rot):
        _samla_i_mapp(niva1)
        for niva2 in _undermappar(niva1):
            _samla_i_mapp(niva2)

    return filer


def samla_skyddade_korningar(
    konfig: LokalDiskKonfig,
    *,
    nu_ts: float | None = None,
    varningar: list[str] | None = None,
) -> frozenset[str]:
    """Bygger atomisk skyddslista med alla `korning_id` som aldrig får raderas.

    Scannar i ordning:
    1. `webb/korningar/*/korning.json` med `status in ("ko", "kor")` samt mappar ändrade inom
       `grace_minuter`.
    2. `demo_rot/varld/demo.json` och `demo_rot/demo.json`.
    3. `lage-idag.json`, `scenarier.json` och `scenarier/*.json` under `projekt_rot`
       (inklusive `projekt/*/` och `kartverktyg/*/`).
    """
    nu = time.time() if nu_ts is None else float(nu_ts)
    grace_s = konfig.grace_minuter * 60.0
    varn = varningar if varningar is not None else []
    skyddade: set[str] = set()

    # 1. Pågående/köade körningar och körningar inom grace_minuter
    rot = _validera_cache_rot(konfig.cache_rot)
    if rot is not None:
        korningsmapp = rot / "webb" / "korningar"
        if korningsmapp.is_symlink():
            varn.append("Hoppar över symlänkad webb/korningar-katalog.")
        elif korningsmapp.is_dir() and _ar_saker_inom_rot(korningsmapp, rot):
            try:
                poster = sorted(korningsmapp.iterdir(), key=lambda p: p.name)
            except OSError:
                poster = []
            for mapp in poster:
                if mapp.is_symlink():
                    continue
                if not mapp.is_dir() or not _ar_saker_inom_rot(mapp, rot):
                    continue
                # Kontrollera grace-tid för hela körningsmappen
                _, senaste_mtime, _ = _mat_trad(mapp, rot)
                if (nu - senaste_mtime) <= grace_s:
                    skyddade.add(mapp.name)

                kfil = mapp / "korning.json"
                if kfil.is_symlink():
                    varn.append(
                        f"Hoppar över symlänkad korning.json för körning '{mapp.name}'."
                    )
                    continue
                if kfil.is_file():
                    data = _las_json_sakert(kfil, rot, f"korning.json ({mapp.name})", varn)
                    if isinstance(data, Mapping):
                        status = data.get("status")
                        kid = data.get("korning_id")
                        if status in _AKTIVA_KORNING_STATUSER:
                            skyddade.add(mapp.name)
                            if isinstance(kid, str) and kid.strip():
                                skyddade.add(kid.strip())
                    elif data is not None:
                        varn.append(
                            f"Ogiltig struktur i korning.json ({mapp.name}); "
                            "förväntade JSON-objekt."
                        )

    # 2. Demokörningar (demo_rot/varld/demo.json eller demo_rot/demo.json)
    if konfig.demo_rot is not None:
        if konfig.demo_rot.is_symlink():
            varn.append("Hoppar över symlänkad demo_rot.")
        elif konfig.demo_rot.is_dir():
            demo_rot_res = konfig.demo_rot.resolve()
            for kandidat in (demo_rot_res / "varld" / "demo.json", demo_rot_res / "demo.json"):
                if kandidat.is_symlink():
                    varn.append("Hoppar över symlänkad demo.json.")
                elif kandidat.is_file():
                    data = _las_json_sakert(kandidat, demo_rot_res, "demo.json", varn)
                    if data is not None:
                        skyddade.update(_extrahera_korning_ids(data))

    # 3. Aktiva projekts lage-idag.json, scenarier.json och kartverktyg/*/scenarier/*.json
    if konfig.projekt_rot is not None:
        if konfig.projekt_rot.is_symlink():
            varn.append("Hoppar över symlänkad projekt_rot.")
        elif konfig.projekt_rot.is_dir():
            proj_rot_res = konfig.projekt_rot.resolve()
            for jfil, etikett in _samla_projekt_json_filer(proj_rot_res):
                data = _las_json_sakert(jfil, proj_rot_res, etikett, varn)
                if data is not None:
                    skyddade.update(_extrahera_korning_ids(data))

    return frozenset(skyddade)


def _ar_tempnamn(namn: str, foralder: Path, rot: Path) -> bool:
    if namn.startswith((".floden_", "tmp_")):
        return True
    # K22 ResultatCache skapar tempkataloger `.<12 hex>-*` under karna/{jobb,scenarier}/<nn>/
    if _K22_TMP_DIR_RE.match(namn):
        try:
            rel = foralder.relative_to(rot).parts
        except ValueError:
            return False
        if len(rel) == 3 and rel[0] == "karna" and rel[1] in ("jobb", "scenarier"):
            return True
    return False


def _radera_sakert(kandidat: Path, rot: Path, varningar: list[str]) -> int:
    """Raderar fil eller katalog strikt under `rot` utan att någonsin följa/radera symlänkar.

    Returnerar antal faktiskt frigjorda bytes.
    """
    if _ar_skyddat_namn(kandidat):
        varningar.append(f"Vägrade radera skyddat namn '{kandidat.name}'.")
        return 0
    if not _ar_saker_inom_rot(kandidat, rot):
        varningar.append("Hoppar över kandidat utanför cache_rot eller via symlänk.")
        return 0
    if not kandidat.exists():
        return 0

    if not kandidat.is_dir():
        try:
            storlek = int(kandidat.lstat().st_size)
            kandidat.unlink(missing_ok=True)
            return storlek
        except OSError:
            varningar.append("Kunde inte radera en cachefil (OSError).")
            return 0

    frigjorda = 0
    for aktuell_str, mappar, filer in os.walk(kandidat, topdown=False, followlinks=False):
        aktuell = Path(aktuell_str)
        if not _ar_saker_inom_rot(aktuell, rot):
            continue
        for filnamn in filer:
            f = aktuell / filnamn
            if _ar_skyddat_namn(f):
                varningar.append(f"Hoppar över skyddad fil '{filnamn}' vid radering.")
                continue
            if f.is_symlink():
                varningar.append("Hoppar över symlänk inuti cachekatalog vid radering.")
                continue
            if not _ar_saker_inom_rot(f, rot):
                varningar.append("Hoppar över fil utanför cache_rot vid radering.")
                continue
            try:
                storlek = int(f.lstat().st_size)
                f.unlink(missing_ok=True)
                frigjorda += storlek
            except OSError:
                varningar.append("Kunde inte radera en fil i cachekatalog (OSError).")
        for m in mappar:
            m_stig = aktuell / m
            if m_stig.is_symlink() or _ar_skyddat_namn(m_stig):
                continue
            if _ar_saker_inom_rot(m_stig, rot):
                with contextlib.suppress(OSError):
                    m_stig.rmdir()

    with contextlib.suppress(OSError):
        # Kan misslyckas om en symlänk eller skyddad fil lämnades kvar avsiktligt.
        if _ar_saker_inom_rot(kandidat, rot):
            kandidat.rmdir()
    return frigjorda


def _rensa_tomma_undermappar(bas: Path, rot: Path) -> None:
    """Tar bort tomma underkataloger under `bas` utan att ta bort `bas` själv eller symlänkar."""
    if bas.is_symlink() or not bas.is_dir() or not _ar_saker_inom_rot(bas, rot):
        return
    for aktuell_str, mappar, _filer in os.walk(bas, topdown=False, followlinks=False):
        aktuell = Path(aktuell_str)
        for m in mappar:
            kandidat = aktuell / m
            if kandidat.is_symlink() or _ar_skyddat_namn(kandidat):
                continue
            if _ar_saker_inom_rot(kandidat, rot) and kandidat != bas:
                with contextlib.suppress(OSError):
                    kandidat.rmdir()


def _korning_ar_aktiv_nu(mapp: Path, rot: Path) -> bool:
    """Sista-sekunden-kontroll före radering av `webb/korningar/<id>/` mot racelägen."""
    kfil = mapp / "korning.json"
    if kfil.is_symlink() or not kfil.is_file() or not _ar_saker_inom_rot(kfil, rot):
        return False
    try:
        data = json.loads(kfil.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(data, Mapping) and data.get("status") in _AKTIVA_KORNING_STATUSER


def _korning_id_fran_fil(mapp: Path, rot: Path) -> str | None:
    kfil = mapp / "korning.json"
    if kfil.is_symlink() or not kfil.is_file() or not _ar_saker_inom_rot(kfil, rot):
        return None
    try:
        data = json.loads(kfil.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if isinstance(data, Mapping):
        kid = data.get("korning_id")
        if isinstance(kid, str) and kid.strip():
            return kid.strip()
    return None


def stada_lokal_disk(
    konfig: LokalDiskKonfig,
    *,
    torrkor: bool = False,
    nu_ts: float | None = None,
) -> DiskStadaResultat:
    """Städar lokal cache i fyra säkra spår med atomiskt skydd av aktiva körningar & projekt."""
    per_kat: dict[str, dict[str, int]] = {
        k: {"antal_raderade": 0, "frigjorda_bytes": 0, "antal_skyddade": 0} for k in _KATEGORIER
    }
    varningar: list[str] = []

    rot = _validera_cache_rot(konfig.cache_rot)
    if rot is None:
        return DiskStadaResultat(
            torrkor=torrkor,
            fore_bytes=0,
            efter_bytes=0,
            frigjorda_bytes=0,
            antal_raderade=0,
            antal_skyddade=0,
            per_kategori=per_kat,
            varningar=(),
        )

    nu = time.time() if nu_ts is None else float(nu_ts)
    grace_s = konfig.grace_minuter * 60.0
    alder_grans_s = konfig.max_alder_dagar * 86400.0
    max_bytes = int(konfig.max_gb * BYTES_PER_GB)
    mal_bytes = int(konfig.max_gb * konfig.mal_andel * BYTES_PER_GB)

    # 0. Mät ursprunglig storlek och bygg atomisk skyddslista innan något planeras eller raderas
    fore_bytes = _rakna_total_bytes(rot)
    skyddade_korningar = samla_skyddade_korningar(konfig, nu_ts=nu, varningar=varningar)

    plan_fas1: list[_Kandidat] = []
    planerade_stigar: set[Path] = set()
    lru_lager: list[_Kandidat] = []
    lru_korningar: list[_Kandidat] = []
    lru_karna: list[_Kandidat] = []

    korningsrot = rot / "webb" / "korningar"
    skyddade_korningsmappar: set[Path] = set()
    if not korningsrot.is_symlink() and korningsrot.is_dir():
        try:
            for p in korningsrot.iterdir():
                if p.is_symlink() or not p.is_dir():
                    continue
                kid = _korning_id_fran_fil(p, rot)
                if p.name in skyddade_korningar or (kid is not None and kid in skyddade_korningar):
                    skyddade_korningsmappar.add(p)
        except OSError:
            pass

    # Spår 1: Övergivna tempfiler/mappar (.floden_*, tmp_*, avbrutna K22-tempmappar)
    for aktuell_str, mappar, filer in os.walk(rot, topdown=True, followlinks=False):
        aktuell = Path(aktuell_str)
        if aktuell in skyddade_korningsmappar:
            mappar[:] = []
            continue

        nasta_mappar: list[str] = []
        for m in sorted(mappar):
            m_stig = aktuell / m
            if m_stig.is_symlink():
                if _ar_tempnamn(m, aktuell, rot):
                    varningar.append("Hoppar över symlänkad tempkatalog i cache_rot.")
                continue
            if m_stig in skyddade_korningsmappar:
                continue
            if not _ar_saker_inom_rot(m_stig, rot):
                continue
            if _ar_tempnamn(m, aktuell, rot) and not _ar_skyddat_namn(m_stig):
                storlek, senaste_mt, _ = _mat_trad(m_stig, rot)
                if (nu - senaste_mt) <= grace_s:
                    per_kat["temp"]["antal_skyddade"] += 1
                else:
                    rel = m_stig.relative_to(rot).as_posix()
                    plan_fas1.append(_Kandidat(m_stig, "temp", storlek, senaste_mt, rel))
                    planerade_stigar.add(m_stig)
                continue
            nasta_mappar.append(m)
        mappar[:] = nasta_mappar

        for filnamn in sorted(filer):
            if not _ar_tempnamn(filnamn, aktuell, rot):
                continue
            f_stig = aktuell / filnamn
            if f_stig.is_symlink():
                varningar.append("Hoppar över symlänkad tempfil i cache_rot.")
                continue
            if _ar_skyddat_namn(f_stig) or not _ar_saker_inom_rot(f_stig, rot):
                continue
            storlek, mt, _ = _mat_trad(f_stig, rot)
            if (nu - mt) <= grace_s:
                per_kat["temp"]["antal_skyddade"] += 1
            else:
                rel = f_stig.relative_to(rot).as_posix()
                plan_fas1.append(_Kandidat(f_stig, "temp", storlek, mt, rel))
                planerade_stigar.add(f_stig)

    # Spår 2: Gamla webb/lager/v*-versioner + LRU-kandidater för enskilda lagerfiler
    lagerrot = rot / "webb" / "lager"
    if lagerrot.is_symlink():
        varningar.append("Hoppar över symlänkad webb/lager-katalog.")
    elif lagerrot.is_dir() and _ar_saker_inom_rot(lagerrot, rot):
        version_mappar: list[tuple[int, Path]] = []
        try:
            poster = sorted(lagerrot.iterdir(), key=lambda p: p.name)
        except OSError:
            poster = []
        for p in poster:
            if p in planerade_stigar:
                continue
            if p.is_symlink():
                varningar.append("Hoppar över symlänk i webb/lager.")
                continue
            if p.is_dir() and _ar_saker_inom_rot(p, rot):
                traff = _LAGER_VERSION_RE.match(p.name)
                if traff is not None:
                    version_mappar.append((int(traff.group(1)), p))

        aktuell_v: int | None = (
            _tolka_lager_version(konfig.aktuell_lager_version)
            if konfig.aktuell_lager_version is not None
            else (max(v for v, _ in version_mappar) if version_mappar else None)
        )
        if aktuell_v is not None:
            for v_nr, v_mapp in version_mappar:
                if v_nr < aktuell_v:
                    storlek, senaste_mt, _ = _mat_trad(v_mapp, rot, exkluderade=planerade_stigar)
                    if (nu - senaste_mt) <= grace_s:
                        per_kat["lager"]["antal_skyddade"] += 1
                    else:
                        rel = v_mapp.relative_to(rot).as_posix()
                        plan_fas1.append(_Kandidat(v_mapp, "lager", storlek, senaste_mt, rel))
                        planerade_stigar.add(v_mapp)

        # Samla återstående enskilda lagerfiler för ev. LRU-eviction
        for aktuell_str, mappar, filer in os.walk(lagerrot, topdown=True, followlinks=False):
            aktuell = Path(aktuell_str)
            if _ar_inom_exkluderad(aktuell, planerade_stigar):
                mappar[:] = []
                continue
            sakra_mappar: list[str] = []
            for m in sorted(mappar):
                m_stig = aktuell / m
                if m_stig.is_symlink():
                    varningar.append("Hoppar över symlänkad undermapp i webb/lager.")
                    continue
                if _ar_inom_exkluderad(m_stig, planerade_stigar):
                    continue
                if _ar_saker_inom_rot(m_stig, rot):
                    sakra_mappar.append(m)
            mappar[:] = sakra_mappar

            for filnamn in sorted(filer):
                f_stig = aktuell / filnamn
                if _ar_inom_exkluderad(f_stig, planerade_stigar):
                    continue
                if f_stig.is_symlink():
                    varningar.append("Hoppar över symlänkad fil i webb/lager.")
                    continue
                if _ar_skyddat_namn(f_stig) or not _ar_saker_inom_rot(f_stig, rot):
                    continue
                storlek, mt, _ = _mat_trad(f_stig, rot)
                if (nu - mt) <= grace_s:
                    per_kat["lager"]["antal_skyddade"] += 1
                else:
                    rel = f_stig.relative_to(rot).as_posix()
                    lru_lager.append(_Kandidat(f_stig, "lager", storlek, mt, rel))

    # Spår 3: Avslutade/misslyckade webb/korningar/<id>/
    if not korningsrot.is_symlink() and korningsrot.is_dir() and _ar_saker_inom_rot(
        korningsrot, rot
    ):
        try:
            k_poster = sorted(korningsrot.iterdir(), key=lambda p: p.name)
        except OSError:
            k_poster = []
        for k_mapp in k_poster:
            if k_mapp in planerade_stigar:
                continue
            if k_mapp.is_symlink():
                varningar.append("Hoppar över symlänkad körningskatalog i webb/korningar.")
                continue
            if not k_mapp.is_dir() or not _ar_saker_inom_rot(k_mapp, rot):
                continue
            if _ar_skyddat_namn(k_mapp):
                per_kat["korningar"]["antal_skyddade"] += 1
                continue

            storlek, senaste_mt, _ = _mat_trad(k_mapp, rot, exkluderade=planerade_stigar)
            if k_mapp in skyddade_korningsmappar or (nu - senaste_mt) <= grace_s:
                per_kat["korningar"]["antal_skyddade"] += 1
                continue

            rel = k_mapp.relative_to(rot).as_posix()
            kand = _Kandidat(k_mapp, "korningar", storlek, senaste_mt, rel)
            if (nu - senaste_mt) > alder_grans_s:
                plan_fas1.append(kand)
                planerade_stigar.add(k_mapp)
            else:
                lru_korningar.append(kand)

    # Spår 4: K22 ResultatCache (karna/jobb/<nn>/<nyckel>/ och karna/scenarier/<nn>/<nyckel>/)
    karna_rot = rot / "karna"
    if karna_rot.is_symlink():
        varningar.append("Hoppar över symlänkad karna-katalog.")
    elif karna_rot.is_dir() and _ar_saker_inom_rot(karna_rot, rot):
        for slag in ("jobb", "scenarier"):
            slag_mapp = karna_rot / slag
            if slag_mapp.is_symlink():
                varningar.append(f"Hoppar över symlänkad karna/{slag}-katalog.")
                continue
            if not slag_mapp.is_dir() or not _ar_saker_inom_rot(slag_mapp, rot):
                continue
            try:
                shard_mappar = sorted(slag_mapp.iterdir(), key=lambda p: p.name)
            except OSError:
                shard_mappar = []
            for shard in shard_mappar:
                if shard in planerade_stigar:
                    continue
                if shard.is_symlink():
                    varningar.append(f"Hoppar över symlänkad shard i karna/{slag}.")
                    continue
                if not shard.is_dir() or not _ar_saker_inom_rot(shard, rot):
                    continue
                try:
                    k22_poster = sorted(shard.iterdir(), key=lambda p: p.name)
                except OSError:
                    k22_poster = []
                for post in k22_poster:
                    if post in planerade_stigar:
                        continue
                    if post.is_symlink():
                        varningar.append(f"Hoppar över symlänkad post i karna/{slag}.")
                        continue
                    if not post.is_dir() or not _ar_saker_inom_rot(post, rot):
                        continue
                    if _ar_skyddat_namn(post):
                        per_kat["karna_cache"]["antal_skyddade"] += 1
                        continue

                    storlek, senaste_mt, _ = _mat_trad(post, rot, exkluderade=planerade_stigar)
                    if (nu - senaste_mt) <= grace_s:
                        per_kat["karna_cache"]["antal_skyddade"] += 1
                        continue

                    klar_fil = post / ".klar"
                    ar_komplett = not klar_fil.is_symlink() and klar_fil.is_file()
                    rel = post.relative_to(rot).as_posix()
                    kand = _Kandidat(post, "karna_cache", storlek, senaste_mt, rel)
                    if not ar_komplett or (nu - senaste_mt) > alder_grans_s:
                        plan_fas1.append(kand)
                        planerade_stigar.add(post)
                    else:
                        lru_karna.append(kand)

    # Planera s3_lru via befintliga stada_lokal_cache (torrkor först för samlad budgetberäkning)
    s3_lru_mapp = rot / "s3_lru"
    s3_plan: StadaResultat | None = None
    s3_eff_max_gb = konfig.max_gb
    if s3_lru_mapp.is_symlink():
        varningar.append("Hoppar över symlänkad s3_lru-katalog.")
    elif s3_lru_mapp.is_dir() and _ar_saker_inom_rot(s3_lru_mapp, rot):
        try:
            s3_plan = stada_lokal_cache(
                s3_lru_mapp,
                max_gb=konfig.max_gb,
                max_alder_dagar=konfig.max_alder_dagar,
                torrkor=True,
                nu_ts=nu,
            )
        except LokalCacheFel as exc:
            varningar.append(f"Kunde inte planera s3_lru-städning: {exc}")
            s3_plan = None

    # Beräkna återstående bytes efter fas 1 (temp, gamla lagerversioner, åldersgräns, s3_lru)
    aktuell_bytes = fore_bytes - sum(k.storlek_bytes for k in plan_fas1)
    if s3_plan is not None:
        aktuell_bytes -= s3_plan.frigjorda_bytes

    # Fas 2: LRU-eviction om total storlek fortfarande > max_gb (ned till <= max_gb * mal_andel)
    plan_fas2: list[_Kandidat] = []
    if aktuell_bytes > max_bytes:
        for pool in (lru_lager, lru_korningar, lru_karna):
            if aktuell_bytes <= mal_bytes:
                break
            for kand in sorted(pool, key=lambda k: (k.mtime, k.nyckel)):
                if aktuell_bytes <= mal_bytes:
                    break
                plan_fas2.append(kand)
                planerade_stigar.add(kand.sokvag)
                aktuell_bytes -= kand.storlek_bytes

        # Om total storlek fortfarande överstiger mal_bytes och s3_lru har kvarvarande filer:
        # sänk effektiv s3_lru-gräns så även s3_lru bidrar ned till mal_bytes.
        if aktuell_bytes > mal_bytes and s3_plan is not None and s3_plan.kvarvarande_bytes > 0:
            over_bytes = aktuell_bytes - mal_bytes
            onskat_s3_kvar = max(0, s3_plan.kvarvarande_bytes - over_bytes)
            if onskat_s3_kvar <= 0:
                s3_eff_max_gb = 1e-9
            else:
                kandidat_gb = onskat_s3_kvar / (0.85 * BYTES_PER_GB)
                trigger_gb = (
                    (s3_plan.kvarvarande_bytes - 1) / BYTES_PER_GB
                    if s3_plan.kvarvarande_bytes > 1
                    else 1e-9
                )
                s3_eff_max_gb = min(konfig.max_gb, max(1e-9, min(kandidat_gb, trigger_gb)))
            try:
                ny_s3_plan = stada_lokal_cache(
                    s3_lru_mapp,
                    max_gb=s3_eff_max_gb,
                    max_alder_dagar=konfig.max_alder_dagar,
                    torrkor=True,
                    nu_ts=nu,
                )
                aktuell_bytes += s3_plan.frigjorda_bytes
                s3_plan = ny_s3_plan
                aktuell_bytes -= s3_plan.frigjorda_bytes
            except LokalCacheFel as exc:
                varningar.append(f"Kunde inte justera s3_lru-budget: {exc}")

    # Fas 3: Utför eller rapportera torrkor
    alla_kandidater = [*plan_fas1, *plan_fas2]
    if torrkor:
        for kand in alla_kandidater:
            per_kat[kand.kategori]["antal_raderade"] += 1
            per_kat[kand.kategori]["frigjorda_bytes"] += kand.storlek_bytes
        if s3_plan is not None:
            per_kat["s3_lru"]["antal_raderade"] += s3_plan.antal_raderade
            per_kat["s3_lru"]["frigjorda_bytes"] += s3_plan.frigjorda_bytes
    else:
        for kand in alla_kandidater:
            if kand.kategori == "korningar" and _korning_ar_aktiv_nu(kand.sokvag, rot):
                per_kat["korningar"]["antal_skyddade"] += 1
                varningar.append(
                    "Avbröt radering av körning som blev aktiv under pågående städning."
                )
                continue
            frigjort = _radera_sakert(kand.sokvag, rot, varningar)
            per_kat[kand.kategori]["antal_raderade"] += 1
            per_kat[kand.kategori]["frigjorda_bytes"] += frigjort

        if s3_plan is not None:
            try:
                s3_utfort = stada_lokal_cache(
                    s3_lru_mapp,
                    max_gb=s3_eff_max_gb,
                    max_alder_dagar=konfig.max_alder_dagar,
                    torrkor=False,
                    nu_ts=nu,
                )
                per_kat["s3_lru"]["antal_raderade"] += s3_utfort.antal_raderade
                per_kat["s3_lru"]["frigjorda_bytes"] += s3_utfort.frigjorda_bytes
            except LokalCacheFel as exc:
                varningar.append(f"Fel vid s3_lru-städning: {exc}")

        # Städa tomma underkataloger i lager och K22-shards
        _rensa_tomma_undermappar(lagerrot, rot)
        _rensa_tomma_undermappar(karna_rot / "jobb", rot)
        _rensa_tomma_undermappar(karna_rot / "scenarier", rot)

    total_frigjort = sum(v["frigjorda_bytes"] for v in per_kat.values())
    total_raderade = sum(v["antal_raderade"] for v in per_kat.values())
    total_skyddade = sum(v["antal_skyddade"] for v in per_kat.values())
    efter_bytes = max(0, fore_bytes - total_frigjort) if torrkor else _rakna_total_bytes(rot)

    if efter_bytes > max_bytes:
        varningar.append(
            "Total cachestorlek överstiger fortfarande max_gb efter städning "
            "(skyddade körningar, grace-tid eller icke-städade filer upptar utrymmet)."
        )

    return DiskStadaResultat(
        torrkor=torrkor,
        fore_bytes=fore_bytes,
        efter_bytes=efter_bytes,
        frigjorda_bytes=total_frigjort,
        antal_raderade=total_raderade,
        antal_skyddade=total_skyddade,
        per_kategori=per_kat,
        varningar=tuple(varningar),
    )


def disk_utrymme_status(cache_rot: Path) -> dict[str, float]:
    """Returnerar PII-fri status över diskutrymmet vid `cache_rot`.

    Nycklar: `ledig_gb`, `total_gb`, `anvand_pct`, `cache_gb`.
    """
    if cache_rot.is_symlink():
        raise LokalDiskStadareFel("Cache-roten får inte vara en symlänk.")

    if cache_rot.exists():
        if not cache_rot.is_dir():
            raise LokalDiskStadareFel("Cache-roten är inte en katalog.")
        rot = cache_rot.resolve()
        disk_sokvag = rot
        cache_bytes = _rakna_total_bytes(rot)
    else:
        cache_bytes = 0
        kandidat = cache_rot.resolve(strict=False)
        while not kandidat.exists() and kandidat.parent != kandidat:
            kandidat = kandidat.parent
        disk_sokvag = kandidat

    usage = shutil.disk_usage(disk_sokvag)
    anvand_pct = (usage.used / usage.total) * 100.0 if usage.total > 0 else 0.0
    return {
        "ledig_gb": float(usage.free / BYTES_PER_GB),
        "total_gb": float(usage.total / BYTES_PER_GB),
        "anvand_pct": float(anvand_pct),
        "cache_gb": float(cache_bytes / BYTES_PER_GB),
    }
