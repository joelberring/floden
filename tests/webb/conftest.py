"""Fixturer för inloggningstesterna: den falska OIDC-leverantören från `tests/konto`."""

from tests.konto.conftest import idp, klocka, miljo

__all__ = ["idp", "klocka", "miljo"]

from tests.demovarld.test_demo import Demo, demo  # noqa: E402,F401

__all__ += ["Demo", "demo"]
