import assert from "node:assert/strict";
import { test } from "node:test";

import type { KorningSlag, RapportAmne, RapportJobb } from "../types.ts";
import {
  arPagaende,
  byggLuftIndata,
  byggRapportValForSkapa,
  fragorForAmne,
  framsteg,
  jobbText,
  knappLage,
  skapaPanelHtml,
  SLAGTEXT,
  TOMT_LUFT_FORM,
  valjUnderlag,
} from "./rapportskapa.ts";
import type { FlodeFraga } from "./status.ts";
import { FORVAL_BESTALLNING } from "./tillstand.ts";

const amne = (over: Partial<RapportAmne>): RapportAmne => ({
  id: "skyfall", namn: "Skyfall", kan_skapas: true, forklaring: null, mall: true, forval_korning: "k1", korningar: [], ...over,
});
const jobb = (over: Partial<RapportJobb>): RapportJobb => ({
  kontrakt: "K48/v1", jobb_id: "j-0123456789ab", projekt: "p", amne: "skyfall", korning_id: "k1", bestallning_id: "b-0123456789ab",
  status: "kor", steg: "skapar", steg_nr: 1, steg_totalt: 4, text: "Skapar rapporten och ritar kartor", rapport_id: null,
  kontroll: null, lankar: null, fel: null, status_url: "/api/projekt/p/rapporter/jobb/j-0123456789ab", ...over,
});
const EKO = amne({ id: "ekologi", namn: "Ekologi", kan_skapas: false, mall: false, forval_korning: null, forklaring: "Rapportmall (R2) för ekologi saknas än." });

test("knappen förklarar varför ekologi, planunderlag och saknad beräkning inte går", () => {
  assert.equal(knappLage(amne({}), FORVAL_BESTALLNING, false).aktiv, true);
  const e = knappLage(EKO, FORVAL_BESTALLNING, false);
  assert.equal(e.aktiv, false);
  assert.match(e.text, /saknas/);
  const p = knappLage(amne({}), { ...FORVAL_BESTALLNING, skede: "planunderlag" }, false);
  assert.equal(p.aktiv, false);
  assert.match(p.text, /planunderlag/);
  assert.equal(knappLage(amne({}), FORVAL_BESTALLNING, true).aktiv, false);
  assert.equal(knappLage(undefined, FORVAL_BESTALLNING, false).aktiv, false);
});

test("förloppet följer jobbets steg och slutar på klart eller fel", () => {
  assert.equal(framsteg(jobb({ status: "ko" })), 0);
  assert.equal(framsteg(jobb({ steg_nr: 3 })), 50);
  assert.equal(framsteg(jobb({ status: "klar" })), 100);
  assert.equal(arPagaende(jobb({ status: "ko" })), true);
  assert.equal(arPagaende(jobb({ status: "klar" })), false);
  assert.equal(arPagaende(null), false);
  assert.match(jobbText(jobb({ steg_nr: 2, text: "Skriver påståenden med belägg" })), /steg 2 av 4/);
  assert.match(jobbText(jobb({ status: "fel", fel: "Körningen saknas" })), /Körningen saknas/);
  assert.match(jobbText(jobb({ status: "klar", kontroll: { godkand: false, antal_fel: 3, exporterad: false } })), /3 fel/);
  assert.match(jobbText(jobb({ status: "klar", kontroll: { godkand: true, antal_fel: 0, exporterad: true } })), /exporterade/);
});

test("panelen visar ekologins förklaring, stänger av knappen och visar länkar när jobbet är klart", () => {
  const eko = skapaPanelHtml({ amnen: [amne({}), EKO], valt: "ekologi", best: FORVAL_BESTALLNING, jobb: null, fel: null });
  assert.match(eko, /id="rp-forklaring"/);
  assert.match(eko, /id="rp-skapa-knapp"[^>]*disabled/);
  assert.match(eko, /aria-describedby="rp-knapp-not"/);
  const klar = skapaPanelHtml({
    amnen: [amne({})], valt: "skyfall", best: FORVAL_BESTALLNING, fel: null,
    jobb: jobb({ status: "klar", rapport_id: "r-1", kontroll: { godkand: true, antal_fel: 0, exporterad: true }, lankar: { pdf: "/api/p/pdf", word: "/api/p/word" } }),
  });
  assert.match(klar, /href="\/api\/p\/pdf"/);
  assert.match(klar, /href="\/api\/p\/word"/);
  assert.match(klar, /<progress[^>]*value="100"/);
  const pagar = skapaPanelHtml({ amnen: [amne({})], valt: "skyfall", best: FORVAL_BESTALLNING, fel: null, jobb: jobb({}) });
  assert.match(pagar, /Skapar …/);
  assert.match(pagar, /id="rp-syfte"[^>]*disabled/);
});

test("fel och syftestext escapas", () => {
  const h = skapaPanelHtml({ amnen: [amne({})], valt: "skyfall", best: { ...FORVAL_BESTALLNING, syfte: "<b>x</b>" }, jobb: null, fel: "<i>fel</i>" });
  assert.doesNotMatch(h, /<b>x<\/b>/);
  assert.doesNotMatch(h, /<i>fel<\/i>/);
});

const fraga = (id: string, amne: string): FlodeFraga => ({ id, amne, text: `Fråga ${id}`, niva: "svarar", doman: [] });
const KAT = [fraga("SK-01", "skyfall"), fraga("SK-02", "skyfall"), fraga("DL-01", "dagsljus"), fraga("demo/vatten.json", "skyfall")];
const KOR = (id: string, slag: KorningSlag, sc: string | null) => ({
  korning_id: id, fraga_id: "f", skapad: null, slag, scenario: sc ? { slug: sc, namn: `Namn ${sc}` } : null,
});

test("frågorna ur huvudkatalogen skickas med, bara för ämnet och bara katalog-id", () => {
  const valda = new Set(["SK-01", "DL-01", "demo/vatten.json"]);
  assert.deepEqual(fragorForAmne("skyfall", valda, KAT).map((f) => f.id), ["SK-01"]);
  assert.deepEqual(fragorForAmne("dagsljus", valda, KAT).map((f) => f.id), ["DL-01"]);
  assert.deepEqual(fragorForAmne("skyfall", null, KAT), []);
  assert.deepEqual(fragorForAmne("skyfall", valda, null), []);
});

test("rapporten bygger på körningen för det ritade förslaget, annars sägs det", () => {
  const a = amne({ forval_korning: "k-demo", korningar: [KOR("k-demo", "demo", null), KOR("k-eget", "forslag", "hus-a")] });
  const forslag = { slug: "hus-a", namn: "Hus A" };
  const eget = valjUnderlag(a, forslag, new Set(["SK-01", "SK-02"]), KAT);
  assert.deepEqual(eget.val, { fragor: ["SK-01", "SK-02"], korning_id: "k-eget", scenario: "hus-a" });
  assert.equal(eget.varning, null);
  assert.match(eget.korningText, /Hus A/);
  assert.match(eget.fragorText, /^2 frågor/);
  // Förslaget är inte beräknat: förvalet (demokörningen) används och varningen säger det.
  const demo = amne({ forval_korning: "k-demo", korningar: [KOR("k-demo", "demo", null)] });
  const u = valjUnderlag(demo, forslag, new Set(["SK-01"]), KAT);
  assert.equal(u.val.korning_id, "k-demo");
  assert.equal(u.val.scenario, undefined);
  assert.match(u.varning ?? "", /inte beräknat/);
  assert.match(u.varning ?? "", /demokörningen/);
  assert.match(u.fragorText, /^1 fråga /);
  // Inga valda frågor: rapporten tar alla frågor för ämnet och säger det.
  const ingen = valjUnderlag(a, forslag, null, KAT);
  assert.deepEqual(ingen.val.fragor, undefined);
  assert.match(ingen.fragorText, /alla frågor/);
});

test("saknas körning säger panelen det, länkar till steg 2 (karta) och stänger knappen", () => {
  const utan = amne({ kan_skapas: false, forval_korning: null, korningar: [], forklaring: "Kör beräkningarna i steg 2 (Karta & analys) först." });
  const u = valjUnderlag(utan, null, null, KAT);
  assert.equal(u.saknasKorning, true);
  const h = skapaPanelHtml({ projekt: "p", underlag: u, amnen: [utan], valt: "skyfall", best: FORVAL_BESTALLNING, jobb: null, fel: null });
  assert.match(h, /id="rp-saknas-korning"/);
  assert.match(h, /href="#\/p\/p\/karta"/);
  assert.doesNotMatch(h, /href="#\/p\/p\/fragor"/);
  assert.doesNotMatch(h, /steg 3/i);
  assert.match(h, /id="rp-skapa-knapp"[^>]*disabled/);
  assert.doesNotMatch(h, /id="rp-forklaring"/);
});

test("W-B5: lage_idag visar 'Läget idag' (aldrig undefined)", () => {
  assert.equal(SLAGTEXT.lage_idag, "Läget idag");
  const nulage = amne({ forval_korning: "k-nulage", forval_slag: "lage_idag", korningar: [KOR("k-nulage", "lage_idag", null)] });
  const u = valjUnderlag(nulage, null, null, KAT);
  assert.equal(u.slag, "lage_idag");
  assert.match(u.korningText, /Läget idag/);
  assert.doesNotMatch(u.korningText, /undefined/);
  assert.match(u.varning ?? "", /Läget idag/);
  assert.doesNotMatch(u.varning ?? "", /undefined/);
});

test("Ekologi är valbart och skapbart när ekologikörning finns", () => {
  const ekoKlar = amne({
    id: "ekologi",
    namn: "Ekologi",
    mall: true,
    kan_skapas: true,
    forval_korning: "k-eko",
    korningar: [KOR("k-eko", "forslag", "hus-a")],
  });
  const u = valjUnderlag(ekoKlar, { slug: "hus-a", namn: "Hus A" }, null, KAT);
  assert.equal(u.saknasKorning, false);
  assert.equal(u.val.korning_id, "k-eko");
  assert.equal(knappLage(ekoKlar, FORVAL_BESTALLNING, false, u.saknasKorning).aktiv, true);
  const h = skapaPanelHtml({ projekt: "p", underlag: u, amnen: [ekoKlar], valt: "ekologi", best: FORVAL_BESTALLNING, jobb: null, fel: null });
  assert.doesNotMatch(h, /id="rp-skapa-knapp"[^>]*disabled/);
});

test("Luft i Steg 3 visar skiss-notis, CSV-formulär, validerar bakgrund och bygger LuftRapportIndata", () => {
  const luft = amne({ id: "luft", namn: "Luft", mall: true, kan_skapas: true, forval_korning: null, korningar: [] });
  const u = valjUnderlag(luft, null, null, KAT);
  assert.equal(u.saknasKorning, false);
  // Tomt formulär: knappen avstängd och formuläret visas med skiss-notis.
  const hTom = skapaPanelHtml({ projekt: "p", underlag: u, amnen: [luft], valt: "luft", best: FORVAL_BESTALLNING, jobb: null, fel: null, luftForm: TOMT_LUFT_FORM });
  assert.match(hTom, /id="rp-luft-skiss"/);
  assert.match(hTom, /Skiss/);
  assert.match(hTom, /id="rp-luft-fore-fil"/);
  assert.match(hTom, /id="rp-luft-fore-csv"/);
  assert.match(hTom, /id="rp-luft-efter-fil"/);
  assert.match(hTom, /id="rp-luft-efter-csv"/);
  assert.match(hTom, /id="rp-luft-bakgrund"/);
  assert.match(hTom, /id="rp-skapa-knapp"[^>]*disabled/);

  // Negativ bakgrund nekas.
  const neg = byggLuftIndata({ ...TOMT_LUFT_FORM, serie_fore_csv: "tid_s,halt_ug_m3\n0,10", bakgrund_text: "-1" });
  assert.equal(neg.ok, false);

  // Giltigt formulär aktiverar knappen och ger exakt objekt.
  const okForm = {
    anvandNySerie: false,
    serie_fore_csv: "tid_s,halt_ug_m3\n0,15.0\n3600,18.5",
    serie_efter_csv: "tid_s,halt_ug_m3\n0,14.0\n3600,17.0",
    bakgrund_text: "12,5",
  };
  const hOk = skapaPanelHtml({ projekt: "p", underlag: u, amnen: [luft], valt: "luft", best: FORVAL_BESTALLNING, jobb: null, fel: null, luftForm: okForm });
  assert.doesNotMatch(hOk, /id="rp-skapa-knapp"[^>]*disabled/);
  const valRes = byggRapportValForSkapa(luft, u, okForm);
  assert.equal(valRes.ok, true);
  if (valRes.ok) {
    assert.deepEqual(valRes.val.luft, {
      serie_fore_csv: "tid_s,halt_ug_m3\n0,15.0\n3600,18.5",
      serie_efter_csv: "tid_s,halt_ug_m3\n0,14.0\n3600,17.0",
      bakgrund_ug_m3: 12.5,
    });
  }
});

test("panelen visar körning, frågor och escapar text", () => {
  const a = amne({ forval_korning: "k1", korningar: [KOR("k1", "forslag", "x")] });
  const u = valjUnderlag(a, { slug: "x", namn: "<b>X</b>" }, new Set(["SK-01"]), KAT);
  const h = skapaPanelHtml({ projekt: "p", underlag: u, amnen: [a], valt: "skyfall", best: FORVAL_BESTALLNING, jobb: null, fel: null });
  assert.match(h, /id="rp-korning"/);
  assert.match(h, /SK-01 Fråga SK-01/);
  assert.doesNotMatch(h, /<b>X<\/b>/);
});
