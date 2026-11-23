import assert from "node:assert/strict";
import { test } from "node:test";
import { tolkaFil } from "./filimport.ts";

test("ogiltig JSON och tomma filer ger ett felmeddelande", () => {
  assert.equal(tolkaFil("inte json").slag, "fel");
  assert.equal(tolkaFil("null").slag, "fel");
  assert.equal(tolkaFil('{"type":"FeatureCollection","features":[]}').slag, "fel");
});

test("ett sparat förslag med ändringar läses som förslag", () => {
  const r = tolkaFil(JSON.stringify({ andringar: [{ typ: "byggnad_bort", id: "b1" }] }));
  assert.equal(r.slag, "forslag");
});

test("GeoJSON-polygon i WGS84 blir ett hus med höjd", () => {
  const ring = [[18.0, 59.3], [18.0005, 59.3], [18.0005, 59.3004], [18.0, 59.3004], [18.0, 59.3]];
  const r = tolkaFil(JSON.stringify({ type: "Feature", properties: { hojd_m: 21 }, geometry: { type: "Polygon", coordinates: [ring] } }));
  assert.equal(r.slag, "hus");
  if (r.slag === "hus") {
    assert.equal(r.hus.length, 1);
    assert.equal(r.hus[0]?.hojd_m, 21);
  }
});
