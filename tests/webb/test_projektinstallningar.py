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


def test_radering_rensar_kartverktyg_k9_stangda_rapportjobb_och_cache(v: Varld) -> None:
    """W-B9: full projektradering tar bort kartverktyg/pid (inkl. k9_stangda), rapportjobb, beställningar
    och cacheposter i minnet utan att röra andra projekt."""
    from types import SimpleNamespace

    pid, mapp = _projekt(v, "anna", "Fullrensning")
    annat, annan_mapp = _projekt(v, "anna", "Orord", x=70.0)
    k1 = _korning(v, "k-rensa", pid)
    k2 = _korning(v, "k-orord", annat)

    # Kartverktygsmapp för båda projekten (inkl. k9_stangda och scenarier).
    kv_rot = v.app.kartverktyg.mapp
    for p_id in (pid, annat):
        (kv_rot / p_id / "scenarier").mkdir(parents=True, exist_ok=True)
        (kv_rot / p_id / "scenarier" / "forslag.json").write_text("{}", encoding="utf-8")
        (kv_rot / p_id / "k9_stangda").mkdir(parents=True, exist_ok=True)
        (kv_rot / p_id / "k9_stangda" / "oppenhet.toml").write_text("oppen = false\n", encoding="utf-8")
        (kv_rot / p_id / "k9_stangda" / "forslag__vatten.json").write_text("{}", encoding="utf-8")
        (kv_rot / p_id / "lage-idag.json").write_text("{}", encoding="utf-8")

    # Beställningar och rapportjobb för båda projekten.
    best_mapp = v.rot / "rapporter" / "bestallningar"
    best_mapp.mkdir(parents=True, exist_ok=True)
    b1 = best_mapp / "b-111111111111.json"
    b2 = best_mapp / "b-222222222222.json"
    b1.write_text(json.dumps({"projekt": pid, "bestallning_id": "b-111111111111"}), encoding="utf-8")
    b2.write_text(json.dumps({"projekt": annat, "bestallning_id": "b-222222222222"}), encoding="utf-8")

    jobb_mapp = v.rot / "rapporter" / "jobb"
    jobb_mapp.mkdir(parents=True, exist_ok=True)
    j1 = jobb_mapp / "j-111111111111.json"
    j2 = jobb_mapp / "j-222222222222.json"
    j1.write_text(json.dumps({"jobb_id": "j-111111111111", "projekt": pid, "status": "kor"}), encoding="utf-8")
    j2.write_text(json.dumps({"jobb_id": "j-222222222222", "projekt": annat, "status": "klar"}), encoding="utf-8")

    # Pågående rapportjobb i minnet stoppar raderingen (409).
    rj = v.app.rapportjobb
    rj._aktiva.add("j-111111111111")  # noqa: SLF001
    status, d = v.http("anna", "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "Fullrensning"})
    assert status == 409 and "rapportjobb" in d["fel"]
    assert mapp.is_dir() and (kv_rot / pid / "k9_stangda").is_dir() and j1.is_file()

    # Avsluta jobbet och fyll minnescacherna för båda projekten.
    rj._aktiva.discard("j-111111111111")  # noqa: SLF001
    j1.write_text(json.dumps({"jobb_id": "j-111111111111", "projekt": pid, "status": "klar"}), encoding="utf-8")
    v.app._projektmodell_cache[(pid, (("r1", 1),))] = SimpleNamespace()  # type: ignore[assignment]  # noqa: SLF001
    v.app._projektmodell_cache[(annat, (("r2", 2),))] = SimpleNamespace()  # type: ignore[assignment]  # noqa: SLF001
    vy = v.app.vy3d
    vy._terrang_per_projekt[pid] = SimpleNamespace()  # type: ignore[assignment]  # noqa: SLF001
    vy._terrang_per_projekt[annat] = SimpleNamespace()  # type: ignore[assignment]  # noqa: SLF001
    vy._spann[f"{pid}|/x/k-rensa/webb/a.npy|1|2"] = (0.0, 1.0)  # noqa: SLF001
    vy._spann[f"{annat}|/x/k-orord/webb/a.npy|1|2"] = (0.0, 2.0)  # noqa: SLF001
    vy._minne["tile-hash"] = b"fl3d"  # noqa: SLF001
    rj._rt = SimpleNamespace(tjanst=SimpleNamespace(_byggnader={pid: (1, []), annat: (2, [])}))  # type: ignore[assignment]  # noqa: SLF001

    status, d = v.http("anna", "POST", f"/api/projekt/{pid}/radera", {"bekrafta_namn": "Fullrensning"})
    assert status == 200 and d["status"] == "raderat"
    assert d["borttaget"]["bestallningar"] == 1
    # Allt för det raderade projektet är borta.
    assert not mapp.exists() and not k1.exists()
    assert not (kv_rot / pid).exists()
    assert not b1.exists() and not j1.exists()
    assert not any(k[0] == pid for k in v.app._projektmodell_cache)  # noqa: SLF001
    assert pid not in vy._terrang_per_projekt  # noqa: SLF001
    assert not any(k.startswith(f"{pid}|") for k in vy._spann)  # noqa: SLF001
    assert not vy._minne  # noqa: SLF001
    assert pid not in rj._rt.tjanst._byggnader  # noqa: SLF001
    # Det andra projektets filer och cacher är helt orörda.
    assert annan_mapp.is_dir() and k2.is_dir()
    assert (kv_rot / annat / "k9_stangda" / "forslag__vatten.json").is_file()
    assert b2.is_file() and j2.is_file()
    assert any(k[0] == annat for k in v.app._projektmodell_cache)  # noqa: SLF001
    assert annat in vy._terrang_per_projekt  # noqa: SLF001
    assert any(k.startswith(f"{annat}|") for k in vy._spann)  # noqa: SLF001
    assert annat in rj._rt.tjanst._byggnader  # noqa: SLF001


def test_radering_sokvagskontroll_och_valfria_beroenden(tmp_path: Path) -> None:
    """Path-containment i `_ta_bort_mapp` och `_ta_bort_fil` tillåter aldrig radering utanför rot,
    och `_rensa_projektcache` tål saknade valfria beroenden."""
    import pytest

    from webb.fel import FlodenWebbFel
    from webb.projektinstallningar import ProjektInstallningarTjanst, _ta_bort_fil, _ta_bort_mapp

    rot = tmp_path / "rot"
    rot.mkdir()
    utanfor_mapp = tmp_path / "utanfor_mapp"
    utanfor_mapp.mkdir()
    utanfor_fil = tmp_path / "utanfor.txt"
    utanfor_fil.write_text("bevaras", encoding="utf-8")

    with pytest.raises(FlodenWebbFel) as fel_rot:
        _ta_bort_mapp(rot, rot)
    assert fel_rot.value.status_kod == 500
    with pytest.raises(FlodenWebbFel) as fel_ut:
        _ta_bort_mapp(rot, utanfor_mapp)
    assert fel_ut.value.status_kod == 500 and utanfor_mapp.is_dir()
    with pytest.raises(FlodenWebbFel) as fel_fil:
        _ta_bort_fil(rot, utanfor_fil)
    assert fel_fil.value.status_kod == 500 and utanfor_fil.read_text(encoding="utf-8") == "bevaras"

    # Symlink inuti rot som pekar utanför: bara länken tas bort, målet rörs inte.
    lank_mapp = rot / "lank_mapp"
    lank_mapp.symlink_to(utanfor_mapp)
    assert _ta_bort_mapp(rot, lank_mapp) is True
    assert not lank_mapp.exists() and utanfor_mapp.is_dir()

    lank_fil = rot / "lank_fil.json"
    lank_fil.symlink_to(utanfor_fil)
    assert _ta_bort_fil(rot, lank_fil) is True
    assert not lank_fil.exists() and utanfor_fil.read_text(encoding="utf-8") == "bevaras"

    # Minimal app utan valfria cacher/tjänster kraschar inte.
    from types import SimpleNamespace

    inst = ProjektInstallningarTjanst(SimpleNamespace())  # type: ignore[arg-type]
    assert inst._har_pagaende_rapportjobb("a" * 32) is False  # noqa: SLF001
    assert inst._rensa_projektcache("a" * 32, ()) == 0  # noqa: SLF001
