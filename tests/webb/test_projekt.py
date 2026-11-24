"""Demokvarteret som projekt: talen på projektsidan kommer ur demokörningarna, inga platshållare."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from testsvit.k1_io import las_geotiff
from webb.api import FlodenApiApp

from .conftest import Demo

REPO = Path(__file__).resolve().parents[2]
PLATSHALLARE = re.compile(r"\[[A-Za-zÅÄÖåäö][^\]]*\]")


def _matt(avsnitt: dict[str, Any], namn: str) -> dict[str, float]:
    return next(m for m in avsnitt["matt"] if m["namn"].startswith(namn))["varden"]  # type: ignore[no-any-return]


def test_projektlistan_har_demokvarteret_markt_pahittat(demo: Demo) -> None:
    status, d = demo.get("/api/projekt")
    assert status == 200
    [p] = d["projekt"]
    assert p["id"] == "demokvarteret"
    assert p["paahittat"] is True
    assert "påhittat" in p["undertitel"]


def test_projektets_tal_ar_korningens_egna(demo: Demo) -> None:
    status, p = demo.get("/api/projekt/demokvarteret")
    assert status == 200
    vatten = next(a for a in p["avsnitt"] if a["id"] == "vatten")
    _, res = demo.get("/api/korningar/demo-vatten/resultat")
    tab = res["tabeller"][0]["rader"]
    per = {r["scenario"]: r["varden"] for r in tab}
    # Största djup och lagrad volym är lösarens mätvärden, oförändrade.
    djup = _matt(vatten, "Största vattendjup")
    assert djup["fore"] == per["fore"]["max_djup_m"]
    assert djup["efter"] == per["efter"]["max_djup_m"]
    assert djup["efter"] >= djup["fore"]
    lagrat = _matt(vatten, "Vatten som blir stående")
    assert lagrat["efter"] == per["efter"]["lagrat_m3"]
    # Regnet är det lösaren använde: Stockholmsregn 2024, 100 år, 6 timmar, klimatfaktor 1,4.
    regn = vatten["regn"]
    mm = res["antaganden"]["regn"][0]["regn_mm"]
    assert regn["regn_mm"] == mm
    forvantad = f"Stockholmsregn 2024, 100 år, 6 timmar, klimatfaktor 1,4 = {mm:.1f} mm"
    assert regn["text"] == forvantad.replace(".", ",")


def test_markytor_stammer_med_skillnadsrastret_utan_tak(demo: Demo) -> None:
    _, p = demo.get("/api/projekt/demokvarteret")
    vatten = next(a for a in p["avsnitt"] if a["id"] == "vatten")
    yta = _matt(vatten, "Mark där vattnet blir djupare")["efter"]
    ok = _matt(vatten, "Största ökning av djupet")["efter"]
    k = demo.app.fragor._hamta("demo-vatten")  # noqa: SLF001
    tif = next((k.utmapp / "jamforelser").glob("*/diff.tif"))
    diff, geo = las_geotiff(tif)
    d = np.asarray(diff, dtype=np.float64)
    tak = np.load(next((k.mapp / "webb").glob("skillnad.*.tak.npy")))
    mark = np.isfinite(d) & ~tak
    assert yta == float(np.count_nonzero(mark & (d > 0.01))) * geo.pixelstorlek_x**2
    assert ok == float(d[mark].max())
    # Kärnans egen jämförelse räknar också tak: marksiffran kan aldrig vara större.
    jf = res_jamforelse(demo, "demo-vatten")
    assert yta <= jf["yta_okning_m2"]


def res_jamforelse(demo: Demo, korning: str) -> dict[str, Any]:
    _, res = demo.get(f"/api/korningar/{korning}/resultat")
    return res["jamforelser"][0]  # type: ignore[no-any-return]


def test_dagsljusets_marksiffror_ar_rimliga_och_har_vyer(demo: Demo) -> None:
    _, p = demo.get("/api/projekt/demokvarteret")
    sol = next(a for a in p["avsnitt"] if a["id"] == "dagsljus")
    forlorar = _matt(sol, "Mark som förlorar mer än 1 timme sol")
    storsta = _matt(sol, "Största minskning")
    _, res = demo.get("/api/korningar/demo-dagsljus/resultat")
    for jf in res["jamforelser"]:
        sid = jf["efter"]
        # Kärnans tröskel är 0,1 h och alla celler; marksiffran för 1 h kan inte vara större.
        assert 0 <= forlorar[sid] <= jf["yta_minskning_m2"]
        assert 0 <= storsta[sid] <= jf["max_minskning"] + 1e-9
    # Varje vy pekar på lager som finns i körningens resultat.
    ids = {la["id"] for la in res["lager"]}
    for vy in p["vyer"]:
        if vy["korning_id"] == "demo-dagsljus":
            assert {vy["fore_lager"], vy["efter_lager"], vy["skillnad_lager"]} <= ids
        assert vy["andring"]


def test_inga_platshallare_eller_nan_i_projektets_svar(demo: Demo) -> None:
    for sokvag in ("/api/projekt", "/api/projekt/demokvarteret", "/api/moduler"):
        status, _, kropp = demo.app.hantera_forfragan("GET", sokvag)
        assert status == 200
        text = kropp.decode("utf-8")
        assert not PLATSHALLARE.search(text), sokvag
        assert "NaN" not in text
        json.loads(text)


def test_moduler_ur_demon_ger_ratt_status(demo: Demo) -> None:
    _, d = demo.get("/api/moduler")
    m = {x["modul_id"]: x for x in d["moduler"]}
    assert [x["status"] for x in d["moduler"]] == [
        "finns_i_demon",
        "finns_i_demon",  # luft: demon kör luft.mkn med två påhittade serier
        "under_utveckling",
        "finns_i_demon",
    ]
    assert "geometri" not in m
    assert m["buller"]["status_text"].startswith("Under utveckling – metodkontroll pågår (")


def test_fragorna_markeras_som_demo_eller_kraver_egna_data(demo: Demo) -> None:
    _, d = demo.get("/api/fragor")
    poster = {f["ref"]: f for f in d["fragor"]}
    assert poster["demo_dagsljus"]["i_demon"] is True
    assert poster["demo_vatten"]["i_demon"] is True
    assert all(
        f["visningsnamn"] and "." not in f["visningsnamn"]
        for f in poster["demo_dagsljus"]["analyser"]
    )


def test_okant_projekt_ger_404(demo: Demo) -> None:
    status, d = demo.get("/api/projekt/finns-inte")
    assert status == 404
    assert "fel" in d


def test_utan_byggd_demo_finns_inget_projekt_och_inget_pastas() -> None:
    rot = Path(tempfile.mkdtemp())
    try:
        app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[rot / "fragor"],
            cachemapp=rot / "cache",
            korningsmapp=rot / "korningar",
        )
        app.fragor.demomapp = rot / "varld"
        status, _, kropp = app.hantera_forfragan("GET", "/api/projekt")
        assert status == 200 and json.loads(kropp) == {"projekt": []}
        status, _, _ = app.hantera_forfragan("GET", "/api/projekt/demokvarteret")
        assert status == 404
        _, _, kropp = app.hantera_forfragan("GET", "/api/moduler")
        for m in json.loads(kropp)["moduler"]:
            assert m["status"] != "finns_i_demon", m
    finally:
        shutil.rmtree(rot, ignore_errors=True)
