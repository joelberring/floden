/* Körs med `npm test`. Ritlagren (K50) i klienten: lager i PlanDok, hus i valt lager, synlighet och låsning, bevarade objekt, panelens rader. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { PlanDok } from "./plandok.ts";
import { KONTRAKT_RITLAGER, nyttLager, nyttLagerId, type RitLager, type RitlagerDok, type RollInfo } from "./ritlager.ts";
import { lagerRader } from "./ritlagerpanel.ts";
import { Ritverktyg } from "./ritverktyg.ts";
import type { Punkt } from "./geom.ts";

const P = (x: number, y: number): Punkt => ({ x, y });
const kvadrat = (x: number, y: number, s: number): Punkt[] => [P(x, y), P(x + s, y), P(x + s, y + s), P(x, y + s)];

const lager = (id: string, roll: RitLager["roll"], over: Partial<RitLager> = {}): RitLager => ({
  lager_id: id,
  namn: id,
  geometri: roll === "brytlinje" || roll === "oppning" ? "linje" : "polygon",
  roll,
  stil: { fyllning: "#d98c5f", linje: "#8a4b24", linjebredd_px: 2 },
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
const FORVAL = [lager("hus", "byggnad"), lager("mark", "mark"), lager("mur", "brytlinje")];
const ROLLER: RollInfo[] = [
  { roll: "byggnad", namn: "Byggnad", geometri: ["polygon"], blir: "Nytt hus.", egenskaper: ["hojd_m"] },
  { roll: "mark", namn: "Mark (ny nivå)", geometri: ["polygon"], blir: "Ny markyta.", egenskaper: ["niva_rh2000"] },
  { roll: "brytlinje", namn: "Mur och kant", geometri: ["linje"], blir: "Brytlinje.", egenskaper: ["typ"] },
];
const dokMedLager = (): PlanDok => {
  const d = new PlanDok();
  d.sattLager(FORVAL);
  return d;
};

test("ett nytt hus hamnar i valt byggnadslager och följer med till K50", () => {
  const dok = dokMedLager();
  dok.laggTillLager(lager("hus-2", "byggnad", { namn: "Etapp 2" }));
  dok.laggTillHus(kvadrat(0, 0, 10), 12);
  dok.sattAktivtLager("hus-2");
  dok.laggTillHus(kvadrat(20, 0, 10), 9);
  const r = dok.tillRitlager((id) => (id === "nytt_hus_2" ? { takform: "platt" } : {}));
  assert.equal(r.kontrakt, KONTRAKT_RITLAGER);
  assert.deepEqual(r.objekt.map((o) => [o.objekt_id, o.lager_id]), [["nytt_hus", "hus"], ["nytt_hus_2", "hus-2"]]);
  assert.deepEqual(r.objekt[1]!.egenskaper, { hojd_m: 9, takform: "platt" });
  assert.equal(r.lager?.length, 4);
  // Husen är inte längre K9-ändringar bland de fria: servern översätter dem efter lagrets roll.
  assert.deepEqual(dok.tillFriaAndringar(), []);
  assert.equal(dok.antalILager("hus"), 1);
  assert.equal(dok.antalILager("hus-2"), 1);
});

test("utan lagerlista skickas inget lager (servern ger förvalda)", () => {
  const dok = new PlanDok();
  dok.laggTillHus(kvadrat(0, 0, 10), 12);
  const r = dok.tillRitlager();
  assert.equal(r.lager, undefined);
  assert.equal(r.objekt.length, 0, "ett hus utan lager kan inte pekas ut förrän lagren finns");
});

test("äldre förslag: fria byggnad_ny hamnar i första byggnadslagret och sparas som objekt", () => {
  const dok = dokMedLager();
  dok.fran([{ typ: "byggnad_ny", andring_id: "g1", geometri: { type: "Polygon", coordinates: [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]] }, egenskaper: { hojd_m: 9, vaningar: 3 } }]);
  assert.equal(dok.ny.get("g1")?.lager, undefined);
  const r = dok.tillRitlager();
  assert.deepEqual(r.objekt.map((o) => [o.objekt_id, o.lager_id]), [["g1", "hus"]]);
  assert.deepEqual(r.objekt[0]!.egenskaper, { vaningar: 3, hojd_m: 9 }, "övriga egenskaper bevaras");
});

test("laddat förslag med lager: hus i sina lager, objekt av andra roller och okända ändringar bevaras", () => {
  const ring = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]];
  const doc: RitlagerDok = {
    kontrakt: KONTRAKT_RITLAGER,
    lager: [...FORVAL, lager("hus-2", "byggnad")],
    objekt: [
      { objekt_id: "a", lager_id: "hus-2", geometri: { type: "Polygon", coordinates: ring }, egenskaper: { hojd_m: 7 } },
      { objekt_id: "plan", lager_id: "mark", geometri: { type: "Polygon", coordinates: ring }, egenskaper: { niva_rh2000: 11.5 } },
    ],
  };
  const fri = [
    { typ: "byggnad_hojd", andring_id: "hojd_b", geometri: null, egenskaper: { byggnads_id: "b", hojd_m: 9 } },
    { typ: "gata", andring_id: "g", geometri: null, egenskaper: { sektion: "x" } },
  ];
  const dok = new PlanDok();
  dok.fran(fri, doc);
  assert.equal(dok.ny.get("a")?.lager, "hus-2");
  assert.equal(dok.ny.has("plan"), false);
  assert.equal(dok.antalILager("mark"), 1);
  assert.equal(dok.tom(), false);
  const ut = dok.tillRitlager();
  assert.deepEqual(ut.objekt.map((o) => o.objekt_id).sort(), ["a", "plan"]);
  assert.deepEqual(ut.objekt.find((o) => o.objekt_id === "plan")!.egenskaper, { niva_rh2000: 11.5 });
  assert.deepEqual(dok.tillFriaAndringar().map((a) => a.typ), ["byggnad_hojd", "gata"]);
  // Ett lager med objekt går inte att ta bort; ett tomt går.
  assert.equal(dok.taBortLager("mark"), false);
  assert.equal(dok.taBortLager("mur"), true);
});

test("lagerpanelens rader: roll, vad den blir, antal, stängt lager och vad som går att rita", () => {
  const dok = dokMedLager();
  dok.laggTillLager(lager("baskarta", "byggnad", { namn: "Baskartans hus", kalla: "baskarta:stockholm", dataklass: "intern" }));
  dok.laggTillHus(kvadrat(0, 0, 10), 12);
  const rader = lagerRader(dok, ROLLER);
  const hus = rader.find((r) => r.id === "hus")!;
  assert.deepEqual([hus.rollNamn, hus.blir, hus.antal, hus.aktiv, hus.kanRitas, hus.stangt], ["Byggnad", "Nytt hus.", "1", true, true, false]);
  const mur = rader.find((r) => r.id === "mur")!;
  assert.deepEqual([mur.kanRitas, mur.kanTasBort], [true, true]);
  assert.equal(rader.find((r) => r.id === "baskarta")!.stangt, true);
});

test("nytt lager får nästa lediga id, rollens stil och ett namn", () => {
  assert.equal(nyttLagerId(FORVAL, "byggnad"), "byggnad-2");
  const n = nyttLager(FORVAL, "mark", "  ", ROLLER);
  assert.equal(n.lager_id, "mark-2");
  assert.equal(n.namn, "Mark (ny nivå) 2");
  assert.deepEqual(n.stil, FORVAL[1]!.stil);
  assert.equal(n.dataklass, "oppen");
});

const miljo = (meddelanden: string[]) => ({ meterPerPx: () => 0.5, meddela: (m: { text: string } | null) => void (m && meddelanden.push(m.text)) });
const PEK = { pekare: "mouse", ctrl: false, shift: false, alt: false };
const klicka = (v: Ritverktyg, p: Punkt): void => {
  v.ror(p, PEK);
  v.klick(p, PEK);
};

test("ritverktyget: hus ritas i valt lager och nekas i ett låst, släckt eller icke-byggnadslager", () => {
  const dok = dokMedLager();
  const med: string[] = [];
  const v = new Ritverktyg(dok, [], miljo(med));
  v.sattVerktyg("hus");
  for (const p of kvadrat(200, 200, 20)) klicka(v, p);
  const klart = v.avsluta();
  assert.equal(klart, true, med.join("; ") + " utkast=" + JSON.stringify(v.utkast));
  assert.equal(dok.ny.get("nytt_hus")?.lager, "hus");

  dok.andraLager("hus", (l) => (l.last = true));
  v.sattVerktyg("hus");
  klicka(v, P(300, 300));
  assert.match(med.at(-1)!, /är låst/);
  assert.equal(v.utkast.length, 0);

  dok.andraLager("hus", (l) => ((l.last = false), (l.synlig = false)));
  klicka(v, P(300, 300));
  assert.match(med.at(-1)!, /släckt/);

  dok.andraLager("hus", (l) => (l.synlig = true));
  dok.laggTillLager(lager("mark-2", "mark"));
  dok.sattAktivtLager("mark-2");
  klicka(v, P(300, 300));
  assert.match(med.at(-1)!, /rollen mark/);
});

test("ritverktyget: hus i ett låst lager går inte att markera, och markeringen släpps när lagret låses", () => {
  const dok = dokMedLager();
  const v = new Ritverktyg(dok, [], miljo([]));
  dok.laggTillHus(kvadrat(200, 200, 20), 12);
  v.sattVerktyg("valj");
  klicka(v, P(210, 210));
  assert.deepEqual(v.val, { id: "nytt_hus", ny: true });
  dok.andraLager("hus", (l) => (l.last = true));
  assert.equal(v.val, null);
  klicka(v, P(210, 210));
  assert.equal(v.val, null);
  assert.equal(dok.arSynligt(dok.ny.get("nytt_hus")!), true);
  assert.equal(dok.arRedigerbart(dok.ny.get("nytt_hus")!), false);
});

test("taOver behåller lager, aktivt lager och objekt när sidan byggs om", () => {
  const a = dokMedLager();
  a.laggTillLager(lager("hus-2", "byggnad"));
  a.sattAktivtLager("hus-2");
  a.laggTillHus(kvadrat(200, 200, 20), 12);
  const b = new PlanDok();
  b.taOver(a);
  assert.equal(b.aktivtLager, "hus-2");
  assert.equal(b.ny.get("nytt_hus")?.lager, "hus-2");
  assert.equal(b.lager.length, 4);
});
