"""SQLite för kontosystemet (lokal disk, sökväg i konfigurationen).

Mönstret (konton, sessioner med hashade hemligheter, spärrade konton) är taget från O-Tids
`packages/database/src/schema/accounts.ts` (MIT, joelberring/otid @ 4a446af), avskalat: inga
tävlingar, klubbar, Eventor, kvoter eller lösenord. Organisation > förvaltning > projekt är nytt.
Tidpunkter är heltal (sekunder sedan epoch, UTC).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_V1 = """
CREATE TABLE konto (
  id TEXT PRIMARY KEY,
  epost TEXT,
  epost_verifierad INTEGER NOT NULL DEFAULT 0 CHECK (epost_verifierad IN (0, 1)),
  namn TEXT NOT NULL,
  skapad INTEGER NOT NULL,
  senast_inloggad INTEGER,
  sparrad INTEGER,
  superadmin INTEGER NOT NULL DEFAULT 0 CHECK (superadmin IN (0, 1))
);
CREATE UNIQUE INDEX konto_verifierad_epost ON konto (epost) WHERE epost_verifierad = 1;
CREATE INDEX konto_epost ON konto (epost);

CREATE TABLE identitet (
  id TEXT PRIMARY KEY,
  konto_id TEXT NOT NULL REFERENCES konto (id) ON DELETE CASCADE,
  leverantor TEXT NOT NULL,
  subject TEXT NOT NULL,
  tenant TEXT NOT NULL DEFAULT '',
  skapad INTEGER NOT NULL,
  senast_inloggad INTEGER,
  UNIQUE (leverantor, tenant, subject)
);
CREATE INDEX identitet_konto ON identitet (konto_id);

CREATE TABLE organisation (
  id TEXT PRIMARY KEY,
  namn TEXT NOT NULL UNIQUE,
  skapad INTEGER NOT NULL
);
CREATE TABLE organisation_tenant (
  leverantor TEXT NOT NULL,
  tenant TEXT NOT NULL,
  organisation_id TEXT NOT NULL REFERENCES organisation (id) ON DELETE CASCADE,
  PRIMARY KEY (leverantor, tenant)
);
CREATE TABLE organisation_doman (
  doman TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisation (id) ON DELETE CASCADE
);
CREATE TABLE forvaltning (
  id TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisation (id) ON DELETE CASCADE,
  namn TEXT NOT NULL,
  UNIQUE (organisation_id, namn)
);
CREATE TABLE forvaltning_grupp (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  organisation_id TEXT NOT NULL REFERENCES organisation (id) ON DELETE CASCADE,
  grupp TEXT NOT NULL,
  forvaltning_id TEXT NOT NULL REFERENCES forvaltning (id) ON DELETE CASCADE,
  andrad_av TEXT,
  andrad_tid INTEGER NOT NULL,
  UNIQUE (organisation_id, grupp, forvaltning_id)
);
CREATE TABLE medlemskap (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  konto_id TEXT NOT NULL REFERENCES konto (id) ON DELETE CASCADE,
  organisation_id TEXT NOT NULL REFERENCES organisation (id) ON DELETE CASCADE,
  forvaltning_id TEXT REFERENCES forvaltning (id) ON DELETE CASCADE,
  roll TEXT NOT NULL CHECK (roll IN ('medlem', 'organisationsadmin')),
  kalla TEXT NOT NULL CHECK (kalla IN ('idp-tenant', 'idp-grupp', 'doman', 'manuell')),
  skapad INTEGER NOT NULL,
  bekraftad_av TEXT
);
CREATE UNIQUE INDEX medlemskap_unik
  ON medlemskap (konto_id, organisation_id, COALESCE(forvaltning_id, ''));
CREATE TABLE projekt (
  id TEXT PRIMARY KEY,
  organisation_id TEXT NOT NULL REFERENCES organisation (id) ON DELETE CASCADE,
  forvaltning_id TEXT REFERENCES forvaltning (id) ON DELETE SET NULL,
  namn TEXT NOT NULL,
  skapad INTEGER NOT NULL,
  UNIQUE (organisation_id, namn)
);
CREATE TABLE projektbehorighet (
  konto_id TEXT NOT NULL REFERENCES konto (id) ON DELETE CASCADE,
  projekt_id TEXT NOT NULL REFERENCES projekt (id) ON DELETE CASCADE,
  roll TEXT NOT NULL CHECK (roll IN ('lasare', 'utredare')),
  kalla TEXT NOT NULL CHECK (kalla IN ('inbjudan', 'manuell')),
  beviljad_av TEXT,
  skapad INTEGER NOT NULL,
  PRIMARY KEY (konto_id, projekt_id)
);
CREATE TABLE projektinbjudan (
  id TEXT PRIMARY KEY,
  projekt_id TEXT NOT NULL REFERENCES projekt (id) ON DELETE CASCADE,
  epost TEXT NOT NULL,
  roll TEXT NOT NULL CHECK (roll IN ('lasare', 'utredare')),
  skapad_av TEXT NOT NULL,
  skapad INTEGER NOT NULL,
  giltig_till INTEGER NOT NULL,
  anvand_av TEXT,
  anvand_tid INTEGER
);
CREATE INDEX projektinbjudan_epost ON projektinbjudan (epost);

CREATE TABLE session (
  id TEXT PRIMARY KEY,
  konto_id TEXT NOT NULL REFERENCES konto (id) ON DELETE CASCADE,
  hemlighet_hash TEXT NOT NULL,
  csrf_hash TEXT NOT NULL,
  utfardad INTEGER NOT NULL,
  utgar INTEGER NOT NULL,
  aterkallad INTEGER
);
CREATE INDEX session_konto ON session (konto_id);
CREATE TABLE pagaende_inloggning (
  state_hash TEXT PRIMARY KEY,
  leverantor TEXT NOT NULL,
  nonce TEXT NOT NULL,
  code_verifier TEXT NOT NULL,
  bindning_hash TEXT NOT NULL,
  atervand TEXT NOT NULL,
  skapad INTEGER NOT NULL
);
CREATE TABLE handelse (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tid INTEGER NOT NULL,
  konto_id TEXT,
  handelse TEXT NOT NULL,
  detalj TEXT NOT NULL
);
"""

# Version 2: användarens egna API-nycklar (krypterade, en rad per konto och typ, se nycklar.py).
SCHEMA_V2 = """
CREATE TABLE konto_nyckel (
  konto_id TEXT NOT NULL REFERENCES konto (id) ON DELETE CASCADE,
  typ TEXT NOT NULL,
  krypterad TEXT NOT NULL,
  slutar_pa TEXT NOT NULL,
  sparad INTEGER NOT NULL,
  PRIMARY KEY (konto_id, typ)
);
"""

# Version 3: personliga MCP-token (bara sha256 sparas, se mcptoken.py).
SCHEMA_V3 = """
CREATE TABLE mcp_token (
  id TEXT PRIMARY KEY,
  konto_id TEXT NOT NULL REFERENCES konto (id) ON DELETE CASCADE,
  namn TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  slutar_pa TEXT NOT NULL,
  skapad INTEGER NOT NULL,
  senast_anvand INTEGER,
  aterkallad INTEGER
);
CREATE INDEX mcp_token_konto ON mcp_token (konto_id);
"""

MIGRERINGAR = (SCHEMA_V1, SCHEMA_V2, SCHEMA_V3)


class Databas:
    """Anslutningar till kontodatabasen: en per anrop (trådsäkert för ThreadingHTTPServer)."""

    def __init__(self, sokvag: str | Path) -> None:
        self.sokvag = Path(sokvag)
        self._lock = threading.Lock()
        self.sokvag.parent.mkdir(parents=True, exist_ok=True)
        nyskapad = not self.sokvag.exists()
        self._migrera()
        if nyskapad:
            os.chmod(self.sokvag, 0o600)

    def _anslut(self) -> sqlite3.Connection:
        anslutning = sqlite3.connect(self.sokvag, timeout=10, isolation_level=None)
        anslutning.row_factory = sqlite3.Row
        anslutning.execute("PRAGMA foreign_keys = ON")
        return anslutning

    def _migrera(self) -> None:
        with self._lock:
            c0 = self._anslut()
            try:
                c0.execute("PRAGMA journal_mode = WAL")
            finally:
                c0.close()
            with self.transaktion() as c:
                version = int(c.execute("PRAGMA user_version").fetchone()[0])
                if version > len(MIGRERINGAR):
                    raise RuntimeError("Kontodatabasen är nyare än koden")
                for nr, skript in enumerate(MIGRERINGAR[version:], start=version + 1):
                    for sats in skript.split(";\n"):
                        if sats.strip():
                            c.execute(sats)
                    c.execute(f"PRAGMA user_version = {nr}")

    @contextmanager
    def lasning(self) -> Iterator[sqlite3.Connection]:
        """Läsande anslutning utan skrivlås (parallell under WAL)."""
        c = self._anslut()
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def transaktion(self) -> Iterator[sqlite3.Connection]:
        """`BEGIN IMMEDIATE` ... commit, eller rollback vid fel."""
        c = self._anslut()
        try:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
        finally:
            c.close()
