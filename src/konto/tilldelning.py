"""Från en kontrollerad identitet till ett konto, organisation, förvaltning och inbjudningar.

Regler (BESLUT 2026-11-21 "Drift", docs/ARKITEKTUR-DRIFT.md 3c):

1. Konto: hitta via identitet (leverantör, tenant, subject). Annars, och bara för en betrodd
   leverantör med verifierad adress, via ett konto med samma verifierade adress eller ett av
   administratören förberett konto utan identitet. Entra-konton kopplas aldrig via e-post.
2. Nytt konto skapas bara om `nya_konton = "oppen"`, eller identiteten har en väg in: registrerad
   tenant, en domän som leverantören intygar och som är registrerad på en organisation, eller en
   öppen inbjudan till den verifierade adressen. Annars nekas inloggningen utan att något sparas.
3. Organisation: IdP-tenant (registrerad), eller intygad domän. Aldrig förvaltning eller projekt.
4. Förvaltning: bara via IdP-grupp (mappningsregel). Prövas om vid varje inloggning; manuella
   medlemskap rörs inte. Okända grupper (Entra-överflöd) lämnar medlemskapen oförändrade.
5. Projekt: bara inbjudan till verifierad adress. Medlemskap ger aldrig projektbehörighet.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from konto.konfig import KontoKonfig
from konto.leverantor import Identitet, Leverantor
from konto.register import PROJEKTROLLER, Register, normalisera_epost


class InloggningNekad(Exception):
    """Identiteten är kontrollerad men får inte in. `kod` är fast text utan personuppgifter."""

    def __init__(self, kod: str) -> None:
        super().__init__(kod)
        self.kod = kod


@dataclass(frozen=True)
class Inloggning:
    konto_id: str
    nytt_konto: bool


def _epost(identitet: Identitet) -> str | None:
    if identitet.epost is None:
        return None
    try:
        return normalisera_epost(identitet.epost)
    except Exception:  # noqa: BLE001 - en ogiltig adress behandlas som saknad
        return None


def _visningsnamn(identitet: Identitet, epost: str | None) -> str:
    namn = " ".join((identitet.namn or "").split())
    if not namn and epost:
        namn = epost.split("@")[0]
    return (namn or "Konto")[:120]


def _organisation(
    c: sqlite3.Connection, identitet: Identitet, lev: Leverantor, epost: str | None
) -> tuple[str, str] | None:
    """(organisation-id, källa) enligt tenant, annars intygad domän."""
    if lev.flertenant:
        rad = c.execute(
            "SELECT organisation_id FROM organisation_tenant WHERE leverantor = ? AND tenant = ?",
            (identitet.leverantor, identitet.tenant),
        ).fetchone()
        return (str(rad["organisation_id"]), "idp-tenant") if rad else None
    intygad = (
        identitet.doman is not None
        and lev.epost_betrodd
        and identitet.epost_verifierad
        and epost is not None
        and epost.endswith("@" + identitet.doman)
    )
    if intygad:
        rad = c.execute(
            "SELECT organisation_id FROM organisation_doman WHERE doman = ?", (identitet.doman,)
        ).fetchone()
        if rad:
            return str(rad["organisation_id"]), "doman"
    return None


def _oppna_inbjudningar(c: sqlite3.Connection, epost: str, nu: int) -> list[sqlite3.Row]:
    return c.execute(
        "SELECT * FROM projektinbjudan WHERE epost = ? AND anvand_tid IS NULL AND giltig_till > ?",
        (epost, nu),
    ).fetchall()


def _hitta_konto(
    c: sqlite3.Connection, identitet: Identitet, lev: Leverantor, epost: str | None
) -> sqlite3.Row | None:
    rad: sqlite3.Row | None = c.execute(
        "SELECT k.* FROM identitet i JOIN konto k ON k.id = i.konto_id"
        " WHERE i.leverantor = ? AND i.tenant = ? AND i.subject = ?",
        (identitet.leverantor, identitet.tenant, identitet.subject),
    ).fetchone()
    if rad or not (lev.epost_betrodd and identitet.epost_verifierad and epost):
        return rad
    # Samma verifierade adress, eller ett förberett konto (ingen identitet alls än).
    kandidat: sqlite3.Row | None = c.execute(
        "SELECT * FROM konto WHERE epost = ? AND (epost_verifierad = 1 OR NOT EXISTS"
        " (SELECT 1 FROM identitet WHERE konto_id = konto.id)) ORDER BY skapad LIMIT 1",
        (epost,),
    ).fetchone()
    return kandidat


def logga_in_identitet(
    register: Register, identitet: Identitet, lev: Leverantor, konfig: KontoKonfig
) -> Inloggning:
    """Hela steget efter att ID-token kontrollerats. Allt eller inget (en transaktion)."""
    nu = register.nu()
    epost = _epost(identitet)
    with register.db.transaktion() as c:
        org = _organisation(c, identitet, lev, epost)
        if lev.flertenant and org is None:
            register.notera(c, "inloggning-nekad", None, "orsak=okand-tenant")
            raise InloggningNekad("okand-tenant")
        betrodd_epost = lev.epost_betrodd and identitet.epost_verifierad and epost is not None
        inbjudningar = _oppna_inbjudningar(c, epost, nu) if betrodd_epost and epost else []

        rad = _hitta_konto(c, identitet, lev, epost)
        nytt = rad is None
        if rad is not None and rad["sparrad"] is not None:
            register.notera(c, "inloggning-nekad", rad["id"], "orsak=sparrad")
            raise InloggningNekad("sparrad")
        if rad is None:
            if konfig.nya_konton != "oppen" and org is None and not inbjudningar:
                register.notera(c, "inloggning-nekad", None, "orsak=ingen-inbjudan")
                raise InloggningNekad("ingen-inbjudan")
            konto_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO konto (id, epost, epost_verifierad, namn, skapad) VALUES (?,?,?,?,?)",
                (
                    konto_id,
                    epost,
                    1 if betrodd_epost else 0,
                    _visningsnamn(identitet, epost),
                    nu,
                ),
            )
            register.notera(c, "konto-skapat", konto_id, f"leverantor={identitet.leverantor}")
        else:
            konto_id = rad["id"]
            upptagen = (
                betrodd_epost
                and c.execute(
                    "SELECT 1 FROM konto WHERE epost = ? AND epost_verifierad = 1 AND id != ?",
                    (epost, konto_id),
                ).fetchone()
            )
            if betrodd_epost and not rad["epost_verifierad"] and not upptagen:
                c.execute(
                    "UPDATE konto SET epost = ?, epost_verifierad = 1 WHERE id = ?",
                    (epost, konto_id),
                )

        c.execute(
            "INSERT INTO identitet (id, konto_id, leverantor, subject, tenant, skapad)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT (leverantor, tenant, subject) DO NOTHING",
            (
                uuid.uuid4().hex,
                konto_id,
                identitet.leverantor,
                identitet.subject,
                identitet.tenant,
                nu,
            ),
        )
        c.execute("UPDATE konto SET senast_inloggad = ? WHERE id = ?", (nu, konto_id))
        c.execute(
            "UPDATE identitet SET senast_inloggad = ? WHERE leverantor = ? AND tenant = ?"
            " AND subject = ?",
            (nu, identitet.leverantor, identitet.tenant, identitet.subject),
        )
        if org is not None:
            _satt_organisation(register, c, konto_id, org[0], org[1], identitet, nu)
        if inbjudningar:
            _anvand_inbjudningar(register, c, konto_id, inbjudningar, nu)
        register.notera(c, "inloggning", konto_id, f"leverantor={identitet.leverantor}")
        return Inloggning(konto_id=konto_id, nytt_konto=nytt)


def _satt_organisation(
    register: Register,
    c: sqlite3.Connection,
    konto_id: str,
    organisation_id: str,
    kalla: str,
    identitet: Identitet,
    nu: int,
) -> None:
    """Organisationsmedlem (utan förvaltning) och, om grupper är kända, omprövad förvaltning."""
    c.execute(
        "INSERT OR IGNORE INTO medlemskap (konto_id, organisation_id, forvaltning_id, roll, kalla,"
        " skapad) VALUES (?, ?, NULL, 'medlem', ?, ?)",
        (konto_id, organisation_id, kalla, nu),
    )
    if identitet.grupper is None or kalla != "idp-tenant":
        return  # förvaltning bara via IdP-grupp; okända grupper ändrar ingenting
    regler = c.execute(
        "SELECT id, forvaltning_id, grupp FROM forvaltning_grupp WHERE organisation_id = ?",
        (organisation_id,),
    ).fetchall()
    onskade: dict[str, int] = {}
    for regel in regler:
        if regel["grupp"] in identitet.grupper:
            onskade.setdefault(regel["forvaltning_id"], int(regel["id"]))
    nuvarande = {
        r["forvaltning_id"]
        for r in c.execute(
            "SELECT forvaltning_id FROM medlemskap WHERE konto_id = ? AND organisation_id = ?"
            " AND kalla = 'idp-grupp'",
            (konto_id, organisation_id),
        )
    }
    for forv_id in sorted(nuvarande - set(onskade)):
        c.execute(
            "DELETE FROM medlemskap WHERE konto_id = ? AND organisation_id = ?"
            " AND forvaltning_id = ? AND kalla = 'idp-grupp'",
            (konto_id, organisation_id, forv_id),
        )
        register.notera(c, "forvaltning-borttagen", konto_id, f"forvaltning={forv_id}")
    for forv_id, regel_id in sorted(onskade.items()):
        if forv_id in nuvarande:
            continue
        lagt = c.execute(
            "INSERT OR IGNORE INTO medlemskap (konto_id, organisation_id, forvaltning_id, roll,"
            " kalla, skapad) VALUES (?, ?, ?, 'medlem', 'idp-grupp', ?)",
            (konto_id, organisation_id, forv_id, nu),
        ).rowcount
        if lagt:
            register.notera(
                c, "forvaltning-tillagd", konto_id, f"forvaltning={forv_id} regel={regel_id}"
            )


def _anvand_inbjudningar(
    register: Register,
    c: sqlite3.Connection,
    konto_id: str,
    inbjudningar: list[sqlite3.Row],
    nu: int,
) -> None:
    """Tar emot öppna inbjudningar till kontots verifierade adress; en högre roll behålls."""
    for inb in inbjudningar:
        befintlig = c.execute(
            "SELECT roll FROM projektbehorighet WHERE konto_id = ? AND projekt_id = ?",
            (konto_id, inb["projekt_id"]),
        ).fetchone()
        if befintlig is None or PROJEKTROLLER.index(befintlig["roll"]) < PROJEKTROLLER.index(
            inb["roll"]
        ):
            c.execute(
                "INSERT INTO projektbehorighet (konto_id, projekt_id, roll, kalla, beviljad_av,"
                " skapad) VALUES (?, ?, ?, 'inbjudan', ?, ?)"
                " ON CONFLICT (konto_id, projekt_id) DO UPDATE SET roll = excluded.roll,"
                " kalla = 'inbjudan', beviljad_av = excluded.beviljad_av",
                (konto_id, inb["projekt_id"], inb["roll"], inb["skapad_av"], nu),
            )
        c.execute(
            "UPDATE projektinbjudan SET anvand_av = ?, anvand_tid = ? WHERE id = ?",
            (konto_id, nu, inb["id"]),
        )
        register.notera(
            c, "inbjudan-mottagen", konto_id, f"inbjudan={inb['id']} projekt={inb['projekt_id']}"
        )
