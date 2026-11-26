import assert from "node:assert/strict";
import { test } from "node:test";

import { rollNamn } from "../konto.ts";
import {
  nyttProjektHtml,
  projektOrganisationNamn,
  projektRadHtml,
  radHandling,
  rollRad,
  tomProjektlistaHtml,
} from "./projektlogik.ts";

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

test("U5g: organisationsnamn, roll och delning visas på projektraden", () => {
  assert.equal(projektOrganisationNamn({ organisation_namn: "  Stockholms stad  " }), "Stockholms stad");
  assert.equal(projektOrganisationNamn({ organisation: "Göteborgs stad" }), "Göteborgs stad");
  assert.equal(projektOrganisationNamn({ organisation_namn: "   " }), null);

  assert.equal(
    rollRad({ organisation_namn: "Stockholms stad", roll: "utredare", organisation_synlig: true }),
    "Stockholms stad · Din roll: utredare · delat med organisationen",
  );
  assert.equal(
    rollRad({ organisation: "Göteborgs stad", roll: "lasare", organisation_synlig: false }),
    "Göteborgs stad · Din roll: läsare · privat",
  );

  const kortHtml = projektRadHtml({
    id: "p1",
    namn: "Haga",
    undertitel: "Projekt",
    paahittat: false,
    organisation_namn: "Göteborgs stad",
    roll: "utredare",
    organisation_synlig: true,
    statusText: "Skyfall beräknade",
    href: "#/p/p1/karta",
    vald: true,
    miniatyr: null,
  });
  assert.match(kortHtml, /class="flode-projekt-org">Göteborgs stad<\/span>/);
  assert.match(kortHtml, /Din roll: utredare/);
  assert.match(kortHtml, /delat med organisationen/);
});

test("U5g: tom projektlista visar användarvägledning inloggat (ej floden demo) och demo-tips enbart lokalt", () => {
  const inloggad = tomProjektlistaHtml("in", false, "#/nytt");
  assert.doesNotMatch(inloggad, /floden demo/i);
  assert.match(inloggad, /Nytt projekt/);
  assert.match(inloggad, /href="#\/nytt"/);

  const okand = tomProjektlistaHtml("okand", false, "#/nytt");
  assert.doesNotMatch(okand, /floden demo/i);
  assert.match(okand, /Nytt projekt/);

  const lokal = tomProjektlistaHtml("av", false, "#/nytt");
  assert.match(lokal, /floden demo/);

  const arkiverad = tomProjektlistaHtml("in", true, "#/nytt");
  assert.doesNotMatch(arkiverad, /floden demo/i);
  assert.match(arkiverad, /inga arkiverade projekt/i);
});
