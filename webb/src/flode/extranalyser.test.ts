/* Körs med `npm test`. Extraanalyser: Ekologi CELF, Skyfall kumulativ risk & framkomlighet (CELF) samt ekologins roll-etiketter. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { ROLL_ETIKETTER, rollEtikett } from "./analyslager.ts";
import {
  extraAnalyserHtml,
  formateraEkologiAtgarder,
  formateraKumulativRisk,
  formateraRuttAnalys,
} from "./extranalyser.ts";

test("extraAnalyserHtml innehåller sektioner för MKN, buller, ekologi (CELF) samt risk och rutt", () => {
  const html = extraAnalyserHtml();
  assert.match(html, /id="rs-mkn-kor"/);
  assert.match(html, /id="rs-buller-kor"/);
  assert.match(html, /id="rs-eko-kandidater"/);
  assert.match(html, /id="rs-eko-k"/);
  assert.match(html, /id="rs-eko-kor"/);
  assert.match(html, /id="rs-risk-omrade"/);
  assert.match(html, /id="rs-risk-kor"/);
  assert.match(html, /id="rs-rutt-fordon"/);
  assert.match(html, /id="rs-rutt-k"/);
  assert.match(html, /id="rs-rutt-kor"/);
});

test("formateraEkologiAtgarder formaterar CELF-svar med svensk decimal och (1 − 1/e)-garanti", () => {
  const tomt = formateraEkologiAtgarder(null);
  assert.equal(tomt.sammanfattning, "Inga ekologiåtgärder valda.");
  assert.equal(tomt.rader.length, 0);

  const svar = formateraEkologiAtgarder({
    kontrakt: "floden-ekologi-celf/v1",
    k: 2,
    valda_atgarder: [
      { atgard_id: "Ekodukt", marginal_vinst: 1.75, kumulativ_vinst: 1.75, vinst_ensam: 1.75 },
      { atgard_id: "Våtmark", marginal_vinst: 0.55, kumulativ_vinst: 2.3, vinst_ensam: 1.3 },
    ],
    total_vinst: 2.3,
    garanti_faktor: 0.63212,
  });
  assert.equal(svar.sammanfattning, "2 åtgärder valda · total vinst 2,30.");
  assert.match(svar.garanti, /63,2 %/);
  assert.deepEqual(svar.rader, [
    { atgard_id: "Ekodukt", marginal_vinst: "1,75", kumulativ_vinst: "1,75", vinst_ensam: "1,75" },
    { atgard_id: "Våtmark", marginal_vinst: "0,55", kumulativ_vinst: "2,30", vinst_ensam: "1,30" },
  ]);
  assert.doesNotMatch(svar.sammanfattning + svar.garanti, /\d\.\d/);
});

test("formateraKumulativRisk formaterar /api/risk med svensk formatering och stegtabell", () => {
  const tomt = formateraKumulativRisk(null);
  assert.equal(tomt.status, "Ingen kumulativ risk beräknad.");
  assert.equal(tomt.tal.length, 0);

  const f = formateraKumulativRisk({
    omrade_id: "A1_enkel_sanka",
    gransdjup_m: 0.2,
    tidshorisont_ar: 50,
    sammanfattning: {
      area_over_grans_m2: 1450.4,
      max_kumulativ_sannolikhet: 0.873,
      min_kritisk_aterkomsttid_ar: 10,
      max_forvantat_arligt_djup_m: 0.042,
    },
    steg: [
      { aterkomsttid_ar: 10, total_djup_mm: 28.4, max_djup_m: 0.25, area_over_grans_m2: 320 },
      { aterkomsttid_ar: 100, total_djup_mm: 64.2, max_djup_m: 0.62, area_over_grans_m2: 1450 },
    ],
  });
  assert.match(f.status, /0,2 m/);
  assert.match(f.status, /50 år/);
  assert.equal(f.tal[0]?.varde, "87,3 %");
  assert.match(f.tal[1]?.varde ?? "", /1\s?450 m²/);
  assert.equal(f.tal[2]?.varde, "10 år");
  assert.equal(f.tal[3]?.varde, "0,042 m");
  assert.equal(f.stegRader.length, 2);
  assert.equal(f.stegRader[1]?.regn, "64,2 mm");
  assert.equal(f.stegRader[1]?.maxDjup, "0,62 m");
});

test("formateraRuttAnalys formaterar /api/rutt med rekommenderade CELF-insatser", () => {
  const f = formateraRuttAnalys({
    omrade_id: "A1_enkel_sanka",
    fordon_typ: "slacksbil",
    varning: "Testvärden används.",
    antal_kanter_totalt: 12,
    antal_kanter_med_risk: 4,
    max_kumulativ_kant_sannolikhet: 0.642,
    rekommenderade_insatser: [
      { kant_id: "gata_h_0_1", marginalvinst: 2.5, kumulativ_vinst: 2.5, vinst_ensam: 2.5 },
      { kant_id: "gata_v_1_0", marginalvinst: 1.25, kumulativ_vinst: 3.75, vinst_ensam: 1.8 },
    ],
  });
  assert.equal(f.sammanfattning, "4 av 12 gatuavsnitt har framkomlighetsrisk (slacksbil).");
  assert.equal(f.varning, "Testvärden används.");
  assert.equal(f.tal[1]?.varde, "64,2 %");
  assert.deepEqual(f.insatser, [
    { kant_id: "gata_h_0_1", marginalvinst: "2,50", kumulativ_vinst: "2,50", vinst_ensam: "2,50" },
    { kant_id: "gata_v_1_0", marginalvinst: "1,25", kumulativ_vinst: "3,75", vinst_ensam: "1,80" },
  ]);
});

test("ROLL_ETIKETTER har tydliga svenska etiketter för ekologins känslighetslager och strömtäthet", () => {
  assert.match(ROLL_ETIKETTER.kansighet?.etikett ?? "", /Förbättringspotential.*känslighet/);
  assert.match(ROLL_ETIKETTER.kansighet?.hjalp ?? "", /∂R_eff\/∂G/);
  assert.deepEqual(rollEtikett("kansighet", "kansighet", ""), {
    etikett: "Förbättringspotential (känslighet ∂R/∂G)",
    enhet: "",
  });
  assert.deepEqual(rollEtikett("stromtathet", "stromtathet", ""), {
    etikett: "Strömtäthet (flaskhalsar)",
    enhet: "",
  });
});
