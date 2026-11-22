import assert from "node:assert/strict";
import { test } from "node:test";
import type { Teckenforklaring } from "../fragor-typer.ts";
import { Color, ColorManagement, LinearSRGBColorSpace, NoToneMapping } from "three";
import { stallInRenderare } from "./farghantering.ts";
import { fargaCeller, fargFor, punktFarger, VSC_TECKENFORKLARING } from "./farger.ts";

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

test("punktFarger: samma RGB som fargFor och teckenförklaringen, inte linjäriserat", () => {
  const v = [0, 10, 16.4, 27, 40, Number.NaN];
  const f = punktFarger(VSC_TECKENFORKLARING, v);
  for (let i = 0; i < 5; i++) {
    const c = fargFor(VSC_TECKENFORKLARING, v[i] as number);
    assert.deepEqual([f[i * 3], f[i * 3 + 1], f[i * 3 + 2]].map((x) => Math.round((x as number) * 255)), [c[0], c[1], c[2]]);
  }
  assert.deepEqual([f[15], f[16], f[17]].map((x) => Math.round((x as number) * 255)), [154, 154, 154]);
  // Mörkt blått stannar mörkt: en sRGB till linjär-omräkning skulle ge 0 för (0, 32, 77) men 8 på blå kanalen -> inte här.
  assert.equal(Math.round((f[1] as number) * 255), 32);
});

test("renderarens färghantering är av: en färg i 3D är exakt den i 2D", () => {
  // farghantering.ts slår av färghanteringen när den läses in; annars tolkar three.js vertexfärger som
  // linjära och kodar om dem till sRGB vid utdata (mörka färger blir ljusa).
  assert.equal(ColorManagement.enabled, false);
  const r = { outputColorSpace: "srgb", toneMapping: 4 };
  stallInRenderare(r);
  assert.equal(r.outputColorSpace, LinearSRGBColorSpace);
  assert.equal(r.toneMapping, NoToneMapping);
  const c = new Color().setRGB(32 / 255, 77 / 255, 140 / 255);
  assert.equal(Math.round(c.r * 255), 32);
  assert.equal(Math.round(c.g * 255), 77);
  assert.equal(Math.round(c.b * 255), 140);
  assert.equal(new Color(0x00204d).g, 32 / 255);
});
