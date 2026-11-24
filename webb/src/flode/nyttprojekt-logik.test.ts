import assert from "node:assert/strict";
import { test } from "node:test";

import type { GeografiPost } from "../types.ts";
import { tolkaFlodeAdress, NYTT_PROJEKT_ADRESS } from "./adress.ts";
import { arealText, formularFel, omslutande, placering, placeringText, punkterPolygon, rektangelPolygon, skapaBegaran } from "./nyttprojekt-logik.ts";

const geo = {
  ref: "goteborg-test@v1", id: "goteborg-test", version: "v1", namn: "Göteborg test", kommun: "goteborg", epsg: 3011,
  karna_3011: [1000, 1000, 2000, 2000], gransen_3011: [500, 500, 2500, 2500],
  buffert_m: 500, varningar: [], manifest_sha256: "x", far_skapa_projekt: true,
} as unknown as GeografiPost;

test("adressen #/nytt är flödets steg 1 med nytt-flagga, och gamla adresser är oförändrade", () => {
  assert.deepEqual(tolkaFlodeAdress(NYTT_PROJEKT_ADRESS), { steg: "projekt", projekt: null, nytt: true });
  assert.deepEqual(tolkaFlodeAdress("#/"), { steg: "projekt", projekt: null });
  assert.equal(tolkaFlodeAdress("#/nytt/extra"), null);
});

test("rektangel och polygon blir GeoJSON i EPSG:3011 och en omslutande rektangel", () => {
  assert.deepEqual(omslutande([{ x: 5, y: 9 }, { x: 1, y: 2 }, { x: 7, y: 3 }]), [1, 2, 7, 9]);
  assert.equal(omslutande([]), null);
  const p = rektangelPolygon([0, 0, 10, 20]);
  assert.deepEqual(p.coordinates[0]?.[0], p.coordinates[0]?.[4]);
  assert.equal(p.coordinates[0]?.length, 5);
  assert.equal(punkterPolygon([{ x: 0, y: 0 }, { x: 1, y: 1 }]), null);
  const q = punkterPolygon([{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 3 }]);
  assert.equal(q?.coordinates[0]?.length, 4);
});

test("placering: inom kärnan, delvis utanför (varning) och utanför yttre gräns (fel)", () => {
  assert.deepEqual(placering([1100, 1100, 1200, 1200], geo), { slag: "inom" });
  const d = placering([900, 1100, 1100, 1300], geo);
  assert.equal(d.slag, "delvis");
  if (d.slag === "delvis") assert.equal(Math.round(d.procentUtanfor), 50);
  assert.deepEqual(placering([2600, 2600, 2700, 2700], geo), { slag: "utanfor" });
  assert.equal(placeringText(d, geo.gransen_3011).nivå, "varning");
  assert.equal(placeringText({ slag: "utanfor" }, geo.gransen_3011).nivå, "fel");
  assert.match(placeringText({ slag: "inom" }, geo.gransen_3011).text, /inom/);
});

test("formuläret nekar tomt namn, personuppgifter, för liten yta och yta utanför gränsen", () => {
  const ok = { geografi: geo, namn: "Haga", avgransning: [1100, 1100, 1200, 1200] as [number, number, number, number], organisation: null, flera: false };
  assert.deepEqual(formularFel(ok), []);
  assert.match(formularFel({ ...ok, namn: " " }).join(), /namn/);
  assert.match(formularFel({ ...ok, namn: "anna@example.test" }).join(), /personuppgifter/);
  assert.match(formularFel({ ...ok, namn: "Test 19121212-1234" }).join(), /personuppgifter/);
  assert.deepEqual(formularFel({ ...ok, namn: "Älvsjö 1:1" }), []);
  assert.match(formularFel({ ...ok, avgransning: [0, 0, 5, 5] }).join(), /för liten/);
  assert.match(formularFel({ ...ok, avgransning: [2600, 2600, 2700, 2700] }).join(), /yttre gräns/);
  assert.match(formularFel({ ...ok, avgransning: null }).join(), /Rita/);
  assert.match(formularFel({ ...ok, geografi: null }).join(), /geografi/);
  assert.match(formularFel({ ...ok, flera: true }).join(), /organisation/);
});

test("begäran bär geografins ref, trimmat namn, delningen och valfri organisation", () => {
  const b = skapaBegaran(geo, " Haga ", [1, 2, 3, 4], true, null);
  assert.deepEqual(b, { geografi: "goteborg-test@v1", namn: "Haga", avgransning: [1, 2, 3, 4], organisation_synlig: true });
  assert.equal(skapaBegaran(geo, "x", [1, 2, 3, 4], false, "org1").organisation, "org1");
  assert.match(arealText([0, 0, 100, 50]), /100 × 50 m/);
});
