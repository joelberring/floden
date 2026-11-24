/* Körs med `npm test`. Varningarna från mappningen: sammanfattning och grupper per slag. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { grupperaVarningar, MAX_RADER_PER_GRUPP, sammanfattning, varningarHtml, varningsslag } from "./varningar.ts";

const hus = (n: number): string[] => Array.from({ length: n }, (_, i) => `byggnad-${i + 1}: ingen markhöjd under eller kring byggnaden; utelämnad`);

test("slaget är raden utan id och utan siffror", () => {
  assert.equal(varningsslag("byggnad-17: ingen markhöjd under eller kring byggnaden; utelämnad"), "ingen markhöjd under eller kring byggnaden; utelämnad");
  assert.equal(varningsslag("mm_3: 12,5 % av cellerna saknar höjd"), "… % av cellerna saknar höjd");
  // Ett inledande ord med blanksteg före kolonet är ingen id: hela raden är slaget.
  assert.equal(varningsslag("Terräng i kärnan: 1,2 till 15 m"), "Terräng i kärnan: … till … m");
  assert.equal(varningsslag("Träd: inga trädpunkter inom kärnan"), "inga trädpunkter inom kärnan");
  assert.equal(varningsslag("Byggnadshöjd ur laser: oklassade punkter"), "Byggnadshöjd ur laser: oklassade punkter");
});

test("gruppering: största gruppen först, tomma rader bort", () => {
  const rader = ["mm_1: 3 % NaN", ...hus(20), "", "mm_2: 40 % NaN", "mm_3: 8 % NaN", "Trasig fil"];
  const g = grupperaVarningar(rader);
  assert.equal(g.length, 3);
  assert.equal(g[0]?.rader.length, 20);
  assert.equal(g[1]?.rubrik, "… % NaN");
  assert.equal(g[1]?.rader.length, 3);
  assert.deepEqual(g[2]?.rader, ["Trasig fil"]);
});

test("sammanfattning: tal med tusentalsmellanrum och rätt böjning", () => {
  assert.equal(sammanfattning(32, "vid mappningen"), "32 varningar vid mappningen");
  assert.equal(sammanfattning(1, "vid mappningen"), "1 varning vid mappningen");
  assert.equal(sammanfattning(1234, "x"), "1 234 varningar x");
});

test("HTML: en sammanfattning och en hopfälld lista; inget utan varningar; text escapas", () => {
  assert.equal(varningarHtml([], "vid mappningen", "v"), "");
  const h = varningarHtml([...hus(30), "mm_1: <b>fel</b>"], "vid mappningen", "np-varn");
  assert.ok(h.startsWith('<details class="flode-varningar-ruta" id="np-varn"><summary>31 varningar vid mappningen</summary>'));
  assert.ok(!h.includes(" open"), "listan ska vara hopfälld");
  assert.ok(h.includes("30 rader: ingen markhöjd under eller kring byggnaden; utelämnad"));
  assert.ok(h.includes("&lt;b&gt;fel&lt;/b&gt;") && !h.includes("<b>"));
});

test("en grupp med många rader kortas och säger hur många som utelämnas", () => {
  const h = varningarHtml(hus(MAX_RADER_PER_GRUPP + 7), "vid mappningen", "v");
  assert.ok(h.includes("och 7 till"));
  assert.equal((h.match(/byggnad-\d+:/g) ?? []).length, MAX_RADER_PER_GRUPP);
});
