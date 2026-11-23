"""Tar skärmbilder av flödets fem steg (docs/bilder/flode-*.png) och av Matematik och forskning
(docs/bilder/matematik*.png), bred 1440x900 och smal 390x844.

Förutsätter `floden demo` och `floden webb` (serverar klienten, standard http://127.0.0.1:8765):

    python -c "from floden.cli import main; main(['webb', '--port', '8765'])"
    python tools/flode_skarmbild.py [--url ...] [--chrome ...] [--projekt demokvarteret]

Inloggning ska vara av (standard lokalt). Resultat och Rapport läser befintliga körningar och
rapporter; inget körs om. Kartan är WebGL: programvaruritning används utan grafikkort.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

UT = Path(__file__).resolve().parents[1] / "docs" / "bilder"
STEG = [
    ("1-projekt", ""),
    ("2-forslag", "forslag"),
    ("3-fragor", "fragor"),
    ("4-resultat", "resultat"),
    ("5-rapport", "rapport"),
]
# Ekologiläget (skiss): Frågor med ämnet Ekologi och Resultat med fliken Ekologi.
EKOLOGI_STEG = [
    ("3-fragor-ekologi", "fragor", 'button[data-amne="ekologi"]'),
    ("4-resultat-ekologi", "resultat", 'button[role="tab"][data-amne="ekologi"]'),
]
STORLEKAR = [("", 1440, 900, False), ("-smal", 390, 844, True)]


def _vanta(sida: Page, steg: str) -> None:
    sida.wait_for_selector("h1", timeout=30_000)
    if sida.locator(".maplibregl-canvas").count() > 0:
        sida.wait_for_timeout(5000)
    else:
        sida.wait_for_timeout(2500)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--projekt", default="demokvarteret")
    args = ap.parse_args()
    UT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        webblasare = p.chromium.launch(
            executable_path=args.chrome,
            args=[
                "--no-sandbox",
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
            ],
        )
        for suffix, bredd, hojd, hel in STORLEKAR:
            sida = webblasare.new_page(viewport={"width": bredd, "height": hojd})
            fel: list[str] = []
            sida.on("pageerror", lambda e, f=fel: f.append(str(e)))
            for namn, steg in STEG:
                adress = "#/" if not steg else f"#/p/{args.projekt}/{steg}"
                sida.goto(f"{args.url}/{adress}")
                _vanta(sida, steg)
                sida.screenshot(path=str(UT / f"flode-{namn}{suffix}.png"), full_page=hel)
                print(f"flode-{namn}{suffix}.png")
            for namn, steg, knapp in EKOLOGI_STEG:
                sida.goto(f"{args.url}/#/p/{args.projekt}/{steg}")
                _vanta(sida, steg)
                sida.locator(knapp).first.click()
                sida.wait_for_timeout(6000)
                sida.screenshot(path=str(UT / f"flode-{namn}{suffix}.png"), full_page=hel)
                print(f"flode-{namn}{suffix}.png")
            # Matematik och forskning (#/matematik): lång sida, hela sidan tas även på bred skärm.
            sida.goto(f"{args.url}/#/matematik")
            sida.wait_for_selector("h1", timeout=30_000)
            sida.wait_for_timeout(1500)
            sida.screenshot(path=str(UT / f"matematik{suffix}.png"), full_page=True)
            print(f"matematik{suffix}.png")
            sida.close()
            if fel:
                raise SystemExit("Sidfel: " + "; ".join(fel))
        webblasare.close()


if __name__ == "__main__":
    main()
