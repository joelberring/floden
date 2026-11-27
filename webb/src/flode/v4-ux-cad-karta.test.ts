/*
 * Körs med `npm test`. Deterministiska regressionstester för Våg 4 (UX U5c, U5d, CAD-mått + GET ekologi):
 * - GET /api/analys/ekologi: skrivlös läsning för `lasare` med URLSearchParams (aldrig POST i Steg 2).
 * - U5c: Tangentbordsåtkomst för kartvärden (`#ar-avl` med `aria-live="polite"`, "Läs av kartcentrum",
 *        `Alt+I`, piltangentsförflyttning i SWEREF 99 18 00 utan musevent).
 * - U5d: Hopfällbar ordlista med nyckelbegrepp (RH 2000, SWEREF 99 18 00, SVF, VSC, ECA / dPC, MKN,
 *        NEN 8100, Skiss) samt domänkonsekventa texter utan "vattendjup"-läckage.
 * - CAD-mått: Exakt SWEREF-geometri (0°, 90°, 180°, 45° inom 1e-9 m), decimalkomma ("12,5"), avvisning
 *             av L <= 0 / NaN samt integration med Ritverktyg, Linjeverktyg och cadMattHtml.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import module from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { ekologiAnalysUrl, hamtaEkologiAnalys } from "../api.ts";
import type { AvlasningSvar } from "../fragor-typer.ts";
import {
  beraknaPunktFranLangdOchBaring,
  tolkaLangdOchBaring,
  tolkaSwerefKoordinat,
} from "../karta/linjeverktyg.ts";
import { PlanDok } from "../karta/plandok.ts";
import type { RitLager } from "../karta/ritlager.ts";
import { Ritverktyg } from "../karta/ritverktyg.ts";
import { swerefTillWgs84 } from "../sweref.ts";
import type { ProjektVy } from "../types.ts";
import { cadMattHtml, ORDLISTA_POSTER, ordlistaHtml } from "./panelhtml.ts";

const FLODE_MAPP = path.dirname(fileURLToPath(import.meta.url));
const ARBETSYTA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.ts"), "utf-8");
const ARBETSYTA_CSS = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.css"), "utf-8");

// Ladda rena exporter från arbetsyta.ts direkt i Node 24 genom att stubba enbart webbläsar-/WebGL-beroenden.
const BROWSER_STUB_SLUT = [
  "/karta/kartapi.ts",
  "/karta/kartmotor.ts",
  "/karta/lagerpanel.ts",
  "/karta/ritlagerpanel.ts",
  "/karta/ritverktyg.ts",
  "/kartsida3d.ts",
  "/flode/kartdel.ts",
  "/flode/resultat.ts",
  "/flode/underlag.ts",
];
const STUB_MODUL = `
export default {};
export const arGrovPekare = () => false;
export const Kartmotor = class {};
export const TOM_RITVY = {};
export const monteraLagerpanel = () => {};
export const monteraRitlagerpanel = () => {};
export const FORVALD_HOJD_M = 9;
export const Ritverktyg = class {};
export const TANGENTER = { valj: "Q", hus: "W", yta: "⇧W", linje: "E", angra: "Z", laggtill: "A", tabort: "S", klipp: "X", ihop: "G", vand: "V", forenkla: "⇧S", fast: "T", foljkant: "F", ratVinkel: "R" };
export const fasadForklaringHtml = () => "";
export const skapa3dVy = () => ({ montera: () => ({ avmontera() {} }) });
export const VY3D_ID = "vy:3d";
export const BasLager = class {};
export const byggnaderMedForslag = () => [];
export const skapaKarta = () => ({});
export const teckenHtml = () => "";
export const monteraUnderlagForm = () => {};
export const taBortUnderlag = async () => {};
export const bytScenarioNamn = async () => ({});
export const foreslaSkarvatgard = async () => ({});
export const hamtaBifurkationer = async () => ({});
export const hamtaProjektInfo = async () => ({});
export const hamtaScenario = async () => ({});
export const hamtaSektion = async () => ({});
export const hamtaSvfIntervall = async () => ({});
export const hamtaTidsserie = async () => ({});
export const hamtaVattenIntervall = async () => ({});
export const kontrolleraAndringar = async () => ({});
export const lageIdagStatus = async () => ({ korningar: [] });
export const listaScenarier = async () => ({ scenarier: [] });
export const scenarioStatus = async () => ({ korningar: [] });
export const sparaScenario = async () => ({});
export const taBortScenario = async () => ({});
`;

let arbetsytaExportsPromise: Promise<typeof import("./arbetsyta.ts")> | null = null;
function laddaArbetsytaExports(): Promise<typeof import("./arbetsyta.ts")> {
  if (!arbetsytaExportsPromise) {
    module.registerHooks({
      resolve(specifier, context, nextResolve) {
        if (specifier === "maplibre-gl") {
          return { shortCircuit: true, url: "stub:maplibre-gl", format: "module" };
        }
        if (specifier.startsWith(".") && context.parentURL?.endsWith("/flode/arbetsyta.ts")) {
          const resolved = new URL(specifier, context.parentURL).pathname;
          if (BROWSER_STUB_SLUT.some((s) => resolved.endsWith(s))) {
            return { shortCircuit: true, url: `stub:${resolved}`, format: "module" };
          }
        }
        return nextResolve(specifier, context);
      },
      load(url, context, nextLoad) {
        if (url.startsWith("stub:")) {
          return { shortCircuit: true, format: "module", source: STUB_MODUL };
        }
        return nextLoad(url, context);
      },
    });
    arbetsytaExportsPromise = import("./arbetsyta.ts");
  }
  return arbetsytaExportsPromise;
}

// ------------------------------------------------------------------ 1. GET /api/analys/ekologi (lasare-kompatibel)

test("GET /api/analys/ekologi: hamtaEkologiAnalys använder GET med URLSearchParams (omrade_id, scenario, natverk) och aldrig POST", async () => {
  const url = ekologiAnalysUrl({
    omrade_id: "haga-test",
    scenario: "mitt-forslag",
    natverk: "ekskog_nmd",
  });
  const parsad = new URL(url, "http://localhost");
  assert.equal(parsad.pathname, "/api/analys/ekologi");
  assert.equal(parsad.searchParams.get("omrade_id"), "haga-test");
  assert.equal(parsad.searchParams.get("scenario"), "mitt-forslag");
  assert.equal(parsad.searchParams.get("natverk"), "ekskog_nmd");
  assert.equal(parsad.searchParams.has("artprofil"), false);

  const baraOmrade = new URL(ekologiAnalysUrl({ omrade_id: "demo" }), "http://localhost");
  assert.equal(baraOmrade.searchParams.get("omrade_id"), "demo");
  assert.equal(baraOmrade.searchParams.has("scenario"), false);
  assert.equal(baraOmrade.searchParams.has("natverk"), false);

  // Verifiera faktiskt HTTP-anrop via fetch (GET, ingen body, inget CSRF-huvud)
  const gammalFetch = globalThis.fetch;
  const gammalDoc = (globalThis as { document?: unknown }).document;
  let anropadUrl = "";
  let anropadInit: RequestInit | undefined;
  (globalThis as { document?: unknown }).document = { cookie: "floden_csrf=hemlig-token" };
  globalThis.fetch = (async (u: string | URL | Request, init?: RequestInit) => {
    anropadUrl = String(u);
    anropadInit = init;
    return new Response(JSON.stringify({ artprofil_id: "ekskog_nmd", fore: { ECA_m2: 120000 } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const svar = await hamtaEkologiAnalys<{ artprofil_id: string }>({
      omrade_id: "haga-test",
      scenario: "forslag-1",
      natverk: "ekskog_nmd",
    });
    assert.equal(svar.artprofil_id, "ekskog_nmd");
  } finally {
    globalThis.fetch = gammalFetch;
    if (gammalDoc === undefined) delete (globalThis as { document?: unknown }).document;
    else (globalThis as { document?: unknown }).document = gammalDoc;
  }

  assert.equal(anropadUrl, "/api/analys/ekologi?omrade_id=haga-test&scenario=forslag-1&natverk=ekskog_nmd");
  assert.equal((anropadInit?.method ?? "GET").toUpperCase(), "GET");
  assert.equal(anropadInit?.body, undefined);
  const huvuden = (anropadInit?.headers ?? {}) as Record<string, string>;
  assert.equal(huvuden["X-CSRF-Token"], undefined, "GET-anrop ska inte skicka CSRF-huvud");

  // Källkodskontroll i arbetsyta.ts: Steg 2 anropar hamtaEkologiAnalys och postar aldrig till /api/analys/ekologi
  assert.match(ARBETSYTA_KALLTEXT, /hamtaEkologiAnalys<EkologiMedLager>\(/);
  assert.doesNotMatch(ARBETSYTA_KALLTEXT, /\/api\/analys\/ekologi["'][^;\n]*method:\s*["']POST["']/);
  // Icke-blockerande Ekologi & GYF: ritaAnalysPanel() (som anropar ritaEkologiSkrivbord) och GYF körs FÖRE hamtaEkologiAnalys()
  const idxValjVy = ARBETSYTA_KALLTEXT.indexOf("const valjVy = async");
  const idxRitaPanel = ARBETSYTA_KALLTEXT.indexOf("ritaAnalysPanel();", idxValjVy);
  const idxHamtaEko = ARBETSYTA_KALLTEXT.indexOf("hamtaEkologiAnalys<EkologiMedLager>(", idxValjVy);
  assert.ok(idxValjVy > 0 && idxRitaPanel > idxValjVy && idxHamtaEko > idxRitaPanel);
  assert.match(ARBETSYTA_KALLTEXT, /gyfUtciKontroller\.sakerstallGyfLaddad\?\.\(\)/);
});

// ------------------------------------------------------------------ 2. U5c: Tangentbordsåtkomst för kartvärden

test("U5c: #ar-avl har aria-live='polite' och tangentbordsavläsning (Alt+I, knapp, piltangenter) uppdaterar #ar-avl utan musevent", async () => {
  // Källkodskontroll för ARIA, fokusbar knapp och kartbehållare
  assert.match(ARBETSYTA_KALLTEXT, /id="ar-avl"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(ARBETSYTA_KALLTEXT, /id="ar-las-centrum"[^>]*>Läs av kartcentrum<\/button>/);
  assert.match(ARBETSYTA_KALLTEXT, /id="ar-a"\s+tabindex="0"/);

  const ar = await laddaArbetsytaExports();
  const selektorer = ar.ARBETSYTA_KLICK_SELEKTOR.split(",").map((s) => s.trim());
  assert.ok(selektorer.includes("#ar-las-centrum"), "ARBETSYTA_KLICK_SELEKTOR måste innehålla #ar-las-centrum");

  // Kortkommando Alt+I (både Windows/Linux key="i" och macOS code="KeyI")
  assert.equal(ar.arKartcentrumKortkommando({ key: "i", code: "KeyI", altKey: true, ctrlKey: false, metaKey: false }), true);
  assert.equal(ar.arKartcentrumKortkommando({ key: "^", code: "KeyI", altKey: true, ctrlKey: false, metaKey: false }), true);
  assert.equal(ar.arKartcentrumKortkommando({ key: "i", code: "KeyI", altKey: false, ctrlKey: false, metaKey: false }), false);
  assert.equal(ar.arKartcentrumKortkommando({ key: "i", code: "KeyI", altKey: true, ctrlKey: true, metaKey: false }), false);

  // Simulera kartvy centrerad i SWEREF 99 18 00 (150 000, 6 580 000) utan något musevent
  const [startLng, startLat] = swerefTillWgs84(150000, 6580000);
  let aktuellVy = { lng: startLng, lat: startLat, zoom: 17 };
  const avlEl = { hidden: true, innerHTML: "" };
  const avlastaPunkter: Array<{ e: number; n: number }> = [];

  const vyObj = {
    fore_lager: "skyfall.fore.max_djup",
    efter_lager: "skyfall.efter.max_djup",
    skillnad_lager: "skyfall.skillnad.max_djup",
    lage_idag: false,
  };

  const styrning = ar.skapaTangentbordsAvlasning({
    hamtaVy: () => aktuellVy,
    settVy: (ny) => {
      aktuellVy = ny;
    },
    meterPerPx: () => 0.5,
    lasAv: (e, n) => {
      avlastaPunkter.push({ e, n });
      const svar: AvlasningSvar = {
        e,
        n,
        analys: "skyfall",
        roll: "max_djup",
        varden: [
          {
            lager_id: "skyfall.fore.max_djup",
            namn: "Maxdjup",
            etikett: "Maxdjup",
            typ: "bas",
            scenario: "fore",
            enhet: "m",
            inom: true,
            tak: false,
            varde: 0.32,
          },
        ],
      };
      ar.renderaAvlasningElement(avlEl, {
        senasteAvl: svar,
        vy: vyObj,
        lage: "nulage",
        amne: "vatten",
        visaTak: false,
        treDText: null,
        hojdText: "Mark +12,4 m",
        punktEtikett: ar.formateraKartcentrumEtikett(e, n),
      });
    },
  });

  // 1. Läs av kartcentrum direkt (knapp / Alt+I)
  const c0 = styrning.lasCentrum();
  assert.ok(c0);
  assert.ok(Math.abs(c0.e - 150000) < 0.05);
  assert.ok(Math.abs(c0.n - 6580000) < 0.05);
  assert.equal(avlEl.hidden, false);
  assert.match(avlEl.innerHTML, /Kartcentrum/);
  assert.match(avlEl.innerHTML, /32 cm/);
  assert.match(avlEl.innerHTML, /Mark \+12,4 m/);

  // 2. Piltangenter (ArrowUp = +N, ArrowRight = +E, ArrowDown = -N, ArrowLeft = -E) flyttar och läser av
  const norr = styrning.stegPil("ArrowUp");
  assert.ok(norr);
  assert.ok(norr.n > c0.n + 20, "ArrowUp ska flytta norrut (+N)");
  assert.ok(Math.abs(norr.e - c0.e) < 0.05);

  const ost = styrning.stegPil("ArrowRight", true); // finsteg (Shift+pil)
  assert.ok(ost);
  assert.ok(ost.e > norr.e + 5 && ost.e < norr.e + 10, "Shift+ArrowRight ska ge finsteg österut (+E)");

  assert.equal(avlastaPunkter.length, 3, "Varje tangentbordsåtgärd ska anropa deterministisk värdeavläsning");

  // 3. Fungerar även för Sol/SVF, Ekologi och Luft (inga hårdkodade vattendjup)
  const solEl = { hidden: true, innerHTML: "" };
  ar.renderaAvlasningElement(solEl, {
    senasteAvl: {
      e: 150000,
      n: 6580000,
      analys: "sol",
      roll: "svf",
      varden: [
        {
          lager_id: "sol.fore.svf",
          namn: "SVF",
          etikett: "SVF",
          typ: "bas",
          scenario: "fore",
          enhet: "",
          inom: true,
          tak: false,
          varde: 0.74,
        },
      ],
    },
    vy: { fore_lager: "sol.fore.svf", efter_lager: null, skillnad_lager: "sol.fore.svf", lage_idag: true },
    lage: "nulage",
    amne: "dagsljus",
    visaTak: false,
    treDText: null,
    hojdText: "Mark +12,4 m",
    punktEtikett: ar.formateraKartcentrumEtikett(150000, 6580000),
  });
  assert.equal(solEl.hidden, false);
  assert.match(solEl.innerHTML, /0,74/);
  assert.doesNotMatch(solEl.innerHTML, /cm|torr mark/i);
});

// ------------------------------------------------------------------ 3. U5d: Ordlista & domänkonsekvent terminologi

test("U5d: hopfällbar ordlista i Steg 2 innehåller alla nyckelbegrepp och är stängd som standard", () => {
  const htmlStangd = ordlistaHtml(false);
  assert.match(htmlStangd, /<details\b[^>]*id="ar-ordlista"/);
  assert.doesNotMatch(htmlStangd, /<details\b[^>]*\bopen\b/, "Ordlistan ska vara hopfälld som standard");
  assert.match(htmlStangd, /<summary>Ordlista och begrepp<\/summary>/);

  const htmlOppen = ordlistaHtml(true);
  assert.match(htmlOppen, /<details\b[^>]*\bopen\b/);

  for (const term of ["RH 2000", "SWEREF 99 18 00", "SVF", "VSC", "ECA", "dPC", "MKN", "NEN 8100", "Skiss"]) {
    assert.ok(htmlStangd.includes(term), `Ordlistan saknar begreppet "${term}"`);
  }
  // Kontrollera korrekta sakdetaljer
  assert.match(htmlStangd, /0–1/, "SVF ska ange skalan 0–1");
  assert.match(htmlStangd, /PM10/, "MKN ska nämna PM10");
  assert.match(htmlStangd, /A–E/, "NEN 8100 ska nämna komfortklasser A–E");
  assert.ok(ORDLISTA_POSTER.length >= 8);

  // Källkodskontroll: ordlistaHtml() monteras i Steg 2
  assert.match(ARBETSYTA_KALLTEXT, /\$\{ordlistaHtml\(\)\}/);
});

test("U5d: domänkonsekvent terminologi utan vattendjup-läckage för sol, ekologi och luft", async () => {
  const ar = await laddaArbetsytaExports();

  for (const amne of ["dagsljus", "ekologi", "luft"] as const) {
    assert.doesNotMatch(ar.nulageTipsHtmlForAmne(amne), /vattendjup|rinnstråk|ovanpå vattnet/i);
    assert.doesNotMatch(ar.underlagEtikettForAmne(amne), /vatten/i);
    assert.doesNotMatch(ar.lagerGruppEtikettForAmne(amne), /vatten/i);
  }
  assert.match(ar.underlagEtikettForAmne("vatten"), /vatten/i);
  assert.match(ar.lagerGruppEtikettForAmne("vatten"), /skyfall/i);

  // Ekologi + skillnad får aldrig visa "djupare vatten"
  const ekoVy: ProjektVy = {
    id: "v-eko",
    avsnitt: "ekologi",
    rubrik: "Ekologi",
    korning_id: "k-eko",
    fore_lager: "eko.fore",
    efter_lager: "eko.efter",
    skillnad_lager: "eko.skillnad",
    efter_namn: "Förslag",
    andring: "",
    mattnamn: "Strömtäthet",
  };
  const ekoLas = ar.lasKartanForAmne("ekologi", ekoVy, "skillnad", false);
  assert.doesNotMatch(ekoLas, /vatten|djupare|grundare/i);
  assert.match(ekoLas, /spridningsflödet/i);

  // Skydda känslighetsnyckeln (får aldrig döpas om)
  assert.match(ARBETSYTA_KALLTEXT, /kansighet_fore/);
});

// ------------------------------------------------------------------ 4. Numerisk CAD-måttinmatning & UI

test("CAD-mått: exakt SWEREF-geometri, kommateckenstöd, avvisning av L <= 0 / NaN samt integration med Ritverktyg & Linjeverktyg", async () => {
  const start = { x: 150250.5, y: 6581200.25 };
  const L = 12.5;

  // 0°, 90°, 180°, 45° inom 1e-9 m
  const p0 = beraknaPunktFranLangdOchBaring(start, L, 0);
  const p90 = beraknaPunktFranLangdOchBaring(start, L, 90);
  const p180 = beraknaPunktFranLangdOchBaring(start, L, 180);
  const p45 = beraknaPunktFranLangdOchBaring(start, L, 45);

  assert.ok(Math.hypot(p0.x - start.x, p0.y - (start.y + L)) < 1e-9);
  assert.ok(Math.hypot(p90.x - (start.x + L), p90.y - start.y) < 1e-9);
  assert.ok(Math.hypot(p180.x - start.x, p180.y - (start.y - L)) < 1e-9);
  assert.ok(Math.hypot(p45.x - (start.x + L / Math.SQRT2), p45.y - (start.y + L / Math.SQRT2)) < 1e-9);

  // Kommateckenstöd ("12,5") och avvisning av L <= 0 / NaN
  const tolkad = tolkaLangdOchBaring("12,5", "45");
  assert.equal(tolkad.ok, true);
  if (tolkad.ok) assert.equal(tolkad.varde.langd_m, 12.5);
  assert.equal(tolkaLangdOchBaring("0", "0").ok, false);
  assert.equal(tolkaLangdOchBaring("-1", "0").ok, false);
  assert.equal(tolkaLangdOchBaring("NaN", "0").ok, false);
  assert.equal(tolkaSwerefKoordinat("150250,5", "6581200,25").ok, true);
  assert.equal(tolkaSwerefKoordinat("150250,5", "NaN").ok, false);

  // End-to-end via tillampaCadInmatning + riktigt Ritverktyg + PlanDok
  const ar = await laddaArbetsytaExports();
  const dok = new PlanDok();
  const lagerLista: RitLager[] = [
    {
      lager_id: "hus",
      namn: "Nya hus",
      geometri: "polygon",
      roll: "byggnad",
      stil: {},
      synlig: true,
      last: false,
      tonad: false,
      opacitet: 1,
      kalla: "ritat",
      dataklass: "oppen",
      egenskaper_mall: {},
      egenskaper_forval: {},
    },
    {
      lager_id: "mur",
      namn: "Mur",
      geometri: "linje",
      roll: "brytlinje",
      stil: {},
      synlig: true,
      last: false,
      tonad: false,
      opacitet: 1,
      kalla: "ritat",
      dataklass: "oppen",
      egenskaper_mall: {},
      egenskaper_forval: {},
    },
  ];
  dok.sattLager(lagerLista);
  const rv = new Ritverktyg(dok, [], { meterPerPx: () => 0.5, meddela: () => {} });

  // Rita ett hus med exakt bas 12,5 m × 8,5 m
  rv.sattVerktyg("hus");
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "sweref", langdText: "", baringText: "", eText: "150 000,5", nText: "6 580 000,25" }).ok,
    true,
  );
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "12,5", baringText: "90", eText: "", nText: "" }).ok,
    true,
  );
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "8,5", baringText: "0", eText: "", nText: "" }).ok,
    true,
  );
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "12,5", baringText: "270", eText: "", nText: "" }).ok,
    true,
  );
  // Ogiltiga värden via formulärhjälparen
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "0", baringText: "90", eText: "", nText: "" }).ok,
    false,
  );
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "NaN", baringText: "90", eText: "", nText: "" }).ok,
    false,
  );
  assert.equal(rv.avsluta(), true);
  assert.equal(dok.ny.size, 1);

  // Rita en linje via samma CAD-inmatning (delegerar till Linjeverktyg)
  rv.sattVerktyg("linje");
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "sweref", langdText: "", baringText: "", eText: "150010", nText: "6580010" }).ok,
    true,
  );
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "10,0", baringText: "45", eText: "", nText: "" }).ok,
    true,
  );
  assert.equal(rv.linje.utkast.length, 2);
  assert.ok(Math.abs(rv.linje.utkast[1]!.x - (150010 + 10 / Math.SQRT2)) < 1e-9);
  assert.ok(Math.abs(rv.linje.utkast[1]!.y - (6580010 + 10 / Math.SQRT2)) < 1e-9);

  // Kontrollera cadMattHtml: hopfällbar, decimal-textfält (inte type="number"), knapp & XSS-skydd
  assert.equal(
    cadMattHtml({
      synlig: false,
      oppen: false,
      lage: "matt",
      antalPunkter: 0,
      senastePunkt: null,
      langdText: "",
      baringText: "",
      eText: "",
      nText: "",
      fel: null,
    }),
    "",
  );
  const htmlMatt = cadMattHtml({
    synlig: true,
    oppen: true,
    lage: "matt",
    antalPunkter: 1,
    senastePunkt: { x: 150000.5, y: 6580000.25 },
    langdText: "12,5",
    baringText: "90",
    eText: "",
    nText: "",
    fel: null,
  });
  assert.match(htmlMatt, /<details\b[^>]*id="ar-cad"/);
  assert.match(htmlMatt, /Längd \(m\)/);
  assert.match(htmlMatt, /Bäring \(°\)/);
  assert.match(htmlMatt, /id="ar-cad-langd"[^>]*type="text"[^>]*inputmode="decimal"/);
  assert.doesNotMatch(htmlMatt, /type="number"/);
  assert.match(htmlMatt, /Lägg till punkt/);

  const htmlSweref = cadMattHtml({
    synlig: true,
    oppen: true,
    lage: "sweref",
    antalPunkter: 0,
    senastePunkt: null,
    langdText: "",
    baringText: "",
    eText: "<script>x</script>",
    nText: "6580000",
    fel: "Ogiltigt <tal>",
  });
  assert.match(htmlSweref, /E \(SWEREF m\)/);
  assert.match(htmlSweref, /N \(SWEREF m\)/);
  assert.doesNotMatch(htmlSweref, /<script>x<\/script>/);
  assert.match(htmlSweref, /role="alert">Ogiltigt &lt;tal&gt;<\/p>/);

  // CSS-kontroll: svartvit krom + 44 px pekytor + fokusmarkering
  assert.match(ARBETSYTA_CSS, /\.ar-ordlista\b/);
  assert.match(ARBETSYTA_CSS, /\.ar-cad\b/);
  assert.match(ARBETSYTA_CSS, /\.ar-centrum-sikte\b/);
  assert.match(ARBETSYTA_CSS, /min-height:\s*var\(--f-mal-yta,\s*44px\)/);
});
