/* Körs med `npm test`. Sparstatusen i steg 2 (Spara förslag). */
import assert from "node:assert/strict";
import { test } from "node:test";
import { sparStatus, type SparIn } from "./forslagtext.ts";

const bas: SparIn = { antal: 2, likaSomSparat: false, sparatNamn: null, tid: null, giltig: true, kontrollFel: null, sparFel: null, sparar: false };

test("tomt förslag: inget att spara, och det sparade finns kvar om ett finns", () => {
  assert.deepEqual(sparStatus({ ...bas, antal: 0 }), { typ: "tomt", text: "Inget att spara än. Rita något först." });
  assert.match(sparStatus({ ...bas, antal: 0, sparatNamn: "Mitt förslag" }).text, /Mitt förslag finns kvar/);
});

test("osparat, sparat, sparar och fel är olika och har text", () => {
  assert.equal(sparStatus(bas).typ, "osparat");
  assert.match(sparStatus(bas).text, /Tryck Spara förslag/);
  assert.match(sparStatus({ ...bas, giltig: null }).text, /Kontrollerar/);
  const s = sparStatus({ ...bas, likaSomSparat: true, sparatNamn: "Kvarteret", tid: "12:03" });
  assert.deepEqual(s, { typ: "sparat", text: "Sparat som Kvarteret kl. 12:03 på den här datorn." });
  assert.equal(sparStatus({ ...bas, sparar: true }).typ, "sparar");
  assert.match(sparStatus({ ...bas, sparFel: "Servern svarade 500" }).text, /^Kunde inte spara: Servern svarade 500/);
});

test("ett förslag med kontrollfel sägs inte vara sparat", () => {
  const s = sparStatus({ ...bas, giltig: false, likaSomSparat: true, sparatNamn: "X" });
  assert.equal(s.typ, "ogiltigt");
  assert.match(s.text, /måste rättas/);
  assert.equal(sparStatus({ ...bas, kontrollFel: "Nätverksfel" }).typ, "ogiltigt");
});
