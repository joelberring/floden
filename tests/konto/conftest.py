"""Gemensamma hjälpmedel: en simulerad webbläsare, falsk leverantör och färdiga miljöer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from konto.adapter import KontoAdapter, Svar
from konto.konfig import KontoKonfig
from konto.leverantor import entra, google
from konto.register import Register
from konto.sessioner import kaknamn
from tests.konto.fake_idp import FalskIdp, Klocka

ORIGIN = "https://floden.test"
KAK = kaknamn(True)
ANNA = {
    "sub": "sub-anna-x1",  # ej hex: får inte kunna råka finnas i slumpade id:n
    "email": "anna@example.test",
    "email_verified": True,
    "name": "Anna Exempel",
}
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
ENTRA_MALL = "https://login.entra.test/{tenant}/v2.0"


def rubrik(svar: Svar, namn: str) -> str | None:
    for k, v in svar[1]:
        if k.lower() == namn.lower():
            return v
    return None


def alla_rubriker(svar: Svar, namn: str) -> list[str]:
    return [v for k, v in svar[1] if k.lower() == namn.lower()]


@dataclass
class Webblasare:
    """Håller kakor som en webbläsare och anropar adaptern."""

    adapter: KontoAdapter
    kakor: dict[str, str] = field(default_factory=dict)
    satta: dict[str, str] = field(default_factory=dict)  # senaste Set-Cookie per namn, rått

    def anrop(
        self,
        metod: str,
        sokvag: str,
        query: Mapping[str, Sequence[str]] | None = None,
        headers: Mapping[str, str] | None = None,
        kropp: bytes = b"",
    ) -> Svar:
        h = dict(headers or {})
        if self.kakor:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.kakor.items())
        svar = self.adapter.hantera_konto(metod, sokvag, query or {}, h, kropp)
        for sc in alla_rubriker(svar, "Set-Cookie"):
            namn, _, resten = sc.partition("=")
            varde = resten.split(";")[0]
            self.satta[namn] = sc
            if "Max-Age=0" in sc:
                self.kakor.pop(namn, None)
            else:
                self.kakor[namn] = varde
        return svar

    def logga_in(
        self,
        idp: FalskIdp,
        claims: Mapping[str, Any],
        lev: str = "google",
        atervand: str | None = None,
    ) -> Svar:
        start = self.anrop(
            "GET", f"/konto/logga-in/{lev}", {"atervand": [atervand]} if atervand else None
        )
        assert start[0] == 302, start
        kod, state = idp.godkann(rubrik(start, "Location") or "", claims)
        return self.anrop("GET", f"/konto/callback/{lev}", {"code": [kod], "state": [state]})

    def csrf_headers(self) -> dict[str, str]:
        return {"X-Floden-CSRF": self.kakor.get(KAK.csrf, "")}


@dataclass
class Miljo:
    adapter: KontoAdapter
    register: Register
    idp: FalskIdp
    klocka: Klocka

    def webblasare(self) -> Webblasare:
        return Webblasare(self.adapter)


def bygg_konfig(tmp_path: Path, **andra: Any) -> KontoKonfig:
    grund: dict[str, Any] = {
        "inloggning": "pa",
        "databas": tmp_path / "konto.sqlite",
        "offentlig_url": ORIGIN,
        "cookie_secure": True,
        "nya_konton": "inbjudan",
    }
    return KontoKonfig(**{**grund, **andra})


@pytest.fixture
def klocka() -> Klocka:
    return Klocka()


@pytest.fixture
def idp(klocka: Klocka) -> FalskIdp:
    return FalskIdp(klocka)


@pytest.fixture
def miljo(tmp_path: Path, klocka: Klocka, idp: FalskIdp) -> Miljo:
    """Google mot den falska leverantören, `nya_konton = "inbjudan"`."""
    lev = google(
        idp.klient_id, idp.hemlighet, discovery_url=idp.discovery_url, issuers=(idp.issuer,)
    )
    adapter = KontoAdapter(
        bygg_konfig(tmp_path), hamtare=idp, klocka=klocka, leverantorer={"google": lev}
    )
    assert adapter.register is not None
    return Miljo(adapter, adapter.register, idp, klocka)


@pytest.fixture
def entra_miljo(tmp_path: Path, klocka: Klocka) -> Miljo:
    """Entra ID (multitenant) mot en falsk leverantör: tenant ur `tid`, grupper ur `groups`."""
    idp = FalskIdp(
        klocka,
        bas="https://login.entra.test",
        issuer=ENTRA_MALL.format(tenant=TENANT_A),
        discovery_issuer=ENTRA_MALL.replace("{tenant}", "{tenantid}"),
    )
    lev = entra(
        idp.klient_id, idp.hemlighet, discovery_url=idp.discovery_url, issuer_mall=ENTRA_MALL
    )
    adapter = KontoAdapter(
        bygg_konfig(tmp_path), hamtare=idp, klocka=klocka, leverantorer={"entra": lev}
    )
    assert adapter.register is not None
    return Miljo(adapter, adapter.register, idp, klocka)


def entra_claims(tid: str = TENANT_A, **extra: Any) -> dict[str, Any]:
    return {
        "sub": "entra-sub-1",
        "tid": tid,
        "iss": ENTRA_MALL.format(tenant=tid),
        "name": "Bo Exempel",
        "email": "bo@kommun.test",
        **extra,
    }
