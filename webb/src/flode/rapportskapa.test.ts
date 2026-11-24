import assert from "node:assert/strict";
import { test } from "node:test";

import type { RapportAmne, RapportJobb } from "../types.ts";
import { arPagaende, framsteg, jobbText, knappLage, skapaPanelHtml } from "./rapportskapa.ts";
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
