/* Körs med `npm test`. Den gamla kartsidan finns inte kvar: #/karta leder till motsvarande steg i flödet. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { tolkaFlodeAdress } from "./flode/adress.ts";
import { kartaOmdirigering, omdirigering, tolkaHash } from "./rutter.ts";

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
  for (const h of ["#/", "#/fragor", "#/matematik", "#/prov/webgpu", "#/p/demokvarteret/resultat", "#/kartor"]) {
    assert.equal(kartaOmdirigering(h), null, h);
  }
  assert.equal(tolkaHash("#/karta/demokvarteret").sida, "start");
  assert.equal(tolkaHash("#/matematik").sida, "matematik");
  assert.equal(tolkaHash("#/prov/webgpu").sida, "prov");
});

test("#/projekt/<id> och #/oversikt/<id> leder till projektets första steg i flödet", () => {
  assert.equal(omdirigering("#/projekt/demokvarteret"), "#/p/demokvarteret/forslag");
  assert.equal(omdirigering("#/projekt/demokvarteret/"), "#/p/demokvarteret/forslag");
  assert.equal(omdirigering("#/oversikt/abc123"), "#/p/abc123/forslag");
  assert.equal(omdirigering("#/projekt/mitt%20projekt"), "#/p/mitt%20projekt/forslag");
  assert.equal(omdirigering("#/projekt/%E0%A4%A"), "#/");
});

test("#/projekt och #/oversikt utan id leder till projektlistan", () => {
  assert.equal(omdirigering("#/projekt"), "#/");
  assert.equal(omdirigering("#/oversikt"), "#/");
  assert.equal(omdirigering("#/projekt/"), "#/");
});

test("målet för #/projekt/<id> är ett flödessteg i samma projekt", () => {
  const mal = omdirigering("#/projekt/demokvarteret");
  assert.ok(mal);
  const rutt = tolkaFlodeAdress(mal as string);
  assert.equal(rutt?.projekt, "demokvarteret");
  assert.equal(rutt?.steg, "forslag");
  assert.equal(omdirigering(mal as string), null); // ingen omdirigeringsslinga
});

test("#/fragor/<ref> leder till frågekatalogen, som själv är en sida och inte omdirigeras", () => {
  assert.equal(omdirigering("#/fragor/exempel%2Fdagsljus.toml"), "#/fragor");
  assert.equal(omdirigering("#/fragor/demo_vatten"), "#/fragor");
  assert.equal(omdirigering("#/fragor"), null);
  assert.equal(tolkaHash("#/fragor").sida, "fragor");
});

test("#/karta omdirigeras som förut via omdirigering", () => {
  assert.equal(omdirigering("#/karta"), "#/p/demokvarteret/forslag");
  assert.equal(omdirigering("#/karta/demokvarteret?vy=x"), "#/p/demokvarteret/resultat?vy=x");
});

test("okända adresser leder till projektlistan; sidor och flödessteg lämnas", () => {
  for (const h of ["#/finns-inte", "#/kartor", "#/p/demokvarteret/saknas", "#/p/demokvarteret/fragor/extra"]) {
    assert.equal(omdirigering(h), "#/", h);
  }
  for (const h of ["", "#", "#/", "#/nytt", "#/p/demokvarteret", "#/p/demokvarteret/rapport", "#/om", "#/matematik", "#/nycklar", "#/administration", "#/forvaltning", "#/prov/webgpu", "#/arbetsbank/A1_x"]) {
    assert.equal(omdirigering(h), null, h);
  }
});

test("adresserna som inte längre är egna sidor tolkas inte som sidor", () => {
  assert.equal(tolkaHash("#/projekt/demokvarteret").sida, "start");
  assert.equal(tolkaHash("#/oversikt").sida, "start");
});
