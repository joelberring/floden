/* Körs med `npm test`. Den gamla kartsidan finns inte kvar: #/karta leder till motsvarande steg i flödet. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { tolkaFlodeAdress } from "./flode/adress.ts";
import { kartaOmdirigering, tolkaHash } from "./rutter.ts";

test("#/karta utan vy leder till Förslag i demokvarteret", () => {
  assert.equal(kartaOmdirigering("#/karta"), "#/p/demokvarteret/forslag");
  assert.equal(kartaOmdirigering("#/karta/"), "#/p/demokvarteret/forslag");
  assert.equal(kartaOmdirigering("#/karta/demokvarteret"), "#/p/demokvarteret/forslag");
});

test("#/karta med vy leder till Resultat med samma vy", () => {
  assert.equal(kartaOmdirigering("#/karta/demokvarteret?vy=dagsljus-hojd"), "#/p/demokvarteret/resultat?vy=dagsljus-hojd");
  assert.equal(kartaOmdirigering("#/karta?vy=vatten-hus"), "#/p/demokvarteret/resultat?vy=vatten-hus");
});

test("projektets id följer med och specialtecken kodas", () => {
  assert.equal(kartaOmdirigering("#/karta/mitt%20projekt"), "#/p/mitt%20projekt/forslag");
  assert.equal(kartaOmdirigering("#/karta/%E0%A4%A"), "#/");
});

test("målet är en adress som flödet känner igen", () => {
  for (const h of ["#/karta", "#/karta/demokvarteret?vy=x"]) {
    const mal = kartaOmdirigering(h);
    assert.ok(mal);
    const rutt = tolkaFlodeAdress(mal as string);
    assert.ok(rutt, mal as string);
    assert.equal(rutt?.projekt, "demokvarteret");
    assert.equal(rutt?.steg, h.includes("vy=") ? "resultat" : "forslag");
  }
});

test("andra adresser omdirigeras inte, och karta är inte längre en egen sida", () => {
  for (const h of ["#/", "#/projekt/demokvarteret", "#/fragor", "#/matematik", "#/prov/webgpu", "#/p/demokvarteret/resultat", "#/kartor"]) {
    assert.equal(kartaOmdirigering(h), null, h);
  }
  assert.equal(tolkaHash("#/karta/demokvarteret").sida, "start");
  assert.equal(tolkaHash("#/matematik").sida, "matematik");
  assert.equal(tolkaHash("#/prov/webgpu").sida, "prov");
});
