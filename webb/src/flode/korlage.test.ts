import assert from "node:assert/strict";
import { test } from "node:test";

import { korHinder, korradHtml, utanForslagRuta } from "./korlage.ts";

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
