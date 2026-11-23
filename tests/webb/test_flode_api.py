"""API för flödet i webben: markytor per körning och rapportens innehåll för förhandsvisning."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from tests.rapport.test_skissrapport import Miljo
from webb.api import FlodenApiApp

from .conftest import Demo


def _get(app: FlodenApiApp, sokvag: str) -> tuple[int, dict[str, Any]]:
    status, _, kropp = app.hantera_forfragan("GET", sokvag)
    return status, json.loads(kropp)


def test_marksiffror_ar_samma_tal_som_projektsidan(demo: Demo) -> None:
    _, p = demo.get("/api/projekt/demokvarteret")
    vatten = next(a for a in p["avsnitt"] if a["id"] == "vatten")
    _, res = demo.get("/api/korningar/demo-vatten/resultat")
    vy = next(v for v in p["vyer"] if v["avsnitt"] == "vatten")
    status, m = demo.get(
        f"/api/korningar/demo-vatten/marksiffror?lager={vy['skillnad_lager']}"
    )
    assert status == 200
    assert m["lager_id"] == vy["skillnad_lager"]
    yta = next(x for x in vatten["matt"] if x["namn"].startswith("Mark där"))["varden"]["efter"]
    rad = next(x for x in vatten["matt"] if x["namn"].startswith("Största ökning"))
    ok = rad["varden"]["efter"]
    assert m["siffror"]["yta_okning_m2"] == yta
    assert m["siffror"]["max_okning"] == ok
    assert "yta_minskning_m2" in m["siffror"]
    assert res["lager"]


def test_marksiffror_for_okant_eller_bas_lager_ger_fel(demo: Demo) -> None:
    assert demo.get("/api/korningar/demo-vatten/marksiffror?lager=finns-inte")[0] == 404
    _, res = demo.get("/api/korningar/demo-vatten/resultat")
    bas = next(x for x in res["lager"] if x["typ"] == "bas")
    assert demo.get(f"/api/korningar/demo-vatten/marksiffror?lager={bas['id']}")[0] == 422


def test_rapportens_innehall_har_fragor_och_tal_som_egna_delar(demo: Demo) -> None:
    rot = Path(tempfile.mkdtemp())
    m = Miljo(demo, rot)
    r = m.full_rapport()
    app = m.app
    status, d = _get(app, f"/api/projekt/demokvarteret/rapporter/{r.rapport_id}")
    assert status == 200
    assert d["rapport_id"] == r.rapport_id
    assert [f["id"] for f in d["fragor"]] == list(r.fragor)
    sk06 = next(f for f in d["fragor"] if f["id"] == "SK-06")
    delar = sk06["pastaenden"][0]["delar"]
    tal = [x["text"] for x in delar if x["tal"]]
    assert tal and all(any(c.isdigit() for c in t) for t in tal)
    assert "{{" not in "".join(x["text"] for x in delar)
    assert sk06["fraga"]
    assert d["metodnotis"]
    assert _get(app, "/api/projekt/annat/rapporter/" + r.rapport_id)[0] == 404
    assert _get(app, "/api/projekt/demokvarteret/rapporter/r-000000000000")[0] == 404
