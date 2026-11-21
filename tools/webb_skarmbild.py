"""Tar skärmbilder av webbens frågesida (docs/bilder/webb-*.png).

Förutsätter att API:t och klienten körs och att demokvarteret är byggt:

    floden dagsljus demo --fore-efter                 # bygger data/cache/exempel/dagsljus-kvarter
    PYTHONPATH=src python -m webb.api                 # API på :8765
    (cd webb && npm run dev)                          # klient på :5173
    python tools/webb_skarmbild.py                    # kräver playwright och Chrome/Chromium

Argument: --url (klientens adress), --chrome (sökväg till Chrome/Chromium).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

UT = Path(__file__).resolve().parents[1] / "docs" / "bilder"


def _kor_fraga(sida: Page, url: str, ref: str) -> None:
    sida.goto(f"{url}/#/fragor")
    sida.wait_for_selector(f"[data-fraga={ref}]")
    sida.click(f"[data-fraga={ref}]")
    sida.wait_for_selector("#kor-fraga:not([disabled])", timeout=60_000)


def _resultat_med_varningar(sida: Page, fil: Path) -> None:
    """Resultatrubrik, varningar och kartvy med lager (utan de långa tabellerna)."""
    topp = sida.locator("#fr-resultat").bounding_box()
    karta = sida.locator(".resultat-karta").bounding_box()
    assert topp is not None and karta is not None
    rull = sida.evaluate("window.scrollY")  # bounding_box() räknar från fönstrets överkant
    topp["y"] += rull
    karta["y"] += rull
    sida.screenshot(
        path=str(fil),
        full_page=True,
        clip={
            "x": topp["x"] - 8,
            "y": topp["y"],
            "width": topp["width"] + 16,
            "height": karta["y"] + karta["height"] - topp["y"] + 8,
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5173")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args()
    UT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        webblasare = p.chromium.launch(executable_path=args.chrome, args=["--no-sandbox"])
        sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})

        _kor_fraga(sida, args.url, "dagsljus_fore_efter")
        sida.locator("#fr-plan").screenshot(path=str(UT / "webb-plan.png"))
        sida.click("#kor-fraga")
        sida.wait_for_selector(".resultat-karta", timeout=600_000)
        sida.wait_for_timeout(1500)
        sida.locator("#fr-resultat").scroll_into_view_if_needed()
        _resultat_med_varningar(sida, UT / "webb-resultat.png")

        _kor_fraga(sida, args.url, "vatten_a1_uppfyllnad")
        sida.click("#kor-fraga")
        sida.wait_for_selector(".resultat-karta", timeout=120_000)
        sida.wait_for_timeout(1000)
        sida.locator(".resultat-karta").screenshot(path=str(UT / "webb-vatten.png"))
        webblasare.close()


if __name__ == "__main__":
    main()
