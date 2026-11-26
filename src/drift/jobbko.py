# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomisk SQLite WAL-backad pull-jobbkö (S5k), organisationskvoter (S10) och kostnadsbok.

Utformad enligt `docs/ARKITEKTUR-DRIFT.md` och `AGENTS.md`:
- Endast anonyma ID:n (`org_id`, `konto_id`, `projekt_id`, `arbetare_id`): inga personuppgifter
  (0 PII) i databas, nyttolastnycklar eller felmeddelanden.
- Atomisk leasing med `BEGIN IMMEDIATE` i SQLite WAL-läge så flera trådar/processer kan hämta
  jobb utan kapplöpning.
- S10 organisationskvoter med säkert förval (`max_aktiva_jobb=0`, `manadsbudget_sek=0.0`) som
  alltid avvisar överskridanden med `KvotOverskridenFel` (aldrig tyst överskridande).
- Exakt kostnadsbok i heltaliga mikro-SEK (`1e-6 SEK`) så månadsackumulering och kvarvarande
  budget aldrig driver av flyttalsavrundning.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

TILLATNA_JOBBTYPER: Final[frozenset[str]] = frozenset({"2d", "lbm", "batch", "rapport"})
TILLATNA_STATUSAR: Final[frozenset[str]] = frozenset(
    {"ko", "pagaende", "klar", "misslyckad", "avbruten"}
)
STANDARD_LEASE_SEKUNDER: Final[int] = 300
STANDARD_MAX_FORSOK: Final[int] = 3
_MIKRO_PER_SEK: Final[int] = 1_000_000

_ANONYMT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_AR_MANAD_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_FORBJUDNA_PII_NYCKLAR: Final[frozenset[str]] = frozenset(
    {
        "epost",
        "e_post",
        "email",
        "namn",
        "fornamn",
        "efternamn",
        "personnummer",
        "telefon",
        "mobil",
        "adress",
        "gatuadress",
    }
)

JobbStatus = Literal["ko", "pagaende", "klar", "misslyckad", "avbruten"]
LarmNiva = Literal["ok", "50%", "80%", "100%"]

_SCHEMA_V1 = """
CREATE TABLE org_kvot (
  org_id TEXT PRIMARY KEY,
  max_aktiva_jobb INTEGER NOT NULL CHECK (max_aktiva_jobb >= 0),
  manadsbudget_mikrosek INTEGER NOT NULL CHECK (manadsbudget_mikrosek >= 0),
  uppdaterad_utc REAL NOT NULL
);

CREATE TABLE jobb (
  jobb_id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  konto_id TEXT NOT NULL,
  projekt_id TEXT NOT NULL,
  typ TEXT NOT NULL CHECK (typ IN ('2d', 'lbm', 'batch', 'rapport')),
  nyttolast_json TEXT NOT NULL,
  prioritet INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('ko', 'pagaende', 'klar', 'misslyckad', 'avbruten')),
  uppskattad_mikrosek INTEGER NOT NULL CHECK (uppskattad_mikrosek >= 0),
  faktisk_mikrosek INTEGER CHECK (faktisk_mikrosek IS NULL OR faktisk_mikrosek >= 0),
  skapad_utc REAL NOT NULL,
  ar_manad TEXT NOT NULL,
  arbetare_id TEXT,
  lease_utgar_utc REAL,
  forsok INTEGER NOT NULL DEFAULT 0 CHECK (forsok >= 0),
  max_forsok INTEGER NOT NULL DEFAULT 3 CHECK (max_forsok >= 1),
  startad_utc REAL,
  avslutad_utc REAL,
  resultat_meta_json TEXT,
  feltext TEXT
);

CREATE INDEX idx_jobb_ko_ordning ON jobb (status, prioritet DESC, skapad_utc ASC);
CREATE INDEX idx_jobb_org_status ON jobb (org_id, status);
CREATE INDEX idx_jobb_org_manad ON jobb (org_id, ar_manad);
CREATE INDEX idx_jobb_lease ON jobb (status, lease_utgar_utc);

CREATE TABLE kostnadspost (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  jobb_id TEXT NOT NULL UNIQUE REFERENCES jobb (jobb_id) ON DELETE CASCADE,
  org_id TEXT NOT NULL,
  ar_manad TEXT NOT NULL,
  typ TEXT NOT NULL,
  belopp_mikrosek INTEGER NOT NULL CHECK (belopp_mikrosek >= 0),
  bokford_utc REAL NOT NULL
);

CREATE INDEX idx_kostnadspost_org_manad ON kostnadspost (org_id, ar_manad);
"""

_MIGRERINGAR: Final[tuple[str, ...]] = (_SCHEMA_V1,)


class JobbKoFel(Exception):
    """Grundfel för pull-jobbkön och kostnadsboken."""


class PiiForbjudetFel(JobbKoFel):
    """Avvisar personuppgifter (t.ex. e-postadress i ID eller PII-nycklar i nyttolast)."""


class KvotOverskridenFel(JobbKoFel):
    """Avvisar jobb som överskrider organisationens aktiva jobb- eller månadskvot (S10)."""

    def __init__(
        self,
        meddelande: str,
        *,
        org_id: str,
        orsak: Literal["max_aktiva_jobb", "manadsbudget_sek"],
        aktuellt: float,
        grans: float,
        begard: float = 0.0,
    ) -> None:
        super().__init__(meddelande)
        self.org_id = org_id
        self.orsak = orsak
        self.aktuellt = aktuellt
        self.grans = grans
        self.begard = begard


class LeaseOgiltigFel(JobbKoFel):
    """Avvisar lease-förnyelse eller avslut för okänt, felägt, utgånget eller avslutat jobb."""


@dataclass(frozen=True)
class OrgKvot:
    """S10-kvot för en organisation (förval 0 = avstängd tills administratör tilldelar kvot)."""

    max_aktiva_jobb: int = 0
    manadsbudget_sek: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.max_aktiva_jobb, bool) or not isinstance(self.max_aktiva_jobb, int):
            raise JobbKoFel("max_aktiva_jobb måste vara ett heltal >= 0.")
        if self.max_aktiva_jobb < 0:
            raise JobbKoFel("max_aktiva_jobb måste vara >= 0.")
        _sek_till_mikrosek(self.manadsbudget_sek, "manadsbudget_sek")


@dataclass(frozen=True)
class JobbPost:
    """En rad i pull-jobbkön (0 PII, endast anonyma ID:n)."""

    jobb_id: str
    org_id: str
    konto_id: str
    projekt_id: str
    typ: str
    nyttolast: dict[str, Any]
    prioritet: int
    status: JobbStatus
    uppskattad_kostnad_sek: float
    faktisk_kostnad_sek: float | None
    skapad_utc: float
    ar_manad: str
    arbetare_id: str | None
    lease_utgar_utc: float | None
    forsok: int
    max_forsok: int
    startad_utc: float | None
    avslutad_utc: float | None
    resultat_meta: dict[str, Any] | None
    feltext: str | None


@dataclass(frozen=True)
class AtertagningsResultat:
    """Resultat från `atertag_utgangna_leasar()`."""

    aterkoade: tuple[str, ...]
    misslyckade: tuple[str, ...]

    @property
    def antal_atertagna(self) -> int:
        return len(self.aterkoade) + len(self.misslyckade)


@dataclass(frozen=True)
class KostnadsbokManad:
    """Månadsvis sammanställning av förbrukad och reserverad kostnad per organisation."""

    org_id: str
    ar_manad: str
    faktisk_kostnad_sek: float
    reserverad_kostnad_sek: float
    total_kostnad_sek: float
    antal_jobb: int
    antal_klara: int
    antal_aktiva: int
    antal_misslyckade: int
    manadsbudget_sek: float
    kvarvarande_budget_sek: float
    utnyttjande_pct: float
    larmniva: LarmNiva

    def till_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "ar_manad": self.ar_manad,
            "faktisk_kostnad_sek": self.faktisk_kostnad_sek,
            "reserverad_kostnad_sek": self.reserverad_kostnad_sek,
            "total_kostnad_sek": self.total_kostnad_sek,
            "antal_jobb": self.antal_jobb,
            "antal_klara": self.antal_klara,
            "antal_aktiva": self.antal_aktiva,
            "antal_misslyckade": self.antal_misslyckade,
            "manadsbudget_sek": self.manadsbudget_sek,
            "kvarvarande_budget_sek": self.kvarvarande_budget_sek,
            "utnyttjande_pct": self.utnyttjande_pct,
            "larmniva": self.larmniva,
        }


def _validera_anonymt_id(varde: str, faltnamn: str) -> str:
    if not isinstance(varde, str) or not varde:
        raise JobbKoFel(f"{faltnamn} måste vara en icke-tom sträng.")
    if "@" in varde:
        raise PiiForbjudetFel(
            f"{faltnamn} får inte innehålla e-postadress eller personuppgifter; ange anonymt ID."
        )
    if not _ANONYMT_ID_RE.match(varde):
        raise JobbKoFel(
            f"{faltnamn} måste vara ett anonymt ID (A-Z, a-z, 0-9, _, ., -, max 128 tecken)."
        )
    return varde


def _validera_ar_manad(ar_manad: str) -> str:
    if not isinstance(ar_manad, str) or not _AR_MANAD_RE.match(ar_manad):
        raise JobbKoFel(f"ar_manad måste ha formatet ÅÅÅÅ-MM, fick {ar_manad!r}.")
    return ar_manad


def _ar_manad_fran_epoch(epoch_utc: float) -> str:
    return datetime.fromtimestamp(epoch_utc, tz=UTC).strftime("%Y-%m")


def _sek_till_mikrosek(belopp_sek: float, faltnamn: str) -> int:
    if isinstance(belopp_sek, bool) or not isinstance(belopp_sek, int | float):
        raise JobbKoFel(f"{faltnamn} måste vara ett tal >= 0.")
    varde = float(belopp_sek)
    if not math.isfinite(varde) or varde < 0.0:
        raise JobbKoFel(f"{faltnamn} måste vara ett ändligt tal >= 0, fick {belopp_sek!r}.")
    return int(round(varde * _MIKRO_PER_SEK))


def _mikrosek_till_sek(mikrosek: int) -> float:
    return round(mikrosek / _MIKRO_PER_SEK, 6)


def _kontrollera_fri_fran_pii(obj: Any, *, sokvag: str = "") -> None:
    """Kontrollerar rekursivt att JSON-strukturer inte innehåller uppenbara PII-fält."""
    if isinstance(obj, Mapping):
        for nyckel, varde in obj.items():
            if not isinstance(nyckel, str):
                raise JobbKoFel("Alla nycklar i JSON-nyttolast/metadata måste vara strängar.")
            norm = nyckel.strip().lower().replace("-", "_")
            if norm in _FORBJUDNA_PII_NYCKLAR:
                plats = f"{sokvag}.{nyckel}" if sokvag else nyckel
                raise PiiForbjudetFel(
                    f"Förbjudet personuppgiftsfält {plats!r} i nyttolast/metadata (0 PII)."
                )
            _kontrollera_fri_fran_pii(varde, sokvag=f"{sokvag}.{nyckel}" if sokvag else nyckel)
    elif isinstance(obj, list | tuple):
        for idx, element in enumerate(obj):
            _kontrollera_fri_fran_pii(element, sokvag=f"{sokvag}[{idx}]")


def _serialisera_json_objekt(obj: Mapping[str, Any], faltnamn: str) -> str:
    if not isinstance(obj, Mapping):
        raise JobbKoFel(f"{faltnamn} måste vara en nyckel-värde-mappning (dict).")
    _kontrollera_fri_fran_pii(obj)
    try:
        return json.dumps(dict(obj), ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise JobbKoFel(f"{faltnamn} kan inte serialiseras som strikt JSON: {exc}") from exc


def _berakna_larmniva(total_mikrosek: int, budget_mikrosek: int) -> tuple[float, LarmNiva]:
    if budget_mikrosek <= 0:
        if total_mikrosek > 0:
            return 100.0, "100%"
        return 0.0, "ok"
    pct = round((100.0 * total_mikrosek) / budget_mikrosek, 2)
    if total_mikrosek >= budget_mikrosek:
        return pct, "100%"
    if total_mikrosek * 100 >= budget_mikrosek * 80:
        return pct, "80%"
    if total_mikrosek * 100 >= budget_mikrosek * 50:
        return pct, "50%"
    return pct, "ok"


def _rad_till_jobbpost(rad: sqlite3.Row) -> JobbPost:
    status_str = str(rad["status"])
    if status_str not in TILLATNA_STATUSAR:
        raise JobbKoFel(f"Okänd jobbstatus i databasen: {status_str!r}.")
    status: JobbStatus = status_str  # type: ignore[assignment]
    faktisk_raw = rad["faktisk_mikrosek"]
    meta_raw = rad["resultat_meta_json"]
    return JobbPost(
        jobb_id=str(rad["jobb_id"]),
        org_id=str(rad["org_id"]),
        konto_id=str(rad["konto_id"]),
        projekt_id=str(rad["projekt_id"]),
        typ=str(rad["typ"]),
        nyttolast=dict(json.loads(str(rad["nyttolast_json"]))),
        prioritet=int(rad["prioritet"]),
        status=status,
        uppskattad_kostnad_sek=_mikrosek_till_sek(int(rad["uppskattad_mikrosek"])),
        faktisk_kostnad_sek=(
            None if faktisk_raw is None else _mikrosek_till_sek(int(faktisk_raw))
        ),
        skapad_utc=float(rad["skapad_utc"]),
        ar_manad=str(rad["ar_manad"]),
        arbetare_id=None if rad["arbetare_id"] is None else str(rad["arbetare_id"]),
        lease_utgar_utc=(
            None if rad["lease_utgar_utc"] is None else float(rad["lease_utgar_utc"])
        ),
        forsok=int(rad["forsok"]),
        max_forsok=int(rad["max_forsok"]),
        startad_utc=None if rad["startad_utc"] is None else float(rad["startad_utc"]),
        avslutad_utc=None if rad["avslutad_utc"] is None else float(rad["avslutad_utc"]),
        resultat_meta=None if meta_raw is None else dict(json.loads(str(meta_raw))),
        feltext=None if rad["feltext"] is None else str(rad["feltext"]),
    )


class JobbKo:
    """SQLite WAL-backad pull-jobbkö (S5k) med organisationskvoter (S10) och kostnadsbok."""

    def __init__(
        self,
        db_sokvag: str | Path,
        *,
        standard_kvot: OrgKvot | None = None,
        klocka: Callable[[], float] | None = None,
    ) -> None:
        self.db_sokvag = Path(db_sokvag)
        self._standard_kvot = standard_kvot if standard_kvot is not None else OrgKvot(0, 0.0)
        self._klocka: Callable[[], float] = klocka if klocka is not None else time.time
        self._init_lock = threading.Lock()
        self.db_sokvag.parent.mkdir(parents=True, exist_ok=True)
        nyskapad = not self.db_sokvag.exists()
        self._migrera()
        if nyskapad:
            os.chmod(self.db_sokvag, 0o600)

    def _anslut(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_sokvag, timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    @contextmanager
    def _lasning(self) -> Iterator[sqlite3.Connection]:
        conn = self._anslut()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaktion(self) -> Iterator[sqlite3.Connection]:
        """Atomisk `BEGIN IMMEDIATE`-transaktion med rollback vid fel."""
        conn = self._anslut()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def _migrera(self) -> None:
        with self._init_lock:
            c0 = self._anslut()
            try:
                c0.execute("PRAGMA journal_mode = WAL")
                c0.execute("PRAGMA synchronous = NORMAL")
            finally:
                c0.close()
            with self._transaktion() as conn:
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if version > len(_MIGRERINGAR):
                    raise JobbKoFel(
                        f"Jobbköns databas (v{version}) är nyare än koden (v{len(_MIGRERINGAR)})."
                    )
                for nr, skript in enumerate(_MIGRERINGAR[version:], start=version + 1):
                    for sats in skript.split(";\n"):
                        if sats.strip():
                            conn.execute(sats)
                    conn.execute(f"PRAGMA user_version = {nr}")

    def _hamta_kvot_i_conn(self, conn: sqlite3.Connection, org_id: str) -> tuple[int, int]:
        rad = conn.execute(
            "SELECT max_aktiva_jobb, manadsbudget_mikrosek FROM org_kvot WHERE org_id = ?",
            (org_id,),
        ).fetchone()
        if rad is None:
            return (
                self._standard_kvot.max_aktiva_jobb,
                _sek_till_mikrosek(self._standard_kvot.manadsbudget_sek, "manadsbudget_sek"),
            )
        return int(rad["max_aktiva_jobb"]), int(rad["manadsbudget_mikrosek"])

    def satt_kvot(
        self,
        org_id: str,
        *,
        max_aktiva_jobb: int,
        manadsbudget_sek: float,
    ) -> OrgKvot:
        """Registrerar eller uppdaterar S10-kvot för en organisation."""
        oid = _validera_anonymt_id(org_id, "org_id")
        kvot = OrgKvot(max_aktiva_jobb=max_aktiva_jobb, manadsbudget_sek=manadsbudget_sek)
        budget_mikro = _sek_till_mikrosek(kvot.manadsbudget_sek, "manadsbudget_sek")
        nu = float(self._klocka())
        with self._transaktion() as conn:
            conn.execute(
                """
                INSERT INTO org_kvot (
                    org_id, max_aktiva_jobb, manadsbudget_mikrosek, uppdaterad_utc
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT (org_id) DO UPDATE SET
                    max_aktiva_jobb = excluded.max_aktiva_jobb,
                    manadsbudget_mikrosek = excluded.manadsbudget_mikrosek,
                    uppdaterad_utc = excluded.uppdaterad_utc
                """,
                (oid, kvot.max_aktiva_jobb, budget_mikro, nu),
            )
        return kvot

    def hamta_kvot(self, org_id: str) -> OrgKvot:
        """Hämtar effektiv S10-kvot för en organisation (eller standardkvoten)."""
        oid = _validera_anonymt_id(org_id, "org_id")
        with self._lasning() as conn:
            max_aktiva, budget_mikro = self._hamta_kvot_i_conn(conn, oid)
        return OrgKvot(
            max_aktiva_jobb=max_aktiva,
            manadsbudget_sek=_mikrosek_till_sek(budget_mikro),
        )

    def lagg_till(
        self,
        org_id: str,
        konto_id: str,
        projekt_id: str,
        typ: str,
        nyttolast: Mapping[str, Any],
        uppskattad_kostnad_sek: float,
        prioritet: int = 0,
        *,
        max_forsok: int = STANDARD_MAX_FORSOK,
    ) -> JobbPost:
        """Lägger till ett jobb i pull-kön efter atomisk S10-kvot- och budgetkontroll."""
        oid = _validera_anonymt_id(org_id, "org_id")
        kid = _validera_anonymt_id(konto_id, "konto_id")
        pid = _validera_anonymt_id(projekt_id, "projekt_id")
        if typ not in TILLATNA_JOBBTYPER:
            tillatna = ", ".join(sorted(TILLATNA_JOBBTYPER))
            raise JobbKoFel(f"Ogiltig jobbtyp {typ!r}; tillåtna är: {tillatna}.")
        if isinstance(prioritet, bool) or not isinstance(prioritet, int):
            raise JobbKoFel("prioritet måste vara ett heltal.")
        if isinstance(max_forsok, bool) or not isinstance(max_forsok, int) or max_forsok < 1:
            raise JobbKoFel("max_forsok måste vara ett heltal >= 1.")

        nyttolast_json = _serialisera_json_objekt(nyttolast, "nyttolast")
        uppskattad_mikro = _sek_till_mikrosek(uppskattad_kostnad_sek, "uppskattad_kostnad_sek")
        nu = float(self._klocka())
        ar_manad = _ar_manad_fran_epoch(nu)
        jobb_id = f"j-{uuid.uuid4().hex[:16]}"

        with self._transaktion() as conn:
            max_aktiva, budget_mikro = self._hamta_kvot_i_conn(conn, oid)

            aktiva = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM jobb
                    WHERE org_id = ? AND status IN ('ko', 'pagaende')
                    """,
                    (oid,),
                ).fetchone()[0]
            )
            if aktiva >= max_aktiva:
                raise KvotOverskridenFel(
                    f"Organisationskvot överskriden för {oid}: "
                    f"{aktiva} aktiva jobb (gräns {max_aktiva}).",
                    org_id=oid,
                    orsak="max_aktiva_jobb",
                    aktuellt=float(aktiva),
                    grans=float(max_aktiva),
                    begard=1.0,
                )

            faktisk_mikro = int(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(belopp_mikrosek), 0) FROM kostnadspost
                    WHERE org_id = ? AND ar_manad = ?
                    """,
                    (oid, ar_manad),
                ).fetchone()[0]
            )
            reserverad_mikro = int(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(uppskattad_mikrosek), 0) FROM jobb
                    WHERE org_id = ? AND ar_manad = ? AND status IN ('ko', 'pagaende')
                    """,
                    (oid, ar_manad),
                ).fetchone()[0]
            )
            ny_total_mikro = faktisk_mikro + reserverad_mikro + uppskattad_mikro
            if ny_total_mikro > budget_mikro:
                aktuell_sek = _mikrosek_till_sek(faktisk_mikro + reserverad_mikro)
                grans_sek = _mikrosek_till_sek(budget_mikro)
                begard_sek = _mikrosek_till_sek(uppskattad_mikro)
                raise KvotOverskridenFel(
                    f"Månadsbudget överskriden för {oid} ({ar_manad}): "
                    f"{aktuell_sek:.2f} + {begard_sek:.2f} > {grans_sek:.2f} SEK.",
                    org_id=oid,
                    orsak="manadsbudget_sek",
                    aktuellt=aktuell_sek,
                    grans=grans_sek,
                    begard=begard_sek,
                )

            conn.execute(
                """
                INSERT INTO jobb (
                    jobb_id, org_id, konto_id, projekt_id, typ, nyttolast_json,
                    prioritet, status, uppskattad_mikrosek, faktisk_mikrosek,
                    skapad_utc, ar_manad, arbetare_id, lease_utgar_utc,
                    forsok, max_forsok, startad_utc, avslutad_utc,
                    resultat_meta_json, feltext
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, 'ko', ?, NULL,
                    ?, ?, NULL, NULL,
                    0, ?, NULL, NULL,
                    NULL, NULL
                )
                """,
                (
                    jobb_id,
                    oid,
                    kid,
                    pid,
                    typ,
                    nyttolast_json,
                    prioritet,
                    uppskattad_mikro,
                    nu,
                    ar_manad,
                    max_forsok,
                ),
            )
            rad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jobb_id,)).fetchone()
            assert rad is not None
            return _rad_till_jobbpost(rad)

    def hamta_nasta(
        self,
        arbetare_id: str,
        lease_sekunder: int = STANDARD_LEASE_SEKUNDER,
        *,
        typer: Sequence[str] | None = None,
    ) -> JobbPost | None:
        """Leasar nästa väntande jobb atomiskt via `BEGIN IMMEDIATE` (högst prioritet, äldst först).

        Returnerar `None` om kön är tom (eller inget jobb matchar `typer`).
        """
        aid = _validera_anonymt_id(arbetare_id, "arbetare_id")
        if isinstance(lease_sekunder, bool) or not isinstance(lease_sekunder, int | float):
            raise JobbKoFel("lease_sekunder måste vara ett positivt tal.")
        lease_s = float(lease_sekunder)
        if not math.isfinite(lease_s) or lease_s <= 0.0:
            raise JobbKoFel("lease_sekunder måste vara > 0.")

        filtrerade_typer: tuple[str, ...] | None = None
        if typer is not None:
            filtrerade_typer = tuple(typer)
            if not filtrerade_typer:
                raise JobbKoFel("typer får inte vara en tom sekvens; använd None för alla typer.")
            for t in filtrerade_typer:
                if t not in TILLATNA_JOBBTYPER:
                    raise JobbKoFel(f"Ogiltig jobbtyp i filter: {t!r}.")

        nu = float(self._klocka())
        lease_utgar = nu + lease_s

        with self._transaktion() as conn:
            if filtrerade_typer is None:
                kandidat = conn.execute(
                    """
                    SELECT jobb_id FROM jobb
                    WHERE status = 'ko'
                    ORDER BY prioritet DESC, skapad_utc ASC, rowid ASC
                    LIMIT 1
                    """
                ).fetchone()
            else:
                platshallare = ", ".join("?" for _ in filtrerade_typer)
                kandidat = conn.execute(
                    f"""
                    SELECT jobb_id FROM jobb
                    WHERE status = 'ko' AND typ IN ({platshallare})
                    ORDER BY prioritet DESC, skapad_utc ASC, rowid ASC
                    LIMIT 1
                    """,
                    filtrerade_typer,
                ).fetchone()
            if kandidat is None:
                return None

            jobb_id = str(kandidat["jobb_id"])
            cur = conn.execute(
                """
                UPDATE jobb
                SET status = 'pagaende',
                    arbetare_id = ?,
                    lease_utgar_utc = ?,
                    forsok = forsok + 1,
                    startad_utc = COALESCE(startad_utc, ?)
                WHERE jobb_id = ? AND status = 'ko'
                """,
                (aid, lease_utgar, nu, jobb_id),
            )
            if cur.rowcount != 1:
                raise JobbKoFel(f"Atomisk leasing misslyckades oväntat för jobb {jobb_id}.")
            rad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jobb_id,)).fetchone()
            assert rad is not None
            return _rad_till_jobbpost(rad)

    def fornya_lease(
        self,
        jobb_id: str,
        arbetare_id: str,
        lease_sekunder: int = STANDARD_LEASE_SEKUNDER,
    ) -> JobbPost:
        """Förlänger leasen (heartbeat) för ett aktivt jobb ägt av `arbetare_id`."""
        jid = _validera_anonymt_id(jobb_id, "jobb_id")
        aid = _validera_anonymt_id(arbetare_id, "arbetare_id")
        if isinstance(lease_sekunder, bool) or not isinstance(lease_sekunder, int | float):
            raise JobbKoFel("lease_sekunder måste vara ett positivt tal.")
        lease_s = float(lease_sekunder)
        if not math.isfinite(lease_s) or lease_s <= 0.0:
            raise JobbKoFel("lease_sekunder måste vara > 0.")

        nu = float(self._klocka())
        ny_utgar = nu + lease_s

        with self._transaktion() as conn:
            rad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jid,)).fetchone()
            if rad is None:
                raise LeaseOgiltigFel(f"Okänt jobb {jid}.")
            if str(rad["status"]) != "pagaende":
                raise LeaseOgiltigFel(
                    f"Jobb {jid} är inte pågående (status={rad['status']!r})."
                )
            if str(rad["arbetare_id"]) != aid:
                raise LeaseOgiltigFel(f"Jobb {jid} ägs inte av arbetare {aid}.")
            if rad["lease_utgar_utc"] is None or float(rad["lease_utgar_utc"]) <= nu:
                raise LeaseOgiltigFel(f"Lease har redan löpt ut för jobb {jid}.")

            conn.execute(
                """
                UPDATE jobb SET lease_utgar_utc = ?
                WHERE jobb_id = ? AND status = 'pagaende' AND arbetare_id = ?
                """,
                (ny_utgar, jid, aid),
            )
            uppdaterad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jid,)).fetchone()
            assert uppdaterad is not None
            return _rad_till_jobbpost(uppdaterad)

    def atertag_utgangna_leasar(self) -> AtertagningsResultat:
        """Återtar jobb med utgången lease (kraschåterhämtning med retry-tak `max_forsok`).

        Jobb med `forsok < max_forsok` återköas (`status = 'ko'`); jobb som nått `max_forsok`
        markeras som `misslyckad` så de inte hamnar i en oändlig omstartsloop.
        """
        nu = float(self._klocka())
        aterkoade: list[str] = []
        misslyckade: list[str] = []

        with self._transaktion() as conn:
            rader = conn.execute(
                """
                SELECT jobb_id, forsok, max_forsok FROM jobb
                WHERE status = 'pagaende'
                  AND lease_utgar_utc IS NOT NULL
                  AND lease_utgar_utc <= ?
                ORDER BY lease_utgar_utc ASC, rowid ASC
                """,
                (nu,),
            ).fetchall()

            for rad in rader:
                jid = str(rad["jobb_id"])
                forsok = int(rad["forsok"])
                max_forsok = int(rad["max_forsok"])
                if forsok >= max_forsok:
                    fel = f"Lease löpte ut efter {forsok}/{max_forsok} försök (retry-tak nått)."
                    conn.execute(
                        """
                        UPDATE jobb
                        SET status = 'misslyckad',
                            arbetare_id = NULL,
                            lease_utgar_utc = NULL,
                            avslutad_utc = ?,
                            faktisk_mikrosek = COALESCE(faktisk_mikrosek, 0),
                            feltext = ?
                        WHERE jobb_id = ? AND status = 'pagaende'
                        """,
                        (nu, fel, jid),
                    )
                    misslyckade.append(jid)
                else:
                    notis = f"Lease löpte ut (försök {forsok}/{max_forsok}); återköad."
                    conn.execute(
                        """
                        UPDATE jobb
                        SET status = 'ko',
                            arbetare_id = NULL,
                            lease_utgar_utc = NULL,
                            feltext = ?
                        WHERE jobb_id = ? AND status = 'pagaende'
                        """,
                        (notis, jid),
                    )
                    aterkoade.append(jid)

        return AtertagningsResultat(
            aterkoade=tuple(aterkoade),
            misslyckade=tuple(misslyckade),
        )

    def slutfor(
        self,
        jobb_id: str,
        arbetare_id: str,
        faktisk_kostnad_sek: float,
        resultat_meta: Mapping[str, Any] | None = None,
    ) -> JobbPost:
        """Markerar ett pågående jobb som slutfört och bokför faktisk kostnad i kostnadsboken."""
        jid = _validera_anonymt_id(jobb_id, "jobb_id")
        aid = _validera_anonymt_id(arbetare_id, "arbetare_id")
        faktisk_mikro = _sek_till_mikrosek(faktisk_kostnad_sek, "faktisk_kostnad_sek")
        meta_json = (
            "{}"
            if resultat_meta is None
            else _serialisera_json_objekt(resultat_meta, "resultat_meta")
        )
        nu = float(self._klocka())

        with self._transaktion() as conn:
            rad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jid,)).fetchone()
            if rad is None:
                raise LeaseOgiltigFel(f"Okänt jobb {jid}.")
            if str(rad["status"]) != "pagaende":
                raise LeaseOgiltigFel(
                    f"Kan inte slutföra jobb {jid} med status {rad['status']!r}."
                )
            if str(rad["arbetare_id"]) != aid:
                raise LeaseOgiltigFel(f"Jobb {jid} ägs inte av arbetare {aid}.")
            if rad["lease_utgar_utc"] is None or float(rad["lease_utgar_utc"]) <= nu:
                raise LeaseOgiltigFel(f"Lease har löpt ut för jobb {jid}; slutförande nekas.")

            conn.execute(
                """
                UPDATE jobb
                SET status = 'klar',
                    faktisk_mikrosek = ?,
                    resultat_meta_json = ?,
                    avslutad_utc = ?,
                    lease_utgar_utc = NULL,
                    feltext = NULL
                WHERE jobb_id = ? AND status = 'pagaende' AND arbetare_id = ?
                """,
                (faktisk_mikro, meta_json, nu, jid, aid),
            )
            conn.execute(
                """
                INSERT INTO kostnadspost (
                    jobb_id, org_id, ar_manad, typ, belopp_mikrosek, bokford_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (jid, str(rad["org_id"]), str(rad["ar_manad"]), str(rad["typ"]), faktisk_mikro, nu),
            )
            uppdaterad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jid,)).fetchone()
            assert uppdaterad is not None
            return _rad_till_jobbpost(uppdaterad)

    def slutför(
        self,
        jobb_id: str,
        arbetare_id: str,
        faktisk_kostnad_sek: float,
        resultat_meta: Mapping[str, Any] | None = None,
    ) -> JobbPost:
        """Alias med svenskt tecken för `slutfor()`."""
        return self.slutfor(
            jobb_id,
            arbetare_id,
            faktisk_kostnad_sek,
            resultat_meta,
        )

    def misslyckas(
        self,
        jobb_id: str,
        arbetare_id: str,
        feltext: str,
        *,
        faktisk_kostnad_sek: float = 0.0,
        aterko_om_forsok_kvar: bool = False,
    ) -> JobbPost:
        """Rapporterar att ett jobb misslyckats (och bokför eventuell förbrukad kostnad)."""
        jid = _validera_anonymt_id(jobb_id, "jobb_id")
        aid = _validera_anonymt_id(arbetare_id, "arbetare_id")
        if not isinstance(feltext, str) or not feltext.strip():
            raise JobbKoFel("feltext måste vara en icke-tom felbeskrivning.")
        ren_feltext = feltext.strip()[:2000]
        faktisk_mikro = _sek_till_mikrosek(faktisk_kostnad_sek, "faktisk_kostnad_sek")
        nu = float(self._klocka())

        with self._transaktion() as conn:
            rad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jid,)).fetchone()
            if rad is None:
                raise LeaseOgiltigFel(f"Okänt jobb {jid}.")
            if str(rad["status"]) != "pagaende":
                raise LeaseOgiltigFel(
                    f"Kan inte felmarkera jobb {jid} med status {rad['status']!r}."
                )
            if str(rad["arbetare_id"]) != aid:
                raise LeaseOgiltigFel(f"Jobb {jid} ägs inte av arbetare {aid}.")
            if rad["lease_utgar_utc"] is None or float(rad["lease_utgar_utc"]) <= nu:
                raise LeaseOgiltigFel(f"Lease har löpt ut för jobb {jid}.")

            forsok = int(rad["forsok"])
            max_forsok = int(rad["max_forsok"])
            if aterko_om_forsok_kvar and forsok < max_forsok:
                conn.execute(
                    """
                    UPDATE jobb
                    SET status = 'ko',
                        arbetare_id = NULL,
                        lease_utgar_utc = NULL,
                        feltext = ?
                    WHERE jobb_id = ? AND status = 'pagaende' AND arbetare_id = ?
                    """,
                    (ren_feltext, jid, aid),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobb
                    SET status = 'misslyckad',
                        faktisk_mikrosek = ?,
                        avslutad_utc = ?,
                        lease_utgar_utc = NULL,
                        feltext = ?
                    WHERE jobb_id = ? AND status = 'pagaende' AND arbetare_id = ?
                    """,
                    (faktisk_mikro, nu, ren_feltext, jid, aid),
                )
                if faktisk_mikro > 0:
                    conn.execute(
                        """
                        INSERT INTO kostnadspost (
                            jobb_id, org_id, ar_manad, typ, belopp_mikrosek, bokford_utc
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            jid,
                            str(rad["org_id"]),
                            str(rad["ar_manad"]),
                            str(rad["typ"]),
                            faktisk_mikro,
                            nu,
                        ),
                    )
            uppdaterad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jid,)).fetchone()
            assert uppdaterad is not None
            return _rad_till_jobbpost(uppdaterad)

    def hamta_jobb(self, jobb_id: str) -> JobbPost | None:
        """Läser ett enskilt jobb efter ID, eller `None` om det inte finns."""
        jid = _validera_anonymt_id(jobb_id, "jobb_id")
        with self._lasning() as conn:
            rad = conn.execute("SELECT * FROM jobb WHERE jobb_id = ?", (jid,)).fetchone()
            return None if rad is None else _rad_till_jobbpost(rad)

    def lista_jobb(
        self,
        *,
        org_id: str | None = None,
        status: JobbStatus | None = None,
        projekt_id: str | None = None,
        begransning: int = 100,
    ) -> list[JobbPost]:
        """Listar jobb filtrerade på organisation, status och/eller projekt."""
        if isinstance(begransning, bool) or not isinstance(begransning, int) or begransning < 1:
            raise JobbKoFel("begransning måste vara ett heltal >= 1.")
        villkor: list[str] = []
        params: list[Any] = []
        if org_id is not None:
            villkor.append("org_id = ?")
            params.append(_validera_anonymt_id(org_id, "org_id"))
        if status is not None:
            if status not in TILLATNA_STATUSAR:
                raise JobbKoFel(f"Ogiltig status {status!r}.")
            villkor.append("status = ?")
            params.append(status)
        if projekt_id is not None:
            villkor.append("projekt_id = ?")
            params.append(_validera_anonymt_id(projekt_id, "projekt_id"))
        where = f"WHERE {' AND '.join(villkor)}" if villkor else ""
        params.append(begransning)
        with self._lasning() as conn:
            rader = conn.execute(
                f"""
                SELECT * FROM jobb
                {where}
                ORDER BY prioritet DESC, skapad_utc ASC, rowid ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_rad_till_jobbpost(r) for r in rader]

    def kostnadsbok_manad(self, org_id: str, ar_manad: str) -> KostnadsbokManad:
        """Returnerar exakt ackumulerad kostnad, antal jobb och kvarvarande budget för månaden."""
        oid = _validera_anonymt_id(org_id, "org_id")
        manad = _validera_ar_manad(ar_manad)
        with self._lasning() as conn:
            _, budget_mikro = self._hamta_kvot_i_conn(conn, oid)
            faktisk_mikro = int(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(belopp_mikrosek), 0) FROM kostnadspost
                    WHERE org_id = ? AND ar_manad = ?
                    """,
                    (oid, manad),
                ).fetchone()[0]
            )
            reserverad_mikro = int(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(uppskattad_mikrosek), 0) FROM jobb
                    WHERE org_id = ? AND ar_manad = ? AND status IN ('ko', 'pagaende')
                    """,
                    (oid, manad),
                ).fetchone()[0]
            )
            rad = conn.execute(
                """
                SELECT
                    COUNT(*) AS totalt,
                    COALESCE(SUM(CASE WHEN status = 'klar' THEN 1 ELSE 0 END), 0) AS klara,
                    COALESCE(
                        SUM(CASE WHEN status IN ('ko', 'pagaende') THEN 1 ELSE 0 END), 0
                    ) AS aktiva,
                    COALESCE(SUM(CASE WHEN status = 'misslyckad' THEN 1 ELSE 0 END), 0) AS fel
                FROM jobb
                WHERE org_id = ? AND ar_manad = ?
                """,
                (oid, manad),
            ).fetchone()
            assert rad is not None
            total_mikro = faktisk_mikro + reserverad_mikro
            kvar_mikro = budget_mikro - total_mikro
            pct, larm = _berakna_larmniva(total_mikro, budget_mikro)
            return KostnadsbokManad(
                org_id=oid,
                ar_manad=manad,
                faktisk_kostnad_sek=_mikrosek_till_sek(faktisk_mikro),
                reserverad_kostnad_sek=_mikrosek_till_sek(reserverad_mikro),
                total_kostnad_sek=_mikrosek_till_sek(total_mikro),
                antal_jobb=int(rad["totalt"]),
                antal_klara=int(rad["klara"]),
                antal_aktiva=int(rad["aktiva"]),
                antal_misslyckade=int(rad["fel"]),
                manadsbudget_sek=_mikrosek_till_sek(budget_mikro),
                kvarvarande_budget_sek=_mikrosek_till_sek(kvar_mikro),
                utnyttjande_pct=pct,
                larmniva=larm,
            )
