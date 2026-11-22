import assert from "node:assert/strict";
import { test } from "node:test";
import { felHtml, informationHtml, korVarningarHtml } from "./felvisning.ts";

test("förklaringen visas först och den tekniska texten ligger under Teknisk information", () => {
  const html = felHtml("InstangtOmradeFel: saknar utloppssadel", "Huset stänger in en lågpunkt.");
  assert.ok(html.indexOf("Huset stänger in en lågpunkt.") < html.indexOf("Teknisk information"));
  assert.ok(html.indexOf("Teknisk information") < html.indexOf("saknar utloppssadel"));
  assert.ok(!/\[[^\]]*\]/.test(html), "inga hakparentesplatshållare");
});

test("utan förklaring visas den tekniska texten som förut och text escapas", () => {
  const html = felHtml("Fel <b>x</b>", null);
  assert.ok(html.includes("Beräkningen misslyckades: Fel &lt;b&gt;x&lt;/b&gt;"));
  assert.ok(!html.includes("Teknisk information"));
});

test("information före körning är status, inte larm, och escapas", () => {
  const html = informationHtml(["Huset stänger in gården – vattnet blir stående där i beräkningen", "<x>"]);
  assert.ok(html.includes('role="status"'));
  assert.ok(!html.includes('role="alert"'));
  assert.ok(html.includes("vattnet blir stående där"));
  assert.ok(html.includes("&lt;x&gt;"));
  assert.equal(informationHtml(undefined), "");
});

test("varningar från en klar körning visas som anmärkningar", () => {
  const html = korVarningarHtml(["Slutet område: vattnet har ingen väg ut i modellen; i verkligheten avgör brunnar, portar och trösklar."]);
  assert.ok(html.includes('role="note"') && html.includes("Slutet område"));
  assert.equal(korVarningarHtml([]), "");
});
