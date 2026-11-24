# ruff: noqa: E501
"""Tar skärmbilder av webbens flöde (docs/bilder/demo-*.png och 3d-*.png).

Förutsätter att API:t och klienten körs och att demokvarteret är byggt:

    floden dagsljus demo --fore-efter                 # bygger data/cache/exempel/dagsljus-kvarter
    PYTHONPATH=src python -m webb.api                 # API på :8765
    (cd webb && npm run dev)                          # klient på :5173
    python tools/webb_skarmbild.py                    # kräver playwright och Chrome/Chromium

Argument: --url (klientens adress), --chrome (sökväg till Chrome/Chromium).

Med `--3d` tas docs/bilder/3d-*.png (soltimmar före och efter på marken, VSC på fasaderna och
vattenyta, i 3D-läget i Resultat) mot `floden webb` efter `floden demo`.

Med `--3d-farg` läses pixlar av för punkter med kända färger (kräver `--3d`-vyn, inget sparas).

Med `--demo` tas i stället docs/bilder/demo-*.png (projektsidan och frågesidan) mot
`floden webb` (som serverar klienten själv, standard http://127.0.0.1:8765) efter `floden demo`.
Inget körs om: resultaten läses ur cachen.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

UT = Path(__file__).resolve().parents[1] / "docs" / "bilder"


def _sida(
    sida: Page, url: str, adress: str, vantar_pa: str, fil: str, *, helsida: bool = True
) -> None:
    sida.goto(f"{url}/{adress}")
    sida.wait_for_selector(vantar_pa, timeout=60_000)
    sida.wait_for_timeout(1500)
    sida.screenshot(path=str(UT / fil), full_page=helsida)


def _demo(webblasare: Browser, url: str) -> None:
    """demo-*.png i flödet (U3 tog bort projektsidan och demokorten på `#/fragor`): resultatet i 1440 och 800 px
    (demo-projekt), steg 3 (demo-fragor) och resultatet för vatten och dagsljus. Kartan har egna bilder
    i tools/flode_skarmbild.py (steg 2 och 4)."""
    for bredd, suffix in ((1440, ""), (800, "-smal")):
        sida = webblasare.new_page(viewport={"width": bredd, "height": 1000})
        _sida(sida, url, "#/p/demokvarteret/resultat", "#rs-a canvas", f"demo-projekt{suffix}.png", helsida=False)
        sida.close()

    sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
    _sida(sida, url, "#/p/demokvarteret/fragor", "#fr-lista input", "demo-fragor.png", helsida=False)
    sida.close()

    for vy, fil in (("vatten-nytt-hus", "demo-vatten.png"), ("dagsljus-hojd", "demo-dagsljus.png")):
        sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
        _sida(sida, url, f"#/p/demokvarteret/resultat?vy={vy}", "#rs-a canvas", fil, helsida=False)
        sida.close()


def _vy3d(webblasare: Browser, url: str) -> None:
    """3d-*.png: soltimmar före/efter på marken, VSC på fasaderna och vattenyta.

    Chrome körs med programvaru-WebGL (SwiftShader) här; bilderna är därför långsamma att
    rita men visar samma sak som en grafikdator.
    """

    def oppna(vy: str) -> Page:
        sida = webblasare.new_page(viewport={"width": 1440, "height": 1000})
        sida.goto(f"{url}/#/p/demokvarteret/resultat?vy={vy}")
        sida.wait_for_selector("#rs-a canvas", timeout=60_000)
        sida.wait_for_timeout(2000)
        sida.click('#rs-vaxel [data-lage="3d"]')
        sida.wait_for_selector("#rs-3d canvas", timeout=60_000)
        sida.wait_for_timeout(5000)
        return sida

    def ta(sida: Page, namn: str) -> None:
        sida.locator("#rs-3d").screenshot(path=str(UT / f"3d-{namn}.png"))

    def lage(sida: Page, namn: str, vanta: int = 3500) -> None:
        sida.click(f'#rs-3d button:text-is("{namn}")')
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
    sida.goto(f"{url}/#/p/demokvarteret/resultat?vy=dagsljus-hojd")
    sida.wait_for_selector("#rs-a canvas", timeout=60_000)
    sida.wait_for_timeout(1500)
    sida.click('#rs-vaxel [data-lage="3d"]')
    sida.wait_for_function("window.__floden3d !== undefined", timeout=60_000)
    sida.wait_for_timeout(5000)
    ut = sida.evaluate(_FARGPROV_JS)
    for ford, last in ut["res"]:
        diff = max(abs(a - b) for a, b in zip(ford, last, strict=True))
        print(f"skala {ford} -> pixel {last} (största avvikelse {diff})")
        assert diff <= 1, "färgen i 3D avviker från skalans färg"
    print("utdatafärgrum", ut["farg"], "tonmappning", ut["ton"])
    sida.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="ta demo-*.png mot floden webb")
    ap.add_argument("--3d", dest="vy3d", action="store_true", help="ta 3d-*.png mot floden webb")
    ap.add_argument(
        "--3d-farg", dest="vy3d_farg", action="store_true", help="prova att 3D-färger är exakta"
    )
    ap.add_argument("--url", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args()
    lokal = args.demo or args.vy3d or args.vy3d_farg
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
        webblasare.close()
        raise SystemExit(
            "Utan flagga fanns webb-plan, webb-resultat och webb-vatten från den äldre "
            "Utredningsfrågor-sidan, som togs bort i U3. Använd --demo, --3d eller "
            "tools/flode_skarmbild.py."
        )


if __name__ == "__main__":
    main()
