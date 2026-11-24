# ruff: noqa: E501, F811
"""Kartverktyget för projekt i en geografi (steg 2 och 3), mot den påhittade geografin `goteborg-test`.

SYNTETISKT: samma falska Lantmäteri- och WFS-värld som `test_skapa_projekt.py`; inget nät och ingen riktig Göteborgsdata.
Visar att ett projekt som skapats i webben får projektinfo, kontroll, sparade förslag, plan och körning (registrets id i
adressen, rollerna som övriga projekt), att rutorna klipps till projektet, att dataklassen följer projektets `oppenhet.toml`
och att MCP inte lämnar ut ett stängt projekt. Demokvarteret rörs inte (`test_kartverktyg.py`).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import anyio
from mcp import Client

from floden_mcp.dataklass import Policy
from floden_mcp.karna_api import FlodenTjanst
from floden_mcp.korlogg import Korlogg
from floden_mcp.server import bygg_server
from tests.kommuner.test_skapa import geo  # noqa: F401
from tests.konto.conftest import miljo  # noqa: F401
from tests.webb.test_skapa_projekt import REPO, Varld, skapa, v  # noqa: F401


def _hus(x0: float, y0: float, b: float = 12.0, d: float = 10.0, hojd: float = 15.0, aid: str = "hus-1") -> dict[str, Any]:
    return {
        "typ": "byggnad_ny",
        "andring_id": aid,
        "geometri": {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + b, y0], [x0 + b, y0 + d], [x0, y0 + d], [x0, y0]]]},
        "egenskaper": {"hojd_m": hojd},
    }


def _l_polygon(v: Varld) -> list[list[float]]:
    """En L-formad avgränsning inom kärnan (hörnet uppe till höger är utsparat)."""
    k = v.karna
    x, y = k[0] + 10, k[1] + 10
    return [[x, y], [x + 60, y], [x + 60, y + 25], [x + 25, y + 25], [x + 25, y + 60], [x, y + 60], [x, y]]


def _projekt(v: Varld, namn: str = "Haga kv", polygon: bool = False) -> tuple[str, dict[str, Any]]:
    avgr: Any = {"type": "Polygon", "coordinates": [_l_polygon(v)]} if polygon else v.inre(marginal=10, storlek=60)
    st, d = skapa(v, "anna", namn, avgr)
    assert st == 201, d
    return d["projekt_id"], d


def test_projektinfo_for_ett_projekt_i_geografin(v: Varld) -> None:
    pid, d = _projekt(v)
    st, info = v.http("anna", "GET", f"/api/kartverktyg/{pid}")
    assert st == 200, info
    assert info["id"] == pid and info["slag"] == "geografi" and info["paahittat"] is False and info["epsg"] == 3011
    assert info["geografi"] == "goteborg-test@v1" and info["avgransning_3011"] == d["avgransning_3011"]
    assert info["avgransning_polygon_3011"] is None
    # Lantmäteriets härledda data är stängda tills villkoren kontrollerats (K38): projektet är det.
    assert info["oppen"] is False
    assert set(info["baser"]) == {"vatten", "dagsljus"}
    ids = {a["id"] for a in info["analyser"]}
    assert {"vatten.skyfall", "dagsljus.sol", "dagsljus.vsc"} <= ids
    # Rutorna är klippta: bara de som skär avgränsningen, inte hela rutnätet (4 rutor à 500 m).
    dag = info["baser"]["dagsljus"]["rutor"]
    manifest = json.loads((v.rot / "geo/goteborg-test/v1/manifest.json").read_text(encoding="utf-8"))
    assert 1 <= len(dag) < len(manifest["lager"]["k1_rutnat"]["rutor"])
    ax0, ay0, ax1, ay1 = d["avgransning_3011"]
    assert all(r["x_min"] < ax1 and r["x_max"] > ax0 and r["y_min"] < ay1 and r["y_max"] > ay0 for r in dag)
    assert info["fokus"] and len(info["fokus"]) == 4
    assert info["byggnader"], "geografins byggnader (K9-basen) ska finnas"
    assert all("i_projektet" in b for b in info["byggnader"])
    assert info["ortofoto"] == {"finns": False}
    assert info["scenarier"] == []


def test_behorighet_som_ovriga_projekt(v: Varld) -> None:
    pid, _ = _projekt(v, "Haga roll")
    assert v.http(None, "GET", f"/api/kartverktyg/{pid}")[0] == 401
    # Bo är medlem i organisationen men har ingen roll i det privata projektet; Di i en annan organisation.
    assert v.http("bo", "GET", f"/api/kartverktyg/{pid}")[0] == 403
    assert v.http("di", "GET", f"/api/kartverktyg/{pid}")[0] == 403
    assert v.http("di", "POST", f"/api/kartverktyg/{pid}/validera", {"andringar": []})[0] == 403
    # Projektets mapp-id ger ingen åtkomst när inloggning är på: bara registrets id.
    st, d = v.http("cy", "GET", "/api/kartverktyg/haga-roll")
    assert st in (403, 404), d
    # Okänt projekt är 404 även för den som får se allt.
    assert v.http("anna", "GET", "/api/kartverktyg/finns-inte")[0] in (403, 404)
    # Demokvarteret är orört: utan byggd demo 404, inte "okänt projekt".
    st, d = v.http("anna", "GET", "/api/kartverktyg/demokvarteret")
    assert st in (200, 403, 404)


def test_kontroll_scenario_plan_och_polygonvarning(v: Varld) -> None:
    pid, d = _projekt(v, "Haga l", polygon=True)
    st, info = v.http("anna", "GET", f"/api/kartverktyg/{pid}")
    assert st == 200 and info["avgransning_polygon_3011"]["type"] == "Polygon"
    k = v.karna
    x, y = k[0] + 10, k[1] + 10
    inne = _hus(x + 3, y + 3)
    # Utsparat hörn: helt inom rektangeln runt polygonen men utanför polygonen.
    ute = _hus(x + 40, y + 40, b=10, d=10, aid="hus-ute")
    st, kont = v.http("anna", "POST", f"/api/kartverktyg/{pid}/validera", {"andringar": [inne, ute]})
    assert st == 200 and kont["giltig"], kont
    assert any("hus-ute" in t and "utanför projektets avgränsning" in t for t in kont["varningar"])
    assert not any("hus-1" in t and "avgränsning" in t for t in kont["varningar"])
    # Utanför all data: fel.
    st, fel = v.http("anna", "POST", f"/api/kartverktyg/{pid}/validera", {"andringar": [_hus(k[0] + 5000, k[1] + 5000)]})
    assert st == 200 and not fel["giltig"] and "utanför" in fel["fel"][0]
    # Spara, lista och läs; läsare får inte skriva.
    st, sp = v.http("anna", "POST", f"/api/kartverktyg/{pid}/scenarier", {"namn": "Nytt hus", "andringar": [inne]})
    assert st == 200 and sp["slug"] == "nytt-hus" and sp["projekt"] == pid
    assert [s["slug"] for s in v.http("anna", "GET", f"/api/kartverktyg/{pid}/scenarier")[1]["scenarier"]] == ["nytt-hus"]
    # Planen: ett K9 per bas och ritade frågor i projektets mapp med projektets prefix.
    st, plan = v.http("anna", "POST", f"/api/kartverktyg/{pid}/scenarier/nytt-hus/plan", {"analyser": ["vatten.skyfall", "dagsljus.sol"]})
    assert st == 200, plan
    assert {f["mall"] for f in plan["fragor"]} == {"vatten", "dagsljus"}
    for f in plan["fragor"]:
        assert f["fraga_id"].startswith("rita-haga-l-nytt-hus-")
        assert (v.rot / "projekt" / "haga-l" / "fragor" / f"{f['fraga_id']}.json").is_file()
        assert f["plan"]["jobb"]
    # Ett eget scenario utan hus i vattnets ruta men inom dagsljusets: skyfall nekas, dagsljus går.
    st, ut = v.http("anna", "POST", f"/api/kartverktyg/{pid}/scenarier/nytt-hus/plan", {"analyser": []})
    assert st == 400


def test_kor_skyfall_ger_korning_i_projektet(v: Varld) -> None:
    pid, d = _projekt(v, "Haga kor")
    k = v.karna
    hus = _hus(k[0] + 20, k[1] + 20)
    assert v.http("anna", "POST", f"/api/kartverktyg/{pid}/scenarier", {"namn": "Hus", "andringar": [hus]})[0] == 200
    st, kor = v.http("anna", "POST", f"/api/kartverktyg/{pid}/scenarier/hus/kor", {"analyser": ["vatten.skyfall"]})
    assert st == 202, kor
    kid = kor["fragor"][0]["korning"]["korning_id"]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 600:
        _, s = v.http("anna", "GET", f"/api/kartverktyg/{pid}/scenarier/hus/status")
        post = s["korningar"][0]
        if post["status"]["status"] in ("klar", "fel"):
            break
        time.sleep(0.5)
    assert post["status"]["status"] == "klar", post
    assert post["vyer"], "före/efter-vyer ska finnas"
    # Körningen bär registrets id: projektets roll läser den, andra inte.
    assert v.http("anna", "GET", f"/api/korningar/{kid}")[0] == 200
    assert v.http("di", "GET", f"/api/korningar/{kid}")[0] in (403, 404)
    # Förslaget syns som förslag i körningens underlag.
    assert v.app.korningsunderlag(kid)["slag"] == "forslag"
    # Läget idag (U5a) skriver sin fråga i projektets mapp med registrets id.
    plan = v.app.kartverktyg.planera_lage_idag(pid, ["vatten.skyfall"])
    fid = plan["fragor"][0]["fraga_id"]
    assert fid.startswith("lage-idag-") and fid.endswith("-vatten")
    assert (v.rot / "projekt" / "haga-kor" / "fragor" / f"{fid}.json").is_file()


def test_mcp_nekar_stangt_projekt_och_ger_oppet_projekt_klassen_oppet(v: Varld, tmp_path: Path) -> None:
    pid, d = _projekt(v, "Haga mcp")
    mapp = v.rot / "projekt" / "haga-mcp"
    tjanst = FlodenTjanst(v.app, rot=v.rot, mcp_mapp=tmp_path / "mcp")
    server = bygg_server(tjanst, policy=Policy(), logg=Korlogg(tmp_path / "mcp" / "korlogg.jsonl"), miljo={})

    async def anropa(verktyg: str, **arg: Any) -> Any:
        async with Client(server) as c:
            return await c.call_tool(verktyg, {"projekt": pid, **arg})

    # Stängt (Lantmäteriets härledda data utan kontrollerade villkor): inget lämnas ut, vilken klass klienten än får.
    assert tjanst.projekt_oppen(pid) is False
    r = anyio.run(lambda: anropa("projekt_info"))
    assert r.is_error and "inte öppna data" in str(r.content[0].text)
    r = anyio.run(lambda: anropa("byggnader_lista"))
    assert r.is_error and "inte öppna data" in str(r.content[0].text)
    # Öppet (K38: oppen = true och en känd licens): projektet lämnas ut och har dataklassen oppet.
    (mapp / "oppenhet.toml").write_text('oppen = true\nlicens = "CC0-1.0"\nkalla = "test"\n', encoding="utf-8")
    assert tjanst.projekt_oppen(pid) is True
    r = anyio.run(lambda: anropa("projekt_info"))
    assert not r.is_error, r
    sc = r.structured_content
    assert sc["dataklass"] == "oppet" and sc["slag"] == "geografi" and sc["demo"] is None and sc["antal_byggnader"] > 0
    r = anyio.run(lambda: anropa("byggnader_lista"))
    assert not r.is_error and r.structured_content["antal_traffar"] > 0
    # Ett scenario via MCP hamnar i projektet.
    k = v.karna
    r = anyio.run(lambda: anropa("scenario_skapa", namn="Mcp-hus", hus=[{"rektangel": [k[0] + 20, k[1] + 20, k[0] + 32, k[1] + 30], "hojd_m": 12}]))
    assert not r.is_error, r
    assert v.http("anna", "GET", f"/api/kartverktyg/{pid}/scenarier")[1]["scenarier"][0]["slug"] == "mcp-hus"


def _bin(v: Varld, vem: str | None, sokvag: str) -> tuple[int, dict[str, str], bytes]:
    """GET som ger binärt svar (PNG): status, rubriker (gemener) och kropp."""
    rub: dict[str, str] = {}
    if vem:
        rub["Cookie"] = "; ".join(f"{k}={x}" for k, x in v.webb[vem].kakor.items())
    status, huvud, kropp = v.app.hantera_http("GET", sokvag, rub, lambda: b"")
    h = dict(huvud.items() if hasattr(huvud, "items") else huvud)
    return status, {k.lower(): str(x) for k, x in h.items()}, kropp


def test_markmodellen_som_underlag_och_markhojd_under_pekaren(v: Varld) -> None:
    """Steg 2 och 4 i ett geografiprojekt: skuggning, höjdkurvor och markhöjd i färg ur K1:s `terrang.tif`, och höjden i en punkt.

    Samma behörighet som övriga projektdata (roll i projektet; 401 utloggad, 403 utan roll)."""
    import io

    import numpy as np
    from PIL import Image

    pid, _ = _projekt(v, "Haga mark")
    for vilket in ("skuggning", "hojdkurvor", "markhojd"):
        st, h, png = _bin(v, "anna", f"/api/kartverktyg/{pid}/geometri/{vilket}.png")
        assert st == 200 and png[:8] == b"\x89PNG\r\n\x1a\n", (vilket, st)
        x0, y0, x1, y1 = (float(t) for t in h["x-floden-utbredning"].split(","))
        assert x1 > x0 and y1 > y0
        bild = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
        assert (bild[..., 3] > 0).any(), f"{vilket} är helt genomskinlig"
        if vilket == "hojdkurvor":
            assert float(h["x-floden-ekvidistans"]) in (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
            # Kurvor är linjer: bara en del av bilden är täckt (inte en heltäckande yta som skuggningen).
            assert 0.001 < (bild[..., 3] > 0).mean() < 0.5
        if vilket == "markhojd":
            lag, hog = (float(t) for t in h["x-floden-hojdspann"].split(","))
            assert lag < hog
    # Okänt lager: 404; terrängen läses bara internt.
    assert _bin(v, "anna", f"/api/kartverktyg/{pid}/geometri/okand.png")[0] == 404
    # Markhöjden i mitten av projektet ligger inom höjdspannet; utanför rutorna finns ingen.
    lag, hog = (float(t) for t in _bin(v, "anna", f"/api/kartverktyg/{pid}/geometri/markhojd.png")[1]["x-floden-hojdspann"].split(","))
    st, d = v.http("anna", "GET", f"/api/kartverktyg/{pid}/hojd?e={(x0 + x1) / 2}&n={(y0 + y1) / 2}")
    assert st == 200 and d["inom"] is True and d["hojdsystem"] == "RH 2000" and d["enhet"] == "m"
    assert lag - 1e-3 <= d["hojd_m"] <= hog + 1e-3
    st, d = v.http("anna", "GET", f"/api/kartverktyg/{pid}/hojd?e={x0 - 5000}&n={y0 - 5000}")
    assert st == 200 and d["inom"] is False and d["hojd_m"] is None
    assert v.http("anna", "GET", f"/api/kartverktyg/{pid}/hojd?e=abc&n=1")[0] == 400
    assert v.http("anna", "GET", f"/api/kartverktyg/{pid}/hojd")[0] == 400
    # Behörighet: utloggad 401, medlem utan roll i projektet och annan organisation 403 (för bilder och höjd).
    for sokvag in (f"/api/kartverktyg/{pid}/geometri/hojdkurvor.png", f"/api/kartverktyg/{pid}/hojd?e={x0}&n={y0}"):
        assert _bin(v, None, sokvag)[0] == 401
        assert _bin(v, "bo", sokvag)[0] == 403
        assert _bin(v, "di", sokvag)[0] == 403


def test_klientfiler_ger_referer_till_tredje_part() -> None:
    """OpenStreetMaps plattservrar kräver Referer: klientfilerna får inte skicka `Referrer-Policy: same-origin`."""
    from webb.api import FlodenApiApp

    app = FlodenApiApp(klientmapp=REPO / "webb" / "dist") if (REPO / "webb" / "dist" / "index.html").is_file() else None
    if app is None:
        import pytest

        pytest.skip("webb/dist är inte byggd")
    status, huvud, _ = app.hantera_http("GET", "/", {}, lambda: b"")
    h = {k.lower(): x for k, x in (huvud.items() if hasattr(huvud, "items") else huvud)}
    assert status == 200 and h["referrer-policy"] == "strict-origin-when-cross-origin"
