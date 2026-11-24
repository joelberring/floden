# ruff: noqa: E501
"""Texten för projektets avgränsning (U2) och raden om underlaget i handlingsrutan."""

from __future__ import annotations

from rapport.belaggskalla import (
    HELA_OMRADET,
    HELA_OMRADET_MARK,
    INOM_AVGRANSNINGEN,
    hela_omradet_text,
)
from rapport.korningsval import underlagstext


def test_hela_beraknings_omradet_blir_projektets_avgransning_nar_polygon_finns() -> None:
    assert hela_omradet_text("hela beräkningsområdet", False) == "hela beräkningsområdet"
    assert hela_omradet_text(HELA_OMRADET_MARK, False) == HELA_OMRADET_MARK
    assert hela_omradet_text("hela beräkningsområdet", True) == "inom projektets avgränsning (polygon)" == INOM_AVGRANSNINGEN
    m = hela_omradet_text(HELA_OMRADET_MARK, True)
    assert m == "mark inom projektets avgränsning (polygon); byggnadernas tak undantagna"
    assert "beräkningsområdet" not in m
    assert "inom projektets avgränsning (polygon)" in hela_omradet_text(HELA_OMRADET, True)


def test_underlagstext_for_forslag_demo_och_okant() -> None:
    assert underlagstext({"slag": "forslag", "scenario": {"slug": "a", "namn": "Hus A"}}) == "gäller förslaget ”Hus A”"
    assert "demokörningen" in underlagstext({"slag": "demo", "scenario": None})
    assert underlagstext({"slag": "okand", "scenario": None}) == ""
    assert underlagstext(None) == ""
