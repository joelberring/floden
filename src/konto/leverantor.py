"""Inloggningsleverantörer (OIDC): Google (aktiv) och generisk OIDC/Microsoft Entra ID (förberedd).

Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af): `GOOGLE_ENDPOINTS` och Googles
`iss`-former i `google-oidc.ts`. Entra och generisk OIDC är nya i Floden (O-Tid har bara Google).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from konto.konfig import KontoKonfigFel, LeverantorInst

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
# Google anger båda formerna i `iss`.
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
ENTRA_DISCOVERY = (
    "https://login.microsoftonline.com/organizations/v2.0/.well-known/openid-configuration"
)
ENTRA_ISSUER_MALL = "https://login.microsoftonline.com/{tenant}/v2.0"
_TENANT_MONSTER = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


@dataclass(frozen=True)
class Claimmappning:
    """Vilka påståenden (claims) i ID-token som är vad. `None` = leverantören har inte det."""

    subject: str = "sub"
    epost: str = "email"
    epost_verifierad: str | None = "email_verified"
    namn: str = "name"
    tenant: str | None = None
    grupper: str | None = None
    doman: str | None = None


@dataclass(frozen=True)
class Identitet:
    """Personen enligt en kontrollerad ID-token. Råa påståenden sparas aldrig."""

    leverantor: str
    subject: str
    tenant: str  # '' när leverantören inte har tenants
    epost: str | None
    epost_verifierad: bool
    namn: str | None
    doman: str | None
    grupper: frozenset[str] | None  # None = okänt (ingen mappning eller överflöd i Entra)


@dataclass(frozen=True)
class Leverantor:
    namn: str
    discovery_url: str
    klient_id: str
    hemlighet: str = field(repr=False)
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    claims: Claimmappning = field(default_factory=Claimmappning)
    issuers: tuple[str, ...] = ()
    issuer_mall: str | None = None  # t.ex. ENTRA_ISSUER_MALL; {tenant} fylls ur tenant-claimen
    epost_betrodd: bool = True  # får e-postadressen (när den är verifierad) styra kontokoppling?
    flertenant: bool = False  # kräver att tenant finns och är registrerad som organisation
    prompt: str | None = "select_account"
    algoritmer: tuple[str, ...] = ("RS256",)

    def giltig_issuer(self, iss: str, tenant: str) -> bool:
        if self.issuer_mall is not None:
            return bool(tenant) and iss == self.issuer_mall.format(tenant=tenant)
        return iss in self.issuers

    def giltig_tenant(self, tenant: str) -> bool:
        return bool(_TENANT_MONSTER.match(tenant)) if self.flertenant else tenant == ""


def google(
    klient_id: str,
    hemlighet: str,
    *,
    discovery_url: str = GOOGLE_DISCOVERY,
    issuers: tuple[str, ...] = GOOGLE_ISSUERS,
) -> Leverantor:
    """Google. `hd` (hostad domän) intygar att kontot tillhör en Workspace-domän."""
    return Leverantor(
        namn="google",
        discovery_url=discovery_url,
        klient_id=klient_id,
        hemlighet=hemlighet,
        claims=Claimmappning(doman="hd"),
        issuers=issuers,
    )


def entra(
    klient_id: str,
    hemlighet: str,
    *,
    discovery_url: str = ENTRA_DISCOVERY,
    issuer_mall: str = ENTRA_ISSUER_MALL,
    namn: str = "entra",
) -> Leverantor:
    """Microsoft Entra ID, multitenant: tenant ur `tid`, grupper ur `groups`.

    E-postadressen i Entra är inte verifierad (nOAuth-problemet) och används därför aldrig för att
    koppla konton eller ta emot inbjudningar. Nyckeln är (`tid`, `sub`).
    """
    return Leverantor(
        namn=namn,
        discovery_url=discovery_url,
        klient_id=klient_id,
        hemlighet=hemlighet,
        claims=Claimmappning(epost_verifierad=None, tenant="tid", grupper="groups"),
        issuer_mall=issuer_mall,
        epost_betrodd=False,
        flertenant=True,
        prompt=None,
    )


def _claims_fran(extra: Mapping[str, Any]) -> Claimmappning:
    rå = extra.get("claims", {})
    if not isinstance(rå, Mapping):
        raise KontoKonfigFel("claims måste vara en tabell")
    standard = Claimmappning()
    falt: dict[str, Any] = {}
    for nyckel in standard.__dataclass_fields__:
        if nyckel in rå:
            varde = rå[nyckel]
            if varde is not None and not isinstance(varde, str):
                raise KontoKonfigFel(f"claims.{nyckel} måste vara text")
            falt[nyckel] = varde
    return Claimmappning(**{**standard.__dict__, **falt})


def bygg_leverantor(inst: LeverantorInst) -> Leverantor:
    """Bygger leverantören ur konfigurationen (`typ` = google, entra eller oidc)."""
    extra = inst.extra
    if inst.typ == "google":
        return google(inst.klient_id, inst.hemlighet)
    if inst.typ == "entra":
        return entra(
            inst.klient_id,
            inst.hemlighet,
            discovery_url=str(extra.get("discovery_url", ENTRA_DISCOVERY)),
            namn=inst.namn,
        )
    if inst.typ != "oidc":
        raise KontoKonfigFel(f"Okänd leverantörstyp {inst.typ!r} för {inst.namn}")
    discovery = extra.get("discovery_url")
    if not isinstance(discovery, str) or not discovery:
        raise KontoKonfigFel(f"Leverantören {inst.namn} saknar discovery_url")
    scopes = extra.get("scopes", ["openid", "email", "profile"])
    if not isinstance(scopes, list) or "openid" not in scopes:
        raise KontoKonfigFel(f"scopes för {inst.namn} måste vara en lista som innehåller openid")
    utfardare = extra.get("issuer")
    standard_issuer = discovery.removesuffix("/.well-known/openid-configuration")
    mall = extra.get("issuer_mall")
    return Leverantor(
        namn=inst.namn,
        discovery_url=discovery,
        klient_id=inst.klient_id,
        hemlighet=inst.hemlighet,
        scopes=tuple(str(s) for s in scopes),
        claims=_claims_fran(extra),
        issuers=(str(utfardare),) if utfardare else (standard_issuer,),
        issuer_mall=str(mall) if mall else None,
        epost_betrodd=extra.get("epost_betrodd", False) is True,
        flertenant=extra.get("flertenant", False) is True,
    )
