# SPDX-License-Identifier: MIT
"""Tester för `luft.skiss_emissionsfaktorer` och `data/luft/skiss_schablon_emission.toml`."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from luft.skiss_emissionsfaktorer import (
    STANDARD_SKISS_SCHABLON_FIL,
    hamta_emissionsfaktorer,
    ladda_skiss_schablon,
    las_emissionsfaktorer,
    las_emissionsfaktorer_csv,
    las_emissionsfaktorer_toml,
)
from luft.typer import LuftFel


def test_skiss_schablon_anvands_aldrig_tyst_som_standard() -> None:
    """Val 3A: publikt skiss-schablonset kräver alltid uttryckligt anvand_skiss_schablon=True."""
    with pytest.raises(LuftFel, match="aldrig tyst"):
        ladda_skiss_schablon()
    with pytest.raises(LuftFel, match="aldrig tyst"):
        ladda_skiss_schablon(anvand_skiss_schablon=False)
    with pytest.raises(LuftFel, match="aldrig tyst"):
        hamta_emissionsfaktorer()


def test_skiss_schablon_toml_har_korrekt_markning_och_interpolerar_deterministiskt() -> None:
    assert STANDARD_SKISS_SCHABLON_FIL.is_file()
    tabell = ladda_skiss_schablon(anvand_skiss_schablon=True)
    assert tabell.ar_schablon is True
    assert tabell.metodstatus == "skiss"
    assert tabell.varning is not None and "HBEFA" in tabell.varning
    assert len(tabell.sha256) == 64

    # Exakt uppslag 50 km/h
    l50 = tabell.hamta_faktor("lätt", 50.0)
    t50 = tabell.hamta_faktor("tung", 50.0)
    assert l50.fordonsklass == "latt"
    assert l50.nox_g_km == pytest.approx(0.19)
    assert t50.nox_g_km == pytest.approx(1.85)
    assert 0.0 < l50.direkt_no2_andel <= 1.0

    # Linjär interpolation vid 40 km/h (mitt emellan 30 och 50 km/h)
    l30 = tabell.hamta_faktor("latt", 30.0)
    l40 = tabell.hamta_faktor("latt", 40.0)
    assert l40.nox_g_km == pytest.approx(0.5 * (l30.nox_g_km + l50.nox_g_km))
    assert l40.pm10_slitage_g_km == pytest.approx(
        0.5 * (l30.pm10_slitage_g_km + l50.pm10_slitage_g_km)
    )

    # Klämning vid yttergränser (ingen vild extrapolering)
    assert tabell.hamta_faktor("latt", 15.0).nox_g_km == pytest.approx(l30.nox_g_km)


def test_lokal_csv_och_toml_med_sha256_verifiering(tmp_path: Path) -> None:
    csv_text = (
        "fordonsklass;hastighet_km_h;nox_g_km;pm10_avgas_g_km;pm10_slitage_g_km;direkt_no2_andel;kalla;ar\n"
        "lätt;50;0,15;0,003;0,040;0,22;Kommun HBEFA-utdrag;2025\n"
        "tung;50;1,40;0,015;0,210;0,13;Kommun HBEFA-utdrag;2025\n"
    )
    csv_fil = tmp_path / "kommun_emission.csv"
    csv_fil.write_text(csv_text, encoding="utf-8")
    ratt_sha = hashlib.sha256(csv_fil.read_bytes()).hexdigest()

    tab = las_emissionsfaktorer(csv_fil, forvantad_sha256=ratt_sha)
    assert tab.ar_schablon is False
    assert tab.sha256 == ratt_sha
    assert tab.ar == 2025
    assert tab.hamta_faktor("lätt", 50.0).nox_g_km == pytest.approx(0.15)
    assert tab.hamta_faktor("tung", 50.0).pm10_slitage_g_km == pytest.approx(0.21)

    # Fel SHA-256 ger tydligt LuftFel!
    with pytest.raises(LuftFel, match="SHA-256"):
        las_emissionsfaktorer(csv_fil, forvantad_sha256="0" * 64)

    # Lokal TOML-tabell fungerar lika rent via hamta_emissionsfaktorer
    toml_text = (
        'kalla = "Egen tabell"\n'
        "ar = 2025\n"
        "[[faktorer]]\n"
        'fordonsklass = "latt"\nhastighet_km_h = 50\nnox_g_km = 0.14\n'
        "pm10_avgas_g_km = 0.003\npm10_slitage_g_km = 0.045\ndirekt_no2_andel = 0.2\n"
        "[[faktorer]]\n"
        'fordonsklass = "tung"\nhastighet_km_h = 50\nnox_g_km = 1.30\n'
        "pm10_avgas_g_km = 0.014\npm10_slitage_g_km = 0.200\ndirekt_no2_andel = 0.12\n"
    )
    tab_toml = hamta_emissionsfaktorer(toml_text=toml_text)
    assert tab_toml.ar_schablon is False
    assert tab_toml.hamta_faktor("tung", 50.0).nox_g_km == pytest.approx(1.30)


def test_ogiltiga_emissionsfaktortabeller_avvisas() -> None:
    # Saknar tung fordonsklass
    with pytest.raises(LuftFel, match="tung"):
        las_emissionsfaktorer_csv(
            "fordonsklass,hastighet_km_h,nox_g_km,pm10_avgas_g_km,pm10_slitage_g_km,direkt_no2_andel,kalla,ar\n"
            "latt,50,0.2,0.004,0.05,0.2,Test,2024\n"
        )
    # Negativ emission avvisas
    with pytest.raises(LuftFel):
        las_emissionsfaktorer_csv(
            "fordonsklass,hastighet_km_h,nox_g_km,pm10_avgas_g_km,pm10_slitage_g_km,direkt_no2_andel,kalla,ar\n"
            "latt,50,-0.1,0.004,0.05,0.2,Test,2024\n"
            "tung,50,1.5,0.02,0.2,0.14,Test,2024\n"
        )
    # direkt_no2_andel > 1 avvisas
    with pytest.raises(LuftFel, match="direkt_no2_andel"):
        las_emissionsfaktorer_toml(
            'kalla = "Test"\nar = 2024\n'
            '[[faktorer]]\nfordonsklass = "latt"\nhastighet_km_h = 50\nnox_g_km = 0.2\n'
            "pm10_avgas_g_km = 0.004\npm10_slitage_g_km = 0.05\ndirekt_no2_andel = 1.5\n"
            '[[faktorer]]\nfordonsklass = "tung"\nhastighet_km_h = 50\nnox_g_km = 1.5\n'
            "pm10_avgas_g_km = 0.02\npm10_slitage_g_km = 0.2\ndirekt_no2_andel = 0.1\n"
        )
