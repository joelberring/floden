import assert from "node:assert/strict";
import { test } from "node:test";
import { nyckeltal, tal } from "./nyckeltal.ts";

test("saknade tal visas som –, aldrig som noll", () => {
  assert.equal(tal(null, 1, "m"), "–");
  assert.equal(tal(undefined, 1, "m"), "–");
  assert.equal(tal(Number.NaN, 1, "m"), "–");
  assert.match(tal(0, 2, "m"), /^0,00\s?m$/);
});

test("fyra nyckeltal för vatten och sol, alla – utan underlag", () => {
  for (const amne of ["vatten", "dagsljus"] as const) {
    const t = nyckeltal(amne, { troskel: null, siffror: null });
    assert.equal(t.length, 4);
    for (const x of t) assert.equal(x.varde, "–", `${amne}: ${x.etikett}`);
  }
});

test("siffrorna kommer från underlaget", () => {
  const t = nyckeltal("vatten", {
    troskel: 0.01,
    siffror: { yta_minskning_m2: 120, yta_okning_m2: 30, max_minskning: -0.2, max_okning: 0.05 },
    foreDjup_m: 0.36,
    efterDjup_m: 0.4,
  });
  assert.ok(t.some((x) => x.varde.includes("0,36")));
  assert.ok(t.some((x) => x.varde.includes("120")));
});
