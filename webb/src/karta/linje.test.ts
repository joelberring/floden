import assert from "node:assert/strict";
import { test } from "node:test";
import { forenkla, infogaPunkt, klipp, langd, mellan, narmastSida, slaIhop, taBortPunkt, vand, type P3 } from "./linje.ts";

const L: P3[] = [{ x: 0, y: 0, z: 10 }, { x: 10, y: 0, z: 12 }, { x: 20, y: 0, z: 10 }];

test("punkt på en sträcka får interpolerad z", () => {
  const r = infogaPunkt(L, 0, 0.5, false) as P3[];
  assert.equal(r.length, 4);
  assert.deepEqual(r[1], { x: 5, y: 0, z: 11 });
  assert.equal(mellan({ x: 0, y: 0 }, { x: 2, y: 0, z: 1 }, 0.5).z, undefined);
});

test("minst två punkter på en linje och tre i en ring", () => {
  assert.equal(taBortPunkt(L.slice(0, 2), 0, false), undefined);
  assert.equal(taBortPunkt(L, 1, false)?.length, 2);
  assert.equal(taBortPunkt(L, 1, true), undefined);
});

test("klipp delar vid brytpunkt eller på sträckan, aldrig i en ändpunkt", () => {
  const vidBryt = klipp(L, { x: 10.2, y: 0.1 }, 1) as [P3[], P3[]];
  assert.equal(vidBryt[0].length, 2);
  assert.equal(vidBryt[1].length, 2);
  assert.deepEqual(vidBryt[0][1], vidBryt[1][0]);
  const paSida = klipp(L, { x: 15, y: 0.2 }, 1) as [P3[], P3[]];
  assert.equal(paSida[0].length, 3);
  assert.deepEqual(paSida[0][2], { x: 15, y: 0, z: 11 });
  assert.equal(klipp(L, { x: 0.1, y: 0 }, 1), undefined);
  assert.equal(klipp(L, { x: 50, y: 50 }, 1), undefined);
  assert.equal(langd(vidBryt[0]) + langd(vidBryt[1]), langd(L));
});

test("slå ihop: vänder rätt och slår ihop mötande ändar till en punkt", () => {
  const b: P3[] = [{ x: 40, y: 0 }, { x: 20.2, y: 0 }];
  const r = slaIhop(L, b, 0.5) as P3[];
  assert.equal(r.length, 4); // 20,0 och 20,2 blev en punkt
  assert.deepEqual(r.at(-1), { x: 40, y: 0 });
  const framfor: P3[] = [{ x: -10, y: 0 }, { x: -0.1, y: 0 }];
  assert.equal((slaIhop(L, framfor, 0.5) as P3[])[0]?.x, -10);
});

test("slå ihop nekas utanför toleransen utom när exakt två linjer tvingas", () => {
  const b: P3[] = [{ x: 30, y: 0 }, { x: 40, y: 0 }];
  assert.equal(slaIhop(L, b, 0.5), undefined);
  const t = slaIhop(L, b, 0.5, true) as P3[];
  assert.equal(t.length, 5); // rak sträcka emellan
});

test("vänd byter ordning och z följer punkterna", () => {
  const v = vand(L);
  assert.deepEqual(v[0], { x: 20, y: 0, z: 10 });
  assert.deepEqual(vand(v), L);
});

test("förenkla tar bort punkter inom toleransen och behåller ändarna", () => {
  const rak: P3[] = [{ x: 0, y: 0 }, { x: 5, y: 0.05 }, { x: 10, y: 0 }, { x: 15, y: 3 }];
  const f = forenkla(rak, 0.2, false) as P3[];
  assert.equal(f.length, 3);
  assert.deepEqual(f[0], { x: 0, y: 0 });
  assert.equal(forenkla(f, 0.2, false), undefined);
});

test("förenkla ring behåller tre punkter", () => {
  const ring: P3[] = [{ x: 0, y: 0 }, { x: 5, y: 0.01 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 5, y: 10.01 }, { x: 0, y: 10 }];
  const f = forenkla(ring, 0.1, true) as P3[];
  assert.equal(f.length, 4);
  assert.equal(forenkla([{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 0, y: 1 }], 5, true), undefined);
});

test("närmaste sida", () => {
  assert.equal(narmastSida(L, { x: 12, y: 3 }, 2, false), undefined);
  assert.equal(narmastSida(L, { x: 12, y: 1 }, 2, false)?.index, 1);
});
