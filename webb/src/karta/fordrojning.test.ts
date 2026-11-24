/* Körs med `npm test`. Fördröjd körning: många anrop ger en körning efter det sista, avbryt och tom. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { fordrojd, type Tidtagare } from "./fordrojning.ts";

/** Falsk klocka: tiden står still tills `framat`. */
function klocka(): { tid: Tidtagare; framat(ms: number): void; antalAktiva(): number } {
  let nu = 0;
  let nr = 0;
  const aktiva = new Map<number, { vid: number; f: () => void }>();
  return {
    tid: {
      satt: (f, ms) => {
        aktiva.set(++nr, { vid: nu + ms, f });
        return nr;
      },
      rensa: (id) => void aktiva.delete(id as number),
    },
    framat: (ms) => {
      nu += ms;
      for (const [id, a] of [...aktiva]) {
        if (a.vid <= nu) {
          aktiva.delete(id);
          a.f();
        }
      }
    },
    antalAktiva: () => aktiva.size,
  };
}

test("många anrop i följd ger en körning, 150 ms efter det sista", () => {
  const k = klocka();
  let n = 0;
  const f = fordrojd(() => n++, 150, k.tid);
  f();
  k.framat(100);
  f();
  k.framat(100);
  f();
  assert.equal(n, 0);
  assert.equal(f.vantar(), true);
  k.framat(149);
  assert.equal(n, 0);
  k.framat(1);
  assert.equal(n, 1);
  assert.equal(f.vantar(), false);
  assert.equal(k.antalAktiva(), 0);
  k.framat(1000);
  assert.equal(n, 1); // inget mer utan nytt anrop
  f();
  k.framat(150);
  assert.equal(n, 2);
});

test("avbryt tar bort en väntande körning och tom kör den direkt", () => {
  const k = klocka();
  let n = 0;
  const f = fordrojd(() => n++, 150, k.tid);
  f();
  f.avbryt();
  k.framat(500);
  assert.equal(n, 0);
  f.tom(); // inget väntar
  assert.equal(n, 0);
  f();
  f.tom();
  assert.equal(n, 1);
  k.framat(500);
  assert.equal(n, 1);
});

test("fungerar med systemets timer", async () => {
  let n = 0;
  const f = fordrojd(() => n++, 5);
  f();
  f();
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(n, 1);
});
