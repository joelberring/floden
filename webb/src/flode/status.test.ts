import assert from "node:assert/strict";
import { test } from "node:test";
import { etikett, franK20, nivaFran, raknaPerAmne, tolkaKatalog } from "./status.ts";

test("statusetiketterna är de tre fasta", () => {
  assert.equal(etikett("svarar"), "Floden svarar");
  assert.equal(etikett("delvis"), "Delvis");
  assert.equal(etikett("saknas"), "Underlag saknas");
});

test("katalogens markering blir en nivå; okänt ger Underlag saknas", () => {
  assert.equal(nivaFran({ markning: "visat" }), "svarar");
  assert.equal(nivaFran({ markning: "kan" }), "svarar");
  assert.equal(nivaFran({ niva: "delvis" }), "delvis");
  assert.equal(nivaFran({ markning: "bor_inte" }), "saknas");
  assert.equal(nivaFran(null), "saknas");
  assert.equal(nivaFran("visat"), "saknas");
});

test("katalogen tolkas, solstudier hör till dagsljus och trasiga poster hoppas över", () => {
  const k = tolkaKatalog({
    fragor: [
      { id: "a", fraga: "Hur djupt blir vattnet?", omrade: "skyfall", floden: { markning: "visat" } },
      { id: "b", fraga: "Sol på gården?", omrade: "solstudier", floden: { markning: "delvis", saknat: "Solmodell" } },
      { id: "c", fraga: "Luft?", omrade: "luft" },
      { fraga: "utan id" },
      null,
    ],
  });
  assert.ok(k);
  assert.equal(k.length, 3);
  assert.deepEqual(k.map((f) => f.niva), ["svarar", "delvis", "saknas"]);
  assert.equal(k[1]?.amne, "dagsljus");
  assert.deepEqual(k[1]?.doman, ["dagsljus"]);
  assert.equal(k[1]?.saknat, "Solmodell");
  assert.deepEqual(k[0]?.doman, ["vatten"]);
  assert.equal(raknaPerAmne(k, "skyfall"), 1);
});

test("svar utan frågor ger null så att reserven används", () => {
  assert.equal(tolkaKatalog({}), null);
  assert.equal(tolkaKatalog(null), null);
});

test("reserven ur K20: nivå efter saknad indata och antal scenarier", () => {
  const f = franK20([
    { ref: "k1", beskrivning: "Vatten", analyser: [{ doman: "vatten" }], indata_saknas: [] },
    { ref: "k2", fraga_id: "x", analyser: [{ doman: "dagsljus" }], indata_saknas: ["a"], scenarier: [1, 2] },
    { ref: "k3", analyser: [], indata_saknas: ["a", "b"], scenarier: [1] },
    { ref: "k4", giltig: false },
  ]);
  assert.deepEqual(f.map((x) => x.niva), ["svarar", "delvis", "saknas", "saknas"]);
  assert.equal(f[0]?.amne, "skyfall");
  assert.equal(f[1]?.amne, "dagsljus");
  assert.equal(f[1]?.text, "x");
});
