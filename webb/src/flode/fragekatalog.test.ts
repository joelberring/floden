/* Frågekatalogen (#/fragor): samma ämnen och ord som steg 3, bara läsning, och länkar till steg 3 per projekt. */
import assert from "node:assert/strict";
import { test } from "node:test";

import { AMNEN } from "./status.ts";
import type { FragelistaSvar } from "./fragor.ts";
import { katalogAmnenHtml, katalogListaHtml, katalogProjektHtml } from "./fragekatalog-html.ts";

const lista: FragelistaSvar = {
  kalla: "katalog",
  anmarkning: "Frågorna kommer ur frågekatalogen.",
  fragor: [
    { id: "SK-01", amne: "skyfall", text: "Var blir vattnet stående?", niva: "svarar", doman: ["vatten"] },
    { id: "SK-02", amne: "skyfall", text: "Hur påverkas ledningsnätet?", niva: "saknas", doman: ["vatten"], saknat: "Ledningsdata" },
    { id: "DL-01", amne: "dagsljus", text: "Hur många soltimmar får gården?", niva: "delvis", doman: ["dagsljus"] },
  ],
};

test("ämnesraden har samma ämnen och samma ord som steg 3, med antal", () => {
  const html = katalogAmnenHtml(lista, "skyfall");
  for (const a of AMNEN) assert.match(html, new RegExp(`data-amne="${a.id}"[^>]*>${a.namn}`));
  assert.match(html, /Skyfall · 2/);
  assert.match(html, /Dagsljus och sol · 1/);
  assert.match(html, /data-amne="skyfall" aria-pressed="true"/);
  assert.match(html, /data-amne="luft" aria-pressed="false" class="tom"/);
  assert.doesNotMatch(html, /Vatten|Utredningsfrågor/);
});

test("listan visar frågorna i ämnet med statusetikett, utan kryssrutor eller körknapp", () => {
  const html = katalogListaHtml(lista, "skyfall");
  assert.match(html, /SK-01 Var blir vattnet stående\?/);
  assert.match(html, /flode-etikett svarar[^>]*>Floden svarar</);
  assert.match(html, /flode-etikett saknas" title="Ledningsdata">Underlag saknas</);
  assert.doesNotMatch(html, /DL-01/);
  assert.doesNotMatch(html, /<input|<button|Kör /);
  assert.match(katalogListaHtml(lista, "dagsljus"), />Delvis</);
  assert.match(katalogListaHtml(lista, "buller"), /Inga frågor i det här ämnet än/);
});

test("utan lista visas ämnena utan antal", () => {
  assert.doesNotMatch(katalogAmnenHtml(null, "skyfall"), /·/);
});

test("länkarna leder till steg 3 i varje projekt, och specialtecken kodas och escapas", () => {
  const html = katalogProjektHtml([
    { id: "demokvarteret", namn: "Demokvarteret" },
    { id: "mitt projekt", namn: "<b>Mitt</b>" },
  ]);
  assert.match(html, /href="#\/p\/demokvarteret\/fragor">Välj frågor i Demokvarteret</);
  assert.match(html, /href="#\/p\/mitt%20projekt\/fragor">Välj frågor i &lt;b&gt;Mitt&lt;\/b&gt;</);
  assert.match(katalogProjektHtml([]), /inga projekt än/);
});
