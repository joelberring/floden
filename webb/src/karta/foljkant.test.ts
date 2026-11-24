import assert from "node:assert/strict";
import { test } from "node:test";
import { foljKant, lokalisera } from "./foljkant.ts";
import type { P3 } from "./linje.ts";

const kant: P3[] = [{ x: 0, y: 0, z: 1 }, { x: 10, y: 0, z: 2 }, { x: 10, y: 10, z: 3 }, { x: 20, y: 10, z: 4 }];

test("följ kant ger kantens egna hörn och en slutpunkt exakt på kanten", () => {
  const r = foljKant({ punkter: kant, sluten: false }, { x: 0, y: 0 }, { x: 15, y: 10.4 }) as NonNullable<ReturnType<typeof foljKant>>;
  assert.deepEqual(r.punkter.slice(0, 2), [{ x: 10, y: 0, z: 2 }, { x: 10, y: 10, z: 3 }]);
  assert.deepEqual(r.punkter[2], { x: 15, y: 10, z: 3.5 });
  // Varje ny punkt ligger exakt på en kant (avstånd 0, inte "nästan").
  for (const p of r.punkter) assert.equal(lokalisera({ punkter: kant, sluten: false }, p).distance, 0);
});

test("följ kant bakåt längs linjen", () => {
  const r = foljKant({ punkter: kant, sluten: false }, { x: 20, y: 10 }, { x: 10, y: 3 }) as NonNullable<ReturnType<typeof foljKant>>;
  assert.deepEqual(r.punkter.map((p) => [p.x, p.y]), [[10, 10], [10, 3]]);
});

test("en ring går den kortare vägen runt", () => {
  const ring: P3[] = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }];
  const r = foljKant({ punkter: ring, sluten: true }, { x: 1, y: 0 }, { x: 0, y: 9 }) as NonNullable<ReturnType<typeof foljKant>>;
  assert.deepEqual(r.punkter.map((p) => [p.x, p.y]), [[0, 0], [0, 9]]); // via hörnet 0,0 (kort väg), inte runt hela
  const lang = foljKant({ punkter: ring, sluten: true }, { x: 9, y: 0 }, { x: 0, y: 1 }) as NonNullable<ReturnType<typeof foljKant>>;
  assert.deepEqual(lang.punkter.map((p) => [p.x, p.y]), [[0, 0], [0, 1]]); // 10 m via hörnet, inte 30 m åt andra hållet
});

test("ingenting att följa när startpunkten inte ligger på linjen eller pekaren står kvar", () => {
  assert.equal(foljKant({ punkter: kant, sluten: false }, { x: 5, y: 5 }, { x: 15, y: 10 }), undefined);
  assert.equal(foljKant({ punkter: kant, sluten: false }, { x: 5, y: 0 }, { x: 5, y: 3 }), undefined);
});
