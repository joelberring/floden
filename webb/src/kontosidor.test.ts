import assert from "node:assert/strict";
import { test } from "node:test";
import { adminTillaten, arbetsbankTillaten, kontoHuvudHtml, settKontoLage, tolkaKontoSvar } from "./konto.ts";
import { adminProjektHtml, nyckelStatusText, sidaAdministration, sidaNycklar } from "./kontosidor-html.ts";
import { sidaOm, sidaProjektUtanData, sidaStart, sidhuvud, type SidId } from "./sidor.ts";
import type { AdminProjektDetalj, ModulInfo, NycklarSvar, OmradeSammanfattning, ProjektListaPost } from "./types.ts";

const text = (html: string): string => html.replace(/<[^>]*>/g, " ").replace(/&[a-z#0-9]+;/g, " ");
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
      { id: "metod", namn: "Metod", krav: false, val: ["oauth2", "basic"] }], sparad: null, sparad_datum: null, slutar_pa: null },
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
  assert.doesNotMatch(html, /<input[^>]*\svalue=/);
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
