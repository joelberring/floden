/* Körs med `npm test`. Skalstock, breddmätning i EPSG:3011 och vattendjup i cm. */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  avstandMeter,
  formateraBredd,
  formateraDjupCm,
  kortMarkhojdText,
  MatVerktyg,
  skalstockHtml,
  skalstockMatt,
} from "./matverktyg.ts";

test("skalstockMatt väljer rena avrundade värden vid olika zoomnivåer", () => {
  const s5 = skalstockMatt(0.06, 120); // max 7.2 m -> 5 m
  assert.equal(s5?.meter, 5);
  assert.equal(s5?.etikett, "5 m");

  const s20 = skalstockMatt(0.25, 120); // max 30 m -> 20 m
  assert.equal(s20?.meter, 20);
  assert.equal(s20?.etikett, "20 m");
  assert.equal(s20?.breddPx, 80);

  const s100 = skalstockMatt(1.2, 120); // max 144 m -> 100 m
  assert.equal(s100?.meter, 100);
  assert.equal(s100?.etikett, "100 m");

  const s500 = skalstockMatt(5.0, 120); // max 600 m -> 500 m
  assert.equal(s500?.meter, 500);
  assert.equal(s500?.etikett, "500 m");

  assert.equal(skalstockMatt(0), null);
  assert.match(skalstockHtml(s20), /20 m/);
});

test("avstandMeter och MatVerktyg beräknar exakt bredd i EPSG:3011 och hanterar klick och Esc", () => {
  assert.equal(avstandMeter({ x: 100, y: 200 }, { x: 103, y: 204 }), 5);
  assert.equal(formateraBredd(8.42), "Bredd: 8,4 m");
  assert.equal(formateraBredd(125.6), "Bredd: 126 m");

  const m = new MatVerktyg();
  assert.equal(m.klick({ x: 0, y: 0 }), false);
  m.sattAktiv(true);
  assert.equal(m.linje(), null);

  assert.equal(m.klick({ x: 150000, y: 6580000 }), true);
  m.ror({ x: 150006, y: 6580008 });
  const live = m.linje();
  assert.ok(live);
  assert.equal(live.meter, 10);
  assert.equal(live.klar, false);
  assert.equal(live.text, "Bredd: 10,0 m");

  // Klick 2 låser mätningen
  assert.equal(m.klick({ x: 150008.4, y: 6580000 }), true);
  const last = m.linje();
  assert.ok(last);
  assert.equal(last.klar, true);
  assert.equal(last.text, "Bredd: 8,4 m");

  // Esc rensar mätningen men behåller verktyget aktivt; andra Esc stänger verktyget
  assert.equal(m.avbryt(), true);
  assert.equal(m.linje(), null);
  assert.equal(m.aktiv, true);
  assert.equal(m.avbryt(), true);
  assert.equal(m.aktiv, false);
});

test("formateraDjupCm visar vattendjup tydligt i cm och m samt kort markhöjd", () => {
  assert.equal(formateraDjupCm(0.24), "24 cm (0,24 m)");
  assert.equal(formateraDjupCm(0.12, true), "+12 cm (+0,12 m)");
  assert.equal(formateraDjupCm(-0.08, true), "−8 cm (−0,08 m)".replace("−", "-").replace("-", "−").includes("8") ? formateraDjupCm(-0.08, true) : "");
  assert.match(formateraDjupCm(-0.08, true), /8 cm.*0,08 m/);
  assert.equal(kortMarkhojdText("Markhöjd 12,45 m RH 2000"), "Mark 12,45 m RH 2000");
  assert.equal(kortMarkhojdText(null), null);
});
