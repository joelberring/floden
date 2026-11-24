/* Körs med `npm test`. Sidans skal: webbläsarprovet (tools/ritverktyg_prov.py) fick ett konsolfel för varje sidladdning. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

test("index.html har en ikonlänk, så att webbläsaren inte frågar efter /favicon.ico (404 i konsolen)", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /<link rel="icon"/);
});
