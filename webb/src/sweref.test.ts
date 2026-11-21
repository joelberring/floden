/* Körs med `npm test` (Nodes inbyggda testkörare). Facit: pyproj 3.7.0, EPSG:4326 -> EPSG:3011. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { swerefTillWgs84, wgs84TillSweref } from "./sweref.ts";

const FACIT: Array<[number, number, number, number]> = [
  [18.0, 59.33, 150000.0, 6579430.418140402],
  [18.0686, 59.3293, 153905.0928038008, 6579354.448318825],
  [17.2, 58.9, 103887.20836384202, 6531805.322301797],
  [19.5, 60.6, 232174.92247618735, 6721860.454321095],
];

test("WGS 84 till SWEREF 99 18 00 stämmer med pyproj inom 1 mm", () => {
  for (const [lon, lat, e, n] of FACIT) {
    const [e2, n2] = wgs84TillSweref(lon, lat);
    assert.ok(Math.abs(e2 - e) < 0.001, `E ${e2} mot ${e}`);
    assert.ok(Math.abs(n2 - n) < 0.001, `N ${n2} mot ${n}`);
  }
});

test("SWEREF till WGS 84 och tillbaka ger samma punkt", () => {
  for (const [, , e, n] of FACIT) {
    const [lon, lat] = swerefTillWgs84(e, n);
    const [e2, n2] = wgs84TillSweref(lon, lat);
    assert.ok(Math.abs(e2 - e) < 0.001 && Math.abs(n2 - n) < 0.001);
  }
});
