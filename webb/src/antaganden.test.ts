import assert from "node:assert/strict";
import { test } from "node:test";
import { vattenAntagandenRader } from "./antaganden.ts";

const TAK = "Takvatten: med, okänd takform räknas som brutet tak (hälften till varje långsida)";
const AVT = "Avtappning: 0 mm/h (ledningsnätet antas fullt)";

test("takvatten och avtappning visas en gång var, med lösarens text", () => {
  const p = { analys: "skyfall", takvatten: true, takvatten_text: TAK, avtappning_mm_h: 0, avtappning_kalla: "standard", avtappning_text: AVT };
  const rader = vattenAntagandenRader([p, { ...p, analys: "annan" }]);
  assert.equal(rader.length, 2);
  assert.ok((rader[0] ?? "").includes(TAK));
  assert.ok((rader[1] ?? "").includes(AVT));
});

test("utan uppgifter blir det inga rader och text escapas", () => {
  assert.deepEqual(vattenAntagandenRader([]), []);
  const r = vattenAntagandenRader([{ analys: "a", takvatten: null, takvatten_text: "<x>", avtappning_mm_h: null, avtappning_kalla: null, avtappning_text: null }]);
  assert.ok((r[0] ?? "").includes("&lt;x&gt;"));
});
