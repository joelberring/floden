import assert from "node:assert/strict";
import { test } from "node:test";
import { flodeAdress, nastaSteg, STEG, stegradHtml, tolkaFlodeAdress } from "./adress.ts";

test("#/ är projektlistan och #/p/<projekt> börjar på Förslag", () => {
  assert.deepEqual(tolkaFlodeAdress("#/"), { steg: "projekt", projekt: null });
  assert.deepEqual(tolkaFlodeAdress(""), { steg: "projekt", projekt: null });
  assert.deepEqual(tolkaFlodeAdress("#/p/demo"), { steg: "forslag", projekt: "demo" });
});

test("alla fyra projektsteg tolkas, med och utan frågetecken", () => {
  for (const s of ["forslag", "fragor", "resultat", "rapport"]) {
    assert.deepEqual(tolkaFlodeAdress(`#/p/demo/${s}`), { steg: s, projekt: "demo" });
  }
  assert.deepEqual(tolkaFlodeAdress("#/p/demo/resultat?k=1"), { steg: "resultat", projekt: "demo" });
});

test("andra adresser lämnas till den gamla routern", () => {
  assert.equal(tolkaFlodeAdress("#/om"), null);
  assert.equal(tolkaFlodeAdress("#/p"), null);
  assert.equal(tolkaFlodeAdress("#/p/demo/okant"), null);
  assert.equal(tolkaFlodeAdress("#/p/demo/resultat/extra"), null);
  assert.equal(tolkaFlodeAdress("#/p/%E0%A4%A"), null);
});

test("adresser byggs och tolkas tillbaka, även med specialtecken", () => {
  assert.equal(flodeAdress("demo", "fragor"), "#/p/demo/fragor");
  assert.equal(flodeAdress("demo", "projekt"), "#/");
  assert.equal(flodeAdress(null, "resultat"), "#/");
  const id = "Å ä/ö";
  assert.deepEqual(tolkaFlodeAdress(flodeAdress(id, "rapport")), { steg: "rapport", projekt: id });
});

test("stegraden har fem länkar i ordning och aria-current bara på aktivt steg", () => {
  const html = stegradHtml("demo", "fragor");
  assert.equal((html.match(/<a /g) ?? []).length, 5);
  assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1);
  assert.match(html, /<a class="flode-steg aktivt" href="#\/p\/demo\/fragor" aria-current="page">/);
  const ordning = STEG.map((s) => s.namn);
  assert.deepEqual(ordning, ["Projekt", "Förslag", "Frågor", "Resultat", "Rapport"]);
  let senast = -1;
  for (const n of ordning) {
    const i = html.indexOf(n);
    assert.ok(i > senast, n);
    senast = i;
  }
});

test("stegraden utan projekt länkar alla steg till listan", () => {
  assert.equal((stegradHtml(null, "projekt").match(/href="#\/"/g) ?? []).length, 5);
});

test("nästa steg: Rapport är sista", () => {
  assert.equal(nastaSteg("projekt"), "forslag");
  assert.equal(nastaSteg("resultat"), "rapport");
  assert.equal(nastaSteg("rapport"), null);
});
