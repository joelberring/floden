"""Sessioner och CSRF-skydd.

Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af): `user-account.ts`
(`issueUserAccountSession`, `authorizeUserAccountSession`, `csrfMatches`) och
`organizer-account-cookies.ts`. Sessions-id och
hemlighet är slumpade (256 bitar), databasen lagrar bara sha256 av hemligheten, jämförelsen görs i
konstant tid (även när sessionen saknas). CSRF: dubbel inlämning (kaka + header) mot en hash som
hör till sessionen, så en kaka som en annan värd satt eller en annan sessions token duger inte.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from konto.konfig import KontoKonfig
from konto.register import Register

TOKEN_FORMAT = re.compile(r"^fs1\.([0-9a-f]{32})\.([A-Za-z0-9_-]{43})$")
SLUMP_FORMAT = re.compile(r"^[A-Za-z0-9_-]{43}$")
_TOMT_HASH = hashlib.sha256(b"\x00" * 32).digest()
CSRF_HEADER = "x-floden-csrf"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_av(text: str | None) -> bytes:
    """sha256 av en base64url-hemlighet; en fast attrapp om värdet har fel form (konstant tid)."""
    if text is None or not SLUMP_FORMAT.match(text):
        return _TOMT_HASH
    data = base64.urlsafe_b64decode(text + "=")
    # Sista tecknet i base64 kan bära oanvända bitar: kräv kanonisk kodning, annars
    # godtas en ändrad kaka som avkodas till samma bytes.
    if _b64(data) != text:
        return _TOMT_HASH
    return hashlib.sha256(data).digest()


class Utfall(Enum):
    OK = "ok"
    EJ_INLOGGAD = "ej-inloggad"
    CSRF = "csrf"


@dataclass(frozen=True)
class Inloggad:
    """Den inloggade personen. Innehåller medvetet inga anspråk från leverantören."""

    konto_id: str
    namn: str
    epost: str | None
    superadmin: bool
    session_id: str
    utgar: int


@dataclass(frozen=True)
class SessionsKontroll:
    utfall: Utfall
    inloggad: Inloggad | None = None


@dataclass(frozen=True)
class NySession:
    token: str
    csrf: str
    utgar: int


@dataclass(frozen=True)
class KakNamn:
    session: str
    csrf: str
    inloggning: str


def kaknamn(secure: bool) -> KakNamn:
    """`__Host-` när kakan är Secure (Path=/, ingen Domain), annars enkla namn (localhost)."""
    if secure:
        return KakNamn("__Host-floden-session", "__Host-floden-csrf", "__Host-floden-inloggning")
    return KakNamn("floden_session", "floden_csrf", "floden_inloggning")


def las_kakor(rubrik: str | None) -> dict[str, str]:
    """Kakor ur `Cookie`-huvudet. Ett namn som förekommer två gånger tas bort (otydligt)."""
    resultat: dict[str, str] = {}
    dubbletter: set[str] = set()
    if not rubrik or len(rubrik) > 4096:
        return resultat
    for del_ in rubrik.split(";"):
        namn, sep, varde = del_.partition("=")
        namn = namn.strip()
        if not sep or not namn:
            continue
        if namn in resultat:
            dubbletter.add(namn)
        resultat[namn] = varde.strip()
    for namn in dubbletter:
        del resultat[namn]
    return resultat


def satt_kaka(
    namn: str, varde: str, *, secure: bool, http_only: bool, max_alder_s: int | None
) -> tuple[str, str]:
    """`Set-Cookie`: Path=/, SameSite=Lax, HttpOnly och Secure enligt konfigurationen."""
    delar = [f"{namn}={varde}", "Path=/", "SameSite=Lax"]
    if http_only:
        delar.append("HttpOnly")
    if secure:
        delar.append("Secure")
    if max_alder_s is not None:
        delar.append(f"Max-Age={max_alder_s}")
    return ("Set-Cookie", "; ".join(delar))


class Sessioner:
    def __init__(self, register: Register, konfig: KontoKonfig) -> None:
        self._register = register
        self._konfig = konfig

    def skapa(self, konto_id: str) -> NySession:
        nu = self._register.nu()
        sid = uuid.uuid4().hex
        hemlighet = secrets.token_bytes(32)
        csrf = secrets.token_bytes(32)
        utgar = nu + int(self._konfig.sessionstimmar * 3600)
        with self._register.db.transaktion() as c:
            c.execute("DELETE FROM session WHERE utgar < ?", (nu - 86400,))
            c.execute(
                "INSERT INTO session (id, konto_id, hemlighet_hash, csrf_hash, utfardad, utgar)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (sid, konto_id, sha256_hex(hemlighet), sha256_hex(csrf), nu, utgar),
            )
        return NySession(token=f"fs1.{sid}.{_b64(hemlighet)}", csrf=_b64(csrf), utgar=utgar)

    def kontrollera(
        self,
        token: str | None,
        *,
        kraver_csrf: bool = False,
        csrf_kaka: str | None = None,
        csrf_header: str | None = None,
    ) -> SessionsKontroll:
        traff = TOKEN_FORMAT.match(token) if token else None
        sid = traff.group(1) if traff else "0" * 32
        hemlighet = traff.group(2) if traff else None
        nu = self._register.nu()
        with self._register.db.lasning() as c:
            rad = c.execute(
                "SELECT s.*, k.namn, k.epost, k.superadmin, k.sparrad FROM session s"
                " JOIN konto k ON k.id = s.konto_id WHERE s.id = ?",
                (sid,),
            ).fetchone()
        lagrad = bytes.fromhex(rad["hemlighet_hash"]) if rad else _TOMT_HASH
        lika = hmac.compare_digest(_hash_av(hemlighet), lagrad)
        if (
            rad is None
            or not lika
            or traff is None
            or rad["aterkallad"] is not None
            or rad["sparrad"] is not None
            or rad["utgar"] <= nu
            or rad["utfardad"] > nu + 60
        ):
            return SessionsKontroll(Utfall.EJ_INLOGGAD)
        if kraver_csrf:
            forvantat = bytes.fromhex(rad["csrf_hash"])
            ok = (
                hmac.compare_digest(_hash_av(csrf_kaka), forvantat)
                & hmac.compare_digest(_hash_av(csrf_header), forvantat)
                & (csrf_kaka is not None and csrf_kaka == csrf_header)
            )
            if not ok:
                return SessionsKontroll(Utfall.CSRF)
        return SessionsKontroll(
            Utfall.OK,
            Inloggad(
                konto_id=rad["konto_id"],
                namn=rad["namn"],
                epost=rad["epost"],
                superadmin=bool(rad["superadmin"]),
                session_id=rad["id"],
                utgar=int(rad["utgar"]),
            ),
        )

    def aterkalla(self, session_id: str) -> None:
        with self._register.db.transaktion() as c:
            c.execute(
                "UPDATE session SET aterkallad = ? WHERE id = ? AND aterkallad IS NULL",
                (self._register.nu(), session_id),
            )

    def aterkalla_alla(self, konto_id: str) -> None:
        with self._register.db.transaktion() as c:
            c.execute(
                "UPDATE session SET aterkallad = ? WHERE konto_id = ? AND aterkallad IS NULL",
                (self._register.nu(), konto_id),
            )

    def sparra_konto(self, konto_id: str) -> None:
        """Spärrar kontot (ingen inloggning, inga giltiga sessioner)."""
        with self._register.db.transaktion() as c:
            c.execute(
                "UPDATE konto SET sparrad = ? WHERE id = ? AND sparrad IS NULL",
                (self._register.nu(), konto_id),
            )
            self._register.notera(c, "konto-sparrat", konto_id)


def csrf_fran_headers(headers: Mapping[str, str]) -> str | None:
    return headers.get(CSRF_HEADER)
