# ruff: noqa: E501
"""Texten för projektets avgränsning (U2) och raden om underlaget i handlingsrutan."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from rapport.belaggskalla import (
    HELA_OMRADET,
    HELA_OMRADET_MARK,
    INOM_AVGRANSNINGEN,
    hela_omradet_text,
)
from rapport.korningsval import UnderlagFel, korningar_for, underlagstext, valj_korning


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


def _fejk_rt(
    korningar: list[dict[str, Any]],
    poster: dict[str, dict[str, Any]],
    underlag: dict[str, dict[str, Any]],
    *,
    scenarier: set[str] = frozenset(),
) -> Any:
    app = SimpleNamespace(
        fragor=SimpleNamespace(lista_korningar=lambda: {"korningar": korningar}),
        projekt_for_korning=lambda kid: "proj-1" if kid in poster else None,
        korningsunderlag=lambda kid: underlag[kid],
        kartverktyg=SimpleNamespace(finns_scenario=lambda _p, sc: sc in scenarier),
    )

    def _korningspost(kid: str, amne: str) -> dict[str, Any]:
        p = poster[kid]
        if p["amne"] != amne:
            raise ValueError("fel ämne")
        return p

    return SimpleNamespace(tjanst=SimpleNamespace(app=app), _korningspost=_korningspost)


def test_lage_idag_inkluderas_och_valjs_utan_409_i_steg3() -> None:
    rt = _fejk_rt(
        korningar=[{"korning_id": "k-nulage", "status": "klar", "skapad": "2026-11-25T10:00:00Z"}],
        poster={
            "k-nulage": {
                "korning_id": "k-nulage",
                "fraga_id": "lage-idag-vatten",
                "amne": "skyfall",
                "jamforelser": [],
                "scenarier": ["fore"],
            }
        },
        underlag={"k-nulage": {"slag": "lage_idag", "scenario": None}},
    )
    lista = korningar_for(rt, "proj-1", "skyfall")
    assert [x["korning_id"] for x in lista] == ["k-nulage"]
    assert lista[0]["slag"] == "lage_idag"

    # Både förval (utan korning_id) och explicit korning_id väljer läget idag utan 409
    assert valj_korning(rt, "proj-1", "skyfall")["korning_id"] == "k-nulage"
    assert valj_korning(rt, "proj-1", "skyfall", korning_id="k-nulage")["korning_id"] == "k-nulage"


def test_rangordning_forslag_fore_egen_fore_lage_idag_fore_demo() -> None:
    rt = _fejk_rt(
        korningar=[
            {"korning_id": "k-demo", "status": "klar", "skapad": "2026-11-25T12:00:00Z"},
            {"korning_id": "k-nulage", "status": "klar", "skapad": "2026-11-25T13:00:00Z"},
            {"korning_id": "k-egen", "status": "klar", "skapad": "2026-11-25T11:00:00Z"},
            {"korning_id": "k-forslag", "status": "klar", "skapad": "2026-11-25T10:00:00Z"},
        ],
        poster={
            "k-demo": {"korning_id": "k-demo", "fraga_id": "demo", "amne": "skyfall", "jamforelser": [{"namn": "j"}], "scenarier": ["fore", "efter"]},
            "k-nulage": {"korning_id": "k-nulage", "fraga_id": "lage-idag-v", "amne": "skyfall", "jamforelser": [], "scenarier": ["fore"]},
            "k-egen": {"korning_id": "k-egen", "fraga_id": "egen-1", "amne": "skyfall", "jamforelser": [{"namn": "j"}], "scenarier": ["fore", "efter"]},
            "k-forslag": {"korning_id": "k-forslag", "fraga_id": "rita-1", "amne": "skyfall", "jamforelser": [{"namn": "j"}], "scenarier": ["fore", "efter"]},
        },
        underlag={
            "k-demo": {"slag": "demo", "scenario": None},
            "k-nulage": {"slag": "lage_idag", "scenario": None},
            "k-egen": {"slag": "egen", "scenario": None},
            "k-forslag": {"slag": "forslag", "scenario": {"slug": "hus-a", "namn": "Hus A"}},
        },
    )
    ordning = [x["korning_id"] for x in korningar_for(rt, "proj-1", "skyfall")]
    assert ordning == ["k-forslag", "k-egen", "k-nulage", "k-demo"]


def test_feltext_hanvisar_till_steg_2_nar_korning_saknas() -> None:
    rt = _fejk_rt(korningar=[], poster={}, underlag={}, scenarier={"hus-a"})
    with pytest.raises(UnderlagFel) as ei1:
        valj_korning(rt, "proj-1", "skyfall")
    assert ei1.value.status == 409
    assert "steg 2" in str(ei1.value)
    assert "steg 3" not in str(ei1.value)

    with pytest.raises(UnderlagFel) as ei2:
        valj_korning(rt, "proj-1", "skyfall", scenario="hus-a")
    assert ei2.value.status == 409
    assert "steg 2" in str(ei2.value)
    assert "steg 3" not in str(ei2.value)


def test_luft_mkn_utan_jamforelser_slapps_igenom_och_ekologi_kraver_jamforelser() -> None:
    """W-B7: luft.mkn har aldrig [[jamforelser]] men ska väljas; ekologi utan jämförelse avvisas."""
    rt = _fejk_rt(
        korningar=[
            {"korning_id": "k-luft-demo", "status": "klar", "skapad": "2026-11-25T10:00:00Z"},
            {"korning_id": "k-luft-egen", "status": "klar", "skapad": "2026-11-25T11:00:00Z"},
            {"korning_id": "k-eko-nulage", "status": "klar", "skapad": "2026-11-25T12:00:00Z"},
            {"korning_id": "k-eko-jfr", "status": "klar", "skapad": "2026-11-25T09:00:00Z"},
        ],
        poster={
            "k-luft-demo": {"korning_id": "k-luft-demo", "fraga_id": "demo-luft", "amne": "luft", "jamforelser": [], "scenarier": ["bas"]},
            "k-luft-egen": {"korning_id": "k-luft-egen", "fraga_id": "luft-egen", "amne": "luft", "jamforelser": [], "scenarier": ["bas"]},
            "k-eko-nulage": {"korning_id": "k-eko-nulage", "fraga_id": "lage-idag-ekologi", "amne": "ekologi", "jamforelser": [], "scenarier": ["fore"]},
            "k-eko-jfr": {
                "korning_id": "k-eko-jfr",
                "fraga_id": "api-ekologi-1",
                "amne": "ekologi",
                "jamforelser": [{"namn": "konnektivitet_stromtathet_efter_minus_fore"}],
                "scenarier": ["fore", "efter"],
                "antaganden": ["Habitatnätverk Tallskog"],
            },
        },
        underlag={
            "k-luft-demo": {"slag": "demo", "scenario": None},
            "k-luft-egen": {"slag": "egen", "scenario": None},
            "k-eko-nulage": {"slag": "lage_idag", "scenario": None},
            "k-eko-jfr": {"slag": "egen", "scenario": None},
        },
    )
    luft_lista = korningar_for(rt, "proj-1", "luft")
    assert [x["korning_id"] for x in luft_lista] == ["k-luft-egen", "k-luft-demo"]
    assert valj_korning(rt, "proj-1", "luft")["korning_id"] == "k-luft-egen"

    eko_lista = korningar_for(rt, "proj-1", "ekologi")
    assert [x["korning_id"] for x in eko_lista] == ["k-eko-jfr"]
    assert valj_korning(rt, "proj-1", "ekologi")["korning_id"] == "k-eko-jfr"
