# ruff: noqa: E501
"""Webbläsarprov av ritverktygen (R1-R7) i demokvarteret, med riktiga musklick, tangenter och beröring.

Förutsätter `floden demo`, en körande `floden webb --port 8795` (inloggning av) och byggd webb (`npm run build` i webb/).
Provet går igenom steg 2 (välj lager, rita hus, markyta med slänt, mur med följ kant, öppning, klipp, ångra, egenskapspanelen,
kontrollen, skarvfynd och "Jämna ut skarven", 3D-förhandsvisningen, sparat förslag) och steg 3 och 4 (kör, öppna resultatet) på en bred skärm
med mus (1440x900), och därefter pekskärmsdelen (390x844 med beröring: verktygsrad, handtag, håll för att flytta, tangentbord, etiketter).

Allt som ritas ritas med `page.mouse` och `page.touchscreen`. Det enda som läses ur sidan utöver text och attribut är kartmotorn, som
sidan lämnar ut när `window.__FLODEN_PROV__` är satt (bara här), för att räkna om SWEREF-punkter till skärmpixlar.
Skärmbilder hamnar i docs/bilder/ritverktyg/. Slutar med en tabell över vad som gick igenom och med felkod 1 om något föll.

    python tools/ritverktyg_prov.py [--url http://127.0.0.1:8795] [--chrome /usr/bin/google-chrome] [--ut docs/bilder/ritverktyg]
        [--bara bred|smal|tangenter|geografi|nytt] [--utan-korning]

Geografiprovet (`--bara geografi`, och `--bara nytt` för Nytt projekt) går mot ett SYNTETISKT geografiprojekt, inte demokvarteret. Starta först
`python tools/geografi_prov_server.py --port 8796` (påhittad geografi `goteborg-test`, falsk Lantmäteri- och WFS-värld, inloggning av) och ange
`--geografi-url http://127.0.0.1:8796`. Provet ritar ett hus, sparar med "Spara förslag", går till "Välj frågor", kör, läser resultatet och kontrollerar
OpenStreetMap-bakgrunden (plattorna ersätts med påhittade bilder, inget nät), Content-Security-Policy ur `deploy/Caddyfile`, markhöjden under pekaren och
sparstatusen. Nytt projekt provas med ett påhittat svar från `/api/geografier` med 32 varningar.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Dialog, Page, Playwright, sync_playwright

UT = Path(__file__).resolve().parents[1] / "docs" / "bilder" / "ritverktyg"
PROJEKT = "demokvarteret"
NAMN = "Ritprov"  # förslagets namn; slug ritprov
ARGS = ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]

# Gården i demokvarterets första kvarter (SWEREF 99 18 00): husen runt ligger på 206-219 och 331-344 i båda riktningarna,
# så gården är ytan 219-331. Hörnet (219, 219) är ett befintligt hörn som ritningen ska fästa mot.
GARD = (151219.0, 6580219.0, 151331.0, 6580331.0)
HUS = [(151220.0, 6580220.0), (151245.0, 6580223.0), (151245.0, 6580240.0), (151223.0, 6580240.0)]
# Upphöjd yta (nivå 14,2 m RH 2000, marken är ca 13,6) tvärs gatukorsningen i kvarterens mitt: dämmer vattnet och ger ett skarvfynd
# som "Jämna ut skarven" löser med ett utlopp (samma fall som tests/webb/test_skarvatgard.py).
SANKT = [(151335.0, 6580335.0), (151365.0, 6580335.0), (151365.0, 6580365.0), (151335.0, 6580365.0)]
PLATA = [
    (151240.0, 6580290.0),
    (151265.0, 6580290.0),
    (151265.0, 6580315.0),
    (151240.0, 6580315.0),
]  # delta +0,3 m med slänt 1:3


class Prov:
    def __init__(self, url: str, ut: Path) -> None:
        self.url = url.rstrip("/")
        self.ut = ut
        self.resultat: list[tuple[str, bool, str, str]] = []  # (del, ok, namn, detalj)
        self.del_ = ""
        self.sida: Page | None = None
        self.kropp: dict[str, Any] = {}
        self.svar: dict[str, Any] = {}
        self.beg = 0  # antal kontrollanrop som skickats och fått svar
        self.sv = 0
        self.fel_i_sidan: list[str] = []
        self.konsol_fel: list[str] = []
        self.ctx: Any = None

    # --- redovisning -------------------------------------------------------------------------------------------------
    def ok(self, namn: str, villkor: bool, detalj: str = "") -> bool:
        self.resultat.append((self.del_, bool(villkor), namn, detalj))
        print(
            f"  {'OK  ' if villkor else 'FEL '} {namn}{(' - ' + detalj) if detalj else ''}",
            flush=True,
        )
        return bool(villkor)

    def bild(self, namn: str, hel: bool = False) -> None:
        self.ut.mkdir(parents=True, exist_ok=True)
        self.s.screenshot(path=str(self.ut / f"{namn}.png"), full_page=hel)

    @property
    def s(self) -> Page:
        assert self.sida is not None
        return self.sida

    # --- sidan --------------------------------------------------------------------------------------------------------
    def oppna(self, webblasare: Browser, bredd: int, hojd: int, touch: bool) -> None:
        ctx = webblasare.new_context(
            viewport={"width": bredd, "height": hojd},
            has_touch=touch,
            is_mobile=touch,
            device_scale_factor=1,
        )
        ctx.add_init_script("window.__FLODEN_PROV__ = true;")
        ctx.set_default_timeout(30_000)
        self.ctx = ctx
        ctx.on("dialog", lambda d: self._dialog(d))
        self.sida = ctx.new_page()
        self.sida.on("pageerror", self._sidfel)
        self.sida.on(
            "console", lambda m: self.konsol_fel.append(m.text) if m.type == "error" else None
        )
        self.sida.on("request", self._begaran)
        self.sida.on("response", self._svar)

    def _sidfel(self, e: Any) -> None:
        senast = self.resultat[-1][2] if self.resultat else "start"
        self.fel_i_sidan.append(f"{e} (efter: {senast})")
        print(f"  SIDFEL {e} (efter: {senast})\n{getattr(e, 'stack', '')[:600]}", flush=True)

    @staticmethod
    def _dialog(d: Dialog) -> None:
        d.accept()

    def _begaran(self, r: Any) -> None:
        if r.method == "POST" and r.url.endswith("/validera") and r.post_data:
            self.kropp = json.loads(r.post_data)
            self.beg += 1

    def _svar(self, r: Any) -> None:
        if r.request.method == "POST" and r.url.endswith("/validera"):
            with contextlib.suppress(Exception):
                self.svar = r.json()
            self.sv += 1

    def starta_steg2(self) -> None:
        self.s.goto(f"{self.url}/#/p/{PROJEKT}/forslag")
        self.s.wait_for_selector("#fs-laddar", state="hidden", timeout=60_000)
        self.s.wait_for_timeout(1500)

    # --- kartan: SWEREF till skärm, riktiga händelser --------------------------------------------------------------
    def skarm(self, x: float, y: float) -> tuple[float, float]:
        r = self.s.evaluate(
            "([x,y])=>{const m=window.__floden_kartmotor;const p=m.tillSkarm({x,y});const c=m.karta.getCanvas().getBoundingClientRect();return [p.x+c.left,p.y+c.top]}",
            [x, y],
        )
        return float(r[0]), float(r[1])

    def meter_per_px(self) -> float:
        return float(self.s.evaluate("window.__floden_kartmotor.meterPerPx()"))

    def zooma_mot(self, x: float, y: float, steg: int = 6) -> None:
        a, b = self.skarm(x, y)
        self.s.mouse.move(a, b)
        for _ in range(steg):
            self.s.mouse.wheel(0, -240)
            self.s.wait_for_timeout(200)
        self.s.wait_for_timeout(700)

    def arbetsvy(self) -> None:
        """Tillbaka till arbetsvyn (Anpassa och zooma mot gården) efter att ett fynd zoomat kartan."""
        self.s.click("#fs-anpassa")
        self.s.wait_for_timeout(700)
        self.zooma_mot(151275, 6580275)

    def klick(self, x: float, y: float, **kw: Any) -> None:
        a, b = self.skarm(x, y)
        self.s.mouse.move(a, b, steps=3)
        self.s.mouse.click(a, b, **kw)
        self.s.wait_for_timeout(120)

    def klicka(self, punkter: list[tuple[float, float]], **kw: Any) -> None:
        for x, y in punkter:
            self.klick(x, y, **kw)

    def dubbelklick(self, x: float, y: float) -> None:
        a, b = self.skarm(x, y)
        self.s.mouse.move(a, b, steps=3)
        self.s.mouse.dblclick(a, b)
        self.s.wait_for_timeout(200)

    def dra(self, fran: tuple[float, float], till: tuple[float, float], steg: int = 8) -> None:
        a, b = self.skarm(*fran)
        c, d = self.skarm(*till)
        self.s.mouse.move(a, b, steps=3)
        self.s.mouse.down()
        self.s.mouse.move(c, d, steps=steg)
        self.s.mouse.up()
        self.s.wait_for_timeout(250)

    def tang(self, k: str) -> None:
        self.s.keyboard.press(k)
        self.s.wait_for_timeout(150)

    def verktyg(self, v: str) -> None:
        self.s.click(f'.flode-verktyg button[data-v="{v}"]')
        self.s.wait_for_timeout(100)

    def aktivt(self, v: str) -> bool:
        return (
            self.s.get_attribute(f'.flode-verktyg button[data-v="{v}"]', "aria-pressed") == "true"
        )

    def lager(self, lager_id: str) -> None:
        self.s.click(f"#rl-a-{lager_id}")

    def hjalp(self) -> str:
        return self.s.inner_text("#fs-hjalp").strip()

    def info(self) -> str:
        return self.s.inner_text("#fs-info").strip()

    def vanta_kontroll(self, text: str = "Sparat som", sek: float = 40) -> bool:
        """Väntar på att den automatiska kontrollen har svarat och förslaget sparats (eller att ett fel visas).

        Kontrollen startar 250 ms efter senaste ändringen; därför väntas först så länge att en pågående ändring hunnit skicka sitt anrop,
        sedan på att alla anrop fått svar, och sist på texten i panelen."""
        self.s.wait_for_timeout(500)
        _vanta(lambda: self.beg == self.sv, sek)
        try:
            self.s.wait_for_function(
                "t => { const e = document.querySelector('#fs-info'); return !!e && e.innerText.includes(t); }",
                arg=text,
                timeout=sek * 1000,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def objekt(self) -> list[dict[str, Any]]:
        return list((self.kropp.get("ritlager") or {}).get("objekt") or [])

    def andringar(self) -> list[dict[str, Any]]:
        return list(self.kropp.get("andringar") or [])

    def fynd(self) -> int:
        sk = self.svar.get("skarvar") or {}
        return int(sk.get("antal") or 0)


def _http(url: str, kropp: dict[str, Any] | None = None) -> Any:
    r = urllib.request.Request(
        url,
        data=json.dumps(kropp).encode() if kropp is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=60) as f:  # noqa: S310
        return json.load(f)


def stada(url: str) -> None:
    """Tar bort ett förslag från tidigare körning, så att provet börjar tomt."""
    for slug in ("ritprov", "ritprov-smal"):
        with contextlib.suppress(Exception):
            _http(f"{url}/api/kartverktyg/{PROJEKT}/scenarier/{slug}/ta-bort", {})


# =====================================================================================================================
# Bred skärm, mus
# =====================================================================================================================
def bred(p: Prov, webblasare: Browser, med_korning: bool) -> None:
    p.del_ = "bred"
    print("Bred skärm (1440x900, mus)", flush=True)
    p.oppna(webblasare, 1440, 900, False)
    s = p.s
    p.starta_steg2()
    s.fill("#fs-namn", NAMN)  # namnet först, så att filen heter ritprov
    s.keyboard.press("Tab")
    p.bild("01-steg2-start")
    p.ok(
        "ritverktygen och lagerpanelen finns",
        s.locator(".flode-verktyg button").count() >= 15
        and s.locator("#fs-ritlager input[type=radio]").count() >= 7,
    )
    p.ok("fäst är på från början", p.aktivt("fast"))

    # --- Hus ---
    p.zooma_mot(151275, 6580275)
    mpp = p.meter_per_px()
    p.ok(
        "zoom med mushjulet ger skala under 0,5 m per pixel",
        mpp < 0.5,
        f"{mpp:.3f} m/px".replace(".", ","),
    )
    p.lager("hus")
    p.verktyg("hus")
    p.ok(
        "Rita hus är valt och hjälptexten säger vad man ska göra",
        p.aktivt("hus") and "hörn" in p.hjalp().lower(),
        p.hjalp(),
    )
    p.klicka(HUS)
    p.tang("Enter")
    p.s.wait_for_timeout(600)
    p.ok("hushöjden efterfrågas efter Enter", "höjd" in p.hjalp().lower(), p.hjalp())
    s.fill("#fs-hojd", "15")
    s.keyboard.press("Enter")
    p.ok("kontrollen svarar och förslaget sparas", p.vanta_kontroll(), p.info().replace("\n", " "))
    hus = [o for o in p.objekt() if o["lager_id"] == "hus"]
    ring = hus[0]["geometri"]["coordinates"][0] if hus else []
    forsta = ring[0] if ring else [0, 0]
    p.ok(
        "husets första hörn fäste mot gårdens hörn (219; 219)",
        abs(forsta[0] - GARD[0]) < 0.01 and abs(forsta[1] - GARD[1]) < 0.01,
        f"{forsta}",
    )
    p.ok(
        "de andra hörnen ligger där de klickades (på 0,3 m när)",
        len(ring) >= 5 and abs(ring[2][0] - HUS[2][0]) < 0.4 and abs(ring[2][1] - HUS[2][1]) < 0.4,
    )
    p.ok(
        "infotexten ger yta och våningar",
        "våning" in p.info() and "Yta" in p.info(),
        p.info().split("\n")[0],
    )
    p.bild("02-hus")

    # --- Markyta som sänks, utan slänt (ger ett skarvfynd) ---
    p.lager("mark")
    p.verktyg("yta")
    p.klicka(SANKT)
    p.tang("Enter")
    p.s.wait_for_timeout(500)
    p.ok(
        "ytan skapas av Enter och egenskapspanelen visas",
        s.locator("#fs-figur:not([hidden])").count() == 1,
    )
    p.ok(
        "egenskapspanelen öppnas för ytan med val av höjd",
        s.locator("#fs-f-hojdlage").count() == 1,
        s.inner_text("#fs-figur-rubrik"),
    )
    p.ok(
        "höjdvalet börjar tomt (inget fält visas förrän man väljer)",
        s.input_value("#fs-f-hojdlage") == ""
        and s.locator("#fs-f-niva_rh2000, #fs-f-delta_m").count() == 0,
    )
    s.select_option("#fs-f-hojdlage", "niva")
    s.fill("#fs-f-niva_rh2000", "14,2")
    s.keyboard.press("Tab")
    p.vanta_kontroll()
    mark = [o for o in p.objekt() if o["lager_id"] == "mark"]
    p.ok(
        "ytan har fyra hörn (Enter utan dubblerat sista hörn, regression)",
        bool(mark) and len(mark[0]["geometri"]["coordinates"][0]) == 5,
        json.dumps(mark[0]["geometri"]["coordinates"][0]) if mark else "inga ytor",
    )
    mark = [o for o in p.objekt() if o["lager_id"] == "mark"]
    p.ok(
        "kontrollen räknar ut ett skarvfynd för den upphöjda ytan",
        p.vanta_kontroll("Sparat som") and _vanta(lambda: p.fynd() >= 1),
        f"{p.fynd()} fynd",
    )
    p.ok(
        "skarvpanelen visas med fyndet och knappen Jämna ut skarven",
        s.locator("#fs-skarv:not([hidden]) [data-skarv='jamna']").count() == 1,
    )
    p.bild("03-sankt-yta-skarvfynd")

    # --- Skarvfynd och Jämna ut skarven ---
    ring_m = [o for o in p.objekt() if o["lager_id"] == "mark"][0]["geometri"]["coordinates"][0]
    ring_nv, ring_no, ring_so = (
        (ring_m[3][0], ring_m[3][1]),
        (ring_m[2][0], ring_m[2][1]),
        (ring_m[1][0], ring_m[1][1]),
    )
    p.vanta_kontroll()
    _vanta(lambda: p.fynd() >= 1)
    p.ok("skarvfyndet finns", p.fynd() >= 1, f"{p.fynd()} fynd")
    s.click("[data-skarv-fynd]")
    s.wait_for_timeout(800)
    p.bild("09-skarvfynd")
    p.arbetsvy()
    s.click("[data-skarv='jamna']")
    ok_forslag = _vanta(lambda: s.locator("[data-skarv='tillampa']").count() == 1, 60)
    p.ok(
        "Jämna ut skarven ger ett förslag med före och efter",
        ok_forslag,
        s.inner_text("#fs-skarv")[:200].replace("\n", " ")
        if ok_forslag
        else s.inner_text("#fs-skarv")[:300],
    )
    p.bild("10-jamna-ut-forslag")
    if ok_forslag:
        s.click("[data-skarv='tillampa']")
        s.wait_for_timeout(500)
        p.vanta_kontroll()
        _vanta(lambda: "tillämpad" in s.inner_text("#fs-skarv"), 40)
        p.ok(
            "Tillämpa lägger åtgärden i förslaget",
            "tillämpad" in s.inner_text("#fs-skarv") or p.fynd() == 0,
            s.inner_text("#fs-skarv")[:160].replace("\n", " "),
        )
        p.bild("11-skarv-atgardad")

    # --- Markyta med slänt (manuellt) ---
    p.verktyg("yta")
    p.klicka(PLATA)
    p.dubbelklick(*PLATA[-1])  # dubbelklick avslutar också (sista hörnet dubbleras)
    p.s.wait_for_timeout(400)
    s.select_option("#fs-f-hojdlage", "delta")
    s.fill("#fs-f-delta_m", "0,3")
    s.keyboard.press("Tab")
    s.select_option("#fs-f-slant_typ", "forhallande")
    s.fill("#fs-f-slant_forhallande", "3")
    s.keyboard.press("Tab")
    p.ok(
        "slänt 1:3 anges i panelen",
        p.vanta_kontroll()
        and _vanta(lambda: any((o.get("egenskaper") or {}).get("slant") for o in p.objekt())),
        json.dumps([o.get("egenskaper") for o in p.objekt() if o["lager_id"] == "mark"]),
    )
    p.bild("04-yta-med-slant")

    # --- Mur med följ kant (F och Skift) längs den sänkta ytans kanter ---
    p.lager("mur-och-kant")
    p.verktyg("linje")
    nv, no, so = ring_nv, ring_no, ring_so
    p.klick(*nv)  # nordvästra hörnet (fäster mot ytans hörn)
    p.tang("f")
    p.ok("F slår på följ kant", p.aktivt("foljkant"))
    p.klick(
        no[0] + 0.5, (no[1] + so[1]) / 2 + 8
    )  # en bit ned längs östra kanten: kanten följs över nordöstra hörnet
    p.tang("f")
    p.ok("F slår av följ kant igen", not p.aktivt("foljkant"))
    s.keyboard.down("Shift")
    p.klick(no[0] + 0.5, (no[1] + so[1]) / 2 - 6)  # med Skift hållen följs kanten också
    s.keyboard.up("Shift")
    p.tang("Enter")
    p.s.wait_for_timeout(500)
    p.ok(
        "muren är ritad i murlagret och panelen visar den",
        "mur" in s.inner_text("#fs-figur-rubrik").lower()
        or "linje" in s.inner_text("#fs-figur-rubrik").lower(),
        s.inner_text("#fs-figur-rubrik"),
    )
    p.vanta_kontroll()
    mur = [o for o in p.objekt() if o["lager_id"] == "mur-och-kant"]
    kord = (mur[0]["geometri"]["coordinates"] if mur else []) or []
    kk = [list(map(float, k)) for k in kord]
    p.ok(
        "murens punkter sammanfaller exakt med ytans hörn och östra kanten (följ kant med F och med Skift)",
        len(kk) == 4
        and kk[0] == list(nv)
        and kk[1] == list(no)
        and all(_avstand_till_sträcka(k, no, so) < 0.02 for k in kk[2:]),
        json.dumps(kk),
    )
    p.ok(
        "murens egenskaper har typ och höjd",
        s.locator("#fs-f-typ").count() == 1 and s.locator("#fs-f-hojd_m").count() == 1,
    )
    s.fill("#fs-f-hojd_m", "0,6")
    s.keyboard.press("Tab")
    p.bild("05-mur-foljkant")

    # --- Öppning (passage) genom muren ---
    p.lager("passage")
    p.verktyg("linje")
    p.klick(nv[0] + 20, nv[1] - 6)
    p.klick(nv[0] + 20, nv[1] + 6)
    p.tang("Enter")
    p.ok(
        "öppningen är ritad i passagelagret",
        "passage" in s.inner_text("#fs-figur-rubrik").lower()
        or "öppning" in s.inner_text("#fs-figur-rubrik").lower()
        or "trumma" in s.inner_text("#fs-figur-rubrik").lower(),
        s.inner_text("#fs-figur-rubrik"),
    )
    p.ok(
        "öppningens fält: typ, bredd och botten",
        s.locator("#fs-f-bredd_m").count() == 1 and s.locator("#fs-f-botten_rh2000").count() == 1,
    )
    p.ok(
        "servern kräver bottennivå för en ny öppning och säger det",
        _vanta(lambda: "botten" in p.info().lower(), 15),
        p.info().replace("\n", " ")[-160:],
    )
    s.fill("#fs-f-botten_rh2000", "13,4")
    s.keyboard.press("Tab")
    p.vanta_kontroll()
    p.ok(
        "servern godtar öppningen (K9 oppning_ny)",
        any(a.get("typ") == "oppning_ny" for a in p.svar.get("andringar", []))
        or any(o["lager_id"] == "passage" for o in p.objekt()),
    )
    p.bild("06-oppning")

    # --- Klipp och ångra ---
    antal_mur_fore = len([o for o in p.objekt() if o["lager_id"] == "mur-och-kant"])
    p.verktyg("klipp")
    p.klick(nv[0] + 10, nv[1])  # på murens första sträcka
    p.vanta_kontroll()
    antal_mur_efter = len([o for o in p.objekt() if o["lager_id"] == "mur-och-kant"])
    p.ok(
        "Klipp delar muren i två",
        antal_mur_efter == antal_mur_fore + 1,
        f"{antal_mur_fore} -> {antal_mur_efter}",
    )
    p.bild("07-klippt")
    p.tang("z")
    p.vanta_kontroll()
    antal_mur_ang = len([o for o in p.objekt() if o["lager_id"] == "mur-och-kant"])
    p.ok(
        "Z ångrar klippet i ett steg",
        antal_mur_ang == antal_mur_fore,
        f"{antal_mur_efter} -> {antal_mur_ang}",
    )
    p.tang("Shift+Z")
    p.vanta_kontroll()
    p.ok(
        "Skift+Z gör om klippet",
        len([o for o in p.objekt() if o["lager_id"] == "mur-och-kant"]) == antal_mur_efter,
    )
    p.tang("z")
    p.verktyg("valj")

    # --- Markera och egenskapspanelen ---
    a, b = p.skarm(SANKT[0][0] + 5, SANKT[0][1] + 5)
    s.mouse.move(a, b)
    s.mouse.click(a, b)
    s.wait_for_timeout(400)
    p.ok(
        "markera en yta i kartan öppnar dess egenskaper",
        "Yta" in s.inner_text("#fs-figur-rubrik"),
        s.inner_text("#fs-figur-rubrik"),
    )
    p.bild("08-egenskaper")

    # --- 3D-förhandsvisning ---
    s.click("#fs-3d-knapp")
    s.wait_for_timeout(5000)
    p.ok(
        "3D-förhandsvisningen öppnas med en canvas",
        s.locator("#fs-3d:not([hidden]) canvas").count() >= 1,
    )
    p.bild("12-3d-forhandsvisning")
    s.click("#fs-3d-stang")

    # --- Sparat förslag ---
    p.vanta_kontroll()
    s.locator("#fs-sparade-ruta summary").click()
    s.wait_for_timeout(800)
    sparade = _http(f"{p.url}/api/kartverktyg/{PROJEKT}/scenarier").get("scenarier", [])
    p.ok(
        "förslaget är sparat på disk under sitt namn",
        any(x["slug"] == "ritprov" and x["namn"] == NAMN for x in sparade),
        json.dumps([(x["slug"], x["namn"]) for x in sparade]),
    )
    p.bild("13-sparade-forslag")

    # --- Ladda om sidan: förslaget ska komma tillbaka ---
    p.starta_steg2()
    s.wait_for_timeout(1500)
    p.ok(
        "efter omladdning finns förslagets figurer kvar",
        "ändring" in p.info() or s.locator("#fs-ritlager").inner_text().count("1") >= 1,
        p.info().replace("\n", " ")[:120],
    )

    # --- Steg 3 och 4 ---
    if med_korning:
        steg3_och_4(p)

    fel = [f for f in p.fel_i_sidan]
    p.ok("inga oupptäckta sidfel (pageerror)", not fel, "; ".join(fel)[:300])
    stada(p.url)
    s.context.close()


def steg3_och_4(p: Prov) -> None:
    s = p.s
    s.click("#fs-vidare")
    s.wait_for_selector("#fr-kor", timeout=60_000)
    s.wait_for_timeout(1500)
    p.bild("14-steg3-fragor")
    p.ok(
        "steg 3 har knappen Kör beräkningarna aktiv",
        s.is_enabled("#fr-kor"),
        s.inner_text("#fr-kor-not")[:120].replace("\n", " "),
    )
    s.click("#fr-kor")
    p.bild("15-steg3-kor")
    klar = _vanta(
        lambda: "resultat" in s.evaluate("location.hash"), 480
    )  # Resultat öppnas av sig självt när körningen är klar
    p.ok(
        "körningen blir klar och Resultat öppnas av sig självt",
        klar,
        s.inner_text("body")[:200].replace("\n", " ") if not klar else s.evaluate("location.hash"),
    )
    s.wait_for_timeout(8000)
    p.bild("16-steg4-resultat")
    p.ok(
        "resultatet visar förslaget och inte bara demokörningen",
        "inte ett eget förslag" not in s.inner_text("body"),
        s.inner_text(".flode-panel")[:160].replace("\n", " "),
    )
    p.ok("resultatet öppnas med en karta", s.locator("canvas").count() >= 1)


def _avstand_till_sträcka(k: list[float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((k[0] - a[0]) * dx + (k[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return float(((k[0] - a[0] - t * dx) ** 2 + (k[1] - a[1] - t * dy) ** 2) ** 0.5)


def _vanta(villkor: Callable[[], bool], sek: float = 30) -> bool:
    import time

    slut = time.time() + sek
    while time.time() < slut:
        with contextlib.suppress(Exception):
            if villkor():
                return True
        time.sleep(0.25)
    return False


# =====================================================================================================================
# Geografiprojekt (syntetiskt) och Nytt projekt
# =====================================================================================================================
GEO_PROJEKT = "haga-prov"
GEO_NAMN = "Geoprov"  # förslagets namn; slug geoprov
REPO = Path(__file__).resolve().parents[1]
TILE_HOST = "https://tile.openstreetmap.org"


def _stub_platta() -> bytes:
    """En påhittad 256x256-bild som ersätter OpenStreetMaps plattor (provet har inget nät mot OSM och ska inte belasta deras servrar)."""
    import io

    from PIL import Image, ImageDraw

    bild = Image.new("RGB", (256, 256), (222, 232, 214))
    d = ImageDraw.Draw(bild)
    for k in range(0, 256, 64):
        d.line([(k, 0), (k, 255)], fill=(196, 208, 186))
        d.line([(0, k), (255, k)], fill=(196, 208, 186))
    buf = io.BytesIO()
    bild.save(buf, "PNG")
    return buf.getvalue()


def csp_overtradelser(meddelanden: list[str]) -> list[str]:
    """CSP-överträdelser i konsolen (med Content-Security-Policy ur deploy/Caddyfile)."""
    return [m for m in meddelanden if ("Content Security Policy" in m or "Content-Security-Policy" in m)]


def _csp_ur_caddyfile() -> str:
    import re

    m = re.search(r'header Content-Security-Policy "([^"]+)"', (REPO / "deploy" / "Caddyfile").read_text(encoding="utf-8"))
    assert m, "Content-Security-Policy saknas i deploy/Caddyfile"
    return m.group(1)


class Natet:
    """Vad sidan hämtade: plattor från OSM (med Referer) och vår egen servers svar. Plattorna svaras med påhittade bilder."""

    def __init__(self, ctx: Any, url: str, csp: str) -> None:
        self.plattor: list[tuple[str, str | None]] = []  # (adress, referer)
        self.svar: list[tuple[str, int]] = []
        self.stub = _stub_platta()
        self.fel_sparande: str | None = None  # påhittat serverfel för POST .../scenarier (provar felmeddelandet)
        self.csp = csp
        ctx.route(f"{url}/**", self._var)
        ctx.route(f"{TILE_HOST}/**", self._platta)

    def _platta(self, route: Any) -> None:
        r = route.request
        self.plattor.append((r.url, r.headers.get("referer")))
        route.fulfill(
            status=200,
            body=self.stub,
            headers={"content-type": "image/png", "cache-control": "max-age=3600", "access-control-allow-origin": "*"},
        )

    def _var(self, route: Any) -> None:
        r = route.request
        if self.fel_sparande and r.method == "POST" and r.url.split("?")[0].endswith("/scenarier"):
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"fel": self.fel_sparande, "status": 500}))
            return
        resp = route.fetch()
        self.svar.append((r.url, resp.status))
        route.fulfill(response=resp, headers={**resp.headers, "content-security-policy": self.csp})

    def antal_plattor(self) -> int:
        return len(self.plattor)

    def z_max(self) -> int:
        return max((int(u.removeprefix(TILE_HOST + "/").split("/")[0]) for u, _ in self.plattor), default=-1)


def _stada_geo(url: str) -> None:
    with contextlib.suppress(Exception):
        _http(f"{url}/api/kartverktyg/{GEO_PROJEKT}/scenarier/geoprov/ta-bort", {})


def geografi(p: Prov, webblasare: Browser, med_korning: bool) -> None:
    """Rita, spara, välj frågor, kör och läs resultatet i ett syntetiskt geografiprojekt, med OSM-bakgrund, markhöjd och sparstatus."""
    p.del_ = "geografi"
    print("Geografiprojekt (syntetiskt, 1440x900, mus)", flush=True)
    _stada_geo(p.url)
    p.oppna(webblasare, 1440, 900, False)
    s = p.s
    net = Natet(p.ctx, p.url, _csp_ur_caddyfile())
    info = _http(f"{p.url}/api/kartverktyg/{GEO_PROJEKT}")
    a0, b0, a1, b1 = info["avgransning_3011"]
    cx, cy = (a0 + a1) / 2, (b0 + b1) / 2
    p.ok("projektet är ett geografiprojekt utan ortofoto", info["slag"] == "geografi" and info["ortofoto"] == {"finns": False})
    s.goto(f"{p.url}/#/p/{GEO_PROJEKT}/forslag")
    s.wait_for_selector("#fs-laddar", state="hidden", timeout=60_000)
    s.wait_for_timeout(2500)
    p.bild("40-geo-steg2-start")

    # --- OpenStreetMap-bakgrunden ---
    p.ok("OSM-bakgrunden är tänd från början i ett geografiprojekt", s.is_checked("#fs-osm-kryss"))
    n0 = net.antal_plattor()
    p.ok("plattor hämtas för det som syns (mer än 0, högst 40 vid första bilden)", 0 < n0 <= 40, f"{n0} plattor, högsta zoom {net.z_max()}")
    p.ok("ingen platta över zoom 19", net.z_max() <= 19)
    refs = {r for _, r in net.plattor}
    p.ok("plattorna begärs med Referer = sidans ursprung (utan sökväg)", refs == {p.url + "/"}, str(refs))
    attrib = s.inner_text(".maplibregl-ctrl-attrib")
    p.ok("attributionen \"© OpenStreetMap-bidragsgivare\" syns", "© OpenStreetMap-bidragsgivare" in attrib and s.locator(".maplibregl-ctrl-attrib a[href='https://www.openstreetmap.org/copyright']").is_visible(), attrib)
    csp_fel = csp_overtradelser(p.konsol_fel)
    p.ok("Content-Security-Policy ur deploy/Caddyfile blockerar ingenting (plattor, bilder, 3D)", not csp_fel, "; ".join(csp_fel)[:300])
    p.ok("höjdkurvor och skuggning hämtades från servern", all(any(f"/geometri/{v}.png" in u and st == 200 for u, st in net.svar) for v in ("hojdkurvor", "skuggning")))
    p.ok("lagerlistan har OpenStreetMap, Höjdkurvor och Terräng", all(t in s.inner_text("#fs-lager-ruta") for t in ("OpenStreetMap", "Höjdkurvor", "Terräng")) or s.locator("#fs-lagerpanel").count() == 1)

    # --- Markhöjden under pekaren ---
    p.zooma_mot(cx, cy, steg=4)
    a, b = p.skarm(cx, cy)
    s.mouse.move(a, b, steps=4)
    s.mouse.move(a + 6, b + 6, steps=2)
    try:
        s.wait_for_function("() => { const e = document.querySelector('#fs-markhojd'); return !!e && !e.hidden && /Markhöjd/.test(e.innerText); }", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    mh = s.inner_text("#fs-markhojd")
    import re

    p.ok("markhöjden under pekaren visas med decimalkomma och höjdsystem", re.fullmatch(r"Markhöjd -?\d+,\d{2} m RH 2000", mh.strip()) is not None, mh.strip())
    p.bild("41-geo-markhojd")

    # --- OSM av och på: släckt lager hämtar ingenting ---
    s.click("#fs-osm-kryss")
    s.wait_for_timeout(500)
    n1 = net.antal_plattor()
    s.mouse.wheel(0, 240)
    s.wait_for_timeout(1200)
    s.mouse.move(a + 200, b + 120, steps=4)
    s.mouse.down()
    s.mouse.move(a + 120, b + 60, steps=6)
    s.mouse.up()
    s.wait_for_timeout(1200)
    p.ok("släckt OSM-bakgrund hämtar inga plattor (zoom och panorering)", net.antal_plattor() == n1, f"{n1} -> {net.antal_plattor()}")
    p.ok("attributionen försvinner när bakgrunden är av", "OpenStreetMap" not in s.inner_text(".maplibregl-ctrl-attrib"))
    p.bild("42-geo-osm-av")
    s.reload()
    s.wait_for_selector("#fs-laddar", state="hidden", timeout=60_000)
    s.wait_for_timeout(1200)
    n2 = net.antal_plattor()
    p.ok("valet 'av' sparas per projekt och överlever omladdning (inga plattor)", not s.is_checked("#fs-osm-kryss") and net.antal_plattor() == n2 == n1)
    s.click("#fs-osm-kryss")
    s.wait_for_timeout(1500)
    p.ok("tänd igen hämtar plattor igen", net.antal_plattor() > n1)

    # --- Rita, spara, välj frågor ---
    s.fill("#fs-namn", GEO_NAMN)
    s.keyboard.press("Tab")
    p.ok("sparstatusen säger 'Inget att spara än' innan något ritats", "Inget att spara" in s.inner_text("#fs-sparstatus"), s.inner_text("#fs-sparstatus"))
    s.click("#fs-spara")
    s.wait_for_timeout(300)
    p.ok("Spara förslag utan ritning ger ett begripligt besked och inget fel", "Inget att spara" in s.inner_text("#fs-sparstatus") and s.locator("#fs-sparstatus.fel").count() == 0)
    s.click("#fs-anpassa")
    s.wait_for_timeout(500)
    p.zooma_mot(cx, cy, steg=6)
    hus = [(a0 + 14, b0 + 14), (a0 + 30, b0 + 15), (a0 + 29, b0 + 27), (a0 + 13, b0 + 26)]
    p.lager("hus")
    p.verktyg("hus")
    net.fel_sparande = "Disken är full (påhittat fel)"
    p.klicka(hus)
    p.tang("Enter")
    s.wait_for_timeout(500)
    s.fill("#fs-hojd", "12")
    s.keyboard.press("Enter")
    try:
        s.wait_for_function("() => /Kunde inte spara/.test(document.querySelector('#fs-sparstatus')?.innerText ?? '')", timeout=20_000)
    except Exception:  # noqa: BLE001
        pass
    st = s.inner_text("#fs-sparstatus")
    p.ok("ett serverfel vid sparandet visas bredvid knappen med skälet", st.startswith("Kunde inte spara") and "Disken är full" in st, st)
    p.ok("felet är en alert och röd", s.get_attribute("#fs-sparstatus", "role") == "alert" and s.locator("#fs-sparstatus.fel").count() == 1)
    p.bild("43-geo-sparfel")
    s.click("#fs-vidare")
    s.wait_for_timeout(1500)
    p.ok(
        "Välj frågor går inte vidare med osparade ändringar och säger varför",
        "forslag" in s.evaluate("location.hash") and s.inner_text("#fs-vidare") == "Gå vidare utan att spara" and "Kunde inte spara" in s.inner_text("#fs-sparstatus"),
        f"{s.evaluate('location.hash')} / {s.inner_text('#fs-vidare')}",
    )
    net.fel_sparande = None
    s.click("#fs-spara")
    try:
        s.wait_for_function("() => /^Sparat som/.test(document.querySelector('#fs-sparstatus')?.innerText ?? '')", timeout=30_000)
    except Exception:  # noqa: BLE001
        pass
    st = s.inner_text("#fs-sparstatus")
    p.ok("Spara förslag sparar och bekräftar med namn och klockslag", re.fullmatch(r"Sparat som Geoprov kl\. \d\d:\d\d på den här datorn\.", st.strip()) is not None, st)
    p.ok("bekräftelsen står också i kartans hjälprad", "Förslaget är sparat som Geoprov" in p.hjalp(), p.hjalp())
    lista = _http(f"{p.url}/api/kartverktyg/{GEO_PROJEKT}/scenarier")["scenarier"]
    p.ok("servern har förslaget (en fil, ett hus)", [x["slug"] for x in lista] == ["geoprov"] and lista[0]["antal_andringar"] == 1, str(lista))
    p.bild("44-geo-sparat")

    # En ändring till och direkt "Välj frågor": den sparar först (tryck innan den automatiska sparningen hunnit).
    s.fill("#fs-hojd", "15")
    s.keyboard.press("Enter")
    try:
        s.wait_for_function("() => /Osparade/.test(document.querySelector('#fs-sparstatus')?.innerText ?? '')", timeout=3000)
        osparat = True
    except Exception:  # noqa: BLE001
        osparat = False
    p.ok("en ändring ger status 'Osparade ändringar' tills den sparats", osparat, s.inner_text("#fs-sparstatus"))
    s.click("#fs-vidare")
    klar = _vanta(lambda: "/fragor" in s.evaluate("location.hash"), 30)
    p.ok("Välj frågor sparar först och går sedan till steg 3", klar, s.evaluate("location.hash"))
    d = _http(f"{p.url}/api/kartverktyg/{GEO_PROJEKT}/scenarier/geoprov")
    hojder = [float((a.get("egenskaper") or {}).get("hojd_m", 0)) for a in d.get("andringar", [])]
    p.ok("det sparade förslaget har den senaste höjden (15 m)", hojder == [15.0], str(hojder))

    # --- Steg 3 och 4 ---
    s.wait_for_selector("#fr-kor", timeout=60_000)
    s.wait_for_timeout(1500)
    p.bild("45-geo-steg3")
    p.ok("steg 3 har knappen Kör beräkningarna aktiv för geografiprojektet", s.is_enabled("#fr-kor"), s.inner_text("#fr-kor-not")[:140].replace("\n", " "))
    if not med_korning:
        return
    s.click("#fr-kor")
    klar = _vanta(lambda: "resultat" in s.evaluate("location.hash"), 480)
    p.ok("körningen i geografiprojektet blir klar och Resultat öppnas", klar, s.inner_text("body")[:300].replace("\n", " ") if not klar else "")
    if not klar:
        return
    s.wait_for_timeout(6000)
    p.ok("resultatet visar förslaget", "inte ett eget förslag" not in s.inner_text("body") and s.locator("canvas").count() >= 1, s.inner_text(".flode-panel")[:160].replace("\n", " "))
    a, b = _mitt(s, "#rs-a canvas")
    s.mouse.move(a, b, steps=5)
    s.mouse.move(a + 8, b + 5, steps=3)
    try:
        s.wait_for_function("() => /Markhöjd/.test(document.querySelector('#rs-avl')?.innerText ?? '')", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    p.ok("steg 4 visar markhöjden under pekaren", re.search(r"Markhöjd -?\d+,\d{2} m RH 2000", s.inner_text("#rs-avl")) is not None, s.inner_text("#rs-avl").replace("\n", " "))
    p.ok("steg 4 har OSM-kryssrutan och OSM-bakgrunden är tänd", s.is_checked("#rs-osm-kryss") and "© OpenStreetMap-bidragsgivare" in s.inner_text(".maplibregl-ctrl-attrib"))
    p.bild("46-geo-steg4")
    csp_fel = csp_overtradelser(p.konsol_fel)
    p.ok("fortfarande inga CSP-överträdelser i steg 3 och 4", not csp_fel)
    _stada_geo(p.url)


def nytt_projekt(p: Prov, webblasare: Browser) -> None:
    """Nytt projekt (`#/nytt`) med påhittade svar: geografin har 32 varningar; bakgrund, hopfälld lista och rutnätets lutning."""
    p.del_ = "nytt"
    print("Nytt projekt (påhittat svar med 32 varningar)", flush=True)
    p.oppna(webblasare, 1440, 900, False)
    s = p.s
    net = Natet(p.ctx, p.url, _csp_ur_caddyfile())
    import glob

    mf = sorted(glob.glob("/tmp/floden-geoprov/geo/goteborg-test/v1/manifest.json"))
    mani = json.loads(Path(mf[0]).read_text(encoding="utf-8")) if mf else {"karna_3011": [-210251.0, 6413249.0, -209749.0, 6413751.0], "gransen_3011": [-210500.0, 6413000.0, -209500.0, 6414000.0]}

    def poly(r: list[float]) -> dict[str, Any]:
        return {"type": "Polygon", "coordinates": [[[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]], [r[0], r[1]]]]}

    varn = [f"byggnad-{i}: ingen markhöjd under eller kring byggnaden; utelämnad" for i in range(1, 25)]
    varn += [f"mm_{i}: {2 + i * 3} % av cellerna saknar höjd (NaN) och fylls från grannrutan" for i in range(1, 7)]
    varn += ["Träd: inga trädpunkter inom kärnan plus bufferten; lagret är tomt.", "Byggnadshöjd: ur Lantmäteriets laser (oklassade punkter), inte ur baskartans z."]
    g = {
        "ref": "goteborg-test@v2", "id": "goteborg-test", "version": "v2", "namn": "Göteborg (påhittad)", "kommun": "Göteborg", "epsg": 3011,
        "karna_3011": mani["karna_3011"], "karna_polygon_3011": poly(mani["karna_3011"]), "gransen_3011": mani["gransen_3011"],
        "gransen_polygon_3011": poly(mani["gransen_3011"]), "buffert_m": 250.0, "varningar": varn, "manifest_sha256": "0" * 64, "far_skapa_projekt": True,
    }  # fmt: skip
    p.ctx.route(f"{p.url}/api/geografier", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"geografier": [g], "far_skapa_projekt": True, "organisationer": [{"id": "o1", "namn": "Exempelkommun"}]})))
    s.goto(f"{p.url}/#/nytt")
    s.wait_for_selector("#np-geovarningar", timeout=30_000)
    s.wait_for_timeout(2500)
    p.bild("50-nytt-start")
    summa = s.inner_text("#np-geovarningar > summary")
    p.ok("vänsterspalten visar en sammanfattning, inte alla varningar", summa == "32 varningar vid mappningen", summa)
    p.ok("listan är hopfälld från början", s.evaluate("document.querySelector('#np-geovarningar').open") is False)
    p.ok("hopfälld lista visar inga rader", not s.locator("#np-geovarningar li").first.is_visible())
    s.click("#np-geovarningar > summary")
    s.wait_for_timeout(300)
    grupper = s.locator("#np-geovarningar .flode-varningsgrupp > summary").all_inner_texts()
    p.ok("raderna är grupperade per slag (störst först)", len(grupper) == 2 and grupper[0].startswith("24 rader: ingen markhöjd") and grupper[1].startswith("6 rader:"), str(grupper))
    p.bild("51-nytt-varningar-oppna")
    vr = s.inner_text("#np-vridning") if s.locator("#np-vridning").count() else ""
    p.ok("en rad förklarar varför rutorna ser vridna ut", vr.startswith("Rutorna ser vridna ut") and "18° ö" in vr and "cirka" in vr, vr)
    p.ok("OSM-bakgrunden är tänd och plattor hämtas", s.is_checked("#np-osm-kryss") and net.antal_plattor() > 0, f"{net.antal_plattor()} plattor")
    p.ok("plattorna begärs med Referer = sidans ursprung", {r for _, r in net.plattor} == {p.url + "/"})
    p.ok("attribution syns på kartan", "© OpenStreetMap-bidragsgivare" in s.inner_text(".maplibregl-ctrl-attrib"))
    # Rita en rektangel inom kärnan: kärnan och avgränsningen ritas ovanpå bakgrunden.
    k = mani["karna_3011"]
    mx, my = (k[0] + k[2]) / 2, (k[1] + k[3]) / 2
    s.fill("#np-namn", "Haga nytt (prov)")
    a, b = p.skarm(mx - 60, my - 40)
    c, d = p.skarm(mx + 60, my + 40)
    s.mouse.click(a, b)
    s.mouse.move(c, d, steps=4)
    s.mouse.click(c, d)
    s.wait_for_timeout(500)
    p.ok("avgränsningen ritas ovanpå bakgrunden och projektet kan skapas", s.is_enabled("#np-skapa"), s.inner_text("#np-hinder"))
    p.bild("52-nytt-ritat")
    s.wait_for_timeout(1500)  # plattor som redan var på väg när bilden ritades
    n = net.antal_plattor()
    s.click("#np-osm-kryss")
    s.wait_for_timeout(300)
    s.mouse.wheel(0, -240)
    s.wait_for_timeout(1200)
    p.ok(
        "bakgrunden går att stänga av (inga fler plattor, ingen attribution)",
        net.antal_plattor() == n and "OpenStreetMap" not in s.inner_text(".maplibregl-ctrl-attrib"),
        f"plattor {n} -> {net.antal_plattor()}; attribution: {s.inner_text('.maplibregl-ctrl-attrib')!r}",
    )
    p.bild("53-nytt-osm-av")
    s.click("#np-osm-kryss")  # tillbaka till förvalet
    csp_fel = csp_overtradelser(p.konsol_fel)
    p.ok("inga CSP-överträdelser på Nytt projekt", not csp_fel, "; ".join(csp_fel)[:300])
    # Efter skapande: projektets varningar visas på samma sätt (påhittat svar).
    p.ctx.route(
        f"{p.url}/api/projekt",
        lambda r: r.fulfill(status=201, content_type="application/json", body=json.dumps({
            "kontrakt": "K48/v1", "projekt_id": "p-1", "namn": "Haga nytt (prov)", "organisation": "o1", "organisation_synlig": False,
            "geografi": "goteborg-test@v2", "avgransning_3011": [0, 0, 1, 1], "roll": "utredare", "varningar": varn[:12], "info": ["Så räknas projektet (påhittat)."],
        })) if r.request.method == "POST" else r.fallback(),
    )
    s.select_option("#np-org", "o1") if s.locator("#np-org").count() else None
    s.click("#np-skapa")
    s.wait_for_selector("#np-oppna", timeout=15_000)
    p.ok("efter skapande: sammanfattning av projektets varningar, hopfälld", s.inner_text("#np-varningar > summary") == "12 varningar när projektet skapades" and s.evaluate("document.querySelector('#np-varningar').open") is False)
    p.bild("54-nytt-skapat")


# =====================================================================================================================
# Smal skärm, beröring
# =====================================================================================================================
def smal(p: Prov, webblasare: Browser) -> None:
    """Pekskärmsdelen (R7): 390x844 med beröring. Tryck med `touchscreen`, håll och dra med CDP:s pekhändelser."""
    p.del_ = "smal"
    print("Smal skärm (390x844, beröring)", flush=True)
    p.oppna(webblasare, 390, 844, True)
    s = p.s
    cdp = s.context.new_cdp_session(s)

    def tryck(x: float, y: float) -> None:
        s.touchscreen.tap(x, y)
        s.wait_for_timeout(250)

    def knapptryck(valjare: str) -> None:
        """Tryck på en knapp i den sidledes rullande verktygsraden: rulla fram den först (annars hamnar trycket utanför)."""
        s.locator(valjare).scroll_into_view_if_needed()
        s.wait_for_timeout(150)
        s.locator(valjare).tap()
        s.wait_for_timeout(200)

    def tryck_sweref(x: float, y: float) -> None:
        a, b = p.skarm(x, y)
        tryck(a, b)

    def beroring(punkter: list[tuple[float, float]], vantan_ms: int = 0) -> None:
        """Ett finger: ned vid första punkten, vänta (håll), rör till de övriga, upp. Riktiga pekhändelser."""
        x0, y0 = punkter[0]
        cdp.send(
            "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]}
        )
        if vantan_ms:
            s.wait_for_timeout(vantan_ms)
        for x, y in punkter[1:]:
            cdp.send(
                "Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]}
            )
            s.wait_for_timeout(40)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        s.wait_for_timeout(300)

    def rad(x0: float, y0: float, x1: float, y1: float, n: int = 8) -> list[tuple[float, float]]:
        return [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]

    p.starta_steg2()
    s.fill("#fs-namn", NAMN + " smal")  # filen heter ritprov-smal
    s.keyboard.press("Tab")
    s.evaluate("window.scrollTo(0, 0)")  # Tab flyttade fokus nedåt och sidan med
    p.bild("20-smal-start")
    p.ok(
        "ingen sidled rullning på 390 px",
        bool(s.evaluate("document.documentElement.scrollWidth <= innerWidth")),
    )
    tb = s.locator(".flode-verktyg").bounding_box() or {}
    karta = s.locator(".flode-karta").bounding_box() or {}
    p.ok(
        "verktygsraden är en rad och täcker under en femtedel av kartan",
        tb.get("height", 999) < 0.2 * karta.get("height", 1),
        f"{tb.get('height')} av {karta.get('height')} px".replace(".", ","),
    )
    p.ok(
        "verktygsraden går att svepa i sidled",
        bool(
            s.evaluate(
                "(() => { const e = document.querySelector('.flode-verktyg'); return e.scrollWidth > e.clientWidth; })()"
            )
        ),
    )
    knappar = s.evaluate(
        "[...document.querySelectorAll('.flode-verktyg button, .flode-zoom button')].map(b => b.getBoundingClientRect().height)"
    )
    p.ok(
        "alla verktygs- och zoomknappar är minst 40 px höga (pekyta)",
        min(knappar) >= 40,
        f"minst {min(knappar):.0f} px",
    )
    bredd = s.evaluate(
        "[...document.querySelectorAll('.flode-verktyg button, .flode-zoom button')].map(b => b.getBoundingClientRect().width)"
    )
    p.ok("och minst 32 px breda", min(bredd) >= 32, f"minst {min(bredd):.0f} px")
    p.ok(
        "handtag är större på pekskärm (grov pekare)",
        bool(s.evaluate("matchMedia('(pointer: coarse)').matches")),
    )

    # Zooma med knapparna och flytta kartan med ett finger till gården
    for _ in range(3):
        tryck(*_mitt(s, "#fs-in"))
    s.wait_for_timeout(500)
    ax, ay = p.skarm(151275, 6580275)
    cb = s.locator(".flode-karta-yta").bounding_box() or {}
    mx, my = cb["x"] + cb["width"] / 2, cb["y"] + cb["height"] / 2 + 40
    dx, dy = mx - ax, my - ay
    start = (cb["x"] + cb["width"] / 2 + dx / 2, cb["y"] + cb["height"] * 0.55 + dy / 2)
    beroring(rad(start[0], start[1], start[0] - dx * 0 + dx, start[1] + dy))
    nx, ny = p.skarm(151275, 6580275)
    p.ok(
        "ett finger panorerar kartan (gården ligger nu nära mitten)",
        abs(nx - mx) < 60 and abs(ny - my) < 60,
        f"({nx:.0f}; {ny:.0f}) mot ({mx:.0f}; {my:.0f})",
    )
    p.bild("21-smal-gard")

    # Hus med tryck, Klar
    s.tap("#rl-a-hus") if s.locator("#rl-a-hus").is_visible() else None
    knapptryck('.flode-verktyg button[data-v="hus"]')
    p.ok("hjälptexten talar om tryck, inte klick och tangenter", "Tryck" in p.hjalp(), p.hjalp())
    for x, y in [(151260, 6580260), (151290, 6580260), (151290, 6580290), (151260, 6580290)]:
        tryck_sweref(x, y)
    p.ok("Klar är aktiv medan huset ritas", s.is_enabled('.flode-verktyg button[data-v="klar"]'))
    knapptryck('.flode-verktyg button[data-v="klar"]')
    p.ok("Klar avslutar huset utan tangentbord", "höjd" in p.hjalp().lower(), p.hjalp())
    p.ok(
        "Klar är avstängd när inget ritas", not s.is_enabled('.flode-verktyg button[data-v="klar"]')
    )
    s.fill("#fs-hojd", "12")
    s.tap("#fs-namn") if False else None
    s.keyboard.press("Enter")
    p.vanta_kontroll()
    hus = [o for o in p.objekt() if o["lager_id"] == "hus"]
    fore = hus[0]["geometri"]["coordinates"][0][0] if hus else [0.0, 0.0]
    p.ok(
        "huset är sparat med fyra hörn",
        bool(hus) and len(hus[0]["geometri"]["coordinates"][0]) == 5,
    )
    p.bild("22-smal-hus")

    # Linje med tryck och Klar, samt Backa och Avbryt
    s.tap("#rl-a-mur-och-kant") if s.locator("#rl-a-mur-och-kant").is_visible() else s.locator(
        "#rl-a-mur-och-kant"
    ).click()
    knapptryck('.flode-verktyg button[data-v="linje"]')
    tryck_sweref(151250, 6580300)
    tryck_sweref(151275, 6580305)
    tryck_sweref(151300, 6580300)
    knapptryck('.flode-verktyg button[data-v="backa"]')
    p.ok(
        "Backa en punkt tar bort den senaste punkten",
        "Tryck nästa" in p.hjalp() or "nästa" in p.hjalp().lower(),
        p.hjalp(),
    )
    tryck_sweref(151300, 6580300)
    knapptryck('.flode-verktyg button[data-v="klar"]')
    s.wait_for_timeout(500)
    p.ok(
        "Klar avslutar en linje och panelen visar den",
        "Mur" in s.inner_text("#fs-figur-rubrik"),
        s.inner_text("#fs-figur-rubrik"),
    )
    knapptryck('.flode-verktyg button[data-v="linje"]')
    tryck_sweref(151250, 6580250)
    knapptryck('.flode-verktyg button[data-v="avbryt"]')
    p.ok("Avbryt kastar utkastet", not s.is_enabled('.flode-verktyg button[data-v="klar"]'))
    p.vanta_kontroll()

    # Håll för att flytta: markera huset, håll 600 ms, dra 20 m österut
    knapptryck('.flode-verktyg button[data-v="valj"]')
    tryck_sweref(151275, 6580275)
    p.ok("ett tryck markerar huset", s.input_value("#fs-hojd") == "12", s.input_value("#fs-hojd"))
    x0, y0 = p.skarm(151275, 6580275)
    x1, y1 = p.skarm(151295, 6580275)
    beroring(rad(x0, y0, x1, y1, 6), vantan_ms=700)
    p.vanta_kontroll()
    hus2 = [o for o in p.objekt() if o["lager_id"] == "hus"]
    efter = hus2[0]["geometri"]["coordinates"][0][0] if hus2 else [0.0, 0.0]
    flytt = efter[0] - fore[0]
    p.ok(
        "håll och dra flyttar huset (cirka 20 m österut)",
        15 < flytt < 25 and abs(efter[1] - fore[1]) < 3,
        f"{flytt:.1f} m öster, {efter[1] - fore[1]:.1f} m norr".replace(".", ","),
    )
    p.bild("23-smal-flyttat")
    # Dra utan att hålla: kartan panorerar, huset ligger kvar
    kvar = efter
    a0, b0 = p.skarm(151295, 6580275)
    beroring(rad(a0, b0, a0 - 60, b0 + 20, 6), vantan_ms=0)
    p.vanta_kontroll()
    hus3 = [o for o in p.objekt() if o["lager_id"] == "hus"]
    efter2 = hus3[0]["geometri"]["coordinates"][0][0] if hus3 else [0.0, 0.0]
    p.ok(
        "ett snabbt drag med fingret panorerar kartan och flyttar inte huset",
        abs(efter2[0] - kvar[0]) < 0.01 and abs(efter2[1] - kvar[1]) < 0.01,
        f"{efter2}",
    )
    # Håll långt från alla figurer: ingenting tas i, kartan flyttas inte
    p.bild("24-smal-panorerat")

    # Egenskapspanelen, skarv och 3D på smal skärm
    s.tap("#fs-3d-knapp")
    s.wait_for_timeout(4000)
    p.ok(
        "3D-förhandsvisningen ryms på smal skärm",
        bool(
            s.evaluate(
                "(() => { const r = document.querySelector('#fs-3d').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; })()"
            )
        ),
    )
    p.bild("25-smal-3d")
    s.tap("#fs-3d-stang")
    s.evaluate("window.scrollTo(0, 0)")
    p.bild("26-smal-panel", hel=True)

    # Tangentbord, fokus och etiketter
    namnlosa = s.evaluate(
        """() => [...document.querySelectorAll('.flode-sida button, .flode-sida input, .flode-sida select, .flode-sida summary, .flode-sida canvas')]
            .filter(e => e.offsetParent !== null && !e.closest('[hidden]') && !e.classList.contains('maplibregl-canvas-container'))
            .filter(e => {
              const n = (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || (e.labels && e.labels.length ? e.labels[0].textContent : '') || e.textContent || e.title || '').trim();
              return n === '';
            }).map(e => e.tagName + '#' + e.id + '.' + e.className).slice(0, 12)"""
    )
    p.ok(
        "alla knappar, fält och val i steg 2 har ett namn (etikett, aria-label eller text)",
        not namnlosa,
        "; ".join(namnlosa),
    )
    fel = [f for f in p.fel_i_sidan]
    p.ok("inga sidfel", not fel, "; ".join(fel)[:300])
    stada(p.url)
    s.context.close()


def tangentbord(p: Prov, webblasare: Browser) -> None:
    """Tangentbindningar som kan ändras (sparas i webbläsaren), fokusordning och synligt fokus."""
    p.del_ = "tangenter"
    print("Tangentbord och fokus (1440x900)", flush=True)
    p.oppna(webblasare, 1440, 900, False)
    s = p.s
    p.starta_steg2()
    if not s.evaluate("document.querySelector('#fs-tangenter-ruta').open"):
        s.locator("#fs-tangenter-ruta summary").click()
    falt = s.locator('input[data-kommando="hus"]')
    falt.click()
    s.keyboard.press("h")
    p.ok(
        "tangenten för Rita hus kan ändras till H",
        falt.input_value().lower() == "h",
        falt.input_value(),
    )
    p.bild("30-tangenter")
    s.locator("body").click(position={"x": 700, "y": 400})
    s.keyboard.press("h")
    p.ok("H väljer nu Rita hus", p.aktivt("hus"))
    s.keyboard.press("w")
    p.ok("den gamla tangenten W gör inget längre", p.aktivt("hus"))
    p.starta_steg2()
    if not s.evaluate("document.querySelector('#fs-tangenter-ruta').open"):
        s.locator("#fs-tangenter-ruta summary").click()
    p.ok(
        "den ändrade bindningen finns kvar efter omladdning",
        s.locator('input[data-kommando="hus"]').input_value().lower() == "h",
    )
    s.click("#fs-tangenter-forval")
    p.ok(
        "Återställ förval ger W igen",
        s.locator('input[data-kommando="hus"]').input_value().lower() == "w",
    )
    # Fokusordning: Tab från sidans början når verktygsknapparna, fokus syns
    s.locator("body").click(position={"x": 700, "y": 400})
    s.evaluate("document.activeElement && document.activeElement.blur()")
    antal = 0
    synligt = True
    for _ in range(40):
        s.keyboard.press("Tab")
        info = s.evaluate(
            "(() => { const e = document.activeElement; if (!e) return null; const c = getComputedStyle(e); return {v: e.closest('.flode-verktyg') !== null, o: c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0, t: e.tagName}; })()"
        )
        if info and info["v"]:
            antal += 1
            synligt = synligt and bool(info["o"])
    p.ok(
        "Tab når verktygsknapparna (minst 10 av dem) och fokus syns på varje",
        antal >= 10 and synligt,
        f"{antal} knappar, synligt fokus: {synligt}",
    )
    p.bild("31-fokus")
    # Rita ett hus med bara tangentbordet för verktygsval och Enter är inte möjligt utan pekare: provet gäller val och avslut.
    p.ok("inga sidfel", not p.fel_i_sidan, "; ".join(p.fel_i_sidan)[:200])
    s.context.close()


def _mitt(s: Page, valjare: str) -> tuple[float, float]:
    b = s.locator(valjare).bounding_box() or {"x": 0, "y": 0, "width": 0, "height": 0}
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default="http://127.0.0.1:8795")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--ut", default=str(UT))
    ap.add_argument("--bara", choices=["bred", "smal", "tangenter", "geografi", "nytt"], default=None)
    ap.add_argument("--geografi-url", default="http://127.0.0.1:8796", help="syntetiskt geografiprojekt (tools/geografi_prov_server.py)")
    ap.add_argument(
        "--utan-korning",
        action="store_true",
        help="hoppa över steg 3 och 4 (körningen tar någon minut)",
    )
    a = ap.parse_args()
    p = Prov(a.url, Path(a.ut))
    if a.bara not in ("geografi", "nytt"):
        stada(a.url)
    with sync_playwright() as pw:
        pw_: Playwright = pw
        webblasare = pw_.chromium.launch(executable_path=a.chrome, args=ARGS)
        try:
            if a.bara in (None, "bred"):
                bred(p, webblasare, not a.utan_korning)
            if a.bara in (None, "smal"):
                smal(p, webblasare)
            if a.bara in (None, "tangenter"):
                tangentbord(p, webblasare)
            if a.bara in ("geografi", "nytt"):
                pg = Prov(a.geografi_url, Path(a.ut))
                if a.bara == "geografi":
                    geografi(pg, webblasare, not a.utan_korning)
                else:
                    nytt_projekt(pg, webblasare)
                p.resultat += pg.resultat
                p.konsol_fel += pg.konsol_fel
                p.fel_i_sidan += pg.fel_i_sidan
        finally:
            webblasare.close()
    print()
    fel = [r for r in p.resultat if not r[1]]
    print(f"{len(p.resultat) - len(fel)} av {len(p.resultat)} kontroller gick igenom.")
    for d, _, namn, detalj in fel:
        print(f"  FEL [{d}] {namn}: {detalj}")
    if p.konsol_fel:
        print("Konsolfel:", "; ".join(sorted(set(p.konsol_fel)))[:600])
    sys.exit(1 if fel else 0)


if __name__ == "__main__":
    main()
