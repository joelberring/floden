"""Gemensam uppsättning för tester med projekt per konto (Anna lasare, Bo utredare)."""

from __future__ import annotations

from dataclasses import dataclass

from tests.konto.conftest import Miljo


@dataclass
class Projekt:
    anna_projekt: str
    bo_projekt: str


def setup_projekt(miljo: Miljo) -> Projekt:
    r = miljo.register
    org = r.skapa_organisation("Exempelkommun")
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, org)
    pa, pb = r.skapa_projekt(org, "Provområdet"), r.skapa_projekt(org, "Annat projekt")
    r.bjud_in(admin, pa, "anna@example.test")
    r.bjud_in(admin, pb, "bo@example.test", "utredare")
    return Projekt(pa, pb)
