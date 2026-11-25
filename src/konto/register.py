"""Konton, organisationer, förvaltningar, medlemskap, projekt och inbjudningar.

Modellen och dess regler (docs/ARKITEKTUR-DRIFT.md 3a-3c, BESLUT 2026-11-21 "Drift"):

- IdP-tenant ger organisation. IdP-grupp ger förvaltning. E-postdomän ger bara organisation och
  bara när leverantören intygar domänen (Googles `hd`).
- Projektbehörighet ges aldrig automatiskt av en IdP-signal: bara genom en inbjudan som tas emot med
  en verifierad e-postadress, eller genom en uttrycklig tilldelning av en admin eller superadmin.
  Medlemskap i förvaltning, enkelt organisationsmedlemskap (`medlem`) och rollen superadmin ger
  ingen läsrätt till data.
- Organisationsroller (medlem < ledare < organisationsadmin < ägare): ledare läser (`lasare`),
  organisationsadmin och ägare utreder (`utredare`) i alla organisationens projekt. Regeln finns på
  ett ställe, `Register.effektiv_projektroll`, och används av alla vägar (webb, API, MCP).
  Superadmin ger fortfarande ingen projektdata.
- Loggar och `handelse` innehåller konto-id och regel-id, aldrig e-post eller namn.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from konto.databas import Databas

logg = logging.getLogger("konto")

PROJEKTROLLER = ("lasare", "utredare", "agare")  # stigande behörighet; agare = projektets ägare
ORGROLLER = ("medlem", "ledare", "organisationsadmin", "agare")  # stigande behörighet
ORGROLL_TILL_PROJEKTROLL = {
    "ledare": "lasare",
    "organisationsadmin": "utredare",
    "agare": "utredare",
}
"""Rätten en organisationsroll ger i alla organisationens projekt. `medlem` ger ingen."""
_NIVA_ORGADMIN = ORGROLLER.index("organisationsadmin")
_NIVA_AGARE = ORGROLLER.index("agare")
_NIVA_SUPERADMIN = len(ORGROLLER)  # över ägare: bara för utförarens rättighetsnivå
_ADMINROLLER_SQL = "('organisationsadmin', 'agare')"
INBJUDAN_GILTIG_DAGAR = 14
_EPOST = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BehorighetFel(Exception):
    """Åtgärden kräver en roll som utföraren inte har."""


class RegisterFel(Exception):
    """Ogiltig indata till registret (okänt id, fel roll, ogiltig adress)."""


def normalisera_epost(epost: str) -> str:
    """Gemener och trimmad; vägrar det som inte liknar en adress."""
    ren = epost.strip().lower()
    if len(ren) > 254 or not _EPOST.match(ren):
        raise RegisterFel("Ogiltig e-postadress")
    return ren


def _roll_niva(roll: str) -> int:
    if roll not in PROJEKTROLLER:
        raise RegisterFel(f"Okänd projektroll {roll!r}")
    return PROJEKTROLLER.index(roll)


def _orgroll_niva(roll: str) -> int:
    if roll not in ORGROLLER:
        raise RegisterFel(f"Okänd organisationsroll {roll!r}")
    return ORGROLLER.index(roll)


def projektroll_fran_organisationsroll(orgroll: str | None) -> str | None:
    """Projektrollen en organisationsroll ger i organisationens projekt (None för `medlem`)."""
    return ORGROLL_TILL_PROJEKTROLL.get(orgroll or "")


@dataclass(frozen=True)
class EffektivRoll:
    """Kontots verkliga rätt i ett projekt. `kalla` är `projekt` (uttrycklig roll, eller lika hög
    som organisationens) eller `organisation` (den högre rätten kommer av organisationsrollen)."""

    roll: str | None
    kalla: str = ""

    def raeckar(self, minst: str) -> bool:
        return self.roll is not None and _roll_niva(self.roll) >= _roll_niva(minst)


@dataclass(frozen=True)
class Konto:
    id: str
    epost: str | None
    epost_verifierad: bool
    namn: str
    superadmin: bool
    sparrad: bool


def _ny_id() -> str:
    return uuid.uuid4().hex


class Register:
    """Alla läs- och skrivfunktioner mot kontodatabasen."""

    def __init__(self, db: Databas, klocka: Callable[[], float] = time.time) -> None:
        self.db = db
        self._klocka = klocka

    def nu(self) -> int:
        return int(self._klocka())

    def kontostatus(self, konto_id: str) -> str:
        """En rad för serveradministratören: superadmin, spärr, organisationsroller (bara id:n)."""
        with self.db.lasning() as c:
            rad = c.execute(
                "SELECT superadmin, sparrad FROM konto WHERE id = ?", (konto_id,)
            ).fetchone()
            if not rad:
                raise RegisterFel("Okänt konto")
            roller = c.execute(
                "SELECT organisation_id, roll FROM medlemskap WHERE konto_id = ?"
                " AND forvaltning_id IS NULL ORDER BY organisation_id",
                (konto_id,),
            ).fetchall()
        orgar = ",".join(f"{r['organisation_id']}:{r['roll']}" for r in roller) or "-"
        return (
            f"superadmin={'ja' if rad['superadmin'] else 'nej'}"
            f" sparrad={'nej' if rad['sparrad'] is None else 'ja'} organisationer={orgar}"
        )

    # --- logg -----------------------------------------------------------------------------

    def notera(
        self, c: sqlite3.Connection, handelse: str, konto_id: str | None, detalj: str = ""
    ) -> None:
        """Skriver en händelse. `detalj` får bara innehålla id:n och regel-nummer."""
        c.execute(
            "INSERT INTO handelse (tid, konto_id, handelse, detalj) VALUES (?, ?, ?, ?)",
            (self.nu(), konto_id, handelse, detalj),
        )
        logg.info("handelse=%s konto=%s %s", handelse, konto_id or "-", detalj)

    # --- uppslag --------------------------------------------------------------------------

    @staticmethod
    def _konto_fran_rad(rad: sqlite3.Row) -> Konto:
        return Konto(
            id=rad["id"],
            epost=rad["epost"],
            epost_verifierad=bool(rad["epost_verifierad"]),
            namn=rad["namn"],
            superadmin=bool(rad["superadmin"]),
            sparrad=rad["sparrad"] is not None,
        )

    def konto(self, konto_id: str) -> Konto | None:
        with self.db.transaktion() as c:
            rad = c.execute("SELECT * FROM konto WHERE id = ?", (konto_id,)).fetchone()
        return self._konto_fran_rad(rad) if rad else None

    def konto_via_epost(self, epost: str) -> Konto | None:
        """Första kontot med adressen (administration/CLI; aldrig för inloggning)."""
        with self.db.transaktion() as c:
            rad = c.execute(
                "SELECT * FROM konto WHERE epost = ? ORDER BY skapad LIMIT 1",
                (normalisera_epost(epost),),
            ).fetchone()
        return self._konto_fran_rad(rad) if rad else None

    # --- organisation, förvaltning, regler (systemnivå: CLI/superadmin) ---------------------

    def skapa_organisation(self, namn: str, agare: str | None = None) -> str:
        """Skapar organisationen. Med `agare` (ett konto-id) blir kontot organisationens ägare."""
        if not namn.strip():
            raise RegisterFel("Organisationen måste ha ett namn")
        org_id = _ny_id()
        with self.db.transaktion() as c:
            if (
                agare is not None
                and not c.execute("SELECT 1 FROM konto WHERE id = ?", (agare,)).fetchone()
            ):
                raise RegisterFel("Okänt konto")
            c.execute(
                "INSERT INTO organisation (id, namn, skapad) VALUES (?, ?, ?)",
                (org_id, namn.strip(), self.nu()),
            )
            self.notera(c, "organisation-skapad", None, f"organisation={org_id}")
            if agare is not None:
                c.execute(
                    "INSERT INTO medlemskap (konto_id, organisation_id, forvaltning_id, roll,"
                    " kalla, skapad, bekraftad_av) VALUES (?, ?, NULL, 'agare', 'manuell', ?,"
                    " 'serverkommando')",
                    (agare, org_id, self.nu()),
                )
                self.notera(c, "orgagare-beviljad", agare, f"organisation={org_id}")
        return org_id

    def registrera_tenant(self, organisation_id: str, leverantor: str, tenant: str) -> None:
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO organisation_tenant (leverantor, tenant, organisation_id)"
                " VALUES (?, ?, ?)",
                (leverantor, tenant.strip().lower(), organisation_id),
            )
            self.notera(c, "tenant-registrerad", None, f"organisation={organisation_id}")

    def registrera_doman(self, organisation_id: str, doman: str) -> None:
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO organisation_doman (doman, organisation_id) VALUES (?, ?)",
                (doman.strip().lower(), organisation_id),
            )
            self.notera(c, "doman-registrerad", None, f"organisation={organisation_id}")

    def skapa_forvaltning(self, organisation_id: str, namn: str) -> str:
        forv_id = _ny_id()
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO forvaltning (id, organisation_id, namn) VALUES (?, ?, ?)",
                (forv_id, organisation_id, namn.strip()),
            )
        return forv_id

    def mappa_grupp(
        self, organisation_id: str, forvaltning_id: str, grupp: str, andrad_av: str | None = None
    ) -> int:
        """Regel: medlemmar i IdP-gruppen `grupp` tillhör förvaltningen. Returnerar regel-id."""
        with self.db.transaktion() as c:
            forv = c.execute(
                "SELECT 1 FROM forvaltning WHERE id = ? AND organisation_id = ?",
                (forvaltning_id, organisation_id),
            ).fetchone()
            if not forv:
                raise RegisterFel("Förvaltningen hör inte till organisationen")
            c.execute(
                "INSERT INTO forvaltning_grupp"
                " (organisation_id, grupp, forvaltning_id, andrad_av, andrad_tid)"
                " VALUES (?, ?, ?, ?, ?)",
                (organisation_id, grupp, forvaltning_id, andrad_av, self.nu()),
            )
            regel = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
            self.notera(c, "grupp-mappad", andrad_av, f"regel={regel}")
        return regel

    def ge_superadmin(self, konto_id: str) -> None:
        with self.db.transaktion() as c:
            if c.execute("UPDATE konto SET superadmin = 1 WHERE id = ?", (konto_id,)).rowcount != 1:
                raise RegisterFel("Okänt konto")
            self.notera(c, "superadmin-beviljad", konto_id)

    def forbered_konto(self, epost: str, namn: str | None = None) -> str:
        """Skapar ett konto utan identitet (t.ex. för den första superadmin). Det kopplas till
        leverantörens identitet första gången samma adress loggar in med en verifierad adress."""
        ren = normalisera_epost(epost)
        konto_id = _ny_id()
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO konto (id, epost, epost_verifierad, namn, skapad)"
                " VALUES (?, ?, 0, ?, ?)",
                (konto_id, ren, namn or ren.split("@")[0], self.nu()),
            )
            self.notera(c, "konto-forberett", konto_id)
        return konto_id

    def gor_organisationsadmin(self, konto_id: str, organisation_id: str) -> None:
        """Manuellt organisationsmedlemskap som organisationsadmin (sänker aldrig en ägare)."""
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO medlemskap (konto_id, organisation_id, forvaltning_id, roll, kalla,"
                " skapad) VALUES (?, ?, NULL, 'organisationsadmin', 'manuell', ?)"
                " ON CONFLICT (konto_id, organisation_id, COALESCE(forvaltning_id, ''))"
                " DO UPDATE SET kalla = 'manuell', roll = CASE WHEN medlemskap.roll = 'agare'"
                " THEN 'agare' ELSE 'organisationsadmin' END",
                (konto_id, organisation_id, self.nu()),
            )
            self.notera(c, "orgadmin-beviljad", konto_id, f"organisation={organisation_id}")

    def lagg_till_medlem(
        self, konto_id: str, organisation_id: str, forvaltning_id: str | None, bekraftad_av: str
    ) -> None:
        """Manuellt medlemskap (ändras aldrig av inloggningens omprövning)."""
        with self.db.transaktion() as c:
            c.execute(
                "INSERT OR IGNORE INTO medlemskap (konto_id, organisation_id, forvaltning_id, roll,"
                " kalla, skapad, bekraftad_av) VALUES (?, ?, ?, 'medlem', 'manuell', ?, ?)",
                (konto_id, organisation_id, forvaltning_id, self.nu(), bekraftad_av),
            )
            self.notera(c, "medlemskap-manuellt", konto_id, f"organisation={organisation_id}")

    def skapa_projekt(
        self,
        organisation_id: str,
        namn: str,
        forvaltning_id: str | None = None,
        organisation_synlig: bool = False,
    ) -> str:
        """Nytt projekt. Förval: privat (`organisation_synlig` falskt)."""
        projekt_id = _ny_id()
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO projekt (id, organisation_id, forvaltning_id, namn, skapad,"
                " organisation_synlig) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    projekt_id,
                    organisation_id,
                    forvaltning_id,
                    namn.strip(),
                    self.nu(),
                    int(organisation_synlig),
                ),
            )
            self.notera(c, "projekt-skapat", None, f"projekt={projekt_id}")
        return projekt_id

    def projekt_finns(self, projekt_id: str) -> bool:
        """Finns projektet i registret? (Utan att säga något om vem som får se det.)"""
        with self.db.transaktion() as c:
            rad = c.execute("SELECT 1 FROM projekt WHERE id = ?", (projekt_id,)).fetchone()
        return rad is not None

    def organisationer_for_konto(self, konto_id: str) -> list[str]:
        """Id:n på organisationerna kontot är medlem i (sorterade)."""
        with self.db.transaktion() as c:
            rader = c.execute(
                "SELECT DISTINCT organisation_id FROM medlemskap WHERE konto_id = ?"
                " ORDER BY organisation_id",
                (konto_id,),
            ).fetchall()
        return [str(r["organisation_id"]) for r in rader]

    def registrera_projekt_for_konto(
        self, konto_id: str, organisation_id: str, namn: str, forvaltning_id: str | None = None
    ) -> tuple[str, bool]:
        """Serverkommandot (`floden projekt skapa`): ett projekt i organisationen med
        kontot som utredare. Returnerar `(projekt_id, nytt)`.

        Samma namn i samma organisation ger det befintliga projektet (kommandot kan köras om).
        Kontots roll höjs till `utredare` men sänks aldrig. Det här är ett kommando på servern, inte
        en åtgärd i webben, och därför prövas ingen utförare: kontot ska finnas och inte vara
        spärrat, och organisationen ska finnas. Händelserna har bara id:n."""
        with self.db.transaktion() as c:
            konto = c.execute("SELECT sparrad FROM konto WHERE id = ?", (konto_id,)).fetchone()
            if not konto or konto["sparrad"] is not None:
                raise RegisterFel("Okänt eller spärrat konto")
            if not c.execute(
                "SELECT 1 FROM organisation WHERE id = ?", (organisation_id,)
            ).fetchone():
                raise RegisterFel("Okänd organisation")
            rad = c.execute(
                "SELECT id FROM projekt WHERE organisation_id = ? AND namn = ?",
                (organisation_id, namn.strip()),
            ).fetchone()
            nytt = rad is None
            if rad is None:
                projekt_id = _ny_id()
                c.execute(
                    "INSERT INTO projekt (id, organisation_id, forvaltning_id, namn, skapad,"
                    " skapad_av) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        projekt_id,
                        organisation_id,
                        forvaltning_id,
                        namn.strip(),
                        self.nu(),
                        konto_id,
                    ),
                )
                self.notera(c, "projekt-skapat", None, f"projekt={projekt_id} via=serverkommando")
            else:
                projekt_id = str(rad["id"])
            c.execute(
                "INSERT INTO projektbehorighet (konto_id, projekt_id, roll, kalla, beviljad_av,"
                " skapad) VALUES (?, ?, 'utredare', 'manuell', 'serverkommando', ?)"
                " ON CONFLICT (konto_id, projekt_id) DO UPDATE SET roll = 'utredare',"
                " kalla = 'manuell', beviljad_av = 'serverkommando'",
                (konto_id, projekt_id, self.nu()),
            )
            self.notera(c, "projektroll-beviljad", konto_id, f"projekt={projekt_id} roll=utredare")
        return projekt_id, nytt

    def organisationer_for_skapa(self, konto_id: str) -> list[dict[str, str]]:
        """Organisationerna kontot får skapa projekt i, med namn (sorterade på namn): alla för
        superadmin, annars de där kontot är organisationsadmin eller ägare. Tom lista för ett
        spärrat eller okänt konto och för enkla medlemmar och ledare. Samma regel som
        `skapa_projekt_av` (webbens `POST /api/projekt` och MCP-verktyget `projekt_skapa`)."""
        with self.db.lasning() as c:
            orgar = c.execute("SELECT id, namn FROM organisation ORDER BY namn, id").fetchall()
            return [
                {"id": str(o["id"]), "namn": str(o["namn"])}
                for o in orgar
                if self._utforarens_niva(c, konto_id, o["id"]) >= _NIVA_ORGADMIN
            ]

    def projektnamn_finns(self, organisation_id: str, namn: str) -> bool:
        """Finns ett projekt med det namnet i organisationen? (Tidigt besked innan något byggs.)"""
        with self.db.lasning() as c:
            return (
                c.execute(
                    "SELECT 1 FROM projekt WHERE organisation_id = ? AND namn = ?",
                    (organisation_id, namn.strip()),
                ).fetchone()
                is not None
            )

    def skapa_projekt_av(
        self,
        utforare: str,
        organisation_id: str,
        namn: str,
        *,
        organisation_synlig: bool = False,
    ) -> str:
        """Nytt projekt i organisationen, skapat av ett konto i webben eller via MCP (inte av ett
        serverkommando: se `registrera_projekt_for_konto`).

        Kräver superadmin, eller organisationsadmin eller ägare i organisationen
        (`BehorighetFel` annars). Skaparen blir `utredare` och `skapad_av`; projektet är privat
        om inte `organisation_synlig` anges. Ett namn som redan finns i organisationen ger
        `RegisterFel`: ett befintligt projekt återanvänds aldrig, så att ingen kan ge sig själv en
        roll i ett privat projekt genom att skapa ett med samma namn. Händelsen har bara id:n."""
        namn = namn.strip()
        with self.db.transaktion() as c:
            org = c.execute("SELECT 1 FROM organisation WHERE id = ?", (organisation_id,))
            if not org.fetchone():
                raise RegisterFel("Okänd organisation")
            if self._utforarens_niva(c, utforare, organisation_id) < _NIVA_ORGADMIN:
                raise BehorighetFel("Kräver organisationsadmin eller ägare")
            if not namn or len(namn) > 120 or any(ord(t) < 32 for t in namn):
                raise RegisterFel("Projektnamnet ska vara 1 till 120 tecken utan styrtecken")
            if c.execute(
                "SELECT 1 FROM projekt WHERE organisation_id = ? AND namn = ?",
                (organisation_id, namn),
            ).fetchone():
                raise RegisterFel("Ett projekt med det namnet finns redan i organisationen")
            projekt_id = _ny_id()
            c.execute(
                "INSERT INTO projekt (id, organisation_id, forvaltning_id, namn, skapad,"
                " skapad_av, organisation_synlig) VALUES (?, ?, NULL, ?, ?, ?, ?)",
                (projekt_id, organisation_id, namn, self.nu(), utforare, int(organisation_synlig)),
            )
            c.execute(
                "INSERT INTO projektbehorighet (konto_id, projekt_id, roll, kalla, beviljad_av,"
                " skapad) VALUES (?, ?, 'utredare', 'manuell', ?, ?)",
                (utforare, projekt_id, utforare, self.nu()),
            )
            self.notera(c, "projekt-skapat", utforare, f"projekt={projekt_id} via=api")
            self.notera(c, "projektroll-beviljad", utforare, f"projekt={projekt_id} roll=utredare")
        return projekt_id

    def ta_bort_nytt_projekt(self, utforare: str, projekt_id: str) -> None:
        """Ångrar ett projekt som `skapa_projekt_av` just skapat när bygget av dess filer föll
        (bara skaparen, och bara om ingen annan fått roll i det)."""
        with self.db.transaktion() as c:
            rad = c.execute("SELECT skapad_av FROM projekt WHERE id = ?", (projekt_id,)).fetchone()
            if not rad or rad["skapad_av"] != utforare:
                return
            andra = c.execute(
                "SELECT 1 FROM projektbehorighet WHERE projekt_id = ? AND konto_id != ?",
                (projekt_id, utforare),
            ).fetchone()
            if andra:
                return
            c.execute("DELETE FROM projekt WHERE id = ?", (projekt_id,))
            self.notera(c, "projekt-angrat", utforare, f"projekt={projekt_id}")

    # --- behörighet -------------------------------------------------------------------------

    def _utforarens_niva(self, c: sqlite3.Connection, utforare: str, organisation_id: str) -> int:
        """Utförarens rättighetsnivå i organisationen: index i `ORGROLLER`, `len(ORGROLLER)` för
        superadmin och -1 för ingen rätt alls (okänt eller spärrat konto, ingen roll)."""
        rad = c.execute(
            "SELECT superadmin, sparrad FROM konto WHERE id = ?", (utforare,)
        ).fetchone()
        if not rad or rad["sparrad"] is not None:
            return -1
        if rad["superadmin"]:
            return _NIVA_SUPERADMIN
        roller = c.execute(
            "SELECT roll FROM medlemskap WHERE konto_id = ? AND organisation_id = ?",
            (utforare, organisation_id),
        ).fetchall()
        return max((ORGROLLER.index(r["roll"]) for r in roller), default=-1)

    def _far_administrera(self, c: sqlite3.Connection, utforare: str, organisation_id: str) -> bool:
        """Superadmin, organisationsadmin eller ägare i organisationen."""
        return self._utforarens_niva(c, utforare, organisation_id) >= _NIVA_ORGADMIN

    # --- projektets ägare och inställningar ------------------------------------------------

    def _ar_agare(self, c: sqlite3.Connection, konto_id: str, projekt_id: str) -> bool:
        """Är kontot (inte spärrat) projektets ägare? Ägare är den med den uttryckliga rollen
        `agare`, och skaparen så länge skaparen är utredare i projektet (så att projekt som
        skapades före rollen har en ägare)."""
        return konto_id in self._agare_ids(c, projekt_id)

    @staticmethod
    def _agare_ids(c: sqlite3.Connection, projekt_id: str) -> set[str]:
        rader = c.execute(
            "SELECT b.konto_id FROM projektbehorighet b"
            " JOIN projekt p ON p.id = b.projekt_id JOIN konto k ON k.id = b.konto_id"
            " WHERE b.projekt_id = ? AND k.sparrad IS NULL"
            " AND (b.roll = 'agare' OR (b.roll = 'utredare' AND b.konto_id = p.skapad_av))",
            (projekt_id,),
        ).fetchall()
        return {str(r["konto_id"]) for r in rader}

    def _far_andra_projekt(
        self, c: sqlite3.Connection, utforare: str, projekt_id: str, organisation_id: str
    ) -> bool:
        """Får utföraren ändra projektets namn, medlemmar och inbjudningar? Projektets ägare,
        organisationsadmin eller ägare i projektets organisation, och superadmin."""
        return self._far_administrera(c, utforare, organisation_id) or self._ar_agare(
            c, utforare, projekt_id
        )

    def _skydda_sista_projektagaren(
        self, c: sqlite3.Connection, konto_id: str, projekt_id: str
    ) -> None:
        """En projektägare kan inte lämna projektet utan ägare: den sista tas inte bort och sänks
        inte av en ägare (administratörer får, så att ett övergivet projekt kan tas över)."""
        agare = self._agare_ids(c, projekt_id)
        if agare == {konto_id}:
            raise RegisterFel("Projektet måste ha en ägare: utse en ny ägare först")

    def ar_projektagare(self, konto_id: str, projekt_id: str) -> bool:
        with self.db.lasning() as c:
            return self._ar_agare(c, konto_id, projekt_id)

    def _projektrad(self, c: sqlite3.Connection, projekt_id: str) -> sqlite3.Row:
        rad = c.execute(
            "SELECT p.id, p.namn, p.organisation_id, p.organisation_synlig, p.arkiverad,"
            " p.skapad, o.namn AS organisation FROM projekt p"
            " JOIN organisation o ON o.id = p.organisation_id WHERE p.id = ?",
            (projekt_id,),
        ).fetchone()
        if rad is None:
            raise RegisterFel("Okänt projekt")
        return rad  # type: ignore[no-any-return]

    def _far_arkivera(self, c: sqlite3.Connection, utforare: str, projekt_id: str) -> bool:
        """Arkivera, återställa och radera: projektets ägare och superadmin (inte en
        organisationsadmin som inte är ägare i projektet)."""
        rad = c.execute(
            "SELECT superadmin, sparrad FROM konto WHERE id = ?", (utforare,)
        ).fetchone()
        if rad is None or rad["sparrad"] is not None:
            return False
        return bool(rad["superadmin"]) or self._ar_agare(c, utforare, projekt_id)

    def projektinstallningar(self, konto_id: str, projekt_id: str) -> dict[str, Any]:
        """Underlaget till sidan Projektinställningar. Den får läsas av den som har en roll i
        projektet (också ur organisationsrollen) och av den som får ändra det; alla andra får
        `RegisterFel("Okänt projekt")`, så att ett id inte går att pröva fram.

        `far_andra`: namn, medlemmar och inbjudningar (e-postadresser och inbjudningar visas bara
        för dem). `far_andra_delning`: som i `kan_andra_delning`. `far_arkivera_radera`: ägare och
        superadmin."""
        with self.db.lasning() as c:
            rad = self._projektrad(c, projekt_id)
            konto = c.execute(
                "SELECT superadmin, sparrad FROM konto WHERE id = ?", (konto_id,)
            ).fetchone()
            if konto is None or konto["sparrad"] is not None:
                raise RegisterFel("Okänt projekt")
            far_andra = self._far_andra_projekt(c, konto_id, projekt_id, rad["organisation_id"])
            far_arkivera = self._far_arkivera(c, konto_id, projekt_id)
            agare = self._agare_ids(c, projekt_id)
            medlemmar = c.execute(
                "SELECT k.id AS konto_id, k.namn, k.epost, b.roll, b.kalla,"
                " k.sparrad IS NOT NULL AS sparrad FROM projektbehorighet b"
                " JOIN konto k ON k.id = b.konto_id WHERE b.projekt_id = ?"
                " ORDER BY k.namn, k.id",
                (projekt_id,),
            ).fetchall()
        effektiv = self.effektiv_projektroll(konto_id, projekt_id)
        if not (effektiv.roll or far_andra or far_arkivera):
            raise RegisterFel("Okänt projekt")
        ut_medlemmar = [
            {
                "konto_id": m["konto_id"],
                "namn": m["namn"],
                "roll": m["roll"],
                "ar_agare": m["konto_id"] in agare,
                "ar_du": m["konto_id"] == konto_id,
                "sparrad": bool(m["sparrad"]),
                **({"epost": m["epost"], "kalla": m["kalla"]} if far_andra else {}),
            }
            for m in medlemmar
        ]
        return {
            "projekt": {
                "id": rad["id"],
                "namn": rad["namn"],
                "organisation_id": rad["organisation_id"],
                "organisation": rad["organisation"],
                "organisation_synlig": bool(rad["organisation_synlig"]),
                "arkiverad": rad["arkiverad"] is not None,
                "skapad": rad["skapad"],
            },
            "din_roll": effektiv.roll,
            "din_roll_kalla": effektiv.kalla,
            "ar_agare": konto_id in agare,
            "far_andra": far_andra,
            "far_andra_delning": self.kan_andra_delning(konto_id, projekt_id),
            "far_arkivera_radera": far_arkivera,
            "roller": list(PROJEKTROLLER),
            "medlemmar": ut_medlemmar,
            "inbjudningar": self.projekt_inbjudningar(konto_id, projekt_id) if far_andra else [],
        }

    def byt_projektnamn(self, utforare: str, projekt_id: str, namn: str) -> str:
        """Byter projektets namn i registret (id, projektmapp och körningar är oförändrade).
        Projektets ägare, organisationsadmin och ägare i organisationen, och superadmin. Namnet
        ska vara unikt i organisationen. Ger det nya (trimmade) namnet."""
        namn = namn.strip()
        with self.db.transaktion() as c:
            rad = self._projektrad(c, projekt_id)
            if not self._far_andra_projekt(c, utforare, projekt_id, rad["organisation_id"]):
                raise BehorighetFel("Kräver organisationsadmin eller projektets ägare")
            if not namn or len(namn) > 120 or any(ord(t) < 32 for t in namn):
                raise RegisterFel("Projektnamnet ska vara 1 till 120 tecken utan styrtecken")
            if c.execute(
                "SELECT 1 FROM projekt WHERE organisation_id = ? AND namn = ? AND id != ?",
                (rad["organisation_id"], namn, projekt_id),
            ).fetchone():
                raise RegisterFel("Ett projekt med det namnet finns redan i organisationen")
            c.execute("UPDATE projekt SET namn = ? WHERE id = ?", (namn, projekt_id))
            self.notera(c, "projekt-namn-andrat", utforare, f"projekt={projekt_id}")
        return namn

    def satt_arkiverad(self, utforare: str, projekt_id: str, arkiverad: bool) -> None:
        """Arkiverar projektet (det döljs i projektlistan) eller återställer det. Roller, data och
        körningar rörs inte. Projektets ägare och superadmin."""
        with self.db.transaktion() as c:
            self._projektrad(c, projekt_id)
            if not self._far_arkivera(c, utforare, projekt_id):
                raise BehorighetFel("Bara projektets ägare och superadmin arkiverar projektet")
            c.execute(
                "UPDATE projekt SET arkiverad = ? WHERE id = ?",
                (self.nu() if arkiverad else None, projekt_id),
            )
            self.notera(
                c,
                "projekt-arkiverat" if arkiverad else "projekt-aterstallt",
                utforare,
                f"projekt={projekt_id}",
            )

    def kontrollera_radera(self, utforare: str, projekt_id: str, bekrafta_namn: str) -> str:
        """Prövar att utföraren får radera projektet och har skrivit dess namn rätt. Ger namnet.
        `BehorighetFel` om utföraren inte är projektets ägare eller superadmin, `RegisterFel` för
        okänt projekt eller fel bekräftelse."""
        with self.db.lasning() as c:
            rad = self._projektrad(c, projekt_id)
            if not self._far_arkivera(c, utforare, projekt_id):
                raise BehorighetFel("Bara projektets ägare och superadmin raderar projektet")
        if bekrafta_namn.strip() != rad["namn"]:
            raise RegisterFel("Skriv projektets namn exakt för att bekräfta raderingen")
        return str(rad["namn"])

    def radera_projekt(self, utforare: str, projekt_id: str) -> dict[str, int]:
        """Tar bort projektet ur registret: posten, projektrollerna och inbjudningarna (ON DELETE
        CASCADE). Händelsen `projekt-raderat` har bara konto-id, projekt-id och organisations-id;
        inget namn och ingen e-postadress. Filerna tas bort av `webb.projektinstallningar`."""
        with self.db.transaktion() as c:
            rad = self._projektrad(c, projekt_id)
            if not self._far_arkivera(c, utforare, projekt_id):
                raise BehorighetFel("Bara projektets ägare och superadmin raderar projektet")
            roller = c.execute(
                "SELECT COUNT(*) FROM projektbehorighet WHERE projekt_id = ?", (projekt_id,)
            ).fetchone()[0]
            inbjudningar = c.execute(
                "SELECT COUNT(*) FROM projektinbjudan WHERE projekt_id = ?", (projekt_id,)
            ).fetchone()[0]
            c.execute("DELETE FROM projekt WHERE id = ?", (projekt_id,))
            self.notera(
                c,
                "projekt-raderat",
                utforare,
                f"projekt={projekt_id} organisation={rad['organisation_id']}"
                f" roller={roller} inbjudningar={inbjudningar}",
            )
        return {"roller": int(roller), "inbjudningar": int(inbjudningar)}

    def aterkalla_inbjudan(self, utforare: str, projekt_id: str, inbjudan_id: str) -> None:
        """Tar bort en öppen inbjudan till projektet."""
        with self.db.transaktion() as c:
            self._projekt_for_admin(c, utforare, projekt_id)
            gjord = c.execute(
                "DELETE FROM projektinbjudan WHERE id = ? AND projekt_id = ? AND anvand_av IS NULL",
                (inbjudan_id, projekt_id),
            ).rowcount
            if not gjord:
                raise RegisterFel("Okänd inbjudan")
            self.notera(c, "inbjudan-aterkallad", utforare, f"inbjudan={inbjudan_id}")

    def projektroll(self, konto_id: str, projekt_id: str) -> str | None:
        """Kontots uttryckliga roll i projektet (tabellen `projektbehorighet`), annars None.
        Organisationsrollerna ingår inte här: se `effektiv_projektroll`."""
        with self.db.transaktion() as c:
            rad = c.execute(
                "SELECT p.roll FROM projektbehorighet p JOIN konto k ON k.id = p.konto_id"
                " WHERE p.konto_id = ? AND p.projekt_id = ? AND k.sparrad IS NULL",
                (konto_id, projekt_id),
            ).fetchone()
        return str(rad["roll"]) if rad else None

    # --- effektiv projektroll: den enda platsen där organisationsroller blir projekträtt --------

    def effektiv_projektroll(self, konto_id: str, projekt_id: str) -> EffektivRoll:
        """Kontots verkliga rätt i projektet: den uttryckliga projektrollen, och om projektet är
        delat med organisationen (`organisation_synlig`) den högsta av den och det
        organisationsrollen ger (ledare: `lasare`; organisationsadmin och ägare: `utredare`; i
        projektets egen organisation, inte i någon annan). Ett privat projekt (förval) ger
        organisationsrollerna ingenting. Ett spärrat konto har ingen rätt.
        Superadmin ger ingen projektdata. Det här är regeln som webben, API:t, körningarna och
        MCP-verktygen alla går genom (`KontoAdapter.har_roll`)."""
        with self.db.lasning() as c:
            rad = c.execute(
                "SELECT k.sparrad, p.organisation_id, p.organisation_synlig,"
                " (SELECT b.roll FROM projektbehorighet b"
                "  WHERE b.konto_id = k.id AND b.projekt_id = p.id) AS projektroll"
                " FROM konto k, projekt p WHERE k.id = ? AND p.id = ?",
                (konto_id, projekt_id),
            ).fetchone()
            if rad is None or rad["sparrad"] is not None:
                # Okänt konto, eller ett projekt som inte finns i registret (t.ex. demokvarteret).
                return EffektivRoll(None)
            orgroller = (
                c.execute(
                    "SELECT roll FROM medlemskap WHERE konto_id = ? AND organisation_id = ?",
                    (konto_id, rad["organisation_id"]),
                ).fetchall()
                if rad["organisation_synlig"]
                else []
            )
        uttrycklig = rad["projektroll"]
        fran_org = max(
            (r for r in (projektroll_fran_organisationsroll(m["roll"]) for m in orgroller) if r),
            key=_roll_niva,
            default=None,
        )
        if fran_org is not None and (
            uttrycklig is None or _roll_niva(fran_org) > _roll_niva(uttrycklig)
        ):
            return EffektivRoll(fran_org, "organisation")
        return EffektivRoll(uttrycklig, "projekt" if uttrycklig else "")

    def har_projektroll(self, konto_id: str, projekt_id: str, minst: str = "lasare") -> bool:
        return self.effektiv_projektroll(konto_id, projekt_id).raeckar(minst)

    def projekt_med_effektiv_roll(
        self, konto_id: str, arkiverade: bool = False
    ) -> list[dict[str, Any]]:
        """Registrets projekt kontot har rätt i, med den effektiva rollen (samma regel som
        `effektiv_projektroll`): id, namn, roll, kalla (`projekt` eller `organisation`) och
        `organisation_synlig` (projektet är delat med organisationen) och `arkiverad`. Arkiverade
        projekt ingår bara med `arkiverade=True` (då är det de enda som ingår)."""
        with self.db.lasning() as c:
            if (
                c.execute(
                    "SELECT 1 FROM konto WHERE id = ? AND sparrad IS NULL", (konto_id,)
                ).fetchone()
                is None
            ):
                return []
            rader = c.execute(
                "SELECT p.id, p.namn, p.organisation_id, p.organisation_synlig, p.arkiverad,"
                " b.roll AS projektroll FROM projekt p"
                " LEFT JOIN projektbehorighet b ON b.projekt_id = p.id AND b.konto_id = ?",
                (konto_id,),
            ).fetchall()
            orgroller: dict[str, str] = {}
            for m in c.execute(
                "SELECT organisation_id, roll FROM medlemskap WHERE konto_id = ?", (konto_id,)
            ):
                ger = projektroll_fran_organisationsroll(m["roll"])
                bef = orgroller.get(m["organisation_id"])
                if ger and (bef is None or _roll_niva(ger) > _roll_niva(bef)):
                    orgroller[m["organisation_id"]] = ger
        ut: list[dict[str, Any]] = []
        for r in sorted(rader, key=lambda x: (x["namn"], x["id"])):
            if (r["arkiverad"] is not None) != arkiverade:
                continue
            uttrycklig = r["projektroll"]
            fran_org = orgroller.get(r["organisation_id"]) if r["organisation_synlig"] else None
            delad = bool(r["organisation_synlig"])
            if fran_org is not None and (
                uttrycklig is None or _roll_niva(fran_org) > _roll_niva(uttrycklig)
            ):
                roll, kalla = fran_org, "organisation"
            elif uttrycklig is not None:
                roll, kalla = uttrycklig, "projekt"
            else:
                continue
            ut.append(
                {
                    "id": r["id"],
                    "namn": r["namn"],
                    "roll": roll,
                    "kalla": kalla,
                    "organisation_synlig": delad,
                    "arkiverad": r["arkiverad"] is not None,
                }
            )
        return ut

    def satt_organisation_synlig(self, utforare: str, projekt_id: str, synlig: bool) -> None:
        """Delar projektet med organisationen (eller gör det privat igen). Bara projektets
        utredare (uttrycklig roll) och den som skapade projektet får: inte organisationens ägare
        eller admin, inte superadmin, om de inte själva har rollen. Ändringen gäller direkt."""
        with self.db.transaktion() as c:
            rad = c.execute("SELECT skapad_av FROM projekt WHERE id = ?", (projekt_id,)).fetchone()
            konto = c.execute("SELECT sparrad FROM konto WHERE id = ?", (utforare,)).fetchone()
            if not rad or not konto:
                raise RegisterFel("Okänt projekt")
            utredare = c.execute(
                "SELECT 1 FROM projektbehorighet WHERE konto_id = ? AND projekt_id = ?"
                " AND roll IN ('utredare', 'agare')",
                (utforare, projekt_id),
            ).fetchone()
            if konto["sparrad"] is not None or not (utredare or rad["skapad_av"] == utforare):
                raise BehorighetFel("Bara projektets utredare, ägare och skaparen ändrar delningen")
            c.execute(
                "UPDATE projekt SET organisation_synlig = ? WHERE id = ?",
                (int(synlig), projekt_id),
            )
            self.notera(
                c,
                "projekt-delning-andrad",
                utforare,
                f"projekt={projekt_id} organisation_synlig={'ja' if synlig else 'nej'}",
            )

    def kan_andra_delning(self, konto_id: str, projekt_id: str) -> bool:
        """Får kontot ändra projektets delning med organisationen? (Samma regel som ovan.)"""
        with self.db.lasning() as c:
            rad = c.execute(
                "SELECT p.skapad_av, k.sparrad,"
                " EXISTS (SELECT 1 FROM projektbehorighet b WHERE b.konto_id = k.id"
                "  AND b.projekt_id = p.id AND b.roll IN ('utredare', 'agare')) AS utredare"
                " FROM projekt p, konto k WHERE p.id = ? AND k.id = ?",
                (projekt_id, konto_id),
            ).fetchone()
        return bool(
            rad and rad["sparrad"] is None and (rad["utredare"] or rad["skapad_av"] == konto_id)
        )

    def ar_superadmin(self, konto_id: str) -> bool:
        """Är kontot en icke spärrad superadmin? (Ger ingen projektdata, se `har_projektroll`.)"""
        with self.db.transaktion() as c:
            rad = c.execute(
                "SELECT superadmin FROM konto WHERE id = ? AND sparrad IS NULL", (konto_id,)
            ).fetchone()
        return bool(rad and rad["superadmin"])

    def ge_projektroll(self, utforare: str, konto_id: str, projekt_id: str, roll: str) -> None:
        """Uttrycklig tilldelning (organisationsadmin i projektets organisation, superadmin).
        Utom superadmin får ingen ge sig själv en roll i ett privat projekt; superadmin får, och
        det loggas som `projektroll-sjalvtilldelad`."""
        _roll_niva(roll)
        with self.db.transaktion() as c:
            org = c.execute(
                "SELECT organisation_id, organisation_synlig FROM projekt WHERE id = ?",
                (projekt_id,),
            ).fetchone()
            if not org:
                raise RegisterFel("Okänt projekt")
            if not self._far_andra_projekt(c, utforare, projekt_id, org["organisation_id"]):
                raise BehorighetFel("Kräver organisationsadmin eller projektets ägare")
            if konto_id == utforare and not self._ar_agare(c, utforare, projekt_id):
                self._sjalvtilldelning(c, utforare, projekt_id, bool(org["organisation_synlig"]))
            skapare = c.execute("SELECT skapad_av FROM projekt WHERE id = ?", (projekt_id,))
            forblir_agare = roll == "agare" or (
                roll == "utredare" and konto_id == skapare.fetchone()["skapad_av"]
            )
            admin = self._far_administrera(c, utforare, org["organisation_id"])
            if not forblir_agare and not admin:
                self._skydda_sista_projektagaren(c, konto_id, projekt_id)
            c.execute(
                "INSERT INTO projektbehorighet (konto_id, projekt_id, roll, kalla, beviljad_av,"
                " skapad) VALUES (?, ?, ?, 'manuell', ?, ?)"
                " ON CONFLICT (konto_id, projekt_id) DO UPDATE SET roll = excluded.roll,"
                " kalla = 'manuell', beviljad_av = excluded.beviljad_av",
                (konto_id, projekt_id, roll, utforare, self.nu()),
            )
            self.notera(c, "projektroll-beviljad", konto_id, f"projekt={projekt_id} roll={roll}")

    def _sjalvtilldelning(
        self, c: sqlite3.Connection, utforare: str, projekt_id: str, synligt: bool
    ) -> None:
        """Regeln när en administratör ger sig själv rätt i ett projekt (direkt eller med en
        inbjudan till den egna adressen): i ett privat projekt bara superadmin, och det loggas."""
        ar_super = c.execute("SELECT superadmin FROM konto WHERE id = ?", (utforare,)).fetchone()[
            "superadmin"
        ]
        if not ar_super:
            if synligt:
                return  # delat projekt: rollen ger inget som organisationsrollen inte redan gav
            raise BehorighetFel(
                "Du kan inte ge dig själv en roll i ett privat projekt; projektets utredare gör det"
            )
        self.notera(c, "projektroll-sjalvtilldelad", utforare, f"projekt={projekt_id}")

    def aterkalla_projektroll(self, utforare: str, konto_id: str, projekt_id: str) -> None:
        with self.db.transaktion() as c:
            org = c.execute(
                "SELECT organisation_id FROM projekt WHERE id = ?", (projekt_id,)
            ).fetchone()
            if not org or not self._far_andra_projekt(
                c, utforare, projekt_id, org["organisation_id"]
            ):
                raise BehorighetFel("Kräver organisationsadmin eller projektets ägare")
            if not self._far_administrera(c, utforare, org["organisation_id"]):
                self._skydda_sista_projektagaren(c, konto_id, projekt_id)
            c.execute(
                "DELETE FROM projektbehorighet WHERE konto_id = ? AND projekt_id = ?",
                (konto_id, projekt_id),
            )
            self.notera(c, "projektroll-aterkallad", konto_id, f"projekt={projekt_id}")

    def bjud_in(
        self,
        utforare: str,
        projekt_id: str,
        epost: str,
        roll: str = "lasare",
        giltig_dagar: int = INBJUDAN_GILTIG_DAGAR,
    ) -> str:
        """Inbjudan till ett projekt, bunden till en e-postadress. Tas emot först när personen
        loggar in med en verifierad adress (`tilldelning.tillampa_inbjudningar`)."""
        _roll_niva(roll)
        ren = normalisera_epost(epost)
        inbjudan_id = _ny_id()
        with self.db.transaktion() as c:
            org = c.execute(
                "SELECT organisation_id, organisation_synlig FROM projekt WHERE id = ?",
                (projekt_id,),
            ).fetchone()
            if not org:
                raise RegisterFel("Okänt projekt")
            if not self._far_andra_projekt(c, utforare, projekt_id, org["organisation_id"]):
                raise BehorighetFel("Kräver organisationsadmin eller projektets ägare")
            egen = c.execute("SELECT epost FROM konto WHERE id = ?", (utforare,)).fetchone()
            if (
                egen is not None
                and egen["epost"] == ren
                and not self._ar_agare(c, utforare, projekt_id)
            ):
                self._sjalvtilldelning(c, utforare, projekt_id, bool(org["organisation_synlig"]))
            c.execute(
                "INSERT INTO projektinbjudan (id, projekt_id, epost, roll, skapad_av, skapad,"
                " giltig_till) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    inbjudan_id,
                    projekt_id,
                    ren,
                    roll,
                    utforare,
                    self.nu(),
                    self.nu() + giltig_dagar * 86400,
                ),
            )
            self.notera(c, "inbjudan-skapad", utforare, f"inbjudan={inbjudan_id} roll={roll}")
        return inbjudan_id

    # --- administration i webben (organisationsadmin och superadmin) ---------------------------

    def ar_admin(self, konto_id: str) -> bool:
        """Superadmin, eller organisationsadmin i någon organisation (och inte spärrad)."""
        with self.db.lasning() as c:
            rad = c.execute(
                "SELECT superadmin, sparrad FROM konto WHERE id = ?", (konto_id,)
            ).fetchone()
            if not rad or rad["sparrad"] is not None:
                return False
            if rad["superadmin"]:
                return True
            return (
                c.execute(
                    "SELECT 1 FROM medlemskap WHERE konto_id = ? AND roll IN " + _ADMINROLLER_SQL,
                    (konto_id,),
                ).fetchone()
                is not None
            )

    def _projekt_for_admin(self, c: sqlite3.Connection, utforare: str, projekt_id: str) -> str:
        """Projektets organisation, om utföraren får administrera den. Annars `BehorighetFel`."""
        org = c.execute(
            "SELECT organisation_id FROM projekt WHERE id = ?", (projekt_id,)
        ).fetchone()
        if not org:
            raise RegisterFel("Okänt projekt")
        if not self._far_andra_projekt(c, utforare, projekt_id, org["organisation_id"]):
            raise BehorighetFel("Kräver organisationsadmin eller projektets ägare")
        return str(org["organisation_id"])

    def admin_projekt(self, utforare: str) -> list[dict[str, object]]:
        """Projekten utföraren får administrera: alla för superadmin, annars organisationernas."""
        if not self.ar_admin(utforare):
            raise BehorighetFel("Kräver organisationsadmin")
        with self.db.lasning() as c:
            rader = c.execute(
                "SELECT p.id, p.namn, p.organisation_id, o.namn AS organisation,"
                " p.organisation_synlig, p.arkiverad,"
                " (SELECT COUNT(*) FROM projektbehorighet b"
                "  WHERE b.projekt_id = p.id) AS medlemmar,"
                " (SELECT COUNT(*) FROM projektinbjudan i WHERE i.projekt_id = p.id"
                "  AND i.anvand_av IS NULL AND i.giltig_till > ?) AS oppna_inbjudningar"
                " FROM projekt p JOIN organisation o ON o.id = p.organisation_id"
                " WHERE (SELECT superadmin FROM konto WHERE id = ?) = 1 OR p.organisation_id IN"
                "  (SELECT organisation_id FROM medlemskap WHERE konto_id = ?"
                "   AND roll IN " + _ADMINROLLER_SQL + ")"
                " ORDER BY o.namn, p.namn",
                (self.nu(), utforare, utforare),
            ).fetchall()
        return [
            {
                **dict(r),
                "organisation_synlig": bool(r["organisation_synlig"]),
                "arkiverad": r["arkiverad"] is not None,
                "kan_andra_delning": self.kan_andra_delning(utforare, r["id"]),
            }
            for r in rader
        ]

    def projekt_medlemmar(self, utforare: str, projekt_id: str) -> list[dict[str, object]]:
        """Konton med en projektroll (id, namn, e-post, roll, källa, spärrad)."""
        with self.db.lasning() as c:
            self._projekt_for_admin(c, utforare, projekt_id)
            rader = c.execute(
                "SELECT k.id AS konto_id, k.namn, k.epost, b.roll, b.kalla, b.skapad,"
                " k.sparrad IS NOT NULL AS sparrad FROM projektbehorighet b"
                " JOIN konto k ON k.id = b.konto_id WHERE b.projekt_id = ?"
                " ORDER BY k.namn, k.id",
                (projekt_id,),
            ).fetchall()
        return [{**dict(r), "sparrad": bool(r["sparrad"])} for r in rader]

    def projekt_inbjudningar(self, utforare: str, projekt_id: str) -> list[dict[str, object]]:
        """Projektets inbjudningar med status `oppen`, `anvand` eller `utgangen`."""
        with self.db.lasning() as c:
            self._projekt_for_admin(c, utforare, projekt_id)
            rader = c.execute(
                "SELECT id, epost, roll, skapad, giltig_till, anvand_tid, anvand_av"
                " FROM projektinbjudan WHERE projekt_id = ? ORDER BY skapad DESC, id",
                (projekt_id,),
            ).fetchall()
        nu = self.nu()
        ut: list[dict[str, object]] = []
        for r in rader:
            status = (
                "anvand"
                if r["anvand_av"] is not None
                else "utgangen"
                if r["giltig_till"] <= nu
                else "oppen"
            )
            ut.append(
                {
                    "id": r["id"],
                    "epost": r["epost"],
                    "roll": r["roll"],
                    "skapad": r["skapad"],
                    "giltig_till": r["giltig_till"],
                    "status": status,
                }
            )
        return ut

    def satt_sparrad(self, utforare: str, konto_id: str, sparrad: bool) -> None:
        """Spärrar eller öppnar ett konto. Superadmin: alla utom sig själv och andra superadmin.
        Organisationsadmin: bara konton med medlemskap eller projektroll i den egna organisationen,
        och aldrig en annan organisationsadmin. Spärrade konton har inga giltiga sessioner."""
        with self.db.transaktion() as c:
            if not self.ar_admin(utforare):
                raise BehorighetFel("Kräver organisationsadmin")
            if utforare == konto_id:
                raise BehorighetFel("Du kan inte spärra ditt eget konto")
            mal = c.execute("SELECT superadmin FROM konto WHERE id = ?", (konto_id,)).fetchone()
            if not mal:
                raise RegisterFel("Okänt konto")
            if mal["superadmin"]:
                raise BehorighetFel("Ett superadmin-konto spärras bara med serverkommando")
            ar_super = c.execute(
                "SELECT superadmin FROM konto WHERE id = ?", (utforare,)
            ).fetchone()["superadmin"]
            if not ar_super:
                egna = (
                    "SELECT organisation_id FROM medlemskap WHERE konto_id = ?"
                    " AND roll IN " + _ADMINROLLER_SQL
                )
                i_orgen = c.execute(
                    "SELECT 1 FROM medlemskap WHERE konto_id = ?"
                    " AND organisation_id IN (" + egna + ")"
                    " UNION SELECT 1 FROM projektbehorighet b JOIN projekt p ON p.id = b.projekt_id"
                    " WHERE b.konto_id = ? AND p.organisation_id IN (" + egna + ")",
                    (konto_id, utforare, konto_id, utforare),
                ).fetchone()
                if not i_orgen:
                    raise BehorighetFel("Kontot tillhör inte din organisation")
                kollega = c.execute(
                    "SELECT 1 FROM medlemskap WHERE konto_id = ? AND roll IN "
                    + _ADMINROLLER_SQL
                    + " AND organisation_id IN ("
                    + egna
                    + ")",
                    (konto_id, utforare),
                ).fetchone()
                if kollega:
                    raise BehorighetFel("En organisationsadmin spärras av superadmin")
            c.execute(
                "UPDATE konto SET sparrad = ? WHERE id = ?",
                (self.nu() if sparrad else None, konto_id),
            )
            self.notera(
                c, "konto-sparrat" if sparrad else "konto-oppnat", konto_id, f"av={utforare}"
            )

    # --- organisationens medlemmar och inbjudningar -------------------------------------------
    #
    # Vem får vad (superadmin räknas som över ägare):
    #   medlem, ledare       ingen administration
    #   organisationsadmin   bjuda in medlem/ledare, ändra och ta bort medlem/ledare
    #   ägare, superadmin    allt ovan, och utse och ta bort organisationsadmin och ägare, byta namn
    # Att mappa geografier, ge superadmin, läsa andras nycklar och ändra serverinställningar finns
    # inte här: det är serverkommandon eller superadmin, och ingen organisationsroll ger det.

    @staticmethod
    def _far_tilldela(niva: int, roll: str) -> bool:
        """Får en utförare på nivån `niva` ge (eller bjuda in med) rollen?"""
        if niva >= _NIVA_AGARE:
            return True
        return niva == _NIVA_ORGADMIN and roll in ("medlem", "ledare")

    def tillatna_organisationsroller(self, utforare: str, organisation_id: str) -> list[str]:
        """Rollerna utföraren får ge i organisationen (tom lista om hen inte administrerar den)."""
        with self.db.lasning() as c:
            niva = self._utforarens_niva(c, utforare, organisation_id)
        return [r for r in ORGROLLER if self._far_tilldela(niva, r)]

    def _organisation_for_admin(
        self, c: sqlite3.Connection, utforare: str, organisation_id: str
    ) -> int:
        """Utförarens nivå i en befintlig organisation, om hen administrerar den."""
        if not c.execute("SELECT 1 FROM organisation WHERE id = ?", (organisation_id,)).fetchone():
            raise RegisterFel("Okänd organisation")
        niva = self._utforarens_niva(c, utforare, organisation_id)
        if niva < _NIVA_ORGADMIN:
            raise BehorighetFel("Kräver organisationsadmin")
        return niva

    def admin_organisationer(self, utforare: str) -> list[dict[str, object]]:
        """Organisationerna utföraren administrerar (alla för superadmin) med hens egen roll och
        rollerna hen får ge. Ordning: namn."""
        if not self.ar_admin(utforare):
            raise BehorighetFel("Kräver organisationsadmin")
        with self.db.lasning() as c:
            orgar = c.execute("SELECT id, namn FROM organisation ORDER BY namn, id").fetchall()
            ut: list[dict[str, object]] = []
            for o in orgar:
                niva = self._utforarens_niva(c, utforare, o["id"])
                if niva < _NIVA_ORGADMIN:
                    continue
                ut.append(
                    {
                        "id": o["id"],
                        "namn": o["namn"],
                        "din_roll": "superadmin" if niva == _NIVA_SUPERADMIN else ORGROLLER[niva],
                        "far_byta_namn": niva >= _NIVA_AGARE,
                        "roller": list(ORGROLLER),
                        "tillatna_roller": [r for r in ORGROLLER if self._far_tilldela(niva, r)],
                    }
                )
        return ut

    def organisation_medlemmar(
        self, utforare: str, organisation_id: str
    ) -> list[dict[str, object]]:
        """Organisationens medlemmar på organisationsnivå: konto-id, namn, e-post, roll, källa,
        spärrad. Kräver organisationsadmin, ägare eller superadmin."""
        with self.db.lasning() as c:
            self._organisation_for_admin(c, utforare, organisation_id)
            rader = c.execute(
                "SELECT k.id AS konto_id, k.namn, k.epost, m.roll, m.kalla,"
                " k.sparrad IS NOT NULL AS sparrad FROM medlemskap m"
                " JOIN konto k ON k.id = m.konto_id"
                " WHERE m.organisation_id = ? AND m.forvaltning_id IS NULL ORDER BY k.namn, k.id",
                (organisation_id,),
            ).fetchall()
        return [{**dict(r), "sparrad": bool(r["sparrad"])} for r in rader]

    def organisation_inbjudningar(
        self, utforare: str, organisation_id: str
    ) -> list[dict[str, object]]:
        """Organisationens inbjudningar med status `oppen`, `anvand` eller `utgangen`."""
        with self.db.lasning() as c:
            self._organisation_for_admin(c, utforare, organisation_id)
            rader = c.execute(
                "SELECT id, epost, roll, skapad, giltig_till, anvand_av FROM organisationsinbjudan"
                " WHERE organisation_id = ? ORDER BY skapad DESC, id",
                (organisation_id,),
            ).fetchall()
        nu = self.nu()
        return [
            {
                "id": r["id"],
                "epost": r["epost"],
                "roll": r["roll"],
                "skapad": r["skapad"],
                "giltig_till": r["giltig_till"],
                "status": "anvand"
                if r["anvand_av"] is not None
                else "utgangen"
                if r["giltig_till"] <= nu
                else "oppen",
            }
            for r in rader
        ]

    def _skapa_organisationsinbjudan(
        self,
        c: sqlite3.Connection,
        skapad_av: str,
        organisation_id: str,
        epost: str,
        roll: str,
        giltig_dagar: int,
    ) -> str:
        ren = normalisera_epost(epost)
        inbjudan_id = _ny_id()
        c.execute(
            "INSERT INTO organisationsinbjudan (id, organisation_id, epost, roll, skapad_av,"
            " skapad, giltig_till) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                inbjudan_id,
                organisation_id,
                ren,
                roll,
                skapad_av,
                self.nu(),
                self.nu() + giltig_dagar * 86400,
            ),
        )
        self.notera(
            c,
            "orginbjudan-skapad",
            None if skapad_av == "serverkommando" else skapad_av,
            f"inbjudan={inbjudan_id} organisation={organisation_id} roll={roll}",
        )
        return inbjudan_id

    def bjud_in_organisation(
        self,
        utforare: str,
        organisation_id: str,
        epost: str,
        roll: str = "medlem",
        giltig_dagar: int = INBJUDAN_GILTIG_DAGAR,
    ) -> str:
        """Inbjudan till organisationen, bunden till en e-postadress och tas emot när personen
        loggar in med en verifierad adress (`tilldelning`). Organisationsadmin får bjuda in
        medlem och ledare; ägare och superadmin får bjuda in med alla roller."""
        _orgroll_niva(roll)
        normalisera_epost(epost)
        with self.db.transaktion() as c:
            niva = self._organisation_for_admin(c, utforare, organisation_id)
            if not self._far_tilldela(niva, roll):
                raise BehorighetFel(
                    f"Rollen {roll} kan bara ges av organisationens ägare eller superadmin"
                )
            return self._skapa_organisationsinbjudan(
                c, utforare, organisation_id, epost, roll, giltig_dagar
            )

    def bjud_in_organisation_server(
        self,
        organisation_id: str,
        epost: str,
        roll: str,
        giltig_dagar: int = INBJUDAN_GILTIG_DAGAR,
    ) -> str:
        """Serverkommandot `bjud-in-organisation`: ingen utförare prövas (den som har serverns
        skal har rätt att ge vilken roll som helst), organisationen ska finnas."""
        _orgroll_niva(roll)
        normalisera_epost(epost)
        with self.db.transaktion() as c:
            if not c.execute(
                "SELECT 1 FROM organisation WHERE id = ?", (organisation_id,)
            ).fetchone():
                raise RegisterFel("Okänd organisation")
            return self._skapa_organisationsinbjudan(
                c, "serverkommando", organisation_id, epost, roll, giltig_dagar
            )

    def _organisationsroll_for(
        self, c: sqlite3.Connection, konto_id: str, organisation_id: str
    ) -> str | None:
        """Kontots högsta roll på organisationsnivå (inte förvaltningsrader), annars None."""
        rader = c.execute(
            "SELECT roll FROM medlemskap WHERE konto_id = ? AND organisation_id = ?"
            " AND forvaltning_id IS NULL",
            (konto_id, organisation_id),
        ).fetchall()
        return max((r["roll"] for r in rader), key=ORGROLLER.index, default=None)

    def _skydda_sista_agaren(
        self,
        c: sqlite3.Connection,
        niva: int,
        konto_id: str,
        organisation_id: str,
        nuvarande: str,
        ny_roll: str | None,
    ) -> None:
        """En organisation ska ha minst en ägare; bara superadmin kan lämna den utan."""
        if nuvarande != "agare" or ny_roll == "agare" or niva == _NIVA_SUPERADMIN:
            return
        andra = c.execute(
            "SELECT 1 FROM medlemskap WHERE organisation_id = ? AND roll = 'agare'"
            " AND forvaltning_id IS NULL AND konto_id != ?",
            (organisation_id, konto_id),
        ).fetchone()
        if not andra:
            raise BehorighetFel("Organisationen måste ha minst en ägare")

    def satt_organisationsroll(
        self, utforare: str, konto_id: str, organisation_id: str, roll: str
    ) -> None:
        """Ändrar en medlems roll. Organisationsadmin kan bara växla mellan medlem och ledare och
        rör aldrig en organisationsadmin eller ägare; ägare och superadmin kan allt (och utser och
        tar bort organisationsadmin). Den sista ägaren kan bara sänkas av superadmin."""
        _orgroll_niva(roll)
        with self.db.transaktion() as c:
            niva = self._organisation_for_admin(c, utforare, organisation_id)
            nuvarande = self._organisationsroll_for(c, konto_id, organisation_id)
            if nuvarande is None:
                raise RegisterFel("Okänt medlemskap")
            if not (self._far_tilldela(niva, roll) and self._far_tilldela(niva, nuvarande)):
                raise BehorighetFel(
                    "Rollerna organisationsadmin och ägare ändras bara av organisationens ägare"
                    " eller superadmin"
                )
            self._skydda_sista_agaren(c, niva, konto_id, organisation_id, nuvarande, roll)
            c.execute(
                "UPDATE medlemskap SET roll = ?, kalla = 'manuell', bekraftad_av = ?"
                " WHERE konto_id = ? AND organisation_id = ? AND forvaltning_id IS NULL",
                (roll, utforare, konto_id, organisation_id),
            )
            self.notera(
                c,
                "orgroll-andrad",
                konto_id,
                f"organisation={organisation_id} roll={roll} av={utforare}",
            )

    def ta_bort_organisationsmedlem(
        self, utforare: str, konto_id: str, organisation_id: str
    ) -> None:
        """Tar bort medlemskapet i organisationen (samma regler som `satt_organisationsroll`).
        Uttryckliga projektroller finns kvar; en medlem som kommer in via IdP-tenant eller
        intygad domän blir medlem igen vid nästa inloggning."""
        with self.db.transaktion() as c:
            niva = self._organisation_for_admin(c, utforare, organisation_id)
            nuvarande = self._organisationsroll_for(c, konto_id, organisation_id)
            if nuvarande is None:
                raise RegisterFel("Okänt medlemskap")
            if not self._far_tilldela(niva, nuvarande):
                raise BehorighetFel(
                    "Rollerna organisationsadmin och ägare tas bara bort av organisationens ägare"
                    " eller superadmin"
                )
            self._skydda_sista_agaren(c, niva, konto_id, organisation_id, nuvarande, None)
            c.execute(
                "DELETE FROM medlemskap WHERE konto_id = ? AND organisation_id = ?",
                (konto_id, organisation_id),
            )
            self.notera(
                c, "orgmedlem-borttagen", konto_id, f"organisation={organisation_id} av={utforare}"
            )

    def byt_organisationsnamn(self, utforare: str, organisation_id: str, namn: str) -> None:
        """Ändrar organisationens namn: ägare och superadmin."""
        ren = " ".join(namn.split())
        if not ren or len(ren) > 120:
            raise RegisterFel("Namnet ska vara 1 till 120 tecken")
        with self.db.transaktion() as c:
            niva = self._organisation_for_admin(c, utforare, organisation_id)
            if niva < _NIVA_AGARE:
                raise BehorighetFel("Organisationens namn ändras bara av ägaren")
            try:
                c.execute("UPDATE organisation SET namn = ? WHERE id = ?", (ren, organisation_id))
            except sqlite3.IntegrityError as exc:
                raise RegisterFel("Namnet används redan av en annan organisation") from exc
            self.notera(c, "organisation-namn-andrat", utforare, f"organisation={organisation_id}")

    # --- sammanfattning till /konto/jag -------------------------------------------------------

    def sammanfattning(self, konto_id: str) -> dict[str, object]:
        with self.db.transaktion() as c:
            org = c.execute(
                "SELECT m.organisation_id AS id, o.namn, m.roll, m.kalla, m.forvaltning_id,"
                " f.namn AS forvaltning FROM medlemskap m"
                " JOIN organisation o ON o.id = m.organisation_id"
                " LEFT JOIN forvaltning f ON f.id = m.forvaltning_id"
                " WHERE m.konto_id = ? ORDER BY o.namn, f.namn",
                (konto_id,),
            ).fetchall()
            proj = c.execute(
                "SELECT p.id, p.namn, b.roll FROM projektbehorighet b"
                " JOIN projekt p ON p.id = b.projekt_id WHERE b.konto_id = ? ORDER BY p.namn",
                (konto_id,),
            ).fetchall()
        return {
            "medlemskap": [
                {
                    "organisation_id": r["id"],
                    "organisation": r["namn"],
                    "forvaltning_id": r["forvaltning_id"],
                    "forvaltning": r["forvaltning"],
                    "roll": r["roll"],
                    "kalla": r["kalla"],
                }
                for r in org
            ],
            "projekt": [{"id": r["id"], "namn": r["namn"], "roll": r["roll"]} for r in proj],
        }
