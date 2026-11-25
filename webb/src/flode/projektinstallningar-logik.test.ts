import assert from "node:assert/strict";
import { test } from "node:test";
import type { ProjektInstallningar } from "../types.ts";
import { arkiveradeLankHtml, installningarHtml, raderaTillaten, raderadText } from "./projektinstallningar-logik.ts";

const bas: ProjektInstallningar = {
  projekt: { id: "p1", namn: "Haga <b>", organisation_id: "o1", organisation: "Staden", organisation_synlig: false, arkiverad: false, skapad: 1800000000 },
  din_roll: "agare",
  din_roll_kalla: "projekt",
  ar_agare: true,
  far_andra: true,
  far_andra_delning: true,
  far_arkivera_radera: true,
  roller: ["lasare", "utredare", "agare"],
  medlemmar: [
    { konto_id: "k1", namn: "Anna", roll: "agare", ar_agare: true, ar_du: true, sparrad: false, epost: "anna@x.se" },
    { konto_id: "k2", namn: "Bo", roll: "lasare", ar_agare: false, ar_du: false, sparrad: false, epost: "bo@x.se" },
  ],
  inbjudningar: [{ id: "i1", epost: "ny@x.se", roll: "utredare", skapad: 1800000000, giltig_till: 1801209600, status: "oppen" }],
};
const lasare: ProjektInstallningar = {
  ...bas,
  din_roll: "lasare",
  ar_agare: false,
  far_andra: false,
  far_andra_delning: false,
  far_arkivera_radera: false,
  medlemmar: bas.medlemmar.map(({ epost: _e, ...m }) => m),
  inbjudningar: [],
};
const text = (h: string): string => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

test("radera kräver att projektets namn skrivs exakt", () => {
  assert.equal(raderaTillaten("Haga", "Haga"), true);
  assert.equal(raderaTillaten("Haga", "  Haga "), true);
  assert.equal(raderaTillaten("Haga", "haga"), false);
  assert.equal(raderaTillaten("Haga", ""), false);
  assert.equal(raderaTillaten("", ""), false);
});

test("ägaren ser alla verktyg och namnet maskeras", () => {
  const h = installningarHtml(bas);
  assert.match(h, /data-byt-namn/);
  assert.match(h, /data-bjud-in/);
  assert.match(h, /data-medlem-roll="k2"/);
  assert.match(h, /data-ta-bort-medlem="k2"/);
  assert.match(h, /data-aterkalla-inbjudan="i1"/);
  assert.match(h, /data-arkiv="ja">Arkivera projektet/);
  assert.match(h, /data-radera/);
  assert.match(h, /<option value="agare" selected>ägare<\/option>/);
  assert.doesNotMatch(h, /Haga <b>/);
  assert.match(text(h), /Privat: bara de som har en roll/);
});

test("läsaren ser bara: inga formulär, inga e-postadresser, ingen radering", () => {
  const h = installningarHtml(lasare);
  assert.doesNotMatch(h, /data-byt-namn|data-bjud-in|data-medlem-roll|data-ta-bort|data-radera|data-arkiv=/);
  assert.match(h, /data-delning[^>]*disabled/);
  assert.doesNotMatch(h, /@x\.se/);
  assert.match(text(h), /Du kan se inställningarna men inte ändra dem/);
  assert.match(text(h), /Bara projektets ägare och superadmin arkiverar eller raderar/);
});

test("ett arkiverat projekt kan återställas och delat projekt förklaras", () => {
  const h = installningarHtml({ ...bas, projekt: { ...bas.projekt, arkiverad: true, organisation_synlig: true } });
  assert.match(h, /data-arkiv="nej">Återställ projektet/);
  assert.match(h, /data-delning checked/);
  assert.match(text(h), /Organisationens ägare och administratörer utreder/);
});

test("meddelanden visas som status eller fel", () => {
  assert.match(installningarHtml(bas, "Klart"), /role="status">Klart</);
  assert.match(installningarHtml(bas, "Nej", true), /role="alert">Nej</);
  assert.match(installningarHtml(bas), /data-meddelande[^>]* hidden/);
});

test("raderingens sammanfattning använder svenska tal", () => {
  const t = raderadText({ projektmapp: 1, korningar: 1234, rapporter: 2, bestallningar: 0, roller: 1, inbjudningar: 0 });
  assert.match(t, /1\u202f234 körningar, 2 rapporter, 1 projektroll togs bort/);
  assert.match(t, /Geografin och de delade cacherna är orörda/);
  assert.match(raderadText({ projektmapp: 0, korningar: 0, rapporter: 0, bestallningar: 0, roller: 0, inbjudningar: 0 }), /^Projektet är raderat\./);
});

test("länken Visa arkiverade och tillbaka", () => {
  assert.match(arkiveradeLankHtml(false, "#/"), /href="#\/\?arkiverade=1"[^>]*>Visa arkiverade/);
  assert.match(arkiveradeLankHtml(true, "#/"), /href="#\/"[^>]*>Visa aktiva projekt/);
});
