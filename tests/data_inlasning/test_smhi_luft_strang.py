# SPDX-License-Identifier: GPL-3.0-or-later
"""100 % nätverksfria tester för `data_inlasning.smhi_luft_strang`.

All nätverksåtkomst ersätts med injicerade `hamtare` och syntetiska mätserier (inga riktiga
mätningar hämtas eller påstås representera faktiska årsmedelvärden).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from data_inlasning.smhi import (
    PARAM_NEDERBORD,
    PARAM_RELATIV_FUKTIGHET,
    PARAM_TEMPERATUR,
    PARAM_VINDHASTIGHET,
    PARAM_VINDRIKTNING,
)
from data_inlasning.smhi_luft_strang import (
    STRANG_PARAM_CIE_UV,
    STRANG_PARAM_DIREKT_NORMAL,
    STRANG_PARAM_GLOBAL,
    SmhiLuftStrangFel,
    bygg_strang_url,
    hamta_datavardluft_bakgrund,
    hamta_metobs_parameter_serie,
    hamta_smhi_vaderpaket,
    hamta_strang_sommarstralning,
    karna_mittpunkt_wgs84,
    kontrollera_smhi_url,
    las_bakgrund_luft_fil,
    las_datavardluft_csv_text,
    las_goteborg_luft_json_data,
    las_vaderserie_csv,
    serialisera_vaderserie_csv,
    tolka_strang_json,
    valj_narmaste_smhi_station,
)
from gemensamt.koordinater import EPSG_INTERN, transformera
from luft.emission import Trafikprofil, Vagavsnitt, berakna_emission
from luft.emission import testparametrar as nortrip_testparametrar


def _goteborg_karna_3011() -> tuple[float, float, float, float]:
    """En 500×500 m kärna i centrala Göteborg uttryckt i SWEREF 99 18 00 (EPSG:3011)."""
    e_c, n_c = transformera(11.9668, 57.7010, 4326, EPSG_INTERN)
    return (e_c - 250.0, n_c - 250.0, e_c + 250.0, n_c + 250.0)


def _bygg_syntetisk_smhi_csv(
    station_id: str,
    station_namn: str,
    param_namn: str,
    enhet: str,
    rader: list[tuple[str, str, str, str]],
) -> bytes:
    huvud = (
        f"\ufeffStationsnamn;Stationsnummer;Mätstationsgrupp\n"
        f"{station_namn};{station_id};SMHI\n"
        f"Parameternamn;Beskrivning;Enhet\n"
        f"{param_namn};timvärde;{enhet}\n\n"
        f"Datum;Tid (UTC);{param_namn};Kvalitet\n"
    )
    kropp = "\n".join(";".join(r) for r in rader) + "\n"
    return (huvud + kropp).encode("utf-8")


# ------------------------------------------------------------------ 1. Värd-allowlist & SSRF


@pytest.mark.parametrize(
    "ogiltig_url",
    [
        "",
        "http://opendata-download-metobs.smhi.se/api/version/1.0/parameter/1.json",
        "https://example.com/api/version/1.0/parameter/1.json",
        "https://opendata-download-metobs.smhi.se.evil.test/data.csv",
        "https://user:pass@opendata-download-metobs.smhi.se/data.csv",
        "https://127.0.0.1/api/version/1.0/parameter/1.json",
        "file:///etc/passwd",
    ],
)
def test_kontrollera_smhi_url_nekar_otillatna_vardar_och_protokoll(ogiltig_url: str) -> None:
    with pytest.raises(SmhiLuftStrangFel):
        kontrollera_smhi_url(ogiltig_url)


@pytest.mark.parametrize(
    "giltig_url",
    [
        "https://opendata-download-metobs.smhi.se/api/version/1.0/parameter/1.json",
        "https://opendata-download-metanalys.smhi.se/api/category/strang1g/version/1/geotype/point/lon/11.9700/lat/57.7000/parameter/117/data.json?from=2024-06-01&to=2024-08-31",
        "https://datavardluft.smhi.se/52North/api/v1/timeseries?expanded=true",
    ],
)
def test_kontrollera_smhi_url_godkanner_allowlist(giltig_url: str) -> None:
    assert kontrollera_smhi_url(giltig_url) == giltig_url


# ------------------------------------------------------------------ 2. MetObs + STRÅNG helhetskedja


def test_valj_narmaste_station_ignorerar_inaktiva_och_cachar(tmp_path: Path) -> None:
    karna = _goteborg_karna_3011()
    lon, lat = karna_mittpunkt_wgs84(karna)
    assert 11.9 < lon < 12.1 and 57.6 < lat < 57.8

    stationer_json = json.dumps(
        {
            "station": [
                {
                    "id": "71419",
                    "name": "Göteborg Inaktiv",
                    "latitude": 57.7010,
                    "longitude": 11.9668,
                    "height": 5.0,
                    "active": False,
                },
                {
                    "id": "71420",
                    "name": "Göteborg A",
                    "latitude": 57.7156,
                    "longitude": 11.9924,
                    "height": 5.0,
                    "active": True,
                },
                {
                    "id": "98210",
                    "name": "Stockholm A",
                    "latitude": 59.3417,
                    "longitude": 18.0549,
                    "height": 44.0,
                    "active": True,
                },
            ]
        }
    ).encode("utf-8")

    anrop: list[str] = []

    def falsk_hamtare(url: str) -> bytes:
        anrop.append(url)
        return stationer_json

    st1, dist1, sha1 = valj_narmaste_smhi_station(
        karna, PARAM_TEMPERATUR, cache_mapp=tmp_path, hamtare=falsk_hamtare
    )
    st2, dist2, sha2 = valj_narmaste_smhi_station(
        karna, PARAM_TEMPERATUR, cache_mapp=tmp_path, hamtare=falsk_hamtare
    )
    assert st1.id == "71420"
    assert st1.namn == "Göteborg A"
    assert 1.0 < dist1 < 5.0
    assert st1 == st2 and dist1 == dist2 and sha1 == sha2
    assert len(anrop) == 1  # andra anropet kom ur SHA-256-cachen


def test_hamta_smhi_vaderpaket_med_strang_vindros_utci_och_nortrip(tmp_path: Path) -> None:
    karna = _goteborg_karna_3011()

    # Bygg 48 syntetiska sommartimmar (2024-07-10..11) plus 1 felaktig rad (kvalitetsflagga O)
    # och 1 äldre år (2023) för att verifiera att år 2024 väljs rent och flagga O filtreras bort.
    rader_t: list[tuple[str, str, str, str]] = [("2023-07-01", "12:00:00", "22.0", "G")]
    rader_u: list[tuple[str, str, str, str]] = [("2023-07-01", "12:00:00", "3.0", "G")]
    rader_d: list[tuple[str, str, str, str]] = [("2023-07-01", "12:00:00", "270", "G")]
    rader_rh: list[tuple[str, str, str, str]] = [("2023-07-01", "12:00:00", "55", "G")]
    rader_ned: list[tuple[str, str, str, str]] = [("2023-07-01", "12:00:00", "0.0", "G")]
    strang_117: list[dict[str, Any]] = []
    strang_118: list[dict[str, Any]] = []

    t0 = datetime(2024, 7, 10, 0, 0, tzinfo=UTC)
    for h in range(48):
        dt = t0 + timedelta(hours=h)
        d_s = dt.strftime("%Y-%m-%d")
        t_s = dt.strftime("%H:%M:%S")
        iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        temp = 18.0 + (12.0 if 11 <= dt.hour <= 15 else 2.0) + (2.0 if h >= 24 else 0.0)
        vind = 0.2 if h % 12 == 0 else (2.0 + (h % 5))
        rikt = 270.0 if h % 2 == 0 else 225.0
        rh = 40.0 if temp >= 30.0 else 65.0
        ned = 1.2 if h == 3 else 0.0
        kval = "Y" if h % 7 == 0 else "G"

        rader_t.append((d_s, t_s, f"{temp:.1f}", kval))
        rader_u.append((d_s, t_s, f"{vind:.1f}", kval))
        rader_d.append((d_s, t_s, f"{rikt:.0f}", kval))
        rader_rh.append((d_s, t_s, f"{rh:.1f}", kval))
        rader_ned.append((d_s, t_s, f"{ned:.1f}", "G"))
        sol_g = 760.0 if 11 <= dt.hour <= 15 else 120.0
        sol_dn = 840.0 if 11 <= dt.hour <= 15 else 100.0
        strang_117.append({"date_time": iso, "value": sol_g})
        strang_118.append({"date_time": iso, "value": sol_dn})

    # Underkänd observation (flagga O, extremtemp 58 °C) ska filtreras bort helt!
    rader_t.append(("2024-07-12", "12:00:00", "58.0", "O"))

    station_payload = json.dumps(
        {
            "station": [
                {
                    "id": "71420",
                    "name": "Göteborg A",
                    "latitude": 57.7156,
                    "longitude": 11.9924,
                    "height": 5.0,
                    "active": True,
                }
            ]
        }
    ).encode("utf-8")

    csv_per_param = {
        PARAM_TEMPERATUR: _bygg_syntetisk_smhi_csv(
            "71420", "Göteborg A", "Lufttemperatur", "C", rader_t
        ),
        PARAM_VINDHASTIGHET: _bygg_syntetisk_smhi_csv(
            "71420", "Göteborg A", "Vindhastighet", "m/s", rader_u
        ),
        PARAM_VINDRIKTNING: _bygg_syntetisk_smhi_csv(
            "71420", "Göteborg A", "Vindriktning", "grader", rader_d
        ),
        PARAM_RELATIV_FUKTIGHET: _bygg_syntetisk_smhi_csv(
            "71420", "Göteborg A", "Relativ Luftfuktighet", "%", rader_rh
        ),
        PARAM_NEDERBORD: _bygg_syntetisk_smhi_csv(
            "71420", "Göteborg A", "Nederbördsmängd", "mm", rader_ned
        ),
    }

    def falsk_hamtare(url: str) -> bytes:
        if "strang1g" in url:
            assert f"/parameter/{STRANG_PARAM_CIE_UV}/" not in url
            if f"/parameter/{STRANG_PARAM_GLOBAL}/" in url:
                return json.dumps(strang_117).encode("utf-8")
            if f"/parameter/{STRANG_PARAM_DIREKT_NORMAL}/" in url:
                return json.dumps(strang_118).encode("utf-8")
        for pid, payload in csv_per_param.items():
            if f"/parameter/{pid}/station/71420/" in url:
                return payload
        if "/parameter/" in url and url.endswith(".json"):
            return station_payload
        raise AssertionError(f"Oväntad URL: {url}")

    paket = hamta_smhi_vaderpaket(
        karna,
        ar=2024,
        cache_mapp=tmp_path / "cache",
        hamtare=falsk_hamtare,
    )
    assert paket.station_id == "71420"
    assert paket.station_namn == "Göteborg A"
    assert paket.ar == 2024
    assert len(paket.vindros.sektorer) == 16
    assert paket.vindros.summa_andel_pct == pytest.approx(100.0)
    # 58 °C (flagga O) filtrerades bort explicit!
    assert paket.utci_sommar.t_max_c == pytest.approx(32.0)
    assert paket.utci_sommar.scenario_p95.t_luft_c >= 30.0
    assert paket.utci_sommar.scenario_p95.rh_pct == pytest.approx(40.0)
    assert paket.utci_sommar.scenario_p95.globalstralning_w_m2 == pytest.approx(760.0)
    assert paket.strang is not None
    assert paket.strang.global_p95_dag_w_m2 == pytest.approx(760.0)
    assert paket.strang.direkt_normal_p95_dag_w_m2 == pytest.approx(840.0)
    assert "metobs_temperatur" in paket.kallor_sha256
    assert "strang_global_117" in paket.kallor_sha256
    assert "strang_direkt_normal_118" in paket.kallor_sha256
    assert "strang_global_116" not in paket.kallor_sha256

    # Timvis väderserie round-trip CSV och kompatibel direkt med NORTRIP (`berakna_emission`).
    assert paket.vader_timserie.antal_timmar == 48
    aterlast = las_vaderserie_csv(serialisera_vaderserie_csv(paket.vader_timserie))
    assert aterlast.antal_timmar == 48
    vag = Vagavsnitt(
        id="haga-1",
        langd_m=150.0,
        adt_fordon_dygn=8000.0,
        andel_tung=0.06,
        andel_dubbdack_latt=0.0,
        andel_dubbdack_tung=0.0,
        hastighet_km_h=40.0,
        antal_korfalt=2,
        vagbredd_m=7.0,
    )
    with pytest.warns():
        em = berakna_emission([vag], aterlast, Trafikprofil.jamn(), nortrip_testparametrar())
    assert em.antal_timmar == 48
    assert em.massbalans_relfel() < 1e-10


def test_strang_url_och_tolkning_filtrerar_ogiltiga() -> None:
    assert STRANG_PARAM_CIE_UV == 116
    assert STRANG_PARAM_GLOBAL == 117
    assert STRANG_PARAM_DIREKT_NORMAL == 118

    with pytest.raises(SmhiLuftStrangFel, match="116"):
        bygg_strang_url(
            11.97, 57.70, STRANG_PARAM_CIE_UV, fran_datum="2024-06-01", till_datum="2024-08-31"
        )

    url = bygg_strang_url(
        11.97, 57.70, STRANG_PARAM_GLOBAL, fran_datum="2024-06-01", till_datum="2024-08-31"
    )
    assert url.startswith(
        "https://opendata-download-metanalys.smhi.se/api/category/strang1g/version/1/"
    )
    assert "/parameter/117/data.json?" in url

    url_dn = bygg_strang_url(
        11.97, 57.70, STRANG_PARAM_DIREKT_NORMAL, fran_datum="2024-06-01", till_datum="2024-08-31"
    )
    assert "/parameter/118/data.json?" in url_dn

    serie = tolka_strang_json(
        [
            {"date_time": "2024-07-01T12:00:00Z", "value": 720.0},
            {"date_time": "2024-07-01T13:00:00Z", "value": -5.0},
            {"date_time": "2024-07-01T14:00:00Z", "value": None},
        ],
        parameter_id=STRANG_PARAM_GLOBAL,
    )
    assert serie.antal_godkanda == 1
    assert serie.bortfiltrerade == 2
    assert "117" in serie.parameter_namn

    def strang_hamtare(u: str) -> bytes:
        if "/parameter/117/" in u:
            return json.dumps(
                [{"date_time": "2024-07-01T12:00:00Z", "value": 765.0}]
            ).encode("utf-8")
        raise SmhiLuftStrangFel("118 ej tillgänglig")

    res = hamta_strang_sommarstralning(11.97, 57.70, ar=2024, hamtare=strang_hamtare)
    assert res.global_p95_dag_w_m2 == pytest.approx(765.0)
    assert res.direkt_normal_serie is None


def test_metobs_parameter_serie_nekar_ogiltigt_format() -> None:
    with pytest.raises(SmhiLuftStrangFel):
        hamta_metobs_parameter_serie(PARAM_TEMPERATUR, "71420", format_typ="xml")


# ------------------------------------------------------------------ 3. Urban bakgrundsluft


def test_datavardluft_kort_timeout_gracios_fallback_aldrig_pahittade_varden() -> None:
    karna = _goteborg_karna_3011()

    def timeout_hamtare(_url: str) -> bytes:
        raise TimeoutError("timed out after 5s")

    res = hamta_datavardluft_bakgrund(karna, timeout_s=2.0, hamtare=timeout_hamtare)
    assert res.tillganglig is False
    assert res.amnen == {}
    assert len(res.varningar) >= 1
    assert "svarade inte" in res.varningar[0]


def test_datavardluft_52north_lyckad_hamtning() -> None:
    karna = _goteborg_karna_3011()
    ts_svar = [
        {
            "id": "ts-no2",
            "label": "NO2 Femman",
            "station": {
                "geometry": {"type": "Point", "coordinates": [11.970, 57.708]},
                "properties": {"id": "SE001", "label": "Femman"},
            },
            "parameters": {"phenomenon": {"label": "NO2"}},
        },
        {
            "id": "ts-pm10",
            "label": "PM10 Femman",
            "station": {
                "geometry": {"type": "Point", "coordinates": [11.970, 57.708]},
                "properties": {"id": "SE001", "label": "Femman"},
            },
            "parameters": {"phenomenon": {"label": "PM10"}},
        },
    ]
    t0_ms = int(datetime(2024, 3, 1, 0, 0, tzinfo=UTC).timestamp() * 1000)
    no2_data = {
        "values": [
            {"timestamp": t0_ms + i * 86_400_000, "value": 10.0 + i} for i in range(10)
        ]
    }
    pm10_data = {
        "values": [
            {"timestamp": t0_ms + i * 86_400_000, "value": 15.0 + 2.0 * i} for i in range(10)
        ]
    }

    def falsk_52n(url: str) -> bytes:
        if "timeseries?expanded=true" in url:
            return json.dumps(ts_svar).encode("utf-8")
        if "/ts-no2/getData" in url:
            return json.dumps(no2_data).encode("utf-8")
        if "/ts-pm10/getData" in url:
            return json.dumps(pm10_data).encode("utf-8")
        raise AssertionError(url)

    res = hamta_datavardluft_bakgrund(karna, hamtare=falsk_52n)
    assert res.tillganglig is True
    assert res.station_namn == "Femman"
    assert res.sha256 is not None
    assert "NO2" in res.amnen and "PM10" in res.amnen and "O3" not in res.amnen
    assert res.amnen["NO2"].arsmedel_ug_m3 == pytest.approx(14.5)
    assert res.amnen["PM10"].antal_dygn == 10
    assert res.amnen["PM10"].percentil_90_4_ug_m3 is not None


def test_deterministisk_filinlasare_csv_och_goteborg_json(tmp_path: Path) -> None:
    # 1. SMHI Datavärdskap CSV (med decimal-komma, semikolon och metadatarader)
    csv_text = (
        "Station;Femman Urban Bakgrund\n"
        "Stationsid;SE0004A\n"
        "Datum;NO2 (µg/m3);PM10 (µg/m3);O3 (µg/m3)\n"
        "2024-01-01;12,0;18,0;52,0\n"
        "2024-01-02;14,0;22,0;58,0\n"
        "2024-01-03;16,0;30,0;60,0\n"
        "2024-01-04;18,0;40,0;62,0\n"
        "2024-01-05;;-5,0;68,0\n"
    )
    csv_fil = tmp_path / "femman_2024.csv"
    csv_fil.write_text(csv_text, encoding="utf-8")
    r_csv = las_bakgrund_luft_fil(csv_fil, avstand_km=1.4)
    assert r_csv.tillganglig is True
    assert r_csv.station_namn == "Femman Urban Bakgrund"
    assert r_csv.station_id == "SE0004A"
    assert r_csv.avstand_km == pytest.approx(1.4)
    assert r_csv.sha256 is not None and len(r_csv.sha256) == 64
    assert r_csv.amnen["NO2"].arsmedel_ug_m3 == pytest.approx(15.0)
    assert r_csv.amnen["PM10"].arsmedel_ug_m3 == pytest.approx(27.5)
    assert r_csv.amnen["PM10"].bortfiltrerade == 1
    assert r_csv.amnen["O3"].arsmedel_ug_m3 == pytest.approx(60.0)
    assert r_csv.period_start == "2024-01-01"
    assert r_csv.period_slut == "2024-01-05"

    # 2. Göteborgs Miljöförvaltnings öppna JSON (via tolka_goteborg_luft_json)
    gbg_json = {
        "station": "Haga",
        "values": [
            {"time": "2024-05-01T08:00:00Z", "NO2": 20.0, "PM10": 16.0, "O3": 55.0},
            {"time": "2024-05-01T09:00:00Z", "NO2": 24.0, "PM10": 20.0, "O3": 61.0},
            {"time": "2024-05-02T08:00:00Z", "NO2": 30.0, "PM10": 32.0, "O3": 50.0},
            {"time": "2024-05-02T09:00:00Z", "NO2": None, "PM10": 28.0, "O3": 54.0},
        ],
    }
    json_fil = tmp_path / "haga_luft.json"
    json_fil.write_text(json.dumps(gbg_json), encoding="utf-8")
    r_json = las_bakgrund_luft_fil(json_fil, avstand_km=0.4)
    assert r_json.tillganglig is True
    assert r_json.station_namn == "Haga"
    assert r_json.amnen["NO2"].antal_dygn == 2
    assert r_json.amnen["PM10"].dygnsserie == (("2024-05-01", 18.0), ("2024-05-02", 30.0))
    assert r_json.amnen["O3"].arsmedel_ug_m3 == pytest.approx(55.0)
    assert r_json.sha256 is not None and len(r_json.sha256) == 64

    # Även direktanrop på CSV-text och Göteborg-dict fungerar deterministiskt.
    r_direkt = las_datavardluft_csv_text(csv_text)
    assert r_direkt.amnen["NO2"].arsmedel_ug_m3 == pytest.approx(15.0)
    r_gbg = las_goteborg_luft_json_data(gbg_json, station="Haga")
    assert r_gbg.amnen["PM10"].arsmedel_ug_m3 == pytest.approx(24.0)
