import assert from "node:assert/strict";
import { test } from "node:test";
import { flodeAdress, huvudSteg, installningarAdress, nastaSteg, STEG, stegradHtml, tolkaFlodeAdress } from "./adress.ts";

test("#/ är projektlistan och #/p/<projekt> börjar på Karta & analys", () => {
  assert.deepEqual(tolkaFlodeAdress("#/"), { steg: "projekt", projekt: null });
  assert.deepEqual(tolkaFlodeAdress(""), { steg: "projekt", projekt: null });
  assert.deepEqual(tolkaFlodeAdress("#/p/demo"), { steg: "karta", projekt: "demo" });
  assert.deepEqual(tolkaFlodeAdress("#/p/demo/karta"), { steg: "karta", projekt: "demo" });
});

test("alla projektsteg (inkl. bakåtkompatibla) tolkas, med och utan frågetecken", () => {
  for (const s of ["karta", "forslag", "fragor", "resultat", "rapport"]) {
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
  assert.equal(flodeAdress("demo", "karta"), "#/p/demo/karta");
  assert.equal(flodeAdress("demo", "fragor"), "#/p/demo/fragor");
  assert.equal(flodeAdress("demo", "projekt"), "#/");
  assert.equal(flodeAdress(null, "resultat"), "#/");
  const id = "Å ä/ö";
  assert.deepEqual(tolkaFlodeAdress(flodeAdress(id, "rapport")), { steg: "rapport", projekt: id });
});

test("stegraden har tre steg i ordning och mappar äldre steg till rätt aktivt huvudsteg", () => {
  const html = stegradHtml("demo", "karta");
  assert.equal((html.match(/<a /g) ?? []).length, 3);
  assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1);
  assert.match(html, /<a class="flode-steg aktivt" href="#\/p\/demo\/karta" aria-current="page">/);
  const ordning = STEG.map((s) => s.namn);
  assert.deepEqual(ordning, ["Projekt", "Karta & analys", "Rapport"]);
  let senast = -1;
  for (const n of ordning) {
    const i = html.indexOf(n.replace("&", "&amp;"));
    assert.ok(i > senast, n);
    senast = i;
  }
  assert.equal(huvudSteg("forslag"), "karta");
  assert.equal(huvudSteg("resultat"), "karta");
  assert.equal(huvudSteg("fragor"), "rapport");
  assert.match(stegradHtml("demo", "forslag"), /href="#\/p\/demo\/karta" aria-current="page"/);
});

test("stegraden utan projekt länkar alla steg till listan", () => {
  assert.equal((stegradHtml(null, "projekt").match(/href="#\/"/g) ?? []).length, 3);
});

test("nästa steg: Projekt → Karta & analys → Rapport", () => {
  assert.equal(nastaSteg("projekt"), "karta");
  assert.equal(nastaSteg("karta"), "rapport");
  assert.equal(nastaSteg("forslag"), "rapport");
  assert.equal(nastaSteg("resultat"), "rapport");
  assert.equal(nastaSteg("rapport"), null);
});

test("projektets inställningar och arkiverade projekt har egna adresser", () => {
  assert.deepEqual(tolkaFlodeAdress("#/p/demo/installningar"), { steg: "karta", projekt: "demo", installningar: true });
  assert.equal(tolkaFlodeAdress("#/p/demo/installningar/extra"), null);
  assert.deepEqual(tolkaFlodeAdress("#/?arkiverade=1"), { steg: "projekt", projekt: null, arkiverade: true });
  assert.deepEqual(tolkaFlodeAdress("#/?arkiverade=0"), { steg: "projekt", projekt: null });
  assert.equal(installningarAdress("Å ä"), "#/p/%C3%85%20%C3%A4/installningar");
});
