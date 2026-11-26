"""`floden demo` i litet läge, byggnadslagret, takmasken, avläsningen och regnets antaganden.

En enda körning (litet läge, 2 m-celler) delas av alla tester i filen.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from demovarld.demo import LITEN, NORMAL, kor_demo
from demovarld.fragor import text_dagsljus, text_ekologi, text_luft, text_vatten
from demovarld.varld import DemoVarld, bygg_ruta
from gemensamt.hash import sha256_fil
from gemensamt.manifest import KONTRAKT_K0, las_manifest
from webb.api import FlodenApiApp, _skapa_fragetjanst

REPO = Path(__file__).resolve().parents[2]
_MALL_KLAR_FIL = ".floden_demo_mall_klar"
_SESSIONS_MALL_ROT: Path | None = None


def _sessions_mall_sokvag(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Delad sökväg per pytest-session (delas mellan xdist-workers via `basetemp.parent`)."""
    bas = tmp_path_factory.getbasetemp()
    if os.environ.get("PYTEST_XDIST_WORKER"):
        bas = bas.parent
    return bas / "floden_demo_mall"


def _skriv_om_json_sokvagar(rot: Path, mall_str: str, mall_resolved: str) -> None:
    """Skriver om absoluta mallsökvägar i klonade JSON-filer så klonen enbart pekar på `rot`."""
    rot_str = str(rot)
    rot_resolved = str(rot.resolve())
    byten = [
        (gammal, ny)
        for gammal, ny in sorted(
            {(mall_resolved, rot_resolved), (mall_str, rot_str)},
            key=lambda par: len(par[0]),
            reverse=True,
        )
        if gammal and gammal != ny
    ]
    if not byten:
        return
    for fil in rot.rglob("*.json"):
        if not fil.is_file():
            continue
        text = fil.read_text(encoding="utf-8")
        ny_text = text
        for gammal, ny in byten:
            if gammal in ny_text:
                ny_text = ny_text.replace(gammal, ny)
        if ny_text != text:
            fil.write_text(ny_text, encoding="utf-8")


def _hamta_eller_bygg_demo_mall(mall_rot: Path) -> Path:
    """Bygger demomallen exakt en gång per pytest-/xdist-session under exklusivt fillås."""
    klar_fil = mall_rot / _MALL_KLAR_FIL
    if klar_fil.is_file():
        return mall_rot
    mall_rot.parent.mkdir(parents=True, exist_ok=True)
    las_fil = mall_rot.parent / f"{mall_rot.name}.lock"
    with las_fil.open("a+b") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            if klar_fil.is_file():
                return mall_rot
            if mall_rot.exists():
                shutil.rmtree(mall_rot)
            mall_rot.mkdir(parents=True, exist_ok=True)
            try:
                Demo(mall_rot, anvand_mall=False)
                meta = json.dumps(
                    {"mall": str(mall_rot), "mall_resolved": str(mall_rot.resolve())},
                    sort_keys=True,
                )
                tmp_klar = mall_rot / f"{_MALL_KLAR_FIL}.{os.getpid()}.tmp"
                tmp_klar.write_text(meta + "\n", encoding="utf-8")
                os.replace(tmp_klar, klar_fil)
            except BaseException:
                shutil.rmtree(mall_rot, ignore_errors=True)
                raise
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return mall_rot


def _klona_demo_mall(mall_rot: Path, rot: Path) -> None:
    """Kopierar mallen med `shutil.copy2` (inga delade hårdlänkar -> 100 % modul-isolering)."""
    meta = json.loads((mall_rot / _MALL_KLAR_FIL).read_text(encoding="utf-8"))
    shutil.copytree(
        mall_rot,
        rot,
        copy_function=shutil.copy2,
        ignore=shutil.ignore_patterns(_MALL_KLAR_FIL),
        dirs_exist_ok=True,
    )
    _skriv_om_json_sokvagar(rot, str(meta["mall"]), str(meta["mall_resolved"]))


class Demo:
    def __init__(
        self, rot: Path, *, mall: Path | None = None, anvand_mall: bool = True
    ) -> None:
        self.rot = rot
        self.varld = rot / "varld"
        self.fragor = rot / "fragor"
        self.tjanst = _skapa_fragetjanst(
            REPO / "config.toml", [self.fragor], rot / "cache", rot / "korningar"
        )
        self.tjanst.demomapp = self.varld
        eff_mall = mall if mall is not None else (_SESSIONS_MALL_ROT if anvand_mall else None)
        if anvand_mall and eff_mall is not None:
            fardig_mall = _hamta_eller_bygg_demo_mall(eff_mall)
            _klona_demo_mall(fardig_mall, rot)
            self.forsta: dict[str, Any] = json.loads(
                (self.varld / "demo.json").read_text(encoding="utf-8")
            )
        else:
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


@pytest.fixture(scope="session", autouse=True)
def _registrera_demo_mall(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Registrerar delad sessionssökväg utan att bygga demon förrän något test begär den."""
    global _SESSIONS_MALL_ROT
    _SESSIONS_MALL_ROT = _sessions_mall_sokvag(tmp_path_factory)


@pytest.fixture(scope="session")
def demo_mall(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Delad, fillåst demomall som byggs en enda gång per pytest-/xdist-session."""
    return _hamta_eller_bygg_demo_mall(_sessions_mall_sokvag(tmp_path_factory))


@pytest.fixture(scope="module")
def demo(demo_mall: Path) -> Iterator[Demo]:
    rot = Path(tempfile.mkdtemp(prefix="floden_demo_"))
    try:
        yield Demo(rot, mall=demo_mall)
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
    assert (fragor / "demo_luft.toml").read_text(encoding="utf-8") == text_luft(v, fragor, varld)


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


def test_sessionsmall_byggs_en_gang_och_ar_isolerad_mellan_kloner(
    demo_mall: Path, tmp_path: Path
) -> None:
    """Delad demomall återanvänds utan ombyggnad och mutationer i en klon läcker inte."""
    klar_fil = demo_mall / _MALL_KLAR_FIL
    assert klar_fil.is_file()
    vid_start = (demo_mall / "varld" / "varld.json").stat().st_mtime_ns
    assert _hamta_eller_bygg_demo_mall(demo_mall) == demo_mall
    assert (demo_mall / "varld" / "varld.json").stat().st_mtime_ns == vid_start

    d1 = Demo(tmp_path / "klon_a", mall=demo_mall)
    d2 = Demo(tmp_path / "klon_b", mall=demo_mall)

    # Inga gamla mallsökvägar får finnas kvar i klonens JSON-manifest eller körningar.
    mall_str = str(demo_mall)
    mall_res = str(demo_mall.resolve())
    for jf in d1.rot.rglob("*.json"):
        if jf.is_file():
            txt = jf.read_text(encoding="utf-8")
            assert mall_str not in txt and mall_res not in txt, jf

    # Muterar klon A i varld, fragor, korningar och cache -> varken mall eller klon B påverkas.
    orig_demo_json = (demo_mall / "varld" / "demo.json").read_text(encoding="utf-8")
    (d1.varld / "demo.json").write_text('{"muterad": true}\n', encoding="utf-8")
    (d1.fragor / "extra_fraga.toml").write_text("# mutation i klon A\n", encoding="utf-8")
    (d1.rot / "korningar" / "demo-vatten" / "korning.json").write_text("{}", encoding="utf-8")
    (d1.rot / "cache" / "markor.txt").write_text("a", encoding="utf-8")

    assert (demo_mall / "varld" / "demo.json").read_text(encoding="utf-8") == orig_demo_json
    assert (d2.varld / "demo.json").read_text(encoding="utf-8") == orig_demo_json
    assert not (demo_mall / "fragor" / "extra_fraga.toml").exists()
    assert not (d2.fragor / "extra_fraga.toml").exists()
    assert not (d2.rot / "cache" / "markor.txt").exists()
    assert (d1.varld / "varld.json").stat().st_ino != (d2.varld / "varld.json").stat().st_ino

    status, oversikt = d2.get("/api/demo")
    assert status == 200 and oversikt["finns"] is True
    status_r, res = d2.get("/api/korningar/demo-vatten/resultat")
    assert status_r == 200 and len(res["lager"]) > 0
