import assert from "node:assert/strict";
import { test } from "node:test";

import type { GeografiPost } from "../types.ts";
import { tolkaFlodeAdress, NYTT_PROJEKT_ADRESS } from "./adress.ts";
import {
  arealText, avgransningsYta, formularFel, omslutande, placering, placeringText, polygonFel, polygonYta, punkterPolygon, rektangelPolygon,
  sjalvkorsning, skapaBegaran, ytaInomRektangel,
} from "./nyttprojekt-logik.ts";

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

const L = [{ x: 1000, y: 1000 }, { x: 1100, y: 1000 }, { x: 1100, y: 1040 }, { x: 1040, y: 1040 }, { x: 1040, y: 1100 }, { x: 1000, y: 1100 }];
const FJARIL = [{ x: 1000, y: 1000 }, { x: 1100, y: 1100 }, { x: 1100, y: 1000 }, { x: 1000, y: 1100 }];

test("polygonens yta och självkorsning: L-formen är giltig, fjärilen korsar sig och får ett svenskt fel", () => {
  const p = punkterPolygon(L);
  assert.ok(p);
  assert.equal(polygonYta(p), 100 * 40 + 40 * 60);
  assert.equal(avgransningsYta([1000, 1000, 1100, 1100], p), 6400);
  assert.equal(avgransningsYta([1000, 1000, 1100, 1100], null), 10000);
  assert.equal(sjalvkorsning(L), null);
  const x = sjalvkorsning(FJARIL);
  assert.deepEqual(x && [Math.round(x.x), Math.round(x.y)], [1050, 1050]);
  assert.match(polygonFel(FJARIL) ?? "", /^Polygonen korsar sig själv nära E 1\s050, N 1\s050\. Rita om den/);
  assert.equal(polygonFel(L), null);
  assert.match(polygonFel([{ x: 0, y: 0 }, { x: 1, y: 1 }]) ?? "", /minst 3/);
  assert.match(polygonFel([{ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 2 }]) ?? "", /rak linje/);
  // Dubbelklickets upprepade hörn ska inte räknas som olika hörn.
  assert.equal(polygonFel([...L, L[5] as { x: number; y: number }]), null);
});

test("formuläret kontrollerar polygonen: yta och självkorsning, inte rektangeln runt den", () => {
  const ok = { geografi: geo, namn: "Haga", avgransning: [1000, 1000, 1100, 1100] as [number, number, number, number], organisation: null, flera: false };
  assert.deepEqual(formularFel({ ...ok, polygon: punkterPolygon(L), horn: L }), []);
  assert.match(formularFel({ ...ok, polygon: punkterPolygon(FJARIL), horn: FJARIL }).join(), /korsar sig själv/);
  // Tunn triangel: rektangeln är 100 x 100 m men polygonen har under 100 m².
  const tunn = [{ x: 1000, y: 1000 }, { x: 1100, y: 1100 }, { x: 1100, y: 1100.5 }];
  assert.match(formularFel({ ...ok, polygon: punkterPolygon(tunn), horn: tunn }).join(), /för liten/);
});

test("placering av en polygon räknar andelen av polygonens yta utanför kärnan, inte rektangelns", () => {
  const kvadratisk = [1100, 1100, 2100, 2100] as [number, number, number, number];
  // Triangel med rektangel som sticker ut ur kärnan (1000..2000) men vars yta inte gör det.
  const tri = punkterPolygon([{ x: 1100, y: 1100 }, { x: 1900, y: 1100 }, { x: 1100, y: 1900 }]);
  assert.ok(tri);
  assert.deepEqual(placering([1100, 1100, 1900, 1900], geo, tri), { slag: "inom" });
  // Rektangeln runt en triangel som går ut över kärnan: andelen följer polygonens yta.
  const ut = punkterPolygon([{ x: 1500, y: 1500 }, { x: 2500, y: 1500 }, { x: 1500, y: 2500 }]);
  assert.ok(ut);
  const d = placering([1500, 1500, 2500, 2500], geo, ut);
  assert.equal(d.slag, "delvis");
  // Triangeln är 1000 x 1000 / 2 = 500 000 m²; kärnan (1000..2000) innehåller hela kvadraten 500 x 500 = 250 000 m² av den.
  if (d.slag === "delvis") assert.equal(Math.round(d.procentUtanfor), 50);
  assert.equal(Math.round(ytaInomRektangel([[1500, 1500], [2500, 1500], [1500, 2500]], geo.karna_3011)), 250000);
  // Utan polygon gäller rektangeln, som förut.
  assert.deepEqual(placering(kvadratisk, geo).slag, "delvis");
  assert.deepEqual(placering([2600, 2600, 2700, 2700], geo, tri), { slag: "utanfor" });
});

test("ytan i texten är polygonens när avgränsningen är en polygon", () => {
  const p = punkterPolygon(L);
  assert.match(arealText([1000, 1000, 1100, 1100], p), /100 × 100 m, polygonens yta 6\s400 m²/);
  assert.doesNotMatch(arealText([1000, 1000, 1100, 1100]), /polygonens/);
});
