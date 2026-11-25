import assert from "node:assert/strict";
import { test } from "node:test";
import { adminTillaten, arbetsbankTillaten, settKontoLage, tolkaKontoSvar } from "./konto.ts";
import { flodeHuvudHtml, type SkalSida } from "./flode/huvud.ts";
import { rollRad } from "./flode/projektlogik.ts";
import {
  adminOrganisationHtml,
  adminProjektHtml,
  mcpKommando,
  mcpKonfig,
  nyckelStatusText,
  sidaAdministration,
  sidaNycklar,
  skapaNycklarStyrning,
} from "./kontosidor-html.ts";
import type { McpAvsnitt, NycklarApi } from "./kontosidor-html.ts";
import { sidaOmFloden } from "./om/sida.ts";
import { sidaOm } from "./sidor.ts";
import type {
  AdminOrganisationDetalj,
  AdminProjektDetalj,
  McpTokenSvar,
  ModulInfo,
  NycklarSvar,
  NyMcpToken,
  OauthAnslutning,
  OmradeSammanfattning,
} from "./types.ts";

const text = (html: string): string => html.replace(/<[^>]*>/g, " ").replace(/&[a-z#0-9]+;/g, " ");
const avkoda = (html: string): string =>
  html.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#39;/g, "'").replace(/&amp;/g, "&");
const moduler: ModulInfo[] = [];
const kontrollfall = [{ omrade_id: "A1_x", kod: "A1", namn: "Enkel sänka", grupp: "A", beskrivning: "Provfall." }] as unknown as OmradeSammanfattning[];

const anvandare = (extra: Record<string, unknown> = {}) =>
  tolkaKontoSvar(200, { inloggning: "pa", konto: { namn: "Anna Svensson", epost: "a@x.se", ...extra } });

test("arbetsbänken visas bara för superadmin, eller alltid när inloggning är av", () => {
  assert.equal(arbetsbankTillaten({ lage: "av" }), true);
  assert.equal(arbetsbankTillaten({ lage: "ut" }), false);
  assert.equal(arbetsbankTillaten({ lage: "okand" }), false);
  assert.equal(arbetsbankTillaten(anvandare()), false);
  assert.equal(arbetsbankTillaten(anvandare({ admin: true })), false);
  assert.equal(arbetsbankTillaten(anvandare({ superadmin: true, admin: true })), true);
});

test("administration för superadmin och organisationsadmin, inte för andra", () => {
  assert.equal(adminTillaten({ lage: "av" }), false);
  assert.equal(adminTillaten(anvandare()), false);
  assert.equal(adminTillaten(anvandare({ admin: true })), true);
  assert.equal(adminTillaten(anvandare({ superadmin: true })), true); // superadmin är alltid admin
});

test("sidhuvudets kontomeny: länk till Nycklar, och Administration bara för admin", () => {
  settKontoLage({ lage: "av" });
  assert.doesNotMatch(flodeHuvudHtml(null, null), /Nycklar|Administration/);
  settKontoLage(anvandare());
  assert.match(flodeHuvudHtml(null, null), /href="#\/nycklar"[^>]*>Nycklar</);
  assert.doesNotMatch(flodeHuvudHtml(null, null), /Administration/);
  settKontoLage(anvandare({ admin: true }));
  assert.match(flodeHuvudHtml(null, null), /href="#\/administration"[^>]*>Administration</);
  settKontoLage({ lage: "av" });
});

test("arbetsbänken finns varken i menyn eller på sidan Om beräkningarna", () => {
  for (const lage of [{ lage: "av" } as const, anvandare({ superadmin: true, admin: true })]) {
    settKontoLage(lage);
    for (const s of [null, "fragor", "om", "nycklar", "administration"] as Array<SkalSida | null>) {
      assert.doesNotMatch(flodeHuvudHtml(null, null, s), /arbetsb/i, String(s));
    }
    assert.doesNotMatch(sidaOm(moduler, kontrollfall), /arbetsb/i);
    assert.match(text(sidaOm(moduler, kontrollfall)), /Enkel sänka/); // kontrollfallen står kvar som text
    assert.doesNotMatch(sidaOmFloden(moduler, kontrollfall), /arbetsb/i);
  }
  settKontoLage({ lage: "av" });
});

const nycklar = (paslagen: boolean): NycklarSvar => ({
  paslagen,
  orsak: paslagen ? null : "saknas",
  text: paslagen ? null : "Nyckelhanteringen är inte påslagen på servern: FLODEN_NYCKELHEMLIGHET saknas.",
  typer: [
    { typ: "anthropic", namn: "Anthropic (Claude)", beskrivning: "API-nyckel.", falt: [{ id: "nyckel", namn: "API-nyckel", krav: true, val: [] }], sparad: 1800000000, sparad_datum: "2027-01-15", slutar_pa: "abcd" },
    { typ: "lantmateriet", namn: "Lantmäteriet", beskrivning: "Nyckel och hemlighet.", falt: [
      { id: "klient_id", namn: "Nyckel", krav: true, val: [] }, { id: "klient_hemlighet", namn: "Hemlighet", krav: true, val: [] },
      { id: "metod", namn: "Sätt att logga in", krav: false, val: ["basic", "oauth2"], standard: "basic", val_etiketter: { basic: "Geotorgets användarnamn och lösenord (privatperson)", oauth2: "API-portalens nyckel och hemlighet" } }],
      sparad: null, sparad_datum: null, slutar_pa: null,
      produkter: [
        { roll: "laser", namn: "Laserdata Nedladdning, skog", stac: "stac-hojd", anvands_till: "Markmodellen för skyfall och dagsljus.", behovs: true, anmarkning: "" },
        { roll: "byggnader", namn: "Byggnad Nedladdning, vektor", stac: "stac-vektor", anvands_till: "Byggnadernas fotavtryck.", behovs: false, anmarkning: "" },
      ],
      bestallning_text: "En beställning på Geotorget kan ta tid, ibland dagar, innan produkten är aktiv. Tills dess svarar Lantmäteriet 401 eller 403.",
      geotorget_url: "https://www.geotorget.lantmateriet.se" },
  ],
});

test("nyckelsidan visar bara datum och de fyra sista tecknen, aldrig ett värde", () => {
  settKontoLage(anvandare());
  const html = sidaNycklar(nycklar(true));
  assert.equal(nyckelStatusText(nycklar(true).typer[0]!), "Sparad 2027-01-15, slutar på …abcd");
  assert.match(text(html), /Sparad 2027-01-15, slutar på …abcd/);
  assert.match(text(html), /Ingen nyckel sparad/);
  assert.match(html, /Ersätt nyckeln/);
  assert.match(html, /data-ta-bort-nyckel="anthropic"/);
  assert.doesNotMatch(html, /data-ta-bort-nyckel="lantmateriet"/); // inget att ta bort
  // Fälten är lösenordsfält och har aldrig ett ifyllt värde.
  assert.match(html, /type="password"[^>]*name="nyckel"/);
  assert.doesNotMatch(html, /<input(?![^>]*type="radio")[^>]*\svalue=/);
  settKontoLage({ lage: "av" });
});

test("Lantmäteri-nyckeln: hopfällbar tabell över produkter att beställa, tidsbesked och tydligt val av inloggningssätt", () => {
  settKontoLage(anvandare());
  const html = sidaNycklar(nycklar(true));
  const t = text(html);
  assert.match(html, /<details class="nyckel-detaljer" data-produkter>/);
  assert.match(t, /Produkter att beställa på Geotorget/);
  assert.match(html, /<th scope="col">Produkt<\/th><th scope="col">Används till<\/th><th scope="col">Behövs\/Valfri<\/th>/);
  assert.match(html, /data-produkt="laser"[\s\S]*Laserdata Nedladdning, skog[\s\S]*Behövs/);
  assert.match(html, /data-produkt="byggnader"[\s\S]*Byggnad Nedladdning, vektor[\s\S]*Valfri/);
  assert.match(t, /beställning[^.]*kan ta tid, ibland dagar/);
  assert.match(t, /401 eller 403/);
  assert.match(html, /Så kopplar du Lantmäteriet/);
  // Inloggningssätt som alternativknappar: Geotorgets inloggning förvald, oauth2 som andra val.
  assert.match(html, /<input type="radio" name="metod" id="[^"]+" value="basic" checked \/> Geotorgets användarnamn och lösenord \(privatperson\)/);
  assert.match(html, /value="oauth2" \/> API-portalens nyckel och hemlighet/);
  assert.doesNotMatch(html, /<select[^>]*name="metod"/);
  // Anthropic-typen har ingen produkttabell.
  assert.equal((html.match(/data-produkter/g) ?? []).length, 1);
  settKontoLage({ lage: "av" });
});

test("nyckelsidan stänger av sig när servern saknar hemligheten, och när inloggning är av", () => {
  settKontoLage(anvandare());
  const av = sidaNycklar(nycklar(false));
  assert.match(text(av), /inte påslagen på servern/);
  assert.doesNotMatch(av, /<form|<input/);
  settKontoLage({ lage: "av" });
  const lokal = sidaNycklar(null);
  assert.match(text(lokal), /config\.local\.toml/);
  assert.doesNotMatch(lokal, /<form|<input/);
  settKontoLage({ lage: "av" });
});

const detalj: AdminProjektDetalj = {
  projekt: { id: "p1", namn: "Provområdet", organisation_id: "o1", organisation: "Staden", medlemmar: 1, oppna_inbjudningar: 1 },
  roller: ["lasare", "utredare"],
  medlemmar: [{ konto_id: "k1", namn: "Anna <b>", epost: "a@x.se", roll: "lasare", kalla: "inbjudan", sparrad: false }],
  inbjudningar: [{ id: "i1", epost: "ny@x.se", roll: "utredare", skapad: 1800000000, giltig_till: 1801209600, status: "oppen" }],
};

test("adminsidan: inbjudan, medlemmar, roll och spärr, med utskriven text", () => {
  const html = adminProjektHtml(detalj);
  assert.match(html, /data-bjud-in="p1"/);
  assert.match(html, /data-roll-projekt="p1" data-roll-konto="k1"/);
  assert.match(html, /<option value="lasare" selected>läsare<\/option>/);
  assert.match(html, /data-sparra="k1" data-sparra-till="ja">Spärra kontot/);
  assert.match(text(html), /ny@x\.se\s+utredare\s+Öppen\s+2027-01-29/);
  assert.doesNotMatch(html, /Anna <b>/); // namnet är maskerat
  const spärrad = adminProjektHtml({ ...detalj, medlemmar: [{ ...detalj.medlemmar[0]!, sparrad: true }] });
  assert.match(spärrad, /data-sparra-till="nej">Öppna kontot/);
  assert.match(text(sidaAdministration([detalj])), /Staden: Provområdet/);
  assert.match(text(sidaAdministration([])), /administrerar inga projekt/);
  assert.match(text(sidaAdministration(null, "Kräver administratör")), /Kräver administratör/);
});

const MCP_URL = "https://floden.berring.se/mcp";
const mcpSvar = (antal: number): McpAvsnitt => ({
  svar: {
    max: 5,
    mcp_url: MCP_URL,
    tokens: Array.from({ length: antal }, (_, i) => ({
      id: `id${i}`,
      namn: i === 0 ? "Laptop <b>" : `Dator ${i}`,
      skapad_datum: "2027-01-15",
      senast_anvand_datum: i === 0 ? null : "2027-01-16",
      slutar_pa: "wxyz",
    })),
  },
  ny: null,
  fel: null,
});

test("MCP-token: kommandot har platshållare tills en token just skapats, då står den i klartext en gång", () => {
  settKontoLage(anvandare());
  const utan = sidaNycklar(nycklar(true), null, mcpSvar(1));
  assert.match(avkoda(utan), /claude mcp add --transport http floden https:\/\/floden\.berring\.se\/mcp --header "Authorization: Bearer <token>"/);
  assert.doesNotMatch(utan, /data-ny-mcp-token|data-kopiera-mcp/);
  const hemlig = "fl_mcp_" + "A".repeat(43);
  const ny: McpAvsnitt = { ...mcpSvar(1), ny: { id: "id0", namn: "Laptop", skapad_datum: "2027-01-15", senast_anvand_datum: null, slutar_pa: "AAAA", token: hemlig } };
  const med = sidaNycklar(nycklar(true), null, ny);
  assert.match(avkoda(med), new RegExp(`--header "Authorization: Bearer ${hemlig}"`));
  assert.match(med, /data-ny-mcp-token/);
  assert.match(med, /class="mcp-kodblock"[\s\S]*data-kopiera-mcp/);
  assert.equal(mcpKommando(MCP_URL, hemlig), `claude mcp add --transport http floden ${MCP_URL} --header "Authorization: Bearer ${hemlig}"`);
  // Listan har bara namn, datum och fyra sista tecken: aldrig token.
  const lista = sidaNycklar(nycklar(true), null, mcpSvar(2));
  assert.doesNotMatch(lista, /fl_mcp_/);
  assert.match(text(lista), /Dator 1\s+2027-01-15\s+2027-01-16\s+…wxyz/);
  settKontoLage({ lage: "av" });
});

test("MCP-token: namn maskeras, återkalla finns per rad och skapa-formuläret stängs vid fem", () => {
  settKontoLage(anvandare());
  const fyra = sidaNycklar(nycklar(true), null, mcpSvar(4));
  assert.doesNotMatch(fyra, /Laptop <b>/); // maskerat
  assert.match(fyra, /data-aterkalla-mcp="id0"/);
  assert.match(fyra, /data-mcp-token-form/);
  const fem = sidaNycklar(nycklar(true), null, mcpSvar(5));
  assert.doesNotMatch(fem, /data-mcp-token-form/);
  assert.match(text(fem), /högsta antalet/);
  const fel = sidaNycklar(nycklar(true), null, { svar: null, ny: null, fel: "Inloggning krävs" });
  assert.match(text(fel), /Inloggning krävs/);
  assert.doesNotMatch(fel, /data-mcp-token-form/);
  settKontoLage({ lage: "av" });
});

test("MCP-token: avsnittet finns även när nyckelhanteringen är avstängd, men inte utan inloggning", () => {
  settKontoLage(anvandare());
  assert.match(sidaNycklar(nycklar(false), null, mcpSvar(0)), /data-mcp-token-form/);
  settKontoLage({ lage: "av" });
  assert.doesNotMatch(sidaNycklar(null, null, mcpSvar(0)), /data-mcp-token/);
});

test("Anslutna appar: adressen till Claude-appen, namn maskeras och ta bort finns per rad", () => {
  settKontoLage(anvandare());
  const sida = sidaNycklar(nycklar(true), null, {
    ...mcpSvar(0),
    appar: [{ id: "g0", namn: "Claude <i>", skapad_datum: "2027-01-15", senast_anvand_datum: null }],
  });
  assert.match(sida, /data-aterkalla-app="g0"/);
  assert.doesNotMatch(sida, /Claude <i>/);
  assert.match(sida, /Claude &lt;i&gt;/);
  assert.match(text(sida), /2027-01-15\s+Aldrig/);
  assert.match(sida, new RegExp(MCP_URL.replace(/\./g, "\\.")));
  const tom = sidaNycklar(nycklar(true), null, { ...mcpSvar(0), appar: [] });
  assert.match(text(tom), /Inga anslutna appar/);
  const hamtar = sidaNycklar(nycklar(true), null, mcpSvar(0));
  assert.match(hamtar, /data-anslutna-appar/);
  settKontoLage({ lage: "av" });
});

const aiNycklarFixture = (): NycklarSvar => ({
  paslagen: true,
  orsak: null,
  text: null,
  primar_ai: "gemini",
  ai_leverantorer: ["anthropic", "gemini", "openai"],
  typer: [
    {
      typ: "anthropic",
      namn: "Anthropic (Claude)",
      kategori: "ai",
      beskrivning: "Claude",
      falt: [{ id: "nyckel", namn: "API-nyckel", krav: true, val: [] }],
      sparad: 1800000000,
      sparad_datum: "2027-01-15",
      slutar_pa: "1111",
    },
    {
      typ: "gemini",
      namn: "Google Gemini",
      kategori: "ai",
      beskrivning: "Gemini",
      falt: [{ id: "nyckel", namn: "API-nyckel", krav: true, val: [] }],
      sparad: 1800000000,
      sparad_datum: "2027-01-15",
      slutar_pa: "2222",
    },
    {
      typ: "openai",
      namn: "OpenAI",
      kategori: "ai",
      beskrivning: "OpenAI",
      falt: [{ id: "nyckel", namn: "API-nyckel", krav: true, val: [] }],
      sparad: null,
      sparad_datum: null,
      slutar_pa: null,
    },
    {
      typ: "lantmateriet",
      namn: "Lantmäteriet",
      kategori: "data",
      beskrivning: "Höjd- och byggnadsdata.",
      falt: [
        { id: "klient_id", namn: "Nyckel", krav: true, val: [] },
        { id: "klient_hemlighet", namn: "Hemlighet", krav: true, val: [] },
      ],
      sparad: null,
      sparad_datum: null,
      slutar_pa: null,
    },
  ],
});

test("samlad AI-nyckelsektion och segmenterad MCP-klientväljare på sidan Nycklar", () => {
  settKontoLage(anvandare());
  const hemlig = "fl_mcp_" + "B".repeat(43);
  const agy = mcpKonfig(MCP_URL, hemlig, "antigravity");
  assert.match(agy, /"mcpServers"/);
  assert.match(agy, new RegExp(`"Authorization": "Bearer ${hemlig}"`));
  assert.match(mcpKonfig(MCP_URL, hemlig, "codex"), /\[mcp_servers\.floden\]/);
  assert.match(mcpKonfig(MCP_URL, hemlig, "http"), /Authorization: Bearer /);

  const sida = sidaNycklar(aiNycklarFixture(), null, { ...mcpSvar(1), klient: "antigravity" });
  // AI-leverantörerna samlas i EN enda kompakt sektion, inte tre separata sektioner + en fjärde för primär.
  assert.equal((sida.match(/data-primar-ai-avsnitt/g) ?? []).length, 1);
  assert.equal((sida.match(/data-nyckel-typ="anthropic"/g) ?? []).length, 1);
  assert.equal((sida.match(/data-nyckel-typ="gemini"/g) ?? []).length, 1);
  assert.equal((sida.match(/data-nyckel-typ="openai"/g) ?? []).length, 1);
  assert.equal((sida.match(/data-nyckel-typ="lantmateriet"/g) ?? []).length, 1);
  assert.equal((sida.match(/class="ai-nyckel-rad"/g) ?? []).length, 3);
  assert.match(sida, /data-primar-ai="gemini" checked/);
  assert.match(sida, /data-primar-ai="openai" disabled/);
  assert.match(sida, /data-nyckelform="anthropic"[\s\S]*>Ersätt<\/button>[\s\S]*data-ta-bort-nyckel="anthropic"/);
  assert.match(sida, /data-nyckelform="openai"[\s\S]*>Spara<\/button>/);
  assert.doesNotMatch(sida, /data-ta-bort-nyckel="openai"/);
  assert.match(text(sida), /Ej sparad/);
  // Lösenordsfält fylls aldrig i av klienten.
  assert.doesNotMatch(sida, /<input(?![^>]*type="radio")[^>]*\svalue=/);
  // Segmenterad MCP-klientväljare.
  assert.match(sida, /data-mcp-klient="antigravity" aria-pressed="true"/);
  assert.match(sida, /data-mcp-klient="claude-code" aria-pressed="false"/);
  assert.match(sida, /data-mcp-klient="codex" aria-pressed="false"/);
  assert.match(sida, /data-mcp-klient="http" aria-pressed="false"/);
  assert.match(sida, /mcp_config\.json/);
  settKontoLage({ lage: "av" });
});

test("nyckelinteraktioner: spara/ersätt/ta bort nyckel, växla primär AI, växla MCP-klient, skapa/återkalla token", async () => {
  settKontoLage(anvandare());
  let aktuellaNycklar = aiNycklarFixture();
  let aktuellaTokens: McpTokenSvar = {
    max: 5,
    mcp_url: MCP_URL,
    tokens: [
      { id: "t1", namn: "Jobbdator", skapad_datum: "2027-01-15", senast_anvand_datum: null, slutar_pa: "aaaa" },
    ],
  };
  let aktuellaAppar: OauthAnslutning[] = [
    { id: "a1", namn: "Claude Desktop", skapad_datum: "2027-01-15", senast_anvand_datum: null },
  ];
  const anrop: string[] = [];
  let senasteHtml = "";
  let senasteMeddelande = "";
  const api: NycklarApi = {
    async hamtaNycklar() {
      return structuredClone(aktuellaNycklar);
    },
    async sparaNyckel(typ, varden) {
      anrop.push(`spara:${typ}:${Object.keys(varden).join(",")}`);
      const post = aktuellaNycklar.typer.find((t) => t.typ === typ);
      if (post) {
        post.sparad = 1800000100;
        post.sparad_datum = "2027-01-20";
        post.slutar_pa = (varden.nyckel ?? "9999").slice(-4);
      }
    },
    async taBortNyckel(typ) {
      anrop.push(`tabort:${typ}`);
      const post = aktuellaNycklar.typer.find((t) => t.typ === typ);
      if (post) {
        post.sparad = null;
        post.sparad_datum = null;
        post.slutar_pa = null;
      }
      if (aktuellaNycklar.primar_ai === typ) aktuellaNycklar.primar_ai = "anthropic";
    },
    async sattPrimarAi(typ) {
      anrop.push(`primar:${typ}`);
      aktuellaNycklar.primar_ai = typ;
    },
    async hamtaMcpTokens() {
      return structuredClone(aktuellaTokens);
    },
    async skapaMcpToken(namn): Promise<NyMcpToken> {
      anrop.push(`skapa-token:${namn}`);
      const ny: NyMcpToken = {
        id: "t2",
        namn,
        skapad_datum: "2027-01-20",
        senast_anvand_datum: null,
        slutar_pa: "ZZZZ",
        token: "fl_mcp_HEMLIG_ZZZZ",
      };
      aktuellaTokens.tokens.push({
        id: ny.id,
        namn: ny.namn,
        skapad_datum: ny.skapad_datum,
        senast_anvand_datum: null,
        slutar_pa: ny.slutar_pa,
      });
      return ny;
    },
    async aterkallaMcpToken(id) {
      anrop.push(`aterkalla-token:${id}`);
      aktuellaTokens.tokens = aktuellaTokens.tokens.filter((t) => t.id !== id);
    },
    async hamtaOauthAnslutningar() {
      return { anslutningar: structuredClone(aktuellaAppar) };
    },
    async aterkallaOauthAnslutning(id) {
      anrop.push(`aterkalla-app:${id}`);
      aktuellaAppar = aktuellaAppar.filter((a) => a.id !== id);
    },
  };

  const styrning = skapaNycklarStyrning(
    api,
    (html, meddelande) => {
      senasteHtml = html;
      if (meddelande) senasteMeddelande = meddelande;
    },
    (textMed) => {
      senasteMeddelande = textMed;
    },
  );

  await styrning.ladda();
  assert.match(senasteHtml, /data-primar-ai="gemini" checked/);
  assert.match(senasteHtml, /data-primar-ai="openai" disabled/);

  // 1. Spara OpenAI-nyckel → nu sparad och valbar som primär.
  await styrning.spara("openai", { nyckel: "sk-proj-test-8888" });
  assert.equal(senasteMeddelande, "Nyckeln är sparad.");
  assert.match(senasteHtml, /Sparad 2027-01-20, slutar på …8888/);
  assert.doesNotMatch(senasteHtml, /data-primar-ai="openai" disabled/);

  // 2. Växla primär AI till OpenAI.
  await styrning.valjPrimarAi("openai");
  assert.equal(senasteMeddelande, "Primär AI-leverantör är uppdaterad.");
  assert.match(senasteHtml, /data-primar-ai="openai" checked/);

  // 3. Ersätt befintlig Claude-nyckel och ta bort Gemini-nyckel.
  await styrning.spara("anthropic", { nyckel: "sk-ant-ny-7777" });
  assert.match(senasteHtml, /Sparad 2027-01-20, slutar på …7777/);
  await styrning.taBort("gemini");
  assert.equal(senasteMeddelande, "Nyckeln är borttagen.");
  assert.match(senasteHtml, /data-primar-ai="gemini" disabled/);

  // 4. Skapa MCP-token och växla MCP-klient med bibehållen nyskapad token.
  await styrning.skapaToken("Antigravity Mac");
  assert.match(senasteHtml, /data-ny-mcp-token/);
  assert.match(avkoda(senasteHtml), /fl_mcp_HEMLIG_ZZZZ/);
  assert.match(styrning.kopierbarKonfig() ?? "", /"mcpServers"/);

  styrning.valjMcpKlient("codex");
  assert.match(senasteHtml, /data-mcp-klient="codex" aria-pressed="true"/);
  assert.match(avkoda(senasteHtml), /\[mcp_servers\.floden\]/);
  assert.match(avkoda(senasteHtml), /fl_mcp_HEMLIG_ZZZZ/);
  assert.match(styrning.kopierbarKonfig() ?? "", /\[mcp_servers\.floden\]/);

  // 5. Återkalla token och ansluten app.
  await styrning.aterkallaToken("t2");
  assert.equal(senasteMeddelande, "Tokenen är återkallad.");
  assert.doesNotMatch(senasteHtml, /data-aterkalla-mcp="t2"/);
  assert.equal(styrning.kopierbarKonfig(), null);

  await styrning.aterkallaApp("a1");
  assert.equal(senasteMeddelande, "Tillgången är borttagen.");
  assert.match(text(senasteHtml), /Inga anslutna appar/);

  assert.deepEqual(anrop, [
    "spara:openai:nyckel",
    "primar:openai",
    "spara:anthropic:nyckel",
    "tabort:gemini",
    "skapa-token:Antigravity Mac",
    "aterkalla-token:t2",
    "aterkalla-app:a1",
  ]);
  settKontoLage({ lage: "av" });
});

const orgDetalj = (tillatna: string[], medlemmar = [
  { konto_id: "k1", namn: "Joel <b>", epost: "j@x.se", roll: "agare", kalla: "manuell", sparrad: false },
  { konto_id: "k2", namn: "Samuel", epost: "s@x.se", roll: "organisationsadmin", kalla: "inbjudan", sparrad: false },
  { konto_id: "k3", namn: "Lena", epost: "l@x.se", roll: "ledare", kalla: "inbjudan", sparrad: false },
]): AdminOrganisationDetalj => ({
  organisation: {
    id: "o1",
    namn: "Floden",
    din_roll: tillatna.includes("agare") ? "agare" : "organisationsadmin",
    far_byta_namn: tillatna.includes("agare"),
    roller: ["medlem", "ledare", "organisationsadmin", "agare"],
    tillatna_roller: tillatna,
  },
  roller: ["medlem", "ledare", "organisationsadmin", "agare"],
  medlemmar,
  inbjudningar: [{ id: "i1", epost: "ny@x.se", roll: "ledare", skapad: 1800000000, giltig_till: 1801209600, status: "oppen" }],
});

test("organisationsadmin: inbjudan, medlemmar och roller; ägaren får alla roller och byta namn", () => {
  const agare = adminOrganisationHtml(orgDetalj(["medlem", "ledare", "organisationsadmin", "agare"]));
  assert.match(agare, /data-org-bjud-in="o1"/);
  assert.match(agare, /data-org-namn="o1"/);
  // Formuläret erbjuder alla fyra rollerna.
  const bjudRoller = /data-org-bjud-in[\s\S]*?<select[^>]*name="roll">([\s\S]*?)<\/select>/.exec(agare)?.[1] ?? "";
  assert.deepEqual([...bjudRoller.matchAll(/value="([a-z]+)"/g)].map((m) => m[1]), ["medlem", "ledare", "organisationsadmin", "agare"]);
  assert.match(agare, /data-org-roll="o1" data-org-konto="k2"/);
  assert.match(agare, /data-org-ta-bort="o1\|k2"/);
  assert.doesNotMatch(agare, /Joel <b>/); // namnet är maskerat
  assert.match(text(agare), /ny@x\.se\s+ledare\s+Öppen\s+2027-01-29/);
});

test("organisationsadmin kan bara bjuda in medlem och ledare och inte röra en annan admin eller ägare", () => {
  const admin = adminOrganisationHtml(orgDetalj(["medlem", "ledare"]));
  const bjudRoller = /data-org-bjud-in[\s\S]*?<select[^>]*name="roll">([\s\S]*?)<\/select>/.exec(admin)?.[1] ?? "";
  assert.deepEqual([...bjudRoller.matchAll(/value="([a-z]+)"/g)].map((m) => m[1]), ["medlem", "ledare"]);
  assert.doesNotMatch(admin, /data-org-namn/); // namnet byter bara ägaren
  assert.doesNotMatch(admin, /data-org-konto="k1"|data-org-konto="k2"/); // ägare och admin är bara text
  assert.doesNotMatch(admin, /data-org-ta-bort="o1\|k1"|data-org-ta-bort="o1\|k2"/);
  assert.match(admin, /data-org-konto="k3"/); // ledaren kan ändras
});

test("administrationssidan visar organisationen före projekten, och utan organisation som förut", () => {
  const sida = sidaAdministration([detalj], null, "", [orgDetalj(["medlem", "ledare"])]);
  assert.ok(sida.indexOf("data-admin-organisation") < sida.indexOf("data-admin-projekt"));
  assert.match(text(sida), /Organisation: Floden/);
  assert.doesNotMatch(sidaAdministration([detalj]), /data-admin-organisation/);
});

test("delning: kryssrutan finns på projektets administrationssida och kan bara ändras av utredare och skapare", () => {
  const privat = adminProjektHtml(detalj);
  assert.match(privat, /data-delning="p1"/);
  assert.match(text(privat), /Synligt för organisationen Staden/);
  assert.match(privat, /data-delning="p1" disabled/); // inget kan_andra_delning: avstängd
  assert.doesNotMatch(privat, /data-delning="p1" checked/);
  const delad = adminProjektHtml({ ...detalj, projekt: { ...detalj.projekt, organisation_synlig: true, kan_andra_delning: true } });
  assert.match(delad, /data-delning="p1" checked \//);
  assert.doesNotMatch(delad, /data-delning="p1"[^>]*disabled/);
  assert.match(text(delad), /ledare läser/);
});

test("projektraden visar roll och delning med organisationen", () => {
  assert.equal(rollRad({ roll: "lasare" }), "Din roll: läsare");
  assert.equal(rollRad({ roll: "utredare", organisation_synlig: true }), "Din roll: utredare · delat med organisationen");
  assert.equal(rollRad({ organisation_synlig: true }), "delat med organisationen");
  assert.equal(rollRad({}), "");
  assert.equal(rollRad({ roll: null }), "");
});
