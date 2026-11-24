"""Produktregistret och felmeddelandena när Lantmäteriet sa nej till en produkt (falsk server, inget nät)."""

# ruff: noqa: E501
from __future__ import annotations

from pathlib import Path

import pytest

from lantmateriet.hamta import hamta_omrade
from lantmateriet.klient import LmKlient
from lantmateriet.nycklar import NyckelFel, Nycklar
from lantmateriet.omrade import las_omrade
from lantmateriet.produkter import produkt_fel_text, produkter
from tests.lantmateriet.conftest import Klocka  # noqa: F401
from tests.lantmateriet.falsk_server import HEMLIGHET, KLIENT_ID, FalskLm
from tests.lantmateriet.test_hamta import skriv_omrade

FORBJUDNA = ("lägenhet", "belägenhet", "fastighetsindelning", "samfällighet")


def test_registret_har_bara_avgiftsfria_produkter_utan_personuppgiftsnara() -> None:
    p = produkter()
    namn = [x.namn for x in p]
    assert {"Laserdata Nedladdning, skog", "Ortofoto Nedladdning", "Byggnad Nedladdning, vektor"} <= set(namn)
    assert all(x.avgiftsfri and x.anvands_till and x.stac.startswith("stac-") for x in p)
    assert not any(f in n.lower() for n in namn for f in FORBJUDNA)
    assert {x.roll for x in p if x.behovs} == {"laser"}  # byggnader och ortofoto är valfria


def test_produkttexten_namnger_produkten_och_att_beställning_tar_tid() -> None:
    t = produkt_fel_text("byggnader", 403, samling="byggnad", metod="oauth2")
    assert "Byggnad Nedladdning, vektor" in t and "403" in t and "'byggnad'" in t
    assert "ibland dagar" in t and "Geotorget" in t
    assert "Geotorgets användarnamn och lösenord" in t  # oauth2 valt: föreslår det andra sättet
    assert "Geotorgets användarnamn" not in produkt_fel_text("laser", 401, metod="basic")


def test_hamtning_nekad_produkt_sager_vilken_produkt(lm: FalskLm, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    k = LmKlient(Nycklar(id=KLIENT_ID, hemlighet=HEMLIGHET), lm.tjanster(), timeout_s=10.0)
    lm.avvisa_tjanster = {"bild"}
    with pytest.raises(NyckelFel) as e:
        hamta_omrade(k, om, tmp_path / "d", laser=False, orto=True, skriv=lambda _: None)
    text = str(e.value)
    assert "Ortofoto Nedladdning" in text and "403" in text and "ibland dagar" in text
    assert e.value.status == 403 and e.value.steg == "tjanst"
    assert HEMLIGHET not in text and KLIENT_ID not in text


def test_fel_nyckel_vid_token_sager_inloggningssatt_inte_produkt(lm: FalskLm, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    k = LmKlient(Nycklar(id=KLIENT_ID, hemlighet="fel-hemlighet-xyz"), lm.tjanster(), timeout_s=10.0)
    with pytest.raises(NyckelFel) as e:
        hamta_omrade(k, om, tmp_path / "d", laser=True, skriv=lambda _: None)
    text = str(e.value)
    assert e.value.steg == "token" and "401" in text and "API-portalen" in text and "inloggningssätt" in text
    assert "Laserdata" not in text and "fel-hemlighet-xyz" not in text
