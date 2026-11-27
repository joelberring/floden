# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatisk in-container diskstädare (bakgrundstråd utan krav på systemd eller S3).

Kör `gemensamt.lokal_diskstadare.stada_lokal_disk` periodiskt i en lätt bakgrundstråd så att
lokal cache under `data/cache/` hålls inom konfigurerad budget även när servern körs som ensam
container utan extern systemd-timer.

Styrs via miljövariabler:
- `FLODEN_AUTO_STADA_DISK`: `"0"` (standard i ren Python/tester) eller `"1"` (aktiv).
- `FLODEN_AUTO_STADA_INTERVALL_TIMMAR`: städintervall i timmar (standard `24.0`, måste vara > 0).
- `FLODEN_AUTO_STADA_VID_START`: `"0"` (standard) eller `"1"` (kör ett första svep efter kort
  fördröjning vid start).

Säkerhetsinvarianter:
- `stoppa()` väcker `threading.Event` och returnerar omedelbart utan att hänga container-shutdown.
- Fel under ett städsvep (t.ex. tillfälligt I/O-fel) loggas PII-fritt men kraschar aldrig
  webbservern eller avslutar bakgrundstråden.
- Skyddar alltid aktiva körningar, demovärld och projekt via `lokal_diskstadare`.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from gemensamt.konfig import KonfigFel, las_konfig
from gemensamt.lokal_diskstadare import (
    DiskStadaResultat,
    LokalDiskKonfig,
    LokalDiskStadareFel,
    las_lokal_disk_konfig,
    stada_lokal_disk,
)

_logg = logging.getLogger(__name__)

FLODEN_AUTO_STADA_DISK_ENV: Final[str] = "FLODEN_AUTO_STADA_DISK"
FLODEN_AUTO_STADA_INTERVALL_ENV: Final[str] = "FLODEN_AUTO_STADA_INTERVALL_TIMMAR"
FLODEN_AUTO_STADA_VID_START_ENV: Final[str] = "FLODEN_AUTO_STADA_VID_START"

STANDARD_INTERVALL_TIMMAR: Final[float] = 24.0
STANDARD_START_FORDROJNING_S: Final[float] = 30.0

_SANNA_VARDEN: Final[frozenset[str]] = frozenset({"1", "true", "ja", "pa", "yes", "on"})
_FALSKA_VARDEN: Final[frozenset[str]] = frozenset({"0", "false", "nej", "av", "no", "off", ""})
_EPOST_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)


class AutoStadareFel(Exception):
    """Konfigurationsfel för `AutoDiskStadare`."""


def _tolka_bool_env(env: Mapping[str, str], namn: str, standard: bool) -> bool:
    raw = env.get(namn)
    if raw is None:
        return standard
    norm = raw.strip().lower()
    if norm in _SANNA_VARDEN:
        return True
    if norm in _FALSKA_VARDEN:
        return False
    raise AutoStadareFel(
        f"Ogiltigt booleskt värde i {namn}={raw!r}: förväntade 0/1 (eller av/pa)."
    )


def _tolka_positiv_float_env(env: Mapping[str, str], namn: str, standard: float) -> float:
    raw = env.get(namn)
    if raw is None or not raw.strip():
        return standard
    try:
        varde = float(raw.strip())
    except ValueError as exc:
        raise AutoStadareFel(
            f"Ogiltigt tal i {namn}={raw!r}: ange ett positivt tal."
        ) from exc
    if not math.isfinite(varde) or varde <= 0.0:
        raise AutoStadareFel(f"Ogiltigt värde i {namn}={raw!r}: måste vara ändligt och > 0.")
    return varde


def _sanera_loggtext(exc: BaseException) -> str:
    """Returnerar kort, PII-fri enradsbeskrivning av ett undantag."""
    text = f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ").strip()
    text = _EPOST_RE.sub("[redakterad-epost]", text)
    return text[:300]


@dataclass(frozen=True)
class AutoStadareKonfig:
    """Konfiguration för `AutoDiskStadare`."""

    disk_konfig: LokalDiskKonfig
    aktiv: bool = False
    intervall_timmar: float = STANDARD_INTERVALL_TIMMAR
    vid_start: bool = False
    start_fordrojning_s: float = STANDARD_START_FORDROJNING_S

    def __post_init__(self) -> None:
        if not math.isfinite(self.intervall_timmar) or self.intervall_timmar <= 0.0:
            raise AutoStadareFel(
                f"intervall_timmar måste vara ändligt och > 0, fick {self.intervall_timmar!r}."
            )
        if not math.isfinite(self.start_fordrojning_s) or self.start_fordrojning_s < 0.0:
            raise AutoStadareFel(
                "start_fordrojning_s måste vara ändligt och >= 0, "
                f"fick {self.start_fordrojning_s!r}."
            )

    @property
    def intervall_s(self) -> float:
        return self.intervall_timmar * 3600.0


def _gor_absolut_mot_rot(sokvag: Path | None, rot: Path | None) -> Path | None:
    if sokvag is None or rot is None or sokvag.is_absolute():
        return sokvag
    return rot / sokvag


def las_auto_stadare_konfig(
    konfig: Mapping[str, Any] | None = None,
    *,
    miljo: Mapping[str, str] | None = None,
    bas_rot: Path | None = None,
    cache_rot: Path | None = None,
    projekt_rot: Path | None = None,
    demo_rot: Path | None = None,
    aktuell_lager_version: str | None = None,
    start_fordrojning_s: float = STANDARD_START_FORDROJNING_S,
) -> AutoStadareKonfig:
    """Läser konfiguration för `AutoDiskStadare` ur miljövariabler och valfri `config.toml`."""
    env = os.environ if miljo is None else miljo
    aktiv = _tolka_bool_env(env, FLODEN_AUTO_STADA_DISK_ENV, standard=False)
    intervall_timmar = _tolka_positiv_float_env(
        env, FLODEN_AUTO_STADA_INTERVALL_ENV, standard=STANDARD_INTERVALL_TIMMAR
    )
    vid_start = _tolka_bool_env(env, FLODEN_AUTO_STADA_VID_START_ENV, standard=False)

    try:
        disk_konfig = las_lokal_disk_konfig(
            konfig,
            miljo=env,
            cache_rot=cache_rot,
            projekt_rot=projekt_rot,
            demo_rot=demo_rot,
            aktuell_lager_version=aktuell_lager_version,
        )
    except LokalDiskStadareFel as exc:
        raise AutoStadareFel(str(exc)) from exc

    if bas_rot is not None:
        rot = bas_rot.resolve()
        disk_konfig = LokalDiskKonfig(
            cache_rot=_gor_absolut_mot_rot(disk_konfig.cache_rot, rot) or disk_konfig.cache_rot,
            projekt_rot=_gor_absolut_mot_rot(disk_konfig.projekt_rot, rot),
            demo_rot=_gor_absolut_mot_rot(disk_konfig.demo_rot, rot),
            max_gb=disk_konfig.max_gb,
            mal_andel=disk_konfig.mal_andel,
            max_alder_dagar=disk_konfig.max_alder_dagar,
            grace_minuter=disk_konfig.grace_minuter,
            aktuell_lager_version=disk_konfig.aktuell_lager_version,
        )

    return AutoStadareKonfig(
        disk_konfig=disk_konfig,
        aktiv=aktiv,
        intervall_timmar=intervall_timmar,
        vid_start=vid_start,
        start_fordrojning_s=start_fordrojning_s,
    )


class AutoDiskStadare:
    """Lätt bakgrundstråd som periodiskt anropar `stada_lokal_disk` med fel-isolering."""

    def __init__(
        self,
        konfig: LokalDiskKonfig | AutoStadareKonfig,
        *,
        intervall_s: float | None = None,
        vid_start: bool | None = None,
        start_fordrojning_s: float | None = None,
        stadare_fn: Callable[[LokalDiskKonfig], DiskStadaResultat] | None = None,
        pa_resultat: Callable[[DiskStadaResultat], None] | None = None,
        pa_fel: Callable[[Exception], None] | None = None,
    ) -> None:
        if isinstance(konfig, AutoStadareKonfig):
            self._disk_konfig = konfig.disk_konfig
            eff_intervall_s = (
                float(intervall_s) if intervall_s is not None else konfig.intervall_s
            )
            eff_vid_start = konfig.vid_start if vid_start is None else bool(vid_start)
            eff_fordrojning_s = (
                konfig.start_fordrojning_s
                if start_fordrojning_s is None
                else float(start_fordrojning_s)
            )
        else:
            self._disk_konfig = konfig
            eff_intervall_s = (
                float(intervall_s)
                if intervall_s is not None
                else STANDARD_INTERVALL_TIMMAR * 3600.0
            )
            eff_vid_start = False if vid_start is None else bool(vid_start)
            eff_fordrojning_s = (
                STANDARD_START_FORDROJNING_S
                if start_fordrojning_s is None
                else float(start_fordrojning_s)
            )

        if not math.isfinite(eff_intervall_s) or eff_intervall_s <= 0.0:
            raise AutoStadareFel(
                f"intervall_s måste vara ändligt och > 0, fick {eff_intervall_s!r}."
            )
        if not math.isfinite(eff_fordrojning_s) or eff_fordrojning_s < 0.0:
            raise AutoStadareFel(
                f"start_fordrojning_s måste vara ändligt och >= 0, fick {eff_fordrojning_s!r}."
            )

        self._intervall_s = eff_intervall_s
        self._vid_start = eff_vid_start
        self._start_fordrojning_s = eff_fordrojning_s
        self._stadare_fn: Callable[[LokalDiskKonfig], DiskStadaResultat] = (
            stadare_fn if stadare_fn is not None else stada_lokal_disk
        )
        self._pa_resultat = pa_resultat
        self._pa_fel = pa_fel

        self._lock = threading.Lock()
        self._stopp_event = threading.Event()
        self._trad: threading.Thread | None = None
        self._antal_svep: int = 0
        self._antal_fel: int = 0
        self._senaste_resultat: DiskStadaResultat | None = None
        self._senaste_fel: str | None = None

    @property
    def disk_konfig(self) -> LokalDiskKonfig:
        return self._disk_konfig

    @property
    def intervall_s(self) -> float:
        return self._intervall_s

    @property
    def ar_igang(self) -> bool:
        with self._lock:
            return self._trad is not None and self._trad.is_alive()

    @property
    def antal_svep(self) -> int:
        with self._lock:
            return self._antal_svep

    @property
    def antal_fel(self) -> int:
        with self._lock:
            return self._antal_fel

    @property
    def senaste_resultat(self) -> DiskStadaResultat | None:
        with self._lock:
            return self._senaste_resultat

    @property
    def senaste_fel(self) -> str | None:
        with self._lock:
            return self._senaste_fel

    def starta(self) -> bool:
        """Startar bakgrundstråden (idempotent; returnerar False om den redan körs)."""
        with self._lock:
            if self._trad is not None and self._trad.is_alive():
                return False
            self._stopp_event.clear()
            trad = threading.Thread(
                target=self._loop,
                name="floden-auto-diskstadare",
                daemon=True,
            )
            self._trad = trad
            trad.start()
            return True

    def stoppa(self, timeout_s: float = 2.0) -> None:
        """Väcker väntande tråd och stoppar städaren omedelbart (idempotent)."""
        self._stopp_event.set()
        with self._lock:
            trad = self._trad
        if trad is not None and trad is not threading.current_thread():
            trad.join(timeout=timeout_s)
        with self._lock:
            if self._trad is trad and (trad is None or not trad.is_alive()):
                self._trad = None

    def kor_ett_svep(self) -> DiskStadaResultat | None:
        """Utför ett städsvep med full fel-isolering (kastar aldrig vidare undantag)."""
        try:
            res = self._stadare_fn(self._disk_konfig)
        except Exception as exc:  # noqa: BLE001 - isolera alla fel från webbservern
            ren_fel = _sanera_loggtext(exc)
            with self._lock:
                self._antal_fel += 1
                self._senaste_fel = ren_fel
            _logg.warning("Automatisk diskstädning misslyckades (fortsätter): %s", ren_fel)
            if self._pa_fel is not None:
                try:
                    self._pa_fel(exc)
                except Exception as cb_exc:  # noqa: BLE001
                    _logg.warning(
                        "Fel i återanrop pa_fel för diskstädning: %s", _sanera_loggtext(cb_exc)
                    )
            return None

        with self._lock:
            self._antal_svep += 1
            self._senaste_resultat = res
            self._senaste_fel = None
        _logg.info(
            "Automatisk diskstädning klar: raderade=%d frigjorde=%.2f MB "
            "skyddade=%d kvar=%.2f MB varningar=%d",
            res.antal_raderade,
            res.frigjorda_mb,
            res.antal_skyddade,
            res.efter_mb,
            len(res.varningar),
        )
        if self._pa_resultat is not None:
            try:
                self._pa_resultat(res)
            except Exception as cb_exc:  # noqa: BLE001
                _logg.warning(
                    "Fel i återanrop pa_resultat för diskstädning: %s",
                    _sanera_loggtext(cb_exc),
                )
        return res

    def _loop(self) -> None:
        if self._vid_start:
            if self._start_fordrojning_s > 0.0 and self._stopp_event.wait(
                self._start_fordrojning_s
            ):
                return
            if not self._stopp_event.is_set():
                self.kor_ett_svep()

        while not self._stopp_event.wait(self._intervall_s):
            self.kor_ett_svep()

    def __enter__(self) -> AutoDiskStadare:
        self.starta()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stoppa()


def _las_valfri_konfig(konfig_fil: Path | None) -> tuple[dict[str, Any], Path | None]:
    if konfig_fil is None:
        return {}, None
    sokvag = Path(konfig_fil)
    bas_rot = sokvag.resolve().parent
    if not sokvag.is_file():
        return {}, bas_rot
    lokal = sokvag.parent / "config.local.toml"
    try:
        return las_konfig(sokvag, lokal if lokal.is_file() else None), bas_rot
    except KonfigFel as exc:
        raise AutoStadareFel(str(exc)) from exc


def starta_auto_diskstadare_om_aktiv(
    konfig_fil: str | Path | None = None,
    *,
    cache_rot: Path | None = None,
    projekt_rot: Path | None = None,
    demo_rot: Path | None = None,
    miljo: Mapping[str, str] | None = None,
    start_fordrojning_s: float = STANDARD_START_FORDROJNING_S,
) -> AutoDiskStadare | None:
    """Startar `AutoDiskStadare` om `FLODEN_AUTO_STADA_DISK=1`, annars returnerar `None`.

    Avsedd för `floden webb` så att containerdrift får automatisk lokal cachestädning utan krav
    på systemd-timer, medan ren lokal körning och tester har funktionen avstängd som standard.
    """
    env = os.environ if miljo is None else miljo
    if not _tolka_bool_env(env, FLODEN_AUTO_STADA_DISK_ENV, standard=False):
        return None
    konfig_dict, bas_rot = _las_valfri_konfig(Path(konfig_fil) if konfig_fil is not None else None)
    auto_konfig = las_auto_stadare_konfig(
        konfig_dict,
        miljo=env,
        bas_rot=bas_rot,
        cache_rot=cache_rot,
        projekt_rot=projekt_rot,
        demo_rot=demo_rot,
        start_fordrojning_s=start_fordrojning_s,
    )
    stadare = AutoDiskStadare(auto_konfig)
    stadare.starta()
    _logg.info(
        "Automatisk lokal diskstädning aktiv (intervall=%.1f h, vid_start=%s, max_gb=%.1f).",
        auto_konfig.intervall_timmar,
        "ja" if auto_konfig.vid_start else "nej",
        auto_konfig.disk_konfig.max_gb,
    )
    return stadare


__all__ = [
    "AutoDiskStadare",
    "AutoStadareFel",
    "AutoStadareKonfig",
    "FLODEN_AUTO_STADA_DISK_ENV",
    "FLODEN_AUTO_STADA_INTERVALL_ENV",
    "FLODEN_AUTO_STADA_VID_START_ENV",
    "STANDARD_INTERVALL_TIMMAR",
    "STANDARD_START_FORDROJNING_S",
    "las_auto_stadare_konfig",
    "starta_auto_diskstadare_om_aktiv",
]
