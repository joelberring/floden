"""Fixturer för rapporttesterna: demovärlden (litet läge) delas med `tests/demovarld`."""

from tests.demovarld.test_demo import (  # noqa: F401
    Demo,
    _registrera_demo_mall,
    demo,
    demo_mall,
)

__all__ = ["Demo", "_registrera_demo_mall", "demo", "demo_mall"]
