import assert from "node:assert/strict";
import { test } from "node:test";
import type { Teckenforklaring } from "../fragor-typer.ts";
import { fargaCeller, fargFor, VSC_TECKENFORKLARING } from "./farger.ts";

// Facit: serverns _sekventiell, _vatten och _skillnad (src/webb/resultatlager.py) för samma värden.
const SEK: Teckenforklaring = { typ: "kontinuerlig", min: 0, max: 12, stopp: ["#00204d", "#31446b", "#666970", "#958f78", "#cbba69", "#ffea46"] };
const VATTEN: Teckenforklaring = {
  typ: "klasser",
  klasser: [
    { fran: 0.02, farg: "#e4edf6", etikett: "" },
    { fran: 0.1, farg: "#c9d9ea", etikett: "" },
    { fran: 0.2, farg: "#7fa3c8", etikett: "" },
    { fran: 0.3, farg: "#4f7daa", etikett: "" },
    { fran: 0.5, farg: "#2b5d8c", etikett: "" },
  ],
};
const SKILLNAD: Teckenforklaring = { typ: "divergerande", min: -6, max: 6, troskel: 0.1, farg_minskning: "#2b5d8c", farg_okning: "#b4462b" };

test("sekventiell skala ger exakt serverns färger", () => {
  assert.deepEqual(fargFor(SEK, 0), [0, 32, 77, 255]);
  assert.deepEqual(fargFor(SEK, 3), [62, 77, 108, 255]);
  assert.deepEqual(fargFor(SEK, 6), [126, 124, 116, 255]);
  assert.deepEqual(fargFor(SEK, 12), [255, 234, 70, 255]);
  assert.deepEqual(fargFor(SEK, 13), [255, 234, 70, 255]); // över skalan klämmer
});

test("vattenklasser: under 2 cm ritas inget, sedan klasstrappan som i 2D", () => {
  assert.equal(fargFor(VATTEN, 0.01)[3], 0);
  assert.deepEqual(fargFor(VATTEN, 0.02), [228, 237, 246, 255]);
  assert.deepEqual(fargFor(VATTEN, 0.1), [201, 217, 234, 255]);
  assert.deepEqual(fargFor(VATTEN, 0.25), [127, 163, 200, 255]);
  assert.deepEqual(fargFor(VATTEN, 0.31), [79, 125, 170, 255]);
  assert.deepEqual(fargFor(VATTEN, 0.6), [43, 93, 140, 255]);
});

test("skillnadsskala: blå minskning, röd ökning, ofärgat under tröskeln", () => {
  assert.deepEqual(fargFor(SKILLNAD, -6), [43, 93, 140, 255]);
  assert.equal(fargFor(SKILLNAD, -0.05)[3], 0);
  assert.deepEqual(fargFor(SKILLNAD, -0.2), [189, 203, 216, 255]);
  assert.equal(fargFor(SKILLNAD, 0)[3], 0);
  assert.deepEqual(fargFor(SKILLNAD, 1), [221, 179, 169, 255]);
  assert.deepEqual(fargFor(SKILLNAD, 3), [204, 136, 119, 255]);
  assert.deepEqual(fargFor(SKILLNAD, -3), [119, 150, 179, 255]);
});

test("värden som saknas (NaN) ger ingen färg", () => {
  for (const tf of [SEK, VATTEN, SKILLNAD]) assert.equal(fargFor(tf, Number.NaN)[3], 0);
});

test("VSC-skalan går från 0 till 40 procent", () => {
  assert.equal(VSC_TECKENFORKLARING.typ, "kontinuerlig");
  assert.deepEqual(fargFor(VSC_TECKENFORKLARING, 0), [0, 32, 77, 255]);
  assert.deepEqual(fargFor(VSC_TECKENFORKLARING, 40), [255, 234, 70, 255]);
});

test("fargaCeller färgar en textur ur uppskalade värden och lämnar 'inget värde' genomskinligt", () => {
  const q = Uint16Array.from([0, 65534, 65535]);
  const ut = new Uint8Array(12);
  fargaCeller(q, 0, 12, SEK, ut);
  assert.deepEqual(Array.from(ut), [0, 32, 77, 255, 255, 234, 70, 255, 0, 0, 0, 0]);
});
