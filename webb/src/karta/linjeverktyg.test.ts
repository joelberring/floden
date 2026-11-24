/* Körs med `npm test`. Linje- och ytverktyget (R3): rita, fäst, vinkellås, följ kant, klipp, slå ihop, vänd, förenkla, ångra. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Punkt } from "./geom.ts";
import { PlanDok } from "./plandok.ts";
import type { RitLager } from "./ritlager.ts";
import { Ritverktyg, type Meddelande, type Pekare, type Tangent } from "./ritverktyg.ts";

const mus: Pekare = { pekare: "mouse", ctrl: false, shift: false, alt: false };
const finger: Pekare = { pekare: "touch", ctrl: false, shift: false, alt: false };
const tang = (key: string, extra: Partial<Tangent> = {}): Tangent => ({ key, code: "", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...extra });
const P = (x: number, y: number): Punkt => ({ x, y });

const lager = (id: string, roll: RitLager["roll"], over: Partial<RitLager> = {}): RitLager => ({
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
  ...over,
});

function skapa(lagerLista: RitLager[] = [lager("hus", "byggnad"), lager("mark", "mark"), lager("mur", "brytlinje")]) {
  const dok = new PlanDok();
  dok.sattLager(lagerLista);
  const meddelanden: string[] = [];
  const skapade: string[] = [];
  const bas = [{ id: "b1", ring: [P(100, 100), P(110, 100), P(110, 110), P(100, 110)], hal: [], hojd_m: 9 }];
  const extra = [{ id: "bl:0", punkter: [P(300, 300), P(320, 300)], sluten: false }];
  const r = new Ritverktyg(dok, bas, { meterPerPx: () => 0.5, meddela: (m: Meddelande | null) => void (m && meddelanden.push(m.text)), figurSkapad: (id) => skapade.push(id) }, extra);
  return { dok, r, meddelanden, skapade };
}

function klicka(r: Ritverktyg, punkter: Array<[number, number]>, e: Pekare = mus): void {
  for (const [x, y] of punkter) {
    r.ror({ x, y }, e);
    r.klick({ x, y }, e);
  }
}

const xy = (l: readonly Punkt[]): Array<[number, number]> => l.map((p) => [p.x, p.y]);

test("E ritar en linje i murlagret: klick, Enter, egenskaper från rollen, ångra tar bort den", () => {
  const { r, dok, skapade } = skapa();
  assert.equal(r.tangent(tang("e")), true);
  assert.equal(r.verktyg, "linje");
  klicka(r, [[10, 10], [20, 10], [20, 20]]);
  assert.equal(r.tangent(tang("Enter")), true);
  const f = dok.figurer.get(skapade[0]!)!;
  assert.deepEqual(xy(f.punkter), [[10, 10], [20, 10], [20, 20]]);
  assert.equal(f.lager, "mur");
  assert.deepEqual(f.egenskaper, { typ: "mur", hojd_m: 1 });
  assert.equal(r.verktyg, "valj");
  assert.equal(r.linje.val, f.id);
  const k50 = dok.tillRitlager();
  const o = k50.objekt.find((x) => x.objekt_id === f.id)!;
  assert.equal(o.geometri.type, "LineString");
  assert.equal(dok.antalILager("mur"), 1);
  dok.angra();
  assert.equal(dok.figurer.size, 0);
  dok.goraOm();
  assert.equal(dok.figurer.size, 1);
});

test("yta: tre hörn och Enter i marklagret; korsande kanter och för få punkter avvisas", () => {
  const { r, dok, meddelanden } = skapa();
  r.sattVerktyg("yta");
  klicka(r, [[0, 0], [10, 0]]);
  r.tangent(tang("Enter"));
  assert.equal(dok.figurer.size, 0);
  assert.match(meddelanden.at(-1)!, /minst tre hörn/);
  klicka(r, [[10, 10], [0, 10]]);
  assert.equal(r.tangent(tang("Enter")), true);
  const f = [...dok.figurer.values()][0]!;
  assert.equal(f.typ, "yta");
  assert.equal(f.lager, "mark");
  // Regression (webbläsarprovet): Enter efter fyra klick gav en yta utan hörn, för att ringen töms innan den kopierats.
  assert.deepEqual(xy(f.punkter), [[0, 0], [10, 0], [10, 10], [0, 10]]);
  const o = dok.tillRitlager().objekt.find((x) => x.objekt_id === f.id)!;
  assert.equal(o.geometri.type, "Polygon");
  r.sattVerktyg("yta");
  klicka(r, [[50, 50], [60, 60], [60, 50], [50, 60]]);
  r.tangent(tang("Enter"));
  assert.match(meddelanden.at(-1)!, /korsar varandra/);
  assert.equal(dok.figurer.size, 1);
});

test("inget lager som passar: ett tydligt besked och inget utkast", () => {
  const { r, meddelanden } = skapa([lager("hus", "byggnad")]);
  r.sattVerktyg("linje");
  klicka(r, [[10, 10]]);
  assert.equal(r.linje.utkast.length, 0);
  assert.match(meddelanden.at(-1)!, /inget tänt och olåst lager för linjer/);
});

test("fäst mot byggnadens hörn och kant, och mot projektets brytlinje (egen räckvidd för finger)", () => {
  const { r } = skapa();
  r.sattVerktyg("linje");
  klicka(r, [[101.5, 99]]);
  assert.deepEqual(xy(r.linje.utkast), [[100, 100]]);
  klicka(r, [[110.4, 105]]); // kant på x = 110
  assert.deepEqual(xy(r.linje.utkast).at(-1), [110, 105]);
  r.avbrytUtkast();
  r.linje.avbryt();
  klicka(r, [[323, 303]]); // 4 m från brytlinjens ände (320,300): mus räckvidd 5 m
  assert.deepEqual(xy(r.linje.utkast), [[320, 300]]);
  r.linje.avbryt();
  klicka(r, [[310, 308]]); // 8 m: bara med finger (11 m)
  assert.deepEqual(xy(r.linje.utkast), [[310, 308]]);
  r.linje.avbryt();
  klicka(r, [[310, 308]], finger);
  assert.deepEqual(xy(r.linje.utkast), [[310, 300]]);
});

test("fäst av: T stänger av fästet", () => {
  const { r } = skapa();
  r.tangent(tang("t"));
  r.sattVerktyg("linje");
  klicka(r, [[101.5, 99]]);
  assert.deepEqual(xy(r.linje.utkast), [[101.5, 99]]);
});

test("vinkellås: R låser sträckan till 90 grader mot norr, sedan mot förra sträckan", () => {
  const { r } = skapa();
  r.sattVerktyg("linje");
  r.tangent(tang("r"));
  klicka(r, [[0, 0], [10, 1], [11, 8]]);
  assert.deepEqual(xy(r.linje.utkast), [[0, 0], [10, 0], [10, 8]]);
  assert.ok(r.lasLinje);
});

test("följ kant: nya punkter följer byggnadens kant och sammanfaller exakt med dess hörn", () => {
  const { r, dok } = skapa();
  r.sattVerktyg("linje");
  klicka(r, [[100.5, 105]]); // fäst på vänsterkanten
  assert.deepEqual(xy(r.linje.utkast), [[100, 105]]);
  r.tangent(tang("f"));
  assert.equal(r.foljKant, true);
  r.ror({ x: 105, y: 110.4 }, mus);
  assert.deepEqual(xy(r.linje.foljForhand ?? []), [[100, 105], [100, 110], [105, 110]]);
  r.klick({ x: 105, y: 110.4 }, mus);
  assert.deepEqual(xy(r.linje.utkast), [[100, 105], [100, 110], [105, 110]]);
  // Vidare längs ringen: höger kant nås via hörnet (110,110)
  r.ror({ x: 110.4, y: 105 }, mus);
  r.klick({ x: 110.4, y: 105 }, mus);
  const u = xy(r.linje.utkast);
  assert.deepEqual(u.slice(0, 3), [[100, 105], [100, 110], [105, 110]]);
  assert.deepEqual(u.slice(3), [[110, 110], [110, 105]]);
  assert.ok(u.some(([x, y]) => x === 110 && y === 110), "hörnet (110,110) tas med");
  r.tangent(tang("Enter"));
  const f = [...dok.figurer.values()][0]!;
  for (const h of [[100, 110], [110, 110]]) assert.ok(f.punkter.some((p) => p.x === h[0] && p.y === h[1]));
});

test("klipp: X delar linjen i två i ett ångrasteg", () => {
  const { r, dok } = skapa();
  r.sattVerktyg("linje");
  klicka(r, [[10, 10], [30, 10], [30, 30]]);
  r.tangent(tang("Enter"));
  r.sattVerktyg("klipp");
  r.klick({ x: 20, y: 10.5 }, mus);
  assert.equal(dok.figurer.size, 2);
  const delar = [...dok.figurer.values()].map((f) => xy(f.punkter));
  assert.deepEqual(delar[0], [[10, 10], [20, 10]]);
  assert.deepEqual(delar[1], [[20, 10], [30, 10], [30, 30]]);
  dok.angra();
  assert.equal(dok.figurer.size, 1);
  assert.equal(dok.figurer.get("linje_1")!.punkter.length, 3);
});

test("slå ihop: två linjer i samma lager blir en, ångra ger tillbaka båda", () => {
  const { r, dok } = skapa();
  for (const l of [[[0, 0], [10, 0]], [[10, 0], [20, 5]]] as Array<Array<[number, number]>>) {
    r.sattVerktyg("linje");
    klicka(r, l);
    r.tangent(tang("Enter"));
  }
  assert.equal(dok.figurer.size, 2);
  r.sattVerktyg("ihop");
  r.klick({ x: 5, y: 0 }, mus);
  r.klick({ x: 15, y: 2.5 }, mus);
  assert.equal(dok.figurer.size, 1);
  assert.deepEqual(xy([...dok.figurer.values()][0]!.punkter), [[0, 0], [10, 0], [20, 5]]);
  dok.angra();
  assert.equal(dok.figurer.size, 2);
});

test("vänd (V) och förenkla (Skift+S) på den markerade figuren", () => {
  const { r, dok } = skapa();
  r.sattVerktyg("linje");
  klicka(r, [[0, 0], [10, 0.05], [20, 0], [20, 10]]);
  r.tangent(tang("Enter"));
  const id = r.linje.val!;
  r.tangent(tang("v"));
  assert.deepEqual(xy(dok.figurer.get(id)!.punkter), [[20, 10], [20, 0], [10, 0.05], [0, 0]]);
  r.tangent(tang("S", { shiftKey: true }));
  assert.deepEqual(xy(dok.figurer.get(id)!.punkter), [[20, 10], [20, 0], [0, 0]]);
  dok.angra();
  assert.equal(dok.figurer.get(id)!.punkter.length, 4);
});

test("markera, dra ett hörn, lägg till och ta bort punkt, Delete", () => {
  const { r, dok } = skapa();
  r.sattVerktyg("linje");
  klicka(r, [[10, 10], [30, 10]]);
  r.tangent(tang("Enter"));
  const id = r.linje.val!;
  // dra slutpunkten
  assert.equal(r.ned({ x: 30, y: 10 }, mus), true);
  r.ror({ x: 30, y: 25 }, mus);
  r.upp();
  assert.deepEqual(xy(dok.figurer.get(id)!.punkter), [[10, 10], [30, 25]]);
  // lägg till en punkt på sträckan
  r.sattVerktyg("laggtill");
  r.klick({ x: 20, y: 17.5 }, mus);
  assert.equal(dok.figurer.get(id)!.punkter.length, 3);
  // ta bort den igen
  r.sattVerktyg("tabort");
  r.klick({ x: 20, y: 17.5 }, mus);
  assert.equal(dok.figurer.get(id)!.punkter.length, 2);
  r.sattVerktyg("valj");
  r.tangent(tang("Delete"));
  assert.equal(dok.figurer.size, 0);
});

test("Esc avbryter utkastet; Backsteg tar bort senaste punkten", () => {
  const { r, dok } = skapa();
  r.sattVerktyg("linje");
  klicka(r, [[10, 10], [20, 10], [30, 10]]);
  r.tangent(tang("Backspace"));
  assert.equal(r.linje.utkast.length, 2);
  r.tangent(tang("Escape"));
  assert.equal(r.linje.utkast.length, 0);
  assert.equal(dok.figurer.size, 0);
});

test("figurer följer med K50 fram och tillbaka, och lagret går inte att ta bort medan det har figurer", () => {
  const { r, dok } = skapa();
  r.sattVerktyg("linje");
  klicka(r, [[10, 10], [20, 10]]);
  r.tangent(tang("Enter"));
  const k50 = dok.tillRitlager();
  const dok2 = new PlanDok();
  dok2.fran([], k50);
  assert.equal(dok2.figurer.size, 1);
  assert.deepEqual(xy(dok2.figurer.get("linje_1")!.punkter), [[10, 10], [20, 10]]);
  assert.equal(dok.taBortLager("mur"), false);
});

test("pekaren lämnar kartan mitt i en linje: förhandsvisningen försvinner utan NaN, utkastet finns kvar (webbläsarprovet)", () => {
  const { r } = skapa();
  r.sattVerktyg("linje");
  klicka(r, [[300, 300]]);
  r.ror({ x: 320, y: 300 }, mus);
  assert.notEqual(r.linje.mark, null);
  r.lamnar();
  assert.equal(r.mark, null);
  assert.equal(r.linje.mark, null);
  assert.equal(r.linje.ritText("linje", (v) => String(v)), null);
  assert.equal(r.linje.utkast.length, 1);
  klicka(r, [[320, 300]]);
  assert.equal(r.linje.utkast.length, 2);
});
