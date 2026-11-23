# ruff: noqa: E501
"""OAuth 2.1 för MCP: lagret bakom `/oauth/*` (HTTP-delen finns i `oauthapi.py`).

Floden är sin egen auktoriseringsserver så att Claude-appen (och andra MCP-klienter) kan läggas till
som en egen koppling utan att Joel klistrar in en token. Inloggningen är den befintliga (Google);
här finns bara klienter, koder och token.

- **Klienter** registreras dynamiskt (RFC 7591), publika (`token_endpoint_auth_method=none`).
  `redirect_uri` ska vara https, eller `http://localhost[:port]/...`, och matchas sedan exakt.
- **Auktoriseringskod**: engångs, 60 s, bunden till klient, `redirect_uri` och PKCE-utmaning (S256,
  RFC 7636). Används en kod två gånger återkallas allt som den gav (RFC 6749 4.1.2).
- **Access-token** `fl_oat_...` gäller 1 timme, **refresh-token** `fl_ort_...` 30 dagar och roteras
  vid varje användning. Visas en redan använd refresh-token igen återkallas hela behörigheten
  (grant), eftersom den då kan vara stulen.
- Allt som är hemligt (kod, access, refresh) sparas bara som sha256. Loggar har konto-id och
  grant-id, aldrig kod eller token.
- En **grant** är kontots godkännande av en klient. Att återkalla den (eller klienten) gör att
  access- och refresh-token slutar gälla direkt, liksom ett spärrat konto.
- Token är bundna till resursen (RFC 8707): `<offentlig_url>/mcp`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from konto.nycklar import datum_text
from konto.register import Register

logg = logging.getLogger("konto")

PREFIX_ACCESS = "fl_oat_"
PREFIX_REFRESH = "fl_ort_"
PREFIX_KOD = "fl_oac_"
KOD_GILTIG_S = 60
ACCESS_GILTIG_S = 3600
REFRESH_GILTIG_S = 30 * 24 * 3600
MAX_REDIRECT_URIS = 5
MAX_URI_LANGD = 512
MAX_KLIENTNAMN = 60
MAX_KLIENTER = 2000
OVERBLIVNA_KLIENTER_S = 7 * 24 * 3600
SENAST_ANVAND_UPPLOSNING_S = 60
_TOKEN = re.compile(r"^fl_o(?:at|rt|ac)_[A-Za-z0-9_-]{43}$")
_VERIFIERARE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_UTMANING = re.compile(r"^[A-Za-z0-9_-]{43}$")
_ID = re.compile(r"^[0-9a-f]{32}$")


class OauthFel(Exception):
    """Fel med OAuth-felkod (`invalid_grant` ...) och en text utan hemligheter."""

    def __init__(self, kod: str, text: str, status: int = 400) -> None:
        super().__init__(text)
        self.kod = kod
        self.text = text
        self.status = status


def hash_hemlighet(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slump(prefix: str) -> str:
    return prefix + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def pkce_utmaning(verifierare: str) -> str:
    digest = hashlib.sha256(verifierare.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def utmaning_ok(utmaning: str) -> bool:
    return bool(_UTMANING.match(utmaning))


def ar_oauth_token(text: str) -> bool:
    return text.startswith(PREFIX_ACCESS) and bool(_TOKEN.match(text))


def redirect_uri_ok(uri: object) -> bool:
    """https med värdnamn, eller `http://localhost[:port]/...`. Aldrig fragment eller inloggning
    i adressen. `127.0.0.1` tillåts inte (beslut som Joel kan ändra, se STATUS)."""
    if not isinstance(uri, str) or not uri or len(uri) > MAX_URI_LANGD:
        return False
    if any(ord(t) < 0x21 or ord(t) == 0x7F for t in uri):
        return False
    try:
        delar = urlsplit(uri)
        _ = delar.port  # ValueError vid ogiltig port
    except ValueError:
        return False
    if delar.fragment or "#" in uri or delar.username or delar.password or not delar.hostname:
        return False
    if delar.scheme == "https":
        return True
    return delar.scheme == "http" and delar.hostname == "localhost"


def _klientnamn(namn: object) -> str:
    if not isinstance(namn, str):
        return "Okänd app"
    rent = " ".join(c for c in namn.split() if c)
    rent = "".join(c for c in rent if c.isprintable())[:MAX_KLIENTNAMN]
    return rent or "Okänd app"


class Hastighetsgrans:
    """Enkel glidande gräns per nyckel (klient-IP) i minnet. Kodens riktiga skydd är att
    hemligheterna är slumpmässiga; gränsen hindrar att databasen fylls och brute force."""

    def __init__(self, max_antal: int, fonster_s: float, klocka: Callable[[], float] = time.time):
        self.max_antal = max_antal
        self.fonster_s = fonster_s
        self._klocka = klocka
        self._tider: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def tillat(self, nyckel: str) -> bool:
        """Registrerar ett anrop; False om gränsen är nådd."""
        nu = self._klocka()
        with self._lock:
            if len(self._tider) > 5000:
                for k in [k for k, d in self._tider.items() if not d or nu - d[-1] > self.fonster_s]:
                    del self._tider[k]
            tider = self._tider.setdefault(nyckel, deque())
            while tider and nu - tider[0] > self.fonster_s:
                tider.popleft()
            if len(tider) >= self.max_antal:
                return False
            tider.append(nu)
            return True


class OauthForrad:
    """Klienter, koder, behörigheter (grants) och token i kontodatabasen."""

    def __init__(self, register: Register, resurs: str) -> None:
        self._register = register
        self.resurs = resurs

    # --- klienter -----------------------------------------------------------------------------

    def registrera(self, namn: object, redirect_uris: object) -> dict[str, Any]:
        if (
            not isinstance(redirect_uris, list)
            or not 1 <= len(redirect_uris) <= MAX_REDIRECT_URIS
            or not all(redirect_uri_ok(u) for u in redirect_uris)
            or len(set(redirect_uris)) != len(redirect_uris)
        ):
            raise OauthFel(
                "invalid_redirect_uri",
                "redirect_uris: 1-5 adresser, https eller http://localhost",
            )
        rent = _klientnamn(namn)
        nu = self._register.nu()
        kid = secrets.token_hex(16)
        with self._register.db.transaktion() as c:
            c.execute(
                "DELETE FROM oauth_klient WHERE skapad < ? AND NOT EXISTS"
                " (SELECT 1 FROM oauth_grant g WHERE g.klient_id = oauth_klient.id)",
                (nu - OVERBLIVNA_KLIENTER_S,),
            )
            antal = c.execute("SELECT COUNT(*) FROM oauth_klient").fetchone()[0]
            if antal >= MAX_KLIENTER:
                raise OauthFel("temporarily_unavailable", "För många registrerade klienter", 503)
            c.execute(
                "INSERT INTO oauth_klient (id, namn, redirect_uris, skapad) VALUES (?, ?, ?, ?)",
                (kid, rent, json.dumps(list(redirect_uris)), nu),
            )
        return {"id": kid, "namn": rent, "redirect_uris": list(redirect_uris), "skapad": nu}

    def klient(self, klient_id: str) -> dict[str, Any] | None:
        """Klienten om den finns och inte är återkallad."""
        if not _ID.match(klient_id):
            return None
        with self._register.db.lasning() as c:
            r = c.execute(
                "SELECT id, namn, redirect_uris FROM oauth_klient"
                " WHERE id = ? AND aterkallad IS NULL",
                (klient_id,),
            ).fetchone()
        if r is None:
            return None
        return {"id": r["id"], "namn": r["namn"], "redirect_uris": json.loads(r["redirect_uris"])}

    def aterkalla_klient(self, klient_id: str) -> bool:
        """Återkallar klienten och alla dess behörigheter: allt den fått slutar fungera."""
        nu = self._register.nu()
        with self._register.db.transaktion() as c:
            n = c.execute(
                "UPDATE oauth_klient SET aterkallad = ? WHERE id = ? AND aterkallad IS NULL",
                (nu, klient_id),
            ).rowcount
            c.execute(
                "UPDATE oauth_grant SET aterkallad = ? WHERE klient_id = ? AND aterkallad IS NULL",
                (nu, klient_id),
            )
            if n:
                self._register.notera(c, "oauth-klient-aterkallad", None, f"klient={klient_id}")
        return bool(n)

    # --- kod ----------------------------------------------------------------------------------

    def skapa_kod(
        self, klient_id: str, konto_id: str, redirect_uri: str, utmaning: str, resurs: str
    ) -> str:
        kod = _slump(PREFIX_KOD)
        nu = self._register.nu()
        with self._register.db.transaktion() as c:
            c.execute("DELETE FROM oauth_kod WHERE skapad < ?", (nu - 10 * KOD_GILTIG_S,))
            c.execute(
                "INSERT INTO oauth_kod (kod_hash, klient_id, konto_id, redirect_uri, challenge,"
                " resurs, skapad) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (hash_hemlighet(kod), klient_id, konto_id, redirect_uri, utmaning, resurs, nu),
            )
        return kod

    def byt_kod(
        self,
        kod: str,
        klient_id: str,
        redirect_uri: str,
        verifierare: str,
        resurs: str | None,
    ) -> dict[str, Any]:
        """Byter en kod mot access- och refresh-token. Koden förbrukas oavsett utfall."""
        fel: OauthFel | None = None
        nu = self._register.nu()
        svar: dict[str, Any] | None = None
        if not _TOKEN.match(kod) or not kod.startswith(PREFIX_KOD):
            raise OauthFel("invalid_grant", "Ogiltig kod")
        with self._register.db.transaktion() as c:
            r = c.execute(
                "SELECT * FROM oauth_kod WHERE kod_hash = ?", (hash_hemlighet(kod),)
            ).fetchone()
            if r is None:
                fel = OauthFel("invalid_grant", "Ogiltig eller förbrukad kod")
            elif r["grant_id"] is not None:  # återanvänd kod: allt den gav återkallas
                c.execute(
                    "UPDATE oauth_grant SET aterkallad = ? WHERE id = ? AND aterkallad IS NULL",
                    (nu, r["grant_id"]),
                )
                logg.warning("oauth kod återanvänd grant=%s", r["grant_id"])
                fel = OauthFel("invalid_grant", "Ogiltig eller förbrukad kod")
            else:
                c.execute(
                    "UPDATE oauth_kod SET grant_id = 'forbrukad' WHERE kod_hash = ?",
                    (r["kod_hash"],),
                )
                konto = c.execute(
                    "SELECT sparrad FROM konto WHERE id = ?", (r["konto_id"],)
                ).fetchone()
                klient = c.execute(
                    "SELECT 1 FROM oauth_klient WHERE id = ? AND aterkallad IS NULL",
                    (r["klient_id"],),
                ).fetchone()
                if (
                    nu - int(r["skapad"]) > KOD_GILTIG_S
                    or not hmac.compare_digest(r["klient_id"], klient_id)
                    or not hmac.compare_digest(r["redirect_uri"], redirect_uri)
                    or konto is None
                    or konto["sparrad"] is not None
                    or klient is None
                ):
                    fel = OauthFel("invalid_grant", "Ogiltig eller utgången kod")
                elif not _VERIFIERARE.match(verifierare) or not hmac.compare_digest(
                    pkce_utmaning(verifierare), r["challenge"]
                ):
                    fel = OauthFel("invalid_grant", "PKCE-kontrollen misslyckades")
                elif resurs is not None and resurs.rstrip("/") != r["resurs"]:
                    fel = OauthFel("invalid_target", "Fel resurs")
                else:
                    gid = secrets.token_hex(16)
                    c.execute(
                        "INSERT INTO oauth_grant (id, klient_id, konto_id, resurs, skapad)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (gid, r["klient_id"], r["konto_id"], r["resurs"], nu),
                    )
                    c.execute(
                        "UPDATE oauth_kod SET grant_id = ? WHERE kod_hash = ?",
                        (gid, r["kod_hash"]),
                    )
                    svar = self._ny_token(c, gid, nu)
                    self._register.notera(
                        c, "oauth-godkand", r["konto_id"], f"grant={gid} klient={r['klient_id']}"
                    )
        if fel is not None:
            raise fel
        assert svar is not None  # noqa: S101
        return svar

    def _ny_token(self, c: Any, grant_id: str, nu: int) -> dict[str, Any]:
        access = _slump(PREFIX_ACCESS)
        refresh = _slump(PREFIX_REFRESH)
        c.execute(
            "INSERT INTO oauth_token (id, grant_id, access_hash, access_utgar, refresh_hash,"
            " refresh_utgar, skapad) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                secrets.token_hex(16),
                grant_id,
                hash_hemlighet(access),
                nu + ACCESS_GILTIG_S,
                hash_hemlighet(refresh),
                nu + REFRESH_GILTIG_S,
                nu,
            ),
        )
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": ACCESS_GILTIG_S,
            "grant_id": grant_id,
        }

    # --- refresh ------------------------------------------------------------------------------

    def forny(self, refresh: str, klient_id: str, resurs: str | None) -> dict[str, Any]:
        """Roterar: ger ett nytt par och gör den gamla refresh-token oanvändbar. Visas en redan
        använd refresh-token igen återkallas hela behörigheten."""
        if not _TOKEN.match(refresh) or not refresh.startswith(PREFIX_REFRESH):
            raise OauthFel("invalid_grant", "Ogiltig refresh-token")
        nu = self._register.nu()
        fel: OauthFel | None = None
        svar: dict[str, Any] | None = None
        with self._register.db.transaktion() as c:
            r = c.execute(
                "SELECT t.id AS tid, t.grant_id, t.refresh_utgar, t.refresh_anvand,"
                " g.klient_id, g.konto_id, g.resurs, g.aterkallad AS g_aterkallad,"
                " k.aterkallad AS k_aterkallad, a.sparrad"
                " FROM oauth_token t JOIN oauth_grant g ON g.id = t.grant_id"
                " JOIN oauth_klient k ON k.id = g.klient_id JOIN konto a ON a.id = g.konto_id"
                " WHERE t.refresh_hash = ?",
                (hash_hemlighet(refresh),),
            ).fetchone()
            if r is None or not hmac.compare_digest(r["klient_id"], klient_id):
                fel = OauthFel("invalid_grant", "Ogiltig refresh-token")
            elif r["refresh_anvand"] is not None:
                c.execute(
                    "UPDATE oauth_grant SET aterkallad = ? WHERE id = ? AND aterkallad IS NULL",
                    (nu, r["grant_id"]),
                )
                logg.warning("oauth refresh återanvänd, grant återkallad grant=%s", r["grant_id"])
                fel = OauthFel("invalid_grant", "Ogiltig refresh-token")
            elif (
                r["g_aterkallad"] is not None
                or r["k_aterkallad"] is not None
                or r["sparrad"] is not None
                or int(r["refresh_utgar"]) <= nu
            ):
                fel = OauthFel("invalid_grant", "Ogiltig refresh-token")
            elif resurs is not None and resurs.rstrip("/") != r["resurs"]:
                fel = OauthFel("invalid_target", "Fel resurs")
            else:
                c.execute(
                    "UPDATE oauth_token SET refresh_anvand = ?, access_utgar = MIN(access_utgar, ?)"
                    " WHERE id = ?",
                    (nu, nu, r["tid"]),
                )
                c.execute(
                    "DELETE FROM oauth_token WHERE grant_id = ? AND refresh_utgar < ?",
                    (r["grant_id"], nu),
                )
                svar = self._ny_token(c, r["grant_id"], nu)
        if fel is not None:
            raise fel
        assert svar is not None  # noqa: S101
        return svar

    # --- bearer -------------------------------------------------------------------------------

    def verifiera(self, access: str) -> tuple[str, str] | None:
        """(konto-id, grant-id) för en giltig access-token, annars None (alla orsaker lika)."""
        if not ar_oauth_token(access):
            return None
        nu = self._register.nu()
        with self._register.db.lasning() as c:
            r = c.execute(
                "SELECT g.id, g.konto_id, g.senast_anvand FROM oauth_token t"
                " JOIN oauth_grant g ON g.id = t.grant_id"
                " JOIN oauth_klient k ON k.id = g.klient_id JOIN konto a ON a.id = g.konto_id"
                " WHERE t.access_hash = ? AND t.access_utgar > ? AND g.aterkallad IS NULL"
                " AND k.aterkallad IS NULL AND a.sparrad IS NULL",
                (hash_hemlighet(access), nu),
            ).fetchone()
        if r is None:
            return None
        sist = r["senast_anvand"]
        if sist is None or nu - int(sist) >= SENAST_ANVAND_UPPLOSNING_S:
            try:
                with self._register.db.transaktion() as c:
                    c.execute("UPDATE oauth_grant SET senast_anvand = ? WHERE id = ?", (nu, r["id"]))
            except Exception as exc:  # noqa: BLE001 - tidsstämpeln får aldrig fälla ett anrop
                logg.warning("oauth: senast_anvand kunde inte sparas fel=%s", type(exc).__name__)
        return str(r["konto_id"]), str(r["id"])

    # --- kontots egna anslutningar -------------------------------------------------------------

    def lista(self, konto_id: str) -> list[dict[str, Any]]:
        with self._register.db.lasning() as c:
            rader = c.execute(
                "SELECT g.id, k.namn, g.skapad, g.senast_anvand FROM oauth_grant g"
                " JOIN oauth_klient k ON k.id = g.klient_id"
                " WHERE g.konto_id = ? AND g.aterkallad IS NULL AND k.aterkallad IS NULL"
                " ORDER BY g.skapad, g.id",
                (konto_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "namn": r["namn"],
                "skapad": int(r["skapad"]),
                "skapad_datum": datum_text(int(r["skapad"])),
                "senast_anvand": None if r["senast_anvand"] is None else int(r["senast_anvand"]),
                "senast_anvand_datum": datum_text(
                    None if r["senast_anvand"] is None else int(r["senast_anvand"])
                ),
            }
            for r in rader
        ]

    def aterkalla_grant(self, konto_id: str, grant_id: str) -> bool:
        """Återkallar kontots egen anslutning. False om den inte finns eller redan är återkallad."""
        with self._register.db.transaktion() as c:
            n = c.execute(
                "UPDATE oauth_grant SET aterkallad = ? WHERE id = ? AND konto_id = ?"
                " AND aterkallad IS NULL",
                (self._register.nu(), grant_id, konto_id),
            ).rowcount
            if n:
                self._register.notera(c, "oauth-aterkallad", konto_id, f"grant={grant_id}")
        return bool(n)

    def aterkalla_med_token(self, token: str) -> None:
        """RFC 7009: återkallar behörigheten bakom en access- eller refresh-token. Tyst om
        token är okänd (svaret ska inte avslöja något)."""
        if not _TOKEN.match(token) or token.startswith(PREFIX_KOD):
            return
        kolumn = "access_hash" if token.startswith(PREFIX_ACCESS) else "refresh_hash"
        with self._register.db.transaktion() as c:
            c.execute(
                "UPDATE oauth_grant SET aterkallad = ? WHERE aterkallad IS NULL AND id IN"  # noqa: S608
                f" (SELECT grant_id FROM oauth_token WHERE {kolumn} = ?)",
                (self._register.nu(), hash_hemlighet(token)),
            )
