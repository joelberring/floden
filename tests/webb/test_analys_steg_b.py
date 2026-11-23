"""Integrationstester för Steg B i webb-API:t: buller-CELF/skuggzon och luft-MKN."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from webb.api import FlodenApiApp


def _skapa_app(tmp_path: Path) -> FlodenApiApp:
    return FlodenApiApp(
        testsvit_rot=Path("testsvit"),
        konfig_fil=tmp_path / "saknas.toml",
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )


def _post_json(
    app: FlodenApiApp, sokvag: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    kropp = json.dumps(payload).encode("utf-8")
    status, _, svar_bytes = app.hantera_forfragan("POST", sokvag, kropp)
    return status, json.loads(svar_bytes.decode("utf-8"))


def test_analys_buller_skarmar_och_skuggzon(tmp_path: Path) -> None:
    app = _skapa_app(tmp_path)
    status, data = _post_json(
        app,
        "/api/analys/buller-skarmar",
        {
            "bas_niva_db": [63.0, 61.0, 58.0, 52.0],
            "skarm_reduktion_db": {
                "S_norr": [8.0, 6.0, 0.0, 0.0],
                "S_soder": [0.0, 2.0, 4.0, 0.0],
            },
            "k": 2,
            "riktvarde_db": 55.0,
            "avstand_m": [40.0, 180.0, 320.0, 500.0],
            "h_kalla_m": 0.5,
            "h_mottagare_m": 4.0,
        },
    )
    assert status == 200
    assert data["format"] == "floden-buller-optimering/v1"
    assert len(data["valda_skarmar"]) == 2
    assert data["valda_skarmar"][0]["skarm_id"] == "S_norr"
    assert data["valda_skarmar"][0]["marginal_vinst"] > 0.0
    assert len(data["akustiska_bifurkationer"]) == 4

    # Ogiltigt mål -> 400
    bad_status, _ = _post_json(
        app,
        "/api/analys/buller-skarmar",
        {
            "bas_niva_db": [60.0],
            "skarm_reduktion_db": {"S1": [5.0]},
            "mal": "okant_mal",
        },
    )
    assert bad_status == 400


def test_analys_luft_mkn_intervall_och_validering(tmp_path: Path) -> None:
    app = _skapa_app(tmp_path)
    # 60 dygn * 24 timmar: 40 dygn med 42 ug/m3 lokal + 10 ug/m3 bakgrund
    timmar = [42.0] * (40 * 24) + [15.0] * (20 * 24)
    status, data = _post_json(
        app,
        "/api/analys/luft-mkn",
        {
            "timvis_lokal_ug_m3": timmar,
            "bakgrund_ug_m3": 10.0,
            "emissionsfaktor_intervall": [0.85, 1.15],
            "depositionsfaktor_intervall": [0.95, 1.05],
        },
    )
    assert status == 200
    assert data["format"] == "floden-luft-mkn-intervall/v1"
    assert data["min_dygn_over_grans"] <= data["max_dygn_over_grans"]
    assert data["max_dygn_over_grans"] >= 40
    assert data["min_arsmedel_ug_m3"] <= data["max_arsmedel_ug_m3"]

    # För kort serie (< 24 timmar) -> 400
    bad_status, _ = _post_json(
        app,
        "/api/analys/luft-mkn",
        {"timvis_lokal_ug_m3": [10.0, 20.0]},
    )
    assert bad_status == 400
