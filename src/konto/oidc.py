"""OIDC-kodflöde med PKCE (S256), `state`, `nonce` och kontroll av ID-token mot leverantörens JWKS.

Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af): `google-oidc.ts` (PKCE, state, nonce,
kodutbyte, kontroll av `iss`/`aud`/`azp`/`exp`/`iat`/`nonce`, `email_verified`). Nytt här:
JWKS-cache med kid-rotation (en okänd `kid` ger högst en omhämtning per minut), tenant- och
gruppanspråk för multitenant-leverantörer och egen tidskontroll med injicerbar klocka.

Kontrollerna görs i den här ordningen och varje fel ger en fast felkod utan anspråk i texten:
algoritm och `kid` -> signatur -> `aud` -> `iss` (med tenant) -> `exp`/`iat`/`nbf` -> `azp` ->
`nonce` -> `email_verified`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

import jwt

from konto.hamtare import HamtaFel, Hamtare
from konto.leverantor import Identitet, Leverantor

KLOCKTOLERANS_S = 60
DISCOVERY_TTL_S = 3600
JWKS_TTL_S = 3600
JWKS_MIN_OMHAMTNING_S = 60
MAX_ID_TOKEN_TECKEN = 8192
_SUBJECT = re.compile(r"^[A-Za-z0-9_.:@-]{1,255}$")


class OidcFel(Exception):
    """Inloggningen misslyckades. `kod` är fast text som är säker att logga och visa."""

    def __init__(self, kod: str) -> None:
        super().__init__(f"Inloggningen misslyckades ({kod})")
        self.kod = kod


def nytt_slump() -> str:
    """43 tecken base64url (256 bitar), som O-Tids `random()`."""
    return secrets.token_urlsafe(32)


def pkce_challenge(code_verifier: str) -> str:
    """PKCE S256: base64url(SHA-256(verifier)) utan utfyllnad (RFC 7636)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _https(url: Any) -> str:
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        raise OidcFel("discovery")
    return url


class _JwksCache:
    """Nycklar per `kid`. Okänd `kid` ger en omhämtning (kid-rotation), högst en per minut."""

    def __init__(self, hamtare: Hamtare, klocka: Callable[[], float]) -> None:
        self._hamtare = hamtare
        self._klocka = klocka
        self._lock = threading.Lock()
        self._nycklar: dict[str, Any] = {}
        self._hamtad: float | None = None
        self.url: str | None = None

    def _ladda(self, url: str) -> None:
        dok = self._hamtare.hamta_json(url)
        poster = dok.get("keys")
        if not isinstance(poster, list):
            raise OidcFel("jwks")
        nycklar: dict[str, Any] = {}
        for post in poster:
            if not isinstance(post, dict) or post.get("kty") != "RSA":
                continue
            kid = post.get("kid")
            if not isinstance(kid, str) or post.get("use", "sig") != "sig":
                continue
            try:
                nycklar[kid] = jwt.PyJWK(post).key
            except jwt.PyJWTError:
                continue
        self._nycklar = nycklar
        self._hamtad = self._klocka()

    def hamta(self, url: str, kid: str) -> Any:
        with self._lock:
            nu = self._klocka()
            try:
                if self._hamtad is None or self.url != url or nu - self._hamtad >= JWKS_TTL_S:
                    self.url = url
                    self._ladda(url)
                nyckel = self._nycklar.get(kid)
                if (
                    nyckel is None
                    and self._hamtad is not None
                    and nu - self._hamtad >= JWKS_MIN_OMHAMTNING_S
                ):
                    self._ladda(url)
                    nyckel = self._nycklar.get(kid)
            except HamtaFel:
                raise OidcFel("jwks") from None
            if nyckel is None:
                raise OidcFel("id-token")
            return nyckel


class OidcKlient:
    """Kodflödet mot en leverantör. Inget här rör databasen."""

    def __init__(
        self,
        leverantor: Leverantor,
        redirect_uri: str,
        hamtare: Hamtare,
        klocka: Callable[[], float] = time.time,
    ) -> None:
        self.leverantor = leverantor
        self._redirect_uri = redirect_uri
        self._hamtare = hamtare
        self._klocka = klocka
        self._jwks = _JwksCache(hamtare, klocka)
        self._lock = threading.Lock()
        self._metadata: dict[str, Any] | None = None
        self._metadata_tid = 0.0

    def metadata(self) -> dict[str, Any]:
        """Discovery-dokumentet (cachat en timme). Alla adresser måste vara https."""
        with self._lock:
            nu = self._klocka()
            if self._metadata is not None and nu - self._metadata_tid < DISCOVERY_TTL_S:
                return self._metadata
            try:
                dok = self._hamtare.hamta_json(self.leverantor.discovery_url)
            except HamtaFel:
                raise OidcFel("discovery") from None
            for nyckel in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                _https(dok.get(nyckel))
            utfardare = dok.get("issuer")
            lev = self.leverantor
            if lev.issuer_mall is None and lev.issuers and utfardare not in lev.issuers:
                raise OidcFel("discovery")
            self._metadata, self._metadata_tid = dok, nu
            return dok

    def auktoriserings_url(self, state: str, nonce: str, code_verifier: str) -> str:
        lev = self.leverantor
        param = {
            "response_type": "code",
            "client_id": lev.klient_id,
            "redirect_uri": self._redirect_uri,
            "scope": " ".join(lev.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        if lev.prompt:
            param["prompt"] = lev.prompt
        bas = str(self.metadata()["authorization_endpoint"])
        return f"{bas}{'&' if '?' in bas else '?'}{urlencode(param)}"

    def byt_kod(self, code: str, code_verifier: str, nonce: str) -> Identitet:
        """Byter koden mot en ID-token (med PKCE-verifieraren) och kontrollerar den."""
        lev = self.leverantor
        meta = self.metadata()
        try:
            svar = self._hamtare.skicka_formular(
                str(meta["token_endpoint"]),
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                    "client_id": lev.klient_id,
                    "client_secret": lev.hemlighet,
                    "code_verifier": code_verifier,
                },
            )
        except HamtaFel:
            raise OidcFel("token-utbyte") from None
        id_token = svar.get("id_token")
        if not isinstance(id_token, str) or not id_token or len(id_token) > MAX_ID_TOKEN_TECKEN:
            raise OidcFel("id-token")
        return self.kontrollera_id_token(id_token, nonce)

    def kontrollera_id_token(self, id_token: str, nonce: str) -> Identitet:
        lev = self.leverantor
        try:
            rubrik = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise OidcFel("id-token") from None
        alg, kid = rubrik.get("alg"), rubrik.get("kid")
        if alg not in lev.algoritmer or not isinstance(kid, str):
            raise OidcFel("id-token")
        nyckel = self._jwks.hamta(str(self.metadata()["jwks_uri"]), kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                nyckel,
                algorithms=list(lev.algoritmer),
                audience=lev.klient_id,
                options={
                    "require": ["exp", "iat", "iss", "sub", "aud"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "verify_iss": False,
                },
            )
        except jwt.PyJWTError:
            raise OidcFel("id-token") from None
        self._kontrollera_tid(claims)
        tenant = self._tenant(claims)
        iss = claims.get("iss")
        if not isinstance(iss, str) or not lev.giltig_issuer(iss, tenant):
            raise OidcFel("id-token")
        aud = claims.get("aud")
        azp = claims.get("azp")
        if (azp is not None and azp != lev.klient_id) or (
            isinstance(aud, list) and len(aud) > 1 and azp != lev.klient_id
        ):
            raise OidcFel("id-token")
        claim_nonce = claims.get("nonce")
        if not isinstance(claim_nonce, str) or not hmac.compare_digest(
            claim_nonce.encode(), nonce.encode()
        ):
            raise OidcFel("nonce")
        return self._identitet(claims, tenant)

    def _kontrollera_tid(self, claims: dict[str, Any]) -> None:
        nu = self._klocka()
        for nyckel in ("exp", "iat"):
            varde = claims.get(nyckel)
            if isinstance(varde, bool) or not isinstance(varde, int | float):
                raise OidcFel("id-token")
        if claims["exp"] <= nu - KLOCKTOLERANS_S:
            raise OidcFel("utgangen")
        if claims["iat"] > nu + KLOCKTOLERANS_S:
            raise OidcFel("id-token")
        nbf = claims.get("nbf")
        if nbf is not None and (
            isinstance(nbf, bool) or not isinstance(nbf, int | float) or nbf > nu + KLOCKTOLERANS_S
        ):
            raise OidcFel("id-token")

    def _tenant(self, claims: dict[str, Any]) -> str:
        namn = self.leverantor.claims.tenant
        tenant = claims.get(namn, "") if namn else ""
        if not isinstance(tenant, str) or not self.leverantor.giltig_tenant(tenant):
            raise OidcFel("tenant")
        return tenant

    def _identitet(self, claims: dict[str, Any], tenant: str) -> Identitet:
        lev = self.leverantor
        mapp = lev.claims
        subject = claims.get(mapp.subject)
        if not isinstance(subject, str) or not _SUBJECT.match(subject):
            raise OidcFel("id-token")
        epost = claims.get(mapp.epost)
        epost_text = epost.strip() if isinstance(epost, str) and "@" in epost else None
        verifierad = False
        if mapp.epost_verifierad is not None and lev.epost_betrodd:
            verifierad = claims.get(mapp.epost_verifierad) in (True, "true")
            if not verifierad or epost_text is None:
                raise OidcFel("epost-overifierad")
        namn = claims.get(mapp.namn)
        doman = claims.get(mapp.doman) if mapp.doman else None
        return Identitet(
            leverantor=lev.namn,
            subject=subject,
            tenant=tenant.lower(),
            epost=epost_text,
            epost_verifierad=verifierad,
            namn=namn.strip() if isinstance(namn, str) and namn.strip() else None,
            doman=doman.strip().lower() if isinstance(doman, str) and doman.strip() else None,
            grupper=self._grupper(claims),
        )

    def _grupper(self, claims: dict[str, Any]) -> frozenset[str] | None:
        namn = self.leverantor.claims.grupper
        if namn is None:
            return None
        namn_claims = claims.get("_claim_names")
        if isinstance(namn_claims, dict) and namn in namn_claims:
            return None  # överflöd (Entra, mer än 200 grupper): okänt, inte tomt
        rader = claims.get(namn, [])
        if not isinstance(rader, list) or len(rader) > 2000:
            raise OidcFel("id-token")
        if not all(isinstance(g, str) and 0 < len(g) <= 256 for g in rader):
            raise OidcFel("id-token")
        return frozenset(rader)
