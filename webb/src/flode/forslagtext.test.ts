/* Körs med `npm test`. Sparstatusen i steg 2 (Spara förslag). */
import assert from "node:assert/strict";
import { test } from "node:test";
import { beraknaMarkvolym, markvolymText, sparStatus, type SparIn } from "./forslagtext.ts";

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

test("beraknaMarkvolym räknar exakt fyllnad, schakt och netto utan att gissa på nivå/lutning", () => {
  const kvadrat10x10 = [
    { x: 0, y: 0 },
    { x: 10, y: 0 },
    { x: 10, y: 10 },
    { x: 0, y: 10 },
  ];
  const v = beraknaMarkvolym([
    { roll: "mark", punkter: kvadrat10x10, egenskaper: { delta_m: 0.5 } }, // +50 m³
    { roll: "mark", yta_m2: 200, egenskaper: { delta_m: -0.75 } }, // -150 m³
    { roll: "mark", punkter: kvadrat10x10, egenskaper: { niva_rh2000: 12.5 } }, // ej beräkningsbar
    { roll: "mark", punkter: kvadrat10x10, egenskaper: { lutning: { fall: 0.02 } } }, // ej beräkningsbar
    { roll: "vegetation", yta_m2: 500, egenskaper: { delta_m: 1 } }, // fel roll, ska ignoreras
  ]);
  assert.equal(v.fyllnad_m3, 50);
  assert.equal(v.schakt_m3, 150);
  assert.equal(v.netto_m3, -100);
  assert.equal(v.antal_beraknade, 2);
  assert.equal(v.ej_berakningsbara, 2);

  const text = markvolymText(v);
  assert.match(text, /Fyllnad 50 m³ · Schakt 150 m³ · Netto −100 m³/);
  assert.match(text, /2 markytor med nivå\/lutning kräver terrängmodell/);
  assert.equal(markvolymText(beraknaMarkvolym([])), "");
});
