import assert from "node:assert/strict";
import { test } from "node:test";
import { inloggningsAdress } from "./konto.ts";
import { sidaInloggning } from "./sidor.ts";

test("inloggningssidan har namnet, meningen, knappen och bilden – och inget mer om verktyget", () => {
  const html = sidaInloggning(inloggningsAdress("/"));
  assert.match(html, /<h1[^>]*>Floden<\/h1>/);
  assert.match(html, /Hantera floden av utredningsfrågor på ett ställe\./);
  assert.match(html, /href="\/konto\/logga-in\/google\?atervand=%2F"/);
  assert.match(html, /Logga in med Google/);
  assert.match(html, /src="\/bild\/inloggning-1600\.webp"/);
  assert.match(html, /alt="[^"]+"/);
  for (const ord of ["skyfall", "dagsljus", "buller", "demokvarter", "hus"]) {
    assert.doesNotMatch(html.toLowerCase(), new RegExp(ord), `inloggningssidan nämner ${ord}`);
  }
});
