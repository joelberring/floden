import assert from "node:assert/strict";
import { test } from "node:test";
import { fargFor } from "./farger.ts";
import { byggnaderFran, extrudera, Hojdfalt, husFranBuffert, markVarde, STATUS_ANDRAD, STATUS_NY, terrangNat, vattenYta, type ByggnaderBuf, type MarkRuta, type TerrangRuta } from "./geometri.ts";
import { CELLER, INGET_VARDE } from "./rutnat.ts";
import { lasBehallare } from "./format.ts";

const O = { e: 1000, n: 2000, z: 10 };
const N = CELLER + 1;

function plan(fn: (e: number, n: number) => number, rut = { lod: 0, tx: 8, ty: 15 }): TerrangRuta {
  const x0 = rut.tx * 128;
  const y0 = rut.ty * 128;
  const z = new Float32Array(N * N);
  for (let r = 0; r < N; r++) for (let i = 0; i < N; i++) z[r * N + i] = fn(x0 + i, y0 + (CELLER - r));
  return { rut, x0, y0, cell: 1, z };
}

function triAreal(pos: Float32Array, idx: Uint32Array, filter: (i: number) => boolean = () => true): number {
  let a = 0;
  for (let t = 0; t < idx.length / 3; t++) {
    if (!filter(t)) continue;
    const [i, j, k] = [idx[t * 3], idx[t * 3 + 1], idx[t * 3 + 2]].map((v) => (v as number) * 3) as [number, number, number];
    const u = [pos[j]! - pos[i]!, pos[j + 1]! - pos[i + 1]!, pos[j + 2]! - pos[i + 2]!];
    const v = [pos[k]! - pos[i]!, pos[k + 1]! - pos[i + 1]!, pos[k + 2]! - pos[i + 2]!];
    a += 0.5 * Math.hypot(u[1]! * v[2]! - u[2]! * v[1]!, u[2]! * v[0]! - u[0]! * v[2]!, u[0]! * v[1]! - u[1]! * v[0]!);
  }
  return a;
}

test("terrängnätet lägger punkter i scenkoordinater: öster +x, norr -z, höjd relativt Z0", () => {
  const t = plan((e, n) => 20 + 0.01 * (e - 1000) + 0.02 * (n - 2000));
  const g = terrangNat(t, O);
  // Punkt (rad 0, kol 0) är rutans nordvästhörn: E = 1024, N = 1920 + 128 = 2048.
  assert.deepEqual([g.position[0], g.position[1], g.position[2]].map((v) => Math.round((v as number) * 1e4) / 1e4), [24, 20 + 0.24 + 0.96 - 10, -48]);
  // Sista punkten (rad 128, kol 128) är sydöst: E = 1152, N = 1920.
  const k = (N * N - 1) * 3;
  assert.ok(Math.abs((g.position[k] as number) - 152) < 1e-4 && Math.abs((g.position[k + 2] as number) - 80) < 1e-4);
  // UV: nordväst (0, 0), sydöst (1, 1).
  assert.deepEqual([g.uv[0], g.uv[1]], [0, 0]);
  assert.deepEqual([g.uv[(N * N - 1) * 2], g.uv[(N * N - 1) * 2 + 1]], [1, 1]);
});

test("ytan av ett plant nät är rutans yta, utan sprickor (kjolen räknas inte)", () => {
  const g = terrangNat(plan(() => 20), O, 1);
  const ytaplan = triAreal(g.position, g.index, (t) => {
    const i = g.index[t * 3] as number;
    return i < N * N && (g.index[t * 3 + 1] as number) < N * N && (g.index[t * 3 + 2] as number) < N * N;
  });
  assert.ok(Math.abs(ytaplan - 128 * 128) < 1e-3);
  // Kjolen ligger under kanten: alla kjolpunkter är exakt 1 m under.
  for (let p = N * N; p < g.position.length / 3; p++) assert.ok(Math.abs((g.position[p * 3 + 1] as number) - (20 - 10 - 1)) < 1e-5);
});

test("celler utan höjd (utanför projektet) får ingen yta", () => {
  const t = plan(() => 20);
  for (let r = 0; r < N; r++) for (let i = 64; i < N; i++) t.z[r * N + i] = Number.NaN;
  const g = terrangNat(t, O);
  const ytan = triAreal(g.position, g.index, (tr) => (g.index[tr * 3] as number) < N * N && (g.index[tr * 3 + 1] as number) < N * N && (g.index[tr * 3 + 2] as number) < N * N);
  assert.ok(Math.abs(ytan - 128 * 63) < 1e-3, `${ytan}`);
});

test("höjdfältet ger ett lutande plan exakt och föredrar den finaste rutan", () => {
  const f = new Hojdfalt();
  const fn = (e: number, n: number): number => 20 + 0.01 * (e - 1000) + 0.02 * (n - 2000);
  f.lagg(plan(fn));
  assert.ok(Math.abs(f.hojd(1037.37, 1950.81) - fn(1037.37, 1950.81)) < 1e-4);
  assert.ok(Number.isNaN(f.hojd(5, 5)));
  // En grövre ruta som täcker samma punkt används inte när en finare finns.
  const grov = plan(() => 99, { lod: 1, tx: 4, ty: 7 });
  f.lagg({ ...grov, cell: 2 });
  assert.ok(Math.abs(f.hojd(1037.37, 1950.81) - fn(1037.37, 1950.81)) < 1e-4);
});

test("extrudering: väggarnas yta är omkrets gånger höjd, taket är fotavtryckets yta, hål räknas av", () => {
  const kvadrat = (s: number, x = 0, z = 0): Array<[number, number]> => [[x, z], [x + s, z], [x + s, z + s], [x, z + s]];
  const g = extrudera([{ ringar: [kvadrat(10)], yFot: 0, yTak: 5 }]);
  const takIdx = (t: number): boolean => (g.position[(g.index[t * 3] as number) * 3 + 1] as number) === 5 && (g.normal[(g.index[t * 3] as number) * 3 + 1] as number) === 1;
  assert.ok(Math.abs(triAreal(g.position, g.index, takIdx) - 100) < 1e-4);
  assert.ok(Math.abs(triAreal(g.position, g.index, (t) => !takIdx(t)) - 4 * 10 * 5) < 1e-4);
  const medHal = extrudera([{ ringar: [kvadrat(10), kvadrat(4, 3, 3).reverse()], yFot: 0, yTak: 5 }]);
  const tak2 = (t: number): boolean => (medHal.normal[(medHal.index[t * 3] as number) * 3 + 1] as number) === 1;
  assert.ok(Math.abs(triAreal(medHal.position, medHal.index, tak2) - (100 - 16)) < 1e-4);
  // Väggar finns också runt hålet (innergård): 4 · 10 · 5 + 4 · 4 · 5.
  assert.ok(Math.abs(triAreal(medHal.position, medHal.index, (t) => !tak2(t)) - (200 + 80)) < 1e-4);
});

test("varje triangel vet vilket hus den hör till, och väggarna har utåtriktade normaler", () => {
  const hus = [
    { ringar: [[[0, 0], [10, 0], [10, 10], [0, 10]] as Array<[number, number]>], yFot: 0, yTak: 5 },
    { ringar: [[[20, 0], [30, 0], [30, 10], [20, 10]] as Array<[number, number]>], yFot: 0, yTak: 8 },
  ];
  const g = extrudera(hus);
  assert.equal(g.triHus.length, g.index.length / 3);
  assert.deepEqual([...new Set(g.triHus)].sort(), [0, 1]);
  // Varje vägg: normalen pekar bort från husets mitt.
  for (let t = 0; t < g.index.length / 3; t++) {
    const i = (g.index[t * 3] as number) * 3;
    if (g.normal[i + 1] !== 0) continue;
    const hi = g.triHus[t] as number;
    const cx = hi === 0 ? 5 : 25;
    const ut = ((g.position[i] as number) - cx) * (g.normal[i] as number) + ((g.position[i + 2] as number) - 5) * (g.normal[i + 2] as number);
    assert.ok(ut > 0, `vägg ${t} pekar inåt`);
  }
});

function bygg(): ByggnaderBuf {
  // Två hus: ett nytt 10 x 10 m med tak 8 m över noll och ett höjt, 6 x 6, förr 4 nu 9 m.
  return {
    antal: 2,
    ids: ["b_aaaa", "b_bbbb"],
    origo: O,
    punkter: Float32Array.from([0, 0, 10, 0, 10, 10, 0, 10, 30, 0, 36, 0, 36, 6, 30, 6]),
    ringstart: Uint32Array.from([0, 4, 8]),
    byggnad_ring: Uint32Array.from([0, 1, 2]),
    z_fot: Float32Array.from([1, 1]),
    z_tak: Float32Array.from([8, 9]),
    z_tak_fore: Float32Array.from([8, 4]),
    hojd_m: Float32Array.from([7, 8]),
    status: Uint8Array.from([STATUS_NY, STATUS_ANDRAD]),
  };
}

test("husen ur en byggnadsbuffert: norr blir -z, urval och 'före'-höjd fungerar", () => {
  const b = bygg();
  const alla = husFranBuffert(b);
  assert.equal(alla.hus.length, 2);
  assert.deepEqual(alla.hus[0]?.ringar[0]?.[2], [10, -10]);
  const bara = husFranBuffert(b, (i) => b.status[i] === STATUS_ANDRAD, "fore");
  assert.deepEqual(bara.index, [1]);
  assert.equal(bara.hus[0]?.yTak, 4);
  assert.equal(husFranBuffert(b).hus[1]?.yTak, 9);
});

test("byggnaderFran läser en serverbehållare (ur det som servern skriver)", () => {
  assert.throws(() => byggnaderFran(lasBehallare(new Uint8Array([0]).buffer as ArrayBuffer)));
});

// -- vatten -----------------------------------------------------------------------------------

function skal(): { mark: MarkRuta; hojd: (e: number, n: number) => number; nivå: number } {
  // En skålformad sänka: terräng 20 m i kanten ned till 18 m i mitten; vatten står till nivå 19 m.
  const x0 = 1024;
  const y0 = 1920;
  const hojd = (e: number, n: number): number => 18 + 2 * Math.min(1, Math.hypot(e - (x0 + 64), n - (y0 + 64)) / 40);
  const nivå = 19;
  const lo = 0;
  const hi = 1.2;
  const q = new Uint16Array(CELLER * CELLER).fill(INGET_VARDE);
  for (let rad = 0; rad < CELLER; rad++) {
    for (let kol = 0; kol < CELLER; kol++) {
      const e = x0 + kol + 0.5;
      const n = y0 + (CELLER - 1 - rad) + 0.5;
      const djup = Math.max(0, nivå - hojd(e, n));
      q[rad * CELLER + kol] = Math.round(((djup - lo) / (hi - lo)) * 65534);
    }
  }
  return { mark: { rut: { lod: 0, tx: 8, ty: 15 }, x0, y0, cell: 1, lo, hi, q, enhet: "m" }, hojd, nivå };
}

test("vattenytan ligger på terräng plus djup: i en fylld sänka är nivån densamma i alla celler", () => {
  const { mark, hojd, nivå } = skal();
  const vatten = { typ: "klasser" as const, klasser: [{ fran: 0.02, farg: "#e4edf6", etikett: "" }, { fran: 0.5, farg: "#2b5d8c", etikett: "" }] };
  const y = vattenYta(mark, hojd, O, 0.02, (d) => fargFor(vatten, d));
  assert.ok(y.celler > 500);
  for (let i = 0; i < y.position.length / 3; i++) assert.ok(Math.abs((y.position[i * 3 + 1] as number) - (nivå - O.z)) < 0.05, `y=${y.position[i * 3 + 1]}`);
  assert.ok(Math.abs(y.maxDjup - 1) < 0.05); // 19 - 18 i mitten
  // Inget vatten utanför sänkan: ingen cell långt från mitten.
  for (let i = 0; i < y.position.length / 3; i++) assert.ok(Math.hypot((y.position[i * 3] as number) + O.e - 1088, -(y.position[i * 3 + 2] as number) + O.n - 1984) < 45);
});

test("djup under tröskeln ritas inte och celler utan värde (tak) hoppas över", () => {
  const { mark, hojd } = skal();
  const f = () => [0, 0, 255, 255] as [number, number, number, number];
  const hog = vattenYta(mark, hojd, O, 0.5, f);
  const lag = vattenYta(mark, hojd, O, 0.02, f);
  assert.ok(hog.celler < lag.celler && hog.celler > 0);
  const m2 = { ...mark, q: mark.q.slice() };
  m2.q[64 * CELLER + 64] = INGET_VARDE;
  assert.equal(vattenYta(m2, hojd, O, 0.02, f).celler, lag.celler - 1);
  assert.equal(vattenYta(mark, hojd, O, 2, f).celler, 0);
});

test("markVarde läser cellen som rymmer punkten och ger null för tak och utanför", () => {
  const { mark } = skal();
  const mitt = markVarde(mark, 1024 + 64.5, 1920 + 63.5);
  assert.ok(mitt !== null && Math.abs(mitt - 1) < 0.05);
  assert.equal(markVarde(mark, 1000, 1950), null);
  const m2 = { ...mark, q: mark.q.slice() };
  m2.q[64 * CELLER + 64] = INGET_VARDE;
  assert.equal(markVarde(m2, 1024 + 64.5, 1920 + 63.5), null);
});
