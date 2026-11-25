/* Körs med `npm test`. Teckenförklaring och kartförklaring: rubrik, klasser, enheter och osäkerhetsnotis. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { LagerPost } from "./fragor-typer.ts";
import { lasKartan, lasKartanText, OSAKERHET_FORE_NOTIS, osakerhetNotisText } from "./kartforklaring.ts";
import { rubrikMedEnhet, teckenforklaringHtml } from "./teckenforklaring.ts";
import type { ProjektVy } from "./types.ts";

const klassLager: LagerPost = {
  id: "vatten.fore.max_djup",
  namn: "Största vattendjup",
  etikett: "Största vattendjup",
  analys: "skyfall",
  scenario: "fore",
  roll: "max_djup",
  typ: "bas",
  primar: true,
  enhet: "m",
  farg: "vatten",
  skala: { min: 0, max: 0.5 },
  teckenforklaring: {
    typ: "klasser",
    klasser: [
      { fran: 0.1, farg: "#dcebe6", etikett: "0,1–0,2 m" },
      { fran: 0.2, farg: "#8fb9ac", etikett: "0,2–0,3 m" },
      { fran: 0.5, farg: "#234b66", etikett: "" },
    ],
  },
  utbredning: { x_min: 0, y_min: 0, x_max: 100, y_max: 100 },
  synlig_utbredning: null,
  pixelstorlek_m: 1,
  bredd_px: 100,
  hojd_px: 100,
  nedsampling: 1,
  png: "/x.png",
  png_med_tak: null,
  tak_maskat: false,
  tak_celler: 0,
  tak_yta_m2: 0,
  geotiff: [],
};

test("rubrikMedEnhet lägger till enheten en gång när den saknas", () => {
  assert.equal(rubrikMedEnhet("Största vattendjup", "m"), "Största vattendjup (m)");
  assert.equal(rubrikMedEnhet("Vindförstärkning (gånger)", "gånger"), "Vindförstärkning (gånger)");
  assert.equal(rubrikMedEnhet("Andel himmel", ""), "Andel himmel");
});

test("teckenforklaringHtml visar rubrik med enhet, färgrutor med exakta klasser och valfri osäkerhetsnotis", () => {
  const utan = teckenforklaringHtml(klassLager, "Största vattendjup");
  assert.match(utan, /Största vattendjup \(m\)/);
  assert.match(utan, /background:#dcebe6/);
  assert.match(utan, /0,1–0,2 m/);
  assert.match(utan, /från 0,5 m/);
  assert.doesNotMatch(utan, /tecken-notis/);

  const med = teckenforklaringHtml(klassLager, "Största vattendjup", { osakerhetFore: true });
  assert.match(med, /class="tecken-notis"/);
  assert.ok(med.includes(OSAKERHET_FORE_NOTIS));
  assert.equal(osakerhetNotisText(false), "");
  assert.equal(osakerhetNotisText(true), OSAKERHET_FORE_NOTIS);
});

test("lasKartan och lasKartanText beskriver luft, ekologi, ankomsttid och projektets avgränsning", () => {
  const vyLuft = { avsnitt: "luft", efter_namn: "Nytt kvarter" } as ProjektVy;
  const textLuft = lasKartan(vyLuft, "efter", false);
  assert.match(textLuft, /luftmiljö på gånghöjd/);
  assert.match(textLuft, /skogsgröna linjen visar projektets avgränsning/);

  const ankomst = lasKartanText({ ...klassLager, roll: "ankomsttid", enhet: "min" }, false);
  assert.match(ankomst, /ankomsttid.*0,1 m/);
});
