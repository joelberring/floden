import assert from "node:assert/strict";
import { test } from "node:test";
import { avkodaFalt, jamforFalt, JamforFel, median } from "./jamfor.ts";

test("jämförelsen ger största fel, medelfel och andel över toleransen", () => {
  const facit = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  const gpu = [0, 1, 2.5, 3, 4, 5, 6, 7.1, 8, 9]; // fel 0,5 i cell 2 och 0,1 i cell 7
  const j = jamforFalt(facit, gpu, 0.2);
  assert.equal(j.antal, 10);
  assert.ok(Math.abs(j.storstaFel - 0.5) < 1e-12);
  assert.equal(j.storstaFelIndex, 2);
  assert.ok(Math.abs(j.medelFel - 0.06) < 1e-12);
  assert.equal(j.antalOverTolerans, 1);
  assert.ok(Math.abs(j.andelOverTolerans - 0.1) < 1e-12);
  assert.equal(j.antalIckeAndliga, 0);
  assert.equal(j.tolerans, 0.2);
});

test("felet exakt på toleransen räknas inte som över", () => {
  const j = jamforFalt([1, 1], [1.5, 1], 0.5);
  assert.equal(j.antalOverTolerans, 0);
  assert.equal(j.andelOverTolerans, 0);
});

test("identiska fält ger noll fel och typade arrayer fungerar", () => {
  const a = new Float32Array([0.1, 0.2, 0.3]);
  const j = jamforFalt(a, new Float32Array(a), 0);
  assert.equal(j.storstaFel, 0);
  assert.equal(j.medelFel, 0);
  assert.equal(j.andelOverTolerans, 0);
  assert.equal(j.storstaFelIndex, -1);
});

test("NaN och oändligt från GPU:n räknas över toleransen och försvinner inte i medelvärdet", () => {
  const j = jamforFalt([1, 2, 3, 4], [1, Number.NaN, 3, Number.POSITIVE_INFINITY], 0.1);
  assert.equal(j.antalIckeAndliga, 2);
  assert.equal(j.antalOverTolerans, 2);
  assert.equal(j.andelOverTolerans, 0.5);
  assert.equal(j.storstaFel, Number.POSITIVE_INFINITY);
  assert.equal(j.medelFel, 0); // bara de ändliga cellerna ingår i medelfelet
  assert.equal(j.storstaFelIndex, 1);
  const alla = jamforFalt([1], [Number.NaN], 1);
  assert.ok(Number.isNaN(alla.medelFel));
  assert.equal(alla.andelOverTolerans, 1);
});

test("fel indata ger tydliga fel", () => {
  assert.throws(() => jamforFalt([1, 2], [1], 0.1), /2 celler.*1/);
  assert.throws(() => jamforFalt([], [], 0.1), JamforFel);
  assert.throws(() => jamforFalt([1], [1], -1), /tolerans/i);
  assert.throws(() => jamforFalt([1], [1], Number.NaN), /tolerans/i);
  assert.throws(() => jamforFalt([Number.NaN], [1], 1), /Facit/);
});

test("median", () => {
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([4, 1, 3, 2]), 2.5);
  assert.equal(median([7]), 7);
  assert.throws(() => median([]), JamforFel);
  assert.throws(() => median([1, Number.NaN]), JamforFel);
});

test("avkodaFalt läser base64 från servern (little endian) och kontrollerar längden", () => {
  const f = new Float32Array([1.5, -2.25, 0]);
  const b64 = Buffer.from(f.buffer).toString("base64");
  const ut = avkodaFalt({ dtyp: "float32", form: [3], base64: b64 });
  assert.deepEqual([...ut], [1.5, -2.25, 0]);
  const u8 = avkodaFalt({ dtyp: "uint8", form: [2, 2], base64: Buffer.from([1, 0, 0, 1]).toString("base64") });
  assert.deepEqual([...u8], [1, 0, 0, 1]);
  assert.throws(() => avkodaFalt({ dtyp: "float32", form: [4], base64: b64 }), JamforFel);
  assert.throws(() => avkodaFalt({ dtyp: "float64", form: [1], base64: b64 }), /dtyp/);
});

test("godkänt tillåter enstaka celler över toleransen men inga stora fel", async () => {
  const { godkand } = await import("./jamfor.ts");
  const bas = { antal: 90000, storstaFelIndex: 0, medelFel: 1e-6, antalOverTolerans: 13, antalIckeAndliga: 0, tolerans: 0.01 };
  // Macens prov 2026-11-23: 0,0126 i 0,0144 % av cellerna.
  assert.equal(godkand({ ...bas, storstaFel: 0.0126, andelOverTolerans: 0.000144 }), true);
  assert.equal(godkand({ ...bas, storstaFel: 0.0126, andelOverTolerans: 0.002 }), false);
  assert.equal(godkand({ ...bas, storstaFel: 0.03, andelOverTolerans: 0.00001 }), false);
  assert.equal(godkand({ ...bas, storstaFel: 0.005, andelOverTolerans: 0, antalIckeAndliga: 1 }), false);
});
