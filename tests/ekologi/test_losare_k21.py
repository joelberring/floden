"""K21-lösaren `ekologi.konnektivitet`, kontrakten K44 och K45 och webb-API:t (litet demoläge).

Facittest G7: ett scenario med ett nytt hus ger ΔECA <= 0 genom hela kedjan (K9 -> K1 -> mosaik ->
kärnan), inte bara i kärnans rena rasterfall (G6)."""

from __future__ import annotations

import inspect
import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tests.demovarld.test_demo import Demo

from ekologi import (
    STANDARD_ARTPROFILER,
    EkologiFel,
    artprofil_fran_dict,
    artprofil_till_dict,
    las_artprofil_fil,
)
from karna.analyslosare import LosarFel, hamta_losare
from karna.ekologi_losare import las_markslagskonfig
from testsvit.k1_io import las_geotiff
from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]
KONTRAKT = (REPO / "docs" / "DATAKONTRAKT.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def demo() -> Iterator[Demo]:
    rot = Path(tempfile.mkdtemp())
    try:
        yield Demo(rot)
    finally:
        shutil.rmtree(rot, ignore_errors=True)


def _avsnitt(rubrikstart: str) -> str:
    start = KONTRAKT.index(f"\n## {rubrikstart}")
    slut = KONTRAKT.find("\n## ", start + 4)
    return KONTRAKT[start : slut if slut != -1 else None]


def _namn_efter(text: str, start: str) -> set[str]:
    """Första kolumnens `namn` i tabellen som följer på raden som börjar med `start`."""
    i = text.index(start)
    ut: set[str] = set()
    for rad in text[i:].splitlines()[1:]:
        if rad.startswith("| `"):
            ut.update(re.findall(r"`([^`]+)`", rad.split("|")[1]))
        elif ut and not rad.startswith("|"):
            break
    return ut


def _tabell(demo: Demo) -> dict[str, dict[str, Any]]:
    res = demo.tjanst.resultat("demo-ekologi")
    tab = next(t for t in res["tabeller"] if t["losare"] == "ekologi.konnektivitet")
    return {r["scenario"]: r["varden"] for r in tab["rader"]}


def test_g7_nytt_hus_ger_delta_eca_inte_positiv_och_intervallen_innesluter(demo: Demo) -> None:
    v = _tabell(demo)
    assert v["efter"]["eca_m2"] <= v["fore"]["eca_m2"]
    assert v["efter"]["total_habitat_area_m2"] < v["fore"]["total_habitat_area_m2"]
    for s in v.values():
        assert s["eca_min_m2"] <= s["eca_m2"] <= s["eca_max_m2"]
        assert s["pc_min"] <= s["pc"] <= s["pc_max"]
        assert s["antal_flackar"] >= 2
    # Monotona intervall: samma hus ger samma ordning i båda extremkörningarna.
    assert v["efter"]["eca_min_m2"] <= v["fore"]["eca_min_m2"]
    assert v["efter"]["eca_max_m2"] <= v["fore"]["eca_max_m2"]


def test_k45_ekologi_json_stammer_med_kontraktet_och_rastren_ar_kontraktsenliga(demo: Demo) -> None:
    namn = _namn_efter(_avsnitt("K45 "), "Fälten i `ekologi.json`:")
    assert namn  # tabellen hittades
    for sc in ("fore", "efter"):
        mapp = demo.tjanst.korningsmapp / "demo-ekologi" / "resultat" / "konnektivitet" / sc
        d = json.loads((mapp / "ekologi.json").read_text(encoding="utf-8"))
        assert set(d) == namn
        assert d["format"] == "floden-ekologi-konnektivitet/v1"
        assert d["metodstatus"] == "skiss" and d["artprofil_id"] == "pollinerare"
        assert d["artprofil"] == artprofil_till_dict(STANDARD_ARTPROFILER["pollinerare"])
        assert "saknar exakta källhänvisningar" in d["parameterkallor"]
        assert d["antal_flackar"] == len(d["dPC"])
        lo, hi = d["ECA_intervall_m2"]
        assert lo <= d["ECA_m2"] <= hi
        lo, hi = d["PC_intervall"]
        assert lo <= d["PC"] <= hi
        stycke = _avsnitt("K45 ").split("`dPC[]`:")[1].split("\n\n")[0]
        dpc_namn = set(re.findall(r"`([A-Za-z_0-9]+)`", stycke))
        assert set(d["dPC"][0]) <= dpc_namn
        for p in d["dPC"]:
            assert p["dPC_intra_procent"] + p["dPC_flux_procent"] + p["dPC_connector_procent"] == (
                pytest.approx(p["dPC_procent"], abs=1e-6)
            )
        j, geo = las_geotiff(mapp / "stromtathet.tif")
        assert j.dtype == np.float32 and geo.epsg == 3011
        assert float(np.nanmin(j)) >= 0.0 and float(np.nanmax(j)) <= 1.0
        kg, _ = las_geotiff(mapp / "kansighet.tif")
        assert kg.shape == j.shape and float(np.nanmax(kg)) <= 0.0


def test_stromtathet_skillnadslager_finns_och_projektet_har_ekologivy(demo: Demo) -> None:
    res = demo.tjanst.resultat("demo-ekologi")
    assert any(la["typ"] == "skillnad" and la["roll"] == "stromtathet" for la in res["lager"])
    status, svar = demo.get("/api/projekt/demokvarteret")
    assert status == 200
    assert any(v["avsnitt"] == "ekologi" for v in svar["vyer"])
    assert any(a["id"] == "ekologi" for a in svar["avsnitt"])


def test_losaren_ar_registrerad_i_domanen_ekologi_och_ger_tydliga_parameterfel() -> None:
    losare = hamta_losare("ekologi.konnektivitet")
    assert losare.doman == "ekologi" and losare.paverkansradie_m({}) is None
    with pytest.raises(LosarFel, match="okänd artprofil"):
        losare.validera_parametrar({"artprofil": "hjorthornsbagge"})
    with pytest.raises(LosarFel, match="okända parametrar"):
        losare.validera_parametrar({"artprofil": "pollinerare", "x": 1})
    with pytest.raises(LosarFel, match="måste uppfylla"):
        losare.validera_parametrar(
            {"artprofil": "pollinerare", "spridning_faktor_intervall": [2, 1]}
        )
    p = losare.validera_parametrar({"artprofil": "pollinerare"})
    assert p["cellstorlek_m"] == 4.0 and p["max_flackar"] == 40
    assert p["markslagsmappning_sha256"] == las_markslagskonfig().sha256


def test_markslagsmappningen_ar_konfiguration_med_giltiga_koder() -> None:
    k = las_markslagskonfig()
    assert set(k.k1_till_karna) == set(range(10))
    giltiga = {0, 1, 2, 3, 4, 5, 6, k.okant_karnkod}
    assert set(k.k1_till_karna.values()) <= giltiga
    assert k.k1_till_karna[9] == 2 and k.k1_till_karna[0] == k.okant_karnkod
    slag = np.array([[0, 4, 9]], dtype=np.uint8)
    veg = np.array([[0.0, 6.0, 6.0]])
    ut, okanda = k.oversatt(slag, veg)
    assert ut.tolist() == [[99, 4, 2]] and okanda == 1  # trädskikt över gräs, aldrig över byggnad


def test_k44_artprofiler_rundar_och_strikt_validering(tmp_path: Path) -> None:
    for p in STANDARD_ARTPROFILER.values():
        d = artprofil_till_dict(p)
        assert json.loads(json.dumps(d)) == d  # ren JSON (inf som null)
        assert artprofil_till_dict(artprofil_fran_dict(d)) == d
    d = artprofil_till_dict(STANDARD_ARTPROFILER["groddjur"])
    for fel in (
        {**d, "extra": 1},
        {**d, "metodstatus": "rapport"},
        {**d, "min_area_m2": 0},
        {**d, "profil_id": "Fel Id"},
        {**d, "motstand_per_markslag": {"1": 0.5}},
        {**d, "motstand_osakerhet_faktor": [2.0, 1.0]},
    ):
        with pytest.raises(EkologiFel):
            artprofil_fran_dict(fel)
    saknas = {k: v for k, v in d.items() if k != "kalla"}
    with pytest.raises(EkologiFel, match="saknas"):
        artprofil_fran_dict(saknas)
    fil = tmp_path / "egen.json"
    fil.write_text(json.dumps({**d, "profil_id": "egen"}), encoding="utf-8")
    assert las_artprofil_fil(fil).profil_id == "egen"


def test_egen_artprofil_via_indata_kor_och_standardid_nekas(demo: Demo) -> None:
    d = artprofil_till_dict(STANDARD_ARTPROFILER["pollinerare"])
    fraga = (demo.fragor / "demo_ekologi.toml").read_text(encoding="utf-8")
    egen = demo.fragor / "egen_profil.json"
    egen.write_text(json.dumps({**d, "profil_id": "egen_pollinerare"}), encoding="utf-8")
    ny = fraga.replace(
        'parametrar = { artprofil = "pollinerare", cellstorlek_m = 4 }',
        'parametrar = { cellstorlek_m = 4 }\nindata = { artprofil = "egen_profil.json" }',
    )
    ny = ny.replace('fraga_id = "demo-ekologi"', 'fraga_id = "egen-ekologi"')
    assert ny != fraga
    (demo.fragor / "egen_ekologi.toml").write_text(ny, encoding="utf-8")
    status, _ = demo.tjanst.kor_synkront("egen_ekologi", "egen-ekologi")
    assert status["status"] == "klar"
    eg = demo.tjanst.resultat("egen-ekologi")
    assert eg["sammanfattning"]["jobb"] == 2
    egen.write_text(json.dumps({**d, "profil_id": "pollinerare"}), encoding="utf-8")
    with pytest.raises(Exception, match="standardprofil-id"):
        demo.tjanst.kor_synkront("egen_ekologi", "egen-ekologi-2")


def _post(demo: Demo, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, _, kropp = demo.app.hantera_forfragan(
        "POST", "/api/analys/ekologi", json.dumps(payload).encode()
    )
    return status, json.loads(kropp)


def test_api_k45_svar_stammer_med_kontraktet(demo: Demo) -> None:
    text = _avsnitt("K45 ")
    api = text[text.index("### Webb-API"):]
    begaran = _namn_efter(api, "Begäran:")
    svar_namn = _namn_efter(api, "Svar (200):")
    status, svar = _post(demo, {"omrade_id": "demokvarteret"})
    assert status == 200, svar
    assert set(svar) == svar_namn
    assert svar["format"] == "floden-ekologi/v1" and svar["metodstatus"] == "skiss"
    assert svar["delta_eca_m2"] <= 0.0
    assert svar["delta_eca_m2"] == pytest.approx(svar["efter"]["ECA_m2"] - svar["fore"]["ECA_m2"])
    assert len(svar["storsta_flaskhalsar"]["efter"]) == 5
    assert svar["lager"]["stromtathet_skillnad"]["lager_id"].startswith("skillnad.")
    kalla = inspect.getsource(FlodenApiApp.kor_ekologi_analys)
    lasta = set(re.findall(r'payload\.get\("(\w+)"', kalla))
    lasta |= set(re.findall(r'\(\s*payload,\s*"(\w+)"', kalla))
    assert lasta == begaran


def test_api_fel_ar_tydliga(demo: Demo) -> None:
    s, d = _post(demo, {"omrade_id": "demokvarteret", "max_flackar": 2})
    assert s == 400 and "max_flackar=2" in d["fel"]
    s, d = _post(demo, {"omrade_id": "demokvarteret", "artprofil": "hjorthornsbagge"})
    assert s == 400 and "artprofil" in d["fel"]
    s, d = _post(demo, {"omrade_id": "demokvarteret", "max_flackar": 1})
    assert s == 400
    s, d = _post(demo, {"omrade_id": "demokvarteret", "cellstorlek_m": 0.01})
    assert s == 400
    s, d = _post(demo, {"omrade_id": "finns_inte"})
    assert s == 404
    s, d = _post(demo, {"omrade_id": "demokvarteret", "scenario": "saknas"})
    assert s == 404
    s, d = _post(demo, {})
    assert s == 400


def test_api_ar_projektbundet_for_behorighet(demo: Demo) -> None:
    kropp = json.dumps({"omrade_id": "demokvarteret"}).encode()
    assert demo.app._projekt_for_analys("POST", "/api/analys/ekologi", kropp) == "demokvarteret"
