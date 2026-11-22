import assert from "node:assert/strict";
import { test } from "node:test";
import { felHtml } from "./felvisning.ts";

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
