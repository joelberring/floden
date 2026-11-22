/* Körs med `npm test`. Prövar de porterade ritalgoritmerna (snäpp, träff, hörnredigering, ångra). */
import assert from "node:assert/strict";
import { test } from "node:test";
import { btaUppskattning, normaliseraRing, sjalvkorsande, tillGeoRing, vaningar, yta, type Punkt } from "./geom.ts";
import { andraHornVid, flyttaHorn, flyttaRing, infogaHorn, infogaPunkter, narmastHorn, taBortHorn } from "./formedit.ts";
import { rutTest, traffTest, type HusForm } from "./hit.ts";
import { PlanDok } from "./plandok.ts";
import { byggFastIndex, fastAt, fastPaStrale, lasVinkel, ringExtra, utkastExtra, vinkelRef } from "./snap.ts";

const P = (x: number, y: number): Punkt => ({ x, y });
const kvadrat = (x: number, y: number, s: number): Punkt[] => [P(x, y), P(x + s, y), P(x + s, y + s), P(x, y + s)];

test("yta, våningar och BTA-uppskattning (3 m våningshöjd)", () => {
  const nytt = [P(296, 219), P(310, 219), P(310, 259), P(296, 259)];
  assert.equal(yta(nytt), 560);
  assert.equal(vaningar(18), 6);
  assert.equal(vaningar(2), 1);
  assert.equal(btaUppskattning(560, 18), 3360);
});

/* Samma tabell finns i tests/webb/test_kartverktyg.py (kärnans karna.ritat.bta_uppskattning); ett pytest
 * kontrollerar att tabellerna är desamma, så att skissens uppskattning och serverns inte glider isär. */
const BTA_VEKTOR: Array<[number, number, number, number]> = [
  // yta m2, höjd m, våningar, BTA m2
  [560, 18, 6, 3360],
  [100, 4, 1, 100],
  [100, 2, 1, 100],
  [250.5, 7.5, 2, 501],
  [1000, 200, 66, 66000],
  [49.99, 9.01, 3, 149.97],
  [300, 4.4999, 1, 300],
];

test("BTA-vektorn stämmer med kärnans uppskattning", () => {
  for (const [a, h, v, bta] of BTA_VEKTOR) {
    assert.equal(vaningar(h), v);
    assert.ok(Math.abs(btaUppskattning(a, h) - bta) < 1e-9, `${a} m2, ${h} m`);
  }
});

test("normaliserad ring är moturs, sluten i GeoJSON och avrundad till cm", () => {
  const medurs = [P(0, 0), P(0, 10.004), P(10, 10), P(10, 0)];
  const r = normaliseraRing(medurs);
  assert.deepEqual(r[0], P(0, 0));
  assert.equal(r.length, 4);
  assert.ok(r[1]!.x > 0 || r[1]!.y === 0, "andra hörnet följer moturs");
  const g = tillGeoRing(r);
  assert.deepEqual(g[0], g[g.length - 1]);
  assert.equal(r.some((p) => p.y > 10), false, "10,004 avrundas till 10,00");
});

test("självkorsande form upptäcks, vanliga former inte", () => {
  assert.equal(sjalvkorsande([P(0, 0), P(10, 10), P(10, 0), P(0, 10)]), true);
  assert.equal(sjalvkorsande(kvadrat(0, 0, 10)), false);
  assert.equal(sjalvkorsande([P(0, 0), P(10, 0), P(10, 5), P(5, 5), P(5, 10), P(0, 10)]), false);
});

const hus = [
  { id: "a", ringar: [kvadrat(0, 0, 10)] },
  { id: "b", ringar: [kvadrat(30, 0, 10)] },
];

test("fäst: hörn går före kant, kant före ingenting", () => {
  const ix = byggFastIndex(hus);
  const hornet = fastAt(ix, { punkt: P(10.3, 10.2), rackvidd: 1 });
  assert.equal(hornet?.typ, "hornpunkt");
  assert.deepEqual(hornet?.at, P(10, 10));
  const kant = fastAt(ix, { punkt: P(5, 10.4), rackvidd: 1 });
  assert.equal(kant?.typ, "kant");
  assert.ok(Math.abs(kant!.at.y - 10) < 1e-9 && Math.abs(kant!.at.x - 5) < 1e-9);
  assert.equal(fastAt(ix, { punkt: P(20, 5), rackvidd: 1 }), undefined);
  assert.equal(fastAt(ix, { punkt: P(10.3, 10.2), rackvidd: 0 }), undefined);
});

test("fäst: skärning mellan två kanter, och exkluderade objekt fäster inte", () => {
  const ix = byggFastIndex([
    { id: "a", ringar: [kvadrat(0, 0, 10)] },
    { id: "c", ringar: [[P(5, 5), P(15, 5), P(15, 20)]] },
  ]);
  const k = fastAt(ix, { punkt: P(10.2, 4.9), rackvidd: 1.5 });
  assert.equal(k?.typ, "skarning");
  assert.ok(Math.abs(k!.at.x - 10) < 1e-9 && Math.abs(k!.at.y - 5) < 1e-9);
  assert.equal(fastAt(ix, { punkt: P(10.3, 10.2), rackvidd: 1, exkludera: new Set(["a"]) }), undefined);
});

test("fäst mot det egna utkastet (utan den senaste punkten) och mot huset som dras", () => {
  const utkast = [P(0, 0), P(10, 0), P(10, 10), P(5, 3)];
  const e = utkastExtra(utkast);
  assert.equal(e.punkter.length, 3);
  const t = fastAt(undefined, { punkt: P(0.4, 0.3), rackvidd: 1, extra: e });
  assert.equal(t?.typ, "hornpunkt");
  const re = ringExtra(kvadrat(0, 0, 10), P(10, 10));
  assert.equal(re.punkter.length, 3);
  assert.equal(re.strackor.length, 2);
});

test("rät vinkel: nästa sträcka låses till 90 grader mot förra", () => {
  const ref = vinkelRef([P(0, 0), P(10, 0)], undefined, 90);
  assert.equal(ref.typ, "strack");
  const l = lasVinkel(P(10, 0), P(11.5, 6), ref);
  assert.ok(Math.abs(l.at.x - 10) < 1e-9);
  assert.ok(Math.abs(l.at.y - 6) < 0.2);
  const diagonal = lasVinkel(P(10, 0), P(15, 5), vinkelRef([P(0, 0), P(10, 0)], undefined, 45));
  assert.ok(Math.abs(diagonal.at.x - 15) < 1e-9 && Math.abs(diagonal.at.y - 5) < 1e-9);
});

test("rät vinkel för första sträckan följer kanten man fäst på", () => {
  const ix = byggFastIndex([{ id: "a", ringar: [[P(0, 0), P(10, 4)]] }]);
  const start = fastAt(ix, { punkt: P(5, 2.1), rackvidd: 1 })!;
  const ref = vinkelRef([start.at], start);
  assert.equal(ref.typ, "kant");
  const l = lasVinkel(start.at, P(start.at.x + 0.5, start.at.y + 8), ref);
  // Vinkelrätt mot kanten (0,4 / 10 -> riktning (10,4))
  const dot = l.dir.x * 10 + l.dir.y * 4;
  assert.ok(Math.abs(dot) < 1e-9 || Math.abs(l.dir.x * 4 - l.dir.y * 10) < 1e-9);
});

test("fäst längs låst stråle skär närmaste kant", () => {
  const ix = byggFastIndex([{ id: "a", ringar: [kvadrat(20, -5, 10)] }]);
  const t = fastPaStrale(ix, { origin: P(0, 0), dir: P(1, 0), nara: P(20.3, 0), rackvidd: 1 });
  assert.ok(t && Math.abs(t.at.x - 20) < 1e-9 && Math.abs(t.at.y) < 1e-9);
});

test("träff: man står i huset före kanten, senare hus ligger överst, ruta rör kanter", () => {
  const h: HusForm[] = [
    { id: "under", ringar: [kvadrat(0, 0, 10)] },
    { id: "over", ringar: [kvadrat(5, 5, 10)] },
    { id: "granne", ringar: [kvadrat(20, 0, 10)] },
  ];
  assert.deepEqual(traffTest(h, P(7, 7), 0.5).map((t) => t.id), ["over", "under"]);
  assert.deepEqual(traffTest(h, P(30.3, 5), 0.5).map((t) => t.id), ["granne"]);
  assert.deepEqual(traffTest(h, P(40, 40), 0.5), []);
  assert.deepEqual(rutTest(h, { minX: 9, minY: 9, maxX: 11, maxY: 11 }), ["under"]);
  assert.deepEqual(rutTest(h, { minX: 4, minY: 9, maxX: 6, maxY: 11 }).sort(), ["over", "under"]);
  // Ett hus som bara omsluter rutan räknas inte.
  assert.deepEqual(rutTest(h, { minX: 1, minY: 1, maxX: 2, maxY: 2 }), []);
});

test("hörnredigering: flytta, infoga, ta bort (minst tre), flytta hela huset", () => {
  const r = kvadrat(0, 0, 10);
  assert.deepEqual(flyttaHorn(r, 2, P(12, 12))[2], P(12, 12));
  assert.equal(r[2]!.x, 10, "originalet är orört");
  const mer = infogaHorn(r, 0, P(5, 0))!;
  assert.equal(mer.length, 5);
  assert.deepEqual(mer[1], P(5, 0));
  assert.equal(taBortHorn(r, 1)?.length, 3);
  assert.equal(taBortHorn([P(0, 0), P(1, 0), P(0, 1)], 0), undefined);
  assert.deepEqual(flyttaRing(r, 2, 3)[0], P(2, 3));
  assert.equal(infogaPunkter(r).length, 4);
  assert.deepEqual(infogaPunkter(r)[0]!.at, P(5, 0));
  assert.deepEqual(narmastHorn(r, P(9.8, 0.1), 0.5), { index: 1 });
  assert.equal(narmastHorn(r, P(5, 5), 0.5), undefined);
});

test("andraHornVid: lägg till på kant, ta bort hörn, ENDERA väljer själv", () => {
  const r = kvadrat(0, 0, 10);
  const lagg = andraHornVid(r, P(5, 0.2), 0.5, "LAGG_TILL")!;
  assert.equal(lagg.ring.length, 5);
  assert.equal(lagg.index, 1);
  assert.equal(andraHornVid(r, P(5, 5), 0.5, "LAGG_TILL"), undefined);
  assert.equal(andraHornVid(r, P(10.1, 0.1), 0.5, "TA_BORT")!.ring.length, 3);
  assert.equal(andraHornVid(r, P(5, 0.2), 0.5, "TA_BORT"), undefined);
  assert.equal(andraHornVid(r, P(10.1, 0.1), 0.5, "ENDERA")!.ring.length, 3);
  assert.equal(andraHornVid(r, P(5, 0.2), 0.5, "ENDERA")!.ring.length, 5);
});

test("ångra och gör om: ett nytt hus, höjd, borttagning och grupper", () => {
  const dok = new PlanDok((id) => (id === "b_1" ? 4 : undefined));
  const h = dok.laggTillHus(kvadrat(0, 0, 10), 12);
  assert.equal(h.id, "nytt_hus");
  assert.equal(dok.nyttId(), "nytt_hus_2");
  dok.sattHojd("nytt_hus", 18, true);
  dok.sattHojd("b_1", 15, false);
  assert.equal(dok.tillAndringar().length, 2);
  // Höjd tillbaka till basens värde tar bort ändringen.
  dok.sattHojd("b_1", 4, false);
  assert.equal(dok.hojd.size, 0);
  dok.sattHojd("b_1", 15, false);
  dok.sattBort("b_1", true);
  assert.equal(dok.hojd.size, 0, "en riven byggnad behöver ingen höjd");
  assert.deepEqual(dok.tillAndringar().map((a) => a.typ), ["byggnad_ny", "byggnad_bort"]);
  // Ångra rivningen ångrar också att höjden togs bort (en grupp).
  dok.angra();
  assert.equal(dok.bort.size, 0);
  assert.equal(dok.hojd.get("b_1"), 15);
  dok.angra();
  assert.equal(dok.hojd.size, 0);
  dok.goraOm();
  assert.equal(dok.hojd.get("b_1"), 15);
  assert.equal(dok.kanGoraOm(), true);
  // En ny ändring tömmer gör om.
  dok.sattHojd("nytt_hus", 20, true);
  assert.equal(dok.kanGoraOm(), false);
  // Ångra ända tillbaka till tomt.
  while (dok.kanAngra()) dok.angra();
  assert.equal(dok.tom(), true);
});

test("K9-ändringarna: sluten ring moturs, höjd som tal, stabil ordning, och in igen", () => {
  const dok = new PlanDok();
  dok.laggTillHus([P(296, 219), P(296, 259), P(310, 259), P(310, 219)], 18, "nytt_hus");
  const a = dok.tillAndringar();
  assert.equal(a.length, 1);
  assert.equal(a[0]!.typ, "byggnad_ny");
  assert.deepEqual(a[0]!.geometri?.coordinates[0], [[296, 219], [310, 219], [310, 259], [296, 259], [296, 219]]);
  assert.deepEqual(a[0]!.egenskaper, { hojd_m: 18 });
  const dok2 = new PlanDok();
  dok2.fran(a);
  assert.deepEqual(dok2.tillAndringar(), a);
  assert.equal(dok2.kanAngra(), false);
});

test("historiken kapas vid 200 enheter men aldrig mitt i en grupp", () => {
  const dok = new PlanDok();
  for (let i = 0; i < 230; i++) dok.sattHojd("b_x", 5 + i, false);
  let n = 0;
  while (dok.kanAngra()) {
    dok.angra();
    n++;
  }
  assert.equal(n, 200);
});
