import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  BINDNINGAR,
  ENTRIES,
  arbetsgrupper,
  kontrolleraVattenIndata,
  packaVattenParams,
} from "./vattenLayout.ts";
import type { VattenGpuIndata } from "./vattenLayout.ts";

const WGSL = readFileSync(new URL("./shaders/vatten_2d.wgsl", import.meta.url), "utf-8").replace(/\/\/.*$/gm, "");

function funktion(namn: string): string {
  const start = WGSL.indexOf(`fn ${namn}(`);
  assert.ok(start >= 0, `saknar fn ${namn}`);
  let djup = 0;
  for (let i = WGSL.indexOf("{", start); i < WGSL.length; i += 1) {
    if (WGSL[i] === "{") djup += 1;
    if (WGSL[i] === "}") {
      djup -= 1;
      if (djup === 0) return WGSL.slice(start, i + 1);
    }
  }
  throw new Error("obalanserade klamrar");
}

test("bindgrupperna per entry point är exakt de bindningar skuggaren använder", () => {
  // layout: "auto" tar bara med bindningar som entry pointen når; en bindgrupp med fler ger valideringsfel.
  const deklarationer = [...WGSL.matchAll(/@binding\((\d+)\)\s+var(?:<[^>]*>)?\s+(\w+)/g)].map((m) => ({
    binding: Number(m[1]),
    namn: m[2] as string,
  }));
  assert.equal(deklarationer.length, 8);
  const hjalp = funktion("bates_punkt_implicit");
  for (const entry of ENTRIES) {
    let kropp = funktion(entry);
    if (/bates_punkt_implicit\(/.test(kropp)) kropp += hjalp;
    const anvanda = deklarationer
      .filter((d) => new RegExp(`\\b${d.namn}\\b`).test(kropp))
      .map((d) => d.binding)
      .sort((a, b) => a - b);
    assert.deepEqual(anvanda, [...BINDNINGAR[entry]], `bindningar för ${entry}`);
  }
});

test("uniformen packas som struct VattenParams (32 byte, u32 u32 och sex f32)", () => {
  const ind = { nx: 64, ny: 48, dx: 2, dy: 2, dt: 0.25, theta: 0, min_djup: 1e-4 } as VattenGpuIndata;
  const buf = packaVattenParams(ind, 3.3e-5);
  assert.equal(buf.byteLength, 32);
  const u = new Uint32Array(buf, 0, 2);
  const f = new Float32Array(buf, 8, 6);
  assert.deepEqual([...u], [64, 48]);
  assert.deepEqual([...f].slice(0, 4), [2, 2, 0.25, 0]);
  assert.ok(Math.abs((f[4] as number) - 1e-4) < 1e-10);
  assert.ok(Math.abs((f[5] as number) - 3.3e-5) < 1e-11);
});

test("arbetsgrupper följer hur skuggarna avgränsar sina index", () => {
  assert.deepEqual(arbetsgrupper("uppdatera_qx", 64, 64), [8, 8]); // 63 x 64
  assert.deepEqual(arbetsgrupper("uppdatera_qx", 65, 64), [8, 8]); // 64 x 64
  assert.deepEqual(arbetsgrupper("uppdatera_qx", 66, 64), [9, 8]); // 65 x 64
  assert.deepEqual(arbetsgrupper("uppdatera_qy", 64, 65), [8, 8]);
  assert.deepEqual(arbetsgrupper("uppdatera_qy", 64, 66), [8, 9]);
  assert.deepEqual(arbetsgrupper("begransa_och_volym", 9, 17), [2, 3]);
});

test("indata med fel längd eller för litet fält avvisas", () => {
  const ok = {
    nx: 2, ny: 2, dx: 1, dy: 1, dt: 0.1, theta: 0, min_djup: 1e-4,
    z_bed: new Float32Array(4), manning: new Float32Array(4), aktiv: new Uint32Array(4), steg: [],
  } satisfies VattenGpuIndata;
  kontrolleraVattenIndata(ok);
  assert.throws(() => kontrolleraVattenIndata({ ...ok, z_bed: new Float32Array(3) }), /z_bed/);
  assert.throws(() => kontrolleraVattenIndata({ ...ok, aktiv: new Uint32Array(5) }), /aktiv/);
  assert.throws(() => kontrolleraVattenIndata({ ...ok, nx: 1, ny: 4 }), /minst 2/);
});

test("dagsljusskuggaren använder alla sina fem bindningar (som WebGpuDagsljusMotor binder)", () => {
  const kod = readFileSync(new URL("./shaders/dagsljus_horisont.wgsl", import.meta.url), "utf-8").replace(/\/\/.*$/gm, "");
  const namn = [...kod.matchAll(/@binding\((\d+)\)\s+var(?:<[^>]*>)?\s+(\w+)/g)].map((m) => [Number(m[1]), m[2] as string] as const);
  assert.deepEqual(namn.map(([b]) => b), [0, 1, 2, 3, 4]);
  const entry = kod.slice(kod.indexOf("fn berakna_svf_och_soltimmar("));
  const hjalp = kod.slice(kod.indexOf("fn horisont_riktning("), kod.indexOf("@compute"));
  for (const [, n] of namn) assert.match(entry + hjalp, new RegExp(`\\b${n}\\b`), `${n} används inte`);
});
