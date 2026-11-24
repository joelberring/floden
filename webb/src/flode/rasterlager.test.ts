/* Körs med `npm test`. Underlag från andra tjänster och bakgrundsbilder som kartlager (flyttat från kartsidan). */
import assert from "node:assert/strict";
import { test } from "node:test";
import { LagerLista, type Lagring } from "../karta/lager.ts";
import { giltigKakelmall, lasUnderlag, sparaUnderlag, wmsBrickmall, type Underlag } from "../karta/wms.ts";
import { ORTO_ID, rasterAv, SKUGGNING_ID } from "./rasterlager.ts";
import { lasAvtappning } from "./tillstand.ts";

const minne = (): Lagring & { d: Map<string, string> } => {
  const d = new Map<string, string>();
  return { d, getItem: (k) => d.get(k) ?? null, setItem: (k, v) => void d.set(k, v) };
};
const wms: Underlag = { id: "wms-1", typ: "wms", namn: "Kommunens karta", url: "https://exempel.se/wms", lager: "bas", stod: "3857", version: "1.3.0", kalla: "WMS från exempel.se" };

test("ett WMS-underlag blir ett kakellager via servern, med källan kvar", () => {
  const r = rasterAv({ id: "u:wms-1", synlig: true, opacitet: 0.5, data: { underlag: wms } });
  assert.ok(r);
  assert.equal(r?.kalla.slag, "kakel");
  const mall = (r?.kalla as { mall: string }).mall;
  assert.ok(mall.startsWith("/api/kartverktyg/wms/bild?"));
  assert.ok(mall.endsWith("&bbox={bbox-epsg-3857}"));
  assert.equal(mall, wmsBrickmall(wms));
  assert.equal(r?.opacitet, 0.5);
});

test("en kakelmall används som den är, och en bild får utbredning", () => {
  const kakel: Underlag = { id: "k", typ: "kakel", namn: "Kakel", url: "https://t.exempel.se/{z}/{x}/{y}.png", kalla: "Kakel" };
  assert.equal((rasterAv({ id: "u:k", synlig: true, opacitet: 1, data: { underlag: kakel } })?.kalla as { mall: string }).mall, kakel.url);
  const ut = { x_min: 0, y_min: 0, x_max: 10, y_max: 10 };
  const bild = rasterAv({ id: SKUGGNING_ID, synlig: false, opacitet: 0.9, data: { url: "blob:x", utbredning: ut, skarpa: true } }, true);
  assert.equal(bild?.synlig, true);
  assert.deepEqual(bild?.kalla, { slag: "bild", url: "blob:x", utbredning: ut, skarpa: true });
  assert.equal(rasterAv({ id: ORTO_ID, synlig: true, opacitet: 1 }), null);
  assert.equal(rasterAv({ id: ORTO_ID, synlig: true, opacitet: 1, data: {} }), null);
});

test("kakelmallen måste ha {z}, {x}, {y} och börja med http", () => {
  assert.equal(giltigKakelmall("https://t.exempel.se/{z}/{x}/{y}.png"), true);
  assert.equal(giltigKakelmall("https://t.exempel.se/{z}/{x}.png"), false);
  assert.equal(giltigKakelmall("ftp://t/{z}/{x}/{y}"), false);
});

test("sparade underlag följer projektet, och lager som tas bort försvinner ur listan", () => {
  const l = minne();
  sparaUnderlag("p1", [wms], l);
  assert.deepEqual(lasUnderlag("p1", l), [wms]);
  assert.deepEqual(lasUnderlag("p2", l), []);
  const lista = new LagerLista("p1", l);
  lista.registrera({ id: "u:wms-1", grupp: "underlag", namn: wms.namn, synlig: true, opacitet: 1, kanTasBort: true, data: { underlag: wms } });
  assert.equal(lista.iGrupp("underlag").length, 1);
  lista.tabort("u:wms-1");
  assert.equal(lista.iGrupp("underlag").length, 0);
});

test("synlighet och täckning för bakgrundslagren sparas per projekt och delas mellan stegen", () => {
  const l = minne();
  const a = new LagerLista("p1", l);
  a.registrera({ id: SKUGGNING_ID, grupp: "geometri", namn: "Terräng", synlig: true, opacitet: 0.9 });
  a.sattSynlig(SKUGGNING_ID, false);
  a.sattOpacitet(SKUGGNING_ID, 0.4);
  // Ett annat steg öppnar samma lagring och ser valet.
  const b = new LagerLista("p1", l);
  b.registrera({ id: SKUGGNING_ID, grupp: "geometri", namn: "Terräng", synlig: true, opacitet: 0.9 });
  assert.equal(b.hamta(SKUGGNING_ID)?.synlig, false);
  assert.equal(b.hamta(SKUGGNING_ID)?.opacitet, 0.4);
});

test("avtappning: komma eller punkt, tomt = standard, negativt och text är fel", () => {
  assert.deepEqual(lasAvtappning(""), { varde: null, ok: true });
  assert.deepEqual(lasAvtappning("  "), { varde: null, ok: true });
  assert.deepEqual(lasAvtappning("2,5"), { varde: 2.5, ok: true });
  assert.deepEqual(lasAvtappning("2.5"), { varde: 2.5, ok: true });
  assert.deepEqual(lasAvtappning("0"), { varde: 0, ok: true });
  assert.equal(lasAvtappning("-1").ok, false);
  assert.equal(lasAvtappning("abc").ok, false);
});
