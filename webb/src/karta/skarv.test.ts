/* Körs med `npm test`. Skarven i klienten (R5): fynden som figurer och text, panelen, och åtgärden på objekten. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Punkt } from "./geom.ts";
import { PlanDok } from "./plandok.ts";
import type { RitLager, RollInfo } from "./ritlager.ts";
import {
  fyndBeskrivning,
  fyndText,
  fyndUtbredning,
  likaStorCirkel,
  omradesRingar,
  overfallText,
  pilspets,
  skarvFigurer,
  skarvPanelHtml,
  tillampaAtgard,
  utfallText,
  type Atgard,
  type AtgardSvar,
  type SkarvFynd,
  type Skarvar,
} from "./skarv.ts";

const SP = "\u202f"; // smalt hårt mellanrum (tal.ts)
const fynd = (over: Partial<SkarvFynd> = {}): SkarvFynd => ({
  nr: 1,
  typ: "ny",
  x_m: 1000,
  y_m: 2000,
  botten_rh2000: 13.5,
  overfall_rh2000: 13.65,
  overfall_x_m: 1010,
  overfall_y_m: 2000,
  storsta_djup_m: 0.12,
  djup_fore_m: 0,
  yta_m2: 314.16,
  volym_m3: 12.34,
  tillkommande_volym_m3: 1234.5,
  avstand_till_kant_m: 2,
  atgard: "text",
  ...over,
});
const skarvar = (f: SkarvFynd[]): Skarvar => ({
  kontrakt: "floden-skarvkontroll/v1",
  band_m: 10,
  antal: f.length,
  tillkommande_volym_m3: f.reduce((s, x) => s + x.tillkommande_volym_m3, 0),
  ovriga_nya_antal: 0,
  ovriga_tillkommande_volym_m3: 0,
  fynd: f,
});

test("texten använder decimalkomma, enheter och smalt mellanrum i tusental (tal.ts)", () => {
  const f = fynd();
  assert.equal(fyndBeskrivning(f), `Ny sänka: största djup 0,12 m, yta 314 m², volym 12,3 m³ (nytt 1${SP}234,5 m³)`);
  assert.equal(fyndText(fynd({ nr: 2, typ: "djupare" })), `2. Djupare sänka: största djup 0,12 m, yta 314 m², volym 12,3 m³ (nytt 1${SP}234,5 m³)`);
  assert.equal(overfallText(f), `Överfall 1${SP}010 E, 2${SP}000 N på 13,65 m RH 2000`);
  assert.equal(utfallText({ antal: 0, tillkommande_volym_m3: 0 }), "inga sänkor vid skarven");
  assert.equal(utfallText({ antal: 1, tillkommande_volym_m3: 3.25 }), "1 sänka vid skarven, 3,3 m³ nytt");
  assert.equal(utfallText({ antal: 2, tillkommande_volym_m3: 8 }), "2 sänkor vid skarven, 8,0 m³ nytt");
  assert.doesNotMatch(fyndText(f) + overfallText(f) + utfallText({ antal: 2, tillkommande_volym_m3: 8 }), /\d\.\d/);
});

test("området: polygon och multipolygon ger ytterringar, utan område ritas en cirkel med samma yta", () => {
  const ring = [[0, 0], [4, 0], [4, 4], [0, 0]];
  assert.equal(omradesRingar({ type: "Polygon", coordinates: [ring] }).length, 1);
  assert.equal(omradesRingar({ type: "MultiPolygon", coordinates: [[ring], [ring]] }).length, 2);
  assert.deepEqual(omradesRingar(undefined), []);
  const c = likaStorCirkel({ x: 0, y: 0 }, 100);
  assert.equal(c.length, 24);
  const radie = Math.sqrt(100 / Math.PI);
  for (const p of c) assert.ok(Math.abs(Math.hypot(p.x, p.y) - radie) < 1e-9);
  const f = skarvFigurer(skarvar([fynd({ omrade: { type: "Polygon", coordinates: [ring] } }), fynd({ nr: 2 })]));
  assert.equal(f.ytor.length, 2);
  assert.equal(f.ytor[0]?.ring.length, 4); // fyndets egen kant
  assert.equal(f.ytor[1]?.ring.length, 24); // cirkel
});

test("figurerna: en punkt vid botten, en pil till överfallet och en pilspets riktad bort från botten", () => {
  const f = skarvFigurer(skarvar([fynd()]), 0.5);
  assert.deepEqual(f.punkter[0]?.p, { x: 1000, y: 2000 });
  assert.match(f.punkter[0]?.text ?? "", /^1\. Ny sänka: största djup 0,12 m.*Överfall 1.010 E/);
  assert.deepEqual(f.pilar[0]?.linje, [{ x: 1000, y: 2000 }, { x: 1010, y: 2000 }]);
  const spets = f.pilar[0]?.spets as Punkt[];
  assert.deepEqual(spets[0], { x: 1010, y: 2000 }); // toppen i överfallet
  assert.ok(spets[1] && spets[2] && spets[1].x < 1010 && spets[2].x < 1010 && Math.abs(spets[1].y - spets[2].y) > 0.1);
  assert.equal(skarvFigurer(null).ytor.length, 0);
});

test("pilspetsen: storlek i meter, kortare än pilen, ingen spets utan sträcka", () => {
  const s = pilspets({ x: 0, y: 0 }, { x: 10, y: 0 }, 2);
  assert.equal(s.length, 3);
  assert.ok(Math.abs(s[1]!.x - 8) < 1e-9 && Math.abs(s[2]!.x - 8) < 1e-9);
  assert.ok(Math.abs(s[1]!.y + s[2]!.y) < 1e-9);
  const kort = pilspets({ x: 0, y: 0 }, { x: 1, y: 0 }, 5);
  assert.ok(kort[1]!.x >= 0.39); // högst 60 % av pilens längd
  assert.deepEqual(pilspets({ x: 3, y: 3 }, { x: 3, y: 3 }, 2), []);
});

test("utbredningen runt ett fynd rymmer botten, överfallet och sänkans kant med marginal", () => {
  const u = fyndUtbredning(fynd({ omrade: { type: "Polygon", coordinates: [[[990, 1995], [1005, 1995], [1005, 2005], [990, 1995]]] } }));
  assert.deepEqual(u, { x_min: 980, y_min: 1985, x_max: 1020, y_max: 2015 });
});

test("panelen: lista med nummer, knappen, och att den döljs utan fynd", () => {
  const bas = { skarvar: skarvar([fynd(), fynd({ nr: 2, typ: "djupare" })]), vantar: false, foreslar: false, forslag: null, fel: null, meddelande: null };
  const h = skarvPanelHtml(bas);
  assert.match(h, /Skarvar/);
  assert.equal((h.match(/class="skarv-fynd"/g) ?? []).length, 2);
  assert.match(h, /data-skarv="jamna">Jämna ut skarven</);
  assert.match(h, /2 nya eller djupare sänkor vid kanten på ny mark/);
  assert.doesNotMatch(h, /\d\.\d/);
  assert.match(skarvPanelHtml({ ...bas, vantar: true }), /data-skarv="jamna" disabled/);
  assert.match(skarvPanelHtml({ ...bas, vantar: true }), /räknas om/);
  assert.equal(skarvPanelHtml({ ...bas, skarvar: null }), "");
  assert.equal(skarvPanelHtml({ ...bas, skarvar: skarvar([]) }), "");
  assert.match(skarvPanelHtml({ ...bas, skarvar: null, meddelande: "Klart." }), /Klart\./);
});

const atgard = (over: Partial<Atgard> = {}): Atgard => ({
  slag: "slant",
  text: "Ge ytan 'mark1' en slänt 1:3 mot marken i stället för en kant.",
  steg: [{ slag: "slant", text: "Ge ytan 'mark1' en slänt 1:3 mot marken i stället för en kant." }],
  mal_id: "mark1",
  andra: [{ andring_id: "mark1", egenskaper: { slant: { forhallande: 3 } } }],
  nya: [],
  efter: { antal: 0, tillkommande_volym_m3: 0 },
  loser_allt: true,
  ...over,
});
const svar = (a: Atgard | null, skal = ""): AtgardSvar => ({ kontrakt: "floden-skarvatgard/v1", fore: { antal: 2, tillkommande_volym_m3: 9.5 }, atgard: a, skal });

test("panelen visar förslaget med före och efter, Tillämpa och Avbryt; ofullständig åtgärd säger det", () => {
  const bas = { skarvar: skarvar([fynd()]), vantar: false, foreslar: false, fel: null, meddelande: null };
  const h = skarvPanelHtml({ ...bas, forslag: svar(atgard()) });
  assert.match(h, /Föreslagen åtgärd/);
  assert.match(h, /Före: 2 sänkor vid skarven, 9,5 m³ nytt\. Efter åtgärden: inga sänkor vid skarven\./);
  assert.match(h, /data-skarv="tillampa"/);
  assert.match(h, /data-skarv="avbryt"/);
  assert.doesNotMatch(h, /data-skarv="jamna"/);
  assert.doesNotMatch(h, /tar inte bort alla/);
  const ofull = skarvPanelHtml({ ...bas, forslag: svar(atgard({ loser_allt: false, efter: { antal: 1, tillkommande_volym_m3: 2 } })) });
  assert.match(ofull, /Åtgärden tar inte bort alla sänkor\./);
  const flera = skarvPanelHtml({ ...bas, forslag: svar(atgard({ steg: [{ slag: "utlopp", text: "A." }, { slag: "slant", text: "B." }] })) });
  assert.match(flera, /<ol[^>]*><li>A\.<\/li><li>B\.<\/li><\/ol>/);
  const inget = skarvPanelHtml({ ...bas, forslag: svar(null, "Ingen åtgärd minskar sänkorna.") });
  assert.match(inget, /Ingen åtgärd minskar sänkorna\./);
  assert.match(inget, /data-skarv="jamna"/);
  assert.match(skarvPanelHtml({ ...bas, forslag: null, foreslar: true }), /Prövar åtgärder/);
  assert.match(skarvPanelHtml({ ...bas, forslag: null, fel: "<b>fel</b>" }), /&lt;b&gt;fel/);
});

const lager = (id: string, roll: RitLager["roll"]): RitLager => ({
  lager_id: id,
  namn: id,
  geometri: roll === "brytlinje" || roll === "oppning" ? "linje" : "polygon",
  roll,
  stil: {},
  synlig: true,
  last: false,
  tonad: false,
  opacitet: 1,
  kalla: "ritat",
  dataklass: "oppen",
  egenskaper_mall: {},
  egenskaper_forval: {},
});
const ROLLER: RollInfo[] = [{ roll: "oppning", namn: "Passage och trumma", geometri: ["linje"], blir: "Öppning.", egenskaper: ["typ"] }];
const dokMedMark = (lagerLista: RitLager[] = [lager("mark", "mark"), lager("passage", "oppning")]): PlanDok => {
  const d = new PlanDok();
  d.sattLager(lagerLista);
  d.laggTillFigur("yta", [{ x: 0, y: 0 }, { x: 20, y: 0 }, { x: 20, y: 20 }, { x: 0, y: 20 }], "mark", { niva_rh2000: 14.2 }, "mark1");
  return d;
};

test("åtgärden slänt: ytans egenskaper får släntförhållandet, ett steg för ångra", () => {
  const d = dokMedMark();
  const r = tillampaAtgard(d, atgard(), ROLLER);
  assert.deepEqual(r, { ok: true, andrade: ["mark1"], nya: [] });
  assert.deepEqual(d.figurer.get("mark1")?.egenskaper, { niva_rh2000: 14.2, slant: { forhallande: 3 } });
  d.angra();
  assert.deepEqual(d.figurer.get("mark1")?.egenskaper, { niva_rh2000: 14.2 });
  assert.equal(d.kanAngra(), true); // ytan själv
});

test("åtgärden utlopp: en linje i första öppningslagret, och lagret skapas om det saknas", () => {
  const utlopp = atgard({
    slag: "utlopp",
    andra: [],
    nya: [{ typ: "oppning_ny", andring_id: "skarv_utlopp", geometri: { type: "LineString", coordinates: [[5, 5], [5, 25]] }, egenskaper: { typ: "passage", bredd_m: 1, botten_rh2000: 13.62 } }],
  });
  const d = dokMedMark();
  assert.deepEqual(tillampaAtgard(d, utlopp, ROLLER), { ok: true, andrade: [], nya: ["skarv_utlopp"] });
  const f = d.figurer.get("skarv_utlopp");
  assert.equal(f?.lager, "passage");
  assert.equal(f?.typ, "linje");
  assert.deepEqual(f?.punkter, [{ x: 5, y: 5 }, { x: 5, y: 25 }]);
  assert.deepEqual(f?.egenskaper, { typ: "passage", bredd_m: 1, botten_rh2000: 13.62 });
  const d2 = dokMedMark([lager("mark", "mark")]);
  const r = tillampaAtgard(d2, utlopp, ROLLER);
  assert.equal(r.ok, true);
  const nytt = d2.lager.find((l) => l.roll === "oppning");
  assert.ok(nytt);
  assert.equal(d2.figurer.get("skarv_utlopp")?.lager, nytt.lager_id);
  // Samma id igen: en ledig id används, inget skrivs över.
  tillampaAtgard(d2, utlopp, ROLLER);
  assert.equal([...d2.figurer.values()].filter((x) => x.typ === "linje").length, 2);
});

test("allt eller inget: en yta som inte är ritad här ändras inte och ger ett tydligt besked", () => {
  const d = dokMedMark();
  const f = atgard({ andra: [{ andring_id: "mark1", egenskaper: { slant: { forhallande: 3 } } }, { andring_id: "fil_yta", egenskaper: { slant: { forhallande: 3 } } }] });
  const r = tillampaAtgard(d, f, ROLLER);
  assert.equal(r.ok, false);
  assert.match(r.ok ? "" : r.fel, /fil_yta är inte ritad i det här förslaget/);
  assert.deepEqual(d.figurer.get("mark1")?.egenskaper, { niva_rh2000: 14.2 });
  const okand = tillampaAtgard(d, atgard({ andra: [], nya: [{ typ: "brytlinje_ny", andring_id: "x", geometri: { type: "LineString", coordinates: [[0, 0], [1, 1]] }, egenskaper: {} }] }), ROLLER);
  assert.equal(okand.ok, false);
});
