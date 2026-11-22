import assert from "node:assert/strict";
import { test } from "node:test";
import { byggnadText, fasadText, straleMotHojdfalt } from "./avlasning.ts";
import { franScen, tillScen } from "./koord.ts";

const O = { e: 151350, n: 6580350, z: 11 };

test("strålen träffar ett plant fält där den korsar det, också snett och förfinat", () => {
  const hojd = (): number => 13;
  const kam = tillScen(151350, 6580300, 63, O);
  const mal = tillScen(151350, 6580350, 13, O);
  const l = Math.hypot(mal.x - kam.x, mal.y - kam.y, mal.z - kam.z);
  const r = { x: (mal.x - kam.x) / l, y: (mal.y - kam.y) / l, z: (mal.z - kam.z) / l };
  const t = straleMotHojdfalt(kam, r, hojd, O);
  assert.ok(t);
  assert.ok(Math.abs(t.e - 151350) < 1e-3 && Math.abs(t.n - 6580350) < 1e-3 && Math.abs(t.z - 13) < 1e-3);
  assert.ok(Math.abs(t.avstand - l) < 1e-3);
});

test("strålen träffar en kulle före marken bakom och missar när den går uppåt", () => {
  const kulle = (e: number): number => (e > 151360 && e < 151380 ? 20 : 12);
  const kam = tillScen(151300, 6580350, 14, O);
  const t = straleMotHojdfalt(kam, { x: 1, y: -0.05, z: 0 }, (e) => kulle(e), O);
  assert.ok(t);
  assert.ok(t.e > 151360 && t.e < 151380 || t.z <= 20, `träff vid E ${t.e}`);
  assert.equal(straleMotHojdfalt(kam, { x: 0, y: 1, z: 0 }, () => 12, O, 500), null);
  // Utanför höjdfältet (NaN) ger ingen träff.
  assert.equal(straleMotHojdfalt(kam, { x: 1, y: -0.1, z: 0 }, () => Number.NaN, O, 500), null);
});

test("träffpunkten går att vända till scenen och tillbaka", () => {
  const p = tillScen(151400, 6580400, 12, O);
  const e = franScen(p, O);
  assert.deepEqual([e.e, e.n, e.z], [151400, 6580400, 12]);
});

test("fasadtexten säger vad värdet är och när det saknas jämförelse", () => {
  const t = fasadText({ vsc: 31.2, fore: 33, skillnad: -1.8, vaning: 3, byggnad: "b_0391f20c326b9f1c" }, "skillnad");
  assert.match(t, /våning 3/);
  assert.match(t, /efter 31,2 %/);
  assert.match(t, /före 33 %/);
  assert.match(t, /−|-1,8 procentenheter/);
  const ny = fasadText({ vsc: 20, fore: null, skillnad: null, vaning: 5, byggnad: "b_x1234567890" }, "skillnad");
  assert.match(ny, /finns inte före/);
  assert.match(fasadText({ vsc: 20, fore: null, skillnad: null, vaning: 1, byggnad: "b_x1234567890" }, "efter"), /Dagsljus \(VSC\): 20 %/);
});

test("byggnadstexten säger att taket inte har något markvärde", () => {
  assert.match(byggnadText("b_0391f20c326b9f1c", 15, 2), /höjt hus/);
  assert.match(byggnadText("b_0391f20c326b9f1c", 15, 0), /Tak: inget markvärde/);
});
