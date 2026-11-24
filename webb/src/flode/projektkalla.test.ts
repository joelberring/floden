import assert from "node:assert/strict";
import { test } from "node:test";

import { projektKallaText } from "./projektkalla.ts";

test("tom för demokvarteret", () => {
  assert.equal(projektKallaText({ slag: "demo" }), null);
  assert.equal(projektKallaText({}), null);
});

test("stängt projekt i en geografi med polygon", () => {
  const t = projektKallaText({
    slag: "geografi",
    geografi: "goteborg-test@v1",
    avgransning_3011: [0, 0, 60, 80],
    avgransning_polygon_3011: { type: "Polygon", coordinates: [] },
    oppen: false,
    licens: "intern",
  });
  assert.ok(t?.includes("goteborg-test@v1"));
  assert.ok(t?.includes("polygon, 60 × 80 m"));
  assert.ok(t?.includes("inte öppna data (licens: intern)"));
});

test("öppet projekt med rektangel", () => {
  const t = projektKallaText({ slag: "geografi", geografi: "x@v1", avgransning_3011: [0, 0, 10, 10], oppen: true });
  assert.ok(t?.includes("rektangel"));
  assert.ok(t?.includes("Underlaget är öppna data."));
});
