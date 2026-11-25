# ruff: noqa: E501, F811
"""Projektinställningar i webben: byta namn och radera projekt (filerna), arkivering i projektlistan.

SYNTETISKT: samma påhittade geografi som `test_skapa_projekt.py`. Körningar och rapporter är minimala filer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.kommuner.test_skapa import geo  # noqa: F401
from tests.webb.test_skapa_projekt import Varld, skapa
from tests.webb.test_skapa_projekt import v as v  # noqa: F401  (fixturen)


def _korning(v: Varld, kid: str, projekt: str | None, status: str = "klar") -> Path:
    mapp = v.rot / "korningar" / kid
    (mapp / "resultat").mkdir(parents=True)
    (mapp / "resultat" / "run.json").write_text("{}", encoding="utf-8")
    d: dict[str, Any] = {
        "korning_id": kid, "ref": "x", "fraga_id": "x", "fraga_fil": "x.toml",
        "status": status, "skapad": "2026-11-25T00:00:00+00:00",
    }  # fmt: skip
    if projekt:
        d["projekt_id"] = projekt
    (mapp / "korning.json").write_text(json.dumps(d), encoding="utf-8")
    return mapp


def _rapport(v: Varld, rid: str, projekt: str) -> Path:
    mapp = v.rot / "rapporter" / rid
    mapp.mkdir(parents=True)
    (mapp / "rapport.json").write_text(json.dumps({"projekt": projekt}), encoding="utf-8")
    return mapp


def _projekt(v: Varld, vem: str, namn: str, x: float = 60.0) -> tuple[str, Path]:
    status, d = skapa(v, vem, namn, v.inre(storlek=x))
    assert status == 201, d
    pid = d["projekt_id"]
    from webb.projektkalla import hitta_projektmapp

    mapp = hitta_projektmapp(v.rot / "projekt", pid, tillat_projektid=False)
    assert mapp is not None
    return pid, mapp


def test_radering_tar_bort_ratt_filer_och_inget_annat(v: Varld) -> None:
    pid, mapp = _projekt(v, "anna", "Raderas")
    annat, annan_mapp = _projekt(v, "anna", "Behalls", x=70.0)
    k1, k2 = _korning(v, "k-mitt", pid), _korning(v, "k-annat", annat)
    k3 = _korning(v, "k-utan", None)
    r1, r2 = _rapport(v, "r-mitt", pid), _rapport(v, "r-annat", annat)
    delad = v.rot / "cache" / "delad.txt"
    delad.parent.mkdir(parents=True, exist_ok=True)
    delad.write_text("cache", encoding="utf-8")
    geografi = v.rot / "geo"
    antal_geo = sum(1 for _ in geografi.rglob("*"))
    v.reg.bjud_in(v.konton["anna"], pid, "hemlig@example.test", "lasare")

    # Fel namn, och saknad bekräftelse: inget händer.
    assert v.http("anna", "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "raderas"})[0] == 400
    assert v.http("anna", "POST", f"/api/projekt/{pid}/radera", {})[0] == 400
    assert mapp.is_dir() and k1.is_dir() and v.reg.projekt_finns(pid)
    # Utan CSRF, utloggad, vanlig medlem och en annan organisations admin nekas.
    assert v.http("anna", "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "Raderas"}, csrf=False)[0] == 403
    assert v.http(None, "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "Raderas"})[0] == 401
    for vem in ("bo", "di"):
        assert v.http(vem, "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "Raderas"})[0] == 403
    assert mapp.is_dir()

    status, d = v.http("anna", "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "Raderas"})
    assert status == 200 and d["status"] == "raderat"
    assert d["borttaget"] == {"projektmapp": 1, "korningar": 1, "rapporter": 1, "bestallningar": 0, "roller": 1, "inbjudningar": 1}
    assert not mapp.exists() and not k1.exists() and not r1.exists()
    assert annan_mapp.is_dir() and k2.is_dir() and k3.is_dir() and r2.is_dir()
    assert delad.read_text(encoding="utf-8") == "cache"
    assert sum(1 for _ in geografi.rglob("*")) == antal_geo
    assert not v.reg.projekt_finns(pid) and v.reg.projekt_finns(annat)
    assert v.http("anna", "GET", "/api/korningar/k-mitt")[0] == 404
    # Namnet går att använda igen.
    assert skapa(v, "anna", "Raderas", v.inre())[0] == 201


def test_superadmin_far_radera_men_inte_organisationsadmin_som_inte_ar_agare(v: Varld) -> None:
    pid, mapp = _projekt(v, "anna", "Annas")
    ed = v.reg.forbered_konto("ed@example.test")
    v.reg.gor_organisationsadmin(ed, v.org)
    assert v.reg.kan_andra_delning(ed, pid) is False
    # Superadmin Cy har ingen projektroll men får radera.
    status, d = v.http("cy", "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "Annas"})
    assert status == 200 and not mapp.exists()


def test_pagaende_korning_stoppar_raderingen_och_demokvarteret_kan_inte_raderas(v: Varld) -> None:
    pid, mapp = _projekt(v, "anna", "Upptaget")
    k = _korning(v, "k-pagar", pid)
    v.app.fragor.korningar_for_projekt(pid)  # läser in körningarna (en pågående på disk räknas som avbruten)
    v.app.fragor._korningar["k-pagar"].status = "kor"  # noqa: SLF001
    status, d = v.http("anna", "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "Upptaget"})
    assert status == 409 and mapp.is_dir() and k.is_dir() and v.reg.projekt_finns(pid)
    status, d = v.http("cy", "POST", "/api/projekt/demokvarteret/radera", {"bekrafta_namn": "Demokvarteret"})
    assert status == 409 and "Demokvarteret" in d["fel"]


def test_byta_namn_registret_och_projektfilen(v: Varld) -> None:
    pid, mapp = _projekt(v, "anna", "Gammalt namn")
    assert v.http("bo", "PUT", f"/api/projekt/{pid}/namn", {"namn": "Nytt"})[0] == 403
    status, d = v.http("anna", "PUT", f"/api/projekt/{pid}/namn", {"namn": "Nytt namn"})
    assert status == 200 and d["namn"] == "Nytt namn"
    assert json.loads((mapp / "projekt.json").read_text(encoding="utf-8"))["namn"] == "Nytt namn"
    assert [p["namn"] for p in v.http("anna", "GET", "/api/projekt")[1]["projekt"] if p["id"] == pid] == ["Nytt namn"]
    # Namn som liknar en e-postadress, och upptaget namn.
    assert v.http("anna", "PUT", f"/api/projekt/{pid}/namn", {"namn": "a@b.se"})[0] == 400
    _projekt(v, "anna", "Annat", x=70.0)
    assert v.http("anna", "PUT", f"/api/projekt/{pid}/namn", {"namn": "Annat"})[0] == 409


def test_arkiverade_projekt_doljs_i_listan_och_visas_med_arkiverade(v: Varld) -> None:
    pid, _ = _projekt(v, "anna", "Gammalt")
    ids = lambda vem, q="": [p["id"] for p in v.http(vem, "GET", f"/api/projekt{q}")[1]["projekt"]]  # noqa: E731
    assert pid in ids("anna")
    assert v.http("anna", "PUT" if False else "PUT", f"/konto/projekt/{pid}/arkiv", {"arkiverad": True})[0] == 200
    assert pid not in ids("anna") and pid in ids("anna", "?arkiverade=1")
    rad = next(p for p in v.http("anna", "GET", "/api/projekt?arkiverade=1")[1]["projekt"] if p["id"] == pid)
    assert rad["arkiverad"] is True
    assert pid not in ids("bo", "?arkiverade=1")
    assert v.http("anna", "PUT", f"/konto/projekt/{pid}/arkiv", {"arkiverad": False})[0] == 200
    assert pid in ids("anna") and pid not in ids("anna", "?arkiverade=1")
