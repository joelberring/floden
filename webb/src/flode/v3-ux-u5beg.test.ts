/*
 * Körs med `npm test`. Deterministiska regressionstester för Våg 3 UX-lyft (U5b, U5e, U5g):
 * - U5b: Responsiva kontotabeller (.f-korttabell + data-etikett på varje <td> + @media (max-width: 720px) i fragor.css).
 * - U5e: Tillgänglig synlig statustext bredvid varje statusprick i rapport.ts och flode.css (aldrig enbart färg).
 * - U5g: Projektlistan visar organisationsnamn + roll + delningstillstånd och ersätter `floden demo`-tomtext
 *        med användarvägledning när användaren är inloggad eller i vanlig webbdrift.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import module from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { settKontoLage, tolkaKontoSvar } from "../konto.ts";
import {
  adminOrganisationHtml,
  adminProjektHtml,
  sidaAdministration,
  sidaNycklar,
  type McpAvsnitt,
} from "../kontosidor-html.ts";
import type { AdminOrganisationDetalj, AdminProjektDetalj, NycklarSvar } from "../types.ts";
import {
  projektMetaHtml,
  projektOrganisationNamn,
  projektRadHtml,
  rollRad,
  tomProjektlistaHtml,
} from "./projektlogik.ts";
import type { Prick, RapportFraga, StatusMarkorLage } from "./rapport.ts";

// Ladda rena exporter från rapport.ts i Node 24 utan att dra in kartapi.ts (som använder Vite-importer utan .ts).
let rapportExportsPromise: Promise<typeof import("./rapport.ts")> | null = null;
function laddaRapportExports(): Promise<typeof import("./rapport.ts")> {
  if (!rapportExportsPromise) {
    module.registerHooks({
      resolve(specifier, context, nextResolve) {
        if (specifier === "./fragor.ts" && context.parentURL?.endsWith("/flode/rapport.ts")) {
          return { shortCircuit: true, url: "stub:flode-fragor", format: "module" };
        }
        return nextResolve(specifier, context);
      },
      load(url, context, nextLoad) {
        if (url === "stub:flode-fragor") {
          return {
            shortCircuit: true,
            format: "module",
            source: `export const hamtaFragelista = async () => ({ fragor: [], kalla: "katalog", anmarkning: "" });`,
          };
        }
        return nextLoad(url, context);
      },
    });
    rapportExportsPromise = import("./rapport.ts");
  }
  return rapportExportsPromise;
}

const FLODE_MAPP = path.dirname(fileURLToPath(import.meta.url));
const SRC_MAPP = path.resolve(FLODE_MAPP, "..");
const FRAGOR_CSS = fs.readFileSync(path.join(SRC_MAPP, "fragor.css"), "utf-8");
const FLODE_CSS = fs.readFileSync(path.join(FLODE_MAPP, "flode.css"), "utf-8");
const RAPPORT_TS = fs.readFileSync(path.join(FLODE_MAPP, "rapport.ts"), "utf-8");
const PROJEKTLISTA_TS = fs.readFileSync(path.join(FLODE_MAPP, "projektlista.ts"), "utf-8");
const KONTOSIDOR_HTML_TS = fs.readFileSync(path.join(SRC_MAPP, "kontosidor-html.ts"), "utf-8");

const stripTags = (html: string): string =>
  html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();

const adminAnvandare = () =>
  tolkaKontoSvar(200, {
    inloggning: "pa",
    konto: { namn: "Anna Svensson", epost: "anna@kommun.se", admin: true, superadmin: false },
  });

const nycklarFixture: NycklarSvar = {
  paslagen: true,
  orsak: null,
  text: null,
  typer: [
    {
      typ: "lantmateriet",
      namn: "Lantmäteriet",
      beskrivning: "Höjd- och byggnadsdata.",
      falt: [{ id: "klient_id", namn: "Nyckel", krav: true, val: [] }],
      sparad: null,
      sparad_datum: null,
      slutar_pa: null,
      produkter: [
        {
          roll: "laser",
          namn: "Laserdata Nedladdning, skog",
          stac: "stac-hojd",
          anvands_till: "Markmodellen för skyfall och dagsljus.",
          behovs: true,
          anmarkning: "Kräver godkänd beställning.",
        },
        {
          roll: "byggnader",
          namn: "Byggnad Nedladdning, vektor",
          stac: "stac-vektor",
          anvands_till: "Byggnadernas fotavtryck.",
          behovs: false,
          anmarkning: "",
        },
      ],
    },
  ],
};

const mcpFixture: McpAvsnitt = {
  svar: {
    max: 5,
    mcp_url: "https://floden.berring.se/mcp",
    tokens: [
      {
        id: "t1",
        namn: "Kontorsdator",
        skapad_datum: "2026-11-20",
        senast_anvand_datum: "2026-11-26",
        slutar_pa: "abcd",
      },
    ],
  },
  ny: null,
  fel: null,
  appar: [
    {
      id: "app1",
      namn: "Claude Desktop",
      skapad_datum: "2026-11-21",
      senast_anvand_datum: null,
    },
  ],
};

const projektDetaljFixture: AdminProjektDetalj = {
  projekt: {
    id: "p-haga",
    namn: "Haga planprogram",
    organisation_id: "org-gbg",
    organisation: "Göteborgs stad",
    medlemmar: 2,
    oppna_inbjudningar: 1,
    organisation_synlig: true,
    kan_andra_delning: true,
  },
  roller: ["lasare", "utredare"],
  medlemmar: [
    { konto_id: "k1", namn: "Anna Svensson", epost: "anna@gbg.se", roll: "utredare", kalla: "projekt", sparrad: false },
    { konto_id: "k2", namn: "Bo Lind", epost: null, roll: "lasare", kalla: "inbjudan", sparrad: true },
  ],
  inbjudningar: [
    { id: "i1", epost: "ny@gbg.se", roll: "utredare", skapad: 1800000000, giltig_till: 1801209600, status: "oppen" },
  ],
};

const orgDetaljFixture: AdminOrganisationDetalj = {
  organisation: {
    id: "org-gbg",
    namn: "Göteborgs stad",
    din_roll: "agare",
    far_byta_namn: true,
    roller: ["medlem", "ledare", "organisationsadmin", "agare"],
    tillatna_roller: ["medlem", "ledare", "organisationsadmin", "agare"],
  },
  roller: ["medlem", "ledare", "organisationsadmin", "agare"],
  medlemmar: [
    { konto_id: "k1", namn: "Anna Svensson", epost: "anna@gbg.se", roll: "agare", kalla: "manuell", sparrad: false },
    { konto_id: "k2", namn: "Bo Lind", epost: "bo@gbg.se", roll: "ledare", kalla: "inbjudan", sparrad: false },
  ],
  inbjudningar: [
    { id: "oi1", epost: "kollega@gbg.se", roll: "ledare", skapad: 1800000000, giltig_till: 1801209600, status: "oppen" },
  ],
};

// ------------------------------------------------------------------ U5b: Responsiva kontotabeller

test("U5b: samtliga 7 kontotabeller har .f-korttabell och varje <td> har data-etikett som matchar kolumnrubrikerna", () => {
  settKontoLage(adminAnvandare());
  try {
    const htmlKallor = [
      { namn: "Nycklar (Geotorget + MCP + Anslutna appar)", html: sidaNycklar(nycklarFixture, null, mcpFixture), antalTabeller: 3 },
      { namn: "Administration (projekt + organisation)", html: sidaAdministration([projektDetaljFixture], null, "", [orgDetaljFixture]), antalTabeller: 4 },
      { namn: "adminProjektHtml direkt", html: adminProjektHtml(projektDetaljFixture), antalTabeller: 2 },
      { namn: "adminOrganisationHtml direkt", html: adminOrganisationHtml(orgDetaljFixture), antalTabeller: 2 },
    ];

    for (const { namn, html, antalTabeller } of htmlKallor) {
      const tabeller = [...html.matchAll(/<table\b([^>]*)>([\s\S]*?)<\/table>/g)];
      assert.equal(tabeller.length, antalTabeller, `${namn}: oväntat antal tabeller`);

      for (const [, attrs, kropp] of tabeller) {
        assert.match(attrs ?? "", /\bf-korttabell\b/, `${namn}: tabell saknar .f-korttabell`);
        const rubriker = [...(kropp ?? "").matchAll(/<th\b[^>]*>([\s\S]*?)<\/th>/g)].map((m) => stripTags(m[1] ?? ""));
        assert.ok(rubriker.length >= 3, `${namn}: tabell saknar kolumnrubriker`);

        const tbody = /<tbody>([\s\S]*?)<\/tbody>/.exec(kropp ?? "")?.[1] ?? "";
        const rader = [...tbody.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g)];
        assert.ok(rader.length > 0, `${namn}: tabell saknar datarader`);

        for (const [, radHtml] of rader) {
          const celler = [...(radHtml ?? "").matchAll(/<td\b([^>]*)>/g)];
          assert.equal(celler.length, rubriker.length, `${namn}: antal <td> matchar inte <th>`);
          for (let i = 0; i < celler.length; i++) {
            const etikett = /\bdata-etikett="([^"]*)"/.exec(celler[i]?.[1] ?? "")?.[1] ?? "";
            assert.ok(etikett.trim().length > 0, `${namn}: <td> kolumn ${i} saknar icke-tom data-etikett`);
            assert.equal(etikett, rubriker[i], `${namn}: data-etikett ska matcha kolumnrubrik ${rubriker[i]}`);
          }
        }
      }
    }

    // Källkodskontroll: ingen ny <table> i kontosidor-html.ts utan f-korttabell och ingen <td> utan data-etikett.
    const allaTabellerIKallkod = [...KONTOSIDOR_HTML_TS.matchAll(/<table\b([^>]*)>/g)];
    assert.equal(allaTabellerIKallkod.length, 7);
    for (const m of allaTabellerIKallkod) {
      assert.match(m[1] ?? "", /\bf-korttabell\b/);
    }
    const allaTdIKallkod = [...KONTOSIDOR_HTML_TS.matchAll(/<td\b([^>]*)>/g)];
    assert.ok(allaTdIKallkod.length > 0);
    for (const m of allaTdIKallkod) {
      assert.match(m[1] ?? "", /\bdata-etikett="[^"]+"/);
    }
  } finally {
    settKontoLage({ lage: "av" });
  }
});

test("U5b: fragor.css innehåller @media (max-width: 720px) för .f-korttabell med etikett, noll min-width och 44 px pekytor", () => {
  const mediaMatch = /@media\s*\(\s*max-width:\s*720px\s*\)\s*\{([\s\S]*)\}\s*$/.exec(FRAGOR_CSS);
  assert.ok(mediaMatch, "fragor.css saknar @media (max-width: 720px)-block för responsiva korttabeller");
  const mediaBlock = mediaMatch[1] ?? "";

  assert.match(mediaBlock, /\.f-korttabell\b/, "Regel för .f-korttabell saknas i @media (max-width: 720px)");
  assert.match(mediaBlock, /min-width:\s*0\s*;/, ".f-korttabell måste nollställa min-width för att undvika horisontell skroll");
  assert.match(mediaBlock, /\.f-korttabell\s+td::before/, "Etikett-pseudoelement .f-korttabell td::before saknas");
  assert.match(mediaBlock, /content:\s*attr\(\s*data-etikett\s*\)/, "td::before måste visa content: attr(data-etikett)");
  assert.match(mediaBlock, /min-height:\s*var\(--f-mal-yta,\s*44px\)/, "Knappar/fält i korttabell ska ha 44 px klickyta");
});

// ------------------------------------------------------------------ U5e: Synlig statustext bredvid färgprick

test("U5e: rapport.ts renderar synlig textetikett (ej enbart prick) för samtliga statuslägen", async () => {
  const { STATUS_MARKOR_INFO, statusMarkorHtml } = await laddaRapportExports();
  const forvantade: Array<[StatusMarkorLage, string]> = [
    ["gron", "Klar"],
    ["gul", "Fördjupa"],
    ["gra", "Underlag saknas"],
    ["klar", "Klar"],
    ["pagar", "Pågår"],
    ["kor", "Pågår"],
    ["koad", "Köad"],
    ["ko", "Köad"],
    ["kontrollera", "Kontrollera"],
    ["fel", "Fel"],
  ];

  for (const [lage, text] of forvantade) {
    assert.equal(STATUS_MARKOR_INFO[lage].etikett, text);
    const html = statusMarkorHtml(lage);
    assert.match(html, /class="flode-statusmarkor\b/, `${lage}: saknar .flode-statusmarkor`);
    assert.match(html, /role="status"/, `${lage}: saknar role="status"`);
    assert.match(html, /aria-label="[^"]+"/, `${lage}: saknar aria-label`);
    assert.match(html, /class="flode-prick [^"]+"[^>]*aria-hidden="true"/, `${lage}: dekorativ prick ska ha aria-hidden="true"`);
    assert.match(html, /class="flode-statusmarkor-text">[^<]+<\/span>/, `${lage}: saknar synlig statustext bredvid pricken`);
    assert.equal(stripTags(html), text, `${lage}: synlig text ska vara "${text}"`);
  }

  // XSS-escaping i anpassad beskrivning
  const xss = statusMarkorHtml("fel", 'Fel "<script>alert(1)</script>');
  assert.doesNotMatch(xss, /<script>/);
  assert.match(xss, /&lt;script&gt;/);
});

test("U5e: rapportens innehållsförteckning visar både prick och synlig textetikett för grön, gul och grå fråga", async () => {
  const { innehallRadHtml, PRICK_ETIKETT, PRICK_TEXT, prickFor } = await laddaRapportExports();
  const gorFraga = (id: string, fraga: string, fordjupning?: string): RapportFraga => ({
    id,
    fraga,
    pastaenden: fordjupning
      ? [
          {
            pastaende_id: `${id}-1`,
            status: "ok",
            status_text: "Belagd",
            fordjupning,
            fordjupning_text: "",
            delar: [{ text: "Test", tal: false }],
            belagg: [],
            forutsattning: null,
            saknat_underlag: [],
          },
        ]
      : [],
  });

  const fall: Array<[RapportFraga, Prick]> = [
    [gorFraga("SK-01", "Maxdjup vid 100-årsregn?", "nej"), "gron"],
    [gorFraga("SK-02", "Byggnader med risk?", "ja"), "gul"],
    [gorFraga("SK-03", "Ledningsnätets kapacitet?", "kan_inte_avgoras"), "gra"],
    [gorFraga("SK-04", "Saknar påstående?", undefined), "gra"],
  ];

  for (const [fraga, forvantadPrick] of fall) {
    assert.equal(prickFor(fraga), forvantadPrick);
    const radHtml = innehallRadHtml(fraga);
    assert.match(radHtml, new RegExp(`class="flode-prick ${forvantadPrick}"`));
    assert.match(radHtml, new RegExp(`class="flode-statusmarkor-text">${PRICK_ETIKETT[forvantadPrick]}</span>`));
    assert.match(radHtml, new RegExp(PRICK_TEXT[forvantadPrick]));
    assert.ok(stripTags(radHtml).includes(PRICK_ETIKETT[forvantadPrick]));
    assert.ok(stripTags(radHtml).includes(fraga.id));
  }

  // Kontrollera att rapport.ts aldrig längre ritar en ensam .flode-prick utan statusMarkorHtml
  const prickFalt = RAPPORT_TS.match(/class="flode-prick/g) ?? [];
  assert.equal(prickFalt.length, 1, "Alla .flode-prick i rapport.ts ska renderas via statusMarkorHtml");
  assert.match(RAPPORT_TS, /r\.fragor\.map\(innehallRadHtml\)/);

  // Kontrollera att flode.css har stilar för statusmarkören och samtliga pricklägen (inkl. fel)
  assert.match(FLODE_CSS, /\.flode-statusmarkor\b/);
  assert.match(FLODE_CSS, /\.flode-prick\.gron\b/);
  assert.match(FLODE_CSS, /\.flode-prick\.gul\b/);
  assert.match(FLODE_CSS, /\.flode-prick\.gra\b/);
  assert.match(FLODE_CSS, /\.flode-prick\.fel\b/);
});

// ------------------------------------------------------------------ U5g: Projektlistan org + roll + inloggad tomtext

test("U5g: projektkort/rad visar organisationsnamn, roll och delningstillstånd samt maskerar HTML", () => {
  assert.equal(projektOrganisationNamn({ organisation_namn: "Stockholms stad" }), "Stockholms stad");
  assert.equal(projektOrganisationNamn({ organisation: "Malmö stad" }), "Malmö stad");
  assert.equal(projektOrganisationNamn({ organisation_namn: "Primär", organisation: "Fallback" }), "Primär");
  assert.equal(projektOrganisationNamn({}), null);

  assert.equal(
    rollRad({ organisation_namn: "Stockholms stad", roll: "utredare", organisation_synlig: true }),
    "Stockholms stad · Din roll: utredare · delat med organisationen",
  );
  assert.equal(
    rollRad({ organisation_namn: "Stockholms stad", roll: "lasare", organisation_synlig: false }),
    "Stockholms stad · Din roll: läsare · privat",
  );

  const metaHtml = projektMetaHtml({
    organisation_namn: "Stockholms stad <script>",
    roll: "agare",
    organisation_synlig: true,
  });
  assert.doesNotMatch(metaHtml, /<script>/);
  assert.match(metaHtml, /class="flode-projekt-org">Stockholms stad &lt;script&gt;<\/span>/);
  assert.match(metaHtml, /Din roll: ägare · delat med organisationen/);

  const radHtml = projektRadHtml({
    id: "p-hagersten",
    namn: "Hägersten-Älvsjö",
    undertitel: "Geografiprojekt",
    paahittat: false,
    organisation_namn: "Stockholms stad",
    roll: "lasare",
    organisation_synlig: true,
    statusText: "Skyfall och sol beräknade",
    href: "#/p/p-hagersten/karta",
    vald: false,
    miniatyr: null,
  });
  const radText = stripTags(radHtml);
  assert.ok(radText.includes("Hägersten-Älvsjö"));
  assert.ok(radText.includes("Stockholms stad"));
  assert.ok(radText.includes("Din roll: läsare"));
  assert.ok(radText.includes("delat med organisationen"));
  assert.ok(radText.includes("Visa på kartan"));

  // Utan metadata renderas ingen tom meta-rad
  assert.equal(projektMetaHtml({}), "");
  const utanMeta = projektRadHtml({
    id: "demo",
    namn: "Demokvarteret",
    undertitel: "Exempel",
    paahittat: true,
    statusText: "Skyfall beräknade",
    href: "#/p/demo/karta",
    vald: true,
    miniatyr: null,
  });
  assert.doesNotMatch(utanMeta, /data-projekt-meta/);
  assert.match(utanMeta, /aria-current="true"/);
});

test("U5g: inloggad och vanlig webbdrift visar vägledning till Nytt projekt (aldrig floden demo)", () => {
  for (const lage of ["in", "okand"] as const) {
    const html = tomProjektlistaHtml(lage, false, "#/nytt");
    assert.doesNotMatch(html, /floden demo/i, `läge=${lage} får inte visa floden demo`);
    assert.doesNotMatch(html, /<code>/i, `läge=${lage} får inte visa CLI-kodblock`);
    assert.match(html, /Nytt projekt/, `läge=${lage} ska vägleda till Nytt projekt`);
    assert.match(html, /href="#\/nytt"/, `läge=${lage} ska länka till #/nytt`);
    assert.match(html, /administratör/i, `läge=${lage} ska nämna administratör för inbjudan`);
  }

  const utloggad = tomProjektlistaHtml("ut", false, "#/nytt");
  assert.doesNotMatch(utloggad, /floden demo/i);
  assert.match(utloggad, /Logga in/i);

  const arkiverade = tomProjektlistaHtml("in", true, "#/nytt");
  assert.doesNotMatch(arkiverade, /floden demo/i);
  assert.match(arkiverade, /arkiverade projekt/i);

  // Lokalt ej-inloggat demoläge (lage === "av") är enda läget som får visa floden demo-tips
  const lokal = tomProjektlistaHtml("av", false, "#/nytt");
  assert.match(lokal, /lokalt demoläge/i);
  assert.match(lokal, /<code>floden demo<\/code>/);

  // Källkodskontroll: projektlista.ts använder tomProjektlistaHtml + projektRadHtml och saknar hårdkodad floden demo
  assert.doesNotMatch(PROJEKTLISTA_TS, /floden demo/i);
  assert.match(PROJEKTLISTA_TS, /tomProjektlistaHtml\(/);
  assert.match(PROJEKTLISTA_TS, /projektRadHtml\(/);
});
