import assert from "node:assert/strict";
import { test } from "node:test";

import { korHinder, korradHtml, uppskattadTidText, utanForslagRuta } from "./korlage.ts";

const esc = (s: string): string => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const bas = { harForslag: true, antalBerakningar: 2, avtappningFel: false, pagar: false };

test("hinder i prioritetsordning: förslag, avtappning, frågor", () => {
  assert.equal(korHinder({ ...bas, harForslag: false, avtappningFel: true }), "forslag");
  assert.equal(korHinder({ ...bas, avtappningFel: true }), "avtappning");
  assert.equal(korHinder({ ...bas, antalBerakningar: 0 }), "fragor");
  assert.equal(korHinder(bas), null);
});

test("utan förslag: ingen död körknapp, i stället Rita ett förslag och Visa projektets befintliga resultat", () => {
  const h = korradHtml({ ...bas, harForslag: false }, "demo", esc);
  assert.doesNotMatch(h, /id="fr-kor"/);
  assert.match(h, /href="#\/p\/demo\/forslag">Rita ett förslag/);
  assert.match(h, /href="#\/p\/demo\/resultat">Visa projektets befintliga resultat/);
  assert.match(h, /inte ritat något/);
});

test("utan förslag: Räkna läget idag ligger bredvid Rita ett förslag i handlingsraden och visar uppskattad tid", () => {
  const h = korradHtml({ ...bas, harForslag: false, lageTidText: "ungefär 12 s" }, "demo", esc);
  assert.match(h, /id="fr-rita"[^>]*>Rita ett förslag<\/a><button type="button" class="flode-knapp stor" id="fr-lage">Räkna läget idag<\/button>/);
  assert.doesNotMatch(h, /id="fr-lage"[^>]*disabled/);
  assert.match(h, /Uppskattad tid för läget idag: ungefär 12 s/);
});

test("utan förslag och utan valda frågor är Räkna läget idag inaktiv; med förslag finns den inte; under körning ersätts raden", () => {
  assert.match(korradHtml({ ...bas, harForslag: false, antalBerakningar: 0 }, "demo", esc), /id="fr-lage"[^>]*disabled/);
  assert.doesNotMatch(korradHtml(bas, "demo", esc), /fr-lage/);
  const nu = korradHtml({ ...bas, harForslag: false, pagar: true, forloppText: "Beräknar läget idag …" }, "demo", esc);
  assert.doesNotMatch(nu, /fr-lage/);
  assert.match(nu, /Beräknar läget idag/);
});

test("uppskattad tid: tal via tal.ts, 0 är klart i cachen, okänt är streck", () => {
  assert.equal(uppskattadTidText([0, 0]), "klart i cachen");
  assert.equal(uppskattadTidText([1200.4, null]), "–");
  assert.equal(uppskattadTidText([]), "–");
  assert.match(uppskattadTidText([1200.4, 100]), /^ungefär 1.300 s$/);
});

test("med förslag och frågor: aktiv körknapp; utan frågor: inaktiv med förklaring", () => {
  assert.doesNotMatch(korradHtml(bas, "demo", esc), /id="fr-kor"[^>]*disabled/);
  const h = korradHtml({ ...bas, antalBerakningar: 0 }, "demo", esc);
  assert.match(h, /id="fr-kor"[^>]*disabled/);
  assert.match(h, /Välj minst en fråga/);
});

test("under körning visas förloppet och knappen är inaktiv; fel visas med role=alert", () => {
  const h = korradHtml({ ...bas, pagar: true, forloppText: "Beräknar … 1 av 3" }, "demo", esc);
  assert.match(h, /Beräknar … 1 av 3/);
  assert.match(h, /disabled/);
  assert.match(korradHtml({ ...bas, korFel: "Beräkningen misslyckades: x" }, "demo", esc), /role="alert"[^>]*>Beräkningen misslyckades/);
});

test("förklaringsrutan har båda handlingarna", () => {
  const h = utanForslagRuta("demo", esc);
  assert.match(h, /Rita ett förslag/);
  assert.match(h, /befintliga resultat/);
});
