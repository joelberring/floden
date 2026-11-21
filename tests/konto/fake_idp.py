"""En falsk OIDC-leverantör för tester utan nätverk: egen RSA-nyckel, JWKS, discovery, kodutbyte.

Den kontrollerar PKCE (`code_verifier` mot `code_challenge`), klient-id, hemlighet och redirect-URI
som en riktig leverantör, och kan med `paverka` ge fel i ID-token (nonce, aud, iss, exp ...).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from konto.hamtare import HamtaFel

T0 = 1_800_000_000.0

_NYCKLAR: dict[str, rsa.RSAPrivateKey] = {}


def rsa_nyckel(namn: str) -> rsa.RSAPrivateKey:
    """RSA-nycklar skapas en gång per testkörning (generering är långsam)."""
    if namn not in _NYCKLAR:
        _NYCKLAR[namn] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _NYCKLAR[namn]


def _jwk(kid: str, nyckel: rsa.RSAPrivateKey) -> dict[str, Any]:
    publik = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(nyckel.public_key()))
    return {**publik, "kid": kid, "use": "sig", "alg": "RS256"}


class Klocka:
    def __init__(self, start: float = T0) -> None:
        self.nu = start

    def __call__(self) -> float:
        return self.nu

    def gå(self, sekunder: float) -> None:
        self.nu += sekunder


class FalskIdp:
    """Både leverantören och `Hamtare` (anropen går hit i stället för över nätet)."""

    def __init__(
        self,
        klocka: Klocka,
        *,
        bas: str = "https://idp.test",
        issuer: str | None = None,
        discovery_issuer: str | None = None,
        klient_id: str = "klient-123",
        hemlighet: str = "hemlig-456",
        redirect_uri: str = "https://floden.test/konto/callback/google",
    ) -> None:
        self.klocka = klocka
        self.bas = bas
        self.issuer = issuer or bas
        self.discovery_issuer = discovery_issuer or self.issuer
        self.klient_id = klient_id
        self.hemlighet = hemlighet
        self.redirect_uri = redirect_uri
        self.nycklar: dict[str, rsa.RSAPrivateKey] = {"k1": rsa_nyckel("k1")}
        self.signera_med = "k1"
        self.koder: dict[str, dict[str, Any]] = {}
        self.anrop: list[str] = []
        self.paverka: dict[str, Any] = {}  # claims som ersätter eller (None) tas bort
        self.token_rubrik: dict[str, Any] = {}
        self.falsk_nyckel: rsa.RSAPrivateKey | None = None  # signerar med fel nyckel
        self._nr = 0

    # --- adresser --------------------------------------------------------------------------
    @property
    def discovery_url(self) -> str:
        return f"{self.bas}/.well-known/openid-configuration"

    @property
    def jwks_url(self) -> str:
        return f"{self.bas}/jwks"

    # --- Hamtare ---------------------------------------------------------------------------
    def hamta_json(self, url: str) -> dict[str, Any]:
        self.anrop.append(url)
        if url == self.discovery_url:
            return {
                "issuer": self.discovery_issuer,
                "authorization_endpoint": f"{self.bas}/authorize",
                "token_endpoint": f"{self.bas}/token",
                "jwks_uri": self.jwks_url,
            }
        if url == self.jwks_url:
            return {"keys": [_jwk(kid, k) for kid, k in self.nycklar.items()]}
        raise HamtaFel("okänd adress")

    def skicka_formular(self, url: str, falt: Mapping[str, str]) -> dict[str, Any]:
        self.anrop.append(url)
        if url != f"{self.bas}/token":
            raise HamtaFel("okänd adress")
        post = self.koder.pop(falt.get("code", ""), None)
        if post is None:
            raise HamtaFel("ogiltig kod")
        if (
            falt.get("client_id") != self.klient_id
            or falt.get("client_secret") != self.hemlighet
            or falt.get("redirect_uri") != post["redirect_uri"]
            or falt.get("grant_type") != "authorization_code"
        ):
            raise HamtaFel("ogiltig klient")
        digest = hashlib.sha256(falt.get("code_verifier", "").encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != post["challenge"]:
            raise HamtaFel("PKCE misslyckades")
        return {"id_token": self.id_token(post["claims"], post["nonce"]), "token_type": "Bearer"}

    # --- användarens godkännande ----------------------------------------------------------------
    def godkann(self, location: str, claims: Mapping[str, Any]) -> tuple[str, str]:
        """Simulerar att personen loggar in: ger (code, state) att skicka till callback."""
        delar = urlsplit(location)
        q = {k: v[0] for k, v in parse_qs(delar.query).items()}
        assert delar.geturl().startswith(f"{self.bas}/authorize")
        assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
        assert q["client_id"] == self.klient_id
        self._nr += 1
        kod = f"kod-{self._nr}"
        self.koder[kod] = {
            "claims": dict(claims),
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
            "redirect_uri": q["redirect_uri"],
        }
        return kod, q["state"]

    def id_token(self, claims: Mapping[str, Any], nonce: str) -> str:
        nu = int(self.klocka())
        grund: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.klient_id,
            "iat": nu,
            "exp": nu + 3600,
            "nonce": nonce,
            **claims,
            **self.paverka,
        }
        nyttlast = {k: v for k, v in grund.items() if v is not None}
        kid = self.signera_med
        return jwt.encode(
            nyttlast,
            self.falsk_nyckel or self.nycklar.get(kid) or rsa_nyckel(kid),
            algorithm="RS256",
            headers={"kid": kid, **self.token_rubrik},
        )
