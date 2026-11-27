# SPDX-License-Identifier: GPL-3.0-or-later
"""Tester för SMHI väder-/luftlager, UTCI-förval (`platsens_smhi_p95`/`p99`) och Luft-skiss."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from data_inlasning.smhi_luft_strang import las_datavardluft_csv_text
from demovarld.varld import DemoVarld, bygg_ruta_vatten
from geografi.modell import KONTRAKT_GEOGRAFI, lagra, skriv_manifest
from geografi.smhi_vader import (
    PARAM_RELATIV_FUKTIGHET,
    PARAM_TEMPERATUR,
    PARAM_VINDHASTIGHET,
    PARAM_VINDRIKTNING,
    berakna_varmebolja_utci,
    berakna_vindros_16,
    las_smhi_csv_text,
)
from geografi.vader_lager import bygg_vader_lager
from testsvit.k1_io import las_geotiff
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel
from webb.gyf_utci import (
    FORMAT_LUFT_SKISS_WEBB,
    FORMAT_VADER_LUFT_WEBB,
    hamta_projekt_vader_luft,
    kor_luft_skiss_analys,
    kor_varmestress_analys,
)
from webb.projektkalla import KONTRAKT_PROJEKT


def _bygg_syntetisk_geo_med_vader(
    rot: Path,
    *,
    med_vader_lager: bool = True,
    med_bakgrund: bool = True,
    med_timserie: bool = True,
) -> tuple[FlodenApiApp, Path, Path, str]:
    """Bygger en 100 % lokal geografi + projekt (med/utan `lager.vader_luft`) och `FlodenApiApp`."""
    geo_rot = rot / "geo"
    proj_rot = rot / "projekt"

    varld = DemoVarld()
    k1_tmp = rot / "bygge_k1"
    bygg_ruta_vatten(varld, k1_tmp)
    (k1_ruta,) = sorted(k1_tmp.glob("mm_*"))
    _, geo_info = las_geotiff(k1_ruta / "mark.tif")
    x0, y0 = float(geo_info.x_min), float(geo_info.y_min)
    x1, y1 = float(geo_info.x_max), float(geo_info.y_max)
    karna = (x0 + 10.0, y0 + 10.0, x1 - 10.0, y1 - 10.0)
    k1_lager = lagra(geo_rot, k1_tmp)

    lager_dict: dict[str, Any] = {"k1_rutnat": {"sha256": k1_lager.sha256}}
    kallor_dict: dict[str, Any] = {
        "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
        "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
    }

    if med_vader_lager:
        su = las_smhi_csv_text(
            "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
            "Datum;Tid (UTC);Vindhastighet;Kvalitet\n"
            "2024-07-10;12:00:00;2.0;G\n"
            "2024-07-10;13:00:00;3.5;G\n"
            "2024-07-10;14:00:00;1.8;G\n"
            "2024-07-10;15:00:00;4.2;G\n",
            parameter_id=PARAM_VINDHASTIGHET,
        )
        sd = las_smhi_csv_text(
            "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
            "Datum;Tid (UTC);Vindriktning;Kvalitet\n"
            "2024-07-10;12:00:00;270;G\n"
            "2024-07-10;13:00:00;260;G\n"
            "2024-07-10;14:00:00;225;G\n"
            "2024-07-10;15:00:00;270;G\n",
            parameter_id=PARAM_VINDRIKTNING,
        )
        st = las_smhi_csv_text(
            "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
            "Datum;Tid (UTC);Lufttemperatur;Kvalitet\n"
            "2024-07-10;12:00:00;25.0;G\n"
            "2024-07-10;13:00:00;28.0;G\n"
            "2024-07-10;14:00:00;31.5;G\n"
            "2024-07-10;15:00:00;32.5;G\n",
            parameter_id=PARAM_TEMPERATUR,
        )
        srh = las_smhi_csv_text(
            "Stationsnamn;Stationsnummer\nGöteborg A;71420\n"
            "Datum;Tid (UTC);Relativ Luftfuktighet;Kvalitet\n"
            "2024-07-10;12:00:00;52;G\n"
            "2024-07-10;13:00:00;44;G\n"
            "2024-07-10;14:00:00;36;G\n"
            "2024-07-10;15:00:00;34;G\n",
            parameter_id=PARAM_RELATIV_FUKTIGHET,
        )
        bakg = (
            las_datavardluft_csv_text(
                "Station;Femman\nDatum;NO2;PM10;O3\n"
                "2024-01-01;14,0;18,0;56,0\n"
                "2024-01-02;16,0;24,0;60,0\n",
                avstand_km=1.1,
            )
            if med_bakgrund
            else None
        )
        timserie = (
            "tid_iso,temperatur_c,nederbord_mm_h,relativ_fuktighet_pct,vindhastighet_m_s\n"
            + "\n".join(
                f"2024-07-10T{h:02d}:00,24.000,{0.5 if h == 2 else 0.0:.3f},45.00,3.200"
                for h in range(24)
            )
            + "\n"
            if med_timserie
            else None
        )
        vl_lager, vl_kallor, _ = bygg_vader_lager(
            geo_rot,
            rot / "bygge_vl",
            karna,
            vindros=berakna_vindros_16(su, sd),
            utci_sommar=berakna_varmebolja_utci(st, relativ_fuktighet=srh, vindhastighet=su),
            bakgrund_luft=bakg,
            vader_timserie_csv=timserie,
            villkor_kontrollerade=True,
            station_avstand_km=2.1,
            ar=2024,
            strang={
                "global_p95_dag_w_m2": 740.0,
                "global_p99_dag_w_m2": 820.0,
                "direkt_normal_p95_dag_w_m2": 790.0,
                "direkt_normal_p99_dag_w_m2": 860.0,
                "kalla": "SMHI Öppna data (STRÅNG v1, CC BY 4.0). © SMHI.",
            },
        )
        lager_dict.update(vl_lager)
        kallor_dict.update(vl_kallor)

    _, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "goteborg-test",
            "version": "v1",
            "kommun": "goteborg",
            "karna_3011": list(karna),
            "gransen_3011": [x0, y0, x1, y1],
            "lager": lager_dict,
            "kallor": kallor_dict,
            "oppen_for_mcp": True,
        },
    )

    pmapp = proj_rot / "haga-proj"
    (pmapp / "fragor").mkdir(parents=True, exist_ok=True)
    (pmapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
        encoding="utf-8",
    )
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": "haga-proj",
                "namn": "Göteborg Haga",
                "kommun": "goteborg",
                "geografi": "goteborg-test@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": [x0 + 40.0, y0 + 40.0, x1 - 40.0, y1 - 40.0],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    k1_lager_mapp = geo_rot / "_lager" / k1_lager.sha256
    (pmapp / "fragor" / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\n'
        'fraga_id = "haga-skyfall"\n'
        'beskrivning = "Haga skyfall"\n\n'
        f'[scenarier.fore]\nbas = "{k1_lager_mapp.as_posix()}"\n\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 40.0 }\n",
        encoding="utf-8",
    )

    konfig = rot / "config.toml"
    konfig.write_text(
        '[webb]\ndemomapp = "varld"\nfragemappar = ["fragor"]\ncachemapp = "cache"\n'
        'korningsmapp = "korningar"\nklientmapp = "klient"\n',
        encoding="utf-8",
    )
    (rot / "tom-testsvit").mkdir(exist_ok=True)
    app = FlodenApiApp(
        testsvit_rot=rot / "tom-testsvit",
        konfig_fil=konfig,
        geomapp=geo_rot,
        projektmapp=proj_rot,
        kartverktygsmapp=rot / "kartverktyg",
    )
    return app, pmapp, geo_rot, "haga-proj"


def test_hamta_projekt_vader_luft_och_utci_platsens_forval(tmp_path: Path) -> None:
    app, pmapp, geo_rot, pid = _bygg_syntetisk_geo_med_vader(tmp_path, med_vader_lager=True)

    # 1. Serverhjälpare `hamta_projekt_vader_luft`
    info = hamta_projekt_vader_luft(pmapp, geo_rot=geo_rot)
    assert info["format"] == FORMAT_VADER_LUFT_WEBB
    assert info["har_vader_luft"] is True
    assert info["station"]["namn"] == "Göteborg A"
    assert info["station"]["avstand_km"] == pytest.approx(2.1)
    assert info["station"]["ar"] == 2024
    assert len(info["vindros"]["sektorer"]) == 16
    assert info["bakgrund_luft"]["tillganglig"] is True
    assert info["utci_forval"]["platsens_smhi_p95"]["globalstralning_w_m2"] == pytest.approx(740.0)

    # 2. UTCI-analys exponerar `platsens_vader` och tillämpar `forval="platsens_smhi_p95"`
    svar = kor_varmestress_analys(
        app.kartverktyg,
        app.projekt,
        {"omrade_id": pid, "forval": "platsens_smhi_p95"},
    )
    assert svar["forval"] == "platsens_smhi_p95"
    assert svar["platsens_vader"] is not None
    assert svar["platsens_vader"]["station_namn"] == "Göteborg A"
    assert svar["platsens_vader"]["station_avstand_km"] == pytest.approx(2.1)
    assert svar["platsens_vader"]["ar"] == 2024
    p95_temp = info["utci_forval"]["platsens_smhi_p95"]["t_luft_c"]
    assert svar["parametrar"]["t_luft_c"] == pytest.approx(p95_temp)
    assert svar["parametrar"]["sol_direkt_w_m2"] == pytest.approx(790.0)
    assert svar["utci_medel_c"] is not None


def test_utci_och_vader_luft_utan_vaderlager_kraschar_inte(tmp_path: Path) -> None:
    app, pmapp, geo_rot, pid = _bygg_syntetisk_geo_med_vader(tmp_path, med_vader_lager=False)

    info = hamta_projekt_vader_luft(pmapp, geo_rot=geo_rot)
    assert info["har_vader_luft"] is False
    assert info["vindros"] is None
    assert len(info["varningar"]) >= 1

    svar = kor_varmestress_analys(app.kartverktyg, app.projekt, {"omrade_id": pid})
    assert svar["platsens_vader"] is None

    with pytest.raises(FlodenWebbFel) as exc_info:
        kor_varmestress_analys(
            app.kartverktyg,
            app.projekt,
            {"omrade_id": pid, "forval": "platsens_smhi_p95"},
        )
    assert exc_info.value.status_kod == 400


def test_kor_luft_skiss_analys_full_kedja_nortrip_kemi_och_val3a_sparr(tmp_path: Path) -> None:
    _, pmapp, geo_rot, _ = _bygg_syntetisk_geo_med_vader(
        tmp_path, med_vader_lager=True, med_bakgrund=True, med_timserie=True
    )
    vag = [
        {
            "id": "sprangkullsgatan",
            "gatunamn": "Sprängkullsgatan",
            "langd_m": 220.0,
            "adt_fordon_dygn": 12_000,
            "hastighet_km_h": 50.0,
            "andel_tung_pct": 8.0,
        }
    ]

    # 1. Utan emissionskälla och utan aktivt schablonval -> 400 (val 3A!)
    with pytest.raises(FlodenWebbFel) as exc_3a:
        kor_luft_skiss_analys(pmapp, {"vagsegment": vag}, geo_rot=geo_rot)
    assert exc_3a.value.status_kod == 400

    # 2. Saknad ÅDT -> 400 (0 påhittade värden!)
    with pytest.raises(FlodenWebbFel) as exc_adt:
        kor_luft_skiss_analys(
            pmapp,
            {
                "vagsegment": [
                    {"id": "g1", "langd_m": 100.0, "hastighet_km_h": 50.0, "andel_tung_pct": 5.0}
                ],
                "anvand_skiss_schablon": True,
            },
            geo_rot=geo_rot,
        )
    assert exc_adt.value.status_kod == 400

    # 3. Med aktivt skiss-schablonval -> NORTRIP + fotostationär NO2 + deterministiska resultat!
    res1 = kor_luft_skiss_analys(
        pmapp, {"vagsegment": vag, "anvand_skiss_schablon": True}, geo_rot=geo_rot
    )
    res2 = kor_luft_skiss_analys(
        pmapp, {"vagsegment": vag, "anvand_skiss_schablon": True}, geo_rot=geo_rot
    )
    assert res1 == res2  # 100 % deterministisk
    assert res1["format"] == FORMAT_LUFT_SKISS_WEBB
    assert res1["metodstatus"] == "skiss"
    assert res1["ar_schablon"] is True
    assert res1["nortrip_kord"] is True
    assert res1["nortrip_antal_timmar"] == 24
    assert res1["no2_kemi_metod"] == "fotostationar_no_o3_no2"
    assert res1["lokalt_tillskott"]["nox_arsmedel_ug_m3"] > 0.0
    assert res1["lokalt_tillskott"]["no2_arsmedel_ug_m3"] > 0.0
    assert res1["lokalt_tillskott"]["pm10_arsmedel_ug_m3"] > 0.0
    assert res1["bakgrund"]["no2_arsmedel_ug_m3"] == pytest.approx(15.0)
    assert res1["totalt"]["no2_arsmedel_ug_m3"] > 15.0
    assert res1["totalt"]["pm10_arsmedel_ug_m3"] > 21.0
    assert res1["vindros_param"]["station_namn"] == "Göteborg A"

    # 4. Med egen lokal CSV-tabell (inte schablon)
    egen_csv = (
        "fordonsklass;hastighet_km_h;nox_g_km;pm10_avgas_g_km;pm10_slitage_g_km;direkt_no2_andel;kalla;ar\n"
        "lätt;50;0,12;0,002;0,035;0,18;Göteborg HBEFA-utdrag;2025\n"
        "tung;50;1,10;0,012;0,180;0,12;Göteborg HBEFA-utdrag;2025\n"
    )
    res_egen = kor_luft_skiss_analys(
        pmapp, {"vagsegment": vag, "emissionsfaktorer_csv": egen_csv}, geo_rot=geo_rot
    )
    assert res_egen["ar_schablon"] is False
    assert res_egen["metodstatus"] == "skiss"
    nox_egen = res_egen["lokalt_tillskott"]["nox_arsmedel_ug_m3"]
    nox_schablon = res1["lokalt_tillskott"]["nox_arsmedel_ug_m3"]
    assert nox_egen < nox_schablon


def test_kor_luft_skiss_saknad_bakgrund_och_celltak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, pmapp, geo_rot, _ = _bygg_syntetisk_geo_med_vader(
        tmp_path, med_vader_lager=True, med_bakgrund=False, med_timserie=False
    )
    vag = [
        {
            "id": "v1",
            "koordinater": [[150000.0, 6580000.0], [150150.0, 6580000.0]],
            "adt_fordon_dygn": 6000,
            "hastighet_km_h": 40.0,
            "andel_tung_pct": 5.0,
        }
    ]
    res = kor_luft_skiss_analys(
        pmapp, {"vagsegment": vag, "anvand_skiss_schablon": True}, geo_rot=geo_rot
    )
    assert res["nortrip_kord"] is False
    assert res["bakgrund"]["no2_arsmedel_ug_m3"] is None
    assert res["totalt"]["no2_arsmedel_ug_m3"] is None
    assert res["totalt"]["pm10_arsmedel_ug_m3"] is None
    assert any("saknas" in v.lower() for v in res["varningar"])

    # CX23-tak för antal vägsegment
    monkeypatch.setenv("FLODEN_MAX_LUFT_SKISS_SEGMENT", "2")
    många_vag = [{**vag[0], "id": f"v{i}"} for i in range(3)]
    with pytest.raises(FlodenWebbFel) as exc_tak:
        kor_luft_skiss_analys(
            pmapp,
            {"vagsegment": många_vag, "anvand_skiss_schablon": True},
            geo_rot=geo_rot,
        )
    assert exc_tak.value.status_kod == 400
