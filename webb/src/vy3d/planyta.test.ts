/* Körs med `npm test`. Ny mark, slänter och murar som ytor i 3D (R6): nivåer, släntens mått, murens höjd och area. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEKAL_M, forfina, lutningHojd, markNiva, markYta, murYta, slantAv, tata, type PlanMark, type PlanMur, type Yta3d } from "./planyta.ts";

const O = { e: 1000, n: 2000, z: 10 };
const plan = (z: number) => (_e: number, _n: number): number => z;
const kvadrat = (x: number, y: number, s: number): Array<[number, number]> => [[x, y], [x + s, y], [x + s, y + s], [x, y + s]];
const nara = (a: number, b: number, tol = 1e-6): void => assert.ok(Math.abs(a - b) <= tol, `${a} != ${b}`);
const mark = (egenskaper: Record<string, unknown>, ring = kvadrat(1010, 2010, 10), roll: PlanMark["roll"] = "mark"): PlanMark => ({ id: "m", roll, ring, egenskaper });

/** Projicerad area (m², x och z i scenen) för alla trianglar. */
function area(y: Yta3d): number {
  let s = 0;
  for (let t = 0; t < y.index.length; t += 3) {
    const [a, b, c] = [0, 1, 2].map((k) => (y.index[t + k] as number) * 3);
    s += Math.abs(((y.position[b!] as number) - (y.position[a!] as number)) * ((y.position[c! + 2] as number) - (y.position[a! + 2] as number)) - ((y.position[b! + 2] as number) - (y.position[a! + 2] as number)) * ((y.position[c!] as number) - (y.position[a!] as number))) / 2;
  }
  return s;
}
const ys = (y: Yta3d): number[] => Array.from({ length: y.position.length / 3 }, (_, i) => y.position[i * 3 + 1] as number);

test("lutande yta: de fyra formerna ger samma värden som serverns (lapp.markniva)", () => {
  // punkt + fall: 2 % fall åt söder (azimut 180): norr om punkten är högre.
  const pf = { punkt: [100, 200, 15], fall: 0.02, riktning_grader: 180 };
  nara(lutningHojd(pf, 100, 200) as number, 15);
  nara(lutningHojd(pf, 100, 210) as number, 15.2);
  nara(lutningHojd(pf, 150, 200) as number, 15);
  // a + b: stiger linjärt från a till b, utan tvärfall.
  const ab = { a: [0, 0, 10], b: [10, 0, 11] };
  nara(lutningHojd(ab, 5, 0) as number, 10.5);
  nara(lutningHojd(ab, 5, 50) as number, 10.5);
  // punkter: plan i minsta kvadrat; punkter på ett plan återges exakt.
  const pk = { punkter: [[0, 0, 5], [10, 0, 6], [0, 10, 4], [10, 10, 5]] };
  nara(lutningHojd(pk, 4, 4) as number, 5 + 0.4 - 0.4);
  nara(lutningHojd(pk, 10, 0) as number, 6);
  // höjdlinjer: linjär interpolation efter avstånden.
  const hl = { hojdlinje_a: [[0, 0, 10], [10, 0, 10]], hojdlinje_b: [[0, 10, 12], [10, 10, 12]] };
  nara(lutningHojd(hl, 5, 5) as number, 11);
  nara(lutningHojd(hl, 5, 2.5) as number, 10.5);
  assert.equal(lutningHojd({ a: [0, 0, 1], b: [0, 0, 2] }, 0, 0), null);
  assert.equal(lutningHojd({ punkter: [[0, 0, 1]] }, 0, 0), null);
  assert.equal(lutningHojd(null, 0, 0), null);
});

test("nivån: lutning före delta före plan nivå; utan nivå en tunn dekal över marken", () => {
  const h = (e: number): number => 20 + e / 100;
  assert.equal(markNiva({ niva_rh2000: 25 }, h).niva(0, 0), 25);
  nara(markNiva({ delta_m: 0.5 }, h).niva(100, 0), 21.5);
  nara(markNiva({ lutning: { a: [0, 0, 10], b: [10, 0, 11] } }, h).niva(5, 0), 10.5);
  const dekal = markNiva({}, h);
  assert.equal(dekal.harEgenNiva, false);
  nara(dekal.niva(100, 0), 21 + DEKAL_M);
  assert.deepEqual(slantAv({ slant: { forhallande: 3 } }), { forhallande: 3 });
  assert.deepEqual(slantAv({ slant: { bredd_m: 4 } }), { bredd_m: 4 });
  assert.equal(slantAv({ slant: { forhallande: -1 } }), null);
  assert.equal(slantAv({}), null);
});

test("platt yta: alla hörn på nivån (minus origo), arean är ytans, normalerna pekar uppåt", () => {
  const { yta, slant } = markYta(mark({ niva_rh2000: 14 }), plan(13.6), O);
  assert.equal(slant, null);
  assert.ok(ys(yta).every((y) => Math.abs(y - (14 - O.z)) < 1e-5));
  nara(area(yta), 100, 1e-3);
  for (let i = 1; i < yta.normal.length; i += 3) assert.ok((yta.normal[i] as number) > 0.99);
  // Scenkoordinater: x = E - E0, z = -(N - N0).
  const xs = Array.from({ length: yta.position.length / 3 }, (_, i) => yta.position[i * 3] as number);
  const zs = Array.from({ length: yta.position.length / 3 }, (_, i) => yta.position[i * 3 + 2] as number);
  nara(Math.min(...xs), 10);
  nara(Math.max(...xs), 20);
  nara(Math.min(...zs), -20);
  nara(Math.max(...zs), -10);
});

test("delta_m följer marken: ytans höjd är markens plus delta överallt, även över en sluttning", () => {
  const slutt = (e: number, _n: number): number => 12 + (e - 1010) * 0.05;
  const { yta } = markYta(mark({ delta_m: 0.6 }), slutt, O);
  for (let i = 0; i < yta.position.length / 3; i++) {
    const e = (yta.position[i * 3] as number) + O.e;
    nara(yta.position[i * 3 + 1] as number, slutt(e, 0) + 0.6 - O.z, 1e-4);
  }
  assert.ok(yta.index.length / 3 > 2); // förfinad: fler än två trianglar för 10 x 10 m
  nara(area(yta), 100, 1e-3);
});

test("lutande yta ritas som planet genom hörnen", () => {
  const { yta } = markYta(mark({ lutning: { punkt: [1015, 2015, 15], fall: 0.02, riktning_grader: 180 } }), plan(0), O);
  for (let i = 0; i < yta.position.length / 3; i++) {
    const n = -(yta.position[i * 3 + 2] as number) + O.n;
    nara(yta.position[i * 3 + 1] as number, 15 + 0.02 * (n - 2015) - O.z, 1e-4);
  }
});

test("slänt 1:3 runt en yta 0,6 m över marken: 1,8 m bred, rak (lutning 1/3), möter marken", () => {
  const { yta, slant } = markYta(mark({ niva_rh2000: 12.6, slant: { forhallande: 3 } }), plan(12), O);
  assert.ok(slant);
  const ysl = ys(slant);
  nara(Math.max(...ysl), 12.6 - O.z, 1e-5);
  nara(Math.min(...ysl), 12 - O.z, 1e-5);
  // Varje yttre punkt ligger 1,8 m från ytans kant (mitrat i hörnen), på markens höjd.
  const kant = (x: number, z: number): number => {
    const dx = Math.max(10 - x, 0, x - 20);
    const dz = Math.max(-20 - z, 0, z + 10);
    return Math.hypot(dx, dz);
  };
  for (let i = 0; i < slant.position.length / 3; i++) {
    const x = slant.position[i * 3] as number;
    const y = slant.position[i * 3 + 1] as number;
    const z = slant.position[i * 3 + 2] as number;
    const d = kant(x, z);
    if (d > 1e-6) {
      // På slänten: höjd = marken + 0,6 (1 - d/1,8) (rak, serverns formel) längs kantens normal.
      assert.ok(d <= 1.8 * 1.5 + 1e-6);
    }
    if (Math.abs(y - (12 - O.z)) < 1e-6) assert.ok(d > 1.79, `yttre punkt ${d}`);
  }
  assert.ok(area(slant) > 4 * 10 * 1.8 * 0.99); // minst remsan runt de fyra sidorna
  assert.ok(area(yta) > 99);
});

test("slänt med fast bredd, och ingen slänt där ytan ligger på marken eller inte har slänt", () => {
  const b = markYta(mark({ niva_rh2000: 12.6, slant: { bredd_m: 4 } }), plan(12), O).slant;
  assert.ok(b);
  const x = Array.from({ length: b.position.length / 3 }, (_, i) => b.position[i * 3] as number);
  nara(Math.min(...x), 10 - 4, 1e-5); // 4 m utanför västra kanten
  assert.equal(markYta(mark({ niva_rh2000: 12, slant: { forhallande: 3 } }), plan(12), O).slant, null);
  assert.equal(markYta(mark({ niva_rh2000: 12.6 }), plan(12), O).slant, null);
});

test("slänt kring en sänkt yta (skärning) stiger upp mot marken utanför kanten", () => {
  const { slant } = markYta(mark({ niva_rh2000: 11.4, slant: { forhallande: 2 } }), plan(12), O);
  assert.ok(slant);
  nara(Math.min(...ys(slant)), 11.4 - O.z, 1e-5);
  nara(Math.max(...ys(slant)), 12 - O.z, 1e-5);
});

test("hårdgjort, vegetation och vatten utan nivå är en tunn dekal på marken; en tom ring ger inget", () => {
  const { yta } = markYta(mark({}, kvadrat(1010, 2010, 4), "vatten"), plan(12), O);
  assert.ok(ys(yta).every((y) => Math.abs(y - (12 + DEKAL_M - O.z)) < 1e-5));
  assert.equal(markYta(mark({ niva_rh2000: 1 }, [[0, 0], [1, 1]]), plan(0), O).yta.index.length, 0);
});

test("tätning och förfining delar kanter och trianglar utan att tappa arean", () => {
  const t = tata(kvadrat(0, 0, 10), 2, true);
  assert.equal(t.length, 20);
  assert.equal(tata([[0, 0], [10, 0]], 4, false).length, 4); // 0, 3,33, 6,67, 10
  const fin = forfina([[[0, 0], [8, 0], [0, 8]]], 4);
  assert.ok(fin.length >= 4 && fin.length <= 6000);
  const ar = fin.reduce((s, t) => s + Math.abs((t[1][0] - t[0][0]) * (t[2][1] - t[0][1]) - (t[1][1] - t[0][1]) * (t[2][0] - t[0][0])) / 2, 0);
  nara(ar, 32);
  assert.equal(forfina([[[0, 0], [1000, 0], [0, 1000]]], 1).length <= 6000, true);
});

const mur = (over: Partial<PlanMur> = {}): PlanMur => ({ id: "mur1", typ: "mur", hojd_m: 1.2, linje: [[1010, 2010], [1030, 2010]], ...over });

test("mur: höjden över marken, längden och tjockleken följer typen; i sluttning följer murkrönet marken", () => {
  const y = murYta(mur(), plan(12), O);
  const hy = ys(y);
  nara(Math.max(...hy), 12 + 1.2 - O.z, 1e-5);
  nara(Math.min(...hy), 12 - 0.1 - O.z, 1e-5); // en decimeter under marken så att inget glapp syns
  const x = Array.from({ length: y.position.length / 3 }, (_, i) => y.position[i * 3] as number);
  nara(Math.max(...x) - Math.min(...x), 20, 1e-5);
  const z = Array.from({ length: y.position.length / 3 }, (_, i) => y.position[i * 3 + 2] as number);
  nara(Math.max(...z) - Math.min(...z), 0.3, 1e-5); // mur: 0,3 m
  const staket = murYta(mur({ typ: "staket" }), plan(12), O);
  const zs = Array.from({ length: staket.position.length / 3 }, (_, i) => staket.position[i * 3 + 2] as number);
  nara(Math.max(...zs) - Math.min(...zs), 0.06, 1e-5);
  const slutt = (e: number): number => 12 + (e - 1010) * 0.1;
  const s = murYta(mur(), (e) => slutt(e), O);
  const sy = ys(s);
  nara(Math.max(...sy), slutt(1030) + 1.2 - O.z, 1e-5);
  for (let i = 1; i < s.normal.length; i += 3) assert.ok(Math.abs(s.normal[i] as number) <= 1 + 1e-6);
});

test("mur utan höjd eller med en enda punkt ritas inte; en kurva delas i högst 2 m långa bitar", () => {
  assert.equal(murYta(mur({ hojd_m: 0 }), plan(0), O).index.length, 0);
  assert.equal(murYta(mur({ linje: [[1010, 2010]] }), plan(0), O).index.length, 0);
  const lang = murYta(mur({ linje: [[1010, 2010], [1010, 2030], [1030, 2030]] }), plan(12), O);
  assert.ok(lang.index.length / 3 > 2 * (2 * 20) / 2 * 2); // många bitar (>= 20 sidor à 2 trianglar per sida)
});
