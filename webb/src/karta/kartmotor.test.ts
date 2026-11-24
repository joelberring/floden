/* Körs med `npm test`. Kartmotorn kräver en webbläsare; här vaktas bara det som webbläsarprovet (tools/ritverktyg_prov.py) hittade. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

test("kartmotorn stänger av rutzoom (boxZoom): Skift+klick ska nå ritverktygen som följ kant", () => {
  const kalla = readFileSync(new URL("./kartmotor.ts", import.meta.url), "utf8");
  assert.match(kalla, /boxZoom:\s*false/);
});
