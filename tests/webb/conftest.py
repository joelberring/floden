"""Fixturer för inloggningstesterna: den falska OIDC-leverantören från `tests/konto`."""

from tests.konto.conftest import idp, klocka, miljo

__all__ = ["idp", "klocka", "miljo"]
