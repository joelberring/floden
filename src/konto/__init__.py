"""Konton och inloggning för Floden: Google nu, förberett för SSO (Entra ID / generisk OIDC).

Se `docs/KONTO.md`. Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af).
"""

from konto.adapter import Forfragan, KontoAdapter, Kontroll, Svar
from konto.databas import Databas
from konto.konfig import KontoKonfig, KontoKonfigFel, las_konto_konfig
from konto.register import BehorighetFel, Konto, Register, RegisterFel
from konto.sessioner import Inloggad

__all__ = [
    "BehorighetFel",
    "Databas",
    "Forfragan",
    "Inloggad",
    "Konto",
    "KontoAdapter",
    "KontoKonfig",
    "KontoKonfigFel",
    "Kontroll",
    "Register",
    "RegisterFel",
    "Svar",
    "las_konto_konfig",
]
