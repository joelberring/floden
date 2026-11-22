"""Fixturer för rapporttesterna: demovärlden (litet läge) delas med `tests/demovarld`."""

from tests.demovarld.test_demo import Demo, demo  # noqa: F401

__all__ = ["Demo", "demo"]
