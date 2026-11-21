import assert from "node:assert/strict";
import { test } from "node:test";
import { csrfFranKakor, csrfHuvud, inloggningsAdress, tolkaKontoSvar } from "./konto.ts";

test("csrfFranKakor läser både __Host- och enkla namn", () => {
  assert.equal(csrfFranKakor("a=1; __Host-floden-csrf=abc%2B; b=2"), "abc+");
  assert.equal(csrfFranKakor("floden_csrf=xyz"), "xyz");
  assert.equal(csrfFranKakor("a=1"), null);
  assert.deepEqual(csrfHuvud("a=1"), {});
  assert.deepEqual(csrfHuvud("floden_csrf=q"), { "X-Floden-CSRF": "q" });
});

test("tolkaKontoSvar", () => {
  assert.deepEqual(tolkaKontoSvar(200, { inloggning: "av" }), { lage: "av" });
  assert.deepEqual(tolkaKontoSvar(401, { fel: "x" }), { lage: "ut" });
  assert.deepEqual(
    tolkaKontoSvar(200, { inloggning: "pa", konto: { namn: "N", epost: "e" } }),
    { lage: "in", namn: "N", epost: "e" },
  );
  assert.deepEqual(tolkaKontoSvar(500, null), { lage: "okand" });
});

test("inloggningsAdress kodar återvändsadressen", () => {
  assert.equal(inloggningsAdress("/a b"), "/konto/logga-in/google?atervand=%2Fa%20b");
});
