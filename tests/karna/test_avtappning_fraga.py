"""Avtappning som uttrycklig parameter i K20-frågor (samordnarens beslut 2026-11-22), med A11."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from karna.analyslosare import LosarFel
from karna.inbyggda import StatiskFyllning
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict

REPO = Path(__file__).resolve().parents[2]
A11 = REPO / "testsvit" / "A11_sluten_gard_avtappning" / "indata"
# Gården i A11 (rad/kolumn 24..63 i rutan med övre vänstra hörnet i (151000, 6576022)).
GARD = {
    "type": "Polygon",
    "coordinates": [[[151006.0, 6576006.0], [151016.0, 6576006.0], [151016.0, 6576016.0],
                     [151006.0, 6576016.0], [151006.0, 6576006.0]]],
}


def _kor(tmp: Path, analyser: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "a11",
            "scenarier": {"bas": {"bas": str(A11)}},
            "analyser": analyser,
        },
        tmp,
    )
    res = kor_utredning(fraga, ResultatCache(tmp / "cache"), tmp / "ut")
    return {j["analys"]: j["matvarden"] for j in res.sammanstallning["jobb"]}


def test_a11_via_fragan_dynamik_och_statisk(tmp_path: Path) -> None:
    regn = {"regn_tid_s": [0, 900], "regn_mm_h": [120, 120]}
    m = _kor(tmp_path, [
        {"id": "dyn", "losare": "vatten.dynamik", "parametrar": {
            **regn, "slut_tid_s": 2700, "avtappning_mm_h": 60, "takvatten": False}},
        {"id": "dyn_omr", "losare": "vatten.dynamik", "parametrar": {
            **regn, "slut_tid_s": 2700, "takvatten": False,
            "avtappning_omraden": [{"geometri": GARD, "mm_h": 60}]}},
        {"id": "stat", "losare": "vatten.statisk_fyllning", "parametrar": {
            "regn_mm": 30, "varaktighet_min": 15, "avtappning_mm_h": 60, "takvatten": False}},
        {"id": "noll", "losare": "vatten.statisk_fyllning", "parametrar": {
            "regn_mm": 30, "takvatten": False}},
    ])
    # (120 − 60) mm/h i 15 min = 15 mm i toppen, tomt vid 30 min; avtappat 3,0 m³.
    for a in ("dyn", "dyn_omr"):
        assert m[a]["max_djup_m"] == pytest.approx(0.015, abs=1e-7)
        assert m[a]["avtappat_m3"] == pytest.approx(3.0, rel=1e-12)
        assert m[a]["volymbalans"]["volymfel_rel"] < 1e-12
    assert m["dyn"]["avtappning_kalla"] == "angiven: 60 mm/h i hela området"
    assert "1 område" in m["dyn_omr"]["avtappning_kalla"]
    # Statiskt: tillståndet vid regnets slut, 30 − 60 · 0,25 = 15 mm; 1,5 m³ avtappat.
    assert m["stat"]["max_djup_m"] == pytest.approx(0.015, abs=1e-7)
    assert m["stat"]["avtappat_m3"] == pytest.approx(1.5, rel=1e-12)
    # Standard: 0 mm/h med källan i manifestet.
    assert m["noll"]["avtappning_mm_h"] == 0.0
    assert m["noll"]["avtappning_kalla"] == "standard: 0 mm/h (ledningsnätet antas fullt)"
    assert m["noll"]["max_djup_m"] == pytest.approx(0.030, abs=1e-7)


def test_avtappningsomraden_ur_fil(tmp_path: Path) -> None:
    fil = tmp_path / "avtappning.geojson"
    fil.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": GARD, "properties": {"avtappning_mm_h": 60}}]}))
    m = _kor(tmp_path, [
        {"id": "stat", "losare": "vatten.statisk_fyllning",
         "parametrar": {"regn_mm": 30, "varaktighet_min": 15, "takvatten": False},
         "indata": {"avtappning_omraden": str(fil)}},
    ])
    assert m["stat"]["avtappat_m3"] == pytest.approx(1.5, rel=1e-12)
    assert "avtappning.geojson" in m["stat"]["avtappning_kalla"]


def test_avtappning_kraver_varaktighet_och_ger_fel_vid_overlapp(tmp_path: Path) -> None:
    los = StatiskFyllning()
    with pytest.raises(LosarFel, match=">= 0"):
        los.validera_parametrar({"regn_mm": 30, "avtappning_mm_h": -1})
    with pytest.raises(LosarFel, match="takvatten_andel_ledning"):
        los.validera_parametrar({"regn_mm": 30, "takvatten_andel_ledning": 1.5})
    with pytest.raises(Exception, match="varaktighet"):
        _kor(tmp_path / "a", [{"id": "s", "losare": "vatten.statisk_fyllning",
                               "parametrar": {"regn_mm": 30, "avtappning_mm_h": 10}}])
    with pytest.raises(Exception, match="överlappar"):
        _kor(tmp_path / "b", [{"id": "s", "losare": "vatten.statisk_fyllning", "parametrar": {
            "regn_mm": 30, "varaktighet_min": 15, "avtappning_omraden": [
                {"geometri": GARD, "mm_h": 10}, {"geometri": GARD, "mm_h": 20}]}}])
