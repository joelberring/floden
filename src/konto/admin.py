"""Administratörs-CLI för det som bara servern ska kunna göra (bootstrap, organisationsregler).

Körs på servern med `PYTHONPATH=src python -m konto.admin <kommando>`. Skriver id:n, aldrig e-post.
Mönster från O-Tids `scripts/account-superadmin.ts`: superadmin sätts bara med serverkommando.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from konto.databas import Databas
from konto.konfig import las_konto_konfig
from konto.register import Register


def main(argv: Sequence[str] | None = None) -> int:
    from gemensamt.konfig import las_konfig  # lokal import: numpy behövs inte för resten av paketet

    p = argparse.ArgumentParser(prog="konto.admin", description=__doc__)
    p.add_argument("--konfig", default="config.toml")
    p.add_argument("--lokal-konfig", default="config.local.toml")
    sub = p.add_subparsers(dest="kommando", required=True)
    for namn, args in {
        "skapa-organisation": ["namn"],
        "registrera-tenant": ["organisation", "leverantor", "tenant"],
        "registrera-doman": ["organisation", "doman"],
        "skapa-forvaltning": ["organisation", "namn"],
        "mappa-grupp": ["organisation", "forvaltning", "grupp"],
        "forbered-konto": ["epost"],
        "konto-id": ["epost"],
        "ge-superadmin": ["konto"],
        "gor-organisationsadmin": ["konto", "organisation"],
        "skapa-projekt": ["organisation", "namn"],
    }.items():
        s = sub.add_parser(namn)
        for arg in args:
            s.add_argument(arg)
    a = p.parse_args(argv)

    lokal = Path(a.lokal_konfig)
    konf = las_konto_konfig(las_konfig(a.konfig, lokal if lokal.exists() else None))
    r = Register(Databas(konf.databas))
    if a.kommando == "skapa-organisation":
        print(r.skapa_organisation(a.namn))
    elif a.kommando == "registrera-tenant":
        r.registrera_tenant(a.organisation, a.leverantor, a.tenant)
        print("ok")
    elif a.kommando == "registrera-doman":
        r.registrera_doman(a.organisation, a.doman)
        print("ok")
    elif a.kommando == "skapa-forvaltning":
        print(r.skapa_forvaltning(a.organisation, a.namn))
    elif a.kommando == "mappa-grupp":
        print(r.mappa_grupp(a.organisation, a.forvaltning, a.grupp))
    elif a.kommando == "forbered-konto":
        print(r.forbered_konto(a.epost))
    elif a.kommando == "konto-id":
        k = r.konto_via_epost(a.epost)
        if k is None:
            print("Inget konto med den adressen. Logga in en gång eller kör forbered-konto.", file=sys.stderr)  # noqa: E501
            return 1
        print(k.id)
    elif a.kommando == "ge-superadmin":
        r.ge_superadmin(a.konto)
        print("ok")
    elif a.kommando == "gor-organisationsadmin":
        r.gor_organisationsadmin(a.konto, a.organisation)
        print("ok")
    elif a.kommando == "skapa-projekt":
        print(r.skapa_projekt(a.organisation, a.namn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
