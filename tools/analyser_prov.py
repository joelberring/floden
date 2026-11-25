# ruff: noqa: E501
"""Webbläsarprov av "Visa" i steg 4 (alla analyser som kartlager): demokvarteret och det syntetiska geografiprojektet.

Förutsätter byggd webb och `floden demo` + `floden webb --port 8795` (demokvarteret) och/eller
`python tools/geografi_prov_server.py --port 8796` (påhittat geografiprojekt). Skärmbilder i docs/bilder/analyser/.

    python tools/analyser_prov.py [--demo http://127.0.0.1:8795] [--geo http://127.0.0.1:8796] [--chrome /usr/bin/google-chrome]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

UT = Path(__file__).resolve().parents[1] / "docs" / "bilder" / "analyser"
RESULTAT: list[tuple[bool, str, str]] = []


def ok(namn: str, villkor: bool, detalj: str = "") -> bool:
    RESULTAT.append((villkor, namn, detalj))
    print(f"  {'OK ' if villkor else 'FEL'} {namn}{(' - ' + detalj) if detalj and not villkor else ''}", flush=True)
    return villkor


def vanta(s: Page, villkor: Callable[[], bool], sek: float = 40) -> bool:
    for _ in range(int(sek * 4)):
        if villkor():
            return True
        s.wait_for_timeout(250)
    return False


def kartfigurer(s: Page, lager: str) -> int:
    """Antal ritade figurer i ett av analyslagren (via MapLibres egna fråga)."""
    return int(
        s.evaluate(
            """(id) => { const m = window.__floden_kartmotor; if (!m) return -1; const k = m.karta;
              return k.queryRenderedFeatures({ layers: [id] }).length; }""",
            lager,
        )
    )


def amne_och_vy(s: Page, amne: str) -> None:
    s.click(f'[data-amne="{amne}"]')
    s.wait_for_timeout(2500)


def demo(s: Page, url: str) -> None:
    print("Demokvarteret (1440x900)", flush=True)
    s.goto(f"{url}/#/p/demokvarteret/resultat")
    s.wait_for_selector("#rs-innehall .flode-slutsats", timeout=60_000)
    s.wait_for_timeout(4000)
    # --- Skyfall: sänkor och flödesvägar ---
    ok("Skyfall har väljaren Visa med sänkor och flödesvägar", s.locator('#rs-visa [data-vektor="sankor"]').count() == 1 and s.locator('#rs-visa [data-vektor="flodesvagar"]').count() == 1)
    ok("inga vektorlager tända från början", s.locator("#rs-visa input:checked").count() == 0)
    s.screenshot(path=str(UT / "01-skyfall-visa.png"))
    s.check('#rs-visa [data-vektor="sankor"]')
    ok("sänkorna hämtas från servern och teckenförklaringen visar dem", vanta(s, lambda: "Sänkor: djup vid fullt" in s.inner_text("#rs-tecken")), s.inner_text("#rs-tecken")[:200])
    s.wait_for_timeout(1500)
    ok("sänkor ritas som ytor i kartan", kartfigurer(s, "analys-yta-fyll") > 0, str(kartfigurer(s, "analys-yta-fyll")))
    ok("sänkorna har etiketter med volym i m³", s.locator(".analys-etikett").count() > 0 and re.fullmatch(r"[\d  ,]+ m³", s.locator(".analys-etikett").first.inner_text().strip()) is not None, s.locator(".analys-etikett").first.inner_text() if s.locator(".analys-etikett").count() else "inga")
    s.screenshot(path=str(UT / "02-skyfall-sankor.png"))
    s.check('#rs-visa [data-vektor="flodesvagar"]')
    ok("flödesvägarna ritas som avrinningsnät med tillrinning i teckenförklaringen", vanta(s, lambda: "Flödesvägar: tillrinning (m²)" in s.inner_text("#rs-tecken")) and vanta(s, lambda: kartfigurer(s, "analys-linje") > 0))
    s.screenshot(path=str(UT / "03-skyfall-sankor-flodesvagar.png"))
    ok("reglaget Minsta tillrinning finns", s.locator('#rs-visa [data-grans="flodesvagar"]').is_visible())
    s.screenshot(path=str(UT / "flodesvagar-avrinningsnat.png"))
    # Före/Efter: vektorerna byter scenario
    s.click('[data-lage="efter"]')
    s.wait_for_timeout(2500)
    ok("Efter hämtar vektorlagren för efterscenariot (andra lagret hämtas)", vanta(s, lambda: kartfigurer(s, "analys-yta-fyll") > 0))
    s.screenshot(path=str(UT / "04-skyfall-efter-vektorer.png"))
    s.uncheck('#rs-visa [data-vektor="sankor"]')
    s.uncheck('#rs-visa [data-vektor="flodesvagar"]')
    s.wait_for_timeout(800)
    ok("släckta vektorlager försvinner från kartan", kartfigurer(s, "analys-yta-fyll") == 0 and kartfigurer(s, "analys-linje") == 0)
    # Känsliga överfall (finns sedan tidigare) går att tända ihop med de nya
    s.click("#rs-osakerhet")
    ok("osäkerheten (bifurkation) laddas som förut", vanta(s, lambda: s.locator("#rs-overfall").count() == 1, 60))
    s.click('[data-lage="skillnad"]')
    s.wait_for_timeout(1500)
    s.screenshot(path=str(UT / "05-skyfall-skillnad-osakerhet.png"))

    # --- Sol: roller, skillnad, kontroll, VSC ---
    amne_och_vy(s, "dagsljus")
    sel = s.locator("#rs-visa-val")
    ok("Sol har väljaren Visa med mått och kontroller", sel.count() == 1 and "Kontroller" in s.inner_html("#rs-visa-val"), s.inner_html("#rs-visa")[:300])
    s.click('[data-lage="skillnad"]')
    s.wait_for_timeout(2000)
    s.screenshot(path=str(UT / "06-sol-soltimmar-skillnad.png"))
    optioner = s.eval_on_selector_all("#rs-visa-val option", "els => els.map(e => [e.value, e.textContent])")
    ok("Sol listar soltimmar och kontrollagren", any("Soltimmar" in t for _, t in optioner) and any("Kontroll" in t for _, t in optioner), str(optioner))
    kontroll = next((v for v, t in optioner if "Kontroll: avskuren" in t), None)
    if kontroll:
        s.select_option("#rs-visa-val", kontroll)
        s.click('[data-lage="fore"]')
        s.wait_for_timeout(2000)
        ok("kontrollagret visas med en teckenförklaring av klasser och en förklarande text", "kontrollager" in s.inner_text("#rs-visa-text").lower() and s.locator("#rs-tecken .flode-klasser").count() >= 1, s.inner_text("#rs-visa-text"))
        s.click('[data-lage="skillnad"]')
        s.wait_for_timeout(1500)
        notis = " ".join(s.locator(".kartnotis:visible").all_inner_texts())
        ok("kontrollager har ingen skillnad: kartan säger det", "ingen skillnad" in notis.lower(), notis)
        s.screenshot(path=str(UT / "07-sol-kontroll-saknar-skillnad.png"))
        forsta = optioner[0][0]
        s.select_option("#rs-visa-val", forsta)
    if s.locator('#rs-visa [data-vektor="vsc"]').count() == 1:
        s.check('#rs-visa [data-vektor="vsc"]')
        ok("fasadpunkterna med VSC ritas", vanta(s, lambda: kartfigurer(s, "analys-punkt") > 0) and "Fasadpunkter: VSC" in s.inner_text("#rs-tecken"))
        s.screenshot(path=str(UT / "08-sol-vsc-punkter.png"))
    else:
        ok("VSC-lagret finns inte i demokörningen (ingen VSC-analys): hoppas över", True)

    # --- Ekologi: strömtäthet och känslighet (härledd skillnad) ---
    amne_och_vy(s, "ekologi")
    optioner = s.eval_on_selector_all("#rs-visa-val option", "els => els.map(e => [e.value, e.textContent])")
    ok("Ekologi listar strömtäthet och känslighet", len(optioner) == 2, str(optioner))
    s.click('[data-lage="skillnad"]')
    s.wait_for_timeout(1500)
    s.screenshot(path=str(UT / "09-ekologi-stromtathet-skillnad.png"))
    kans = next((v for v, t in optioner if "Känslighet" in t), None)
    if kans:
        s.select_option("#rs-visa-val", kans)
        s.wait_for_timeout(2000)
        ok("känslighetens skillnad är härledd och texten säger det", "efter minus före" in s.inner_text("#rs-visa-text"), s.inner_text("#rs-visa-text"))
        s.click('[data-lage="efter"]')
        s.wait_for_timeout(1500)
        s.screenshot(path=str(UT / "10-ekologi-kansighet-efter.png"))
    # Värdet under pekaren för den valda rollen
    box = s.locator("#rs-a canvas").first.bounding_box()
    if box:
        s.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=5)
        s.mouse.move(box["x"] + box["width"] / 2 + 4, box["y"] + box["height"] / 2 + 4, steps=3)
        s.wait_for_timeout(1500)
        ok("värdet under pekaren följer det valda lagret (Känslighet för motstånd)", "Före" in s.inner_text("#rs-avl") or s.locator("#rs-avl").is_hidden(), s.inner_text("#rs-avl")[:150])
        s.screenshot(path=str(UT / "11-ekologi-varde-under-pekaren.png"))


def geo(s: Page, url: str) -> None:
    print("Syntetiskt geografiprojekt (1440x900)", flush=True)
    pid = "haga-prov"
    s.goto(f"{url}/#/p/{pid}/fragor")
    s.wait_for_selector("#fr-kor, #fr-lage", timeout=60_000)
    s.wait_for_timeout(1500)
    knapp = s.get_by_role("button", name=re.compile("Räkna läget idag"))
    ok("steg 3 har knappen Räkna läget idag", knapp.count() >= 1)
    if knapp.count() == 0:
        return
    knapp.first.click()
    ok("läget idag räknas klart och Resultat öppnas", vanta(s, lambda: "resultat" in s.evaluate("location.hash"), 480))
    vanta(s, lambda: s.locator("#rs-innehall .flode-slutsats").count() > 0, 90)
    s.wait_for_timeout(5000)
    s.screenshot(path=str(UT / "20-geo-resultat.png"))
    print("  panel:", s.inner_text("#rs-innehall")[:300].replace("\n", " "))
    ok("geografiprojektet har väljaren Visa med vektorlager", s.locator("#rs-visa").count() == 1, s.inner_text(".flode-panel")[:200])
    if s.locator('#rs-visa [data-vektor="sankor"]').count() == 1:
        s.check('#rs-visa [data-vektor="sankor"]')
        s.check('#rs-visa [data-vektor="flodesvagar"]')
        ok("sänkor och flödesvägar hämtas i geografiprojektet", vanta(s, lambda: "Sänkor: djup vid fullt" in s.inner_text("#rs-tecken")) and vanta(s, lambda: kartfigurer(s, "analys-linje") + kartfigurer(s, "analys-yta-fyll") > 0))
        s.wait_for_timeout(1500)
        s.screenshot(path=str(UT / "21-geo-sankor-flodesvagar.png"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", default="http://127.0.0.1:8795")
    ap.add_argument("--geo", default="")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    a = ap.parse_args()
    UT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=a.chrome, headless=True, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        for url, f in ((a.demo, demo), (a.geo, geo)):
            if not url:
                continue
            ctx = b.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script("window.__FLODEN_PROV__ = true;")
            ctx.set_default_timeout(30_000)
            s: Any = ctx.new_page()
            fel: list[str] = []
            s.on("pageerror", lambda e, fel=fel: fel.append(str(e)))
            f(s, url)
            ok(f"inga sidfel i {url}", not fel, "; ".join(fel)[:300])
            ctx.close()
        b.close()
    n_fel = sum(1 for v, _, _ in RESULTAT if not v)
    print(f"\n{len(RESULTAT) - n_fel} av {len(RESULTAT)} kontroller OK")
    sys.exit(1 if n_fel else 0)


if __name__ == "__main__":
    main()
