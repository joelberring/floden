import assert from "node:assert/strict";
import { test } from "node:test";
import { avkvantisera, cellstorlek, INGET_VARDE, maxLod, rutHorn, rutorI, rutstorlek, rutUtbr, valjRutor, type Rut, type Utbr } from "./rutnat.ts";

const DEMO: Utbr = { x_min: 151000, y_min: 6580000, x_max: 151700, y_max: 6580700 };

test("rutnätet har samma regler som servern", () => {
  assert.deepEqual([0, 1, 2, 3].map(cellstorlek), [1, 2, 4, 8]);
  assert.deepEqual([0, 1, 2].map(rutstorlek), [128, 256, 512]);
  assert.deepEqual(rutHorn({ lod: 1, tx: 3, ty: -2 }), { x0: 768, y0: -512, cell: 2 });
});

test("antal rutor per nivå och maxnivå är desamma som i serverns info för demokvarteret", () => {
  // Servern gav 42, 12, 6 och 4 rutor för nivå 0 till 3 (src/webb/vy3d.py, test_info_...).
  assert.deepEqual([0, 1, 2, 3].map((l) => rutorI(DEMO, l).length), [42, 12, 6, 4]);
  assert.equal(maxLod(DEMO), 3);
  assert.deepEqual(rutorI({ x_min: 0, y_min: 0, x_max: 128, y_max: 128 }, 0), [{ lod: 0, tx: 0, ty: 0 }]);
});

function antalRutorSomTackerPunkt(rutor: Rut[], e: number, n: number): number {
  return rutor.filter((r) => {
    const u = rutUtbr(r);
    return e >= u.x_min && e < u.x_max && n >= u.y_min && n < u.y_max;
  }).length;
}

test("valda rutor täcker varje punkt i området exakt en gång, vart kameran än står", () => {
  for (const kam of [
    { e: 151350, n: 6580350, z: 80 },
    { e: 151010, n: 6580010, z: 20 },
    { e: 151900, n: 6580900, z: 300 },
  ]) {
    const rutor = valjRutor(DEMO, kam);
    for (let e = 151000.5; e < 151700; e += 37.3) {
      for (let n = 6580000.5; n < 6580700; n += 41.9) assert.equal(antalRutorSomTackerPunkt(rutor, e, n), 1, `${e},${n}`);
    }
  }
});

test("nära kameran är rutorna fina och långt bort grova", () => {
  const kam = { e: 151100, n: 6580100, z: 30 };
  const rutor = valjRutor(DEMO, kam);
  const nara = rutor.filter((r) => {
    const u = rutUtbr(r);
    return kam.e >= u.x_min && kam.e < u.x_max && kam.n >= u.y_min && kam.n < u.y_max;
  });
  assert.equal(nara.length, 1);
  assert.equal(nara[0]?.lod, 0);
  const langst = rutor.reduce((a, b) => (b.lod > a.lod ? b : a));
  assert.ok(langst.lod >= 2, `grövsta nivån ${langst.lod}`);
  // Hög kamera ger färre och grövre rutor än låg.
  const hog = valjRutor(DEMO, { e: 151350, n: 6580350, z: 1500 });
  assert.ok(hog.length < rutor.length);
});

test("antalet rutor begränsas av taket och de närmaste delas först", () => {
  const rutor = valjRutor(DEMO, { e: 151350, n: 6580350, z: 2 }, 50, 20);
  assert.ok(rutor.length <= 20);
  assert.equal(antalRutorSomTackerPunkt(rutor, 151350.5, 6580350.5), 1);
});

test("avkvantisering: 65535 är inget värde och ändpunkterna stämmer", () => {
  assert.equal(avkvantisera(0, 2, 12), 2);
  assert.equal(avkvantisera(65534, 2, 12), 12);
  assert.ok(Number.isNaN(avkvantisera(INGET_VARDE, 2, 12)));
});
