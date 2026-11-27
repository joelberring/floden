"""Webbens tjänst för Flodens utredningsfrågor (K20): lista, planera, köra och läsa resultat.

Allt läser och körs via kärnans API (`karna.utredning.las_fraga`, `karna.korplan.planera` och
`kor_utredning`, `karna.resultatcache.ResultatCache`, `karna.analyslosare`). Webben räknar
ingenting själv: den väljer fråga, visar planen, startar körningen i en bakgrundstråd i samma
process (ingen extern kö) och läser det kärnan skrev i resultatmappen.

Körningar ligger på lokal disk under `korningsmapp/<körnings-id>/`:

    korning.json       status (webbens egen, ingår inte i kärnans K0/K22)
    resultat/          kärnans utmapp: sammanstallning.json, run.json, <analys>/<scenario>/...
    webb/              PNG-förhandsvisningar och lager.json (cache av det som ritats)

Inloggning och konton finns inte; servern är till för lokal användning.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from shapely import contains_xy
from shapely.geometry import Polygon

from gemensamt import avgransning as avgr
from gemensamt.hash import sha256_fil
from karna.analyslosare import LosarFel, hamta_losare
from karna.geometri import GeometriFel, las_byggnader
from karna.korplan import Plan, kor_utredning, planera
from karna.resultatcache import CacheFel, ResultatCache
from karna.rutscenario import ScenarioFel, materialisera_scenario
from karna.utredning import FragaFel, KorbarFraga, las_fraga
from webb import analysvektor
from webb.fel import FlodenWebbFel
from webb.felforklaring import forklara_fel
from webb.moduler import losarinfo, visningsnamn
from webb.resultatlager import (
    KONTROLLROLLER,
    MASKADE_ROLLER,
    LagerFel,
    Mosaik,
    Utbredning,
    etikett_och_enhet,
    fara_mosaik,
    farg_for_roll,
    rita_bild,
    sammanfoga,
    skillnad_mosaik,
    tak_mask,
    takkalla_for_roll,
    teckenforklaring,
    varde_intervall,
)
from webb.subproc_kor import (
    STANDARD_JOBB_MAX_RAM_MB,
    STANDARD_JOBB_MAX_TID_S,
    SubprocAvbrutenFel,
    SubprocFel,
    SubprocHanterare,
    SubprocMinneFel,
    SubprocTimeoutFel,
    befintlig_sokvag,
    kor_fraga_i_barnprocess,
    kor_vektor_i_barnprocess,
    las_framsteg,
)

_logg = logging.getLogger(__name__)
_ID = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
_FRAGEFEL = (FragaFel, LosarFel, ScenarioFel, CacheFel)
MAX_TABELLRADER = 200
LAGER_VERSION = 12
STANDARD_MAX_KO = 16
STANDARD_EXEKUTOR = "trad"
STANDARD_MIN_LEDIG_DISK_MB = 500.0
_GILTIGA_EXEKUTORER = frozenset({"trad", "process"})


def las_exekutor_lage(varde: str | None = None) -> str:
    """Läser exekutorläge (`'trad'` eller `'process'`): argument > `FLODEN_EXEKUTOR` > `'trad'`."""
    raw = varde if varde is not None else os.environ.get("FLODEN_EXEKUTOR")
    if raw is None or not raw.strip():
        return STANDARD_EXEKUTOR
    lage = raw.strip().lower()
    if lage not in _GILTIGA_EXEKUTORER:
        raise ValueError(
            f"Ogiltigt värde för FLODEN_EXEKUTOR={raw!r}: förväntade 'trad' eller 'process'."
        )
    return lage


def _las_positivt_tal_env(
    varde: float | None,
    env_namn: str,
    standard: float,
    *,
    tillat_noll: bool = False,
) -> float:
    if varde is not None:
        if isinstance(varde, bool):
            raise ValueError(f"{env_namn} måste vara ett tal, inte bool.")
        tal = float(varde)
    else:
        raw = os.environ.get(env_namn)
        if raw is None or not raw.strip():
            return float(standard)
        try:
            tal = float(raw.strip())
        except ValueError as exc:
            raise ValueError(f"Ogiltigt värde för {env_namn}={raw!r}: ange ett tal.") from exc
    if not math.isfinite(tal) or (tal < 0.0 if tillat_noll else tal <= 0.0):
        villkor = ">= 0" if tillat_noll else "> 0"
        raise ValueError(
            f"Ogiltigt värde för {env_namn}={tal!r}: måste vara ändligt och {villkor}."
        )
    return tal


def las_jobb_max_ram_mb(varde: float | None = None) -> float:
    """Läser RAM-gräns per K20-barnprocess i MB (`FLODEN_JOBB_MAX_RAM_MB`, standard 2200)."""
    return _las_positivt_tal_env(varde, "FLODEN_JOBB_MAX_RAM_MB", STANDARD_JOBB_MAX_RAM_MB)


def las_jobb_max_tid_s(varde: float | None = None) -> float:
    """Läser tidsgräns per K20-barnprocess i sekunder (`FLODEN_JOBB_MAX_TID_S`, standard 300)."""
    return _las_positivt_tal_env(varde, "FLODEN_JOBB_MAX_TID_S", STANDARD_JOBB_MAX_TID_S)


def las_min_ledig_disk_mb(varde: float | None = None) -> float:
    """Läser minsta lediga diskutrymme i MB (`FLODEN_MIN_LEDIG_DISK_MB`, standard 500)."""
    return _las_positivt_tal_env(
        varde,
        "FLODEN_MIN_LEDIG_DISK_MB",
        STANDARD_MIN_LEDIG_DISK_MB,
        tillat_noll=True,
    )


class KoFullFel(FlodenWebbFel):
    """Körningskön är full (eller servern stänger ner) — motsvarar HTTP 503 med `Retry-After`."""

    def __init__(self, meddelande: str, *, retry_after_s: int = 5) -> None:
        super().__init__(meddelande, status_kod=503)
        self.retry_after_s = int(retry_after_s)


def skriv_atomiskt_bytes(mal: Path, data: bytes) -> None:
    """Skriver `data` atomiskt till `mal` via unik tempfil i samma katalog + `os.replace`."""
    mal.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_namn = tempfile.mkstemp(prefix=f".{mal.name}.", suffix=".tmp", dir=mal.parent)
    tmp = Path(tmp_namn)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, mal)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def skriv_atomiskt_text(mal: Path, text: str) -> None:
    """Skriver UTF-8-text atomiskt till `mal` via tempfil i samma katalog + `os.replace`."""
    skriv_atomiskt_bytes(mal, text.encode("utf-8"))


def _spara_npy_atomiskt(mal: Path, arr: np.ndarray[Any, Any]) -> None:
    """Skriver en `.npy`-fil atomiskt via unik tempfil i samma katalog + `os.replace`."""
    mal.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_namn = tempfile.mkstemp(prefix=f".{mal.name}.", suffix=".tmp", dir=mal.parent)
    tmp = Path(tmp_namn)
    try:
        with os.fdopen(fd, "wb") as fh:
            np.save(fh, arr)
        os.replace(tmp, mal)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


_SCENARIOTEXT = {"fore": "före", "hojd": "höjd"}


def _scen(scenario_id: str) -> str:
    """Scenario-id som läsbar text (`nytt_hus` blir `nytt hus`)."""
    return _SCENARIOTEXT.get(scenario_id, scenario_id).replace("_", " ")


def _nu() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _plan_nyckel(plan: Plan) -> str:
    nycklar = "\n".join(sorted(j.nyckel for j in plan.jobb))
    return hashlib.sha256(nycklar.encode("utf-8")).hexdigest()


def _kontrollera_id(varde: str, vad: str) -> str:
    if not _ID.match(varde) or ".." in varde:
        raise FlodenWebbFel(f"Ogiltigt {vad}: {varde!r}.", status_kod=400)
    return varde


from sankgraf.avrinningsnat import STANDARD_MIN_YTA_M2  # noqa: E402

VAT_YTA_M = 0.02
"""Vattenyta: celler med större djup än så här (2 cm)."""
_SANKMATT = ("max_djup_m", "medel_djup_m", "vat_yta_m2", "volym_m3")


@dataclass
class Korning:
    """En körning av en fråga. Skrivs till `korning.json` vid varje statusbyte."""

    korning_id: str
    ref: str
    fraga_id: str
    fraga_fil: str
    mapp: Path
    status: str = "ko"  # ko | kor | klar | fel
    skapad: str = field(default_factory=_nu)
    start: str | None = None
    slut: str | None = None
    fel: str | None = None
    jobb_totalt: int = 0
    plan_nyckel: str | None = None
    """sha256 över planens jobbnycklar: samma nyckel betyder att körningen redan är gjord."""
    plan: Plan | None = None
    """Planen hålls i minnet så länge körningen pågår (för framstegsmätning)."""
    projekt_id: str | None = None
    """Projektet körningen hör till (valfritt tillägg i `korning.json`). Saknas det hör körningen
    inte till något projekt, utom demokörningarna som `demo.json` pekar ut."""

    @property
    def utmapp(self) -> Path:
        return self.mapp / "resultat"

    def som_dict(self) -> dict[str, Any]:
        projekt = {} if self.projekt_id is None else {"projekt_id": self.projekt_id}
        return {
            **projekt,
            "korning_id": self.korning_id,
            "ref": self.ref,
            "fraga_id": self.fraga_id,
            "fraga_fil": self.fraga_fil,
            "status": self.status,
            "skapad": self.skapad,
            "start": self.start,
            "slut": self.slut,
            "fel": self.fel,
            "fel_forklaring": forklara_fel(self.fel),
            "jobb_totalt": self.jobb_totalt,
            "plan_nyckel": self.plan_nyckel,
        }


class FragaTjanst:
    """Lista, planera, köra och läsa resultat för K20-frågor."""

    def __init__(
        self,
        *,
        rot: Path,
        fragemappar: list[Path],
        cachemapp: Path,
        korningsmapp: Path,
        demomapp: Path | None = None,
        projektrot: Path | None = None,
        max_ko: int = STANDARD_MAX_KO,
        exekutor: str | None = None,
        jobb_max_ram_mb: float | None = None,
        jobb_max_tid_s: float | None = None,
        min_ledig_disk_mb: float | None = None,
    ) -> None:
        if max_ko < 0:
            raise ValueError("max_ko måste vara >= 0.")
        self.demomapp = demomapp
        self.projektrot = projektrot
        """Rotmapp för kommunprojekt (`floden projekt skapa`): varje `<projekt>/fragor`
        under den räknas som en frågemapp, så att projektens frågor syns utan ändrad konfig."""
        self.rot = rot
        self.fragemappar = fragemappar
        self.cachemapp = cachemapp
        self.korningsmapp = korningsmapp
        self.max_ko = int(max_ko)
        self.exekutor_lage = las_exekutor_lage(exekutor)
        self.jobb_max_ram_mb = las_jobb_max_ram_mb(jobb_max_ram_mb)
        self.jobb_max_tid_s = las_jobb_max_tid_s(jobb_max_tid_s)
        self.min_ledig_disk_mb = las_min_ledig_disk_mb(min_ledig_disk_mb)
        self._cache: ResultatCache | None = None
        self._lock = threading.RLock()
        self._korningslas: dict[str, threading.RLock] = {}
        self._korningar: dict[str, Korning] = {}
        self._ko_ordning: list[str] = []
        self._aktiva_korningar: set[str] = set()
        self._stangd = False
        self._ko_snapshot: dict[str, int] = {"koade": 0, "pagaende": 0, "max_ko": self.max_ko}
        self._inlast = False
        self._mapp_stampel = -1
        self._subproc = SubprocHanterare()
        self._exekutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="floden-kor")

    def _uppdatera_ko_snapshot(self) -> None:
        """Byter ut den oföränderliga kö-ögonblicksbilden (anropas under `self._lock`)."""
        self._ko_snapshot = {
            "koade": len(self._ko_ordning),
            "pagaende": len(self._aktiva_korningar),
            "max_ko": self.max_ko,
        }

    def ko_status(self) -> dict[str, int]:
        """Låsfri ögonblicksbild av körkön för `GET /api/halsa`."""
        return dict(self._ko_snapshot)

    def _las_for_nyckel(self, nyckel: str) -> threading.RLock:
        """Finkornigt lås per nyckel (t.ex. körning eller enskilt vektorlager)."""
        with self._lock:
            las = self._korningslas.get(nyckel)
            if las is None:
                las = threading.RLock()
                self._korningslas[nyckel] = las
            return las

    def _las_for_korning(self, korning_id: str) -> threading.RLock:
        """Per-körningslås så tung PNG/mosaik/byggnads-rendering inte blockerar andra körningar."""
        return self._las_for_nyckel(korning_id)

    def stang(self, *, vanta: bool = True) -> None:
        """Stänger bakgrundsexekutorn och ev. barnprocesser rent (idempotent, inga zombies)."""
        with self._lock:
            if self._stangd:
                return
            self._stangd = True
        # Avbryt pågående barnprocesser FÖRE `shutdown(wait=True)` så arbetstråden inte hänger.
        self._subproc.avbryt_alla()
        self._exekutor.shutdown(wait=vanta, cancel_futures=True)
        with self._lock:
            kvar = list(self._ko_ordning)
            self._ko_ordning.clear()
            for kid in kvar:
                k = self._korningar.get(kid)
                if k is not None and k.status == "ko":
                    k.status = "fel"
                    k.fel = "Körningen avbröts när servern stoppades."
                    k.slut = k.slut or _nu()
                    k.plan = None
                    self._skriv_status(k)
            self._uppdatera_ko_snapshot()

    def _kontrollera_diskutrymme(self) -> None:
        """Pre-flight diskvakt: avvisar nya körningar med HTTP 507 om ledig disk är för låg."""
        if self.min_ledig_disk_mb <= 0.0:
            return
        sokvag = befintlig_sokvag(self.cachemapp)
        try:
            anvandning = shutil.disk_usage(sokvag)
        except OSError as exc:
            _logg.warning("Kunde inte kontrollera ledigt diskutrymme för %s: %s", sokvag, exc)
            return
        ledig_mb = anvandning.free / (1024.0 * 1024.0)
        if ledig_mb < self.min_ledig_disk_mb:
            raise FlodenWebbFel(
                f"Serverns diskutrymme är för lågt ({ledig_mb:.0f} MB ledigt, "
                f"kräver minst {self.min_ledig_disk_mb:.0f} MB). "
                "Frigör diskutrymme eller rensa gammal cache innan ny beräkning startas.",
                status_kod=507,
            )

    # ------------------------------------------------------------------ hjälpfunktioner

    def cache(self) -> ResultatCache:
        with self._lock:
            if self._cache is None:
                self._cache = ResultatCache(self.cachemapp)
            return self._cache

    def _visa(self, sokvag: Path) -> str:
        try:
            return sokvag.resolve().relative_to(self.rot.resolve()).as_posix()
        except ValueError:
            return str(sokvag)

    def _fragefiler(self) -> dict[str, Path]:
        """ref -> fil. Ref är filnamnet utan ändelse (med -2, -3 vid krock mellan mappar)."""
        ut: dict[str, Path] = {}
        mappar = list(self.fragemappar)
        if self.projektrot is not None and self.projektrot.is_dir():
            mappar += sorted(p for p in self.projektrot.glob("*/fragor") if p.is_dir())
        sedda: set[Path] = set()
        for mapp in mappar:
            if not mapp.is_dir():
                continue
            for fil in sorted([*mapp.glob("*.toml"), *mapp.glob("*.json")]):
                if fil.resolve() in sedda:
                    continue
                sedda.add(fil.resolve())
                bas = re.sub(r"[^a-z0-9_\-]+", "-", fil.stem.lower()).strip("-") or "fraga"
                ref, n = bas, 2
                while ref in ut:
                    ref = f"{bas}-{n}"
                    n += 1
                ut[ref] = fil
        return ut

    def _fil_for(self, ref: str) -> Path:
        _kontrollera_id(ref, "frågereferens")
        fil = self._fragefiler().get(ref)
        if fil is None:
            raise FlodenWebbFel(f"Okänd fråga '{ref}'.", status_kod=404)
        return fil

    def fragefil_eller_none(self, ref: str) -> Path | None:
        """Frågefilen för `ref`, eller `None` om frågan inte finns (kastar 400 vid ogiltigt id)."""
        _kontrollera_id(ref, "frågereferens")
        return self._fragefiler().get(ref)

    def _projekt_id_i_mapp(self, pmapp: Path, *, kraver_registerid: bool) -> str | None:
        """Projektets id ur `<projektmapp>/projekt.json` (`register_id` när inloggning är aktiv)."""
        try:
            d = json.loads((pmapp / "projekt.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict) or d.get("kontrakt") != "floden-projekt/v1":
            return None
        rid = d.get("register_id")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
        if not kraver_registerid:
            pid = d.get("projekt_id")
            if isinstance(pid, str) and pid.strip():
                return pid.strip()
        return None

    def agarprojekt_for_fil(
        self,
        ref: str,
        fil: Path | None = None,
        *,
        kraver_registerid: bool = False,
        demo_fragemapp: Path | None = None,
    ) -> str | None:
        """Ägarprojektet för frågan `ref` (eller `None` för fristående frågor utanför projekt).

        - Frågor under `<projektrot>/<mapp>/...` binds till projektets `register_id` ur
          `projekt.json` (eller `projekt_id` när `kraver_registerid` är falskt).
        - Demofrågor (`demo.json`, `demomapp`, eller `demo_fragemapp`) hör till `demokvarteret`.
        - Övriga frågor utanför projekt ger `None` (endast superadmin när inloggning är på).
        """
        if fil is None:
            fil = self._fragefiler().get(ref)
        if fil is not None:
            ren = fil.resolve()
            if self.projektrot is not None:
                ren_rot = self.projektrot.resolve()
                if ren.is_relative_to(ren_rot):
                    rel = ren.relative_to(ren_rot)
                    if len(rel.parts) >= 2:
                        return self._projekt_id_i_mapp(
                            ren_rot / rel.parts[0], kraver_registerid=kraver_registerid
                        )
                    return None
            if self.demomapp is not None and ren.is_relative_to(self.demomapp.resolve()):
                return "demokvarteret"
            if demo_fragemapp is not None and ren.is_relative_to(demo_fragemapp.resolve()):
                return "demokvarteret"
        demo = self.demo()
        delar = demo.get("delar", {}) if demo.get("finns") else {}
        if isinstance(delar, Mapping) and ref in delar:
            return "demokvarteret"
        return None

    @staticmethod
    def _inledande_kommentar(fil: Path) -> str:
        """Frågefilens inledande `#`-kommentar (så står byggkommandot för demodata där)."""
        if fil.suffix.lower() != ".toml":
            return ""
        rader: list[str] = []
        for rad in fil.read_text(encoding="utf-8").splitlines():
            if rad.startswith("#"):
                rader.append(rad[1:].removeprefix(" "))
            elif rad.strip() == "" and rader:
                break
            else:
                break
        return "\n".join(rader)

    def _beskriv(self, ref: str, fil: Path) -> dict[str, Any]:
        post: dict[str, Any] = {
            "ref": ref,
            "fil": self._visa(fil),
            "hjalp": self._inledande_kommentar(fil),
        }
        try:
            fraga = las_fraga(fil)
        except FragaFel as exc:
            return {**post, "fraga_id": ref, "beskrivning": "", "giltig": False, "fel": str(exc)}
        problem: list[str] = []
        analyser: list[dict[str, Any]] = []
        for a in fraga.analyser:
            info: dict[str, Any] | None = None
            try:
                info = losarinfo(hamta_losare(a.losare))
            except LosarFel as exc:
                problem.append(str(exc))
            analyser.append(
                {
                    "id": a.id,
                    "losare": a.losare,
                    "doman": None if info is None else info["doman"],
                    "visningsnamn": visningsnamn(a.losare),
                    "scenarier": list(a.scenarier),
                    "parametrar": dict(a.parametrar),
                    "beror_pa": list(a.beror_pa),
                }
            )
        scenarier: list[dict[str, Any]] = []
        saknas: list[str] = []
        for s in fraga.scenarier.values():
            for p in (s.bas, *s.k9, *s.lappar):
                if not p.exists():
                    saknas.append(self._visa(p))
            scenarier.append(
                {
                    "id": s.id,
                    "bas": self._visa(s.bas),
                    "k9": [self._visa(p) for p in s.k9],
                    "lappar": [self._visa(p) for p in s.lappar],
                }
            )
        return {
            **post,
            "fraga_id": fraga.fraga_id,
            "beskrivning": fraga.beskrivning,
            "giltig": not problem,
            "fel": "; ".join(problem) or None,
            "scenarier": scenarier,
            "analyser": analyser,
            "jamforelser": [
                {
                    "analys": j.analys,
                    "utdata": j.utdata,
                    "fore": j.fore,
                    "efter": j.efter,
                    "troskel": j.troskel,
                }
                for j in fraga.jamforelser
            ],
            "indata_saknas": sorted(set(saknas)),
        }

    def _las(self, ref: str) -> tuple[Path, KorbarFraga]:
        fil = self._fil_for(ref)
        try:
            return fil, las_fraga(fil)
        except FragaFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=422) from exc

    # ------------------------------------------------------------------ frågor och plan

    def demo(self) -> dict[str, Any]:
        """Demons översikt (`demo.json`, skriven av `floden demo`), eller `finns: false`."""
        fil = None if self.demomapp is None else self.demomapp / "demo.json"
        if fil is None or not fil.is_file():
            return {"finns": False}
        try:
            d: dict[str, Any] = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"finns": False}
        return {"finns": True, **d}

    def lista_fragor(
        self,
        *,
        filtrera: Callable[[str, Path], bool] | None = None,
    ) -> dict[str, Any]:
        demo_refs = {
            r
            for r, d in self.demo().get("delar", {}).items()
            if isinstance(d, dict) and "jobb" in d
        }
        fragor = [
            {**self._beskriv(ref, fil), "i_demon": ref in demo_refs}
            for ref, fil in self._fragefiler().items()
            if filtrera is None or filtrera(ref, fil)
        ]
        return {
            "fragemappar": [self._visa(m) for m in self.fragemappar],
            "fragor": fragor,
        }

    def hamta_fraga(self, ref: str) -> dict[str, Any]:
        return self._beskriv(ref, self._fil_for(ref))

    def _planera(self, fraga: KorbarFraga) -> Plan:
        try:
            return planera(fraga, self.cache())
        except _FRAGEFEL as exc:
            raise FlodenWebbFel(str(exc), status_kod=422) from exc
        except OSError as exc:
            raise FlodenWebbFel(f"Kunde inte planera frågan: {exc}", status_kod=422) from exc

    def planera_fraga(self, ref: str) -> dict[str, Any]:
        _, fraga = self._las(ref)
        plan = self._planera(fraga)
        return {
            "ref": ref,
            "fraga_id": fraga.fraga_id,
            "plan_nyckel": _plan_nyckel(plan),
            **_beskriv_plan(plan),
        }

    # ------------------------------------------------------------------ körningar

    def _skriv_status(self, k: Korning) -> None:
        skriv_atomiskt_text(
            k.mapp / "korning.json",
            json.dumps(k.som_dict(), indent=2, ensure_ascii=False) + "\n",
        )

    def _lasta_korningar(self) -> None:
        """Läser körregistret från disk: allt vid första anropet (en körning som stod som pågående
        då är avbruten), därefter bara körningar som tillkommit sedan sist (skrivna av en annan
        process, t.ex. `floden projekt skapa`). Mappens ändringstid avgör om det är värt
        att titta; en känd körning skrivs aldrig över härifrån."""
        with self._lock:
            forsta = not self._inlast
            self._inlast = True
            if not self.korningsmapp.is_dir():
                return
            try:
                stampel = self.korningsmapp.stat().st_mtime_ns
            except OSError:
                return
            if not forsta and stampel == self._mapp_stampel:
                return
            self._mapp_stampel = stampel
            for fil in sorted(self.korningsmapp.glob("*/korning.json")):
                if fil.parent.name in self._korningar:
                    continue
                try:
                    d = json.loads(fil.read_text(encoding="utf-8"))
                    k = Korning(
                        korning_id=d["korning_id"],
                        ref=d["ref"],
                        fraga_id=d["fraga_id"],
                        fraga_fil=d["fraga_fil"],
                        mapp=fil.parent,
                        status=d["status"],
                        skapad=d["skapad"],
                        start=d.get("start"),
                        slut=d.get("slut"),
                        fel=d.get("fel"),
                        jobb_totalt=int(d.get("jobb_totalt", 0)),
                        plan_nyckel=d.get("plan_nyckel"),
                        projekt_id=(
                            d["projekt_id"] if isinstance(d.get("projekt_id"), str) else None
                        ),
                    )
                except (OSError, ValueError, KeyError):
                    continue  # en trasig post hindrar inte de andra
                if forsta and k.status in ("ko", "kor"):
                    k.status = "fel"
                    k.fel = "Körningen avbröts när servern stoppades."
                    k.slut = k.slut or _nu()
                    self._skriv_status(k)
                self._korningar[k.korning_id] = k

    def korningar_for_projekt(self, projekt_id: str) -> list[Korning]:
        """Alla körningar (på disk och i minnet) vars `korning.json` har projektets id."""
        self._lasta_korningar()
        with self._lock:
            return [k for k in self._korningar.values() if k.projekt_id == projekt_id]

    def glom_korningar(self, korning_ids: list[str]) -> None:
        """Tar körningarna ur minnet (filerna har tagits bort av anroparen)."""
        with self._lock:
            for kid in korning_ids:
                self._korningar.pop(kid, None)
                self._korningslas.pop(kid, None)
                prefix = f"vektor:{kid}:"
                for nyckel in [k for k in self._korningslas if k.startswith(prefix)]:
                    self._korningslas.pop(nyckel, None)
                if kid in self._ko_ordning:
                    self._ko_ordning.remove(kid)
                self._aktiva_korningar.discard(kid)
            self._uppdatera_ko_snapshot()

    def _hitta_pagande(
        self, ref: str, projekt_id: str | None, plan_nyckel: str | None = None
    ) -> Korning | None:
        """Pågående/köad körning för `(ref, projekt_id)` och ev. `plan_nyckel`."""
        for k in self._korningar.values():
            # Återanvänd bara en pågående körning i samma projekt: annars skulle någon få ett
            # körnings-id som den inte har rätt att läsa. Skiljer sig `plan_nyckel` (t.ex. byte
            # från Standard till 10-årsregn på samma frågefil) startas en ny körning.
            if (
                k.ref == ref
                and k.status in ("ko", "kor")
                and k.projekt_id == projekt_id
                and (plan_nyckel is None or k.plan_nyckel is None or k.plan_nyckel == plan_nyckel)
            ):
                return k
        return None

    def starta_korning(self, ref: str, projekt_id: str | None = None) -> dict[str, Any]:
        fil, fraga = self._las(ref)
        self._lasta_korningar()
        with self._lock:
            if self._stangd:
                raise KoFullFel(
                    "Servern håller på att stängas ner och tar inte emot nya körningar."
                )
        self._kontrollera_diskutrymme()
        plan = self._planera(fraga)  # tydligt fel direkt, innan något köas
        plan_nyckel = _plan_nyckel(plan)
        korning_id = (
            f"{fraga.fraga_id}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        )
        k = Korning(
            korning_id=korning_id,
            ref=ref,
            fraga_id=fraga.fraga_id,
            fraga_fil=self._visa(fil),
            mapp=self.korningsmapp / korning_id,
            jobb_totalt=len(plan.jobb),
            plan_nyckel=plan_nyckel,
            plan=plan,
            projekt_id=projekt_id,
        )
        with self._lock:
            if self._stangd:
                raise KoFullFel(
                    "Servern håller på att stängas ner och tar inte emot nya körningar."
                )
            # Omkontroll under `_lock` efter `_planera` för att förhindra TOCTOU-dubbelkörning.
            befintlig = self._hitta_pagande(ref, projekt_id, plan_nyckel)
            if befintlig is not None:
                return {**self._status(befintlig), "ateranvand": True}
            if len(self._aktiva_korningar) + len(self._ko_ordning) >= 1 + self.max_ko:
                raise KoFullFel(
                    f"Körningskön är full ({len(self._ko_ordning)}/{self.max_ko} köade jobb). "
                    "Försök igen om en stund.",
                    retry_after_s=5,
                )
            self._korningar[korning_id] = k
            self._ko_ordning.append(korning_id)
            self._skriv_status(k)
            self._uppdatera_ko_snapshot()
            try:
                self._exekutor.submit(self._kor_fran_ko, k, fraga, plan)
            except RuntimeError as exc:
                self._korningar.pop(korning_id, None)
                if korning_id in self._ko_ordning:
                    self._ko_ordning.remove(korning_id)
                self._uppdatera_ko_snapshot()
                raise KoFullFel(
                    "Servern håller på att stängas ner och tar inte emot nya körningar."
                ) from exc
            return {**self._status(k), "ateranvand": False}

    def _kor_synkront_fraga(
        self,
        ref: str,
        fil: Path,
        fraga: KorbarFraga,
        korning_id: str,
        projekt_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self._lasta_korningar()
        plan = self._planera(fraga)
        plan_nyckel = _plan_nyckel(plan)
        _kontrollera_id(korning_id, "körnings-id")
        with self._lock:
            gammal = self._korningar.get(korning_id)
            if (
                gammal is not None
                and gammal.status == "klar"
                and gammal.plan_nyckel == plan_nyckel
                and (gammal.utmapp / "sammanstallning.json").is_file()
            ):
                return self._status(gammal), True
            self._kontrollera_diskutrymme()
            mapp = self.korningsmapp / korning_id
            if mapp.exists():
                shutil.rmtree(mapp)
            k = Korning(
                korning_id=korning_id,
                ref=ref,
                fraga_id=fraga.fraga_id,
                fraga_fil=self._visa(fil),
                mapp=mapp,
                jobb_totalt=len(plan.jobb),
                plan_nyckel=plan_nyckel,
                plan=plan,
                projekt_id=projekt_id,
            )
            self._korningar[korning_id] = k
            self._skriv_status(k)
        self._kor(k, fraga, plan)
        if k.status != "klar":
            raise FlodenWebbFel(f"Körningen {korning_id} misslyckades: {k.fel}", status_kod=500)
        return self._status(k), False

    def kor_synkront(
        self, ref: str, korning_id: str, projekt_id: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Kör frågan i anropets tråd under ett fast `korning_id` (för `floden demo`).

        Är en körning med samma id redan klar för exakt samma plan (samma jobbnycklar) lämnas den
        orörd och `(status, True)` returneras; annars körs frågan (jobb som finns i cachen körs
        inte om) och resultatet ersätter den gamla körningen. Fel kastas som `FlodenWebbFel`.
        """
        fil, fraga = self._las(ref)
        return self._kor_synkront_fraga(ref, fil, fraga, korning_id, projekt_id)

    def kor_fil_synkront(
        self, fil: Path, korning_id: str, projekt_id: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Kör en K20-frågefil synkront utan att registrera den i publika `fragemappar`.

        Används av läsande analyser (t.ex. `GET /api/analys/ekologi`) som skriver till en intern
        cachekatalog utan att mutera projektets mappar eller scenarier.
        """
        try:
            fraga = las_fraga(fil)
        except FragaFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=422) from exc
        ref = re.sub(r"[^a-z0-9_\-]+", "-", fil.stem.lower()).strip("-") or "fraga"
        return self._kor_synkront_fraga(ref, fil, fraga, korning_id, projekt_id)

    def _kor_fran_ko(self, k: Korning, fraga: KorbarFraga, plan: Plan) -> None:
        with self._lock:
            if k.korning_id in self._ko_ordning:
                self._ko_ordning.remove(k.korning_id)
            self._aktiva_korningar.add(k.korning_id)
            self._uppdatera_ko_snapshot()
        try:
            self._kor(k, fraga, plan)
        finally:
            with self._lock:
                self._aktiva_korningar.discard(k.korning_id)
                self._uppdatera_ko_snapshot()

    def _kor(self, k: Korning, fraga: KorbarFraga, plan: Plan) -> None:
        with self._lock:
            if self._stangd:
                k.status, k.slut = "fel", k.slut or _nu()
                k.fel = "Körningen avbröts när servern stoppades."
                k.plan = None
                self._skriv_status(k)
                return
            k.status, k.start = "kor", _nu()
            self._skriv_status(k)
        try:
            if self.exekutor_lage == "process":
                fraga_fil = fraga.kalla
                if fraga_fil is None:
                    raise FlodenWebbFel(
                        "Frågan saknar källfil och kan inte köras i separat barnprocess.",
                        status_kod=500,
                    )
                kor_fraga_i_barnprocess(
                    fraga_fil=fraga_fil,
                    cachemapp=self.cachemapp,
                    utmapp=k.utmapp,
                    status_fil=k.mapp / "framsteg.json",
                    max_ram_mb=self.jobb_max_ram_mb,
                    max_tid_s=self.jobb_max_tid_s,
                    hanterare=self._subproc,
                )
            else:
                kor_utredning(fraga, self.cache(), k.utmapp, plan=plan)
        except SubprocAvbrutenFel:
            with self._lock:
                k.status, k.slut = "fel", _nu()
                k.fel = "Körningen avbröts när servern stoppades."
                k.plan = None
                self._skriv_status(k)
            return
        except SubprocFel as exc:
            fel_typ = exc.ursprunglig_typ or type(exc).__name__
            with self._lock:
                k.status, k.slut, k.fel = "fel", _nu(), f"{fel_typ}: {exc}"
                k.plan = None
                self._skriv_status(k)
            return
        except Exception as exc:  # noqa: BLE001 - felet sparas och visas, inget sväljs
            with self._lock:
                k.status, k.slut, k.fel = "fel", _nu(), f"{type(exc).__name__}: {exc}"
                k.plan = None
                self._skriv_status(k)
            return
        with self._lock:
            k.status, k.slut, k.plan = "klar", _nu(), None
            self._skriv_status(k)

    def _hamta(self, korning_id: str) -> Korning:
        _kontrollera_id(korning_id, "körnings-id")
        self._lasta_korningar()
        with self._lock:
            k = self._korningar.get(korning_id)
        if k is None:
            raise FlodenWebbFel(f"Okänd körning '{korning_id}'.", status_kod=404)
        return k

    def korning_eller_none(self, korning_id: str) -> Korning | None:
        """Körningen om den finns, annars None (utan fel; för behörighetskontroller)."""
        try:
            return self._hamta(korning_id)
        except FlodenWebbFel:
            return None

    def _status(self, k: Korning) -> dict[str, Any]:
        with self._lock:
            bas = k.som_dict()
            status = k.status
            plan = k.plan
            jobb_totalt = k.jobb_totalt
            ko_langd = len(self._ko_ordning)
            ko_plats = (
                self._ko_ordning.index(k.korning_id) + 1
                if status == "ko" and k.korning_id in self._ko_ordning
                else None
            )
        if status == "klar":
            klara = jobb_totalt
        else:
            klara = 0
            if plan is not None:
                # Antal jobb i cachen: kärnans cache är facit för vad som är klart.
                klara = sum(1 for j in plan.jobb if self.cache().finns("jobb", j.nyckel))
            framsteg = las_framsteg(k.mapp / "framsteg.json")
            if framsteg is not None:
                klara_fil = framsteg.get("klara")
                if (
                    isinstance(klara_fil, int)
                    and not isinstance(klara_fil, bool)
                    and klara_fil >= 0
                ):
                    klara = max(klara, min(klara_fil, jobb_totalt))
        ut: dict[str, Any] = {**bas, "framsteg": {"klara": klara, "totalt": jobb_totalt}}
        if status == "ko":
            ut["ko_plats"] = ko_plats
            ut["ko_langd"] = ko_langd
        return ut

    def korning_status(self, korning_id: str) -> dict[str, Any]:
        return self._status(self._hamta(korning_id))

    def lista_korningar(self) -> dict[str, Any]:
        self._lasta_korningar()
        with self._lock:
            alla = sorted(self._korningar.values(), key=lambda k: k.skapad, reverse=True)
        return {"korningar": [self._status(k) for k in alla]}

    # ------------------------------------------------------------------ resultat

    def _sammanstallning(self, k: Korning) -> dict[str, Any]:
        if k.status == "fel":
            raise FlodenWebbFel(f"Körningen misslyckades: {k.fel}", status_kod=409)
        if k.status != "klar":
            raise FlodenWebbFel(f"Körningen är inte klar (status: {k.status}).", status_kod=409)
        try:
            data = json.loads(
                (k.utmapp / "sammanstallning.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise FlodenWebbFel(f"Resultatet kan inte läsas: {exc}", status_kod=500) from exc
        if not isinstance(data, dict):
            raise FlodenWebbFel(
                "Resultatet kan inte läsas: sammanstallning.json är inte ett JSON-objekt.",
                status_kod=500,
            )
        return data

    def _jobbmapp(self, k: Korning, j: dict[str, Any]) -> Path:
        mapp: Path = k.utmapp / str(j["analys"]) / str(j["scenario"])
        return mapp / str(j["ruta"]) if j.get("ruta") else mapp

    def _roller(self, k: Korning, j: dict[str, Any]) -> dict[str, str]:
        """roll -> filnamn för ett jobb, ur dess run.json (K0).

        Saknad eller oläsbar `run.json` (t.ex. äldre eller avbruten körning på disk) loggar en
        varning och returnerar `{}` i stället för att krascha med HTTP 500.
        """
        fil = self._jobbmapp(k, j) / "run.json"
        try:
            manifest = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _logg.warning(
                "run.json saknas eller går inte att läsa för körning %s (%s/%s): %s",
                k.korning_id,
                j.get("analys"),
                j.get("scenario"),
                exc,
            )
            return {}
        if not isinstance(manifest, dict):
            _logg.warning(
                "run.json har ogiltigt format för körning %s (%s/%s)",
                k.korning_id,
                j.get("analys"),
                j.get("scenario"),
            )
            return {}
        utdata = manifest.get("utdata", [])
        if not isinstance(utdata, list):
            return {}
        return {
            str(p["roll"]): str(p["fil"])
            for p in utdata
            if isinstance(p, dict)
            and isinstance(p.get("roll"), str)
            and isinstance(p.get("fil"), str)
        }

    def _url_fil(self, k: Korning, rel: Path) -> str:
        return f"/api/korningar/{k.korning_id}/fil/{rel.relative_to(k.utmapp).as_posix()}"

    def resultat(self, korning_id: str) -> dict[str, Any]:
        k = self._hamta(korning_id)
        s = self._sammanstallning(k)
        lager, lagervarningar = self._lager(k, s)
        byggnader, byggnadsvarning = self._byggnader(k, s, lager)
        if byggnadsvarning:
            lagervarningar = [*lagervarningar, byggnadsvarning]
        projekt_poly = self._projektpolygon(k)
        byggnader = self._markera_projektbyggnader(byggnader, projekt_poly)
        return {
            "korning": self._status(k),
            "fraga_id": s["fraga_id"],
            "beskrivning": s.get("beskrivning", ""),
            "sammanfattning": s["sammanfattning"],
            "scenarier": [
                {"id": sid, "nyckel": v["nyckel"], "rutor": len(v["rutor"])}
                for sid, v in s["scenarier"].items()
            ],
            "tabeller": _matvardestabeller(s),
            "jamforelser": [
                {k2: v for k2, v in jf.items() if k2 not in ("per_ruta", "mapp")}
                for jf in s["jamforelser"]
            ],
            "varningar": [*_varningar(s), *(_webbvarning(t) for t in lagervarningar)],
            "lager": lager,
            "byggnader": byggnader,
            "projekt_avgransning": (
                self.projekt_avgransning(korning_id) if projekt_poly is not None else None
            ),
            "antaganden": self._antaganden(k, s),
            "datatabeller": self._datatabeller(k, s),
            "vektorlager": self._vektorlager(k, s),
            "tidsserie_scenarier": self._tidsserie_scenarier(k, s),
        }

    def _tidsserie_scenarier(self, k: Korning, s: dict[str, Any]) -> list[str]:
        """Sorterad lista av scenario-id i körningen som har `tidsserie.json`."""
        scenarier = {
            str(j["scenario"])
            for j in s.get("jobb", [])
            if (self._jobbmapp(k, j) / "tidsserie.json").is_file()
        }
        return sorted(scenarier)

    def _tidsserie_jobb(
        self, k: Korning, s: dict[str, Any], scenario: str | None, filnamn: str
    ) -> tuple[str, list[dict[str, Any]]]:
        tillgangliga = self._tidsserie_scenarier(k, s)
        if not tillgangliga:
            raise FlodenWebbFel("Körningen har ingen dynamisk tidsserie.", status_kod=404)
        if scenario is None or scenario == "":
            sid = tillgangliga[0]
        else:
            sid = _kontrollera_id(scenario, "scenario")
        jobb = [
            j
            for j in s.get("jobb", [])
            if str(j["scenario"]) == sid and (self._jobbmapp(k, j) / filnamn).is_file()
        ]
        if not jobb:
            raise FlodenWebbFel(
                f"Scenario '{sid}' saknar dynamisk tidsserie i körningen.", status_kod=404
            )
        return sid, jobb

    def _utbredning_for_jobb(self, k: Korning, jobb: list[dict[str, Any]]) -> dict[str, float]:
        x_mins: list[float] = []
        y_mins: list[float] = []
        x_maxs: list[float] = []
        y_maxs: list[float] = []
        for j in jobb:
            tif = self._jobbmapp(k, j) / "max_djup.tif"
            if not tif.is_file():
                continue
            with rasterio.open(tif) as ds:
                b = ds.bounds
                x_mins.append(float(b.left))
                y_mins.append(float(b.bottom))
                x_maxs.append(float(b.right))
                y_maxs.append(float(b.top))
        if not x_mins:
            raise FlodenWebbFel("Kunde inte läsa utbredning för tidsserien.", status_kod=500)
        return {
            "x_min": min(x_mins),
            "y_min": min(y_mins),
            "x_max": max(x_maxs),
            "y_max": max(y_maxs),
        }

    def tidsserie(self, korning_id: str, scenario: str | None = None) -> dict[str, Any]:
        """Returnerar `tidsserie.json` kompletterad med `utbredning` och `tidssteg`-länkar."""
        k = self._hamta(korning_id)
        s = self._sammanstallning(k)
        sid, jobb = self._tidsserie_jobb(k, s, scenario, "tidsserie.json")
        try:
            delar = [
                json.loads((self._jobbmapp(k, j) / "tidsserie.json").read_text(encoding="utf-8"))
                for j in jobb
            ]
        except (OSError, ValueError) as exc:
            raise FlodenWebbFel(f"Tidsserien kunde inte läsas: {exc}", status_kod=500) from exc
        data: dict[str, Any] = dict(delar[0])
        if len(delar) > 1:
            # Flerrutskörning: aggregera ytor, volymer och maxdjup per samplad tidpunkt.
            bas_punkter = [dict(p) for p in data.get("punkter", [])]
            for extra in delar[1:]:
                extra_punkter = extra.get("punkter", [])
                for p0, p1 in zip(bas_punkter, extra_punkter, strict=False):
                    p0["max_djup_m"] = round(
                        max(float(p0["max_djup_m"]), float(p1["max_djup_m"])), 4
                    )
                    p0["vat_yta_01m_m2"] = round(
                        float(p0["vat_yta_01m_m2"]) + float(p1["vat_yta_01m_m2"]), 2
                    )
                    p0["vat_yta_03m_m2"] = round(
                        float(p0["vat_yta_03m_m2"]) + float(p1["vat_yta_03m_m2"]), 2
                    )
                    p0["volym_m3"] = round(float(p0["volym_m3"]) + float(p1["volym_m3"]), 3)
                    p0["utflode_kum_m3"] = round(
                        float(p0["utflode_kum_m3"]) + float(p1["utflode_kum_m3"]), 3
                    )
            data["punkter"] = bas_punkter
        utbredning = self._utbredning_for_jobb(k, jobb)
        tidssteg = [
            {
                "index": i,
                "tid_min": float(t_min),
                "png_url": f"/api/korningar/{korning_id}/tidssteg/{sid}/{i}.png",
            }
            for i, t_min in enumerate(data.get("tidssteg_tid_min", []))
        ]
        return {
            **data,
            "scenario": sid,
            "utbredning": utbredning,
            "tidssteg": tidssteg,
        }

    def tidssteg_png(self, korning_id: str, scenario: str, index: int) -> bytes:
        """Renderar ett tidssteg ur `tidssteg.npz` som transparent vatten-PNG och cachar på disk."""
        k = self._hamta(korning_id)
        sid = _kontrollera_id(scenario, "scenario")
        if index < 0:
            raise FlodenWebbFel(f"Ogiltigt tidsstegsindex: {index}.", status_kod=400)
        cachefil = k.mapp / "webb" / "tidssteg" / f"{sid}_{index}.png"
        las = self._las_for_korning(k.korning_id)
        with las:
            if cachefil.is_file():
                return cachefil.read_bytes()
        s = self._sammanstallning(k)
        _, jobb = self._tidsserie_jobb(k, s, sid, "tidssteg.npz")
        delar: list[tuple[np.ndarray[Any, Any], float, float, float]] = []
        px: float | None = None
        for j in jobb:
            jmapp = self._jobbmapp(k, j)
            try:
                with np.load(jmapp / "tidssteg.npz") as npz:
                    stack = npz["djup_mm"]
                    if index >= int(stack.shape[0]):
                        max_idx = int(stack.shape[0]) - 1
                        raise FlodenWebbFel(
                            f"Tidsstegsindex {index} ligger utanför [0, {max_idx}].",
                            status_kod=404,
                        )
                    djup_m = np.asarray(stack[index], dtype=np.float32) / 1000.0
                    faktor = int(npz["nedsampling"]) if "nedsampling" in npz else 1
                with rasterio.open(jmapp / "max_djup.tif") as ds:
                    b = ds.bounds
                    cell = float(ds.transform.a) * max(1, faktor)
                    x_min, y_max = float(b.left), float(b.top)
            except FlodenWebbFel:
                raise
            except (OSError, ValueError, KeyError, rasterio.errors.RasterioError) as exc:
                raise FlodenWebbFel(f"Tidssteget kunde inte läsas: {exc}", status_kod=500) from exc
            if px is None:
                px = cell
            elif abs(px - cell) > 1e-6:
                raise FlodenWebbFel("Tidsstegen har olika upplösning mellan rutor.", status_kod=500)
            delar.append((djup_m, x_min, y_max, cell))
        assert px is not None
        if len(delar) == 1:
            arr, x0, y1, _ = delar[0]
            h, b_px = arr.shape
            utbredning = Utbredning(x0, y1 - h * px, x0 + b_px * px, y1)
            mosaik = Mosaik(arr, utbredning, px)
        else:
            x0 = min(x for _, x, _, _ in delar)
            y1 = max(y for _, _, y, _ in delar)
            x1 = max(x + a.shape[1] * px for a, x, _, _ in delar)
            y0 = min(y - a.shape[0] * px for a, _, y, _ in delar)
            bredd = round((x1 - x0) / px)
            hojd = round((y1 - y0) / px)
            ut = np.full((hojd, bredd), np.nan, dtype=np.float32)
            djupast = np.full((hojd, bredd), -np.inf, dtype=np.float32)
            for arr, x, y, _ in delar:
                c0i = round((x - x0) / px)
                r0i = round((y1 - y) / px)
                h, b_px = arr.shape
                rr = np.arange(h, dtype=np.float32)
                cc = np.arange(b_px, dtype=np.float32)
                avst = np.minimum(
                    np.minimum(rr[:, None] + 0.5, h - rr[:, None] - 0.5),
                    np.minimum(cc[None, :] + 0.5, b_px - cc[None, :] - 0.5),
                )
                fonster = (slice(r0i, r0i + h), slice(c0i, c0i + b_px))
                battre = avst > djupast[fonster]
                ut[fonster] = np.where(battre, arr, ut[fonster])
                djupast[fonster] = np.where(battre, avst, djupast[fonster])
            mosaik = Mosaik(ut, Utbredning(x0, y0, x1, y1), px)

        tak: Any = None
        rutor_per_scenario, _ = self._scenariorutor(k, s)
        if rutor_per_scenario is not None:
            mappar = list(rutor_per_scenario.get(sid, {}).values())
            if mappar:
                try:
                    tak = tak_mask(mosaik, mappar, takkalla_for_roll("max_djup"))
                except (LagerFel, OSError, ValueError):
                    tak = None
        har_tak = tak is not None and bool(tak.any())
        bild = rita_bild(
            mosaik.raster,
            "vatten",
            lo=0.0,
            hi=1.0,
            dold=tak if har_tak else None,
        )
        with las:
            skriv_atomiskt_bytes(cachefil, bild.png)
        return bild.png

    @staticmethod
    def _markera_projektbyggnader(
        byggnader: dict[str, Any] | None, poly: Polygon | None
    ) -> dict[str, Any] | None:
        """Lägger `i_projektet` (sant/falskt) på varje byggnad och `antal_i_projektet` per scenario
        när projektet har en polygon. En byggnad räknas till projektet om fotavtryckets inre punkt
        är inne i polygonen. Byggnader utanför visas fortfarande (de ger skuggor och hinder) men
        räknas inte. Utan polygon returneras byggnaderna orörda."""
        if poly is None or byggnader is None:
            return byggnader
        ut: dict[str, Any] = {**byggnader, "avgransad_till_projektet": True, "scenarier": {}}
        antal: dict[str, int] = {}
        for sid, lista in byggnader["scenarier"].items():
            ny = [
                {**b, "i_projektet": avgr.byggnad_inne(poly, b["ring"], b.get("hal", ()))}
                for b in lista
            ]
            ut["scenarier"][sid] = ny
            antal[sid] = sum(1 for b in ny if b["i_projektet"])
        ut["antal_i_projektet"] = antal
        return ut

    # Lager -------------------------------------------------------------------------------

    def _lager(self, k: Korning, s: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        cachefil = k.mapp / "webb" / "lager.json"
        with self._las_for_korning(k.korning_id):
            if cachefil.is_file():
                d = json.loads(cachefil.read_text(encoding="utf-8"))
                if d.get("version") == LAGER_VERSION:
                    return d["lager"], d["varningar"]
            lager, varningar = self._bygg_lager(k, s)
            skriv_atomiskt_text(
                cachefil,
                json.dumps(
                    {"version": LAGER_VERSION, "lager": lager, "varningar": varningar},
                    ensure_ascii=False,
                )
                + "\n",
            )
            return lager, varningar

    def _bygg_lager(self, k: Korning, s: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        ut = k.mapp / "webb"
        ut.mkdir(parents=True, exist_ok=True)
        varningar: list[str] = []
        scenarioordning = list(s["scenarier"])
        fore = {jf["fore"] for jf in s["jamforelser"]}
        efter = {jf["efter"] for jf in s["jamforelser"]}

        def typ(sid: str) -> str:
            if sid in efter:
                return "scenario"
            return "bas" if sid in fore or sid == scenarioordning[0] else "scenario"

        # (analys, scenario) -> jobb
        grupper: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for j in s["jobb"]:
            grupper[(j["analys"], j["scenario"])].append(j)

        # Ämnet är lösarens namnrymd (`dagsljus.svf_mark` -> dagsljus), som lösarnas `doman`.
        doman_for: dict[str, str] = {}
        for j in s["jobb"]:
            doman_for.setdefault(j["analys"], str(j["losare"]).split(".", 1)[0])

        poster: list[dict[str, Any]] = []  # lager före rendering
        for (analys, sid), jobb in grupper.items():
            roller: dict[str, str] = {}
            for j_kand in jobb:
                roller = self._roller(k, j_kand)
                if roller:
                    break
            if not roller:
                varningar.append(
                    f"Utdata saknas för {analys}/{sid} (run.json saknas eller kunde inte läsas)."
                )
                continue
            try:
                primar_ordning = list(hamta_losare(jobb[0]["losare"]).utdataroller)
            except LosarFel:
                primar_ordning = list(roller)
            for roll, namn in roller.items():
                if not namn.lower().endswith((".tif", ".tiff")):
                    continue
                filer = [self._jobbmapp(k, j) / namn for j in jobb]
                poster.append(
                    {
                        "id": f"{analys}.{sid}.{roll}",
                        "analys": analys,
                        "scenario": sid,
                        "roll": roll,
                        "typ": typ(sid),
                        "primar": bool(primar_ordning) and roll == primar_ordning[0],
                        "filer": filer,
                        "ruta_ider": [j.get("ruta") for j in jobb],
                        "skillnad": False,
                        "troskel": 0.0,
                    }
                )
        for jf in s["jamforelser"]:
            mapp = Path(k.utmapp / "jamforelser" / jf["namn"])
            filer = sorted(mapp.glob("*.tif"))
            poster.append(
                {
                    "id": f"skillnad.{jf['namn']}",
                    "analys": jf["analys"],
                    "scenario": jf["efter"],
                    "roll": jf["utdata"],
                    "typ": "skillnad",
                    "primar": True,
                    "filer": filer,
                    "ruta_ider": [None if f.stem == "diff" else f.stem for f in filer],
                    "skillnad": True,
                    "troskel": float(jf["troskel"]),
                    "fore": jf["fore"],
                    "efter": jf["efter"],
                }
            )

        mosaiker: dict[str, Mosaik] = {}
        for p in poster:
            try:
                mosaiker[p["id"]] = sammanfoga(p["filer"])
            except (LagerFel, OSError, ValueError) as exc:
                varningar.append(f"Lagret {p['id']} kunde inte visas: {exc}")
        # Deterministisk härledd riskroll `fara = max_djup * hastighet` (m²/s) för 2D-körningar,
        # med bevarad NaN där endera saknas. Skapas före skillnadsloop så även `fara` får skillnad.
        per_as_roll: dict[tuple[str, str, str], dict[str, Any]] = {
            (str(p["analys"]), str(p["scenario"]), str(p["roll"])): p
            for p in poster
            if not p["skillnad"] and p["id"] in mosaiker
        }
        for (analys_id, scen_id, roll), p_djup in list(per_as_roll.items()):
            if roll != "max_djup" or (analys_id, scen_id, "fara") in per_as_roll:
                continue
            p_hast = per_as_roll.get((analys_id, scen_id, "hastighet"))
            if p_hast is None:
                continue
            fara_m = fara_mosaik(mosaiker[p_djup["id"]], mosaiker[p_hast["id"]])
            if fara_m is None:
                continue
            fara_id = f"{analys_id}.{scen_id}.fara"
            mosaiker[fara_id] = fara_m
            poster.append(
                {
                    "id": fara_id,
                    "analys": analys_id,
                    "scenario": scen_id,
                    "roll": "fara",
                    "typ": p_djup["typ"],
                    "primar": False,
                    "filer": [],
                    "ruta_ider": [],
                    "skillnad": False,
                    "troskel": 0.0,
                    "harledd": True,
                }
            )
        # Roller som frågan inte jämför får ändå en skillnad (efter minus före, cell för cell),
        # så att varje roll går att se som före, efter och skillnad.
        jamforda = {(p["analys"], p["roll"], p["scenario"]) for p in poster if p["skillnad"]}
        basposter = {
            (p["analys"], p["roll"]): p for p in poster if p["typ"] == "bas" and not p["skillnad"]
        }
        for p in [q for q in poster if q["typ"] == "scenario" and not q["skillnad"]]:
            bas = basposter.get((p["analys"], p["roll"]))
            if bas is None or (p["analys"], p["roll"], p["scenario"]) in jamforda:
                continue
            if p["roll"] in KONTROLLROLLER or bas["id"] not in mosaiker or p["id"] not in mosaiker:
                continue
            diff = skillnad_mosaik(mosaiker[bas["id"]], mosaiker[p["id"]])
            if diff is None:
                continue
            nytt_id = f"skillnad.{p['analys']}.{p['roll']}.{p['scenario']}"
            mosaiker[nytt_id] = diff
            poster.append(
                {
                    "id": nytt_id,
                    "analys": p["analys"],
                    "scenario": p["scenario"],
                    "roll": p["roll"],
                    "typ": "skillnad",
                    "primar": False,
                    "filer": [],
                    "ruta_ider": [],
                    "skillnad": True,
                    "troskel": 1e-6,
                    "fore": bas["scenario"],
                    "efter": p["scenario"],
                    "harledd": True,
                }
            )
        # Gemensam skala per (analys, roll) så att bas och scenario går att jämföra.
        skalor: dict[tuple[str, str], tuple[float, float]] = {}
        for nyckel in {(p["analys"], p["roll"]) for p in poster if not p["skillnad"]}:
            ras = [
                mosaiker[p["id"]].raster
                for p in poster
                if (p["analys"], p["roll"]) == nyckel and not p["skillnad"] and p["id"] in mosaiker
            ]
            skalor[nyckel] = varde_intervall(ras)

        rutor_per_scenario, rutvarning = self._scenariorutor(k, s)
        if rutvarning:
            varningar.append(rutvarning)
        lager: list[dict[str, Any]] = []
        for p in poster:
            m = mosaiker.get(p["id"])
            if m is None:
                continue
            etikett, enhet = etikett_och_enhet(p["roll"])
            if p["skillnad"]:
                lo, hi = varde_intervall([m.raster])
                lo, hi = 0.0, max(abs(lo), abs(hi))
                namn = f"{etikett} · {_scen(p['efter'])} − {_scen(p['fore'])}"
            else:
                lo, hi = skalor[(p["analys"], p["roll"])]
                namn = f"{etikett} · {_scen(p['scenario'])}"
            farg = farg_for_roll(p["roll"], skillnad=p["skillnad"], lo=lo, hi=hi)
            # Byggnadernas tak är inte mark: maskas i markrastren (soltimmar, SVF, djup). Skillnaden
            # maskar fotavtrycket i före och efter, så att ett nytt hus inte syns som ändrad mark.
            tak: Any = None
            if rutor_per_scenario is not None and p["roll"] in MASKADE_ROLLER:
                scen = [p["fore"], p["efter"]] if p["skillnad"] else [p["scenario"]]
                try:
                    mappar = [
                        mapp for sid in scen for mapp in rutor_per_scenario.get(sid, {}).values()
                    ]
                    tak = tak_mask(m, mappar, takkalla_for_roll(p["roll"]))
                except (LagerFel, OSError, ValueError) as exc:
                    varningar.append(f"Takmasken för {p['id']} kunde inte byggas: {exc}")
            har_tak = tak is not None and bool(tak.any())
            bild = rita_bild(
                m.raster,
                farg,
                lo=lo,
                hi=hi,
                troskel=float(p["troskel"]),
                dold=tak if har_tak else None,
            )
            skriv_atomiskt_bytes(ut / f"{p['id']}.png", bild.png)
            png_tak = None
            if har_tak:
                med = rita_bild(m.raster, farg, lo=lo, hi=hi, troskel=float(p["troskel"]))
                skriv_atomiskt_bytes(ut / f"{p['id']}.tak.png", med.png)
                png_tak = f"/api/korningar/{k.korning_id}/lager/{p['id']}.tak.png"
            _spara_npy_atomiskt(ut / f"{p['id']}.npy", m.raster)
            if tak is not None:
                _spara_npy_atomiskt(ut / f"{p['id']}.tak.npy", tak)
            faktor = bild.faktor
            if faktor > 1:
                varningar.append(
                    f"Förhandsvisningen av {p['id']} är nedsamplad {faktor} gånger; "
                    "GeoTIFF-filerna har full upplösning."
                )
            u = m.utbredning
            synlig = None
            if bild.synlig_px is not None:
                c0, r0, c1, r1 = bild.synlig_px
                cell = m.pixelstorlek_m * faktor
                synlig = {
                    "x_min": u.x_min + c0 * cell,
                    "x_max": min(u.x_max, u.x_min + c1 * cell),
                    "y_max": u.y_max - r0 * cell,
                    "y_min": max(u.y_min, u.y_max - r1 * cell),
                }
            lager.append(
                {
                    "id": p["id"],
                    "namn": namn,
                    "etikett": etikett,
                    "analys": p["analys"],
                    "scenario": p["scenario"],
                    "roll": p["roll"],
                    "typ": p["typ"],
                    "primar": p["primar"],
                    "doman": doman_for.get(p["analys"], ""),
                    "kontroll": p["roll"] in KONTROLLROLLER,
                    "harledd": bool(p.get("harledd")),
                    "enhet": enhet,
                    "farg": farg,
                    "skala": {"min": lo, "max": hi},
                    "teckenforklaring": teckenforklaring(farg, lo, hi, float(p["troskel"])),
                    "utbredning": m.utbredning.som_dict(),
                    "synlig_utbredning": synlig,
                    "pixelstorlek_m": m.pixelstorlek_m,
                    "bredd_px": bild.bredd_px,
                    "hojd_px": bild.hojd_px,
                    "nedsampling": faktor,
                    "png": f"/api/korningar/{k.korning_id}/lager/{p['id']}.png",
                    "png_med_tak": png_tak,
                    "tak_maskat": har_tak,
                    "tak_celler": int(tak.sum()) if tak is not None else 0,
                    "tak_yta_m2": (
                        float(tak.sum()) * m.pixelstorlek_m**2 if tak is not None else 0.0
                    ),
                    "geotiff": [
                        {"ruta": rid, "fil": f.name, "url": self._url_fil(k, f)}
                        for rid, f in zip(p["ruta_ider"], p["filer"], strict=True)
                    ],
                    **({"fore": p["fore"], "efter": p["efter"]} if p["skillnad"] else {}),
                }
            )
        ordning = {"bas": 0, "scenario": 1, "skillnad": 2}
        lager.sort(key=lambda x: (not x["primar"], x["analys"], ordning[x["typ"]], x["id"]))
        return lager, varningar

    def lager_png(self, korning_id: str, lager_id: str) -> bytes:
        k = self._hamta(korning_id)
        _kontrollera_id(lager_id, "lager-id")
        self.resultat(korning_id)  # ritar lagren första gången
        fil = k.mapp / "webb" / f"{lager_id}.png"
        if not fil.is_file():
            raise FlodenWebbFel(f"Okänt lager '{lager_id}'.", status_kod=404)
        return fil.read_bytes()

    def vektor(
        self,
        korning_id: str,
        scenario: str,
        slag: str,
        *,
        min_yta_m2: float | None = None,
        min_volym_m3: float | None = None,
        min_djup_m: float | None = None,
    ) -> dict[str, Any]:
        """Vektordata ur körningen för kartan: `sankor`, `flodesvagar` (ur sänkgrafen) eller `vsc`.

        Sänkgrafen byggs om för scenariots K1-ruta med samma markmodell och samma antaganden
        (takvatten, persistens) som lösaren fick, ur jobbets `run.json`; samma kod, inget nytt tal.
        Svaret sparas bredvid lagren och kräver samma behörighet som körningens övriga resultat.

        `flodesvagar` är avrinningsnätet (`sankgraf.avrinningsnat`); `min_yta_m2` är minsta
        tillrinning för ett stråk (förval 500). `sankor` slår ihop sänkor som fylls ihop;
        `min_volym_m3` (förval 1) och `min_djup_m` (förval 0,05) döljer småsänkor."""
        k = self._hamta(korning_id)
        yta = STANDARD_MIN_YTA_M2 if min_yta_m2 is None else float(min_yta_m2)
        vol = 1.0 if min_volym_m3 is None else float(min_volym_m3)
        djup = 0.05 if min_djup_m is None else float(min_djup_m)
        for namn, v in (("min_yta_m2", yta), ("min_volym_m3", vol), ("min_djup_m", djup)):
            if not (math.isfinite(v) and 0.0 <= v <= 1e7) or (namn == "min_yta_m2" and v <= 0.0):
                raise FlodenWebbFel(f"Parametern {namn} har ett ogiltigt värde.", status_kod=400)
        _kontrollera_id(scenario, "scenario")
        if slag not in analysvektor.SLAG:
            raise FlodenWebbFel(f"Okänt vektorlager '{slag}'.", status_kod=404)
        s = self._sammanstallning(k)
        if scenario not in s["scenarier"]:
            raise FlodenWebbFel(f"Okänt scenario '{scenario}'.", status_kod=404)
        suffix = {
            "flodesvagar": f".v2.{yta:g}",
            "sankor": f".v2.{vol:g}.{djup:g}",
        }.get(slag, "")
        cachefil = k.mapp / "webb" / f"vektor.{scenario}.{slag}{suffix}.json"
        las = self._las_for_nyckel(f"vektor:{k.korning_id}:{scenario}:{slag}")
        with las:
            if cachefil.is_file():
                cachad: dict[str, Any] = json.loads(cachefil.read_text(encoding="utf-8"))
                return cachad
            if slag == "vsc":
                fil = next(
                    (
                        self._jobbmapp(k, j) / "vsc.csv"
                        for j in s["jobb"]
                        if j["scenario"] == scenario and "vsc" in self._roller(k, j)
                    ),
                    None,
                )
                if fil is None or not fil.is_file():
                    raise FlodenWebbFel("Körningen har inga fasadpunkter med VSC.", status_kod=404)
                ut = analysvektor.vsc_geojson(fil)
            elif slag == "rannsten_flode":
                fil = next(
                    (
                        self._jobbmapp(k, j) / roller["rannsten_flode"]
                        for j in s["jobb"]
                        if j["scenario"] == scenario
                        and "rannsten_flode" in (roller := self._roller(k, j))
                    ),
                    None,
                )
                if fil is None or not fil.is_file():
                    raise FlodenWebbFel(
                        "Körningen har inget 1D-rännstenslager (underlaget saknar 3D-kantsten).",
                        status_kod=404,
                    )
                try:
                    ut = analysvektor.rannsten_flode_geojson(fil)
                except (OSError, ValueError) as exc:
                    raise FlodenWebbFel(
                        f"Kunde inte läsa 1D-rännstenslagret: {exc}", status_kod=500
                    ) from exc
            else:
                ut = self._sankgraf_vektor(k, s, scenario, slag, yta, vol, djup)
            ut = {"slag": slag, "scenario": scenario, "korning_id": korning_id, **ut}
            skriv_atomiskt_text(cachefil, json.dumps(ut, ensure_ascii=False) + "\n")
            return ut

    @staticmethod
    def _bestam_overlapp_m(mappar: Sequence[Path], geos: Sequence[Any]) -> float:
        """Rutöverlapp (m) ur `recept.json` eller rutgeometri (samma regel som `karna.inbyggda`)."""
        recept_varden: list[float] = []
        for mapp in mappar:
            recept = mapp / "recept.json"
            if not recept.is_file():
                continue
            try:
                data = json.loads(recept.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and "overlapp_m" in data:
                ov = data["overlapp_m"]
                if (
                    not isinstance(ov, bool)
                    and isinstance(ov, int | float)
                    and math.isfinite(float(ov))
                ):
                    if float(ov) < 0.0:
                        raise ValueError(f"Ogiltigt overlapp_m ({ov}) i {recept}.")
                    recept_varden.append(float(ov))
        if len(recept_varden) == len(mappar) and recept_varden:
            for ov in recept_varden[1:]:
                if not math.isclose(ov, recept_varden[0], rel_tol=0.0, abs_tol=1e-6):
                    unika = sorted(set(recept_varden))
                    raise ValueError(f"Oförenliga overlapp_m i rutornas recept.json: {unika}.")
            return recept_varden[0]

        kandidater: list[float] = []
        tol = 1e-6
        for i in range(len(geos)):
            ga = geos[i]
            for j in range(i + 1, len(geos)):
                gb = geos[j]
                if min(ga.y_max, gb.y_max) > max(ga.y_min, gb.y_min) + tol:
                    v, o = (ga, gb) if ga.x_min <= gb.x_min else (gb, ga)
                    if (
                        v.x_min < o.x_min - tol
                        and v.x_max >= o.x_min - tol
                        and v.x_max < o.x_max - tol
                    ):
                        kandidater.append(max(0.0, 0.5 * (v.x_max - o.x_min)))
                if min(ga.x_max, gb.x_max) > max(ga.x_min, gb.x_min) + tol:
                    s_geo, n_geo = (ga, gb) if ga.y_min <= gb.y_min else (gb, ga)
                    if (
                        s_geo.y_min < n_geo.y_min - tol
                        and s_geo.y_max >= n_geo.y_min - tol
                        and s_geo.y_max < n_geo.y_max - tol
                    ):
                        kandidater.append(max(0.0, 0.5 * (s_geo.y_max - n_geo.y_min)))
        if not kandidater:
            return 0.0
        for ov in kandidater[1:]:
            if not math.isclose(ov, kandidater[0], rel_tol=0.0, abs_tol=1e-6):
                unika_k = sorted(set(kandidater))
                raise ValueError(
                    f"Oförenliga rutöverlapp mellan angränsande K1-rutor: {unika_k}."
                )
        return kandidater[0]

    @classmethod
    def _bygg_flerruts_sankgraf(
        cls,
        mappar: Sequence[Path],
        *,
        persistens_faktor: float | None,
        takvatten: bool,
        takvatten_andel_ledning: float,
    ) -> Any:
        """Bygger sammanslagen sänkgraf över flera K1-rutor (samma regel som `karna.inbyggda`)."""
        from sankgraf.hierarki import bygg_sankgraf, sammanfoga_rutor
        from sankgraf.persistens import filtrera_persistens
        from sankgraf.takvatten import bygg_takvatten, las_takpolygoner
        from sankgraf.typer import (
            MARKSLAG_BYGGNAD,
            las_brytlinjer_fran_gpkg,
            las_oppningar_fran_gpkg,
        )
        from testsvit.k1_io import las_geotiff

        rut_data: list[tuple[Any, Any, Any, Any]] = []
        geos: list[Any] = []
        brytlinjer: list[Any] = []
        oppningar: list[Any] = []
        sedda_bryt: set[Any] = set()
        sedda_opp: set[Any] = set()
        tak_per_id: dict[str, Any] = {}

        for mapp in mappar:
            mark_raw, geo = las_geotiff(mapp / "mark.tif")
            osak_raw, _ = las_geotiff(mapp / "osakerhet.tif")
            slag_raw, _ = las_geotiff(mapp / "markslag.tif")
            geos.append(geo)
            rut_data.append(
                (
                    geo,
                    np.asarray(mark_raw, dtype=np.float32),
                    np.asarray(osak_raw, dtype=np.float32),
                    np.asarray(slag_raw, dtype=np.uint8),
                )
            )
            bryt_fil = mapp / "brytlinjer.gpkg"
            if bryt_fil.is_file():
                for b in las_brytlinjer_fran_gpkg(bryt_fil):
                    nyckel_b = (b.typ, b.hojd_m, tuple(b.koordinater), b.z_kron_rh2000)
                    if nyckel_b not in sedda_bryt:
                        sedda_bryt.add(nyckel_b)
                        brytlinjer.append(b)
            opp_fil = mapp / "oppningar.gpkg"
            if opp_fil.is_file():
                for o in las_oppningar_fran_gpkg(opp_fil):
                    nyckel_o = (o.typ, o.bredd_m, o.botten_rh2000, tuple(o.koordinater))
                    if nyckel_o not in sedda_opp:
                        sedda_opp.add(nyckel_o)
                        oppningar.append(o)
            if takvatten:
                bygg_fil = mapp / "byggnader.gpkg"
                if bygg_fil.is_file():
                    for tp in las_takpolygoner(bygg_fil):
                        tak_per_id.setdefault(tp.byggnad_id, tp)

        overlapp_m = cls._bestam_overlapp_m(mappar, geos)
        glob_geo, glob_mark, glob_osak, glob_slag = sammanfoga_rutor(
            rut_data, overlapp_m=overlapp_m
        )
        from karna.inbyggda import kontrollera_sankgraf_celler

        kontrollera_sankgraf_celler(int(glob_mark.shape[0] * glob_mark.shape[1]), len(mappar))
        graf = bygg_sankgraf(
            glob_mark,
            glob_osak,
            glob_slag,
            glob_geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
        )
        if persistens_faktor is not None:
            graf = filtrera_persistens(graf, faktor=persistens_faktor)
        if takvatten:
            takpolygoner = tuple(tak_per_id[bid] for bid in sorted(tak_per_id))
            oppning_mask = np.asarray(
                (glob_slag == MARKSLAG_BYGGNAD) & (graf.markslag != MARKSLAG_BYGGNAD),
                dtype=np.bool_,
            )
            tv = bygg_takvatten(
                graf.mark_z,
                graf.markslag,
                graf.geo,
                byggnader=takpolygoner,
                andel_ledning=takvatten_andel_ledning,
                oppning_mask=oppning_mask if np.any(oppning_mask) else None,
            )
            graf = replace(graf, takvatten=tv)
        return graf

    @classmethod
    def _ladda_avrinningsnat_underlag(
        cls,
        mappar: Sequence[Path],
        *,
        takvatten: bool,
        takvatten_andel_ledning: float,
    ) -> tuple[Any, np.ndarray[Any, Any], np.ndarray[Any, Any], np.ndarray[Any, Any] | None]:
        """Läser höjd, markslag, öppningar och ev. takvatten för flödesvägar (utan sänkgraf)."""
        from karna.inbyggda import kontrollera_sankgraf_celler
        from sankgraf.hierarki import _applicera_oppningar, sammanfoga_rutor
        from sankgraf.takvatten import bygg_takvatten, las_takpolygoner
        from sankgraf.typer import MARKSLAG_BYGGNAD, las_oppningar_fran_gpkg
        from testsvit.k1_io import las_geotiff

        oppningar: list[Any] = []
        sedda_opp: set[Any] = set()
        tak_per_id: dict[str, Any] = {}

        if len(mappar) == 1:
            mapp = mappar[0]
            mark_raw, glob_geo = las_geotiff(mapp / "mark.tif")
            slag_raw, _ = las_geotiff(mapp / "markslag.tif")
            glob_mark = np.asarray(mark_raw, dtype=np.float32)
            glob_slag = np.asarray(slag_raw, dtype=np.uint8)
            opp_fil = mapp / "oppningar.gpkg"
            if opp_fil.is_file():
                oppningar = list(las_oppningar_fran_gpkg(opp_fil))
            if takvatten:
                bygg_fil = mapp / "byggnader.gpkg"
                if bygg_fil.is_file():
                    for tp in las_takpolygoner(bygg_fil):
                        tak_per_id.setdefault(tp.byggnad_id, tp)
        else:
            rut_data: list[tuple[Any, Any, Any, Any]] = []
            geos: list[Any] = []
            for mapp in mappar:
                mark_raw, geo = las_geotiff(mapp / "mark.tif")
                slag_raw, _ = las_geotiff(mapp / "markslag.tif")
                mark_f32 = np.asarray(mark_raw, dtype=np.float32)
                geos.append(geo)
                rut_data.append(
                    (
                        geo,
                        mark_f32,
                        np.zeros_like(mark_f32, dtype=np.float32),
                        np.asarray(slag_raw, dtype=np.uint8),
                    )
                )
                opp_fil = mapp / "oppningar.gpkg"
                if opp_fil.is_file():
                    for o in las_oppningar_fran_gpkg(opp_fil):
                        nyckel_o = (o.typ, o.bredd_m, o.botten_rh2000, tuple(o.koordinater))
                        if nyckel_o not in sedda_opp:
                            sedda_opp.add(nyckel_o)
                            oppningar.append(o)
                if takvatten:
                    bygg_fil = mapp / "byggnader.gpkg"
                    if bygg_fil.is_file():
                        for tp in las_takpolygoner(bygg_fil):
                            tak_per_id.setdefault(tp.byggnad_id, tp)
            overlapp_m = cls._bestam_overlapp_m(mappar, geos)
            glob_geo, glob_mark, _glob_osak, glob_slag = sammanfoga_rutor(
                rut_data, overlapp_m=overlapp_m
            )
            del _glob_osak, rut_data

        kontrollera_sankgraf_celler(int(glob_mark.shape[0] * glob_mark.shape[1]), len(mappar))
        eff_z, eff_slag = _applicera_oppningar(glob_mark, glob_slag, glob_geo, oppningar)
        aktiv = np.asarray(eff_slag != MARKSLAG_BYGGNAD, dtype=bool) & np.isfinite(eff_z)

        extra: np.ndarray[Any, Any] | None = None
        if takvatten:
            takpolygoner = tuple(tak_per_id[bid] for bid in sorted(tak_per_id))
            oppning_mask = np.asarray(
                (glob_slag == MARKSLAG_BYGGNAD) & (eff_slag != MARKSLAG_BYGGNAD),
                dtype=np.bool_,
            )
            tv = bygg_takvatten(
                eff_z,
                eff_slag,
                glob_geo,
                byggnader=takpolygoner,
                andel_ledning=takvatten_andel_ledning,
                oppning_mask=oppning_mask if np.any(oppning_mask) else None,
            )
            if not tv.tom:
                extra = np.asarray(tv.extra_area_m2, dtype=np.float64)
        return glob_geo, eff_z, aktiv, extra

    @staticmethod
    def _spara_avrinningsnat_bas_atomiskt(
        mal: Path,
        *,
        z: np.ndarray[Any, Any],
        flow: np.ndarray[Any, Any],
        ordn: np.ndarray[Any, Any],
        a: np.ndarray[Any, Any],
        x_min: float,
        y_max: float,
        pixel_m: float,
    ) -> None:
        mal.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_namn = tempfile.mkstemp(prefix=f".{mal.name}.", suffix=".tmp", dir=mal.parent)
        tmp = Path(tmp_namn)
        try:
            with os.fdopen(fd, "wb") as fh:
                np.savez(
                    fh,
                    z=np.asarray(z, dtype=np.float32),
                    flow=np.asarray(flow, dtype=np.int32),
                    ordn=np.asarray(ordn, dtype=np.int32),
                    a=np.asarray(a, dtype=np.float64),
                    geo=np.asarray([float(x_min), float(y_max), float(pixel_m)], dtype=np.float64),
                )
            os.replace(tmp, mal)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _las_avrinningsnat_bas(
        mal: Path,
    ) -> tuple[
        np.ndarray[Any, Any],
        np.ndarray[Any, Any],
        np.ndarray[Any, Any],
        np.ndarray[Any, Any],
        float,
        float,
        float,
    ] | None:
        if not mal.is_file():
            return None
        try:
            with np.load(mal, allow_pickle=False) as d:
                z = np.asarray(d["z"], dtype=np.float32)
                flow = np.asarray(d["flow"], dtype=np.int32)
                ordn = np.asarray(d["ordn"], dtype=np.int32)
                a = np.asarray(d["a"], dtype=np.float64)
                geo = np.asarray(d["geo"], dtype=np.float64)
            if geo.size != 3 or z.ndim != 2 or flow.size != z.size or a.size != z.size:
                raise ValueError("Ogiltiga dimensioner i avrinningsnat-cache.")
            return z, flow, ordn, a, float(geo[0]), float(geo[1]), float(geo[2])
        except Exception:  # noqa: BLE001 - trasig cachefil rensas och räknas om rent
            mal.unlink(missing_ok=True)
            return None

    @classmethod
    def _berakna_sankgraf_vektor_fran_mappar(
        cls,
        mappar: Sequence[Path],
        *,
        slag: str,
        persistens_faktor: float | None,
        takvatten: bool,
        takvatten_andel_ledning: float,
        min_yta_m2: float,
        min_volym_m3: float,
        min_djup_m: float,
        bas_npz: Path | None = None,
    ) -> dict[str, Any]:
        """Bygger `sankor` eller `flodesvagar` ur K1-rutor (anropas direkt eller i barnprocess)."""
        if slag == "flodesvagar":
            from sankgraf.avrinningsnat import berakna_avrinningsfalt, strackor_fran_falt

            cachad = cls._las_avrinningsnat_bas(bas_npz) if bas_npz is not None else None
            if cachad is not None:
                z_arr, flow, ordn, a, x_min, y_max, pixel_m = cachad
            else:
                glob_geo, eff_z, aktiv, extra = cls._ladda_avrinningsnat_underlag(
                    mappar,
                    takvatten=takvatten,
                    takvatten_andel_ledning=takvatten_andel_ledning,
                )
                pixel_m = float(glob_geo.pixelstorlek_x)
                x_min = float(glob_geo.x_min)
                y_max = float(glob_geo.y_max)
                z64 = np.asarray(eff_z, dtype=np.float64)
                flow, ordn, a = berakna_avrinningsfalt(z64, aktiv, extra, pixel_m=pixel_m)
                z_arr = eff_z
                if bas_npz is not None:
                    cls._spara_avrinningsnat_bas_atomiskt(
                        bas_npz,
                        z=z_arr,
                        flow=flow,
                        ordn=ordn,
                        a=a,
                        x_min=x_min,
                        y_max=y_max,
                        pixel_m=pixel_m,
                    )
            vagar = strackor_fran_falt(
                z_arr,
                flow,
                ordn,
                a,
                x_min=x_min,
                y_max=y_max,
                pixel_m=pixel_m,
                min_yta_m2=min_yta_m2,
            )
            return analysvektor.flodesvagar_fran_strackor(vagar, min_yta_m2)

        from sankgraf.fyllning import kor_statisk_k1_mapp

        if len(mappar) == 1:
            graf, _ = kor_statisk_k1_mapp(
                mappar[0],
                regn_m=0.0,
                persistens_faktor=persistens_faktor,
                takvatten=takvatten,
                takvatten_andel_ledning=takvatten_andel_ledning,
            )
        else:
            graf = cls._bygg_flerruts_sankgraf(
                mappar,
                persistens_faktor=persistens_faktor,
                takvatten=takvatten,
                takvatten_andel_ledning=takvatten_andel_ledning,
            )
        return analysvektor.sankor_geojson(
            graf, min_volym_m3=min_volym_m3, min_djup_m=min_djup_m
        )

    def _sankgraf_vektor(
        self,
        k: Korning,
        s: dict[str, Any],
        scenario: str,
        slag: str,
        min_yta_m2: float,
        min_volym_m3: float,
        min_djup_m: float,
    ) -> dict[str, Any]:
        jobb = next(
            (
                j
                for j in s["jobb"]
                if j["scenario"] == scenario
                and str(j["losare"]) in ("vatten.statisk_fyllning", "vatten.dynamik")
            ),
            None,
        )
        if jobb is None:
            raise FlodenWebbFel(
                "Körningen har ingen vattenberäkning för scenariot, så det finns ingen sänkgraf.",
                status_kod=404,
            )
        rutor, varning = self._scenariorutor(k, s)
        scen_rutor = (rutor or {}).get(scenario, {})
        run_fil = self._jobbmapp(k, jobb) / "run.json"
        try:
            run_data = json.loads(run_fil.read_text(encoding="utf-8"))
            if not isinstance(run_data, dict):
                raise TypeError("run.json är inte ett JSON-objekt")
            par_raw = run_data["parametrar"]
            if not isinstance(par_raw, dict):
                raise TypeError("parametrar i run.json är inte ett JSON-objekt")
            par = dict(par_raw)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _logg.warning(
                "run.json saknas eller går inte att läsa i _sankgraf_vektor för körning %s: %s",
                k.korning_id,
                exc,
            )
            raise FlodenWebbFel(
                "Körningens manifest (run.json) saknas eller kan inte läsas "
                "för sänkor och flödesvägar.",
                status_kod=409,
            ) from exc
        indata_raw = run_data.get("indata", [])
        indata_lista = indata_raw if isinstance(indata_raw, list) else []
        ruta_ids = [
            str(p["fil"])
            for p in indata_lista
            if isinstance(p, dict)
            and isinstance(p.get("roll"), str)
            and p["roll"].startswith("ruta:")
            and isinstance(p.get("fil"), str)
        ]
        if ruta_ids and all(rid in scen_rutor for rid in ruta_ids):
            mappar = [scen_rutor[rid] for rid in sorted(set(ruta_ids))]
        else:
            mappar = [m for _, m in sorted(scen_rutor.items())]
        if not mappar:
            raise FlodenWebbFel(
                varning or "Scenariot saknar K1-rutor för sänkor och flödesvägar.",
                status_kod=409,
            )
        persistens_faktor = par.get("persistens_faktor")
        takvatten = bool(par.get("takvatten", True))
        takvatten_andel_ledning = float(par.get("takvatten_andel_ledning", 0.0))
        bas_npz = k.mapp / "webb" / f"avrinningsnat.{scenario}.v2.npz"

        # Snabbväg: när avrinningsfältet redan är beräknat och cachat räcker en lätt tröskling.
        if slag == "flodesvagar" and bas_npz.is_file():
            try:
                return self._berakna_sankgraf_vektor_fran_mappar(
                    mappar,
                    slag=slag,
                    persistens_faktor=persistens_faktor,
                    takvatten=takvatten,
                    takvatten_andel_ledning=takvatten_andel_ledning,
                    min_yta_m2=min_yta_m2,
                    min_volym_m3=min_volym_m3,
                    min_djup_m=min_djup_m,
                    bas_npz=bas_npz,
                )
            except Exception as exc:  # ett tydligt fel i stället för ett tyst tomt lager
                raise FlodenWebbFel(
                    f"Flödesvägarna kunde inte byggas: {exc}", status_kod=409
                ) from exc

        if self.exekutor_lage == "process":
            webbmapp = k.mapp / "webb"
            webbmapp.mkdir(parents=True, exist_ok=True)
            fd, tmp_ut = tempfile.mkstemp(
                prefix=f".vektor_ut_{scenario}_{slag}_", suffix=".json", dir=str(webbmapp)
            )
            os.close(fd)
            ut_fil = Path(tmp_ut)
            ut_fil.unlink(missing_ok=True)
            try:
                kor_vektor_i_barnprocess(
                    slag=slag,
                    mappar=mappar,
                    persistens_faktor=persistens_faktor,
                    takvatten=takvatten,
                    takvatten_andel_ledning=takvatten_andel_ledning,
                    min_yta_m2=min_yta_m2,
                    min_volym_m3=min_volym_m3,
                    min_djup_m=min_djup_m,
                    ut_json=ut_fil,
                    bas_npz=bas_npz if slag == "flodesvagar" else None,
                    max_ram_mb=self.jobb_max_ram_mb,
                    max_tid_s=self.jobb_max_tid_s,
                    hanterare=self._subproc,
                )
                ut_data: dict[str, Any] = json.loads(ut_fil.read_text(encoding="utf-8"))
                return ut_data
            except (SubprocMinneFel, SubprocTimeoutFel, SubprocAvbrutenFel) as exc:
                raise FlodenWebbFel(str(exc), status_kod=503) from exc
            except SubprocFel as exc:
                raise FlodenWebbFel(
                    f"Sänkgrafen kunde inte byggas: {exc}", status_kod=409
                ) from exc
            except (OSError, ValueError) as exc:
                raise FlodenWebbFel(
                    f"Vektorlagret kunde inte läsas från barnprocessen: {exc}", status_kod=500
                ) from exc
            finally:
                ut_fil.unlink(missing_ok=True)

        try:
            return self._berakna_sankgraf_vektor_fran_mappar(
                mappar,
                slag=slag,
                persistens_faktor=persistens_faktor,
                takvatten=takvatten,
                takvatten_andel_ledning=takvatten_andel_ledning,
                min_yta_m2=min_yta_m2,
                min_volym_m3=min_volym_m3,
                min_djup_m=min_djup_m,
                bas_npz=bas_npz if slag == "flodesvagar" else None,
            )
        except Exception as exc:  # ett tydligt fel i stället för ett tyst tomt lager
            raise FlodenWebbFel(f"Sänkgrafen kunde inte byggas: {exc}", status_kod=409) from exc

    # Byggnader, antaganden och avläsning --------------------------------------------------

    def _fil_for_korning(self, k: Korning) -> Path:
        """Frågefilen för en körning: först via `k.ref`, annars direkt via `k.fraga_fil`."""
        try:
            return self._fil_for(k.ref)
        except FlodenWebbFel:
            p = Path(k.fraga_fil)
            if not p.is_absolute():
                p = self.rot / p
            if p.is_file():
                return p
            raise

    def _scenariorutor(
        self, k: Korning, s: dict[str, Any]
    ) -> tuple[dict[str, dict[str, Path]] | None, str | None]:
        """K1-rutornas kataloger per scenario (kärnans materialisering ur cachen).

        Stämmer inte scenariots nyckel med den som körningen fick (frågefilen eller indata har
        ändrats sedan dess) returneras inget, så att byggnader och tak aldrig kommer från en annan
        värld än resultatet."""
        try:
            fraga = las_fraga(self._fil_for_korning(k))
            ut: dict[str, dict[str, Path]] = {}
            for sid, spec in fraga.scenarier.items():
                sc = materialisera_scenario(spec, self.cache())
                kand = s["scenarier"].get(sid)
                if kand is not None and kand["nyckel"] != sc.nyckel:
                    return None, (
                        "Frågefilen eller dess indata har ändrats sedan körningen: byggnadslagret "
                        "och takmaskerna visas inte. Kör frågan igen."
                    )
                ut[sid] = {r.ruta_id: Path(r.mapp) for r in sc.rutor}
            return ut, None
        except (FlodenWebbFel, *_FRAGEFEL, OSError) as exc:
            return None, f"Byggnadslagret och takmaskerna kunde inte läsas: {exc}"

    def _byggnader(
        self, k: Korning, s: dict[str, Any], lager: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Byggnadsfotavtryck per scenario ur K1 v2-rutornas byggnadsregister (`byggnader.gpkg`).

        Bara byggnader i rutor som ligger i lagrens utsträckning. `status` jämför med första
        scenariot: `ny`, `andrad` (annan takhöjd) eller `oforandrad`."""
        if not lager:
            return None, None
        cachefil = k.mapp / "webb" / "byggnader.json"
        with self._las_for_korning(k.korning_id):
            if cachefil.is_file():
                d = json.loads(cachefil.read_text(encoding="utf-8"))
                if d.get("version") == LAGER_VERSION:
                    return d["byggnader"], d.get("varning")
            rutor, varning = self._scenariorutor(k, s)
            if rutor is None:
                return None, varning
            x0 = min(la["utbredning"]["x_min"] for la in lager)
            y0 = min(la["utbredning"]["y_min"] for la in lager)
            x1 = max(la["utbredning"]["x_max"] for la in lager)
            y1 = max(la["utbredning"]["y_max"] for la in lager)
            ordning = list(s["scenarier"])
            per_scen: dict[str, dict[str, dict[str, Any]]] = {}
            try:
                for sid in ordning:
                    poster: dict[str, dict[str, Any]] = {}
                    for mapp in rutor.get(sid, {}).values():
                        for b in las_byggnader(mapp):
                            xs = [p[0] for p in b.polygon]
                            ys = [p[1] for p in b.polygon]
                            if max(xs) < x0 or min(xs) > x1 or max(ys) < y0 or min(ys) > y1:
                                continue
                            poster[b.byggnad_id] = {
                                "id": b.byggnad_id,
                                "ring": [[float(x), float(y)] for x, y in b.polygon],
                                "hal": [[[float(x), float(y)] for x, y in r] for r in b.hal],
                                "hojd_m": float(b.hojd_m),
                                "takhojd_rh2000": float(b.takhojd_rh2000),
                                "hojdkalla": b.hojdkalla,
                            }
                    per_scen[sid] = poster
            except (GeometriFel, OSError, ValueError) as exc:
                return None, f"Byggnadsregistret kunde inte läsas: {exc}"
            bas_id = ordning[0]
            bas = per_scen.get(bas_id, {})
            ut: dict[str, Any] = {"bas": bas_id, "scenarier": {}}
            for sid in ordning:
                lista: list[dict[str, Any]] = []
                for bid, post in sorted(per_scen[sid].items()):
                    if sid == bas_id:
                        status = "oforandrad"
                    elif bid not in bas:
                        status = "ny"
                    elif abs(bas[bid]["takhojd_rh2000"] - post["takhojd_rh2000"]) > 1e-6:
                        status = "andrad"
                    else:
                        status = "oforandrad"
                    lista.append({**post, "status": status})
                ut["scenarier"][sid] = lista
            skriv_atomiskt_text(
                cachefil,
                json.dumps(
                    {"version": LAGER_VERSION, "byggnader": ut, "varning": None}, ensure_ascii=False
                )
                + "\n",
            )
            return ut, None

    def _antaganden(self, k: Korning, s: dict[str, Any]) -> dict[str, Any]:
        """Regnkälla och parametrar per analys, rakt ur jobbens K0-manifest (`run.json`).

        Webben räknar inget: `regn_mm` och källan är det lösaren skrev. Samma parametrar i flera
        jobb ger en post."""
        regn: list[dict[str, Any]] = []
        analyser: list[dict[str, Any]] = []
        vatten: list[dict[str, Any]] = []
        sedda: set[str] = set()
        for j in s["jobb"]:
            if j["analys"] in sedda:
                continue
            sedda.add(j["analys"])
            try:
                m = json.loads((self._jobbmapp(k, j) / "run.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(m, dict):
                _logg.warning(
                    "run.json har ogiltigt format i _antaganden för körning %s (%s/%s)",
                    k.korning_id,
                    j.get("analys"),
                    j.get("scenario"),
                )
                continue
            par_raw = m.get("parametrar")
            mv_raw = m.get("matvarden")
            par: dict[str, Any] = dict(par_raw) if isinstance(par_raw, dict) else {}
            mv: dict[str, Any] = dict(mv_raw) if isinstance(mv_raw, dict) else {}
            enkla = {
                n: v
                for n, v in par.items()
                if n not in ("nyckel", "losare_version")
                and not isinstance(v, dict | list)
                and not n.startswith("idf_")
            }
            analyser.append(
                {
                    "analys": j["analys"],
                    "losare": j["losare"],
                    "version": j["version"],
                    "kodversion": m.get("kodversion"),
                    "parametrar": enkla,
                }
            )
            if "idf_kalla" in par:
                regn.append(
                    {
                        "analys": j["analys"],
                        "kalla": par["idf_kalla"],
                        "rapport": par.get("idf_rapport"),
                        "tabell": par.get("idf_tabell"),
                        "url": par.get("idf_url"),
                        "aterkomsttid_ar": par.get("aterkomsttid_ar"),
                        "varaktighet_min": par.get("varaktighet_min"),
                        "regntyp": par.get("regntyp"),
                        "klimatfaktor": par.get("idf_klimatfaktor"),
                        "klimatfaktor_ingar_i_tabell": par.get("idf_klimatfaktor_ingar_i_tabell"),
                        "regn_mm": mv.get("regn_mm"),
                        "sha256": par.get("idf_sha256"),
                    }
                )
            elif "regn_mm" in par:
                regn.append(
                    {
                        "analys": j["analys"],
                        "kalla": None,
                        "regn_mm": par["regn_mm"],
                        "klimatfaktor": None,
                    }
                )
            if "takvatten_antagande" in mv or "avtappning_antagande" in mv:
                vatten.append(
                    {
                        "analys": j["analys"],
                        "takvatten": mv.get("takvatten"),
                        "takvatten_text": mv.get("takvatten_antagande"),
                        "avtappning_mm_h": mv.get("avtappning_mm_h"),
                        "avtappning_kalla": mv.get("avtappning_kalla"),
                        "avtappning_text": mv.get("avtappning_antagande"),
                    }
                )
        return {"regn": regn, "analyser": analyser, "vatten": vatten}

    def avlasning(self, korning_id: str, lager_id: str, e: float, n: float) -> dict[str, Any]:
        """Värdena i punkten (e, n) i alla lager för samma analys och roll som `lager_id`.

        Läser cellen ur de rasterlager som ritats (samma celler som syns); inget räknas om. `tak`
        är sant när cellen är en byggnads fotavtryck."""
        k = self._hamta(korning_id)
        _kontrollera_id(lager_id, "lager-id")
        lager = self.resultat(korning_id)["lager"]
        valt = next((la for la in lager if la["id"] == lager_id), None)
        if valt is None:
            raise FlodenWebbFel(f"Okänt lager '{lager_id}'.", status_kod=404)
        rader: list[dict[str, Any]] = []
        for la in lager:
            if la["analys"] != valt["analys"] or la["roll"] != valt["roll"]:
                continue
            u = la["utbredning"]
            px = la["pixelstorlek_m"]
            kol, rad = int((e - u["x_min"]) // px), int((u["y_max"] - n) // px)
            inom = u["x_min"] <= e < u["x_max"] and u["y_min"] < n <= u["y_max"]
            varde: float | None = None
            tak = False
            if inom:
                arr = np.load(k.mapp / "webb" / f"{la['id']}.npy", mmap_mode="r")
                if 0 <= rad < arr.shape[0] and 0 <= kol < arr.shape[1]:
                    v = float(arr[rad, kol])
                    varde = v if np.isfinite(v) else None
                    takfil = k.mapp / "webb" / f"{la['id']}.tak.npy"
                    if takfil.is_file():
                        tak = bool(np.load(takfil, mmap_mode="r")[rad, kol])
            rader.append(
                {
                    "lager_id": la["id"],
                    "namn": la["namn"],
                    "typ": la["typ"],
                    "scenario": la["scenario"],
                    "enhet": la["enhet"],
                    "etikett": la["etikett"],
                    "varde": varde,
                    "tak": tak,
                    "inom": inom,
                    "kalla": self._cellkalla(k, la, e, n) if inom else None,
                }
            )
        ordning = {"bas": 0, "scenario": 1, "skillnad": 2}
        rader.sort(key=lambda x: (ordning[x["typ"]], x["lager_id"]))
        return {"e": e, "n": n, "analys": valt["analys"], "roll": valt["roll"], "varden": rader}

    def sanka(
        self, korning_id: str, e: float, n: float, scenario: str | None = None
    ) -> dict[str, Any]:
        """Den sammanhängande vattenytan i (e, n) ur körningens största vattendjup (`max_djup`).

        Vattenyta = celler med djup över 2 cm som hänger ihop (4-grannar) med cellen i punkten.
        Ger per läge (före, efter) största och medeldjup, våt yta (m²) och volym (summa djup
        gånger cellyta, m³), och skillnaden efter minus före. Läser bara de ritade lagren."""
        from scipy import ndimage

        k = self._hamta(korning_id)
        if scenario is not None:
            _kontrollera_id(scenario, "scenario")
        lager = [
            la
            for la in self.resultat(korning_id)["lager"]
            if la["roll"] == "max_djup" and la["typ"] in ("bas", "scenario")
        ]
        if not lager:
            raise FlodenWebbFel("Körningen har inget lager med största vattendjup.", status_kod=404)
        lager.sort(key=lambda la: (la["typ"] != "bas", la["id"]))
        rader: list[dict[str, Any]] = []
        for la in lager:
            u, px = la["utbredning"], float(la["pixelstorlek_m"])
            kol, rad = int((e - u["x_min"]) // px), int((u["y_max"] - n) // px)
            rad_ut: dict[str, Any] = {
                "lager_id": la["id"],
                "scenario": la["scenario"],
                "typ": la["typ"],
                "vatt": False,
            }
            arr = np.load(k.mapp / "webb" / f"{la['id']}.npy", mmap_mode="r")
            if 0 <= rad < arr.shape[0] and 0 <= kol < arr.shape[1]:
                d = np.nan_to_num(np.asarray(arr, dtype=np.float64), nan=0.0)
                if d[rad, kol] > VAT_YTA_M:
                    etik, _ = ndimage.label(d > VAT_YTA_M)
                    yta = etik == etik[rad, kol]
                    dj = d[yta]
                    rad_ut.update(
                        vatt=True,
                        max_djup_m=float(dj.max()),
                        medel_djup_m=float(dj.mean()),
                        vat_yta_m2=float(dj.size * px * px),
                        volym_m3=float(dj.sum() * px * px),
                    )
            rader.append(rad_ut)
        fore = next((r for r in rader if r["typ"] == "bas"), None)
        efter = next((r for r in rader if r["typ"] == "scenario"), None)
        skillnad: dict[str, float] | None = None
        if fore and efter:
            f = {m: fore.get(m, 0.0) for m in _SANKMATT}
            g = {m: efter.get(m, 0.0) for m in _SANKMATT}
            skillnad = {m: g[m] - f[m] for m in _SANKMATT}
        return {"e": e, "n": n, "grans_m": VAT_YTA_M, "lagen": rader, "skillnad": skillnad}

    def _cellkalla(
        self, k: Korning, la: dict[str, Any], e: float, n: float
    ) -> dict[str, Any] | None:
        """Vilken GeoTIFF i resultatet (och vilken cell) som ger värdet i (e, n): belägget.

        Där rutor överlappar gäller samma regel som när lagret lades ihop (`sammanfoga`): rutan där
        punkten ligger djupast inne. Filens sha256 tas ur jobbets `run.json` (K0) när den finns där,
        annars räknas den."""
        basta: tuple[float, dict[str, Any]] | None = None
        for g in la["geotiff"]:
            rel = str(g["url"]).split("/fil/", 1)[1]
            fil = k.utmapp / rel
            try:
                with rasterio.open(fil) as ds:
                    b = ds.bounds
                    px = float(ds.transform.a)
            except (OSError, rasterio.errors.RasterioError):
                continue
            inne = min(e - b.left, b.right - e, n - b.bottom, b.top - n)
            if inne < 0 or (basta is not None and inne <= basta[0]):
                continue
            basta = (
                inne,
                {
                    "fil": rel,
                    "ruta": g["ruta"],
                    "fil_sha256": self._fil_sha256(fil),
                    "cell": {
                        "e_mitt": b.left + (int((e - b.left) // px) + 0.5) * px,
                        "n_mitt": b.top - (int((b.top - n) // px) + 0.5) * px,
                        "storlek_m": px,
                    },
                },
            )
        return None if basta is None else basta[1]

    @staticmethod
    def _fil_sha256(fil: Path) -> str:
        """sha256 för en resultatfil: ur K0-manifestet bredvid den, annars räknad."""
        run = fil.parent / "run.json"
        try:
            m = json.loads(run.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            m = None
        if isinstance(m, dict):
            utdata = m.get("utdata")
            if isinstance(utdata, list):
                for p in utdata:
                    if isinstance(p, dict) and p.get("fil") == fil.name and p.get("sha256"):
                        return str(p["sha256"])
        return sha256_fil(fil)

    def lager_raster(self, korning_id: str, lager_id: str) -> dict[str, Any]:
        """Lagrets raster (rad 0 = norr, NaN där värde saknas), utbredning, cellstorlek och takmask.

        Samma raster som visas i kartan; inget räknas om."""
        k = self._hamta(korning_id)
        _kontrollera_id(lager_id, "lager-id")
        lager = self.resultat(korning_id)["lager"]
        la = next((x for x in lager if x["id"] == lager_id), None)
        if la is None:
            raise FlodenWebbFel(f"Okänt lager '{lager_id}'.", status_kod=404)
        takfil = k.mapp / "webb" / f"{lager_id}.tak.npy"
        return {
            "lager": la,
            "raster": np.load(k.mapp / "webb" / f"{lager_id}.npy"),
            "tak": np.load(takfil) if takfil.is_file() else None,
        }

    def _projektpolygon(self, k: Korning) -> Polygon | None:
        """Projektets avgränsningspolygon (`avgransning_polygon_3011` i `projekt.json`) för
        körningens projekt, eller `None`: körning utan projekt (demokvarteret, testsviten),
        projekt utan polygon (ritat som rektangel eller skapat före polygonstödet) och polygon
        som inte går att läsa. Då räknas hela rutan som förut."""
        if not k.projekt_id or self.projektrot is None or not self.projektrot.is_dir():
            return None
        for fil in sorted(self.projektrot.glob("*/projekt.json")):
            try:
                d = json.loads(fil.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # Registrets id; lokalt utan inloggning har projektet bara projektid.
            pid = (d.get("register_id") or d.get("projekt_id")) if isinstance(d, dict) else None
            if pid == k.projekt_id and isinstance(d, dict):
                return avgr.fran_projekt(d)
        return None

    def projekt_avgransning(self, korning_id: str) -> dict[str, Any] | None:
        """Polygonen som avgör vad som räknas till körningens projekt, som GeoJSON (EPSG:3011) med
        yta, eller `None` (hela rutan räknas)."""
        poly = self._projektpolygon(self._hamta(korning_id))
        if poly is None:
            return None
        return {"polygon_3011": avgr.till_geojson(poly), "yta_m2": float(poly.area), "epsg": 3011}

    def _inne_i_projektet(
        self, k: Korning, lager: dict[str, Any], rader: int, kolumner: int
    ) -> np.ndarray[Any, Any] | None:
        """Cellmask (sant = cellens mittpunkt är inne i projektets polygon) för ett lager, eller
        `None` utan polygon."""
        poly = self._projektpolygon(k)
        if poly is None:
            return None
        u = lager["utbredning"]
        return avgr.celler_inne(
            poly,
            x_min=float(u["x_min"]),
            y_max=float(u["y_max"]),
            pixel_m=float(lager["pixelstorlek_m"]),
            rader=rader,
            kolumner=kolumner,
        )

    def _markvarden(
        self, korning_id: str, lager: dict[str, Any]
    ) -> tuple[np.ndarray[Any, Any], float] | None:
        """Lagrets värden på **mark** (ändliga celler utanför takmasken) och cellens yta (m²).

        Samma takmask som `marksiffror` och kartan (`<lager>.tak.npy`). Har projektet en polygon
        räknas bara celler vars mittpunkt är inne i den; annars hela rutan. `None` om lagret saknar
        markvärden."""
        k = self._hamta(korning_id)
        lager_id = str(lager["id"])
        fil = k.mapp / "webb" / f"{lager_id}.npy"
        if not fil.is_file():
            return None
        d = np.asarray(np.load(fil), dtype=np.float64)
        giltig = np.isfinite(d)
        takfil = k.mapp / "webb" / f"{lager_id}.tak.npy"
        if takfil.is_file():
            giltig &= ~np.load(takfil)
        inne = self._inne_i_projektet(k, lager, d.shape[0], d.shape[1])
        if inne is not None:
            giltig &= inne
        if not giltig.any():
            return None
        px = float(lager["pixelstorlek_m"])
        return d[giltig], px * px

    def marksiffror(
        self, korning_id: str, lager: dict[str, Any], troskel: float
    ) -> dict[str, float] | None:
        """Ytor (m²) och största förändring på **mark** i ett skillnadslager, utan byggnadernas tak.

        Den enda funktionen för dessa mått: projektsidan och MCP-servern använder den. Takmasken är
        den som kartan ritar bilderna med (`<lager>.tak.npy`); kärnans egen jämförelse räknar alla
        celler, tak inräknade. Har projektet en polygon räknas bara celler inne i den. `None` om
        lagret inte har något markvärde."""
        k = self._hamta(korning_id)
        fil = k.mapp / "webb" / f"{lager['id']}.npy"
        if not fil.is_file():
            return None
        d = np.asarray(np.load(fil), dtype=np.float64)
        giltig = np.isfinite(d)
        takfil = k.mapp / "webb" / f"{lager['id']}.tak.npy"
        if takfil.is_file():
            giltig &= ~np.load(takfil)
        inne = self._inne_i_projektet(k, lager, d.shape[0], d.shape[1])
        if inne is not None:
            giltig &= inne
        if not giltig.any():
            return None
        cell = float(lager["pixelstorlek_m"]) ** 2
        v = d[giltig]
        return {
            "yta_minskning_m2": float(np.count_nonzero(v < -troskel)) * cell,
            "yta_okning_m2": float(np.count_nonzero(v > troskel)) * cell,
            "max_minskning": float(max(0.0, -v.min())),
            "max_okning": float(max(0.0, v.max())),
        }

    def markyta_over(
        self, korning_id: str, lager: dict[str, Any], troskel: float, riktning: str
    ) -> float | None:
        """Yta (m²) på **mark** där ett skillnadslager har minskat (`minskning`) eller ökat
        (`okning`) mer än `troskel`, med samma takmask som `marksiffror`.

        Används för rapportmåttet "yta som förlorar mer än X timmar sol". `None` om lagret saknar
        markvärden."""
        if riktning not in ("minskning", "okning"):
            raise FlodenWebbFel("riktning ska vara 'minskning' eller 'okning'.", status_kod=422)
        if not (troskel >= 0.0 and np.isfinite(troskel)):
            raise FlodenWebbFel("Tröskeln ska vara ett tal som inte är negativt.", status_kod=422)
        mark = self._markvarden(korning_id, lager)
        if mark is None:
            return None
        v, cell = mark
        antal = (
            np.count_nonzero(v < -troskel)
            if riktning == "minskning"
            else np.count_nonzero(v > troskel)
        )
        return float(antal) * cell

    def omradesstatistik(
        self, korning_id: str, lager_id: str, ring: list[list[float]]
    ) -> dict[str, Any]:
        """Min, medel, median och max av lagrets celler vars mittpunkt ligger i polygonen `ring`.

        Byggnadernas tak räknas inte som mark (samma regel som kartan): celler i takmasken
        utesluts och antalet redovisas. För skillnadslager redovisas även ytan som ökat och
        minskat över lagrets tröskel (samma regel som jämförelsens ytmått). Polygonen anges i
        EPSG:3011."""
        d = self.lager_raster(korning_id, lager_id)
        la, raster, tak = d["lager"], d["raster"], d["tak"]
        if len(ring) < 3:
            raise FlodenWebbFel("Polygonen behöver minst tre hörn.", status_kod=422)
        try:
            poly = Polygon([(float(p[0]), float(p[1])) for p in ring])
        except (TypeError, ValueError, IndexError) as exc:
            raise FlodenWebbFel("Polygonens hörn ska vara [e, n]-par.", status_kod=422) from exc
        if not poly.is_valid or poly.area <= 0.0:
            raise FlodenWebbFel(
                "Polygonen är ogiltig (korsar sig själv eller har ingen yta).", status_kod=422
            )
        u, px = la["utbredning"], float(la["pixelstorlek_m"])
        rader, kolumner = raster.shape
        xs = u["x_min"] + (np.arange(kolumner) + 0.5) * px
        ys = u["y_max"] - (np.arange(rader) + 0.5) * px
        gx, gy = np.meshgrid(xs, ys)
        inne = np.asarray(contains_xy(poly, gx, gy), dtype=bool)
        antal_i_omradet = int(inne.sum())
        tak_ute = inne & tak if tak is not None else np.zeros_like(inne)
        giltig = inne & ~tak_ute & np.isfinite(raster)
        v = raster[giltig].astype(np.float64)
        ut: dict[str, Any] = {
            "lager_id": lager_id,
            "typ": la["typ"],
            "scenario": la["scenario"],
            "etikett": la["etikett"],
            "enhet": la["enhet"],
            "cellstorlek_m": px,
            "antal_celler_i_omradet": antal_i_omradet,
            "antal_celler_tak_uteslutna": int(tak_ute.sum()),
            "antal_celler_med_varde": int(v.size),
            "yta_med_varde_m2": float(v.size) * px * px,
        }
        if v.size:
            ut.update(
                min=float(v.min()),
                medel=float(v.mean()),
                median=float(np.median(v)),
                max=float(v.max()),
            )
            if la["typ"] == "skillnad":
                t = float(la["teckenforklaring"].get("troskel", 0.0))
                ut["troskel"] = t
                ut["yta_okning_m2"] = float((v > t).sum()) * px * px
                ut["yta_minskning_m2"] = float((v < -t).sum()) * px * px
        else:
            ut.update(min=None, medel=None, median=None, max=None)
        return ut

    # Filer och tabeller --------------------------------------------------------------------

    def _fil_i_resultat(self, k: Korning, rel: str) -> Path:
        if k.status != "klar":
            raise FlodenWebbFel("Körningen är inte klar.", status_kod=409)
        rot = k.utmapp.resolve()
        fil = (rot / rel).resolve()
        if not fil.is_relative_to(rot) or not fil.is_file():
            raise FlodenWebbFel(f"Okänd fil '{rel}'.", status_kod=404)
        return fil

    def fil_sokvag(self, korning_id: str, rel: str) -> Path:
        """Sökväg till en fil i en klar körnings resultat (för kod i webbpaketet)."""
        return self._fil_i_resultat(self._hamta(korning_id), rel)

    def fil(self, korning_id: str, rel: str) -> tuple[bytes, str]:
        fil = self._fil_i_resultat(self._hamta(korning_id), rel)
        typer = {
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".csv": "text/csv; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".geojson": "application/geo+json; charset=utf-8",
            ".png": "image/png",
        }
        return fil.read_bytes(), typer.get(fil.suffix.lower(), "application/octet-stream")

    def _vektorlager(self, k: Korning, s: dict[str, Any]) -> list[dict[str, Any]]:
        """Vektorfiler (GeoJSON) som lösarna skrivit i resultatet (t.ex. `rannsten_flode`)."""
        ut: list[dict[str, Any]] = []
        for j in s["jobb"]:
            for roll, namn in self._roller(k, j).items():
                if namn.lower().endswith(".geojson"):
                    fil = self._jobbmapp(k, j) / namn
                    etikett, enhet = etikett_och_enhet(roll)
                    ut.append(
                        {
                            "analys": j["analys"],
                            "scenario": j["scenario"],
                            "ruta": j.get("ruta"),
                            "roll": roll,
                            "etikett": etikett,
                            "enhet": enhet,
                            "fil": namn,
                            "url": self._url_fil(k, fil),
                            "vektor_url": (
                                f"/api/korningar/{k.korning_id}/vektor/{j['scenario']}/{roll}"
                            ),
                        }
                    )
        return ut

    def _datatabeller(self, k: Korning, s: dict[str, Any]) -> list[dict[str, Any]]:
        ut: list[dict[str, Any]] = []
        for j in s["jobb"]:
            for roll, namn in self._roller(k, j).items():
                if namn.lower().endswith((".csv", ".parquet")):
                    fil = self._jobbmapp(k, j) / namn
                    ut.append(
                        {
                            "analys": j["analys"],
                            "scenario": j["scenario"],
                            "ruta": j.get("ruta"),
                            "roll": roll,
                            "fil": namn,
                            "url": self._url_fil(k, fil),
                            "forhandsvisning": self._url_fil(k, fil).replace("/fil/", "/tabell/"),
                        }
                    )
        return ut

    def tabell(self, korning_id: str, rel: str) -> dict[str, Any]:
        """Första raderna ur en CSV- eller Parquet-fil i resultatet."""
        fil = self._fil_i_resultat(self._hamta(korning_id), rel)
        if fil.suffix.lower() == ".csv":
            with fil.open(encoding="utf-8", newline="") as fh:
                läsare = csv.DictReader(fh)
                kolumner = list(läsare.fieldnames or [])
                rader: list[dict[str, str]] = []
                antal = 0
                for rad in läsare:
                    antal += 1
                    if len(rader) < MAX_TABELLRADER:
                        rader.append(rad)
            return {"fil": rel, "kolumner": kolumner, "rader": rader, "antal_rader": antal}
        if fil.suffix.lower() == ".parquet":
            pq: Any = importlib.import_module("pyarrow.parquet")
            tabell = pq.read_table(fil)
            ark = tabell.slice(0, MAX_TABELLRADER).to_pylist()
            return {
                "fil": rel,
                "kolumner": list(tabell.column_names),
                "rader": [{kol: _json_varde(v) for kol, v in r.items()} for r in ark],
                "antal_rader": tabell.num_rows,
            }
        raise FlodenWebbFel(f"Bara CSV och Parquet kan visas som tabell ('{rel}').", status_kod=415)


def _json_varde(v: Any) -> Any:
    return v if v is None or isinstance(v, str | int | float | bool) else str(v)


def _beskriv_plan(plan: Plan) -> dict[str, Any]:
    scenarier = []
    for sid, sc in plan.scenarier.items():
        scenarier.append(
            {
                "id": sid,
                "nyckel": sc.nyckel,
                "antal_rutor": len(sc.rutor),
                "rutor": [
                    {
                        "ruta_id": r.ruta_id,
                        "x_min": r.x_min,
                        "y_min": r.y_min,
                        "x_max": r.x_max,
                        "y_max": r.y_max,
                        "hash": r.hash[:12],
                    }
                    for r in sc.rutor
                ],
            }
        )
    alla = [r for sc in plan.scenarier.values() for r in sc.rutor]
    utbredning = (
        {
            "x_min": min(r.x_min for r in alla),
            "y_min": min(r.y_min for r in alla),
            "x_max": max(r.x_max for r in alla),
            "y_max": max(r.y_max for r in alla),
        }
        if alla
        else None
    )
    jobb = [
        {
            "etikett": j.etikett,
            "analys": j.analys.id,
            "scenario": j.scenario.id,
            "ruta": None if j.ruta is None else j.ruta.ruta_id,
            "losare": j.losare.namn,
            "version": j.losare.version,
            "radie_m": j.radie_m,
            "grannar": [g.ruta_id for g in j.grannar],
            "nyckel": j.nyckel[:12],
            "i_cache": j.i_cache,
        }
        for j in plan.jobb
    ]
    per_analys: dict[str, dict[str, int]] = {}
    for j in plan.jobb:
        p = per_analys.setdefault(j.analys.id, {"jobb": 0, "i_cache": 0, "att_kora": 0})
        p["jobb"] += 1
        p["i_cache" if j.i_cache else "att_kora"] += 1
    return {
        "sammanfattning": plan.sammanfattning(),
        "per_analys": per_analys,
        "scenarier": scenarier,
        "utbredning": utbredning,
        "jobb": jobb,
    }


def _matvardestabeller(s: dict[str, Any]) -> list[dict[str, Any]]:
    """Mätvärden per analys: en rad per jobb (scenario × ruta), kolumner = mätvärdesnamn."""
    per: dict[str, dict[str, Any]] = {}
    for j in s["jobb"]:
        t = per.setdefault(
            j["analys"],
            {"analys": j["analys"], "losare": j["losare"], "kolumner": [], "rader": []},
        )
        for namn in j["matvarden"]:
            if namn not in t["kolumner"]:
                t["kolumner"].append(namn)
        t["rader"].append(
            {
                "scenario": j["scenario"],
                "ruta": j.get("ruta"),
                "fran_cache": j["fran_cache"],
                "kortid_s": j["kortid_s"],
                "varden": j["matvarden"],
            }
        )
    return list(per.values())


_TAL_I_TEXT = re.compile(r"\d+(?:[.,]\d+)?")


def _varningar(s: dict[str, Any]) -> list[dict[str, Any]]:
    """Lösarnas varningar som grupper.

    Varningar som bara skiljer sig i tal (t.ex. antal fasadpunkter per ruta) hamnar i samma grupp;
    gruppen visar första texten och listar alla varianter. Inget tal ändras eller summeras.
    """
    grupper: dict[str, dict[str, Any]] = {}
    for j in s["jobb"]:
        for text in j["varningar"]:
            g = grupper.setdefault(
                _TAL_I_TEXT.sub("#", text),
                {
                    "text": text,
                    "antal_jobb": 0,
                    "analyser": set(),
                    "scenarier": set(),
                    "varianter": {},
                },
            )
            g["antal_jobb"] += 1
            g["analyser"].add(j["analys"])
            g["scenarier"].add(j["scenario"])
            g["varianter"][text] = g["varianter"].get(text, 0) + 1
    return [
        {
            "text": g["text"],
            "kalla": "losare",
            "antal_jobb": g["antal_jobb"],
            "analyser": sorted(g["analyser"]),
            "scenarier": sorted(g["scenarier"]),
            "varianter": (
                [{"text": t, "antal_jobb": n} for t, n in sorted(g["varianter"].items())]
                if len(g["varianter"]) > 1
                else []
            ),
        }
        for g in grupper.values()
    ]


def _webbvarning(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "kalla": "webb",
        "antal_jobb": 0,
        "analyser": [],
        "scenarier": [],
        "varianter": [],
    }
