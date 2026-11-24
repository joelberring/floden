"""Användarens egna API-nycklar, krypterade i kontodatabasen (en rad per konto och typ).

Typer: `lantmateriet` (Lantmäteriets STAC/nedladdning: klient-id, hemlighet och metod, samma som
`lantmateriet.nycklar`; svaret har också tabellen över produkter att beställa på Geotorget ur
`data/lantmateriet/produkter.toml`; metoden är förvald till `basic`, men en redan sparad nyckel
behåller sin metod) och `anthropic` (för framtida AI-körningar på servern; ingen kod anropar
den än). Så här skyddas nycklarna:

- Krypteringen är Fernet (`cryptography`, följer med PyJWT[crypto]). Hemligheten kommer ur
  miljövariabeln `FLODEN_NYCKELHEMLIGHET` (flera nycklar skiljs med komma; den första krypterar,
  alla kan dekryptera, så att hemligheten går att rotera). Saknas den, eller är den ogiltig, är
  funktionen **inte påslagen**: ingenting sparas, och ingenting sparas någonsin okrypterat.
- En nyckel visas aldrig igen efter att den sparats. Databasen har bara chiffertexten och de fyra
  sista tecknen av fältet som identifierar nyckeln (`slutar_pa`), så att användaren känner igen den.
- Chiffertexten bär konto-id och typ; en rad som flyttats till ett annat konto eller en annan typ
  går inte att läsa.
- `las` ger klartext och får bara anropas av serverkod som ska använda nyckeln (Lantmäteri-
  hämtningen, framtida AI-körningar). Det finns ingen väg till den från HTTP-API:t eller MCP.
- Nycklar skrivs aldrig i loggar, händelser, felmeddelanden, körlogg eller `run.json`: alla texter
  här nämner fält och typ, aldrig värden.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from konto.register import Register

logg = logging.getLogger("konto")

HEMLIGHET_MILJOVARIABEL = "FLODEN_NYCKELHEMLIGHET"
SLUTAR_PA_TECKEN = 4
MIN_LANGD = 8
MAX_LANGD = 512
_TILLATET = re.compile(r"^[\x21-\x7e]+$")  # synlig ASCII, inga blanksteg


class NyckelForradFel(Exception):
    """Fel med en kod (för API-svaret) och en text som aldrig innehåller ett nyckelvärde."""

    def __init__(self, kod: str, text: str, status: int = 400) -> None:
        super().__init__(text)
        self.kod = kod
        self.text = text
        self.status = status


@dataclass(frozen=True)
class NyckelFalt:
    id: str
    namn: str
    krav: bool = True
    val: tuple[str, ...] = ()  # tillåtna värden, tom = fri text
    standard: str | None = None
    val_etiketter: tuple[tuple[str, str], ...] = ()  # (värde, text användaren ser) för `val`


@dataclass(frozen=True)
class NyckelTyp:
    id: str
    namn: str
    beskrivning: str
    falt: tuple[NyckelFalt, ...]
    identifierande: str  # fältet vars fyra sista tecken visas efter att nyckeln sparats


NYCKELTYPER: dict[str, NyckelTyp] = {
    "lantmateriet": NyckelTyp(
        id="lantmateriet",
        namn="Lantmäteriet (STAC och nedladdning)",
        beskrivning=(
            "Välj hur du loggar in hos Lantmäteriet. Det vanligaste för en privatperson är "
            "Geotorgets användarnamn och lösenord. API-portalens nyckel och hemlighet "
            "(apimanager.lantmateriet.se) är ett annat sätt. Fel val ger 401 från Lantmäteriet. "
            "Floden använder bara avgiftsfria produkter, och varje produkt i tabellen nedan "
            "måste vara beställd på Geotorget för kontot."
        ),
        falt=(
            NyckelFalt(
                "metod",
                "Sätt att logga in",
                krav=False,
                val=("basic", "oauth2"),
                standard="basic",
                val_etiketter=(
                    ("basic", "Geotorgets användarnamn och lösenord (privatperson)"),
                    ("oauth2", "API-portalens nyckel och hemlighet"),
                ),
            ),
            NyckelFalt("klient_id", "Användarnamn (Geotorget) eller nyckel (API-portalen)"),
            NyckelFalt("klient_hemlighet", "Lösenord (Geotorget) eller hemlighet (API-portalen)"),
        ),
        # Användarnamnet eller nyckeln visas som "slutar på ...", inte lösenordet (nya sparningar).
        identifierande="klient_id",
    ),
    "anthropic": NyckelTyp(
        id="anthropic",
        namn="Anthropic (Claude)",
        beskrivning=(
            "API-nyckel till Claude, sparad för framtida AI-körningar på servern. Ingen kod "
            "använder den än."
        ),
        falt=(NyckelFalt("nyckel", "API-nyckel"),),
        identifierande="nyckel",
    ),
}


def datum_text(tid: int | None) -> str | None:
    """`ÅÅÅÅ-MM-DD` (UTC) för en tidpunkt i sekunder."""
    if tid is None:
        return None
    return datetime.fromtimestamp(tid, tz=UTC).strftime("%Y-%m-%d")


def _bygg_fernet(hemlighet: str | None) -> tuple[MultiFernet | None, str | None]:
    """(kryptering, orsak till att den saknas). Orsaken nämner aldrig hemligheten."""
    if not hemlighet or not hemlighet.strip():
        return None, "saknas"
    try:
        nycklar = [Fernet(d.strip()) for d in hemlighet.split(",") if d.strip()]
    except (ValueError, TypeError):
        return None, "ogiltig"
    if not nycklar:
        return None, "saknas"
    return MultiFernet(nycklar), None


class NyckelForrad:
    """Läs- och skrivfunktioner för kontots nycklar mot kontodatabasen."""

    def __init__(
        self,
        register: Register,
        hemlighet: str | None = None,
        klocka: Callable[[], float] = time.time,
        *,
        miljo: Mapping[str, str] | None = None,
    ) -> None:
        self._register = register
        self._klocka = klocka
        if hemlighet is None:
            hemlighet = (os.environ if miljo is None else miljo).get(HEMLIGHET_MILJOVARIABEL)
        self._fernet, self._orsak = _bygg_fernet(hemlighet)
        if self._orsak == "ogiltig":
            logg.error("%s är ogiltig: nycklar kan inte sparas", HEMLIGHET_MILJOVARIABEL)

    @property
    def paslagen(self) -> bool:
        return self._fernet is not None

    @property
    def orsak(self) -> str | None:
        """`saknas` eller `ogiltig` när funktionen är avstängd, annars None."""
        return self._orsak

    def avstangd_text(self) -> str:
        if self._orsak == "ogiltig":
            return (
                f"Nyckelhanteringen är inte påslagen på servern: {HEMLIGHET_MILJOVARIABEL} är "
                "ogiltig. Be den som driver servern rätta den (docs/DRIFT.md)."
            )
        return (
            "Nyckelhanteringen är inte påslagen på servern: den som driver servern har inte "
            f"satt {HEMLIGHET_MILJOVARIABEL} (docs/DRIFT.md). Inga nycklar kan sparas."
        )

    # --- uppslag och status (ingen dekryptering) ---------------------------------------------

    @staticmethod
    def typ(typ_id: str) -> NyckelTyp:
        t = NYCKELTYPER.get(typ_id)
        if t is None:
            raise NyckelForradFel("okand-typ", "Okänd nyckeltyp", 404)
        return t

    def _status(self, t: NyckelTyp, rad: Any) -> dict[str, Any]:
        sparad = int(rad["sparad"]) if rad else None
        ut: dict[str, Any] = {
            "typ": t.id,
            "namn": t.namn,
            "beskrivning": t.beskrivning,
            "falt": [
                {
                    "id": f.id,
                    "namn": f.namn,
                    "krav": f.krav,
                    "val": list(f.val),
                    "val_etiketter": dict(f.val_etiketter),
                    "standard": f.standard,
                }
                for f in t.falt
            ],
            "sparad": sparad,
            "sparad_datum": datum_text(sparad),
            "slutar_pa": str(rad["slutar_pa"]) if rad else None,
        }
        if t.id == "lantmateriet":
            from lantmateriet.produkter import BESTALLNING_TID_TEXT, GEOTORGET_URL, tabell_for_api

            ut["produkter"] = tabell_for_api()
            ut["bestallning_text"] = BESTALLNING_TID_TEXT
            ut["geotorget_url"] = GEOTORGET_URL
        return ut

    def status(self, konto_id: str, typ_id: str) -> dict[str, Any]:
        t = self.typ(typ_id)
        with self._register.db.lasning() as c:
            rad = c.execute(
                "SELECT sparad, slutar_pa FROM konto_nyckel WHERE konto_id = ? AND typ = ?",
                (konto_id, t.id),
            ).fetchone()
        return self._status(t, rad)

    def oversikt(self, konto_id: str) -> dict[str, Any]:
        with self._register.db.lasning() as c:
            rader = {
                r["typ"]: r
                for r in c.execute(
                    "SELECT typ, sparad, slutar_pa FROM konto_nyckel WHERE konto_id = ?",
                    (konto_id,),
                ).fetchall()
            }
        return {
            "paslagen": self.paslagen,
            "orsak": self._orsak,
            "text": None if self.paslagen else self.avstangd_text(),
            "typer": [self._status(t, rader.get(t.id)) for t in NYCKELTYPER.values()],
        }

    # --- skriva ------------------------------------------------------------------------------

    @staticmethod
    def _kontrollera(t: NyckelTyp, indata: Mapping[str, Any]) -> dict[str, str]:
        """Rensar och validerar fälten. Felen nämner bara fältets id, aldrig värdet."""
        kanda = {f.id for f in t.falt}
        if any(k not in kanda for k in indata):
            # Namnet på ett okänt fält ekas inte: det kommer från användaren och kan vara en nyckel.
            raise NyckelForradFel(
                "okant-falt", f"Okänt fält. Tillåtna fält: {', '.join(sorted(kanda))}"
            )
        ut: dict[str, str] = {}
        for f in t.falt:
            varde = indata.get(f.id)
            if varde is None or (isinstance(varde, str) and not varde.strip()):
                if f.standard is not None:
                    ut[f.id] = f.standard
                    continue
                if f.krav:
                    raise NyckelForradFel("saknas", f"Fältet {f.id} saknas")
                continue
            if not isinstance(varde, str):
                raise NyckelForradFel("ogiltigt", f"Fältet {f.id} måste vara text")
            rent = varde.strip()
            if f.val:
                if rent.lower() not in f.val:
                    raise NyckelForradFel(
                        "ogiltigt", f"Fältet {f.id} måste vara {' eller '.join(f.val)}"
                    )
                ut[f.id] = rent.lower()
                continue
            if not MIN_LANGD <= len(rent) <= MAX_LANGD:
                raise NyckelForradFel(
                    "ogiltigt", f"Fältet {f.id} måste vara {MIN_LANGD} till {MAX_LANGD} tecken"
                )
            if not _TILLATET.match(rent):
                raise NyckelForradFel(
                    "ogiltigt", f"Fältet {f.id} har tecken som inte är tillåtna (inga blanksteg)"
                )
            ut[f.id] = rent
        return ut

    def spara(self, konto_id: str, typ_id: str, indata: Mapping[str, Any]) -> dict[str, Any]:
        """Krypterar och sparar (ersätter en tidigare nyckel av samma typ). Svaret har aldrig
        nyckeln, bara datum och de fyra sista tecknen."""
        t = self.typ(typ_id)
        if self._fernet is None:
            raise NyckelForradFel("nycklar-av", self.avstangd_text(), 503)
        varden = self._kontrollera(t, indata)
        klartext = json.dumps(
            {"v": 1, "konto": konto_id, "typ": t.id, "varden": varden},
            separators=(",", ":"),
        ).encode("utf-8")
        krypterad = self._fernet.encrypt(klartext).decode("ascii")
        slutar_pa = varden[t.identifierande][-SLUTAR_PA_TECKEN:]
        nu = int(self._klocka())
        with self._register.db.transaktion() as c:
            if c.execute("SELECT 1 FROM konto WHERE id = ?", (konto_id,)).fetchone() is None:
                raise NyckelForradFel("okant-konto", "Okänt konto", 404)
            c.execute(
                "INSERT INTO konto_nyckel (konto_id, typ, krypterad, slutar_pa, sparad)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (konto_id, typ) DO UPDATE SET"
                " krypterad = excluded.krypterad, slutar_pa = excluded.slutar_pa,"
                " sparad = excluded.sparad",
                (konto_id, t.id, krypterad, slutar_pa, nu),
            )
            self._register.notera(c, "nyckel-sparad", konto_id, f"typ={t.id}")
        return self.status(konto_id, t.id)

    def ta_bort(self, konto_id: str, typ_id: str) -> bool:
        """Tar bort nyckeln. Behöver ingen hemlighet, så den går att ta bort även när
        funktionen är avstängd. Returnerar om en nyckel fanns."""
        t = self.typ(typ_id)
        with self._register.db.transaktion() as c:
            n = c.execute(
                "DELETE FROM konto_nyckel WHERE konto_id = ? AND typ = ?", (konto_id, t.id)
            ).rowcount
            if n:
                self._register.notera(c, "nyckel-borttagen", konto_id, f"typ={t.id}")
        return bool(n)

    # --- bara för serverkod som ska använda nyckeln ---------------------------------------------

    def las(self, konto_id: str, typ_id: str) -> dict[str, str] | None:
        """Klartexten (fältnamn till värde), eller None om kontot inte har någon nyckel av typen.

        **Får aldrig nå HTTP-API:t, MCP, loggar eller filer.** Är funktionen avstängd, eller går
        raden inte att dekryptera (fel hemlighet, raden flyttad), är det ett fel, inte None."""
        t = self.typ(typ_id)
        if self._fernet is None:
            raise NyckelForradFel("nycklar-av", self.avstangd_text(), 503)
        with self._register.db.lasning() as c:
            rad = c.execute(
                "SELECT krypterad FROM konto_nyckel WHERE konto_id = ? AND typ = ?",
                (konto_id, t.id),
            ).fetchone()
        if rad is None:
            return None
        try:
            data = json.loads(self._fernet.decrypt(str(rad["krypterad"]).encode("ascii")))
        except (InvalidToken, ValueError) as exc:
            raise NyckelForradFel(
                "kan-inte-lasas",
                "Den sparade nyckeln går inte att läsa (hemligheten har bytts?). Lägg in den igen.",
                409,
            ) from exc
        if (
            not isinstance(data, dict)
            or data.get("konto") != konto_id
            or data.get("typ") != t.id
            or not isinstance(data.get("varden"), dict)
        ):
            raise NyckelForradFel("kan-inte-lasas", "Den sparade nyckeln går inte att läsa.", 409)
        return {str(k): str(v) for k, v in data["varden"].items()}
