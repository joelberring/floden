"""Fixturer för inloggningstesterna: den falska OIDC-leverantören från `tests/konto`."""

from tests.konto.conftest import idp, klocka, miljo

__all__ = ["idp", "klocka", "miljo"]

from tests.demovarld.test_demo import (  # noqa: E402,F401
    Demo,
    _registrera_demo_mall,
    demo,
    demo_mall,
)

__all__ += ["Demo", "_registrera_demo_mall", "demo", "demo_mall"]
