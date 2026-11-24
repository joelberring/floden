import assert from "node:assert/strict";
import { test } from "node:test";

import { rollNamn } from "../konto.ts";
import { nyttProjektHtml, radHandling } from "./projektlogik.ts";

test("projektraden säger vad ett klick gör: välja eller öppna", () => {
  assert.match(radHandling(false), /Visa på kartan/);
  assert.match(radHandling(true), /Öppna projektet/);
});

test("Nytt projekt: knapp när inloggning är på, förklaring (ingen död länk) i lokalt läge", () => {
  assert.match(nyttProjektHtml("in", "#/nytt"), /<a [^>]*id="nytt-projekt" href="#\/nytt">Nytt projekt<\/a>/);
  const lokalt = nyttProjektHtml("av", "#/nytt");
  assert.doesNotMatch(lokalt, /href=/);
  assert.match(lokalt, /floden projekt skapa/);
  assert.match(nyttProjektHtml("okand", "#/nytt"), /id="nytt-projekt"/);
});

test("roller visas i klartext med å, ä och ö", () => {
  assert.equal(rollNamn("lasare"), "läsare");
  assert.equal(rollNamn("agare"), "ägare");
  assert.equal(rollNamn("utredare"), "utredare");
  assert.equal(rollNamn("okänd-roll"), "okänd-roll");
});
