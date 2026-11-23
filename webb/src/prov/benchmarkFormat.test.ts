import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  BENCHMARK_FORMAT,
  fallFranJamforelse,
  hoppatFall,
  kontrolleraDokument,
  skapaBenchmarkDokument,
  SKISSLAGE_TEXT,
  tidObjekt,
} from "./benchmarkFormat.ts";
import { jamforFalt } from "./jamfor.ts";

const EXEMPEL = new URL("../../../tests/benchmark/exempel_webblasare.json", import.meta.url);

/** Samma dokument som tests/benchmark/exempel_webblasare.json; Python kontrollerar filen. */
export function exempelDokument() {
  const j = jamforFalt([0.5, 0.6, 0.7, 0.8], [0.5, 0.61, 0.7, 0.8], 0.02);
  const jSol = jamforFalt([1, 2, 3, 4], [1, 2, 3.5, 4], 0.25);
  const tider = [0.0123, 0.0119, 0.0125];
  return skapaBenchmarkDokument({
    kodversion: "abc123456789",
    skapadUtc: "2026-11-23T12:00:00Z",
    maskin: {
      cpu: null,
      karnor: 8,
      os: "MacIntel",
      python: null,
      numpy: null,
      rust_karna: null,
      gpu: { tillganglig: true, adapter: { vendor: "apple", architecture: "metal-3", device: "", description: "" }, begransningar: { maxBufferSize: 1073741824 } },
      webblasare: { user_agent: "Exempel/1.0", sprak: "sv-SE", enhetsminne_gb: 8 },
    },
    installning: { uppvarmning: 1, upprepningar: 3, tidsmatt: "median", minnesmatt: [] },
    delar: {
      dagsljus: {
        status: "kord",
        skal: null,
        fall: [
          fallFranJamforelse({ id: "dagsljus-kvarter300:svf", namn: "Dagsljus (svf)", tiderSekunder: tider, enhet: "andel", jamforelse: j, beskrivning: "GPU (f32) mot server (f64)", minne: { gpu_buffrar_mb: 2.1 } }),
          fallFranJamforelse({ id: "dagsljus-kvarter300:soltimmar_h", namn: "Dagsljus (soltimmar_h)", tiderSekunder: tider, enhet: "h", jamforelse: jSol, beskrivning: "GPU (f32) mot server (f64)", minne: { gpu_buffrar_mb: 2.1 } }),
        ],
      },
      skyfall: {
        status: "hoppas_over",
        skal: "Provet av vatten 2D kördes inte.",
        fall: [hoppatFall("vatten2d-lutande-plan", "Vatten 2D", "Provet av vatten 2D kördes inte.")],
      },
    },
  });
}

test("dokumentet följer floden-benchmark/v1 med källa webblasare och skisslägestext", () => {
  const d = exempelDokument();
  assert.equal(d.format, BENCHMARK_FORMAT);
  assert.equal(d.kalla, "webblasare");
  assert.equal(d.anmarkning, SKISSLAGE_TEXT);
  assert.match(d.anmarkning, /Skissläge/);
  assert.deepEqual(kontrolleraDokument(d), []);
});

test("avvikelsen bär största fel, tolerans och godkänt-flagga", () => {
  const d = exempelDokument();
  const [svf, sol] = d.delar.dagsljus!.fall;
  assert.ok(Math.abs((svf!.avvikelse!.varde) - 0.01) < 1e-9);
  assert.equal(svf!.avvikelse!.enhet, "andel");
  assert.equal(svf!.avvikelse!.grans, 0.02);
  assert.equal(svf!.avvikelse!.godkand, true);
  assert.equal(svf!.avvikelse!.matt.antal_celler, 4);
  assert.equal(svf!.avvikelse!.matt.andel_over_tolerans, 0);
  assert.equal(sol!.avvikelse!.godkand, false); // 0,5 h > 0,25 h
  assert.equal(sol!.avvikelse!.matt.andel_over_tolerans, 0.25);
  assert.equal(svf!.tid_s!.median, 0.0123);
  assert.equal(svf!.tid_s!.n, 3);
});

test("icke-ändligt GPU-resultat ger underkänt fall som ändå går att skriva som JSON", () => {
  const j = jamforFalt([1, 2], [1, Number.NaN], 0.1);
  const f = fallFranJamforelse({ id: "x", namn: "x", tiderSekunder: [0.1], enhet: "m", jamforelse: j, beskrivning: "" });
  assert.equal(f.avvikelse!.godkand, false);
  assert.ok(Number.isFinite(f.avvikelse!.varde));
  JSON.parse(JSON.stringify(f)); // inga NaN/Infinity i dokumentet
  assert.equal(f.avvikelse!.matt.antal_ickeandliga, 1);
});

test("tidObjekt", () => {
  const t = tidObjekt([3, 1, 2]);
  assert.deepEqual({ ...t, alla: undefined }, { median: 2, min: 1, max: 3, n: 3, alla: undefined });
});

test("ogiltiga dokument avvisas", () => {
  const bas = JSON.parse(JSON.stringify(exempelDokument()));
  const med = (andra: (d: any) => void): string[] => {
    const d = JSON.parse(JSON.stringify(bas));
    andra(d);
    return kontrolleraDokument(d);
  };
  assert.match(med((d) => (d.format = "x"))[0]!, /format/);
  assert.match(med((d) => (d.kalla = "moln"))[0]!, /kalla/);
  assert.match(med((d) => delete d.kodversion)[0]!, /kodversion/);
  assert.ok(med((d) => (d.delar.skyfall.skal = null)).some((t) => /skal/.test(t)));
  assert.ok(med((d) => (d.delar.dagsljus.fall[0].tid_s.median = Number.NaN)).some((t) => /median/.test(t)));
  assert.ok(med((d) => delete d.delar.dagsljus.fall[0].id).some((t) => /id/.test(t)));
  assert.ok(med((d) => (d.delar.dagsljus.fall[0].avvikelse.varde = "x")).some((t) => /varde/.test(t)));
  assert.deepEqual(kontrolleraDokument(null), ["Dokumentet måste vara ett objekt."]);
  assert.throws(() => skapaBenchmarkDokument({ kodversion: "", maskin: {}, installning: {}, delar: {} }), /kodversion/);
});

test("exempelfilen som Python också kontrollerar är identisk med det här dokumentet", () => {
  const fil = JSON.parse(readFileSync(EXEMPEL, "utf-8"));
  assert.deepEqual(fil, JSON.parse(JSON.stringify(exempelDokument())));
});
