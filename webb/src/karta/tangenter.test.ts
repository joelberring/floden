import assert from "node:assert/strict";
import { test } from "node:test";
import { bindningAv, FORVAL, kommandoFor, lasTangenter, medBindning, sparaTangenter, tangentEtikett } from "./tangenter.ts";

const h = (key: string, code: string, shiftKey = false, extra = {}) => ({ key, code, shiftKey, ...extra });

test("bokstäver läses ur tangentens plats, så de är desamma på alla tangentbord", () => {
  assert.equal(bindningAv(h("ц", "KeyW")), "W");
  assert.equal(bindningAv(h("W", "KeyW", true)), "Shift+W");
  assert.equal(bindningAv(h("§", "Backquote")), "§");
});

test("Ctrl, Cmd, Alt och rena Skift ger ingen bindning", () => {
  assert.equal(bindningAv(h("z", "KeyZ", false, { ctrlKey: true })), undefined);
  assert.equal(bindningAv(h("Shift", "ShiftLeft", true)), undefined);
});

test("förvalet: X klipp, G slå ihop, V vänd, F följ kant, Skift+W yta", () => {
  assert.equal(kommandoFor(FORVAL, "X"), "klipp");
  assert.equal(kommandoFor(FORVAL, "G"), "ihop");
  assert.equal(kommandoFor(FORVAL, "V"), "vand");
  assert.equal(kommandoFor(FORVAL, "F"), "foljkant");
  assert.equal(kommandoFor(FORVAL, "Shift+W"), "yta");
  assert.equal(new Set(Object.values(FORVAL)).size, Object.values(FORVAL).length); // inga två kommandon delar tangent
});

test("byte av tangent: det som hade den står utan, reserverade tangenter nekas", () => {
  const r = medBindning(FORVAL, "klipp", "G");
  assert.equal(r.karta.klipp, "G");
  assert.equal(r.bortbytt, "ihop");
  assert.equal(r.karta.ihop, "");
  assert.equal(medBindning(FORVAL, "klipp", "Enter").karta, FORVAL);
});

test("sparas bara det egna; trasig lagring ger förvalet", () => {
  const { karta } = medBindning(FORVAL, "klipp", "K");
  const s = sparaTangenter(karta);
  assert.equal(s, '{"klipp":"K"}');
  assert.deepEqual(lasTangenter(s), karta);
  assert.equal(lasTangenter("{trasig"), FORVAL);
  assert.equal(lasTangenter(null), FORVAL);
  assert.equal(tangentEtikett("Shift+W"), "⇧W");
});
