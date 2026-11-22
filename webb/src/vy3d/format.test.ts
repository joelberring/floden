import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { FormatFel, lasBehallare } from "./format.ts";

// prov.fl3d är skriven av servern (webb.vy3d_format.kapsla); testet i Python bygger samma bytes.
const fil = readFileSync(new URL("./provdata/prov.fl3d", import.meta.url));
const buf = (): ArrayBuffer => fil.buffer.slice(fil.byteOffset, fil.byteOffset + fil.byteLength) as ArrayBuffer;

test("läser en behållare skriven av servern, alla fyra talslagen", () => {
  const { huvud, delar } = lasBehallare(buf());
  assert.equal(huvud.typ, "prov");
  assert.equal((huvud as unknown as { x: number }).x, 1.5);
  assert.deepEqual(Array.from(delar.a as Float32Array), [0.5, 1.5, -2]);
  assert.deepEqual(Array.from(delar.b as Uint16Array), [1, 2, 3, 4, 5, 65535]);
  assert.deepEqual(Array.from(delar.c as Uint8Array), [7, 8, 9]);
  assert.deepEqual(Array.from(delar.d as Uint32Array), [1, 4000000000]);
  assert.deepEqual(huvud.delar.find((d) => d.namn === "b")?.form, [2, 3]);
});

test("delarna är vyer över samma buffert, utan kopiering", () => {
  const b = buf();
  const { delar } = lasBehallare(b);
  assert.equal((delar.a as Float32Array).buffer, b);
});

test("nekar fel magi, avkortat svar och fel format", () => {
  assert.throws(() => lasBehallare(new ArrayBuffer(4)), FormatFel);
  const dalig = new Uint8Array(buf());
  dalig[0] = 0x58;
  assert.throws(() => lasBehallare(dalig.buffer as ArrayBuffer), /FL3D/);
  assert.throws(() => lasBehallare(buf().slice(0, buf().byteLength - 10)), FormatFel);
});
