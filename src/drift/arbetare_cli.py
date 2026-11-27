# SPDX-License-Identifier: GPL-3.0-or-later
"""Enhetlig GPU/CPU-arbetare (`floden drift arbetare`) och diskstädning (`floden drift stada-disk`).

Bygger ovanpå `drift.jobbko.JobbKo` (S5k pull-kö), `gemensamt.gpu` och `gemensamt.objektlagring`
så att samma Docker-avbildning kan startas både som webbserver idag och som fristående CPU- eller
GPU-arbetare imorgon — utan kodändring.

Feltillstånd som hanteras deterministiskt:
- `--enhet auto`: väljer CUDA/CuPy om tillgängligt, annars CPU utan krasch.
- `--enhet cuda`: fail-fast med tydligt svenskt felmeddelande och icke-noll slutkod om CUDA/CuPy
  saknas (aldrig tyst fallback till CPU).
- Graceful shutdown (`SIGTERM`/`SIGINT`): slutar hämta nya jobb omedelbart och släpper pågående
  lease rent (återköar om försök återstår) så jobb inte blir fast i `pagaende`.
- Lease-heartbeat-tråd under långa jobb: förnyar leasen automatiskt vid halva `lease_s` och
  stoppas/joinas garanterat i `finally` (0 tråd-läckor).
- Giftiga jobb / undantag i exekveraren: fångas, saneras från PII och nycklar/lösenord, markeras
  misslyckade i `JobbKo` och arbetarloopen fortsätter med nästa jobb.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
import re
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal

from drift.jobbko import (
    STANDARD_LEASE_SEKUNDER,
    TILLATNA_JOBBTYPER,
    JobbKo,
    JobbKoFel,
    JobbPost,
    LeaseOgiltigFel,
    PiiForbjudetFel,
)
from gemensamt.gpu import BackendNamn, GpuFel, hamta_backend, tillgangligt_vram_mb
from gemensamt.hash import sha256_fil
from gemensamt.konfig import KonfigFel, las_konfig
from gemensamt.lokal_cache import LokalCacheFel
from gemensamt.lokal_diskstadare import (
    LokalDiskStadareFel,
    disk_utrymme_status,
    las_lokal_disk_konfig,
    stada_lokal_disk,
)
from gemensamt.objektlagring import (
    LagringTyp,
    LokalLagring,
    ObjektLagring,
    ObjektLagringFel,
    las_lagring_konfig,
    skapa_lagring,
)

_logg = logging.getLogger(__name__)

FLODEN_JOBBKO_ROT_ENV: Final[str] = "FLODEN_JOBBKO_ROT"
STANDARD_KO_ROT: Final[Path] = Path("data/lokal/drift")
STANDARD_KO_FILNAMN: Final[str] = "jobbko.sqlite"
STANDARD_POLL_INTERVALL_S: Final[float] = 2.0
_MIN_HEARTBEAT_S: Final[float] = 0.05

_ANONYMT_OGILTIGT_RE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_.\-]+")
_EPOST_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)
_HEMLIGHET_NYCKEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b("
    r"secret(?:[_-]?key)?|access[_-]?key|api[_-]?key|password|losenord|token|"
    r"nyckelhemlighet|authorization"
    r")\s*([:=])\s*(?:bearer\s+)?\S+"
)
_BEARER_RE: Final[re.Pattern[str]] = re.compile(r"(?i)\bbearer\s+\S+")
_FLODEN_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\bfl_mcp_[A-Za-z0-9_\-]+\b")
_AWS_KEY_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")

AvslutningsOrsak = Literal["en_gang", "max_jobb", "stoppad", "tom_ko"]


class ArbetareFel(Exception):
    """Konfigurations- eller uppstartsfel för `floden drift arbetare`."""


class ArbetareAvbrutenFel(Exception):
    """Kastas av en exekverare som avbryter kontrollerat vid graceful shutdown."""


class ArbetareJobbFel(Exception):
    """Fel i ett enskilt jobb som fångas av arbetarloopen och markerar jobbet misslyckat."""


@dataclass(frozen=True)
class JobbKontext:
    """Körkontext som skickas in till jobbköraren för varje leasat jobb."""

    arbetare_id: str
    enhet: BackendNamn
    lagring: ObjektLagring
    lagring_typ: LagringTyp
    arbetsrot: Path
    stopp_event: threading.Event
    lease_forlorad: threading.Event


@dataclass(frozen=True)
class JobbKorUtfall:
    """Strukturerat resultat från en jobbkörare."""

    faktisk_kostnad_sek: float = 0.0
    resultat_meta: Mapping[str, Any] = field(default_factory=dict)


JobbExekverare = Callable[[JobbPost, JobbKontext], JobbKorUtfall | Mapping[str, Any] | None]


@dataclass(frozen=True)
class ArbetareResultat:
    """Sammanfattning av en arbetarkörning (PII-fri)."""

    arbetare_id: str
    enhet: BackendNamn
    lagring_typ: LagringTyp
    antal_behandlade: int
    antal_klara: int
    antal_misslyckade: int
    antal_avbrutna: int
    antal_heartbeats: int
    avslutningsorsak: AvslutningsOrsak
    kortid_s: float

    def till_dict(self) -> dict[str, Any]:
        return {
            "arbetare_id": self.arbetare_id,
            "enhet": self.enhet,
            "lagring_typ": self.lagring_typ,
            "antal_behandlade": self.antal_behandlade,
            "antal_klara": self.antal_klara,
            "antal_misslyckade": self.antal_misslyckade,
            "antal_avbrutna": self.antal_avbrutna,
            "antal_heartbeats": self.antal_heartbeats,
            "avslutningsorsak": self.avslutningsorsak,
            "kortid_s": round(self.kortid_s, 4),
        }


def standard_arbetare_id(*, hostname: str | None = None, pid: int | None = None) -> str:
    """Bygger ett säkert, anonymt arbetar-ID (`<hostname>-<pid>`) kompatibelt med `JobbKo`."""
    raa_vard = (socket.gethostname() if hostname is None else hostname).strip()
    raa_pid = os.getpid() if pid is None else int(pid)
    ren_vard = _ANONYMT_OGILTIGT_RE.sub("-", raa_vard).strip("-_.")
    if not ren_vard or not ren_vard[0].isalnum():
        ren_vard = "arbetare"
    suffix = f"-{raa_pid}"
    max_vard = max(1, 128 - len(suffix))
    return f"{ren_vard[:max_vard]}{suffix}"


def los_ko_databas(
    ko_rot: str | Path | None = None,
    *,
    miljo: Mapping[str, str] | None = None,
) -> Path:
    """Löser sökvägen till jobbköns SQLite-fil ur argument eller `FLODEN_JOBBKO_ROT`."""
    env = os.environ if miljo is None else miljo
    if ko_rot is not None and str(ko_rot).strip():
        sokvag = Path(ko_rot)
    else:
        fran_env = env.get(FLODEN_JOBBKO_ROT_ENV, "").strip()
        sokvag = Path(fran_env) if fran_env else STANDARD_KO_ROT

    if sokvag.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return sokvag
    return sokvag / STANDARD_KO_FILNAMN


def los_enhet(enhet: str = "auto") -> BackendNamn:
    """Validerar och detekterar beräkningsenhet (`auto`, `cpu`, `cuda`) utan tyst fallback."""
    try:
        _xp, vald = hamta_backend(enhet)
    except GpuFel as exc:
        raise ArbetareFel(str(exc)) from exc
    return vald


def sanera_feltext(exc_eller_text: BaseException | str, *, max_tecken: int = 500) -> str:
    """Formaterar en kort, ren feltext utan PII (e-post) eller hemligheter/nycklar."""
    if isinstance(exc_eller_text, BaseException):
        text = f"{type(exc_eller_text).__name__}: {exc_eller_text}"
    else:
        text = str(exc_eller_text)
    ren = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    ren = _EPOST_RE.sub("[redakterad-epost]", ren)
    ren = _HEMLIGHET_NYCKEL_RE.sub(r"\1\2[redakterad]", ren)
    ren = _BEARER_RE.sub("Bearer [redakterad]", ren)
    ren = _FLODEN_TOKEN_RE.sub("[redakterad-token]", ren)
    ren = _AWS_KEY_RE.sub("[redakterad-nyckel]", ren)
    if not ren:
        return "Okänt fel i arbetare."
    return ren[:max_tecken]


def _las_valfri_konfig(konfig_fil: Path | None) -> dict[str, Any]:
    if konfig_fil is None or not konfig_fil.is_file():
        return {}
    lokal = konfig_fil.parent / "config.local.toml"
    try:
        return las_konfig(konfig_fil, lokal if lokal.is_file() else None)
    except KonfigFel as exc:
        raise ArbetareFel(str(exc)) from exc


def skapa_arbetare_lagring(
    lagring_typ: LagringTyp = "lokal",
    *,
    konfig_fil: Path | None = None,
    lagring_rot: Path | None = None,
    miljo: Mapping[str, str] | None = None,
) -> ObjektLagring:
    """Skapar `LokalLagring` (utan krav på S3) eller `S3Lagring` via `ObjektLagring`."""
    if lagring_typ not in ("lokal", "s3"):
        raise ArbetareFel(f"Ogiltig lagringstyp {lagring_typ!r}; välj 'lokal' eller 's3'.")
    if lagring_typ == "lokal":
        if lagring_rot is not None:
            return LokalLagring(lagring_rot)
        konfig_dict = _las_valfri_konfig(konfig_fil)
        try:
            lk = las_lagring_konfig(konfig_dict, miljo=miljo)
        except ObjektLagringFel as exc:
            raise ArbetareFel(str(exc)) from exc
        return LokalLagring(lk.rot)

    konfig_dict = _las_valfri_konfig(konfig_fil)
    try:
        lk = las_lagring_konfig(konfig_dict, miljo=miljo)
        if lk.typ != "s3":
            lk = replace(lk, typ="s3")
        return skapa_lagring(lk)
    except ObjektLagringFel as exc:
        raise ArbetareFel(str(exc)) from exc


def _ladda_upp_katalog(
    lagring: ObjektLagring,
    katalog: Path,
    prefix: str,
) -> list[str]:
    """Laddar upp alla vanliga filer i `katalog` under `prefix` med SHA-256-verifiering."""
    ren_prefix = prefix.strip().strip("/")
    uppladdade: list[str] = []
    for fil in sorted(katalog.rglob("*")):
        if fil.is_symlink() or not fil.is_file():
            continue
        rel = fil.relative_to(katalog).as_posix()
        nyckel = f"{ren_prefix}/{rel}" if ren_prefix else rel
        lagring.ladda_upp_fil(fil, nyckel, sha256=sha256_fil(fil))
        uppladdade.append(nyckel)
    return uppladdade


def _kor_k20_fraga_jobb(jobb: JobbPost, kontext: JobbKontext) -> JobbKorUtfall:
    """Kör en K20-utredningsfråga (`fraga_fil`) via `karna.utredning` och `karna.korplan`."""
    from karna.korplan import kor_utredning, planera
    from karna.resultatcache import ResultatCache
    from karna.utredning import las_fraga

    nyttolast = jobb.nyttolast
    fraga_fil_raw = nyttolast.get("fraga_fil")
    if not isinstance(fraga_fil_raw, str) or not fraga_fil_raw.strip():
        raise ArbetareJobbFel("Nyttolast saknar giltig 'fraga_fil'.")
    fraga_fil = Path(fraga_fil_raw)
    if not fraga_fil.is_file():
        raise ArbetareJobbFel(f"Utredningsfråga hittades inte: {fraga_fil.name}")

    cache_raw = nyttolast.get("cache_mapp")
    cache_mapp = (
        Path(cache_raw)
        if isinstance(cache_raw, str) and cache_raw.strip()
        else kontext.arbetsrot / "cache"
    )
    ut_raw = nyttolast.get("ut_mapp")
    ut_mapp = (
        Path(ut_raw)
        if isinstance(ut_raw, str) and ut_raw.strip()
        else kontext.arbetsrot / "resultat"
    )

    fraga = las_fraga(fraga_fil)
    cache = ResultatCache(cache_mapp)
    plan = planera(fraga, cache)
    if kontext.stopp_event.is_set():
        raise ArbetareAvbrutenFel("Avbröts före körning av utredningsplan.")
    kor_utredning(fraga, cache, ut_mapp, plan=plan)

    ut_prefix_raw = nyttolast.get("ut_prefix")
    uppladdade: list[str] = []
    if isinstance(ut_prefix_raw, str) and ut_prefix_raw.strip():
        uppladdade = _ladda_upp_katalog(kontext.lagring, ut_mapp, ut_prefix_raw)
    elif kontext.lagring_typ == "s3":
        uppladdade = _ladda_upp_katalog(
            kontext.lagring, ut_mapp, f"projekt/{jobb.projekt_id}/jobb/{jobb.jobb_id}"
        )

    return JobbKorUtfall(
        faktisk_kostnad_sek=0.0,
        resultat_meta={
            "typ": jobb.typ,
            "enhet": kontext.enhet,
            "antal_planjobb": len(plan.jobb),
            "antal_uppladdade": len(uppladdade),
        },
    )


def _kor_2d_k1_jobb(jobb: JobbPost, kontext: JobbKontext) -> JobbKorUtfall:
    """Kör direkt 2D lokal tröghet på en K1-mapp med vald beräkningsenhet (`cpu`/`cuda`)."""
    import numpy as np

    from gemensamt.regn import las_regn_k4_csv, skapa_regn_fran_spec
    from testsvit.k1_io import las_geotiff
    from tvad.lokal_troghet import kor_2d_lokal_troghet, skriv_tvad_resultat_k6

    nyttolast = jobb.nyttolast
    k1_raw = nyttolast.get("k1_mapp")
    if not isinstance(k1_raw, str) or not k1_raw.strip():
        raise ArbetareJobbFel("2D-jobb kräver 'fraga_fil' eller 'k1_mapp' i nyttolast.")
    k1_mapp = Path(k1_raw)
    if not (k1_mapp / "mark.tif").is_file() or not (k1_mapp / "markslag.tif").is_file():
        raise ArbetareJobbFel("K1-mappen saknar mark.tif eller markslag.tif.")

    mark_raw, geo = las_geotiff(k1_mapp / "mark.tif")
    slag_raw, _ = las_geotiff(k1_mapp / "markslag.tif")
    mark_z = np.asarray(mark_raw, dtype=np.float32)
    markslag = np.asarray(slag_raw, dtype=np.uint8)

    if isinstance(nyttolast.get("regn_csv"), str) and str(nyttolast["regn_csv"]).strip():
        serie = las_regn_k4_csv(Path(str(nyttolast["regn_csv"])))
    elif isinstance(nyttolast.get("regn_id"), str) and str(nyttolast["regn_id"]).strip():
        serie = skapa_regn_fran_spec(regn_id=str(nyttolast["regn_id"]))
    elif nyttolast.get("aterkomsttid_ar") is not None:
        serie = skapa_regn_fran_spec(
            aterkomsttid_ar=float(nyttolast["aterkomsttid_ar"]),
            klimatfaktor=float(nyttolast.get("klimatfaktor", 1.0)),
            varaktighet_min=float(nyttolast.get("varaktighet_min", 60.0)),
        )
    elif (k1_mapp / "regn.csv").is_file():
        serie = las_regn_k4_csv(k1_mapp / "regn.csv")
    else:
        raise ArbetareJobbFel("2D-jobb saknar regnindata (regn_id, regn_csv eller regn.csv).")

    precision_raw = str(nyttolast.get("precision", "f64"))
    precision: Literal["f64", "f32"] = "f32" if precision_raw == "f32" else "f64"
    subgrid = int(nyttolast.get("subgrid_faktor", 1))
    slut_tid_raw = nyttolast.get("slut_tid_s")
    slut_tid = None if slut_tid_raw is None else float(slut_tid_raw)

    if kontext.stopp_event.is_set():
        raise ArbetareAvbrutenFel("Avbröts före 2D-simulering.")

    res = kor_2d_lokal_troghet(
        mark_z,
        markslag,
        geo,
        serie.tid_s,
        serie.intensitet_mm_h,
        slut_tid_s=slut_tid,
        subgrid_faktor=subgrid,
        enhet=kontext.enhet,
        precision=precision,
    )

    ut_raw = nyttolast.get("ut_mapp")
    ut_mapp = (
        Path(ut_raw)
        if isinstance(ut_raw, str) and ut_raw.strip()
        else kontext.arbetsrot / "resultat_2d"
    )
    skriv_tvad_resultat_k6(
        res,
        geo,
        ut_mapp,
        parametrar={
            "jobb_id": jobb.jobb_id,
            "enhet": kontext.enhet,
            "precision": precision,
            "regn_id": serie.regn_id,
        },
    )

    ut_prefix_raw = nyttolast.get("ut_prefix")
    uppladdade: list[str] = []
    if isinstance(ut_prefix_raw, str) and ut_prefix_raw.strip():
        uppladdade = _ladda_upp_katalog(kontext.lagring, ut_mapp, ut_prefix_raw)
    elif kontext.lagring_typ == "s3":
        uppladdade = _ladda_upp_katalog(
            kontext.lagring, ut_mapp, f"projekt/{jobb.projekt_id}/jobb/{jobb.jobb_id}"
        )

    max_djup = float(np.nanmax(res.max_djup_m)) if res.max_djup_m.size > 0 else 0.0
    return JobbKorUtfall(
        faktisk_kostnad_sek=0.0,
        resultat_meta={
            "typ": "2d",
            "enhet": kontext.enhet,
            "berakningsplats": res.berakningsplats,
            "max_djup_m": round(max_djup, 6),
            "volymfel_rel": float(res.volymfel_rel),
            "kortid_s": round(float(res.kortid_s), 4),
            "antal_uppladdade": len(uppladdade),
        },
    )


def _kor_batch_jobb(jobb: JobbPost, kontext: JobbKontext) -> JobbKorUtfall:
    """Kör rutvis förberäkning (`karna.batch`) med vald beräkningsenhet och lagring."""
    from karna.batch import (
        BatchKonfig,
        generera_rutor_for_omrade,
        kontrollera_dataskydd,
        kor_batch_rutor,
        skapa_k1_rutkorare,
    )

    nyttolast = jobb.nyttolast
    omrade_raw = nyttolast.get("omrade_fil") or nyttolast.get("omrade")
    if not isinstance(omrade_raw, str) or not omrade_raw.strip():
        raise ArbetareJobbFel("Batch-jobb saknar 'omrade_fil' i nyttolast.")
    omrade_fil = Path(omrade_raw)

    moduler_raw = nyttolast.get("moduler", ["vatten2d", "dagsljus"])
    if isinstance(moduler_raw, str):
        moduler = tuple(m.strip() for m in moduler_raw.split(",") if m.strip())
    elif isinstance(moduler_raw, Sequence):
        moduler = tuple(str(m).strip() for m in moduler_raw if str(m).strip())
    else:
        raise ArbetareJobbFel("Batch-jobb har ogiltigt fält 'moduler'.")

    dataklass_raw = str(nyttolast.get("dataklass", "oppen"))
    dataklass: Literal["oppen", "intern", "skyddad"]
    if dataklass_raw == "intern":
        dataklass = "intern"
    elif dataklass_raw == "skyddad":
        dataklass = "skyddad"
    else:
        dataklass = "oppen"
    kontrollera_dataskydd(dataklass)

    precision_raw = str(nyttolast.get("precision", "f64"))
    precision: Literal["f64", "f32"] = "f32" if precision_raw == "f32" else "f64"

    utdata_raw = nyttolast.get("utdata_rot")
    utdata_rot = (
        Path(utdata_raw)
        if isinstance(utdata_raw, str) and utdata_raw.strip()
        else kontext.arbetsrot / "batch_ut"
    )
    rutstorlek_m = float(nyttolast.get("rutstorlek_m", 500.0))
    overlapp_m = float(nyttolast.get("overlapp_m", 25.0))
    upplosning_m = float(nyttolast.get("upplosning_m", 1.0))

    rutor = generera_rutor_for_omrade(
        omrade_fil,
        rutstorlek_m=rutstorlek_m,
        overlapp_m=overlapp_m,
        upplosning_m=upplosning_m,
    )
    konfig = BatchKonfig(
        moduler=moduler,
        utdata_rot=utdata_rot,
        enhet=kontext.enhet,
        precision=precision,
        dataklass=dataklass,
        rutstorlek_m=rutstorlek_m,
        overlapp_m=overlapp_m,
        upplosning_m=upplosning_m,
        max_arbetare=int(nyttolast.get("max_arbetare", 1)),
        kommun=str(nyttolast.get("kommun", "standard")),
        lagring_typ=kontext.lagring_typ,
        indata_filer=(omrade_fil,),
    )
    k1_rot_raw = nyttolast.get("k1_rot")
    rutkorare = (
        skapa_k1_rutkorare(Path(k1_rot_raw))
        if isinstance(k1_rot_raw, str) and k1_rot_raw.strip()
        else None
    )
    if kontext.stopp_event.is_set():
        raise ArbetareAvbrutenFel("Avbröts före batchkörning.")

    status = kor_batch_rutor(
        rutor,
        konfig,
        rutkorare=rutkorare,
        objektlagring=kontext.lagring if kontext.lagring_typ == "s3" else None,
    )
    if status.felade:
        raise ArbetareJobbFel(
            f"Batchkörning fick {len(status.felade)} felade rutor av {status.totalt_rutor}."
        )
    return JobbKorUtfall(
        faktisk_kostnad_sek=0.0,
        resultat_meta={
            "typ": "batch",
            "enhet": kontext.enhet,
            "berakningsplats": status.berakningsplats,
            "totalt_rutor": status.totalt_rutor,
            "klara": len(status.klara),
            "hoppade": len(status.hoppade),
            "total_tid_s": round(status.total_tid_s, 4),
        },
    )


def standard_exekverare(jobb: JobbPost, kontext: JobbKontext) -> JobbKorUtfall:
    """Standardexekverare kopplad till Flodens verkliga körpipeline (`karna`, `tvad`, `batch`)."""
    if jobb.typ == "batch":
        return _kor_batch_jobb(jobb, kontext)
    if jobb.typ == "2d":
        if "fraga_fil" in jobb.nyttolast:
            return _kor_k20_fraga_jobb(jobb, kontext)
        return _kor_2d_k1_jobb(jobb, kontext)
    if jobb.typ in {"lbm", "rapport"}:
        if "fraga_fil" in jobb.nyttolast:
            return _kor_k20_fraga_jobb(jobb, kontext)
        raise ArbetareJobbFel(
            f"Jobb av typ '{jobb.typ}' kräver 'fraga_fil' i nyttolast för standardexekveraren."
        )
    raise ArbetareJobbFel(f"Okänd jobbtyp: {jobb.typ!r}.")


class _LeaseHeartbeat:
    """Bakgrundstråd som förnyar jobbets lease periodiskt och stängs garanterat i `finally`."""

    def __init__(
        self,
        ko: JobbKo,
        jobb_id: str,
        arbetare_id: str,
        lease_s: int,
        intervall_s: float,
    ) -> None:
        self._ko = ko
        self._jobb_id = jobb_id
        self._arbetare_id = arbetare_id
        self._lease_s = lease_s
        self._intervall_s = max(_MIN_HEARTBEAT_S, intervall_s)
        self._stopp = threading.Event()
        self.lease_forlorad = threading.Event()
        self._lock = threading.Lock()
        self._antal_fornyelser = 0
        self._trad: threading.Thread | None = None

    @property
    def antal_fornyelser(self) -> int:
        with self._lock:
            return self._antal_fornyelser

    def starta(self) -> None:
        self._stopp.clear()
        self._trad = threading.Thread(
            target=self._loop,
            name=f"floden-hb-{self._jobb_id}",
            daemon=True,
        )
        self._trad.start()

    def stoppa(self, timeout_s: float = 2.0) -> None:
        self._stopp.set()
        trad = self._trad
        if trad is not None and trad is not threading.current_thread():
            trad.join(timeout=timeout_s)
        self._trad = None

    def _loop(self) -> None:
        while not self._stopp.wait(self._intervall_s):
            try:
                self._ko.fornya_lease(
                    self._jobb_id,
                    self._arbetare_id,
                    lease_sekunder=self._lease_s,
                )
                with self._lock:
                    self._antal_fornyelser += 1
            except LeaseOgiltigFel as exc:
                self.lease_forlorad.set()
                _logg.warning(
                    "Lease förlorad för jobb %s: %s", self._jobb_id, sanera_feltext(exc)
                )
                return
            except Exception as exc:  # noqa: BLE001 - tillfälligt fel får inte döda heartbeat-tråd
                _logg.warning(
                    "Tillfälligt fel vid lease-förnyelse för jobb %s: %s",
                    self._jobb_id,
                    sanera_feltext(exc),
                )


def _normalisera_utfall(raw: JobbKorUtfall | Mapping[str, Any] | None) -> JobbKorUtfall:
    if raw is None:
        return JobbKorUtfall()
    if isinstance(raw, JobbKorUtfall):
        return raw
    if isinstance(raw, Mapping):
        return JobbKorUtfall(faktisk_kostnad_sek=0.0, resultat_meta=dict(raw))
    raise ArbetareJobbFel(
        f"Exekveraren returnerade ogiltig utfallstyp: {type(raw).__name__}."
    )


def _slapp_lease_vid_avbrott(
    ko: JobbKo,
    jobb_id: str,
    arbetare_id: str,
    orsak: str,
) -> None:
    """Släpper en pågående lease rent så jobbet återköas omedelbart i stället för att hänga."""
    try:
        ko.misslyckas(
            jobb_id,
            arbetare_id,
            sanera_feltext(orsak),
            faktisk_kostnad_sek=0.0,
            aterko_om_forsok_kvar=True,
        )
    except LeaseOgiltigFel as exc:
        _logg.warning(
            "Kunde inte släppa lease för jobb %s (redan utgången/avslutad): %s",
            jobb_id,
            sanera_feltext(exc),
        )
    except JobbKoFel as exc:
        _logg.warning(
            "Kunde inte släppa lease för jobb %s: %s",
            jobb_id,
            sanera_feltext(exc),
        )


def kor_arbetare(
    ko: JobbKo,
    *,
    arbetare_id: str | None = None,
    enhet: str = "auto",
    lagring: ObjektLagring | None = None,
    lagring_typ: LagringTyp = "lokal",
    exekverare: JobbExekverare | None = None,
    typer: Sequence[str] | None = None,
    intervall_s: float = STANDARD_POLL_INTERVALL_S,
    lease_s: int = STANDARD_LEASE_SEKUNDER,
    heartbeat_intervall_s: float | None = None,
    en_gang: bool = False,
    max_jobb: int | None = None,
    stopp_event: threading.Event | None = None,
    installera_signaler: bool = True,
    arbetsrot: Path | None = None,
) -> ArbetareResultat:
    """Kör pull-arbetarloopen mot `JobbKo` med heartbeat, fel-isolering och graceful shutdown."""
    if not math.isfinite(intervall_s) or intervall_s <= 0.0:
        raise ArbetareFel(f"intervall_s måste vara ändligt och > 0, fick {intervall_s!r}.")
    if isinstance(lease_s, bool) or not isinstance(lease_s, int) or lease_s <= 0:
        raise ArbetareFel(f"lease_s måste vara ett heltal > 0, fick {lease_s!r}.")
    if heartbeat_intervall_s is not None and (
        not math.isfinite(heartbeat_intervall_s) or heartbeat_intervall_s <= 0.0
    ):
        raise ArbetareFel(
            f"heartbeat_intervall_s måste vara ändligt och > 0, fick {heartbeat_intervall_s!r}."
        )
    if max_jobb is not None and (
        isinstance(max_jobb, bool) or not isinstance(max_jobb, int) or max_jobb < 1
    ):
        raise ArbetareFel(f"max_jobb måste vara ett heltal >= 1, fick {max_jobb!r}.")

    vald_enhet = los_enhet(enhet)
    eff_id = standard_arbetare_id() if arbetare_id is None else arbetare_id
    eff_hb_s = (
        float(heartbeat_intervall_s)
        if heartbeat_intervall_s is not None
        else max(_MIN_HEARTBEAT_S, lease_s / 2.0)
    )
    eff_stopp = stopp_event if stopp_event is not None else threading.Event()
    eff_exekverare: JobbExekverare = (
        exekverare if exekverare is not None else standard_exekverare
    )
    eff_lagring = (
        lagring
        if lagring is not None
        else LokalLagring(
            arbetsrot / "lagring" if arbetsrot is not None else ko.db_sokvag.parent / "lagring"
        )
    )
    eff_max_jobb = 1 if en_gang else max_jobb

    vram_mb = tillgangligt_vram_mb() if vald_enhet == "cuda" else None
    if vram_mb is not None:
        _logg.info(
            "Arbetare %s startad: enhet=%s (fritt VRAM %.0f MB), lagring=%s, lease=%ds",
            eff_id,
            vald_enhet,
            vram_mb,
            lagring_typ,
            lease_s,
        )
    else:
        _logg.info(
            "Arbetare %s startad: enhet=%s, lagring=%s, lease=%ds",
            eff_id,
            vald_enhet,
            lagring_typ,
            lease_s,
        )

    gamla_signaler: dict[signal.Signals, Any] = {}
    i_huvudtrad = threading.current_thread() is threading.main_thread()
    if installera_signaler and i_huvudtrad:

        def _hantera_signal(signum: int, _frame: Any) -> None:
            _logg.info("Arbetare %s tog emot stoppsignal (%d); avslutar rent.", eff_id, signum)
            eff_stopp.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            gamla_signaler[sig] = signal.getsignal(sig)
            signal.signal(sig, _hantera_signal)

    t0 = time.monotonic()
    antal_behandlade = 0
    antal_klara = 0
    antal_misslyckade = 0
    antal_avbrutna = 0
    antal_heartbeats = 0
    avslut: AvslutningsOrsak = "stoppad"

    try:
        while True:
            if eff_stopp.is_set():
                avslut = "stoppad"
                break
            if eff_max_jobb is not None and antal_behandlade >= eff_max_jobb:
                avslut = "en_gang" if en_gang else "max_jobb"
                break

            try:
                ko.atertag_utgangna_leasar()
                jobb = ko.hamta_nasta(eff_id, lease_sekunder=lease_s, typer=typer)
            except JobbKoFel as exc:
                # Valideringsfel (t.ex. ogiltigt arbetare_id / typer) ska avbryta direkt.
                raise ArbetareFel(str(exc)) from exc

            if jobb is None:
                if en_gang:
                    avslut = "tom_ko"
                    break
                if eff_stopp.wait(intervall_s):
                    avslut = "stoppad"
                    break
                continue

            antal_behandlade += 1
            hb = _LeaseHeartbeat(ko, jobb.jobb_id, eff_id, lease_s, eff_hb_s)
            hb.starta()
            bas_tmp = str(arbetsrot) if arbetsrot is not None else None
            if arbetsrot is not None:
                arbetsrot.mkdir(parents=True, exist_ok=True)
            jobb_tmp = Path(tempfile.mkdtemp(prefix=f"floden_jobb_{jobb.jobb_id}_", dir=bas_tmp))

            avbrutet = False
            fel_att_rapportera: str | None = None
            utfall: JobbKorUtfall | None = None
            try:
                kontext = JobbKontext(
                    arbetare_id=eff_id,
                    enhet=vald_enhet,
                    lagring=eff_lagring,
                    lagring_typ=lagring_typ,
                    arbetsrot=jobb_tmp,
                    stopp_event=eff_stopp,
                    lease_forlorad=hb.lease_forlorad,
                )
                raa_utfall = eff_exekverare(jobb, kontext)
                utfall = _normalisera_utfall(raa_utfall)
            except (ArbetareAvbrutenFel, KeyboardInterrupt):
                avbrutet = True
                eff_stopp.set()
            except Exception as exc:  # noqa: BLE001 - giftigt jobb får aldrig krascha arbetaren
                fel_att_rapportera = sanera_feltext(exc)
            finally:
                hb.stoppa()
                antal_heartbeats += hb.antal_fornyelser
                shutil.rmtree(jobb_tmp, ignore_errors=True)

            if avbrutet:
                _slapp_lease_vid_avbrott(
                    ko,
                    jobb.jobb_id,
                    eff_id,
                    "Arbetaren avbröts under kontrollerad nedstängning; leasen släppt.",
                )
                antal_avbrutna += 1
                avslut = "stoppad"
                break

            if fel_att_rapportera is not None:
                _logg.warning(
                    "Jobb %s (%s) misslyckades: %s", jobb.jobb_id, jobb.typ, fel_att_rapportera
                )
                try:
                    ko.misslyckas(
                        jobb.jobb_id,
                        eff_id,
                        fel_att_rapportera,
                        faktisk_kostnad_sek=0.0,
                        aterko_om_forsok_kvar=False,
                    )
                except LeaseOgiltigFel as exc:
                    _logg.warning(
                        "Lease ogiltig vid felmarkering av jobb %s: %s",
                        jobb.jobb_id,
                        sanera_feltext(exc),
                    )
                antal_misslyckade += 1
                continue

            assert utfall is not None
            if hb.lease_forlorad.is_set():
                _logg.warning("Hoppar över slutförande för jobb %s (lease förlorad).", jobb.jobb_id)
                antal_misslyckade += 1
                continue

            try:
                ko.slutfor(
                    jobb.jobb_id,
                    eff_id,
                    utfall.faktisk_kostnad_sek,
                    utfall.resultat_meta,
                )
                antal_klara += 1
                _logg.info("Jobb %s (%s) slutfört av %s.", jobb.jobb_id, jobb.typ, eff_id)
            except PiiForbjudetFel as exc:
                ren = sanera_feltext(exc)
                _logg.warning("Resultatmetadata för jobb %s avvisades (PII): %s", jobb.jobb_id, ren)
                with contextlib.suppress(LeaseOgiltigFel):
                    ko.misslyckas(
                        jobb.jobb_id,
                        eff_id,
                        f"Resultatmetadata avvisades (0 PII): {ren}",
                        faktisk_kostnad_sek=utfall.faktisk_kostnad_sek,
                        aterko_om_forsok_kvar=False,
                    )
                antal_misslyckade += 1
            except LeaseOgiltigFel as exc:
                _logg.warning(
                    "Lease ogiltig vid slutförande av jobb %s: %s",
                    jobb.jobb_id,
                    sanera_feltext(exc),
                )
                antal_misslyckade += 1

            if en_gang:
                avslut = "en_gang"
                break
    finally:
        for sig, gammal in gamla_signaler.items():
            signal.signal(sig, gammal)

    return ArbetareResultat(
        arbetare_id=eff_id,
        enhet=vald_enhet,
        lagring_typ=lagring_typ,
        antal_behandlade=antal_behandlade,
        antal_klara=antal_klara,
        antal_misslyckade=antal_misslyckade,
        antal_avbrutna=antal_avbrutna,
        antal_heartbeats=antal_heartbeats,
        avslutningsorsak=avslut,
        kortid_s=time.monotonic() - t0,
    )


def registrera(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Registrerar kommandogruppen `floden drift` (`arbetare` och `stada-disk`)."""
    drift_parser = subparsers.add_parser(
        "drift",
        help=(
            "Driftverktyg: enhetlig CPU/GPU-arbetare (`arbetare`) och "
            "diskstädning (`stada-disk`)."
        ),
    )
    drift_sub = drift_parser.add_subparsers(dest="drift_kommando")

    arb = drift_sub.add_parser(
        "arbetare",
        help="Starta pull-arbetare mot jobbkön (S5k) på CPU eller CUDA-GPU.",
        description=(
            "Hämtar och kör jobb från Flodens pull-jobbkö (SQLite WAL, S5k) med automatisk "
            "lease-heartbeat, fel-isolering och kontrollerad nedstängning."
        ),
    )
    arb.add_argument(
        "--ko-rot",
        type=Path,
        default=None,
        help=(
            "Katalog för jobbködatabasen (eller direkt sökväg till .sqlite-fil). "
            f"Standard: ${FLODEN_JOBBKO_ROT_ENV} eller {STANDARD_KO_ROT}."
        ),
    )
    arb.add_argument(
        "--lagring",
        choices=("lokal", "s3"),
        default="lokal",
        help="Lagringstyp: 'lokal' (standard, utan S3) eller 's3' (Hetzner Object Storage).",
    )
    arb.add_argument(
        "--lagring-rot",
        type=Path,
        default=None,
        help="Valfri lokal lagringskatalog vid --lagring lokal.",
    )
    arb.add_argument(
        "--konfig",
        type=Path,
        default=Path("config.toml"),
        help="Sökväg till config.toml (standard: config.toml).",
    )
    arb.add_argument(
        "--enhet",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help=(
            "Beräkningsenhet: 'auto' (CUDA om tillgängligt annars CPU, standard), "
            "'cpu' eller 'cuda' (fail-fast om CUDA/CuPy saknas)."
        ),
    )
    arb.add_argument(
        "--intervall-s",
        type=float,
        default=STANDARD_POLL_INTERVALL_S,
        help=f"Pollintervall i sekunder när kön är tom (standard: {STANDARD_POLL_INTERVALL_S}).",
    )
    arb.add_argument(
        "--lease-s",
        type=int,
        default=STANDARD_LEASE_SEKUNDER,
        help=f"Lease-längd i sekunder per jobb (standard: {STANDARD_LEASE_SEKUNDER}).",
    )
    arb.add_argument(
        "--arbetare-id",
        type=str,
        default=None,
        help="Anonymt arbetar-ID (standard: <hostname>-<pid>).",
    )
    arb.add_argument(
        "--typer",
        type=str,
        default=None,
        help=(
            "Valfritt kommaseparerat filter på jobbtyper, t.ex. '2d,batch'. "
            f"Tillåtna: {', '.join(sorted(TILLATNA_JOBBTYPER))}."
        ),
    )
    arb.add_argument(
        "--en-gang",
        action="store_true",
        help="Behandla högst ett jobb och avsluta rent (avslutar direkt om kön är tom).",
    )
    arb.add_argument(
        "--max-jobb",
        type=int,
        default=None,
        help="Valfritt max antal jobb att behandla innan arbetaren avslutar rent.",
    )
    arb.add_argument("--json", action="store_true", help="Skriv ut sammanfattning som JSON.")

    stada = drift_sub.add_parser(
        "stada-disk",
        help="Städa lokal cache (temp, webb/lager, gamla körningar, K22-cache) utan krav på S3.",
    )
    stada.add_argument(
        "--konfig",
        type=Path,
        default=Path("config.toml"),
        help="Sökväg till config.toml (standard: config.toml).",
    )
    stada.add_argument(
        "--cache-rot",
        type=Path,
        default=None,
        help="Cache-rot (standard: [sokvagar].cache eller data/cache).",
    )
    stada.add_argument(
        "--projekt-rot",
        type=Path,
        default=None,
        help="Projekt-/lokalrot för skydd av refererade körningar (standard: data/lokal).",
    )
    stada.add_argument(
        "--demo-rot",
        type=Path,
        default=None,
        help="Demomapp för skydd av demokörningar (standard: [webb].demomapp eller data/demo).",
    )
    stada.add_argument(
        "--max-gb",
        type=float,
        default=None,
        help="Maxstorlek i GB innan LRU-rensning körs (åsidosätter config/miljövariabel).",
    )
    stada.add_argument(
        "--max-alder-dagar",
        type=float,
        default=None,
        help="Maxålder i dagar för cachade poster (åsidosätter config/miljövariabel).",
    )
    stada.add_argument(
        "--grace-minuter",
        type=float,
        default=None,
        help="Skyddstid i minuter för nyligen ändrade filer/mappar (standard: 30).",
    )
    stada.add_argument(
        "--lager-version",
        type=str,
        default=None,
        help="Aktuell webb/lager-version (t.ex. v12; annars högsta vN).",
    )
    stada.add_argument(
        "--torrkor",
        action="store_true",
        help="Visa vad som skulle raderas utan att ta bort några filer.",
    )
    stada.add_argument("--json", action="store_true", help="Skriv ut sammanfattning som JSON.")


def _kor_stada_disk(args: argparse.Namespace) -> int:
    try:
        konfig = _las_valfri_konfig(args.konfig)
        disk_konfig = las_lokal_disk_konfig(
            konfig,
            cache_rot=args.cache_rot,
            projekt_rot=args.projekt_rot,
            demo_rot=args.demo_rot,
            max_gb=args.max_gb,
            max_alder_dagar=args.max_alder_dagar,
            grace_minuter=args.grace_minuter,
            aktuell_lager_version=args.lager_version,
        )
        disk_res = stada_lokal_disk(disk_konfig, torrkor=args.torrkor)
        disk_status = disk_utrymme_status(disk_konfig.cache_rot)
    except (ArbetareFel, KonfigFel, LokalCacheFel, LokalDiskStadareFel) as exc:
        print(f"Fel vid lokal diskstädning: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            **disk_res.som_dict(),
            "disk": {
                "ledig_gb": round(disk_status["ledig_gb"], 3),
                "total_gb": round(disk_status["total_gb"], 3),
                "anvand_pct": round(disk_status["anvand_pct"], 2),
                "cache_gb": round(disk_status["cache_gb"], 6),
            },
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    prefix = "[TORRKÖRNING] Skulle radera" if disk_res.torrkor else "Raderade"
    print(
        f"{prefix} {disk_res.antal_raderade} poster ({disk_res.frigjorda_mb:.2f} MB; "
        f"skyddade: {disk_res.antal_skyddade}). "
        f"Före: {disk_res.fore_mb:.2f} MB, efter: {disk_res.efter_mb:.2f} MB. "
        f"Ledigt på disk: {disk_status['ledig_gb']:.2f} GB "
        f"({disk_status['anvand_pct']:.1f} % använt)."
    )
    for varning in disk_res.varningar:
        print(f"Varning: {varning}", file=sys.stderr)
    return 0


def _kor_arbetare_cli(args: argparse.Namespace) -> int:
    try:
        # Fail-fast enhets- och lagringsvalidering innan kön ens öppnas.
        vald_enhet = los_enhet(args.enhet)
        lagring_typ: LagringTyp = "s3" if args.lagring == "s3" else "lokal"
        lagring = skapa_arbetare_lagring(
            lagring_typ,
            konfig_fil=args.konfig,
            lagring_rot=args.lagring_rot,
        )
        typer: list[str] | None = None
        if args.typer is not None:
            typer = [t.strip() for t in args.typer.split(",") if t.strip()]
            if not typer:
                raise ArbetareFel("--typer får inte vara tom.")
        db_sokvag = los_ko_databas(args.ko_rot)
        ko = JobbKo(db_sokvag)
        res = kor_arbetare(
            ko,
            arbetare_id=args.arbetare_id,
            enhet=vald_enhet,
            lagring=lagring,
            lagring_typ=lagring_typ,
            typer=typer,
            intervall_s=args.intervall_s,
            lease_s=args.lease_s,
            en_gang=bool(args.en_gang),
            max_jobb=args.max_jobb,
        )
    except (ArbetareFel, JobbKoFel, ObjektLagringFel, GpuFel) as exc:
        print(f"Fel i driftarbetare: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(res.till_dict(), indent=2, ensure_ascii=False))
        return 0

    print(
        f"Arbetare {res.arbetare_id} klar ({res.enhet}, lagring={res.lagring_typ}, "
        f"orsak={res.avslutningsorsak}): behandlade={res.antal_behandlade}, "
        f"klara={res.antal_klara}, misslyckade={res.antal_misslyckade}, "
        f"avbrutna={res.antal_avbrutna}, heartbeats={res.antal_heartbeats} "
        f"({res.kortid_s:.2f} s)."
    )
    return 0


def kor(args: argparse.Namespace) -> int:
    """Exekverar `floden drift <underkommando>`."""
    under = getattr(args, "drift_kommando", None)
    if under == "arbetare":
        return _kor_arbetare_cli(args)
    if under == "stada-disk":
        return _kor_stada_disk(args)
    print("Ange underkommando: 'arbetare' eller 'stada-disk'.", file=sys.stderr)
    return 2


__all__ = [
    "ArbetareAvbrutenFel",
    "ArbetareFel",
    "ArbetareJobbFel",
    "ArbetareResultat",
    "AvslutningsOrsak",
    "FLODEN_JOBBKO_ROT_ENV",
    "JobbExekverare",
    "JobbKontext",
    "JobbKorUtfall",
    "STANDARD_KO_FILNAMN",
    "STANDARD_KO_ROT",
    "STANDARD_POLL_INTERVALL_S",
    "kor",
    "kor_arbetare",
    "los_enhet",
    "los_ko_databas",
    "registrera",
    "sanera_feltext",
    "skapa_arbetare_lagring",
    "standard_arbetare_id",
    "standard_exekverare",
]
