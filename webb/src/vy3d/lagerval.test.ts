import assert from "node:assert/strict";
import { test } from "node:test";
import type { LagerPost } from "../fragor-typer.ts";
import { arVattenYta, lagerForTid, valjSidor } from "./lagerval.ts";

const lp = (over: Partial<LagerPost>): LagerPost => ({ id: "x", scenario: "bas", typ: "bas", roll: "soltimmar", ...over }) as LagerPost;
const U = {
  fore: lp({ id: "sol.bas.soltimmar", scenario: "bas" }),
  efter: lp({ id: "sol.hojd.soltimmar", scenario: "hojd", typ: "scenario" }),
  skillnad: lp({ id: "skillnad.x", scenario: "hojd", typ: "skillnad", fore: "bas", efter: "hojd" }),
};

test("före och efter visar ett scenario var, på en sida", () => {
  const f = valjSidor("fore", U, ["bas", "hojd"]);
  assert.equal(f.delning, "ingen");
  assert.equal(f.a.scen, "bas");
  assert.equal(f.a.mark?.id, "sol.bas.soltimmar");
  assert.deepEqual(f.a.fasad, { scenario: "bas", jamfor: null });
  const e = valjSidor("efter", U, ["bas", "hojd"]);
  assert.equal(e.a.scen, "hojd");
  assert.equal(e.b, null);
});

test("skillnad visar efterscenariots hus med skillnadslagret och fasadskillnad mot före", () => {
  const s = valjSidor("skillnad", U, ["bas", "hojd"]);
  assert.equal(s.a.scen, "hojd");
  assert.equal(s.a.mark?.typ, "skillnad");
  assert.deepEqual(s.a.fasad, { scenario: "hojd", jamfor: "bas" });
});

test("svep och sida vid sida har före till vänster och efter till höger", () => {
  for (const [lage, delning] of [["svep", "svep"], ["sidavid", "sida"]] as const) {
    const d = valjSidor(lage, U, ["bas", "hojd"]);
    assert.equal(d.delning, delning);
    assert.equal(d.a.scen, "bas");
    assert.equal(d.b?.scen, "hojd");
    assert.equal(d.b?.mark?.scenario, "hojd");
  }
});

test("utan fasadresultat för scenariot visas inga fasader, och saknas före ges ingen skillnad", () => {
  assert.equal(valjSidor("fore", U, []).a.fasad, null);
  assert.equal(valjSidor("skillnad", U, ["hojd"]).a.fasad?.jamfor, null);
});

test("utan resultat (ingen körning) ger varje läge tomma sidor men ingen krasch", () => {
  const tom = { fore: null, efter: null, skillnad: null };
  for (const l of ["fore", "efter", "skillnad", "svep", "sidavid"] as const) {
    const d = valjSidor(l, tom, []);
    assert.equal(d.a.mark, null);
  }
});

test("tidsreglaget väljer steg och faller tillbaka på maxdjupet utan tidsserie", () => {
  const steg = [{ lager: "a", minut: 0 }, { lager: "b", minut: 30 }, { lager: "c", minut: 60 }, { lager: "d", minut: 90 }];
  assert.deepEqual(lagerForTid(steg, 0, "max"), { lager: "a", minut: 0 });
  assert.deepEqual(lagerForTid(steg, 1, "max"), { lager: "d", minut: 90 });
  assert.deepEqual(lagerForTid(steg, 0.5, "max"), { lager: "c", minut: 60 });
  assert.deepEqual(lagerForTid(steg, -3, "max"), { lager: "a", minut: 0 });
  assert.deepEqual(lagerForTid(undefined, 0.5, "max"), { lager: "max", minut: null });
  assert.deepEqual(lagerForTid([], 0.5, "max"), { lager: "max", minut: null });
});

test("bara maxdjup och djup över tid ritas som vattenyta; skillnad och soltimmar färgar marken", () => {
  assert.equal(arVattenYta(lp({ roll: "max_djup", typ: "scenario" })), true);
  assert.equal(arVattenYta(lp({ roll: "djup_t0030", typ: "bas" })), true);
  assert.equal(arVattenYta(lp({ roll: "max_djup", typ: "skillnad" })), false);
  assert.equal(arVattenYta(lp({ roll: "soltimmar" })), false);
  assert.equal(arVattenYta(null), false);
});
