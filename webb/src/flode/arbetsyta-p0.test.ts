/* Körs med `npm test`. Regressionstester för B11 (sektion vattenlager) och B14 (solSignatur & solFel). */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { ProjektVy } from "../types.ts";
import { regnSignatur, STANDARD_REGNVAL } from "./regnval.ts";
import { sektionVattenLager } from "./sektion.ts";
import { solSignatur, STANDARD_SOLVAL, valideraSolVal, type SolVal } from "./solval.ts";
import { bedomInstallningsStatus, kanStartaKorning, tillstandFor } from "./tillstand.ts";

// ------------------------------------------------------------------ B11: sektionVattenLager

test("B11: sektionVattenLager skickar bara fore_lager/efter_lager när roll === 'max_djup' (ej skillnad)", () => {
  const lager = [
    { id: "skyfall.fore.max_djup", roll: "max_djup", typ: "bas" },
    { id: "skyfall.efter.max_djup", roll: "max_djup", typ: "scenario" },
    { id: "skyfall.skillnad.max_djup", roll: "max_djup", typ: "skillnad" },
    { id: "skyfall.fore.ankomsttid", roll: "ankomsttid", typ: "bas" },
    { id: "skyfall.efter.ankomsttid", roll: "ankomsttid", typ: "scenario" },
    { id: "skyfall.fore.tid_till_0_1m", roll: "tid_till_0_1m", typ: "bas" },
    { id: "sol.fore.soltimmar", roll: "soltimmar", typ: "bas" },
    { id: "sol.efter.soltimmar", roll: "soltimmar", typ: "scenario" },
    { id: "sol.fore.sammanhangande_soltimmar", roll: "sammanhangande_soltimmar", typ: "bas" },
    { id: "sol.fore.skuggmask", roll: "skuggmask", typ: "bas" },
    { id: "luft.fore.arsmedel", roll: "arsmedel", typ: "bas" },
    { id: "ekologi.fore.stromtathet", roll: "stromtathet", typ: "bas" },
  ];

  // Maxdjup (före + efter) -> skickas som vattenlager.
  assert.deepEqual(
    sektionVattenLager(lager, {
      fore_lager: "skyfall.fore.max_djup",
      efter_lager: "skyfall.efter.max_djup",
    }),
    { fore_lager: "skyfall.fore.max_djup", efter_lager: "skyfall.efter.max_djup" },
  );

  // Maxdjup nuläge (bara före) -> efter_lager är null.
  assert.deepEqual(
    sektionVattenLager(lager, { fore_lager: "skyfall.fore.max_djup", efter_lager: null }),
    { fore_lager: "skyfall.fore.max_djup", efter_lager: null },
  );

  // Alla icke-djuplager och skillnadslager -> alltid null (ren mark- och byggnadsprofil).
  for (const [fore, efter] of [
    ["sol.fore.soltimmar", "sol.efter.soltimmar"],
    ["sol.fore.sammanhangande_soltimmar", null],
    ["sol.fore.skuggmask", null],
    ["skyfall.fore.ankomsttid", "skyfall.efter.ankomsttid"],
    ["skyfall.fore.tid_till_0_1m", null],
    ["luft.fore.arsmedel", null],
    ["ekologi.fore.stromtathet", null],
    ["skyfall.skillnad.max_djup", null],
  ] as const) {
    assert.deepEqual(
      sektionVattenLager(lager, { fore_lager: fore, efter_lager: efter }),
      { fore_lager: null, efter_lager: null },
      `Förväntade null för ${fore}/${efter}`,
    );
  }

  // Saknade lager eller vy -> null utan krasch.
  assert.deepEqual(sektionVattenLager(null, { fore_lager: "skyfall.fore.max_djup", efter_lager: null }), {
    fore_lager: null,
    efter_lager: null,
  });
  assert.deepEqual(sektionVattenLager(lager, null), { fore_lager: null, efter_lager: null });
});

// ------------------------------------------------------------------ B14: solSignatur & solFel

test("B14: ändrad solinställning (datum, brukstid, sammanhängande, ögonblicksskugga, VSC) markerar solresultat som utdaterat", () => {
  const t = tillstandFor("p0-b14-solsignatur");
  t.regn = { ...STANDARD_REGNVAL };
  t.avtappning = null;
  t.sol = { ...STANDARD_SOLVAL };
  t.lageVyer = [
    { id: "v-vat", avsnitt: "vatten" } as ProjektVy,
    { id: "v-sol", avsnitt: "dagsljus" } as ProjektVy,
  ];
  t.ritVyer = [];
  t.lageRegnSignatur = regnSignatur(t.regn, t.avtappning);
  t.lageSolSignatur = solSignatur(t.sol);

  // Oförändrade inställningar -> aktuellt resultat, ingen varning.
  const ok = bedomInstallningsStatus(t, "dagsljus");
  assert.equal(ok.harAktuelltResultat, true);
  assert.equal(ok.solUtdaterat, false);
  assert.equal(ok.varningText, null);

  // Varje ändring av solinställning ska ge solUtdaterat = true och tydlig varning.
  const andringar: Array<[string, SolVal]> = [
    ["datum (midsommar)", { ...STANDARD_SOLVAL, datumForval: "midsommar" }],
    ["eget datum", { ...STANDARD_SOLVAL, datumForval: "eget", egetDatumIso: "2025-05-01" }],
    ["brukstid 09-17", { ...STANDARD_SOLVAL, brukstid: "09-17" }],
    ["sammanhängande sol", { ...STANDARD_SOLVAL, sammanhangande: true }],
    ["ögonblicksskugga", { ...STANDARD_SOLVAL, klockslag_utc: 14 }],
    ["inkludera VSC", { ...STANDARD_SOLVAL, inkludera_vsc: true }],
  ];
  for (const [etikett, nySol] of andringar) {
    t.sol = nySol;
    const st = bedomInstallningsStatus(t, "dagsljus");
    assert.equal(st.solUtdaterat, true, `Förväntade utdaterat solresultat vid ${etikett}`);
    assert.equal(st.harAktuelltResultat, false, `harAktuelltResultat ska vara false vid ${etikett}`);
    assert.equal(st.regnUtdaterat, false, `Regn ska inte flaggas som utdaterat vid ${etikett}`);
    assert.match(st.varningText ?? "", /Vald solinställning skiljer sig från visat resultat/);
  }
});

test("B14: ogiltig solinställning (solFel) spärrar körning i både korNulageNu och korForslagNu", () => {
  const ogiltigtDatumFel = valideraSolVal({
    ...STANDARD_SOLVAL,
    datumForval: "eget",
    egetDatumIso: "2025-02-30",
  });
  assert.ok(ogiltigtDatumFel);
  assert.equal(kanStartaKorning(["vatten.skyfall", "dagsljus.sol"], null, false, ogiltigtDatumFel), false);

  const ogiltigtKlockslagFel = valideraSolVal({
    ...STANDARD_SOLVAL,
    klockslag_utc: 23,
  });
  assert.ok(ogiltigtKlockslagFel);
  assert.equal(kanStartaKorning(["dagsljus.sol"], null, false, ogiltigtKlockslagFel), false);

  // Giltig solinställning + giltigt regn + minst en analys -> körning tillåts.
  assert.equal(kanStartaKorning(["vatten.skyfall", "dagsljus.sol"], null, false, valideraSolVal(STANDARD_SOLVAL)), true);
  // Tom analyslista, regnFel eller avtappningFel spärrar fortsatt.
  assert.equal(kanStartaKorning([], null, false, null), false);
  assert.equal(kanStartaKorning(["vatten.skyfall"], "Fel regn", false, null), false);
  assert.equal(kanStartaKorning(["vatten.skyfall"], null, true, null), false);
});
