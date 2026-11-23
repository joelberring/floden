"""Tester för Göteborgs öppna testområde, baskarteprofil och datahämtare (Pass 2)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from data_inlasning.goteborg import (
    EPSG_GOTEBORG,
    GOTEBORG_HAGA_TESTOMRADE,
    GoteborgDataFel,
    hamta_goteborg_wfs_geojson,
    tolka_goteborg_luft_json,
)
from markmodell.lagerprofil import las_lagerprofil

REPO_ROT = Path(__file__).resolve().parents[2]


def test_goteborg_baskarta_lagerprofil_laddas_rent() -> None:
    """data/lagerprofiler/goteborg_baskarta.toml ska följa markmodell.lagerprofil-schemat."""
    profil = las_lagerprofil(REPO_ROT / GOTEBORG_HAGA_TESTOMRADE.lagerprofil_rel)
    assert profil.namn == "goteborg_baskarta"
    assert profil.version == 1
    assert len(profil.linjeregler) >= 10
    assert len(profil.ytregler) >= 8


def test_goteborg_testomrade_epsg_3007_till_wgs84() -> None:
    """Haga testområde (EPSG:3007) ska omvandlas till centrala Göteborg i WGS 84."""
    assert GOTEBORG_HAGA_TESTOMRADE.epsg == EPSG_GOTEBORG
    lon0, lat0, lon1, lat1 = GOTEBORG_HAGA_TESTOMRADE.bbox_wgs84()
    assert 11.85 < lon0 < lon1 < 12.10
    assert 57.65 < lat0 < lat1 < 57.76


def test_goteborg_wfs_cache_och_domanskydd(tmp_path: Path) -> None:
    """WFS-klienten ska cacha lokalt och neka URL:er utanför goteborg.se."""
    with pytest.raises(GoteborgDataFel):
        hamta_goteborg_wfs_geojson(
            "byggnader",
            GOTEBORG_HAGA_TESTOMRADE.bbox_3007,
            wfs_bas_url="https://example.com/wfs",
        )

    anrop: list[str] = []

    def falsk_hamta(url: str) -> bytes:
        anrop.append(url)
        return json.dumps(
            {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}}]}
        ).encode("utf-8")

    fc1 = hamta_goteborg_wfs_geojson(
        "baskarta:byggnader",
        GOTEBORG_HAGA_TESTOMRADE.bbox_3007,
        cache_mapp=tmp_path,
        hamta_bytes=falsk_hamta,
    )
    fc2 = hamta_goteborg_wfs_geojson(
        "baskarta:byggnader",
        GOTEBORG_HAGA_TESTOMRADE.bbox_3007,
        cache_mapp=tmp_path,
        hamta_bytes=falsk_hamta,
    )
    assert len(anrop) == 1  # andra anropet kom ur lokal cache
    assert fc1 == fc2
    assert len(fc1["features"]) == 1


def test_tolka_goteborg_luft_json() -> None:
    """Öppna timvisa mätdata (PM10, PM2.5, NO2) ska tolkas rent med NaN för saknade värden."""
    ra = {
        "values": [
            {"time": "2026-03-15T08:00:00Z", "PM10": 32.5, "PM2.5": 9.1, "NO2": 28.0},
            {"time": "2026-03-15T09:00:00Z", "PM10": 41.0, "PM2.5": None, "NO2": 34.2},
        ]
    }
    serie = tolka_goteborg_luft_json(ra, station="Haga")
    assert serie.station == "Haga"
    assert len(serie.tider_iso) == 2
    assert serie.pm10_ug_m3[0] == pytest.approx(32.5)
    assert np.isnan(serie.pm25_ug_m3[1])
    assert serie.no2_ug_m3[1] == pytest.approx(34.2)


def test_goteborg_luft_till_mkn_intervall_och_r2_sammanfattning() -> None:
    """Helhetskedja: Göteborgs öppna timdata -> monotont MKN-intervall -> R2-rapport."""
    from luft.kanslighet import kor_monoton_mkn_intervall
    from rapport.r2_domaner import skapa_r2_fleromans_sammanfattning

    # 365 dygn * 24 timmar med vintersäsongstoppar (dubbdäck/sandning)
    poster = []
    for dygn in range(365):
        bas_pm10 = 42.0 if dygn < 45 else 18.0
        for tim in range(24):
            poster.append(
                {
                    "time": f"2026-01-01T{tim:02d}:00:00Z",
                    "PM10": bas_pm10,
                    "PM2.5": 8.0,
                    "NO2": 22.0,
                }
            )
    serie = tolka_goteborg_luft_json({"values": poster}, station="Femman")
    iv = kor_monoton_mkn_intervall(
        serie.pm10_ug_m3,
        bakgrund_ug_m3=10.0,
        emissionsfaktor_intervall=(0.85, 1.15),
        depositionsfaktor_intervall=(0.95, 1.05),
    )
    assert iv.min_dygn_over_grans <= iv.max_dygn_over_grans
    assert iv.max_dygn_over_grans >= 45

    sam = skapa_r2_fleromans_sammanfattning(
        GOTEBORG_HAGA_TESTOMRADE.namn,
        mkn_intervall=[("Göteborg – Femman", iv)],
    )
    assert "Göteborg – Femman" in sam["markdown"]
    assert sam["domaner"]["luft"]["mkn_platser"][0]["dygn_over_50_intervall"] == [
        iv.min_dygn_over_grans,
        iv.max_dygn_over_grans,
    ]
