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
    assert.equal(r.linjer.length, 0);
    assert.equal(r.ytor.length, 0);
    assert.equal(r.sammanfattning.text, "1 hus");
  }
});

test("GeoJSON FeatureCollection importerar byggnader, brytlinjer, öppningar och märkta ritytor", () => {
  const husRing = [[150000, 6580000], [150020, 6580000], [150020, 658010], [150000, 658010], [150000, 6580000]];
  const markRing = [[150100, 6580000], [150120, 6580000], [150120, 6580020], [150100, 6580020], [150100, 6580000]];
  const vattenRing = [[150200, 6580000], [150210, 6580000], [150210, 6580010], [150200, 6580010], [150200, 6580000]];
  const fc = {
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: { hojd_m: 12 }, geometry: { type: "Polygon", coordinates: [husRing] } },
      {
        type: "Feature",
        properties: { typ: "mur", hojd_m: 1.5 },
        geometry: { type: "LineString", coordinates: [[150000, 6580000], [150030, 6580000]] },
      },
      {
        type: "Feature",
        properties: { roll: "oppning", typ: "trumma", bredd_m: 2 },
        geometry: { type: "MultiLineString", coordinates: [[[150010, 6580000], [150010, 6580005]]] },
      },
      {
        type: "Feature",
        properties: { roll: "mark", delta_m: 0.5 },
        geometry: { type: "Polygon", coordinates: [markRing] },
      },
      {
        type: "Feature",
        properties: { roll: "vatten", volym_m3: 150, botten_rh2000: 8.2 },
        geometry: { type: "Polygon", coordinates: [vattenRing] },
      },
    ],
  };

  const r = tolkaFil(JSON.stringify(fc));
  assert.equal(r.slag, "hus");
  if (r.slag === "hus") {
    assert.equal(r.hus.length, 1);
    assert.equal(r.linjer.length, 2);
    assert.equal(r.linjer[0]?.roll, "brytlinje");
    assert.deepEqual(r.linjer[0]?.egenskaper, { typ: "mur", hojd_m: 1.5 });
    assert.equal(r.linjer[1]?.roll, "oppning");
    assert.deepEqual(r.linjer[1]?.egenskaper, { typ: "trumma", bredd_m: 2 });
    assert.equal(r.ytor.length, 2);
    assert.equal(r.ytor[0]?.roll, "mark");
    assert.deepEqual(r.ytor[0]?.egenskaper, { delta_m: 0.5 });
    assert.equal(r.ytor[1]?.roll, "vatten");
    assert.deepEqual(r.ytor[1]?.egenskaper, { volym_m3: 150, botten_rh2000: 8.2 });
    assert.deepEqual(r.sammanfattning, {
      antalHus: 1,
      antalLinjer: 2,
      antalYtor: 2,
      text: "1 hus, 2 linjer, 2 ytor",
    });
  }
});
