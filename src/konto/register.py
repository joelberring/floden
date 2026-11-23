"""Konton, organisationer, förvaltningar, medlemskap, projekt och inbjudningar.

Modellen och dess regler (docs/ARKITEKTUR-DRIFT.md 3a-3c, BESLUT 2026-11-21 "Drift"):

- IdP-tenant ger organisation. IdP-grupp ger förvaltning. E-postdomän ger bara organisation och
  bara när leverantören intygar domänen (Googles `hd`).
- Projektbehörighet ges aldrig automatiskt: bara genom en inbjudan som tas emot med en verifierad
  e-postadress, eller genom en uttrycklig tilldelning av en organisationsadmin/superadmin.
  Medlemskap i organisation eller förvaltning, och rollen superadmin, ger ingen läsrätt till data.
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

from konto.databas import Databas

logg = logging.getLogger("konto")

PROJEKTROLLER = ("lasare", "utredare")  # stigande behörighet
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

    def skapa_organisation(self, namn: str) -> str:
        org_id = _ny_id()
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO organisation (id, namn, skapad) VALUES (?, ?, ?)",
                (org_id, namn.strip(), self.nu()),
            )
            self.notera(c, "organisation-skapad", None, f"organisation={org_id}")
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
        """Manuellt medlemskap på organisationsnivå med rollen organisationsadmin."""
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO medlemskap (konto_id, organisation_id, forvaltning_id, roll, kalla,"
                " skapad) VALUES (?, ?, NULL, 'organisationsadmin', 'manuell', ?)"
                " ON CONFLICT (konto_id, organisation_id, COALESCE(forvaltning_id, ''))"
                " DO UPDATE SET roll = 'organisationsadmin', kalla = 'manuell'",
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
        self, organisation_id: str, namn: str, forvaltning_id: str | None = None
    ) -> str:
        projekt_id = _ny_id()
        with self.db.transaktion() as c:
            c.execute(
                "INSERT INTO projekt (id, organisation_id, forvaltning_id, namn, skapad)"
                " VALUES (?, ?, ?, ?, ?)",
                (projekt_id, organisation_id, forvaltning_id, namn.strip(), self.nu()),
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
            konto = c.execute(
                "SELECT sparrad FROM konto WHERE id = ?", (konto_id,)
            ).fetchone()
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
                    "INSERT INTO projekt (id, organisation_id, forvaltning_id, namn, skapad)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (projekt_id, organisation_id, forvaltning_id, namn.strip(), self.nu()),
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
            self.notera(
                c, "projektroll-beviljad", konto_id, f"projekt={projekt_id} roll=utredare"
            )
        return projekt_id, nytt

    # --- behörighet -------------------------------------------------------------------------

    def _far_administrera(self, c: sqlite3.Connection, utforare: str, organisation_id: str) -> bool:
        rad = c.execute(
            "SELECT superadmin, sparrad FROM konto WHERE id = ?", (utforare,)
        ).fetchone()
        if not rad or rad["sparrad"] is not None:
            return False
        if rad["superadmin"]:
            return True
        return (
            c.execute(
                "SELECT 1 FROM medlemskap WHERE konto_id = ? AND organisation_id = ?"
                " AND roll = 'organisationsadmin'",
                (utforare, organisation_id),
            ).fetchone()
            is not None
        )

    def projektroll(self, konto_id: str, projekt_id: str) -> str | None:
        """Kontots uttryckliga roll i projektet, annars None. Inget annat ger projektdata."""
        with self.db.transaktion() as c:
            rad = c.execute(
                "SELECT p.roll FROM projektbehorighet p JOIN konto k ON k.id = p.konto_id"
                " WHERE p.konto_id = ? AND p.projekt_id = ? AND k.sparrad IS NULL",
                (konto_id, projekt_id),
            ).fetchone()
        return str(rad["roll"]) if rad else None

    def har_projektroll(self, konto_id: str, projekt_id: str, minst: str = "lasare") -> bool:
        roll = self.projektroll(konto_id, projekt_id)
        return roll is not None and _roll_niva(roll) >= _roll_niva(minst)

    def ar_superadmin(self, konto_id: str) -> bool:
        """Är kontot en icke spärrad superadmin? (Ger ingen projektdata, se `har_projektroll`.)"""
        with self.db.transaktion() as c:
            rad = c.execute(
                "SELECT superadmin FROM konto WHERE id = ? AND sparrad IS NULL", (konto_id,)
            ).fetchone()
        return bool(rad and rad["superadmin"])

    def ge_projektroll(self, utforare: str, konto_id: str, projekt_id: str, roll: str) -> None:
        """Uttrycklig tilldelning (organisationsadmin i projektets organisation, superadmin)."""
        _roll_niva(roll)
        with self.db.transaktion() as c:
            org = c.execute(
                "SELECT organisation_id FROM projekt WHERE id = ?", (projekt_id,)
            ).fetchone()
            if not org:
                raise RegisterFel("Okänt projekt")
            if not self._far_administrera(c, utforare, org["organisation_id"]):
                raise BehorighetFel("Kräver organisationsadmin")
            c.execute(
                "INSERT INTO projektbehorighet (konto_id, projekt_id, roll, kalla, beviljad_av,"
                " skapad) VALUES (?, ?, ?, 'manuell', ?, ?)"
                " ON CONFLICT (konto_id, projekt_id) DO UPDATE SET roll = excluded.roll,"
                " kalla = 'manuell', beviljad_av = excluded.beviljad_av",
                (konto_id, projekt_id, roll, utforare, self.nu()),
            )
            self.notera(c, "projektroll-beviljad", konto_id, f"projekt={projekt_id} roll={roll}")

    def aterkalla_projektroll(self, utforare: str, konto_id: str, projekt_id: str) -> None:
        with self.db.transaktion() as c:
            org = c.execute(
                "SELECT organisation_id FROM projekt WHERE id = ?", (projekt_id,)
            ).fetchone()
            if not org or not self._far_administrera(c, utforare, org["organisation_id"]):
                raise BehorighetFel("Kräver organisationsadmin")
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
                "SELECT organisation_id FROM projekt WHERE id = ?", (projekt_id,)
            ).fetchone()
            if not org:
                raise RegisterFel("Okänt projekt")
            if not self._far_administrera(c, utforare, org["organisation_id"]):
                raise BehorighetFel("Kräver organisationsadmin")
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
                    "SELECT 1 FROM medlemskap WHERE konto_id = ? AND roll = 'organisationsadmin'",
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
        if not self._far_administrera(c, utforare, org["organisation_id"]):
            raise BehorighetFel("Kräver organisationsadmin")
        return str(org["organisation_id"])

    def admin_projekt(self, utforare: str) -> list[dict[str, object]]:
        """Projekten utföraren får administrera: alla för superadmin, annars organisationernas."""
        if not self.ar_admin(utforare):
            raise BehorighetFel("Kräver organisationsadmin")
        with self.db.lasning() as c:
            rader = c.execute(
                "SELECT p.id, p.namn, p.organisation_id, o.namn AS organisation,"
                " (SELECT COUNT(*) FROM projektbehorighet b"
                "  WHERE b.projekt_id = p.id) AS medlemmar,"
                " (SELECT COUNT(*) FROM projektinbjudan i WHERE i.projekt_id = p.id"
                "  AND i.anvand_av IS NULL AND i.giltig_till > ?) AS oppna_inbjudningar"
                " FROM projekt p JOIN organisation o ON o.id = p.organisation_id"
                " WHERE (SELECT superadmin FROM konto WHERE id = ?) = 1 OR p.organisation_id IN"
                "  (SELECT organisation_id FROM medlemskap WHERE konto_id = ?"
                "   AND roll = 'organisationsadmin')"
                " ORDER BY o.namn, p.namn",
                (self.nu(), utforare, utforare),
            ).fetchall()
        return [dict(r) for r in rader]

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
                    " AND roll = 'organisationsadmin'"
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
                    "SELECT 1 FROM medlemskap WHERE konto_id = ? AND roll = 'organisationsadmin'"
                    " AND organisation_id IN (" + egna + ")",
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
