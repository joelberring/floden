"""Tar skärmbilder av webbens frågesida (docs/bilder/webb-*.png).

Förutsätter att API:t och klienten körs och att demokvarteret är byggt:

    floden dagsljus demo --fore-efter                 # bygger data/cache/exempel/dagsljus-kvarter
    PYTHONPATH=src python -m webb.api                 # API på :8765
    (cd webb && npm run dev)                          # klient på :5173
    python tools/webb_skarmbild.py                    # kräver playwright och Chrome/Chromium

Argument: --url (klientens adress), --chrome (sökväg till Chrome/Chromium).

Med `--demo` tas i stället docs/bilder/demo-*.png mot `floden webb` (som serverar klienten själv,
standard http://127.0.0.1:8765) efter `floden demo`. Inget körs om: resultaten läses ur cachen.
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


def _valj_lager(sida: Page, lager_id: str) -> None:
    sida.click(f'input[name="lager"][value="{lager_id}"]', force=True)
    sida.wait_for_timeout(500)


def _hovra(sida: Page, rel_x: float, rel_y: float) -> None:
    """Flyttar markören till en relativ punkt i kartytan och väntar in avläsningen."""
    sida.locator("#vy-yta").scroll_into_view_if_needed()
    ruta = sida.locator("#vy-yta").bounding_box()
    assert ruta is not None
    x, y = ruta["x"] + ruta["width"] * rel_x, ruta["y"] + ruta["height"] * rel_y
    sida.mouse.move(x - 5, y - 5)
    sida.mouse.move(x, y)
    sida.wait_for_timeout(900)


def _demo(sida: Page, url: str) -> None:
    sida.goto(f"{url}/#/fragor")
    sida.wait_for_selector("[data-demo=demo_dagsljus]")
    sida.wait_for_timeout(500)
    sida.screenshot(path=str(UT / "demo-start.png"))

    sida.click("[data-demo=demo_vatten]")
    sida.wait_for_selector(".resultat-karta", timeout=60_000)
    sida.wait_for_timeout(1200)
    sida.locator("#fr-resultat").scroll_into_view_if_needed()
    _hovra(sida, 0.5, 0.5)
    _resultat_med_varningar(sida, UT / "demo-vatten.png")

    sida.click("[data-demo=demo_dagsljus]")
    sida.wait_for_selector(".resultat-karta", timeout=60_000)
    sida.wait_for_timeout(1200)
    sida.locator("#fr-resultat").scroll_into_view_if_needed()
    _hovra(sida, 0.34, 0.77)
    _resultat_med_varningar(sida, UT / "demo-dagsljus.png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="ta demo-*.png mot floden webb")
    ap.add_argument("--url", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args()
    url = args.url or ("http://127.0.0.1:8765" if args.demo else "http://127.0.0.1:5173")
    UT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        webblasare = p.chromium.launch(executable_path=args.chrome, args=["--no-sandbox"])
        sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
        if args.demo:
            _demo(sida, url)
            webblasare.close()
            return

        _kor_fraga(sida, url, "dagsljus_fore_efter")
        sida.locator("#fr-plan").screenshot(path=str(UT / "webb-plan.png"))
        sida.click("#kor-fraga")
        sida.wait_for_selector(".resultat-karta", timeout=600_000)
        sida.wait_for_timeout(1500)
        sida.locator("#fr-resultat").scroll_into_view_if_needed()
        _resultat_med_varningar(sida, UT / "webb-resultat.png")

        _kor_fraga(sida, url, "vatten_a1_uppfyllnad")
        sida.click("#kor-fraga")
        sida.wait_for_selector(".resultat-karta", timeout=120_000)
        sida.wait_for_timeout(1000)
        sida.locator(".resultat-karta").screenshot(path=str(UT / "webb-vatten.png"))
        webblasare.close()


if __name__ == "__main__":
    main()
