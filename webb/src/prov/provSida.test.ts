import assert from "node:assert/strict";
import { test } from "node:test";
import { provSidaHtml, talText, type ProvResultatRad, type ProvTillstand } from "./provSida.ts";
import { provTillgang } from "./provtillgang.ts";
import { MEDDELANDE_INGEN_ADAPTER, MEDDELANDE_SAKNAS, ryms, upptackaWebGpu } from "./webgpuStatus.ts";
import type { WebGpuStatus } from "./webgpuStatus.ts";

const GRUND: ProvTillstand = {
  tillgang: "tillaten",
  webgpu: "laddar",
  fall: [{ id: "a", typ: "dagsljus", namn: "Dagsljus <test>", beskrivning: "Beskrivning" }],
  resultat: [],
  problem: [],
  pagar: null,
  fel: null,
  upprepningar: 5,
  toleranser: { "a:svf": 0.01 },
};

const MED_GPU: WebGpuStatus = {
  tillganglig: true,
  meddelande: "WebGPU finns. Provet kan köras.",
  adapter: { vendor: "nvidia", architecture: "ampere", device: "", description: "RTX" },
  begransningar: { maxBufferSize: 2 ** 30, maxStorageBufferBindingSize: 2 ** 27 },
  funktioner: ["timestamp-query"],
};

test("utan WebGPU visas ett tydligt meddelande och knapparna är avstängda", async () => {
  const status = await upptackaWebGpu(undefined);
  assert.equal(status.tillganglig, false);
  assert.equal(status.meddelande, MEDDELANDE_SAKNAS);
  const html = provSidaHtml({ ...GRUND, webgpu: status });
  assert.match(html, /Ingen WebGPU/);
  assert.match(html, /WebGPU saknas i den här webbläsaren/);
  assert.match(html, /role="alert"/);
  for (const knapp of ["kor-dagsljus", "kor-vatten", "kor-alla"]) {
    assert.match(html, new RegExp(`data-prov="${knapp}" disabled`));
  }
  // Nedladdningen är alltid möjlig: den ger ett dokument med fallen markerade som hoppade över.
  assert.doesNotMatch(html, /data-prov="ladda-ned" disabled/);
});

test("navigator.gpu finns men ger ingen adapter, eller kastar", async () => {
  const ingen = await upptackaWebGpu({ gpu: { requestAdapter: async () => null } });
  assert.equal(ingen.tillganglig, false);
  assert.equal(ingen.meddelande, MEDDELANDE_INGEN_ADAPTER);
  const kastar = await upptackaWebGpu({
    gpu: { requestAdapter: async () => Promise.reject(new Error("blockerad")) },
  });
  assert.equal(kastar.tillganglig, false);
  assert.match(kastar.meddelande, /blockerad/);
  assert.equal(await upptackaWebGpu({}).then((s) => s.tillganglig), false);
});

test("med adapter läses uppgifter och begränsningar", async () => {
  const s = await upptackaWebGpu({
    gpu: {
      requestAdapter: async () => ({
        info: { vendor: "apple", architecture: "metal-3", device: "M2", description: "" },
        limits: { maxBufferSize: 1000, maxStorageBufferBindingSize: 500, okandGrans: 1, maxBindGroups: 4 },
        features: new Set(["shader-f16", "depth-clip-control"]),
      }),
    },
  });
  assert.equal(s.tillganglig, true);
  assert.equal(s.adapter?.vendor, "apple");
  assert.deepEqual(s.begransningar, { maxBufferSize: 1000, maxStorageBufferBindingSize: 500, maxBindGroups: 4 });
  assert.deepEqual(s.funktioner, ["depth-clip-control", "shader-f16"]);
  assert.equal(ryms(s, 500), true);
  assert.equal(ryms(s, 501), false);
  assert.equal(ryms({ ...s, begransningar: null }, 1), false);
});

test("med WebGPU är knapparna på och adaptern visas", () => {
  const html = provSidaHtml({ ...GRUND, webgpu: MED_GPU });
  assert.doesNotMatch(html, /data-prov="kor-alla" disabled/);
  assert.match(html, /nvidia/);
  assert.match(html, /maxStorageBufferBindingSize/);
});

test("pågående körning stänger knapparna", () => {
  const html = provSidaHtml({ ...GRUND, webgpu: MED_GPU, pagar: "Kör a …" });
  assert.match(html, /data-prov="kor-alla" disabled/);
  assert.match(html, /Kör a …/);
});

test("skissläget står alltid på sidan och text från servern och fel escapas", () => {
  const html = provSidaHtml({ ...GRUND, webgpu: MED_GPU, fel: "fel <b>x</b>" });
  assert.match(html, /Skissläge/);
  assert.match(html, /f32/);
  assert.match(html, /Dagsljus &lt;test&gt;/);
  assert.doesNotMatch(html, /<b>x<\/b>/);
});

test("resultattabellen visar tid, fel, andel över tolerans och utfall", () => {
  const ok: ProvResultatRad = {
    fallId: "dagsljus-kvarter300", falt: "svf", enhet: "andel", medianMs: 12.5, minMs: 11, maxMs: 15, n: 5,
    forstaMs: 80, storstaFel: 0.002, medelFel: 0.0001, andelOverTolerans: 0, tolerans: 0.01,
    antalIckeAndliga: 0, godkand: true, serverMs: 5000,
  };
  const over: ProvResultatRad = { ...ok, falt: "soltimmar_h", godkand: false, andelOverTolerans: 0.0123, antalIckeAndliga: 3 };
  const html = provSidaHtml({ ...GRUND, webgpu: MED_GPU, resultat: [ok, over] });
  assert.match(html, /12,5 ms/);
  assert.match(html, /5000 ms/);
  assert.match(html, /inom toleransen/);
  assert.match(html, /ÖVER toleransen/);
  assert.match(html, /1,23 %/);
  assert.match(html, /3 icke-ändliga/);
  assert.match(html, /class="prov-over"/);
});

test("talText: svensk decimalkomma, små tal i exponentform, icke-ändligt", () => {
  assert.equal(talText(0), "0");
  assert.equal(talText(0.5), "0,500");
  assert.equal(talText(0.00001234), "1,23e-5");
  assert.equal(talText(5000.4), "5000");
  assert.equal(talText(Number.NaN), "ej ändligt");
  assert.equal(talText(Number.POSITIVE_INFINITY), "ej ändligt");
});

test("sidan visas inte för andra än superadmin", () => {
  for (const t of ["forbjuden", "ej-inloggad", "okand"] as const) {
    const html = provSidaHtml({ ...GRUND, tillgang: t, webgpu: MED_GPU });
    assert.doesNotMatch(html, /data-prov="kor-alla"/);
    assert.doesNotMatch(html, /Kör dagsljus/);
    assert.match(html, /prov-forbjuden/);
  }
  assert.match(provSidaHtml({ ...GRUND, tillgang: "forbjuden" }), /bara för superadmin/);
});

test("provTillgang: lokalt öppen, med inloggning bara superadmin", () => {
  assert.equal(provTillgang(200, { inloggning: "av" }), "tillaten");
  assert.equal(provTillgang(200, { inloggning: "pa", konto: { superadmin: true } }), "tillaten");
  assert.equal(provTillgang(200, { inloggning: "pa", konto: { superadmin: false } }), "forbjuden");
  assert.equal(provTillgang(200, { inloggning: "pa", konto: {} }), "forbjuden");
  assert.equal(provTillgang(401, null), "ej-inloggad");
  assert.equal(provTillgang(500, null), "okand");
  assert.equal(provTillgang(200, null), "okand");
});
