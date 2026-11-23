"""Integrationstester för Steg A i webb-API:t: intervall, CELF-rutt och SVF-intervall."""

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


def test_analys_intervall_pa_a1_och_ogiltiga_parametrar(tmp_path: Path) -> None:
    app = _skapa_app(tmp_path)
    status, data = _post_json(
        app,
        "/api/analys/intervall",
        {
            "omrade_id": "A1_enkel_sanka",
            "klimatfaktor_intervall": [0.9, 1.2],
            "initial_forlust_mm_intervall": [0.0, 2.0],
        },
    )
    assert status == 200
    assert data["format"] == "floden-vatten-intervall/v1"
    assert data["antal_korningar"] >= 2
    assert data["max_volymfel_rel"] < 1e-6
    sam = data["sammanfattning"]
    assert sam["antal_noder"] >= 1
    assert 0.0 <= sam["storsta_min_djup_m"] <= sam["storsta_max_djup_m"]

    # Ogiltigt intervall (min > max) ska ge 400
    bad_status, _ = _post_json(
        app,
        "/api/analys/intervall",
        {"omrade_id": "A1_enkel_sanka", "klimatfaktor_intervall": [1.5, 1.0]},
    )
    assert bad_status == 400


def test_rutt_analys_med_celf_insatser_och_bakatkompatibilitet(tmp_path: Path) -> None:
    app = _skapa_app(tmp_path)
    bas_payload = {
        "omrade_id": "A1_enkel_sanka",
        "tillat_testvarden": True,
        "gransdjup_m": 0.10,
        "fordon_typ": "slacksbil",
    }
    # Utan antal_insatser: oförändrat svar utan rekommenderade_insatser
    s0, d0 = _post_json(app, "/api/rutt", bas_payload)
    assert s0 == 200
    assert "rekommenderade_insatser" not in d0

    # Med antal_insatser > 0: kör CELF och returnerar prioriterad lista
    s1, d1 = _post_json(app, "/api/rutt", {**bas_payload, "antal_insatser": 3})
    assert s1 == 200
    assert "rekommenderade_insatser" in d1
    assert isinstance(d1["rekommenderade_insatser"], list)
    assert len(d1["rekommenderade_insatser"]) <= 3

    # Ogiltigt antal_insatser -> 400
    s_bad, _ = _post_json(app, "/api/rutt", {**bas_payload, "antal_insatser": -1})
    assert s_bad == 400


def test_analys_svf_intervall_pa_a8_och_ogiltiga_parametrar(tmp_path: Path) -> None:
    app = _skapa_app(tmp_path)
    status, data = _post_json(
        app,
        "/api/analys/svf-intervall",
        {
            "omrade_id": "A8_port_byggnad",
            "osakerhet_byggnad_m": 1.5,
            "antal_riktningar": 8,
            "svf_troskel": 0.85,
        },
    )
    assert status == 200
    assert data["format"] == "floden-dagsljus-svf-intervall/v1"
    assert 0.0 <= data["svf_min_medel"] <= data["svf_max_medel"] <= 1.0
    assert data["bredd_max"] >= 0.0
    assert data["mittlinje_max_horisont_rad"] >= 0.0

    # Ogiltigt antal_riktningar -> 400
    bad_status, _ = _post_json(
        app,
        "/api/analys/svf-intervall",
        {"omrade_id": "A8_port_byggnad", "antal_riktningar": 2},
    )
    assert bad_status == 400
