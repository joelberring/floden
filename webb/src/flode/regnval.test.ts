/* Körs med `npm test`. Regnväljare: snabbval, beräkningstyp, regntyp, validering, JSON-kontrakt och etiketter. */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  arDirektMm,
  effektivRegntyp,
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
  assert.deepEqual(tillRegnParam({ ...STANDARD_REGNVAL, berakningstyp: "dynamisk" }), {
    berakningstyp: "dynamisk",
    regntyp: "cds",
  });
  assert.deepEqual(tillRegnParam(medRegnForval(STANDARD_REGNVAL, "10ar")), {
    aterkomsttid_ar: 10,
    varaktighet_min: 60,
    klimatfaktor: 1.25,
    berakningstyp: "statisk",
    regntyp: "cds",
  });
  assert.deepEqual(tillRegnParam(medRegnForval(STANDARD_REGNVAL, "100ar")), {
    aterkomsttid_ar: 100,
    varaktighet_min: 60,
    klimatfaktor: 1.25,
    berakningstyp: "statisk",
    regntyp: "cds",
  });
  assert.deepEqual(tillRegnParam(medRegnForval(STANDARD_REGNVAL, "extrem")), {
    regn_mm: 100,
    berakningstyp: "statisk",
    regntyp: "block",
  });
});

test("eget regn stöder både IDF och direkt mm samt strikt validering", () => {
  const idfOk = { ...STANDARD_REGNVAL, forval: "eget" as const, egetLage: "idf" as const, aterkomsttid_ar: 50, varaktighet_min: 30, klimatfaktor: 1.2 };
  assert.equal(valideraRegnVal(idfOk), null);
  assert.deepEqual(tillRegnParam(idfOk), {
    aterkomsttid_ar: 50,
    varaktighet_min: 30,
    klimatfaktor: 1.2,
    berakningstyp: "statisk",
    regntyp: "cds",
  });

  const mmOk = { ...STANDARD_REGNVAL, forval: "eget" as const, egetLage: "mm" as const, regn_mm: 85, berakningstyp: "dynamisk" as const, regntyp: "cds" as const };
  assert.equal(valideraRegnVal(mmOk), null);
  assert.equal(arDirektMm(mmOk), true);
  assert.equal(effektivRegntyp(mmOk), "block");
  assert.deepEqual(tillRegnParam(mmOk), { regn_mm: 85, berakningstyp: "dynamisk", regntyp: "block" });

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

test("valdRegnText visar projektets förval, dynamisk typ och avtappning", () => {
  const forval = { aterkomsttid_ar: 100, varaktighet_min: 60, klimatfaktor: 1.25, regn_mm: 68, kalla: "Dahlström" };
  assert.equal(valdRegnText(STANDARD_REGNVAL, forval, null), "Standard (100-årsregn · 60 min · kf 1,25 = 68 mm)");
  assert.equal(valdRegnText(STANDARD_REGNVAL, null, 20), "Standard (projektets förval) · avtappning 20,0 mm/h");
  assert.equal(
    valdRegnText({ ...STANDARD_REGNVAL, berakningstyp: "dynamisk", regntyp: "cds" }, null, null),
    "Standard (projektets förval) · dynamisk (CDS)",
  );
});

test("tolkaRegnTal, regnSignatur, lasRegnVal och regnValHtml fungerar deterministiskt", () => {
  assert.equal(tolkaRegnTal("1,25"), 1.25);
  assert.equal(tolkaRegnTal(""), null);
  assert.ok(Number.isNaN(tolkaRegnTal("abc")));

  const a = regnSignatur(medRegnForval(STANDARD_REGNVAL, "10ar"), null);
  const b = regnSignatur(medRegnForval(STANDARD_REGNVAL, "100ar"), null);
  const c = regnSignatur({ ...STANDARD_REGNVAL, berakningstyp: "dynamisk", regntyp: "cds" }, null);
  const d = regnSignatur({ ...STANDARD_REGNVAL, berakningstyp: "dynamisk", regntyp: "block" }, null);
  assert.notEqual(a, b);
  assert.notEqual(c, d);

  assert.deepEqual(lasRegnVal(null), STANDARD_REGNVAL);
  const laddad = lasRegnVal({ forval: "extrem", egetLage: "mm", regn_mm: 120, berakningstyp: "dynamisk", regntyp: "block" });
  assert.equal(laddad.forval, "extrem");
  assert.equal(laddad.berakningstyp, "dynamisk");
  assert.equal(laddad.regntyp, "block");

  const html = regnValHtml({ ...STANDARD_REGNVAL, forval: "eget", egetLage: "idf", berakningstyp: "dynamisk" }, null, 15, null, false);
  assert.match(html, /data-regn-forval="100ar"/);
  assert.match(html, /data-regn-berakningstyp="dynamisk"/);
  assert.match(html, /data-regn-regntyp="cds"/);
  assert.match(html, /ar-regn-ar/);
  assert.match(html, /15/);

  const extremDynHtml = regnValHtml({ ...STANDARD_REGNVAL, forval: "extrem", berakningstyp: "dynamisk" }, null, null, null, false);
  assert.match(extremDynHtml, /data-regn-regntyp="cds"[^>]*disabled/);
  assert.match(extremDynHtml, /CDS-regn kräver återkomsttid/);
});
