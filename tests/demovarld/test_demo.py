"""`floden demo` i litet läge, byggnadslagret, takmasken, avläsningen och regnets antaganden.

En enda körning (litet läge, 2 m-celler) delas av alla tester i filen.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from demovarld.demo import LITEN, NORMAL, kor_demo
from demovarld.fragor import text_dagsljus, text_ekologi, text_vatten
from demovarld.varld import DemoVarld, bygg_ruta
from gemensamt.hash import sha256_fil
from gemensamt.manifest import KONTRAKT_K0, las_manifest
from webb.api import FlodenApiApp, _skapa_fragetjanst

REPO = Path(__file__).resolve().parents[2]


class Demo:
    def __init__(self, rot: Path) -> None:
        self.rot = rot
        self.varld = rot / "varld"
        self.fragor = rot / "fragor"
        self.tjanst = _skapa_fragetjanst(
            REPO / "config.toml", [self.fragor], rot / "cache", rot / "korningar"
        )
        self.tjanst.demomapp = self.varld
        self.forsta = kor_demo(
            tjanst=self.tjanst,
            varld_dir=self.varld,
            fragedir=self.fragor,
            lage=LITEN,
            logg=lambda _t: None,
        )
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[self.fragor],
            cachemapp=rot / "cache",
            korningsmapp=rot / "korningar",
        )
        self.app.fragor.demomapp = self.varld

    def get(self, sokvag: str) -> tuple[int, dict[str, Any]]:
        status, _, kropp = self.app.hantera_forfragan("GET", sokvag)
        return status, json.loads(kropp)


@pytest.fixture(scope="module")
def demo() -> Iterator[Demo]:
    rot = Path(tempfile.mkdtemp())
    try:
        yield Demo(rot)
    finally:
        shutil.rmtree(rot, ignore_errors=True)


def test_demon_skriver_k0_manifest_och_luft_ar_inte_forkord(demo: Demo) -> None:
    sam = demo.forsta
    assert sam["delar"]["luft"]["status"] == "ej_forkord"
    assert sam["delar"]["demo_dagsljus"]["jobb"] > 0
    assert sam["delar"]["demo_vatten"]["jobb"] == 2
    manifest = las_manifest(demo.varld / "demo.run.json")
    assert manifest.kontrakt == KONTRAKT_K0
    assert manifest.modul == "demo"
    assert {i.roll for i in manifest.indata} >= {"varld", "fraga:demo_vatten"}


def test_andra_korningen_ar_oforandrad(demo: Demo) -> None:
    vid_start = (demo.varld / "varld.json").stat().st_mtime_ns
    sam = kor_demo(
        tjanst=demo.tjanst,
        varld_dir=demo.varld,
        fragedir=demo.fragor,
        lage=LITEN,
        logg=lambda _t: None,
    )
    assert sam["varld"]["byggd"] is False
    assert sam["delar"]["demo_dagsljus"]["oforandrad"] is True
    assert sam["delar"]["demo_vatten"]["oforandrad"] is True
    assert (demo.varld / "varld.json").stat().st_mtime_ns == vid_start


def test_varlden_ar_deterministisk(tmp_path: Path) -> None:
    v = DemoVarld(upplosning_m=2.0)
    a = bygg_ruta(v, tmp_path / "a", v.e0_m, v.n0_m, 100.0)
    b = bygg_ruta(v, tmp_path / "b", v.e0_m, v.n0_m, 100.0)
    for fil in sorted(a.iterdir()):
        if fil.name != "run.json":  # manifestets tidsstämpel får skilja
            assert sha256_fil(fil) == sha256_fil(b / fil.name), fil.name


def test_incheckade_demofragor_ar_malletens_utdata() -> None:
    v = NORMAL.varld()
    fragor = REPO / "exempel" / "fragor"
    varld = REPO / "data" / "demo" / "varld"
    assert (fragor / "demo_dagsljus.toml").read_text(encoding="utf-8") == text_dagsljus(
        v, fragor, varld, riktningar=NORMAL.riktningar
    )
    assert (fragor / "demo_vatten.toml").read_text(encoding="utf-8") == text_vatten(
        v, fragor, varld
    )
    assert (fragor / "demo_ekologi.toml").read_text(encoding="utf-8") == text_ekologi(
        v, fragor, varld
    )


def test_api_demo_ger_oversikt(demo: Demo) -> None:
    status, d = demo.get("/api/demo")
    assert status == 200 and d["finns"] is True
    assert d["delar"]["luft"]["status"] == "ej_forkord"
    assert d["delar"]["demo_vatten"]["korning_id"] == "demo-vatten"


def test_api_demo_utan_demo_ger_finns_false(tmp_path: Path) -> None:
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[tmp_path],
        cachemapp=tmp_path / "c",
        korningsmapp=tmp_path / "k",
    )
    app.fragor.demomapp = tmp_path / "finns-inte"
    _, _, kropp = app.hantera_forfragan("GET", "/api/demo")
    assert json.loads(kropp) == {"finns": False}


def test_regn_visas_ur_manifestet(demo: Demo) -> None:
    _, r = demo.get("/api/korningar/demo-vatten/resultat")
    (regn,) = r["antaganden"]["regn"]
    assert regn["kalla"] == "stockholmsregn-2024"
    assert regn["aterkomsttid_ar"] == 100.0
    assert regn["klimatfaktor"] == 1.4
    assert regn["klimatfaktor_ingar_i_tabell"] is False
    assert regn["regn_mm"] > 50.0
    assert r["varningar"] == []  # inga artefakter: bara riktiga varningar visas


def test_byggnadslager_med_ny_byggnad(demo: Demo) -> None:
    _, r = demo.get("/api/korningar/demo-vatten/resultat")
    b = r["byggnader"]
    assert b["bas"] == "fore"
    status_fore = {x["status"] for x in b["scenarier"]["fore"]}
    status_efter = [x["status"] for x in b["scenarier"]["efter"]]
    assert status_fore == {"oforandrad"}
    assert status_efter.count("ny") == 1
    assert len(b["scenarier"]["efter"]) == len(b["scenarier"]["fore"]) + 1
    ny = next(x for x in b["scenarier"]["efter"] if x["status"] == "ny")
    assert len(ny["ring"]) >= 4 and ny["hojd_m"] > 0


def test_andrad_hojd_markeras_i_dagsljus(demo: Demo) -> None:
    _, r = demo.get("/api/korningar/demo-dagsljus/resultat")
    assert any(x["status"] == "andrad" for x in r["byggnader"]["scenarier"]["hojd"])
    # Litet läge (2 m-celler, 64 riktningar) ger just de två varningar som lösarna ska ge för
    # det grova valet; normalläget (1 m, 180 riktningar) körs utan varningar.
    for v in r["varningar"]:
        assert "azimutriktningar=64" in v["text"] or "fasadpunkter" in v["text"], v["text"]


def test_takmask_och_synlig_utbredning(demo: Demo) -> None:
    _, r = demo.get("/api/korningar/demo-vatten/resultat")
    skillnad = next(la for la in r["lager"] if la["typ"] == "skillnad")
    assert skillnad["tak_maskat"] is True
    assert skillnad["tak_celler"] > 0 and skillnad["tak_yta_m2"] > 0
    assert skillnad["png_med_tak"] and skillnad["png_med_tak"] != skillnad["png"]
    u, s = skillnad["utbredning"], skillnad["synlig_utbredning"]
    assert s is not None
    assert u["x_min"] <= s["x_min"] < s["x_max"] <= u["x_max"]
    assert (s["x_max"] - s["x_min"]) < (u["x_max"] - u["x_min"])  # passar mot det som syns
    # Både masken och PNG:erna går att hämta.
    for url in (skillnad["png"], skillnad["png_med_tak"]):
        status, huvud, kropp = demo.app.hantera_forfragan("GET", url)
        assert status == 200 and huvud["Content-Type"] == "image/png"
        assert kropp.startswith(b"\x89PNG")


def test_avlasning_ger_varden_och_tak(demo: Demo) -> None:
    _, r = demo.get("/api/korningar/demo-vatten/resultat")
    skillnad = next(la for la in r["lager"] if la["typ"] == "skillnad")
    k = demo.tjanst._hamta("demo-vatten")
    tak = np.load(k.mapp / "webb" / f"{skillnad['id']}.tak.npy")
    rad, kol = (int(x) for x in np.argwhere(tak)[0])
    u, px = skillnad["utbredning"], skillnad["pixelstorlek_m"]
    e, n = u["x_min"] + (kol + 0.5) * px, u["y_max"] - (rad + 0.5) * px
    status, a = demo.get(f"/api/korningar/demo-vatten/avlasning?lager={skillnad['id']}&e={e}&n={n}")
    assert status == 200
    assert {x["typ"] for x in a["varden"]} == {"bas", "scenario", "skillnad"}
    assert next(x for x in a["varden"] if x["typ"] == "skillnad")["tak"] is True
    # Utanför lagret: inget värde.
    _, ute = demo.get(
        f"/api/korningar/demo-vatten/avlasning?lager={skillnad['id']}&e={u['x_min'] - 50}&n={n}"
    )
    assert all(x["inom"] is False and x["varde"] is None for x in ute["varden"])


def test_avlasning_avvisar_felaktiga_parametrar(demo: Demo) -> None:
    status, _ = demo.get("/api/korningar/demo-vatten/avlasning?lager=x&e=abc&n=1")
    assert status == 400
    status, _ = demo.get("/api/korningar/demo-vatten/avlasning?lager=x&e=1&n=nan")
    assert status == 400
    status, _ = demo.get("/api/korningar/demo-vatten/avlasning?lager=finns-inte&e=1&n=1")
    assert status == 404


def test_plan_ger_nyckel_som_matchar_korningen(demo: Demo) -> None:
    status, _, kropp = demo.app.hantera_forfragan("POST", "/api/fragor/demo_vatten/plan")
    plan = json.loads(kropp)
    _, k = demo.get("/api/korningar/demo-vatten")
    assert status == 200 and plan["plan_nyckel"] == k["plan_nyckel"]
