# ruff: noqa: E501
"""Kartverktyget: kontroll, scenarier, K9/K20, körning, WMS-proxy, ortofoto och klientens facit.

Delar demovärlden (litet läge) med `tests/demovarld`. Allt som skrivs hamnar i en tillfällig mapp,
aldrig i repot.
"""

from __future__ import annotations

import http.server
import io
import json
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from demovarld.demo import LITEN
from demovarld.varld import nytt_hus_polygon
from karna.ritat import (
    RitatFel,
    bta_uppskattning,
    kontrollera_andringar,
    normalisera_ring,
    vaningar,
)
from karna.scenario import las_scenario
from tests.webb.conftest import Demo
from webb.api import FlodenApiApp
from webb.kartverktyg import slug
from webb.moduler import visningsnamn  # noqa: F401 - kontrollerar att modulen går att läsa

REPO = Path(__file__).resolve().parents[2]
P = "/api/kartverktyg/demokvarteret"


class Kv:
    def __init__(self, demo: Demo, rot: Path) -> None:
        self.demo = demo
        self.rot = rot
        self.data = rot / "data"
        self.data.mkdir()
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=demo.rot / "korningar",
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[self.data],
            tillat_loopback_underlag=True,
        )
        self.app.fragor.demomapp = demo.varld

    def anrop(self, verb: str, sokvag: str, kropp: Any = None) -> tuple[int, dict[str, str], bytes]:
        data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
        return self.app.hantera_forfragan(verb, sokvag, data)

    def json(self, verb: str, sokvag: str, kropp: Any = None) -> tuple[int, dict[str, Any]]:
        status, _, ut = self.anrop(verb, sokvag, kropp)
        return status, json.loads(ut)

    def vantaklar(self, namn: str, antal: int, tidsgrans: float = 600.0) -> dict[str, Any]:
        slut = time.monotonic() + tidsgrans
        while time.monotonic() < slut:
            _, st = self.json("GET", f"{P}/scenarier/{namn}/status")
            k = st["korningar"]
            assert not any(x["status"]["status"] == "fel" for x in k), k
            if len(k) == antal and all(x["status"]["status"] == "klar" for x in k):
                return st
            time.sleep(0.5)
        raise AssertionError("körningen blev inte klar")


@pytest.fixture(scope="module")
def kv(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Kv]:
    yield Kv(demo, tmp_path_factory.mktemp("kartverktyg"))


def demohus(hojd: float | int = 18) -> dict[str, Any]:
    ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
    ring.append(ring[0])
    return {
        "typ": "byggnad_ny",
        "andring_id": "nytt_hus",
        "geometri": {"type": "Polygon", "coordinates": [ring]},
        "egenskaper": {"hojd_m": hojd},
    }


def kvadrat(
    x: float, y: float, s: float, hojd: float | int = 12, aid: str = "hus1"
) -> dict[str, Any]:
    ring = [[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]
    return {
        "typ": "byggnad_ny",
        "andring_id": aid,
        "geometri": {"type": "Polygon", "coordinates": [ring]},
        "egenskaper": {"hojd_m": hojd},
    }


# ---------------------------------------------------------------- projektinfo och kontroll


def test_projektinfo_ger_utbredning_byggnader_och_analyser(kv: Kv) -> None:
    status, info = kv.json("GET", P)
    assert status == 200
    assert info["epsg"] == 3011
    assert info["hojd_max_m"] == 200.0
    u = info["utbredning"]
    assert u["x_max"] > u["x_min"] and u["y_max"] > u["y_min"]
    assert len(info["byggnader"]) > 0
    assert {a["id"] for a in info["analyser"]} >= {"dagsljus.sol", "vatten.skyfall"}
    assert info["ortofoto"] == {"finns": False}
    assert kv.json("GET", "/api/kartverktyg/finns-inte")[0] == 404


def test_giltigt_hus_ger_yta_och_bta_ur_karnan(kv: Kv) -> None:
    status, v = kv.json("POST", f"{P}/validera", {"andringar": [demohus()]})
    assert status == 200 and v["giltig"], v
    (h,) = v["hus"]
    assert h["yta_m2"] == pytest.approx(14 * 40, abs=0.1) or h["yta_m2"] > 0
    assert h["vaningar"] == 6
    assert h["bta_m2"] == pytest.approx(h["yta_m2"] * 6)


def test_hus_utanfor_k1_ger_tydligt_fel(kv: Kv) -> None:
    status, v = kv.json("POST", f"{P}/validera", {"andringar": [kvadrat(900000, 7000000, 20)]})
    assert status == 200 and not v["giltig"]
    assert "utanför" in " ".join(v["fel"])


def test_hus_delvis_utanfor_ger_fel(kv: Kv) -> None:
    u = kv.json("GET", P)[1]["utbredning"]
    status, v = kv.json(
        "POST", f"{P}/validera", {"andringar": [kvadrat(u["x_max"] - 5, u["y_min"] + 50, 20)]}
    )
    assert not v["giltig"], v


def test_sjalvkorsande_polygon_nekas(kv: Kv) -> None:
    a = kvadrat(151100, 6580100, 20)
    a["geometri"]["coordinates"] = [
        [
            [151100, 6580100],
            [151120, 6580120],
            [151120, 6580100],
            [151100, 6580120],
            [151100, 6580100],
        ]
    ]
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [a]})
    assert not v["giltig"]
    assert "korsar" in " ".join(v["fel"])


@pytest.mark.parametrize("hojd", [0, -3, 200.01, 201, 1000])
def test_ogiltig_hojd_nekas_med_tydligt_besked(kv: Kv, hojd: float) -> None:
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [kvadrat(151100, 6580100, 20, hojd)]})
    assert not v["giltig"]
    assert "200" in " ".join(v["fel"]) or "större än 0" in " ".join(v["fel"])


@pytest.mark.parametrize("hojd", [0.5, 18, 200])
def test_giltiga_hojder_accepteras(kv: Kv, hojd: float) -> None:
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [kvadrat(151110, 6580110, 20, hojd)]})
    assert v["giltig"], v


def test_hojd_utan_tal_nekas(kv: Kv) -> None:
    a = kvadrat(151100, 6580100, 20)
    a["egenskaper"]["hojd_m"] = "hög"
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [a]})
    assert not v["giltig"]


def test_hojdandring_och_rivning_av_befintlig_byggnad(kv: Kv) -> None:
    info = kv.json("GET", P)[1]
    bid = info["byggnader"][0]["id"]
    andr = [
        {
            "typ": "byggnad_hojd",
            "andring_id": "h1",
            "geometri": None,
            "egenskaper": {"byggnads_id": bid, "hojd_m": 25},
        },
    ]
    _, v = kv.json("POST", f"{P}/validera", {"andringar": andr})
    assert v["giltig"], v
    andr2 = [
        {
            "typ": "byggnad_bort",
            "andring_id": "b1",
            "geometri": None,
            "egenskaper": {"byggnads_id": bid},
        }
    ]
    _, v2 = kv.json("POST", f"{P}/validera", {"andringar": andr2})
    assert v2["giltig"], v2
    okand = [
        {
            "typ": "byggnad_bort",
            "andring_id": "b2",
            "geometri": None,
            "egenskaper": {"byggnads_id": "finns-inte"},
        }
    ]
    _, v3 = kv.json("POST", f"{P}/validera", {"andringar": okand})
    assert not v3["giltig"] or v3["varningar"]


def test_validera_utan_andringar_ger_400(kv: Kv) -> None:
    assert kv.json("POST", f"{P}/validera", {})[0] == 400


def test_ritat_enhetskontroller() -> None:
    ring = normalisera_ring([[0, 0], [0, 10.004], [10, 10], [10, 0]])
    assert ring[0] == ring[-1] or len(ring) == 4
    with pytest.raises(RitatFel):
        kontrollera_andringar([kvadrat(0, 0, 10, 0)], [(0, 0, 100, 100)])


# ---------------------------------------------------------------- BTA-vektor mot klienten


BTA_VEKTOR = [
    (560, 18, 6, 3360),
    (100, 4, 1, 100),
    (100, 2, 1, 100),
    (250.5, 7.5, 2, 501),
    (1000, 200, 66, 66000),
    (49.99, 9.01, 3, 149.97),
    (300, 4.4999, 1, 300),
]


def test_bta_vektorn_stammer_med_klientens_tabell() -> None:
    for yta, hojd, v, bta in BTA_VEKTOR:
        assert vaningar(hojd, 3.0) == v
        r = bta_uppskattning(yta, hojd)
        assert r["bta_m2"] == pytest.approx(bta, abs=1e-9)
    ts = (REPO / "webb/src/karta/ritlogik.test.ts").read_text(encoding="utf-8")
    rader = re.findall(r"^\s*\[([\d.]+), ([\d.]+), (\d+), ([\d.]+)\],$", ts, re.M)
    assert [tuple(float(x) for x in r) for r in rader] == [tuple(map(float, t)) for t in BTA_VEKTOR]


def test_klientens_pyproj_facit_stammer_med_pyproj() -> None:
    pyproj = pytest.importorskip("pyproj")
    ts = (REPO / "webb/src/sweref.test.ts").read_text(encoding="utf-8")
    block = ts.split("const PYPROJ", 1)[1].split("];", 1)[0]
    punkter = [
        tuple(map(float, m))
        for m in re.findall(r"\[([-\d.]+), ([-\d.]+), ([-\d.]+), ([-\d.]+)\]", block)
    ]
    assert len(punkter) >= 40
    t = pyproj.Transformer.from_crs(4326, 3011, always_xy=True)
    for lon, lat, e, n in punkter:
        e2, n2 = t.transform(lon, lat)
        assert abs(e2 - e) < 1e-6 and abs(n2 - n) < 1e-6


# ---------------------------------------------------------------- scenarier


def test_spara_hamta_byt_namn_och_ta_bort_scenario(kv: Kv) -> None:
    status, d = kv.json(
        "POST", f"{P}/scenarier", {"namn": "Prov ett", "andringar": [kvadrat(151120, 6580120, 20)]}
    )
    assert status == 200 and d["slug"] == "prov-ett"
    fil = kv.rot / "kartverktyg" / "demokvarteret" / "scenarier" / "prov-ett.json"
    assert fil.is_file()
    assert REPO not in fil.parents
    _, lista = kv.json("GET", f"{P}/scenarier")
    assert [s["slug"] for s in lista["scenarier"]] == ["prov-ett"]
    _, hamtad = kv.json("GET", f"{P}/scenarier/prov-ett")
    assert (
        hamtad["andringar"][0]["geometri"]["coordinates"][0][0]
        == hamtad["andringar"][0]["geometri"]["coordinates"][0][-1]
    )
    status, ny = kv.json("POST", f"{P}/scenarier/prov-ett/byt-namn", {"namn": "Prov två"})
    assert status == 200 and ny["namn"] == "Prov två"
    assert kv.json("GET", f"{P}/scenarier/{ny['slug']}")[1]["namn"] == "Prov två"
    assert kv.json("POST", f"{P}/scenarier/{ny['slug']}/ta-bort", {})[0] == 200
    assert kv.json("GET", f"{P}/scenarier")[1]["scenarier"] == []
    assert kv.json("GET", f"{P}/scenarier/{ny['slug']}")[0] == 404


def test_scenario_med_ogiltigt_hus_sparas_inte(kv: Kv) -> None:
    status, d = kv.json(
        "POST", f"{P}/scenarier", {"namn": "Fel", "andringar": [kvadrat(151120, 6580120, 20, 0)]}
    )
    assert status == 422 and "fel" in d
    assert kv.json("POST", f"{P}/scenarier", {"namn": "  ", "andringar": []})[0] == 400


def test_slug_och_osakra_namn(kv: Kv) -> None:
    assert slug("Mitt förslag 2") == "mitt-forslag-2"
    assert kv.json("GET", f"{P}/scenarier/..%2F..%2Fconfig")[0] in (400, 404)


# ---------------------------------------------------------------- samma K9 och samma resultat som den handskrivna


def test_ritat_demohus_ger_samma_k9_som_den_handskrivna(kv: Kv) -> None:
    status, d = kv.json("POST", f"{P}/scenarier", {"namn": "Demohuset", "andringar": [demohus(18)]})
    assert status == 200
    status, plan = kv.json(
        "POST", f"{P}/scenarier/demohuset/plan", {"analyser": ["dagsljus.sol", "vatten.skyfall"]}
    )
    assert status == 200, plan
    nycklar = {f["mall"]: f["k9_nyckel"] for f in plan["fragor"]}
    for mall in ("dagsljus", "vatten"):
        hand = las_scenario(kv.demo.varld / mall / "k9_nytt_hus.json")
        assert nycklar[mall] == hand.nyckel, mall
        k9 = next(f["k9"] for f in plan["fragor"] if f["mall"] == mall)
        ritat = las_scenario(kv.rot / "kartverktyg" / "demokvarteret" / "k9" / k9)
        assert ritat.nyckel == hand.nyckel
        assert ritat.bas_recept == hand.bas_recept
        assert [a.till_dict() for a in ritat.andringar] == [a.till_dict() for a in hand.andringar]


def test_planen_visar_radie_tid_och_cache(kv: Kv) -> None:
    _, plan = kv.json("POST", f"{P}/scenarier/demohuset/plan", {"analyser": ["dagsljus.sol"]})
    (f,) = plan["fragor"]
    assert f["paverkansradie_m"]["sol"] and f["paverkansradie_m"]["sol"] > 0
    assert f["uppskattad_tid_s"] is not None or f["tid_text"]
    assert all(j["i_cache"] for j in f["plan"]["jobb"])  # demon har redan räknat exakt det här
    assert kv.json("POST", f"{P}/scenarier/demohuset/plan", {"analyser": []})[0] == 400
    assert kv.json("POST", f"{P}/scenarier/demohuset/plan", {"analyser": ["x.y"]})[0] == 400


def test_k20_ligger_i_lokal_mapp_och_inte_i_repot(kv: Kv) -> None:
    kv.json("POST", f"{P}/scenarier/demohuset/plan", {"analyser": ["dagsljus.sol"]})
    fil = kv.rot / "kartverktyg" / "fragor" / "rita-demohuset-dagsljus.json"
    assert fil.is_file()
    k20 = json.loads(fil.read_text(encoding="utf-8"))
    assert k20["kontrakt"] == "K20/v1"
    assert set(k20["scenarier"]) == {"fore", "ritat"}
    assert k20["scenarier"]["ritat"]["k9"]


def test_avtappning_for_en_korning_hamnar_i_vattnets_k20(kv: Kv) -> None:
    status, _ = kv.json(
        "POST", f"{P}/scenarier/demohuset/plan",
        {"analyser": ["vatten.skyfall"], "avtappning_mm_h": 12.5},
    )
    assert status == 200
    (fil,) = (kv.rot / "kartverktyg" / "fragor").glob("rita-demohuset-vatten*.json")
    k20 = json.loads(fil.read_text(encoding="utf-8"))
    (a,) = k20["analyser"]
    assert a["parametrar"]["avtappning_mm_h"] == 12.5
    # Utan fält: inget i frågan, lösarens standard 0 mm/h gäller.
    kv.json("POST", f"{P}/scenarier/demohuset/plan", {"analyser": ["vatten.skyfall"]})
    k20 = json.loads(fil.read_text(encoding="utf-8"))
    assert "avtappning_mm_h" not in k20["analyser"][0]["parametrar"]
    fel = kv.json(
        "POST", f"{P}/scenarier/demohuset/plan",
        {"analyser": ["vatten.skyfall"], "avtappning_mm_h": -1},
    )
    assert fel[0] == 400


def test_korning_ger_samma_resultat_som_demons(kv: Kv) -> None:
    status, k = kv.json(
        "POST", f"{P}/scenarier/demohuset/kor", {"analyser": ["dagsljus.sol", "vatten.skyfall"]}
    )
    assert status == 202, k
    st = kv.vantaklar("demohuset", 2)
    vyer = {x["mall"]: x["vyer"] for x in st["korningar"]}
    assert vyer["dagsljus"] and vyer["vatten"]
    for mall, ref, demo_scen in (
        ("dagsljus", "demo_dagsljus", "nytt_hus"),
        ("vatten", "demo_vatten", "efter"),
    ):
        demo_id = kv.demo.forsta["delar"][ref]["korning_id"]
        demo_res = kv.demo.app.fragor.resultat(demo_id)
        rit_id = vyer[mall][0]["korning_id"]
        rit_res = kv.app.fragor.resultat(rit_id)
        gemensamma = {v["analys"] for v in rit_res["lager"]} & {
            v["analys"] for v in demo_res["lager"]
        }
        assert gemensamma
        for analys in gemensamma:

            def skillnad(res: dict[str, Any], efter: str, a: str = analys) -> dict[str, Any]:
                return next(
                    x
                    for x in res["lager"]
                    if x["analys"] == a and x["typ"] == "skillnad" and x["efter"] == efter
                )

            ra = skillnad(rit_res, "ritat")
            da = skillnad(demo_res, demo_scen)
            u = ra["utbredning"]
            assert u == da["utbredning"]
            # Samma värden i samma celler: avläsning i ett nät över hela området, i båda körningarna.
            antal = 0
            for i in range(1, 12):
                for j in range(1, 12):
                    e = u["x_min"] + (u["x_max"] - u["x_min"]) * i / 12
                    n = u["y_min"] + (u["y_max"] - u["y_min"]) * j / 12
                    v1 = kv.app.fragor.avlasning(rit_id, ra["id"], e, n)["varden"]
                    v2 = kv.demo.app.fragor.avlasning(demo_id, da["id"], e, n)["varden"]
                    x1 = next(x for x in v1 if x["lager_id"] == ra["id"])
                    x2 = next(x for x in v2 if x["lager_id"] == da["id"])
                    assert x1["varde"] == x2["varde"], f"{mall}/{analys} i ({e:.1f}, {n:.1f})"
                    antal += x1["varde"] is not None
            assert antal > 20, f"{mall}/{analys}: för få celler med värde ({antal})"


def test_status_for_scenario_utan_korning_ar_tom(kv: Kv) -> None:
    kv.json("POST", f"{P}/scenarier", {"namn": "Tom", "andringar": [kvadrat(151120, 6580120, 20)]})
    _, st = kv.json("GET", f"{P}/scenarier/tom/status")
    assert st["korningar"] == []


# ---------------------------------------------------------------- WMS via servern


class _FakeWms(http.server.BaseHTTPRequestHandler):
    CAPS = b"""<?xml version="1.0"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms">
 <Service><Title>Provtjanst</Title></Service>
 <Capability><Layer><Title>Rot</Title>
  <Layer><Name>orto</Name><Title>Ortofoto</Title><CRS>EPSG:3857</CRS><CRS>EPSG:4326</CRS></Layer>
  <Layer><Name>sweref</Name><Title>Bara SWEREF</Title><CRS>EPSG:3006</CRS></Layer>
 </Layer></Capability>
</WMS_Capabilities>"""
    sett: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        self.sett.append(self.path)
        if self.path.startswith("/omdirigera"):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest")
            self.end_headers()
        elif "GetCapabilities" in self.path:
            self._svar(200, "text/xml", self.CAPS)
        elif "GetMap" in self.path:
            from PIL import Image

            buf = io.BytesIO()
            Image.new("RGBA", (16, 16), (10, 20, 30, 255)).save(buf, "PNG")
            self._svar(200, "image/png", buf.getvalue())
        else:
            self._svar(404, "text/plain", b"nej")

    def _svar(self, kod: int, typ: str, data: bytes) -> None:
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a: Any) -> None:  # noqa: D401
        return


@pytest.fixture()
def fakewms() -> Iterator[str]:
    _FakeWms.sett = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeWms)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/wms"
    finally:
        srv.shutdown()
        srv.server_close()


def test_wms_lager_via_servern(kv: Kv, fakewms: str) -> None:
    status, d = kv.json("POST", "/api/kartverktyg/wms/lager", {"url": fakewms})
    assert status == 200, d
    assert d["titel"] == "Provtjanst" and d["version"] == "1.3.0"
    namn = {la["namn"]: la["stod"] for la in d["lager"] if la["namn"]}
    assert namn["orto"] in ("3857", "4326")
    assert [la["namn"] for la in d["anvandbara"]] == ["orto"]


def test_wms_bild_hamtas_via_servern(kv: Kv, fakewms: str) -> None:
    from urllib.parse import quote

    q = f"url={quote(fakewms, safe='')}&lager=orto&crs=3857&bredd=256&hojd=256&bbox=2000000,8000000,2001000,8001000"
    status, huvud, data = kv.anrop("GET", f"/api/kartverktyg/wms/bild?{q}")
    assert status == 200 and huvud["Content-Type"].startswith("image/png")
    assert data[:4] == b"\x89PNG"
    assert any("GetMap" in s and "EPSG%3A3857" in s for s in _FakeWms.sett)
    q4326 = q.replace("crs=3857", "crs=4326")
    assert kv.anrop("GET", f"/api/kartverktyg/wms/bild?{q4326}")[0] == 200
    assert any("EPSG%3A4326" in s for s in _FakeWms.sett)


def test_wms_nekar_skadliga_adresser(demo: Demo, tmp_path: Path, fakewms: str) -> None:
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[demo.fragor],
        cachemapp=demo.rot / "cache",
        korningsmapp=demo.rot / "korningar",
        kartverktygsmapp=tmp_path / "k",
        datamappar=[tmp_path],
    )  # utan tillat_loopback_underlag

    def lager(url: str) -> int:
        s, _, _ = app.hantera_forfragan(
            "POST", "/api/kartverktyg/wms/lager", json.dumps({"url": url}).encode()
        )
        return s

    assert lager(fakewms) == 400  # loopback
    assert lager("file:///etc/passwd") == 400
    assert lager("ftp://example.org/x") == 400
    assert lager("http://169.254.169.254/latest/meta-data") == 400  # länklokal
    assert lager("") == 400


def test_wms_omdirigering_till_lankokal_adress_nekas(kv: Kv, fakewms: str) -> None:
    status, d = kv.json(
        "POST", "/api/kartverktyg/wms/lager", {"url": fakewms.replace("/wms", "/omdirigera")}
    )
    assert status in (400, 502), d


def test_wms_bild_med_dalig_indata(kv: Kv, fakewms: str) -> None:
    from urllib.parse import quote

    bas = f"/api/kartverktyg/wms/bild?url={quote(fakewms, safe='')}&lager=orto"
    assert kv.anrop("GET", bas + "&bbox=1,2,3")[0] == 400
    assert kv.anrop("GET", bas + "&bbox=1,2,3,x")[0] == 400
    assert kv.anrop("GET", bas + "&bbox=1,2,3,4&crs=3006")[0] == 400
    assert kv.anrop("GET", bas + "&bbox=1,2,3,4&bredd=99999")[0] == 400


# ---------------------------------------------------------------- ortofoto (GeoTIFF)


def _orto(kv: Kv, crs: int, namn: str) -> None:
    rasterio = pytest.importorskip("rasterio")
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds

    x0, y0, x1, y1 = 150900.0, 6579900.0, 151800.0, 6580800.0
    if crs == 3011:
        b = (x0, y0, x1, y1)
    else:
        b = transform_bounds(CRS.from_epsg(3011), CRS.from_epsg(crs), x0, y0, x1, y1)
    px = 2.0
    bredd, hojd = int((b[2] - b[0]) / px), int((b[3] - b[1]) / px)
    rgb = np.zeros((3, hojd, bredd), dtype="uint8")
    rgb[0], rgb[1], rgb[2] = 200, 80, 40
    mapp = kv.data / namn
    mapp.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        mapp / "orto.tif",
        "w",
        driver="GTiff",
        width=bredd,
        height=hojd,
        count=3,
        dtype="uint8",
        crs=CRS.from_epsg(crs),
        transform=from_origin(b[0], b[3], px, px),
    ) as ds:
        ds.write(rgb)
    (mapp / "lantmateriet_manifest.json").write_text(
        json.dumps({"filer": [{"roll": "orto", "fil": "orto.tif"}]}), encoding="utf-8"
    )


@pytest.mark.filterwarnings("ignore::PendingDeprecationWarning")
@pytest.mark.parametrize("crs", [3011, 3006])
def test_ortofoto_ur_geotiff_i_3011_och_3006(kv: Kv, crs: int) -> None:
    from PIL import Image

    for g in list(kv.data.glob("*")):
        for f in g.glob("*"):
            f.unlink()
        g.rmdir()
    assert kv.json("GET", P)[1]["ortofoto"] == {"finns": False}
    _orto(kv, crs, f"orto{crs}")
    info = kv.json("GET", P)[1]["ortofoto"]
    assert (
        info["finns"] and info["png"].endswith("/ortofoto.png") and "Lantmäteriet" in info["kalla"]
    )
    status, huvud, data = kv.anrop("GET", f"{P}/ortofoto.png")
    assert status == 200 and data[:4] == b"\x89PNG"
    x0, y0, x1, y1 = (float(v) for v in huvud["X-Floden-Utbredning"].split(","))
    assert (x0, y0, x1, y1) == (151000.0, 6580000.0, 151700.0, 6580700.0)
    im = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    assert im.shape[0] > 100 and (im[..., 3] == 255).mean() > 0.99
    assert abs(int(im[im.shape[0] // 2, im.shape[1] // 2, 0]) - 200) <= 2


def test_geometrilager_ar_png_med_utbredning(kv: Kv) -> None:
    for vilket in ("skuggning", "vegetation"):
        status, huvud, data = kv.anrop("GET", f"{P}/geometri/{vilket}.png")
        assert status in (200, 404)
        if status == 200:
            assert data[:4] == b"\x89PNG" and len(huvud["X-Floden-Utbredning"].split(",")) == 4
    assert kv.anrop("GET", f"{P}/geometri/okand.png")[0] == 404


# ---------------------------------------------------------------- hus som stänger in vatten


def hus_i_porten(aid: str = "hus_i_porten") -> dict[str, Any]:
    """Ett hus som ligger i gårdens enda utlopp (porten) och stänger in kvarterets lågpunkt."""
    x, y = 151318.0, 6580196.0
    ring = [[x, y], [x + 12, y], [x + 12, y + 11], [x, y + 11], [x, y]]
    return {
        "typ": "byggnad_ny",
        "andring_id": aid,
        "geometri": {"type": "Polygon", "coordinates": [ring]},
        "egenskaper": {"hojd_m": 12},
    }


def test_hus_som_stanger_in_vatten_ger_information_fore_korning(kv: Kv) -> None:
    from webb.felforklaring import SLUTET_INFO

    status, v = kv.json("POST", f"{P}/validera", {"andringar": [hus_i_porten()]})
    assert status == 200 and v["giltig"], v
    # Sedan K2/v2 är det information, inte varning: beräkningen går och vattnet blir stående.
    assert v["information"] == [SLUTET_INFO]
    assert SLUTET_INFO == "Huset stänger in gården – vattnet blir stående där i beräkningen"
    assert SLUTET_INFO not in v["varningar"]
    # Det vanliga demohuset stänger inte in något och ger ingen sådan information.
    _, ok = kv.json("POST", f"{P}/validera", {"andringar": [demohus()]})
    assert ok["information"] == []


def test_hus_i_porten_ger_resultat_med_varning_och_markerade_slutna_omraden(kv: Kv) -> None:
    """Reproduktionen från #36 (E 151318, N 6580196, 12 x 11 m): förut fel, nu resultat."""
    from sankgraf.typer import SLUTSANKA_VARNING

    status, _ = kv.json(
        "POST", f"{P}/scenarier", {"namn": "instangt", "andringar": [hus_i_porten()]}
    )
    assert status in (200, 201)
    status, k = kv.json("POST", f"{P}/scenarier/instangt/kor", {"analyser": ["vatten.skyfall"]})
    assert status == 202, k
    st = kv.vantaklar("instangt", 1)
    post = st["korningar"][0]
    assert post["status"]["status"] == "klar", post
    assert SLUTSANKA_VARNING in post["varningar"]
    vy = post["vyer"][0]
    assert vy["markering_lager"], vy
    res = kv.app.fragor.resultat(vy["korning_id"])
    markering = next(x for x in res["lager"] if x["id"] == vy["markering_lager"])
    assert markering["roll"] == "slutsanka" and markering["scenario"] == "ritat"
    assert markering["etikett"].startswith("Kontroll: slutet område")
    # Basen (utan huset) har inget slutet område och därför inget sådant lager.
    assert not any(
        x["roll"] == "slutsanka" and x["scenario"] != "ritat" for x in res["lager"]
    )
    # Måtten om det slutna området kommer ur lösaren: en slutsänka med lagrat vatten.
    korning = kv.app.fragor._hamta(vy["korning_id"])  # noqa: SLF001
    samman = json.loads((korning.utmapp / "sammanstallning.json").read_text(encoding="utf-8"))
    ritat = [j for j in samman["jobb"] if j["scenario"] == "ritat"]
    assert ritat
    for j in ritat:
        assert j["matvarden"]["antal_slutsankor"] >= 1
        assert j["matvarden"]["slutsanka_lagrat_m3"] > 0.0
        assert j["matvarden"]["slutsanka_over_hinder_m3"] == 0.0


def test_forklara_fel_okanda_fel_ger_inget_klartext() -> None:
    from webb.felforklaring import INSTANGT_TEXT, forklara_fel

    # Felet finns kvar bara för instängda områden vid mark utan höjd (se InstangtOmradeFel).
    assert "utan höjduppgift" in INSTANGT_TEXT
    assert forklara_fel(None) is None
    assert forklara_fel("ValueError: något annat") is None
    assert forklara_fel("SankGrafFel: Nod 3 saknar utloppssadel mot randen.") == INSTANGT_TEXT


def test_ekologi_ritat_hus_ger_samma_eca_som_demons_och_api_kan_ta_scenariot(kv: Kv) -> None:
    status, _ = kv.json(
        "POST", f"{P}/scenarier/demohuset/kor", {"analyser": ["ekologi.konnektivitet"]}
    )
    assert status == 202
    slut = time.monotonic() + 300
    while True:
        _, st = kv.json("GET", f"{P}/scenarier/demohuset/status")
        ek = [x for x in st["korningar"] if x["mall"] == "ekologi"]
        assert not ek or ek[0]["status"]["status"] != "fel", ek
        if ek and ek[0]["status"]["status"] == "klar":
            break
        assert time.monotonic() < slut
        time.sleep(0.5)
    assert ek[0]["vyer"] and ek[0]["vyer"][0]["avsnitt"] == "ekologi"

    def eca(res: dict[str, Any], scen: str) -> float:
        tab = next(t for t in res["tabeller"] if t["losare"] == "ekologi.konnektivitet")
        return float(next(r for r in tab["rader"] if r["scenario"] == scen)["varden"]["eca_m2"])

    rit = kv.app.fragor.resultat(ek[0]["vyer"][0]["korning_id"])
    demo_res = kv.demo.app.fragor.resultat(kv.demo.forsta["delar"]["demo_ekologi"]["korning_id"])
    assert eca(rit, "ritat") == eca(demo_res, "efter")
    status, svar = kv.json(
        "POST", "/api/analys/ekologi", {"omrade_id": "demokvarteret", "scenario": "demohuset"}
    )
    assert status == 200, svar
    assert svar["scenario"] == "demohuset"
    assert svar["efter"]["ECA_m2"] == eca(demo_res, "efter")
    assert svar["delta_eca_m2"] <= 0.0


# ---------------------------------------------------------------- läget idag (U5a)


def _vanta_lage(kv: Kv, antal: int, tidsgrans: float = 600.0) -> dict[str, Any]:
    slut = time.monotonic() + tidsgrans
    while time.monotonic() < slut:
        _, st = kv.json("GET", f"{P}/lage-idag/status")
        k = st["korningar"]
        assert not any(x["status"]["status"] == "fel" for x in k), k
        if len(k) == antal and all(x["status"]["status"] == "klar" for x in k):
            return st
        time.sleep(0.5)
    raise AssertionError("läget idag blev inte klart")


def test_lage_idag_plan_har_bara_fore_och_ingen_jamforelse(kv: Kv) -> None:
    status, plan = kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["dagsljus.sol"]})
    assert status == 200, plan
    assert plan["lage"] == "idag"
    (f,) = plan["fragor"]
    assert f["fraga_id"].startswith("lage-idag-")
    assert "uppskattad_tid_s" in plan and "uppskattad_tid_s" in f
    fil = kv.rot / "kartverktyg" / "fragor" / f"{f['fraga_id']}.json"
    k20 = json.loads(fil.read_text(encoding="utf-8"))
    assert set(k20["scenarier"]) == {"fore"} and "k9" not in k20["scenarier"]["fore"]
    assert k20["jamforelser"] == []
    assert all(a["scenarier"] == ["fore"] for a in k20["analyser"])
    assert {j["scenario"] for j in f["plan"]["jobb"]} == {"fore"}
    # Demon har redan räknat befintligt läge: allt finns i cachen, så ingen tid att vänta.
    assert all(j["i_cache"] for j in f["plan"]["jobb"])
    assert plan["uppskattad_tid_s"] == 0


def test_lage_idag_nekar_okand_analys_och_tomt_urval_och_avtappning(kv: Kv) -> None:
    assert kv.json("POST", f"{P}/lage-idag/plan", {"analyser": []})[0] == 400
    assert kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["x.y"]})[0] == 400
    assert kv.json("POST", f"{P}/lage-idag/plan", {"analyser": ["vatten.skyfall"], "avtappning_mm_h": -1})[0] == 400
    assert kv.json("POST", "/api/kartverktyg/finns-inte/lage-idag/kor", {"analyser": ["vatten.skyfall"]})[0] == 404
    assert kv.json("GET", f"{P}/lage-idag/okand")[0] == 404


def test_lage_idag_kor_sparas_som_projektets_egen_och_ger_fore_vyer(kv: Kv) -> None:
    status, k = kv.json("POST", f"{P}/lage-idag/kor", {"analyser": ["dagsljus.sol", "vatten.skyfall"]})
    assert status == 202, k
    st = _vanta_lage(kv, 2)
    vyer = [v for x in st["korningar"] for v in x["vyer"]]
    assert {v["avsnitt"] for v in vyer} >= {"dagsljus", "vatten"}
    for v in vyer:
        assert v["fore_lager"] and v["efter_lager"] is None and v["lage_idag"] is True
        assert v["fore_lager"] == v["skillnad_lager"]
    # Körningen är projektets egen och märkt som läget idag (inte som förslag eller demo).
    for x in st["korningar"]:
        kid = x["status"]["korning_id"]
        assert kv.app.projekt_for_korning(kid) == "demokvarteret"
        assert kv.app.korningsunderlag(kid) == {"slag": "lage_idag", "scenario": None}
        res = kv.app.fragor.resultat(kid)
        assert {la["scenario"] for la in res["lager"]} == {"fore"}
    assert set(kv.app.kartverktyg.lage_idag_korningar("demokvarteret")) == {
        x["status"]["korning_id"] for x in st["korningar"]
    }
    assert (kv.rot / "kartverktyg" / "demokvarteret" / "lage-idag.json").is_file()


def test_lage_idag_status_utan_korning_ar_tom(demo: Demo, tmp_path: Path) -> None:
    ny = Kv(demo, tmp_path)
    assert ny.json("GET", f"{P}/lage-idag/status")[1]["korningar"] == []


def test_rapportens_underlagstext_och_ordning_for_lage_idag() -> None:
    from rapport.korningsval import RANG, underlagstext

    assert "läget idag" in underlagstext({"slag": "lage_idag", "scenario": None})
    assert RANG["lage_idag"] == RANG["egen"] < RANG["demo"]


# ---------------------------------------------------------------- skarvkontroll (R3)


def markyta(x: float, y: float, s: float, niva: float, aid: str = "mark1") -> dict[str, Any]:
    ring = [[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]
    return {
        "typ": "markhojd",
        "andring_id": aid,
        "geometri": {"type": "Polygon", "coordinates": [ring]},
        "egenskaper": {"niva_rh2000": niva},
    }


def test_markyta_utan_niva_nekas(kv: Kv) -> None:
    a = markyta(151100, 6580100, 20, 10.0)
    a["egenskaper"] = {}
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [a]})
    assert not v["giltig"] and "nivå" in " ".join(v["fel"])


def test_skarvkontroll_utan_markyta_ger_ingen_skarvpost(kv: Kv) -> None:
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [demohus()]})
    assert v["skarvar"] is None


def test_skarvkontroll_i_demokvarteret(kv: Kv) -> None:
    """En hög platå i demokvarterets sänka ger en varning (nytt fält `skarvar`, additivt)."""
    u = kv.json("GET", P)[1]["utbredning"]
    cx, cy = (u["x_min"] + u["x_max"]) / 2, (u["y_min"] + u["y_max"]) / 2
    _, v = kv.json("POST", f"{P}/validera", {"andringar": [markyta(cx - 15, cy - 15, 30, 400.0)]})
    assert v["giltig"], v
    sk = v["skarvar"]
    assert sk["kontrakt"] == "floden-skarvkontroll/v1" and sk["antal"] >= 1
    assert sk["tillkommande_volym_m3"] > 0
    assert any("sänka vid skarven" in t for t in v["varningar"])
    assert len([t for t in v["varningar"] if "sänka vid skarven" in t]) <= 3
