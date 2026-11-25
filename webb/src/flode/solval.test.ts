/* Körs med `npm test`. Solinställningar: datumförval, brukstid, tillval, validering och HTML. */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  arGiltigtDatumIso,
  effektivtSolDatum,
  lasSolVal,
  MIDSOMMAR_ISO,
  solSignatur,
  solValHtml,
  STANDARD_SOLVAL,
  tillSolParam,
  valideraSolVal,
  VARDAGJAMNING_ISO,
} from "./solval.ts";

test("standard solval ger null (serverns förval) och icke-standard ger exakt backend-kontrakt", () => {
  assert.equal(tillSolParam(STANDARD_SOLVAL), null);
  assert.deepEqual(tillSolParam({ ...STANDARD_SOLVAL, datumForval: "midsommar" }), {
    datum_iso: MIDSOMMAR_ISO,
    start_timme: null,
    slut_timme: null,
    sammanhangande: false,
    inkludera_vsc: false,
  });
  assert.deepEqual(
    tillSolParam({
      datumForval: "eget",
      egetDatumIso: "2025-05-15",
      brukstid: "09-17",
      sammanhangande: true,
      inkludera_vsc: true,
    }),
    {
      datum_iso: "2025-05-15",
      start_timme: 9,
      slut_timme: 17,
      sammanhangande: true,
      inkludera_vsc: true,
    },
  );
});

test("datumvalidering och effektivtSolDatum hanterar giltiga och ogiltiga ISO-datum", () => {
  assert.equal(arGiltigtDatumIso("2025-03-21"), true);
  assert.equal(arGiltigtDatumIso("2025-02-30"), false);
  assert.equal(arGiltigtDatumIso("21/3"), false);
  assert.equal(valideraSolVal(STANDARD_SOLVAL), null);
  assert.ok(valideraSolVal({ ...STANDARD_SOLVAL, datumForval: "eget", egetDatumIso: "ogiltigt" }));
  assert.equal(effektivtSolDatum(STANDARD_SOLVAL), VARDAGJAMNING_ISO);
  assert.equal(effektivtSolDatum({ ...STANDARD_SOLVAL, datumForval: "midsommar" }), MIDSOMMAR_ISO);
});

test("lasSolVal, solSignatur och solValHtml fungerar deterministiskt", () => {
  assert.deepEqual(lasSolVal(null), STANDARD_SOLVAL);
  const laddad = lasSolVal({
    datumForval: "midsommar",
    brukstid: "09-17",
    sammanhangande: true,
    inkludera_vsc: true,
  });
  assert.equal(laddad.datumForval, "midsommar");
  assert.equal(laddad.brukstid, "09-17");
  assert.equal(laddad.sammanhangande, true);
  assert.equal(laddad.inkludera_vsc, true);

  assert.notEqual(solSignatur(STANDARD_SOLVAL), solSignatur(laddad));

  const html = solValHtml({ ...STANDARD_SOLVAL, datumForval: "eget", egetDatumIso: "2025-04-10" }, null);
  assert.match(html, /data-sol-datum="vardagjamning"/);
  assert.match(html, /data-sol-datum="midsommar"/);
  assert.match(html, /data-sol-brukstid="09-17"/);
  assert.match(html, /data-sol-kryss="sammanhangande"/);
  assert.match(html, /data-sol-kryss="inkludera_vsc"/);
  assert.match(html, /ar-sol-datum/);
});
