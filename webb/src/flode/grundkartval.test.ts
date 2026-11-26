import assert from "node:assert/strict";
import { test } from "node:test";
import { LagerLista } from "../karta/lager.ts";
import { OSM_ID } from "../karta/osm.ts";
import {
  aktuellGrundkarta,
  grundkartvalHtml,
  tillampaGrundkarta,
  tillgangligaGrundkartor,
} from "./grundkartval.ts";
import { BYGGNADER_ID, HOJDKURVOR_ID, ORTO_ID, PLAN_ID, SKUGGNING_ID } from "./rasterlager.ts";

function skapaLager(medOrto = true): LagerLista {
  const l = new LagerLista("test", null);
  l.registrera({ id: OSM_ID, grupp: "underlag", namn: "OSM", synlig: false, opacitet: 1 });
  l.registrera({
    id: ORTO_ID,
    grupp: "underlag",
    namn: "Ortofoto",
    synlig: medOrto,
    opacitet: 1,
    ejTillgangligt: medOrto ? undefined : "Saknas",
  });
  l.registrera({ id: SKUGGNING_ID, grupp: "geometri", namn: "Terräng", synlig: true, opacitet: 0.9 });
  l.registrera({ id: HOJDKURVOR_ID, grupp: "geometri", namn: "Höjdkurvor", synlig: true, opacitet: 1 });
  l.registrera({ id: BYGGNADER_ID, grupp: "geometri", namn: "Byggnader", synlig: true, opacitet: 1 });
  l.registrera({ id: PLAN_ID, grupp: "planforslag", namn: "Förslag", synlig: true, opacitet: 1 });
  l.registrera({ id: "res:visad", grupp: "resultat", namn: "Skyfall", synlig: true, opacitet: 0.85 });
  return l;
}

test("tillgangligaGrundkartor inkluderar alltid ren och osm samt tillgängliga projektlager", () => {
  assert.deepEqual(tillgangligaGrundkartor(skapaLager(true)), ["ren", "osm", "ortofoto", "skuggning", "hojdkurvor"]);
  assert.deepEqual(tillgangligaGrundkartor(skapaLager(false)), ["ren", "osm", "skuggning", "hojdkurvor"]);
  assert.deepEqual(tillgangligaGrundkartor(new LagerLista("tom", null)), ["ren", "osm"]);
});

test("tillampaGrundkarta växlar rent utan att röra byggnader, förslag eller analyslager", () => {
  const l = skapaLager(true);
  let osmPa: boolean | null = null;

  tillampaGrundkarta("ren", 0.5, l, (pa) => {
    osmPa = pa;
  });
  assert.equal(osmPa, false);
  assert.equal(l.hamta(OSM_ID)?.synlig, false);
  assert.equal(l.hamta(ORTO_ID)?.synlig, false);
  assert.equal(l.hamta(SKUGGNING_ID)?.synlig, false);
  assert.equal(l.hamta(HOJDKURVOR_ID)?.synlig, false);
  assert.equal(l.hamta(BYGGNADER_ID)?.synlig, true);
  assert.equal(l.hamta(PLAN_ID)?.synlig, true);
  assert.equal(l.hamta("res:visad")?.synlig, true);
  assert.equal(l.hamta("res:visad")?.opacitet, 0.85);
  assert.equal(aktuellGrundkarta(l).val, "ren");

  tillampaGrundkarta("osm", 0.65, l, (pa) => {
    osmPa = pa;
  });
  assert.equal(osmPa, true);
  assert.equal(l.hamta(OSM_ID)?.synlig, true);
  assert.equal(l.hamta(OSM_ID)?.opacitet, 0.65);
  assert.equal(l.hamta(ORTO_ID)?.synlig, false);
  assert.deepEqual(aktuellGrundkarta(l), { val: "osm", opacitet: 0.65 });

  tillampaGrundkarta("skuggning", 0.8, l, (pa) => {
    osmPa = pa;
  });
  assert.equal(osmPa, false);
  assert.equal(l.hamta(OSM_ID)?.synlig, false);
  assert.equal(l.hamta(SKUGGNING_ID)?.synlig, true);
  assert.equal(l.hamta(SKUGGNING_ID)?.opacitet, 0.8);
  assert.equal(l.hamta("res:visad")?.synlig, true);
});

test("grundkartvalHtml döljer opacitetsslider i läge Ren och visar den i övriga lägen", () => {
  const renHtml = grundkartvalHtml("ren", 1, ["ren", "osm", "skuggning"]);
  assert.match(renHtml, /data-grundkarta="ren" aria-pressed="true"/);
  assert.match(renHtml, /Terräng/);
  assert.doesNotMatch(renHtml, /data-grundkarta="ortofoto"/);
  assert.doesNotMatch(renHtml, /data-grundkarta-opacitet/);

  const ortoHtml = grundkartvalHtml("ortofoto", 0.75, ["ren", "osm", "ortofoto"]);
  assert.match(ortoHtml, /data-grundkarta="ortofoto" aria-pressed="true"/);
  assert.match(ortoHtml, /data-grundkarta-opacitet/);
  assert.match(ortoHtml, /value="75"/);
  assert.match(ortoHtml, /75\u202F%/);
  assert.match(ortoHtml, /data-grundkarta-vaxla aria-expanded="true"/);
});

test("grundkartvalHtml stöder hopfällt och utfällt läge", () => {
  const hopfalldTerrang = grundkartvalHtml("skuggning", 0.6, ["ren", "osm", "skuggning"], false);
  assert.match(hopfalldTerrang, /gk-hopfalld/);
  assert.match(hopfalldTerrang, /data-grundkarta-vaxla aria-expanded="false"/);
  assert.match(hopfalldTerrang, /Terräng · 60\u202F% ▾/);
  assert.doesNotMatch(hopfalldTerrang, /data-grundkarta="skuggning"/);
  assert.doesNotMatch(hopfalldTerrang, /data-grundkarta-opacitet/);

  const hopfalldRen = grundkartvalHtml("ren", 1, ["ren", "osm"], false);
  assert.match(hopfalldRen, /Ren karta ▾/);
  assert.doesNotMatch(hopfalldRen, /%/);
});
