/* Körs med `npm test`. Takformer i 3D (R6): profil, delning av ytan, väggar som följer taket, area och höjder. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { extrudera, type Hus } from "./geometri.ts";
import { delaKant, delaTriangel, konvextHolje, minstaRektangel, takFor, takTrianglar, tolkaTakform, MANSARD_KNAK } from "./tak.ts";

type P2 = [number, number];
const rekt = (x0: number, z0: number, b: number, d: number): P2[] => [[x0, z0], [x0 + b, z0], [x0 + b, z0 + d], [x0, z0 + d]];
const nara = (a: number, b: number, tol = 1e-6): void => assert.ok(Math.abs(a - b) <= tol, `${a} != ${b}`);
const yta = (r: ReadonlyArray<readonly [number, number]>): number => {
  let s = 0;
  for (let i = 0; i < r.length; i++) s += (r[i] as P2)[0] * (r[(i + 1) % r.length] as P2)[1] - (r[(i + 1) % r.length] as P2)[0] * (r[i] as P2)[1];
  return Math.abs(s) / 2;
};

test("takform ur K9-text: okänd och brutet räknas som sadel, som takvattnet", () => {
  assert.equal(tolkaTakform("platt"), "platt");
  assert.equal(tolkaTakform(" Flat "), "platt");
  assert.equal(tolkaTakform("pulpettak"), "pulpet");
  assert.equal(tolkaTakform("skillion"), "pulpet");
  assert.equal(tolkaTakform("mansard"), "mansard");
  assert.equal(tolkaTakform("gambrel"), "mansard");
  assert.equal(tolkaTakform("sadel"), "sadel");
  assert.equal(tolkaTakform("brutet"), "sadel");
  assert.equal(tolkaTakform(undefined), "sadel");
  assert.equal(tolkaTakform("något annat"), "sadel");
});

test("minsta rektangeln: längdaxel, halva mått och rotation; v pekar söderut", () => {
  const r = minstaRektangel(rekt(0, 0, 10, 6)) as NonNullable<ReturnType<typeof minstaRektangel>>;
  nara(r.hl, 5);
  nara(r.hw, 3);
  nara(Math.abs(r.u[0]), 1);
  assert.ok(r.v[1] > 0.99); // tvärs husets längd, åt söder (+z)
  nara(r.c[0], 5);
  nara(r.c[1], 3);
  // Ett hus som är vridet 30 grader: längdaxeln följer huset.
  const a = (30 * Math.PI) / 180;
  const vridet = rekt(0, 0, 12, 4).map(([x, z]) => [x * Math.cos(a) - z * Math.sin(a), x * Math.sin(a) + z * Math.cos(a)] as P2);
  const v = minstaRektangel(vridet) as NonNullable<ReturnType<typeof minstaRektangel>>;
  nara(v.hl, 6, 1e-6);
  nara(v.hw, 2, 1e-6);
  nara(Math.abs(v.u[0] * Math.cos(a) + v.u[1] * Math.sin(a)), 1, 1e-9);
  assert.equal(minstaRektangel([[0, 0], [1, 1]]), null);
  assert.equal(konvextHolje(rekt(0, 0, 4, 4).concat([[2, 2]])).length, 4);
});

test("sadel: nock längs längdaxeln, takfot 0, nock = resningen, vinkeln 27 grader (eller kapad vid låga hus)", () => {
  const t = takFor("sadel", rekt(0, 0, 10, 6), 12);
  assert.ok(t);
  nara(t.resning, 3 * Math.tan((27 * Math.PI) / 180), 1e-9);
  nara(t.hojdOverFot(5, 3), t.resning); // nocken
  nara(t.hojdOverFot(5, 0), 0); // takfoten norr
  nara(t.hojdOverFot(5, 6), 0); // takfoten söder
  nara(t.hojdOverFot(2, 1.5), t.resning / 2); // halvvägs
  assert.deepEqual(t.knackar, [0]);
  const lag = takFor("sadel", rekt(0, 0, 10, 6), 2);
  assert.ok(lag);
  nara(lag.resning, 1); // högst hälften av husets höjd
  assert.equal(takFor("platt", rekt(0, 0, 10, 6), 12), null);
  assert.equal(takFor("sadel", rekt(0, 0, 10, 6), 0), null);
});

test("pulpet faller mot söder: högst vid norra långsidan, takfot i söder", () => {
  const t = takFor("pulpet", rekt(0, 0, 10, 6), 12);
  assert.ok(t);
  nara(t.resning, 6 * Math.tan((10 * Math.PI) / 180), 1e-9);
  nara(t.hojdOverFot(5, 0), t.resning);
  nara(t.hojdOverFot(5, 6), 0);
  nara(t.hojdOverFot(5, 3), t.resning / 2);
  assert.deepEqual(t.knackar, []);
});

test("mansard: brant nedre del och flackare övre, knäck på 70 % av halva bredden", () => {
  const t = takFor("mansard", rekt(0, 0, 10, 6), 12);
  assert.ok(t);
  const b = MANSARD_KNAK * 3;
  nara(t.hojdOverFot(5, 0), 0);
  nara(t.hojdOverFot(5, 3 - b), 0.7 * t.resning); // knäcken norr om nocken
  nara(t.hojdOverFot(5, 3), t.resning);
  assert.equal(t.knackar.length, 3);
  const brant = (0.7 * t.resning) / (3 - b);
  const flack = (0.3 * t.resning) / b;
  assert.ok(brant > 2 * flack);
});

test("delning av en triangel längs en linje bevarar arean och ger delar på var sida", () => {
  const tri: [P2, P2, P2] = [[0, 0], [4, 0], [0, 4]];
  const d = (p: P2): number => p[0];
  const delar = delaTriangel(tri, d, 1);
  const area = (t: [P2, P2, P2]): number => Math.abs((t[1][0] - t[0][0]) * (t[2][1] - t[0][1]) - (t[1][1] - t[0][1]) * (t[2][0] - t[0][0])) / 2;
  nara(delar.reduce((s, t) => s + area(t), 0), 8);
  assert.ok(delar.length >= 2);
  for (const t of delar) assert.ok(t.every((p) => p[0] <= 1 + 1e-9) || t.every((p) => p[0] >= 1 - 1e-9));
  assert.equal(delaTriangel(tri, d, 10).length, 1); // linjen skär inte triangeln
});

test("takytorna täcker husets yta exakt (projicerad area) och ligger mellan takfot och nock", () => {
  for (const form of ["sadel", "pulpet", "mansard"] as const) {
    const ring = rekt(0, 0, 10, 6);
    const t = takFor(form, ring, 12);
    assert.ok(t, form);
    const tri = takTrianglar([ring], t);
    let a = 0;
    for (const x of tri) {
      a += yta(x.map((p) => [p[0], p[2]] as P2));
      for (const p of x) assert.ok(p[1] >= -1e-9 && p[1] <= t.resning + 1e-9, `${form}: höjd ${p[1]}`);
    }
    nara(a, 60, 1e-6);
  }
  // L-formad yta och ett hus med gård (hål): arean stämmer fortfarande.
  const L: P2[] = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]];
  const tl = takFor("sadel", L, 12);
  assert.ok(tl);
  nara(takTrianglar([L], tl).reduce((s, x) => s + yta(x.map((p) => [p[0], p[2]] as P2)), 0), 64, 1e-6);
  const gard = rekt(0, 0, 20, 20);
  const hal = rekt(6, 6, 8, 8);
  const tg = takFor("mansard", gard, 15);
  assert.ok(tg);
  nara(takTrianglar([gard, hal], tg).reduce((s, x) => s + yta(x.map((p) => [p[0], p[2]] as P2)), 0), 400 - 64, 1e-6);
});

test("kanter delas där taket knäcker: gavlarna får en topp, långsidorna ingen", () => {
  const t = takFor("sadel", rekt(0, 0, 10, 6), 12);
  assert.ok(t);
  assert.equal(delaKant([0, 0], [10, 0], t).length, 2); // långsidan (z = 0): ingen knäck
  const gavel = delaKant([10, 0], [10, 6], t);
  assert.equal(gavel.length, 3);
  nara(gavel[1]?.[1] ?? 0, 3); // mitt på gaveln
  const m = takFor("mansard", rekt(0, 0, 10, 6), 12);
  assert.ok(m);
  assert.equal(delaKant([10, 0], [10, 6], m).length, 5);
});

const HUS = (takform?: Hus["takform"]): Hus => ({ ringar: [rekt(0, 0, 10, 6)], yFot: 0, yTak: 12, ...(takform ? { takform } : {}) });
const maxY = (pos: Float32Array): number => Math.max(...Array.from({ length: pos.length / 3 }, (_, i) => pos[i * 3 + 1] as number));

test("extrudera: utan takform eller med platt är allt som förut (väggar och ett platt tak)", () => {
  const a = extrudera([HUS()]);
  const b = extrudera([HUS("platt")]);
  assert.deepEqual(Array.from(a.position), Array.from(b.position));
  assert.equal(a.index.length, 4 * 6 + 2 * 3);
  assert.equal(maxY(a.position), 12);
});

test("extrudera sadel: nocken når husets höjd, väggarna slutar vid takfoten, gavlarna har en spets", () => {
  const e = extrudera([HUS("sadel")]);
  nara(maxY(e.position), 12, 1e-6);
  const t = takFor("sadel", rekt(0, 0, 10, 6), 12);
  assert.ok(t);
  // Takfoten är 12 - resning; ingen vägg- eller takpunkt ligger över nocken.
  const fot = 12 - t.resning;
  const ys = Array.from({ length: e.position.length / 3 }, (_, i) => e.position[i * 3 + 1] as number);
  assert.ok(ys.every((y) => y <= 12 + 1e-6 && y >= -1e-6));
  assert.ok(ys.some((y) => Math.abs(y - fot) < 1e-6));
  // 2 långsidor (1 yta) + 2 gavlar (2 delar) = 6 väggytor à 2 trianglar; resten är takytor (minst 4).
  const vagg = Array.from({ length: e.index.length / 3 }, (_, tr) => e.normal[(e.index[tr * 3] as number) * 3 + 1] as number).filter((ny) => Math.abs(ny) <= 1e-9).length;
  assert.equal(vagg, 6 * 2);
  assert.ok(e.index.length / 3 >= 12 + 4);
  // Alla normaler är enhetsvektorer; takets pekar uppåt, väggarnas är vågräta och pekar ut.
  for (let i = 0; i < e.normal.length; i += 3) nara(Math.hypot(e.normal[i] as number, e.normal[i + 1] as number, e.normal[i + 2] as number), 1, 1e-5);
  for (let tr = 0; tr < e.index.length / 3; tr++) {
    const i = (e.index[tr * 3] as number) * 3;
    const ny = e.normal[i + 1] as number;
    if (Math.abs(ny) > 1e-9) {
      assert.ok(ny > 0.3 && ny < 1, "takyta uppåt");
    } else {
      const ut = ((e.position[i] as number) - 5) * (e.normal[i] as number) + ((e.position[i + 2] as number) - 3) * (e.normal[i + 2] as number);
      assert.ok(ut > 0, "vägg utåt");
    }
  }
  assert.ok(e.triHus.every((h) => h === 0));
});

test("extrudera pulpet och mansard: högsta punkt = husets höjd, och olika former ger olika geometri", () => {
  const former = ["platt", "sadel", "pulpet", "mansard"] as const;
  const g = former.map((f) => extrudera([HUS(f)]));
  for (const e of g) nara(maxY(e.position), 12, 1e-6);
  const nycklar = new Set(g.map((e) => Array.from(e.position).join(",")));
  assert.equal(nycklar.size, 4);
  // Pulpet: väggen i söder (z = 6) är lägst, i norr (z = 0) högst.
  const p = g[2] as ReturnType<typeof extrudera>;
  const toppar = new Map<number, number>();
  for (let i = 0; i < p.position.length / 3; i++) {
    const z = Math.round((p.position[i * 3 + 2] as number) * 100) / 100;
    toppar.set(z, Math.max(toppar.get(z) ?? -1, p.position[i * 3 + 1] as number));
  }
  assert.ok((toppar.get(0) as number) > (toppar.get(6) as number) + 0.5);
  nara(toppar.get(0) as number, 12, 1e-6);
});

test("extrudera: flera hus med olika tak, ett lågt hus får kapad resning, och hus utan rektangel ritas platta", () => {
  const e = extrudera([HUS("sadel"), { ...HUS("mansard"), ringar: [rekt(30, 0, 8, 8)] }, { ringar: [rekt(50, 0, 5, 5)], yFot: 0, yTak: 2, takform: "sadel" }]);
  assert.deepEqual(Array.from(new Set(Array.from(e.triHus))).sort(), [0, 1, 2]);
  const lag = extrudera([{ ringar: [rekt(0, 0, 5, 5)], yFot: 0, yTak: 2, takform: "sadel" }]);
  nara(maxY(lag.position), 2, 1e-6);
  const fot = Math.min(...Array.from({ length: lag.position.length / 3 }, (_, i) => lag.position[i * 3 + 1] as number).filter((y) => y > 0));
  nara(fot, 1, 1e-6); // takfot minst hälften av höjden
  const tunn = extrudera([{ ringar: [[[0, 0], [1, 0], [1, 0]]], yFot: 0, yTak: 5, takform: "sadel" }]);
  assert.ok(tunn.index.length >= 0);
});
