"""Isolerad barnprocesskörning för K20-utredningar med aktiv RSS- och tidsvakt (S-O1).

Kör tunga fysik- och sänkgrafberäkningar i en kortlivad separat Python-process (`subprocess.Popen`)
så att:

* ett minnesspiks- eller OOM-avslut bara dödar barnprocessen — webbserverprocessen (ASGI/konto)
  lever vidare 100 % ostörd och `/api/halsa` svarar `200 OK`,
* OpenBLAS/OpenMP/MKL/NumExpr tvingas till 1 tråd i barnprocessen (`*_NUM_THREADS=1`) för att
  förhindra virtuell trådpool-explosion på 3 GB / 2 vCPU-servrar,
* minnesgränsen övervakas via verklig fysisk RSS (`/proc/<pid>/status` `VmRSS` på Linux,
  `ps -o rss=` på macOS) — aldrig med `RLIMIT_AS`, som bryter OpenBLAS virtuella mappning,
* hängande körningar avbryts rent vid `max_tid_s`,
* aktiva barnprocesser spåras i ett trådsäkert register och avslutas utan zombie-processer
  vid nedstängning (`avbryt_alla()`),
* enbart JSON-serialiserbara sökvägar skickas till barnprocessen (inga lås eller pickle-problem),
  och framsteg skrivs atomiskt till `status_fil`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STANDARD_JOBB_MAX_RAM_MB = 2200.0
STANDARD_JOBB_MAX_TID_S = 300.0
STANDARD_POLL_INTERVALL_S = 0.15
STANDARD_AVSLUT_GRACE_S = 1.5

BARN_TRAD_ENV: dict[str, str] = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}

_CGROUP_V2_MEM_MAX = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_MEM_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
_CGROUP_OBEGRANSAD_GRANS_BYTES = 1 << 50  # ~1 PB: filtrerar kernelns "unlimited"-sentineller


class SubprocFel(RuntimeError):
    """Fel från en isolerad K20-barnprocess."""

    def __init__(
        self,
        meddelande: str,
        *,
        ursprunglig_typ: str | None = None,
        returkod: int | None = None,
    ) -> None:
        super().__init__(meddelande)
        self.ursprunglig_typ = ursprunglig_typ
        self.returkod = returkod


class SubprocMinneFel(SubprocFel):
    """Barnprocessen överskred serverns RAM-gräns (RSS-vakt, OOM-kill eller MemoryError)."""


class SubprocTimeoutFel(SubprocFel):
    """Barnprocessen överskred tidsgränsen och avbröts rent."""


class SubprocAvbrutenFel(SubprocFel):
    """Barnprocessen avbröts kontrollerat när servern stoppades."""


@dataclass(frozen=True)
class SubprocResultat:
    """Utfall för en avslutad barnprocess."""

    returkod: int
    topp_rss_mb: float | None
    kortid_s: float


def _nu_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _skriv_json_atomiskt(mal: Path, payload: Mapping[str, Any]) -> None:
    """Skriver JSON atomiskt via unik tempfil i samma katalog + `os.replace`."""
    mal.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_namn = tempfile.mkstemp(prefix=f".{mal.name}.", suffix=".tmp", dir=str(mal.parent))
    tmp = Path(tmp_namn)
    try:
        data = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, mal)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def las_framsteg(status_fil: Path) -> dict[str, Any] | None:
    """Läser atomiskt skriven status-/framstegsfil, eller `None` om den saknas/inte kan tolkas."""
    try:
        ra = status_fil.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(ra)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def befintlig_sokvag(mapp: Path) -> Path:
    """Närmaste befintliga katalog i eller ovanför `mapp` (för `shutil.disk_usage`)."""
    p = mapp
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def disk_ledig_gb(mapp: Path) -> float | None:
    """Ledigt diskutrymme i GB för filsystemet som rymmer `mapp` (PII-fri siffra)."""
    try:
        anvandning = shutil.disk_usage(befintlig_sokvag(mapp))
    except OSError:
        return None
    return round(anvandning.free / (1024**3), 2)


def _proc_vmrss_kb(pid: int) -> int | None:
    """Läser `VmRSS` i KiB ur `/proc/<pid>/status` (Linux), eller `None` om ej tillgängligt."""
    status_fil = Path(f"/proc/{pid}/status")
    try:
        text = status_fil.read_text(encoding="utf-8")
    except OSError:
        return None
    for rad in text.splitlines():
        if rad.startswith("VmRSS:"):
            delar = rad.split()
            if len(delar) >= 2:
                try:
                    return int(delar[1])
                except ValueError:
                    return None
    return None


def _proc_barn_pids(pid: int) -> list[int]:
    """Läser direkta barn-PID:er ur `/proc/<pid>/task/*/children` (Linux)."""
    task_rot = Path(f"/proc/{pid}/task")
    if not task_rot.is_dir():
        return []
    barn: list[int] = []
    try:
        for barnfil in task_rot.glob("*/children"):
            try:
                innehall = barnfil.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            for token in innehall.split():
                try:
                    barn.append(int(token))
                except ValueError:
                    continue
    except OSError:
        return []
    return barn


def _ps_rss_kb(pid: int, *, inkl_barn: bool) -> int | None:
    """Läser RSS i KiB via `ps -o rss=` (macOS/POSIX-reserv när `/proc` saknas)."""
    flaggor = [["-o", "rss=", "-g", str(pid)], ["-o", "rss=", "-p", str(pid)]] if inkl_barn else [
        ["-o", "rss=", "-p", str(pid)]
    ]
    for args in flaggor:
        try:
            ut = subprocess.run(
                ["ps", *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=1.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if ut.returncode != 0:
            continue
        rader = [r.strip() for r in ut.stdout.splitlines() if r.strip()]
        if not rader:
            continue
        totalt = 0
        hittad = False
        for rad in rader:
            try:
                totalt += int(rad)
                hittad = True
            except ValueError:
                continue
        if hittad:
            return totalt
    return None


def process_rss_mb(pid: int | None = None, *, inkl_barn: bool = False) -> float | None:
    """Returnerar aktuell fysisk RSS i MiB för `pid` (standard: egen process).

    På Linux läses `VmRSS` direkt ur `/proc/<pid>/status` (utan subshell).
    På macOS/övriga POSIX-system används `ps -o rss=`.
    Med `inkl_barn=True` summeras även processgruppens/barnprocessernas RSS.
    """
    mal_pid = os.getpid() if pid is None else int(pid)
    if mal_pid <= 0:
        return None

    # 1) Linux procfs (snabbt, noll subprocesser)
    egen_kb = _proc_vmrss_kb(mal_pid)
    if egen_kb is not None:
        totalt_kb = egen_kb
        if inkl_barn:
            sedda: set[int] = {mal_pid}
            stack = _proc_barn_pids(mal_pid)
            while stack:
                bpid = stack.pop()
                if bpid in sedda:
                    continue
                sedda.add(bpid)
                b_kb = _proc_vmrss_kb(bpid)
                if b_kb is not None:
                    totalt_kb += b_kb
                stack.extend(_proc_barn_pids(bpid))
        return round(totalt_kb / 1024.0, 2)

    # Om /proc finns på systemet men inte /proc/<pid>/status har processen redan avslutats.
    if Path("/proc/self/status").is_file():
        return None

    # 2) macOS / POSIX utan /proc: `ps -o rss=`
    ps_kb = _ps_rss_kb(mal_pid, inkl_barn=inkl_barn)
    if ps_kb is not None:
        return round(ps_kb / 1024.0, 2)

    # 3) Sista reserv endast för egen process (ru_maxrss: byte på darwin, KiB på Linux)
    if pid is None:
        try:
            maxrss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        except OSError:
            return None
        if maxrss <= 0.0:
            return None
        if sys.platform == "darwin":
            return round(maxrss / (1024.0 * 1024.0), 2)
        return round(maxrss / 1024.0, 2)
    return None


def cgroup_minne_grans_mb(
    *,
    v2_fil: Path = _CGROUP_V2_MEM_MAX,
    v1_fil: Path = _CGROUP_V1_MEM_LIMIT,
) -> float | None:
    """Containerns minnesgräns i MiB ur cgroup v2 (`memory.max`) eller v1, annars `None`."""
    for fil in (v2_fil, v1_fil):
        try:
            ra = fil.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not ra or ra.lower() == "max":
            return None
        try:
            antal_bytes = int(ra)
        except ValueError:
            continue
        if 0 < antal_bytes < _CGROUP_OBEGRANSAD_GRANS_BYTES:
            return round(antal_bytes / (1024.0 * 1024.0), 1)
        return None
    return None


def bygg_barnmiljo(extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Bygger ren miljö för K20-barnprocess med trådtak för BLAS/OpenMP (aldrig `RLIMIT_AS`)."""
    env = os.environ.copy()
    env.update(BARN_TRAD_ENV)
    env["PYTHONUNBUFFERED"] = "1"
    src_rot = str(Path(__file__).resolve().parents[1])
    befintlig = env.get("PYTHONPATH", "")
    delar = [d for d in befintlig.split(os.pathsep) if d] if befintlig else []
    if src_rot not in delar:
        env["PYTHONPATH"] = os.pathsep.join([src_rot, *delar])
    if extra_env:
        env.update(extra_env)
    return env


def _skicka_signal_processgrupp(proc: subprocess.Popen[bytes], sig: int) -> None:
    """Skickar signal till barnprocessens processgrupp (POSIX) eller själva processen."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()
    except OSError:
        pass


def _avsluta_process(
    proc: subprocess.Popen[bytes],
    *,
    grace_s: float = STANDARD_AVSLUT_GRACE_S,
) -> int:
    """Avslutar en barnprocess (och dess processgrupp) rent utan zombie-processer."""
    rc = proc.poll()
    if rc is not None:
        return rc
    _skicka_signal_processgrupp(proc, signal.SIGTERM)
    try:
        return proc.wait(timeout=max(0.05, float(grace_s)))
    except subprocess.TimeoutExpired:
        pass
    _skicka_signal_processgrupp(proc, signal.SIGKILL)
    with suppress(OSError):
        proc.kill()
    return proc.wait()


_GLOBALA_LOCK = threading.Lock()
_GLOBALA_PROCESSER: dict[int, subprocess.Popen[bytes]] = {}
_GLOBALA_HANTERARE: weakref.WeakSet[SubprocHanterare] = weakref.WeakSet()


class SubprocHanterare:
    """Trådsäkert register över aktiva barnprocesser för kontrollerad nedstängning."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stangd = False
        self._aktiva: dict[int, subprocess.Popen[bytes]] = {}
        self._avbrutna: set[int] = set()
        with _GLOBALA_LOCK:
            _GLOBALA_HANTERARE.add(self)

    @property
    def stangd(self) -> bool:
        with self._lock:
            return self._stangd

    def aktiva_pids(self) -> list[int]:
        """Snapshot av aktiva barn-PID:er."""
        with self._lock:
            return sorted(self._aktiva)

    def ar_avbruten(self, pid: int) -> bool:
        with self._lock:
            return self._stangd or pid in self._avbrutna

    def registrera(self, proc: subprocess.Popen[bytes]) -> None:
        """Registrerar en nystartad barnprocess, eller dödar den direkt om hanteraren är stängd."""
        with self._lock:
            if self._stangd:
                self._avbrutna.add(proc.pid)
                stangd = True
            else:
                self._aktiva[proc.pid] = proc
                stangd = False
        if stangd:
            _avsluta_process(proc, grace_s=0.3)
            raise SubprocAvbrutenFel("Körningen avbröts när servern stoppades.")
        with _GLOBALA_LOCK:
            _GLOBALA_PROCESSER[proc.pid] = proc

    def avregistrera(self, proc: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._aktiva.pop(proc.pid, None)
        with _GLOBALA_LOCK:
            _GLOBALA_PROCESSER.pop(proc.pid, None)

    def avbryt_alla(self, *, grace_s: float = STANDARD_AVSLUT_GRACE_S) -> int:
        """Avbryter och väntar in alla aktiva barnprocesser i den här hanteraren (inga zombies)."""
        with self._lock:
            self._stangd = True
            processer = list(self._aktiva.values())
            for proc in processer:
                self._avbrutna.add(proc.pid)
        for proc in processer:
            _avsluta_process(proc, grace_s=grace_s)
        with self._lock:
            for proc in processer:
                self._aktiva.pop(proc.pid, None)
        with _GLOBALA_LOCK:
            for proc in processer:
                _GLOBALA_PROCESSER.pop(proc.pid, None)
        return len(processer)


def avbryt_alla(*, grace_s: float = STANDARD_AVSLUT_GRACE_S) -> int:
    """Avbryter samtliga registrerade barnprocesser i hela webbprocessen (inga zombies)."""
    with _GLOBALA_LOCK:
        hanterare = list(_GLOBALA_HANTERARE)
        direkta = list(_GLOBALA_PROCESSER.values())
    avbrutna_pids: set[int] = set()
    for h in hanterare:
        with h._lock:  # noqa: SLF001
            for pid in h._aktiva:  # noqa: SLF001
                avbrutna_pids.add(pid)
        h.avbryt_alla(grace_s=grace_s)
    for proc in direkta:
        if proc.pid not in avbrutna_pids:
            _avsluta_process(proc, grace_s=grace_s)
            avbrutna_pids.add(proc.pid)
            with _GLOBALA_LOCK:
                _GLOBALA_PROCESSER.pop(proc.pid, None)
    return len(avbrutna_pids)


def _las_stderr_svans(fil: Any, *, max_bytes: int = 2048) -> str:
    """Läser slutet av barnprocessens temporära stderr-fil utan att blockera."""
    try:
        fil.flush()
        fil.seek(0, os.SEEK_END)
        storlek = int(fil.tell())
        start = max(0, storlek - max_bytes)
        fil.seek(start)
        data = fil.read()
        if not isinstance(data, bytes):
            return ""
        text = data.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    rader = [r.strip() for r in text.splitlines() if r.strip()]
    if not rader:
        return ""
    return rader[-1][:300]


def overvaka_barnprocess(
    kommando: Sequence[str],
    *,
    max_ram_mb: float = STANDARD_JOBB_MAX_RAM_MB,
    max_tid_s: float = STANDARD_JOBB_MAX_TID_S,
    poll_intervall_s: float = STANDARD_POLL_INTERVALL_S,
    hanterare: SubprocHanterare | None = None,
    status_fil: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
    avslut_grace_s: float = STANDARD_AVSLUT_GRACE_S,
) -> SubprocResultat:
    """Startar och övervakar en barnprocess med aktiv RSS- och tidsgräns.

    * Överstiger faktisk fysisk RSS `max_ram_mb` avslutas barnprocessen rent med `SubprocMinneFel`.
    * Överstiger körtiden `max_tid_s` avslutas barnprocessen rent med `SubprocTimeoutFel`.
    * Dödas barnprocessen av kärnans OOM-killer (`returncode == -9` eller `137`) kastas
      `SubprocMinneFel` med begripligt svenskt felmeddelande.
    * Anropas `hanterare.avbryt_alla()` avslutas barnprocessen rent utan zombie och
      `SubprocAvbrutenFel` kastas.
    """
    if not math.isfinite(max_ram_mb) or max_ram_mb <= 0.0:
        raise ValueError(f"max_ram_mb måste vara > 0, fick {max_ram_mb!r}.")
    if not math.isfinite(max_tid_s) or max_tid_s <= 0.0:
        raise ValueError(f"max_tid_s måste vara > 0, fick {max_tid_s!r}.")
    if not math.isfinite(poll_intervall_s) or poll_intervall_s <= 0.0:
        raise ValueError(f"poll_intervall_s måste vara > 0, fick {poll_intervall_s!r}.")

    if hanterare is not None and hanterare.stangd:
        raise SubprocAvbrutenFel("Körningen avbröts när servern stoppades.")

    barnmiljo = bygg_barnmiljo(extra_env)
    t0 = time.monotonic()
    topp_rss_mb: float | None = None
    stderr_svans = ""

    with tempfile.TemporaryFile(mode="w+b") as stderr_fil:
        proc = subprocess.Popen(
            list(kommando),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_fil,
            env=barnmiljo,
            start_new_session=(os.name == "posix"),
        )
        if hanterare is not None:
            hanterare.registrera(proc)
        else:
            with _GLOBALA_LOCK:
                _GLOBALA_PROCESSER[proc.pid] = proc

        try:
            while True:
                rc = proc.poll()
                if rc is not None:
                    break

                if hanterare is not None and hanterare.ar_avbruten(proc.pid):
                    _avsluta_process(proc, grace_s=avslut_grace_s)
                    raise SubprocAvbrutenFel("Körningen avbröts när servern stoppades.")

                forflutet = time.monotonic() - t0
                if forflutet >= max_tid_s:
                    _avsluta_process(proc, grace_s=avslut_grace_s)
                    raise SubprocTimeoutFel(
                        f"Beräkningen överskred serverns tidsgräns ({max_tid_s:g} s) och avbröts."
                    )

                rss_mb = process_rss_mb(proc.pid, inkl_barn=True)
                if rss_mb is not None:
                    topp_rss_mb = rss_mb if topp_rss_mb is None else max(topp_rss_mb, rss_mb)
                    if rss_mb > max_ram_mb:
                        _avsluta_process(proc, grace_s=avslut_grace_s)
                        raise SubprocMinneFel(
                            f"Beräkningen krävde mer minne än serverns gräns "
                            f"({rss_mb:.0f} MB > {max_ram_mb:.0f} MB). "
                            "Välj en mindre avgränsning eller färre analyser."
                        )

                kvar = max(0.01, min(poll_intervall_s, max_tid_s - (time.monotonic() - t0)))
                try:
                    rc = proc.wait(timeout=kvar)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            if proc.poll() is None:
                _avsluta_process(proc, grace_s=avslut_grace_s)
            raise
        finally:
            if hanterare is not None:
                hanterare.avregistrera(proc)
            else:
                with _GLOBALA_LOCK:
                    _GLOBALA_PROCESSER.pop(proc.pid, None)
            stderr_svans = _las_stderr_svans(stderr_fil)

    kortid_s = time.monotonic() - t0
    if hanterare is not None and hanterare.ar_avbruten(proc.pid):
        raise SubprocAvbrutenFel("Körningen avbröts när servern stoppades.")

    status_data = las_framsteg(status_fil) if status_fil is not None else None
    if rc == 0:
        return SubprocResultat(returkod=0, topp_rss_mb=topp_rss_mb, kortid_s=kortid_s)

    if status_data is not None and status_data.get("status") == "fel":
        fel_typ = str(status_data.get("fel_typ") or "SubprocFel")
        fel_msg = str(status_data.get("fel_meddelande") or status_data.get("fel") or "").strip()
        if fel_typ == "MemoryError":
            raise SubprocMinneFel(
                "Beräkningen krävde mer minne än serverns gräns (MemoryError). "
                "Välj en mindre avgränsning eller färre analyser.",
                ursprunglig_typ="SubprocMinneFel",
                returkod=rc,
            )
        raise SubprocFel(
            fel_msg or f"Beräkningsprocessen misslyckades (returkod {rc}).",
            ursprunglig_typ=fel_typ,
            returkod=rc,
        )

    if rc in (-9, 137, -int(signal.SIGKILL)):
        raise SubprocMinneFel(
            f"Beräkningen krävde mer minne än serverns gräns och stoppades av systemet "
            f"(OOM, returkod {rc}). Välj en mindre avgränsning eller färre analyser.",
            returkod=rc,
        )

    detalj = f": {stderr_svans}" if stderr_svans else ""
    raise SubprocFel(
        f"Beräkningsprocessen avslutades oväntat (returkod {rc}){detalj}",
        returkod=rc,
    )


def _bygg_barnkommando(spec_fil: Path) -> list[str]:
    """Standardkommando för att köra en K20-utredning i en isolerad barnprocess."""
    return [sys.executable, "-m", "webb.subproc_kor", "--spec-fil", str(spec_fil)]


def kor_fraga_i_barnprocess(
    *,
    fraga_fil: Path,
    cachemapp: Path,
    utmapp: Path,
    status_fil: Path | None = None,
    max_ram_mb: float = STANDARD_JOBB_MAX_RAM_MB,
    max_tid_s: float = STANDARD_JOBB_MAX_TID_S,
    poll_intervall_s: float = STANDARD_POLL_INTERVALL_S,
    hanterare: SubprocHanterare | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> SubprocResultat:
    """Kör en K20-frågefil i en isolerad barnprocess med enbart JSON-serialiserbara sökvägar."""
    fraga_abs = Path(fraga_fil).resolve()
    cache_abs = Path(cachemapp).resolve()
    ut_abs = Path(utmapp).resolve()
    status_abs = (
        Path(status_fil).resolve()
        if status_fil is not None
        else (ut_abs.parent / "framsteg.json").resolve()
    )
    status_abs.parent.mkdir(parents=True, exist_ok=True)
    status_abs.unlink(missing_ok=True)

    spec_payload: dict[str, Any] = {
        "fraga_fil": str(fraga_abs),
        "cachemapp": str(cache_abs),
        "utmapp": str(ut_abs),
        "status_fil": str(status_abs),
        "max_ram_mb": float(max_ram_mb),
        "max_tid_s": float(max_tid_s),
    }
    fd, tmp_spec = tempfile.mkstemp(
        prefix=".subproc_spec_", suffix=".json", dir=str(status_abs.parent)
    )
    spec_fil = Path(tmp_spec)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(spec_payload, fh, ensure_ascii=False)
            fh.write("\n")
        kommando = _bygg_barnkommando(spec_fil)
        resultat = overvaka_barnprocess(
            kommando,
            max_ram_mb=max_ram_mb,
            max_tid_s=max_tid_s,
            poll_intervall_s=poll_intervall_s,
            hanterare=hanterare,
            status_fil=status_abs,
            extra_env=extra_env,
        )
    finally:
        spec_fil.unlink(missing_ok=True)

    if not (ut_abs / "sammanstallning.json").is_file():
        raise SubprocFel(
            "Beräkningsprocessen avslutades utan att skriva sammanstallning.json.",
            returkod=resultat.returkod,
        )
    return resultat


def _kor_spec_fil(spec_fil: Path) -> int:
    """Körs inne i barnprocessen: läser JSON-spec, kör `kor_utredning` och skriver framsteg."""
    from karna.korplan import Plan, kor_utredning, planera
    from karna.resultatcache import ResultatCache
    from karna.utredning import las_fraga

    spec = json.loads(spec_fil.read_text(encoding="utf-8"))
    fraga_fil = Path(spec["fraga_fil"])
    cachemapp = Path(spec["cachemapp"])
    utmapp = Path(spec["utmapp"])
    status_fil = Path(spec["status_fil"])

    def _skriv_status(
        status: str,
        *,
        klara: int = 0,
        totalt: int = 0,
        aktuellt_jobb: str | None = None,
        fel_typ: str | None = None,
        fel_meddelande: str | None = None,
    ) -> None:
        post: dict[str, Any] = {
            "status": status,
            "klara": int(klara),
            "totalt": int(totalt),
            "pid": os.getpid(),
            "uppdaterad": _nu_iso(),
        }
        if aktuellt_jobb is not None:
            post["aktuellt_jobb"] = aktuellt_jobb
        if fel_typ is not None:
            post["fel_typ"] = fel_typ
        if fel_meddelande is not None:
            post["fel_meddelande"] = fel_meddelande
            post["fel"] = f"{fel_typ}: {fel_meddelande}" if fel_typ else fel_meddelande
        _skriv_json_atomiskt(status_fil, post)

    class _FramstegsCache(ResultatCache):
        """ResultatCache-subklass som uppdaterar `status_fil` atomiskt efter varje avslutat jobb."""

        def __init__(self, rot: Path) -> None:
            super().__init__(rot)
            self._nycklar: tuple[str, ...] = ()
            self._etiketter: dict[str, str] = {}

        def bind_plan(self, plan: Plan) -> None:
            self._nycklar = tuple(j.nyckel for j in plan.jobb)
            self._etiketter = {j.nyckel: j.etikett for j in plan.jobb}

        def antal_klara(self) -> int:
            return sum(1 for n in self._nycklar if self.finns("jobb", n))

        def skapa(
            self,
            slag: str,
            nyckel: str,
            skrivare: Callable[[Path], dict[str, Any] | None],
        ) -> Path:
            if slag == "jobb" and nyckel in self._etiketter:
                _skriv_status(
                    "kor",
                    klara=self.antal_klara(),
                    totalt=len(self._nycklar),
                    aktuellt_jobb=self._etiketter[nyckel],
                )
            mal = super().skapa(slag, nyckel, skrivare)
            if slag == "jobb" and nyckel in self._etiketter:
                _skriv_status(
                    "kor",
                    klara=self.antal_klara(),
                    totalt=len(self._nycklar),
                )
            return mal

    _skriv_status("kor", klara=0, totalt=0)
    try:
        cache = _FramstegsCache(cachemapp)
        fraga = las_fraga(fraga_fil)
        plan = planera(fraga, cache)
        cache.bind_plan(plan)
        totalt = len(plan.jobb)
        _skriv_status("kor", klara=cache.antal_klara(), totalt=totalt)
        kor_utredning(fraga, cache, utmapp, plan=plan)
        _skriv_status("klar", klara=totalt, totalt=totalt)
        return 0
    except MemoryError as exc:
        _skriv_status(
            "fel",
            fel_typ="MemoryError",
            fel_meddelande=str(exc) or "Otillräckligt arbetsminne.",
        )
        return 137
    except Exception as exc:  # noqa: BLE001 - fånga alla fel och skriv atomiskt för föräldern
        _skriv_status(
            "fel",
            fel_typ=type(exc).__name__,
            fel_meddelande=str(exc),
        )
        return 1


def _bygg_vektor_barnkommando(spec_fil: Path) -> list[str]:
    """Standardkommando för att bygga vektorlager (sänkor/flödesvägar) i isolerad barnprocess."""
    return [sys.executable, "-m", "webb.subproc_kor", "--vektor-spec-fil", str(spec_fil)]


def kor_vektor_i_barnprocess(
    *,
    slag: str,
    mappar: Sequence[Path],
    persistens_faktor: float | None,
    takvatten: bool,
    takvatten_andel_ledning: float,
    min_yta_m2: float,
    min_volym_m3: float,
    min_djup_m: float,
    ut_json: Path,
    bas_npz: Path | None = None,
    max_ram_mb: float = STANDARD_JOBB_MAX_RAM_MB,
    max_tid_s: float = STANDARD_JOBB_MAX_TID_S,
    poll_intervall_s: float = STANDARD_POLL_INTERVALL_S,
    hanterare: SubprocHanterare | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> SubprocResultat:
    """Bygger tunga vektorlager (`sankor` / `flodesvagar`) i en isolerad barnprocess.

    Håller Python-GIL och minnestoppar helt utanför webbserverprocessen (S-O1)."""
    ut_abs = Path(ut_json).resolve()
    ut_abs.parent.mkdir(parents=True, exist_ok=True)
    fd_st, tmp_status = tempfile.mkstemp(
        prefix=f".vektor_status_{slag}_", suffix=".json", dir=str(ut_abs.parent)
    )
    os.close(fd_st)
    status_abs = Path(tmp_status)
    status_abs.unlink(missing_ok=True)

    spec_payload: dict[str, Any] = {
        "slag": slag,
        "mappar": [str(Path(m).resolve()) for m in mappar],
        "persistens_faktor": persistens_faktor,
        "takvatten": bool(takvatten),
        "takvatten_andel_ledning": float(takvatten_andel_ledning),
        "min_yta_m2": float(min_yta_m2),
        "min_volym_m3": float(min_volym_m3),
        "min_djup_m": float(min_djup_m),
        "ut_json": str(ut_abs),
        "bas_npz": str(Path(bas_npz).resolve()) if bas_npz is not None else None,
        "status_fil": str(status_abs),
    }
    fd, tmp_spec = tempfile.mkstemp(
        prefix=f".vektor_spec_{slag}_", suffix=".json", dir=str(ut_abs.parent)
    )
    spec_fil = Path(tmp_spec)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(spec_payload, fh, ensure_ascii=False)
            fh.write("\n")
        kommando = _bygg_vektor_barnkommando(spec_fil)
        resultat = overvaka_barnprocess(
            kommando,
            max_ram_mb=max_ram_mb,
            max_tid_s=max_tid_s,
            poll_intervall_s=poll_intervall_s,
            hanterare=hanterare,
            status_fil=status_abs,
            extra_env=extra_env,
        )
    finally:
        spec_fil.unlink(missing_ok=True)
        status_abs.unlink(missing_ok=True)

    if not ut_abs.is_file():
        raise SubprocFel(
            f"Vektorprocessen ({slag}) avslutades utan att skriva resultatfil.",
            returkod=resultat.returkod,
        )
    return resultat


def _kor_vektor_spec_fil(spec_fil: Path) -> int:
    """Körs inne i barnprocessen: bygger `sankor` eller `flodesvagar` och skriver JSON atomiskt."""
    from webb.fragor import FragaTjanst

    spec = json.loads(spec_fil.read_text(encoding="utf-8"))
    status_fil = Path(spec["status_fil"])
    ut_json = Path(spec["ut_json"])
    bas_npz = Path(spec["bas_npz"]) if spec.get("bas_npz") else None
    mappar = [Path(m) for m in spec["mappar"]]

    try:
        ut = FragaTjanst._berakna_sankgraf_vektor_fran_mappar(  # noqa: SLF001 - samma paket
            mappar,
            slag=str(spec["slag"]),
            persistens_faktor=spec.get("persistens_faktor"),
            takvatten=bool(spec.get("takvatten", True)),
            takvatten_andel_ledning=float(spec.get("takvatten_andel_ledning", 0.0)),
            min_yta_m2=float(spec["min_yta_m2"]),
            min_volym_m3=float(spec["min_volym_m3"]),
            min_djup_m=float(spec["min_djup_m"]),
            bas_npz=bas_npz,
        )
        _skriv_json_atomiskt(ut_json, ut)
        _skriv_json_atomiskt(status_fil, {"status": "klar", "uppdaterad": _nu_iso()})
        return 0
    except MemoryError as exc:
        _skriv_json_atomiskt(
            status_fil,
            {
                "status": "fel",
                "fel_typ": "MemoryError",
                "fel_meddelande": str(exc) or "Otillräckligt arbetsminne.",
                "uppdaterad": _nu_iso(),
            },
        )
        return 137
    except Exception as exc:  # noqa: BLE001
        _skriv_json_atomiskt(
            status_fil,
            {
                "status": "fel",
                "fel_typ": type(exc).__name__,
                "fel_meddelande": str(exc),
                "uppdaterad": _nu_iso(),
            },
        )
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m webb.subproc_kor",
        description="Kör en K20-utredningsfråga eller vektorberäkning i isolerad barnprocess.",
    )
    grupp = ap.add_mutually_exclusive_group(required=True)
    grupp.add_argument("--spec-fil", type=Path, help="JSON-fil med körspecifikation.")
    grupp.add_argument("--vektor-spec-fil", type=Path, help="JSON-fil med vektorspecifikation.")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.vektor_spec_fil is not None:
        return _kor_vektor_spec_fil(args.vektor_spec_fil)
    return _kor_spec_fil(args.spec_fil)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BARN_TRAD_ENV",
    "STANDARD_AVSLUT_GRACE_S",
    "STANDARD_JOBB_MAX_RAM_MB",
    "STANDARD_JOBB_MAX_TID_S",
    "STANDARD_POLL_INTERVALL_S",
    "SubprocAvbrutenFel",
    "SubprocFel",
    "SubprocHanterare",
    "SubprocMinneFel",
    "SubprocResultat",
    "SubprocTimeoutFel",
    "avbryt_alla",
    "befintlig_sokvag",
    "bygg_barnmiljo",
    "cgroup_minne_grans_mb",
    "disk_ledig_gb",
    "kor_fraga_i_barnprocess",
    "kor_vektor_i_barnprocess",
    "las_framsteg",
    "overvaka_barnprocess",
    "process_rss_mb",
]
