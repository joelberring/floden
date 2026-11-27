# SPDX-License-Identifier: GPL-3.0-or-later
"""Tester för additivt K46-lager `lager.vader_luft` (`geografi.vader_lager`)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from data_inlasning.smhi_luft_strang import las_datavardluft_csv_text
from floden_mcp.oppenhet import Oppenhet
from geografi.modell import KONTRAKT_GEOGRAFI, GeografiFel, lagra, skriv_manifest
from geografi.smhi_vader import (
    PARAM_RELATIV_FUKTIGHET,
    PARAM_TEMPERATUR,
    PARAM_VINDHASTIGHET,
    PARAM_VINDRIKTNING,
    VarmeboljaStatistik,
    Vindros16,
    berakna_varmebolja_utci,
    berakna_vindros_16,
    las_smhi_csv_text,
)
from geografi.vader_lager import (
    FIL_BAKGRUND_LUFT,
    FIL_UTCI_SOMMAR,
    FIL_VADER_TIMSERIE_GZ,
    FIL_VINDROS,
    VADER_LUFT_LICENS_SPDX,
    bygg_vader_lager,
    las_vader_lager,
)


def _syntetisk_vindros_och_utci() -> tuple[Vindros16, VarmeboljaStatistik]:
    csv_u = (
        "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
        "Datum;Tid (UTC);Vindhastighet;Kvalitet\n"
        "2024-07-10;12:00:00;2.0;G\n"
        "2024-07-10;13:00:00;4.0;G\n"
        "2024-07-10;14:00:00;3.0;G\n"
        "2024-07-10;15:00:00;1.5;G\n"
    )
    csv_d = (
        "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
        "Datum;Tid (UTC);Vindriktning;Kvalitet\n"
        "2024-07-10;12:00:00;270;G\n"
        "2024-07-10;13:00:00;265;G\n"
        "2024-07-10;14:00:00;225;G\n"
        "2024-07-10;15:00:00;270;G\n"
    )
    csv_t = (
        "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
        "Datum;Tid (UTC);Lufttemperatur;Kvalitet\n"
        "2024-07-10;12:00:00;26.0;G\n"
        "2024-07-10;13:00:00;28.5;G\n"
        "2024-07-10;14:00:00;31.0;G\n"
        "2024-07-10;15:00:00;29.5;G\n"
    )
    csv_rh = (
        "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
        "Datum;Tid (UTC);Relativ Luftfuktighet;Kvalitet\n"
        "2024-07-10;12:00:00;50;G\n"
        "2024-07-10;13:00:00;45;G\n"
        "2024-07-10;14:00:00;38;G\n"
        "2024-07-10;15:00:00;42;G\n"
    )
    su = las_smhi_csv_text(csv_u, parameter_id=PARAM_VINDHASTIGHET)
    sd = las_smhi_csv_text(csv_d, parameter_id=PARAM_VINDRIKTNING)
    st = las_smhi_csv_text(csv_t, parameter_id=PARAM_TEMPERATUR)
    sr = las_smhi_csv_text(csv_rh, parameter_id=PARAM_RELATIV_FUKTIGHET)
    return berakna_vindros_16(su, sd), berakna_varmebolja_utci(
        st, relativ_fuktighet=sr, vindhastighet=su
    )


def _skriv_k1_dummy_lager(geo_rot: Path, tmp: Path) -> str:
    m = tmp / "k1_dummy"
    m.mkdir(parents=True, exist_ok=True)
    (m / "dummy.txt").write_text("k1\n", encoding="utf-8")
    return lagra(geo_rot, m).sha256


def test_bygg_och_las_vader_lager_deterministisk_sha256_och_oppenhet(tmp_path: Path) -> None:
    geo_rot = tmp_path / "geo"
    karna = (-210_500.0, 6_400_000.0, -210_000.0, 6_400_500.0)
    vindros, utci = _syntetisk_vindros_och_utci()
    bakg = las_datavardluft_csv_text(
        "Station;Femman\nDatum;NO2;PM10;O3\n2024-01-01;14,0;18,0;58,0\n2024-01-02;16,0;22,0;62,0\n",
        avstand_km=1.2,
    )
    timserie_csv = (
        "tid_iso,temperatur_c,nederbord_mm_h,relativ_fuktighet_pct,vindhastighet_m_s\n"
        "2024-07-10T12:00,26.000,0.000,50.00,2.000\n"
        "2024-07-10T13:00,28.500,0.000,45.00,4.000\n"
    )
    strang_meta = {
        "global_p95_dag_w_m2": 720.0,
        "global_p99_dag_w_m2": 810.0,
        "direkt_normal_p95_dag_w_m2": 780.0,
        "direkt_normal_p99_dag_w_m2": 860.0,
        "kalla": "SMHI Öppna data (STRÅNG v1, CC BY 4.0). © SMHI.",
    }

    # Två separata byggen ska ge identisk trädhash (även med gzip-komprimerad timserie!).
    lager1, kallor1, varn1 = bygg_vader_lager(
        geo_rot,
        tmp_path / "bygge1",
        karna,
        vindros=vindros,
        utci_sommar=utci,
        bakgrund_luft=bakg,
        vader_timserie_csv=timserie_csv,
        villkor_kontrollerade=True,
        station_avstand_km=2.1,
        ar=2024,
        strang=strang_meta,
    )
    lager2, _, _ = bygg_vader_lager(
        geo_rot,
        tmp_path / "bygge2",
        karna,
        vindros=vindros,
        utci_sommar=utci,
        bakgrund_luft=bakg,
        vader_timserie_csv=timserie_csv,
        villkor_kontrollerade=True,
        station_avstand_km=2.1,
        ar=2024,
        strang=strang_meta,
    )
    assert lager1["vader_luft"]["sha256"] == lager2["vader_luft"]["sha256"]
    assert varn1 == []
    assert kallor1["vader_luft"]["licens"] == VADER_LUFT_LICENS_SPDX

    # Verifiera filer och K38 oppenhet.toml (öppen med CC-BY-4.0 och © SMHI).
    vl_mapp = geo_rot / "_lager" / lager1["vader_luft"]["sha256"]
    for filnamn in (FIL_VINDROS, FIL_UTCI_SOMMAR, FIL_BAKGRUND_LUFT, FIL_VADER_TIMSERIE_GZ):
        assert (vl_mapp / filnamn).is_file()
    opp_data = tomllib.loads((vl_mapp / "oppenhet.toml").read_text(encoding="utf-8"))
    assert opp_data["oppen"] is True
    assert opp_data["licens"] == "CC-BY-4.0"
    assert "© SMHI" in opp_data["kalla"]
    assert Oppenhet(tmp_path).bedom(vl_mapp).oppen is True

    # Skriv K46-manifest och läs tillbaka via `las_vader_lager`.
    k1_sha = _skriv_k1_dummy_lager(geo_rot, tmp_path / "k1tmp")
    mfil, _ = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "goteborg-haga",
            "version": "v1",
            "kommun": "goteborg",
            "karna_3011": list(karna),
            "lager": {"k1_rutnat": {"sha256": k1_sha}, **lager1},
            "kallor": {**kallor1},
            "oppen_for_mcp": True,
        },
    )
    last = las_vader_lager(mfil)
    assert last is not None
    assert last.station_id == "71420"
    assert last.station_namn == "Göteborg A"
    assert last.station_avstand_km == pytest.approx(2.1)
    assert last.ar == 2024
    assert last.har_vader_timserie is True
    assert last.las_vader_timserie_csv() == timserie_csv
    assert last.kalla_text_kort() == "SMHI Göteborg A, 2,1 km, 2024"

    forval = last.utci_forval()
    assert "platsens_smhi_p95" in forval and "platsens_smhi_p99" in forval
    assert forval["platsens_smhi_p95"]["globalstralning_w_m2"] == pytest.approx(720.0)
    assert forval["platsens_smhi_p99"]["direkt_normal_w_m2"] == pytest.approx(860.0)
    assert forval["platsens_smhi_p95"]["sol_direkt_w_m2"] == pytest.approx(780.0)

    # Manipulerad fil i lagret måste fångas av SHA-256-verifieringen!
    (vl_mapp / FIL_VINDROS).write_text(json.dumps({"manipulerad": True}), encoding="utf-8")
    with pytest.raises(GeografiFel, match="sha256"):
        las_vader_lager(mfil)


def test_vader_lager_bakatkompatibelt_och_stangt_utan_villkor(tmp_path: Path) -> None:
    geo_rot = tmp_path / "geo"
    karna = (150_000.0, 6_580_000.0, 150_500.0, 6_580_500.0)
    k1_sha = _skriv_k1_dummy_lager(geo_rot, tmp_path / "k1tmp")

    # 1. Äldre K46 v1-manifest utan `lager.vader_luft` -> returnerar None utan fel.
    mfil_gammal, _ = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "aldre-geo",
            "version": "v1",
            "kommun": "stockholm",
            "karna_3011": list(karna),
            "lager": {"k1_rutnat": {"sha256": k1_sha}},
            "oppen_for_mcp": True,
        },
    )
    assert las_vader_lager(mfil_gammal) is None

    # 2. `villkor_kontrollerade=False` -> stängt för MCP i oppenhet.toml.
    vindros, utci = _syntetisk_vindros_och_utci()
    lager, _, varn = bygg_vader_lager(
        geo_rot,
        tmp_path / "bygge_stangd",
        karna,
        vindros=vindros,
        utci_sommar=utci,
        bakgrund_luft=None,
        villkor_kontrollerade=False,
    )
    assert lager["vader_luft"]["har_bakgrund_luft"] is False
    assert lager["vader_luft"]["har_vader_timserie"] is False
    assert len(varn) >= 1
    vl_mapp = geo_rot / "_lager" / lager["vader_luft"]["sha256"]
    assert Oppenhet(tmp_path).bedom(vl_mapp).oppen is False
