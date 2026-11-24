import assert from "node:assert/strict";
import { test } from "node:test";
import { PlanDok } from "./plandok.ts";
import { Ritverktyg, type Meddelande, type Pekare, type Tangent } from "./ritverktyg.ts";

const mus: Pekare = { pekare: "mouse", ctrl: false, shift: false, alt: false };
const tang = (key: string, extra: Partial<Tangent> = {}): Tangent => ({ key, code: key, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...extra });

function skapa() {
  const dok = new PlanDok();
  const meddelanden: Array<Meddelande | null> = [];
  const skapade: string[] = [];
  const bas = [{ id: "b1", ring: [{ x: 100, y: 100 }, { x: 110, y: 100 }, { x: 110, y: 110 }, { x: 100, y: 110 }], hal: [], hojd_m: 9 }];
  const r = new Ritverktyg(dok, bas, { meterPerPx: () => 0.5, meddela: (m) => meddelanden.push(m), husSkapat: (id) => skapade.push(id) });
  return { dok, r, meddelanden, skapade };
}

function ritaHus(r: Ritverktyg, punkter: Array<[number, number]>): void {
  r.sattVerktyg("hus");
  for (const [x, y] of punkter) {
    r.ror({ x, y }, mus);
    r.klick({ x, y }, mus);
  }
}

test("W ritar hus: klick, Enter avslutar och höjdpanelen öppnas", () => {
  const { r, dok, skapade } = skapa();
  assert.equal(r.tangent(tang("w")), true);
  ritaHus(r, [[200, 200], [220, 200], [220, 215]]);
  assert.equal(r.utkast.length, 3);
  assert.equal(r.tangent(tang("Enter")), true);
  assert.equal(dok.ny.size, 1);
  assert.equal(skapade.length, 1);
  assert.deepEqual(r.val, { id: skapade[0], ny: true });
  assert.equal(r.verktyg, "valj");
});

test("Esc avbryter utkastet utan att skapa något", () => {
  const { r, dok } = skapa();
  ritaHus(r, [[200, 200], [220, 200]]);
  r.tangent(tang("Escape"));
  assert.equal(r.utkast.length, 0);
  assert.equal(dok.ny.size, 0);
});

test("två hörn räcker inte: tydligt fel och inget hus", () => {
  const { r, dok, meddelanden } = skapa();
  ritaHus(r, [[200, 200], [220, 200]]);
  assert.equal(r.avsluta(), false);
  assert.equal(dok.ny.size, 0);
  assert.equal(meddelanden.at(-1)?.typ, "fel");
});

test("självkorsande form nekas vid avslut", () => {
  const { r, dok, meddelanden } = skapa();
  ritaHus(r, [[200, 200], [220, 220], [220, 200], [200, 220]]);
  assert.equal(r.avsluta(), false);
  assert.equal(dok.ny.size, 0);
  assert.match(meddelanden.at(-1)?.text ?? "", /korsar/);
});

test("fäster mot befintligt hörn och Backsteg tar bort senaste punkten", () => {
  const { r } = skapa();
  r.sattVerktyg("hus");
  r.ror({ x: 110.5, y: 110.5 }, mus);
  assert.ok(r.fast);
  assert.deepEqual(r.mark, { x: 110, y: 110 });
  r.klick({ x: 110.5, y: 110.5 }, mus);
  r.klick({ x: 130, y: 130 }, mus);
  r.tangent(tang("Backspace"));
  assert.equal(r.utkast.length, 1);
});

test("klick på befintlig byggnad markerar den; Delete river, ångra återställer", () => {
  const { r, dok } = skapa();
  r.klick({ x: 105, y: 105 }, mus);
  assert.deepEqual(r.val, { id: "b1", ny: false });
  r.tangent(tang("Delete"));
  assert.equal(dok.bort.has("b1"), true);
  r.tangent(tang("z", { ctrlKey: true }));
  assert.equal(dok.bort.has("b1"), false);
  r.tangent(tang("Z", { shiftKey: true }));
  assert.equal(dok.bort.has("b1"), true);
});

test("höjd på befintlig byggnad blir ändring; dra hörn på nytt hus sparas", () => {
  const { r, dok } = skapa();
  r.klick({ x: 105, y: 105 }, mus);
  r.sattHojd(15);
  assert.equal(dok.hojd.get("b1"), 15);
  ritaHus(r, [[300, 300], [320, 300], [320, 320], [300, 320]]);
  r.avsluta();
  const id = r.val?.id as string;
  assert.equal(r.ned({ x: 320, y: 320 }, mus), true);
  r.ror({ x: 330, y: 335 }, { ...mus, alt: true });
  r.upp();
  const ring = dok.ny.get(id)?.ring ?? [];
  assert.deepEqual(ring[2], { x: 330, y: 335 });
});

test("dra hörn så att formen korsar sig ändras inte", () => {
  const { r, dok } = skapa();
  ritaHus(r, [[300, 300], [320, 300], [320, 320], [300, 320]]);
  r.avsluta();
  const id = r.val?.id as string;
  r.ned({ x: 300, y: 300 }, mus);
  r.ror({ x: 330, y: 310 }, { ...mus, alt: true });
  r.upp();
  assert.deepEqual(dok.ny.get(id)?.ring[0], { x: 300, y: 300 });
});

test("K9 ur dokumentet: nytt hus och höjdändring", () => {
  const { r, dok } = skapa();
  ritaHus(r, [[300, 300], [320, 300], [320, 320], [300, 320]]);
  r.avsluta();
  r.sattHojd(18);
  const a = dok.tillAndringar();
  assert.equal(a.length, 1);
  assert.equal(a[0]?.typ, "byggnad_ny");
  assert.equal(a[0]?.egenskaper?.hojd_m, 18);
});

test("pekskärm (R7): ett hörn går att ta tag i på fingrets räckvidd (22 px) men inte på musens (9 px)", () => {
  const { r, skapade } = skapa();
  ritaHus(r, [[200, 200], [220, 200], [220, 220], [200, 220]]);
  r.tangent(tang("Enter"));
  assert.equal(r.val?.id, skapade[0]);
  const finger: Pekare = { pekare: "touch", ctrl: false, shift: false, alt: false };
  // 0,5 m per px: musens räckvidd 4,5 m, fingrets 11 m. Punkten ligger 8,5 m diagonalt utanför hörnet (220, 220).
  const bort = { x: 226, y: 226 };
  assert.equal(r.ned(bort, mus), false);
  assert.equal(r.ned(bort, finger), true);
  assert.equal(r.drar, true);
  r.upp();
  assert.equal(r.drar, false);
});

test("Klar, Backa och Avbryt som tangenter (knapparna på pekskärm): Enter, Backsteg och Esc", () => {
  const { r, dok } = skapa();
  ritaHus(r, [[200, 200], [220, 200], [220, 220]]);
  assert.equal(r.utkast.length, 3);
  r.tangent(tang("Backspace"));
  assert.equal(r.utkast.length, 2);
  r.tangent(tang("Escape"));
  assert.equal(r.utkast.length, 0);
  assert.equal(dok.ny.size, 0);
});

test("pekaren lämnar kartan mitt i en ritning: förhandsvisningen försvinner utan NaN, utkastet finns kvar (webbläsarprovet)", () => {
  const { r } = skapa();
  ritaHus(r, [[200, 200], [220, 200], [220, 220]]);
  r.ror({ x: 205, y: 225 }, mus);
  assert.notEqual(r.mark, null);
  assert.ok(r.ritText() !== null);
  r.lamnar();
  assert.equal(r.mark, null);
  assert.equal(r.ritText(), null);
  assert.equal(r.utkast.length, 3);
});

test("pekskärm: ett drag i mitten av ett litet hus flyttar huset, inte en mittpunkt (handtagens räckvidd begränsas inuti figuren)", () => {
  const { r, dok, skapade } = skapa();
  ritaHus(r, [[200, 200], [220, 200], [220, 220], [200, 220]]);
  r.tangent(tang("Enter"));
  const finger: Pekare = { pekare: "touch", ctrl: false, shift: false, alt: false };
  const forst = dok.ny.get(skapade[0]!)!.ring;
  assert.equal(r.ned({ x: 210, y: 210 }, finger), true); // 10 m från varje mittpunkt, inom fingrets 11 m
  r.ror({ x: 210, y: 210 }, finger);
  r.ror({ x: 240, y: 210 }, finger);
  r.upp();
  const efter = dok.ny.get(skapade[0]!)!.ring;
  assert.equal(efter.length, forst.length); // inget nytt hörn
  assert.deepEqual(efter.map((p) => [Math.round(p.x), Math.round(p.y)]), [[230, 200], [250, 200], [250, 220], [230, 220]]);
});
