# ruff: noqa: E501
"""Personliga MCP-token: `fl_mcp_` + 32 slumpbyte (base64url), bara sha256 sparas.

En token låter ett verktyg som inte kan logga in i en webbläsare (Claude Code, Codex, skript) använda
Flodens MCP-server på `/mcp` som kontot som skapade den. Reglerna:

- Token visas **en gång**, i svaret på skapandet. Databasen har bara sha256 av hela texten, så den
  går inte att läsa ut igen. Därefter syns bara namn, när den skapades, när den användes senast och
  de fyra sista tecknen (`slutar_pa`).
- Högst 5 aktiva token per konto. En token kan återkallas (av ägaren); den slutar gälla direkt.
- Ett spärrat konto nekas: kontrollen sker mot kontots status vid varje anrop, så en spärr gäller
  omedelbart även för redan skapade token.
- Token gäller bara `/mcp` (se `floden_mcp.http.KontoSkydd`), aldrig webbens API, och kan inte skapa
  eller återkalla token (de ändpunkterna kräver session och CSRF).
- Aldrig i loggar, händelser eller felsvar: loggar och händelser har konto-id och token-id.
- Ett misslyckat försök ger alltid samma svar (None); orsaken skiljs inte åt utåt.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from typing import Any

from konto.nycklar import datum_text
from konto.register import Register

logg = logging.getLogger("konto")

PREFIX = "fl_mcp_"
MAX_PER_KONTO = 5
SLUTAR_PA_TECKEN = 4
MAX_NAMN = 60
SENAST_ANVAND_UPPLOSNING_S = 60
_FORMAT = re.compile(r"^fl_mcp_[A-Za-z0-9_-]{43}$")  # 32 byte base64url utan utfyllnad = 43 tecken


class McpTokenFel(Exception):
    """Fel med kod och text för API-svaret. Texten innehåller aldrig en token."""

    def __init__(self, kod: str, text: str, status: int = 400) -> None:
        super().__init__(text)
        self.kod = kod
        self.text = text
        self.status = status


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ny_token() -> str:
    return PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def ser_ut_som_mcp_token(text: str) -> bool:
    return bool(_FORMAT.match(text))


class McpTokenForrad:
    """Skapa, lista, återkalla och kontrollera MCP-token i kontodatabasen."""

    def __init__(self, register: Register) -> None:
        self._register = register

    @staticmethod
    def _rad(r: Any) -> dict[str, Any]:
        senast = None if r["senast_anvand"] is None else int(r["senast_anvand"])
        return {
            "id": str(r["id"]),
            "namn": str(r["namn"]),
            "skapad": int(r["skapad"]),
            "skapad_datum": datum_text(int(r["skapad"])),
            "senast_anvand": senast,
            "senast_anvand_datum": datum_text(senast),
            "slutar_pa": str(r["slutar_pa"]),
        }

    def lista(self, konto_id: str) -> list[dict[str, Any]]:
        """Kontots aktiva token (äldst först). Aldrig token eller hash."""
        with self._register.db.lasning() as c:
            rader = c.execute(
                "SELECT id, namn, skapad, senast_anvand, slutar_pa FROM mcp_token"
                " WHERE konto_id = ? AND aterkallad IS NULL ORDER BY skapad, id",
                (konto_id,),
            ).fetchall()
        return [self._rad(r) for r in rader]

    @staticmethod
    def _rent_namn(namn: object) -> str:
        if not isinstance(namn, str):
            raise McpTokenFel("ogiltigt", "namn måste vara text")
        rent = " ".join(namn.split())
        if not rent or len(rent) > MAX_NAMN or not rent.isprintable():
            raise McpTokenFel("ogiltigt", f"namn måste vara 1 till {MAX_NAMN} synliga tecken")
        return rent

    def skapa(self, konto_id: str, namn: object) -> dict[str, Any]:
        """Skapar en token och returnerar den **en gång** (`token`) tillsammans med dess uppgifter."""
        rent = self._rent_namn(namn)
        token = ny_token()
        nu = self._register.nu()
        tid = secrets.token_hex(16)
        with self._register.db.transaktion() as c:
            konto = c.execute("SELECT sparrad FROM konto WHERE id = ?", (konto_id,)).fetchone()
            if konto is None:
                raise McpTokenFel("okant-konto", "Okänt konto", 404)
            if konto["sparrad"] is not None:
                raise McpTokenFel("sparrat", "Kontot är spärrat", 403)
            antal = c.execute(
                "SELECT COUNT(*) FROM mcp_token WHERE konto_id = ? AND aterkallad IS NULL",
                (konto_id,),
            ).fetchone()[0]
            if antal >= MAX_PER_KONTO:
                raise McpTokenFel(
                    "for-manga",
                    f"Högst {MAX_PER_KONTO} aktiva token per konto: återkalla en först",
                    409,
                )
            c.execute(
                "INSERT INTO mcp_token (id, konto_id, namn, token_hash, slutar_pa, skapad)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (tid, konto_id, rent, hash_token(token), token[-SLUTAR_PA_TECKEN:], nu),
            )
            self._register.notera(c, "mcp-token-skapad", konto_id, f"token={tid}")
        return {
            "id": tid,
            "namn": rent,
            "skapad": nu,
            "skapad_datum": datum_text(nu),
            "senast_anvand": None,
            "senast_anvand_datum": None,
            "slutar_pa": token[-SLUTAR_PA_TECKEN:],
            "token": token,
        }

    def aterkalla(self, konto_id: str, token_id: str) -> bool:
        """Återkallar kontots egen token. False om den inte finns (eller redan är återkallad)."""
        with self._register.db.transaktion() as c:
            n = c.execute(
                "UPDATE mcp_token SET aterkallad = ? WHERE id = ? AND konto_id = ?"
                " AND aterkallad IS NULL",
                (self._register.nu(), token_id, konto_id),
            ).rowcount
            if n:
                self._register.notera(c, "mcp-token-aterkallad", konto_id, f"token={token_id}")
        return bool(n)

    def verifiera(self, token: str) -> tuple[str, str] | None:
        """(konto-id, token-id) för en giltig token, annars None.

        Ogiltig form, okänd, återkallad och spärrat konto ger samma svar. Uppslaget sker på
        sha256 av token, så ingen jämförelse av hemligheten görs i klartext."""
        if not _FORMAT.match(token):
            return None
        with self._register.db.lasning() as c:
            rad = c.execute(
                "SELECT t.id, t.konto_id, t.senast_anvand FROM mcp_token t"
                " JOIN konto k ON k.id = t.konto_id"
                " WHERE t.token_hash = ? AND t.aterkallad IS NULL AND k.sparrad IS NULL",
                (hash_token(token),),
            ).fetchone()
        if rad is None:
            return None
        nu = self._register.nu()
        sist = rad["senast_anvand"]
        if sist is None or nu - int(sist) >= SENAST_ANVAND_UPPLOSNING_S:
            try:
                with self._register.db.transaktion() as c:
                    c.execute("UPDATE mcp_token SET senast_anvand = ? WHERE id = ?", (nu, rad["id"]))
            except Exception as exc:  # noqa: BLE001 - att stämpla tiden får aldrig fälla ett anrop
                logg.warning("mcp-token: senast_anvand kunde inte sparas fel=%s", type(exc).__name__)
        return str(rad["konto_id"]), str(rad["id"])
