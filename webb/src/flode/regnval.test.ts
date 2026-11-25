/* Körs med `npm test`. Regnväljare: snabbval, validering, JSON-kontrakt och etiketter. */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  lasRegnVal,
  medRegnForval,
  regnEtikett,
  regnSignatur,
  regnValHtml,
  STANDARD_REGNVAL,
  tillRegnParam,
  tolkaRegnTal,
  valdRegnText,
  valideraRegnVal,
} from "./regnval.ts";

test("snabbval ger exakta parametrar enligt backend-kontraktet", () => {
  assert.equal(tillRegnParam(STANDARD_REGNVAL), null);
  assert.deepEqual(tillRegnParam(medRegnForval(STANDARD_REGNVAL, "10ar")), {
    aterkomsttid_ar: 10,
    varaktighet_min: 60,
    klimatfaktor: 1.25,
  });
  assert.deepEqual(tillRegnParam(medRegnForval(STANDARD_REGNVAL, "100ar")), {
    aterkomsttid_ar: 100,
    varaktighet_min: 60,
    klimatfaktor: 1.25,
  });
  assert.deepEqual(tillRegnParam(medRegnForval(STANDARD_REGNVAL, "extrem")), {
    regn_mm: 100,
  });
});

test("eget regn stöder både IDF och direkt mm samt strikt validering", () => {
  const idfOk = { ...STANDARD_REGNVAL, forval: "eget" as const, egetLage: "idf" as const, aterkomsttid_ar: 50, varaktighet_min: 30, klimatfaktor: 1.2 };
  assert.equal(valideraRegnVal(idfOk), null);
  assert.deepEqual(tillRegnParam(idfOk), { aterkomsttid_ar: 50, varaktighet_min: 30, klimatfaktor: 1.2 });

  const mmOk = { ...STANDARD_REGNVAL, forval: "eget" as const, egetLage: "mm" as const, regn_mm: 85 };
  assert.equal(valideraRegnVal(mmOk), null);
  assert.deepEqual(tillRegnParam(mmOk), { regn_mm: 85 });

  assert.ok(valideraRegnVal({ ...mmOk, regn_mm: 0 }));
  assert.ok(valideraRegnVal({ ...mmOk, regn_mm: 600 }));
  assert.ok(valideraRegnVal({ ...idfOk, aterkomsttid_ar: 0 }));
  assert.ok(valideraRegnVal({ ...idfOk, varaktighet_min: 2 }));
  assert.ok(valideraRegnVal({ ...idfOk, klimatfaktor: 4.0 }));
});

test("regnEtikett formaterar serverns regndata som en ren 1-radig svensk etikett", () => {
  assert.equal(
    regnEtikett({ aterkomsttid_ar: 100, varaktighet_min: 60, klimatfaktor: 1.25, regn_mm: 68 }),
    "100-årsregn · 60 min · kf 1,25 = 68 mm",
  );
  assert.equal(regnEtikett({ regn_mm: 100 }), "Regn 100 mm");
  assert.equal(regnEtikett(null), null);
});

test("valdRegnText visar projektets förval för Standard och avtappning när den är satt", () => {
  const forval = { aterkomsttid_ar: 100, varaktighet_min: 60, klimatfaktor: 1.25, regn_mm: 68, kalla: "Dahlström" };
  assert.equal(valdRegnText(STANDARD_REGNVAL, forval, null), "Standard (100-årsregn · 60 min · kf 1,25 = 68 mm)");
  assert.equal(valdRegnText(STANDARD_REGNVAL, null, 20), "Standard (projektets förval) · avtappning 20,0 mm/h");
});

test("tolkaRegnTal, regnSignatur, lasRegnVal och regnValHtml fungerar deterministiskt", () => {
  assert.equal(tolkaRegnTal("1,25"), 1.25);
  assert.equal(tolkaRegnTal(""), null);
  assert.ok(Number.isNaN(tolkaRegnTal("abc")));

  const a = regnSignatur(medRegnForval(STANDARD_REGNVAL, "10ar"), null);
  const b = regnSignatur(medRegnForval(STANDARD_REGNVAL, "100ar"), null);
  assert.notEqual(a, b);

  assert.deepEqual(lasRegnVal(null), STANDARD_REGNVAL);
  assert.equal(lasRegnVal({ forval: "extrem", egetLage: "mm", regn_mm: 120 }).forval, "extrem");

  const html = regnValHtml({ ...STANDARD_REGNVAL, forval: "eget", egetLage: "idf" }, null, 15, null, false);
  assert.match(html, /data-regn-forval="100ar"/);
  assert.match(html, /ar-regn-ar/);
  assert.match(html, /15/);
});
