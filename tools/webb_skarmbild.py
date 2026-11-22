"""Tar skärmbilder av webbens frågesida (docs/bilder/webb-*.png).

Förutsätter att API:t och klienten körs och att demokvarteret är byggt:

    floden dagsljus demo --fore-efter                 # bygger data/cache/exempel/dagsljus-kvarter
    PYTHONPATH=src python -m webb.api                 # API på :8765
    (cd webb && npm run dev)                          # klient på :5173
    python tools/webb_skarmbild.py                    # kräver playwright och Chrome/Chromium

Argument: --url (klientens adress), --chrome (sökväg till Chrome/Chromium).

Med `--kartverktyg` tas docs/bilder/kartverktyg-*.png (lagerpanel, ritning med fästmarkör,
höjdpanel, före och efter) mot `floden webb`; scenariot som ritas sparas lokalt.

Med `--3d` tas docs/bilder/3d-*.png (soltimmar före och efter på marken, VSC på fasaderna,
vattenyta och ett ritat hus i 3D) mot `floden webb` efter `floden demo`.

Med `--3d-farg` läses pixlar av för punkter med kända färger (kräver `--3d`-vyn, inget sparas).

Med `--demo` tas i stället docs/bilder/demo-*.png (start, projekt, karta bred och smal,
frågesidan) mot `floden webb` (som serverar klienten själv, standard
http://127.0.0.1:8765) efter `floden demo`. Inget körs om: resultaten läses ur cachen.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

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


def _sida(
    sida: Page, url: str, adress: str, vantar_pa: str, fil: str, *, helsida: bool = True
) -> None:
    sida.goto(f"{url}/{adress}")
    sida.wait_for_selector(vantar_pa, timeout=60_000)
    sida.wait_for_timeout(1500)
    sida.screenshot(path=str(UT / fil), full_page=helsida)


def _demo(webblasare: Browser, url: str) -> None:
    """demo-*.png: start, projekt, karta (1440 och 800 px) samt frågesidans resultat."""
    for bredd, suffix in ((1440, ""), (800, "-smal")):
        sida = webblasare.new_page(viewport={"width": bredd, "height": 1000})
        _sida(sida, url, "#/", ".modul-kort", f"demo-start{suffix}.png")
        _sida(sida, url, "#/projekt/demokvarteret", ".jamfor-tabell", f"demo-projekt{suffix}.png")
        sida.goto(f"{url}/#/karta/demokvarteret?vy=dagsljus-hojd")
        sida.wait_for_selector("#vy-yta canvas", timeout=60_000)
        sida.wait_for_timeout(2000)
        _hovra(sida, 0.60, 0.38)
        sida.screenshot(path=str(UT / f"demo-karta{suffix}.png"), full_page=True)
        if bredd == 1440:
            sida.click('[data-vy="vatten-nytt-hus"]')
            sida.wait_for_timeout(1500)
            sida.click('[data-lage="skillnad"]')
            sida.wait_for_timeout(1000)
            _hovra(sida, 0.33, 0.74)
            sida.screenshot(path=str(UT / "demo-karta-vatten.png"), full_page=True)
        sida.close()

    sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
    _sida(sida, url, "#/fragor", "[data-demo=demo_dagsljus]", "demo-fragor.png")
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


def _vy3d(webblasare: Browser, url: str) -> None:
    """3d-*.png: soltimmar före/efter på marken, VSC på fasaderna, vattenyta, ritat hus.

    Chrome körs med programvaru-WebGL (SwiftShader) här; bilderna är därför långsamma att
    rita men visar samma sak som en grafikdator.
    """

    def oppna(vy: str) -> Page:
        sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
        sida.goto(f"{url}/#/karta/demokvarteret?vy={vy}")
        sida.wait_for_selector("#vy-yta canvas", timeout=60_000)
        sida.wait_for_timeout(2000)
        sida.click('[data-visa="3d"]')
        sida.wait_for_selector("#vy3d-yta canvas", timeout=60_000)
        sida.wait_for_timeout(5000)
        return sida

    def ta(sida: Page, namn: str) -> None:
        sida.locator("#vy3d-yta").screenshot(path=str(UT / f"3d-{namn}.png"))

    def lage(sida: Page, namn: str, vanta: int = 3500) -> None:
        sida.click(f'button:text-is("{namn}")')
        sida.wait_for_timeout(vanta)

    sida = oppna("dagsljus-hojd")
    lage(sida, "Snett", 2500)
    lage(sida, "Före")
    ta(sida, "soltimmar-fore")
    lage(sida, "Efter")
    ta(sida, "soltimmar-efter")
    # Längs den smala gatan (E 151350) norrut: gatufasaderna syns från marknivå och uppåt, så att
    # det går att jämföra de mörkare nedre våningarna med de ljusa översta.
    sida.evaluate(
        "window.__floden3d.settKamera({ mal: { e: 151350, n: 6580345, z: 8 },"
        " avstand: 45, azimut: 0, lutning: 68 })"
    )
    sida.wait_for_timeout(5000)
    ta(sida, "vsc-fasader")
    sida.close()

    sida = oppna("vatten-nytt-hus")
    lage(sida, "Efter")
    lage(sida, "Snett", 2500)
    ta(sida, "vatten")
    sida.close()

    # Ett hus som ritas i kartan syns direkt i 3D (samma ritgång som --kartverktyg).
    sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
    sida.goto(f"{url}/#/karta/demokvarteret")
    sida.wait_for_selector("#vy-yta canvas", timeout=60_000)
    sida.wait_for_timeout(2500)
    sida.click('[data-lage="fore"]')
    sida.wait_for_timeout(800)
    sida.evaluate("document.querySelector('#verktygsrad').scrollIntoView()")
    kyta = sida.locator("#vy-yta").bounding_box()
    assert kyta is not None
    sida.keyboard.press("w")
    for fx, fy in ((0.30, 0.15), (0.38, 0.15), (0.38, 0.22), (0.30, 0.22)):
        x, y = kyta["x"] + kyta["width"] * fx, kyta["y"] + kyta["height"] * fy
        sida.mouse.move(x, y)
        sida.mouse.click(x, y)
    sida.keyboard.press("Enter")
    sida.wait_for_selector("#hojd-falt")
    sida.fill("#hojd-falt", "18")
    sida.press("#hojd-falt", "Tab")
    sida.wait_for_timeout(1000)
    sida.evaluate("window.scrollTo(0, 0)")
    sida.click('[data-visa="3d"]')
    sida.wait_for_selector("#vy3d-yta canvas", timeout=60_000)
    sida.wait_for_timeout(4000)
    lage(sida, "Snett", 2500)
    lage(sida, "Före", 2500)
    ta(sida, "ritat-hus")
    sida.close()


_FARGPROV_JS = """() => {
  const v = window.__floden3d, sc0 = v.sidor[0], fp = sc0.fasadPunkter;
  const Points = fp.constructor, Mesh = sc0.terrang.children[0]?.constructor ?? null, Geo = fp.geometry.constructor;
  const Mat = fp.material.constructor, Scene = sc0.scene.constructor, Attr = fp.geometry.attributes.color.constructor;
  const Cam = v.camera.constructor, cam = new Cam(); cam.position.set(0, 0, 10); cam.lookAt(0, 0, 0);
  const r = v.renderer, gl = r.getContext(), res = [];
  const cols = [[0, 32, 77], [49, 68, 107], [102, 105, 112], [203, 186, 105], [255, 234, 70], [43, 93, 140]];
  for (const c of cols) {
    const sc = new Scene(), g = new Geo();
    g.setAttribute('position', new Attr(new Float32Array([0, 0, 0]), 3));
    g.setAttribute('color', new Attr(new Float32Array(c.map((x) => x / 255)), 3));
    sc.add(new Points(g, new Mat({ size: 300, sizeAttenuation: false, vertexColors: true })));
    r.setScissorTest(false); r.setClearColor(0xffffff, 1); r.clear(); r.render(sc, cam);
    const px = new Uint8Array(4);
    gl.readPixels(gl.drawingBufferWidth >> 1, gl.drawingBufferHeight >> 1, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    res.push([c, Array.from(px).slice(0, 3)]);
  }
  return { res, farg: r.outputColorSpace, ton: r.toneMapping };
}"""  # noqa: E501


def _vy3d_farg(webblasare: Browser, url: str) -> None:
    """Rendera punkter med kända färger och läs av pixlarna: en färg i 3D är exakt skalans RGB."""
    sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
    sida.goto(f"{url}/#/karta/demokvarteret?vy=dagsljus-hojd")
    sida.wait_for_selector("#vy-yta canvas", timeout=60_000)
    sida.wait_for_timeout(1500)
    sida.click('[data-visa="3d"]')
    sida.wait_for_function("window.__floden3d !== undefined", timeout=60_000)
    sida.wait_for_timeout(5000)
    ut = sida.evaluate(_FARGPROV_JS)
    for ford, last in ut["res"]:
        diff = max(abs(a - b) for a, b in zip(ford, last, strict=True))
        print(f"skala {ford} -> pixel {last} (största avvikelse {diff})")
        assert diff <= 1, "färgen i 3D avviker från skalans färg"
    print("utdatafärgrum", ut["farg"], "tonmappning", ut["ton"])
    sida.close()


def _kartverktyg(webblasare: Browser, url: str) -> None:
    """kartverktyg-*.png: lagerpanel, ritning med fästmarkör, höjdpanel, före och efter."""
    sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
    sida.goto(f"{url}/#/karta/demokvarteret")
    sida.wait_for_selector("#vy-yta canvas", timeout=60_000)
    sida.wait_for_timeout(2500)
    sida.click('[data-lage="fore"]')
    sida.wait_for_timeout(800)
    sida.evaluate("document.querySelector('#verktygsrad').scrollIntoView()")
    sida.wait_for_timeout(300)
    ruta = sida.locator("#vy-yta").bounding_box()
    assert ruta is not None

    def pa(fx: float, fy: float) -> tuple[float, float]:
        return ruta["x"] + ruta["width"] * fx, ruta["y"] + ruta["height"] * fy

    sida.locator(".lager-kolumn .panel").first.screenshot(path=str(UT / "kartverktyg-lager.png"))
    sida.keyboard.press("w")
    for fx, fy in ((0.30, 0.15), (0.38, 0.15), (0.38, 0.22)):
        x, y = pa(fx, fy)
        sida.mouse.move(x, y)
        sida.mouse.click(x, y)
    x, y = pa(0.247, 0.118)  # nära ett hörn på kvarterets innervägg: fästmarkören syns
    sida.mouse.move(x, y, steps=5)
    sida.wait_for_timeout(500)
    sida.screenshot(
        path=str(UT / "kartverktyg-rita.png"),
        clip={
            "x": max(0, ruta["x"] - 10),
            "y": max(0, ruta["y"] - 150),
            "width": ruta["width"] + 20,
            "height": ruta["height"] + 180,
        },
    )
    x, y = pa(0.30, 0.22)
    sida.mouse.click(x, y)
    sida.keyboard.press("Enter")
    sida.wait_for_selector("#hojd-falt")
    sida.fill("#hojd-falt", "18")
    sida.press("#hojd-falt", "Tab")
    sida.wait_for_timeout(1200)
    sida.locator("#hojdpanel").screenshot(path=str(UT / "kartverktyg-hojd.png"))
    sida.click("#rakna-plan")
    sida.wait_for_selector("#rakna-starta", timeout=60_000)
    sida.click("#rakna-starta")
    sida.wait_for_function(
        "document.querySelector('#rakna')?.textContent.includes('Klart')", timeout=900_000
    )
    sida.wait_for_timeout(2000)
    sida.click('[data-lage="sidavid"]')
    sida.wait_for_timeout(3000)
    sida.evaluate("document.querySelector('#verktygsrad').scrollIntoView()")
    sida.wait_for_timeout(500)
    sida.screenshot(
        path=str(UT / "kartverktyg-resultat.png"),
        clip={"x": 0, "y": 0, "width": 1440, "height": 1000},
    )
    sida.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="ta demo-*.png mot floden webb")
    ap.add_argument(
        "--kartverktyg", action="store_true", help="ta kartverktyg-*.png mot floden webb"
    )
    ap.add_argument("--3d", dest="vy3d", action="store_true", help="ta 3d-*.png mot floden webb")
    ap.add_argument(
        "--3d-farg", dest="vy3d_farg", action="store_true", help="prova att 3D-färger är exakta"
    )
    ap.add_argument("--url", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args()
    lokal = args.demo or args.kartverktyg or args.vy3d or args.vy3d_farg
    url = args.url or ("http://127.0.0.1:8765" if lokal else "http://127.0.0.1:5173")
    UT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # Kartan är MapLibre (WebGL): utan grafikkort används programvaruritning.
        webblasare = p.chromium.launch(
            executable_path=args.chrome,
            args=[
                "--no-sandbox",
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
            ],
        )
        if args.kartverktyg:
            _kartverktyg(webblasare, url)
        if args.vy3d_farg:
            _vy3d_farg(webblasare, url)
            webblasare.close()
            return
        if args.vy3d:
            _vy3d(webblasare, url)
            webblasare.close()
            return
        if args.demo:
            _demo(webblasare, url)
            webblasare.close()
            return
        sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})

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
