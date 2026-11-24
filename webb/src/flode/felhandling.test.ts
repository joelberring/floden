import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiFel, OinloggadFel } from "../apifel.ts";
import type { KontoLage } from "../konto.ts";
import { felBeskedHtml, felSlag, ingenGeografi, nyttProjektFel, sidFel } from "./felhandling.ts";

const AV: KontoLage = { lage: "av" };
const UT: KontoLage = { lage: "ut" };
const IN: KontoLage = { lage: "in", namn: "Pia", epost: "p@example.org", medlemskap: [], projekt: [] };
const esc = (s: string): string => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

test("felSlag skiljer inloggning, behörighet, nätverk och serverfel", () => {
  assert.equal(felSlag(new OinloggadFel()).slag, "inloggning");
  assert.equal(felSlag(new ApiFel("x", 401)).slag, "inloggning");
  assert.equal(felSlag(new ApiFel("x", 403)).slag, "behorighet");
  assert.equal(felSlag(new ApiFel("x", 0)).slag, "natverk");
  assert.equal(felSlag(new TypeError("fetch failed")).slag, "natverk");
  assert.equal(felSlag(new ApiFel("x", 503)).slag, "server");
  assert.equal(felSlag(new ApiFel("x", 404)).slag, "annat");
});

test("Nytt projekt, utloggad: länk till inloggning med återvändsadress", () => {
  const b = nyttProjektFel(new ApiFel("kräver inloggning", 403), UT);
  assert.equal(b.slag, "inloggning");
  assert.match(b.handlingar[0]?.href ?? "", /^\/konto\/logga-in\/google\?atervand=%2F%23%2Fnytt$/);
  assert.equal(nyttProjektFel(new OinloggadFel(), IN).slag, "inloggning");
});

test("Nytt projekt, lokalt utan inloggning: förklaring med kommandot, ingen inloggningslänk", () => {
  const b = nyttProjektFel(new ApiFel("kräver inloggning (lokalt: floden projekt skapa)", 403), AV);
  assert.equal(b.slag, "lokalt");
  assert.match(b.text, /floden projekt skapa/);
  assert.ok(b.handlingar.every((h) => !(h.href ?? "").includes("logga-in")));
});

test("Nytt projekt, inloggad utan rätt: säger vem som kan skapa projekt och att man kan be sin organisationsadmin", () => {
  const b = nyttProjektFel(new ApiFel("nej", 403), IN);
  assert.equal(b.slag, "behorighet");
  assert.match(b.text, /superadmin/);
  assert.match(b.text, /organisationsadmin/);
  assert.match(b.text, /ägare/);
  assert.match(b.text, /Be din organisationsadmin/);
});

test("Nätverks- och serverfel ger Försök igen, och ingen 'Är servern igång?'", () => {
  for (const e of [new ApiFel("x", 0), new ApiFel("x", 500)]) {
    const b = nyttProjektFel(e, IN);
    assert.equal(b.handlingar[0]?.text, "Försök igen");
    assert.doesNotMatch(b.text + b.rubrik, /Är servern igång/);
  }
});

test("ingen geografi: olika text för superadmin och andra", () => {
  assert.match(ingenGeografi(true).text, /Mappa en geografi/);
  assert.match(ingenGeografi(false).text, /Be en superadmin/);
});

test("sidFel: behörighet, nätverk och inloggning har varsin handling", () => {
  assert.match(sidFel(new ApiFel("x", 403), "Frågorna", IN, "#/p/a/fragor").text, /läsare eller utredare/);
  assert.equal(sidFel(new ApiFel("x", 0), "Frågorna", IN, "#/p/a/fragor").handlingar[0]?.href, "#/p/a/fragor");
  assert.equal(sidFel(new OinloggadFel(), "Frågorna", UT, "/#/p/a/fragor").handlingar[0]?.text, "Logga in");
});

test("felBeskedHtml har role=alert, rubrik och en länk per handling, och escapar", () => {
  const h = felBeskedHtml({ slag: "annat", rubrik: "<b>", text: "t", handlingar: [{ text: "Gå", href: "#/" }] }, esc);
  assert.match(h, /role="alert"/);
  assert.match(h, /&lt;b>/);
  assert.match(h, /<a class="flode-knapp svart" href="#\/">Gå<\/a>/);
});
