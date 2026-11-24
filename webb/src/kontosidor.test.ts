import assert from "node:assert/strict";
import { test } from "node:test";
import { adminTillaten, arbetsbankTillaten, kontoHuvudHtml, settKontoLage, tolkaKontoSvar } from "./konto.ts";
import { adminOrganisationHtml, adminProjektHtml, mcpKommando, nyckelStatusText, sidaAdministration, sidaNycklar } from "./kontosidor-html.ts";
import type { McpAvsnitt } from "./kontosidor-html.ts";
import { sidaOm, sidaProjektUtanData, sidaStart, sidhuvud, type SidId } from "./sidor.ts";
import type { AdminOrganisationDetalj, AdminProjektDetalj, ModulInfo, NycklarSvar, OmradeSammanfattning, ProjektListaPost } from "./types.ts";

const text = (html: string): string => html.replace(/<[^>]*>/g, " ").replace(/&[a-z#0-9]+;/g, " ");
const avkoda = (html: string): string =>
  html.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#39;/g, "'").replace(/&amp;/g, "&");
const moduler: ModulInfo[] = [];
const kontrollfall = [{ omrade_id: "A1_x", kod: "A1", namn: "Enkel sänka", grupp: "A", beskrivning: "Provfall." }] as unknown as OmradeSammanfattning[];

const anvandare = (extra: Record<string, unknown> = {}) =>
  tolkaKontoSvar(200, { inloggning: "pa", konto: { namn: "Anna Svensson", epost: "a@x.se", ...extra } });

const projekt: ProjektListaPost[] = [
  { id: "demokvarteret", namn: "Demokvarteret", undertitel: "påhittat kvarter", paahittat: true, beskrivning: "x", roll: "utredare", finns: true },
  { id: "abc123", namn: "Provområdet", undertitel: "Projekt", paahittat: false, beskrivning: "", roll: "lasare", finns: false },
];

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

test("sidhuvudets kontodel: länk till Nycklar, och Administration bara för admin", () => {
  settKontoLage({ lage: "av" });
  assert.doesNotMatch(sidhuvud("start"), /Nycklar|Administration/);
  settKontoLage(anvandare());
  assert.match(sidhuvud("start"), /href="#\/nycklar"[^>]*>Nycklar</);
  assert.doesNotMatch(sidhuvud("start"), /Administration/);
  settKontoLage(anvandare({ admin: true }));
  assert.match(kontoHuvudHtml(), /href="#\/administration"[^>]*>Administration</);
  settKontoLage({ lage: "av" });
});

test("arbetsbänken finns varken i menyn eller på sidan Om beräkningarna", () => {
  for (const lage of [{ lage: "av" } as const, anvandare({ superadmin: true, admin: true })]) {
    settKontoLage(lage);
    for (const s of ["start", "projekt", "karta", "fragor", "om", "nycklar", "administration"] as SidId[]) {
      assert.doesNotMatch(sidhuvud(s), /arbetsb/i, s);
    }
    assert.doesNotMatch(sidaOm(moduler, kontrollfall), /arbetsb/i);
    assert.match(text(sidaOm(moduler, kontrollfall)), /Enkel sänka/); // kontrollfallen står kvar som text
  }
  settKontoLage({ lage: "av" });
});

test("startsidan för en inloggad visar projekten med länkar och ingen demotext", () => {
  settKontoLage(anvandare());
  const html = sidaStart(moduler, true, projekt);
  assert.match(html, /Dina projekt/);
  assert.match(html, /href="#\/projekt\/demokvarteret"/);
  assert.match(html, /href="#\/projekt\/abc123"/);
  assert.match(text(html), /Provområdet\s+Projekt · lasare/);
  assert.doesNotMatch(text(html), /floden demo/);
  // Bara projekt ur registret, inget demokvarter: ingen demotext och knappen går till projektet.
  const utanDemo = sidaStart(moduler, false, [projekt[1]!]);
  assert.doesNotMatch(text(utanDemo), /floden demo|påhittat kvarter/);
  assert.match(utanDemo, /Öppna Provområdet/);
  // Utan inloggning och utan projekt: som förut.
  settKontoLage({ lage: "av" });
  assert.doesNotMatch(sidaStart(moduler, true, projekt), /Dina projekt/);
  assert.match(sidaStart(moduler, false, []), /floden demo/);
  settKontoLage({ lage: "av" });
});

test("projekt utan beräkningar visas utan påhittat innehåll", () => {
  const html = sidaProjektUtanData(projekt[1]!);
  assert.match(text(html), /Provområdet/);
  assert.match(text(html), /inga beräkningar än/);
  assert.doesNotMatch(text(html), /demokvarter|floden demo/i);
  assert.match(text(sidaProjektUtanData(null)), /inte tillgång/);
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

test("Lantmäteri-nyckeln: tabell över produkter att beställa, tidsbesked och tydligt val av inloggningssätt", () => {
  settKontoLage(anvandare());
  const html = sidaNycklar(nycklar(true));
  const t = text(html);
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
  assert.match(html, /<option value="lasare" selected>/);
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

test("projektlistan visar vilka projekt som är delade med organisationen", () => {
  settKontoLage(anvandare());
  const lista: ProjektListaPost[] = [{ ...projekt[1]!, organisation_synlig: true, roll: "utredare" }, { ...projekt[1]!, id: "x", namn: "Privat" }];
  const html = sidaStart(moduler, false, lista);
  assert.match(text(html), /Provområdet\s+Projekt · utredare · delat med organisationen/);
  assert.doesNotMatch(text(html), /Privat\s+Projekt · lasare · delat/);
  assert.match(sidaProjektUtanData(lista[0]!), /Delat med organisationen/);
  assert.doesNotMatch(sidaProjektUtanData(lista[1]!), /Delat med organisationen/);
  settKontoLage({ lage: "av" });
});
