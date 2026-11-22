import assert from "node:assert/strict";
import { test } from "node:test";
import { swerefTillWgs84, wgs84TillSweref } from "../sweref.ts";
import { avstandFranMpp, franScen, kameraFranKarta, kameraPosition, kameraVinklar, meterPerPxFranZoom, mppFranAvstand, mppFranKamera, tillScen, zoomFranMeterPerPx } from "./koord.ts";

const O = { e: 151350, n: 6580350, z: 11 };

test("scenkoordinater: öster är +x, upp är +y, norr är -z, och de går att vända", () => {
  const p = tillScen(151360, 6580370, 14, O);
  assert.deepEqual(p, { x: 10, y: 3, z: -20 });
  const t = franScen(p, O);
  assert.deepEqual(t, { e: 151360, n: 6580370, z: 14 });
});

test("zoom och meter per pixel går att vända och halveras per zoomsteg", () => {
  for (const [zoom, lat] of [[15, 59.3], [17.4, 59.34], [3, 0]] as const) {
    assert.ok(Math.abs(zoomFranMeterPerPx(meterPerPxFranZoom(zoom, lat), lat) - zoom) < 1e-9);
  }
  assert.ok(Math.abs(meterPerPxFranZoom(16, 59.3) * 2 - meterPerPxFranZoom(15, 59.3)) < 1e-9);
  // Känt värde: zoom 0 på ekvatorn ger 40 075 016,686 / 512 m per pixel.
  assert.ok(Math.abs(meterPerPxFranZoom(0, 0) - 78271.5170) < 1e-3);
});

test("kameran ser samma bredd som kartan: avstånd och meter per pixel går att vända", () => {
  const mpp = 0.42;
  for (const [h, fov] of [[600, 40], [1000, 35], [400, 60]] as const) {
    const d = avstandFranMpp(mpp, h, fov);
    assert.ok(Math.abs(mppFranAvstand(d, h, fov) - mpp) < 1e-12);
    // Bildens höjd i meter vid måltavlan är pixelhöjden gånger mpp.
    assert.ok(Math.abs(2 * d * Math.tan((fov * Math.PI) / 360) - h * mpp) < 1e-9);
  }
});

test("synk 2D till 3D till 2D ger tillbaka kartans mitt och zoom (via riktiga SWEREF-omräkningen)", () => {
  const mitt = { lng: 18.0254, lat: 59.3382, zoom: 17.2 };
  const [e, n] = wgs84TillSweref(mitt.lng, mitt.lat);
  const mpp = meterPerPxFranZoom(mitt.zoom, mitt.lat);
  const k = kameraFranKarta({ e, n, z: 12 }, mpp, 640, 40);
  assert.equal(k.lutning, 0);
  assert.equal(k.azimut, 0);
  // Rakt uppifrån ligger kameran ovanför måltavlan.
  const p = kameraPosition(k, O);
  assert.ok(Math.abs(p.x - (e - O.e)) < 1e-6 && Math.abs(p.z + (n - O.n)) < 1e-6 && p.y > 0);
  const tillbaka = mppFranKamera({ ...k, lutning: 55, azimut: 30 }, 640, 40);
  const [lng, lat] = swerefTillWgs84(k.mal.e, k.mal.n);
  assert.ok(Math.abs(lng - mitt.lng) < 1e-8 && Math.abs(lat - mitt.lat) < 1e-8);
  assert.ok(Math.abs(zoomFranMeterPerPx(tillbaka, lat) - mitt.zoom) < 1e-6);
});

test("kamerans position och vinklar är varandras omvändning", () => {
  for (const [azimut, lutning, avstand] of [[0, 0, 300], [0, 55, 300], [90, 45, 120], [215, 70, 80], [359, 10, 1000]] as const) {
    const k = { mal: { e: 151400, n: 6580400, z: 12 }, avstand, azimut, lutning };
    const p = kameraPosition(k, O);
    const mal = tillScen(k.mal.e, k.mal.n, k.mal.z, O);
    const v = kameraVinklar(p, mal);
    assert.ok(Math.abs(v.avstand - avstand) < 1e-6);
    assert.ok(Math.abs(v.lutning - lutning) < 1e-6, `${lutning}`);
    if (lutning > 1) assert.ok(Math.abs(((v.azimut - azimut + 540) % 360) - 180) < 1e-6, `${azimut}`);
  }
});

test("tittar kameran norrut ligger den söder om målet (norr är uppåt i bilden)", () => {
  const p = kameraPosition({ mal: { e: O.e, n: O.n, z: O.z }, avstand: 100, azimut: 0, lutning: 45 }, O);
  assert.ok(p.z > 0, "söder = +z");
  assert.ok(Math.abs(p.x) < 1e-9);
  const ost = kameraPosition({ mal: { e: O.e, n: O.n, z: O.z }, avstand: 100, azimut: 90, lutning: 45 }, O);
  assert.ok(ost.x < 0, "tittar österut: kameran ligger väster om målet");
});
