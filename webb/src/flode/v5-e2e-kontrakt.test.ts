/*
 * Körs med `npm test`. Korsvågs-kontraktstest för Våg 5 (binder ihop frontend-kontrakt från Våg 2–4):
 * 1. `GET /api/analys/ekologi` URL-bygge (`ekologiAnalysUrl` + `hamtaEkologiAnalys` utan POST/CSRF).
 * 2. Steg 3 `byggRapportJobbKropp` för Luft (extern CSV + bakgrundshalt) + `SLAGTEXT["lage_idag"]`.
 * 3. U5b/U5e/U5g-rendering (responsiv `.f-korttabell` med `data-etikett`, synlig statustext bredvid
 *    statusprick, organisationsnamn/roll/delning samt inloggad tomtext utan `floden demo`).
 * 4. U5c/U5d-ordlista + `aria-live="polite"` på `#ar-avl` och tangentbordsavläsning i kartcentrum.
 * 5. CAD-bäring och SWEREF 99 18 00-beräkning (0°, 90°, 180°, 45°, decimalkomma, avvisning av L <= 0/NaN
 *    samt integration med `Ritverktyg`/`PlanDok`).
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import module from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { byggRapportJobbKropp, ekologiAnalysUrl, hamtaEkologiAnalys } from "../api.ts";
import type { AvlasningSvar } from "../fragor-typer.ts";
import {
  beraknaPunktFranLangdOchBaring,
  tolkaLangdOchBaring,
  tolkaSwerefKoordinat,
} from "../karta/linjeverktyg.ts";
import { PlanDok } from "../karta/plandok.ts";
import type { RitLager } from "../karta/ritlager.ts";
import { Ritverktyg } from "../karta/ritverktyg.ts";
import { settKontoLage, tolkaKontoSvar } from "../konto.ts";
import { sidaAdministration, sidaNycklar, type McpAvsnitt } from "../kontosidor-html.ts";
import { swerefTillWgs84 } from "../sweref.ts";
import type { AdminOrganisationDetalj, AdminProjektDetalj, NycklarSvar, RapportAmne } from "../types.ts";
import { cadMattHtml, ORDLISTA_POSTER, ordlistaHtml } from "./panelhtml.ts";
import { projektRadHtml, rollRad, tomProjektlistaHtml } from "./projektlogik.ts";
import type { RapportFraga } from "./rapport.ts";
import {
  byggRapportValForSkapa,
  SLAGTEXT,
  underlagHtml,
  valjUnderlag,
  type LuftFormTillstand,
} from "./rapportskapa.ts";
import { FORVAL_BESTALLNING } from "./tillstand.ts";

const FLODE_MAPP = path.dirname(fileURLToPath(import.meta.url));
const SRC_MAPP = path.resolve(FLODE_MAPP, "..");
const ARBETSYTA_KALLTEXT = fs.readFileSync(path.join(FLODE_MAPP, "arbetsyta.ts"), "utf-8");
const FRAGOR_CSS = fs.readFileSync(path.join(SRC_MAPP, "fragor.css"), "utf-8");

const stripTags = (html: string): string =>
  html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();

// Gemensam minimal hook-laddare för Node 24 så vi kan testa rena exporter ur arbetsyta.ts och rapport.ts
// utan WebGL-/Vite-importer.
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
export const hamtaFragelista = async () => ({ fragor: [], kalla: "katalog", anmarkning: "" });
`;

let hooksRegistrerade = false;
function sakerstallNodeHooks(): void {
  if (hooksRegistrerade) return;
  hooksRegistrerade = true;
  module.registerHooks({
    resolve(specifier, context, nextResolve) {
      if (specifier === "maplibre-gl") {
        return { shortCircuit: true, url: "stub:maplibre-gl", format: "module" };
      }
      if (specifier === "./fragor.ts" && context.parentURL?.endsWith("/flode/rapport.ts")) {
        return { shortCircuit: true, url: "stub:flode-fragor", format: "module" };
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
}

// ------------------------------------------------------------------ 1. GET /api/analys/ekologi

test("V5 kontrakt 1: GET /api/analys/ekologi bygger query-parametrar (omrade_id, scenario, natverk) utan POST eller CSRF", async () => {
  const url = ekologiAnalysUrl({
    omrade_id: "v5-kvarter",
    scenario: "nytt-hus",
    natverk: "barrskog-nacka",
  });
  const parsad = new URL(url, "http://localhost");
  assert.equal(parsad.pathname, "/api/analys/ekologi");
  assert.equal(parsad.searchParams.get("omrade_id"), "v5-kvarter");
  assert.equal(parsad.searchParams.get("scenario"), "nytt-hus");
  assert.equal(parsad.searchParams.get("natverk"), "barrskog-nacka");

  const gammalFetch = globalThis.fetch;
  const gammalDoc = (globalThis as { document?: unknown }).document;
  let anropadUrl = "";
  let anropadInit: RequestInit | undefined;
  (globalThis as { document?: unknown }).document = { cookie: "floden_csrf=test-csrf-v5" };
  globalThis.fetch = (async (u: string | URL | Request, init?: RequestInit) => {
    anropadUrl = String(u);
    anropadInit = init;
    return new Response(JSON.stringify({ format: "floden-ekologi/v1", fore: { ECA_m2: 95000 } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const svar = await hamtaEkologiAnalys<{ format: string; fore: { ECA_m2: number } }>({
      omrade_id: "v5-kvarter",
      natverk: "barrskog-nacka",
    });
    assert.equal(svar.format, "floden-ekologi/v1");
    assert.equal(svar.fore.ECA_m2, 95000);
  } finally {
    globalThis.fetch = gammalFetch;
    if (gammalDoc === undefined) delete (globalThis as { document?: unknown }).document;
    else (globalThis as { document?: unknown }).document = gammalDoc;
  }

  assert.equal(anropadUrl, "/api/analys/ekologi?omrade_id=v5-kvarter&natverk=barrskog-nacka");
  assert.equal((anropadInit?.method ?? "GET").toUpperCase(), "GET");
  assert.equal(anropadInit?.body, undefined);
  assert.equal(((anropadInit?.headers ?? {}) as Record<string, string>)["X-CSRF-Token"], undefined);
});

// ------------------------------------------------------------------ 2. Steg 3 Luft + lage_idag

test("V5 kontrakt 2: Steg 3 byggRapportJobbKropp för Luft (CSV + bakgrundshalt) och SLAGTEXT['lage_idag']", () => {
  // Läget idag-etikett i Steg 3 (aldrig undefined, pekar på steg 2)
  assert.equal(SLAGTEXT.lage_idag, "Läget idag");
  const skyfallAmne: RapportAmne = {
    id: "skyfall",
    namn: "Skyfall",
    kan_skapas: true,
    forklaring: null,
    mall: true,
    forval_korning: "k-v5-idag",
    forval_slag: "lage_idag",
    korningar: [
      { korning_id: "k-v5-idag", fraga_id: "v5-skyfall", skapad: "2026-11-26", slag: "lage_idag", scenario: null },
    ],
  };
  const uSky = valjUnderlag(skyfallAmne, null, null, null);
  assert.equal(uSky.slag, "lage_idag");
  assert.equal(uSky.korningText, "Körning k-v5-idag: Läget idag.");
  const skyHtml = underlagHtml("v5-kvarter", uSky);
  assert.match(skyHtml, /steg 2 \(Karta &amp; analys\)/);
  assert.doesNotMatch(skyHtml, /undefined|steg 3|\/fragor/i);

  // Luft Steg 3 payload med extern CSV före/efter och kommatecken-bakgrundshalt
  const luftAmne: RapportAmne = {
    id: "luft",
    namn: "Luft",
    kan_skapas: true,
    forklaring: null,
    mall: true,
    forval_korning: null,
    korningar: [],
  };
  const luftForm: LuftFormTillstand = {
    anvandNySerie: true,
    serie_fore_csv: "tid_s,halt_ug_m3\n0,14.2\n3600,18.4",
    serie_efter_csv: "tid_s,halt_ug_m3\n0,12.1\n3600,16.0",
    bakgrund_text: "12,5",
  };
  const uLuft = valjUnderlag(luftAmne, null, null, null, true);
  const valRes = byggRapportValForSkapa(luftAmne, uLuft, luftForm);
  assert.equal(valRes.ok, true);
  if (!valRes.ok) throw new Error("oväntat valideringsfel");

  const kropp = byggRapportJobbKropp(
    "luft",
    {
      syfte: FORVAL_BESTALLNING.syfte,
      skede: FORVAL_BESTALLNING.skede,
      mottagare_roll: FORVAL_BESTALLNING.mottagare,
    },
    valRes.val,
  );
  assert.equal(kropp.amne, "luft");
  assert.deepEqual(kropp.amnen, ["luft"]);
  assert.equal(kropp.korning_id, undefined);
  assert.deepEqual(kropp.luft, {
    serie_fore_csv: "tid_s,halt_ug_m3\n0,14.2\n3600,18.4",
    serie_efter_csv: "tid_s,halt_ug_m3\n0,12.1\n3600,16.0",
    bakgrund_ug_m3: 12.5,
  });
});

// ------------------------------------------------------------------ 3. U5b / U5e / U5g

test("V5 kontrakt 3: U5b korttabell, U5e synlig statustext och U5g org/roll/tomtext", async () => {
  // U5b: Kontosidor renderar .f-korttabell med data-etikett på varje <td> + 720px-regel i CSS
  settKontoLage(
    tolkaKontoSvar(200, {
      inloggning: "pa",
      konto: { namn: "Anna Utredare", epost: "anna@example.test", admin: true, superadmin: false },
    }),
  );
  try {
    const nycklar: NycklarSvar = {
      paslagen: true,
      orsak: null,
      text: null,
      typer: [
        {
          typ: "lantmateriet",
          namn: "Lantmäteriet",
          beskrivning: "Höjddata",
          falt: [{ id: "klient_id", namn: "Nyckel", krav: true, val: [] }],
          sparad: null,
          sparad_datum: null,
          slutar_pa: null,
          produkter: [
            {
              roll: "laser",
              namn: "Laserdata",
              stac: "stac-hojd",
              anvands_till: "Markmodell",
              behovs: true,
              anmarkning: "",
            },
          ],
        },
      ],
    };
    const mcp: McpAvsnitt = {
      svar: {
        max: 5,
        mcp_url: "https://floden.test/mcp",
        tokens: [
          {
            id: "t1",
            namn: "Arbetsdator",
            skapad_datum: "2026-11-20",
            senast_anvand_datum: "2026-11-26",
            slutar_pa: "99ff",
          },
        ],
      },
      ny: null,
      fel: null,
      appar: [
        { id: "a1", namn: "Claude Desktop", skapad_datum: "2026-11-22", senast_anvand_datum: null },
      ],
    };
    const proj: AdminProjektDetalj = {
      projekt: {
        id: "p-v5",
        namn: "V5 Kvarter",
        organisation_id: "org-1",
        organisation: "Exempelstad",
        medlemmar: 1,
        oppna_inbjudningar: 1,
        organisation_synlig: true,
        kan_andra_delning: true,
      },
      roller: ["lasare", "utredare"],
      medlemmar: [
        { konto_id: "k1", namn: "Anna Utredare", epost: "anna@example.test", roll: "utredare", kalla: "projekt", sparrad: false },
      ],
      inbjudningar: [
        { id: "i1", epost: "bo@example.test", roll: "lasare", skapad: 1800000000, giltig_till: 1801209600, status: "oppen" },
      ],
    };
    const org: AdminOrganisationDetalj = {
      organisation: {
        id: "org-1",
        namn: "Exempelstad",
        din_roll: "agare",
        far_byta_namn: true,
        roller: ["medlem", "organisationsadmin", "agare"],
        tillatna_roller: ["medlem", "organisationsadmin", "agare"],
      },
      roller: ["medlem", "organisationsadmin", "agare"],
      medlemmar: [
        { konto_id: "k1", namn: "Anna Utredare", epost: "anna@example.test", roll: "agare", kalla: "manuell", sparrad: false },
      ],
      inbjudningar: [
        { id: "oi1", epost: "ny@example.test", roll: "medlem", skapad: 1800000000, giltig_till: 1801209600, status: "oppen" },
      ],
    };

    const kombiHtml = sidaNycklar(nycklar, null, mcp) + sidaAdministration([proj], null, "", [org]);
    const tabeller = [...kombiHtml.matchAll(/<table\b([^>]*)>([\s\S]*?)<\/table>/g)];
    assert.equal(tabeller.length, 7);
    for (const [, attrs, kropp] of tabeller) {
      assert.match(attrs ?? "", /\bf-korttabell\b/);
      const tdLista = [...(kropp ?? "").matchAll(/<td\b([^>]*)>/g)];
      assert.ok(tdLista.length > 0);
      for (const td of tdLista) {
        assert.match(td[1] ?? "", /\bdata-etikett="[^"]+"/);
      }
    }
    assert.match(FRAGOR_CSS, /@media\s*\(\s*max-width:\s*720px\s*\)/);
    assert.match(FRAGOR_CSS, /content:\s*attr\(\s*data-etikett\s*\)/);
  } finally {
    settKontoLage({ lage: "av" });
  }

  // U5e: Synlig statustext bredvid varje färgprick i rapport.ts
  sakerstallNodeHooks();
  const rapport = await import("./rapport.ts");
  const fragaGul: RapportFraga = {
    id: "SK-02",
    fraga: "Berörs byggnader?",
    pastaenden: [
      {
        pastaende_id: "SK-02-1",
        status: "ok",
        status_text: "Belagd",
        fordjupning: "ja",
        fordjupning_text: "",
        delar: [{ text: "2 byggnader berörs", tal: false }],
        belagg: [],
        forutsattning: null,
        saknat_underlag: [],
      },
    ],
  };
  const radHtml = rapport.innehallRadHtml(fragaGul);
  assert.match(radHtml, /class="flode-prick gul"[^>]*aria-hidden="true"/);
  assert.match(radHtml, /class="flode-statusmarkor-text">Fördjupa<\/span>/);
  assert.equal(stripTags(rapport.statusMarkorHtml("fel")), "Fel");

  // U5g: Projektkort med org + roll + delning samt inloggad tomtext utan `floden demo`
  assert.equal(
    rollRad({ organisation_namn: "Göteborgs stad", roll: "lasare", organisation_synlig: true }),
    "Göteborgs stad · Din roll: läsare · delat med organisationen",
  );
  const kortHtml = projektRadHtml({
    id: "p-v5",
    namn: "Haga E2E",
    undertitel: "Geografiprojekt",
    paahittat: false,
    organisation_namn: "Göteborgs stad",
    roll: "utredare",
    organisation_synlig: false,
    statusText: "Skyfall beräknade",
    href: "#/p/p-v5/karta",
    vald: false,
    miniatyr: null,
  });
  const kortText = stripTags(kortHtml);
  assert.ok(kortText.includes("Göteborgs stad"));
  assert.ok(kortText.includes("Din roll: utredare"));
  assert.ok(kortText.includes("privat"));

  const tomIn = tomProjektlistaHtml("in", false, "#/nytt");
  assert.doesNotMatch(tomIn, /floden demo/i);
  assert.match(tomIn, /Nytt projekt/);
  assert.match(tomIn, /href="#\/nytt"/);
});

// ------------------------------------------------------------------ 4. U5c / U5d

test("V5 kontrakt 4: U5c aria-live + tangentbordsavläsning och U5d hopfällbar ordlista", async () => {
  assert.match(ARBETSYTA_KALLTEXT, /id="ar-avl"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(ARBETSYTA_KALLTEXT, /id="ar-las-centrum"[^>]*>Läs av kartcentrum<\/button>/);

  const ordStangd = ordlistaHtml(false);
  assert.match(ordStangd, /<details\b[^>]*id="ar-ordlista"/);
  assert.doesNotMatch(ordStangd, /<details\b[^>]*\bopen\b/);
  assert.ok(ORDLISTA_POSTER.length >= 8);
  for (const term of ["RH 2000", "SWEREF 99 18 00", "SVF", "VSC", "ECA", "dPC", "MKN", "NEN 8100", "Skiss"]) {
    assert.ok(ordStangd.includes(term), `Ordlistan saknar ${term}`);
  }

  sakerstallNodeHooks();
  const ar = await import("./arbetsyta.ts");
  assert.equal(ar.arKartcentrumKortkommando({ key: "i", code: "KeyI", altKey: true, ctrlKey: false, metaKey: false }), true);
  assert.equal(ar.arKartcentrumKortkommando({ key: "i", code: "KeyI", altKey: false, ctrlKey: false, metaKey: false }), false);

  const [lng0, lat0] = swerefTillWgs84(150500, 6580050);
  let vy = { lng: lng0, lat: lat0, zoom: 17 };
  const avlEl = { hidden: true, innerHTML: "" };
  const styrning = ar.skapaTangentbordsAvlasning({
    hamtaVy: () => vy,
    settVy: (ny) => {
      vy = ny;
    },
    meterPerPx: () => 0.5,
    lasAv: (e, n) => {
      const svar: AvlasningSvar = {
        e,
        n,
        analys: "dagsljus",
        roll: "svf",
        varden: [
          {
            lager_id: "dagsljus.bas.svf",
            namn: "SVF",
            etikett: "SVF",
            typ: "bas",
            scenario: "bas",
            enhet: "",
            inom: true,
            tak: false,
            varde: 0.72,
          },
        ],
      };
      ar.renderaAvlasningElement(avlEl, {
        senasteAvl: svar,
        vy: { fore_lager: "dagsljus.bas.svf", efter_lager: null, skillnad_lager: "dagsljus.bas.svf", lage_idag: true },
        lage: "nulage",
        amne: "dagsljus",
        visaTak: false,
        treDText: null,
        hojdText: "Mark +10,5 m",
        punktEtikett: ar.formateraKartcentrumEtikett(e, n),
      });
    },
  });

  const c0 = styrning.lasCentrum();
  assert.ok(c0 && Math.abs(c0.e - 150500) < 0.05 && Math.abs(c0.n - 6580050) < 0.05);
  assert.equal(avlEl.hidden, false);
  assert.match(avlEl.innerHTML, /Kartcentrum/);
  assert.match(avlEl.innerHTML, /0,72/);
  assert.doesNotMatch(avlEl.innerHTML, /vattendjup|torr mark/i);

  const cNorr = styrning.stegPil("ArrowUp");
  assert.ok(cNorr && cNorr.n > c0.n + 20 && Math.abs(cNorr.e - c0.e) < 0.05);
});

// ------------------------------------------------------------------ 5. CAD-bäring och SWEREF

test("V5 kontrakt 5: CAD geodetisk bäring (0°=norr, 90°=öst) och SWEREF-inmatning i Ritverktyg", async () => {
  const start = { x: 150400.25, y: 6580010.5 };
  const L = 12.5;
  const p0 = beraknaPunktFranLangdOchBaring(start, L, 0);
  const p90 = beraknaPunktFranLangdOchBaring(start, L, 90);
  const p180 = beraknaPunktFranLangdOchBaring(start, L, 180);
  const p45 = beraknaPunktFranLangdOchBaring(start, L, 45);

  assert.ok(Math.hypot(p0.x - start.x, p0.y - (start.y + L)) < 1e-9);
  assert.ok(Math.hypot(p90.x - (start.x + L), p90.y - start.y) < 1e-9);
  assert.ok(Math.hypot(p180.x - start.x, p180.y - (start.y - L)) < 1e-9);
  assert.ok(Math.hypot(p45.x - (start.x + L / Math.SQRT2), p45.y - (start.y + L / Math.SQRT2)) < 1e-9);

  assert.equal(tolkaLangdOchBaring("12,5", "90").ok, true);
  assert.equal(tolkaLangdOchBaring("0", "90").ok, false);
  assert.equal(tolkaLangdOchBaring("-5", "90").ok, false);
  assert.equal(tolkaLangdOchBaring("NaN", "90").ok, false);
  assert.equal(tolkaSwerefKoordinat("150400,25", "6580010,5").ok, true);
  assert.equal(tolkaSwerefKoordinat("150400,25", "NaN").ok, false);

  sakerstallNodeHooks();
  const ar = await import("./arbetsyta.ts");
  const dok = new PlanDok();
  const lager: RitLager[] = [
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
  ];
  dok.sattLager(lager);
  const rv = new Ritverktyg(dok, [], { meterPerPx: () => 0.5, meddela: () => {} });
  rv.sattVerktyg("hus");

  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "sweref", langdText: "", baringText: "", eText: "150400,25", nText: "6580010,5" }).ok,
    true,
  );
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "12,5", baringText: "90", eText: "", nText: "" }).ok,
    true,
  );
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "8,0", baringText: "0", eText: "", nText: "" }).ok,
    true,
  );
  assert.equal(
    ar.tillampaCadInmatning(rv, { lage: "matt", langdText: "12,5", baringText: "270", eText: "", nText: "" }).ok,
    true,
  );
  assert.equal(rv.avsluta(), true);
  assert.equal(dok.ny.size, 1);

  const cadHtml = cadMattHtml({
    synlig: true,
    oppen: true,
    lage: "matt",
    antalPunkter: 1,
    senastePunkt: start,
    langdText: "12,5",
    baringText: "90",
    eText: "",
    nText: "",
    fel: null,
  });
  assert.match(cadHtml, /id="ar-cad-langd"[^>]*type="text"[^>]*inputmode="decimal"/);
  assert.doesNotMatch(cadHtml, /type="number"/);
});
