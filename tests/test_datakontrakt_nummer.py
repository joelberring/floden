# ruff: noqa: E501
"""Kontraktsnumren i docs/DATAKONTRAKT.md är unika och står i stigande ordning (luckor är tillåtna: K12-K19 m.fl. är reserverade)."""

from __future__ import annotations

import re
from pathlib import Path

DOK = Path(__file__).resolve().parents[1] / "docs" / "DATAKONTRAKT.md"


def test_kontraktsnummer_ar_unika_och_i_ordning() -> None:
    nummer = [int(m.group(1)) for m in re.finditer(r"^## K(\d+)\b", DOK.read_text(encoding="utf-8"), re.M)]
    assert nummer, "inga kontraktsrubriker hittades"
    dubbletter = sorted({n for n in nummer if nummer.count(n) > 1})
    assert not dubbletter, f"Dubbla kontraktsnummer: {[f'K{n}' for n in dubbletter]}"
    assert nummer == sorted(nummer), "Kontraktsnumren står inte i ordning i DATAKONTRAKT.md"
