# ruff: noqa: E501
"""Skärmbilder av steg 1 ("Nytt projekt", `#/nytt`) och steg 5 (Skapa rapport), bred och smal.

Förutsätter `floden demo` och `floden webb` (http://127.0.0.1:8765, inloggning av) och byggd webb. Steg 1 kräver inloggning
och en mappad geografi, som en lokal demo inte har: `/api/geografier` och `POST /api/projekt` **svaras därför av
webbläsarens rutt med påhittade värden** (påhittad geografi, inget projekt skapas). Steg 5 går mot den riktiga demon:
rapporten skapas på riktigt (jobb, kontroll, PDF och Word).

    python tools/flode_skarmbild_skapa.py [--url ...] [--chrome ...] [--ut docs/bilder]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import Page, Route, sync_playwright

UT = Path(__file__).resolve().parents[1] / "docs" / "bilder"
STORLEKAR = [("", 1440, 900, False), ("-smal", 390, 844, True)]
KARNA = [170000.0, 6400000.0, 170500.0, 6400500.0]
GRANS = [169300.0, 6399300.0, 171200.0, 6401200.0]


def _pol(b: list[float]) -> dict[str, object]:
    x0, y0, x1, y1 = b
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


GEOGRAFIER = {
    "geografier": [
        {
            "ref": "pahittad-geografi@v1", "id": "pahittad-geografi", "version": "v1", "namn": "Påhittad geografi (skärmbild)",
            "kommun": "Påhittad", "epsg": 3011, "karna_3011": KARNA, "karna_polygon_3011": _pol(KARNA),
            "gransen_3011": GRANS, "gransen_polygon_3011": _pol(GRANS), "buffert_m": 700.0, "varningar": [],
            "manifest_sha256": "0" * 64, "far_skapa_projekt": True,
        }
    ],
    "far_skapa_projekt": True,
    "organisationer": [{"id": "o-1", "namn": "Påhittad organisation"}],
}  # fmt: skip


def _svara(route: Route) -> None:
    r = route.request
    if r.url.endswith("/api/geografier"):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(GEOGRAFIER))
    elif r.url.endswith("/api/projekt") and r.method == "POST":
        ut = {
            "kontrakt": "K48/v1", "projekt_id": "p-skarmbild", "namn": "Skärmbild", "organisation": "o-1",
            "organisation_synlig": True, "geografi": "pahittad-geografi@v1", "avgransning_3011": KARNA, "roll": "utredare",
            "varningar": ["Delar av avgränsningen ligger utanför geografins giltiga kärna."],
        }  # fmt: skip
        route.fulfill(status=201, content_type="application/json", body=json.dumps(ut))
    else:
        route.continue_()


def _rita(sida: Page) -> None:
    ruta = sida.locator("#np-karta").bounding_box()
    assert ruta is not None
    b, h = ruta["width"], ruta["height"]
    x0, y0 = ruta["x"] + b * 0.4, ruta["y"] + h * 0.4
    sida.mouse.move(x0, y0)
    sida.mouse.down()
    sida.mouse.move(x0 + b * 0.1, y0 + h * 0.1, steps=6)
    sida.mouse.up()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--ut", default=str(UT))
    a = ap.parse_args()
    ut = Path(a.ut)
    ut.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        wl = p.chromium.launch(
            executable_path=a.chrome,
            args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        for suffix, bredd, hojd, hel in STORLEKAR:
            sida = wl.new_page(viewport={"width": bredd, "height": hojd})
            fel: list[str] = []
            sida.on("pageerror", lambda e, f=fel: f.append(str(e)))
            sida.route("**/api/**", _svara)
            sida.goto(f"{a.url}/#/nytt")
            sida.wait_for_selector("#np-karta", timeout=30_000)
            sida.wait_for_timeout(5000)
            _rita(sida)
            sida.fill("#np-namn", "Exempelprojekt")
            sida.wait_for_timeout(1000)
            sida.screenshot(path=str(ut / f"flode-1-nytt-projekt{suffix}.png"), full_page=hel)
            print(f"flode-1-nytt-projekt{suffix}.png")
            sida.close()
            sida = wl.new_page(viewport={"width": bredd, "height": hojd})
            sida.on("pageerror", lambda e, f=fel: f.append(str(e)))
            sida.goto(f"{a.url}/#/p/demokvarteret/rapport")
            sida.wait_for_selector("#rp-skapa-knapp", timeout=30_000)
            sida.wait_for_timeout(1500)
            sida.screenshot(path=str(ut / f"flode-5-skapa-rapport{suffix}.png"), full_page=hel)
            print(f"flode-5-skapa-rapport{suffix}.png")
            sida.click("#rp-skapa-knapp")
            sida.wait_for_selector("text=Klar", timeout=240_000)
            sida.wait_for_timeout(1500)
            sida.screenshot(path=str(ut / f"flode-5-skapa-rapport-klar{suffix}.png"), full_page=hel)
            print(f"flode-5-skapa-rapport-klar{suffix}.png")
            sida.close()
            if fel:
                raise SystemExit("Sidfel: " + "; ".join(fel))
        wl.close()


if __name__ == "__main__":
    main()
